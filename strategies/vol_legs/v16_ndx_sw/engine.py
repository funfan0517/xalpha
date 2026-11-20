# -*- coding: utf-8 -*-
"""v16 · 纳指100 · MA200 切换（上=V10 波动层 / 下=V12 八态趋势）。

来源：`strategies/vol_legs/research/beat_best/_beat_best_top3.md`（2026-11-20 第三次重跑）。
状态：**★ 研究超越项（最佳）**。v12 的美股两腿改用 C5 后，纳指的 ★2 由 V9 换成 V12，
`SW` 的「下方切换目标」随之从 V9 变为 V12（V12 是前三里平均仓位最低的一条，47%），
于是 `SW MA200→V12` 同尺夏普 **0.77 → 0.81**、后段 0.94 → **1.01**、回撤 −27.38% → **−12.97%**。
邻域检验（MA 100~300）：100~250 全部仍过双闸门，仅 275/300 掉线 —— 不是单点拟合。

规则：`MA200` 上方用 **V10 波动层**（ER20/10 自适应 + 5 日死区，吃趋势 beta），
下方用 **V12 八态趋势许可**（八态层在下跌段自带降仓/空仓）。
逐日权重直接调各变种的**原生引擎**（经 `strategies/vol_legs/_top3_bridge.py` 隔离加载），
不重写规则。`weight(t)` = t 日收盘决策，`run()` 内 `shift(1)` → T+1 收益生效。
闸门用**未滞后**的 `px > MA200`：决策 t 用的是 t 日收盘已知的均线，
`run()` 的 `shift(1)` 才是那一格时滞（与研究脚本 `switch_ma` 的 `hold = decision.shift(1)` 完全一致）。
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
LABEL = "v16 纳指100 · MA200 切换（上=V10 波动层 / 下=V12 八态趋势）"
STATUS = "★ 研究超越项"
PARAMS = dict(ma_win=200)

MA_WIN = PARAMS["ma_win"]


def load(leg=LEG):
    """该腿信号价：真实指数收盘。"""
    return bridge.load(leg)


def gate(px):
    """MA200 闸门：1 = 站在 MA200 上方（用 V10），0 = 下方（用 V12）。

    均线未成形的前 `MA_WIN` 根按 1 处理（与研究脚本 `switch_ma` 的 `fillna(1.0)` 同义；
    这些根都落在预热窗内，不影响回测，只为让实盘早期不误切到八态）。
    """
    ma = px.rolling(MA_WIN, min_periods=MA_WIN).mean()
    return ((px > ma) | ma.isna()).astype(float)


def weight(px):
    """t 日收盘决策仓位 ∈ [0,1]（含 T+1 时滞前的最后一步）。"""
    px = px.astype("float64")
    hi = bridge.v10(px, LEG)          # MA200 上方：V10 波动层
    lo = bridge.v12(px, LEG)          # MA200 下方：V12 八态趋势
    g = gate(px)
    return (hi * g + lo * (1.0 - g)).clip(0.0, 1.0)


def detail(px):
    """日报用：闸门读数与两条腿各自的决策仓位（全为 t 日收盘决策，未 shift）。"""
    px = px.astype("float64")
    ma = px.rolling(MA_WIN, min_periods=MA_WIN).mean()
    g = gate(px)
    return dict(ma=ma, gate=g, hi=bridge.v10(px, LEG), lo=bridge.v12(px, LEG),
                weight=weight(px))


def run(px, sig=None, cash=None, fee=None):
    s = weight(px) if sig is None else sig
    return bridge.run(px, s, cash=cash, fee=fee)


def perf(nav):
    return bridge.perf(nav)
