# -*- coding: utf-8 -*-
r"""红利基金策略 · 5 个打分指标的重要程度对比回测

问题
----------------------------------------------------------------------
`rank.py` 的总分 = 60%×业绩分 + 40%×价值分，其中**价值分 = 5 项各 20 分**:
    PE(低者高) + PE分位(低者高) + 股息率(高者高) + 股债收益比(高者高) + RSI14(低者高)
用户想测：这 5 个指标各自「单独」作为选基依据时，推荐操作在 5 年回测里的表现如何，
借此判断每个指标的重要程度（谁最能选出好标的）。

方法（无前视 walk-forward）
----------------------------------------------------------------------
  * 决策：每月最后一个交易日 t 收盘，用 **t 及以前**的数据算每只基金在该指标上的得分，
    按该指标打分规则（高者/低者高分已内置于 `rank.value_parts_raw`）排序，取分最高 topN 等权;
  * 执行：信号次日生效，月度再平衡；单边 FEE = 0.03%。
  * 各指标的历史可得性（决定"能否真回测"）:
      - PE / PE分位：中证官网 peg 历史 → 真实逐月可得（仅 csi 指数基金；非 csi 基金该维 None 不参与该维排序）
      - RSI14     ：基金累计净值历史 → 真实逐月可得（全池）
      - 股息率    ：**无历史序列**（蛋卷/官网只给当前快照）→ 以最新快照为常数（视为慢变量，标注）
      - 股债收益比：= 股息率 ÷ 10Y国债；分子同上无历史，分母 10Y 国债有月度历史 →
                   用「最新股息率快照 ÷ t 日 10Y 国债」近似（标注）
  * 参考组合：① 5 维合并价值分（按 `rank` 同口径中位数补齐）→ topN；② 全池等权基准。
  * 另算每维的「选择质量」：每期取该维最高分标的的未来 3 月收益 − 可选池等权未来 3 月收益(bp)。

产物（AGENTS §8.4 ③）
----------------------------------------------------------------------
    backtest/_dims_bt.jsonl      每 (指标, topn) 一行
    backtest/_dims_bt.json       结构化结果
    backtest/_dims_report.md     对比报告

用法: python strategies/dividend/backtest_dims.py [--refresh-nav] [--refresh-val]
"""
import json
import os
import sys
from datetime import datetime

import numpy as np
import pandas as pd

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")
_DIR = os.path.dirname(os.path.abspath(__file__))
if _DIR not in sys.path:
    sys.path.insert(0, _DIR)
_ROOT = os.path.dirname(os.path.dirname(_DIR))

from backtest_rank import (  # noqa: E402  # 复用纯函数工具
    month_ends, perf_stats, trailing, objective, simulate, FEE,
)

import pool   # noqa: E402
import rank   # noqa: E402

BACKTEST_DIR = os.path.join(_DIR, "backtest")
OUT_JSONL = os.path.join(BACKTEST_DIR, "_dims_bt.jsonl")
OUT_JSON = os.path.join(BACKTEST_DIR, "_dims_bt.json")
OUT_REPORT = os.path.join(BACKTEST_DIR, "_dims_report.md")

Y10_CACHE = os.path.join(_ROOT, "data", "_bt_caches", "bond10y_m.csv")
WINDOWS = (3, 5, 10)
TOPN_LIST = (1, 2)
BACKTEST_YEARS = 5          # 回测窗 = 近 5 年

DIMS = ("pe", "pe_pct", "dy", "ratio", "rsi")
DIM_LABELS = {
    "pe": "PE(低者高)", "pe_pct": "PE分位(低者高)", "dy": "股息率(高者高)",
    "ratio": "股债收益比(高者高)", "rsi": "RSI14(低者高)",
}


# ----------------------------------------------------------------------
# 逐月算每只基金 5 维得分（as-of t）
# ----------------------------------------------------------------------
def dim_scores_asof(navs, peg, dy_now, y10, t):
    """{code: {dim: score 0-20}}，按 t 及以前数据计算（无前视）。"""
    rsis = pool.rsi14(navs.loc[:t])
    y10_t = float(y10.loc[:t].iloc[-1]) if len(y10.loc[:t]) else None
    out = {}
    for r in pool.POOL:
        c = r["code"]
        pe = pe_pct = None
        if r["csi"] and r["csi"] in peg.columns:
            s = peg[r["csi"]].loc[:t].dropna()
            if len(s):
                pe = float(s.iloc[-1])
                pct = pool._percentile(s)
                pe_pct = float(pct.iloc[-1])
        dy = dy_now.get(c)
        ratio = round(dy / y10_t, 3) if (dy is not None and y10_t) else None
        rsi = rsis.get(c)
        out[c] = rank.value_parts_raw(pe, pe_pct, dy, ratio, rsi)
    return out


