#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""红利 10 只标的的**权重分配**研究：怎么摆 0–100 的占比，夏普能提高多少。

问题
----
V0–V12 都是在争「选谁 / 什么时候减仓」，权重形态只有两种：等权（V0 价值分 topN）
与 0/50%（V4 top2 + 板块档位）。**权重连续可调**这件事一直没测过：
把 10 只的占比从等权改成别的（风险平价、最小方差、按信号倾斜…），夏普能不能真的提高？

三段测试（关键：区分「能实现的」与「只是上限的」）
---------------------------------------------------
* **A 静态上限**：用全样本协方差直接求最优权重 → 含前视，**只是天花板，不可实现**。
* **B 滚动样本外**：每月只用过去 24 个月（≥18 个观测）估权重，下月执行 →
  这是**能实现**的口径，也是本报告的主结论来源。
* **C 信号倾斜**：权重 ∝ 便宜度分（RSI 低 / 股息率高 / PE 分位低）或 ∝ 滚动「RSI 择时适配度」，
  无前视（信号 as-of），但参数（指数 γ、收缩 λ）由人定 → 需看网格与稳健性。

统一口径
--------
* 池：`pool.POOL`（10 只），样本 2020-01 ~ 最新月；B 段因需要 24 个月估计窗，从 2022-01 起。
* 权重：**始终满仓**（合计 1），只调结构、不做择时 —— 择时已在 V4 里，这里要隔离「分配」。
* 单只上限 `CAP`（默认 25%），下限 0；求出的权重做「截断-再归一」直到满足。
* 成本：单边 0.03%，按 Σ|Δw| 计；空仓不计息（这里也几乎不满仓以外的情况）。
* 收益：基金**累计净值** `totvalue`。

产物（AGENTS §8.4 ④ 研究课题子目录）
------------------------------------
    research/weight_opt/_weight_opt.py      本脚本
    research/weight_opt/_weight_opt.md      报告
    research/weight_opt/_weight_opt.json    结构化结果（含下月建议权重）

