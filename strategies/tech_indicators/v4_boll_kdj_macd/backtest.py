# -*- coding: utf-8 -*-
"""技术指标策略 · 变种 4：BOLL_KDJ_MACD（布林 + KDJ + MACD 三共振）· 全池回测。

分工：布林带定空间（下轨支撑/上轨压力/中轨方向）、MACD 定趋势动能、KDJ 定情绪时机；
三者共振以提高抄底/逃顶判断胜率。它是辅助判断工具，不是稳赚公式。

- 抄底买入：价格接近布林下轨 + MACD 金叉 + KDJ 超卖拐头金叉，三者共振。
- 逃顶卖出：价格接近布林上轨 + MACD 死叉 + KDJ 超买拐头死叉，三者共振。

通道位置 bpos = (收盘 - 下轨) / (上轨 - 下轨)。
未量化项：MACD 底/顶背离（主观信号）。

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

VARIANT = "BOLL_KDJ_MACD"
BUY_POS = 0.20    # 接近下轨: 通道位置 <= 20%
SELL_POS = 0.80   # 接近上轨: 通道位置 >= 80%
KWIN = 10         # 情绪窗口: 近 KWIN 日 K 曾进入超卖/超买
RES = 10          # 共振窗口: 近 RES 日曾触及下/上轨
BUY_DESC = (f"价格接近布林下轨(近{RES}日通道位置曾≤{BUY_POS:.0%}) 且 MACD 金叉(DIF>DEA) "
            f"且 KDJ 超卖拐头金叉(近{KWIN}日 K<20 且现 K>D)")
SELL_DESC = (f"价格接近布林上轨(近{RES}日通道位置曾≥{SELL_POS:.0%}) 且 MACD 死叉(DIF<DEA) "
             f"且 KDJ 超买拐头死叉(近{KWIN}日 K>80 且现 K<D)")


def build_variant(close, r, buy_pos=BUY_POS, sell_pos=SELL_POS, kwin=KWIN, res=RES):
    """BOLL_KDJ_MACD: 布林空间 + MACD 动能 + KDJ 情绪 三共振抄底/逃顶。"""
    n = len(close)
    dif, dea = base._arr(r["DIF"]), base._arr(r["DEA"])
    k, d = base._arr(r["K"]), base._arr(r["D"])
    upper, mid, lower = base._arr(r["BOLL_UPPER"]), base._arr(r["BOLL_MID"]), base._arr(r["BOLL_LOWER"])
    bpos = pd.Series((close - lower) / (upper - lower))
    kmin = pd.Series(k).rolling(kwin).min().to_numpy()
    kmax = pd.Series(k).rolling(kwin).max().to_numpy()
    low_recent = bpos.rolling(res).min().to_numpy() <= buy_pos
    high_recent = bpos.rolling(res).max().to_numpy() >= sell_pos
    sig = np.zeros(n, dtype=int)
    p = 0
    for t in range(1, n):
        need = (dif[t], dea[t], k[t], d[t], upper[t], lower[t], kmin[t], kmax[t])
        if any(np.isnan(x) for x in need) or np.isnan(low_recent[t]) or np.isnan(high_recent[t]):
            sig[t] = p
            continue
        if p == 0:
            if (low_recent[t] and dif[t] > dea[t] and k[t] > d[t] and kmin[t] < 20):
                p = 1
        else:
            if (high_recent[t] and dif[t] < dea[t] and k[t] < d[t] and kmax[t] > 80):
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
              "KDJ": "#27c", "BOLL": "#93c", VARIANT: "#b50"}
    for kk in ["BH"] + base.STRAT + [VARIANT]:
        if kk in curves:
            ax.plot(x, curves[kk], label=("买入持有" if kk == "BH" else kk),
                    color=colors.get(kk), linewidth=1.4)
    ax.set_title("510300 沪深300ETF · BOLL_KDJ_MACD 变种 vs 基础策略 vs 买入持有（净值, 起=1）")
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

    with open(os.path.join(OUTDIR, "_v4_boll_kdj_macd_results.jsonl"), "w", encoding="utf-8") as f:
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
        "# 技术指标策略 · 变种 4：BOLL_KDJ_MACD（布林+KDJ+MACD 三共振）· 全池回测",
        "> 55 只场内 ETF · 各自全历史（第 61 个交易日起）· 多头/空仓 0-1 · "
        "信号当日收盘成交 · 未计佣金滑点 · 无风险利率 0",
        "",
        "## 策略规则",
        "",
        f"- **抄底买入**：{BUY_DESC}",
        f"- **逃顶卖出**：{SELL_DESC}",
        "- **分工**：布林定空间、MACD 定动能、KDJ 定情绪，三者共振。",
        "",
        "> 说明：MACD 底/顶背离为主观信号，未纳入量化。",
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

    with open(os.path.join(OUTDIR, "_v4_boll_kdj_macd_report.md"), "w", encoding="utf-8") as f:
        f.write("\n".join(L))

    _plot(dates_510300, curves, os.path.join(OUTDIR, "_v4_boll_kdj_macd_510300.png"))
    print("报告:", os.path.join(OUTDIR, "_v4_boll_kdj_macd_report.md"))
    print("结果:", os.path.join(OUTDIR, "_v4_boll_kdj_macd_results.jsonl"))


if __name__ == "__main__":
    main()
