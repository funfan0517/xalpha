# -*- coding: utf-8 -*-
"""成长/红利风格轮动 v4 · 回测（R 阶梯分档 5:0→0:5 + 双侧闸门 + 现金仓）

目标：把 v3 的二元切换（≤0.35 满仓成长 / ≥0.65 满仓红利）改成 5 档逐级过渡，
检验「连续化能否在不大幅让出收益的前提下进一步压低回撤」。

R 层阶梯（成长:红利）：
  R <0.35 → 5:0 · 0.35~0.45 → 4:1 · 0.45~0.55 → 1:1 · 0.55~0.65 → 1:4 · ≥0.65 → 0:5

对照口径（同代码、同趋势层、同费率、同冷却，只改 R 层）：
  - v3 二元（`mode="binary"`）：状态保持，中间带不动
  - 无趋势阶梯：关掉双侧闸门，看趋势层的增量
  - 棘轮（`mode="ratchet"`，**诊断用**）：减仓走阶梯，加成长仅限 R<0.35

产物：backtest/_v4_report.md · _v4_bt.json · _v4_bt.jsonl
      backtest/_v4_nav.png    净值 + 回撤
      backtest/_v4_alloc.png  三资产仓位堆叠 + 创业板指
      backtest/_v4_dual.png   R + 四条档位线 + 创业板指与 MA200 + 买卖点
用法：python strategies/growth_div_rotation/v4_graded_bands/backtest.py [--refresh]
"""
import io
import json
import os
import sys
from datetime import datetime

import numpy as np
import pandas as pd

try:
    sys.stdout.reconfigure(encoding="utf-8")
except (AttributeError, OSError):
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8")

_DIR = os.path.dirname(os.path.abspath(__file__))
if _DIR not in sys.path:
    sys.path.insert(0, _DIR)

import matplotlib  # noqa: E402
matplotlib.use("Agg")
import matplotlib.dates as mdates  # noqa: E402
import matplotlib.pyplot as plt  # noqa: E402

plt.rcParams["font.sans-serif"] = ["Microsoft YaHei", "SimHei", "DejaVu Sans"]
plt.rcParams["axes.unicode_minus"] = False

import rule  # noqa: E402
import data as datamod  # noqa: E402
import factors  # noqa: E402
import engine  # noqa: E402

OUT = os.path.join(_DIR, "backtest")


def pct(x, nd=2):
    return "—" if x is None or (isinstance(x, float) and np.isnan(x)) else f"{x * 100:+.{nd}f}%"


def mdd_window(eq):
    """最大回撤的幅度与起止日（峰值日 → 谷底日）。"""
    s = eq / eq.iloc[0]
    dd = s / s.cummax() - 1.0
    end = dd.idxmin()
    return dict(dd=float(dd.min()), peak=str(s.loc[:end].idxmax().date()),
                end=str(end.date()))


def seg_ret(eq, start, stop):
    """区间累计收益（段内重新起算）。"""
    e = eq[(eq.index >= pd.Timestamp(start)) & (eq.index < pd.Timestamp(stop))]
    return float(e.iloc[-1] / e.iloc[0] - 1.0)


def seg_mdd(eq, start, stop):
    """区间内最大回撤（段内重新起算）。"""
    e = eq[(eq.index >= pd.Timestamp(start)) & (eq.index < pd.Timestamp(stop))]
    s = e / e.iloc[0]
    return float((s / s.cummax() - 1.0).min())


