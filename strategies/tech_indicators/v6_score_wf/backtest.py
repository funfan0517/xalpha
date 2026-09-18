# -*- coding: utf-8 -*-
"""技术指标策略 · 变种 6：打分制 · Walk-Forward 样本外验证 · 全池回测。

打分制（多指标共振）：7 项看多票数（价>MA20、价>MA60、DIF>DEA、DIF>0、K>D、价>BOLL中轨、放量上涨）
>= enter 进场，<= exit 离场（滞回）。为检验其全样本高收益是否过拟合，做锚定式滚动 Walk-Forward：

- 参数网格：enter∈{4,5,6} × exit∈{1,2,3}（9 组）。
- 测试年 2019..2026；训练窗口 = 该测试年之前的全部历史（锚定式扩张）。
- 选择指标 = 全池「中位夏普」（仅用训练期数据），取最优参数固定用于紧接的测试年。
- 把逐年测试结果拼成连续样本外（OOS）净值，与「全样本最优固定参数」「基线 MACD」「买入持有」对照。

本脚本**复用基线** ../../backtest.py 的引擎（只读导入，不修改基线文件）；
产物落在本目录 backtest/ 下。模拟结果，非投资建议。
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

VARIANT = "打分制(WF)"
PARAMS = [(e, x) for e in (4, 5, 6) for x in (1, 2, 3)]   # (enter, exit_th)
TEST_YEARS = list(range(2019, 2027))
OOS_START = "2019-01-01"
DESC = ("7 项看多票数(价>MA20、价>MA60、DIF>DEA、DIF>0、K>D、价>BOLL中轨、放量上涨)"
        ">= enter 买、<= exit 卖(滞回)")


def build_score(close, r, enter=5, exit_th=2):
    n = len(close)
    ma20, ma60 = base._arr(r["MA20"]), base._arr(r["MA60"])
    dif, dea = base._arr(r["DIF"]), base._arr(r["DEA"])
    k, d = base._arr(r["K"]), base._arr(r["D"])
    mid = base._arr(r["BOLL_MID"])
    vol = base._arr(r["volume"])
    vol_ma5 = pd.Series(vol).rolling(5).mean().to_numpy()
    prev = np.concatenate([[np.nan], close[:-1]])
    sig = np.zeros(n, dtype=int)
    p = 0
    for t in range(1, n):
        if any(np.isnan(x) for x in (ma20[t], ma60[t], dif[t], dea[t], k[t], d[t], mid[t], vol_ma5[t])):
            sig[t] = p
            continue
        score = (int(close[t] > ma20[t]) + int(close[t] > ma60[t]) + int(dif[t] > dea[t])
                 + int(dif[t] > 0) + int(k[t] > d[t]) + int(close[t] > mid[t])
                 + int(vol[t] > vol_ma5[t] and close[t] > prev[t]))
        if p == 0 and score >= enter:
            p = 1
        elif p == 1 and score <= exit_th:
            p = 0
        sig[t] = p
    return sig


def _idx_ge(dates, d):
    for i, dt in enumerate(dates):
        if dt >= d:
            return i
    return len(dates)


def _seg_sharpe(eq_full, i0, i1):
    """eq_full 从第 WARMUP 日起(索引 k <-> 日期索引 WARMUP+k); 取 [i0,i1] 窗口夏普。"""
    k0, k1 = i0 - WARMUP, min(i1 - WARMUP, len(eq_full) - 1)
    if k1 <= k0:
        return None
    seg = [v / eq_full[k0] for v in eq_full[k0:k1 + 1]]
    return base._metrics(seg, [])["sharpe"]


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
    colors = {"买入持有": "#888", "打分制(WF)": "#111", "固定参数(全样本最优)": "#0a7", "基线MACD": "#2a2"}
    for kk in ["买入持有", "基线MACD", "固定参数(全样本最优)", "打分制(WF)"]:
        if kk in curves:
            ax.plot(x, curves[kk], label=kk, color=colors.get(kk), linewidth=1.5)
    ax.set_title("510300 沪深300ETF · 打分制 Walk-Forward vs 固定参数 vs 买入持有（OOS 2019 起, 净值=1）")
    ax.set_ylabel("净值")
    ax.grid(alpha=0.3)
    ax.legend()
    fig.tight_layout()
    fig.savefig(path, dpi=120)
    plt.close(fig)
    print("图:", path)


def main():
    ind, lk = base.load()
    # 预计算：每只标的、每个参数的全历史信号与净值
    store = {}
    for code, r in ind.items():
        close = base._arr(lk[code]["close"])
        dates = lk[code]["dates"]
        if len(close) != len(dates) or len(close) <= WARMUP + 2:
            continue
        sigs, eqs = {}, {}
        for pr in PARAMS:
            s = build_score(close, r, pr[0], pr[1])
            sigs[pr] = s
            eqs[pr] = base.backtest(close, s, dates, code)[1]
        store[code] = {"close": close, "dates": dates, "sigs": sigs, "eqs": eqs}

    # 逐年选择参数（仅用训练期 = 该年之前全部历史）
    chosen, fold_rows = {}, []
    for y in TEST_YEARS:
        med = {}
        for pr in PARAMS:
            sh = []
            for code, st in store.items():
                i0, i1 = WARMUP, _idx_ge(st["dates"], f"{y}-01-01") - 1
                v = _seg_sharpe(st["eqs"][pr], i0, i1)
                if v is not None:
                    sh.append(v)
            med[pr] = float(np.median(sh)) if sh else -9.0
        best = max(PARAMS, key=lambda pr: med[pr])
        chosen[y] = best
        fold_rows.append((y, best, med[best]))

    # 全样本最优参数（参考，含训练期，乐观口径）
    full_med = {}
    for pr in PARAMS:
        full_med[pr] = float(np.median([base._metrics(st["eqs"][pr], [])["sharpe"] for st in store.values()]))
    best_param = max(PARAMS, key=lambda pr: full_med[pr])

    # 样本外（OOS）拼接
    wf_rows, fixed_rows, macd_rows = [], [], []
    curves, dates_510300 = {}, None
    for code, st in store.items():
        close, dates = st["close"], st["dates"]
        oos0 = _idx_ge(dates, OOS_START)
        if oos0 >= len(dates) - 2:
            continue
        sig = np.zeros(len(dates), dtype=int)
        for t in range(len(dates)):
            pr = chosen.get(int(dates[t][:4]))
            if pr is not None:
                sig[t] = st["sigs"][pr][t]
        m, eq, tr = base.backtest(close, sig, dates, code, s=oos0)
        bh, bh_eq = base.backtest_bh(close, dates, code, s=oos0)
        wf_rows.append({"code": code, "m": m, "bh": bh, "trades": tr})

        mf, eqf, trf = base.backtest(close, st["sigs"][best_param], dates, code, s=oos0)
        fixed_rows.append({"code": code, "m": mf, "bh": bh, "trades": trf})

        if code == "510300":
            dates_510300 = dates[oos0:]
            curves["打分制(WF)"] = eq
            curves["固定参数(全样本最优)"] = eqf
            curves["买入持有"] = bh_eq

    # 基线 MACD 在 OOS 上的对照（需要用原始指标重建信号）
    ind2, lk2 = ind, lk
    for code, r in ind2.items():
        if code not in store:
            continue
        close, dates = store[code]["close"], store[code]["dates"]
        oos0 = _idx_ge(dates, OOS_START)
        if oos0 >= len(dates) - 2:
            continue
        sig = base.build_signals(close, r)["MACD"]
        m, eq, tr = base.backtest(close, sig, dates, code, s=oos0)
        bh, _ = base.backtest_bh(close, dates, code, s=oos0)
        macd_rows.append({"code": code, "m": m, "bh": bh, "trades": tr})
        if code == "510300":
            curves["基线MACD"] = eq

    os.makedirs(OUTDIR, exist_ok=True)
    with open(os.path.join(OUTDIR, "_v6_score_wf_results.jsonl"), "w", encoding="utf-8") as f:
        for row in wf_rows:
            f.write(json.dumps({
                "code": row["code"], "strategy": VARIANT,
                "ann": round(row["m"]["ann"], 4), "dd": round(row["m"]["dd"], 4),
                "sharpe": round(row["m"]["sharpe"], 3), "exposure": round(row["m"]["exposure"], 3),
                "n_trades": row["m"]["n"], "win_rate": round(row["m"]["win_rate"], 4),
                "bh_ann": round(row["bh"]["ann"], 4), "bh_dd": round(row["bh"]["dd"], 4),
            }, ensure_ascii=False) + "\n")

    a_wf, a_fx, a_macd = base._agg(wf_rows), base._agg(fixed_rows), base._agg(macd_rows)
    longset = {c for c in store if store[c]["dates"][0] <= COHORT_2015}
    c_wf = base._agg([x for x in wf_rows if x["code"] in longset])
    c_fx = base._agg([x for x in fixed_rows if x["code"] in longset])
    bh_ann = np.median([x["bh"]["ann"] for x in wf_rows])
    bh_dd = np.median([x["bh"]["dd"] for x in wf_rows])
    bh_sh = np.median([x["bh"]["sharpe"] for x in wf_rows])

    L = [
        "# 技术指标策略 · 变种 6：打分制 · Walk-Forward 样本外验证",
        "> 55 只场内 ETF · 多头/空仓 0-1 · 信号当日收盘成交 · 未计佣金滑点 · 无风险利率 0",
        "",
        "## 策略与验证方法",
        "",
        f"- **打分制**：{DESC}。",
        f"- **参数网格**：enter∈{{4,5,6}} × exit∈{{1,2,3}}（9 组）。",
        f"- **锚定式滚动**：测试年 {TEST_YEARS[0]}..{TEST_YEARS[-1]}，训练=该年之前全部历史；选择指标=全池中位夏普（仅用训练期）。",
        "- **样本外拼接**：逐年用训练期选出、其后固定不变的参数，拼成连续 OOS 净值。",
        "",
        "## 逐年参数选择",
        "",
        "| 测试年 | 选中参数 (进/退) | 训练中位夏普 |",
        "|---|---|---|",
    ]
    for y, pr, sc in fold_rows:
        L.append(f"| {y} | {pr[0]} / {pr[1]} | {sc:.2f} |")

    L += [
        "",
        f"## 样本外汇总（OOS {OOS_START} 起 · 中位数 · {len(wf_rows)} 只）",
        "",
        "| 口径 | 中位年化 | 中位最大回撤 | 中位夏普 | 中位曝光 | 跑赢买入持有 | 回撤更小 | 交易 | 利润因子 |",
        "|---|---|---|---|---|---|---|---|---|",
        _row(f"**{VARIANT}（样本外）**", a_wf),
        _row(f"固定参数 {best_param[0]}/{best_param[1]}（全样本最优, OOS）", a_fx),
        _row("基线MACD（OOS）", a_macd),
        f"| 买入持有（OOS） | {base.pct(bh_ann)} | {base.pct(bh_dd)} | {bh_sh:.2f} | 100% | — | — | 1 | — |",
        "",
        f"## 长历史子样本（2015 起 · {len([x for x in wf_rows if x['code'] in longset])} 只 · 中位数）",
        "",
        "| 口径 | 中位年化 | 中位最大回撤 | 中位夏普 | 中位曝光 | 跑赢买入持有 | 回撤更小 | 交易 | 利润因子 |",
        "|---|---|---|---|---|---|---|---|---|",
        _row(f"**{VARIANT}（样本外）**", c_wf),
        _row(f"固定参数 {best_param[0]}/{best_param[1]}（OOS）", c_fx),
    ]

    L += ["", "## 代表标的：510300 沪深300ETF（OOS 2019 起）", "",
          "| 口径 | 年化 | 最大回撤 | 夏普 | 总收益 | 卡玛比率 | 交易 | 胜率 | 盈亏比 |",
          "|---|---|---|---|---|---|---|---|---|"]
    m510 = {x["code"]: x for x in wf_rows}["510300"]
    mf510 = {x["code"]: x for x in fixed_rows}["510300"]
    mm510 = {x["code"]: x for x in macd_rows}["510300"]
    L.append(f"| 买入持有 | {base.pct(m510['bh']['ann'])} | {base.pct(m510['bh']['dd'])} | {m510['bh']['sharpe']:.2f} | "
             f"{base.pct(m510['bh']['total'])} | {m510['bh']['calmar']:.2f} | 1 | — | — |")
    for lab, m in ((f"{VARIANT}(WF)", m510["m"]), (f"固定{best_param[0]}/{best_param[1]}", mf510["m"]),
                   ("基线MACD", mm510["m"])):
        L.append(f"| {lab} | {base.pct(m['ann'])} | {base.pct(m['dd'])} | {m['sharpe']:.2f} | "
                 f"{base.pct(m['total'])} | {m['calmar']:.2f} | {m['n']} | {base.pct(m['win_rate'])} | {m['payoff']:.2f} |")

    pooled = base.trade_stats([t for x in wf_rows for t in x["trades"]])
    L += base.section_lines(pooled, title=f"单笔交易统计（OOS 全池合并 · {VARIANT}）")
    L += ["", "> 口径：单笔=建/平仓收盘价收益（未计费用）；无风险利率 0。**模拟结果，非投资建议。**"]

    with open(os.path.join(OUTDIR, "_v6_score_wf_report.md"), "w", encoding="utf-8") as f:
        f.write("\n".join(L))
    _plot(dates_510300, curves, os.path.join(OUTDIR, "_v6_score_wf_510300.png"))
    print("报告:", os.path.join(OUTDIR, "_v6_score_wf_report.md"))
    print("全样本最优参数:", best_param)


if __name__ == "__main__":
    main()
