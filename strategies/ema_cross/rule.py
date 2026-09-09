# -*- coding: utf-8 -*-
"""双均线趋势策略 · 规则化定义（唯一权威源, 2026-09-08 落地）

方法论文档: 「双均线趋势策略 · 完整落地规则手册（实盘/回测通用版）」
核心逻辑: 短 EMA12 上穿长 EMA26(金叉) -> 次日开盘买入; 下穿(死叉) -> 次日开盘清仓。
实盘增强过滤(手册第五章, 默认全开):
  1) 趋势过滤: 金叉日收盘价须站上 EMA26 上方才认可, 过滤弱势反弹假信号;
  2) 阈值过滤: 交叉当日两线差距 < 0.3% 视为毛刺, 不触发交易;
  3) 锁仓规则: 两次实际交易间隔不足 5 个交易日时, 忽略第二次信号。

引擎口径: 当日收盘计算指标并判信号, 次日开盘执行(回测/实盘一致, 无前视);
数据列需含 date/open/close; 标的池 = data/_universe.md 唯一池场内映射(禁止硬编码)。
"""
import os
import sys

import numpy as np
import pandas as pd

import xalpha as xa

# ---- 核心参数(手册第二章: 经典 EMA12/26, 禁止频繁修改) ----
EMA_FAST = 12
EMA_SLOW = 26
# 备选稳健参数(长线配置, 手册提及): MA20/MA60, 供敏感性对照, 非默认
ALT_FAST, ALT_SLOW = 20, 60

# ---- 实盘增强过滤(手册第五章, 默认全开) ----
TREND_FILTER = True      # 1) 趋势过滤: 金叉须 close > EMA26
THRESHOLD = 0.003        # 2) 交叉阈值: 两线差距 >= 0.3% 才算有效交叉(毛刺过滤)
LOCK_BARS = 5            # 3) 锁仓: 两次交易信号间隔 < 5 交易日忽略后信号

FEE = 0.0003             # 单边交易成本(场内 ETF 佣金口径, 与 four_lights 一致)
START = "2015-01-01"     # 抓取起点(给 EMA warm-up 缓冲)
SAMPLE_FROM = "2016-09-01"  # 样本窗口起点(最长十年口径)

_INV = 252

_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)
from pipeline import universe  # noqa: E402

# 唯一池: data/_universe.md 中「有场内对应」的行(唯一维护入口)
POOL = universe.inner_rows()


def sh(code):
    """6 位场内代码 -> xalpha 前缀格式(SH/SZ)。"""
    return ("SH" if code.startswith(("5", "6", "9")) else "SZ") + code


def load_bars(code, start=START):
    """标的代码 -> 日线 DataFrame(date/open/close, 升序, RangeIndex)。

    与 four_lights 回测同一数据管线(xa.get_daily), 供回测与每日信号共用。
    """
    df = xa.get_daily(sh(code), start=start)
    df = df.dropna(subset=["close", "open"])
    df["date"] = pd.to_datetime(df["date"])
    df = df.sort_values("date").reset_index(drop=True)
    return df[["date", "open", "close"]].reset_index(drop=True)


def ema(s, n):
    """指数移动平均(与 xalpha/indicator.ema 同口径: ewm span, adjust=False)。"""
    return s.ewm(span=n, adjust=False).mean()


