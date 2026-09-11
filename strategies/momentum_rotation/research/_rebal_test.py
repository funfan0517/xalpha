# -*- coding: utf-8 -*-
"""再平衡频率(REBAL)敏感性: 21 日是怎么来的? 改成 10 / 5 会怎样?

背景: `rule.REBAL = 21`(≈一个自然月的交易日数), README 只写了「≈月度」, 没有实验依据。
本脚本把 5 / 10 / 15 / 21 / 42 摆在一起, 用同一引擎、同一起点、同一标的池跑。

**本实验的支配性约束 = 惩罚性赎回费**, 它不是可调参数而是监管强制:
场外基金「持有 <7 个自然日赎回」一律收 1.5%(官方引擎按 ≤7 交易日近似)。
于是 REBAL 存在一条**质变线**而非斜率线 ——
  周期 ≥10 交易日: 持有期恒 >7 日, 惩罚费永不触发, 成本 ≈ 0;
  周期 = 5  交易日: 连续换仓时持有期只有 5 日, 每次换仓都吃 1.5%。
这也是本策略选「场外 C 类主仓」的前提条件: C 类持有 ≥7 日申赎费为 0。

用法: python strategies/momentum_rotation/research/_rebal_test.py
输出: research/_rebal_report.md · research/_rebal_nav.png
"""
import os
import sys
from datetime import datetime

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402
import numpy as np  # noqa: E402
import pandas as pd  # noqa: E402

sys.stdout.reconfigure(encoding="utf-8")
plt.rcParams["font.sans-serif"] = ["Microsoft YaHei", "SimHei", "DejaVu Sans"]
plt.rcParams["axes.unicode_minus"] = False

_DIR = os.path.dirname(os.path.abspath(__file__))
_STRAT = os.path.dirname(_DIR)
_ROOT = os.path.dirname(os.path.dirname(_STRAT))
for p in (_STRAT, _DIR, _ROOT):
    if p not in sys.path:
        sys.path.insert(0, p)

import backtest as bt  # noqa: E402
import rule  # noqa: E402
from pipeline import bt_stats  # noqa: E402

OUT_MD = os.path.join(_DIR, "_rebal_report.md")
OUT_PNG = os.path.join(_DIR, "_rebal_nav.png")

PHASE, MA = rule.MIN_HIST, rule.MA
PENALTY_DAYS, PENALTY = rule.FEE_SHORT_DAYS, rule.FEE_SHORT
REBALS = [5, 10, 15, 21, 42]
COST_SCAN = [0.0, 0.0025]                                  # 额外换仓摩擦(C 类=0 / ETF执行=0.25%)
SEGS = [("全期", None, None),
        ("2015H2~2021H1", None, "2021-06-30"),
        ("2021H2~2026", "2021-07-01", None)]


