# -*- coding: utf-8 -*-
"""优化探针 6：利率「水平」+ 风险预算（波动目标）组合，双资产、双样本。

规则形态（最终候选）：
    base  = clip(VOL_TARGET / std(120)年化, MIN_SCALE, 1)      # 风险预算
    tilt  = 1.0 if DFII10 > TH else TILT_LOW                   # 实际利率水平
    e*    = 量化到 0/20/40/60/80/100，跨档才动
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


def quantize(raw, step=0.2, hyst=1):
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


def rate_level(rate, index, lag=1):
    """实际利率「水平」（T 日只用 ≤T−1 的值）。"""
    s = rate.sort_index().shift(lag)
    return s.reindex(index.union(s.index)).ffill().reindex(index)


def ev(e, price, tag, segs=None):
    nav, st, _ = P.nav_of(pd.Series(e, index=price.index).astype(float), price)
    cells = []
    for a, b in (segs or ((None, None),)):
        m = engine.metrics(nav.loc[a:b] if a else nav)
        cells.append(f"{m['ann']:+6.2%}/{m['mdd']:7.2%}/{m['sharpe']:4.2f}")
    m = engine.metrics(nav)
    return (f"{tag:<44} " + "  ".join(cells) +
            f" | 仓位 {st.mean():4.0%} 调仓 {int((st.diff().abs() > 1e-9).sum()):4d}")


SEGS = ((None, None), ("2013-08-23", "2019-12-31"), ("2020-01-02", "2023-12-29"), ("2024-01-02", None))

if __name__ == "__main__":
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:  # noqa: BLE001
        pass
    x = pd.read_csv(F.XAU_CSV)
    x["date"] = pd.to_datetime(x["date"])
    g = pd.Series(x["gold"].to_numpy(dtype=float), index=x["date"]).sort_index()
    close = F.price_frame()["close"].astype(float)
    rate = F.read_rate()

    print("== 518880：全样本 / IS / OOS1 / OOS2 ==")
    ret = close.pct_change()
    v120 = ret.rolling(120).std() * np.sqrt(252)
    rl = rate_level(rate, close.index)
    base = (0.10 / v120).clip(0.2, 1.0)

    print(ev(pd.Series(1.0, index=close.index), close, "恒满仓", SEGS))
    print(ev(pd.Series(0.5, index=close.index), close, "恒半仓", SEGS))
    print(ev(quantize(base, 0.2), close, "VT10/120 量化20%（无利率）", SEGS))
    for th, low in ((0.0, 0.6), (0.5, 0.6), (0.5, 0.5), (1.0, 0.6), (0.5, 0.8)):
        tilt = np.where(rl > th, 1.0, low)
        e = quantize((base * tilt).clip(0, 1), 0.2)
        print(ev(e, close, f"VT10/120 + 利率>{th}pp 否则×{low:g}", SEGS))

    print("\n== XAU 20 年（同一套参数，检验外推）==")
    retg = g.pct_change()
    vg = retg.rolling(120).std() * np.sqrt(252)
    rlg = rate_level(rate, g.index)
    baseg = (0.10 / vg).clip(0.2, 1.0)
    print(ev(pd.Series(1.0, index=g.index), g, "恒满仓"))
    print(ev(pd.Series(0.5, index=g.index), g, "恒半仓"))
    print(ev(quantize(baseg, 0.2), g, "VT10/120 量化20%（无利率）"))
    for th, low in ((0.0, 0.6), (0.5, 0.6), (0.5, 0.5), (1.0, 0.6)):
        tilt = np.where(rlg > th, 1.0, low)
        e = quantize((baseg * tilt).clip(0, 1), 0.2)
        print(ev(e, g, f"VT10/120 + 利率>{th}pp 否则×{low:g}"))

    print("\n== XAU 分段（每 5 年）==")
    for a, b in (("2006-11-07", "2011-12-31"), ("2012-01-01", "2016-12-31"),
                 ("2017-01-01", "2021-12-31"), ("2022-01-01", None)):
        e = quantize((baseg * np.where(rlg > 0.5, 1.0, 0.6)).clip(0, 1), 0.2)
        nav, st, _ = P.nav_of(e, g)
        m = engine.metrics(nav.loc[a:b] if a else nav)
        mb = engine.metrics(P.nav_of(pd.Series(1.0, index=g.index), g)[0].loc[a:b] if a else
                            P.nav_of(pd.Series(1.0, index=g.index), g)[0])
        print(f"  {a}~{b or '今'}: 策略 {m['ann']:+6.2%}/{m['mdd']:7.2%}/{m['sharpe']:4.2f}"
              f"   满仓 {mb['ann']:+6.2%}/{mb['mdd']:7.2%}/{mb['sharpe']:4.2f}")
