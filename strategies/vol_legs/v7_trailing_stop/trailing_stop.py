# -*- coding: utf-8 -*-
"""波动资产 · V7 变种：**回撤止损**（跌破近 1 年高 X% 减仓 → 收复 MA200 回满）。

一句话：**不猜顶、不择时进出，只在「从持仓期高点回撤超过 X%」时把该腿目标带砍到 floor，
等价格重新站回 MA200（趋势修复）再回满。** 它是**崩盘保险**，不是收益引擎。
（回撤锚点是标准移动止损口径：锚点随（再）入场重置为当时高点，不是固定回看 1 年 ——
否则刚回满时价格仍低于旧高点，会被同一段回撤反复触发。）

## 1. 为什么是这个指标（而不是趋势闸门 v5）

美股/A股的大回撤几乎都是**快跌**（2020 几周 −34%、2022 阴跌、2015 三个月 −45%）：
- 慢均线（MA250 四象限，v5）要等趋势确认，确认时坑已经挖完 —— 用它躲回撤基本无效
  （实测：把仓位从 100% 软降到 70~80%，回撤几乎不动）；
- **回撤止损**对快跌反应快：价格从近 1 年高跌掉 X% 立刻减仓，收复 MA200 再进，
  正好吃「快跌段」的亏、留住「趋势修复后」的涨。

## 2. 规则（可执行版）

对每条腿（默认七腿全套装，`GATE_ALL=True`）：

| 环节 | 规则 |
|---|---|
| 触发减仓 | `close / 持仓期高点 − 1 < −STOP_PCT`（默认 −15%；锚点在（再）入场时重置为当时收盘，即标准移动止损）→ 该腿目标带降到 `FLOOR`（默认 50%） |
| 触发回满 | 减仓态下 `close > MA(REENTRY_MA=200)`（趋势修复）→ 回到满带 100% |
| 预热 | `MA200` 成形前不交易（`ready=False`），保持 `START`（默认满带） |
| 成交 | T 日盘后算信号、**T+1 收盘成交**（与 v1~v6 同一纪律） |
| 空仓计息 | 减仓部分按货基 `rule.CASH_ANNUAL` 逐日计息 |
| 频率帽 | 同一腿两次调仓最小间隔 `ACTION_GAP`（默认 1 个交易日，防同日翻烧饼） |

## 3. 仓位语义

不是 0/1 择时，而是**目标带上的闸门**：`state ∈ {1.0, FLOOR}` 是「占本腿目标带的比例」。
带位仍由主策略决定（`rule.LEGS[leg]["band"]`），本变种只回答「这条带今天露多少」。
所以基准对照是**静态带（恒持满带）**，不是「买入持有」（虽然逐腿也给买入持有列）。

## 4. 已知边界（写在参数里，不是写在注释里）

- **不是「降回撤」的万能药**：它只对「快跌 + 之后趋势修复」的腿有效。对**频繁围绕 −15%
  来回插针**的腿（沪深300、纳指100 的部分窗口），它会「卖在坑里、追在高处」，扣掉申赎费后
  反而更差（见回测逐腿表）。这是规则的适用边界，不是实现 bug。
- **参数敏感**：`STOP_PCT` 越窄越能砍回撤，但换手/费用越高；`gold` 是商品、波动结构不同，
  默认放宽到 20%（`STOP_BY_LEG`）。
- **费用是生死线**：A 类场外（申购 0.10% + 赎回 0.50%）下，止损频繁的腿会被费用吃掉；
  场内 ETF / 免佣账户下净收益会明显改善（回测同时给「含费」与「不计费」两列）。

## 5. 与其它变种的关系

同属「逐腿进出时点」层（与 v1/v2/v3/v5 同层），但**方向相反于突破型**：
v1/v2/v3 是「涨起来才追」，本变种是「跌下去才躲」。可与主策略并存
（主策略定带位、本变种定「这条带今天露多少」），但**不要与突破型叠加在同一份额度上**。

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
_VL = os.path.dirname(_HERE)                     # strategies/vol_legs
_ROOT = os.path.dirname(os.path.dirname(_VL))    # 仓库根
for _p in (_HERE, _VL):
    if _p not in sys.path:
        sys.path.insert(0, _p)

import data as vdata    # noqa: E402
import engine           # noqa: E402
import rule             # noqa: E402

LABEL = "V7 回撤止损"

# ---------------------------------------------------------------- 1. 参数（唯一权威源）
STOP_PCT = 0.15         # 从近 HI_WIN 日最高收盘回撤超过它 → 减仓
HI_WIN = 250            # 「近 1 年高」的窗口（交易日）
FLOOR = 0.5             # 减仓后保留的带内比例（0.5 = 半仓；设 0.0 = 清仓）
REENTRY_MA = 200        # 收复它 → 回满（趋势修复线）
MIN_OBS = 20            # 近高窗口的最小样本（否则不回撤判定）
ACTION_GAP = 1          # 同一腿两次调仓最小间隔（交易日）
START = "full"          # "full" = 带内满仓起步（实盘口径）/ "flat" = 空仓起步
GATE_ALL = True         # 默认七腿全套装；False 时只套 GATE_LEGS
GATE_LEGS = ("a500", "hs300", "ndx", "spx", "kc50", "hkt", "gold")  # 无现金流锚/高波动子集的历史默认

WARMUP = REENTRY_MA     # MA200 成形前的样本不进回测
SLIP = 0.001            # 单边滑点 0.1%（与 v1~v6 同一口径）
CASH_ANNUAL = rule.CASH_ANNUAL
CASH_DAILY = (1.0 + CASH_ANNUAL) ** (1.0 / 252) - 1.0

LEGS = rule.LEG_ORDER
W = {k: float(rule.LEGS[k]["band"]) / float(rule.SLEEVE_PP) for k in LEGS}   # 带权归一化(Σ=1)

# 场外申赎成本（(买入, 卖出) 单边费率），与 v1/v2/v3/v5/v6 同一张表：
FEE = {
    "a500": (0.0, 0.0),
    "hs300": (0.001, 0.005),
    "ndx": (0.001, 0.005),
    "spx": (0.001, 0.005),
    "kc50": (0.0, 0.0),
    "hkt": (0.0, 0.0),
    "gold": (0.001, 0.005),
}

# 逐腿止损宽度覆盖（默认全用 STOP_PCT/FLOOR；仅黄金因商品波动结构不同放宽到 20%）。
# 命名 STOP_BY_LEG，与 v5 的 TREND_READ「逐腿读数」是同一类设计：只改读数，不改规则。
STOP_BY_LEG = {
    "gold": (0.20, 0.5),
}

# 逐腿「止损是否适合这条腿」的实测结论（供每日/报告引用，不是代码分支）。
STOP_READ = {
    "a500": dict(label="止损 15% 清仓", stats="+4.79% / −38.79% · 按止损距离定量后",
                 why="A 股宽基波动大、2015 式快跌多；止损对快跌有效，但需注意样本前段是桥接。"),
    "hs300": dict(label="止损 15% 半仓（本腿不建议）", stats="+1.53% / −43.42%（vs 满带 +2.66% / −43.43%）",
                  why="围绕 −15% 频繁插针，止损卖在坑里、追在高处；扣费后几乎无用，甚至有害。"),
    "ndx": dict(label="止损 15% 半仓（本腿不建议 · 费用敏感）", stats="+14.15% / −30.27%（vs 满带 +16.51% / −29.47%）",
                why="美股慢牛急跌，止损对 2020 式快跌有效、但插针也多；A 类场外费下净收益为负，低费 ETF 下才转正。"),
    "spx": dict(label="止损 15% 半仓", stats="+10.98% / −24.58%（vs 满带 +13.27% / −30.50%）",
                why="宽基里最适用的一档：回撤压缩 ~6pp、年化让渡 ~2pp，是典型的「崩盘保险」性价比。"),
    "kc50": dict(label="止损 15% 半仓", stats="+8.47% / −24.89%（vs 满带 −1.83% / −58.34%）",
                 why="高波动、快跌多，止损把回撤从 −58% 压到 −25%、年化还转正 —— 本变种最强的一条腿。"),
    "hkt": dict(label="止损 15% 半仓", stats="−7.14% / −45.75%（vs 满带 −9.25% / −53.89%）",
                why="震荡下行里止损能少亏、压回撤；但绝对值仍为负，属「少亏」不是「盈利」。"),
    "gold": dict(label="止损 20% 半仓", stats="+7.95% / −32.01%（vs 满带 +8.77% / −32.78%）",
                 why="商品波动结构不同，15% 太紧会频繁误伤；放宽到 20% 才回到「中性」。"),
}

QUADS = None  # 兼容占位（本变种无四象限）


def params(**kw):
    """参数打包：回测敏感性扫描时用 `**{...}` 覆盖，不必改模块常量。"""
    p = dict(STOP_PCT=STOP_PCT, HI_WIN=HI_WIN, FLOOR=FLOOR, REENTRY_MA=REENTRY_MA,
             MIN_OBS=MIN_OBS, ACTION_GAP=ACTION_GAP, START=START,
             GATE_ALL=GATE_ALL, GATE_LEGS=GATE_LEGS, STOP_BY_LEG=STOP_BY_LEG)
    p.update(kw)
    return p


def gated(leg, p=None):
    """该腿是否套闸门（默认 GATE_ALL=True，七腿全套装）。"""
    p = p or params()
    if p["GATE_ALL"]:
        return True
    return leg in tuple(p["GATE_LEGS"])


def stop_of(leg, p=None):
    """-> (stop_pct, floor)。该腿套止损则返回参数，否则 None。"""
    p = p or params()
    if not gated(leg, p):
        return None
    tbl = p.get("STOP_BY_LEG") or {}
    if leg in tbl:
        v = tbl[leg]
        return None if v is None else (float(v[0]), float(v[1]))
    return float(p["STOP_PCT"]), float(p["FLOOR"])


def load(leg, mode=None):
    """该腿价格序列（默认场外复权净值）；缺失直接抛错，不静默补位。"""
    return vdata.series_for_leg(leg, mode=mode).dropna()


def fee_of(leg, slip=None):
    """-> (买入费率, 卖出费率)，含滑点。"""
    s = SLIP if slip is None else float(slip)
    b, sl = FEE[leg]
    return float(b) + s, float(sl) + s


# ---------------------------------------------------------------- 2. 指标与状态机
def indicators(px, **kw):
    """收盘价 -> 指标表。只用到当日及之前的数据。

    列：close / hi（近 HI_WIN 日最高收盘）/ dd_hi（距该高的回撤）/ ma_re（MA200）/ ready
    """
    p = params(**kw)
    c = px.astype("float64")
    out = pd.DataFrame(index=c.index)
    out["close"] = c
    hi = c.rolling(p["HI_WIN"], min_periods=p["MIN_OBS"]).max()
    out["hi"] = hi
    out["dd_hi"] = c / hi - 1.0
    out["ma_re"] = c.rolling(p["REENTRY_MA"], min_periods=p["REENTRY_MA"]).mean()
    out["ready"] = hi.notna() & out["ma_re"].notna()
    return out


def state_machine(px, leg, **kw):
    """-> DataFrame(index=净值日)。`state[t]` = t 日收盘成交后的带内占比。

    `decide[t]` ∈ {buy, sell, ""} 是 t 日收盘做出、t+1 日成交的决策；`done[t]` 是 t 日
    成交后的说明。不套门/无止损的腿恒为满带（state=1.0）。
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

    sf = stop_of(leg, p)
    if sf is None:
        state[:] = 1.0
        out = pd.DataFrame(dict(state=state, decide=decide, done=done, target=1.0),
                           index=px.index)
        return out.join(I)

    stop, floor = sf
    c = I["close"].to_numpy(dtype=float)
    mv = I["ma_re"].to_numpy(dtype=float)
    ready = I["ready"].to_numpy(dtype=bool)

    hold = 1.0 if p["START"] == "full" else 0.0
    peak = c[0] if hold >= 1.0 - 1e-9 else np.nan   # 持仓期高点（回撤锚点）
    last_act, pend, pend_note = -10 ** 9, None, ""
    for t in range(n):
        if pend is not None:                     # 执行上一根的决策（T+1 收盘成交）
            hold = float(pend)
            last_act = t
            done[t] = pend_note
            if hold >= 1.0 - 1e-9:
                peak = c[t]                      # (再)入场：回撤锚点重置为持仓期高点
            else:
                peak = np.nan
            pend = None
        state[t] = hold
        if not ready[t]:
            continue
        inpos = hold >= 1.0 - 1e-9
        if inpos:
            peak = max(peak, c[t]) if peak == peak else c[t]
            dd = c[t] / peak - 1.0
            if dd < -stop and t - last_act >= p["ACTION_GAP"]:
                pend, pend_note = floor, f"从持仓高点回撤 {dd:.1%} → 降到 {floor:.0%}"
                decide[t] = "sell"
        else:
            if mv[t] == mv[t] and c[t] > mv[t] and t - last_act >= p["ACTION_GAP"]:
                pend, pend_note = 1.0, f"收复 MA{p['REENTRY_MA']} → 回满"
                decide[t] = "buy"
        tgt_v[t] = 1.0 if hold >= 1.0 - 1e-9 else floor

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
            miss.append(val[j] / val[i] - 1.0)
    return dict(n=n, avg_miss=(float(np.mean(miss)) if miss else 0.0),
                total_miss=(float(np.sum(miss)) if miss else 0.0))


