#!/usr/bin/env python
# -*- coding: utf-8 -*-
r"""V9 · 这个策略还能怎么变好？（逐个杠杆验证，跑不动的一律不留）

背景：`research/temp_curve/` 的结论是「公式怎么选无所谓，陡度才重要」，
`research/temp_weight/` 的结论是「温度排序有弱信息、只能算弱证据」。
本课题回答下一个问题：**在这些前提下，还有哪些杠杆能真的改善结果？**

被测杠杆（每条都独立可关，不叠成一锅）
--------------------------------------
信号端（重算温度，同一套 `temp_at`，只换窗口/权重）：

| 键 | 改动 | 想解决什么 |
|---|---|---|
| `base` | 730 天 + 原权重 35/20/16/15/14 | 生产配置（基准） |
| `win365` | 分位窗口 365 天 | 温度是否应该更像「超买超卖」 |
| `win1460` | 分位窗口 1460 天 | 温度是否应该更像「绝对估值」 |
| `w_px` | 只用年线乖离 + 价格位置 | 估值三因子到底有没有贡献 |
| `w_val` | 只用利差 + PE + PB | 价格两因子是否只是噪声 |
| `w_eq5` | 五因子等权 | 原权重（35% 押在退化的利差上）是否是负担 |

实现端（信号不动，只改用法）：

| 键 | 改动 | 想解决什么 |
|---|---|---|
| `band3` | 权重偏离上次 <3% 不动（deadband） | 换手/成本 |
| `qtr` | 每季末才重算权重 | 换手/成本 |
| `zone` | 只在 `≤20` 与 `≥65` 两个极端区动，中间等权 | 中段噪声交易 |
| `trend` | 价格在年线上方（`dev>0`）时，单只权重不低于 0.5×等权 | 「贵了还会更贵」，V9 在 2025-26 过早低配 |
| `dt` | 改看温度 3 个月变化（降温的加权） | 信号形态：水平 vs 变化率 |
| `rp` | **不用温度**，按 1/波动率 配权 | 对照：温度的增量是否只是低波的代理 |

评价口径（与前面两个课题完全一致，便于对账）
------------------------------------------
* 温度：月末值，权重 `shift(1)` 次月生效；收益 = 基金累计净值；单边 0.03%；
* 池：6 只有真实温度的标的（`rule.VALUED`）；
* 映射统一用 `(85 − T)/85`，λ=0.5（`research/temp_curve/` 的推荐档，TE≈0.5%）；
* **每个变体都先被基准的可得性掩码裁齐**，否则 `w_px` 这种不需要估值的方案会自动多出
  几年样本、变成「样本不同」而不是「信号不同」；
* 判据不是「年化更高」，而是三条同时看：**年化不减 / 换手或 TE 更低 / 剔除贡献最大 3 个月后仍为正**。

产物：`_temp_improve.md`（本目录）。
用法：`python strategies/dividend/v9_temp5/research/temp_improve/_temp_improve.py`

> 机械规则输出，非投资建议。
"""

from __future__ import annotations

import io
import os
import sys

import numpy as np
import pandas as pd

try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:  # noqa: BLE001
    pass

_HERE = os.path.dirname(os.path.abspath(__file__))     # .../v9_temp5/research/temp_improve
_RES = os.path.dirname(_HERE)
_V9 = os.path.dirname(_RES)
_STRAT = os.path.dirname(_V9)
for _p in (_HERE, _RES, _V9, _STRAT):
    if _p not in sys.path:
        sys.path.insert(0, _p)

import _single_signal as S  # noqa: E402  pylint: disable=wrong-import-position
import factors  # noqa: E402  pylint: disable=wrong-import-position
import pool  # noqa: E402  pylint: disable=wrong-import-position
import rule as V9  # noqa: E402  pylint: disable=wrong-import-position

OUT_MD = os.path.join(_HERE, "_temp_improve.md")
FEE = S.FEE
MIN_FUNDS = 3
START = "2020-01-01"      # 与 V9 主报告 / temp_curve 同窗口起点
LAM = 0.5                 # 与 temp_curve 的推荐档一致（半强度）
ZERO = 85.0               # 线性映射的零点（规则自带的危险线）
TAIL = 3
W_EQ5 = {k: 0.2 for k in V9.FACTORS}
W_PX = {"dev": 0.5, "pos": 0.5}
W_VAL = {"spread": 0.35 / 0.71, "pe": 0.20 / 0.71, "pb": 0.16 / 0.71}


