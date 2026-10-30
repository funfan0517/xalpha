# -*- coding: utf-8 -*-
"""V2（BOLL 上轨突破）单腿图：买入持有 vs 择时，标出买卖点。

规则：收盘上穿 `BOLL(35, 2)` 上轨买入（前一日在上轨之下）；收盘跌破**中轨**卖出；
其余维持 0/1 仓位。**T+1 净值成交**，空仓按货基 1.40% 计息。

画法（与 V3 共用 `strategies/vol_legs/_plot_leg_core.py`，三条腿 / 双线标注 / 归一口径一致）：

| key | 标的 | 序列 | 说明 |
|---|---|---|---|
| `a500` | 中证A500 | `023299` 场外复权净值 | 六腿之一；**默认只画成立（2025-03-27）之后** |
| `hs300` | 沪深300 | `000051` 场外复权净值 | 不属六腿，同一套规则的同口径外推（也是 A500 的桥接基金） |
| `hkt` | 恒生科技 | `012805` 场外复权净值 | 六腿之一；QDII 净值滞后 T+1~T+2 |

产物（本目录）：`_v2_a500.png` / `_v2_hs300.png` / `_v2_hkt.png`。机械规则输出，非投资建议。
"""
import os
import sys

_HERE = os.path.dirname(os.path.abspath(__file__))
_V2 = os.path.dirname(_HERE)
_VL = os.path.dirname(_V2)

for _p in (_V2, _VL):
    if _p not in sys.path:
        sys.path.insert(0, _p)

import boll                            # noqa: E402
import _plot_leg_core as core          # noqa: E402

SPEC = dict(
    mod=boll,
    tag="V2",
    short="V2（BOLL 35,2 上轨突破）",
    line="V2 择时（BOLL 上轨突破 35,2）",
    warmup=boll.PARAMS["N"],
    signal=lambda px: boll.signal(px)[0],
    run=lambda px, sig, fee=None: boll.run(px, sig, fee=fee),
    perf=boll.perf,
    cash=boll.CASH_ANNUAL,
)


if __name__ == "__main__":
    core.cli(SPEC, prefix="v2", out_dir=_HERE,
             desc="V2 单腿图（买入持有 vs BOLL 上轨突破择时，标买卖点）")
