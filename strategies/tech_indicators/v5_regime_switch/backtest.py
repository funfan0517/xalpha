# -*- coding: utf-8 -*-
"""技术指标策略 · 变种 5：Regime 切换（趋势用突破 / 震荡用抄底）· 全池回测。

思路：先用 Kaufman 效率比 ER（收盘价可得，ER→1 为趋势、→0 为震荡）判断当前市场状态，
趋势状态用变种 3 的「MACD 零轴上金叉 + 布林开口突破」，震荡状态用变种 4 的
「BOLL_KDJ_MACD 抄底（近下轨 + MACD 金叉 + KDJ 超卖拐头）」，两种买卖逻辑按状态切换。

- ER = |收盘[t]-收盘[t-N]| / Σ|逐日涨跌| over 最近 N 日。
- 滞回：ER >= ER_TREND 进入趋势态；ER <= ER_RANGE 回到震荡态；其间维持原状态。

本脚本**复用基线** ../../backtest.py 的引擎（只读导入，不修改基线文件），
并在报告中对 v3(突破) / v4(抄底) 作参考对照。产物落在本目录 backtest/ 下。
模拟结果，非投资建议。
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

_spec = importlib.util.spec_from_file_location("ti_base", os.path.join(_PARENT, "backtest.py"))
base = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(base)

DATA = base.DATA
OUTDIR = os.path.join(_HERE, "backtest")
COHORT_2015 = base.COHORT_2015
WARMUP = base.WARMUP

VARIANT = "Regime切换"
ER_N = 20         # 效率比窗口
ER_TREND = 0.40   # ER >= 此值 -> 趋势态
ER_RANGE = 0.30   # ER <= 此值 -> 震荡态(滞回区间 0.30~0.40)
DESC_TREND = "趋势态：MACD 零轴上金叉 + 布林开口突破买入，死叉/下穿中轨卖出（同 v3）"
DESC_RANGE = "震荡态：近下轨 + MACD 金叉 + KDJ 超卖拐头买入，近上轨共振逃顶（同 v4）"


# ------------------------- 组件信号（v3 / v4） ------------------------- #
def sig_v3(close, r):
    n = len(close)
    dif, dea = base._arr(r["DIF"]), base._arr(r["DEA"])
    upper, mid, lower = base._arr(r["BOLL_UPPER"]), base._arr(r["BOLL_MID"]), base._arr(r["BOLL_LOWER"])
    bw = (upper - lower) / mid
    sig = np.zeros(n, dtype=int)
    p = 0
    for t in range(1, n):
        need = (dif[t], dea[t], upper[t], mid[t], lower[t], upper[t - 1], mid[t - 1], bw[t - 1])
        if any(np.isnan(x) for x in need):
            sig[t] = p
            continue
        if p == 0:
            if dif[t] > 0 and dif[t] > dea[t] and bw[t] > bw[t - 1] and \
                    close[t] > upper[t] and close[t - 1] <= upper[t - 1]:
                p = 1
        else:
            death = dif[t] < dea[t] and dif[t - 1] >= dea[t - 1]
            mid_break = close[t] < mid[t] and close[t - 1] >= mid[t - 1]
            if death or mid_break:
                p = 0
        sig[t] = p
    return sig


def sig_v4(close, r, buy_pos=0.20, sell_pos=0.80, kwin=10, res=10):
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
        need = (dif[t], dea[t], k[t], d[t], kmin[t], kmax[t], low_recent[t], high_recent[t])
        if any(np.isnan(x) for x in need):
            sig[t] = p
            continue
        if p == 0:
            if low_recent[t] and dif[t] > dea[t] and k[t] > d[t] and kmin[t] < 20:
                p = 1
        else:
            if high_recent[t] and dif[t] < dea[t] and k[t] < d[t] and kmax[t] > 80:
                p = 0
        sig[t] = p
    return sig


def efficiency_ratio(close, n=ER_N):
    s = pd.Series(close)
    change = (s - s.shift(n)).abs()
    vol = s.diff().abs().rolling(n).sum()
    return (change / vol).to_numpy()


def sig_regime(close, r, er_n=ER_N, er_trend=ER_TREND, er_range=ER_RANGE):
    """按 ER 状态在 v3(趋势) 与 v4(震荡) 之间切换。"""
    er = efficiency_ratio(close, er_n)
    s3, s4 = sig_v3(close, r), sig_v4(close, r)
    n = len(close)
    sig = np.zeros(n, dtype=int)
    regime = 0  # 0=震荡, 1=趋势
    for t in range(n):
        if not np.isnan(er[t]):
            if er[t] >= er_trend:
                regime = 1
            elif er[t] <= er_range:
                regime = 0
        sig[t] = s3[t] if regime == 1 else s4[t]
    return sig


def sig_trend_only(close, r, er_n=ER_N, er_trend=ER_TREND, er_range=ER_RANGE):
    """只在趋势态用 v3，震荡态空仓（对照：震荡不做）。"""
    er = efficiency_ratio(close, er_n)
    s3 = sig_v3(close, r)
    n = len(close)
    sig = np.zeros(n, dtype=int)
    regime = 0
    for t in range(n):
        if not np.isnan(er[t]):
            if er[t] >= er_trend:
                regime = 1
            elif er[t] <= er_range:
                regime = 0
        sig[t] = s3[t] if regime == 1 else 0
    return sig


def _run_cfg(ind, lk, fn):
    rows = []
    for code, r in ind.items():
        close = base._arr(lk[code]["close"])
        dates = lk[code]["dates"]
        if len(close) != len(dates) or len(close) <= WARMUP + 2:
            continue
        bh, _ = base.backtest_bh(close, dates, code)
        m, eq, tr = base.backtest(close, fn(close, r), dates, code)
        rows.append({"code": code, "m": m, "bh": bh, "trades": tr})
    return base._agg(rows)


def _row(label, a):
    return (f"| {label} | {base.pct(a['ann'])} | {base.pct(a['dd'])} | {a['sharpe']:.2f} | "
            f"{a['exposure']:.0%} | {a['beat_ret']:.0%} | {a['beat_dd']:.0%} | "
            f"{a['pooled']['n']} | {a['pooled']['profit_factor']:.2f} |")


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
    colors = {"买入持有": "#888", "Regime切换": "#111", "v3突破": "#e56", "v4抄底": "#b50", "基线MACD": "#2a2"}
    for kk in ["买入持有", "基线MACD", "v3突破", "v4抄底", "Regime切换"]:
        if kk in curves:
            ax.plot(x, curves[kk], label=kk, color=colors.get(kk), linewidth=1.5)
    ax.set_title("510300 沪深300ETF · Regime切换 vs v3/v4 vs 买入持有（净值, 起=1）")
    ax.set_ylabel("净值")
    ax.grid(alpha=0.3)
    ax.legend()
    fig.tight_layout()
    fig.savefig(path, dpi=120)
    plt.close(fig)
    print("图:", path)


def main():
    ind, lk = base.load()
    rows = {"Regime切换": [], "v3突破": [], "v4抄底": []}
    baserows = {s: [] for s in base.STRAT}
    curves, dates_510300 = {}, None

    for code, r in ind.items():
        close = base._arr(lk[code]["close"])
        dates = lk[code]["dates"]
        if len(close) != len(dates) or len(close) <= WARMUP + 2:
            continue
        bh, bh_eq = base.backtest_bh(close, dates, code)
        for name, fn in (("Regime切换", sig_regime), ("v3突破", sig_v3), ("v4抄底", sig_v4)):
            m, eq, tr = base.backtest(close, fn(close, r), dates, code)
            rows[name].append({"code": code, "m": m, "bh": bh, "trades": tr})
            if code == "510300":
                curves[name] = eq
        sigs = base.build_signals(close, r)
        for s in base.STRAT:
            m5, eq5, tr5 = base.backtest(close, sigs[s], dates, code)
            baserows[s].append({"code": code, "m": m5, "bh": bh, "trades": tr5})
            if code == "510300" and s == "MACD":
                curves["基线MACD"] = eq5
        if code == "510300":
            dates_510300 = dates[WARMUP:]
            curves["买入持有"] = bh_eq

    os.makedirs(OUTDIR, exist_ok=True)
    with open(os.path.join(OUTDIR, "_v5_regime_switch_results.jsonl"), "w", encoding="utf-8") as f:
        for row in rows["Regime切换"]:
            f.write(json.dumps({
                "code": row["code"], "strategy": VARIANT,
                "ann": round(row["m"]["ann"], 4), "dd": round(row["m"]["dd"], 4),
                "sharpe": round(row["m"]["sharpe"], 3), "exposure": round(row["m"]["exposure"], 3),
                "n_trades": row["m"]["n"], "win_rate": round(row["m"]["win_rate"], 4),
                "bh_ann": round(row["bh"]["ann"], 4), "bh_dd": round(row["bh"]["dd"], 4),
            }, ensure_ascii=False) + "\n")

    longset = {c for c in ind if lk[c]["dates"][0] <= COHORT_2015}
    a = {k: base._agg(v) for k, v in rows.items()}
    ca = {k: base._agg([x for x in v if x["code"] in longset]) for k, v in rows.items()}
    ab = {s: base._agg(baserows[s]) for s in base.STRAT}
    cab = {s: base._agg([x for x in baserows[s] if x["code"] in longset]) for s in base.STRAT}
    bh_ann = np.median([x["bh"]["ann"] for x in rows[VARIANT]])
    bh_dd = np.median([x["bh"]["dd"] for x in rows[VARIANT]])
    bh_sh = np.median([x["bh"]["sharpe"] for x in rows[VARIANT]])
    sub = [x for x in rows[VARIANT] if x["code"] in longset]
    c_bh_ann, c_bh_dd = np.median([x["bh"]["ann"] for x in sub]), np.median([x["bh"]["dd"] for x in sub])
    c_bh_sh = np.median([x["bh"]["sharpe"] for x in sub])

    L = [
        "# 技术指标策略 · 变种 5：Regime 切换（趋势用突破 / 震荡用抄底）· 全池回测",
        "> 55 只场内 ETF · 各自全历史（第 61 个交易日起）· 多头/空仓 0-1 · "
        "信号当日收盘成交 · 未计佣金滑点 · 无风险利率 0",
        "",
        "## 策略规则",
        "",
        f"- **状态判定**：Kaufman 效率比 ER（{ER_N} 日）—— ER≥{ER_TREND} 趋势态、ER≤{ER_RANGE} 震荡态，其间滞回保持。",
        f"- **{DESC_TREND}**",
        f"- **{DESC_RANGE}**",
        "",
        "## 全池汇总（中位数 · 55 只）",
        "",
        "| 策略 | 中位年化 | 中位最大回撤 | 中位夏普 | 中位曝光 | 跑赢买入持有 | 回撤更小 | 交易 | 利润因子 |",
        "|---|---|---|---|---|---|---|---|---|",
        _row(f"**{VARIANT}（变种）**", a[VARIANT]),
        _row("v3 突破（参考）", a["v3突破"]),
        _row("v4 抄底（参考）", a["v4抄底"]),
    ]
    for s in base.STRAT:
        L.append(_row(s, ab[s]))
    L.append(f"| 买入持有(基准) | {base.pct(bh_ann)} | {base.pct(bh_dd)} | {bh_sh:.2f} | 100% | — | — | 1 | — |")

    L += [
        "",
        f"## 长历史子样本（2015 起 · {len(sub)} 只 · 约 10.7 年）",
        "",
        "| 策略 | 中位年化 | 中位最大回撤 | 中位夏普 | 中位曝光 | 跑赢买入持有 | 回撤更小 | 交易 | 利润因子 |",
        "|---|---|---|---|---|---|---|---|---|",
        _row(f"**{VARIANT}（变种）**", ca[VARIANT]),
        _row("v3 突破（参考）", ca["v3突破"]),
        _row("v4 抄底（参考）", ca["v4抄底"]),
    ]
    for s in base.STRAT:
        L.append(_row(s, cab[s]))
    L.append(f"| 买入持有(基准) | {base.pct(c_bh_ann)} | {base.pct(c_bh_dd)} | {c_bh_sh:.2f} | 100% | — | — | 1 | — |")

    sens = [
        ("0.40 / 0.30（默认）", lambda c, r: sig_regime(c, r, er_trend=0.40, er_range=0.30)),
        ("0.50 / 0.40", lambda c, r: sig_regime(c, r, er_trend=0.50, er_range=0.40)),
        ("0.30 / 0.20", lambda c, r: sig_regime(c, r, er_trend=0.30, er_range=0.20)),
        ("趋势用 v3 / 震荡空仓", sig_trend_only),
    ]
    L += ["", "## ER 阈值敏感性（全池中位数）", "",
          "| 配置 | 中位年化 | 中位最大回撤 | 中位夏普 | 曝光 | 跑赢买入持有 | 交易 | 利润因子 |",
          "|---|---|---|---|---|---|---|---|"]
    for label, fn in sens:
        L.append(_row(label, _run_cfg(ind, lk, fn)))
    L += ["",
          f"> 对照：纯 v3 夏普 {a['v3突破']['sharpe']:.2f} / 利润因子 {a['v3突破']['pooled']['profit_factor']:.2f}"
          f"（回撤 {a['v3突破']['dd'] * 100:.0f}%）；"
          f"纯 v4 夏普 {a['v4抄底']['sharpe']:.2f} / 利润因子 {a['v4抄底']['pooled']['profit_factor']:.2f}"
          f"（回撤 {a['v4抄底']['dd'] * 100:.0f}%）。",
          "> **结论：ER 切换未能跑赢其组件** —— 切换继承了 v4 的高回撤，又丢掉了 v3 的干净离场；"
          "「震荡空仓」虽把回撤降到 -15%，但收益代价过大。说明 ER 这类滞后状态指标切换会带来边界反复。",
          ]

    L += ["", "## 代表标的：510300 沪深300ETF（2015-01-05 起）", "",
          "| 口径 | 年化 | 最大回撤 | 夏普 | 总收益 | 卡玛比率 | 交易 | 胜率 | 盈亏比 |",
          "|---|---|---|---|---|---|---|---|---|"]
    bh510 = {x["code"]: x for x in rows[VARIANT]}["510300"]["bh"]
    L.append(f"| 买入持有 | {base.pct(bh510['ann'])} | {base.pct(bh510['dd'])} | {bh510['sharpe']:.2f} | "
             f"{base.pct(bh510['total'])} | {bh510['calmar']:.2f} | 1 | — | — |")
    for name in [VARIANT, "v3突破", "v4抄底"]:
        m = {x["code"]: x for x in rows[name]}["510300"]["m"]
        L.append(f"| {name} | {base.pct(m['ann'])} | {base.pct(m['dd'])} | {m['sharpe']:.2f} | "
                 f"{base.pct(m['total'])} | {m['calmar']:.2f} | {m['n']} | "
                 f"{base.pct(m['win_rate'])} | {m['payoff']:.2f} |")

    pooled = base.trade_stats([t for x in rows[VARIANT] for t in x["trades"]])
    L += base.section_lines(pooled, title=f"单笔交易统计（全池合并 · {VARIANT}）")
    L += ["", "> 口径：单笔=建/平仓收盘价收益（未计费用）。**模拟结果，非投资建议。**"]

    with open(os.path.join(OUTDIR, "_v5_regime_switch_report.md"), "w", encoding="utf-8") as f:
        f.write("\n".join(L))
    _plot(dates_510300, curves, os.path.join(OUTDIR, "_v5_regime_switch_510300.png"))
    print("报告:", os.path.join(OUTDIR, "_v5_regime_switch_report.md"))


if __name__ == "__main__":
    main()
