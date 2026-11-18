# -*- coding: utf-8 -*-
"""V5 · 黄金三因子计分仓位 —— **因子层**（规格 `E:\\qianwen\\jijin\\gold_rules_v2.md` §1~§3）。

本文件是**唯一**的因子实现，回测（`backtest.py`）与每日（`daily/_daily_scan.py`）都调它，
杜绝「回测一套口径、实盘另一套」（规格 §6.5）。只做三件事：

1. **三个计分因子**（§1）：
   - `f_p` 价格趋势：`close > max(MA5, MA10)` **且** `close ≥ 20 日高 × 0.995` → +1；
     `close < min(MA5, MA10)` **且** `close ≤ 20 日低 × 1.005` → −1；其余 0。
   - `f_r` 实际利率：`DFII10` 的 **10 日变化** `Δ ≤ −25bp` → +1，`Δ ≥ +25bp` → −1，其余 0；
     **T 日只用 ≤T−1 的值**（`merge_asof(allow_exact_matches=False)`，规格 §1 / §6.1）。
   - `f_f` 资金流：两种口径都实现了，由 `FLOW_MODE` 选：
     `"netin"` = 东财主力净流入连续 ≥3 日同向（**接口硬上限 120 个交易日**，个股也一样）；
     `"share"` = **上交所每日份额**的净申购/净赎回连续 ≥N 日（**2013-07 起全史**，
     `data/_etf_shares_518880.csv`）。**发布的默认口径是 `"off"`**：规格 §1 因「无全史」剔除，
     而本轮把份额全史补齐后复核（`backtest` §8.2 / `research/flow_source/_probe_flow.md`）
     发现顺势会显著恶化回撤、逆势只修回撤不增收益 —— **两个方向都跑输恒定半仓**，
     所以剔除不只是「没数据」，而是「有数据也没用」。
2. **计分与置信度**（§3）：`score = f_p + f_r (+ f_f)`；同向=高、两因子=中、分歧/缺失=低；
   缺因子再降一档（`MISSING_PENALTY`），低置信度把**极端档**向 50% 收缩一档（防弱信号打满）。
3. **信号 → 目标档映射**（§3）：`≥+2 → 100% / +1 → 75% / 0 → 50% / −1 → 25% / ≤−2 → 0~10%`。

> ⚠ 两因子口径的映射说明：规格括号写「映射同下调一档」。本实现取
> 「**同表 + 缺因子 → 低置信度 → 极端档收缩一档**」，落在 `+2 → 75%`、`−2 → 25%`，
> 与「100% 不要被两个因子打成满配」的写作意图一致；另两读法（不收缩 / 全档收缩）
> 在 `backtest.py` §8.2 作敏感性列出，结论方向不变。
"""
import io
import json
import os
import sys

import numpy as np
import pandas as pd

_HERE = os.path.dirname(os.path.abspath(__file__))        # strategies/vol_legs/v5_gold_score
_VL = os.path.dirname(_HERE)                              # strategies/vol_legs
_ROOT = os.path.dirname(os.path.dirname(_VL))             # 仓库根（推导，不写死）
_DATA = os.path.join(_ROOT, "data")
RATE_CSV = os.path.join(_DATA, "_dfii10.csv")             # FRED DFII10（数据类，全仓共用）
FLOW_CSV = os.path.join(_DATA, "_flow_518880.csv")        # 东财主力净流入（只有 120 天）
SHARE_CSV = os.path.join(_DATA, "_etf_shares_518880.csv")  # 上交所每日份额（2013-07 起，全史）
FOMC_CSV = os.path.join(_DATA, "_fomc_meetings.csv")
XAU_CSV = os.path.join(_DATA, "_gold_xau.csv")            # 伦敦金现（只当「跌幅尺子」）

FRED_URL = "https://fred.stlouisfed.org/graph/fredgraph.csv?id=DFII10"

