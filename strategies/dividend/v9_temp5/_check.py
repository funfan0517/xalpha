import sys, os
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import factors
import pandas as pd

pn = factors.build()
print("code   name            dy_src   可用月   T@2026-07-01  T@2026-09-01  T@2026-10-28")
for code, p in pn.items():
    f = p["fund"]
    t = factors.temp_at(p["daily"], ["2026-07-01", "2026-09-01", "2026-10-28"],
                        has_valuation=f["has_valuation"])
    vals = {}
    for ts, r in t.iterrows():
        vals[ts] = float(r["temp"])
    n = int(p["monthly"]["temp"].notna().sum())

    def g(k):
        return "  —  " if k not in vals else "{0:5.1f}".format(vals[k])

    print("{0} {1:<14} {2!s:<8} {3:4d}     {4}        {5}        {6}".format(
        code, f["short"], p.get("dy_src"), n,
        g(pd.Timestamp("2026-07-01")), g(pd.Timestamp("2026-09-01")), g(pd.Timestamp("2026-10-28"))))

print()
d = pn["090010"]["daily"]
print("090010 daily 末尾: dy / y10 / spread")
print(d[["dy", "y10", "spread"]].tail(3).to_string())
