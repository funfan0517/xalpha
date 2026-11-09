# -*- coding: utf-8 -*-
"""波动资产 · V1 量价家族 · **子策略 V1-2：20/60/250 量价趋势跟随** —— 规则与回测引擎。

规则来源：一条「20/60/250 均线 + 量能 + ATR/ADX」的中长期趋势跟踪体系（用户给的原始描述，
逐条映射见 README §1）。核心一句话：**靠年线（250）定牛熊、60 日线控生死、20 日线找波段，
用扣抵预判拐头、靠 ATR/ADX 过滤震荡、以小止损换大盈亏比；不预测，只跟随。**

## 与 V1-1 的关系（家族口径）
- V1-1（`../v1_1_ma20`）= **纯价基线**：只问「净值在不在 MA20 上方」，不看量、不设止损；
- V1-2（本文件）= **量价**：价（三线 + 扣抵 + ATR/ADX）+ 量（放量突破 / 缩量回踩），
  仓位由风险预算算出（不是 0/1 全仓），止损与移动保护写死在状态机里。
- 两者**信号口径不同源**：V1-1 用场外复权净值，V1-2 必须用场内 ETF（只有场内有成交量）。
  同一个标的、两种口径，收益不可直接相减，只能各自与「同期买入持有」比。

## 规则 -> 参数（改规则只改下面的常量）
| 原始描述 | 本实现 |
|---|---|
| 20/60/250 三条均线 | `SMA_FAST/MID/SLOW = 20/60/250` |
| 多头环境：股价在年线上方、年线走平或上倾 | `close > MA250` 且 `up250`（扣抵见下） |
| 均线扣抵：比较最新价与 N 天前被扣掉的价格 | `up250 = close > close.shift(250)` —— 数学上**等价于** `MA250` 今日上行（`MA_t−MA_{t−1} = (P_t−P_{t−N})/N`），无近似 |
| 放量上穿 60 日线 + 20 日线金叉 60 日线 | 建仓型 ①：`MA20 > MA60` 且 上穿 MA60 且 `close ≥ MA60 + max(1ATR, 2%)` |
| 缩量回踩 60/20 线托举后放量中阳站回 20 线 | 建仓型 ②：近 10 日触及 20/60 线（≤ +0.5%）且 今日收复 MA20 且 涨 ≥ 1.5% |
| 三线粘合后放量长阳脱离 | 建仓型 ③：近 10 日三线极差 ≤ 2.5%×close，今日站上三线最高且涨 ≥ 2% |
| 量能 ≥ 20 日均量 1.5 倍 | `rvol = volume / MAV20 ≥ VOL_MULT(1.5)`（三种建仓共用） |
| ADX > 25 趋势激活、ADX < 20 休眠 | 突破型建仓要 `ADX ≥ ADX_ON(25)`；回踩型只要 `ADX ≥ ADX_OFF(20)`；`ADX < 20` 一律不开新仓 |
| 初始止损：回踩低点下方 / 有效跌破 60 线 3%~5% | `stop = min(close − 1.5ATR, 近 10 日最低)`，止损幅度夹在 `[2%, 10%]` |
| 移动保护：沿 20 日线持有 | 跌破 MA20 减半；站回 MA20 恢复；另加 `peak − 3ATR` 移动止损（只上移） |
| 清仓：放量击穿 60 线 / 有效跌破 60 线 / 年线拐头向下 | 三种清仓条件写死在状态机里（见 `simulate`） |
| 单笔最大风险 ≤ 2%、仓位 = 风险 ÷ 止损幅度 | `size = min(1, RISK_PCT(2%) / stop_pct)` —— 波动大的腿自动买得少 |
| 双边佣金 + 印花税 + 滑点 | 场内 ETF **免印花税**；`COST_ONE_WAY = 7.5bp`（佣金万 2.5 + 滑点 5bp） |

## 执行纪律（与家族一致，无前视）
T 日收盘算信号 -> **T+1 收盘成交**（`pos = w.shift(1)`）。场内只有日线收盘价可用（本地无开盘价），
所以按次日收盘而非次日开盘撮合；止损同样按「收盘触发、次日生效」处理，比日内触发**保守**。

机械规则输出，非投资建议。
"""
import math
import os
import sys

