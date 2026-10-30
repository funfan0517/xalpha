# -*- coding: utf-8 -*-
"""V3（MACD+BOLL 自适应窗口）单腿图：买入持有 vs 择时，标出买卖点。

规则：`DIF>0 且 DIF>DEA` + 布林开口放大 + 收盘上穿上轨买入；死叉或跌破中轨卖出；
布林窗口按 ER(20) 滞回 20↔30。**T+1 净值成交**，空仓按货基 1.40% 计息。

画法（与 V2 共用 `strategies/vol_legs/_plot_leg_core.py`，三条腿 / 双线标注 / 归一口径一致）：

| key | 标的 | 序列 | 说明 |
|---|---|---|---|
| `a500` | 中证A500 | `023299` 场外复权净值 | 六腿之一；**默认只画成立（2025-03-27）之后** |
| `hs300` | 沪深300 | `000051` 场外复权净值 | 不属六腿，同一套规则的同口径外推（也是 A500 的桥接基金） |
| `hkt` | 恒生科技 | `012805` 场外复权净值 | 六腿之一；QDII 净值滞后 T+1~T+2 |

产物（本目录）：`_v3_a500.png` / `_v3_hs300.png` / `_v3_hkt.png`。机械规则输出，非投资建议。
"""
import os
import sys

_HERE = os.path.dirname(os.path.abspath(__file__))
_V3 = os.path.dirname(_HERE)
_VL = os.path.dirname(_V3)

for _p in (_V3, _VL):
    if _p not in sys.path:
        sys.path.insert(0, _p)

import macd_boll                       # noqa: E402
import _plot_leg_core as core          # noqa: E402

SPEC = dict(
    mod=macd_boll,
    tag="V3",
    short="V3（MACD+BOLL）",
    line="V3 择时（MACD+BOLL）",
    warmup=macd_boll.WARMUP,
    signal=lambda px: macd_boll.signal(px),
    run=lambda px, sig, fee=None: macd_boll.run(px, sig, fee=fee),
    perf=macd_boll.perf,
    cash=macd_boll.CASH_ANNUAL,
)


if __name__ == "__main__":
    core.cli(SPEC, prefix="v3", out_dir=_HERE,
             desc="V3 单腿图（买入持有 vs MACD+BOLL 择时，标买卖点）")
