# -*- coding: utf-8 -*-
"""v16 · 纳指100 · V10 波动层（当前 ★1）。

来源：`strategies/vol_legs/research/beat_best/_beat_best_top3.md`（2026-11-18）。
状态：**研究确认无提升**。_beat_best_top3.md：最佳候选 ROT 全样本只赢 0.02（0.79 vs 0.77）、后段 0.93 < 0.94；保持当前 ★1 V10。

规则：V10 ER20/10 自适应波动层 + 5 日死区（原生 vol_layer.state_daily）。
逐日权重直接调各变种的**原生引擎**（经 `strategies/vol_legs/_top3_bridge.py` 隔离加载），
不重写规则。`weight(t)` = t 日收盘决策，`run()` 内 `shift(1)` → T+1 收益生效。
成本：研究口径统一单边 10bp（非该腿原生费率），空仓货基 1.40%。
"""
import os
import sys

import numpy as np
import pandas as pd

_HERE = os.path.dirname(os.path.abspath(__file__))
_STRAT = os.path.dirname(_HERE)
_VL = os.path.dirname(_STRAT)
for _p in (_HERE, _STRAT, _VL):
    if _p not in sys.path:
        sys.path.insert(0, _p)

from _top3_bridge import CASH_ANNUAL, FEE_SIDE, WARMUP, bridge  # noqa: E402

LEG = "ndx"
LABEL = "v16 纳指100 · V10 波动层（当前 ★1）"
STATUS = "研究确认无提升"
PARAMS = {}

def load(leg=LEG):
    """该腿信号价：真实指数收盘（黄金 = 518880 场内 ETF）。"""
    return bridge.load(leg)


def weight(px):
    """t 日收盘决策仓位 ∈ [0,1]（含 T+1 时滞前的最后一步）。"""
    px = px.astype("float64")
    return bridge.v10(px, LEG)


def run(px, sig=None, cash=None, fee=None):
    s = weight(px) if sig is None else sig
    return bridge.run(px, s, cash=cash, fee=fee)


def perf(nav):
    return bridge.perf(nav)
