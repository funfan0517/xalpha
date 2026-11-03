# -*- coding: utf-8 -*-
"""波动资产 · V9 变种：下跌后四条件齐现的「抄底确认」——趋势反转 / 左侧风控。

来源：用户 2026-11-03 的抄底规则（地量见底 / 底背离 / 跌速放缓 / 成本线与风控）。
**它不预测底，只等确认**：四条件齐现、放量阳重新站回成本线，才允许试仓；
跌破「阶段最低点 ×(1−3%)」无条件离场；再创新低不修复同样离场。

## 0. 它是什么 / 不是什么

| 是 | 不是 |
|---|---|
| **确认型**入场（条件齐了才动手，不做「越跌越买」） | 预测底在哪（不估算底、不给目标价） |
| 单标的**状态机**（0/1 仓位 + 硬止损） | 自动买入信号（四条件齐现只是「允许试仓」） |
| **风控优先**：止损位在入场前就算好、且是常驻挂单 | 收益引擎（回测结论见 README §4，多数腿跑不过买入持有） |

## 1. 四条件（编码规则，逐条对应参数）

| # | 条件 | 口径 | 参数 |
|---|---|---|---|
| 1 | **地量见底 + 放量阳** | 最近 `dry_max_age`（90）日内出现过「成交量 = 近 `window`（60）日最低量」的地量日；**触发日当天**必须 `close>open` 且成交量 `>1.5×地量` 或 `>MAV5`（`vol_rule` 可切 both），且距地量日 `1 ≤ dry_age ≤ dry_max_age` | `window/vol_min_tol/vol_mult/vol_rule/mav_win/dry_max_age` |
| 2 | **底背离** | 价格创 `div_window` 日新低（`close ≤ 窗口最低 close`），而 MACD 的柱或 DIF（`hist=2×(DIF−DEA)`）**至少一个**没有同步创新低（`div_mode=either`；`window` 档=两者都不新低）。另有 `corr`（价格与 DIF 的窗口相关系数 <0）/ `slope`（价格窗口跌幅为负而 DIF 回升） | `div_window/div_mode/macd` |
| 3 | **跌速放缓** | `close` 站上「速度阻力扇 0.5 线」= 阶段 `(最低+最高)/2`；或 近 5 日跌幅 < 前 5 日跌幅 且 ATR 较 5 日前下降。`c3_mode=any` 默认「或」 | `fibo_frac/c3_mode/atr_win` |
| 4 | **站回成本线** | `close ≥ 锚定 VWAP`（锚 = **最近一次地量日**，`Σ(典型价×量)/Σ量`），且当日放量 `>1.5×地量`。`c4_mode` 可切更早的修复标尺（`or_ma20`/`ma20`/`both`） | `vol_mult/c4_mode` |
| 过滤 | **先过滤再找背离** | `MA20 < MA60`（趋势向下）且 距 `drop_win` 日收盘高点回撤 `≥ drop_min`（默认 250 日 / **10%**，2026-11-03 由 20% 放宽：多数底部反转发生在 −10%~−20% 之间） | `trend_filter/ma_fast/ma_slow/drop_win/drop_min` |

> **「同日或连续 3 日内集齐」怎么落成代码**：条件 1 与 4 是**触发日本身**（放量阳站上
> 成本线就是「入场那根 K 线」），必须同日；条件 2/3 是**结构背景**，给时间窗
> `ctx_recall`（默认 20 个交易日）。原因很硬：背离日通常是一根**阴线**，而放量阳在它之后
> 若干天、中间还隔着地量期 —— 实测 `ctx_recall=3` 时七腿十三年合计只有 1~2 次信号
> （A股宽基 0 次），那不是策略，是巧合。`ctx_recall=3` 的档位保留在敏感性表里。
>
> 反向也不能写成「`cond1` 曾经发生过就一直有效」（原始伪代码的 `cumsum()>=1`）：那样地量
> 过去 200 天后仍会发信号，等于没有窗口。`dry_max_age` 就是给它的上限。

## 2. 入场与出场

- **入场**：t 日收盘四条件齐 → **t+1 收盘**成交（回测口径，无前视）。实盘对应「t+1 15:00 前提交」。
- **止损**：`stage_low × (1−stop_pct)`，`stage_low` = 信号日的 `window` 日最低 `low`（入场前定好、
  入场后**冻结**）。这是**常驻挂单**：日内 `low ≤ stop` 即成交，跳空低开按**开盘价**成交
  （`fill = min(stop, open)`）—— 不假装止损永远精确。
- **再创新低**：`close < stage_low`（且未站回 VWAP）→ 次日收盘清仓，不看其他条件。
- **止盈**（`exit_mode`，回测里逐档对比，**默认 1:2 盈亏比**）：
  `rr2` 目标 = `entry + 2×(entry−stop)`；`batch` 1:1 卖一半、1:2 清尾；
  `fib618` 阶段低 + 0.618×(阶段高−阶段低)；`prior_high` 近 `ph_win` 日最高价；`none` 只靠止损与新低退出。
- **再入场**：止损后至少隔 `reentry_gap` 个交易日，且必须等**新的**四条件信号。

## 3. 仓位语义

`position ∈ [0, 1]` 是「本策略分配给该标的的额度里露出多少」，不是 pp。
单腿回测的基准是**买入持有同一标的**；组合层把腿按目标带归一化、空仓按货基
`rule.CASH_ANNUAL` 计息，基准是「静态带（恒满）+ 买入持有」。

**「试仓」= 按止损距离定量**（`size_mode="risk"`，默认）：止损锚是「阶段最低点」，
离入场价可以很远（实测 −10%~−25%），固定满仓会把单笔亏损放大到两位数
（科创50 满仓档回撤 −42.8%）。所以仓位 = `risk_per_trade / (1 − 止损/入场价)`
（下限 `size_floor`），把单笔亏损封在「该腿额度的 5%」附近。`size_mode="full"`
是朴素对照，回测里两档都跑。

「试仓」的另一半含义：**一笔进、不加仓**。加仓路径留给主策略的回撤阶梯
（`../rule.py::IndexLadderPolicy`）；两者叠加前先定谁的额度优先（README §5）。

## 4. 无前视三条（由 backtest.py 的断言强制）

1. `signal[t]` 只用 ≤t 的 OHLCV；`position[t]` 只由 `t−1` 及更早的决策改变（T+1 成交）。
2. 止损是**当日**的常驻挂单，成交价 `min(stop, open)`，可以比 `t−1` 收盘更差、不可能更好。
3. 指标未成形（`ready=False`）的日子一律不决策 —— 预热期完整空仓。
"""
import math
import os
import sys

