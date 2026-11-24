# -*- coding: utf-8 -*-
"""波动资产操作规则 · 数据层。

设计原则: **A 股行情只读本地库; 美股指数按需增量刷新(带本地缓存)**。
  - A 股/港股 ETF 行情: `data/_long_klines.json`(跨策略共享十年库, `data/_refresh_data.py` 每日刷新)
  - **A 股指数**: `data/_cn_index_klines.json`(`SH000300` 2005 起 / `SH000688` 2019-12 起 / `SH000001`)
  - **美股指数(美元口径)**: 信号唯一走 `data/_global_index_klines.json`(2005 起);
    `data/_us_index_klines.json`(2010 起) 只是历史遗留缓存, 由 `refresh_us()` 维护,
    **不再是任何策略的信号源**(2026-11-16 起 v7/v8/v9/v10 全部改用长库)。
  - **带成交量的指数**: `data/_global_index_klines.json`(`SH000510` / `.NDX` / `.INX` /
    `HKHSTECH` 的收盘 + 成交量, `refresh_global_index()` 维护)。给 v12 的 `rvol` 用:
    `xalpha.get_daily` 本来就返回 volume, 上面那份短库只是**没存**。
  - A500 估值: `data/_a500_pe_hist.csv`(中证官网 PE, 2024-09 起)
  - 10Y 中债: `data/_bt_caches/bond10y_m.csv`; 10Y 美债: `data/_bt_caches/ust10y_m.csv`
  - 人工/外部回填: `strategies/vol_legs/data/_val_inputs.json`(FOMC 日历、PB/ERP 分位、
    TIPS/WGRC 保险丝、QDII 额度状态)

价格口径只有一套(**真实指数 + 场内黄金**, 2026-11-13 起):
  指数没有场内溢价、没有跟踪误差、没有汇率污染(美股是美元口径), 历史比 ETF/场外基金长
  3~10 年 —— 规则研究只能在这个口径上做。`_global_index_klines.json` 有 `SH000510`(2005 起)、
  `.NDX` / `.INX` / `HKHSTECH`(2020-07 起), `_cn_index_klines.json` 有 `SH000300`(2005 起)/
  `SH000688`(2019-12 起); 黄金没有指数, 用场内 `518880` 实物 ETF(无分红)。
  代价是**价格指数不含分红再投**: A股腿低估约 2.0~2.5pp/年、美股约 1.3pp/年, 对账时按
  这条折算(README §2.2)。执行不变 —— 六条指数腿仍是场外 A/C 类基金(`LEGS[...]['exec']`),
  黄金走场内 `518880`; 指数只用于「信号 + 回测」。
  旧的真实指数收盘 / 场内 ETF 收盘两套对照口径已删除, 不再有口径开关。

为什么美股腿**必须**用美元指数(§6):
  人民币 ETF/联接净值 = 指数 × 汇率 × (1+溢价), 用净值算 MA250/回撤会把汇率波动当成趋势信号
  —— 文档因此明令「信号一律用美元指数(NDX/SPX)」。本模块只取 `.NDX`/`.INX`(Xueqiu,
  缓存到 `_global_index_klines.json`); 取不到就抛, 不降级到人民币代理(那会换轨)。

「数据不足 → null」是本策略的硬约束(文档 §1): 所有估值的分位在历史不足 3 年、或源缺失时
返回 None, 引擎对应规则**不执行**, 绝不用「池内中位数」「指数代理」这类估算值补位。
"""
import json
import os
import sys
from datetime import date, timedelta

import pandas as pd

_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

import rule  # noqa: E402

_DIR = os.path.dirname(os.path.abspath(__file__))
_DATA_DIR = os.path.join(_DIR, "data")
if _DIR not in sys.path:
    sys.path.insert(0, _DIR)
import _idxcache  # noqa: E402  七腿信号价格的唯一数据层（指数 OHLCV + 黄金 ETF）

_LK = os.path.join(_ROOT, "data", "_long_klines.json")
_US_LK = os.path.join(_ROOT, "data", "_us_index_klines.json")
_A500_PE = os.path.join(_ROOT, "data", "_a500_pe_hist.csv")
_HS300_CSV = os.path.join(_ROOT, "data", "_bt_caches", "csi_000300.csv")
_BOND10Y = os.path.join(_ROOT, "data", "_bt_caches", "bond10y_m.csv")
_UST10Y = os.path.join(_ROOT, "data", "_bt_caches", "ust10y_m.csv")
_VAL_INPUTS = os.path.join(_DATA_DIR, "_val_inputs.json")