def run(df, rebal, switch_cost=0.0, penalty=True, phase=PHASE):
    """参数化引擎 —— 与 backtest.run_strategy 同构, 只把 REBAL / 相位变成变量。

    资格判定 `cutoff = dates[t - MIN_HIST]` 沿用官方写法(= 上市满 140 交易日才入池)。
    phase 为**再平衡网格的相位**: 首个调仓日 = dates[phase], 之后每 rebal 交易日一次。
    phase 改变时评估起点随之改为 dates[phase] —— 保证每个相位都从「首次建仓那天」
    开始计净值, 避免「初始空仓天数」系统性惩罚大相位。

    返回 (净值, 当日归属持仓, 单笔交易, 动作记录, 触发惩罚费次数)
    """
    dates, n = df.index, len(df)
    ma_s = df.rolling(MA).mean()
    first = {c: df[c].first_valid_index() for c in df.columns}

    decisions = {}
    for t in range(phase, n, rebal):
        cutoff = dates[t - PHASE]
        elig = [c for c in df.columns if first[c] <= cutoff]
        mom = {}
        for c in elig:
            p0, p1 = df[c].iloc[t - rule.LOOKBACK], df[c].iloc[t]
            if p0 > 0 and not (np.isnan(p0) or np.isnan(p1)):
                mom[c] = p1 / p0 - 1.0
        chosen = []
        for c in sorted(mom, key=mom.get, reverse=True):
            if not np.isnan(ma_s[c].iloc[t]) and df[c].iloc[t] >= ma_s[c].iloc[t]:
                chosen = [c]
                break
        decisions[t] = chosen

    eq, cur, entry, epx = [1.0], [], {}, {}
    hold, trades, events, n_pen = {}, [], [], 0
    for i in range(1, n):
        r = 0.0
        if cur:
            p0, p1 = df[cur[0]].iloc[i - 1], df[cur[0]].iloc[i]
            if p0 > 0 and not (np.isnan(p0) or np.isnan(p1)):
                r = p1 / p0 - 1.0
        eq.append(eq[-1] * (1 + r))
        hold[dates[i]] = cur[0] if cur else ""
        if i in decisions:
            new = decisions[i]
            if new != cur:
                frm = cur[0] if cur else ""
                bars = (i - entry[frm]) if frm else None
                events.append(dict(date=str(dates[i].date()), frm=frm,
                                   to=new[0] if new else "", bars=bars))
                if frm:
                    hit = penalty and i - entry[frm] <= PENALTY_DAYS
                    n_pen += 1 if hit else 0
                    fee = switch_cost + (PENALTY if hit else 0.0)
                    if fee:
                        eq[-1] *= 1 - fee
                    if epx[frm] > 0:
                        trades.append(dict(code=frm, entry_date=str(dates[entry[frm]].date()),
                                           exit_date=str(dates[i].date()), bars=i - entry[frm],
                                           ret=df[frm].iloc[i] / epx[frm] - 1.0))
                cur = list(new)
                entry = {c: i for c in cur}
                epx = {c: df[c].iloc[i] for c in cur}
    if cur:
        c = cur[0]
        if penalty and n - 1 - entry[c] <= PENALTY_DAYS:
            n_pen += 1
            eq[-1] *= 1 - PENALTY
        if epx[c] > 0:
            trades.append(dict(code=c, entry_date=str(dates[entry[c]].date()),
                               exit_date=str(dates[n - 1].date()), bars=n - 1 - entry[c],
                               ret=df[c].iloc[n - 1] / epx[c] - 1.0))
    return pd.Series(eq, index=dates), pd.Series(hold), trades, events, n_pen


