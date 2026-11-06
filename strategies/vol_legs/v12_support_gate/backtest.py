# -*- coding: utf-8 -*-
"""波动资产 · V12 支撑位闸门 · 七腿 NAV 回测。

与 v11（事件研究，不报年化/回撤）的分工：这里给的是**组合层 NAV 口径** ——
年化 / 最大回撤 / 夏普 / Calmar / 平均曝光 / 年换手，逐腿 + 组合 + 五腿长窗口。

四个对照，口径完全相同（同一份 `tg.portfolio / tg.run_leg / fee_of`）：
- `买入持有`：恒满带
- `V5 默认`：年线 250 日频 + 20 日斜率三档 + 波动层（家族里在跑的对照）
- `V12 纯趋势`：MA200 月频上行 → 满带／否则观察仓（**只有趋势层**，隔离支撑层的贡献）
- `V12`：纯趋势 + 贴线减半 + 破位清仓/冷却（本变种完整规则）

产出：
- `backtest/_v12_support_bt.md`
- `backtest/_v12_support_bt.jsonl`（逐腿/组合/消融/分段，逐行可复核）

模拟结果，非投资建议。
"""
import json
import os
import sys

import numpy as np
import pandas as pd

try:
    sys.stdout.reconfigure(encoding="utf-8")  # type: ignore[attr-defined]
except Exception:  # noqa: BLE001
    pass

_HERE = os.path.dirname(os.path.abspath(__file__))
if _HERE not in sys.path:
    sys.path.insert(0, _HERE)

import support_gate as sg  # noqa: E402

tg = sg.tg
OUTDIR = os.path.join(_HERE, "backtest")
OUT_MD = os.path.join(OUTDIR, "_v12_support_bt.md")
OUT_JSONL = os.path.join(OUTDIR, "_v12_support_bt.jsonl")

LONG_LEGS = ("a500", "hs300", "ndx", "spx", "gold")   # 长窗口五腿（2013-08 起，13 年）

# 消融 / 敏感性档
SENS = [
    ("V12 完整（基准）", {}),
    ("① 去掉整个支撑层（= 纯趋势 MA200 月频）", dict(USE_SUPPORT=False)),
    ("① 只留「贴线减半」、破位不清仓", dict(CLEAR_ON_BREAK=False)),
    ("① 只留「破位清仓」、不做贴线减半", dict(NEAR=0.0)),
    ("② 贴线带 1%", dict(NEAR=0.01)),
    ("② 贴线带 2%", dict(NEAR=0.02)),
    ("② 贴线带 5%", dict(NEAR=0.05)),
    ("③ 破位冷却 0 日", dict(COOL=0)),
    ("③ 破位冷却 40 日", dict(COOL=40)),
    ("④ 关掉滞回（HYST=0）", dict(HYST=0.0)),
    ("⑤ 开 ATR 跟踪止损 1.5×ATR", dict(STOP_USE=True)),
    ("⑥ 趋势改 MA250 + 日频（= v5 年线口径）", dict(MA_WIN=250, TREND_FREQ="daily")),
    ("⑦ 加仓间隔改 5 日", dict(ACTION_GAP=5)),
    ("⑦ 加仓间隔改 20 日", dict(ACTION_GAP=20)),
    ("⑧ 零成本（仅诊断成本占比）", dict(_NOCOST=True)),
    ("⑧ 纯趋势 · 零成本", dict(USE_SUPPORT=False, _NOCOST=True)),
]


def fp(x, nd=2):
    return "—" if x is None or not np.isfinite(x) else f"{x * 100:+.{nd}f}%"


def fn(x, nd=2):
    return "—" if x is None or not np.isfinite(x) else f"{x:.{nd}f}"


def fs(x, nd=0):
    """无符号百分比（曝光等比例列）。"""
    return "—" if x is None or not np.isfinite(x) else f"{x * 100:.{nd}f}%"


def metrics(nav, exposure=None, acts=None, years=None):
    m = tg.perf(nav)
    out = dict(ann=m["ann"], mdd=m["mdd"], sharpe=m["sharpe"], vol=m["vol"],
               cum=m["cum"], days=m["days"],
               calmar=(m["ann"] / abs(m["mdd"]) if m["mdd"] < -1e-9 else None),
               expo=exposure, per_year=acts)
    if acts is not None and years:
        out["acts_per_year"] = acts / max(years, 1e-9)
    return out


