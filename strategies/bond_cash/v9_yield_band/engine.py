# -*- coding: utf-8 -*-
"""V9 · 信号状态机 + 绩效引擎（债腿 30pp 内部口径）。

- 主口径（2026-11-27 起）：10Y 绝对阈值 3.0% / 2.0%，三区直接映射（HIGH>3.0 / MID 2.0~3.0 / LOW<2.0）。
- 对照口径：带记忆状态机（HIGH 破 2.0 才走、LOW 上 3.0 才回）、10Y 滚动分位。
- 成交：T 日收盘算状态 → T+1 承担新权重；月末再平衡 + 档位突破次日执行。
"""
import math

import numpy as np
import pandas as pd

import rule

CFG = rule.CFG
STATES = ("HIGH", "MID", "LOW")


def rolling_percentile(s, years=None, min_obs=None):
    """历史分位（%）：当期值在过去 N 年样本中的排位，越高表示利率越处高位。"""
    years = CFG["percentile"]["years"] if years is None else years
    min_obs = CFG["percentile"]["min_obs"] if min_obs is None else min_obs
    return s.rolling(f"{int(years * 365)}D", min_periods=min_obs).apply(
        lambda w: float((w <= w[-1]).mean() * 100.0), raw=True)


def _step(cur, x, lo, hi, hysteresis=True):
    if not hysteresis or cur == "MID":
        if x > hi:
            return "HIGH"
        if x < lo:
            return "LOW"
        return "MID"
    if cur == "HIGH":
        return "LOW" if x < lo else "HIGH"
    if cur == "LOW":
        return "HIGH" if x > hi else "LOW"
    return "MID"


def state_series(x, lo, hi, hysteresis=None, start=None):
    """单条序列 -> 状态 Series。NaN 日维持上一状态。"""
    hysteresis = CFG["hysteresis"] if hysteresis is None else hysteresis
    start = CFG["start_state"] if start is None else start
    cur, out = start, []
    for v in x.values:
        if pd.notna(v):
            cur = _step(cur, float(v), lo, hi, hysteresis=hysteresis)
        out.append(cur)
    return pd.Series(out, index=x.index, dtype=object)


def signal_frame(y, mode="absolute", lo=None, hi=None, years=None, hysteresis=None):
    """-> DataFrame(yield, pct, state)。

    mode="absolute"：阈值取 lo/hi（默认 2.0/3.0）；分位列只作诊断。
    mode="percentile"：阈值取分位 lo/hi（默认 30/70）；状态由滚动分位驱动。
    """
    pct = rolling_percentile(y, years=years)
    if mode == "percentile":
        pcfg = CFG["percentile"]
        lo = pcfg["low"] if lo is None else lo
        hi = pcfg["high"] if hi is None else hi
        driver = pct
    else:
        tcfg = CFG["threshold"]
        lo = tcfg["low"] if lo is None else lo
        hi = tcfg["high"] if hi is None else hi
        driver = y
    st = state_series(driver, lo, hi, hysteresis=hysteresis)
    return pd.DataFrame({"yield": y, "pct": pct, "state": st})


def target_matrix(states, weights=None):
    """状态序列 -> 每日目标权重 DataFrame（列 = 各腿）。"""
    wmap = CFG["weights"] if weights is None else weights
    cols = [c for s in STATES for c in wmap[s]]
    cols = list(dict.fromkeys(cols))
    return pd.DataFrame({s: [wmap[s].get(c, 0.0) for c in cols] for s in STATES},
                        index=cols).T.reindex(states.values).fillna(0.0).set_axis(states.index)


def realized_vol(navs, target, lookback=None):
    """基础目标权重的已实现年化波动（只用 t 及以前数据，权重 shift(1) 避免未来函数）。"""
    lookback = CFG.get("vol_target", {}).get("lookback", 20) if lookback is None else lookback
    cols = [c for c in target.columns if c in navs]
    px = pd.concat({c: navs[c] for c in cols}, axis=1, join="inner").dropna()
    rets = px.pct_change().dropna()
    w = target.reindex(index=rets.index, columns=cols).ffill().shift(1).fillna(0.0)
    base = (rets[cols] * w[cols]).sum(axis=1)
    return base.rolling(int(lookback)).std() * np.sqrt(252)


