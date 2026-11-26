# -*- coding: utf-8 -*-
"""v20 · 黄金 · `(V5 + V12)/2 × 波动上限 TV15%` —— ★ 研究超越项。

来源：`strategies/vol_legs/research/beat_best/_beat_best_top3.md`（2026-11-26 重跑，
候选池加入 TV15 / TV10 两档之后）。黄金腿 ★1 仍是 `v5_gold_score`（V5，波动窗口 90 日），
但「E2 = 与 V12 等权融合 + 波动上限」在 2026-11-26 首次过了双闸门。

**2026-11-26 用户决定：黄金腿采用 `E2×TV15`（不取研究最优的 TV10）** ——
TV10 的换手从 5.3 涨到 57.9 次/年、且同一技法在 XAU 19.5 年上反向（夏普 0.79→0.72）；
TV15 是折中档：全历史同尺 1.0x（后段 1.6x）、近五年 1.74（后段 2.03）。

规则（逐日权重全部来自原生引擎，经 `_top3_bridge.py` 隔离加载，不重写规则）：
1. **V5**：`scale = clip(10% / 90 日实现波动, 20%, 1)` × 实际利率闸门（`DFII10` 水平
   `0.5% ± 0.15pp`）→ 量化到 20pp 六档 —— `v5_gold_score` 原生状态机；
2. **V12**：八态趋势的许可上限仓位（`v12_octo_trend` 原生引擎，黄金腿 TV10 + 死区）；
3. **E2** = 两条腿等权平均 —— 跨信号族分散（风险预算 × 趋势状态机）；
4. **cap** = `min(1, 15% / σ20)`，带 5 日冷却与 5pp 死区（复用 v2/v3 的 `vol_cap`）；
5. 仓位 = `E2 × cap`；T 日收盘算信号 → **T+1 收盘成交**。

`weight(t)` = t 日收盘决策（未 shift），`run()` 内 `shift(1)` → T+1 收益生效。
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
LABEL = "v20 黄金 · (V5 + V12)/2 × 波动上限 TV15%"
STATUS = "★ 研究超越项（E2×TV15）"
RULE_TXT = ("(V5 黄金仓位 + V12 八态趋势) 等权 × 波动上限 TV15%（σ20，5 日冷却 / 5pp 死区）；"
            "T 日收盘信号 → T+1 收盘成交")
PARAMS = {"VOL_TARGET": 0.10, "VOL_WINDOW": 90, "TV": 0.15, "TV_WIN": 20, "BLEND": "E2"}


def load(leg=LEG):
    """该腿信号价：真实指数收盘（黄金 = 518880 场内 ETF）。"""
    return bridge.load(leg)


def detail(px=None):
    """V5 全因子表（state / target / action / vol / scale / gate / rate_level）。

    `px` 只用于确定窗口末端，状态机本身在 `_idxcache` 的全历史上跑。
    """
    return bridge.v5_detail(px if px is not None else load(), LEG)


def parts(px):
    """E2×TV 的三个部件（决策口径，未 shift），供日报解释用。"""
    px = px.astype("float64")
    a = bridge.v5(px, LEG)
    b = bridge.v12(px, LEG)
    e2 = ((a + b) / 2.0).reindex(px.index).fillna(0.0)
    cap = bridge.cap_for_decision(px, tv=float(PARAMS["TV"]))
    return dict(v5=a.reindex(px.index).fillna(0.0), v12=b.reindex(px.index).fillna(0.0),
                e2=e2, cap=cap.reindex(px.index).fillna(0.0))


def weight(px):
    """t 日收盘决策仓位 ∈ [0,1]：`E2 × cap`（cap 已按决策口径左移一格）。"""
    d = parts(px)
    return (d["e2"] * d["cap"]).reindex(px.index).fillna(0.0).clip(0.0, 1.0)


def run(px, sig=None, cash=None, fee=None):
    s = weight(px) if sig is None else sig
    return bridge.run(px, s, cash=cash, fee=fee)


def perf(nav):
    return bridge.perf(nav)
