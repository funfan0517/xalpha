# -*- coding: utf-8 -*-
"""V12 · 八态单腿图（价格 + 三均线 + 态色带 + 许可阶梯 + 操作点）。

版式对齐 v13 的 `research/_plot_grid.py`（上价格 / 中许可 / 下面板色带），多出来的一层是
**八态色带** —— 八态是这条策略唯一的信息源，必须能一眼看出「哪段是急涨、哪段是慢跌」。

产物（本目录）：
- `_v12_state_{leg}.png`  逐腿（默认只画 `rule.CORE_LEGS`；`--all` 出全 7 腿）
- `_v12_states.png`       沪深300 的**八态后验**（占比 + fwd20 + 胜率），规格 §3 的可视化版

用法：
    python strategies/vol_legs/v12_octo_trend/research/_plot_state.py            # 只画执行范围
    python strategies/vol_legs/v12_octo_trend/research/_plot_state.py --all      # 全 7 腿
    python strategies/vol_legs/v12_octo_trend/research/_plot_state.py --leg kc50
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

import rule           # noqa: E402
import data as vdata  # noqa: E402
import engine         # noqa: E402

C_PRICE, C_MA20, C_MA60, C_MA250 = "#1b2b3a", "#1565c0", "#ef6c00", "#6d4c41"
C_UP, C_DOWN, C_IDX = "#2e7d32", "#c62828", "#5b6b7a"

# 八态配色：上涨侧绿系、下跌侧红系、回调/反弹给中间色（一眼能区分"趋势"与"逆势"）
STATE_COLOR = {
    "急涨": "#1b5e20",
    "慢涨": "#81c784",
    "弱回调": "#00897b",
    "强回调": "#f9a825",
    "急跌": "#b71c1c",
    "慢跌": "#ef9a9a",
    "弱反弹": "#5c6bc0",
    "强反弹": "#26c6da",
}


def _setup():
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    plt.rcParams["font.sans-serif"] = ["Microsoft YaHei", "SimHei", "DejaVu Sans"]
    plt.rcParams["axes.unicode_minus"] = False
    return plt


def one(leg, r):
    plt = _setup()
    from matplotlib.lines import Line2D
    from matplotlib.patches import Patch

    df, ind, cx, w = r["df"], r["ind"], r["cx"], r["w"]
    start = r["start"]
    idx = df.index[df.index >= start]
    px = df["close"].loc[idx]
    w = w.loc[idx]
    st = cx["state"].loc[idx]
    reg = cx["regime_used"].loc[idx]
    acts = engine.actions(w)

    fig, (ax, ax2, ax3) = plt.subplots(
        3, 1, figsize=(14.6, 8.2), sharex=True,
        gridspec_kw=dict(height_ratios=[2.6, 0.95, 0.22], hspace=0.07))
    ylo, yhi = float(px.min()) * 0.96, float(px.max()) * 1.04

    ax.fill_between(idx, ylo, yhi, where=(reg == "向上").to_numpy(),
                    color=C_UP, alpha=0.06, step="mid")
    ax.fill_between(idx, ylo, yhi, where=(reg == "向下").to_numpy(),
                    color=C_DOWN, alpha=0.06, step="mid")
    ax.plot(idx, px.to_numpy(), color=C_PRICE, lw=1.15)
    for col, c, lw, lbl in (("ma_s", C_MA20, 0.85, f"MA{rule.MA_SHORT}"),
                            ("ma_m", C_MA60, 0.95, f"MA{rule.MA_MID}"),
                            ("ma_l", C_MA250, 1.15, f"MA{rule.MA_LONG}")):
        ax.plot(idx, ind[col].loc[idx].to_numpy(), color=c, lw=lw, label=lbl)

    buys = [(t, float(px.loc[t])) for t, d in acts if d > 0]
    sells = [(t, float(px.loc[t])) for t, d in acts if d < 0]
    if buys:
        ax.scatter([b[0] for b in buys], [b[1] for b in buys], marker="^", s=38,
                   color=C_UP, edgecolor="white", lw=0.5, zorder=5)
    if sells:
        ax.scatter([s[0] for s in sells], [s[1] for s in sells], marker="v", s=38,
                   color=C_DOWN, edgecolor="white", lw=0.5, zorder=5)
    ax.set_ylim(ylo, yhi)
    ax.set_ylabel("收盘（元）", fontsize=9.5)
    ax.grid(ls="--", lw=0.5, alpha=0.3)
    last_state = str(st.iloc[-1])
    ax.set_title(f"{rule.NAMES[leg]}（{rule.LEGS[leg][0]}）· 当前 {last_state}"
                 f"（regime {cx['regime'].iloc[-1]}）· 许可 {float(w.iloc[-1]):.0%}"
                 f" · z20 {float(cx['z20'].iloc[-1]):+.2f} · rvol {float(cx['rvol'].iloc[-1]):.2f}",
                 fontsize=12.5, color="#1b2b3a", weight="bold", loc="left")

    handles = [Line2D([], [], color=C_PRICE, lw=1.15, label="收盘"),
               Line2D([], [], color=C_MA20, lw=0.85, label=f"MA{rule.MA_SHORT}"),
               Line2D([], [], color=C_MA60, lw=0.95, label=f"MA{rule.MA_MID}"),
               Line2D([], [], color=C_MA250, lw=1.15, label=f"MA{rule.MA_LONG}"),
               Line2D([], [], marker="^", ls="", color=C_UP, label="加仓"),
               Line2D([], [], marker="v", ls="", color=C_DOWN, label="减仓")]
    ax.legend(handles=handles, fontsize=8, frameon=False, ncol=6, loc="upper left")

    # 中格：许可上限（带内比例）
    ax2.step(idx, (w * 100).to_numpy(), where="post", color=C_DOWN, lw=1.25)
    ax2.fill_between(idx, 0, (w * 100).to_numpy(), step="post", color=C_DOWN, alpha=0.10)
    ax2.set_ylim(-3, 108)
    ax2.set_yticks([0, 20, 50, 100])
    ax2.set_ylabel("许可（%）", fontsize=9.5)
    ax2.grid(ls="--", lw=0.5, alpha=0.3)

    # 下格：八态色带（按连续同态分段画 axvspan —— 比 imshow 干净，不会把日期轴变成 0~n）
    runs, cur, t0 = [], None, None
    for t, s in zip(idx, st.to_numpy()):
        if s != cur:
            if cur is not None:
                runs.append((t0, t, cur))
            cur, t0 = s, t
    if cur is not None:
        runs.append((t0, idx[-1], cur))
    for a, b, s in runs:
        if s in STATE_COLOR:
            ax3.axvspan(a, b, color=STATE_COLOR[s], lw=0)
    ax3.set_xlim(idx[0], idx[-1])
    ax3.set_yticks([])
    ax3.set_ylim(0, 1)
    ax3.set_ylabel("八态", fontsize=9)
    ax3.legend(handles=[Patch(color=STATE_COLOR[s], label=f"{s} {rule.cap_of(s):.0%}")
                        for s in rule.STATE_ORDER],
               fontsize=7.4, frameon=False, ncol=8, loc="upper center",
               bbox_to_anchor=(0.5, -0.55))
    for a in (ax, ax2, ax3):
        for s in ("top", "right"):
            a.spines[s].set_visible(False)
    ax2.set_xlabel(f"绿底 = regime 向上 ｜ 红底 = regime 向下 ｜ 下格色带 = 八态（括号是许可上限）"
                   f" ｜ 量能：{vdata.vol_note(leg)}", fontsize=8.4, color=C_IDX)

    fig.text(0.012, 0.012,
             f"V12 八态趋势 · MA{rule.MA_SHORT}/{rule.MA_MID}/{rule.MA_LONG} · z 阈值 ±{rule.Z_TH} · "
             f"量比 {rule.RVOL_HI:.2f}/{rule.RVOL_LO:.2f} · 多数决 {rule.VOTE_CONFIRM} 日 · "
             f"升格确认 {rule.UP_CONFIRM} 日 · 场内前复权 · 机械规则输出，非投资建议",
             fontsize=8.0, color=C_IDX)
    fig.subplots_adjust(left=0.055, right=0.99, top=0.93, bottom=0.135)
    out = os.path.join(_HERE, f"_v12_state_{leg}.png")
    fig.savefig(out, dpi=125, facecolor="white")
    plt.close(fig)
    print("图:", out)


def summary(leg, r):
    """八态后验图：占比（柱）+ fwd20（折线，右轴）+ 胜率标注。"""
    plt = _setup()
    ss = engine.state_stats(r["df"]["close"].loc[r["start"]:], r["cx"].loc[r["start"]:])
    by = {x["state"]: x for x in ss["rows"]}
    states = [s for s in rule.STATE_ORDER if s in by]
    shares = [by[s]["share"] * 100 for s in states]
    fwd = [by[s]["fwd"] * 100 for s in states]
    win = [by[s]["win"] * 100 for s in states]

    fig, ax = plt.subplots(figsize=(12.6, 4.8))
    x = np.arange(len(states))
    ax.bar(x, shares, color=[STATE_COLOR[s] for s in states], alpha=0.85, width=0.62,
           label="占比（左轴）")
    ax.set_ylabel("占比（%）", fontsize=9.5)
    ax.set_xticks(x)
    ax.set_xticklabels([f"{s}\n许可 {rule.cap_of(s):.0%}" for s in states], fontsize=9)
    ax.set_ylim(0, max(shares) * 1.22)
    ax.grid(axis="y", ls="--", lw=0.5, alpha=0.3)
    for s_ in ("top", "right"):
        ax.spines[s_].set_visible(False)

    ax2 = ax.twinx()
    ax2.plot(x, fwd, color="#b71c1c", marker="o", lw=1.5, label="fwd20（右轴）")
    for i, v in enumerate(fwd):
        ax2.annotate(f"{v:+.2f}%", (i, v), textcoords="offset points", xytext=(0, 9),
                     ha="center", fontsize=8.4, color="#b71c1c")
    ax2.axhline(ss["base"] * 100, color=C_IDX, ls=":", lw=1.0)
    ax2.set_ylabel("fwd20（%）", fontsize=9.5, color="#b71c1c")
    ax2.set_ylim(min(fwd + [0]) - 0.8, max(fwd) + 1.2)
    ax2.tick_params(axis="y", colors="#b71c1c", labelsize=8.5)
    for s_ in ("top",):
        ax2.spines[s_].set_visible(False)
    ax2.text(len(states) - 0.5, ss["base"] * 100, f"基准 {ss['base'] * 100:+.2f}%",
             fontsize=8.2, color=C_IDX, ha="right", va="bottom")

    ax.set_title(f"V12 八态后验 · {rule.NAMES[leg]}（{r['start'].date()} ~ {r['end'].date()}，"
                 f"{ss['days']} 天）· 柱=占比 红点=fwd20 蓝字=胜率",
                 fontsize=12, color="#1b2b3a", weight="bold", loc="left")
    for i, w_ in enumerate(win):
        ax.text(i, 0.6, f"胜 {w_:.0f}%", ha="center", fontsize=7.8, color="white")
    fig.text(0.012, 0.015,
             "用途是验口径：四组配对（急涨−慢涨 / 弱回调−强回调 / 强反弹−弱反弹 / 急跌−慢跌）"
             "必须全为正，否则先查量能口径。",
             fontsize=8.0, color=C_IDX)
    fig.subplots_adjust(left=0.06, right=0.93, top=0.88, bottom=0.20)
    out = os.path.join(_HERE, "_v12_states.png")
    fig.savefig(out, dpi=125, facecolor="white")
    plt.close(fig)
    print("图:", out)


def main():
    ap = argparse.ArgumentParser(description="V12 八态单腿图")
    ap.add_argument("--leg", action="append", choices=list(rule.LEGS), help="只画指定腿（可重复）")
    ap.add_argument("--all", action="store_true", help="画全 7 腿（默认只画执行范围）")
    args = ap.parse_args()

    avail = vdata.available()
    pool = avail if args.all else [l for l in rule.CORE_LEGS if l in avail]
    pick = args.leg or pool
    results = {}
    for leg in pick:
        r = engine.backtest_leg(leg)
        if r["start"] is None:
            print(f"  {rule.NAMES[leg]}: 预热不足，跳过")
            continue
        results[leg] = r
        one(leg, r)
    core = rule.CORE_LEGS[0] if rule.CORE_LEGS else "hs300"
    if core in results:
        summary(core, results[core])


if __name__ == "__main__":
    main()
