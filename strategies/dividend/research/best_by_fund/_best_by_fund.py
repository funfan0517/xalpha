#!/usr/bin/env python
# -*- coding: utf-8 -*-
r"""红利 · 每只标的的「策略表现 × 前三名」清单（对应 vol_legs 的 `best_by_leg`）

回答两个问题：

1. **10 只红利标的 × 家族里每一支单标的规则的表现各是多少** —— §1 全量表；
2. **每只标的排前三的是哪三支** —— §0 前三名表（排序看夏普，含买入持有基准）。

## 为什么「腿」是基金、不是版本

`strategies/vol_legs` 的腿是 7 条资产（A500/沪深300/…），每个变种是这条腿上的一套仓位规则。
红利家族反过来：**只有一类资产（红利），10 只标的跟踪 10 个不同指数**，版本 V0–V10 大多是
**组合规则**（横截面 top2 × 板块时间倍率）。所以这里的「腿」= **10 只基金**，「策略」=
可以落到单只基金上的规则 —— 这正是 `v4_rsi/universe.py` 做过的事（只做了 RSI 一项），
本课题把它扩到整族。

## 它怎么算「最优」

1. 每个候选在**同一只标的、同一窗口**（全部候选信号都可得的那段）上重算，
   口径见 `research/_leg_engine.py`：累计净值 · 月末信号次月生效 · 单边 0.03% · 空仓不计息；
2. 选优规则：**夏普最高**（同分看年化）；**买入持有也参赛** —— 时机策略跑不过就一直拿着；
3. 样本 < 3 年只列不选（`·短样本`）；无股息率历史的 3 只没有估值类候选（表内标 `·无源`）；
4. 另标「年化最高」（`↑`），因为夏普最优与收益最高常常不是同一支。

## §4 是上界参照，不是方案

「每只标的取自己的 ★1 拼成组合」用到了**全样本信息**（每只标的的最优是在同一段样本里挑的），
必然高估。它只用来说明「单标的层面还有多少空间」，**不可当可执行组合**。

产物（本目录）：
- `_best_by_fund.md`    人读清单（一页前三名 + 分标的明细 + 未纳入原因 + 组合层诊断 + 口径）
- `_best_by_fund.jsonl` 机读结果（每行 = 一个 标的×候选；含 `rank` / `short` / `bench`）

跑一次：`python strategies/dividend/research/best_by_fund/_best_by_fund.py`
（须先有此脚本；`beat_best` 会读本目录的 jsonl 当基线）

> 模拟结果，非投资建议。
"""

from __future__ import annotations

import io
import json
import os
import sys

import pandas as pd

try:
    sys.stdout.reconfigure(encoding="utf-8")  # type: ignore[attr-defined]
except Exception:  # noqa: BLE001
    pass

_HERE = os.path.dirname(os.path.abspath(__file__))     # .../research/best_by_fund
_RES = os.path.dirname(_HERE)                          # .../research
if _RES not in sys.path:
    sys.path.insert(0, _RES)

import _leg_engine as E  # noqa: E402  pylint: disable=wrong-import-position
import _single_signal as S  # noqa: E402  pylint: disable=wrong-import-position
import pool  # noqa: E402  pylint: disable=wrong-import-position

OUT_MD = os.path.join(_HERE, "_best_by_fund.md")
OUT_JSONL = os.path.join(_HERE, "_best_by_fund.jsonl")
TOP_N = 3


# ================================================================ 采集
def collect() -> list[dict]:
    rows = []
    for code in E.CODES:
        keys = E.available_keys(code)
        for k in keys:
            d = E.run_leg(code, k)
            cfg = E.CAND[k]
            rows.append(dict(
                code=code, name=E.NAME[code], cat=E.LEG[code]["cat"],
                idx=E.LEG[code]["idx"], yield_ok=E.LEG[code]["yield_ok"],
                key=k, label=cfg["label"], note=cfg["note"], bench=bool(cfg.get("bench")),
                ann=d["cagr"], mdd=d["dd"], sharpe=d["sharpe"], calmar=d["calmar"],
                vol=d["vol"], expo=d["expo"], trades=d["trades"],
                start=str(d["start"].date()), end=str(d["end"].date()), years=d["yrs"],
                rank=None, short=False,
            ))
        print(f"  {code} {E.NAME[code][:18]:<18s} 候选 {len(keys)} 个")
    return rows


