# -*- coding: utf-8 -*-
"""v3 参数标定：双侧趋势闸门 × 均线窗口 × 削减比例 × 冷却期 × 现金计息。

用途：确定 rule.py 的默认值（MA_TREND / TREND_CUT / COOL_BARS / CASH_YIELD），并留作调优记录。
用法：python strategies/growth_div_rotation/v3_trend_filter/research/_tune.py
产物：仅打印（不落盘）；结论见 ../backtest/_v3_report.md §5 与 ../README.md

⚠ 网格是**在同一段全样本上**跑的，最优格必然带选择偏差 —— 报告里已如实标注。
"""
import io
import os
import sys

try:
    sys.stdout.reconfigure(encoding="utf-8")
except (AttributeError, OSError):
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8")

_DIR = os.path.dirname(os.path.abspath(__file__))
_V3 = os.path.dirname(_DIR)
if _V3 not in sys.path:
    sys.path.insert(0, _V3)

import rule  # noqa: E402
import data as datamod  # noqa: E402
import factors  # noqa: E402
import engine  # noqa: E402


def setup():
    wide = datamod.load_wide()
    g, d = wide[rule.GROWTH_INDEX], wide[rule.DIV_INDEX]
    r = factors.ratio_series(g, d)
    return r, g.reindex(r.index), d.reindex(r.index)


def row(tag, r, g, d, **kw):
    """一行一个变体：绩效 + 平均三资产暴露。"""
    W = engine.target_weights(r, g, d, **kw)
    res = engine.run(g, d, W)
    m = engine.metrics(res["eq"])
    print(f"{tag:<34}{m['ann'] * 100:>8.2f}%{m['dd'] * 100:>10.2f}%{m['shp']:>7.2f}"
          f"{m['calmar']:>8.2f}{len(res['switches']):>6}"
          f"{W['growth'].mean():>8.0%}{W['div'].mean():>8.0%}{W['cash'].mean():>8.0%}")
    return m


def main():
    r, g, d = setup()
    head = (f"{'变体':<34}{'年化':>9}{'回撤':>11}{'夏普':>7}{'Calmar':>8}{'切换':>6}"
            f"{'成长':>8}{'红利':>8}{'现金':>8}")
    print(head)
    print("-" * len(head))
    row("v2 基线（0.35/0.65，无闸门）", r, g, d, use_trend=False)
    print("-" * len(head))

    for ma in rule.SENS_MA:
        for cut in rule.SENS_CUT:
            row(f"双侧 MA{ma} 削减 {cut:.0%}", r, g, d, ma_window=ma, cut=cut)
        print("-" * len(head))

    print(f"\n冷却期（双侧 MA{rule.MA_TREND} 削减 {rule.TREND_CUT:.0%}）：")
    print(head)
    print("-" * len(head))
    for cool in rule.SENS_COOL:
        row(f"  cool={cool}", r, g, d, cool=cool)
    print("-" * len(head))

    print("\n现金计息（默认权重，只看年化/回撤/Calmar）：")
    W = engine.target_weights(r, g, d)
    for cy in rule.SENS_CASH:
        m = engine.metrics(engine.run(g, d, W, cash_yield=cy)["eq"])
        print(f"  现金年化 {cy:>5.1%}{m['ann'] * 100:>12.2f}%{m['dd'] * 100:>11.2f}%"
              f"{m['calmar']:>8.2f}")


if __name__ == "__main__":
    main()
