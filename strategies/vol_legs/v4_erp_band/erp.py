# -*- coding: utf-8 -*-
"""波动资产 · V4 变种：股债利差（ERP）通道 —— 沪深300 × 10Y 国债 · 规则与引擎。

> 编号说明：用户原话是「创建变种策略 v3」，创建时 `v3` 已被等金额网格变种 `v3_grid/` 占用，
> 故本变种编号 **v4**，目录 `v4_erp_band/`。该网格变种已于 2026-10-30 删除，v3 槽位空置。

## 1. 输入数据
- 沪深300：中证官网 `index-perf` 的日频收盘 + `peg`（= 市盈率，滚动口径）
  → 缓存 `data/_bt_caches/csi_000300.csv`（`date,close,peg`，跨策略数据类）
- 10Y 国债到期收益率（%）：东财 `RPTA_WEB_TREASURYYIELD` 日频
  → 本变种自用 `v4_erp_band/data/bond10y_long.csv`（25 年）。**不写**共享的
  `data/_bt_caches/bond10y_d.csv`：那份由股息策略生产，按 12 年窗口整体覆盖，
  会把本变种需要的历史截短（见 `load_bond10y` 的说明）
- 日期对齐：以**沪深300 交易日**为基准；国债在非交易日缺值时**前向填充**（`ffill_limit` 天），
  超过上限即剔除该日；PE 同法处理。两者取交集后不 drop 任何一腿 —— 「数据不足 → 不做信号」。

## 2. 核心指标：股债利差（ERP）
    ERP = 100 / PE − 10Y国债收益率      （单位：%，国债已是百分比数值，如 2.5 表示 2.5%）
> **PE 口径**：中证官网 `peg` 是滚动市盈率（TTM 口径）。换成静态 PE 只会把整条 ERP 曲线
> **平移**一个常数，而通道是「ERP 相对自己的滚动均值 ±2σ」，平移在 MA/STD 里**相互抵消** ——
> 所以口径差异对**信号**几乎没有影响，但对**报告的 ERP 绝对读数**有影响（README §5 有量化）。

## 3. 通道与信号（`signal_series`）
    MA  = ERP 的 window 日滚动均值（默认 250 ≈ 1 年）
    STD = 同上滚动（总体标准差 ddof=0）
    上轨 = MA + k×STD      下轨 = MA − k×STD      （默认 k = 2）
- **低估**（state = +1）：ERP ≥ 上轨 —— 视频里的「上轨 = 极端低估」。取**水平判定**而非
  金叉判定：只要还在上轨上方就一直是低估态（视频说「历史低估可能持续数月」）。
- **高估**（state = −1）：ERP ≤ 下轨 —— 视频没重点讲，按**对称**实现。
- **退出**：ERP 回到 `MA + exit_frac×STD`（默认 `exit_frac = 0`，即回穿均线）→ state = 0。
  高估侧对称（`MA − exit_frac×STD`）。
- 宽滞回（进 2σ / 出 0σ 或 0.5σ）是有意的：否则 ERP 在上轨附近抖动会天天翻信号。

## 4. 仓位与再平衡（`target_weight` / `run`）
| 信号 | 沪深300 | 债券 |
|---|---|---|
| +1（低估） | `eq_cheap = 80%` | 20% |
| 0（中性） | `eq_neutral = 50%`（或 `hold_on_neutral=True` 则维持上期） | 50% |
| −1（高估） | `eq_expan = 20%` | 80% |
- **只在信号切换日动手**；同一信号内不因为涨跌微调（`rebal_freq = "M"` 月末才回锚，
  且仅当偏离 > `rebal_tol`）。这就是「避免日内频繁交易」。
- 成交口径：**T 日收盘后算信号 → T+1 收盘净值成交**（`lag = 1`）。t 日收益不会用到
  t 日之后才知道的信息。
- 成本：股票腿与债券腿各自按 `(申购, 赎回)` 扣费，默认 0.10% / 0.50%（场外 A 类量级）；
  切换信号时两侧都要付。`slip` 默认 0（按净值成交，无盘口滑点；场内口径可给 0.2%）。
- 未投部分（只在 T→T+1 这一天存在）按货基 `rule.CASH_ANNUAL` 计息。

## 5. 债券腿怎么算（两个口径，回测里直接对照）
- `bond_synthetic()`：**用 10Y 收益率合成**一只国债指数 —— 日收益 = 票息 `y/252` − 久期
  `D×Δy`（默认 D = 8）。好处：与信号**同一个 instrument**、且历史覆盖到 2002 年；
  坏处：忽略凸性与骑乘，是近似。
- `bond_real()`：真实债券基金 `003377` 的累计净值（`data/_bt_caches/nav_003377.csv`，
  2016-09 起）—— 真金白银能拿到的收益，但样本短、且是信用债为主的久期（约 3~4）。

回测主口径 = `bond_synthetic`（长样本），稳健性表里给 `bond_real`。

## 6. 复用的主策略口径
绩效指标 `engine.metrics`（rf = 货基 1.40%）、现金利率 `rule.CASH_ANNUAL`、
费率量级 `rule.FEE_BUY/FEE_SELL` —— 与 vol_legs 主策略、v1_ma20 同一套算法。
"""
import math
import os
import sys
import time
from datetime import date, timedelta

