# -*- coding: utf-8 -*-
"""技术指标策略 · 变体探索：在 v3(突破) / v4(抄底) 基础上叠加止损、趋势过滤、打分制。

一次性对比多个候选规则，找出比现有变种更优的方向。只读复用基线引擎，不改动基线/变种文件。
产物：research/_variant_explore_report.md。模拟结果，非投资建议。
"""
import importlib.util
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

def _load_module(path, name):
    spec = importlib.util.spec_from_file_location(name, path)
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


base = _load_module(os.path.join(_PARENT, "backtest.py"), "ti_base")
v6 = _load_module(os.path.join(_PARENT, "v6_score_wf", "backtest.py"), "ti_v6")

OUTDIR = _HERE
COHORT_2015 = base.COHORT_2015
WARMUP = base.WARMUP

# 打分制统一复用 v6 的 4 票实现（单一事实来源），阈值取 v6 网格最优
SCORE_E, SCORE_X = v6.DEFAULT_PARAM
SCORE_BEST = f"打分制(≥{SCORE_E}/≤{SCORE_X} 网格最优)"


# ------------------------------- 候选信号 ------------------------------- #
def build_v3(close, r, ma_filter=False, stop=None):
    """MACD 零轴上金叉 + 布林开口突破; 死叉/下穿中轨离场。可选 MA60 过滤、固定止损。"""
    n = len(close)
    dif, dea = base._arr(r["DIF"]), base._arr(r["DEA"])
    upper, mid, lower = base._arr(r["BOLL_UPPER"]), base._arr(r["BOLL_MID"]), base._arr(r["BOLL_LOWER"])
    ma60 = base._arr(r["MA60"])
    bw = (upper - lower) / mid
    sig = np.zeros(n, dtype=int)
    p, entry = 0, None
    for t in range(1, n):
        need = (dif[t], dea[t], upper[t], mid[t], lower[t], upper[t - 1], mid[t - 1], bw[t - 1])
        if any(np.isnan(x) for x in need):
            sig[t] = p
            continue
        if p == 0:
            ok_trend = (not ma_filter) or (not np.isnan(ma60[t]) and close[t] > ma60[t])
            if dif[t] > 0 and dif[t] > dea[t] and bw[t] > bw[t - 1] and \
                    close[t] > upper[t] and close[t - 1] <= upper[t - 1] and ok_trend:
                p, entry = 1, close[t]
        else:
            death = dif[t] < dea[t] and dif[t - 1] >= dea[t - 1]
            mid_break = close[t] < mid[t] and close[t - 1] >= mid[t - 1]
            stop_hit = stop is not None and close[t] < entry * (1 - stop)
            if death or mid_break or stop_hit:
                p, entry = 0, None
        sig[t] = p
    return sig


def build_v4(close, r, buy_pos=0.20, sell_pos=0.80, kwin=10, res=10, stop=None):
    """黄金三角抄底(近带+MACD金叉+KDJ超卖拐头); 近上轨共振逃顶。可选固定止损。"""
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
    p, entry = 0, None
    for t in range(1, n):
        need = (dif[t], dea[t], k[t], d[t], kmin[t], kmax[t], low_recent[t], high_recent[t])
        if any(np.isnan(x) for x in need):
            sig[t] = p
            continue
        if p == 0:
            if low_recent[t] and dif[t] > dea[t] and k[t] > d[t] and kmin[t] < 20:
                p, entry = 1, close[t]
        else:
            stop_hit = stop is not None and close[t] < entry * (1 - stop)
            if (high_recent[t] and dif[t] < dea[t] and k[t] < d[t] and kmax[t] > 80) or stop_hit:
                p, entry = 0, None
        sig[t] = p
    return sig


def with_stop(sig, close, stop):
    """在给定信号上叠加固定止损（收盘跌破 entry×(1-stop) 即离场，信号再度转多才重进）。"""
    n = len(close)
    out = np.zeros(n, dtype=int)
    p, entry = 0, None
    for t in range(n):
        if p == 0:
            if sig[t] == 1:
                p, entry = 1, close[t]
        elif sig[t] == 0 or close[t] < entry * (1 - stop):
            p, entry = 0, None
        out[t] = p
    return out


def build_union(close, r):
    """v3(突破) 与 v4(抄底) 任一给出多头即持有(并集)。"""
    return np.maximum(build_v3(close, r), build_v4(close, r))


CAND = {
    "v3突破(参考)": lambda c, r: build_v3(c, r),
    "v3+MA60过滤": lambda c, r: build_v3(c, r, ma_filter=True),
    "v3+止损8%": lambda c, r: build_v3(c, r, stop=0.08),
    "v3+MA60+止损8%": lambda c, r: build_v3(c, r, ma_filter=True, stop=0.08),
    "v4抄底(参考)": lambda c, r: build_v4(c, r),
    "v4+止损8%": lambda c, r: build_v4(c, r, stop=0.08),
    "v3∪v4(并集)": lambda c, r: build_union(c, r),
    "打分制(≥2/≤1)": lambda c, r: v6.build_score(c, r, 2, 1),
    "打分制(≥3/≤1)": lambda c, r: v6.build_score(c, r, 3, 1),
    SCORE_BEST: lambda c, r: v6.build_score(c, r),
    "打分制+止损8%": lambda c, r: with_stop(v6.build_score(c, r), c, 0.08),
}


