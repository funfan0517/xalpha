# -*- coding: utf-8 -*-
"""波动资产 · V11 变种：支撑/压力位 × 期望值 —— **事件研究**（不是仓位规则）。

来源：`E:\\qianwen\\jijin\\支撑压力位期望值策略.md`（抖音「熊猫讲技术分析」口播拆解，
2026-10-15 发布；作者**未给样本量、周期、品种、成交成本、胜率口径**）。该文档 §4 自己
写明了「若要把它变成可验证的规则」的顺序，本模块就是**逐条执行那四步**：

1. 先写死定义（§4.1）—— 趋势 / 支撑 / 止损 / 目标，四个量的算法全部固定在本文件；
2. 单腿样本 ≥ **50 笔**才允许谈业绩（§4.2）—— 不足的腿报告只列信号日，不汇总成「业绩」；
3. 只看信号日后 **20/60/120 日**前向收益分布 vs 同腿同区间的**无条件基准**（§4.3）——
   **本变种因此刻意不输出任何年化 / 最大回撤 / 夏普**，这跟家族其它变种（v1~v9 都是
   NAV 择时回测）是根本区别，不是漏写；
4. 条件逐个加 + 查共线（§4.4）—— 见 `backtest.py` 的消融表与 `coverage_vs_v5()`。

## 1. 四个量的写死版（文档 §4.1 的可执行翻译）

| 量 | 文档原文 | 本实现 | 为什么这么定 |
|---|---|---|---|
| 趋势 | 「收盘价 > MA200 且 MA200 斜率 > 0，月频判定」 | `close > MA200` 且 `MA200 − MA200.shift(20) > 0`；**每月最后一个交易日判定、次月整月沿用**（`TREND_FREQ="monthly"`，`shift(1)` 保证无前视） | 文档写死 MA200（**不是** v5 的 MA250），月频是为了避免「同一根线每天翻一次」；日频作对照 |
| 支撑 | 「前低或成交密集区下沿」 | 主口径 `SUP_MODE="pivot"`：最近一个**已确认**摆动低点（`SWING_N=10`，滞后 10 日才确认）；对照口径 `"profile"`：近 250 日收盘价的**时间密集区**下沿 | 本地行情库只有 close（`data/_long_klines.json` / 场外净值都无 OHLCV），**没有成交量 → 只能做「时间密集区」**，不是真正的 volume profile，报告里全程标注 |
| 止损 | 「支撑下方 0.5×ATR(14)」 | `stop = support − 0.5 × ATR14`，`ATR14 = mean(|Δclose|, 14)` | 同上没有 H/L：`TR = max(H−L, |H−C₋₁|, |L−C₋₁|) ≥ |C−C₋₁|`，所以 close-to-close 的 ATR 是真实 ATR 的**严格下界** → 止损更紧 → 更易被打掉 → **对策略不利（保守偏置）** |
| 目标 | 「上方密集区上沿」 | 主口径固定 `RR=5`（文档口播的「赚 1000 / 亏 200」）；结构目标（上方枢轴高点 / 密集区上沿）单列 `target_struct` 作对照 | 文档给的期望值例子就是 5:1；结构目标与 5R 并列，才能回答「5R 现实吗」 |

## 2. 出场与记账（事件口径，不是持仓口径）

- **触线（signal）**：`0 ≤ close/support − 1 ≤ BAND(2%)` 且 `close ≥ support`（未破位）。
  破位（close < support）不算「回踩支撑」，按文档铁律①是「走人」，不进样本。
- **确认（可选层 L2）**：「到线不动手，要等确认」→ `close[t] − close[t−1] ≥ CONF_ATR × ATR14[t]`
  （`CONF_ATR=0.25`，即触线当日收出至少 1/4 ATR 的涨幅）。close-only 口径下这是「阳线」的替代。
- **出场**：从 t+1 起逐日看收盘，先触 `stop` → `"stop"`、先触 `target` → `"target"`、
  满 `HOLD_MAX=120` 个交易日 → `"timeout"`（同日两者都满足算 stop，保守）。
- **冷却**：同一腿两个事件至少间隔 `COOLDOWN=20` 个交易日 —— 否则一次触线会连出十几个「样本」，
  把样本量虚增（这正是「≥50 笔」这条守门的漏洞所在）。被冷却掉的候选数在报告里单列。
- **R 的定义**：`1R = entry − stop`（entry = 信号日收盘）。`realized_R = (exit/entry − 1)/risk_pct`，
  所以止损位被打穿时可能 < −1R，目标被跳空穿过时可能 > +5R，都按实际值记，不截断。

## 3. 这个变种回答什么、不回答什么

回答：**「上涨趋势里回踩支撑做多」这个主张，在七腿的 15 年场外净值上，前向收益分布是否
显著优于「同一腿随便哪天买入」**；以及文档那个 `70% × 5R − 30% × 1R = +3.2R` 的期望值
在实测里是多少、错在哪（推断：**胜率与盈亏比不独立**，止损越紧、胜率越低）。

**不回答**：能不能赚钱（→ 文档 §4.3 明令不在小样本上报年化/回撤）；下跌趋势里做空
（→ A 股散户无做空工具，文档 §3.4 自己承认落不了地，本变种只统计「下跌趋势触支撑」的
前向收益作对照，不做空）；压力位做空（同理）。

模拟结果，非投资建议。
"""
import os
import sys

