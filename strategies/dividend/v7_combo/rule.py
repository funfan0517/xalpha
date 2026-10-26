#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""V7 · 多指标组合策略 —— 候选定义与参数权威源（`RULES.md` 是文字版）

组合原则（先定规矩，再看数据）
------------------------------
1. **不搜索权重**。四个指标的权重只取「等权」这一种，避免在 82 个月、7 只标的上
   拟合出一组看起来最优的权重。要区分优劣，靠的是「信息是否重复」，不是权重微调。
2. **按信息独立性分组**：V5/V6 与 V3 的横截面排序**完全等价**（y10 是共同项），
   所以「dy+spread+ratio 等权」实际只是把股息率复制了三遍。
   因此候选里必须包含「估值 1 票 + RSI 1 票」的两维版本，否则组合会被估值类淹没。
3. **阈值沿用单指标口径**：估值类 0.80/0.50，RSI 0.70/0.50（见 `v4_rsi/RULES.md`）。

候选
----
| 代号 | 构成 | 说明 |
|---|---|---|
| `all4` | dy + spread + ratio + rsi 等权 | 名义四维，实际 75% 权重给了同一个估值信号 |
| `val` | dy + spread + ratio 等权 | 三胞胎，≈ 单用股息率 |
| `dy_rsi` | dy + rsi 各 50% | **两个独立信息源**，理论上最合理 |
| `rsi` | 仅 rsi | 单指标对照（= V4） |
| `vote` | 4 个指标里 ≥2 个认为「便宜」 | 非加权，用票数，抗单指标噪声 |

`q`（时间维度）与 `xs`（横截面）的合成方式不同：
* `q`：各指标自身分位贵度的**均值**（都已在 0–1 且方向一致）；
* `xs`：各指标**当月横截面排名**（0=最便宜）的均值 —— 不能直接平均原始值，
  股息率是 4.x%、RSI 是 60，量纲不同，必须先在每月内做横截面标准化。
"""

from __future__ import annotations

import os

_DIR = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(os.path.dirname(os.path.dirname(_DIR)))

CANDIDATES = {
    "all4（4 维等权）": ["dy", "spread", "ratio", "rsi"],
    "val（估值三胞胎）": ["dy", "spread", "ratio"],
    "dy_rsi（估值+RSI 各半）": ["dy", "rsi"],
    "rsi（单指标对照）": ["rsi"],
}
VOTE_THRESHOLD = 0.50      # q < 0.50 记为「该指标认为便宜」
VOTE_MIN = 2               # ≥2 票才算便宜

# 分工组合：(名称, 时间维度指标, 横截面指标, (清仓线, 减半线))
# 与「把两个指标平均」不同：这里两个维度各用自己最擅长的指标，互不稀释。
SPECIAL = (
    ("mix｜RSI 择时 + 股息率选品", "rsi", "dy", (0.70, 0.50)),
    ("mix｜股息率择时 + RSI 选品", "dy", "rsi", (0.80, 0.50)),
)

TOP_K = 2
Q_OUT, Q_HALF = 0.80, 0.50

OUT_MD = os.path.join(_DIR, "backtest", "_v7_report.md")
