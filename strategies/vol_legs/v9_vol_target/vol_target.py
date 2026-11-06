# -*- coding: utf-8 -*-
"""波动资产 · V9 变种：**美股两腿的波动率目标仓位**（vol20 · TV 15% · 月末定仓 + 月中快减/快补）。

本变种**只服务纳指100 与标普500 两条腿**（池 = `("ndx", "spx")`）。

## 0. 两档，一个开关

| 档 | `TRIGGER` | 规则 | 出处 |
|---|---|---|---|
| **默认（fast）** | `"fast"` | 月末定仓 + **月中快减** + **月中快补** | 2026-11-04 由 `research/_probe_opt.py` 的四道闸门筛出 |
| 文档档（month） | `"month"` | 纯月末定仓，文档 S-A 原样 | `策略规格_v1.0.md`（用户说「文档只是参考」，故不再是默认） |

## 1. 规则（R-A01~A06 / R-01~R-04 未变；`fast` 档只加了两个月中触发器）

| 编号 | 规则 | 本实现 |
|---|---|---|
| R-A01 | 每月最后一个交易日收盘，算近 20 日收益的**年化已实现波动率** `vol20 = std(r,20)×sqrt(252)` | `VOL_WIN=20` · `ddof=1` · `month_ends()` |
| R-A02 | 目标波动 `TV = 15%` | `TV=0.15`（10/12/18/22 见回测 §4） |
| R-A03 | 目标权重 `w = min(1, TV / vol20)`，下限 0（不加杠杆、不做空） | `CAP=1.0`，`max(0, ·)` |
| R-A04 | `t` 日信号 → `t+1` 日收盘调仓；**未达 ±0.05 权重变化不交易** | `DRIFT=0.05`（对**当前仓位**算差值，不是对上次目标） |
| R-A05 | 未投资部分按 R-03 计息（**禁止把空仓记 0**） | `CASH_ANNUAL=0.02` |
| R-A06 | 单标的应用；同时持有 SPY/QQQ 时各自独立算 `w` 后按资金占比分配 | `PORTFOLIO` = 两腿各自算 w，再按 `W` 加权 |
| R-01 | 信号 `t` → 成交 `t+1` → **新权重自 `t+2` 起承担收益** | `machine()` 记「当日收盘已持有」，`run_leg()` 再 `shift(1)` |
| R-02 | 成本单边 0.03%，按 `Σ|Δw|` 计 | `COST=0.0003`（场外现实 0.6% 见 §4） |
| R-03 | 空仓年化 2.0%（2001-2026 美国短端近似均值），日计复利 | `CASH_ANNUAL=0.02` |
| R-04 | 权重矩阵 `Σw ≤ 1`，剩余现金；禁止杠杆/做空 | 组合层 `Σ weight×(≤1) ≤ 1` |
| **V9-1** | **快减**（月中）：5 日跌幅 ≤ −5% **且** vol20 ≥ 1.4×TV → 次日降到当日公式值 | `CUT` / `CUT_RET` / `CUT_VOLX` |
| **V9-2** | **快补**（月中）：vol20 ≤ 近 60 日峰值的 70% **且** 站上 MA10 → 次日补到公式值 | `ADD` / `DECAY` / `PEAK_WIN` / `ADD_MA` |

`V9-1/2` 只用**波动率自身的信息**（跌幅门槛是「波动已实现」的另一种说法，MA10 是「恐慌消退」的确认），
**没有引入方向判断** —— 所以它仍然不是择时，只是更快的风险预算调整。

## 2. 为什么加这两个触发器（不是调参调出来的）

月末单点定仓有两个结构性缺陷（见 `research/_probe_fast.md`）：
**减得太慢**（2020-02→03 五周跌完，月末看不到）与**补得太晚**（3-31 那次月末尾恰好踩在波动率峰值上，
在底部砍仓、到 4 月末才补）。`research/_probe_opt.py` 用**预注册的四道闸门**（全样本 / 分半 /
剔除 2020 / 参数邻域）筛了 10 个方向，只有这两个触发器全部通过；13 档阈值邻域内结论不翻转。

代价也写清楚：**2018 年 −4.8pp**（「急跌后快速 V 反」的行情里，快减砍在低位、快补追在高位），
以及调仓次数翻倍（`_probe_opt` 组合层口径 7.2 → 13.4 次/年；`backtest.py` 按两腿合计为 11.0 → 17.5 次/年）。
**它是尾部保险，不是收益引擎**（剔除 2020 后夏普与月度档打平）。

## 3. 与家族其余变种的关系（别混）

| | `v7_us_gate` | `v8_golden_cross` | 本变种 `v9_vol_target` |
|---|---|---|---|
| 池 | 仅 ndx / spx | **仅 ndx / spx** | **仅 ndx / spx** |
| 维度 | 单腿进出时点（MA200） | **快线 vs 慢线（MA50 / MA200）** | **单腿仓位深度**（风险预算） |
| 输入 | 价格与年线的位置 | **两条均线的相对位置** | **只有波动率**（不猜方向） |
| 动作 | 二元（0 / 50% / 100%） | **纯二元（0 / 100%）** | **连续 0~100%** |
| 频率 | 月末（+2 月确认） | **日频** | 月末定仓 + 月中触发（fast 档） |
| 文档出处 | 另一份文档（S-B 的近亲） | 传统技术分析（教科书基线 · 对照组） | S-A（文档排名第 1）+ fast 补丁 |

**核心差别**：v7 问「在不在年线上」，v9 问「眼下这段波动值多少仓位」。
高波动期减仓天然规避了部分下跌，低波动慢牛期满仓吃到涨幅 —— 它不预测方向，只调风险预算。

## 4. 已知边界（先读，再看数字）

- **样本只到 2012/2010**（QDII 场外净值起点）：不含 2000-2002（纳指 −83%）与 2008，
  而那正是「波动率聚集 + 下跌」最典型的窗口 —— 本报告对该场景是**外推**。
- **`cap=1` 是唯一可执行档**：文档 R-04 禁止杠杆；`cap>1` 需两融，成本与额度不在回测里。
- **它改善的是波动、不是收益**：免不了让渡年化，目标指标是 Sharpe / 回撤，不是 CAGR。
- **快减/快补只对「深跌」有效**：2020 波动率冲到 80%（有 82pp 的降仓空间），2018 只到 25%
  （只有 40pp），空间小、来回一折腾就是净亏 —— **同一套补丁在深跌与浅摔里方向相反**。
- **信号价与执行价不是同一条序列**：信号默认美元指数（`.NDX` / `.INX`，与文档口径一致），
  执行是场外 QDII 人民币复权净值（含汇率与跟踪误差）。`SIGNAL="nav"` 可切成净值信号作对照。

模拟结果，非投资建议。
"""
import math
import os
import sys

