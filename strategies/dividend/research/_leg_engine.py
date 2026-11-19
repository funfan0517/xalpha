# -*- coding: utf-8 -*-
r"""红利「单标的」研究共用引擎（每只基金 = 一条腿）

`research/best_by_fund/` 与 `research/beat_best/` 两个课题共用本模块，
职责与 vol_legs 的 `research/_plot_leg.py` 相同：只放**数据 + 原语 + 同尺模拟**，
不放结论、不写产物。

为什么需要它
------------
红利家族的 V2–V9 大多是**组合规则**（横截面 top2 × 板块时间倍率），
每个版本各写一份「单标的版」会让口径漂移。本模块把单标的口径钉死在一处：

* 收益源：基金**累计净值** `totvalue`（`pool.load_navs`，含分红再投）
* 频率：**月末信号 → 次月生效**（`mult.shift(1)`），不猜月内路径
* 成本：单边 0.03%/次，按 Σ|Δw| 计（与 `_single_signal.FEE` 同一把尺）
* 空仓：**不计息**（`CASH = 0`）—— 与家族 `_single_signal.simulate` 一致。
  这会让择时类每年少赚「空仓比例 × 货基利率」，是**偏保守**的口径；
  表里同时给平均仓位，读者可自行折算（约 `(1−expo) × 货基年化` 的量级）。
* 绩效：与 `_single_signal.stats` 同式（年化 / 最大回撤 / Calmar / 波动 / 夏普），
  夏普按 `月均 × 12 / 年化波动`，无风险利率取 0。

信号原语（全部「越高越贵」）
--------------------------
| 原语 | 计算 | 用于 |
|---|---|---|
| `dy` | 指数月度股息率（`data/_div_yield_m.csv`） | V3 |
| `spread` | 股息率 − 10Y 中债（月末） | V5 / V1 |
| `ratio` | 股息率 ÷ 10Y 中债 | V6 |
| `rsi14` / `rsi12` | 基金净值的 Wilder RSI，日频算、月末取值 | V4 / V8 |

股息率三兄弟做**扩张窗口分位**（最少 36 月、上限 120 月，`_single_signal.pct_rank`），
再取 `q = 1 − 分位`（0 = 最便宜）。RSI 直接用绝对刻度 `q = RSI/100`。

覆盖缺口（如实标注，不补假数据）
------------------------------
池内 3 只**没有股息率月度历史**（`008163` 标普红利低波50、`005125` 标普红利机会、
`018388` 港股通高股息）→ 它们只有 BH / V4 / V8 三个候选，估值类一律「无源」。
"""

from __future__ import annotations

import os
import sys

import numpy as np
import pandas as pd

_HERE = os.path.dirname(os.path.abspath(__file__))      # .../dividend/research
_STRAT = os.path.dirname(_HERE)                         # .../dividend
for _p in (_HERE, _STRAT):
    if _p not in sys.path:
        sys.path.insert(0, _p)

import _single_signal as S  # noqa: E402  pylint: disable=wrong-import-position
import pool  # noqa: E402  pylint: disable=wrong-import-position

FEE = S.FEE                    # 单边 0.03%
CASH = 0.0                     # 空仓不计息（与家族口径一致）
MIN_YEARS = 3.0                # 排名下限：不到 3 年的样本只列不选

# 有股息率月度历史的标的（与 `_single_signal.POOL` 完全一致，勿另写一份）
YIELD_CODES = {c for c, _, _ in S.POOL}

LEGS = [dict(code=r["code"], name=r["name"], cat=r["cat"], idx=r["idx_name"],
             yield_ok=r["code"] in YIELD_CODES)
        for r in pool.POOL]
LEG = {r["code"]: r for r in LEGS}
CODES = [r["code"] for r in LEGS]
NAME = {r["code"]: r["name"] for r in LEGS}


# ================================================================ 数据（带缓存）
_C = {}


def navs() -> pd.DataFrame:
    if "navs" not in _C:
        _C["navs"] = pool.load_navs()
    return _C["navs"]


def monthly_nav(code: str) -> pd.Series:
    """月末累计净值；首月（不完整）保留但由调用方按窗口裁掉。"""
    s = navs()[code].dropna()
    return s.resample("ME").last().dropna()


def monthly_rets(code: str) -> pd.Series:
    return monthly_nav(code).pct_change().dropna()


def _panel(kind: str) -> pd.DataFrame:
    """dy / spread / ratio 的月末宽表（date × code），只覆盖 7 只。"""
    key = f"panel::{kind}"
    if key not in _C:
        _C[key] = S.load_signal(kind)
    return _C[key]