def build_signals(close):
    """按规则逐行判定「有效信号」(收盘后判定, 次日开盘执行)。

    返回 numpy bool 数组 buy/sell(长度=len(close)), 索引 t 表示第 t 日收盘
    产出的信号(实际交易发生在 t+1 开盘)。过滤:
      - 交叉: fast 与 slow 的位置反转(穿过)才算;
      - 阈值: 交叉当日 |fast-slow|/slow >= THRESHOLD(0.3%), 否则视为毛刺忽略;
      - 趋势: 金叉须 close > slow(TREND_FILTER 关闭时不做)。
    锁仓(两次交易间隔)属交易执行层, 由引擎在状态机中套用(见 run_engine)。
    """
    s = close.astype(float)
    f = ema(s, EMA_FAST)
    g = ema(s, EMA_SLOW)
    n = len(s)
    buy = np.zeros(n, dtype=bool)
    sell = np.zeros(n, dtype=bool)
    for t in range(1, n):
        if np.isnan(f.iloc[t]) or np.isnan(g.iloc[t]) or np.isnan(f.iloc[t - 1]) or np.isnan(g.iloc[t - 1]):
            continue
        d0, d1 = f.iloc[t - 1] - g.iloc[t - 1], f.iloc[t] - g.iloc[t]
        if d0 <= 0 < d1:  # 上穿(金叉)
            depth = (f.iloc[t] - g.iloc[t]) / g.iloc[t]
            if depth >= THRESHOLD and (not TREND_FILTER or s.iloc[t] > g.iloc[t]):
                buy[t] = True
        elif d0 >= 0 > d1:  # 下穿(死叉)
            depth = (g.iloc[t] - f.iloc[t]) / g.iloc[t]
            if depth >= THRESHOLD:
                sell[t] = True
    return dict(fast=f.to_numpy(), slow=g.to_numpy(), buy=buy, sell=sell)


def run_engine(open_px, close_px, buy, sell, begin=0):
    """纯状态机: 信号(收盘)在次日开盘成交; 持仓期间不手动止盈止损; 锁仓过滤。

    入参均为 numpy 数组(长度 n, 同交易日序列)。
    begin: 样本窗口起点索引; [0, begin) 视为空仓背景期(回测从样本起点起步,
           EMA warm-up 只用来让信号自 begin 起即为"真实规则信号", 无前视)。
    规则: 金叉且空仓 -> 次日开盘全仓买入; 死叉且持仓 -> 次日开盘全部清仓;
          无信号 -> 维持仓位; 信号距上次实际交易 < LOCK_BARS -> 忽略(锁仓)。
    返回 (nav, pos_ratio, trades, holding):
      nav = 每交易日净值(空仓=现金, 持仓=市值), 自首日起;
      pos_ratio = 样本窗口内持仓时间占比; holding = 期末是否持仓(供每日信号);
      trades = [{entry_i, exit_i, ret}] 每笔持仓周期; ret 为扣除双边 FEE 的
               净收益, 期末未平仓按最新收盘估值(仅扣买入费)。
    """
    n = len(open_px)
    cash, shares = 1.0, 0.0
    nav = np.ones(n)
    pos_log = np.zeros(n, dtype=bool)
    trades = []
    entry_i, entry_px = None, None
    last_sig = begin - LOCK_BARS - 1  # 样本起点前无交易, 首次信号不受锁仓限制
    for i in range(n):
        if i >= begin + 1:
            prev = i - 1  # 前一交易日收盘信号, 于 i 日开盘执行
            locked = (prev - last_sig) < LOCK_BARS
            if shares > 0 and sell[prev] and not locked:
                cash = shares * open_px[i] * (1 - FEE)
                shares = 0.0
                if entry_px is not None and entry_px > 0:
                    trades.append(dict(entry_i=entry_i, exit_i=i,
                                       ret=open_px[i] / entry_px * (1 - FEE) ** 2 - 1.0))
                entry_px = None
                last_sig = prev
            elif shares == 0 and buy[prev] and not locked:
                shares = cash * (1 - FEE) / open_px[i]
                cash = 0.0
                entry_i, entry_px = i, open_px[i]
                last_sig = prev
        pos_log[i] = shares > 0
        nav[i] = cash + shares * close_px[i]
    if entry_px is not None and entry_px > 0:  # 期末仍持仓: 按最新收盘估值
        trades.append(dict(entry_i=entry_i, exit_i=n - 1,
                           ret=close_px[-1] / entry_px - 1.0))
    return nav, float(pos_log[begin:].mean()), trades, bool(pos_log[-1])
