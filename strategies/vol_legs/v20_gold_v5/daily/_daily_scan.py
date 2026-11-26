# -*- coding: utf-8 -*-
"""v20 每日操作：黄金 · `(V5 + V12)/2 × 波动上限 TV15%`。

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


def act_of(cur, prev):
    if cur - prev > 1e-9:
        return f"买入 → {cur:.0%}"
    if prev - cur > 1e-9:
        return f"卖出 → {cur:.0%}"
    return "买入并持有" if cur > 1e-9 else "空仓"


def main():
    rate_note = refresh_rate()
    px = engine.load()
    df = engine.detail(px).reindex(px.index)
    d = engine.parts(px)
    w = (d["e2"] * d["cap"]).reindex(px.index).fillna(0.0).clip(0.0, 1.0)
    cur, prev = float(w.iloc[-1]), float(w.iloc[-2])
    action = act_of(cur, prev)
    last = df.iloc[-1]

    date = str(px.index[-1].date())
    v5_last = float(d["v5"].iloc[-1])
    v12_last = float(d["v12"].iloc[-1])
    cap_last = float(d["cap"].iloc[-1])
    gate_txt = (f"波动 {last['vol']:.1%} → V5 预算 {last['scale']:.2f}；"
                f"实际利率 {last['rate_level']:+.2f}% → 闸门 {last['gate']:.2f}；"
                f"V5 {v5_last:.0%} + V12 {v12_last:.0%} → E2 {(v5_last + v12_last) / 2:.0%}，"
                f"TV15 上限系数 {cap_last:.0%}")

    recent = []
    for i in range(max(1, len(w) - RECENT), len(w)):
        a, b = float(w.iloc[i]), float(w.iloc[i - 1])
        if abs(a - b) > 1e-9:
            recent.append(dict(date=str(px.index[i].date()),
                               action="买入" if a > b else "卖出",
                               from_pct=round(b, 4), to_pct=round(a, 4)))
    leg = dict(leg=engine.LEG, name=engine.LABEL, code="518880",
               action=action, state=round(prev, 4), pending=round(cur, 4),
               pos=round(prev, 4), level_txt=f"当前 {prev:.0%} → 目标 {cur:.0%}",
               date=date, close=round(float(px.iloc[-1]), 4))

    snap = dict(strategy="v20", name=engine.LABEL, status=engine.STATUS,
                leg=engine.LEG, as_of=date, generated=str(pd.Timestamp.now().date()),
                rule=dict(rule=engine.RULE_TXT, trade_at="T 日收盘信号 → T+1 收盘成交",
                          fee_side=engine.FEE_SIDE, cash_annual=engine.CASH_ANNUAL,
                          rate_source="FRED DFII10（data/_dfii10.csv）"),
                current_position=round(prev, 4), next_target=round(cur, 4),
                action=action, legs=[leg], recent_flow=recent,
                reading=gate_txt, rate_note=rate_note,
                note="2026-11-26 用户决定：黄金由 V5 原样改为 E2×TV15（V5 + V12 等权 × TV15）；"
                     "研究最优是 E2×TV10，因换手 5.3→57.9 次/年且 XAU 长样本反向而放弃。")
    with io.open(OUT_JSON, "w", encoding="utf-8", newline="\n") as f:
        json.dump(snap, f, ensure_ascii=False, indent=2)

    L = ["# v20 每日操作 · 黄金", "",
         f"> **{engine.STATUS}** · 数据截至 {date} · T 日盘后出信号、T+1 收盘前执行。", "",
         "## 今日动作", "",
         f"- 当前持仓：**{prev:.0%}**；次日目标仓位：**{cur:.0%}**；动作：**{action}**。",
         f"- 标的：黄金（`518880`，真实指数口径）；策略：`(V5 + V12)/2 × 波动上限 TV15%`。", "",
         "## 规则", "", f"{engine.RULE_TXT}。", "",
         f"- 当日读数：{gate_txt}。",
         f"- 上一交易日收盘仓位 {prev:.0%} → 今日收盘成交后 {cur:.0%}。", "",
         f"> {engine.STATUS}。研究出处 `beat_best/_beat_best_top3.md`（2026-11-26）："
         "黄金 ★1 = V5（同尺 1.06 / 后段 1.55），`E2×TV15` 过双闸门（后段 1.6x），"
         "`E2×TV10` 更优（1.08 / 1.87）但换手 57.9 次/年、XAU 19.5 年反向 → 取 TV15。", "",
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
    print(f"  {engine.LEG}: {prev:.0%} -> {cur:.0%}  {action}")
    if rate_note:
        print("  [数据]", rate_note)


if __name__ == "__main__":
    main()
