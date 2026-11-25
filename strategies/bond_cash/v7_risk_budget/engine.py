# -*- coding: utf-8 -*-
"""V7 · 风险预算引擎：ERC（等风险贡献）+ 硬约束 + 风险贡献分解。

三类风险资产（债腿内部）：利率 / 信用 / 转债。现金腿 10pp 单独定额，不参与。
月频：月末最后交易日用**过去 60 个交易日**的收缩协方差解 ERC，T+1 承担。
"""
import math
import os
import sys

import numpy as np
import pandas as pd

_HERE = os.path.dirname(os.path.abspath(__file__))
if _HERE not in sys.path:
    sys.path.insert(0, _HERE)

import rule  # noqa: E402

CFG = rule.CFG
BLOCKS = rule.BLOCKS


# ---------------------------------------------------------------- 收益与协方差
def block_returns(navs, index=None):
    """-> DataFrame(index=日期, columns=[rate, credit, conv]) 的**债腿内部**日收益。

    利率腿 = 0.35×003376 + 0.65×001512（固定权重，日频再平衡近似；2026-11-25 中端由 011983 政金 3-5 换成 001512 国债 3-5，比例仍为 35/65）。
    """
    need = [CFG["long_leg"], CFG["base_leg"], CFG["credit_leg"], CFG["conv_leg"]]
    px = pd.concat({c: navs[c] for c in need}, axis=1, join="inner").dropna()
    if index is not None:
        px = px.reindex(pd.DatetimeIndex(index).union(px.index)).ffill().reindex(index).dropna()
    r = px.pct_change().dropna()
    m = CFG["rate_mix"]
    return pd.DataFrame({
        "rate": r[CFG["long_leg"]] * m[CFG["long_leg"]] + r[CFG["base_leg"]] * m[CFG["base_leg"]],
        "credit": r[CFG["credit_leg"]],
        "conv": r[CFG["conv_leg"]],
    }, index=r.index)


def cov_est(rets, shrink=None):
    """年化协方差 + 向对角收缩（δ 默认 0.30）。"""
    d = CFG["erc"]["shrink"] if shrink is None else shrink
    cov = rets.cov().values * 252.0
    diag = np.diag(np.diag(cov))
    return (1 - d) * cov + d * diag


def erc_weights(cov, target=None, iters=None, damp=None, tol=None):
    """等风险贡献解（不动点迭代，无外部依赖）。

    约束在 ``project`` 里另行施加 —— 这里解的是**无约束** ERC，再投影到可行域，
    投影后风险贡献不再精确相等（这是约束的代价，报告里会显示实际贡献）。
    """
    e = CFG["erc"]
    iters = e["iters"] if iters is None else iters
    damp = e["damp"] if damp is None else damp
    tol = e["tol"] if tol is None else tol
    n = cov.shape[0]
    tgt = np.full(n, 1.0 / n) if target is None else np.asarray(target, float)
    tgt = tgt / tgt.sum()
    var = np.maximum(np.diag(cov), 1e-12)
    w = (1.0 / np.sqrt(var))
    w = w / w.sum()
    rc = np.full(n, 1.0 / n)
    for _ in range(int(iters)):
        sw = cov @ w
        port = float(w @ sw)
        rc = w * sw / max(port, 1e-18)
        w = w * np.power(np.maximum(tgt, 1e-9) / np.maximum(rc, 1e-9), damp)
        w = np.maximum(w, 1e-8)
        w = w / w.sum()
        if np.max(np.abs(rc - tgt)) < tol:
            break
    return w


def project(w, caps=None, floors=None):
    """把权重投影到 [floors, caps] ∩ {Σw=1}（按剩余容量比例分配缺口）。"""
    lo = np.array([CFG["floors"][b] for b in BLOCKS], float) if floors is None else np.asarray(floors, float)
    hi = np.array([CFG["caps"][b] for b in BLOCKS], float) if caps is None else np.asarray(caps, float)
    w = np.clip(np.asarray(w, float), lo, hi)
    for _ in range(200):
        w = np.clip(w, lo, hi)
        gap = 1.0 - w.sum()
        if abs(gap) < 1e-12:
            break
        room = np.clip((hi - w) if gap > 0 else (w - lo), 0.0, None)
        tot = room.sum()
        if tot < 1e-12:
            break
        w = w + gap * room / tot
    w = np.clip(w, lo, hi)
    gap = 1.0 - w.sum()
    if abs(gap) > 1e-9:                     # 兜底：全部余量压给有空间的那一格
        room = np.clip((hi - w) if gap > 0 else (w - lo), 0.0, None)
        i = int(np.argmax(room))
        w[i] += gap
    return np.clip(w, lo, hi)


