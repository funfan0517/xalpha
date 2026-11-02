# -*- coding: utf-8 -*-
"""波动资产 · V5 变种：趋势四象限闸门（年线位置 × 年线 20 日斜率）。

来源：用户 2026-11-02 的「趋势判断」说明。核心命题是**趋势判断不是预测**，而是把
已经发生的价格结构压缩成一个可计算的布尔值；工程上只有两个数：

- `close` 在 250 日均线（A 股年线）**上方还是下方**；
- 250 日均线**自身近 20 日朝上还是朝下**（同一根线的属性，不是新维度）。

两者都朝上才叫上涨趋势，**其余三格都不给加仓许可**：

|  | 均线朝上 | 均线朝下 |
|---|---|---|
| **close > ma250** | 上涨趋势 → 可加仓（满带） | 反弹 → 只持有，不给加仓许可 |
| **close < ma250** | 高位回调 → 降一档（封顶半仓），不新增 | 下跌趋势 → 5 日减半 → 20 日清至观察，等右侧 |

右上角那格是本变种存在的理由：多数人把「站上年线」当成趋势反转，但 A 股大量行情是
「跌透之后的暴力反弹先把价格推回均线上方、而均线还在下行」（2024-10、2025-09 都是）。
价格先动、均线后动，中间这段必须单独分格，否则「站上年线就加仓」会买在反弹的第一天。

## 1. 用在哪、不用在哪（这是规则的一部分，不是建议）

- **只用在没有现金流锚的资产**：科创50（亏损股权导致 ERP 失真）、恒生科技、黄金。
  这类资产价格本身就是唯一可靠的信息源，年线闸门是合理的。
- **不要用在宽基上**：510300 的回测里，纯估值分位调仓年化 6.45% / 回撤 18.24%，
  叠加「跌破 200 日均线就降仓」后只剩 4.10% / 16.47% —— 用 2.35pp 年化换 1.77pp 回撤，
  不划算。原因是宽基的估值锚本身已经隐含均值回归（跌多了 PE 分位自动变低、自动触发买入），
  再加一层趋势过滤，等于把同一个信号用两次并让它们互相抵消。
- 所以本变种的默认作用域是 `GATE_LEGS = ("kc50", "hkt", "gold")`，A500 / 纳指100 / 标普500
  **不套闸门**（维持带内满仓）。`GATE_ALL=True` 可把六腿全套上，回测里两档并列，用来验证
  上面那条「不划算」在本库净值上是否同样成立 —— 不是用来选优的。

## 2. 三个坑（写在参数里，不是写在注释里）

1. **滞后是设计特性不是 bug**。ma250 确认「进入下跌趋势」通常已经跌掉 15–25%（回测报告
   「滞后成本」表逐腿给出实测中位数）。目标是避开以年为单位的阴跌段，不是躲三个月的回调；
   拿它躲短期波动必然被反复打脸。
2. **250 和 200 不是一回事**。A 股一年约 240–245 个交易日，中文语境的「年线」取 250；
   美股习惯 200 日均线。混用会让两套回测结论看起来互相矛盾。这里 `MA_WIN=250` 全池统一。
3. **别加过滤器，加频率帽**。震荡市里假信号一定存在；工程解法不是再叠一个指标（会引入新维度），
   而是限制响应速度。本实现用两层频率帽，不带任何技术指标过滤：
   - 阶梯帽：跌破年线满 `REDUCE_DAYS=5` 日才减半、满 `CLEAR_DAYS=20` 日未收复才清至观察；
   - 间隔帽：同一腿两次调仓至少间隔 `ACTION_GAP=5` 个交易日。

## 3. 仓位语义（与 v1/v2/v3 不同）

本变种不是 0/1 择时信号，而是**仓位带上的闸门**：`state∈{1.0, 0.5, 0.2}` 是「占本腿目标带
的比例」。带位仍由主策略决定（`rule.LEGS[leg]["band"]`：科创50/恒生科技 2.5pp、黄金 10pp），
本变种只回答「这条带今天该露出多少」。所以它的基准对照是**静态带**（恒持满带），
不是「买入持有」（虽然逐腿也给买入持有列）。

观察仓 0.2 与主策略 `rule.OBSERVE_PP`（0.5pp / 2.5pp 带）同比例，减半 1.25pp 与之拉开一档。

## 4. 成交与时序（无前视）

`state[t]` = **t 日收盘成交完之后**的带内占比；t 日只是决策，成交在 t+1 收盘净值
（与 v1/v2/v3 同一纪律）。日收益归属用 `state.shift(1)`。空仓部分按货基 `rule.CASH_ANNUAL`
（1.40%）逐日计息，不假设 0 收益。

## 5. 与其它变种的关系

同属「逐腿进出时点」层，与主策略的组合层仓位带可叠加不可替代。与 v1_ma20 同向（都做多头
趋势），但 v1 是 0/1 全进全出、且用 MA20（月线级别），本变种是带内分档、用 MA250（年级别）；
与 v2/v3（突破追涨）方向不冲突（本变种不看突破），但不要和它们共用同一份额度。

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
_VL = os.path.dirname(_HERE)                        # strategies/vol_legs
_ROOT = os.path.dirname(os.path.dirname(_VL))       # 仓库根
for _p in (_HERE, _VL):
    if _p not in sys.path:
        sys.path.insert(0, _p)

import data as vdata    # noqa: E402
import engine           # noqa: E402
import rule             # noqa: E402

LABEL = "V5 趋势四象限闸门"

# ---------------------------------------------------------------- 1. 参数（唯一权威源）
MA_WIN = 250            # 年线（A 股口径 250，不是美股 200）
SLOPE_N = 20            # 年线斜率回看交易日数
REDUCE_DAYS = 5         # 跌破年线满 5 个交易日 → 减至半仓
CLEAR_DAYS = 20         # 满 20 个交易日未收复 → 清至观察仓
ACTION_GAP = 5          # 同一腿两次调仓的最小间隔（频率帽第二层）

FULL, HALF, OBSERVE = 1.0, 0.5, 0.2      # 满带 / 半仓 / 观察仓（带的占比）
LEVEL_TXT = {1.0: "满带", 0.5: "半仓", 0.2: "观察仓", 0.0: "空仓"}    # 档位中文名（图与每日共用）

# 作用域：只套没有现金流锚的三条腿；宽基（A500/纳指/标普）有估值锚，套上会互相抵消
GATE_LEGS = ("kc50", "hkt", "gold")
GATE_ALL = False        # True = 六腿全套（仅作对照，不推荐）
START = "full"          # "full" = 带内满仓起步（实盘口径）/ "flat" = 空仓起步
MODE = "quad"           # "quad" = 四象限（默认）/ "ma_only" = 只看 close 与 ma250（对照）

WARMUP = MA_WIN + SLOPE_N - 1     # 年线成形 + 斜率可算的第一个交易日
SLIP = 0.001            # 单边滑点 0.1%（与 v1~v6 同一口径）

CASH_ANNUAL = rule.CASH_ANNUAL
CASH_DAILY = (1.0 + CASH_ANNUAL) ** (1.0 / 252) - 1.0
LEGS = rule.LEG_ORDER
W = {k: float(rule.LEGS[k]["band"]) / float(rule.SLEEVE_PP) for k in LEGS}   # 带权归一化(Σ=1)

# 场外申赎成本（(买入, 卖出) 单边费率），与 v1/v2/v3/v6 同一张表：
# A 类（270042 / 017641 / 000216）申购 0.10% + 赎回 0.50%（持有 >7 天）；
# C 类（023299 / 011609 / 012805）申赎免费，改收约 0.2%/年销售服务费 —— 后者**未计入**，
# 故 C 类腿的扣费后数字偏乐观。滑点 `SLIP` 在 `fee_of()` 里逐边追加。
FEE = {
    "a500": (0.0, 0.0),
    "ndx": (0.001, 0.005),
    "spx": (0.001, 0.005),
    "kc50": (0.0, 0.0),
    "hkt": (0.0, 0.0),
    "gold": (0.001, 0.005),
}

QUADS = ("上涨趋势", "反弹", "高位回调", "下跌趋势")


def params(**kw):
    """参数打包：回测敏感性扫描时用 `**{...}` 覆盖，不必改模块常量。"""
    p = dict(MA_WIN=MA_WIN, SLOPE_N=SLOPE_N, REDUCE_DAYS=REDUCE_DAYS,
             CLEAR_DAYS=CLEAR_DAYS, ACTION_GAP=ACTION_GAP,
             FULL=FULL, HALF=HALF, OBSERVE=OBSERVE,
             GATE_LEGS=GATE_LEGS, GATE_ALL=GATE_ALL, START=START, MODE=MODE)
    p.update(kw)
    return p


def gated(leg, p=None):
    """该腿是否套闸门（默认只套无现金流锚的三腿）。"""
    p = p or params()
    if p["GATE_ALL"]:
        return True
    return leg in tuple(p["GATE_LEGS"])


def load(leg, mode=None):
    """该腿价格序列（默认场外复权净值）；缺失/刷新失败直接抛错，不静默补位。"""
    return vdata.series_for_leg(leg, mode=mode).dropna()


def fee_of(leg, slip=None):
    """-> (买入费率, 卖出费率)，含滑点。"""
    s = SLIP if slip is None else float(slip)
    b, sl = FEE[leg]
    return float(b) + s, float(sl) + s


# ---------------------------------------------------------------- 2. 指标与象限
def _streak(cond):
    """cond 连续为 True 的计数（遇 False 归零）。"""
    grp = (~cond.fillna(False)).cumsum()
    return cond.fillna(False).astype(int).groupby(grp).cumsum()


def indicators(px, **kw):
    """收盘价 -> 指标表。只用到当日及之前的数据。

    列：close / ma / slope / above / below_days / quad / dev / dd_hi / ready
        + 诊断列 mom6 / mom12（动量法口径，只用于「三口径分歧」统计，不参与交易）
    """
    p = params(**kw)
    c = px.astype("float64")
    out = pd.DataFrame(index=c.index)
    out["close"] = c
    out["ma"] = c.rolling(p["MA_WIN"], min_periods=p["MA_WIN"]).mean()
    out["slope"] = out["ma"] - out["ma"].shift(p["SLOPE_N"])
    out["ready"] = out["ma"].notna() & out["slope"].notna()
    out["above"] = c >= out["ma"]
    # 连续在年线下只从「指标成形」那天起算 —— 预热期的 NaN 不参与计数，
    # 否则第一条 ready 的记录会显示「已跌破 270 日」（实际上是预热期被算了进去）
    out["below_days"] = _streak(~out["above"] & out["ready"]).where(out["ready"], 0)
    out["dev"] = c / out["ma"] - 1.0                      # 年线乖离
    out["dd_hi"] = c / c.rolling(p["MA_WIN"], min_periods=1).max() - 1.0
    up = out["slope"] > 0.0
    quad = np.where(out["above"].to_numpy(dtype=bool),
                    np.where(up.to_numpy(dtype=bool), QUADS[0], QUADS[1]),
                    np.where(up.to_numpy(dtype=bool), QUADS[2], QUADS[3]))
    out["quad"] = np.where(out["ready"].to_numpy(dtype=bool), quad, "")
    out["mom6"] = c / c.shift(126) - 1.0
    out["mom12"] = c / c.shift(252) - 1.0
    return out


def target_of(quad, below_days, cur, p=None):
    """象限 + 连续在年线下的天数 -> (目标带内占比, 理由)。纯函数，回测与每日共用。"""
    p = p or params()
    full, half, obs = p["FULL"], p["HALF"], p["OBSERVE"]
    if p.get("MODE") == "ma_only":              # 对照档：只看 close 在年线上/下
        return (full, "年线上方：满带") if quad in (QUADS[0], QUADS[1]) \
            else (obs, "年线下方：清至观察仓")
    if quad == QUADS[0]:                        # 上涨趋势：两个都朝上
        return full, "年线上方且年线上行：加至满带"
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
    """-> DataFrame(index=净值日)。`state[t]` = t 日收盘成交后的带内占比。

    `decide[t]` ∈ {buy, sell, ""} 是 t 日收盘做出、t+1 日成交的决策；`done[t]` 是 t 日
    成交后的说明。不套闸门的腿恒为满带（`state=1.0`，decide 恒空）。
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

    if not gated(leg, p):
        state[:] = 1.0
        out = pd.DataFrame(dict(state=state, decide=decide, done=done, target=1.0),
                           index=px.index)
        return out.join(I)

    c = I["close"].to_numpy(dtype=float)
    quad = I["quad"].to_numpy(dtype=object)
    ready = I["ready"].to_numpy(dtype=bool)
    below = I["below_days"].to_numpy(dtype=int)

    hold = 1.0 if p["START"] == "full" else 0.0
    last_act, pend, pend_note = -10 ** 9, None, ""
    for t in range(n):
        if pend is not None:                     # 执行上一根的决策（T+1 收盘成交）
            hold = float(pend)
            last_act = t
            done[t] = pend_note
            pend = None
        state[t] = hold
        if not ready[t]:
            continue
        tgt, why = target_of(quad[t], int(below[t]), hold, p)
        tgt_v[t] = tgt
        if abs(tgt - hold) > 1e-9 and t - last_act >= p["ACTION_GAP"]:
            pend, pend_note = tgt, why
            decide[t] = "buy" if tgt > hold else "sell"

    out = pd.DataFrame(dict(state=state, decide=decide, done=done, target=tgt_v),
                       index=px.index)
    return out.join(I)


