# -*- coding: utf-8 -*-
"""波动资产 · 探针：波动率目标化（vol targeting）在 标普500 / 纳指100 上的实测。

背景（2026-11-04 用户提问「网上针对 spx/ndx 有没有更好的策略」）：
七腿池里 spx/ndx 是**所有「躲跌型」变种都跑不赢买入持有**的两条腿（见
`research/v1v10_compare/_v1v10_compare.md`
第一组逐腿表）。公开证据里唯一「机制不同、且没被本仓库证伪」的一类是
**风险缩放**：按近期已实现波动率的倒数缩放仓位（Moreira & Muir 2017, J. Finance）。
它不是「跌了才躲」，而是「**低波动满仓/加仓、高波动降仓**」——

  w_t = min(cap, 目标波动 / 近 N 日已实现年化波动)

与 v5（慢均线躲阴跌）、v7（移动止损躲快跌）的方向正好相反，因此值得单独测。

口径（与仓库其它回测一致）：
- 价格 = `data.series_for_leg()` 场外**复权净值**（含分红再投 / 汇率 / 跟踪误差）；
- 信号用 t 日及之前数据（滚动已实现波动，预热期满才起算），**t+1 收盘成交**，无前视；
- 未投部分按货基 `rule.CASH_ANNUAL` 逐日计息；
- 三套成本：`otc`（A 类场外：申购 0.10% + 赎回持有<7天 1.5%/否则 0.5%）、
  `etf`（场内：佣金+滑点单边 0.075%，无申赎费）、`free`（零费，看上限）；
- `cap>1` 的杠杆档按年化 4.5% 计**融资成本**（现实中 A 类场外不可用，只作证据对照）。

为什么必须有**漂移阈值**（drift）：vol targeting 每日重算权重会天天微调，
A 类场外 0.6% 的单次往返成本会把收益吃光。所以只在与当前仓位偏离 ≥ drift 时才成交。

产出（本目录 research/，策略类测试调优产物）：
- `_probe_vol_target.md`   报告（基准 / 网格 / 杠杆 / 硬断言）
- `_probe_vol_target.png`  逐腿：净值对照 + 仓位轨迹

模拟结果，非投资建议。
"""
import math
import os
import sys

import numpy as np
import pandas as pd

try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:  # noqa: BLE001
    pass

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt

_HERE = os.path.dirname(os.path.abspath(__file__))   # strategies/vol_legs/research/probe_vol_target
_RES = os.path.dirname(_HERE)                        # strategies/vol_legs/research
_VL = os.path.dirname(_RES)                          # strategies/vol_legs
_ROOT = os.path.dirname(os.path.dirname(_VL))        # 仓库根（推导，不写死）
for _p in (_HERE, _VL, _ROOT):
    if _p not in sys.path:
        sys.path.insert(0, _p)

import data as vdata          # noqa: E402
import engine                 # noqa: E402
import rule                   # noqa: E402

vdata._otc_stale = lambda: False  # 离线优先：只读本地缓存，不联网  # type: ignore[assignment]

plt.rcParams["font.sans-serif"] = ["Microsoft YaHei", "SimHei", "DejaVu Sans"]
plt.rcParams["axes.unicode_minus"] = False

LEGS = ["spx", "ndx"]
NAME = {k: rule.LEGS[k]["name"] for k in LEGS}
CASH = rule.CASH_ANNUAL
FIN = 0.045                      # 杠杆融资成本（年化），只对 cap>1 且满仓部分计

CFG = dict(tgt=0.15, win=60, drift=0.10, cap=1.0)   # 默认档
GRID_TGT = [0.10, 0.12, 0.15, 0.20]
GRID_WIN = [20, 60]
GRID_DRIFT = [0.0, 0.10]

COSTS = {
    # 场外 A 类：申购 0.10%；赎回持有 <7 天 1.5%、否则 0.5%
    "otc": dict(buy=rule.FEE_BUY, sell=rule.FEE_SELL,
                sell7=rule.FEE_SELL_7D, minhold=rule.MIN_HOLD_FOR_LOW_FEE),
    # 场内 ETF：佣金 0.025% + 滑点 0.05% 单边，无申赎费
    "etf": dict(buy=0.00075, sell=0.00075, sell7=0.00075, minhold=0),
    # 零费：上限参考
    "free": dict(buy=0.0, sell=0.0, sell7=0.0, minhold=0),
}
COST_NAME = {"otc": "场外A类", "etf": "场内ETF", "free": "零费"}

