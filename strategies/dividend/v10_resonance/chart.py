#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""V10 · 「净值 + 股息率」双线图，并在净值曲线上标注每个操作点

两个面板（共用时间轴）
----------------------
* **上 · 净值**：默认画**价格指数点位**（与买卖点、止损线、前高同一把尺子）；
  `--basis nav` 改画**全收益指数净值化**（含分红再投，起点 = 1.0）。
  两种口径都不含 ETF 费率，费率影响见 `backtest/_v10_report.md`。
* **下 · 指标**：**反推股息率**（全收益 ÷ 价格的 250 日比值变化率，%），
  带 4.5% / 5.0% / 6.0% 三条阈值线 —— 它们决定「基础买点 / 强买 / 极端」的批次。
  买点日期在下图用竖线贯穿，方便对照「当天股息率与回撤同时够不够」。

操作点（直接用 `engine.run` 的结果，与回测/每日读数同一套状态机）
-------------------------------------------------------------
* ▲ 实心 = **建仓**（第一批），标注批次与建仓价；
* ▲ 空心 = **加仓**（第 2/3 批），标注批次；
* ▼ = **卖出**，标注退出原因（前高 / 满 1 年 / 止损）与该笔收益（全收益口径）；
* 浅绿色底纹 = **持仓区间**；末点空心圆 = **当前未平仓**。

产物：`v10_resonance/daily/_v10_<index>_chart[_<basis>].png`

用法::

    python strategies/dividend/v10_resonance/chart.py              # 主标的 · 全样本 · 价格点位
    python strategies/dividend/v10_resonance/chart.py --basis nav  # 上轴改全收益净值（归一）
    python strategies/dividend/v10_resonance/chart.py --years 5    # 只画最近 5 年（标签更宽松）
    python strategies/dividend/v10_resonance/chart.py --index H30269 --refresh
