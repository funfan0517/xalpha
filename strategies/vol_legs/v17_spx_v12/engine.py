# -*- coding: utf-8 -*-
"""v17 · 标普500 · V12 八态趋势（当前 ★1）。

来源：`strategies/vol_legs/research/beat_best/_beat_best_top3.md`（2026-11-19 重跑）。
状态：**研究确认无提升**。同尺基线夏普 0.59（V12）；九个候选里最好的 MAX2 只在全样本
+0.02、后段 0.74 < 基线 0.80，被双闸门挡掉；E2/E3/MIN2/E2×TV20 也都 ≤ 基线。保持 ★1。

规则：V12 八态趋势→仓位许可上限（原生 `engine.backtest_leg` 的逐日权重，v12.1 生效口径）。
2026-11-19 前本策略是「MAX2 任一在场（max(V12, V13)）」的 ★超越项 —— V12 把 `慢跌`
改到 0% 后自己升到标普500 的 ★1，MAX2 相对它不再有增量，故退化为单一 V12。
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

LEG = "spx"
LABEL = "v17 标普500 · V12 八态趋势（当前 ★1）"
STATUS = "研究确认无提升"
RULE_TXT = "V12 八态趋势 → 带内仓位许可上限（原生 engine.backtest_leg，v12.1 生效口径）"
PARAMS = {}

def load(leg=LEG):
    """该腿信号价：真实指数收盘（黄金 = 518880 场内 ETF）。"""
    return bridge.load(leg)


def weight(px):
    """t 日收盘决策仓位 ∈ [0,1]（含 T+1 时滞前的最后一步）。"""
    px = px.astype("float64")
    return bridge.v12(px, LEG)


def run(px, sig=None, cash=None, fee=None):
    s = weight(px) if sig is None else sig
    return bridge.run(px, s, cash=cash, fee=fee)


def perf(nav):
    return bridge.perf(nav)