def _pct(kind: str) -> pd.DataFrame:
    key = f"pct::{kind}"
    if key not in _C:
        _C[key] = S.pct_rank(_panel(kind))
    return _C[key]


def dy_pct(code: str) -> pd.Series:
    """股息率分位（0 = 历史最低 = 最便宜）。"""
    return _pct("dy")[code].dropna() if code in _panel("dy").columns else pd.Series(dtype=float)


def spread_pct(code: str) -> pd.Series:
    return _pct("spread")[code].dropna() if code in _panel("spread").columns else pd.Series(dtype=float)


def ratio_pct(code: str) -> pd.Series:
    return _pct("ratio")[code].dropna() if code in _panel("ratio").columns else pd.Series(dtype=float)


def rsi_monthly(code: str, window: int = 14) -> pd.Series:
    """日频 Wilder RSI → 月末取值（与 V4/V8 的决策点一致）。"""
    key = f"rsi{window}::{code}"
    if key not in _C:
        nav = navs()[code].dropna()
        _C[key] = S.rsi_series(nav, window).resample("ME").last().dropna()
    return _C[key]


def ma_monthly(code: str, n: int) -> pd.Series:
    """日频 N 日均线 → 月末取值（只在「价格 vs 均线」里用）。"""
    key = f"ma{n}::{code}"
    if key not in _C:
        nav = navs()[code].dropna()
        _C[key] = nav.rolling(n).mean().resample("ME").last().dropna()
    return _C[key]


# ================================================================ 仓位倍率原语
def mult_three(q: pd.Series, q_out: float, q_half: float) -> pd.Series:
    """三档：q ≥ q_out 清仓 / ≥ q_half 减半 / 其余满仓（V3–V6 的标准形态）。"""
    return S.time_mult(q, q_out, q_half)


def mult_band(pct: pd.Series, buy: float = 0.70, sell: float = 0.20) -> pd.Series:
    """V1 的分位带：≥buy 满仓、<sell 清仓、中间**保持原仓位**（不带保险丝）。"""
    out = pd.Series(np.nan, index=pct.index, dtype="float64")
    cur = 1.0                     # 期初满仓（与 v1_spread/backtest.py 一致）
    for ts, v in pct.items():
        if np.isfinite(v):
            if v >= buy:
                cur = 1.0
            elif v < sell:
                cur = 0.0
        out.loc[ts] = cur
    return out


# ================================================================ 同尺模拟
def simulate(mult: pd.Series, rets: pd.Series) -> pd.Series:
    """`mult[t]` = t 月末决策 → t+1 月生效。首月丢弃（没有可执行的下一月）。

    成本 = Σ|Δw| × 单边费率；空仓收益 0（`CASH=0`）。
    """
    m = mult.reindex(rets.index).astype(float)
    pos = m.shift(1)
    pos.iloc[0] = 0.0
    turn = (pos - pos.shift(1).fillna(0.0)).abs()
    r = (pos * rets - turn * FEE).iloc[1:]
    return r.dropna()


def stats(r: pd.Series, mult: pd.Series = None) -> dict:
    """家族口径绩效 + 平均仓位 / 调仓次数。"""
    st = dict(S.stats(r))
    if mult is not None:
        m = mult.reindex(r.index).astype(float)
        st["expo"] = float(m.mean())
        st["trades"] = int((m.diff().abs() > 1e-9).sum())
        st["yrs"] = st["n"] / 12.0
    else:
        st["expo"], st["trades"], st["yrs"] = np.nan, 0, st["n"] / 12.0
    return st


def fmt_pct(x, nd: int = 2, sign: bool = False) -> str:
    if x is None or (isinstance(x, float) and not np.isfinite(x)):
        return "—"
    return f"{x:+.{nd}%}" if sign else f"{x:.{nd}%}"


def fmt2(x) -> str:
    if x is None or (isinstance(x, float) and not np.isfinite(x)):
        return "—"
    return f"{x:.2f}"


