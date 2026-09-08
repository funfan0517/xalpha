# -*- coding: utf-8 -*-
"""A 全球相对动量轮动 · 规则定义(唯一保留版本, 2026-09-08 定稿方向)
标的池: scan_universe 34 只场内 ETF/LOF(xueqiu 十年库)
规则:
  1) MTM: 每 21 交易日(≈月)再平衡, momentum = close_t/close_{t-120} - 1 降序
  2) MA20 过滤: 从动量排名由高到低取首个 close ≥ MA20 的标的(仅持一只)
  3) 全部跌破 MA20 或无候选 → 空仓现金
  4) 无前视: 当日收益归旧仓, 再平衡日收盘后换仓; 卖出费 ≤7 交易日 1.5% / 7 日外 0%
"""
import json

import pandas as pd

SCAN34 = [
    "563360", "588000", "515080", "518880", "513100", "513500", "513180",
    "515880", "512480", "159819", "562500", "512010", "159992", "159928",
    "512690", "159825", "159698", "512800", "512000", "512660", "516160",
    "159326", "515790", "512400", "159869", "159870", "159227", "515230",
    "515220", "560860", "512980", "159732", "161715", "159201",
]
LONG_CACHE = "g:/xalpha/data/_long_klines.json"  # 十年库(2015+, 含 510300/511260 等扩展列)
LOOKBACK, MA, REBAL, MIN_HIST = 120, 20, 21, 140
FEE_SHORT_DAYS, FEE_SHORT, FEE_LONG = 7, 0.015, 0.0


def load_wide(path=None):
    """xueqiu 十年库 -> ffill 宽表(index=日期, columns=code)"""
    raw = json.load(open(path or LONG_CACHE, encoding="utf-8"))
    series = {}
    for code in SCAN34:
        d = raw.get(code)
        if not d or len(d["close"]) < MIN_HIST:
            continue
        s = pd.Series(d["close"], index=pd.to_datetime(d["dates"]), dtype=float)
        s = s[~s.index.duplicated(keep="last")].sort_index()
        series[code] = s
    return pd.DataFrame(series).sort_index().ffill()
