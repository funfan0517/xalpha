#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""V10 · 状态机引擎（idle / buy_signal / holding / sold）

状态转移
--------
```
idle ──(三条件共振)──► holding ──(收复前高 / 满 250 日 / 止损 / 条件失效)──► sold ──(冷静期结束)──► idle
                         │
                         └──(dy≥5% 或 dy≥6%/dd≥25%)──► holding（加批次）
```

执行口径
--------
* 信号在 **T 日收盘**算出，**T+1 日收盘**成交（不用 T 日收盘价成交，避免「收盘才算出来、
  又用同一收盘价成交」的隐性前视）；建仓价 = T+1 日收盘价，止损与卖点都对着它比。
* 前高在建仓日**锁定**（`peak_lock`），之后不随行情移动 —— 否则「收复前高」会变成
  「追着新高跑」，永远卖不出去。
* 仓位 `w` 在 **0–100%** 之间（这块资金当独立组合，不再受「占总资金 30%」约束）；
  空仓部分按现金（0 收益）计，不加杠杆。
* 成本：换手按 `|Δw| × FEE` 一次性扣；持仓按 `w × ETF_FEE_YEAR / 244` 逐日扣年费。
* **条件失效卖点（`exit_count`，默认关）**：把买点的三条条件逐日重新计分，剩几条低于阈值就卖，
  与「3/3 才买」对称。它排在止损之后、收复前高之前 —— 止损是风控、条件失效是「买它的理由没了」。
  开启后条件卖点几乎总比 250 日期限先到，**期限那条等于被删掉**（`research/_sell_lab_report.md §二·补`）。