import numpy as np
import pandas as pd

try:
    sys.stdout.reconfigure(encoding="utf-8")  # type: ignore[attr-defined]
except Exception:  # noqa: BLE001
    pass

_HERE = os.path.dirname(os.path.abspath(__file__))
_VL = os.path.dirname(_HERE)                                   # strategies/vol_legs
_ROOT = os.path.dirname(os.path.dirname(_VL))                  # 仓库根
for _p in (_HERE, _VL):
    if _p not in sys.path:
        sys.path.insert(0, _p)

import engine       # noqa: E402
import klines       # noqa: E402
import rule         # noqa: E402

LABEL = "V9 抄底确认（地量 + 背离 + 跌速放缓 + 站回成本线）"

# ---------------------------------------------------------------- 1. 参数（唯一权威源）
CFG = {
    # —— 条件 1：地量 + 放量阳 ——
    "window": 60,           # 地量 / 阶段最低点 / 速度阻力扇 的回看窗口
    "vol_min_tol": 0.0,     # 地量判定容差（0 = 严格等于窗口最小量）
    "vol_mult": 1.5,        # 放量阈值：地量的 1.5 倍
    "vol_rule": "any",      # any = 超过 1.5×地量 **或** 超过 MAV5；both = 两者都要
    "mav_win": 5,
    "dry_max_age": 90,      # 地量日到触发日的最长间隔（超过则地量失效，不追旧底）
                            # 2026-11-03 由 60 放宽到 90：长期缩量阴跌会让地量日「变老」，
                            # 60 日会把 2018-10、2024-02 那种「先地量、后拖两个月才放量」挡掉。
    # —— 条件 4 的「修复标尺」：默认 = 规则原文「站回地量日锚定的 VWAP」。 ——
    # 其他三档是**更早触发**的替代标尺（用于回答「为什么几次底部上涨没定位到」）：
    #   or_ma20 = 站回 VWAP **或** 站上 MA20（放量阳站上 20 日线也算修复完成）
    #   ma20    = 只要站上 MA20（完全不用 VWAP）
    #   both    = VWAP 与 MA20 都要站上（更严）
    # 实测（hs300，见 backtest 报告 §9）：口径放得越早，接住的底越多、胜率越低。
    "c4_mode": "vwap",
    # —— 条件 2：底背离 ——
    "div_window": 20,       # 价格创 20 日新低，而 MACD 不同时创新低
    "div_mode": "either",   # either（默认，柱或DIF）/ window（两者都要）/ corr / slope
    "macd": (12, 26, 9),
    # —— 四条件的时间窗 ——
    "ctx_recall": 20,       # 条件 2/3 允许在最近多少个交易日内出现过（见模块 docstring §1）
    # —— 研究开关：把某条强制置为「恒成立」，用于消融实验（默认全开，不参与交易语义）——
    "skip": (),             # 可含 "c1"/"c2"/"c3"/"c4"/"filt"
    # —— 条件 3：跌速放缓 ——
    "c3_mode": "any",       # any = 0.5 扇线 **或** 跌幅收敛；fibo / slope 可单开
    "fibo_frac": 0.5,       # 速度阻力扇 0.5 线 = 阶段 (最低 + 最高) / 2
    "atr_win": 14,
    "decel_win": 5,
    # —— 先过滤：趋势向下 + 前期跌幅 ——
    "trend_filter": True,
    "ma_fast": 20,
    "ma_slow": 60,
    "drop_win": 250,
    "drop_min": 0.10,       # 2026-11-03 由 20% 放宽到 10%：A股宽基近年多数底部反转发生在
                            # −10%~−20% 之间（2024-09 只 −15.3%），20% 会整段漏掉。
    # —— 风控 / 出场 ——
    "stop_pct": 0.03,       # 阶段最低点下方 3%
    # 「试仓」怎么定大小：止损锚是**阶段最低点**，离入场价可以很远（实测 −10%~−25%），
    # 固定满仓会把单笔风险放大到两位数。risk = 按「入场到止损的距离」反推仓位，
    # 使单笔亏损上限 ≈ risk_per_trade × 该腿额度；full = 一次性满额（朴素对照）。
    "size_mode": "risk",    # risk（默认）/ full
    "risk_per_trade": 0.05,  # risk 模式：单笔风险预算（占该腿额度）
    "size_floor": 0.10,     # 仓位下限（否则止损太宽时会缩到近乎不建仓）
    "exit_mode": "rr2",     # rr2（默认）/ batch / fib618 / prior_high / none
    "rr": 2.0,              # rr2 的盈亏比
    "batch_rr": (1.0, 2.0),  # batch：1:1 卖一半、1:2 清尾
    "tp_fib": 0.618,        # fib618 阻力位
    "ph_win": 60,           # prior_high 的前高窗口
    "reentry_gap": 5,       # 止损后最少隔几个交易日才允许再次试仓
    # 规则原文没有时间止损：kc50 2022-10 那笔横盘 226 个交易日才被「再创新低」带出场。
    # 0 = 不启用（保持规则原样），>0 = 持有满 N 个交易日强制离场（可选补丁）。
    "max_hold": 0,
    # —— 成本 / 现金 ——
    "fee": (0.0010, 0.0050), "slip": 0.0010,
}

