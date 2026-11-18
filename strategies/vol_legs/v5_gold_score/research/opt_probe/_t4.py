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

d = S.build(EVENT_FREEZE=0)
df, px = d["df"], d["px"]
nav_repo = S.run_leg(df["state"], px)
nav_mine, st0, _ = P.nav_of(df["target"], px["close"], hyst=0.25)
cmp = pd.DataFrame({"repo": nav_repo, "mine": nav_mine, "repo_st": df["state"], "mine_st": st0})
for dt in ("2013-08-23", "2014-12-31", "2016-12-30", "2019-12-31", "2023-12-29", "2026-11-17"):
    r = cmp.loc[dt]
    print(f"{dt}  repo nav {r['repo']:.4f} st {r['repo_st']:.2f} | mine nav {r['mine']:.4f} st {r['mine_st']:.2f}")
print()
dr = cmp["repo"].pct_change()
dm = cmp["mine"].pct_change()
dd = (dm - dr).abs()
print("单日收益差 top10:")
print(pd.DataFrame({"repo_r": dr, "mine_r": dm, "gap": dm - dr}).reindex(dd.sort_values(ascending=False).index[:10]).to_string())
