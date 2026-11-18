# -*- coding: utf-8 -*-
"""v15 · 沪深300 · MIN2 双确认（V2∩V12）。

来源：`strategies/vol_legs/research/beat_best/_beat_best_top3.md`（2026-11-18）。
状态：**★ 研究超越项**。_beat_best_top3.md：同尺夏普 0.77→0.97、后段 0.27→0.36，毛夏普 0.82→1.05（信号效应）。

规则：min(V2 BOLL 突破仓位, V12 八态趋势许可上限)——两条都认可才持仓。
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

LEG = "hs300"
LABEL = "v15 沪深300 · MIN2 双确认（V2∩V12）"
STATUS = "★ 研究超越项"
PARAMS = {}

def load(leg=LEG):
    """该腿信号价：真实指数收盘（黄金 = 518880 场内 ETF）。"""
    return bridge.load(leg)


def weight(px):
    """t 日收盘决策仓位 ∈ [0,1]（含 T+1 时滞前的最后一步）。"""
    px = px.astype("float64")
    a = bridge.v2(px, LEG)
    b = bridge.v12(px, LEG)
    return pd.Series(np.minimum(a, b), index=px.index).clip(0.0, 1.0)


def run(px, sig=None, cash=None, fee=None):
    s = weight(px) if sig is None else sig
    return bridge.run(px, s, cash=cash, fee=fee)


def perf(nav):
    return bridge.perf(nav)
