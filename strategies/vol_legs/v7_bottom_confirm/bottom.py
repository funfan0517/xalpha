# -*- coding: utf-8 -*-
"""波动资产 · V7 变种 v2：下跌后「证据计数」式抄底确认 —— 趋势反转 / 左侧风控。

来源：用户 2026-11-03 的抄底规则 v2.0（`V9抄底确认规则_v2.md`，外部定稿）。
核心差异（相对 v2 之前的四条件-AND 版）：
  - **① 地量是窗口开关（锚点），不是当日事件**：存在冻结锚点 d0 且 `t−d0 ≤ 60` 且 `t > d0`；
    锚点 = **本轮缩量段首个地量日，冻结不重置**（仅超 60 日未触发才改锚）。
  - **④ 放量基准 `V[t] > 1.5 × 前5日均量`（不含当日，严格大于）**，删掉「或 >MAV5」分支。
  - **③ 跌速衰竭换成真下压斜线** `frl_0.5 = stage_high − 0.5·slope·(t−t_H)`，
    并保留 `hist` 连 3 日收敛旁路。
  - **② 底背离以 DIF 为主判据**，且带「自背离成立日起未再创新低」的失效条款。
  - **证据计数 → 仓位**：`score = E2 + E3`（0/1/2），E1 是开关、E4 是触发，不进计数；
    首仓 = `risk_size × 0.5 × (0.75 if score==2 else 1.0)`；`score==0` 不入场。
  - **回踩不破加仓**：首仓后 10 个交易日内出现「回踩不破」→ 补满 `risk_size`。
  - **三条独立风控**：结构止损（日内 low≤stage×(1−3%)）、VWAP 止损（收盘 < AVWAP→次日清仓）、
    走平否决（|AVWAP 20 日变动|<1% → 不入场）。
  - 前置过滤 `drop_min = 15%`，且带**记忆期**：确认日前 90 个交易日内曾满足
    「MA20<MA60 且回撤≥15%」即可（`filt_memory_days=90`；0 = 要求当日，v2 原行为）。

## 0. 它是什么 / 不是什么

| 是 | 不是 |
|---|---|
| **确认型**入场（证据够了才动手） | 预测底在哪 |
| 单标的**状态机**（0/0.5/1 仓位 + 硬止损） | 自动买入信号（四条件齐现只是「允许试仓」） |
| **风控优先**：止损位在入场前就算好、且是常驻挂单 | 收益引擎（回测结论见 README §4） |

全部无前视：指标只用 ≤t；E1 窗口开关用 `d0<t`；决策 T 日收盘做、T+1 收盘成交。
"""
import math
import os
import sys

import numpy as np
import pandas as pd

try:
    sys.stdout.reconfigure(encoding="utf-8")  # type: ignore[attr-defined]
except Exception:  # noqa: BLE001
    pass

_HERE = os.path.dirname(os.path.abspath(__file__))
_VL = os.path.dirname(_HERE)                                   # strategies/vol_legs
_ROOT = os.path.dirname(os.path.dirname(_VL))                  # 仓库根
for _p in (_HERE, _VL):
    if _p not in sys.path:
        sys.path.insert(0, _p)

import engine       # noqa: E402
import klines       # noqa: E402
import rule         # noqa: E402

LABEL = "V7 抄底确认 v2（地量窗口 + 背离 + 跌速斜线 + 站回VWAP · 证据计数+回踩加仓）"

