# -*- coding: utf-8 -*-
"""临时：v7 七腿组合层（同窗口）在 EXIT 开/关下的对比，用于决定默认档。"""
import os
import sys

ROOT = os.path.dirname(os.path.abspath(__file__))
VL = os.path.join(ROOT, "strategies", "vol_legs")
for p in (VL, os.path.join(VL, "v7_us_gate")):
    sys.path.insert(0, p)

import us_gate as ug      # noqa: E402

for kw in ({}, dict(EXIT="ma50200"), dict(EXIT="ma50200", PATCH="fast")):
    navs, states, idx = {}, {}, None
    for leg in ug.POOL:
        nav, st, _ = ug.build(leg, **kw)
        navs[leg], states[leg] = nav, st
        idx = nav.index if idx is None else idx.intersection(nav.index)
    navs = {k: v.reindex(idx).ffill() for k, v in navs.items()}
    states = {k: v.reindex(idx).ffill() for k, v in states.items()}
    out = ug.portfolio(navs, states)
    pv = out[0] if isinstance(out, tuple) else out
    pf = ug.perf(pv)
    expo = sum(ug.exposure(states[k]) for k in ug.POOL) / len(ug.POOL)
    trades = sum(ug.n_trades(states[k]) for k in ug.POOL)
    tag = kw.get("EXIT", "none") + ("+patch" if kw.get("PATCH") else "")
    cal = ug.calmar(pv)
    print(f"{tag:16s} 年化 {pf['ann']:+.2%} · 回撤 {pf['mdd']:+.2%} · "
          f"夏普 {pf['sharpe']:.2f} · Calmar {cal:.2f} · 仓位 {expo:.0%} · 调仓 {trades}")
