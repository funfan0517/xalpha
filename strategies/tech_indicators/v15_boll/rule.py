# -*- coding: utf-8 -*-
"""变种 15 · BOLL（布林通道反转）单指标规则。

**本文件是 BOLL 策略的唯一实现**：家族引擎 `../backtest.py::build_signals` 通过
`_rule("BOLL")` 按名分发到这里；v7 风格路由的「防御」档也走这里，不另存副本。

规则：
- 入场：收盘触及/跌破布林下轨（`close <= lower`）—— 超跌接反转；
- 离场：收盘触及/升破布林上轨（`close >= upper`）—— 回到上轨就兑现；
- 上下轨 NaN 时维持上一状态；
- 因果：只用当日上下轨，不含未来（布林带本身用过去 N 日计算）。
"""
import numpy as np

VARIANT = "BOLL"
FAMILY_KEY = "BOLL"
DESC = "收盘触及/跌破下轨买入；收盘触及/升破上轨卖出"
PARAMS = {"N": 20, "K": 2.0}                       # 指标由 data/_indicators_hist.json 预计算


def _arr(x):
    return np.array([np.nan if v is None else float(v) for v in x], dtype=float)


def signal(close, r):
    """按布林上下轨生成目标仓位序列（0/1，逐日因果）。

    close: np.ndarray 收盘价；r: 指标字典（需 BOLL_UPPER / BOLL_LOWER）。
    """
    n = len(close)
    upper, lower = _arr(r["BOLL_UPPER"]), _arr(r["BOLL_LOWER"])
    sig = np.zeros(n, dtype=int)
    p = 0
    for t in range(n):
        if np.isnan(upper[t]) or np.isnan(lower[t]):
            sig[t] = p
            continue
        if p == 0 and close[t] <= lower[t]:
            p = 1
        elif p == 1 and close[t] >= upper[t]:
            p = 0
        sig[t] = p
    return sig
