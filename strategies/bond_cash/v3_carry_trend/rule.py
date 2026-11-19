# -*- coding: utf-8 -*-
"""V3 · Carry + 趋势投票的久期择时（参数权威源）。

规则来源（2026-11-18 联网研究）：
  1) Newfound Research「A Carry-Trend-Hedge Approach to Duration Timing」(2018-10)：
     - Carry = 10Y−1Y 期限利差的**过去 10 年日度分位**，最高 1/3 做多久期、最低 1/3 做空；
     - Trend = 债券超额收益 21 日与 252 日 EWMA 同号才开仓（双确认）；
     - 合成后 Sharpe 0.49~0.58 vs long-only 0.27（1972-2018）。
  2) 兴业证券《基于宏观预期、趋势过滤与泡沫预警的利率择时框架》(2026-05)：
     - 利率 MA5<MA20 / MA20<MA60 → 利率下行 → 利多债券（+1）；
     - 短中长三端投票，得分 ≥2 偏多、≤−2 偏空；
     - 中证10债/中证1债久期轮动 5.86% vs 等权 3.79%，回撤 −1.58% vs −2.96%；
     - 10年国债ETF/短融ETF 5.47% vs 3.79%，回撤 −1.43% vs −1.95%。

**V3 = 这两条的可复现交集**（去掉做空、去掉需要彭博 ECFC / LPPLS 的不可得数据）：
  - Carry 票：10Y−1Y 期限利差的近 3 年分位，≥67% → +1，≤33% → −1，中间 0
  - Trend 票：10Y 国债收益率 MA5<MA20 与 MA20<MA60 各投一票（利率下行=利多债）
  - 两票相加 ∈[−2,+2] → 长端占比 {0%, 17.5%, 35%, 52.5%, 70%}
  - 月度最后交易日调仓、T+1 承担；40pp 额度与现金腿不动

与 v0/v1/v2 的区别
  v0 固定；v1 绝对温度打分（含信用/资金/动能）；v2 横截面排名（实测失败）；
  **v3 只用一条收益率曲线的形状 + 利率自身趋势**，数据最轻、最不依赖基金净值噪音，
  正好补 v1 的「信号源是基金净值、翻转次数 2.5 倍于指数」这个已知缺陷。

口径：利率用 `v1_four_factor/data/_curve.csv` 的 cgb_1y / cgb_10y（akshare 中债国债曲线），
该缓存由 v1 统一维护；v3 只读不写，避免两个变种重复抓同一份数据。
"""
import os

_HERE = os.path.dirname(os.path.abspath(__file__))
_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(_HERE)))   # 仓库根

CFG = {
    # 数据源（v1 维护的曲线缓存 + v0 的腿净值）
    "curve_csv": os.path.abspath(os.path.join(_HERE, "..", "v1_four_factor", "data", "_curve.csv")),
    "universe": ["003376", "011983"],
    "long_leg": "003376",       # 7-10 年国开 A（长端）
    "base_leg": "011983",       # 3-5 年政金 A（短端）
    "sleeve_bond_pp": 30.0,
    "sleeve_cash_pp": 10.0,
    "cash": {"000198": 0.5, "014437": 0.5},

    # Carry 票
    "carry": {"short": "cgb_1y", "long": "cgb_10y", "years": 3,
              "lo": 0.3333, "hi": 0.6667, "min_obs": 250},

    # Trend 票（利率均线：短<长 = 利率下行 = 利多债券）
    "trend": {"pairs": [(5, 20), (20, 60)], "vote_up": 2, "vote_down": -2},

    # 两票 → 长端占比（按 score = carry + trend 索引）
    "long_by_score": {-2: 0.0, -1: 0.175, 0: 0.35, 1: 0.525, 2: 0.70},
    "rebalance": "month_end",
    "hysteresis_pp": 1.5,       # 本策略带宽也是 35pp（0→70%），但信号是月度低频，
                                # 实测 1.5pp 不会造成翻仓（与 v1 的日频不同）

    "fee_side": 0.0010,
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