# ---------------------------------------------------------------- 1. 参数（唯一权威源）
# 对应外部规则 v2 的 CFG 表。键名扁平化以适配回测扫描（`params(**kw)` 覆盖）。
CFG = {
    # —— 锚点 / E1 载体 ——
    "dry_lookback": 60,       # 地量分位的回看窗口
    "dry_quantile": 0.05,     # 地量判据：V[t] ≤ quantile(V[t-60:t], 0.05)（实现者选择，跨标的可比）
    "dry_tol": 0.0,           # 容差（0 = 严格低于分位）
    "max_age_days": 60,       # 锚点有效期：t−d0 ≤ 60 且 t > d0；超期才改锚
    # —— E2 底背离 ——
    "div_window": 20,          # 价格创 20 日新低
    "macd": (12, 26, 9),
    # —— E3 跌速衰竭 ——
    "fibo_frac": 0.5,         # 真斜线系数
    # —— E4 触发 ——
    "vol_mult_vs_prev5": 1.5, # V[t] > 1.5 × mean(V[t-5:t])（前5日均量，不含当日；严格大于）
    "require_above_avwap": True,
    "require_bull": True,      # 当日阳线
    # —— 前置过滤 ——
    "trend_filter": True,
    "ma_fast": 20, "ma_slow": 60,
    "drop_win": 250,
    "drop_min": 0.15,         # 回撤门槛 15%（用户 2026-11-04 调整）
    "filt_memory_days": 90,   # 前置过滤记忆期：确认日前 90 个交易日内「曾」满足 MA20<MA60 且回撤≥门槛即可（0 = 要求当日，v2 原行为）
    # —— 证据计数 → 仓位 ——
    "size_mode": "risk",       # risk（按止损距离定量）/ full（一次性满额，朴素对照）
    "risk_per_trade": 0.05,
    "first_fill": 0.5,         # 首仓 = risk_size × first_fill
    "score2_discount": 0.75,   # score==2 时再乘 0.75
    "size_floor": 0.10, "size_cap": 1.00,
    "addon": "retest_hold_within_10d",  # 首仓后 10 日内回踩不破 → 补满
    "addon_days": 10,
    "addon_fill_on_expiry": True,  # 试仓期到期仍未跌破成本线 → 也补满（否则永远半仓、MA20 离场无从触发）
    # —— 风控（三条独立）——
    "stop_struct_pct": 0.03,   # 日内 low ≤ stage×(1−3%) → 当日成交（常驻挂单）
    "stop_vwap": "close_lt_avwap",  # 收盘 < AVWAP(d0,t) → 次日清仓（原文「跌回线下=判断错了」）
    "flat_veto_pct": 0.01,     # |AVWAP[t] − AVWAP[t-20]| / AVWAP[t-20] < 1% → 走平，不入场（底部横盘常态，2%→1%）
    "reentry_cooldown": 5,     # 止损/清仓后最少隔几个交易日才允许再试
    # —— 止盈 ——
    "exit_mode": "ma20",       # ma20（两段式：试仓期跌破成本线清仓/未破满仓；满仓后跌破 MA20 清仓）默认
                               # 其余：batch（1:1 减半、1:2 清尾）/ rr2 / fib618 / prior_high / none
    "rr": 2.0,
    "batch_rr": (1.0, 2.0),
    "tp_fib": 0.618,
    "ph_win": 60,
    # —— 时间止损（规则没有；可选补丁，默认不启用）——
    "max_hold": 0,
    # —— 成本 / 现金 ——
    "fee": (0.0010, 0.0050), "slip": 0.0010,
    # —— 研究开关：把某条强制置为「恒成立」，用于消融（默认全开）——
    "skip": (),                # 可含 "e1"/"e2"/"e3"/"e4"/"filt"/"flat_veto"
}

EXIT_MODES = ("ma20", "rr2", "batch", "fib618", "prior_high", "none")
EXIT_TEXT = {
    "ma20": "两段式：试仓跌破成本线清仓 / 满仓跌破 MA20 清仓",
    "rr2": "固定 1:2 盈亏比",
    "batch": "分批：1:1 减半、1:2 清尾",
    "fib618": "0.618 阻力（阶段低 + 0.618×区间）",
    "prior_high": "前高（近 ph_win 日最高价）",
    "none": "不设止盈（只靠止损 + VWAP 止损）",
}
CASH_ANNUAL = rule.CASH_ANNUAL
CASH_DAILY = (1.0 + CASH_ANNUAL) ** (1.0 / 252.0) - 1.0
LEGS = klines.LEGS
PORTFOLIO_LEGS = klines.PORTFOLIO_LEGS


def params(**kw):
    """参数打包（回测敏感性扫描用 `**{...}` 覆盖，不必改模块常量）。"""
    p = dict(CFG)
    p.update(kw)
    return p


def fee_of(slip=None):
    """-> (买入费率, 卖出费率)，含单边滑点。"""
    b, s = CFG["fee"]
    sp = CFG["slip"] if slip is None else float(slip)
    return float(b) + float(sp), float(s) + float(sp)


def weight_of(leg, legs=None):
    """组合层权重 = 该腿目标带 / 参与腿的带和（Σ=1）。"""
    legs = list(legs or PORTFOLIO_LEGS)
    return float(rule.LEGS[leg]["band"]) / float(sum(rule.LEGS[k]["band"] for k in legs))


# ---------------------------------------------------------------- 2. 指标
def macd(close, fast=12, slow=26, sig=9):
    """MACD(12,26,9)：DIF / DEA / 柱。柱 = 2×(DIF−DEA)。"""
    dif = (close.ewm(span=fast, adjust=False).mean()
           - close.ewm(span=slow, adjust=False).mean())
    dea = dif.ewm(span=sig, adjust=False).mean()
    return dif, dea, 2.0 * (dif - dea)


def atr(df, n=14):
    """ATR（真实波幅的 n 日简单均值）。"""
    h, l, c = df["high"], df["low"], df["close"]
    pc = c.shift(1)
    tr = pd.concat([h - l, (h - pc).abs(), (l - pc).abs()], axis=1).max(axis=1)
    return tr.rolling(n, min_periods=n).mean()


