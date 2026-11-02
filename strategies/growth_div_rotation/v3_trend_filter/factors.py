# -*- coding: utf-8 -*-
"""成长/红利风格轮动 v3 · 指标（风格比值 R / 趋势均线 / 趋势状态）

纯函数，不触碰参数（参数仅在 rule.py）。
"""
import pandas as pd

import rule


def ratio_series(growth_close, div_close):
    """风格比值 R_t = 创业板指收盘 / 中证红利收盘（双列非缺失交集对齐后逐日）。"""
    g = growth_close.astype(float)
    d = div_close.astype(float)
    both = pd.concat([g, d], axis=1).dropna()
    both.columns = ["g", "d"]
    return both["g"] / both["d"]


def trend_ma(growth_close, window=None):
    """创业板指的 window 日简单均线（趋势闸门的基准）。"""
    window = rule.MA_TREND if window is None else window
    return growth_close.astype(float).rolling(window).mean()


def trend_broken(growth_close, window=None):
    """创业板指是否跌破趋势均线（布尔 Series）。

    均线只用当日及之前的数据，判断也用当日收盘（次日按新权重计收益），无前视。
    均线未成形（前 window-1 根）时为 False —— 不阻断，交给 R 规则决定。
    """
    ma = trend_ma(growth_close, window)
    return (growth_close < ma).fillna(False)
