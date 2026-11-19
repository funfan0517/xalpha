# -*- coding: utf-8 -*-
"""v21 · 沪深300 · 利弗莫尔关键点买入法（趋势确认 + 资金管理）。

来源：用户 2026-11-19 提供的视频口径（利弗莫尔关键点买入法，2005–2026 沪深300）。
这不是「长期持有」的替代品，而是一套**趋势确认 + 金字塔加仓 + 单笔止损**的风控策略：
大跌后不抄底，等收盘重新站上 20 日关键点才分批试仓，只在浮盈时加仓，跌破首买价 10% 全砍。

## 规则（把用户列出的「回测要控制的变量」逐条落定）

1. **触发场景**：距一年（252 日）最高收盘回撤 ≥ 20% —— 只在「熊市/深调」里允许新开仓，
   牛市里出现的关键点突破不参与（这是「不追高」的硬闸门）。
2. **关键点**：过去 `LOOKBACK=20` 日**最高收盘价**（`max(close[t-20:t])`，不含当日）。
   突破判定用**收盘价** `close[t] > 关键点`，不用盘中最高价 —— 避免盘中假突破。
3. **建仓**：T 日收盘确认突破 → **T+1 收盘**成交 20%（`EXEC_LAG=1`），收益从 T+2 起算。
   下跌途中不买、不摊平：加仓只在浮盈时发生。
4. **加仓（金字塔，只赚钱时加）**：相对**首买价** +5% / +10% / +15% 各触发一次，
   依次 +20% / +20% / +40%（20→40→60→100%）。**每根 K 线最多加一档**。
   `ADD_ANCHOR="first"` 也可切到 `"avg"`（相对加权平均成本），研究报告里有对照。
5. **止损**：**收盘价** ≤ 首买价 × (1 − 10%) → T+1 收盘全部清仓，回到「等下一个关键点」。
   不用盘中最低价，因为本条腿的执行载体是场外 `000051`（按收盘净值成交，盘中价不可成交）。
6. **退出**：视频没有固定止盈，主口径**只有止损**；`exit_mode` 另支持 `trail`（自持仓最高
   收盘回落 15%）与 `hold_days`（持有满 250 根），在 `research/_sweep.py` 里做对照。
7. **空仓计息**：货基 1.40%（族内统一口径）。视频口径「空仓不计息、指数不计分红」在研究报告
   里单列（`cash=0`），避免用计息把这条低频策略的收益说高。
8. **成本**：研究同尺单边 10bp；场外 A 类真实费率（申 0.10% / 赎 0.50%）在回测里单列一行。

## 与族的接口

`weight(px)` 返回的是**已经落地的持仓序列** `hold[t]`（t 日实际持有的仓位，已含 T+1 执行滞后），
不是「决策序列」—— 因为本策略是路径依赖的状态机（首买价、已加档数、止损线），
没法用一个 `decision.shift(1)` 表达。所以：
  - `run(px, hold)` **不再 shift**，直接按 `hold` 记收益；
  - `research/_plot_leg.py` 给 `_plot_leg_core` 传 `lag=0`，买卖点才不会整体后移一天。

边界（务必一起读）：
  - **样本内**。触发阈值 20%、加仓 5%/5%/5%、止损 10% 都是视频给的，没做参数搜索，
    但 2005–2026 只有 ~5 个完整熊市，样本量极小，`research/_sweep.py` 的邻域扫描**不能**
    当作过拟合的豁免；
  - 指数口径不含分红再投（沪深300 约 −2.0~2.5pp/年），且指数不会停牌、不会涨跌停；
  - 机械规则输出，非投资建议。
"""
import os
import sys

import numpy as np
import pandas as pd

_HERE = os.path.dirname(os.path.abspath(__file__))
_STRAT = os.path.dirname(_HERE)          # strategies/vol_legs
_VL = os.path.dirname(_STRAT)            # strategies
for _p in (_HERE, _STRAT, _VL):
    if _p not in sys.path:
        sys.path.insert(0, _p)

import _idxcache  # noqa: E402

TD = 252
CASH_ANNUAL = 0.014
FEE_SIDE = 0.0010

LEG = "hs300"
LABEL = "v21 沪深300 · 利弗莫尔关键点"
STATUS = "用户口径 · 样本外待验证"

