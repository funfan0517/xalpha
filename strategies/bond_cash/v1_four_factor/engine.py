# -*- coding: utf-8 -*-
"""V1 · 四因子引擎（温度 → 因子 → 分档 → 情景 → 长端目标占比），每日与回测共用。

时序：T 日收盘用 T 日及以前的数据算分，T+1 起承担仓位（``shift(1)``）。

分位口径
  估值/资金/信用用**时间窗**滚动分位（``rolling("1095D")``，3 个自然年），
  因为 SHIBOR 与曲线都有缺日；动能用**观测窗**（250 个交易日）取 [低, 高] 区间位置。
  分位样本 < 250 个观测时给 NaN（不硬造数），由覆盖度降级处理。
"""
import math
import os
import sys

import numpy as np
import pandas as pd

_HERE = os.path.dirname(os.path.abspath(__file__))
for _p in (os.path.dirname(_HERE), _HERE):
    while _p in sys.path:
        sys.path.remove(_p)
    sys.path.insert(0, _p)

import rule  # noqa: E402

CFG = rule.CFG
MIN_OBS = 250            # 分位最少观测数
_PCT_WINDOW = f"{CFG['pct_years'] * 365}D"


# ---------------------------------------------------------------- 温度
def rolling_pct(s, window=_PCT_WINDOW, min_obs=MIN_OBS):
    """滚动分位（0~100）：窗口内 ≤ 当日值的比例。"""
    if s is None or len(s) == 0:
        return pd.Series(dtype="float64")
    return s.rolling(window, min_periods=min_obs).apply(
        lambda w: float((w <= w[-1]).mean() * 100.0), raw=True)


def build_temps(navs, y10, shibor, curve, index=None):
    """-> DataFrame(valuation / funding / momentum / credit / composite / coverage / degrade)。"""
    bench = navs[CFG["bench"]]
    idx = pd.DatetimeIndex(index if index is not None else bench.index)
    out = pd.DataFrame(index=idx)

    y = y10.reindex(idx).ffill()
    out["valuation"] = 100.0 - rolling_pct(y)

    sh = shibor.reindex(idx).ffill() if len(shibor) else pd.Series(np.nan, index=idx)
    out["funding"] = 100.0 - rolling_pct(sh)

    c = bench.reindex(idx).ffill()
    lo = c.rolling(CFG["mom_window"]).min()
    hi = c.rolling(CFG["mom_window"]).max()
    out["momentum"] = (c - lo) / (hi - lo) * 100.0

    if curve is not None and len(curve) and {"aaa_3y", "cgb_1y"} <= set(curve.columns):
        spread = (curve["aaa_3y"] - curve["cgb_1y"]).reindex(idx).ffill()
        out["credit"] = rolling_pct(spread)
    else:
        out["credit"] = np.nan

    w = pd.Series(CFG["temp_weights"], dtype="float64")
    avail = out[list(w.index)].notna()
    num = (out[list(w.index)].fillna(0.0) * w).sum(axis=1)
    den = (avail * w).sum(axis=1)
    out["composite"] = (num / den.replace(0, np.nan)).astype("float64")
    out["coverage"] = avail.sum(axis=1).astype("int64")
    out["degrade"] = out["coverage"] < CFG["temp_min_coverage"]
    return out


