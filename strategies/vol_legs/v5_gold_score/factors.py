# -*- coding: utf-8 -*-
"""V5.1 · 黄金仓位（风险预算 × 实际利率闸门）—— **因子层**（唯一口径）。

回测（`backtest.py`）与每日（`daily/_daily_scan.py`）都调本文件，杜绝两套口径。

**规则（2026-11-18 起唯一在用的口径）**：
1. 风险预算 `f_vol`：`scale = clip(VOL_TARGET / 120 日实现波动, VOL_MIN, 1)`。
   黄金波动聚集真实存在（120 日波动 8%~38%），波动冲高自动减仓 —— 风险归一化，不是 alpha。
2. 实际利率闸门 `f_rate_level`：用 DFII10 的**水平**（不是 10 日变化）。
   高于 `RATE_LEVEL_TH + BAND` 给 1.0、低于 `TH − BAND` 给 `LOW`，死区内维持上一状态。
   水平值与未来 12 个月金价收益正相关（年度非重叠点 +0.63，见
   `research/rate_level/_probe_level.md`；三段 7/7/6 点符号不稳，证据等级为中）。
3. 目标档 `target_risk`：`量化(clip(scale × gate, 0, 1), STEP=20%)` → 六档；
   T 日只用 ≤T−1 的 DFII10（`RATE_LAG=1`）。

**旧 v5.0「三因子计分 → 三档」已整体删除**：`TARGET_MODE` 分支、价格突破/利率变化/
资金流计分、置信度收缩与相关消融全部移除（结论与历史留在 git 历史，不再随策略发布）。
资金流数据只作每日报告的「近期提示」，**永不进仓位**。
同理，本文件下半部分的 `macro_cell`（DXY × CPI 格子读数）也只是「不计分项」：
外部研究发现这组月度宏观水平值接进公式后年化/回撤/夏普均无改善
（见 `research/dxy_cpi/_probe_dxy_cpi.md`）。
"""
import io
import os
import sys

import numpy as np
import pandas as pd

_HERE = os.path.dirname(os.path.abspath(__file__))        # strategies/vol_legs/v5_gold_score
_VL = os.path.dirname(_HERE)                              # strategies/vol_legs
_ROOT = os.path.dirname(os.path.dirname(_VL))             # 仓库根（推导，不写死）
_DATA = os.path.join(_ROOT, "data")
RATE_CSV = os.path.join(_DATA, "_dfii10.csv")             # FRED DFII10（数据类，全仓共用）
FLOW_CSV = os.path.join(_DATA, "_flow_518880.csv")        # 东财主力净流入（只有 120 天，仅提示）
SHARE_CSV = os.path.join(_DATA, "_etf_shares_518880.csv")  # 上交所每日份额（2013-07 起，全史，仅提示）
FOMC_CSV = os.path.join(_DATA, "_fomc_meetings.csv")
XAU_CSV = os.path.join(_DATA, "_gold_xau.csv")            # 伦敦金现（只当「跌幅尺子」）
FRED_URL = "https://fred.stlouisfed.org/graph/fredgraph.csv?id=DFII10"
SIGNAL_SHARE_CODE = "518880"

# ---------------------------------------------------------------- 参数（唯一权威源）
VOL_TARGET = 0.10               # 目标年化波动
VOL_WINDOW = 120                # 实现波动窗口（交易日）
VOL_MIN = 0.20                  # 风险预算下限
RATE_LEVEL_TH = 0.5             # DFII10 水平阈值（%）
RATE_LEVEL_BAND = 0.15          # 闸门死区（±pp）
RATE_LEVEL_LOW = 0.5            # 闸门关闭时的乘数
RATE_LAG = 1                    # T 日只用 ≤T−1 的 DFII10（0 仅供断言对照）
STEP = 0.20                     # 六档步长（0/20/40/60/80/100%）
HYST = 1                        # 跨 1 档才动
START = 0.40                    # 起手档（实盘用 --hold 覆盖）
EVENT_FREEZE = 0                # 事件冻结默认关闭（新口径下是负贡献）


def params(**kw):
    """参数打包（回测/每日共用；敏感性扫描靠 `**{...}` 覆盖）。"""
    p = dict(VOL_TARGET=VOL_TARGET, VOL_WINDOW=VOL_WINDOW, VOL_MIN=VOL_MIN,
             RATE_LEVEL_TH=RATE_LEVEL_TH, RATE_LEVEL_BAND=RATE_LEVEL_BAND,
             RATE_LEVEL_LOW=RATE_LEVEL_LOW, RATE_LAG=RATE_LAG,
             STEP=STEP, HYST=HYST, START=START, EVENT_FREEZE=EVENT_FREEZE)
    p.update(kw)
    return p


