# -*- coding: utf-8 -*-
"""V12 · 八态趋势状态机（指标 → 结构 regime → 八态 → 许可上限 → 仓位 → 净值）。

**这是 `hs300_trend/state8_final.py::classify` 的逐句移植**（规格 v1.1 的口径），唯一的三处
改动都写在 `rule.py` 的 docstring 里：标的换成场内 7 腿 OHLCV、八态映射成许可上限、
升格补 2 日确认。判定流程严格六步，顺序不能变：

  1. 结构（长期层）：三票 `s1 = MA20>MA60` · `s2 = MA60 > MA60[−20]` · `s3 = close > MA250`，
     `S = s1+s2+s3`；
  2. 定 regime：`S ≥ 2` 连续 5 日 → 向上；`S ≤ 1` 连续 5 日 → 向下；确认期内沿用旧 regime，
     暖机段 regime=None（不出状态）；
  3. 向上 regime 内四态（急涨/慢涨/弱回调/强回调）；
  4. 向下 regime 内四态（急跌/慢跌/弱反弹/强反弹）；
  5. 反转确认：强回调连续 2 日 → regime 翻向下；强反弹连续 2 日 → 翻向上（当日状态不变，
     下一日起按新 regime 分态）；
  6. 标注：burst = |z20| ≥ 1.5、near_ph = close ≥ 120 日最高 × 0.97（不参与判定）。

两处**与参考实现逐字对齐**、但读代码容易改错的细节：

- 「弱回调」= `close > MA60` **且** `rvol ≤ 0.85`（双条件缺一不可，规格 §4③）；
- 「强反弹」只认放量 `rvol ≥ 1.2`，**前高不进判定**（规格 §4③：前高单独用样本仅 39~45 天）。

时序：T 日收盘判态、T+1 起承担仓位（`w.shift(1)`），退出用 `w.diff()` 计费 —— 与 v13 同。
"""
import os

import numpy as np
import pandas as pd

import rule


# ---------------------------------------------------------------- 指标
def indicators(df, p=None):
    """-> DataFrame(close / ma_s / ma_m / ma_l / ma_m_ref / z20 / rvol / dd20 / ph /
    near_ph / burst / ready)。`ready` 之前不出状态（暖机 WARMUP 根）。"""
    p = p or rule.params()
    c = df["close"].astype("float64")
    v = df["volume"].astype("float64")
    out = pd.DataFrame(index=df.index)
    out["close"] = c
    out["ma_s"] = c.rolling(int(p["ma_short"])).mean()
    out["ma_m"] = c.rolling(int(p["ma_mid"])).mean()
    out["ma_l"] = c.rolling(int(p["ma_long"])).mean()
    out["ma_m_ref"] = out["ma_m"].shift(int(p["slope_n"]))

    r = c.pct_change()
    sd = r.rolling(20, min_periods=20).std()
    out["z20"] = (c / c.shift(20) - 1.0) / (sd * np.sqrt(20.0))
    out["dd20"] = c / c.rolling(20).max() - 1.0
    vol_ma = v.rolling(20, min_periods=20).mean()
    out["rvol"] = v / vol_ma
    out["ph"] = c.rolling(int(p["ph_n"])).max()
    out["near_ph"] = (c >= out["ph"] * float(p["ph_mult"])) & out["ph"].notna()
    out["burst"] = out["z20"].abs() >= float(p["z_burst"])

    n = len(out)
    out["ready"] = ((np.arange(n) >= rule.WARMUP)
                    & out["ma_l"].notna() & out["ma_m_ref"].notna()
                    & out["z20"].notna() & out["rvol"].notna())
    return out


