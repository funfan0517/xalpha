# -*- coding: utf-8 -*-
"""V8（美股两腿金叉 50/200）· 专项：**给它加一层波动层，效果如何**。

> **状态（2026-11-05）**：本探针的结论已被采纳 —— v8 默认档切到「金叉 × 波动层 TV20」
> （`golden_cross.VOL_ON=True`），纯二元档退化为对照（`VOL_ON=False`）；
> 「只留波动层、去掉金叉门」那条曲线则单独成支 → `v10_vol_layer`。
> 报告里的数字仍是本文件的原始实测（与 `backtest.py` 逐位一致）。

## 问题

改之前的 v8 是纯二元（`MA50 > MA200` → 100% / 0%），7 条硬断言全过，但结论是**短样本里最差的一支**：
14.2 年年化 +11.34%、日频回撤 −28.17%（与买入持有完全相同）、夏普 0.66。
诊断很清楚 —— **它不是被「打脸太多」拖死的，是被「转向太晚」拖死的**：
2020 那种 5 周跌完的行情里，200 日慢线要到 2020-04-30 才翻空（离底部一个月），
**方向信号来得太晚，而它又没有别的刹车**。

v7 的解法是「波动层」：`目标权重 = 闸门 × min(1, TV / vol20)`，月末定、死区 10pp。
本探针回答：**把这层（以及几种「自适应」变体）装到 v8 上，能不能把那天花板抬起来。**

## 「自适应」在这里指五种不同的事（分开测，别混）

| 记号 | 自适应的是什么 | 定义 |
|---|---|---|
| `fixed-TV` | 什么都不自适应（基线） | `w = 金叉 × min(1, TV/vol20)`，TV ∈ {12,15,18,20,22,25}% |
| `vol=max(20,60)` | **估计子** | 分母取 `max(vol20, vol60)`：波动一起就反应，回落时慢一点 |
| `vol=ewma94` | **估计子** | RiskMetrics 口径 `sqrt(EWMA(r², λ=0.94))`，半衰期约 11 天 |
| `vol=blend` | **估计子** | `0.5×vol20 + 0.5×vol60` |
| `TV∝vol250/中位` | **目标**（regime） | `TV_t = TV0 × vol250_t / 扩张中位数`，夹在 [0.5, 2]×TV0：长期高波动期放宽目标 |
| `分位五档` | **档位** | `vol20` 的 3 年滚动分位 → 100/75/50/25/0% 五档（低波动满仓） |

另有两个**分解对照**（回答「这层到底在做什么」）：

- `只波动层 · 无金叉门`：去掉方向判断，只留 `min(1, TV/vol20)` —— 看波动层自己赚不赚钱
  （**这条后来被单独做成 `v10_vol_layer`**，两处实现必须逐位一致，见其回测断言 7）；
- `v7 年线闸门`（同尺子重算）：v7 = 「MA200 + 2 月确认」门 × 同一层波动层 ——
  用来回答「**同样的波动层，配 50/200 金叉门还是配年线门更好**」。

## 口径

与 `backtest.py` 完全一致：信号 = 美元指数收盘、执行 = 场外 QDII 复权净值、
T 日决策 → T+1 收盘成交 → 收益自 T+2 起、单边 0.1%、空仓按货基 1.40% 计息、两腿等权 50/50。
**死区 10pp 只在「金叉状态没变」时生效**（与 v7 的 `VOL_DEAD` 同语义）。

产物：`_probe_vol_layer.md` / `_probe_vol_layer.png`。
模拟结果，非投资建议。
"""
import json
import math
import os
import sys

import numpy as np
import pandas as pd

try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:  # noqa: BLE001
    pass

_HERE = os.path.dirname(os.path.abspath(__file__))
_V8 = os.path.dirname(_HERE)
_VL = os.path.dirname(_V8)
for _p in (_V8, os.path.join(_VL, "v7_us_gate")):
    if _p not in sys.path:
        sys.path.insert(0, _p)

import golden_cross as gc  # noqa: E402

OUT_MD = os.path.join(_HERE, "_probe_vol_layer.md")
OUT_PNG = os.path.join(_HERE, "_probe_vol_layer.png")
OUT_JSON = os.path.join(_HERE, "_probe_vol_layer.json")

COST = gc.COST                 # 0.1% 单边
CASH = gc.CASH_ANNUAL          # 1.40%
DEAD = 0.10                    # 死区（闸门未变时）
TV0 = 0.20                     # 固定目标波动基准

WINDOWS = [
    ("2015 加息预期", "2015-06-01", "2016-02-29"),
    ("2018Q4 急跌", "2018-09-20", "2018-12-31"),
    ("2020 疫情", "2020-02-19", "2020-08-31"),
    ("2022 加息熊市", "2022-01-03", "2022-12-30"),
    ("2025 至今年内", "2025-01-02", "2026-11-04"),
]


