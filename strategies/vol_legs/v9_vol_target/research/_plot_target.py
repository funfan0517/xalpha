# -*- coding: utf-8 -*-
"""V9（美股两腿波动率目标仓位）· 单腿操作点图：纳指100 / 标普500。

图的读法：
- **上格深色线** = 场外 QDII 复权净值（含分红再投 / 汇率 / 跟踪误差），这是你真能拿到的收益；
- **底色** = 当天 vol20 高于（浅红）还是低于（浅绿）目标波动 15% —— 决策依据；
  浅红 = 该降仓的区间，浅绿 = 允许满仓的区间；
- **▲/▼** = 该笔调仓的**成交日**（月末盘后决策 → 次一交易日收盘成交）；只对
  `|Δw| ≥ 15pp` 的大动作加文字，其余用小点，否则 14 年 100 多笔会把图糊死；
- **下格** = 仓位曲线（左轴，连续 0~100%）+ vol20（右轴）+ 15% 目标波动虚线 ——
  「仓位 = 15% / vol20」这条双曲线的镜像关系一眼可见。

产物（本目录）：
- `_v9_vt_ndx.png` / `_v9_vt_spx.png`

用法：
- `python _plot_target.py`            → 两腿都画
- `python _plot_target.py --leg ndx`  → 只画纳指

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
_V9 = os.path.dirname(_HERE)
sys.path.insert(0, _V9)

import vol_target as vt  # noqa: E402

C_PRICE, C_POS, C_VOL = "#1b2b3a", "#2c6fbb", "#e07b39"
C_BUY, C_SELL = "#d33", "#1f8a4c"
C_UP, C_DOWN = "#2e7d32", "#c62828"
LABEL_MIN = 0.40          # |Δw| ≥ 40pp 的动作才加文字（14 年 200+ 笔，全标会糊）


def plot(leg, out_dir=_HERE):
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.dates as mdates
    import matplotlib.pyplot as plt
    from matplotlib.lines import Line2D
    from matplotlib.patches import Patch

    plt.rcParams["font.sans-serif"] = ["Microsoft YaHei", "SimHei", "DejaVu Sans"]
    plt.rcParams["axes.unicode_minus"] = False

    cfg = vt.rule.LEGS[leg]
    nav, st, dec = vt.build(leg)
    sig = vt.load_signal(leg)
    vol = vt.vol_series(sig).reindex(nav.index).ffill()

    nv = vt.run_leg(st, nav)
    hold = vt.run_leg(pd.Series(1.0, index=nav.index), nav)
    pn, ph = vt.perf(nv), vt.perf(hold)
    ops = vt.actions(st)
    idx = nav.index
    yrs = (idx[-1] - idx[0]).days / 365.25

    cal = (vol > vt.TV).astype("float64").reindex(idx).ffill().fillna(0.0) > 0.5
    lo = float(nav.min()) * 0.94
    hi = float(nav.max()) * 1.06

    fig = plt.figure(figsize=(13.2, 8.8))
    gs = fig.add_gridspec(2, 1, height_ratios=[3.6, 1.4], hspace=0.08,
                          left=0.06, right=0.925, top=0.795, bottom=0.165)
    ax = fig.add_subplot(gs[0])
    axp = fig.add_subplot(gs[1], sharex=ax)

    hot = cal.to_numpy()
    ax.fill_between(idx, lo, hi, where=hot, color=C_DOWN, alpha=0.085, lw=0, zorder=0)
    ax.fill_between(idx, lo, hi, where=~hot, color=C_UP, alpha=0.075, lw=0, zorder=0)
    ax.plot(idx, nav.to_numpy(), color=C_PRICE, lw=1.3, zorder=3)
    ax.set_ylim(lo, hi)
    if hi / lo > 4.0:
        ax.set_yscale("log")
        ax.set_ylim(lo, hi)
        ticks = [t for t in (0.1, 0.15, 0.2, 0.25, 0.3, 0.4, 0.5, 0.6, 0.8, 1.0, 1.2, 1.5,
                             2, 2.5, 3, 4, 5, 6, 8, 10, 12, 15, 20) if lo <= t <= hi]
        ax.set_yticks(ticks)
        ax.set_yticklabels([f"{t:g}" for t in ticks])
        ax.set_ylabel("场外复权净值（对数轴）", fontsize=10)
    else:
        ax.set_ylabel("场外复权净值", fontsize=10)
    ax.grid(ls="--", lw=0.6, alpha=0.28, zorder=1)
    ax.tick_params(labelbottom=False)

    if ops:
        big = [o for o in ops if abs(o["to"] - o["frm"]) >= LABEL_MIN]
        bd = [o["date"] for o in ops if o["kind"] == "buy"]
        sd = [o["date"] for o in ops if o["kind"] == "sell"]
        ax.plot(bd, [float(nav.loc[d]) for d in bd], ls="none", marker="^", ms=3.6,
                mfc="none", mec=C_BUY, mew=0.8, alpha=0.75, zorder=5)
        ax.plot(sd, [float(nav.loc[d]) for d in sd], ls="none", marker="v", ms=3.6,
                mfc="none", mec=C_SELL, mew=0.8, alpha=0.75, zorder=5)
        for o in big:
            up_o = o["kind"] == "buy"
            ax.plot([o["date"]], [float(nav.loc[o["date"]])], ls="none", marker="^" if up_o else "v",
                    ms=7.0, mfc="none", mec=C_BUY if up_o else C_SELL, mew=1.3, zorder=6)
            ax.annotate(f"{o['to']:.0%}", (o["date"], float(nav.loc[o["date"]])),
                        textcoords="offset points", xytext=(0, 9 if up_o else -15),
                        ha="center", fontsize=6.8,
                        color=C_BUY if up_o else C_SELL, zorder=7)

    cut = None
    if cfg.get("otc_proxy"):
        _cut = pd.Timestamp(cfg.get("otc_start"))
        if idx[0] < _cut < idx[-1]:
            cut = idx[idx.searchsorted(_cut)]
            ax.axvspan(idx[0], cut, color="#9aa5b1", alpha=0.10, lw=0, zorder=1)
            ax.axvline(cut, color="#7b8794", lw=0.9, ls="--", zorder=2)
            ax.text(cut, 0.975, f"{cut.date()} 起为 {cfg['otc']} 真实净值"
                                f"（此前 {cfg.get('otc_proxy')} 桥接） ",
                    transform=ax.get_xaxis_transform(), va="top", ha="right",
                    fontsize=8.3, color="#5b6b7a")

    # ---- 下格：仓位（左轴）+ vol20（右轴）
    axp.plot(idx, st.to_numpy() * 100.0, color=C_POS, lw=1.3, zorder=4)
    axp.fill_between(idx, 0, st.to_numpy() * 100.0, color=C_POS, alpha=0.12, lw=0, zorder=1)
    axp.set_ylim(-4, 108)
    axp.set_yticks([0, 25, 50, 75, 100])
    axp.set_yticklabels([f"{t}%" for t in (0, 25, 50, 75, 100)],
                        fontsize=8.2, color=C_POS)
    axp.set_ylabel("带内仓位", fontsize=9, color=C_POS)

    axv = axp.twinx()
    axv.plot(idx, vol.to_numpy() * 100.0, color=C_VOL, lw=1.0, alpha=0.85, zorder=3)
    axv.axhline(vt.TV * 100.0, color=C_VOL, lw=1.0, ls="--", alpha=0.75, zorder=2)
    axv.set_ylim(0, max(70.0, float(vol.max() * 100.0 * 1.05)))
    axv.set_ylabel(f"vol{vt.VOL_WIN}（年化）", fontsize=9, color=C_VOL)
    axv.tick_params(axis="y", colors=C_VOL, labelsize=8.2, length=2)
    axv.text(idx[-1], vt.TV * 100.0, f" 目标 {vt.TV:.0%}", va="center", ha="left",
             fontsize=8.0, color=C_VOL)

    axp.grid(ls="--", lw=0.55, alpha=0.28)
    axp.tick_params(axis="y", length=2)
    for a in (ax, axp):
        for s in ("top", "right"):
            a.spines[s].set_visible(False)
    axp.xaxis.set_major_locator(mdates.YearLocator())
    axp.xaxis.set_major_formatter(mdates.DateFormatter("%Y"))
    axp.set_xlabel("成交日 = 决策日的次一交易日（T+1 收盘；新权重 t+2 起生效）",
                   fontsize=9, color="#5b6b7a")

    handles = [
        Line2D([], [], color=C_PRICE, lw=1.3, label=f"{cfg['name']} 复权净值"),
        Line2D([], [], color=C_POS, lw=1.3, label="带内仓位（下格左轴）"),
        Line2D([], [], color=C_VOL, lw=1.0, label=f"vol{vt.VOL_WIN} 年化波动（下格右轴）"),
        Line2D([], [], color=C_VOL, lw=1.0, ls="--", label=f"目标波动 {vt.TV:.0%}"),
        Line2D([], [], ls="none", marker="^", ms=6, mfc="none", mec=C_BUY,
               label=f"加仓 ▲（{sum(1 for o in ops if o['kind'] == 'buy')}）"),
        Line2D([], [], ls="none", marker="v", ms=6, mfc="none", mec=C_SELL,
               label=f"减仓 ▼（{sum(1 for o in ops if o['kind'] == 'sell')}）"),
        Patch(facecolor=C_UP, alpha=0.30, label=f"vol{vt.VOL_WIN} < {vt.TV:.0%}（允许满仓）"),
        Patch(facecolor=C_DOWN, alpha=0.30, label=f"vol{vt.VOL_WIN} > {vt.TV:.0%}（该降仓）"),
    ]
    ax.legend(handles=handles, loc="lower left", bbox_to_anchor=(0.0, 1.005, 1.0, 0.10),
              ncol=4, mode="expand", fontsize=8.4, frameon=False,
              columnspacing=1.0, handletextpad=0.5, borderaxespad=0.2)

    fig.text(0.045, 0.972,
             f"{cfg['name']}（{cfg['otc']}）波动率目标仓位 · vol{vt.VOL_WIN} × TV {vt.TV:.0%}"
             f" · 月末定仓 + 月中快减/快补（真实操作点）",
             ha="left", fontsize=13.2, color="#1b2b3a", weight="bold")
    fig.text(0.045, 0.941,
             f"{idx[0].date()} ~ {idx[-1].date()}（{yrs:.1f} 年）· 本变种 {pn['ann']:+.2%}/年、"
             f"回撤 {pn['mdd']:.2%}、夏普 {pn['sharpe']:.2f}、平均仓位 {vt.exposure(st):.0%}、"
             f"调仓 {len(ops)} 笔（{len(ops) / yrs:.1f}/年）",
             ha="left", fontsize=10, color="#3d4f5f")
    fig.text(0.045, 0.914,
             f"恒持满仓 {ph['ann']:+.2%}/年、回撤 {ph['mdd']:.2%}、夏普 {ph['sharpe']:.2f}"
             f"   ｜   仓位 = min({vt.CAP:.0f}, {vt.TV:.0%} / vol{vt.VOL_WIN})，"
             f"与当前仓位差 < {vt.DRIFT:.0%} 不交易",
             ha="left", fontsize=9.4, color="#5b6b7a")

    note = [
        f"规则：月末收盘算 vol{vt.VOL_WIN}（近 {vt.VOL_WIN} 日收益的年化已实现波动，用美元指数 "
        f"{cfg['signal']}）→ 目标仓位 `w = min(1, {vt.TV:.0%}/vol{vt.VOL_WIN})`；次一交易日收盘成交，"
        "新权重自 t+2 起承担收益。",
        f"月中补丁：**快减** = 5 日跌幅 ≤ {vt.CUT_RET:.0%} 且 vol{vt.VOL_WIN} ≥ {vt.CUT_VOLX:.1f}×TV；"
        f"**快补** = vol{vt.VOL_WIN} ≤ {vt.DECAY:.0%}×近 {vt.PEAK_WIN} 日峰值 且 站上 MA{vt.ADD_MA}。"
        "两者都只用波动率自己的信息，不引入方向判断。",
        f"小字标注只给 |Δ仓位| ≥ {LABEL_MIN:.0%} 的大动作，其余为小三角（14 年 200+ 笔，全标会糊）；"
        f"未投部分按 {vt.CASH_ANNUAL:.2%} 计息、单边成本 {vt.COST:.2%}。"
        "不预测方向，只调风险预算；治慢熊，急跌只能减轻。机械规则输出，非投资建议。",
    ]
    fig.text(0.045, 0.014, "\n".join(note), fontsize=8.4, color="#5b6b7a", linespacing=1.5,
             va="bottom")

    out = os.path.join(out_dir, f"_v9_vt_{leg}.png")
    fig.savefig(out, dpi=135, facecolor="white")
    plt.close(fig)

    print(f"图: {out}")
    print(f"  {cfg['name']} {idx[0].date()} ~ {idx[-1].date()}（{yrs:.1f} 年）· "
          f"调仓 {len(ops)} 笔 · 本变种 {pn['ann']:+.2%}/年 回撤 {pn['mdd']:.2%} "
          f"夏普 {pn['sharpe']:.2f} 仓位均值 {vt.exposure(st):.0%} | "
          f"恒持 {ph['ann']:+.2%}/年 回撤 {ph['mdd']:.2%} 夏普 {ph['sharpe']:.2f}")
    return out


def main():
    ap = argparse.ArgumentParser(description="V9 美股波动率目标仓位单腿图")
    ap.add_argument("--leg", choices=list(vt.POOL), action="append",
                    help="只画指定腿（可重复）；默认两腿都画")
    args = ap.parse_args()
    for leg in (args.leg or list(vt.POOL)):
        plot(leg)


if __name__ == "__main__":
    main()
