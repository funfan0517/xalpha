#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""V4 · RSI14 单指标红利策略 —— 回测与报告生成（含阈值敏感性）

标的池自 2026-11-20 起是**全池 10 只**（`rule.POOL_MODE = "all10"`；此前为按单标的
ΔCalmar 筛出的 5 只，`"sel5"` 可一键回退）。不是 V3–V7 共用的 7 只 ——
因此这里不能直接用 `S.cheapness/S.xs_key`（它们绑 S.CODES）。

报告除主表外另有两节：附一 5 只筛池 vs 10 只全池的同期对照、附二 逐只最优策略
（RSI 择时 vs 买入持有，计算在 `fit.py`）。

用法::

    python strategies/dividend/v4_rsi/backtest.py
"""

from __future__ import annotations

import os
import sys

_DIR = os.path.dirname(os.path.abspath(__file__))
_STRAT = os.path.dirname(_DIR)
sys.path.insert(0, _DIR)
sys.path.insert(0, _STRAT)

import _single_signal as S  # noqa: E402  pylint: disable=wrong-import-position
import fit  # noqa: E402  pylint: disable=wrong-import-position
import pool  # noqa: E402  pylint: disable=wrong-import-position
import rule  # noqa: E402  pylint: disable=wrong-import-position

GRID = ((0.70, 0.50), (0.80, 0.50), (0.60, 0.40))   # (清仓线, 减半线) = RSI 70/80/60 与 50/50/40
OOS_START = "2025-01-01"                           # 池子口径变更后的「检验期」起点


def load() -> tuple:
    """V4 池的 (贵度 q, 横截面键 xs, 原始 RSI, 月度收益)。q = RSI/100，不做分位化。"""
    navs = pool.load_navs(codes=rule.CODES)
    rets = S.load_rets(navs)
    raw = S.load_signal(rule.SIGNAL, navs).reindex(rets.index)
    return (raw / 100.0).loc[S.START:], raw.loc[S.START:], raw, rets


def _run(codes: list[str]) -> dict:
    """给定池，跑「横截面 topK + 时间择时」与等权基准，返回全样本与 2025+ 两段绩效。"""
    navs = pool.load_navs(codes=codes)
    rets = S.load_rets(navs)
    raw = S.load_signal(rule.SIGNAL, navs).reindex(rets.index)
    q, xs = (raw / 100.0).loc[S.START:], raw.loc[S.START:]
    sims, _, _ = S.run_variants(q, rets, xs, k=rule.TOP_K, q_out=rule.Q_OUT,
                                q_half=rule.Q_HALF, base_label=f"基准：{len(codes)} 只等权")
    best = sims[f"横截面 top{rule.TOP_K} + 时间择时"]
    base = sims[S.base_key(sims)]
    out = {"n": len(codes), "codes": list(codes)}
    for tag, r in (("all", best), ("all_base", base),
                   ("oos", best.loc[OOS_START:]), ("oos_base", base.loc[OOS_START:])):
        st = S.stats(r.dropna())
        out[tag] = (st["cagr"], st["dd"], st["calmar"])
    out["n_oos"] = len(best.loc[OOS_START:].dropna())
    return out


def pool_compare() -> list[str]:
    """5 只筛池 vs 全 10 只池 —— 同一套规则、同一段样本。"""
    a = _run(list(pool.TIMING_CODES))
    b = _run(list(pool.TIMING_CODES_ALL))
    L = ["## 附一：筛选池（5 只） vs 全池（10 只）", "",
         f"同一套规则（横截面 top{rule.TOP_K} + 板块 RSI {rule.Q_OUT * 100:.0f}/"
         f"{rule.Q_HALF * 100:.0f}），只差池子。2026-11-20 起实盘口径取 **全 {b['n']} 只**。",
         "",
         "| 池 | 只数 | 全样本 年化 | 回撤 | Calmar | "
         f"{OOS_START[:4]} 起 年化 | 回撤 | Calmar |",
         "|---|---:|---:|---:|---:|---:|---:|---:|"]
    for tag, r in (("筛选池（ΔCalmar 双窗口 ≥0.10，2026-10-26~11-20 用）", a),
                   (f"全池（2026-11-20 起用，{b['n']} 只）", b)):
        L.append(f"| {tag} | {r['n']} | "
                 f"{S.fmt_pct(r['all'][0])} | {S.fmt_pct(r['all'][1])} | {r['all'][2]:.2f} | "
                 f"{S.fmt_pct(r['oos'][0])} | {S.fmt_pct(r['oos'][1])} | {r['oos'][2]:.2f} |")
        L.append(f"| └ 同池等权基准 | {r['n']} | "
                 f"{S.fmt_pct(r['all_base'][0])} | {S.fmt_pct(r['all_base'][1])} | "
                 f"{r['all_base'][2]:.2f} | {S.fmt_pct(r['oos_base'][0])} | "
                 f"{S.fmt_pct(r['oos_base'][1])} | {r['oos_base'][2]:.2f} |")
    L += ["",
          f"- 「{OOS_START[:4]} 起」= {b['n_oos']} 个月：筛选池是在**含这段的全样本**上挑的，"
          "所以这段对筛选池不是干净的样本外（见 `research/_oos_pool.md`）。",
          f"- 换池不是「升级」：多出的 5 只里有 3 只（`007467`/`561580`/`018388`）"
          "被 RSI 择时是负贡献，但全池的**等权基准**更分散、且不再有样本内挑选。",
          "- 判据取舍：筛选池胜在「每只都吃得住择时」，全池胜在「不含选择偏差 + 不漏掉"
          "持有 Calmar 最高的 007467」。两者差异不大时优先可复现的那个。", ""]
    return L


def fit_table() -> list[str]:
    """逐只最优策略：RSI 择时 vs 买入持有。"""
    rows = fit.fit_all()
    L = ["## 附二：逐只最优策略（RSI 择时 vs 买入持有）", "",
         "单标的跑同一套 `RSI14 ≥70 清仓 / ≥50 减半`，与买入持有比 ΔCalmar。"
         f"ΔCalmar ≥ +{fit.BEST_EPS:.2f} → 最优 = **RSI 择时**；否则 → 最优 = **买入持有**。",
         "计算在 `fit.py`（唯一实现），每日报告与看板读同一份结果。", "",
         "| 代码 | 名称 | 月数 | 持有 年化 | 持有 回撤 | 持有 Calmar | "
         "择时 年化 | 择时 回撤 | 择时 Calmar | ΔCalmar | 最优策略 |",
         "|---|---|---:|---:|---:|---:|---:|---:|---:|---:|---|"]
    for r in rows:
        L.append(f"| {r['code']} | {r['name']} | {r['months']} | "
                 f"{S.fmt_pct(r['cagr_hold'])} | {S.fmt_pct(r['dd_hold'])} | {r['calmar_hold']:.2f} | "
                 f"{S.fmt_pct(r['cagr_rsi'])} | {S.fmt_pct(r['dd_rsi'])} | {r['calmar_rsi']:.2f} | "
                 f"{r['dcalmar']:+.2f} | {'RSI 择时' if r['best'] == 'rsi' else '买入持有'} |")
    hold = [r for r in rows if r["best"] == "hold"]
    L += ["",
          f"- **{len(rows) - len(hold)} 只最优是 RSI 择时、{len(hold)} 只最优是买入持有**"
          f"（{'、'.join(r['code'] for r in hold) or '无'}）。",
          "- 标「买入持有」的标的被 RSI 减仓是负和：它们减半的月份本来也在涨，"
          "一半仓位只换来手续费与少赚。V4 仍会按横截面规则买它们（因为横截面看的是"
          "**相对超卖**，不是择时适配度），但不必指望它们贡献择时收益。", ""]
    return L


def main() -> int:
    q, xs, raw, rets = load()
    sims, _, _ = S.run_variants(q, rets, xs, k=rule.TOP_K, q_out=rule.Q_OUT,
                                q_half=rule.Q_HALF, base_label=rule.BASE_LABEL)

    text = S.render_report(rule.TITLE, rule.SUBTITLE, q, raw, sims,
                           {"unit_fmt": rule.unit_fmt}, rets=rets, xs=xs,
                           q_out=rule.Q_OUT, q_half=rule.Q_HALF,
                           codes=rule.CODES, name=rule.NAME)

    # 阈值敏感性：文献里的 70/30 与常见 80/20 都跑一遍，看结论是否依赖单点参数
    sens = ["## 附：阈值敏感性（时间维度）", "",
            "| 清仓线 / 减半线 | 年化 | 最大回撤 | Calmar | 减半月数 | 清仓月数 |",
            "|---|---:|---:|---:|---:|---:|"]
    for qo, qh in GRID:
        sm, _, mt = S.run_variants(q, rets, xs, k=rule.TOP_K, q_out=qo, q_half=qh,
                                   base_label=rule.BASE_LABEL)
        r = sm["时间：板块贵度择时"]
        st = S.stats(r)
        sens.append(f"| RSI {qo * 100:.0f} / {qh * 100:.0f} | {S.fmt_pct(st['cagr'])} | "
                    f"{S.fmt_pct(st['dd'])} | {st['calmar']:.2f} | "
                    f"{int(((mt > 0.01) & (mt < 0.99)).sum())} | {int((mt <= 0.01).sum())} |")
    sens += ["", f"- 基准同口径 Calmar {S.stats(sims[S.base_key(sims)])['calmar']:.2f}。", ""]

    text += "\n" + "\n".join(S.conclusion(sims, rule.NOTE)) + "\n"
    text += "\n" + "\n".join(sens) + "\n"
    text += "\n" + "\n".join(pool_compare()) + "\n"
    text += "\n" + "\n".join(fit_table()) + "\n"
    S.write(rule.OUT_MD, text)

    base = S.stats(sims[S.base_key(sims)])
    print(f"[v4] 样本 {rets.index[0]:%Y-%m}~{rets.index[-1]:%Y-%m} ({len(rets)} 月)")
    for name, r in sims.items():
        st = S.stats(r)
        print(f"[v4] {name:24s} 年化 {st['cagr'] * 100:5.1f}%  回撤 {st['dd'] * 100:6.1f}%  "
              f"Calmar {st['calmar']:5.2f}")
    print(f"[v4] 基准 Calmar {base['calmar']:.2f} · 产物 -> "
          f"{os.path.relpath(rule.OUT_MD, _STRAT)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