def eligible(codes, hist, min_hist=1.0):
    ok = []
    for c in codes:
        if c not in hist.columns:
            continue
        s = hist[c].dropna()
        if len(s) > 1 and (s.index[-1] - s.index[0]).days / 365.25 >= min_hist:
            ok.append(c)
    return ok


# ----------------------------------------------------------------------
# 选基（单维 / 5 维合并 / 基准）
# ----------------------------------------------------------------------
def build_picks(navs, peg, dy_now, y10, dates, topns, benches, min_hist=1.0):
    out = {(d, n): {} for d in DIMS for n in topns}
    for n in topns:
        out[("combined", n)] = {}
    for bname in benches:
        out[(bname, 0)] = {}

    all_codes = [r["code"] for r in pool.POOL]
    for t in dates:
        hist = navs.loc[:t]
        for bname, codes in benches.items():
            out[(bname, 0)][t] = eligible(codes, hist, min_hist)
        sc = dim_scores_asof(navs, peg, dy_now, y10, t)
        # 合并用的中位数补齐（与 rank.build_table 同口径）
        med = {}
        for d in DIMS:
            vals = sorted(x[d] for x in sc.values() if x[d] is not None)
            med[d] = vals[len(vals) // 2] if vals else None
        el = eligible(all_codes, hist, min_hist)
        for d in DIMS:
            ranked = [c for c in el if sc[c][d] is not None]
            ranked.sort(key=lambda c: -sc[c][d])
            for n in topns:
                out[(d, n)][t] = ranked[:n]

        def cval(c):
            p = sc[c]
            avail = [d for d in DIMS if p[d] is not None]
            if len(avail) < 2:
                return None
            return sum(p[d] if p[d] is not None else med[d] for d in DIMS)

        cr = [(c, cval(c)) for c in el if cval(c) is not None]
        cr.sort(key=lambda x: -x[1])
        ranked_codes = [c for c, _ in cr]
        for n in topns:
            out[("combined", n)][t] = ranked_codes[:n]
    return out


# ----------------------------------------------------------------------
# 单维选择质量（对未来 3 月的判别力）
# ----------------------------------------------------------------------
def dim_edge(navs, peg, dy_now, y10, dates, dim, fwd=3):
    edges = []
    for t in dates:
        sc = dim_scores_asof(navs, peg, dy_now, y10, t)
        el = eligible([r["code"] for r in pool.POOL], navs.loc[:t], 1.0)
        ranked = [c for c in el if sc[c][dim] is not None]
        if len(ranked) < 2:
            continue
        ranked.sort(key=lambda c: -sc[c][dim])
        top = ranked[0]
        end = t + pd.DateOffset(months=fwd)
        fwd_rets = {}
        for c in ranked:
            s = navs[c].loc[t:end].dropna()
            if len(s) > 1:
                fwd_rets[c] = float(s.iloc[-1] / s.iloc[0] - 1)
        if top in fwd_rets and len(fwd_rets) >= 2:
            edges.append(fwd_rets[top] - sum(fwd_rets.values()) / len(fwd_rets))
    return (round(float(np.mean(edges)) * 1e4, 1), len(edges)) if edges else (None, 0)


# ----------------------------------------------------------------------
# 主流程
# ----------------------------------------------------------------------
def main(argv=None):
    argv = argv or sys.argv[1:]
    refresh_nav = "--refresh-nav" in argv
    refresh_val = "--refresh-val" in argv

    navs = pool.load_navs(refresh=refresh_nav)
    peg = pool.load_peg()
    y10 = pd.read_csv(Y10_CACHE, index_col=0).iloc[:, 0]
    y10.index = pd.to_datetime(y10.index)
    val = pool.valuation(refresh=refresh_val)
    dy_now = {r["code"]: val[r["code"]]["dy"] for r in pool.POOL}

    end = navs.index[-1]
    start = end - pd.DateOffset(years=BACKTEST_YEARS)
    dates = [d for d in month_ends(navs.index, "2000-01-01", end) if d >= start]
    print(f"[info] 回测窗 {start.date()} ~ {end.date()} · {len(dates)} 个决策月 · "
          f"池内 {len(pool.POOL)} 只")

    benches = {"all": [r["code"] for r in pool.POOL]}
    picks = build_picks(navs, peg, dy_now, y10, dates, TOPN_LIST, benches)

    edges = {d: dim_edge(navs, peg, dy_now, y10, dates, d) for d in DIMS}

    rows = []
    for (key, n), pk in picks.items():
        if key == "all":                  # 等权基准
            nav = simulate(navs, pk, FEE).loc[start:]
            st = perf_stats(nav)
            obj, _ = objective(nav)
            rows.append(dict(dim="all", topn=0, label="全池等权基准",
                             kind="bench", **st, obj=obj, edge=None,
                             n_rebal=sum(1 for v in pk.values() if v)))
            continue
        nav = simulate(navs, pk, FEE).loc[start:]
        st = perf_stats(nav)
        obj, _ = objective(nav)
        label = (DIM_LABELS.get(key, key) + ("(合并5维)" if key == "combined" else ""))
        rows.append(dict(dim=key, topn=n, label=label,
                         kind=("combined" if key == "combined" else "single"),
                         **st, obj=obj, edge=edges.get(key),
                         n_rebal=sum(1 for v in pk.values() if v)))

    os.makedirs(BACKTEST_DIR, exist_ok=True)
    with open(OUT_JSONL, "w", encoding="utf-8") as fh:
        for r in rows:
            fh.write(json.dumps(r, ensure_ascii=False, default=str) + "\n")
    payload = {"generated_at": datetime.now().strftime("%Y-%m-%d %H:%M"),
               "sample": [str(start.date()), str(end.date())],
               "fee": FEE, "rebal": "月末决策/次日生效", "windows": WINDOWS,
               "note": ("PE/PE分位=peg真实历史; RSI=净值真实历史; 股息率/股债收益比无历史序列,"
                        "股息率用最新快照为常数、股债比=快照÷10Y国债(t) 近似(标注)"),
               "rows": rows}
    with open(OUT_JSON, "w", encoding="utf-8") as fh:
        json.dump(payload, fh, ensure_ascii=False, indent=2, default=str)

    txt = render(rows, start, end, edges)
    with open(OUT_REPORT, "w", encoding="utf-8") as fh:
        fh.write(txt)
    print(txt)
    print(f"产物: {OUT_REPORT}\n      {OUT_JSONL}\n      {OUT_JSON}")
    return 0


def _p(v, d=1):
    return "—" if v is None else f"{v * 100:.{d}f}%"


def render(rows, start, end, edges):
    L = ["# 红利基金策略 · 5 个打分指标重要程度对比回测", "",
         f"> 生成 {datetime.now():%Y-%m-%d %H:%M} · 样本 {start.date()} ~ {end.date()}"
         f" · 月末再平衡 / 信号次日生效 / 单边 {FEE:.2%}",
         "> 方法: 每个指标单独作为选基依据（按该指标打分规则排序取 topN 等权），5 年 walk-forward 回测；"
         "并附 5 维合并(top2) 与全池等权基准。",
         "> 历史可得性: **PE/PE分位=中证官网 peg 真实历史；RSI14=累计净值真实历史；"
         "股息率/股债收益比无历史序列 → 股息率用最新快照为常数、股债比=快照÷10Y国债(t) 近似**（标注⚠）。", ""]

    L += ["## 一、单指标选基 vs 基准（topN=2，全期）", "",
          "| 指标 | 类型 | 累计 | 年化 | 最大回撤 | 夏普 | Calmar | **obj** | 选择质量(bp) |",
          "|---|---|---:|---:|---:|---:|---:|---:|---:|"]
    single = [r for r in rows if r["kind"] == "single" and r["topn"] == 2]
    bench = next((r for r in rows if r["dim"] == "all"), None)
    comb = next((r for r in rows if r["dim"] == "combined" and r["topn"] == 2), None)
    for r in sorted(single, key=lambda x: (-1e9 if x["obj"] is None else -x["obj"])):
        e = r.get("edge")
        L.append(f"| {r['label']} | 单指标 | {_p(r['cum'], 0)} | {_p(r['ann'])} | {_p(r['mdd'])} | "
                 f"{r['sharpe']:.2f} | {'—' if r['calmar'] is None else format(r['calmar'], '.2f')} | "
                 f"**{r['obj']}** | {('+' if e and e[0] >= 0 else '') + f'{e[0]:.1f}' if e and e[0] is not None else '—'} |")
    if comb:
        e = comb.get("edge")
        L.append(f"| {comb['label']} | 合并 | {_p(comb['cum'], 0)} | {_p(comb['ann'])} | {_p(comb['mdd'])} | "
                 f"{comb['sharpe']:.2f} | {'—' if comb['calmar'] is None else format(comb['calmar'], '.2f')} | "
                 f"**{comb['obj']}** | — |")
    if bench:
        L.append(f"| {bench['label']} | 基准 | {_p(bench['cum'], 0)} | {_p(bench['ann'])} | {_p(bench['mdd'])} | "
                 f"{bench['sharpe']:.2f} | {'—' if bench['calmar'] is None else format(bench['calmar'], '.2f')} | "
                 f"**{bench['obj']}** | — |")
    L += ["",
          "> **obj** = Σ w_W·(年化_W − 0.5·|回撤_W|)，w = 3年50%/5年30%/10年20%（与 `backtest_rank.py` 同目标函数）。",
          "> **选择质量(bp)** = 每期该指标最高分标的的未来 3 月收益 − 可选池等权未来 3 月收益；正值=该指标有判别力。", ""]

    L += ["## 二、topN=1 对照（看集中下注的单指标表现）", "",
          "| 指标 | 累计 | 年化 | 最大回撤 | 夏普 | Calmar | obj |",
          "|---|---:|---:|---:|---:|---:|---:|"]
    single1 = [r for r in rows if r["kind"] == "single" and r["topn"] == 1]
    for r in sorted(single1, key=lambda x: (-1e9 if x["obj"] is None else -x["obj"])):
        L.append(f"| {r['label']} | {_p(r['cum'], 0)} | {_p(r['ann'])} | {_p(r['mdd'])} | "
                 f"{r['sharpe']:.2f} | {'—' if r['calmar'] is None else format(r['calmar'], '.2f')} | **{r['obj']}** |")
    L += ["", ""]

    L += ["## 三、单指标重要性排序（按 obj，topN=2）", ""]
    ranked = sorted(single, key=lambda x: (-1e9 if x["obj"] is None else -x["obj"]))
    for i, r in enumerate(ranked, 1):
        e = r.get("edge")
        e_txt = (f"，未来3月选择质量 {('+' if e[0] >= 0 else '') + format(e[0], '.1f')}bp（{e[1]}期）"
                 if e and e[0] is not None else "，选择质量无数据")
        L.append(f"{i}. **{r['label']}**：年化 {_p(r['ann'])} / 回撤 {_p(r['mdd'])} / obj {r['obj']}{e_txt}")
    L += ["",
          "> 排名越靠前 = 该指标单独选基越有效 = 重要程度越高。若某单指标接近甚至超过「5 维合并」与基准，"
          "说明该指标信息量主导；若所有单指标都明显弱于合并/基准，说明指标间互补、需组合使用。", ""]

    L += ["## 四、口径与边界", "",
          "- 净值用 **totvalue（累计净值, 含分红再投）**，场内 ETF 与场外联接同口径。",
          "- **PE/PE分位**：中证官网 peg 真实历史逐月可得（仅 csi 指数基金；`008164`/`005125`/`159905` 等非 csi "
          "基金在该维历史为 None → 不参与该维排序，属如实标注的覆盖缺口）。",
          "- **RSI14**：基金累计净值真实历史，全池可得。",
          "- ⚠ **股息率 / 股债收益比无历史序列**（公开源只给当前快照）：股息率以最新快照为常数（慢变量假设），"
          "股债收益比 = 快照 ÷ 10Y 国债(t)（分母有月度历史）。故这两维的排序在 5 年内基本由同一截面决定，"
          "测试的是「当前股息率/股债比水平作为静态选基因子的有效性」，而非其随时间变动的择时能力。",
          "- 成本仅计单边 0.03%；未扣 ETF 跟踪误差与场外短期赎回费。",
          "- 历史回测不代表未来；机械输出，非投资建议。", ""]
    return "\n".join(L) + "\n"


if __name__ == "__main__":
    sys.exit(main())
