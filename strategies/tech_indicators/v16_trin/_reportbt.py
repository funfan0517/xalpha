# -*- coding: utf-8 -*-
"""TRIN 策略 · 回测报告（渲染 backtest.py 的产物）。

输入：`backtest/_v16_bt.jsonl`（逐口径逐参数格指标）+ `backtest/_v16_bt_summary.json`（净值/逐年/逐笔）
输出：`backtest/_v16_report.md` + `backtest/_v16_nav.png`

版式沿用仓库惯例：H1 + `>` 口径块 + 主结果 + 单笔统计（`pipeline.bt_stats`）+ 参数敏感性 + 诚实结论。
"""
import json
import os
import sys
from datetime import datetime

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402
import numpy as np  # noqa: E402
import pandas as pd  # noqa: E402

try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:  # noqa
    pass

_DIR = os.path.dirname(os.path.abspath(__file__))
_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(_DIR)))
for _p in (_ROOT, os.path.dirname(_DIR), _DIR):
    if _p not in sys.path:
        sys.path.insert(0, _p)

import rule  # noqa: E402
from pipeline import bt_stats  # noqa: E402

IN_JSONL = os.path.join(_DIR, "backtest", "_v16_bt.jsonl")
IN_SUM = os.path.join(_DIR, "backtest", "_v16_bt_summary.json")
MD = os.path.join(_DIR, "backtest", "_v16_report.md")
PNG = os.path.join(_DIR, "backtest", "_v16_nav.png")

plt.rcParams["font.sans-serif"] = ["Microsoft YaHei", "SimHei", "DejaVu Sans"]
plt.rcParams["axes.unicode_minus"] = False


def pct(x, dp=1):
    return "—" if x is None else f"{x * 100:+.{dp}f}%"


def num(x, dp=2):
    return "—" if x is None else f"{x:.{dp}f}"


def load():
    if not os.path.exists(IN_JSONL) or not os.path.exists(IN_SUM):
        sys.exit("缺回测产物; 先跑 python strategies/tech_indicators/v16_trin/backtest.py")
    rows = [json.loads(ln) for ln in open(IN_JSONL, encoding="utf-8") if ln.strip().startswith("{")]
    summary = json.load(open(IN_SUM, encoding="utf-8"))
    return rows, summary


def main_row(rows, kind):
    for r in rows:
        if r.get("kind") == kind:
            return r
    sys.exit(f"产物里没有 kind={kind} 的行")


def table_main(m, mb, win):
    L = ["| 指标 | 策略（TRIN 择时） | 买入持有 510300 |", "|---|---|---|"]
    rows = [
        ("样本区间", f"{win['start']} ~ {win['end']}", "同左"),
        ("年化收益", pct(m["ann"], 2), pct(mb["ann"], 2)),
        ("最大回撤", pct(m["mdd"], 2), pct(mb["mdd"], 2)),
        ("年化波动", pct(m["vol"], 2).lstrip("+"), pct(mb["vol"], 2).lstrip("+")),
        ("夏普(无风险=0)", num(m["sharpe"]), num(mb["sharpe"])),
        ("持仓时间占比", pct(m["pos_ratio"], 1).replace("+", ""), "100.0%"),
        ("交易笔数", str(m["trades"]), "—"),
        ("单笔胜率", pct(m["t_stats"]["win_rate"], 1).replace("+", ""), "—"),
        ("盈亏比", num(m["t_stats"]["payoff"]), "—"),
        ("利润因子", num(m["t_stats"]["profit_factor"]), "—"),
        ("平均持仓（交易日）", num(m.get("bars_avg"), 1), "—"),
        ("单次建仓比例", pct(rule.SIZE, 0).replace("+", ""), "—"),
        ("择时边际（持仓日-空仓日）", f"{m['timing_edge'] * 1e4:+.1f}bp", "—"),
    ]
    for a, b, c in rows:
        L.append(f"| {a} | {b} | {c} |")
    return L


