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
      `v4_rsi/daily/_v4_ops_all.png`（`--ops`：五只标的总图，净值 + 实际仓位 + ▲▼ 操作点）

用法::

    python strategies/dividend/v4_rsi/chart.py                # 池内 5 只 · 近一年
    python strategies/dividend/v4_rsi/chart.py --all          # 全样本
    python strategies/dividend/v4_rsi/chart.py --code 090010  # 只画一只
    python strategies/dividend/v4_rsi/chart.py --basis index  # 左轴改指数点位
    python strategies/dividend/v4_rsi/chart.py --refresh      # 先抓最新净值
    python strategies/dividend/v4_rsi/chart.py --ops          # 五只标的的操作标注总图
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


# ==================================================================== 操作标注总图
def v4_state(navs: pd.DataFrame | None = None) -> tuple:
    """V4 的「逐标的月末权重」全套状态（与 backtest.py 完全同源同参数）。

    返回 `(navs, rets, q, xs, W_sig, W_eff, mult_eff, bq)`：

    * `W_sig`  = 当月月末信号算出的目标权重（`top2 等权 × 板块档位`）；
    * `W_eff`  = `W_sig.shift(1)`，即**当月真正持有的权重**（月末算、下月生效，无前视）；
    * `mult_eff` = 板块档位倍率的生效版（1.0 / 0.5 / 0.0），五个标的共用；
    * `bq`    = 板块 RSI 均值（0–100），档位就是由它决定的。

    每只标的的权重台阶只可能是 0 / 25% / 50%：选中 top2 各占 50%，再乘板块倍率。
    """
    navs = pool.load_navs(codes=V4.CODES) if navs is None else navs
    rets = S.load_rets(navs)
    raw = S.load_signal(V4.SIGNAL, navs).reindex(rets.index)
    q = (raw / 100.0).loc[S.START:]
    xs = raw.loc[S.START:]
    rets = rets.loc[S.START:]
    ok = S.available(q, rets)
    bq = S.board_q(q, ok)                                   # RSI 均值（×100 才是 RSI 刻度）
    mult = S.time_mult(bq, V4.Q_OUT, V4.Q_HALF)
    W = S.top_w(xs, ok, V4.TOP_K).mul(mult, axis=0)
    return (navs, rets, q, xs, W, W.shift(1).fillna(0.0),
            mult.shift(1).fillna(1.0), bq * 100.0)


def _band_spans(ax, mult_eff: pd.Series, alpha=0.13, half=False) -> None:
    """把板块档位的生效区间铺成底色，五只标的共用同一份档位。

    `half=False`（默认）**只铺清仓期**：减半月占了 83 个月里的一大半，逐格都铺会变成
    竖条纹噪声，而「减半」在右轴的 25% 台阶上本来就看得见；板块 RSI 那一格用
    `half=True` 两档都铺，那里才是判读档位的正主。
    """
    idx = mult_eff.index
    for i in range(len(idx) - 1):
        m = float(mult_eff.iloc[i])
        if m >= 0.99:
            continue
        if m > 0.01 and not half:
            continue
        ax.axvspan(idx[i], idx[i + 1], color=AMBER if m > 0.01 else RED,
                   alpha=alpha, lw=0, zorder=0)


