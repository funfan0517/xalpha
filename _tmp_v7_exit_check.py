# -*- coding: utf-8 -*-
"""临时：校验 v7 新增 EXIT/PATCH 与 probe_fusion 的 F1/F3/F6 完全等价，并看七腿影响。"""
import os
import sys

import numpy as np
import pandas as pd

ROOT = os.path.dirname(os.path.abspath(__file__))
VL = os.path.join(ROOT, "strategies", "vol_legs")
for p in (VL, os.path.join(VL, "v7_us_gate"), os.path.join(VL, "v10_vol_layer"),
          os.path.join(VL, "v9_vol_target"), os.path.join(VL, "v8_golden_cross"),
          os.path.join(VL, "research", "probe_fusion")):
    sys.path.insert(0, p)

import data as vdata      # noqa: E402
import rule               # noqa: E402
import us_gate as ug      # noqa: E402
import _probe_fusion as pf  # noqa: E402

print("== 等价性：模块新开关 vs 探针 F1/F3/F6（同一价格序列）==")
SIG = {leg: vdata.series_for_leg(leg) for leg in ("ndx", "spx")}
PF = {
    "F3": pf.make_states(dict(gate="ma200", tv="fixed", tv_hi=0.20, dead=0.10,
                              trigger="month", extra_exit=True), SIG),
    "F1": pf.make_states(dict(gate="ma200", tv="fixed", tv_hi=0.20, dead=0.10,
                              trigger="fast"), SIG),
    "F6": pf.make_states(dict(gate="ma200", tv="fixed", tv_hi=0.20, dead=0.10,
                              trigger="fast", extra_exit=True), SIG),
}
for leg in ("ndx", "spx"):
    sig = SIG[leg]
    base = ug.state_series(sig.index, ug.decisions(leg))
    chk = [
        ("EXIT=none PATCH=none == 老 state_series", ug.build(leg)[1].reindex(sig.index), base),
        ("F3 = EXIT ma50200", ug.build(leg, EXIT="ma50200")[1].reindex(sig.index), PF["F3"][leg]),
        ("F1 = PATCH fast", ug.build(leg, PATCH="fast")[1].reindex(sig.index), PF["F1"][leg]),
        ("F6 = EXIT+PATCH", ug.build(leg, EXIT="ma50200", PATCH="fast")[1].reindex(sig.index),
         PF["F6"][leg]),
    ]
    for tag, a, b in chk:
        d = float((a.reindex(b.index).fillna(1.0) - b).abs().max())
        print(f"  {leg} {tag:42s} max|Δ| = {d:.2e} {'OK' if d < 1e-9 else 'FAIL'}")

print("\n== 七腿影响（各腿自身样本 · 0.10% / 1.40%）==")
print("| 腿 | 老口径 | EXIT | EXIT+补丁 |")
print("|---|---:|---:|---:|")
for leg in rule.LEG_ORDER:
    nav = vdata.series_for_leg(leg)
    row = []
    for kw in ({}, dict(EXIT="ma50200"), dict(EXIT="ma50200", PATCH="fast")):
        _, st, _ = ug.build(leg, **kw)
        nv = ug.run_leg(st.reindex(nav.index).ffill(), nav)
        st2 = ug.stats_all(nv) if hasattr(ug, "stats_all") else None
        p = ug.perf(nv)
        row.append(f"{p['ann']:+.2%}/{p['mdd']:+.1%}/{p['sharpe']:.2f}")
    print(f"| {rule.LEGS[leg]['name']} | {row[0]} | {row[1]} | {row[2]} |")