# ============================================================ 计算
def compute(refresh=False):
    if refresh:
        datamod.refresh()
    wide = datamod.load_wide()
    g, d = wide[rule.GROWTH_INDEX], wide[rule.DIV_INDEX]
    r = factors.ratio_series(g, d)
    g, d = g.reindex(r.index), d.reindex(r.index)

    def build(**kw):
        W = engine.target_weights(r, g, d, **kw)
        res = engine.run(g, d, W)
        return dict(W=W, res=res, m=engine.metrics(res["eq"]))

    main = build()                                        # v4 阶梯 + 双侧闸门
    binary = build(mode="binary")                         # v3 的 R 层（对照）
    ratchet = build(mode="ratchet")                       # 诊断：只在 R<0.35 加成长
    no_trend = build(use_trend=False)                     # 纯阶梯，无趋势层

    shapes = [(tag, build(bands=b)) for tag, b in rule.SENS_SHAPE]

    # 敏感性
    sens = []
    for ma in rule.SENS_MA:
        for cut in rule.SENS_CUT:
            v = build(ma_window=ma, cut=cut)
            sens.append(dict(kind="ma×cut", ma=ma, cut=cut, n=len(v["res"]["switches"]),
                             cash=float(v["W"]["cash"].mean()),
                             **{k: v["m"][k] for k in ("ann", "dd", "shp", "calmar")}))
    for cool in rule.SENS_COOL:
        v = build(cool=cool)
        sens.append(dict(kind="cool", cool=cool, n=len(v["res"]["switches"]),
                         cash=float(v["W"]["cash"].mean()),
                         **{k: v["m"][k] for k in ("ann", "dd", "shp", "calmar")}))
    for cy in rule.SENS_CASH:
        W = engine.target_weights(r, g, d)
        res = engine.run(g, d, W, cash_yield=cy)
        m = engine.metrics(res["eq"])
        sens.append(dict(kind="cash", cash_yield=cy, n=len(res["switches"]),
                         cash=float(W["cash"].mean()),
                         **{k: m[k] for k in ("ann", "dd", "shp", "calmar")}))

    bh_g = engine.run(g, d, pd.DataFrame({"growth": 1.0, "div": 0.0, "cash": 0.0},
                                         index=r.index), fee=0)["eq"]
    bh_d = engine.run(g, d, pd.DataFrame({"growth": 0.0, "div": 1.0, "cash": 0.0},
                                         index=r.index), fee=0)["eq"]

    # 档位分布（按 R 本身统计，与仓位无关）
    band = engine.band_series(r)
    band_stats = []
    for i, (hi, (wg, wd)) in enumerate(rule.RATIO_BANDS):
        mask = band == i
        n = int(mask.sum())
        lo = 0.0 if i == 0 else rule.RATIO_BANDS[i - 1][0]
        band_stats.append(dict(idx=i, label=f"{wg}:{wd}", lo=lo,
                               hi=(None if np.isinf(hi) else hi), days=n,
                               share=n / len(r), mean_r=float(r[mask].mean()) if n else float("nan"),
                               wg=wg / (wg + wd), wd=wd / (wg + wd)))

    switches = engine.describe_switches(main["res"]["switches"], r)
    legs = engine.hold_legs(main["W"])
    bars = [b - a for a, b, _ in legs]

    # 三个关键区间的相对表现（机制诊断用）
    periods = [("2013-01-01", "2015-06-12", "成长牛 2013-2015H1"),
               ("2015-06-12", "2016-01-28", "2015 股灾"),
               ("2016-01-01", "2018-10-18", "成长熊 2016-2018"),
               ("2019-01-01", "2021-12-31", "成长牛 2019-2021"),
               ("2024-10-08", "2025-04-07", "急涨回落 2024H2")]
    period_rows = []
    for a, b, tag in periods:
        period_rows.append(dict(tag=tag, start=a, end=b,
                                v4=seg_ret(main["res"]["eq"], a, b),
                                v3=seg_ret(binary["res"]["eq"], a, b),
                                bh_g=seg_ret(bh_g, a, b), bh_d=seg_ret(bh_d, a, b)))

    return dict(g=g, d=d, r=r, main=main, binary=binary, ratchet=ratchet,
                no_trend=no_trend, shapes=shapes, sens=sens, band_stats=band_stats,
                switches=switches, legs=legs, bars=bars, periods=period_rows,
                ddw=dict(v4=mdd_window(main["res"]["eq"]),
                         v3=mdd_window(binary["res"]["eq"])),
                dd_same=dict(v4=seg_mdd(main["res"]["eq"], "2015-06-12", "2018-10-19"),
                             v3=seg_mdd(binary["res"]["eq"], "2015-06-12", "2018-10-19")),
                nv=dict(v4=main["res"]["eq"], v3=binary["res"]["eq"],
                        bh_g=bh_g, bh_d=bh_d))


