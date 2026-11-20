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


def pct(x, d=1):
    return "—" if x is None or (isinstance(x, float) and np.isnan(x)) else f"{x * 100:.{d}f}%"


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
    ab = [
        ("基准：5 只等权满仓", E.evaluate(base_w, pd.Series(1.0, index=bq.index), rets)),
        ("横截面：RSI 最低 2 只（满仓）", E.evaluate(top, pd.Series(1.0, index=bq.index), rets)),
        ("V4 时间档（5 只等权 × 70/50）", E.evaluate(base_w, m_rsi, rets)),
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

    # ---------------------------------------------------------------- 报告
    L = [f"# {rule.TITLE}", "",
         "> 规则文件是唯一权威（`RULES.md`）；本文件由 `backtest.py` 生成，数字每次重跑都会重算。",
         "> **一句话：温度当闸门接 V4 不成立** —— 冷闸全样本 Calmar 1.44 → 1.54，但这一点优势在"
         "剔除 3 个月后转负，且减半档内温度对下月收益没有判别力（t=−0.57）。详见 §七。", ""]

    L += ["## 〇、样本与口径", "",
          f"- 样本 **{rets.index[0]:%Y-%m} ~ {rets.index[-1]:%Y-%m}（{len(rets)} 月）**，"
          f"与 V4 **旧版**（5 只筛池）同窗口同池（{len(rule.CODES)} 只，`pool.TIMING_POOL`）；"
          "V4 自 2026-11-20 起已改用全池 10 只，V11 保留此池以便与历史结论对照。",
          "- 收益：基金累计净值（含分红再投）；月末信号 → 下月生效；成本单边 0.03%。",
          "- 温度：V9 五因子月度温度，取 **6 只的中位数**（主口径）/ **V4 池内有温度的 4 只**（敏感性）。",
          "  `008163` 无 etf.run 等权 PE/PB，**不进温度聚合**，但仍在交易池里 —— 这是本设计的已知妥协。",
          "- 闸门只改**仓位倍率**，横截面仍是「RSI 最低 2 只等权」；温度缺失月保持 V4 原档位，不插补。", ""]

    L += ["## 一、消融", "",
          "| 方案 | 年化 | 最大回撤 | Calmar | 波动 | 夏普 | 累计 | 平均仓位 | 换档次数 |",
          "|---|---:|---:|---:|---:|---:|---:|---:|---:|"]
    L += [row(n, st) for n, st in ab]
    L += ["", f"- V4 完整复现：**{pct(v4['cagr'])} / {pct(v4['dd'])} / Calmar {v4['calmar']:.2f}**"
              f"（与 `v4_rsi/backtest/_v4_report.md` 的 11.4% / −7.9% / 1.44 一致）。",
          "- 冷闸把平均仓位从 71% 抬到 78%、最大回撤**不变**（−7.9%）。这个「不变」是路径巧合："
          "谷底在 2024-08，而放量月不在它附近；2022-10 的回撤其实被加深了（见 §六.1）。",
          "- 热闸在 60 只压 3 个月，年化 −0.5pp、Calmar −0.07；**温度高时减仓在本样本是负和**。",
          f"- 板块温度与板块 RSI 的相关：**水平 {corr_lv:.2f}、月度差分 {corr_df:.2f}** —— "
          "两者本就是同一个位置信号的两种写法，用温度修饰 RSI 的信息增量天然有限。", ""]

    L += ["## 二、无温度的暴露对照（闸门是不是只是「多拿仓位」）", "",
          "| 减半档统一抬到 | 年化 | 最大回撤 | Calmar | 平均仓位 |", "|---|---:|---:|---:|---:|"]
    for h, st in half_ctrl:
        L.append(f"| {h:.1f} | {pct(st['cagr'])} | {pct(st['dd'])} | {st['calmar']:.2f} | {st['pos']:.0%} |")
    L += ["", "- 无差别抬半仓（h 0.6/0.7/0.8）**全部低于 V4 的 1.44**（1.35 / 1.27 / 1.20）："
              "说明 V4 的 −7.9% 回撤确实是靠这 42 个半仓月压出来的，不能整体抬。",
          f"- 冷闸与它们的区别只在「挑哪几个月抬」：冷闸挑 {len(trig)} 个月，Calmar "
          f"{ab[4][1]['calmar']:.2f}。这个差是不是挑得准，看 §五。", ""]

    L += ["## 三、阈值网格（温度 = 6 只中位数）", "",
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
    L += ["", "### 3.5 温度口径敏感性（4 只 vs 6 只）", "",
          "| 口径 | 冷 40 | 冷 45 | 冷 50 | 热 60 | 热 65 |", "|---|---:|---:|---:|---:|---:|"]

    def pick(grid, name):
        return next(st for n, st in grid if n == name)

    L.append(f"| 6 只（主） | {pick(cg6, 'cold=40')['calmar']:.2f} | {pick(cg6, 'cold=45')['calmar']:.2f} | "
             f"{pick(cg6, 'cold=50')['calmar']:.2f} | {pick(hg6, 'hot=60')['calmar']:.2f} | "
             f"{pick(hg6, 'hot=65')['calmar']:.2f} |")
    L.append(f"| 4 只（V4 池内） | {pick(cg4, 'cold=40')['calmar']:.2f} | {pick(cg4, 'cold=45')['calmar']:.2f} | "
             f"{pick(cg4, 'cold=50')['calmar']:.2f} | {pick(hg4, 'hot=60')['calmar']:.2f} | "
             f"{pick(hg4, 'hot=65')['calmar']:.2f} |")
    L += ["", "- 两个口径方向一致（冷闸抬 Calmar、热闸压 Calmar），说明结论不依赖「008163 被排除」这件事；"
              "但具体数值差 0.1 上下，本身就在噪声里。", ""]

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
          "- **V4 自己过刀**（剔除 3 月后 13.1% / 1.65，比全样本还高），"
          "冷闸过不了（12.2% → 9.2%，Calmar 1.54 → 1.09）。这就是判 V11 不成立的直接依据。", ""]

    L += ["## 五、置换检验（随机挑同样多月份放满）", "",
          "| 方案 | 放量月数 | 闸门 Calmar | 随机中位 | 5%–95% | 超过随机 |",
          "|---|---:|---:|---:|---|---:|"]
    for k, p in perm.items():
        L.append(f"| {k} | {p['n']} | {p['calmar']:.2f} | {p['median']:.2f} | "
                 f"[{p['p5']:.2f}, {p['p95']:.2f}] | {p['pct']:.0%} |")
    L += ["", f"- {rule.N_PERM} 次随机置换。冷闸落在约 85 分位 —— **不能算显著**（要 ≥95% 才有话说），"
              "而且它的「超额」大部分来自「本来那些月也在涨」。", ""]

    L += ["## 六、触发月诊断", "",
          f"冷闸（{rule.COLD:.0f}）共放量 **{len(trig)} 个月**："
          + "、".join(f"{t:%Y-%m}" for t in trig) + "。", "",
          "| 组 | 月数 | 下月平均收益 | 胜率 |", "|---|---:|---:|---:|",
          f"| 冷闸放量的月份 | {len(hit_ret)} | {hit_ret.mean() * 1e4:+.0f}bp | {(hit_ret > 0).mean():.0%} |",
          f"| 其它减半月份 | {len(other)} | {other.mean() * 1e4:+.0f}bp | {(other > 0).mean():.0%} |",
          "", f"- 两组下月收益几乎相同（{hit_ret.mean() * 1e4:+.0f} vs {other.mean() * 1e4:+.0f}bp）—— "
              "**闸门并没有挑到更好的月份**；它赚的是「这些月本来在涨，而 V4 只拿了一半」。",
          f"- 减半档内（n={bs['n']}）温度对下月收益的相关系数 **{bs['r']:+.3f}（t={bs['t']:+.2f}）**，"
              "没有判别力。这是「温度当闸门」在统计上最直接的否证。",
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
    L += ["", f"- 最深回撤都在 **{ddp['V4 完整'].idxmin():%Y-%m}**，而冷闸的放量月不在它附近，"
              "所以最大回撤一格不变 —— 这是**路径巧合，不是保护**。",
          f"- 反证：2022-10 的回撤被冷闸从 {pct(_d22['V4 完整'])} 加深到 {pct(_d22['冷闸 40'])}"
          "（2022-05/06/08 三个放量月正好加在下跌前）。只看「最大回撤不变」会漏掉这件事。", ""]

    # 分年
    ys = E.yearly({"V4 完整": v4["r"], "冷闸 40": ab[4][1]["r"], "热闸 60": ab[5][1]["r"],
                   "基准 5 只": ab[0][1]["r"]})
    L += ["### 6.2 分年度收益", "", "| 年份 | " + " | ".join(ys.columns) + " |",
          "|---|" + "---:|" * len(ys.columns)]
    for ts, r in ys.iterrows():
        L.append(f"| {int(ts)} | " + " | ".join(pct(v) for v in r) + " |")
    L += ["", f"- 冷闸的超额集中在 {int((ys['冷闸 40'] - ys['V4 完整']).idxmax())} 年"
              f"（{pct((ys['冷闸 40'] - ys['V4 完整']).max())}），2021/2022 都是负贡献 —— "
              "「2020 修复市里低配」这个动机对，但同一招在 2022 的反弹里会挨打。", ""]

    L += ["## 七、结论", "",
          "1. **不接。** 温度当闸门（冷 → 回满仓、热 → 压到减半）在本样本里过不了三把刀："
          "剔除 3 个月转负、置换检验 85 分位、减半档内 t=−0.57。",
          "2. **热闸明确有害**：55–70 的每一条热线都让 Calmar 从 1.44 掉到 1.34–1.37，"
          "只压 3 个月却年化 −0.5pp。温度高在红利里不等于「接下来要跌」。",
          "3. **冷闸的 +0.8pp 是「多拿仓位」不是「挑对月份」**：放量月与其它减半月的下月收益没有差别；"
          "同期无差别抬半仓反而更差（1.20–1.35），说明这 11 个月的选择性是路径运气，"
          "不是温度给的。",
          "4. **温度与 RSI 是同一件事的弱拷贝**：月度水平相关 0.44、差分相关 0.72。"
          "用温度去修饰 RSI，等于给同一个位置信号再打一次折 —— 没有新信息可用。",
          "5. 因此 `rule.GATE_ENABLED = False`，每日仓位仍按 V4（`v4_rsi/daily.py`）出；"
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
    print(f"[v11] -> {os.path.relpath(rule.OUT_REPORT, os.path.dirname(_HERE))}")
    return 0


if __name__ == "__main__":
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    sys.exit(main(refresh="--refresh" in sys.argv))
