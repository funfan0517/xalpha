# -*- coding: utf-8 -*-
"""V8 · 久期阶梯风险预算引擎：档位结构预算 + 同档择一 + 进攻档波动反馈。

与 v7 的分工：v7 解的是「风险贡献该多均」（ERC），v8 解的是「钱该怎么按久期阶梯摆」
（权重空间）。理由见 ``rule.py`` 顶部：ERC 会把组合波动压到 1% 以下，而 rf=1.4% 是硬扣项，
低波动组合的夏普上不去；v8 用结构预算把长端从 70% 降到 20%，把比重给 3-5 底仓与信用。
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


# ---------------------------------------------------------------- 收益
def leg_returns(navs, legs=None, index=None):
    """-> DataFrame(index=日期, columns=可用腿) 的日收益（场外累计净值，inner join）。"""
    legs = [c for c in (rule.CFG["universe"] if legs is None else legs) if c in navs]
    if not legs:
        raise KeyError("没有可用腿")
    px = pd.concat({c: navs[c] for c in legs}, axis=1, join="inner").dropna()
    if index is not None:
        idx = pd.DatetimeIndex(index)
        px = px.reindex(px.index.union(idx)).ffill().reindex(idx).dropna()
    if len(px) < 3:
        raise ValueError("净值序列过短，无法算收益")
    return px.pct_change().dropna()


def cov_est(rets_hist):
    """年化协方差（用于风险贡献读数；v8 不用它解权重，只用来做体检）。"""
    return rets_hist.cov().values * 252.0


def risk_contrib(w, cov):
    """-> (rc_share, port_vol)；rc_share 之和为 1。"""
    w = np.asarray(w, float)
    sw = cov @ w
    port = float(w @ sw)
    if port <= 0:
        return np.full(len(w), 1.0 / len(w)), 0.0
    return w * sw / port, math.sqrt(port)


# ---------------------------------------------------------------- ③ 长端趋势闸门
def trend_gate_state(nav, mom_win=None, min_hold=None, index=None):
    """长端日频趋势闸门 -> Series(int)：1 = 持有长端，0 = 离场（资金去 ``to`` 腿）。

    规则（``CFG["trend_gate"]``，2026-11-20 装）：
      * 长端 ``mom_win`` 日动量**转负** → 立即离场（风险信号不等）；
      * 动量**转正**且**离场已满 ``min_hold`` 个交易日**才回场（躲场外 <7 天 1.5% 赎回费）；
      * 动量尚未成形的前 ``mom_win`` 个交易日沿用结构预算（=1），不提前下车。
    动量用**累计净值**比（``totvalue``），不是净价 —— 分红日不会假摔。
    """
    g = CFG.get("trend_gate") or {}
    win = int(g.get("mom_win", 30) if mom_win is None else mom_win)
    hold = int(g.get("min_hold", 7) if min_hold is None else min_hold)
    s = pd.Series(nav, dtype=float).dropna().sort_index()
    if index is not None:
        idx = pd.DatetimeIndex(index)
        s = s.reindex(s.index.union(idx)).ffill().reindex(idx)
    vals = np.ones(len(s), dtype=int)
    if len(s) <= win + 1:
        return pd.Series(vals, index=s.index, dtype=int)
    mom = (s / s.shift(win) - 1.0).values
    cur, entered, exited = 1, 0, 0
    for i in range(len(s)):
        if i <= win or not np.isfinite(mom[i]):
            vals[i] = cur
            continue
        if cur == 1:
            if mom[i] < 0 and (i - entered) >= hold:
                cur, exited = 0, i                   # 转负且已满最短持有：离场
        elif mom[i] > 0 and (i - exited) >= hold:
            cur, entered = 1, i                      # 转正且冷却期满：回场
        vals[i] = cur
    return pd.Series(vals, index=s.index, dtype=int)


def redeem_rate(days, ladder=None):
    """按持有**自然日**返回赎回费率（小数）。

    场外 A 类的赎回费是持有期阶梯，不是固定摩擦成本：``003376`` 为
    <7 日 1.50% / 7~30 日 0.50% / 30~365 日 0.10% / 365~730 日 0.05% / ≥730 日 0。
    日频闸门会造出「只持有几天」的份额，按 10bp 单边统一近似会**系统性低估**成本。
    """
    g = CFG.get("trend_gate") or {}
    lad = ladder if ladder is not None else (g.get("redeem_ladder") or [])
    for lim, pct in lad:
        if days < int(lim):
            return float(pct) / 100.0
    return 0.0


def hold_days(index, pos):
    """-> 每行的「连续持有自然日数」（``pos`` 为 False 处为 0）。"""
    out = np.zeros(len(index), dtype=int)
    entry = None
    for i, (d, p) in enumerate(zip(index, pos)):
        if not p:
            entry = None
            continue
        if entry is None:
            entry = pd.Timestamp(d)
        out[i] = (pd.Timestamp(d) - entry).days + 1
    return out


def apply_trend_gate(w, navs, mom_win=None, min_hold=None):
    """把闸门叠加到权重矩阵（列需含长端 leg）-> (W, state)。

    离场时把长端的权重**整块**移到 ``to``（货基），买回并持有：不预测方向、不加杠杆，
    只是「长端趋势坏了就先躲进现金」，权重和恒为 1。
    """
    g = CFG.get("trend_gate") or {}
    if not g.get("on") or g.get("leg") not in w.columns or g.get("leg") not in navs:
        return w, None
    leg, to = g["leg"], g["to"]
    st = trend_gate_state(navs[leg], mom_win=mom_win, min_hold=min_hold, index=w.index)
    W = w.copy()
    if to not in W.columns:
        W[to] = 0.0
    moved = W[leg] * (1 - st)
    W[leg] = W[leg] * st
    W[to] = W[to] + moved
    return W, st


def add_leg(navs, rets, code):
    """把一只额外腿（通常是货基）按 ``rets.index`` 对齐后并入收益矩阵。

    **不用 inner join**：那样会缩短回测窗口，让带闸门的方案与基准落在不同样本上。
    缺净值日 ffill（货基当天视为 0 收益）。
    """
    s = pd.Series(navs[code], dtype=float).dropna().sort_index()
    r = s.pct_change()
    r = r.reindex(r.index.union(rets.index)).ffill().reindex(rets.index).fillna(0.0)
    out = rets.copy()
    out[code] = r.values
    return out


# ---------------------------------------------------------------- 解权重
def _sr(hist, code, win):
    s = hist[code].iloc[-int(win):]
    if len(s) < 20:
        return -9.0
    sd = float(s.std()) * math.sqrt(252)
    if sd <= 1e-12:
        return -9.0
    return (float(s.mean()) * 252 - CFG["rf_annual"]) / sd


def _cap_project(w, cap, allow=None):
    """把权重投影到 [0, cap] ∩ {Σw=1}，缺口只补给 ``allow`` 为真的腿。

    ⚠ 必须带 ``allow``：结构预算里被**主动排除**的腿（同档没选中的那只）权重是 0，
    但它「容量」很大；若按全容量补缺口，它会被重新填回来，等于偷偷破坏「同档择一」。
    """
    hi = np.full(len(w), cap, float)
    w = np.clip(np.asarray(w, float), 0.0, hi)
    allow = np.ones(len(w), bool) if allow is None else np.asarray(allow, bool)
    for _ in range(200):
        w = np.clip(w, 0.0, hi)
        gap = 1.0 - w.sum()
        if abs(gap) < 1e-12:
            break
        room = np.clip(hi - w, 0.0, None) * allow
        tot = room.sum()
        if tot < 1e-12:
            break
        w = w + gap * room / tot
    w = np.clip(w, 0.0, hi)
    gap = 1.0 - w.sum()
    if abs(gap) > 1e-9:
        cand = np.clip(hi - w, 0.0, None) * allow
        if cand.max() > 1e-12:
            w[int(np.argmax(cand))] += gap
    return np.clip(w, 0.0, hi)


def pick_mid(hist, cols, pick=None):
    """中端档同档择一：近 ``sr_win`` 日夏普高者胜；领先不足 ``deadband`` 维持原持仓。"""
    cfg = CFG["pick"]
    mid = [c for c in CFG["blocks"][cfg["block"]]["legs"] if c in cols]
    if not mid:
        return None
    if len(mid) == 1:
        return mid[0]
    sr = {c: _sr(hist, c, cfg["sr_win"]) for c in mid}
    best = max(mid, key=lambda c: sr[c])
    if pick is None or pick not in mid:
        return best
    if best != pick and sr[best] - sr[pick] > cfg["deadband"]:
        return best
    return pick


def _credit_vol_ratio(hist):
    """信用腿的「当期波动 / 中枢波动」最大值（>1.5 视为信用出事）。"""
    legs = [c for c in CFG["credit_freeze"]["legs"] if c in hist.columns]
    if not legs:
        return 0.0
    out = []
    for c in legs:
        v_s = float(hist[c].iloc[-int(CFG["vol_win"]):].std())
        v_l = float(hist[c].iloc[-int(CFG["vol_long_win"]):].std())
        if v_l > 1e-12:
            out.append(v_s / v_l)
    return max(out) if out else 0.0


def target_weights(hist, cols, pick=None, vol_feedback=True, prev=None, freeze_left=0):
    """-> (Series 权重, pick, 诊断 dict)。

    hist: 截至当日的日收益；cols: 可用腿；prev: 上期持仓（用于信用腿单向冻结）；
    freeze_left: 剩余冻结周期数（由调用方在调仓间传递）。
    """
    pick = pick_mid(hist, cols, pick)
    cols = list(cols)

    # ---- 1) 档位预算；缺档时按 degrade 规则移交（不是摊给其他档）
    avail, missing = {}, []
    for k in CFG["blocks_order"]:
        legs = [c for c in CFG["blocks"][k]["legs"] if c in cols]
        if legs:
            avail[k] = legs
        else:
            missing.append(k)
    raw = {k: CFG["blocks"][k]["budget"] for k in avail}
    degraded = []
    if missing:
        move = sum(CFG["blocks"][k]["budget"] for k in missing)
        safe = CFG["degrade"]["safe_leg"]
        if CFG["degrade"]["on"] and safe in cols:
            owner = rule.leg_block(safe)
            raw[owner] = raw.get(owner, 0.0) + move      # 预算移交最短久期腿
            degraded = missing
        else:
            scale = 1.0 / max(1.0 - move, 1e-9)          # 退回按比例补齐
            raw = {k: v * scale for k, v in raw.items()}
            degraded = missing

    w = pd.Series(0.0, index=cols)
    for k, legs in avail.items():
        b = raw[k]
        if k == CFG["pick"]["block"]:
            for c in legs:
                w[c] = b if c == pick else 0.0
        elif "split" in CFG["blocks"][k]:
            split = CFG["blocks"][k]["split"]
            s = sum(split.get(c, 0.0) for c in legs) or 1.0
            for c in legs:
                w[c] = b * split.get(c, 0.0) / s
        else:
            for c in legs:
                w[c] = b / len(legs)

    # ---- 2) 信用腿单向冻结：出事后的 N 个周期只许减不许加
    cf = CFG["credit_freeze"]
    frozen, freeze_next = False, max(0, int(freeze_left) - 1)
    if cf["on"]:
        ratio = _credit_vol_ratio(hist)
        if ratio > cf["vol_ratio"]:
            freeze_next = int(cf["cycles"])
        frozen = freeze_next > 0
        if frozen:
            credit = [c for c in cf["legs"] if c in cols]
            freed = 0.0
            derisk = float(cf.get("derisk", 1.0))
            for c in credit:
                base = float(w.get(c, 0.0))
                keep = base * derisk                       # ① 主动降险
                if prev is not None:                       # ② 只许减不许加
                    keep = min(keep, float(prev.get(c, 0.0)))
                freed += base - keep
                w[c] = keep
            if freed > 1e-12:
                safe = CFG["degrade"]["safe_leg"]
                if safe in cols:
                    w[safe] += freed                      # 腾出的给最短久期腿（降险方向）
                else:
                    host = [c for c in cols if c not in credit and w.get(c, 0.0) > 1e-12]
                    tot = sum(float(w[c]) for c in host) or 1.0
                    for c in host:
                        w[c] += freed * float(w[c]) / tot

    # ---- 3) 进攻档波动反馈
    fb = {}
    if vol_feedback:
        for c, kappa in CFG["vol_feedback"].items():
            if c not in cols or w.get(c, 0.0) <= 0:
                continue
            v_s = float(hist[c].iloc[-int(CFG["vol_win"]):].std())
            v_l = float(hist[c].iloc[-int(CFG["vol_long_win"]):].std())
            if v_s > 1e-12 and v_l > 1e-12:
                mult = (v_l / v_s) ** float(kappa)
                lo, hi = CFG["vol_feedback_clip"]
                mult = float(np.clip(mult, lo, hi))         # 反馈闸：再极端也只动到边界
                w[c] *= mult
                fb[c] = round(mult, 3)

    wv = w.values.astype(float)
    if wv.sum() > 1e-12:                            # 先归一，再上障（缩放不该改变结构占比）
        wv = wv / wv.sum()
    wv = _cap_project(wv, CFG["cap"], allow=wv > 1e-12)
    diag = dict(pick=pick, vol_feedback=fb, degraded=degraded,
                credit_vol_ratio=round(_credit_vol_ratio(hist), 2),
                frozen=frozen, freeze_left=freeze_next,
                sr={c: round(_sr(hist, c, CFG["pick"]["sr_win"]), 2) for c in cols})
    return pd.Series(wv, index=cols), pick, diag


def build_weights(rets, pick_fixed=None, vol_feedback=True, hysteresis_pp=None):
    """月末解一次 -> 下一交易日生效。-> (W DataFrame, picks Series)。"""
    cols = list(rets.columns)
    idx = rets.index
    reb = rule.is_rebalance(idx).values
    thr = (CFG["hysteresis_pp"] if hysteresis_pp is None else hysteresis_pp) / 100.0
    min_obs = int(CFG["pick"]["min_obs"])
    lookback = max(int(CFG["pick"]["sr_win"]), int(CFG["vol_long_win"]))

    rows = pd.DataFrame(np.nan, index=idx, columns=cols, dtype=float)
    picks = pd.Series(index=idx, dtype=object)
    freezes = pd.Series(0, index=idx, dtype=int)
    cur, pick, fl = None, pick_fixed, 0
    for i, d in enumerate(idx):
        if not reb[i] or i + 1 < min_obs:
            continue
        hist = rets.iloc[max(0, i + 1 - lookback):i + 1]
        prev = None if cur is None else pd.Series(cur, index=cols)
        w, pick, diag = target_weights(hist, cols, pick=pick_fixed or pick,
                                       vol_feedback=vol_feedback, prev=prev,
                                       freeze_left=fl)
        fl = int(diag["freeze_left"])
        wv = w.reindex(cols).values
        if cur is not None and float(np.max(np.abs(wv - cur))) < thr:
            wv = cur
        cur = wv
        rows.loc[d] = wv
        picks.loc[d] = pick
        freezes.loc[d] = fl

    if cur is None:                                  # 历史不足：结构预算直接起手
        w0, pick, _ = target_weights(rets, cols, pick=pick_fixed, vol_feedback=False)
        rows.iloc[0] = w0.reindex(cols).values
        picks.iloc[0] = pick
    rows = rows.ffill().bfill()
    picks = picks.ffill()                      # object dtype：只向后填，避免 pandas 降级告警
    freezes = freezes.ffill().fillna(0).astype(int)
    return rows, picks, freezes


# ---------------------------------------------------------------- 当日决策
def evaluate(navs, asof=None, legs=None):
    cols_req = [c for c in (rule.CFG["universe"] if legs is None else legs) if c in navs]
    miss = [c for c in rule.CFG["universe"] if c not in navs]
    rets = leg_returns(navs, cols_req)
    cols = list(rets.columns)
    W, picks, freezes = build_weights(rets)
    held = W.iloc[-1]
    lookback = max(int(CFG["pick"]["sr_win"]), int(CFG["vol_long_win"]))
    target, pick_now, diag = target_weights(rets.iloc[-lookback:], cols,
                                            pick=picks.iloc[-1], prev=held,
                                            freeze_left=int(freezes.iloc[-1]))
    target = target.reindex(cols).fillna(0.0)
    bp = CFG["sleeve_bond_pp"]

    # ---- ③ 长端趋势闸门：离场时长端权重整块移到货基（债腿内部再分配，不动总敞口）
    g = CFG.get("trend_gate") or {}
    gate = None
    rets_all = rets
    if g.get("on") and g.get("leg") in cols and g.get("to") in navs:
        leg, to = g["leg"], g["to"]
        win = int(g["mom_win"])
        st = trend_gate_state(navs[leg], index=rets.index)
        state = int(st.iloc[-1])
        nav_leg = pd.Series(navs[leg], dtype=float).dropna().sort_index()
        mom = (float(nav_leg.iloc[-1] / nav_leg.iloc[-1 - win] - 1.0)
               if len(nav_leg) > win else None)
        held, target = held.copy(), target.copy()
        for f in (held, target):
            mv = float(f[leg]) * (1 - state)
            f[leg] = float(f[leg]) * state
            f[to] = float(f.get(to, 0.0)) + mv
        rets_all = add_leg(navs, rets, to)
        flips = np.where(np.diff(st.values) != 0)[0]
        gate = dict(on=True, leg=leg, to=to, state=state, mom_win=win,
                    mom=round(mom * 100, 2) if mom is not None else None,
                    off_pp=round(float(held[to]) * bp, 2),
                    last_flip=(str(st.index[flips[-1] + 1].date()) if len(flips) else None))

    cols_all = list(rets_all.columns)
    held = held.reindex(cols_all).fillna(0.0)
    target = target.reindex(cols_all).fillna(0.0)
    cov = cov_est(rets_all.iloc[-int(CFG["vol_win"]):])
    rc_held, vol = risk_contrib(held.values, cov)
    rc_tgt, _ = risk_contrib(target.values, cov)
    rc_map = dict(zip(cols_all, rc_held))
    rc_map_t = dict(zip(cols_all, rc_tgt))

    is_reb = bool(rule.is_rebalance(rets.index).iloc[-1])
    diff = (target - held) * bp
    moved = is_reb and float(np.max(np.abs(diff.values))) >= CFG["hysteresis_pp"]

    rows = []
    for c in cols:
        d = float(diff[c])
        # 只有「今天确实是调仓日且越过滞后带」才给 buy/sell；否则一律 hold，
        # 免得把「月度目标的偏离」误读成「今天要动手」。
        act = "hold" if held[c] > 1e-9 else "none"
        if moved:
            if d >= 0.5:
                act = "buy"
            elif d <= -0.5:
                act = "sell"
            if held[c] <= 1e-9 and target[c] <= 1e-9:
                act = "none"
        note = f"目标 {float(target[c]) * 100:.1f}% · 风险贡献 {float(rc_map[c]) * 100:.1f}%"
        if gate and c == gate["leg"]:
            note += (" · ⛔ 趋势闸门关闭（动量转负）" if gate["state"] == 0
                     else f" · ✅ 趋势闸门开启（{gate['mom_win']} 日动量 {gate['mom']:+.2f}%）")
        rows.append(dict(
            code=c, name=rule.NAMES[c], short=rule.SHORT[c], num=rule.NUM[c],
            sect=rule.SECT[c], block=rule.CFG["blocks"][rule.leg_block(c)]["name"],
            duration=rule.DURATION[c],
            weight=round(float(held[c]), 4), target=round(float(target[c]), 4),
            pp=round(float(held[c]) * bp, 2), pp_target=round(float(target[c]) * bp, 2),
            diff_pp=round(d, 2), rc=round(float(rc_map[c]) * 100, 1),
            rc_target=round(float(rc_map_t[c]) * 100, 1),
            act=act, label=f"{float(held[c]) * bp:.1f}pp", note=note,
        ))
    for c, frac in CFG["cash"].items():
        pp = frac * CFG["sleeve_cash_pp"]
        note = "固定 10pp，不进风险预算"
        if gate and c == gate["to"] and gate["off_pp"] > 0:
            pp += gate["off_pp"]
            note = (f"固定 {frac * CFG['sleeve_cash_pp']:.1f}pp + "
                    f"长端闸门退货基 {gate['off_pp']:.1f}pp（长端离场）")
        rows.append(dict(
            code=c, name=rule.NAMES[c], short=rule.NAMES[c], num=0, sect="现金",
            block="现金腿", duration=0.0, weight=None, target=None,
            pp=round(pp, 2), pp_target=round(pp, 2), diff_pp=0.0,
            rc=0.0, rc_target=0.0, act="hold",
            label=f"{pp:.1f}pp", note=note,
        ))

    return dict(
        as_of=asof or str(rets.index[-1].date()),
        bond_pp=bp, cash_pp=CFG["sleeve_cash_pp"],
        vol=round(vol * 100, 2),
        weights_held={c: round(float(held[c]), 4) for c in cols_all},
        weights_target={c: round(float(target[c]), 4) for c in cols_all},
        pp_held={c: round(float(held[c]) * bp, 2) for c in cols_all},
        pp_target={c: round(float(target[c]) * bp, 2) for c in cols_all},
        rc_held={c: round(float(rc_map[c]) * 100, 1) for c in cols_all},
        rc_target={c: round(float(rc_map_t[c]) * 100, 1) for c in cols_all},
        pick=diag["pick"], pick_prev=picks.iloc[-1],
        sr=diag["sr"], vol_feedback=diag["vol_feedback"],
        degraded=diag["degraded"], frozen=diag["frozen"],
        freeze_left=diag["freeze_left"], credit_vol_ratio=diag["credit_vol_ratio"],
        trend_gate=gate, missing=miss,
        signal=dict(is_rebalance=is_reb, moved=bool(moved),
                    act=("hold" if not moved else ("buy" if float(diff.sum()) > 0 else "sell")),
                    diff_pp={c: round(float(diff[c]), 2) for c in cols_all},
                    next_rebalance="今天（月末最后一个交易日）" if is_reb else "下月最后一个交易日"),
        rows=rows,
    )


# ---------------------------------------------------------------- 回测
def backtest(navs, start=None, fee_side=None, weights=None, pick_fixed=None,
             vol_feedback=True, legs=None, trend_gate=None):
    """weights 给定时用固定权重（dict），用于基准/静态对照。

    ``trend_gate``：None = 跟随 ``CFG["trend_gate"]["on"]``；固定权重基准一律不套闸门
    （基准要的是「全程持有」的对照）。
    """
    cols = [c for c in (rule.CFG["universe"] if legs is None else legs) if c in navs]
    rets = leg_returns(navs, cols)
    idx = rets.index
    g = CFG.get("trend_gate") or {}
    use_gate = bool(g.get("on")) if trend_gate is None else bool(trend_gate)
    use_gate = use_gate and weights is None and g.get("leg") in cols and g.get("to") in navs
    if weights is None:
        w, _, _ = build_weights(rets, pick_fixed=pick_fixed, vol_feedback=vol_feedback)
    else:
        wv = pd.Series({c: float(weights.get(c, 0.0)) for c in rets.columns})
        wv = wv / wv.sum()
        w = pd.DataFrame(np.tile(wv.values, (len(idx), 1)), index=idx, columns=rets.columns)
    gate = None
    if use_gate:
        w, gate = apply_trend_gate(w, navs)
        rets = add_leg(navs, rets, g["to"])
    w = w.shift(1).ffill().bfill()
    gross = (w.values * rets.values).sum(axis=1)
    turn = np.abs(np.diff(w.values, axis=0)).sum(axis=1)
    fee = CFG["fee_side"] if fee_side is None else fee_side
    cost = np.concatenate([[0.0], turn * fee])
    gate_fee = None
    # 闸门腿按**真实申赎费**计（持有期阶梯 + 申购费），并从统一 fee_side 里扣掉它的部分，
    # 避免「一笔卖出收两次费」。fee_side=0 视为费前口径，闸门费也一并去掉。
    if gate is not None and g.get("leg") in w.columns and fee > 0:
        leg = g["leg"]
        wl = w[leg].to_numpy(dtype=float)
        dw = np.diff(wl, prepend=wl[0])
        hold = hold_days(w.index, wl > 1e-12)
        hold_prev = np.concatenate([[0], hold[:-1]])
        sell = np.clip(-dw, 0.0, None)
        buy = np.clip(dw, 0.0, None)
        rf = np.array([redeem_rate(h) for h in hold_prev])
        bf = float(g.get("buy_fee", 0.0)) / 100.0
        real = sell * rf + buy * bf
        cost = cost - np.abs(dw) * fee + real
        sold = sell > 1e-12
        gate_fee = dict(redeem=round(float((sell * rf).sum()) * 100, 2),
                        buy=round(float((buy * bf).sum()) * 100, 2),
                        sells=int(sold.sum()), buys=int((buy > 1e-12).sum()),
                        hold_min=int(hold_prev[sold].min()) if sold.any() else 0,
                        hold_med=int(np.median(hold_prev[sold])) if sold.any() else 0)
    net = pd.Series(gross - cost, index=idx)
    turn_s = pd.Series(np.concatenate([[0.0], turn]), index=idx)
    if start:
        net = net[net.index >= pd.Timestamp(start)]
        w = w[w.index >= pd.Timestamp(start)]
    return dict(ret=net, switches=int((turn_s.reindex(net.index) > 1e-9).sum()),
                turn=float(turn_s.reindex(net.index).sum()), w=w.reindex(net.index),
                gate=gate, gate_fee=gate_fee)


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
