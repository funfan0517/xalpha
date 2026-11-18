# -*- coding: utf-8 -*-
"""优化探针 4：实际利率水平是否还有「战略层」价值（低频、季度调仓，20 年样本）。

日频 10 日变化已证明是噪声；这里换成「水平 / 分位」，只做战略倾斜，换手极低。
"""
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


def monthly(sig, rebal="M"):
    """把日频信号降成月度（每期最后一个交易日取值），只在换月时更新。"""
    return sig.resample(rebal).last().dropna()


if __name__ == "__main__":
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:  # noqa: BLE001
        pass
    xau = pd.read_csv(F.XAU_CSV)
    xau["date"] = pd.to_datetime(xau["date"])
    g = pd.Series(xau["gold"].to_numpy(dtype=float), index=xau["date"]).sort_index()
    rate = F.read_rate()

    print(f"XAU {g.index[0].date()} ~ {g.index[-1].date()}  n={len(g)}")
    print(f"DFII10 {rate.index[0].date()} ~ {rate.index[-1].date()}")

    # 水平 vs 未来 12 个月收益（月频，避免重叠偏差只做描述）
    rm = monthly(rate)
    gm = g.resample("ME").last()
    fwd12 = (gm.shift(-12) / gm - 1.0)
    j = pd.concat([rm.rename("r"), fwd12.rename("fwd")], axis=1).dropna()
    print(f"\n月频 corr(实际利率水平, 未来12月收益) = {j['r'].corr(j['fwd']):+.3f} "
          f"(n={len(j)})")
    print(f"corr(实际利率 12 月变化, 未来12月收益) = "
          f"{j['r'].diff(12).corr(j['fwd']):+.3f}")
    for lo, hi in ((-9, 0), (0, 1), (1, 2), (2, 9)):
        sub = j[(j["r"] >= lo) & (j["r"] < hi)]
        if len(sub):
            print(f"  实际利率 in [{lo},{hi}) pp: n={len(sub):3d} 未来12月均值 "
                  f"{sub['fwd'].mean():+6.2%}")

    # 战略倾斜：利率低 -> 满仓，利率高 -> 半仓；月度执行
    print("\n战略口径（月频，XAU 计价，成本万2.5/边，现金1.4%）：")
    for th in (0.5, 1.0, 1.5, 2.0):
        sig = monthly(rate) < th
        e = pd.Series(np.where(sig, 1.0, 0.5), index=sig.index).reindex(
            g.index, method="ffill").fillna(0.5)
        nav, st, _ = P.nav_of(e, g)
        m = engine.metrics(nav)
        print(f"  利率<{th}pp 满仓否则半仓: 年化 {m['ann']:+.2%} 回撤 {m['mdd']:.2%} "
              f"夏普 {m['sharpe']:.2f} 仓位 {st.mean():.0%} 调仓 "
              f"{int((st.diff().abs() > 1e-9).sum())}")
    nav, st, _ = P.nav_of(pd.Series(1.0, index=g.index), g)
    m = engine.metrics(nav)
    print(f"  恒满仓 XAU:              年化 {m['ann']:+.2%} 回撤 {m['mdd']:.2%} 夏普 {m['sharpe']:.2f}")

    # 分位数口径（滚动 5 年分位）
    q = rate.rolling(252 * 5, min_periods=252).rank(pct=True)
    qm = monthly(q)
    for cut in (0.3, 0.5, 0.7):
        sig = qm < cut
        e = pd.Series(np.where(sig, 1.0, 0.5), index=sig.index).reindex(
            g.index, method="ffill").fillna(0.5)
        nav, st, _ = P.nav_of(e, g)
        m = engine.metrics(nav)
        print(f"  5年分位<{cut:.0%} 满仓否则半仓: 年化 {m['ann']:+.2%} 回撤 {m['mdd']:.2%} "
              f"夏普 {m['sharpe']:.2f} 仓位 {st.mean():.0%}")

    # 波动目标在 20 年样本上的表现（同一套规则，检验是否只是 2024-2026 的运气）
    ret = g.pct_change()
    v120 = ret.rolling(120).std() * np.sqrt(252)
    for tv in (0.08, 0.10, 0.12):
        e = (tv / v120).clip(0.2, 1.0)
        nav, st, _ = P.nav_of(e, g)
        m = engine.metrics(nav)
        print(f"  VT{tv:.0%}/120日 连续（XAU）: 年化 {m['ann']:+.2%} 回撤 {m['mdd']:.2%} "
              f"夏普 {m['sharpe']:.2f} 仓位 {st.mean():.0%}")
