# -*- coding: utf-8 -*-
"""成长/红利风格轮动 · **v1「绝对阈值 + 5 期分批 + 定投」**（本变种唯一参数权威源）

相对 v0（`../rule.py`：MA20±缓冲带 + 250 日分位）的三处根本差异：

| 维度 | v0 | v1（本变种） |
| --- | --- | --- |
| 信号 | R 相对自身轨道（MA20±1% 缓冲带 + 250 日分位 Q） | R 的**绝对水位** `RATIO_LOW` / `RATIO_HIGH` |
| 换仓 | 一次性整仓切换 | 清仓旧标的 → 资金拆 `TRANCHE` 份 → 分 `TRANCHE` 个定投日买回 |
| 资金 | 无资金流（净值口径） | 每定投日固定金额 `FIXED_AMOUNT` 买入当前标的 |

状态只有两态 `growth / dividend`（手册里的 `none` 等价于「保持当前标的继续定投」，
不产生额外分支，故不设第三态）。任何阈值改动**只改本文件**。

⚠ **RATIO_LOW / RATIO_HIGH 是量纲相关阈值**：R 的绝对值取决于两只指数的基期与编制方式，
0.20 / 0.31 只对「科创50(000688) ÷ 中证红利(000922)、数据起点 2020」这一对成立
（约等于该比值的 40% / 90% 分位，与 v0 原 0.40/0.62 在创业板指比值上的分位位置一致）。
换分子（创业板指）、换基期、换数据源起点，都必须重新拟合 —— 这是 v1 相对 v0 的结构性弱点
（v0 的缓冲带/分位无量纲，换标的不失效）。

⚠ 手册内部有一处自相矛盾：伪代码 `check_signal` 在 `pending_swap` 时无条件返回
`continue_swap`，但正文第 4 条要求「期间若反向阈值先触发，停止未完成的分批，按新信号处理」。
本实现按**正文**（`INTERRUPT_PENDING=True`），伪代码版本可置 False 复现。
"""

# ============================ 标的（信号与执行通道沿用 v0） ============================
# 指数缓存是**跨版本共享数据**，归 v0 根目录的 data/ 管，本变种只读（见 data.py）。
GROWTH_INDEX = "000688"      # 科创50（与 v0 默认分子一致）
DIV_INDEX = "000922"         # 中证红利


# ============================ 绝对水位阈值（量纲相关，见文件头警告） ============================
# 阈值已按「科创50(000688) ÷ 中证红利(000922)」这一对重新标定（约 40% / 90% 分位）。
RATIO_LOW = 0.20             # R ≤ 0.20：科创50 低估 → 清红利，分 5 期买成长
RATIO_HIGH = 0.31            # R ≥ 0.31：科创50 高估 → 清成长，分 5 期买红利
# 中间地带（0.20 < R < 0.31）：不轮动，只继续定投当前标的


# ============================ 分批换仓 ============================
TRANCHE = 5                  # 换仓资金拆几份（手册固定 5 份）
TRANCHE_SCHEDULE = "monthly"  # monthly = 逐月定投日各买 1 份（手册正文口径）; days = 每 N 交易日 1 份
TRANCHE_GAP_DAYS = 5         # TRANCHE_SCHEDULE="days" 时的间距（手册「或 5 周 / 5 日」）
INTERRUPT_PENDING = True     # 分批未完成时，反向阈值是否打断重来（手册正文=是，伪代码=否）


# ============================ 定投 ============================
FIXED_AMOUNT = 1000.0        # 每定投日固定买入金额（手册：每月 1000 元）
TARGET_INIT = "dividend"     # 起始标的（防御侧起步，与 v0 的初始状态 0=红利 一致）
INIT_CASH_DCA = 0.0          # 定投模式的初始现金
INIT_CASH_LUMP = 1.0         # 一次性本金模式的初始资金（口径 A，与 v0 净值口径可比）


# ============================ 交易成本 ============================
FEE = 0.0003                 # 单边费率（与 v0 同口径，便于对比）


def growing(code=None):
    """当前成长指数代码（v1 只跑默认分子 000688 科创50；创业板指见 README「已知边界」）。"""
    return code or GROWTH_INDEX


def check_signal(r_prev, target, pending, interrupt=None, low=None, high=None):
    """状态机取信号：返回 None（不动）或新标的字符串（需要换仓）。

    r_prev  = R_{t-1}（决策用上一交易日收盘）
    target  = 当前标的 'growth' / 'dividend'
    pending = 未完成的分批 dict 或 None
    low/high= 阈值覆盖（敏感性分析用；None 取本文件常量 —— 常量仍是唯一权威源）
    """
    low = RATIO_LOW if low is None else low
    high = RATIO_HIGH if high is None else high
    if interrupt is None:
        interrupt = INTERRUPT_PENDING
    if pending is not None and not interrupt:
        return None                      # 伪代码口径：分批期间不看反向信号
    if r_prev <= low and target != "growth":
        return "growth"
    if r_prev >= high and target != "dividend":
        return "dividend"
    return None
