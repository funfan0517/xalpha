# -*- coding: utf-8 -*-
"""右侧买入策略 · 规则化定义（**唯一参数权威源**）。

问题定义
--------
「抄底抄到半山腰」的成因是判据用错：用**价格位置**（跌了 30% / 估值分位低 / 触布林下轨）
判断便宜，而趋势中「便宜」可以持续很久。右侧买入把判据换成**状态转移** ——
等下跌结构自己先被证伪，再买。

五票确认（全部为「当日收盘可算、次日可执行」的因果量）
----------------------------------------------------
  V1 结构止跌：近 20 日最低收盘 > 再往前 60 日最低收盘（低点抬高，不再创新低）
  V2 结构翻越：收盘 > 前 60 日最高收盘（突破箱体/下跌结构上沿；不含当日，无前视）
  V3 均线转向：收盘 > MA20 **且** MA20 较 5 日前抬升（不是「下行均线上的反抽」）
  V4 动能确认：DIF > DEA **且** DIF > 0（零轴上方多头；零轴下金叉只是反弹）
  V5 量能确认：成交量 ≥ 1.5×20 日均量 **且** 当日收涨（突破要有资金，不是缩量假突破）

  票数 ≥ VOTE_ENTER(5) -> 买入信号（全票）；== VOTE_WATCH(4) -> 观察（差一票，等确认）

硬否决（任一成立即不买，票数再高也不买）
  - 追高：ret5 > RET5_MAX（12%）
  - 僵尸池：20 日均成交额 < AMT_MIN（3000 万）
  - 数据未就绪：所需因子存在 NaN

离场（两条，先后生效）
  - 结构止损：收盘跌破**入场日**结构位（信号日前 20 日最低收盘）-> 次日收盘清仓
  - 趋势离场：连续 EXIT_CONFIRM(2) 日收盘 < MA20 -> 次日收盘清仓

执行口径
  当日收盘算信号 -> **次日收盘**成交（十年库只有收盘价，无开盘价；不用未来数据）。
  单边费率 FEE（场内 ETF 佣金口径）。0/1 满仓，不做分层仓位。

参数改动只改本文件；`pipeline/strategies.json` 的 thresholds 是镜像。
"""
import numpy as np
import pandas as pd

# ---- 五票确认 ----
# 2026-10-18 由 research/_sweep.py 选定：5 票全过 vs 4 票的中位数对照 ——
#   回撤 -16.8% vs -25.2% / 利润因子 2.09 vs 1.55 / 单笔胜率 44.3% vs 38.6%
#   / 择时边际 +8.3bp vs +3.1bp；代价是持仓仅 9.3%（约每年 23 个交易日）。
# 结论与策略定位一致：右侧买点的价值在**不接飞刀**，宁可少买、不可买错。
VOTE_ENTER = 5           # 票数 >= 5（全票）-> 买入信号
VOTE_WATCH = 4           # 票数 == 4 -> 观察名单（差一票，等确认）

STRUCT_WIN_SHORT = 20    # V1: 近端低点窗口
STRUCT_WIN_LONG = 60     # V1: 远端低点窗口
STRUCT_EPS = 0.005       # V1: 低点抬高幅度阈值（0.5%，滤掉「齐平」噪声）
BREAK_WIN = 60           # V2: 突破窗口（前 N 日最高收盘）
MA_SHORT = 20            # V3: 短均线
MA_TREND = 60            # 展示用长均线（不作硬门槛，V 型反转早期常仍在 MA60 下方）
MA_SLOPE = 5             # V3: 均线斜率比较跨度
MACD_FAST, MACD_SLOW, MACD_SIGNAL = 12, 26, 9   # V4
VOL_WIN, VOL_MULT = 20, 1.5                     # V5

# ---- 硬否决 ----
RET5_MAX = 0.12          # 追高否决：5 日涨幅上限
AMT_MIN = 3e7            # 流动性否决：20 日均成交额下限（元）

# ---- 离场 ----
EXIT_MA = 20             # 趋势离场参考均线
EXIT_CONFIRM = 2         # 连续跌破天数确认
EXIT_REQUIRE_SLOPE = False   # True = 还要求 MA20 同期走平/下行才离场（更慢，抗震荡）
STOP_LOOKBACK = 10       # 结构止损位 = 信号日前 N 日最低收盘（10 日：止损更近，边际更高）

# ---- 执行 ----
FEE = 0.0003             # 单边交易成本（场内 ETF 佣金口径）
START = "2015-01-01"     # 抓取/样本可用起点（指标 warm-up 用）
SAMPLE_FROM = "2016-09-01"   # 回测样本起点（十年口径）
MIN_BARS = 250           # 样本内最少交易日（MA60/250 日分位预热）
LABEL = "rs5v1"          # 本参数组标签（写进报告，便于后续对照）