PCT_MIN_MONTHS = 36      # 文档 §1: 不足 3 年 -> 分位 = null
PCT_WINDOW_MONTHS = 60   # 近 5 年月度值
US_START = "20100101"    # 美股指数库起点(NDX/SPX 都能取到 2010 起)
_US_ONCE = []            # 进程内只刷新一次
SOURCE_NOTE = {}         # {leg: 信号源说明}, 供每日报告标注


# ---------------------------------------------------------------- 行情
def load_klines(code):
    """`data/_long_klines.json` 取单码收盘 -> Series(index=DatetimeIndex)。"""
    with open(_LK, encoding="utf-8") as f:
        lib = json.load(f)
    if code not in lib:
        raise KeyError(f"{code} 不在 data/_long_klines.json(先跑 data/_refresh_data.py)")
    node = lib[code]
    s = pd.Series(node["close"], index=pd.to_datetime(node["dates"]), dtype="float64")
    return s.sort_index()


def _to_series(node):
    return pd.Series(node["close"], index=pd.to_datetime(node["dates"]), dtype="float64").sort_index()


def _read_us_lib():
    try:
        with open(_US_LK, encoding="utf-8") as f:
            return json.load(f)
    except (OSError, ValueError):
        return {}


def _write_us_lib(lib):
    os.makedirs(os.path.dirname(_US_LK), exist_ok=True)
    with open(_US_LK, "w", encoding="utf-8") as f:
        json.dump(lib, f, ensure_ascii=False)


def _fetch_us(code, start):
    """取美股指数日线(Xueqiu 经 xalpha)。失败直接抛, 由调用方决定降级。"""
    import xalpha as xa
    df = xa.get_daily(code, start=start)
    if df is None or not len(df):
        raise RuntimeError(f"{code} 返回空")
    out = df.copy()
    out["d"] = pd.to_datetime(out["date"]).dt.strftime("%Y-%m-%d")
    return out


def refresh_us(codes=None):
    """增量刷新美股指数库。返回 (latest, fails)。

    落库前过一遍 `_idxcache.clock`：美股 bar 的日期是美东交易日、收盘在北京次日 05:05，
    22:30–05:00 之间那根是半成品（`rvol` 只看比值，但收盘价错会带偏 MA/回撤），不写。
    """
    lib = _read_us_lib()
    targets = codes or sorted({v["idx"] for v in rule.LEGS.values()
                               if str(v["idx"]).startswith(".")})
    latest, fails = {}, {}
    for code in targets:
        node = lib.get(code)
        try:
            if not node:
                df = _fetch_us(code, US_START)
                lib[code] = dict(dates=list(df["d"]), close=[float(x) for x in df["close"]])
            else:
                dates, close = node["dates"], node["close"]
                start = (pd.Timestamp(dates[-1]) - pd.Timedelta(days=10)).strftime("%Y%m%d")
                df = _fetch_us(code, start)
                fmap = dict(zip(df["d"], [float(x) for x in df["close"]]))
                stored = dict(zip(dates, close))
                overlap = [d for d in dates[-5:] if d in fmap]
                if overlap and any(abs(fmap[d] - stored[d]) > 1e-6 * max(1.0, abs(stored[d]))
                                   for d in overlap):
                    df = _fetch_us(code, US_START)       # 口径变了 -> 整段重取
                    lib[code] = dict(dates=list(df["d"]), close=[float(x) for x in df["close"]])
                else:
                    add = [d for d in df["d"] if d > dates[-1]]
                    node["dates"] = dates + add
                    node["close"] = close + [fmap[d] for d in add]
            _idxcache.clock.trim_incomplete({code: lib[code]})   # 未收盘的尾部 bar 不入库
            latest[code] = lib[code]["dates"][-1]
        except Exception as exc:  # noqa: BLE001  —— 网络/口径问题都退化为「用缓存」
            fails[code] = f"{type(exc).__name__}: {exc}"
            latest[code] = (lib.get(code) or {}).get("dates", [""])[-1]
    _write_us_lib(lib)
    return latest, fails


