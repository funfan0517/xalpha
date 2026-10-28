# -*- coding: utf-8 -*-
"""变种 12 · VOL（量价）单指标规则。

**本文件是 VOL 策略的唯一实现**：家族引擎 `../backtest.py::build_signals` 通过
`_rule("VOL")` 按名分发到这里，其它变种与每日建议复用同一份代码，不另存副本。

规则：
- 入场：放量上涨（收盘涨 **且** 成交量 > 5 日均量）；
- 离场：放量下跌（跌 + 量放大）**或** 缩量上涨（涨但量不足）—— 只要量价不配合就走；
- 量或量均线为 NaN 时维持上一状态；
- 因果：量价均用 t 与 t-1，5 日均量含 t 当日（不含未来）。
"""
import numpy as np
import pandas as pd

VARIANT = "VOL"
FAMILY_KEY = "VOL"
DESC = "放量上涨买入；放量下跌或缩量上涨卖出"
PARAMS = {"VOL_MA": 5}


def _arr(x):
    return np.array([np.nan if v is None else float(v) for v in x], dtype=float)


def signal(close, r, vol_ma=PARAMS["VOL_MA"]):
    """按量价规则生成目标仓位序列（0/1，逐日因果）。

    close: np.ndarray 收盘价；r: 指标字典（需 volume）。
    """
    n = len(close)
    vol = _arr(r["volume"])
    vma = pd.Series(vol).rolling(vol_ma).mean().to_numpy()
    sig = np.zeros(n, dtype=int)
    p = 0
    for t in range(1, n):
        if np.isnan(vol[t]) or np.isnan(vma[t]):
            sig[t] = p
            continue
        up = close[t] > close[t - 1]
        dn = close[t] < close[t - 1]
        vup = vol[t] > vma[t]
        if p == 0 and up and vup:
            p = 1
        elif p == 1 and ((dn and vup) or (up and not vup)):
            p = 0
        sig[t] = p
    return sig