import pandas as pd

try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:  # noqa: BLE001
    pass

_HERE = os.path.dirname(os.path.abspath(__file__))
_VL = os.path.dirname(_HERE)                            # strategies/vol_legs
_ROOT = os.path.dirname(os.path.dirname(_VL))           # 仓库根
for _p in (_HERE, _VL):
    if _p not in sys.path:
        sys.path.insert(0, _p)

import engine  # noqa: E402  —— 绩效口径复用主策略
import rule    # noqa: E402

LABEL = "V4 股债利差通道"

# ---------------------------------------------------------------- 数据文件（跨策略数据类）
_HS300_CSV = os.path.join(_ROOT, "data", "_bt_caches", "csi_000300.csv")
_HS300_TR_CSV = os.path.join(_ROOT, "data", "_bt_caches", "csi_H00300.csv")
_BOND_SHARED = os.path.join(_ROOT, "data", "_bt_caches", "bond10y_d.csv")
_BOND_LONG = os.path.join(_HERE, "data", "bond10y_long.csv")     # 本变种自用（25 年）
_BOND_FUND = os.path.join(_ROOT, "data", "_bt_caches", "nav_003377.csv")

_CSI_URL = "https://www.csindex.com.cn/csindex-home/perf/index-perf"
_BOND_URL = "https://datacenter.eastmoney.com/api/data/get"
_CSI_START = "20050101"
_BOND_YEARS = 25
_BOND_TOKEN = "894050c76af8597a853f5b408b759f5d"

CACHE_DAYS = 3          # 缓存新鲜度（自然日）
FFILL_LIMIT = 10        # 国债/PE 的最大前向填充天数（约两周）

SOURCE = {}             # {key: 说明}，每日报告与回测报告里标注来源 / 是否降级
_WARN = []              # 采集告警（不静默）

# ---------------------------------------------------------------- 1. 参数（唯一权威源）
CFG = {
    # 通道
    "window": 250,          # 滚动窗口（交易日）≈ 1 年；敏感性看 120 / 500 / 750 / 1250
    "k": 2.0,               # 上下轨 = MA ± k×STD
    "exit_frac": 0.0,       # 退出线 MA + exit_frac×STD（0 = 回穿均线；0.5 = 回到 MA+0.5σ）
    # 仓位（沪深300 占比 of 股债两部分）
    "eq_cheap": 0.80,
    "eq_neutral": 0.50,
    "eq_expan": 0.20,
    "hold_on_neutral": False,   # True = 信号回到 0 时维持上一次的仓位，不改单
    # 再平衡
    "rebal_freq": "M",      # "M" 月末 / "Q" 季末 / None 只在信号切换时动手
    "rebal_tol": 0.05,      # 定期回锚的最小偏离（否则不动手，省手续费）
    "lag": 1,               # T 日收盘算信号 → T+lag 收盘成交
    # 成本
    "fee_eq": (0.0010, 0.0050),     # 股票：申购 / 赎回
    "fee_bd": (0.0010, 0.0050),     # 债券：申购 / 赎回
    "slip": 0.0,                    # 单边滑点（按净值成交 → 0；场内口径可给 0.002）
    # 债券腿
    "bond_duration": 8.0,   # 合成国债指数的修正久期（10Y 国债约 8 年）
    "ffill_limit": FFILL_LIMIT,
}


def cfg(key, default=None):
    return CFG.get(key, default)


# ---------------------------------------------------------------- 2. 采集（缓存优先）
def _session():
    try:
        import requests
        import urllib3

        urllib3.disable_warnings()
        s = requests.Session()
        s.headers.update({
            "User-Agent": ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                           "(KHTML, like Gecko) Chrome/124.0 Safari/537.36"),
            "Accept": "application/json, text/plain, */*",
        })
        s.verify = False
        return s
    except ImportError:                                  # pragma: no cover
        return None


_SESSION = _session()


def _warn(msg):
    _WARN.append(msg)
    print(f"[v4:warn] {msg}", file=sys.stderr)


def _get_json(url, params, referer, tries=3, timeout=25):
    """带退避重试的 GET -> json。失败抛异常，由调用方决定是否沿用缓存。"""
    if _SESSION is None:
        raise RuntimeError("requests 不可用")
    last = None
    for i in range(tries):
        try:
            r = _SESSION.get(url, params=params, headers={"Referer": referer},
                             timeout=timeout, verify=False)
            if r.status_code == 200:
                return r.json()
            last = RuntimeError(f"HTTP {r.status_code}")
        except Exception as exc:  # noqa: BLE001  —— SSL EOF / ProxyError 靠重试消化
            last = exc
        time.sleep(1.0 * (i + 1))
    raise RuntimeError(f"GET 失败 {url}: {last}")


def _cache_fresh(path, days=CACHE_DAYS):
    if not os.path.exists(path):
        return False
    try:
        df = pd.read_csv(path, usecols=["date"], parse_dates=["date"])
    except (OSError, ValueError):
        return False
    return bool(len(df)) and df["date"].max() >= (
        pd.Timestamp.today().normalize() - pd.Timedelta(days=days))


