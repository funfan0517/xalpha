#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""V10 · 日频面板（价格指数信号 + 全收益指数收益 + 反推股息率）

单只标的的面板（`build`）
-------------------------
| 列 | 定义 | 用途 |
|---|---|---|
| close / high / low | 价格指数日频 | 信号 |
| ma250 / ma750 | 收盘价 250 / 750 日均线（年线 / 三年线） | 买点条件② |
| peak | `close.rolling(PEAK_WIN, min_periods=PEAK_MIN).max()` | 「本轮下跌起点」的前高 |
| dd | `1 − close / peak` | 买点条件①、深跌加仓 |
| dy | `(T/P) / (T/P).shift(250) − 1`（**小数**，0.0458 = 4.58%） | 买点条件③ |
| tr | 全收益指数（分红再投） | 记收益 |

⚠ **股息率必须是比值，不是「两个收益率相减」**
`dy = T_t/T_{t-250} ÷ P_t/P_{t-250} − 1`。若写成 `(T_t/T_{t-250}−1) − (P_t/P_{t-250}−1)`，
在 2007 那种价格翻倍的年份会把股息率虚高到 11.4%（正确值 3.4%）—— 因为算术差漏掉了
「股息本身也参与了复利」。这一条 `../yield_hist.py` 用的是算术差，见 `RULES.md §四`。

无前视：所有列都是「当日及以前」的滚动量；回测里信号在 T 日收盘算出、T+1 日收盘执行。
"""
from __future__ import annotations

import os
import sys

import pandas as pd

_DIR = os.path.dirname(os.path.abspath(__file__))
if _DIR not in sys.path:
    sys.path.insert(0, _DIR)

import data  # noqa: E402  pylint: disable=wrong-import-position
import rule  # noqa: E402  pylint: disable=wrong-import-position

COLS = ["close", "high", "low", "ma250", "ma750", "peak", "dd", "dy", "tr", "below_ma"]


def panel(target: dict, refresh: bool = False) -> pd.DataFrame:
    """单只标的的日频面板 -> DataFrame(index=date, 见模块 docstring)。"""
    px = data.load_index(target["index"], refresh=refresh)
    tr = data.load_index(target["total"], refresh=refresh)
    if px.empty or tr.empty:
        print(f"[v10:warn] {target['index']} 价格 / 全收益数据缺失，跳过", file=sys.stderr)
        return pd.DataFrame(columns=COLS)

    p = px.set_index("date").sort_index()
    t = tr.set_index("date").sort_index()
    d = pd.DataFrame({
        "close": pd.to_numeric(p["close"], errors="coerce"),
        "high": pd.to_numeric(p["high"], errors="coerce"),
        "low": pd.to_numeric(p["low"], errors="coerce"),
        "tr": pd.to_numeric(t["close"], errors="coerce"),
    }).dropna(subset=["close", "tr"])
    d = d[~d.index.duplicated(keep="last")]

    d["ma250"] = d["close"].rolling(rule.MA_SHORT, min_periods=rule.MA_SHORT).mean()
    d["ma750"] = d["close"].rolling(rule.MA_LONG, min_periods=rule.MA_LONG).mean()
    d["peak"] = d["close"].rolling(rule.PEAK_WIN, min_periods=rule.PEAK_MIN).max()
    d["dd"] = 1 - d["close"] / d["peak"]

    # ⚠ 单位是**小数**（0.0458 = 4.58%），与 rule.DY_BUY 等阈值同量纲；
    # 写成百分数会让 `dy >= 0.045` 变成「恒真」，2008 年 1.4% 的股息率也会被当买点。
    ratio = d["tr"] / d["close"]
    d["dy"] = ratio / ratio.shift(rule.DY_WIN) - 1

    # 「或」条件：跌破年线 或 跌破三年线，二者其一即可（NaN 视为不成立）
    below_s = d["close"] <= d["ma250"]
    below_l = d["close"] <= d["ma750"]
    d["below_ma"] = (below_s.fillna(False) | below_l.fillna(False)).astype(bool)
    return d[COLS]


def build(refresh: bool = False) -> dict[str, pd.DataFrame]:
    """全部标的 -> {指数代码: 面板}。"""
    out = {}
    for t in rule.TARGETS:
        d = panel(t, refresh=refresh)
        if not d.empty:
            out[t["index"]] = d
    return out


def align_asof(panels: dict) -> tuple[pd.Timestamp, dict]:
    """盘中（15:10 前）剔除当日半根 K；截止日取各标的最后交易日的最大值。"""
    from datetime import datetime       # pylint: disable=import-outside-toplevel

    now = datetime.now()
    drop_today = now.hour * 60 + now.minute < 15 * 60 + 10
    lasts = {}
    for code, d in panels.items():
        idx = d.index
        if drop_today and len(idx) and idx[-1].normalize() == pd.Timestamp(now.date()):
            idx = idx[:-1]
        if len(idx):
            lasts[code] = idx[-1]
    if not lasts:
        raise RuntimeError("没有任何标的有价格数据")
    top = max(lasts.values())
    return pd.Timestamp(top), {c: v for c, v in lasts.items() if v != top}


if __name__ == "__main__":
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    pn = build(refresh="--refresh" in sys.argv)
    for code, d in pn.items():
        t = rule.BY_INDEX[code]
        ok = d.dropna(subset=["dy", "ma750"])
        print(f"[v10:factors] {code} {t['name']:<10s} {len(d):5d} 行 · 可用 {len(ok):5d} 行 "
              f"{ok.index[0]:%Y-%m-%d}~{ok.index[-1]:%Y-%m-%d}")
        r = ok.iloc[-1]
        print(f"               close={r['close']:.2f} ma250={r['ma250']:.2f} ma750={r['ma750']:.2f} "
              f"peak={r['peak']:.2f} dd={r['dd'] * 100:.1f}% dy={r['dy'] * 100:.2f}% "
              f"below={bool(r['below_ma'])}")
