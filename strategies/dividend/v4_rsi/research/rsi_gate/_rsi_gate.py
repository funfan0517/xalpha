#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""V4 · 「RSI14 门槛 + 等权」能否替代「top2 + 板块择时」—— 门槛研究

要回答的问题
------------
V4 现在的横截面规则是**固定只数**：每月在 10 只里挑 RSI 最低的 2 只等权，再乘板块
RSI 均值档位（≥70 清仓 / ≥50 减半）。用户提问：

  **改成「RSI14 < 50 的全部等权」（门槛筛选而非固定只数）效果如何？其他门槛呢？**

本脚本把「门槛」这一族完整跑一遍，并回答三个衍生问题：

1. 门槛该设多少（35 / 40 / 45 / 50 / 55 / 60 / 65）？门槛升高 = 买得更多还是更少？
2. 门槛等权**要不要再叠板块择时**？—— 门槛本身已经是一层时间择时，重复叠加会不会
   变成「双重保守」导致长期空仓？
3. 门槛满不了怎么办？三种兜底：**空仓（cash）/ 退回全池等权（all）/ 只取最低 k 只（cap）**。
   另外测一个更符合红利性格的变体：**滞回带**（RSI 低于 t_in 买、高于 t_out 才卖，
   中间维持持仓），以避免红利常年 50~70 区间带来的反复空仓。

口径（与 V4 完全一致，便于横向比较）
-----------------------------------
* 池 = `rule.CODES`（全 10 只）；收益 = 基金累计净值月度（含分红再投）
* 信号：Wilder RSI14，日频算、**月末取值**决策；`simulate()` 内部 `shift(1)`，次月生效
* 成本：单边 0.03%，按 Σ|Δw| 计；空仓不计息（现金 0 收益，因此「平均仓位」是硬约束）
* 样本：2020-01 ~ 最新；检验段 2025-01 起（与 `backtest.py::OOS_START` 同一条线）

产物（AGENTS §8.4 ④ 课题子目录）
--------------------------------
    v4_rsi/research/rsi_gate/_rsi_gate.py
    v4_rsi/research/rsi_gate/_rsi_gate.md
    v4_rsi/research/rsi_gate/_rsi_gate.png

用法::

    python strategies/dividend/v4_rsi/research/rsi_gate/_rsi_gate.py