EXIT_MODES = ("rr2", "batch", "fib618", "prior_high", "none")
EXIT_TEXT = {
    "rr2": "固定 1:2 盈亏比",
    "batch": "分批：1:1 减半、1:2 清尾",
    "fib618": "0.618 阻力（阶段低 + 0.618×区间）",
    "prior_high": "前高（近 ph_win 日最高价）",
    "none": "不设止盈（只靠止损 + 再创新低）",
}
CASH_ANNUAL = rule.CASH_ANNUAL
CASH_DAILY = (1.0 + CASH_ANNUAL) ** (1.0 / 252.0) - 1.0
LEGS = klines.LEGS
PORTFOLIO_LEGS = klines.PORTFOLIO_LEGS


def params(**kw):
    """参数打包（回测敏感性扫描用 `**{...}` 覆盖，不必改模块常量）。"""
    p = dict(CFG)
    p.update(kw)
    return p


def fee_of(slip=None):
    """-> (买入费率, 卖出费率)，含单边滑点。"""
    b, s = CFG["fee"]
    sp = CFG["slip"] if slip is None else float(slip)
    return float(b) + float(sp), float(s) + float(sp)


def weight_of(leg, legs=None):
    """组合层权重 = 该腿目标带 / 参与腿的带和（Σ=1）。"""
    legs = list(legs or PORTFOLIO_LEGS)
    return float(rule.LEGS[leg]["band"]) / float(sum(rule.LEGS[k]["band"] for k in legs))


# ---------------------------------------------------------------- 2. 指标
def macd(close, fast=12, slow=26, sig=9):
    """MACD(12,26,9)：DIF / DEA / 柱。

    柱按国内软件惯例取 `2×(DIF−DEA)`；倍率不影响任何符号判定，只影响绝对值大小。
    """
    dif = (close.ewm(span=fast, adjust=False).mean()
           - close.ewm(span=slow, adjust=False).mean())
    dea = dif.ewm(span=sig, adjust=False).mean()
    return dif, dea, 2.0 * (dif - dea)


def atr(df, n=14):
    """ATR（真实波幅的 n 日简单均值）。TR = max(H−L, |H−C₋₁|, |L−C₋₁|)。"""
    h, l, c = df["high"], df["low"], df["close"]
    pc = c.shift(1)
    tr = pd.concat([h - l, (h - pc).abs(), (l - pc).abs()], axis=1).max(axis=1)
    return tr.rolling(n, min_periods=n).mean()


def anchored_vwap(df, anchor_idx):
    """自锚点起算的 VWAP：`Σ(典型价×量)/Σ量`，锚点变化即重置。

    `anchor_idx[i]` = i 日为止**最近一次地量日**的下标（−1 = 还没有地量日）。
    """
    tp = (df["high"] + df["low"] + df["close"]) / 3.0
    pv = (tp * df["volume"]).to_numpy(dtype="float64")
    vv = df["volume"].to_numpy(dtype="float64")
    cpv = np.concatenate([[0.0], np.cumsum(np.nan_to_num(pv))])
    cv = np.concatenate([[0.0], np.cumsum(np.nan_to_num(vv))])
    n = len(df)
    a = np.asarray(anchor_idx, dtype=int)
    out = np.full(n, np.nan)
    ok = a >= 0
    pos = np.arange(n)
    den = cv[pos[ok] + 1] - cv[a[ok]]
    num = cpv[pos[ok] + 1] - cpv[a[ok]]
    out[ok] = np.where(den > 0, num / np.where(den > 0, den, 1.0), np.nan)
    return pd.Series(out, index=df.index)