import numpy as np
import pandas as pd

_HERE = os.path.dirname(os.path.abspath(__file__))            # .../v1_2_ma_trend_pv
_FAMILY = os.path.dirname(_HERE)                              # .../v1_price_volume
_VL = os.path.dirname(_FAMILY)                                # strategies/vol_legs
_ROOT = os.path.dirname(os.path.dirname(_VL))                 # 仓库根

for _p in (_HERE, _VL):
    if _p not in sys.path:
        sys.path.insert(0, _p)

import engine       # noqa: E402
import rule         # noqa: E402
import ohlcv        # noqa: E402

# ---------------------------------------------------------------- 1. 参数（规则权威源）
SMA_FAST, SMA_MID, SMA_SLOW = 20, 60, 250
ATR_N = 14
ADX_N = 14
VOL_N = 20

VOL_MULT = 1.5          # 建仓量能门槛：≥ 20 日均量 1.5 倍
VOL_CONFIRM_N = 3       # 放量确认窗口：近 3 日内出现过 ≥1.5 倍量比即算
EXIT_VOL_MULT = 1.5     # 「放量」击穿 60 线的判定
BRK_ATR = 1.0           # 突破 MA60 需 ≥ MA60 + 1 ATR
BRK_PCT = 0.02          # ……或 ≥ MA60 + 2%
ENTRY_YANG = 0.01       # 回踩后「中阳」：单日涨幅 ≥ 1%
GOLDEN_TOL = 0.01       # 突破型只要求 20 线在 60 线的 −1% 以内（金叉/即将金叉）
CR20_WIN = 3            # 「上穿 20 线」的有效窗口（放量常在站回后 1~2 日才出现）
CR60_WIN = 5            # 「上穿 60 线」的有效窗口
BELOW20_N = 10          # 回踩判定：近 10 日内曾收盘跌破 20 线
SQUEEZE_N = 10          # 三线粘合的观察窗口
SQUEEZE_PCT = 0.025     # 三线极差 ≤ 2.5%×close 视为粘合
SQUEEZE_YANG = 0.02     # 脱离粘合的「长阳」≥ 2%
TOUCH_PCT = 0.01        # 回踩判定：近 10 日最低价曾进入 20 线上方 1% 以内
PULLBACK_N = 10         # 回踩 / 初始止损用的回看窗口
BREAK_MA60_PCT = 0.02   # 「有效跌破」60 线：低于 2%
COOLDOWN = 5            # 平仓后的冷却交易日（避免止损/再入场来回打）

ADX_ON, ADX_OFF = 25.0, 20.0
RISK_PCT = 0.02         # 单笔最大风险（占该腿额度）
STOP_ATR = 1.5          # 初始止损 = 1.5 ATR（与回踩低点取更低者）
STOP_BUFFER = 0.005     # 回踩低点下方再留 0.5% 缓冲（避免贴着低点被扫）
TRAIL_ATR = 6.0         # 移动止损 = 峰值 − 6 ATR（只上移；取宽是为了「让利润奔跑」）
TRAIL_ARM = 4.0         # 浮盈达到 4 ATR 之后移动止损才接管（之前用结构性止损）
MIN_STOP, MAX_STOP = 0.03, 0.08   # 止损幅度夹在 3%~8%（原始描述：初始止损 3%~5%）
MAX_POS = 1.0           # 单腿上限 = 该腿额度
HALF = 0.5              # 跌破 MA20 减到原仓位的一半
THREAT_GAP = 0.02       # ……但只在「价距 60 线 ≤2%」时才算威胁（否则视为正常回踩，不动仓位）
COST_ONE_WAY = 0.00075  # 场内单边摩擦：佣金万 2.5 + 滑点 5bp（ETF 免印花税）

