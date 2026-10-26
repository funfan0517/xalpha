#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""V4 读数图：指数点位（左轴）+ 分数（右轴，RSI14）

V4 的「分数」就是 **RSI14 本身**（0–100 的绝对刻度，`q = RSI/100`），所以这一张图
就是策略的原始输入：左轴看点位，右轴看分数，两条阈值线对应规则档位 ——
**分数 ≥70 清仓、≥50 减半**。月末的圆点是 V4 真正做决策的时点（日频 RSI 只用于读图）。

标的取池内代码（默认 `008115` 天弘中证红利低波100C → 指数 `930955`）：
点位与分数默认都用**指数日线**（图上自洽）；`--basis nav` 改用**基金累计净值**，
即 V4 回测的官方口径。

产物（AGENTS §8.4 ⑤）：`v4_rsi/daily/_v4_<code>_chart[_all].png`

用法::

    python strategies/dividend/v4_rsi/chart.py                     # 008115 · 近一年
    python strategies/dividend/v4_rsi/chart.py --code 090010
    python strategies/dividend/v4_rsi/chart.py --all               # 全样本
    python strategies/dividend/v4_rsi/chart.py --basis nav         # 用基金累计净值算分数
"""

from __future__ import annotations

import os
import sys

import matplotlib

matplotlib.use("Agg")

import matplotlib.dates as mdates  # noqa: E402
import matplotlib.pyplot as plt  # noqa: E402
import pandas as pd  # noqa: E402

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

plt.rcParams["font.sans-serif"] = ["Microsoft YaHei", "SimHei", "DejaVu Sans"]
plt.rcParams["axes.unicode_minus"] = False

BLUE, RED, AMBER = "#1f4e79", "#c0504d", "#e8a33d"


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


def plot(code: str = "008115", full: bool = False, basis: str = "index") -> str:
    px, idx = _index_series(code)
    name = next(d["name"] for d in pool.POOL if d["code"] == code)

    if basis == "nav":
        score_src = _nav_series(code)
        basis_txt = "基金累计净值"
    else:
        score_src = px
        basis_txt = f"指数 {idx}"

    score = S.rsi_series(score_src, V4.WINDOW)
    start = px.index[0] if full else px.index[-250]
    p, sc = px.loc[start:], score.loc[start:]

    fig, ax1 = plt.subplots(figsize=(14, 6.2), dpi=150)
    ax2 = ax1.twinx()
    ax1.plot(p.index, p.to_numpy(), color=BLUE, lw=1.6, label=f"点位（{idx}）")
    # 全样本 12 年日频分数会糊成一片，改画月末值（正好是 V4 的决策口径）
    sc_plot = sc.resample("ME").last().dropna() if full else sc
    ax2.plot(sc_plot.index, sc_plot.to_numpy(), color=RED, lw=1.7,
             label=f"分数（RSI{V4.WINDOW}{'·月末' if full else ''}）")

    # 规则档位：≥70 清仓、50–70 减半
    ax2.axhspan(V4.Q_OUT * 100, 100, color=RED, alpha=0.06)
    ax2.axhspan(V4.Q_HALF * 100, V4.Q_OUT * 100, color=AMBER, alpha=0.06)
    ax2.axhline(V4.Q_OUT * 100, color=RED, ls="--", lw=1.1, alpha=0.75)
    ax2.axhline(V4.Q_HALF * 100, color=AMBER, ls="--", lw=1.1, alpha=0.75)
    ax2.set_ylim(0, 100)

    # 月末决策点（V4 只在月末看信号）
    me = sc_plot if full else sc.resample("ME").last().dropna()
    ax2.scatter(me.index, me.to_numpy(), s=16, color=RED, zorder=5, alpha=0.85,
                label=None if full else "月末决策点")

    last_d, last_p, last_s = sc.index[-1], float(p.iloc[-1]), float(sc.iloc[-1])
    ax1.scatter([last_d], [last_p], s=46, facecolor="white", edgecolor=BLUE, zorder=6)
    ax2.scatter([last_d], [last_s], s=46, facecolor="white", edgecolor=RED, zorder=6)
    tier = ("清仓" if last_s >= V4.Q_OUT * 100
            else "减半" if last_s >= V4.Q_HALF * 100 else "满仓")
    dx, ha = (-9, "right") if full else (9, "left")
    ax1.annotate(f"{last_p:,.0f}", (last_d, last_p), textcoords="offset points",
                 xytext=(dx, 4), color=BLUE, fontsize=10, ha=ha, fontweight="bold")
    ax2.annotate(f"{last_s:.1f}", (last_d, last_s), textcoords="offset points",
                 xytext=(dx, -14), color=RED, fontsize=10, ha=ha, fontweight="bold")

    ax1.set_ylabel(f"指数点位（{idx}）", color=BLUE, fontsize=11)
    ax2.set_ylabel(f"分数（RSI{V4.WINDOW}）", color=RED, fontsize=11)
    ax1.tick_params(axis="y", colors=BLUE)
    ax2.tick_params(axis="y", colors=RED)
    ax1.grid(True, axis="x", ls=":", color="#cccccc", alpha=0.7)
    if full:
        ax1.xaxis.set_major_locator(mdates.YearLocator())
        ax1.xaxis.set_major_formatter(mdates.DateFormatter("%Y"))
    else:
        ax1.xaxis.set_major_formatter(mdates.DateFormatter("%Y-%m"))
        ax1.xaxis.set_major_locator(mdates.MonthLocator(interval=1))
    ax1.tick_params(axis="x", labelsize=9)
    # 右侧留白，避免末点标注被裁掉
    ax1.set_xlim(p.index[0], p.index[-1] + pd.Timedelta(days=18))

    ax1.set_title(f"{name}（{code} → {idx}）· 点位与 V4 分数（{basis_txt}算 RSI{V4.WINDOW}）\n"
                  f"截至 {last_d:%Y-%m-%d}：点位 {last_p:,.0f}｜分数 {last_s:.1f} → "
                  f"{tier}（≥{int(V4.Q_OUT * 100)} 清仓 / ≥{int(V4.Q_HALF * 100)} 减半）",
                  fontsize=12.5, pad=12)

    h1, l1 = ax1.get_legend_handles_labels()
    h2, l2 = ax2.get_legend_handles_labels()
    ax1.legend(h1 + h2, l1 + l2, loc="upper center", bbox_to_anchor=(0.5, -0.09),
               fontsize=9.5, ncol=3, frameon=False)
    box = dict(facecolor="white", alpha=0.85, edgecolor="none", pad=1.6)
    for lbl, y in ((f"清仓线 {int(V4.Q_OUT * 100)}", V4.Q_OUT * 100),
                   (f"减半线 {int(V4.Q_HALF * 100)}", V4.Q_HALF * 100)):
        ax2.text(p.index[3], y - 3.4, lbl, color=RED if y >= 60 else AMBER,
                 fontsize=8.5, ha="left", alpha=0.95, bbox=box)

    fig.tight_layout()
    os.makedirs(OUT_DIR, exist_ok=True)
    out = os.path.join(OUT_DIR, f"_v4_{code}_chart{'_all' if full else ''}.png")
    fig.savefig(out, bbox_inches="tight")
    plt.close(fig)
    return out


def main() -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    args = sys.argv[1:]
    code = args[args.index("--code") + 1] if "--code" in args else "008115"
    basis = args[args.index("--basis") + 1] if "--basis" in args else "index"
    outs = [plot(code, full=False, basis=basis)]
    if "--all" in args:
        outs.append(plot(code, full=True, basis=basis))
    for o in outs:
        print(f"[chart] {os.path.relpath(o, _DIR)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
