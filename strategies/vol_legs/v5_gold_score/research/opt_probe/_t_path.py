# -*- coding: utf-8 -*-
import os
import sys

_V5 = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.dirname(_V5))
sys.path.insert(0, _V5)

import engine  # noqa: E402
import score as S  # noqa: E402

d = S.build()
df = d["df"]
print(df.loc["2026-01-01":, ["close", "vol", "scale", "rate_level", "gate", "target", "state"]]
      .iloc[::20].to_string())
print()
print("2015 熊市片段：")
print(df.loc["2015-05-01":"2015-12-31", ["close", "vol", "scale", "rate_level", "gate", "target", "state"]]
      .iloc[::10].to_string())
start = d["start"]
st = df["state"].loc[start:]
px = d["px"].loc[start:]
for a, b in (("2014-01-23", "2019-12-31"), ("2020-01-02", "2023-12-29"), ("2024-01-02", None)):
    s2 = st.loc[a:b]
    nav = S.run_leg(s2, px.loc[a:b])
    m = engine.metrics(nav)
    navb = S.run_leg(__import__("pandas").Series(1.0, index=s2.index), px.loc[a:b])
    mb = engine.metrics(navb)
    print(f"{a}~{b}: 策略 {m['ann']:+.2%}/{m['mdd']:.2%}/{m['sharpe']:.2f} 仓位 {s2.mean():.0%} | "
          f"满仓 {mb['ann']:+.2%}/{mb['mdd']:.2%}/{mb['sharpe']:.2f}")
