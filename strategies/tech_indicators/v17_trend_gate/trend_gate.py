# -*- coding: utf-8 -*-
"""技术指标家族 · V17 变种：趋势四象限闸门（年线位置 × 年线 20 日斜率）。

来源：用户 2026-11-02 的「趋势判断」说明（与 vol_legs/v5_trend_gate 同一套规则）。
本变种把它**搬到 tech_indicators 家族**：把 v5 的「七腿场外复权净值」换成家族
**54 只场内 ETF 收盘价**（`data/_long_klines.json` + `_pool.py` / `data/_universe.md` 前四类），
其余规则、参数、象限判定、档位状态机、回测分析**原样保留**。

核心命题：**趋势判断不是预测**，而是把已经发生的价格结构压成两个可计算的布尔量：

- `close` 在 250 日均线（A 股年线）**上方还是下方**；
- 250 日均线**自身近 20 日朝上还是朝下**（同一根线的属性，不是新维度）。

两者都朝上才叫上涨趋势，**其余三格都不给加仓许可**。

## 1. 用在哪、不用在哪（这是规则的一部分，不是建议）

- **用在逐只 ETF 上**：对池内每只 ETF 各自算这两个数，给出它的象限与目标档位。
  原 v5 的「科创50/恒生科技/黄金 vs 宽基」作用域之争，在这里不再成立——
  家族 54 只 ETF 都是场内价格序列，且**没有 vol_legs 那种「组合层仓位带」概念**
  （带内占比在这里直接等于该 ETF 的目标仓位），故**全部 54 只统一套闸门**（`GATE_ALL=True`）。
- 原 v5 指出「宽基估值锚叠加趋势过滤会互相抵消」——那是 vol_legs 组合层带位的取舍；
  本变种是单标的分档，不存在带位叠加问题，所以全部套上即可，结论自行由回测验证。

## 2. 三个坑（写在参数里，不是写在注释里）

1. **滞后是设计特性不是 bug**。ma250 确认「进入下跌趋势」通常已跌掉两位数（回测逐只给出实测）。
   目标是避开以年为单位的阴跌段，不是躲三个月的回调；拿它躲短期波动必然被反复打脸。
2. **250 和 200 不是一回事**。A 股一年约 240–245 个交易日，中文语境的「年线」取 250；
   美股习惯 200 日均线。混用会让两套回测结论互相矛盾。这里 `MA_WIN=250` 全池统一。
3. **别加过滤器，加频率帽**。震荡市里假信号一定存在；工程解法不是再叠一个指标（会引入新维度），
   而是限制响应速度。本实现用两层频率帽，不带任何技术指标过滤：
   - 阶梯帽：跌破年线满 `REDUCE_DAYS=5` 日才减半、满 `CLEAR_DAYS=20` 日未收复才清至观察；
   - 间隔帽：同一标的两次调仓至少间隔 `ACTION_GAP=5` 个交易日。

## 3. 仓位语义（与 vol_legs v5 不同）

本变种不是 0/1 择时信号，而是**仓位档位闸门**：`state∈{1.0, 0.5, 0.2}` 是该 ETF 的**目标仓位比例**
（家族惯例是 0/1，本变种保留 v5 的带内分档——这是「复制这个策略」的关键，不可折叠成 0/1）。
- 上涨趋势 → 满仓（1.0）；
- 反弹 → 只持有，不给加仓许可（维持当前档位）；
- 高位回调 → 降一档（封顶 0.5），不新增；
- 下跌趋势 → 满 5 日减半（0.5）→ 满 20 日清至观察（0.2），等右侧。

基准对照 = **该 ETF 买入持有满仓（1.0）**（即「恒持满仓」），不是「空仓」。无风险利率按
家族惯例取 0（与 tech_indicators 五基线/`v16_trin` 一致，不假设货基利息）。

## 4. 成交与时序（无前视）

`state[t]` = **t 日收盘成交完之后**的目标仓位；t 日只是决策，赚 t+1 日收益（`state.shift(1)`）。
家族惯例是「信号当日收盘成交」，这里与其一致：决策用 t 日及之前数据，仓位在 t 日收盘确定，
赚 t+1 日收益——无未来函数、无 T+1 净值时滞（场内收盘价即可）。

模拟结果，非投资建议。
"""
import math
import os
import sys

