# -*- coding: utf-8 -*-
"""V10（美股两腿纯波动层）· 单腿图：纳指100 / 标普500。

图的读法（与 `v8_golden_cross/research/_plot_gc.py` 同一版式，便于并排比较）：
- **上格深色线** = 场外 QDII 复权净值（含分红再投 / 汇率 / 跟踪误差），这是你真能拿到的收益；
- **上格浅色虚线** = 恒持满仓的净值，用来直观看「降仓有没有帮上忙」；
- **下格实线** = 带内仓位（0~100% 的阶梯，由 `min(1, TV/vol20)` 决定）；
- **下格右轴虚线** = `vol20`，配水平虚线 `TV = 20%`：**vol 线在 TV 之上 → 仓位 < 100%**，
  两者的镜像关系就是这条策略的全部内容；
- **▲/▼** = 单次仓位变动 ≥ 20pp 的大动作（小动作太多，全画会糊）。

产物（本目录）：
- `_v10_vl_ndx.png` / `_v10_vl_spx.png`

用法：
- `python _plot_vl.py`            → 两腿都画
- `python _plot_vl.py --leg ndx`  → 只画纳指

机械规则输出，非投资建议。
"""
import argparse
import os
import sys

import pandas as pd

try:
    sys.stdout.reconfigure(encoding="utf-8")  # type: ignore[attr-defined]
except Exception:  # noqa: BLE001
    pass

_HERE = os.path.dirname(os.path.abspath(__file__))
_V10 = os.path.dirname(_HERE)
sys.path.insert(0, _V10)

import vol_layer as vl  # noqa: E402

C_PRICE, C_BH, C_STEP, C_VOL = "#1b2b3a", "#9aa5b1", "#1f77b4", "#d62728"
C_UP, C_DOWN = "#d33", "#1f8a4c"
BIG = 0.20          # 只标注 |Δ仓位| ≥ 20pp 的动作