LABEL = "V1-2 量价趋势跟随"
LEGS = rule.LEG_ORDER
W = {k: float(rule.LEGS[k]["band"]) / float(rule.SLEEVE_PP) for k in LEGS}
CASH_ANNUAL = rule.CASH_ANNUAL
CASH_DAILY = (1.0 + CASH_ANNUAL) ** (1.0 / 252) - 1.0

COLS = ("ma20", "ma60", "ma250", "atr", "adx", "rvol", "up250", "regime")


# ---------------------------------------------------------------- 2. 指标
def _atr(high, low, close, n=ATR_N):
    """Wilder ATR（真实波幅的指数平滑；有 H/L 就用真 ATR，不做收盘价近似）。"""
    pc = close.shift(1)
    tr = pd.concat([(high - low).abs(), (high - pc).abs(), (low - pc).abs()], axis=1).max(axis=1)
    return tr.ewm(alpha=1.0 / n, adjust=False).mean()


def _adx(high, low, close, n=ADX_N):
    """Wilder ADX -> (adx, +DI, -DI)。"""
    up, dn = high.diff(), -low.diff()
    pdm = up.where((up > dn) & (up > 0), 0.0)
    mdm = dn.where((dn > up) & (dn > 0), 0.0)
    pc = close.shift(1)
    tr = pd.concat([(high - low).abs(), (high - pc).abs(), (low - pc).abs()], axis=1).max(axis=1)
    atr = tr.ewm(alpha=1.0 / n, adjust=False).mean()
    pdi = 100.0 * pdm.ewm(alpha=1.0 / n, adjust=False).mean() / atr
    mdi = 100.0 * mdm.ewm(alpha=1.0 / n, adjust=False).mean() / atr
    den = (pdi + mdi).replace(0.0, np.nan)
    dx = 100.0 * (pdi - mdi).abs() / den
    return dx.ewm(alpha=1.0 / n, adjust=False).mean(), pdi, mdi


def prepare(df):
    """OHLCV -> 指标面板（均线 / ATR / ADX / 量比 / 扣抵 / 市况）。"""
    out = df.copy()
    c = out["close"]
    out["ma20"] = c.rolling(SMA_FAST).mean()
    out["ma60"] = c.rolling(SMA_MID).mean()
    out["ma250"] = c.rolling(SMA_SLOW).mean()
    out["atr"] = _atr(out["high"], out["low"], c)
    out["adx"], out["pdi"], out["mdi"] = _adx(out["high"], out["low"], c)
    out["mav"] = out["volume"].rolling(VOL_N).mean()
    out["rvol"] = out["volume"] / out["mav"]
    # 扣抵：MA_N 今日上行 ⟺ 今日收盘 > N 日前的收盘（严格等价，不是近似）
    out["d20"], out["d60"], out["d250"] = c.shift(SMA_FAST), c.shift(SMA_MID), c.shift(SMA_SLOW)
    out["up20"], out["up60"], out["up250"] = c > out["d20"], c > out["d60"], c > out["d250"]
    out["atr_pct"] = out["atr"] / c
    three = out[["ma20", "ma60", "ma250"]]
    out["spread"] = (three.max(axis=1) - three.min(axis=1)) / c
    out["squeeze"] = (out["spread"] <= SQUEEZE_PCT).rolling(SQUEEZE_N, min_periods=1).max() > 0
    out["low_min"] = out["low"].rolling(PULLBACK_N, min_periods=1).min()
    cr20 = (c > out["ma20"]) & (c.shift(1) <= out["ma20"].shift(1))
    cr60 = (c > out["ma60"]) & (c.shift(1) <= out["ma60"].shift(1))
    out["cr20w"] = cr20.rolling(CR20_WIN, min_periods=1).max().astype(bool)
    out["cr60w"] = cr60.rolling(CR60_WIN, min_periods=1).max().astype(bool)
    out["below20"] = (c < out["ma20"]).rolling(BELOW20_N, min_periods=1).max().astype(bool)
    out["vol3"] = out["rvol"].rolling(VOL_CONFIRM_N, min_periods=1).max()
    bull = (c > out["ma250"]) & out["up250"]
    bear = (c < out["ma250"]) & (~out["up250"])
    out["regime"] = np.where(bull, "牛", np.where(bear, "熊", "震荡"))
    out["regime"] = pd.Series(out["regime"], index=out.index).where(out["ma250"].notna(), "")
    return out