import numpy as np
import pandas as pd

try:
    sys.stdout.reconfigure(encoding="utf-8")  # type: ignore[attr-defined]
except Exception:  # noqa: BLE001
    pass

_HERE = os.path.dirname(os.path.abspath(__file__))
_VL = os.path.dirname(_HERE)                        # strategies/vol_legs
_ROOT = os.path.dirname(os.path.dirname(_VL))       # 仓库根
for _p in (_HERE, _VL):
    if _p not in sys.path:
        sys.path.insert(0, _p)

import data as vdata    # noqa: E402
import rule             # noqa: E402

LABEL = "V11 支撑/压力位 × 期望值（事件研究）"

# ---------------------------------------------------------------- 1. 参数（唯一权威源）
MA_WIN = 200            # 趋势线：文档 §4.1 写死 MA200（注意 v5 用的是 MA250，两者不可混用）
SLOPE_N = 20            # MA200 自身斜率回看交易日
TREND_FREQ = "monthly"  # "monthly" 月末判定次月沿用（文档口径）/ "daily" 日频（对照）

SWING_N = 10            # 摆动点确认窗口：i 日才确认 i-SWING_N 是局部极值（无前视）
SUP_LOOKBACK = 250      # 「前低 / 密集区」的回看窗口
SUP_MODE = "pivot"      # "pivot" 前低（主口径）/ "profile" 时间密集区（无成交量的降级替代）
PROFILE_BINS = 30       # 密集区的价格分箱数
PROFILE_Q = 0.20        # 「密集」= 落价次数最多的 20% 分箱

BAND = 0.02             # 距支撑 2% 内算「到线」
TREND_SIDE = "up"       # "up" 上涨趋势回踩支撑（文档主主张）/ "down" 下跌趋势触支撑（「不抄底」对照）
ATR_N = 14              # ATR 窗口（close-to-close 版，见 docstring §1）
STOP_ATR = 0.5          # 止损 = 支撑下方 0.5×ATR
RR = 5.0                # 目标盈亏比（文档口播 1000:200）
CONF_ATR = 0.25         # 「确认」= 触线日涨幅 ≥ 0.25×ATR（L2 层）
COOLDOWN = 20           # 同一腿两个事件的最小间隔（交易日）
HOLD_MAX = 120          # 事件最长跟踪 120 个交易日
FWD = (20, 60, 120)     # 前向收益窗口（文档 §4.3 指定）

MIN_SAMPLE = 50         # 文档 §4.2：单腿 ≥50 笔才允许谈业绩

WARMUP = max(MA_WIN + SLOPE_N, SUP_LOOKBACK)   # 趋势线与支撑位都成形的最早交易日
LEGS = rule.LEG_ORDER
# 池化一律**每笔一票**，不按目标带加权（v1~v9 的 W）：事件研究问的是「这一笔交易值不值」，
# 不是「组合层该配多少 pp」。按带加权会让美股/黄金的 10pp 腿吞掉 A股 2.5pp 腿的声音，
# 而「支撑位有没有效」这件事与目标带大小无关。


