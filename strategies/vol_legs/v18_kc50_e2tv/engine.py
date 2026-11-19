# -*- coding: utf-8 -*-
"""v18 · 科创50 · E2×TV20 波动上限。

来源：`strategies/vol_legs/research/beat_best/_beat_best_top3.md`（2026-11-18）。
状态：**★ 研究超越项**。_beat_best_top3.md：同尺夏普 0.79→0.90、后段 1.57→1.85，回撤 −14.37%→−8.31%；样本仅 6 年。

规则：(V2 + V12 等权融合) × 波动上限 TV20%（研究同款 cap 层）。
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

LEG = "kc50"
LABEL = "v18 科创50 · E2×TV20 波动上限"
STATUS = "★ 研究超越项"
PARAMS = {"TV": 0.20, "TV_WIN": 20}

def load(leg=LEG):
    """该腿信号价：真实指数收盘（黄金 = 518880 场内 ETF）。"""
    return bridge.load(leg)


def weight(px):
    """t 日收盘决策仓位 ∈ [0,1]（含 T+1 时滞前的最后一步）。"""
    px = px.astype("float64")
    a = bridge.v2(px, LEG)
    b = bridge.v12(px, LEG)
    e2 = ((a + b) / 2.0).reindex(px.index).fillna(0.0)
    cap = bridge.cap_for_decision(px, tv=0.20)
    return (e2 * cap).reindex(px.index).fillna(0.0).clip(0.0, 1.0)


def run(px, sig=None, cash=None, fee=None):
    s = weight(px) if sig is None else sig
    return bridge.run(px, s, cash=cash, fee=fee)


def perf(nav):
    return bridge.perf(nav)