import numpy as np
import pandas as pd

_HERE = os.path.dirname(os.path.abspath(__file__))
_VL = os.path.dirname(_HERE)
_ROOT = os.path.dirname(_VL)
for _p in (_HERE, _VL, _ROOT):
    if _p not in sys.path:
        sys.path.insert(0, _p)

import data as vdata      # noqa: E402
import engine             # noqa: E402
import rule               # noqa: E402

vdata._otc_stale = lambda: False   # 离线优先：只读本地缓存  # type: ignore[assignment]

LABEL = "V9 美股波动率目标仓位"

# ---------------------------------------------------------------- 1. 参数（唯一权威源）
POOL = ("ndx", "spx")           # 策略标的池：只有这两条
TV = 0.15                       # R-A02 目标波动 15%
VOL_WIN = 20                    # R-A01 近 20 日收益
DRIFT = 0.05                    # R-A04 权重变化 < 5pp 不交易
CAP = 1.0                       # R-A03/R-04 不加杠杆
SIGNAL = "index"                # "index" = 美元指数（文档口径）; "nav" = 人民币净值（对照）
COST = 0.0003                   # R-02 单边 0.03%
CASH_ANNUAL = 0.02              # R-03 空仓 2.0%
CASH_DAILY = (1.0 + CASH_ANNUAL) ** (1.0 / 252) - 1.0
MA_WIN_SB = 200                 # S-B（MA200 月频）对照用均线
PERIODS = 252

# --- 月中补丁（2026-11-04 由 `research/_probe_opt.py` 的四道闸门筛出，见 README §7.1）
TRIGGER = "fast"                # "fast" = 月末定仓 + 月中快减/快补（默认）; "month" = 纯月末（文档原样）
CUT = True                      # 快减开关
CUT_RET = -0.05                 # 快减：5 日收益 ≤ −5%
CUT_VOLX = 1.4                  # 快减：且 vol20 ≥ 1.4 × TV（= 21%）
ADD = "decay"                   # 快补开关："decay" 波动率峰值回落 / None 关闭
DECAY = 0.70                    # 快补：vol20 ≤ 近 60 日峰值的 70%
PEAK_WIN = 60                   # 快补：峰值窗口
ADD_MA = 10                     # 快补：且站上 MA10

