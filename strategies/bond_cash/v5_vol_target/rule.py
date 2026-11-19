# -*- coding: utf-8 -*-
"""V5 · 波动率目标（vol targeting）的久期敞口 —— **参数权威源**。

规则来源（2026-11-19 联网研究）：
  1) Moreira & Muir (2017) *Volatility-Managed Portfolios*：按已实现波动反向缩放敞口，
     夏普提升 20~40%；机制是「波动可预测、收益不可预测」。
  2) 中金/发现报告《基于目标波动率的资产组合管理》：股票/债券/商品通吃，
     7.5 年回测 9.5% / 17.3% / 28.0% 三档目标，夏普均 >1.8、卡玛 >1.3。
  3) 兴证《利率债收益预测框架》(2020-05)：VaR 方法的久期择时在**不降低收益**的前提下
     大幅压低回撤（VaR25% 年化 5.3% / 回撤 2.9% / 夏普 2.37 vs 基准 1.85）。
  4) 国泰海通《从利率择时到久期轮动》(2026-06)：资金条件维度用「长短期均线相对位置」
     识别边际松紧，年化 2.64% / 夏普 1.26。

与 v1~v4 的差别（这是本变种唯一的新东西）：
  **v1~v4 全是「方向票」（预测利率往哪走），V5 不预测方向，只按风险预算缩放敞口。**
  长端腿（003376）的已实现波动 σ 上升 → 按 target/σ 自动减久期；σ 回落 → 自动加回。
  极端波动（σ > guard_mult × target，典型是赎回潮/债灾）→ 直接清零长端。

口径（其他一切与 v0 一致）：
  - 债腿 30pp 内：长端 003376 占比 w = cap × min(1, target/σ)，clip 到 [0, cap]
  - σ = max(σ21, σ63)（两窗取较大者，偏保守；只用一个窗口会被单日跳空骗到）
  - 调仓：月末（默认）或每 10 个交易日（"双周"）；T+1 承担；滞后带 1.5pp
  - 现金腿 5pp 货基 + 5pp 存单、40pp 额度不动

已知风险：收益端不做择时意味着「波动高的那一段如果是上涨段，V5 会系统性少赚」——
2024Q4~2025 的债牛就是这种形态，回测里能看到，不要让「回撤更浅」掩盖这一点。
"""
import os

_HERE = os.path.dirname(os.path.abspath(__file__))

CFG = {
    "universe": ["003376", "011983"],
    "long_leg": "003376",       # 7-10 年国开 A（波动源）
    "base_leg": "011983",       # 3-5 年政金 A（其余都放这里）
    "sleeve_bond_pp": 30.0,
    "sleeve_cash_pp": 10.0,
    "cash": {"000198": 0.5, "014437": 0.5},

    # 波动率目标
    "vol": {
        "fast": 21,             # 快速窗（约 1 个月）
        "slow": 63,             # 慢速窗（约 1 个季度）
        "target_annual": 0.015,  # 长端腿目标年化波动 1.50%（约 003376 长期中枢）
        "cap_w": 0.70,          # 上限 = v0 的进攻档，不允许加杠杆
        "floor_w": 0.00,
        "guard_mult": 2.0,      # σ > 2×target → 清零长端（极端波动熔断）
        "min_obs": 30,
    },

    "rebalance": "month_end",   # month_end | every_n
    "every_n": 10,              # 双周（10 个交易日）
    "warmup": 63,               # 回测起点前的预热交易日
    "hysteresis_pp": 1.5,

    "fee_side": 0.0010,
    "fee_side_grid": [0.0, 0.0010, 0.0040],
    "rf_annual": 0.014,
}

NAMES = {"003376": "广发7-10年国开A", "011983": "永赢3-5年政金A",
         "000198": "余额宝", "014437": "存单AAA 7天"}


def is_rebalance(idx, mode=None, n=None):
    """调仓日 bool Series。

    - month_end：看**下一个工作日**是否跨月（不能用「下一行是否换月」，否则数据只到今天时
      最后一行会被判成月末）
    - every_n：从第一行起每 n 个交易日
    """
    import pandas as pd
    mode = mode or CFG["rebalance"]
    n = int(n or CFG["every_n"])
    idx = pd.DatetimeIndex(idx)
    if mode == "every_n":
        return pd.Series([(i % n) == (n - 1) for i in range(len(idx))], index=idx)
    return pd.Series([(pd.Timestamp(d) + pd.offsets.BDay(1)).month != pd.Timestamp(d).month
                      for d in idx], index=idx)
