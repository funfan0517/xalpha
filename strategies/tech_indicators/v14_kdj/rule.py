# -*- coding: utf-8 -*-
"""变种 14 · KDJ（超买超卖反转）单指标规则。

**本文件是 KDJ 策略的唯一实现**：家族引擎 `../backtest.py::build_signals` 通过
`_rule("KDJ")` 按名分发到这里，其它变种复用同一份代码，不另存副本。

规则：
- 入场：K 上穿 D（金叉）**且** K < 20（超卖区）—— 只在低位接反转，不追高位金叉；
- 离场：K 下穿 D（死叉）**且** K > 80（超买区）—— 高位死叉才走，低位震荡不折腾；
- K/D 任一侧 NaN 时维持上一状态；
- 因果：金叉/死叉判定用 t 与 t-1 的 K/D。
"""
import numpy as np

VARIANT = "KDJ"
FAMILY_KEY = "KDJ"
DESC = "K 上穿 D 且 K < 20（超卖）买入；K 下穿 D 且 K > 80（超买）卖出"
PARAMS = {"N": 9, "K_LOW": 20, "K_HIGH": 80}       # 指标由 data/_indicators_hist.json 预计算


def _arr(x):
    return np.array([np.nan if v is None else float(v) for v in x], dtype=float)


def signal(close, r, k_low=PARAMS["K_LOW"], k_high=PARAMS["K_HIGH"]):
    """按 KDJ 超卖金叉 / 超买死叉生成目标仓位序列（0/1，逐日因果）。

    close: np.ndarray 收盘价（本规则不用，保持统一签名）；r: 指标字典（需 K / D）。
    """
    n = len(close)
    k, d = _arr(r["K"]), _arr(r["D"])
    sig = np.zeros(n, dtype=int)
    p = 0
    for t in range(1, n):
        if np.isnan(k[t]) or np.isnan(d[t]) or np.isnan(k[t - 1]) or np.isnan(d[t - 1]):
            sig[t] = p
            continue
        golden = k[t] > d[t] and k[t - 1] <= d[t - 1]
        death = k[t] < d[t] and k[t - 1] >= d[t - 1]
        if p == 0 and golden and k[t] < k_low:
            p = 1
        elif p == 1 and death and k[t] > k_high:
            p = 0
        sig[t] = p
    return sig