def _merge_write(path, new, cols):
    """把新抓到的序列并进缓存（**并集，新值覆盖旧值**）后写回，绝不截断历史。

    共享缓存（`data/_bt_caches/`）里可能已有别的策略写到更晚/更早的日期，
    所以这里只做「单调增厚」，不整体替换。
    """
    os.makedirs(os.path.dirname(path), exist_ok=True)
    parts = [new[cols]]
    if os.path.exists(path):
        try:
            old = pd.read_csv(path, parse_dates=["date"])
            if set(cols) <= set(old.columns):
                parts.insert(0, old[cols])
        except (OSError, ValueError):
            pass
    out = (pd.concat(parts, ignore_index=True)
           .drop_duplicates("date", keep="last")
           .sort_values("date"))
    out["date"] = pd.to_datetime(out["date"]).dt.strftime("%Y-%m-%d")
    out.to_csv(path, index=False, encoding="utf-8")


# ---- 沪深300 日频（收盘 + PE）
def fetch_hs300(code="000300", start=_CSI_START):
    """中证官网日频 -> DataFrame(date, close, peg)。`peg` = 滚动市盈率，2011-06 起才有。"""
    d = _get_json(_CSI_URL,
                  {"indexCode": code, "startDate": start,
                   "endDate": date.today().strftime("%Y%m%d")},
                  referer="https://www.csindex.com.cn/")
    rows = (d.get("data") or []) if isinstance(d, dict) else []
    out, seen = [], set()
    for x in rows:
        dt = str(x.get("tradeDate") or "")
        if not dt or dt in seen:
            continue
        seen.add(dt)
        try:
            out.append({"date": dt, "close": float(x.get("close")),
                        "peg": float(x["peg"]) if x.get("peg") not in (None, "") else float("nan")})
        except (TypeError, ValueError):                  # 坏行跳过，不整批作废
            continue
    if not out:
        raise RuntimeError(f"{code} 日频返回空（该指数可能不在中证官网）")
    df = pd.DataFrame(out)
    df["date"] = pd.to_datetime(df["date"], format="%Y%m%d")
    return df.sort_values("date").drop_duplicates("date", keep="last").reset_index(drop=True)


def load_hs300(refresh=False, days=CACHE_DAYS, code="000300"):
    """沪深300 日频（缓存优先）-> DataFrame(date, close, peg)。抓取失败沿用缓存并告警。"""
    if refresh or not _cache_fresh(_HS300_CSV, days):
        try:
            df = fetch_hs300(code)
            _merge_write(_HS300_CSV, df, ["date", "close", "peg"])
            SOURCE["hs300"] = (f"中证官网 `{code}`（实时抓取）· 至 {df['date'].max():%Y-%m-%d}")
        except Exception as exc:  # noqa: BLE001
            _warn(f"沪深300 日频抓取失败（{type(exc).__name__}: {exc}），沿用缓存")
    df = pd.read_csv(_HS300_CSV, parse_dates=["date"])
    if "peg" not in df.columns:                          # 兼容旧列名 `pe`
        df["peg"] = df.get("pe")
    df = df.dropna(subset=["close"]).sort_values("date")
    pe_ok = df.dropna(subset=["peg"])
    if pe_ok.empty:
        raise RuntimeError("沪深300 PE 全为空 —— 没有 `peg` 列或该指数无估值数据")
    SOURCE.setdefault("hs300", f"本地缓存 `{os.path.basename(_HS300_CSV)}` · "
                              f"至 {df['date'].max():%Y-%m-%d}")
    return df.reset_index(drop=True)


def hs300_pe_start(df=None):
    """PE 可用的第一天（ERP 序列的起点，之前的日期无意义）。"""
    df = load_hs300() if df is None else df
    pe = df.dropna(subset=["peg"])
    return pe["date"].iloc[0]


def load_hs300_tr(refresh=False, days=CACHE_DAYS, code="H00300"):
    """沪深300 **全收益**指数 H00300 日频收盘（含分红再投）—— 回测的股票腿。

    信号仍用价格指数的 PE（分红不影响 PE 口径），但**净值必须走全收益**：
    用价格指数回测会把每年 ~2pp 的分红漏掉，对「股 vs 债」的对比是系统性偏差。
    """
    if refresh or not _cache_fresh(_HS300_TR_CSV, days):
        try:
            df = fetch_hs300(code)
            _merge_write(_HS300_TR_CSV, df, ["date", "close", "peg"])
            SOURCE["hs300_tr"] = (f"中证官网 `{code}` 全收益（实时抓取）· "
                                  f"至 {df['date'].max():%Y-%m-%d}")
        except Exception as exc:  # noqa: BLE001
            _warn(f"沪深300 全收益抓取失败（{type(exc).__name__}: {exc}），沿用缓存")
    df = pd.read_csv(_HS300_TR_CSV, parse_dates=["date"]).dropna(subset=["close"])
    df = df.sort_values("date")
    SOURCE.setdefault("hs300_tr", f"本地缓存 `{os.path.basename(_HS300_TR_CSV)}` · "
                                  f"至 {df['date'].max():%Y-%m-%d}")
    return pd.Series(df["close"].astype("float64").to_numpy(),
                     index=pd.DatetimeIndex(df["date"])).sort_index()