# ---------------------------------------------------------------- 因子
def build_factors(temps, bench):
    """-> DataFrame(valuation / trend / funding / credit / score / conflict / degrade)。"""
    f = pd.DataFrame(index=temps.index)
    v_lo, v_hi = CFG["factor_cuts"]["valuation"]
    u_lo, u_hi = CFG["factor_cuts"]["funding"]
    c_lo, c_hi = CFG["factor_cuts"]["credit"]

    f["valuation"] = np.select([temps["valuation"] <= v_lo, temps["valuation"] >= v_hi],
                               [1, -1], default=0)
    f["funding"] = np.select([temps["funding"] >= u_hi, temps["funding"] <= u_lo],
                             [1, -1], default=0)
    f["credit"] = np.select([temps["credit"] >= c_hi, temps["credit"] <= c_lo],
                            [1, -1], default=0)

    # R-05 趋势因子
    tr = CFG["trend"]
    c = bench.reindex(temps.index).ffill()
    ma_f = c.rolling(tr["ma_fast"]).mean()
    ma_s = c.rolling(tr["ma_slow"]).mean()
    hi20 = c.rolling(tr["h20"]).max()
    lo20 = c.rolling(tr["h20"]).min()
    up = (c > ma_f) & (ma_f > ma_s) & (c >= (1.0 - tr["near"]) * hi20)
    dn = (c < ma_f) & (ma_f < ma_s) & (c <= (1.0 + tr["near"]) * lo20)
    f["trend"] = np.select([up, dn], [1, -1], default=0)

    cols = ["valuation", "trend", "funding", "credit"]
    f["score"] = f[cols].sum(axis=1)
    f["conflict"] = (f[cols] > 0).any(axis=1) & (f[cols] < 0).any(axis=1)

    # R-11：趋势偏多但估值极贵 / 趋势偏空但资金极松 → 降级为震荡
    f["degrade"] = (((f["trend"] == 1) & (f["valuation"] == -1)) |
                    ((f["trend"] == -1) & (f["funding"] == 1)))
    f["degrade"] = f["degrade"] | temps["degrade"].reindex(f.index).fillna(True)
    return f


def build_signal(factors):
    """-> DataFrame(side / conf / target)，含 R-09/R-10/R-18 的逐日结论（未加滞后带）。"""
    s = pd.DataFrame(index=factors.index)
    score = factors["score"].astype(int)
    conflict = factors["conflict"].astype(bool)
    degrade = factors["degrade"].astype(bool)

    s["score"] = score
    s["conflict"] = conflict
    s["degrade"] = degrade
    s["side"] = np.where(conflict | degrade, "震荡观望",
                         np.where(score >= CFG["side_up"], "偏多",
                                  np.where(score <= CFG["side_down"], "偏空", "震荡观望")))

    conf = pd.Series("low", index=s.index)
    conf[score.abs() == 3] = "medium"
    conf[score.abs() == 4] = "high"
    s["conf"] = np.where(conflict | degrade, "low", conf)

    base, cap = CFG["long_base"], CFG["long_cap"]
    disc = s["conf"].map(CFG["discount"]).astype(float)
    s["target"] = (base + (score / 4.0) * (cap - base) * disc).clip(
        CFG["long_floor"], cap)
    return s


def apply_hysteresis(targets, start=None):
    """R-18：|目标 − 当前| ≥ 1.5pp 才调仓；返回逐日实际权重。"""
    thr = CFG["hysteresis_pp"] / 100.0
    cur = float(CFG["long_base"] if start is None else start)
    out = []
    for t in targets.values:
        if t == t and abs(float(t) - cur) >= thr:
            cur = float(t)
        out.append(cur)
    return pd.Series(out, index=targets.index, dtype="float64")


# ---------------------------------------------------------------- 情景（R-12~R-15）
def scenario(temps, bench, i=-1):
    """按当前估值温度找历史同分位桶，统计未来 ``horizon`` 日上涨频率。"""
    sc = CFG["scenario"]
    t = temps["valuation"].iloc[i]
    if pd.isna(t):
        return dict(ok=False, p_up=50.0, n=0, bucket=None,
                    bull=50.0, flat=25.0, bear=25.0,
                    note="估值温度缺失 → 退化中性 50%")
    buckets = sc["buckets"]
    centers = np.array(buckets, dtype=float)
    center = float(centers[np.argmin(np.abs(centers - float(t)))])

    fwd = bench.reindex(temps.index).shift(-sc["horizon"]) / bench.reindex(temps.index) - 1.0
    mask = (temps["valuation"] >= center - sc["band"]) & (temps["valuation"] <= center + sc["band"])
    sample = fwd[mask].dropna()
    n = int(len(sample))
    if n < sc["min_n"]:
        p = 50.0
        note = f"同桶样本 n={n} < {sc['min_n']} → 退化中性 50%"
    else:
        p = float((sample > 0).mean() * 100.0)
        note = f"同桶样本 n={n}（估值温度 {center:.0f}±{sc['band']}）"
    flat = (100.0 - p) * 0.5
    return dict(ok=n >= sc["min_n"], p_up=round(p, 1), n=n, bucket=center,
                bull=round(p, 1), flat=round(flat, 1), bear=round(flat, 1), note=note)


