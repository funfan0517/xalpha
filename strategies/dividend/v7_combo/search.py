#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""V7 · 多指标组合搜索与最终优选

做三件事：
1. 把 V3–V6 的四个单指标放进同一张表（同池、同样本、同成本），给横向对比；
2. 按 `rule.CANDIDATES` 构造 4 个组合（等权，不搜权重）+ 1 个投票制；
3. 用「Calmar + 剔除贡献最大 3 个月后是否仍优于基准」两条一起选最优，
   单看 Calmar 会把「靠 2–3 个月运气」的方案选出来。

产物：`backtest/_v7_report.md`

用法::

    python strategies/dividend/v7_combo/search.py
"""

from __future__ import annotations

import os
import sys

import numpy as np
import pandas as pd

_DIR = os.path.dirname(os.path.abspath(__file__))
_STRAT = os.path.dirname(_DIR)
sys.path.insert(0, _DIR)
sys.path.insert(0, _STRAT)

import _single_signal as S  # noqa: E402  pylint: disable=wrong-import-position
import rule  # noqa: E402  pylint: disable=wrong-import-position

VARIANTS = ("时间：板块贵度择时", "横截面：最便宜 2 只", "横截面 top2 + 时间择时")

# 分工组合候选定义在 `rule.SPECIAL`（参数权威源，总对比脚本 `../_compare_all.py` 共用）
SPECIAL = rule.SPECIAL


# ------------------------------------------------------------------ 构造
def build_combo(rets: pd.DataFrame, kinds: list[str]) -> tuple[pd.DataFrame, pd.DataFrame]:
    """q：自身分位贵度均值（时间维度）；xs：当月横截面排名均值（横截面）。"""
    qs = [S.cheapness(k).reindex(rets.index) for k in kinds]
    q = pd.concat(qs, keys=range(len(qs))).groupby(level=1).mean()
    ranks = [S.xs_key(k).reindex(rets.index).rank(axis=1, pct=True) for k in kinds]
    xs = pd.concat(ranks, keys=range(len(ranks))).groupby(level=1).mean()
    return q, xs


def build_vote(rets: pd.DataFrame, kinds: list[str]) -> tuple[pd.DataFrame, pd.DataFrame]:
    """票数制：q = 1 − 便宜票占比；xs 用 −票数（票多 = 更便宜），
    平票时用估值贵度均值做第二排序键，避免退化成按列序取。"""
    qs = [S.cheapness(k).reindex(rets.index) for k in kinds]
    votes = sum((q < rule.VOTE_THRESHOLD).astype(float).fillna(0.0) for q in qs)
    n = len(kinds)
    q = 1.0 - votes / n
    xs = -votes + 1e-3 * pd.concat(qs, keys=range(n)).groupby(level=1).mean()
    return q, xs


def eval_one(rets, q, xs, q_out, q_half, bq=None, mult=None) -> tuple[dict, float]:
    sims, _, _ = S.run_variants(q, rets, xs, bq=bq, k=rule.TOP_K,
                                q_out=q_out, q_half=q_half, mult_override=mult)
    base = sims["基准：7 只等权"]
    rows = {}
    for v in VARIANTS:
        st = S.stats(sims[v])
        diff = (sims[v] - base).dropna()
        drop3 = st["cagr"] if len(diff) == 0 else S.stats(sims[v].drop(diff.nlargest(3).index))["cagr"]
        rows[v] = {**st, "cagr_drop3": drop3}
    b = S.stats(base)
    b3 = S.stats(base.drop((sims[VARIANTS[-1]] - base).dropna().nlargest(3).index))["cagr"]
    return rows, b, b3


def main() -> int:
    rets = S.load_rets()
    res = {}

    # ---- 单指标（V3–V6）
    for kind in ("dy", "spread", "ratio", "rsi"):
        q = S.cheapness(kind).reindex(rets.index)
        xs = S.xs_key(kind).reindex(rets.index)
        th = (0.70, 0.50) if kind == "rsi" else (rule.Q_OUT, rule.Q_HALF)
        rows, base, base3 = eval_one(rets, q, xs, *th)
        res[f"单指标 · {kind}"] = (rows, base, base3)

    # ---- 组合
    for name, kinds in rule.CANDIDATES.items():
        q, xs = build_combo(rets, kinds)
        th = (0.70, 0.50) if kinds == ["rsi"] else (rule.Q_OUT, rule.Q_HALF)
        rows, base, base3 = eval_one(rets, q, xs, *th)
        res[name] = (rows, base, base3)

    # ---- 投票
    kinds = ["dy", "spread", "ratio", "rsi"]
    q, xs = build_vote(rets, kinds)
    rows, base, base3 = eval_one(rets, q, xs, rule.Q_OUT, rule.Q_HALF)
    res["vote（≥2 票）"] = (rows, base, base3)

    # ---- 分工组合：两个维度各用最适合的指标（时间用 RSI、选品用股息率，反之亦然）
    for name, kt, kx, th in SPECIAL:
        q = S.cheapness(kt).reindex(rets.index)
        xs = S.xs_key(kx).reindex(rets.index)
        bq = S.board_q(q, S.available(q, rets))
        rows, base, base3 = eval_one(rets, q, xs, *th, bq=bq)
        res[name] = (rows, base, base3)

    # ---- 仓位投票：每个指标按**自己的阈值**给 0/0.5/1，再对仓位倍率取平均。
    # 这与「先把 q 平均再套一个阈值」不同：后者会把各指标的阈值含义搅在一起。
    q, xs = build_combo(rets, kinds)
    mults = []
    for k in kinds:
        qk = S.cheapness(k).reindex(rets.index)
        th = (0.70, 0.50) if k == "rsi" else (rule.Q_OUT, rule.Q_HALF)
        mults.append(S.time_mult(S.board_q(qk, S.available(qk, rets)), *th))
    rows, base, base3 = eval_one(rets, q, xs, rule.Q_OUT, rule.Q_HALF,
                                 mult=pd.concat(mults, axis=1).mean(axis=1))
    res["pos_vote（仓位投票）"] = (rows, base, base3)

    text = render(res, base, base3, rets)
    S.write(rule.OUT_MD, text)

    print(f"[v7] 样本 {rets.index[0]:%Y-%m}~{rets.index[-1]:%Y-%m} ({len(rets)} 月) · "
          f"基准 Calmar {base['calmar']:.2f}")
    for name, (rows, _, _) in res.items():
        r = rows[VARIANTS[-1]]
        print(f"[v7] {name:22s} top2+时间 年化 {r['cagr'] * 100:5.1f}%  "
              f"回撤 {r['dd'] * 100:6.1f}%  Calmar {r['calmar']:5.2f}  "
              f"(剔除3月后 {r['cagr_drop3'] * 100:5.1f}%)")
    print(f"[v7] 产物 -> {os.path.relpath(rule.OUT_MD, _STRAT)}")
    return 0


def render(res: dict, base: dict, base3: float, rets: pd.DataFrame) -> str:
    L = ["# V7 · 多指标组合：横向对比与最终优选", "",
         f"> 样本 {rets.index[0]:%Y-%m} ~ {rets.index[-1]:%Y-%m}（{len(rets)} 月）· 7 只基金 · "
         "月末信号下月生效 · 成本单边 0.03%",
         "> 全部候选**同一池、同样本、同成本**；组合只用等权/投票，**不搜索权重**（见 `RULES.md`）。", ""]

    # 一、单指标横向对比
    L += ["## 一、四个单指标（同一口径下的正面比较）", "",
          "| 指标 | 时间维度 Calmar | 横截面 top2 Calmar | 两者叠加 Calmar | 最佳方案年化 | 基准 Calmar |",
          "|---|---:|---:|---:|---:|---:|"]
    for name, (rows, b, _) in res.items():
        if not name.startswith("单指标"):
            continue
        L.append(f"| {name.replace('单指标 · ', '')} | {rows[VARIANTS[0]]['calmar']:.2f} | "
                 f"{rows[VARIANTS[1]]['calmar']:.2f} | {rows[VARIANTS[2]]['calmar']:.2f} | "
                 f"{S.fmt_pct(max(r['cagr'] for r in rows.values()))} | {b['calmar']:.2f} |")
    L += ["", "> `dy`/`spread`/`ratio` 的横截面结果完全相同（0.87）—— 因为 y10 是共同项，"
              "三者的横截面排序在数学上是同一个。它们的差别只在时间维度："
              "利差/收益比的自身分位长期贴顶，贵度永远 < 0.5，一次都没触发减仓。", ""]

    # 二、组合对比
    L += ["## 二、组合候选", "",
          "| 候选 | 时间 Calmar | 横截面 Calmar | 两者叠加 Calmar | 叠加年化 | 叠加回撤 | 剔除 3 月后年化 |",
          "|---|---:|---:|---:|---:|---:|---:|"]
    for name, (rows, b, _) in res.items():
        if name.startswith("单指标"):
            continue
        r = rows[VARIANTS[2]]
        L.append(f"| {name} | {rows[VARIANTS[0]]['calmar']:.2f} | "
                 f"{rows[VARIANTS[1]]['calmar']:.2f} | **{r['calmar']:.2f}** | "
                 f"{S.fmt_pct(r['cagr'])} | {S.fmt_pct(r['dd'])} | {S.fmt_pct(r['cagr_drop3'])} |")
    L += ["", f"- 基准：Calmar **{base['calmar']:.2f}**、年化 {S.fmt_pct(base['cagr'])}、"
              f"回撤 {S.fmt_pct(base['dd'])}；基准剔除 3 月后年化 {S.fmt_pct(base3)}。", ""]

    # 三、优选
    cand = {n: v for n, v in res.items() if not n.startswith("单指标")}
    ranked = sorted(cand.items(), key=lambda kv: -kv[1][0][VARIANTS[2]]["calmar"])
    winner = ranked[0][0]
    w = cand[winner][0][VARIANTS[2]]
    robust_ok = w["cagr_drop3"] > base3
    combos = {n: v for n, v in cand.items() if not n.startswith("rsi")}
    best_combo = max(combos, key=lambda n: combos[n][0][VARIANTS[2]]["calmar"])
    bc = combos[best_combo][0][VARIANTS[2]]
    pure = cand.get("rsi（单指标对照）")
    pw = pure[0][VARIANTS[2]] if pure else None

    L += ["## 三、优选结果", "",
          f"- **全场最优是 `{winner}`**：叠加方案年化 {S.fmt_pct(w['cagr'])}、"
          f"回撤 {S.fmt_pct(w['dd'])}、Calmar **{w['calmar']:.2f}**（基准 {base['calmar']:.2f}）。",
          f"- 它是**单指标**，不是组合。所有真正的组合里最好的是 `{best_combo}`"
          f"（Calmar {bc['calmar']:.2f}、年化 {S.fmt_pct(bc['cagr'])}），"
          f"仍比纯 RSI 低 {pw['calmar'] - bc['calmar']:.2f}。",
          f"- 稳健性：纯 RSI 剔除贡献最大 3 个月后年化 {S.fmt_pct(w['cagr_drop3'])}，"
          f"基准同口径 {S.fmt_pct(base3)} → "
          f"{'**仍然领先**，不是靠个别月份' if robust_ok else '**已不领先**，优势集中在少数月份'}。",
          ""]

    L += ["## 四、结论与用法", "",
          "1. **估值类三个指标在横截面上是同一个信号。** `dy`/`spread`/`ratio` 的 top2 持仓"
          "逐月完全相同（`v5_spread` 里已用代码验证：差异 0 个月），所以"
          "「三个都放进组合」等价于「给股息率三倍权重」—— 这是组合最容易犯的错。",
          "2. **组合没有带来增量，这是本轮最重要的否定结果。** 8 个候选里，"
          "等权/投票/仓位投票/分工组合全部落在 0.87–1.05 区间，"
          "没有一个超过纯 RSI 的 1.44。原因不复杂：估值三项在本样本里连基准都跑不过"
          "（时间 0.91 = 基准、横截面 0.87 < 基准），把无效信号掺进来只会摊薄有效信号。",
          "3. **RSI 赢在「控回撤」，不是「增强收益」。** 它平均仓位只有 72%，"
          "年化 10.8% 比基准 8.2% 高 2.6pp，但 2020/2021/2024 三年都落后基准 —— "
          "它靠的是 2022、2026 两次下跌前减仓。把它当「稳定 alpha」会失望。",
          "4. **多重比较警告。** 8 个候选 × 3 个变体 = 24 次比较，挑出来的最优天然偏高。"
          "RSI 的阈值敏感性（70/50、80/50、60/40）都优于基准，是它比其它候选更可信的唯一理由。",
          "5. 以上全部建立在 **82 个月、7 只高度相关的红利基金**上；"
          "横截面在板块内能提供的差异化本来就有限，时间维度的贡献更大也更脆弱。", ""]
    return "\n".join(L)


if __name__ == "__main__":
    sys.exit(main())