# ---------------------------------------------------------------- 参数（因子层权威源）
MA_FAST, MA_SLOW = 5, 10        # 规格 §1：close 与 max/min(MA5, MA10) 比
BRK_WIN = 20                    # 突破回看
BRK_HI, BRK_LO = 0.995, 1.005   # 距 20 日高/低 0.5% 内算「贴边」
RATE_WIN = 10                   # DFII10 变化窗口（交易日/观测）
RATE_TH = 25.0                  # 利率阈值（bp，单边）
RATE_LAG = 1                    # T 日只能看到 ≤T−LAG 的 DFII10（规格 §1/§6.1；0 仅用于断言对照）
USE_P, USE_R, USE_F = True, True, True   # 消融开关：False = 该因子记 0 分，但仍占「已就绪」名额
FLOW_STREAK = 3                 # 资金流连续同向天数（两种口径共用）
FLOW_MODE = "off"               # "off" 剔除（规格 §1 括号条款）/ "share" 上交所份额净申购（全史）/
                                # "netin" 东财主力净流入（**只有 120 天**，只能跑近期）
SIGNAL_SHARE_CODE = "518880"    # 上交所 ETF 规模表里的 SEC_CODE
FLOW_SIGN = 1                   # 资金流因子方向：+1 顺势（规格 §1）/ −1 逆势（敏感性）
SHRINK_MIN_DAYS = 3             # 份额口径：连续 ≥N 日净申购/净赎回才给分（= FLOW_STREAK）

# score -> 目标档（占「黄金仓位」的比例，§3）
MAP = {-3: 0.0, -2: 0.0, -1: 0.25, 0: 0.50, 1: 0.75, 2: 1.0, 3: 1.0}
LOW_POS = 0.0                   # 「≤−2 → 0~10%」的取值（0 = 清出；0.10 = 留观察仓，敏感性用）

START = 0.50                    # 起手档 = 中性基准（实盘用 --hold 覆盖）
HYST = 1                        # 滞后：|e* − e| ≥ HYST 档（1 档 = 25pp）才动作（§4）
EVENT_FREEZE = 2                # FOMC/非农/核心PCE ±2 个交易日内只许持有或减仓（§4）
MISSING_PENALTY = True          # 缺因子 → 置信度降一档（§3「缺失=低」）
SHRINK = "extreme"              # 低置信度的收缩范围："extreme"（默认）/"all"/"off"
STEP = 0.25                     # 一档 = 25pp


def params(**kw):
    """参数打包（回测/每日共用；敏感性扫描靠 `**{...}` 覆盖）。"""
    p = dict(MA_FAST=MA_FAST, MA_SLOW=MA_SLOW, BRK_WIN=BRK_WIN,
             BRK_HI=BRK_HI, BRK_LO=BRK_LO, RATE_WIN=RATE_WIN, RATE_TH=RATE_TH,
             RATE_LAG=RATE_LAG, USE_P=USE_P, USE_R=USE_R,
             FLOW_MODE=FLOW_MODE, FLOW_STREAK=FLOW_STREAK, USE_F=USE_F,
             FLOW_SIGN=FLOW_SIGN, SHARE_LAG=0,
             MAP=dict(MAP), LOW_POS=LOW_POS, START=START, HYST=HYST,
             EVENT_FREEZE=EVENT_FREEZE, MISSING_PENALTY=MISSING_PENALTY,
             SHRINK=SHRINK, STEP=STEP)
    p.update(kw)
    return p


# ---------------------------------------------------------------- 数据：价格 / 实际利率 / 事件
def price_frame():
    """黄金信号价 = 场内 `518880` 的 OHLCV（`_idxcache` 唯一数据层，2013-07 起）。

    `high/low` 来自 `data/_ohlcv.json`（真实日内高低）；该库缺字段时 `_idxcache` 用
    close 回填 —— 那时「20 日高低」退化成收盘极值，报告里会标注。
    **盘中半根 bar 按北京 15:10 剔除**（规格 §5 执行口径）。
    """
    if _VL not in sys.path:
        sys.path.insert(0, _VL)
    import _idxcache
    df = _idxcache.frame("gold")
    if len(df) and pd.Timestamp(df.index[-1]).date() == pd.Timestamp.now().date():
        now = pd.Timestamp.now()
        if now.hour * 60 + now.minute < 15 * 60 + 10:
            df = df.iloc[:-1]
    return df.dropna(subset=["close"])


def read_rate(path=RATE_CSV):
    """读 FRED DFII10 缓存 -> Series(index=date, value=%)。缺文件直接抛（fail-fast）。"""
    if not os.path.exists(path):
        raise FileNotFoundError(f"缺 {os.path.relpath(path, _ROOT)}（先跑 "
                                f"python strategies/vol_legs/v5_gold_score/factors.py --refresh-rate）")
    df = pd.read_csv(path)
    col = "value" if "value" in df.columns else df.columns[-1]
    s = pd.Series(pd.to_numeric(df[col], errors="coerce").to_numpy(),
                  index=pd.to_datetime(df.iloc[:, 0])).dropna().sort_index()
    return s