def indicators(df, **kw):
    """OHLCV -> 指标 + 四条件（全部只用当日及以前的数据）。"""
    p = params(**kw)
    o, h, l, c, v = (df[k].astype("float64") for k in
                     ("open", "high", "low", "close", "volume"))
    w = int(p["window"])
    out = pd.DataFrame(index=df.index)
    out[["open", "high", "low", "close", "volume"]] = df[["open", "high", "low", "close",
                                                          "volume"]]
    out["lowest"] = l.rolling(w, min_periods=w).min()
    out["highest"] = h.rolling(w, min_periods=w).max()
    out["vol_min"] = v.rolling(w, min_periods=w).min()
    out["mav5"] = v.rolling(int(p["mav_win"]), min_periods=int(p["mav_win"])).mean()

    dif, dea, hist = macd(c, *p["macd"])
    out["dif"], out["dea"], out["hist"] = dif, dea, hist

    out["ma_f"] = c.rolling(int(p["ma_fast"]), min_periods=int(p["ma_fast"])).mean()
    out["ma_s"] = c.rolling(int(p["ma_slow"]), min_periods=int(p["ma_slow"])).mean()
    out["drop"] = 1.0 - c / c.rolling(int(p["drop_win"]),
                                      min_periods=int(p["drop_win"])).max()
    out["atr"] = atr(df, int(p["atr_win"]))
    dw = int(p["decel_win"])
    out["ret5"] = c / c.shift(dw) - 1.0
    out["ret5_prev"] = c.shift(dw) / c.shift(2 * dw) - 1.0

    # —— 地量：当日成交量 = 近 window 日最低量 ——
    out["dry"] = (v <= out["vol_min"] * (1.0 + float(p["vol_min_tol"]))).fillna(False)
    idx = np.arange(len(out))
    last = np.where(out["dry"].to_numpy(dtype=bool), idx, -1)
    last = pd.Series(last, index=out.index).cummax().to_numpy(dtype=int)
    out["dry_idx"] = last
    out["dry_age"] = np.where(last >= 0, idx - last, np.nan)
    varr = v.to_numpy(dtype="float64")
    out["dry_vol"] = np.where(last >= 0, varr[np.clip(last, 0, len(out) - 1)], np.nan)
    out["vwap"] = anchored_vwap(df, last)
    out["fib"] = out["lowest"] + float(p["fibo_frac"]) * (out["highest"] - out["lowest"])

    # —— 条件 1：地量后的放量阳（必须在**触发日当天**）——
    strong = v > float(p["vol_mult"]) * out["dry_vol"]
    vol_up = (strong | (v > out["mav5"])) if p["vol_rule"] == "any" else (strong & (v > out["mav5"]))
    yang = c > o
    c1 = (yang & vol_up.fillna(False)
          & (out["dry_age"] >= 1) & (out["dry_age"] <= int(p["dry_max_age"]))).fillna(False)
    out["c1"] = c1

    # —— 条件 2：底背离 ——
    dw2 = int(p["div_window"])
    new_low = c <= c.rolling(dw2, min_periods=dw2).min()
    dif_low = dif.rolling(dw2, min_periods=dw2).min()
    hist_low = hist.rolling(dw2, min_periods=dw2).min()
    if p["div_mode"] == "corr":
        corr = c.rolling(dw2, min_periods=dw2).corr(dif)
        near_low = c <= c.rolling(dw2, min_periods=dw2).min() * (1.0 + 0.02)
        c2 = (corr < 0) & near_low
        out["div_corr"] = corr
    elif p["div_mode"] == "slope":
        px_slope = c / c.shift(dw2 - 1) - 1.0
        macd_slope = dif - dif.shift(dw2 - 1)
        c2 = (px_slope < 0) & (macd_slope > 0)
        out["px_slope"] = px_slope
    elif p["div_mode"] == "window":
        # 更严：DIF 与柱**都**不在窗口最低
        c2 = new_low & (dif > dif_low) & (hist > hist_low)
    else:
        # either（默认）：规则原文「MACD 柱/DIFF 不创新低」= 至少一个不创新低
        c2 = new_low & ((dif > dif_low) | (hist > hist_low))
    out["c2"] = c2.fillna(False)

    # —— 条件 3：跌速放缓 ——
    c3_fibo = (c > out["fib"]).fillna(False)
    c3_slope = ((out["ret5"] > out["ret5_prev"])
                & (out["atr"] < out["atr"].shift(dw))).fillna(False)
    mode = p["c3_mode"]
    c3 = {"fibo": c3_fibo, "slope": c3_slope}.get(mode, c3_fibo | c3_slope)
    out["c3_fibo"], out["c3_slope"], out["c3"] = c3_fibo, c3_slope, c3

    # —— 条件 4：站回成本线（锚定 VWAP + 放量，必须在**触发日当天**）——
    above_vwap = (c >= out["vwap"]).fillna(False)
    above_ma = (c >= out["ma_f"]).fillna(False)
    repair = {"vwap": above_vwap,
              "or_ma20": above_vwap | above_ma,
              "ma20": above_ma,
              "both": above_vwap & above_ma}[p["c4_mode"]]
    out["c4"] = (repair & strong.fillna(False)).fillna(False)

    # —— 先过滤 ——
    trend_down = (out["ma_f"] < out["ma_s"]).fillna(False) if p["trend_filter"] \
        else pd.Series(True, index=out.index)
    drop_ok = (out["drop"] >= float(p["drop_min"])).fillna(False)
    out["trend_down"], out["drop_ok"] = trend_down, drop_ok
    out["filt"] = (trend_down & drop_ok).fillna(False)

    # —— 消融开关：把某条强制置为「恒成立」（只给研究用，默认不启用）——
    for k in set(p.get("skip") or ()):
        out[k] = pd.Series(True, index=out.index)

    # —— 条件 2/3 的时间窗：允许在最近 ctx_recall 个交易日内出现过 ——
    # 「四条件连续 3 日内集齐」在真实数据上几乎不可能：背离日通常是一根**阴线**、
    # 而放量阳是它之后若干天的事，中间还隔着地量期（实测 3 日窗口下七腿仅 1~2 次）。
    # 故 2/3 给结构化时间窗 ctx_recall（默认 20），1/4 仍要求与触发日同日。
    rec = int(p["ctx_recall"])
    for k in ("c1", "c2", "c3", "c4"):
        out[f"{k}_recent"] = (out[k].astype(int).rolling(rec, min_periods=1)
                              .max().fillna(0).astype(bool))

    out["ready"] = (out["lowest"].notna() & out["vol_min"].notna() & out["mav5"].notna()
                    & out["ma_f"].notna() & out["ma_s"].notna() & out["drop"].notna()
                    & out["atr"].notna() & out["dif"].notna())
    out["signal"] = (out["ready"] & out["filt"] & out["c1"] & out["c4"]
                     & out["c2_recent"] & out["c3_recent"])
    return out


