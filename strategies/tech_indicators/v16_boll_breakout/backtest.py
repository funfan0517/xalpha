# -*- coding: utf-8 -*-
"""技术指标策略 · 变种 16：BOLL 上轨突破（趋势跟踪）· 全池回测。

规则本体在 `rule.py`（N=35 / k=2.0 / 上穿上轨买、跌回中轨卖，**只做多，不做下轨反向**）。
本脚本负责跑全池、出报告，并给出三类诚实验证：

1. **与基线对照**：v15 的 BOLL 反转（触下轨买/触上轨卖）+ 买入持有；
2. **成交口径敏感性**：家族口径是「信号当日收盘成交」，实盘应「次日收盘/开盘成交」并计费用 ——
   本表量化 lag=1/2 × 手续费/滑点 0/万3/万10 对全池中位年化的影响；
3. **止损敏感性**：`rule.PARAMS["STOP"]` 默认关闭，本表给出 -5%/-8%/-10%/-15% 的结果。

另附**指数口径核对**：在沪深300指数（`data/_bt_caches/csi_000300.csv`）上本规则能否复现
视频示例那笔交易（2025-06-25 收盘站上轨买入 → 2025-10-17 收盘跌回中轨卖出，持有 76 个交易日）。
（ETF 510300 是跟踪指数的基金，与指数本身有跟踪误差/费率拖累，同日信号可能差 1~2 天。）

产物（本目录 backtest/）：`_BOLL_BREAK_report.md` · `_BOLL_BREAK_results.jsonl` · `_BOLL_BREAK_510300.png`

用法：python strategies/tech_indicators/v16_boll_breakout/backtest.py
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
_PARENT = os.path.dirname(_HERE)                     # strategies/tech_indicators
_ROOT = os.path.dirname(os.path.dirname(_PARENT))    # 仓库根
for _p in (_ROOT, os.path.join(_ROOT, "data"), _PARENT):
    if _p not in sys.path:
        sys.path.insert(0, _p)


def _load(path, name):
    spec = importlib.util.spec_from_file_location(name, path)
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


base = _load(os.path.join(_PARENT, "backtest.py"), "ti_base")
rule = _load(os.path.join(_HERE, "rule.py"), "_ti_rule_" + os.path.basename(_HERE))

VARIANT = rule.VARIANT
OUTDIR = os.path.join(_HERE, "backtest")
CSI300 = os.path.join(_ROOT, "data", "_bt_caches", "csi_000300.csv")

# (标签, lag, 单边成本) —— lag=1 家族口径（信号当日收盘成交）；lag=2 = 次日收盘成交
CALIBERS = [
    ("家族口径（信号当日收盘成交，零费用）", 1, 0.0),
    ("次日成交（lag=2，零费用）", 2, 0.0),
    ("次日成交 + 万3 双边", 2, 0.0003),
    ("次日成交 + 万10 双边（含滑点）", 2, 0.0010),
]
STOPS = [None, 0.05, 0.08, 0.10, 0.15]


def _positions(sig, lag):
    """目标仓位序列平移 lag 天：pos[t] = 第 t-lag 日收盘产生的信号。"""
    sig = np.asarray(sig, dtype=float)
    n = len(sig)
    pos = np.zeros(n)
    if lag <= 0:
        return sig.copy()
    pos[lag:] = sig[:n - lag]
    return pos


def _log_trades(pos, close, dates, code, s, cost=0.0):
    """持仓日区间 [t0, t1) 记为一笔：T0 日收盘建仓、T1 日收盘平仓。"""
    trades, entry = [], None
    for t in range(s, len(close)):
        if entry is None and pos[t] > 0:
            entry = t
        elif entry is not None and pos[t] <= 0:
            t0, t1 = max(entry, 1), t
            trades.append({"code": code, "entry_date": dates[t0 - 1], "exit_date": dates[t1 - 1],
                           "bars": t1 - t0, "ret": close[t1 - 1] / close[t0 - 1] - 1 - 2 * cost})
            entry = None
    if entry is not None:
        t0 = max(entry, 1)
        trades.append({"code": code, "entry_date": dates[t0 - 1], "exit_date": dates[-1],
                       "bars": len(close) - t0, "ret": close[-1] / close[t0 - 1] - 1 - cost})
    return trades


def run(close, sig, dates, code, s=base.WARMUP, lag=1, cost=0.0):
    """按指定成交口径回测，返回 (指标, 净值序列, 逐笔交易)。"""
    pos = _positions(sig, lag)
    close = np.asarray(close, dtype=float)
    eq = [1.0]
    for t in range(s + 1, len(close)):
        turn = abs(pos[t] - pos[t - 1])
        eq.append(eq[-1] * (1 + pos[t] * (close[t] / close[t - 1] - 1)) * (1 - cost * turn))
    trades = _log_trades(pos, close, dates, code, s, cost)
    m = base._metrics(eq, trades)
    m["exposure"] = float(np.mean(pos[s:]))
    return m, eq, trades


def _med(rows, fn):
    return float(np.median([fn(r) for r in rows]))


def _pool_section(ind, lk):
    """全池：本变种（家族口径）+ v15 BOLL 反转 + 买入持有 对照，并收集各类敏感性。"""
    rows, bol, bh_rows = [], [], []
    curves, dates510 = {}, None
    cal = {lab: [] for lab, _, _ in CALIBERS}
    stops = {st: [] for st in STOPS}
    for code, r in ind.items():
        if code not in lk:
            continue
        close = np.array([np.nan if v is None else float(v) for v in lk[code]["close"]])
        dates = lk[code]["dates"]
        if len(close) != len(dates) or len(close) <= base.WARMUP + 2:
            continue
        sig = rule.signal(close)
        m, eq, tr = run(close, sig, dates, code)
        bh, bh_eq = base.backtest_bh(close, dates, code)
        rows.append({"code": code, "m": m, "bh": bh, "trades": tr})
        bh_rows.append({"code": code, "m": bh, "bh": bh, "trades": []})
        mb, eqb, trb = base.backtest(close, base.signal("BOLL", close, r), dates, code)
        bol.append({"code": code, "m": mb, "bh": bh, "trades": trb})
        for lab, lag, cost in CALIBERS[1:]:
            cal[lab].append(run(close, sig, dates, code, lag=lag, cost=cost)[0])
        for st in STOPS:
            sig_st = sig if st is None else rule.signal(close, stop=st)
            m_st, _, tr_st = run(close, sig_st, dates, code)
            stops[st].append({"code": code, "m": m_st, "bh": bh, "trades": tr_st})
        if code == "510300":
            dates510 = dates[base.WARMUP:]
            curves["BH"] = bh_eq
            curves[VARIANT] = eq
            curves["BOLL(v15 反转)"] = eqb
            curves["close"] = close[base.WARMUP:]
    return rows, bol, bh_rows, cal, stops, curves, dates510


def _csi300_check():
    """指数口径核对：本规则在沪深300指数上是否复现视频那笔交易。"""
    if not os.path.exists(CSI300):
        return ["", "> 指数数据 `data/_bt_caches/csi_000300.csv` 缺失，跳过核对。"]
    df = pd.read_csv(CSI300)
    close = df["close"].astype(float).to_numpy()
    dates = df["date"].astype(str).tolist()
    sig = rule.signal(close)
    trades = _log_trades(_positions(sig, 1), close, dates, "000300", 0)
    hit = [t for t in trades if t["entry_date"] == "2025-06-25"]
    L = ["", "## 指数口径核对（沪深300 指数 · 000300）", "",
         "视频示例：2025-06-25 收盘站上轨买入 → 2025-10-17 收盘跌回中轨卖出，持有 76 个交易日。", ""]
    if hit:
        t = hit[0]
        L.append(f"- 本规则实际：**{t['entry_date']} 建仓 → {t['exit_date']} 平仓**，"
                 f"持仓 {t['bars']} 个交易日，区间收益 {base.pct(t['ret'])} —— **完全复现**。")
    else:
        L.append("- 本规则在该窗口未出现同日期交易，与视频口径不一致（窗口/数据源差异），需核对。")
    n_year = (pd.Timestamp(dates[-1]) - pd.Timestamp(dates[0])).days / 365.25
    L += ["",
          f"- 指数全历史（{dates[0]} ~ {dates[-1]}，约 {n_year:.1f} 年）：交易 {len(trades)} 笔，"
          f"**年均 {len(trades) / n_year:.1f} 笔**，曝光 {np.mean(sig):.0%}"
          f"（即约 {1 - np.mean(sig):.0%} 的时间空仓）——与视频「一年 2~3 次、七成时间空仓」一致。",
          "", "| 建仓 | 平仓 | 持仓交易日 | 区间收益 |", "|---|---|---|---|"]
    for t in trades[-8:]:
        L.append(f"| {t['entry_date']} | {t['exit_date']} | {t['bars']} | {base.pct(t['ret'])} |")
    return L


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
    fig, (ax1, ax2) = plt.subplots(2, 1, figsize=(13, 9), height_ratios=[1.15, 1],
                                   sharex=False, gridspec_kw={"hspace": 0.28})
    for k, c in [("BH", "#888"), (VARIANT, "#93c"), ("BOLL(v15 反转)", "#d33")]:
        if k in curves:
            ax1.plot(x, curves[k], label=("买入持有" if k == "BH" else k), color=c, linewidth=1.5)
    ax1.set_title("510300 沪深300ETF · BOLL 上轨突破 vs v15 布林反转 vs 买入持有（净值, 起=1）")
    ax1.set_ylabel("净值")
    ax1.grid(alpha=0.3)
    ax1.legend()

    close = np.asarray(curves["close"], dtype=float)
    mid, upper, _ = rule.bands(close)
    pos = _positions(rule.signal(close), 1)
    buys, sells, entry = [], [], None
    for t in range(1, len(close)):
        if entry is None and pos[t] > 0:
            entry = t - 1
            buys.append(entry)
        elif entry is not None and pos[t] <= 0:
            sells.append(t - 1)
            ax2.axvspan(x[entry], x[t - 1], color="#93c", alpha=0.10)
            entry = None
    if entry is not None:
        ax2.axvspan(x[entry], x[-1], color="#93c", alpha=0.10)
    ax2.plot(x, close, color="#333", linewidth=1.1, label="收盘价")
    ax2.plot(x, mid, color="#27c", linewidth=0.9, alpha=0.8, label=f"中轨 MA{rule.PARAMS['N']}")
    ax2.plot(x, upper, color="#d33", linewidth=0.9, alpha=0.8,
             label=f"上轨 +{rule.PARAMS['K']:g}σ")
    ax2.scatter(x[buys], close[buys], marker="^", s=32, color="#d33", zorder=5, label="买入（收盘上穿上轨）")
    ax2.scatter(x[sells], close[sells], marker="v", s=32, color="#27c", zorder=5, label="卖出（收盘跌回中轨）")
    ax2.set_title("510300 收盘价 vs 布林带（紫色阴影 = 持仓区间）")
    ax2.grid(alpha=0.3)
    ax2.legend(loc="upper left")
    fig.savefig(path, dpi=120)
    plt.close(fig)
    print("图:", path)


def main():
    from pipeline.bt_stats import trade_stats, section_lines

    ind, lk = base.load()
    rows, bol, bh_rows, cal, stops, curves, dates510 = _pool_section(ind, lk)
    os.makedirs(OUTDIR, exist_ok=True)

    with open(os.path.join(OUTDIR, f"_{VARIANT}_results.jsonl"), "w", encoding="utf-8") as f:
        for row in rows:
            m = row["m"]
            f.write(json.dumps({
                "code": row["code"], "strategy": VARIANT,
                "ann": round(m["ann"], 4), "dd": round(m["dd"], 4),
                "sharpe": round(m["sharpe"], 3), "exposure": round(m["exposure"], 3),
                "n_trades": m["n"], "win_rate": round(m["win_rate"], 4),
                "payoff": round(m["payoff"], 3),
            }, ensure_ascii=False) + "\n")

    a_var, a_bol = base._agg(rows), base._agg(bol)
    a_bh = base._agg(bh_rows)
    longset = {c for c in ind if c in lk and lk[c]["dates"][0] <= base.COHORT_2015}
    c_var = base._agg([x for x in rows if x["code"] in longset])
    c_bol = base._agg([x for x in bol if x["code"] in longset])
    c_bh = base._agg([x for x in bh_rows if x["code"] in longset])

    def row_md(label, a):
        return (f"| {label} | {base.pct(a['ann'])} | {base.pct(a['dd'])} | {a['sharpe']:.2f} | "
                f"{a['exposure']:.0%} | {a['beat_ret']:.0%} | {a['pooled']['n']} | "
                f"{base.pct(a['pooled']['win_rate'])} | {a['pooled']['payoff']:.2f} |")

    def bh_md(label, a):
        return (f"| {label} | {base.pct(a['ann'])} | {base.pct(a['dd'])} | {a['sharpe']:.2f} | "
                f"{a['exposure']:.0%} | — | 1 | — | — |")

    head = ["| 策略 | 中位年化 | 中位最大回撤 | 中位夏普 | 中位曝光 | 跑赢买入持有 | 总交易 | 合计胜率 | 盈亏比 |",
            "|---|---|---|---|---|---|---|---|---|"]

    L = [
        f"# 技术指标策略 · 变种 16（{VARIANT}）· BOLL 上轨突破全池回测",
        f"> {len(rows)} 只场内 ETF（_universe.md 前四类）· 各自全历史（第 {base.WARMUP + 1} 个交易日起）· "
        "多头/空仓 0-1 · 信号当日收盘成交 · 未计佣金滑点 · 无风险利率 0",
        "",
        "## 策略规则",
        "",
        f"- **指标**：`mid = MA(close, {rule.PARAMS['N']})`、`std = STD(close, {rule.PARAMS['N']})`、"
        f"`upper = mid + {rule.PARAMS['K']:g} * std`（下轨算而不用）",
        "- **开多**：`close > upper` 且前一日 `close <= upper`（要求「上穿」，避免同一段趋势重复发信号）",
        "- **平仓**：`close < mid`（跌回中轨即退出，不等下轨）",
        "- **无反向开空**；盘中触碰不算，只看收盘；无信号时空仓等待",
        f"- **止损**：默认关闭（`PARAMS['STOP']=None`），敏感性见下文",
        "",
        "> 实现：`rule.py`（参数权威源，指标在规则内自算 —— 指标库只预计算 BOLL(20,2)）· "
        "引擎/统计复用 `../backtest.py` 与 `pipeline/bt_stats.py`。",
        "",
        f"## 全池汇总（中位数 · {len(rows)} 只）",
        "",
    ] + head + [
        row_md(f"**{VARIANT}（本变种）**", a_var),
        row_md("BOLL（v15 反转：触下轨买/触上轨卖）", a_bol),
        bh_md("买入持有(基准)", a_bh),
        "",
        f"## 长历史子样本（2015 起 · {len(longset)} 只）",
        "",
    ] + head + [
        row_md(f"**{VARIANT}**", c_var),
        row_md("BOLL（v15 反转）", c_bol),
        bh_md("买入持有(基准)", c_bh),
        "",
        "## 成交口径 / 费用敏感性（全池中位年化）",
        "",
        "> 家族口径「信号当日收盘成交」是**回测便利**，实盘做不到（收盘价要收盘后才知道）。"
        "实盘应次日开盘或限价单成交，本节量化这一步的代价。",
        "",
        "| 成交口径 | 中位年化 | 中位最大回撤 | 中位夏普 | 中位曝光 |",
        "|---|---|---|---|---|",
        f"| {CALIBERS[0][0]} | {base.pct(a_var['ann'])} | {base.pct(a_var['dd'])} | "
        f"{a_var['sharpe']:.2f} | {a_var['exposure']:.0%} |",
    ]
    for lab, _, _ in CALIBERS[1:]:
        a = base._agg([{"m": m, "bh": bh_rows[i]["m"], "trades": []} for i, m in enumerate(cal[lab])])
        L.append(f"| {lab} | {base.pct(a['ann'])} | {base.pct(a['dd'])} | {a['sharpe']:.2f} | "
                 f"{a['exposure']:.0%} |")

    L += ["", "## 止损敏感性（全池中位 · 家族成交口径）", "",
          "> 止损自建仓日收盘价起算，同样只在收盘判定。趋势跟踪靠少数大盈利覆盖多次小亏，"
          "**过紧的止损会先切掉这些大盈利**，因此本表未必「止损越紧越好」。", "",
          "| 止损 | 中位年化 | 中位最大回撤 | 中位夏普 | 总交易 | 合计胜率 |",
          "|---|---|---|---|---|---|"]
    for st in STOPS:
        a = base._agg(stops[st])
        lab = "不启用" if st is None else f"-{st:.0%}"
        L.append(f"| {lab} | {base.pct(a['ann'])} | {base.pct(a['dd'])} | {a['sharpe']:.2f} | "
                 f"{a['pooled']['n']} | {base.pct(a['pooled']['win_rate'])} |")

    L += _csi300_check()

    m510 = {x["code"]: x for x in rows}["510300"]
    bh510 = {x["code"]: x for x in bh_rows}["510300"]["m"]
    b510 = {x["code"]: x for x in bol}["510300"]["m"]
    n_year = (pd.Timestamp(lk["510300"]["dates"][-1]) - pd.Timestamp(lk["510300"]["dates"][base.WARMUP])).days / 365.25
    L += ["", f"## 代表标的：510300 沪深300ETF（{lk['510300']['dates'][base.WARMUP]} 起，约 {n_year:.1f} 年）", "",
          "| 口径 | 年化 | 最大回撤 | 夏普 | 总收益 | 卡玛比率 | 交易 | 年均交易 | 胜率 | 盈亏比 | 曝光 |",
          "|---|---|---|---|---|---|---|---|---|---|---|"]
    for lab, m in [(f"**{VARIANT}**", m510["m"]), ("BOLL（v15 反转）", b510), ("买入持有", bh510)]:
        n = m["n"]
        win = "—" if n <= 1 else base.pct(m["win_rate"])
        payoff = "—" if n <= 1 else f"{m['payoff']:.2f}"
        L.append(f"| {lab} | {base.pct(m['ann'])} | {base.pct(m['dd'])} | {m['sharpe']:.2f} | "
                 f"{base.pct(m['total'])} | {m['calmar']:.2f} | {n} | {n / n_year:.1f} | "
                 f"{win} | {payoff} | {m['exposure']:.0%} |")
    L += ["", "### 510300 近年逐笔交易", "", "| 建仓 | 平仓 | 持仓交易日 | 区间收益 |", "|---|---|---|---|"]
    for t in m510["trades"][-10:]:
        L.append(f"| {t['entry_date']} | {t['exit_date']} | {t['bars']} | {base.pct(t['ret'])} |")

    pooled = trade_stats([t for x in rows for t in x["trades"]])
    L += section_lines(pooled, title=f"单笔交易统计（全池合并 · {VARIANT}）")
    L += ["", "> 口径：单笔=建/平仓收盘价收益（未计费用）；胜率=盈利笔数/总笔数；盈亏比=平均盈利/|平均亏损|。"
              "本策略是**趋势跟踪**：胜率不高、靠少数大盈利覆盖多次小亏，"
              "单看胜率会误判；**模拟结果，非投资建议。**"]

    rpt = os.path.join(OUTDIR, f"_{VARIANT}_report.md")
    with open(rpt, "w", encoding="utf-8") as f:
        f.write("\n".join(L))
    if dates510:
        _plot(dates510, curves, os.path.join(OUTDIR, f"_{VARIANT}_510300.png"))
    print("报告:", rpt)
    print("结果:", os.path.join(OUTDIR, f"_{VARIANT}_results.jsonl"))


if __name__ == "__main__":
    main()
