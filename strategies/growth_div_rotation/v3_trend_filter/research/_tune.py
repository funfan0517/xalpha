# -*- coding: utf-8 -*-
"""v3 参数标定：趋势叠加方式 × 封顶 × 冷却期 × 均线窗口。

用途：确定 rule.py 的默认值（TREND_CAP / COOL_BARS / MA_TREND / mode），并留作调优记录。
用法：python strategies/growth_div_rotation/v3_trend_filter/research/_tune.py
产物：仅打印（不落盘）；结论写进 ../README.md 与背后报告。
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

HEAD = ("变体", "年化", "回撤", "夏普", "Calmar", "切换", "暴露")


def setup():
    wide = datamod.load_wide()
    g, d = wide[rule.GROWTH_INDEX], wide[rule.DIV_INDEX]
    r = factors.ratio_series(g, d)
    return r, g.reindex(r.index), d.reindex(r.index)


def row(tag, w, g, d):
    res = engine.run(g, d, w)
    m = engine.metrics(res["eq"])
    print(f"{tag:<40}{m['ann'] * 100:>8.2f}%{m['dd'] * 100:>9.2f}%"
          f"{m['shp']:>7.2f}{m['calmar']:>8.2f}{len(res['switches']):>6}{w.mean():>8.1%}")
    return m, res


def main():
    r, g, d = setup()
    print(f"{HEAD[0]:<40}{HEAD[1]:>9}{HEAD[2]:>10}{HEAD[3]:>7}{HEAD[4]:>8}"
          f"{HEAD[5]:>6}{HEAD[6]:>8}")
    print("-" * 88)
    w0 = engine.signal_weights(r, g, use_trend=False)
    row("v2 0.35/0.65（无趋势，基线）", w0, g, d)
    print("-" * 88)

    for mode in ("cap", "switch"):
        for cap in rule.SENS_TREND_CAP:
            for cool in rule.SENS_COOL:
                w = engine.signal_weights(r, g, trend_cap=cap, cool=cool, mode=mode)
                row(f"{mode} cap={cap:.1f} cool={cool}", w, g, d)
        print("-" * 88)

    print("\n均线窗口（mode=cap, cap=0.0, cool=20）：")
    for ma in rule.SENS_MA:
        w = engine.signal_weights(r, g, ma_window=ma, trend_cap=0.0, cool=20)
        row(f"MA{ma}", w, g, d)


if __name__ == "__main__":
    main()