# ---------------------------------------------------------------- 数据
def price_frame():
    """黄金信号价 = 场内 `518880` 的 OHLCV（`_idxcache` 唯一数据层，2013-07 起）。

    **盘中半根 bar 按北京 15:10 剔除**（执行口径）。
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
    return pd.Series(pd.to_numeric(df[col], errors="coerce").to_numpy(),
                     index=pd.to_datetime(df.iloc[:, 0])).dropna().sort_index()


def refresh_rate(path=RATE_CSV, timeout=25):
    """从 FRED 重取 DFII10（`observation_date,DFII10`）-> (Series, 说明)，原子覆盖。

    `pipeline/intraday_preview.py` 会设 `XALPHA_PREVIEW=1`：预演版**不写任何数据文件**，
    这条缓存是跨策略共享的数据类（`data/`），所以预演一律沿用末次观测。
    """
    if os.environ.get("XALPHA_PREVIEW"):
        return read_rate(path), "预演不刷新缓存（沿用末次 FRED 观测）"
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
    """东财 518880 主力净流入（元）—— **仅每日提示**，缺文件返回 None。"""
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
    """抓东财 `fflow/daykline`（**只回 120 根**，写进缓存当「近期提示」用，不进仓位）。"""
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
    """上交所 518880 每日份额（万份）—— **仅每日提示**，缺文件返回 None。"""
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
    """补上交所 518880 每日份额（`TOT_VOL` 万份），2013-07-29 起全史。

    增量 + 断点续传：只补缓存里没有的交易日；每 `progress` 日落盘一次。
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
    if rows:
        _write_shares(rows, path)
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
    """FOMC（真实日历）/ 非农（每月首个周五）/ 核心 PCE ≈ 月末最后交易日。"""
    days = set()
    if os.path.exists(FOMC_CSV):
        df = pd.read_csv(FOMC_CSV)
        col = "date" if "date" in df.columns else df.columns[0]
        for d in pd.to_datetime(df[col], errors="coerce").dropna():
            days.add(pd.Timestamp(d).normalize())
    idx = pd.DatetimeIndex(index)
    for m in pd.period_range(idx[0], idx[-1], freq="M"):
        first = pd.Timestamp(m.start_time).normalize()
        days.add(first + pd.Timedelta(days=(4 - first.weekday()) % 7))
        last = pd.Timestamp(m.end_time).normalize()
        while last.weekday() >= 5:
            last -= pd.Timedelta(days=1)
        days.add(last)
    return days


def _event_mask(index, k=EVENT_FREEZE):
    idx = pd.DatetimeIndex(index)
    if k <= 0:
        return pd.Series(False, index=idx)
    days = event_days(idx)
    hit = pd.Series([d.normalize() in days for d in idx], index=idx)
    return hit.rolling(2 * k + 1, center=True, min_periods=1).max().astype(bool)


# ---------------------------------------------------------------- 因子（v5.1 唯一口径）
def f_vol(px, p=None):
    """风险预算：`scale = clip(VOL_TARGET / VOL_WINDOW 日实现波动, VOL_MIN, 1)`。"""
    p = p or params()
    win = int(p["VOL_WINDOW"])
    c = px["close"].astype("float64")
    vol = c.pct_change().rolling(win, min_periods=win).std() * np.sqrt(252.0)
    scale = (float(p["VOL_TARGET"]) / vol).clip(float(p["VOL_MIN"]), 1.0)
    return scale, vol, vol.notna()


def f_rate_level(rate, index, p=None):
    """实际利率**水平** -> (gate, 用到的观测日, 水平值)。

    `> TH+BAND` 开闸、`TH−BAND` 以下关闸，死区内维持上一状态；T 日用 ≤T−1。
    """
    p = p or params()
    lag = max(int(p["RATE_LAG"]), 0)
    # 右表保持原始日期（值不预先 shift）：严格 backward 匹配自然取到 ≤T−1 的观测，
    # `rate_date` 标签也就是**真正用到的那一天**（C2 断言据此检查无前视）。
    s = rate.sort_index().shift(lag - 1) if lag > 1 else rate.sort_index()
    left = pd.DataFrame({"date": pd.DatetimeIndex(index)}).sort_values("date")
    right = pd.DataFrame({"rate_date": s.index, "level": s.to_numpy()}).dropna()
    m = pd.merge_asof(left, right, left_on="date", right_on="rate_date",
                      direction="backward", allow_exact_matches=(lag == 0))
    lvl = pd.Series(m["level"].to_numpy(dtype="float64"), index=pd.DatetimeIndex(index))
    th = float(p["RATE_LEVEL_TH"])
    band = float(p.get("RATE_LEVEL_BAND", 0.0))
    low = float(p["RATE_LEVEL_LOW"])
    v = lvl.to_numpy(dtype="float64")
    out = np.empty(len(v))
    cur = low
    for i, x in enumerate(v):
        if np.isnan(x):
            out[i] = np.nan
            continue
        if x > th + band:
            cur = 1.0
        elif x < th - band:
            cur = low
        out[i] = cur
    gate = pd.Series(out, index=lvl.index)
    used = pd.Series(pd.to_datetime(m["rate_date"]).to_numpy(), index=lvl.index)
    return gate, used, lvl


