# -*- coding: utf-8 -*-
"""成长/红利风格轮动 v3 · R 绝对水位 + MA250 趋势闸门（无前视）

时序与 v0/v1/v2 完全一致：用 `R[t-1]` 与 `趋势[t-1]` 决策，权重 `w[t]` 从下一交易日生效。

两层结构：
  R 层（同 v2）  : R ≤ 0.35 → 1.0；R ≥ 0.65 → 0.0；中间带 → 保持 R 层原状态
  趋势层（v3 新增）: 创业板指跌破 MA250 时把成长权重压到 `TREND_CAP`（默认 0 = 清仓）

两种叠加方式（`mode`）：
  "cap"    —— 严格过滤：`w = min(w_R, TREND_CAP if 破线 else ∞)`。字面意义的「加一层过滤」。
              破线清仓后，靠 R 层状态恢复（若 R 层本身是空仓，就不会恢复 → 死锁仍在）。
  "switch" —— 两层分工：极端水位由 R 强制（≤0.35 必满、≥0.65 必空），**中间带交给趋势**
              （站上 MA250 持有、破线空仓）。彻底消除死锁，但语义已不是「过滤」而是「分工」。

`COOL_BARS` 是两次切换之间的最小间隔（交易日），用于把趋势带来的高频切换压到场外 C 类
基金可执行的频率（<7 天赎回费 1.5%）。冷却期间目标变化被挂起，到期后若目标仍成立才执行。
"""
import numpy as np
import pandas as pd

import rule
import factors


def signal_weights(r, g, low=None, high=None, w_low=None, w_high=None,
                   ma_window=None, trend_cap=None, cool=None, use_trend=None,
                   init_side=None, mode="cap"):
    """R 序列 + 创业板指收盘 -> 目标权重序列 w[t]（w[t] 由 t-1 的信息决定）。"""
    low = rule.RATIO_LOW if low is None else low
    high = rule.RATIO_HIGH if high is None else high
    w_low = rule.W_AT_LOW if w_low is None else w_low
    w_high = rule.W_AT_HIGH if w_high is None else w_high
    ma_window = rule.MA_TREND if ma_window is None else ma_window
    trend_cap = rule.TREND_CAP if trend_cap is None else trend_cap
    cool = rule.COOL_BARS if cool is None else cool
    use_trend = rule.TREND_USE if use_trend is None else use_trend
    init_side = rule.INIT_SIDE if init_side is None else init_side

    n = len(r)
    rv = r.values.astype(float)
    broken = factors.trend_broken(g.reindex(r.index), ma_window).values

    cur = w_low if init_side == "growth" else w_high
    w_r = cur                       # R 层状态机（中间带保持）
    last_switch = -10 ** 9
    out = np.empty(n)
    out[0] = cur

    for t in range(1, n):
        rp = rv[t - 1]
        br = bool(broken[t - 1]) and use_trend

        # ---- R 层 ----
        if not np.isnan(rp):
            if rp <= low:
                w_r = w_low
            elif rp >= high:
                w_r = w_high
            # 中间带：w_r 不变

        # ---- 趋势层 ----
        if not use_trend:
            target = w_r
        elif mode == "cap":
            target = min(w_r, trend_cap) if br else w_r
        else:                        # "switch"：中间带交给趋势
            if not np.isnan(rp) and rp <= low:
                target = w_low
            elif not np.isnan(rp) and rp >= high:
                target = w_high
            else:
                target = trend_cap if br else w_low

        # ---- 冷却期 ----
        if target != cur and (t - last_switch) >= cool:
            cur = target
            last_switch = t
        out[t] = cur

    return pd.Series(out, index=r.index, name="w")


def run(gclose, dclose, w, fee=None):
    """按目标权重序列模拟净值（一次性本金 1.0，与 v0/v2 同口径）。"""
    fee = rule.FEE if fee is None else fee
    idx = w.index
    g = gclose.reindex(idx).astype(float).values
    d = dclose.reindex(idx).astype(float).values
    ww = w.astype(float).values
    rg = np.concatenate([[0.0], g[1:] / g[:-1] - 1.0])
    rd = np.concatenate([[0.0], d[1:] / d[:-1] - 1.0])

    eq = np.ones(len(idx))
    switches = []
    for t in range(1, len(idx)):
        turn = abs(ww[t] - ww[t - 1])
        eq[t] = eq[t - 1] * (1.0 + ww[t] * rg[t] + (1.0 - ww[t]) * rd[t] - fee * turn)
        if turn > 1e-12:
            switches.append(dict(date=str(idx[t].date()),
                                 from_w=round(float(ww[t - 1]), 4),
                                 to_w=round(float(ww[t]), 4),
                                 turn=round(float(turn), 4),
                                 fee=round(float(fee * turn), 6)))
    return dict(eq=pd.Series(eq, index=idx), w=pd.Series(ww, index=idx),
                switches=switches, params=dict(fee=fee))


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