# 预热 = 一年高点窗口 252：第一根可判定日就是 2006-01（再往前 252 日高点算不出来）。
# 状态机在**全样本**上跑，预热只决定报告/回测从哪天开始截，不会把首买价截断。
WARMUP = 252

PARAMS = dict(
    lookback=20,              # 关键点回看窗口（收盘价）
    dd_win=252,               # 一年高点窗口
    dd_trigger=0.20,          # 回撤触发线
    first_size=0.20,          # 首笔试仓
    add_levels=(0.05, 0.10, 0.15),   # 相对首买价的浮盈档位
    add_sizes=(0.20, 0.20, 0.40),    # 20 → 40 → 60 → 100
    add_anchor="first",       # first = 首买价 / avg = 加权平均成本
    stop_pct=0.10,            # 首买价 -10%
    exec_lag=1,               # 1 = 次日收盘成交（用户口径）；0 = 当日收盘视同成交
    exit_mode="stop",         # stop | trail | hold_days
    trail_pct=0.15,
    max_hold=250,
    cooldown=0,               # 止损后至少空 N 根才允许再开仓
    filter_vol=None,          # 例如 1.2 = 成交量 > 1.2 × 20 日均量
    filter_ma_up=False,       # 关键点突破且 MA20 上拐
    filter_weekly=False,      # 周线确认：周收盘 > 4 周前 且 > 20 周均线
    ma_win=20,
    weekly_win=20,
    weekly_mom=4,
)


# ---------------------------------------------------------------- 数据
def load(leg=LEG):
    """该腿信号价：真实指数收盘（沪深300 = `SH000300`，2005 起真实 OHLCV）。"""
    return _idxcache.series(leg)


def frame(leg=LEG):
    """该腿 OHLCV（关键点只用收盘；量能过滤用 `volume`）。"""
    return _idxcache.frame(leg)


# ---------------------------------------------------------------- 规则状态机
def _num_vol(vol, idx):
    if vol is None:
        return None
    s = pd.Series(vol, dtype="float64") if not isinstance(vol, pd.Series) else vol.astype("float64")
    return s.reindex(idx).astype("float64")


