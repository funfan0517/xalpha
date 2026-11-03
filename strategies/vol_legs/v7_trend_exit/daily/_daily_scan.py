# -*- coding: utf-8 -*-
"""波动资产 · V7 变种：中期趋势 + 急跌退出 —— 每日 / 每月操作提示。

离线（只读本地缓存 csi_H00300.csv），输出：
- 当前是否**在场**（状态）、已持仓天数、各信号读数；
- 触发退出条件还剩多少空间（dev60 距 EXIT_DEV、vol20 距 EXIT_VOL）；
- 回场条件是否满足、冷却剩余天数；
- 最近若干次决策（信号日→成交日→动作→原因）。

运行：
    python strategies/vol_legs/v7_trend_exit/daily/_daily_scan.py [--capital 2000000] [--b 10000]
        [--days 12]

`--b` 仅用于提示「若此刻在场/回场，理论动用金额」（实盘按 0/1 满仓，金额不由本策略决定，
这里给一个参考规模）。
"""
import argparse
import json
import os
import sys

import numpy as np
import pandas as pd

try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:  # noqa: BLE001
    pass

_HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(_HERE))

import trend_exit as te  # noqa: E402

OUT_MD = os.path.join(_HERE, "_v7_trend_exit_daily.md")
OUT_JSON = os.path.join(_HERE, "_v7_trend_exit_daily.json")


def pct(x, nd=1):
    return "—" if x is None or (isinstance(x, float) and x != x) else f"{x * 100:+.{nd}f}%"


