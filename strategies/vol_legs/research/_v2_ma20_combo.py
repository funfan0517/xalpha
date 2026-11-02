# -*- coding: utf-8 -*-
"""V2（BOLL 上轨突破）叠不叠 MA20？—— 六腿 + 组合层的同口径对照。

`v2_boll_breakout` 与 `v1_ma20` 回答的是同一件事（逐腿进出时点），只是触发条件不同：
V2 要「突破上轨」才上车、跌回中轨就下车；V1 是「站上 MA20 就上车、跌破 MA20 就下车」。
本脚本把 MA20 以三种方式**叠到 V2 上**，看是不是更好：

| # | 组合 | 买入 | 卖出 |
|---|---|---|---|
| 1 | `V2 基准` | 上穿上轨 | 跌破中轨 |
| 2 | `+MA20 入场过滤` | 上穿上轨 **且** 收盘 > MA20 | 跌破中轨 |
| 3 | `+MA20 出场止损` | 上穿上轨 | 跌破中轨 **或** 跌破 MA20 |
| 4 | `+两者` | 上穿上轨 且 收盘 > MA20 | 跌破中轨 或 跌破 MA20 |
| 5 | `+MA20 斜率` | 上穿上轨 **且** MA20 上行（> 20 日前） | 跌破中轨 |
| 6 | `V1 基准`（参照） | 上穿 MA20 | 跌破 MA20 |

口径与族内一致：场外复权净值、T+1 净值成交、空仓按货基 1.40%、组合层按目标带归一化（满仓 = 40pp）。
每格给「不计费 / 扣费」两行；扣费按真实执行代码（A 类 0.10%/0.50%，C 类 0）。

样本两段分别报：**全样本**（各腿指标预热后各自起算）与**共同窗口**（2021-11 ~ 2026-10，六腿交集），
A500 另给 `023299` 成立后（2025-03-27 起）的单腿结果 —— 那才是本基金自己的历史。

产物（本目录）：`_v2_ma20_combo.md` · `_v2_ma20_combo.png`。机械规则输出，非投资建议。
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
_VL = os.path.dirname(_HERE)

for _p in (_VL, os.path.join(_VL, "v2_boll_breakout")):
    if _p not in sys.path:
        sys.path.insert(0, _p)

import boll          # noqa: E402
import engine        # noqa: E402
import rule          # noqa: E402

LEGS = rule.LEG_ORDER
CASH_DAILY = boll.CASH_DAILY
FEE = boll.FEE
MA_WIN = 20
N, K = boll.PARAMS["N"], boll.PARAMS["K"]
W = {k: float(rule.LEGS[k]["band"]) / float(rule.SLEEVE_PP) for k in LEGS}

COMBOS = [
    ("V2 基准", dict(ma_entry=False, ma_exit=False, ma_slope=False)),
    ("+MA20 入场过滤", dict(ma_entry=True, ma_exit=False, ma_slope=False)),
    ("+MA20 出场止损", dict(ma_entry=False, ma_exit=True, ma_slope=False)),
    ("+两者", dict(ma_entry=True, ma_exit=True, ma_slope=False)),
    ("+MA20 斜率", dict(ma_entry=False, ma_exit=False, ma_slope=True)),
    ("+MA20 替代中轨", dict(ma_exit_only=True)),
    ("V1 基准", dict(v1=True)),
]


def state(buy_ev, sell_ev):
    """事件 → 0/1 状态（当日成立当日切换；T+1 成交在 run 里做）。"""
    b = np.asarray(buy_ev, dtype=bool)
    s = np.asarray(sell_ev, dtype=bool)
    out = np.zeros(len(b), dtype=int)
    cur = 0
    for i in range(len(b)):
        if cur == 0 and b[i]:
            cur = 1
        elif cur == 1 and s[i]:
            cur = 0
        out[i] = cur
    return pd.Series(out, index=buy_ev.index)


def make_sig(px, **opt):
    mid, up, _ = boll.bands(px, N, K)
    ma = px.rolling(MA_WIN).mean()
    if opt.get("v1"):
        s = (px > ma).astype(int)
        s[ma.isna()] = 0
        return s
    cross_up = (px > up) & (px.shift(1) <= up.shift(1))
    buy = cross_up.copy()
    if opt.get("ma_entry"):
        buy &= (px > ma)
    if opt.get("ma_slope"):
        buy &= (ma > ma.shift(MA_WIN))
    sell = px < ma if opt.get("ma_exit_only") else (px < mid)
    if opt.get("ma_exit"):
        sell = sell | (px < ma)
    buy = buy.fillna(False)
    sell = sell.fillna(False)
    # 预热期（中轨/上轨为 NaN）不允许有信号
    need_ma = opt.get("ma_entry") or opt.get("ma_exit") or opt.get("ma_exit_only") or opt.get("ma_slope")
    warm = mid.notna() & ma.notna() if need_ma else mid.notna()
    buy &= warm.fillna(False)
    return state(buy, sell)


def run_leg(px, sig, fee=(0.0, 0.0)):
    ret = px.pct_change().fillna(0.0)
    pos = sig.shift(1).fillna(0).astype(float)
    turn = pos.diff().fillna(0.0)
    cost = turn.clip(lower=0) * fee[0] + (-turn).clip(lower=0) * fee[1]
    r = pos * ret + (1.0 - pos) * CASH_DAILY - cost
    return (1.0 + r).cumprod()


def portfolio(frame, sigs, use_fee=False):
    wr = frame.pct_change().fillna(0.0)
    total = pd.Series(0.0, index=frame.index)
    inv = pd.Series(0.0, index=frame.index)
    for k in frame.columns:
        w = W[k]
        pos = sigs[k].reindex(frame.index).shift(1).fillna(0).astype(float)
        turn = pos.diff().fillna(0.0)
        cost = (turn.clip(lower=0) * FEE[k][0] + (-turn).clip(lower=0) * FEE[k][1]) if use_fee else 0.0
        total += w * (pos * wr[k]) - (w * cost if use_fee else 0.0)
        inv += w * pos
    return (1.0 + total + (1.0 - inv) * CASH_DAILY).cumprod(), float(inv.mean()), \
        float(sum((sigs[k].diff().abs() > 0).sum() for k in frame.columns) / 2.0)


def main():
    px = {k: boll.load(k) for k in LEGS}
    sigs = {name: {k: make_sig(px[k], **opt) for k in LEGS} for name, opt in COMBOS}

    # ---------- 单腿：全样本 / A500 真实段
    print("=" * 100)
    print("单腿（全样本，各自预热后起算）· 不计费 / 扣费后年化")
    md = ["# V2 叠 MA20 的六种组合（同口径对照）", "",
          "口径：场外复权净值 · T+1 净值成交 · 空仓按货基 1.40% · 扣费按真实执行代码"
          "（A 类 0.10%/0.50%，C 类 0）。组合层按目标带归一化（满仓 = 40pp）。", "",
          "## 1. 单腿 · 全样本（年化 / 最大回撤 / 夏普 / 曝光 / 往返/年）", "",
          "| 组合 | " + " | ".join(rule.LEGS[k]["name"] for k in LEGS) + " |",
          "|---|" + "---|" * len(LEGS)]
    for name, _ in COMBOS:
        cells = []
        for k in LEGS:
            nav = run_leg(px[k], sigs[name][k])
            p = engine.metrics(nav)
            e = float(sigs[name][k].shift(1).fillna(0).mean())
            cells.append(f"{p['ann']:+.1%} / {p['mdd']:.0%} / {e:.0%}")
        md.append(f"| {name} | " + " | ".join(cells) + " |")
        print(f"  {name:14s} " + "  ".join(
            f"{rule.LEGS[k]['name'][:4]}:{engine.metrics(run_leg(px[k], sigs[name][k]))['ann']:+.1%}"
            for k in LEGS))

    # ---------- A500 真实段
    cut = pd.Timestamp(rule.LEGS["a500"]["otc_start"])
    print("\nA500 真实段（2025-03-27 起）")
    md += ["", "## 2. A500 · 只算 023299 成立后（2025-03-27 起，桥接段不计）", "",
           "| 组合 | 年化 | 最大回撤 | 夏普 | 累计 | 曝光 | 往返/年 |", "|---|---|---|---|---|---|---|"]
    for name, _ in COMBOS:
        full = run_leg(px["a500"], sigs[name]["a500"])
        seg = full.loc[cut:]
        seg = seg / seg.iloc[0]
        p = engine.metrics(seg)
        e = float(sigs[name]["a500"].loc[cut:].shift(1).fillna(0).mean())
        n = float((sigs[name]["a500"].loc[cut:].diff().abs() > 0).sum()) / 2.0 / 1.6
        md.append(f"| {name} | {p['ann']:+.2%} | {p['mdd']:.2%} | {p['sharpe']:.2f} | "
                  f"{p['cum']:+.1%} | {e:.0%} | {n:.1f} |")
        print(f"  {name:14s} 年化 {p['ann']:+7.2%} 回撤 {p['mdd']:+7.2%} 夏普 {p['sharpe']:5.2f} "
              f"曝光 {e:4.0%} {n:5.1f} 往返/年")

    # ---------- 组合层：共同窗口
    frame = pd.DataFrame({k: px[k] for k in LEGS}).dropna(how="any")
    frame = frame.iloc[max(N, MA_WIN):]
    yrs = (frame.index[-1] - frame.index[0]).days / 365.25
    print(f"\n组合层共同窗口 {frame.index[0].date()} ~ {frame.index[-1].date()}（{yrs:.1f} 年）")
    md += ["", f"## 3. 组合层（六腿按目标带，{frame.index[0].date()} ~ {frame.index[-1].date()}，{yrs:.1f} 年）",
           "", "| 组合 | 不计费 年化 | 回撤 | 夏普 | 累计 | 扣费 年化 | 回撤 | 夏普 | 曝光 | 笔/年 |",
           "|---|---|---|---|---|---|---|---|---|---|"]
    navs, invs, ntrs = {}, {}, {}
    for name, _ in COMBOS:
        nav, inv, ntr = portfolio(frame, sigs[name], use_fee=False)
        navf, _, _ = portfolio(frame, sigs[name], use_fee=True)
        pf, p = engine.metrics(navf), engine.metrics(nav)
        navs[name], invs[name], ntrs[name] = nav, inv, ntr
        md.append(f"| {name} | {p['ann']:+.2%} | {p['mdd']:.2%} | {p['sharpe']:.2f} | {p['cum']:+.1%} | "
                  f"{pf['ann']:+.2%} | {pf['mdd']:.2%} | {pf['sharpe']:.2f} | {inv:.0%} | "
                  f"{ntr / yrs / len(LEGS):.1f} |")
        print(f"  {name:14s} 不计费 {p['ann']:+6.2%}/{p['mdd']:+6.2%}/{p['sharpe']:4.2f} · "
              f"扣费 {pf['ann']:+6.2%}/{pf['mdd']:+6.2%}/{pf['sharpe']:4.2f} · 曝光 {inv:4.0%}")

    # V2 / V1 各半（不是逐腿规则，是资金对半分给两套规则）
    blend = (navs["V2 基准"] + navs["V1 基准"]) / 2.0
    pbl = engine.metrics(blend)
    inv_bl = (invs["V2 基准"] + invs["V1 基准"]) / 2.0
    ntr_bl = ntrs["V2 基准"] + ntrs["V1 基准"]
    print(f"  {'V2/V1 各半':14s} 不计费 {pbl['ann']:+6.2%}/{pbl['mdd']:+6.2%}/{pbl['sharpe']:4.2f}"
          f" · 曝光 {inv_bl:4.0%}")
    md.append(f"| V2/V1 各半（资金对半分） | {pbl['ann']:+.2%} | {pbl['mdd']:.2%} | "
              f"{pbl['sharpe']:.2f} | {pbl['cum']:+.1%} | — | — | — | {inv_bl:.0%} | "
              f"{ntr_bl / yrs / len(LEGS):.1f} |")

    # 静态带参照
    static = (1.0 + frame.pct_change().fillna(0.0).mul(pd.Series(W), axis=1).sum(axis=1)).cumprod()
    ps = engine.metrics(static)
    print(f"  {'静态带(参照)':14s} {ps['ann']:+.2%}/{ps['mdd']:+.2%}/{ps['sharpe']:.2f}")
    md += ["", f"参照：静态带（恒定满仓持有）年化 {ps['ann']:+.2%} / 回撤 {ps['mdd']:.2%} / "
               f"夏普 {ps['sharpe']:.2f}。", ""]

    # ---------- 图
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    import matplotlib.dates as mdates

    plt.rcParams["font.sans-serif"] = ["Microsoft YaHei", "SimHei", "DejaVu Sans"]
    plt.rcParams["axes.unicode_minus"] = False
    colors = ["#7b52ab", "#d9782d", "#1f6fd0", "#c0392b", "#2e8b57", "#888"]
    fig, ax = plt.subplots(figsize=(13.0, 7.0))
    for (name, _), c in zip(COMBOS, colors):
        p = engine.metrics(navs[name])
        ax.plot(navs[name].index, navs[name].to_numpy(), color=c,
                lw=1.6 if name == "V2 基准" else 1.25,
                label=f"{name}  {p['ann']:+.1%}/年 · 回撤 {p['mdd']:.0%} · 夏普 {p['sharpe']:.2f}")
    psr = engine.metrics(static)
    ax.plot(static.index, static.to_numpy(), color="#bbb", lw=1.0, ls="--",
            label=f"静态带  {psr['ann']:+.1%}/年 · 回撤 {psr['mdd']:.0%} · 夏普 {psr['sharpe']:.2f}")
    ax.axhline(1.0, color="#aaa", lw=0.8, ls=":")
    ax.set_ylabel("组合净值（起点 = 1）", fontsize=10)
    ax.grid(ls="--", lw=0.6, alpha=0.4)
    ax.set_axisbelow(True)
    ax.legend(loc="upper left", fontsize=9.5, frameon=False)
    for s in ("top", "right"):
        ax.spines[s].set_visible(False)
    ax.xaxis.set_major_locator(mdates.YearLocator())
    ax.xaxis.set_major_formatter(mdates.DateFormatter("%Y"))
    fig.suptitle("V2（BOLL 上轨突破）叠不叠 MA20 · 六腿组合层（不计费，目标带归一化）"
                 f"    |    {frame.index[0].date()} ~ {frame.index[-1].date()}",
                 x=0.055, ha="left", y=0.965, fontsize=14, color="#1b2b3a", weight="bold")
    fig.text(0.055, 0.015,
             "口径：场外复权净值 · T+1 净值成交 · 空仓按货基 1.40% · 未扣申赎费（扣费版见同名 .md）。"
             "「+MA20 入场过滤」= 上穿上轨且收盘在 MA20 上；「+MA20 出场止损」= 跌破中轨或跌破 MA20。"
             "  ·  机械规则输出，非投资建议。",
             fontsize=8.8, color="#5b6b7a")
    fig.subplots_adjust(left=0.065, right=0.975, top=0.90, bottom=0.11)
    out_png = os.path.join(_HERE, "_v2_ma20_combo.png")
    fig.savefig(out_png, dpi=130, facecolor="white")
    plt.close(fig)
    print("图:", out_png)

    out_md = os.path.join(_HERE, "_v2_ma20_combo.md")
    with open(out_md, "w", encoding="utf-8") as f:
        f.write("\n".join(md))
    print("报告:", out_md)


if __name__ == "__main__":
    main()