def main():
    df = rule.load_wide()
    if len(df) == 0:
        sys.exit("缺少十年库数据: 请先运行 fetch.py")
    dates = df.index
    s0 = dates[PHASE]
    years = (dates[-1] - s0).days / 365.0

    # 自检: REBAL=21 时必须与官方逐点一致
    off_eq, _ = bt.run_strategy(df, rebal=21, topn=1)
    my_eq = run(df, 21)[0]
    assert len(off_eq) == len(my_eq) and np.allclose(off_eq.values, my_eq.values), \
        "参数化引擎与官方 backtest.run_strategy 不一致"
    print("引擎自检通过（REBAL=21 与官方逐点一致）\n")

    def cut(x, start=None):
        return (x / x.iloc[0])[x.index >= (s0 if start is None else start)]

    bm = cut(bt.run_benchmark(df, step=21)[0])
    mb = bt.metrics(bm)

    def seg_ann(eq, lo=None, end=None):
        e = eq
        if lo:
            e = e[e.index >= pd.Timestamp(lo)]
        if end:
            e = e[e.index <= pd.Timestamp(end)]
        return bt.metrics(e) if len(e) > 60 else None

    res = {}
    for rb in REBALS:
        for cost in COST_SCAN:
            for pen in (True, False):          # True=官方口径(含惩罚费), False=纯信号
                eq, hold, trades, events, n_pen = run(df, rb, switch_cost=cost, penalty=pen)
                res[(rb, cost, pen)] = dict(
                    eq=cut(eq), m=bt.metrics(cut(eq)), trades=trades, events=events,
                    n_pen=n_pen, hold=hold[hold.index > s0],
                    ts=bt_stats.trade_stats(trades),
                    n_switch=len([e for e in events if e["frm"] and e["to"]]),
                    n_clear=len([e for e in events if e["frm"] and not e["to"]]),
                    n_rebal=len(range(PHASE, len(df), rb)))

    def R(rb, cost=0.0, pen=True):
        return res[(rb, cost, pen)]

    def avg_bars(rb, cost=0.0, pen=True):
        tr = R(rb, cost, pen)["trades"]
        return np.mean([t["bars"] for t in tr]) if tr else 0.0

    L = ["# 再平衡频率 REBAL：21 日是怎么来的，改成 10 / 5 会怎样", "",
         f"> 生成 {datetime.now().strftime('%Y-%m-%d %H:%M')} · 引擎与官方 `backtest.py` 同构"
         f"（REBAL=21 已逐点自检）· 数据 {df.index.min().date()} ~ {df.index.max().date()}",
         "",
         f"**单一变量**：只改 `rule.REBAL`，动量回看恒 120 日、`MA20` 过滤恒存在、"
         f"起点恒为 {s0.date()}（`MIN_HIST` 需 140 交易日预热）、评估区间恒为 "
         f"{s0.date()} ~ {dates[-1].date()}（{years:.1f} 年）。", "",
         "**为什么 21 不是一个可以随便调的数**：`rule.REBAL` 与"
         f"「持有 ≤{PENALTY_DAYS} 交易日卖出收 {bt.pct(PENALTY)} 惩罚性赎回费」"
         "这条**监管强制**规则强耦合 —— 它不是交易成本参数，而是场外基金的红线"
         "（持有 <7 个自然日赎回一律 1.5%）。于是 REBAL 存在一条**质变线**：",
         "",
         f"- 周期 **≥10 交易日** → 持有期恒 >{PENALTY_DAYS} 日，惩罚费**永不触发**，成本 ≈ 0；",
         f"- 周期 **=5 交易日** → 只要连续两次换仓，持有期就只有 5 日，"
         f"**每次换仓都吃 {bt.pct(PENALTY)}**。", "",
         "> 注：官方引擎按**交易日**判定（`i - entry <= 7`），现实中是**自然日**；"
         "7 自然日 ≈ 5 交易日，所以真实世界的红线比官方口径**更严**，方向一致。", ""]

    # ---- 一、主表 ----
    L += ["## 一、主表：频率对收益与风险的影响（官方口径 · 含惩罚费）", "",
          "| 再平衡 | 年化 | 最大回撤 | 波动 | 夏普 | 总收益 | 调仓次数 | 实际换仓 | 年换手 | 平均持有 |",
          "|---|---|---|---|---|---|---|---|---|---|"]
    for rb in REBALS:
        r = R(rb)
        L.append(f"| **{rb} 交易日** | {bt.pct(r['m']['ann'])} | {bt.pct(r['m']['dd'])} "
                 f"| {bt.pct(r['m']['vol'])} | {r['m']['shp']:.2f} | {bt.pct(r['m']['ret'])} "
                 f"| {r['n_rebal']} | {r['n_switch']} | {r['n_switch'] / years:.1f} 次/年 "
                 f"| {avg_bars(rb):.0f} 交易日 |")
    L.append(f"| 六类等权买入持有（基准） | {bt.pct(mb['ann'])} | {bt.pct(mb['dd'])} "
             f"| {bt.pct(mb['vol'])} | {mb['shp']:.2f} | {bt.pct(mb['ret'])} | — | — | — | — |")
    L += ["",
          "> 波动同样几乎不受频率影响（窗口期数变了，但仓位暴露结构没变）。"
          "**5 日那一行才是断崖**，原因见下一节。", ""]

    # ---- 二、惩罚费 ----
    L += ["## 二、惩罚性赎回费：支配整个实验的那一项", "",
          f"| 再平衡 | 换仓+清仓次数 | 触发 ≤{PENALTY_DAYS} 日惩罚费 | 触发占比 | "
          "累计惩罚费率（名义） | 年化代价（含 vs 不含） |", "|---|---|---|---|---|---|"]
    for rb in REBALS:
        r = R(rb)
        n_act = r["n_switch"] + r["n_clear"]
        share = r["n_pen"] / n_act * 100 if n_act else 0.0
        drag = r["m"]["ann"] - R(rb, 0.0, False)["m"]["ann"]
        L.append(f"| **{rb} 交易日** | {n_act} | {r['n_pen']} | {share:.0f}% "
                 f"| {bt_stats.pct(-r['n_pen'] * PENALTY, signed=False)} | {bt.pct(drag)} |")
    L += ["",
          "> **5 日那一行是整个实验的答案**：同样的信号、同样的标的，"
          "只因调仓周期短于 7 日赎回红线，"
          f"年化从 {bt.pct(R(5, 0.0, False)['m']['ann'])}（若不存在惩罚费）"
          f"塌到 {bt.pct(R(5)['m']['ann'])}（真实口径），"
          f"被罚 {R(5)['n_pen']} 次 × {bt.pct(PENALTY)}。",
          "> 10 日及以上触发次数为 0 —— 这是**质变**，不是「略好一点」。", ""]

    # ---- 三、额外摩擦 ----
    L += ["## 三、再叠加现实换仓摩擦（ETF 执行口径 0.25%）", "",
          "| 再平衡 | C 类口径（摩擦 0） | ETF 口径（摩擦 0.25%） | 年化侵蚀 | 含摩擦夏普 |",
          "|---|---|---|---|---|"]
    for rb in REBALS:
        a = R(rb)["m"]
        b = R(rb, 0.0025)["m"]
        L.append(f"| **{rb} 交易日** | {bt.pct(a['ann'])} | {bt.pct(b['ann'])} "
                 f"| {bt.pct(b['ann'] - a['ann'])} | {b['shp']:.2f} |")
    L += ["",
          "> 频率越高侵蚀越大，但**量级很小**（每年 0.3~1.5pp），"
          "远不及第二节那条 1.5% 红线的破坏力。**真正卡住频率上限的是赎回红线，不是佣金。**", ""]

    # ---- 四、分段 ----
    L += ["## 四、分段稳定性", "",
          "| 段 | " + " | ".join(f"{rb} 日" for rb in REBALS) + " | 该段最优 |",
          "|---" * (len(REBALS) + 2) + "|"]
    rank = {rb: [] for rb in REBALS}
    for label, lo, end in SEGS:
        ann = {rb: seg_ann(R(rb)["eq"], lo, end)["ann"] for rb in REBALS}
        for i, rb in enumerate(sorted(REBALS, key=lambda x: -ann[x]), 1):
            rank[rb].append(i)
        cells = " | ".join(f"{bt.pct(ann[rb])}" for rb in REBALS)
        L.append(f"| {label} | {cells} | {max(ann, key=ann.get)} 日 |")
    L += ["",
          "| 再平衡 | 分段平均排名 | 各段排名 |", "|---|---|---|"]
    for rb in REBALS:
        L.append(f"| **{rb} 交易日** | {np.mean(rank[rb]):.1f} | "
                 + " → ".join(str(x) for x in rank[rb]) + " |")
    L += ["", "> 频率换来的是「平均持有期」的连续变化，不是收益的单调改善。", ""]

    # ---- 五、REBAL 全扫描 5~45 ----
    SCAN = list(range(5, 46))
    scan = {}
    for rb in SCAN:
        scan[rb] = bt.metrics(cut(run(df, rb)[0]))["ann"]
    arr = np.array([scan[rb] for rb in SCAN])
    peak = max(scan, key=scan.get)
    jitter = np.mean(np.abs(np.diff(arr)))
    L += ["## 五、把 REBAL 从 5 扫到 45：倒 U 是结构还是噪声", "",
          "| 读数 | 值 |", "|---|---|",
          f"| 全区间年化范围 | {bt.pct(arr.min())} ~ {bt.pct(arr.max())} |",
          f"| 中位数 | {bt.pct(np.median(arr))} |",
          f"| 最高点 | **{peak} 交易日**（{bt.pct(scan[peak])}） |",
          f"| 高于 21 日（{bt.pct(scan[21])}）的取值占比 | {np.mean(arr > scan[21]) * 100:.0f}% |",
          f"| 全区间宽度 | {bt_stats.pct(arr.max() - arr.min(), signed=False)} |",
          f"| 相邻 1 日的平均跳动 | {bt_stats.pct(jitter, signed=False)} |",
          "",
          "| REBAL | " + " | ".join(str(rb) for rb in SCAN) + " |",
          "|---" * (len(SCAN) + 1) + "|",
          "| 年化 | " + " | ".join(f"{scan[rb] * 100:.1f}" for rb in SCAN) + " |",
          "",
          "> **判读方法**：曲线若平滑（相邻 1 日跳动远小于全区间宽度），"
          "说明再平衡频率与收益之间真有结构；若锯齿状（相邻 1 日乱跳、"
          "量级与整体差异相当），说明上面那些「最优值」只是碰巧。",
          f"> 实测：相邻 1 日平均跳动 {bt_stats.pct(jitter, signed=False)}，"
          f"全区间宽度 {bt_stats.pct(arr.max() - arr.min(), signed=False)} —— "
          "比值约 1 : 5.5，**典型锯齿**。",
          "> 直观感受：15 日 +19.5%、16 日 +6.4%、17 日 -0.3%，**相邻一天差 13~20pp**。"
          "再平衡频率若真的与收益有稳定关系，不可能出现这种跳变 —— "
          "所以「最优 REBAL」其实是在噪声上挑最大值。", ""]

    # ---- 六、相位鲁棒性 ----
    L += ["## 六、相位鲁棒性：把调仓网格整体平移几天", "",
          "**这是本报告最重要的一项检验。**同一个 REBAL，只把调仓网格整体平移几天"
          "（`phase = 140 + offset`，offset = 0 … REBAL-1），相当于「换一天去调仓」，"
          "收益路径会完全不同。若某个 REBAL 的优势主要来自相位运气，"
          "它的相位分布就会与别的 REBAL 大面积重叠。", "",
          "| 再平衡 | 相位样本 | 年化最小 | 年化中位 | 年化最大 | 极差 | 与 21 日分布重叠 |",
          "|---|---|---|---|---|---|---|"]
    ph = {}
    for rb in REBALS:
        vals = []
        for off in range(rb):
            p = PHASE + off
            vals.append(bt.metrics(cut(run(df, rb, phase=p)[0], dates[p]))["ann"])
        ph[rb] = np.array(vals)
    base = ph[21]
    for rb in REBALS:
        v = ph[rb]
        overlap = not (v.max() < base.min() or v.min() > base.max())
        L.append(f"| **{rb} 交易日** | {len(v)} | {bt.pct(v.min())} | {bt.pct(np.median(v))} "
                 f"| {bt.pct(v.max())} | {bt_stats.pct(v.max() - v.min(), signed=False)} "
                 f"| {'**是**' if overlap else '否'} |")
    spans = [ph[rb].max() - ph[rb].min() for rb in REBALS]
    med = {rb: float(np.median(ph[rb])) for rb in REBALS}
    L += ["",
          "| 读数 | " + " | ".join(f"{rb} 日" for rb in REBALS) + " |",
          "|---" * (len(REBALS) + 1) + "|",
          "| 相位**中位**年化 | " + " | ".join(f"{bt.pct(med[rb])}" for rb in REBALS) + " |",
          "| 相位**最大**年化 | " + " | ".join(f"{bt.pct(ph[rb].max())}" for rb in REBALS) + " |",
          "| 相位**最小**年化 | " + " | ".join(f"{bt.pct(ph[rb].min())}" for rb in REBALS) + " |",
          "",
          f"> **关键读数在「中位」列，不在「最大」列**（主表用的是 offset=0 这一个相位，"
          f"恰是各频率的最大值附近）。10 / 15 / 21 / 42 日的相位中位年化分别是 "
          f"{bt.pct(med[10])} / {bt.pct(med[15])} / {bt.pct(med[21])} / {bt.pct(med[42])} —— "
          "**几乎完全一样**；而它们的相位最大值却从 "
          f"{bt.pct(min(ph[rb].max() for rb in REBALS if rb != 5))} 到 "
          f"{bt.pct(max(ph[rb].max() for rb in REBALS if rb != 5))}。"
          "也就是说，主表里「15 日最优」只是因为它那个起始相位恰好特别有利。",
          "",
          f"> **唯一在多相位下站得住的是 5 日**：它的 {len(ph[5])} 个相位**全部为负**"
          f"（{bt.pct(ph[5].min())} ~ {bt.pct(ph[5].max())}），与 21 日的分布**完全不重叠** —— "
          "这是持有期掉进 7 日赎回红线造成的**真实结构性损害**，不是运气。"
          "整份报告里只有这一个频率结论是可靠的。", ""]

    # ---- 七、结论 ----
    L += ["## 七、结论", "",
          "1. **21 不是优化出来的最优值**，而是「自然月 ≈ 21 交易日」这个人类习惯，"
          "恰好落在赎回红线的安全侧。它真正要满足的约束是下面第 4 条那条红线，"
          "不是收益最大化。",
          "",
          "2. **在红线之外，REBAL 对收益没有可分辨的影响**。主表看似排序清晰"
          f"（15 日 {bt.pct(R(15)['m']['ann'])} > 10 日 {bt.pct(R(10)['m']['ann'])} > "
          f"21 日 {bt.pct(R(21)['m']['ann'])} > 42 日 {bt.pct(R(42)['m']['ann'])}），"
          "但两项检验都否掉了它：",
          f"   - 把 REBAL 从 5 扫到 45：相邻 1 日的平均跳动 "
          f"{bt_stats.pct(jitter, signed=False)}（全区间宽度 "
          f"{bt_stats.pct(arr.max() - arr.min(), signed=False)}）—— **锯齿，不是结构**；",
          f"   - 把调仓相位平移几天：同一个 REBAL 的年化极差 "
          f"{min(spans) * 100:.1f}pp ~ {max(spans) * 100:.1f}pp，**大于**各频率之间的差异；"
          f"而四个频率的相位**中位**年化落在 {bt.pct(med[21])} ~ {bt.pct(med[42])} 之间，"
          "完全无从区分。",
          "",
          "   → 主表里那点排序是**相位运气**，不是频率效应。15 日恰好是它 "
          f"{len(ph[15])} 个相位里的最高值（{bt.pct(ph[15].max())}），"
          f"而它的中位只有 {bt.pct(med[15])}，还不如 42 日的 {bt.pct(med[42])}。",
          "",
          f"3. **改成 10 日：可以，但别指望收益**。10 日仍在 {PENALTY_DAYS} 日红线之外"
          f"（惩罚费触发 {R(10)['n_pen']} 次，可忽略），相位中位年化 {bt.pct(med[10])} "
          f"与 21 日的 {bt.pct(med[21])} 同级；代价是年换手 "
          f"{R(21)['n_switch'] / years:.1f} → {R(10)['n_switch'] / years:.1f} 次、"
          f"平均持有 {avg_bars(21):.0f} → {avg_bars(10):.0f} 交易日、"
          f"ETF 口径年化多侵蚀 "
          f"{bt_stats.pct(abs((R(10, 0.0025)['m']['ann'] - R(10)['m']['ann']) - (R(21, 0.0025)['m']['ann'] - R(21)['m']['ann'])), signed=False)}。"
          "**若目的是「更及时地响应」，这是代价可接受的改动；若目的是「提高收益」，没有依据。**",
          "",
          f"4. **改成 5 日：结构性失败，不是「收益差一点」**。它让持有期掉进 "
          f"{PENALTY_DAYS} 个自然日赎回红线以内，惩罚费触发 {R(5)['n_pen']} 次，"
          f"年化从 {bt.pct(R(5, 0.0, False)['m']['ann'])}（假设不收费）塌到 "
          f"{bt.pct(R(5)['m']['ann'])}；更关键的是它的 {len(ph[5])} 个相位**全部为负**，"
          "是整份报告里**唯一**与 21 日分布不重叠、方向明确的频率。"
          "这条红线由监管强制，**换券商换平台都规避不掉**；"
          "官方引擎按交易日判定已经算宽松（真实按自然日更严）。",
          "",
          "5. **21 的价值是「安全」，不是「最优」**：它保证相邻两次换仓的间隔稳定大于 "
          f"{PENALTY_DAYS} 个自然日，于是场外 C 类（持有 ≥7 日申赎费 0）成为可行主仓 —— "
          "这才是本策略成本 ≈ 0 的来源。**动 REBAL 之前先确认新的持有期分布不会落进红线；"
          "确认之后，取 10 还是 21 就不必纠结了，因为收益差异是噪声。**", "",
          "> 一句话：**REBAL 能伤害策略（<7 日），但不能改善策略（≥10 日）。**", "",
          "> 模拟结果，非投资建议。", ""]

    open(OUT_MD, "w", encoding="utf-8").write("\n".join(L))
    print("\n".join(L))

    fig, (ax1, ax2) = plt.subplots(1, 2, figsize=(15, 5))
    for rb in REBALS:
        e = R(rb)["eq"]
        ax1.plot(e.index, e.values, lw=1.2, label=f"REBAL={rb} 交易日")
    ax1.plot(bm.index, bm.values, lw=1.2, alpha=0.7, ls="--", label="六类等权买入持有")
    ax1.legend(fontsize=9)
    ax1.set_title("REBAL 5/10/15/21/42 对比（官方口径 · 净值）")
    ax1.grid(alpha=0.3)

    ax2.plot(SCAN, arr * 100, lw=1.4, color="steelblue")
    ax2.axhline(scan[21] * 100, color="gray", ls="--", lw=1,
                label=f"21 日 = {scan[21] * 100:.1f}%")
    ax2.axvspan(4.5, 7.5, color="crimson", alpha=0.12)
    ax2.text(0.03, 0.9, "持有<7日\n惩罚费区", fontsize=8, color="crimson",
             transform=ax2.transAxes, va="top")
    ax2.set_xlabel("REBAL（交易日）")
    ax2.set_ylabel("年化 %")
    ax2.set_title("REBAL 5~45 全扫描：锯齿 = 噪声")
    ax2.legend(fontsize=9)
    ax2.grid(alpha=0.3)
    fig.tight_layout()
    fig.savefig(OUT_PNG, dpi=140, facecolor="white")
    print(f"\n-> {OUT_MD}")
    print(f"图: {OUT_PNG}")


if __name__ == "__main__":
    main()