def us_series(code, refresh=True):
    """美股指数收盘(美元口径)。本地库缺失时先拉一次; 拉不到则抛, 由 series_for_leg 降级。

    刷新是**按需**的: 库内最新日期已到「美股应已收盘的交易日」就不再联网(同日多次运行零成本),
    否则增量拉取; 网络失败不影响使用缓存。
    """
    if refresh and not _US_ONCE:
        stale, _last = us_is_stale()
        if stale or not _read_us_lib():
            latest, fails = refresh_us()
            _US_ONCE.append((latest, fails))
        else:
            _US_ONCE.append(({}, {}))
    lib = _read_us_lib()
    node = lib.get(code)
    if not node:
        raise KeyError(f"{code} 不在 data/_us_index_klines.json 且刷新失败")
    if not node["dates"]:
        raise KeyError(f"{code} 数据为空")
    return _to_series(node)


def _expected_us_latest():
    """美股最近一个「应已收盘」的交易日(北京时间的 T-1)。"""
    d = date.today() - timedelta(days=1)
    while d.weekday() >= 5:
        d -= timedelta(days=1)
    return d.isoformat()


def us_is_stale():
    """库内最新日期是否落后于「应已收盘」的交易日 -> (是否陈旧, 最新日期)。"""
    lib = _read_us_lib()
    last = max((v["dates"][-1] for v in lib.values() if v.get("dates")), default="")
    return (bool(last) and last < _expected_us_latest()), last


def expected_latest(leg):
    """该腿「应已收盘」的最新交易日(北京时间) —— 每日报告标注数据新鲜度用。

    美股腿 = T-1(过去的工作日, 美股夜间收盘); 其余腿与 A 股同一个交易日历,
    取 `_long_klines.json` 全库的最新日期(每日主流程刷新的就是它)。
    """
    if leg in ("ndx", "spx"):
        return _expected_us_latest()
    try:
        with open(_LK, encoding="utf-8") as f:
            lib = json.load(f)
        return max((v["dates"][-1] for v in lib.values() if v.get("dates")), default="")
    except (OSError, ValueError):
        return ""


# ---------------------------------------------------------------- 口径文案
# 变种(v1~v13)的报告头/图注不能各写一份口径名: 取价走 `series_for_leg(leg)`(真实指数),
# 文案就从这里取, 保证「数字是什么、标签就写什么」。
MODE_LABEL = "真实指数(主口径)"

_PRICE_DESC = ("**真实指数**（`SH000300`/`SH000510`/`SH000688`/`.NDX`/`.INX`/`HKHSTECH`；"
               "黄金用 `518880` 实物 ETF）—— 不含分红再投、场内溢价与汇率")

_PRICE_CAVEAT = ("执行: 六条指数腿走场外 A 类基金（`LEGS[...]['exec']`），黄金走场内 `518880`；"
                 "指数只用于「信号 + 回测」。价格指数不含分红再投，对账时 A 股腿按 "
                 "−2.0~2.5pp/年、美股 −1.3pp/年 折算。")

_PRICE_PLAIN = "真实指数（不含分红再投、溢价与汇率；黄金用 518880 实物 ETF）"


def price_desc_plain():
    """`price_desc` 的纯文本版，给 matplotlib 图表标题 / 轴标签用（不渲染 markdown）。"""
    return _PRICE_PLAIN


def price_short(leg=None):
    """极短口径名，给图表标题的括号里用（如「（518880, 实物ETF）」）。

    黄金是唯一的例外：它用的是 `518880` 实物 ETF（黄金没有指数），
    单腿图传入 `leg="gold"` 才不会标成「真实指数」。
    """
    return "实物ETF" if leg == "gold" else "真实指数"


def mode_label():
    """报告标题里的口径名。"""
    return MODE_LABEL


def price_desc():
    """报告头「价格 = ...」的那半句。"""
    return _PRICE_DESC


def price_caveat():
    """`price_desc` 配套的一句边界说明（执行口径 / 折算）。"""
    return _PRICE_CAVEAT


def fill_desc():
    """成交口径：指数收盘出信号，T+1 收盘成交。"""
    return "**T+1 收盘成交**"


