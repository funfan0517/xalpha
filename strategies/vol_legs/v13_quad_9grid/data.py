# -*- coding: utf-8 -*-
"""V13 · 行情读取（**真实指数**为默认口径；ETF 只作历史对照）。

为什么不直接 import 别人的 `ohlcv.py`：那些模块里有 `import rule` / `import legs`，
而本目录也有 `rule.py`，同名会把两套参数搅在一起。数据格式只有五行，重复读一次比
调试「谁 import 了谁的 rule」便宜得多。

## 价格口径（唯一，2026-11-13 起）
价格 = 该市场**真实指数**收盘，量能 = 同源指数成交量；黄金无指数，用场内 `518880`。
旧的场内 ETF 前复权对照口径已删除。

指数库：A股 3 腿 + 美股 2 腿 + 恒生科技在 `data/_global_index_klines.json`
（`SH000510` / `.NDX` / `.INX` / `HKHSTECH`）与 `data/_cn_index_klines.json`
（`SH000300` / `SH000688`）；黄金没有指数（现货 OTC 无量），退回 `518880`。
本变种只用收盘价（九宫格不看量）；`open/high/low` 现在由 `_idxcache` 给出**真实值**
（2026-11-13 起落库），只是本变种不读它们 —— schema 与 v12 保持一致。
"""
import json
import os
import sys

import pandas as pd

import rule

# 七腿信号价格的唯一数据层（指数 OHLCV / 黄金用 518880 实物 ETF）。
def _load_idxcache():
    """按**路径**加载唯一数据层，不往 `sys.path` 插 vol_legs 目录。

    ⚠ 不能写成 `import _idxcache`：那要把 `strategies/vol_legs` 插到 `sys.path` 最前，
    而那里有同名的 `engine.py` / `data.py` / `rule.py`，会把本变种的同名模块顶掉。
    """
    import importlib.util
    mod = sys.modules.get("_vl_idxcache")
    if mod is not None:
        return mod
    path = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                        "_idxcache.py")
    spec = importlib.util.spec_from_file_location("_vl_idxcache", path)
    mod = importlib.util.module_from_spec(spec)
    sys.modules["_vl_idxcache"] = mod
    spec.loader.exec_module(mod)
    return mod


_idxcache = _load_idxcache()

LIB = os.path.join(rule._ROOT, "data", "_ohlcv.json")
GIDX_LIB = os.path.join(rule._ROOT, "data", "_global_index_klines.json")
CN_IDX_LIB = os.path.join(rule._ROOT, "data", "_cn_index_klines.json")

# 腿 -> (库, 指数代码)。**权威表在 `_idxcache.LEGS`**，这里派生（黄金不在，退回 ETF）。
IDX_MAP = {leg: (kind, code)
           for leg, (kind, code, _name) in _idxcache.LEGS.items() if kind != "etf"}


def _read(path):
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


def index_frame(leg):
    """该腿真实指数 OHLCV -> DataFrame(open/high/low/close/volume)（实现见 `_idxcache`）。"""
    return _idxcache.frame(leg)


def frame(leg):
    """该腿日线 -> DataFrame(index=date, columns=open/high/low/close/volume)。

    价格恒为真实指数；黄金没有指数 -> 场内 `518880` 实物 ETF（与全族同一序列）。
    """
    if leg in IDX_MAP:
        return index_frame(leg)
    if leg == "gold":
        return _idxcache.frame("gold")
    raise KeyError(f"{leg} 没有真实指数序列（黄金用 518880）")


def available():
    """有数据的腿（真实指数下 A500/纳指/标普 都有 2005 起的样本）。"""
    return [k for k in rule.LEG_ORDER if k in IDX_MAP or rule.LEGS[k][0] in _read(LIB)]
