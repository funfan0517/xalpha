# -*- coding: utf-8 -*-
"""成长/红利风格轮动 v2 · 绝对水位带 0.30/0.70（复现用户两张图）

信号: R = 创业板指(399006) ÷ 中证红利(000922)
      R ≤ 0.30 → 加仓（满仓创业板）；R ≥ 0.70 → 减仓（清空创业板，全仓红利）
      中间带保持原仓不动。用 R[-1] 决策，次一交易日开始按新权重计收益（无前视）。
基准: 买入持有创业板 / 买入持有中证红利 / 50-50 恒定
对照: ①阈值敏感性 4 组 ②滚动 250 日分位版（无前视，剔除"用全样本极值当阈值"的优势）
产物: backtest/_v2_report.md · _v2_bt.json · _v2_bt.jsonl（单行摘要）
      backtest/_v2_nav.png        净值对比
      backtest/_v2_band.png       复现图 1：比值带状示意图（红/绿带 + 0.70/0.30 阈值）
      backtest/_v2_cycle.png      复现图 2：比值统计规律与周期轮动（2010-2026）
用法: python strategies/growth_div_rotation/v2_absolute_bands/backtest.py [--refresh]
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
import matplotlib.patheffects as pe  # noqa: E402
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


# ============================================================ 计算
def compute(refresh=False):
    if refresh:
        datamod.refresh()
    wide = datamod.load_wide()
    g = wide[rule.GROWTH_INDEX]
    d = wide[rule.DIV_INDEX]
    r = factors.ratio_series(g, d)
    g, d = g.reindex(r.index), d.reindex(r.index)

    w = engine.signal_weights(r)
    res = engine.run(g, d, w)
    switches = engine.describe_switches(res["switches"], r)

    # 基准（同一引擎、权重恒定 = 买入持有，无费用）
    bh_g = engine.run(g, d, pd.Series(1.0, index=r.index), fee=0.0)["eq"]
    bh_d = engine.run(g, d, pd.Series(0.0, index=r.index), fee=0.0)["eq"]
    ff = engine.run(g, d, pd.Series(0.5, index=r.index), fee=0.0)["eq"]

    # 敏感性
    sens = []
    for lo, hi in rule.SENS_GRID:
        w2 = engine.signal_weights(r, lo, hi)
        r2 = engine.run(g, d, w2)
        m2 = engine.metrics(r2["eq"])
        sens.append(dict(low=lo, high=hi, n_switches=len(r2["switches"]),
                         **{k: m2[k] for k in ("ann", "dd", "shp", "calmar", "ret")},
                         mean_w=float(r2["w"].mean())))

    # 无前视对照：滚动 250 日分位 5% / 98%
    rank = factors.rolling_rank(r)
    w3 = engine.signal_weights(r, rank=rank)
    res3 = engine.run(g, d, w3)

    # 起始态对照：绝对阈值规则对「开机那天手里是什么」极其敏感
    init_alt = {}
    for side in ("div", "growth"):
        wi = engine.signal_weights(r, init_side=side)
        ri = engine.run(g, d, wi)
        mi = engine.metrics(ri["eq"])
        init_alt[side] = dict(n_switches=len(ri["switches"]), mean_w=float(wi.mean()),
                              ann=mi["ann"], dd=mi["dd"], shp=mi["shp"])

    return dict(
        g=g, d=d, r=r, w=w, res=res, switches=switches,
        nv=dict(strategy=res["eq"], bh_g=bh_g, bh_d=bh_d, ff=ff),
        sens=sens, rank_res=res3, rank_w=w3, init_alt=init_alt,
        cycles=factors.up_cycles(r), marks=factors.extreme_marks(r),
    )


# ============================================================ 报告
def build_report(C):
    r, res = C["r"], C["res"]
    m = engine.metrics(res["eq"])
    mg = engine.metrics(C["nv"]["bh_g"])
    md = engine.metrics(C["nv"]["bh_d"])
    mf = engine.metrics(C["nv"]["ff"])
    mr = engine.metrics(C["rank_res"]["eq"])
    lo, hi = rule.RATIO_LOW, rule.RATIO_HIGH
    q_lo = float((r <= lo).mean())
    q_hi = float((r >= hi).mean())

    L = [f"# 成长/红利风格轮动 · v2「绝对水位带 {lo:.2f} / {hi:.2f}」", "",
         f"> 生成 {datetime.now():%Y-%m-%d %H:%M} · 数据 {r.index[0].date()}~{r.index[-1].date()}"
         f"（{len(r)} 交易日）· 分子 {rule.GROWTH_NAME} {rule.GROWTH_INDEX} ÷ "
         f"分母 {rule.DIV_NAME} {rule.DIV_INDEX}", "",
         "> 复现来源：用户提供的两张图（比值带示意图 + 2010-2026 周期轮动图）",
         f"> 规则：`R ≤ {lo:.2f}` 加仓创业板、`R ≥ {hi:.2f}` 减仓换红利、中间带不动；"
         "一次性整仓切换、纯净值口径（与 v0 直接可比，无定投、无分批）。",
         f"> **2026-10-30 阈值由原图口径 {rule.RATIO_LOW_ORIG:.2f}/{rule.RATIO_HIGH_ORIG:.2f} "
         f"改为 {lo:.2f}/{hi:.2f}**（依据 `research/_opt_probe.py` 的阈值曲面："
         "高估线 0.62~0.68 是 Calmar 平台，0.70 掉在平台之外）。原口径在本报告第 4 节作为对照保留。", "",
         "## 1. 规则落地", "",
         "| 项 | 取值 | 来源 |", "|---|---|---|",
         f"| 风格比值 | R = {rule.GROWTH_NAME} ÷ {rule.DIV_NAME} | 图中标题「比值（创业板指数/中证红利指数）」 |",
         f"| 低估加仓线 | `R ≤ {lo:.2f}` → 成长权重 {rule.W_AT_LOW:.1f} | 用户设定（原图 0.30） |",
         f"| 高估减仓线 | `R ≥ {hi:.2f}` → 成长权重 {rule.W_AT_HIGH:.1f} | 用户设定（原图 0.70） |",
         f"| 中间带 | `{lo:.2f} < R < {hi:.2f}` → 保持原仓 | 图中两条虚线之间无标注 |",
         f"| 执行 | 用 `R[-1]` 决策，次一交易日起按新权重计收益 | 无前视 |",
         f"| 成本 | 单边 {rule.FEE:.2%} | 与 v0/v1 同口径 |", "",
         f"**阈值在样本内的实际分位**：`R ≤ {lo:.2f}` 占全期 **{q_lo:.1%}** 的交易日"
         f"（{int((r <= lo).sum())} 天）、`R ≥ {hi:.2f}` 占 **{q_hi:.1%}**（{int((r >= hi).sum())} 天），"
         f"实测区间 {r.min():.4f}~{r.max():.4f}。"
         f"对照原口径 {rule.RATIO_LOW_ORIG:.2f}/{rule.RATIO_HIGH_ORIG:.2f} 的 5.6% / 1.4% —— "
         "挪线把触发频率放大了 4~5 倍，这是切换次数与绩效变化的直接来源。", "",
         "## 2. 绩效（全期，一次性本金 1.0）", "",
         "| 组合 | 年化 | 最大回撤 | 波动 | 夏普 | Calmar | 总收益 |",
         "|---|---|---|---|---|---|---|"]
    for nm, mm in [(f"**v2 绝对水位 {lo:.2f}/{hi:.2f}**", m), (f"买入持有{rule.GROWTH_NAME}", mg),
                   (f"买入持有{rule.DIV_NAME}", md), ("50-50 恒定", mf)]:
        L.append(f"| {nm} | {pct(mm['ann'])} | {pct(mm['dd'])} | {pct(mm['vol'])} | "
                 f"{mm['shp']:.2f} | {mm['calmar']:.2f} | {pct(mm['ret'])} |")
    L += ["", f"- 全期切换 **{len(C['switches'])}** 次，成长暴露均值 **{C['w'].mean():.1%}**。",
          f"- 相对买入持有红利超额 **{pct(m['ann'] - md['ann'])}/年**，"
          f"相对买入持有创业板 **{pct(m['ann'] - mg['ann'])}/年**。", ""]

    L += ["## 3. 切换日志（全部）", ""]
    if C["switches"]:
        L += ["| 交易日 | 动作 | 成长权重 | 决策用 R[-1] |", "|---|---|---|---|"]
        for s in C["switches"]:
            L.append(f"| {s['date']} | **{s['action']}** | {s['from_w']:.2f} → {s['to_w']:.2f} | "
                     f"{s['R_prev']} |")
    else:
        L.append("- 无切换")
    n_sw = len(C["switches"])
    buys = [s["date"] for s in C["switches"] if s["to_w"] > s["from_w"]]
    sells = [s["date"] for s in C["switches"] if s["to_w"] < s["from_w"]]
    if n_sw > 2:
        tail = (f"> 全期共 **{n_sw}** 次动作：买 {' / '.join(buys)}，"
                f"卖 {' / '.join(sells)}。原口径 "
                f"{rule.RATIO_LOW_ORIG:.2f}/{rule.RATIO_HIGH_ORIG:.2f} 只在 "
                "2010-10 与 2021-01 各动一次 —— 挪线把「十年一遇」变成了「四年一轮」，"
                "这是收益与回撤同时改善（也同时改变风险特征）的直接原因。")
    else:
        tail = f"> 全期共 **{n_sw}** 次动作，策略退化成长期单边持有。"
    L += ["", tail, ""]

    L += ["## 4. 阈值敏感性（含原图口径对照）", "",
          "| RATIO_LOW | RATIO_HIGH | 切换次数 | 成长暴露 | 年化 | 最大回撤 | 夏普 | Calmar |",
          "|---|---|---|---|---|---|---|---|"]
    for s in C["sens"]:
        mark = ""
        if (s["low"], s["high"]) == (lo, hi):
            mark = " ← **本变种默认**"
        elif (s["low"], s["high"]) == (rule.RATIO_LOW_ORIG, rule.RATIO_HIGH_ORIG):
            mark = " ← 原图口径（旧默认）"
        L.append(f"| {s['low']:.2f} | {s['high']:.2f}{mark} | {s['n_switches']} | "
                 f"{s['mean_w']:.1%} | {pct(s['ann'])} | {pct(s['dd'])} | "
                 f"{s['shp']:.2f} | {s['calmar']:.2f} |")
    anns = [s["ann"] for s in C["sens"]]
    L += ["", f"> 四组阈值的年化从 {pct(min(anns))} 到 {pct(max(anns))}，"
              f"极差 **{(max(anns) - min(anns)) * 100:.1f}pp**。"
              "阈值稍一挪动结论就变，说明这条规则的表现很大程度取决于「在哪里画线」，而非信号本身有效。",
          "> `research/_opt_probe.py` 的阈值曲面还显示：`RATIO_LOW` 从 0.30 抬到 0.32 时年化"
          "从 10% 跳到 19%（**不连续**）—— 说明部分收益来自「买点恰好落在哪个历史低点」的运气。"
          "本变种取 0.35/0.65 是按高估线平台的逻辑选的，不是曲面上的最优点，但仍属**样本内选优**。", ""]

    ia = C["init_alt"]
    L += [f"**起始态对照**（阈值固定 {lo:.2f}/{hi:.2f}）：", "",
          "| 起始持仓 | 切换次数 | 成长暴露 | 年化 | 最大回撤 | 夏普 |",
          "|---|---|---|---|---|---|",
          f"| 红利/现金（默认，与 v0/v1 一致） | {ia['div']['n_switches']} | "
          f"{ia['div']['mean_w']:.1%} | {pct(ia['div']['ann'])} | {pct(ia['div']['dd'])} | "
          f"{ia['div']['shp']:.2f} |",
          f"| 创业板（乐观起点） | {ia['growth']['n_switches']} | {ia['growth']['mean_w']:.1%} | "
          f"{pct(ia['growth']['ann'])} | {pct(ia['growth']['dd'])} | {ia['growth']['shp']:.2f} |", "",
          (f"> 当前阈值下两个起点年化只差 {abs(ia['div']['ann'] - ia['growth']['ann']) * 100:.2f}pp，"
           "路径依赖不明显（原口径 0.30/0.70 时差 1.3pp）—— 阈值放宽后首次动作出现在第 2 个月，"
           "起点的影响被迅速抹平。"
           if abs(ia["div"]["ann"] - ia["growth"]["ann"]) < 0.02 else
           "> 换个起点就能差出几个点年化 —— 这种「路径依赖」本身就是规则不稳的证据之一。"), ""]

    L += ["## 5. 无前视对照：滚动 250 日分位版", "",
          f"把「绝对水位 {lo:.2f}/{hi:.2f}」换成「过去 250 日的 {rule.RANK_LOW:.0%}/{rule.RANK_HIGH:.0%} 分位」"
          "（同样「低估加仓、高估减仓」，但阈值随行情自适应、不用全样本极值）：", "",
          "| 版本 | 切换次数 | 成长暴露 | 年化 | 最大回撤 | 夏普 | Calmar |",
          "|---|---|---|---|---|---|---|",
          f"| 绝对水位 {lo:.2f}/{hi:.2f}（含前视） | {len(C['switches'])} | "
          f"{C['w'].mean():.1%} | {pct(m['ann'])} | {pct(m['dd'])} | {m['shp']:.2f} | {m['calmar']:.2f} |",
          f"| 滚动分位 {rule.RANK_LOW:.0%}/{rule.RANK_HIGH:.0%}（无前视） | "
          f"{len(C['rank_res']['switches'])} | {C['rank_w'].mean():.1%} | {pct(mr['ann'])} | "
          f"{pct(mr['dd'])} | {mr['shp']:.2f} | {mr['calmar']:.2f} |", "",
          f"> 去掉前视后年化变化 **{pct(mr['ann'] - m['ann'])}**、"
          f"回撤变化 **{pct(mr['dd'] - m['dd'])}**。"
          "这一栏才是「站在当年能拿到的成绩」，第 2 节的数字必须与它一起看。", ""]

    L += ["## 6. 图 2 的上行周期（自动识别，用于画黄带）", "",
          f"| # | 起点（低位片区最低点） | R | 终点（触到 {rule.CYCLE_END_MIN:.2f} 或见顶回撤 "
          f"{rule.CYCLE_FALLBACK_DROP:.0%}） | R | 跨度 |",
          "|---|---|---|---|---|---|",
          ]
    for i, (a, b) in enumerate(C["cycles"], 1):
        L.append(f"| {i} | {a.date()} | {float(r.loc[a]):.3f} | {b.date()} | "
                 f"{float(r.loc[b]):.3f} | {(b - a).days / 365:.1f} 年 |")
    L += ["", "> 黄带是**事后标注**（要知道后面涨到哪才知道这段周期在哪结束），"
              "只能用来讲故事，**不能作为信号**。图中的手工标注与自动识别边界有 ±1 年差异，结构一致（3 段）。",
          "> 周期标注（黄带 / 圈点）用**原图口径**（`rule.CYCLE_*` / `MARK_*_ABS`），"
          f"与交易阈值 {lo:.2f}/{hi:.2f} **解耦** —— 挪交易阈值不会改变图 2 的历史周期标注。", ""]

    L += ["## 7. 已知边界", "",
          "1. **阈值仍是样本内选优**：0.35/0.65 来自阈值曲面（高估线 0.62~0.68 为 Calmar 平台），"
          "不是样本外验证的结果。曲面在 `RATIO_LOW=0.30→0.32` 处年化从 10% 跳到 19%，"
          "说明结论对 0.01 级差异敏感，**不宜把第 2 节的数字当作可复现的未来收益**。",
          "2. **悬崖效应没有消失，只是挪了位置**：买入只认 `R ≤ "
          f"{lo:.2f}`、卖出只认 `R ≥ {hi:.2f}`。R 在 2024-08 最低 0.304 —— 原口径 0.30 "
          "差 0.004 没触发、策略空仓到期末；当前口径则在 2024-01（R=0.348）就买回了。"
          "**同样的「差一点点」换个年份仍可能让策略错过整轮行情**，换成「绝对水位 + 趋势过滤」"
          "（见同级 `../v3_trend_filter/`）才是结构性解法。",
          "3. **图中「加仓/减仓」是模糊措辞**：本变种按一步到位（0→1 / 1→0）落地。"
          "若原意是分档减仓，改 `rule.W_AT_LOW/W_AT_HIGH` 或加阶梯即可，引擎支持连续权重（w∈[0,1]）。",
          "4. **红利端系统性低估**：中证红利是价格指数，股息率约 4.5%，持有红利时段的收益被少算。"
          f"本策略成长暴露 {C['w'].mean():.0%}，非成长那部分的收益被系统性低估。",
          "5. 未含滑点与场外确认时滞；全部为模拟结果，非投资建议。", ""]
    return "\n".join(L) + "\n"


# ============================================================ 图 1：比值带状示意图
def plot_bands(C):
    """复现图 1：深色底 + 红色高估带 / 绿色低估带 + 白色阈值虚线 + 实际 R 曲线。

    色带宽度固定 ±0.10（原图 0.70 线配 0.60~0.80 带、0.30 线配 0~0.40 带），
    阈值挪到 0.35/0.65 后色带随之变为 0.55~0.75 / 0~0.45。
    """
    r = C["r"]
    lo, hi = rule.RATIO_LOW, rule.RATIO_HIGH
    top = 0.85
    fig, ax = plt.subplots(figsize=(11, 7.6))
    fig.patch.set_facecolor("#0b0b0d")
    ax.set_facecolor("#0b0b0d")

    # 色带 = 高估区（R ≥ hi）/ 低估区（R ≤ lo）的实际范围，中间留黑 = 不动区
    ax.axhspan(hi, top, color="#e8322f", alpha=0.55, zorder=1)
    ax.axhspan(0.0, lo, color="#8ef24a", alpha=0.50, zorder=1)
    ax.axhline(hi, color="white", ls=(0, (5, 5)), lw=2.0, alpha=0.95, zorder=3)
    ax.axhline(lo, color="white", ls=(0, (5, 5)), lw=2.0, alpha=0.95, zorder=3)

    # 实际 R 曲线（让示意图带数据）
    ax.plot(r.index, r.values, color="#cfe9ff", lw=1.0, alpha=0.75, zorder=2)

    txt_kw = dict(ha="center", va="center", fontsize=30, color="white",
                  fontweight="bold", zorder=5,
                  path_effects=[pe.withStroke(linewidth=3.2, foreground="#111111")])
    mid = r.index[len(r) // 2]
    ax.text(mid, (hi + top) / 2, "创业板高估（减仓）", **txt_kw)
    ax.text(mid, lo / 2 + 0.01, "创业板低估（加仓）", **txt_kw)

    ax.set_ylim(0, top)
    ax.set_yticks([0, 0.1, 0.2, 0.3, 0.4, 0.5, 0.6, 0.7, 0.8])
    ax.tick_params(colors="#d9d9d9", labelsize=13)
    for s in ("top", "right"):
        ax.spines[s].set_visible(False)
    for s in ("left", "bottom"):
        ax.spines[s].set_color("#8a8a8a")
    ax.xaxis.set_major_locator(mdates.YearLocator(2, month=1, day=1))
    ax.xaxis.set_major_formatter(mdates.DateFormatter("%Y"))
    ax.grid(axis="y", color="#3a3a3a", lw=0.6, alpha=0.6)
    ax.set_title("比值（创业板指数 / 中证红利指数）", color="#e8e8e8", fontsize=13,
                 loc="left", pad=12)
    fig.tight_layout()
    path = os.path.join(OUT, "_v2_band.png")
    fig.savefig(path, dpi=150, facecolor=fig.get_facecolor())
    plt.close(fig)
    return path


# ============================================================ 图 2：周期轮动
def plot_cycles(C):
    """复现图 2：R 曲线 + 0.70/0.30 阈值线 + 历史极值实线 + 上行周期黄带 + 红/绿圈。"""
    r = C["r"]
    lo, hi = rule.RATIO_LOW, rule.RATIO_HIGH
    vmax, vmin = float(r.max()), float(r.min())
    fig, ax = plt.subplots(figsize=(13, 7.2))
    fig.patch.set_facecolor("#fdfdf7")
    ax.set_facecolor("#fdfdf7")

    for a, b in C["cycles"]:                                          # 黄带
        ax.axvspan(a, b, color="#f3d24a", alpha=0.62, lw=0, zorder=0)

    ax.plot(r.index, r.values, color="#12395e", lw=1.1, zorder=3, label="创业板指数 / 中证红利指数（比值）")
    ax.axhline(hi, color="#c0392b", ls=(0, (7, 5)), lw=2.0, zorder=2,
               label=f"高估阈值 {hi:.2f}")
    ax.axhline(lo, color="#2ecc40", ls=(0, (7, 5)), lw=2.0, zorder=2,
               label=f"低估阈值 {lo:.2f}")
    ax.axhline(vmax, color="#8b1a12", lw=1.6, zorder=2,
               label=f"历史最高 {vmax:.3f}")
    ax.axhline(vmin, color="#1d7a2a", lw=1.6, zorder=2,
               label=f"历史最低 {vmin:.3f}")

    for dt, v in C["marks"]["low"]:                                   # 绿圈：低估区极值
        ax.scatter([dt], [v], s=150, facecolors="none", edgecolors="#2ecc40",
                   linewidths=2.2, zorder=6)
    for dt, v in C["marks"]["high"]:                                  # 红圈：高估区极值
        ax.scatter([dt], [v], s=150, facecolors="none", edgecolors="#c0392b",
                   linewidths=2.2, zorder=6)

    ax.text(pd.Timestamp("2016-06-01"), 0.745, "创业板高估区间", ha="center", fontsize=22,
            color="#7a2b22", zorder=6,
            path_effects=[pe.withStroke(linewidth=3.0, foreground="#fdfdf7")])
    ax.text(pd.Timestamp("2016-06-01"), 0.285, "创业板低估区间", ha="center", fontsize=22,
            color="#2e5c1f", zorder=6,
            path_effects=[pe.withStroke(linewidth=3.0, foreground="#fdfdf7")])

    ax.set_ylim(0.22, 0.92)
    ax.set_xlim(pd.Timestamp("2010-01-01"), pd.Timestamp("2027-01-01"))
    ax.xaxis.set_major_locator(mdates.YearLocator(2, month=1, day=1))
    ax.xaxis.set_major_formatter(mdates.DateFormatter("%Y"))
    ax.set_ylabel("比值（创业板指数 / 中证红利指数）", fontsize=11)
    ax.set_xlabel("日期", fontsize=11)
    ax.set_title(f"创业板指数 vs 中证红利指数 比值统计规律与周期轮动"
                 f"（{r.index[0].year}-{r.index[-1].year}）", fontsize=15, pad=12)
    ax.grid(color="#dcdcd0", lw=0.7, alpha=0.9)
    ax.legend(loc="upper left", frameon=True, fontsize=9)
    fig.tight_layout()
    path = os.path.join(OUT, "_v2_cycle.png")
    fig.savefig(path, dpi=150, facecolor=fig.get_facecolor())
    plt.close(fig)
    return path


# ============================================================ 净值图
def plot_nav(C):
    nv = C["nv"]
    fig, ax = plt.subplots(figsize=(12, 5.2))
    labels = dict(strategy="v2 绝对水位 0.30/0.70",
                  bh_g=f"持有{rule.GROWTH_NAME}",
                  bh_d=f"持有{rule.DIV_NAME}", ff="50-50 恒定")
    for k, s in nv.items():
        s = s / s.iloc[0]
        ax.plot(s.index, s.values, label=f"{labels[k]}（{s.iloc[-1]:.2f}×）",
                lw=1.5 if k == "strategy" else 1.0,
                alpha=1.0 if k == "strategy" else 0.65,
                ls="-" if k != "ff" else "--")
    ax.set_yscale("log")
    ax.legend(loc="upper left")
    ax.set_title(f"v2 绝对水位带（{rule.RATIO_LOW:.2f} / {rule.RATIO_HIGH:.2f}）vs 基准 —— 净值（对数轴）")
    ax.grid(alpha=0.3)
    fig.tight_layout()
    path = os.path.join(OUT, "_v2_nav.png")
    fig.savefig(path, dpi=140, facecolor="white")
    plt.close(fig)
    return path


# ============================================================ 双线图：比值 R + 创业板指 + 买卖点
def plot_dual(C):
    """一根线 = 图 2 的风格比值 R（左轴），另一根 = 创业板指点位（右轴），
    并把 v2 规则实际触发的买入/卖出点标在**创业板指曲线**上。"""
    r, g = C["r"], C["g"]
    lo, hi = rule.RATIO_LOW, rule.RATIO_HIGH
    fig, ax1 = plt.subplots(figsize=(13, 7.4))
    ax2 = ax1.twinx()
    fig.patch.set_facecolor("#fdfdf7")
    ax1.set_facecolor("#fdfdf7")

    for a, b in C["cycles"]:                                          # 上行周期黄带（同图 2）
        ax1.axvspan(a, b, color="#f3d24a", alpha=0.42, lw=0, zorder=0)

    # 左轴：风格比值 R
    ax1.plot(r.index, r.values, color="#12395e", lw=1.1, zorder=4,
             label="比值 R = 创业板指 / 中证红利（左轴）")
    ax1.axhline(hi, color="#c0392b", ls=(0, (7, 5)), lw=1.8, zorder=3,
                label=f"高估阈值 {hi:.2f}")
    ax1.axhline(lo, color="#2ecc40", ls=(0, (7, 5)), lw=1.8, zorder=3,
                label=f"低估阈值 {lo:.2f}")
    ax1.set_ylim(0.22, 0.95)

    # 右轴：创业板指点位 + 250 日均线（趋势）
    ax2.plot(g.index, g.values, color="#d95f02", lw=1.0, alpha=0.9, zorder=2,
             label="创业板指 399006 收盘点位（右轴）")
    ma = g.rolling(250).mean()
    ax2.plot(ma.index, ma.values, color="#8c4a00", lw=1.2, ls="--", alpha=0.6, zorder=2,
             label="创业板指 250 日均线（趋势）")
    ax2.set_ylim(200, 4700)

    # 买卖点：标在创业板指曲线上
    for s in C["switches"]:
        dt = pd.Timestamp(s["date"])
        px = float(g.loc[dt])
        buy = s["to_w"] > s["from_w"]
        color = "#1a9850" if buy else "#d73027"
        ax1.axvline(dt, color="#9a9a8e", ls=":", lw=1.0, zorder=1)
        ax2.scatter([dt], [px], marker="^" if buy else "v", s=320, color=color,
                    edgecolors="white", linewidths=1.4, zorder=9)
        txt = (f"{'买入' if buy else '卖出'} {dt:%Y-%m-%d}\n"
               f"指数 {px:,.0f} 点 · R={s['R_prev']:.3f}")
        # 按点在 x 轴上的相对位置选标注方向，避免文本框被裁到画布外
        span = (r.index[-1] - r.index[0]).days
        frac = (dt - r.index[0]).days / span if span else 0.5
        near_right = frac > 0.78
        if buy:
            off, ha, va = ((-30, -50), "right", "top") if near_right \
                else ((62, 26), "left", "bottom")
        else:
            off, ha, va = ((-34, 62), "right", "bottom") if near_right \
                else ((0, 62), "center", "bottom")
        ax2.annotate(txt, xy=(dt, px), xytext=off, textcoords="offset points",
                     ha=ha, va=va, fontsize=10.5, color="white",
                     fontweight="bold", zorder=10,
                     bbox=dict(boxstyle="round,pad=0.45", fc=color, ec="white", lw=1.2),
                     arrowprops=dict(arrowstyle="-|>", color=color, lw=1.4,
                                     shrinkA=2, shrinkB=6))

    ax1.set_xlim(pd.Timestamp("2010-01-01"), pd.Timestamp("2027-01-01"))
    ax1.xaxis.set_major_locator(mdates.YearLocator(2, month=1, day=1))
    ax1.xaxis.set_major_formatter(mdates.DateFormatter("%Y"))
    ax1.set_ylabel("比值（创业板指数 / 中证红利指数）", fontsize=11, color="#12395e")
    ax2.set_ylabel("创业板指点位（收盘）", fontsize=11, color="#a04a02")
    ax1.tick_params(axis="y", colors="#12395e")
    ax2.tick_params(axis="y", colors="#a04a02")
    ax1.set_xlabel("日期", fontsize=11)
    ax1.set_title(f"风格比值 R（左轴）与创业板指趋势（右轴）· "
                  f"{lo:.2f}/{hi:.2f} 规则的买卖点（{r.index[0].year}-{r.index[-1].year}）",
                  fontsize=14, pad=12)
    ax1.grid(color="#dcdcd0", lw=0.7, alpha=0.9)
    ax1.set_ylim(0.22, 1.00)          # 抬高点位轴，给图例留空间
    h1, l1 = ax1.get_legend_handles_labels()
    h2, l2 = ax2.get_legend_handles_labels()
    ax1.legend(h1 + h2, l1 + l2, loc="upper left", frameon=True, fontsize=8.5,
               ncol=2, framealpha=0.92)
    fig.tight_layout()
    path = os.path.join(OUT, "_v2_dual.png")
    fig.savefig(path, dpi=150, facecolor=fig.get_facecolor())
    plt.close(fig)
    return path


# ============================================================ 产物
def write_products(C):
    os.makedirs(OUT, exist_ok=True)
    txt = build_report(C)
    open(os.path.join(OUT, "_v2_report.md"), "w", encoding="utf-8").write(txt)
    print(txt)

    r, res = C["r"], C["res"]
    m = engine.metrics(res["eq"])
    doc = dict(
        generated_at=datetime.now().strftime("%Y-%m-%d %H:%M"),
        variant="v2_absolute_bands",
        growth=rule.GROWTH_NAME, growth_code=rule.GROWTH_INDEX,
        dividend=rule.DIV_NAME, dividend_code=rule.DIV_INDEX,
        params=dict(ratio_low=rule.RATIO_LOW, ratio_high=rule.RATIO_HIGH,
                    w_at_low=rule.W_AT_LOW, w_at_high=rule.W_AT_HIGH,
                    init_side=rule.INIT_SIDE, fee=rule.FEE),
        window=f"{r.index[0].date()}~{r.index[-1].date()}", n_bars=len(r),
        r_stats=dict(min=float(r.min()), max=float(r.max()),
                     q_low=float((r <= rule.RATIO_LOW).mean()),
                     q_high=float((r >= rule.RATIO_HIGH).mean()),
                     median=float(r.median())),
        metrics=dict(
            strategy=m,
            bh_growth=engine.metrics(C["nv"]["bh_g"]),
            bh_dividend=engine.metrics(C["nv"]["bh_d"]),
            fifty_fifty=engine.metrics(C["nv"]["ff"]),
            rank_version=engine.metrics(C["rank_res"]["eq"]),
        ),
        mean_weight_growth=float(C["w"].mean()),
        switches=C["switches"],
        sens=C["sens"],
        cycles=[dict(start=str(a.date()), end=str(b.date()),
                     r_start=round(float(r.loc[a]), 4), r_end=round(float(r.loc[b]), 4))
                for a, b in C["cycles"]],
        marks={k: [(str(d.date()), round(v, 4)) for d, v in vs]
               for k, vs in C["marks"].items()},
        eq={str(d.date()): round(float(v), 6) for d, v in res["eq"].items()},
        w=list(map(float, res["w"].round(4).tolist())),
        dates=[str(d.date()) for d in r.index],
    )
    json.dump(doc, open(os.path.join(OUT, "_v2_bt.json"), "w", encoding="utf-8"),
              ensure_ascii=False, indent=2)

    summary = dict(ok=True, code="GD_ROT_V2",
                   theme=(f"成长/红利风格轮动 v2(绝对水位 "
                          f"{rule.RATIO_LOW:.2f}/{rule.RATIO_HIGH:.2f})"),
                   years=round(m["years"], 1), st_ann=m["ann"], st_mdd=m["dd"],
                   base_ann=engine.metrics(C["nv"]["bh_d"])["ann"],
                   base_mdd=engine.metrics(C["nv"]["bh_d"])["dd"],
                   switches=len(C["switches"]), mean_w=round(float(C["w"].mean()), 3),
                   params=doc["params"])
    with open(os.path.join(OUT, "_v2_bt.jsonl"), "w", encoding="utf-8") as f:
        f.write(json.dumps(summary, ensure_ascii=False) + "\n")

    print("图:", plot_nav(C))
    print("图:", plot_bands(C))
    print("图:", plot_cycles(C))
    print("图:", plot_dual(C))
    return summary


def main():
    C = compute(refresh="--refresh" in sys.argv)
    write_products(C)


if __name__ == "__main__":
    main()