W = {k: 1.0 / len(POOL) for k in POOL}          # 组合层：两腿等权（各自 10pp 带）
W_DOC = {"spx": 2.0 / 3.0, "ndx": 1.0 / 3.0}    # 文档 §7「底仓:进攻 ≈ 2:1」（按波动倒数 11.14:22.45）

# 文档 S-A 实测值（QQQ 单标的 · 2002-01-02~2026-11-03 · 24.84 年 · 含分红 · 单边 0.03%）
DOC_REF = dict(ann=0.1231, mdd=-0.3057, sharpe=0.70, sortino=0.99, expo=0.799, trades_per_year=9)


def params(**kw):
    """参数打包：敏感性扫描用 `**{...}` 覆盖，不必改模块常量。"""
    p = dict(POOL=POOL, TV=TV, VOL_WIN=VOL_WIN, DRIFT=DRIFT, CAP=CAP, SIGNAL=SIGNAL,
             COST=COST, CASH_ANNUAL=CASH_ANNUAL, MA_WIN_SB=MA_WIN_SB,
             TRIGGER=TRIGGER, CUT=CUT, CUT_RET=CUT_RET, CUT_VOLX=CUT_VOLX,
             ADD=ADD, DECAY=DECAY, PEAK_WIN=PEAK_WIN, ADD_MA=ADD_MA,
             # 回测口径: 样本末根也算「月末」（否则最后一个月不调仓, 与报告口径不符）;
             # 每日口径必须置 False —— 否则「今天」永远被当成月末, 每天都误报一次定仓决策。
             MONTH_END_LAST=True)
    p.update(kw)
    return p


# ---------------------------------------------------------------- 2. 数据
def load_nav(leg, mode=None):
    """执行口径：场外 QDII 人民币复权净值（含分红再投 / 汇率）。"""
    return vdata.series_for_leg(leg, mode=mode).dropna()


def load_signal(leg, p=None):
    """信号口径：**美元指数**收盘（`.NDX` / `.INX`），缺数据就报错（不静默降级）。"""
    code = rule.LEGS[leg]["signal"]
    return vdata.us_series(code, refresh=False).dropna()


def signal_of(leg, p=None):
    """按 `SIGNAL` 取信号价：指数（文档口径）或人民币净值（对照）。"""
    p = p or params()
    return load_nav(leg) if p["SIGNAL"] == "nav" else load_signal(leg, p)


# ---------------------------------------------------------------- 3. 信号
def vol_series(sig, p=None):
    """年化已实现波动率：`std(r, N, ddof=1) × sqrt(252)`（R-A01 / N-03 只用滚动窗口）。"""
    p = p or params()
    n = int(p["VOL_WIN"])
    return sig.pct_change().rolling(n, min_periods=n).std(ddof=1) * math.sqrt(PERIODS)


def month_ends(idx, include_last=True):
    """每月最后一个交易日。

    `include_last=True`（默认, 回测口径）= 把样本末根也算作月末, 否则最后一个月不产生决策、
    与报告口径不符; **每日操作必须传 False** —— 否则「今天」恒被当成月末, 每天都会误报一次
    月末定仓（2026-11-06 接入板块2 时踩到: 非月末日也报「→ 48%（月末）」）。
    """
    idx = pd.DatetimeIndex(idx)
    out = [idx[i] for i in range(len(idx) - 1)
           if (idx[i].year, idx[i].month) != (idx[i + 1].year, idx[i + 1].month)]
    if include_last and len(idx):
        out.append(idx[-1])
    return out


def raw_weight(vol, p=None):
    """`w = min(cap, TV / vol)`，下限 0（R-A03）。"""
    p = p or params()
    if not np.isfinite(vol) or vol <= 0:
        return None
    return float(max(0.0, min(float(p["CAP"]), float(p["TV"]) / float(vol))))


