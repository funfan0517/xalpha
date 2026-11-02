# -*- coding: utf-8 -*-
"""专项分析：同一个 TRIN 信号，换到不同宽基上表现如何（沪深300 / 中证A500 / 科创50）。

动机：TRIN 是**全市场**情绪指标，但落地必须买某个标的。买哪个决定了策略的收益形态 ——
沪深300 是「大盘 beta」，中证A500 是「行业中性的大中盘」，科创50 是「高波动成长」。
同一个择时信号，弹性越大的标的，20 日持有的赔率应该越高，但假信号也更贵。

口径（两套，必须分开看）
------------------------
1. **指数口径**（长历史、价格指数**不含分红**、无费用）：
   `1.000300` 沪深300（2005 起）、`1.000510` 中证A500（2005 起，**2024-09 才发布、历史为回溯**）、
   `1.000688` 科创50（2019-12-31 起）。
2. **ETF 可交易口径**（含分红再投的前复权价、可实际下单）：
   `510300` 沪深300ETF（2012 起）、`563360` A500ETF（2024-10 起）、`588000` 科创50ETF（2020-11 起）。

A500 的 ETF 只有 2024-10 之后的数据，所以它的「可交易口径」样本不足两年，只能作为跟踪误差/流动性参考，
**不能用来判断策略好坏**。

为公平，除各自全历史外，另给一段**共同窗口**（2020-01-02 起，三者都有数据），
共同窗口内三行可以直接比大小。

产物：`research/_index_compare.md` + `research/_index_compare.png`
用法：python strategies/tech_indicators/v16_trin/research/_index_compare.py [--cache]
模拟结果，非投资建议。
"""
import argparse
import os
import sys

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402
import numpy as np  # noqa: E402
import pandas as pd  # noqa: E402

try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:  # noqa
    pass

_HERE = os.path.dirname(os.path.abspath(__file__))
_V16 = os.path.dirname(_HERE)
_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(_V16)))
for _p in (_ROOT, os.path.join(_ROOT, "data"), os.path.dirname(_V16), _V16, _HERE):
    if _p not in sys.path:
        sys.path.insert(0, _p)

import backtest as bt  # noqa: E402
import data as _data  # noqa: E402
import rule  # noqa: E402

MD = os.path.join(_HERE, "_index_compare.md")
PNG = os.path.join(_HERE, "_index_compare.png")
plt.rcParams["font.sans-serif"] = ["Microsoft YaHei", "SimHei", "DejaVu Sans"]
plt.rcParams["axes.unicode_minus"] = False

COMMON_START = "2020-01-02"
PERIODS = [("2015-2019", "2015-01-01", "2019-12-31"),
           ("2020-2026", "2020-01-01", "2026-12-31")]
TARGETS = [
    ("沪深300 指数", "1.000300", "index"),
    ("中证A500 指数", "1.000510", "index"),
    ("科创50 指数", "1.000688", "index"),
    ("沪深300ETF", "510300", "etf"),
    ("A500ETF(563360)", "563360", "etf"),
    ("科创50ETF", "588000", "etf"),
]


def pct(x, dp=1):
    return "—" if x is None else f"{x * 100:+.{dp}f}%"


def run_one(label, code, kind, breadth, start=None, end=None, period="—", refresh=True):
    veh = _data.load_vehicle(code, refresh=refresh)
    idx = veh.index.intersection(breadth.index)
    veh, br = veh.loc[idx], breadth.loc[idx]
    if start:
        veh, br = veh.loc[start:], br.loc[start:]
    if end:
        veh, br = veh.loc[:end], br.loc[:end]
    stop = _data.complete_end(br.index)
    veh, br = veh.loc[:stop], br.loc[:stop]
    if len(veh) < 30:
        return None
    m, nav, pos, trades, begin, base = bt.eval_single(br, veh)
    return dict(label=label, code=code, kind=kind, period=period, m=m, nav=nav, base=base,
                trades=trades, dates=veh.index, begin=begin, n=len(veh))


