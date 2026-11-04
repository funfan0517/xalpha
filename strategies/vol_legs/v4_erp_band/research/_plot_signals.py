# -*- coding: utf-8 -*-
"""V4（ERP 两带滞回）· 操作点底图：只出图，不重算回测。

产物：`../backtest/_v4_erp_band_signals.png`（与 `backtest.py` 里那张同一份图、同一条口径）。

为什么单独一个脚本：每日操作要刷新这张图，但跑 `backtest.py` 会顺带重写
`backtest/_v4_erp_band_bt.md` / `.jsonl`（README 与每日报告都引用了里面的具体数字，
如「14.2 年 / -38.96%」），天天重算会让那些数字随日期漂移。这里只做「取帧 → 画图」。

用法：
- `python _plot_signals.py`            读缓存（默认，接在 v4 `_daily_scan.py --refresh` 之后）
- `python _plot_signals.py --refresh`  先联网刷新 CSI / 10Y 国债 CSV

机械规则输出，非投资建议。
"""
import os
import sys

try:
    sys.stdout.reconfigure(encoding="utf-8")  # type: ignore[attr-defined]
except Exception:  # noqa: BLE001
    pass

_HERE = os.path.dirname(os.path.abspath(__file__))
_PKG = os.path.dirname(_HERE)
_VL = os.path.dirname(_PKG)
for _p in (_PKG, _VL):
    if _p not in sys.path:
        sys.path.insert(0, _p)

import erp     # noqa: E402
import ladder as L  # noqa: E402

OUT = os.path.join(_PKG, "backtest", "_v4_erp_band_signals.png")


def main():
    refresh = "--refresh" in sys.argv
    df = L.frame(refresh=refresh)
    d = df[df["ready"]]                       # 与 backtest.align(L.frame()) 同一口径
    if d.empty:
        raise RuntimeError("通道未就绪，样本为空")

    os.makedirs(os.path.dirname(OUT), exist_ok=True)
    path = erp.plot_overlay_signals(
        d, OUT,
        title="股债利差 vs 沪深300 · 操作点（两带滞回）",
        subtitle=f"{d.index[0]:%Y-%m-%d} 至 {d.index[-1]:%Y-%m-%d}（日线）"
                 f" · 通道 {L.CFG['window']} 日 / ±{L.CFG['k']:g}σ"
                 f" · 带界 z={L.CFG['edges']} · ▲加仓 / ▼减仓")
    print(f"图: {path}")
    print(f"  {d.index[0].date()} ~ {d.index[-1].date()}（{len(d)} 行）· "
          f"z={float(d['z'].iloc[-1]):.2f} · 操作点 {int((d['event'] != 0).sum())} 个")


if __name__ == "__main__":
    main()