def plot_ops(navs: pd.DataFrame | None = None) -> str:
    """五只标的各一格：累计净值走势 + 该标的实际持仓 + **▲▼ 操作点**；末格画板块 RSI 均值。

    与 `plot()`（单只净值 + RSI 曲线）的区别：这里标的是**真正执行的操作** ——
    权重台阶来自 `top2 × 板块档位`，▲ = 该标的被加仓（含被选进 top2、或板块从减半恢复到满仓），
    ▼ = 减仓（被挤出 top2、或板块降到减半/清仓）。净值和权重同轴对照，能直接看出
    「这一次减仓躲过了下跌，还是砍在了上涨途中」。
    """
    navs_all, rets, _q, _xs, W_sig, W_eff, mult_eff, bq = v4_state(navs)
    idx = W_eff.index

    fig, axes = plt.subplots(3, 2, figsize=(14.4, 11.8), sharex=True,
                             gridspec_kw=dict(hspace=0.30, wspace=0.22))
    cells = [axes[0][0], axes[0][1], axes[1][0], axes[1][1], axes[2][0]]
    nav_m = {c: navs_all[c].resample("ME").last() for c in V4.CODES}

    for k, (ax, code) in enumerate(zip(cells, V4.CODES)):
        nav = navs_all[code].dropna()
        nav = nav.loc[idx[0]:]
        w = W_eff[code].reindex(idx).fillna(0.0)
        name = V4.NAME.get(code, code)

        _band_spans(ax, mult_eff)
        axw = ax.twinx()
        axw.set_zorder(ax.get_zorder() - 1)
        ax.patch.set_visible(False)
        # 只画台阶线不填充：月度轮换很密（20~40 次），填充的窄峰会糊成一片绿色
        axw.step(idx, w.to_numpy(), where="post", color=GREEN, lw=1.0, alpha=0.55, zorder=1)
        axw.set_ylim(0, 0.55)
        axw.set_yticks([0.0, 0.25, 0.5])
        axw.set_yticklabels(["0%", "25%", "50%"], fontsize=7, color="#3f6b2f")
        axw.set_ylabel("该标的仓位（右轴）", fontsize=7.5, color="#3f6b2f")
        axw.tick_params(axis="y", colors="#3f6b2f", length=2, labelsize=7)

        ax.plot(nav.index, nav.to_numpy(), color=BLUE, lw=1.25, zorder=4,
                label="累计净值（含分红再投）")

        d = w.diff().fillna(0.0)
        up, dn = d[d > 1e-9], d[d < -1e-9]
        y = nav_m[code].reindex(idx).ffill()
        for sel, mk, color, lab in ((up, "^", "#2e7d32", "加仓 ▲"),
                                    (dn, "v", "#c0392b", "减仓 ▼")):
            if len(sel):
                ax.plot(sel.index, y.reindex(sel.index).to_numpy(), ls="none", marker=mk,
                        ms=5.6, mfc=color, mec="white", mew=0.5, zorder=6, alpha=0.92,
                        label=f"{lab}（{len(sel)} 次）")

        ax.set_ylabel("累计净值", fontsize=7.5, color=BLUE)
        ax.tick_params(axis="y", colors=BLUE, labelsize=7)
        ax.grid(axis="x", ls=":", color="#cccccc", alpha=0.6)
        ax.set_axisbelow(True)
        for sp in ("top", "right"):
            ax.spines[sp].set_visible(False)
        if k == 0:      # 六格图例同义，只留第一格，其余格少一块遮挡
            ax.legend(loc="upper left", fontsize=6.3, framealpha=0.9, handlelength=1.3,
                      labelspacing=0.3)
        ax.set_title(f"{name}（{code}）· 平均仓位 {w.mean():.0%}、当前 {w.iloc[-1]:.0%}"
                     f" · 加 {len(up)} / 减 {len(dn)}", fontsize=8.2, fontweight="bold",
                     loc="left", pad=5)

    # ---- 末格：板块 RSI 均值（五只标的共用的驱动量）----
    axb = axes[2][1]
    _band_spans(axb, mult_eff, alpha=0.22, half=True)
    axb.plot(bq.index, bq.to_numpy(), color=RED, lw=1.3, zorder=4,
             label=f"板块 RSI 均值（{len(V4.CODES)} 只）")
    axb.axhline(V4.Q_OUT * 100, color=RED, ls="--", lw=1.1, alpha=0.85)
    axb.axhline(V4.Q_HALF * 100, color=AMBER, ls="--", lw=1.1, alpha=0.85)
    axb.text(idx[0], V4.Q_OUT * 100, f" ≥{V4.Q_OUT * 100:.0f} 清仓", va="bottom", ha="left",
             fontsize=7, color=RED)
    axb.text(idx[0], V4.Q_HALF * 100, f" ≥{V4.Q_HALF * 100:.0f} 减半", va="bottom", ha="left",
             fontsize=7, color="#a3761f")
    axb.scatter([bq.index[-1]], [bq.iloc[-1]], s=26, facecolor="white", edgecolor=RED, zorder=6)
    axb.annotate(f"{bq.iloc[-1]:.1f}", (bq.index[-1], bq.iloc[-1]), textcoords="offset points",
                 xytext=(5, 4), color=RED, fontsize=7, fontweight="bold")
    axb.set_ylabel(f"RSI{V4.WINDOW} 均值", fontsize=7.5, color=RED)
    axb.tick_params(axis="y", colors=RED, labelsize=7)
    axb.set_ylim(35, 85)
    axb.grid(axis="x", ls=":", color="#cccccc", alpha=0.6)
    axb.set_axisbelow(True)
    for sp in ("top", "right"):
        axb.spines[sp].set_visible(False)
    axb.legend(loc="upper left", fontsize=6.6, framealpha=0.9)
    last_m = float(mult_eff.iloc[-1])
    axb.annotate(f"当前 {bq.iloc[-1]:.1f} → "
                 f"{'清仓' if last_m <= 0.01 else '减半' if last_m < 0.99 else '满仓'}"
                 f"（组合 ×{last_m:.2f}）",
                 xy=(0.985, 0.05), xycoords="axes fraction", ha="right", va="bottom",
                 fontsize=7.2, color="#8a2b20",
                 bbox=dict(boxstyle="round,pad=0.28", fc="white", ec="#e0c9c4", lw=0.6))
    axb.set_title("板块 RSI 均值 → 总仓位档位（底色：黄 = 减半、红 = 清仓）",
                  fontsize=8.2, fontweight="bold", loc="left", pad=5)

    axes[2][0].xaxis.set_major_locator(mdates.YearLocator())
    axes[2][0].xaxis.set_major_formatter(mdates.DateFormatter("%Y"))
    for ax in (axes[2][0], axes[2][1]):
        ax.tick_params(axis="x", labelsize=7.5)
    for row in axes:                 # 收紧 x 轴，别让自动留白多出一个空的「2027」
        for ax in row:
            ax.set_xlim(idx[0], idx[-1])

    fig.suptitle(f"{V4.TITLE} · 各标的净值走势与操作标注"
                 f"    {idx[0]:%Y-%m} ~ {idx[-1]:%Y-%m}（{len(idx)} 月）",
                 x=0.035, ha="left", y=0.975, fontsize=13.5, color="#1b2b3a", fontweight="bold")
    note = (
        "操作口径：每月末算信号、**下月生效**。逐标的权重 = 「原始 RSI 最低的 2 只等权（各 50%）」"
        f" × 板块档位（RSI 均值 ≥{V4.Q_OUT * 100:.0f} 清仓 0、≥{V4.Q_HALF * 100:.0f} 减半 ×0.5），"
        "所以台阶只有 0 / 25% / 50%。\n"
        "▲ = 该标的当月被加仓（被选进 top2 或板块档位恢复）／▼ = 被减仓（被挤出 top2 或板块降档）；"
        "红底 = 板块清仓期（五格共用，减半期不铺底以免糊成条纹，看右轴 25% 台阶即可）。\n"
        "成本单边 0.03%/次；净值与信号同源（基金累计净值，含分红再投）。模拟结果，非投资建议。")
    fig.text(0.035, 0.012, note, fontsize=8.2, color="#5b6b7a", linespacing=1.55, va="bottom")
    fig.subplots_adjust(left=0.055, right=0.945, top=0.925, bottom=0.115)

    os.makedirs(OUT_DIR, exist_ok=True)
    out = os.path.join(OUT_DIR, "_v4_ops_all.png")
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

    if "--ops" in args:            # 只出「五只标的 + 操作标注」总图
        out = plot_ops(navs=navs)
        print(f"[chart] 操作标注总图 -> {os.path.relpath(out, _DIR)}")
        return 0

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
