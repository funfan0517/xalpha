#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""V9 · 逐标的走势图（净值 + 温度 双轴，标出温度带与对应操作）

每只标的输出一张 PNG：`daily/charts/<代码>_<短名>.png`

* 左轴：净值（价格序列起点归一 = 1.0；标普系为 ETF/LOF 代理价格，非基金真实净值）
* 右轴：温度（逐日，`factors.temp_at` 与每日读数同一函数，不另算口径）
* 背景：按 `rule.BANDS` 把 0–100 划成温度带，每带配色并在图例里写明对应操作

用法::

    python strategies/dividend/v9_temp5/charts.py [--refresh]

只读 `factors.build()` 的数据面板，不写任何信号文件，与 `daily.py` 口径一致。
"""
from __future__ import annotations

import os
import sys

import matplotlib
matplotlib.use("Agg")            # 无界面后端，服务器/命令行也能出图
import matplotlib.dates as mdates  # noqa: E402
import matplotlib.pyplot as plt   # noqa: E402
import pandas as pd               # noqa: E402
from matplotlib.patches import Patch  # noqa: E402

_DIR = os.path.dirname(os.path.abspath(__file__))
if _DIR not in sys.path:
    sys.path.insert(0, _DIR)

import factors  # noqa: E402  pylint: disable=wrong-import-position
import rule     # noqa: E402  pylint: disable=wrong-import-position

OUT_DIR = os.path.join(rule.DAILY_DIR, "charts")
WINDOW_DAYS = rule.PCT_WINDOW_DAYS   # 图与分位窗口对齐：近 2 年（730 日历日）

plt.rcParams["font.sans-serif"] = ["Microsoft YaHei", "SimHei", "DejaVu Sans"]
plt.rcParams["axes.unicode_minus"] = False

# 温度带配色（背景柔色 + 边框深色），与看板 zone_color 同一套语义
BAND_STYLE = {
    "危险":     ("#f6d5cf", "#a8271a"),
    "过热":     ("#fbe0d2", "#cf4b25"),
    "偏热":     ("#fbe8d6", "#e07b27"),
    "中性偏热": ("#fdf4e3", "#d9a13b"),
    "中性":     ("#eef4e6", "#7d9a55"),
    "偏冷":     ("#dfeaf5", "#4a86bd"),
    "冰点":     ("#d3e2f2", "#3c6fa8"),
}
# 图例里的操作用短标签（完整动作见卡片正文），避免小图被长文本撑爆
SHORT_ACTION = {
    "危险": "大幅减仓", "过热": "分批减仓", "偏热": "部分止盈",
    "中性偏热": "持有观望", "中性": "正常定投", "偏冷": "分批加仓", "冰点": "分批加仓",
}


def band_ranges():
    """rule.BANDS（降序阈值）-> [(lo, hi, 状态, 动作)]，hi 为开区间上界。"""
    out, hi = [], 100.0
    for lo, name, action in rule.BANDS:
        out.append((float(lo), float(hi), name, action))
        hi = float(lo)
    return out


def _temp_series(panel: dict):
    """逐日温度（与 daily.py 同一 `temp_at`），返回按日期索引的 DataFrame。"""
    f = panel["fund"]
    daily = panel["daily"]
    return factors.temp_at(daily, daily.index, has_valuation=f.get("has_valuation", True))


def plot_one(code: str, panel: dict, t, refresh: bool = False) -> str | None:
    f = panel["fund"]
    daily = panel["daily"]
    nav = daily["close"].astype(float).dropna()
    if nav.empty:
        return None

    # 展示窗口 = 分位窗口（730 天）：x 轴 [asof − WINDOW_DAYS, asof]
    asof = nav.index[-1]
    xmin = max(asof - pd.Timedelta(days=WINDOW_DAYS), nav.index[0])
    nav = nav.loc[xmin:]
    nav = nav / nav.iloc[0]                   # 起点归一 = 1.0

    temp = t["temp"].astype(float) if t is not None and len(t) else None
    if temp is not None:
        temp = temp.loc[xmin:]

    fig, ax = plt.subplots(figsize=(5.0, 2.55), dpi=180)
    ax2 = ax.twinx()

    # 温度带背景 + 操作标注（图例）
    handles = []
    for lo, hi, name, action in band_ranges():
        face, edge = BAND_STYLE.get(name, ("#f0f0f0", "#999999"))
        ax2.axhspan(lo, hi, color=face, zorder=0, ec="none", alpha=0.85)
        handles.append(Patch(facecolor=face, edgecolor=edge,
                             label=f"{name} {lo:.0f}–{hi:.0f} · {SHORT_ACTION.get(name, action)}"))

    # 净值（左轴）
    ax.plot(nav.index, nav.values, color="#1f6fb2", lw=1.2, label="净值（起点=1）", zorder=6)
    ax.set_ylabel("净值（起点=1）", color="#1f6fb2", fontsize=7.5)
    ax.tick_params(axis="y", colors="#1f6fb2", labelsize=7)

    # 温度（右轴）
    if temp is not None and len(temp):
        ax2.plot(temp.index, temp.values, color="#c0392b", lw=1.05, alpha=0.92,
                 label="温度", zorder=5)
        last = temp.dropna()
        if len(last):
            ax2.scatter([last.index[-1]], [last.iloc[-1]], s=16, color="#c0392b",
                        zorder=5, edgecolor="white", linewidth=0.7)
            ax2.annotate(f"{last.iloc[-1]:.1f}", (last.index[-1], last.iloc[-1]),
                         xytext=(4, 0), textcoords="offset points", fontsize=7.5,
                         color="#c0392b", va="center", fontweight="bold")
    ax2.set_ylim(0, 100)
    ax2.set_ylabel("温度（0–100）", color="#c0392b", fontsize=7.5)
    ax2.tick_params(axis="y", colors="#c0392b", labelsize=7)
    ax.set_xlim(xmin, nav.index[-1])

    is_px = not f.get("has_valuation", True)
    tag = "价格温度†" if is_px else "五因子温度"
    ax.set_title(f"{f['short']}（{code}）· {tag} · 近两年",
                 fontsize=9.5, fontweight="bold", pad=6, loc="left")

    ax.grid(axis="x", color="#e6ddcb", lw=0.4, alpha=0.6)
    ax.set_axisbelow(True)
    ax.xaxis.set_major_locator(mdates.MonthLocator(interval=2))
    ax.xaxis.set_major_formatter(mdates.DateFormatter("%y-%m"))
    ax.tick_params(axis="x", labelsize=6.5, rotation=0)
    for sp in ("top",):
        ax.spines[sp].set_visible(False)
        ax2.spines[sp].set_visible(False)

    # 图例：净值/温度在左上，温度带在右侧外
    h1, l1 = ax.get_legend_handles_labels()
    ax.legend(h1, l1, loc="upper left", fontsize=6.2, framealpha=0.9, handlelength=1.2)
    ax2.legend(handles=handles, loc="center left", bbox_to_anchor=(1.075, 0.5),
               fontsize=5.6, title="温度带 / 操作", title_fontsize=6.2, framealpha=0.92,
               handlelength=1.0, borderpad=0.5, labelspacing=0.35)

    # 让左轴（净值）压在右轴（温度带背景）之上，避免 axhspan 把净值线冲淡
    ax.set_zorder(ax2.get_zorder() + 1)
    ax.patch.set_visible(False)

    fig.subplots_adjust(left=0.085, right=0.70, top=0.88, bottom=0.15)
    os.makedirs(OUT_DIR, exist_ok=True)
    path = os.path.join(OUT_DIR, f"{code}_{f['short']}.png")
    fig.savefig(path, facecolor="#fbf8f1")
    plt.close(fig)
    return path


def main(refresh: bool = False) -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    panels = factors.build(refresh=refresh)
    if not panels:
        print("[v9:chart] 无数据面板，跳过", file=sys.stderr)
        return 1
    # 清理已剔除标的的旧图，避免 charts/ 残留历史标的（如 005125/008163）
    os.makedirs(OUT_DIR, exist_ok=True)
    keep = {f"{f['code']}_" for f in rule.FUNDS}
    for fn in os.listdir(OUT_DIR):
        if fn.lower().endswith(".png") and not any(fn.startswith(k) for k in keep):
            try:
                os.remove(os.path.join(OUT_DIR, fn))
            except OSError:
                pass
    n = 0
    for code, p in panels.items():
        t = _temp_series(p)
        path = plot_one(code, p, t)
        if path:
            n += 1
            rel = os.path.relpath(path, rule._DIR)  # noqa: SLF001
            print(f"[v9:chart] {code} {p['fund']['short']:<12s} -> {rel}")
    print(f"[v9:chart] 共 {n} 张 · 目录 {os.path.relpath(OUT_DIR, rule._DIR)}")
    return 0 if n else 1


if __name__ == "__main__":
    sys.exit(main("--refresh" in sys.argv))