def signal(df, **kw):
    """对外的最小接口（与用户给的伪代码骨架同形）：-> (entry, stop)。

    `entry` 是「t 日收盘确认、t+1 收盘试仓」的布尔序列；`stop` 是**当时**的止损位
    （阶段最低点 ×(1−stop_pct)），只在 `entry` 为 True 的日子有意义。
    """
    I = indicators(df, **kw)
    return I["signal"], I["lowest"] * (1.0 - float(params(**kw)["stop_pct"]))


# ---------------------------------------------------------------- 3. 状态机
def _targets(entry, stop, I, t, p):
    """-> (第一目标, 第二目标)。无固定止盈时都给 +inf。"""
    mode = p["exit_mode"]
    if mode == "rr2":
        return entry + float(p["rr"]) * (entry - stop), math.inf
    if mode == "batch":
        r1, r2 = p["batch_rr"]
        return entry + float(r1) * (entry - stop), entry + float(r2) * (entry - stop)
    if mode == "fib618":
        lo = float(I["lowest"].iloc[t])
        hi = float(I["highest"].iloc[t])
        return lo + float(p["tp_fib"]) * (hi - lo), math.inf
    if mode == "prior_high":
        return float(I["high"].iloc[max(0, t - int(p["ph_win"]) + 1):t + 1].max()), math.inf
    return math.inf, math.inf


