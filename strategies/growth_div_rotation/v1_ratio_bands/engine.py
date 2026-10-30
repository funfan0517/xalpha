# -*- coding: utf-8 -*-
"""成长/红利风格轮动 v1 · 绝对阈值 + 5 期分批 + 定投 状态机（无前视）

与 v0 引擎（`../engine.py`）**逐行同口径**：
  用 `ratio[t-1]` 决策、在 `price[t-1]` 成交、赚取 `ret[t] = price[t]/price[t-1]-1`。
  等价于「收盘后按同一收盘价成交」——两侧真实下单都有 1 日延迟，同受影响，
  因此不影响 v1/v0 的相对比较（正是本次对比的唯一目的）。

状态：target ∈ {growth, dividend}；pending = 未完成的分批（缺 `none` 态，见 rule.py）。
每日动作顺序（同一 bar 内）：换仓卖出 → 定投到账 → 买 1 份分批 → 买普通定投 → 估值。
"""
import numpy as np
import pandas as pd

import rule


def month_start_flags(idx):
    """每个自然月首个交易日 -> True 的布尔数组（定投日口径）。"""
    s = pd.Series(idx, index=range(len(idx)))
    first = s.groupby([idx.year, idx.month]).first()
    return np.isin(s.values, first.values)


def tranche_due_bars(t, n, schedule, gap, tranche, mstart):
    """触发 bar t 之后的分批买入 bar 序列（含触发日本身 = 第 1 份）。"""
    if schedule == "days":
        due = [t + i * gap for i in range(tranche)]
    else:  # monthly：首份在触发日，其余逐月首个交易日
        later = [j for j in range(t + 1, n) if mstart[j]]
        due = [t] + later[:tranche - 1]
    return [j for j in due if j < n]


def run(gclose, dclose, ratio, *, fixed_amount=rule.FIXED_AMOUNT,
        initial_cash=rule.INIT_CASH_DCA, target_init=rule.TARGET_INIT,
        tranche=rule.TRANCHE, schedule=rule.TRANCHE_SCHEDULE, gap=rule.TRANCHE_GAP_DAYS,
        ratio_low=rule.RATIO_LOW, ratio_high=rule.RATIO_HIGH, fee=rule.FEE,
        interrupt=rule.INTERRUPT_PENDING):
    """返回 dict：equity/twr/weight_g 三条 Series + switches/tranche_log/contributions。

    equity = 账面总资产（受定投现金流影响，**不可**直接当年化收益）
    twr    = 时间加权净值（剔除定投现金流，与 v0 的 eq 同口径，用于绩效对比）
    """
    idx = ratio.index
    n = len(idx)
    g = gclose.reindex(idx).astype(float).values
    d = dclose.reindex(idx).astype(float).values
    r = ratio.astype(float).values
    rg = np.concatenate([[0.0], g[1:] / g[:-1] - 1.0])
    rd = np.concatenate([[0.0], d[1:] / d[:-1] - 1.0])
    mstart = month_start_flags(idx)

    cash = float(initial_cash)
    ug = ud = 0.0
    target = target_init
    pending = None
    pending_due = []
    switches, tranche_log = [], []
    contributions = []          # [(bar, amount)]

    equity = np.zeros(n)
    twr = np.zeros(n)
    wg = np.zeros(n)

    def price(side, bar):
        return g[bar] if side == "growth" else d[bar]

    # ---- 建仓 ----
    if fixed_amount <= 0 and cash > 0:                 # 口径 A：一次性本金全额买入起始标的
        units = cash / price(target, 0) * (1 - fee)
        if target == "growth":
            ug = units
        else:
            ud = units
        cash = 0.0
    equity[0] = cash + ug * g[0] + ud * d[0]
    twr[0] = 1.0
    wg[0] = (ug * g[0]) / equity[0] if equity[0] > 0 else 0.0

    for t in range(1, n):
        eg_prev = equity[t - 1]
        pex = t - 1                                     # 成交价用上一根收盘（同 v0 口径）
        fee_amt = 0.0

        # 1) 换仓判定（用 R_{-1}）→ 清仓旧标的，资金拆 TRANCHE 份
        was_pending = pending is not None
        new = (rule.check_signal(r[t - 1], target, pending, interrupt,
                                ratio_low, ratio_high)
               if not np.isnan(r[t - 1]) else None)
        if new is not None:
            px = price(target, pex)
            old_units = ug if target == "growth" else ud
            proceeds = old_units * px
            cash += proceeds * (1 - fee)
            fee_amt += proceeds * fee
            if target == "growth":
                ug = 0.0
            else:
                ud = 0.0
            pending = {"target": new, "per_buy": cash / tranche,
                       "remaining": int(tranche), "trigger": str(idx[t].date())}
            pending_due = tranche_due_bars(t, n, schedule, gap, tranche, mstart)
            switches.append(dict(
                date=str(idx[t].date()), from_side=target, to_side=new,
                R=round(float(r[t - 1]), 4), reason="R≤%.2f" % ratio_low
                if new == "growth" else "R≥%.2f" % ratio_high,
                sold=round(proceeds, 2), per_buy=round(pending["per_buy"], 2),
                interrupted=was_pending,
            ))
            target = new

        # 2) 定投到账（定投日；分批期间照常投，且投向新标的）
        c = 0.0
        if fixed_amount > 0 and mstart[t]:
            c = float(fixed_amount)
            cash += c
            contributions.append((t, c))

        # 3) 买入到期份额（同一 bar 可能有多份到期：gap=0 即「一次性」对照口径）
        while pending is not None and pending_due and pending_due[0] == t:
            pending_due.pop(0)
            per = pending["per_buy"]
            if cash >= per * 0.999:
                px = price(pending["target"], pex)
                units = per / px * (1 - fee)
                if pending["target"] == "growth":
                    ug += units
                else:
                    ud += units
                cash -= per
                fee_amt += per * fee
                tranche_log.append(dict(date=str(idx[t].date()), side=pending["target"],
                                        amount=round(per, 2),
                                        left=int(pending["remaining"]) - 1))
            pending["remaining"] -= 1
            if pending["remaining"] <= 0:
                pending = None
                pending_due = []

        # 4) 普通定投买入当前标的
        if c > 0:
            px = price(target, pex)
            units = c / px * (1 - fee)
            if target == "growth":
                ug += units
            else:
                ud += units
            cash -= c
            fee_amt += c * fee

        # 5) 估值 + 时间加权收益
        equity[t] = cash + ug * g[t] + ud * d[t]
        val_before = ug * g[t - 1] + ud * d[t - 1] + cash
        w_growth_now = (ug * g[t - 1]) / val_before if val_before > 0 else 0.0
        wg[t] = w_growth_now
        drag = (fee_amt / eg_prev) if eg_prev > 0 else 0.0
        r_port = w_growth_now * rg[t] + (1 - w_growth_now) * rd[t] - drag
        twr[t] = twr[t - 1] * (1.0 + r_port)

    return dict(
        equity=pd.Series(equity, index=idx),
        twr=pd.Series(twr, index=idx),
        weight_g=pd.Series(wg, index=idx),
        switches=switches,
        tranche_log=tranche_log,
        contributions=contributions,
        pending_left=(0 if pending is None else pending["remaining"]),
        params=dict(ratio_low=ratio_low, ratio_high=ratio_high, tranche=tranche,
                    schedule=schedule, gap=gap, fee=fee, fixed_amount=fixed_amount,
                    initial_cash=initial_cash, target_init=target_init,
                    interrupt=interrupt),
    )