# ============================================================ 报告
def build_report(C):
    r = C["r"]
    m = C["main"]["m"]
    m3 = C["binary"]["m"]
    mr = C["ratchet"]["m"]
    mn = C["no_trend"]["m"]
    mg = engine.metrics(C["nv"]["bh_g"])
    md = engine.metrics(C["nv"]["bh_d"])
    gap_ann = (m3["ann"] - m["ann"]) * 100
    gap_cal = m3["calmar"] - m["calmar"]

    L = [f"# 成长/红利风格轮动 · v4「R 阶梯分档 5:0→0:5 + 双侧 MA{rule.MA_TREND} 闸门 + 现金仓」", "",
         f"> 生成 {datetime.now():%Y-%m-%d %H:%M} · 数据 {r.index[0].date()}~{r.index[-1].date()}"
         f"（{len(r)} 交易日）· 分子 {rule.GROWTH_NAME} {rule.GROWTH_INDEX} ÷ "
         f"分母 {rule.DIV_NAME} {rule.DIV_INDEX}", "",
         "> 目标：把 v3 的二元切换改成 5 档逐级过渡，检验连续化能否**在不大幅让出收益的前提下**"
         "进一步压低回撤。", "",
         "## 0. 结论先行", "",
         f"**本样本上，这套等分阶梯明显跑输 v3**：年化 {pct(m['ann'])} vs {pct(m3['ann'])}"
         f"（**少 {gap_ann:.1f}pp**），最大回撤 {pct(m['dd'])} vs {pct(m3['dd'])}（**反而更深**），"
         f"Calmar {m['calmar']:.2f} vs {m3['calmar']:.2f}（**低 {gap_cal:.2f}**）。"
         f"切换 {len(C['switches'])} 次（v3 为 {len(C['binary']['res']['switches'])} 次）。", "",
         f"不是小退化：**年化只剩 v3 的 {m['ann'] / m3['ann']:.0%}，回撤还深了 "
         f"{abs(m['dd'] - m3['dd']) * 100:.1f}pp**。原因不是「分档」这个想法本身，"
         "而是**分档把「R 下降」读成了「加仓成长」的信号**，而在这份样本里，R 的中段下降"
         "常常是成长持续走弱的开始（详见 §3）。", "",
         "## 1. R 层规则", "",
         "| R 区间 | 成长:红利 | 成长仓位 | 红利仓位 |", "|---|---|---|---|"]
    for s in C["band_stats"]:
        hi = "∞" if s["hi"] is None else f"{s['hi']:.2f}"
        L.append(f"| {s['lo']:.2f} ~ {hi} | **{s['label']}** | "
                 f"{s['wg']:.0%} | {s['wd']:.0%} |")
    L += ["",
          f"趋势层与 v3 完全一致（未改动）：创业板指 < MA{rule.MA_TREND} → 成长部分 ×"
          f"{1 - rule.TREND_CUT:.0%}；中证红利 < MA{rule.MA_TREND} → 红利部分 ×"
          f"{1 - rule.TREND_CUT:.0%}；削掉的仓位**转现金**，按 {rule.CASH_YIELD:.1%} 计息；"
          f"两次切换间隔 < {rule.COOL_BARS} 交易日则挂起。", "",
          "## 2. 与 v3 的对照（同代码、同趋势层、同费率，只改 R 层）", "",
          "| 口径 | 年化 | 最大回撤 | 波动 | 夏普 | Calmar | 切换 | 成长暴露 | 红利暴露 | 现金 |",
          "|---|---|---|---|---|---|---|---|---|---|"]
    rows = [("**v4 阶梯（本变种）**", C["main"]), ("v3 二元（中间带保持）", C["binary"]),
            ("纯阶梯，无趋势层", C["no_trend"]), ("棘轮诊断（加成长仅限 R<0.35）", C["ratchet"])]
    for tag, v in rows:
        w = v["W"].mean()
        L.append(f"| {tag} | {pct(v['m']['ann'])} | {pct(v['m']['dd'])} | {pct(v['m']['vol'])} | "
                 f"{v['m']['shp']:.2f} | {v['m']['calmar']:.2f} | "
                 f"{len(v['res']['switches'])} | {w['growth']:.0%} | {w['div']:.0%} | {w['cash']:.0%} |")
    L += [f"| 买入持有{rule.GROWTH_NAME} | {pct(mg['ann'])} | {pct(mg['dd'])} | {pct(mg['vol'])} | "
          f"{mg['shp']:.2f} | {mg['calmar']:.2f} | — | 100% | 0% | 0% |",
          f"| 买入持有{rule.DIV_NAME} | {pct(md['ann'])} | {pct(md['dd'])} | {pct(md['vol'])} | "
          f"{md['shp']:.2f} | {md['calmar']:.2f} | — | 0% | 100% | 0% |", "",
          f"- **趋势层对回撤仍有效、但不是收益来源**：关掉闸门后 {pct(mn['ann'])} / "
          f"{pct(mn['dd'])}，加上闸门后 {pct(m['ann'])} / {pct(m['dd'])} —— "
          f"回撤改善 {abs(m['dd'] - mn['dd']) * 100:.1f}pp，年化却少了 "
          f"{abs(m['ann'] - mn['ann']) * 100:.1f}pp。所以问题不在趋势层。",
          f"- **病灶在加仓侧**：棘轮版（减仓照走阶梯、但只有 R 跌破 {rule.RATIO_LOW:.2f} 才加成长）"
          f"年化回升到 {pct(mr['ann'])}、回撤 {pct(mr['dd'])}、Calmar {mr['calmar']:.2f} —— "
          f"比 v4 好，但仍不如 v3 的 {pct(m3['ann'])} / Calmar {m3['calmar']:.2f}。"
          "说明「中段就减仓」也损失了收益。", "",
          f"> ⚠ 棘轮版**不是本变种的规则**，只是用来定位问题的诊断口径，不要照此实盘。", ""]

    # ---- 机制 ----
    L += ["## 3. 为什么变差：三段机制", "",
          "### 3.1 档位分布极度偏斜，最高档几乎不存在", "",
          "| 档位 | R 区间 | 天数 | 占比 | 该档平均 R |", "|---|---|---|---|---|"]
    for s in C["band_stats"]:
        hi = "∞" if s["hi"] is None else f"{s['hi']:.2f}"
        L.append(f"| **{s['label']}** | {s['lo']:.2f} ~ {hi} | {s['days']} | {s['share']:.1%} | "
                 f"{s['mean_r']:.3f} |")
    top = C["band_stats"][-1]
    L += ["",
          f"R 的中位数约 0.44、75 分位 0.57，**R ≥ 0.65 只占 {top['share']:.1%}**。"
          f"也就是说 0:5（满仓红利）这一档几乎用不上，而 4:1 与 1:4 两档各占四分之一 —— "
          "「5 档」实质退化成「3 档且中间三档都带成长仓位」。", "",
          "### 3.2 成长牛市里中途下车", "",
          "| 区间 | v4 阶梯 | v3 二元 | 持有成长 | 持有红利 |", "|---|---|---|---|---|"]
    for p in C["periods"]:
        L.append(f"| {p['tag']}（{p['start']}~{p['end']}） | {pct(p['v4'], 1)} | {pct(p['v3'], 1)} | "
                 f"{pct(p['bh_g'], 1)} | {pct(p['bh_d'], 1)} |")
    p1, p2 = C["periods"][0], C["periods"][3]
    L += ["",
          "R 在成长牛市里是**一路上升**的（成长涨得比红利快）。v3 的状态保持让它在 R 摸到 0.65 之前"
          "一直满仓成长、吃满主升段；v4 则在 R 每上一个台阶就减一档 —— **在牛市中途不断下车**："
          f"2013-2015H1 少赚 {(p1['v3'] - p1['v4']) * 100:.0f}pp，2019-2021 少赚 "
          f"{(p2['v3'] - p2['v4']) * 100:.0f}pp（均按区间累计收益）。"
          f"另一半亏损来自熊市：2016-2018 多亏 {abs(C['periods'][2]['v4'] - C['periods'][2]['v3']) * 100:.0f}pp、"
          f"2015 股灾多亏 {abs(C['periods'][1]['v4'] - C['periods'][1]['v3']) * 100:.0f}pp —— "
          "**两头都输**，这才是年化少 7pp 的完整来源。", "",
          "### 3.3 下跌中加仓：慢刀", "",
          f"v4 的最大回撤 {pct(C['ddw']['v4']['dd'])} 发生在 "
          f"**{C['ddw']['v4']['peak']} → {C['ddw']['v4']['end']}（"
          f"{(pd.Timestamp(C['ddw']['v4']['end']) - pd.Timestamp(C['ddw']['v4']['peak'])).days / 365:.2f} 年）**，"
          f"不是一次暴跌，而是一把慢刀。同期 R 从 {float(r.loc[C['ddw']['v4']['peak']]):.3f} 一路跌到 "
          f"{float(r.loc[C['ddw']['v4']['end']]):.3f}，按阶梯规则就是**逐档加仓成长**"
          "（0% → 20% → 50%），正好接住 2016-2018 的成长熊市。",
          f"同段对照 v3：**{pct(C['dd_same']['v3'])}**（v4 为 {pct(C['dd_same']['v4'])}）—— "
          f"v3 因为「没回到 {rule.RATIO_LOW:.2f} 就不切成长」而一直待在红利侧，避开了这三年；"
          "v4 的阶梯没有这个保护。", "",
          "> **本质冲突**：R 下降既可能是「成长相对变便宜了」（该买），也可能是「成长在持续走弱」"
          "（该躲）。二元水位带把加仓限制在**极端便宜**（≤0.35）这一侧，天然过滤了温和下跌；"
          "等分阶梯却在 0.35~0.65 全程加仓，把「温和下跌」也当成了买点。", ""]

    # ---- 形状敏感性 ----
    L += ["## 4. 阶梯形状敏感性（问题是否出在具体分档）", "",
          "| 阶梯形状 | 年化 | 最大回撤 | 夏普 | Calmar | 切换 | 成长暴露 |",
          "|---|---|---|---|---|---|---|"]
    for tag, v in C["shapes"]:
        L.append(f"| {tag} | {pct(v['m']['ann'])} | {pct(v['m']['dd'])} | {v['m']['shp']:.2f} | "
                 f"{v['m']['calmar']:.2f} | {len(v['res']['switches'])} | "
                 f"{v['W']['growth'].mean():.0%} |")
    L += ["", "读法：把中间档往两端推（更陡）或往中间拉（更平），都没能接近 v3 的水平。"
              "**问题不在 4:1 / 1:1 / 1:4 这三个具体权重，而在「R 中段也持有成长」这个结构本身**。", ""]

    # ---- 参数敏感性 ----
    grid = [s for s in C["sens"] if s["kind"] == "ma×cut"]

    def cell(ma, cut):
        return next(s for s in grid if s["ma"] == ma and abs(s["cut"] - cut) < 1e-9)

    best = max(grid, key=lambda s: s["calmar"])
    L += ["## 5. 参数敏感性", "",
          f"**均线 × 削减比例**（阶梯默认，cool={rule.COOL_BARS}，现金 {rule.CASH_YIELD:.1%}）：", "",
          "| MA | 削减 | 切换 | 现金 | 年化 | 最大回撤 | 夏普 | Calmar |",
          "|---|---|---|---|---|---|---|---|"]
    for s in grid:
        mark = " ← **默认**" if (s["ma"], s["cut"]) == (rule.MA_TREND, rule.TREND_CUT) else ""
        L.append(f"| {s['ma']} | {s['cut']:.0%}{mark} | {s['n']} | {s['cash']:.0%} | "
                 f"{pct(s['ann'])} | {pct(s['dd'])} | {s['shp']:.2f} | {s['calmar']:.2f} |")
    L += ["",
          f"> 这张表最好的格子（MA{best['ma']} / {best['cut']:.0%}）Calmar 也只有 {best['calmar']:.2f}，"
          f"仍低于 v3 的 {m3['calmar']:.2f} —— **调趋势层参数救不回来**，因为病灶在 R 层。", "",
          f"**冷却期**（阶梯默认）：", "",
          "| COOL_BARS | 切换 | 年化 | 最大回撤 | Calmar |", "|---|---|---|---|---|"]
    for s in C["sens"]:
        if s["kind"] != "cool":
            continue
        mark = " ← 默认" if s["cool"] == rule.COOL_BARS else ""
        L.append(f"| {s['cool']}{mark} | {s['n']} | {pct(s['ann'])} | {pct(s['dd'])} | "
                 f"{s['calmar']:.2f} |")
    cools = [s for s in C["sens"] if s["kind"] == "cool"]
    cbest = max(cools, key=lambda s: s["calmar"])
    L += ["", f"> 冷却期对这个方案影响很大（Calmar {min(s['calmar'] for s in cools):.2f}~"
              f"{cbest['calmar']:.2f}），本身就不稳健；最好的是 cool={cbest['cool']}"
              f"（{pct(cbest['ann'])} / {pct(cbest['dd'])} / Calmar {cbest['calmar']:.2f}），"
              f"仍远低于 v3 的 {m3['calmar']:.2f}。参数一挪结论就变，说明这层结构没有稳定的"
              "收益来源，只是在搬仓位。", "",
          "**现金计息**：", "",
          "| 现金年化 | 年化 | 最大回撤 | Calmar |", "|---|---|---|---|"]
    for s in C["sens"]:
        if s["kind"] != "cash":
            continue
        mark = " ← 默认" if abs(s["cash_yield"] - rule.CASH_YIELD) < 1e-9 else ""
        L.append(f"| {s['cash_yield']:.1%}{mark} | {pct(s['ann'])} | {pct(s['dd'])} | "
                 f"{s['calmar']:.2f} |")
    L.append("")

    # ---- 分段 ----
    eq = C["main"]["res"]["eq"]
    split = pd.Timestamp("2019-01-01")
    a, b = engine.metrics(eq[eq.index < split]), engine.metrics(eq[eq.index >= split])
    wa, wb = mdd_window(eq[eq.index < split]), mdd_window(eq[eq.index >= split])
    L += ["## 6. 分段稳健性", "",
          "| 段 | 年化 | 最大回撤 | 夏普 | Calmar | 最大回撤区间 |", "|---|---|---|---|---|---|",
          f"| 前半 {r.index[0].year}-2018 | {pct(a['ann'])} | {pct(a['dd'])} | {a['shp']:.2f} | "
          f"{a['calmar']:.2f} | {wa['peak']} → {wa['end']} |",
          f"| 后半 2019-{r.index[-1].year} | {pct(b['ann'])} | {pct(b['dd'])} | {b['shp']:.2f} | "
          f"{b['calmar']:.2f} | {wb['peak']} → {wb['end']} |", "",
          "> 前半（含 2016-2018 慢刀）是主要拖累；后半靠 2019-2021 牛市仍能做出正收益，"
          "但明显低于 v3。**两段都输，不是某一段的偶然**。", ""]

    # ---- 可执行性 ----
    L += ["## 7. 可执行性", "",
          f"- 持仓段 **{len(C['legs'])}** 段（v3 为 54 段），最短 **{min(C['bars'])}** 个交易日、"
          f"中位 {int(np.median(C['bars']))}、最长 {max(C['bars'])}。",
          f"- **不足 7 个交易日的段：{sum(1 for x in C['bars'] if x < 7)} 个** —— "
          "场外 C 类 <7 天赎回费 1.5%。",
          f"- 切换 {len(C['switches'])} 次，是 v3（53 次）的 "
          f"{len(C['switches']) / max(len(C['binary']['res']['switches']), 1):.1f} 倍；"
          "相邻档之间单边换手 20%（5:0↔4:1、1:4↔0:5）或 30%（4:1↔1:1↔1:4），"
          "单次摩擦小于 v3 的整仓切换，但次数是 2.5 倍，总摩擦并不更低。", ""]

    # ---- 切换 ----
    L += ["## 8. 最近 12 次切换", ""]
    if C["switches"]:
        L += ["| 交易日 | 动作 | 成长/红利/现金 变化 | 决策用 R[-1] |", "|---|---|---|---|"]
        for s in C["switches"][-12:]:
            fw = "/".join(f"{x:.2f}" for x in s["from_w"])
            tw = "/".join(f"{x:.2f}" for x in s["to_w"])
            L.append(f"| {s['date']} | **{s['action']}** | {fw} → {tw} | {s['R_prev']} |")
    L.append("")

    # ---- 边界 ----
    L += ["## 9. 已知边界与可选方向", "",
          f"1. **结论是「这套等分阶梯在当前样本上不成立」**：年化 {pct(m['ann'])} / 回撤 "
          f"{pct(m['dd'])} / Calmar {m['calmar']:.2f}，三项全面劣于 v3。不要因为「更连续 = 更稳健」"
          "这种直觉就采信它 —— 实测是相反的。",
          "2. **失败可归因，不是黑箱**：①最高档 R≥0.65 只占 5%，5 档退化成 3 档；"
          "②成长牛市里逐档减仓、中途下车；③下跌中逐档加仓，被 2016-2018 慢刀收割。",
          "3. **若仍要保留阶梯，唯一有实测支持的方向是收紧加仓侧**（棘轮口径）——"
          f"年化 {pct(mr['ann'])} / Calmar {mr['calmar']:.2f}，比 v4 好但仍不如 v3；"
          "也就是说这条路的天花板低于 v3，不建议继续投入。",
          "4. **R 阈值前视**：0.35/0.65 沿自 v2/v3，仍是全样本选优；中间三条线是在两端之间"
          "等分得到的，没有单独拟合 —— 这部分比 v3 干净，但端点前视仍在。",
          "5. **红利端系统性低估**：中证红利是价格指数，股息率约 4.5%，持有红利时段收益被少算；"
          "v4 现金占比更高（三成），受影响比 v3 大。",
          "6. 未含滑点与场外确认时滞；全部为模拟结果，非投资建议。", ""]
    return "\n".join(L) + "\n"