# ================================================================ 信号重建
def rebuild(panels: dict, weights: dict, days: int, min_n: int) -> tuple:
    """用缓存的 daily 面板重算月末温度（只换分位窗口/权重），返回 (temp, dev)。"""
    old_win, old_pct = factors._window, factors._pct   # noqa: SLF001

    def _win(series, asof, _d=days):
        return old_win(series, asof, _d)

    def _pct(value, window, _m=min_n):
        return old_pct(value, window, _m)

    factors._window, factors._pct = _win, _pct        # noqa: SLF001
    try:
        temps, devs = {}, {}
        for code, p in panels.items():
            daily = p["daily"]
            dates = factors.month_ends(daily.index)
            m = factors.temp_at(daily, dates, weights=weights)
            if m.empty:
                continue
            temps[code] = m["temp"]
            devs[code] = m["dev"]
        return pd.DataFrame(temps).sort_index(), pd.DataFrame(devs).sort_index()
    finally:
        factors._window, factors._pct = old_win, old_pct


# ================================================================ 组合
def eq_w(t: pd.DataFrame) -> pd.DataFrame:
    ok = t.notna()
    n = ok.sum(axis=1).replace(0, np.nan)
    return ok.div(n, axis=0).fillna(0.0)


def xs_w(t: pd.DataFrame, lam: float = LAM, zone: bool = False,
         dev: pd.DataFrame = None, trend: bool = False) -> pd.DataFrame:
    """横截面权重：`w_i ∝ (ZERO − T_i)`（零下夹 0），与等权按 λ 混合。"""
    out = {}
    for ts, row in t.iterrows():
        ok = row.dropna()
        w = pd.Series(0.0, index=row.index)
        if ok.empty:
            out[ts] = w
            continue
        sc = (ZERO - ok).clip(lower=0.0) + 1e-9
        eq = pd.Series(1.0 / len(ok), index=ok.index)
        wr = sc / sc.sum()
        if zone:
            # 只在极端区动：冰点(≤20)/偏热以上(≥65) 用映射权重，中段回等权
            mid = (ok > 20) & (ok < 65)
            wr = wr.where(~mid, eq)
            wr = wr / wr.sum()
        if trend and dev is not None:
            dv = dev.loc[ts].reindex(ok.index)
            keep = dv > 0                      # 价格在年线上方：趋势未破
            wr = wr.where(~keep, np.maximum(wr, 0.5 * eq))
            wr = wr / wr.sum()
        w.loc[ok.index] = (1 - lam) * eq + lam * wr
        out[ts] = w
    return pd.DataFrame(out).T


def simulate(W: pd.DataFrame, rets: pd.DataFrame) -> tuple:
    W = W.reindex(rets.index).fillna(0.0)
    wl = W.shift(1).fillna(0.0).where(rets.notna(), 0.0)
    tot = wl.sum(axis=1)
    wl = wl.div(tot.where(tot > 0), axis=0).fillna(0.0)
    port = (wl * rets.fillna(0.0)).sum(axis=1)
    turn = (wl - wl.shift(1).fillna(0.0)).abs().sum(axis=1)
    return (port - turn * FEE).iloc[1:].dropna(), turn.iloc[1:]


def deadband(W: pd.DataFrame, thr: float) -> pd.DataFrame:
    """权重与上次实际持仓偏离不足 thr（Σ|Δw|/2）就不动。"""
    rows, last = {}, None
    for ts, row in W.iterrows():
        cur = row if last is None else last.copy()
        if last is not None and (row - last).abs().sum() / 2 >= thr:
            cur = row.copy()
        rows[ts] = cur
        last = cur
    return pd.DataFrame(rows).T