# 因子就绪所需列（缺任一 -> 该日信号无效）
READY_COLS = ("c", "v", "ma20", "ma60", "ma20_slope", "dif", "dea",
              "volma20", "hi60", "lo20", "lo60_prior", "stop_ref", "amt20")

VOTE_NAMES = {
    "v1": "结构止跌",
    "v2": "结构翻越",
    "v3": "均线转向",
    "v4": "动能确认",
    "v5": "量能确认",
}
VOTE_SHORT = {"v1": "低点抬高", "v2": "破60日高", "v3": "MA20上行",
              "v4": "零轴上多头", "v5": "放量上涨"}

PARAMS = dict(
    vote_enter=VOTE_ENTER, vote_watch=VOTE_WATCH,
    struct_win_short=STRUCT_WIN_SHORT, struct_win_long=STRUCT_WIN_LONG,
    struct_eps=STRUCT_EPS, break_win=BREAK_WIN,
    ma_short=MA_SHORT, ma_trend=MA_TREND, ma_slope=MA_SLOPE,
    macd_fast=MACD_FAST, macd_slow=MACD_SLOW, macd_signal=MACD_SIGNAL,
    vol_win=VOL_WIN, vol_mult=VOL_MULT,
    ret5_max=RET5_MAX, amt_min=AMT_MIN,
    exit_ma=EXIT_MA, exit_confirm=EXIT_CONFIRM, stop_lookback=STOP_LOOKBACK,
    fee=FEE, trade_at="信号次日收盘", label=LABEL,
)


# ---------------------------------------------------------------- 指标
def ema(s, n):
    """指数移动平均（与 xalpha/indicator.ema 同口径：ewm span, adjust=False）。"""
    return s.ewm(span=n, adjust=False).mean()


def macd(close):
    """DIF / DEA / HIST（12,26,9 标准口径，hist = 2*(dif-dea)）。"""
    dif = ema(close, MACD_FAST) - ema(close, MACD_SLOW)
    dea = ema(dif, MACD_SIGNAL)
    return dif, dea, 2.0 * (dif - dea)


def efficiency_ratio(close, n=20):
    """Kaufman 效率比：|净变动| / Σ|逐日变动|。→1 趋势，→0 震荡（仅展示，不进票）。"""
    net = (close - close.shift(n)).abs()
    path = close.diff().abs().rolling(n).sum()
    return net / path.replace(0.0, np.nan)


def boll_width(close, n=20, k=2.0):
    """布林带宽 =（上轨-下轨）/中轨（仅展示，用于解释「开口/收敛」）。"""
    mid = close.rolling(n).mean()
    sd = close.rolling(n).std(ddof=0)
    return (2 * k * sd) / mid.replace(0.0, np.nan)


# ---------------------------------------------------------------- 因子
def compute_factors(df):
    """单标的日线（index=日期, 列 c/v）-> 因子表（全部因果，不含未来数据）。"""
    c = df["c"].astype(float)
    v = df["v"].astype(float)
    f = pd.DataFrame(index=df.index)
    f["c"], f["v"] = c, v
    f["ma5"] = c.rolling(5).mean()
    f["ma10"] = c.rolling(10).mean()
    f["ma20"] = c.rolling(MA_SHORT).mean()
    f["ma60"] = c.rolling(MA_TREND).mean()
    f["ma20_slope"] = f["ma20"] - f["ma20"].shift(MA_SLOPE)
    f["dif"], f["dea"], f["hist"] = macd(c)
    f["bw"] = boll_width(c)
    f["er20"] = efficiency_ratio(c, 20)
    f["volma20"] = v.rolling(VOL_WIN).mean()
    f["vr"] = v / f["volma20"].replace(0.0, np.nan)
    f["ret1"] = c.pct_change()
    f["ret5"] = c / c.shift(5) - 1.0
    f["ret20"] = c / c.shift(20) - 1.0
    f["hi60"] = c.shift(1).rolling(BREAK_WIN).max()                 # 不含当日
    f["hi250"] = c.rolling(250).max()
    f["dd250"] = c / f["hi250"] - 1.0
    f["lo20"] = c.rolling(STRUCT_WIN_SHORT).min()
    f["lo60_prior"] = c.shift(STRUCT_WIN_SHORT).rolling(STRUCT_WIN_LONG).min()
    f["stop_ref"] = c.rolling(STOP_LOOKBACK).min()                  # 入场结构失效位
    f["amt20"] = (c * v).rolling(20).mean()
    f["bars"] = np.arange(1, len(c) + 1)

    # 五票
    f["v1"] = f["lo20"] > f["lo60_prior"] * (1.0 + STRUCT_EPS)
    f["v2"] = f["c"] > f["hi60"]
    f["v3"] = (f["c"] > f["ma20"]) & (f["ma20_slope"] > 0)
    f["v4"] = (f["dif"] > f["dea"]) & (f["dif"] > 0)
    f["v5"] = (f["v"] >= f["volma20"] * VOL_MULT) & (f["c"] > f["c"].shift(1))
    for k in VOTE_NAMES:
        f[k] = f[k].fillna(False).astype(bool)
    f["score"] = sum(f[k].astype(int) for k in VOTE_NAMES)

    # 否决 & 离场
    f["ready"] = f[list(READY_COLS)].notna().all(axis=1)
    f["veto_hot"] = f["ret5"] > RET5_MAX
    f["veto_liq"] = f["amt20"] < AMT_MIN
    f["veto"] = f["veto_hot"] | f["veto_liq"] | (~f["ready"])
    f["ma_exit"] = c.rolling(int(EXIT_MA)).mean() if int(EXIT_MA) != MA_SHORT else f["ma20"]
    exit_raw = f["c"] < f["ma_exit"]
    if EXIT_REQUIRE_SLOPE:
        exit_raw = exit_raw & (f["ma20_slope"] <= 0)
    ex = exit_raw.astype(bool)
    for k in range(1, max(int(EXIT_CONFIRM), 1)):
        ex = ex & exit_raw.shift(k, fill_value=False).astype(bool)
    f["exit_raw"] = exit_raw
    f["exit"] = ex
    return f