# ============================================================ 图：净值 + 回撤
def plot_nav(C):
    nv = C["nv"]
    labels = dict(v4=f"v4 阶梯（{len(C['switches'])} 次切换）",
                  v3="v3 二元（53 次切换）",
                  bh_g=f"持有{rule.GROWTH_NAME}", bh_d=f"持有{rule.DIV_NAME}")
    fig, (ax1, ax2) = plt.subplots(2, 1, figsize=(12, 8),
                                   gridspec_kw=dict(height_ratios=[2.1, 1], hspace=0.12),
                                   sharex=True)
    for k, s in nv.items():
        s = s / s.iloc[0]
        ax1.plot(s.index, s.values, label=f"{labels[k]}（{s.iloc[-1]:.2f}×）",
                 lw=1.6 if k == "v4" else 1.0, alpha=1.0 if k == "v4" else 0.7)
        dd = (s / s.cummax() - 1) * 100
        ax2.plot(dd.index, dd.values, lw=1.4 if k == "v4" else 0.9,
                 alpha=1.0 if k == "v4" else 0.6)
    ax1.set_yscale("log")
    ax1.legend(loc="upper left", fontsize=9)
    ax1.set_title("v4 阶梯分档 vs v3 二元 vs 基准 —— 净值（对数轴，上）与回撤（下）")
    ax1.grid(alpha=0.3)
    ax2.grid(alpha=0.3)
    ax2.set_ylabel("回撤 (%)")
    m, m3 = C["main"]["m"], C["binary"]["m"]
    ax2.axhline(m3["dd"] * 100, color="#1a9850", ls=":", lw=1.0,
                label=f"v3 最大回撤 {m3['dd']:.2%}")
    ax2.axhline(m["dd"] * 100, color="#c0392b", ls=":", lw=1.0,
                label=f"v4 最大回撤 {m['dd']:.2%}")
    ax2.legend(loc="lower right", fontsize=8)
    fig.subplots_adjust(left=0.075, right=0.985, top=0.935, bottom=0.07, hspace=0.12)
    path = os.path.join(OUT, "_v4_nav.png")
    fig.savefig(path, dpi=140, facecolor="white")
    plt.close(fig)
    return path


