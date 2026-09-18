# -*- coding: utf-8 -*-
"""技术指标五大策略（MA / VOL / MACD / KDJ / BOLL）· 全池回测。

标的池：data/_indicators_hist.json 覆盖的 55 只场内 ETF。
行情源：data/_indicators_hist.json（MA/MACD/KDJ/BOLL/VOL） + data/_long_klines.json（市价收盘价）。

口径：
- 目标仓位 0/1（多头/空仓），信号当日收盘成交，收盘价对收盘价计收益；
- 信号仅用当日及之前的指标（无未来函数）；
- 未计佣金 / 滑点；无风险利率按 0 计；年化按 252 交易日；
- 每只标的从其自身可用区间的第 61 个交易日（MA60 预热完成）起回测。

产物（相对本文件）：
- backtest/_tech_indicators_report.md
- backtest/_tech_indicators_results.jsonl
- backtest/_tech_indicators_510300.png

模拟结果，非投资建议。
"""
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
_ROOT = os.path.dirname(os.path.dirname(_HERE))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

from pipeline.bt_stats import trade_stats, section_lines, pct  # noqa: E402

DATA = os.path.join(_ROOT, "data")
OUTDIR = os.path.join(_HERE, "backtest")
ANN = 252
RF = 0.0
WARMUP = 60
COHORT_2015 = "2015-01-06"

STRAT = ["MA", "VOL", "MACD", "KDJ", "BOLL"]
STRAT_DESC = {
    "MA": "收盘上穿 MA20(生命线) 且价 > MA60(牛熊线) 买入；收盘下穿 MA20 卖出",
    "VOL": "放量上涨买入；放量下跌或缩量上涨卖出",
    "MACD": "DIF 上穿 DEA（金叉）买入；DIF 下穿 DEA（死叉）卖出",
    "KDJ": "K 上穿 D 且 K < 20（超卖）买入；K 下穿 D 且 K > 80（超买）卖出",
    "BOLL": "收盘触及/跌破下轨买入；收盘触及/升破上轨卖出",
}


def _arr(x):
    return np.array([np.nan if v is None else float(v) for v in x], dtype=float)


def load():
    ind = json.load(open(os.path.join(DATA, "_indicators_hist.json"), encoding="utf-8"))
    ind.pop("_meta", None)
    lk = json.load(open(os.path.join(DATA, "_long_klines.json"), encoding="utf-8"))
    return ind, lk


def build_signals(close, r):
    """按 5 条规则生成目标仓位序列（0/1，逐日因果）。"""
    n = len(close)
    ma20, ma60 = _arr(r["MA20"]), _arr(r["MA60"])
    dif, dea = _arr(r["DIF"]), _arr(r["DEA"])
    k, d = _arr(r["K"]), _arr(r["D"])
    upper, lower = _arr(r["BOLL_UPPER"]), _arr(r["BOLL_LOWER"])
    vol = _arr(r["volume"])
    vol_ma5 = pd.Series(vol).rolling(5).mean().to_numpy()
    sigs = {s: np.zeros(n, dtype=int) for s in STRAT}

    p = 0
    for t in range(1, n):
        if np.isnan(ma20[t]) or np.isnan(ma60[t]) or np.isnan(ma20[t - 1]):
            sigs["MA"][t] = p
            continue
        if p == 0 and close[t] > ma20[t] and close[t - 1] <= ma20[t - 1] and close[t] > ma60[t]:
            p = 1
        elif p == 1 and close[t] < ma20[t] and close[t - 1] >= ma20[t - 1]:
            p = 0
        sigs["MA"][t] = p

    p = 0
    for t in range(1, n):
        if np.isnan(vol[t]) or np.isnan(vol_ma5[t]):
            sigs["VOL"][t] = p
            continue
        up = close[t] > close[t - 1]
        dn = close[t] < close[t - 1]
        vup = vol[t] > vol_ma5[t]
        if p == 0 and up and vup:
            p = 1
        elif p == 1 and ((dn and vup) or (up and not vup)):
            p = 0
        sigs["VOL"][t] = p

    p = 0
    for t in range(1, n):
        golden = dif[t] > dea[t] and dif[t - 1] <= dea[t - 1]
        death = dif[t] < dea[t] and dif[t - 1] >= dea[t - 1]
        if p == 0 and golden:
            p = 1
        elif p == 1 and death:
            p = 0
        sigs["MACD"][t] = p

    p = 0
    for t in range(1, n):
        if np.isnan(k[t]) or np.isnan(d[t]) or np.isnan(k[t - 1]) or np.isnan(d[t - 1]):
            sigs["KDJ"][t] = p
            continue
        golden = k[t] > d[t] and k[t - 1] <= d[t - 1]
        death = k[t] < d[t] and k[t - 1] >= d[t - 1]
        if p == 0 and golden and k[t] < 20:
            p = 1
        elif p == 1 and death and k[t] > 80:
            p = 0
        sigs["KDJ"][t] = p

    p = 0
    for t in range(n):
        if np.isnan(upper[t]) or np.isnan(lower[t]):
            sigs["BOLL"][t] = p
            continue
        if p == 0 and close[t] <= lower[t]:
            p = 1
        elif p == 1 and close[t] >= upper[t]:
            p = 0
        sigs["BOLL"][t] = p

    return sigs


