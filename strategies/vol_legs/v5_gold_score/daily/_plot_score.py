# -*- coding: utf-8 -*-
"""V5 黄金仓位（风险预算 × 实际利率闸门）· 净值 + 操作点 + 预算 + 六档仓位图。

三格：
- 上格：策略净值（扣真实成本）vs 恒持满带 vs 恒定半仓，▲▼ 标操作点；
- 中格：120 日已实现波动（右轴）与风险预算 `scale`（左轴）+ 利率闸门 `gate`（虚线）；
- 下格：目标档 e*（虚线）与实际档 e（实线阶梯），六档 0/20/40/60/80/100%。

产物（本目录 `daily/`）：`_v5_gold_score.png`（全历史）· `_v5_gold_score_3y.png`（近三年）。

用法（从仓库根跑）：
    python strategies/vol_legs/v5_gold_score/daily/_plot_score.py

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
C_SCALE, C_VOL, C_GATE = "#2f6f9f", "#b9c4cf", "#8d6ab5"


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
    st_full, tgt_full = df["state"], df["target"]

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
    scale, gate, vol = df.loc[idx, "scale"], df.loc[idx, "gate"], df.loc[idx, "vol"]
    yrs = (idx[-1] - idx[0]).days / 365.25

    mS, mBH, mHF = S.perf(nav), S.perf(bh), S.perf(hf)
    ops = S.actions(st)
    expo = float(st.mean())

    fig = plt.figure(figsize=(13.2, 9.2))
    gs = fig.add_gridspec(3, 1, height_ratios=[3.4, 1.15, 1.05], hspace=0.10,
                          left=0.058, right=0.952, top=0.775, bottom=0.185)
    ax = fig.add_subplot(gs[0])
    axr = fig.add_subplot(gs[1], sharex=ax)
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
        for kind, mk, col in (("buy", "^", C_BUY), ("sell", "v", C_SELL)):
            pts = [o["date"] for o in show if o["kind"] == kind]
            if pts:
                ax.plot(pts, [float(nav.loc[b]) for b in pts], ls="none", marker=mk, ms=7,
                        mfc="none", mec=col, mew=1.3, zorder=6)

    # ---- 中格：风险预算 / 波动 / 闸门
    axr.fill_between(idx, 0, scale.to_numpy(), color=C_SCALE, alpha=0.13, lw=0, zorder=1)
    axr.step(idx, scale.to_numpy(), where="post", color=C_SCALE, lw=1.5, zorder=4,
             label=f"风险预算 scale（目标 {F.VOL_TARGET:.0%}）")
    axr.set_ylim(-0.02, 1.08)
    axr.set_yticks([0, 0.25, 0.5, 0.75, 1.0])
    axr.set_yticklabels(["0", "25%", "50%", "75%", "100%"], fontsize=8.2, color="#6b7a8c")
    axr.set_ylabel("风险预算", fontsize=9, color=C_SCALE)
    axr.tick_params(axis="y", colors=C_SCALE, labelsize=8.2)
    axr.grid(ls="--", lw=0.55, alpha=0.28)
    axr2 = axr.twinx()
    axr2.plot(idx, vol.to_numpy(), color=C_VOL, lw=2.6, alpha=0.55, zorder=2,
              label="120 日实现波动（右轴）")
    axr2.step(idx, gate.to_numpy(), where="post", color=C_GATE, lw=1.0, ls="--", zorder=3,
              label="利率闸门 gate（右轴 0.5/1）")
    axr2.set_ylim(-0.1, max(0.85, float(vol.max()) * 1.15))
    axr2.set_yticks([0.0, 0.25, 0.5, 0.75])
    axr2.set_yticklabels(["0%", "25%", "50%", "75%"], fontsize=8.2, color="#8a94a0")
    axr2.tick_params(axis="y", colors="#8a94a0", labelsize=8.2)
    axr2.spines["top"].set_visible(False)
    axr.tick_params(labelbottom=False)

    # ---- 下格：目标档 vs 实际档
    axp.plot(idx, tgt.to_numpy(), color=C_TGT, lw=1.0, ls="--", zorder=3, label="目标档 e*")
    axp.fill_between(idx, 0, st.to_numpy(), step="post", color=C_STEP, alpha=0.16, lw=0, zorder=1)
    axp.step(idx, st.to_numpy(), where="post", color=C_STEP, lw=1.3, zorder=4, label="实际档 e")
    axp.set_ylim(-0.03, 1.08)
    ticks = np.round(np.arange(0, 1.01, F.STEP), 6)
    axp.set_yticks(ticks)
    axp.set_yticklabels([f"{t:.0%}" for t in ticks], fontsize=8.2, color="#6b7a8c")
    axp.set_ylabel("带内仓位", fontsize=9, color="#6b7a8c")
    axp.grid(ls="--", lw=0.55, alpha=0.30)
    for a in (ax, axr, axp):
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
          Line2D([], [], ls="none", marker="s", ms=6, mfc="none", mec=C_GATE,
                 label="利率闸门（中格虚线）")]
    ax.legend(handles=hd, loc="lower left", bbox_to_anchor=(0.0, 1.005, 1.0, 0.10),
              ncol=len(hd), mode="expand", fontsize=8.2, frameon=False,
              columnspacing=1.0, handletextpad=0.5, borderaxespad=0.2)

    win_txt = f"近 {years:g} 年 · " if years else ""
    fig.text(0.045, 0.962,
             f"黄金（{S.SIGNAL_CODE}）· V5 仓位 = 风险预算 × 实际利率闸门 → 六档"
             f" · 波动目标 {F.VOL_TARGET:.0%}/{F.VOL_WINDOW}日"
             f" / 闸门 {F.RATE_LEVEL_TH:+.1f}±{F.RATE_LEVEL_BAND:.2f}pp",
             ha="left", fontsize=12.2, color="#1b2b3a", weight="bold")
    fig.text(0.045, 0.928,
             f"{idx[0].date()} ~ {idx[-1].date()}（{yrs:.1f} 年）· {win_txt}"
             f"策略 {mS['ann']:+.2%}/年、回撤 {mS['mdd']:.2%}、夏普 {mS['sharpe']:.2f}、"
             f"平均仓位 {expo:.0%}、调仓 {len(ops)} 次",
             ha="left", fontsize=9.6, color="#3d4f5f")
    fig.text(0.045, 0.902,
             f"对照：恒持满带 {mBH['ann']:+.2%}/年、回撤 {mBH['mdd']:.2%}、夏普 {mBH['sharpe']:.2f}"
             f"   |   恒定半仓 {mHF['ann']:+.2%}/年、回撤 {mHF['mdd']:.2%}、夏普 {mHF['sharpe']:.2f}",
             ha="left", fontsize=9.6, color="#5b6b7a")

    note = [
        f"规则：scale = clip({F.VOL_TARGET:.0%} / {F.VOL_WINDOW} 日实现波动, {F.VOL_MIN:.0%}, 1)；"
        f"gate = 1 if DFII10(T−1) > {F.RATE_LEVEL_TH:+.1f}%+{F.RATE_LEVEL_BAND:.2f}pp else "
        f"{F.RATE_LEVEL_LOW:g}（之间维持上一状态）；e* = 量化(scale × gate, {F.STEP:.0%})。",
        f"防抖：|e* − e| ≥ {F.HYST} 档（{F.STEP:.0%} 带内仓位）才动；"
        f"事件冻结 ±{F.EVENT_FREEZE} 日（默认关闭）；T+1 收盘成交。",
        f"成本 = |Δ仓位| × 单边 {S.FEE:.4%} + 滑点 {S.SLIP:.2%}；空仓按货基 {S.CASH_ANNUAL:.2%} 计息；"
        "这是风险归一化，不是方向预测。",
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
    ap = argparse.ArgumentParser(description="V5 黄金仓位净值图")
    ap.add_argument("--years", type=float, action="append",
                    help="额外出近 N 年版（默认同时出全历史 + 近三年）")
    args = ap.parse_args()
    plot()
    for y in (args.years or [3.0]):
        plot(years=y)


if __name__ == "__main__":
    main()
