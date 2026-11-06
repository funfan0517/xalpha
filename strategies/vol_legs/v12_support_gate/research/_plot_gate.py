# -*- coding: utf-8 -*-
"""V12（支撑位闸门）· 单腿操作图：趋势线 + 前低支撑 + 四态背景 + 真实操作点。

图的读法（与 v5 的 `_v5_trend_{leg}.png` 同一套版式，换成 v12 的三个量）：
- **上格深色线** = 场外复权净值；**橙线** = MA200（月频趋势的读数主线）；
- **上格绿色阶梯虚线** = 最近一个**已确认**的摆动低点（支撑位，滞后 10 日确认）；
- **上格背景色带** = 当日档位：绿=满带 1.0 / 黄=半仓 0.5（贴线 ≤3%）/ 橙=观察仓 0.2（趋势不成立）/
  红=空仓 0（跌破支撑 + 冷却 20 日）；
- **▲/▼ 标在价格线上** = 换档的**决策日**（T 日盘后算、T+1 收盘净值成交），旁边小字是调完后的档位；
- **中格** = `close/support − 1`，绿带 = v11 定义的「到线带」0~3%，虚线 = 回到满带要越过的 5%（3%+2% 滞回）；
- **下格阶梯** = 带内占比（0.2/0.5/1.0），不是 pp。

产物：`research/_v12_gate_{leg}.png`（`--trend-only` 时为 `_v12_gate_{leg}_trend.png`）。
数值与 `backtest.py` 同源（`sg.state_series` + `tg.run_leg / tg.perf`），图不复算收益。

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
_V12 = os.path.dirname(_HERE)
if _V12 not in sys.path:
    sys.path.insert(0, _V12)

import support_gate as sg  # noqa: E402

tg = sg.tg

# 档位 -> (底色, 图例名)。色带的分界 = 换档日，与下格阶梯严格对齐。
STATE_STYLE = {
    1.0: ("#2e7d32", "满带（趋势成立 · 不贴线）"),
    0.5: ("#f9a825", f"半仓（贴线 ≤{sg.NEAR:.0%}）"),
    0.2: ("#ef6c00", "观察仓（趋势不成立）"),
    0.0: ("#c62828", "空仓（跌破支撑 / 冷却）"),
}
C_PRICE, C_MA, C_SUP = "#1b2b3a", "#e07b39", "#4c9a6a"
C_BUY, C_SELL, C_STEP = "#d33", "#1f8a4c", "#7b8794"


def _spans(ax, idx, state, y0, y1):
    """按档位铺背景色（fill_between + where，边界精确到日）。"""
    for lv, (color, _lab) in STATE_STYLE.items():
        mask = np.isclose(state.to_numpy(dtype=float), lv, atol=1e-9)
        if mask.any():
            ax.fill_between(idx, y0, y1, where=mask, color=color, alpha=0.10, lw=0, zorder=0)


def plot(key, out_dir=_HERE, years=None, trend_only=False):
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.dates as mdates
    import matplotlib.pyplot as plt
    from matplotlib.lines import Line2D
    from matplotlib.patches import Patch

    plt.rcParams["font.sans-serif"] = ["Microsoft YaHei", "SimHei", "DejaVu Sans"]
    plt.rcParams["axes.unicode_minus"] = False

    cfg = sg.base.LEGS[key]
    kw = dict(USE_SUPPORT=False) if trend_only else {}
    px_full = sg.load(key)
    sm_full = sg.state_series(px_full, key, **kw)
    start = px_full.index[min(int(sg.WARMUP), max(len(px_full) - 2, 0))]
    if years:
        i0 = px_full.index.searchsorted(px_full.index[-1] - pd.Timedelta(days=int(years * 365.25)))
        start = px_full.index[max(i0, px_full.index.get_loc(start), 0)]
    px, sm = px_full.loc[start:], sm_full.loc[start:]
    state, dist, sup, ma = sm["state"], sm["dist"], sm["support"], sm["ma"]
    idx = px.index

    # 净值：档位接续更早的历史（不在窗口首日重置），切片后归 1
    nav = tg.run_leg(sm_full["state"], px_full, fee=sg.fee_of(key)).loc[idx]
    nav = nav / nav.iloc[0]
    hold = px / px.iloc[0]
    pn, ph = tg.perf(nav), tg.perf(hold)
    ops = sg.actions(state)
    yrs = (idx[-1] - idx[0]).days / 365.25

    lines = [px, ma.dropna(), sup.dropna()]
    lo = float(min(s.min() for s in lines if len(s))) * 0.97
    hi = float(max(s.max() for s in lines if len(s))) * 1.03
    log = (hi / lo) > 4.0                      # 长牛腿（纳指/标普）线性轴会把前几年压扁，换对数轴

    fig = plt.figure(figsize=(13.2, 9.4))
    gs = fig.add_gridspec(3, 1, height_ratios=[4.2, 1.0, 0.9], hspace=0.09,
                          left=0.062, right=0.975, top=0.775, bottom=0.195)
    ax = fig.add_subplot(gs[0])
    axd = fig.add_subplot(gs[1], sharex=ax)
    axp = fig.add_subplot(gs[2], sharex=ax)

    # ---- 上格：档位底色 + MA200 + 支撑阶梯 + 价格 + 操作点
    _spans(ax, idx, state, lo, hi)
    ax.plot(idx, ma.to_numpy(dtype=float), color=C_MA, lw=1.25, zorder=2,
            label=f"MA{sg.MA_WIN}（月频趋势读数）")
    ax.step(idx, sup.to_numpy(dtype=float), where="post", color=C_SUP, lw=1.0, ls=":",
            zorder=2, label="前低支撑（已确认枢轴低点）")
    ax.plot(idx, px.to_numpy(dtype=float), color=C_PRICE, lw=1.3, zorder=3,
            label=f"{cfg['name']} 复权净值")
    ax.set_ylim(lo, hi)
    if log:
        ax.set_yscale("log")
        ax.set_ylim(lo, hi)
        ticks = [t for t in (0.5, 1, 1.5, 2, 3, 4, 5, 6, 8, 10, 12, 15, 20) if lo <= t <= hi]
        ax.set_yticks(ticks)
        ax.set_yticklabels([f"{t:g}" for t in ticks])
        ax.set_ylabel("场外复权净值（对数轴）", fontsize=10)
    else:
        ax.set_ylabel("场外复权净值", fontsize=10)
    ax.grid(ls="--", lw=0.6, alpha=0.30, zorder=1)
    ax.tick_params(labelbottom=False)

    if ops:
        # 动作密集时（完整规则七腿平均 10 次/年）三角标会连成一条毯子盖住价格线，缩小一号
        dense = len(ops) > 40
        ms, mew = (5.2, 1.0) if dense else (7.5, 1.4)
        bd = [o["date"] for o in ops if o["kind"] == "buy"]
        sd = [o["date"] for o in ops if o["kind"] == "sell"]
        ax.plot(bd, [float(px.loc[d]) for d in bd], ls="none", marker="^", ms=ms,
                mfc="none", mec=C_BUY, mew=mew, zorder=6)
        ax.plot(sd, [float(px.loc[d]) for d in sd], ls="none", marker="v", ms=ms,
                mfc="none", mec=C_SELL, mew=mew, zorder=6)
        # 档位文字只标最近几笔（全标会糊成一片，档位本身在下格已经能读）；偏移上下交替防撞
        show = ops if len(ops) <= 10 else ops[-6:]
        for o in show:
            up = o["kind"] == "buy"
            ax.annotate(sg.LEVEL_TXT.get(round(o["to"], 6), f"{o['to']:.0%}"),
                        (o["date"], float(px.loc[o["date"]])), textcoords="offset points",
                        xytext=(0, 9 if up else -15), ha="center", fontsize=7.2,
                        color=C_BUY if up else C_SELL, zorder=7)

    # ---- 中格：距支撑（v11 的 dist = close/support − 1）
    d = dist.to_numpy(dtype=float) * 100.0
    axd.plot(idx, d, color="#4b5d6b", lw=1.0, zorder=3)
    axd.axhspan(0.0, sg.NEAR * 100.0, color="#2e7d32", alpha=0.16, lw=0, zorder=1)
    axd.axhline(0.0, color=C_SUP, lw=1.0, ls=":", zorder=2)
    axd.axhline((sg.NEAR + sg.HYST) * 100.0, color="#b6c0cb", lw=0.8, ls="--", zorder=2)
    fin = d[np.isfinite(d)]
    dlo = min(float(fin.min()) if len(fin) else 0.0, -2.0 * sg.NEAR * 100) - 2.0
    dhi = max(float(fin.max()) if len(fin) else 1.0, 2.0 * sg.NEAR * 100) + 2.0
    axd.set_ylim(dlo, dhi)
    axd.set_ylabel("距支撑 %", fontsize=9, color="#6b7a8c")
    axd.tick_params(labelsize=8, labelbottom=False)
    axd.grid(ls="--", lw=0.5, alpha=0.25)
    axd.set_title(f"绿带 0~{sg.NEAR:.0%} = 贴线（→ 半仓）· 灰虚线 {sg.NEAR + sg.HYST:.0%} = 回满带门槛"
                  f"（含 {sg.HYST:.0%} 滞回）· 跌破 0 = 破位清仓",
                  loc="left", fontsize=7.7, color="#2e7d32", pad=3.0)

    # ---- 下格：带内档位阶梯
    axp.fill_between(idx, 0, state.to_numpy(dtype=float), step="post",
                     color=C_STEP, alpha=0.14, lw=0, zorder=1)
    axp.step(idx, state.to_numpy(dtype=float), where="post", color=C_STEP, lw=1.3, zorder=3)
    axp.set_ylim(-0.03, 1.10)
    axp.set_yticks([0.0, sg.OBSERVE, sg.HALF, sg.FULL])
    axp.set_yticklabels([f"空仓 {0:.0%}", f"观察 {sg.OBSERVE:.0%}",
                         f"半仓 {sg.HALF:.0%}", f"满带 {sg.FULL:.0%}"], fontsize=8.2,
                        color="#6b7a8c")
    axp.set_ylabel("带内仓位", fontsize=9, color="#6b7a8c")
    axp.grid(ls="--", lw=0.55, alpha=0.30)
    axp.tick_params(axis="y", colors="#6b7a8c", length=2, labelsize=8)
    for a in (ax, axd, axp):
        for s in ("top", "right"):
            a.spines[s].set_visible(False)
    if years:
        axp.xaxis.set_major_locator(mdates.MonthLocator(interval=6))
        axp.xaxis.set_major_formatter(mdates.DateFormatter("%Y-%m"))
    else:
        axp.xaxis.set_major_locator(mdates.YearLocator())
        axp.xaxis.set_major_formatter(mdates.DateFormatter("%Y"))
    axp.set_xlabel("决策日 = 信号当日盘后；成交日 = 次日收盘净值（T+1）", fontsize=9,
                   color="#5b6b7a")

    # ---- 图例（标题与坐标区之间，两行）
    handles = [
        Line2D([], [], color=C_PRICE, lw=1.3, label=f"{cfg['name']} 复权净值"),
        Line2D([], [], color=C_MA, lw=1.25, label=f"MA{sg.MA_WIN} 趋势线（月频判定）"),
        Line2D([], [], color=C_SUP, lw=1.0, ls=":", label="前低支撑（阶梯 = 每次更新）"),
        Line2D([], [], ls="none", marker="^", ms=8, mfc="none", mec=C_BUY,
               label=f"加仓 ▲（{sum(1 for o in ops if o['kind'] == 'buy')}）"),
        Line2D([], [], ls="none", marker="v", ms=8, mfc="none", mec=C_SELL,
               label=f"减仓 ▼（{sum(1 for o in ops if o['kind'] == 'sell')}）"),
    ]
    handles += [Patch(facecolor=c, alpha=0.30, label=lab) for c, lab in STATE_STYLE.values()]
    ax.legend(handles=handles, loc="lower left", bbox_to_anchor=(0.0, 1.005, 1.0, 0.10),
              ncol=5, mode="expand", fontsize=8.2, frameon=False,
              columnspacing=1.0, handletextpad=0.5, borderaxespad=0.2)

    variant = "纯趋势（去支撑层·消融档）" if trend_only else "完整规则（趋势 + 支撑层）"
    win_txt = f"近 {years:g} 年 · " if years else ""
    fig.text(0.045, 0.972,
             f"{cfg['name']}（{cfg['otc']}）{win_txt}MA{sg.MA_WIN} 月频趋势 × 前低支撑 → 带内档位 · "
             f"{variant} · 真实操作点",
             ha="left", fontsize=13.0, color="#1b2b3a", weight="bold")
    fig.text(0.045, 0.938,
             f"{idx[0].date()} ~ {idx[-1].date()}（{yrs:.1f} 年）· 策略 {pn['ann']:+.2%}/年、"
             f"回撤 {pn['mdd']:.2%}、夏普 {pn['sharpe']:.2f}、平均曝光 {sg.exposure(state):.0%}"
             f"   |   恒持满带 {ph['ann']:+.2%}/年、回撤 {ph['mdd']:.2%}、夏普 {ph['sharpe']:.2f}"
             f"   |   换档 {len(ops)} 次（{len(ops) / max(yrs, 1e-9):.1f} 次/年）",
             ha="left", fontsize=10, color="#3d4f5f")

    note = [
        f"判定链（收盘后算、次日收盘成交）：①趋势 = close > MA{sg.MA_WIN} 且 MA{sg.MA_WIN} 近 {sg.SLOPE_N} 日上行",
        f"    （月频：月末判定、次月整月沿用）→ 不成立则观察仓 {sg.OBSERVE:.0%}；②趋势成立且距支撑 ≤{sg.NEAR:.0%}"
        f"（贴线）→ 半仓 {sg.HALF:.0%}，否则满带 {sg.FULL:.0%}；",
        f"    ③close < support（跌破已确认前低）→ 空仓 + 冷却 {sg.COOL} 日，要等 close 站上「支撑 + {sg.NEAR:.0%}」才回补。",
        f"限速：非风控换档至少隔 {sg.ACTION_GAP} 个交易日（滞回 {sg.HYST:.0%}）；破位清仓不受限 —— 风控优先。"
        "扣费 = 真实申赎费 + 滑点 0.1%，空仓按货基计息。",
        "支撑位只认已确认的摆动低点（21 日窗口极值，滞后 10 个交易日确认，无前视），且只认收盘价（本地无 OHLCV）。",
        "注意：本图默认画完整规则，它在七腿回测里是负贡献（支撑层 -4~-5pp/年、换手 ×7，见 backtest/_v12_support_bt.md §7）；",
        "加 --trend-only 看纯趋势档（唯一站得住的配置）。图不复算收益，机械规则输出，非投资建议。",
    ]
    fig.text(0.045, 0.012, "\n".join(note), fontsize=8.2, color="#5b6b7a", linespacing=1.55,
             va="bottom")

    out = os.path.join(out_dir, f"_v12_gate_{key}{'_trend' if trend_only else ''}"
                                f"{f'_{years:g}y' if years else ''}.png")
    fig.savefig(out, dpi=135, facecolor="white")
    plt.close(fig)

    print(f"图: {out}")
    print(f"  {cfg['name']} {idx[0].date()} ~ {idx[-1].date()}（{yrs:.1f} 年）· "
          f"{'纯趋势' if trend_only else '完整'} · 换档 {len(ops)} 次"
          f"（{len(ops) / max(yrs, 1e-9):.1f} 次/年）· 策略 {pn['ann']:+.2%}/年 "
          f"回撤 {pn['mdd']:.2%} 夏普 {pn['sharpe']:.2f} 曝光 {sg.exposure(state):.0%} | "
          f"恒持 {ph['ann']:+.2%}/年 回撤 {ph['mdd']:.2%}")
    return out


def main():
    ap = argparse.ArgumentParser(description="V12 单腿操作图（趋势 + 支撑 → 档位 + 操作点）")
    ap.add_argument("--leg", choices=list(sg.LEGS), action="append", help="只画指定腿（可重复）")
    ap.add_argument("--years", type=float, help="只画最近 N 年（如 --years 3）")
    ap.add_argument("--trend-only", action="store_true",
                    help="画纯趋势档（USE_SUPPORT=False，唯一站得住的配置）")
    args = ap.parse_args()
    for key in (args.leg or list(sg.LEGS)):
        plot(key, years=args.years, trend_only=args.trend_only)


if __name__ == "__main__":
    main()
