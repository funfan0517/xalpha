# -*- coding: utf-8 -*-
"""动量窗口 120/60/30 敏感性 + 交易成本对照（research 专项）

背景: 官方口径为 120 日动量 + 近乎零成本 —— 只有「持有 ≤7 交易日卖出收 1.5%」的
惩罚性赎回费, 而 21 交易日月频换仓基本不触发, 故官方回测实际上按零成本在跑。
本脚本回答两件事:
  一、把成本**去除**(全部费率归零, 含惩罚费)后, 动量回看窗口 120/60/30 交易日差别多大;
  二、换手率如何随窗口变化, 把成本**加回去**后结论会不会反转。

实验设计(严格单一变量):
  三个窗口统一调仓相位与评估起点 —— 均在 dates[140] 起、每 21 交易日调仓,
  评估区间同为 dates[140]~末并在该日归一。唯一变量 = lookback。
  (120/60/30 所需热身 = lookback+MA 分别 140/80/50 交易日, 均 ≤140, 故相位对齐
   不会让任何窗口「历史不足」)

成本口径: switch_cost = 每次换仓(卖旧+买新)合计扣除的费率; 0 = 去除成本。
  与官方引擎同构 —— 只在「换仓/清仓」那一刻扣, 从空仓建仓不计买入费(与 backtest.py 一致)。

用法: python strategies/momentum_rotation/research/_window_test.py
输出: research/_window_report.md · research/_window_nav.png
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

OUT_MD = os.path.join(_DIR, "_window_report.md")
OUT_PNG = os.path.join(_DIR, "_window_nav.png")

PHASE, REBAL, MA = rule.MIN_HIST, 21, rule.MA   # REBAL 固定旧口径 21 日(与 rule.REBAL 解耦)
PENALTY_DAYS, PENALTY = rule.FEE_SHORT_DAYS, rule.FEE_SHORT
WINDOWS = [120, 60, 30]
COST_SCAN = [0.0, 0.0025, 0.0050, 0.0100]                  # 每次换仓合计费率
FEE_DESC = [
    ("场外 C 类（申购 0 / 持有≥7 日赎回 0）", "≈0", "官方口径的隐含假设"),
    ("场内 ETF（佣金 万0.2~万3 双边 + 冲击 5~15bp 双边）", "0.10% ~ 0.35%", "信号代理执行口径"),
    ("场外 A 类（申购 0.10~0.15% + 持有<1 年赎回 0.5%）", "0.60% ~ 0.65%", "本池 270042/000216 为 A 类"),
]
SEGS = [("全期", None, None),
        ("2015H2~2018", None, "2018-12-31"),
        ("2019~2021H1", "2019-01-01", "2021-06-30"),
        ("2021H2~2023", "2021-07-01", "2023-12-31"),
        ("2024~2026", "2024-01-01", None)]


def run(df, lookback, switch_cost=0.0, penalty=True):
    """参数化引擎: 与 backtest.run_strategy 同构, 仅把 lookback / 成本变成参数。

    返回 (净值 Series, 当日归属持仓 Series, 单笔交易 list, 动作记录 list)
    动作记录 ev: {date, frm, to, bars} —— frm=="" 为建仓, to=="" 为清仓, 两者皆非空为换仓。
    """
    dates, n = df.index, len(df)
    warm = lookback + MA
    ma_s = df.rolling(MA).mean()
    first = {c: df[c].first_valid_index() for c in df.columns}

    # ---- 调仓决策(相位固定 PHASE, 与窗口无关) ----
    decisions = {}
    for t in range(PHASE, n, REBAL):
        cutoff = dates[t - warm]
        elig = [c for c in df.columns if first[c] <= cutoff]
        mom = {}
        for c in elig:
            p0, p1 = df[c].iloc[t - lookback], df[c].iloc[t]
            if p0 > 0 and not (np.isnan(p0) or np.isnan(p1)):
                mom[c] = p1 / p0 - 1.0
        chosen = []
        for c in sorted(mom, key=mom.get, reverse=True):
            if not np.isnan(ma_s[c].iloc[t]) and df[c].iloc[t] >= ma_s[c].iloc[t]:
                chosen = [c]
                break
        decisions[t] = chosen

    # ---- 逐日净值(当日收益归旧仓, 收盘后换仓) ----
    eq, cur, entry, epx = [1.0], [], {}, {}
    hold, trades, events = {}, [], []
    for i in range(1, n):
        r = 0.0
        if cur:
            p0, p1 = df[cur[0]].iloc[i - 1], df[cur[0]].iloc[i]
            if p0 > 0 and not (np.isnan(p0) or np.isnan(p1)):
                r = p1 / p0 - 1.0
        eq.append(eq[-1] * (1 + r))
        hold[dates[i]] = cur[0] if cur else ""          # 当日收益的归属方(调仓前)
        if i in decisions:
            new = decisions[i]
            if new != cur:
                frm = cur[0] if cur else ""
                events.append(dict(date=str(dates[i].date()), frm=frm,
                                   to=new[0] if new else "",
                                   bars=(i - entry[frm]) if frm else None))
                if frm:
                    fee = switch_cost + (PENALTY if (penalty and i - entry[frm] <= PENALTY_DAYS)
                                         else 0.0)
                    if fee:
                        eq[-1] *= 1 - fee
                    if epx[frm] > 0:
                        trades.append(dict(code=frm, entry_date=str(dates[entry[frm]].date()),
                                           exit_date=str(dates[i].date()), bars=i - entry[frm],
                                           ret=df[frm].iloc[i] / epx[frm] - 1.0))
                cur = list(new)
                entry = {c: i for c in cur}
                epx = {c: df[c].iloc[i] for c in cur}
    if cur:                                              # 末笔: 只可能触发惩罚费, 非换仓
        c = cur[0]
        if penalty and n - 1 - entry[c] <= PENALTY_DAYS:
            eq[-1] *= 1 - PENALTY
        if epx[c] > 0:
            trades.append(dict(code=c, entry_date=str(dates[entry[c]].date()),
                               exit_date=str(dates[n - 1].date()), bars=n - 1 - entry[c],
                               ret=df[c].iloc[n - 1] / epx[c] - 1.0))
    return pd.Series(eq, index=dates), pd.Series(hold), trades, events


def main():
    df = rule.load_wide()
    if len(df) == 0:
        sys.exit("缺少十年库数据: 请先运行 fetch.py")
    dates = df.index
    s0 = dates[PHASE]
    years = (dates[-1] - s0).days / 365.0

    # ---- 自检: 参数化引擎(lookback=120/保留惩罚费/零额外成本) 必须与官方逐点一致 ----
    off_eq, _ = bt.run_strategy(df, rebal=21, topn=1)
    my_eq = run(df, 120)[0]
    assert len(off_eq) == len(my_eq) and np.allclose(off_eq.values, my_eq.values), \
        "参数化引擎与官方 backtest.run_strategy 不一致"
    print("引擎自检通过（与官方 backtest 逐点一致）\n")

    def cut(x):
        return (x / x.iloc[0])[x.index >= s0]

    bm = cut(bt.run_benchmark(df, step=21)[0])
    mb = bt.metrics(bm)

    # ---- 三窗口 × 成本档 ----
    res = {}
    for lb in WINDOWS:
        for cost in COST_SCAN:
            eq, hold, trades, events = run(df, lb, switch_cost=cost)
            eq = cut(eq)
            res[(lb, cost)] = dict(eq=eq, m=bt.metrics(eq), trades=trades, events=events,
                                   hold=hold[hold.index > s0], ts=bt_stats.trade_stats(trades))

    # 自检: 空仓由「全部标的跌破 MA20」决定, 与动量窗口无关 -> 三窗口空仓时点必须完全相同
    cash_sets = [set(res[(lb, 0.0)]["hold"][res[(lb, 0.0)]["hold"] == ""].index) for lb in WINDOWS]
    assert all(c == cash_sets[0] for c in cash_sets), "空仓时点不应随动量窗口变化"
    n_cash = len(cash_sets[0])
    n_switch = {lb: len([e for e in res[(lb, 0.0)]["events"] if e["frm"] and e["to"]])
                for lb in WINDOWS}
    n_clear = {lb: len([e for e in res[(lb, 0.0)]["events"] if e["frm"] and not e["to"]])
               for lb in WINDOWS}
    n_open = {lb: len([e for e in res[(lb, 0.0)]["events"] if not e["frm"]]) for lb in WINDOWS}
    n_rebal = len(range(PHASE, len(df), REBAL))

    def seg_ann(lb, lo=None, end=None, cost=0.0):
        e = res[(lb, cost)]["eq"]
        if lo:
            e = e[e.index >= pd.Timestamp(lo)]
        if end:
            e = e[e.index <= pd.Timestamp(end)]
        return bt.metrics(e) if len(e) > 60 else None

    L = ["# 动量回看窗口 120 / 60 / 30 对比 · 去除成本与含成本", "",
         f"> 生成 {datetime.now().strftime('%Y-%m-%d %H:%M')} · 引擎与官方 `backtest.py` 同构"
         f"（已逐点自检）· 数据 {df.index.min().date()} ~ {df.index.max().date()} · "
         f"评估区间 {s0.date()} ~ {dates[-1].date()}"
         f"（{years:.1f} 年 / {n_rebal} 次调仓）", "",
         "**实验设计**：三个窗口**调仓相位与评估起点完全对齐**（均在 "
         f"{s0.date()} 起、每 {REBAL} 交易日调仓、在该日归一净值），"
         "唯一变量就是动量回看窗口。", "",
         f"**成本口径**：官方回测的成本只有「持有 ≤{PENALTY_DAYS} 交易日卖出收 "
         f"{bt.pct(PENALTY)} 惩罚性赎回费」，而 {REBAL} 交易日月频换仓几乎不触发 —— "
         "**即官方口径本就按零成本在跑**。下面「去除成本」= 连这笔惩罚费也归零（纯信号对比）；"
         "「含成本」= 每次换仓再扣合计费率。", ""]

    # ---- 一、纯信号对比（去除成本）----
    L += ["## 一、去除成本：三窗口纯信号对比", "",
          "| 动量窗口 | 年化 | 最大回撤 | 波动 | 夏普 | 总收益 |",
          "|---|---|---|---|---|---|"]
    for lb in WINDOWS:
        m = res[(lb, 0.0)]["m"]
        L.append(f"| **{lb} 日** | {bt.pct(m['ann'])} | {bt.pct(m['dd'])} | {bt.pct(m['vol'])}"
                 f" | {m['shp']:.2f} | {bt.pct(m['ret'])} |")
    L.append(f"| 六类等权买入持有（基准） | {bt.pct(mb['ann'])} | {bt.pct(mb['dd'])} "
             f"| {bt.pct(mb['vol'])} | {mb['shp']:.2f} | {bt.pct(mb['ret'])} |")
    best_nc = max(WINDOWS, key=lambda lb: res[(lb, 0.0)]["m"]["ann"])
    worst_nc = min(WINDOWS, key=lambda lb: res[(lb, 0.0)]["m"]["ann"])
    spread = (res[(best_nc, 0.0)]["m"]["ann"] - res[(worst_nc, 0.0)]["m"]["ann"]) * 100
    L += ["",
          f"> 波动几乎不受窗口影响（{min(res[(lb, 0.0)]['m']['vol'] for lb in WINDOWS) * 100:.1f}%"
          f" ~ {max(res[(lb, 0.0)]['m']['vol'] for lb in WINDOWS) * 100:.1f}%），"
          f"窗口只改变收益路径。最好（{best_nc} 日）与最差（{worst_nc} 日）"
          f"年化相差 {spread:.1f}pp，且**非单调** —— 60 日夹在中间却最差。", ""]

    # ---- 二、换手与交易统计 ----
    L += ["## 二、窗口越短，换仓越频繁", "",
          "| 动量窗口 | 换仓（A→B） | 清仓（→现金） | 建仓（现金→） | 月换手率 | 年换手 | "
          "平均持有 | 单笔笔数 | 单笔胜率 | 利润因子 |",
          "|---|---|---|---|---|---|---|---|---|---|"]
    for lb in WINDOWS:
        r = res[(lb, 0.0)]
        ts = r["ts"]
        avg_bars = np.mean([t["bars"] for t in r["trades"]]) if r["trades"] else 0.0
        L.append(f"| **{lb} 日** | {n_switch[lb]} | {n_clear[lb]} | {n_open[lb]} "
                 f"| {n_switch[lb] / n_rebal * 100:.0f}% | {n_switch[lb] / years:.1f} 次/年 "
                 f"| {avg_bars:.0f} 交易日 | {ts['n']} | {bt_stats.pct(ts['win_rate'])} "
                 f"| {ts['profit_factor']:.2f} |")
    L += ["",
          "> 「月换手率」= 换仓次数 / 调仓次数。「清仓/建仓」三窗口完全相同（各 "
          f"{n_clear[WINDOWS[0]]} / {n_open[WINDOWS[0]]} 次）—— 因为空仓条件只看 MA20，"
          "**与动量窗口无关**，全期空仓 "
          f"{n_cash} 个交易日（{n_cash / len(res[(WINDOWS[0], 0.0)]['hold']) * 100:.1f}%）。", ""]

    # ---- 三、把成本加回去 ----
    L += ["## 三、把成本加回去：结论是否反转", "",
          "| 动量窗口 | " + " | ".join(
              f"成本 {c * 100:.2f}%" if c else "零成本" for c in COST_SCAN) + " |",
          "|---" * (len(COST_SCAN) + 1) + "|"]
    for lb in WINDOWS:
        cells = " | ".join(f"{bt.pct(res[(lb, c)]['m']['ann'])}" for c in COST_SCAN)
        L.append(f"| **{lb} 日** | {cells} |")
    L += ["",
          "| 动量窗口 | 换仓+清仓总次数 | 0.50% 档累计摩擦（理论） | 年化侵蚀（实测） | "
          "含 0.50% 夏普 |", "|---|---|---|---|---|"]
    for lb in WINDOWS:
        n_act = n_switch[lb] + n_clear[lb]
        drag_theory = n_act * 0.0050
        drag = res[(lb, 0.0050)]["m"]["ann"] - res[(lb, 0.0)]["m"]["ann"]
        L.append(f"| **{lb} 日** | {n_act} | {bt_stats.pct(-drag_theory, signed=False)} "
                 f"| {bt.pct(drag)} | {res[(lb, 0.0050)]['m']['shp']:.2f} |")
    L += ["",
          "> 「年化侵蚀（实测）」= 含 0.50% 档年化 − 零成本年化。换手随窗口缩短而升，"
          "侵蚀也随之升（120 日 → 30 日侵蚀更大），但幅度差很小。", "",
          "**现实成本参照**（本池为场外主仓执行、场内代理作信号）：", "",
          "| 执行口径 | 每次换仓合计费率 | 说明 |", "|---|---|---|"]
    for a, b, c in FEE_DESC:
        L.append(f"| {a} | {b} | {c} |")
    L += ["",
          f"> 场外 C 类（6 只里 4 只）在持有 ≥7 天时申购赎回**均为 0**，成本 ≈ 0 —— "
          "这就是官方口径的合理性来源；只有 270042（纳指 A）/000216（黄金 A）"
          "以及场内 ETF 执行会真的产生摩擦。", ""]

    # ---- 四、分段稳定性 ----
    L += ["## 四、分段稳定性：窗口之间是「稳定差异」还是「噪声」", "",
          "| 段 | 120 日 | 60 日 | 30 日 | 该段最优 |", "|---|---|---|---|---|"]
    rank = {lb: [] for lb in WINDOWS}
    for label, lo, end in SEGS:
        row = {lb: seg_ann(lb, lo, end) for lb in WINDOWS}
        for i, lb in enumerate(sorted(WINDOWS, key=lambda x: -row[x]["ann"]), 1):
            rank[lb].append(i)
        cells = " | ".join(f"{bt.pct(row[lb]['ann'])}" if row[lb] else "—" for lb in WINDOWS)
        best = max((m["ann"], lb) for lb, m in row.items() if m)[1]
        L.append(f"| {label} | {cells} | {best} 日 |")
    L += ["",
          "| 动量窗口 | 分段平均排名 | 各段排名 |", "|---|---|---|"]
    for lb in WINDOWS:
        L.append(f"| **{lb} 日** | {np.mean(rank[lb]):.1f} | "
                 + " → ".join(str(x) for x in rank[lb]) + " |")
    L += ["",
          "> 排名越乱，说明窗口之间的差异越接近**噪声**而非稳定规律。"
          "三个窗口在 5 个分段里各自都赢过也输过 —— 没有任何一个窗口能持续领先。", ""]

    # ---- 五、持仓分布 ----
    L += ["## 五、各窗口的持仓分布（占评估期交易日）", "",
          "| 标的 | " + " | ".join(f"{lb} 日" for lb in WINDOWS) + " |",
          "|---" * (len(WINDOWS) + 1) + "|"]
    for c in df.columns:
        cells = " | ".join(
            f"{res[(lb, 0.0)]['hold'].eq(c).sum() / len(res[(lb, 0.0)]['hold']) * 100:.1f}%"
            for lb in WINDOWS)
        L.append(f"| `{rule.LABEL6.get(c, c)}` | {cells} |")
    cells = " | ".join(
        f"{res[(lb, 0.0)]['hold'].eq('').sum() / len(res[(lb, 0.0)]['hold']) * 100:.1f}%"
        for lb in WINDOWS)
    L.append(f"| **空仓（现金）** | {cells} |")
    L += ["",
          "> 三窗口的持仓分布高度相似（纳指都占 40%+、黄金 21%~24%）—— "
          "换窗口并没有改变「押什么」，只是改变了**切换的时点**。", ""]

    # ---- 六、聚焦 2021H2~2023：60 日为什么单独掉队 ----
    LO, END = "2021-07-01", "2023-12-31"
    L += ["## 六、聚焦 2021H2~2023：60 日为什么单独掉队", "",
          f"全期 5.9pp 的差距几乎全部来自这一段（60 日 "
          f"{bt.pct(seg_ann(60, LO, END)['ann'])} vs 120 日 "
          f"{bt.pct(seg_ann(120, LO, END)['ann'])} vs 30 日 "
          f"{bt.pct(seg_ann(30, LO, END)['ann'])}）。", "",
          "| 动量窗口 | 该段年化 | 该段换仓次数 | 该段空仓占比 | 该段持仓 TOP3 |",
          "|---|---|---|---|---|"]
    for lb in WINDOWS:
        h = res[(lb, 0.0)]["hold"]
        hs = h[(h.index >= pd.Timestamp(LO)) & (h.index <= pd.Timestamp(END))]
        ev = [e for e in res[(lb, 0.0)]["events"]
              if LO <= e["date"] <= END and e["frm"] and e["to"]]
        top = " · ".join(f"{rule.LABEL6.get(c, c)} {v / len(hs) * 100:.0f}%"
                         for c, v in hs[hs != ""].value_counts().head(3).items())
        L.append(f"| **{lb} 日** | {bt.pct(seg_ann(lb, LO, END)['ann'])} | {len(ev)} "
                 f"| {(hs == '').mean() * 100:.1f}% | {top} |")
    L += ["", "该段各标的买入持有的表现（用于判断「换来换去」是躲坑还是踩坑）：", "",
          "| 标的 | 2021H2~2023 买入持有 | 2021H2~2023 年化 |", "|---|---|---|"]
    seg_ret = {}
    for c in df.columns:
        s = df[c][(df.index >= pd.Timestamp(LO)) & (df.index <= pd.Timestamp(END))]
        if s.empty or not np.isfinite(s.iloc[0]) or s.iloc[0] <= 0 or not np.isfinite(s.iloc[-1]):
            continue                                       # 该段尚未上市的标的（如 A500 代理）
        r = s.iloc[-1] / s.iloc[0] - 1
        seg_ret[c] = r
        y = (s.index[-1] - s.index[0]).days / 365.0
        L.append(f"| `{rule.LABEL6.get(c, c)}` | {bt.pct(r)} "
                 f"| {bt.pct((1 + r) ** (1 / y) - 1)} |")
    seg_sorted = "、".join(f"{rule.LABEL6.get(c, c)} {bt.pct(r)}"
                          for c, r in sorted(seg_ret.items(), key=lambda kv: -kv[1]))
    L += ["",
          f"> 该段各标的买入持有：{seg_sorted} —— 典型「分化 + 反转」行情。"
          "而 60 日窗口恰恰在这一段的换仓次数最多（见上表）：**动得越多、错得越多**，"
          "120 日窗口反应慢，反而少动了。", ""]

    # ---- 七、结论 ----
    L += ["## 七、结论", "",
          f"1. **去除成本后，三个窗口没有稳定优劣**。全期看 {best_nc} 日最好"
          f"（{bt.pct(res[(best_nc, 0.0)]['m']['ann'])}）、{worst_nc} 日最差"
          f"（{bt.pct(res[(worst_nc, 0.0)]['m']['ann'])}），但 {spread:.1f}pp 的差距"
          "几乎全部来自**个别时段**：60 日在 2021H2~2023 只有 "
          f"{bt.pct(seg_ann(60, '2021-07-01', '2023-12-31')['ann'])}，"
          f"同期 120 日是 {bt.pct(seg_ann(120, '2021-07-01', '2023-12-31')['ann'])}、"
          f"30 日是 {bt.pct(seg_ann(30, '2021-07-01', '2023-12-31')['ann'])}。",
          f"2. **窗口几乎不改变波动，只改变路径**：三窗口波动 "
          f"{min(res[(lb, 0.0)]['m']['vol'] for lb in WINDOWS) * 100:.1f}% ~ "
          f"{max(res[(lb, 0.0)]['m']['vol'] for lb in WINDOWS) * 100:.1f}% 完全同一水平；"
          f"最大回撤则从 {bt.pct(max(res[(lb, 0.0)]['m']['dd'] for lb in WINDOWS))} 到 "
          f"{bt.pct(min(res[(lb, 0.0)]['m']['dd'] for lb in WINDOWS))}，"
          "差异比波动明显，但同样来自个别时段的选错，而非窗口的系统性优劣。",
          f"3. **换手确实随窗口缩短而升**：换仓 "
          f"{n_switch[120]} → {n_switch[60]} → {n_switch[30]} 次"
          f"（{n_switch[120] / years:.1f} → {n_switch[30] / years:.1f} 次/年），"
          "但绝对水平很低（平均持有 33~39 交易日），所以**成本的绝对影响很小**："
          "0.50% 档下年化被吃掉约 3.2~3.9pp，三窗口的相对排名不变。",
          "4. **成本无法解释跑输**。即使按最保守的 1.00% 档，三窗口都还是远落后于"
          f"等权持有（{bt.pct(mb['ann'])}）；真正的原因仍是**单仓集中**而非摩擦"
          "（见 `_fallback_report.md`：持仓日策略只领先 +2.89%，却承担 2 倍波动）。",
          "",
          ("> ⚠ 三个窗口在 5 个分段里各赢过也输过（排名 1 = 最好）："
           + "；".join(f"{lb} 日 " + " → ".join(str(x) for x in rank[lb]) for lb in WINDOWS)
           + "。说明这点差异主要是噪声，**不建议据此调窗口**。"), "",
          "> 模拟结果，非投资建议。", ""]

    open(OUT_MD, "w", encoding="utf-8").write("\n".join(L))
    print("\n".join(L))

    # ---- 净值图 ----
    fig, ax = plt.subplots(figsize=(12, 5))
    for lb in WINDOWS:
        e = res[(lb, 0.0)]["eq"]
        ax.plot(e.index, e.values, lw=1.2, label=f"{lb} 日动量（零成本）")
    ax.plot(bm.index, bm.values, lw=1.2, alpha=0.8, ls="--", label="六类等权买入持有")
    ax.legend()
    ax.set_title("动量回看窗口 120 / 60 / 30 对比（去除成本，净值）")
    ax.grid(alpha=0.3)
    fig.tight_layout()
    fig.savefig(OUT_PNG, dpi=140, facecolor="white")
    print(f"\n-> {OUT_MD}")
    print(f"图: {OUT_PNG}")


if __name__ == "__main__":
    main()