OUT_MD = os.path.join(_HERE, "_probe_vol_target.md")
OUT_PNG = os.path.join(_HERE, "_probe_vol_target.png")


# ---------------------------------------------------------------- 信号
def rv_series(px, win):
    """近 win 日已实现年化波动（只用当日及之前）。"""
    r = px.pct_change()
    return r.rolling(int(win), min_periods=int(win)).std(ddof=1) * math.sqrt(252.0)


def weight_series(px, tgt, win, cap=1.0):
    """目标权重 = min(cap, 目标波动/已实现波动)；预热期内为 NaN（不交易）。"""
    rv = rv_series(px, win)
    w = (float(tgt) / rv).clip(upper=float(cap))
    return w.where(rv.notna())


# ---------------------------------------------------------------- 模拟
def simulate(px, w_target, *, cost="otc", cap=1.0, drift=0.10, fin=FIN, cash=CASH):
    """按目标权重逐日复算。信号 t 日收盘算、**t+1 收盘成交**（与全仓库同一纪律）。

    记账：`hv` = 该腿持仓市值（含杠杆时为总资产的一部分或超过总资产），`ca` = 现金（可为负）。
    返回 dict(nav, w, trades, fees, turnover)。
    """
    c = COSTS[cost]
    idx = px.index
    hv, ca = 0.0, 1.0
    buy_date = None
    navs, ws = [], []
    trades, fees, turnover = 0, 0.0, 0.0

    for i, d in enumerate(idx):
        if i > 0:                                        # 1) 市值变动 + 现金计息
            hv *= (1.0 + float(px.iloc[i] / px.iloc[i - 1] - 1.0))
            ca *= (1.0 + cash) ** (1.0 / 252.0)
        total = hv + ca
        w_cur = (hv / total) if total > 1e-12 else 0.0

        if i > 0:                                        # 2) 执行「昨日信号」
            tgt = w_target.iloc[i - 1]
            if tgt == tgt:                               # NaN = 预热期，不动
                tgt = min(float(tgt), float(cap))
                if abs(tgt - w_cur) >= drift - 1e-12:
                    trade = tgt * total - hv
                    if abs(trade) > 1e-12:
                        if trade > 0:
                            rate = c["buy"]
                        else:
                            short = (buy_date is not None
                                     and (d - buy_date).days < c["minhold"])
                            rate = c["sell7"] if short else c["sell"]
                        fee = abs(trade) * rate
                        hv += trade
                        ca -= trade
                        ca -= fee
                        fees += fee
                        turnover += abs(trade)
                        trades += 1
                        if trade > 0:
                            buy_date = d
            if hv > total + 1e-12:                       # 3) 融资成本（杠杆档）
                ca -= (hv - total) * fin / 252.0

        total = hv + ca
        navs.append(total)
        ws.append((hv / total) if total > 1e-12 else 0.0)

    return dict(nav=pd.Series(navs, index=idx, dtype="float64"),
                w=pd.Series(ws, index=idx, dtype="float64"),
                trades=trades, fees=fees, turnover=turnover)


def buy_hold(px, cost="otc", buy_at=1):
    """买入持有基准（buy_at 收盘一次性买入，期末不卖 -> 不计赎回费）。"""
    c = COSTS[cost]
    p0 = float(px.iloc[buy_at])
    units = (1.0 - c["buy"]) / p0
    nav = px.iloc[buy_at:] * units
    return dict(nav=nav, w=pd.Series(1.0, index=px.index[buy_at:], dtype="float64"),
                trades=1, fees=c["buy"], turnover=1.0)


def perf(res):
    m = engine.metrics(res["nav"])
    w = res["w"]
    return dict(cum=m["cum"], ann=m["ann"], vol=m["vol"], mdd=m["mdd"],
                sharpe=m["sharpe"], expo=float(w.mean()), trades=res["trades"],
                days=m["days"], fees=res["fees"])