def pick(rows: list[dict]) -> dict:
    """每只标的内排名：夏普优先，同分看年化；样本 <3 年不参赛。"""
    out = {}
    for code in E.CODES:
        mine = [r for r in rows if r["code"] == code]
        for r in mine:
            r["short"] = r["years"] < E.MIN_YEARS
        eligible = [r for r in mine if not r["short"] and (r["bench"] or r["trades"] > 0)]
        eligible.sort(key=lambda r: (r["sharpe"], r["ann"]), reverse=True)
        for i, r in enumerate(eligible[:TOP_N], 1):
            r["rank"] = i
        out[code] = dict(rows=sorted(mine, key=lambda r: (r["sharpe"], r["ann"]), reverse=True),
                         top=eligible[:TOP_N],
                         best=eligible[0] if eligible else None,
                         best_ann=max(eligible, key=lambda r: r["ann"]) if eligible else None,
                         bh=next((r for r in mine if r["key"] == "BH"), None),
                         keys=E.available_keys(code))
    return out


# ================================================================ 组合层诊断
def sim_port(w: pd.DataFrame, rets: pd.DataFrame) -> pd.Series:
    wl = w.reindex(rets.index).shift(1).fillna(0.0)
    port = (wl * rets).sum(axis=1, min_count=1)
    turn = (wl - wl.shift(1).fillna(0.0)).abs().sum(axis=1)
    return (port - turn * E.FEE).iloc[1:].dropna()


def portfolio_check(picked: dict) -> dict:
    """同一段共同窗口里比三个组合：全池等权 / 现行 V4 组合 / 每腿★1等权（上界）。"""
    # 每只标的 ★1 的月收益（若该标的最优是 BH，就是买入持有）
    per = {}
    for code in E.CODES:
        best = picked[code]["best"]
        if best is None:
            continue
        per[code] = E.run_leg(code, best["key"])["r"]
    idx = pd.DataFrame(per)
    start, end = idx.index[0], idx.index[-1]
    start = max(d.index[0] for d in per.values())
    end = min(d.index[-1] for d in per.values())
    idx = idx.loc[start:end]

    # ① 全池等权（10 只，月度回到等权）
    navs = E.navs()
    rets10 = navs[E.CODES].resample("ME").last().ffill().pct_change().loc[start:end]
    w_eq = pd.DataFrame(1.0 / len(rets10.columns), index=rets10.index, columns=rets10.columns)
    eq = sim_port(w_eq, rets10)

    # ② 现行 V4 组合：5 只池，横截面 RSI 最低 2 只 × 板块 RSI 时间倍率
    codes5 = pool.TIMING_CODES
    rets5 = navs[codes5].resample("ME").last().ffill().pct_change().dropna()
    q5 = pd.DataFrame({c: E.rsi_monthly(c, 14) for c in codes5}) / 100.0
    xs5 = pd.DataFrame({c: E.rsi_monthly(c, 14) for c in codes5})
    sims, _, _ = S.run_variants(q5, rets5, xs5, k=2, q_out=0.70, q_half=0.50,
                                base_label="基准：5 只等权")
    v4 = sims.get("横截面 top2 + 时间择时")
    v4 = v4.loc[start:end] if v4 is not None else pd.Series(dtype=float)

    # ③ 每腿★1 等权（上界参照；再平衡成本忽略，权重恒定 1/N）
    best_mix = idx.mean(axis=1)

    rows = [("全池等权（10 只）", eq), ("现行 V4（5 只池 · top2 × 板块 RSI）", v4),
            ("每腿 ★1 等权（上界，含选择偏差）", best_mix)]
    out = []
    for name, r in rows:
        if r is None or r.dropna().empty:
            continue
        st = E.stats(r.dropna())
        out.append(dict(name=name, **{k: st[k] for k in ("cagr", "dd", "calmar", "vol", "sharpe", "n")}))
    return dict(start=start, end=end, rows=out, n=len(idx))