def decisions(leg, **kw):
    """-> [{date, close, vol, raw, target, traded}]，每个有效月末一条。

    `raw` = 当月按公式算出的目标权重；`target` = **实际执行**的权重 —— 与**当前仓位**
    差不足 `DRIFT` 就不交易（R-A04），此时 target 沿用旧仓位。
    预热期内（vol 为 NaN）不出决策，仓位维持 1.0（= 起点即满仓，与买入持有同口径）。
    """
    p = params(**kw)
    sig = signal_of(leg, p)
    vol = vol_series(sig, p)
    hold = 1.0
    out = []
    for d in month_ends(sig.index):
        v = float(vol.loc[d])
        raw = raw_weight(v, p)
        if raw is None:
            continue
        traded = abs(raw - hold) >= float(p["DRIFT"]) - 1e-12
        tgt = raw if traded else hold
        hold = tgt
        out.append(dict(date=d, close=float(sig.loc[d]), vol=v, raw=float(raw),
                        target=float(tgt), traded=bool(traded)))
    return out


def state_series(nav_index, dec):
    """把月末决策映射到净值日历：**决策日的次一根净值日成交**，其余时间保持。

    这正是 R-01 的 `shift(2)`：信号 t 日收盘 → t+1 收盘成交 → 新权重自 t+2 起承担收益。
    （`run_leg` 里再 `state.shift(1)`，两步合起来就是文档的「整体 shift(2)」。）
    起点口径：第一根净值日即按「已满仓」计，与买入持有基准一致，不另计建仓费 ——
    所以**早于净值起点的决策一律丢弃**（那是信号序列的预热段，还没有可交易的净值）。
    """
    idx = pd.DatetimeIndex(nav_index)
    vals = {}
    for x in dec:
        if x["date"] < idx[0]:
            continue
        pos = int(idx.searchsorted(x["date"], side="right"))
        if pos < len(idx):
            vals[idx[pos]] = x["target"]
    s = pd.Series(vals, dtype="float64").sort_index().reindex(idx).ffill()
    return s.fillna(1.0)


def decisions_ma200(leg, **kw):
    """S-B（文档 §3）月频对照：`close > MA200 → 1`，否则 0；月末判断、次一交易日成交。

    R-B04 明确推荐月频（换手从 163 次降到 39 次，CAGR 反而高 0.7pp）。它只作**对照**，
    不是本变种的一部分 —— 本变种只做波动率目标。
    """
    p = params(**kw)
    sig = signal_of(leg, p)
    n = int(p["MA_WIN_SB"])
    ma = sig.rolling(n, min_periods=n).mean()
    out = []
    for d in month_ends(sig.index):
        m = float(ma.loc[d])
        if not np.isfinite(m):
            continue
        out.append(dict(date=d, close=float(sig.loc[d]), ma=m,
                        target=1.0 if float(sig.loc[d]) > m else 0.0))
    return out


def machine(sig, p=None):
    """逐日仓位状态机（**本变种的唯一实现**）。

    返回 `(s, events)`：
    - `s[d]` = **d 日收盘时已经持有**的仓位（d 日的信号只改 d+1 的仓位，`run_leg` 再 `shift(1)`
      → 新权重自 t+2 起承担收益，与 R-01 一致）；
    - `events` = 每次改变仓位的决策（`date` / `target` / `reason`：month|cut|add）。

    三档触发：
    1. **月末定仓**（R-A01~A04）：`|raw - hold| ≥ drift` 才调；
    2. **快减**（月中）：5 日跌幅 ≤ `CUT_RET` **且** vol20 ≥ `CUT_VOLX×TV` → 立刻降到当日公式值；
    3. **快补**（月中）：vol20 ≤ `DECAY × 近 PEAK_WIN 日峰值` **且**站上 MA(`ADD_MA`) → 立刻补到公式值。

    `TRIGGER="month"` 关掉 2/3，即文档 S-A 原样。
    """
    p = p or params()
    tv, cap = float(p["TV"]), float(p["CAP"])
    vol = vol_series(sig, p)
    raw = (tv / vol).clip(upper=cap)
    peak = vol.rolling(int(p["PEAK_WIN"]), min_periods=20).max()
    ma = sig.rolling(int(p["ADD_MA"])).mean()
    ret5 = sig.pct_change(5)
    ends = set(month_ends(sig.index, include_last=bool(p.get("MONTH_END_LAST", True))))
    drift = float(p["DRIFT"])
    fast = str(p["TRIGGER"]) == "fast"
    cut_on = fast and bool(p["CUT"])
    add_on = fast and bool(p["ADD"])

    hold, vals, events = 1.0, [], []
    for d in sig.index:
        vals.append(hold)                                  # d 日收盘时（旧仓）
        r = float(raw.loc[d]) if np.isfinite(raw.loc[d]) else None
        if r is None:
            continue
        tgt, why = hold, None
        if d in ends and abs(r - hold) >= drift - 1e-12:
            tgt, why = r, "month"
        if cut_on:                                         # 快减：急跌 + 波动已高
            v5 = float(ret5.loc[d]) if np.isfinite(ret5.loc[d]) else 0.0
            if r < tgt - drift and v5 <= float(p["CUT_RET"]) \
                    and float(vol.loc[d]) >= float(p["CUT_VOLX"]) * tv:
                tgt, why = r, "cut"
        if add_on:                                         # 快补：恐慌消退 + 站上短均线
            pk = float(peak.loc[d]) if np.isfinite(peak.loc[d]) else np.nan
            m = float(ma.loc[d]) if np.isfinite(ma.loc[d]) else np.inf
            if np.isfinite(pk) and float(vol.loc[d]) <= float(p["DECAY"]) * pk \
                    and float(sig.loc[d]) > m and r > tgt + drift:
                tgt, why = r, "add"
        if why:
            events.append(dict(date=d, target=float(tgt), raw=float(r), reason=why))
        hold = tgt
    return pd.Series(vals, index=sig.index, dtype="float64"), events


