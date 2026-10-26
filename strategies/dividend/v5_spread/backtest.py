#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""V5 · 股债利差单指标红利策略 —— 回测与报告生成

用法::

    python strategies/dividend/v5_spread/backtest.py
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
    text += "\n" + "\n".join(S.conclusion(sims, rule.NOTE)) + "\n"

    # 与 V3 的等价性：同一套横截面规则，选出的组合逐月比较
    xs_dy = S.xs_key("dy").reindex(rets.index)
    w_sp = S.top_w(xs, S.available(q, rets), rule.TOP_K)
    w_dy = S.top_w(xs_dy, S.available(q, rets), rule.TOP_K)
    diff_months = int((w_sp - w_dy).abs().sum(axis=1).gt(1e-9).sum())
    text += ("\n## 附：与 V3（股息率）的等价性检验\n\n"
             f"- 同一套横截面规则下，V5 与 V3 的持仓在 **{len(w_sp)} 个月里有 "
             f"{diff_months} 个月不同**。\n"
             "- 理论上是 0 个月：`利差 = dy − y10`，y10 对所有标的是同一个数，"
             "排序不变。若不为 0，说明某月有标的缺数据导致可选集不同。\n")
    S.write(rule.OUT_MD, text)

    base = S.stats(sims["基准：7 只等权"])
    print(f"[v5] 样本 {rets.index[0]:%Y-%m}~{rets.index[-1]:%Y-%m} ({len(rets)} 月) · "
          f"V5 vs V3 持仓差异 {diff_months} 月")
    for name, r in sims.items():
        st = S.stats(r)
        print(f"[v5] {name:24s} 年化 {st['cagr'] * 100:5.1f}%  回撤 {st['dd'] * 100:6.1f}%  "
              f"Calmar {st['calmar']:5.2f}")
    print(f"[v5] 基准 Calmar {base['calmar']:.2f} · 产物 -> "
          f"{os.path.relpath(rule.OUT_MD, _STRAT)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
