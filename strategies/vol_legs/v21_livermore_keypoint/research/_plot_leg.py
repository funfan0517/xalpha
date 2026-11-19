# -*- coding: utf-8 -*-
"""v21 单腿图：沪深300 · 利弗莫尔关键点买入法。

产物：`research/_v21_hs300.png`（长期）与 `_v21_hs300_3y.png`（近三年）。
信号与净值在全样本上算，近三年只截末端并归 1。机械规则输出，非投资建议。
"""
import argparse
import os
import sys

_HERE = os.path.dirname(os.path.abspath(__file__))
_STRAT = os.path.dirname(_HERE)
_VL = os.path.dirname(_STRAT)
# 顺序必须是「近的插到最前」：先 _VL 再 _STRAT 最后 _HERE，保证 import engine
# 解析到本策略的 engine.py，而不是 vol_legs 根目录那份（名字冲突不会报错，只会静默换规则）。
for _p in (_VL, _STRAT, _HERE):
    if _p not in sys.path:
        sys.path.insert(0, _p)

import engine                  # noqa: E402
import _plot_leg_core as core  # noqa: E402

SPEC = dict(
    mod=engine, tag="v21", short="利弗莫尔关键点（20 日突破 + 20/20/20/40 金字塔）",
    line="v21 利弗莫尔关键点择时",
    warmup=engine.WARMUP,
    signal=lambda px: engine.weight(px),
    mark_signal=lambda px: (engine.weight(px) > 1e-9).astype(float),
    run=lambda px, sig, fee=None: engine.run(px, sig, fee=fee),
    perf=engine.perf, cash=engine.CASH_ANNUAL,
    # 状态机返回的已经是「实际持仓」（含 T+1 成交滞后），不能再 shift 一次
    lag=0,
)


def main():
    ap = argparse.ArgumentParser(description="v21 单腿图（买入持有 vs 利弗莫尔关键点）")
    ap.add_argument("--years", type=float, metavar="N",
                    help="只画末端 N 年（如 3），文件名加 _3y 后缀")
    args = ap.parse_args()
    core.plot(engine.LEG, SPEC, prefix="v21", out_dir=_HERE, years=args.years)


if __name__ == "__main__":
    main()