def params(**kw):
    """参数打包：回测敏感性扫描时用 `**{...}` 覆盖，不必改模块常量。"""
    p = dict(MA_WIN=MA_WIN, SLOPE_N=SLOPE_N, TREND_FREQ=TREND_FREQ,
             SWING_N=SWING_N, SUP_LOOKBACK=SUP_LOOKBACK, SUP_MODE=SUP_MODE,
             PROFILE_BINS=PROFILE_BINS, PROFILE_Q=PROFILE_Q,
             BAND=BAND, TREND_SIDE=TREND_SIDE, ATR_N=ATR_N, STOP_ATR=STOP_ATR, RR=RR,
             CONF_ATR=CONF_ATR, COOLDOWN=COOLDOWN, HOLD_MAX=HOLD_MAX, FWD=FWD)
    p.update(kw)
    return p


def load(leg, mode=None):
    """该腿价格序列（默认场外复权净值）；缺失直接抛错，不静默补位。"""
    return vdata.series_for_leg(leg, mode=mode).dropna()


# ---------------------------------------------------------------- 2. 四个量的实现
def atr(px, n=ATR_N):
    """ATR(14) 的 close-to-close 版：`mean(|Δclose|)`。

    真实 ATR 的 TR = max(H−L, |H−C₋₁|, |L−C₋₁|) ≥ |C−C₋₁|，所以本式是真实 ATR 的
    **严格下界** —— 止损带因此更紧、更容易被打掉，方向上是保守偏置（不会美化策略）。
    """
    return px.astype("float64").diff().abs().rolling(int(n), min_periods=int(n)).mean()


def _monthly_hold(flag, px, freq):
    """把日频布尔量压成「月末判定、次月沿用」。`shift(1)` 保证当月用的是上月末的状态。"""
    if freq == "daily":
        return flag.fillna(False).astype(bool)
    if freq != "monthly":
        raise ValueError(f"未知 TREND_FREQ: {freq}（可选 monthly / daily）")
    month_end = flag.groupby([flag.index.year, flag.index.month]).tail(1).astype("float64")
    state = month_end.shift(1)                       # 次月沿用上月末判定（无前视）
    held = state.reindex(px.index).ffill()
    return (held.fillna(0.0) > 0.5)


def trend_flags(px, **kw):
    """-> (up, dn) 两条布尔序列（已按月/日口径处理）。

    `up` = `close > MA(MA_WIN)` 且 `MA 近 SLOPE_N 日上行`（文档 §4.1 的上涨趋势）；
    `dn` = 完全对称的下跌趋势（`close < MA` 且 `MA` 下行）—— 只用于文档里两个下跌趋势格
    （「触支撑不抄底」「反弹到压力位做空」，后者 A 股不可执行，只作诊断）。
    """
    p = params(**kw)
    c = px.astype("float64")
    ma = c.rolling(int(p["MA_WIN"]), min_periods=int(p["MA_WIN"])).mean()
    slope = ma - ma.shift(int(p["SLOPE_N"]))
    ok = ma.notna() & slope.notna()
    up = (c > ma) & (slope > 0.0) & ok
    dn = (c < ma) & (slope < 0.0) & ok
    return _monthly_hold(up, c, p["TREND_FREQ"]), _monthly_hold(dn, c, p["TREND_FREQ"])


def ma_trend(px, **kw):
    """上涨趋势（文档 §4.1 的「趋势」）。月频 = 月末判定、次月整月沿用，无前视。"""
    return trend_flags(px, **kw)[0]


def pivots(px, n=SWING_N):
    """已确认的摆动低点 / 高点 -> (low, high) 两条稀疏 Series。

    `i` 日的值来自 `i-n` 日：**要看到 i 日才知道 i-n 是 2n+1 窗口里的极值**，
    所以 `shift(n)` 是确认滞后，不是装饰 —— 少了它就是前视。
    """
    c = px.astype("float64")
    win = 2 * int(n) + 1
    roll_min = c.rolling(win, center=True, min_periods=win).min()
    roll_max = c.rolling(win, center=True, min_periods=win).max()
    low = c.where(c <= roll_min).shift(int(n))
    high = c.where(c >= roll_max).shift(int(n))
    return low, high


