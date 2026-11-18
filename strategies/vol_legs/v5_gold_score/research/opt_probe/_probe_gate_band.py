# -*- coding: utf-8 -*-
"""探针 10：利率闸门加死区（0.5±band，粘性），看是否减少 2015 那种临界抖动。"""
import os
import sys

import numpy as np
import pandas as pd

_V5 = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.dirname(_V5))
sys.path.insert(0, _V5)
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import _probe_opt as P  # noqa: E402
import engine  # noqa: E402
import factors as F  # noqa: E402


def gate_sticky(lvl, th, band, low):
    v = lvl.to_numpy(dtype=float)
    out = np.empty(len(v))
    cur = 1.0 if (not np.isnan(v[0]) and v[0] > th) else low
    for i, x in enumerate(v):
        if np.isnan(x):
            out[i] = np.nan
            continue
        if x > th + band:
            cur = 1.0
        elif x < th - band:
            cur = low
        out[i] = cur
    return pd.Series(out, index=lvl.index)


def ev(e, close, tag):
    nav, st, _ = P.nav_of(pd.Series(e, index=close.index).astype(float), close)
    m = engine.metrics(nav)
    print(f"{tag:<34} 年化 {m['ann']:+6.2%} 回撤 {m['mdd']:7.2%} 夏普 {m['sharpe']:4.2f} "
          f"| 仓位 {st.mean():4.0%} 调仓 {int((st.diff().abs() > 1e-9).sum()):4d}")


if __name__ == "__main__":
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:  # noqa: BLE001
        pass
    close = F.price_frame()["close"].astype(float)
    rate = F.read_rate()
    p = F.params()
    scale, vol, ok = F.f_vol(F.price_frame(), p)
    g0, used, lvl = F.f_rate_level(rate, close.index, p)
    for band in (0.0, 0.05, 0.1, 0.15, 0.2, 0.3):
        g = gate_sticky(lvl, 0.5, band, 0.5) if band else g0
        e = F._quantize((scale * g).clip(0, 1), 0.2)
        ev(e, close, f"闸门 0.5±{band:.2f}")
    print()
    for th in (0.25, 0.5, 0.75):
        for band in (0.0, 0.1, 0.2):
            g = gate_sticky(lvl, th, band, 0.5) if band else np.where(lvl > th, 1.0, 0.5)
            e = F._quantize((scale * pd.Series(g, index=close.index)).clip(0, 1), 0.2)
            ev(e, close, f"闸门 {th}±{band:.2f}")
