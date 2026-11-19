# -*- coding: utf-8 -*-
"""V2 · 双动量轮动引擎：横截面排名 → 绝对过滤 → 月度持仓 → 记账。

与 v1 最大的不同：v1 是**绝对读数**（温度/因子阈值），v2 是**相对排名**——
「谁最近 6 个月最强」，再问一句「它自己是不是正收益」，不正就不做。

时序：每月最后一个交易日收盘算排名 → 下个交易日（T+1）起承担新持仓。
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


def nav_frame(navs, codes=None):
    """所有候选按日期并表（各自成立日前为 NaN，不 ffill 造历史）。"""
    codes = codes or CFG["universe"]
    cols = [navs[c].rename(c) for c in codes if c in navs]
    return pd.concat(cols, axis=1, join="outer").sort_index()


def momentum_table(df, lookback=None):
    """每只标的的 lookback 日累计收益（NaN = 样本不足，不参与排名）。"""
    lb = CFG["lookback"] if lookback is None else lookback
    return df / df.shift(lb) - 1.0


def select(mom_row):
    """单日横截面选择 -> (dict{code: w}, 说明)。

    绝对动量过滤：第一名动量 ≤ abs_floor → 全部切防御；
    否则取前 top_n 名等权（缺席/NaN 的不参与）。
    """
    row = mom_row.dropna()
    if row.empty:
        return {}, "无可排名标的（样本不足）"
    ranked = row.sort_values(ascending=False)
    best = ranked.index[0]
    if float(ranked.iloc[0]) <= CFG["abs_floor"]:
        d = CFG["defensive"]
        if d in df_codes_ok(ranked) and d in row.index:
            return {d: 1.0}, f"绝对动量过滤：最强 {best} {float(ranked.iloc[0]) * 100:+.2f}% ≤ 0 → 全切 {d}"
        return {d: 1.0}, f"绝对动量过滤（{d} 当日无有效动量，仍按防御处理）"
    picks = list(ranked.index[:CFG["top_n"]])
    w = 1.0 / len(picks)
    return {c: w for c in picks}, (
        "持前 %d：" % len(picks) + "、".join(f"{c} {float(ranked[c]) * 100:+.1f}%" for c in picks))


def df_codes_ok(ranked):
    return list(ranked.index)


def build_weights(df, lookback=None):
    """-> (权重 DataFrame(index=全部交易日, columns=候选), rebal_info list)。

    每个调仓日的决定在该日收盘作出，下一交易日才生效（对外用 shift(1)）。
    """
    lb = CFG["lookback"] if lookback is None else lookback
    mom = momentum_table(df, lb)
    reb = rule.is_rebalance(df.index)
    reb_dates = df.index[reb & (mom.notna().any(axis=1))]
    # 必须用 NaN 初始化：用 0.0 初始化时 ffill 无法把调仓日的持仓带到下一月
    # （ffill 只填 NaN），回测会退化成「只有月末那天在场、其余空仓」，收益全被吃掉。
    target = pd.DataFrame(np.nan, index=df.index, columns=df.columns)
    infos = []
    for d in reb_dates:
        w, why = select(mom.loc[d])
        for c in df.columns:
            target.loc[d, c] = w.get(c, 0.0)
        infos.append(dict(date=str(d.date()), why=why, picks=list(w),
                          mom={c: round(float(mom.loc[d, c]) * 100, 2)
                               for c in df.columns if pd.notna(mom.loc[d, c])}))
    return target.ffill().fillna(0.0), infos


def evaluate(navs, asof=None, lookback=None):
    """-> 当日决策 dict（读最后一个调仓日的持仓，不产生日内动作）。"""
    df = nav_frame(navs)
    mom = momentum_table(df, lookback)
    target, infos = build_weights(df, lookback)
    last = target.iloc[-1]
    held = {c: round(float(w), 4) for c, w in last.items() if w > 1e-9}
    latest_mom = mom.iloc[-1].dropna().sort_values(ascending=False)

    now_mom = {c: round(float(v) * 100, 2) for c, v in latest_mom.items()}
    last_reb = infos[-1] if infos else dict(date="—", why="尚未产生有效调仓", picks=[])
    next_reb = "下月最后一个交易日"

    bp = CFG["sleeve_bond_pp"]
    rows = []
    for c in CFG["universe"]:
        w = held.get(c, 0.0)
        rows.append(dict(code=c, num=rule.NUM.get(c, 0), name=rule.NAMES.get(c, c),
                         short=rule.NAMES.get(c, c),
                         theme=("现金替补" if c == CFG["defensive"] else "债腿候选"),
                         role=("防御降落伞" if c == CFG["defensive"] else "轮动候选"),
                         nav=round(float(df[c].dropna().iloc[-1]), 4) if c in df and df[c].notna().any() else None,
                         r=now_mom.get(c), w=round(w * bp, 2),
                         act="buy" if w > 0 else "none",
                         label=f"{w * bp:.1f}pp" if w > 0 else "—",
                         note=("月度排名前 %d 等权" % CFG["top_n"]) if w > 0 else "未入选"))
    for c, frac in CFG["cash"].items():
        rows.append(dict(code=c, num=701 if c == "000198" else 702, name=rule.NAMES[c],
                         short=rule.NAMES[c], theme="现金腿", role="现金腿",
                         nav=None, r=None, w=round(frac * CFG["sleeve_cash_pp"], 2),
                         act="hold", label=f"{frac * CFG['sleeve_cash_pp']:.1f}pp",
                         note="与 v0/v1 一致"))

    return dict(
        as_of=asof or str(df.index[-1].date()),
        momentum=dict(win=CFG["lookback"], top_n=CFG["top_n"], abs_floor=CFG["abs_floor"],
                      table=now_mom, defensive=CFG["defensive"]),
        held=held,
        last_rebalance=last_reb,
        next_rebalance=next_reb,
        rows=rows,
        bond_pp=bp,
        cash_pp={c: round(v * CFG["sleeve_cash_pp"], 2) for c, v in CFG["cash"].items()},
    )


# ---------------------------------------------------------------- 回测
def backtest(df, lookback=None, fee_side=None, start=None):
    """双动量轮动回测：T 日调仓决定 → T+1 承担收益。

    -> dict(ret, switches, turn, avg_holdings)
    """
    fee = CFG["fee_side"] if fee_side is None else fee_side
    target, infos = build_weights(df, lookback)
    w_eff = target.shift(1).fillna(0.0)         # 首月空仓，等第一个完整动量窗口
    rets = df.pct_change()
    ret = (w_eff * rets).sum(axis=1, min_count=1).dropna()

    turn = w_eff.diff().abs().sum(axis=1).reindex(ret.index).fillna(0.0)
    cost = turn * fee                            # Σ|Δw| 已含卖+买两侧
    net = ret - cost
    if start is not None:
        net = net[net.index >= pd.Timestamp(start)]
    return dict(ret=net, gross=ret.reindex(net.index), switches=len(infos),
                turn=float(turn.reindex(net.index).sum()),
                avg_holdings=float((w_eff.reindex(net.index) > 1e-9).sum(axis=1).mean()),
                infos=infos)


def buy_hold(df, code, start=None):
    s = df[code].pct_change().dropna()
    return s[s.index >= pd.Timestamp(start)] if start else s


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