def profile_levels(c, i, p):
    """`[i-LOOKBACK+1, i]` 收盘价的**时间密集区** -> (下沿支撑, 上沿目标)。

    没有成交量，所以「密集」= 落在该价格箱的交易日数多（时间是成交量的降级替代）。
    下沿 = 最近一个位于当前价下方的密集箱下边界；上沿 = 最近一个位于当前价上方的密集箱上边界。
    """
    w = c.iloc[max(0, i - int(p["SUP_LOOKBACK"]) + 1):i + 1]
    if len(w) < 30:
        return None, None
    hist, edges = np.histogram(w.to_numpy(dtype=float), bins=int(p["PROFILE_BINS"]))
    thr = np.quantile(hist, 1.0 - float(p["PROFILE_Q"]))
    dense = np.where(hist >= max(thr, 1.0))[0]
    if not len(dense):
        return None, None
    cur = float(c.iloc[i])
    below = [b for b in dense if edges[b + 1] <= cur]
    above = [b for b in dense if edges[b] >= cur]
    sup = float(edges[below[-1]]) if below else None
    tgt = float(edges[above[0] + 1]) if above else None
    return sup, tgt


def build(px, leg, **kw):
    """逐日候选表。列 = 判断这四件事所需的全部诊断量，**不含任何未来信息**。

    - `trend`  : 月频趋势（文档 §4.1）
    - `atr`    : close-to-close ATR（严格下界）
    - `support` / `sup_kind` : 支撑位（前低 / 密集区下沿）
    - `dist`   : close/support − 1（>0 = 在支撑上方）
    - `on_line`: 是否处于「到线带」内且未破位
    - `conf`   : L2 确认（触线日涨幅 ≥ CONF_ATR×ATR）
    - `target_struct` : 结构目标（上方枢轴高点 / 密集区上沿），只作对照
    """
    p = params(**kw)
    c = px.astype("float64")
    out = pd.DataFrame(index=px.index)
    out["close"] = c
    out["atr"] = atr(c, p["ATR_N"])
    up, dn = trend_flags(c, **kw)
    out["trend"] = up
    out["trend_dn"] = dn

    if p["SUP_MODE"] == "pivot":
        low, high = pivots(c, p["SWING_N"])
        out["support"] = low.ffill()
        out["target_struct"] = high.ffill()
        out["sup_kind"] = "前低"
    elif p["SUP_MODE"] == "profile":
        n = len(c)
        sup_v = np.full(n, np.nan)
        tgt_v = np.full(n, np.nan)
        for i in range(n):
            s, t = profile_levels(c, i, p)
            sup_v[i], tgt_v[i] = (np.nan if s is None else s), (np.nan if t is None else t)
        out["support"] = sup_v
        out["target_struct"] = tgt_v
        out["sup_kind"] = "密集区下沿"
    else:
        raise ValueError(f"未知 SUP_MODE: {p['SUP_MODE']}（可选 pivot / profile）")

    out["dist"] = c / out["support"] - 1.0
    # 「到线」= 在支撑上方 0~BAND 内（破位不算回踩，按文档铁律①是走人，不进样本）
    out["on_line"] = (out["dist"] >= 0.0) & (out["dist"] <= float(p["BAND"]))
    out["conf"] = (c - c.shift(1)) >= float(p["CONF_ATR"]) * out["atr"]
    return out


# ---------------------------------------------------------------- 3. 事件构造
def _forward(c, i, hs):
    """信号日 i 之后 h 个交易日的前向收益（基 = 信号日收盘）。"""
    out = {}
    for h in hs:
        j = i + int(h)
        out[f"f{h}"] = float(c[j] / c[i] - 1.0) if j < len(c) else np.nan
    return out


def _path(c, i, stop, target, hold_max):
    """从 t+1 起逐日看收盘 -> (结局, 退出序号, 退出价, MFE_R 前收盘, MAE_R)。"""
    n = len(c)
    end = min(i + int(hold_max), n - 1)
    mfe = mae = 0.0
    for j in range(i + 1, end + 1):
        r = float(c[j] / c[i] - 1.0)
        mfe, mae = max(mfe, r), min(mae, r)
        if float(c[j]) <= stop:
            return "stop", j, float(c[j]), mfe, mae
        if float(c[j]) >= target:
            return "target", j, float(c[j]), mfe, mae
    return "timeout", end, float(c[end]), mfe, mae


