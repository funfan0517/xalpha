# -*- coding: utf-8 -*-
"""技术指标策略 · 变种 1：KDJ + MA10 共振 · 全池回测。

核心：KDJ 找超卖(J<20 金叉)、10 日均线定趋势，两者共振再买卖。
口诀：线上多、线下空，均线不破趋势不改。

- 买入：KDJ 超卖金叉（K 上穿 D 且 J<20）后，窗口内价格站稳 > MA10 → 共振进场；
- 卖出：收盘跌破 MA10（持仓中 KDJ 反复交叉不动，只要价不破 MA10 就持有）。

实现说明：KDJ 超卖(J<20) 与「价站稳 MA10」同日几乎互斥（超卖时价多在 MA10 之下），
故按策略原文「**随后**…站稳…共振」的**时序**实现：先记超卖金叉，再在 WAIT 个交易日内
等价格收复 MA10 确认进场。

本脚本**复用基线** ../../backtest.py 的引擎与 5 个基础策略（只读导入，不修改基线文件），
额外加入本变种并与之对照；产物落在本目录 backtest/ 下。模拟结果，非投资建议。
"""
import importlib.util
import json
import os
import sys

import numpy as np
import pandas as pd

try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:  # noqa
    pass

_HERE = os.path.dirname(os.path.abspath(__file__))
_PARENT = os.path.dirname(_HERE)                    # strategies/tech_indicators
_ROOT = os.path.dirname(os.path.dirname(_PARENT))   # 仓库根

# 以独立命名载入基线引擎（不重写、不覆盖基线文件）
_spec = importlib.util.spec_from_file_location("ti_base", os.path.join(_PARENT, "backtest.py"))
base = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(base)

DATA = base.DATA
OUTDIR = os.path.join(_HERE, "backtest")
COHORT_2015 = base.COHORT_2015
WARMUP = base.WARMUP

VARIANT = "KDJ+MA10"
J_LOW = 20   # 超卖阈值(J)
WAIT = 10    # 超卖金叉后，等待价站稳 MA10 的确认窗口(交易日)
BUY_DESC = f"KDJ 超卖金叉（K 上穿 D 且 J<{J_LOW}）后，{WAIT} 个交易日内价格站稳 > MA10 → 共振进场"
SELL_DESC = "收盘跌破 MA10（线上多、线下空；持仓中 KDJ 反复交叉不动）"


def build_variant(close, r, j_low=J_LOW, wait=WAIT):
    """KDJ+MA10 共振：超卖金叉后窗口内价站稳 MA10 买入；收盘跌破 MA10 卖出。"""
    n = len(close)
    k, d, j = base._arr(r["K"]), base._arr(r["D"]), base._arr(r["J"])
    ma10 = base._arr(r["MA10"])
    sig = np.zeros(n, dtype=int)
    p, pending = 0, -1
    for t in range(1, n):
        if (np.isnan(k[t]) or np.isnan(d[t]) or np.isnan(j[t]) or np.isnan(ma10[t])
                or np.isnan(k[t - 1]) or np.isnan(d[t - 1]) or np.isnan(ma10[t - 1])):
            sig[t] = p
            continue
        if k[t] > d[t] and k[t - 1] <= d[t - 1] and j[t] < j_low:
            pending = t
        if p == 0:
            if pending != -1 and (t - pending) <= wait and close[t] > ma10[t]:
                p = 1
                pending = -1
        elif p == 1 and close[t] < ma10[t] and close[t - 1] >= ma10[t - 1]:
            p = 0
        sig[t] = p
    return sig


def _row(label, a):
    return (f"| {label} | {base.pct(a['ann'])} | {base.pct(a['dd'])} | {a['sharpe']:.2f} | "
            f"{a['exposure']:.0%} | {a['beat_ret']:.0%} | {a['beat_dd']:.0%} | "
            f"{a['pooled']['n']} | {base.pct(a['pooled']['win_rate'])} |")