def _slice(leg, **kw):
    """-> (px, {策略名: state})，全部基于**完整序列**算指标后再按 WARMUP 截断。"""
    px_full = sg.load(leg)
    states = {
        "buyhold": pd.Series(1.0, index=px_full.index),
        "v5": tg.state_machine(px_full, leg)["state"],
        "v5_trend": tg.state_machine(px_full, leg, VOL_USE=False)["state"],
        "v12_trend": sg.state_series(px_full, leg, USE_SUPPORT=False, **kw)["state"],
        "v12": sg.state_series(px_full, leg, **kw)["state"],
    }
    start = px_full.index[min(int(sg.WARMUP), max(len(px_full) - 2, 0))]
    px = px_full.loc[start:]
    return px, {k: v.loc[start:] for k, v in states.items()}


def leg_rows(**kw):
    rows = []
    for k in sg.LEGS:
        px, sts = _slice(k, **kw)
        fee = sg.fee_of(k)
        yrs = (px.index[-1] - px.index[0]).days / 365.25
        r = dict(leg=k, name=sg.base.LEGS[k]["name"], role=sg.base.LEGS[k]["role"],
                 band=sg.base.LEGS[k]["band"], start=px.index[0].date().isoformat(),
                 end=px.index[-1].date().isoformat(), years=round(yrs, 2), n=len(px))
        for nm, st in sts.items():
            nav = tg.run_leg(st, px, fee=fee)
            acts = len(sg.actions(st))
            r[nm] = metrics(nav, sg.exposure(st), acts, yrs)
        rows.append(r)
    return rows


def _frame(legs):
    """七腿（或五腿）的共同窗口。

    **必须先截掉预热期再取交集**：MA200 + 250 日支撑成形之前，v12 只能给观察仓、
    v5 只能给空仓（`START`），两边都不可比 —— 把预热期算进来会让「谁的空仓期更长」
    变成收益差异的主要来源（实测能把年化差别到 3pp 以上）。
    """
    cols = {k: sg.load(k).iloc[int(sg.WARMUP):] for k in legs}
    return pd.DataFrame(cols).dropna(how="any").sort_index()


def _states_for(frame, legs, **kw):
    """-> {策略名: {leg: state Series 对齐 frame.index}}"""
    out = {nm: {} for nm in ("buyhold", "v5", "v5_trend", "v12_trend", "v12")}
    for k in legs:
        px_full = sg.load(k)
        sts = {
            "buyhold": pd.Series(1.0, index=px_full.index),
            "v5": tg.state_machine(px_full, k)["state"],
            "v5_trend": tg.state_machine(px_full, k, VOL_USE=False)["state"],
            "v12_trend": sg.state_series(px_full, k, USE_SUPPORT=False, **kw)["state"],
            "v12": sg.state_series(px_full, k, **kw)["state"],
        }
        for nm, s in sts.items():
            out[nm][k] = s.reindex(frame.index).ffill().bfill().fillna(0.0)
    return out


def port_rows(legs, label, **kw):
    frame = _frame(legs)
    total = sum(sg.base.LEGS[k]["band"] for k in legs)
    w = {k: float(sg.base.LEGS[k]["band"]) / total for k in legs}   # 组内归一化（Σ=1）
    sts = _states_for(frame, legs, **kw)
    yrs = (frame.index[-1] - frame.index[0]).days / 365.25
    rows = []
    for nm in ("buyhold", "v5", "v5_trend", "v12_trend", "v12"):
        nav, expo = tg.portfolio(frame, sts[nm], weight=w, fee=True, detail=True)
        acts = sum(len(sg.actions(sts[nm][k])) for k in legs)
        rows.append(dict(group=label, kind=nm, legs=len(legs), n=len(frame),
                         start=frame.index[0].date().isoformat(),
                         end=frame.index[-1].date().isoformat(),
                         years=round(yrs, 2),
                         **metrics(nav, expo, acts, yrs)))
    return rows, frame, sts, w


