# -*- coding: utf-8 -*-
"""v3 回撤归因：v2（0.35/0.65）的最大回撤到底发生在哪一段、当时是什么仓位。

用途：先定位「回撤来源」，再决定加哪一层过滤 —— 避免盲目堆参数。
用法：python strategies/growth_div_rotation/v3_trend_filter/research/_dd_probe.py
"""
import io
import os
import sys

try:
    sys.stdout.reconfigure(encoding="utf-8")
except (AttributeError, OSError):
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8")

_DIR = os.path.dirname(os.path.abspath(__file__))
_V3 = os.path.dirname(_DIR)
if _V3 not in sys.path:
    sys.path.insert(0, _V3)

import numpy as np  # noqa: E402
import pandas as pd  # noqa: E402

import rule  # noqa: E402
import data as datamod  # noqa: E402
import factors  # noqa: E402
import engine  # noqa: E402


def drawdown(eq):
    peak = eq.cummax()
    dd = eq / peak - 1
    return dd


def main():
    wide = datamod.load_wide()
    g, d = wide[rule.GROWTH_INDEX], wide[rule.DIV_INDEX]
    r = factors.ratio_series(g, d)
    g, d = g.reindex(r.index), d.reindex(r.index)

    W0 = engine.target_weights(r, g, d, use_trend=False)  # 纯 v2（两资产，现金恒 0）
    w = W0["growth"]                                      # 后面只需成长权重这一列
    res = engine.run(g, d, W0)
    eq = res["eq"]
    dd = drawdown(eq)

    print(f"v2 {rule.RATIO_LOW:.2f}/{rule.RATIO_HIGH:.2f} 全期最大回撤 {dd.min():.2%} "
          f"@ {dd.idxmin().date()}")
    peak_before = eq.loc[:dd.idxmin()].idxmax()
    print(f"  回撤起点（前高）{peak_before.date()}  净值 {eq.loc[peak_before]:.3f}"
          f"  →  谷底 {dd.idxmin().date()}  净值 {eq.loc[dd.idxmin()]:.3f}")
    seg = eq.loc[peak_before:dd.idxmin()]
    print(f"  该段 {len(seg)} 交易日 ≈ {(dd.idxmin() - peak_before).days / 365:.2f} 年")

    wseg = w.loc[peak_before:dd.idxmin()]
    print(f"  该段成长权重：均值 {wseg.mean():.2f}，最小 {wseg.min():.2f}，最大 {wseg.max():.2f}")

    # 指数同期表现
    g0, g1 = g.loc[peak_before], g.loc[dd.idxmin()]
    d0, d1 = d.loc[peak_before], d.loc[dd.idxmin()]
    print(f"  同期创业板指 {g0:.0f} → {g1:.0f}（{g1 / g0 - 1:+.2%}）")
    print(f"  同期中证红利 {d0:.0f} → {d1:.0f}（{d1 / d0 - 1:+.2%}）")
    rseg = r.loc[peak_before:dd.idxmin()]
    print(f"  同期 R：{rseg.iloc[0]:.3f} → {rseg.iloc[-1]:.3f}（区间 {rseg.min():.3f}~{rseg.max():.3f}）")

    # 回撤前 5 深（按独立谷底去重）
    print("\n前 5 深回撤（谷底去重，间隔 > 120 交易日）：")
    order = dd.sort_values()
    picked = []
    for dt in order.index:
        if all(abs((dt - p).days) > 120 for p in picked):
            picked.append(dt)
        if len(picked) >= 5:
            break
    for dt in picked:
        pk = eq.loc[:dt].idxmax()
        ws = w.loc[pk:dt]
        print(f"  {dd.loc[dt]:>7.2%}  峰 {pk.date()} → 谷 {dt.date()}  "
              f"成长权重均值 {ws.mean():.2f}  R {r.loc[pk]:.3f}→{r.loc[dt]:.3f}")

    # 按持仓状态拆分：满仓成长 / 半仓 / 空仓 各段的回撤贡献
    print("\n按权重状态统计日收益（v2）：")
    ret = eq.pct_change()
    for name, mask in [("w=1.0 满仓成长", w == 1.0), ("w=0.5", (w > 0.01) & (w < 0.99)),
                       ("w=0.0 空仓红利", w == 0.0)]:
        sub = ret[mask]
        if len(sub) == 0:
            continue
        cum = float((1 + sub).prod() - 1)
        print(f"  {name:<16} 交易日 {len(sub):>5}  累计 {cum:>8.1%}  "
              f"日均 {sub.mean():>7.4%}  日波动 {sub.std():>6.3%}")


if __name__ == "__main__":
    main()
