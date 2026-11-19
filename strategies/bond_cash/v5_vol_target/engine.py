# -*- coding: utf-8 -*-
"""V5 · 波动率目标引擎：不预测方向，只按风险预算缩放长端敞口。

w_raw = cap × min(1, target/σ)，σ = max(σ21, σ63)（年化）；σ > guard_mult×target → 0
调仓：月末或每 n 个交易日；T+1 承担；滞后带 1.5pp。
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


def realized_vol(nav, window, min_obs=None):
    """年化已实现波动（对数收益）。"""
    r = np.log(nav.astype(float)).diff()
    mo = min_obs or max(10, int(window) // 2)
    return r.rolling(int(window), min_periods=mo).std() * math.sqrt(252)


def build_signal(nav, index):
    """-> DataFrame(sigma21, sigma63, sigma, w_raw, guard)。"""
    v = CFG["vol"]
    idx = pd.DatetimeIndex(index)
    base = nav.reindex(nav.index.union(idx)).ffill()
    s21 = realized_vol(base, v["fast"]).reindex(idx)
    s63 = realized_vol(base, v["slow"]).reindex(idx)
    sigma = pd.concat([s21, s63], axis=1).max(axis=1)
    w = (v["cap_w"] * np.minimum(1.0, v["target_annual"] / sigma)).clip(v["floor_w"], v["cap_w"])
    guard = sigma > v["guard_mult"] * v["target_annual"]
    w[guard] = v["floor_w"]
    w[sigma.isna()] = CFG["vol"]["cap_w"] * 0.5      # 预热期按半仓，不给默认满仓
    return pd.DataFrame(dict(sigma21=s21, sigma63=s63, sigma=sigma, w_raw=w.fillna(0.35),
                             guard=guard), index=idx)


def build_weights(sig, index, mode=None, n=None):
    reb = rule.is_rebalance(index, mode, n)
    t = pd.Series(np.nan, index=index, dtype=float)
    where = reb.values & sig["w_raw"].notna().reindex(index).fillna(False).values
    t.loc[where] = sig["w_raw"].reindex(index).loc[where]
    return t.ffill().fillna(0.35)


def apply_hysteresis(w, thr=None):
    thr = (CFG["hysteresis_pp"] / 100.0) if thr is None else thr
    cur = 0.35
    out = []
    for x in w.values:
        if x == x and abs(float(x) - cur) >= thr:
            cur = float(x)
        out.append(cur)
    return pd.Series(out, index=w.index, dtype=float)


def evaluate(navs, asof=None):
    long_c, base_c = CFG["long_leg"], CFG["base_leg"]
    if long_c not in navs or base_c not in navs:
        raise KeyError(f"缺腿净值 {long_c}/{base_c}")
    idx = navs[long_c].index.union(navs[base_c].index).sort_values()
    sig = build_signal(navs[long_c], idx)
    w = apply_hysteresis(build_weights(sig, idx))

    row = sig.iloc[-1]
    cur = float(w.iloc[-1])
    target = float(row["w_raw"])
    is_reb = bool(rule.is_rebalance(idx).iloc[-1])
    moved = is_reb and abs(target - cur) * 100 >= CFG["hysteresis_pp"]
    eff = target if moved else cur
    pending = None if (not is_reb and abs(target - cur) * 100 < CFG["hysteresis_pp"]) else target
    bp = CFG["sleeve_bond_pp"]

    rows = []
    held = {long_c: eff, base_c: 1.0 - eff}
    for c in (long_c, base_c):
        rows.append(dict(code=c, name=rule.NAMES[c], short=rule.NAMES[c],
                         theme="长端" if c == long_c else "底仓",
                         role="长端(10Y)" if c == long_c else "底仓(3-5Y)",
                         nav=round(float(navs[c].dropna().iloc[-1]), 4),
                         r=None, pp=round(held[c] * bp, 2),
                         act="hold" if held[c] > 0 else "none",
                         label=f"{held[c] * bp:.1f}pp",
                         note=f"长端占比 {eff * 100:.1f}%"))
    for c, frac in CFG["cash"].items():
        rows.append(dict(code=c, name=rule.NAMES[c], short=rule.NAMES[c], theme="现金腿",
                         role="现金腿", nav=None, r=None, pp=round(frac * CFG["sleeve_cash_pp"], 2),
                         act="hold", label=f"{frac * CFG['sleeve_cash_pp']:.1f}pp",
                         note="与 v0/v1 一致"))

    def _f(x, nd=4):
        return None if x is None or pd.isna(x) else round(float(x), nd)

    return dict(
        as_of=asof or str(idx[-1].date()),
        vol=dict(sigma21=_f(row["sigma21"], 5), sigma63=_f(row["sigma63"], 5),
                 sigma=_f(row["sigma"], 5), target=CFG["vol"]["target_annual"],
                 ratio=_f(row["sigma"] / CFG["vol"]["target_annual"], 2),
                 guard=bool(row["guard"]),
                 rule="σ = max(σ21, σ63) 年化；w = 70% × min(1, 1.50%/σ)；σ > 3.00% → 清零"),
        signal=dict(w_raw=round(target * 100, 2), cur=round(cur * 100, 2),
                    eff=round(eff * 100, 2), moved=bool(moved), is_rebalance=is_reb,
                    act="hold" if not moved else ("buy" if eff > cur else "sell"),
                    pending=None if pending is None else round(pending * 100, 2),
                    next_rebalance="今天（月末最后一个交易日）" if is_reb else "下月最后一个交易日"),
        bond_pp=bp,
        cash_pp={c: round(v * CFG["sleeve_cash_pp"], 2) for c, v in CFG["cash"].items()},
        held={k: round(v * bp, 2) for k, v in held.items()},
        rows=rows,
    )


def backtest(navs, start=None, fee_side=None, mode=None, n=None, hysteresis_pp=None):
    long_c, base_c = CFG["long_leg"], CFG["base_leg"]
    df = pd.concat({long_c: navs[long_c], base_c: navs[base_c]}, axis=1, join="inner").dropna()
    idx = df.index
    sig = build_signal(navs[long_c], idx)
    w = build_weights(sig, idx, mode=mode, n=n)
    if hysteresis_pp is not None:
        w = apply_hysteresis(w, hysteresis_pp / 100.0)
    else:
        w = apply_hysteresis(w)
    w = w.shift(1).fillna(0.35).reindex(idx)
    rets = df.pct_change().dropna()
    gross = w.reindex(rets.index) * rets[long_c] + (1 - w.reindex(rets.index)) * rets[base_c]
    turn = w.reindex(rets.index).diff().abs().fillna(0.0)
    fee = CFG["fee_side"] if fee_side is None else fee_side
    net = gross - turn * 2 * fee
    if start:
        net = net[net.index >= pd.Timestamp(start)]
    return dict(ret=net, switches=int((turn.reindex(net.index) > 1e-9).sum()),
                turn=float(turn.reindex(net.index).sum()),
                w=w.reindex(net.index), sig=sig.reindex(net.index))


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
