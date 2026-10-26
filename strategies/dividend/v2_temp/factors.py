#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""估值温度 v2 · 因子面板（数据 → 五维分位 → 温度）

口径
----
* **股债收益差** = 税后股息率 − 10Y 中债（`data/_div_yield_m.csv` + `bond10y_m.csv`）。
  `000922`/`H30269` 为全收益反推的真实实现股息率；`930955` 为中证红利变化率代理；
  `931446` 无序列 → 同样用代理 + 官网快照(4.14%)校准，标 `proxy`。
* **PE 分位**：中证官网 `peg` 日频历史（`strategies/dividend/data/_index_peg.json`）。
* **年线乖离** = 收盘 / MA250 − 1（日频，月末取值）。
* **价格位置** = (收盘 − 近 3 年最低) / (近 3 年最高 − 最低)，月末取值。
* **PB**：无历史序列 → **不参与温度**（`rule.PB_AVAILABLE = False`）。

分位一律为**扩张窗口**：最少 36 个月、上限 120 个月（10 年），含当月自身
（与 `v1_spread` 的「排除自身」差别可忽略，但这里不再排除，因为分位本身就是
「当前值在历史里的位置」，排除自身会让极值月系统性偏离）。

无前视：所有分位只用 t 及以前的样本；温度在月末算出，下月才用于决策。
"""

from __future__ import annotations

import os
import sys

import numpy as np
import pandas as pd

_DIR = os.path.dirname(os.path.abspath(__file__))
_STRAT = os.path.dirname(_DIR)
sys.path.insert(0, _STRAT)
sys.path.insert(0, os.path.join(_STRAT, "v1_spread"))   # 数据层 yielddata（其余脚本已在用）

import pool  # noqa: E402  pylint: disable=wrong-import-position
import yielddata  # noqa: E402  pylint: disable=wrong-import-position
import rule  # noqa: E402  pylint: disable=wrong-import-position

FACTORS = ("spread", "pe", "ma_dev", "pos")


# ------------------------------------------------------------------ 数据
def _y10() -> pd.Series:
    raw = pd.read_csv(rule.Y10_CSV)
    col = "close" if "close" in raw.columns else raw.columns[-1]
    s = pd.Series(pd.to_numeric(raw[col], errors="coerce").to_numpy(),
                  index=pd.to_datetime(raw.iloc[:, 0])).dropna().sort_index()
    return s.rename("y10")


def _dy_map() -> dict[str, pd.Series]:
    df = pd.read_csv(rule.YIELD_CSV, parse_dates=["date"])
    base = yielddata.series(df, rule.BASE_INDEX)
    out = {}
    for code in rule.INDICES:
        s = yielddata.series(df, code)
        if s.empty:
            # 无序列 → 中证红利变化率 × 快照校准（与 yielddata.build 的代理口径一致）
            snap = rule.DY_SNAPSHOT[code]
            s = base * (snap / base.iloc[-1])
        else:
            s = s * (rule.DY_SNAPSHOT.get(code, float(s.iloc[-1])) / s.iloc[-1])
        out[code] = s.rename("dy")
    return out


def _peg(codes) -> pd.DataFrame:
    return pool.load_peg(codes=list(codes))


# ------------------------------------------------------------------ 单指数面板
def index_panel(code: str, y10: pd.Series, dy: pd.Series, peg: pd.Series,
                refresh: bool = False) -> pd.DataFrame:
    """-> 月度 DataFrame(close, dy, y10, pe, spread, ma_dev, pos)。"""
    close = yielddata.csi_series(code, force=refresh)
    daily = pd.concat([close.rename("close"), peg.rename("pe")], axis=1, join="outer").sort_index()
    daily["close"] = daily["close"].ffill()
    daily["pe"] = daily["pe"].ffill()
    ma = daily["close"].rolling(rule.MA_WIN).mean()
    lo = daily["close"].rolling(rule.POS_WIN).min()
    hi = daily["close"].rolling(rule.POS_WIN).max()

    m = pd.DataFrame({
        "close": daily["close"].resample("ME").last(),
        "pe": daily["pe"].resample("ME").last(),
        "ma_dev": (daily["close"] / ma - 1).resample("ME").last(),
        "pos": ((daily["close"] - lo) / (hi - lo)).resample("ME").last(),
    })
    m["dy"] = dy.reindex(m.index, method="ffill")
    m["y10"] = y10.reindex(m.index, method="ffill")
    m["spread"] = m["dy"] * rule.TAX - m["y10"]
    # 记录真实数据日期：resample("ME") 会把未走完的当月标成月末，读数卡要用真实收盘日
    m.attrs["as_of"] = daily["close"].dropna().index[-1]
    return m


def pct_rank(s: pd.Series, min_n=rule.PCT_MIN_N, max_n=rule.PCT_MAX_N) -> pd.Series:
    """扩张窗口分位（最少 min_n、最多 max_n 个观测，含当前点）。"""
    v = s.to_numpy(dtype=float)
    out = np.full(len(v), np.nan)
    for i in range(len(v)):
        lo = max(0, i - max_n + 1)
        w = v[lo:i + 1]
        w = w[~np.isnan(w)]
        if len(w) >= min_n:
            out[i] = float((w < v[i]).mean())
    return pd.Series(out, index=s.index)


# ------------------------------------------------------------------ 温度
def add_temperature(m: pd.DataFrame, weights=None, signs=None) -> pd.DataFrame:
    """追加 4 个分位列与 temp(0-100)。任一分位缺失 → 该月温度缺失（不填补）。"""
    weights = weights or rule.W_EQ
    signs = signs or rule.SIGN
    m = m.copy()
    acc = pd.Series(0.0, index=m.index)
    wsum = 0.0
    for f in FACTORS:
        if f not in weights:
            continue
        m[f + "_p"] = pct_rank(m[f])
        col = m[f + "_p"] if signs[f] > 0 else (1 - m[f + "_p"])
        acc = acc.add(col.fillna(np.nan) * weights[f])
        wsum += weights[f]
    m["temp"] = acc / wsum * 100
    return m


def build(weights=None, signs=None, refresh=False) -> dict[str, pd.DataFrame]:
    """-> {指数代码: 月度面板（含 temp）}。"""
    y10 = _y10()
    dy = _dy_map()
    peg = _peg(rule.INDICES)
    out = {}
    for code in rule.INDICES:
        m = index_panel(code, y10, dy[code],
                        peg[code] if code in peg.columns else pd.Series(dtype=float),
                        refresh=refresh)
        out[code] = add_temperature(m, weights=weights, signs=signs)
    return out


def long_panel(panels=None, weights=None, signs=None, refresh=False) -> pd.DataFrame:
    """-> 长表 (date, code, name, temp, spread, pe, ma_dev, pos, 各分位, close)。"""
    panels = panels or build(weights=weights, signs=signs, refresh=refresh)
    rows = []
    for code, m in panels.items():
        d = m.loc[m["temp"].first_valid_index():] if m["temp"].notna().any() else m.iloc[0:0]
        for ts, r in d.iterrows():
            rows.append({
                "date": ts.strftime("%Y-%m-%d"), "code": code, "name": rule.INDICES[code],
                "temp": None if np.isnan(r["temp"]) else round(float(r["temp"]), 1),
                "spread": round(float(r["spread"]), 3),
                "pe": None if np.isnan(r["pe"]) else round(float(r["pe"]), 2),
                "ma_dev": None if np.isnan(r["ma_dev"]) else round(float(r["ma_dev"]), 4),
                "pos": None if np.isnan(r["pos"]) else round(float(r["pos"]), 3),
                **{f + "_p": (None if np.isnan(r[f + "_p"]) else round(float(r[f + "_p"]), 3))
                   for f in FACTORS},
                "close": round(float(r["close"]), 2),
            })
    return pd.DataFrame(rows)


if __name__ == "__main__":
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    pn = build()
    for code, m in pn.items():
        s = m["temp"].dropna()
        last = m.iloc[-1]
        print(f"{code:7s} {rule.INDICES[code]:8s} 温度 {last['temp']:5.1f} · "
              f"样本 {s.index[0]:%Y-%m}~{s.index[-1]:%Y-%m} ({len(s)} 月) · "
              f"历史 {s.min():.0f}~{s.max():.0f} · 当前分位 {(s < last['temp']).mean():.0%}")
