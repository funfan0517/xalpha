# -*- coding: utf-8 -*-
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(
    os.path.dirname(os.path.abspath(__file__))))))
_V5 = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, _V5)

import engine  # noqa: E402
import score as S  # noqa: E402
import pandas as pd  # noqa: E402

d = S.build()
df, px, start = d["df"], d["px"], d["start"]
px_s = px.loc[start:]
st = df["state"].loc[start:]


def show(tag, state):
    nav = S.run_leg(state, px_s)
    m = engine.metrics(nav)
    print(f"{tag:<26} ann {m['ann']:+.2%} cum {m['cum']:+.2%} vol {m['vol']:.2%} "
          f"mdd {m['mdd']:.2%} sharpe {m['sharpe']:.2f} expo {state.mean():.0%} "
          f"trades {int((state.diff().abs() > 1e-9).sum())}")


show("策略", st)
show("恒满仓", pd.Series(1.0, index=px_s.index))
show("恒半仓", pd.Series(0.5, index=px_s.index))
show("纯现金", pd.Series(0.0, index=px_s.index))

print("\nlegacy score 口径（旧 v5.0）:")
for over in (dict(), dict(TARGET_MODE="score")):
    dd = S.build(**over)
    ss = dd["df"]["state"].loc[dd["start"]:]
    show(f"mode={over or 'risk'}", ss)