def vol_scale(navs, target, cfg=None):
    """月末计算、下月执行的波动缩放系数：clip(target_vol / 已实现波动, min, max)。

    `min_change`（默认 0）是缩放变动阈值：新系数与当前系数差不到该值就不调，
    用于压换手；档位切换不受此限制（由 backtest 的目标变化单独触发）。
    """
    cfg = CFG["vol_target"] if cfg is None else cfg
    vol = realized_vol(navs, target, lookback=cfg["lookback"])
    raw = (cfg["target"] / vol).clip(cfg["min_scale"], cfg["max_scale"])
    reb = rule.is_rebalance(vol.index, freq=cfg.get("rebalance", "month_end"))
    min_change = float(cfg.get("min_change", 0.0))
    if min_change <= 0:
        return raw.where(reb).ffill().fillna(1.0), vol
    held = pd.Series(1.0, index=vol.index)
    cur = 1.0
    for d in vol.index:
        if bool(reb.get(d, False)) and pd.notna(raw.get(d)):
            v = float(raw[d])
            if abs(v - cur) >= min_change:
                cur = v
        held[d] = cur
    return held, vol


def backtest(navs, states, start=None, fee_side=None, rebalance=None,
             breach_next_day=None, weights=None, vol_target=None):
    """按状态序列回测 -> dict(ret, w, switches, turnover, trades, scale)。

    `w` 是**实际持有**权重（收益日 d 用的是 d-1 收盘决策出的权重）。
    `vol_target=None` 用 CFG["vol_target"]；传 False 关闭波动缩放（旧口径对照）；
    传 dict 可覆盖目标波动等参数。
    """
    fee = CFG["fee_side"] if fee_side is None else fee_side
    rebalance = CFG["rebalance"] if rebalance is None else rebalance
    breach_next_day = CFG["breach_next_day"] if breach_next_day is None else breach_next_day
    cols = [c for c in CFG["universe"] if c in navs]
    px = pd.concat({c: navs[c] for c in cols}, axis=1, join="inner").dropna()
    rets = px.pct_change().dropna()
    if rets.empty:
        raise ValueError("净值交集为空")

    idx = rets.index
    st = states.reindex(idx).ffill().bfill()
    tgt = target_matrix(st, weights=weights).reindex(index=idx, columns=cols).fillna(0.0)
    vt = CFG.get("vol_target", {}) if vol_target is None else vol_target
    if vt and vt.get("enabled", False):
        scale_m, _ = vol_scale(navs, target_matrix(st, weights=weights), cfg=vt)
        scale_m = scale_m.reindex(idx).ffill().fillna(1.0)
        tgt = tgt.mul(scale_m, axis=0)
        tgt[CFG["short_leg"]] = tgt[CFG["short_leg"]] + (1.0 - tgt.sum(axis=1))
    else:
        scale_m = pd.Series(1.0, index=idx)
    reb = rule.is_rebalance(idx, freq=rebalance)
    changed = st.ne(st.shift(1)).fillna(False).values

    cur = tgt.iloc[0].to_numpy(dtype=float).copy()
    gross, cost, holds, traded = [], [], [], []
    thr = CFG["hysteresis_pp"] / 100.0
    for i, d in enumerate(idx):
        gross.append(float(rets.iloc[i].to_numpy(dtype=float) @ cur))
        holds.append(cur.copy())
        new = tgt.iloc[i].to_numpy(dtype=float)
        delta = float(np.abs(new - cur).sum())
        fire = (bool(reb.iloc[i]) or (breach_next_day and bool(changed[i]))) and i > 0
        if fire and np.max(np.abs(new - cur)) >= thr:
            traded.append((str(d.date()), st.iloc[i], delta))
            cost.append(delta)
            cur = new.copy()          # 收盘换仓，下一交易日承担
        else:
            cost.append(0.0)

    gross_s = pd.Series(gross, index=idx)
    cost_s = pd.Series(cost, index=idx).shift(1).fillna(0.0)   # T 日换仓 → T+1 付成本
    net = gross_s - cost_s * fee
    wdf = pd.DataFrame(holds, index=idx, columns=cols)
    if start is not None:
        keep = net.index >= pd.Timestamp(start)
        net, wdf = net[keep], wdf[keep]
    return dict(ret=net, w=wdf, switches=len(traded), turnover=float(sum(cost_s)),
                scale=scale_m.reindex(net.index),
                trades=[t for t in traded if t[0] >= str(net.index[0].date())])


