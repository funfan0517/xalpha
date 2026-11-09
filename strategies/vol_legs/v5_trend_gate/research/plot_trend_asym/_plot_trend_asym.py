# -*- coding: utf-8 -*-
"""V5 非对称口径（**快下车 + 慢上车**）· 趋势线 + 操作点单腿图。

## 这是什么口径

`README §3.6 / trend_gate.MODE="asym"`：两类错误的代价本来不对称 —— A 股阴跌以年为单位
（下车慢 = 持续失血），反弹往往几周内完成（上车慢 = 少赚一段）。所以把两件事拆开：

- **减仓**用快线：`MA120` 的 `5` 日斜率（`DOWN_WIN` / `DOWN_SLOPE`）；
- **加仓**仍由慢线把门：`MA250` 的 `20` 日斜率（主读数，`KIND="ma"` 默认参数）。

实现是「两套读数各自算一次目标档位、取 `min`」—— 所以快线**只能把仓位压得更低，不能抬得更高**。
四象限（两套各自一套）与 5/20 日阶梯不变，换的只是「谁有权减仓」。

## 与默认口径图的关系

版式、配色、坐标口径与 `research/plot_trend/_plot_trend.py` **完全一致**（直接复用它的 `plot()`），
只多画一条虚线快线 `MA120`，标题与脚注换成非对称语义。两套图可以并排看：
同一段行情里，默认口径（只 MA250）在哪里没反应过来、非对称口径提前压了多少。

产物（本目录）：`_v5_trend_{key}_asym.png` + `..._asym_3y.png`（六腿 × 长期/近三年）。

用法（**从仓库根跑**）：
- `python strategies/vol_legs/v5_trend_gate/research/plot_trend_asym/_plot_trend_asym.py`
  → 六腿长期 + 近三年（默认口径含波动层）；
- `... _plot_trend_asym.py --leg kc50 --down-win 50 --down-slope 5` → 换快线参数、只画一条腿；
- `... _plot_trend_asym.py --no-vol` → 关掉波动层（纯趋势对照，和 README §3.6 的表同口径）。

机械规则输出，非投资建议。
"""
import argparse
import os
import sys

try:
    sys.stdout.reconfigure(encoding="utf-8")  # type: ignore[attr-defined]
except Exception:  # noqa: BLE001
    pass

_HERE = os.path.dirname(os.path.abspath(__file__))       # research/plot_trend_asym
_RES = os.path.dirname(_HERE)                            # research
_V5 = os.path.dirname(_RES)                              # strategies/vol_legs/v5_trend_gate
for _p in (_V5, os.path.join(_RES, "plot_trend")):
    if _p not in sys.path:
        sys.path.insert(0, _p)

import trend_gate as tg          # noqa: E402
import _plot_trend as base       # noqa: E402  默认口径的绘图实现（版式唯一来源，不复制粘贴）

# 快线参数：README §3.6 实测最优的一档（+18.12% / 回撤 −11.61% / 夏普 1.58，调仓 25→32 次）
DOWN_WIN = 120
DOWN_SLOPE = 5


def main():
    ap = argparse.ArgumentParser(description="V5 非对称口径（快下车 + 慢上车）单腿图")
    ap.add_argument("--leg", choices=list(base.SPEC), action="append",
                    help="只画指定腿（可重复）；默认全部")
    ap.add_argument("--down-win", type=int, default=DOWN_WIN,
                    help=f"减仓快线窗口（默认 {DOWN_WIN}；可选 20 / 50 / 120）")
    ap.add_argument("--down-slope", type=int, default=DOWN_SLOPE,
                    help=f"快线斜率回看日数（默认 {DOWN_SLOPE}）")
    ap.add_argument("--no-vol", action="store_true",
                    help="关掉波动层（纯趋势对照，与 README §3.6 的灵敏度表同口径）")
    ap.add_argument("--long-only", action="store_true", help="只出长期图，不出近三年")
    ap.add_argument("--years", type=float, help="只出最近 N 年（与 --long-only 互斥，优先本参数）")
    args = ap.parse_args()

    if args.no_vol:
        tg.VOL_USE = False

    kw = dict(MODE="asym", DOWN_WIN=args.down_win, DOWN_SLOPE=args.down_slope)
    if args.years:
        spans = [args.years]
    else:
        spans = [None] if args.long_only else [None, 3]
    for key in (args.leg or list(base.SPEC)):
        for yr in spans:
            base.plot(key, out_dir=_HERE, kw=kw, tag="_asym", years=yr)


if __name__ == "__main__":
    main()