# ---------------------------------------------------------------- 六步判定
def classify(ind, p=None):
    """-> DataFrame(state / regime / regime_used / vote / z20 / rvol / dd20 / burst / near_ph)。

    `regime`    = 当日收盘后的 regime（翻转日记录的是**新** regime，与参考实现一致）;
    `regime_used` = 当日分态时实际使用的 regime（翻转日 = 旧 regime）。
    两个都保留，日报里能看出「今天状态是旧 regime 给的、明天起按新的算」。
    """
    p = p or rule.params()
    up_min = int(p["up_min"])
    vote_confirm = int(p["vote_confirm"])
    flip_n = int(p["flip_confirm"])
    z_th, rvol_hi, rvol_lo = float(p["z_th"]), float(p["rvol_hi"]), float(p["rvol_lo"])
    sticky = str(p["regime_mode"]) == "sticky"

    c = ind["close"].to_numpy(dtype=float)
    ma_s = ind["ma_s"].to_numpy(dtype=float)
    ma_m = ind["ma_m"].to_numpy(dtype=float)
    ma_l = ind["ma_l"].to_numpy(dtype=float)
    ma_m_ref = ind["ma_m_ref"].to_numpy(dtype=float)
    z20 = ind["z20"].to_numpy(dtype=float)
    rvol = ind["rvol"].to_numpy(dtype=float)
    ready = ind["ready"].to_numpy(dtype=bool)
    n = len(c)

    s1 = ma_s > ma_m
    s2 = ma_m > ma_m_ref
    s3 = c > ma_l
    vote = s1.astype(int) + s2.astype(int) + s3.astype(int)
    up_st = s1 & s2 & s3            # 仅 sticky 模式用
    dn_st = (ma_s < ma_m) & (ma_m < ma_m_ref) & (c < ma_l)

    states = np.array([None] * n, dtype=object)
    regimes = np.array([None] * n, dtype=object)
    used = np.array([None] * n, dtype=object)

    regime = None
    upn = dnn = 0        # 强回调 / 强反弹 的连续计数（第 5 步）
    vup = vdn = 0        # 多数决票的连续计数（第 2 步）

    for i in range(n):
        if not ready[i]:
            used[i] = regime
            regimes[i] = regime
            continue

        # ---- 第 1~2 步：结构 -> regime
        if sticky:
            if regime is None:
                regime = "向上" if up_st[i] else ("向下" if dn_st[i] else None)
            if regime == "向上" and dn_st[i]:
                regime, upn, dnn = "向下", 0, 0
            elif regime == "向下" and up_st[i]:
                regime, upn, dnn = "向上", 0, 0
        else:
            if vote[i] >= up_min:
                vup += 1
                vdn = 0
                if vup >= vote_confirm:
                    regime, upn, dnn = "向上", 0, 0
            else:                                   # S <= up_min-1，即 S<=1
                vdn += 1
                vup = 0
                if vdn >= vote_confirm:
                    regime, upn, dnn = "向下", 0, 0
        if regime is None:
            used[i] = None
            regimes[i] = None
            continue

        # ---- 第 3~4 步：regime 内分四态
        used[i] = regime
        if regime == "向上":
            if c[i] >= ma_s[i]:
                states[i] = "急涨" if z20[i] >= z_th else "慢涨"
                upn = dnn = 0
            else:
                weak = (c[i] > ma_m[i]) and (rvol[i] <= rvol_lo)
                states[i] = "弱回调" if weak else "强回调"
                if weak:
                    dnn = 0
                else:
                    dnn += 1
                    if dnn >= flip_n:               # 第 5 步：强回调连续 N 日 -> 翻向下
                        regime, dnn, upn = "向下", 0, 0
        else:
            if c[i] >= ma_s[i]:
                strong = rvol[i] >= rvol_hi
                states[i] = "强反弹" if strong else "弱反弹"
                if strong:
                    upn += 1
                    if upn >= flip_n:               # 第 5 步：强反弹连续 N 日 -> 翻向上
                        regime, upn, dnn = "向上", 0, 0
                else:
                    upn = 0
            else:
                states[i] = "急跌" if z20[i] <= -z_th else "慢跌"
                upn = dnn = 0

        regimes[i] = regime          # 翻转日记录新 regime（与 state8_final 一致）

    return pd.DataFrame({
        "state": states, "regime": regimes, "regime_used": used, "vote": vote,
        "z20": z20, "rvol": rvol, "dd20": ind["dd20"].to_numpy(dtype=float),
        "burst": ind["burst"].to_numpy(dtype=bool),
        "near_ph": ind["near_ph"].to_numpy(dtype=bool),
    }, index=ind.index)


# ---------------------------------------------------------------- 八态 -> 仓位
def positions(states, up_confirm=None, cap_map=None):
    """八态 -> 仓位序列（0~1 的带内比例）。空仓起步。

    `up_confirm`（默认 `rule.UP_CONFIRM`）：**升格要连续 N 日同向目标**，降格立即生效。
    这是态的边界（z20 穿 ±1.2、量比穿 1.2/0.85）本身的高频抖动与「仓位」之间的缓冲，
    只影响换手，不参与判定。
    """
    nd = rule.UP_CONFIRM if up_confirm is None else int(up_confirm)
    cmap = rule.CAP if cap_map is None else cap_map
    w = np.zeros(len(states))
    cur, pend_val, pend_cnt = 0.0, None, 0
    for i, st in enumerate(states):
        tgt = float(cmap.get(str(st), 0.0)) if st is not None and str(st) in cmap else cur
        if tgt > cur + 1e-9:
            if pend_val is not None and abs(pend_val - tgt) < 1e-9:
                pend_cnt += 1
            else:
                pend_val, pend_cnt = tgt, 1
            if pend_cnt >= max(nd, 1):
                cur, pend_val, pend_cnt = tgt, None, 0
        elif tgt < cur - 1e-9:
            cur, pend_val, pend_cnt = tgt, None, 0       # 降格立即
        else:
            pend_val, pend_cnt = None, 0
        w[i] = cur
    return pd.Series(w, index=states.index if hasattr(states, "index") else None)


# ---------------------------------------------------------------- 净值与指标
def run(df, w):
    px = df["close"]
    ret = px.pct_change().fillna(0.0)
    w_eff = w.shift(1).fillna(0.0)              # T 日收盘判态，T+1 起承担收益
    dw = w.diff().fillna(w).abs()
    cash_d = (1.0 + rule.CASH_ANNUAL) ** (1.0 / rule.TRADING_DAYS) - 1.0
    port = w_eff * ret + (1.0 - w_eff) * cash_d - dw * rule.FEE_SIDE
    return (1.0 + port).cumprod()