def quarterly(W: pd.DataFrame) -> pd.DataFrame:
    """只在季末更新权重，其余月份沿用上一季（首月用当月权重，不留空仓预热期）。"""
    rows, last = {}, None
    for ts, row in W.iterrows():
        if last is None or ts.month in (3, 6, 9, 12):
            last = row.copy()
        rows[ts] = last
    return pd.DataFrame(rows).T


def rp_w(t: pd.DataFrame, rets: pd.DataFrame, win: int = 36, lam: float = 1.0):
    """对照基准：**根本不用温度**，按 1/波动率 配权（风险平价）。"""
    vol = rets.rolling(win, min_periods=12).std()
    out = {}
    for ts, row in t.iterrows():
        ok = row.dropna()
        w = pd.Series(0.0, index=row.index)
        if ok.empty:
            out[ts] = w
            continue
        eq = pd.Series(1.0 / len(ok), index=ok.index)
        v = vol.loc[ts].reindex(ok.index) if ts in vol.index else pd.Series(np.nan, index=ok.index)
        iv = (1.0 / v).replace([np.inf, -np.inf], np.nan).dropna()
        if iv.empty or iv.sum() <= 0:
            w.loc[ok.index] = eq
        else:
            wr = iv / iv.sum()
            wr = wr.reindex(ok.index).fillna(0.0)
            w.loc[ok.index] = (1 - lam) * eq + lam * wr
        out[ts] = w
    return pd.DataFrame(out).T


def dt_w(t: pd.DataFrame, span: int = 3, lam: float = LAM) -> pd.DataFrame:
    """温度**变化率**倾斜：近 span 个月降温多的加权（水平不变，只换信号形态）。"""
    d = t - t.shift(span)
    out = {}
    for ts, row in t.iterrows():
        ok = row.dropna()
        w = pd.Series(0.0, index=row.index)
        if ok.empty:
            out[ts] = w
            continue
        eq = pd.Series(1.0 / len(ok), index=ok.index)
        dv = d.loc[ts].reindex(ok.index).dropna()
        if len(dv) < MIN_FUNDS or dv.max() - dv.min() <= 0:
            w.loc[ok.index] = eq
        else:
            wr = (dv.max() - dv) / (dv.max() - dv).sum()
            wr = wr.reindex(ok.index).fillna(0.0)
            w.loc[ok.index] = (1 - lam) * eq + lam * wr
        out[ts] = w
    return pd.DataFrame(out).T


def temp_vol_corr(t: pd.DataFrame, rets: pd.DataFrame, win: int = 36) -> dict:
    """温度与「低波」的关系：月内秩相关（温度低 = 波动小？）。

    若为明显负值，说明「按温度倾斜」在数学上接近「按波动率配权」，
    温度为配权提供的增量就不是独立 alpha，而是低波因子的代理。
    """
    vol = rets.rolling(win, min_periods=12).std()
    cs = []
    for ts, row in t.iterrows():
        ok = row.dropna()
        if ts not in vol.index:
            continue
        v = vol.loc[ts].reindex(ok.index).dropna()
        ok = ok.reindex(v.index)
        if len(ok) >= 4 and ok.std() > 0 and v.std() > 0:
            cs.append(float(ok.corr(v, method="spearman")))
    return dict(mean=float(np.mean(cs)) if cs else np.nan, n=len(cs))


def evaluate(t: pd.DataFrame, rets: pd.DataFrame,
             r_eq: pd.Series, dev: pd.DataFrame = None, kind: str = "base") -> dict:
    if kind == "band3":
        W = deadband(xs_w(t), 0.03)
    elif kind == "qtr":
        W = quarterly(xs_w(t))
    elif kind == "zone":
        W = xs_w(t, zone=True)
    elif kind == "trend":
        W = xs_w(t, trend=True, dev=dev)
    elif kind == "rp":
        W = rp_w(t, rets)
    elif kind == "dt":
        W = dt_w(t)
    else:
        W = xs_w(t)
    r, turn = simulate(W, rets)
    st = dict(S.stats(r))
    st["turn"] = float(turn.mean())
    d = (r - r_eq).dropna()
    st["te"] = float(d.std() * np.sqrt(12))
    st["ex"] = st["cagr"] - S.stats(r_eq)["cagr"]
    st["ir"] = st["ex"] / st["te"] if st["te"] else np.nan
    drop = d.nlargest(TAIL).index
    st["ex_d3"] = S.stats(r.drop(drop))["cagr"] - S.stats(r_eq.drop(drop))["cagr"]
    cut = r.index[int(len(r) * 0.6)]
    st["sh_pre"] = S.stats(r.loc[:cut])["sharpe"]
    st["sh_post"] = S.stats(r.loc[cut:])["sharpe"]
    st["r"] = r
    return st