def risk_contrib(w, cov):
    """-> (rc_share, port_vol)。rc_share 之和为 1，>1/n 表示「这一类在偷风险」。"""
    w = np.asarray(w, float)
    sw = cov @ w
    port = float(w @ sw)
    if port <= 0:
        return np.full(len(w), 1.0 / len(w)), 0.0
    return w * sw / port, math.sqrt(port)


# ---------------------------------------------------------------- 权重序列
def _solve(rets_hist, constrained=True):
    cov = cov_est(rets_hist)
    w = erc_weights(cov)
    if constrained:
        w = project(w)
    return np.asarray(w, float), cov


def build_weights(rets, constrained=True, hysteresis_pp=None, min_obs=None):
    """月末解 ERC -> 下一交易日生效。-> DataFrame(index=交易日, columns=BLOCKS)。"""
    e = CFG["erc"]
    min_obs = e["min_obs"] if min_obs is None else min_obs
    thr = (CFG["hysteresis_pp"] if hysteresis_pp is None else hysteresis_pp) / 100.0
    idx = rets.index
    reb = rule.is_rebalance(idx).values
    rows = pd.DataFrame(np.nan, index=idx, columns=BLOCKS, dtype=float)
    cur = None
    for i, d in enumerate(idx):
        if not reb[i] or i + 1 < min_obs:
            continue
        w, _ = _solve(rets.iloc[max(0, i + 1 - int(e["window"])):i + 1], constrained)
        if cur is not None and float(np.max(np.abs(w - cur))) < thr:
            w = cur
        cur = w
        rows.loc[d] = w
    if cur is None:                       # 历史不够：给一个保守起点（v0 口径）
        rows.iloc[0] = [0.85, 0.15, 0.0]
    return rows.ffill().bfill()


# ---------------------------------------------------------------- 当日决策
def evaluate(navs, asof=None):
    need = [CFG["long_leg"], CFG["base_leg"], CFG["credit_leg"], CFG["conv_leg"]]
    for c in need:
        if c not in navs:
            raise KeyError(f"缺腿净值 {c}")
    idx = None
    for c in need:
        idx = navs[c].index if idx is None else idx.union(navs[c].index)
    idx = idx.sort_values()
    rets = block_returns(navs, idx)
    w_series = build_weights(rets)
    held = w_series.iloc[-1].values.astype(float)
    win = int(CFG["erc"]["window"])
    target, cov = _solve(rets.iloc[-win:])

    rc_held, vol = risk_contrib(held, cov)
    rc_tgt, _ = risk_contrib(target, cov)
    bp = CFG["sleeve_bond_pp"]
    is_reb = bool(rule.is_rebalance(rets.index).iloc[-1])
    moved = is_reb and float(np.max(np.abs(target - held))) * bp >= CFG["hysteresis_pp"]

    rows = []
    for i, b in enumerate(BLOCKS):
        rows.append(dict(code=rule.BLOCK_LEG[b], name=rule.BLOCK_LEGNAME[b], short=rule.BLOCK_NAME[b],
                         theme="门派", role=rule.BLOCK_NAME[b], nav=None, r=None,
                         pp=round(held[i] * bp, 2), act="hold" if held[i] > 0 else "none",
                         label=f"{held[i] * bp:.1f}pp",
                         note=f"目标 {target[i] * 100:.1f}% · 风险贡献 {rc_held[i] * 100:.1f}%"))
    for c, frac in CFG["cash"].items():
        rows.append(dict(code=c, name=rule.NAMES[c], short=rule.NAMES[c], theme="现金腿",
                         role="现金腿", nav=None, r=None,
                         pp=round(frac * CFG["sleeve_cash_pp"], 2), act="hold",
                         label=f"{frac * CFG['sleeve_cash_pp']:.1f}pp", note="固定 10pp，不进 ERC"))

    return dict(
        as_of=asof or str(rets.index[-1].date()),
        vol=round(vol * 100, 2),
        held={b: round(float(held[i]) * bp, 2) for i, b in enumerate(BLOCKS)},
        target={b: round(float(target[i]) * bp, 2) for i, b in enumerate(BLOCKS)},
        rc_held={b: round(float(rc_held[i]) * 100, 1) for i, b in enumerate(BLOCKS)},
        rc_target={b: round(float(rc_tgt[i]) * 100, 1) for i, b in enumerate(BLOCKS)},
        weights_held={b: round(float(held[i]), 4) for i, b in enumerate(BLOCKS)},
        weights_target={b: round(float(target[i]), 4) for i, b in enumerate(BLOCKS)},
        cash_pp=CFG["sleeve_cash_pp"],
        bond_pp=bp,
        signal=dict(is_rebalance=is_reb, moved=bool(moved),
                    # 「买/卖」按**风险资产（信用+转债）净变化**定方向：利率端是资金池，
                    # 信用/转债加仓 = 买，减仓 = 卖（与 v1 的 buy/sell/hold 词表一致）
                    act=("hold" if not moved else
                         ("buy" if float(target[1] + target[2] - held[1] - held[2]) > 0 else "sell")),
                    diff_pp={b: round(float(target[i] - held[i]) * bp, 2) for i, b in enumerate(BLOCKS)},
                    next_rebalance="今天（月末最后一个交易日）" if is_reb else "下月最后一个交易日"),
        rows=rows,
    )