# ---- 10Y 国债（日频，%）
def fetch_bond10y(years=_BOND_YEARS):
    """东财数据中心：中国 10 年期国债到期收益率（日频，%）。"""
    need_from = (date.today() - timedelta(days=365 * years)).strftime("%Y-%m-%d")
    out, page = {}, 1
    while page <= 30:
        d = _get_json(_BOND_URL,
                      {"type": "RPTA_WEB_TREASURYYIELD", "sty": "ALL", "st": "SOLAR_DATE",
                       "sr": "-1", "token": _BOND_TOKEN, "p": str(page), "ps": "500"},
                      referer="https://data.eastmoney.com/")
        rows = ((d or {}).get("result") or {}).get("data") or []
        if not rows:
            break
        for r in rows:
            dt = str(r.get("SOLAR_DATE") or "")[:10]
            v = r.get("EMM00166466")
            if dt and v not in (None, ""):
                try:
                    out[dt] = float(v)
                except (TypeError, ValueError):
                    continue
        if min(out) <= need_from or len(rows) < 500:
            break
        page += 1
    if not out:
        raise RuntimeError("国债收益率返回空")
    s = pd.Series(out, dtype="float64")
    s.index = pd.to_datetime(s.index)
    return s.sort_index().rename("close").rename_axis("date").reset_index()


def _read_yield_csv(path):
    """读 (date, close) 形式的收益率 csv -> Series。"""
    df = pd.read_csv(path, parse_dates=["date"])
    col = "close" if "close" in df.columns else df.columns[-1]
    return pd.Series(pd.to_numeric(df[col], errors="coerce").to_numpy(),
                     index=pd.DatetimeIndex(df["date"])).dropna().sort_index()


def load_bond10y(refresh=False, days=CACHE_DAYS):
    """10Y 中债日频（%）Series(index=日期)。

    ⚠ **不写共享缓存** `data/_bt_caches/bond10y_d.csv`。
    那个文件由股息策略生产（`dividend/v9_temp5/data.py::load_bond_cn`），它按 **12 年窗口
    整体覆盖**写入 —— 2026-10-30 实测：本变种把历史补到 2002 年，股息流程跑一次就被截回
    2013-09，于是本变种的样本起点和债券腿年化**静默变化**（4.88% → 4.88% 但债券 4.18% → 4.77%）。

    所以本变种自存一份 **25 年**的 `data/bond10y_long.csv`（策略数据类，见 AGENTS §8.4 ②），
    共享文件只在私有缓存缺失时作兜底读取。
    """
    if refresh or not _cache_fresh(_BOND_LONG, days):
        try:
            df = fetch_bond10y()
            _merge_write(_BOND_LONG, df, ["date", "close"])
            SOURCE["bond"] = (f"东财 10Y 中债（实时抓取 25 年）· "
                              f"至 {pd.to_datetime(df['date']).max():%Y-%m-%d}"
                              f" · 自 {pd.read_csv(_BOND_LONG, usecols=['date'])['date'].min()}")
        except Exception as exc:  # noqa: BLE001
            _warn(f"10Y 中债抓取失败（{type(exc).__name__}: {exc}），沿用本地缓存")

    if os.path.exists(_BOND_LONG):
        s = _read_yield_csv(_BOND_LONG)
        SOURCE.setdefault("bond", f"本变种缓存 `data/bond10y_long.csv` · "
                                  f"至 {s.index[-1]:%Y-%m-%d} · 自 {s.index[0]:%Y-%m-%d}")
        if len(s) and s.index[0] > pd.Timestamp("2012-01-01"):
            _warn(f"10Y 中债历史只到 {s.index[0]:%Y-%m-%d}（短于 2012），"
                  "ERP 样本会被压缩 —— 建议 --refresh")
        return s
    if os.path.exists(_BOND_SHARED):                       # 兜底：读股息策略那份
        s = _read_yield_csv(_BOND_SHARED)
        _warn(f"缺 `data/bond10y_long.csv`，暂用共享缓存 `{os.path.basename(_BOND_SHARED)}`"
              f"（自 {s.index[0]:%Y-%m-%d}，可能被截短）")
        SOURCE.setdefault("bond", f"共享缓存 `bond10y_d.csv`（兜底）· "
                                  f"至 {s.index[-1]:%Y-%m-%d} · 自 {s.index[0]:%Y-%m-%d}")
        return s
    raise RuntimeError("10Y 中债无任何可用缓存")


# ---------------------------------------------------------------- 3. 指标与信号
def add_band(df, window=None, k=None):
    """ERP + 滚动 MA/STD + 上下轨 + ready。列: erp/ma/std/upper/lower/ready。"""
    w = int(CFG["window"] if window is None else window)
    kk = float(CFG["k"] if k is None else k)
    out = df.copy()
    out["erp"] = 100.0 / out["pe"] - out["y10"]
    out["ma"] = out["erp"].rolling(w, min_periods=w).mean()
    out["std"] = out["erp"].rolling(w, min_periods=w).std(ddof=0)
    out["upper"] = out["ma"] + kk * out["std"]
    out["lower"] = out["ma"] - kk * out["std"]
    out["ready"] = out["ma"].notna() & out["std"].notna() & (out["std"] > 0)
    return out