def indicators(df, **kw):
    """OHLCV -> 指标 + 四要素（全部只用当日及以前的数据，无前视）。"""
    p = params(**kw)
    o = df["open"].astype("float64")
    h = df["high"].astype("float64")
    l = df["low"].astype("float64")
    c = df["close"].astype("float64")
    v = df["volume"].astype("float64")
    n = len(df)
    idx = df.index
    out = pd.DataFrame(index=idx)
    out[["open", "high", "low", "close", "volume"]] = df[["open", "high", "low", "close", "volume"]]

    # —— 基础 ——
    w = int(p["dry_lookback"])
    out["lowest"] = l.rolling(w, min_periods=w).min()      # 兼容：窗口最低（= stage 起点参考）
    out["highest"] = h.rolling(w, min_periods=w).max()
    out["vol_min"] = v.rolling(w, min_periods=w).min()
    out["mav5"] = v.rolling(5, min_periods=5).mean()
    mav5_prev = out["mav5"].shift(1)                       # 前5日均量，不含当日
    dif, dea, hist = macd(c, *p["macd"])
    out["dif"], out["dea"], out["hist"] = dif, dea, hist
    out["ma_f"] = c.rolling(int(p["ma_fast"]), min_periods=int(p["ma_f"])).mean() \
        if False else c.rolling(int(p["ma_fast"]), min_periods=int(p["ma_fast"])).mean()
    out["ma_s"] = c.rolling(int(p["ma_slow"]), min_periods=int(p["ma_slow"])).mean()
    out["drop"] = 1.0 - c / c.rolling(int(p["drop_win"]), min_periods=int(p["drop_win"])).max()
    out["atr"] = atr(df, 14)

    # —— ① 地量分位 + 冻结锚点 d0 ——
    vol_q = v.rolling(w, min_periods=w).quantile(float(p["dry_quantile"]))
    dry = (v <= vol_q * (1.0 + float(p["dry_tol"]))).fillna(False)
    max_age = int(p["max_age_days"])
    d0_seq = np.full(n, -1, dtype=int)
    d0 = -1
    dry_a = dry.to_numpy(dtype=bool)
    for t in range(n):
        if d0 >= 0 and (t - d0) > max_age:
            d0 = -1
        if dry_a[t] and d0 < 0:
            d0 = t
        d0_seq[t] = d0 if (d0 >= 0 and d0 < t <= d0 + max_age) else -1
    out["d0_idx"] = d0_seq

    # —— AVWAP(d0, t)（从 d0 累计到 t，含当日）——
    tp = (h + l + c) / 3.0
    pv = (tp * v).to_numpy(dtype="float64")
    vv = v.to_numpy(dtype="float64")
    cpv = np.concatenate([[0.0], np.cumsum(np.nan_to_num(pv))])
    cv = np.concatenate([[0.0], np.cumsum(np.nan_to_num(vv))])
    avwap = np.full(n, np.nan)
    for t in range(n):
        d = d0_seq[t]
        if d >= 0:
            den = cv[t + 1] - cv[d]
            avwap[t] = (cpv[t + 1] - cpv[d]) / den if den > 0 else np.nan
    out["vwap"] = avwap

    # —— 阶段高/低 + 拐点位置（expanding from d0）——
    h_a = h.to_numpy(dtype="float64")
    l_a = l.to_numpy(dtype="float64")
    c_a = c.to_numpy(dtype="float64")
    stage_h = np.full(n, np.nan)
    stage_l = np.full(n, np.nan)
    tH = np.full(n, -1, dtype=int)
    tL = np.full(n, -1, dtype=int)
    cur_h = float("-inf"); cur_l = float("inf"); cur_Hi = -1; cur_Li = -1; prev_d0 = -999
    for t in range(n):
        d = d0_seq[t]
        if d < 0:
            cur_Hi = cur_Li = -1; prev_d0 = -999
            continue
        if d != prev_d0:
            cur_h = h_a[d]; cur_l = l_a[d]; cur_Hi = d; cur_Li = d; prev_d0 = d
        else:
            if h_a[t] > cur_h:
                cur_h = h_a[t]; cur_Hi = t
            if l_a[t] < cur_l:
                cur_l = l_a[t]; cur_Li = t
        stage_h[t] = cur_h; stage_l[t] = cur_l; tH[t] = cur_Hi; tL[t] = cur_Li
    out["stage_high"] = stage_h
    out["stage_low"] = stage_l

    # —— ② 底背离（DIF 主判据 + 失效条款）——
    dw = int(p["div_window"])
    price_newlow = (c <= c.rolling(dw, min_periods=dw).min()).fillna(False)
    dif_low = dif.rolling(dw, min_periods=dw).min()
    e2_event = (price_newlow & (dif > dif_low)).fillna(False)
    div_active = np.zeros(n, dtype=bool)
    t_b = -1
    sl_b = np.nan
    for t in range(n):
        if bool(e2_event.iloc[t]):
            t_b = t; sl_b = stage_l[t]; div_active[t] = True
        elif t_b >= 0:
            if (not math.isnan(sl_b)) and c_a[t] <= sl_b:
                div_active[t] = False; t_b = -1; sl_b = np.nan
            else:
                div_active[t] = True
    out["e2"] = div_active

    # —— ③ 跌速衰竭（真下压斜线 + hist3 旁路）——
    mask = (tH >= 0) & (tL >= 0) & (tL > tH)
    tHc = np.clip(tH, 0, n - 1)
    tLc = np.clip(tL, 0, n - 1)
    hi_v = np.where(mask, h_a[tHc], np.nan)
    lo_v = np.where(mask, l_a[tLc], np.nan)
    nn = np.where(mask, (tL - tH).astype("float64"), np.nan)
    slope = np.where(mask, (hi_v - lo_v) / nn, np.nan)
    frl = np.where(mask, hi_v - 0.5 * slope * (np.arange(n) - tHc), np.nan)
    e3_frl = mask & (c_a >= frl)
    h3 = (hist > hist.shift(1)) & (hist.shift(1) > hist.shift(2)) & \
          (hist.shift(2) > hist.shift(3)) & (hist < 0)
    e3 = e3_frl | h3.fillna(False).to_numpy(dtype=bool)
    out["e3"] = e3

    # —— ④ 触发（唯一要求「当日」的一条）——
    vol_up = (v > float(p["vol_mult_vs_prev5"]) * mav5_prev).fillna(False)
    avwap_s = pd.Series(avwap, index=idx)
    above_avwap = (c >= avwap_s) if p["require_above_avwap"] else pd.Series(True, index=idx)
    above_avwap = above_avwap.fillna(False)
    bull = (c > o) if p["require_bull"] else pd.Series(True, index=idx)
    bull = bull.fillna(False)
    e4 = vol_up & above_avwap & bull
    out["e4"] = e4

    # —— 前置过滤 ——
    trend_down = (out["ma_f"] < out["ma_s"]).fillna(False) if p["trend_filter"] \
        else pd.Series(True, index=idx)
    drop_ok = (out["drop"] >= float(p["drop_min"])).fillna(False)
    filt_now = trend_down & drop_ok
    # 记忆期：确认日前 mem 个交易日内「曾」满足即可（0 = 要求当日，v2 原行为）
    mem = int(p.get("filt_memory_days") or 0)
    if mem > 0:
        filt = (filt_now.astype(float).rolling(mem, min_periods=1).max() > 0)
    else:
        filt = filt_now
    out["filt"] = filt
    out["filt_now"] = filt_now
    out["trend_down"], out["drop_ok"] = trend_down, drop_ok

    # —— 走平否决（flat_veto）——
    avwap_20 = avwap_s.shift(20)
    d0_s = pd.Series(d0_seq, index=idx)
    same_ep = (d0_s == d0_s.shift(20)) & (d0_s >= 0) & (d0_s.shift(20) >= 0)
    avwap_20v = avwap_20.where(same_ep)
    flat_veto = ((avwap_s - avwap_20v).abs() / avwap_20v < float(p["flat_veto_pct"]))
    flat_veto = flat_veto.fillna(False)
    out["flat_veto"] = flat_veto

    # —— 证据计数 + 信号 ——
    e1 = pd.Series(d0_seq >= 0, index=idx)
    # 消融开关：把某条强制置为「恒成立」（只给研究用），在合成 score / signal 前生效
    for k in set(p.get("skip") or ()):
        if k == "e1":
            e1 = pd.Series(True, index=idx)
        elif k == "e2":
            div_active = np.ones(n, dtype=bool)
        elif k == "e3":
            e3 = np.ones(n, dtype=bool)
        elif k == "e4":
            e4 = pd.Series(True, index=idx)
        elif k == "filt":
            filt = pd.Series(True, index=idx)
        elif k == "flat_veto":
            flat_veto = pd.Series(False, index=idx)
    out["e1"] = e1
    out["e2"] = pd.Series(div_active, index=idx)
    out["e3"] = pd.Series(e3, index=idx)
    out["e4"] = e4
    out["filt"] = filt
    out["flat_veto"] = flat_veto
    score = div_active.astype(int) + e3.astype(int)
    out["score"] = score
    signal = filt & e1 & e4 & (score >= 1) & (~flat_veto)
    out["signal"] = signal.fillna(False)

    out["ready"] = (out["lowest"].notna() & out["vol_min"].notna() & out["mav5"].notna()
                    & out["ma_f"].notna() & out["ma_s"].notna() & out["drop"].notna()
                    & out["dif"].notna())
    return out


