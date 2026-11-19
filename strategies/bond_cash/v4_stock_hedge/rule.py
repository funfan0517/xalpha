# -*- coding: utf-8 -*-
"""V4 · 股债对冲三票（Carry + Trend + Hedge）的久期择时 —— **参数权威源**。

规则来源（2026-11-19 联网研究，接 v3 README §3 留的「Hedge 票」口子）：
  1) Newfound Research（2018-10）*A Carry-Trend-Hedge Approach to Duration Timing*：
     Carry / Trend 两票之外，第三条腿是**股票动量极端分位反向映射债券** ——
     股票风险偏好过热（动量分位顶）压制久期，股票动量崩到分位底（避险）拉久期。
     v3 只实现了前两票，Hedge 票当时因「需要股票指数长序列」未做；本仓库
     `data/_bt_caches/csi_000300.csv` 从 2005 起，足够覆盖 5 年分位窗。
  2) 国泰海通（2026-06）*从利率择时到久期轮动*：股债增强配置（久期轮动 + 权益风格择时）
     年化 11.00% / 夏普 1.70；说明「股债相对位置」对久期敞口有独立增量信息。

三票（与 v3 的两票严格同源，只多一条）：
  - Carry：10Y−1Y 期限利差近 3 年滚动分位 ≥66.7% → +1，≤33.3% → −1，中间 0
  - Trend：10Y 国债收益率 MA5<MA20 与 MA20<MA60 各一票，全同向才记 ±1
  - Hedge：沪深300 近 252 日累计收益的 5 年滚动分位 ≥90% → −1（股票过热，减久期），
           ≤10% → +1（股票崩塌，避险加久期），其余 0

score = carry + trend + hedge ∈ [−3,+3] → 长端占比 0 / 12 / 23 / 35 / 47 / 58 / 70%，
月末最后交易日调仓、T+1 承担；40pp 额度与现金腿（5pp 货基 + 5pp 存单）完全不动。

为什么 Hedge 票可能与 v3 抵消而不是叠加：股票极端强势期往往也是「宽货币 + 风险偏好修复」，
债券并不必然走熊；所以本变种的全部价值必须由回测说话，见 backtest/_v4_bt.md。
"""
import os

_HERE = os.path.dirname(os.path.abspath(__file__))
_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(_HERE)))   # 仓库根

CFG = {
    # 数据源（v1 维护的曲线缓存 + 仓库既有沪深300 日线）
    "curve_csv": os.path.abspath(os.path.join(_HERE, "..", "v1_four_factor", "data", "_curve.csv")),
    "stock_csv": os.path.join(_ROOT, "data", "_bt_caches", "csi_000300.csv"),
    "universe": ["003376", "011983"],
    "long_leg": "003376",       # 7-10 年国开 A（长端）
    "base_leg": "011983",       # 3-5 年政金 A（短端）
    "sleeve_bond_pp": 30.0,
    "sleeve_cash_pp": 10.0,
    "cash": {"000198": 0.5, "014437": 0.5},

    # Carry 票（与 v3 完全一致，便于横向对比）
    "carry": {"short": "cgb_1y", "long": "cgb_10y", "years": 3,
              "lo": 0.3333, "hi": 0.6667, "min_obs": 250},

    # Trend 票（利率均线：短<长 = 利率下行 = 利多债券）
    "trend": {"pairs": [(5, 20), (20, 60)], "vote_up": 2, "vote_down": -2},

    # Hedge 票（股票动量极端分位反向映射债券）
    "hedge": {"window": 252, "years": 5, "min_obs": 250,
              "hi": 90.0, "lo": 10.0,
              "on_stock_hot": -1,     # 股票极端强 → 减久期
              "on_stock_cold": 1},    # 股票极端弱 → 加久期（避险）

    # 三票 → 长端占比（score = carry + trend + hedge）
    "long_by_score": {-3: 0.00, -2: 0.12, -1: 0.23, 0: 0.35, 1: 0.47, 2: 0.58, 3: 0.70},
    "rebalance": "month_end",
    "hysteresis_pp": 1.5,       # 带宽 35pp、信号月度低频，1.5pp 足够（与 v3 同源）

    "fee_side": 0.0010,
    "fee_side_grid": [0.0, 0.0010, 0.0040],
    "rf_annual": 0.014,
}

NAMES = {"003376": "广发7-10年国开A", "011983": "永赢3-5年政金A",
         "000198": "余额宝", "014437": "存单AAA 7天"}


def is_rebalance(idx):
    """月末最后一个交易日 -> bool Series。

    看**下一个工作日**是否跨月，而不是「下一行是否换月」—— 后者在「数据只到最新一天」
    时会把最后一行判成月末，让月末调仓退化成日频调仓。
    """
    import pandas as pd
    return pd.Series([(pd.Timestamp(d) + pd.offsets.BDay(1)).month != pd.Timestamp(d).month
                      for d in idx], index=idx)
