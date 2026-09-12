# -*- coding: utf-8 -*-
r"""中证红利ETF 投资策略 · 规则化定义 —— 唯一权威源

方法论文档: doc/核心轮动投资策略手册.md §4「中证红利策略（防御底仓）」

定位与 core_rotation 的关系
----------------------------------------------------------------------
core_rotation 把中证红利当作六类资产之一（组合层权重分配）；本策略把 §4 单独
抽出来做**单标的估值择时**：只在中证红利 ETF 上做「持有 / 空仓」的仓位切换。
规则本体完全来自手册 §4.1/§4.2，不改阈值，只把它落地成可回测、可每日出信号的实现。

标的（一类一标的, 取自 data/_universe.md 场外行 #42）
----------------------------------------------------------------------
  场外主仓(执行申赎) 012644  招商中证红利ETF联接C
  场内信号代理       515080  中证红利ETF招商     (只看信号, 不交易)
  跟踪指数           000922  中证红利(价格) · H00922 中证红利(全收益, 用于回测收益)
  回测收益口径       H00922 全收益指数(含分红再投) —— 红利策略必须用全收益, 价格指数会漏掉 ~5%/年的股息

三个候选指标（§4.1/§4.2）—— 本策略的核心问题「哪个指标更好」
----------------------------------------------------------------------
| 指标 | 买入 | 持有 | 卖出/停加 | 历史实现口径 |
|---|---|---|---|---|
| 股债收益比 | > 2.5 | 1.5–2.5 | < 1.5 | 中证红利股息率 ÷ 10Y国债 |
| 中证红利 PE 分位(10年) | < 30% | 30%–70% | > 70% | 中证官网 peg 的过去最多10年累计分位 |
| 股息率(TTM) | > 4.5% | 3.5%–4.5% | < 3.5% | H00922/000922 比值近12月增长(TTM 代理) |
| 成分股暴雷/下调分红 | — | — | 红线 | 人工布尔(无历史序列, 不进回测) |

组合方式（本策略采纳的合取规则，见 COMPOSE）: 三个指标**任一**落在「卖出/停加」区
即退到空仓；三者都非卖出才持有。这是手册「任何维度矛盾 → 留在更防御一档」的单标的版本。

生效参数 = 本文件顶部的常量（阈值直接抄手册 §4.2，不调）；`backtest.py` 用来回答
「哪个指标更好」，daily 用 `_signal.py` 出实时信号。改阈值只改本文件。
"""
import os
from datetime import datetime

import numpy as np
import pandas as pd

# ----------------------------------------------------------------------
# 路径（相对脚本自身；禁止写死盘符, 见 AGENTS.md §8.2）
# ----------------------------------------------------------------------
_DIR = os.path.dirname(os.path.abspath(__file__))          # strategies/dividend
_ROOT = os.path.dirname(os.path.dirname(_DIR))             # 仓库根, 推导
# 共享原始数据缓存（中证官网日频/中债月频）—— 属**数据类**, 按约定留在仓库 data/,
# 与 core_rotation 共用, 不复制一份。
CACHE_DIR = os.path.join(_ROOT, "data", "_bt_caches")
BACKTEST_DIR = os.path.join(_DIR, "backtest")
DAILY_DIR = os.path.join(_DIR, "daily")
DATA_DIR = os.path.join(_DIR, "data")

OUT_BT_JSONL = os.path.join(BACKTEST_DIR, "_dividend_bt.jsonl")
OUT_BT_REPORT = os.path.join(BACKTEST_DIR, "_bt_report.md")
OUT_SIGNAL_MD = os.path.join(DAILY_DIR, "_signal_report.md")
OUT_SIGNAL_JSON = os.path.join(DAILY_DIR, "_dividend_signal.json")
OUT_ACTIVE_MD = os.path.join(DAILY_DIR, "_universe_dividend_active.md")
OUT_ACTIVE_JSON = os.path.join(DAILY_DIR, "_universe_dividend_active.json")

# ----------------------------------------------------------------------
# 标的定义
# ----------------------------------------------------------------------
ASSET = dict(
    name="中证红利",
    off_code="012644", off_name="招商中证红利ETF联接C",
    inner_code="515080", inner_name="中证红利ETF招商",
    price_index="000922", total_index="H00922",
    style="防御",
)

# ----------------------------------------------------------------------
# 手册 §4.2 阈值（本策略的非调参常量）
# ----------------------------------------------------------------------
RATIO_BUY, RATIO_SELL = 2.5, 1.5        # 股债收益比
PE_BUY, PE_SELL = 0.30, 0.70            # 中证红利 PE 分位(10年)
DY_BUY, DY_SELL = 4.5, 3.5              # 股息率 % (TTM)
DY_FLOOR = 3.0                          # 手册 §4.1「>3% 是底线」(仅提示, 不作信号)

# 指标构建口径
PE_WINDOW_YEARS = 10                    # PE 分位窗口: 过去最多 10 年
PE_WINDOW_DAYS = int(PE_WINDOW_YEARS * 244)
DY_LOOKBACK = 252                       # 股息率 TTM 代理: 比值近 252 交易日增长

# 成本与样本
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
# 组合规则：§4.2 三指标的合取
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
# 风险红线（§4.4）: 人工布尔, 无历史序列 -> 不进回测, 只在 daily 生效
# ----------------------------------------------------------------------
def make_redline_template():
    return {
        "as_of": "",
        "n_blowup": 0,        # 中证红利前十大权重中业绩暴雷只数
        "n_cut_div": 0,       # 下调分红预告只数
        "systemic": False,    # 银行净息差恶化 + 地产频繁暴雷 -> 系统性风险
        "note": "",
    }


REDLINE_FILE = os.path.join(_ROOT, "data", "_dividend_redline.json")


def redline_triggered(rl):
    """§4.4: ≥2 只暴雷 或 ≥3 只下调分红 或 系统性风险 -> 触发。"""
    if not rl:
        return False
    return (int(rl.get("n_blowup") or 0) >= 2
            or int(rl.get("n_cut_div") or 0) >= 3
            or bool(rl.get("systemic")))


def now_str():
    return datetime.now().strftime("%Y-%m-%d %H:%M")