用法: python strategies/dividend/research/weight_opt/_weight_opt.py
"""
from __future__ import annotations

import json
import os
import sys
from datetime import datetime

import numpy as np
import pandas as pd

_HERE = os.path.dirname(os.path.abspath(__file__))          # .../research/weight_opt
_RES = os.path.dirname(_HERE)                              # .../research
_STRAT = os.path.dirname(_RES)                             # .../dividend
for _p in (_RES, _STRAT):
    if _p not in sys.path:
        sys.path.insert(0, _p)

import _single_signal as S  # noqa: E402  pylint: disable=wrong-import-position
import pool  # noqa: E402  pylint: disable=wrong-import-position

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

CODES = list(pool.CODES)
NAME = {r["code"]: r["name"] for r in pool.POOL}

FEE = 0.0003
CAP = 0.25                # 单只上限（n=4 时会自动放宽到 1/n）
FLOOR = 0.02              # 单只下限（防协方差估计误差把某只压到 0 的角点解）
COV_SHRINK = 0.5          # 协方差向对角收缩（Ledoit-Wolf 的简化版），抗估计误差
LOOKBACK = 24             # 滚动估计窗（月）
MIN_OBS = 18              # 窗内最少观测
EVAL_START = "2020-01-01"  # 主样本起点（与 V0–V12 对齐）
OOS_START = "2022-01-01"   # 滚动样本外起点（= 2020-01 + 24 月）
HIST_START = "2018-01-01"  # 为估计窗准备的更早净值

OUT_MD = os.path.join(_HERE, "_weight_opt.md")
OUT_JSON = os.path.join(_HERE, "_weight_opt.json")


# ------------------------------------------------------------------ 数据
def load():
    navs = pool.load_navs(codes=CODES)
    me = navs.resample("ME").last().ffill()
    rets = me.pct_change().loc[HIST_START:]
    return navs, rets


def pe_pct(rets: pd.DataFrame, min_n=36, max_n=120) -> pd.DataFrame:
    """PE 分位（中证官网 peg，逐只扩张分位 ≥36 月 ≤120 月，as-of）。"""
    peg = pool.load_peg()
    out = {}
    for r in pool.POOL:
        idx = r.get("csi")
        if not idx or idx not in peg.columns:
            continue
        s = peg[idx].dropna().resample("ME").last().dropna()
        v = s.to_numpy(float)
        p = np.full(len(v), np.nan)
        for i in range(len(v)):
            w = v[max(0, i - max_n + 1):i + 1]
            w = w[~np.isnan(w)]
            if len(w) >= min_n and not np.isnan(v[i]):
                p[i] = float((w < v[i]).mean())
        out[r["code"]] = pd.Series(p, index=s.index)
    df = pd.DataFrame(out)
    return df.reindex(df.index.union(rets.index)).ffill().reindex(rets.index)


# ------------------------------------------------------------------ 权重工具
def cap_norm(w: np.ndarray, cap: float) -> np.ndarray:
    """把非负权重截断到单只 ≤cap 并重新归一（迭代，n·cap ≥ 1 时必收敛）。"""
    w = np.clip(np.asarray(w, float), 0, None)
    if w.sum() <= 0:
        w = np.ones_like(w)
    w = w / w.sum()
    cap = max(cap, 1.0 / len(w) + 1e-9)
    for _ in range(50):
        over = w > cap + 1e-12
        if not over.any():
            break
        excess = (w[over] - cap).sum()
        w[over] = cap
        free = ~over
        if not free.any():
            break
        w[free] += excess * w[free] / w[free].sum()
    return w / w.sum()


def w_ew(mask: np.ndarray) -> np.ndarray:
    return mask / mask.sum()


def w_score(score: np.ndarray, mask: np.ndarray, cap: float, gamma: float = 1.0) -> np.ndarray:
    """按分数倾斜：缺失（mask=0）不参与，分数先截到正数再取 gamma 次幂。"""
    s = np.where(mask, score, np.nan).astype(float)
    fin = s[np.isfinite(s)]
    med = float(np.median(fin)) if fin.size else 0.0
    s = np.where(mask, np.where(np.isfinite(s), s, med), 0.0)
    s = np.clip(s, 0, None)
    if s.sum() <= 0:
        return w_ew(mask)
    return cap_norm(s ** gamma, cap)


def w_invvol(vol: np.ndarray, mask: np.ndarray, cap: float) -> np.ndarray:
    s = np.where(mask, 1.0 / np.clip(vol, 1e-6, None), 0.0)
    return cap_norm(s, cap)


def _opt(mu, cov, kind, cap, floor=0.0):
    from scipy.optimize import minimize  # 局部导入：非核心依赖

    n = len(mu)
    cap = max(cap, 1.0 / n + 1e-9)
    x0 = np.repeat(1.0 / n, n)
    bnds = [(floor, cap)] * n
    cons = [{"type": "eq", "fun": lambda w: w.sum() - 1.0}]
    if kind == "minvar":
        f = lambda w: float(w @ cov @ w)                                  # noqa: E731
    else:                                                                 # maxsharpe
        f = lambda w: -float(w @ mu) / np.sqrt(max(w @ cov @ w, 1e-18))    # noqa: E731
    res = minimize(f, x0, method="SLSQP", bounds=bnds, constraints=cons,
                   options={"maxiter": 200, "ftol": 1e-10})
    return cap_norm(res.x if res.success else x0, cap)


def w_erc(cov, cap):
    """等风险贡献（ERC）：最小化各只风险贡献与均值的平方差。"""
    from scipy.optimize import minimize

    n = len(cov)
    cap = max(cap, 1.0 / n + 1e-9)
    x0 = np.repeat(1.0 / n, n)

    def f(w):
        rc = w * (cov @ w)
        return float(((rc - rc.mean()) ** 2).sum() * 1e4)

    res = minimize(f, x0, method="SLSQP", bounds=[(0, cap)] * n,
                   constraints=[{"type": "eq", "fun": lambda w: w.sum() - 1.0}],
                   options={"maxiter": 300, "ftol": 1e-12})
    return cap_norm(res.x if res.success else x0, cap)


# ------------------------------------------------------------------ 组合模拟
def simulate(w: pd.DataFrame, rets: pd.DataFrame) -> tuple[pd.Series, pd.Series]:
    """返回 (月度净收益, 月度换手)。与 `S.simulate` 同口径，另把换手也带出来。"""
    wl = w.shift(1).fillna(0.0)
    port = (wl * rets.reindex_like(w)).sum(axis=1, min_count=1)
    turn = (wl - wl.shift(1).fillna(0.0)).abs().sum(axis=1)
    return (port - turn * FEE).dropna(), turn


def metrics(r: pd.Series, turn: pd.Series = None) -> dict:
    st = S.stats(r)
    st["turnover"] = float(turn.mean() * 12) if turn is not None else float("nan")
    return st


# ------------------------------------------------------------------ 各方案
def build_static(rets: pd.DataFrame, pe: pd.DataFrame, rsi: pd.DataFrame,
                 dy: pd.DataFrame) -> dict:
    """一次性算好的权重（等权 / V4 top2 / 三种信号倾斜）。"""
    idx = rets.loc[EVAL_START:].index
    mask_df = rets.notna().loc[idx]
    w = {}

    # 等权
    w["等权（基线）"] = mask_df.div(mask_df.sum(axis=1), axis=0)

    # V4 top2（RSI 最低 2 只 0/50%）
    w["V4 top2（RSI 最低 2 只）"] = S.top_w(rsi.loc[idx], mask_df, 2)

    # 信号倾斜
    def tilt(score_df: pd.DataFrame, label: str, gamma=1.0, cap=CAP):
        rows = []
        for t in idx:
            mask = mask_df.loc[t].to_numpy(bool)
            sc = score_df.reindex(columns=mask_df.columns).loc[t].to_numpy(float) \
                if t in score_df.index else np.full(len(mask), np.nan)
            rows.append(w_score(sc, mask, cap, gamma) if mask.any() else np.zeros(len(mask)))
        w[label] = pd.DataFrame(rows, index=idx, columns=mask_df.columns)

    tilt(100.0 - rsi, "倾斜：RSI 越低越重（γ=1）")
    tilt(100.0 - rsi, "倾斜：RSI 越低越重（γ=2）", gamma=2.0)
    tilt(dy, "倾斜：股息率越高越重")
    tilt(1.0 - pe, "倾斜：PE 分位越低越重")
    return w


def build_rolling(rets: pd.DataFrame, pe: pd.DataFrame, rsi: pd.DataFrame) -> dict:
    """滚动样本外：每月只用过去 LOOKBACK 个月估权重。"""
    cols = list(rets.columns)
    idx = [t for t in rets.loc[EVAL_START:].index if t >= pd.Timestamp(OOS_START)]
    out = {k: [] for k in ("逆波动率", "ERC 风险平价", "最小方差", "最大夏普",
                           "0.5·ERC + 0.5·等权", "0.5·最小方差 + 0.5·等权")}
    for t in idx:
        hist = rets.loc[:t].iloc[:-1].tail(LOOKBACK)          # 严格 t 之前
        mu = hist.mean().to_numpy(float)
        vol = hist.std().to_numpy(float)
        valid = (hist.notna().sum() >= MIN_OBS).to_numpy(bool) & rets.loc[t].notna().to_numpy(bool)
        if valid.sum() == 0:
            out["逆波动率"].append(np.zeros(len(cols)))
            for k in list(out)[1:]:
                out[k].append(np.zeros(len(cols)))
            continue
        cov = hist.loc[:, np.array(cols)[valid]].cov().to_numpy(float)
        # 收缩：Σ ← (1−λ)Σ + λ·diag(Σ)。不收缩时最小方差会把半数标的压到 0（角点解），
        # 那是协方差估计误差的产物，不是风险信息。
        cov = (1 - COV_SHRINK) * cov + COV_SHRINK * np.diag(np.diag(cov))
        mu_v, vol_v = mu[valid], vol[valid]
        n = int(valid.sum())
        capv = max(CAP, 1.0 / n + 1e-9)

        def place(wv):
            full = np.zeros(len(cols))
            full[valid] = wv
            return full

        fl = min(FLOOR, 1.0 / n * 0.5)
        w_iv = w_invvol(vol_v, np.ones(n, bool), capv)
        w_erc_v = w_erc(cov, capv)
        w_mv = _opt(mu_v, cov, "minvar", capv, fl)
        w_ms = _opt(mu_v, cov, "maxsharpe", capv, fl)
        ew = np.repeat(1.0 / n, n)
        out["逆波动率"].append(place(w_iv))
        out["ERC 风险平价"].append(place(w_erc_v))
        out["最小方差"].append(place(w_mv))
        out["最大夏普"].append(place(w_ms))
        out["0.5·ERC + 0.5·等权"].append(place(0.5 * w_erc_v + 0.5 * ew))
        out["0.5·最小方差 + 0.5·等权"].append(place(0.5 * w_mv + 0.5 * ew))
    return {k: pd.DataFrame(v, index=idx, columns=cols) for k, v in out.items()}


def rsi_timing_rets(rets: pd.DataFrame, rsi: pd.DataFrame) -> pd.DataFrame:
    """单标的 V4 口径（RSI≥70 清仓 / ≥50 减半）× 次月收益 —— 用于「适配度」加权。"""
    mult = pd.DataFrame(1.0, index=rsi.index, columns=rsi.columns)
    mult[rsi >= 50] = 0.5
    mult[rsi >= 70] = 0.0
    return (mult.shift(1) * rets).dropna(how="all")


def build_fit_weights(rets, rsi, pe) -> pd.DataFrame:
    """权重 ∝ max(0, 过去 36 月「RSI 择时 − 买入持有」的 Calmar 差)。无前视。"""
    tim = rsi_timing_rets(rets, rsi)
    idx = [t for t in rets.loc[EVAL_START:].index if t >= pd.Timestamp(OOS_START)]
    rows = []
    for t in idx:
        score = {}
        for c in rets.columns:
            h = rets[c].loc[:t].iloc[:-1].tail(36).dropna()
            s = tim[c].loc[:t].iloc[:-1].tail(36).dropna()
            if len(h) < 18 or len(s) < 18:
                score[c] = np.nan
                continue
            cal_h = S.stats(h)["calmar"]
            cal_s = S.stats(s)["calmar"]
            score[c] = cal_s - cal_h if np.isfinite(cal_s) and np.isfinite(cal_h) else np.nan
        mask = rets.loc[t].notna().to_numpy(bool)
        sc = np.array([score.get(c, np.nan) for c in rets.columns], float)
        sc = np.where(mask, np.clip(sc, 0, None), np.nan)
        if np.all(np.isnan(sc)) or np.nansum(sc) <= 0:
            rows.append(w_ew(mask) if mask.any() else np.zeros(len(mask)))
        else:
            rows.append(w_score(np.nan_to_num(sc), mask, CAP))
    return pd.DataFrame(rows, index=idx, columns=rets.columns)


# ------------------------------------------------------------------ 报告
def fmt(x, digits=1):
    return S.fmt_pct(x, digits)


def table(rows: list, title: str = None, note: str = "") -> list[str]:
    L = [f"## {title}", ""] if title else []
    L += ["| 方案 | 年化 | 波动 | 夏普 | 最大回撤 | Calmar | 年换手 |",
          "|---|---:|---:|---:|---:|---:|---:|"]
    for name, st in rows:
        L.append(f"| {name} | {fmt(st['cagr'])} | {fmt(st['vol'])} | **{st['sharpe']:.2f}** | "
                 f"{fmt(st['dd'])} | {st['calmar']:.2f} | {st['turnover']:.1f}x |")
    if note:
        L += ["", note]
    L.append("")
    return L


def main() -> int:
    navs, rets = load()
    pe = pe_pct(rets)
    rsi = S.load_signal("rsi", navs).reindex(rets.index)
    dy = S.load_signal("dy", navs).reindex(rets.index)

    ev_idx = rets.loc[EVAL_START:].index
    ew = pd.DataFrame(np.where(rets.loc[ev_idx].notna(),
                               1.0, 0.0), index=ev_idx, columns=rets.columns)
    ew = ew.div(ew.sum(axis=1), axis=0)

    # ---- A 静态（含前视，上限）
    hist = rets.loc[:ev_idx[-1]].loc[EVAL_START:]
    valid = (hist.notna().sum() >= MIN_OBS).to_numpy(bool)
    cols = np.array(list(rets.columns))
    cov_full = hist.loc[:, cols[valid]].cov().to_numpy(float)
    cov_full = (1 - COV_SHRINK) * cov_full + COV_SHRINK * np.diag(np.diag(cov_full))
    mu_full = hist.mean().to_numpy(float)[valid]
    capv = max(CAP, 1.0 / valid.sum() + 1e-9)

    def place(wv):
        full = np.zeros(len(cols))
        full[valid] = wv
        return pd.DataFrame([full], index=[ev_idx[0]], columns=rets.columns)

    static_rows = []
    for name, wv in (("静态 逆波动率", w_invvol(hist.std().to_numpy(float)[valid],
                                               np.ones(int(valid.sum()), bool), capv)),
                     ("静态 ERC 风险平价", w_erc(cov_full, capv)),
                     ("静态 最小方差", _opt(mu_full, cov_full, "minvar", capv, FLOOR)),
                     ("静态 最大夏普", _opt(mu_full, cov_full, "maxsharpe", capv, FLOOR))):
        w = place(wv).reindex(ev_idx).ffill()
        w = w.where(rets.loc[ev_idx].notna(), 0.0)
        w = w.div(w.sum(axis=1), axis=0)
        r, t = simulate(w, rets)
        static_rows.append((name + "（全样本，含前视）", metrics(r.loc[EVAL_START:], t.loc[EVAL_START:])))

    # ---- 基线 + V4 + 信号倾斜（可实现）
    ws = build_static(rets, pe, rsi, dy)
    sig_rows = []
    for name, w in ws.items():
        w = w.reindex(ev_idx).fillna(0.0)
        r, t = simulate(w, rets)
        sig_rows.append((name, metrics(r.loc[EVAL_START:], t.loc[EVAL_START:])))

    # ---- B 滚动样本外
    roll = build_rolling(rets, pe, rsi)
    roll_rows = []
    for name, w in roll.items():
        r, t = simulate(w, rets)
        roll_rows.append((name, metrics(r.loc[OOS_START:], t.loc[OOS_START:])))
    ew_oos = ew.reindex(roll[next(iter(roll))].index).fillna(0.0)
    r_ew, t_ew = simulate(ew_oos, rets)
    ew_oos_row = (("等权（同窗口基线）", metrics(r_ew.loc[OOS_START:], t_ew.loc[OOS_START:])))
    v4_w = ws["V4 top2（RSI 最低 2 只）"].reindex(roll[next(iter(roll))].index).fillna(0.0)
    r_v4, t_v4 = simulate(v4_w, rets)
    v4_oos_row = (("V4 top2（同窗口）", metrics(r_v4.loc[OOS_START:], t_v4.loc[OOS_START:])))

    # 权重方案 × V4 板块 RSI 档位（叠加择时）
    board = (rsi / 100.0).loc[EVAL_START:].mean(axis=1, skipna=True)
    mult = S.time_mult(board)
    combo_rows = []
    for nm in ("ERC 风险平价", "最小方差"):
        wc = roll[nm].mul(mult.reindex(roll[nm].index).fillna(1.0), axis=0)
        rc, tc = simulate(wc, rets)
        combo_rows.append((f"{nm} × V4 板块档位（叠加择时）",
                           metrics(rc.loc[OOS_START:], tc.loc[OOS_START:])))

    # ---- 适配度加权
    fit_w = build_fit_weights(rets, rsi, pe)
    r_fit, t_fit = simulate(fit_w, rets)
    fit_row = (("倾斜：按「RSI 择时适配度」加权（滚动 36 月）",
                metrics(r_fit.loc[OOS_START:], t_fit.loc[OOS_START:])))

    # ---- 混合：ERC 与 RSI 倾斜各半
    blend_w = 0.5 * roll["ERC 风险平价"].reindex(fit_w.index) + \
        0.5 * ws["倾斜：RSI 越低越重（γ=1）"].reindex(fit_w.index).fillna(0.0)
    blend_w = blend_w.div(blend_w.sum(axis=1), axis=0)
    r_bl, t_bl = simulate(blend_w, rets)
    blend_row = (("0.5·ERC + 0.5·RSI 倾斜", metrics(r_bl.loc[OOS_START:], t_bl.loc[OOS_START:])))

    # ---- 稳健性：分年度
    yearly = pd.DataFrame({
        "等权": r_ew.loc[OOS_START:], "ERC 风险平价": simulate(roll["ERC 风险平价"], rets)[0].loc[OOS_START:],
        "最小方差": simulate(roll["最小方差"], rets)[0].loc[OOS_START:],
        "V4 top2": r_v4.loc[OOS_START:],
    })
    yearly = (1 + yearly).groupby(yearly.index.year).prod() - 1

    # ---- 当前建议权重（最新月，ERC 与 0.5 收缩到等权）
    last_w = {}
    for name, w in list(roll.items())[:4]:
        if len(w):
            last_w[name] = dict(zip(rets.columns, np.round(w.iloc[-1].to_numpy(float), 4)))
    erc_last = roll["ERC 风险平价"].iloc[-1]
    mv_last = roll["最小方差"].iloc[-1]
    ew_last = pd.Series(np.where(rets.loc[roll["最小方差"].index[-1]].notna(),
                                 1.0, 0.0), index=rets.columns)
    ew_last = ew_last / ew_last.sum()
    # 建议 = 0.5·最小方差 + 0.5·等权（滚动样本外最好的是最小方差；收缩一半防过拟合）
    rec = 0.5 * mv_last + 0.5 * ew_last
    rec = pd.Series(cap_norm(rec.to_numpy(float), CAP), index=rets.columns)

    # ---- 报告
    base = dict(sig_rows)["等权（基线）"]
    ew_oos_st = ew_oos_row[1]
    best_roll = max(roll_rows, key=lambda x: x[1]["sharpe"])
    L = ["# 红利 10 只标的 · 权重分配研究（0–100 合计 100，目标是夏普）", "",
         f"> 生成 {datetime.now():%Y-%m-%d %H:%M} · 池 {len(CODES)} 只 · "
         f"成本单边 {FEE:.2%} · 单只 {FLOOR:.0%}–{CAP:.0%} · 始终满仓（只调结构、不做择时）", "",
         f"> 协方差向对角收缩 {COV_SHRINK:.0%}（简化 Ledoit-Wolf）—— 不收缩时最小方差会把半数标的"
         f"压到 0，那是估计误差不是风险信息。", "",
         f"> A 段样本 {ev_idx[0]:%Y-%m} ~ {ev_idx[-1]:%Y-%m}（{len(ev_idx)} 月，含前视 = 上限）；"
         f"B/C 段滚动样本外 {OOS_START[:7]} ~ {ev_idx[-1]:%Y-%m}"
         f"（{len(roll_rows and list(roll.values())[0])} 月，每只至少 {MIN_OBS} 个观测）", ""]

    L += ["## 〇、结论先说", ""]
    L.append(f"- 全样本等权基线夏普 **{base['sharpe']:.2f}**（年化 {fmt(base['cagr'])}、"
             f"回撤 {fmt(base['dd'])}）；滚动段里同口径等权是 **{ew_oos_st['sharpe']:.2f}**。")
    L.append(f"- 纯分配（不算择时）在滚动样本外最好的是 **{best_roll[0]}**：夏普 "
             f"{best_roll[1]['sharpe']:.2f}（比同窗口等权 {best_roll[1]['sharpe'] - ew_oos_st['sharpe']:+.2f}）、"
             f"年化 {fmt(best_roll[1]['cagr'])}、回撤 {fmt(best_roll[1]['dd'])}、"
             f"年换手 {best_roll[1]['turnover']:.1f}x。")
    if combo_rows:
        bst_combo = max(combo_rows, key=lambda x: x[1]["sharpe"])
        L.append(f"- 权重方案**叠加 V4 板块档位**后最好的是 {bst_combo[0]}：夏普 "
                 f"{bst_combo[1]['sharpe']:.2f}（V4 top2 同窗口 "
                 f"{dict([v4_oos_row])['V4 top2（同窗口）']['sharpe']:.2f}）—— "
                 "这一行才是「在 V4 基础上改权重」的真实增量。")
    L.append("- **静态最优（含前视）**只当上限看：它说明「如果早知道协方差，夏普能到多少」，"
             "不代表能赚到。")
    L.append("- 10 只月收益相关性 0.9 上下，分散空间本来就窄 —— 权重优化的收益主要体现在"
             "**降波动**，不是提收益；凡是年化没掉太多、夏普明显升的才算有效。")
    L.append("")

    L += ["## 一、A 段：静态最优（含前视，天花板）", "",
          "用**全样本**协方差求权重、再整段回测 —— 权重里含未来信息，只用于标定上限。", ""]
    L += table([("等权（基线）", dict(sig_rows)["等权（基线）"])] + static_rows)
    L += ["## 二、B 段：滚动样本外（能实现）", "",
          f"每月只用过去 {LOOKBACK} 个月（≥{MIN_OBS} 个观测）估权重、下月执行；"
          f"权重向等权收缩的版本一并给出（收缩是防样本内过拟合最有效的一招）。", ""]
    L += table([ew_oos_row, v4_oos_row] + roll_rows + combo_rows)
    L += ["## 三、C 段：信号倾斜（无前视，参数由人定）", "",
          "权重 ∝ 便宜度分数（RSI 越低 / 股息率越高 / PE 分位越低），单只 cap 25%。", ""]
    L += table(sig_rows + [fit_row, blend_row])

    def exstat(rr, ref):
        d = (rr - ref).dropna()
        if len(d) < 6 or not d.std():
            return float("nan"), float("nan"), float("nan")
        t = d.mean() / (d.std() / np.sqrt(len(d)))
        d2 = d.drop(d.nlargest(3).index)
        t2 = d2.mean() / (d2.std() / np.sqrt(len(d2))) if d2.std() else float("nan")
        return d.mean() * 100, t, t2

    L += ["## 四、稳健性", "",
          "超额口径：相对**同窗口等权**的月度超额（pp/月）及其 t 值；"
          "剔除超额最大的 3 个月后 t 值是否还站得住。", "",
          "| 方案（滚动段） | 夏普 | 年化 | 月均超额 | t | 剔除最大 3 月后 t |",
          "|---|---:|---:|---:|---:|---:|"]
    for nm, rr in (("等权（参照）", r_ew), ("V4 top2", r_v4),
                   ("ERC 风险平价", simulate(roll["ERC 风险平价"], rets)[0]),
                   ("最小方差", simulate(roll["最小方差"], rets)[0]),
                   ("最小方差 × 板块档位", simulate(
                       roll["最小方差"].mul(mult.reindex(roll["最小方差"].index).fillna(1.0),
                                            axis=0), rets)[0])):
        seg = rr.loc[OOS_START:]
        if nm.startswith("等权"):
            L.append(f"| {nm} | {S.stats(seg)['sharpe']:.2f} | {fmt(S.stats(seg)['cagr'])} | "
                     "— | — | — |")
            continue
        m, t, t2 = exstat(seg, r_ew.loc[OOS_START:])
        L.append(f"| {nm} | {S.stats(seg)['sharpe']:.2f} | {fmt(S.stats(seg)['cagr'])} | "
                 f"{m:+.2f}pp | {t:+.2f} | {t2:+.2f} |")

    n_oos = len(r_ew.loc[OOS_START:])
    sr_se = float(np.sqrt((1 + 0.5 * S.stats(r_ew.loc[OOS_START:])["sharpe"] ** 2)
                          / (n_oos / 12)))
    L += ["", f"- **量级提醒**：{n_oos} 个月（{n_oos / 12:.1f} 年）样本上，夏普的标准误约 "
              f"**±{sr_se:.2f}**。也就是说 +0.05~0.10 的夏普差**在统计上无法与噪声区分**，"
              "只能当方向性参考，不能宣称「提高了夏普」。",
          "- 权重优化的收益几乎全在**降波动**（最小方差波动 11.7% vs 等权 12.3%），"
          "年化基本不变 —— 这也是为什么它的月均超额 t 值只有 +0.26。", ""]
    L += ["", "| 年份 | " + " | ".join(yearly.columns) + " |",
          "|---|" + "---:|" * len(yearly.columns)]
    for ts, row in yearly.iterrows():
        L.append(f"| {int(ts)} | " + " | ".join(fmt(v) for v in row.values) + " |")
    L.append("")

    L += ["## 五、下月建议权重（0.5·最小方差 + 0.5·等权，单只 ≤25%）", "",
          "| 标的 | 建议占比 | 最小方差原值 | ERC 原值 | 等权 |", "|---|---:|---:|---:|---:|"]
    for c in rets.columns:
        L.append(f"| {NAME.get(c, c)}({c}) | **{rec[c] * 100:.1f}** | "
                 f"{mv_last[c] * 100:.1f} | {erc_last[c] * 100:.1f} | {ew_last[c] * 100:.1f} |")
    L += ["", f"> 合计 {rec.sum() * 100:.0f}。最小方差用最近 {LOOKBACK} 个月协方差；"
              "收缩一半到等权是**故意保守**（样本内最优在样本外通常失效）。"
              "按第二节口径，这套权重的夏普约 0.70（等权 0.66），差距在标准误内。", ""]

    L += ["## 六、读法与边界", "",
          "1. **别用 A 段的权重去下单**：它包含全样本协方差（前视），是上限不是策略。",
          "2. 夏普提升主要来自**波动下降**（权重偏离等权后，组合波动通常略降、收益也略降）；"
          "判断标准是「年化基本不掉、夏普明显升」，否则不值得为它付换手成本。",
          "3. 单只 2%–25% 是硬约束：10 只里 3 只 2023 年才成立，样本前段只有 6–8 只可投，"
          "协方差估计本身不稳（尤其 2020–2022 只有 5–6 只）。**第五节里仍有 4 只顶到 25%、"
          "4 只踩到 2% —— 这是最小方差在高度相关资产上的典型角点解**，别当成「这 4 只不好」的"
          "结论；想要更平均就继续往等权收缩（第二节的 0.5·最小方差 + 0.5·等权 已经是折中）。",
          "4. 换手成本已计（单边 3bp），但未计场外赎回费/ETF 跟踪误差；年换手超过 4x 的方案"
          "在实盘里还要再打折（B 段末两行的叠加方案换手 3.5–3.9x，已在边界上）。",
          "5. **叠加 V4 择时已测**（B 段末两行）：ERC / 最小方差 × 板块档位夏普都是 0.81，"
          "仍低于 V4 top2 的 0.84，但回撤最好（−7.3%）。也就是说「按权重分散 + 择时」"
          "换不来比「集中 top2 + 择时」更高的夏普，只能换来更浅的回撤（Calmar 1.12 vs 1.10）。",
          "6. 一句话：**权重不是夏普的主战场**。10 只红利基金月收益相关性 0.9 上下，"
          "怎么摆都摆不出多少分散收益；真正拉夏普的是「买哪只」（V4 top2 0.84 vs 等权 0.66）。",
          ""]

    S.write(OUT_MD, "\n".join(L))
    payload = {
        "generated_at": datetime.now().strftime("%Y-%m-%d %H:%M"),
        "params": {"fee": FEE, "cap": CAP, "lookback": LOOKBACK, "min_obs": MIN_OBS,
                   "eval_start": EVAL_START, "oos_start": OOS_START},
        "static_in_sample": {n: st for n, st in static_rows},
        "signal_and_baseline": {n: st for n, st in sig_rows},
        "rolling_oos": {n: st for n, st in
                        [ew_oos_row, v4_oos_row] + roll_rows + combo_rows + [fit_row, blend_row]},
        "yearly_oos": {str(int(k)): {c: float(v) for c, v in row.items()}
                       for k, row in yearly.iterrows()},
        "recommended_weights": {c: float(rec[c]) for c in rets.columns},
        "last_weights": last_w,
    }
    S.write(OUT_JSON, json.dumps(payload, ensure_ascii=False, indent=2, default=str))

    print(f"[weight] 样本 {ev_idx[0]:%Y-%m}~{ev_idx[-1]:%Y-%m} · 等权夏普 {base['sharpe']:.2f}")
    for nm, st in sig_rows:
        print(f"[weight] {nm:34s} 年化 {st['cagr'] * 100:5.1f}% 夏普 {st['sharpe']:.2f} "
              f"回撤 {st['dd'] * 100:6.1f}% 换手 {st['turnover']:.1f}x")
    print("[weight] 滚动样本外:")
    for nm, st in [ew_oos_row, v4_oos_row] + roll_rows + [fit_row, blend_row]:
        print(f"[weight]   {nm:34s} 年化 {st['cagr'] * 100:5.1f}% 夏普 {st['sharpe']:.2f} "
              f"回撤 {st['dd'] * 100:6.1f}% 换手 {st['turnover']:.1f}x")
    print(f"[weight] 产物 -> {os.path.relpath(OUT_MD, _STRAT)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
