# -*- coding: utf-8 -*-
"""成长/红利风格轮动 · 规则化定义（唯一权威源）

方法论：每日收盘后算「成长指数 ÷ 中证红利」风格比值 R，
        算 R 的均线(20/30)与上下缓冲带，算 R 过去 250 日分位数 Q，
        按缓冲带 + 分位数双信号做「满仓成长 / 红利(现金)」二元切换，
        次日开盘执行。默认成长=创业板指，红利=中证红利；科创50 仅替换分子。

参数判据（见各常量 docstring）：
  - 缓冲带 BUFFER 默认 ±1%，可在 0.5%~1.5% 微调（震荡大加宽、科创50 收窄）
  - 分位数窗口 QUANTILE_WINDOW=250 交易日
  - 入场阈值 ENTER_Q_MAX=0.8（持有红利/现金时，R>上轨 且 Q<0.8 才满仓成长）
  - 离场阈值 EXIT_Q_MIN=0.9（持有成长时，R<下轨 或 Q>0.9 才切红利）
  - 月频上限 MAX_TRADES_PER_MONTH=4（当月已超 4 次则暂停一次，防摩擦）
  - 任何阈值改动**只改本文件**，引擎/信号层禁止内联常量。

执行通道（信号用指数、执行走场外联接基金，见 EXEC）：
  成长侧默认创业板指联接 / 科创50 联接；红利侧=中证红利联接（与唯一池 #42 同代码）。
"""
import os
import sys

_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)


# ============================ 指数与标的 ============================
# 指数代码（信号源，直接用指数收盘算风格比值 R；前缀 SH/SZ 供 xa.get_daily）
GROWTH_INDEX = "399006"      # 创业板指（默认分子）
DIV_INDEX = "000922"         # 中证红利（分母，固定）
STAR50_INDEX = "000688"      # 科创50（备选分子，仅替换分子用）

GROWTH_NAME = "创业板指"
DIV_NAME = "中证红利"
STAR50_NAME = "科创50"

# 执行通道（场外联接基金）：信号用指数，申赎走场外。
# 红利侧 012644 与唯一池 strategies 共用；成长侧为对应指数联接（非唯一池硬性约束，
# 属本策略方法论固有标的，随指数切换而切换）。
EXEC = {
    "cyb": ("011362", "易方达创业板ETF联接C"),          # 创业板指联接
    "kc": ("011609", "易方达上证科创板50ETF联接C"),       # 科创50联接（唯一池 #2）
    "div": ("012644", "招商中证红利ETF联接C"),            # 中证红利联接（唯一池 #42）
}


# ============================ 均线 / 缓冲带 ============================
MA_FAST = 20                 # 20 日简单均线（缓冲带基准）
MA_SLOW = 30                 # 30 日简单均线（辅助观察，震荡大时可选作基准）
QUANTILE_WINDOW = 250        # 分位数窗口（交易日）

BUFFER = 0.01                # 缓冲带半宽 ±1%（上轨=MA20×(1+BUFFER)，下轨=MA20×(1-BUFFER)）
BUFFER_RANGE = (0.005, 0.015)  # 微调允许区间 0.5%~1.5%（仅提示，不改逻辑）


# ============================ 分位风控阈值 ============================
Q_LOW = 0.30                 # Q<0.3 成长低估
Q_HIGH = 0.70                # Q>0.7 成长高估
ENTER_Q_MAX = 0.80           # 持有红利/现金 → 满仓成长 的额外约束：Q_{-1} < 此值
EXIT_Q_MIN = 0.90            # 持有成长 → 切红利 的额外约束：Q_{-1} > 此值


# ============================ 交易执行 ============================
MAX_TRADES_PER_MONTH = 4     # 当月已超 4 次切换则暂停下一次（防摩擦）
FEE = 0.0003                 # 单边切换成本（ETF 佣金，默认未含滑点）
TRADE_AT = "信号次日开盘"    # 决策用 R_{-1}（最新可得收盘），执行用次日开盘价


def index_prefix(code):
    """指数代码 -> xa.get_daily 所需 SH/SZ 前缀（5/6/9 开头为 SH）。"""
    return ("SH" if code.startswith(("5", "6", "9")) else "SZ") + code


def growth_code(alt=False):
    """当前使用的成长指数代码（alt=True 时切科创50）。"""
    return STAR50_INDEX if alt else GROWTH_INDEX


def growth_label(alt=False):
    return STAR50_NAME if alt else GROWTH_NAME


def exec_pair(alt=False):
    """返回 (成长侧场外代码, 成长侧名称, 红利侧场外代码, 红利侧名称)。"""
    g_code, g_name = EXEC["kc"] if alt else EXEC["cyb"]
    d_code, d_name = EXEC["div"]
    return g_code, g_name, d_code, d_name
