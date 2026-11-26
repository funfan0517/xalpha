# -*- coding: utf-8 -*-
"""v20 回测：黄金 · `(V5 + V12)/2 × 波动上限 TV15%`（2026-11-26 由「V5 原样」改）。

产物：`backtest/_<v>_bt.md` / `.jsonl`。机械规则输出，非投资建议。
用法：`python strategies/vol_legs/v20_gold_v5/backtest.py`
"""
import io
import json
import math
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

import engine  # noqa: E402

OUTDIR = os.path.join(_HERE, "backtest")
MD = os.path.join(OUTDIR, "_v20_bt.md")
JSONL = os.path.join(OUTDIR, "_v20_bt.jsonl")


def main():
    os.makedirs(OUTDIR, exist_ok=True)
    px_full = engine.load()
    w_full = engine.weight(px_full)                       # 全样本算信号，避免状态层被截断重置
    start = px_full.index[min(engine.WARMUP, len(px_full) - 2)]
    px, w = px_full.loc[start:], w_full.loc[start:]
    nav = engine.run(px, w)
    bh = px / px.iloc[0]
    m, mb = engine.perf(nav), engine.perf(bh)
    hold = w.shift(1).fillna(0.0)
    dw = hold.diff().fillna(hold)
    years = len(px) / 252.0
    acts = int((dw.abs() > 1e-9).sum())

    row = dict(leg=engine.LEG, name=engine.LABEL, status=engine.STATUS,
               code="518880", strategy="v20",
               start=str(px.index[0].date()), end=str(px.index[-1].date()),
               years=round(years, 2), bars=len(px),
               ann=round(m["ann"], 4), vol=round(m["vol"], 4),
               mdd=round(m["mdd"], 4), sharpe=round(m["sharpe"], 3),
               calmar=round(m["calmar"], 3), expo=round(float(hold.mean()), 4),
               acts=acts, acts_py=round(acts / years, 2),
               bh_ann=round(mb["ann"], 4), bh_mdd=round(mb["mdd"], 4),
               bh_sharpe=round(mb["sharpe"], 3),
               fee_side=engine.FEE_SIDE, cash_annual=engine.CASH_ANNUAL,
               note="2026-11-26 用户决定：黄金由 V5 原样改为 E2×TV15（V5 + V12 等权 × TV15）；"
                    "研究最优是 E2×TV10（1.08 / 后段 1.87），因换手 5.3→57.9 次/年且 XAU 反向而放弃。")
    with io.open(JSONL, "w", encoding="utf-8", newline="\n") as f:
        f.write(json.dumps(row, ensure_ascii=False) + "\n")

    cut = px.index[int(len(px) * 0.60)]
    # 后段必须先把净值归 1 再算：直接切 nav 会让年化沿用起点之前累积的涨幅，后段夏普被系统性放大
    post = engine.perf(nav.loc[cut:] / float(nav.loc[cut:].iloc[0]))
    L = [
        "# v20 回测报告 · 黄金", "",
        f"> 由 `strategies/vol_legs/v20_gold_v5/backtest.py` 生成。**{engine.STATUS}**。", "",
        "## 规则", "",
        f"{engine.RULE_TXT}。", "",
        f"逐日权重来自原生引擎（`_top3_bridge.py` → `v5_gold_score/score.py`、`v12_octo_trend/engine.py`），"
        f"t 日收盘决策 → T+1 收益；统一单边 {engine.FEE_SIDE:.2%}、"
        f"空仓 {engine.CASH_ANNUAL:.2%}；预热 {engine.WARMUP} 根。", "",
        "> 研究出处：`research/beat_best/_beat_best_top3.md`（2026-11-26 重跑，候选池加入 TV15/TV10）——"
        "黄金 ★1 = V5（同尺 1.06 / 后段 1.55）；`E2×TV20` 首次过闸门后，TV 目标越紧夏普越高："
        "TV20 → 1.0x、**TV15 → 已采纳**、TV10 → 1.08 / 后段 1.87。"
        "**用户 2026-11-26 决定取 TV15**：TV10 的换手 5.3 → 57.9 次/年，"
        "且同一技法在 XAU 19.5 年上反向（夏普 0.79→0.72、回撤 −13.9%→−19.3%）。", "",
        "## 结果", "",
        "| 口径 | 年化 | 波动 | 最大回撤 | 夏普 | Calmar | 平均仓位 | 调仓/年 |",
        "|---|---:|---:|---:|---:|---:|---:|---:|",
        f"| v20 择时 | {m['ann']:+.2%} | {m['vol']:.2%} | {m['mdd']:.2%} | "
        f"{m['sharpe']:.2f} | {m['calmar']:.2f} | {hold.mean():.0%} | {acts / years:.1f} |",
        f"| 买入持有 | {mb['ann']:+.2%} | {mb['vol']:.2%} | {mb['mdd']:.2%} | "
        f"{mb['sharpe']:.2f} | {mb['calmar']:.2f} | 100% | 0.0 |", "",
        f"样本 {px.index[0].date()} ~ {px.index[-1].date()}（{len(px)} 根 / {years:.1f} 年）；"
        f"后段（自 {cut.date()}）夏普 **{post['sharpe']:.2f}**。", "",
        "## 口径与边界", "",
        "- 单一标的策略，**不做跨腿配权**；与其他版本同尺比较时先看费率列。",
        "- 成本为研究口径单边 10bp，与 V5 自己的产物（场内万 2.5 单边）不同，"
        "绝对数不可与 `best_by_leg.md` 的 1.02 直接对账（同为含费但费率档不同）。",
        "- 实际利率取 FRED `DFII10` 缓存（`data/_dfii10.csv`），T 日只用 ≤T−1 的观测；"
        "缓存过期时每日扫描会尝试联网补一次，失败则沿用缓存并在报告里标注。",
        "- 图中长期与近三年两张 PNG 由 `research/_plot_leg.py` 生成；"
        "近三年窗口仍用全样本算信号，只截末端并把净值归 1。",
        "", "> 机械规则输出，非投资建议。",
    ]
    with io.open(MD, "w", encoding="utf-8", newline="\n") as f:
        f.write("\n".join(L) + "\n")
    print("WROTE", MD)
    print("WROTE", JSONL)


if __name__ == "__main__":
    main()