def scenario_history(temps, bench, months=1):
    """最近 ``months`` 个自然月逐日的 point-in-time 情景概率。

    每个交易日都把 temps/bench **截到当日**再调 :func:`scenario`，所以桶样本与
    未来 20 日频率都只用当时已知的数据，不含前视；最后一行与当日 ``scenario()`` 完全一致。
    ``fwd20`` 是该日之后 20 个交易日基准累计净值的已实现涨跌（未到期为 None），仅供事后核对。
    """
    if not len(temps):
        return []
    end = temps.index[-1]
    idx = temps.index[temps.index >= end - pd.DateOffset(months=months)]
    bx = bench.reindex(temps.index)
    fwd = bx.shift(-CFG["scenario"]["horizon"]) / bx - 1.0

    rows = []
    for d in idx:
        pos = int(temps.index.get_loc(d))
        t_sub = temps.iloc[:pos + 1]
        b_sub = bx.iloc[:pos + 1]
        sc = scenario(t_sub, b_sub)
        rv = fwd.iloc[pos]
        val = t_sub["valuation"].iloc[-1]
        rows.append(dict(
            date=str(d.date()),
            valuation=None if pd.isna(val) else round(float(val), 1),
            bucket=sc["bucket"],
            n=sc["n"],
            p_up=sc["p_up"],
            bull=sc["bull"], flat=sc["flat"], bear=sc["bear"],
            fwd20=None if pd.isna(rv) else round(float(rv) * 100.0, 2),
            ok=sc["ok"],
        ))
    return rows


# ---------------------------------------------------------------- 当日决策
def evaluate(p, cur_w=None, today=None):
    """-> 当日决策 dict。``cur_w`` 为当前长端占比（缺省按基准 35% 并标注）。"""
    navs = p["navs"]
    bench = navs.get(CFG["bench"])
    if bench is None:
        raise KeyError(f"缺基准 {CFG['bench']}（{rule.NAMES[CFG['bench']]}）的净值，无法出信号")

    temps = build_temps(navs, p["y10"], p["shibor"], p["curve"])
    factors = build_factors(temps, bench)
    sig = build_signal(factors)

    i = -1
    row_t = temps.iloc[i]
    row_f = factors.iloc[i]
    row_s = sig.iloc[i]
    score = int(row_s["score"])
    conf = str(row_s["conf"])
    conflict = bool(row_s["conflict"])
    degrade = bool(row_s["degrade"])
    side = rule.side(score, conflict) if not degrade else "震荡观望"

    # 降级时 conf 已被压到 low，折扣 0.5 本身就是「打折执行」，不再额外改目标
    target = rule.target_long(score, conf)
    frozen = rule.in_freeze(today or bench.index[-1])
    cur = float(CFG["long_base"] if cur_w is None else cur_w)
    eff_target = min(target, cur) if frozen else target

    moved = abs(eff_target - cur) * 100.0 >= CFG["hysteresis_pp"]
    act = "hold"
    if moved:
        act = "buy" if eff_target > cur else "sell"
    w_long = eff_target if moved else cur

    bp = CFG["sleeve_bond_pp"]
    long_pp = round(w_long * bp, 2)
    base_pp = round((1.0 - w_long) * bp, 2)
    cash_pp = {k: round(v * CFG["sleeve_cash_pp"], 2) for k, v in CFG["cash"].items()}

    sc = scenario(temps, bench)
    as_of = str(bench.index[-1].date())
    return dict(
        as_of=as_of,
        temp=dict(valuation=_num(row_t["valuation"]), funding=_num(row_t["funding"]),
                  momentum=_num(row_t["momentum"]), credit=_num(row_t["credit"]),
                  composite=_num(row_t["composite"]), band=rule.temp_band(row_t["composite"]),
                  coverage=int(row_t["coverage"]), degrade=bool(row_t["degrade"])),
        factors={k: int(row_f[k]) for k in ("valuation", "trend", "funding", "credit")},
        signal=dict(score=score, side=side, conf=conf, conflict=conflict, degrade=degrade,
                    freeze=bool(frozen)),
        scenario=sc,
        position=dict(cur=round(cur * 100, 2), target=round(target * 100, 2),
                      eff_target=round(eff_target * 100, 2), w_long=round(w_long * 100, 2),
                      moved=bool(moved), act=act, delta_pp=round((w_long - cur) * 100, 2),
                      long_pp=long_pp, base_pp=base_pp, cash_pp=cash_pp,
                      long_code=CFG["long_leg"], base_code=CFG["base_leg"]),
        note=_note(row_t, row_f, row_s, sc),
    )


