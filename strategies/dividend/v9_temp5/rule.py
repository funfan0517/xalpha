#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""V9 · 红利五因子估值温度 —— 参数权威源（`RULES.md` 是文字版，本文件是代码版）

    温度 = 35%·股债收益差 + 20%·PE + 16%·PB + 15%·年线乖离 + 14%·价格位置（各取近 2 年日频分位）

口径（生产用单一路径 `etfrun`；`csindex` 仅 backtest 对照复现 v1.0 恒等式）：

1. **股债收益差 = 股息率 − 10Y**（`SPREAD_KIND = "dy"`）：股息率取 `data/_div_yield_m.csv`
   月度序列（000922 / H30269 真实，其余 4 只按 000922 变化率代理）；10Y 取中债日频。
2. **PE / PB = etf.run 等权日频真实值**（`equalWeightedPeTtm` / `equalWeightedPbTtm`），
   全池 6 只覆盖，落 `data/_etfrun_eva.csv`。**不是**「中证加权 PE + `PE × ROE` 合成 PB」。
   ⚠ 等权 ≠ 加权：同一天 000922 等权 PE 12.04 vs 中证加权 9.23（差 30%），
   不要与外部「按加权 PE 分位」的说法直接对照。
3. **权重原样保留** 35/20/16/15/14（外部规则 R0-1，禁改）。

