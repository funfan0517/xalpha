# -*- coding: utf-8 -*-
"""成长/红利风格轮动 v5 · 指标（风格比值 R / 趋势均线 / 已实现波动）

纯函数，不触碰参数（参数仅在 rule.py）。
"""
import numpy as np
import pandas as pd

import rule


def ratio_series(growth_close, div_close):
    """风格比值 R_t = 创业板指收盘 / 中证红利收盘（双列非缺失交集对齐后逐日）。"""
    g = growth_close.astype(float)
    d = div_close.astype(float)
    both = pd.concat([g, d], axis=1).dropna()
    both.columns = ["g", "d"]
    return both["g"] / both["d"]


def trend_ma(price, window=None):
    """price 的 window 日简单均线（趋势闸门的基准）。两侧各用自己那条。"""
    window = rule.MA_TREND if window is None else window
    return price.astype(float).rolling(window).mean()


def trend_broken(price, window=None):
    """price 是否跌破趋势均线（布尔 Series）；均线未成形时为 False。"""
    ma = trend_ma(price, window)
    return (price < ma).fillna(False)


def realized_vol(eq, window=None):
    """净值序列的 window 日已实现年化波动（用日收益的标准差 × √252）。

    **截止到 t 日收盘**（含当日），调用方负责只用 t-1 及之前的值（见 engine.run）。
    """
    window = rule.VOL_WINDOW if window is None else window
    rr = eq.astype(float).pct_change()
    return rr.rolling(window).std() * np.sqrt(252)


def vol_scale(vol, target=None, deadband=None, floor=None):
    """已实现波动 -> 仓位缩放系数（长度与 vol 相同）。

    scale_t = clip(min(1, target / vol_{t-1}), floor, 1)：**只减不加**，永不放大杠杆。
    死区 `deadband`：与当前系数相差不足该幅度则不动，避免每天微调。
    第 t 个位置的系数只用 `vol[t-1]`，因此 t 日调仓不依赖 t 日收益（无前视）。

    「两次缩放/换仓之间至少隔几天」由 engine 的全局限速 `throttle` 统一负责 —— 放在那一层
    才能同时管住「换档」与「改缩放」两种调仓，见 engine.run 的 min_rebalance。
    """
    target = rule.VOL_TARGET if target is None else target
    deadband = rule.VOL_DEADBAND if deadband is None else deadband
    floor = rule.VOL_FLOOR if floor is None else floor
    v = np.asarray(vol, dtype=float)
    n = len(v)
    out = np.ones(n)
    cur = 1.0
    for t in range(1, n):
        x = v[t - 1]
        want = 1.0 if (np.isnan(x) or x <= 0) else min(1.0, target / x)
        want = max(want, floor)
        if abs(want - cur) >= deadband:
            cur = want
        out[t] = cur
    return out
