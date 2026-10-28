# -*- coding: utf-8 -*-
"""变种 13 · MACD（基线最强）单指标规则。

**本文件是 MACD 策略的唯一实现**：家族引擎 `../backtest.py::build_signals` 通过
`_rule("MACD")` 按名分发到这里；每日建议（`../daily/_daily_recommend.py` 的 `MACD` 列）
与 v7 风格路由的「中枢/其他」也走这里，不另存副本。

规则：
- 入场：DIF 上穿 DEA（金叉）；
- 离场：DIF 下穿 DEA（死叉）；
- 不看零轴位置（v3/v2 才叠加 DIF>0 过滤）；
- 因果：金叉/死叉判定用 t 与 t-1 的 DIF/DEA。
"""
import numpy as np

VARIANT = "MACD"
FAMILY_KEY = "MACD"
DESC = "DIF 上穿 DEA（金叉）买入；DIF 下穿 DEA（死叉）卖出"
PARAMS = {"FAST": 12, "SLOW": 26, "SIGNAL": 9}     # 指标由 data/_indicators_hist.json 预计算


def _arr(x):
    return np.array([np.nan if v is None else float(v) for v in x], dtype=float)


def signal(close, r):
    """按 MACD 金叉/死叉生成目标仓位序列（0/1，逐日因果）。

    close: np.ndarray 收盘价（本规则不用，保持统一签名）；r: 指标字典（需 DIF / DEA）。
    """
    n = len(close)
    dif, dea = _arr(r["DIF"]), _arr(r["DEA"])
    sig = np.zeros(n, dtype=int)
    p = 0
    for t in range(1, n):
        if np.isnan(dif[t]) or np.isnan(dea[t]) or np.isnan(dif[t - 1]) or np.isnan(dea[t - 1]):
            sig[t] = p
            continue
        golden = dif[t] > dea[t] and dif[t - 1] <= dea[t - 1]
        death = dif[t] < dea[t] and dif[t - 1] >= dea[t - 1]
        if p == 0 and golden:
            p = 1
        elif p == 1 and death:
            p = 0
        sig[t] = p
    return sig
