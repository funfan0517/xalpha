# -*- coding: utf-8 -*-
"""优化探针 9：事件冻结 / 起手档 / 滞后 在新口径下的敏感性。"""
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
import _probe_grid as G  # noqa: E402
import factors as F  # noqa: E402


def run(e, close, block=None, start=0.5, hyst_step=None):
    """带事件冻结的状态机（冻结只禁加仓）。"""
    tgt = pd.Series(e, index=close.index).ffill().fillna(start).clip(0, 1).to_numpy()
    n = len(tgt)
    blk = np.zeros(n, dtype=bool) if block is None else block
    st = np.empty(n)
    cur = start
    pend = None
    stepT = G.quantize(pd.Series([0, 1], index=[0, 1]), 0.2)  # noqa: F841
    for t in range(n):
        if pend is not None:
            cur = pend
            pend = None
        st[t] = cur
        if abs(tgt[t] - cur) < 1e-9:
            continue
        if tgt[t] > cur and blk[t]:
            continue
        pend = tgt[t]
    eff = pd.Series(st, index=close.index)
    hold = eff.shift(1).fillna(start)
    ret = close.pct_change().fillna(0.0)
    r = hold * ret + (1 - hold) * P.CASH_D - eff.diff().fillna(0).abs() * P.FEE
    return (1 + r).cumprod(), eff


if __name__ == "__main__":
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:  # noqa: BLE001
        pass
    px = F.price_frame()
    close = px["close"].astype(float)
    rate = F.read_rate()
    e = G.build(close, rate, 0.10, 120, 0.5, 0.5, 0.2)
    idx = close.index

    print("事件窗口覆盖：", end="")
    for k in (0, 2, 5):
        m = F._event_mask(idx, k)
        print(f"±{k}日 {m.mean():.0%}  ", end="")
    print()

    for k in (0, 2, 3, 5):
        blk = F._event_mask(idx, k).to_numpy(dtype=bool)
        nav, st = run(e, close, blk)
        mm = engine.metrics(nav)
        print(f"事件冻结 ±{k}: 年化 {mm['ann']:+.2%} 回撤 {mm['mdd']:.2%} 夏普 {mm['sharpe']:.2f} "
              f"仓位 {st.mean():.0%} 调仓 {int((st.diff().abs() > 1e-9).sum())}")

    print("\n起手档：")
    nav0, _ = run(e, close)
    for s in (0.2, 0.5, 0.8, 1.0):
        nav, st = run(e, close, start=s)
        mm = engine.metrics(nav)
        print(f"  起手 {s:.0%}: 年化 {mm['ann']:+.2%} 回撤 {mm['mdd']:.2%} 夏普 {mm['sharpe']:.2f}")

    print("\n滞后档数（量化步长 20%）：")
    for h in (1, 2, 3):
        ee = G.quantize(((0.10 / (close.pct_change().rolling(120).std() * np.sqrt(252))).clip(0.2, 1.0)
                         * np.where(rate.sort_index().shift(1).reindex(idx.union(rate.index)).ffill()
                                    .reindex(idx) > 0.5, 1.0, 0.5)).clip(0, 1), 0.2,
                        hyst=h)
        nav, st = P.nav_of(ee, close)
        mm = engine.metrics(nav)
        print(f"  HYST={h}: 年化 {mm['ann']:+.2%} 回撤 {mm['mdd']:.2%} 夏普 {mm['sharpe']:.2f} "
              f"调仓 {int((st.diff().abs() > 1e-9).sum())}")

    nav, st = run(e, close, F._event_mask(idx, 2).to_numpy(dtype=bool))
    m = engine.metrics(nav)
    print(f"\n当前读数（{idx[-1].date()}）：目标 {e.iloc[-1]:.0%} 当前 {st.iloc[-1]:.0%} "
          f"波动 {close.pct_change().rolling(120).std().iloc[-1] * np.sqrt(252):.1%} "
          f"利率 {rate.iloc[-1]:.2f}%")