def signal(df, **kw):
    """对外最小接口：-> (signal, stop)。

    `entry` 是「t 日收盘确认、t+1 收盘试仓」的布尔序列；`stop` 是**当时**的止损位
    （阶段最低点 ×(1−stop_struct_pct)），只在 `entry` 为 True 的日子有意义。
    """
    I = indicators(df, **kw)
    return I["signal"], I["stage_low"] * (1.0 - float(params(**kw)["stop_struct_pct"]))


# ---------------------------------------------------------------- 3. 状态机
def _risk_size(entry, stop, p):
    """单笔风险预算 / 入场到止损的距离；下限 size_floor、上限 size_cap。

    `size_mode=full` 时不做风险定量、直接满额（朴素对照）。
    """
    if str(p.get("size_mode")) == "full":
        return float(p["size_cap"])
    dist = (entry - stop) / entry
    if dist <= 0:
        return 1.0
    raw = float(p["risk_per_trade"]) / dist
    return min(float(p["size_cap"]), max(float(p["size_floor"]), raw))


def _targets(entry, stop, I, t, p):
    """-> (第一目标, 第二目标)。无固定止盈时都给 +inf。"""
    mode = p["exit_mode"]
    if mode == "rr2":
        return entry + float(p["rr"]) * (entry - stop), math.inf
    if mode == "batch":
        r1, r2 = p["batch_rr"]
        return entry + float(r1) * (entry - stop), entry + float(r2) * (entry - stop)
    if mode == "fib618":
        lo = float(I["stage_low"].iloc[t]); hi = float(I["stage_high"].iloc[t])
        return lo + float(p["tp_fib"]) * (hi - lo), math.inf
    if mode == "prior_high":
        return float(I["high"].iloc[max(0, t - int(p["ph_win"]) + 1):t + 1].max()), math.inf
    return math.inf, math.inf