def events(px, leg, **kw):
    """-> (ev, feat)。`ev` 是冷却后的独立事件，`feat` 是逐日候选表。

    事件列：date / close / trend / support / dist / atr / stop / target / risk_pct /
    conf / f20 / f60 / f120 / exit_kind / exit_date / bars_held / realized_R / mfe_R / mae_R /
    hit_struct。
    """
    p = params(**kw)
    side = p["TREND_SIDE"]
    if side not in ("up", "down"):
        raise ValueError(f"未知 TREND_SIDE: {side}（可选 up / down）")
    F = build(px, leg, **kw)
    flag = F["trend"] if side == "up" else F["trend_dn"]
    c = F["close"].to_numpy(dtype=float)
    n = len(c)
    rows = []
    cand = 0
    last = -10 ** 9
    for i in range(int(WARMUP), n):
        if not bool(flag.iloc[i]):
            continue
        sup = F["support"].iloc[i]
        if not np.isfinite(sup) or sup >= c[i]:
            continue
        if not bool(F["on_line"].iloc[i]):
            continue
        a = F["atr"].iloc[i]
        if not np.isfinite(a) or a <= 0:
            continue
        stop = float(sup) - float(p["STOP_ATR"]) * float(a)
        risk = c[i] - stop
        if risk <= 0:
            continue
        cand += 1
        if i - last < int(p["COOLDOWN"]):
            continue
        last = i
        target = c[i] + float(p["RR"]) * risk
        kind, j, ep, mfe, mae = _path(c, i, stop, target, p["HOLD_MAX"])
        row = dict(leg=leg, name=rule.LEGS[leg]["name"], role=rule.LEGS[leg]["role"],
                   date=px.index[i], close=float(c[i]), side=side,
                   trend=bool(F["trend"].iloc[i]), trend_dn=bool(F["trend_dn"].iloc[i]),
                   support=float(sup), sup_kind=F["sup_kind"].iloc[i],
                   dist=float(F["dist"].iloc[i]), atr=float(a),
                   stop=stop, target=target, risk_pct=float(risk / c[i]),
                   conf=bool(F["conf"].iloc[i]),
                   target_struct=(None if not np.isfinite(F["target_struct"].iloc[i])
                                  else float(F["target_struct"].iloc[i])),
                   exit_kind=kind, exit_date=px.index[j], bars_held=int(j - i),
                   exit_price=ep,
                   realized_R=float((ep / c[i] - 1.0) / (risk / c[i])),
                   mfe_R=float(mfe / (risk / c[i])), mae_R=float(mae / (risk / c[i])))
        row.update(_forward(c, i, p["FWD"]))
        ts = row["target_struct"]
        row["hit_struct"] = bool(ts is not None and mfe > 0 and c[i] * (1.0 + mfe) >= ts)
        # 「撑住」= 未来 20 个交易日的最低价（收盘口径）都没跌破支撑 —— 直接对应
        # 文档口播的「上涨趋势支撑撑住 ≈70% / 下跌趋势支撑撑住只有 30~40%」两个数字。
        j20 = min(i + 20, n - 1)
        lo = float(c[i + 1:j20 + 1].min()) if i + 1 <= j20 else np.nan
        row["min20"] = lo
        row["hold20"] = bool(np.isfinite(lo) and lo >= float(sup))
        # 交易口径的「撑住」= 未来 20 日内连**止损位**（支撑下方 0.5×ATR）都没碰到。
        # 比 hold20 宽松：价格可以插破支撑，只要没破止损就不算出局。
        row["hold20_stop"] = bool(np.isfinite(lo) and lo >= stop)
        rows.append(row)
    ev = pd.DataFrame(rows)
    if len(ev):
        ev["cand"] = cand
    return ev, F