def build_signals(f):
    """因子表 -> 信号 dict（bool 数组，索引 t = 第 t 日收盘产出、t+1 日收盘执行）。"""
    ready = f["ready"].to_numpy(bool)
    score = f["score"].to_numpy(int)
    veto = f["veto"].to_numpy(bool)
    buy = (score >= VOTE_ENTER) & (~veto) & ready
    return dict(
        buy=buy,
        exit=f["exit"].to_numpy(bool),
        watch=(score == VOTE_WATCH) & (~veto) & ready,
        score=score,
        veto=veto,
        veto_hot=f["veto_hot"].to_numpy(bool),
        veto_liq=f["veto_liq"].to_numpy(bool),
        ready=ready,
        stop_ref=f["stop_ref"].to_numpy(float),
        votes={k: f[k].to_numpy(bool) for k in VOTE_NAMES},
    )


# ---------------------------------------------------------------- 引擎
def run_engine(close, sig, begin=0, fee=FEE):
    """0/1 状态机：信号次日收盘成交；结构止损优先于趋势离场。

    返回 (nav, pos_ratio, trades, holding, pos_log)
      nav       = 每交易日净值（空仓=现金，持仓=市值）
      pos_ratio = 样本窗口 [begin:] 持仓时间占比
      trades    = [{entry_i, exit_i, bars, ret, reason}]，ret 已扣双边费用
      holding   = 期末是否持仓（供每日扫描）
      pos_log   = 逐日是否持仓（供择时边际）
    """
    c = np.asarray(close, float)
    n = len(c)
    buy, exit_sig, stop_ref = sig["buy"], sig["exit"], sig["stop_ref"]
    cash, shares = 1.0, 0.0
    nav = np.ones(n)
    pos_log = np.zeros(n, dtype=bool)
    trades = []
    entry_i = None
    entry_px = None
    stop_px = None

    for i in range(n):
        if i > begin:
            t = i - 1                          # 信号日（收盘判定），i 日收盘执行
            if shares > 0:
                reason = None
                if stop_px is not None and c[i] < stop_px:
                    reason = "结构止损"
                elif exit_sig[t]:
                    reason = "趋势离场"
                if reason:
                    px = c[i] * (1.0 - fee)
                    cash = shares * px
                    ret = px / entry_px - 1.0 if entry_px and entry_px > 0 else 0.0
                    trades.append(dict(entry_i=entry_i, exit_i=i, bars=int(i - entry_i),
                                       ret=float(ret), reason=reason))
                    shares, entry_i, entry_px, stop_px = 0.0, None, None, None
            elif buy[t]:
                s = stop_ref[t]
                # 成交价已跌破结构位 -> 信号当场失效，不买（避免「买入即止损」）
                if np.isfinite(s) and c[i] >= s:
                    entry_px = c[i] * (1.0 + fee)
                    shares = cash / entry_px
                    cash = 0.0
                    entry_i, stop_px = i, float(s)
        pos_log[i] = shares > 0
        nav[i] = cash + shares * c[i]

    if entry_px and entry_px > 0:              # 期末仍持仓：按最新收盘估值
        trades.append(dict(entry_i=entry_i, exit_i=n - 1, bars=int(n - 1 - entry_i),
                           ret=float(c[-1] / entry_px - 1.0), reason="期末持仓"))
    return nav, float(pos_log[begin:].mean()), trades, bool(pos_log[-1]), pos_log


def classify(score, veto):
    """扫描分档：READY（买点）/ WATCH（差一票）/ VETO（票数够但被否决）/ NO（未达标）。

    VETO 只在**票数已达标**时成立 —— 低票数且流动性差的标的是「未达标」，不是「被否决」。
    """
    if score >= VOTE_ENTER:
        return "VETO" if veto else "READY"
    if score >= VOTE_WATCH:
        return "VETO" if veto else "WATCH"
    return "NO"
