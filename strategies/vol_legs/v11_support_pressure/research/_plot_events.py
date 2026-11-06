# -*- coding: utf-8 -*-
"""V11（支撑/压力位 × 期望值）· 事件研究图：触线点 + 前向收益分布。

图的读法（上面两格 = 价格，下面两格 = 分布）：

- **上格**：场外复权净值（对数轴）+ 趋势期底纹（月频 MA200 上行）。**▲** = 触线事件日
  （信号日收盘，实际下单是 t+1）；**竖虚线** = 支撑位（当时最近一个已确认前低）；
  **灰色横线** = 止损位（支撑下方 0.5×ATR）。只为读数清楚，最多画 `MAX_MARK` 个事件。
- **中格**：`close/support − 1`（距支撑的距离，%），0 线就是支撑本身；
  绿色带 = 「到线」阈值（0~2%）。**能一眼看出「贴线买入」总是发生在下探段**。
- **下两格**：前向 60 日收益分布 —— 左 = 事件笔（V11），右 = 同腿同区间**全部交易日**
  （买入持有基准）。两格用同一横轴范围，**中位数与胜率的差就是 §3/§4 那张表的意思**。

产物：`research/_v11_events_{leg}.png`（七腿各一张）。

机械规则输出，非投资建议。
"""
import argparse
import os
import sys

import numpy as np
import pandas as pd

try:
    sys.stdout.reconfigure(encoding="utf-8")  # type: ignore[attr-defined]
except Exception:  # noqa: BLE001
    pass

_HERE = os.path.dirname(os.path.abspath(__file__))
_V11 = os.path.dirname(_HERE)
if _V11 not in sys.path:
    sys.path.insert(0, _V11)

import support_pressure as sp  # noqa: E402

MAX_MARK = 40          # 上格最多标多少个事件（多了看不清）
C_PRICE, C_SUP, C_STOP = "#1b2b3a", "#1f8a4c", "#8a94a6"
C_EV, C_BASE = "#c0392b", "#6b8cae"