def load(leg, refresh_=False, fails=None):
    """该腿指标面板（场内 ETF 口径）。"""
    return prepare(ohlcv.frame(leg, refresh_=refresh_, fails=fails))


# ---------------------------------------------------------------- 3. 建仓判定
def entry_signal(ind, t):
    """第 t 根的建仓信号 -> (类型, 说明) 或 None。所有条件都只用 ≤t 的数据。"""
    c = float(ind["close"].iat[t])
    ma20, ma60, ma250 = (float(ind[k].iat[t]) for k in ("ma20", "ma60", "ma250"))
    atr, adx, rvol = (float(ind[k].iat[t]) for k in ("atr", "adx", "rvol"))
    if not all(map(math.isfinite, (ma20, ma60, ma250, atr, adx, rvol))):
        return None
    # §二 多头环境：股价在年线上方且年线（扣抵口径）向上
    if not (c > ma250 and bool(ind["up250"].iat[t])):
        return None
    # §三.6 休眠：ADX < 20 不开新仓
    if adx < ADX_OFF:
        return None

    prev_c = float(ind["close"].iat[t - 1])
    low_min = float(ind["low_min"].iat[t])
    vol3 = float(ind["vol3"].iat[t])          # 近 3 日最大量比（放量确认不必与站回同日）
    top = max(ma20, ma60, ma250)

    # ② 回踩确认（主入场路径）：多头环境 + 20 线在 60 线上方 + 站上 20 线 + 近 10 日回踩到 20 线附近
    #    + 近 3 日出现放量（≥1.5×MAV20）+ 今日收盘上行。
    #    原始描述是「缩量回踩 60/20 线托举 -> 回踩后放量中阳重新站上 20 日线」，
    #    这里把「放量中阳」拆成「近 3 日放量确认 + 今日上行」——严格同日会几乎无信号（实测 2 笔/11 年）。
    if (ma20 > ma60 and c > ma20 and c > prev_c and vol3 >= VOL_MULT
            and low_min <= ma20 * (1.0 + TOUCH_PCT)):
        return ("回踩", f"回踩20线后放量确认(3日量比{vol3:.2f},ADX{adx:.0f})")

    # ① 突破：近 5 日内放量上穿 60 日线，且收盘站在 MA60 + max(1ATR, 2%) 之上
    #    §三.2 的「20 日线金叉 60 日线」在这里表现为「20 线已在 60 线附近或上方」——
    #    要求严格 > 是错的：刚上穿 60 线时 20 线必然还在下方（两条线交叉需要时间）。
    if (adx >= ADX_ON and rvol >= VOL_MULT and bool(ind["cr60w"].iat[t]) and c > ma60
            and c >= ma60 + max(BRK_ATR * atr, BRK_PCT * ma60)
            and (ma20 >= ma60 * (1.0 - GOLDEN_TOL) or bool(ind["up20"].iat[t]))):
        return ("突破", f"放量上穿60日线(量比{rvol:.2f},ADX{adx:.0f})")

    # ③ 三线粘合后放量长阳脱离（起爆，不提前猜方向；此时不要求 20 线已在 60 线上）
    if (vol3 >= VOL_MULT and bool(ind["squeeze"].iat[t]) and c >= top
            and c >= prev_c * (1.0 + SQUEEZE_YANG)):
        return ("粘合", f"三线粘合放量长阳(极差{float(ind['spread'].iat[t]):.1%},量比{vol3:.2f})")
    return None