def _metrics(eq, trades):
    eq = np.asarray(eq, dtype=float)
    n = len(eq) - 1
    total = eq[-1] - 1
    ann = eq[-1] ** (ANN / n) - 1 if n > 0 and eq[-1] > 0 else 0.0
    dr = eq[1:] / eq[:-1] - 1
    vol = float(dr.std(ddof=1)) * np.sqrt(ANN) if len(dr) > 1 else 0.0
    sharpe = (ann - RF) / vol if vol > 0 else 0.0
    dd = float((eq / np.maximum.accumulate(eq) - 1).min())
    calmar = ann / abs(dd) if dd < 0 else 0.0
    ts = trade_stats(trades)
    return {"total": float(total), "ann": float(ann), "vol": vol, "sharpe": float(sharpe),
            "dd": dd, "calmar": float(calmar), "n": ts["n"], "win_rate": ts["win_rate"],
            "avg_win": ts["avg_win"], "avg_loss": ts["avg_loss"],
            "payoff": ts["payoff"], "profit_factor": ts["profit_factor"]}


def _trades_log(sig, close, dates, code, s):
    n = len(close)
    trades, entry = [], None
    for t in range(s, n):
        if entry is None and sig[t] == 1:
            entry = t
        elif entry is not None and sig[t] == 0:
            trades.append({"code": code, "entry_date": dates[entry], "exit_date": dates[t],
                           "bars": t - entry, "ret": close[t] / close[entry] - 1})
            entry = None
    if entry is not None:
        trades.append({"code": code, "entry_date": dates[entry], "exit_date": dates[-1],
                       "bars": (n - 1) - entry, "ret": close[-1] / close[entry] - 1})
    return trades


def backtest(close, sig, dates, code, s=WARMUP):
    n = len(close)
    eq = [1.0]
    for t in range(s + 1, n):
        r = close[t] / close[t - 1] - 1
        eq.append(eq[-1] * (1 + sig[t - 1] * r))
    trades = _trades_log(sig, close, dates, code, s)
    m = _metrics(eq, trades)
    m["exposure"] = float(np.mean(sig[s:n]))
    return m, eq, trades


def backtest_bh(close, dates, code, s=WARMUP):
    n = len(close)
    eq = [close[t] / close[s] for t in range(s, n)]
    trades = [{"code": code, "entry_date": dates[s], "exit_date": dates[-1],
               "bars": (n - 1) - s, "ret": close[-1] / close[s] - 1}]
    m = _metrics(eq, trades)
    m["exposure"] = 1.0
    return m, eq


def _agg(rows):
    ann = np.array([r["m"]["ann"] for r in rows])
    dd = np.array([r["m"]["dd"] for r in rows])
    sh = np.array([r["m"]["sharpe"] for r in rows])
    exp = np.array([r["m"]["exposure"] for r in rows])
    beat_ret = np.mean([r["m"]["ann"] > r["bh"]["ann"] for r in rows])
    beat_dd = np.mean([abs(r["m"]["dd"]) < abs(r["bh"]["dd"]) for r in rows])
    pooled = trade_stats([t for r in rows for t in r["trades"]])
    return {"ann": float(np.median(ann)), "dd": float(np.median(dd)),
            "sharpe": float(np.median(sh)), "exposure": float(np.median(exp)),
            "beat_ret": float(beat_ret), "beat_dd": float(beat_dd),
            "pooled": pooled, "n_funds": len(rows)}


