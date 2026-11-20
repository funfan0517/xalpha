# -*- coding: utf-8 -*-
"""v16 每日操作：纳指100 · MA200 切换（上=V10 波动层 / 下=V12 八态趋势）。

T 日盘后算决策 → T+1 收盘前执行。只读本地缓存，不联网刷新（与 v2/v13 同纪律）。
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

OUT_MD = os.path.join(_HERE, "_v16_daily.md")
OUT_JSON = os.path.join(_HERE, "_v16_daily.json")
RECENT = 20
RULE_TXT = "MA200 上方用 V10 波动层（ER20/10 + 5 日死区），下方用 V12 八态趋势"
NOTE = ("_beat_best_top3.md（2026-11-20 第三次重跑）：SW MA200→V12 同尺夏普 0.77→0.81、"
        "后段 0.94→1.01、回撤 −27.38%→−12.97%；MA 100~250 邻域仍全部过线。")


def act_of(cur, prev):
    if cur - prev > 1e-9:
        return f"买入 → {cur:.0%}"
    if prev - cur > 1e-9:
        return f"卖出 → {cur:.0%}"
    return "买入并持有" if cur > 1e-9 else "空仓"


def main():
    px = engine.load()
    d = engine.detail(px)
    w = d["weight"].reindex(px.index).fillna(0.0).clip(0.0, 1.0)
    cur, prev = float(w.iloc[-1]), float(w.iloc[-2])
    action = act_of(cur, prev)
    date = str(px.index[-1].date())
    close = float(px.iloc[-1])
    ma = float(d["ma"].iloc[-1])
    above = bool(d["gate"].iloc[-1] > 0.5)
    side = "MA200 上方 → 用 V10 波动层" if above else "MA200 下方 → 用 V12 八态趋势"
    hi_now, lo_now = float(d["hi"].iloc[-1]), float(d["lo"].iloc[-1])

    recent = []
    for i in range(max(1, len(w) - RECENT), len(w)):
        a, b = float(w.iloc[i]), float(w.iloc[i - 1])
        if abs(a - b) > 1e-9:
            recent.append(dict(date=str(px.index[i].date()),
                               action="买入" if a > b else "卖出",
                               from_pct=round(b, 4), to_pct=round(a, 4)))
    leg = dict(leg=engine.LEG, name=engine.LABEL, code="270042",
               action=action, state=round(prev, 4), pending=round(cur, 4),
               pos=round(prev, 4), level_txt=f"当前 {prev:.0%} → 目标 {cur:.0%}",
               date=date, close=round(close, 4))

    snap = dict(strategy="v16", name=engine.LABEL, status=engine.STATUS,
                leg=engine.LEG, as_of=date, generated=str(pd.Timestamp.now().date()),
                rule=dict(rule=RULE_TXT, trade_at="T 日收盘信号 → T+1 收盘成交",
                          fee_side=engine.FEE_SIDE, cash_annual=engine.CASH_ANNUAL,
                          ma_win=engine.MA_WIN),
                gate=dict(side=side, above_ma=above, close=round(close, 4),
                          ma=round(ma, 4), gap=round(close / ma - 1.0, 4)),
                branch=dict(v10=round(hi_now, 4), v12=round(lo_now, 4)),
                current_position=round(prev, 4), next_target=round(cur, 4),
                action=action, legs=[leg], recent_flow=recent, note=NOTE)
    with io.open(OUT_JSON, "w", encoding="utf-8", newline="\n") as f:
        json.dump(snap, f, ensure_ascii=False, indent=2)

    L = ["# v16 每日操作 · 纳指100（MA200 切换）", "",
         f"> **{engine.STATUS}** · 数据截至 {date} · T 日盘后出信号、T+1 收盘前执行。", "",
         "## 今日动作", "",
         f"- 当前持仓：**{prev:.0%}**；次日目标仓位：**{cur:.0%}**；动作：**{action}**。",
         f"- 标的：纳指100（`270042`，真实指数口径）；策略：MA200 切换（上=V10 / 下=V12）。", "",
         "## 闸门读数", "",
         f"- 收盘 {close:.2f} vs MA200 {ma:.2f}（偏离 **{close / ma - 1:+.1%}**）"
         f" → **{side}**。",
         f"- 两条腿各自的目标仓位：V10 波动层 **{hi_now:.0%}** · V12 八态趋势 **{lo_now:.0%}**"
         f"（生效档 = {cur:.0%}，取闸门选中的那条）。", "",
         "## 规则", "", f"{RULE_TXT}；闸门为 t 日收盘 `px>MA200`，T+1 收益生效。", "",
         f"> {engine.STATUS}。{NOTE}", "",
         "## 近期信号流水", "",
         "| 日期 | 动作 | 仓位变化 |", "|---|---|---:|"]
    if recent:
        for x in recent:
            L.append(f"| {x['date']} | {x['action']} | {x['from_pct']:.0%} → {x['to_pct']:.0%} |")
    else:
        L.append("| — | 近 20 日无仓位变化 | — |")
    L += ["", f"成本口径：统一单边 {engine.FEE_SIDE:.2%}（研究同尺），空仓货基 "
          f"{engine.CASH_ANNUAL:.2%}。机械规则输出，非投资建议。"]
    with io.open(OUT_MD, "w", encoding="utf-8", newline="\n") as f:
        f.write("\n".join(L) + "\n")
    print("WROTE", OUT_MD)
    print("WROTE", OUT_JSON)
    print(f"  {engine.LEG}: {prev:.0%} -> {cur:.0%}  {action}  [{side}]")


if __name__ == "__main__":
    main()
