# -*- coding: utf-8 -*-
"""临时：① 模块 EXIT/PATCH 与探针等价；② 长样本看「EXIT 清仓 vs EXIT 降到 floor」。"""
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
import us_gate as ug      # noqa: E402
import _probe_fusion as pf  # noqa: E402

SIG = {leg: vdata.series_for_leg(leg) for leg in ("ndx", "spx")}
PF = {
    "F3": pf.make_states(dict(gate="ma200", tv="fixed", tv_hi=0.20, dead=0.10,
                              trigger="month", extra_exit=True), SIG),
    "F1": pf.make_states(dict(gate="ma200", tv="fixed", tv_hi=0.20, dead=0.10,
                              trigger="fast"), SIG),
    "F6": pf.make_states(dict(gate="ma200", tv="fixed", tv_hi=0.20, dead=0.10,
                              trigger="fast", extra_exit=True), SIG),
}
print("== 等价性（模块 vs 探针）==")
for leg in ("ndx", "spx"):
    sig = SIG[leg]
    for tag, a, b in [
        ("F3", ug.build(leg, EXIT="ma50200")[1].reindex(sig.index), PF["F3"][leg]),
        ("F1", ug.build(leg, PATCH="fast")[1].reindex(sig.index), PF["F1"][leg]),
        ("F6", ug.build(leg, EXIT="ma50200", PATCH="fast")[1].reindex(sig.index), PF["F6"][leg]),
    ]:
        d = float((a.reindex(b.index).fillna(1.0) - b).abs().max())
        print(f"  {leg} {tag} max|Δ| = {d:.2e} {'OK' if d < 1e-9 else 'FAIL'}")

print("\n== 长样本 1995+：EXIT 清仓 vs EXIT 降到 floor（50/50 · 0.10%）==")
px = {leg: pf.pg.long_series(pf.pg.CODES[leg]) for leg in ("ndx", "spx")}
base_p = dict(pf.P0, gate="ma200", tv="fixed", tv_hi=0.20, dead=0.10,
              trigger="month", extra_exit=True)
st0 = pf.make_states(dict(gate="ma200", tv="fixed", tv_hi=0.20, dead=0.10, trigger="month"), px)
mask = {leg: pf.gate_ma50200(px[leg]) > 0.5 for leg in ("ndx", "spx")}
FLOOR = {"ndx": 0.0, "spx": 0.5}
CASES = {
    "老口径（无离场）": st0,
    "EXIT → 清仓 0": {leg: st0[leg].where(mask[leg], 0.0) for leg in ("ndx", "spx")},
    "EXIT → 降到 floor": {leg: st0[leg].where(mask[leg], FLOOR[leg]) for leg in ("ndx", "spx")},
}
print("| 档 | 年化 | 回撤·日频 | 回撤·月末 | 夏普 | Calmar(月末) | 调仓 |")
print("|---|---:|---:|---:|---:|---:|---:|")
for tag, st in CASES.items():
    nv, expo, trades = pf.sim(st, px, 0.001)
    s = pf.pg.stats(nv)
    print(f"| {tag} | {s['ann']:+.2%} | {s['mdd']:+.2%} | {s['mdd_me']:+.2%} | "
          f"{s['sharpe']:.2f} | {s['calmar_me']:.2f} | {trades} |")
