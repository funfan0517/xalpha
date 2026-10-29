#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""V4 · 逐标的「累计净值 + RSI14」走势图

每只池内标的输出一张 PNG（左轴累计净值、右轴 RSI14），背景按 RSI 档位着色并标注对应操作：
**RSI < 50 满仓 / 50–70 减半 / ≥70 清仓**（`rule.Q_HALF` / `rule.Q_OUT`）。

口径（同 V4 回测）：
* 累计净值取 `pool.load_navs` 的 `totvalue`（含分红再投），RSI14 用 Wilder 口径；
* **档位本是按「板块 RSI 均值」判定**，图上是把单只自己的 RSI 套用同一组阈值，仅作读图参考，
  真实仓位请以 `daily.py` 的板块均值为准；
* 月末圆点是 V4 真正做决策的时点（日频 RSI 只用于读图）。

产物：`v4_rsi/daily/_v4_<code>_chart[_all][_index].png`

用法::

    python strategies/dividend/v4_rsi/chart.py                # 池内 5 只 · 近一年
    python strategies/dividend/v4_rsi/chart.py --all          # 全样本
    python strategies/dividend/v4_rsi/chart.py --code 090010  # 只画一只
    python strategies/dividend/v4_rsi/chart.py --basis index  # 左轴改指数点位
    python strategies/dividend/v4_rsi/chart.py --refresh      # 先抓最新净值
