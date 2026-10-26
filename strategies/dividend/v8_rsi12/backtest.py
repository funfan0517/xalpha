#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""V8 · RSI12 —— 回测 + 与 V4 的对照 + 窗口/阈值敏感性

本脚本回答三件事：

1. **V8 自己跑多少**（12 日 + 80/50，与 V4 同池同横截面）
2. **和 V4 差在哪**（14/70/50 vs 12/80/50，只差窗口与清仓线）
3. **「80/20」若把 20 当减半线会怎样**（`Q_HALF_LITERAL`，说明为什么这条线不进规则）

用法::

    python strategies/dividend/v8_rsi12/backtest.py
"""

from __future__ import annotations

import os
import sys

import pandas as pd

_DIR = os.path.dirname(os.path.abspath(__file__))
_STRAT = os.path.dirname(_DIR)
sys.path.insert(0, _DIR)
sys.path.insert(0, _STRAT)

import _single_signal as S  # noqa: E402  pylint: disable=wrong-import-position
import pool  # noqa: E402  pylint: disable=wrong-import-position
import rule  # noqa: E402  pylint: disable=wrong-import-position

# (窗口, 清仓线, 减半线) —— 第一行是 V8 本体，第二行是 V4，第三行是 80/20 的字面落地
FAMILY = (
    (rule.WINDOW, rule.Q_OUT, rule.Q_HALF),
    (14, 0.70, 0.50),
    (rule.WINDOW, rule.Q_OUT, rule.Q_HALF_LITERAL),
    (rule.WINDOW, 0.70, 0.50),
    (rule.WINDOW, 0.90, 0.50),
    (11, rule.Q_OUT, 0.50),
    (13, rule.Q_OUT, 0.50),
)
TAG = {FAMILY[0]: "**V8 本体：12 日 + 80/50**",
       FAMILY[1]: "V4 对照：14 日 + 70/50",
       FAMILY[2]: "80/20 字面：20 当减半线",
       FAMILY[3]: "12 日 + 70/50",
       FAMILY[4]: "12 日 + 90/50",
       FAMILY[5]: "11 日 + 80/50",
       FAMILY[6]: "13 日 + 80/50"}


def panel(navs: pd.DataFrame, rets: pd.DataFrame, window: int) -> tuple:
    """(贵度 q, 横截面键 xs, 原始 RSI) —— 指定窗口的月末 RSI 宽表。"""
    rsi = pd.DataFrame({c: S.rsi_series(navs[c].dropna(), window)
                        .resample("ME").last().dropna()
                        for c in navs.columns}).sort_index()
    raw = rsi.reindex(rets.index)
    return (raw / 100.0).loc[S.START:], raw.loc[S.START:], raw


def run(navs: pd.DataFrame, rets: pd.DataFrame, window: int,
        q_out: float, q_half: float) -> tuple:
    q, xs, raw = panel(navs, rets, window)
    sims, _, mult = S.run_variants(q, rets, xs, k=rule.TOP_K, q_out=q_out,
                                   q_half=q_half, base_label=rule.BASE_LABEL)
    return sims, q, xs, raw, mult


def _fam_row(navs: pd.DataFrame, rets: pd.DataFrame, window: int, q_out: float,
             q_half: float, base: dict) -> str:
    sims, q, xs, _, mult = run(navs, rets, window, q_out, q_half)
    key = f"横截面 top{rule.TOP_K} + 时间择时"
    st = S.stats(sims[key])
    ok = S.available(q, rets)
    pos = float(S.top_w(xs, ok, rule.TOP_K).mul(mult, axis=0).sum(axis=1).mean())
    switch = int((mult.diff().abs() > 1e-9).sum())
    name = TAG.get((window, q_out, q_half), f"{window} 日 + {q_out * 100:.0f}/{q_half * 100:.0f}")
    return (f"| {name} | {window} | {q_out * 100:.0f} / {q_half * 100:.0f} | "
            f"{S.fmt_pct(st['cagr'])} | {S.fmt_pct(st['dd'])} | {st['calmar']:.2f} | "
            f"{pos:.0%} | {switch} | {st['calmar'] - base['calmar']:+.2f} |")


def main() -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")

    navs = pool.load_navs(codes=rule.CODES)
    rets = S.load_rets(navs)
    sims, q, xs, raw, mult = run(navs, rets, rule.WINDOW, rule.Q_OUT, rule.Q_HALF)

    text = S.render_report(rule.TITLE, rule.SUBTITLE, q, raw, sims,
                           {"unit_fmt": rule.unit_fmt}, rets=rets, xs=xs,
                           q_out=rule.Q_OUT, q_half=rule.Q_HALF,
                           codes=rule.CODES, name=rule.NAME)
    text += "\n" + "\n".join(S.conclusion(sims, rule.NOTE)) + "\n"

    base = S.stats(sims[S.base_key(sims)])
    key = f"横截面 top{rule.TOP_K} + 时间择时"

    # 附一：参数族（窗口 / 清仓线 / 减半线）
    fam = ["## 附一：参数族对照（同池、同横截面、同成本）", "",
           "| 方案 | 窗口 | 清仓/减半 | 年化 | 最大回撤 | Calmar | 平均仓位 | 倍率变动 | ΔCalmar vs 基准 |",
           "|---|---:|---:|---:|---:|---:|---:|---:|---:|"]
    fam += [_fam_row(navs, rets, w, qo, qh, base) for w, qo, qh in FAMILY]
    fam += ["",
            f"- 基准（{len(rule.CODES)} 只等权）：年化 {S.fmt_pct(base['cagr'])}、"
            f"回撤 {S.fmt_pct(base['dd'])}、Calmar {base['calmar']:.2f}。",
            "- 「80/20 字面」= 把 20 当减半线，等于 RSI 一过 20 就半仓（几乎常驻半仓），"
            "它的存在是为了量化「照字面用 20」的代价，不是可选方案。",
            "- 换手成本：倍率变动次数越多，Σ|Δw| 越大；12 日比 14 日更敏感，本版的成本天然更高。", ""]

    # 附二：窗口 × 清仓线（只看主方案 Calmar）
    wins = (10, 11, 12, 13, 14, 16)
    outs = (0.70, 0.75, 0.80, 0.85, 0.90)
    grid = ["## 附二：窗口 × 清仓线（主方案 Calmar，减半线固定 50）", "",
            "| 窗口 \\ 清仓线 | " + " | ".join(f"RSI {o * 100:.0f}" for o in outs) + " |",
            "|---|" + "---:|" * len(outs)]
    cells = {}
    for w in wins:
        for o in outs:
            sm = run(navs, rets, w, o, rule.Q_HALF)[0]
            cells[(w, o)] = S.stats(sm[f"横截面 top{rule.TOP_K} + 时间择时"])["calmar"]
    for w in wins:
        row = [f"{cells[(w, o)]:.2f}" for o in outs]
        best = max(range(len(outs)), key=lambda i: cells[(w, outs[i])])
        row[best] = f"**{row[best]}**"
        grid.append(f"| {w} 日 | " + " | ".join(row) + " |")
    grid += ["", f"- 基准 Calmar {base['calmar']:.2f}；粗体 = 该窗口内最优清仓线。",
             "- 读法：若整行/整列差异都在 ±0.1 内，说明参数不敏感、结论可外推；"
             "若最优点孤立，则不要按最优点下结论。", ""]

    # 附三：窗口变化到底动了哪一层（时间维度 / 横截面 / 合成）
    dec = ["## 附三：窗口的敏感性来自哪一层（清仓线 80、减半线 50）", "",
           "| 窗口 | 时间：板块择时 | 横截面：最便宜 2 只 | 主方案 | 主方案平均仓位 |",
           "|---|---:|---:|---:|---:|"]
    for w in wins:
        sm, qq, xss, _, mt = run(navs, rets, w, rule.Q_OUT, rule.Q_HALF)
        okw = S.available(qq, rets)
        posw = float(S.top_w(xss, okw, rule.TOP_K).mul(mt, axis=0).sum(axis=1).mean())
        dec.append(f"| {w} 日 | {S.stats(sm['时间：板块贵度择时'])['calmar']:.2f} | "
                   f"{S.stats(sm[f'横截面：最便宜 {rule.TOP_K} 只'])['calmar']:.2f} | "
                   f"**{S.stats(sm[key])['calmar']:.2f}** | {posw:.0%} |")
    dec += ["", "- 若两列（时间/横截面）都平滑、只有主方案跳变，说明是**两层叠加后的换股时点**变了，"
            "不是某个参数本身更优；这种跳变不可外推。", ""]

    text += "\n" + "\n".join(fam) + "\n" + "\n".join(grid) + "\n" + "\n".join(dec) + "\n"
    S.write(rule.OUT_MD, text)

    print(f"[v8] 样本 {rets.index[0]:%Y-%m}~{rets.index[-1]:%Y-%m} ({len(rets)} 月) · "
          f"池 {len(rule.CODES)} 只 · {rule.WINDOW} 日 + "
          f"{rule.Q_OUT * 100:.0f}/{rule.Q_HALF * 100:.0f}")
    for name, r in sims.items():
        st = S.stats(r)
        print(f"[v8] {name:24s} 年化 {st['cagr'] * 100:5.1f}%  回撤 {st['dd'] * 100:6.1f}%  "
              f"Calmar {st['calmar']:5.2f}")
    v4_calmar = S.stats(run(navs, rets, 14, 0.70, 0.50)[0][key])["calmar"]
    print(f"[v8] 对照 V4（14 日 + 70/50）Calmar {v4_calmar:.2f} · "
          f"产物 -> {os.path.relpath(rule.OUT_MD, _STRAT)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