def state_of(leg, p=None):
    """-> (nav, 净值日历上的仓位, events)。状态机跑在信号日历上，再前值填充到净值日历。"""
    p = p or params()
    nav = load_nav(leg)
    sm, ev = machine(signal_of(leg, p), p)
    return nav, sm.reindex(nav.index).ffill().fillna(1.0), ev


def build(leg, kind="vt", **kw):
    """-> (nav, state, dec)。

    `kind="vt"` 本变种（按 `TRIGGER`）/ `"vt_month"` 强制文档月度档 /
    `"ma200"` S-B 对照 / `"hold"` 买入持有。
    """
    p = params(**kw)
    if kind == "hold":
        nav = load_nav(leg)
        return nav, pd.Series(1.0, index=nav.index), []
    if kind == "ma200":
        nav = load_nav(leg)
        dec = decisions_ma200(leg, **p)
        return nav, state_series(nav.index, dec), dec
    if kind == "vt_month":
        p = dict(p, TRIGGER="month")
    return state_of(leg, p)


# ---------------------------------------------------------------- 4. 回测
def run_leg(state, nav, cost=None, cash=None, periods=PERIODS):
    """单腿净值（起点 = 1）。

    `state[t]` = 第 t 根**收盘成交后**的仓位 → 它只能赚 **t+1** 的收益（`shift(1)`），
    否则就是「用当日收盘才知道的仓位吃当日收益」的前视（R-01 的 t+2 生效）。
    未投部分按 `cash` 逐日计息；成本按 `|Δstate|` 单边扣。
    """
    c = COST if cost is None else float(cost)
    rf = CASH_ANNUAL if cash is None else float(cash)
    cash_d = (1.0 + rf) ** (1.0 / periods) - 1.0
    ret = nav.pct_change().fillna(0.0)
    hold = state.astype(float).shift(1)
    if len(hold):
        hold.iloc[0] = float(state.iloc[0])
    d = state.astype(float).diff().fillna(0.0).abs()
    r = hold * ret + (1.0 - hold) * cash_d - d * c
    return (1.0 + r).cumprod()


def portfolio(navs, states, weight=None, cost=None, cash=None, periods=PERIODS):
    """两腿组合净值（默认等权）。逐日线性加权 + 各腿成本（R-04: Σw ≤ 1，其余计息）。"""
    weight = weight or W
    cols = list(navs)
    idx = navs[cols[0]].index
    c = COST if cost is None else float(cost)
    rf = CASH_ANNUAL if cash is None else float(cash)
    cash_d = (1.0 + rf) ** (1.0 / periods) - 1.0
    rets = {k: navs[k].pct_change().fillna(0.0) for k in cols}
    sts = {k: states[k].astype(float).reindex(idx).ffill() for k in cols}
    eff = {k: sts[k].shift(1) for k in cols}
    for k in cols:
        if len(eff[k]):
            eff[k].iloc[0] = float(sts[k].iloc[0])
    nav, out, inv = 1.0, [], []
    for i, _d in enumerate(idx):
        r, e_sum = 0.0, 0.0
        for k in cols:
            e = float(eff[k].iloc[i])                     # 昨日收盘的仓位赚今日收益
            r += weight[k] * (e * float(rets[k].iloc[i]) + (1.0 - e) * cash_d)
            e_sum += weight[k] * e
            if i > 0:                                     # 今日收盘成交的仓位变化才付成本
                r -= weight[k] * abs(float(sts[k].iloc[i]) - float(sts[k].iloc[i - 1])) * c
        nav *= (1.0 + r)
        out.append(nav)
        inv.append(e_sum)
    return pd.Series(out, index=idx, name="nav"), float(np.mean(inv))


