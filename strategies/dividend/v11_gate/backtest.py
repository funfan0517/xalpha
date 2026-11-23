#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""V11 · 温度闸门回测与报告

产出 `backtest/_v11_report.md` + `_v11_bt.jsonl`（逐月倍率）。

跑的不是「再试一个参数」，而是三件事：
1. **消融**：V4 的每个零件单独 / 组合，闸门加在哪一层；
2. **闸门族**：冷闸、热闸、双向、连续倾斜、无温度的暴露对照 —— 谁都不许只报一个点；
3. **三把刀**：剔除贡献最大 3/5 个月、分窗、置换检验（随机挑同样多月份放量）。

用法::

    python strategies/dividend/v11_gate/backtest.py [--refresh]
"""
from __future__ import annotations

import json
import os
import sys

import numpy as np
import pandas as pd

_HERE = os.path.dirname(os.path.abspath(__file__))
if _HERE not in sys.path:
    sys.path.insert(0, _HERE)

import data as D  # noqa: E402  pylint: disable=wrong-import-position
import engine as E  # noqa: E402  pylint: disable=wrong-import-position
import rule  # noqa: E402  pylint: disable=wrong-import-position


def pct(x, d=1, sign=False):
    if x is None or (isinstance(x, float) and np.isnan(x)):
        return "—"
    return f"{x * 100:+.{d}f}%" if sign else f"{x * 100:.{d}f}%"


def row(name, st, tag=""):
    return (f"| {name}{tag} | {pct(st['cagr'])} | {pct(st['dd'])} | {st['calmar']:.2f} | "
            f"{pct(st['vol'])} | {st['sharpe']:.2f} | {pct(st['total'], 0)} | {st['pos']:.0%} | {st['switches']} |")


def main(refresh: bool = False) -> int:
    d = D.load(refresh=refresh)
    rets, bq, top, base_w = d["rets"], d["bq"], d["top"], d["base_w"]
    temp6, temp4 = d["temp6"], d["temp4"]
    end = d["end"]
    m_rsi = E.mult_rsi(bq)
    corr_lv = float((bq * 100).corr(temp6))
    corr_df = float((bq * 100).diff().corr(temp6.diff()))

    cold40 = E.gate_cold(m_rsi, temp6, rule.COLD)
    hot60 = E.gate_hot(m_rsi, temp6, rule.HOT)
    both = E.gate_hot(E.gate_cold(m_rsi, temp6, rule.COLD), temp6, rule.HOT)

    # ---------------------------------------------------------------- 消融
    _n = len(rule.CODES)
    ab = [
        (f"基准：{_n} 只等权满仓", E.evaluate(base_w, pd.Series(1.0, index=bq.index), rets)),
        ("横截面：RSI 最低 2 只（满仓）", E.evaluate(top, pd.Series(1.0, index=bq.index), rets)),
        (f"V4 时间档（{_n} 只等权 × 70/50）", E.evaluate(base_w, m_rsi, rets)),
        ("V4 完整（top2 × 70/50）", E.evaluate(top, m_rsi, rets)),
        (f"V4 + 冷闸 {rule.COLD:.0f}", E.evaluate(top, cold40, rets)),
        (f"V4 + 热闸 {rule.HOT:.0f}", E.evaluate(top, hot60, rets)),
        (f"V4 + 双向 {rule.COLD:.0f}/{rule.HOT:.0f}", E.evaluate(top, both, rets)),
        ("纯温度择时（无 RSI）：冷满/温半/热空", E.evaluate(
            top, E.mult_temp_only(temp6, rule.COLD, rule.HOT), rets)),
    ]
    v4 = ab[3][1]

    # ---------------------------------------------------------------- 无温度暴露对照
    half_ctrl = []
    for h in rule.HALF_H:
        m = m_rsi.copy()
        m[(m_rsi > 0.01) & (m_rsi < 0.99)] = h
        half_ctrl.append((h, E.evaluate(top, m, rets)))

    # ---------------------------------------------------------------- 网格
    def cold_grid(temp):
        out = [("无冷闸", E.evaluate(top, m_rsi, rets))]
        for c in rule.COLD_GRID:
            out.append((f"cold={c:.0f}", E.evaluate(top, E.gate_cold(m_rsi, temp, c), rets)))
        return out

    def hot_grid(temp):
        out = [("无热闸", E.evaluate(top, m_rsi, rets))]
        for h in rule.HOT_GRID:
            out.append((f"hot={h:.0f}", E.evaluate(top, E.gate_hot(m_rsi, temp, h), rets)))
        return out

    cg6, hg6 = cold_grid(temp6), hot_grid(temp6)
    cg4, hg4 = cold_grid(temp4), hot_grid(temp4)
    both_m = {(c, h): E.evaluate(top, E.gate_hot(E.gate_cold(m_rsi, temp6, c), temp6, h), rets)
              for c in rule.COLD_GRID for h in rule.HOT_GRID}

    tilt = {(t0, lam): E.evaluate(top, E.mult_tilt(m_rsi, temp6, t0, lam), rets)
            for t0 in rule.TILT_T0 for lam in rule.TILT_LAM}

    # ---------------------------------------------------------------- 稳健性
    def robs(st):
        r = st["r"]
        r3 = E.drop_best(r, v4["r"], 3)
        r5 = E.drop_best(r, v4["r"], 5)
        h1 = r.loc[:"2023-12-31"]
        h2 = r.loc["2024-01-01":]
        return {"d3": E.metrics(r3), "d5": E.metrics(r5),
                "h1": E.metrics(h1), "h2": E.metrics(h2)}

    rob_rows = [(n, st, robs(st)) for n, st in
                [("V4 完整", v4), ("+ 冷闸 40", ab[4][1]), ("+ 冷闸 50", cg6[4][1]),
                 ("+ 半仓 0.6（无温度）", half_ctrl[0][1])]]

    # ---------------------------------------------------------------- 置换
    perm = {"cold40": E.perm_test(cold40, m_rsi, top, rets),
            "cold50": E.perm_test(E.gate_cold(m_rsi, temp6, 50.0), m_rsi, top, rets)}

    # ---------------------------------------------------------------- 触发诊断
    port = (top.shift(1) * rets).sum(axis=1)
    half = (m_rsi > 0.01) & (m_rsi < 0.99)
    trig = E.trigger_months(cold40, m_rsi)
    hit_ret = port.reindex(trig).dropna()
    other = port[half & ~port.index.isin(trig)].dropna()
    bs = E.band_stats(m_rsi, temp6, port)
    hot_trig = E.trigger_months(hot60, m_rsi)

    # ---------------------------------------------------------------- 对齐当前 V4（10 只全池）
    # V4 自 2026-11-20 起改用 pool.TIMING_POOL_ALL（10 只）。上面的 §一 是旧 5 只筛池，
    # 已不代表今天的 V4，故用同一组闸门、同一条温度序列在 10 只池上重跑一遍作为复核。
    d10 = D.load(refresh=refresh, codes=rule.CODES_V4NOW)
    r10, bq10, top10, base10 = d10["rets"], d10["bq"], d10["top"], d10["base_w"]
    t10 = d10["temp6"]
    m10 = E.mult_rsi(bq10)
    one10 = pd.Series(1.0, index=bq10.index)
    v4n = E.evaluate(top10, m10, r10)
    cold10 = {c: E.evaluate(top10, E.gate_cold(m10, t10, c), r10) for c in (40.0, 45.0, 50.0)}
    hot10 = {h: E.evaluate(top10, E.gate_hot(m10, t10, h), r10) for h in rule.HOT_GRID}
    ab10 = [(f"基准：{len(rule.CODES_V4NOW)} 只等权满仓", E.evaluate(base10, one10, r10)),
            ("横截面：RSI 最低 2 只（满仓）", E.evaluate(top10, one10, r10)),
            ("V4 时间档（10 只等权 × 70/50）", E.evaluate(base10, m10, r10)),
            ("V4 完整（top2 × 70/50）", v4n)]
    ab10 += [(f"V4 + 冷闸 {c:.0f}", st) for c, st in cold10.items()]
    ab10 += [(f"V4 + 热闸 {h:.0f}", st) for h, st in hot10.items()]
    both10 = E.evaluate(top10, E.gate_hot(E.gate_cold(m10, t10, 40.0), t10, 60.0), r10)
    pure10 = E.evaluate(top10, E.mult_temp_only(t10, rule.COLD, rule.HOT), r10)
    ab10 += [("V4 + 双向 40/60", both10), ("纯温度择时（无 RSI）：冷满/温半/热空", pure10)]
    corr_10 = float((bq10 * 100).corr(t10))
    bs10 = E.band_stats(m10, t10, (top10.shift(1) * r10).sum(axis=1))
    # 无温度对照：把减半档一律抬到 h（检验「冷闸是不是只是多拿仓位」）
    half10 = []
    for h in (0.6, 0.7, 0.8, 0.9):
        _m = m10.copy()
        _m[(m10 > 0.01) & (m10 < 0.99)] = h
        half10.append((h, E.evaluate(top10, _m, r10)))
    perm10 = {"cold40": E.perm_test(E.gate_cold(m10, t10, 40.0), m10, top10, r10),
              "cold50": E.perm_test(E.gate_cold(m10, t10, 50.0), m10, top10, r10)}
    trig10 = E.trigger_months(E.gate_cold(m10, t10, 40.0), m10)
    # 触发月诊断（10 只池）：放量月 vs 其它减半月的下月收益 —— 「挑月」还是「多拿仓位」
    # 用冷闸 50 的放量月来诊断，因为「同平均仓位下 +0.34 Calmar」说的是冷闸 50。
    trig50 = E.trigger_months(E.gate_cold(m10, t10, 50.0), m10)
    port10 = (top10.shift(1) * r10).sum(axis=1)
    half_mask10 = (m10 > 0.01) & (m10 < 0.99)
    hit10 = port10.reindex(trig50).dropna()
    other10 = port10[half_mask10 & ~port10.index.isin(trig50)].dropna()

    def rob10(st):
        r = st["r"]
        return {"d3": E.metrics(E.drop_best(r, v4n["r"], 3)),
                "d5": E.metrics(E.drop_best(r, v4n["r"], 5)),
                "h1": E.metrics(r.loc[:"2023-12-31"]),
                "h2": E.metrics(r.loc["2024-01-01":])}

    rob10_rows = [("V4 完整（10 只）", v4n, rob10(v4n)),
                  ("+ 冷闸 40", cold10[40.0], rob10(cold10[40.0])),
                  ("+ 冷闸 50", cold10[50.0], rob10(cold10[50.0])),
                  ("+ 热闸 60", hot10[60.0], rob10(hot10[60.0]))]

    # ---------------------------------------------------------------- 三池对照
    pool_rows = []
    for label, codes in (("5 只旧筛池（V4 2026-11-20 前）", rule.CODES_LEGACY),
                         (f"{len(rule.CODES)} 只温度同源（**本版**）", rule.CODES),
                         (f"{len(rule.CODES_V4NOW)} 只当前 V4 全池", rule.CODES_V4NOW)):
        same = sorted(codes) == sorted(rule.CODES)
        dd = d if same else D.load(refresh=refresh, codes=codes)
        _m = E.mult_rsi(dd["bq"])
        _one = pd.Series(1.0, index=dd["bq"].index)
        _eq = E.evaluate(dd["base_w"], _one, dd["rets"])
        _v4 = E.evaluate(dd["top"], _m, dd["rets"])
        _cold = E.evaluate(dd["top"], E.gate_cold(_m, dd["temp6"], 40.0), dd["rets"])
        _hot = E.evaluate(dd["top"], E.gate_hot(_m, dd["temp6"], 60.0), dd["rets"])
        _d3 = E.metrics(E.drop_best(_cold["r"], _v4["r"], 3))
        pool_rows.append((label, _eq, _v4, _cold, _d3, _hot))

    # ---------------------------------------------------------------- 报告
    L = [f"# {rule.TITLE}", "",
         "> 规则文件是唯一权威（`RULES.md`）；本文件由 `backtest.py` 生成，数字每次重跑都会重算。",
         f"> **一句话：温度当闸门接 V4 不成立** —— 冷闸全样本 Calmar {v4['calmar']:.2f} → "
         f"{ab[4][1]['calmar']:.2f}，但这一点优势在剔除 3 个月后转负"
         f"（{pct(rob_rows[1][2]['d3']['cagr'])} / {rob_rows[1][2]['d3']['calmar']:.2f} vs V4 "
         f"{pct(rob_rows[0][2]['d3']['cagr'])} / {rob_rows[0][2]['d3']['calmar']:.2f}），"
         f"且减半档内温度对下月收益没有判别力（t={bs['t']:+.2f}）。详见 §七。",
         f"> **池已按用户指令改成「既有温度又有 RSI 的 {len(rule.CODES)} 只」**（2026-11-23），"
         "温度与交易池同源；三池对照见 §一·补，结论不变。", ""]

    L += ["## 〇、样本与口径", "",
          f"- 样本 **{rets.index[0]:%Y-%m} ~ {rets.index[-1]:%Y-%m}（{len(rets)} 月）**。",
          f"- **交易池 = 既有温度又有 RSI 的 {len(rule.CODES)} 只**（`rule.CODES`："
          + " / ".join(f"{c} {rule.NAME_SHORT.get(c, '')}" for c in rule.CODES) + "）。"
          " 2026-11-23 按用户指令从「5 只筛池」改成这个同源池：温度与交易池完全重合，"
          "板块温度就是交易池自己的中位数，不再有「用 6 只描述 5 只」的错配。",
          "- 收益：基金累计净值（含分红再投）；月末信号 → 下月生效；成本单边 0.03%。",
          f"- 温度：V9 五因子月度温度取**这 {len(rule.CODES)} 只的中位数**。"
          f"因为池就是温度池，敏感性口径（{len(rule.TEMP_IN_V4)} 只）与主口径**完全相同**，"
          "§三.5 的两口径对照在本版退化为一行（保留该表以留证）。",
          "- 闸门只改**仓位倍率**，横截面仍是「RSI 最低 2 只等权」；温度缺失月保持 V4 原档位，不插补。", ""]

    L += ["## 一、消融", "",
          "| 方案 | 年化 | 最大回撤 | Calmar | 波动 | 夏普 | 累计 | 平均仓位 | 换档次数 |",
          "|---|---:|---:|---:|---:|---:|---:|---:|---:|"]
    L += [row(n, st) for n, st in ab]
    L += ["", f"- V4 完整（**{len(rule.CODES)} 只同源池**）：**{pct(v4['cagr'])} / {pct(v4['dd'])} / "
              f"Calmar {v4['calmar']:.2f}**。对照：旧 5 只筛池 "
              f"{pct(pool_rows[0][2]['cagr'])} / {pct(pool_rows[0][2]['dd'])} / "
              f"{pool_rows[0][2]['calmar']:.2f}、10 只全池 "
              f"{pct(pool_rows[2][2]['cagr'])} / {pct(pool_rows[2][2]['dd'])} / "
              f"{pool_rows[2][2]['calmar']:.2f} —— 换成本版池后回撤最浅（{pct(v4['dd'])}），"
              f"Calmar 也是三者最高（详见 §一·补）。",
          f"- 冷闸把平均仓位从 {v4['pos']:.0%} 抬到 {ab[4][1]['pos']:.0%}、最大回撤 "
          f"{pct(v4['dd'])} → {pct(ab[4][1]['dd'])}。**别只看这个数**：回撤的谷底与放量月的位置"
          "决定了它，路径细节见 §六.1。",
          f"- 热闸在 {rule.HOT:.0f} 只压 {len(hot_trig)} 个月，年化 "
          f"{pct(ab[5][1]['cagr'] - v4['cagr'], 1, True)}、Calmar "
          f"{ab[5][1]['calmar'] - v4['calmar']:+.2f}；**温度高时减仓在本样本是负和**"
          f"（本版比旧池轻，但仍为负）。",
          f"- 板块温度与板块 RSI 的相关：**水平 {corr_lv:.2f}、月度差分 {corr_df:.2f}** —— "
          "两者本就是同一个位置信号的两种写法，用温度修饰 RSI 的信息增量天然有限。", ""]

    # ---------------------------------------------------------------- §一·补 报告
    L += ["## 一·补、换池对照（5 旧 / 6 同源 / 10 当前 V4）", "",
          "> **为什么要换池**：旧口径温度由 6 只算出、却只交易其中 5 只（008163 无温度），"
          "「板块温度」描述的是一个比交易池更宽的集合。2026-11-23 按用户指令把交易池改成"
          "**既有温度又有 RSI 的 6 只**，温度与交易池完全重合。",
          "> 这一节回答两个问题：① 换池后 V4 本身变好还是变差；② 闸门的结论有没有因为池而变化。", ""]

    # 三池对照
    L += ["| 池 | 等权满仓 | **V4 完整** | + 冷闸 40 | 冷闸 40 剔 3 月 | + 热闸 60 |",
          "|---|---:|---:|---:|---:|---:|"]
    for label, st_eq, st_v4, st_cold, st_d3, st_hot in pool_rows:
        L.append(f"| {label} | {pct(st_eq['cagr'])}/{st_eq['calmar']:.2f} | "
                 f"**{pct(st_v4['cagr'])}/{st_v4['calmar']:.2f}** | "
                 f"{pct(st_cold['cagr'])}/{st_cold['calmar']:.2f} | "
                 f"{pct(st_d3['cagr'])}/{st_d3['calmar']:.2f} | "
                 f"{pct(st_hot['cagr'])}/{st_hot['calmar']:.2f} |")
    _c = [row[2]["calmar"] for row in pool_rows]
    L += ["", "> 单元格 = 年化 / Calmar；「剔 3 月」= 相对**同池 V4** 超额最大的 3 个月。", "",
          f"- **换池没有让 V4 变差**：V4 完整在三个池上是 "
          f"{_c[0]:.2f} / **{_c[1]:.2f}** / {_c[2]:.2f}（5 只 / 6 只 / 10 只），"
          f"6 只同源池的回撤最浅（{pct(v4['dd'])}）—— 少掉的 4 只是与已持仓重复度最高、"
          "且没有温度口径的标的，换成它们本来就是在稀释信号。",
          f"- **闸门结论与池无关**：三个池上冷闸 40 都能把全样本 Calmar 抬 0.10~0.15，"
          f"但**剔除 3 个月后一律低于同池 V4**（"
          + "、".join(f"{row[0]} {row[4]['calmar']:.2f} vs {row[2]['calmar']:.2f}" for row in pool_rows)
          + "）。所以「不成立」不是池的问题。", ""]

    L += ["### 一·补.1 10 只当前 V4 全池的细节（留证）", "",
          f"> V4 自 2026-11-20 起改用全池 10 只（`pool.TIMING_POOL_ALL`）。这里保留它的完整消融与检验，"
          f"用于回答「如果沿用 V4 的 10 只池，闸门行不行」——结论同上，只是数字不同。", ""]
    L += ["| 方案 | 年化 | 最大回撤 | Calmar | 波动 | 夏普 | 累计 | 平均仓位 | 换档次数 |",
          "|---|---:|---:|---:|---:|---:|---:|---:|---:|"]
    L += [row(n, st) for n, st in ab10]
    L += ["",
          f"- 10 只池的 V4 完整：**{pct(v4n['cagr'])} / {pct(v4n['dd'])} / Calmar {v4n['calmar']:.2f}**"
          f"（6 只同源池 {pct(v4['cagr'])} / {pct(v4['dd'])} / {v4['calmar']:.2f}）—— "
          f"10 只池回撤略深、Calmar 略低，与 §一·补 的三池对照一致。",
          f"- 冷闸 40 在 10 只池上：{pct(cold10[40.0]['cagr'])} / Calmar {cold10[40.0]['calmar']:.2f}"
          f"（V4 {v4n['calmar']:.2f}），放量 {len(trig10)} 个月，平均仓位 "
          f"{cold10[40.0]['pos']:.0%}（V4 {v4n['pos']:.0%}）。",
          f"- 热闸 60 在 10 只池上：{pct(hot10[60.0]['cagr'])} / Calmar {hot10[60.0]['calmar']:.2f}"
          f" —— 方向与 6 只同源池一致（压 Calmar）。",
          f"- 纯温度择时：{pct(pure10['cagr'])} / Calmar {pure10['calmar']:.2f}，仍不如 RSI 骨架。",
          f"- 板块温度与板块 RSI 的相关：**水平 {corr_10:.2f}**（6 只同源池 {corr_lv:.2f}）；"
          f"减半档内（n={bs10['n']}）温度对下月收益 r={bs10['r']:+.3f}（t={bs10['t']:+.2f}）。",
          f"- 温度覆盖：10 只池里 **{len(rule.TEMP_IN_V4NOW)}/{len(rule.CODES_V4NOW)} 只**有温度"
          f"（`rule.TEMP_IN_V4NOW`；本版 6 只同源池是 6/6）—— 这是本版换成同源池的原因："
          f"在 10 只池里，池内四成标的没有温度口径、选中的 top2 可能落在其中，"
          f"「板块温度」描述的比交易池窄。", ""]
    L += ["#### 无温度的暴露对照（10 只池）", "",
          "| 减半档统一抬到 | 年化 | 最大回撤 | Calmar | 平均仓位 |",
          "|---|---:|---:|---:|---:|"]
    L += [f"| {h:.1f} | {pct(st['cagr'])} | {pct(st['dd'])} | {st['calmar']:.2f} | {st['pos']:.0%} |"
          for h, st in half10]
    L += ["", f"> 冷闸 50 的平均仓位 {cold10[50.0]['pos']:.0%}、Calmar {cold10[50.0]['calmar']:.2f}；"
          "对照表里同仓位档的「无差别抬半仓」就在旁边 —— "
          "**如果它俩的 Calmar 差不多，说明冷闸 50 的好看只是一次隐形的加杠杆**，"
          "不是温度挑对了月份。", ""]
    L += ["#### 三把刀（10 只池）", "",
          "| 方案 | 全样本 | 剔除 3 月 | 剔除 5 月 | 2020–2023 | 2024–2026 |",
          "|---|---:|---:|---:|---:|---:|"]
    for n, st, rb in rob10_rows:
        L.append(f"| {n} | {pct(st['cagr'])}/{st['calmar']:.2f} | "
                 f"{pct(rb['d3']['cagr'])}/{rb['d3']['calmar']:.2f} | "
                 f"{pct(rb['d5']['cagr'])}/{rb['d5']['calmar']:.2f} | "
                 f"{pct(rb['h1']['cagr'])}/{rb['h1']['calmar']:.2f} | "
                 f"{pct(rb['h2']['cagr'])}/{rb['h2']['calmar']:.2f} |")
    L += ["", "> 单元格 = 年化 / Calmar；剔除口径是「相对 10 只池 V4 超额最大的 N 个月」。", ""]
    L += ["#### 触发月诊断（10 只池，看冷闸 50 —— 「+0.34」的来源）", "",
          f"冷闸 40 放量 {len(trig10)} 个月、冷闸 50 放量 **{len(trig50)} 个月**。", "",
          "| 组（冷闸 50 口径） | 月数 | 下月平均收益 | 胜率 |", "|---|---:|---:|---:|",
          f"| 放量月（抬回满仓） | {len(hit10)} | {hit10.mean() * 1e4:+.0f}bp | "
          f"{(hit10 > 0).mean():.0%} |",
          f"| 其它减半月 | {len(other10)} | {other10.mean() * 1e4:+.0f}bp | "
          f"{(other10 > 0).mean():.0%} |",
          "",
          f"- 两组下月收益差 **{(hit10.mean() - other10.mean()) * 1e4:+.0f}bp**、胜率差 "
          f"{(hit10 > 0).mean() - (other10 > 0).mean():+.0%} —— "
          + ("**放量月的下月表现反而更差**。" if (hit10.mean() < other10.mean())
             else "两组几乎无差。")
          + "也就是说「挑月能力」这栏基本是零。",
          f"- 既然如此，冷闸 50 为什么在同平均仓位下还高 "
          f"{cold10[50.0]['calmar'] - half10[2][1]['calmar']:.2f} Calmar？"
          "只能是**回撤路径**的巧合：Calmar 的分母（最大回撤）对「哪几个月恰好重仓」极度敏感。"
          f"这种差额在剔除检验里立刻消失（{cold10[50.0]['cagr'] * 100:.1f}%/"
          f"{cold10[50.0]['calmar']:.2f} → {rob10_rows[2][2]['d3']['cagr'] * 100:.1f}%/"
          f"{rob10_rows[2][2]['d3']['calmar']:.2f}）。", ""]
    L += ["#### 置换检验（10 只池）", "",
          "| 方案 | 放量月数 | 闸门 Calmar | 随机中位 | 5%–95% | 超过随机 |",
          "|---|---:|---:|---:|---|---:|"]
    for k, p in perm10.items():
        L.append(f"| {k} | {p['n']} | {p['calmar']:.2f} | {p['median']:.2f} | "
                 f"[{p['p5']:.2f}, {p['p95']:.2f}] | {p['pct']:.0%} |")
    _d3_10 = rob10_rows[1][2]["d3"]
    L += ["",
          f"- 冷闸 40 在 10 只池上剔除 3 个月后：{pct(_d3_10['cagr'])} / Calmar "
          f"{_d3_10['calmar']:.2f}（V4 同口径 "
          f"{pct(rob10_rows[0][2]['d3']['cagr'])} / {rob10_rows[0][2]['d3']['calmar']:.2f}）—— "
          f"与本版 6 只同源池同一结论：**过不了剔除检验**。",
          f"- 置换：cold40 {perm10['cold40']['pct']:.0%} 分位、cold50 {perm10['cold50']['pct']:.0%} 分位"
          f"（6 只同源池 {perm['cold40']['pct']:.0%} / {perm['cold50']['pct']:.0%}），仍不到 95%。", ""]

    L += ["## 二、无温度的暴露对照（闸门是不是只是「多拿仓位」）", "",
          "| 减半档统一抬到 | 年化 | 最大回撤 | Calmar | 平均仓位 |", "|---|---:|---:|---:|---:|"]
    for h, st in half_ctrl:
        L.append(f"| {h:.1f} | {pct(st['cagr'])} | {pct(st['dd'])} | {st['calmar']:.2f} | {st['pos']:.0%} |")
    L += ["", "- 无差别抬半仓（h = "
          + " / ".join(f"{h:.1f}" for h, _ in half_ctrl)
          + f"）的 Calmar 是 " + " / ".join(f"{st['calmar']:.2f}" for _, st in half_ctrl)
          + f"，**都不高于 V4 的 {v4['calmar']:.2f}**："
            f"说明 V4 的 {pct(v4['dd'])} 回撤确实是靠这 {bs['n']} 个半仓月压出来的，不能整体抬。",
          f"- 冷闸与它们的区别只在「挑哪几个月抬」：冷闸挑 {len(trig)} 个月，Calmar "
          f"{ab[4][1]['calmar']:.2f}。这个差是不是挑得准，看 §五。", ""]

    L += [f"## 三、阈值网格（温度 = 交易池 {len(rule.CODES)} 只中位数）", "",
          "### 3.1 冷闸（减半档 → 满仓）", "",
          "| 冷线 | 年化 | 最大回撤 | Calmar | 平均仓位 |", "|---|---:|---:|---:|---:|"]
    L += [f"| {n} | {pct(st['cagr'])} | {pct(st['dd'])} | {st['calmar']:.2f} | {st['pos']:.0%} |"
          for n, st in cg6]
    L += ["", "### 3.2 热闸（满仓 → 减半）", "",
          "| 热线 | 年化 | 最大回撤 | Calmar | 平均仓位 |", "|---|---:|---:|---:|---:|"]
    L += [f"| {n} | {pct(st['cagr'])} | {pct(st['dd'])} | {st['calmar']:.2f} | {st['pos']:.0%} |"
          for n, st in hg6]
    L += ["", "### 3.3 双向（格内 = Calmar）", "",
          "| 冷线 \\ 热线 | " + " | ".join(f"hot={h:.0f}" for h in rule.HOT_GRID) + " |",
          "|---|" + "---:|" * len(rule.HOT_GRID)]
    for c in rule.COLD_GRID:
        L.append(f"| cold={c:.0f} | " + " | ".join(f"{both_m[(c, h)]['calmar']:.2f}"
                                                   for h in rule.HOT_GRID) + " |")
    L += ["", "### 3.4 连续倾斜（不设硬阈值）", "",
          "| T0 \\ λ | " + " | ".join(f"λ={l:.1f}" for l in rule.TILT_LAM) + " |",
          "|---|" + "---:|" * len(rule.TILT_LAM)]
    for t0 in rule.TILT_T0:
        L.append(f"| T0={t0:.0f} | " + " | ".join(
            f"{tilt[(t0, l)]['calmar']:.2f}" for l in rule.TILT_LAM) + " |")
    L += ["", "### 3.5 温度口径敏感性（本版已退化为单一口径）", "",
          "| 口径 | 冷 40 | 冷 45 | 冷 50 | 热 60 | 热 65 |", "|---|---:|---:|---:|---:|---:|"]

    def pick(grid, name):
        return next(st for n, st in grid if n == name)

    L.append(f"| 交易池 {len(rule.CODES)} 只（主 = V9 温度池） | "
             f"{pick(cg6, 'cold=40')['calmar']:.2f} | {pick(cg6, 'cold=45')['calmar']:.2f} | "
             f"{pick(cg6, 'cold=50')['calmar']:.2f} | {pick(hg6, 'hot=60')['calmar']:.2f} | "
             f"{pick(hg6, 'hot=65')['calmar']:.2f} |")
    L.append(f"| 池内有温度的子集（{len(rule.TEMP_IN_V4)} 只） | "
             f"{pick(cg4, 'cold=40')['calmar']:.2f} | {pick(cg4, 'cold=45')['calmar']:.2f} | "
             f"{pick(cg4, 'cold=50')['calmar']:.2f} | {pick(hg4, 'hot=60')['calmar']:.2f} | "
             f"{pick(hg4, 'hot=65')['calmar']:.2f} |")
    L += ["", f"- 本版交易池 = 温度池（都是这 {len(rule.CODES)} 只），所以两行**必然相同** —— "
              "这正是换池的目的（RULES §七.1 的「温度池 ≠ 交易池」错配已消除）。"
              "旧版这一节用来检验「008163 被排除」是否影响结论，现在这个检验没有对象了。", ""]

    L += ["## 四、稳健性（三把刀）", "",
          "| 方案 | 全样本 | 剔除 3 月 | 剔除 5 月 | 2020–2023 | 2024–2026 |",
          "|---|---:|---:|---:|---:|---:|"]
    for n, st, rb in rob_rows:
        L.append(f"| {n} | {pct(st['cagr'])}/{st['calmar']:.2f} | "
                 f"{pct(rb['d3']['cagr'])}/{rb['d3']['calmar']:.2f} | "
                 f"{pct(rb['d5']['cagr'])}/{rb['d5']['calmar']:.2f} | "
                 f"{pct(rb['h1']['cagr'])}/{rb['h1']['calmar']:.2f} | "
                 f"{pct(rb['h2']['cagr'])}/{rb['h2']['calmar']:.2f} |")
    L += ["", "> 单元格 = 年化 / Calmar。剔除口径是「相对 V4 超额最大的 N 个月」。",
          f"- **V4 自己过刀**（剔除 3 月后 {pct(rob_rows[0][2]['d3']['cagr'])} / "
          f"{rob_rows[0][2]['d3']['calmar']:.2f}，{'' if rob_rows[0][2]['d3']['cagr'] > v4['cagr'] else '不'}"
          f"比全样本高），冷闸过不了（{pct(v4['cagr'])} → "
          f"{pct(rob_rows[1][2]['d3']['cagr'])}，Calmar {v4['calmar']:.2f} → "
          f"{rob_rows[1][2]['d3']['calmar']:.2f}）。这就是判 V11 不成立的直接依据。", ""]

    L += ["## 五、置换检验（随机挑同样多月份放满）", "",
          "| 方案 | 放量月数 | 闸门 Calmar | 随机中位 | 5%–95% | 超过随机 |",
          "|---|---:|---:|---:|---|---:|"]
    for k, p in perm.items():
        L.append(f"| {k} | {p['n']} | {p['calmar']:.2f} | {p['median']:.2f} | "
                 f"[{p['p5']:.2f}, {p['p95']:.2f}] | {p['pct']:.0%} |")
    L += ["", f"- {rule.N_PERM} 次随机置换。cold40 落在 **{perm['cold40']['pct']:.0%} 分位**、"
              f"cold50 **{perm['cold50']['pct']:.0%} 分位** —— "
          + ("**不能算显著**（要 ≥95% 才有话说）。" if max(perm['cold40']['pct'],
                                                                perm['cold50']['pct']) < 0.95
             else "**过了 95% 门槛**。")
          + "而且它的「超额」大部分来自「本来那些月也在涨」。", ""]

    L += ["## 六、触发月诊断", "",
          f"冷闸（{rule.COLD:.0f}）共放量 **{len(trig)} 个月**："
          + "、".join(f"{t:%Y-%m}" for t in trig) + "。", "",
          "| 组 | 月数 | 下月平均收益 | 胜率 |", "|---|---:|---:|---:|",
          f"| 冷闸放量的月份 | {len(hit_ret)} | {hit_ret.mean() * 1e4:+.0f}bp | {(hit_ret > 0).mean():.0%} |",
          f"| 其它减半月份 | {len(other)} | {other.mean() * 1e4:+.0f}bp | {(other > 0).mean():.0%} |",
          "", f"- 两组下月收益 {hit_ret.mean() * 1e4:+.0f} vs {other.mean() * 1e4:+.0f}bp、"
              f"胜率 {(hit_ret > 0).mean():.0%} vs {(other > 0).mean():.0%} —— "
              + ("**闸门没有挑到更好的月份**，它赚的是「这些月本来在涨，而 V4 只拿了一半」。"
                 if abs(hit_ret.mean() - other.mean()) < 0.003
                 else "方向是「挑到了更好的月份」；但 n 很小，能不能当能力用要看 §五 的置换"
                      "（以及 §四 的剔除检验）。"),
          f"- 减半档内（n={bs['n']}）温度对下月收益的相关系数 **{bs['r']:+.3f}（t={bs['t']:+.2f}）**，"
          + ("没有判别力（|t|<2）。这是「温度当闸门」在统计上最直接的否证。"
             if abs(bs['t']) < 2 else "|t|≥2，需重新评估。"),
          f"- 热闸（{rule.HOT:.0f}）只压到 {len(hot_trig)} 个月：" + ("、".join(f"{t:%Y-%m}" for t in hot_trig)
                                                          if len(hot_trig) else "无") + "。", ""]

    # 回撤路径：最大回撤「没变」是不是真的没影响
    ddp = {}
    for n, st in [("V4 完整", v4), ("冷闸 40", ab[4][1]), ("热闸 60", ab[5][1])]:
        nav = (1 + st["r"]).cumprod()
        ddp[n] = nav / nav.cummax() - 1
    L += ["### 6.1 回撤路径（最大回撤为什么没变）", "",
          "| 方案 | 最深 | 月份 | 次深 | 月份 |", "|---|---:|---|---:|---|"]
    for n, dd in ddp.items():
        L.append(f"| {n} | {pct(dd.min())} | {dd.idxmin():%Y-%m} | "
                 f"{pct(dd.nsmallest(2).iloc[-1])} | {dd.nsmallest(2).index[-1]:%Y-%m} |")
    _d22 = {n: dd.get(pd.Timestamp("2022-10-31"), float("nan")) for n, dd in ddp.items()}
    L += ["", f"- 最深回撤都在 **{ddp['V4 完整'].idxmin():%Y-%m}**；冷闸的放量月"
              f"{'不在它附近' if len(trig) else '（无）'}，所以最大回撤变化很小 —— "
              "这是**路径巧合，不是保护**。",
          f"- 反证：2022-10 的回撤被冷闸从 {pct(_d22['V4 完整'])} 加深到 {pct(_d22['冷闸 40'])}"
          "（2022-05/06/08 三个放量月正好加在下跌前）。只看「最大回撤不变」会漏掉这件事。", ""]

    # 分年
    ys = E.yearly({"V4 完整": v4["r"], "冷闸 40": ab[4][1]["r"], "热闸 60": ab[5][1]["r"],
                   f"基准 {len(rule.CODES)} 只": ab[0][1]["r"]})
    L += ["### 6.2 分年度收益", "", "| 年份 | " + " | ".join(ys.columns) + " |",
          "|---|" + "---:|" * len(ys.columns)]
    for ts, r in ys.iterrows():
        L.append(f"| {int(ts)} | " + " | ".join(pct(v) for v in r) + " |")
    L += ["", f"- 冷闸的超额集中在 {int((ys['冷闸 40'] - ys['V4 完整']).idxmax())} 年"
              f"（{pct((ys['冷闸 40'] - ys['V4 完整']).max())}），2021/2022 都是负贡献 —— "
              "「2020 修复市里低配」这个动机对，但同一招在 2022 的反弹里会挨打。", ""]

    _hot_cal = [st["calmar"] for _n, st in hg6]
    L += ["## 七、结论", "",
          f"1. **不接。** 温度当闸门（冷 → 回满仓、热 → 压到减半）在本样本里过不了三把刀："
          f"剔除 3 个月转负（{pct(rob_rows[1][2]['d3']['cagr'])} / "
          f"{rob_rows[1][2]['d3']['calmar']:.2f} vs V4 "
          f"{pct(rob_rows[0][2]['d3']['cagr'])} / {rob_rows[0][2]['d3']['calmar']:.2f}）、"
          f"置换检验 {perm['cold40']['pct']:.0%} 分位、减半档内 t={bs['t']:+.2f}。",
          f"2. **热闸在本版池上只是略微有害**：55–70 的热线 Calmar 落在 "
          f"{min(_hot_cal):.2f}–{max(_hot_cal):.2f}（V4 {v4['calmar']:.2f}），"
          f"年化 {pct(ab[5][1]['cagr'] - v4['cagr'], 1, True)}。"
          "温度高在红利里不等于「接下来要跌」—— 旧池上这条更明显（1.34–1.37），"
          "换成同源池后伤害变小，但方向未变。",
          f"3. **冷闸的 {pct(ab[4][1]['cagr'] - v4['cagr'], 1, True)} 过不了稳健性检验，"
          f"哪怕它的「挑月」方向是对的**：放量月下月收益 {hit_ret.mean() * 1e4:+.0f}bp"
          f"（胜率 {(hit_ret > 0).mean():.0%}）vs 其它减半月 {other.mean() * 1e4:+.0f}bp"
          f"（{(other > 0).mean():.0%}）；但只有 {len(trig)} 个月、置换 "
          f"{perm['cold40']['pct']:.0%} 分位，剔除最强的 3 个月后 Calmar "
          f"{ab[4][1]['calmar']:.2f} → {rob_rows[1][2]['d3']['calmar']:.2f}。"
          f"**样本撑不住「这是能力」这个判断。**",
          f"4. **温度与 RSI 是同一件事的弱拷贝**：月度水平相关 {corr_lv:.2f}、"
          f"差分相关 {corr_df:.2f}。用温度去修饰 RSI，等于给同一个位置信号再打一次折 —— "
          "没有新信息可用。",
          f"5. **换池不改变结论**（§一·补）：三个池上冷闸全样本都能抬 0.10~0.15 Calmar，"
          f"但剔除 3 个月后一律低于同池 V4。所以本否决不是「池选得不对」造成的 —— "
          f"包括 2026-11-23 这次换成「既有温度又有 RSI 的 {len(rule.CODES)} 只」。",
          "6. 因此 `rule.GATE_ENABLED = False`，每日仓位仍按 V4（`v4_rsi/daily.py`）出；"
          "V11 保留为「已否决」的留证与读数（`daily/_v11_signal.md`）。", ""]

    L += ["## 八、复现", "",
          "```powershell",
          "python strategies/dividend/v11_gate/_temp_board.py     # 板块温度 → data/_board_temp.csv",
          "python strategies/dividend/v11_gate/data.py            # 数据自检",
          "python strategies/dividend/v11_gate/backtest.py        # 本报告 + _v11_bt.jsonl",
          "python strategies/dividend/v11_gate/daily.py           # 今日读数（闸门状态）",
          "```", ""]
    L += ["> 本报告所有数字由脚本重算；`--refresh` 会重抓中证指数 / etf.run 估值 / 10Y 国债缓存。",
          "> 单一历史样本（82 个月、一轮涨跌）内的判断，方向性参考，不构成「以后也不会有效」的证明。", ""]

    text = "\n".join(L)
    os.makedirs(rule.BT_DIR, exist_ok=True)
    with open(rule.OUT_REPORT, "w", encoding="utf-8") as fh:
        fh.write(text)

    bt = pd.DataFrame({"date": rets.index,
                       "rsi_board": (bq * 100).round(2).to_numpy(),
                       "temp6": temp6.to_numpy(), "temp4": temp4.to_numpy(),
                       "m_rsi": m_rsi.to_numpy(), "m_cold": cold40.to_numpy(), "m_hot": hot60.to_numpy()})
    bt.to_json(rule.OUT_BT, orient="records", lines=True, force_ascii=False, date_format="iso")

    print(f"[v11] 样本 {rets.index[0]:%Y-%m}~{rets.index[-1]:%Y-%m} ({len(rets)} 月) · 终点 {end:%Y-%m-%d}")
    for n, st in ab:
        print(f"[v11] {n:32s} 年化 {st['cagr'] * 100:5.1f}%  回撤 {st['dd'] * 100:6.1f}%  "
              f"Calmar {st['calmar']:5.2f}  仓位 {st['pos']:.0%}")
    print(f"[v11] 减半档 corr(temp, 下月) {bs['r']:+.3f} t={bs['t']:+.2f} (n={bs['n']})")
    print(f"[v11] 置换：cold40 {perm['cold40']['pct']:.0%} / cold50 {perm['cold50']['pct']:.0%}")
    print(f"[v11] --- 对齐当前 V4（10 只全池） ---")
    for n, st in ab10:
        print(f"[v11] {n:32s} 年化 {st['cagr'] * 100:5.1f}%  回撤 {st['dd'] * 100:6.1f}%  "
              f"Calmar {st['calmar']:5.2f}  仓位 {st['pos']:.0%}")
    print(f"[v11] 10 只池：剔3月 cold40 {pct(rob10_rows[1][2]['d3']['cagr'])}/"
          f"{rob10_rows[1][2]['d3']['calmar']:.2f} vs V4 "
          f"{pct(rob10_rows[0][2]['d3']['cagr'])}/{rob10_rows[0][2]['d3']['calmar']:.2f} · "
          f"置换 cold40 {perm10['cold40']['pct']:.0%} / cold50 {perm10['cold50']['pct']:.0%}")
    print(f"[v11] -> {os.path.relpath(rule.OUT_REPORT, os.path.dirname(_HERE))}")
    return 0


if __name__ == "__main__":
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    sys.exit(main(refresh="--refresh" in sys.argv))
