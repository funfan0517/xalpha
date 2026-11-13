# -*- coding: utf-8 -*-
"""V3（MACD+BOLL 自适应窗口）单腿图：买入持有 vs 择时，标出买卖点。

规则：`DIF>0 且 DIF>DEA` + 布林开口放大 + 收盘上穿上轨买入；死叉或跌破中轨卖出；
布林窗口按 ER(20) 滞回 20↔30。**T+1 净值成交**，空仓按货基 1.40% 计息。

画法（与 V1 / V2 共用 `strategies/vol_legs/_plot_leg_core.py`，多腿 / 双线标注 / 归一口径一致）：

| key | 标的 | 序列 | 说明 |
|---|---|---|---|
| `a500` | 中证A500 | `023299` 场外复权净值 | 七腿之一；**默认只画成立（2025-03-27）之后** |
| `hs300` | 沪深300 | `000051` 场外复权净值 | 七腿之一（v1.4 起）；也是 A500 的桥接基金 |
| `ndx` | 纳指100 | `270042` 场外复权净值 | 七腿之一；QDII 净值滞后 T+1~T+2；无桥接，2012-08 起 |
| `spx` | 标普500 | `017641` 场外复权净值 | 七腿之一；**默认只画成立（2023-04-06）之后** |
| `kc50` | 科创50 | `011609` 场外复权净值 | 七腿之一；2021-03 起 |
| `hkt` | 恒生科技 | `012805` 场外复权净值 | 七腿之一；QDII 净值滞后 T+1~T+2 |
| `gold` | 黄金 | `000216` 场外复权净值 | 七腿之一（避险腿）；2013-08 起 |

产物（本目录）：`_v3_<key>.png` × 7。机械规则输出，非投资建议。
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
    short=f"V3（MACD+BOLL + 波动上限 {macd_boll.TV:.0%}）",
    line=f"V3 择时（MACD+BOLL 自适应 + 波动上限 {macd_boll.TV:.0%}）",
    warmup=macd_boll.WARMUP,
    # 净值走「信号 × 波动上限」的连续仓位；▲▼ 仍按 0/1 入场判定标
    signal=lambda px: macd_boll.weight(px, macd_boll.signal(px)),
    mark_signal=lambda px: macd_boll.signal(px),
    run=lambda px, sig, fee=None: macd_boll.run(px, sig, fee=fee),
    perf=macd_boll.perf,
    cash=macd_boll.CASH_ANNUAL,
)


if __name__ == "__main__":
    core.cli(SPEC, prefix="v3", out_dir=_HERE,
             desc="V3 单腿图（买入持有 vs MACD+BOLL 择时，标买卖点）")
