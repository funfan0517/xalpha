# -*- coding: utf-8 -*-
"""成长/红利风格轮动 · 指标计算（R / 均线 / 缓冲带 / 分位数）

纯函数，不触碰参数（参数仅在 rule.py）；回测与每日信号共用。
"""
import numpy as np
import pandas as pd

import rule


def ratio_series(growth_close, div_close):
    """风格比值 R_t = 成长指数收盘 / 红利指数收盘（对齐后逐日）。"""
    g = growth_close.astype(float)
    d = div_close.astype(float)
    both = pd.concat([g, d], axis=1).dropna()
    both.columns = ["g", "d"]
    r = both["g"] / both["d"]
    return r


def compute_signals(growth_close, div_close):
    """两列收盘 Series(同 index=日期) -> 指标 DataFrame。

    列: R, ma20, ma30, upper, lower, q
      - R     = 成长/红利 比值
      - ma20  = R 的 20 日简单均线
      - ma30  = R 的 30 日简单均线
      - upper = ma20 × (1 + BUFFER)；lower = ma20 × (1 - BUFFER)
      - q     = R 在过去 QUANTILE_WINDOW 日的分位(当前值 <= 窗口内比例)
    """
    r = ratio_series(growth_close, div_close)
    ma20 = r.rolling(rule.MA_FAST).mean()
    ma30 = r.rolling(rule.MA_SLOW).mean()
    upper = ma20 * (1 + rule.BUFFER)
    lower = ma20 * (1 - rule.BUFFER)

    def _rank(x):
        x = np.asarray(x, dtype=float)
        if len(x) == 0:
            return np.nan
        return float((x <= x[-1]).mean())

    q = r.rolling(rule.QUANTILE_WINDOW).apply(_rank, raw=True)
    out = pd.DataFrame({
        "R": r,
        "ma20": ma20,
        "ma30": ma30,
        "upper": upper,
        "lower": lower,
        "q": q,
    })
    return out


def latest_snapshot(sig):
    """指标 DataFrame 末行 -> 决策所需快照 dict（含各字段与是否有效）。"""
    last = sig.iloc[-1]
    prev = sig.iloc[-2] if len(sig) >= 2 else last
    return dict(
        date=str(sig.index[-1].date()),
        R=float(last["R"]),
        R_prev=float(prev["R"]),
        ma20=(None if pd.isna(last["ma20"]) else float(last["ma20"])),
        ma30=(None if pd.isna(last["ma30"]) else float(last["ma30"])),
        upper=(None if pd.isna(last["upper"]) else float(last["upper"])),
        lower=(None if pd.isna(last["lower"]) else float(last["lower"])),
        q=(None if pd.isna(last["q"]) else float(last["q"])),
        valid=not (pd.isna(last["upper"]) or pd.isna(last["q"])),
    )