import numpy as np
import pandas as pd

try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:  # noqa: BLE001
    pass

_HERE = os.path.dirname(os.path.abspath(__file__))
_PARENT = os.path.dirname(_HERE)                    # strategies/tech_indicators
_ROOT = os.path.dirname(os.path.dirname(_PARENT))   # 仓库根
_DATA = os.path.join(_ROOT, "data")
for _p in (_HERE, _PARENT, _ROOT, _DATA):
    if _p not in sys.path:
        sys.path.insert(0, _p)

import data as vdata  # noqa: E402

LABEL = "V17 趋势四象限闸门"

# ---------------------------------------------------------------- 1. 参数（唯一权威源）
MA_WIN = 250            # 年线（A 股口径 250，不是美股 200）
SLOPE_N = 20            # 年线斜率回看交易日数

# 趋势「读数」口径：默认均线法。研究脚本可换这几档做灵敏度对比
# （它们全部映射成同一组 (above, up) 两个布尔量，档位阶梯不改）：
#   ma        价格 vs MA(MA_WIN)
#   donchian  价格 vs 唐奇安通道中轨 (N 日最高+最低)/2
#   dual_ma   价格 ≥ MA(FAST_WIN) 且 MA(FAST_WIN) ≥ MA(SLOW_WIN)
#   momentum  动量读数 vs 0（MOM_N 日累计收益）
#   macd      DIF > 0 且 DIF ≥ DEA（FAST_WIN/SLOW_WIN 当 EMA 周期）
KIND = "ma"
BUF = 0.0               # 均线类口径的确认缓冲（0 = 贴着线就翻转）
FAST_WIN = 50           # dual_ma / macd 的快线（EMA）
SLOW_WIN = 200          # dual_ma / macd 的慢线（EMA）
KAMA_N = 10             # 考夫曼自适应均线：效率比率窗口
KAMA_FAST = 2           # KAMA 快端 EMA 常数（2/(n+1) 里的 n）
KAMA_SLOW = 30          # KAMA 慢端
HULL_N = 100            # Hull 均线窗口（低滞后）
DON_N = 55              # 唐奇安通道回看
MOM_N = 126             # 动量口径回看（约 6 个月）
REF_HI = 250            # 「距高点回撤」的参照窗口，固定不随口径变（否则滞后统计不可比）
REDUCE_DAYS = 5         # 跌破年线满 5 个交易日 → 减至半仓
CLEAR_DAYS = 20         # 满 20 个交易日未收复 → 清至观察仓
ACTION_GAP = 5          # 同一标的两次调仓的最小间隔（频率帽第二层）

FULL, HALF, OBSERVE = 1.0, 0.5, 0.2      # 满仓 / 半仓 / 观察仓（目标仓位比例）
LEVEL_TXT = {1.0: "满仓", 0.5: "半仓", 0.2: "观察仓", 0.0: "空仓"}    # 档位中文名（图与每日共用）

# 作用域：全部 54 只场内 ETF 均套闸门（家族单标的分档，不存在 vol_legs 带位叠加问题）。
GATE_ALL = True
MODE = "quad"           # "quad" 四象限（默认）/ "ma_only" 只看 close 与年线（对照）/
                        # "asym" 非对称：减仓用快线（DOWN_WIN + DOWN_SLOPE），加仓仍由 MA250+20 把门
DOWN_WIN = 120          # 非对称档的「快线」：跌破它满 REDUCE_DAYS 日就减半（只压不抬）
DOWN_SLOPE = 5          # 快线斜率回看（判定「快线下行」）

WARMUP = MA_WIN + SLOPE_N - 1     # 年线成形 + 斜率可算的第一个交易日
SLIP = 0.0             # 单边滑点：家族惯例未计佣金/滑点，故 0

# 无风险利率：家族惯例取 0（与 tech_indicators 五基线 / v16_trin 一致）。
CASH_ANNUAL = 0.0
CASH_DAILY = 0.0

# 池：家族 54 只场内 ETF（前四类）。`LEGS` 名沿用便于与 v5 同名函数对齐。
CODES = vdata.codes()
NAMES = vdata.names()
LEGS = sorted(CODES)
W = {k: 1.0 / len(LEGS) for k in LEGS}   # 组合的等权权重（Σ=1）