def _quantize(s, step, hyst=1):
    """连续仓位量化到 `step` 网格，NaN 保留。"""
    v = s.to_numpy(dtype="float64")
    ok = ~np.isnan(v)
    base = pd.Series(v).ffill().fillna(0.0).to_numpy()
    q = np.round(base / step) * step
    out = np.empty(len(q))
    cur = q[0] if len(q) else 0.0
    for i, x in enumerate(q):
        if abs(x - cur) >= (hyst - 0.5) * step - 1e-9:
            cur = x
        out[i] = cur
    return pd.Series(np.where(ok, out, np.nan), index=s.index)


def target_risk(scale, gate, p=None):
    """六档目标：`量化(clip(scale × gate, 0, 1), STEP)`。跨档防抖由状态机统一负责。"""
    p = p or params()
    return _quantize((scale * gate).clip(0.0, 1.0), float(p["STEP"]), 1)


def table(px, rate=None, flow=None, p=None, shares=None):
    """因子表（回测与每日共用）：close / 波动 / 预算 / 利率水平 / 闸门 / 目标档 / 就绪。

    保留 `flow` / `shares` 形参只为兼容探针脚本的调用签名 —— **不进任何仓位计算**。
    """
    p = p or params()
    scale, vol, vol_ok = f_vol(px, p)
    if rate is not None and len(rate):
        gate, used, lvl = f_rate_level(rate, px.index, p)
    else:
        gate = pd.Series(np.nan, index=px.index)
        used = pd.Series(pd.NaT, index=px.index)
        lvl = pd.Series(np.nan, index=px.index)
    out = pd.DataFrame(dict(close=px["close"], vol=vol, scale=scale,
                            rate_level=lvl, gate=gate, rate_date=used,
                            target=target_risk(scale, gate, p),
                            ready=vol_ok.to_numpy(dtype=bool) & gate.notna().to_numpy(dtype=bool)))
    out["blocked"] = _event_mask(px.index, int(p["EVENT_FREEZE"]))
    return out


# ---------------------------------------------------------------- 不计分的宏观读数（DXY × CPI）
# **永不进仓位**：只给每日报告一行「当前在哪一格」，与 read_flow / read_shares 同一级别。
# 为什么只做读数：`research/dxy_cpi/_probe_dxy_cpi.md` —— 接进 scale × gate 后年化/回撤/夏普
# 均无改善，连全样本作弊上界也只有 +0.05pp。
DXY_CSV = os.path.join(_DATA, "_dxy.csv")                 # ICE 美元指数（东财 100.UDI）
CPI_CSV = os.path.join(_DATA, "_cpi.csv")                 # FRED CPIAUCSL（月度）
DX_BUCKETS = (("<95", 0, 95), ("95~97.5", 95, 97.5), ("97.5~100", 97.5, 100),
              ("100~102.5", 100, 102.5), ("102.5~105", 102.5, 105), (">105", 105, 1e9))
CPI_BUCKETS = (("<2", 0, 2), ("2~3", 2, 3), ("3~4", 3, 4), ("4~5", 4, 5), (">5", 5, 1e9))


def read_dxy(path=DXY_CSV):
    """ICE 美元指数日频 -> Series；缺文件返回 None（提示项，不 fail-fast）。"""
    if not os.path.exists(path):
        return None
    df = pd.read_csv(path)
    col = "dxy" if "dxy" in df.columns else df.columns[-1]
    return pd.Series(pd.to_numeric(df[col], errors="coerce").to_numpy(),
                     index=pd.to_datetime(df.iloc[:, 0])).dropna().sort_index()


def read_cpi_yoy(path=CPI_CSV):
    """FRED CPIAUCSL -> 同比%（index = PeriodIndex[月]）；缺文件返回 None。"""
    if not os.path.exists(path):
        return None
    df = pd.read_csv(path)
    col = "cpi" if "cpi" in df.columns else df.columns[-1]
    s = pd.Series(pd.to_numeric(df[col], errors="coerce").to_numpy(),
                  index=pd.PeriodIndex(pd.to_datetime(df.iloc[:, 0]), freq="M")).dropna().sort_index()
    return ((s / s.shift(12) - 1.0) * 100.0).dropna()


def _month_last(s):
    d = pd.DataFrame({"v": s.astype(float)})
    d["ym"] = d.index.to_period("M")
    return d.groupby("ym")["v"].last()


