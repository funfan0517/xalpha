# -*- coding: utf-8 -*-
"""v20 · 黄金 · V5.1 黄金仓位（风险预算 × 实际利率闸门）· 当前 ★1。

来源：`strategies/vol_legs/research/best_by_leg/_best_by_leg.md` §0（2026-11-19 重跑）：
黄金腿 ★1 由 V6-B（夏普 0.74 / 回撤 −16.12%）换成 **V5.1**（夏普 1.02 / 回撤 −12.33%）。
`research/beat_best/_beat_best_top3.md` 里九个「前三名之上的组合/叠加」候选
**无一通过双闸门**（最好的 MIN2 0.98 < 基线 1.04），所以 v20 就是 ★1 本身。

规则（`v5_gold_score/factors.py` + `score.py`，原生状态机）：
1. `scale = clip(10% / 120 日实现波动, 20%, 1)` —— 风险预算（不是 alpha，是风险归一化）；
2. `gate = 1.0 / 0.5` —— 实际利率（FRED `DFII10`）**水平**闸门，`0.5% ± 0.15pp` 死区；
3. `e* = 量化(clip(scale × gate, 0, 1), 20%)` —— 六档 0/20/40/60/80/100%；
4. 跨 1 档（20pp）才动；T 日收盘算信号 → **T+1 收盘成交**。

与 v20 旧口径（V6-B 纯波动层）的区别只有一条：多了**实际利率水平**这一维 ——
闸门不开（实际利率过低，未来更可能上行）时只拿一半风险预算。两者共用同一风险机制，
**不可叠加在同一份额度上**。

`weight(t)` = t 日收盘成交后的仓位（v5 的 `state` 已含 T+1 成交滞后），
`run()` 内 `shift(1)` → 与研究/回测同口径。
成本：研究口径统一单边 10bp（V5 自己的产物用场内万 2.5）；空仓货基 1.40%。
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

LEG = "gold"
LABEL = "v20 黄金 · V5.1 黄金仓位（风险预算 × 利率闸门）"
STATUS = "当前 ★1（研究确认无提升）"
RULE_TXT = ("风险预算 scale=clip(10%/120 日波动, 20%, 1) × 实际利率闸门 gate(DFII10 水平 0.5%±0.15pp) "
            "→ 量化到 20pp 六档；跨 1 档才动，T 日收盘信号 → T+1 收盘成交")
PARAMS = {"VOL_TARGET": 0.10, "VOL_WINDOW": 120, "STEP": 0.20, "HYST": 1}

def load(leg=LEG):
    """该腿信号价：真实指数收盘（黄金 = 518880 场内 ETF）。"""
    return bridge.load(leg)


def detail(px=None):
    """V5.1 全因子表（state / target / action / vol / scale / gate / rate_level）。

    `px` 只用于确定窗口末端，状态机本身在 `_idxcache` 的全历史上跑。
    """
    return bridge.v5_detail(px if px is not None else load(), LEG)


def weight(px):
    """t 日收盘成交后的仓位 ∈ [0,1]（v5 `state` 已含 T+1 成交滞后）。"""
    px = px.astype("float64")
    return bridge.v5(px, LEG)


def run(px, sig=None, cash=None, fee=None):
    s = weight(px) if sig is None else sig
    return bridge.run(px, s, cash=cash, fee=fee)


def perf(nav):
    return bridge.perf(nav)
