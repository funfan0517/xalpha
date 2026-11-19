# -*- coding: utf-8 -*-
"""v21 口径扫描：把「回测要控制的变量」逐条拨一遍，看结论是**稳健**还是**靠调参**。

只改一个变量、其余保持主口径（10bp / 货基 1.40% / 次日收盘成交），
在同一批样本上重跑状态机 → 年化 / 回撤 / 夏普 / 曝光 / 交易次数。

产物：`research/_sweep.md`。机械规则输出，非投资建议。
用法：`python strategies/vol_legs/v21_livermore_keypoint/research/_sweep.py`
"""
import io
import os
import sys

import numpy as np
import pandas as pd

try:
    sys.stdout.reconfigure(encoding="utf-8")  # type: ignore[attr-defined]
except Exception:  # noqa: BLE001
    pass

_HERE = os.path.dirname(os.path.abspath(__file__))
_STRAT = os.path.dirname(_HERE)
if _STRAT not in sys.path:
    sys.path.insert(0, _STRAT)

import engine  # noqa: E402

OUT = os.path.join(_HERE, "_sweep.md")
SPLIT = pd.Timestamp("2016-01-01")

# 跨标的对照（同一套规则、不同市场）：这是比时间切分更接近样本外的检验。
CROSS = [("hs300", "沪深300（本腿）"), ("spx", "标普500"), ("ndx", "纳指100"),
         ("kc50", "科创50（2019-12 起）"), ("gold", "黄金 518880（2013 起）")]

VARIANTS = [
    ("主口径 · 只止损（视频原教旨）", {}),
    ("退出：新高回落 20%", dict(exit_mode="trail", trail_pct=0.20)),
    ("退出：新高回落 15%", dict(exit_mode="trail", trail_pct=0.15)),
    ("退出：新高回落 12%", dict(exit_mode="trail", trail_pct=0.12)),
    ("退出：新高回落 10%", dict(exit_mode="trail", trail_pct=0.10)),
    ("退出：新高回落 8%", dict(exit_mode="trail", trail_pct=0.08)),
    ("退出：固定持有 250 根", dict(exit_mode="hold_days", max_hold=250)),
    ("退出：固定持有 500 根", dict(exit_mode="hold_days", max_hold=500)),
    ("过滤：量能 > 1.2 × MA20", dict(filter_vol=1.2)),
    ("过滤：MA20 上拐", dict(filter_ma_up=True)),
    ("过滤：周线确认", dict(filter_weekly=True)),
    ("过滤：量能 + MA20 上拐 + 周线", dict(filter_vol=1.2, filter_ma_up=True, filter_weekly=True)),
    ("加仓锚：加权平均成本（而非首买价）", dict(add_anchor="avg")),
    ("止损后冷静期 20 根", dict(cooldown=20)),
    ("止损后冷静期 60 根", dict(cooldown=60)),
    ("触发线：回撤 15%", dict(dd_trigger=0.15)),
    ("触发线：回撤 25%", dict(dd_trigger=0.25)),
    ("触发线：回撤 30%", dict(dd_trigger=0.30)),
    ("止损：首买价 -8%", dict(stop_pct=0.08)),
    ("止损：首买价 -12%", dict(stop_pct=0.12)),
    ("成交：当日收盘视同成交", dict(exec_lag=0)),
    ("去掉回撤闸门（任何 20 日突破都试仓）", dict(dd_trigger=0.0)),
]


def metrics(px, hold, cash=engine.CASH_ANNUAL, fee=engine.FEE_SIDE):
    nav = engine.run(px, hold, cash=cash, fee=fee)
    m = engine.perf(nav)
    return m, nav


def trades(res, start):
    ev = [e for e in res["events"] if pd.Timestamp(e["date"]) >= start]
    n_entry = sum(1 for e in ev if e["kind"] == "entry")
    n_add = sum(1 for e in ev if e["kind"] == "add")
    n_exit = sum(1 for e in ev if e["kind"] in ("stop", "trail", "timeout"))
    return n_entry, n_add, n_exit


