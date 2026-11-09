# -*- coding: utf-8 -*-
"""V1-2（20/60/250 量价趋势跟随）单腿图：价格 + 三条均线 + 成交量 + 进出手。

画法（两栏：上=价格与均线，下=成交量与量比）：
- 价格：收盘价 + MA20 / MA60 / MA250（年线）；
- 阴影：**持仓区间**（底色浅红 = 有仓位，深浅按仓位比例不区分，只标是否在场）；
- 标记：▲ 建仓 / 加仓（恢复）、▼ 减仓、✕ 清仓（按状态机输出，落在信号当日，成交在次日收盘）；
- 成交量：柱子（超 1.5×MAV20 的日子染色）+ 20 日均量线。

产物（本目录）：`_v1_2_<leg>.png`。机械规则输出，非投资建议。
"""
import argparse
import os
import sys

import pandas as pd

_HERE = os.path.dirname(os.path.abspath(__file__))          # .../v1_2_ma_trend_pv/research
_V2 = os.path.dirname(_HERE)                                # .../v1_2_ma_trend_pv
sys.path.insert(0, _V2)

import ma_trend_vol as mtv  # noqa: E402

MARKS = {"建仓": ("^", "#c2185b"), "加仓": ("^", "#ef6c00"),
         "减仓": ("v", "#5c6bc0"), "清仓": ("x", "#212121")}


def _bucket(act):
    a = str(act)
    for k in MARKS:
        if a.startswith(k):
            return k
    return None


def plot(leg, out_dir):
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    plt.rcParams["font.sans-serif"] = ["Microsoft YaHei", "SimHei", "DejaVu Sans"]
    plt.rcParams["axes.unicode_minus"] = False

    ind = mtv.load(leg)
    start = ind["ma250"].first_valid_index()
    sim = mtv.simulate(ind.loc[start:])
    cfg = mtv.rule.LEGS[leg]
    x = pd.to_datetime(sim.index)

    fig, (ax1, ax2) = plt.subplots(2, 1, figsize=(13, 8), sharex=True,
                                   gridspec_kw={"height_ratios": [3, 1]})
    ax1.plot(x, sim["close"], color="#333", lw=1.2, label="收盘")
    for col, c, lb in (("ma20", "#1e88e5", f"MA{mtv.SMA_FAST}"),
                       ("ma60", "#fb8c00", f"MA{mtv.SMA_MID}"),
                       ("ma250", "#6d4c41", f"MA{mtv.SMA_SLOW}(年线)")):
        ax1.plot(x, sim[col], color=c, lw=1.0, label=lb, alpha=0.9)

    # 持仓区间底色
    w = sim["w"].to_numpy()
    on = w > 0
    if on.any():
        ax1.fill_between(x, ax1.get_ylim()[0], ax1.get_ylim()[1], where=on,
                         color="#c2185b", alpha=0.06, step="mid", label="持仓区间")

    # 进出手
    for ts, row in sim.iterrows():
        b = _bucket(row["act"])
        if b:
            mk, c = MARKS[b]
            ax1.scatter(ts, row["close"], marker=mk, s=42, color=c, zorder=5)
    for lb, (mk, c) in MARKS.items():
        ax1.scatter([], [], marker=mk, color=c, label=lb)

    m = mtv.perf(sim["nav"])
    b_ = mtv.perf(sim["bh"])
    st = mtv.trade_stats(mtv.trades(sim))
    ax1.set_title(f"{cfg['name']} · {mtv.LABEL}（{mtv.ohlcv.CODES[leg]} 场内量价）\n"
                  f"策略 {mtv.fmt_pct(m['ann'])}/年 · 回撤 {mtv.fmt_pct(m['mdd'])} · "
                  f"{st['n']} 笔 · 胜率 {st['win_rate']:.0%} · 盈亏比 {mtv.fmt_x(st['payoff'])} · "
                  f"曝光 {mtv.exposure(sim):.0%}　|　买入持有 {mtv.fmt_pct(b_['ann'])}/年 · "
                  f"回撤 {mtv.fmt_pct(b_['mdd'])}", fontsize=11)
    ax1.set_ylabel("价格")
    ax1.grid(alpha=0.3)
    ax1.legend(fontsize=8, ncol=4, loc="upper left")

    vol = sim["volume"] / 1e8
    colors = ["#c2185b" if r >= mtv.VOL_MULT else "#90a4ae" for r in sim["rvol"].fillna(0)]
    ax2.bar(x, vol, color=colors, width=1.0)
    ax2.plot(x, sim["mav"] / 1e8, color="#37474f", lw=1.0, label=f"{mtv.VOL_N}日均量")
    ax2.set_ylabel("成交量（亿股）")
    ax2.grid(alpha=0.3)
    ax2.legend(fontsize=8, loc="upper left")

    fig.tight_layout()
    out = os.path.join(out_dir, f"_v1_2_{leg}.png")
    fig.savefig(out, dpi=130, facecolor="white")
    plt.close(fig)
    print("图:", out)


def main():
    ap = argparse.ArgumentParser(description="V1-2 单腿图（价格 + 20/60/250 + 成交量 + 进出手）")
    ap.add_argument("--leg", choices=list(mtv.LEGS), action="append",
                    help="只画指定腿（可重复）；默认全部")
    args = ap.parse_args()
    for leg in (args.leg or list(mtv.LEGS)):
        plot(leg, _HERE)


if __name__ == "__main__":
    main()
