# -*- coding: utf-8 -*-
"""V13 · 场内 OHLCV 读取（只读仓库级缓存 `data/_ohlcv.json`，不刷新）。

不直接 import 别人的 `ohlcv.py` 的原因：那些模块里有 `import rule` / `import legs`，
而本目录也有 `rule.py`，同名会把两套参数搅在一起。数据格式只有五行，重复读一次比
调试「谁 import 了谁的 rule」便宜得多。缓存由 `tech_indicators/v18_volume_notes/ohlcv.py`
的 `--bootstrap` / 增量刷新维护（v1-2 的同名脚本已于 2026-11-10 随 V1-2 一起删除）。
"""
import json
import os

import pandas as pd

import rule

LIB = os.path.join(rule._ROOT, "data", "_ohlcv.json")


def frame(leg):
    """该腿场内前复权日线 -> DataFrame(index=date, columns=open/high/low/close/volume)。"""
    code = rule.LEGS[leg][0]
    with open(LIB, "r", encoding="utf-8") as f:
        node = json.load(f)[code]
    df = pd.DataFrame({
        "open": node["open"], "high": node["high"], "low": node["low"],
        "close": node["close"], "volume": node["volume"],
    }, index=pd.to_datetime(node["dates"])).sort_index()
    return df.astype(float)


def available():
    """有数据的腿（A500 2024-10 才上市，年线成形后样本很短）。"""
    with open(LIB, "r", encoding="utf-8") as f:
        lib = json.load(f)
    return [k for k in rule.LEG_ORDER if rule.LEGS[k][0] in lib]
