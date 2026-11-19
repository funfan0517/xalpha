# -*- coding: utf-8 -*-
"""V6 · 双利差锚引擎：久期轴（期限利差）× 品种轴（信用利差），两轴正交。

久期轴  score = carry + slope ∈[−2,+2] → 长端 003376 占债腿 0/17.5/35/52.5/70%
品种轴  Δcs<−5bp（信用收窄）→ 底仓 000032；Δcs>+5bp（走阔）→ 底仓 011983；中间维持
调仓   月末最后交易日算、T+1 承担；滞后带 1.5pp
成本   Σ|Δw| × 单边 10bp（含品种切换：换底仓 = 卖出 (1−w) + 买入 (1−w)，Σ|Δw| = 2(1−w)）
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
    return s.rolling(f"{int(years * 365)}D", min_periods=min_obs).apply(
        lambda w: float((w <= w[-1]).mean() * 100.0), raw=True)


def build_signal(curve, index=None):
    """-> DataFrame(carry, slope, credit, score, long_w, base_code, s10_1, ds_bp, cs, dcs_bp)。

    credit 列是**品种票**：+1 = 切信用债、−1 = 切利率债、0 = 维持上次（由 base_code 落实）。
    """
    idx = pd.DatetimeIndex(index if index is not None else curve.index)
    c = CFG["carry"]
    sl = CFG["slope"]
    cr = CFG["credit"]

    def _col(name):
        return curve[name].reindex(idx).ffill() if name in curve else pd.Series(np.nan, index=idx)

    y10, y1 = _col(c["long"]), _col(c["short"])
    spread = y10 - y1
    pct = rolling_pct(spread, c["years"], c["min_obs"])
    carry = pd.Series(0, index=idx, dtype=int)
    carry[pct >= c["hi"] * 100] = 1
    carry[pct <= c["lo"] * 100] = -1
    carry[pct.isna()] = 0

    ds_bp = (spread - spread.shift(int(sl["window"]))) * 100.0
    slope = pd.Series(0, index=idx, dtype=int)
    slope[ds_bp <= -sl["deadband_bp"]] = 1        # 利差收窄（牛平）→ 拉久期
    slope[ds_bp >= sl["deadband_bp"]] = -1        # 利差走阔（熊陡）→ 缩久期
    slope[ds_bp.isna()] = 0

    score = carry + slope
    m = CFG["long_by_score"]
    long_w = score.map(lambda x: m.get(int(x), m[0])).astype(float)

    cs = _col(cr["spread_leg"]) - _col(cr["base_leg"])
    dcs_bp = (cs - cs.shift(int(cr["window"]))) * 100.0
    credit = pd.Series(0, index=idx, dtype=int)
    credit[dcs_bp <= -cr["deadband_bp"]] = 1      # 信用收窄 → 信用债
    credit[dcs_bp >= cr["deadband_bp"]] = -1      # 信用走阔 → 利率债
    credit[dcs_bp.isna()] = 0
    base = pd.Series(np.nan, index=idx, dtype=object)
    base[credit == 1] = CFG["credit_leg"]
    base[credit == -1] = CFG["rate_leg"]
    base = base.ffill().fillna(CFG["rate_leg"])

    return pd.DataFrame(dict(carry=carry, slope=slope, credit=credit, score=score,
                             long_w=long_w, base_code=base, s10_1=spread * 100.0,
                             pct=pct, ds_bp=ds_bp, cs=cs * 100.0, dcs_bp=dcs_bp), index=idx)


def build_weights(sig, index):
    """月末调仓 -> (长端权重, 底仓代码)，其余日沿用；T+1 在 backtest 里 shift。"""
    reb = rule.is_rebalance(index)
    w = pd.Series(np.nan, index=index, dtype=float)
    b = pd.Series(np.nan, index=index, dtype=object)
    where = reb.values & sig["long_w"].notna().reindex(index).fillna(False).values
    w.loc[where] = sig["long_w"].reindex(index).loc[where]
    b.loc[where] = sig["base_code"].reindex(index).loc[where]
    return (w.ffill().fillna(CFG["long_by_score"][0]),
            b.ffill().fillna(CFG["rate_leg"]))


def apply_hysteresis(w, thr=None):
    thr = (CFG["hysteresis_pp"] / 100.0) if thr is None else thr
    cur = float(CFG["long_by_score"][0])
    out = []
    for x in w.values:
        if x == x and abs(float(x) - cur) >= thr:
            cur = float(x)
        out.append(cur)
    return pd.Series(out, index=w.index, dtype=float)


def evaluate(navs, curve, asof=None):
    long_c, rate_c, credit_c = CFG["long_leg"], CFG["rate_leg"], CFG["credit_leg"]
    if long_c not in navs or rate_c not in navs or credit_c not in navs:
        raise KeyError(f"缺腿净值 {long_c}/{rate_c}/{credit_c}")
    idx = navs[long_c].index.union(navs[rate_c].index).union(navs[credit_c].index).sort_values()
    sig = build_signal(curve, idx)
    w_raw, b_raw = build_weights(sig, idx)
    w = apply_hysteresis(w_raw)

    row = sig.iloc[-1]
    cur = float(w.iloc[-1])
    target = float(row["long_w"])
    is_reb = bool(rule.is_rebalance(idx).iloc[-1])
    moved = is_reb and abs(target - cur) * 100 >= CFG["hysteresis_pp"]
    eff = target if moved else cur
    pending = None if (not is_reb and abs(target - cur) * 100 < CFG["hysteresis_pp"]) else target
    bp = CFG["sleeve_bond_pp"]
    base_now = str(b_raw.iloc[-1])

    held = {long_c: eff, base_now: 1.0 - eff}
    rows = []
    for c in (long_c, rate_c, credit_c):
        rows.append(dict(code=c, name=rule.NAMES[c], short=rule.NAMES[c],
                         theme="长端利率" if c == long_c else "底仓",
                         role=("长端(10Y)" if c == long_c else
                               ("底仓·利率" if c == rate_c else "底仓·信用")),
                         nav=round(float(navs[c].dropna().iloc[-1]), 4),
                         r=None, pp=round(held.get(c, 0.0) * bp, 2),
                         act="hold" if held.get(c, 0.0) > 0 else "none",
                         label=f"{held.get(c, 0.0) * bp:.1f}pp",
                         note=("长端占比 %.1f%%" % (eff * 100)) if c == long_c else
                              ("当前底仓" if c == base_now else "未持有")))
    for c, frac in CFG["cash"].items():
        rows.append(dict(code=c, name=rule.NAMES[c], short=rule.NAMES[c], theme="现金腿",
                         role="现金腿", nav=None, r=None, pp=round(frac * CFG["sleeve_cash_pp"], 2),
                         act="hold", label=f"{frac * CFG['sleeve_cash_pp']:.1f}pp",
                         note="与 v0/v1 一致"))

    def _f(x, nd=2):
        return None if x is None or pd.isna(x) else round(float(x), nd)

    return dict(
        as_of=asof or str(idx[-1].date()),
        carry=dict(spread=_f(row["s10_1"] / 100.0, 4), pct=_f(row["pct"], 1),
                   vote=int(row["carry"]),
                   rule="10Y-1Y 期限利差近3年分位 ≥66.7%→+1 / ≤33.3%→-1 / 中间0"),
        slope=dict(ds_bp=_f(row["ds_bp"], 1), vote=int(row["slope"]),
                   rule=f"10Y-1Y 的 {CFG['slope']['window']} 日变化 ≤-{CFG['slope']['deadband_bp']:.0f}bp→+1（牛平）/ "
                        f"≥+{CFG['slope']['deadband_bp']:.0f}bp→-1（熊陡）/ 中间0"),
        credit=dict(cs=_f(row["cs"]), dcs_bp=_f(row["dcs_bp"], 1), vote=int(row["credit"]),
                    leg=base_now, leg_name=rule.NAMES[base_now],
                    rule=f"AAA3Y-国债3Y 的 {CFG['credit']['window']} 日变化 ≤-{CFG['credit']['deadband_bp']:.0f}bp→信用债 000032 / "
                         f"≥+{CFG['credit']['deadband_bp']:.0f}bp→利率债 011983 / 中间维持"),
        signal=dict(score=int(row["score"]), long_w=round(target * 100, 2),
                    cur=round(cur * 100, 2), eff=round(eff * 100, 2),
                    moved=bool(moved), is_rebalance=is_reb,
                    act="hold" if not moved else ("buy" if eff > cur else "sell"),
                    pending=None if pending is None else round(pending * 100, 2),
                    next_rebalance="今天（月末最后一个交易日）" if is_reb else "下月最后一个交易日"),
        bond_pp=bp,
        cash_pp={c: round(v * CFG["sleeve_cash_pp"], 2) for c, v in CFG["cash"].items()},
        held={k: round(v * bp, 2) for k, v in held.items()},
        rows=rows,
    )


def backtest(navs, curve, start=None, fee_side=None, dur_only=False, credit_only=False):
    long_c, rate_c, credit_c = CFG["long_leg"], CFG["rate_leg"], CFG["credit_leg"]
    df = pd.concat({long_c: navs[long_c], rate_c: navs[rate_c], credit_c: navs[credit_c]},
                   axis=1, join="inner").dropna()
    idx = df.index
    sig = build_signal(curve, idx)
    w, base = build_weights(sig, idx)
    if credit_only:
        w = pd.Series(CFG["long_by_score"][2], index=idx)      # 长端固定 70%，只做品种轴
    if dur_only:
        base = pd.Series(CFG["rate_leg"], index=idx)           # 底仓固定利率，只做久期轴
    w = apply_hysteresis(w).shift(1).fillna(CFG["long_by_score"][0]).reindex(idx)
    base = base.shift(1).ffill().fillna(CFG["rate_leg"]).reindex(idx)

    rets = df.pct_change().dropna()
    base_ret = pd.Series([rets.at[d, base.at[d]] for d in rets.index], index=rets.index)
    gross = w.reindex(rets.index) * rets[long_c] + (1 - w.reindex(rets.index)) * base_ret
    switch = (base != base.shift(1)).astype(float)
    turn = (w.reindex(rets.index).diff().abs().fillna(0.0)
            + 2 * (1 - w.reindex(rets.index)) * switch.reindex(rets.index).fillna(0.0))
    fee = CFG["fee_side"] if fee_side is None else fee_side
    net = gross - turn * fee
    if start:
        net = net[net.index >= pd.Timestamp(start)]
    return dict(ret=net, switches=int((turn.reindex(net.index) > 1e-9).sum()),
                turn=float(turn.reindex(net.index).sum()),
                w=w.reindex(net.index), base=base.reindex(net.index)), sig


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
