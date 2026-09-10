# -*- coding: utf-8 -*-
"""四灯共振 · 5-10 天短线变种（规则化定义, 回测/实盘通用）

设计定位: 在 original 四灯共振(趋势/主力/持续力/热度) 基础上, 面向「5-10 个交易日
短线波段」改造。历史无主力资金/换手率明细, 因此主力维度与换手维度沿用
four_lights/resonance_4d/_backtest.py 的「量价代理」口径(文档注明), 结构上忠实于四灯方法论。

引擎口径: 当日收盘算指标/信号 -> 次日开盘成交(与 _backtest.py / ema_cross 一致, 无前视);
成本: 单边 0.03%(场内 ETF 佣金); 期末持仓按最新收盘估值。

----------------------------------------------------------------------
规则摘要
----------------------------------------------------------------------
L1 底层大趋势门(前置过滤; 用双均线 MA20/60 + 绝对动量(60日) + MA60 上行):
   strong(2) = MA20>MA60 且 close>MA60 且 MA60 近10日上行 且 绝对动量为正
   mild(1)   = (close>MA60 且 绝对动量为正) 或 (MA20>MA60 且 MA60 上行), 未达 strong
   bear(0)   = 其余: 关闸, 空仓并屏蔽全部短线信号(持仓于次日开盘清仓)

L2 三维短线条件(逐日 close 判定 -> 次日开盘):
   D1 近端动量  m:  m=0 近5日涨幅<=0 (禁开仓) | m=1 >0 | m=2 且近5日>=2% 且近3日>0
                   另开仓附加确认: 收盘 > MA10(短线站上10日线)
   D2 主力(加分) c:  量价代理 0-2: 放量收阳=2 / 收阳或放量(单边信息)=1 / 缩量收阴=0
                   —— 不是硬开关, 只进综合分 S
   D3 换手合理  h:  量能相对历史(60日均量)倍数 vr60 落在合理带, 剔除爆量出货区:
                     h=2: 0.7<=vr60<=1.4 且收阳
                     h=1: 0.5<vr60<1.6 且非放量收阴(vr60>=1.2 收阴即出货嫌疑, 禁开仓)
                     h=0: vr60>=1.6(爆量) 或 vr60<=0.5(过度萎缩) 或 放量收阴 -> 禁开仓

开仓(硬性: trend>=1 且 m>=1 且 h>=1 且 close>MA10; c 不进硬条件):
   S = trend + m + c + h (范围 3~8; trend 取 1/2)
   仓位档:
     trend==1 -> 轻仓 1/3           # 弱趋势不重仓
     trend==2 & S>=7 -> 满仓 1.0    # 信号强
     trend==2 & 5<=S<=6 -> 半仓 2/3 # 信号中
     trend==2 & S<=4   -> 轻仓 1/3  # 信号弱(主净缺席/换手一般)

出场(规则4: 不止看资金流出):
   E1 均线死叉: MA5 下穿 MA10(事件)
   E2 动量转负: 近5日涨幅 < 0, 且收盘 < MA10 确认(过滤单日噪音)
   E3 最大回撤止损: 持仓期最高收盘价回撤 >= TRAIL_STOP(8%) -> 次日开盘离场
   E4 趋势转熊: trend==0 -> 次日开盘清仓(L1 强制)
   E5 持仓超 MAX_HOLD(10) 个交易日 -> 到期离场(维持 5-10 天短线定位)

参数集中于此, 便于对照调参。
"""
import os
import sys

import numpy as np
import pandas as pd

import xalpha as xa

# ---- 参数 ----
TREND_S, TREND_L = 20, 60       # 双均线: 中期均线 MA20 / 长期 MA60
ABS_MOM = 60                    # 绝对动量: 现收盘 相对 60 交易日前收盘
MA60_SLOPE = 10                 # MA60 判上行窗(近10日)
MOM_MAIN = 5                    # 近端动量主窗(5日)
MOM_SUB = 3                     # 近端动量副窗(3日)
MOM_STRONG = 0.02               # m=2 阈值: 近5日涨幅
ENTRY_MA10 = True               # 开仓附加确认: 收盘 > MA10(短线站上10日线)
MOM_EXIT_CONFIRM = True         # E2 动量转负需再确认收盘 < MA10(过滤单日噪音)
VOL_FAST, VOL_HIST = 5, 60      # 量能代理: 5日均量(主力) / 60日均量(换手历史中枢)
HEAT_HI = 1.6                   # 换手代理合理带上限(vr60, 超过视为爆量出货区)
HEAT_DRY = 0.5                  # 换手代理合理带下限(过度萎缩)
DUMP_VR = 1.2                   # 收阴时视为放量出货的量能倍数
TRAIL_STOP = 0.08               # E3: 持仓最高收盘回撤止损 8%
MAX_HOLD = 10                   # E5: 最长持仓交易日(5-10 天短线)

