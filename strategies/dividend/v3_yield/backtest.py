#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""V3 · 股息率单指标红利策略 —— 回测与报告生成

用法::

    python strategies/dividend/v3_yield/backtest.py
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
    sims, _, _ = S.run_variants(q, rets, xs, k=rule.TOP_K)

    text = S.render_report(rule.TITLE, rule.SUBTITLE, q, raw, sims,
                           {"unit_fmt": rule.unit_fmt}, rets=rets, xs=xs)
    text += "\n" + "\n".join(S.conclusion(sims, rule.NOTE)) + "\n"
    S.write(rule.OUT_MD, text)

    base = S.stats(sims["基准：7 只等权"])
    print(f"[v3] 样本 {rets.index[0]:%Y-%m}~{rets.index[-1]:%Y-%m} ({len(rets)} 月)")
    for name, r in sims.items():
        st = S.stats(r)
        print(f"[v3] {name:24s} 年化 {st['cagr'] * 100:5.1f}%  回撤 {st['dd'] * 100:6.1f}%  "
              f"Calmar {st['calmar']:5.2f}")
    print(f"[v3] 基准 Calmar {base['calmar']:.2f} · 产物 -> "
          f"{os.path.relpath(rule.OUT_MD, _STRAT)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