# ---------------------------------------------------------------- 回测
def backtest(navs, start=None, fee_side=None, constrained=True, weights=None):
    """weights 给定时用固定权重（dict 或 list），用于基准对照。"""
    rets = block_returns(navs)
    idx = rets.index
    if weights is None:
        w = build_weights(rets, constrained=constrained)
    else:
        if isinstance(weights, dict):
            wv = np.array([weights.get(b, 0.0) for b in BLOCKS], float)
        else:
            wv = np.asarray(weights, float)
        w = pd.DataFrame(np.tile(wv, (len(idx), 1)), index=idx, columns=BLOCKS)
        w = w / w.sum(axis=1).values[:, None]
    w = w.shift(1).ffill().bfill()
    gross = (w.values * rets.values).sum(axis=1)
    turn = np.abs(np.diff(w.values, axis=0)).sum(axis=1)
    fee = CFG["fee_side"] if fee_side is None else fee_side
    net = pd.Series(gross - np.concatenate([[0.0], turn * fee]), index=idx)
    if start:
        net = net[net.index >= pd.Timestamp(start)]
        w = w[w.index >= pd.Timestamp(start)]
    turn_s = pd.Series(np.concatenate([[0.0], turn]), index=idx)
    return dict(ret=net, switches=int((turn_s.reindex(net.index) > 1e-9).sum()),
                turn=float(turn_s.reindex(net.index).sum()), w=w.reindex(net.index))


def metrics(ret, rf=None):
    rf = CFG["rf_annual"] if rf is None else rf
    if len(ret) < 2:
        return dict(years=0.0, cagr=0.0, vol=0.0, sharpe=0.0, mdd=0.0, worst_year=0.0, total=0.0)
    nav = (1 + ret).cumprod()
    years = (ret.index[-1] - ret.index[0]).days / 365.25
    cagr = float(nav.iloc[-1] ** (1 / years) - 1) if years > 0 else 0.0
    vol = float(ret.std() * math.sqrt(252))
    mdd = float((nav / nav.cummax() - 1).min())
    yearly = ret.groupby(ret.index.year).apply(lambda x: (1 + x).prod() - 1)
    return dict(years=round(years, 2), cagr=round(cagr * 100, 2), vol=round(vol * 100, 2),
                sharpe=round((cagr - rf) / vol, 2) if vol > 1e-9 else 0.0,
                mdd=round(mdd * 100, 2),
                worst_year=round(float(yearly.min()) * 100, 2) if len(yearly) else 0.0,
                total=round(float(nav.iloc[-1] - 1) * 100, 2))
