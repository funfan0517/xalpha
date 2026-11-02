# -*- coding: utf-8 -*-
"""成长/红利风格轮动 v5 · R 水位 + 双侧趋势闸门 + **波动率目标** + 现金仓（无前视）

时序：用 `R[t-1]`、两侧 `趋势[t-1]` 与 `波动率[t-1]` 决策，权重 `W[t]` 从下一交易日生效。

三资产权重 W = [成长, 红利, 现金]，每行和恒为 1：

  1. **R 层**：R ≤ 0.35 → 目标满仓成长；R ≥ 0.65 → 目标满仓红利；中间带保持状态
  2. **趋势层**：创业板指 < MA200 → 成长部分 ×(1-cut_growth)；
     中证红利 < MA200 → 红利部分 ×(1-cut_div)。削掉的部分进现金
  3. **波动层**：`scale_t = min(1, target_vol / 已实现波动_{t-1})`，
     把前两层算出的成长/红利**等比缩放**，腾出的权重全部进现金；死区防抖

为什么用「两遍模拟」：
  波动率必须从**未缩放**的策略净值上估（即第 1、2 层的 base 净值）。
  若用缩放后的净值反馈，scale 与收益互为因果，会得到一条在实盘无法复现的曲线。
  因此第 1 遍算出 base 净值 → 取滚动波动 → 第 2 遍按 scale 重新模拟成最终净值。
"""
import numpy as np
import pandas as pd

import rule
import factors


def target_weights(r, g, d, low=None, high=None, ma_window=None, cut=None,
                   cool=None, init_side=None, use_trend=None,
                   cut_growth=None, cut_div=None):
    """R + 两侧收盘 -> 三资产**基础**目标权重（不含波动层），index=r.index。"""
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
        i = t - 1
        x = rv[i]
        if not np.isnan(x):
            if x <= low:
                state = 1
            elif x >= high:
                state = 0
        b_g = 1.0 if state == 1 else 0.0
        b_d = 1.0 - b_g

        e_g, e_d = b_g, b_d
        if use_trend:
            if b_g > 0 and not np.isnan(ma_g[i]) and gv[i] < ma_g[i]:
                e_g = b_g * (1.0 - cut_growth)
            if b_d > 0 and not np.isnan(ma_d[i]) and dv[i] < ma_d[i]:
                e_d = b_d * (1.0 - cut_div)
        tgt = np.array([e_g, e_d, max(0.0, 1.0 - e_g - e_d)])

        if not np.allclose(tgt, cur) and (t - last) >= cool:
            cur, last = tgt, t
        out[t] = cur

    return pd.DataFrame(out, index=r.index, columns=["growth", "div", "cash"])


def apply_scale(W, scale):
    """把基础权重按 scale 等比缩放（成长、红利同时缩），腾出的进现金。"""
    X = W.copy()
    s = np.asarray(scale, dtype=float)
    X["growth"] = X["growth"].values * s
    X["div"] = X["div"].values * s
    X["cash"] = 1.0 - X["growth"] - X["div"]
    return X


def fixed_exposure(W, k):
    """恒定 k 倍暴露（其余现金）—— 用于「是不是只靠降仓位」的对照实验。"""
    X = W.copy()
    X["growth"] *= k
    X["div"] *= k
    X["cash"] = 1.0 - X["growth"] - X["div"]
    return X


def throttle(W, min_gap):
    """对**最终权重**统一限速：任何两次变化之间至少隔 min_gap 个交易日。

    必要性：R/趋势层有 COOL_BARS、缩放层有死区，但两层各自限速时仍可能前后只差 1 天
    （t 日换档、t+1 日改缩放），产生「最短持仓 0 天、多段 <7 天」——踩场外 C 类赎回费。
    被挂起的调整不会丢失：一旦间隔够了，下一次比较就会把当时最新的目标落下去。
    """
    if not min_gap:
        return W
    X = W.copy()
    vals = X.values.astype(float).copy()
    last = -10 ** 9
    for t in range(1, len(vals)):
        if not np.allclose(vals[t], vals[t - 1]):
            if (t - last) < min_gap:
                vals[t] = vals[t - 1]
            else:
                last = t
    X.iloc[:, :] = vals
    return X


def _sim(g, d, W, fee, cash_yield):
    """按权重模拟净值，返回 (净值 Series, 切换列表)。"""
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
    return pd.Series(eq, index=idx), switches


def run(g, d, W, fee=None, cash_yield=None, target_vol=None, vol_window=None,
        deadband=None, min_rebalance=None, floor=None):
    """基础权重 -> 最终净值（含波动层）。

    target_vol=None 且 rule.VOL_USE=True 时取 rule.VOL_TARGET；
    显式传 target_vol=0 / VOL_USE=False 则完全退化为 v3（内置对照）。
    """
    fee = rule.FEE if fee is None else fee
    cash_yield = rule.CASH_YIELD if cash_yield is None else cash_yield
    if target_vol is None and rule.VOL_USE:
        target_vol = rule.VOL_TARGET
    vol_window = rule.VOL_WINDOW if vol_window is None else vol_window
    deadband = rule.VOL_DEADBAND if deadband is None else deadband
    min_rebalance = rule.MIN_REBALANCE if min_rebalance is None else min_rebalance

    base_eq, base_switches = _sim(g, d, W, fee, cash_yield)
    if not target_vol:
        return dict(eq=base_eq, W=W, base_W=W, scale=None, vol=None,
                    switches=base_switches, base_switches=base_switches,
                    params=dict(fee=fee, cash_yield=cash_yield, target_vol=None))

    vol = factors.realized_vol(base_eq, vol_window)
    scale = factors.vol_scale(vol, target_vol, deadband, floor)
    Ws = throttle(apply_scale(W, scale), min_rebalance)
    eq, switches = _sim(g, d, Ws, fee, cash_yield)
    return dict(eq=eq, W=Ws, base_W=W, scale=scale, vol=vol,
                switches=switches, base_switches=base_switches,
                params=dict(fee=fee, cash_yield=cash_yield, target_vol=target_vol,
                            vol_window=vol_window, deadband=deadband,
                            min_rebalance=min_rebalance))


def describe_switches(switches, r):
    """给切换事件补上决策用 R 值与动作名（按主导方向）。"""
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
