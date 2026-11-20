# -*- coding: utf-8 -*-
"""v16 单腿图：纳指100 · MA200 切换（上=V10 波动层 / 下=V12 八态趋势）。

产物：`research/_<v>___leg__.png`（长期）与 `_<v>___leg___3y.png`（近三年）。
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

PARAMS = {}

SPEC = dict(
    mod=engine, tag="v16", short="MA200 切换（上=V10 / 下=V12）",
    line="v16 择时（MA200 切换：上=V10 波动层 / 下=V12 八态趋势）",
    warmup=engine.WARMUP,
    signal=lambda px: engine.weight(px),
    mark_signal=lambda px: (engine.weight(px) >= 0.5).astype(float),
    run=lambda px, sig, fee=None: engine.run(px, sig, fee=fee),
    perf=engine.perf, cash=engine.CASH_ANNUAL,
)


def main():
    ap = argparse.ArgumentParser(description="v16 单腿图（买入持有 vs 择时）")
    ap.add_argument("--years", type=float, metavar="N",
                    help="只画末端 N 年（如 3），文件名加 _3y 后缀")
    args = ap.parse_args()
    core.plot(engine.LEG, SPEC, prefix="v16", out_dir=_HERE, years=args.years)


if __name__ == "__main__":
    main()