def plot(leg, out_dir=_HERE, kw=None):
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.dates as mdates
    import matplotlib.pyplot as plt
    from matplotlib.lines import Line2D
    from matplotlib.patches import Patch

    plt.rcParams["font.sans-serif"] = ["Microsoft YaHei", "SimHei", "DejaVu Sans"]
    plt.rcParams["axes.unicode_minus"] = False

    kw = dict(kw or {})
    px = sp.load(leg)
    ev, F = sp.events(px, leg, **kw)
    name = sp.rule.LEGS[leg]["name"]

    # 事件版前向收益：只取信号日
    ev_dates = pd.DatetimeIndex(ev["date"]) if len(ev) else pd.DatetimeIndex([])
    f60_ev = pd.to_numeric(ev["f60"], errors="coerce").dropna() if len(ev) else pd.Series(dtype=float)
    c = px
    f60_all = (c.shift(-60) / c - 1.0).dropna()
    f60_all = f60_all[f60_all.index >= c.index[int(sp.WARMUP)]]

    fig = plt.figure(figsize=(13.2, 10.4))
    gs = fig.add_gridspec(4, 2, height_ratios=[2.6, 0.9, 1.5, 1.5], hspace=0.42, wspace=0.20)
    ax = fig.add_subplot(gs[0, :])
    axd = fig.add_subplot(gs[1, :], sharex=ax)
    ax1 = fig.add_subplot(gs[2, 0])
    ax2 = fig.add_subplot(gs[2, 1])

    # ---- 上格：净值 + 趋势底纹 + 事件
    idx = px.index
    ax.plot(idx, px.to_numpy(dtype=float), color=C_PRICE, lw=1.2, label="场外复权净值")
    ax.set_yscale("log")
    trend = F["trend"].reindex(idx).fillna(False).to_numpy(dtype=bool)
    ax.fill_between(idx, px.min() * 0.9, px.max() * 1.1, where=trend, color="#2e7d32",
                    alpha=0.07, lw=0, label="趋势（MA200 上行 · 月频）")
    ax.set_ylim(px.min() * 0.92, px.max() * 1.08)

    show = ev.iloc[:: max(1, len(ev) // MAX_MARK)] if len(ev) else ev
    for _, r in show.iterrows():
        ax.scatter(r["date"], r["close"], marker="^", s=34, color=C_EV, zorder=6)
        ax.hlines(r["support"], r["date"], r["date"] + pd.Timedelta(days=140),
                  color=C_SUP, ls=":", lw=0.8, alpha=0.55, zorder=3)
        ax.hlines(r["stop"], r["date"], r["date"] + pd.Timedelta(days=140),
                  color=C_STOP, ls="--", lw=0.7, alpha=0.45, zorder=3)
    ax.set_ylabel("场外复权净值（对数轴）", fontsize=10)
    ax.grid(ls="--", lw=0.6, alpha=0.28)
    ax.tick_params(labelbottom=False)

    # ---- 中格：距支撑的距离
    dist = F["dist"].reindex(idx) * 100.0
    axd.plot(idx, dist.to_numpy(dtype=float), color="#4b5d6b", lw=0.8)
    axd.axhspan(0.0, float(sp.params(**kw)["BAND"]) * 100.0, color="#2e7d32", alpha=0.16, lw=0)
    axd.axhline(0.0, color=C_SUP, lw=0.9, ls=":")
    axd.set_ylim(-12, 12)
    axd.set_ylabel("距支撑 %", fontsize=9)
    axd.grid(ls="--", lw=0.5, alpha=0.25)
    axd.tick_params(labelsize=8)

    # ---- 下两格：f60 分布
    rng = (-0.30, 0.30)
    bins = np.linspace(*rng, 25)
    ax1.hist(np.clip(f60_ev.to_numpy(dtype=float), *rng), bins=bins, color=C_EV, alpha=0.80)
    ax2.hist(np.clip(f60_all.to_numpy(dtype=float), *rng), bins=bins, color=C_BASE, alpha=0.80)
    for a, s, ttl, col in ((ax1, f60_ev, "V11 事件（触线日买入）", C_EV),
                           (ax2, f60_all, "基准（同区间全部交易日）", C_BASE)):
        a.set_xlim(*rng)
        a.axvline(0.0, color="#7b8794", lw=0.9)
        if len(s):
            a.axvline(float(s.median()), color=col, lw=1.4, ls="--")
            a.set_title(f"{ttl} · n={len(s)} · 中位 {float(s.median()) * 100:+.2f}% · "
                        f"胜率 {float((s > 0).mean()) * 100:.0f}%", fontsize=9.5, color=col)
        a.set_xlabel("未来 60 个交易日收益（截断在 ±30%）", fontsize=8.6)
        a.set_ylabel("频次", fontsize=8.6)
        a.grid(ls="--", lw=0.5, alpha=0.25)

    st = sp.exit_stats(ev)
    L = [
        Line2D([], [], marker="^", ls="", color=C_EV, ms=6, label="触线事件（信号日收盘 · 密集时抽样）"),
        Line2D([], [], color=C_SUP, ls=":", lw=1.0, label="支撑位（最近已确认前低）"),
        Line2D([], [], color=C_STOP, ls="--", lw=1.0, label="止损位（支撑 −0.5×ATR14）"),
        Patch(facecolor="#2e7d32", alpha=0.20, label="趋势期（MA200 上行 · 月频）"),
        Patch(facecolor="#2e7d32", alpha=0.16, label="中格绿带 = 到线阈值（0~2%）"),
    ]
    ax.legend(handles=L, loc="lower left", bbox_to_anchor=(0.0, 1.005, 1.0, 0.08),
              ncol=5, mode="expand", fontsize=8.2, frameon=False)

    fig.text(0.045, 0.972,
             f"{name}（{sp.rule.LEGS[leg]['otc']}）· V11 支撑/压力位事件研究 · "
             f"趋势 MA{sp.MA_WIN} 月频 · 目标 {sp.RR:g}R",
             ha="left", fontsize=13.2, color="#1b2b3a", weight="bold")
    sub = (f"{px.index[0].date()} ~ {px.index[-1].date()} · 事件 {len(ev)} 笔"
           + (f" · 触 {sp.RR:g}R {st['hit_rate']:.0%} · 止损 {st['stop_rate']:.0%} · "
              f"20日未破支撑 {st['hold20']:.0%} · EV {st['ev_R']:+.2f}R" if len(ev) else ""))
    fig.text(0.045, 0.938, sub, ha="left", fontsize=10, color="#3d4f5f")

    note = [
        "▲ 是「信号日收盘」，实际下单在 t+1（场外净值）；上格只画前 40 个事件，避免线太密。",
        "中格能看出「贴线买入」总发生在下探段 —— 这就是 f60 反而不如基准的机制（报告 §8.1）。",
        "下两格同横轴、同分箱：左 = 事件笔，右 = 同腿同区间全部交易日。两边的中位数差就是「超额」，"
        "但**独立样本只有左边那几十笔**，右边几千个交易日高度自相关，不能当显著性检验。",
        f"止损按**收盘价**确认（没有盘中价），所以实际亏损常超过 1R；本图不画交易净值 —— "
        f"按文档 §4.3 的要求，本变种不报年化/回撤。",
    ]
    fig.text(0.045, 0.012, "\n".join(note), fontsize=8.3, color="#5b6b7a", linespacing=1.5, va="bottom")

    out = os.path.join(out_dir, f"_v11_events_{leg}.png")
    fig.savefig(out, dpi=135, facecolor="white")
    plt.close(fig)
    return out


def main():
    ap = argparse.ArgumentParser(description="V11 事件研究图（七腿）")
    ap.add_argument("--leg", action="append", default=None,
                    help="只画指定腿（可多次）；默认七腿全画")
    args = ap.parse_args()
    legs = args.leg or list(sp.LEGS)
    for k in legs:
        print("出图:", plot(k))


if __name__ == "__main__":
    main()
