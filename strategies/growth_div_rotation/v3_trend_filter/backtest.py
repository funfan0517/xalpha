# -*- coding: utf-8 -*-
"""成长/红利风格轮动 v3 · 回撤控制版（R 绝对水位 0.35/0.65 + 双侧趋势闸门 + 现金仓）

目标：**收益略让、回撤大幅下降**。v2 的 +17.27% 已经够，本变种只解决 -51.85% 的回撤。

规则：
  R 层   : R ≤ 0.35 → 目标满仓成长；R ≥ 0.65 → 目标满仓红利；中间带保持
  趋势层 : 创业板指 < MA200 → 成长部分减半；中证红利 < MA200 → 红利部分减半；
           **削掉的仓位转现金**（现金按 CASH_YIELD=1.5%/年计息）
  冷却期 : 两次切换间隔 < 20 交易日则挂起

产物：backtest/_v3_report.md · _v3_bt.json · _v3_bt.jsonl
      backtest/_v3_nav.png    净值 + 回撤（双子图）
      backtest/_v3_alloc.png  三资产仓位堆叠 + 创业板指
      backtest/_v3_dual.png   R + 创业板指与 MA200 + 买卖点
用法：python strategies/growth_div_rotation/v3_trend_filter/backtest.py [--refresh]
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


def seg_mdd(eq, start, stop):
    """区间内的最大回撤（段内重新起算，用于「这段到底亏多少」的对比）。"""
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

    main = build()
    v2 = build(use_trend=False)                                   # v2 同阈值（两资产）
    one_side = build(ma_window=250, cut_growth=0.5, cut_div=0.0)  # 只用成长侧闸门，转现金
    one_side_div = build(ma_window=250, cut_growth=0.5, cut_div=0.0,
                         redirect="div")                          # 反例：同样减半，但转红利
    two_250 = build(ma_window=250)                                # 双侧但 MA250
    ref_o = build(low=rule.REF_RATIO[0], high=rule.REF_RATIO[1], use_trend=False)

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

    switches = engine.describe_switches(main["res"]["switches"], r, main["W"])
    legs = engine.hold_legs(main["W"])
    bars = [b - a for a, b, _ in legs]

    return dict(g=g, d=d, r=r, main=main, v2=v2, one_side=one_side,
                one_side_div=one_side_div, two_250=two_250,
                ref_o=ref_o, sens=sens, switches=switches,
                legs=legs, bars=bars,
                ddw=dict(v3=mdd_window(main["res"]["eq"]), v2=mdd_window(v2["res"]["eq"])),
                dd2010=dict(v3=seg_mdd(main["res"]["eq"], "2010-12-17", "2013-01-01"),
                            v2=seg_mdd(v2["res"]["eq"], "2010-12-17", "2013-01-01")),
                dd2015=dict(v3=seg_mdd(main["res"]["eq"], "2015-06-12", "2016-02-01"),
                            v2=seg_mdd(v2["res"]["eq"], "2015-06-12", "2016-02-01")),
                nv=dict(v3=main["res"]["eq"], v2=v2["res"]["eq"],
                        bh_g=bh_g, bh_d=bh_d))


# ============================================================ 报告
def build_report(C):
    r = C["r"]
    m = C["main"]["m"]
    m2 = C["v2"]["m"]
    ms = C["one_side"]["m"]
    md1 = C["one_side_div"]["m"]
    m250 = C["two_250"]["m"]
    mg = engine.metrics(C["nv"]["bh_g"])
    md = engine.metrics(C["nv"]["bh_d"])
    lo, hi = rule.RATIO_LOW, rule.RATIO_HIGH

    L = [f"# 成长/红利风格轮动 · v3「回撤控制版」"
         f"（R {lo:.2f}/{hi:.2f} + 双侧 MA{rule.MA_TREND} 闸门 + 现金仓）", "",
         f"> 生成 {datetime.now():%Y-%m-%d %H:%M} · 数据 {r.index[0].date()}~{r.index[-1].date()}"
         f"（{len(r)} 交易日）· 分子 {rule.GROWTH_NAME} {rule.GROWTH_INDEX} ÷ "
         f"分母 {rule.DIV_NAME} {rule.DIV_INDEX}", "",
         f"> 目标：**收益略让、回撤大幅下降**。v2 的 {pct(m2['ann'])} 年化已经够，"
         f"本变种只解决 {pct(m2['dd'])} 的深回撤。", "",
         "## 1. 回撤归因（`research/_dd_probe.py` 实测）", "",
         "| 排名 | 回撤 | 区间 | 当时仓位 | 病因 |", "|---|---|---|---|---|",
         "| 1 | **-51.85%** | 2010-12-17 → 2012-12-03（1.96 年） | **全程满仓成长 w=1.0** | "
         "创业板指 1233→594。R 从 0.390 一路跌到 0.283，**始终 ≤0.35**，规则只会「越跌越买」，无法减仓 |",
         "| 2 | -49.32% | 2010-12-17 → 2012-01-18 | 满仓成长 | 与第 1 名**同源**（同一段阴跌的中间谷，"
         "按谷底去重后仍被单列） |",
         "| 3 | **-46.51%** | 2015-06-12 → 2016-01-28 | **空仓成长、满仓红利** | "
         "中证红利自己跌了 40%+ —— **「防御侧」在这段根本不防御**。R 0.626→0.572，一直没到 0.65，"
         "规则不会减仓 |",
         "| 4 | -44.88% | 2015-06-12 → 2015-09-15 | 满仓红利 | 与第 3 名同源，谷底早两个月 |", "",
         "两个病（**2010-2012 满仓成长阴跌**、**2015 满仓红利不防御**）→ 三个改动："
         "**双侧**闸门（治第 3 名）、**减掉的仓位转现金**（治「换方向挨打」）、"
         "**减半而非清仓**（治均线附近反复穿越的摩擦）。", "",
         f"> v3 在同两段的实测：2010-2012 段 {pct(C['dd2010']['v2'])} → "
         f"**{pct(C['dd2010']['v3'])}**（改善 "
         f"{abs(C['dd2010']['v3'] - C['dd2010']['v2']) * 100:.1f}pp）；"
         f"2015-06-12→2016-01 段 {pct(C['dd2015']['v2'])} → "
         f"**{pct(C['dd2015']['v3'])}**（改善 "
         f"{abs(C['dd2015']['v3'] - C['dd2015']['v2']) * 100:.1f}pp）。", "",
         "## 2. 逐步加法：每个改动贡献多少", "",
         "| 版本 | 年化 | 最大回撤 | 夏普 | Calmar | 切换 | 现金占比 |",
         "|---|---|---|---|---|---|---|",
         f"| ① v2 基线（0.35/0.65，无闸门） | {pct(m2['ann'])} | {pct(m2['dd'])} | "
         f"{m2['shp']:.2f} | {m2['calmar']:.2f} | {len(C['v2']['res']['switches'])} | — |",
         f"| ② + 单侧 MA250 减半（**转现金**） | {pct(ms['ann'])} | {pct(ms['dd'])} | "
         f"{ms['shp']:.2f} | {ms['calmar']:.2f} | {len(C['one_side']['res']['switches'])} | "
         f"{C['one_side']['W']['cash'].mean():.0%} |",
         f"| ③ + 红利侧也加闸门（双侧 MA250） | {pct(m250['ann'])} | {pct(m250['dd'])} | "
         f"{m250['shp']:.2f} | {m250['calmar']:.2f} | {len(C['two_250']['res']['switches'])} | "
         f"{C['two_250']['W']['cash'].mean():.0%} |",
         f"| ④ = **v3 最终（双侧 MA{rule.MA_TREND}）** | **{pct(m['ann'])}** | **{pct(m['dd'])}** | "
         f"**{m['shp']:.2f}** | **{m['calmar']:.2f}** | {len(C['switches'])} | "
         f"{C['main']['W']['cash'].mean():.0%} |",
         f"| ②′ 同 ② 但**转红利**（反例） | {pct(md1['ann'])} | {pct(md1['dd'])} | "
         f"{md1['shp']:.2f} | {md1['calmar']:.2f} | {len(C['one_side_div']['res']['switches'])} | 0% |", "",
         f"读法：**第 ② 步是全部价值所在** —— 只加成长侧一道闸门、削掉的仓位转现金，"
         f"回撤 {pct(m2['dd'])} → {pct(ms['dd'])}，而年化 {pct(m2['ann'])} → {pct(ms['ann'])}（不降反微升），"
         f"Calmar {m2['calmar']:.2f} → {ms['calmar']:.2f}。"
         f"但同样是「破线减半」，若把削掉的仓位转成红利（②′），回撤 {pct(md1['dd'])}、"
         f"Calmar 掉到 {md1['calmar']:.2f} —— **比不加闸门的基线还差**。"
         "破线时红利通常也在跌，转过去等于换个方向挨打。"
         f"③④ 再把红利侧的坑补上、窗口收到 {rule.MA_TREND} 日，回撤压到 {pct(m['dd'])}，"
         f"代价是年化让到 {pct(m['ann'])}。", "",
         f"> **回撤来源换位了**：v2 的 {pct(m2['dd'])} 全在 2010-12→2012-12（满仓成长阴跌，R 一路 ≤0.35，"
         f"规则只会越跌越买）；v3 把这一段压到 {pct(C['dd2010']['v3'])}（同段 v2 为 "
         f"{pct(C['dd2010']['v2'])}）。于是全期最大回撤变成 {C['ddw']['v3']['peak']} → "
         f"{C['ddw']['v3']['end']} 的 {pct(C['ddw']['v3']['dd'])}（满仓红利、红利侧闸门减半后的残仓）。"
         f"**第一病治好了，剩下的坑是第二病** —— 这也是 ③④ 把红利侧也加闸门的原因。", ""]

    L += ["## 3. 绩效", "",
          "| 组合 | 年化 | 最大回撤 | 波动 | 夏普 | Calmar | 总收益 |",
          "|---|---|---|---|---|---|---|",
          f"| **v3（双侧 MA{rule.MA_TREND} 减半 + 现金）** | {pct(m['ann'])} | "
          f"**{pct(m['dd'])}** | {pct(m['vol'])} | **{m['shp']:.2f}** | **{m['calmar']:.2f}** | "
          f"{pct(m['ret'])} |",
          f"| v2（同阈值，无闸门） | **{pct(m2['ann'])}** | {pct(m2['dd'])} | {pct(m2['vol'])} | "
          f"{m2['shp']:.2f} | {m2['calmar']:.2f} | {pct(m2['ret'])} |",
          f"| 买入持有{rule.GROWTH_NAME} | {pct(mg['ann'])} | {pct(mg['dd'])} | {pct(mg['vol'])} | "
          f"{mg['shp']:.2f} | {mg['calmar']:.2f} | {pct(mg['ret'])} |",
          f"| 买入持有{rule.DIV_NAME} | {pct(md['ann'])} | {pct(md['dd'])} | {pct(md['vol'])} | "
          f"{md['shp']:.2f} | {md['calmar']:.2f} | {pct(md['ret'])} |", "",
          f"- v3 最大回撤 {pct(m['dd'])}：{C['ddw']['v3']['peak']} → {C['ddw']['v3']['end']}；"
          f"v2 最大回撤 {pct(m2['dd'])}：{C['ddw']['v2']['peak']} → {C['ddw']['v2']['end']}。",
          f"- **回撤 {pct(m2['dd'])} → {pct(m['dd'])}（改善 {abs(m['dd'] - m2['dd']) * 100:.1f}pp）**，"
          f"年化 {pct(m2['ann'])} → {pct(m['ann'])}（让出 "
          f"{abs(m2['ann'] - m['ann']) * 100:.1f}pp），夏普 {m2['shp']:.2f} → {m['shp']:.2f}、"
          f"Calmar {m2['calmar']:.2f} → {m['calmar']:.2f}、波动 {pct(m2['vol'], 1)} → {pct(m['vol'], 1)}。",
          f"- 全期切换 {len(C['switches'])} 次，现金平均占比 {C['main']['W']['cash'].mean():.0%}"
          f"（成长 {C['main']['W']['growth'].mean():.0%} / 红利 {C['main']['W']['div'].mean():.0%}）。", ""]

    # 分段
    eq = C["main"]["res"]["eq"]
    split = pd.Timestamp("2019-01-01")
    a = engine.metrics(eq[eq.index < split])
    b = engine.metrics(eq[eq.index >= split])
    wa = mdd_window(eq[eq.index < split])
    wb = mdd_window(eq[eq.index >= split])
    L += ["## 4. 分段稳健性", "",
          "| 段 | 年化 | 最大回撤 | 夏普 | Calmar | 最大回撤区间 |", "|---|---|---|---|---|---|",
          f"| 前半 {r.index[0].year}-2018 | {pct(a['ann'])} | {pct(a['dd'])} | {a['shp']:.2f} | "
          f"{a['calmar']:.2f} | {wa['peak']} → {wa['end']} |",
          f"| 后半 2019-{r.index[-1].year} | {pct(b['ann'])} | {pct(b['dd'])} | {b['shp']:.2f} | "
          f"{b['calmar']:.2f} | {wb['peak']} → {wb['end']} |", "",
          f"> 前半的回撤是 2015 年那段（{wa['peak']} → {wa['end']}，满仓红利时红利自己腰斩）；"
          f"2010-2012 已被闸门压到 {pct(C['dd2010']['v3'])}，不再是最大的坑。"
          f"后半的回撤是 {wb['peak']} → {wb['end']} —— 急涨后回落，闸门在震荡里反复减仓，"
          "摩擦成本主要出在这里。", ""]

    L += ["## 5. 敏感性", "",
          f"**均线 × 削减比例**（cool={rule.COOL_BARS}，现金 {rule.CASH_YIELD:.1%}）：", "",
          "| MA | 削减 | 切换 | 现金 | 年化 | 最大回撤 | 夏普 | Calmar |",
          "|---|---|---|---|---|---|---|---|"]
    for s in C["sens"]:
        if s["kind"] != "ma×cut":
            continue
        mark = " ← **默认**" if (s["ma"], s["cut"]) == (rule.MA_TREND, rule.TREND_CUT) else ""
        L.append(f"| {s['ma']} | {s['cut']:.0%}{mark} | {s['n']} | {s['cash']:.0%} | "
                 f"{pct(s['ann'])} | {pct(s['dd'])} | {s['shp']:.2f} | {s['calmar']:.2f} |")
    grid = [s for s in C["sens"] if s["kind"] == "ma×cut"]

    def cell(ma, cut):
        return next(s for s in grid if s["ma"] == ma and abs(s["cut"] - cut) < 1e-9)

    best = max(grid, key=lambda s: s["calmar"])
    best120 = max((s for s in grid if s["ma"] == 120), key=lambda s: s["calmar"])
    half = [s for s in grid if abs(s["cut"] - 0.5) < 1e-9]
    full = [s for s in grid if abs(s["cut"] - 1.0) < 1e-9]
    gap_full = (sum(s["ann"] for s in half) / len(half)
                - sum(s["ann"] for s in full) / len(full))
    nb = cell(250, 0.5)["calmar"]
    nc = cell(200, 1.0)["calmar"]
    L += ["", f"> 六格里 **(MA{best['ma']}, {best['cut']:.0%})** 的 Calmar {best['calmar']:.2f} 最高、"
              f"回撤 {best['dd']:.2%} 也最低 —— 默认值就是这张表的最优格，**存在明显的选择偏差**，"
              "不能当成「随便取个整数都一样」。可辩护的是相邻格：MA250/50% 与 MA200/100% 的 Calmar "
              f"（{nb:.2f} / {nc:.2f}）都还在基线 {m2['calmar']:.2f} 之上，"
              "说明「200 日量级的趋势闸门」这层逻辑是稳的；而 MA120 太快、切换涨到 "
              f"{best120['n']} 次，摩擦把改善吃光（最好的 MA120 格 Calmar 仅 {best120['calmar']:.2f}，"
              f"已略差于基线）。清仓（100%）在三个窗口上的平均年化比减半低 "
              f"{gap_full * 100:.1f}pp，回撤只有 200/250 略好 —— **减半赢在风险调整后收益，不是裸回撤**。",
          "",
          f"**冷却期**（双侧 MA{rule.MA_TREND} 减半）：", "",
          "| COOL_BARS | 切换 | 年化 | 最大回撤 | Calmar |", "|---|---|---|---|---|"]
    for s in C["sens"]:
        if s["kind"] != "cool":
            continue
        mark = " ← 默认" if s["cool"] == rule.COOL_BARS else ""
        L.append(f"| {s['cool']}{mark} | {s['n']} | {pct(s['ann'])} | {pct(s['dd'])} | "
                 f"{s['calmar']:.2f} |")
    cools = [s for s in C["sens"] if s["kind"] == "cool"]
    c_min = min(cools, key=lambda s: s["calmar"])
    c_lo = min(s["calmar"] for s in cools if s["cool"] <= 30)
    c_hi = max(s["calmar"] for s in cools if s["cool"] <= 30)
    L += ["", f"> 冷却期在 15~30 之间 Calmar 只在 {c_lo:.2f}~{c_hi:.2f} 摆动，结论不靠这个参数撑着；"
              f"但 cool={c_min['cool']} 会退化（Calmar {c_min['calmar']:.2f}、回撤 {c_min['dd']:.2%}）—— "
              "两次切换的最小间隔放太久，趋势转向后迟迟不动。"
              f"默认 {rule.COOL_BARS} 顺带把最短持仓压到 19 个交易日，避开场外 C 类 7 天赎回费。",
          "",
          "**现金计息**：", "",
          "| 现金年化 | 年化 | 最大回撤 | Calmar |", "|---|---|---|---|"]
    for s in C["sens"]:
        if s["kind"] != "cash":
            continue
        mark = " ← 默认" if abs(s["cash_yield"] - rule.CASH_YIELD) < 1e-9 else ""
        L.append(f"| {s['cash_yield']:.1%}{mark} | {pct(s['ann'])} | {pct(s['dd'])} | "
                 f"{s['calmar']:.2f} |")
    cashs = [s for s in C["sens"] if s["kind"] == "cash"]
    cy0 = next(s for s in cashs if abs(s["cash_yield"]) < 1e-9)
    cy1 = next(s for s in cashs if abs(s["cash_yield"] - rule.CASH_YIELD) < 1e-9)
    L += ["", f"> 现金平均占比 {C['main']['W']['cash'].mean():.0%}，"
              f"{rule.CASH_YIELD:.1%} 与 0% 之间年化只差 {(cy1['ann'] - cy0['ann']) * 100:.1f}pp "
              "—— 不影响结论，按实际货币基金收益计即可。", ""]

    L += ["## 6. 可执行性", "",
          f"- 持仓段 **{len(C['legs'])}** 段，最短 **{min(C['bars'])}** 个交易日、"
          f"中位 {int(np.median(C['bars']))}、最长 {max(C['bars'])}。",
          f"- **不足 7 个交易日的段：{sum(1 for x in C['bars'] if x < 7)} 个** —— "
          "场外 C 类基金 <7 天赎回费 1.5%，本方案没有触发这个坑。",
          f"- 现金仓用货币基金/短债承接，申赎 T+1，不影响节奏。", ""]

    L += ["## 7. 最近 12 次切换", ""]
    if C["switches"]:
        L += ["| 交易日 | 动作 | 成长/红利/现金 变化 | 决策用 R[-1] |", "|---|---|---|---|"]
        for s in C["switches"][-12:]:
            fw = "/".join(f"{x:.2f}" for x in s["from_w"])
            tw = "/".join(f"{x:.2f}" for x in s["to_w"])
            L.append(f"| {s['date']} | **{s['action']}** | {fw} → {tw} | {s['R_prev']} |")
    L.append("")

    L += ["## 8. 已知边界", "",
          f"1. **收益让出 {(m2['ann'] - m['ann']) * 100:.1f}pp 是真实的代价**：闸门在 "
          f"{wb['peak']} → {wb['end']} 那种急涨回落里也会反复减仓，这部分摩擦不可避免。"
          f"若目标只是「不走 -50% 的深坑」，本方案达到了；若还想要 {pct(m2['ann'])} 年化，"
          "需要另找信号源，不是调参能解决的。",
          "2. **R 阈值前视**：0.35/0.65 沿自 v2，仍是全样本选优（见 `../v2_absolute_bands/README.md` §4）。",
          f"3. **MA{rule.MA_TREND} 是网格里的最优格**：{len(grid)} 格（MA × 削减比例）里 (200, 50%) "
          "在年化、夏普、Calmar、回撤上全面第一 —— 同一样本、六选一，选择偏差真实存在。"
          f"能撑住结论的是相邻格：MA250/50% 与 MA200/100% 的 Calmar {nb:.2f} / {nc:.2f} 仍在基线 "
          f"{m2['calmar']:.2f} 之上，MA120 那一档则因摩擦翻倍而退化。"
          "也就是说「趋势这一层有效」成立，但「必须 200 日、必须减半」这两个具体取值的把握没那么强。",
          "4. **红利端系统性低估**：中证红利是价格指数，股息率约 4.5%，持有红利时段收益被少算，"
          "本方案现金占比两成、红利占比更低，受影响比 v2 小。",
          "5. 未含滑点与场外确认时滞；全部为模拟结果，非投资建议。", ""]
    return "\n".join(L) + "\n"


# ============================================================ 图：净值 + 回撤
def plot_nav(C):
    nv = C["nv"]
    labels = dict(v3=f"v3 双侧MA{rule.MA_TREND}闸门+现金",
                  v2="v2 无闸门",
                  bh_g=f"持有{rule.GROWTH_NAME}", bh_d=f"持有{rule.DIV_NAME}")
    fig, (ax1, ax2) = plt.subplots(2, 1, figsize=(12, 8),
                                   gridspec_kw=dict(height_ratios=[2.1, 1], hspace=0.12),
                                   sharex=True)
    for k, s in nv.items():
        s = s / s.iloc[0]
        ax1.plot(s.index, s.values, label=f"{labels[k]}（{s.iloc[-1]:.2f}×）",
                 lw=1.6 if k == "v3" else 1.0, alpha=1.0 if k == "v3" else 0.7)
        dd = (s / s.cummax() - 1) * 100
        ax2.plot(dd.index, dd.values, lw=1.4 if k == "v3" else 0.9,
                 alpha=1.0 if k == "v3" else 0.6)
    ax1.set_yscale("log")
    ax1.legend(loc="upper left", fontsize=9)
    ax1.set_title(f"v3 回撤控制版 vs v2 vs 基准 —— 净值（对数轴，上）与回撤（下）")
    ax1.grid(alpha=0.3)
    ax2.grid(alpha=0.3)
    ax2.set_ylabel("回撤 (%)")
    m, m2 = C["main"]["m"], C["v2"]["m"]
    ax2.axhline(m2["dd"] * 100, color="#c0392b", ls=":", lw=1.0,
                label=f"v2 最大回撤 {m2['dd']:.2%}")
    ax2.axhline(m["dd"] * 100, color="#1a9850", ls=":", lw=1.0,
                label=f"v3 最大回撤 {m['dd']:.2%}")
    ax2.legend(loc="lower right", fontsize=8)
    fig.subplots_adjust(left=0.075, right=0.985, top=0.935, bottom=0.07, hspace=0.12)
    path = os.path.join(OUT, "_v3_nav.png")
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
    ax.set_title(f"v3 三资产仓位：成长 / 红利 / 现金（R {rule.RATIO_LOW:.2f}/{rule.RATIO_HIGH:.2f}"
                 f" + 双侧 MA{rule.MA_TREND} 闸门）")
    ax.legend(loc="upper left", ncol=3, fontsize=9, framealpha=0.9)
    ax.grid(alpha=0.25, axis="y")
    ax.xaxis.set_major_locator(mdates.YearLocator(2, month=1, day=1))
    ax.xaxis.set_major_formatter(mdates.DateFormatter("%Y"))

    ax2 = ax.twinx()
    ax2.plot(g.index, g.values, color="#333333", lw=0.9, alpha=0.55, zorder=5)
    ax2.set_ylabel("创业板指点位（灰线，右轴）", fontsize=10, color="#333333")
    ax2.tick_params(axis="y", colors="#333333")
    fig.tight_layout()
    path = os.path.join(OUT, "_v3_alloc.png")
    fig.savefig(path, dpi=140, facecolor="white")
    plt.close(fig)
    return path


# ============================================================ 图：R + 创业板指 + 买卖点
def plot_dual(C):
    r, g = C["r"], C["g"]
    lo, hi = rule.RATIO_LOW, rule.RATIO_HIGH
    ma = factors.trend_ma(g, rule.MA_TREND)
    fig, ax1 = plt.subplots(figsize=(13, 7.2))
    ax2 = ax1.twinx()
    fig.patch.set_facecolor("#fdfdf7")
    ax1.set_facecolor("#fdfdf7")

    ax1.plot(r.index, r.values, color="#12395e", lw=1.1, zorder=4,
             label="比值 R（左轴）")
    ax1.axhline(hi, color="#c0392b", ls=(0, (7, 5)), lw=1.7, zorder=3,
                label=f"高估 {hi:.2f}")
    ax1.axhline(lo, color="#2ecc40", ls=(0, (7, 5)), lw=1.7, zorder=3,
                label=f"低估 {lo:.2f}")
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
        ax2.scatter([dt], [float(g.loc[dt])], marker="^" if dg > 0 else "v", s=90,
                    color=color, edgecolors="white", linewidths=0.8, zorder=9)

    ax1.set_xlim(pd.Timestamp("2010-01-01"), pd.Timestamp("2027-01-01"))
    ax1.xaxis.set_major_locator(mdates.YearLocator(2, month=1, day=1))
    ax1.xaxis.set_major_formatter(mdates.DateFormatter("%Y"))
    ax1.set_ylabel("比值（创业板指 / 中证红利）", fontsize=11, color="#12395e")
    ax2.set_ylabel("创业板指点位（收盘）", fontsize=11, color="#a04a02")
    ax1.tick_params(axis="y", colors="#12395e")
    ax2.tick_params(axis="y", colors="#a04a02")
    ax1.set_title(f"v3：比值 R（左轴）+ 创业板指与 MA{rule.MA_TREND}（右轴）· "
                  f"{len(C['switches'])} 次切换（▲加仓成长 ▼减仓成长）", fontsize=13, pad=10)
    ax1.grid(color="#dcdcd0", lw=0.7, alpha=0.9)
    h1, l1 = ax1.get_legend_handles_labels()
    h2, l2 = ax2.get_legend_handles_labels()
    ax1.legend(h1 + h2, l1 + l2, loc="upper left", frameon=True, fontsize=8.5,
               ncol=2, framealpha=0.92)
    fig.tight_layout()
    path = os.path.join(OUT, "_v3_dual.png")
    fig.savefig(path, dpi=150, facecolor=fig.get_facecolor())
    plt.close(fig)
    return path


# ============================================================ 产物
def write_products(C):
    os.makedirs(OUT, exist_ok=True)
    txt = build_report(C)
    open(os.path.join(OUT, "_v3_report.md"), "w", encoding="utf-8").write(txt)
    print(txt)

    r, m = C["r"], C["main"]["m"]
    W = C["main"]["W"]
    doc = dict(
        generated_at=datetime.now().strftime("%Y-%m-%d %H:%M"),
        variant="v3_trend_filter",
        growth=rule.GROWTH_NAME, growth_code=rule.GROWTH_INDEX,
        dividend=rule.DIV_NAME, dividend_code=rule.DIV_INDEX,
        params=dict(ratio_low=rule.RATIO_LOW, ratio_high=rule.RATIO_HIGH,
                    ma_trend=rule.MA_TREND, trend_cut=rule.TREND_CUT,
                    cool_bars=rule.COOL_BARS, cash_yield=rule.CASH_YIELD, fee=rule.FEE),
        window=f"{r.index[0].date()}~{r.index[-1].date()}", n_bars=len(r),
        metrics=dict(v3=m, v2=C["v2"]["m"], one_side=C["one_side"]["m"],
                     one_side_redirect_div=C["one_side_div"]["m"],
                     two_sided_ma250=C["two_250"]["m"], ref_ratio=C["ref_o"]["m"],
                     bh_growth=engine.metrics(C["nv"]["bh_g"]),
                     bh_dividend=engine.metrics(C["nv"]["bh_d"])),
        mdd_windows=dict(v3=C["ddw"]["v3"], v2=C["ddw"]["v2"],
                         seg_2010_2012=C["dd2010"], seg_2015=C["dd2015"]),
        n_switches=len(C["switches"]),
        cash_mean=float(W["cash"].mean()),
        legs=[dict(start=str(r.index[a].date()), end=str(r.index[b].date()), bars=b - a,
                   weight=list(w)) for a, b, w in C["legs"]],
        switches=C["switches"], sens=C["sens"],
        eq={str(k.date()): round(float(v), 6) for k, v in C["main"]["res"]["eq"].items()},
        alloc={c: list(map(float, W[c].round(4).tolist())) for c in W.columns},
        dates=[str(k.date()) for k in r.index],
    )
    json.dump(doc, open(os.path.join(OUT, "_v3_bt.json"), "w", encoding="utf-8"),
              ensure_ascii=False, indent=2)

    summary = dict(ok=True, code="GD_ROT_V3",
                   theme=(f"成长/红利风格轮动 v3(回撤控制: {rule.RATIO_LOW:.2f}/{rule.RATIO_HIGH:.2f}"
                          f" + 双侧 MA{rule.MA_TREND} 闸门 + 现金)"),
                   years=round(m["years"], 1), st_ann=m["ann"], st_mdd=m["dd"],
                   base_ann=engine.metrics(C["nv"]["bh_d"])["ann"],
                   base_mdd=engine.metrics(C["nv"]["bh_d"])["dd"],
                   switches=len(C["switches"]), cash_mean=round(float(W["cash"].mean()), 3),
                   v2_ann=C["v2"]["m"]["ann"], v2_mdd=C["v2"]["m"]["dd"],
                   sharpe=m["shp"], calmar=m["calmar"], params=doc["params"])
    with open(os.path.join(OUT, "_v3_bt.jsonl"), "w", encoding="utf-8") as f:
        f.write(json.dumps(summary, ensure_ascii=False) + "\n")

    print("图:", plot_nav(C))
    print("图:", plot_alloc(C))
    print("图:", plot_dual(C))
    return summary


def main():
    write_products(compute(refresh="--refresh" in sys.argv))


if __name__ == "__main__":
    main()
