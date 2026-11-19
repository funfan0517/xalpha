# -*- coding: utf-8 -*-
"""V4 · 三票引擎：Carry（期限利差分位）+ Trend（利率均线）+ Hedge（股票动量极端分位）。

三票各 ∈{−1,0,+1}，score ∈[−3,+3] → 长端占比 0 / 12 / 23 / 35 / 47 / 58 / 70%，
月末最后交易日调仓、T+1 承担；滞后带 1.5pp；其余（额度、现金腿、成本口径）与 v0/v3 一致。

与 v3 的关系：carry / trend 两票**逐行同源**（同样的曲线、同样的窗口、同样的阈值），
所以同一窗口下「V4 三票 vs V3 双票」的差值就是 Hedge 票的贡献 —— 这是本变种唯一要回答的问题。
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


def rolling_pct(s, years=3, min_obs=250):
    """滚动分位（0~100）：窗口内 ≤ 当前值的比例。"""
    return s.rolling(f"{int(years * 365)}D", min_periods=min_obs).apply(
        lambda w: float((w <= w[-1]).mean() * 100.0), raw=True)


def build_signal(curve, stock=None, index=None, use_hedge=True):
    """-> DataFrame(carry, trend, hedge, score, long_w, spread, pct, stock_ret, stock_pct)。"""
    idx = pd.DatetimeIndex(index if index is not None else curve.index)
    c = CFG["carry"]
    y10 = curve[c["long"]].reindex(idx).ffill() if c["long"] in curve else pd.Series(np.nan, index=idx)
    y1 = curve[c["short"]].reindex(idx).ffill() if c["short"] in curve else pd.Series(np.nan, index=idx)
    spread = y10 - y1
    pct = rolling_pct(spread, c["years"], c["min_obs"])
    carry = pd.Series(0, index=idx, dtype=int)
    carry[pct >= c["hi"] * 100] = 1
    carry[pct <= c["lo"] * 100] = -1
    carry[pct.isna()] = 0

    votes = []
    for s, l in CFG["trend"]["pairs"]:
        votes.append((y10.rolling(s).mean() < y10.rolling(l).mean()).astype(int).replace({0: -1}))
    tvote = sum(votes) if votes else pd.Series(0, index=idx)
    trend = pd.Series(0, index=idx, dtype=int)
    trend[tvote >= CFG["trend"]["vote_up"]] = 1
    trend[tvote <= CFG["trend"]["vote_down"]] = -1

    # Hedge 票：股票 252 日累计收益的 5 年滚动分位，极端高位减久期、极端低位加久期
    h = CFG["hedge"]
    stock = stock if stock is not None else pd.Series(dtype="float64")
    if len(stock):
        px = stock.reindex(idx.union(stock.index)).ffill().reindex(idx)
        ret = px / px.shift(int(h["window"])) - 1.0
        spct = rolling_pct(ret.dropna(), h["years"], h["min_obs"]).reindex(idx)
    else:
        ret = pd.Series(np.nan, index=idx)
        spct = pd.Series(np.nan, index=idx)
    hedge = pd.Series(0, index=idx, dtype=int)
    if use_hedge:
        hedge[spct >= h["hi"]] = h["on_stock_hot"]
        hedge[spct <= h["lo"]] = h["on_stock_cold"]
    hedge[spct.isna()] = 0

    score = carry + trend + hedge
    m = CFG["long_by_score"]
    long_w = score.map(lambda x: m.get(int(x), m[0])).astype(float)
    return pd.DataFrame(dict(carry=carry, trend=trend, hedge=hedge, score=score, long_w=long_w,
                             spread=spread, pct=pct, stock_ret=ret, stock_pct=spct), index=idx)


def build_weights(sig, index):
    """月末出信号 → 下一交易日生效（T+1），其余日沿用。"""
    reb = rule.is_rebalance(index)
    t = pd.Series(np.nan, index=index, dtype=float)
    where = reb & sig["long_w"].notna().reindex(index).fillna(False)
    t.loc[where] = sig["long_w"].reindex(index).loc[where]
    return t.ffill().fillna(CFG["long_by_score"][0])


def apply_hysteresis(w, thr=None):
    thr = (CFG["hysteresis_pp"] / 100.0) if thr is None else thr
    cur = float(CFG["long_by_score"][0])
    out = []
    for x in w.values:
        if x == x and abs(float(x) - cur) >= thr:
            cur = float(x)
        out.append(cur)
    return pd.Series(out, index=w.index, dtype=float)


def evaluate(navs, curve, stock=None, asof=None):
    """-> 当日决策 dict。"""
    long_c, base_c = CFG["long_leg"], CFG["base_leg"]
    if long_c not in navs or base_c not in navs:
        raise KeyError(f"缺腿净值 {long_c}/{base_c}")
    idx = navs[long_c].index.union(navs[base_c].index).sort_values()
    sig = build_signal(curve, stock, idx)
    w = apply_hysteresis(build_weights(sig, idx))

    row = sig.iloc[-1]
    cur = float(w.iloc[-1])
    target = float(row["long_w"])
    is_reb = bool(rule.is_rebalance(idx).iloc[-1])
    moved = is_reb and abs(target - cur) * 100 >= CFG["hysteresis_pp"]
    eff = target if moved else cur
    pending = None if (not is_reb and abs(target - cur) * 100 < CFG["hysteresis_pp"]) else target
    bp = CFG["sleeve_bond_pp"]
    next_reb = "今天（月末最后一个交易日）" if is_reb else "下月最后一个交易日"

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
                         note="与 v0/v1/v3 一致"))

    def _f(x, nd=4):
        return None if x is None or pd.isna(x) else round(float(x), nd)

    return dict(
        as_of=asof or str(idx[-1].date()),
        carry=dict(spread=_f(row["spread"]), pct=_f(row["pct"], 1), vote=int(row["carry"]),
                   rule="10Y-1Y 期限利差近3年分位 ≥66.7%→+1 / ≤33.3%→-1 / 中间0"),
        trend=dict(vote=int(row["trend"]),
                   rule="10Y 收益率 MA5<MA20 且 MA20<MA60 → +1；全反向 → -1；否则 0"),
        hedge=dict(stock_ret=_f(row["stock_ret"]), stock_pct=_f(row["stock_pct"], 1),
                   vote=int(row["hedge"]),
                   rule="沪深300 近252日收益的5年分位 ≥90%→-1（股票过热减久期）/ ≤10%→+1（避险加久期）"),
        signal=dict(score=int(row["score"]), long_w=round(target * 100, 2),
                    cur=round(cur * 100, 2), eff=round(eff * 100, 2),
                    moved=bool(moved), is_rebalance=is_reb,
                    act="hold" if not moved else ("buy" if eff > cur else "sell"),
                    pending=None if pending is None else round(pending * 100, 2),
                    next_rebalance=next_reb),
        bond_pp=bp,
        cash_pp={c: round(v * CFG["sleeve_cash_pp"], 2) for c, v in CFG["cash"].items()},
        held={k: round(v * bp, 2) for k, v in held.items()},
        rows=rows,
    )


def backtest(navs, curve, stock=None, start=None, fee_side=None, use_hedge=True):
    long_c, base_c = CFG["long_leg"], CFG["base_leg"]
    df = pd.concat({long_c: navs[long_c], base_c: navs[base_c]},
                   axis=1, join="inner").dropna()
    idx = df.index
    sig = build_signal(curve, stock, idx, use_hedge=use_hedge)
    w = apply_hysteresis(build_weights(sig, idx)).shift(1).fillna(CFG["long_by_score"][0])
    rets = df.pct_change().dropna()
    gross = w.reindex(rets.index) * rets[long_c] + (1 - w.reindex(rets.index)) * rets[base_c]
    turn = w.reindex(rets.index).diff().abs().fillna(0.0)
    fee = CFG["fee_side"] if fee_side is None else fee_side
    net = gross - turn * 2 * fee
    if start:
        net = net[net.index >= pd.Timestamp(start)]
    return dict(ret=net, switches=int((turn.reindex(net.index) > 1e-9).sum()),
                turn=float(turn.reindex(net.index).sum())), sig


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
