# -*- coding: utf-8 -*-
"""变种 11 · MA（双均线生命线）单指标规则。

**本文件是 MA 策略的唯一实现**：家族引擎 `../backtest.py::build_signals` 通过
`_rule("MA")` 按名分发到这里，其它变种与每日建议复用同一份代码，不另存副本。

规则：
- 入场：收盘上穿 MA20（生命线）**且** 收盘 > MA60（牛熊线）—— 短均线转头 + 中期趋势同向；
- 离场：收盘下穿 MA20 —— 只认生命线，不看 MA60（MA60 只作入场过滤）；
- 预热不足（MA20/MA60 为 NaN）时维持上一状态；
- 因果：第 t 根只用 t 与 t-1 的价格与均线，无未来函数。
"""
import numpy as np

VARIANT = "MA"
FAMILY_KEY = "MA"           # 家族引擎 STRAT 里的键名
DESC = "收盘上穿 MA20(生命线) 且价 > MA60(牛熊线) 买入；收盘下穿 MA20 卖出"
PARAMS = {"MA_FAST": 20, "MA_SLOW": 60}


def _arr(x):
    return np.array([np.nan if v is None else float(v) for v in x], dtype=float)


def signal(close, r):
    """按 MA 规则生成目标仓位序列（0/1，逐日因果）。

    close: np.ndarray 收盘价；r: 指标字典（需 MA20 / MA60，来自 data/_indicators_hist.json）。
    周期写死为 20/60：指标库只提供这两个均线序列，改周期需先在 data/_refresh_data.py 里补列。
    """
    n = len(close)
    fast, slow = _arr(r["MA20"]), _arr(r["MA60"])
    sig = np.zeros(n, dtype=int)
    p = 0
    for t in range(1, n):
        if np.isnan(fast[t]) or np.isnan(slow[t]) or np.isnan(fast[t - 1]):
            sig[t] = p
            continue
        if p == 0 and close[t] > fast[t] and close[t - 1] <= fast[t - 1] and close[t] > slow[t]:
            p = 1
        elif p == 1 and close[t] < fast[t] and close[t - 1] >= fast[t - 1]:
            p = 0
        sig[t] = p
    return sig
