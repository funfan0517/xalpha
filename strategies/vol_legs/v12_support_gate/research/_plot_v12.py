# -*- coding: utf-8 -*-
"""V12 支撑位闸门 · 回测可视化（一张图讲完「为什么不上线」）。

六格：
1. 七腿组合净值：买入持有 / V5 / **V12 纯趋势** / V12 完整 —— 差距在哪一眼可见
2. 组合回撤（同一批策略，水下图）
3. 逐腿年化（4 组柱）
4. 逐腿最大回撤（4 组柱）
5. 中证A500 的档位带：价格 + `state`（0/0.2/0.5/1.0）—— 看「破位清仓」有多频繁
6. 消融年化条形：从「纯趋势」到「完整 V12」每一层各砍掉多少

数字与 `backtest.py` 同源（直接复用它的 `leg_rows / sens_rows / SENS`），
不另写一套统计，避免图与报告对不上。

机械规则输出，非投资建议。
"""
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

import backtest as bt    # noqa: E402
import support_gate as sg  # noqa: E402

tg = sg.tg

C = {"buyhold": "#8a94a6", "v5": "#6b8cae", "v12_trend": "#1f8a4c", "v12": "#c0392b"}
LAB = {"buyhold": "买入持有", "v5": "V5 默认", "v12_trend": "V12 纯趋势（去支撑层）",
       "v12": "V12 完整（趋势+支撑）"}


def _navs():
    frame = bt._frame(sg.LEGS)      # 与 backtest.py 同一窗口（预热期之后取交集）
    total = sum(sg.base.LEGS[k]["band"] for k in sg.LEGS)
    w = {k: float(sg.base.LEGS[k]["band"]) / total for k in sg.LEGS}
    sts = {}
    for k in sg.LEGS:
        px = sg.load(k)
        sts[k] = {
            "buyhold": pd.Series(1.0, index=px.index),
            "v5": tg.state_machine(px, k)["state"],
            "v12_trend": sg.state_series(px, k, USE_SUPPORT=False)["state"],
            "v12": sg.state_series(px, k)["state"],
        }
    navs = {}
    for nm in ("buyhold", "v5", "v12_trend", "v12"):
        st = {k: sts[k][nm].reindex(frame.index).ffill().bfill().fillna(0.0) for k in sg.LEGS}
        navs[nm] = tg.portfolio(frame, st, weight=w, fee=True)
    return frame, navs