def us_signal_exec_desc():
    """v7/v8/v9/v10（现为全七腿）报告头的「信号 / 执行」说明。

    2026-11-17 起这四个变种的池从 ndx/spx 两腿扩到 rule.LEG_ORDER 全七腿：
    信号与回测统一走 series_for_leg() 的真实指数（A股/港股指数、美股 .NDX/.INX、黄金 518880），
    表内收益是**指数口径**、不含汇率与 QDII 跟踪误差；实盘各腿按 rule.LEGS 的执行标的下单。
    函数名保留 us_ 前缀以兼容 12 处调用，不再表示「只美股两腿」。
    """
    return ("信号 = 执行 = **真实指数**收盘（A股/港股指数 · 美股 `.NDX`/`.INX` · 黄金 518880），"
            "**不含汇率与 QDII 跟踪误差**；美股腿实盘下单场外 QDII A 类（`270042` / `017641`），"
            "对账时按 −1.3pp/年 折算分红；其余腿按各自主标的执行")


def series_for_leg(leg):
    """该腿的价格序列(信号 & 回测 & 记账共用): 真实指数, 黄金用 `518880`。

    ⚠ 这里**故意不降级**: 六腿的信号口径就是真实指数（黄金 518880），场外只是「执行途径」。
    指数库坏了就让它炸出来, 别用另一套口径的数字继续往下算 —— 那会让报告和信号悄悄换轨
    （AGENTS.md §7 fail-fast）。
    """
    s = index_series_for_leg(leg)
    _src, _code, _name = IDX[leg]
    SOURCE_NOTE[leg] = (f"实物ETF `{_code}` {_name}" if _src == "etf"   # 黄金没有指数
                        else f"指数 `{_code}` {_name}")
    return s


# ---------------------------------------------------------------- 真实指数（唯一价格口径）
# 判据：**信号与回测都用「该市场指数本身」，不用 ETF 价格、不用场外净值**。
#   - 指数没有溢价、没有跟踪误差、没有汇率污染（美股指数是美元口径），
#     且历史比 ETF/场外基金长 3~10 年 —— 规则研究只能在这个口径上做。
#   - 黄金没有「指数」：`518880` 是实物黄金 ETF（无分红、溢价 <0.3%），
#     是可得的最干净序列。
# ⚠ 全部是**价格指数**（不含分红再投）：A股腿会低估约 2.0~2.5pp/年、美股约 1.3pp/年，
#   黄金不受影响。跨腿排序不受影响（同腿内一致），但绝对收益要按这条折算；
#   沪深300 的实测差见 `backtest/_vol_legs_bt.md`（H00300 全收益 − 000300 价格）。
# ⚠ 代码表有两处: 规则层 `rule.LEGS[leg]["idx"]`(唯一人读口径) 与数据层
#   `_idxcache.LEGS`(取数实现)。这里拼成 `IDX` 并断言两者完全一致 —— 改动漏了另一边,
#   import 时就炸, 不会让报告悄悄换腿。
IDX = {leg: (_idxcache.LEGS[leg][0], cfg["idx"], cfg["idx_name"])
       for leg, cfg in rule.LEGS.items()}

assert IDX == _idxcache.LEGS, (
    "rule.LEGS 与 _idxcache.LEGS 不一致 —— "
    f"rule={ {k: (v[1], v[2]) for k, v in IDX.items()} } / "
    f"cache={ {k: (v[1], v[2]) for k, v in _idxcache.LEGS.items()} }")


def index_series_for_leg(leg):
    """该腿的**真实指数**收盘序列。

    实现统一在 `_idxcache`（唯一数据层：指数 OHLCV / 黄金用 `518880` 实物 ETF），
    这里只取收盘 —— 需要 `open/high/low/volume` 的变种（v12/v13）直接调
    `_idxcache.frame(leg)`，不要再各写一份读库逻辑。
    """
    return _idxcache.series(leg)


def price_frame(legs=None):
    """-> DataFrame(index=交易日并集, columns=腿), 各腿只在自身有数据的日期有值。"""
    legs = legs or rule.LEG_ORDER
    return pd.DataFrame({leg: series_for_leg(leg) for leg in legs}).sort_index()


