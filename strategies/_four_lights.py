# -*- coding: utf-8 -*-
"""原四灯机制复测：把决策层换成「与逻辑」(AND)，并对 K 做一般化扫描。

原机制（见 doc 里的四灯体系）:
    四灯齐亮才买入, 任一灯转空立即卖出。
    灯 = 趋势(trend) / 主力(capital) / 持续力(sustain) / 热度(heat), 四维独立。

与当前生效 c7 的区别在**决策层**, 不在灯定义:
    c7  : 加权求和 score -> enter_min / exit_max 阈值 (+ enter_required 两盏灯)
    四灯: n_on = 「亮着的灯数」 -> n_on>=K 买 / n_on<K 卖;  K=4 即原四灯

所以本脚本绕过 rule.build_signals 的决策层, 自己构造 enter/exit 掩码后直接调引擎,
隔离「决策逻辑」这一个变量。K 从 1 扫到 4:
    K=1 = 只要有一盏亮就买 (OR, 最松)
    K=4 = 四灯齐亮 (原机制, 最严)

用法: python strategies/_four_lights.py
输出: strategies/_four_lights_report.md
"""
import os
import sys

import numpy as np
import pandas as pd

sys.stdout.reconfigure(encoding="utf-8")
_DIR = os.path.dirname(os.path.abspath(__file__))
_ROOT = os.path.dirname(_DIR)
for p in (_ROOT, _DIR, os.path.join(_DIR, "lights")):
    if p not in sys.path:
        sys.path.insert(0, p)

import rule  # noqa: E402
import backtest as bt  # noqa: E402
from _tune import Bench, metrics  # noqa: E402

OUT = os.path.join(_DIR, "_four_lights_report.md")
L4 = ("trend", "capital", "sustain", "heat")     # 原四灯对应的四个维度

# 灯定义组合: 每个维度挑一个「更贴近原文档」的定义
COMBOS = [
    ("原文档口径（含周线 MACD + 量价主力 + 量价热度）",
     dict(trend="ma_short_adx", capital="vr5_ge07", sustain="macd_weekly", heat="ret5_vr5")),
    ("去周线 MACD（当前 sustain 定义）",
     dict(trend="ma_short_adx", capital="vr5_ge07", sustain="macd", heat="ret5_vr5")),
    ("主力灯用 cap3（资金强度带）",
     dict(trend="ma_short_adx", capital="cap3", sustain="macd_weekly", heat="ret5_vr5")),
    ("热度灯用纯涨幅带 ret5_band",
     dict(trend="ma_short_adx", capital="vr5_ge07", sustain="macd_weekly", heat="ret5_band")),
]


def gate_mask(f, cfg, names=None):
    names = names if names is not None else cfg.gates
    ok = np.ones(len(f["c"]), dtype=bool)
    for n in names:
        ok &= np.asarray(rule.GATE_REGISTRY[n](f, cfg), dtype=bool)
    return ok


def light_vals(f, defs):
    out = {}
    for dim, name in defs.items():
        fn = rule.LIGHT_REGISTRY[dim][name][0]
        out[dim] = np.asarray(fn(f), dtype=float)
    return out


def run_combo(bench, defs, K, gate_names=("liq", "age", "ma_trend", "ret3_max",
                                          "cap_abs_max", "indicators_ready")):
    """n_on >= K 买 / n_on < K 卖, 直接构造掩码调引擎。"""
    cfg = rule.ACTIVE.with_(lights={d: (defs[d], 1) for d in L4} | {"momentum": None})
    rows = []
    for meta in rule.POOL:
        code = meta["code"]
        df = bench.frame(code)
        if len(df) < cfg.min_bars + 60:
            continue
        f = bench.factors(code, cfg)
        lv = light_vals(f, defs)
        n_on = np.sum([(lv[d] >= 1).astype(int) for d in L4], axis=0)
        gate = gate_mask(f, cfg, gate_names)
        o = df["open"].to_numpy(float)
        c = df["close"].to_numpy(float)
        begin = int(np.argmax(np.asarray(df.index >= np.datetime64(cfg.sample_from))))
        if len(c) - begin < cfg.min_bars:
            continue
        enter = gate & (n_on >= K)
        exit_ = (~gate) | (n_on < K)
        sig = dict(enter=enter, exit=exit_, weight=np.ones(len(c)),
                   cost=rule.cost_array(f, cfg), high=f["h"].to_numpy(float))
        nav, pos, trades, _h, pos_log = rule.run_engine(o, c, sig, begin=begin, cfg=cfg)
        if not np.isfinite(nav[begin]) or nav[begin] <= 0:
            continue
        years = max((df.index[-1] - df.index[begin]).days / 365.0, 1e-9)
        _r, a, smdd = bt._stat(nav[begin:], years)
        _r2, bb, bmdd = bt._stat(c[begin:], years)
        rows.append(dict(code=code, cat=meta["cat"], theme=meta["theme"],
                         st_ann=a, base_ann=bb, st_mdd=smdd, base_mdd=bmdd,
                         pos_ratio=pos, n_trades=len(trades), trade_log=trades,
                         timing_edge=rule._eng.timing_edge(c, pos_log, begin)))
    return rows