# 场内 ETF 无申赎费口径（家族惯例未计佣金/滑点），全部 0。
FEE = {k: (0.0, 0.0) for k in LEGS}

QUADS = ("上涨趋势", "反弹", "高位回调", "下跌趋势")


def params(**kw):
    """参数打包：回测敏感性扫描时用 `**{...}` 覆盖，不必改模块常量。"""
    p = dict(MA_WIN=MA_WIN, SLOPE_N=SLOPE_N, REDUCE_DAYS=REDUCE_DAYS,
             CLEAR_DAYS=CLEAR_DAYS, ACTION_GAP=ACTION_GAP,
             FULL=FULL, HALF=HALF, OBSERVE=OBSERVE,
             GATE_ALL=GATE_ALL, START="full", MODE=MODE,
             DOWN_WIN=DOWN_WIN, DOWN_SLOPE=DOWN_SLOPE,
             KIND=KIND, BUF=BUF, FAST_WIN=FAST_WIN, SLOW_WIN=SLOW_WIN, DON_N=DON_N, MOM_N=MOM_N,
             KAMA_N=KAMA_N, KAMA_FAST=KAMA_FAST, KAMA_SLOW=KAMA_SLOW, HULL_N=HULL_N)
    p.update(kw)
    return p


def gated(leg, p=None):
    """该标的是否套闸门（默认 GATE_ALL=True，全部 54 只套）。"""
    p = p or params()
    return bool(p["GATE_ALL"])


def load(leg, mode=None):
    """该标的收盘价序列（默认场内收盘价）；缺失/刷新失败直接抛错，不静默补位。"""
    return vdata.series(leg, refresh=False).dropna()


def fee_of(leg, slip=None):
    """-> (买入费率, 卖出费率)；家族未计佣金/滑点，返回 (0, 0)。"""
    return 0.0, 0.0


# ---------------------------------------------------------------- 2. 指标与象限
def _streak(cond):
    """cond 连续为 True 的计数（遇 False 归零）。"""
    grp = (~cond.fillna(False)).cumsum()
    return cond.fillna(False).astype(int).groupby(grp).cumsum()


def _wma(s, n):
    """加权移动平均（近端权重高）—— Hull 均线的零件。"""
    w = np.arange(1, n + 1, dtype=float)
    return s.rolling(n, min_periods=n).apply(lambda x: float(np.dot(x, w)) / w.sum(), raw=True)


def _kama(c, n, fast, slow):
    """考夫曼自适应均线：平滑系数随「效率比率」在 fast/slow 之间自适应。"""
    arr = c.to_numpy(dtype=float)
    er = (c.diff(n).abs() / c.diff().abs().rolling(n).sum()).to_numpy(dtype=float)
    sc = (np.nan_to_num(er) * (2.0 / (fast + 1) - 2.0 / (slow + 1))
          + 2.0 / (slow + 1)) ** 2
    out = np.full(len(arr), np.nan)
    if len(arr) <= n:
        return pd.Series(out, index=c.index)
    out[n] = arr[n]
    for i in range(n + 1, len(arr)):
        out[i] = out[i - 1] + sc[i] * (arr[i] - out[i - 1])
    return pd.Series(out, index=c.index)