# ---------------------------------------------------------------- 3. 回测
def run_leg(state, px, fee=(0.0, 0.0)):
    """单腿净值（起点 = 1）。持仓按 `state.shift(1)` 赚当日收益，成交日扣成本。"""
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


def perf(nav):
    """绩效指标（复用主策略 engine.metrics，无风险利率 = 货基 CASH_ANNUAL）。"""
    return engine.metrics(nav)


def exposure(state):
    """带内平均曝光（0–1）。"""
    return float(state.mean()) if len(state) else 0.0


def leg_slice(leg, **kw):
    """-> (px, sm)。样本自年线 + 斜率都成形的第一个交易日起算。"""
    px_full = load(leg)
    sm = state_machine(px_full, leg, **kw)
    start = px_full.index[min(WARMUP, max(len(px_full) - 2, 0))]
    return px_full.loc[start:], sm.loc[start:]


def common_frame():
    """六腿净值的共同窗口（各自年线 + 斜率成形之后再取交集）。"""
    cols = {}
    for k in LEGS:
        px = load(k)
        if len(px) > WARMUP:
            px = px.iloc[WARMUP:]
        cols[k] = px
    return pd.DataFrame(cols).dropna(how="any").sort_index()


def portfolio(frame, states=None, weight=None, fee=False, slip=None, detail=False):
    """组合层净值：Σ w·[state·ret + (1−state)·货基] − 调仓成本。

    `state` 是**带内占比**（0.2/0.5/1.0），不是 pp；`detail=True` 时返回 (nav, 平均带内曝光)。
    """
    weight = weight or W
    ret = frame.pct_change().fillna(0.0)
    cols = list(frame.columns)
    idx = frame.index
    if states is None:
        hold = {k: pd.Series(1.0, index=idx) for k in cols}
    else:
        hold = {}
        for k in cols:
            s = states[k].reindex(idx)
            hold[k] = s.ffill().bfill().fillna(0.0).astype(float).shift(1).fillna(0.0)
    prev = {k: float(hold[k].iloc[0]) for k in cols} if len(idx) else {k: 0.0 for k in cols}
    nav, out, inv = 1.0, [], []
    for i, _d in enumerate(idx):
        if i == 0:
            out.append(nav)
            inv.append(0.0)
            continue
        r, cost, e_sum = 0.0, 0.0, 0.0
        for k in cols:
            e = float(hold[k].iloc[i])
            e_sum += weight[k] * e
            r += weight[k] * (e * float(ret[k].iloc[i]) + (1.0 - e) * CASH_DAILY)
            if fee:
                de = e - prev[k]
                rate = fee_of(k, slip)[0] if de > 0 else fee_of(k, slip)[1]
                cost += weight[k] * abs(de) * rate
            prev[k] = e
        r -= cost
        nav *= (1.0 + r)
        out.append(nav)
        inv.append(e_sum)
    nav_s = pd.Series(out, index=idx, name="nav")
    return (nav_s, float(np.mean(inv))) if detail else nav_s


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
    return perf(sub / sub.iloc[0])