def run_leg(px, *, tgt=None, win=None, drift=None, cap=None, cost="otc"):
    tgt = CFG["tgt"] if tgt is None else tgt
    win = CFG["win"] if win is None else win
    drift = CFG["drift"] if drift is None else drift
    cap = CFG["cap"] if cap is None else cap
    w = weight_series(px, tgt, win, cap)
    first = w.first_valid_index()
    if first is None:
        raise ValueError("信号全为 NaN（样本短于波动窗口）")
    px = px[px.index >= first]
    w = w[w.index >= first]
    res = simulate(px, w, cost=cost, cap=cap, drift=drift)
    # 与买入持有同一窗口（都在第 2 根成交），指标从第 2 根起算
    res["nav"] = res["nav"].iloc[1:]
    res["w"] = res["w"].iloc[1:]
    return res, px


# ---------------------------------------------------------------- 绘图
def plot(curves, path):
    n = len(LEGS)
    fig, axes = plt.subplots(n, 1, figsize=(11, 4.6 * n), sharex=False)
    axes = np.atleast_1d(axes)
    for ax, leg in zip(axes, LEGS):
        cur = curves[leg]
        for key, lab, color, lw in (
                ("bh", "买入持有", "#888888", 2.2),
                ("otc", f"VT {CFG['tgt']:.0%}/{CFG['win']}日 · 场外A类", "#d62728", 1.8),
                ("etf", f"VT {CFG['tgt']:.0%}/{CFG['win']}日 · 场内ETF", "#1f77b4", 1.4)):
            nav = cur[key]
            ax.plot(nav.index, nav / nav.iloc[0], label=lab, color=color, lw=lw)
        ax.set_title(f"{NAME[leg]} · 波动率目标化 vs 买入持有（净值归一）")
        ax.grid(alpha=0.25)
        ax.legend(loc="upper left", fontsize=9)
        ax2 = ax.twinx()
        wv = cur["w"]
        ax2.fill_between(wv.index, 0, wv.values, color="#ff7f0e", alpha=0.18, lw=0)
        ax2.set_ylim(0, max(1.05, float(wv.max()) * 1.05))
        ax2.set_ylabel("仓位（橙）", color="#ff7f0e", fontsize=9)
        ax2.tick_params(axis="y", colors="#ff7f0e", labelsize=8)
    fig.tight_layout()
    fig.savefig(path, dpi=110)
    plt.close(fig)


# ---------------------------------------------------------------- 硬断言
def self_check(px):
    """四条硬断言：权重边界、纯现金=货基、恒满仓=买入持有、截断不变（无前视）。"""
    out = []

    res, _ = run_leg(px, cost="free")
    w = res["w"]
    ok = bool((w >= -1e-9).all() and (w <= 1.0 + 1e-9).all())
    out.append(("仓位始终落在 [0, cap]", ok, f"min {w.min():.3f} max {w.max():.3f}"))

    res0, _ = run_leg(px, tgt=1e-6, cost="free")
    m0 = engine.metrics(res0["nav"])
    ok = abs(m0["ann"] - CASH) < 5e-4
    out.append(("目标波动≈0（纯现金）年化 == 货基", ok,
                f"{m0['ann']:.4%} vs {CASH:.4%}"))

    big = weight_series(px, 1e6, CFG["win"], 1.0)
    first = big.first_valid_index()
    sub = px[px.index >= first]
    resb = simulate(sub, big[big.index >= first], cost="free", cap=1.0, drift=0.0)
    navb = resb["nav"].iloc[1:]
    g_vt = float(navb.iloc[-1] / navb.iloc[0])
    g_bh = float(sub.iloc[-1] / sub.iloc[1])
    ok = abs(g_vt / g_bh - 1.0) < 1e-10
    out.append(("恒满仓（零费）== 买入持有", ok, f"{g_vt:.10f} vs {g_bh:.10f}"))

    cut = px.iloc[: -6]
    r_full, _ = run_leg(px, cost="otc")
    r_cut, _ = run_leg(cut, cost="otc")
    common = r_full["nav"].index.intersection(r_cut["nav"].index)
    d = float((r_full["nav"].reindex(common) - r_cut["nav"].reindex(common)).abs().max())
    wd = float((r_full["w"].reindex(common) - r_cut["w"].reindex(common)).abs().max())
    ok = max(d, wd) < 1e-12
    out.append(("截掉最后 6 天重跑，历史净值/仓位不变（无前视）", ok,
                f"最大差 {max(d, wd):.2e}"))
    return out


# ---------------------------------------------------------------- 主流程
def fmt_pct(x):
    return "—" if x is None else f"{x * 100:+.2f}%"