def _plot(dates, curves, path):
    try:
        import matplotlib
        matplotlib.use("Agg")
        import matplotlib.pyplot as plt
    except Exception as e:  # noqa
        print("matplotlib 不可用，跳过作图:", e)
        return
    plt.rcParams["font.sans-serif"] = ["Microsoft YaHei", "SimHei", "DejaVu Sans"]
    plt.rcParams["axes.unicode_minus"] = False
    x = pd.to_datetime(dates)
    fig, ax = plt.subplots(figsize=(12, 6))
    colors = {"BH": "#888", "MA": "#d33", "VOL": "#e90", "MACD": "#2a2",
              "KDJ": "#27c", "BOLL": "#93c", VARIANT: "#c2185b"}
    for kk in ["BH"] + base.STRAT + [VARIANT]:
        if kk in curves:
            ax.plot(x, curves[kk], label=("买入持有" if kk == "BH" else kk),
                    color=colors.get(kk), linewidth=1.4)
    ax.set_title("510300 沪深300ETF · KDJ+MA10 变种 vs 基础策略 vs 买入持有（净值, 起=1）")
    ax.set_ylabel("净值")
    ax.grid(alpha=0.3)
    ax.legend()
    fig.tight_layout()
    fig.savefig(path, dpi=120)
    plt.close(fig)
    print("图:", path)