方向：分位一律折算成「越高越热」。股债收益差反号（利差宽 = 便宜 = 低分位 = 冷）。
"""
from __future__ import annotations

import os

_DIR = os.path.dirname(os.path.abspath(__file__))
_STRAT = os.path.dirname(_DIR)                      # strategies/dividend
ROOT = os.path.dirname(os.path.dirname(_STRAT))     # 仓库根（推导，不写死）

SIGNAL = "temp5"
TITLE = "V9 · 红利五因子估值温度（35/20/16/15/14 · 等权 PE + 真实 PB）"

# ------------------------------------------------------------------ 因子
FACTORS = ("spread", "pe", "pb", "dev", "pos")
LABEL = {"spread": "股债收益差", "pe": "PE", "pb": "PB", "dev": "年线乖离", "pos": "价格位置"}

# 外部规则原样权重（R0-1：禁止修改）
W = {"spread": 0.35, "pe": 0.20, "pb": 0.16, "dev": 0.15, "pos": 0.14}
# 对照口径：去掉 PB 后按剩余四维归一（回答「PB 到底提供了什么信息」）
_W4_SUM = sum(v for k, v in W.items() if k != "pb")
W_NO_PB = {k: W[k] / _W4_SUM for k in W if k != "pb"}
# 方向：+1 = 分位越高越热；-1 = 分位越高越冷（利差宽 = 便宜 = 冷）
SIGN = {"spread": -1, "pe": +1, "pb": +1, "dev": +1, "pos": +1}

# 分位窗口：近 2 年（730 日历日）的全部日频观测；最少 300 个观测才给分位
PCT_WINDOW_DAYS = 730
PCT_MIN_N = 300
MA_WIN = 250            # 年线
POS_WIN = 250           # 价格位置（52 周 = 250 根 high/low）

# ------------------------------------------------------------------ 档位（规则 §4.7，禁改阈值）
BANDS = [
    (85, "危险", "大幅减仓"),
    (75, "过热", "分批减仓"),
    (65, "偏热", "可开始部分止盈"),
    (55, "中性偏热", "不再新增，持有观望"),
    (40, "中性", "持有 / 正常定投"),
    (20, "偏冷", "便宜区域，可分批加仓"),
    (0, "冰点", "便宜区域，可分批加仓"),
]

# ------------------------------------------------------------------ 时间维度
# 板块温度用**自身历史分位**（绝对刻度不可移植：四维合成温度的经验区间只有 10~66）。
# 阈值由 backtest.py 的三档敏感性确认（与 V2 同构，便于横比）。
CUT_Q, OUT_Q = 0.80, 0.90
Q_MIN_N = 24            # 分位最少样本（月）
# 对照：直接拿规则 §4.7 的绝对档当择时（≥65 减半 / ≥75 清仓），回测后由数据判定
ABS_CUT, ABS_OUT = 65.0, 75.0

# 横截面 topK（只在有估值口径的标的里选，† 标的价格温度不参与）
TOP_K = 2
# 两个维度是否参与每日推荐 —— 由 `backtest/_v9_report.md` 的结论决定，不凭印象开
XS_ENABLED = False      # 横截面选品
TIME_ENABLED = False    # 时间维度降暴露

# ------------------------------------------------------------------ 标的池
# 6 只基金 = 6 个跟踪指数（来源 `../pool.py::POOL`）。
# **入选条件：必须有 etf.run 等权日频 PE/PB 全历史** —— 全池同一估值口径，温度才可横比。
# 2026-10-29 剔出 4 只：007760(931157)、018388(930914) 无等权 PE/PB；
# 008163(标普红利低波50)、005125(标普红利机会) 无可用的估值历史（只剩价格温度，与其余不可比）。
# 要加回标的，**先确认它也有等权估值**，否则会重新引入混合口径。
FUNDS = [
    # fund, 短名, 指数键, 跟踪指数全称
    dict(code="008115", short="红利低波100", index="930955", index_name="中证红利低波动100"),
    dict(code="007467", short="红利低波", index="H30269", index_name="中证红利低波动"),
    dict(code="090010", short="中证红利", index="000922", index_name="中证红利"),
    dict(code="016441", short="红利质量", index="931468", index_name="中证红利质量"),
    dict(code="561060", short="国企红利", index="000824", index_name="中证国有企业红利"),
    dict(code="561580", short="央企红利", index="000825", index_name="中证中央企业红利"),
]
CODES = [f["code"] for f in FUNDS]
NAME = {f["code"]: f["short"] for f in FUNDS}
BY_CODE = {f["code"]: f for f in FUNDS}
VALUED = CODES                    # 全池都有估值口径（入选条件）
INDEX_OF = {f["code"]: f["index"] for f in FUNDS}

BASE_LABEL = f"基准：{len(CODES)} 只等权"
UNIT = ""


def unit_fmt(v) -> str:
    return f"{v:.1f}"


def band_of(temp: float):
    """温度 -> (状态, 动作)。"""
    for lo, name, action in BANDS:
        if temp >= lo:
            return name, action
    return BANDS[-1][1], BANDS[-1][2]


# ------------------------------------------------------------------ 路径
DATA_DIR = os.path.join(_DIR, "data")
IDX_DIR = os.path.join(DATA_DIR, "_idx")            # 中证指数日频（close/high/low）
BT_DIR = os.path.join(_DIR, "backtest")
DAILY_DIR = os.path.join(_DIR, "daily")

BOND_CN = os.path.join(ROOT, "data", "_bt_caches", "bond10y_d.csv")      # 日频（本策略生产）
BOND_CN_M = os.path.join(ROOT, "data", "_bt_caches", "bond10y_m.csv")    # 月频（历史兜底）
BOND_US_M = os.path.join(ROOT, "data", "_bt_caches", "ust10y_m.csv")     # 月频
DY_CSV = os.path.join(ROOT, "data", "_div_yield_m.csv")                  # 指数股息率月度历史（跨策略数据类）

# 蛋卷 index_eva：指数 PE/PB 周频历史（免 token，跨策略数据类）。**只覆盖 3 只**：
#   000922 -> SH000922 · H30269 -> CSIH30269 · 931157 -> CSI931157
# 其中 931157 已于 2026-10-29 移出 V9 池，仅保留给 `research/_pb_real_check.py` 做口径对照。
DJ_CSV = os.path.join(ROOT, "data", "_danjuan_eva.csv")
DJ_CODE = {"000922": "SH000922", "H30269": "CSIH30269", "931157": "CSI931157"}

# etf.run：指数 **日频** PE/PB（**等权**口径）历史，免 token，跨策略数据类。
# 这是 V9 **唯一**的估值来源，全池 6 只全部覆盖：
#   000922(2005→) · H30269(2005→) · 000824(2015→) · 000825(2015→) · 930955(2020→) · 931468(2021→)
ETF_RUN_CSV = os.path.join(ROOT, "data", "_etfrun_eva.csv")
ETF_RUN_CODE = {
    "000922": "CSI/000922", "H30269": "CSI/H30269", "930955": "CSI/930955",
    "931468": "CSI/931468", "000824": "CSI/000824", "000825": "CSI/000825",
}

# 股债收益差口径："dy" = 股息率 − 10Y（红利专用，优先）；"ey" = 100/PE − 10Y（无股息率时的兜底）
SPREAD_KIND = "dy"

# 估值口径（单一主路径）："etfrun" = etf.run 等权真实 PE/PB（默认、唯一可用）；
# "csindex" 是文档里 v1.0 的对照概念（PB=PE×ROE 合成），当前 factors 已不支持该口径。
VALUATION_SRC = "etfrun"

OUT_REPORT = os.path.join(BT_DIR, "_v9_report.md")
OUT_PANEL = os.path.join(BT_DIR, "_v9_panel.jsonl")
OUT_BT = os.path.join(BT_DIR, "_v9_bt.jsonl")
OUT_BT_SUM = os.path.join(BT_DIR, "_v9_bt_summary.json")   # 供 daily 引用，避免手抄数字
OUT_MD = os.path.join(DAILY_DIR, "_v9_signal.md")
OUT_JSON = os.path.join(DAILY_DIR, "_v9_signal.json")
OUT_HTML = os.path.join(DAILY_DIR, "_v9_temp.html")

SUBTITLE = [
    "温度 = 35%·股债收益差 + 20%·PE + 16%·PB + 15%·年线乖离 + 14%·价格位置（各取近 2 年分位）",
    "股债收益差 = 股息率 − 10Y 国债（红利专用）",
    "PE / PB 取 etf.run 等权日频（全池 6 只同源，无回退口径）",
    "档位阈值固定：<20 冰点 / 20–40 偏冷 / 40–55 中性 / 55–65 中性偏热 / 65–75 偏热 / 75–85 过热 / ≥85 危险",
    f"池 {len(CODES)} 只，全部有等权 PE/PB 日频历史",
]

NOTE = ("V9 是外部《红利族基金估值温度》规则在 xalpha 池上的落地：五因子原版权重、"
        "股息率−10Y 利差、etf.run 等权日频真实 PE/PB（非合成的 PB）。"
        "它验证过两件事：①估值换成真实 PB 后，16% 的 PB 权重才不再与 PE 重复；"
        "②绝对档位（20/40/55/65/75/85）能不能当择时阈值（结论：不能照抄）。")
