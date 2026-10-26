#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""V8 · RSI12 单指标红利策略 —— 参数权威源（`RULES.md` 是文字版）

与 V4 的唯一差别
----------------
V4 = **14 日 + 70/50**（Wilder 原教旨：14 日、70 超买）；
V8 = **12 日 + 80/20**（A 股科普/投顾文章里最常见的那组，见 `../v4_rsi/rule.py` 的引文）。

两版共用**同一个 5 只择时池**（`../pool.py::TIMING_POOL`）、同样的横截面 top2、
同样的成本与执行口径，所以两版之间的差异可以干净地归因到「窗口」与「清仓线」两个变量。

80/20 里的 20 怎么落地
----------------------
20 是「超卖 → 加仓」线。本策略是**单向降暴露**：仓位上限 100%，无杠杆、不融资。
RSI < 20 时本来就已是满仓，「加仓」没有可执行含义 —— 所以 20 不进入仓位规则。

中间档沿用 V4 的中轴 **50** 做「减半」，理由有两条：

1. 与 V4 同构（只差窗口与清仓线），两版对比才有意义；
2. 12 日 RSI 波动更快，若真把 20 当减半线（`Q_HALF_LITERAL`），会退化成「常年半仓」——
   `backtest.py` 的对照表把这一行也跑出来，用数据说明它为什么不合理，而不是凭感觉跳过。

已知失效模式
------------
与 V4 同源：**强单边趋势里 RSI 钝化**。窗口越短钝化越轻、但噪声越大 ——
12 日比 14 日更容易在震荡里反复跨线，所以换手次数（进而成本）会更高，这是本版的结构性代价。
"""

from __future__ import annotations

import os
import sys

_DIR = os.path.dirname(os.path.abspath(__file__))
_STRAT = os.path.dirname(_DIR)
if _STRAT not in sys.path:
    sys.path.insert(0, _STRAT)
ROOT = os.path.dirname(os.path.dirname(os.path.dirname(_DIR)))

import pool  # noqa: E402  pylint: disable=wrong-import-position

SIGNAL = "rsi"
TITLE = "V8 · RSI12 单指标红利策略（12 日 + 80/20 口径）"
TOP_K = 2
WINDOW = 12
Q_OUT, Q_HALF = 0.80, 0.50          # RSI 80 清仓 / RSI 50 减半
Q_HALF_LITERAL = 0.20               # 80/20 的字面落地（仅回测对照，不进实盘）

# 与 V4 同一个池（单一定义在 pool.py::TIMING_POOL）
POOL = pool.TIMING_POOL
CODES = [p["code"] for p in POOL]
NAME = {p["code"]: p["name"] for p in POOL}
BASE_LABEL = f"基准：{len(CODES)} 只等权"

UNIT = ""


def unit_fmt(v) -> str:
    return f"{v:.1f}"


SUBTITLE = [
    "指标：Wilder RSI12（基金累计净值）→ 贵度 q = RSI / 100（保留绝对刻度，不做分位化）",
    f"样本 2020-01 ~ 2026-10（82 月）· {len(CODES)} 只基金（与 V4 同一个择时池）"
    " · 月末信号下月生效 · 成本单边 0.03%",
    "规则：板块 RSI12 均值 ≥80 清仓 / ≥50 减半；横截面选 RSI 最低 2 只等权",
    "口径来源：A 股科普/投顾最常见的「12 日 + 80/20」；20 是超卖加仓线，无杠杆下不可执行，故不入规则",
]

OUT_MD = os.path.join(_DIR, "backtest", "_v8_report.md")

NOTE = ("V8 是 V4 的**参数孪生**：同池、同横截面、同成本，只把「14 日 + 70/50」换成「12 日 + 80/20」。"
        "它存在的意义是回答「换个文献口径会不会更好」，不是另起一套逻辑 —— "
        "若两版 Calmar 差距在噪声内，说明 RSI 择时对参数不敏感，结论可外推；"
        "若差距显著，要检查是窗口还是清仓线在起作用（backtest.py 的对照表已拆开）。")
