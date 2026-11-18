# -*- coding: utf-8 -*-
"""优化探针：在 518880 全史上快速对比「规则家族」，只为找方向，不产出发布口径。

纪律：
- T 日收盘算信号 -> T+1 收盘成交（state = decide.shift(1)）。
- 单边万 2.5，空仓 1.40% 货基。
- IS 2013-08~2019-12 / OOS1 2020~2023 / OOS2 2024~今，参数只做**同一套**跑三段（不逐段调参）。
"""
import os
import sys

import numpy as np
import pandas as pd

_HERE = os.path.dirname(os.path.abspath(__file__))
_V5 = os.path.dirname(os.path.dirname(_HERE))
_VL = os.path.dirname(_V5)
for _p in (_VL, _V5):
    if _p not in sys.path:
        sys.path.insert(0, _p)

import engine       # noqa: E402
import factors as F  # noqa: E402

FEE = 0.00025
CASH = 0.0140
CASH_D = (1.0 + CASH) ** (1.0 / 252) - 1.0


def nav_of(decide, close, hyst=0.0, fee=FEE):
    """decide: T 日收盘目标仓位 -> 净值。

    执行口径（与 `score.py` 一致，最保守）：
    T 日收盘算信号 -> **T+1 收盘成交** -> 从 **T+2** 起才赚收益。
    即 `st[t] = decide[t]`（t 日盘后决定的仓位，t+1 收盘才生效），
    收益用 `st.shift(2)`，成本按 `st.shift(1)` 的变动在 t+1 计。
    """
    tgt = decide.reindex(close.index).ffill().fillna(0.0).clip(0.0, 1.0).to_numpy()
    n = len(tgt)
    cur = tgt[0]
    st = np.empty(n)
    for t in range(n):
        if abs(tgt[t] - cur) >= hyst - 1e-12:
            cur = tgt[t]
        st[t] = cur
    decided = pd.Series(st, index=close.index)
    eff = decided.shift(1).fillna(st[0])       # t 日收盘成交后生效
    hold = decided.shift(2).fillna(st[0])      # t 日实际承担收益的仓位
    ret = close.pct_change().fillna(0.0)
    r = hold * ret + (1.0 - hold) * CASH_D
    d = eff.diff().fillna(0.0)
    r = r - d.abs() * fee
    return (1.0 + r).cumprod(), eff, tgt


def seg(nav, a=None, b=None):
    s = nav.loc[a:b] if a else nav
    m = engine.metrics(s)
    return m


def line(tag, nav, a=None, b=None, state=None):
    m = seg(nav, a, b)
    sd = state.loc[a:b] if (state is not None and a) else state
    expo = float(sd.mean()) if sd is not None else float("nan")
    tr = int((sd.diff().abs() > 1e-9).sum()) if sd is not None else 0
    return (f"{tag:<34} 年化 {m['ann']:+7.2%}  回撤 {m['mdd']:7.2%}  "
            f"夏普 {m['sharpe']:5.2f}  仓位 {expo:5.0%}  调仓 {tr:4d}")


def main():
    px = F.price_frame()
    close = px["close"].astype(float)
    hi, lo = px["high"].astype(float), px["low"].astype(float)
    ret = close.pct_change()
    vol20 = ret.rolling(20).std() * np.sqrt(252)
    vol60 = ret.rolling(60).std() * np.sqrt(252)
    rate = F.read_rate()
    r5 = rate.reindex(close.index.union(rate.index)).ffill().reindex(close.index).shift(1)

    print(f"518880 {close.index[0].date()} ~ {close.index[-1].date()} n={len(close)}")
    print(f"年化波动 20d 中位 {vol20.median():.1%} / 60d {vol60.median():.1%}")

    variants = {}

    # ---- 基准
    variants["B0 恒满仓"] = pd.Series(1.0, index=close.index)
    variants["B1 恒半仓"] = pd.Series(0.5, index=close.index)
    variants["B2 恒 75%"] = pd.Series(0.75, index=close.index)

    # ---- 现有规则
    T = F.table(px, rate, None, shares=None)
    variants["V0 现规则（两因子三档）"] = T["target"]

    # ---- 家族 A：慢趋势闸门
    for ma in (120, 200, 250):
        m = close.rolling(ma).mean()
        variants[f"A{ma} 慢趋势 0/100"] = (close > m).astype(float)
        variants[f"A{ma} 慢趋势 50/100"] = np.where(close > m, 1.0, 0.5)

    # ---- 家族 B：波动目标
    for tv in (0.10, 0.12, 0.15, 0.18):
        variants[f"B VolTarget {tv:.0%}"] = (tv / vol20).clip(0, 1)

    # ---- 家族 C：慢趋势 × 波动目标
    m200 = close.rolling(200).mean()
    for tv in (0.10, 0.12, 0.15):
        variants[f"C 趋势200×VT{tv:.0%}"] = np.where(close > m200, (tv / vol20).clip(0, 1), 0.0)
        variants[f"C 趋势200×VT{tv:.0%} 半底"] = np.where(
            close > m200, (tv / vol20).clip(0, 1), (tv / vol20).clip(0, 1) * 0.5)

    # ---- 家族 D：实际利率水平（而非变化）
    for win in (120, 250):
        ma_r = r5.rolling(win).mean()
        variants[f"D 利率<{win}日均 0/100"] = np.where(r5 < ma_r, 1.0, 0.0)
        variants[f"D 利率<{win}日均 50/100"] = np.where(r5 < ma_r, 1.0, 0.5)
        variants[f"D 利率<{win}日均 ×VT12%"] = np.where(
            r5 < ma_r, (0.12 / vol20).clip(0, 1), 0.0)

    # ---- 家族 E：动量
    for lb in (60, 120, 252):
        mom = close / close.shift(lb) - 1.0
        variants[f"E 动量{lb}日 0/100"] = (mom > 0).astype(float)
        variants[f"E 动量{lb}日 50/100"] = np.where(mom > 0, 1.0, 0.5)

    # ---- 家族 F：现规则 × 波动目标（把离散档换成风险预算）
    variants["F 现规则×VT15%"] = T["target"] * (0.15 / vol20).clip(0, 1)
    variants["F 现规则×VT15%（下限0.6）"] = T["target"] * (0.15 / vol20).clip(0.6, 1)

    # ---- 家族 G：趋势 + 波动目标 + 利率否决
    variants["G 趋势200 × VT12% × 利率否决"] = np.where(
        (close > m200) & (r5 < r5.rolling(250).mean()),
        (0.12 / vol20).clip(0, 1), 0.0)

    rows = []
    for tag, dec in variants.items():
        if isinstance(dec, np.ndarray):
            dec = pd.Series(dec, index=close.index)
        nav, st, _ = nav_of(dec.fillna(0.0), close)
        rows.append((tag, nav, st))

    print("\n=== 全样本 ===")
    for tag, nav, st in rows:
        print(line(tag, nav, state=st))

    print("\n=== IS 2013-08~2019-12 ===")
    for tag, nav, st in rows:
        print(line(tag, nav, "2013-08-23", "2019-12-31", st))

    print("\n=== OOS1 2020-01~2023-12 ===")
    for tag, nav, st in rows:
        print(line(tag, nav, "2020-01-02", "2023-12-29", st))

    print("\n=== OOS2 2024-01~ ===")
    for tag, nav, st in rows:
        print(line(tag, nav, "2024-01-02", None, st))


if __name__ == "__main__":
    main()