def analyze(px, p=None, vol=None):
    """跑一遍状态机 -> dict(hold, events, state, params)。

    `px` 只用于收盘价；量能过滤需要 `vol`（不传时按 `leg` 从 `_idxcache` 现取，避免调用方
    为了一个可选过滤器都要改签名）。
    """
    cfg = dict(PARAMS)
    if p:
        cfg.update(p)
    px = px.astype("float64").dropna()
    idx = px.index
    c = px.to_numpy(dtype=float)
    n = len(c)

    lkb, ddw = int(cfg["lookback"]), int(cfg["dd_win"])
    levels = tuple(float(x) for x in cfg["add_levels"])
    sizes = tuple(float(x) for x in cfg["add_sizes"])
    first_size, stop_pct = float(cfg["first_size"]), float(cfg["stop_pct"])
    lag, cooldown = int(cfg["exec_lag"]), int(cfg["cooldown"])
    mode = str(cfg["exit_mode"])

    s = pd.Series(c, index=idx)
    keypoint = s.rolling(lkb, min_periods=lkb).max().shift(1).to_numpy()
    year_high = s.rolling(ddw, min_periods=ddw).max().to_numpy()
    ma = s.rolling(int(cfg["ma_win"]), min_periods=int(cfg["ma_win"])).mean().to_numpy()
    ma_up = np.zeros(n, dtype=bool)
    ma_up[1:] = ma[1:] > ma[:-1]

    ok_vol = np.ones(n, dtype=bool)
    if cfg.get("filter_vol"):
        v = _num_vol(vol, idx)
        if v is None:
            v = _num_vol(frame()["volume"], idx)
        vma = v.rolling(int(cfg["ma_win"]), min_periods=int(cfg["ma_win"])).mean()
        ok_vol = (v > float(cfg["filter_vol"]) * vma).fillna(False).to_numpy(dtype=bool)

    ok_wk = np.ones(n, dtype=bool)
    if cfg.get("filter_weekly"):
        wk = s.resample("W-FRI").last().dropna()
        wma = wk.rolling(int(cfg["weekly_win"]), min_periods=int(cfg["weekly_win"])).mean()
        flag = (wk > wk.shift(int(cfg["weekly_mom"]))) & (wk > wma)
        ok_wk = flag.reindex(idx, method="ffill").fillna(False).to_numpy(dtype=bool)

    hold = np.zeros(n)
    pos, avg, first, peak = 0.0, np.nan, np.nan, np.nan
    adds, entry_i, last_stop_i = 0, -1, -10 ** 9
    pending = None
    events = []

    def _fill(i, tgt, kind, sig_i):
        """在 close[i] 成交到目标仓位 tgt。"""
        nonlocal pos, avg, first, peak, adds, entry_i, last_stop_i
        tgt = float(tgt)
        old = pos
        if tgt <= 1e-12:
            ev = dict(date=str(idx[i].date()), sig_date=str(idx[sig_i].date()), kind=kind,
                      price=float(c[i]), from_weight=round(old, 4), to_weight=0.0,
                      first_buy=None if np.isnan(first) else round(float(first), 4))
            events.append(ev)
            pos, avg, first, peak, adds, entry_i = 0.0, np.nan, np.nan, np.nan, 0, -1
            last_stop_i = i if kind == "stop" else last_stop_i
            return
        if old <= 1e-12:
            first = avg = peak = float(c[i])
            adds, entry_i, ev_kind = 0, i, "entry"
        elif kind == "add" and np.isfinite(first) and c[i] <= first * (1.0 - stop_pct):
            # 「只在浮盈时加仓」：成交价已经跌破止损线时，这笔加仓作废（下一根按止损处理）
            return
        else:
            adds += 1
            avg = (avg * old + float(c[i]) * (tgt - old)) / tgt if tgt > 0 else avg
            peak = max(peak, float(c[i]))
            ev_kind = "add"
        pos = tgt
        events.append(dict(date=str(idx[i].date()), sig_date=str(idx[sig_i].date()), kind=ev_kind,
                           price=float(c[i]), from_weight=round(old, 4), to_weight=round(tgt, 4),
                           first_buy=round(float(first), 4), avg_cost=round(float(avg), 4)))

    for i in range(n):
        hold[i] = pos
        if pending is not None and i >= pending["fill_i"]:
            _fill(i, pending["tgt"], pending["kind"], pending["sig_i"])
            pending = None
        if i < max(lkb, ddw):
            continue

        if pos <= 1e-12:
            if pending is not None or (i - last_stop_i) < cooldown:
                continue
            high = year_high[i]
            dd = c[i] / high - 1.0 if np.isfinite(high) and high > 0 else 0.0
            if (dd <= -float(cfg["dd_trigger"])
                    and np.isfinite(keypoint[i]) and c[i] > keypoint[i]
                    and ok_vol[i]
                    and (not cfg["filter_ma_up"] or ma_up[i])
                    and ok_wk[i]):
                pending = dict(fill_i=i + lag, sig_i=i, tgt=first_size, kind="entry")
        else:
            if pending is not None:
                continue
            peak = max(peak, float(c[i]))
            stop_line = first * (1.0 - stop_pct)
            if c[i] <= stop_line:
                pending = dict(fill_i=i + lag, sig_i=i, tgt=0.0, kind="stop")
            elif mode == "trail" and peak > 0 and c[i] <= peak * (1.0 - float(cfg["trail_pct"])):
                pending = dict(fill_i=i + lag, sig_i=i, tgt=0.0, kind="trail")
            elif mode == "hold_days" and entry_i >= 0 and (i - entry_i) >= int(cfg["max_hold"]):
                pending = dict(fill_i=i + lag, sig_i=i, tgt=0.0, kind="timeout")
            elif pos < 1.0 - 1e-12 and adds < len(sizes):
                anchor = first if str(cfg["add_anchor"]) == "first" else avg
                if (np.isfinite(anchor) and c[i] >= anchor * (1.0 + levels[adds])
                        and ok_vol[i]
                        and (not cfg["filter_ma_up"] or ma_up[i])
                        and ok_wk[i]):
                    pending = dict(fill_i=i + lag, sig_i=i,
                                   tgt=min(1.0, pos + sizes[adds]), kind="add")

        if pending is not None and pending["fill_i"] <= i:      # exec_lag=0：当日收盘成交
            _fill(i, pending["tgt"], pending["kind"], pending["sig_i"])
            pending = None

    i = n - 1
    high = year_high[i]
    dd = float(c[i] / high - 1.0) if np.isfinite(high) and high > 0 else float("nan")
    anchor = (first if str(cfg["add_anchor"]) == "first" else avg) if pos > 0 else np.nan
    state = dict(
        date=str(idx[i].date()), close=float(c[i]), pos=float(pos),
        first_buy=None if np.isnan(first) else float(first),
        avg_cost=None if np.isnan(avg) else float(avg),
        adds=int(adds), dd=dd,
        dd_trigger=float(cfg["dd_trigger"]),
        keypoint=None if not np.isfinite(keypoint[i]) else float(keypoint[i]),
        peak=None if np.isnan(peak) else float(peak),
        trail15=None if np.isnan(peak) else float(peak * 0.85),
        stop_price=None if np.isnan(first) else float(first * (1.0 - stop_pct)),
        next_add_price=(float(anchor * (1.0 + levels[adds]))
                        if (pos > 0 and adds < len(sizes) and np.isfinite(anchor)) else None),
        pending=None if pending is None else dict(target=float(pending["tgt"]),
                                                  kind=pending["kind"],
                                                  sig_date=str(idx[pending["sig_i"]].date())),
    )
    return dict(hold=pd.Series(hold, index=idx), events=events, state=state, params=cfg)


