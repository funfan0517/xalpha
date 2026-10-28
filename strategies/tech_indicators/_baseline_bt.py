# -*- coding: utf-8 -*-
"""v11~v15（五个单指标基线变种）的共用回测 runner。

变种目录里的 `backtest.py` 只做三件事：载入 base 引擎、解析出本变种的 signal、调用 `main()`，
这样五个变种不必各复制一份百行报告代码，同时保持「每个变种目录都能单独跑」的家族约定。

产物（落在调用方变种目录的 `backtest/` 下）：
- `_<key>_report.md`、`_<key>_results.jsonl`、`_<key>_510300.png`

口径与家族基线完全一致：0-1 多头/空仓、信号当日收盘成交、未计佣金滑点、无风险利率 0。
模拟结果，非投资建议。
"""
import json
import os
import sys

import numpy as np
import pandas as pd


def load_module(path, name):
    """按独立命名载入一个文件模块（不注册到 sys.modules，可重复载入）。"""
    import importlib.util

    spec = importlib.util.spec_from_file_location(name, path)
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


def load_rule(path, name):
    """载入变种的 rule.py，并**登记到 sys.modules**。

    命名与 `../backtest.py::_rule()` 保持一致（`_ti_rule_<变种目录名>`），
    这样 base 后续分发时会复用同一个模块实例，不会出现两份互不相干的规则。
    """
    if name in sys.modules:
        return sys.modules[name]
    import importlib.util

    spec = importlib.util.spec_from_file_location(name, path)
    m = importlib.util.module_from_spec(spec)
    sys.modules[name] = m
    spec.loader.exec_module(m)
    return m


def _fmt(label, a, base):
    return (f"| {label} | {base.pct(a['ann'])} | {base.pct(a['dd'])} | {a['sharpe']:.2f} | "
            f"{a['exposure']:.0%} | {a['beat_ret']:.0%} | {a['beat_dd']:.0%} | "
            f"{a['pooled']['n']} | {base.pct(a['pooled']['win_rate'])} |")


def _plot(dates, curves, path, title):
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
              "KDJ": "#27c", "BOLL": "#93c"}
    for k, v in curves.items():
        ax.plot(x, v, label=("买入持有" if k == "BH" else k),
                color=colors.get(k), linewidth=1.4)
    ax.set_title(title)
    ax.set_ylabel("净值")
    ax.grid(alpha=0.3)
    ax.legend()
    fig.tight_layout()
    fig.savefig(path, dpi=120)
    plt.close(fig)
    print("图:", path)


