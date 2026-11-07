# -*- coding: utf-8 -*-
"""右侧买入策略 · 行情装载（只读公用十年库，策略自身不联网）。

数据来源（**数据类**，留在 `data/`，跨策略共享）：
  - `data/_long_klines.json`    收盘价（2015-01 起，前复权）
  - `data/_indicators_hist.json` 成交量（本策略只取 volume，指标一律自算，避免两套口径打架）
刷新：`data/_refresh_data.py::ensure_fresh()` —— 增量 + 复权一致性校验，已最新时只做日期比较。

标的池唯一入口：`data/_universe.md` 的「场内对应」列（由 `pipeline/universe.py` 派生），**禁止硬编码**。
"""
import os
import sys

import pandas as pd

_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)
if os.path.join(_ROOT, "data") not in sys.path:
    sys.path.insert(0, os.path.join(_ROOT, "data"))

_PARENT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))   # strategies/tech_indicators
if _PARENT not in sys.path:
    sys.path.insert(0, _PARENT)
_STRAT = os.path.dirname(_PARENT)                                       # strategies/
if _STRAT not in sys.path:
    sys.path.insert(0, _STRAT)
import _pool  # noqa: E402  家族唯一池定义（_universe.md 前四类）
import _panel  # noqa: E402  面板口径唯一实现（与 v16_trin 共用）

LK = os.path.join(_ROOT, "data", "_long_klines.json")
IND = os.path.join(_ROOT, "data", "_indicators_hist.json")

# 2026-10-28：与家族统一 —— 池只取 _universe.md 前四类的场内标的（唯一实现在 ../_pool.py）
POOL = _pool.rows()                                # [{idx, code, cat, theme, off_name, inner_name}]
CODES = [r["code"] for r in POOL]
CATS = {r["code"]: r["cat"] for r in POOL}
NAMES = {r["code"]: r["inner_name"] for r in POOL}
THEMES = {r["code"]: r["theme"] for r in POOL}


def sh(code):
    """6 位场内代码 -> xalpha 前缀格式（SH/SZ）。"""
    return ("SH" if code.startswith(("5", "6", "9")) else "SZ") + code


def _refresh(verbose=True):
    """触发公用行情库刷新检查（实现在 strategies/_panel.py，进程内幂等）。"""
    return _panel.ensure_fresh(verbose=verbose)


def load_panels(refresh=True):
    """-> {field: DataFrame(index=DatetimeIndex, columns=code)}，field ∈ {close, volume}。

    口径实现在 `strategies/_panel.py::load_pair`（v9/v16 共用的唯一实现）：
    每只标的保留**自身交易日**（不 ffill），停牌日留 NaN、下游按行丢弃，
    避免补齐后的 0 成交量污染量比类因子（与 lights 同一教训，fix#2）。
    volume 与 dates 长度不一致时**直接抛错**，不再静默丢列。
    """
    return _panel.load_pair(CODES, refresh=refresh, lk=LK, ind=IND)


def load_code(panels, code):
    """单标的 -> DataFrame(index=date, c, v)，丢弃 close 缺失行。"""
    df = pd.DataFrame({"c": panels["close"][code], "v": panels["volume"][code]}).dropna(subset=["c"])
    return df.sort_index()


def complete_end(idx, close_hour=15):
    """最后一个「已收盘」交易日（避免盘中半日 bar 污染指标与扫描结论）。

    实现在 `strategies/_panel.py::complete_end`（v9/v16 共用）。
    """
    return _panel.complete_end(idx, close_hour=close_hour)


def _main():
    p = load_panels(refresh=False)
    print(f"标的池: {len(CODES)} 只（{os.path.join(_ROOT, 'data', '_universe.md')}）")
    print(f"收盘面板: {p['close'].shape} · {p['close'].index[0].date()} ~ {p['close'].index[-1].date()}")
    print(f"成交面板: {p['volume'].shape} · 缺失列 {[c for c in CODES if c not in p['volume']]}")


if __name__ == "__main__":
    _main()