def refresh_rate(path=RATE_CSV, timeout=25):
    """从 FRED 增量重取 DFII10（`observation_date,DFII10`）-> (Series, 说明)。

    全量覆盖写（原子替换）：这条例行序列只有 6000 行，增量收益不值得多一份拼接逻辑。
    """
    import requests
    r = requests.get(FRED_URL, timeout=timeout)
    r.raise_for_status()
    df = pd.read_csv(io.StringIO(r.text))
    df.columns = ["date", "value"]
    df["value"] = pd.to_numeric(df["value"], errors="coerce")
    df = df.dropna().sort_values("date")
    os.makedirs(os.path.dirname(path), exist_ok=True)
    tmp = path + ".tmp"
    df.to_csv(tmp, index=False)
    os.replace(tmp, path)
    s = pd.Series(df["value"].to_numpy(), index=pd.to_datetime(df["date"]))
    return s, f"FRED DFII10 {s.index[0].date()} ~ {s.index[-1].date()}（{len(s)} 行）"


def rate_is_stale(path=RATE_CSV, days=1):
    try:
        s = read_rate(path)
    except (OSError, ValueError):
        return True
    return bool(s.index[-1] < pd.Timestamp.now().normalize() - pd.Timedelta(days=days))


def read_flow(path=FLOW_CSV):
    """东财 518880 主力净流入（元）。缺文件 / 太短 -> None（资金流因子不计分）。"""
    if not os.path.exists(path):
        return None
    try:
        df = pd.read_csv(path)
    except (OSError, ValueError):
        return None
    if not {"date", "main_net"} <= set(df.columns):
        return None
    s = pd.Series(pd.to_numeric(df["main_net"], errors="coerce").to_numpy(),
                  index=pd.to_datetime(df["date"])).dropna().sort_index()
    return s if len(s) else None


def refresh_flow(path=FLOW_CSV, timeout=20):
    """抓东财 `fflow/daykline`（**只回 120 根**，写进缓存当「近期提示」用，不进计分）。"""
    import requests
    url = ("https://push2his.eastmoney.com/api/qt/stock/fflow/daykline/get"
           "?lmt=0&klt=101&secid=1.518880&fields1=f1,f2,f3,f7"
           "&fields2=f51,f52,f53,f54,f55,f56,f57,f58,f59,f60,f61,f62,f63,f64,f65")
    r = requests.get(url, timeout=timeout)
    r.raise_for_status()
    rows = (r.json().get("data") or {}).get("klines") or []
    if not rows:
        raise RuntimeError("东财 fflow 返回空")
    out = [dict(date=p[0], main_net=float(p[1])) for p in (x.split(",") for x in rows)]
    df = pd.DataFrame(out).sort_values("date")
    os.makedirs(os.path.dirname(path), exist_ok=True)
    tmp = path + ".tmp"
    df.to_csv(tmp, index=False)
    os.replace(tmp, path)
    return df, f"{df['date'].iloc[0]} ~ {df['date'].iloc[-1]}（{len(df)} 根，无全史）"


def read_shares(path=SHARE_CSV):
    """上交所 518880 每日份额（万份）-> Series。缺文件返回 None。"""
    if not os.path.exists(path):
        return None
    try:
        df = pd.read_csv(path)
    except (OSError, ValueError):
        return None
    if not {"date", "tot_vol_wan"} <= set(df.columns) or not len(df):
        return None
    return pd.Series(pd.to_numeric(df["tot_vol_wan"], errors="coerce").to_numpy(),
                     index=pd.to_datetime(df["date"])).dropna().sort_index()