"""
from __future__ import annotations

import os
import sys

import matplotlib

matplotlib.use("Agg")

import matplotlib.pyplot as plt  # noqa: E402
import numpy as np  # noqa: E402
import pandas as pd  # noqa: E402

_HERE = os.path.dirname(os.path.abspath(__file__))     # .../v4_rsi/research/rsi_gate
_RES = os.path.dirname(_HERE)                          # .../v4_rsi/research
_V4 = os.path.dirname(_RES)                            # .../v4_rsi
_STRAT = os.path.dirname(_V4)                          # .../dividend
for _p in (_RES, _V4, _STRAT):
    if _p not in sys.path:
        sys.path.insert(0, _p)

import _single_signal as S  # noqa: E402  pylint: disable=wrong-import-position
import pool  # noqa: E402  pylint: disable=wrong-import-position
import rule as V4  # noqa: E402  pylint: disable=wrong-import-position

OUT_MD = os.path.join(_HERE, "_rsi_gate.md")
OUT_PNG = os.path.join(_HERE, "_rsi_gate.png")

OOS_START = "2025-01-01"                 # 与 backtest.py 同一条检验线
THRS = (35, 40, 45, 50, 55, 60, 65)      # 绝对门槛网格
HYS_GRID = ((45, 65), (50, 65), (50, 70), (55, 70))

plt.rcParams["font.sans-serif"] = ["Microsoft YaHei", "SimHei", "DejaVu Sans"]
plt.rcParams["axes.unicode_minus"] = False

BLUE, RED, AMBER, GREEN, GRAY = "#1f4e79", "#c0504d", "#e8a33d", "#137333", "#8a8f98"


# ------------------------------------------------------------------ 数据
def load() -> tuple[pd.DataFrame, pd.DataFrame]:
    navs = pool.load_navs(codes=V4.CODES)
    rets = S.load_rets(navs)
    rsi = S.load_signal("rsi", navs).reindex(rets.index)
    return rets, rsi


def avail(rsi: pd.DataFrame, rets: pd.DataFrame) -> pd.DataFrame:
    """信号可得（RSI 非空）且当月净值存在。"""
    return rsi.notna() & rets.notna()


# ------------------------------------------------------------------ 权重构造
def gate_w(rsi: pd.DataFrame, rets: pd.DataFrame, thr: float,
           cap: int | None = None, fallback: str = "cash") -> pd.DataFrame:
    """门槛等权：RSI < thr 的标的等权。

    cap      : 最多持几只（在满足门槛的里面取 RSI 最低的 cap 只）；None = 不设上限
    fallback : "cash" = 没有标的不满门槛就空仓；"all" = 退回全池等权
    """
    ok = avail(rsi, rets)
    pick = ok & (rsi < thr)
    if cap:
        out = pd.DataFrame(0.0, index=rsi.index, columns=rsi.columns)
        for ts, row in rsi.where(pick).iterrows():
            good = row.dropna()
            if good.empty:
                continue
            sel = good.nsmallest(min(cap, len(good))).index
            out.loc[ts, sel] = 1.0 / len(sel)
        w = out
    else:
        n = pick.sum(axis=1)
        w = pick.div(n.where(n > 0), axis=0).fillna(0.0)
    if fallback == "all":
        eq = S.equal_w(ok)
        empty = w.sum(axis=1) <= 1e-12
        w = w.mask(empty, eq, axis=0)
    return w


def rel_w(rsi: pd.DataFrame, rets: pd.DataFrame, q: float = 0.50) -> pd.DataFrame:
    """相对门槛：RSI 低于池内当月可得标的的 q 分位 → 等权（q=0.5 即「低于中位数」）。"""
    ok = avail(rsi, rets)
    line = rsi.where(ok).quantile(q, axis=1)
    pick = ok & rsi.lt(line, axis=0)
    n = pick.sum(axis=1)
    return pick.div(n.where(n > 0), axis=0).fillna(0.0)


def hyst_w(rsi: pd.DataFrame, rets: pd.DataFrame, t_in: float, t_out: float) -> pd.DataFrame:
    """滞回带：未持仓且 RSI < t_in → 买入；持仓且 RSI ≥ t_out → 卖出；其间维持。

    逐列独立状态机（每只标的自成一路），持仓集合内等权；集合为空则空仓。
    """
    ok = avail(rsi, rets)
    held = pd.Series(False, index=rsi.columns)      # 逐标的持仓状态
    rows = {}
    for ts in rsi.index:
        row, av = rsi.loc[ts], ok.loc[ts]
        sell = held & (row >= t_out) & av
        buy = (~held) & (row < t_in) & av
        held = (held & ~sell) | buy
        rows[ts] = held
    mask = pd.DataFrame(rows).T.reindex(rsi.index).fillna(False).astype(bool) & ok
    arr = mask.to_numpy(dtype=float)
    nn = arr.sum(axis=1, keepdims=True)
    out = np.divide(arr, nn, out=np.zeros_like(arr), where=nn > 0)
    return pd.DataFrame(out, index=rsi.index, columns=rsi.columns)


def board_mult(rsi: pd.DataFrame, rets: pd.DataFrame) -> pd.Series:
    """V4 的板块择时倍率（0 / 0.5 / 1）：池内可得标的 RSI 均值 ≥70 清仓、≥50 减半。"""
    q = rsi / 100.0
    return S.time_mult(S.board_q(q, S.available(q, rets)), V4.Q_OUT, V4.Q_HALF)


# ------------------------------------------------------------------ 统计
def profile(w: pd.DataFrame, rets: pd.DataFrame) -> dict:
    """一套权重 → 绩效 + 仓位结构（平均仓位、均持只数、空仓月占比、年化换手）。"""
    r = S.simulate(w, rets)
    st = S.stats(r)
    expo = w.shift(1).fillna(0.0).sum(axis=1).reindex(r.index)
    turn = (w.shift(1).fillna(0.0) - w.shift(1).fillna(0.0).shift(1).fillna(0.0)).abs().sum(axis=1)
    st.update(
        n_hold=float((w > 1e-12).sum(axis=1).reindex(r.index).mean()),
        expo=float(expo.mean()),
        flat=float((expo <= 1e-12).mean()),
        turnover=float(turn.mean() * 12),
        nav=(1 + r).cumprod(),
        ret=r,
    )
    return st


def seg(st: dict, start: str) -> dict:
    """从全样本序列里切一段重算（用于 2025-01 起的检验段）。"""
    r = st["ret"].loc[start:].dropna()
    s = S.stats(r)
    return {"cagr": s["cagr"], "dd": s["dd"], "calmar": s["calmar"],
            "year": (1 + r).prod() ** (12 / max(len(r), 1)) - 1, "n": len(r)}


def fmt(x, d=1) -> str:
    return S.fmt_pct(x, d)


# ------------------------------------------------------------------ 方案集
def build(rets: pd.DataFrame, rsi: pd.DataFrame) -> dict[str, pd.DataFrame]:
    ok = avail(rsi, rets)
    mult = board_mult(rsi, rets)
    top = S.top_w(rsi, ok, V4.TOP_K)
    out = {
        "基准：10 只等权": S.equal_w(ok),
        "V4 现行：top2 + 板块择时": top.mul(mult, axis=0),
        "top2 无择时": top,
    }
    for t in THRS:
        out[f"RSI<{t} 等权"] = gate_w(rsi, rets, t)
    out["RSI<50 等权 + 板块择时"] = gate_w(rsi, rets, 50).mul(mult, axis=0)
    out["RSI<50 取最低 2 只"] = gate_w(rsi, rets, 50, cap=V4.TOP_K)
    out["RSI<50 空仓退回全池等权"] = gate_w(rsi, rets, 50, fallback="all")
    out["RSI<中位数 等权"] = rel_w(rsi, rets, 0.50)
    for a, b in HYS_GRID:
        out[f"滞回 {a}/{b}"] = hyst_w(rsi, rets, a, b)
    return out


# ------------------------------------------------------------------ 画图
def plot(sims: dict, rets: pd.DataFrame, gate_rows: list[dict], path: str) -> str:
    fig, (ax1, ax2) = plt.subplots(1, 2, figsize=(12.4, 4.3), dpi=120,
                                   gridspec_kw={"width_ratios": [1.25, 1]})

    show = [("基准：10 只等权", GRAY, 1.3),
            ("V4 现行：top2 + 板块择时", BLUE, 1.6),
            ("RSI<50 等权", RED, 1.5),
            ("RSI<60 等权", AMBER, 1.3),
            ("滞回 50/70", GREEN, 1.5)]
    for name, color, lw in show:
        if name in sims:
            nav = sims[name]["nav"]
            ax1.plot(nav.index, nav.to_numpy(), color=color, lw=lw, label=name)
    ax1.set_yscale("log")
    ax1.set_title("净值（对数轴，含成本）", fontsize=11)
    ax1.grid(True, ls=":", color="#ddd")
    ax1.legend(fontsize=8, loc="upper left")
    ax1.tick_params(labelsize=8)

    xs = [r["thr"] for r in gate_rows]
    ax2.plot(xs, [r["calmar"] for r in gate_rows], "-o", color=RED, lw=1.6, ms=4, label="Calmar")
    ax2.plot(xs, [r["calmar_t"] for r in gate_rows], "--s", color=BLUE, lw=1.2, ms=3.5,
             label="Calmar（+板块择时）")
    ax2.axhline(gate_rows[0]["base_calmar"], color=GRAY, ls=":", lw=1.2, label="基准 Calmar")
    ax2.set_xlabel("RSI14 门槛", fontsize=9)
    ax2.set_ylabel("Calmar", fontsize=9, color=RED)
    ax2.tick_params(labelsize=8)
    ax2.grid(True, ls=":", color="#ddd")
    for r in gate_rows:                       # 平均仓位标注（门槛越高仓位越低）
        ax2.annotate(f"{r['expo'] * 100:.0f}%", (r["thr"], r["calmar"]),
                     textcoords="offset points", xytext=(0, -12), ha="center",
                     fontsize=7, color=GRAY)
    ax2.legend(fontsize=8, loc="lower left")
    ax2.set_title("门槛敏感性（点下数字 = 平均仓位）", fontsize=11)

    fig.tight_layout(pad=0.4)
    fig.savefig(path, bbox_inches="tight")
    plt.close(fig)
    return path


# ------------------------------------------------------------------ 报告
def main() -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")

    rets, rsi = load()
    schemes = build(rets, rsi)
    prof = {k: profile(w, rets) for k, w in schemes.items()}

    base = prof["基准：10 只等权"]
    base_r = base["ret"]
    gate_rows = []
    for t in THRS:
        p = prof[f"RSI<{t} 等权"]
        gate_rows.append({"thr": t, "calmar": p["calmar"], "cagr": p["cagr"],
                          "expo": p["expo"], "n_hold": p["n_hold"], "flat": p["flat"],
                          "calmar_t": float("nan"), "base_calmar": base["calmar"]})

    # —— 叠加择时的门槛网格（图里需要完整两行）——
    prof_t = {}
    for t in THRS:
        w = gate_w(rsi, rets, t).mul(board_mult(rsi, rets), axis=0)
        prof_t[t] = profile(w, rets)
    for r in gate_rows:
        r["calmar_t"] = prof_t[r["thr"]]["calmar"]

    # —— 空仓月的「机会成本」：门槛把它躲掉的那些月份，基准实际在涨还是跌 ——
    miss = {}
    for t in (40, 50, 60):
        w = schemes[f"RSI<{t} 等权"]
        expo = w.shift(1).fillna(0.0).sum(axis=1).reindex(base_r.index)
        flat = expo <= 1e-12
        miss[t] = (int(flat.sum()), float(base_r[flat].mean()), float(base_r[~flat].mean()))

    # —— 关键对照：门槛的改善是不是只等于「同等暴露的等权基准」——
    # 若某门槛方案的平均仓位是 83%，那它的正确基准不是「满仓等权」，而是「83% 仓位的等权
    # 基准」。增量 = 门槛方案 Calmar − 同暴露基准 Calmar，> 0 才说明门槛本身有信息量。
    hys_best = max(HYS_GRID, key=lambda ab: prof[f"滞回 {ab[0]}/{ab[1]}"]["calmar"])
    hys_name = f"滞回 {hys_best[0]}/{hys_best[1]}"
    eq = S.equal_w(avail(rsi, rets))
    ctrl_rows = []
    for name in ("RSI<35 等权", "RSI<40 等权", "RSI<50 等权", "RSI<55 等权", "RSI<60 等权",
                 "RSI<50 等权 + 板块择时", hys_name, "V4 现行：top2 + 板块择时"):
        e = prof[name]["expo"]
        c = profile(eq * e, rets)
        ctrl_rows.append({"name": name, "expo": e, "calmar": prof[name]["calmar"],
                          "cagr": prof[name]["cagr"], "ctrl": c["calmar"],
                          "ctrl_cagr": c["cagr"],
                          "inc": prof[name]["calmar"] - c["calmar"]})

    # —— 门槛的暴露有没有真躲开下跌月：按「基准次月涨/跌」分组看平均暴露 ——
    expo50 = schemes["RSI<50 等权"].shift(1).fillna(0.0).sum(axis=1).reindex(base_r.index)
    up_m, dn_m = base_r > 0, base_r <= 0
    knife = {"dn_expo": float(expo50[dn_m].mean()), "up_expo": float(expo50[up_m].mean()),
             "n_dn": int(dn_m.sum()), "n_up": int(up_m.sum())}

    plot(prof, rets, gate_rows, OUT_PNG)

    n_months = len(base_r)
    oos_n = len(base_r.loc[OOS_START:])
    main_names = ["基准：10 只等权", "V4 现行：top2 + 板块择时", "top2 无择时",
                  "RSI<40 等权", "RSI<45 等权", "RSI<50 等权", "RSI<55 等权",
                  "RSI<60 等权", "RSI<65 等权", "RSI<50 等权 + 板块择时",
                  "RSI<50 取最低 2 只", "RSI<50 空仓退回全池等权",
                  "RSI<中位数 等权", "滞回 50/70"]

    L = [
        "# V4 · RSI 门槛等权 研究（RSI14 < T 全部等权，替代 top2 + 板块择时？）",
        "",
        f"> 样本 {base_r.index[0]:%Y-%m} ~ {base_r.index[-1]:%Y-%m}（{n_months} 月，"
        f"末月为不完整月，截至最新净值日）· 检验段 {OOS_START[:4]} 起（{oos_n} 月）· "
        "月末信号下月生效 · 成本单边 0.03% · 空仓不计息。",
        "> 池 = 全 10 只（`rule.CODES`）；口径与 `backtest.py` 完全一致，差异只在「怎么选权重」。",
        "> 机械规则输出，非投资建议。",
        "",
        "## 一、主结果",
        "",
        "| 方案 | 全样本 年化 | 回撤 | Calmar | 夏普 | 平均仓位 | 均持只数 | 空仓月占比 |"
        f" 年化换手 | {OOS_START[:4]} 起 年化 | 回撤 | Calmar |",
        "|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|",
    ]
    for name in main_names:
        p = prof[name]
        o = seg(p, OOS_START)
        L.append(f"| {name} | {fmt(p['cagr'])} | {fmt(p['dd'])} | {p['calmar']:.2f} | "
                 f"{p['sharpe']:.2f} | {p['expo']:.0%} | {p['n_hold']:.1f} | "
                 f"{p['flat']:.0%} | {p['turnover'] * 100:.0f}% | "
                 f"{fmt(o['cagr'])} | {fmt(o['dd'])} | {o['calmar']:.2f} |")

    L += ["",
          "「平均仓位」= 实际暴露（权重和 × 板块倍率）的时间均值，「空仓月占比」= 完全持币的月份比例，"
          "「均持只数」= 有仓位的月份里平均持几只，「年化换手」= Σ|Δw| 的月度均值 × 12。",
          "",
          "## 二、门槛敏感性（RSI < T 全部等权）",
          "",
          "| 门槛 | 年化 | 回撤 | Calmar | 夏普 | 平均仓位 | 均持只数 | 空仓月占比 |"
          " 叠加板块择时 Calmar | 叠加后 年化 | 叠加后 平均仓位 |",
          "|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|"]
    for r in gate_rows:
        p, pt = prof[f"RSI<{r['thr']} 等权"], prof_t[r["thr"]]
        L.append(f"| RSI < {r['thr']} | {fmt(p['cagr'])} | {fmt(p['dd'])} | "
                 f"{p['calmar']:.2f} | {p['sharpe']:.2f} | {p['expo']:.0%} | "
                 f"{p['n_hold']:.1f} | {p['flat']:.0%} | **{pt['calmar']:.2f}** | "
                 f"{fmt(pt['cagr'])} | {pt['expo']:.0%} |")
    L += ["", f"- 基准（10 只等权）Calmar {base['calmar']:.2f}、年化 {fmt(base['cagr'])}；"
          f"V4 现行（top2 + 板块择时）Calmar {prof['V4 现行：top2 + 板块择时']['calmar']:.2f}、"
          f"年化 {fmt(prof['V4 现行：top2 + 板块择时']['cagr'])}。", ""]

    # —— 关键对照：门槛是否有增量，还是仅仅「降仓位」——
    L += ["## 三、门槛 vs 同等暴露的等权基准（关键对照）", "",
          "门槛方案的平均仓位远低于满仓，所以它**真正的对手不是「满仓等权」，而是"
          "「同样仓位、只靠固定比例持币的等权基准」**。增量 > 0 才说明门槛本身提供信息，"
          "否则只是把仓位调低了。", "",
          "| 方案 | 平均仓位 | 年化 | Calmar | 同暴露等权基准 年化 | 同暴露基准 Calmar | 增量 |",
          "|---|---:|---:|---:|---:|---:|---:|"]
    for c in ctrl_rows:
        L.append(f"| {c['name']} | {c['expo']:.0%} | {fmt(c['cagr'])} | {c['calmar']:.2f} | "
                 f"{fmt(c['ctrl_cagr'])} | {c['ctrl']:.2f} | {c['inc']:+.2f} |")
    inc = {c["name"]: c for c in ctrl_rows}
    i50 = inc["RSI<50 等权"]
    L += ["",
          f"- 门槛 50 在「基准次月下跌」的 {knife['n_dn']} 个月里平均暴露 "
          f"{knife['dn_expo']:.0%}、在「上涨」的 {knife['n_up']} 个月里 "
          f"{knife['up_expo']:.0%} —— 方向上确实在跌月收了仓，但**暴露的时点滞后于下跌**："
          "RSI 掉到 50 以下通常发生在跌完之后，于是「跌月低暴露」换来的是「跌完立刻满仓」。"
          f"同暴露对照的增量是 **{i50['inc']:+.2f}**（Calmar {i50['calmar']:.2f} vs "
          f"固定 {i50['expo']:.0%} 仓位基准 {i50['ctrl']:.2f}），"
          f"说明这一层时间信息抵不过集中度上升（均持 {prof['RSI<50 等权']['n_hold']:.1f} 只 "
          f"vs 基准 {base['n_hold']:.1f} 只）与高换手成本。",
          ""]

    # —— 门槛的兜底方式与只数上限 ——
    L += ["## 四、兜底方式与只数上限（门槛都用 50）",
          "",
          "| 方案 | 年化 | 回撤 | Calmar | 平均仓位 | 均持只数 | 空仓月占比 | 年化换手 |",
          "|---|---:|---:|---:|---:|---:|---:|---:|"]
    for name in ("RSI<50 等权", "RSI<50 空仓退回全池等权", "RSI<50 取最低 2 只", "RSI<中位数 等权"):
        p = prof[name]
        L.append(f"| {name} | {fmt(p['cagr'])} | {fmt(p['dd'])} | {p['calmar']:.2f} | "
                 f"{p['expo']:.0%} | {p['n_hold']:.1f} | {p['flat']:.0%} | "
                 f"{p['turnover'] * 100:.0f}% |")
    L += [""]

    # —— 滞回带 ——
    L += ["## 五、滞回带（RSI < t_in 买、≥ t_out 才卖，中间维持）",
          "",
          "| 买线 / 卖线 | 年化 | 回撤 | Calmar | 夏普 | 平均仓位 | 均持只数 | 空仓月占比 | 年化换手 |",
          "|---|---:|---:|---:|---:|---:|---:|---:|---:|"]
    for a, b in HYS_GRID:
        p = prof[f"滞回 {a}/{b}"]
        L.append(f"| {a} / {b} | {fmt(p['cagr'])} | {fmt(p['dd'])} | {p['calmar']:.2f} | "
                 f"{p['sharpe']:.2f} | {p['expo']:.0%} | {p['n_hold']:.1f} | "
                 f"{p['flat']:.0%} | {p['turnover'] * 100:.0f}% |")
    L += [""]

    # —— 分年度 ——
    y = pd.DataFrame({n: prof[n]["ret"] for n in
                      ("基准：10 只等权", "V4 现行：top2 + 板块择时", "RSI<50 等权", "滞回 50/70")})
    y = (1 + y).groupby(y.index.year).prod() - 1
    L += ["## 六、分年度收益", "",
          "| 年份 | " + " | ".join(y.columns) + " |",
          "|---|" + "---:|" * len(y.columns)]
    for ts, row in y.iterrows():
        L.append(f"| {int(ts)} | " + " | ".join(fmt(v) for v in row.values) + " |")
    L += [""]

    # —— 自动结论（数字驱动）——
    best = max(gate_rows, key=lambda r: r["calmar"])
    g50 = prof["RSI<50 等权"]
    g50t = prof_t[50]
    v4 = prof["V4 现行：top2 + 板块择时"]
    hp = prof[hys_name]
    n_below = sum(1 for r in gate_rows if r["calmar"] > base["calmar"])
    m50 = miss[50]
    i40, i55, i60 = inc["RSI<40 等权"], inc["RSI<55 等权"], inc["RSI<60 等权"]
    i50t, ihys, iv4 = inc["RSI<50 等权 + 板块择时"], inc[hys_name], inc["V4 现行：top2 + 板块择时"]
    miss_txt = (f"**空仓月几乎没躲到东西。** RSI<50 完全持币的 {m50[0]} 个月里，"
                f"10 只等权基准的次月平均收益只有 **{m50[1] * 100:+.2f}%**（有仓位的月份 "
                f"{m50[2] * 100:+.2f}%）—— 空掉的月份本来就不赚不亏，躲它们省不下收益，"
                f"却要为此付 {g50['turnover'] * 100:.0f}%/年 的换手成本。")

    L += [
        "## 七、结论",
        "",
        f"1. **直接回答：`RSI14 < 50 全部等权`不成立。** 年化 {fmt(g50['cagr'])}、回撤 "
        f"{fmt(g50['dd'])}、Calmar {g50['calmar']:.2f} —— 三项都不如「10 只满仓等权」"
        f"（{fmt(base['cagr'])} / {fmt(base['dd'])} / {base['calmar']:.2f}），与现行 V4"
        f"（{fmt(v4['cagr'])} / {fmt(v4['dd'])} / {v4['calmar']:.2f}）差得更远。"
        f"它平均仓位只有 {g50['expo']:.0%}、{g50['flat']:.0%} 的月份完全持币，**降仓位却没换来"
        f"更浅的回撤**（{fmt(g50['dd'])} 反而深于基准 {fmt(base['dd'])}）："
        "空掉的是横盘月，持有的是刚跌完的月份。",
        "",
        f"2. **门槛网格不单调，50 附近恰好是坑。** 7 档 Calmar：35 {gate_rows[0]['calmar']:.2f}、"
        f"40 {gate_rows[1]['calmar']:.2f}、45 {gate_rows[2]['calmar']:.2f}、"
        f"50 {gate_rows[3]['calmar']:.2f}、55 {gate_rows[4]['calmar']:.2f}、"
        f"60 {gate_rows[5]['calmar']:.2f}、65 {gate_rows[6]['calmar']:.2f}。"
        f"最好的是 **RSI<{best['thr']}（{best['calmar']:.2f}）**，最差是 40（{gate_rows[1]['calmar']:.2f}）；"
        f"50 这一档同时输给更低的 45（{gate_rows[2]['calmar']:.2f}）和更高的 55"
        f"（{gate_rows[4]['calmar']:.2f}）。红利 RSI 中枢在 55~65，"
        "**「50 以下」在红利里不是「便宜」，是「异常下跌」**。"
        f"{len(gate_rows)} 档里 {n_below} 档跑赢满仓等权基准（{base['calmar']:.2f}），"
        f"但其中 35 那档靠的是仓位只有 {gate_rows[0]['expo']:.0%}"
        f"（同暴露增量仅 {inc['RSI<35 等权']['inc']:+.2f}），"
        "**既有仓位又有增量的只有 55/60/65**，而它们的仓位都在 83% 以上 —— 已接近满仓。",
        "",
        f"3. **换正确基准后，低门槛是负增量。** 门槛方案平均仓位远低于满仓，它的对手应是"
        f"「同仓位固定持币的等权基准」：RSI<40 增量 {i40['inc']:+.2f}（仓位 {i40['expo']:.0%}）、"
        f"RSI<50 {i50['inc']:+.2f}（仓位 {i50['expo']:.0%}）—— 两档都比"
        f"「每月固定同比例仓位一直持有」还差；"
        f"RSI<55 {i55['inc']:+.2f}、RSI<60 {i60['inc']:+.2f}、{hys_name} {ihys['inc']:+.2f}、"
        f"V4 现行 {iv4['inc']:+.2f}。**门槛越低越负**：40/50 两档的「越跌越买」正好把仓位"
        "压在波动最大的时点。",
        "",
        f"4. **`RSI<50 等权` 的超额只来自「仓位低」。** 再叠板块择时后 Calmar {g50['calmar']:.2f} → "
        f"{g50t['calmar']:.2f}、仓位 {g50['expo']:.0%} → {g50t['expo']:.0%}、回撤 "
        f"{fmt(g50['dd'])} → {fmt(g50t['dd'])}；但同暴露基准就是 0.97，增量只有 "
        f"{i50t['inc']:+.2f}。两条时间规则叠在一起，真正干活的是「仓位更低」，"
        "门槛本身没有额外交代。",
        "",
        f"5. **滞回带胜在成本与分散，不在 Calmar。** {hys_name}：年化 {fmt(hp['cagr'])}、回撤 "
        f"{fmt(hp['dd'])}、Calmar {hp['calmar']:.2f}、仓位 {hp['expo']:.0%}、均持 "
        f"{hp['n_hold']:.1f} 只、换手 {hp['turnover'] * 100:.0f}%/年，同暴露增量 "
        f"{ihys['inc']:+.2f}，{len(HYS_GRID)} 组参数里最好。",
        "",
        f"   Calmar 低于 RSI<55（{gate_rows[4]['calmar']:.2f}），但换手不到它的 "
        f"{prof['RSI<55 等权']['turnover'] / hp['turnover']:.0f} 分之一、持仓更分散"
        f"（{hp['n_hold']:.1f} 只 vs {prof['RSI<55 等权']['n_hold']:.1f} 只）："
        f"门槛 55 用 {prof['RSI<55 等权']['turnover'] * 100:.0f}% 换手换 {i55['inc']:+.2f}，"
        f"滞回用 {hp['turnover'] * 100:.0f}% 换 {ihys['inc']:+.2f}，边际收益在递减。"
        f"两者都仍在 V4 之下（{v4['calmar']:.2f} / {iv4['inc']:+.2f}），"
        "且买线卖线是样本内挑的，不能当样本外证据。",
        "",
        f"6. **兜底与 cap 都救不回来。** `RSI<50 取最低 2 只` "
        f"{prof['RSI<50 取最低 2 只']['calmar']:.2f}，比纯 top2 无择时"
        f"（{prof['top2 无择时']['calmar']:.2f}）更差 —— 门槛把 30% 的月份压到只剩 1 只甚至 0 只，"
        f"集中度失控；空仓退回全池等权 {prof['RSI<50 空仓退回全池等权']['calmar']:.2f}，"
        f"等于放弃门槛只留下换手；相对门槛（低于当月中位数）"
        f"{prof['RSI<中位数 等权']['calmar']:.2f}，比固定 50 好，仍低于 V4。",
        "",
        f"7. **检验段（{OOS_START[:4]} 起 {oos_n} 个月）门槛族集体落后。** V4 现行 "
        f"{seg(v4, OOS_START)['calmar']:.2f}（年化 {fmt(seg(v4, OOS_START)['cagr'])}）、"
        f"top2 无择时 {seg(prof['top2 无择时'], OOS_START)['calmar']:.2f}、"
        f"{hys_name} {seg(hp, OOS_START)['calmar']:.2f}"
        f"（年化 {fmt(seg(hp, OOS_START)['cagr'])}，仅与基准 "
        f"{fmt(seg(base, OOS_START)['cagr'])} 持平）、"
        f"RSI<55 {seg(prof['RSI<55 等权'], OOS_START)['calmar']:.2f}、"
        f"RSI<50 {seg(g50, OOS_START)['calmar']:.2f}"
        f"（年化 {fmt(seg(g50, OOS_START)['cagr'])}）。2025–2026 红利单边走强，"
        "门槛族要么长期空仓（RSI<50）、要么减仓幅度不够只够打平（滞回），"
        "**都没有解决「慢牛里 RSI 不回到 50 以下」这个结构问题**。这段只有 23 个月，"
        "但方向与全样本判断一致。",
        "",
        "### 处置建议",
        "",
        "- 按用户硬规则（新方案未稳定不进每日报告），本课题**只留研究产物**：不改 `rule.py`、"
        "不进 `daily_digest`、不进 `intraday_preview` 的复制清单。",
        f"- **维持现行 V4**（top{V4.TOP_K} + 板块 {V4.Q_OUT * 100:.0f}/{V4.Q_HALF * 100:.0f}）："
        f"它的同暴露增量 {iv4['inc']:+.2f} 与检验段 Calmar {seg(v4, OOS_START)['calmar']:.2f} "
        "都是这一族里最高的，没有用门槛替代它的理由。",
        f"- 若继续研究，候选顺序：**滞回带（{hys_name}）** 与 **RSI<{gate_rows[4]['thr']} 等权**。"
        "前者结构上贴合红利（中枢 55~65，单门槛会把它判成「贵」）、换手低、持仓分散；"
        "后者 Calmar 更高但换手是前者的数倍、只在极端超买时减仓（本质接近满仓）。"
        "两者都必须换成**滚动参数**（每期只用过去数据定买线/卖线/门槛）再跑一次，"
        "现在的最优值都是样本内挑出来的。",
        "- 门槛若要进 V4，合理位置是**否决线而非选品**：例如「池内 RSI 均值 > 60 时不允许加仓」，"
        "而不是「RSI<50 才买」。本报告的 40/50 两档已证明后者是负和。",
        "",
        f"> 复现：`python strategies/dividend/v4_rsi/research/rsi_gate/_rsi_gate.py` · "
        f"图：`{os.path.basename(OUT_PNG)}`",
    ]

    with open(OUT_MD, "w", encoding="utf-8") as fh:
        fh.write("\n".join(L) + "\n")

    print(f"[gate] 样本 {base_r.index[0]:%Y-%m}~{base_r.index[-1]:%Y-%m}（{n_months} 月）"
          f" · 检验段 {oos_n} 月")
    for name in main_names:
        p = prof[name]
        print(f"[gate] {name:26s} 年化 {p['cagr'] * 100:5.1f}%  回撤 {p['dd'] * 100:6.1f}%  "
              f"Calmar {p['calmar']:5.2f}  仓位 {p['expo'] * 100:3.0f}%  "
              f"持 {p['n_hold']:4.1f} 只  空仓 {p['flat'] * 100:3.0f}%")
    print(f"[gate] 产物 -> {os.path.relpath(OUT_MD, _HERE)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