产出（`run`）
-------------
`{"par", "bars", "equity", "bench", "expo", "trades", "open", "state"}`：
`bars` 是逐日信号表（报告与每日读数都读它，避免两条代码路径算出不同状态）。
"""
from __future__ import annotations

import os
import sys

import numpy as np
import pandas as pd

_DIR = os.path.dirname(os.path.abspath(__file__))
if _DIR not in sys.path:
    sys.path.insert(0, _DIR)

import rule  # noqa: E402  pylint: disable=wrong-import-position

TRADING_DAYS = 244

DEFAULTS = dict(
    dd_buy=rule.DD_BUY, dy_buy=rule.DY_BUY, dy_strong=rule.DY_STRONG,
    dy_extreme=rule.DY_EXTREME, dd_deep=rule.DD_DEEP,
    peak_win=rule.PEAK_WIN, ttl=rule.SELL_TTL, stop=rule.STOP_LOSS,
    max_pos=rule.MAX_POS, ladder=rule.LADDER, tranche=rule.TRANCHE,
    fee=rule.FEE, etf_fee_year=rule.ETF_FEE_YEAR,
    cool_sell=rule.COOL_SELL, cool_stop=rule.COOL_STOP,
    # ---- 卖点（已转正，默认即生效）----
    ttl_renew=rule.SELL_TTL_RENEW,   # 到期复核：三条件仍共振则续期（卖点与买点对称）
    ttl_max=rule.SELL_TTL_MAX,       # 续期总上限（交易日），只有 ttl_renew=True 时生效
    # ---- 研究开关（默认全关；打开后改变卖点行为，见 research/_sell_lab.py）----
    exit_ma=False,        # True：收盘站上年线即卖（「不再便宜」离场）
    exit_dy=None,         # 例 0.035：反推股息率回落到该值以下即卖
    trail=None,           # 例 0.10：自建仓后最高收盘回撤该比例即卖（移动止盈）
    exit_count=None,      # 三条件「还剩几条」低于该值即卖：
                          #   3 = 任一条失效就卖；2 = 两条失效才卖；1 = 三条全失效才卖
)

REASON = {"peak": "收复前高", "ttl": "满 250 日", "stop": "止损",
          "ma": "站上年线", "dy": "息率回落", "trail": "回撤止盈", "cond": "条件失效"}


def params(**kw) -> dict:
    p = dict(DEFAULTS)
    p.update({k: v for k, v in kw.items() if k in DEFAULTS})
    return p


def stats(eq: pd.Series, periods: int = TRADING_DAYS) -> dict:
    """净值序列 -> 年化 / 最大回撤 / Calmar / 波动 / 夏普 / 累计。"""
    s = eq.dropna()
    if s.empty:
        return {k: float("nan") for k in ("cagr", "dd", "calmar", "vol", "sharpe", "total")}
    yrs = max(len(s) / periods, 1e-9)
    total = float(s.iloc[-1] / s.iloc[0] - 1)
    cagr = (1 + total) ** (1 / yrs) - 1 if total > -1 else -1.0
    dd = float((s / s.cummax() - 1).min())
    r = s.pct_change().dropna()
    vol = float(r.std() * np.sqrt(periods))
    return {"cagr": cagr, "dd": dd, "calmar": cagr / abs(dd) if dd else float("nan"),
            "vol": vol, "sharpe": float(r.mean() * periods) / vol if vol else float("nan"),
            "total": total}


def run(df: pd.DataFrame, **kw) -> dict:
    """在日频面板上跑一遍状态机。"""
    par = params(**kw)
    d = df.dropna(subset=["close", "tr"]).copy()
    if par["peak_win"] != rule.PEAK_WIN:
        d["peak"] = d["close"].rolling(int(par["peak_win"]), min_periods=rule.PEAK_MIN).max()
        d["dd"] = 1 - d["close"] / d["peak"]

    close = d["close"].to_numpy(float)
    tr = d["tr"].to_numpy(float)
    peak = d["peak"].to_numpy(float)
    dd = d["dd"].to_numpy(float)
    dy = d["dy"].to_numpy(float)
    ma250 = d["ma250"].to_numpy(float)
    below = d["below_ma"].to_numpy(bool)
    rtr = np.concatenate([[0.0], tr[1:] / tr[:-1] - 1.0])
    n = len(d)
    fee_d = par["etf_fee_year"] / TRADING_DAYS

    eq = np.ones(n)
    expo = np.zeros(n)
    state_arr = np.empty(n, dtype=object)
    w = 0.0
    state = "idle"
    entry = entry_i = peak_lock = None
    stage = 0
    cool = 0
    cur = None
    pending = None
    trades = []
    hi = 0.0            # 建仓后最高收盘（移动止盈用）
    mfe = mae = 0.0     # 持仓期最大浮盈 / 最大浮亏（诊断用）
    t1, t2, t3 = par["tranche"]

    for i in range(n):
        if i:
            eq[i] = eq[i - 1] * (1 + w * (rtr[i] - fee_d))          # 计入当日收益与年费
        # ---- 执行 T-1 日挂单（按今日收盘价成交）----
        if pending is not None:
            act, pending = pending, None
            tw = float(act["w"])
            dw = tw - w
            if abs(dw) > 1e-12:
                eq[i] *= (1 - abs(dw) * par["fee"])                 # 交易成本
            if act["kind"] == "enter":
                state, entry, entry_i, peak_lock = "holding", close[i], i, peak[i]
                stage = 1
                hi, mfe, mae = close[i], 0.0, 0.0
                cur = {"entry_date": d.index[i], "entry_price": entry, "peak": peak_lock,
                       "dy_entry": dy[i], "dd_entry": dd[i], "stage": 1, "max_w": tw,
                       "adds": [], "entry_i": i}
            elif act["kind"] == "add":
                stage = act["stage"]
                cur["stage"] = stage
                cur["max_w"] = max(cur["max_w"], tw)
                cur["adds"].append({"date": d.index[i], "stage": stage, "w": tw,
                                    "price": close[i], "dy": dy[i]})
            elif act["kind"] == "exit":
                ret = tr[i] / tr[entry_i] - 1                       # 含息、不含费
                cur.update(exit_date=d.index[i], exit_price=close[i], reason=act["reason"],
                           days=i - entry_i, ret=float(ret), exit_i=i,
                           hi=float(hi), mfe=float(mfe), mae=float(mae),
                           ret_net=float((1 + ret) * (1 - par["fee"] * cur["max_w"]) ** 2 - 1))
                if "cond_left" in act:                              # 条件失效卖出：记录剩几条
                    cur["cond_left"] = act["cond_left"]
                trades.append(cur)
                state, entry, entry_i, peak_lock, stage, cur = "sold", None, None, None, 0, None
                hi, mfe, mae = 0.0, 0.0, 0.0
                cool = i + int(par["cool_stop"] if act["reason"] == "stop" else par["cool_sell"])
            w = tw
        expo[i] = w
        state_arr[i] = state

        # ---- 用今日收盘生成下一日挂单 ----
        ok = bool(np.isfinite(dd[i]) and np.isfinite(dy[i]))
        # 三条买点条件逐条计分：① 回撤够深 ② 破年线/三年线 ③ 股息率够高
        c_dd = ok and bool(dd[i] >= par["dd_buy"])
        c_ma = ok and bool(below[i])
        c_dy = ok and bool(dy[i] >= par["dy_buy"])
        n_cond = int(c_dd) + int(c_ma) + int(c_dy)
        resonance = n_cond == 3
        if state in ("idle", "sold") and resonance and i >= cool:
            target = par["max_pos"] * (t1 if par["ladder"] else 1.0)
            pending = {"kind": "enter", "w": target}
        elif state == "holding":
            hi = max(hi, close[i])
            move = close[i] / entry - 1
            mfe, mae = max(mfe, move), min(mae, move)
            if cur is not None:
                cur.update(hi=float(hi), mfe=float(mfe), mae=float(mae))
            if close[i] <= entry * (1 - par["stop"]):
                pending = {"kind": "exit", "w": 0.0, "reason": "stop"}
            elif par["exit_count"] is not None and ok and n_cond < int(par["exit_count"]):
                # 卖点与买点对称：买入要求 3/3 共振，卖出看「还剩几条」
                #   exit_count=3 → 破 1 条就走；=2 → 破 2 条才走；=1 → 三条全破才走
                pending = {"kind": "exit", "w": 0.0, "reason": "cond", "cond_left": n_cond}
            elif close[i] >= peak_lock:
                pending = {"kind": "exit", "w": 0.0, "reason": "peak"}
            elif par["exit_ma"] and np.isfinite(ma250[i]) and close[i] >= ma250[i]:
                pending = {"kind": "exit", "w": 0.0, "reason": "ma"}
            elif par["exit_dy"] is not None and np.isfinite(dy[i]) and dy[i] <= par["exit_dy"]:
                pending = {"kind": "exit", "w": 0.0, "reason": "dy"}
            elif par["trail"] and close[i] <= hi * (1 - par["trail"]):
                pending = {"kind": "exit", "w": 0.0, "reason": "trail"}
            elif i - entry_i >= par["ttl"]:
                # 到期但「仍然便宜」（三条件仍共振）→ 续期，而不是机械卖出；
                # 卖点与买点对称：贵了/回撤修复了才走。ttl_max 防止无限期持有。
                renew = (par["ttl_renew"] and resonance
                         and (i - entry_i) < par["ttl_max"])
                if not renew:
                    pending = {"kind": "exit", "w": 0.0, "reason": "ttl"}
            elif par["ladder"] and stage < 3 and resonance:
                if stage == 1 and dy[i] >= par["dy_strong"]:
                    pending = {"kind": "add", "w": par["max_pos"] * (t1 + t2), "stage": 2}
                elif stage == 2 and (dy[i] >= par["dy_extreme"] or dd[i] >= par["dd_deep"]):
                    pending = {"kind": "add", "w": par["max_pos"], "stage": 3}

    # 末日仍在持仓 → 记为未平仓交易（不编造退出价）
    if cur is not None:
        ret = tr[-1] / tr[entry_i] - 1
        cur.update(exit_date=None, exit_price=None, reason="持仓中", days=n - 1 - entry_i,
                   ret=float(ret), ret_net=float(ret - par["fee"] * (1 + cur["max_w"]) * 2),
                   hi=float(hi), mfe=float(mfe), mae=float(mae), open=True)
    open_pos = cur

    bench = np.ones(n)
    for i in range(1, n):
        bench[i] = bench[i - 1] * (1 + rtr[i] - fee_d)

    bars = pd.DataFrame({
        "close": close, "ma250": d["ma250"].to_numpy(float), "ma750": d["ma750"].to_numpy(float),
        "peak": peak, "dd": dd, "dy": dy, "below_ma": below,
        "buy": [(bool(np.isfinite(dd[i]) and np.isfinite(dy[i]))
                 and dd[i] >= par["dd_buy"] and below[i] and dy[i] >= par["dy_buy"]) for i in range(n)],
        "strong": [bool(np.isfinite(dy[i]) and dy[i] >= par["dy_strong"]) for i in range(n)],
        "extreme": [bool(np.isfinite(dy[i]) and dy[i] >= par["dy_extreme"]) for i in range(n)],
        "state": state_arr, "expo": expo, "equity": eq, "bench": bench,
    }, index=d.index)

    return {"par": par, "bars": bars, "equity": pd.Series(eq, index=d.index),
            "bench": pd.Series(bench, index=d.index), "expo": pd.Series(expo, index=d.index),
            "trades": trades, "open": open_pos, "state": state,
            "entry_i": entry_i, "entry_price": entry, "peak_lock": peak_lock, "stage": stage}


def next_actions(res: dict, asof: pd.Timestamp | None = None) -> dict:
    """当前状态下「下一步看什么」——每日读数直接展示这三条线。"""
    bars = res["bars"]
    asof = bars.index[-1] if asof is None else asof
    row = bars.loc[asof]
    par = res["par"]
    out = {"asof": asof, "state": row["state"], "expo": float(row["expo"]),
           "close": float(row["close"]), "dd": float(row["dd"]), "dy": float(row["dy"]),
           "below_ma": bool(row["below_ma"]), "peak": float(row["peak"]),
           "buy": bool(row["buy"]), "strong": bool(row["strong"]), "extreme": bool(row["extreme"])}
    okr = bool(np.isfinite(row["dd"]) and np.isfinite(row["dy"]))
    # 三条件还剩几条（买点用 3/3，卖点用 exit_count 时可对照读数）
    out["cond_count"] = (int(okr and row["dd"] >= par["dd_buy"])
                         + int(okr and bool(row["below_ma"]))
                         + int(okr and row["dy"] >= par["dy_buy"]))
    if row["state"] == "holding" and res["open"]:
        cur = res["open"]
        entry_i = int(bars.index.get_loc(cur["entry_date"]))
        i = int(bars.index.get_loc(asof))
        out.update(entry_date=cur["entry_date"], entry_price=cur["entry_price"],
                   peak_lock=cur["peak"],
                   stop_price=cur["entry_price"] * (1 - par["stop"]),
                   ttl_due=bars.index[min(entry_i + par["ttl"], len(bars) - 1)],
                   days_held=i - entry_i, stage=cur["stage"],
                   next_add=(par["dy_strong"] if cur["stage"] == 1 else
                             (par["dy_extreme"] if cur["stage"] == 2 else None)))
    return out


if __name__ == "__main__":
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    import factors  # pylint: disable=import-outside-toplevel

    pn = factors.build(refresh="--refresh" in sys.argv)
    for code, d in pn.items():
        d = d.loc[d.index >= pd.Timestamp(rule.BT_START)]      # 与回测同窗口
        res = run(d)
        st = stats(res["equity"])
        bs = stats(res["bench"])
        na = next_actions(res)
        print(f"[v10:engine] {code} {rule.BY_INDEX[code]['name']:<10s} 状态 {res['state']:<7s} "
              f"策略 Calmar {st['calmar']:.2f} / 基准 {bs['calmar']:.2f} · "
              f"交易 {len(res['trades'])} 笔 · 当前仓位 {na['expo']:.0%}")