def refresh_shares(path=SHARE_CSV, dates=None, sleep=0.04, progress=200, timeout=20):
    """从**上交所**补 518880 每日份额（`TOT_VOL` 万份）—— 主力净流入的全史替代口径。

    来源：`query.sse.com.cn/commonQuery.do` 的 ETF 规模表（`STAT_DATE` 逐日，一页整表，
    `pageSize=2000` 时一次请求就能拿到当日全部 ETF）。**2013-07-29 起有全史**
    （东财 `fflow` 只有 120 天，规格 §1 因此剔除了资金流因子）。

    增量 + 断点续传：只补缓存里没有的交易日；每 `progress` 个交易日落一次盘，
    中途断掉重跑会接着补。返回 (Series, 说明)。
    """
    import requests
    px = price_frame()
    want = list(pd.DatetimeIndex(px.index if dates is None else dates).normalize().unique())
    old = read_shares(path)
    have = set() if old is None else set(old.index.normalize())
    todo = [d for d in want if d not in have]
    sess = requests.Session()
    sess.headers.update({"User-Agent": "Mozilla/5.0", "Referer": "http://www.sse.com.cn/"})
    rows = [] if old is None else [
        dict(date=d.date().isoformat(), tot_vol_wan=float(v)) for d, v in old.items()]
    added, miss = 0, 0
    for i, d in enumerate(todo, 1):
        q = {"sqlId": "COMMON_SSE_ZQPZ_ETFZL_XXPL_ETFGM_SEARCH_L", "isPagination": "true",
             "pageHelp.pageSize": "2000", "pageHelp.pageNo": "1", "pageHelp.beginPage": "1",
             "pageHelp.cacheSize": "1", "pageHelp.endPage": "1",
             "STAT_DATE": d.date().isoformat()}
        try:
            r = sess.get("https://query.sse.com.cn/commonQuery.do", params=q, timeout=timeout)
            hit = [x for x in (r.json().get("result") or [])
                   if str(x.get("SEC_CODE")) == SIGNAL_SHARE_CODE]
        except Exception:  # noqa: BLE001
            miss += 1
            continue
        if hit and hit[0].get("TOT_VOL") not in (None, "", "-"):
            rows.append(dict(date=d.date().isoformat(), tot_vol_wan=float(hit[0]["TOT_VOL"])))
            added += 1
        else:
            miss += 1
        if progress and i % progress == 0:
            _write_shares(rows, path)
            print(f"  [份额] {i}/{len(todo)}  已补 {added}  缺失 {miss}  最新 {rows[-1]['date']}")
        if sleep:
            import time
            time.sleep(sleep)
    _write_shares(rows, path) if rows else None
    s = read_shares(path)
    note = (f"SSE 份额 {s.index[0].date()} ~ {s.index[-1].date()}（{len(s)} 日，本次补 {added}，"
            f"缺 {miss}）") if s is not None and len(s) else "SSE 份额：无数据"
    return s, note


def _write_shares(rows, path):
    df = pd.DataFrame(rows).drop_duplicates(subset=["date"]).sort_values("date")
    os.makedirs(os.path.dirname(path), exist_ok=True)
    tmp = path + ".tmp"
    df.to_csv(tmp, index=False)
    os.replace(tmp, path)


def event_days(index):
    """事件日集合（FOMC 用真实日历；非农 = 每月首个周五；核心 PCE ≈ 每月末最后交易日）。

    非农/核心PCE 是**发布日近似**（真实的节假日顺延没有建模），只在每日/回测里当
    「禁加仓窗口」用，误差最多挪动 1 个交易日，报告 §10 已如实标注。
    """
    days = set()
    if os.path.exists(FOMC_CSV):
        df = pd.read_csv(FOMC_CSV)
        col = "date" if "date" in df.columns else df.columns[0]
        for d in pd.to_datetime(df[col], errors="coerce").dropna():
            days.add(pd.Timestamp(d).normalize())
    idx = pd.DatetimeIndex(index)
    months = pd.period_range(idx[0], idx[-1], freq="M")
    for m in months:
        # 非农：当月第一个周五
        first = pd.Timestamp(m.start_time).normalize()
        days.add(first + pd.Timedelta(days=(4 - first.weekday()) % 7))
        # 核心 PCE：BEA 惯例月末最后一个工作日
        last = pd.Timestamp(m.end_time).normalize()
        wd = last
        while wd.weekday() >= 5:
            wd -= pd.Timedelta(days=1)
        days.add(wd)
    return days


def _event_mask(index, k=EVENT_FREEZE):
    idx = pd.DatetimeIndex(index)
    days = event_days(idx)                     # 只算一次（早期版本每天算一遍，慢 3 个数量级）
    hit = pd.Series([d.normalize() in days for d in idx], index=idx)
    if k <= 0:
        return hit
    return hit.rolling(2 * k + 1, center=True, min_periods=1).max().astype(bool)


