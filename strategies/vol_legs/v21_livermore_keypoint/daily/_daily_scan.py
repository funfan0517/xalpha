# -*- coding: utf-8 -*-
"""v21 每日操作：沪深300 · 利弗莫尔关键点买入法。

T 日盘后算决策 → T+1 收盘成交。只读本地缓存，不联网刷新（与 v14~v20 同纪律）。
产物：`daily/_v21_daily.md` / `.json`。机械规则输出，非投资建议。
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

OUT_MD = os.path.join(_HERE, "_v21_daily.md")
OUT_JSON = os.path.join(_HERE, "_v21_daily.json")
RECENT = 20


def act_of(cur, tgt):
    if tgt - cur > 1e-9:
        return f"买入 → {tgt:.0%}"
    if cur - tgt > 1e-9:
        return f"卖出 → {tgt:.0%}"
    return f"持有 {cur:.0%}" if cur > 1e-9 else "空仓"


def main():
    px = engine.load()
    res = engine.analyze(px)
    hold, st = res["hold"], res["state"]
    cur = float(hold.iloc[-1])
    pend = st.get("pending") or {}
    tgt = float(pend.get("target", cur))
    action = act_of(cur, tgt)
    date = st["date"]
    close = st["close"]

    # 近期成交流水
    recent = []
    for e in res["events"][-RECENT:]:
        recent.append(dict(date=e["date"], sig_date=e["sig_date"], kind=e["kind"],
                           price=round(e["price"], 2),
                           from_pct=e["from_weight"], to_pct=e["to_weight"]))

    dd, trig = st["dd"], st["dd_trigger"]
    key = st["keypoint"]
    gap = (close / key - 1.0) if key else None
    pnl = (close / st["avg_cost"] - 1.0) if (cur > 0 and st.get("avg_cost")) else None

    if pend and pend.get("kind") == "entry":
        level_txt = f"回撤 {dd:.0%}"
    elif pend and pend.get("kind") in ("stop", "trail", "timeout"):
        level_txt = f"浮盈 {pnl:+.0%}" if pnl is not None else f"回撤 {dd:.0%}"
    elif cur > 0 and pnl is not None:
        level_txt = f"浮盈 {pnl:+.0%}"
    else:
        level_txt = f"回撤 {dd:.0%}"

    leg = dict(leg=engine.LEG, name=engine.LABEL, code="000051",
               action=action, state=round(cur, 4),
               pending=(round(tgt, 4) if pend else None),
               pos=round(cur, 4), level_txt=level_txt,
               date=date, close=round(close, 4),
               dd=round(dd, 4), keypoint=None if key is None else round(key, 2),
               first_buy=None if st.get("first_buy") is None else round(st["first_buy"], 2),
               avg_cost=None if st.get("avg_cost") is None else round(st["avg_cost"], 2),
               stop_price=None if st.get("stop_price") is None else round(st["stop_price"], 2),
               next_add_price=(None if st.get("next_add_price") is None
                               else round(st["next_add_price"], 2)),
               adds=st.get("adds"))

    snap = dict(strategy="v21", name=engine.LABEL, status=engine.STATUS,
                leg=engine.LEG, as_of=date, generated=str(pd.Timestamp.now().date()),
                rule=dict(rule="回撤≥20% 后收盘突破 20 日最高收盘 → 次日收盘 20%；"
                               "浮盈 +5/+10/+15% 各加 20/20/40%；收盘破首买价 -10% 清仓",
                          trade_at="T 日收盘信号 → T+1 收盘成交",
                          fee_side=engine.FEE_SIDE, cash_annual=engine.CASH_ANNUAL),
                current_position=round(cur, 4), next_target=round(tgt, 4),
                action=action, legs=[leg], recent_flow=recent,
                note="用户 2026-11-19 视频口径。主口径只有止损、无固定止盈，"
                     "2009-02 加满后一直持有；退出备选见 research/_sweep.md。")
    with io.open(OUT_JSON, "w", encoding="utf-8", newline="\n") as f:
        json.dump(snap, f, ensure_ascii=False, indent=2)

    def _p(v, fmt="{:.2f}"):
        return "—" if v is None else fmt.format(v)

    L = ["# v21 每日操作 · 沪深300", "",
         f"> **用户口径（样本内）** · 数据截至 {date} · T 日盘后出信号、T+1 收盘成交。", "",
         "## 今日动作", "",
         f"- 当前持仓：**{cur:.0%}**；次日目标仓位：**{tgt:.0%}**；动作：**{action}**。",
         f"- 标的：沪深300（`000051`，真实指数口径）；策略：利弗莫尔关键点"
         f"（{engine.PARAMS['lookback']} 日突破 + 20/20/20/40 金字塔 + 首买价 -10% 止损）。", "",
         "## 触发状态", "",
         f"- 距一年高点回撤 **{dd:+.1%}**（触发线 {-trig:.0%}）"
         + ("；**处于可开仓区**，仍需等 20 日关键点突破" if dd <= -trig
            else "；未到触发线，不新开仓")
         + ("（已在场内，该闸门只影响新开仓）" if cur > 0 else ""),
         f"- 20 日关键点 **{_p(key)}**；今收 **{close:.2f}**"
         + (f"（距关键点 {gap:+.1%}）" if gap is not None else ""),
         ]
    if cur > 0:
        L.append(f"- 持仓：首买价 **{_p(st.get('first_buy'))}** · 加权成本 "
                 f"**{_p(st.get('avg_cost'))}** · 已加 {st.get('adds', 0)} 档 · "
                 f"止损线 **{_p(st.get('stop_price'))}** · 下一档加仓 "
                 f"{_p(st.get('next_add_price'))}")
    else:
        L.append("- 空仓。**下跌途中不买**；要等「回撤 ≥20% + 收盘突破 20 日关键点」同时成立。")

    L += ["", "## 规则", "",
          "1. **触发**：距 252 日最高收盘回撤 ≥ 20% 才允许新开仓。",
          f"2. **关键点**：过去 {engine.PARAMS['lookback']} 日最高**收盘价**（不含当日），"
          "`收盘 > 关键点` 才算突破。",
          "3. **建仓**：T 日收盘确认 → T+1 收盘买 20%。",
          "4. **加仓**：相对首买价 +5% / +10% / +15% 各一次，+20% / +20% / +40%；"
          "每根 K 线最多加一档，只在浮盈时加。",
          "5. **止损**：收盘价 ≤ 首买价 × 0.90 → T+1 收盘清仓（收盘判定，不用盘中最低价）。",
          "6. **退出**：主口径只有止损（视频无固定止盈）—— **加满后可能长期持有**，"
          "这正是它 2009 年至今一直满仓的原因。", "",
          "## 成交流水（最近 20 笔）", "",
          "| 信号日 | 成交日 | 动作 | 成交价 | 仓位 |", "|---|---|---|---:|---:|"]
    if recent:
        for x in recent:
            kind = {"entry": "建仓", "add": "加仓", "stop": "止损清仓",
                    "trail": "回落清仓", "timeout": "到期清仓"}.get(x["kind"], x["kind"])
            L.append(f"| {x['sig_date']} | {x['date']} | {kind} | {x['price']:.2f} | "
                     f"{x['from_pct']:.0%} → {x['to_pct']:.0%} |")
    else:
        L.append("| — | — | 无成交 | — | — |")

    L += ["", "## 口径与边界", "",
          f"- 成本单边 {engine.FEE_SIDE:.2%}（研究同尺），空仓货基 {engine.CASH_ANNUAL:.2%}；"
          "执行载体场外 `000051`（申 0.10% / 赎 0.50%）。",
          "- 价格是**沪深300 价格指数**（不含分红再投，约 −2.0~2.5pp/年）；"
          "指数不会停牌/涨跌停，个股层面的流动性风险不在模型内。",
          "- **样本内**：阈值全部来自视频，2005–2026 只有两笔建仓，"
          "`research/_sweep.md` 的邻域对照只用于判断稳健性，不能当样本外证据。", "",
          "> 机械规则输出，非投资建议。"]
    with io.open(OUT_MD, "w", encoding="utf-8", newline="\n") as f:
        f.write("\n".join(L) + "\n")
    print("WROTE", OUT_MD)
    print("WROTE", OUT_JSON)
    print(f"  {engine.LEG}: {cur:.0%} -> {tgt:.0%}  {action}  "
          f"(回撤 {dd:+.1%}, 关键点 {_p(key)})")


if __name__ == "__main__":
    main()
