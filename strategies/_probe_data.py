# -*- coding: utf-8 -*-
"""探针: 数据源三条口径的逐日对比（fix#2 的依据与回归检查）。

比较三者的每只标的逐日 OHLCV:
  A 逐标的 xa.get_daily + dropna（= 「标的自身交易日」）
  B 全池面板 + ffill
  C 全池面板不填充 + 丢 NaN 行（当前口径）
只读。
"""
import os
import sys
import warnings

import numpy as np
import pandas as pd

warnings.filterwarnings("ignore")
sys.stdout.reconfigure(encoding="utf-8")
_DIR = os.path.dirname(os.path.abspath(__file__))
for p in (_DIR, os.path.join(_DIR, "lights")):
    if p not in sys.path:
        sys.path.insert(0, p)

import rule

FIELDS = ("open", "high", "low", "close", "volume")


def frame_from(panels, code, end):
    df = pd.DataFrame({f: panels[f][code] for f in FIELDS}).loc[:end]
    return df[df["close"].notna()]


def cmp(a, b):
    """两份同标的 DataFrame -> (行数是否相同, 不一致的字段计数)"""
    if len(a) != len(b):
        return False, {"rows": abs(len(a) - len(b))}
    d = {}
    for f in FIELDS:
        x, y = a[f].to_numpy(float), b[f].to_numpy(float)
        m = ~(np.isclose(x, y, rtol=0, atol=1e-9) | (np.isnan(x) & np.isnan(y)))
        if m.any():
            d[f] = int(m.sum())
    return True, d


raw = rule.load_panels_raw()
filled = rule._data.load_panels(ffill=True)
end = rule._data.complete_end(raw["close"].index)
print(f"面板最后交易日: {end.date()}")

stat = {"A_vs_B": [], "A_vs_C": [], "A_vs_net": []}
for meta in rule.POOL:
    code = meta["code"]
    own = rule._data.load_bars(code)                 # A: 网络最新
    own = own[own["date"] <= end].reset_index(drop=True)
    A = pd.DataFrame({f: own[f].to_numpy(float) for f in FIELDS})
    B = frame_from(filled, code, end)
    C = frame_from(raw, code, end)
    okB, dB = cmp(A, B)
    okC, dC = cmp(A, C)
    if not (okB and not dB):
        stat["A_vs_B"].append((code, okB, dB, len(A), len(B)))
    if not (okC and not dC):
        stat["A_vs_net"].append((code, okC, dC, len(A), len(C)))

n = len(rule.POOL)
print(f"\nA(逐标的网络最新) vs B(面板+ffill)      不一致: {len(stat['A_vs_B'])}/{n}")
for code, ok, d, la, lb in stat["A_vs_B"][:8]:
    print(f"   {code}: 行 {la} vs {lb}  {d}")
print(f"A(逐标的网络最新) vs C(面板不填充+丢NaN) 不一致: {len(stat['A_vs_net'])}/{n}")
for code, ok, d, la, lc in stat["A_vs_net"][:8]:
    print(f"   {code}: 行 {la} vs {lc}  {d}")
