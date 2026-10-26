#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""估值温度 v2 · 每日/每月读数卡

与图片版日报的差别（都写在报告里，不藏）
----------------------------------------
* **不看绝对温度数字，看「温度的历史分位」**：四维分位合成的温度经验区间只有 10~66，
  绝对刻度不可移植（图片给的 0/100 是名义刻度）。
* 档位只是标签；**唯一会改变动作的是「板块温度分位 ≥80% 减半 / ≥90% 清仓」**。
* 温度不用于选品（横截面 IC≈0，见 `backtest/_v2_report.md`）。

产物
----
    daily/_v2_read.md / daily/_v2_read.json

用法::

    python strategies/dividend/v2_temp/readout.py [--refresh]
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from datetime import datetime

import numpy as np
import pandas as pd

_DIR = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, _DIR)
sys.path.insert(0, os.path.dirname(_DIR))

import factors  # noqa: E402  pylint: disable=wrong-import-position
import rule  # noqa: E402  pylint: disable=wrong-import-position

CHAIN = "利差反号 → 四维等权 → 温度 → 温度自身历史分位 → 减仓"


def band_of(temp: float) -> tuple[str, str]:
    for lo, name, advice in rule.BANDS:
        if temp >= lo:
            return name, advice
    return rule.BANDS[-1][1], rule.BANDS[-1][2]


def collect(refresh=False) -> dict:
    panels = factors.build(weights=rule.W_EQ, signs=rule.SIGN, refresh=refresh)
    temp = pd.DataFrame({c: m["temp"] for c, m in panels.items()})
    board = temp.mean(axis=1)
    board_q = factors.pct_rank(board, min_n=rule.Q_MIN_N, max_n=10 ** 6)
    last_q = float(board_q.iloc[-1]) if board_q.notna().any() else float("nan")

    rows = []
    for code, m in panels.items():
        s = m["temp"].dropna()
        r = m.iloc[-1]
        q = float((s < r["temp"]).mean()) if len(s) else float("nan")
        name, advice = band_of(float(r["temp"]))
        rows.append({
            "code": code, "name": rule.INDICES[code],
            "temp": round(float(r["temp"]), 1), "temp_pct": round(q, 3),
            "band": name, "advice": advice,
            "spread": round(float(r["spread"]), 2), "pe": round(float(r["pe"]), 2),
            "ma_dev": round(float(r["ma_dev"]) * 100, 1),
            "p_factors": {f: round(float(r[f + "_p"]), 3) for f in factors.FACTORS},
            "as_of": str(r.name.date()), "n_months": int(len(s)),
        })

    close_asof = max(p.attrs.get("as_of", p.index[-1]) for p in panels.values())
    month_end = temp.index[-1]

    # 时间维度：板块温度分位决定减仓
    if np.isnan(last_q):
        action, mult = "样本不足，按满仓", 1.0
    elif last_q >= rule.OUT_Q:
        action, mult = f"板块温度分位 {last_q:.0%} ≥ {rule.OUT_Q:.0%} → 清仓/降至底仓", 0.0
    elif last_q >= rule.CUT_Q:
        action, mult = f"板块温度分位 {last_q:.0%} ≥ {rule.CUT_Q:.0%} → 仓位减半", 0.5
    else:
        action, mult = f"板块温度分位 {last_q:.0%} < {rule.CUT_Q:.0%} → 不动作", 1.0

    return {
        "generated": datetime.now().strftime("%Y-%m-%d %H:%M"),
        "as_of": str(month_end.date()),
        "close_as_of": str(close_asof.date()),
        "month_complete": bool(close_asof >= month_end - pd.Timedelta(days=2)),
        "board_temp": round(float(board.iloc[-1]), 1),
        "board_pct": None if np.isnan(last_q) else round(last_q, 3),
        "action": action, "mult": mult,
        "rows": rows,
        "pb_available": rule.PB_AVAILABLE,
        "weights": rule.W_EQ,
        "signs": rule.SIGN,
    }


def render(d: dict) -> str:
    pct_txt = "—" if d["board_pct"] is None else f"{d['board_pct'] * 100:.0f}%"
    asof = d["close_as_of"]
    if not d.get("month_complete", True):
        asof += f"（{d['as_of'][:7]} 月未走完，用最新收盘）"
    L = ["# 红利估值温度 v2 · 读数卡", "",
         f"> 生成 {d['generated']} · 数据截至 **{asof}** · "
         f"链路：{CHAIN}（PB 无历史，权重在四维上归一）", ""]

    L += ["## 一、板块结论", "",
          f"- 板块温度（4 只均值）**{d['board_temp']:.1f}**，自身历史分位 **{pct_txt}**。",
          f"- **动作：{d['action']}**（仓位倍率 {d['mult']:.1f}）。",
          "- 温度只用来决定「整体要不要降暴露」，**不用来选品**（横截面 IC≈0）。", ""]

    L += ["## 二、逐指数温度", "",
          "| 指数 | 温度 | 温度历史分位 | 档位 | 利差分位 | PE分位 | 乖离分位 | 位置分位 |",
          "|---|---:|---:|---|---:|---:|---:|---:|"]
    for r in d["rows"]:
        pf = r["p_factors"]
        L.append(f"| {r['name']}({r['code']}) | {r['temp']:.1f} | {r['temp_pct'] * 100:.0f}% | "
                 f"{r['band']} | {pf['spread']:.2f} | {pf['pe']:.2f} | "
                 f"{pf['ma_dev']:.2f} | {pf['pos']:.2f} |")
    L += ["", "> 分位方向已统一为「越高越热」：利差列已反号（利差宽 = 便宜 = 低分位）。"
              "四列分位若明显不同向，说明是某一维在主导温度 —— 看单维比看合成分更有信息量。", ""]

    L += ["## 三、原始值", "",
          "| 指数 | 利差(pp) | PE | 年线乖离 | 样本月数 |", "|---|---:|---:|---:|---:|"]
    for r in d["rows"]:
        L.append(f"| {r['name']} | {r['spread']:+.2f} | {r['pe']:.2f} | "
                 f"{r['ma_dev']:+.1f}% | {r['n_months']} |")
    L += ["", f"> 缺口：PB {'可得' if d['pb_available'] else '**不可得**（中证官网只有 PE，东财只有当前快照）'}；"
              "温度经验区间约 10–66，绝对刻度不代表 0–100 分。", ""]
    return "\n".join(L)


def main(argv=None):
    ap = argparse.ArgumentParser(description="估值温度 v2 读数卡")
    ap.add_argument("--refresh", action="store_true")
    args = ap.parse_args(argv)

    d = collect(refresh=args.refresh)
    os.makedirs(rule.DAILY_DIR, exist_ok=True)
    with open(rule.OUT_READ_MD, "w", encoding="utf-8") as fh:
        fh.write(render(d))
    with open(rule.OUT_READ_JSON, "w", encoding="utf-8") as fh:
        json.dump(d, fh, ensure_ascii=False, indent=1)

    pct_txt = "n/a" if d["board_pct"] is None else f"{d['board_pct'] * 100:.0f}%"
    print(f"[v2] 温度读数 {d['as_of']} · 板块 {d['board_temp']:.1f} (分位 {pct_txt})")
    for r in d["rows"]:
        print(f"[v2] {r['code']:7s} {r['name']:8s} 温度 {r['temp']:5.1f} "
              f"分位 {r['temp_pct'] * 100:3.0f}%  {r['band']}")
    print(f"[v2] 动作: {d['action']}")
    print(f"[v2] 产物 -> {os.path.relpath(rule.OUT_READ_MD, rule.ROOT)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