def state_machine(df, **kw):
    """-> DataFrame。核心列：

    - `signal`  : t 日收盘四条件齐现（不含成交）
    - `position`: t 日收盘成交后的仓位（0 / 0.5 / 1）
    - `decide`  : t 日收盘做出、t+1 日成交的决策（buy / sell / ""）
    - `done`    : t 日成交后的说明（买单/止损/止盈/新低）
    - `stop`    : 持仓期的止损位（入场时冻结）；空仓为 NaN
    - `stop_hit`/`stop_fill`: 当日是否被止损、成交价（`min(stop, open)`）
    - `exit_reason`: 当日离场原因（空串 = 未离场）
    """
    p = params(**kw)
    I = indicators(df, **kw)
    n = len(I)
    c = I["close"].to_numpy(dtype="float64")
    o = I["open"].to_numpy(dtype="float64")
    l = I["low"].to_numpy(dtype="float64")
    sig = I["signal"].to_numpy(dtype=bool)
    ready = I["ready"].to_numpy(dtype=bool)
    lowest = I["lowest"].to_numpy(dtype="float64")

    pos = np.zeros(n)
    decide = np.empty(n, dtype=object)
    decide[:] = ""
    done = np.empty(n, dtype=object)
    done[:] = ""
    reason = np.empty(n, dtype=object)
    reason[:] = ""
    stop_v = np.full(n, np.nan)
    tgt_v = np.full(n, np.nan)
    hit = np.zeros(n, dtype=bool)
    fill = np.full(n, np.nan)

    hold, entry_px, stop, stage, tgt = 0.0, math.nan, math.nan, math.nan, (math.inf, math.inf)
    entry_idx, half_done, block = -1, False, -1
    pend = None

    for t in range(n):
        # 1) 执行 t−1 的决策（T+1 收盘成交）
        if pend is not None:
            if pend["side"] == "buy":
                entry_idx, half_done = t, False
                entry_px = float(c[t])
                stage = pend["stage"]
                stop = stage * (1.0 - float(p["stop_pct"]))
                if p["size_mode"] == "risk":
                    # 单笔风险预算 / 入场到止损的距离 —— 止损越远，仓位越小
                    dist = (entry_px - stop) / entry_px
                    size = 1.0 if dist <= 0 else min(1.0, float(p["risk_per_trade"]) / dist)
                    hold = max(float(p["size_floor"]), size)
                else:
                    hold = 1.0
                tgt = _targets(entry_px, stop, I, t, p)
                done[t] = f"试仓 {hold:.0%}（四条件齐现）@ {entry_px:.4f} 止损 {stop:.4f}"
                reason[t] = "建仓"
            else:
                sold = min(hold, float(pend["frac"]))
                hold = max(hold - sold, 0.0)
                done[t] = f"{pend['why']} @ {c[t]:.4f}"
                reason[t] = pend["why"]
                if hold <= 1e-9:
                    hold, entry_px, stage = 0.0, math.nan, math.nan
                    stop, tgt, half_done = math.nan, (math.inf, math.inf), False
            pend = None

        # 2) 常驻止损（当日日内成交，跳空按开盘价）
        if hold > 0 and t > entry_idx and not math.isnan(stop) and l[t] <= stop:
            f = min(stop, float(o[t]))
            hit[t], fill[t] = True, f
            stop_v[t] = stop          # 记当日**生效**的止损位（清仓后 stop 变量置空）
            hold, entry_px, stage = 0.0, math.nan, math.nan
            stop, tgt, half_done = math.nan, (math.inf, math.inf), False
            done[t] = f"止损（low {l[t]:.4f} ≤ {f:.4f}）"
            reason[t] = f"止损(最低点−{float(p['stop_pct']):.0%})"
            block = t + int(p["reentry_gap"])
            pos[t] = 0.0
            continue

        # 3) 收盘决策：止盈 / 再创新低 / 到期 / 建仓
        if hold > 0 and t > entry_idx:
            ct = float(c[t])
            if ct < stage:                                    # 再创新低且未修复
                pend = dict(side="sell", frac=hold, why="再创新低未修复，无条件退出")
                decide[t] = "sell"
            elif int(p["max_hold"]) > 0 and (t - entry_idx) >= int(p["max_hold"]):
                pend = dict(side="sell", frac=hold, why="到期退出（时间止损）")
                decide[t] = "sell"
            elif p["exit_mode"] == "batch":
                if (not half_done) and ct >= tgt[0]:
                    pend = dict(side="sell", frac=hold / 2.0, why="分批止盈 1:1 减半")
                    decide[t] = "sell"
                    half_done = True
                elif half_done and ct >= tgt[1]:
                    pend = dict(side="sell", frac=hold, why="分批止盈 1:2 清尾")
                    decide[t] = "sell"
            elif math.isfinite(tgt[0]) and ct >= tgt[0]:
                pend = dict(side="sell", frac=hold, why=f"止盈（{EXIT_TEXT[p['exit_mode']]}）")
                decide[t] = "sell"
        elif hold <= 0 and ready[t] and sig[t] and t >= block:
            pend = dict(side="buy", stage=float(lowest[t]))
            decide[t] = "buy"
            done[t] = ""

        pos[t] = hold
        stop_v[t] = stop
        tgt_v[t] = tgt[0] if math.isfinite(tgt[0]) else np.nan

    out = pd.DataFrame(dict(signal=I["signal"], ready=I["ready"], position=pos,
                            decide=decide, done=done, exit_reason=reason,
                            stop=stop_v, target=tgt_v, stop_hit=hit,
                            stop_fill=fill), index=df.index)
    keep = [k for k in I.columns if k not in out.columns]
    return out.join(I[keep])


# ---------------------------------------------------------------- 4. 回测
def run_leg(df, sm, **kw):
    """单腿净值（起点 1.0）。

    日收益 = `position[t−1] × 标的日收益 + (1−position[t−1]) × 货基日息`；
    止损日在 `stop_fill` 成交（可以差于前收、不可能更好）；仓位变化当日扣双边费率。
    """
    p = params(**kw)
    fb, fs = fee_of()
    c = df["close"].to_numpy(dtype="float64")
    pos = sm["position"].to_numpy(dtype="float64")
    hit = sm["stop_hit"].to_numpy(dtype=bool)
    fill = sm["stop_fill"].to_numpy(dtype="float64")
    n = len(df)
    nav = np.ones(n)
    for t in range(1, n):
        w = pos[t - 1]
        if w > 0 and hit[t]:
            pr = fill[t] / c[t - 1] - 1.0
        elif w > 0:
            pr = c[t] / c[t - 1] - 1.0
        else:
            pr = 0.0
        r = w * pr + (1.0 - w) * CASH_DAILY
        d = pos[t] - pos[t - 1]
        r -= fb * max(d, 0.0) + fs * max(-d, 0.0)
        nav[t] = nav[t - 1] * (1.0 + r)
    return pd.Series(nav, index=df.index, name="nav")