def macro_bucket(v, buckets):
    for name, lo, hi in buckets:
        if lo <= v < hi:
            return name
    return "n/a"


def macro_monthly(gold=None, dxy=None, yoy=None):
    """月频格子底表：period / dxy / cpi / fwd1 / fwd3（**无前视**）。

    DXY 先 `shift(1)` 再取月末（北京收盘早于美市收盘）；CPI 用 m−1 月同比
    （m 月数据 m+1 月中才发布）。`gold` 缺省用 `data/_gold_xau.csv`；缺数据返回 None。
    """
    if dxy is None:
        dxy = read_dxy()
    if yoy is None:
        yoy = read_cpi_yoy()
    if gold is None:
        if not os.path.exists(XAU_CSV):
            return None
        x = pd.read_csv(XAU_CSV)
        gold = pd.Series(pd.to_numeric(x["gold"], errors="coerce").to_numpy(),
                         index=pd.to_datetime(x["date"])).dropna().sort_index()
    if dxy is None or yoy is None or gold is None or not len(yoy):
        return None
    gm, dm = _month_last(gold), _month_last(dxy.shift(1))
    known = {p + 1: v for p, v in yoy.items()}
    idx = gm.index.intersection(dm.index).sort_values()
    rows = []
    for i, m in enumerate(idx):
        if m not in known:
            continue
        f1 = gm[idx[i + 1]] / gm[m] - 1.0 if i + 1 < len(idx) else np.nan
        f3 = gm[idx[i + 3]] / gm[m] - 1.0 if i + 3 < len(idx) else np.nan
        rows.append(dict(p=m, dxy=float(dm[m]), cpi=float(known[m]),
                         fwd1=float(f1) if np.isfinite(f1) else None,
                         fwd3=float(f3) if np.isfinite(f3) else None))
    return pd.DataFrame(rows) if rows else None


def macro_cell(table=None, asof=None):
    """当前 DXY × CPI 格子 + 该格子历史 1 月远期统计（**不计分，永不进仓位**）。

    -> dict(dxy, cpi, cell, key, n, mean, win, asof) 或 None（缺数据）。
    """
    t = macro_monthly() if table is None else table
    if t is None or not len(t):
        return None
    if asof is not None:
        t = t[t["p"] <= pd.Period(asof, freq="M")]
        if not len(t):
            return None
    last = t.iloc[-1]
    kb = macro_bucket(last["dxy"], DX_BUCKETS)
    kc = macro_bucket(last["cpi"], CPI_BUCKETS)
    key = f"{kb} × {kc}"
    sub = t[[macro_bucket(r["dxy"], DX_BUCKETS) == kb and macro_bucket(r["cpi"], CPI_BUCKETS) == kc
             for _, r in t.iterrows()]]
    vals = [float(v) for v in sub["fwd1"].tolist() if v is not None and np.isfinite(v)]
    return dict(dxy=float(last["dxy"]), cpi=float(last["cpi"]), cell=key, key=(kb, kc),
                n=len(vals),
                mean=(float(np.mean(vals)) if vals else None),
                win=(float(np.mean([v > 0 for v in vals])) if vals else None),
                asof=str(last["p"]))


# ---------------------------------------------------------------- 自检
def _self_check():
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:  # noqa: BLE001
        pass
    if "--refresh-rate" in sys.argv:
        _, note = refresh_rate()
        print("已更新:", note)
    if "--refresh-flow" in sys.argv:
        _, note = refresh_flow()
        print("已更新主力净流入提示:", note)
    if "--refresh-shares" in sys.argv:
        _, note = refresh_shares()
        print("已更新上交所份额提示:", note)
    px, rate = price_frame(), read_rate()
    shares = read_shares()
    T = table(px, rate)
    last = T.iloc[-1]
    print(f"价格 518880 {px.index[0].date()} ~ {px.index[-1].date()}（{len(px)} 根）")
    print(f"实际利率 {rate.index[0].date()} ~ {rate.index[-1].date()}（{len(rate)} 行，"
          f"T 日用 ≤T−1：{last['rate_date'].date()} 的水平 {last['rate_level']:+.2f}%）")
    print(f"最新 {px.index[-1].date()}: 波动 {last['vol']:.1%}（目标 {VOL_TARGET:.0%}）→ "
          f"预算 {last['scale']:.2f}；利率 {last['rate_level']:+.2f}% → 闸门 {last['gate']:.2f}；"
          f"目标档 {last['target']:.0%} 事件冻结={'是' if last['blocked'] else '否'}")
    if shares is not None and len(shares):
        print(f"每日提示数据：上交所份额 {shares.index[0].date()} ~ {shares.index[-1].date()}"
              f"（{len(shares)} 日；**不进仓位**）")


if __name__ == "__main__":
    _self_check()