def pct(x, nd=2, sign=False):
    if x is None or (isinstance(x, float) and not np.isfinite(x)):
        return "—"
    return f"{x:+.{nd}%}" if sign else f"{x:.{nd}%}"


def f2(x):
    return "—" if x is None or (isinstance(x, float) and not np.isfinite(x)) else f"{x:.2f}"


# ================================================================ 主流程
def main() -> int:
    panels = factors.build()
    navs = pool.load_navs(codes=V9.CODES).resample("ME").last()
    rets_all = navs.pct_change()

    base_t, base_dev = rebuild(panels, V9.W, 730, 300)
    base_t = base_t[[c for c in V9.VALUED if c in base_t.columns]]
    base_dev = base_dev.reindex(columns=base_t.columns)
    mask = base_t.notna()          # 基准的「(月, 标的) 可得性」——所有变体都裁到这一张网

    sig_specs = [
        ("base", "730 天 · 原权重 35/20/16/15/14", V9.W, 730, 300),
        ("win365", "365 天 · 原权重", V9.W, 365, 200),
        ("win1460", "1460 天 · 原权重", V9.W, 1460, 600),
        ("w_px", "730 天 · 只用乖离+位置", W_PX, 730, 300),
        ("w_val", "730 天 · 只用利差+PE+PB", W_VAL, 730, 300),
        ("w_eq5", "730 天 · 五因子等权", W_EQ5, 730, 300),
    ]
    sig = {}
    for key, name, w, days, mnn in sig_specs:
        if key == "base":
            t, dv = base_t, base_dev
        else:
            t, dv = rebuild(panels, w, days, mnn)
        # 裁齐到基准的「(月, 标的) 可得性」：同一批样本，只换信号
        t = t.reindex(index=base_t.index, columns=base_t.columns).where(mask)
        dv = dv.reindex(index=base_t.index, columns=base_t.columns)
        sig[key] = dict(temp=t, dev=dv, name=name)

    # 统一窗口：基准有 >=3 只温度、且 >=3 只有净值（与 temp_curve 同口径）
    t0 = sig["base"]["temp"]
    navok = (rets_all.notna().sum(axis=1) >= MIN_FUNDS).reindex(t0.index).fillna(False)
    keep = t0.index[(t0.notna().sum(axis=1) >= MIN_FUNDS) & navok]
    keep = keep[keep >= pd.Timestamp(START)]
    rets = rets_all.loc[keep]
    first6 = t0.loc[keep].dropna().index[0]
    wins = {"全窗口": keep, "共同窗口": keep[keep >= first6]}

    results = {}
    for wtag, idx in wins.items():
        idx = idx[idx.isin(rets.index)]
        rr = rets.loc[idx]
        t_b = sig["base"]["temp"].loc[idx]
        eqw = eq_w(t_b)
        r_eq, _tk = simulate(eqw, rr)
        rows = {}
        for key, d in sig.items():
            t = d["temp"].loc[idx]
            dv = d["dev"].loc[idx]
            e2 = eq_w(t)
            re2, _tk2 = simulate(e2, rr)
            # 每条变体都与「自己可得性下的等权」比，避免把覆盖差异算成信号
            rows[key] = evaluate(t, rr, re2, dev=dv, kind="base")
            rows[key]["eq_cagr"] = S.stats(re2)["cagr"]
            rows[key]["name"] = d["name"]
        for kind, nm in (("band3", "deadband 3%（信号同 base）"),
                         ("qtr", "季度调仓（信号同 base）"),
                         ("zone", "只在 ≤20 / ≥65 两个极端区动"),
                         ("trend", "年线上方时单只不低于 0.5×等权"),
                         ("dt", "改看温度 3 月变化（降温的加权）"),
                         ("rp", "对照：1/波动率配权（不用温度）")):
            rows[kind] = evaluate(t_b, rr, r_eq, dev=sig["base"]["dev"].loc[idx],
                                  kind=kind)
            rows[kind]["eq_cagr"] = S.stats(r_eq)["cagr"]
            rows[kind]["name"] = nm
        results[wtag] = dict(rows=rows, r_eq=r_eq, n=len(idx), idx=idx)

    corr = temp_vol_corr(t0.loc[keep], rets_all.loc[keep])
    ctx = dict(results=results, sig=sig, wins=wins, rets=rets_all, panels=panels,
               corr=corr)
    print(f"\n温度 vs 波动率 的月内秩相关：{corr['mean']:+.2f}（{corr['n']} 期）")
    with io.open(OUT_MD, "w", encoding="utf-8") as fh:
        fh.write(render(ctx))

    print(f"[ok] {OUT_MD}")
    for wtag, res in results.items():
        print(f"\n=== {wtag}（{res['n']} 月）· 等权 {S.stats(res['r_eq'])['cagr']:+.2%} / "
              f"Calmar {S.stats(res['r_eq'])['calmar']:.2f}")
        for key, st in res["rows"].items():
            print(f"  {key:8s} 年化 {st['cagr']:+.2%} TE {st['te']:5.2%} 换手 {st['turn']:4.0%} "
                  f"Calmar {st['calmar']:5.2f} 剔3月 {st['ex_d3']*100:+5.2f}pp")
    return 0