def main(base, key, sig_fn, here):
    """跑单个基线变种全池回测 + 与其余 4 条基线对照，产物写 `here/backtest/`。"""
    from pipeline.bt_stats import trade_stats, section_lines

    variant = key
    outdir = os.path.join(here, "backtest")
    ind, lk = base.load()
    rows = []
    baserows = {s: [] for s in base.STRAT}
    curves, dates_510300 = {}, None

    for code, r in ind.items():
        if code not in lk:
            continue
        close = base._arr(lk[code]["close"])
        dates = lk[code]["dates"]
        if len(close) != len(dates) or len(close) <= base.WARMUP + 2:
            continue
        bh, bh_eq = base.backtest_bh(close, dates, code)
        m, eq, trades = base.backtest(close, sig_fn(close, r), dates, code)
        rows.append({"code": code, "m": m, "bh": bh, "trades": trades})

        # 对照：其余基线（同一条变种的信号直接复用，不重复算）
        bc = {}
        for s in base.STRAT:
            if s == variant:
                baserows[s].append({"code": code, "m": m, "bh": bh, "trades": trades})
                bc[s] = eq
                continue
            m5, eq5, tr5 = base.backtest(close, base.signal(s, close, r), dates, code)
            baserows[s].append({"code": code, "m": m5, "bh": bh, "trades": tr5})
            bc[s] = eq5

        if code == "510300":
            dates_510300 = dates[base.WARMUP:]
            curves = {"BH": bh_eq, variant: eq}
            curves.update(bc)

    os.makedirs(outdir, exist_ok=True)

    with open(os.path.join(outdir, f"_{variant}_results.jsonl"), "w", encoding="utf-8") as f:
        for row in rows:
            f.write(json.dumps({
                "code": row["code"], "strategy": variant,
                "ann": round(row["m"]["ann"], 4), "dd": round(row["m"]["dd"], 4),
                "sharpe": round(row["m"]["sharpe"], 3), "exposure": round(row["m"]["exposure"], 3),
                "n_trades": row["m"]["n"], "win_rate": round(row["m"]["win_rate"], 4),
                "bh_ann": round(row["bh"]["ann"], 4), "bh_dd": round(row["bh"]["dd"], 4),
            }, ensure_ascii=False) + "\n")

    longset = {c for c in ind if c in lk and lk[c]["dates"][0] <= base.COHORT_2015}
    cohort = [x for x in rows if x["code"] in longset]
    cbase = {s: [x for x in baserows[s] if x["code"] in longset] for s in base.STRAT}

    a_var, a_cvar = base._agg(rows), base._agg(cohort)
    a_base = {s: base._agg(baserows[s]) for s in base.STRAT}
    a_cbase = {s: base._agg(cbase[s]) for s in base.STRAT}
    bh_ann = np.median([x["bh"]["ann"] for x in rows])
    bh_dd = np.median([x["bh"]["dd"] for x in rows])
    bh_sh = np.median([x["bh"]["sharpe"] for x in rows])
    c_bh_ann = np.median([x["bh"]["ann"] for x in cohort])
    c_bh_dd = np.median([x["bh"]["dd"] for x in cohort])
    c_bh_sh = np.median([x["bh"]["sharpe"] for x in cohort])

    head = ["| 策略 | 中位年化 | 中位最大回撤 | 中位夏普 | 中位曝光 | 跑赢买入持有 | 回撤更小 | 总交易 | 合计胜率 |",
            "|---|---|---|---|---|---|---|---|---|"]
    L = [
        f"# 技术指标策略 · 变种 {variant}（单指标基线）· 全池回测",
        f"> {len(rows)} 只场内 ETF（_universe.md 前四类）· 各自全历史（第 61 个交易日起）· 多头/空仓 0-1 · "
        "信号当日收盘成交 · 未计佣金滑点 · 无风险利率 0",
        "",
        "## 策略规则",
        "",
        f"- **入场/离场**：{base.STRAT_DESC[variant]}",
        "",
        "> 规则本体在 `rule.py`（家族引擎 `../backtest.py::build_signals` 按名分发到这里，"
        "其余变种与每日建议共用同一份实现）。",
        "",
        f"## 全池汇总（中位数 · {len(rows)} 只）",
        "",
    ] + head + [_fmt(f"**{variant}（本变种）**", a_var, base)]
    for s in base.STRAT:
        if s != variant:
            L.append(_fmt(s, a_base[s], base))
    L.append(f"| 买入持有(基准) | {base.pct(bh_ann)} | {base.pct(bh_dd)} | {bh_sh:.2f} | "
             f"100% | — | — | 1 | — |")

    L += ["", f"## 长历史子样本（2015 起 · {len(cohort)} 只 · 约 10.7 年）", ""] + head
    L.append(_fmt(f"**{variant}（本变种）**", a_cvar, base))
    for s in base.STRAT:
        if s != variant:
            L.append(_fmt(s, a_cbase[s], base))
    L.append(f"| 买入持有(基准) | {base.pct(c_bh_ann)} | {base.pct(c_bh_dd)} | {c_bh_sh:.2f} | "
             f"100% | — | — | 1 | — |")

    L += ["", "## 代表标的：510300 沪深300ETF（2015-01-05 起）", "",
          "| 口径 | 年化 | 最大回撤 | 夏普 | 总收益 | 卡玛比率 | 交易 | 胜率 | 盈亏比 |",
          "|---|---|---|---|---|---|---|---|---|"]
    v510 = {x["code"]: x for x in rows}["510300"]
    bh510, m510 = v510["bh"], v510["m"]
    L.append(f"| 买入持有 | {base.pct(bh510['ann'])} | {base.pct(bh510['dd'])} | "
             f"{bh510['sharpe']:.2f} | {base.pct(bh510['total'])} | {bh510['calmar']:.2f} | 1 | — | — |")
    L.append(f"| **{variant}（本变种）** | {base.pct(m510['ann'])} | {base.pct(m510['dd'])} | "
             f"{m510['sharpe']:.2f} | {base.pct(m510['total'])} | {m510['calmar']:.2f} | "
             f"{m510['n']} | {base.pct(m510['win_rate'])} | {m510['payoff']:.2f} |")
    for s in base.STRAT:
        if s == variant:
            continue
        m5 = {x["code"]: x for x in baserows[s]}["510300"]["m"]
        L.append(f"| {s} | {base.pct(m5['ann'])} | {base.pct(m5['dd'])} | {m5['sharpe']:.2f} | "
                 f"{base.pct(m5['total'])} | {m5['calmar']:.2f} | {m5['n']} | "
                 f"{base.pct(m5['win_rate'])} | {m5['payoff']:.2f} |")

    pooled = trade_stats([t for x in rows for t in x["trades"]])
    L += section_lines(pooled, title=f"单笔交易统计（全池合并 · {variant}）")
    L += ["", "> 口径：单笔=建/平仓收盘价收益（未计费用）；胜率=盈利笔数/总笔数；"
              "盈亏比=平均盈利/|平均亏损|。**模拟结果，非投资建议。**"]

    rpt = os.path.join(outdir, f"_{variant}_report.md")
    with open(rpt, "w", encoding="utf-8") as f:
        f.write("\n".join(L))
    if dates_510300:
        _plot(dates_510300, curves, os.path.join(outdir, f"_{variant}_510300.png"),
              f"510300 沪深300ETF · {variant} vs 其余基线 vs 买入持有（净值, 起=1）")

    print("报告:", rpt)
    print("结果:", os.path.join(outdir, f"_{variant}_results.jsonl"))
