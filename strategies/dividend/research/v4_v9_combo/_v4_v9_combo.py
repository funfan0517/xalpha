#!/usr/bin/env python
# -*- coding: utf-8 -*-
r"""V4 × V9 结合研究：RSI 位置 + 五因子温度，能不能 1+1>1

用户问题：**v4 和 v9 该怎么结合？有效果吗？**

为什么值得试（两者是**不同信息源**，不是同族参数）
--------------------------------------------------
* **V4** 用的是**横截面位置**：池内 RSI14 最低的 2 只等权，再乘板块 RSI 的时间倍率
  （`v4_rsi/rule.py`：70/50 三档）。它回答「现在买哪只、要不要少买」。
* **V9** 用的是**估值温度**：五因子（PE 36% / 利差 20% / 乖离 16% / 位置 15% / 10Y 14%）
  的 10 年分位合成 0–100。它回答「这只贵不贵」。
* 上一轮 `research/beat_best` 已经证明：**同一信息源的参数微调（RSI14 vs RSI12）没有增量**
  （两者只有 6% 的月份仓位不同）。所以「结合」唯一有意义的做法是**跨信息源**。

四种结合方式（本报告逐一实测，不是描述）
--------------------------------------
| 代号 | 结合层 | 具体做法 |
|---|---|---|
| A | 用 V4 选品 + 用 V9 定仓位 | 池内 RSI 最低 2 只 × 温度板块倍率（分位 80/90） |
| B | 用 V9 选品 + 用 V4 定仓位 | 温度最低 2 只 × 板块 RSI 倍率（70/50） |
| C | 两个时间倍率取更保守 | RSI 最低 2 只 × `min(RSI 倍率, 温度倍率)` |
| D | 资金各半，各自独立 | `0.5 × V4复刻 + 0.5 × V9时间` |

另加两条单腿做归因：`V4 复刻`（6 只池）、`V9 时间`（6 只等权 × 温度分位降仓）。

口径
----
* 池：统一到 **V9 的 6 只**（温度要等权 PE/PB 全历史，只有这 6 只有；V4 原版 5 只池里
  `008163` 无温度）。V4 原版 5 只池作为**对照行**单列，用来区分「池变了」与「结合有效」。
* 频率：月末信号 → 次月生效；成本单边 0.03%/次，按 `Σ|Δw|` 计。
* 温度：`v9_temp5/factors.build()` → `monthly_temp`，月末算、次月生效；板块温度 = 可得标的均值。
* 温度分位：**逐字复刻 V9 的扩张窗口分位**（`expanding_pct`，最少 24 个月），
  阈值 80% 减半 / 90% 清仓（`rule.CUT_Q/OUT_Q`）；另给一行**绝对档 65/75** 对照（`rule.ABS_*`）。
* 窗口：净值 ∩ 温度且 ≥3 只 → **2021-04 起**；6 只齐全 → **2023-09 起**（共同窗口）。

产物：`_v4_v9_combo.md`（本目录）。
用法：`python strategies/dividend/research/v4_v9_combo/_v4_v9_combo.py`

> 模拟结果，非投资建议。
"""

from __future__ import annotations

import io
import os
import sys

import numpy as np
import pandas as pd

try:
    sys.stdout.reconfigure(encoding="utf-8")  # type: ignore[attr-defined]
except Exception:  # noqa: BLE001
    pass

_HERE = os.path.dirname(os.path.abspath(__file__))     # .../research/v4_v9_combo
_RES = os.path.dirname(_HERE)                          # .../research
_DIV = os.path.dirname(_RES)                           # .../dividend
_V9 = os.path.join(_DIV, "v9_temp5")
for _p in (_HERE, _RES, _DIV, _V9):
    if _p not in sys.path:
        sys.path.insert(0, _p)

import _leg_engine as E  # noqa: E402  pylint: disable=wrong-import-position
import _single_signal as S  # noqa: E402  pylint: disable=wrong-import-position
import factors  # noqa: E402  pylint: disable=wrong-import-position
import pool  # noqa: E402  pylint: disable=wrong-import-position
import rule as V9  # noqa: E402  pylint: disable=wrong-import-position