def plot(summary):
    idx = summary["index"]
    pool = summary["pool"]
    d1 = pd.to_datetime(idx["dates"])
    d2 = pd.to_datetime(pool["dates"])
    fig, axes = plt.subplots(2, 2, figsize=(13, 8), gridspec_kw=dict(height_ratios=[2, 1]))

    ax = axes[0][0]
    ax.plot(d1, idx["nav"], color="#c62828", lw=1.5, label="TRIN 择时")
    ax.plot(d1, idx["base"], color="#90a4ae", lw=1.2, label="510300 买入持有")
    ax.set_title("主口径：510300（次日开盘成交）")
    ax.legend(loc="upper left")
    ax.grid(alpha=0.25)

    ax = axes[1][0]
    for series, color, label in ((idx["nav"], "#c62828", "择时"), (idx["base"], "#90a4ae", "买入持有")):
        s = np.asarray(series, float)
        ax.plot(d1, s / np.maximum.accumulate(s) - 1, color=color, lw=1.1, label=label)
    ax.legend(loc="lower left")
    ax.grid(alpha=0.25)

    ax = axes[0][1]
    ax.plot(d2, pool["nav"], color="#1565c0", lw=1.5, label="TRIN + 错杀选股(3只等权)")
    ax.plot(d2, pool["base"], color="#90a4ae", lw=1.2, label="54 只池等权")
    ax.set_title("池子口径：错杀选股（次日收盘成交）")
    ax.legend(loc="upper left")
    ax.grid(alpha=0.25)

    ax = axes[1][1]
    for series, color, label in ((pool["nav"], "#1565c0", "选股"), (pool["base"], "#90a4ae", "池等权")):
        s = np.asarray(series, float)
        ax.plot(d2, s / np.maximum.accumulate(s) - 1, color=color, lw=1.1, label=label)
    ax.legend(loc="lower left")
    ax.grid(alpha=0.25)

    fig.tight_layout()
    fig.savefig(PNG, dpi=130)
    plt.close(fig)


def sensitivity(rows):
    g = pd.DataFrame([r for r in rows if r.get("kind") == "grid"])
    if g.empty:
        return ["", "（未跑参数网格：`backtest.py --no-grid`）"]
    L = ["", "### 2.1 阈值 × TRIN 字段（其余参数取该组均值）", "",
         "| 字段 | 阈值 | 年化 | 最大回撤 | 夏普 | 持仓占比 | 笔数 | 胜率 | 盈亏比 | 择时边际 |",
         "|---|---|---|---|---|---|---|---|---|---|"]
    for (field, th), part in g.groupby(["field", "th"]):
        L.append(f"| {field} | {th} | {pct(part['ann'].mean(), 2)} | {pct(part['mdd'].mean(), 2)} | "
                 f"{num(part['sharpe'].mean())} | {pct(part['pos_ratio'].mean(), 1).replace('+', '')} | "
                 f"{part['trades'].mean():.1f} | {pct(part['win_rate'].mean(), 1).replace('+', '')} | "
                 f"{num(part['payoff'].mean())} | {part['timing_edge'].mean() * 1e4:+.1f}bp |")

    L += ["", "### 2.2 视频参数（1.4）下 连续天数 × 持有期", "",
          "| 字段 | 连续天数 | 持有期 | 年化 | 最大回撤 | 夏普 | 笔数 | 择时边际 |",
          "|---|---|---|---|---|---|---|---|"]
    for _, r in g[g.th == 1.4].sort_values(["field", "confirm", "hold"]).iterrows():
        L.append(f"| {r['field']} | {int(r['confirm'])} | {int(r['hold'])} | {pct(r['ann'], 2)} | "
                 f"{pct(r['mdd'], 2)} | {num(r['sharpe'])} | {int(r['trades'])} | "
                 f"{r['timing_edge'] * 1e4:+.1f}bp |")

    best = g.loc[g["sharpe"].idxmax()]
    L += ["", f"> 108 格里夏普最高的是 `{best['field']}` / 阈值 {best['th']} / 连续 {int(best['confirm'])} 日 / "
              f"持有 {int(best['hold'])} 日（年化 {pct(best['ann'], 2)}、回撤 {pct(best['mdd'], 2)}、"
              f"夏普 {num(best['sharpe'])}）。**这是看完全部格子后挑出来的，属样本内最优，不能当预期收益。**"]

    z = pd.DataFrame([r for r in rows if r.get("kind") == "size"])
    if not z.empty:
        L += ["", "### 2.3 单次建仓比例（视频「不满仓」· 其余参数取默认）", "",
              "| 投入比例 | 年化 | 最大回撤 | 夏普 | 平均资金占用 | 笔数 | 胜率 |",
              "|---|---|---|---|---|---|---|"]
        for _, r in z.iterrows():
            L.append(f"| {r['size']:.0%} | {pct(r['ann'], 2)} | {pct(r['mdd'], 2)} | "
                     f"{num(r['sharpe'])} | {pct(r['avg_alloc'], 1).replace('+', '')} | "
                     f"{int(r['trades'])} | {pct(r['win_rate'], 1).replace('+', '')} |")
        L.append("")
        L.append("> 按比例缩仓基本是线性缩放（收益与回撤同步变小、夏普几乎不变）："
                 "它解决的是「睡得着」而不是「赚得多」。")

    s = pd.DataFrame([r for r in rows if r.get("kind") == "stop"])
    if not s.empty:
        L += ["", "### 2.4 止损与触发方式（其余参数取默认）", "",
              "| 止损 | 触发方式 | 年化 | 最大回撤 | 夏普 | 笔数 | 胜率 | 择时边际 |",
              "|---|---|---|---|---|---|---|---|"]
        for _, r in s.iterrows():
            L.append(f"| {('-%.0f%%' % (r['stop'] * 100)) if r['stop'] else '关闭'} | "
                     f"{'条件成立当日' if r['edge'] else '满足即买'} | {pct(r['ann'], 2)} | "
                     f"{pct(r['mdd'], 2)} | {num(r['sharpe'])} | {int(r['trades'])} | "
                     f"{pct(r['win_rate'], 1).replace('+', '')} | {r['timing_edge'] * 1e4:+.1f}bp |")
    return L


