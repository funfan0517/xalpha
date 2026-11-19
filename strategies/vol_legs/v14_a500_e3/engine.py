# -*- coding: utf-8 -*-
"""v14 · 中证A500 · E3 前三等权融合（V3 + V2 + V12）。

来源：`strategies/vol_legs/research/beat_best/_beat_best_top3.md`（2026-11-19 重跑）。
状态：**★ 研究超越项**。同尺夏普 0.66 → **0.82**，后段 0.34 → **0.38**，
年化 +6.59% → +9.22%，回撤 −13.74% → −16.20%；毛夏普 0.74 → 0.92（**信号效应为主**），
换手 8.5 → 44.8 次/年。

规则：`(V3 MACD+BOLL 开口突破 + V2 BOLL 突破 + V12 八态趋势许可) / 3` —— 三条同族信号等权，
跨信号族分散（突破 × 趋势），不重写任何一条规则。
逐日权重直接调各变种的**原生引擎**（经 `strategies/vol_legs/_top3_bridge.py` 隔离加载）。
`weight(t)` = t 日收盘决策，`run()` 内 `shift(1)` → T+1 收益生效。
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

LEG = "a500"
LABEL = "v14 中证A500 · E3 前三等权融合（V3+V2+V12）"
STATUS = "★ 研究超越项"
RULE_TXT = "(V3 MACD+BOLL + V2 BOLL 突破 + V12 八态趋势许可) 等权融合，三条原生引擎各 1/3"
PARAMS = {}

def load(leg=LEG):
    """该腿信号价：真实指数收盘（黄金 = 518880 场内 ETF）。"""
    return bridge.load(leg)


def weight(px):
    """t 日收盘决策仓位 ∈ [0,1]（含 T+1 时滞前的最后一步）。"""
    px = px.astype("float64")
    parts = (bridge.v3(px, LEG), bridge.v2(px, LEG), bridge.v12(px, LEG))
    return sum(p.reindex(px.index).fillna(0.0) for p in parts).div(3.0).clip(0.0, 1.0)


def run(px, sig=None, cash=None, fee=None):
    s = weight(px) if sig is None else sig
    return bridge.run(px, s, cash=cash, fee=fee)


def perf(nav):
    return bridge.perf(nav)
