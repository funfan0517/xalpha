#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""红利估值温度 v2 · 参数权威源（`RULES.md` 是文字版，本文件是代码版）

与图片版「五维估值温度」的关系
------------------------------
图片口径::

    温度 = 35%·股债收益差 + 20%·PE + 16%·PB + 15%·年线乖离 + 14%·价格位置
           （各取 10 年分位，0 = 冰点，100 = 过热）

v2 改三处，每处都有可验证的理由（见 `backtest/_v2_report.md`）：

1. **股债收益差反号**。利差宽 = 便宜 = 冰点，原式把它正向计入 35% 权重，
   等于「越便宜越热」。⚠ 诚实说明：在 2020-2026 样本上把符号改对，平均 IC 只从
   -0.46 变到 -0.50（利差分位长期贴顶、方差太小），所以改它的理由是**语义不能反**，
   不是「因此更准」。
2. **PB 不参与**。中证官网 `index-perf` 只给 PE(`peg`)，无 PB 历史；
   东财 MCP 只有当前快照与年内分位，无法回测也无法归因。
   故 16% 权重按剩余四维归一 —— 不编造 PB 历史。
3. **等权为主口径**。35/20/16/15/14 无出处且不可复核（其中一维不可得），
   v2 主口径四维各 25%，原权重（去掉 PB 后归一）只作对照。

因此 v2 的原始温度与图片数字**不可直接比大小**，只可对照「谁更热」的排序；
`backtest.py` 会同时给出图片口径的复算值，以便看清差异来自哪里。
"""

from __future__ import annotations

import os

_DIR = os.path.dirname(os.path.abspath(__file__))

# ------------------------------------------------------------------ 标的
# 图内 4 只指数。931446（东证红利低波）无股息率历史，用中证红利变化率代理 +
# 官网快照(4.14%)校准，method 标 "proxy"。
INDICES = {
    "000922": "中证红利",
    "931446": "东证红利低波",
    "930955": "红利低波100",
    "H30269": "红利低波",
}

# 无 _div_yield_m.csv 序列时的代理校准水平（中证官网指数估值·股息率1，2026-10-23）
DY_SNAPSHOT = {
    "000922": 4.21,
    "931446": 4.14,
    "930955": 4.32,
    "H30269": 4.22,
}
BASE_INDEX = "000922"          # 代理基准（与 v1_spread/yielddata.py 同口径）
TAX = 1.0                      # A 股：持股满一年分红免个税

# ------------------------------------------------------------------ 因子
PCT_MIN_N = 36                 # 分位最少样本（月）
PCT_MAX_N = 120                # 分位窗口上限（月）= 10 年
MA_WIN = 250                   # 年线（交易日）
POS_WIN = 756                  # 价格位置的回看区间（3 年交易日）

# 方向：+1 = 分位越高越「热」（贵）；-1 = 分位越高越「冷」（便宜）
SIGN = {
    "spread": -1,              # 利差宽 = 便宜 → 反号（这是 v2 相对图片的关键修正）
    "pe": +1,
    "ma_dev": +1,
    "pos": +1,
}

# 权重（各自归一前）—— 图片口径，保留用于对照
W_IMAGE = {"spread": 0.35, "pe": 0.20, "pb": 0.16, "ma_dev": 0.15, "pos": 0.14}
W_IMAGE_NO_PB = {k: v / sum(v for k, v in W_IMAGE.items() if k != "pb")
                 for k, v in W_IMAGE.items() if k != "pb"}
# 主口径：等权（PB 不可得，四维各 25%）
W_EQ = {k: 1 / 4 for k in ("spread", "pe", "ma_dev", "pos")}

PB_AVAILABLE = False           # 无 PB 历史序列（只有当前快照，见 README/PB 缺口）

# ------------------------------------------------------------------ 档位
# 温度 → 档位（读数卡展示用）。注意：四维分位合成的温度**经验分布不是 0~100 均匀**，
# 实测区间只有 10~66，所以绝对档位只能当标签，不能当交易阈值。
BANDS = [
    (60, "过热", "清至底仓/不加"),
    (50, "偏贵", "不加仓，可部分止盈"),
    (35, "中性", "持有"),
    (25, "偏冷", "可加"),
    (0, "冰点", "加至目标"),
]

# 时间维度用**温度自身的历史分位**（扩张窗口，最少 24 个月）—— 绝对刻度不可移植。
# 板块温度分位 ≥CUT_Q → 减半；≥OUT_Q → 清仓。
CUT_Q, OUT_Q = 0.80, 0.90
Q_MIN_N = 24

# ------------------------------------------------------------------ 路径
ROOT = os.path.dirname(os.path.dirname(os.path.dirname(_DIR)))
DATA_DIR = os.path.join(_DIR, "data")
BT_DIR = os.path.join(_DIR, "backtest")
DAILY_DIR = os.path.join(_DIR, "daily")
YIELD_CSV = os.path.join(ROOT, "data", "_div_yield_m.csv")
Y10_CSV = os.path.join(ROOT, "data", "_bt_caches", "bond10y_m.csv")
OUT_REPORT = os.path.join(BT_DIR, "_v2_report.md")
OUT_PANEL = os.path.join(BT_DIR, "_v2_panel.jsonl")
OUT_READ_MD = os.path.join(DAILY_DIR, "_v2_read.md")
OUT_READ_JSON = os.path.join(DAILY_DIR, "_v2_read.json")
