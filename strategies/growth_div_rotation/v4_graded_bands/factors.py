# -*- coding: utf-8 -*-
"""成长/红利风格轮动 v4 · 指标（风格比值 R / 趋势均线 / 阶梯分档）

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
    """price 是否跌破趋势均线（布尔 Series）。

    均线只用当日及之前的数据，判断也用当日收盘（次日按新权重计收益），无前视。
    均线未成形（前 window-1 根）时为 False —— 不阻断，交给 R 阶梯决定。
    """
    ma = trend_ma(price, window)
    return (price < ma).fillna(False)


def band_of(x, bands=None):
    """R 值 -> 档位序号（0=最左/满仓成长 … len-1=最右/满仓红利），NaN 返回 -1。"""
    bands = rule.RATIO_BANDS if bands is None else bands
    if x is None or (isinstance(x, float) and np.isnan(x)):
        return -1
    for i, (hi, _) in enumerate(bands):
        if x < hi:
            return i
    return len(bands) - 1


def band_weights(x, bands=None):
    """R 值 -> (成长比例, 红利比例)，**按比例归一**，合计恒为 1。

    档位是「成长:红利」的**比值**，不是「占 5 份里的几份」：
    5:0 → 100/0、4:1 → 80/20、1:1 → **50/50**、1:4 → 20/80、0:5 → 0/100。
    ！（若按「除以总份数」实现，1:1 会变成 20%+20%+60% 现金 —— 那是错的。）
    NaN 返回 None（由调用方沿用上一档）。
    """
    bands = rule.RATIO_BANDS if bands is None else bands
    i = band_of(x, bands)
    if i < 0:
        return None
    wg, wd = bands[i][1]
    total = float(wg + wd)
    return wg / total, wd / total


def band_label(i, bands=None):
    """档位序号 -> 「4:1」这样的标签（供报告与图例用）。"""
    bands = rule.RATIO_BANDS if bands is None else bands
    if i < 0:
        return "—"
    wg, wd = bands[i][1]
    return f"{wg}:{wd}"