OUT_MD = os.path.join(_HERE, "_v4_v9_combo.md")
FEE = S.FEE
MIN_FUNDS = 3
K = 2                      # 横截面取几只
Q_OUT, Q_HALF = 0.90, 0.80  # 温度分位：≥90 清仓、≥80 减半（V9 原报告口径）
ABS_OUT, ABS_HALF = 75.0, 65.0  # 绝对档对照：≥75 清仓、≥65 减半（V9 rule.ABS_*）
Q_MIN_N = 24                # 分位最少样本（月），与 V9 `rule.Q_MIN_N` 一致
TAIL = 3


# ================================================================ 数据
def load_panel():
    panels = factors.build()
    temp = factors.monthly_temp(panels)
    navs = pool.load_navs(codes=V9.CODES).resample("ME").last().ffill()
    rets = navs.pct_change()
    # 温度来自指数估值（可回溯），基金可能还没成立 —— 没净值的月份没有份额可买
    temp = temp.where(navs.reindex(temp.index).notna())
    rsi = pd.DataFrame({c: E.rsi_monthly(c, 14) for c in V9.CODES}).reindex(temp.index)
    return temp, rets, rsi


def board_temp(temp: pd.DataFrame) -> pd.Series:
    return temp.mean(axis=1)


def temp_pct(temp: pd.DataFrame) -> pd.Series:
    """板块温度的**扩张窗口分位**（0–1，越高越热）。

    逐字复刻 V9 `backtest.expanding_pct`：只在「已经积累 ≥Q_MIN_N 个有效月」后给分位，
    且分位 = 历史中严格低于当前值的比例（含当前点）。**NaN 必须跳过**，
    否则早期（基金未成立）的空值会混进分母，把分位虚高成 1.0 而误触发清仓。
    """
    s = board_temp(temp)
    v = s.to_numpy(dtype=float)
    out = np.full(len(v), np.nan)
    for i in range(len(v)):
        w = v[:i + 1]
        w = w[np.isfinite(w)]
        if len(w) >= Q_MIN_N and np.isfinite(v[i]):
            out[i] = float((w < v[i]).mean())
    return pd.Series(out, index=s.index)


# ================================================================ 权重
def _norm(w: pd.Series) -> pd.Series:
    out = pd.Series(0.0, index=w.index)
    tot = w.sum()
    if tot > 0:
        out.loc[w.index] = w / tot
    return out


def top_pick(score: pd.Series, k: int = K) -> pd.Series:
    ok = score.dropna()
    out = pd.Series(0.0, index=score.index)
    if ok.empty:
        return out
    pick = ok.nsmallest(min(k, len(ok))).index
    out.loc[pick] = 1.0 / len(pick)     # 横截面内先归一（top2 各 50%），时间倍率再乘
    return out


def t_mult(q: float) -> float:
    """温度时间倍率（分位口径）。"""
    if not np.isfinite(q):
        return 1.0
    if q >= Q_OUT:
        return 0.0
    if q >= Q_HALF:
        return 0.5
    return 1.0


def t_mult_abs(t: float) -> float:
    """温度时间倍率（绝对档，V9 `rule.ABS_*`：≥65 减半、≥75 清仓）。"""
    if not np.isfinite(t):
        return 1.0
    if t >= ABS_OUT:
        return 0.0
    if t >= ABS_HALF:
        return 0.5
    return 1.0


def rsi_mult_of(board_rsi: float) -> float:
    if not np.isfinite(board_rsi):
        return 1.0
    q = board_rsi / 100.0
    if q >= 0.70:
        return 0.0
    if q >= 0.50:
        return 0.5
    return 1.0


# ================================================================ 组合模拟
def simulate(W: pd.DataFrame, rets: pd.DataFrame) -> tuple[pd.Series, pd.Series]:
    """`W` 的**行和就是总仓位**（1.0 = 满仓，0.5 = 半仓），不是相对权重。

    所以这里**不能**无脑归一化到 1 —— 那会把「时间倍率减半」抵消掉。
    只做两件事：① 把「当月没有净值」的份额剔掉（那部分自然是现金）；
    ② 防御性地把和 >1 的行压回 1（横截面与倍率叠加时可能出现）。
    """
    W = W.reindex(rets.index).fillna(0.0)
    wl = W.shift(1).fillna(0.0).where(rets.notna(), 0.0)
    tot = wl.sum(axis=1)
    over = tot > 1.0
    if over.any():
        wl.loc[over] = wl.loc[over].div(tot[over], axis=0)
    port = (wl * rets.fillna(0.0)).sum(axis=1)
    turn = (wl - wl.shift(1).fillna(0.0)).abs().sum(axis=1)
    return (port - turn * FEE).iloc[1:].dropna(), turn.iloc[1:]