# ---------------------------------------------------------------- 估值输入
def val_inputs():
    """人工/外部回填的估值与事件输入 -> dict(缺失键为 None)。"""
    try:
        with open(_VAL_INPUTS, encoding="utf-8") as f:
            d = json.load(f)
    except (OSError, ValueError):
        d = {}
    return {
        "fomc_days": list(d.get("fomc_days") or []),
        "kc50_pb_pct": _num(d.get("kc50_pb_pct")),
        "hkt_erp_pct": _num(d.get("hkt_erp_pct")),
        # v1.1 §6: 美股 ERP 分位(本地无 NDX/SPX 的 TTM PE 月度库 -> 人工月度复核)
        "ndx_erp_pct": _num(d.get("ndx_erp_pct")),
        "spx_erp_pct": _num(d.get("spx_erp_pct")),
        "tips_fuse_paused": d.get("tips_fuse_paused"),
        "wgrc_fuse": d.get("wgrc_fuse"),
        # QDII 额度关闭/限大额的腿(命中则该档顺延, 禁止换场内追溢价)
        "qdii_limit_closed": [str(x) for x in (d.get("qdii_limit_closed") or [])],
        "as_of": d.get("as_of"),
        "notes": d.get("_note", ""),
    }


def _num(x):
    try:
        return None if x is None else float(x)
    except (TypeError, ValueError):
        return None


def _monthly(ser):
    """月末重采样 —— pandas 1.x 用 "M"、2.2+ 用 "ME", 两者都兼容(AGENTS.md §2)。"""
    for freq in ("ME", "M"):
        try:
            return ser.resample(freq).last()
        except ValueError:
            continue
    raise ValueError("本机 pandas 不支持月末重采样")


# ---------------------------------------------------------------- A股 ERP(A500 / 沪深300)
def _erp_from_pe(csv_path, asof=None):
    """PE 日频表(`date,peg`) + 10Y 中债月末 -> ERP 分位。A500 与沪深300 共用这一条实现。

    ERP = 1/PE(TTM) − 10Y 中债, 两个都取月末值。分位 = 近 5 年窗口内 ≤ 当前值的比例;
    月度样本不足 `PCT_MIN_MONTHS`(36) 时分位为 None(文档 §1「数据不足 → null」)。
    """
    out = dict(pe=None, pe_asof=None, bond10y=None, bond_asof=None, erp=None,
               erp_pct=None, n_months=0, window_months=PCT_WINDOW_MONTHS,
               ok=False, why="")
    if not os.path.exists(csv_path):
        out["why"] = f"缺 {os.path.relpath(csv_path, _ROOT)}"
        return out
    pe = pd.read_csv(csv_path)
    pe["date"] = pd.to_datetime(pe["date"])
    pe = pe.dropna(subset=["peg"]).sort_values("date")
    if asof is not None:
        pe = pe[pe["date"] <= pd.Timestamp(asof)]
    if pe.empty:
        out["why"] = "PE 序列为空"
        return out

    pe_m = _monthly(pe.set_index("date")["peg"]).dropna()
    out["pe"] = float(pe["peg"].iloc[-1])
    out["pe_asof"] = str(pe["date"].iloc[-1].date())

    bond = None
    if os.path.exists(_BOND10Y):
        b = pd.read_csv(_BOND10Y)
        b.columns = ["date", "close"]
        b["date"] = pd.to_datetime(b["date"])
        if asof is not None:
            b = b[b["date"] <= pd.Timestamp(asof)]
        if not b.empty:
            bond = _monthly(b.set_index("date")["close"]).dropna()
            out["bond10y"] = float(bond.iloc[-1])
            out["bond_asof"] = str(bond.index[-1].date())
    if bond is None:
        out["why"] = "缺 data/_bt_caches/bond10y_m.csv"
        return out

    df = pd.DataFrame({"pe": pe_m, "y10": bond}).dropna()
    if df.empty:
        out["why"] = "PE 与 10Y 无重叠月份"
        return out
    df["erp"] = 100.0 / df["pe"] - df["y10"]
    out["n_months"] = int(len(df))
    out["erp"] = float(df["erp"].iloc[-1])
    if len(df) < PCT_MIN_MONTHS:
        out["why"] = (f"月度样本仅 {len(df)} 个月 < {PCT_MIN_MONTHS} 个月(3 年门槛) → 分位 null")
        return out
    win = df["erp"].iloc[-PCT_WINDOW_MONTHS:]
    out["erp_pct"] = float((win <= out["erp"]).mean())
    out["ok"] = True
    return out