def _idle_reason(ind, t):
    """空仓时今天为什么还不买（给每日扫描用）。"""
    c = float(ind["close"].iat[t])
    ma250, adx, rvol = (float(ind[k].iat[t]) for k in ("ma250", "adx", "rvol"))
    vol3 = float(ind["vol3"].iat[t])
    ma20, ma60 = float(ind["ma20"].iat[t]), float(ind["ma60"].iat[t])
    if not math.isfinite(ma250):
        return "年线未成形"
    if not (c > ma250 and bool(ind["up250"].iat[t])):
        return "非多头环境（价在年线下或年线向下）"
    if adx < ADX_OFF:
        return f"ADX休眠({adx:.0f}<{ADX_OFF:.0f})"
    if ma20 <= ma60:
        return "20日线未在60日线上方"
    if vol3 < VOL_MULT:
        return f"量能不足(3日最大量比{vol3:.2f}<{VOL_MULT})"
    if c <= ma20:
        return f"价在20日线下({c / ma20 - 1:+.1%})"
    if float(ind["low_min"].iat[t]) > ma20 * (1.0 + TOUCH_PCT):
        return "未回踩到20线附近（不是回踩后的介入点）"
    return "等待回踩确认/放量突破"


# ---------------------------------------------------------------- 4. 状态机
def simulate(ind, cost=COST_ONE_WAY):
    """逐腿状态机 -> DataFrame（在原指标面板上加 w / nav / act / why / stop）。

    权重 `w[t]` 是「t 日收盘做出、t+1 收盘成交」的目标仓位；净值里用 `w.shift(1)` 吃收益，
    因此与家族其它变种同一纪律、无前视。
    """
    n = len(ind)
    c = ind["close"].to_numpy(dtype=float)
    ma20 = ind["ma20"].to_numpy(dtype=float)
    ma60 = ind["ma60"].to_numpy(dtype=float)
    ma250 = ind["ma250"].to_numpy(dtype=float)
    atr = ind["atr"].to_numpy(dtype=float)
    rvol = ind["rvol"].to_numpy(dtype=float)
    low_min = ind["low_min"].to_numpy(dtype=float)
    up250 = ind["up250"].to_numpy(dtype=bool)

    w = np.zeros(n)
    stop = np.full(n, np.nan)
    act = np.empty(n, dtype=object)
    why = np.empty(n, dtype=object)
    act[:] = ""
    why[:] = ""

    size0, stop_px, peak, half = 0.0, np.nan, np.nan, False
    entry_px = np.nan
    cool = 0
    for t in range(n):
        prev = w[t - 1] if t else 0.0
        if cool > 0:
            cool -= 1
        if not (math.isfinite(ma250[t]) and math.isfinite(atr[t]) and math.isfinite(rvol[t])):
            w[t] = prev
            if t:
                act[t], why[t] = act[t - 1], why[t - 1]
            continue
        px = c[t]
        if prev <= 0.0:
            sig = entry_signal(ind, t) if cool == 0 else None
            if sig:
                kind, note = sig
                stop_ref = min(px - STOP_ATR * atr[t], low_min[t] * (1.0 - STOP_BUFFER))
                stop_pct = float(np.clip((px - stop_ref) / px, MIN_STOP, MAX_STOP))
                size0 = min(MAX_POS, RISK_PCT / stop_pct)
                w[t], stop_px, peak, half = size0, px * (1.0 - stop_pct), px, False
                entry_px = px
                act[t] = f"建仓[{kind}]"
                why[t] = f"{note}｜仓位{size0:.0%}｜止损{stop_px:.3f}(-{stop_pct:.1%})"
            else:
                w[t] = 0.0
                act[t], why[t] = "空仓", _idle_reason(ind, t)
            stop[t] = stop_px
            continue

        peak = max(peak, px)
        # 移动止损只在「已有 TRAIL_ARM 个 ATR 的浮盈」之后才接管 —— 否则建仓首日
        # `peak − 3ATR` 就会把止损抬到结构性止损之上，变成一进场就被噪声扫掉。
        if peak - entry_px >= TRAIL_ARM * atr[t]:
            trail = peak - TRAIL_ATR * atr[t]
            if trail > stop_px:
                stop_px = trail
        stop[t] = stop_px

        if px <= stop_px:
            w[t], size0, half, stop_px = 0.0, 0.0, False, np.nan
            cool = COOLDOWN
            act[t] = "清仓[止损]"
            why[t] = f"收盘{px:.3f}≤止损{stop[t]:.3f}"
        elif px < ma60[t] and (rvol[t] >= EXIT_VOL_MULT or px < ma60[t] * (1.0 - BREAK_MA60_PCT)):
            w[t], size0, half, stop_px = 0.0, 0.0, False, np.nan
            cool = COOLDOWN
            tag = "放量" if rvol[t] >= EXIT_VOL_MULT else "有效"
            act[t] = "清仓[破60线]"
            why[t] = f"{tag}跌破60日线({px:.3f}<{ma60[t]:.3f})"
        elif px < ma250[t] and not up250[t]:
            w[t], size0, half, stop_px = 0.0, 0.0, False, np.nan
            cool = COOLDOWN
            act[t] = "清仓[年线转弱]"
            why[t] = f"价在年线下且年线向下({px:.3f}<{ma250[t]:.3f})"
        elif px < ma20[t] and (px / ma60[t] - 1.0) <= THREAT_GAP:
            # 「跌破 20 日线**并且威胁 60 日线**」才减仓 —— 单纯的 MA20 上下穿在趋势里是常态，
            # 每次都减半会把趋势中的正常回踩当成风险事件，收益被反复切碎。
            if not half:
                w[t], half = prev * HALF, True
                act[t] = "减半[破20线威胁60线]"
                why[t] = (f"跌破20日线({px:.3f}<{ma20[t]:.3f}) 且距60线仅 "
                          f"{px / ma60[t] - 1:+.1%}，仓位→{w[t]:.0%}")
            else:
                w[t] = prev
                act[t], why[t] = "持有[半仓]", f"仍在20日线下({px / ma20[t] - 1:+.1%})"
        elif half and px > ma20[t]:
            w[t], half = size0, False
            act[t] = "恢复[站回20线]"
            why[t] = f"收复20日线({px:.3f}>{ma20[t]:.3f})，仓位→{w[t]:.0%}"
        elif half:
            w[t] = prev
            act[t], why[t] = "持有[半仓]", f"仍在20日线下({px / ma20[t] - 1:+.1%})，未威胁60线"
        else:
            w[t] = prev
            act[t], why[t] = "持有", f"在20日线上({px / ma20[t] - 1:+.1%})"

    ret = ind["close"].pct_change().fillna(0.0).to_numpy(dtype=float)
    pos = np.concatenate([[0.0], w[:-1]])
    r = pos * ret + (1.0 - pos) * CASH_DAILY - np.abs(np.diff(np.concatenate([[0.0], pos]))) * cost
    nav = np.cumprod(1.0 + r)

    out = ind.copy()
    out["w"], out["pos"], out["stop"] = w, pos, stop
    out["act"], out["why"] = act, why
    out["nav"] = nav
    out["nav_gross"] = np.cumprod(1.0 + pos * ret + (1.0 - pos) * CASH_DAILY)
    out["bh"] = ind["close"] / ind["close"].iloc[0]
    return out


