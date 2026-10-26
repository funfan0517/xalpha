#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""V6 · 股债收益比单指标红利策略 —— 回测与报告生成

用法::

    python strategies/dividend/v6_ratio/backtest.py
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

    # 与 V5 的对比：减法 vs 除法，同一逾期只差口径，看差别有多大
    q_sp = S.cheapness("spread").reindex(rets.index)
    bq_ratio = S.board_q(q, S.available(q, rets))
    bq_spread = S.board_q(q_sp, S.available(q_sp, rets))
    text += ("\n## 附：与 V5（股债利差）的对比\n\n"
             f"- 板块贵度均值：收益比 **{bq_ratio.mean():.2f}** vs 利差 **{bq_spread.mean():.2f}**"
             "（越低越便宜）。收益比的贵度更贴底、波动更小 → 信号更钝。\n"
             "- 两者横截面排序相同（y10 是共同项），差异只来自分位口径。\n"
             f"- 两者当前贵度：收益比 {bq_ratio.iloc[-1]:.2f}、利差 {bq_spread.iloc[-1]:.2f}；"
             f"清仓线 {rule.Q_OUT:.2f}，本样本内收益比越线 "
             f"{int((bq_ratio >= rule.Q_OUT).sum())} 次、利差越线 "
             f"{int((bq_spread >= rule.Q_OUT).sum())} 次。\n")
    S.write(rule.OUT_MD, text)

    base = S.stats(sims["基准：7 只等权"])
    print(f"[v6] 样本 {rets.index[0]:%Y-%m}~{rets.index[-1]:%Y-%m} ({len(rets)} 月)")
    for name, r in sims.items():
        st = S.stats(r)
        print(f"[v6] {name:24s} 年化 {st['cagr'] * 100:5.1f}%  回撤 {st['dd'] * 100:6.1f}%  "
              f"Calmar {st['calmar']:5.2f}")
    print(f"[v6] 基准 Calmar {base['calmar']:.2f} · 产物 -> "
          f"{os.path.relpath(rule.OUT_MD, _STRAT)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