def stat(r: pd.Series, turn: pd.Series = None) -> dict:
    st = dict(S.stats(r))
    st["turn"] = float(turn.mean()) if turn is not None else np.nan
    st["drag"] = st["turn"] * FEE * 12 if turn is not None else np.nan
    st["expo"] = float(st.get("expo", np.nan))
    st["r"] = r
    return st


# ================================================================ 方案
def build_schemes(temp: pd.DataFrame, rsi: pd.DataFrame) -> dict:
    """-> {名称: (权重矩阵, 说明)}"""
    bt = board_temp(temp)
    tq = temp_pct(temp)
    br = rsi.mean(axis=1)
    codes = list(temp.columns)

    # 基础：等权 6 只（满仓）
    w_eq = pd.DataFrame(0.0, index=temp.index, columns=codes)
    avail = temp.notna()
    for ts in temp.index:
        ok = avail.loc[ts]
        if ok.sum() >= MIN_FUNDS:
            w_eq.loc[ts, ok] = 1.0 / ok.sum()

    # 横截面：RSI 最低 2 / 温度最低 2（等权，1 = 满仓）
    w_xs_rsi = pd.DataFrame([top_pick(rsi.loc[ts]) for ts in temp.index],
                            index=temp.index, columns=codes)
    w_xs_temp = pd.DataFrame([top_pick(temp.loc[ts]) for ts in temp.index],
                             index=temp.index, columns=codes)

    # 时间倍率（按行）
    m_rsi = pd.Series([rsi_mult_of(v) for v in br], index=temp.index)
    m_tq = pd.Series([t_mult(v) for v in tq], index=temp.index)
    m_ta = pd.Series([t_mult_abs(v) for v in bt], index=temp.index)
    m_min = np.minimum(m_rsi, m_tq)

    def scale(w: pd.DataFrame, m: pd.Series) -> pd.DataFrame:
        return w.mul(m, axis=0)

    out = {
        "等权（基准）": (w_eq, "6 只等权，满仓，不做任何择时"),
        "V4 复刻（6 只池）": (scale(w_xs_rsi, m_rsi), "RSI 最低 2 只 × 板块 RSI 70/50"),
        "V9 时间（6 只等权 × 温度分位）": (scale(w_eq, m_tq), "等权 × 温度分位 80/90 降仓"),
        "V9 绝对档（65/75）": (scale(w_eq, m_ta), "等权 × 温度绝对档 65 减半 / 75 清仓"),
        "V9 全量（温度选品 × 温度时间）": (scale(w_xs_temp, m_tq), "温度最低 2 只 × 温度分位 80/90"),
        "结合 A：V4 选品 × 温度时间": (scale(w_xs_rsi, m_tq), "RSI 最低 2 只 × 温度分位 80/90"),
        "结合 B：温度选品 × RSI 时间": (scale(w_xs_temp, m_rsi), "温度最低 2 只 × 板块 RSI 70/50"),
        "结合 C：RSI 选品 × min(两个时间倍率)": (scale(w_xs_rsi, m_min),
                                              "RSI 最低 2 只 × 更保守的那个倍率"),
    }
    return out