# ---------------------------------------------------------------- 5. 统计
def perf(nav):
    """绩效（复用主策略 engine.metrics，无风险利率 = 货基）+ 卡玛。"""
    m = engine.metrics(nav)
    m["calmar"] = m["ann"] / abs(m["mdd"]) if abs(m["mdd"]) > 1e-9 else 0.0
    return m


def trades(sim):
    """按仓位由 0 -> >0 -> 0 切出单笔（净值口径，含减半段与空仓利息）。"""
    w = sim["w"].to_numpy(dtype=float)
    nav = sim["nav"].to_numpy(dtype=float)
    idx = sim.index
    out, entry = [], None
    for i, v in enumerate(w):
        if v > 0 and entry is None:
            entry = i
        elif v <= 0 and entry is not None:
            out.append(dict(entry=idx[entry], exit=idx[i], bars=i - entry,
                            ret=float(nav[i] / nav[entry] - 1.0), open=False))
            entry = None
    if entry is not None:
        out.append(dict(entry=idx[entry], exit=idx[-1], bars=len(w) - 1 - entry,
                        ret=float(nav[-1] / nav[entry] - 1.0), open=True))
    return out


def trade_stats(tr):
    """单笔统计：笔数 / 胜率 / 盈亏比 / 利润因子 / 平均持仓 / 最大连续亏损 / 期望。"""
    if not tr:
        return dict(n=0, win_rate=0.0, avg_win=0.0, avg_loss=0.0, payoff=0.0,
                    pf=0.0, bars=0.0, exp=0.0, max_loss_streak=0)
    wins = [t["ret"] for t in tr if t["ret"] > 0]
    loss = [t["ret"] for t in tr if t["ret"] <= 0]
    gp, gl = sum(wins), -sum(loss)
    aw = gp / len(wins) if wins else 0.0
    al = gl / len(loss) if loss else 0.0
    streak = cur = 0
    for t in tr:
        cur = cur + 1 if t["ret"] <= 0 else 0
        streak = max(streak, cur)
    return dict(n=len(tr), win_rate=len(wins) / len(tr),
                avg_win=aw, avg_loss=-al if loss else 0.0,
                payoff=(aw / al) if al > 1e-12 else float("inf") if aw else 0.0,
                pf=(gp / gl) if gl > 1e-12 else float("inf") if gp else 0.0,
                bars=sum(t["bars"] for t in tr) / len(tr),
                exp=sum(t["ret"] for t in tr) / len(tr),
                max_loss_streak=streak)