# ============================================================ 图：三资产仓位
def plot_alloc(C):
    W = C["main"]["W"]
    g = C["g"]
    fig, ax = plt.subplots(figsize=(13, 6.2))
    ax.stackplot(W.index, W["growth"] * 100, W["div"] * 100, W["cash"] * 100,
                 colors=["#d95f02", "#4a7fb5", "#bdbdbd"],
                 labels=["成长（创业板）", "红利（中证红利）", "现金"],
                 alpha=0.85)
    ax.set_ylim(0, 100)
    ax.set_ylabel("仓位 (%)")
    ax.set_title("v4 三资产仓位：成长 / 红利 / 现金（R 阶梯 5:0→0:5 + 双侧 MA200 闸门）")
    ax.legend(loc="upper left", ncol=3, fontsize=9, framealpha=0.9)
    ax.grid(alpha=0.25, axis="y")
    ax.xaxis.set_major_locator(mdates.YearLocator(2, month=1, day=1))
    ax.xaxis.set_major_formatter(mdates.DateFormatter("%Y"))

    ax2 = ax.twinx()
    ax2.plot(g.index, g.values, color="#333333", lw=0.9, alpha=0.55, zorder=5)
    ax2.set_ylabel("创业板指点位（灰线，右轴）", fontsize=10, color="#333333")
    ax2.tick_params(axis="y", colors="#333333")
    fig.tight_layout()
    path = os.path.join(OUT, "_v4_alloc.png")
    fig.savefig(path, dpi=140, facecolor="white")
    plt.close(fig)
    return path