def build():
    rows, summary = load()
    m, p = main_row(rows, "index"), main_row(rows, "pool")
    mb = dict(ann=m["base_ann"], mdd=m["base_mdd"], vol=None, sharpe=m["base_sharpe"])
    # 买入持有的波动/夏普：从 summary 的 base 净值补算
    base = np.asarray(summary["index"]["base"], float)
    dr = np.diff(base) / base[:-1]
    mb["vol"] = float(np.std(dr, ddof=0) * np.sqrt(252))
    mb["sharpe"] = float(np.mean(dr) / np.std(dr, ddof=0) * np.sqrt(252)) if np.std(dr) > 0 else 0.0
    win = summary["window"]

    L = [
        "# TRIN（阿姆氏指标）市场广度择时 · 回测报告（v16）",
        "",
        f"> 生成时间：{datetime.now():%Y-%m-%d %H:%M} · 参数标签 `{rule.LABEL}` · "
        f"脚本 `strategies/tech_indicators/v16_trin/backtest.py`",
        f"> 信号：全市场 **TRIN 5 日均 > {rule.TRIN_TH} 连续 {rule.TRIN_CONFIRM} 日** → 次日买入，"
        f"持有 {rule.HOLD_DAYS} 个交易日后收盘卖出，收盘跌破 -{rule.STOP:.0%} 止损，其余时间空仓",
        f"> 数据：`data/_breadth_daily.json`（沪深 A 股聚合，TRIN 分母口径 `{rule.FIELD}`）"
        f" + `v16_trin/data/_v16_vehicle.json`（510300 前复权 OHLC）",
        f"> 样本：{win['start']} ~ {win['end']}（{win['days']} 个交易日）· 单边费率 {rule.FEE:.2%} · 不计滑点",
        "",
        "## 一、主口径（视频参数 · 510300 沪深300ETF）",
        "",
    ]
    L += table_main(m, mb, win)
    L += ["", "### 逐年收益（策略 vs 买入持有）", "",
          "| 年份 | 策略 | 买入持有 |", "|---|---|---|"]

    idx_dates = pd.to_datetime(summary["index"]["dates"])
    bh = pd.Series(base, index=idx_dates)
    bh_y = {str(y): float(part.iloc[-1] / part.iloc[0] - 1)
            for y, part in bh.groupby(bh.index.year) if len(part) > 1}
    for y, v in m["yearly"].items():
        L.append(f"| {y} | {pct(v, 2)} | {pct(bh_y.get(y), 2)} |")

    L += bt_stats.section_lines(
        m["t_stats"], title="单笔交易统计（主口径）",
        note="单笔收益已扣双边费用；胜率=盈利笔数/总笔数；盈亏比=平均盈利/|平均亏损|；"
             "利润因子=总盈利/|总亏损|。")

    L += ["", "## 二、参数敏感性", ""]
    L += sensitivity(rows)

    L += ["", "## 三、池子口径（错杀 + 放量温和 + 未破位，3 只等权）", "",
          "| 指标 | TRIN + 选股 | 54 只池等权 |", "|---|---|---|",
          f"| 年化收益 | {pct(p['ann'], 2)} | {pct(p['base_ann'], 2)} |",
          f"| 最大回撤 | {pct(p['mdd'], 2)} | {pct(p['base_mdd'], 2)} |",
          f"| 夏普 | {num(p['sharpe'])} | {num(p['base_sharpe'])} |",
          f"| 持仓占比 | {pct(p['pos_ratio'], 1).replace('+', '')} | 100.0% |",
          f"| 笔数 | {p['trades']} | — |",
          f"| 单笔胜率 | {pct(p['t_stats']['win_rate'], 1).replace('+', '')} | — |",
          f"| 盈亏比 | {num(p['t_stats']['payoff'])} | — |",
          f"| 择时边际 | {p['timing_edge'] * 1e4:+.1f}bp | — |",
          "",
          "> 执行口径差异：池子口径只能用共享十年库的**收盘价**，成交价 = 信号次日收盘；"
          "主口径用 510300 的**开盘价**，与视频一致。两行不可直接比大小。",
          ]

    picks = summary.get("pool_picks") or []
    if picks:
        L += ["", "### 最近 5 次触发选中的标的", "",
              "| 信号日 | 选中 | 错杀阈值(ret20) | 合格只数 |", "|---|---|---|---|"]
        for x in picks[-5:]:
            L.append(f"| {x['date']} | {'、'.join(x['codes']) or '（无合格标的，空仓）'} | "
                     f"{pct(x.get('threshold'), 1)} | {x.get('eligible', 0)} |")

    L += ["", "## 四、结论与局限", ""]
    L += [
        f"1. **视频参数在本样本里只有「降回撤」的作用，没有收益优势**："
        f"年化 {pct(m['ann'], 2)} vs 买入持有 {pct(mb['ann'], 2)}，"
        f"但回撤 {pct(m['mdd'], 2)} vs {pct(mb['mdd'], 2)}，持仓时间只有 "
        f"{pct(m['pos_ratio'], 1).replace('+', '')}。",
        f"2. **信号本身有方向性**：持仓日的平均日收益比空仓日高 "
        f"{m['timing_edge'] * 1e4:+.1f}bp（全期 {m['trades']} 笔、胜率 "
        f"{pct(m['t_stats']['win_rate'], 1).replace('+', '')}），说明「恐慌放量后 20 日」这段确实偏强；"
        f"但一年只持仓约 40 个交易日，beta 少拿的收益大于择时省下的亏损。",
        "3. **1.4 不是平台而是斜坡**：阈值从 1.2 抬到 1.8，年化单调变差、持仓单调变少、"
        "择时边际也变薄（见 3.1）。这说明 A 股里 TRIN 高位不是「越极端越好」，"
        "而是「越宽松越像加仓信号」—— 与视频「连续 >1.4 = 阶段底」的直觉不完全一致。",
        "4. **视频宣称的平均 8.13%、胜率 71% 复现不出来**：本样本单笔胜率 "
        f"{pct(m['t_stats']['win_rate'], 1).replace('+', '')}、平均盈利 {pct(m['t_stats']['avg_win'], 1)}、"
        f"平均亏损 {pct(m['t_stats']['avg_loss'], 1)}。差异来源很多：标的（个股组合 vs 沪深300ETF）、"
        "样本区间、是否复权、是否含滑点，以及视频大概率是**样本内挑过参数**的结果。",
        "5. **选股层没有加分**：错杀 + 放量温和 + 未破位的 3 只等权，"
        f"年化 {pct(p['ann'], 2)} vs 全池等权 {pct(p['base_ann'], 2)}，"
        f"回撤 {pct(p['mdd'], 2)} vs {pct(p['base_mdd'], 2)}。「基本面/逻辑未坏」只能拿价格结构代理，"
        "这一层本质上是「跌得多的先买」，并不构成 alpha。",
        "6. **已知边界**：①广度库的股票列表是**当前**存续标的，退市股历史缺失（存活偏差，早年家数略少）；"
        "②沪深 A 股口径不含北交所/B 股；③涨跌方向用前复权收盘自算，除权日不会被误记成暴跌；"
        "④单边 0.03% 佣金、未计滑点与冲击成本；⑤参数网格是样本内扫描，"
        "用它挑最优格等于过拟合，只能用来判断「参数的形状」；⑥单边熊市里 TRIN 会钝化"
        "（连续高位可以更高），本策略没有趋势过滤，这一点与视频描述一致。",
        "",
        "> 机械规则回测输出，**非投资建议**。",
    ]

    open(MD, "w", encoding="utf-8").write("\n".join(L) + "\n")
    plot(summary)
    print(f"[报告] {MD}\n[图表] {PNG}")


if __name__ == "__main__":
    build()