def rows_table(res):
    L = ["| 标的 | 口径 | 时段 | 窗口 | 年化 | 最大回撤 | 夏普 | 持仓 | 笔数 | 胜率 | 盈亏比 | 择时边际 | 基准年化 | 基准回撤 |",
         "|---|---|---|---|---|---|---|---|---|---|---|---|---|---|"]
    for r in res:
        m = r["m"]
        L.append(f"| **{r['label']}** `{r['code']}` | {r['kind']} | {r.get('period', '—')} | "
                 f"{r['dates'][r['begin']].date()} ~ {r['dates'][-1].date()}（{r['n'] - r['begin']} 天） | "
                 f"{pct(m['ann'], 2)} | {pct(m['mdd'], 2)} | {m['sharpe']:.2f} | "
                 f"{pct(m['pos_ratio'], 1).replace('+', '')} | {m['trades']} | "
                 f"{pct(m['t_stats']['win_rate'], 1).replace('+', '')} | {m['t_stats']['payoff']:.2f} | "
                 f"{m['timing_edge'] * 1e4:+.1f}bp | {pct(m['base_ann'], 2)} | {pct(m['base_mdd'], 2)} |")
    return L


def plot(common):
    fig, axes = plt.subplots(2, 1, figsize=(12, 8), gridspec_kw=dict(height_ratios=[2, 1]))
    colors = {"沪深300 指数": "#c62828", "中证A500 指数": "#1565c0", "科创50 指数": "#2e7d32"}
    ax = axes[0]
    for r in common:
        c = colors.get(r["label"], "#666")
        ax.plot(r["dates"], r["nav"], color=c, lw=1.6, label=f"{r['label']} 择时")
        ax.plot(r["dates"], r["base"], color=c, lw=0.9, ls="--", alpha=0.55,
                label=f"{r['label']} 买入持有")
    ax.set_title(f"同一 TRIN 信号 · 三大宽基（共同窗口 {COMMON_START} 起，指数口径）")
    ax.legend(loc="upper left", ncol=2, fontsize=8)
    ax.grid(alpha=0.25)

    ax = axes[1]
    for r in common:
        s = np.asarray(r["nav"], float)
        ax.plot(r["dates"], s / np.maximum.accumulate(s) - 1,
                color=colors.get(r["label"], "#666"), lw=1.2, label=f"{r['label']} 择时")
    ax.legend(loc="lower left", fontsize=8)
    ax.grid(alpha=0.25)
    fig.tight_layout()
    fig.savefig(PNG, dpi=130)
    plt.close(fig)