def simulate_weights(w_growth, gclose, dclose, *, fixed_amount=0.0, initial_cash=1.0,
                     fee=rule.FEE, contrib_flags=None):
    """通用「按目标权重持仓」定投模拟器 —— 用于把 v0 的 pos(0/1) 拉到同一资金口径。

    与 v1 引擎同口径：`w_growth[t]` 是赚取 `ret[t]` 的权重（决策来自 t-1）。
    返回 dict(equity, twr, contributions)。
    """
    idx = w_growth.index
    n = len(idx)
    g = gclose.reindex(idx).astype(float).values
    d = dclose.reindex(idx).astype(float).values
    w = w_growth.astype(float).values
    rg = np.concatenate([[0.0], g[1:] / g[:-1] - 1.0])
    rd = np.concatenate([[0.0], d[1:] / d[:-1] - 1.0])
    flags = month_start_flags(idx) if contrib_flags is None else np.asarray(contrib_flags)

    eq = np.zeros(n)
    twr = np.zeros(n)
    contribs = []
    cur = float(initial_cash)
    eq[0] = cur
    twr[0] = 1.0
    for t in range(1, n):
        r_port = w[t] * rg[t] + (1.0 - w[t]) * rd[t]
        turn = abs(w[t] - w[t - 1])
        r_port -= fee * turn
        twr[t] = twr[t - 1] * (1.0 + r_port)
        eq[t] = eq[t - 1] * (1.0 + r_port)
        if fixed_amount > 0 and flags[t]:
            eq[t] += fixed_amount
            contribs.append((t, fixed_amount))
    return dict(equity=pd.Series(eq, index=idx), twr=pd.Series(twr, index=idx),
                contributions=contribs)


def xirr(contributions, final_equity, idx):
    """资金加权年化（XIRR，二分法）。contributions=[(bar, amount)]。"""
    if not contributions or final_equity <= 0:
        return np.nan
    flows = [(idx[b], -a) for b, a in contributions]
    flows.append((idx[-1], float(final_equity)))
    flows.sort(key=lambda x: x[0])
    t0 = flows[0][0]

    def npv(rate):
        return sum(a / (1.0 + rate) ** ((dt - t0).days / 365.0) for dt, a in flows)

    lo, hi = -0.9999, 10.0
    f_lo, f_hi = npv(lo), npv(hi)
    if f_lo * f_hi > 0:
        return np.nan
    for _ in range(200):
        mid = 0.5 * (lo + hi)
        f_mid = npv(mid)
        if f_lo * f_mid <= 0:
            hi, f_hi = mid, f_mid
        else:
            lo, f_lo = mid, f_mid
    return 0.5 * (lo + hi)
