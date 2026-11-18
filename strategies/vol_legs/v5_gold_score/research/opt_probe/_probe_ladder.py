# -*- coding: utf-8 -*-
"""优化探针 3：把「风险预算」压成五档梯子（保留 v5 的产品形态），再看分数倾斜是否显著。

设计：
    raw = clip(VOL_TARGET / 实现波动, MIN_SCALE, 1.0)     # 连续风险预算
    e*  = 把 raw 量化到 {0,.25,.5,.75,1}，跨档才动（滞后 1 档）  # 五档梯子
    score 只做 ±1 档微调（可关）
"""
import itertools
import os
import sys

import numpy as np
import pandas as pd

_HERE = os.path.dirname(os.path.abspath(__file__))
_V5 = os.path.dirname(os.path.dirname(_HERE))
sys.path.insert(0, os.path.dirname(_V5))
sys.path.insert(0, _V5)
sys.path.insert(0, _HERE)

import _probe_opt as P  # noqa: E402
import engine  # noqa: E402
import factors as F  # noqa: E402


def quantize(raw, step=0.25, hyst=1):
    """连续 -> 档位（只跨档才动，滞后 hyst 档）。保留原索引。"""
    s = raw if isinstance(raw, pd.Series) else pd.Series(raw)
    v = s.to_numpy(dtype=float)
    ok = ~np.isnan(v)
    vv = pd.Series(v).ffill().fillna(0.0).to_numpy()
    q = np.round(vv / step) * step
    out = np.empty(len(q))
    cur = q[0] if len(q) else 0.0
    for i, x in enumerate(q):
        if abs(x - cur) >= (hyst - 0.5) * step - 1e-9:
            cur = x
        out[i] = cur
    return pd.Series(np.where(ok, out, np.nan), index=s.index)


def evaluate(tag, e, close):
    nav, st, _ = P.nav_of(pd.Series(e, index=close.index), close)
    cells = []
    for a, b in ((None, None), ("2013-08-23", "2019-12-31"),
                 ("2020-01-02", "2023-12-29"), ("2024-01-02", None)):
        m = engine.metrics(nav.loc[a:b] if a else nav)
        cells.append(f"{m['ann']:+6.2%}/{m['mdd']:7.2%}/{m['sharpe']:4.2f}")
    m = engine.metrics(nav)
    tr = int((st.diff().abs() > 1e-9).sum())
    return (f"{tag:<38} " + " ".join(cells) +
            f" | 仓位 {st.mean():4.0%} 调仓 {tr:4d} 年化 {m['ann']:+.2%}")


if __name__ == "__main__":
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:  # noqa: BLE001
        pass
    px = F.price_frame()
    close = px["close"].astype(float)
    ret = close.pct_change()
    rate = F.read_rate()
    T = F.table(px, rate, None, shares=None)
    score = T["score"].astype(float)

    vols = {w: ret.rolling(w).std() * np.sqrt(252) for w in (20, 60, 120)}

    print("口径：全样本 / IS / OOS1 / OOS2 = 年化/回撤/夏普\n")
    ref = [
        ("恒满仓", pd.Series(1.0, index=close.index)),
        ("恒半仓", pd.Series(0.5, index=close.index)),
        ("V0 现规则（分数→三档）", T["target"]),
    ]
    for tag, e in ref:
        print(evaluate(tag, e, close))

    print("\n-- 连续风险预算（不量化）--")
    for tv, w in itertools.product((0.08, 0.10, 0.12), (60, 120)):
        raw = (tv / vols[w]).clip(0.2, 1.0)
        print(evaluate(f"VT{tv:.0%}/{w}日 连续", raw, close))

    print("\n-- 量化到五档（步长/滞后）--")
    for tv, w in ((0.08, 60), (0.10, 60), (0.10, 120), (0.12, 120)):
        raw = (tv / vols[w]).clip(0.0, 1.0)
        for step in (0.20, 0.25):
            print(evaluate(f"VT{tv:.0%}/{w}日 量化{step:.0%}", quantize(raw, step), close))

    print("\n-- 五档 + 分数 ±1 档倾斜 --")
    raw = (0.10 / vols[60]).clip(0.0, 1.0)
    base = quantize(raw, 0.25)
    for tilt in (-1, 0, 1):
        for cut in (0, 1):
            e = base.copy()
            if tilt:
                e = (e + tilt * (score >= 1) * 0.25 + tilt * (score <= -1) * -0.25).clip(0, 1)
            if cut:
                e = (e * np.where(score >= 0, 1.0, 0.85)).clip(0, 1)
            tag = f"五档 VT10/60 tilt={tilt:+d} cut={cut}"
            print(evaluate(tag, e, close))

    print("\n-- 五档：只让分数降档（不做加档）--")
    for cut in (0.75, 0.5):
        for thr in (-1, 0):
            e = (base * np.where(score > thr, 1.0, cut)).clip(0, 1)
            print(evaluate(f"五档 VT10/60 score>{thr} 否则×{cut:g}", e, close))
