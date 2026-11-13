# -*- coding: utf-8 -*-
"""V2（BOLL 上轨突破）单腿图：买入持有 vs 择时，标出买卖点。

规则：收盘上穿 `BOLL(35, 2)` 上轨买入（前一日在上轨之下）；收盘跌破**中轨**卖出；
持仓时仓位再乘**波动上限** `min(1, 20% / σ20年化)`（`PARAMS["TV"]`，设 None 关闭）。
**T+1 净值成交**，空仓按货基 1.40% 计息。

画法（与 V1 / V3 共用 `strategies/vol_legs/_plot_leg_core.py`，七条腿 / 双线标注 / 归一口径一致）：

可选七腿（`--leg` 任选，默认全跑）：`a500` 023299 · `hs300` 000051 · `ndx` 270042 ·
`spx` 017641 · `kc50` 011609 · `hkt` 012805 · `gold` 000216，全部为场外复权净值。
A500 / 标普500 默认只画真基金成立之后的真实净值（桥接段见 `--with-bridge`）。

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
    short="V2（BOLL 35,2 上轨突破 + 波动上限）",
    line=f"V2 择时（BOLL 上轨突破 35,2 + 波动上限 {boll.PARAMS['TV']:.0%}）",
    warmup=boll.PARAMS["N"],
    # 净值走「信号 × 波动上限」的连续仓位；▲▼ 仍按 0/1 入场判定标，否则会被微调糊满
    signal=lambda px: boll.weight(px, boll.signal(px)[0]),
    mark_signal=lambda px: boll.signal(px)[0],
    run=lambda px, sig, fee=None: boll.run(px, sig, fee=fee),
    perf=boll.perf,
    cash=boll.CASH_ANNUAL,
)


if __name__ == "__main__":
    core.cli(SPEC, prefix="v2", out_dir=_HERE,
             desc="V2 单腿图（买入持有 vs BOLL 上轨突破择时，标买卖点）")