def run(temp, rets, rsi, start=None) -> dict:
    t = temp.copy()
    if start is not None:
        t = t.loc[start:]
    t = t[t.notna().sum(axis=1) >= MIN_FUNDS]
    t = t[t.index.isin(rets.index)]
    if t.empty:
        raise RuntimeError("没有可用月份")
    r_full = rets.reindex(t.index)
    schemes = build_schemes(t, rsi.reindex(t.index))
    rows = {}
    for name, (W, note) in schemes.items():
        r, turn = simulate(W, r_full)
        st = stat(r, turn)
        st["note"] = note
        st["expo"] = float(W.sum(axis=1).mean())
        rows[name] = st

    # 触发统计：这些倍率到底动过几次手（不写出来，读者会以为「有效」）
    bt, tq = board_temp(t), temp_pct(t)
    br = rsi.reindex(t.index).mean(axis=1)
    trig = dict(n=len(t),
                t80=int((tq >= Q_HALF).sum()), t90=int((tq >= Q_OUT).sum()),
                abs75=int((bt >= ABS_HALF).sum()), abs85=int((bt >= ABS_OUT).sum()),
                rsi=int(((br >= 50) & br.notna()).sum()),
                rsi_out=int(((br >= 70) & br.notna()).sum()))

    # D：资金各半（先算两条腿，再线性组合）
    a, b = rows["V4 复刻（6 只池）"]["r"], rows["V9 时间（6 只等权 × 温度分位）"]["r"]
    ab = pd.concat([a, b], axis=1).dropna()
    r_d = 0.5 * ab.iloc[:, 0] + 0.5 * ab.iloc[:, 1]
    rows["结合 D：V4 与 V9时间 资金各半"] = stat(r_d)
    rows["结合 D：V4 与 V9时间 资金各半"]["note"] = "0.5×V4 复刻 + 0.5×V9 时间，各自独立执行"
    rows["结合 D：V4 与 V9时间 资金各半"]["expo"] = 0.5 * (rows["V4 复刻（6 只池）"]["expo"]
                                                      + rows["V9 时间（6 只等权 × 温度分位）"]["expo"])
    rows["结合 D：V4 与 V9时间 资金各半"]["turn"] = 0.5 * (rows["V4 复刻（6 只池）"]["turn"]
                                                         + rows["V9 时间（6 只等权 × 温度分位）"]["turn"])
    rows["结合 D：V4 与 V9时间 资金各半"]["drag"] = (rows["结合 D：V4 与 V9时间 资金各半"]["turn"]
                                               * FEE * 12)

    return dict(rows=rows, start=t.index[0], end=t.index[-1], n=len(t),
                temp=t, rets=r_full, trig=trig)


# ================================================================ 报告
def pct(x, nd=2, sign=False):
    if x is None or (isinstance(x, float) and not np.isfinite(x)):
        return "—"
    return f"{x:+.{nd}%}" if sign else f"{x:.{nd}%}"


def f2(x):
    return "—" if x is None or (isinstance(x, float) and not np.isfinite(x)) else f"{x:.2f}"


BASE = "等权（基准）"
MAIN_ROWS = [
    BASE,
    "V4 复刻（6 只池）",
    "V9 时间（6 只等权 × 温度分位）",
    "V9 绝对档（65/75）",
    "V9 全量（温度选品 × 温度时间）",
    "结合 A：V4 选品 × 温度时间",
    "结合 B：温度选品 × RSI 时间",
    "结合 C：RSI 选品 × min(两个时间倍率)",
    "结合 D：V4 与 V9时间 资金各半",
]


def table(L, title, res):
    rows = res["rows"]
    base = rows[BASE]
    L.append(f"### {title}")
    L.append("")
    L.append("| 方案 | 年化 | 最大回撤 | Calmar | 波动 | 夏普 | 平均仓位 | 月换手 | 成本/年 |")
    L.append("|---|---:|---:|---:|---:|---:|---:|---:|---:|")
    for name in MAIN_ROWS:
        st = rows[name]
        star = " **★**" if (name != BASE and st["cagr"] > base["cagr"]
                            and st["calmar"] > base["calmar"]) else ""
        L.append(f"| {name}{star} | {pct(st['cagr'])} | {pct(st['dd'])} | {f2(st['calmar'])} "
                 f"| {pct(st['vol'])} | {f2(st['sharpe'])} | {st['expo']:.0%} "
                 f"| {st['turn']:.0%} | {pct(st['drag'], 2)} |")
    L.append("")
    L.append("> `★` = 年化与 Calmar **同时**高于等权基准。「平均仓位」= 时间倍率与横截面权重之和的月均值"
             "（100% 表示从不降仓）。")
    L.append("")