def pressure_events(px, leg, side="up", **kw):
    """趋势方向上「到达压力位」的事件 —— 文档四格里的第 2 格与第 3 格。

    - `side="up"`  ：**上涨趋势**中价格贴到压力位下方（第 2 格：作者说突破 60~70%）；
    - `side="down"`：**下跌趋势**中反弹到压力位下方（第 3 格：作者说「压住约 70%，该做空」）。

    压力位 = 最近一个**已确认**的摆动高点（`pivots` 的 high 列，滞后 SWING_N 确认）；
    事件 = `0 ≤ piv_high/close − 1 ≤ BAND`（价格贴在压力位下方、尚未突破），冷却同主口径。
    结局统计**突破率**（未来 20/60 日内收盘是否 ≥ piv_high）+ 前向收益 —— **不算做空 EV**：
    A 股散户没有做空工具，文档 §3.4 自己也承认那一半打法落不了地，给了 EV 也执行不了。
    `down` 侧额外统计 `below_sup20`（20 日内收盘是否跌破**当时**的支撑）——
    它是「做空方向有没有空间」的代理，也是 A 股唯一能落地动作（反弹不加仓/减仓）的依据。
    """
    p = params(**kw)
    if side not in ("up", "down"):
        raise ValueError(f"未知 side: {side}（可选 up / down）")
    c = px.astype("float64")
    up, dn = trend_flags(c, **kw)
    flag = up if side == "up" else dn
    low, high = pivots(c, p["SWING_N"])
    piv = high.ffill()
    sup = low.ffill()
    cc = c.to_numpy(dtype=float)
    pv = piv.to_numpy(dtype=float)
    sv = sup.to_numpy(dtype=float)
    n = len(cc)
    rows, last = [], -10 ** 9
    for i in range(int(WARMUP), n):
        if not bool(flag.iloc[i]) or not np.isfinite(pv[i]) or pv[i] < cc[i]:
            continue
        gap = float(pv[i] / cc[i] - 1.0)
        if gap < 0.0 or gap > float(p["BAND"]):
            continue
        if i - last < int(p["COOLDOWN"]):
            continue
        last = i
        row = dict(leg=leg, name=rule.LEGS[leg]["name"], side=side, date=px.index[i],
                   close=float(cc[i]), pivot_high=float(pv[i]),
                   support=(float(sv[i]) if np.isfinite(sv[i]) else None), gap=gap)
        row.update(_forward(cc, i, p["FWD"]))
        for h in (20, 60):
            seg = cc[i + 1:min(i + h + 1, n)]        # 只统计「完整 h 日窗口」
            row[f"brk{h}"] = bool(seg.max() >= pv[i]) if len(seg) >= h else None
        seg20 = cc[i + 1:min(i + 21, n)]
        row["below_sup20"] = (bool(seg20.min() < sv[i])
                              if (len(seg20) >= 20 and np.isfinite(sv[i])) else None)
        rows.append(row)
    return pd.DataFrame(rows)


# ---------------------------------------------------------------- 4. 基础统计
def fwd_stats(sub, hs=FWD):
    """前向收益分布：笔数 / 均值 / 中位 / 胜率 / 10 分位 / 最差 / 最好。"""
    out = dict(n=int(len(sub)))
    for h in hs:
        s = pd.to_numeric(sub.get(f"f{h}"), errors="coerce").dropna()
        key = f"f{h}"
        if not len(s):
            out.update({f"{key}_mean": None, f"{key}_med": None, f"{key}_win": None,
                        f"{key}_q10": None, f"{key}_worst": None})
            continue
        out.update({f"{key}_mean": float(s.mean()), f"{key}_med": float(s.median()),
                    f"{key}_win": float((s > 0).mean()),
                    f"{key}_q10": float(s.quantile(0.10)), f"{key}_worst": float(s.min())})
    return out


def baseline(px, start=None, hs=FWD):
    """无条件基准：同一腿、同一区间「随便哪天买入」的前向收益均值/胜率（= 买入持有）。"""
    c = px.astype("float64")
    i0 = 0
    if start is not None:
        i0 = int(max(0, c.index.get_indexer([pd.Timestamp(start)], method="nearest")[0]))
    vals = {h: [] for h in hs}
    arr = c.to_numpy(dtype=float)
    for i in range(i0, len(arr)):
        for h in hs:
            if i + h < len(arr):
                vals[h].append(float(arr[i + h] / arr[i] - 1.0))
    out = dict(n=len(vals[hs[0]]))
    for h in hs:
        s = pd.Series(vals[h], dtype="float64")
        out.update({f"f{h}_mean": float(s.mean()) if len(s) else None,
                    f"f{h}_win": float((s > 0).mean()) if len(s) else None,
                    f"f{h}_q10": float(s.quantile(0.10)) if len(s) else None})
    return out


