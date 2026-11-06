# -*- coding: utf-8 -*-
"""V6 估值分位仓位带 · 单标的图（版式对齐 `v5_trend_gate/research/_plot_trend.py`）。

三格，从上到下：

1. **净值**（对数轴）：策略净值 vs 恒持满仓，操作点标在恒持曲线上（▼ 降带 / ▲ 阶梯加仓）；
2. **PE 分位**：0–100%，五档底色 + 20/40/60/80 阈值；A500 档额外叠一条
   **沪深300 分位**（文档 §6-1 的第二把尺子，两者不一致时取低档）；
3. **仓位**：虚线 = 目标带（定档日更新），实线 = 实际仓位（阶梯 + RSI 扳机爬出来的）。

产出（相对本文件）：`research/_v6_pe_band_{hs300,a500}.png`。
用法：`python research/_plot_pe_band.py`（两个标的都画）或 `--univ a500`。
模拟结果，非投资建议。
"""
import argparse
import os
import sys

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402
import numpy as np  # noqa: E402
import pandas as pd  # noqa: E402
from matplotlib.lines import Line2D  # noqa: E402
from matplotlib.patches import Patch  # noqa: E402
from matplotlib.ticker import FuncFormatter, NullFormatter  # noqa: E402

try:
    sys.stdout.reconfigure(encoding="utf-8")  # type: ignore[attr-defined]
except Exception:  # noqa: BLE001
    pass

_HERE = os.path.dirname(os.path.abspath(__file__))          # .../v6_pe_band/research
_V6 = os.path.dirname(_HERE)                                # .../v6_pe_band
if _V6 not in sys.path:
    sys.path.insert(0, _V6)

import backtest as bt   # noqa: E402  复用它的 run_mode（回测口径唯一入口）
import band             # noqa: E402

# ---- 配色：与 v5 的操作点图同一族
C_NAV, C_BH, C_PCT, C_ALT = "#1b2b3a", "#9aa7b4", "#2a6f97", "#b07aa1"
C_BUY, C_SELL, C_STEP = "#d33", "#1f8a4c", "#7b8794"

# 五档底色（便宜 → 贵）
BAND_BG = ["#e8f3ea", "#f1f7ee", "#fbf8ec", "#fbeee6", "#f7e4e4"]

FREQ_TXT = {"weekly": "每周定档", "monthly": "每月定档", "quarterly": "每季定档"}


def yrs_first(idx):
    """样本长度（年）：样本太短时不套对数轴、统计也不当结论。"""
    return (idx[-1] - idx[0]).days / 365.25


def _weights():
    return [float(x) for x in band.CFG["BAND_WEIGHTS"]]      # [1.0, .7, .5, .3, .1]


def _edges():
    return [float(x) for x in band.CFG["BAND_EDGES"]]        # [.2, .4, .6, .8]