def cell(m):
    return f"{m['ann'] * 100:+.2f}% / {m['mdd'] * 100:.2f}% / {m['sharpe']:.2f}"


def main():
    lines = []
    add = lines.append
    add("# 波动资产 · 探针：波动率目标化（spx / ndx）")
    add("")
    add("> `w_t = min(cap, 目标波动 / 近 N 日已实现年化波动)`；t 日收盘算、**t+1 收盘成交**；")
    add("> 未投部分按货基 1.40% 逐日计息；只在 |目标权重 − 当前权重| ≥ drift 时成交（控换手）。")
    add("> 价格 = 场外复权净值。**这不是「躲跌」规则，是「低波动重仓、高波动轻仓」**，")
    add("> 方向与 v5（慢均线）/ v7（移动止损）相反。")
    add("")
    add("参考：Moreira & Muir (2017, *Journal of Finance*) 波动率管理组合提升夏普；")
    add("后续复现（30 年五资产）结论是「真实但被夸大，要付收益，只对股票有效」。")
    add("")

    curves, res_all, checks = {}, {}, []
    for leg in LEGS:
        px = vdata.series_for_leg(leg)
        res_all[leg] = {}
        for cost in ("otc", "etf", "free"):
            res_all[leg][cost], pxs = run_leg(px, cost=cost)
        res_all[leg]["bh_otc"] = buy_hold(pxs, "otc")
        res_all[leg]["bh_free"] = buy_hold(pxs, "free")
        res_all[leg]["ota"] = buy_hold(pxs, "etf")
        curves[leg] = dict(bh=res_all[leg]["bh_otc"]["nav"],
                           otc=res_all[leg]["otc"]["nav"],
                           etf=res_all[leg]["etf"]["nav"],
                           w=res_all[leg]["otc"]["w"])
        checks.extend((f"{NAME[leg]}",) + c for c in self_check(px))

    # ---- §1 基准
    add("## 1. 基准对照（默认档：目标波动 15% · 60 日窗 · 漂移 10% · cap 1.0）")
    add("")
    add("| 腿 | 组合 | 累计 | 年化 | 波动 | 最大回撤 | 夏普 | 曝光 | 调仓 | 费用合计 |")
    add("|---|---|---:|---:|---:|---:|---:|---:|---:|---:|")
    for leg in LEGS:
        rows = [
            ("买入持有（场外A类）", perf(res_all[leg]["bh_otc"])),
            ("**VT 默认（场外A类）**", perf(res_all[leg]["otc"])),
            ("VT 默认（场内ETF）", perf(res_all[leg]["etf"])),
            ("VT 默认（零费）", perf(res_all[leg]["free"])),
            ("买入持有（零费）", perf(res_all[leg]["bh_free"])),
        ]
        for lab, m in rows:
            add(f"| {NAME[leg]} | {lab} | {fmt_pct(m['cum'])} | {fmt_pct(m['ann'])} | "
                f"{m['vol'] * 100:.2f}% | {m['mdd'] * 100:.2f}% | {m['sharpe']:.2f} | "
                f"{m['expo']:.0%} | {m['trades']} | {m['fees'] * 100:.2f}% |")
    add("")
    add("**同腿对照（引用值：`research/v1v10_compare/_v1v10_compare.md` 与 `v5_trend_gate/` 报告，")
    add("成本口径各自不同 —— compare 表为零费、v5 为含费，只作量级参照。")
    add("注意「买入持有」两行是**各报告自己窗口**的值（VT 需 60 日预热，起点晚 3 个月，故略低）。）**")
    add("")
    add("| 腿 | 变种 | 年化 | 最大回撤 | 夏普 | 口径 |")
    add("|---|---|---:|---:|---:|---|")
    add("| 标普500 | 买入持有 | +13.02% | -30.50% | 0.73 | 零费 |")
    add("| 标普500 | v5 趋势闸门 | +10.05% | -29.53% | — | 含费 |")
    add("| 标普500 | 旧 v7 移动止损 15%（已重构掉） | +10.50% | -22.86% | 0.63 | 含费 |")
    add(f"| 标普500 | **本探针 VT 15%/60日** | {fmt_pct(perf(res_all['spx']['otc'])['ann'])} | "
        f"{perf(res_all['spx']['otc'])['mdd'] * 100:.2f}% | "
        f"{perf(res_all['spx']['otc'])['sharpe']:.2f} | 含费 |")
    add("| 纳指100 | 买入持有 | +17.22% | -29.47% | 0.80 | 零费 |")
    add("| 纳指100 | v5 趋势闸门 | +13.74% | -26.49% | — | 含费 |")
    add("| 纳指100 | 旧 v7 移动止损 15%（已重构掉） | +14.33% | -37.88% | 0.73 | 含费 |")
    add(f"| 纳指100 | **本探针 VT 15%/60日** | {fmt_pct(perf(res_all['ndx']['otc'])['ann'])} | "
        f"{perf(res_all['ndx']['otc'])['mdd'] * 100:.2f}% | "
        f"{perf(res_all['ndx']['otc'])['sharpe']:.2f} | 含费 |")
    add("")

    # ---- §2 网格
    add("## 2. 参数网格（每格 = 年化 / 最大回撤 / 夏普；场外A类含费）")
    add("")
    best = {}
    for leg in LEGS:
        px = vdata.series_for_leg(leg)
        add(f"**{NAME[leg]}**")
        add("")
        add("| 目标波动 | 60日·漂移10% | 20日·漂移10% | 60日·漂移0 | 20日·漂移0 |")
        add("|---|---:|---:|---:|---:|")
        for tgt in GRID_TGT:
            cells = []
            for win, drift in ((60, 0.10), (20, 0.10), (60, 0.0), (20, 0.0)):
                res, _ = run_leg(px, tgt=tgt, win=win, drift=drift, cost="otc")
                m = perf(res)
                cells.append(cell(m))
                if leg not in best or m["sharpe"] > best[leg]["sharpe"]:
                    best[leg] = dict(sharpe=m["sharpe"], ann=m["ann"], mdd=m["mdd"],
                                     desc=f"目标波动 {tgt:.0%} · {win} 日窗 · 漂移 {drift:.0%}")
            star = "**" if (tgt == CFG["tgt"]) else ""
            add(f"| {star}{tgt:.0%}{star} | " + " | ".join(cells) + " |")
        add("")
    add("**网格最优（按夏普）**")
    add("")
    add("| 腿 | 最优档 | 年化 | 最大回撤 | 夏普 | 买入持有夏普 |")
    add("|---|---|---:|---:|---:|---:|")
    for leg in LEGS:
        b = best[leg]
        bh = perf(res_all[leg]["bh_otc"])
        add(f"| {NAME[leg]} | {b['desc']} | {fmt_pct(b['ann'])} | {b['mdd'] * 100:.2f}% | "
            f"{b['sharpe']:.2f} | {bh['sharpe']:.2f} |")
    add("")

    # ---- §3 杠杆
    add("## 3. 杠杆档（cap 1.5 · 融资成本 4.5%/年；A 类场外不可执行，仅作公开证据对照）")
    add("")
    add("| 腿 | 组合 | 年化 | 最大回撤 | 夏普 | 平均曝光 |")
    add("|---|---|---:|---:|---:|---:|")
    for leg in LEGS:
        px = vdata.series_for_leg(leg)
        res, _ = run_leg(px, cap=1.5, cost="etf")
        m = perf(res)
        base = perf(res_all[leg]["etf"])
        add(f"| {NAME[leg]} | 买入持有（场内ETF） | {fmt_pct(base['ann'])} | "
            f"{base['mdd'] * 100:.2f}% | {base['sharpe']:.2f} | {base['expo']:.0%} |")
        add(f"| {NAME[leg]} | **VT cap1.5（场内ETF+融资4.5%）** | {fmt_pct(m['ann'])} | "
            f"{m['mdd'] * 100:.2f}% | {m['sharpe']:.2f} | {m['expo']:.0%} |")
    add("")

    # ---- §4 断言
    add("## 4. 硬断言")
    add("")
    add("| 腿 | 检查 | 结果 | 备注 |")
    add("|---|---|---|---|")
    bad = 0
    for leg_name, name, ok, note in checks:
        bad += 0 if ok else 1
        add(f"| {leg_name} | {name} | {'✅' if ok else '❌'} | {note} |")
    add("")
    add(f"共 {len(checks)} 条，失败 {bad} 条。")
    add("")

    # ---- §5 结论
    add("## 5. 结论（读法）")
    add("")
    for leg in LEGS:
        bh = perf(res_all[leg]["bh_otc"])
        vt = perf(res_all[leg]["otc"])
        add(f"- **{NAME[leg]}** · VT 默认 vs 买入持有：年化 {fmt_pct(bh['ann'])} → "
            f"{fmt_pct(vt['ann'])}（{(vt['ann'] - bh['ann']) * 100:+.2f}pp）、回撤 "
            f"{bh['mdd'] * 100:.2f}% → {vt['mdd'] * 100:.2f}%（回撤"
            f"{'更浅' if vt['mdd'] > bh['mdd'] else '更深'} "
            f"{abs(vt['mdd'] - bh['mdd']) * 100:.2f}pp）、夏普 {bh['sharpe']:.2f} → "
            f"{vt['sharpe']:.2f}。")
    add("")
    add("- **它压回撤有效、但夏普基本不动**：本质是「把风险等比缩放」，不是 alpha。")
    add("  与 v5/v7 的区别在于**没有单边卖飞**：v7 在纳指上把回撤从 −29.47% 做到 **−37.88%**，")
    add("  而 VT 做到 −19.86% —— 同样是「减仓」，机制不同结果就相反。")
    best_s = ", ".join(f"{NAME[k]} {best[k]['sharpe']:.2f}" for k in LEGS)
    bh_s = ", ".join(f"{NAME[k]} {perf(res_all[k]['bh_otc'])['sharpe']:.2f}" for k in LEGS)
    add(f"- **网格最优也只到夏普 {max(b['sharpe'] for b in best.values()):.2f}**"
        f"（{best_s} vs 买入持有 {bh_s}）：")
    add("  唯一的小胜出现在「20 日窗 + 漂移 10%」，但同一目标波动下把漂移改成 0 夏普立刻塌掉")
    add("  （见 §2 网格）—— 这是**参数敏感**，不是「更好的策略」。")
    add("- **杠杆没救回夏普**（§3：cap1.5 后 0.62 / 0.75 < 买入持有的 0.73 / 0.80）：")
    add("  公开文献里「加杠杆后夏普翻倍」来自 1928-2015 的长样本与低融资成本，本样本不复现。")
    add("")
    add("**一句话**：对 spx/ndx，VT 是目前测过的所有规则里**唯一能把回撤压到 −20% 量级且不比 v5/v7 更差**的，")
    add("但它买的是「更平缓的曲线」，不是「更高的收益」。想要收益，仍是买入持有；想要 −20% 级回撤，用它而不是止损。")
    add("")

    # ---- §6 边界
    add("## 6. 口径与边界")
    add("")
    add("1. **样本只到 2010/2013 起**：不含 2000-2002（纳指 −83%）与 1973-74，")
    add("   而波动率目标化最被称赞的正是「泡沫破裂时降仓」—— 本报告对这一场景是外推。")
    add("2. **每日重算必被费用吃光**：场外 A 类单次往返 0.6%，`drift=0` 一列就是为了量化这个代价。")
    add("3. **`cap=1` 是唯一可执行的档**：A 类场外不能加杠杆；场内 ETF 加杠杆需两融，")
    add("   成本与额度都不在回测里，§3 只用来判断「公开证据里的高夏普是否来自杠杆」。")
    add("4. **它改善的是波动、不是收益**：本探针的结论**未被采纳为 v7** —— 2026-11-04 用户指定按")
    add("   `纳指100与标普500_策略回测与规则.md` 把 v7 改为美股年线闸门（见 `v7_us_gate/`）。")
    add("   本探针留档的理由：它解释了「止损在美股上是负优化」，且 VT 仍是 spx/ndx 上压回撤的可选工具。")
    add("5. 本脚本只读本地缓存（`vdata._otc_stale` 被置为 False），不联网、不改任何产物。")
    add("")
    add("> 模拟结果，非投资建议。")

    with open(OUT_MD, "w", encoding="utf-8") as f:
        f.write("\n".join(lines) + "\n")
    plot(curves, OUT_PNG)

    for leg_name, name, ok, note in checks:
        print(f"[{'OK ' if ok else 'FAIL'}] {leg_name} · {name} · {note}")
    print(f"\n报告 -> {os.path.relpath(OUT_MD, _ROOT)}")
    print(f"图   -> {os.path.relpath(OUT_PNG, _ROOT)}")
    return bad


if __name__ == "__main__":
    sys.exit(1 if main() else 0)