def _md(strat_rows):
    """strat_rows: {strat: rows} -> md 汇总行。"""
    L = []
    for s in STRAT:
        a = _agg(strat_rows[s])
        L.append(f"| {s} | {pct(a['ann'])} | {pct(a['dd'])} | {a['sharpe']:.2f} | "
                 f"{a['exposure']:.0%} | {a['beat_ret']:.0%} | {a['beat_dd']:.0%} | "
                 f"{a['pooled']['n']} | {pct(a['pooled']['win_rate'])} |")
    return L


def main():
    ind, lk = load()
    results = {s: [] for s in STRAT}
    equity_510300 = None
    dates_510300 = None

    for code, r in ind.items():
        if code not in lk:
            continue
        close = _arr(lk[code]["close"])
        dates = lk[code]["dates"]
        if len(close) != len(dates) or len(close) <= WARMUP + 2:
            continue
        sigs = build_signals(close, r)
        bh, bh_eq = backtest_bh(close, dates, code)

        for s in STRAT:
            m, eq, trades = backtest(close, sigs[s], dates, code)
            results[s].append({"code": code, "m": m, "bh": bh, "trades": trades})
            if code == "510300":
                equity_510300 = equity_510300 or {}
                equity_510300[s] = eq

        if code == "510300":
            dates_510300 = dates[WARMUP:]
            equity_510300["BH"] = bh_eq

    os.makedirs(OUTDIR, exist_ok=True)

    # JSONL 逐基金逐策略
    with open(os.path.join(OUTDIR, "_tech_indicators_results.jsonl"), "w", encoding="utf-8") as f:
        for s in STRAT:
            for row in results[s]:
                f.write(json.dumps({
                    "code": row["code"], "strategy": s,
                    "ann": round(row["m"]["ann"], 4), "dd": round(row["m"]["dd"], 4),
                    "sharpe": round(row["m"]["sharpe"], 3), "exposure": round(row["m"]["exposure"], 3),
                    "n_trades": row["m"]["n"], "win_rate": round(row["m"]["win_rate"], 4),
                    "bh_ann": round(row["bh"]["ann"], 4), "bh_dd": round(row["bh"]["dd"], 4),
                }, ensure_ascii=False) + "\n")

    # 长历史子样本
    cohort = {s: [row for row in results[s] if row["code"] in
                  {c for c in ind if lk[c]["dates"][0] <= COHORT_2015}] for s in STRAT}

    L = [
        "# 技术指标五大策略 · 全池回测",
        f"> 55 只场内 ETF · 各自全历史（第 61 个交易日起）· 多头/空仓 0-1 · "
        f"信号当日收盘成交 · 未计佣金滑点 · 无风险利率 0",
        "",
        "## 策略规则",
        "",
        "| 策略 | 规则 |",
        "|---|---|",
    ]
    for s in STRAT:
        L.append(f"| {s} | {STRAT_DESC[s]} |")

    L += [
        "",
        "## 全池汇总（中位数 · 55 只）",
        "",
        "| 策略 | 中位年化 | 中位最大回撤 | 中位夏普 | 中位曝光 | 跑赢买入持有 | 回撤更小 | 总交易 | 合计胜率 |",
        "|---|---|---|---|---|---|---|---|---|",
    ]
    L += _md(results)
    bh_ann = np.median([r["bh"]["ann"] for r in results["MA"]])
    bh_dd = np.median([r["bh"]["dd"] for r in results["MA"]])
    bh_sh = np.median([r["bh"]["sharpe"] for r in results["MA"]])
    L.append(f"| 买入持有(基准) | {pct(bh_ann)} | {pct(bh_dd)} | {bh_sh:.2f} | 100% | — | — | 1 | — |")

    L += [
        "",
        f"## 长历史子样本（2015 起 · {len(cohort['MA'])} 只 · 约 10.7 年）",
        "",
        "| 策略 | 中位年化 | 中位最大回撤 | 中位夏普 | 中位曝光 | 跑赢买入持有 | 回撤更小 | 总交易 | 合计胜率 |",
        "|---|---|---|---|---|---|---|---|---|",
    ]
    L += _md(cohort)
    c_bh_ann = np.median([r["bh"]["ann"] for r in cohort["MA"]])
    c_bh_dd = np.median([r["bh"]["dd"] for r in cohort["MA"]])
    c_bh_sh = np.median([r["bh"]["sharpe"] for r in cohort["MA"]])
    L.append(f"| 买入持有(基准) | {pct(c_bh_ann)} | {pct(c_bh_dd)} | {c_bh_sh:.2f} | 100% | — | — | 1 | — |")

    # 代表标的 510300
    L += ["", "## 代表标的：510300 沪深300ETF（2015-01-05 起）", "",
          "| 口径 | 年化 | 最大回撤 | 夏普 | 总收益 | 卡玛比率 | 交易 | 胜率 | 盈亏比 |",
          "|---|---|---|---|---|---|---|---|---|"]
    row_map = {r["code"]: r for r in results["MA"]}
    bh = row_map["510300"]["bh"]
    L.append(f"| 买入持有 | {pct(bh['ann'])} | {pct(bh['dd'])} | {bh['sharpe']:.2f} | "
             f"{pct(bh['total'])} | {bh['calmar']:.2f} | 1 | — | — |")
    for s in STRAT:
        m = {r["code"]: r for r in results[s]}["510300"]["m"]
        L.append(f"| {s} | {pct(m['ann'])} | {pct(m['dd'])} | {m['sharpe']:.2f} | "
                 f"{pct(m['total'])} | {m['calmar']:.2f} | {m['n']} | {pct(m['win_rate'])} | {m['payoff']:.2f} |")

    # 全池分策略合并单笔统计
    for s in STRAT:
        pooled = trade_stats([t for r in results[s] for t in r["trades"]])
        L += section_lines(pooled, title=f"单笔交易统计（全池合并 · {s}）")

    L += ["",
          "> 口径：单笔=建/平仓收盘价收益（未计费用）；胜率=盈利笔数/总笔数；盈亏比=平均盈利/|平均亏损|；"
          "利润因子=总盈利/|总亏损|。夏普按无风险利率 0 计。**模拟结果，非投资建议。**"]

    with open(os.path.join(OUTDIR, "_tech_indicators_report.md"), "w", encoding="utf-8") as f:
        f.write("\n".join(L))

    _plot_510300(dates_510300, equity_510300,
                 os.path.join(OUTDIR, "_tech_indicators_510300.png"))

    print("报告:", os.path.join(OUTDIR, "_tech_indicators_report.md"))
    print("结果:", os.path.join(OUTDIR, "_tech_indicators_results.jsonl"))


def _plot_510300(dates, curves, path):
    try:
        import matplotlib
        matplotlib.use("Agg")
        import matplotlib.pyplot as plt
    except Exception as e:  # noqa
        print("matplotlib 不可用, 跳过作图:", e)
        return
    plt.rcParams["font.sans-serif"] = ["Microsoft YaHei", "SimHei", "DejaVu Sans"]
    plt.rcParams["axes.unicode_minus"] = False
    x = pd.to_datetime(dates)
    fig, ax = plt.subplots(figsize=(12, 6))
    colors = {"BH": "#888", "MA": "#d33", "VOL": "#e90", "MACD": "#2a2",
              "KDJ": "#27c", "BOLL": "#93c"}
    for k in ["BH"] + STRAT:
        if k in curves:
            ax.plot(x, curves[k], label=("买入持有" if k == "BH" else k),
                    color=colors.get(k), linewidth=1.4)
    ax.set_title("510300 沪深300ETF · 五大指标策略 vs 买入持有（净值, 起=1）")
    ax.set_ylabel("净值")
    ax.grid(alpha=0.3)
    ax.legend()
    fig.tight_layout()
    fig.savefig(path, dpi=120)
    plt.close(fig)
    print("图:", path)


if __name__ == "__main__":
    main()