def exposure(sim):
    """平均投入（按次日生效口径）。"""
    return float(sim["pos"].mean()) if len(sim) else 0.0


def friction(sim):
    """摩擦成本：累计换手 x 单边费率，以及扣费前后年化的差。"""
    turn = float(np.abs(np.diff(np.concatenate([[0.0], sim["pos"].to_numpy()]))).sum())
    gross, net = perf(sim["nav_gross"]), perf(sim["nav"])
    return dict(turnover=turn, cost_total=turn * COST_ONE_WAY,
                ann_gross=gross["ann"], ann_net=net["ann"],
                drag_ann=gross["ann"] - net["ann"])


def regime_breakdown(sim):
    """分市对比：牛 / 熊 / 震荡 三种市况下（次日生效口径）策略与买入持有的**日均收益**。

    为什么用日均而不是累计：三种市况在时间上是**交叉**的，累计值会把「牛市里的 8 年」和
    「熊市里的 2 年」放在一起比，没有可比性；日均收益再乘 244 就是该市况下的年化量级。
    """
    pos = sim["pos"].to_numpy(dtype=float)
    ret = sim["close"].pct_change().fillna(0.0).to_numpy()
    reg = np.concatenate([[""], sim["regime"].to_numpy()[:-1]])
    out = {}
    for k in ("牛", "熊", "震荡"):
        m = reg == k
        if not m.any():
            out[k] = dict(days=0, strat_day=0.0, bh_day=0.0)
            continue
        out[k] = dict(days=int(m.sum()),
                      strat_day=float((pos[m] * ret[m] + (1.0 - pos[m]) * CASH_DAILY).mean()),
                      bh_day=float(ret[m].mean()))
    return out


def state(sim):
    """最新一根的状态（每日扫描 / 报告用）。"""
    r = sim.iloc[-1]
    p = sim.iloc[-2] if len(sim) > 1 else r
    return dict(
        date=sim.index[-1].date().isoformat(), close=float(r["close"]),
        ma20=float(r["ma20"]), ma60=float(r["ma60"]), ma250=float(r["ma250"]),
        d20=float(r["d20"]), d60=float(r["d60"]), d250=float(r["d250"]),
        up20=bool(r["up20"]), up60=bool(r["up60"]), up250=bool(r["up250"]),
        atr_pct=float(r["atr_pct"]), adx=float(r["adx"]), rvol=float(r["rvol"]),
        regime=str(r["regime"]), w=float(r["w"]), pos=float(r["pos"]),
        stop=float(r["stop"]) if math.isfinite(float(r["stop"])) else None,
        action=str(r["act"]), reason=str(r["why"]),
        w_prev=float(p["w"]),
    )


