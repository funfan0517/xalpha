# -*- coding: utf-8 -*-
"""波动资产操作规则 · 数据层。

设计原则: **A 股行情只读本地库; 美股指数按需增量刷新(带本地缓存)**。
  - A 股/港股 ETF 行情: `data/_long_klines.json`(跨策略共享十年库, `data/_refresh_data.py` 每日刷新)
  - **美股指数(美元口径)**: `data/_us_index_klines.json`(本模块维护的共享库, `.NDX` / `.INX`)
  - A500 估值: `data/_a500_pe_hist.csv`(中证官网 PE, 2024-09 起)
  - 10Y 中债: `data/_bt_caches/bond10y_m.csv`; 10Y 美债: `data/_bt_caches/ust10y_m.csv`
  - 人工/外部回填: `strategies/vol_legs/data/_val_inputs.json`(FOMC 日历、PB/ERP 分位、
    TIPS/WGRC 保险丝、QDII 额度状态)

为什么 A 股/港股用场内 ETF 收盘而不是指数:
  A500 指数只有 2024-09 之后的历史, 恒生科技指数在本仓库无行情库; 场内 ETF
  (563360/588000/513180/518880)历史连续、与执行标的同源、且 §7 硬过滤要看的「场内溢价」
  正是它们。代价是 ±0.5%~2% 的溢价噪声, 对 250 日均线与 52 周高点影响可忽略(README §2.2)。

为什么美股腿**必须**用美元指数(§6):
  人民币 ETF/联接净值 = 指数 × 汇率 × (1+溢价), 用净值算 MA250/回撤会把汇率波动当成趋势信号
  —— 文档因此明令「信号一律用美元指数(NDX/SPX)」。本模块优先取 `.NDX`/`.INX`(Xueqiu),
  取不到才降级到 513100/513500 人民币代理, 并在每日报告里显式标注降级原因。

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

_LK = os.path.join(_ROOT, "data", "_long_klines.json")
_US_LK = os.path.join(_ROOT, "data", "_us_index_klines.json")
_OTC_LK = os.path.join(_ROOT, "data", "_otc_nav.json")
_A500_PE = os.path.join(_ROOT, "data", "_a500_pe_hist.csv")
_BOND10Y = os.path.join(_ROOT, "data", "_bt_caches", "bond10y_m.csv")
_UST10Y = os.path.join(_ROOT, "data", "_bt_caches", "ust10y_m.csv")
_VAL_INPUTS = os.path.join(_DATA_DIR, "_val_inputs.json")

PCT_MIN_MONTHS = 36      # 文档 §1: 不足 3 年 -> 分位 = null
PCT_WINDOW_MONTHS = 60   # 近 5 年月度值
US_START = "20100101"    # 美股指数库起点(NDX/SPX 都能取到 2010 起)
_US_ONCE = []            # 进程内只刷新一次
_OTC_ONCE = []           # 同上(场外净值)
SOURCE_NOTE = {}         # {leg: 信号源说明}, 供每日报告标注(含降级/桥接)


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
    """增量刷新美股指数库。返回 (latest, fails)。"""
    lib = _read_us_lib()
    targets = codes or sorted({v["signal"] for v in rule.LEGS.values()
                               if str(v["signal"]).startswith(".")})
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


def series_for_leg(leg, mode=None):
    """该腿的价格序列(信号 & 回测 & 记账共用)。

    mode="otc"(默认, rule.SIGNAL_MODE): 场外复权净值 —— 与实际下单的基金同一口径,
        含汇率、跟踪误差与分红再投, 回测收益 = 你真能拿到的收益。
    mode="exchange": 场内 ETF 收盘(美股腿用美元指数, 失败降级到人民币 ETF 代理并标注)。
    """
    mode = mode or rule.SIGNAL_MODE
    if mode == "otc":
        return otc_series_for_leg(leg)
    if leg == "a500":                       # 场内口径: 510300 链接补齐 2024-10 之前
        SOURCE_NOTE.setdefault(leg, f"`{rule.LEGS[leg]['signal']}` "
                                    f"{rule.LEGS[leg]['signal_name']}")
        return a500_series()
    sig = rule.LEGS[leg]["signal"]
    if not str(sig).startswith("."):
        SOURCE_NOTE.setdefault(leg, f"`{sig}` {rule.LEGS[leg]['signal_name']}")
        return load_klines(sig)
    try:
        s = us_series(sig)
        SOURCE_NOTE[leg] = f"`{sig}` {rule.LEGS[leg]['signal_name']}"
        return s
    except Exception as exc:  # noqa: BLE001
        proxy = rule.LEGS[leg]["proxy"]
        SOURCE_NOTE[leg] = (f"**降级 `{proxy}` {rule.LEGS[leg]['proxy_name']}**"
                            f"(美元指数源不可用: {type(exc).__name__})")
        return load_klines(proxy)


def _splice(base, overlay):
    """把短历史序列接在长历史序列尾部(按拼接日缩放), 使长窗口指标连续。

    拼接日之后用 overlay 的真实收益率, 之前保留 base 的形状 —— 只影响 52 周窗口的早期段,
    拼接日之后是真实标的。用于 A500(563360 上市 2024-10, 之前用沪深300ETF 补齐窗口)。
    """
    start = overlay.index[0]
    head = base[base.index < start]
    if head.empty or len(overlay) < 2:
        return overlay
    # 缩放**前段**去对齐 overlay 的首值(而不是反过来): 这样序列末值 = 真实净值,
    # 每日报告里显示的就是基金实际净值, 前段只保留收益率形状(标识为桥接段)。
    head = head * (float(overlay.iloc[0]) / float(head.iloc[-1]))
    return pd.concat([head, overlay]).sort_index()


def a500_series():
    """A500 信号序列; 是否用沪深300补齐早期窗口见 rule.A500_USE_PROXY_HISTORY。"""
    real = load_klines(rule.LEGS["a500"]["signal"])
    if not rule.A500_USE_PROXY_HISTORY:
        return real
    return _splice(load_klines(rule.A500_PROXY_CODE), real)


def price_frame(legs=None):
    """-> DataFrame(index=交易日并集, columns=腿), 各腿只在自身有数据的日期有值。"""
    legs = legs or rule.LEG_ORDER
    return pd.DataFrame({leg: series_for_leg(leg) for leg in legs}).sort_index()


# ---------------------------------------------------------------- 场外净值库
def otc_codes():
    """所有需要用到的场外代码(六腿 + 桥接代理), 去重保序。"""
    out = []
    for k in rule.LEG_ORDER:
        for key in ("otc", "otc_proxy"):
            c = rule.LEGS[k].get(key)
            if c and c not in out:
                out.append(c)
    return out


def _read_otc_lib():
    try:
        with open(_OTC_LK, encoding="utf-8") as f:
            return json.load(f)
    except (OSError, ValueError):
        return {}


def _write_otc_lib(lib):
    os.makedirs(os.path.dirname(_OTC_LK), exist_ok=True)
    with open(_OTC_LK, "w", encoding="utf-8") as f:
        json.dump(lib, f, ensure_ascii=False)


def _a_share_latest_date():
    try:
        with open(_LK, encoding="utf-8") as f:
            lib = json.load(f)
        return max(pd.Timestamp(v["dates"][-1]) for v in lib.values() if v.get("dates"))
    except Exception:  # noqa: BLE001
        return pd.Timestamp(date.today() - timedelta(days=1))


def _expected_otc_latest(qdii=False):
    """场外净值「应已公布」的日期。

    境内基金 T 日净值要 T 日晚间才公布 -> 以「昨天(工作日)」为上限;
    QDII 净值通常再滞后 1~2 个交易日。
    """
    cap = date.today() - timedelta(days=1)
    while cap.weekday() >= 5:
        cap -= timedelta(days=1)
    d = min(_a_share_latest_date().date(), cap)
    for _ in range(2 if qdii else 0):
        d -= timedelta(days=1)
        while d.weekday() >= 5:
            d -= timedelta(days=1)
    return d.isoformat()


def _fund_nav(code):
    """取单只场外基金净值(xalpha fundinfo) -> DataFrame(date, nav, tot)。"""
    import xalpha as xa
    p = xa.fundinfo(code).price
    if p is None or not len(p):
        raise RuntimeError(f"{code} 净值返回空")
    df = pd.DataFrame({
        "date": pd.to_datetime(p["date"]).dt.strftime("%Y-%m-%d"),
        "nav": pd.to_numeric(p["netvalue"], errors="coerce"),
        "tot": pd.to_numeric(p.get("totvalue", p["netvalue"]), errors="coerce"),
    }).dropna(subset=["nav"]).sort_values("date")
    return df


def refresh_otc(codes=None):
    """刷新场外净值库(全量重取, 基金净值接口本来就是全历史)。返回 (latest, fails)。"""
    lib = _read_otc_lib()
    latest, fails = {}, {}
    for code in (codes or otc_codes()):
        try:
            df = _fund_nav(code)
            lib[code] = dict(dates=list(df["date"]), nav=[float(x) for x in df["nav"]],
                             tot=[float(x) for x in df["tot"]])
            latest[code] = lib[code]["dates"][-1]
        except Exception as exc:  # noqa: BLE001
            fails[code] = f"{type(exc).__name__}: {exc}"
            latest[code] = (lib.get(code) or {}).get("dates", [""])[-1]
    _write_otc_lib(lib)
    return latest, fails


def _otc_stale():
    lib = _read_otc_lib()
    for k in rule.LEG_ORDER:
        code = rule.LEGS[k].get("otc")
        if not code:
            continue
        node = lib.get(code)
        if not node or not node["dates"]:
            return True
        if node["dates"][-1] < _expected_otc_latest(rule.LEGS[k].get("otc_qdii", False)):
            return True
    return False


def otc_series(code, refresh=True):
    """场外基金**复权净值**序列(累计净值, 已含分红再投) —— 回测与信号的统一口径。"""
    if refresh and not _OTC_ONCE:
        if _otc_stale():
            latest, fails = refresh_otc()
            _OTC_ONCE.append((latest, fails))
        else:
            _OTC_ONCE.append(({}, {}))
    lib = _read_otc_lib()
    node = lib.get(code)
    if not node or not node["dates"]:
        raise KeyError(f"{code} 不在 data/_otc_nav.json 且刷新失败")
    tot = node.get("tot") or node["nav"]
    return _to_series(dict(dates=node["dates"], close=tot))


def otc_nav_table():
    """-> {code: 最新复权净值(累计净值)}, 报告里展示用 —— 与信号序列同一列。"""
    lib = _read_otc_lib()
    return {c: ((v.get("tot") or v.get("nav") or [None])[-1]) for c, v in lib.items()}


def otc_series_for_leg(leg):
    """该腿场外净值(必要时用 otc_proxy 桥接早期窗口) + 写 SOURCE_NOTE。"""
    cfg = rule.LEGS[leg]
    main = otc_series(cfg["otc"])
    note = f"场外 `{cfg['otc']}` {cfg['exec_name']}(复权净值)"
    if cfg.get("otc_proxy") and rule.OTC_USE_PROXY_HISTORY:
        proxy = otc_series(cfg["otc_proxy"])
        start = main.index[0].date().isoformat()
        if len(proxy) and proxy.index[0].date().isoformat() < start:
            main = _splice(proxy, main)
            note += (f" · 前段用 `{cfg['otc_proxy']}` {cfg.get('otc_proxy_name', '')}"
                     f"(本基金 {cfg.get('otc_start', start)} 起)桥接 250 日窗口")
    SOURCE_NOTE[leg] = note
    return main


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


# ---------------------------------------------------------------- A500 ERP
def a500_erp(asof=None):
    """A500 的 ERP 分位(月度, 近 5 年) —— 数据不足时 pct 为 None。

    ERP = 1/PE(TTM) − 10Y 中债, 两个都取月末值。分位 = 窗口内 ≤ 当前值的比例。
    """
    out = dict(pe=None, pe_asof=None, bond10y=None, bond_asof=None, erp=None,
               erp_pct=None, n_months=0, window_months=PCT_WINDOW_MONTHS,
               ok=False, why="")
    if not os.path.exists(_A500_PE):
        out["why"] = "缺 data/_a500_pe_hist.csv"
        return out
    pe = pd.read_csv(_A500_PE)
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

    st = {
        "a500_erp": a500_gate,
        "a500_erp_pct": erp.get("erp_pct"),
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


def indicators_all(legs=None, mode=None):
    """-> {leg: 指标DataFrame}。mode="otc"(默认) 用场外复权净值; "exchange" 用场内/美元指数。"""
    legs = legs or rule.LEG_ORDER
    return {leg: rule.add_indicators(series_for_leg(leg, mode)) for leg in legs}


def asof_note(ind):
    """(最新数据日, 该腿是否陈旧) —— 用于每日报告标注。"""
    last = ind.index[-1]
    ready = bool(ind["ready"].iloc[-1])
    return str(last.date()), ready


def main():
    """自检 / 手动刷新行情库:
    python strategies/vol_legs/data.py [--refresh-us] [--refresh-otc] [--mode otc|exchange]
    """
    import sys as _sys
    mode = rule.SIGNAL_MODE
    if "--mode" in _sys.argv:
        mode = _sys.argv[_sys.argv.index("--mode") + 1]
    if "--refresh-otc" in _sys.argv:
        latest, fails = refresh_otc()
        for c in latest:
            print(f"  [{c}] {latest[c]}" + (f"  [失败] {fails[c]}" if c in fails else ""))
    if "--refresh-us" in _sys.argv:
        latest, fails = refresh_us()
        for c in latest:
            print(f"  [{c}] {latest[c]}" + (f"  [失败] {fails[c]}" if c in fails else ""))
    stale, last = us_is_stale()
    print(f"美股指数库: {os.path.basename(_US_LK)} · 最新 {last or '(空)'} · "
          f"{'陈旧' if stale else '已最新'}")
    print(f"场外净值库: {os.path.basename(_OTC_LK)} · "
          f"{'陈旧/缺失' if _otc_stale() else '已最新'} · 信号模式 = {mode}")
    print("六腿价格序列:")
    for leg in rule.LEG_ORDER:
        try:
            s = series_for_leg(leg, mode)
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