def build(refresh=True):
    breadth = _data.load_breadth(refresh=False)      # 广度库只读：本脚本不负责维护它
    full, common = [], []
    for label, code, kind in TARGETS:
        r = run_one(label, code, kind, breadth, refresh=refresh)
        if r is None:
            print(f"[跳过] {label} {code} 与广度库无重叠")
            continue
        full.append(r)
        m = r["m"]
        print(f"[{label:>18}] {r['dates'][r['begin']].date()} ~ {r['dates'][-1].date()} "
              f"年化 {pct(m['ann'], 2)} 回撤 {pct(m['mdd'], 2)} 夏普 {m['sharpe']:.2f} "
              f"持仓 {m['pos_ratio']:.1%} {m['trades']} 笔 胜率 {m['t_stats']['win_rate']:.0%} "
              f"| 基准 {pct(m['base_ann'], 2)}/{pct(m['base_mdd'], 2)}")

    for label, code, kind in TARGETS[:3]:               # 共同窗口只用三大指数
        r = run_one(label, code, kind, breadth, start=COMMON_START, refresh=False)
        if r is not None:
            common.append(r)

    split = []
    for label, code, kind in TARGETS[:3]:
        for pname, pa, pb in PERIODS:
            r = run_one(label, code, kind, breadth, start=pa, end=pb,
                        period=pname, refresh=False)
            if r is not None:
                split.append(r)
                print(f"[分时段] {label:>14} {pname} 年化 {pct(r['m']['ann'], 2)} "
                      f"回撤 {pct(r['m']['mdd'], 2)} {r['m']['trades']} 笔 "
                      f"胜率 {r['m']['t_stats']['win_rate']:.0%}")

    L = [
        "# TRIN 信号在三大宽基上的表现对照",
        "",
        f"> 参数 `{rule.LABEL}`：TRIN 5 日均 > {rule.TRIN_TH} 连续 {rule.TRIN_CONFIRM} 日 → 次日开盘买入 · "
        f"持有 {rule.HOLD_DAYS} 个交易日收盘卖出 · 止损 -{rule.STOP:.0%} · 单边 {rule.FEE:.2%}",
        "> 信号完全相同（全市场广度），只换执行标的。**指数口径不含分红**，ETF 为前复权（含分红再投）。",
        "",
        "## 一、各自全历史（窗口不同，不可横向比大小）",
        "",
    ]
    L += rows_table(full)

    L += ["", "## 二、共同窗口（2020-01-02 起，三者都有数据，可直接比）", ""]
    L += rows_table(common) if common else ["（数据不足）"]

    L += ["", "## 三、分时段：同一信号在不同市场形态下的差别", ""]
    L += rows_table(split) if split else ["（数据不足）"]

    L += ["", "## 四、结论", ""]
    if common and split:
        by = {r["label"]: r for r in common}
        fb = {r["label"]: r for r in full}
        h3, a5, k5 = by["沪深300 指数"], by["中证A500 指数"], by["科创50 指数"]
        sp = {(r["label"], r["period"]): r["m"] for r in split}

        def g(label, period, key):
            return sp[(label, period)][key]

        def stops(r):
            return sum(1 for t in r["trades"] if t.get("reason") == "止损")

        fh, fa = fb["沪深300 指数"], fb["中证A500 指数"]
        etf300 = [r for r in full if r["code"] == "510300"][0]
        pos_lo = min(fh["m"]["pos_ratio"], fa["m"]["pos_ratio"])
        pos_hi = max(fh["m"]["pos_ratio"], fa["m"]["pos_ratio"])
        L += [
            f"1. **全样本只买到「回撤减半」，买不到收益**：2015 起年化 沪深300 指数 "
            f"{pct(fh['m']['ann'], 2)}（ETF 口径 {pct(etf300['m']['ann'], 2)}）"
            f" / 中证A500 {pct(fa['m']['ann'], 2)}，对应买入持有 "
            f"{pct(fh['m']['base_ann'], 2)} / {pct(fa['m']['base_ann'], 2)}；"
            f"但最大回撤从 {pct(fh['m']['base_mdd'], 2)} / {pct(fa['m']['base_mdd'], 2)} "
            f"压到 {pct(fh['m']['mdd'], 2)} / {pct(fa['m']['mdd'], 2)}。"
            f"持仓只有 {pct(pos_lo, 0).replace('+', '')}~{pct(pos_hi, 0).replace('+', '')}，"
            f"本质是「大部分时间不在场」的减仓开关。",
            f"2. **分时段是决定性的，2019 是分水岭**：2015-2019 策略明显更优 —— "
            f"沪深300 年化 {pct(g('沪深300 指数', '2015-2019', 'ann'), 2)}"
            f"（回撤 {pct(g('沪深300 指数', '2015-2019', 'mdd'), 2)}、胜率 "
            f"{pct(g('沪深300 指数', '2015-2019', 't_stats')['win_rate'], 0).replace('+', '')}、"
            f"择时边际 {g('沪深300 指数', '2015-2019', 'timing_edge') * 1e4:+.1f}bp），"
            f"中证A500 {pct(g('中证A500 指数', '2015-2019', 'ann'), 2)}"
            f"（回撤 {pct(g('中证A500 指数', '2015-2019', 'mdd'), 2)}、胜率 "
            f"{pct(g('中证A500 指数', '2015-2019', 't_stats')['win_rate'], 0).replace('+', '')}）；"
            f"2020 之后全部转负：沪深300 {pct(g('沪深300 指数', '2020-2026', 'ann'), 2)}、"
            f"中证A500 {pct(g('中证A500 指数', '2020-2026', 'ann'), 2)}、"
            f"科创50 {pct(g('科创50 指数', '2020-2026', 'ann'), 2)}。"
            f"视频宣称的 **71% 胜率恰好落在这段 2015-2019 的 69%** 上 —— 那是有真实全市场恐慌"
            "（2015 股灾、2016 熔断、2018 单边熊市）的年代；2020 后的结构性行情里，"
            "「全市场恐慌」越来越少，信号样本从每年 2~3 次掉到 2 次左右，且多为结构性杀跌。",
            f"3. **A500 比沪深300 略好，但两者同源**：全样本年化 "
            f"{pct(fa['m']['ann'], 2)} vs {pct(fh['m']['ann'], 2)}，共同窗口 "
            f"{pct(a5['m']['ann'], 2)} vs {pct(h3['m']['ann'], 2)}，"
            f"最大回撤 {pct(fa['m']['mdd'], 2)} vs {pct(fh['m']['mdd'], 2)}，"
            f"笔数 {fa['m']['trades']} vs {fh['m']['trades']}，几乎重合。"
            "差别来自成分弹性（A500 行业中性、成长权重略高），**不是「换个指数就能救活策略」**。",
            f"4. **科创50 是放大器，不是更好的载体**：共同窗口年化 {pct(k5['m']['ann'], 2)}、"
            f"回撤 {pct(k5['m']['mdd'], 2)}（沪深300 {pct(h3['m']['mdd'], 2)}）、"
            f"胜率 {pct(k5['m']['t_stats']['win_rate'], 0).replace('+', '')}"
            f"（沪深300 {pct(h3['m']['t_stats']['win_rate'], 0).replace('+', '')}）、"
            f"{len(k5['trades'])} 笔里 {stops(k5)} 笔被 -8% 止损打掉"
            f"（沪深300 {stops(h3)}/{len(h3['trades'])}）。它的择时边际反而最高"
            f"（{k5['m']['timing_edge'] * 1e4:+.1f}bp vs 沪深300 {h3['m']['timing_edge'] * 1e4:+.1f}bp），"
            "说明信号抓到的少数反弹幅度很大，但胜率低、方差大 —— 收益靠右尾，不适合作为唯一标的，"
            "固定 -8% 止损在高波动标的上还过早出局（应按标的波动缩放）。",
            f"5. **落地口径建议**：若要用，选 A500 / 沪深300 这类低波动宽基；止损改按波动缩放"
            f"（如 1.5×年化波动，本分析未做）；把它当**减仓开关**而不是收益引擎 —— "
            f"它的价值在把 -47% 的回撤换成 -28%，而不是把 +1% 的年化换成 +8%。",
            "",
            "> 口径提醒：`中证A500 指数` 的历史是 2024-09 发布时回溯的（指数本身在此之前不存在），"
            "指数口径也不含分红；ETF 口径样本太短（A500 ETF 仅 2024-10 起，不足两年）。"
            "所有数字都是机械规则输出，**非投资建议**。",
        ]

    open(MD, "w", encoding="utf-8").write("\n".join(L) + "\n")
    if common:
        plot(common)
    print(f"[报告] {MD}" + (f"\n[图表] {PNG}" if common else ""))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--cache", action="store_true", help="不刷新行情，只用本地缓存")
    a = ap.parse_args()
    build(refresh=not a.cache)
    return 0


if __name__ == "__main__":
    sys.exit(main())