def signal_series(df, exit_frac=None):
    """通道状态机 -> DataFrame(state, event)。

    state : +1 低估 / 0 中性 / -1 高估（当日**收盘后**的状态）
    event : +1 新进入低估 / -1 新进入高估 / 0 无（用于图上的圆点与笔数统计）
    """
    ef = float(CFG["exit_frac"] if exit_frac is None else exit_frac)
    erp = df["erp"].to_numpy(dtype="float64")
    ma = df["ma"].to_numpy(dtype="float64")
    sd = df["std"].to_numpy(dtype="float64")
    up = df["upper"].to_numpy(dtype="float64")
    lo = df["lower"].to_numpy(dtype="float64")
    ready = df["ready"].to_numpy(dtype=bool)

    state = 0
    states, events = [], []
    for i in range(len(df)):
        ev = 0
        if not ready[i]:
            states.append(0)
            events.append(0)
            continue
        e, m, s = erp[i], ma[i], sd[i]
        if state == 0:
            if e >= up[i]:
                state, ev = 1, 1
            elif e <= lo[i]:
                state, ev = -1, -1
        elif state == 1:
            if e <= m + ef * s:                      # 退出低估
                state = 0
        else:
            if e >= m - ef * s:                      # 退出高估
                state = 0
        states.append(state)
        events.append(ev)
    return pd.DataFrame({"state": states, "event": events}, index=df.index)


def target_weight(state, eq_cheap=None, eq_neutral=None, eq_expan=None, hold_neutral=None):
    """信号 -> 沪深300 目标权重（of 股债合计）。hold_neutral=True 时 0 态沿用上期。"""
    ec = float(CFG["eq_cheap"] if eq_cheap is None else eq_cheap)
    en = float(CFG["eq_neutral"] if eq_neutral is None else eq_neutral)
    ex = float(CFG["eq_expan"] if eq_expan is None else eq_expan)
    hold = bool(CFG["hold_on_neutral"] if hold_neutral is None else hold_neutral)
    mp = {1: ec, -1: ex}
    s_state = pd.Series(state)
    out, last = [], None
    for s in s_state.astype(int).tolist():
        if s == 0:
            w = last if (hold and last is not None) else en
        else:
            w = mp[s]
            last = w                                   # 「维持上期」的锚点只由非 0 信号刷新
        out.append(w)
    return pd.Series(out, index=s_state.index, dtype="float64")


def erp_frame(window=None, k=None, exit_frac=None, ffill_limit=None, refresh=False,
              start="2011-06-01"):
    """完整框架 -> DataFrame(index=交易日, cols: close/tr/pe/y10/erp/ma/std/upper/lower/
    ready/state/event/target)。

    对齐规则：以沪深300 交易日为基准；国债与 PE 前向填充（`ffill_limit` 天），
    填充不到的那几天直接剔除（不猜、不插值）。
    `close` = 价格指数（画图 / 展示），`tr` = **全收益**指数（回测的股票腿，含分红再投）。
    """
    lim = int(CFG["ffill_limit"] if ffill_limit is None else ffill_limit)
    hs = load_hs300(refresh=refresh)
    y = load_bond10y(refresh=refresh)
    tr = load_hs300_tr(refresh=refresh)
    hs = hs[hs["date"] >= pd.Timestamp(start)].reset_index(drop=True)
    if hs.empty:
        raise RuntimeError(f"沪深300 序列在 {start} 之后为空")

    df = pd.DataFrame({"date": hs["date"], "close": hs["close"].astype("float64"),
                       "pe": hs["peg"].ffill(limit=lim)})
    df["y10"] = y.reindex(pd.DatetimeIndex(df["date"])).ffill(limit=lim).to_numpy()
    df = df.dropna(subset=["close", "pe", "y10"]).set_index("date").sort_index()
    df["tr"] = tr.reindex(df.index).ffill(limit=lim)
    df = df.dropna(subset=["tr"])
    df = add_band(df, window=window, k=k)
    sig = signal_series(df, exit_frac=exit_frac)
    df["state"] = sig["state"]
    df["event"] = sig["event"]
    df["target"] = target_weight(df["state"]).to_numpy()
    return df


# ---------------------------------------------------------------- 4. 债券腿
def bond_synthetic(y10, duration=None):
    """用 10Y 收益率合成国债指数（净值起点 1.0）。

    日收益 = 票息 − 久期×Δy：
        carry = y_{t-1} / 100 / 252
        price = −D × Δy / 100        （Δy 以 % 为单位）
    忽略凸性与骑乘收益（对 ±2σ 级别的信号没有影响，README §5 与真实债基对照过）。
    """
    d = float(CFG["bond_duration"] if duration is None else duration)
    y = y10.astype("float64").sort_index().dropna()
    dy = y.diff() / 100.0
    ret = y.shift(1) / 100.0 / 252.0 - d * dy
    nav = (1.0 + ret.fillna(0.0)).cumprod()
    nav.iloc[0] = 1.0
    return nav.rename("bond")


def bond_real():
    """真实债券基金 `003377` 的累计净值（2016-09 起）。缺失时返回 None。"""
    if not os.path.exists(_BOND_FUND):
        return None
    df = pd.read_csv(_BOND_FUND)
    col = "close" if "close" in df.columns else df.columns[-1]
    s = pd.Series(pd.to_numeric(df[col], errors="coerce").to_numpy(),
                  index=pd.DatetimeIndex(pd.to_datetime(df.iloc[:, 0]))).dropna().sort_index()
    return s if len(s) > 10 else None