def perf(nav):
    """绩效（复用主策略 engine.metrics，无风险利率 = 货基 CASH_ANNUAL）。"""
    return engine.metrics(nav)


def trades(df, sm):
    """按仓位变化折出成交 -> [{date, side, price, frac, reason, ...}]，并配对成轮次。"""
    c = df["close"]
    pos = sm["position"]
    hit = sm["stop_hit"]
    fill = sm["stop_fill"]
    where = {d: i for i, d in enumerate(df.index)}
    out, open_trade = [], None
    prev = 0.0
    for t, d in enumerate(df.index):
        cur = float(pos.iloc[t])
        if cur > prev + 1e-9:
            px = float(c.iloc[t])
            # stage = 决策日（t−1）的 window 日最低 low，也就是止损锚的基准
            stage = float(sm["lowest"].iloc[max(0, t - 1)])
            out.append(dict(date=d, side="buy", price=px, frac=cur - prev, stage=stage,
                            reason="四条件齐现"))
            if open_trade is None:
                open_trade = dict(entry=d, entry_px=px)
        elif cur < prev - 1e-9:
            px = float(fill.iloc[t]) if bool(hit.iloc[t]) else float(c.iloc[t])
            out.append(dict(date=d, side="sell", price=px, frac=prev - cur,
                            reason=str(sm["exit_reason"].iloc[t]) or "离场"))
            if open_trade is not None and cur <= 1e-9:
                open_trade.update(exit=d, exit_px=px, days=(d - open_trade["entry"]).days,
                                  bars=t - where[open_trade["entry"]],
                                  pnl=px / open_trade["entry_px"] - 1.0,
                                  why=str(sm["exit_reason"].iloc[t]))
                out[-1].update(round=dict(open_trade))
                open_trade = None
        prev = cur
    return out


def round_stats(tr):
    """轮次统计：笔数 / 胜率 / 平均盈亏 / 盈亏比 / 平均持仓 / 止损占比。"""
    rs = [x["round"] for x in tr if x.get("round")]
    if not rs:
        return dict(n=0, win=None, avg=None, med=None, payoff=None, days=None,
                    stop_share=None, best=None, worst=None)
    pnl = pd.Series([r["pnl"] for r in rs], dtype="float64")
    win = pnl[pnl > 0]
    loss = pnl[pnl <= 0]
    stops = sum(1 for r in rs if str(r.get("why", "")).startswith("止损"))
    return dict(n=len(rs), win=float((pnl > 0).mean()), avg=float(pnl.mean()),
                med=float(pnl.median()),
                payoff=(float(win.mean() / abs(loss.mean()))
                        if len(win) and len(loss) else None),
                days=float(pd.Series([r["bars"] for r in rs]).median()),
                stop_share=float(stops / len(rs)),
                best=float(pnl.max()), worst=float(pnl.min()))


def portfolio(frame, positions, legs=None, fee=True, slip=None):
    """组合层净值：Σ w·[pos·标的日收益 + (1−pos)·货基] − 调仓成本（Σ=1 权重）。"""
    legs = list(legs or [k for k in frame.columns])
    w = {k: weight_of(k, legs) for k in legs}
    ret = frame.pct_change().fillna(0.0)
    fb, fs = fee_of(slip)
    # 先把各腿仓位对齐到共同日历（每个标的只在自己的交易日有信号，其余前向填充）
    P = {k: positions[k].reindex(frame.index).ffill().fillna(0.0).to_numpy(dtype="float64")
         for k in legs}
    R = {k: ret[k].to_numpy(dtype="float64") for k in legs}
    nav = 1.0
    out, inv = [], []
    prev = {k: 0.0 for k in legs}
    for i, _d in enumerate(frame.index):
        if i == 0:
            out.append(nav)
            inv.append(0.0)
            continue
        r, cost, expo = 0.0, 0.0, 0.0
        for k in legs:
            p_prev, p_now = P[k][i - 1], P[k][i]
            expo += w[k] * p_prev
            r += w[k] * (p_prev * R[k][i] + (1.0 - p_prev) * CASH_DAILY)
            if fee:
                d = p_now - prev[k]
                cost += w[k] * (fb * max(d, 0.0) + fs * max(-d, 0.0))
            prev[k] = p_now
        r -= cost
        nav *= (1.0 + r)
        out.append(nav)
        inv.append(expo)
    return pd.Series(out, index=frame.index, name="nav"), float(np.mean(inv))


# ---------------------------------------------------------------- 5. 读数（每日报告用）
MISS_TEXT = {
    "filt": "前置过滤（MA20<MA60 且 回撤≥阈值）",
    "c1": "放量阳线（地量之后）",
    "c2_recent": "底背离（价格新低 / MACD 不新低）",
    "c3_recent": "跌速放缓（0.5 扇线 或 跌幅收敛）",
    "c4": "站回成本线（≥ 锚定 VWAP 且放量）",
}