def plot(univ, out_dir):
    p = band.params(UNIVERSE=univ)
    r = bt.run_mode(univ, p)
    ind, sim = r["ind"], r["sim"]
    idx = ind.index
    nav, bh = sim["nav"], sim["bh"]
    w = sim["w"]
    target = pd.to_numeric(pd.Series(sim["bands"]["target"], index=idx), errors="coerce")
    pct = ind["pct"]
    pct_h = ind["pct_h"] if "pct_h" in ind else pd.Series(np.nan, index=idx)
    u = band.universe_info(univ, p)
    ws, edges = _weights(), _edges()

    plt.rcParams["font.sans-serif"] = ["Microsoft YaHei", "SimHei", "DejaVu Sans"]
    plt.rcParams["axes.unicode_minus"] = False

    fig = plt.figure(figsize=(13.2, 8.6), dpi=150)
    gs = fig.add_gridspec(3, 1, height_ratios=[3.05, 1.15, 1.05], hspace=0.09,
                          left=0.075, right=0.975, top=0.845, bottom=0.185)
    ax = fig.add_subplot(gs[0])
    axq = fig.add_subplot(gs[1], sharex=ax)
    axw = fig.add_subplot(gs[2], sharex=ax)

    # ----------------------------------------------------------- 1. 净值
    ax.plot(idx, bh, color=C_BH, lw=1.0, ls="--", label="恒持满仓", zorder=2)
    ax.plot(idx, nav, color=C_NAV, lw=1.45, label="策略净值", zorder=3)
    logy = yrs_first(idx) >= 5.0          # 样本短时不套对数轴，否则波动被放大
    if logy:
        ax.set_yscale("log")
        lo, hi = float(min(bh.min(), nav.min())), float(max(bh.max(), nav.max()))
        ticks = [t for t in (0.7, 0.8, 1.0, 1.2, 1.5, 2.0, 2.5, 3.0, 4.0, 5.0, 7.0)
                 if lo * 0.92 <= t <= hi * 1.08]
        ax.set_yticks(ticks)
        ax.set_yticklabels([f"{t:g}" for t in ticks])
        ax.yaxis.set_minor_formatter(NullFormatter())
        ax.set_ylabel("累计净值（对数轴）", fontsize=10)
    else:
        ax.set_ylabel("累计净值", fontsize=10)
    ax.grid(alpha=0.16, lw=0.6)
    ax.tick_params(labelbottom=False)
    for s in ("top", "right"):
        ax.spines[s].set_visible(False)

    # 操作点（成交日）
    for t in sim["trades"]:
        d = pd.Timestamp(t["exec_date"])
        if d not in bh.index:
            continue
        y = float(bh.loc[d])
        up = t["side"] == "buy"
        ax.scatter([d], [y], marker="^" if up else "v", s=22 if up else 26,
                   color=C_BUY if up else C_SELL, zorder=5, linewidths=0)
    n_buy = sum(1 for t in sim["trades"] if t["side"] == "buy")
    n_sell = sum(1 for t in sim["trades"] if t["side"] == "sell")

    # ----------------------------------------------------------- 2. PE 分位
    for i, wp in enumerate(ws):
        lo = 0.0 if i == 0 else edges[i - 1]
        hi = 1.0 if i == len(ws) - 1 else edges[i]
        axq.axhspan(lo, hi, color=BAND_BG[i], zorder=0)
    for e in edges:
        axq.axhline(e, color="#c9d2da", lw=0.7, ls=(0, (4, 3)), zorder=1)
    axq.plot(idx, pct, color=C_PCT, lw=1.25, zorder=3, label="PE 分位（本标的）")
    # 分位窗口首次填满（此前 n_eff < WINDOW_DAYS，早期分位会剧烈抖动）
    if "n_eff" in ind:
        full = ind.index[ind["n_eff"] >= int(p["WINDOW_DAYS"])]
        if len(full) and full[0] > idx[0]:
            axq.axvline(full[0], color="#b0bcc8", lw=0.9, ls=(0, (2, 3)), zorder=1)
            axq.annotate("分位窗口填满", xy=(full[0], 0.04), fontsize=7.0, color="#8d99a6",
                         ha="left", va="bottom", xytext=(4, 0), textcoords="offset points")
    if pct_h.notna().any():
        axq.plot(idx, pct_h, color=C_ALT, lw=0.95, ls="-.", zorder=2,
                 label=f"PE 分位（{band.UNIVERSES[u['cross']]['name']}）")
    axq.set_ylim(0, 1)
    axq.set_yticks([0, 0.2, 0.4, 0.6, 0.8, 1.0])
    axq.set_yticklabels(["0%", "20%", "40%", "60%", "80%", "100%"], fontsize=8.2,
                        color="#6b7a8c")
    axq.set_ylabel("PE 分位", fontsize=9, color="#6b7a8c")
    axq.grid(axis="x", alpha=0.0)
    axq.tick_params(labelbottom=False)
    for s in ("top", "right"):
        axq.spines[s].set_visible(False)

    # ----------------------------------------------------------- 3. 仓位阶梯
    axw.step(idx, target.to_numpy(dtype=float), where="post", color=C_STEP, lw=0.95,
             ls="--", zorder=2, label="目标带（定档日更新）")
    axw.step(idx, w.to_numpy(dtype=float), where="post", color=C_NAV, lw=1.35, zorder=3,
             label="实际仓位（阶梯 + RSI 扳机）")
    axw.fill_between(idx, 0, w.to_numpy(dtype=float), step="post", color=C_NAV,
                     alpha=0.10, lw=0, zorder=1)
    axw.set_ylim(-0.02, 1.06)
    axw.set_yticks(sorted(ws))
    axw.set_yticklabels([f"{x:.0%}" for x in sorted(ws)], fontsize=8.2, color="#6b7a8c")
    axw.set_ylabel("仓位", fontsize=9, color="#6b7a8c")
    axw.grid(alpha=0.16, lw=0.6)
    for s in ("top", "right"):
        axw.spines[s].set_visible(False)
    axw.set_xlabel("定档日 = 周期最后一个交易日；成交 = 信号次日（T+1）", fontsize=9,
                   color="#5b6b7a")

    # ----------------------------------------------------------- 标题与统计
    yrs = (idx[-1] - idx[0]).days / 365.25
    m, mbh = sim["m"], sim["mbh"]
    freq = FREQ_TXT.get(str(p["REBAL_FREQ"]), str(p["REBAL_FREQ"]))
    cross = (f" · 交叉校验 {band.UNIVERSES[u['cross']]['name']}"
             if pct_h.notna().any() else "")
    fig.text(0.045, 0.972,
             f"{u['name']}（{u['code']}）估值分位仓位带 · {freq}{cross}",
             ha="left", fontsize=13.2, color="#1b2b3a", weight="bold")
    fig.text(0.045, 0.938,
             f"{idx[0].date()} ~ {idx[-1].date()}（{yrs:.1f} 年）· 策略 {m['ann']:+.2%}/年、"
             f"回撤 {m['mdd']:.2%}、夏普 {m['sharpe']:.2f}、平均仓位 {sim['exposure']:.0%}、"
             f"换手 {sim['turnover'] / max(yrs, 1e-9):.1f}×/年"
             f"   |   恒持满仓 {mbh['ann']:+.2%}/年、回撤 {mbh['mdd']:.2%}、夏普 {mbh['sharpe']:.2f}",
             ha="left", fontsize=9.6, color="#3d4f5f")

    handles = [
        Line2D([], [], color=C_NAV, lw=1.45, label="策略净值"),
        Line2D([], [], color=C_BH, lw=1.0, ls="--", label="恒持满仓"),
        Line2D([], [], color=C_BUY, marker="^", ls="", ms=6, label=f"加仓（{n_buy} 次）"),
        Line2D([], [], color=C_SELL, marker="v", ls="", ms=6, label=f"减仓（{n_sell} 次）"),
        Line2D([], [], color=C_PCT, lw=1.25, label="PE 分位（本标的）"),
        Line2D([], [], color=C_STEP, lw=0.95, ls="--", label="目标带（定档）"),
        Line2D([], [], color=C_NAV, lw=1.35, label="实际仓位"),
        Patch(facecolor=BAND_BG[0], label="便宜档底色"),
        Patch(facecolor=BAND_BG[-1], label="高估档底色"),
    ]
    if pct_h.notna().any():
        handles.insert(5, Line2D([], [], color=C_ALT, lw=0.95, ls="-.",
                                 label="PE 分位（第二把尺子）"))
    ax.legend(handles=handles, loc="lower left", bbox_to_anchor=(0.0, 1.005, 1.0, 0.085),
              ncol=5, mode="expand", fontsize=8.2, frameon=False,
              columnspacing=1.0, handletextpad=0.45, borderaxespad=0.2)

    # ----------------------------------------------------------- 脚注
    note = [
        f"分位 = 当前 PE 在近 {p['WINDOW_YEARS']} 年自身历史中的位置（含当期、≤ 比较，无前视）；"
        f"最小样本 {p['MIN_OBS']} 个交易日，不足则不出信号。",
        "仓位档：" + " / ".join(f"{e:.0%}→{w0:.0%}" for e, w0 in
                              [(0, ws[0])] + list(zip(edges, ws[1:])))
        + f"；分位 ≥{edges[-1]:.0%} 需连续 {p['SELL_CONFIRM_MONTHS']} 个月才降到 {ws[-1]:.0%}。",
        f"加仓只来自阶梯（回撤 {p['LADDER_DD']} 触发 {p['LADDER_UNITS']} 份、每份 "
        f"{p['UNIT_PCT_POOL']:.0f}% 资金）且要求 RSI{p['RSI_PERIOD']} < {p['RSI_BELOW']:.0f}；"
        f"每月最多 {p['MAX_TRANCHE_PER_MONTH']} 次；减仓只在定档日卖到目标带。",
        f"扣费：申购 {p['FEE_BUY']:.2%} / 赎回 {p['FEE_SELL']:.2%}；空仓按货基 {p['RF']:.2%} 计息。"
        + ("  注意：A500 的 PE 只有 2024-09 起（指数发布日起），样本仅 1 年出头，"
           "年化/夏普不构成结论，只当现状实录。" if univ == "a500" else ""),
    ]
    fig.text(0.045, 0.010, "\n".join(note), fontsize=8.0, color="#5b6b7a", ha="left", va="bottom",
             linespacing=1.55)

    os.makedirs(out_dir, exist_ok=True)
    out = os.path.join(out_dir, f"_v6_pe_band_{univ}.png")
    fig.savefig(out, bbox_inches="tight", facecolor="white")
    plt.close(fig)
    print(f"图: {out}")
    print(f"  {u['name']} {idx[0].date()} ~ {idx[-1].date()}（{yrs:.1f} 年）· "
          f"策略 {m['ann']:+.2%}/年 回撤 {m['mdd']:.2%} 夏普 {m['sharpe']:.2f} "
          f"均仓位 {sim['exposure']:.0%} | 恒持 {mbh['ann']:+.2%}/年 回撤 {mbh['mdd']:.2%}")
    return out


def main():
    ap = argparse.ArgumentParser(description="V6 估值分位仓位带 · 单标的图")
    ap.add_argument("--univ", default="all",
                    help=f"{' / '.join(band.UNIVERSES)} / all（默认）")
    ap.add_argument("--out-dir", default=_HERE)
    a = ap.parse_args()
    if a.univ != "all" and a.univ not in band.UNIVERSES:
        raise SystemExit(f"未知标的 {a.univ}；可选 {sorted(band.UNIVERSES)} 或 all")
    todo = list(band.UNIVERSES) if a.univ == "all" else [a.univ]
    for u in todo:
        plot(u, a.out_dir)


if __name__ == "__main__":
    main()