def fmt_pct(x, nd=2):
    return "—" if x is None else f"{x * 100:+.{nd}f}%"


def fmt_x(x, nd=2):
    if x is None:
        return "—"
    if isinstance(x, float) and math.isinf(x):
        return "∞"
    return f"{x:.{nd}f}"


# ---------------------------------------------------------------- 6. 组合层
def union_frame(start_from_ma=True):
    """七腿价格并集（各腿从自己年线成形日起；缺数据的日期留 NaN）。

    组合层**不取交集**：A500（563360）2024-10 才上市，取交集会把组合样本压到 2 年，
    结论会被「近几年这一种行情」绑架。改为「缺数据的腿不占权重、权重在可用腿之间重新归一」。
    """
    cols = {}
    for k in LEGS:
        ind = load(k)
        px = ind["close"]
        if start_from_ma:
            first = ind["ma250"].first_valid_index()
            if first is not None:
                px = px.loc[first:]
        cols[k] = px
    return pd.DataFrame(cols).sort_index()


def _weight_matrix(frame, weight):
    """可用腿之间重新归一的权重矩阵（缺数据的腿权重为 0）。"""
    wmat = frame.notna().mul(pd.Series(weight), axis=1)
    tot = wmat.sum(axis=1).replace(0.0, np.nan)
    return wmat.div(tot, axis=0).fillna(0.0)


def portfolio(frame, sims=None, weight=None, cost=True):
    """组合层净值：Σ w_i·pos_i·ret_i + (1−Σ)·货基，按换手扣单边成本。"""
    weight = weight or W
    wmat = _weight_matrix(frame, weight)
    ret = frame.pct_change(fill_method=None).fillna(0.0)
    if sims is None:
        return (1.0 + (ret * wmat).sum(axis=1)).cumprod()
    pos = pd.DataFrame({k: sims[k]["pos"].reindex(frame.index).fillna(0.0) for k in frame.columns})
    inv = pos * wmat
    r = (inv * ret).sum(axis=1) + (1.0 - inv.sum(axis=1)) * CASH_DAILY
    if cost:
        d = pos.diff().fillna(pos).abs()
        r = r - (d * wmat).sum(axis=1) * COST_ONE_WAY
    return (1.0 + r).cumprod()


def avg_invested(frame, sims, weight=None):
    """平均投入（Σ w_i·pos_i 的均值）。"""
    weight = weight or W
    wmat = _weight_matrix(frame, weight)
    pos = pd.DataFrame({k: sims[k]["pos"].reindex(frame.index).fillna(0.0) for k in frame.columns})
    return float((pos * wmat).sum(axis=1).mean())


def yearly(nav):
    """按自然年切分的年度收益（首尾为不完整年度）。"""
    out, seg = {}, {}
    vals = nav.to_numpy(dtype=float)
    for i, d in enumerate(nav.index):
        y = str(d.year)
        st = 1.0 if i == 0 else vals[i - 1]
        seg.setdefault(y, [st, vals[i]])
        seg[y][1] = vals[i]
    for y, (a, b) in seg.items():
        out[y] = b / a - 1.0
    return out


def _self_check():
    """快速自检：七腿可读、指标成形、状态机权重合法。"""
    for k in LEGS:
        sim = simulate(load(k))
        st = state(sim)
        assert 0.0 <= st["w"] <= 1.0 + 1e-9, (k, st["w"])
        m = perf(sim["nav"])
        print(f"{k:6s} {rule.LEGS[k]['name']:8s} {st['date']} 收盘 {st['close']:.3f} "
              f"MA20/60/250 {st['ma20']:.3f}/{st['ma60']:.3f}/{st['ma250']:.3f} "
              f"ADX {st['adx']:.0f} 量比 {st['rvol']:.2f} 仓位 {st['w']:.0%} "
              f"[{st['action']}] 年化 {fmt_pct(m['ann'])} 回撤 {fmt_pct(m['mdd'])}")
    print(f"带权(Σ=1): " + "  ".join(f"{k}={W[k]:.3f}" for k in LEGS))


if __name__ == "__main__":
    _self_check()
