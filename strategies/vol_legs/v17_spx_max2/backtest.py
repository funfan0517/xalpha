# -*- coding: utf-8 -*-
"""v17 回测：标普500 · MAX2 任一在场（V12∪V13）。

产物：`backtest/_<v>_bt.md` / `.jsonl`。机械规则输出，非投资建议。
用法：`python strategies/vol_legs/v17_spx_max2/backtest.py`
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
MD = os.path.join(OUTDIR, "_v17_bt.md")
JSONL = os.path.join(OUTDIR, "_v17_bt.jsonl")


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
               code="017641", strategy="v17",
               start=str(px.index[0].date()), end=str(px.index[-1].date()),
               years=round(years, 2), bars=len(px),
               ann=round(m["ann"], 4), vol=round(m["vol"], 4),
               mdd=round(m["mdd"], 4), sharpe=round(m["sharpe"], 3),
               calmar=round(m["calmar"], 3), expo=round(float(hold.mean()), 4),
               acts=acts, acts_py=round(acts / years, 2),
               bh_ann=round(mb["ann"], 4), bh_mdd=round(mb["mdd"], 4),
               bh_sharpe=round(mb["sharpe"], 3),
               fee_side=engine.FEE_SIDE, cash_annual=engine.CASH_ANNUAL,
               note="_beat_best_top3.md：同尺夏普 0.53→0.62、后段 0.71→0.75；毛夏普持平，增益来自换手下降。")
    with io.open(JSONL, "w", encoding="utf-8", newline="\n") as f:
        f.write(json.dumps(row, ensure_ascii=False) + "\n")

    cut = px.index[int(len(px) * 0.60)]
    post = engine.perf(nav.loc[cut:])
    L = [
        "# v17 回测报告 · 标普500", "",
        f"> 由 `strategies/vol_legs/v17_spx_max2/backtest.py` 生成。**★ 研究超越项**。", "",
        "## 规则", "",
        "max(V12 八态趋势许可上限, V13 九宫格许可上限)——任一认可即持仓。", "",
        f"逐日权重来自原生引擎（`_top3_bridge.py`），t 日收盘决策 → T+1 收益；"
        f"统一单边 {engine.FEE_SIDE:.2%}、空仓 {engine.CASH_ANNUAL:.2%}；预热 {engine.WARMUP} 根。", "",
        f"> 研究出处：`research/beat_best/_beat_best_top3.md`。_beat_best_top3.md：同尺夏普 0.53→0.62、后段 0.71→0.75；毛夏普持平，增益来自换手下降。", "",
        "## 结果", "",
        "| 口径 | 年化 | 波动 | 最大回撤 | 夏普 | Calmar | 平均仓位 | 调仓/年 |",
        "|---|---:|---:|---:|---:|---:|---:|---:|",
        f"| v17 择时 | {m['ann']:+.2%} | {m['vol']:.2%} | {m['mdd']:.2%} | "
        f"{m['sharpe']:.2f} | {m['calmar']:.2f} | {hold.mean():.0%} | {acts / years:.1f} |",
        f"| 买入持有 | {mb['ann']:+.2%} | {mb['vol']:.2%} | {mb['mdd']:.2%} | "
        f"{mb['sharpe']:.2f} | {mb['calmar']:.2f} | 100% | 0.0 |", "",
        f"样本 {px.index[0].date()} ~ {px.index[-1].date()}（{len(px)} 根 / {years:.1f} 年）；"
        f"后段（自 {cut.date()}）夏普 **{post['sharpe']:.2f}**。", "",
        "## 口径与边界", "",
        "- 单一标的策略，**不做跨腿配权**；与其他版本同尺比较时先看费率列。",
        "- 成本为研究口径单边 10bp，与该腿原生费率（如 A 类 60bp 往返 / 场内 16bp）不同，"
        "绝对数不可与 `best_by_leg.md` 直接对账。",
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
