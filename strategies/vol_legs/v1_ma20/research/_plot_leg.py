# -*- coding: utf-8 -*-
"""V1（MA20 穿越）单腿图：买入持有 vs 择时，标出买卖点。

规则：复权净值 **上穿 MA20** 买入、**下穿 MA20** 卖出，其余维持 0/1 仓位（等价于 `px > MA20`
的状态序列）。**T+1 净值成交**，空仓按货基 1.40% 计息。

画法（与 V2 / V3 共用 `strategies/vol_legs/_plot_leg_core.py`，三条腿 / 双线标注 / 归一口径一致）：

| key | 标的 | 序列 | 说明 |
|---|---|---|---|
| `a500` | 中证A500 | `023299` 场外复权净值 | 六腿之一；**默认只画成立（2025-03-27）之后** |
| `hs300` | 沪深300 | `000051` 场外复权净值 | 不属六腿，同一套规则的同口径外推（也是 A500 的桥接基金） |
| `hkt` | 恒生科技 | `012805` 场外复权净值 | 六腿之一；QDII 净值滞后 T+1~T+2 |

产物（本目录）：`_v1_a500.png` / `_v1_hs300.png` / `_v1_hkt.png`。
注意与本目录 `backtest.py` 产出的 `_v1_ma20_<leg>.png`（价格 + MA20 + 买卖点）不是同一张图。机械规则输出，非投资建议。
"""
import os
import sys

_HERE = os.path.dirname(os.path.abspath(__file__))
_V1 = os.path.dirname(_HERE)
_VL = os.path.dirname(_V1)

for _p in (_V1, _VL):
    if _p not in sys.path:
        sys.path.insert(0, _p)

import ma20                            # noqa: E402
import _plot_leg_core as core          # noqa: E402

SPEC = dict(
    mod=ma20,
    tag="V1",
    short="V1（MA20 穿越）",
    line="V1 择时（MA20 穿越）",
    warmup=ma20.MA_WIN,
    signal=lambda px: ma20.signal(px)[0],
    run=lambda px, sig, fee=None: ma20.run(px, sig, fee=fee),
    perf=ma20.perf,
    cash=ma20.CASH_ANNUAL,
)


if __name__ == "__main__":
    core.cli(SPEC, prefix="v1", out_dir=_HERE,
             desc="V1 单腿图（买入持有 vs MA20 穿越择时，标买卖点）")