# ---------------------------------------------------------------- 因子
def f_price(px, p=None):
    """`f_p`：趋势 + 贴 20 日高/低 的「双向确认」（规格 §1）。预热不足 -> NaN。"""
    p = p or params()
    c = px["close"].astype("float64")
    hi_src = px["high"] if "high" in px else c
    lo_src = px["low"] if "low" in px else c
    ma_f = c.rolling(int(p["MA_FAST"]), min_periods=int(p["MA_FAST"])).mean()
    ma_s = c.rolling(int(p["MA_SLOW"]), min_periods=int(p["MA_SLOW"])).mean()
    hi = hi_src.rolling(int(p["BRK_WIN"]), min_periods=int(p["BRK_WIN"])).max()
    lo = lo_src.rolling(int(p["BRK_WIN"]), min_periods=int(p["BRK_WIN"])).min()
    up = (c > np.maximum(ma_f, ma_s)) & (c >= hi * float(p["BRK_HI"]))
    dn = (c < np.minimum(ma_f, ma_s)) & (c <= lo * float(p["BRK_LO"]))
    out = pd.Series(np.where(up, 1.0, np.where(dn, -1.0, 0.0)), index=c.index)
    ready = ma_f.notna() & ma_s.notna() & hi.notna() & lo.notna()
    return out.where(ready), ready


def rate_change(rate, win=RATE_WIN):
    """DFII10 的 `win` 个观测变化（bp）。"""
    return rate.sort_index().diff(int(win)) * 100.0


def f_rate(rate, index, p=None):
    """`f_r`：**滞后 1 日**的实际利率变化 -> (f_r, 用到的利率观测日, chg)。

    T 日只能看到 ≤T−1 的 DFII10（美东收盘在北京时间 T 日凌晨，T 日 A 股收盘时固然已知，
    但规格 §1/§6.1 要求按 T−1 处理 —— 这里取最严口径，`allow_exact_matches=False`）。
    """
    p = p or params()
    chg = rate_change(rate, p["RATE_WIN"])
    left = pd.DataFrame({"date": pd.DatetimeIndex(index)}).sort_values("date")
    right = pd.DataFrame({"rate_date": chg.index, "chg": chg.to_numpy()})
    m = pd.merge_asof(left, right, left_on="date", right_on="rate_date",
                      direction="backward",
                      allow_exact_matches=(int(p["RATE_LAG"]) == 0))
    v = m["chg"].to_numpy(dtype="float64")
    th = float(p["RATE_TH"])
    val = np.where(np.isnan(v), np.nan,
                   np.where(v <= -th, 1.0, np.where(v >= th, -1.0, 0.0)))
    out = pd.Series(val, index=pd.DatetimeIndex(index))
    used = pd.Series(pd.to_datetime(m["rate_date"]).to_numpy(), index=out.index)
    return out, used, pd.Series(v, index=out.index)


def f_flow(flow, index, p=None):
    """`f_f`：主力净流入连续 ≥`FLOW_STREAK` 日同向 -> ±1（无全史 -> 返回全 NaN）。"""
    p = p or params()
    k = int(p["FLOW_STREAK"])
    if flow is None or not len(flow):
        return pd.Series(np.nan, index=pd.DatetimeIndex(index))
    s = flow.reindex(pd.DatetimeIndex(index)).ffill()
    pos = (s > 0).astype(int)
    neg = (s < 0).astype(int)
    run_pos = pos.groupby((pos == 0).cumsum()).cumsum()
    run_neg = neg.groupby((neg == 0).cumsum()).cumsum()
    out = np.where(run_pos >= k, 1.0, np.where(run_neg >= k, -1.0, 0.0))
    return pd.Series(out, index=pd.DatetimeIndex(index)).where(s.notna())


