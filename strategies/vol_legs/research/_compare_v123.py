# -*- coding: utf-8 -*-
"""V1 / V2 / V3 逐腿择时对比图（中证A500 / 科创50 / 恒生科技）。

三套规则同池、同口径、同执行纪律，只有指标不同：
- V1 `ma20`        ：净值上穿 MA20 买入、下穿 MA20 卖出（13~16 笔/年）；
- V2 `boll`        ：收盘上穿 BOLL(35,2) 上轨买入、跌回中轨卖出（2~3 笔/年）；
- V3 `macd_boll`   ：MACD 零轴上金叉 + 布林开口上穿买入、死叉或跌破中轨卖出（2~4 笔/年）。

口径：场外**复权净值**（`data.series_for_leg`，含分红再投 / 汇率 / 跟踪误差）；
T 日盘后算信号、**T+1 净值成交**（`pos = sig.shift(1)`）；空仓按货基 1.40% 逐日计息；不含申赎费。

每张图两栏：上 = 复权净值 + 三套规则的买卖点（实心 = 买、空心 = 卖）；
下 = 三套规则的择时净值 + 买入持有（起点 = 1），图例带年化 / 回撤 / 夏普 / 笔数。

产物（本目录）：`_compare_v123_a500.png` / `_compare_v123_kc50.png` / `_compare_v123_hkt.png`。
机械规则输出，非投资建议。
"""
import os
import sys

import numpy as np
import pandas as pd

try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:  # noqa
    pass

_HERE = os.path.dirname(os.path.abspath(__file__))
_VL = os.path.dirname(_HERE)                       # strategies/vol_legs

for _p in (_VL,
           os.path.join(_VL, "v1_ma20"),
           os.path.join(_VL, "v2_boll_breakout"),
           os.path.join(_VL, "v3_macd_boll")):
    if _p not in sys.path:
        sys.path.insert(0, _p)

import ma20          # noqa: E402
import boll          # noqa: E402
import macd_boll     # noqa: E402
import rule          # noqa: E402

LEGS = ["a500", "kc50", "hkt"]
WARMUP = max(ma20.MA_WIN, boll.PARAMS["N"], macd_boll.WARMUP)   # 三套指标全部成形

# (显示名, 模块, 颜色, 买点 marker)
STYLE = [
    ("V1 MA20", ma20, "#1f6fd0", "o"),
    ("V2 BOLL", boll, "#d9782d", "^"),
    ("V3 MACD+BOLL", macd_boll, "#7b52ab", "s"),
]
# 标记轨道偏移（点）：买点下移、卖点上移，三条规则各占一条，避免 17 年的标记糊在一起
LANE = {"V1 MA20": 10, "V2 BOLL": 21, "V3 MACD+BOLL": 32}


def signals(px):
    """三套规则的 0/1 目标仓位（各自模块的 signal，未切片）。"""
    return {
        "V1 MA20": ma20.signal(px)[0],
        "V2 BOLL": boll.signal(px)[0],
        "V3 MACD+BOLL": macd_boll.signal(px),
    }


def trades_per_year(sig):
    """仓位翻转（单边）+ 年均笔数（往返/年）。"""
    d = sig.shift(1).fillna(0).diff().fillna(0)
    n_turn = int((d.abs() > 0).sum())
    yrs = max((sig.index[-1] - sig.index[0]).days / 365.25, 1e-9)
    return n_turn, n_turn / 2.0 / yrs