FEE = 0.0003                    # 单边佣金(场内 ETF 口径, 与 four_lights/resonance_4d/_backtest.py 一致)
START = "2015-01-01"            # 抓取起点(warm-up 缓冲)
SAMPLE_FROM = "2016-09-01"      # 样本窗口起点(最长十年口径)

_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)
from pipeline import universe  # noqa: E402

# 唯一池: data/_universe.md 中「有场内对应」的行(唯一维护入口, 勿在此硬编码)
POOL = universe.inner_rows()


def sh(code):
    """6 位场内代码 -> xalpha 前缀格式(SH/SZ)。"""
    return ("SH" if code.startswith(("5", "6", "9")) else "SZ") + code


def load_bars(code, start=START):
    """标的代码 -> 日线 DataFrame(date/open/high/low/close/volume, 升序, RangeIndex)。"""
    df = xa.get_daily(sh(code), start=start)
    df = df.dropna(subset=["close", "open", "high", "low", "volume"])
    df["date"] = pd.to_datetime(df["date"])
    df = df.sort_values("date").reset_index(drop=True)
    return df


def build_signals(df):
    """按规则逐行产出「信号数组」。索引 t 的信号由 t 日收盘产生, t+1 日开盘执行。

    返回 dict(均为长度 n 的数组):
      trend: 0/1/2  底层趋势门
      buy:   bool   满足硬性开仓条件
      w:     float  buy 时的目标仓位(1.0/2/3/1/3); 非 buy 行为 0
      tech_sell: bool  技术性离场 E1/E2/E4(均线死叉 / 动量转负 / 趋势转熊)
      s/score 等仅供诊断
    """
    c = df["close"].astype(float)
    o = df["open"].astype(float)
    v = df["volume"].astype(float)
    n = len(c)
    yang = (c > o).to_numpy()

    ma5 = c.rolling(5).mean()
    ma10 = c.rolling(10).mean()
    ma20 = c.rolling(TREND_S).mean()
    ma60 = c.rolling(TREND_L).mean()

    # ---- L1 底层趋势门 ----
    dualm = (ma20 > ma60).to_numpy()
    above60 = (c > ma60).to_numpy()
    ma60_rise = (ma60 > ma60.shift(MA60_SLOPE)).to_numpy()
    abs_mom = (c > c.shift(ABS_MOM)).to_numpy()
    strong = dualm & above60 & ma60_rise & abs_mom
    mild = ((above60 & abs_mom) | (dualm & ma60_rise)) & ~strong
    trend = np.where(strong, 2, np.where(mild, 1, 0)).astype(int)
    trend = np.where(np.isnan(c.to_numpy()) | np.isnan(ma60.to_numpy()), 0, trend)

    # ---- L2-D1 近端动量 ----
    mom5 = c.pct_change(MOM_MAIN)
    mom3 = c.pct_change(MOM_SUB)
    m5 = mom5.to_numpy()
    m3 = mom3.to_numpy()
    m = np.zeros(n, dtype=int)
    m[(~np.isnan(m5)) & (m5 > 0)] = 1
    m[((~np.isnan(m5)) & (~np.isnan(m3))) & (m5 >= MOM_STRONG) & (m3 > 0)] = 2

    # ---- L2-D2 主力(量价代理, 加分项) ----
    v5 = v.rolling(VOL_FAST).mean()
    vr5 = (v / v5).to_numpy()
    cscore = np.zeros(n, dtype=int)
    cscore[(yang & (vr5 >= 1.5))] = 2
    cscore[(yang & (vr5 < 1.5)) | ((~yang) & (vr5 >= 1.5))] = 1
    cscore[np.isnan(vr5)] = 0

    # ---- L2-D3 换手代理(相对历史量能, 剔除爆量出货区) ----
    v60 = v.rolling(VOL_HIST).mean()
    vr60 = (v / v60).to_numpy()
    valid = ~np.isnan(vr60)
    overflow = valid & (vr60 >= HEAT_HI)          # 爆量(出货嫌疑)
    shrunk = valid & (vr60 <= HEAT_DRY)           # 过度萎缩
    dump = valid & (~yang) & (vr60 >= DUMP_VR)    # 放量收阴(出货嫌疑)
    h2 = valid & (vr60 >= 0.7) & (vr60 <= 1.4) & yang
    h1 = valid & (vr60 > HEAT_DRY) & (vr60 < HEAT_HI) & ~dump & ~h2
    h = np.zeros(n, dtype=int)
    h[h2] = 2
    h[h1] = 1
    h[overflow | shrunk | dump] = 0

    # ---- 综合分 S 与开仓/仓位 ----
    S = trend + m + cscore + h
    ready = (trend >= 1) & (m >= 1) & (h >= 1) & (~np.isnan(vr5)) & (~np.isnan(vr60))
    if ENTRY_MA10:
        ready = ready & (c.to_numpy() > ma10.to_numpy())
    w = np.zeros(n)
    w[ready & (trend == 1)] = 1.0 / 3.0
    w[ready & (trend == 2) & (S >= 7)] = 1.0
    w[ready & (trend == 2) & (S >= 5) & (S <= 6)] = 2.0 / 3.0
    w[ready & (trend == 2) & (S <= 4)] = 1.0 / 3.0
    buy = w > 0

    # ---- 技术性离场 E1/E2/E4 (E3/E5 由引擎按持仓状态处理) ----
    # E2 动量转负: 近5日为负; 需确认时再加收盘跌破 MA10(过滤单日噪音)
    diff = ma5 - ma10
    death = ((diff < 0) & (diff.shift(1) >= 0)).to_numpy()
    if MOM_EXIT_CONFIRM:
        mom_neg = ((m5 < 0) & (c.to_numpy() < ma10.to_numpy())) & ~np.isnan(m5)
    else:
        mom_neg = (m5 < 0) & ~np.isnan(m5)
    bear = trend == 0
    tech_sell = death | mom_neg | bear

    return dict(
        trend=trend, buy=buy, w=w, tech_sell=tech_sell,
        score=S.astype(float), m=m, c=cscore, h=h,
        death=death, mom_neg=mom_neg, bear=bear,
    )