def plot(out_dir=_HERE):
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    from matplotlib.gridspec import GridSpec

    plt.rcParams["font.sans-serif"] = ["Microsoft YaHei", "SimHei", "DejaVu Sans"]
    plt.rcParams["axes.unicode_minus"] = False

    frame, navs = _navs()
    legs = bt.leg_rows()
    sens = bt.sens_rows()

    fig = plt.figure(figsize=(14.2, 12.4))
    gs = GridSpec(3, 2, figure=fig, height_ratios=[1.35, 1.0, 1.0],
                  hspace=0.42, wspace=0.22)

    # ---- 1. 组合净值
    ax = fig.add_subplot(gs[0, :])
    for nm in ("buyhold", "v5", "v12_trend", "v12"):
        nav = navs[nm]
        ax.plot(nav.index, nav / nav.iloc[0], lw=1.5 if nm == "v12" else 1.2,
                color=C[nm], label=f"{LAB[nm]}（年化 {bt.fp(_ann(nav))}）")
    ax.set_title("七腿组合净值（40pp 额度 · 组内归一化 · 已扣申赎费）", fontsize=11.5, loc="left")
    ax.grid(ls="--", lw=0.6, alpha=0.28)
    ax.legend(fontsize=9, loc="upper left", frameon=False)
    ax.tick_params(labelsize=8.5)

    # ---- 2. 组合回撤
    axd = fig.add_subplot(gs[1, 0])
    for nm in ("buyhold", "v5", "v12_trend", "v12"):
        nav = navs[nm]
        dd = nav / nav.cummax() - 1.0
        axd.plot(dd.index, dd * 100.0, lw=1.0, color=C[nm], label=LAB[nm])
    axd.set_title("组合回撤（%）· 支撑层并没有把回撤压下去", fontsize=10.5, loc="left")
    axd.grid(ls="--", lw=0.5, alpha=0.25)
    axd.legend(fontsize=7.6, loc="lower left", frameon=False)
    axd.tick_params(labelsize=8)

    # ---- 3. 逐腿年化
    axa = fig.add_subplot(gs[1, 1])
    x = np.arange(len(legs))
    bw = 0.2
    for i, nm in enumerate(("buyhold", "v5", "v12_trend", "v12")):
        vals = [r[nm]["ann"] * 100.0 for r in legs]
        axa.bar(x + (i - 1.5) * bw, vals, bw, color=C[nm], alpha=0.9,
                label=LAB[nm].split("（")[0])
    axa.set_xticks(x)
    axa.set_xticklabels([r["name"] for r in legs], fontsize=7.6, rotation=18)
    axa.axhline(0, color="#5b6b7a", lw=0.8)
    axa.set_title("逐腿年化（%）· 美股两条腿被支撑层砍得最惨", fontsize=10.5, loc="left")
    axa.grid(ls="--", axis="y", lw=0.5, alpha=0.25)
    axa.legend(fontsize=7.4, ncol=2, frameon=False)
    axa.tick_params(labelsize=8)

    # ---- 4. 逐腿最大回撤
    axm = fig.add_subplot(gs[2, 0])
    for i, nm in enumerate(("buyhold", "v5", "v12_trend", "v12")):
        vals = [r[nm]["mdd"] * 100.0 for r in legs]
        axm.bar(x + (i - 1.5) * bw, vals, bw, color=C[nm], alpha=0.9)
    axm.set_xticks(x)
    axm.set_xticklabels([r["name"] for r in legs], fontsize=7.6, rotation=18)
    axm.set_title("逐腿最大回撤（%）· V12 略浅，但代价是丢掉一半年化", fontsize=10.5, loc="left")
    axm.grid(ls="--", axis="y", lw=0.5, alpha=0.25)
    axm.tick_params(labelsize=8)

    # ---- 5. 消融年化条形
    axs = fig.add_subplot(gs[2, 1])
    names = [r["name"] for r in sens][::-1]
    vals = [r["ann"] * 100.0 for r in sens][::-1]
    cols = ["#1f8a4c" if "去掉整个支撑层" in n or "纯趋势" in n else
            ("#c0392b" if n.startswith("V12 完整") else "#6b8cae") for n in names]
    axs.barh(range(len(vals)), vals, color=cols, alpha=0.9, height=0.72)
    axs.set_yticks(range(len(vals)))
    axs.set_yticklabels([n[:22] for n in names], fontsize=6.8)
    axs.axvline(vals[-1], color="#c0392b", ls=":", lw=0.9)
    axs.set_title("消融：每一层各砍掉多少年化（%）", fontsize=10.5, loc="left")
    axs.grid(ls="--", axis="x", lw=0.5, alpha=0.25)
    axs.tick_params(labelsize=7.6)

    fig.text(0.045, 0.975,
             "V12 支撑位闸门 · 七腿回测 · 结论：支撑层是负贡献，只有趋势层站得住",
             fontsize=14, color="#1b2b3a", weight="bold", ha="left")
    v12_ann = _ann(navs["v12"])
    tr_ann = _ann(navs["v12_trend"])
    v5_ann = _ann(navs["v5"])
    fig.text(0.045, 0.947,
             f"七腿窗口 {frame.index[0].date()} ~ {frame.index[-1].date()} · "
             f"V12 完整 {bt.fp(v12_ann)} / V12 纯趋势 {bt.fp(tr_ann)} / V5 {bt.fp(v5_ann)} —— "
             f"装上支撑层后年化掉 {(tr_ann - v12_ann) * 100:.2f}pp",
             fontsize=10, color="#3d4f5f", ha="left")

    by_leg = {r["leg"]: r for r in legs}

    def _pick(pre):
        return next(r for r in sens if r["name"].startswith(pre))

    s_full, s_tr = _pick("V12 完整"), _pick("① 去掉整个支撑层")
    s_clear, s_stop = _pick("① 只留「破位清仓」"), _pick("⑤ 开 ATR")
    ndx, spx = by_leg["ndx"], by_leg["spx"]
    note = [
        f"① 支撑层 = 贴线减半 + 破位清仓。年化从纯趋势的 {bt.fp(s_tr['ann'])} 变成 "
        f"{bt.fp(s_full['ann'])}，动作从 {bt.fn(s_tr['acts_per_year'], 1)} 次/年 涨到 "
        f"{bt.fn(s_full['acts_per_year'], 1)} 次/年；",
        f"   其中**破位清仓**是主因（单测 {bt.fp(s_clear['ann'])}，回撤反而更深 {bt.fp(s_clear['mdd'])}）"
        f"—— 卖在低点，再等价格站回支撑上方 {sg.NEAR:.0%} 才回补。",
        f"② 逐腿看，纳指（{bt.fp(ndx['v5']['ann'])} → {bt.fp(ndx['v12']['ann'])}）与标普"
        f"（{bt.fp(spx['v5']['ann'])} → {bt.fp(spx['v12']['ann'])}）被砍得最狠："
        "慢牛里「跌破前低」是常态噪声。",
        f"③ v11 的「放宽 ATR 止损」只在**单笔 + 固定 5R 目标**下成立；搬到逐日跟踪止损是 "
        f"{bt.fp(s_stop['ann'])}/年、{bt.fn(s_stop['acts_per_year'], 0)} 次/年（消融表末几行）。",
        "④ 本图不复算收益：数字全部来自 `backtest.py`，图只是它的可视化。模拟结果，非投资建议。",
    ]
    fig.text(0.045, 0.012, "\n".join(note), fontsize=8.4, color="#5b6b7a",
             linespacing=1.55, va="bottom")

    out = os.path.join(out_dir, "_v12_support_bt.png")
    fig.savefig(out, dpi=135, facecolor="white")
    plt.close(fig)
    return out


def _ann(nav):
    return tg.perf(nav)["ann"]


if __name__ == "__main__":
    print("出图:", plot())