def state_machine(df, **kw):
    """-> DataFrame。核心列：signal / position / decide / done / exit_reason /
    stop / stop_hit / stop_fill / target。

    仓位：0（空仓）/ first_fill×risk_size（首仓，0.5×或×0.375）/ risk_size（回踩补满）。
    """
    p = params(**kw)
    I = indicators(df, **kw)
    n = len(I)
    c = I["close"].to_numpy(dtype="float64")
    o = I["open"].to_numpy(dtype="float64")
    l = I["low"].to_numpy(dtype="float64")
    d0 = I["d0_idx"].to_numpy(dtype=int)
    vwap = I["vwap"].to_numpy(dtype="float64")
    stage_l = I["stage_low"].to_numpy(dtype="float64")
    score = I["score"].to_numpy(dtype=int)
    sig = I["signal"].to_numpy(dtype=bool)
    ready = I["ready"].to_numpy(dtype=bool)
    e4 = I["e4"].to_numpy(dtype=bool)
    flat = I["flat_veto"].to_numpy(dtype=bool)
    filt = I["filt"].to_numpy(dtype=bool)
    e1 = I["e1"].to_numpy(dtype=bool)

    pos = np.zeros(n)
    decide = np.empty(n, dtype=object); decide[:] = ""
    done = np.empty(n, dtype=object); done[:] = ""
    reason = np.empty(n, dtype=object); reason[:] = ""
    stop_v = np.full(n, np.nan)
    tgt_v = np.full(n, np.nan)
    hit = np.zeros(n, dtype=bool)
    fill = np.full(n, np.nan)

    hold = 0.0
    entry_px = math.nan; stg = math.nan; stp = math.nan
    tgt = (math.inf, math.inf); entry_idx = -1; half_done = False
    block = -1; pend = None; risk_size = 0.0; addon_deadline = -1
    stop_pct = float(p["stop_struct_pct"]); reentry = int(p["reentry_cooldown"])
    addon_on = str(p["addon"]).startswith("retest")
    addon_win = int(p["addon_days"])

    for t in range(n):
        # 1) 执行 t−1 的决策（T+1 收盘成交）
        if pend is not None:
            if pend["side"] == "buy":
                entry_idx, entry_px, stg = t, float(c[t]), float(pend["stage"])
                stp = stg * (1.0 - stop_pct)
                if pend["kind"] == "first":
                    rs = _risk_size(entry_px, stp, p)
                    risk_size = rs
                    fill0 = rs * float(p["first_fill"]) * \
                             (float(p["score2_discount"]) if int(pend["score"]) == 2 else 1.0)
                    hold = min(float(p["size_cap"]), fill0)
                    addon_deadline = t + addon_win
                else:  # 回踩加仓：补满到 risk_size
                    hold = risk_size
                tgt = _targets(entry_px, stp, I, t, p)
                why = "回踩加仓补满" if pend["kind"] == "addon" else "首仓"
                done[t] = (f"{why} {hold:.0%}（score={int(pend['score'])} 四条件齐现）"
                           f"@ {entry_px:.4f} 止损 {stp:.4f}")
                reason[t] = "建仓"
            else:
                sold = min(hold, float(pend["frac"]))
                hold = max(hold - sold, 0.0)
                done[t] = f"{pend['why']} @ {c[t]:.4f}"
                reason[t] = pend["why"]
                if hold <= 1e-9:
                    hold, entry_px, stg = 0.0, math.nan, math.nan
                    stp, tgt, half_done, risk_size = math.nan, (math.inf, math.inf), False, 0.0
            pend = None

        # 2) 常驻结构止损（当日日内成交，跳空按开盘价）
        if hold > 0 and t > entry_idx and not math.isnan(stp) and l[t] <= stp:
            f = min(stp, float(o[t]))
            hit[t], fill[t] = True, f
            stop_v[t] = stp
            hold, entry_px, stg = 0.0, math.nan, math.nan
            stp, tgt, half_done, risk_size = math.nan, (math.inf, math.inf), False, 0.0
            done[t] = f"止损（low {l[t]:.4f} ≤ {f:.4f}）"
            reason[t] = f"止损(最低点−{stop_pct:.0%})"
            block = t + reentry
            pos[t] = 0.0
            continue

        # 3) 收盘决策：VWAP 止损 / 时间止损 / 止盈 / 回踩加仓
        if hold > 0 and t > entry_idx:
            ct = float(c[t])
            d0t = d0[t]; vwt = vwap[t]
            full = (risk_size > 0.0) and (hold >= risk_size - 1e-9)
            ma_t = float(I["ma_f"].iloc[t])
            if str(p["exit_mode"]) == "ma20":
                # —— 两段式：试仓期判「跌破成本线→清仓 / 未破→满仓」；满仓后只看 MA20 ——
                if not full:
                    if d0t >= 0 and not math.isnan(vwt) and ct < vwt:
                        pend = dict(side="sell", frac=hold, why="试仓期收盘跌破成本线，清仓")
                        decide[t] = "sell"
                    elif (addon_on and hold < risk_size - 1e-9 and not math.isnan(vwt)
                          and (float(l[t]) >= vwt
                               or (t >= addon_deadline
                                   and bool(p.get("addon_fill_on_expiry", True))))):
                        pend = dict(side="buy", kind="addon", stage=stg, score=2)
                        decide[t] = "buy"
                elif math.isfinite(ma_t) and ct < ma_t:
                    pend = dict(side="sell", frac=hold,
                                why=f"满仓后收盘跌破 MA{int(p['ma_fast'])}，清仓")
                    decide[t] = "sell"
            elif (str(p["stop_vwap"]) == "close_lt_avwap" and d0t >= 0
                    and not math.isnan(vwt) and ct < vwt):
                pend = dict(side="sell", frac=hold, why="收盘跌破锚定VWAP，判断失效，清仓")
                decide[t] = "sell"
            elif int(p["max_hold"]) > 0 and (t - entry_idx) >= int(p["max_hold"]):
                pend = dict(side="sell", frac=hold, why="到期退出（时间止损）")
                decide[t] = "sell"
            elif p["exit_mode"] == "batch":
                if (not half_done) and ct >= tgt[0]:
                    pend = dict(side="sell", frac=hold / 2.0, why="分批止盈 1:1 减半")
                    decide[t] = "sell"; half_done = True
                elif half_done and ct >= tgt[1]:
                    pend = dict(side="sell", frac=hold, why="分批止盈 1:2 清尾")
                    decide[t] = "sell"
            elif math.isfinite(tgt[0]) and ct >= tgt[0]:
                pend = dict(side="sell", frac=hold, why=f"止盈（{EXIT_TEXT[p['exit_mode']]}）")
                decide[t] = "sell"
            elif (pend is None and addon_on and t <= addon_deadline
                  and hold < risk_size - 1e-9 and d0t >= 0
                  and not math.isnan(vwt)):
                retest = (l[t] >= vwt) and (ct > float(o[t]))   # 回踩不破
                if retest:
                    pend = dict(side="buy", kind="addon", stage=stg, score=2)
                    decide[t] = "buy"; done[t] = ""

        elif hold <= 0 and ready[t] and sig[t] and t >= block \
                and e1[t] and e4[t] and (not flat[t]) and filt[t]:
            pend = dict(side="buy", kind="first", stage=float(stage_l[t]), score=int(score[t]))
            decide[t] = "buy"; done[t] = ""

        pos[t] = hold
        stop_v[t] = stp
        tgt_v[t] = tgt[0] if math.isfinite(tgt[0]) else np.nan

    out = pd.DataFrame(dict(signal=I["signal"], ready=I["ready"], position=pos,
                            decide=decide, done=done, exit_reason=reason,
                            stop=stop_v, target=tgt_v, stop_hit=hit,
                            stop_fill=fill), index=df.index)
    keep = [k for k in I.columns if k not in out.columns]
    return out.join(I[keep])