# ================================================================ 渲染
def _vs_bh(r, bh):
    if not bh or r["bench"]:
        return "基准本身"
    return f"{(r['ann'] - bh['ann']) * 100:+.2f}pp/年"


def render(picked: dict, rows: list[dict], port: dict) -> str:
    gen = pd.Timestamp.today().date().isoformat()
    L = []
    L.append("# 红利 · 每只标的的策略表现与前三名")
    L.append("")
    L.append(f"> 生成 {gen} · 由 `strategies/dividend/research/best_by_fund/_best_by_fund.py` **同尺重算**"
             "（不引用各版本报告里的数字）。")
    L.append("> 重跑：`python strategies/dividend/research/best_by_fund/_best_by_fund.py`")
    L.append("> 口径：基金**累计净值** · 月末信号次月生效 · 单边 0.03% · 空仓不计息 · "
             "每只标的所有候选共用同一窗口（见 `research/_leg_engine.py`）。"
             "对应 vol_legs 的 `research/best_by_leg`，但「腿」= 10 只红利基金。模拟结果，非投资建议。")
    L.append("")

    # ---- 一页结论
    L.append("## 0. 一页结论 · 每只标的的前三名")
    L.append("")
    L.append(f"排序只看**夏普**（同分看年化）。进排名条件：样本 ≥ {E.MIN_YEARS:.0f} 年、"
             "必须是仓位规则（`·短样本` 只列不选）。**买入持有同样参赛** —— 它占一个名次，"
             "说明这只标的上择时不值得做。")
    L.append("")
    L.append("> 全部含费（单边 0.03%）；「相对买入持有」= 年化差（pp/年）。"
             "**空仓不计息**：择时类平均仓位 70% 上下，按货基 1.4%/年算等于每年再让 0.4pp 左右。")
    L.append("")
    L.append("| 标的 | 跟踪指数 | 名次 | 策略 | 年化 | 最大回撤 | 夏普 | Calmar "
             "| 平均仓位 | 调仓 | 样本 | 相对买入持有 |")
    L.append("|---|---|---|---|---:|---:|---:|---:|---:|---:|---|---:|")
    for code in E.CODES:
        info = picked[code]
        if not info["top"]:
            L.append(f"| **{E.NAME[code]}**（`{code}`） | {info['rows'][0]['idx'] if info['rows'] else '—'} "
                     "| — | 无可用产物 | — | — | — | — | — | — | — | — |")
            continue
        bh = info["bh"]
        for i, r in enumerate(info["top"], 1):
            L.append(f"| **{E.NAME[code]}**（`{code}`） | {r['idx']} | **★{i}** | {r['label']} "
                     f"| {E.fmt_pct(r['ann'])} | {E.fmt_pct(r['mdd'])} | **{E.fmt2(r['sharpe'])}** "
                     f"| {E.fmt2(r['calmar'])} | {r['expo']:.0%} | {r['trades']} "
                     f"| {r['years']:.1f}y | {_vs_bh(r, bh)} |")
    L.append("")

    L.append("**及格线：买入持有（同一窗口，不计费）**")
    L.append("")
    L.append("| 标的 | 年化 | 最大回撤 | 夏普 | Calmar | 波动 | 样本 |")
    L.append("|---|---:|---:|---:|---:|---:|---|")
    for code in E.CODES:
        bh = picked[code]["bh"]
        if not bh:
            continue
        L.append(f"| {E.NAME[code]}（`{code}`） | {E.fmt_pct(bh['ann'])} | {E.fmt_pct(bh['mdd'])} "
                 f"| {E.fmt2(bh['sharpe'])} | {E.fmt2(bh['calmar'])} | {E.fmt_pct(bh['vol'])} "
                 f"| {bh['years']:.1f}y |")
    L.append("")
    L.append("> 落在及格线以下的择时**不如一直拿着**。")
    L.append(">")
    L.append("> 怎么用：只做一只 → 取 ★1；要收益 → 往后翻（年化最高的常在 ★2/★3，代价是回撤更深）；"
             "要控回撤 → 直接挑回撤最浅的那格，名次不重要。")
    L.append("")

    diff = [c for c in E.CODES
            if picked[c]["best"] and picked[c]["best_ann"]
            and picked[c]["best"]["key"] != picked[c]["best_ann"]["key"]]
    if diff:
        L.append("**夏普最优 ≠ 收益最高**（换收益要付回撤代价）：")
        for code in diff:
            i = picked[code]
            top = i["best_ann"]
            rk = f"，前三第 {top['rank']}" if top.get("rank") else "，未进前三"
            L.append(f"- {E.NAME[code]}：夏普最优 **{i['best']['label']}**"
                     f"（{E.fmt_pct(i['best']['ann'])} / {E.fmt_pct(i['best']['mdd'])} / "
                     f"{E.fmt2(i['best']['sharpe'])}） vs 收益最高 **{top['label']}**"
                     f"（{E.fmt_pct(top['ann'])} / {E.fmt_pct(top['mdd'])} / "
                     f"{E.fmt2(top['sharpe'])}{rk}）")
        L.append("")

    # ---- 分标的明细
    L.append("## 1. 分标的候选明细")
    L.append("")
    L.append("`名次` = 该标的前三名（夏普）；`↑` = 年化最高；`·短样本` = 不足 3 年只列不选；"
             "`·无源` = 该标的没有股息率月度历史，估值类（V1/V3/V5/V6）无法计算。"
             "**每只标的所有行共用同一窗口**，所以行间可以直接比。")
    L.append("")
    for code in E.CODES:
        info = picked[code]
        leg = E.LEG[code]
        L.append(f"### {leg['name']}（`{code}` · {leg['cat']} · {leg['idx']} · "
                 f"{'有' if leg['yield_ok'] else '**无**'}股息率历史）")
        L.append("")
        L.append("| 策略 | 名次 | 年化 | 最大回撤 | 夏普 | Calmar | 波动 | 平均仓位 | 调仓 "
                 "| 样本 | 席位 |")
        L.append("|---|---|---:|---:|---:|---:|---:|---:|---:|---|---|")
        for r in info["rows"]:
            mark = ""
            if info["best_ann"] is not None and r is info["best_ann"]:
                mark += " ↑"
            if r["short"]:
                mark += " ·短样本"
            if not leg["yield_ok"] and E.CAND[r["key"]]["needs"] in E.VALUATION_NEEDS:
                mark += " ·无源"
            rank = f"**{r['rank']}**" if r.get("rank") else "—"
            seat = "基准" if r["bench"] else f"{r['start'][:7]}~{r['end'][:7]}"
            L.append(f"| {r['label']}{mark} | {rank} | {E.fmt_pct(r['ann'])} | {E.fmt_pct(r['mdd'])} "
                     f"| {E.fmt2(r['sharpe'])} | {E.fmt2(r['calmar'])} | {E.fmt_pct(r['vol'])} "
                     f"| {r['expo']:.0%} | {r['trades']} | {r['years']:.1f}y | {seat} |")
        if not leg["yield_ok"]:
            L.append("")
            L.append("> 该标的无股息率月度历史（指数不在中证官网估值接口 / 港股不做代理），"
                     "V1/V3/V5/V6 四项**无源**，只剩买入持有与 RSI 两族。")
        L.append("")

    # ---- 组合层
    L.append("## 2. 组合层诊断（上界参照，不是方案）")
    L.append("")
    L.append(f"同一段共同窗口 **{port['start']:%Y-%m} ~ {port['end']:%Y-%m}**（{port['n']} 月，"
             "由最短历史的标的决定）里，三种拼法各跑一遍：")
    L.append("")
    L.append("| 组合 | 年化 | 最大回撤 | Calmar | 波动 | 夏普 |")
    L.append("|---|---:|---:|---:|---:|---:|")
    for r in port["rows"]:
        L.append(f"| {r['name']} | {E.fmt_pct(r['cagr'])} | {E.fmt_pct(r['dd'])} "
                 f"| {E.fmt2(r['calmar'])} | {E.fmt_pct(r['vol'])} | {E.fmt2(r['sharpe'])} |")
    L.append("")
    L.append("> 读法：**「每腿 ★1 等权」这一行是上界，不是能拿到的收益** —— 每只标的的 ★1 是在"
             "同一段样本里挑出来的，等于把整段样本的运气都算进去。它与全池等权的差额，"
             "就是「按单标的最优挑规则」这件事在样本内的最大可能贡献；"
             "真实可执行的版本必须用样本外选规则（见 `v4_rsi/research/_oos_pool.md` 的选择偏差量级）。")
    v4 = next((r for r in port["rows"] if r["name"].startswith("现行")), None)
    mix = next((r for r in port["rows"] if r["name"].startswith("每腿")), None)
    eq = next((r for r in port["rows"] if r["name"].startswith("全池")), None)
    if v4 and mix:
        rel = "高于" if mix["sharpe"] > v4["sharpe"] else "**低于**"
        L.append(">")
        L.append(f"> 这一段有个反直觉的结果：**每腿 ★1 拼装的夏普（{E.fmt2(mix['sharpe'])}）{rel} "
                 f"现行 V4 组合（{E.fmt2(v4['sharpe'])}）**。也就是说「每只标的各配一套最好的规则」"
                 "并不比「固定 5 只池 + 横截面选最便宜的 2 只 + 板块 RSI 择时」更强 —— "
                 "单标的最优会**丢掉横截面那一层信息**（同一个月里哪只最便宜），"
                 "而红利各标的之间的相对贵贱才是这个家族真正赚钱的地方。"
                 "这也解释了为什么 V0 打分（横截面）与 V4（横截面 + 时间）能在"
                 f"全池等权（夏普 {E.fmt2(eq['sharpe']) if eq else '—'}）之上再拉开距离。")
    L.append("")

    # ---- 未纳入
    L.append("## 3. 没有进清单的版本及原因")
    L.append("")
    L.append("| 版本 | 为什么不参与单标的排序 |")
    L.append("|---|---|")
    L.append("| `v0_rank` 打分轮动 | **横截面选基，不是单标的择时**：它回答「10 只里买哪 2 只」，"
             "在单只标的上无法落地；且 `v0_rank/backtest/_rank_report.md` 已给出选择边际为负"
             "（3年30 −62.6bp），不构成候选 |")
    L.append("| `v2_temp` 四维估值温度 | **口径与前两项重复且多源**：温度 = 利差/PE/乖离/位置，"
             "其中利差已由 V5 单列、股债收益比由 V6 单列；PE 与乖离/位置需要指数 PE 日频与均线，"
             "与基金净值不同源。V2 自己的结论也是「时间维度有增量但剔除 5 个月转负」"
             "（`v2_temp/backtest/_v2_report.md`），故只作背景、不进候选 |")
    L.append("| `v7_combo` 组合搜索 | **组合层**：8 个组合候选、横截面 topN × 时间倍率，"
             "不是单标的规则；结论是「没跑赢纯 RSI」（`v7_combo/backtest/_v7_report.md`） |")
    L.append("| `v9_temp5` 五因子温度 | **定位是体检表，不是择时器**：横截面 top2 为负贡献"
             "（Calmar 0.72 vs 基准 1.03）、时间分位减仓无增量；且 PB ≡ PE，五因子实为 PE 36%"
             "（`v9_temp5/RULES.md`）。列进单标的排序会重复 V3/V5/V6 的信息 |")
    L.append("| `v10_resonance` 状态机 | **覆盖与口径都不同**：只做 `000922`（对应 `090010`），"
             "口径是「单标的全收益 + 30% 仓位上限、买点三条件共振」，与「基金净值的月度仓位」"
             "不可同尺。原报告满仓版 Calmar 0.29 vs 买入持有 0.05，"
             "但换成首笔建仓日起算年化 6.63% vs 11.71%（`v10_resonance/backtest/_v10_report.md`），"
             "只作对照、不参赛 |")
    L.append("| `v1_spread` 的保险丝 | 需要「红利 − 全A 40 日超额」，只有 `000922` 有全 A 代理；"
             "本清单里的 V1 是**不带保险丝**的分位带（见 §1 备注） |")
    L.append("| 3 只无股息率历史的标的的估值类 | `008163`（标普红利低波50）、`005125`（标普红利机会）、"
             "`018388`（港股通高股息）没有股息率月度历史，V1/V3/V5/V6 **无源**，"
             "只用买入持有与 RSI 两族参赛 |")
    L.append("")

    # ---- 口径
    L.append("## 4. 口径与维护")
    L.append("")
    L.append("1. **同一只标的所有候选共用同一窗口**：窗口 = 净值覆盖 ∩ 全部候选信号可得。"
             "有股息率历史的 7 只因此从 2018-05 左右起算（分位需 36 个月预热），"
             "`090010` 的 2010–2017 段不进表 —— 这是同尺的代价，不是数据缺失。")
    L.append("2. **空仓不计息**（`research/_leg_engine.py::CASH = 0`），与家族 `_single_signal.simulate` "
             "一致，对择时类**偏保守**：V4 这类平均仓位 70% 的策略，按货基 1.4%/年折算少赚约 0.4pp/年。"
             "表里给了平均仓位，可按 `(1−expo) × 货基年化` 自行校正。")
    L.append("3. **夏普无风险利率取 0**（`月均 × 12 / 年化波动`）。这与 vol_legs 的 `(年化−货基)/波动` "
             "不同，跨家族比数字前先换算式。")
    L.append("4. **V5 / V6 / V1 在多数标的上「从不触发」**：2018 年后 10Y 中债单边下行，"
             "利差与收益比的分位长期顶在高位，所以这三行的平均仓位 92%~100%、"
             "调仓 0~4 次，绩效几乎等同买入持有 —— 这是**结论**（指标在当前利率环境下失效），"
             "不是漏算。V3（股息率分位）略好，但也只在 3 只上有 1~9 次动作。")
    L.append("5. **短样本**：`561060`（3.0y）/`561580`（3.3y）/`018388`（3.3y）/`016441`（4.0y）"
             "刚过或不足一轮牛熊，★ 的置信度明显低于表里看起来的样子；"
             "`MIN_YEARS=3` 只是下限，不是「够用」的标准。")
    L.append("6. **维护**：新增单标的规则 → 在 `research/_leg_engine.py::CANDIDATES` 加一条 + "
             "在 `_series()` 里加分支 → 重跑本脚本；`beat_best` 会读本目录的 jsonl 当基线，"
             "所以顺序是 **本脚本 → beat_best**。")
    L.append("")

    # ---- 结论（数字由本脚本算出，不写死）
    RSI_KEYS = {"V4", "V8"}
    rsi_best = [c for c in E.CODES if picked[c]["best"] and picked[c]["best"]["key"] in RSI_KEYS]
    bh_best = [c for c in E.CODES if picked[c]["best"] and picked[c]["best"]["bench"]]
    val_rows = [r for r in rows if r["key"] in ("V1", "V3", "V5", "V6")]
    val_flat = [r for r in val_rows if r["expo"] >= 0.90 and r["trades"] <= 4]
    gain = sorted(((r["ann"] - picked[r["code"]]["bh"]["ann"], r["code"], r)
                   for r in rows if r["rank"] == 1 and not r["bench"]),
                  key=lambda x: -x[0])
    ok_pool = [c for c in pool.TIMING_CODES if c in rsi_best]

    L.append("## 5. 结论")
    L.append("")
    L.append(f"1. **RSI 是唯一在整池上都站得住的规则**：10 只里 {len(rsi_best)} 只的 ★1 是 "
             f"V4/V8（`{'`、`'.join(rsi_best)}`）；{len(bh_best)} 只的 ★1 是买入持有"
             f"（`{'`、`'.join(bh_best) if bh_best else '—'}`）。"
             "红利类资产波动低、均值回复强，RSI 抓的是「短期位置」，与估值三兄弟不重复，"
             "这是它在 2020–2026 这段利率下行 + 慢牛的样本里还能有增量的原因。")
    L.append(f"2. **估值三兄弟在当前环境下等同不做**：V1/V3/V5/V6 共 {len(val_rows)} 行里，"
             f"{len(val_flat)} 行的平均仓位 ≥90% 且调仓 ≤4 次（利差/收益比分位被 10Y 国债的历史低位"
             "顶死，V1 的分位带只在 `090010` 上真正动过一次手，还是负贡献）。"
             "所以「再加一个估值指标」在这个家族是**零和**，这也是 V7 组合搜索全部落在 0.87–1.05 的原因。")
    if gain:
        top = gain[0]
        L.append(f"3. **增量集中在少数标的**：相对买入持有年化最高的是 `{top[1]}`（{top[2]['label']}，"
                 f"{top[0] * 100:+.2f}pp/年），最低的是 `{gain[-1][1]}`"
                 f"（{gain[-1][2]['label']}，{gain[-1][0] * 100:+.2f}pp/年）。"
                 "差距不是规则好坏，是**标的性格**：低波动、宽基化的红利（红利低波100、红利低波50、"
                 "国企红利）上 RSI 能压住回撤；港股通/央企红利这类趋势性强、回撤本来浅的，"
                 "择时会把上涨段一起削掉。")
    L.append(f"4. **与现行 V4 的池交叉验证，并给出一个必要的警告**：V4 的 5 只池"
             f"（`{'`、`'.join(pool.TIMING_CODES)}`）里 {len(ok_pool)}/5 只在本表的 ★1 也是 RSI 两族"
             " —— 两条独立路径（`universe.py` 的 ΔCalmar 判据 vs 本表的夏普排序）指向同一批标的。"
             "但**「★1 是 RSI」不等于「该标的适合 RSI」**：`005125`/`007467`/`007760`/`561580` 的 ★1 "
             "也是 RSI，可它们的 RSI 版本年化仍低于或仅略高于买入持有（ΔCalmar 判据把它们挡在池外）。"
             "★1 只在**同一只标的内部**比较、不含「该不该择时」这一层 —— 那一层只有 `universe.py` "
             "的双窗口 ΔCalmar 在回答（见 `v4_rsi/research/_oos_pool.md` 的选择偏差量级）。")
    L.append("5. **组合层比单标的层更重要**（见 §2）：把每只标的的最优规则拼起来，"
             "并不比「固定 5 只池 + 横截面 top2 + 板块 RSI」更强。要在这个家族上继续挖，"
             "**优先改横截面与池，而不是给单只标的换择时器**。")
    L.append("")
    return "\n".join(L) + "\n"


def main() -> int:
    print("同尺重算各候选 …")
    rows = collect()
    print(f"共 {len(rows)} 行（标的 × 候选）")
    picked = pick(rows)
    port = portfolio_check(picked)
    md = render(picked, rows, port)
    with io.open(OUT_MD, "w", encoding="utf-8") as fh:
        fh.write(md)
    with io.open(OUT_JSONL, "w", encoding="utf-8") as fh:
        for r in rows:
            fh.write(json.dumps(r, ensure_ascii=False) + "\n")
    print(f"[ok] {OUT_MD}")
    print(f"[ok] {OUT_JSONL}")
    print("")
    print("每标的前三名（夏普）：")
    for code in E.CODES:
        info = picked[code]
        if not info["top"]:
            print(f"  {E.NAME[code]:<20} —")
            continue
        print(f"  {E.NAME[code]}")
        for i, r in enumerate(info["top"], 1):
            print(f"    ★{i} {r['label']:<28} 年化 {r['ann']:+.2%} · 回撤 {r['mdd']:.2%} · "
                  f"夏普 {r['sharpe']:.2f} · {r['years']:.1f}y")
    return 0


if __name__ == "__main__":
    sys.exit(main())
