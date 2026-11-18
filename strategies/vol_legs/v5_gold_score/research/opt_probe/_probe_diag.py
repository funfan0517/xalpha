# -*- coding: utf-8 -*-
"""优化探针 8：最终候选的机制诊断（利率闸门触发时点 / 位移安慰剂 / 事件冻结 / 起手档）。"""
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


def ev(e, price, tag):
    nav, st, _ = P.nav_of(pd.Series(e, index=price.index).astype(float), price)
    m = engine.metrics(nav)
    return (f"{tag:<40} 年化 {m['ann']:+6.2%} 回撤 {m['mdd']:7.2%} 夏普 {m['sharpe']:4.2f}"
            f" | 仓位 {st.mean():4.0%} 调仓 {int((st.diff().abs() > 1e-9).sum()):4d}")


if __name__ == "__main__":
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:  # noqa: BLE001
        pass
    close = F.price_frame()["close"].astype(float)
    rate = F.read_rate()
    ret = close.pct_change()
    v120 = ret.rolling(120).std() * np.sqrt(252)
    rl = rate.sort_index().shift(1).reindex(close.index.union(rate.index)).ffill().reindex(close.index)

    print("利率水平分布：", rl.describe(percentiles=[.1, .25, .5, .75, .9]).round(3).to_dict())
    gate = rl > 0.5
    print(f"闸门开启（利率>0.5pp）占比 {gate.mean():.0%}")
    print("开启日按年：", pd.Series(gate, index=close.index).resample("YE").mean().round(2).to_dict())

    print("\n== 步长 0.20 vs 0.25（默认参数）==")
    for step in (0.2, 0.25):
        e = G.build(close, rate, 0.10, 120, 0.5, 0.5, step)
        print(ev(e, close, f"VT10/120 利率0.5 低配0.5 步长{step:.0%}"))

    print("\n== 安慰剂：把利率闸门信号整体平移 ==")
    for lag in (-500, -250, -120, 0, 120, 250, 500):
        r = rl.shift(lag)
        g2 = np.where(r > 0.5, 1.0, 0.5)
        e = G.quantize(((0.10 / v120).clip(0.2, 1.0) * g2).clip(0, 1), 0.2)
        print(ev(e, close, f"闸门平移 {lag:+d} 交易日"))

    print("\n== 安慰剂：闸门换成「恒定比例」==")
    for frac in (1.0, 0.8, 0.6, 0.4):
        e = G.quantize(((0.10 / v120).clip(0.2, 1.0) * frac), 0.2)
        print(ev(e, close, f"无闸门，整体×{frac:g}"))

    print("\n== 闸门与「价格趋势」「份额」的相关性（是否只是另一种趋势）==")
    m200 = close.rolling(200).mean() > 0
    trend = (close > close.rolling(200).mean())
    print(f"corr(闸门, 年线上方) = {pd.Series(gate.astype(float)).corr(trend.astype(float)):.3f}")

    print("\n== 逐段闸门贡献（有/无）==")
    e_with = G.build(close, rate, 0.10, 120, 0.5, 0.5, 0.2)
    e_none = G.build(close, rate, 0.10, 120, -9.9, 1.0, 0.2)
    for a, b in ((None, None), ("2013-08-23", "2019-12-31"), ("2020-01-02", "2023-12-29"),
                 ("2024-01-02", None)):
        for e, tag in ((e_with, "有闸门"), (e_none, "无闸门")):
            nav, st, _ = P.nav_of(e, close)
            m = engine.metrics(nav.loc[a:b] if a else nav)
            print(f"  {a or '全样本'}~{b or '今'} {tag}: {m['ann']:+6.2%}/{m['mdd']:7.2%}/{m['sharpe']:4.2f}"
                  f" 仓位 {st.loc[a:b].mean() if a else st.mean():.0%}")
        print()
