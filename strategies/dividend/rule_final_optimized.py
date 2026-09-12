# -*- coding: utf-8 -*-
r"""中证红利ETF 投资策略 · 最终优化版规则定义

基于全面分析的最终优化方案：
1. 考虑年度数据分析结果
2. 考虑原始回测表现
3. 平衡收益与风险
4. 适应当前市场环境
"""
import os
from datetime import datetime

import numpy as np
import pandas as pd

# ----------------------------------------------------------------------
# 路径（与原始规则保持一致）
# ----------------------------------------------------------------------
_DIR = os.path.dirname(os.path.abspath(__file__))          # strategies/dividend
_ROOT = os.path.dirname(os.path.dirname(_DIR))             # 仓库根, 推导
CACHE_DIR = os.path.join(_ROOT, "data", "_bt_caches")
BACKTEST_DIR = os.path.join(_DIR, "backtest")
DAILY_DIR = os.path.join(_DIR, "daily")
DATA_DIR = os.path.join(_DIR, "data")

OUT_BT_JSONL = os.path.join(BACKTEST_DIR, "_dividend_bt_final_optimized.jsonl")
OUT_BT_REPORT = os.path.join(BACKTEST_DIR, "_bt_report_final_optimized.md")

# ----------------------------------------------------------------------
# 标的定义（保持不变）
# ----------------------------------------------------------------------
ASSET = dict(
    name="中证红利",
    off_code="012644", off_name="招商中证红利ETF联接C",
    inner_code="515080", inner_name="中证红利ETF招商",
    price_index="000922", total_index="H00922",
    style="防御",
)

# ----------------------------------------------------------------------
# 最终优化方案
# ----------------------------------------------------------------------
# 基于全面分析的综合优化：
# 1. 股债收益比：适度提高，适应低利率环境
# 2. PE分位：微调，保持有效性
# 3. 股息率：适度提高，反映分红能力增强

# 股债收益比阈值
# 原始：2.5/1.5，优化：2.7/1.7（提高13%/13%）
RATIO_BUY, RATIO_SELL = 2.7, 1.7        # 股债收益比

# PE分位阈值
# 原始：30%/70%，优化：28%/72%（微调）
PE_BUY, PE_SELL = 0.28, 0.72            # 中证红利 PE 分位(10年)

# 股息率阈值
# 原始：4.5%/3.5%，优化：4.7%/3.7%（提高4%/6%）
DY_BUY, DY_SELL = 4.7, 3.7              # 股息率 % (TTM)
DY_FLOOR = 3.2                          # 股息率底线

# 指标构建口径（保持不变）
PE_WINDOW_YEARS = 10                    # PE 分位窗口: 过去最多 10 年
PE_WINDOW_DAYS = int(PE_WINDOW_YEARS * 244)
DY_LOOKBACK = 252                       # 股息率 TTM 代理: 比值近 252 交易日增长

# 成本与样本（保持不变）
FEE = 0.0003                            # 单边 0.03%(场内 ETF 佣金)
TRADE_AT = "信号次日执行"
SAMPLE_FROM = "2016-09-01"              # 与仓库其他策略一致的样本起点
START = "2015-06-01"                    # 缓存起点(留 warm-up); 缓存已存在则忽略

# ----------------------------------------------------------------------
# 指标 -> 三档分区（"buy" / "hold" / "sell"）
# ----------------------------------------------------------------------
IND_KEYS = ("ratio", "pe_pct", "dy")
IND_NAMES = {
    "ratio": "股债收益比",
    "pe_pct": "中证红利PE分位(10年)",
    "dy": "股息率(TTM)",
}
IND_UNIT = {"ratio": "", "pe_pct": "%", "dy": "%"}


def _nan(v):
    return v is None or (isinstance(v, float) and np.isnan(v))


def zone_ratio(v):
    if _nan(v):
        return None
    if v > RATIO_BUY:
        return "buy"
    if v >= RATIO_SELL:
        return "hold"
    return "sell"


def zone_pe_pct(v):
    if _nan(v):
        return None
    if v < PE_BUY:
        return "buy"
    if v <= PE_SELL:
        return "hold"
    return "sell"


def zone_dy(v):
    if _nan(v):
        return None
    if v > DY_BUY:
        return "buy"
    if v >= DY_SELL:
        return "hold"
    return "sell"