def readout(c, p):
    """按 `KIND` 算该口径的「趋势读数」。-> (base, above, ok)"""
    kind = p["KIND"]
    if kind == "ma":
        base = c.rolling(p["MA_WIN"], min_periods=p["MA_WIN"]).mean()
        return base, c >= base * (1.0 - p["BUF"]), base.notna()
    if kind == "donchian":
        n = p["DON_N"]
        mid = (c.rolling(n, min_periods=n).max() + c.rolling(n, min_periods=n).min()) / 2.0
        return mid, c >= mid * (1.0 - p["BUF"]), mid.notna()
    if kind == "dual_ma":
        f = c.rolling(p["FAST_WIN"], min_periods=p["FAST_WIN"]).mean()
        s = c.rolling(p["SLOW_WIN"], min_periods=p["SLOW_WIN"]).mean()
        return f, (c >= f * (1.0 - p["BUF"])) & (f >= s), f.notna() & s.notna()
    if kind == "momentum":
        mom = c / c.shift(p["MOM_N"]) - 1.0
        return mom, mom > 0.0, mom.notna()
    if kind == "macd":
        dif = (c.ewm(span=p["FAST_WIN"], adjust=False).mean()
               - c.ewm(span=p["SLOW_WIN"], adjust=False).mean())
        dea = dif.ewm(span=9, adjust=False).mean()
        return dif, (dif > 0.0) & (dif >= dea), dif.notna()
    if kind == "kama":
        base = _kama(c, p["KAMA_N"], p["KAMA_FAST"], p["KAMA_SLOW"])
        return base, c >= base * (1.0 - p["BUF"]), base.notna()
    if kind == "hull":
        n = p["HULL_N"]
        base = _wma(2.0 * _wma(c, max(n // 2, 2)) - _wma(c, n), max(int(np.sqrt(n)), 2))
        return base, c >= base * (1.0 - p["BUF"]), base.notna()
    raise ValueError(
        f"未知 KIND: {kind}（可选 ma / donchian / dual_ma / momentum / macd / kama / hull）")


def indicators(px, **kw):
    """收盘价 -> 指标表。只用到当日及之前的数据。

    列：close / ma / slope / above / below_days / quad / dev / dd_hi
        + 诊断列 mom6 / mom12（动量法口径，只用于「三口径分歧」统计，不参与交易）
    """
    p = params(**kw)
    c = px.astype("float64")
    out = pd.DataFrame(index=c.index)
    out["close"] = c
    base, above, ok = readout(c, p)
    out["ma"] = base
    out["slope"] = base - base.shift(p["SLOPE_N"])
    out["ready"] = ok & out["slope"].notna()
    out["above"] = above
    out["below_days"] = _streak(~out["above"] & out["ready"]).where(out["ready"], 0)
    if p["KIND"] in ("ma", "donchian", "dual_ma", "kama", "hull"):
        out["dev"] = c / base - 1.0                       # 价格乖离
    else:
        out["dev"] = np.nan                               # 读数类口径无乖离概念
    out["dd_hi"] = c / c.rolling(REF_HI, min_periods=1).max() - 1.0
    up = out["slope"] > 0.0
    quad = np.where(out["above"].to_numpy(dtype=bool),
                    np.where(up.to_numpy(dtype=bool), QUADS[0], QUADS[1]),
                    np.where(up.to_numpy(dtype=bool), QUADS[2], QUADS[3]))
    out["quad"] = np.where(out["ready"].to_numpy(dtype=bool), quad, "")
    out["mom6"] = c / c.shift(126) - 1.0
    out["mom12"] = c / c.shift(252) - 1.0
    return out


def target_of(quad, below_days, cur, p=None):
    """象限 + 连续在年线下的天数 -> (目标仓位比例, 理由)。纯函数，回测与每日共用。"""
    p = p or params()
    full, half, obs = p["FULL"], p["HALF"], p["OBSERVE"]
    if p.get("MODE") == "ma_only":              # 对照档：只看 close 在年线上/下
        return (full, "年线上方：满仓") if quad in (QUADS[0], QUADS[1]) \
            else (obs, "年线下方：清至观察仓")
    if quad == QUADS[0]:                        # 上涨趋势：两个都朝上
        return full, "年线上方且年线上行：加至满仓"
    if quad == QUADS[1]:                        # 反弹：价格回到年线上，均线还在下
        return cur, "年线上方但年线下行：只持有，不给加仓许可"
    if quad == QUADS[2]:                        # 高位回调：均线还朝上
        if below_days >= p["REDUCE_DAYS"] and cur > half:
            return half, f"年线上行但已跌破 {below_days} 日：降一档"
        return cur, "年线下方：不新增"
    # 下跌趋势：两个都朝下
    if below_days >= p["CLEAR_DAYS"] and cur > obs:
        return obs, f"年线下行且已跌破 {below_days} 日：清至观察仓，等右侧"
    if below_days >= p["REDUCE_DAYS"] and cur > half:
        return half, f"年线下行且已跌破 {below_days} 日：减半"
    return cur, "年线下方：不新增"


def state_machine(px, leg, **kw):
    """-> DataFrame(index=净值日)。`state[t]` = t 日收盘成交后的目标仓位。

    `decide[t]` ∈ {buy, sell, ""} 是 t 日收盘做出、t+1 日成交的决策；`done[t]` 是 t 日
    成交后的说明。本变种全部套闸门（`gated()` 恒 True）。
    """
    p = params(**kw)
    I = indicators(px, **kw)
    n = len(px)
    state = np.zeros(n)
    decide = np.empty(n, dtype=object)
    decide[:] = ""
    done = np.empty(n, dtype=object)
    done[:] = ""
    tgt_v = np.full(n, np.nan)

    c = I["close"].to_numpy(dtype=float)
    quad = I["quad"].to_numpy(dtype=object)
    ready = I["ready"].to_numpy(dtype=bool)
    below = I["below_days"].to_numpy(dtype=int)

    # 非对称档（MODE="asym"）：另算一套「快」读数，只允许把档位压得更低（min）。
    d_quad = d_below = d_ready = None
    if p["MODE"] == "asym":
        D = indicators(px, KIND="ma", MA_WIN=p["DOWN_WIN"], SLOPE_N=p["DOWN_SLOPE"])
        d_quad = D["quad"].to_numpy(dtype=object)
        d_below = D["below_days"].to_numpy(dtype=int)
        d_ready = D["ready"].to_numpy(dtype=bool)

    hold = 1.0 if p.get("START", "full") == "full" else 0.0
    last_act, pend, pend_note = -10 ** 9, None, ""
    for t in range(n):
        if pend is not None:                     # 执行上一根的决策（次日收盘）
            hold = float(pend)
            last_act = t
            done[t] = pend_note
            pend = None
        state[t] = hold
        if not ready[t]:
            continue
        tgt, why = target_of(quad[t], int(below[t]), hold, p)
        if d_ready is not None and d_ready[t]:
            tgt_d, why_d = target_of(d_quad[t], int(d_below[t]), hold, p)
            if tgt_d < tgt - 1e-9:
                tgt, why = tgt_d, why_d + "（快线下车）"
        tgt_v[t] = tgt
        if abs(tgt - hold) > 1e-9 and t - last_act >= p["ACTION_GAP"]:
            pend, pend_note = tgt, why
            decide[t] = "buy" if tgt > hold else "sell"

    out = pd.DataFrame(dict(state=state, decide=decide, done=done, target=tgt_v),
                       index=px.index)
    return out.join(I)


# ---------------------------------------------------------------- 3. 回测
def run_leg(state, px, fee=(0.0, 0.0)):
    """单标的净值（起点 = 1）。持仓按 `state.shift(1)` 赚当日收益，成交日扣成本。

    与 vol_legs v5 同构，仅无风险利率 CASH_DAILY=0（家族惯例）。
    """
    ret = px.pct_change().fillna(0.0)
    hold = state.shift(1).fillna(0.0).astype(float)
    r = hold * ret + (1.0 - hold) * CASH_DAILY
    d = state.diff().fillna(state).astype(float)
    r = r - (d > 0) * float(fee[0]) - (d < 0) * float(fee[1])
    return (1.0 + r).cumprod()


def actions(state):
    """按 state 的变化折出调仓动作（成交日 = 决策次日）。-> [{date, frm, to, kind, gap}]"""
    s = state.to_numpy(dtype=float)
    idx = state.index
    out, prev = [], 0
    for i in range(1, len(s)):
        if abs(s[i] - s[i - 1]) > 1e-9:
            out.append(dict(date=idx[i], frm=float(s[i - 1]), to=float(s[i]),
                            kind="buy" if s[i] > s[i - 1] else "sell", gap=i - prev))
            prev = i
    return out


def whipsaw(state, px, window=20):
    """打脸统计：减仓后 `window` 个交易日内又加回的次数，以及期间让掉的价格变动。"""
    acts = []
    s = state.to_numpy(dtype=float)
    for i in range(1, len(s)):
        if abs(s[i] - s[i - 1]) > 1e-9:
            acts.append((i, "buy" if s[i] > s[i - 1] else "sell"))
    val = px.to_numpy(dtype=float)
    n, miss = 0, []
    for a in range(len(acts) - 1):
        i, kind = acts[a]
        j, kind2 = acts[a + 1]
        if kind == "sell" and kind2 == "buy" and j - i <= window:
            n += 1
            miss.append(val[j] / val[i] - 1.0)      # 卖在地板、买回来的代价
    return dict(n=n, avg_miss=(float(np.mean(miss)) if miss else 0.0),
                total_miss=(float(np.sum(miss)) if miss else 0.0))


def metrics(nav):
    """绩效指标（年化/回撤/波动/夏普，无风险利率 = 0，年化按 252 交易日）。"""
    nav = np.asarray(nav, dtype=float)
    if len(nav) < 2 or nav[0] <= 0 or nav[-1] <= 0:
        return dict(ann=0.0, mdd=0.0, vol=0.0, sharpe=0.0)
    n = len(nav) - 1
    years = max(n / 252.0, 1e-9)
    ann = (nav[-1] / nav[0]) ** (1.0 / years) - 1.0
    mdd = float((nav / np.maximum.accumulate(nav) - 1.0).min())
    dr = nav[1:] / nav[:-1] - 1.0
    vol = float(dr.std(ddof=1) * np.sqrt(252)) if len(dr) > 1 else 0.0
    sharpe = (ann - CASH_ANNUAL) / vol if vol > 0 else 0.0
    return dict(ann=ann, mdd=mdd, vol=vol, sharpe=sharpe)


def exposure(state):
    """平均仓位（0–1）。"""
    return float(state.mean()) if len(state) else 0.0


def leg_slice(leg, **kw):
    """-> (px, sm)。样本自年线 + 斜率都成形的第一个交易日起算。"""
    px_full = load(leg)
    sm = state_machine(px_full, leg, **kw)
    start = px_full.index[min(WARMUP, max(len(px_full) - 2, 0))]
    return px_full.loc[start:], sm.loc[start:]


def yearly(nav):
    """按自然年切分的年度收益（首尾为不完整年度）。"""
    out, seg = {}, {}
    vals = nav.to_numpy(dtype=float)
    for i, d in enumerate(nav.index):
        y = str(d.year)
        st = 1.0 if i == 0 else vals[i - 1]
        if y not in seg:
            seg[y] = [st, vals[i]]
        seg[y][1] = vals[i]
    for y, (a, b) in seg.items():
        out[y] = b / a - 1.0
    return out


def seg_perf(nav, d0, d1):
    """区间绩效（区间首日重新归 1）；区间无数据返回 None。"""
    sub = nav.loc[pd.Timestamp(d0):pd.Timestamp(d1)]
    if len(sub) < 3:
        return None
    return metrics(sub / sub.iloc[0])


def fmt_pct(x, nd=2):
    return "—" if x is None else f"{x * 100:+.{nd}f}%"


def fmt_x(x, nd=2):
    if x is None:
        return "—"
    if math.isinf(x):
        return "∞"
    return f"{x:.{nd}f}"


def _self_check():
    """快速自检：池内序列可读、象限四格齐全、仓位只落在 {0, .2, .5, 1} 内。"""
    for k in LEGS:
        try:
            px, sm = leg_slice(k)
        except KeyError:
            print(f"{k:8s} 无行情，跳过")
            continue
        st = sm["state"]
        assert len(st) == len(px)
        assert set(np.round(st.unique(), 6)) <= {0.0, 0.2, 0.5, 1.0}
        q = sm.loc[sm["quad"] != "", "quad"]
        cnt = q.value_counts()
        print(f"{k:8s} {NAMES.get(k, k):10s} {px.index[0].date()} ~ {px.index[-1].date()} "
              f"n={len(px):5d} 当前={st.iloc[-1]:.0%} 曝光={exposure(st):.0%} 象限日数="
              + " ".join(f"{name}{int(cnt.get(name, 0))}" for name in QUADS))
    print(f"年线 MA{MA_WIN} · 斜率 {SLOPE_N} 日 · 阶梯 {REDUCE_DAYS}/{CLEAR_DAYS} 日 · "
          f"间隔帽 {ACTION_GAP} 日 · 档位 {FULL:g}/{HALF:g}/{OBSERVE:g} · "
          f"作用域 全池 {len(LEGS)} 只")


if __name__ == "__main__":
    _self_check()