def f_share(shares, px, p=None):
    """`f_f` · **份额口径**（全史可用）：518880 每日份额的净申购/净赎回连续 ≥N 日 → ±1。

    为什么用份额而不是「主力净流入」：ETF 的真实资金流是**申购赎回**（份额变化），
    东财的「主力净流入」是对成交单按金额分档的统计，对 ETF 既不代表申赎、也只回 120 天
    （见 `research/flow_source/_probe_flow.md`）。上交所每日规模表给出 `TOT_VOL`（万份），
    **2013-07-29 起全史**，`Δ份额 × 收盘价 ≈ 当日净申购金额`。

    实现：`sh = 份额 ffill 到交易日`，`d = sh.diff()`；连续 ≥N 日 `d > 0` → +1、连续 ≥N 日
    `d < 0` → −1、其余 0；`d == 0`（当日无申赎）**中断**连续计数（严格口径，不用「忽略 0」）。
    份额未更新到当日时，用最近一次已知值（`ffill`），并在 `p["SHARE_LAG"]` 里可加滞后。
    """
    p = p or params()
    k = int(p["FLOW_STREAK"])
    idx = pd.DatetimeIndex(px.index)
    if shares is None or not len(shares):
        return pd.Series(np.nan, index=idx)
    lag = int(p.get("SHARE_LAG", 0))
    s = shares.reindex(idx.union(shares.index)).sort_index().ffill().reindex(idx)
    if lag:
        s = s.shift(lag)
    d = s.diff()
    pos = (d > 0).astype(int)
    neg = (d < 0).astype(int)
    run_pos = pos.groupby((pos == 0).cumsum()).cumsum()
    run_neg = neg.groupby((neg == 0).cumsum()).cumsum()
    out = np.where(run_pos >= k, 1.0, np.where(run_neg >= k, -1.0, 0.0))
    return pd.Series(out, index=idx).where(d.notna())


def score_of(fp, fr, ff=None):
    """-> (score, n_factors, n_nonzero)。缺失因子记 0 分但计入 `n_factors`（缺一档）。"""
    cols = [fp, fr] + ([ff] if ff is not None else [])
    df = pd.concat(cols, axis=1)
    n_f = df.notna().sum(axis=1).astype(int)
    score = df.fillna(0.0).sum(axis=1).round().astype(int)
    return score, n_f, (df.fillna(0.0) != 0).sum(axis=1).astype(int)


def conf_of(score, n_f, n_nz):
    """置信度（§3）：同向=高；两因子=中；分歧/缺失=低。"""
    conf = []
    for sc, nf, nz in zip(score.to_numpy(), n_f.to_numpy(), n_nz.to_numpy()):
        if nz == 0:
            conf.append("低")
        elif nf < 2 or nz < 2:
            conf.append("低")
        elif abs(sc) == nz:                     # 所有非零因子同向且无反向票
            conf.append("高" if nf >= 3 and nz >= 3 else "中")
        else:
            conf.append("低")
    return pd.Series(conf, index=score.index, dtype=object)


def _shrink(t, mode="all", step=STEP):
    """低置信度：目标档向 50% 收缩一档（§3）。

    - `all`（默认，规格 §3 字面）：75%→50%、50% 不动、25%→50%、100%→75%、0%→25%；
    - `extreme`：只压极端档（100%→75%、0%→25%），中间档不动；
    - `off`：不收缩（照原表，两个因子同向就能打到 100%）。
    """
    if mode == "off":
        return t
    if mode == "extreme":
        if t >= 1.0 - 1e-9:
            return t - step
        if t <= 1e-9:
            return t + step
        return t
    if t > 0.5 + 1e-9:
        return max(0.5, t - step)
    if t < 0.5 - 1e-9:
        return min(0.5, t + step)
    return t


def _downgrade(conf):
    return "中" if conf == "高" else "低"


def target_of(score, conf, n_f, p=None):
    """score + 置信度 -> 目标档（含缺因子降档与低置信度收缩）。"""
    p = p or params()
    mp = p["MAP"]
    out = []
    for sc, cf, nf in zip(score.to_numpy(), conf.to_numpy(), n_f.to_numpy()):
        t = float(mp[int(np.clip(sc, -3, 3))])
        if int(sc) <= -2:
            t = float(p["LOW_POS"])
        if p["MISSING_PENALTY"] and nf < 3:
            cf = _downgrade(cf)
        if cf == "低":
            t = _shrink(t, p["SHRINK"], float(p["STEP"]))
        out.append(round(t, 10))
    return pd.Series(out, index=score.index)