def fmt_pct(x, nd=2):
    return "—" if x is None else f"{x * 100:+.{nd}f}%"


def fmt_x(x, nd=2):
    if x is None:
        return "—"
    if math.isinf(x):
        return "∞"
    return f"{x:.{nd}f}"


def _self_check():
    """快速自检：六腿序列可读、象限四格齐全、仓位只落在 {0, .2, .5, 1} 内。"""
    for k in LEGS:
        px, sm = leg_slice(k)
        st = sm["state"]
        assert len(st) == len(px)
        assert set(np.round(st.unique(), 6)) <= {0.0, 0.2, 0.5, 1.0}
        q = sm.loc[sm["quad"] != "", "quad"]
        cnt = q.value_counts()
        print(f"{k:6s} {rule.LEGS[k]['name']:8s} {px.index[0].date()} ~ {px.index[-1].date()} "
              f"n={len(px):5d} 闸门={'是' if gated(k) else '否'} 当前={st.iloc[-1]:.0%} "
              f"曝光={exposure(st):.0%} 象限日数="
              + " ".join(f"{name}{int(cnt.get(name, 0))}" for name in QUADS))
    print(f"年线 MA{MA_WIN} · 斜率 {SLOPE_N} 日 · 阶梯 {REDUCE_DAYS}/{CLEAR_DAYS} 日 · "
          f"间隔帽 {ACTION_GAP} 日 · 档位 {FULL:g}/{HALF:g}/{OBSERVE:g} · "
          f"作用域 {'全腿' if GATE_ALL else '/'.join(GATE_LEGS)}")


if __name__ == "__main__":
    _self_check()
