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
import score as S  # noqa: E402

d = S.build(EVENT_FREEZE=0)
df, px = d["df"], d["px"]
nav0, st0, _ = P.nav_of(df["target"], px["close"], hyst=0.25)
cmp = pd.DataFrame({"repo": df["state"], "mine": st0, "close": px["close"]})
cmp["diff"] = cmp["mine"] - cmp["repo"]
bad = cmp[cmp["diff"].abs() > 1e-9]
print(bad.to_string())
print()
print("repo 累计(不一致日) 收益:", float((1 + px["close"].pct_change().loc[bad.index]).prod()))
print("差异分布:", bad["diff"].value_counts().to_dict())