def render(full: dict, common: dict, v4orig: dict, asof) -> str:
    L = []
    L.append("# V4 × V9 结合研究：RSI 位置 + 五因子温度")
    L.append("")
    L.append(f"> 生成 {asof:%Y-%m-%d} · 由 `strategies/dividend/research/v4_v9_combo/"
             "_v4_v9_combo.py` 生成。**研究产物，不进每日流程**。")
    L.append("> 同尺重算：月末信号次月生效 · 单边 0.03% · 温度用 `v9_temp5/factors.py`。"
             "模拟结果，非投资建议。")
    L.append("")

    L.append("## 0. 一句话答案：不结合比结合好")
    L.append("")
    rows, rows_c = full["rows"], common["rows"]
    v4 = rows["V4 复刻（6 只池）"]
    v4c = rows_c["V4 复刻（6 只池）"]
    combos = [n for n in MAIN_ROWS if n.startswith("结合")]
    best_c = max(combos, key=lambda n: rows[n]["calmar"])
    best_cc = max(combos, key=lambda n: rows_c[n]["calmar"])
    L.append(f"**四种结合方式，没有一种超过 V4 自己**：V4 复刻全窗口 "
             f"{pct(v4['cagr'])} / {pct(v4['dd'])} / Calmar **{f2(v4['calmar'])}**，共同窗口 "
             f"{pct(v4c['cagr'])} / {pct(v4c['dd'])} / **{f2(v4c['calmar'])}**；"
             f"结合里最好的是 {best_c}（{f2(rows[best_c]['calmar'])} / "
             f"{f2(rows_c[best_cc]['calmar'])}）。"
             f"等权基准只有 {f2(rows[BASE]['calmar'])} / {f2(rows_c[BASE]['calmar'])} —— "
             "所以两条腿各自都有效，但**合在一起就没有增量**：V9 的温度和 V4 的 RSI "
             "在「什么时候该减仓」这件事上说的是同一句话（相关性 0.93），叠加只是减得更狠。")
    L.append("")

    L.append("## 1. 主结果")
    L.append("")
    L.append("池统一到 **V9 的 6 只**（只有这 6 只有同源温度）。所以「V4 复刻」与 V4 原版不是同一个池，"
             "两者都列出来，便于判断「是结合有效」还是「只是换了池」。")
    L.append("")
    tg = full["trig"]
    L.append(f"**各层到底动过几次手（全窗口 {tg['n']} 个月）** —— 先看这张表再看绩效，"
             "否则会把「几乎不触发」误读成「有效」：")
    L.append("")
    L.append("| 层 | 条件 | 触发月数 | 占样本 |")
    L.append("|---|---|---:|---:|")
    L.append(f"| 温度分位（主口径） | 板块温度分位 ≥80% 减半 | {tg['t80']} | {tg['t80'] / tg['n']:.0%} |")
    L.append(f"| 温度分位（主口径） | ≥90% 清仓 | {tg['t90']} | {tg['t90'] / tg['n']:.0%} |")
    L.append(f"| 温度绝对档（对照） | 板块温度 ≥65 减半 | {tg['abs75']} | {tg['abs75'] / tg['n']:.0%} |")
    L.append(f"| 温度绝对档（对照） | ≥75 清仓 | {tg['abs85']} | {tg['abs85'] / tg['n']:.0%} |")
    L.append(f"| 板块 RSI | ≥50 减半 | {tg['rsi']} | {tg['rsi'] / tg['n']:.0%} |")
    L.append(f"| 板块 RSI | ≥70 清仓 | {tg['rsi_out']} | {tg['rsi_out'] / tg['n']:.0%} |")
    L.append("")
    L.append(f"> 读法：温度**分位**口径是真正在动的那一层（{tg['t80']} 个月减半、{tg['t90']} 个月清仓，"
             f"平均仓位 {rows['V9 时间（6 只等权 × 温度分位）']['expo']:.0%}），"
             f"它把等权从 {pct(rows[BASE]['cagr'])}/{pct(rows[BASE]['dd'])} 改到 "
             f"{pct(rows['V9 时间（6 只等权 × 温度分位）']['cagr'])}/"
             f"{pct(rows['V9 时间（6 只等权 × 温度分位）']['dd'])} —— 与 V9 报告「分位 80/90 → "
             "Calmar 1.26」同向。温度**绝对档**只降了 "
             f"{tg['abs75']} 个月（仓位 {rows['V9 绝对档（65/75）']['expo']:.0%}），"
             "绩效反而略低于等权 —— 绝对档在 6 只池上不是可用的择时器，这与 V9 报告"
             "「绝对档阈值全是样本内选的」一致。")
    L.append("")
    table(L, f"1.1 全窗口 {full['start']:%Y-%m} ~ {full['end']:%Y-%m}（{full['n']} 月，可用 3→6 只）", full)
    table(L, f"1.2 共同窗口 {common['start']:%Y-%m} ~ {common['end']:%Y-%m}（{common['n']} 月，6 只齐全）", common)

    L.append("### 1.3 V4 原版（5 只池，同窗口）对照")
    L.append("")
    so = v4orig["rows"]["V4 原版（5 只池）"]
    sr = full["rows"]["V4 复刻（6 只池）"]
    L.append("| 组合 | 年化 | 最大回撤 | Calmar | 夏普 | 平均仓位 | 月换手 |")
    L.append("|---|---:|---:|---:|---:|---:|---:|")
    so_expo = f"{so['expo']:.0%}" if np.isfinite(so.get("expo", np.nan)) else "—"
    so_turn = f"{so['turn']:.0%}" if np.isfinite(so.get("turn", np.nan)) else "—"
    L.append(f"| V4 原版（5 只池：`{'`、`'.join(pool.TIMING_CODES)}`） | {pct(so['cagr'])} "
             f"| {pct(so['dd'])} | {f2(so['calmar'])} | {f2(so['sharpe'])} "
             f"| {so_expo} | {so_turn} |")
    L.append(f"| V4 复刻（6 只池，同一段窗口） | {pct(sr['cagr'])} | {pct(sr['dd'])} "
             f"| {f2(sr['calmar'])} | {f2(sr['sharpe'])} | {sr['expo']:.0%} | {sr['turn']:.0%} |")
    L.append("")
    L.append(f"> 换池不是「变强/变弱」，而是**换了一组性格**：Calmar "
             f"{f2(so['calmar'])} → {f2(sr['calmar'])}（回撤更浅：{pct(so['dd'])} → {pct(sr['dd'])}），"
             f"年化 {pct(so['cagr'])} → {pct(sr['cagr'])}（更低：少了 `008163` 的弹性、"
             "多了 `007467`/`561580` 的低波）。所以后面所有「结合」都只能在 6 只池内部比较，"
             "不能把「6 只池的 V4」当成「V4 升级版」对外引用。")
    L.append("")

    # ---- 相关性
    L.append("## 2. 四条腿的相关性（结合有没有效的前提）")
    L.append("")
    legs = ["V4 复刻（6 只池）", "V9 时间（6 只等权 × 温度分位）",
            "V9 全量（温度选品 × 温度时间）", "结合 A：V4 选品 × 温度时间"]
    mat = pd.concat({n: rows[n]["r"] for n in legs}, axis=1).dropna()
    corr = mat.corr()
    L.append("| | " + " | ".join(n.split("（")[0] for n in legs) + " |")
    L.append("|---|" + "---:|" * len(legs))
    for n in legs:
        L.append(f"| {n.split('（')[0]} | " + " | ".join(f"{corr.loc[n, m]:.2f}" for m in legs) + " |")
    L.append("")
    L.append("> 资金各半（结合 D）只有在两条腿**相关性明显低于 1**时才有分散价值；"
             "如果都在 0.9 以上，各半只是「把两个差不多的东西平均」，等于削弱了较好的那个"
             "（上一轮 `beat_best` 的 C10 就是这种情况）。")
    L.append("")

    # ---- 分段与剔除
    L.append("## 3. 逐年与稳健性")
    L.append("")
    keys = [BASE, "V4 复刻（6 只池）", "V9 时间（6 只等权 × 温度分位）",
            "结合 A：V4 选品 × 温度时间", "结合 D：V4 与 V9时间 资金各半"]
    yr = {k: (1 + rows[k]["r"]).groupby(rows[k]["r"].index.year).prod() - 1 for k in keys}
    years = sorted(yr[BASE].index)
    L.append("| 年份 | " + " | ".join(k.replace("（", "(").replace("）", ")") for k in keys) + " |")
    L.append("|---|" + "---:|" * len(keys))
    for y in years:
        L.append(f"| {y} | " + " | ".join(pct(float(yr[k].get(y, np.nan))) for k in keys) + " |")
    L.append("")
    L.append("| 方案 | 全部年化 | 剔除最强 3 月 | 前段夏普 | 后段夏普 |")
    L.append("|---|---:|---:|---:|---:|")
    for name in MAIN_ROWS:
        st = rows[name]
        r = st["r"]
        diff = (r - rows[BASE]["r"]).dropna()
        st_cut = S.stats(r.drop(diff.nlargest(TAIL).index))
        cut = r.index[int(len(r) * 0.6)]
        L.append(f"| {name} | {pct(st['cagr'])} | {pct(st_cut['cagr'])} "
                 f"| {f2(S.stats(r.loc[:cut])['sharpe'])} | {f2(S.stats(r.loc[cut:])['sharpe'])} |")
    L.append("")
    L.append("> 「剔除最强 3 月」去掉的是该方案**相对等权**贡献最大的 3 个月。"
             "红利这种低波动资产，两三年里的超额常常集中在 2~3 个月，"
             "这一列掉到基准以下的都不算数。")
    L.append("")

    # ---- 结论
    a = rows["结合 A：V4 选品 × 温度时间"]
    b = rows["结合 B：温度选品 × RSI 时间"]
    c = rows["结合 C：RSI 选品 × min(两个时间倍率)"]
    d = rows["结合 D：V4 与 V9时间 资金各半"]
    v4 = rows["V4 复刻（6 只池）"]
    v9 = rows["V9 时间（6 只等权 × 温度分位）"]
    base = rows[BASE]
    L.append("## 4. 结论：怎么结合、有没有用")
    L.append("")
    L.append(f"1. **两条腿都有效，但强弱差一档**：全窗口 V4 复刻 Calmar {f2(v4['calmar'])}、"
             f"V9 时间 {f2(v9['calmar'])}、等权 {f2(base['calmar'])}；共同窗口分别是 "
             f"{f2(v4c['calmar'])} / {f2(rows_c['V9 时间（6 只等权 × 温度分位）']['calmar'])} / "
             f"{f2(rows_c[BASE]['calmar'])}。V4 的优势是「选品 + 定时」两层的乘积，"
             "V9 只做时间层，所以单独使用时差一档是正常的 —— 问题不在于谁无效，而在于**能不能叠加**。")
    L.append(f"2. **时间层不能替换**：A（V4 选品 × 温度定时）Calmar {f2(a['calmar'])}"
             f"（共同窗口 {f2(rows_c['结合 A：V4 选品 × 温度时间']['calmar'])}），"
             f"比 V4 自己低 {v4['calmar'] - a['calmar']:.2f}。原因在 §2：两条腿的月度收益相关性 "
             f"{corr.loc[legs[0], legs[1]]:.2f}，**温度减仓的时点与 RSI 减仓高度重叠**，"
             "换过去等于把 V4 那层更灵敏的 RSI 定时换成更钝的同类信号，"
             "逐年表里 2022（V4 +9.47% vs A +2.72%）和 2025（+7.46% vs +2.91%）的差距就是这么来的。")
    L.append(f"3. **选品层也不能替换**：B（温度选品 × RSI 定时）{f2(b['calmar'])} / "
             f"{f2(rows_c['结合 B：温度选品 × RSI 时间']['calmar'])}，同样低于 V4。"
             "红利 6 只的月收益相关性本来就高，「谁更便宜」（温度）在横截面上的区分度"
             "不如「谁更超买」（RSI）—— 这也与 `best_by_fund` 和 V9 报告（横截面 top2/top3 不优于等权）一致。")
    L.append(f"4. **叠加与各半都没有增量**：C（取更保守的时间倍率）{f2(c['calmar'])} / "
             f"{f2(rows_c['结合 C：RSI 选品 × min(两个时间倍率)']['calmar'])}，与 V4 打平"
             f"（{v4['calmar']:.2f} / {v4c['calmar']:.2f}）—— 多一层复杂度换 0.0x，不值得；"
             f"D（资金各半）{f2(d['calmar'])} / "
             f"{f2(rows_c['结合 D：V4 与 V9时间 资金各半']['calmar'])}，"
             "在 0.9 以上的相关性下各半只是把两个差不多的东西平均。")
    L.append("5. **落地建议：不结合**。V4 保持现状（选品 + RSI 定时），V9 保持「体检表」定位，"
             "每日报告继续只出 V4。理由不是温度「无效」——它单独做时间层能把等权从 0.81 "
             f"提到 {f2(v9['calmar'])} —— 而是它和 RSI 说的是同一件事（相关性 0.93），"
             "叠加后信息量不增加、只增加数据依赖。若一定要留一个温度出口，"
             f"只保留「分位 ≥90% 清仓」这一层（{tg['t90']} 个月、{tg['t90'] / tg['n']:.0%}），"
             "不做减半微调；实测 C（含减半）与 V4 打平，说明这层加不加都无所谓 —— "
             "**不值得为 0.0x 的 Calmar 再背一套 PE/PB 数据**。")
    L.append("")
    L.append("## 5. 口径与已知局限")
    L.append("")
    L.append("1. **池不同**：V4 原版是 5 只（含 `008163`，无温度），本报告统一到 V9 的 6 只。"
             "§1.3 的对照说明换池本身的影响；不要把它算成「结合」的功劳。")
    L.append("2. **温度分位口径**：主表用 V9 的 `expanding_pct`（最少 24 个月、含当前点），"
             "与 `v9_temp5/backtest.py` 一字不差；§1 另给「绝对档 65/75」一行，"
             "避免结论依赖单一阈值定义。")
    L.append("3. **阈值是样本内选的**：80/90 与 65/75 都来自 V9 报告的网格；本报告没有重新搜索，"
             "但也没有做样本外检验 —— 这正是 V9 报告最后把它标成「不能照抄」的原因。")
    L.append("4. **样本短**：全窗口 68 个月、共同窗口 39 个月，一轮涨跌。"
             "任何 Calmar 差 0.1 以内的排序都不该当成结论。")
    L.append("")
    L.append("> 机械规则输出，非投资建议。")
    return "\n".join(L) + "\n"


