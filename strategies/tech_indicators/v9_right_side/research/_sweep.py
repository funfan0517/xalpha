# -*- coding: utf-8 -*-
"""右侧买入策略 · 参数敏感性（一次跑完多个变体，供 README 选参依据）。

不追求「最优收益」，而是看**哪个变体在收益/回撤/择时边际三者上都不垫底** ——
右侧策略的价值在于「少接飞刀」, 而不是最大化收益, 因此以中位回撤与择时边际为主判据。

用法: python strategies/tech_indicators/v9_right_side/research/_sweep.py
产物: 本目录 _sweep_report.md
"""
import os
import sys

import numpy as np

sys.stdout.reconfigure(encoding="utf-8")
_DIR = os.path.dirname(os.path.abspath(__file__))
_PKG = os.path.dirname(_DIR)
_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(_PKG)))
for p in (_ROOT, os.path.dirname(_PKG), _PKG):
    if p not in sys.path:
        sys.path.insert(0, p)

import backtest as bt  # noqa: E402
import data as _data  # noqa: E402
import rule  # noqa: E402
from pipeline import bt_stats  # noqa: E402

OUT = os.path.join(_DIR, "_sweep_report.md")

# (标签, 覆盖参数) —— 每次只动一个维度, 保持其他为基线
BASE = dict(VOTE_ENTER=4, EXIT_CONFIRM=2, EXIT_REQUIRE_SLOPE=False,
            EXIT_MA=rule.EXIT_MA, BREAK_WIN=rule.BREAK_WIN,
            STOP_LOOKBACK=rule.STOP_LOOKBACK, VOL_MULT=rule.VOL_MULT)
VARIANTS = [
    ("基线 4票 · MA20×2 · 破60日高 · 放量1.5", {}),
    ("5票全过", dict(VOTE_ENTER=5)),
    ("3票即买", dict(VOTE_ENTER=3)),
    ("离场 MA20×3 日确认", dict(EXIT_CONFIRM=3)),
    ("离场要求 MA20 走平/下行", dict(EXIT_REQUIRE_SLOPE=True)),
    ("离场改 MA60×2", dict(EXIT_MA=60)),
    ("突破窗口 20 日（更早）", dict(BREAK_WIN=20)),
    ("突破窗口 120 日（更晚）", dict(BREAK_WIN=120)),
    ("量能 1.2×", dict(VOL_MULT=1.2)),
    ("量能 2.0×", dict(VOL_MULT=2.0)),
    ("结构位改 10 日", dict(STOP_LOOKBACK=10)),
    ("5票 + MA20×3", dict(VOTE_ENTER=5, EXIT_CONFIRM=3)),
    ("5票 + 结构位 10 日", dict(VOTE_ENTER=5, STOP_LOOKBACK=10)),
    ("5票 + MA20×3 + 结构位 10 日", dict(VOTE_ENTER=5, EXIT_CONFIRM=3, STOP_LOOKBACK=10)),
    ("5票 + 量能 1.2×（放宽）", dict(VOTE_ENTER=5, VOL_MULT=1.2)),
]


def apply(over):
    for k, v in BASE.items():
        setattr(rule, k, v)
    for k, v in over.items():
        setattr(rule, k, v)


def run(panels):
    rows, _fails = bt.run_pool(panels)
    return rows


def agg(rows, key):
    return float(np.median([r[key] for r in rows]))


def main():
    panels = _data.load_panels(refresh=True)
    # 因子与参数无关的部分只算一次会更快, 但各变体窗口不同, 稳妥起见逐变体重算
    lines = ["# 右侧买入 · 参数敏感性（55 只场内 ETF · 中位数）", "",
             "> 口径同 `backtest.py`：信号次日收盘成交、单边 0.03%、0/1 满仓、样本 2016-09 起。",
             "> **主判据不是年化**：右侧策略买的是「不接飞刀」，看回撤改善与择时边际是否同时为正。", "",
             "| 变体 | 中位年化 | 中位回撤 | 中位超额 | 中位持仓 | 单笔数/只 | 胜率 | 利润因子 "
             "| 择时边际(bp) | 边际为正 |",
             "|---" * 10 + "|"]
    for label, over in VARIANTS:
        apply(over)
        rows = run(panels)
        if not rows:
            continue
        trades = [t for r in rows for t in r["trade_log"]]
        ts = bt_stats.trade_stats(trades)
        inf = lambda x: "∞" if x == float("inf") else f"{x:.2f}"  # noqa: E731
        lines.append(
            f"| {label} | {agg(rows, 'st_ann') * 100:+.2f}% | {agg(rows, 'st_mdd') * 100:+.2f}% "
            f"| {(agg(rows, 'st_ann') - agg(rows, 'base_ann')) * 100:+.2f}% "
            f"| {agg(rows, 'pos_ratio') * 100:.1f}% | {agg(rows, 'trades'):.0f} "
            f"| {ts['win_rate'] * 100:.1f}% | {inf(ts['profit_factor'])} "
            f"| {agg(rows, 'timing_edge') * 1e4:+.1f} "
            f"| {sum(1 for r in rows if r['timing_edge'] > 0)}/{len(rows)} |")
        print(f"[sweep] {label}: 年化 {agg(rows, 'st_ann') * 100:+.2f}% "
              f"回撤 {agg(rows, 'st_mdd') * 100:+.2f}% 边际 {agg(rows, 'timing_edge') * 1e4:+.1f}bp")
    lines += ["", "> 机械网格输出，**不是**参数寻优结论；采纳值见 `rule.py` 顶部常量。", ""]
    open(OUT, "w", encoding="utf-8").write("\n".join(lines))
    print(f"-> {OUT}")


if __name__ == "__main__":
    main()