def _row(label, a):
    return (f"| {label} | {base.pct(a['ann'])} | {base.pct(a['dd'])} | {a['sharpe']:.2f} | "
            f"{a['exposure']:.0%} | {a['beat_ret']:.0%} | {a['beat_dd']:.0%} | "
            f"{a['pooled']['n']} | {a['pooled']['profit_factor']:.2f} |")


def main():
    ind, lk = base.load()
    rows = {k: [] for k in CAND}
    macd_rows = []
    curves, dates_510300 = {}, None

    for code, r in ind.items():
        close = base._arr(lk[code]["close"])
        dates = lk[code]["dates"]
        if len(close) != len(dates) or len(close) <= WARMUP + 2:
            continue
        bh, bh_eq = base.backtest_bh(close, dates, code)
        for name, fn in CAND.items():
            m, eq, tr = base.backtest(close, fn(close, r), dates, code)
            rows[name].append({"code": code, "m": m, "bh": bh, "trades": tr})
            if code == "510300":
                curves[name] = eq
        sigs = base.build_signals(close, r)
        m5, eq5, tr5 = base.backtest(close, sigs["MACD"], dates, code)
        macd_rows.append({"code": code, "m": m5, "bh": bh, "trades": tr5})
        if code == "510300":
            dates_510300 = dates[WARMUP:]
            curves["基线MACD"] = eq5
            curves["买入持有"] = bh_eq

    longset = {c for c in ind if lk[c]["dates"][0] <= COHORT_2015}
    L = [
        "# 技术指标策略 · 变体探索（止损 / 趋势过滤 / 打分制）",
        "> 55 只场内 ETF · 各自全历史（第 61 个交易日起）· 多头/空仓 0-1 · "
        "信号当日收盘成交 · 未计佣金滑点 · 无风险利率 0",
        "",
        "## 全池汇总（中位数 · 55 只）",
        "",
        "| 变体 | 中位年化 | 中位最大回撤 | 中位夏普 | 中位曝光 | 跑赢买入持有 | 回撤更小 | 交易 | 利润因子 |",
        "|---|---|---|---|---|---|---|---|---|",
    ]
    for name in CAND:
        L.append(_row(name, base._agg(rows[name])))
    L.append(_row("基线MACD(参考)", base._agg(macd_rows)))
    bh_ann = np.median([x["bh"]["ann"] for x in macd_rows])
    bh_dd = np.median([x["bh"]["dd"] for x in macd_rows])
    bh_sh = np.median([x["bh"]["sharpe"] for x in macd_rows])
    L.append(f"| 买入持有(基准) | {base.pct(bh_ann)} | {base.pct(bh_dd)} | {bh_sh:.2f} | 100% | — | — | 1 | — |")

    L += [
        "",
        f"## 长历史子样本（2015 起 · {len([x for x in macd_rows if x['code'] in longset])} 只）",
        "",
        "| 变体 | 中位年化 | 中位最大回撤 | 中位夏普 | 中位曝光 | 跑赢买入持有 | 回撤更小 | 交易 | 利润因子 |",
        "|---|---|---|---|---|---|---|---|---|",
    ]
    for name in CAND:
        sub = [x for x in rows[name] if x["code"] in longset]
        L.append(_row(name, base._agg(sub)))

    L += ["", "## 代表标的：510300 沪深300ETF（2015-01-05 起）", "",
          "| 变体 | 年化 | 最大回撤 | 夏普 | 总收益 | 交易 | 胜率 | 盈亏比 |",
          "|---|---|---|---|---|---|---|---|"]
    for name in CAND:
        m = {x["code"]: x for x in rows[name]}["510300"]["m"]
        L.append(f"| {name} | {base.pct(m['ann'])} | {base.pct(m['dd'])} | {m['sharpe']:.2f} | "
                 f"{base.pct(m['total'])} | {m['n']} | {base.pct(m['win_rate'])} | {m['payoff']:.2f} |")

    L += ["", "> 口径：止损=收盘跌破 entry×(1-SL) 离场；MA60 过滤=仅在价>MA60 时开仓；"
              "打分制=4 项看多票数（价>MA20且>MA60、DIF>DEA且DIF>0、K>D、放量上涨），"
              f"实现复用 v6_score_wf，阈值取其网格最优 {SCORE_E}/{SCORE_X}。"
              "**模拟结果，非投资建议。**"]

    with open(os.path.join(OUTDIR, "_variant_explore_report.md"), "w", encoding="utf-8") as f:
        f.write("\n".join(L))
    print("报告:", os.path.join(OUTDIR, "_variant_explore_report.md"))

    _plot(dates_510300, curves, os.path.join(OUTDIR, "_variant_explore_510300.png"))


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
    for name in ["买入持有", "基线MACD", "v3突破(参考)", SCORE_BEST, "v3∪v4(并集)"]:
        if name in curves:
            ax.plot(x, curves[name], label=name, linewidth=1.5)
    ax.set_title("510300 沪深300ETF · 变体探索（净值, 起=1）")
    ax.set_ylabel("净值")
    ax.grid(alpha=0.3)
    ax.legend()
    fig.tight_layout()
    fig.savefig(path, dpi=120)
    plt.close(fig)
    print("图:", path)


if __name__ == "__main__":
    main()
