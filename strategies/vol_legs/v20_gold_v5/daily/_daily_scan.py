# -*- coding: utf-8 -*-
"""v20 每日操作：黄金 · V5 黄金仓位（风险预算 × 利率闸门）· 当前 ★1。

T 日盘后算决策 → T+1 收盘前执行。行情只读本地缓存；**实际利率（FRED DFII10）过期时联网补一次**
（拿不到就沿用缓存并在报告里标注，不中断流程 —— 闸门是慢变量，缺一天不影响档位）。

产物：`daily/_<v>_daily.md` / `.json`。机械规则输出，非投资建议。
"""
import io
import json
import os
import sys

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
from _top3_bridge import bridge  # noqa: E402

OUT_MD = os.path.join(_HERE, "_v20_daily.md")
OUT_JSON = os.path.join(_HERE, "_v20_daily.json")
RECENT = 20


def refresh_rate():
    """尽量补 FRED 实际利率 -> (说明 or ""). 失败只降级，不抛。"""
    F = bridge.v5_factors
    try:
        if not F.rate_is_stale():
            return ""
        _s, note = F.refresh_rate()
        return note
    except Exception as exc:  # noqa: BLE001
        return f"实际利率刷新失败（沿用缓存）：{type(exc).__name__}: {exc}"


def main():
    rate_note = refresh_rate()
    px = engine.load()
    df = engine.detail(px).reindex(px.index)
    w = engine.weight(px).reindex(px.index).fillna(0.0).clip(0.0, 1.0)
    cur, prev = float(w.iloc[-1]), float(w.iloc[-2])
    last = df.iloc[-1]

    # `state[-1]` = t 日收盘成交后的仓位；`action[-1]` / `target[-1]` = 次日的动作与目标档。
    pos = float(last["state"])
    nxt = float(last["target"]) if str(last["action"]) != "持有" else pos
    action = (f"{last['action']} → {nxt:.0%}" if abs(nxt - pos) > 1e-9
              else ("买入并持有" if pos > 1e-9 else "空仓"))
    date = str(px.index[-1].date())
    gate_txt = (f"波动 {last['vol']:.1%} → 预算 {last['scale']:.2f}；"
                f"实际利率 {last['rate_level']:+.2f}% → 闸门 {last['gate']:.2f}")

    recent = []
    for i in range(max(1, len(w) - RECENT), len(w)):
        a, b = float(w.iloc[i]), float(w.iloc[i - 1])
        if abs(a - b) > 1e-9:
            recent.append(dict(date=str(px.index[i].date()),
                               action="买入" if a > b else "卖出",
                               from_pct=round(b, 4), to_pct=round(a, 4)))
    leg = dict(leg=engine.LEG, name=engine.LABEL, code="518880",
               action=action, state=round(pos, 4), pending=round(nxt, 4),
               pos=round(pos, 4), level_txt=f"当前 {pos:.0%} → 目标 {nxt:.0%}",
               date=date, close=round(float(px.iloc[-1]), 4))

    snap = dict(strategy="v20", name=engine.LABEL, status=engine.STATUS,
                leg=engine.LEG, as_of=date, generated=str(pd.Timestamp.now().date()),
                rule=dict(rule=engine.RULE_TXT, trade_at="T 日收盘信号 → T+1 收盘成交",
                          fee_side=engine.FEE_SIDE, cash_annual=engine.CASH_ANNUAL,
                          rate_source="FRED DFII10（data/_dfii10.csv）"),
                current_position=round(pos, 4), next_target=round(nxt, 4),
                action=action, legs=[leg], recent_flow=recent,
                reading=gate_txt, rate_note=rate_note,
                note="best_by_leg.md §0（2026-11-19）：黄金 ★1 = V5；"
                     "_beat_best_top3.md 九个组合候选无一通过双闸门。")
    with io.open(OUT_JSON, "w", encoding="utf-8", newline="\n") as f:
        json.dump(snap, f, ensure_ascii=False, indent=2)

    L = ["# v20 每日操作 · 黄金", "",
         f"> **{engine.STATUS}** · 数据截至 {date} · T 日盘后出信号、T+1 收盘前执行。", "",
         "## 今日动作", "",
         f"- 当前持仓：**{pos:.0%}**；次日目标仓位：**{nxt:.0%}**；动作：**{action}**。",
         f"- 标的：黄金（`518880`，真实指数口径）；策略：V5 黄金仓位（风险预算 × 利率闸门）。", "",
         "## 规则", "", f"{engine.RULE_TXT}。", "",
         f"- 当日读数：{gate_txt}（目标档已按 20pp 量化，跨 1 档才动）。",
         f"- 上一交易日收盘仓位 {prev:.0%} → 今日收盘成交后 {cur:.0%}。", "",
         f"> {engine.STATUS}。best_by_leg.md §0：黄金 ★1 = V5（含费夏普 1.02 / 回撤 −12.33%，"
         "V6-B 退到 ★2 的 0.74 / −16.12%）。", "",
         "## 近期信号流水", "",
         "| 日期 | 动作 | 仓位变化 |", "|---|---|---:|"]
    if recent:
        for x in recent:
            L.append(f"| {x['date']} | {x['action']} | {x['from_pct']:.0%} → {x['to_pct']:.0%} |")
    else:
        L.append("| — | 近 20 日无仓位变化 | — |")
    L += ["", f"成本口径：统一单边 {engine.FEE_SIDE:.2%}（研究同尺），空仓货基 "
          f"{engine.CASH_ANNUAL:.2%}。"]
    if rate_note:
        L.append(f"实际利率：{rate_note}")
    L.append("机械规则输出，非投资建议。")
    with io.open(OUT_MD, "w", encoding="utf-8", newline="\n") as f:
        f.write("\n".join(L) + "\n")
    print("WROTE", OUT_MD)
    print("WROTE", OUT_JSON)
    print(f"  {engine.LEG}: {pos:.0%} -> {nxt:.0%}  {action}")
    if rate_note:
        print("  [数据]", rate_note)


if __name__ == "__main__":
    main()