def perf(nav):
    """绩效指标（复用 engine.metrics：年化 / 波动 / 最大回撤 / 夏普）。"""
    return engine.metrics(nav)


def calmar(nav, mdd=None):
    m = engine.metrics(nav)
    d = m["mdd"] if mdd is None else mdd
    return m["ann"] / abs(d) if d else float("nan")


def mdd_sampled(nav, freq="ME"):
    """**月末采样**的最大回撤（文档表 1 的度量口径，便于与文档逐格对照）。"""
    me = nav.resample(freq).last().dropna()
    if not len(me):
        return float("nan")
    return float((me / me.cummax() - 1.0).min())


def actions(state, eps=1e-9):
    """把仓位序列折成调仓动作（成交日 = 决策日的次一交易日）。"""
    s = state.astype(float).to_numpy()
    idx = state.index
    out, prev = [], 0
    for i in range(1, len(s)):
        if abs(s[i] - s[i - 1]) > eps:
            out.append(dict(date=idx[i], frm=float(s[i - 1]), to=float(s[i]),
                            kind="buy" if s[i] > s[i - 1] else "sell", gap=i - prev))
            prev = i
    return out


def exposure(state):
    """平均仓位（0–1）。"""
    return float(state.astype(float).mean()) if len(state) else 0.0


def turnover(state):
    """累计换手（倍本金）= Σ|Δ仓位|。"""
    return float(state.astype(float).diff().fillna(0.0).abs().sum())


def n_trades(state):
    """调仓次数（含首次建仓以外的每一次变动）。"""
    return int((state.astype(float).diff().fillna(0.0).abs() > 1e-12).sum())


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


def window_nav(nav, start, end):
    """按日期窗口切片（用于压力窗口）。"""
    return nav.loc[(nav.index >= pd.Timestamp(start)) & (nav.index <= pd.Timestamp(end))]


def weight_at(dec, when):
    """某个日期**收盘时**已生效的仓位（= 最近一条「成交日 ≤ when」的决策目标）。"""
    tgt = 1.0
    for x in dec:
        if x["date"] < pd.Timestamp(when):
            tgt = float(x["target"])
    return tgt


def level_name(x):
    """仓位 -> 中文档位名（图上标注与每日报告共用）。"""
    x = float(x)
    if x >= 0.995:
        return "满仓"
    if x <= 0.005:
        return "清仓"
    return f"{x:.0%}"


def fmt_pct(x, nd=2):
    return "—" if x is None else f"{x * 100:+.{nd}f}%"


def _self_check():
    """快速自检：两条腿的样本、当前仓位、事件条数、两档差异。"""
    for k in POOL:
        nav, st, dec = build(k)
        assert float(st.min()) >= -1e-12 and float(st.max()) <= CAP + 1e-12, (k, st.min(), st.max())
        last = dec[-1]
        n_cut = sum(1 for x in dec if x["reason"] == "cut")
        n_add = sum(1 for x in dec if x["reason"] == "add")
        _n2, st_m, _d2 = build(k, kind="vt_month")
        print(f"{k} {rule.LEGS[k]['name']}: 净值 {nav.index[0].date()}~{nav.index[-1].date()} "
              f"n={len(nav)} · 当前 {st.iloc[-1]:.0%}（{level_name(st.iloc[-1])}；月文档 "
              f"{st_m.iloc[-1]:.0%}）· 事件 {len(dec)} 条（快减 {n_cut} / 快补 {n_add}）· "
              f"末次 {last['date'].date()} {last['reason']} → {last['target']:.0%}")
    print(f"触发档 {TRIGGER} · TV {TV:.0%} · vol{VOL_WIN} · 漂移 {DRIFT:.0%} · cap {CAP:.1f} · "
          f"信号 {SIGNAL} · 单边成本 {COST:.2%} · 空仓 {CASH_ANNUAL:.2%}")
    if TRIGGER == "fast":
        print(f"快减: 5 日 ≤ {CUT_RET:.0%} 且 vol20 ≥ {CUT_VOLX:.1f}×TV · "
              f"快补: vol20 ≤ {DECAY:.0%}×{PEAK_WIN}日峰值 且 站上 MA{ADD_MA}")


if __name__ == "__main__":
    _self_check()