# ================================================================ 候选注册（best_by_fund 用）
# needs 决定可得性：无股息率历史的标的只能跑 rsi / bh
CANDIDATES = [
    dict(key="BH", label="买入持有（基准）", needs=None, bench=True,
         note="这条腿的基金累计净值全窗口；所有择时的及格线"),
    dict(key="V1", label="V1 利差分位 70/20（带）", needs="spread",
         note="分位 ≥70% 满仓、<20% 清仓、中间保持；不带保险丝"),
    dict(key="V3", label="V3 股息率分位 80/50", needs="dy",
         note="股息率分位贵度 ≥0.80 清仓 / ≥0.50 减半"),
    dict(key="V4", label="V4 RSI14 三档 70/50", needs="rsi14",
         note="家族现行口径；RSI 用绝对刻度，不分位化"),
    dict(key="V5", label="V5 股债利差分位 80/50", needs="spread",
         note="利差 = 股息率 − 10Y 中债，扩张窗口分位"),
    dict(key="V6", label="V6 股债收益比分位 80/50", needs="ratio",
         note="收益比 = 股息率 ÷ 10Y 中债"),
    dict(key="V8", label="V8 RSI12 三档 80/50", needs="rsi12",
         note="V4 的参数孪生（12 日 + 80 清仓）"),
]
CAND = {c["key"]: c for c in CANDIDATES}


def _series(key: str, code: str) -> pd.Series:
    """候选 -> 月末仓位倍率（未 shift，尚未生效）。"""
    if key == "BH":
        return pd.Series(1.0, index=monthly_nav(code).index)
    if key == "V1":
        return mult_band(spread_pct(code), 0.70, 0.20)
    if key == "V3":
        p = dy_pct(code)
        return mult_three(1.0 - p, 0.80, 0.50)
    if key == "V5":
        p = spread_pct(code)
        return mult_three(1.0 - p, 0.80, 0.50)
    if key == "V6":
        p = ratio_pct(code)
        return mult_three(1.0 - p, 0.80, 0.50)
    if key == "V4":
        return mult_three(rsi_monthly(code, 14) / 100.0, 0.70, 0.50)
    if key == "V8":
        return mult_three(rsi_monthly(code, 12) / 100.0, 0.80, 0.50)
    raise KeyError(f"未知候选: {key}")


VALUATION_NEEDS = {"dy", "spread", "ratio"}


def available_keys(code: str) -> list[str]:
    """该标的可跑的候选（无股息率历史 → 只有 BH / V4 / V8）。"""
    ok = LEG[code]["yield_ok"]
    return [c["key"] for c in CANDIDATES
            if c["needs"] not in VALUATION_NEEDS or ok]


def leg_window(code: str, keys: list[str]) -> tuple[pd.Timestamp, pd.Timestamp]:
    """该标的在给定候选集下的**共同窗口**：所有候选都有信号的交集 + 净值覆盖。

    同一只标的所有候选共用同一个窗口，排名才是同尺的。
    """
    nav = monthly_nav(code)
    start, end = nav.index[0], nav.index[-1]
    rets = nav.pct_change().dropna()
    start = max(start, rets.index[0])
    for k in keys:
        s = _series(k, code).dropna()
        if s.empty:
            continue
        start = max(start, s.index[0])
        end = min(end, s.index[-1])
    return start, end


def run_leg(code: str, key: str, start: pd.Timestamp = None,
            end: pd.Timestamp = None) -> dict:
    """跑一只标的一个候选 -> 指标 dict（含收益序列与倍率，便于组合层复用）。"""
    key_ = key
    win = leg_window(code, available_keys(code))
    start = win[0] if start is None else max(start, win[0])
    end = win[1] if end is None else min(end, win[1])
    mult = _series(key_, code).reindex(pd.date_range(start, end, freq="ME")).ffill()
    rets = monthly_rets(code).loc[start:end]
    r = simulate(mult, rets)
    st = stats(r, mult.reindex(r.index))
    st.update(code=code, key=key_, leg=LEG[code], start=r.index[0], end=r.index[-1],
              r=r, mult=mult)
    return st


if __name__ == "__main__":
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    print(f"池内 {len(CODES)} 只（有股息率历史 {len(YIELD_CODES)} 只）· 单边费 {FEE:.2%} · 空仓计息 {CASH:.2%}")
    for code in CODES:
        keys = available_keys(code)
        st, en = leg_window(code, keys)
        print(f"  {code} {NAME[code][:18]:18s} 候选 {len(keys)} 个 · "
              f"{st:%Y-%m}~{en:%Y-%m} ({(en - st).days / 365.25:.1f}y)")
        for k in keys:
            if k == "BH":
                continue
            d = run_leg(code, k)
            print(f"      {k}: 年化 {d['cagr']:+.2%} 回撤 {d['dd']:.2%} "
                  f"夏普 {d['sharpe']:.2f} 仓位 {d['expo']:.0%} 调仓 {d['trades']}")