def plot_leg(leg):
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    import matplotlib.dates as mdates

    plt.rcParams["font.sans-serif"] = ["Microsoft YaHei", "SimHei", "DejaVu Sans"]
    plt.rcParams["axes.unicode_minus"] = False

    px_full = ma20.load(leg)
    if len(px_full) <= WARMUP + 10:
        print(f"[跳过] {leg}: 样本不足")
        return None
    sig_full = signals(px_full)
    start = px_full.index[min(WARMUP, len(px_full) - 2)]
    px = px_full.loc[start:]
    sig = {k: v.reindex(px.index).fillna(0).astype(int) for k, v in sig_full.items()}

    from matplotlib.transforms import ScaledTranslation

    fig, (ax1, ax2) = plt.subplots(
        2, 1, figsize=(13.5, 8.8), sharex=True,
        gridspec_kw={"height_ratios": [1.25, 1.0], "hspace": 0.12})

    # ---- 上：复权净值 + 买卖点（每条规则占一条「标记轨道」，买在下方、卖在上方，避免糊成一片）
    ax1.plot(px.index, px.to_numpy(), color="#333", lw=1.0, label="复权净值")
    vals = px.to_numpy()

    # 桥接段：otc_start 之前不是本基金净值（A500 用 000051 沪深300 联接补齐窗口）
    bridge = None
    cut = pd.Timestamp(rule.LEGS[leg].get("otc_start")) if rule.LEGS[leg].get("otc_start") else None
    if cut is not None and rule.LEGS[leg].get("otc_proxy") and px.index[0] < cut < px.index[-1]:
        bridge = (cut, rule.LEGS[leg]["otc_proxy"], rule.LEGS[leg].get("otc_proxy_name", ""))
        for ax in (ax1, ax2):
            ax.axvspan(px.index[0], cut, color="#999", alpha=0.08, lw=0)
            ax.axvline(cut, color="#888", lw=0.9, ls="--")
        ax1.text(cut, 0.99, f"{cut.date()} 起才是 {rule.LEGS[leg]['otc']} 真实净值  ",
                 transform=ax1.get_xaxis_transform(), va="top", ha="right",
                 fontsize=8.5, color="#666")

    stats = {}
    for name, mod, color, mk in STYLE:
        pos = sig[name].shift(1).fillna(0).astype(float)
        d = pos.diff().fillna(0).to_numpy()
        buys, sells = np.where(d > 0)[0], np.where(d < 0)[0]
        lane = LANE[name]
        ax1.plot(px.index[buys], vals[buys], ls="none", marker=mk, ms=4.6, mfc=color,
                 mec="white", mew=0.4, zorder=5, label=f"{name} 买",
                 transform=ax1.transData + ScaledTranslation(0, -lane / 72.0, fig.dpi_scale_trans))
        ax1.plot(px.index[sells], vals[sells], ls="none", marker=mk, ms=4.6, mfc="white",
                 mec=color, mew=1.0, zorder=5, label=f"{name} 卖",
                 transform=ax1.transData + ScaledTranslation(0, lane / 72.0, fig.dpi_scale_trans))

        nav = mod.run(px, sig[name])
        p = mod.perf(nav)
        n_turn, per_year = trades_per_year(sig[name])
        stats[name] = (p, n_turn, per_year, float(pos.mean()))
        ax2.plot(nav.index, nav.to_numpy(), color=color, lw=1.5,
                 label=f"{name}  {p['ann']:+.1%}/年 · 回撤 {p['mdd']:.1%} · "
                       f"夏普 {p['sharpe']:.2f} · {per_year:.1f} 笔/年")

    # ---- 下：择时净值 vs 买入持有
    bh = px / px.iloc[0]
    pb = macd_boll.perf(bh)
    ax2.plot(bh.index, bh.to_numpy(), color="#666", lw=1.0, ls="--",
             label=f"买入持有  {pb['ann']:+.1%}/年 · 回撤 {pb['mdd']:.1%} · 夏普 {pb['sharpe']:.2f}")

    ax1.margins(y=0.14)
    ax1.set_ylabel("复权净值", fontsize=10)
    ax1.grid(ls="--", lw=0.6, alpha=0.4)
    ax1.set_axisbelow(True)
    ax1.legend(loc="lower left", bbox_to_anchor=(0.0, 1.01, 1.0, 0.10), ncol=7,
               mode="expand", frameon=False, fontsize=8.5,
               handletextpad=0.3, columnspacing=1.0)
    for s in ("top", "right"):
        ax1.spines[s].set_visible(False)

    ax2.axhline(1.0, color="#aaa", lw=0.8, ls=":")
    ax2.set_ylabel("择时净值（起点 = 1）", fontsize=10)
    ax2.grid(ls="--", lw=0.6, alpha=0.4)
    ax2.set_axisbelow(True)
    ax2.legend(loc="upper left", fontsize=9, frameon=False)
    for s in ("top", "right"):
        ax2.spines[s].set_visible(False)
    ax2.xaxis.set_major_locator(mdates.YearLocator())
    ax2.xaxis.set_major_formatter(mdates.DateFormatter("%Y"))

    name = rule.LEGS[leg]["name"]
    code = rule.LEGS[leg]["otc"]
    fig.suptitle(f"{name}（{code}）· V1 / V2 / V3 逐腿择时对比"
                 f"    |    {px.index[0].date()} ~ {px.index[-1].date()}",
                 x=0.055, ha="left", y=0.975, fontsize=14.5, color="#1b2b3a", weight="bold")
    note = ("上：场外复权净值 + 信号（实心 = 买入、空心 = 卖出；标记分轨，离净值线越远 = V1→V2→V3）。"
            "下：T+1 净值成交、空仓按货基 1.40% 计息、未扣申赎费；V1/V2/V3 属同一层信号，实盘请择一。")
    if bridge:
        note += f"\n灰色区段为桥接（{bridge[1]} {bridge[2]}），只用于撑指标窗口，不是本基金业绩。"
    fig.text(0.055, 0.010, note + "  ·  模拟结果，非投资建议。",
             fontsize=9, color="#5b6b7a", linespacing=1.5)
    fig.subplots_adjust(left=0.065, right=0.975, top=0.885, bottom=0.105)

    out = os.path.join(_HERE, f"_compare_v123_{leg}.png")
    fig.savefig(out, dpi=130, facecolor="white")
    plt.close(fig)
    print("图:", out)
    for name2, (p, n_turn, per_year, expo) in stats.items():
        print(f"  {name2:14s} 年化 {p['ann']:+7.2%}  回撤 {p['mdd']:+7.2%}  夏普 {p['sharpe']:5.2f}"
              f"  曝光 {expo:5.0%}  {n_turn:3d} 笔单边 / {per_year:.1f} 往返/年")
    print(f"  {'买入持有':14s} 年化 {pb['ann']:+7.2%}  回撤 {pb['mdd']:+7.2%}  夏普 {pb['sharpe']:5.2f}")
    return out


def main():
    for leg in LEGS:
        plot_leg(leg)


if __name__ == "__main__":
    main()