def a500_erp(asof=None):
    """中证A500 的 ERP 分位(月度, 近 5 年) —— 数据不足时 pct 为 None。

    数据源 `data/_a500_pe_hist.csv`(中证官网 PE, 2024-09 起) → 月度样本不到 36 个, 分位恒 null。
    """
    return _erp_from_pe(_A500_PE, asof=asof)


def hs300_erp(asof=None):
    """沪深300 的 ERP 分位 —— 与 A500 同一算法, 但数据是**长历史真分位**。

    数据源 `data/_bt_caches/csi_000300.csv`(中证官网 `index-perf`, `peg` = 滚动PE, 2005 起)。
    这条腿的闸门因此会真的生效(不像 A500 恒 null): 分位 ≥70 → on、<30 → off、中间 → 不设限。
    """
    return _erp_from_pe(_HS300_CSV, asof=asof)


def erp_pct_history(csv_path):
    """逐月**因果** ERP 分位: 第 i 个月的窗口只含 [i-window+1, i] 的样本。

    与 `_erp_from_pe` 同一算法(月度 ERP、近 5 年窗口、≥36 个月门槛), 区别只在
    「逐月重算」。用途: 回答「如果历史每一天都按**当时**的分位设闸门, 回测会变成什么样」——
    `gates()` 现在是「当下快照」, 那是前视; 本函数提供去掉前视的对照序列。

    返回 DataFrame(index = 月末, columns = erp / pct / gate), gate ∈ {"on","off",None}。
    """
    if not (os.path.exists(csv_path) and os.path.exists(_BOND10Y)):
        return pd.DataFrame(columns=["erp", "pct", "gate"])
    pe = pd.read_csv(csv_path)
    pe["date"] = pd.to_datetime(pe["date"])
    pe = pe.dropna(subset=["peg"]).sort_values("date")
    pe_m = _monthly(pe.set_index("date")["peg"]).dropna()
    b = pd.read_csv(_BOND10Y)
    b.columns = ["date", "close"]
    b["date"] = pd.to_datetime(b["date"])
    bond = _monthly(b.set_index("date")["close"]).dropna()
    df = pd.DataFrame({"pe": pe_m, "y10": bond}).dropna()
    if df.empty:
        return pd.DataFrame(columns=["erp", "pct", "gate"])
    df["erp"] = 100.0 / df["pe"] - df["y10"]
    pcts = []
    vals = df["erp"].to_numpy(dtype=float)
    for i, v in enumerate(vals):
        win = vals[max(0, i - PCT_WINDOW_MONTHS + 1):i + 1]
        pcts.append(float((win <= v).mean()) if len(win) >= PCT_MIN_MONTHS else None)
    df["pct"] = pcts
    df["gate"] = [_gate_from_pct(x, "hs300") for x in pcts]
    return df[["erp", "pct", "gate"]]


def hs300_erp_history():
    """沪深300 的逐月因果分位(见 `erp_pct_history`)。"""
    return erp_pct_history(_HS300_CSV)


def us_erp(asof=None):
    """美股 ERP 辅助读数: 10Y 美债(月末) + 本地可得的 NDX forward PE。

    只作报告展示, **不参与闸门判定** —— 文档 §6 要的是 PE(TTM), 本地没有 TTM 月度库,
    forward PE 会系统性抬高 ERP(与文档 §6 当前读数 3.3%/4% 对不上), 不能拿来顶替。
    """
    out = dict(ust10y=None, ust10y_asof=None, ndx_fwd_pe=None, ndx_fwd_pe_asof=None,
               ndx_ey=None)
    if os.path.exists(_UST10Y):
        b = pd.read_csv(_UST10Y)
        b.columns = ["date", "close"]
        b["date"] = pd.to_datetime(b["date"])
        if asof is not None:
            b = b[b["date"] <= pd.Timestamp(asof)]
        if not b.empty:
            out["ust10y"] = float(b["close"].iloc[-1])
            out["ust10y_asof"] = str(b["date"].iloc[-1].date())
    pe_csv = os.path.join(_ROOT, "data", "_ndx_fwd_pe_hist.csv")
    if os.path.exists(pe_csv):
        pe = pd.read_csv(pe_csv)
        pe["date"] = pd.to_datetime(pe["date"])
        if asof is not None:
            pe = pe[pe["date"] <= pd.Timestamp(asof)]
        if not pe.empty:
            out["ndx_fwd_pe"] = float(pe["value"].iloc[-1])
            out["ndx_fwd_pe_asof"] = str(pe["date"].iloc[-1].date())
            out["ndx_ey"] = 100.0 / out["ndx_fwd_pe"]
    return out