# ---------------------------------------------------------------- 5. 再平衡与回测
def period_end_flags(index, freq=None):
    """每个周期（月末/季末）的最后一个交易日 -> 布尔 Series。"""
    f = CFG["rebal_freq"] if freq is None else freq
    idx = pd.DatetimeIndex(index)
    if f in (None, "", "none"):
        return pd.Series(False, index=idx)
    pairs = [("ME", "M"), ("QE", "Q")] if str(f).upper() == "M" else [("QE", "Q"), ("ME", "M")]
    last = None
    for new, _old in pairs:
        try:
            last = pd.Series(idx, index=idx).resample(new).last().dropna()
            break
        except ValueError:
            continue
    if last is None:
        raise ValueError("本机 pandas 不支持月末/季末重采样")
    marks = set(pd.DatetimeIndex(last.to_numpy()))
    return pd.Series([d in marks for d in idx], index=idx)


def run(eq_px, bd_px, target, period_end=None, lag=None, tol=None,
        fee_eq=None, fee_bd=None, slip=None, cash_annual=None):
    """逐日复算股债组合。-> dict(nav, w, trades, fees)

    记账：两个子账户（股票份额 × 收盘价、债券份额 × 净值）+ 现金（只在 T→T+1 存在）。
    调仓：先卖超配腿（按该腿赎回费扣），再用所得现金买低配腿（按申购费扣）。
    信号：`target[t]` 由 t 日收盘算出，`lag=1` 时在 t+1 收盘成交（无前视）。
    """
    lag = int(CFG["lag"] if lag is None else lag)
    if lag != 1:
        raise ValueError("本变种只实现「T 日收盘算信号 → T+1 收盘成交」(lag=1)；"
                         "当日成交会引入前视，不提供。")
    tol = float(CFG["rebal_tol"] if tol is None else tol)
    fe = tuple(CFG["fee_eq"] if fee_eq is None else fee_eq)
    fb = tuple(CFG["fee_bd"] if fee_bd is None else fee_bd)
    sp = float(CFG["slip"] if slip is None else slip)
    cash_ann = float(rule.CASH_ANNUAL if cash_annual is None else cash_annual)
    cash_daily = (1.0 + cash_ann) ** (1.0 / 252) - 1.0

    pe_all = eq_px.astype("float64")
    pb_all = bd_px.astype("float64").reindex(pe_all.index).ffill()
    tgt = target.reindex(pe_all.index).ffill()
    if period_end is None:
        period_end = period_end_flags(pe_all.index)
    pends = period_end.reindex(pe_all.index).fillna(False).to_numpy(dtype=bool)

    cash, q_eq, q_bd = 1.0, 0.0, 0.0
    fees_paid, trades = 0.0, []
    navs, ws = [], []
    pending = None          # (目标权重, 原因)
    last_target = None      # 上一次**已执行**的目标权重（None = 还没建仓）

    for i, (d, pe) in enumerate(pe_all.items()):
        pb = float(pb_all.iloc[i])
        pe = float(pe)
        cash *= (1.0 + cash_daily)

        # 1) 先执行昨日挂单：按**今日收盘价**成交（lag=1 → 无前视）
        if pending is not None:
            w_t, why = float(pending[0]), pending[1]
            nav0 = cash + q_eq * pe + q_bd * pb
            delta = w_t * nav0 - q_eq * pe
            fee = 0.0
            if abs(delta) > 1e-12 and pe > 0 and pb > 0:
                if delta > 0:                                  # 加股票 → 卖债
                    gross = min(abs(delta) / max(1e-9, 1.0 - fb[1]), q_bd * pb)
                    q_bd -= gross / pb
                    cash += gross * (1.0 - fb[1])
                    fee += gross * fb[1]
                    buy = min(cash, abs(delta))
                    q_eq += buy * (1.0 - fe[0]) / (pe * (1.0 + sp))
                    cash -= buy
                    fee += buy * fe[0]
                else:                                          # 加债券 → 卖股
                    gross = min(abs(delta) / max(1e-9, 1.0 - fe[1]), q_eq * pe)
                    q_eq -= gross / pe
                    cash += gross * (1.0 - fe[1])
                    fee += gross * fe[1]
                    buy = min(cash, abs(delta))
                    q_bd += buy * (1.0 - fb[0]) / (pb * (1.0 - sp))
                    cash -= buy
                    fee += buy * fb[0]
                fees_paid += fee
                nav1 = cash + q_eq * pe + q_bd * pb
                trades.append(dict(
                    date=d.date().isoformat(), side="signal", why=why,
                    amount=abs(delta), fee=fee, target=w_t,
                    w_after=(q_eq * pe / nav1 if nav1 > 0 else 0.0)))
            last_target = w_t
            pending = None

        # 2) 用今日收盘算「明日该不该动」
        nav = cash + q_eq * pe + q_bd * pb
        w_now = (q_eq * pe / nav) if nav > 0 else 0.0
        t = float(tgt.iloc[i])
        if last_target is None:
            pending = (t, "init")                       # 首次建仓
        elif abs(t - last_target) > 1e-9:
            pending = (t, "signal")                     # 信号切换 → 次日成交
        elif pends[i] and abs(w_now - t) > tol:
            pending = (t, "rebal")                      # 月末/季末回锚（偏离够大才动）

        navs.append(nav)
        ws.append(w_now)

    nav = pd.Series(navs, index=pe_all.index, dtype="float64")
    return dict(nav=nav, w=pd.Series(ws, index=pe_all.index, dtype="float64"),
                trades=trades, fees=fees_paid)