# ---------------------------------------------------------------- 4. 回测
def run_leg(df, sm, **kw):
    """单腿净值（起点 1.0）。日收益 = position[t−1]×标的日收益 + (1−position[t−1])×货基日息。"""
    p = params(**kw)
    fb, fs = fee_of()
    c = df["close"].to_numpy(dtype="float64")
    pos = sm["position"].to_numpy(dtype="float64")
    hit = sm["stop_hit"].to_numpy(dtype=bool)
    fill = sm["stop_fill"].to_numpy(dtype="float64")
    n = len(df)
    nav = np.ones(n)
    for t in range(1, n):
        w = pos[t - 1]
        if w > 0 and hit[t]:
            pr = fill[t] / c[t - 1] - 1.0
        elif w > 0:
            pr = c[t] / c[t - 1] - 1.0
        else:
            pr = 0.0
        r = w * pr + (1.0 - w) * CASH_DAILY
        d = pos[t] - pos[t - 1]
        r -= fb * max(d, 0.0) + fs * max(-d, 0.0)
        nav[t] = nav[t - 1] * (1.0 + r)
    return pd.Series(nav, index=df.index, name="nav")


def perf(nav):
    """绩效（复用主策略 engine.metrics，无风险利率 = 货基 CASH_ANNUAL）。"""
    return engine.metrics(nav)