def _gate_from_pct(pct, key):
    """分位(0-1) -> "on"/"off"/None(中间地带不设限)。"""
    if pct is None:
        return None
    p = pct * 100.0
    return ("on" if p >= rule.CFG[key]["erp_gate_on"]
            else ("off" if p < rule.CFG[key]["erp_gate_off"] else None))


# ---------------------------------------------------------------- 闸门汇总
def gates(calendar, asof=None):
    """-> (static_gates dict, fomc_dates set)。

    static 部分在回测区间内不变(估值分位是「当下快照」; 且样本内多数分位恒 null, 见 README §5);
    FOMC 禁新仓窗口逐日判定, 单独返回日期集合, 引擎按日合并。
    """
    vi = val_inputs()
    erp = a500_erp(asof=asof)
    a500_gate = _gate_from_pct(erp.get("erp_pct"), "a500")
    erp3 = hs300_erp(asof=asof)
    hs300_gate = _gate_from_pct(erp3.get("erp_pct"), "hs300")

    st = {
        "a500_erp": a500_gate,
        "a500_erp_pct": erp.get("erp_pct"),
        # v1.4 第七腿(沪深300): 与 A500 同一条实现, 但 PE 自 2005 年 -> 分位不是 null
        "hs300_erp": hs300_gate,
        "hs300_erp_pct": erp3.get("erp_pct"),
        # 美股腿(§6): 分位为人工月度复核值; 未维护 -> None(§1 不执行)
        "ndx_erp": _gate_from_pct(vi["ndx_erp_pct"], "ndx"),
        "ndx_erp_pct": vi["ndx_erp_pct"],
        "spx_erp": _gate_from_pct(vi["spx_erp_pct"], "spx"),
        "spx_erp_pct": vi["spx_erp_pct"],
        "qdii_limit_closed": list(vi["qdii_limit_closed"]),
        "kc50_pb_pct": vi["kc50_pb_pct"],
        "hkt_erp_pct": vi["hkt_erp_pct"],
        "tips_fuse": (True if vi["tips_fuse_paused"] is True else
                      (None if vi["tips_fuse_paused"] is None else False)),
        "wgrc_fuse": (True if vi["wgrc_fuse"] is True else
                      (None if vi["wgrc_fuse"] is None else False)),
        "fomc_blackout": False,          # 逐日覆盖
        "_erp": erp,
        "_hs300_erp": erp3,
        "_us_erp": us_erp(asof=asof),
        "_inputs": vi,
        "_input_asof": vi.get("as_of"),
    }
    return st, fomc_blackout_set(calendar, vi["fomc_days"])


def fomc_blackout_set(calendar, fomc_days, window=None):
    """FOMC 决议日及其前后各 N 个交易日 -> 禁新仓日期集合。"""
    window = int(rule.CFG["hkt"]["fomc_blackout_days"]) if window is None else window
    cal = pd.DatetimeIndex(calendar).sort_values()
    out = set()
    if not fomc_days or window <= 0:
        return out
    pos = {d: i for i, d in enumerate(cal)}
    for ds in fomc_days:
        try:
            d = pd.Timestamp(ds)
        except (TypeError, ValueError):
            continue
        # 决议日若为非交易日, 取其后第一个交易日
        later = cal[cal >= d]
        idx = pos[later[0]] if len(later) else None
        if idx is None:
            continue
        for i in range(max(0, idx - window), min(len(cal), idx + window + 1)):
            out.add(cal[i].normalize())
    return out


def indicators_all(legs=None):
    """-> {leg: 指标DataFrame}, 价格一律取真实指数(黄金 518880)。"""
    legs = legs or rule.LEG_ORDER
    return {leg: rule.add_indicators(series_for_leg(leg)) for leg in legs}


def asof_note(ind):
    """(最新数据日, 该腿是否陈旧) —— 用于每日报告标注。"""
    last = ind.index[-1]
    ready = bool(ind["ready"].iloc[-1])
    return str(last.date()), ready


# ---------------------------------------------------------------- 全球指数(收盘 + 成交量)
_GIDX_LK = os.path.join(_ROOT, "data", "_global_index_klines.json")