# ============================================================ 图：R + 档位线 + 创业板指
def plot_dual(C):
    r, g = C["r"], C["g"]
    ma = factors.trend_ma(g, rule.MA_TREND)
    fig, ax1 = plt.subplots(figsize=(13, 7.2))
    ax2 = ax1.twinx()
    fig.patch.set_facecolor("#fdfdf7")
    ax1.set_facecolor("#fdfdf7")

    ax1.plot(r.index, r.values, color="#12395e", lw=1.1, zorder=4, label="比值 R（左轴）")
    colors = ["#2ecc40", "#7fbf4d", "#999999", "#d98c3f", "#c0392b"]
    lines = [hi for hi, _ in rule.RATIO_BANDS if not np.isinf(hi)]
    for i, hi in enumerate(lines):
        ax1.axhline(hi, color=colors[i], ls=(0, (7, 5)), lw=1.5, zorder=3,
                    label=("档位分界 " + " / ".join(f"{x:.2f}" for x in lines)) if i == 0
                    else "_nolegend_")
    ax1.set_ylim(0.22, 0.95)

    ax2.plot(g.index, g.values, color="#d95f02", lw=1.0, alpha=0.9, zorder=2,
             label="创业板指（右轴）")
    ax2.plot(ma.index, ma.values, color="#8c4a00", lw=1.4, ls="--", alpha=0.75, zorder=2,
             label=f"MA{rule.MA_TREND} 成长侧闸门")
    ax2.set_ylim(200, 4700)

    for s in C["switches"]:
        dt = pd.Timestamp(s["date"])
        dg = s["to_w"][0] - s["from_w"][0]
        color = "#1a9850" if dg > 0 else "#d73027"
        ax2.scatter([dt], [float(g.loc[dt])], marker="^" if dg > 0 else "v", s=55,
                    color=color, edgecolors="white", linewidths=0.6, alpha=0.9, zorder=9)

    ax1.set_xlim(pd.Timestamp("2010-01-01"), pd.Timestamp("2027-01-01"))
    ax1.xaxis.set_major_locator(mdates.YearLocator(2, month=1, day=1))
    ax1.xaxis.set_major_formatter(mdates.DateFormatter("%Y"))
    ax1.set_ylabel("比值（创业板指 / 中证红利）", fontsize=11, color="#12395e")
    ax2.set_ylabel("创业板指点位（收盘）", fontsize=11, color="#a04a02")
    ax1.tick_params(axis="y", colors="#12395e")
    ax2.tick_params(axis="y", colors="#a04a02")
    ax1.set_title(f"v4：R 阶梯分档 + 创业板指与 MA{rule.MA_TREND}（右轴）· "
                  f"{len(C['switches'])} 次切换（▲加仓成长 ▼减仓成长）", fontsize=13, pad=10)
    ax1.grid(color="#dcdcd0", lw=0.7, alpha=0.9)
    h1, l1 = ax1.get_legend_handles_labels()
    h2, l2 = ax2.get_legend_handles_labels()
    ax1.legend(h1 + h2, l1 + l2, loc="upper left", frameon=True, fontsize=8, ncol=2,
               framealpha=0.92)
    fig.tight_layout()
    path = os.path.join(OUT, "_v4_dual.png")
    fig.savefig(path, dpi=150, facecolor=fig.get_facecolor())
    plt.close(fig)
    return path


