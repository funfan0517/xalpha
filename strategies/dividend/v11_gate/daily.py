#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""V11 · 每日读数（温度闸门状态）

**不出仓位建议**：`rule.GATE_ENABLED = False`，仓位仍由 V4 决定。本脚本只做三件事：

1. 现在板块温度多少、落在 V9 的哪个档；
2. 如果闸门开着，它今天会不会动手（冷闸放量 / 热闸压仓），动作是什么；
3. 并排给出 V4 的实际档位，避免「读数」被误当「指令」。

当月未走完时，温度行标 `asof`（最新交易日）；已生效的信号只用上一个完整月末。

用法::

    python strategies/dividend/v11_gate/daily.py [--refresh]
"""
from __future__ import annotations

import json
import os
import sys

import pandas as pd

_HERE = os.path.dirname(os.path.abspath(__file__))
if _HERE not in sys.path:
    sys.path.insert(0, _HERE)

import data as D  # noqa: E402  pylint: disable=wrong-import-position
import engine as E  # noqa: E402  pylint: disable=wrong-import-position
import rule  # noqa: E402  pylint: disable=wrong-import-position
import _single_signal as S  # noqa: E402  pylint: disable=wrong-import-position


def band(temp: float) -> tuple:
    for lo, name, act in ((85, "危险", "大幅减仓"), (75, "过热", "分批减仓"),
                          (65, "偏热", "可开始部分止盈"), (55, "中性偏热", "不再新增，持有观望"),
                          (40, "中性", "持有 / 正常定投"), (20, "偏冷", "便宜区域，可分批加仓"),
                          (0, "冰点", "便宜区域，可分批加仓")):
        if temp >= lo:
            return name, act
    return "冰点", "便宜区域，可分批加仓"


def main(refresh: bool = False) -> int:
    d = D.load(refresh=refresh)
    board = D.load_board(refresh=refresh)          # 全表（含当月未走完的一行）
    last = board.iloc[-1]
    prev_done = board.iloc[-2] if last["asof"] < last.name else last   # 上一个完整月

    # 已生效信号（上一个完整月末）
    bq = d["bq"]
    m_rsi = E.mult_rsi(bq)
    temp_eff = d["temp6"].iloc[-1]
    cold_hit = bool(temp_eff <= rule.COLD and 0.01 < m_rsi.iloc[-1] < 0.99)
    hot_hit = bool(temp_eff >= rule.HOT and m_rsi.iloc[-1] > rule.CAP_HOT)

    # 实时读数（当月未走完）
    navs = d["navs"]
    rets_all = S.load_rets(navs)
    raw_all = S.load_signal("rsi", navs).reindex(rets_all.index)
    ok_all = S.available(raw_all / 100.0, rets_all)
    bq_all = S.board_q(raw_all / 100.0, ok_all)
    rsi_now = float(bq_all.dropna().iloc[-1] * 100)
    picks = raw_all.iloc[-1].dropna().nsmallest(rule.TOP_K).index.tolist()
    pos_now = float(m_rsi.iloc[-1])

    t_now = float(last["board6_med"])
    nm, act = band(t_now)
    rank = float((board["board6_med"] <= t_now).mean())

    L = [f"# {rule.TITLE} · 今日读数", "",
         "> **闸门未采纳**（`rule.GATE_ENABLED = False`）：下面的「闸门动作」是**假设它开着会怎样**，"
         "不是今天的操作。今天的仓位仍按 V4（`v4_rsi/daily.py`）。",
         f"> 生成 {pd.Timestamp.today():%Y-%m-%d %H:%M} · **交易池 {len(rule.CODES)} 只 = 温度池**"
         f"（既有温度又有 RSI："
         + "、".join(rule.NAME_SHORT.get(c, c) for c in rule.CODES)
         + "）", ""]

    L += ["## 一、读数", "",
          "| 项 | 值 | 说明 |", "|---|---:|---|",
          f"| 板块温度（6 只中位） | **{t_now:.1f}** | asof {last['asof']:%Y-%m-%d}"
          f"（{'当月未走完' if last['asof'] < last.name else '完整月'}）· 历史分位 {rank:.0%} |",
          f"| V9 档位 | {nm} | {act}（**额度参考，不是择时**） |",
          f"| 已生效温度（{prev_done.name:%Y-%m} 末） | {float(prev_done['board6_med']):.1f} | "
          f"用于下月决策的那一个 |",
          f"| 板块 RSI 均值 | {rsi_now:.1f} | 实时（累计净值，V4 口径） |",
          f"| V4 仓位倍率 | **{pos_now:.0%}** | "
          f"{'满仓' if pos_now >= 0.99 else ('减半' if pos_now > 0.01 else '清仓')}"
          f"（RSI≥{rule.Q_HALF * 100:.0f} 减半 / ≥{rule.Q_OUT * 100:.0f} 清仓） |",
          f"| 横截面（V4 top{rule.TOP_K}） | {'、'.join(rule.NAME_SHORT.get(c, c) for c in picks)} | "
          "RSI 最低 2 只等权 |", ""]

    L += ["## 二、闸门（若开启）", "",
          "| 闸门 | 条件（用已生效温度） | 已生效月 | 会做什么 |", "|---|---|---|---|",
          f"| 冷闸 | 温度 ≤ {rule.COLD:.0f} 且 V4 处于减半档 | "
          f"{'触发' if cold_hit else '不触发'} | 减半 → 满仓 |",
          f"| 热闸 | 温度 ≥ {rule.HOT:.0f} 且 V4 处于满仓 | "
          f"{'触发' if hot_hit else '不触发'} | 满仓 → 减半 |",
          "", f"- 已生效读数：温度 {temp_eff:.1f}、V4 倍率 {pos_now:.0%} → "
              f"**闸门{'会' if (cold_hit or hot_hit) else '不会'}改档**。", ""]

    L += ["## 三、为什么不采纳", "",
          "1. 冷闸全样本能把 Calmar 抬高 0.1 上下，但**剔除贡献最大 3 个月后一律低于同池 V4**"
          "（三个池都试过：5 旧 / 6 同源 / 10 当前 V4）。",
          "2. 减半档内温度对下月收益的相关系数为负但 **|t| < 2** —— 闸门要作用的那一段，温度没有判别力。",
          "3. 热闸在每个阈值上都更差（或接近中性），却要以牺牲满仓月的收益为代价。",
          "", "> 具体数字见 `backtest/_v11_report.md`（§一·补 三池对照 / §四 三把刀 / §五 置换）；"
          "结论与边界见 `RULES.md`。", ""]

    os.makedirs(rule.DAILY_DIR, exist_ok=True)
    with open(rule.OUT_MD, "w", encoding="utf-8") as fh:
        fh.write("\n".join(L))

    payload = {
        "signal": rule.SIGNAL,
        "asof": str(last["asof"].date()),
        "month": str(last.name.date()),
        "temp6": round(t_now, 2),
        "temp_effective": round(float(temp_eff), 2),
        "temp_rank": round(rank, 4),
        "band": nm,
        "band_action": act,
        "rsi_board": round(rsi_now, 2),
        "v4_mult": round(pos_now, 3),
        "v4_picks": picks,
        "gate_enabled": rule.GATE_ENABLED,
        "gate": {"cold": rule.COLD, "hot": rule.HOT,
                 "cold_hit": cold_hit, "hot_hit": hot_hit},
        "note": "闸门未采纳；仓位仍按 V4（v4_rsi/daily.py）",
    }
    with open(rule.OUT_JSON, "w", encoding="utf-8") as fh:
        json.dump(payload, fh, ensure_ascii=False, indent=2)

    print(f"[v11] 温度 {t_now:.1f}（{nm}，分位 {rank:.0%}，asof {last['asof']:%Y-%m-%d}）"
          f" · 板块 RSI {rsi_now:.1f} · V4 倍率 {pos_now:.0%}")
    print(f"[v11] 闸门 {'开启' if rule.GATE_ENABLED else '未采纳'}：冷闸{'触发' if cold_hit else '不触发'} / "
          f"热闸{'触发' if hot_hit else '不触发'}")
    print(f"[v11] -> {os.path.relpath(rule.OUT_MD, os.path.dirname(_HERE))}")
    return 0


if __name__ == "__main__":
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    sys.exit(main(refresh="--refresh" in sys.argv))