def perf(nav):
    """绩效指标（复用主策略 engine.metrics，rf = 货基）。"""
    return engine.metrics(nav)


def forward_stats(df, horizons=(20, 60, 250), col="tr"):
    """按当日状态统计未来 h 个交易日的沪深300 收益 —— 回答「低估 ≠ 立刻上涨」。

    `col="tr"` 用全收益指数（与回测股票腿同一口径）；`col="close"` 用价格指数。
    """
    px = df[col] if col in df.columns else df["close"]
    rows = []
    for h in horizons:
        fwd = px.shift(-h) / px - 1.0
        for st, name in ((1, "低估(+1)"), (0, "中性(0)"), (-1, "高估(-1)")):
            m = (df["state"] == st) & fwd.notna()
            r = fwd[m]
            if not len(r):
                continue
            rows.append(dict(horizon=h, state=name, n=int(len(r)),
                             mean=float(r.mean()), median=float(r.median()),
                             win=float((r > 0).mean()),
                             p10=float(r.quantile(0.10)), p90=float(r.quantile(0.90))))
    return pd.DataFrame(rows)


def episodes(df):
    """信号区间 -> [(kind, start, end, days, erp, upper)]，最近的在前面。"""
    out, cur, start = [], None, None
    for d, s in df["state"].items():
        if s != cur:
            if cur not in (None, 0) and start is not None:
                seg = df.loc[start:d]
                out.append(dict(kind=int(cur), start=start.date().isoformat(),
                                end=d.date().isoformat(), days=int(len(seg)),
                                erp_mean=float(seg["erp"].mean()),
                                erp_max=float(seg["erp"].max()),
                                erp_min=float(seg["erp"].min()),
                                px_start=float(seg["close"].iloc[0]),
                                px_end=float(seg["close"].iloc[-1])))
            cur, start = int(s), (d if s != 0 else None)
    if cur not in (None, 0) and start is not None:
        seg = df.loc[start:]
        out.append(dict(kind=int(cur), start=start.date().isoformat(),
                        end=df.index[-1].date().isoformat(), days=int(len(seg)),
                        erp_mean=float(seg["erp"].mean()), erp_max=float(seg["erp"].max()),
                        erp_min=float(seg["erp"].min()),
                        px_start=float(seg["close"].iloc[0]),
                        px_end=float(seg["close"].iloc[-1]), open_=True))
    return out[::-1]


# ---------------------------------------------------------------- 6. 绘图
def plot_overlay(df, path, title="股债利差 vs 沪深300", start=None, end=None,
                 subtitle=None, note=None):
    """单面板双 Y 轴（复刻用户的 Wind 参考截图）：左 = 股债利差（含均线 / ±2σ 上下轨），
    右 = 沪深300 点位。看的是「两条线怎么反向走」。"""
    try:
        import matplotlib
        matplotlib.use("Agg")
        import matplotlib.pyplot as plt
        from matplotlib.ticker import StrMethodFormatter
    except Exception as exc:  # noqa: BLE001
        print("matplotlib 不可用，跳过作图:", exc)
        return None

    plt.rcParams["font.sans-serif"] = ["Microsoft YaHei", "SimHei", "DejaVu Sans"]
    plt.rcParams["axes.unicode_minus"] = False

    d = df.loc[start:end]
    if d.empty:
        return None

    c_erp, c_px, c_ma, c_band = "#2f4b9b", "#a4302f", "#e08c33", "#4a7c3f"
    fig, ax = plt.subplots(figsize=(13, 7.4))
    ax2 = ax.twinx()

    l_erp, = ax.plot(d.index, d["erp"], color=c_erp, lw=1.5, label="股债利差")
    l_px, = ax2.plot(d.index, d["close"], color=c_px, lw=1.4, label="指数")
    l_ma, = ax.plot(d.index, d["ma"], color=c_ma, lw=2.0, label="股债利差均线")
    l_up, = ax.plot(d.index, d["upper"], color=c_band, lw=1.7, label="股债利差标准差 × 2")
    l_dn, = ax.plot(d.index, d["lower"], color=c_band, lw=1.2, ls="--",
                    label="股债利差标准差 × 2")

    ax.set_ylabel("股债利差（%）", color=c_erp, fontsize=11)
    ax.tick_params(axis="y", colors=c_erp)
    ax.yaxis.set_major_formatter(StrMethodFormatter("{x:,.1f}"))
    ax2.set_ylabel("指数", color=c_px, fontsize=11)
    ax2.tick_params(axis="y", colors=c_px)
    ax2.yaxis.set_major_formatter(StrMethodFormatter("{x:,.0f}"))

    ax.grid(axis="y", ls="--", lw=0.6, alpha=0.45)
    ax.grid(axis="x", ls=":", lw=0.5, alpha=0.25)
    ax.set_axisbelow(True)
    for s in ("top",):
        ax.spines[s].set_visible(False)
        ax2.spines[s].set_visible(False)

    handles = [l_erp, l_px, l_ma, l_up, l_dn]
    ax.legend(handles, [h.get_label() for h in handles], loc="lower left",
              bbox_to_anchor=(0.0, 1.01, 1.0, 0.12), ncol=5, mode="expand",
              frameon=False, fontsize=10, handlelength=1.8, columnspacing=1.2)

    head = title
    if subtitle:
        head = f"{title}    |    {subtitle}"
    fig.suptitle(head, x=0.06, ha="left", y=0.985, fontsize=14, color="#1b2b3a", weight="bold")
    fig.text(0.06, 0.015,
             note or ("左轴：股债利差（%）= 100/PE(沪深300) − 10Y国债；橙线 = 250 日均线；"
                      "绿线 = 均线 ± 2σ。右轴：沪深300 收盘（点）。"
                      "  来源：中证官网 + 东财。  ·  模拟结果，非投资建议。"),
             fontsize=9, color="#5b6b7a")
    fig.subplots_adjust(left=0.07, right=0.93, top=0.86, bottom=0.10)
    fig.savefig(path, dpi=130, facecolor="white")
    plt.close(fig)
    return path


