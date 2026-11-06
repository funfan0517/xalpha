# -*- coding: utf-8 -*-
"""一次性探针：v5 净值口径 vs v4 close 口径的 MA250+20 四象限是否等价。"""
import importlib.util
import os
import sys

import numpy as np
import pandas as pd

V4 = r"g:\xalpha\strategies\vol_legs\v4_erp_band"
V5 = r"g:\xalpha\strategies\vol_legs\v5_trend_gate"
sys.path.insert(0, V4)
sys.path.insert(0, V5)
try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:  # noqa: BLE001
    pass

import erp  # noqa: E402
import ladder as L  # noqa: E402
import trend_gate as tg  # noqa: E402

erp.CACHE_DAYS = 99999
_spec = importlib.util.spec_from_file_location("_v4bt", os.path.join(V4, "backtest.py"))
bt = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(bt)

df = L.frame()
d0 = df[df["ready"]]
t0 = d0.index[0]
bond = bt.bond_series("synth")

# A：v5 净值口径
px5 = tg.load("hs300")
IA = tg.indicators(px5, KIND="ma", MA_WIN=250, SLOPE_N=20).reindex(df.index).ffill()
IA["quad"] = IA["quad"].astype(str)

# B：v4 close 口径（同一参数）
c = df["close"].astype(float)
ma = c.rolling(250, min_periods=250).mean()
slope = ma - ma.shift(20)
ok = ma.notna() & slope.notna()
above = (c >= ma) & ok
up = (slope > 0) & ok
QB = np.where(ok, np.where(above, np.where(up, tg.QUADS[0], tg.QUADS[1]),
                           np.where(up, tg.QUADS[2], tg.QUADS[3])), "")

sub = IA.loc[df.index >= t0]
m = ok.loc[df.index >= t0].to_numpy()
qa = sub["quad"].to_numpy(dtype=object)[m]
qb = QB[df.index >= t0][m]
diff = qa != qb
print(f"就绪对比 {len(qa)} 日 · 象限不一致 {int(diff.sum())} 日 ({diff.mean():.1%})")
for q in tg.QUADS:
    print(f"  {q:6s} A(v5净值) {int((qa == q).sum()):5d} 日 · B(close) {int((qb == q).sum()):5d} 日")
same_up = ((qa == tg.QUADS[0]) == (qb == tg.QUADS[0]))
same_dn = ((qa == tg.QUADS[3]) == (qb == tg.QUADS[3]))
print(f"  「上涨」判定一致 {same_up.mean():.1%} · 「下跌」判定一致 {same_dn.mean():.1%}")
if diff.any():
    idx = sub.index[m][diff]
    print(f"  不一致日期示例：{', '.join(f'{d:%Y-%m-%d}' for d in idx[:8])}")
