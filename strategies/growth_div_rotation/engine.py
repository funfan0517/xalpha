# -*- coding: utf-8 -*-
"""成长/红利风格轮动 · 二元状态机引擎（无前视）

状态: 0 = 红利/现金；1 = 成长。
决策（次日开盘执行）：用 R_{-1}（最新可得收盘）对应的指标判断：
  - 持有红利/现金(0): 若 R_{-1} > 上轨 且 Q_{-1} < ENTER_Q_MAX → 切成长(1)
  - 持有成长(1):       若 R_{-1} < 下轨 或 Q_{-1} > EXIT_Q_MIN → 切红利(0)
  - 其余（含中间地带 下轨<R<上轨 且 0.3<Q<0.7）→ 不交易，留原仓
  - 月频上限：当月切换已 >= MAX_TRADES_PER_MONTH 则暂停下一次（防摩擦）
  - 指标未成形（前 MA_FAST/QUANTILE_WINDOW 日）不切换，留原仓
"""
import pandas as pd

import rule


def run_strategy(sig, growth_close, div_close,
                 buffer=None, enter_q=None, exit_q=None,
                 max_trades_month=None, fee=None):
    """信号 DataFrame(compute_signals 产物) + 两列收盘 -> 回测结果。

    返回 dict: eq(净值Series), pos(0/1 Series), trades(逐段 legs),
               switches(切换事件), month_count, total_switches, fee, params
    """
    buffer = rule.BUFFER if buffer is None else buffer
    enter_q = rule.ENTER_Q_MAX if enter_q is None else enter_q
    exit_q = rule.EXIT_Q_MIN if exit_q is None else exit_q
    max_trades_month = (rule.MAX_TRADES_PER_MONTH if max_trades_month is None
                        else max_trades_month)
    fee = rule.FEE if fee is None else fee

    n = len(sig)
    # 对齐到 sig 的索引（ratio 已按双列非缺失交集裁剪，growth_close 可能更长）
    gclose = growth_close.reindex(sig.index).astype(float)
    dclose = div_close.reindex(sig.index).astype(float)
    gret = gclose.pct_change().fillna(0.0)
    dret = dclose.pct_change().fillna(0.0)

    eq_vals = [1.0]
    pos_vals = [0]
    state = 0
    leg_side = 0
    leg_start = 0
    trades, switches = [], []
    month_count = {}

    for t in range(1, n):
        dt = sig.index[t]
        sp = sig.iloc[t - 1]
        r_prev = sp["R"]
        up, lo, q_prev = sp["upper"], sp["lower"], sp["q"]
        valid = not (pd.isna(up) or pd.isna(q_prev))
        want = state
        if valid:
            if state == 0:
                if r_prev > up and q_prev < enter_q:
                    want = 1
            else:
                if r_prev < lo or q_prev > exit_q:
                    want = 0
        switch = want != state
        if switch:
            mk = (dt.year, dt.month)
            if month_count.get(mk, 0) >= max_trades_month:
                want = state            # 暂停一次，防摩擦
                switch = False
        if switch:
            mk = (dt.year, dt.month)
            month_count[mk] = month_count.get(mk, 0) + 1
            leg_g = 1.0
            for i in range(leg_start, t):
                leg_g *= (1.0 + (gret.iloc[i] if leg_side == 1 else dret.iloc[i]))
            trades.append(dict(
                code=("GROWTH" if leg_side == 1 else "DIVIDEND"),
                entry_date=str(sig.index[leg_start].date()),
                exit_date=str(sig.index[t - 1].date()),
                bars=t - 1 - leg_start,
                ret=leg_g - 1.0,
            ))
            switches.append(dict(
                date=str(dt.date()), from_side=state, to_side=want,
                R=round(float(r_prev), 5), q=round(float(q_prev), 4),
                upper=(None if pd.isna(up) else round(float(up), 5)),
                lower=(None if pd.isna(lo) else round(float(lo), 5)),
                fee=fee,
            ))
            leg_side = want
            leg_start = t
        state = want
        pos_vals.append(state)
        r = gret.iloc[t] if state == 1 else dret.iloc[t]
        if switch:
            r -= fee
        eq_vals.append(eq_vals[-1] * (1.0 + r))

    # 收尾：闭合最后一段
    if leg_start < n - 1:
        leg_g = 1.0
        for i in range(leg_start, n):
            leg_g *= (1.0 + (gret.iloc[i] if leg_side == 1 else dret.iloc[i]))
        trades.append(dict(
            code=("GROWTH" if leg_side == 1 else "DIVIDEND"),
            entry_date=str(sig.index[leg_start].date()),
            exit_date=str(sig.index[n - 1].date()),
            bars=(n - 1) - leg_start,
            ret=leg_g - 1.0,
        ))

    eq = pd.Series(eq_vals, index=sig.index)
    pos = pd.Series(pos_vals, index=sig.index)
    params = dict(buffer=buffer, enter_q=enter_q, exit_q=exit_q,
                  max_trades_month=max_trades_month, fee=fee)
    return dict(eq=eq, pos=pos, trades=trades, switches=switches,
                month_count=month_count, total_switches=len(switches),
                params=params)
