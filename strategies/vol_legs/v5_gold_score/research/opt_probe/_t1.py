# -*- coding: utf-8 -*-
import os
import sys

_V5 = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.dirname(_V5))
sys.path.insert(0, _V5)
import engine  # noqa: E402
import score as S  # noqa: E402

for k in (0, 2, 5):
    d = S.build(EVENT_FREEZE=k)
    df, px = d["df"], d["px"]
    nav = S.run_leg(df["state"], px)
    m = engine.metrics(nav.loc[d["start"]:])
    st = df["state"].loc[d["start"]:]
    tr = int((df["state"].diff().abs() > 1e-9).sum())
    print(f"freeze={k}  ann {m['ann']:.2%}  mdd {m['mdd']:.2%}  sharpe {m['sharpe']:.2f}  "
          f"avg {st.mean():.0%}  trades {tr}")
