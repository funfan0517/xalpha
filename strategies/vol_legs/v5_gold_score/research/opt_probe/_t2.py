# -*- coding: utf-8 -*-
import os
import sys

import pandas as pd

_HERE = os.path.dirname(os.path.abspath(__file__))
_V5 = os.path.dirname(os.path.dirname(_HERE))
sys.path.insert(0, os.path.dirname(_V5))
sys.path.insert(0, _V5)
sys.path.insert(0, _HERE)

import _probe_opt as P  # noqa: E402
import engine  # noqa: E402
import score as S  # noqa: E402

for k in (0, 2):
    d = S.build(EVENT_FREEZE=k)
    df, px = d["df"], d["px"]
    nav0, st0, _ = P.nav_of(df["target"], px["close"], hyst=0.25)
    miss = (st0 - df["state"]).abs()
    print(f"freeze={k}: state 不一致天数 {int((miss > 1e-9).sum())}/{len(miss)}")
    bad = miss.loc[d["start"]:]
    print("  首 6 个不一致日:", [str(x.date()) for x in bad[bad > 1e-9].index[:6]])
    print("  mine", engine.metrics(nav0.loc[d["start"]:]))
