#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""V11 · 引擎：仓位倍率的改造族 + 统一评估

所有闸门只作用于**仓位倍率**（V4 的时间维度），横截面权重不动：

    m_rsi  = 1 / 0.5 / 0      （板块 RSI 均值 <50 / 50–70 / ≥70）
    m_cold = m_rsi，但「减半档且板块温度 ≤ cold」→ 1        （冷 = 允许拿满）
    m_hot  = m_rsi，但「满仓且板块温度 ≥ hot」→ cap(0.5)     （热 = 压到减半）

温度缺失（早期月份）时一律保持 m_rsi，不做插补 —— 插补等于凭空造信号。
"""
from __future__ import annotations

import numpy as np
import pandas as pd

import rule


# ------------------------------------------------------------------ 倍率族
def mult_rsi(bq: pd.Series, q_out: float = rule.Q_OUT, q_half: float = rule.Q_HALF) -> pd.Series:
    m = pd.Series(1.0, index=bq.index)
    m[bq >= q_half] = 0.5
    m[bq >= q_out] = 0.0
    m[bq.isna()] = 1.0
    return m


def gate_cold(m: pd.Series, temp: pd.Series, cold: float) -> pd.Series:
    out = m.copy()
    hit = (m > 0.01) & (m < 0.99) & (temp <= cold)
    out[hit] = 1.0
    return out


def gate_hot(m: pd.Series, temp: pd.Series, hot: float, cap: float = rule.CAP_HOT) -> pd.Series:
    out = m.copy()
    hit = (temp >= hot) & (m > cap)
    out[hit] = cap
    return out


def mult_temp_only(temp: pd.Series, cold: float, hot: float, mid: float = 0.5) -> pd.Series:
    """纯温度择时（不用 RSI）：冷 → 满仓 / 中间 → 半仓 / 热 → 空仓。用来证明「温度自己更差」。"""
    m = pd.Series(mid, index=temp.index)
    m[temp <= cold] = 1.0
    m[temp >= hot] = 0.0
    m[temp.isna()] = 1.0
    return m


def mult_tilt(m: pd.Series, temp: pd.Series, t0: float, lam: float) -> pd.Series:
    """连续版：m × (1 + λ(T0 − T)/100)，夹在 0.4–1.4 再截到 0–1。"""
    f = (1 + lam * (t0 - temp) / 100.0).clip(0.4, 1.4).fillna(1.0)
    return (m * f).clip(0.0, 1.0)


# ------------------------------------------------------------------ 评估
def weights(w0: pd.DataFrame, m: pd.Series) -> pd.DataFrame:
    return w0.mul(m.reindex(w0.index).fillna(1.0), axis=0)


def sim(w0: pd.DataFrame, m: pd.Series, rets: pd.DataFrame, fee: float = rule.FEE) -> pd.Series:
    w = weights(w0, m)
    wl = w.shift(1).fillna(0.0)
    port = (wl * rets).sum(axis=1, min_count=1)
    turn = (wl - wl.shift(1).fillna(0.0)).abs().sum(axis=1)
    return (port - turn * fee).dropna()


def metrics(r: pd.Series) -> dict:
    nav = (1 + r).cumprod()
    yrs = max(len(r) / 12.0, 1e-9)
    cagr = nav.iloc[-1] ** (1 / yrs) - 1
    dd = float((nav / nav.cummax() - 1).min())
    vol = float(r.std() * np.sqrt(12))
    return {"cagr": cagr, "dd": dd, "calmar": cagr / abs(dd) if dd else float("nan"),
            "vol": vol, "sharpe": float(r.mean() * 12) / vol if vol else float("nan"),
            "total": float(nav.iloc[-1] - 1), "n": len(r)}


def evaluate(w0: pd.DataFrame, m: pd.Series, rets: pd.DataFrame) -> dict:
    r = sim(w0, m, rets)
    st = metrics(r)
    st.update({"r": r, "m": m, "pos": float(m.mean()),
               "switches": int((m.diff().abs() > 1e-9).sum())})
    return st


def yearly(sims: dict) -> pd.DataFrame:
    df = pd.DataFrame(sims)
    return (1 + df).groupby(df.index.year).prod() - 1


def drop_best(r: pd.Series, base: pd.Series, n: int) -> pd.Series:
    """剔除「相对 base 超额最大」的 n 个月。"""
    diff = (r - base).dropna()
    return r.drop(diff.nlargest(n).index)


def perf(r: pd.Series) -> str:
    st = metrics(r)
    return f"{st['cagr'] * 100:5.1f}% {st['dd'] * 100:6.1f}% {st['calmar']:5.2f}"


# ------------------------------------------------------------------ 诊断
def band_stats(m_rsi: pd.Series, temp: pd.Series, port: pd.Series) -> dict:
    """减半档内：温度 → 下月收益 的相关与 t 值（闸门有没有判别力的直接证据）。"""
    half = (m_rsi > 0.01) & (m_rsi < 0.99)
    d = pd.DataFrame({"temp": temp, "fwd": port.shift(-1)}).loc[half].dropna()
    n = len(d)
    if n < 5:
        return {"n": n, "r": float("nan"), "t": float("nan")}
    r = float(d["temp"].corr(d["fwd"]))
    t = r * np.sqrt(n - 2) / np.sqrt(1 - r ** 2) if abs(r) < 1 else float("inf")
    return {"n": n, "r": r, "t": t}


def trigger_months(m: pd.Series, base_m: pd.Series) -> pd.Index:
    return m.index[(m - base_m).abs() > 1e-9]


def perm_test(m_gate: pd.Series, m_base: pd.Series, w0: pd.DataFrame,
              rets: pd.DataFrame, n_perm: int = rule.N_PERM, seed: int = rule.SEED) -> dict:
    """把「放量的那些减半月」随机替换成同样多的其它减半月，看闸门 Calmar 落在零分布哪。"""
    rng = np.random.default_rng(seed)
    hit = (m_gate - m_base).abs() > 1e-9
    n_hit = int(hit.sum())
    cand = np.where((m_base.to_numpy() > 0.01) & (m_base.to_numpy() < 0.99))[0]
    if n_hit == 0 or len(cand) < n_hit:
        return {"n": n_hit, "calmar": float("nan"), "median": float("nan"),
                "p5": float("nan"), "p95": float("nan"), "pct": float("nan")}
    cal = []
    for _ in range(n_perm):
        m = m_base.copy()
        pick = rng.choice(cand, size=n_hit, replace=False)
        m.iloc[pick] = m_gate.iloc[pick]        # 用闸门给出的目标仓位（可能满仓/压仓）
        cal.append(metrics(sim(w0, m, rets))["calmar"])
    target = metrics(sim(w0, m_gate, rets))["calmar"]
    return {"n": n_hit, "calmar": target, "median": float(np.median(cal)),
            "p5": float(np.percentile(cal, 5)), "p95": float(np.percentile(cal, 95)),
            "pct": float(np.mean(np.array(cal) < target))}