"""

from __future__ import annotations

import os
import sys

import matplotlib

matplotlib.use("Agg")

import matplotlib.dates as mdates  # noqa: E402
import matplotlib.pyplot as plt  # noqa: E402
import pandas as pd  # noqa: E402
from matplotlib.patches import Patch  # noqa: E402

_DIR = os.path.dirname(os.path.abspath(__file__))
_STRAT = os.path.dirname(_DIR)
for _p in (_DIR, _STRAT):
    if _p not in sys.path:
        sys.path.insert(0, _p)

import _single_signal as S  # noqa: E402  pylint: disable=wrong-import-position
import pool  # noqa: E402  pylint: disable=wrong-import-position
import rule as V4  # noqa: E402  pylint: disable=wrong-import-position

CACHE = os.path.join(S.ROOT, "data", "_bt_caches")
OUT_DIR = os.path.join(_DIR, "daily")
WINDOW_DAYS = 250          # 近一年（约 250 个交易日）

plt.rcParams["font.sans-serif"] = ["Microsoft YaHei", "SimHei", "DejaVu Sans"]
plt.rcParams["axes.unicode_minus"] = False

BLUE, RED, AMBER, GREEN = "#1f4e79", "#c0504d", "#e8a33d", "#5c8a4a"

# RSI 档位：满仓 / 减半 / 清仓（低 RSI = 便宜 = 满仓）
BANDS = [
    (0.0, V4.Q_HALF * 100, "满仓 100%（<50）", "#e4efe0", GREEN),
    (V4.Q_HALF * 100, V4.Q_OUT * 100, "减半 50%（50–70）", "#fbeccf", AMBER),
    (V4.Q_OUT * 100, 100.0, "清仓 0%（≥70）", "#f7dcd9", RED),
]


def _index_series(code: str) -> tuple[pd.Series, str]:
    ent = next((d for d in pool.POOL if d["code"] == code), None)
    if ent is None:
        raise SystemExit(f"池内没有 {code}，可选：{[d['code'] for d in pool.POOL]}")
    idx = ent.get("csi") or ""
    path = os.path.join(CACHE, f"csi_{idx}.csv")
    if not idx or not os.path.exists(path):
        raise SystemExit(f"{code} 的指数 {idx!r} 没有本地日线缓存：{path}")
    df = pd.read_csv(path, parse_dates=["date"])
    return df.set_index("date")["close"].astype(float).dropna(), idx


def _nav_series(code: str) -> pd.Series:
    return pool.load_navs(codes=[code])[code].dropna()


def _tier(rsi: float) -> str:
    if rsi >= V4.Q_OUT * 100:
        return "清仓"
    if rsi >= V4.Q_HALF * 100:
        return "减半"
    return "满仓"


def plot(code: str, nav: pd.Series | None = None, full: bool = False,
         basis: str = "nav") -> str:
    name = V4.NAME.get(code, code)
    if basis == "index":
        src, idx = _index_series(code)
        left_label = f"指数点位（{idx}）"
    else:
        src = (nav if nav is not None else _nav_series(code)).dropna()
        left_label = "累计净值（含分红再投）"

    score = S.rsi_series(src, V4.WINDOW)
    start = src.index[0] if full else src.index[-WINDOW_DAYS]
    p, sc = src.loc[start:], score.loc[start:]

    fig, ax1 = plt.subplots(figsize=(5.6, 2.85), dpi=175)
    ax2 = ax1.twinx()

    # 档位背景 + 操作图例
    band_handles = []
    for lo, hi, lab, face, edge in BANDS:
        ax2.axhspan(lo, hi, color=face, alpha=0.9, zorder=0, ec="none")
        band_handles.append(Patch(facecolor=face, edgecolor=edge, label=lab))
    ax2.axhline(V4.Q_OUT * 100, color=RED, ls="--", lw=1.0, alpha=0.75)
    ax2.axhline(V4.Q_HALF * 100, color=AMBER, ls="--", lw=1.0, alpha=0.75)

    # 累计净值（左轴）
    ax1.plot(p.index, p.to_numpy(), color=BLUE, lw=1.4, label="累计净值", zorder=6)
    # RSI（右轴）—— 全样本日频会糊成一片，改画月末值（正好是 V4 决策口径）
    sc_plot = sc.resample("ME").last().dropna() if full else sc
    ax2.plot(sc_plot.index, sc_plot.to_numpy(), color=RED, lw=1.2, alpha=0.92,
             label=f"RSI{V4.WINDOW}{'·月末' if full else ''}", zorder=5)

    # 月末决策点
    me = sc_plot if full else sc.resample("ME").last().dropna()
    ax2.scatter(me.index, me.to_numpy(), s=14, color=RED, zorder=5, alpha=0.8,
                edgecolor="white", linewidth=0.4)

    # 末值标注
    last_d = sc.index[-1]
    last_p = float(p.iloc[-1])
    last_s = float(sc.iloc[-1])
    tier = _tier(last_s)
    ax1.scatter([last_d], [last_p], s=40, facecolor="white", edgecolor=BLUE, zorder=7)
    ax2.scatter([last_d], [last_s], s=40, facecolor="white", edgecolor=RED, zorder=7)
    ax1.annotate(f"{last_p:.4f}", (last_d, last_p), textcoords="offset points",
                 xytext=(6, 3), color=BLUE, fontsize=7, fontweight="bold")
    ax2.annotate(f"{last_s:.1f}", (last_d, last_s), textcoords="offset points",
                 xytext=(6, -10), color=RED, fontsize=7, fontweight="bold")

    ax1.set_ylabel(left_label, color=BLUE, fontsize=7.5)
    ax2.set_ylabel(f"RSI{V4.WINDOW}（0–100）", color=RED, fontsize=7.5)
    ax1.tick_params(axis="y", colors=BLUE, labelsize=6.8)
    ax2.tick_params(axis="y", colors=RED, labelsize=6.8)
    ax2.set_ylim(0, 100)
    ax1.grid(axis="x", ls=":", color="#cccccc", alpha=0.7)
    ax1.set_axisbelow(True)
    for sp in ("top",):
        ax1.spines[sp].set_visible(False)
        ax2.spines[sp].set_visible(False)

    if full:
        ax1.xaxis.set_major_locator(mdates.YearLocator())
        ax1.xaxis.set_major_formatter(mdates.DateFormatter("%Y"))
    else:
        ax1.xaxis.set_major_locator(mdates.MonthLocator(interval=2))
        ax1.xaxis.set_major_formatter(mdates.DateFormatter("%y-%m"))
    ax1.tick_params(axis="x", labelsize=6.5)
    # 右侧留白，避免末值标注被裁掉
    ax1.set_xlim(p.index[0], p.index[-1] + pd.Timedelta(days=40 if full else 16))

    span_txt = "全样本" if full else "近一年"
    ax1.set_title(f"{name}（{code}）· 净值与 RSI{V4.WINDOW} · {span_txt}\n"
                  f"RSI {last_s:.1f} → 单只档位「{tier}」"
                  f"（板块均值 ≥{int(V4.Q_OUT * 100)} 清仓 / ≥{int(V4.Q_HALF * 100)} 减半）",
                  fontsize=8, fontweight="bold", pad=6, loc="left")

    # 线图例左上，档位图例右侧外
    h1, l1 = ax1.get_legend_handles_labels()
    h2, l2 = ax2.get_legend_handles_labels()
    ax1.legend(h1 + h2, l1 + l2, loc="upper left", fontsize=6, framealpha=0.9, handlelength=1.2)
    ax2.legend(handles=band_handles, loc="center left", bbox_to_anchor=(1.08, 0.5),
               fontsize=5.6, title="RSI 档位 / 操作", title_fontsize=6, framealpha=0.92,
               handlelength=1.0, borderpad=0.5, labelspacing=0.35)

    # 让左轴压在右轴背景之上，避免色带冲淡净值线
    ax1.set_zorder(ax2.get_zorder() + 1)
    ax1.patch.set_visible(False)

    fig.subplots_adjust(left=0.10, right=0.73, top=0.83, bottom=0.15)
    os.makedirs(OUT_DIR, exist_ok=True)
    suffix = "" if basis == "nav" else f"_{basis}"
    out = os.path.join(OUT_DIR, f"_v4_{code}_chart{'_all' if full else ''}{suffix}.png")
    fig.savefig(out, facecolor="#fbf8f1")
    plt.close(fig)
    return out


def main() -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    args = sys.argv[1:]
    full = "--all" in args
    refresh = "--refresh" in args
    basis = args[args.index("--basis") + 1] if "--basis" in args else "nav"
    codes = [args[args.index("--code") + 1]] if "--code" in args else list(V4.CODES)

    navs = pool.load_navs(codes=V4.CODES, refresh=refresh) if basis == "nav" else None
    n = 0
    for code in codes:
        nav = navs[code].dropna() if navs is not None and code in navs else None
        out = plot(code, nav=nav, full=full, basis=basis)
        n += 1
        print(f"[chart] {code} {V4.NAME.get(code, code):<14s} -> {os.path.relpath(out, _DIR)}")
    print(f"[chart] 共 {n} 张 · 目录 {os.path.relpath(OUT_DIR, _DIR)}")
    return 0 if n else 1


if __name__ == "__main__":
    sys.exit(main())
