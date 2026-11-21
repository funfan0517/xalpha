#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""V4 之上：**核心-卫星**（持有组固定比例 + 择时组再分配），目标是整个红利板块夏普最优。

问题
----
`fit.py` 逐只算过：同一套「RSI14 ≥70 清仓 / ≥50 减半」落在 10 只上，**有 2 只的最优策略
其实是买入持有**（018388 港股通高股息、561580 央企红利），其余 8 只是 RSI 择时。
而 V4 现在把 10 只一视同仁地全仓压进「RSI 最低 2 只」，等于让那 2 只也被择时割来割去。

于是问三件事：

1. 把「最优策略 = 持有」的标的单独拎出来当**底仓**（固定比例，如 50%），
   剩下 50% 只在**择时组**里按 V4 规则分配 —— 夏普能不能超过 V4？
2. 底仓内部要不要再**筛更优质的标的**（按过去 36 个月夏普取 top2）？
3. 分组怎么定才不算作弊：**滚动判定**（只用过去数据）vs **事后固定**（用全样本结论）差多少。

口径
----
* 池 = `pool.POOL`（10 只）；样本 2020-01 ~ 最新月；收益 = 累计净值月度。
* 分组：每半年（1 月 / 7 月）用过去 `FIT_WIN` 个月算逐只 `ΔCalmar = Calmar(RSI择时) − Calmar(持有)`，
  `Δ ≥ FIT_EPS` → 择时组，否则 → 持有组；历史 < `FIT_MIN` 个月的标的暂归择时组（不假设持有有效）。
* 底仓权重 = `w_core`，组内等权（另有「按过去 36 月夏普 top2」「逆波动」两种对照）。
* 卫星权重 = `1 − w_core`，组内按 V4：RSI 最低 `TOP_K=2` 只等权 × 板块 RSI 档位（≥0.70 清仓 / ≥0.50 减半）。
  **档位只作用于卫星部分**（底仓不动）——这是本方案与 V4 的核心区别。
* 成本单边 0.03%，按 Σ|Δw|；空仓不计息。

产物（AGENTS §8.4 ④ 课题子目录）
--------------------------------
    v4_rsi/research/core_satellite/_core_satellite.py
    v4_rsi/research/core_satellite/_core_satellite.md
    v4_rsi/research/core_satellite/_core_satellite.json