def perf(nav):
    nav = nav.dropna()
    ret = nav.pct_change().dropna()
    n = len(ret)
    ann = float((1.0 + ret).prod() ** (rule.TRADING_DAYS / n) - 1.0) if n else np.nan
    vol = float(ret.std() * np.sqrt(rule.TRADING_DAYS)) if n else np.nan
    mdd = float((nav / nav.cummax() - 1.0).min())
    sharpe = float((ann - rule.CASH_ANNUAL) / vol) if vol and vol > 0 else np.nan
    return dict(ann=ann, vol=vol, mdd=mdd, sharpe=sharpe, days=n,
                cum=float(nav.iloc[-1] / nav.iloc[0] - 1.0))


def actions(w):
    """相邻变化 > 1e-9 的调仓点 -> [(date, delta)]。"""
    d = w.diff().fillna(w)
    idx = d.index[d.abs() > 1e-9]
    return [(t, float(d.loc[t])) for t in idx]


# ---------------------------------------------------------------- 后验统计（规格 §3 的复现）
def state_stats(px, cx, horizon=20):
    """逐态后验：占比 / fwd20 / 胜率；外加四组配对分离度。

    用途是**验口径**：把规格 §3 的两张表在本仓库数据上重算一遍，数字不必逐位相同
    （标的是 ETF 前复权不是指数、样本长度也不同），但**相对排序与符号必须一致**
    —— 不一致说明移植错了，而不是"市场变了"。
    """
    fwd = px.shift(-horizon) / px - 1.0
    st = cx["state"]
    m = st.notna()
    rows = []
    for s in rule.STATE_ORDER:
        k = m & (st == s)
        kf = k & fwd.notna()
        if not k.sum():
            continue
        rows.append({
            "state": s, "n": int(k.sum()),
            "share": float(k.sum() / m.sum()),
            "fwd": float(fwd[kf].mean()) if kf.sum() else np.nan,
            "win": float((fwd[kf] > 0).mean()) if kf.sum() else np.nan,
        })
    pairs = []
    for a, b in [("急涨", "慢涨"), ("弱回调", "强回调"), ("强反弹", "弱反弹"), ("急跌", "慢跌")]:
        ka = m & (st == a) & fwd.notna()
        kb = m & (st == b) & fwd.notna()
        if not ka.sum() or not kb.sum():
            continue
        pairs.append({
            "a": a, "b": b,
            "dfwd": float(fwd[ka].mean() - fwd[kb].mean()),
            "dwin": float((fwd[ka] > 0).mean() - (fwd[kb] > 0).mean()),
            "na": int(ka.sum()), "nb": int(kb.sum()),
        })
    return dict(rows=rows, pairs=pairs,
                base=float(fwd[m].mean()), days=int(m.sum()))


# ---------------------------------------------------------------- 单腿回测
def backtest_leg(leg, df=None, up_confirm=None, params=None):
    """-> dict(leg, name, df, ind, cx, w, nav, perf, acts, expo, start, end, bars)。

    `start` = 第一根「出状态」的 bar（暖机之后），净值与统计都从那里起算 ——
    与 v13 的 `sm["start"]` 同口径，避免拿暖机段的 0 仓摊薄年化。
    """
    import importlib
    vdata = importlib.import_module("data")
    # 仓库里有 5 个 `data.py`（本策略根 + 各变种）。名字解析到别人身上不会报错，只会**静默换一套行情**，
    # 所以这里 fail-fast：不是本变种的 data.py 就直接抛，别让它跑出"看起来正常"的错误净值。
    if os.path.dirname(os.path.abspath(vdata.__file__)) != os.path.dirname(os.path.abspath(__file__)):
        raise RuntimeError(
            f"`import data` 解析到了 {vdata.__file__}，不是 v12_octo_trend/data.py。"
            f"把本目录插到 sys.path[0]（或先清 sys.modules 里的 'data'）再跑。")
    p = params or rule.params(leg)
    df = vdata.frame(leg) if df is None else df
    ind = indicators(df, p)
    cx = classify(ind, p)
    w = positions(cx["state"], up_confirm=up_confirm)
    first = cx["state"].notna()
    if not first.any():
        return dict(leg=leg, name=rule.NAMES[leg], df=df, ind=ind, cx=cx, w=w, nav=None,
                    perf=None, acts=[], expo=np.nan, start=None, end=df.index[-1], bars=0)
    st = int(np.argmax(first.to_numpy()))
    nav = run(df, w).loc[df.index[st]:]
    w_st = w.loc[df.index[st]:]
    return dict(leg=leg, name=rule.NAMES[leg], df=df, ind=ind, cx=cx, w=w, nav=nav,
                perf=perf(nav), acts=actions(w_st), expo=float(w_st.mean()),
                start=df.index[st], end=df.index[-1], bars=len(nav))