def weight(px, p=None, vol=None):
    """**实际持仓序列**（不是决策序列）：`hold[t]` = t 日持有的仓位，已含 T+1 执行滞后。"""
    return analyze(px, p=p, vol=vol)["hold"]


# ---------------------------------------------------------------- 回测/绩效
def run(px, sig=None, cash=None, fee=None):
    """按实际持仓记净值：`hold[t]` 吃 t 日收益，成本 = |Δhold| × 单边费率。

    `sig` 必须是 `weight()` 的产物（已含执行滞后）；不传就现算。
    `fee` 可以是单边标量（研究口径 10bp），也可以是 `(买, 卖)` 元组（场外 A 类 0.10%/0.50%）。
    """
    h = weight(px) if sig is None else sig.reindex(px.index).fillna(0.0)
    h = h.astype("float64").clip(0.0, 1.0)
    cash_d = ((CASH_ANNUAL if cash is None else float(cash)) + 1.0) ** (1.0 / TD) - 1.0
    if isinstance(fee, tuple):
        buy_fee, sell_fee = float(fee[0]), float(fee[1])
    else:
        buy_fee = sell_fee = FEE_SIDE if fee is None else float(fee)
    dh = h.diff().fillna(h)
    cost = dh.clip(lower=0.0) * buy_fee + (-dh.clip(upper=0.0)) * sell_fee
    ret = px.astype("float64").pct_change().fillna(0.0)
    return (1.0 + h * ret + (1.0 - h) * cash_d - cost).cumprod()


def perf(nav):
    """与族内同口径：年化 / 波动 / 最大回撤 / 夏普 / Calmar / 累计。"""
    r = nav.pct_change().dropna()
    n = len(r)
    if n < 2:
        return dict(ann=np.nan, vol=np.nan, mdd=np.nan, sharpe=np.nan,
                    calmar=np.nan, cum=np.nan)
    ann = float(nav.iloc[-1] ** (TD / n) - 1.0)
    vol = float(r.std() * np.sqrt(TD))
    mdd = float((nav / nav.cummax() - 1.0).min())
    return dict(ann=ann, vol=vol, mdd=mdd,
                sharpe=(ann - CASH_ANNUAL) / vol if vol > 0 else np.nan,
                calmar=ann / abs(mdd) if mdd < 0 else np.nan,
                cum=float(nav.iloc[-1] - 1.0))


# ---------------------------------------------------------------- 对照基准
def hold_buy_dip(px, trigger=0.20, win=252):
    """「一把抄底」：首次回撤 ≥ 20% 当天满仓，之后一直满仓（视频对照口径，无止损）。"""
    s = px.astype("float64")
    dd = s / s.rolling(int(win), min_periods=int(win)).max() - 1.0
    hit = np.where((dd <= -float(trigger)).to_numpy())[0]
    h = pd.Series(0.0, index=s.index)
    if len(hit):
        h.iloc[int(hit[0]) + 1:] = 1.0          # 触发日收盘满仓，次日起计收益
    return h


def hold_buy_hold(px):
    return pd.Series(1.0, index=px.index)