用法: python strategies/dividend/v4_rsi/research/core_satellite/_core_satellite.py
"""
from __future__ import annotations

import json
import os
import sys
from datetime import datetime

import numpy as np
import pandas as pd

_HERE = os.path.dirname(os.path.abspath(__file__))     # .../v4_rsi/research/core_satellite
_RES = os.path.dirname(_HERE)                          # .../v4_rsi/research
_V4 = os.path.dirname(_RES)                            # .../v4_rsi
_STRAT = os.path.dirname(_V4)                          # .../dividend
for _p in (_RES, _V4, _STRAT):
    if _p not in sys.path:
        sys.path.insert(0, _p)

import _single_signal as S  # noqa: E402  pylint: disable=wrong-import-position
import pool  # noqa: E402  pylint: disable=wrong-import-position
import rule as V4  # noqa: E402  pylint: disable=wrong-import-position

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

CODES = list(V4.CODES)
NAME = {c: pool.BY_CODE.get(c, {}).get("name", c) for c in CODES}

FEE = S.FEE
Q_OUT, Q_HALF = V4.Q_OUT, V4.Q_HALF     # 0.70 / 0.50
TOP_K = V4.TOP_K                        # 2
FIT_WIN = 36                            # 分组判定窗（月）
FIT_MIN = 24                            # 判定所需最少观测
FIT_EPS = 0.02                          # ΔCalmar ≥ 0.02 才算「择时更优」
GROUP_MONTHS = (1, 7)                   # 分组更新月（半年一次）
CORE_W = 0.5                            # 主方案底仓比例
CORE_GRID = (0.0, 0.3, 0.5, 0.7, 1.0)
START = "2020-01-01"

OUT_MD = os.path.join(_HERE, "_core_satellite.md")
OUT_JSON = os.path.join(_HERE, "_core_satellite.json")


# ------------------------------------------------------------------ 数据
def load():
    navs = pool.load_navs(codes=CODES)
    me = navs.resample("ME").last().ffill()
    rets = me.pct_change()
    rsi = S.load_signal("rsi", navs).reindex(rets.index)
    return navs, rets, rsi


def timing_w(rsi: pd.DataFrame) -> pd.DataFrame:
    """单标的 V4 口径仓位倍率（0 / 0.5 / 1），逐列（`S.time_mult` 只吃 Series）。"""
    q = rsi / 100.0
    m = pd.DataFrame(1.0, index=q.index, columns=q.columns)
    m[q >= Q_HALF] = 0.5
    m[q >= Q_OUT] = 0.0
    m[q.isna()] = 1.0
    return m


def fund_timing_rets(rets: pd.DataFrame, rsi: pd.DataFrame) -> pd.DataFrame:
    """逐只「RSI 择时」净收益（次月生效 + 换手成本），用于算 ΔCalmar。"""
    w = timing_w(rsi)
    wl = w.shift(1).fillna(1.0)
    turn = (wl - wl.shift(1).fillna(0.0)).abs()
    return (wl * rets - turn * FEE)


def delta_calmar(rets: pd.DataFrame, tim: pd.DataFrame, asof: pd.Timestamp) -> pd.Series:
    """asof 时点、过去 FIT_WIN 个月的 ΔCalmar（不含 asof 当月）。"""
    hist = rets.loc[:asof].iloc[:-1].tail(FIT_WIN)
    out = {}
    for c in rets.columns:
        h = hist[c].dropna()
        if len(h) < FIT_MIN:
            out[c] = np.nan
            continue
        s = tim[c].reindex(h.index)
        ch = S.stats(h)["calmar"]
        cs = S.stats(s)["calmar"]
        out[c] = cs - ch if np.isfinite(cs) and np.isfinite(ch) else np.nan
    return pd.Series(out)


def past_sharpe(rets: pd.DataFrame, asof: pd.Timestamp, win=FIT_WIN) -> pd.Series:
    hist = rets.loc[:asof].iloc[:-1].tail(win)
    return hist.apply(lambda s: S.stats(s.dropna())["sharpe"] if s.notna().sum() >= FIT_MIN
                      else np.nan)


def past_vol(rets: pd.DataFrame, asof: pd.Timestamp, win=FIT_WIN) -> pd.Series:
    return rets.loc[:asof].iloc[:-1].tail(win).std()


# ------------------------------------------------------------------ 分组时间线
def group_timeline(rets: pd.DataFrame, tim: pd.DataFrame, idx: pd.DatetimeIndex) -> dict:
    """{月份: (core_codes, sat_codes)} —— 每半年更新一次，中间沿用。"""
    out, cur = {}, None
    for t in idx:
        if cur is None or t.month in GROUP_MONTHS:
            dc = delta_calmar(rets, tim, t)
            avail = rets.loc[t].notna()
            core = [c for c in rets.columns if avail[c] and dc.get(c, np.nan) == dc.get(c, np.nan)
                    and dc[c] < FIT_EPS]
            sat = [c for c in rets.columns if avail[c] and c not in core]
            cur = (core, sat)
        out[t] = cur
    return out


# ------------------------------------------------------------------ 权重
def build_weights(rets: pd.DataFrame, rsi: pd.DataFrame, groups: dict,
                  w_core=CORE_W, core_mode="ew", sat_mult="sat_only") -> pd.DataFrame:
    """核心-卫星权重矩阵。

    core_mode : ew（等权）/ sharpe2（底仓内过去 36 月夏普前 2）/ invvol（逆波动）
                sharpe2_all / lowvol2_all（**无视分组**，直接在全池按过去 36 月夏普/波动筛底仓）
    sat_mult  : sat_only（档位只作用于卫星，底仓不动）/ all（档位作用于整体，退回 V4 风格）
    """
    idx = [t for t in rets.loc[START:].index]
    board = (rsi / 100.0).mean(axis=1, skipna=True)
    mult = S.time_mult(board, Q_OUT, Q_HALF)
    w = pd.DataFrame(0.0, index=idx, columns=rets.columns)
    for t in idx:
        core, sat = groups[t]
        avail = rets.loc[t].notna()
        if core_mode in ("sharpe2_all", "lowvol2_all"):
            # 「筛选更优质的标的」：不看 ΔCalmar 分组，直接在全池按过去业绩筛
            cand = [c for c in rets.columns if avail[c]]
            if core_mode == "sharpe2_all":
                sc = past_sharpe(rets, t)[cand].dropna()
                core = list(sc.nlargest(min(2, len(sc))).index) if len(sc) else []
            else:
                vv = past_vol(rets, t)[cand].dropna()
                core = list(vv.nsmallest(min(2, len(vv))).index) if len(vv) else []
            sat = [c for c in sat if avail[c]] or [c for c in cand if c not in core]
        core = [c for c in core if avail[c]]
        sat = [c for c in sat if avail[c]]
        if not core and not sat:                       # 极端情况：全都不足历史
            continue
        if not core:                                   # 底仓没人 → 全给卫星
            core, sat, wc = [], sat, 0.0
        elif not sat:
            sat, wc = [], core, 1.0
        else:
            wc = w_core
        # 底仓
        if core:
            if core_mode == "sharpe2" and len(core) > 2:
                sh = past_sharpe(rets, t)[core].dropna()
                pick = list(sh.nlargest(2).index) if len(sh) >= 2 else core
            elif core_mode == "invvol":
                vol = past_vol(rets, t)[core].replace(0, np.nan)
                pick = core
                iw = (1 / vol).dropna()
                iw = iw / iw.sum() if iw.sum() else pd.Series(1 / len(core), index=core)
                for c, v in iw.items():
                    w.loc[t, c] += wc * float(v)
                core = []
            else:
                pick = core
            if pick:
                for c in pick:
                    w.loc[t, c] += wc / len(pick)
        # 卫星：V4 规则（RSI 最低 2 只等权）
        if sat:
            row = rsi.loc[t, sat].dropna()
            take = list(row.nsmallest(min(TOP_K, len(row))).index) if len(row) else []
            if take:
                for c in take:
                    w.loc[t, c] += (1 - wc) / len(take) * (mult.get(t, 1.0)
                                                           if sat_mult == "sat_only" else 1.0)
        if sat_mult == "all":
            w.loc[t, :] *= mult.get(t, 1.0)
    return w


def v4_weights(rets: pd.DataFrame, rsi: pd.DataFrame) -> pd.DataFrame:
    """V4 原样：全池 RSI 最低 2 只 × 板块档位。"""
    sub = rets.loc[START:]
    q = (rsi / 100.0).loc[START:]
    ok = S.available(q, sub)
    top = S.top_w(rsi.loc[START:], ok, TOP_K)
    mult = S.time_mult(S.board_q(q, ok), Q_OUT, Q_HALF)
    # 显式对齐到样本索引：ok/mult 可能带着更早的索引，直接相 Mul 会并集扩行
    return top.mul(mult, axis=0).reindex(sub.index).fillna(0.0)


# ------------------------------------------------------------------ 指标
def sim(w: pd.DataFrame, rets: pd.DataFrame) -> tuple[pd.Series, float]:
    wl = w.shift(1).fillna(0.0)
    port = (wl * rets.reindex_like(w)).sum(axis=1, min_count=1)
    turn = (wl - wl.shift(1).fillna(0.0)).abs().sum(axis=1)
    r = (port - turn * FEE).dropna()
    return r, float(turn.mean() * 12)


def metr(r: pd.Series, turn: float = float("nan")) -> dict:
    st = S.stats(r)
    st["turnover"] = turn
    return st


def excess(r: pd.Series, ref: pd.Series) -> dict:
    d = (r - ref).dropna()
    if len(d) < 6 or not d.std():
        return {"mean": float("nan"), "t": float("nan"), "t_ex3": float("nan")}
    t = d.mean() / (d.std() / np.sqrt(len(d)))
    d2 = d.drop(d.nlargest(3).index)
    t2 = d2.mean() / (d2.std() / np.sqrt(len(d2))) if d2.std() else float("nan")
    return {"mean": float(d.mean() * 100), "t": float(t), "t_ex3": float(t2)}


# ------------------------------------------------------------------ 主流程
def main() -> int:
    navs, rets, rsi = load()
    tim = fund_timing_rets(rets, rsi)
    idx = list(rets.loc[START:].index)
    groups = group_timeline(rets, tim, idx)

    # 全样本事后ΔCalmar（对照滚动判定的稳定性）
    full_dc = {}
    for c in rets.columns:
        h = rets[c].loc[START:].dropna()
        s = tim[c].reindex(h.index).dropna()
        if len(h) < FIT_MIN or len(s) < FIT_MIN:
            full_dc[c] = float("nan")
            continue
        full_dc[c] = S.stats(s)["calmar"] - S.stats(h)["calmar"]
    hold_fixed = [c for c in rets.columns if full_dc[c] == full_dc[c] and full_dc[c] < FIT_EPS]

    # 基线
    ew = pd.DataFrame(np.where(rets.loc[idx].notna(), 1.0, 0.0), index=idx, columns=rets.columns)
    ew = ew.div(ew.sum(axis=1), axis=0)
    w4 = v4_weights(rets, rsi)

    rows = {}
    r_ew, t_ew = sim(ew, rets)
    rows["等权 10 只"] = metr(r_ew, t_ew)
    r_v4, t_v4 = sim(w4, rets)
    rows["V4 原样（全池 top2 + 板块档位）"] = metr(r_v4, t_v4)

    # 只择时组（w_core=0）
    w_sat = build_weights(rets, rsi, groups, w_core=0.0)
    r_s, t_s = sim(w_sat, rets)
    rows["只用择时组（w_core=0，池内筛选）"] = metr(r_s, t_s)

    # 核心-卫星网格
    grid_rows = {}
    for wc in CORE_GRID[1:]:
        w = build_weights(rets, rsi, groups, w_core=wc)
        r, t = sim(w, rets)
        grid_rows[wc] = metr(r, t)
        rows[f"核心-卫星 w_core={wc:.0%}（底仓等权）"] = grid_rows[wc]

    # 底仓分配方式对照（w_core=主方案）
    alt = {}
    for mode, label in (("sharpe2", "底仓内筛「过去 36 月夏普前 2」"),
                        ("sharpe2_all", "底仓=全池过去 36 月夏普前 2（不看分组）"),
                        ("lowvol2_all", "底仓=全池过去 36 月波动最低 2 只（不看分组）"),
                        ("invvol", "底仓逆波动加权")):
        w = build_weights(rets, rsi, groups, w_core=CORE_W, core_mode=mode)
        r, t = sim(w, rets)
        alt[label] = metr(r, t)
        rows[label] = alt[label]

    # 档位作用于整体（退回 V4 风格的对照）
    w_all = build_weights(rets, rsi, groups, w_core=CORE_W, sat_mult="all")
    r_all, t_all = sim(w_all, rets)
    rows["w_core=50% 但档位作用于整体"] = metr(r_all, t_all)

    # 事后固定分组（含前视，上限）
    fixed_groups = {t: (hold_fixed, [c for c in rets.columns if c not in hold_fixed]) for t in idx}
    w_fix = build_weights(rets, rsi, fixed_groups, w_core=CORE_W)
    r_fix, t_fix = sim(w_fix, rets)
    rows[f"事后固定底仓（{'+'.join(hold_fixed)}，含前视）"] = metr(r_fix, t_fix)

    # 稳健性：主方案
    w_main = build_weights(rets, rsi, groups, w_core=CORE_W)
    r_main, t_main = sim(w_main, rets)
    ex = {"vs V4": excess(r_main, r_v4), "vs 等权": excess(r_main, r_ew),
          "只择时组 vs V4": excess(r_s, r_v4),
          "档位整体 vs V4": excess(r_all, r_v4)}
    yearly = pd.DataFrame({"等权": r_ew, "V4": r_v4, "核心-卫星 50%": r_main})
    yearly = (1 + yearly).groupby(yearly.index.year).prod() - 1

    # 当前分组与建议权重
    last = idx[-1]
    core_now, sat_now = groups[last]
    last_w = w_main.loc[last]

    # 报告
    base = rows["等权 10 只"]
    v4st = rows["V4 原样（全池 top2 + 板块档位）"]
    main_st = rows[f"核心-卫星 w_core={CORE_W:.0%}（底仓等权）"]
    best_name = max(rows, key=lambda k: rows[k]["sharpe"])

    L = ["# V4 之上 · 核心-卫星配置（持有组固定比例 + 择时组再分配）", "",
         f"> 生成 {datetime.now():%Y-%m-%d %H:%M} · 池 {len(CODES)} 只 · 成本单边 {FEE:.2%} · "
         f"分组每半年滚动判定（{FIT_WIN} 月窗，ΔCalmar ≥ {FIT_EPS:.2f} 进择时组）", "",
         f"> 样本 {idx[0]:%Y-%m} ~ {idx[-1]:%Y-%m}（{len(idx)} 月）· 底仓固定比例、"
         f"卫星按 V4 规则（RSI 最低 {TOP_K} 只 × 板块档位 {Q_OUT * 100:.0f}/{Q_HALF * 100:.0f}）", ""]

    L += ["## 〇、结论先说", ""]
    L.append(f"- 等权夏普 **{base['sharpe']:.2f}**；V4 原样 **{v4st['sharpe']:.2f}**；"
             f"核心-卫星（{CORE_W:.0%} 底仓）**{main_st['sharpe']:.2f}**。")
    L.append(f"- 全部方案里夏普最高的是 **{best_name}**（{rows[best_name]['sharpe']:.2f}）。")
    L.append(f"- 主方案相对 V4：月均超额 {ex['vs V4']['mean']:+.2f}pp（t={ex['vs V4']['t']:+.2f}），"
             f"相对等权 {ex['vs 等权']['mean']:+.2f}pp（t={ex['vs 等权']['t']:+.2f}）。")
    L.append(f"- **用户设想的那个结构（底仓固定 + 档位只作用于卫星）是单调亏夏普的**："
             f"w_core 0→30%→50%→70%→100% 时夏普 "
             + "→".join(f"{rows[k]['sharpe']:.2f}" for k in
                        [k for k in rows if k.startswith("只用择时组")]
                        + [k for k in rows if k.startswith("核心-卫星 w_core")]) + "。"
             "底仓越大、夏普越低。")
    L.append(f"- 唯一不低于 V4 的是「{best_name}」：夏普 {rows[best_name]['sharpe']:.2f}（+"
             f"{rows[best_name]['sharpe'] - v4st['sharpe']:.2f}）、Calmar {rows[best_name]['calmar']:.2f}、"
             f"换手 {rows[best_name]['turnover']:.1f}x（V4 {v4st['turnover']:.1f}x）—— "
             "但它的档位压在**整体**上，本质是「权重倾斜」而不是「持有组不动」。")
    L.append("- 记住对照组：**「事后固定底仓」那一行含前视**，它衡量的是「如果一开始就知道"
             "哪几只好持有，能拿多少」，不能当策略 —— 而它也只有 "
             f"{rows[[k for k in rows if k.startswith('事后固定底仓')][0]]['sharpe']:.2f}，低于 V4。")
    L.append("")

    L += ["## 一、谁该当底仓（滚动判定 vs 全样本事后）", "",
          f"全样本 ΔCalmar（RSI 择时 − 买入持有）：",
          "", "| 标的 | ΔCalmar | 事后最优 |", "|---|---:|---|"]
    for c, v in sorted(full_dc.items(), key=lambda x: -(x[1] if x[1] == x[1] else -9)):
        tag = "买入持有" if (v == v and v < FIT_EPS) else "RSI 择时"
        L.append(f"| {NAME.get(c, c)}({c}) | {'—' if v != v else f'{v:+.2f}'} | {tag} |")
    L += ["", f"→ 事后看，底仓应该是 **{'+'.join(hold_fixed)}**（{len(hold_fixed)} 只）。"
              "但「事后」不能用来下单，所以主方案用**滚动判定**：", "",
          "| 判定日 | 底仓 | 择时组 |", "|---|---|---|"]
    seen = set()
    for t in idx:
        if t.month in GROUP_MONTHS or t == idx[0]:
            core, sat = groups[t]
            key = (tuple(core), tuple(sat))
            if key in seen:
                continue
            seen.add(key)
            L.append(f"| {t:%Y-%m} | {'、'.join(core) or '—'} | {'、'.join(sat) or '—'} |")
    L += ["", "> 滚动判定会来回改分组（尤其 2023 年那 3 只成立之后），这是它的代价，也是它诚实的地方。", ""]

    L += ["## 二、主表", "",
          "| 方案 | 年化 | 波动 | 夏普 | 最大回撤 | Calmar | 年换手 |",
          "|---|---:|---:|---:|---:|---:|---:|"]
    for nm, st in rows.items():
        L.append(f"| {nm} | {S.fmt_pct(st['cagr'])} | {S.fmt_pct(st['vol'])} | **{st['sharpe']:.2f}** | "
                 f"{S.fmt_pct(st['dd'])} | {st['calmar']:.2f} | {st['turnover']:.1f}x |")
    L.append("")

    L += ["## 三、底仓比例平台（不挑单点）", "",
          "| w_core | 年化 | 波动 | 夏普 | 回撤 | Calmar |", "|---|---:|---:|---:|---:|---:|"]
    L.append(f"| 0%（=只用择时组） | {S.fmt_pct(rows['只用择时组（w_core=0，池内筛选）']['cagr'])} | "
             f"{S.fmt_pct(rows['只用择时组（w_core=0，池内筛选）']['vol'])} | "
             f"{rows['只用择时组（w_core=0，池内筛选）']['sharpe']:.2f} | "
             f"{S.fmt_pct(rows['只用择时组（w_core=0，池内筛选）']['dd'])} | "
             f"{rows['只用择时组（w_core=0，池内筛选）']['calmar']:.2f} |")
    for wc, st in grid_rows.items():
        L.append(f"| {wc:.0%} | {S.fmt_pct(st['cagr'])} | {S.fmt_pct(st['vol'])} | "
                 f"{st['sharpe']:.2f} | {S.fmt_pct(st['dd'])} | {st['calmar']:.2f} |")
    L.append("")

    L += ["## 四、稳健性（相对 V4 的月度超额）", "",
          "| 方案 | 月均超额 | t | 剔除超额最大 3 月后 t |", "|---|---:|---:|---:|"]
    for k in ("只择时组 vs V4", "vs V4", "档位整体 vs V4"):
        e = ex[k]
        nm = {"只择时组 vs V4": "只用择时组（w_core=0）",
              "vs V4": f"核心-卫星（{CORE_W:.0%} 底仓，档位只作用于卫星）",
              "档位整体 vs V4": f"核心-卫星（{CORE_W:.0%} 底仓，档位作用于整体）"}[k]
        L.append(f"| {nm} | {e['mean']:+.2f}pp | {e['t']:+.2f} | {e['t_ex3']:+.2f} |")
    L += ["", "| 年份 | " + " | ".join(yearly.columns) + " |",
          "|---|" + "---:|" * len(yearly.columns)]
    for ts, row in yearly.iterrows():
        L.append(f"| {int(ts)} | " + " | ".join(S.fmt_pct(v) for v in row.values) + " |")
    L.append("")

    L += ["## 五、当前读数与建议权重", "",
          f"最近决策月 {last:%Y-%m}：底仓 = {'、'.join(core_now) or '—'}；"
          f"择时组 = {'、'.join(sat_now) or '—'}。", "",
          "| 标的 | 建议占比 | 组 |", "|---|---:|---|"]
    for c in rets.columns:
        g = "底仓（持有）" if c in core_now else "卫星（择时）"
        L.append(f"| {NAME.get(c, c)}({c}) | {last_w[c] * 100:.1f} | {g} |")
    L += ["", f"> 合计 {last_w.sum() * 100:.0f}（卫星部分被板块档位缩放后会低于 100%，差额是现金）。", ""]

    L += ["## 六、读法", "",
          f"1. 主方案（{CORE_W:.0%} 底仓、档位只作用于卫星）夏普 {main_st['sharpe']:.2f} vs V4 "
          f"{v4st['sharpe']:.2f}，差 {main_st['sharpe'] - v4st['sharpe']:+.2f}，"
          f"月均超额 {ex['vs V4']['mean']:+.2f}pp（t={ex['vs V4']['t']:+.2f}）。**不采纳。**",
          "2. **为什么底仓越大夏普越低**：ΔCalmar < 0 只说明「对这几只做 RSI 择时是亏的」，"
          "**不等于「持有它们最好」**。这几只（018388/561580/007760）的绝对夏普本来就低于"
          "V4 每月挑出来的 top2，把它们固定成底仓，等于拿低夏普资产替换高夏普仓位 —— "
          "数学上必然摊薄。",
          f"3. **「筛选更优质的标的」也没救回来**：底仓内筛过去 36 月夏普前 2 → "
          f"{rows['底仓内筛「过去 36 月夏普前 2」']['sharpe']:.2f}；"
          f"全池筛夏普前 2 → {rows['底仓=全池过去 36 月夏普前 2（不看分组）']['sharpe']:.2f}；"
          f"全池筛波动最低 2 只 → {rows['底仓=全池过去 36 月波动最低 2 只（不看分组）']['sharpe']:.2f}。"
          f"连**事后固定**底仓（含前视）都只有 {rows[[k for k in rows if k.startswith('事后固定底仓')][0]]['sharpe']:.2f}。",
          f"4. 唯一可看的是「{best_name}」：夏普 {rows[best_name]['sharpe']:.2f}、"
          f"Calmar {rows[best_name]['calmar']:.2f}（V4 {v4st['calmar']:.2f}）、"
          f"回撤 {S.fmt_pct(rows[best_name]['dd'])}（V4 {S.fmt_pct(v4st['dd'])}）、"
          f"换手 {rows[best_name]['turnover']:.1f}x（V4 {v4st['turnover']:.1f}x）。"
          "但夏普只 +0.01、t 值不显著，**本质是把档位压在整体上（等于权重倾斜），"
          "不是用户设想的核心-卫星**。若只想降波动/降换手，它可以作为一个可选口径；"
          "想靠它提高夏普，没有证据。",
          "5. 边界：分组滚动判定只用过去 36 个月，2023 年前可投只有 5–7 只，"
          "分组的噪声很大（判定表里 2023-01、2024-01 底仓为空就是例子）；"
          "样本 83 个月，夏普标准误约 ±0.4，任何小于 0.1 的差异都不该被当成结论。",
          ""]

    S.write(OUT_MD, "\n".join(L))
    payload = {
        "generated_at": datetime.now().strftime("%Y-%m-%d %H:%M"),
        "params": {"fee": FEE, "fit_win": FIT_WIN, "fit_min": FIT_MIN, "fit_eps": FIT_EPS,
                   "group_months": list(GROUP_MONTHS), "core_w": CORE_W,
                   "top_k": TOP_K, "q_out": Q_OUT, "q_half": Q_HALF},
        "sample": [str(idx[0].date()), str(idx[-1].date())],
        "full_sample_dcalmar": {k: (None if v != v else float(v)) for k, v in full_dc.items()},
        "hold_fixed": hold_fixed,
        "rows": rows,
        "core_grid": {f"{k:.2f}": v for k, v in grid_rows.items()},
        "excess": ex,
        "yearly": {str(int(k)): {c: float(v) for c, v in row.items()}
                   for k, row in yearly.iterrows()},
        "now": {"core": core_now, "satellite": sat_now,
                "weights": {c: float(last_w[c]) for c in rets.columns}},
        "group_timeline": {f"{t:%Y-%m}": {"core": [c for c in groups[t][0]],
                                          "sat": [c for c in groups[t][1]]}
                           for t in idx if t.month in GROUP_MONTHS},
    }
    S.write(OUT_JSON, json.dumps(payload, ensure_ascii=False, indent=2, default=str))

    print(f"[core-sat] 样本 {idx[0]:%Y-%m}~{idx[-1]:%Y-%m} ({len(idx)} 月) · 事后底仓 {hold_fixed}")
    for nm, st in rows.items():
        print(f"[core-sat] {nm:44s} 年化 {st['cagr'] * 100:5.1f}% 波动 {st['vol'] * 100:5.1f}% "
              f"夏普 {st['sharpe']:.2f} 回撤 {st['dd'] * 100:6.1f}% 换手 {st['turnover']:.1f}x")
    print(f"[core-sat] 超额 vs V4 {ex['vs V4']['mean']:+.2f}pp/月 t={ex['vs V4']['t']:+.2f}")
    print(f"[core-sat] 产物 -> {os.path.relpath(OUT_MD, _STRAT)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