# ---------------------------------------------------------------- 7. 工具
def fmt_pct(x, nd=2):
    return "—" if x is None or (isinstance(x, float) and math.isnan(x)) else f"{x * 100:+.{nd}f}%"


def fmt_x(x, nd=2):
    return "—" if x is None else f"{x:.{nd}f}"


def latest_reading(window=None, k=None, exit_frac=None, refresh=False):
    """当前读数（每日报告 / 自检共用）。"""
    df = erp_frame(window=window, k=k, exit_frac=exit_frac, refresh=refresh)
    last = df.iloc[-1]
    st = int(last["state"])
    ef = float(CFG["exit_frac"] if exit_frac is None else exit_frac)
    exit_line = float(last["ma"] + ef * last["std"]) if st == 1 else (
        float(last["ma"] - ef * last["std"]) if st == -1 else float(last["ma"]))
    return dict(date=df.index[-1], df=df, erp=float(last["erp"]), pe=float(last["pe"]),
                y10=float(last["y10"]), ma=float(last["ma"]), std=float(last["std"]),
                upper=float(last["upper"]), lower=float(last["lower"]), state=st,
                exit_line=exit_line, target=float(last["target"]),
                days_in_state=int(_days_in_state(df["state"])), ep=episodes(df),
                source=dict(SOURCE))


def _days_in_state(state):
    s = state.iloc[-1]
    n = 0
    for v in reversed(state.tolist()):
        if v != s:
            break
        n += 1
    return n


def _self_check():
    df = erp_frame()
    print(f"样本 {df.index[0]:%Y-%m-%d} ~ {df.index[-1]:%Y-%m-%d} · {len(df)} 个交易日 "
          f"· PE 可用起点 {hs300_pe_start():%Y-%m-%d}")
    print(f"来源: 沪深300 {SOURCE.get('hs300')} | 10Y {SOURCE.get('bond')}")
    ready = df[df["ready"]]
    print(f"通道就绪 {ready.index[0]:%Y-%m-%d} 起（{len(ready)} 日）")
    vc = ready["state"].value_counts().to_dict()
    tot = len(ready)
    print("状态占比: " + " / ".join(
        f"{n} {vc.get(k, 0) / tot:.1%}" for k, n in ((1, "低估"), (0, "中性"), (-1, "高估"))))
    r = latest_reading()
    print(f"最新 {r['date']:%Y-%m-%d}: PE {r['pe']:.2f} · 10Y {r['y10']:.3f}% · "
          f"ERP {r['erp']:.2f}% · MA {r['ma']:.2f} · 上轨 {r['upper']:.2f} · 下轨 {r['lower']:.2f}")
    print(f"状态 {r['state']:+d} · 已持续 {r['days_in_state']} 日 · 目标沪深300 {r['target']:.0%} "
          f"· 退出线 {r['exit_line']:.2f}%")
    eps = r["ep"][:5]
    print("最近 5 段信号区间:")
    for e in eps:
        print(f"  {'低估' if e['kind'] == 1 else '高估'} {e['start']} ~ {e['end']} "
              f"({e['days']} 日) ERP 均值 {e['erp_mean']:.2f}% "
              f"沪深300 {e['px_start']:.0f} → {e['px_end']:.0f}")
    y = load_bond10y()
    sb, rb = bond_synthetic(y), bond_real()
    m1 = perf(sb)
    print(f"合成国债（D={CFG['bond_duration']:g}）年化 {m1['ann']:+.2%} "
          f"回撤 {m1['mdd']:+.2%} · 样本 {sb.index[0]:%Y-%m-%d} 起")
    if rb is not None:
        m2 = perf(rb)
        print(f"真实债基 003377 年化 {m2['ann']:+.2%} 回撤 {m2['mdd']:+.2%} · "
              f"样本 {rb.index[0]:%Y-%m-%d} 起")
    if _WARN:
        print("告警:", len(_WARN))
        for w in _WARN[:5]:
            print("  -", w)


if __name__ == "__main__":
    if "--refresh" in sys.argv:
        load_hs300(refresh=True)
        load_bond10y(refresh=True)
    _self_check()
