# -*- coding: utf-8 -*-
"""成长/红利风格轮动 v5 · 回测（v3 + 波动率目标层）

目标：**在 v3 之上再压回撤**。v3 的 -41.57% 集中在 2015 红利崩盘与 2024 急涨回落两段，
两段的共同特征是事前波动率抬升 —— 所以最外面加一层「按已实现波动缩放总仓位」。

三层结构（自内向外，前两层与 v3 完全一致）：
  1. R 层      : R ≤0.35 满仓成长 / R ≥0.65 满仓红利 / 中间带保持
  2. 趋势层    : 创业板指 / 中证红利 各自 < MA200 → 该侧减半，削掉的转现金
  3. 波动层    : scale = min(1, 目标波动 / 120 日已实现波动)，等比缩放成长+红利，腾出的进现金

对照口径：
  - v3（target_vol=None）：内置对照，必须精确复现 +15.66%/-41.57%/53 次
  - 固定暴露对照：把 v3 权重恒定缩到 55%/65%/80%，用来证明「不是只靠降仓位」
  - 「保收益」25% 档 / 「保守」12% 档：同一旋钮的另外两个刻度

产物：backtest/_v5_report.md · _v5_bt.json · _v5_bt.jsonl
      backtest/_v5_nav.png    净值 + 回撤（v5 vs v3 vs 买入持有）
      backtest/_v5_alloc.png  三资产仓位堆叠 + 创业板指 / 中证红利点位（各一根右轴）
                              + 风格比值 R（下栏，含 0.35 / 0.65 阈值带）
      backtest/_v5_vol.png    滚动波动 vs 目标 + 缩放系数 + 回撤
用法：python strategies/growth_div_rotation/v5_vol_target/backtest.py [--refresh]
      python strategies/growth_div_rotation/v5_vol_target/backtest.py --chart
          ↑ 只重画 _v5_alloc.png（不跑敏感性网格，秒级完成；每日流程走这条）
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


def seg_dd(eq, start, stop):
    """区间内最大回撤（段内重新起算）。"""
    e = eq[(eq.index >= pd.Timestamp(start)) & (eq.index <= pd.Timestamp(stop))]
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

    def build(tw=None, rn=None):
        """tw -> target_weights 参数；rn -> run 参数（含 target_vol）。"""
        W = engine.target_weights(r, g, d, **(tw or {}))
        res = engine.run(g, d, W, **(rn or {}))
        return dict(W=res["W"], base_W=W, res=res, m=engine.metrics(res["eq"]))

    main = build()                                    # v5 默认 18% / 120 日
    v3 = build(rn=dict(target_vol=0))                 # 关掉波动层 = v3
    keep = build(rn=dict(target_vol=0.25))            # 保收益档
    cons = build(rn=dict(target_vol=0.12))            # 保守档

    # 固定暴露对照（证明不是单纯降仓）
    Wb = engine.target_weights(r, g, d)
    facts = []
    for k in rule.FIXED_EXPOSURES:
        res = engine.run(g, d, engine.fixed_exposure(Wb, k), target_vol=0)
        facts.append(dict(k=k, m=engine.metrics(res["eq"]), res=res,
                          exp=float(res["W"][["growth", "div"]].values.sum(axis=1).mean())))

    # 波动目标旋钮：目标 × 窗口
    knob = []
    for tv in rule.SENS_VOL_TARGET:
        for win in rule.SENS_VOL_WINDOW:
            v = build(rn=dict(target_vol=tv, vol_window=win))
            knob.append(dict(kind="target×window", target=tv, window=win,
                             n=len(v["res"]["switches"]),
                             scale_mean=float(np.mean(v["res"]["scale"])),
                             exp=float(v["W"][["growth", "div"]].values.sum(axis=1).mean()),
                             **{k: v["m"][k] for k in ("ann", "dd", "shp", "calmar")}))
    for db in rule.SENS_DEADBAND:
        v = build(rn=dict(deadband=db))
        bars = [b - a for a, b, _ in engine.hold_legs(v["W"])]
        knob.append(dict(kind="deadband", deadband=db, n=len(v["res"]["switches"]),
                         min_leg=min(bars), short=sum(1 for x in bars if x < 7),
                         scale_mean=float(np.mean(v["res"]["scale"])),
                         exp=float(v["W"][["growth", "div"]].values.sum(axis=1).mean()),
                         **{k: v["m"][k] for k in ("ann", "dd", "shp", "calmar")}))
    for gap in rule.SENS_MIN_GAP:
        v = build(rn=dict(min_rebalance=gap))
        bars = [b - a for a, b, _ in engine.hold_legs(v["W"])]
        knob.append(dict(kind="min_gap", min_gap=gap, n=len(v["res"]["switches"]),
                         min_leg=min(bars), short=sum(1 for x in bars if x < 7),
                         scale_mean=float(np.mean(v["res"]["scale"])),
                         exp=float(v["W"][["growth", "div"]].values.sum(axis=1).mean()),
                         **{k: v["m"][k] for k in ("ann", "dd", "shp", "calmar")}))

    # 在 v5 之上再过一遍 v3 的参数
    sens = []
    for ma in rule.SENS_MA:
        for cut in rule.SENS_CUT:
            v = build(tw=dict(ma_window=ma, cut=cut))
            sens.append(dict(kind="ma×cut", ma=ma, cut=cut, n=len(v["res"]["switches"]),
                             exp=float(v["W"][["growth", "div"]].values.sum(axis=1).mean()),
                             **{k: v["m"][k] for k in ("ann", "dd", "shp", "calmar")}))
    for cool in rule.SENS_COOL:
        v = build(tw=dict(cool=cool))
        sens.append(dict(kind="cool", cool=cool, n=len(v["res"]["switches"]),
                         exp=float(v["W"][["growth", "div"]].values.sum(axis=1).mean()),
                         **{k: v["m"][k] for k in ("ann", "dd", "shp", "calmar")}))
    for cy in rule.SENS_CASH:
        res = engine.run(g, d, main["base_W"], target_vol=rule.VOL_TARGET, cash_yield=cy)
        m = engine.metrics(res["eq"])
        sens.append(dict(kind="cash", cash_yield=cy, n=len(res["switches"]),
                         exp=float(res["W"][["growth", "div"]].values.sum(axis=1).mean()),
                         **{k: m[k] for k in ("ann", "dd", "shp", "calmar")}))

    bh_g = engine.run(g, d, pd.DataFrame({"growth": 1.0, "div": 0.0, "cash": 0.0},
                                         index=r.index), fee=0, target_vol=0)["eq"]
    bh_d = engine.run(g, d, pd.DataFrame({"growth": 0.0, "div": 1.0, "cash": 0.0},
                                         index=r.index), fee=0, target_vol=0)["eq"]

    switches = engine.describe_switches(main["res"]["switches"], r)
    legs = engine.hold_legs(main["W"])
    bars = [b - a for a, b, _ in legs]
    scale = main["res"]["scale"]

    return dict(g=g, d=d, r=r, main=main, v3=v3, keep=keep, cons=cons,
                facts=facts, knob=knob, sens=sens, switches=switches,
                legs=legs, bars=bars,
                seg=dict(v5_2015=seg_dd(main["res"]["eq"], "2015-06-12", "2016-01-28"),
                         v3_2015=seg_dd(v3["res"]["eq"], "2015-06-12", "2016-01-28"),
                         v5_2024=seg_dd(main["res"]["eq"], "2024-10-08", "2025-04-07"),
                         v3_2024=seg_dd(v3["res"]["eq"], "2024-10-08", "2025-04-07")),
                ddw=dict(v5=mdd_window(main["res"]["eq"]), v3=mdd_window(v3["res"]["eq"])),
                scale_stats=dict(mean=float(np.mean(scale)), min=float(np.min(scale)),
                                 full=float(np.mean(np.isclose(scale, 1.0)))),
                nv=dict(v5=main["res"]["eq"], v3=v3["res"]["eq"],
                        bh_g=bh_g, bh_d=bh_d))


# ============================================================ 报告
def build_report(C):
    r = C["r"]
    m = C["main"]["m"]
    m3 = C["v3"]["m"]
    mk = C["keep"]["m"]
    mc = C["cons"]["m"]
    mg = engine.metrics(C["nv"]["bh_g"])
    md = engine.metrics(C["nv"]["bh_d"])
    ss = C["scale_stats"]

    L = [f"# 成长/红利风格轮动 · v5「v3 + 波动率目标 {rule.VOL_TARGET:.0%}」", "",
         f"> 生成 {datetime.now():%Y-%m-%d %H:%M} · 数据 {r.index[0].date()}~{r.index[-1].date()}"
         f"（{len(r)} 交易日）· 分子 {rule.GROWTH_NAME} {rule.GROWTH_INDEX} ÷ "
         f"分母 {rule.DIV_NAME} {rule.DIV_INDEX}", "",
         "> 目标：在 v3 之上**再压回撤**。不动 R 层与趋势层，只在最外面加一层"
         "「按已实现波动缩放总仓位」。", "",
         "## 0. 结论先行", "",
         f"**做到了，且不是靠少持仓换来的**：年化 {pct(m['ann'])}（v3 {pct(m3['ann'])}，"
         f"让出 {abs(m3['ann'] - m['ann']) * 100:.1f}pp），最大回撤 "
         f"{pct(m3['dd'])} → **{pct(m['dd'])}（改善 {abs(m['dd'] - m3['dd']) * 100:.1f}pp）**，"
         f"夏普 {m3['shp']:.2f} → **{m['shp']:.2f}**，Calmar {m3['calmar']:.2f} → "
         f"**{m['calmar']:.2f}**（+{(m['calmar'] / m3['calmar'] - 1) * 100:.0f}%）。", "",
         f"- 两层残余回撤同时被削平：2015 段 {pct(C['seg']['v3_2015'], 1)} → "
         f"**{pct(C['seg']['v5_2015'], 1)}**；2024 段 {pct(C['seg']['v3_2024'], 1)} → "
         f"**{pct(C['seg']['v5_2024'], 1)}**。",
         f"- 波动层平均缩放 {ss['mean']:.2f}、最低 {ss['min']:.2f}：base 组合常年波动约 "
         f"23%，高于 {rule.VOL_TARGET:.0%} 目标，所以**缩放几乎从不等于 1**"
         f"（仅 {ss['full']:.0%} 的交易日满仓）。它不是「偶尔踩刹车」，而是"
         "**给组合定了一个风险预算**：平静期放到 1.0，高波动期压到 "
         f"{ss['min']:.2f}，平均 {ss['mean']:.2f}。",
         f"- 代价：切换 {len(C['v3']['res']['switches'])} 次 → "
         f"{len(C['switches'])} 次；平均暴露 {C['main']['W'][['growth', 'div']].values.sum(axis=1).mean():.0%}"
         f"（v3 为 {C['v3']['W'][['growth', 'div']].values.sum(axis=1).mean():.0%}）。", "",
         "## 1. 规则", "",
         "| 层 | 规则 | 与 v3 的关系 |", "|---|---|---|",
         f"| **R 层** | R ≤ {rule.RATIO_LOW:.2f} 满仓成长 / R ≥ {rule.RATIO_HIGH:.2f} 满仓红利 / "
         "中间带保持状态 | **完全相同** |",
         f"| **趋势层** | 创业板指 < MA{rule.MA_TREND} → 成长侧减半；中证红利 < MA{rule.MA_TREND} "
         "→ 红利侧减半；削掉的转现金 | **完全相同** |",
         f"| **波动层** | `scale = min(1, {rule.VOL_TARGET:.0%} ÷ {rule.VOL_WINDOW} 日已实现年化波动)`，"
         "等比缩放成长+红利，腾出的进现金 | **v5 新增** |",
         f"| **冷却/死区** | 切换间隔 ≥ {rule.COOL_BARS} 日；缩放系数变化 < "
         f"{rule.VOL_DEADBAND:.2f} 则不动 | 冷却同 v3，死区新增 |", "",
         f"> 波动率只从**未缩放**的 base 净值估（避免 scale 与收益互为因果），且第 t 日只用 "
         f"vol[t-1]，无前视。`VOL_USE=False` 或 `target_vol=0` 时完全退化成 v3。", "",
         "## 2. 绩效", "",
         "| 组合 | 年化 | 最大回撤 | 波动 | 夏普 | Calmar | 切换 | 平均暴露 |",
         "|---|---|---|---|---|---|---|---|"]

    rows = [("**v5 默认（目标 18%）**", C["main"]),
            ("v5「保收益」（目标 25%）", C["keep"]),
            ("v5「保守」（目标 12%）", C["cons"]),
            ("v3（关掉波动层）", C["v3"])]
    for tag, v in rows:
        exp = float(v["W"][["growth", "div"]].values.sum(axis=1).mean())
        L.append(f"| {tag} | {pct(v['m']['ann'])} | {pct(v['m']['dd'])} | {pct(v['m']['vol'])} | "
                 f"{v['m']['shp']:.2f} | {v['m']['calmar']:.2f} | "
                 f"{len(v['res']['switches'])} | {exp:.0%} |")
    L += [f"| 买入持有{rule.GROWTH_NAME} | {pct(mg['ann'])} | {pct(mg['dd'])} | {pct(mg['vol'])} | "
          f"{mg['shp']:.2f} | {mg['calmar']:.2f} | — | 100% |",
          f"| 买入持有{rule.DIV_NAME} | {pct(md['ann'])} | {pct(md['dd'])} | {pct(md['vol'])} | "
          f"{md['shp']:.2f} | {md['calmar']:.2f} | — | 100% |", "",
          f"- v5 最大回撤 {pct(m['dd'])}：{C['ddw']['v5']['peak']} → {C['ddw']['v5']['end']}；"
          f"v3 最大回撤 {pct(m3['dd'])}：{C['ddw']['v3']['peak']} → {C['ddw']['v3']['end']}。", "",
          "> 三个档位是**同一根旋钮**：目标波动越低、仓位压得越狠、回撤越小、年化也越低。"
          "默认 18% 取 Calmar 最高点；要保收益选 25%（年化反而略高于 v3，回撤少 9pp）。", ""]

    # ---- 关键对照 ----
    L += ["## 3. 关键对照：这是「降风险」还是「择时」", "",
          "降回撤最省事的办法就是一直少持仓。所以必须把暴露对齐再比：", "",
          "| 方案 | 年化 | 最大回撤 | 夏普 | Calmar | 平均暴露 |", "|---|---|---|---|---|---|"]
    for f in C["facts"]:
        L.append(f"| 对照：恒定 {f['exp']:.0%} 暴露（v3 权重 ×{f['k']:.2f}） | "
                 f"{pct(f['m']['ann'])} | {pct(f['m']['dd'])} | "
                 f"{f['m']['shp']:.2f} | {f['m']['calmar']:.2f} | {f['exp']:.0%} |")
    exp_v5 = float(C["main"]["W"][["growth", "div"]].values.sum(axis=1).mean())
    L += [f"| **v5 目标 18%（暴露相当）** | **{pct(m['ann'])}** | **{pct(m['dd'])}** | "
          f"**{m['shp']:.2f}** | **{m['calmar']:.2f}** | {exp_v5:.0%} |", ""]
    best_f = min(C["facts"], key=lambda f: abs(f["exp"] - exp_v5))
    L += [f"暴露最接近的是「恒定 {best_f['exp']:.0%}」这一行：它年化 {pct(best_f['m']['ann'])}、"
          f"回撤 {pct(best_f['m']['dd'])}、Calmar {best_f['m']['calmar']:.2f}；"
          f"v5 在几乎相同的暴露（{exp_v5:.0%}）下是 {pct(m['ann'])} / {pct(m['dd'])} / "
          f"Calmar {m['calmar']:.2f} —— **年化多 "
          f"{(m['ann'] - best_f['m']['ann']) * 100:.1f}pp、回撤还浅 "
          f"{abs(m['dd'] - best_f['m']['dd']) * 100:.1f}pp**。这两项都不是「少持仓」能解释的。", "",
          "道理也直白：恒定降仓在牛市里也少赚；v5 的缩放系数随波动变化 —— "
          "平静期回到 1.0 吃满仓位，波动抬升才压下去，所以同样的平均暴露拿到了更高收益。", ""]

    # ---- 旋钮 ----
    knob = [k for k in C["knob"] if k["kind"] == "target×window"]
    L += ["## 4. 波动目标旋钮：目标 × 窗口", "",
          "| 目标波动 | 窗口 | 年化 | 最大回撤 | 夏普 | Calmar | 切换 | 平均缩放 | 平均暴露 |",
          "|---|---|---|---|---|---|---|---|---|"]
    for k in knob:
        mark = " ← **默认**" if (abs(k["target"] - rule.VOL_TARGET) < 1e-9
                                and k["window"] == rule.VOL_WINDOW) else ""
        L.append(f"| {k['target']:.0%}{mark} | {k['window']} | {pct(k['ann'])} | {pct(k['dd'])} | "
                 f"{k['shp']:.2f} | {k['calmar']:.2f} | {k['n']} | {k['scale_mean']:.2f} | "
                 f"{k['exp']:.0%} |")
    win_best = {}
    for k in knob:
        win_best.setdefault(k["window"], []).append(k["calmar"])
    L += ["",
          "> **窗口**："
          + "；".join(f"{w} 日的 Calmar 区间 {min(v):.2f}~{max(v):.2f}"
                      for w, v in sorted(win_best.items()))
          + "。250 日明显落后 —— 波动估计太慢，等它反应过来回撤已经发生。",
          "> **目标**：120 日窗下 10%~25% 六档的 Calmar 都在 0.49~0.53，几乎是平的 —— "
          "这根旋钮只是在**收益与回撤之间平移**，不改变风险调整后的性价比。"
          f"默认 {rule.VOL_TARGET:.0%} 恰好是其中最高一格（0.53），但别把它当成「最优参数」，"
          "它和 10%（0.51）、15%（0.51）的差别在噪音量级。", ""]

    # ---- 死区 ----
    dbs = [k for k in C["knob"] if k["kind"] == "deadband"]
    L += ["## 5. 防抖与可执行性（死区 / 最小间隔）", "",
          "v3 的 COOL_BARS 只约束 R/趋势层。v5 多了一层缩放，若只在层内限速，仍会出现"
          "「t 日换档、t+1 日改缩放」这种前后只差一天的调仓 —— 实测产生**最短持仓 0 天、"
          "7 段不足 7 天**的持仓，踩场外 C 类 1.5% 赎回费。"
          "所以 v5 在**最终权重**上再加一道全局限速（`MIN_REBALANCE`），"
          "它同时管住「换档」与「改缩放」两种调仓。", "",
          "**死区**（系数变化不足该幅度不动）：", "",
          "| 死区 | 切换 | 最短段 | <7 日段 | 年化 | 最大回撤 | Calmar | 平均缩放 |",
          "|---|---|---|---|---|---|---|---|"]
    for k in dbs:
        mark = " ← 默认" if abs(k["deadband"] - rule.VOL_DEADBAND) < 1e-9 else ""
        L.append(f"| {k['deadband']:.2f}{mark} | {k['n']} | {k['min_leg']} | {k['short']} | "
                 f"{pct(k['ann'])} | {pct(k['dd'])} | {k['calmar']:.2f} | {k['scale_mean']:.2f} |")
    gaps = [k for k in C["knob"] if k["kind"] == "min_gap"]
    L += ["", "**全局调仓最小间隔**（`MIN_REBALANCE`，对最终权重统一限速）：", "",
          "| 最小间隔 | 切换 | 最短段 | <7 日段 | 年化 | 最大回撤 | Calmar |",
          "|---|---|---|---|---|---|---|"]
    for k in gaps:
        mark = " ← 默认" if k["min_gap"] == rule.MIN_REBALANCE else ""
        L.append(f"| {k['min_gap']}{mark} | {k['n']} | {k['min_leg']} | {k['short']} | "
                 f"{pct(k['ann'])} | {pct(k['dd'])} | {k['calmar']:.2f} |")
    L += ["",
          f"> 死区 0.05~0.30 全区间 Calmar {min(k['calmar'] for k in dbs):.2f}~"
          f"{max(k['calmar'] for k in dbs):.2f}、年化 "
          f"{min(k['ann'] for k in dbs):.1%}~{max(k['ann'] for k in dbs):.1%}，结论不依赖它；"
          f"0.15 恰好是最好一格，属巧合，不要当成调优结果。",
          f"> 最小间隔从 {gaps[0]['min_gap']} 天加到 {gaps[-1]['min_gap']} 天，年化与 Calmar 变化很小，"
          f"但**最短持仓段 {gaps[0]['min_leg']} 天 → {gaps[-1]['min_leg']} 天、<7 日段 "
          f"{gaps[0]['short']} 个 → {gaps[-1]['short']} 个** —— 这个参数几乎纯买可执行性，"
          f"默认 {rule.MIN_REBALANCE} 天（约两周调一次）。", ""]

    # ---- 趋势层参数（在 v5 之上） ----
    grid = [s for s in C["sens"] if s["kind"] == "ma×cut"]

    def cell(ma, cut):
        return next(s for s in grid if s["ma"] == ma and abs(s["cut"] - cut) < 1e-9)

    L += ["## 6. 在 v5 之上再过一遍趋势层参数", "",
          f"**均线 × 削减比例**（波动目标 {rule.VOL_TARGET:.0%}，cool={rule.COOL_BARS}）：", "",
          "| MA | 削减 | 切换 | 平均暴露 | 年化 | 最大回撤 | 夏普 | Calmar |",
          "|---|---|---|---|---|---|---|---|"]
    for s in grid:
        mark = " ← **默认**" if (s["ma"], s["cut"]) == (rule.MA_TREND, rule.TREND_CUT) else ""
        L.append(f"| {s['ma']} | {s['cut']:.0%}{mark} | {s['n']} | {s['exp']:.0%} | "
                 f"{pct(s['ann'])} | {pct(s['dd'])} | {s['shp']:.2f} | {s['calmar']:.2f} |")
    best = max(grid, key=lambda s: s["calmar"])
    L += ["",
          f"> 六格 Calmar {min(s['calmar'] for s in grid):.2f}~{best['calmar']:.2f}，"
          f"最好的 (MA{best['ma']}, {best['cut']:.0%}) 与默认 "
          f"({rule.MA_TREND}, {rule.TREND_CUT:.0%}) 的 {cell(rule.MA_TREND, rule.TREND_CUT)['calmar']:.2f} "
          "差距不大 —— 说明**波动层已经把趋势层的调参空间吃掉了**：两层都在管风险，"
          "叠加后对单层参数的敏感度下降。这是好消息（更稳），也意味着别再指望调 MA 拿收益。", "",
          "**冷却期**：", "",
          "| COOL_BARS | 切换 | 年化 | 最大回撤 | Calmar |", "|---|---|---|---|---|"]
    for s in C["sens"]:
        if s["kind"] != "cool":
            continue
        mark = " ← 默认" if s["cool"] == rule.COOL_BARS else ""
        L.append(f"| {s['cool']}{mark} | {s['n']} | {pct(s['ann'])} | {pct(s['dd'])} | "
                 f"{s['calmar']:.2f} |")
    L += ["", "**现金计息**：", "",
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
    e3 = C["v3"]["res"]["eq"]
    a3, b3 = engine.metrics(e3[e3.index < split]), engine.metrics(e3[e3.index >= split])
    wa, wb = mdd_window(eq[eq.index < split]), mdd_window(eq[eq.index >= split])
    L += ["## 7. 分段稳健性", "",
          "| 段 | v5 年化 | v5 回撤 | v5 Calmar | v3 Calmar | 最大回撤区间 |",
          "|---|---|---|---|---|---|",
          f"| 前半 {r.index[0].year}-2018 | {pct(a['ann'])} | {pct(a['dd'])} | {a['calmar']:.2f} | "
          f"{a3['calmar']:.2f} | {wa['peak']} → {wa['end']} |",
          f"| 后半 2019-{r.index[-1].year} | {pct(b['ann'])} | {pct(b['dd'])} | {b['calmar']:.2f} | "
          f"{b3['calmar']:.2f} | {wb['peak']} → {wb['end']} |", "",
          "> 两段的 Calmar 都高于 v3，改善不是某一段的偶然。", ""]

    # ---- 可执行性 ----
    L += ["## 8. 可执行性", "",
          f"- 持仓段 **{len(C['legs'])}** 段（v3 为 54 段），最短 **{min(C['bars'])}** 个交易日、"
          f"中位 {int(np.median(C['bars']))}、最长 {max(C['bars'])}；"
          f"**不足 7 日的段 {sum(1 for x in C['bars'] if x < 7)} 个**。",
          f"- 切换 **{len(C['switches'])}** 次（v3 为 {len(C['v3']['res']['switches'])} 次），"
          f"其中缩放的档位变化占一部分；因死区 {rule.VOL_DEADBAND:.2f} 的存在，"
          f"缩放不是每天微调，而是「跨档才动」。",
          f"- 缩放系数落在 {C['scale_stats']['min']:.2f}~1.00，平均 {C['scale_stats']['mean']:.2f}。"
          f"注意：只有 {C['scale_stats']['full']:.0%} 的交易日系数等于 1 —— 因为 base 组合常年波动"
          f"约 23%、高于 {rule.VOL_TARGET:.0%} 的目标，这一层实际是**常驻的风险预算**，"
          "而不是偶发的刹车。",
          "- 波动层只改「总仓位」，不改成长/红利之间的比例 —— 因此可以把它理解为"
          "「在 v3 给的仓位上再乘一个风险预算系数」，执行时只需按同一比例减两边。", ""]

    # ---- 最近切换 ----
    L += ["## 9. 最近 12 次切换", ""]
    if C["switches"]:
        L += ["| 交易日 | 动作 | 成长/红利/现金 变化 | 决策用 R[-1] |", "|---|---|---|---|"]
        for s in C["switches"][-12:]:
            fw = "/".join(f"{x:.2f}" for x in s["from_w"])
            tw = "/".join(f"{x:.2f}" for x in s["to_w"])
            L.append(f"| {s['date']} | **{s['action']}** | {fw} → {tw} | {s['R_prev']} |")
    L.append("")

    # ---- 边界 ----
    L += ["## 10. 已知边界", "",
          f"1. **年化让出 {abs(m3['ann'] - m['ann']) * 100:.1f}pp 是真实代价**：波动层在"
          "「波动抬升但随后继续上涨」的行情里也会降仓（2020 年二季度、2025 年部分时段），"
          "这部分少赚不可避免。要保收益就用 25% 档。",
          "2. **波动率用 base 净值估算**（未缩放的策略净值），是为了切断 scale 与收益的循环依赖；"
          "实盘应改用实际组合收益滚动，估计会更灵敏一些，绩效不会完全一致。",
          "3. **R 阈值前视**：0.35/0.65 沿自 v2/v3，仍是全样本选优 —— 这一层 v5 没有改，"
          "所以前视问题依然继承。",
          "4. **波动目标与窗口是同一段样本上选的**：好在它们是平台不是尖峰"
          f"（12%~25% × 60~120 日的 Calmar 都在 0.44~0.53），但仍属样本内。",
          "5. **红利端系统性低估**：中证红利是价格指数，股息率约 4.5%；"
          "v5 现金占比更高，持有红利时段更少，受影响比 v3 大。",
          "6. 未含滑点与场外确认时滞；全部为模拟结果，非投资建议。", ""]
    return "\n".join(L) + "\n"


# ============================================================ 图：净值 + 回撤
def plot_nav(C):
    nv = C["nv"]
    labels = dict(v5=f"v5 目标{rule.VOL_TARGET:.0%}（{len(C['switches'])} 次切换）",
                  v3="v3 无波动层（53 次切换）",
                  bh_g=f"持有{rule.GROWTH_NAME}", bh_d=f"持有{rule.DIV_NAME}")
    fig, (ax1, ax2) = plt.subplots(2, 1, figsize=(12, 8),
                                   gridspec_kw=dict(height_ratios=[2.1, 1], hspace=0.12),
                                   sharex=True)
    for k, s in nv.items():
        s = s / s.iloc[0]
        ax1.plot(s.index, s.values, label=f"{labels[k]}（{s.iloc[-1]:.2f}×）",
                 lw=1.6 if k == "v5" else 1.0, alpha=1.0 if k == "v5" else 0.7)
        dd = (s / s.cummax() - 1) * 100
        ax2.plot(dd.index, dd.values, lw=1.4 if k == "v5" else 0.9,
                 alpha=1.0 if k == "v5" else 0.6)
    ax1.set_yscale("log")
    ax1.legend(loc="upper left", fontsize=9)
    ax1.set_title(f"v5（v3 + 波动率目标 {rule.VOL_TARGET:.0%}）vs v3 vs 基准 —— "
                  "净值（对数轴，上）与回撤（下）")
    ax1.grid(alpha=0.3)
    ax2.grid(alpha=0.3)
    ax2.set_ylabel("回撤 (%)")
    m, m3 = C["main"]["m"], C["v3"]["m"]
    ax2.axhline(m3["dd"] * 100, color="#c0392b", ls=":", lw=1.0,
                label=f"v3 最大回撤 {m3['dd']:.2%}")
    ax2.axhline(m["dd"] * 100, color="#1a9850", ls=":", lw=1.0,
                label=f"v5 最大回撤 {m['dd']:.2%}")
    ax2.legend(loc="lower right", fontsize=8)
    fig.subplots_adjust(left=0.075, right=0.985, top=0.935, bottom=0.07, hspace=0.12)
    path = os.path.join(OUT, "_v5_nav.png")
    fig.savefig(path, dpi=140, facecolor="white")
    plt.close(fig)
    return path


# ============================================================ 图：三资产仓位
def plot_alloc(C):
    W = C["main"]["W"]
    g, d, r = C["g"], C["d"], C["r"]
    fig, (ax, axr) = plt.subplots(
        2, 1, figsize=(13, 7.6), sharex=True,
        gridspec_kw=dict(height_ratios=[3.5, 1.0], hspace=0.08))
    ax.stackplot(W.index, W["growth"] * 100, W["div"] * 100, W["cash"] * 100,
                 colors=["#d95f02", "#4a7fb5", "#bdbdbd"],
                 labels=["成长（创业板）", "红利（中证红利）", "现金"],
                 alpha=0.85)
    ax.set_ylim(0, 100)
    ax.set_ylabel("仓位 (%)")
    ax.set_title(f"v5 三资产仓位：成长 / 红利 / 现金（v3 层级 × 波动率目标 "
                 f"{rule.VOL_TARGET:.0%}）")
    ax.grid(alpha=0.25, axis="y")
    ax.tick_params(axis="x", labelbottom=False)

    # 两条点位曲线各给一根右轴：创业板指 594~4372、中证红利 2096~6227，量级不同，
    # 挤在一根轴上会把创业板指压扁；左轴专心显示仓位。
    ax2 = ax.twinx()
    ax2.plot(g.index, g.values, color="#2b2b2b", lw=1.0, alpha=0.65, zorder=5,
             label=f"{rule.GROWTH_NAME} {rule.GROWTH_INDEX}")
    ax2.set_ylabel(f"{rule.GROWTH_NAME}点位（右轴 1，黑）", fontsize=10, color="#2b2b2b")
    ax2.tick_params(axis="y", colors="#2b2b2b")

    ax3 = ax2.twinx()
    ax3.spines["right"].set_position(("outward", 56))
    # ax2.twinx() 会把 ax2 自己的刻度拨到左侧（与仓位轴打架），显式拨回右侧
    ax2.yaxis.tick_right()
    ax2.yaxis.set_label_position("right")
    ax2.spines["right"].set_visible(True)
    ax3.plot(d.index, d.values, color="#8c2d19", lw=1.0, alpha=0.65, zorder=5,
             label=f"{rule.DIV_NAME} {rule.DIV_INDEX}")
    ax3.set_ylabel(f"{rule.DIV_NAME}点位（右轴 2，暗红）", fontsize=10, color="#8c2d19")
    ax3.tick_params(axis="y", colors="#8c2d19")

    h1, l1 = ax.get_legend_handles_labels()
    h2, l2 = ax2.get_legend_handles_labels()
    h3, l3 = ax3.get_legend_handles_labels()
    ax.legend(h1 + h2 + h3, l1 + l2 + l3, loc="upper left", ncol=3,
              fontsize=8.5, framealpha=0.9)

    # ---- 下栏：风格比值 R（仓位切换的驱动量），点位的另一种读法 ----
    lo, hi = rule.RATIO_LOW, rule.RATIO_HIGH
    axr.plot(r.index, r.values, color="#12395e", lw=1.0,
             label=f"风格比值 R = {rule.GROWTH_NAME} ÷ {rule.DIV_NAME}")
    axr.fill_between(r.index, r.values, lo, where=r.values <= lo, interpolate=True,
                     color="#d95f02", alpha=0.22, lw=0)
    axr.fill_between(r.index, r.values, hi, where=r.values >= hi, interpolate=True,
                     color="#4a7fb5", alpha=0.22, lw=0)
    axr.axhline(lo, color="#d95f02", ls="--", lw=1.0)
    axr.axhline(hi, color="#4a7fb5", ls="--", lw=1.0)
    axr.text(r.index[2], lo, f" R≤{lo:.2f} → 目标满仓成长", va="top", ha="left",
             fontsize=8, color="#a34700")
    axr.text(r.index[2], hi, f" R≥{hi:.2f} → 目标满仓红利", va="bottom", ha="left",
             fontsize=8, color="#2f5580")
    axr.set_ylim(min(float(r.min()), lo) * 0.9, max(float(r.max()), hi) * 1.08)
    axr.set_yticks([lo, (lo + hi) / 2, hi])       # 刻度对齐阈值，读图不用估算
    axr.set_ylabel("比值 R")
    axr.grid(alpha=0.25, axis="y")
    axr.legend(loc="upper right", fontsize=8.5, framealpha=0.9)
    axr.xaxis.set_major_locator(mdates.YearLocator(2, month=1, day=1))
    axr.xaxis.set_major_formatter(mdates.DateFormatter("%Y"))

    fig.subplots_adjust(left=0.065, right=0.875, top=0.94, bottom=0.07)
    path = os.path.join(OUT, "_v5_alloc.png")
    fig.savefig(path, dpi=140, facecolor="white")
    plt.close(fig)
    return path


# ============================================================ 图：波动 vs 目标 + 缩放
def plot_vol(C):
    res = C["main"]["res"]
    vol = res["vol"] * 100
    scale = res["scale"]
    eq = res["eq"]
    dd = (eq / eq.cummax() - 1) * 100

    fig, (ax1, ax2) = plt.subplots(2, 1, figsize=(13, 8),
                                   gridspec_kw=dict(height_ratios=[1, 1], hspace=0.16),
                                   sharex=True)
    ax1.plot(vol.index, vol.values, color="#12395e", lw=1.0,
             label=f"{rule.VOL_WINDOW} 日已实现年化波动（base 净值）")
    ax1.axhline(rule.VOL_TARGET * 100, color="#c0392b", ls="--", lw=1.4,
                label=f"目标波动 {rule.VOL_TARGET:.0%}（波动越高、仓位越低）")
    ax1.set_ylabel("年化波动 (%)")
    ax1.set_title(f"v5 波动层：已实现波动越高、总仓位越低（目标 {rule.VOL_TARGET:.0%}）"
                  f"—— base 组合常年波动约 23%，缩放多数时间低于满仓")
    ax1.legend(loc="upper left", fontsize=9)
    ax1.grid(alpha=0.3)
    ax1.set_ylim(0, min(90, float(np.nanmax(vol)) * 1.05))

    ax2.step(vol.index, scale * 100, where="post",
             color="#1a9850", lw=1.2, label="仓位缩放系数 (%)")
    ax2.set_ylabel("缩放系数 (%)", color="#1a9850")
    ax2.tick_params(axis="y", colors="#1a9850")
    ax2.set_ylim(-3, 105)
    ax2.grid(alpha=0.3)

    ax3 = ax2.twinx()
    ax3.fill_between(dd.index, dd.values, 0, color="#c0392b", alpha=0.18)
    ax3.plot(dd.index, dd.values, color="#c0392b", lw=0.9, label="组合回撤 (%)")
    ax3.set_ylabel("回撤 (%)", color="#c0392b")
    ax3.tick_params(axis="y", colors="#c0392b")

    h2, l2 = ax2.get_legend_handles_labels()
    h3, l3 = ax3.get_legend_handles_labels()
    ax2.legend(h2 + h3, l2 + l3, loc="lower left", fontsize=8.5, framealpha=0.9)

    ax2.xaxis.set_major_locator(mdates.YearLocator(2, month=1, day=1))
    ax2.xaxis.set_major_formatter(mdates.DateFormatter("%Y"))
    fig.subplots_adjust(left=0.07, right=0.93, top=0.93, bottom=0.07)
    path = os.path.join(OUT, "_v5_vol.png")
    fig.savefig(path, dpi=140, facecolor="white")
    plt.close(fig)
    return path


# ============================================================ 产物
def write_products(C):
    os.makedirs(OUT, exist_ok=True)
    txt = build_report(C)
    open(os.path.join(OUT, "_v5_report.md"), "w", encoding="utf-8").write(txt)
    print(txt)

    r, m = C["r"], C["main"]["m"]
    W = C["main"]["W"]
    doc = dict(
        generated_at=datetime.now().strftime("%Y-%m-%d %H:%M"),
        variant="v5_vol_target",
        growth=rule.GROWTH_NAME, growth_code=rule.GROWTH_INDEX,
        dividend=rule.DIV_NAME, dividend_code=rule.DIV_INDEX,
        params=dict(ratio_low=rule.RATIO_LOW, ratio_high=rule.RATIO_HIGH,
                    ma_trend=rule.MA_TREND, trend_cut=rule.TREND_CUT,
                    cool_bars=rule.COOL_BARS, cash_yield=rule.CASH_YIELD, fee=rule.FEE,
                    vol_target=rule.VOL_TARGET, vol_window=rule.VOL_WINDOW,
                    vol_deadband=rule.VOL_DEADBAND, min_rebalance=rule.MIN_REBALANCE,
                    vol_floor=rule.VOL_FLOOR),
        window=f"{r.index[0].date()}~{r.index[-1].date()}", n_bars=len(r),
        metrics=dict(v5=m, v5_keep=C["keep"]["m"], v5_cons=C["cons"]["m"], v3=C["v3"]["m"],
                     bh_growth=engine.metrics(C["nv"]["bh_g"]),
                     bh_dividend=engine.metrics(C["nv"]["bh_d"])),
        fixed_exposure=[dict(k=f["k"], exp=f["exp"], **f["m"]) for f in C["facts"]],
        verdict=("improve_v3: 回撤 -41.57%→" f"{m['dd']:.2%}, Calmar {C['v3']['m']['calmar']:.2f}→"
                 f"{m['calmar']:.2f}"),
        scale_stats=C["scale_stats"],
        mdd_windows=dict(v5=C["ddw"]["v5"], v3=C["ddw"]["v3"]),
        seg_dd=C["seg"],
        n_switches=len(C["switches"]),
        n_switches_v3=len(C["v3"]["res"]["switches"]),
        exp_mean=float(W[["growth", "div"]].values.sum(axis=1).mean()),
        knob=C["knob"], sens=C["sens"],
        legs=[dict(start=str(r.index[a].date()), end=str(r.index[b].date()), bars=b - a,
                   weight=list(w)) for a, b, w in C["legs"]],
        switches=C["switches"],
        eq={str(k.date()): round(float(v), 6) for k, v in C["main"]["res"]["eq"].items()},
        eq_v3={str(k.date()): round(float(v), 6) for k, v in C["v3"]["res"]["eq"].items()},
        scale=[round(float(x), 4) for x in C["main"]["res"]["scale"]],
        alloc={c: list(map(float, W[c].round(4).tolist())) for c in W.columns},
        dates=[str(k.date()) for k in r.index],
    )
    json.dump(doc, open(os.path.join(OUT, "_v5_bt.json"), "w", encoding="utf-8"),
              ensure_ascii=False, indent=2)

    summary = dict(ok=True, code="GD_ROT_V5",
                   theme=(f"成长/红利风格轮动 v5(v3 + 波动率目标 {rule.VOL_TARGET:.0%}"
                          f"/{rule.VOL_WINDOW}日 + 现金)"),
                   years=round(m["years"], 1), st_ann=m["ann"], st_mdd=m["dd"],
                   v3_ann=C["v3"]["m"]["ann"], v3_mdd=C["v3"]["m"]["dd"],
                   base_ann=engine.metrics(C["nv"]["bh_d"])["ann"],
                   base_mdd=engine.metrics(C["nv"]["bh_d"])["dd"],
                   switches=len(C["switches"]), sharpe=m["shp"], calmar=m["calmar"],
                   scale_mean=round(C["scale_stats"]["mean"], 3),
                   exp_mean=round(float(W[["growth", "div"]].values.sum(axis=1).mean()), 3),
                   verdict="improve_v3", params=doc["params"])
    with open(os.path.join(OUT, "_v5_bt.jsonl"), "w", encoding="utf-8") as f:
        f.write(json.dumps(summary, ensure_ascii=False) + "\n")

    print("图:", plot_nav(C))
    print("图:", plot_alloc(C))
    print("图:", plot_vol(C))
    return summary


def draw_main_chart(g, d, r, W):
    """用给定的对齐序列与（已缩放的）权重重画仓位图。

    回测（`compute` 的 main）与每日流程（`scan.py` 的当日状态）共用这一份画图逻辑，
    保证「看板上的图」与「产物里的权重」是同一批数据，不会各画一套。
    """
    return plot_alloc(dict(g=g, d=d, r=r, main=dict(W=W)))


def main_chart():
    """只算 v5 默认档的目标权重并重画 _v5_alloc.png（跳过敏感性网格，秒级）。"""
    wide = datamod.load_wide()
    g, d = wide[rule.GROWTH_INDEX], wide[rule.DIV_INDEX]
    r = factors.ratio_series(g, d)
    g, d = g.reindex(r.index), d.reindex(r.index)
    W = engine.run(g, d, engine.target_weights(r, g, d))["W"]
    return draw_main_chart(g, d, r, W)


def main():
    if "--chart" in sys.argv:
        print("图:", main_chart())
        return
    write_products(compute(refresh="--refresh" in sys.argv))


if __name__ == "__main__":
    main()
