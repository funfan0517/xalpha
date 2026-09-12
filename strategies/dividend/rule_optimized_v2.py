# -*- coding: utf-8 -*-
r"""中证红利ETF 投资策略 · 优化版V2规则定义

基于近十年年度数据分析和原始回测表现的平衡优化方案。
优化原则：
1. 保持策略有效性，不过度严格
2. 适应低利率环境，适度调整阈值
3. 保持合理的持仓比例
4. 基于原始策略中表现最好的股息率策略进行优化
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

OUT_BT_JSONL = os.path.join(BACKTEST_DIR, "_dividend_bt_optimized_v2.jsonl")
OUT_BT_REPORT = os.path.join(BACKTEST_DIR, "_bt_report_optimized_v2.md")

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
# 优化后的阈值V2（更平衡的方案）
# ----------------------------------------------------------------------
# 原始阈值：RATIO_BUY=2.5, RATIO_SELL=1.5
# 优化分析：股债收益比整体上升，但原始策略中ratio策略表现一般
# 调整：适度提高，但不过度严格
RATIO_BUY, RATIO_SELL = 2.6, 1.8        # 股债收益比（原：2.5, 1.5）

# 原始阈值：PE_BUY=0.30, PE_SELL=0.70
# 优化分析：PE分位策略表现良好，微调以保持有效性
PE_BUY, PE_SELL = 0.28, 0.72            # 中证红利 PE 分位(10年)（原：0.30, 0.70）

# 原始阈值：DY_BUY=4.5, DY_SELL=3.5
# 优化分析：股息率策略表现最好，适度提高阈值以适应当前环境
DY_BUY, DY_SELL = 4.6, 3.8              # 股息率 % (TTM)（原：4.5, 3.5）
DY_FLOOR = 3.2                          # 股息率底线（原：3.0）

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
# 阈值对比函数（用于报告）
# ----------------------------------------------------------------------
def get_threshold_comparison():
    """返回原始阈值和优化阈值的对比。"""
    return {
        "original": {
            "ratio_buy": 2.5,
            "ratio_sell": 1.5,
            "pe_buy": 0.30,
            "pe_sell": 0.70,
            "dy_buy": 4.5,
            "dy_sell": 3.5,
            "dy_floor": 3.0
        },
        "optimized_v2": {
            "ratio_buy": RATIO_BUY,
            "ratio_sell": RATIO_SELL,
            "pe_buy": PE_BUY,
            "pe_sell": PE_SELL,
            "dy_buy": DY_BUY,
            "dy_sell": DY_SELL,
            "dy_floor": DY_FLOOR
        },
        "changes": {
            "ratio_buy": f"+{(RATIO_BUY - 2.5) / 2.5 * 100:.1f}%",
            "ratio_sell": f"+{(RATIO_SELL - 1.5) / 1.5 * 100:.1f}%",
            "pe_buy": f"-{(0.30 - PE_BUY) / 0.30 * 100:.1f}%",
            "pe_sell": f"+{(PE_SELL - 0.70) / 0.70 * 100:.1f}%",
            "dy_buy": f"+{(DY_BUY - 4.5) / 4.5 * 100:.1f}%",
            "dy_sell": f"+{(DY_SELL - 3.5) / 3.5 * 100:.1f}%",
            "dy_floor": f"+{(DY_FLOOR - 3.0) / 3.0 * 100:.1f}%"
        }
    }