def main():
    ind, lk = base.load()
    var_rows = []
    baserows = {s: [] for s in base.STRAT}
    curves, dates_510300 = {}, None

    for code, r in ind.items():
        close = base._arr(lk[code]["close"])
        dates = lk[code]["dates"]
        if len(close) != len(dates) or len(close) <= WARMUP + 2:
            continue
        bh, bh_eq = base.backtest_bh(close, dates, code)

        m, eq, trades = base.backtest(close, build_variant(close, r), dates, code)
        var_rows.append({"code": code, "m": m, "bh": bh, "trades": trades})

        sigs = base.build_signals(close, r)
        base_curves = {}
        for s in base.STRAT:
            m5, eq5, tr5 = base.backtest(close, sigs[s], dates, code)
            baserows[s].append({"code": code, "m": m5, "bh": bh, "trades": tr5})
            base_curves[s] = eq5

        if code == "510300":
            dates_510300 = dates[WARMUP:]
            curves = {"BH": bh_eq, VARIANT: eq}
            curves.update(base_curves)

    os.makedirs(OUTDIR, exist_ok=True)

    with open(os.path.join(OUTDIR, "_v1_kdj_ma10_results.jsonl"), "w", encoding="utf-8") as f:
        for row in var_rows:
            f.write(json.dumps({
                "code": row["code"], "strategy": VARIANT,
                "ann": round(row["m"]["ann"], 4), "dd": round(row["m"]["dd"], 4),
                "sharpe": round(row["m"]["sharpe"], 3), "exposure": round(row["m"]["exposure"], 3),
                "n_trades": row["m"]["n"], "win_rate": round(row["m"]["win_rate"], 4),
                "bh_ann": round(row["bh"]["ann"], 4), "bh_dd": round(row["bh"]["dd"], 4),
            }, ensure_ascii=False) + "\n")

    longset = {c for c in ind if lk[c]["dates"][0] <= COHORT_2015}
    cohort = [x for x in var_rows if x["code"] in longset]
    cbase = {s: [x for x in baserows[s] if x["code"] in longset] for s in base.STRAT}

    a_var, a_cvar = base._agg(var_rows), base._agg(cohort)
    a_base = {s: base._agg(baserows[s]) for s in base.STRAT}
    a_cbase = {s: base._agg(cbase[s]) for s in base.STRAT}
    bh_ann = np.median([x["bh"]["ann"] for x in var_rows])
    bh_dd = np.median([x["bh"]["dd"] for x in var_rows])
    bh_sh = np.median([x["bh"]["sharpe"] for x in var_rows])
    c_bh_ann = np.median([x["bh"]["ann"] for x in cohort])
    c_bh_dd = np.median([x["bh"]["dd"] for x in cohort])
    c_bh_sh = np.median([x["bh"]["sharpe"] for x in cohort])

    L = [
        "# 技术指标策略 · 变种 1：KDJ + MA10 共振 · 全池回测",
        "> 55 只场内 ETF · 各自全历史（第 61 个交易日起）· 多头/空仓 0-1 · "
        "信号当日收盘成交 · 未计佣金滑点 · 无风险利率 0",
        "",
        "## 策略规则",
        "",
        f"- **买入**：{BUY_DESC}",
        f"- **卖出**：{SELL_DESC}",
        f"- **持有过滤**：持仓中只要不跌破 MA10，趋势视为未破，KDJ 反复交叉不动。",
        "",
        "> 实现说明：KDJ 超卖(J<20) 与「价站稳 MA10」同日几乎互斥（超卖时价多在 MA10 之下），",
        f"> 故按原文「**随后**…站稳…共振」的**时序**实现（确认窗口 {WAIT} 个交易日）。",
        "",
        "## 全池汇总（中位数 · 55 只）",
        "",
        "| 策略 | 中位年化 | 中位最大回撤 | 中位夏普 | 中位曝光 | 跑赢买入持有 | 回撤更小 | 总交易 | 合计胜率 |",
        "|---|---|---|---|---|---|---|---|---|",
        _row(f"**{VARIANT}（变种）**", a_var),
    ]
    for s in base.STRAT:
        L.append(_row(s, a_base[s]))
    L.append(f"| 买入持有(基准) | {base.pct(bh_ann)} | {base.pct(bh_dd)} | {bh_sh:.2f} | 100% | — | — | 1 | — |")

    L += [
        "",
        f"## 长历史子样本（2015 起 · {len(cohort)} 只 · 约 10.7 年）",
        "",
        "| 策略 | 中位年化 | 中位最大回撤 | 中位夏普 | 中位曝光 | 跑赢买入持有 | 回撤更小 | 总交易 | 合计胜率 |",
        "|---|---|---|---|---|---|---|---|---|",
        _row(f"**{VARIANT}（变种）**", a_cvar),
    ]
    for s in base.STRAT:
        L.append(_row(s, a_cbase[s]))
    L.append(f"| 买入持有(基准) | {base.pct(c_bh_ann)} | {base.pct(c_bh_dd)} | {c_bh_sh:.2f} | 100% | — | — | 1 | — |")

    L += ["", "## 代表标的：510300 沪深300ETF（2015-01-05 起）", "",
          "| 口径 | 年化 | 最大回撤 | 夏普 | 总收益 | 卡玛比率 | 交易 | 胜率 | 盈亏比 |",
          "|---|---|---|---|---|---|---|---|---|"]
    v510 = {x["code"]: x for x in var_rows}["510300"]
    bh510, m510 = v510["bh"], v510["m"]
    L.append(f"| 买入持有 | {base.pct(bh510['ann'])} | {base.pct(bh510['dd'])} | {bh510['sharpe']:.2f} | "
             f"{base.pct(bh510['total'])} | {bh510['calmar']:.2f} | 1 | — | — |")
    L.append(f"| **{VARIANT}（变种）** | {base.pct(m510['ann'])} | {base.pct(m510['dd'])} | {m510['sharpe']:.2f} | "
             f"{base.pct(m510['total'])} | {m510['calmar']:.2f} | {m510['n']} | "
             f"{base.pct(m510['win_rate'])} | {m510['payoff']:.2f} |")
    for s in base.STRAT:
        m5 = {x["code"]: x for x in baserows[s]}["510300"]["m"]
        L.append(f"| {s} | {base.pct(m5['ann'])} | {base.pct(m5['dd'])} | {m5['sharpe']:.2f} | "
                 f"{base.pct(m5['total'])} | {m5['calmar']:.2f} | {m5['n']} | "
                 f"{base.pct(m5['win_rate'])} | {m5['payoff']:.2f} |")

    pooled = base.trade_stats([t for x in var_rows for t in x["trades"]])
    L += base.section_lines(pooled, title=f"单笔交易统计（全池合并 · {VARIANT}）")
    L += ["", "> 口径：单笔=建/平仓收盘价收益（未计费用）。**模拟结果，非投资建议。**"]

    with open(os.path.join(OUTDIR, "_v1_kdj_ma10_report.md"), "w", encoding="utf-8") as f:
        f.write("\n".join(L))

    _plot(dates_510300, curves, os.path.join(OUTDIR, "_v1_kdj_ma10_510300.png"))
    print("报告:", os.path.join(OUTDIR, "_v1_kdj_ma10_report.md"))
    print("结果:", os.path.join(OUTDIR, "_v1_kdj_ma10_results.jsonl"))


if __name__ == "__main__":
    main()