def trades(df, sm):
    """按仓位变化折出成交 -> [{date, side, price, frac, reason, stage, is_addon, ...}]。"""
    c = df["close"]
    pos = sm["position"]
    hit = sm["stop_hit"]
    fill = sm["stop_fill"]
    where = {d: i for i, d in enumerate(df.index)}
    out, open_trade = [], None
    prev = 0.0
    for t, d in enumerate(df.index):
        cur = float(pos.iloc[t])
        if cur > prev + 1e-9:
            px = float(c.iloc[t])
            stage = float(sm["stage_low"].iloc[max(0, t - 1)])
            is_addon = str(sm["exit_reason"].iloc[t]) == "" and "回踩加仓" in str(sm["done"].iloc[t])
            out.append(dict(date=d, side="buy", price=px, frac=cur - prev, stage=stage,
                            reason=("回踩加仓" if is_addon else "四条件齐现"),
                            is_addon=is_addon))
            if open_trade is None:
                open_trade = dict(entry=d, entry_px=px)
        elif cur < prev - 1e-9:
            px = float(fill.iloc[t]) if bool(hit.iloc[t]) else float(c.iloc[t])
            reason = str(sm["exit_reason"].iloc[t]) or "离场"
            out.append(dict(date=d, side="sell", price=px, frac=prev - cur,
                            reason=reason, is_addon=False))
            if open_trade is not None and cur <= 1e-9:
                open_trade.update(exit=d, exit_px=px, days=(d - open_trade["entry"]).days,
                                  bars=t - where[open_trade["entry"]],
                                  pnl=px / open_trade["entry_px"] - 1.0, why=reason)
                out[-1].update(round=dict(open_trade))
                open_trade = None
        prev = cur
    return out


def round_stats(tr):
    """轮次统计：笔数 / 胜率 / 平均盈亏 / 盈亏比 / 平均持仓 / 止损占比。"""
    rs = [x["round"] for x in tr if x.get("round")]
    if not rs:
        return dict(n=0, win=None, avg=None, med=None, payoff=None, days=None,
                    stop_share=None, best=None, worst=None)
    pnl = pd.Series([r["pnl"] for r in rs], dtype="float64")
    win = pnl[pnl > 0]
    loss = pnl[pnl <= 0]
    stops = sum(1 for r in rs if str(r.get("why", "")).startswith("止损"))
    return dict(n=len(rs), win=float((pnl > 0).mean()), avg=float(pnl.mean()),
                med=float(pnl.median()),
                payoff=(float(win.mean() / abs(loss.mean()))
                        if len(win) and len(loss) else None),
                days=float(pd.Series([r["bars"] for r in rs]).median()),
                stop_share=float(stops / len(rs)),
                best=float(pnl.max()), worst=float(pnl.min()))


def portfolio(frame, positions, legs=None, fee=True, slip=None):
    """组合层净值：Σ w·[pos·标的日收益 + (1−pos)·货基] − 调仓成本（Σ=1 权重）。"""
    legs = list(legs or [k for k in frame.columns])
    w = {k: weight_of(k, legs) for k in legs}
    ret = frame.pct_change().fillna(0.0)
    fb, fs = fee_of(slip)
    P = {k: positions[k].reindex(frame.index).ffill().fillna(0.0).to_numpy(dtype="float64")
         for k in legs}
    R = {k: ret[k].to_numpy(dtype="float64") for k in legs}
    nav = 1.0
    out, inv = [], []
    prev = {k: 0.0 for k in legs}
    for i, _d in enumerate(frame.index):
        if i == 0:
            out.append(nav); inv.append(0.0); continue
        r, cost, expo = 0.0, 0.0, 0.0
        for k in legs:
            p_prev, p_now = P[k][i - 1], P[k][i]
            expo += w[k] * p_prev
            r += w[k] * (p_prev * R[k][i] + (1.0 - p_prev) * CASH_DAILY)
            if fee:
                d = p_now - prev[k]
                cost += w[k] * (fb * max(d, 0.0) + fs * max(-d, 0.0))
            prev[k] = p_now
        r -= cost
        nav *= (1.0 + r)
        out.append(nav); inv.append(expo)
    return pd.Series(out, index=frame.index, name="nav"), float(np.mean(inv))


# ---------------------------------------------------------------- 5. 读数（每日报告用）
MISS_TEXT = {
    "filt": "前置过滤（MA20<MA60 且 回撤≥阈值）",
    "e1": "地量窗口有效（锚点 d0 未过期）",
    "e2": "底背离（价格新低 / DIF 不新低）",
    "e3": "跌速放缓（0.5 真斜线 或 hist 收敛）",
    "e4": "触发（放量≥2.5×前5日均量 + 站回VWAP + 阳线）",
    "flat_veto": "走平否决（AVWAP 横盘<2%）",
}