"""
from __future__ import annotations

import argparse
import os
import sys

import matplotlib

matplotlib.use("Agg")

import matplotlib.dates as mdates  # noqa: E402
import matplotlib.pyplot as plt  # noqa: E402
import pandas as pd  # noqa: E402
from matplotlib.lines import Line2D  # noqa: E402

_DIR = os.path.dirname(os.path.abspath(__file__))
_STRAT = os.path.dirname(_DIR)
for _p in (_DIR, _STRAT):
    if _p not in sys.path:
        sys.path.insert(0, _p)

import engine  # noqa: E402  pylint: disable=wrong-import-position
import factors  # noqa: E402  pylint: disable=wrong-import-position
import rule  # noqa: E402  pylint: disable=wrong-import-position

plt.rcParams["font.sans-serif"] = ["Microsoft YaHei", "SimHei", "DejaVu Sans"]
plt.rcParams["axes.unicode_minus"] = False

NAV_BLUE = "#1f4e79"
DY_RED = "#c0504d"
BUY_GREEN = "#3f6b35"
ADD_AMBER = "#d08a1e"
SELL_RED = "#8c2f39"
HOLD_FACE = "#e4efe0"
BG = "#fbf8f1"

REASON = {"peak": "前高", "ttl": "满1年", "stop": "止损"}


# 标注排布说明：试过按 bbox 自动避让，但 21 年尺度上「买」集中在 4900–5500、彼此只隔几个月，
# 自动推开会把卖出标签顶到标题区、引线穿图，反而更乱。最终用**分层手排**：
# 建仓标签向上 2 档、减持标签向下 3 档、加仓只画空心三角不写字；密集年份看 `--years 8` 的放大图。


def _left_series(panel: pd.DataFrame, basis: str) -> tuple[pd.Series, str]:
    if basis == "nav":
        return panel["tr"] / panel["tr"].iloc[0], "标的全收益净值（归一，含分红再投）"
    return panel["close"], f"价格指数点位（{rule.PRIMARY}）"


def _ops(res: dict, start: pd.Timestamp) -> list[dict]:
    """把 engine 的 trades/open 摊平成「操作点」列表（建仓 / 加仓 / 卖出）。"""
    out = []
    for i, t in enumerate(res["trades"], 1):
        if t["entry_date"] >= start:
            out.append({"kind": "buy", "seq": i, "date": t["entry_date"],
                        "text": f"买{i} {t['entry_price']:.0f}",
                        "dd": t["dd_entry"], "dy": t["dy_entry"]})
        for a in (t.get("adds") or []):
            if a["date"] >= start:
                out.append({"kind": "add", "seq": a["stage"], "date": a["date"],
                            "text": f"加{a['stage']} {a['dy'] * 100:.1f}%",
                            "dd": None, "dy": a["dy"]})
        if t["exit_date"] is not None and t["exit_date"] >= start:
            out.append({"kind": "sell", "seq": i, "date": t["exit_date"],
                        "text": f"卖{i} {REASON.get(t['reason'], t['reason'])}"
                                f" {t['ret'] * 100:+.0f}%",
                        "dd": None, "dy": None})
    if res["open"] and res["open"]["entry_date"] >= start:
        out.append({"kind": "open", "seq": 0, "date": res["bars"].index[-1],
                    "text": f"持仓中 {res['open']['ret'] * 100:+.1f}%", "dd": None, "dy": None})
    return sorted(out, key=lambda x: (x["date"], x["kind"] != "buy"))


def plot(index: str = rule.PRIMARY, basis: str = "price", years: int | None = None,
         refresh: bool = False) -> str:
    panels = factors.build(refresh=refresh)
    if index not in panels:
        raise SystemExit(f"{index} 无数据，可先跑 data.py 看数据源")
    panel = panels[index]
    target = rule.BY_INDEX[index]

    res = engine.run(panel)
    bars = res["bars"]
    end = bars.index[-1]
    start = bars.index[0]
    if years:
        start = max(start, end - pd.DateOffset(years=years))
    left, left_label = _left_series(panel, basis)
    left = left.loc[start:]
    dy = (panel["dy"] * 100).loc[start:]
    ops = _ops(res, start)

    fig = plt.figure(figsize=(13.2, 7.6), dpi=140, facecolor=BG)
    gs = fig.add_gridspec(2, 1, height_ratios=[1.95, 1.0], hspace=0.10,
                          left=0.068, right=0.905, top=0.862, bottom=0.088)
    ax1 = fig.add_subplot(gs[0])
    ax2 = fig.add_subplot(gs[1], sharex=ax1)
    for ax in (ax1, ax2):
        ax.set_facecolor(BG)
        ax.grid(axis="x", ls=":", color="#cfcfcf", alpha=0.8)
        ax.set_axisbelow(True)

    # ---- 持仓区间底纹 ----
    spans = [(t["entry_date"], t["exit_date"]) for t in res["trades"]]
    if res["open"]:
        spans.append((res["open"]["entry_date"], end))
    for a, b in spans:
        if b >= start:
            for ax in (ax1, ax2):
                ax.axvspan(max(a, start), b, color=HOLD_FACE, alpha=0.9, zorder=0, ec="none")

    # ---- 两条线 ----
    ax1.plot(left.index, left.to_numpy(), color=NAV_BLUE, lw=1.5, zorder=6)
    ax2.plot(dy.dropna().index, dy.dropna().to_numpy(), color=DY_RED, lw=1.2, zorder=5)

    # ---- 股息率阈值线 ----
    for thr, txt, ls in ((rule.DY_BUY * 100, f"{rule.DY_BUY:.1%} 基础买点", "--"),
                         (rule.DY_STRONG * 100, f"{rule.DY_STRONG:.1%} 强买", ":"),
                         (rule.DY_EXTREME * 100, f"{rule.DY_EXTREME:.1%} 极端", ":")):
        ax2.axhline(thr, color=DY_RED, ls=ls, lw=0.9, alpha=0.6, zorder=1)
        ax2.annotate(txt, (0.995, thr), xycoords=("axes fraction", "data"),
                     fontsize=6.6, color=DY_RED, alpha=0.9, va="bottom", ha="right",
                     bbox=dict(boxstyle="round,pad=0.15", fc=BG, ec="none", alpha=0.8))

    # ---- 操作点 ----
    box = dict(boxstyle="round,pad=0.2", fc=BG, ec="none", alpha=0.82)

    def _y(ts):
        return float(left.loc[:ts].iloc[-1])

    nb = ns = 0
    for op in ops:
        d0 = op["date"]
        y = float(left.iloc[-1]) if op["kind"] == "open" else _y(d0)
        ax2.axvline(d0, color=(BUY_GREEN if op["kind"] in ("buy", "add") else SELL_RED),
                    lw=0.6, ls=(0, (2, 3)), alpha=0.45, zorder=2)
        if op["kind"] == "buy":
            nb += 1
            ax1.scatter([d0], [y], marker="^", s=95, color=BUY_GREEN,
                        edgecolor="white", linewidth=0.8, zorder=9)
            # 标注「图上这条线在该点的值」：价格口径给点位，净值口径给净值，
            # 否则 `--basis nav` 下会在净值 3.3 的点旁看到「2218」，读起来自相矛盾
            label = (f"买{op['seq']} {y:.0f}" if basis == "price"
                     else f"买{op['seq']} {y:.2f}")
            ax1.annotate(label, (d0, y), textcoords="offset points",
                         xytext=(7, 9 + 14 * (nb % 2)), fontsize=6.6, color=BUY_GREEN,
                         fontweight="bold", ha="left", va="bottom", bbox=box, zorder=10,
                         arrowprops=dict(arrowstyle="-", color=BUY_GREEN, lw=0.6, alpha=0.55))
        elif op["kind"] == "add":
            # 加仓只画空心三角，不写字（批次与股息率见 §三 与 RULES.md），
            # 否则 2022–2026 六个操作点会挤成一团
            ax1.scatter([d0], [y], marker="^", s=40, facecolor="none",
                        edgecolor=ADD_AMBER, linewidth=1.1, zorder=8)
        elif op["kind"] == "sell":
            ns += 1
            ax1.scatter([d0], [y], marker="v", s=105, color=SELL_RED,
                        edgecolor="white", linewidth=0.8, zorder=9)
            # 卖出标签向左下伸展：建仓标签向右上，二者即使只隔一个月也不会叠在一起
            ax1.annotate(op["text"], (d0, y), textcoords="offset points",
                         xytext=(-7, -11 - 15 * (ns % 3)), fontsize=6.5, color=SELL_RED,
                         fontweight="bold", ha="right", va="top", bbox=box, zorder=10,
                         arrowprops=dict(arrowstyle="-", color=SELL_RED, lw=0.6, alpha=0.55))
        else:
            ax1.scatter([d0], [y], marker="o", s=48, facecolor="white",
                        edgecolor=BUY_GREEN, linewidth=1.3, zorder=9)
            # 放在末点右下方：右上方已被最后一笔「买」的标签占住
            ax1.annotate(op["text"], (d0, y), textcoords="offset points",
                         xytext=(10, -6), fontsize=6.6, color=BUY_GREEN, fontweight="bold",
                         ha="left", va="top",
                         bbox=dict(boxstyle="round,pad=0.2", fc=BG, ec=BUY_GREEN,
                                   lw=0.5, alpha=0.9), zorder=11)

    # ---- 轴与样式 ----
    ax1.set_ylabel(left_label, color=NAV_BLUE, fontsize=9)
    ax2.set_ylabel("反推股息率（%）", color=DY_RED, fontsize=9)
    ax1.tick_params(axis="y", colors=NAV_BLUE, labelsize=7.5)
    ax2.tick_params(axis="y", colors=DY_RED, labelsize=7.5)
    ax1.margins(y=0.13)
    ax2.margins(y=0.10)
    ax2.set_ylim(max(0.0, float(dy.min()) - 0.4), max(8.2, float(dy.max()) + 0.5))
    ax1.tick_params(axis="x", labelbottom=False)
    ax2.xaxis.set_major_locator(mdates.YearLocator())
    ax2.xaxis.set_major_formatter(mdates.DateFormatter("%Y"))
    ax2.tick_params(axis="x", labelsize=7.2)
    ax1.set_xlim(start, end + pd.Timedelta(days=90))

    span_txt = "全样本" if not years else f"近 {years} 年"
    ax1.set_title(
        f"{target['name']}（{index}）· 净值 + 反推股息率 · {span_txt}"
        "　　▲ 建仓（实心）/ 加仓（空心）　▼ 卖出　绿底 = 持仓区间\n"
        f"买点 = 回撤 ≥{rule.DD_BUY:.0%} 且 收盘 ≤年线({rule.MA_SHORT})/三年线({rule.MA_LONG}) "
        f"且 股息率 ≥{rule.DY_BUY:.1%}　·　"
        f"卖点 = 收复前高 / 满 {rule.SELL_TTL} 日（仍共振则续期至 {rule.SELL_TTL_MAX} 日）"
        f" / 止损 −{rule.STOP_LOSS:.0%}　·　"
        f"仓位 0–100%（三批 {rule.TRANCHE[0]:.0%}/{rule.TRANCHE[1]:.0%}/{rule.TRANCHE[2]:.0%}）",
        fontsize=10.5, fontweight="bold", pad=8, loc="left", linespacing=1.6)

    handles = [
        Line2D([], [], color=NAV_BLUE, lw=1.5, label=left_label),
        Line2D([], [], color=DY_RED, lw=1.2, label="反推股息率（下图）"),
        Line2D([], [], marker="^", color="none", markerfacecolor=BUY_GREEN,
               markeredgecolor="white", markersize=8, label="建仓"),
        Line2D([], [], marker="^", color="none", markerfacecolor="none",
               markeredgecolor=ADD_AMBER, markersize=6.5, label="加仓"),
        Line2D([], [], marker="v", color="none", markerfacecolor=SELL_RED,
               markeredgecolor="white", markersize=8, label="卖出"),
        Line2D([], [], marker="o", color="none", markerfacecolor="white",
               markeredgecolor=BUY_GREEN, markersize=7, label="当前持仓"),
    ]
    # 图例放在下方面板的左上空区：上方面板左侧是 2007 年的高点，图例放那儿会盖住曲线
    ax2.legend(handles=handles, loc="upper left", fontsize=6.8, framealpha=0.92,
               borderpad=0.5, labelspacing=0.35, ncol=2)

    fig.text(0.068, 0.014,
             f"数据截至 {end:%Y-%m-%d} · 操作点来自 engine.run（与回测 / 每日读数同一状态机）· "
             "盈亏为全收益口径（含分红再投、不含费）· "
             "股息率 = 全收益 ÷ 价格的 250 日比值反推，非指数公司当期口径",
             fontsize=6.6, color="#666666")

    os.makedirs(rule.DAILY_DIR, exist_ok=True)
    suffix = (f"_{years}y" if years else "") + ("" if basis == "price" else f"_{basis}")
    out = os.path.join(rule.DAILY_DIR, f"_v10_{index}_chart{suffix}.png")
    fig.savefig(out, facecolor=BG)
    plt.close(fig)
    return out


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="V10 净值 + 股息率双线图（标注操作点）")
    ap.add_argument("--index", default=rule.PRIMARY, choices=rule.INDEXES)
    ap.add_argument("--basis", default="price", choices=("price", "nav"),
                    help="price=价格指数点位（默认）；nav=全收益净值归一")
    ap.add_argument("--years", type=int, default=None, help="只画最近 N 年")
    ap.add_argument("--refresh", action="store_true")
    args = ap.parse_args(argv)

    out = plot(index=args.index, basis=args.basis, years=args.years, refresh=args.refresh)
    print(f"[v10:chart] {args.index} {rule.BY_INDEX[args.index]['name']} "
          f"basis={args.basis} -> {os.path.relpath(out, rule.ROOT)}")
    return 0


if __name__ == "__main__":
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    sys.exit(main())
