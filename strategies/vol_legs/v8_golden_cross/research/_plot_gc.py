# -*- coding: utf-8 -*-
"""V8（美股两腿金叉 50/200）· 操作点单腿图：纳指100 / 标普500。

图的读法（与 `v7_us_gate/research/_plot_gate.py` 同一版式，便于并排比较）：
- **上格深色线** = 场外 QDII 复权净值（含分红再投 / 汇率 / 跟踪误差），这是你真能拿到的收益；
- **两条橙线** = 净值口径的 MA50（细）/ MA200（粗）—— 只作视觉参照；
  **决策用的是美元指数**上的同一对均线（两者驱动同一段行情，形状一致、数值以报告为准）；
- **底色** = 当天**美元指数**的 MA50 在 MA200 上（浅绿 = 金叉状态、该满仓）/ 下（浅红 = 死叉、该清仓）；
- **▲/▼** = 每笔调仓的**成交日**（信号日收盘决策 → 次一交易日收盘成交）的净值点位；
- **下格** = 带内仓位（0% / 100% 的阶梯）。

产物（本目录）：
- `_v8_gc_ndx.png` / `_v8_gc_spx.png`

用法：
- `python _plot_gc.py`            → 两腿都画
- `python _plot_gc.py --leg ndx`  → 只画纳指

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
_V8 = os.path.dirname(_HERE)
sys.path.insert(0, _V8)

import golden_cross as gc  # noqa: E402

C_PRICE, C_FAST, C_SLOW = "#1b2b3a", "#e07b39", "#8c5a2b"
C_BUY, C_SELL, C_STEP = "#d33", "#1f8a4c", "#7b8794"
C_UP, C_DOWN = "#2e7d32", "#c62828"
C_VOL = "#8e44ad"


def plot(leg, out_dir=_HERE):
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.dates as mdates
    import matplotlib.pyplot as plt
    from matplotlib.lines import Line2D
    from matplotlib.patches import Patch
    from matplotlib.ticker import NullFormatter

    plt.rcParams["font.sans-serif"] = ["Microsoft YaHei", "SimHei", "DejaVu Sans"]
    plt.rcParams["axes.unicode_minus"] = False

    cfg = gc.rule.LEGS[leg]
    p = gc.params()
    nav, st, dec = gc.build(leg)
    sig = gc.load_signal(leg)
    f_sig, s_sig = gc.ma_pair(sig, p)
    golden = gc.golden_raw(leg, p).reindex(nav.index).ffill().fillna(1.0) > 0.5

    nv = gc.run_leg(st, nav, cost=gc.COST)
    hold = gc.run_leg(pd.Series(1.0, index=nav.index), nav, cost=gc.COST)
    pn, ph = gc.perf(nv), gc.perf(hold)
    ops = gc.actions(st)
    idx = nav.index
    yrs = (idx[-1] - idx[0]).days / 365.25

    m_fast, m_slow = gc.ma_pair(nav, p)
    lo = float(min(nav.min(), m_slow.min())) * 0.94
    hi = float(max(nav.max(), m_slow.max())) * 1.06

    fig = plt.figure(figsize=(13.2, 8.4))
    gs = fig.add_gridspec(2, 1, height_ratios=[3.4, 1.25], hspace=0.10,
                          left=0.082, right=0.935, top=0.752, bottom=0.178)
    ax = fig.add_subplot(gs[0])
    axp = fig.add_subplot(gs[1], sharex=ax)

    up = golden.to_numpy()
    ax.fill_between(idx, lo, hi, where=up, color=C_UP, alpha=0.09, lw=0, zorder=0)
    ax.fill_between(idx, lo, hi, where=~up, color=C_DOWN, alpha=0.09, lw=0, zorder=0)
    ax.plot(idx, m_fast.to_numpy(), color=C_FAST, lw=1.0, zorder=2)
    ax.plot(idx, m_slow.to_numpy(), color=C_SLOW, lw=1.3, zorder=2)
    ax.plot(idx, nav.to_numpy(), color=C_PRICE, lw=1.35, zorder=3)
    ax.set_ylim(lo, hi)
    if hi / lo > 4.0:
        ax.set_yscale("log")
        ax.set_ylim(lo, hi)
        ticks = [t for t in (0.3, 0.5, 0.8, 1, 1.5, 2, 2.5, 3, 4, 5, 6, 8, 10, 12, 15)
                 if lo <= t <= hi]
        ax.set_yticks(ticks)
        ax.set_yticklabels([f"{t:g}" for t in ticks])
        ax.yaxis.set_minor_formatter(NullFormatter())
        ax.set_ylabel("场外复权净值（对数轴）", fontsize=10)
    else:
        ax.set_ylabel("场外复权净值", fontsize=10)
    ax.grid(ls="--", lw=0.6, alpha=0.30, zorder=1)
    ax.tick_params(labelbottom=False)

    # 调仓有 130+ 次（波动层带来的微调），全画会糊：只标 |Δ仓位| ≥ 20pp 的大动作
    big = [o for o in ops if abs(o["to"] - o["frm"]) >= 0.20 - 1e-9]
    if big:
        bd = [o["date"] for o in big if o["kind"] == "buy"]
        sd = [o["date"] for o in big if o["kind"] == "sell"]
        ax.plot(bd, [float(nav.loc[d]) for d in bd], ls="none", marker="^", ms=8,
                mfc="none", mec=C_BUY, mew=1.4, zorder=6)
        ax.plot(sd, [float(nav.loc[d]) for d in sd], ls="none", marker="v", ms=8,
                mfc="none", mec=C_SELL, mew=1.4, zorder=6)

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

    axp.step(idx, st.to_numpy(), where="post", color=C_STEP, lw=1.15, zorder=3)
    axp.fill_between(idx, 0, st.to_numpy(), step="post", color=C_STEP, alpha=0.14, lw=0, zorder=2)
    axp.set_ylim(-0.02, 1.08)
    ticks = [0.0, 0.25, 0.5, 0.75, 1.0]
    axp.set_yticks(ticks)
    axp.set_yticklabels([f"{t:.0%}" for t in ticks], fontsize=8.2, color="#6b7a8c")
    axp.grid(ls="--", lw=0.55, alpha=0.30)
    axp.tick_params(axis="y", colors="#6b7a8c", length=2)

    # 右轴：vol20 与 TV 水平线 —— 解释「金叉期内仓位为什么不是 100%」
    axv = axp.twinx()
    vol = gc.vol_series(leg).reindex(idx).ffill()
    axv.plot(idx, vol.to_numpy(), color=C_VOL, lw=0.8, alpha=0.75, zorder=4)
    axv.axhline(float(p["TV"]), color=C_VOL, lw=0.9, ls="--", alpha=0.8, zorder=4)
    axv.set_ylim(0, max(float(vol.max()) * 1.08, float(p["TV"]) * 2))
    axv.set_ylabel(f"vol{int(p['VOL_WIN'])}（右轴）", fontsize=8.5, color=C_VOL)
    axv.tick_params(axis="y", colors=C_VOL, labelsize=7.8, length=2)
    axv.spines["top"].set_visible(False)
    for a in (ax, axp):
        for s in ("top", "right"):
            a.spines[s].set_visible(False)
    axp.xaxis.set_major_locator(mdates.YearLocator())
    axp.xaxis.set_major_formatter(mdates.DateFormatter("%Y"))
    axp.set_xlabel("成交日 = 信号日的次一交易日（T+1 净值）", fontsize=9, color="#5b6b7a")

    handles = [
        Line2D([], [], color=C_PRICE, lw=1.35, label=f"{cfg['name']} 复权净值"),
        Line2D([], [], color=C_FAST, lw=1.0, label=f"MA{int(p['FAST'])}（净值口径）"),
        Line2D([], [], color=C_SLOW, lw=1.3, label=f"MA{int(p['SLOW'])}（净值口径）"),
        Line2D([], [], ls="none", marker="^", ms=8, mfc="none", mec=C_BUY,
               label=f"仓位上调 ≥ 20pp ▲（{sum(1 for o in big if o['kind'] == 'buy')}）"),
        Line2D([], [], ls="none", marker="v", ms=8, mfc="none", mec=C_SELL,
               label=f"仓位下调 ≥ 20pp ▼（{sum(1 for o in big if o['kind'] == 'sell')}）"),
        Line2D([], [], color=C_STEP, lw=1.15, label="带内仓位（下格左轴）"),
        Line2D([], [], color=C_VOL, lw=0.8, label=f"vol{int(p['VOL_WIN'])}（下格右轴）"),
        Patch(facecolor=C_UP, alpha=0.30, label=f"美元指数 MA{int(p['FAST'])} > MA{int(p['SLOW'])}"),
        Patch(facecolor=C_DOWN, alpha=0.30, label=f"美元指数 MA{int(p['FAST'])} < MA{int(p['SLOW'])}"),
    ]
    ax.legend(handles=handles, loc="lower left", bbox_to_anchor=(0.0, 1.005, 1.0, 0.10),
              ncol=4, mode="expand", fontsize=8.4, frameon=False,
              columnspacing=1.0, handletextpad=0.5, borderaxespad=0.2)

    fig.text(0.045, 0.968,
             f"{cfg['name']}（{cfg['otc']}）金叉 {int(p['FAST'])}/{int(p['SLOW'])} × 波动层 "
             f"TV{float(p['TV']):.0%}｜日频",
             ha="left", fontsize=13.6, color="#1b2b3a", weight="bold")
    fig.text(0.045, 0.937,
             f"{idx[0].date()} ~ {idx[-1].date()}（{yrs:.1f} 年）· 本策略 {pn['ann']:+.2%}/年、"
             f"回撤 日频 {pn['mdd']:.2%} / 月末 {gc.mdd_sampled(nv):.2%}、夏普 {pn['sharpe']:.2f}、"
             f"平均仓位 {gc.exposure(st):.0%}、最低 {float(st.min()):.0%}、调仓 {len(ops)} 次",
             ha="left", fontsize=10, color="#3d4f5f")
    fig.text(0.045, 0.910,
             f"恒持满仓 {ph['ann']:+.2%}/年、回撤 日频 {ph['mdd']:.2%} / 月末 "
             f"{gc.mdd_sampled(hold):.2%}、夏普 {ph['sharpe']:.2f}"
             "   ｜   慢线 200 日 ⇒ 转向滞后 2~4 个月，急跌时靠波动层先降仓",
             ha="left", fontsize=9.4, color="#5b6b7a")

    note = [
        f"规则：仓位 = (MA{int(p['FAST'])} > MA{int(p['SLOW'])} ? 1 : 0) × min(1, TV/vol"
        f"{int(p['VOL_WIN'])})，TV = {float(p['TV']):.0%}，死区 {float(p['VOL_DEAD']):.0%}；"
        f"信号 = 美元指数（{cfg['signal']}）日频，成交 = 信号日的次一交易日收盘净值。",
        f"扣费按单边 {gc.COST:.1%}；空仓按货基 {gc.CASH_ANNUAL:.2%} 计息。"
        "下格两条线是联动的：底色（门）决定仓位能不能存在，vol（右轴）决定它有多高。",
        "底色 = 美元指数的均线关系（决策依据），橙线 = 净值口径的同名均线（只作视觉参照）。"
        "机械规则输出，非投资建议。",
    ]
    fig.text(0.045, 0.014, "\n".join(note), fontsize=8.4, color="#5b6b7a", linespacing=1.5,
             va="bottom")

    out = os.path.join(out_dir, f"_v8_gc_{leg}.png")
    fig.savefig(out, dpi=135, facecolor="white")
    plt.close(fig)

    print(f"图: {out}")
    print(f"  {cfg['name']} {idx[0].date()} ~ {idx[-1].date()}（{yrs:.1f} 年）· 操作 {len(ops)} 笔 · "
          f"金叉 {pn['ann']:+.2%}/年 回撤(日){pn['mdd']:.2%} 夏普 {pn['sharpe']:.2f} 仓位均值 "
          f"{gc.exposure(st):.0%} | 恒持 {ph['ann']:+.2%}/年 回撤(日){ph['mdd']:.2%}")
    return out


def main():
    ap = argparse.ArgumentParser(description="V8 美股金叉 50/200 单腿图")
    ap.add_argument("--leg", choices=list(gc.POOL), action="append",
                    help="只画指定腿（可重复）；默认两腿都画")
    args = ap.parse_args()
    for leg in (args.leg or list(gc.POOL)):
        plot(leg)


if __name__ == "__main__":
    main()