def fixed_backtest(navs, weights, start=None):
    """固定权重对照（买入并持有 / 基准代理）：按目标权重持有，不计再平衡成本。"""
    cols = [c for c in weights if c in navs]
    px = pd.concat({c: navs[c] for c in cols}, axis=1, join="inner").dropna()
    rets = px.pct_change().dropna()
    w = np.array([weights[c] for c in cols], dtype=float)
    w = w / w.sum()
    net = pd.Series(rets.to_numpy(dtype=float) @ w, index=rets.index)
    if start is not None:
        net = net[net.index >= pd.Timestamp(start)]
    return net


def blended_backtest(parts, start=None):
    """跨资产固定比例对照（如股债 60/40）：parts = {name: (return Series, weight)}。"""
    rets = pd.concat({k: v[0] for k, v in parts.items()}, axis=1, join="inner").dropna()
    w = np.array([v[1] for v in parts.values()], dtype=float)
    w = w / w.sum()
    net = pd.Series(rets.to_numpy(dtype=float) @ w, index=rets.index)
    if start is not None:
        net = net[net.index >= pd.Timestamp(start)]
    return net


def metrics(ret, rf=None):
    """年化 / 波动 / 夏普 / 回撤 / 卡玛 / 日胜率 / 月胜率 / 最长水下天数 / 最差年。"""
    rf = CFG["rf_annual"] if rf is None else rf
    if len(ret) < 2:
        return dict(years=0.0, cagr=0.0, vol=0.0, sharpe=0.0, mdd=0.0, calmar=0.0,
                    daily_win=0.0, monthly_win=0.0, dd_days=0, worst_year=0.0, total=0.0)
    nav = (1 + ret).cumprod()
    years = (ret.index[-1] - ret.index[0]).days / 365.25
    cagr = float(nav.iloc[-1] ** (1 / years) - 1) if years > 0 else 0.0
    vol = float(ret.std() * math.sqrt(252))
    mdd = float((nav / nav.cummax() - 1).min())
    monthly = ret.groupby([ret.index.year, ret.index.month]).apply(lambda x: (1 + x).prod() - 1)
    yearly = ret.groupby(ret.index.year).apply(lambda x: (1 + x).prod() - 1)
    under = nav < nav.cummax()
    grp = under.ne(under.shift()).cumsum()
    lens = under.groupby(grp).sum()
    dd_days = int(lens[lens > 0].max()) if (lens > 0).any() else 0
    return dict(
        years=round(years, 2),
        cagr=round(cagr * 100, 2),
        vol=round(vol * 100, 2),
        sharpe=round((cagr - rf) / vol, 2) if vol > 1e-9 else 0.0,
        mdd=round(mdd * 100, 2),
        calmar=round(cagr / abs(mdd), 2) if mdd < -1e-9 else 0.0,
        daily_win=round(float((ret > 0).mean()) * 100, 1),
        monthly_win=round(float((monthly > 0).mean()) * 100, 1) if len(monthly) else 0.0,
        dd_days=dd_days,
        worst_year=round(float(yearly.min()) * 100, 2) if len(yearly) else 0.0,
        total=round(float(nav.iloc[-1] - 1) * 100, 2),
    )
