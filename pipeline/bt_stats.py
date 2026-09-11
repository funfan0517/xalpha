# -*- coding: utf-8 -*-
"""pipeline 回测报告统一口径：单笔交易统计（胜率 / 盈亏比 / 利润因子）。

各策略回测引擎将各自记录的单笔交易统一归一为:
    {"code": "512800", "entry_date": "2024-01-05", "exit_date": "2024-02-09",
     "bars": 21, "ret": 0.0123}
`ret` = 该持仓期收益（是否扣交易成本、按收盘/开盘价，由各引擎在报告注明）。
引擎用 trade_stats() 得到同一套指标，用 section_lines() 渲染与 momentum/backtest.py
一致的默认报告节 —— 任何新策略的 backtest 报告都默认包含此节。
"""
import os
import sys

import numpy as np

# 允许被仓库内任意子目录的脚本直接 import（sys.path[0] = 脚本自身目录）
_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)


def trade_stats(trades):
    """trades: list[dict 含 ret] -> 统一单笔统计 dict。
    口径: 胜率=盈利笔数/总笔数(平局按负计)；
         盈亏比(payoff)=平均盈利/|平均亏损|；
         利润因子=总盈利/|总亏损|。
    空列表返回全 0 占位(n=0)。"""
    if not trades:
        return dict(n=0, win_rate=0.0, avg_win=0.0, avg_loss=0.0, payoff=0.0,
                    profit_factor=0.0, best=0.0, worst=0.0)
    rets = np.array([t["ret"] for t in trades])
    wins, losses = rets[rets > 0], rets[rets <= 0]
    gw, gl = wins.sum(), -losses.sum()
    aw = wins.mean() if len(wins) else 0.0
    al = losses.mean() if len(losses) else 0.0
    return dict(n=int(len(rets)), win_rate=float(len(wins) / len(rets)),
                avg_win=float(aw), avg_loss=float(al),
                payoff=float(aw / abs(al)) if al < 0 else float("inf"),
                profit_factor=float(gw / gl) if gl > 0 else float("inf"),
                best=float(rets.max()), worst=float(rets.min()))


def pct(x, signed=True, dp=2):
    return f"{x * 100:+.{dp}f}%" if signed else f"{x * 100:.{dp}f}%"


def _cells(ts):
    inf = lambda x: "∞" if x == float("inf") else f"{x:.2f}"
    return (f"{ts['n']} | {pct(ts['win_rate'])} | {pct(ts['avg_win'])} | {pct(ts['avg_loss'])}"
            f" | {inf(ts['payoff'])} | {inf(ts['profit_factor'])} | {pct(ts['best'])} | {pct(ts['worst'])}")


def section_lines(ts, title="单笔交易统计（全期）", note=None):
    """单口径默认报告节 -> md 行列表（调用方直接 L += section_lines(ts)）。"""
    if note is None:
        note = ("单笔按持仓期收益计；胜率=盈利笔数/总笔数；盈亏比=平均盈利/|平均亏损|；"
                "利润因子=总盈利/|总亏损|。")
    return [
        "",
        f"## {title}",
        "",
        "| 交易笔数 | 胜率 | 平均盈利 | 平均亏损 | 盈亏比 | 利润因子 | 最佳单笔 | 最差单笔 |",
        "|---|---|---|---|---|---|---|---|",
        f"| {_cells(ts)} |",
        "",
        f"> {note}",
    ]


def section_rows(rows, title="单笔/周期统计对照（全期）", note=None):
    """多口径对照节（如 策略 vs 基准 同表），rows: list[(口径标签, ts)]。"""
    if note is None:
        note = ("胜率=盈利笔数/总笔数；盈亏比=平均盈利/|平均亏损|；利润因子=总盈利/|总亏损|；"
                "口径差异见各行说明。")
    L = [
        "",
        f"## {title}",
        "",
        "| 口径 | 笔数 | 胜率 | 平均盈利 | 平均亏损 | 盈亏比 | 利润因子 | 最佳 | 最差 |",
        "|---|---|---|---|---|---|---|---|---|",
    ]
    for label, ts in rows:
        L.append(f"| {label} | {_cells(ts)} |")
    L.append("")
    L.append(f"> {note}")
    return L