ZONE_FN = {"ratio": zone_ratio, "pe_pct": zone_pe_pct, "dy": zone_dy}
# 阈值展示用（买入 / 持有 / 卖出 的文本口径, 供报告与 pipeline 镜像）
ZONE_TEXT = {
    "ratio": (f">{RATIO_BUY:g}", f"{RATIO_SELL:g}–{RATIO_BUY:g}", f"<{RATIO_SELL:g}"),
    "pe_pct": (f"<{PE_BUY:.0%}", f"{PE_BUY:.0%}–{PE_SELL:.0%}", f">{PE_SELL:.0%}"),
    "dy": (f">{DY_BUY:g}%", f"{DY_SELL:g}–{DY_BUY:g}%", f"<{DY_SELL:g}%"),
}
ZONE_CN = {"buy": "买入", "hold": "持有", "sell": "卖出/停加"}

# 分区 -> 目标仓位。二元口径(买入/持有都满仓)是「哪个指标更好」的主判据;
# 分档口径(持有半仓)作为稳健性对照, 见 backtest.py 的 MODES。
WEIGHT_BINARY = {"buy": 1.0, "hold": 1.0, "sell": 0.0}
WEIGHT_GRADED = {"buy": 1.0, "hold": 0.5, "sell": 0.0}
MODES = {"binary": WEIGHT_BINARY, "graded": WEIGHT_GRADED}

# ----------------------------------------------------------------------
# 组合规则：三指标的合取（保持不变）
# ----------------------------------------------------------------------
COMPOSE = "all"      # "all" = 任一卖出即空仓(保守, 本策略采纳); "vote" = 多数非卖出


def compose_zone(zones):
    """{指标: zone} -> 合成 zone。

    "all" (手册「矛盾留更防御一档」的单标的版): 任一 sell -> sell; 全 buy -> buy; 否则 hold。
    "vote": 非 sell 的个数占多数 -> hold(或全 buy 则 buy), 否则 sell。
    """
    vals = [z for z in zones.values() if z is not None]
    if not vals:
        return None
    if COMPOSE == "vote":
        n_buy = sum(z == "buy" for z in vals)
        n_sell = sum(z == "sell" for z in vals)
        if n_sell > len(vals) - n_sell:
            return "sell"
        if n_buy == len(vals):
            return "buy"
        return "hold"
    if "sell" in vals:
        return "sell"
    if all(z == "buy" for z in vals):
        return "buy"
    return "hold"


# ----------------------------------------------------------------------
# 优化总结函数
# ----------------------------------------------------------------------
def get_optimization_summary():
    """返回优化方案总结。"""
    return {
        "optimization_basis": [
            "基于近十年年度数据分析",
            "考虑原始回测表现（股息率策略最佳）",
            "适应低利率环境和股息率中枢上移",
            "平衡收益与风险"
        ],
        "threshold_changes": {
            "ratio": {
                "original": "买入>2.5, 卖出<1.5",
                "optimized": f"买入>{RATIO_BUY}, 卖出<{RATIO_SELL}",
                "change": f"买入提高{(RATIO_BUY - 2.5) / 2.5 * 100:.1f}%, 卖出提高{(RATIO_SELL - 1.5) / 1.5 * 100:.1f}%",
                "reason": "适应低利率环境，股债收益比整体上升"
            },
            "pe_pct": {
                "original": "买入<30%, 卖出>70%",
                "optimized": f"买入<{PE_BUY:.0%}, 卖出>{PE_SELL:.0%}",
                "change": f"买入降低{(0.30 - PE_BUY) / 0.30 * 100:.1f}%, 卖出提高{(PE_SELL - 0.70) / 0.70 * 100:.1f}%",
                "reason": "微调以保持策略有效性"
            },
            "dy": {
                "original": "买入>4.5%, 卖出<3.5%",
                "optimized": f"买入>{DY_BUY}%, 卖出<{DY_SELL}%",
                "change": f"买入提高{(DY_BUY - 4.5) / 4.5 * 100:.1f}%, 卖出提高{(DY_SELL - 3.5) / 3.5 * 100:.1f}%",
                "reason": "反映分红能力增强，股息率中枢上移"
            }
        },
        "expected_effects": [
            "更好的适应当前市场环境",
            "保持策略的防御特性",
            "适度提高买入门槛，增强风险控制",
            "基于历史数据优化，提高策略稳健性"
        ]
    }