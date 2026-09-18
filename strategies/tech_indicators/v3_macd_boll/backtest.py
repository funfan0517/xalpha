# -*- coding: utf-8 -*-
"""技术指标策略 · 变种 3：MACD + 布林通道 波段 · 全池回测。

核心：用 MACD 过滤震荡、定多空；用布林找突破入场与转势离场；布林带平行时不做，
只抓开口后的波段行情。

- 入场：MACD 金叉且站上零轴（DIF>0 且 DIF>DEA）；
        布林三线开口放大（带宽 (上轨-下轨)/中轨 放大）且 K 线突破上轨（收上穿）。
- 离场：MACD 死叉（DIF 下穿 DEA）或 价格自上轨下穿中轨（转弱）。
- 观望：布林带平行走平、K 线在通道内震荡 → 无突破即不进场（自然空仓）。

未量化项：MACD 顶背离（主观信号）。
参数：MACD(12,26,9)、BOLL(20,2)。

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

VARIANT = "MACD+BOLL"
BUY_DESC = "DIF>0 且 DIF>DEA（零轴上金叉状态）且 布林带宽放大（开口）且 收盘上穿布林上轨（突破）"
SELL_DESC = "DIF 下穿 DEA（死叉）或 收盘自上轨下穿布林中轨（转弱）"


def build_variant(close, r):
    """MACD 定多空 + 布林开口突破买入; 死叉或下穿中轨卖出。"""
    n = len(close)
    dif, dea = base._arr(r["DIF"]), base._arr(r["DEA"])
    upper, mid, lower = base._arr(r["BOLL_UPPER"]), base._arr(r["BOLL_MID"]), base._arr(r["BOLL_LOWER"])
    bw = (upper - lower) / mid  # 布林带宽(相对)
    sig = np.zeros(n, dtype=int)
    p = 0
    for t in range(1, n):
        need = (dif[t], dea[t], upper[t], mid[t], lower[t],
                dif[t - 1], dea[t - 1], upper[t - 1], mid[t - 1], bw[t - 1])
        if any(np.isnan(x) for x in need):
            sig[t] = p
            continue
        breakout = close[t] > upper[t] and close[t - 1] <= upper[t - 1]   # 上穿上轨
        expand = bw[t] > bw[t - 1]                                        # 开口放大
        if p == 0:
            if dif[t] > 0 and dif[t] > dea[t] and expand and breakout:
                p = 1
        else:
            death = dif[t] < dea[t] and dif[t - 1] >= dea[t - 1]
            mid_break = close[t] < mid[t] and close[t - 1] >= mid[t - 1]
            if death or mid_break:
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
              "KDJ": "#27c", "BOLL": "#93c", VARIANT: "#e56"}
    for kk in ["BH"] + base.STRAT + [VARIANT]:
        if kk in curves:
            ax.plot(x, curves[kk], label=("买入持有" if kk == "BH" else kk),
                    color=colors.get(kk), linewidth=1.4)
    ax.set_title("510300 沪深300ETF · MACD+BOLL 变种 vs 基础策略 vs 买入持有（净值, 起=1）")
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

    with open(os.path.join(OUTDIR, "_v3_macd_boll_results.jsonl"), "w", encoding="utf-8") as f:
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
        "# 技术指标策略 · 变种 3：MACD + 布林通道 波段 · 全池回测",
        "> 55 只场内 ETF · 各自全历史（第 61 个交易日起）· 多头/空仓 0-1 · "
        "信号当日收盘成交 · 未计佣金滑点 · 无风险利率 0",
        "",
        "## 策略规则",
        "",
        f"- **入场**：{BUY_DESC}",
        f"- **离场**：{SELL_DESC}",
        "- **观望**：布林带平行走平、K 线在通道内震荡 → 无突破即不进场（自然空仓）。",
        "",
        "> 说明：MACD 顶背离为主观信号，未纳入量化。",
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

    with open(os.path.join(OUTDIR, "_v3_macd_boll_report.md"), "w", encoding="utf-8") as f:
        f.write("\n".join(L))

    _plot(dates_510300, curves, os.path.join(OUTDIR, "_v3_macd_boll_510300.png"))
    print("报告:", os.path.join(OUTDIR, "_v3_macd_boll_report.md"))
    print("结果:", os.path.join(OUTDIR, "_v3_macd_boll_results.jsonl"))


if __name__ == "__main__":
    main()
