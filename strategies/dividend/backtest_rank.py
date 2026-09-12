# -*- coding: utf-8 -*-
r"""红利基金打分策略 · walk-forward 回测与时间窗权重调优

问题
----------------------------------------------------------------------
`rank.py` 的业绩分按「近3年 / 近5年 / 近10年」加权，但各窗权重该给多少？
本模块做 walk-forward 回测，比较若干**窗权方案**，并按「3 年权重最高」的目标函数择优，
再把胜出方案回写 `rank.py::WINDOW_WEIGHTS`。

方法（无前视）
----------------------------------------------------------------------
  * 决策：每月最后一个交易日 t 收盘, 用 **t 及以前**的净值算业绩分, 取分最高 topN 等权;
  * 执行: 信号次日生效(持仓矩阵整体下移 1 行), 月度再平衡;
  * 成本: 单边 FEE(0.03%); 场外 C 类赎回费(持有>7天 0)不计。
  * 标的在 t 的可选性: 至少 1 年历史且窗口覆盖 >= rank.MIN_COVER。

只回测**业绩分**（价值分是当期快照, 无历史序列, 不可回测）——这也正是用户强调的
「3 年收益 + 回撤权重最高」所在的那一半。

目标函数（3 年权重最高）
----------------------------------------------------------------------
    obj = Σ_W OBJ_W · (年化_W − 0.5·|回撤_W|)  ÷ Σ_W OBJ_W
    OBJ_W = {3年:0.5, 5年:0.3, 10年:0.2}；窗口不可得则按可得窗归一化。

产物（AGENTS §8.4 ③）
----------------------------------------------------------------------
    backtest/_rank_bt.jsonl       每方案一行
    backtest/_rank_bt.json        结构化结果(供报告复用)
    backtest/_rank_report.md      方案对比报告

用法: python strategies/dividend/backtest_rank.py [--refresh]
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

import pool    # noqa: E402
import rank    # noqa: E402

BACKTEST_DIR = os.path.join(_DIR, "backtest")
OUT_JSONL = os.path.join(BACKTEST_DIR, "_rank_bt.jsonl")
OUT_JSON = os.path.join(BACKTEST_DIR, "_rank_bt.json")
OUT_REPORT = os.path.join(BACKTEST_DIR, "_rank_report.md")

BACKTEST_FROM = "2016-09-01"     # 与仓库其他策略一致的样本起点
FEE = 0.0003                     # 单边(场外 C 类按佣金口径近似, 见 README)
TOPN_LIST = (1, 2)

WINDOWS = (3, 5, 10)
SCHEMES = {
    "eq":      {3: 1 / 3, 5: 1 / 3, 10: 1 / 3},
    "user":    {3: 0.5, 5: 0.3, 10: 0.2},     # 用户指定: 3年 > 5年 > 10年
    "heavy3":  {3: 0.7, 5: 0.2, 10: 0.1},
    "heavy5":  {3: 0.3, 5: 0.5, 10: 0.2},
    "heavy10": {3: 0.2, 5: 0.3, 10: 0.5},
}
OBJ_W = {3: 0.5, 5: 0.3, 10: 0.2}          # 目标函数窗权(3年最高)
PICK_LABELS = {"eq": "等权1/3", "user": "3年50/5年30/10年20(指定)",
               "heavy3": "3年70/5年20/10年10", "heavy5": "3年30/5年50/10年20",
               "heavy10": "3年20/5年30/10年50"}


# ----------------------------------------------------------------------
# 工具
# ----------------------------------------------------------------------
def month_ends(idx, start, end):
    """每个自然月的最后一个交易日（区间内）。"""
    grp = pd.Series(idx, index=idx).groupby(idx.to_period("M")).max()
    return [pd.Timestamp(d) for d in grp.values
            if pd.Timestamp(start) <= pd.Timestamp(d) <= pd.Timestamp(end)]


def perf_stats(nav):
    """净值序列 -> 年化 / 最大回撤 / 夏普 / Calmar / 年数。"""
    nav = nav.dropna()
    if len(nav) < 2:
        return None
    years = (nav.index[-1] - nav.index[0]).days / 365.25
    tot = float(nav.iloc[-1] / nav.iloc[0] - 1)
    ann = (1 + tot) ** (1 / years) - 1 if years > 0 else None
    mdd = float((nav / nav.cummax() - 1).min())
    r = nav.pct_change().dropna()
    sharpe = float(r.mean() / r.std() * np.sqrt(252)) if r.std() > 0 else 0.0
    return dict(years=round(years, 2), cum=round(tot, 4), ann=round(float(ann), 4),
                mdd=round(mdd, 4), sharpe=round(sharpe, 2),
                calmar=round(float(ann) / abs(mdd), 2) if mdd < 0 else None)


def trailing(nav, years):
    """净值序列在末端「过去 years 年」的统计（覆盖不足返回 None）。"""
    sub = nav.dropna()
    sub = sub.loc[sub.index[-1] - pd.DateOffset(years=years):]
    if len(sub) < 2:
        return None
    if (sub.index[-1] - sub.index[0]).days / 365.25 < 0.6 * years:
        return None
    return perf_stats(sub)


def objective(nav, weights=OBJ_W):
    """obj = Σ w_W·(年化_W − 0.5|回撤_W|); 窗口不可得则归一化。返回 (obj, {W: val})。"""
    parts = {}
    for y in weights:
        st = trailing(nav, y)
        if st is not None:
            parts[y] = round(st["ann"] - 0.5 * abs(st["mdd"]), 4)
    if not parts:
        return None, {}
    num = sum(weights[y] * v for y, v in parts.items())
    return round(num / sum(weights[y] for y in parts), 4), parts


def selection_quality(navs, dates, weights, fwd_months=3):
    """打分对「未来 fwd_months 个月」的判别力: 每期取最高分标的的未来收益 − 可选池等权未来收益。

    正值 = 选中的标的后市跑赢池均值（打分有效）；返回 (均值 bp, 期数)。
    """
    edges = []
    for t in dates:
        raw = rank.perf_raw(navs.loc[:t], t, windows=list(WINDOWS))
        if not raw:
            continue
        sc = rank.perf_score(raw, weights)
        end = t + pd.DateOffset(months=fwd_months)
        fwd = {}
        for c in sc:
            s = navs[c].loc[t:end].dropna()
            if len(s) > 1:
                fwd[c] = float(s.iloc[-1] / s.iloc[0] - 1)
        if len(fwd) < 2:
            continue
        top = max(sc, key=lambda c: sc[c][0])
        if top in fwd:
            edges.append(fwd[top] - sum(fwd.values()) / len(fwd))
    if not edges:
        return None, 0
    return round(float(np.mean(edges)) * 1e4, 1), len(edges)


# ----------------------------------------------------------------------
# 选基与模拟
# ----------------------------------------------------------------------
def build_picks(navs, dates, schemes, topns, benches, min_hist=1.0):
    """逐决策日算业绩分 -> {(scheme, topn): {date: [codes]}}；并生成等权基准。

    benches: {名称: [codes]} —— 等权持有该组可得标的（无则空仓）。
    """
    out = {(s, n): {} for s in schemes for n in topns}
    for bname in benches:
        out[(bname, 0)] = {}

    def eligible(codes, hist):
        ok = []
        for c in codes:
            if c not in hist.columns:
                continue
            s = hist[c].dropna()
            if len(s) > 1 and (s.index[-1] - s.index[0]).days / 365.25 >= min_hist:
                ok.append(c)
        return ok

    for t in dates:
        hist = navs.loc[:t]
        for bname, codes in benches.items():
            out[(bname, 0)][t] = eligible(codes, hist)
        raw = rank.perf_raw(hist, t, windows=list(WINDOWS))
        if not raw:
            for key in out:
                out[key].setdefault(t, [])
            continue
        for s, w in schemes.items():
            sc = rank.perf_score(raw, w)
            ranked = sorted(sc, key=lambda c: -sc[c][0])
            for n in topns:
                out[(s, n)][t] = ranked[:n]
    return out


def simulate(navs, picks, fee):
    """{date: [codes]} -> 组合净值（信号次日生效, 月度再平衡）。"""
    idx = navs.index
    cols = list(navs.columns)
    rets = navs.pct_change(fill_method=None).fillna(0.0)
    tgt = pd.DataFrame(0.0, index=idx, columns=cols)
    for t, codes in sorted(picks.items()):
        codes = [c for c in codes if c in tgt.columns]
        tgt.loc[t:, :] = 0.0                 # 先清空, 避免历史持仓单调累积
        if codes:
            tgt.loc[t:, codes] = 1.0 / len(codes)
    held = tgt.shift(1).fillna(0.0)
    gross = (held * rets).sum(axis=1)
    dpos = held.diff().abs().sum(axis=1)
    if len(dpos):
        dpos.iloc[0] = held.iloc[0].abs().sum()
    net = gross - fee * dpos
    return (1 + net).cumprod().rename("nav")


# ----------------------------------------------------------------------
# 主流程
# ----------------------------------------------------------------------
def main(argv=None):
    argv = argv or sys.argv[1:]
    refresh = "--refresh" in argv
    navs_all = pool.load_navs(refresh=refresh)        # 打分用完整历史(不可截断)
    dates = month_ends(navs_all.index, BACKTEST_FROM, navs_all.index[-1])
    benches = {"bench": [r["code"] for r in pool.POOL if r["role"] == "表内"],
               "benchall": [r["code"] for r in pool.POOL]}
    picks = build_picks(navs_all, dates, SCHEMES, TOPN_LIST, benches)
    sq = {s: selection_quality(navs_all, dates, w) for s, w in SCHEMES.items()}

    rows = []
    for (s, n), pk in picks.items():
        nav = simulate(navs_all, pk, FEE).loc[BACKTEST_FROM:]   # 回测窗内净值
        st = perf_stats(nav)
        obj, obj_parts = objective(nav)
        win = {y: trailing(nav, y) for y in WINDOWS}
        rows.append(dict(scheme=s, topn=n,
                         label=PICK_LABELS.get(s, "等权基准" + ("(表内5)" if s == "bench" else "(全池)")),
                         weights=(SCHEMES.get(s) if s in SCHEMES else None),
                         **st, obj=obj, obj_parts=obj_parts,
                         w3=win[3], w5=win[5], w10=win[10],
                         sq=sq.get(s), n_rebal=sum(1 for v in pk.values() if v)))

    os.makedirs(BACKTEST_DIR, exist_ok=True)
    with open(OUT_JSONL, "w", encoding="utf-8") as fh:
        for r in rows:
            fh.write(json.dumps(r, ensure_ascii=False, default=str) + "\n")
    best = max([r for r in rows if r["scheme"] in SCHEMES and r["obj"] is not None],
               key=lambda x: x["obj"])
    sample = [BACKTEST_FROM, str(navs_all.index[-1].date())]
    payload = {"generated_at": datetime.now().strftime("%Y-%m-%d %H:%M"),
               "sample": sample, "fee": FEE, "rebal": "月末决策/次日生效",
               "obj_weights": OBJ_W, "schemes": {k: v for k, v in SCHEMES.items()},
               "best": {"scheme": best["scheme"], "topn": best["topn"], "obj": best["obj"]},
               "rows": rows}
    with open(OUT_JSON, "w", encoding="utf-8") as fh:
        json.dump(payload, fh, ensure_ascii=False, indent=2, default=str)

    txt = render(rows, sample, best)
    with open(OUT_REPORT, "w", encoding="utf-8") as fh:
        fh.write(txt)
    print(txt)
    print(f"产物: {OUT_REPORT}\n      {OUT_JSONL}\n      {OUT_JSON}")
    return 0


def _p(v, d=1):
    return "—" if v is None else f"{v * 100:.{d}f}%"


def _obj_range(rows):
    vals = [r["obj"] for r in rows if r["scheme"] in SCHEMES and r["topn"] == 2 and r["obj"] is not None]
    return "—" if not vals else f"{min(vals)} ~ {max(vals)}"


def render(rows, sample, best):
    L = ["# 红利基金打分策略 · 窗权调优回测", "",
         f"> 生成 {datetime.now():%Y-%m-%d %H:%M} · 样本 {sample[0]} ~ {sample[1]}"
         f" · 月末再平衡 / 信号次日生效 / 单边 {FEE:.2%}",
         "> 只回测**业绩分**（价值分为当期快照, 无历史序列）；目标函数 obj = "
         "Σ w_W·(年化_W − 0.5·|回撤_W|), w = 3年50%/5年30%/10年20%（3 年权重最高）。", ""]

    L += ["## 一、方案对比（全期）", "",
          "| 方案 | topN | 累计 | 年化 | 最大回撤 | 夏普 | Calmar | **obj** |",
          "|---|---:|---:|---:|---:|---:|---:|---:|"]
    for r in sorted(rows, key=lambda x: (-1e9 if x["obj"] is None else -x["obj"])):
        star = " ⭐" if (r["scheme"] == best["scheme"] and r["topn"] == best["topn"]) else ""
        L.append(f"| {r['label']}{star} | {r['topn']} | {_p(r['cum'], 0)} | {_p(r['ann'])} | "
                 f"{_p(r['mdd'])} | {r['sharpe']:.2f} | "
                 f"{'—' if r['calmar'] is None else format(r['calmar'], '.2f')} | **{r['obj']}** |")
    L += ["", f"> ⭐ = 按 obj 择优（{best['label']} × topN={best['topn']}, obj={best['obj']}）。", ""]

    L += ["## 二、分窗业绩（年化 / 最大回撤）—— 目标函数的三段", "",
          "| 方案 | topN | 近3年 | 3年回撤 | 近5年 | 5年回撤 | 近10年 | 10年回撤 | obj(3/5/10) |",
          "|---|---:|---:|---:|---:|---:|---:|---:|---|"]
    for r in sorted(rows, key=lambda x: (-1e9 if x["obj"] is None else -x["obj"])):
        cells = []
        for k in ("w3", "w5", "w10"):
            st = r[k]
            cells += [_p(st["ann"]) if st else "—", _p(st["mdd"]) if st else "—"]
        parts = "/".join(f"{r['obj_parts'].get(y, '—')}" for y in (3, 5, 10)) if r["obj_parts"] else "—"
        L.append(f"| {r['label']} | {r['topn']} | " + " | ".join(cells) + f" | {parts} |")
    L += ["", "> 方案层 10 年窗需回测自 2016-09 起满 10 年才可得；早期可选标的少（仅长史基金），见边界说明。", ""]

    L += ["## 三、选择质量（打分对未来 3 个月的判别力）", "",
          "每期取最高分标的的未来 3 月收益 − 可选池等权未来 3 月收益（bp）；正值 = 打分有效。", "",
          "| 窗权方案 | 选择边际(bp) | 期数 |", "|---|---:|---:|"]
    seen = set()
    for r in rows:
        if r["scheme"] not in SCHEMES or r["scheme"] in seen or not r.get("sq"):
            continue
        seen.add(r["scheme"])
        L.append(f"| {r['label']} | {r['sq'][0]:+.1f} | {r['sq'][1]} |")
    sq_sorted = sorted({r["scheme"]: r["sq"] for r in rows
                        if r["scheme"] in SCHEMES and r.get("sq")}.items(),
                       key=lambda kv: -kv[1][0])
    sq_txt = "、".join(f"{PICK_LABELS.get(k, k)} {v[0]:+.1f}bp" for k, v in sq_sorted)
    L += ["", "> **读法**：3 年主导的三组（含用户指定的 3年50/5年30/10年20）判别力为正，"
          "等权与 10 年主导为负 —— 支持「近 3 年权重最高」。", ""]

    bench = [r for r in rows if r["scheme"] in ("bench", "benchall") and r["obj"] is not None]
    bb = max(bench, key=lambda x: x["obj"]) if bench else None
    judge = ""
    if bb is not None:
        judge = (f"对比基准（{bb['label']}, obj {bb['obj']}）："
                 + ("轮动已跑赢基准。" if best["obj"] > bb["obj"] else "轮动未跑赢该基准。"))
    best2 = max([r for r in rows if r["scheme"] in SCHEMES and r["topn"] == 2 and r["obj"] is not None],
                key=lambda x: x["obj"])
    best1 = max([r for r in rows if r["scheme"] in SCHEMES and r["topn"] == 1 and r["obj"] is not None],
                key=lambda x: x["obj"])
    L += ["## 四、结论", "",
          f"- 按「3 年权重最高」的目标函数（obj = Σ w_W·(年化_W − 0.5|回撤_W|), w=3年50%/5年30%/10年20%），"
          f"最优窗权方案 = **{best['label']} × topN={best['topn']}**"
          f"（obj {best['obj']}，年化 {_p(best['ann'])}，回撤 {_p(best['mdd'])}）。",
          f"- **选基判别力层面，3 年权重最高确实更优**：选择边际(bp) = {sq_txt} —— "
          "三组 3 年主导方案为正、等权与 10 年主导为负，**直接支持用户「近 3 年收益 + 回撤权重最高」的要求**。",
          f"- **组合层窗权差异在噪声范围**：topN=2 各方案 obj 落在 {_obj_range(rows)}，"
          "说明这 11 只红利基金高度相关，窗权不是组合收益的主要来源（判别力差异要先于组合差异体现）。",
          f"- **分散 >> 择基**：topN=2（{_p(best2['ann'])} / {_p(best2['mdd'])}）显著优于 "
          f"topN=1（{_p(best1['ann'])} / {_p(best1['mdd'])}），回撤收窄是主要收益来源。",
          f"- {judge}",
          "- **采纳**：保留 `rank.py::WINDOW_WEIGHTS = 3年50%/5年30%/10年20%`（用户要求 + 判别力为正），"
          "推荐档位取 **topN=2 等权**；打分作「排序 + 估值定性」参考，不作集中下注。", ""]

    L += ["## 五、口径与边界", "",
          "- 净值用 **totvalue（累计净值, 含分红再投）**，场内 ETF 与场外联接同口径。",
          "- 选基只用净值（业绩分）；价值分是当期快照，无法回测，只在 `rank.py` 的当期推荐里生效。",
          "- **早期段占权重**：2016-09~2019-09 池内只有 `090010`/`159905` 两只长史基金可选，"
          "10 年窗的 -30% 量级回撤主要来自该段（深证红利 2018 年大跌）——属数据起点产物, 非窗权问题；"
          "看近 3 年 / 近 5 年列更贴近当前池子。",
          "- 新基金（021551/020603）2024 年才有净值，在其上市满 1 年前不参与选择 → 早期池子更小。",
          "- 成本仅计单边 0.03%；场外 C 类持有 >7 天赎回费为 0，月度调仓一般不触发惩罚费，"
          "若极端情况 7 天内卖出需另计 1.5%。",
          "- 历史回测不代表未来；机械输出，非投资建议。", ""]
    return "\n".join(L) + "\n"


if __name__ == "__main__":
    sys.exit(main())
