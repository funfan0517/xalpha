# -*- coding: utf-8 -*-
"""优化探针 7：最终候选 rule 的稳健性面（参数网格 + 分段 + 成本 + 换手）。"""
import itertools
import os
import sys

import numpy as np
import pandas as pd

_HERE = os.path.dirname(os.path.abspath(__file__))
_V5 = os.path.dirname(os.path.dirname(_HERE))
sys.path.insert(0, os.path.dirname(_V5))
sys.path.insert(0, _V5)
sys.path.insert(0, _HERE)

import _probe_opt as P  # noqa: E402
import engine  # noqa: E402
import factors as F  # noqa: E402


def quantize(raw, step=0.2, hyst=1):
    s = raw if isinstance(raw, pd.Series) else pd.Series(raw)
    v = s.to_numpy(dtype=float)
    ok = ~np.isnan(v)
    vv = pd.Series(v).ffill().fillna(0.0).to_numpy()
    q = np.round(vv / step) * step
    out = np.empty(len(q))
    cur = q[0] if len(q) else 0.0
    for i, x in enumerate(q):
        if abs(x - cur) >= (hyst - 0.5) * step - 1e-9:
            cur = x
        out[i] = cur
    return pd.Series(np.where(ok, out, np.nan), index=s.index)


def build(price, rate, tv, vw, th, low, step, lag=1, fee=P.FEE):
    ret = price.pct_change()
    vol = ret.rolling(vw).std() * np.sqrt(252)
    scale = (tv / vol).clip(0.2, 1.0)
    rl = rate.sort_index().shift(lag).reindex(price.index.union(rate.index)).ffill().reindex(price.index)
    gate = np.where(rl > th, 1.0, low)
    e = quantize((scale * gate).clip(0, 1), step)
    return e


def score_of(e, price, fee=P.FEE):
    nav, st, _ = P.nav_of(e, price, fee=fee)
    return nav, st, engine.metrics(nav)


if __name__ == "__main__":
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:  # noqa: BLE001
        pass
    close = F.price_frame()["close"].astype(float)
    rate = F.read_rate()
    x = pd.read_csv(F.XAU_CSV)
    x["date"] = pd.to_datetime(x["date"])
    g = pd.Series(x["gold"].to_numpy(dtype=float), index=x["date"]).sort_index()

    b_nav, _, b_m = score_of(pd.Series(1.0, index=close.index), close)
    h_nav, _, h_m = score_of(pd.Series(0.5, index=close.index), close)
    gb_nav, _, gb_m = score_of(pd.Series(1.0, index=g.index), g)
    print(f"基准 518880 满仓 {b_m['ann']:+.2%}/{b_m['mdd']:.2%}/{b_m['sharpe']:.2f} · "
          f"半仓 {h_m['ann']:+.2%}/{h_m['mdd']:.2%}/{h_m['sharpe']:.2f}")
    print(f"基准 XAU 满仓 {gb_m['ann']:+.2%}/{gb_m['mdd']:.2%}/{gb_m['sharpe']:.2f}\n")

    grid = list(itertools.product(
        (0.08, 0.09, 0.10, 0.11, 0.12),   # vol target
        (60, 90, 120, 180),               # vol window
        (0.0, 0.25, 0.5, 0.75, 1.0),      # rate threshold
        (0.4, 0.5, 0.6, 0.7),             # low multiplier
        (0.2, 0.25),                      # step
    ))
    rows = []
    for tv, vw, th, low, step in grid:
        e = build(close, rate, tv, vw, th, low, step)
        nav, st, m = score_of(e, close)
        eg = build(g, rate, tv, vw, th, low, step)
        _, _, mg = score_of(eg, g)
        rows.append(dict(tv=tv, vw=vw, th=th, low=low, step=step,
                         ann=m["ann"], mdd=m["mdd"], sharpe=m["sharpe"],
                         expo=float(st.mean()), trades=int((st.diff().abs() > 1e-9).sum()),
                         g_ann=mg["ann"], g_mdd=mg["mdd"], g_sharpe=mg["sharpe"]))
    df = pd.DataFrame(rows)
    print(f"网格 {len(df)} 组")
    for col, ref, better in (("ann", b_m["ann"], "gt"), ("mdd", b_m["mdd"], "gt"),
                             ("sharpe", b_m["sharpe"], "gt")):
        if better == "gt":
            ok = (df[col] > ref).mean() if col != "mdd" else (df[col] > ref).mean()
        print(f"  518880 {col} 优于满仓: {ok:.0%}  ({df[col].min():.4f} ~ {df[col].max():.4f})")
    print(f"  XAU   sharpe 优于满仓: {(df['g_sharpe'] > gb_m['sharpe']).mean():.0%} "
          f"({df['g_sharpe'].min():.2f} ~ {df['g_sharpe'].max():.2f})")
    print(f"  XAU   mdd 优于满仓: {(df['g_mdd'] > gb_m['mdd']).mean():.0%}")

    print("\n按 vol 目标 / 窗口 / 门槛 / 低配乘数 / 步长的中位数：")
    for c in ("tv", "vw", "th", "low", "step"):
        sub = df.groupby(c).agg(ann=("ann", "median"), mdd=("mdd", "median"),
                                sharpe=("sharpe", "median"), g_sharpe=("g_sharpe", "median"))
        print(f"\n-- {c} --")
        print(sub.to_string(float_format=lambda v: f"{v:.3f}"))

    print("\n默认参数邻域（10%/120/0.5/0.5/0.2）：")
    base = df[(df.tv == 0.10) & (df.vw == 120) & (df.th == 0.5) & (df.low == 0.5) & (df.step == 0.2)]
    print(base.to_string(index=False, float_format=lambda v: f"{v:.4f}"))

    print("\n分段（默认参数）：")
    e = build(close, rate, 0.10, 120, 0.5, 0.5, 0.2)
    nav, st, _ = P.nav_of(e, close)
    for a, b in ((None, None), ("2013-08-23", "2019-12-31"),
                 ("2020-01-02", "2023-12-29"), ("2024-01-02", None)):
        m = engine.metrics(nav.loc[a:b] if a else nav)
        mb = engine.metrics(b_nav.loc[a:b] if a else b_nav)
        print(f"  {a or '全样本'}~{b or '今'}: 策略 {m['ann']:+6.2%}/{m['mdd']:7.2%}/{m['sharpe']:4.2f}"
              f"   满仓 {mb['ann']:+6.2%}/{mb['mdd']:7.2%}/{mb['sharpe']:4.2f}")

    print("\n成本/滑点敏感性：")
    for fee, slip in ((0.00025, 0.0), (0.00025, 0.0005), (0.0005, 0.0005), (0.001, 0.001)):
        nav2, st2, _ = P.nav_of(e, close, fee=fee + slip)
        m = engine.metrics(nav2)
        print(f"  单边 {fee:.4%}+滑点 {slip:.2%}: 年化 {m['ann']:+.2%} 回撤 {m['mdd']:.2%} 夏普 {m['sharpe']:.2f}")
