#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""V11 · 数据层（V4 的净值/RSI + V9 聚合出来的板块温度）

不重算任何因子：RSI14 走 `../_single_signal.py`（与 V4 逐字同源），
温度走 `_temp_board.py` 产出的 `data/_board_temp.csv`（V9 五因子的月度聚合）。

样本终点取「上一个完整月末」—— 当月还没走完，纳入会让最后一个月是半根，
年化与回撤都会随运行日期漂移。
"""
from __future__ import annotations

import os
import subprocess
import sys

import pandas as pd

_HERE = os.path.dirname(os.path.abspath(__file__))
_STRAT = os.path.dirname(_HERE)
for _p in (_HERE, _STRAT):
    if _p not in sys.path:
        sys.path.insert(0, _p)

import _single_signal as S  # noqa: E402  pylint: disable=wrong-import-position
import pool  # noqa: E402  pylint: disable=wrong-import-position
import rule  # noqa: E402  pylint: disable=wrong-import-position


def sample_end() -> pd.Timestamp:
    """上一个完整月末（今天 2026-11-19 -> 2026-10-31）。"""
    if rule.END:
        return pd.Timestamp(rule.END)
    today = pd.Timestamp.today().normalize()
    return (today.replace(day=1) - pd.Timedelta(days=1)).normalize()


def ensure_board(refresh: bool = False) -> str:
    """CSV 不存在（或要求 refresh）时，用子进程跑 V9 的温度聚合（避免同进程模块名冲突）。"""
    if refresh or not os.path.exists(rule.BOARD_CSV):
        cmd = [sys.executable, os.path.join(_HERE, "_temp_board.py")]
        if refresh:
            cmd.append("--refresh")
        subprocess.run(cmd, check=True)
    return rule.BOARD_CSV


def load_board(refresh: bool = False) -> pd.DataFrame:
    ensure_board(refresh)
    df = pd.read_csv(rule.BOARD_CSV, parse_dates=["date", "asof"]).set_index("date").sort_index()
    return df


def load(refresh: bool = False) -> dict:
    """-> 回测所需的一切（全部在 [START, END] 内、月末刻度）。"""
    end = sample_end()
    navs = pool.load_navs(codes=rule.CODES)
    rets = S.load_rets(navs).loc[rule.START:end]
    raw = S.load_signal("rsi", navs).reindex(rets.index).loc[rule.START:end]
    q = raw / 100.0
    ok = S.available(q, rets)
    bq = S.board_q(q, ok)                    # 板块 RSI 均值（0–1）
    top = S.top_w(raw, ok, rule.TOP_K)       # V4 的横截面：RSI 最低 2 只等权

    board = load_board(refresh=refresh).reindex(rets.index)
    temp6 = board["board6_med"]
    temp4 = board["board4_med"]
    return {"end": end, "rets": rets, "raw": raw, "q": q, "ok": ok, "bq": bq,
            "top": top, "base_w": S.equal_w(ok), "temp6": temp6, "temp4": temp4,
            "board": board, "navs": navs}


if __name__ == "__main__":
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    d = load(refresh="--refresh" in sys.argv)
    print(f"[v11:data] 样本 {d['rets'].index[0]:%Y-%m}~{d['rets'].index[-1]:%Y-%m} "
          f"({len(d['rets'])} 月) · 池 {len(rule.CODES)} 只")
    print(f"[v11:data] 温度可用 {d['temp6'].notna().sum()} 月 · 最新 "
          f"{d['temp6'].dropna().iloc[-1]:.1f}（{d['temp6'].dropna().index[-1]:%Y-%m}）")
    print(f"[v11:data] 板块 RSI 均值 {d['bq'].iloc[-1] * 100:.1f}")
    print(f"[v11:data] 主板口径 = {rule.BOARD_SET}")