def exit_stats(ev):
    """结局构成 + R 口径期望值 + 文档口径期望值。

    - `ev_R`  = `mean(realized_R)`（实际路径，按 1R = entry − stop）
    - `ev_doc`= `hit_target_rate × RR − (1 − hit_target_rate) × 1`（文档那个
      「赢的概率 × 赢时赚多少 − 输的概率 × 输时亏多少」，把「赢」定义为打到 5R 目标）
    """
    if not len(ev):
        return dict(n=0)
    n = len(ev)
    kinds = ev["exit_kind"].value_counts()
    hit = float(kinds.get("target", 0)) / n
    return dict(
        n=n,
        target=int(kinds.get("target", 0)), stop=int(kinds.get("stop", 0)),
        timeout=int(kinds.get("timeout", 0)),
        hit_rate=hit, stop_rate=float(kinds.get("stop", 0)) / n,
        timeout_rate=float(kinds.get("timeout", 0)) / n,
        ev_R=float(ev["realized_R"].mean()),
        ev_R_med=float(ev["realized_R"].median()),
        ev_doc=hit * float(RR) - (1.0 - hit) * 1.0,
        mfe_R=float(ev["mfe_R"].median()), mae_R=float(ev["mae_R"].median()),
        bars=float(ev["bars_held"].median()),
        risk_pct=float(ev["risk_pct"].median()),
        hit_struct=float(ev["hit_struct"].mean()) if "hit_struct" in ev else None,
        hold20=float(ev["hold20"].mean()) if "hold20" in ev else None,
        hold20_stop=float(ev["hold20_stop"].mean()) if "hold20_stop" in ev else None,
    )


def coverage_vs_v5(ev, px, **kw):
    """与 v5 年线闸门（MA250 + 20 日斜率，日频四象限）的**重合率** —— 共线检查。

    v5 是家族里已在跑的「上涨趋势」定义（MA250 日频），本变种用 MA200 月频。
    两者若高度重合，则 v11 不是新信息，只是在同一信号上换了个窗口 —— 这正是
    文档 §4.4 要求「查共线」的原因。返回 v5 上涨趋势在 v11 事件日的占比。
    """
    c = px.astype("float64")
    ma = c.rolling(250, min_periods=250).mean()
    up = (c > ma) & ((ma - ma.shift(20)) > 0)
    hit = up.reindex(pd.DatetimeIndex(ev["date"])).fillna(False)
    return dict(n=int(len(ev)), v5_up=int(hit.sum()),
                share=float(hit.mean()) if len(hit) else None)


# ---------------------------------------------------------------- 5. 自检
def _self_check():
    """七腿可读、趋势/支撑无前视性错误、事件数与结局分布可打印。"""
    print(f"{LABEL} · MA{MA_WIN}/{SLOPE_N} 日斜率 · 趋势口径 {TREND_FREQ} · "
          f"支撑 {SUP_MODE} · 带 {BAND:.0%} · 止损 {STOP_ATR}×ATR{ATR_N} · "
          f"目标 {RR:g}R · 冷却 {COOLDOWN} 日 · 跟踪 {HOLD_MAX} 日")
    for k in LEGS:
        px = load(k)
        ev, F = events(px, k)
        n_up = int(F["trend"].sum())
        n_dn = int(F["trend_dn"].sum())
        st = exit_stats(ev)
        print(f"{k:6s} {rule.LEGS[k]['name']:8s} {px.index[0].date()} ~ {px.index[-1].date()} "
              f"n={len(px):5d} 趋势日 {n_up / len(px):.0%}/{n_dn / len(px):.0%} "
              f"事件 {len(ev):3d} 笔"
              + (f" (候选 {int(ev['cand'].iloc[0])} 被冷却掉 {int(ev['cand'].iloc[0]) - len(ev)})"
                 if len(ev) else "")
              + (f" · 目标 {st['target']}/止损 {st['stop']}/到期 {st['timeout']}"
                 f" · 中位 risk {st['risk_pct']:.1%} · EV {st['ev_R']:+.2f}R"
                 if len(ev) else ""))


if __name__ == "__main__":
    _self_check()