def run(capital, b, days):
    px = te.load_px("tr")
    r = te.latest(px)
    sm, trades = r["sm"], r["trades"]
    p = te.params()

    # 回场条件实时评估（用最新一行指标）
    last = sm.iloc[-1]
    cooldown_left = 0
    if trades:
        last_exit = max((t["exec_date"] for t in trades if t["side"] == "SELL"), default=None)
        if last_exit is not None:
            pos = {d: i for i, d in enumerate(sm.index)}
            cooldown_left = int(p["COOLDOWN"]) - (len(sm) - 1 - pos[last_exit])

    # 触发退出还剩多少空间
    dev_gap = float(last["dev60"] - p["EXIT_DEV"])      # >0 表示离触发还远（dev60 更负才触发）
    vol_gap = float(p["EXIT_VOL"] - last["vol20"])      # >0 表示离触发还远（vol20 更大约触发）
    rec_ok = (bool(last["trend_enter"]) and (last["dev60"] >= p["RECOVER_DEV"])
              and (last["vol20"] <= p["RECOVER_VOL"]) and cooldown_left <= 0)

    # 最近若干次决策
    dec = sm[sm["decide"] != ""].tail(days)
    rows = []
    for idx, row in dec.iterrows():
        ex = row["exit_reason"] if row["decide"] == "sell" else "—"
        rows.append(dict(date=idx.date().isoformat(), decide=row["decide"],
                         close=float(row["close"]), dev60=float(row["dev60"]),
                         vol20=float(row["vol20"]), ma120=float(row["ma120"]),
                         reason=ex))

    payload = dict(
        date=r["date"].date().isoformat(), ready=r["ready"], state=int(r["state"]),
        days_in_state=int(r["days_in_state"]), close=float(r["close"]), ma120=float(r["ma120"]),
        ma250=float(r["ma250"]), dev60=float(r["dev60"]), vol20=float(r["vol20"]),
        slope120=float(r["slope120"]), trend_enter=bool(r["trend_enter"]),
        trend_hold=bool(r["trend_hold"]), exit_dev_line=float(r["exit_dev_line"]),
        exit_vol_line=float(r["exit_vol_line"]), recover_dev=p["RECOVER_DEV"],
        recover_vol=p["RECOVER_VOL"], cooldown_left=max(cooldown_left, 0),
        reenter_ready=rec_ok, dev_gap=dev_gap, vol_gap=vol_gap,
        recent=rows, source=te.SOURCE.get("tr"),
        n_trades=len(trades), mode=p["TREND_MODE"], band=p["BAND"],
        exit_dev=p["EXIT_DEV"], exit_vol=p["EXIT_VOL"], cooldown=p["COOLDOWN"],
        trend_exit=p["TREND_EXIT"], hint_capital=capital, hint_b=b,
    )

    # ---------------- markdown
    state_txt = "**在场（持仓）**" if r["state"] else "**空仓（持有现金）**"
    L = [
        f"# 波动资产 · V7 变种：中期趋势 + 急跌退出 · 每日提示",
        f"> 标的：沪深300 全收益 H00300 · 数据 {r['source']}",
        f"> 评价区间自 {p['START']} 起 · 预热 {p['WARMUP']} 日 · 趋势口径 `{p['TREND_MODE']}`（BAND {p['BAND']:.1%}）",
        "",
        f"## 当前状态（{r['date']:%Y-%m-%d}）",
        "",
        f"- **仓位**：{state_txt}（已连续 {r['days_in_state']} 个交易日）",
        f"- 收盘 {r['close']:.0f} · MA120 {r['ma120']:.0f} · MA250 {r['ma250']:.0f}",
        f"- `dev60 = {pct(r['dev60'])}`（退出阈值 ≤ {pct(p['EXIT_DEV'])}；距触发还差 {pct(dev_gap)}）",
        f"- `vol20 = {pct(r['vol20'], 1)}`（退出阈值 ≥ {pct(p['EXIT_VOL'], 0)}；距触发还差 {pct(vol_gap, 1)}）",
        f"- `MA120 斜率(20日) = {pct(r['slope120'])}`",
        f"- 趋势：进场条件 {'满足' if r['trend_enter'] else '不满足'} / 基础仓位 {'成立' if r['trend_hold'] else '破位'}",
        "",
        "## 回场条件（当前空仓时才有意义）",
        "",
        f"- 冷却剩余 **{max(cooldown_left, 0)}** / {p['COOLDOWN']} 交易日",
        f"- 回场四条件（`冷却满 + 趋势恢复 + dev60≥{pct(p['RECOVER_DEV'])} + vol20≤{pct(p['RECOVER_VOL'],0)}`）"
        f"：{'**全部满足 → 可回场**' if rec_ok else '尚未满足'}",
        f"- 参考动用规模（满仓口径）：约 {b:,.0f} 元（实际金额由你的总配置决定，本策略只给 0/1）",
        "",
        "## 最近决策（信号日 → 动作 → 原因）",
        "",
        "| 日期 | 动作 | 收盘 | dev60 | vol20 | MA120 | 退出原因 |",
        "|---|---|---:|---:|---:|---:|---|",
    ]
    label = {"buy": "回场/买入", "sell": "清仓/卖出", "": "—"}
    for x in rows:
        L.append(f"| {x['date']} | {label.get(x['decide'], x['decide'])} | {x['close']:.0f} | "
                 f"{pct(x['dev60'])} | {pct(x['vol20'], 1)} | {x['ma120']:.0f} | {x['reason']} |")
    L += [
        "",
        f"> 全样本共 {len(trades)} 笔成交。退出原因分布见回测报告 §2 语义矩阵 / §3 交易统计。",
        f"> **本策略赚的是「少跌」，不是「多涨」**：年化通常不超过买入持有，唯一价值是 Calmar "
        f"（年化/回撤）更高。若看到「在场但跑输基准」，是正常现象，不是 bug。",
        "",
        "> 模拟结果，非投资建议。",
    ]
    with open(OUT_MD, "w", encoding="utf-8") as f:
        f.write("\n".join(L))
    with open(OUT_JSON, "w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False, indent=2)

    print(f"[{r['date']:%Y-%m-%d}] 状态 {'在场' if r['state'] else '空仓'}（{r['days_in_state']} 日）"
          f"· dev60 {pct(r['dev60'])} · vol20 {pct(r['vol20'],1)} · 回场{'可' if rec_ok else '否'}"
          f" · 报告 {OUT_MD}")
    return payload


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--capital", type=float, default=2_000_000,
                    help="账户总规模（仅用于提示，策略本身只看 0/1）")
    ap.add_argument("--b", type=float, default=None,
                    help="单腿参考仓位（默认 = capital/120/2，与 v6 同源，便于两策略联动）")
    ap.add_argument("--days", type=int, default=12, help="最近决策条数")
    args = ap.parse_args()
    b = args.b if args.b else args.capital / 120.0 / 2.0
    run(args.capital, b, args.days)


if __name__ == "__main__":
    main()