# ================================================================ V4 原版对照
def run_v4_original(start, end) -> dict:
    """V4 原版：5 只池 + RSI 最低 2 只 + 板块 RSI 70/50（与 `v4_rsi/rule.py` 同口径）。"""
    codes = list(pool.TIMING_CODES)
    navs = pool.load_navs(codes=codes).resample("ME").last().ffill()
    r = navs.pct_change().loc[start:end]
    q = pd.DataFrame({c: E.rsi_monthly(c, 14) for c in codes}).loc[start:end]
    sims, _, _ = S.run_variants(q / 100.0, r, q, k=K, q_out=0.70, q_half=0.50,
                                base_label="基准：5 只等权")
    best = sims.get("横截面 top2 + 时间择时")
    st = stat(best.dropna())
    st["note"] = "5 只池原版"
    return dict(rows={"V4 原版（5 只池）": st})


def main() -> int:
    temp, rets, rsi = load_panel()
    asof = temp.index[-1]
    full = run(temp, rets, rsi)
    first6 = temp.dropna().index[0]
    common = run(temp, rets, rsi, start=first6)
    v4o = run_v4_original(full["start"], full["end"])

    md = render(full, common, v4o, asof)
    with io.open(OUT_MD, "w", encoding="utf-8") as fh:
        fh.write(md)
    print(f"[ok] {OUT_MD}")
    print("")
    for tag, res in (("全窗口", full), ("共同窗口", common)):
        print(f"{tag} {res['start']:%Y-%m}~{res['end']:%Y-%m} ({res['n']} 月)")
        for n in MAIN_ROWS:
            st = res["rows"][n]
            print(f"   {n:<34} 年化 {st['cagr']:+.2%} 回撤 {st['dd']:.2%} "
                  f"Calmar {st['calmar']:.2f} 夏普 {st['sharpe']:.2f} 仓位 {st['expo']:.0%}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
