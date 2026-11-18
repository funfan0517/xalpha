# -*- coding: utf-8 -*-
"""优化探针 5：实际利率「水平」的方向到底是正还是负？

月频相关 +0.70（水平越高，未来 12 月金价越好）与教科书相反，需要排除「重叠窗口 + 单一样本」。
这里做三件事：非重叠年度点、分年代子样本、以及可执行规则的正反两个方向。
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


def load_xau():
    x = pd.read_csv(F.XAU_CSV)
    x["date"] = pd.to_datetime(x["date"])
    return pd.Series(x["gold"].to_numpy(dtype=float), index=x["date"]).sort_index()


def annual_pairs(price, rate):
    """年度非重叠点：每年首个交易日的实际利率 vs 该年收益。"""
    y = price.resample("YE").last()
    r0 = rate.resample("YE").last().shift(1)          # 上年末利率 = 年初可用值
    df = pd.DataFrame({"price": y, "rate0": r0}).dropna()
    df["ret"] = df["price"].pct_change()
    return df.dropna()


def rule(e, price, tag):
    nav, st, _ = P.nav_of(pd.Series(e, index=price.index).astype(float), price)
    cells = []
    for a, b in ((None, None), ("2013-08-23", "2019-12-31") if len(price) > 3000 else (None, None)):
        m = engine.metrics(nav.loc[a:b] if a else nav)
        cells.append(f"{m['ann']:+6.2%}/{m['mdd']:7.2%}/{m['sharpe']:4.2f}")
    m = engine.metrics(nav)
    print(f"{tag:<34} " + "  ".join(cells) +
          f" | 仓位 {st.mean():4.0%} 调仓 {int((st.diff().abs() > 1e-9).sum()):4d}")


if __name__ == "__main__":
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:  # noqa: BLE001
        pass
    g = load_xau()
    rate = F.read_rate()
    px = F.price_frame()
    close = px["close"].astype(float)

    print("== 年度非重叠点：实际利率水平 vs 同年收益 ==")
    for name, pr in (("XAU", g), ("518880", close)):
        ap = annual_pairs(pr, rate)
        print(f"{name}: n={len(ap)} corr={ap['rate0'].corr(ap['ret']):+.3f}  "
              f"（{ap.index[0].year}~{ap.index[-1].year}）")
        for lo, hi in ((-9, 0), (0, 0.5), (0.5, 1), (1, 2), (2, 9)):
            sub = ap[(ap["rate0"] >= lo) & (ap["rate0"] < hi)]
            if len(sub):
                print(f"   利率 in [{lo},{hi}) : n={len(sub):2d} 当年收益均值 {sub['ret'].mean():+7.2%}")

    print("\n== 分年代（月频，重叠但有指示性）==")
    gm = g.resample("ME").last()
    rm = rate.resample("ME").last()
    for a, b in (("2006", "2012"), ("2013", "2019"), ("2020", "2026")):
        sub = pd.DataFrame({"g": gm, "r": rm}).loc[a:b].dropna()
        sub["fwd"] = sub["g"].shift(-12) / sub["g"] - 1
        sub = sub.dropna()
        print(f"  {a}-{b}: corr(水平, 未来12月)={sub['r'].corr(sub['fwd']):+.3f} "
              f"n={len(sub)}")

    print("\n== 可执行规则：利率高 -> 满仓（顺水平方向），月频，XAU ==")
    rm = rate.resample("ME").last()
    for th in (0.0, 0.5, 1.0, 1.5):
        sig = rm > th
        e = pd.Series(np.where(sig, 1.0, 0.5), index=sig.index).reindex(g.index, method="ffill").fillna(0.5)
        rule(e, g, f"XAU 利率>{th}pp 满仓否则半仓")
    rule(pd.Series(1.0, index=g.index), g, "XAU 恒满仓")
    rule(pd.Series(0.5, index=g.index), g, "XAU 恒半仓")

    print("\n== 同一规则在 518880（13 年）==")
    for th in (0.0, 0.5, 1.0, 1.5):
        sig = rm > th
        e = pd.Series(np.where(sig, 1.0, 0.5), index=sig.index).reindex(close.index, method="ffill").fillna(0.5)
        rule(e, close, f"518880 利率>{th}pp 满仓否则半仓")
    rule(pd.Series(1.0, index=close.index), close, "518880 恒满仓")
    rule(pd.Series(0.5, index=close.index), close, "518880 恒半仓")