# ============================================================ 产物
def write_products(C):
    os.makedirs(OUT, exist_ok=True)
    txt = build_report(C)
    open(os.path.join(OUT, "_v4_report.md"), "w", encoding="utf-8").write(txt)
    print(txt)

    r, m = C["r"], C["main"]["m"]
    W = C["main"]["W"]
    doc = dict(
        generated_at=datetime.now().strftime("%Y-%m-%d %H:%M"),
        variant="v4_graded_bands",
        growth=rule.GROWTH_NAME, growth_code=rule.GROWTH_INDEX,
        dividend=rule.DIV_NAME, dividend_code=rule.DIV_INDEX,
        params=dict(bands=[[(None if np.isinf(hi) else hi), list(w)]
                           for hi, w in rule.RATIO_BANDS],
                    band_units=rule.BAND_UNITS, ma_trend=rule.MA_TREND,
                    trend_cut=rule.TREND_CUT, cool_bars=rule.COOL_BARS,
                    cash_yield=rule.CASH_YIELD, fee=rule.FEE),
        window=f"{r.index[0].date()}~{r.index[-1].date()}", n_bars=len(r),
        metrics=dict(v4=m, v3_binary=C["binary"]["m"], no_trend=C["no_trend"]["m"],
                     ratchet=C["ratchet"]["m"],
                     bh_growth=engine.metrics(C["nv"]["bh_g"]),
                     bh_dividend=engine.metrics(C["nv"]["bh_d"])),
        verdict="跑输 v3：年化与回撤双输，病因在 R 层加仓侧（详见报告 §0/§3）",
        mdd_windows=dict(v4=C["ddw"]["v4"], v3=C["ddw"]["v3"]),
        band_stats=C["band_stats"], periods=C["periods"],
        n_switches=len(C["switches"]),
        cash_mean=float(W["cash"].mean()),
        growth_mean=float(W["growth"].mean()), div_mean=float(W["div"].mean()),
        legs=[dict(start=str(r.index[a].date()), end=str(r.index[b].date()), bars=b - a,
                   weight=list(w)) for a, b, w in C["legs"]],
        switches=C["switches"], sens=C["sens"],
        shapes=[dict(tag=t, **{k: v["m"][k] for k in ("ann", "dd", "shp", "calmar")})
                for t, v in C["shapes"]],
        eq={str(k.date()): round(float(v), 6) for k, v in C["main"]["res"]["eq"].items()},
        alloc={c: list(map(float, W[c].round(4).tolist())) for c in W.columns},
        dates=[str(k.date()) for k in r.index],
    )
    json.dump(doc, open(os.path.join(OUT, "_v4_bt.json"), "w", encoding="utf-8"),
              ensure_ascii=False, indent=2)

    summary = dict(ok=True, code="GD_ROT_V4",
                   theme=(f"成长/红利风格轮动 v4(阶梯 {rule.BAND_UNITS} 份: 5:0→0:5"
                          f" + 双侧 MA{rule.MA_TREND} 闸门 + 现金)"),
                   years=round(m["years"], 1), st_ann=m["ann"], st_mdd=m["dd"],
                   v3_ann=C["binary"]["m"]["ann"], v3_mdd=C["binary"]["m"]["dd"],
                   base_ann=engine.metrics(C["nv"]["bh_d"])["ann"],
                   base_mdd=engine.metrics(C["nv"]["bh_d"])["dd"],
                   switches=len(C["switches"]), cash_mean=round(float(W["cash"].mean()), 3),
                   sharpe=m["shp"], calmar=m["calmar"],
                   verdict="underperform_v3", params=doc["params"])
    with open(os.path.join(OUT, "_v4_bt.jsonl"), "w", encoding="utf-8") as f:
        f.write(json.dumps(summary, ensure_ascii=False) + "\n")

    print("图:", plot_nav(C))
    print("图:", plot_alloc(C))
    print("图:", plot_dual(C))
    return summary


def main():
    write_products(compute(refresh="--refresh" in sys.argv))


if __name__ == "__main__":
    main()