# ---------------------------------------------------------------- 1. 波动率估计子
def vol_std(sig, win):
    return sig.pct_change().rolling(int(win), min_periods=int(win)).std(ddof=1) * math.sqrt(252.0)


def vol_ewma(sig, lam=0.94):
    r = sig.pct_change()
    var = (r * r).ewm(alpha=1.0 - lam, adjust=False, min_periods=20).mean()
    return np.sqrt(var) * math.sqrt(252.0)


def vol_of(sig, kind):
    """按名字取波动率序列（全部只用滚动窗口，无前视）。"""
    if kind == "std20":
        return vol_std(sig, 20)
    if kind == "std10":
        return vol_std(sig, 10)
    if kind == "std60":
        return vol_std(sig, 60)
    if kind == "max2060":
        return pd.concat([vol_std(sig, 20), vol_std(sig, 60)], axis=1).max(axis=1)
    if kind == "blend":
        return 0.5 * vol_std(sig, 20) + 0.5 * vol_std(sig, 60)
    if kind == "ewma94":
        return vol_ewma(sig, 0.94)
    raise ValueError(kind)


def tv_regime(sig, tv0=TV0, win=250, lo=0.5, hi=2.0):
    """目标自适应：`TV_t = tv0 × vol250_t / 扩张中位数`（只用当日及以前的数据）。"""
    vl = vol_std(sig, win)
    med = vl.expanding(min_periods=120).median()
    return (tv0 * vl / med).clip(lower=tv0 * lo, upper=tv0 * hi)


def pct_scale(sig, win=750, cuts=(0.2, 0.4, 0.6, 0.8), levels=(1.0, 0.75, 0.5, 0.25, 0.0)):
    """档位自适应：vol20 的滚动分位 → 五档仓位（**低波动满仓、高波动空仓**，不含方向）。"""
    v = vol_std(sig, 20)
    pct = v.rolling(int(win), min_periods=252).rank(pct=True)
    out = pd.Series(levels[0], index=sig.index, dtype="float64")
    for i, c in enumerate(cuts):
        out[pct > c] = levels[i + 1]
    out[pct.isna()] = 1.0
    return out


# ---------------------------------------------------------------- 2. 状态（含死区）
def deadzone(base, tgt, dead=DEAD):
    """闸门（金叉状态）未变时，`|新目标 − 现仓| < dead` 就不动手；闸门一变立刻执行。"""
    b = base.to_numpy(dtype=float)
    g = tgt.to_numpy(dtype=float)
    out = np.empty(len(b))
    hold, prev = float(g[0]), float(b[0])
    out[0] = hold
    for i in range(1, len(b)):
        if abs(b[i] - prev) > 1e-12 or abs(g[i] - hold) >= float(dead) - 1e-12:
            hold = g[i]
        out[i] = hold
        prev = b[i]
    return pd.Series(out, index=base.index, dtype="float64")


def to_nav_calendar(state, nav_index):
    """信号日历 → 净值日历：**次一净值日成交**（与 `golden_cross.state_daily` 同一纪律）。"""
    idx = pd.DatetimeIndex(nav_index)
    return state.reindex(idx).ffill().fillna(1.0).shift(1).fillna(1.0)


def build(leg, tv=None, volkind="std20", dead=DEAD, gate=True, pct=False, regime=False):
    """-> (nav, state)。`gate=False` = 只波动层（无金叉门）。"""
    sig = gc.load_signal(leg)
    nav = gc.load_nav(leg)
    base = gc.golden_raw(leg) if gate else pd.Series(1.0, index=sig.index)
    if pct:
        scale = pct_scale(sig)
    else:
        v = vol_of(sig, volkind)
        t = tv_regime(sig, float(tv if tv is not None else TV0)) if regime \
            else pd.Series(float(tv if tv is not None else TV0), index=sig.index)
        scale = (t / v).clip(upper=1.0).fillna(1.0)
    tgt = (base.astype(float) * scale).clip(0.0, 1.0)
    return nav, to_nav_calendar(deadzone(base, tgt, dead), nav.index)


# ---------------------------------------------------------------- 3. 组合层
def run(states_fn):
    """-> (navs, states, idx)。`states_fn(leg)` -> 该腿的仓位序列（信号日历或净值日历均可）。"""
    navs, states = {}, {}
    idx = None
    for leg in gc.POOL:
        navs[leg] = gc.load_nav(leg)
        states[leg] = states_fn(leg)
        idx = navs[leg].index if idx is None else idx.intersection(navs[leg].index)
    idx = pd.DatetimeIndex(idx).sort_values()
    return ({k: v.reindex(idx).ffill() for k, v in navs.items()},
            {k: v.reindex(idx).ffill() for k, v in states.items()}, idx)