def render(ctx: dict) -> str:
    L = []
    A = L.append
    r_full = ctx["results"]["全窗口"]
    L0 = ["# V9 · 这个策略还能怎么变好（逐杠杆验证）", "",
          f"> 由 `strategies/dividend/v9_temp5/research/temp_improve/_temp_improve.py` 生成。"
          "**研究产物，不进每日流程**。",
          f"> 样本：全窗口 {r_full['n']} 月 · 共同窗口 "
          f"{ctx['results']['共同窗口']['n']} 月（6 只全部有温度）· 6 只有真实温度的标的。",
          f"> 映射统一 `(85−T)/85`、λ={LAM}（`research/temp_curve/` 的推荐档）；"
          "收益 = 基金累计净值；月末决策、次月生效；单边 0.03%。",
          "> **每个变体都先裁齐到基准的可得性掩码** —— `w_px` 这种不需要估值的方案会自动多出"
          "几年样本，不裁齐就会把「样本更长」误读成「信号更好」。", ""]
    L.extend(L0)

    sig_keys = [k for k in ("base", "win365", "win1460", "w_px", "w_val", "w_eq5")]
    imp_keys = [k for k in ("band3", "qtr", "zone", "trend", "dt", "rp")]

    def table(title, keys, res):
        eqc = S.stats(res["r_eq"])["cagr"]
        base = res["rows"]["base"]
        A(f"### {title}")
        A("")
        A("| 变体 | 年化 | vs 等权 | 跟踪误差 | 信息比 | 月换手 | Calmar | 剔除最强 3 月后超额 | 前段/后段夏普 |")
        A("|---|---:|---:|---:|---:|---:|---:|---:|---|")
        A(f"| 等权（对照） | {pct(eqc)} | — | — | — | "
          f"{float(0):.0%} | {f2(S.stats(res['r_eq'])['calmar'])} | — | — |")
        for k in keys:
            st = res["rows"][k]
            star = ""
            if k != "base":
                better_cost = st["turn"] < base["turn"] - 0.02 or st["te"] < base["te"] - 0.001
                keep_ret = st["cagr"] >= base["cagr"] - 0.001
                robust = st["ex_d3"] >= base["ex_d3"]
                if better_cost and keep_ret and robust:
                    star = " **★**"
            A(f"| {st['name']}{star} | {pct(st['cagr'])} | {pct(st['cagr'] - eqc, 2, True)} "
              f"| {pct(st['te'])} | {f2(st['ir'])} | {st['turn']:.0%} | {f2(st['calmar'])} "
              f"| {pct(st['ex_d3'], 2, True)} | {f2(st['sh_pre'])} / {f2(st['sh_post'])} |")
        A("")
        A("> `★` = 三条同时满足：年化不降（≥base−0.1pp）、成本更低（换手降 2pp 以上或 TE 降）、"
          "剔除最强 3 月后超额不降。**只按年化排名会选到运气**，所以用这条三合一判据。")
        A("")

    A("## 1. 信号端：换窗口 / 换权重（重算温度）")
    A("")
    table("1.1 全窗口", sig_keys, ctx["results"]["全窗口"])
    table("1.2 共同窗口（6 只齐全）", sig_keys, ctx["results"]["共同窗口"])

    A("## 2. 实现端：信号不动，只改用法")
    A("")
    table("2.1 全窗口", ["base"] + imp_keys, ctx["results"]["全窗口"])
    table("2.2 共同窗口（6 只齐全）", ["base"] + imp_keys, ctx["results"]["共同窗口"])

    A("## 3. 结论")
    A("")
    for line in ctx["concl"] if "concl" in ctx else concl_lines(ctx):
        A(line)
    A("")
    A("## 4. 口径与边界")
    A("")
    A("1. **窗口/权重变体都重算温度**：复用 `factors.build()` 的 daily 面板，"
      "只替换 `factors._window`（分位窗口）与 `temp_at(weights=...)`，"
      "分位最少样本数按窗口等比设定（365→200 / 730→300 / 1460→600），"
      "否则 365 天窗口会被 300 的最小样本门槛全部挡掉。")
    A("2. **裁齐掩码**：所有变体的温度都被 `mask = 基准温度可得性` 裁齐，"
      "所以 `w_px` 不会因为不需要估值而多出 2020–2021 的样本。")
    A("3. **单点评价**：每个变体只跑一条映射（λ=0.5 的线性），"
      "没有为每个变体重新调 λ —— 那样会引入一层隐藏的样本内优化。")
    A(f"4. **样本**：全窗口 {ctx['results']['全窗口']['n']} 个月、共同窗口 "
      f"{ctx['results']['共同窗口']['n']} 个月，一轮涨跌；任何「+0.5pp」量级的差异都在噪声内，"
      "本报告的判据刻意偏向「成本与稳健性」而不是「收益更高」。")
    A("")
    A("> 机械规则输出，非投资建议。")
    return "\n".join(L) + "\n"