def table(px, rate=None, flow=None, p=None, shares=None):
    """因子表（回测与每日共用）：close / 三因子 / score / 置信度 / 目标档 / 事件窗口。

    `flow` = 东财主力净流入（"netin" 口径，只有 120 天）；`shares` = 上交所每日份额
    （"share" 口径，全史）—— 由 `p["FLOW_MODE"]` 决定用哪个，另一个可以不传。
    """
    p = p or params()
    fp, ready_p = f_price(px, p)
    if rate is not None and len(rate):
        fr, used, chg = f_rate(rate, px.index, p)
    else:
        fr = pd.Series(np.nan, index=px.index)
        used = pd.Series(pd.NaT, index=px.index)
        chg = pd.Series(np.nan, index=px.index)
    # 消融开关：单因子口径只是把另一个因子记 0 分，**不改就绪判据**（起算点保持可比）
    if not p["USE_P"]:
        fp = pd.Series(0.0, index=px.index).where(fp.notna())
    if not p["USE_R"]:
        fr = pd.Series(0.0, index=px.index).where(fr.notna())
    mode = p.get("FLOW_MODE", "off")
    if mode == "share":
        ff = f_share(shares, px, p)
    elif mode == "netin":
        ff = f_flow(flow, px.index, p)
    else:
        ff = None
    if ff is not None and not p["USE_F"]:
        ff = pd.Series(0.0, index=px.index).where(ff.notna())
    if ff is not None and int(p.get("FLOW_SIGN", 1)) < 0:
        ff = -ff
    score, n_f, n_nz = score_of(fp, fr, ff)
    conf = conf_of(score, n_f, n_nz)
    conf_eff = (conf.where(n_f >= 3, conf.map(lambda c: _downgrade(c)))
                if p["MISSING_PENALTY"] else conf)
    tgt = target_of(score, conf, n_f, p)
    r_ok = (pd.Series(rate.notna().reindex(px.index).to_numpy(dtype=bool), index=px.index)
            if rate is not None and len(rate) else pd.Series(True, index=px.index))
    out = pd.DataFrame(dict(close=px["close"], f_p=fp.fillna(0.0), f_r=fr.fillna(0.0),
                            score=score, n_f=n_f, conf=conf, conf_eff=conf_eff, target=tgt,
                            rate_chg=chg, rate_date=used,
                            ready=ready_p.to_numpy(dtype=bool) & r_ok.to_numpy(dtype=bool)))
    if ff is not None:
        out["f_f"] = ff.fillna(0.0)
    out["blocked"] = _event_mask(px.index, int(p["EVENT_FREEZE"]))
    return out


# ---------------------------------------------------------------- 自检
def _self_check():
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:  # noqa: BLE001
        pass
    if "--refresh-rate" in sys.argv:
        s, note = refresh_rate()
        print("已更新:", note)
    if "--refresh-flow" in sys.argv:
        df, note = refresh_flow()
        print("已更新主力净流入:", note)
    if "--refresh-shares" in sys.argv:
        s, note = refresh_shares()
        print("已更新上交所份额:", note)
    px = price_frame()
    rate = read_rate()
    shares = read_shares()
    T = table(px, rate, read_flow(), shares=shares)
    last = T.iloc[-1]
    print(f"价格 518880 {px.index[0].date()} ~ {px.index[-1].date()}（{len(px)} 根）"
          f" · high/low {'真实' if bool((px['high'] > px['low']).any()) else '用收盘回填'}")
    print(f"实际利率 {rate.index[0].date()} ~ {rate.index[-1].date()}（{len(rate)} 行，"
          f"T 日用 ≤T−1：{last['rate_date'].date()} 的 10 日变化 {last['rate_chg']:+.0f}bp）")
    print(f"最新 {px.index[-1].date()}: score={int(last['score'])}"
          f"（f_p={last['f_p']:+.0f} f_r={last['f_r']:+.0f}）"
          f" 置信度={last['conf']} 目标档={last['target']:.0%}"
          f" 事件冻结={'是' if last['blocked'] else '否'}")
    if shares is not None and len(shares):
        print(f"上交所份额 {shares.index[0].date()} ~ {shares.index[-1].date()}"
              f"（{len(shares)} 日）· 最新 {shares.iloc[-1]:,.0f} 万份")
    print(f"资金流口径：{FLOW_MODE}"
          + ("（份额净申购，全史）" if FLOW_MODE == "share" else
             "（主力净流入，仅 120 天）" if FLOW_MODE == "netin" else
             "（剔除：规格 §1 括号条款）"))


if __name__ == "__main__":
    _self_check()
