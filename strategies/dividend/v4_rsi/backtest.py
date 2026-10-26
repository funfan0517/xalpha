#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""V4 · RSI14 单指标红利策略 —— 回测与报告生成（含阈值敏感性）

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
import rule  # noqa: E402  pylint: disable=wrong-import-position

GRID = ((0.70, 0.50), (0.80, 0.50), (0.60, 0.40))   # (清仓线, 减半线) = RSI 70/80/60 与 50/50/40


def main() -> int:
    rets = S.load_rets()
    q = S.cheapness(rule.SIGNAL).reindex(rets.index)
    xs = S.xs_key(rule.SIGNAL).reindex(rets.index)
    raw = S.load_signal(rule.SIGNAL).reindex(q.index)
    sims, _, _ = S.run_variants(q, rets, xs, k=rule.TOP_K,
                                q_out=rule.Q_OUT, q_half=rule.Q_HALF)

    text = S.render_report(rule.TITLE, rule.SUBTITLE, q, raw, sims,
                           {"unit_fmt": rule.unit_fmt}, rets=rets, xs=xs,
                           q_out=rule.Q_OUT, q_half=rule.Q_HALF)

    # 阈值敏感性：文献里的 70/30 与常见 80/20 都跑一遍，看结论是否依赖单点参数
    sens = ["## 附：阈值敏感性（时间维度）", "",
            "| 清仓线 / 减半线 | 年化 | 最大回撤 | Calmar | 减半月数 | 清仓月数 |",
            "|---|---:|---:|---:|---:|---:|"]
    for qo, qh in GRID:
        sm, _, mt = S.run_variants(q, rets, xs, k=rule.TOP_K, q_out=qo, q_half=qh)
        r = sm["时间：板块贵度择时"]
        st = S.stats(r)
        sens.append(f"| RSI {qo * 100:.0f} / {qh * 100:.0f} | {S.fmt_pct(st['cagr'])} | "
                    f"{S.fmt_pct(st['dd'])} | {st['calmar']:.2f} | "
                    f"{int(((mt > 0.01) & (mt < 0.99)).sum())} | {int((mt <= 0.01).sum())} |")
    sens += ["", f"- 基准同口径 Calmar {S.stats(sims['基准：7 只等权'])['calmar']:.2f}。", ""]

    text += "\n" + "\n".join(S.conclusion(sims, rule.NOTE)) + "\n"
    text += "\n" + "\n".join(sens) + "\n"
    S.write(rule.OUT_MD, text)

    base = S.stats(sims["基准：7 只等权"])
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