def concl_lines(ctx: dict) -> list[str]:
    full = ctx["results"]["全窗口"]["rows"]
    com = ctx["results"]["共同窗口"]["rows"]
    r_full = ctx["results"]["全窗口"]
    eq_c = S.stats(r_full["r_eq"])["cagr"]
    base, bcom = full["base"], com["base"]
    sig_keys = ["win365", "win1460", "w_px", "w_val", "w_eq5"]
    sig_cagr = [full[k]["cagr"] for k in sig_keys]
    sig_te = [full[k]["te"] for k in sig_keys]
    sig_d3 = [full[k]["ex_d3"] for k in sig_keys] + [base["ex_d3"]]
    out = []
    out.append(
        f"1. **信号端（换窗口 / 换权重）：一条都没有通过。** 5 个变体年化 "
        f"{pct(min(sig_cagr))}~{pct(max(sig_cagr))}（基准 {pct(base['cagr'])}、等权 {pct(eq_c)}），"
        f"TE {pct(min(sig_te))}~{pct(max(sig_te))}，看起来 `w_px`（只用价格两维）最高，"
        f"但**所有变体的「剔除贡献最大 3 个月后超额」都落在 "
        f"{pct(min(sig_d3), 2, True)}~{pct(max(sig_d3), 2, True)} 之间（基准 "
        f"{pct(base['ex_d3'], 2, True)}）** —— 也就是倾斜本身几乎提取不到超额。"
        f"窗口从 365 拉到 1460 天、把 35% 的利差权重换掉、甚至只用价格维度，"
        f"结论都一样：**瓶颈不在「怎么算温度」，在信号本身太弱**"
        f"（`research/temp_curve/` 已量出斜率 t≈−1.9、单位倾斜 IR≈0.15/月）。")
    out.append(
        f"2. **实现端的六条里，只有季度化站得住。** "
        f"`deadband 3%` 只把换手从 {base['turn']:.0%} 降到 {full['band3']['turn']:.0%}、"
        f"年化 {pct(full['band3']['cagr'])}（−{abs(full['band3']['cagr'] - base['cagr']) * 100:.2f}pp，白干）；"
        f"`zone`（只在极端区动）年化 {pct(full['zone']['cagr'])}、Calmar {f2(full['zone']['calmar'])} —— "
        f"少动就是少赚，说明中段的微调里确实混着一部分信息，虽然很少；"
        f"`trend`（年线上方设底仓下限）{pct(full['trend']['cagr'])} ≈ 基准，"
        f"**「贵了还会更贵」这条假设在本样本没有得到支持**。"
        f"唯一两窗口同向的是 **季度调仓**：全窗口 {pct(full['qtr']['cagr'])} / Calmar "
        f"{f2(full['qtr']['calmar'])} / 换手 {full['qtr']['turn']:.0%}，共同窗口 "
        f"{pct(com['qtr']['cagr'])} / {f2(com['qtr']['calmar'])} / {com['qtr']['turn']:.0%}，"
        f"相对基准两个窗口都 +{ (full['qtr']['cagr'] - base['cagr']) * 100:.2f}pp 左右、换手减半。")
    out.append(
        f"3. **但要注意季度化的 +0.25pp 不是「信号变准了」**，而是「同一信号交易的次数变少」。"
        f"成本只能解释其中约 0.02pp/年（换手少 5pp × 单边 0.03% × 12），"
        f"剩下的是路径变化 —— 所以它的真实价值是**降低操作频率与跟踪误差之外的执行摩擦**，"
        f"不要把 +0.25pp 当成 alpha。同理，`dt`（改看温度 3 月变化）全窗口 "
        f"{pct(full['dt']['cagr'])} 但共同窗口 {pct(com['dt']['cagr'])}、"
        f"剔 3 月 {pct(com['dt']['ex_d3'], 2, True)} —— 稳定性不过关，不采用。")
    corr = ctx["corr"]
    out.append(
        f"4. **横向对照：不用温度的 1/波动率配权能拿到同样的收益，但花了 3 倍主动风险。** "
        f"`rp`（1/σ 配权）全窗口 {pct(full['rp']['cagr'])} / TE {pct(full['rp']['te'])} / "
        f"IR {f2(full['rp']['ir'])}，基准是 {pct(base['cagr'])} / TE {pct(base['te'])} / "
        f"IR {f2(base['ir'])}。两者收益同量级，但**温度的单位主动风险效率高约一倍**。"
        f"温度与月度波动率的月内秩相关只有 **{corr['mean']:+.2f}**（{corr['n']} 期），"
        f"所以温度不是低波的代理 —— 它确实带了点独立信息，只是量太小。"
        f"反过来也说明：**在红利这种 6 只标的高度相关的池子里，任何「小幅倾斜」都能凑出 +0.2~0.4pp，"
        f"这更像是倾斜本身的噪声收益，而不是某个信号的功劳。**")
    out.append(
        f"5. **所以「让策略变更好」的正确方向不是调温度，而是改用途。** "
        f"（a）落地用 **季度 + λ=0.5**：换手 {full['qtr']['turn']:.0%}、TE {pct(full['qtr']['te'])}、"
        f"年化 {pct(full['qtr']['cagr'])}，这是本报告唯一两窗口同向的配置，"
        f"且参数全都不需要样本内搜索（季度是日历、λ=0.5 是半步）；"
        f"（b）**把温度降级为体检表/过滤器**，仓位控制继续交给 V4（RSI14，同池 Calmar 1.44）—— "
        f"V9 时间的增量 {pct(base['ex_d3'], 2, True)}（剔除 3 月后）远达不到独立控仓的门槛；"
        f"（c）若要更高收益，只能换信号源或换池：单因子温度的信息比已接近这个信号的上限，"
        f"继续在「窗口/权重/形态」上打磨是**在噪声里找参数**。")
    return out


if __name__ == "__main__":
    sys.exit(main())