def plot(leg, out_dir=_HERE):
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.dates as mdates
    import matplotlib.pyplot as plt
    import matplotlib.ticker as mticker
    from matplotlib.lines import Line2D

    plt.rcParams["font.sans-serif"] = ["Microsoft YaHei", "SimHei", "DejaVu Sans"]
    plt.rcParams["axes.unicode_minus"] = False

    cfg = vl.rule.LEGS[leg]
    p = vl.params()
    nav, st, dec = vl.build(leg)
    vol = vl.vol_series(leg).reindex(nav.index).ffill()

    nv = vl.run_leg(st, nav, cost=vl.COST)
    hold = vl.run_leg(pd.Series(1.0, index=nav.index), nav, cost=vl.COST)
    pn, ph = vl.perf(nv), vl.perf(hold)
    ops = [o for o in vl.actions(st) if abs(o["to"] - o["frm"]) >= BIG - 1e-9]
    idx = nav.index
    yrs = (idx[-1] - idx[0]).days / 365.25

    lo = float(min(nav.min(), hold.min())) * 0.94
    hi = float(max(nav.max(), hold.max())) * 1.06

    fig = plt.figure(figsize=(13.2, 8.4))
    gs = fig.add_gridspec(2, 1, height_ratios=[3.2, 1.5], hspace=0.10,
                          left=0.082, right=0.915, top=0.780, bottom=0.172)
    ax = fig.add_subplot(gs[0])
    axp = fig.add_subplot(gs[1], sharex=ax)

    ax.plot(idx, hold.to_numpy(), color=C_BH, lw=1.0, ls="--", zorder=2)
    ax.plot(idx, nav.to_numpy(), color=C_PRICE, lw=1.35, zorder=3)
    ax.set_ylim(lo, hi)
    if hi / lo > 4.0:
        ax.set_yscale("log")
        ax.set_ylim(lo, hi)
        ax.yaxis.set_major_locator(mticker.LogLocator(
            base=10.0, subs=(1.0, 1.5, 2.0, 3.0, 4.0, 5.0, 6.0, 8.0)))
        ax.yaxis.set_major_formatter(mticker.FuncFormatter(lambda v, _p: f"{v:g}"))
        ax.yaxis.set_minor_formatter(mticker.NullFormatter())
        ax.set_ylabel("场外复权净值（对数轴）", fontsize=10)
    else:
        ax.set_ylabel("场外复权净值", fontsize=10)
    ax.grid(ls="--", lw=0.6, alpha=0.30, zorder=1)
    ax.tick_params(labelbottom=False)

    if ops:
        bd = [o["date"] for o in ops if o["kind"] == "buy"]
        sd = [o["date"] for o in ops if o["kind"] == "sell"]
        ax.plot(bd, [float(nav.loc[d]) for d in bd], ls="none", marker="^", ms=7,
                mfc="none", mec=C_UP, mew=1.3, zorder=6)
        ax.plot(sd, [float(nav.loc[d]) for d in sd], ls="none", marker="v", ms=7,
                mfc="none", mec=C_DOWN, mew=1.3, zorder=6)

    # 下格：仓位（左轴）+ vol20（右轴）
    axp.step(idx, st.to_numpy(), where="post", color=C_STEP, lw=1.3, zorder=3)
    axp.fill_between(idx, 0, st.to_numpy(), step="post", color=C_STEP, alpha=0.16, lw=0, zorder=2)
    axp.set_ylim(0, 1.08)
    axp.set_yticks([0, 0.25, 0.5, 0.75, 1.0])
    axp.set_yticklabels(["0%", "25%", "50%", "75%", "100%"], fontsize=8.2, color=C_STEP)
    axp.set_ylabel("带内仓位", fontsize=9, color=C_STEP)
    axp.grid(ls="--", lw=0.55, alpha=0.30)

    axv = axp.twinx()
    axv.plot(idx, vol.to_numpy(), color=C_VOL, lw=0.9, alpha=0.85, zorder=4)
    axv.axhline(vl.TV, color=C_VOL, lw=0.9, ls="--", alpha=0.8, zorder=4)
    axv.set_ylim(0, max(float(vol.max()) * 1.08, vl.TV * 2))
    axv.set_yticks([0, vl.TV, 0.4, 0.6, 0.8])
    axv.set_yticklabels(["0%", f"TV {vl.TV:.0%}", "40%", "60%", "80%"], fontsize=8.2, color=C_VOL)
    axv.set_ylabel(f"vol{vl.VOL_WIN}（年化，右轴）", fontsize=9, color=C_VOL)
    for a in (ax, axp):
        for s in ("top", "right"):
            a.spines[s].set_visible(False)
    axv.spines["top"].set_visible(False)
    axp.xaxis.set_major_locator(mdates.YearLocator())
    axp.xaxis.set_major_formatter(mdates.DateFormatter("%Y"))
    axp.set_xlabel("成交日 = 信号日的次一交易日（T+1 净值）", fontsize=9, color="#5b6b7a")

    handles = [
        Line2D([], [], color=C_PRICE, lw=1.35, label=f"{cfg['name']} 复权净值（本策略仓位）"),
        Line2D([], [], color=C_BH, lw=1.0, ls="--", label="恒持满仓（对照）"),
        Line2D([], [], ls="none", marker="^", ms=7, mfc="none", mec=C_UP,
               label=f"仓位上调 ≥ {BIG:.0%} ▲（{sum(1 for o in ops if o['kind'] == 'buy')}）"),
        Line2D([], [], ls="none", marker="v", ms=7, mfc="none", mec=C_DOWN,
               label=f"仓位下调 ≥ {BIG:.0%} ▼（{sum(1 for o in ops if o['kind'] == 'sell')}）"),
        Line2D([], [], color=C_STEP, lw=1.3, label=f"带内仓位（下格左轴）"),
        Line2D([], [], color=C_VOL, lw=0.9, label=f"vol{vl.VOL_WIN}（下格右轴）"),
    ]
    ax.legend(handles=handles, loc="lower left", bbox_to_anchor=(0.0, 1.005, 1.0, 0.09),
              ncol=3, mode="expand", fontsize=8.4, frameon=False,
              columnspacing=1.0, handletextpad=0.5, borderaxespad=0.2)

    fig.text(0.045, 0.968,
             f"{cfg['name']}（{cfg['otc']}）纯波动层 TV{vl.TV:.0%} · 日频 · 无方向判断",
             ha="left", fontsize=13.6, color="#1b2b3a", weight="bold")
    fig.text(0.045, 0.937,
             f"{idx[0].date()} ~ {idx[-1].date()}（{yrs:.1f} 年）· 本策略 {pn['ann']:+.2%}/年、"
             f"回撤 日频 {pn['mdd']:.2%} / 月末 {vl.mdd_sampled(nv):.2%}、夏普 {pn['sharpe']:.2f}、"
             f"平均仓位 {vl.exposure(st):.0%}、最低 {float(st.min()):.0%}、调仓 {vl.n_trades(st)} 次",
             ha="left", fontsize=10, color="#3d4f5f")
    fig.text(0.045, 0.910,
             f"恒持满仓 {ph['ann']:+.2%}/年、回撤 日频 {ph['mdd']:.2%} / 月末 "
             f"{vl.mdd_sampled(hold):.2%}、夏普 {ph['sharpe']:.2f}"
             f"   ｜   仓位 = min(1, TV/vol)：波动高就减、波动低就加，不看方向",
             ha="left", fontsize=9.4, color="#5b6b7a")

    note = [
        f"规则：仓位 = min(1, {vl.TV:.0%} / vol{vl.VOL_WIN})，无方向判断；死区 {vl.DEAD:.0%}；"
        f"信号 = 美元指数（{cfg['signal']}）日频，成交 = 信号日的次一交易日收盘净值。",
        f"扣费按单边 {vl.COST:.1%}；空仓按货基 {vl.CASH_ANNUAL:.2%} 计息。"
        "下格两条线是镜像的：vol 在 TV 虚线之上 → 仓位必然低于 100%。",
        "它没有方向保护：阴跌 + 低波动的行情里仓位仍会回到满仓（本样本不含 2000-2002）。"
        "机械规则输出，非投资建议。",
    ]
    fig.text(0.045, 0.012, "\n".join(note), fontsize=8.4, color="#5b6b7a", linespacing=1.5,
             va="bottom")

    out = os.path.join(out_dir, f"_v10_vl_{leg}.png")
    fig.savefig(out, dpi=135, facecolor="white")
    plt.close(fig)

    print(f"图: {out}")
    print(f"  {cfg['name']} {idx[0].date()} ~ {idx[-1].date()}（{yrs:.1f} 年）· 调仓 "
          f"{vl.n_trades(st)} 次 · {pn['ann']:+.2%}/年 回撤(日){pn['mdd']:.2%} 夏普 {pn['sharpe']:.2f} "
          f"仓位均值 {vl.exposure(st):.0%} 最低 {float(st.min()):.0%} | "
          f"恒持 {ph['ann']:+.2%}/年 回撤(日){ph['mdd']:.2%}")
    return out


def main():
    ap = argparse.ArgumentParser(description="V10 纯波动层单腿图")
    ap.add_argument("--leg", choices=list(vl.POOL), action="append",
                    help="只画指定腿（可重复）；默认两腿都画")
    args = ap.parse_args()
    for leg in (args.leg or list(vl.POOL)):
        plot(leg)


if __name__ == "__main__":
    main()