def row(name, navs, states, idx, note=""):
    nv, expo = gc.portfolio(navs, states, cost=COST, cash=CASH)
    m = gc.perf(nv)
    me = gc.mdd_sampled(nv)
    return dict(name=name, note=note, ann=m["ann"], vol=m["vol"], mdd=m["mdd"], mdd_me=me,
                calmar=gc.calmar(nv, me), sharpe=m["sharpe"], expo=expo,
                trades=sum(gc.n_trades(s) for s in states.values()),
                turnover=sum(gc.turnover(s) for s in states.values()), nav=nv)


def metrics_for(fn):
    navs, states, idx = run(fn)
    return navs, states, idx


def fixed(tv=TV0, volkind="std20", dead=DEAD, gate=True, pct=False, regime=False):
    return lambda leg: build(leg, tv=tv, volkind=volkind, dead=dead, gate=gate,
                             pct=pct, regime=regime)[1]


def main():
    rows = []
    # 基线用**旧默认**（VOL_ON=False，纯二元金叉）。2026-11-05 起 v8 默认已含波动层，
    # 所以这里必须显式关掉，否则「基线」与 TV20 行会变成同一条曲线。
    navs0, states0, idx = metrics_for(
        lambda leg: gc.state_daily(gc.load_nav(leg).index, leg, gc.params(VOL_ON=False)))
    rows.append(row("静态 50/50（买入持有）", navs0,
                    {k: pd.Series(1.0, index=idx) for k in gc.POOL}, idx, "无信号"))
    rows.append(row("**v8 旧默认（纯二元金叉 · VOL_ON=False）**", navs0, states0, idx, "基线"))

    # --- 固定 TV 邻域
    for tv in (0.12, 0.15, 0.18, 0.20, 0.22, 0.25):
        nv, st, _ = metrics_for(fixed(tv))
        rows.append(row(f"金叉 × 波动层 TV {tv:.0%}（vol20 · 死区 10pp）", nv, st, idx,
                        "fixed-TV"))

    # --- 估计子自适应（TV 都取 20%）
    for kind, label in (("max2060", "vol = max(vol20, vol60)"),
                        ("blend", "vol = 0.5·vol20 + 0.5·vol60"),
                        ("ewma94", "vol = EWMA(λ=0.94)"),
                        ("std10", "vol = vol10（更快）"),
                        ("std60", "vol = vol60（更慢）")):
        nv, st, _ = metrics_for(fixed(TV0, volkind=kind))
        rows.append(row(f"金叉 × 自适应估计子 · {label}", nv, st, idx, "估计子"))

    # --- 目标自适应（regime）
    for tv in (0.18, 0.20, 0.22):
        nv, st, _ = metrics_for(fixed(tv, regime=True))
        rows.append(row(f"金叉 × 自适应目标 TV{idx_label(tv)} ∝ vol250/中位", nv, st, idx,
                        "目标自适应"))

    # --- 档位自适应（分位五档）
    nv, st, _ = metrics_for(fixed(pct=True))
    rows.append(row("金叉 × 自适应档位（vol20 三年分位 → 100/75/50/25/0）", nv, st, idx,
                    "档位自适应"))

    # --- 分解对照
    nv, st, _ = metrics_for(fixed(TV0, gate=False))
    rows.append(row("只波动层 TV20（**无金叉门**）", nv, st, idx, "分解"))
    nv, st, _ = metrics_for(fixed(TV0, dead=0.0))
    rows.append(row("金叉 × TV20 · 死区 0pp", nv, st, idx, "死区"))
    nv, st, _ = metrics_for(fixed(TV0, dead=0.20))
    rows.append(row("金叉 × TV20 · 死区 20pp", nv, st, idx, "死区"))

    # --- v7 参照（同尺子）
    import us_gate as ug  # noqa: E402
    navs7, states7 = {}, {}
    for leg in gc.POOL:
        nav, st, _ = ug.build(leg)
        navs7[leg], states7[leg] = nav, st
    idx7 = pd.DatetimeIndex(navs0[gc.POOL[0]].index)
    navs7 = {k: v.reindex(idx7).ffill() for k, v in navs7.items()}
    states7 = {k: v.reindex(idx7).ffill() for k, v in states7.items()}
    rows.append(row("v7 年线闸门（MA200 + 2 月确认 + 深破 + TV20）", navs7, states7, idx7,
                    "参照"))

    # --- 压力窗口 / 逐年 / 分半
    by = {r["name"]: r for r in rows}
    base = by["**v8 旧默认（纯二元金叉 · VOL_ON=False）**"]
    stat = by["静态 50/50（买入持有）"]
    best_tv = by["金叉 × 波动层 TV 20%（vol20 · 死区 10pp）"]
    best_est = max((r for r in rows if r["note"] == "估计子"), key=lambda r: r["sharpe"])
    best_reg = max((r for r in rows if r["note"] == "目标自适应"), key=lambda r: r["sharpe"])
    pctl = by["金叉 × 自适应档位（vol20 三年分位 → 100/75/50/25/0）"]
    vonly = by["只波动层 TV20（**无金叉门**）"]
    v7 = by["v7 年线闸门（MA200 + 2 月确认 + 深破 + TV20）"]

    wins = {}
    for label, a, b in WINDOWS:
        cells = {}
        for nm, r in (("默认", base), ("TV20", best_tv), (best_est["name"], best_est),
                      ("v7", v7), ("静态", stat)):
            seg = gc.window_nav(r["nav"], a, b)
            cells[nm] = (float(seg.iloc[-1] / seg.iloc[0] - 1.0), gc.perf(seg)["mdd"]) \
                if len(seg) > 5 else (None, None)
        wins[label] = cells

    yrs = (idx[-1] - idx[0]).days / 365.25
    mid = idx[len(idx) // 2]

    def half(nv, lo, hi):
        seg = nv.loc[(nv.index >= lo) & (nv.index <= hi)]
        seg = seg / seg.iloc[0]
        m = gc.perf(seg)
        return m["ann"], m["mdd"], m["sharpe"]

    yr_rows = []
    for y in range(2012, 2027):
        a = f"{y}-01-01"
        b = f"{y}-12-31"
        vals = {}
        for nm, r in (("默认", base), ("TV20", best_tv), ("v7", v7), ("静态", stat)):
            seg = gc.window_nav(r["nav"], a, b)
            vals[nm] = float(seg.iloc[-1] / seg.iloc[0] - 1.0) if len(seg) > 5 else None
        yr_rows.append((y, vals))

    plot([stat, base, best_tv, vonly, best_est, best_reg, pctl, v7], idx)
    write_md(rows, by, base, stat, best_tv, best_est, best_reg, pctl, vonly, v7, wins,
             yr_rows, half, mid, yrs, idx)

    with open(OUT_JSON, "w", encoding="utf-8") as f:
        for r in rows:
            f.write(json.dumps({"variant": r["name"], "kind": r["note"],
                                **{k: (round(v, 4) if isinstance(v, float) else v)
                                   for k, v in r.items()
                                   if k not in ("name", "note", "nav")}},
                               ensure_ascii=False) + "\n")
        f.write(json.dumps({"windows": wins}, ensure_ascii=False) + "\n")

    print("报告:", OUT_MD)
    print("图:", OUT_PNG)
    print(f"窗口 {idx[0].date()} ~ {idx[-1].date()} · {yrs:.1f} 年 · 单边 {COST:.1%} · 空仓 {CASH:.2%}")
    for r in rows:
        print(f"  {r['name'][:44]:44s} 年化 {gc.fmt_pct(r['ann']):>9s} · 回撤 "
              f"{gc.fmt_pct(r['mdd']):>8s} · 月末 {gc.fmt_pct(r['mdd_me']):>8s} · "
              f"Calmar {r['calmar']:>5.2f} · 夏普 {r['sharpe']:>5.2f} · "
              f"仓位 {r['expo']:.0%} · 调仓 {r['trades']}")
    return 0


def idx_label(tv):
    return f"{tv:.0%}"


def fmt(x, nd=2):
    return "—" if x is None else f"{x * 100:+.{nd}f}%"


# ---------------------------------------------------------------- 4. 图
def plot(curves, idx):
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    import matplotlib.ticker as mticker

    plt.rcParams["font.sans-serif"] = ["Microsoft YaHei", "SimHei", "DejaVu Sans"]
    plt.rcParams["axes.unicode_minus"] = False

    style = {
        "静态 50/50（买入持有）": ("#999999", 1.2, "--"),
        "**v8 旧默认（纯二元金叉 · VOL_ON=False）**": ("#1b2b3a", 1.5, "-"),
        "金叉 × 波动层 TV 20%（vol20 · 死区 10pp）": ("#d62728", 1.6, "-"),
        "只波动层 TV20（**无金叉门**）": ("#1f77b4", 2.0, "-"),
        "金叉 × 自适应档位（vol20 三年分位 → 100/75/50/25/0）": ("#9467bd", 1.3, "-"),
        "v7 年线闸门（MA200 + 2 月确认 + 深破 + TV20）": ("#ff7f0e", 1.9, "-"),
    }
    fig, ax = plt.subplots(figsize=(13, 6.8))
    for r in curves:
        c, lw, ls = style.get(r["name"], ("#2ca02c", 1.3, "-."))
        nm = r["name"].replace("**", "")
        ax.plot(r["nav"].index, r["nav"].to_numpy(), color=c, lw=lw, ls=ls,
                label=f"{nm}（{gc.fmt_pct(r['ann'])} / {gc.fmt_pct(r['mdd'])} / "
                      f"夏普 {r['sharpe']:.2f}）")
    ax.set_yscale("log")
    ax.yaxis.set_major_locator(mticker.LogLocator(base=10.0,
                                                  subs=(1.0, 1.5, 2.0, 3.0, 4.0, 5.0, 6.0, 8.0)))
    ax.yaxis.set_major_formatter(mticker.FuncFormatter(lambda v, _p: f"{v:g}"))
    ax.yaxis.set_minor_formatter(mticker.NullFormatter())
    ax.set_ylabel("组合净值（对数轴）")
    ax.set_title(f"V8 金叉 + 波动层 · 两腿等权 50/50 · {idx[0].date()} ~ {idx[-1].date()} · "
                 f"单边 {COST:.1%}", fontsize=12.5)
    ax.legend(loc="upper left", fontsize=8.2, frameon=False)
    ax.grid(alpha=0.3)
    fig.tight_layout()
    fig.savefig(OUT_PNG, dpi=130, facecolor="white")
    plt.close(fig)


# ---------------------------------------------------------------- 5. 报告
def write_md(rows, by, base, stat, tv20, best_est, best_reg, pctl, vonly, v7, wins,
             yr_rows, half, mid, yrs, idx):
    lo, hi = idx[0], mid
    L = [
        f"# V8 金叉 50/200 加波动层 · 效果实测（{idx[0].date()} ~ {idx[-1].date()} · {yrs:.1f} 年）",
        "> 由 `research/_probe_vol_layer.py` 生成。口径与 `backtest.py` 完全一致："
        "信号 = 美元指数、执行 = 场外 QDII 复权净值、T+1 收盘成交、"
        f"单边 {COST:.1%}、空仓 {CASH:.2%}、两腿等权 50/50。",
        "> 「自适应」在这里分五种不同的事，分开测：`fixed-TV`（不自适应）/ `估计子`"
        "（max(20,60)、blend、EWMA）/ `目标`（TV ∝ vol250/中位）/ `档位`（分位五档）。",
        "",
        "## 0. 结论",
        "",
        f"**1. 波动层确实把最坏的地方补上了，但没能翻盘。** 纯二元金叉 {fmt(base['ann'])} / "
        f"回撤 {fmt(base['mdd'])} / 夏普 {base['sharpe']:.2f}；加固定 TV20 后 "
        f"{fmt(tv20['ann'])} / {fmt(tv20['mdd'])} / 夏普 {tv20['sharpe']:.2f} —— "
        f"年化 {(tv20['ann'] - base['ann']) * 100:+.2f}pp、回撤浅 "
        f"{(tv20['mdd'] - base['mdd']) * 100:.2f}pp、夏普 {tv20['sharpe'] - base['sharpe']:+.2f}、"
        f"Calmar(月末) {base['calmar']:.2f} → {tv20['calmar']:.2f}、调仓 {base['trades']} → "
        f"{tv20['trades']} 次。**但它夏普仍低于买入持有的 {stat['sharpe']:.2f}（{tv20['sharpe']:.2f}）** ——"
        "只是从「明显更差」变成「略差但更平」。",
        f"**2. 真正的发现：把金叉门去掉、只留波动层，反而更好。** 只波动层（无方向判断）"
        f"{fmt(vonly['ann'])} / {fmt(vonly['mdd'])} / 夏普 **{vonly['sharpe']:.2f}**，"
        f"比「金叉 × TV20」高 {(vonly['ann'] - tv20['ann']) * 100:+.2f}pp 年化、"
        f"{vonly['sharpe'] - tv20['sharpe']:+.2f} 夏普，回撤只差 "
        f"{(vonly['mdd'] - tv20['mdd']) * 100:+.2f}pp。**金叉门在波动层之上是净扣分的。**"
        f"同尺子下 v7（价格 vs 年线门 + 同一层波动层）是 {v7['sharpe']:.2f} —— "
        "**门越钝、边际越负**，而 50/200 金叉是所有门里最钝的（见 §10）。",
        "> **已采纳（2026-11-05）**：① v8 默认档改为「金叉 × TV20」（本文第 1 条）；"
        "② 「只留波动层」单独成支 → `v10_vol_layer`（本文第 2 条那条曲线）。"
        "纯二元金叉保留为 `VOL_ON=False` 的对照档，硬断言仍在跑。",
        f"**3. 「自适应」四种做法，没有一种赢过固定 TV20。** 估计子最好的是 "
        f"`{best_est['name'].split('· ')[-1]}`（夏普 {best_est['sharpe']:.2f}），"
        f"五种估计式全在 {tv20['sharpe']:.2f} 及以下（vol10 0.70 / vol60 0.68 两头顶差）；"
        f"目标自适应最好 {best_reg['sharpe']:.2f}（与固定 TV20 打平，但调仓 {best_reg['trades']} vs "
        f"{tv20['trades']} 次）；档位自适应 {pctl['sharpe']:.2f}（调仓 {pctl['trades']} 次，明显更差）。"
        "**波动层不需要自适应 —— 需要的是常数 TV + 死区。**",
        f"**4. TV 与死区是纯平移旋钮，不是最优点。** TV 12%→25%：夏普 0.71~0.74、Calmar(月末) "
        f"0.93~1.00、平均仓位 70%→83%，**单调、无尖峰**；死区 0 / 10 / 20pp 夏普都是 0.73，"
        f"只把调仓从 934 压到 {tv20['trades']}、再到 73 次。"
        "**TV 按你的回撤容忍度选，死区按你能承受的调仓频率选。**",
        f"**5. 代价：v8 唯一的工程优势被吃掉大半。** 它原本一年动 2 次（成本 0.6% 几乎无感）；"
        f"加 TV20 后 {tv20['trades']} 次（一年 {tv20['trades'] / yrs:.1f} 次），0.6% 成本下年化再掉约 0.5pp。"
        "**要这一层，就得接受它从「最省的一支」变成「中等换手的一支」。**",
        "**6. 2000-2002 / 2008 这一课仍然没被验证**：样本只到 2012。波动层在那两段"
        "**理论上**会在泡沫破裂期持续低仓（有利），但本仓库无数据可验 —— 别把它当已知。",
        "",
        "## 1. 全表（组合层 · 两腿等权 50/50 · 单边 0.1% · 空仓 1.40%）",
        "",
        "| 组合 | 类别 | 年化 | 波动 | 回撤·日频 | 回撤·月末 | Calmar(月末) | 夏普 | 仓位 | 调仓 |",
        "|---|---|---:|---:|---:|---:|---:|---:|---:|---:|",
    ]
    for r in rows:
        L.append(f"| {r['name']} | {r['note']} | {gc.fmt_pct(r['ann'])} | {r['vol'] * 100:.2f}% | "
                 f"{gc.fmt_pct(r['mdd'])} | {gc.fmt_pct(r['mdd_me'])} | {r['calmar']:.2f} | "
                 f"{r['sharpe']:.2f} | {r['expo']:.0%} | {r['trades']} |")
    L += [
        "",
        "> `类别` 列：`基线` = 纯二元；`fixed-TV` = 固定目标波动；`估计子` = 换波动率估计式；"
        "`目标自适应` = TV 随长期波动漂移；`档位自适应` = 分位五档；"
        "`分解` / `死区` = 拆解项；`参照` = v7。",
        "",
        "## 2. 固定 TV 邻域（vol20 · 死区 10pp）",
        "",
        "| TV | 年化 | 回撤·日频 | 回撤·月末 | Calmar(月末) | 夏普 | 仓位 | 调仓 |",
        "|---|---:|---:|---:|---:|---:|---:|---:|",
    ]
    for tv in (0.12, 0.15, 0.18, 0.20, 0.22, 0.25):
        r = by[f"金叉 × 波动层 TV {tv:.0%}（vol20 · 死区 10pp）"]
        L.append(f"| **{tv:.0%}** | {gc.fmt_pct(r['ann'])} | {gc.fmt_pct(r['mdd'])} | "
                 f"{gc.fmt_pct(r['mdd_me'])} | {r['calmar']:.2f} | {r['sharpe']:.2f} | "
                 f"{r['expo']:.0%} | {r['trades']} |")
    L += [
        "",
        "> **TV 是「你要多平的曲线」的旋钮，不是最优化对象**：12%→25% 之间年化与回撤单调平移"
        "（8.80%→11.20%、−13.54%→−19.21%），夏普在 0.71~0.74 之间一条平带，**没有尖峰**。"
        "代价在换手上：12% 要 400 次调仓，25% 只要 77 次 —— **低 TV = 更平但更贵**。"
        "**20% 是 v7 的默认**，这里沿用，方便同尺子比较。",
        "",
        "## 3. 估计子自适应（TV 固定 20%）",
        "",
        "| 波动率估计 | 年化 | 回撤·日频 | Calmar | 夏普 | 仓位 | 调仓 |",
        "|---|---:|---:|---:|---:|---:|---:|",
    ]
    for r in [x for x in rows if x["note"] == "估计子"]:
        L.append(f"| {r['name'].split('· ')[-1]} | {gc.fmt_pct(r['ann'])} | {gc.fmt_pct(r['mdd'])} | "
                 f"{r['calmar']:.2f} | {r['sharpe']:.2f} | {r['expo']:.0%} | {r['trades']} |")
    L += [
        "",
        "> **五种估计式没有一种比纯 vol20 好**（0.68~0.72 vs 0.73）。两个极端最差："
        "vol10（太快）0.70、vol60（太慢）0.68、EWMA 0.72、max(20,60) 0.71、blend 0.72。"
        "**「窗口别太长也别太短」这句话在 v8 上同样成立，但它只是「别选错」，不会带来增益** ——"
        "把估计子搞得再聪明，也不如把 TV 调对一格。",
        "",
        "## 4. 目标自适应（TV ∝ vol250 / 扩张中位数，夹在 [0.5, 2]×TV0）",
        "",
        "| 基准 TV | 年化 | 回撤·日频 | Calmar | 夏普 | 仓位 | 调仓 |",
        "|---|---:|---:|---:|---:|---:|---:|",
    ]
    for r in [x for x in rows if x["note"] == "目标自适应"]:
        L.append(f"| {r['name'].split('∝')[0].replace('金叉 × 自适应目标 ', '')} | "
                 f"{gc.fmt_pct(r['ann'])} | {gc.fmt_pct(r['mdd'])} | {r['calmar']:.2f} | "
                 f"{r['sharpe']:.2f} | {r['expo']:.0%} | {r['trades']} |")
    L += [
        "",
        "> 想法是「长期高波动期把目标放宽，免得永远半仓」。实测**最多打平、且更贵**："
        "最好的一档（基准 TV18%）夏普 0.73 与固定 TV20 相同，但调仓 177 vs 132 次；"
        "基准调高到 22% 反而掉到 0.70。**它把「风险预算」交给了一个没人验证过的锚** ——"
        "**目标波动应当是你的风险偏好，不是一个要跟着市场变的数。**",
        "",
        "## 5. 档位自适应（vol20 的三年滚动分位 → 100/75/50/25/0）",
        "",
        "| 组合 | 年化 | 回撤·日频 | 回撤·月末 | Calmar | 夏普 | 仓位 | 调仓 |",
        "|---|---:|---:|---:|---:|---:|---:|---:|",
        f"| 分位五档 | {gc.fmt_pct(pctl['ann'])} | {gc.fmt_pct(pctl['mdd'])} | "
        f"{gc.fmt_pct(pctl['mdd_me'])} | {pctl['calmar']:.2f} | {pctl['sharpe']:.2f} | "
        f"{pctl['expo']:.0%} | {pctl['trades']} |",
        "",
        "> 把连续系数换成 5 档，等于给仓位加了「跳跃 + 更容易打到 0」：平均仓位被压到 49%，"
        "年化掉到 5.13%、夏普 0.50、调仓 780 次。**连续缩放（`min(1, TV/vol)`）本身就是它的优点** ——"
        "离散化只保留了下限、丢掉了梯度。",
        "",
        "## 6. 死区（闸门未变时的容忍带）",
        "",
        "| 死区 | 年化 | 回撤·日频 | Calmar | 夏普 | 仓位 | 调仓 |",
        "|---|---:|---:|---:|---:|---:|---:|",
    ]
    for key, label in (("金叉 × TV20 · 死区 0pp", "0pp"), ("金叉 × 波动层 TV 20%（vol20 · 死区 10pp）", "10pp"),
                       ("金叉 × TV20 · 死区 20pp", "20pp")):
        r = by[key]
        L.append(f"| {label} | {gc.fmt_pct(r['ann'])} | {gc.fmt_pct(r['mdd'])} | "
                 f"{r['calmar']:.2f} | {r['sharpe']:.2f} | {r['expo']:.0%} | {r['trades']} |")
    L += [
        "",
        "> 死区的价值与 v7 一致：它只砍「几 pp 的噪音微调」，不动真正的状态切换。"
        "**0pp 会把调仓推高一个量级而收益不变**；20pp 开始变钝。",
        "",
        "## 7. 压力窗口（收益 / 窗口内最大回撤）",
        "",
        "| 窗口 | 默认 | **TV20** | " + best_est["name"].split("· ")[-1] + " | v7 | 静态 |",
        "|---|---:|---:|---:|---:|---:|",
    ]
    for label, _a, _b in WINDOWS:
        c = wins[label]
        cells = []
        for k in ("默认", "TV20", best_est["name"], "v7", "静态"):
            v = c.get(k, (None, None))
            cells.append(f"{fmt(v[0])} / {fmt(v[1])}")
        L.append(f"| {label} | " + " | ".join(cells) + " |")
    L += [
        "",
        f"> **2020 是这一层的全部理由**：默认 {fmt(wins['2020 疫情']['默认'][1])} → "
        f"TV20 {fmt(wins['2020 疫情']['TV20'][1])}（窗口收益 {fmt(wins['2020 疫情']['默认'][0])} → "
        f"{fmt(wins['2020 疫情']['TV20'][0])}，基本没变）。"
        f"**慢熊里它同样帮得上**：2018Q4 {fmt(wins['2018Q4 急跌']['默认'][1])} → "
        f"{fmt(wins['2018Q4 急跌']['TV20'][1])}、2022 {fmt(wins['2022 加息熊市']['默认'][1])} → "
        f"{fmt(wins['2022 加息熊市']['TV20'][1])}（波动率一路上行，本来就该降仓）。",
        "> **代价年仍在牛市**：逐年表里 2019（−1.25pp）、2021（−2.26pp）、2024（−1.30pp）、"
        "2025（−1.46pp）持续少赚 —— 这是保险费，不是 bug。",
        "",
        "## 8. 逐年（默认 / TV20 / v7 / 静态）",
        "",
        "| 年 | 默认 | TV20 | v7 | 静态 | TV20 vs 默认 |",
        "|---|---:|---:|---:|---:|---:|",
    ]
    for y, v in yr_rows:
        d = "—" if (v["TV20"] is None or v["默认"] is None) else f"{(v['TV20'] - v['默认']) * 100:+.2f}pp"
        L.append(f"| {y} | {fmt(v['默认'])} | {fmt(v['TV20'])} | {fmt(v['v7'])} | "
                 f"{fmt(v['静态'])} | {d} |")
    a1, d1, s1 = half(base["nav"], lo, hi)
    a2, d2, s2 = half(tv20["nav"], lo, hi)
    a3, d3, s3 = half(base["nav"], mid, idx[-1])
    a4, d4, s4 = half(tv20["nav"], mid, idx[-1])
    L += [
        "",
        "## 9. 分半样本",
        "",
        "| 段 | 区间 | 默认 年化/回撤/夏普 | TV20 年化/回撤/夏普 |",
        "|---|---|---|---|",
        f"| 前半 | {lo.date()} ~ {mid.date()} | {fmt(a1)} / {fmt(d1)} / {s1:.2f} | "
        f"{fmt(a2)} / {fmt(d2)} / {s2:.2f} |",
        f"| 后半 | {mid.date()} ~ {idx[-1].date()} | {fmt(a3)} / {fmt(d3)} / {s3:.2f} | "
        f"{fmt(a4)} / {fmt(d4)} / {s4:.2f} |",
        "",
        "> **前半段（2012-2019，没有大崩盘）几乎打平，后半段（含 2020）才是它的战场。**"
        f"前半段夏普 {s1:.2f} → {s2:.2f}、回撤 {fmt(d1)} → {fmt(d2)}；"
        f"后半段 {s3:.2f} → {s4:.2f}、回撤 {fmt(d3)} → {fmt(d4)}。"
        "**这层买的是尾部保护，不是日常增益** —— 也意味着它的价值高度依赖样本里有没有那一次崩盘"
        "（这正是 §11 第 1 条要提醒的事）。",
        "",
        "## 10. 为什么「同样的波动层，金叉门不如年线门」",
        "",
        f"同尺子：v8+TV20 {fmt(tv20['ann'])} / {fmt(tv20['mdd'])} / {tv20['sharpe']:.2f}"
        f"（调仓 {tv20['trades']} 次）vs v7 {fmt(v7['ann'])} / {fmt(v7['mdd'])} / "
        f"{v7['sharpe']:.2f}（调仓 {v7['trades']} 次）。",
        "",
        "两个原因，都在「门」的性质上：",
        "",
        "1. **金叉门比年线门更钝**：`MA50 > MA200` 要求**快线也翻下去**才关门；"
        "年线门只看**价格**是否跌破 MA200。2020 那次，价格 **2020-03-09** 就跌破年线，"
        "而 MA50 要到 **2020-04-30** 才穿 MA200 —— **整整晚 7 周多**；"
        "v8 的成交更晚（05-06 清仓、05-22 又买回），v7 早在 04-01 就清仓了。",
        "2. **金叉门没有「确认计数」与「深破位豁免」**：v7 的「连续 2 个月」其实是让门更钝，"
        "但它同时用「深破位 3% 当月就减」把 2020 那种一次性破位补掉。金叉门两头都没有。",
        "",
        "> 换句话说：**v8 + 波动层 ≈ v7 的骨架少了「价格 vs 年线」这条信息**。"
        "这也解释了为什么「金叉 + 价格过滤」那一档（`backtest.py` §3）能把回撤压到 −16.05% ——"
        "它补的正是这条信息。",
        "",
        "## 11. 边界",
        "",
        "1. **样本仍只到 2012**（QDII 净值起点）：不含 2000-2002 与 2008。"
        "这两段里波动层的作用**未知** —— 直觉上它会在泡沫破裂期持续低仓（有利），"
        "但没有本仓库的数据可验。",
        "2. **波动层是风险预算、不是择时**：低波动阴跌（波动率低但一直跌）里它照样满仓挨打；"
        "它救 2020 是因为那次波动率**先于价格**爆表。",
        "3. **换手上升是明码标价的**：固定 TV20 把调仓从 "
        f"{base['trades']} 推到 {tv20['trades']} 次（14.2 年）。"
        "v8 原始的工程优势（一年 2 次、0.6% 成本几乎无感）**会被这一层吃掉大半** —— "
        "要那一层就得接受这个。",
        "4. **TV=20% 是从 v7 借来的默认值**，不是本探针选出来的最优（§2 是单调平移，没有最优点）。"
        "真要定，应按你自己的回撤容忍度选。",
        "",
        "> 模拟结果，非投资建议。",
    ]
    with open(OUT_MD, "w", encoding="utf-8") as f:
        f.write("\n".join(L))


if __name__ == "__main__":
    sys.exit(main())
