# -*- coding: utf-8 -*-
"""成长/红利风格轮动 v3 · R 绝对水位 + 双侧趋势闸门 + 现金仓（无前视）

时序：用 `R[t-1]` 与两侧 `趋势[t-1]` 决策，权重 `W[t]` 从下一交易日生效。

三资产权重 W = [成长, 红利, 现金]，每行和恒为 1：
  1. **R 层**定方向：R ≤ 0.35 → 目标 (1,0,0)；R ≥ 0.65 → 目标 (0,1,0)；中间带保持 R 层状态
  2. **趋势层**按侧重削减：创业板指 < MA200 → 成长部分 ×(1-TREND_CUT)；
     中证红利 < MA200 → 红利部分 ×(1-TREND_CUT)。**削掉的部分进现金**，不改投另一侧
  3. **冷却期**：距上次切换不足 COOL_BARS 交易日则挂起，到期后若目标仍成立才执行

削减时用「因子相乘」，因此天然支持分层（如同时给 (120,0.5) 与 (200,1.0) 两道闸门）。
"""
import numpy as np
import pandas as pd

import rule
import factors


def target_weights(r, g, d, low=None, high=None, ma_window=None, cut=None,
                   cool=None, init_side=None, use_trend=None, cash_yield=None,
                   cut_growth=None, cut_div=None, redirect="cash"):
    """R + 两侧收盘 -> 三资产目标权重 DataFrame(growth, div, cash)，index=r.index。

    `cut_growth` / `cut_div` 可分别覆盖两侧的削减比例（默认都取 `cut`），
    用于做「单侧闸门」与「双侧闸门」的对照实验。

    `redirect` 决定**削掉的仓位去哪**：
      - `"cash"`（默认，本方案）：进现金仓。
      - `"div"`：并到红利侧。**只用于「减仓后转红利」的反例对照**，
        且只在单侧闸门（`cut_div=0`）下语义清晰；双侧同时削减时会失真。
    """
    low = rule.RATIO_LOW if low is None else low
    high = rule.RATIO_HIGH if high is None else high
    ma_window = rule.MA_TREND if ma_window is None else ma_window
    cut = rule.TREND_CUT if cut is None else cut
    cut_growth = cut if cut_growth is None else cut_growth
    cut_div = cut if cut_div is None else cut_div
    cool = rule.COOL_BARS if cool is None else cool
    init_side = rule.INIT_SIDE if init_side is None else init_side
    use_trend = rule.TREND_USE if use_trend is None else use_trend

    n = len(r)
    rv = r.values.astype(float)
    gv = g.reindex(r.index).values.astype(float)
    dv = d.reindex(r.index).values.astype(float)
    ma_g = factors.trend_ma(g.reindex(r.index), ma_window).values
    ma_d = factors.trend_ma(d.reindex(r.index), ma_window).values

    state = 0 if init_side == "div" else 1        # 0=红利侧 1=成长侧
    cur = np.array([0.0, 1.0, 0.0])
    last = -10 ** 9
    out = np.zeros((n, 3))
    out[0] = cur

    for t in range(1, n):
        # ---- R 层 ----
        x = rv[t - 1]
        if not np.isnan(x):
            if x <= low:
                state = 1
            elif x >= high:
                state = 0
        b_g = 1.0 if state == 1 else 0.0
        b_d = 1.0 - b_g

        # ---- 趋势层：两侧各自削减，削掉的进现金 ----
        e_g, e_d = b_g, b_d
        if use_trend:
            i = t - 1
            if b_g > 0 and not np.isnan(ma_g[i]) and gv[i] < ma_g[i]:
                e_g = b_g * (1.0 - cut_growth)
            if b_d > 0 and not np.isnan(ma_d[i]) and dv[i] < ma_d[i]:
                e_d = b_d * (1.0 - cut_div)
        if redirect == "div":
            target = np.array([e_g, 1.0 - e_g, 0.0])   # 反例：削减后转红利
        else:
            target = np.array([e_g, e_d, max(0.0, 1.0 - e_g - e_d)])

        # ---- 冷却期 ----
        if not np.allclose(target, cur) and (t - last) >= cool:
            cur, last = target, t
        out[t] = cur

    return pd.DataFrame(out, index=r.index, columns=["growth", "div", "cash"])