def pooled(rows):
    r = np.array([t["ret"] for x in rows for t in (x.get("trade_log") or [])])
    if not len(r):
        return None
    w, l = r[r > 0], r[r <= 0]
    return dict(n=len(r), win=len(w) / len(r), exp=r.mean(),
                payoff=(w.mean() / abs(l.mean())) if len(l) and l.mean() != 0 else float("inf"))


def main():
    bench = Bench()
    res = []

    base_rows, _ = bench.run(rule.ACTIVE)
    res.append(("**当前生效 c7（加权阈值，对照）**", metrics(base_rows), pooled(base_rows)))

    for tag, defs in COMBOS:
        for K in (1, 2, 3, 4):
            rows = run_combo(bench, defs, K)
            m, p = metrics(rows), pooled(rows)
            if m is None or p is None:
                continue
            lab = {"原文档口径（含周线 MACD + 量价主力 + 量价热度）": "原文档口径",
                   "去周线 MACD（当前 sustain 定义）": "去周线MACD",
                   "主力灯用 cap3（资金强度带）": "主力cap3",
                   "热度灯用纯涨幅带 ret5_band": "热度ret5_band"}[tag]
            mark = " ← 原四灯" if K == 4 else (" ← OR" if K == 1 else "")
            res.append((f"{lab} · K={K}{mark}", m, p))

    print(f"池子 {len(rule.POOL)} 只 · 四灯 = {'/'.join(L4)} · n_on>=K 买, n_on<K 卖\n")
    H = (f"{'口径':<34}{'年化':>8}{'超额':>8}{'边际bp':>9}{'目标':>8}"
         f"{'持仓':>7}{'笔数':>7}{'胜率':>7}{'盈亏比':>7}{'单笔期望':>9}")
    print(H)
    print("-" * 96)
    for tag, m, p in res:
        pf = "inf" if p["payoff"] == float("inf") else f"{p['payoff']:.2f}"
        print(f"{tag:<34}{m['ann'] * 100:>7.1f}%{m['excess'] * 100:>7.1f}%{m['edge']:>9.1f}"
              f"{m['score']:>8.3f}{m['pos'] * 100:>6.1f}%{p['n']:>7}{p['win'] * 100:>6.1f}%"
              f"{pf:>7}{p['exp'] * 100:>8.3f}%")

    best = max(res, key=lambda r: r[1]["score"])
    print(f"\n目标最优: {best[0]}（目标 {best[1]['score']:.3f}）")
    c7 = res[0]
    print(f"当前 c7  : 目标 {c7[1]['score']:.3f}")
    ands = [r for r in res if "K=4" in r[0]]
    print(f"四灯齐亮 (K=4) 各口径目标: " +
          "、".join(f"{r[1]['score']:.3f}" for r in ands) +
          f"  (c7 = {c7[1]['score']:.3f})")

    L = ["# 原四灯机制复测（AND 逻辑 vs 加权阈值）", "",
         f"> 池子 {len(rule.POOL)} 只行业 ETF · 四灯 = {'/'.join(L4)} · "
         f"样本 {rule.ACTIVE.sample_from} ~ {bench.end.date()} · 成本单边 {rule.ACTIVE.fee:.2%}", "",
         "> **原机制**: 四灯齐亮才买, 任一灯转空立即卖。本脚本把决策层换成",
         "> `n_on >= K` 买 / `n_on < K` 卖（`n_on` = 亮着的灯数, 灯值 ≥1 视为亮）。",
         "> `K=4` 即原四灯齐亮, `K=1` 即 OR（只要一盏亮就买）。**灯定义保持现状**, "
         "只变决策逻辑, 以隔离这一个变量。", "",
         "| 口径 | 年化 | 超额 | 边际(bp) | 目标 | 持仓 | 笔数 | 胜率 | 盈亏比 | 单笔期望 |",
         "|---|---|---|---|---|---|---|---|---|---|"]
    for tag, m, p in res:
        pf = "inf" if p["payoff"] == float("inf") else f"{p['payoff']:.2f}"
        L.append(f"| {tag} | {m['ann'] * 100:+.1f}% | {m['excess'] * 100:+.1f}% | "
                 f"{m['edge']:+.1f} | **{m['score']:.3f}** | {m['pos'] * 100:.1f}% | "
                 f"{p['n']} | {p['win'] * 100:.1f}% | {pf} | {p['exp'] * 100:+.3f}% |")
    L += ["", f"> 目标最优: **{best[0]}**（{best[1]['score']:.3f}）· "
              f"当前 c7 = {c7[1]['score']:.3f}。", ""]
    open(OUT, "w", encoding="utf-8").write("\n".join(L))
    print(f"\n-> {OUT}")


if __name__ == "__main__":
    main()
