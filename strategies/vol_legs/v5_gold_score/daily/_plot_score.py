# -*- coding: utf-8 -*-
"""V5 黄金计分仓位 · 净值 + 操作点 + 计分 + 仓位档图（**长期 / 近三年各一份**）。

三格：
- 上格：策略净值（扣真实成本）vs 恒持满带 vs 恒定半仓，▲▼ 标操作点；
- 中格：逐日 `score` 柱（−2…+2，本口径两因子；±1/±2 参考线）；
- 下格：目标档 e*（虚线）与实际档 e（实线阶梯），标出 0/25/50/75/100% 五档。

产物（本目录 `daily/`）：`_v5_gold_score.png`（全历史）· `_v5_gold_score_3y.png`（近三年）。

用法（从仓库根跑）：
    python strategies/vol_legs/v5_gold_score/daily/_plot_score.py
    python strategies/vol_legs/v5_gold_score/daily/_plot_score.py --years 3

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
_V5 = os.path.dirname(_HERE)
if _V5 not in sys.path:
    sys.path.insert(0, _V5)

import factors as F  # noqa: E402
import score as S    # noqa: E402

C_NAV, C_BH, C_HF = "#1b2b3a", "#9aa5b1", "#c9d3dd"
C_BUY, C_SELL, C_STEP, C_TGT = "#d33", "#1f8a4c", "#7b8794", "#e07b39"


def plot(years=None, out_dir=_HERE):
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.dates as mdates
    import matplotlib.pyplot as plt
    from matplotlib.lines import Line2D

    plt.rcParams["font.sans-serif"] = ["Microsoft YaHei", "SimHei", "DejaVu Sans"]
    plt.rcParams["axes.unicode_minus"] = False

    d = S.build(refresh=False)
    px_full, df = d["px"], d["df"]
    p = d["params"]
    st_full = df["state"]
    tgt_full = df["target"]

    nav_full = S.run_leg(st_full, px_full, fee=p["FEE"], slip=p["SLIP"])
    bh_full = S.run_leg(pd.Series(1.0, index=px_full.index), px_full, fee=p["FEE"], slip=p["SLIP"])
    hf_full = S.run_leg(pd.Series(0.5, index=px_full.index), px_full, fee=p["FEE"], slip=p["SLIP"])

    start = d["start"]
    if years:
        i0 = px_full.index.searchsorted(px_full.index[-1] - pd.Timedelta(days=int(years * 365.25)))
        start = px_full.index[max(i0, px_full.index.get_loc(start), 0)]

    idx = px_full.loc[start:].index
    nav = (nav_full / nav_full.loc[start]).loc[idx]
    bh = (bh_full / bh_full.loc[start]).loc[idx]
    hf = (hf_full / hf_full.loc[start]).loc[idx]
    st, tgt = st_full.loc[idx], tgt_full.loc[idx]
    score = df["score"].loc[idx]
    yrs = (idx[-1] - idx[0]).days / 365.25

    mS, mBH, mHF = S.perf(nav), S.perf(bh), S.perf(hf)
    ops = S.actions(st)
    expo = float(st.mean())

    fig = plt.figure(figsize=(13.2, 9.0))
    gs = fig.add_gridspec(3, 1, height_ratios=[3.5, 0.95, 1.0], hspace=0.10,
                          left=0.058, right=0.975, top=0.795, bottom=0.185)
    ax = fig.add_subplot(gs[0])
    axs = fig.add_subplot(gs[1], sharex=ax)
    axp = fig.add_subplot(gs[2], sharex=ax)

    # ---- 上格：净值
    ax.plot(idx, hf.to_numpy(), color=C_HF, lw=1.05, ls=":", zorder=2, label="恒定半仓（对照）")
    ax.plot(idx, bh.to_numpy(), color=C_BH, lw=1.0, ls="--", zorder=2, label="恒持满带（买入持有）")
    ax.plot(idx, nav.to_numpy(), color=C_NAV, lw=1.45, zorder=3, label="策略净值")
    lo = float(min(nav.min(), bh.min(), hf.min())) * 0.95
    hi = float(max(nav.max(), bh.max(), hf.max())) * 1.05
    ax.set_ylim(lo, hi)
    if hi / lo > 3.2:
        ax.set_yscale("log")
        ax.set_ylim(lo, hi)
        ticks = [t for t in (1, 1.5, 2, 3, 4, 5, 6) if lo <= t <= hi]
        ax.set_yticks(ticks)
        ax.set_yticklabels([f"{t:g}" for t in ticks])
        ax.minorticks_off()
    ax.set_ylabel("净值（起点 = 1）", fontsize=10)
    ax.grid(ls="--", lw=0.6, alpha=0.30, zorder=1)
    ax.tick_params(labelbottom=False)

    show = ops if len(ops) <= 16 else ops[-14:]
    if show:
        bd = [o["date"] for o in show if o["kind"] == "buy"]
        sd = [o["date"] for o in show if o["kind"] == "sell"]
        if bd:
            ax.plot(bd, [float(nav.loc[b]) for b in bd], ls="none", marker="^", ms=7,
                    mfc="none", mec=C_BUY, mew=1.3, zorder=6)
        if sd:
            ax.plot(sd, [float(nav.loc[b]) for b in sd], ls="none", marker="v", ms=7,
                    mfc="none", mec=C_SELL, mew=1.3, zorder=6)

    # ---- 中格：score
    cols = np.where(score.to_numpy() > 0, "#c62828",
                    np.where(score.to_numpy() < 0, "#1f8a4c", "#b0b8c1"))
    axs.bar(idx, score.to_numpy(), width=1.6, color=cols, alpha=0.85, lw=0)
    for y in (-2, -1, 1, 2):
        axs.axhline(y, color="#8d98a3", lw=0.6, ls=":")
    axs.axhline(0, color="#5b6b7a", lw=0.7)
    axs.set_ylim(-2.6, 2.6)
    axs.set_yticks([-2, -1, 0, 1, 2])
    axs.set_ylabel("score", fontsize=9, color="#6b7a8c")
    axs.tick_params(axis="y", labelsize=8, colors="#6b7a8c")
    axs.tick_params(labelbottom=False)
    axs.grid(ls="--", lw=0.55, alpha=0.25, axis="x")

    # ---- 下格：目标档 vs 实际档
    axp.plot(idx, tgt.to_numpy(), color=C_TGT, lw=1.0, ls="--", zorder=3, label="目标档 e*")
    axp.fill_between(idx, 0, st.to_numpy(), step="post", color=C_STEP, alpha=0.16, lw=0, zorder=1)
    axp.step(idx, st.to_numpy(), where="post", color=C_STEP, lw=1.3, zorder=4, label="实际档 e")
    axp.set_ylim(-0.03, 1.08)
    axp.set_yticks([0.0, 0.25, 0.5, 0.75, 1.0])
    axp.set_yticklabels(["0%", "25%", "50%", "75%", "100%"], fontsize=8.2, color="#6b7a8c")
    axp.set_ylabel("带内仓位", fontsize=9, color="#6b7a8c")
    axp.grid(ls="--", lw=0.55, alpha=0.30)
    for a in (ax, axs, axp):
        for s in ("top", "right"):
            a.spines[s].set_visible(False)
    if years:
        axp.xaxis.set_major_locator(mdates.MonthLocator(interval=6))
        axp.xaxis.set_major_formatter(mdates.DateFormatter("%Y-%m"))
    else:
        axp.xaxis.set_major_locator(mdates.YearLocator())
        axp.xaxis.set_major_formatter(mdates.DateFormatter("%Y"))
    axp.set_xlabel("成交日 = 信号次日（T+1）", fontsize=9, color="#5b6b7a")

    hd = [Line2D([], [], color=C_NAV, lw=1.45, label="策略净值（扣费）"),
          Line2D([], [], color=C_BH, lw=1.0, ls="--", label="恒持满带"),
          Line2D([], [], color=C_HF, lw=1.05, ls=":", label="恒定半仓"),
          Line2D([], [], ls="none", marker="^", ms=8, mfc="none", mec=C_BUY,
                 label=f"加仓 ▲（{sum(1 for o in ops if o['kind'] == 'buy')}）"),
          Line2D([], [], ls="none", marker="v", ms=8, mfc="none", mec=C_SELL,
                 label=f"减仓 ▼（{sum(1 for o in ops if o['kind'] == 'sell')}）"),
          Line2D([], [], color=C_TGT, lw=1.0, ls="--", label="目标档 e*（下格）")]
    ax.legend(handles=hd, loc="lower left", bbox_to_anchor=(0.0, 1.005, 1.0, 0.10),
              ncol=len(hd), mode="expand", fontsize=8.2, frameon=False,
              columnspacing=1.0, handletextpad=0.5, borderaxespad=0.2)

    win_txt = f"近 {years:g} 年 · " if years else ""
    fig.text(0.045, 0.972,
             f"黄金（{S.SIGNAL_CODE}）· V5 计分仓位 · "
             f"价格趋势 MA{F.MA_FAST}/{F.MA_SLOW}+{F.BRK_WIN}日高低 ｜ "
             f"实际利率 DFII10 {F.RATE_WIN}日 ±{F.RATE_TH:.0f}bp",
             ha="left", fontsize=12.6, color="#1b2b3a", weight="bold")
    fig.text(0.045, 0.940,
             f"{idx[0].date()} ~ {idx[-1].date()}（{yrs:.1f} 年）· {win_txt}"
             f"策略 {mS['ann']:+.2%}/年、回撤 {mS['mdd']:.2%}、夏普 {mS['sharpe']:.2f}、"
             f"平均仓位 {expo:.0%}、调仓 {len(ops)} 次",
             ha="left", fontsize=9.6, color="#3d4f5f")
    fig.text(0.045, 0.913,
             f"对照：恒持满带 {mBH['ann']:+.2%}/年、回撤 {mBH['mdd']:.2%}、夏普 {mBH['sharpe']:.2f}"
             f"   |   恒定半仓 {mHF['ann']:+.2%}/年、回撤 {mHF['mdd']:.2%}、夏普 {mHF['sharpe']:.2f}",
             ha="left", fontsize=9.6, color="#5b6b7a")

    note = [
        f"规则：score = f_p + f_r（资金流 f_f 因只有 120 天历史、按规格 §1 剔除）→ "
        f"≥+2 100% / +1 75% / 0 50% / −1 25% / ≤−2 0~10%；"
        f"缺因子 → 低置信度 → 极端档向 50% 收缩一档（两因子口径实际落在 75/50/25 三档）。",
        f"防抖：|e* − e| ≥ {F.HYST} 档（{F.HYST * 25}pp）才动；"
        f"FOMC / 非农 / 核心PCE ±{F.EVENT_FREEZE} 个交易日内只许持有或减仓。",
        f"成本 = |Δ仓位| × 单边 {S.FEE:.4%} + 滑点 {S.SLIP:.2%}；空仓按货基 {S.CASH_ANNUAL:.2%} 计息；"
        f"T 日盘后算信号、T+1 收盘成交；DFII10 只用 ≤T−1 的值。",
    ]
    if years:
        note.insert(1, f"图为最近 {years:g} 年切片：仓位自更早历史延续（不在窗口首日重置），"
                       "净值按该区间重新归 1，年化 / 回撤 / 夏普只对该区间有效。")
    note.append("模拟结果，非投资建议。")
    fig.text(0.045, 0.014, "\n".join(note), fontsize=8.3, color="#5b6b7a",
             linespacing=1.5, va="bottom")

    out = os.path.join(out_dir, f"_v5_gold_score{'_3y' if years else ''}.png")
    fig.savefig(out, dpi=135, facecolor="white")
    plt.close(fig)
    print(f"图: {out}")
    print(f"  {idx[0].date()} ~ {idx[-1].date()}（{yrs:.1f} 年）· {mS['ann']:+.2%}/年 "
          f"回撤 {mS['mdd']:.2%} 夏普 {mS['sharpe']:.2f} 均仓位 {expo:.0%} 调仓 {len(ops)} 次 "
          f"| 满仓 {mBH['ann']:+.2%}/年 | 半仓 {mHF['ann']:+.2%}/年")
    return out


def main():
    ap = argparse.ArgumentParser(description="V5 黄金计分仓位净值图")
    ap.add_argument("--years", type=float, action="append",
                    help="额外出近 N 年版（默认同时出全历史 + 近三年）")
    args = ap.parse_args()
    plot()
    for y in (args.years or [3.0]):
        plot(years=y)


if __name__ == "__main__":
    main()
