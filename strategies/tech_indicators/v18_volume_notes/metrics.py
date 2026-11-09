# -*- coding: utf-8 -*-
"""V18 的绩效函数（年化 / 回撤 / 夏普 / Calmar）。

2026-11-09 从 `strategies/vol_legs/engine.py::metrics` **冻结快照**而来，只做两处调整：
① 无风险利率取本变种的 `LG.CASH_ANNUAL`（不再跨家族 import）；
② 把 vol_notes 里那层 `perf()` 包装合并进来（原先是 `perf → engine.metrics` 两级）。

口径与家族其它变种一致：`ann = (末值/首值) ** (252/n) - 1`（几何年化）、
夏普 = (ann − rf) / 年化波动、回撤按**日收盘**峰值回撤（不含盘中）。
"""
import math
import os
import sys

_HERE = os.path.dirname(os.path.abspath(__file__))
if _HERE not in sys.path:
    sys.path.insert(0, _HERE)

import legs as LG  # noqa: E402


def metrics(nav, rf=None, periods=252):
    """净值序列 → 绩效字典（cum / ann / vol / mdd / sharpe / days / calmar）。"""
    rf = LG.CASH_ANNUAL if rf is None else rf
    nav = nav.dropna()
    if len(nav) < 3:
        return dict(cum=0.0, ann=0.0, vol=0.0, mdd=0.0, sharpe=0.0, calmar=0.0, days=len(nav))
    rs = nav.pct_change().dropna().tolist()
    n = len(rs)
    growth = float(nav.iloc[-1] / nav.iloc[0])
    ann = growth ** (periods / n) - 1.0
    mu = sum(rs) / n
    sd = math.sqrt(sum((x - mu) ** 2 for x in rs) / n) * math.sqrt(periods)
    peak, mdd = float(nav.iloc[0]), 0.0
    for v in nav:
        peak = max(peak, float(v))
        mdd = min(mdd, float(v) / peak - 1.0)
    calmar = ann / abs(mdd) if abs(mdd) > 1e-9 else 0.0
    return dict(cum=growth - 1.0, ann=ann, vol=sd, mdd=mdd, calmar=calmar,
                sharpe=(ann - rf) / sd if sd > 1e-9 else 0.0, days=n)


def perf(nav):
    """`metrics` 的别名（本变种旧代码里的叫法）。"""
    return metrics(nav)


if __name__ == "__main__":
    import numpy as np
    import pandas as pd

    idx = pd.bdate_range("2024-01-01", periods=500)
    rng = np.random.default_rng(7)
    r = pd.Series(rng.normal(0.0004, 0.011, len(idx)), index=idx)
    nv = (1.0 + r).cumprod()
    m = metrics(nv)
    print(f"自检 OK · 年化 {m['ann']:+.2%} 回撤 {m['mdd']:.2%} 夏普 {m['sharpe']:.2f} "
          f"Calmar {m['calmar']:.2f}（随机序列，仅验证公式可跑）")