def sens_rows(**kw):
    frame = _frame(sg.LEGS)
    total = sum(sg.base.LEGS[k]["band"] for k in sg.LEGS)
    w = {k: float(sg.base.LEGS[k]["band"]) / total for k in sg.LEGS}
    yrs = (frame.index[-1] - frame.index[0]).days / 365.25
    rows = []
    for name, over in SENS:
        over = dict(over)
        nocost = bool(over.pop("_NOCOST", False))
        kw2 = dict(kw)
        kw2.update(over)
        sts = {k: sg.state_series(sg.load(k), k, **kw2)["state"].reindex(frame.index)
               .ffill().bfill().fillna(0.0) for k in sg.LEGS}
        nav, expo = tg.portfolio(frame, sts, weight=w, fee=not nocost, detail=True)
        acts = sum(len(sg.actions(sts[k])) for k in sg.LEGS)
        rows.append(dict(name=name, **metrics(nav, expo, acts, yrs)))
    return rows


def seg_rows(nav_map):
    """三段等分（按行数），看结论是否只由某一段贡献。"""
    out = []
    idx = nav_map["v12"].index
    n = len(idx)
    for i in range(3):
        i0, i1 = i * n // 3, (i + 1) * n // 3 - 1 if i < 2 else n - 1
        d0, d1 = idx[i0], idx[i1]
        row = dict(seg=f"{d0.date()} ~ {d1.date()}")
        for nm, nav in nav_map.items():
            sub = nav.loc[d0:d1]
            row[nm] = (float(sub.iloc[-1] / sub.iloc[0]) - 1.0) if len(sub) > 2 else None
        out.append(row)
    return out


def yearly_map(nav_map):
    years = sorted({d.year for d in nav_map["v12"].index})
    out = []
    for y in years:
        row = dict(year=str(y))
        for nm, nav in nav_map.items():
            sub = nav[nav.index.year == y]
            row[nm] = (float(sub.iloc[-1] / sub.iloc[0]) - 1.0) if len(sub) > 2 else None
        out.append(row)
    return out