def run_engine(open_px, close_px, sig, begin=0):
    """状态机: 当日收盘信号 -> 次日开盘成交; 持仓期附加 E3 回撤止损与 E5 持仓天数。

    入参 open_px/close_px 为 numpy 数组(长度 n, 同交易日序列, 升序)。
    begin: 样本窗口起点索引; [0, begin) 视为空仓背景期(warm-up, 不交易)。
    规则: 空仓 & buy(prev) -> 次日开盘按 w 建仓(不重复加仓);
          持仓 & (tech_sell(prev) | 回撤止损(prev 收盘) | 持有满 MAX_HOLD 日)
                -> 次日开盘全部清仓;
          其余维持。
    返回 (nav, pos_ratio, trades, holding, bars_log):
      nav: 每交易日净值(空仓=现金, 持仓=现金+市值), 自首日起;
      pos_ratio: 样本窗口内持仓时间占比;
      trades: [{entry_date, exit_date, bars, ret}], 期末未平仓按最新收盘估值;
      holding: 期末是否持仓; bars_log: 各笔持仓交易日数。
    """
    n = len(open_px)
    cash, shares = 1.0, 0.0
    nav = np.ones(n)
    pos_log = np.zeros(n, dtype=bool)
    trades, bars_log = [], []
    entry_i, entry_px, peak_close = None, None, None
    for i in range(n):
        just_sold = False
        if i >= begin + 1:
            prev = i - 1
            if shares > 0:
                # 引擎内条件(依赖持仓状态): E3 回撤止损 / E5 持仓到期
                trail = peak_close is not None and close_px[prev] < peak_close * (1 - TRAIL_STOP)
                expired = (i - entry_i) >= MAX_HOLD
                if sig["tech_sell"][prev] or trail or expired:
                    cash = cash + shares * open_px[i] * (1 - FEE)
                    shares = 0.0
                    just_sold = True
                    if entry_px is not None and entry_px > 0:
                        trades.append(dict(
                            entry_i=entry_i, exit_i=i, bars=int(i - entry_i),
                            ret=open_px[i] / entry_px * (1 - FEE) ** 2 - 1.0))
                        bars_log.append(int(i - entry_i))
                    entry_i, entry_px, peak_close = None, None, None
            # 卖出当日不再同一开盘价回补, 至少空仓 1 个交易日等新的合格信号
            if shares == 0 and not just_sold and sig["buy"][prev]:
                wgt = float(sig["w"][prev])
                invest = cash * wgt
                if invest > 0:
                    shares = invest * (1 - FEE) / open_px[i]
                    cash -= invest
                    entry_i, entry_px = i, open_px[i]
                    peak_close = close_px[i]
        if shares > 0 and peak_close is not None:
            peak_close = max(peak_close, close_px[i])
        pos_log[i] = shares > 0
        nav[i] = cash + shares * close_px[i]
    if entry_px is not None and entry_px > 0:  # 期末仍持仓: 按最新收盘估值
        trades.append(dict(
            entry_i=entry_i, exit_i=n - 1, bars=int(n - 1 - entry_i),
            ret=close_px[-1] / entry_px - 1.0))
        bars_log.append(int(n - 1 - entry_i))
    return nav, float(pos_log[begin:].mean()), trades, bool(pos_log[-1]), bars_log
