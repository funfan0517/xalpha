# -*- coding: utf-8 -*-
"""成长/红利风格轮动 v2 · 绝对水位带状态机（无前视）

与 v0（`../v0/engine.py`）**同一时序口径**：
  用 `R[t-1]` 决策 → 权重 `w[t]` 从下一交易日开始生效 → 赚 `ret[t] = price[t]/price[t-1]-1`。
两侧真实下单都有 1 日延迟，同受影响，故 v0 / v1 / v2 的相对比较成立。

权重是连续的 `w ∈ [0,1]`（成长占比），默认只取 0/1 两端：
  w=1.0 满仓创业板（低估加仓） / w=0.0 全仓中证红利（高估减仓）
中间带（`RATIO_LOW < R < RATIO_HIGH`）**保持原权重不动**，避免在带内来回打脸。
"""
import numpy as np
import pandas as pd

import rule


def signal_weights(r, low=None, high=None, w_low=None, w_high=None, init_side=None,
                   rank=None, rank_low=None, rank_high=None):
    """比值序列 R -> 目标权重序列 w[t]（w[t] 由 R[t-1] 决定）。

    rank 非空时改走**滚动分位**口径（无前视对照版）：rank[t-1] ≤ rank_low → w_low，
    rank[t-1] ≥ rank_high → w_high，其余保持。绝对阈值与分位口径互斥，只取一个。
    """
    low = rule.RATIO_LOW if low is None else low
    high = rule.RATIO_HIGH if high is None else high
    w_low = rule.W_AT_LOW if w_low is None else w_low
    w_high = rule.W_AT_HIGH if w_high is None else w_high
    init_side = rule.INIT_SIDE if init_side is None else init_side
    rank_low = rule.RANK_LOW if rank_low is None else rank_low
    rank_high = rule.RANK_HIGH if rank_high is None else rank_high
    use_rank = rank is not None

    cur = w_low if init_side == "growth" else w_high
    rv = r.values.astype(float)
    qv = rank.values.astype(float) if use_rank else None
    out = np.empty(len(r))
    for t in range(len(r)):
        if t > 0:
            if use_rank:
                q = qv[t - 1]
                if not np.isnan(q):
                    if q <= rank_low:
                        cur = w_low
                    elif q >= rank_high:
                        cur = w_high
            else:
                rp = rv[t - 1]
                if not np.isnan(rp):
                    if rp <= low:
                        cur = w_low
                    elif rp >= high:
                        cur = w_high
        out[t] = cur
    return pd.Series(out, index=r.index, name="w")


def run(gclose, dclose, w, fee=None):
    """按目标权重序列模拟净值（口径 A：一次性本金，起点 1.0，与 v0 直接可比）。

    返回 dict: eq(净值 Series), w(实际权重 Series), switches(权重变动事件), params
    """
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
    return dict(eq=pd.Series(eq, index=idx),
                w=pd.Series(ww, index=idx),
                switches=switches,
                params=dict(fee=fee))


def describe_switches(switches, r):
    """给切换事件补上决策用 R 值（切换日前一交易日收盘），并按方向标注动作。"""
    out = []
    for s in switches:
        dt = pd.Timestamp(s["date"])
        prev = r.index[r.index < dt]
        val = float(r.loc[prev[-1]]) if len(prev) else float("nan")
        out.append(dict(s,
                        R_prev=None if np.isnan(val) else round(val, 4),
                        action="加仓成长" if s["to_w"] > s["from_w"] else "减仓成长"))
    return out


def metrics(eq):
    """净值 -> 年化 / 最大回撤 / 波动 / 夏普 / Calmar / 总收益。"""
    s = eq / eq.iloc[0]
    ret = float(s.iloc[-1] - 1.0)
    y = max((s.index[-1] - s.index[0]).days / 365.0, 1e-9)
    ann = (1 + ret) ** (1 / y) - 1 if ret > -1 else -1.0
    dd = float((s / s.cummax() - 1).min())
    r = s.pct_change().dropna()
    vol = float(r.std() * np.sqrt(252))
    shp = float(r.mean() / r.std() * np.sqrt(252)) if r.std() > 0 else 0.0
    calmar = ann / abs(dd) if dd else float("nan")
    return dict(ann=ann, dd=dd, vol=vol, shp=shp, ret=ret, years=y, calmar=calmar)