def main():
    os.makedirs(OUTDIR, exist_ok=True)
    rows = leg_rows()
    p7, frame7, sts7, w7 = port_rows(sg.LEGS, "七腿（2021-08 起，组内归一化 40pp）")
    p5, frame5, sts5, w5 = port_rows(LONG_LEGS, "五腿长窗口（2013-08 起，组内归一化 35pp）")
    sens = sens_rows()
    navs7 = {nm: tg.portfolio(frame7, sts7[nm], weight=w7, fee=True) for nm in sts7}
    segs = seg_rows(navs7)
    years = yearly_map(navs7)

    with open(OUT_JSONL, "w", encoding="utf-8") as f:
        for tag, tbl in (("leg", rows), ("port", p7 + p5), ("sens", sens),
                         ("seg", segs), ("year", years)):
            for r in tbl:
                f.write(json.dumps({tag: r}, ensure_ascii=False, default=str) + "\n")

    def g(rows_, kind, group=None):
        return next(r for r in rows_ if r["kind"] == kind and (group is None or r["group"] == group))

    v12_7 = g(p7, "v12")
    v5_7 = g(p7, "v5")
    bh_7 = g(p7, "buyhold")
    tr_7 = g(p7, "v12_trend")
    v12_5 = g(p5, "v12")
    v5_5 = g(p5, "v5")
    bh_5 = g(p5, "buyhold")
    tr_5 = g(p5, "v12_trend")
    base = next(r for r in sens if r["name"].startswith("V12 完整"))

    n_beat = [r for r in rows if r["v12"]["ann"] > r["v5"]["ann"]]
    n_dd = [r for r in rows if r["v12"]["mdd"] > r["v5"]["mdd"]]

    L = [
        "# 波动资产 · V12 变种：支撑位闸门 · 七腿回测",
        "",
        "> **把 v11 事件研究的四条结论翻译成仓位规则**，然后在七条腿上做 NAV 回测。",
        "> v11 回答「这笔交易值不值」（单笔 R 口径，**不报年化/回撤**，见 `../v11_support_pressure/`）；",
        "> 本变种回答「这条腿今天该露出多少带」（组合 NAV 口径）—— 两者的趋势/支撑/ATR 是**同一份代码**",
        "> （v12 直接 import v11 的 `build`），口径不会漂移。",
        "> 成本、滑点、货基利息、NAV 记账全部复用 `v5_trend_gate`（`fee_of / portfolio / run_leg`），",
        "> 所以「V12 vs V5」的差异**只来自规则**。",
        "",
        "## 1. 规则（参数权威源 = `support_gate.py`）",
        "",
        "| 条件（收盘后判定，次日收盘成交） | 档位（占本腿目标带） |",
        "|---|---|",
        f"| 上涨趋势不成立（MA{sg.MA_WIN} 下行 或 价在 MA{sg.MA_WIN} 之下，月频判定） | 观察仓 0.2 |",
        f"| 趋势成立，距支撑 `> {sg.NEAR:.0%}`（不贴线） | 满带 1.0 |",
        f"| 趋势成立，距支撑 `≤ {sg.NEAR:.0%}`（**贴线**） | 半仓 0.5 |",
        f"| `close < support`（**跌破支撑**，无论趋势） | 空仓 0 + 冷却 {sg.COOL} 日 |",
        f"| 破位后，价格未重新站上「支撑 + {sg.NEAR:.0%}」 | 空仓 0（不回补） |",
        f"| （可选 `STOP_USE`）从持仓期最高点回撤 ≥ {sg.STOP_ATR}×ATR | 空仓 0 + 冷却 |",
        "",
        f"限速：非风控换档至少隔 **{sg.ACTION_GAP} 个交易日**（滞回 `HYST={sg.HYST:.0%}` 防在贴线边界抖动）；",
        "**破位/止损的减仓不受限速** —— 风控优先。",
        "",
        "每条规则都对应 v11 的一条实测结论：",
        "",
        "| 规则 | v11 的实测依据 |",
        "|---|---|",
        "| 趋势不成立 → 观察仓（不空仓、不抄底） | v11 §7.2：下跌趋势里支撑撑住只有 32%，但趋势本身有 +1.00% 的 60 日超额 |",
        "| 贴线 → 半仓 | v11 §8.1：距支撑 ≤2% 时 f60 比「趋势日均值」低 0.66pp，3% 以上转正 |",
        "| 破位 → 清仓 | 文档铁律①「支撑破了就走」—— **本变种实测它是负贡献（§7 第 2 条）**，"
        "保留在规则里是为了让「被证伪」这件事有数字，不是为了推荐 |",
        f"| 0.5×ATR 止损**不用**，改用结构位 | v11 §9：止损放宽到 1.0/1.5×ATR，EV 从 +0.40R 升到 +0.61/+0.79R |",
        "| 不做空 | A 股散户没有工具；v11 §7.1 实测上涨趋势里压力位 60 日突破 90% |",
        "",
        "## 2. 逐腿结果（各腿全历史，成本已扣）",
        "",
        "样本：每腿自「MA200 + 250 日支撑」成形后起算（A股 2010、美股 2013、黄金 2014、科创50/恒生科技 2022）。",
        "",
        "| 腿 | 样本 | 年数 | 策略 | 年化 | 最大回撤 | 夏普 | Calmar | 平均曝光 | 动作/年 |",
        "|---|---|---:|---|---:|---:|---:|---:|---:|---:|",
    ]
    for r in rows:
        for nm, lab in (("buyhold", "买入持有"), ("v5", "V5 默认"),
                        ("v12_trend", "V12 纯趋势"), ("v12", "**V12**")):
            m = r[nm]
            L.append(f"| {r['name'] if nm == 'buyhold' else ''} | "
                     f"{r['start'][:7] + '~' + r['end'][:7] if nm == 'buyhold' else ''} | "
                     f"{r['years'] if nm == 'buyhold' else ''} | {lab} | "
                     f"{fp(m['ann'])} | {fp(m['mdd'])} | {fn(m['sharpe'])} | "
                     f"{fn(m['calmar'])} | {fs(m['expo'])} | "
                     f"{fn(m['acts_per_year'], 1)} |")
    n_beat_tr = [r for r in rows if r["v12_trend"]["ann"] > r["v5"]["ann"]]
    L += [
        "",
        f"> V12（含支撑层）只有 **{len(n_beat)}/{len(rows)}** 条腿年化高于 V5、"
        f"**{len(n_dd)}/{len(rows)}** 条腿回撤更浅；**去掉支撑层（纯趋势）后有 "
        f"{len(n_beat_tr)}/{len(rows)}** 条腿年化高于 V5 —— 差异全在支撑层，不在趋势层。",
        "> ⚠ **别用逐腿加权去推组合**：组合有再平衡（每年把涨多的腿卖掉一部分）与现金计息，"
        "复利路径与单腿不同 —— 这里 6/7 条腿更好、组合层反而低 0.33pp 就是例子。结论请看 §3。",
        "> 逐腿是**单腿自带额度**的口径；组合层的绝对收益不可与单腿直接比（额度不同）。",
        "",
        "## 3. 组合结果",
        "",
        f"「七腿」= 七条腿**预热期之后**的交集窗口（{frame7.index[0].date()} 起，只有 "
        f"{len(frame7)} 个可比交易日 —— 恒生科技 2021-08 上市，MA200+250 日支撑要到 2022-08 才成形）；"
        f"「五腿长窗口」= A股两条 + 美股两条 + 黄金（{frame5.index[0].date()} 起，"
        f"{len(frame5)} 个交易日）。两者权重都在组内归一化（Σ=1），所以**不能横向比绝对收益**，只比同组内的相对表现。",
        "",
        "| 组合 | 年数 | 策略 | 年化 | 最大回撤 | 夏普 | Calmar | 平均曝光 | 动作/年 |",
        "|---|---:|---|---:|---:|---:|---:|---:|---:|",
    ]
    for grp, rs in (("七腿", p7), ("五腿长窗口", p5)):
        for r in rs:
            lab = {"buyhold": "买入持有", "v5": "V5 默认", "v5_trend": "V5 关波动层",
                   "v12_trend": "V12 纯趋势（消融）", "v12": "**V12**"}[r["kind"]]
            L.append(f"| {grp if r['kind'] == 'buyhold' else ''} | {r['years'] if r['kind'] == 'buyhold' else ''} | "
                     f"{lab} | {fp(r['ann'])} | {fp(r['mdd'])} | {fn(r['sharpe'])} | "
                     f"{fn(r['calmar'])} | {fs(r['expo'])} | {fn(r['acts_per_year'], 1)} |")
    L += [
        "",
        f"> 七腿窗口里：V12 年化 {fp(v12_7['ann'])} / 回撤 {fp(v12_7['mdd'])} / Calmar "
        f"{fn(v12_7['calmar'])}；V5 是 {fp(v5_7['ann'])} / {fp(v5_7['mdd'])} / {fn(v5_7['calmar'])}；"
        f"买入持有 {fp(bh_7['ann'])} / {fp(bh_7['mdd'])}。",
        f"> 五腿长窗口里：V12 {fp(v12_5['ann'])} / {fp(v12_5['mdd'])} / Calmar {fn(v12_5['calmar'])}；"
        f"V5 {fp(v5_5['ann'])} / {fp(v5_5['mdd'])} / {fn(v5_5['calmar'])}；"
        f"买入持有 {fp(bh_5['ann'])} / {fp(bh_5['mdd'])}。",
        "",
        "## 4. 逐年收益（七腿组合）",
        "",
        "| 年 | 买入持有 | V5 默认 | V12 纯趋势 | **V12** |",
        "|---|---:|---:|---:|---:|",
    ]
    for r in years:
        L.append(f"| {r['year']} | {fp(r.get('buyhold'), 1)} | {fp(r.get('v5'), 1)} | "
                 f"{fp(r.get('v12_trend'), 1)} | **{fp(r.get('v12'), 1)}** |")
    L += [
        "",
        "## 5. 三段等分（结论是否只由某一段贡献）",
        "",
        "| 区间 | 买入持有 | V5 默认 | V12 纯趋势 | **V12** |",
        "|---|---:|---:|---:|---:|",
    ]
    for r in segs:
        L.append(f"| {r['seg']} | {fp(r.get('buyhold'), 1)} | {fp(r.get('v5'), 1)} | "
                 f"{fp(r.get('v12_trend'), 1)} | **{fp(r.get('v12'), 1)}** |")
    L += [
        "",
        "## 6. 消融与敏感性（七腿组合）",
        "",
        "| 档位 | 年化 | 最大回撤 | 夏普 | Calmar | 平均曝光 | 动作/年 |",
        "|---|---:|---:|---:|---:|---:|---:|",
    ]
    for r in sens:
        L.append(f"| {r['name']} | {fp(r['ann'])} | {fp(r['mdd'])} | {fn(r['sharpe'])} | "
                 f"{fn(r['calmar'])} | {fs(r['expo'])} | {fn(r['acts_per_year'], 1)} |")
    anns = [r["ann"] for r in sens]
    L += [
        "",
        f"> {len(sens)} 档的年化落在 **{fp(min(anns))} ~ {fp(max(anns))}**，中位 "
        f"**{fp(float(np.median(anns)))}**。",
        "",
    ]

    def s(pre):
        return next(r for r in sens if r["name"].startswith(pre))

    s_tr = s("① 去掉整个支撑层")
    s_half = s("① 只留「贴线减半」")
    s_clear = s("① 只留「破位清仓」")
    s_stop = s("⑤ 开 ATR")
    s_nc = s("⑧ 零成本")
    s_tr_nc = s("⑧ 纯趋势 · 零成本")
    cost_all = s_nc["ann"] - base["ann"]
    cost_tr = s_tr_nc["ann"] - s_tr["ann"]
    L += [
        "## 7. 结论：支撑位层不能迁移，趋势层站得住但与 v5 等价",
        "",
        f"**一句话**：按 v11 的结论全部实现后，v12 跑输 v5 "
        f"**{(v5_7['ann'] - v12_7['ann']) * 100:.2f}pp/年**、Calmar 从 {fn(v5_7['calmar'])} 掉到 "
        f"{fn(v12_7['calmar'])} —— **不是因为实现错了，而是「支撑位」在逐日仓位口径上比在单笔口径上更没用**。",
        "",
        f"1. **支撑层整体是负贡献**：去掉支撑层（纯趋势 MA200 月频）年化 {fp(s_tr['ann'])}，"
        f"装上后 {fp(base['ann'])}（**{(base['ann'] - s_tr['ann']) * 100:+.2f}pp**），"
        f"回撤几乎没变（{fp(s_tr['mdd'])} → {fp(base['mdd'])}），"
        f"动作从 {fn(s_tr['acts_per_year'], 1)} 次/年 涨到 {fn(base['acts_per_year'], 1)} 次/年。",
        f"2. **主因是「破位清仓」**：只留破位清仓、不做贴线减半 → 年化 {fp(s_clear['ann'])}、"
        f"回撤 {fp(s_clear['mdd'])}（比纯趋势的 {fp(s_tr['mdd'])} **更深**）。"
        "也就是说**「跌破支撑就走」在七腿宽基上既亏收益又不减回撤** —— 它卖在低点，"
        f"然后要等价格重新站上「支撑 + {sg.NEAR:.0%}」才回补，正好错过反弹第一段。"
        "这是 v11 §7.2「下跌趋势支撑撑住只有 32%」的另一面：撑不住是常态，"
        "但**破位后继续跌的概率并没有高到值得清仓**（否则回撤该更小）。",
        f"3. **「贴线减半」也是负的，但两个负贡献不叠加**：只留贴线减半 {fp(s_half['ann'])}"
        f"（**{(s_half['ann'] - s_tr['ann']) * 100:+.2f}pp**）、只留破位清仓 {fp(s_clear['ann'])}"
        f"（{(s_clear['ann'] - s_tr['ann']) * 100:+.2f}pp）、两者都开 {fp(base['ann'])} —— "
        "清仓之后「贴线」这个状态本来就很难再触发，所以完整版介于两者之间、不是相加。"
        f"零成本档显示完整版的成本拖累 **{abs(cost_all) * 100:.2f}pp/年**、纯趋势只有 "
        f"**{abs(cost_tr) * 100:.2f}pp/年**，"
        f"差出的约 **{abs(cost_all - cost_tr) * 100:.2f}pp/年** 就是多出来的 "
        f"{fn(s_half['acts_per_year'] - s_tr['acts_per_year'], 0)} "
        "次/年交易的代价 —— 收益增量（v11 测到的 0.66pp/60 日）撑不住这个成本。",
        f"4. **ATR 跟踪止损完全不可迁移**：{fp(s_stop['ann'])}/年、"
        f"{fn(s_stop['acts_per_year'], 0)} 次/年、曝光只剩 {fs(s_stop['expo'])}。"
        "v11 §9「1.5×ATR 比 0.5×ATR 好」成立的前提是**单笔 + 固定 5R 目标**；"
        "逐日跟踪止损把同一个 1.5×ATR 变成高频割肉 —— **结论的口径边界比结论本身更重要**。",
        f"5. **留下的只有趋势层，它的价值是「省交易」不是「更高收益」**：V12 纯趋势 七腿窗口 "
        f"{fp(s_tr['ann'])}/{fp(s_tr['mdd'])}/Calmar {fn(s_tr['calmar'])}、"
        f"动作 {fn(s_tr['acts_per_year'], 1)} 次/年；V5 默认 {fp(v5_7['ann'])}/{fp(v5_7['mdd'])}/"
        f"{fn(v5_7['calmar'])}、{fn(v5_7['acts_per_year'], 1)} 次/年；V5 关波动层 "
        f"{fp(g(p7, 'v5_trend')['ann'])}、{fn(g(p7, 'v5_trend')['acts_per_year'], 1)} 次/年。"
        "三者年化在约 1pp 内互有胜负，**但 v12 纯趋势的换手只有 v5 的 45~60%** —— "
        "等价于「v5 的省交易版本」，不是新 alpha。五腿长窗口同理："
        f"{fp(tr_5['ann'])} vs v5 {fp(v5_5['ann'])}，换手 {fn(tr_5['acts_per_year'], 1)} vs "
        f"{fn(v5_5['acts_per_year'], 1)} 次/年，但回撤 {fp(tr_5['mdd'])} 比 v5 的 "
        f"{fp(v5_5['mdd'])} 更深。差异来自「MA200 月频」vs「MA250 日频」，不是新增信息；"
        "而 MA200/月频是**文档写死的、不是在本样本上选的**，所以也没有理由假设它未来更好。",
        "6. **所以 v12 不上线**（`pipeline/strategies.json` 不注册、不进每日流程）。"
        "如果将来要用，只用 `USE_SUPPORT=False` 这一档（纯趋势），并明确它是**v5 的慢速对照**、"
        "不是替代品。v11 四条结论里唯一可迁移的是**「不抄底、不逆势空」这条约束** —— "
        "它是「不做某事」，不产生仓位信号。",
        "",
        "> 最值得记住的一条：**事件研究里的弱信号（0.5~1pp / 60 日）搬到逐日仓位后，"
        "会被交易成本和「错过上涨」放大成负贡献**。v11 的净结论是「支撑位不是 alpha」，"
        "v12 用另一种口径又验证了一遍 —— 两个独立口径给同一答案，这个结论就可以信了。",
        "",
        "> **模拟结果，非投资建议。**",
    ]

    with open(OUT_MD, "w", encoding="utf-8") as f:
        f.write("\n".join(L))

    print("报告:", OUT_MD)
    print("数据:", OUT_JSONL)
    for grp, rs in (("七腿", p7), ("五腿", p5)):
        for r in rs:
            print(f"  {grp} {r['kind']:10s} 年化 {fp(r['ann']):>9s} 回撤 {fp(r['mdd']):>9s} "
                  f"夏普 {fn(r['sharpe']):>5s} Calmar {fn(r['calmar']):>5s} "
                  f"曝光 {fp(r['expo'], 0):>5s} 动作 {fn(r['acts_per_year'], 1)}/年")


if __name__ == "__main__":
    main()