def main():
    px_full = engine.load()
    res0 = engine.analyze(px_full)
    start = px_full.index[min(engine.WARMUP, len(px_full) - 2)]
    px = px_full.loc[start:]

    bh = engine.perf(px / px.iloc[0])
    dip = engine.perf(engine.run(px, engine.hold_buy_dip(px_full).loc[start:]))
    base_nav = engine.run(px, res0["hold"].loc[start:])
    base = engine.perf(base_nav)

    rows = []
    for name, p in VARIANTS:
        res = engine.analyze(px_full, p=p)
        hold = res["hold"].loc[start:]
        m, nav = metrics(px, hold)
        ne, na, nx = trades(res, start)
        pre = engine.perf(nav.loc[:SPLIT])
        post = engine.perf(nav.loc[SPLIT:])
        rows.append(dict(name=name, p=p, m=m, nav=nav,
                         expo=float(hold.mean()), ne=ne, na=na, nx=nx,
                         pre=pre, post=post))

    L = ["# v21 口径扫描 · 利弗莫尔关键点（沪深300）", "",
         f"> 由 `strategies/vol_legs/v21_livermore_keypoint/research/_sweep.py` 生成。"
         f"样本 {px.index[0].date()} ~ {px.index[-1].date()}，成本单边 10bp、空仓货基 "
         f"{engine.CASH_ANNUAL:.2%}、次日收盘成交。**只改一个变量**，其余保持主口径。", "",
         "## 基准", "",
         "| 口径 | 年化 | 最大回撤 | 夏普 | 累计 |", "|---|---:|---:|---:|---:|",
         f"| 主口径（只止损） | {base['ann']:+.2%} | {base['mdd']:.2%} | {base['sharpe']:.2f} | "
         f"{base['cum']:+.1%} |",
         f"| 一把抄底 | {dip['ann']:+.2%} | {dip['mdd']:.2%} | {dip['sharpe']:.2f} | "
         f"{dip['cum']:+.1%} |",
         f"| 买入持有 | {bh['ann']:+.2%} | {bh['mdd']:.2%} | {bh['sharpe']:.2f} | "
         f"{bh['cum']:+.1%} |", "",
         "## 逐项对照", "",
         "| 变体 | 年化 | 最大回撤 | 夏普 | 累计 | 曝光 | 建仓/加仓/退出 | 前段夏普 | 后段夏普 |",
         "|---|---:|---:|---:|---:|---:|---|---:|---:|",
         ]
    for r in rows:
        m = r["m"]
        L.append(f"| {r['name']} | {m['ann']:+.2%} | {m['mdd']:.2%} | {m['sharpe']:.2f} | "
                 f"{m['cum']:+.1%} | {r['expo']:.0%} | {r['ne']}/{r['na']}/{r['nx']} | "
                 f"{r['pre']['sharpe']:.2f} | {r['post']['sharpe']:.2f} |")

    L += ["", "## 读法（先看这几条，再看表）", "",
          "- **「只止损」会退化成长期持有**：2008-11 建仓、2009-02 加满，首买价止损线"
          "（1788.5）此后从未被触及，于是 2009 年至今一直是 100% 仓位。这既是视频口径的"
          "必然结果，也是它 2009 年之后回撤 −46.7% 的原因 —— **不加退出规则，这套规则只有"
          "前半段是「策略」，后半段是长期持有**。",
          "- **新高回落不是「改善」，是「换风险」**：把「加满后不退出」换成「自持仓最高收盘"
          "回落 N% 清仓」，回撤从 −46.7% 压到 −14%~−37%，代价是年化从 +3.11% 掉到 "
          "+1.2%~+2.0%、夏普基本归零。它买到的是回撤，不是收益；N 越小越像短趋势，"
          "越大越接近长期持有。**默认仍按视频口径只止损**，trail 作为可选开关。",
          "- **回撤闸门/假突破过滤在沪深300 上几乎不起作用**：能建仓的机会只有 2008-04 与 "
          "2008-11 两次（且两次都在 −30% 深跌里），把闸门从 15% 调到 30% 完全改变不了结果；"
          "止损放到 −8% 只多出一次 2008-05 的重建仓；量能/MA20/周线过滤只会让第二次建仓更晚"
          "甚至消失。样本里只有 2 笔，**这些变量的差异不是「稳健性证据」**。",
          "- **「固定持有 500 根」在样本里险胜**（+4.63% / −33.65% / 0.25），但 250 根就掉到 "
          "+1.81% / −29.20% / 0.04 —— 同一个变量的两档差这么多，说明这不是稳定结构，"
          "只是样本里的巧合；**不要因为这一行就去改用固定持有期**。",
          "- **加仓锚换成加权均价**几乎无差别（那两笔加仓发生在 4 个交易日内），"
          "但在更长/更慢的趋势里会让金字塔更容易半途而废（成本被抬高）。",
          "- **前段/后段只是时间切分**，不是样本外；真正的样本外要看下面「跨标的」表。", "",
          "## 跨标的（粗略样本外）", "",
          "同一套规则换到别的指数上重跑（各自从可用数据起、预热 252 根）。"
          "标普500 / 纳指100 与 A 股的市场结构不同，这条比时间切分更接近样本外。", "",
          "| 标的 | 样本 | 买入持有 年化/回撤/夏普 | 只止损 年化/回撤/夏普/曝光 | "
          "回落 15% 年化/回撤/夏普/曝光 |",
          "|---|---|---|---|---|",
          ]
    for leg, name in CROSS:
        try:
            pxl = engine.load(leg)
        except Exception as exc:  # noqa: BLE001
            L.append(f"| {name} | — | 数据不可用 `{type(exc).__name__}` | — | — |")
            continue
        s0 = pxl.index[min(engine.WARMUP, len(pxl) - 2)]
        pl = pxl.loc[s0:]
        mbh = engine.perf(pl / pl.iloc[0])
        cells = []
        for par in ({}, dict(exit_mode="trail", trail_pct=0.15)):
            rr = engine.analyze(pxl, p=par)
            mm = engine.perf(engine.run(pl, rr["hold"].loc[s0:]))
            cells.append(f"{mm['ann']:+.2%} / {mm['mdd']:.2%} / {mm['sharpe']:.2f} / "
                         f"{float(rr['hold'].loc[s0:].mean()):.0%}")
        L.append(f"| {name} | {pl.index[0].date()} ~ {pl.index[-1].date()} | "
                 f"{mbh['ann']:+.2%} / {mbh['mdd']:.2%} / {mbh['sharpe']:.2f} | "
                 f"{cells[0]} | {cells[1]} |")

    L += ["", "> 机械规则输出，非投资建议。"]
    with io.open(OUT, "w", encoding="utf-8", newline="\n") as f:
        f.write("\n".join(L) + "\n")
    print("WROTE", OUT)
    for r in rows:
        m = r["m"]
        print(f"  {r['name']:32s} {m['ann']:+7.2%} 回撤 {m['mdd']:7.2%} 夏普 {m['sharpe']:5.2f} "
              f"曝光 {r['expo']:4.0%} 建仓 {r['ne']} 退出 {r['nx']}")


if __name__ == "__main__":
    main()