def _why(r, p):
    """每条未通过的条件给一句人话原因（「差在哪儿」比「❌」有用得多）。"""
    why = {}
    nan = lambda x: x != x  # noqa: E731
    if not bool(r["trend_down"]):
        why["filt"] = "MA20 未下穿 MA60"
    elif not bool(r["drop_ok"]):
        why["filt"] = f"回撤 {float(r['drop']):.1%} < 门槛 {float(p['drop_min']):.0%}"
    if nan(r["dry_age"]):
        why["c1"] = "还没有地量日"
    elif int(r["dry_age"]) > int(p["dry_max_age"]):
        why["c1"] = f"地量已 {int(r['dry_age'])} 日 > 上限 {int(p['dry_max_age'])}"
    elif not bool(float(r["close"]) > float(r["open"])):
        why["c1"] = "今日非阳线"
    elif not bool(r["c1"]):
        why["c1"] = f"量能不足，需 >{float(p['vol_mult'])}×地量 或 >MAV{int(p['mav_win'])}"
    if not bool(r["c2_recent"]):
        why["c2_recent"] = f"近 {int(p['ctx_recall'])} 日无背离"
    if not bool(r["c3_recent"]):
        why["c3_recent"] = f"近 {int(p['ctx_recall'])} 日无跌速放缓"
    if not bool(r["c4"]):
        vw = float(r["vwap"])
        why["c4"] = ("VWAP 未成形" if nan(vw) else
                     (f"低于 VWAP {float(r['close']) / vw - 1.0:+.1%}"
                      if float(r["close"]) < vw else "放量不足"))
    return why


def diagnose(leg, **kw):
    """单标的当前读数 -> dict（四条件逐条给「过/不过」+ 距阈值还有多远）。"""
    df = klines.frame(leg)
    sm = state_machine(df, **kw)
    I = indicators(df, **kw)
    p = params(**kw)
    r, i = sm.iloc[-1], I.iloc[-1]
    why = _why(r, p)
    miss = [MISS_TEXT[k] for k in ("filt", "c1", "c2_recent", "c3_recent", "c4")
            if not bool(r[k])]
    gap = {}
    if not math.isnan(float(r["vwap"])):
        gap["vwap"] = float(r["close"]) / float(r["vwap"]) - 1.0
    if not math.isnan(float(r["fib"])):
        gap["fib"] = float(r["close"]) / float(r["fib"]) - 1.0
    gap["vol_mult"] = (float(r["volume"]) / float(r["dry_vol"])
                       if r["dry_vol"] == r["dry_vol"] and float(r["dry_vol"]) > 0 else None)
    gap["drop"] = float(r["drop"]) if r["drop"] == r["drop"] else None
    return dict(
        leg=leg, name=klines.UNIVERSE[leg]["name"], code=klines.UNIVERSE[leg]["code"],
        date=df.index[-1], close=float(r["close"]), position=float(r["position"]),
        stop=(None if r["stop"] != r["stop"] else float(r["stop"])),
        target=(None if r["target"] != r["target"] else float(r["target"])),
        vwap=(None if r["vwap"] != r["vwap"] else float(r["vwap"])),
        fib=(None if r["fib"] != r["fib"] else float(r["fib"])),
        lowest=(None if r["lowest"] != r["lowest"] else float(r["lowest"])),
        dry_age=(None if r["dry_age"] != r["dry_age"] else int(r["dry_age"])),
        conds={k: bool(r[k]) for k in ("filt", "c1", "c2_recent", "c3_recent", "c4")},
        signal=bool(r["signal"]), decide=str(r["decide"]), done=str(r["done"]),
        miss=miss, why=why, gap=gap, rule=p, leg_band=float(rule.LEGS[leg]["band"]),
    )


def _self_check():
    print(f"{LABEL} · 自检")
    print(f"参数: 窗口 {CFG['window']} 日 · 放量 {CFG['vol_mult']}× 地量({CFG['vol_rule']}) · "
          f"背离 {CFG['div_window']} 日({CFG['div_mode']}) · 跌速 {CFG['c3_mode']} · "
          f"止损 −{CFG['stop_pct']:.0%} · 止盈 {CFG['exit_mode']}")
    print(f"过滤: MA{CFG['ma_fast']}<MA{CFG['ma_slow']} · 距 {CFG['drop_win']} 日高点 "
          f"≥{CFG['drop_min']:.0%} · 成本: 申 {CFG['fee'][0]:.2%}/赎 {CFG['fee'][1]:.2%}"
          f" + 滑点 {CFG['slip']:.2%}")
    for leg in LEGS:
        d = diagnose(leg)
        tag = "在仓" if d["position"] > 0 else ("**四条件齐现**" if d["signal"] else "空仓")
        lack = "；".join(d["why"].values())
        print(f"  {d['name']:14s} {d['date']:%Y-%m-%d} close {d['close']:.3f} "
              f"仓位 {d['position']:.1%} · {tag}" + (f" · 缺: {lack}" if lack else ""))


if __name__ == "__main__":
    _self_check()
