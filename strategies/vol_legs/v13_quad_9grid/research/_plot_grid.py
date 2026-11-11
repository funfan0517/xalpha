# -*- coding: utf-8 -*-
"""V13 · 九宫格单腿图（价格 + 三均线 + 背景分 + 许可阶梯 + 操作点）。

版式对齐 v5 的 `research/plot_trend/_plot_trend.py`：上格价格与均线、下格仓位；
多出来的信息是**背景色带**（S≥2 绿 / S=1 灰 / S=0 红）与**兜底段**（下格浅红底）。

产物（本目录）：`_v13_grid_{leg}.png`（**只画 `rule.CORE_LEGS`**）+ `_v13_grid_heatmap.png`（许可热力图）。

2026-11-10 起执行范围收窄为中证A500 + 沪深300，图也只出这两张；
其余标的的图已删除（信号与许可仍可在日报的「诊断」区看到）。

用法：`python strategies/vol_legs/v13_quad_9grid/research/_plot_grid.py [--leg hs300]`
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
_V13 = os.path.dirname(_HERE)
if _V13 not in sys.path:
    sys.path.insert(0, _V13)

import rule           # noqa: E402
import data as vdata  # noqa: E402
import engine         # noqa: E402

C_PRICE, C_MA20, C_MA60, C_MA250 = "#1b2b3a", "#1565c0", "#ef6c00", "#6d4c41"
C_UP, C_DOWN, C_IDX = "#2e7d32", "#c62828", "#5b6b7a"


def one(leg, df, sm, ind, sc):
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    from matplotlib.lines import Line2D

    plt.rcParams["font.sans-serif"] = ["Microsoft YaHei", "SimHei", "DejaVu Sans"]
    plt.rcParams["axes.unicode_minus"] = False

    start = sm["start"]
    idx = df.index[start:]
    px = df["close"].loc[idx]
    w = sm["w"].loc[idx]
    S = sc["S"].loc[idx]
    floor = sm["diag"]["floor"].loc[idx].astype(bool)
    acts = engine.actions(w)

    fig, (ax, ax2) = plt.subplots(2, 1, figsize=(14.6, 7.6), sharex=True,
                                  gridspec_kw=dict(height_ratios=[2.5, 1.0], hspace=0.06))
    ylo, yhi = float(px.min()) * 0.96, float(px.max()) * 1.04

    ax.fill_between(idx, ylo, yhi, where=(S >= 2).to_numpy(), color=C_UP, alpha=0.07, step="mid")
    ax.fill_between(idx, ylo, yhi, where=(S == 0).to_numpy(), color=C_DOWN, alpha=0.07, step="mid")
    ax.plot(idx, px.to_numpy(), color=C_PRICE, lw=1.15, label="收盘（前复权）")
    ax.plot(idx, ind["ma20"].loc[idx].to_numpy(), color=C_MA20, lw=0.85, label="MA20")
    ax.plot(idx, ind["ma60"].loc[idx].to_numpy(), color=C_MA60, lw=0.95, label="MA60")
    ax.plot(idx, ind["ma250"].loc[idx].to_numpy(), color=C_MA250, lw=1.15, label="MA250")

    buys = [(t, float(px.loc[t])) for t, d in acts if d > 0]
    sells = [(t, float(px.loc[t])) for t, d in acts if d < 0]
    if buys:
        ax.scatter([b[0] for b in buys], [b[1] for b in buys], marker="^", s=42,
                   color=C_UP, edgecolor="white", lw=0.6, zorder=5)
    if sells:
        ax.scatter([s[0] for s in sells], [s[1] for s in sells], marker="v", s=42,
                   color=C_DOWN, edgecolor="white", lw=0.6, zorder=5)
    ax.set_ylim(ylo, yhi)
    ax.set_ylabel("净值（元）", fontsize=9.5)
    ax.grid(ls="--", lw=0.5, alpha=0.3)

    cell = sm["diag"]["cell"].iloc[-1]
    L = [Line2D([], [], color=C_PRICE, lw=1.15, label="收盘"),
         Line2D([], [], color=C_MA20, lw=0.85, label="MA20"),
         Line2D([], [], color=C_MA60, lw=0.95, label="MA60"),
         Line2D([], [], color=C_MA250, lw=1.15, label="MA250"),
         Line2D([], [], marker="^", ls="", color=C_UP, label="加仓"),
         Line2D([], [], marker="v", ls="", color=C_DOWN, label="减仓")]
    ax.legend(handles=L, fontsize=8, frameon=False, ncol=6, loc="upper left")
    ax.set_title(f"{rule.NAMES[leg]}（{rule.LEGS[leg][0]}）· 当前 {cell} · 许可 {float(w.iloc[-1]):.0%}",
                 fontsize=12.5, color="#1b2b3a", weight="bold", loc="left")

    ax2.fill_between(idx, 0, 100, where=floor.to_numpy(), color="#ef9a9a", alpha=0.28, step="mid")
    ax2.step(idx, (w * 100).to_numpy(), where="post", color=C_DOWN, lw=1.25)
    ax2.set_ylim(-3, 108)
    ax2.set_yticks([0, 20, 50, 100])
    ax2.set_ylabel("许可（%）", fontsize=9.5)
    ax2.grid(ls="--", lw=0.5, alpha=0.3)
    ax2.set_xlabel("绿底 = 背景向上（S≥2）｜红底 = 背景向下（S=0）｜下格浅红 = 兜底在途",
                   fontsize=8.6, color=C_IDX)
    for a in (ax, ax2):
        for s in ("top", "right"):
            a.spines[s].set_visible(False)

    fig.text(0.012, 0.012,
             f"V13 九宫格 · S(0~3)×力度 → 许可上限 · 兜底 {rule.DD_TRIGGER:.0%} · "
             f"迟滞 {rule.HOLD_DAYS} 日（只约束升格）· 场内 {rule.LEGS[leg][0]} 前复权 · 机械规则输出，非投资建议",
             fontsize=8.0, color=C_IDX)
    fig.subplots_adjust(left=0.055, right=0.99, top=0.93, bottom=0.115)
    out = os.path.join(_HERE, f"_v13_grid_{leg}.png")
    fig.savefig(out, dpi=125, facecolor="white")
    plt.close(fig)
    print("图:", out)


def heatmap(legs, ws):
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    plt.rcParams["font.sans-serif"] = ["Microsoft YaHei", "SimHei", "DejaVu Sans"]
    plt.rcParams["axes.unicode_minus"] = False

    M = pd.DataFrame({l: ws[l] for l in legs})
    M = M.ffill().dropna(how="all")
    fig, ax = plt.subplots(figsize=(14.6, 4.4))
    ax.imshow((M.T.to_numpy() * 100), aspect="auto", cmap="RdYlGn", vmin=0, vmax=100,
              interpolation="nearest")
    ax.set_yticks(range(len(legs)))
    ax.set_yticklabels([rule.NAMES[l] for l in legs], fontsize=9)
    step = max(len(M) // 12, 1)
    ax.set_xticks(range(0, len(M), step))
    ax.set_xticklabels([str(d.date()) for d in M.index[::step]], rotation=30, ha="right", fontsize=8)
    ax.set_title("V13 许可热力图（腿 × 时间；红 = 观察仓 20%，绿 = 满带 100%）",
                 fontsize=12, color="#1b2b3a", weight="bold", loc="left")
    sm = plt.cm.ScalarMappable(cmap="RdYlGn", norm=plt.Normalize(vmin=0, vmax=100))
    cb = fig.colorbar(sm, ax=ax, pad=0.01)
    cb.set_label("许可（%）", fontsize=8.5)
    fig.text(0.012, 0.015, "空白 = 该腿尚未预热完成（需 290 根）", fontsize=8.0, color=C_IDX)
    fig.subplots_adjust(left=0.075, right=0.985, top=0.88, bottom=0.20)
    out = os.path.join(_HERE, "_v13_grid_heatmap.png")
    fig.savefig(out, dpi=125, facecolor="white")
    plt.close(fig)
    print("图:", out)


def main():
    ap = argparse.ArgumentParser(description="V13 九宫格单腿图")
    ap.add_argument("--leg", action="append", choices=list(rule.LEGS), help="只画指定腿（可重复）")
    args = ap.parse_args()

    # 只画执行范围（CORE_LEGS）；`--leg` 可临时指定其中之一
    legs = [l for l in rule.CORE_LEGS if l in vdata.available()]
    pick = args.leg or legs
    ws = {}
    for l in legs:
        df = vdata.frame(l)
        sm = engine.state_machine(df)
        ind = engine.indicators(df)
        sc = engine.score(ind)
        ws[l] = sm["w"]
        if l in pick:
            one(l, df, sm, ind, sc)
    heatmap([l for l in legs if l in ws], ws)


if __name__ == "__main__":
    main()