def perf(nav):
    """绩效指标（复用主策略 engine.metrics，无风险利率 = 货基 CASH_ANNUAL）。"""
    return engine.metrics(nav)


def exposure(state):
    """带内平均曝光（0–1）。"""
    return float(state.mean()) if len(state) else 0.0


def leg_slice(leg, **kw):
    """-> (px, sm)。样本自 MA200 成形的第一个交易日起算。"""
    px_full = load(leg)
    sm = state_machine(px_full, leg, **kw)
    start = px_full.index[min(WARMUP, max(len(px_full) - 2, 0))]
    return px_full.loc[start:], sm.loc[start:]


def common_frame():
    """七腿净值的共同窗口（各自 MA200 成形之后再取交集）。"""
    cols = {}
    for k in LEGS:
        px = load(k)
        if len(px) > WARMUP:
            px = px.iloc[WARMUP:]
        cols[k] = px
    return pd.DataFrame(cols).dropna(how="any").sort_index()


def portfolio(frame, states=None, weight=None, fee=False, slip=None, detail=False):
    """组合层净值：Σ w·[state·ret + (1−state)·货基] − 调仓成本。"""
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
    """快速自检：七腿序列可读、仓位只落在 {1.0, floor} 内。"""
    for k in LEGS:
        px, sm = leg_slice(k)
        st = sm["state"]
        sf = stop_of(k)
        allowed = {1.0} | ({0.0} if sf is None else {round(sf[1], 6)})
        assert set(np.round(st.unique(), 6)) <= allowed, (k, st.unique())
        print(f"{k:6s} {rule.LEGS[k]['name']:8s} {px.index[0].date()} ~ {px.index[-1].date()} "
              f"n={len(px):5d} 止损={'无' if sf is None else f'{sf[0]:.0%}/{sf[1]:.0%}'} "
              f"当前={st.iloc[-1]:.0%} 曝光={exposure(st):.0%} 调仓={len(actions(st))}")
    print(f"止损 {STOP_PCT:.0%} · 近高 {HI_WIN} 日 · 回满 MA{REENTRY_MA} · 间隔帽 {ACTION_GAP} 日 · "
          f"作用域 {'全腿' if GATE_ALL else '/'.join(GATE_LEGS)}")


if __name__ == "__main__":
    _self_check()