def run(g, d, W, fee=None, cash_yield=None):
    """按三资产权重模拟净值（起点 1.0，与 v0/v1/v2 同口径）。"""
    fee = rule.FEE if fee is None else fee
    cash_yield = rule.CASH_YIELD if cash_yield is None else cash_yield
    idx = W.index
    gv = g.reindex(idx).astype(float).values
    dv = d.reindex(idx).astype(float).values
    Wv = W.values.astype(float)
    rg = np.concatenate([[0.0], gv[1:] / gv[:-1] - 1.0])
    rd = np.concatenate([[0.0], dv[1:] / dv[:-1] - 1.0])
    rc = cash_yield / 252.0

    eq = np.ones(len(idx))
    switches = []
    for t in range(1, len(idx)):
        turn = float(np.abs(Wv[t] - Wv[t - 1]).sum())
        eq[t] = eq[t - 1] * (1.0 + Wv[t, 0] * rg[t] + Wv[t, 1] * rd[t]
                             + Wv[t, 2] * rc - fee * turn)
        if turn > 1e-12:
            switches.append(dict(date=str(idx[t].date()),
                                 from_w=[round(float(x), 4) for x in Wv[t - 1]],
                                 to_w=[round(float(x), 4) for x in Wv[t]],
                                 turn=round(turn, 4), fee=round(float(fee * turn), 6)))
    return dict(eq=pd.Series(eq, index=idx), W=W, switches=switches,
                params=dict(fee=fee, cash_yield=cash_yield))


def describe_switches(switches, r, W):
    """给切换事件补上决策用 R 值，并按主方向标注动作名。"""
    out = []
    for s in switches:
        dt = pd.Timestamp(s["date"])
        prev = r.index[r.index < dt]
        val = float(r.loc[prev[-1]]) if len(prev) else float("nan")
        dg = s["to_w"][0] - s["from_w"][0]
        dd = s["to_w"][1] - s["from_w"][1]
        if abs(dg) >= abs(dd):
            action = "加仓成长" if dg > 0 else "减仓成长"
        else:
            action = "加仓红利" if dd > 0 else "减仓红利"
        out.append(dict(s, R_prev=None if np.isnan(val) else round(val, 4), action=action))
    return out


def hold_legs(W):
    """按权重签名切持仓段，返回 [(起始位置, 结束位置, 段内权重)]。"""
    sig = [tuple(np.round(x, 3)) for x in W.values]
    legs, start = [], 0
    for t in range(1, len(sig)):
        if sig[t] != sig[t - 1]:
            legs.append((start, t - 1, sig[start]))
            start = t
    legs.append((start, len(sig) - 1, sig[start]))
    return legs


def metrics(eq):
    """净值 -> 年化 / 最大回撤 / 波动 / 夏普 / Calmar / 总收益。"""
    s = eq / eq.iloc[0]
    ret = float(s.iloc[-1] - 1.0)
    y = max((s.index[-1] - s.index[0]).days / 365.0, 1e-9)
    ann = (1 + ret) ** (1 / y) - 1 if ret > -1 else -1.0
    dd = float((s / s.cummax() - 1).min())
    rr = s.pct_change().dropna()
    vol = float(rr.std() * np.sqrt(252))
    shp = float(rr.mean() / rr.std() * np.sqrt(252)) if rr.std() > 0 else 0.0
    return dict(ann=ann, dd=dd, vol=vol, shp=shp, ret=ret, years=y,
                calmar=ann / abs(dd) if dd else float("nan"))
