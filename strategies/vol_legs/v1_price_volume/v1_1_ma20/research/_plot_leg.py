# -*- coding: utf-8 -*-
"""V1 量价家族 · 子策略 V1-1（MA20 穿越）单腿图：买入持有 vs 择时，标出买卖点。

规则：复权净值 **上穿 MA20** 买入、**下穿 MA20** 卖出，其余维持 0/1 仓位（等价于 `px > MA20`
的状态序列）。**T+1 净值成交**，空仓按货基 1.40% 计息。

画法（与 V2 / V3 共用 `strategies/vol_legs/_plot_leg_core.py`，七条腿 / 双线标注 / 归一口径一致）：

可选七腿（`--leg` 任选，默认全跑）：`a500` 023299 · `hs300` 000051 · `ndx` 270042 ·
`spx` 017641 · `kc50` 011609 · `hkt` 012805 · `gold` 000216，全部为场外复权净值。
A500 / 标普500 默认只画真基金成立之后的真实净值（桥接段见 `--with-bridge`）。

产物（本目录）：`_v1_1_a500.png` / `_v1_1_hs300.png` / `_v1_1_hkt.png`。
注意与本目录 `backtest.py` 产出的 `_v1_1_ma20_<leg>.png`（价格 + MA20 + 买卖点）不是同一张图。机械规则输出，非投资建议。
"""
import os
import sys

_HERE = os.path.dirname(os.path.abspath(__file__))     # .../v1_price_volume/v1_1_ma20/research
_V1 = os.path.dirname(_HERE)                            # .../v1_price_volume/v1_1_ma20
_VL = os.path.dirname(os.path.dirname(_V1))             # strategies/vol_legs

for _p in (_V1, _VL):
    if _p not in sys.path:
        sys.path.insert(0, _p)

import ma20                            # noqa: E402
import _plot_leg_core as core          # noqa: E402

SPEC = dict(
    mod=ma20,
    tag="V1-1",
    short="V1-1（MA20 穿越）",
    line="V1-1 择时（MA20 穿越）",
    warmup=ma20.MA_WIN,
    signal=lambda px: ma20.signal(px)[0],
    run=lambda px, sig, fee=None: ma20.run(px, sig, fee=fee),
    perf=ma20.perf,
    cash=ma20.CASH_ANNUAL,
)


if __name__ == "__main__":
    core.cli(SPEC, prefix="v1_1", out_dir=_HERE,
             desc="V1-1 单腿图（买入持有 vs MA20 穿越择时，标买卖点）")