# leg -> (xueqiu/sina 代码, 名称)。v12 的 rvol 需要「指数成交量」，而
# `_us_index_klines.json` 只存了收盘价 —— 这不是数据取不到，是当时没存。
GIDX = {
    "a500": ("SH000510", "中证A500 指数"),
    "ndx": (".NDX", "纳斯达克100 指数"),
    "spx": (".INX", "标普500 指数"),
    "hkt": ("HKHSTECH", "恒生科技指数"),
}


def _read_gidx_lib():
    try:
        with open(_GIDX_LK, encoding="utf-8") as f:
            return json.load(f)
    except (OSError, ValueError):
        return {}


def refresh_global_index(legs=None, start="20050101"):
    """全量重取「带成交量的指数」库 -> (latest, fails)。

    为什么不塞进 `_us_index_klines.json`：那份库只有 `dates/close`、且 2026-11-16 起
    只剩主策略的冗余刷新（v7/v8/v9/v10 的信号一律读本库）；加字段要整库重取 4 千根，
    而「指数成交量」目前只有 v12 的 `rvol` 需要。数据性质相同（指数行情），
    落位仍是 `data/`（AGENTS.md §8.1）。
    """
    latest, fails = _idxcache.refresh(legs, start=start)
    # 老接口按 leg 返回，转一次；`_idxcache` 内部按 code（它还要管 `SH000001` 这类非腿代码）
    out_latest = {leg: latest.get(GIDX[leg][0], "") for leg in (legs or list(GIDX))}
    out_fails = {leg: fails[GIDX[leg][0]] for leg in (legs or list(GIDX))
                 if GIDX[leg][0] in fails}
    return out_latest, out_fails


def global_index_frame(leg, refresh=False):
    """-> DataFrame(close, volume), index=交易日（库缺失/要求刷新时联网取一次）。

    薄封装：真正的读库与字段回填在 `_idxcache.frame`；要 `open/high/low` 的调用方
    直接用那个，这里只保留旧的 close/volume 两列语义。
    """
    if refresh:
        _idxcache.refresh([leg])
    try:
        df = _idxcache.frame(leg)
    except KeyError:                       # 库缺失时联网补一次（旧行为）
        _idxcache.refresh([leg])
        df = _idxcache.frame(leg)
    return df[["close", "volume"]]


def main():
    """自检 / 手动刷新行情库:
    python strategies/vol_legs/data.py [--refresh-idx] [--refresh-us]
    """
    import sys as _sys
    if "--refresh-idx" in _sys.argv:
        latest, fails = refresh_global_index()
        for leg in rule.LEG_ORDER:
            print(f"  {rule.LEGS[leg]['name']:8s} {latest.get(leg) or '—'}"
                  + (f"  [失败] {fails[leg]}" if leg in fails else ""))
    if "--refresh-us" in _sys.argv:
        latest, fails = refresh_us()
        for c in latest:
            print(f"  [{c}] {latest[c]}" + (f"  [失败] {fails[c]}" if c in fails else ""))
    stale, last = us_is_stale()
    print(f"美股指数库: {os.path.basename(_US_LK)} · 最新 {last or '(空)'} · "
          f"{'陈旧' if stale else '已最新'}")
    print(f"信号口径: {mode_label()} · "
          + " / ".join(f"{rule.LEGS[k]['name']}={IDX[k][1]}" for k in rule.LEG_ORDER))
    print("七腿价格序列:")
    for leg in rule.LEG_ORDER:
        try:
            s = series_for_leg(leg)
            print(f"  {rule.LEGS[leg]['name']:8s} {s.index[0].date()} ~ {s.index[-1].date()} "
                  f"· {len(s):5d} 根 · {SOURCE_NOTE.get(leg)}")
        except Exception as exc:  # noqa: BLE001
            print(f"  {rule.LEGS[leg]['name']:8s} 不可用 {type(exc).__name__}: {exc}")
    us = us_erp()
    if us.get("ndx_ey"):
        print(f"美股辅助读数: 10Y 美债 {us['ust10y']}%({us['ust10y_asof']}) · "
              f"NDX forward PE {us['ndx_fwd_pe']}({us['ndx_fwd_pe_asof']}) → "
              f"盈利收益率 {us['ndx_ey']:.2f}%")
    else:
        print("美股 ERP 辅助读数不可用")


if __name__ == "__main__":
    main()