def _why(r, p):
    """每条未通过的条件给一句人话原因。"""
    why = {}
    nan = lambda x: x != x  # noqa: E731
    if not bool(r["filt"]):
        _mem = int(p.get("filt_memory_days") or 0)
        if _mem > 0:
            why["filt"] = (f"近 {_mem} 日内未出现「MA20<MA60 且回撤≥{float(p['drop_min']):.0%}」"
                           f"（当日回撤 {float(r['drop']):.1%}）")
        elif not bool(r["trend_down"]):
            why["filt"] = "MA20 未下穿 MA60"
        else:
            why["filt"] = f"回撤 {float(r['drop']):.1%} < 门槛 {float(p['drop_min']):.0%}"
    if int(r["d0_idx"]) < 0:
        why["e1"] = "还没有地量锚点（或已过期）"
    elif not bool(r["e4"]):
        vwt = float(r["vwap"])
        if nan(vwt):
            why["e4"] = "VWAP 未成形"
        elif not bool(float(r["close"]) > float(r["open"])):
            why["e4"] = "今日非阳线"
        elif float(r["close"]) < vwt:
            why["e4"] = f"低于 VWAP {float(r['close']) / vwt - 1.0:+.1%}"
        else:
            why["e4"] = (f"量能不足，需 >{float(p['vol_mult_vs_prev5'])}×前5日均量")
    if not bool(r["e2"]):
        why["e2"] = f"近 {int(p['div_window'])} 日无有效底背离（或已失效）"
    if not bool(r["e3"]):
        why["e3"] = "跌速未衰竭（不在 0.5 斜线上方 / hist 未收敛）"
    if bool(r["flat_veto"]):
        why["flat_veto"] = "AVWAP 走平（多空拉锯），不入场"
    return why


def diagnose(leg, **kw):
    """单标的当前读数 -> dict（四要素逐条给「过/不过」+ 距阈值还有多远）。"""
    df = klines.frame(leg)
    sm = state_machine(df, **kw)
    I = indicators(df, **kw)
    p = params(**kw)
    n = len(I)
    r, i = sm.iloc[-1], I.iloc[-1]
    why = _why(r, p)
    miss = [MISS_TEXT[k] for k in ("filt", "e1", "e2", "e3", "e4", "flat_veto")
            if k == "flat_veto" and bool(r[k]) or (k != "flat_veto" and not bool(r[k]))]
    gap = {}
    if not math.isnan(float(r["vwap"])):
        gap["vwap"] = float(r["close"]) / float(r["vwap"]) - 1.0
    if not math.isnan(float(r["stage_high"])) and not math.isnan(float(r["stage_low"])):
        # 距 0.5 斜线（E3）的近似：用阶段高低中点的水平参考
        gap["fib"] = (float(r["close"]) /
                      (0.5 * (float(r["stage_high"]) + float(r["stage_low"]))) - 1.0)
    gap["vol_mult"] = (float(r["volume"]) / float(r["mav5"]) if r["mav5"] == r["mav5"] and float(r["mav5"]) > 0 else None)
    gap["drop"] = float(r["drop"]) if r["drop"] == r["drop"] else None
    return dict(
        leg=leg, name=klines.UNIVERSE[leg]["name"], code=klines.UNIVERSE[leg]["code"],
        date=df.index[-1], close=float(r["close"]), position=float(r["position"]),
        stop=(None if r["stop"] != r["stop"] else float(r["stop"])),
        target=(None if r["target"] != r["target"] else float(r["target"])),
        vwap=(None if r["vwap"] != r["vwap"] else float(r["vwap"])),
        fib=(None if r["stage_high"] != r["stage_high"] else
             0.5 * (float(r["stage_high"]) + float(r["stage_low"]))),
        lowest=(None if r["stage_low"] != r["stage_low"] else float(r["stage_low"])),
        dry_age=(None if int(r["d0_idx"]) < 0 else int(n - 1 - int(r["d0_idx"]))),
        conds={k: bool(r[k]) for k in ("filt", "e1", "e2", "e3", "e4", "flat_veto")},
        signal=bool(r["signal"]), decide=str(r["decide"]), done=str(r["done"]),
        miss=miss, why=why, gap=gap, rule=p, leg_band=float(rule.LEGS[leg]["band"]),
    )


def _self_check():
    p = params()
    print(f"{LABEL} · 自检")
    print(f"参数: 地量分位 q{p['dry_quantile']:.2f}/{p['dry_lookback']}日 · 锚点有效期 "
          f"{p['max_age_days']}日 · E4 {p['vol_mult_vs_prev5']}×前5日均量 · "
          f"背离 {p['div_window']}日 · 跌速真斜线 · 离场 {p['exit_mode']} · 试仓 {p['size_mode']}")
    print(f"过滤: MA{p['ma_fast']}<MA{p['ma_slow']} · 距 {p['drop_win']} 日高点 "
          f"≥{p['drop_min']:.0%} · 成本: 申 {p['fee'][0]:.2%}/赎 {p['fee'][1]:.2%} + 滑点 {p['slip']:.2%}")
    tot = 0
    for leg in LEGS:
        d = diagnose(leg)
        tag = "在仓" if d["position"] > 0 else ("**四要素齐现**" if d["signal"] else "空仓")
        lack = "；".join(d["why"].values())
        sig = int(bt_signal_count(leg))
        tot += sig
        print(f"  {d['name']:14s} {d['date']:%Y-%m-%d} close {d['close']:.3f} "
              f"仓位 {d['position']:.1%} · {tag}" + (f" · 缺: {lack}" if lack else ""))
    print(f"  全历史信号日合计 {tot}")


def bt_signal_count(leg, **kw):
    df = klines.frame(leg)
    sm = state_machine(df, **kw)
    return int(sm["signal"].sum())


if __name__ == "__main__":
    _self_check()