def _num(v):
    return None if v is None or (isinstance(v, float) and math.isnan(v)) else round(float(v), 1)


_CONF_CN = {"high": "高", "medium": "中", "low": "低"}


def _note(t, f, s, sc):
    bits = []
    if t["degrade"]:
        bits.append(f"温度覆盖 {int(t['coverage'])}/{len(CFG['temp_weights'])} 项 → 降级")
    if s["conflict"]:
        bits.append("四因子互斥 → 强制震荡观望")
    if s["degrade"] and not t["degrade"]:
        bits.append("R-11 降级（趋势与估值/资金背离）")
    if sc.get("n", 0) < CFG["scenario"]["min_n"]:
        bits.append(sc["note"])
    return "；".join(bits) if bits else "无降级"


# ---------------------------------------------------------------- 回测
def backtest_weights(sig_series, index, start=None):
    """逐日长端权重（含滞后带）。"""
    tgt = sig_series["target"].reindex(index)
    w = apply_hysteresis(tgt, start=CFG["long_base"])
    return w


def leg_returns(navs, long_code, base_code):
    """-> DataFrame(long / base)，按两腿共同交易日。"""
    df = pd.concat({"long": navs[long_code], "base": navs[base_code]},
                   axis=1, join="inner").dropna()
    return df.pct_change().dropna()


def run(daily_w, rets, fee_side=None):
    """按逐日权重记账（T 日权重 → T+1 承担收益），返回净收益 Series 与换手次数。"""
    fee = CFG["fee_side"] if fee_side is None else fee_side
    w = daily_w.reindex(rets.index).ffill()
    w_eff = w.shift(1).fillna(CFG["long_base"])
    gross = w_eff * rets["long"] + (1.0 - w_eff) * rets["base"]
    turn = w_eff.diff().abs().fillna(0.0)
    net = gross - turn * 2.0 * fee
    return net, int((turn > 1e-9).sum())


def metrics(ret, rf=None):
    rf = CFG["rf_annual"] if rf is None else rf
    if len(ret) < 2:
        return dict(years=0.0, cagr=0.0, vol=0.0, sharpe=0.0, mdd=0.0, worst_year=0.0,
                    total=0.0, n=0)
    nav = (1.0 + ret).cumprod()
    years = (ret.index[-1] - ret.index[0]).days / 365.25
    cagr = float(nav.iloc[-1] ** (1.0 / years) - 1.0) if years > 0 else 0.0
    vol = float(ret.std() * math.sqrt(252))
    mdd = float((nav / nav.cummax() - 1.0).min())
    yearly = ret.groupby(ret.index.year).apply(lambda x: (1.0 + x).prod() - 1.0)
    return dict(years=round(years, 2), cagr=round(cagr * 100, 2), vol=round(vol * 100, 2),
                sharpe=round((cagr - rf) / vol, 2) if vol > 1e-9 else 0.0,
                mdd=round(mdd * 100, 2),
                worst_year=round(float(yearly.min()) * 100, 2) if len(yearly) else 0.0,
                total=round(float(nav.iloc[-1] - 1.0) * 100, 2), n=int(len(ret)))
