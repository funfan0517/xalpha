# -*- coding: utf-8 -*-
"""波动资产 · V9 变种：**全七腿的波动率目标仓位**（vol20 · TV 15% · 月末定仓 + 月中快减/快补）。
（2026-11-17 由 ndx/spx 两腿扩到全部七腿；规则源自两腿文档，新增腿按同一规则外推。）

规则对**全七腿通用**（`POOL = rule.LEG_ORDER`）；**现阶段重点跑纳指100 与标普500 两条腿** ——
实盘执行走场外 QDII A 类（`270042` / `017641`），看板 ★ 也只标这两腿，其余五腿为同规则外推的参考读数。
（v7 / v8 / v10 同理：规则本身与「重点腿」是两件事，别把当前执行范围读成策略适用范围。）

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
| R-A05 | 未投资部分按 R-03 计息（**禁止把空仓记 0**） | `CASH_ANNUAL=rule.CASH_ANNUAL`（1.40%） |
| R-A06 | 单标的应用；同时持有 SPY/QQQ 时各自独立算 `w` 后按资金占比分配 | `PORTFOLIO` = 各腿独立算 w，再按 `W` 等权加权（七腿）；`W_DOC` 是文档 §7 的标普:纳指 2:1 两腿子集口径 |
| R-01 | 信号 `t` → 成交 `t+1` → **新权重自 `t+2` 起承担收益** | `machine()` 记「当日收盘已持有」，`run_leg()` 再 `shift(1)` |
| R-02 | 成本单边 **0.10%**（仓库惯例，与 v7/v8/v10 同口径），按 `Σ|Δw|` 计 | `COST=0.001`（文档原口径 0.03% 与场外现实 0.6% 均保留为敏感性档） |
| R-03 | 空仓年化 **1.40%**（货基惯例，与 v7/v8/v10 同口径），日计复利 | `CASH_ANNUAL=rule.CASH_ANNUAL`（文档原口径 2.0% 保留为敏感性档） |
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
以及调仓次数翻倍（`_probe_opt` 组合层口径 7.2 → 13.4 次/年；`backtest.py` 扩池前的标普+纳指两腿合计为 11.0 → 17.5 次/年）。
**它是尾部保险，不是收益引擎**（剔除 2020 后夏普与月度档打平）。

## 3. 与家族其余变种的关系（别混）

| | `v7_us_gate` | `v8_golden_cross` | 本变种 `v9_vol_target` |
|---|---|---|---|
| 池 | 全七腿（重点美股两腿） | **全七腿（重点美股两腿）** | **全七腿（重点美股两腿）** |
| 维度 | 单腿进出时点（MA200） | **快线 vs 慢线（MA50 / MA200）** | **单腿仓位深度**（风险预算） |
| 输入 | 价格与年线的位置 | **两条均线的相对位置** | **只有波动率**（不猜方向） |
| 动作 | 二元（0 / 50% / 100%） | **纯二元（0 / 100%）** | **连续 0~100%** |
| 频率 | 月末（+2 月确认） | **日频** | 月末定仓 + 月中触发（fast 档） |
| 文档出处 | 另一份文档（S-B 的近亲） | 传统技术分析（教科书基线 · 对照组） | S-A（文档排名第 1）+ fast 补丁 |

**核心差别**：v7 问「在不在年线上」，v9 问「眼下这段波动值多少仓位」。
高波动期减仓天然规避了部分下跌，低波动慢牛期满仓吃到涨幅 —— 它不预测方向，只调风险预算。

## 3.1 三个可插拔开关（2026-11-18 新增；**当前按腿默认：标普 010，其余 111**）

三个 0/1 开关接在 `params()` 上，`leg_params()` 负责按腿套用默认配置；八种组合的对照见
`research/_probe_switches.md`，当前定稿的理由见 `README.md` §2。

| 开关 | 标普 | 其余六腿 | 接的是什么 | 落点 |
|---|---|---|---|---|
| `ADAPTIVE=1` | 关 | 开 | v10 自适应波动层：Kaufman ER(20) 带滞回切 TV 15%/10% | `raw = TV(ER)/vol`；快减 vol 门槛也用当日 TV |
| `GATE=1` | 开 | 开 | v7 年线闸门：月末 MA200、2 月确认、破位 ≥3% 免确认；纳指 0 / 标普 50% | `raw ×= gate`（月末决定，次日仓位生效） |
| `XCROSS=1` | 关 | 开 | v8 金叉：MA50 > MA200 才允许持仓 | 死叉日收盘 `state = 0`（次日即无敞口，比月末闸门快一天） |

模块常量 `ADAPTIVE/GATE/XCROSS` 仍是 `False`，只服务显式传参的对照档；`build()` 经 `leg_params()`
自动按腿覆盖。**想复现旧口径**（三开关全关）就显式传
`ADAPTIVE=False, GATE=False, XCROSS=False`。开 `GATE`/`XCROSS` 后 v9 第一次引入方向判断，
**已不是「纯风险预算」的原版 v9** —— 这是 2026-11-18 用户指定的口径变更。

## 4. 已知边界（先读，再看数字）

- **样本只到 2012/2010**（QDII 场外净值起点）：不含 2000-2002（纳指 −83%）与 2008，
  而那正是「波动率聚集 + 下跌」最典型的窗口 —— 本报告对该场景是**外推**。
- **`cap=1` 是唯一可执行档**：文档 R-04 禁止杠杆；`cap>1` 需两融，成本与额度不在回测里。
- **它改善的是波动、不是收益**：免不了让渡年化，目标指标是 Sharpe / 回撤，不是 CAGR。
- **快减/快补只对「深跌」有效**：2020 波动率冲到 80%（有 82pp 的降仓空间），2018 只到 25%
  （只有 40pp），空间小、来回一折腾就是净亏 —— **同一套补丁在深跌与浅摔里方向相反**。
- **信号与回测价格同源**：都是该市场真实指数（`.NDX` / `.INX`，2005 起）；
  执行走场外 QDII A 类（`270042` / `017641`），汇率与跟踪误差只在实盘对账时另算。

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


LABEL = "V9 美股波动率目标仓位"

# ---------------------------------------------------------------- 1. 参数（唯一权威源）
POOL = tuple(rule.LEG_ORDER)    # 策略标的池：全部七腿（2026-11-17 由用户要求从 ndx/spx 两腿扩展）
TV = 0.15                       # R-A02 目标波动 15%
VOL_WIN = 20                    # R-A01 近 20 日收益
DRIFT = 0.05                    # R-A04 权重变化 < 5pp 不交易
CAP = 1.0                       # R-A03/R-04 不加杠杆
SIGNAL = "index"                # 唯一口径：真实指数（保留字段名以兼容 params()）
COST = 0.001                    # R-02 单边 0.10%（2026-11-17 由文档原口径 0.03% 改为仓库惯例，与 v7/v8/v10 一致）
CASH_ANNUAL = rule.CASH_ANNUAL  # R-03 空仓 1.40%（同上，由文档原口径 2.0% 改为仓库惯例）
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

# --- 三个可插拔开关（2026-11-18）：把 v10 / v7 / v8 的机制接成 0/1 选项
# 模块常量保留 False（旧调用/对照档用）；**按腿默认配置**见下方 SWITCH_DEFAULT / SWITCH_BY_LEG。
# 1) v10 自适应波动层：ER 趋势态用 TV、震荡态用 TV_LOW（带滞回）；只改风险预算，不判方向
ADAPTIVE = False
TV_LOW = 0.10                   # 低挡沿用 v10 的 10%（高挡 = 本变种 TV=15%）
ER_N = 20
ER_HI = 0.40
ER_LO = 0.30
# 2) v7 年线闸门：月末 MA200，连续 2 月线下（或深破位免确认）→ 乘闸门系数
GATE = False
GATE_MA = 200                   # v7 的「年线」= MA200（不是 v5 的 MA250）
GATE_CONFIRM = 2
GATE_DEEP = 0.03
GATE_HYST = 0.0
GATE_FLOOR = {"ndx": 0.0, "spx": 0.5}   # 其余五腿减仓后留 0
# 3) v8 金叉 50/200：死叉日收盘后清仓（次日无敞口），金叉恢复由 v9 自身月末/补丁决定
XCROSS = False
XCROSS_FAST = 50
XCROSS_SLOW = 200

# 2026-11-18 用户定稿：**按腿开关配置**（写入 leg_params()，调用方显式传参优先）
SW_KEYS = ("ADAPTIVE", "GATE", "XCROSS")
SWITCH_DEFAULT = dict(ADAPTIVE=True, GATE=True, XCROSS=True)             # 111：除标普外全部
SWITCH_BY_LEG = {"spx": dict(ADAPTIVE=False, GATE=True, XCROSS=False)}   # 010：仅标普

W = {k: 1.0 / len(POOL) for k in POOL}          # 组合层：全池等权（各腿各 10pp 带）
W_DOC = {"spx": 2.0 / 3.0, "ndx": 1.0 / 3.0}    # 文档 §7「底仓:进攻 ≈ 2:1」（按波动倒数 11.14:22.45）
                                                # 只定义标普/纳指两腿子集；`portfolio()` 对未列腿取 0，Σw 仍为 1

# 文档 S-A 实测值（QQQ 单标的 · 2002-01-02~2026-11-03 · 24.84 年 · 含分红 · 单边 0.03%）
DOC_REF = dict(ann=0.1231, mdd=-0.3057, sharpe=0.70, sortino=0.99, expo=0.799, trades_per_year=9)


def params(**kw):
    """参数打包：敏感性扫描用 `**{...}` 覆盖，不必改模块常量。"""
    p = dict(POOL=POOL, TV=TV, VOL_WIN=VOL_WIN, DRIFT=DRIFT, CAP=CAP, SIGNAL=SIGNAL,
             COST=COST, CASH_ANNUAL=CASH_ANNUAL, MA_WIN_SB=MA_WIN_SB,
             TRIGGER=TRIGGER, CUT=CUT, CUT_RET=CUT_RET, CUT_VOLX=CUT_VOLX,
             ADD=ADD, DECAY=DECAY, PEAK_WIN=PEAK_WIN, ADD_MA=ADD_MA,
             # 三个可插拔开关：模块常量给对照档用，按腿默认在 leg_params() 里套
             ADAPTIVE=ADAPTIVE, TV_LOW=TV_LOW, ER_N=ER_N, ER_HI=ER_HI, ER_LO=ER_LO,
             GATE=GATE, GATE_MA=GATE_MA, GATE_CONFIRM=GATE_CONFIRM,
             GATE_DEEP=GATE_DEEP, GATE_HYST=GATE_HYST, GATE_FLOOR=GATE_FLOOR,
             XCROSS=XCROSS, XCROSS_FAST=XCROSS_FAST, XCROSS_SLOW=XCROSS_SLOW,
             # 回测口径: 样本末根也算「月末」（否则最后一个月不调仓, 与报告口径不符）;
             # 每日口径必须置 False —— 否则「今天」永远被当成月末, 每天都误报一次定仓决策。
             MONTH_END_LAST=True)
    explicit = tuple(k for k in SW_KEYS if k in kw)
    p.update(kw)
    p["_SW_EXPLICIT"] = explicit          # 供 leg_params() 判断哪些开关是调用方点名的
    return p


def leg_params(leg, p=None):
    """按腿套用三开关配置：**标普 010，其余六腿 111**；调用方显式传过的开关优先。

    这是 2026-11-18 用户定稿的当前默认口径。`build()` / `state_of()` 都走这里；
    想复现旧口径就显式传 `ADAPTIVE=False, GATE=False, XCROSS=False`。
    """
    q = dict(p or params())
    cfg = dict(SWITCH_DEFAULT)
    cfg.update(SWITCH_BY_LEG.get(leg, {}))
    explicit = set(q.get("_SW_EXPLICIT", ()))
    for k, v in cfg.items():
        if k not in explicit:
            q[k] = v
    return q


# ---------------------------------------------------------------- 2. 数据
def load_nav(leg, mode=None):
    """该腿价格序列：恒为**美元指数真实收盘**（不含汇率 / 跟踪误差 / 分红再投）。"""
    return vdata.series_for_leg(leg).dropna()


def load_signal(leg, p=None):
    """信号口径：**真实指数**收盘（`.NDX` / `.INX`），与 `load_nav()` 同源、同起点。

    ⚠ 必须走 `series_for_leg()`（`_global_index_klines.json`, 2005 起）。早期这里读
    `_us_index_klines.json`（只有 2010 起），导致 2005~2010 没有 vol20、仓位默认满仓，
    2008 危机整段被计入净值 —— 所有参数档的最大回撤因此都等于买入持有的 -54.74%。
    """
    return vdata.series_for_leg(leg).dropna()


def signal_of(leg, p=None):
    """信号价恒为该市场真实指数收盘（与回测价格同源），缺数据就报错。"""
    return load_signal(leg, p)


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


# ---------------------------------------------------------------- 3.1 三个可插拔开关（v10 / v7 / v8）
def adaptive_tv(sig, p=None):
    """v10 自适应波动层：Kaufman ER 判趋势/震荡 → TV 在高低两挡切换（带滞回）。

    `ADAPTIVE=False` 时退化为常数 `TV`（逐日 Series，便于直接除 vol）；
    ER 算法与滞回完全照搬 `v10_vol_layer/vol_layer.py`（只移植「层」本身，不引入
    v10 的 DEAD_DAYS/FLOOR —— v9 的死区由 DRIFT 与月末节奏负责）。
    """
    p = p or params()
    if not bool(p.get("ADAPTIVE", False)):
        return pd.Series(float(p["TV"]), index=sig.index, dtype="float64")
    s = sig.astype("float64")
    n = int(p["ER_N"])
    er = (s - s.shift(n)).abs() / s.diff().abs().rolling(n).sum()
    hi, lo = float(p["ER_HI"]), float(p["ER_LO"])
    fast, flags = False, []
    for v in er.to_numpy(dtype=float):
        if np.isfinite(v):
            if v >= hi:
                fast = True
            elif v <= lo:
                fast = False
        flags.append(fast)
    return pd.Series(np.where(flags, float(p["TV"]), float(p["TV_LOW"])),
                     index=sig.index, dtype="float64")


def annual_gate(sig, p=None, leg=None):
    """v7 年线闸门：月末 MA200 判断 → 逐日闸门系数（1 / FLOOR / 0）。

    照搬 `v7_us_gate/us_gate.py::decisions()` 的 gate 部分：连 `CONFIRM` 个月在年线下
    才减，首次破位低于年线 `DEEP` 以上免确认，站上立即回 1。返回 Series 在信号日历上：
    月末当日 = 该月末新决定的闸门（供 `machine()` 改次日仓位），其余日 = 上次决定值。
    `GATE=False` 时返回全 1（等价于关闭）。
    """
    p = p or params()
    if not bool(p.get("GATE", False)):
        return pd.Series(1.0, index=sig.index, dtype="float64")
    n = int(p["GATE_MA"])
    ma = sig.rolling(n, min_periods=n).mean()
    floor_cfg = p.get("GATE_FLOOR") or {}
    fl = float(floor_cfg.get(leg, 0.0)) if isinstance(floor_cfg, dict) else float(floor_cfg)
    deep_th = float(p.get("GATE_DEEP") or 0.0)
    confirm = int(p["GATE_CONFIRM"])
    hyst = float(p.get("GATE_HYST") or 0.0)
    ends = set(month_ends(sig.index, include_last=bool(p.get("MONTH_END_LAST", True))))
    gates, below, prev = {}, 0, 1.0
    for d in sig.index:
        gates[d] = prev                       # d 日收盘时已生效的闸门
        if d not in ends:
            continue
        s, m = float(sig.loc[d]), float(ma.loc[d])
        if not (np.isfinite(s) and np.isfinite(m)):
            continue
        above = s > m * (1.0 + hyst)
        gap = s / m - 1.0
        if above:
            below, gate = 0, 1.0
        else:
            below += 1
            deep = deep_th > 0.0 and gap <= -deep_th and below < confirm
            gate = fl if (deep or below >= confirm) else prev
        prev = gate
        gates[d] = gate                       # d 日收盘定 → 次日仓位生效（T+1）
    return pd.Series([gates[d] for d in sig.index], index=sig.index, dtype="float64")


def cross_mask(sig, p=None):
    """v8 金叉 50/200：MA50 > MA200 → True（可持有）；死叉 → False（清仓）。

    照搬 `v7_us_gate/us_gate.py::exit_mask()` 的口径：慢线未成形的预热期记 True，
    避免样本起点被强行清零。`XCROSS=False` 时返回 None（不覆写）。
    """
    p = p or params()
    if not bool(p.get("XCROSS", False)):
        return None
    f = sig.rolling(int(p["XCROSS_FAST"]), min_periods=int(p["XCROSS_FAST"])).mean()
    s = sig.rolling(int(p["XCROSS_SLOW"]), min_periods=int(p["XCROSS_SLOW"])).mean()
    ok = (f > s)
    ok[s.isna()] = True
    return ok


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


def machine(sig, p=None, leg=None):
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

    三个 0/1 开关（2026-11-18，默认全关 = 上面原样）：`ADAPTIVE` 把 TV 换成 ER 自适应序列
    （同时用于快减的 vol 门槛）；`GATE` 把公式值乘上 v7 月末年线闸门；`XCROSS` 在 v8 死叉日
    收盘把仓位直接清零（比月末闸门快一天，与 v7 `state_final()` 的日频离场同口径）。
    `leg` 只用于年线闸门的逐腿 FLOOR（纳指 0 / 标普 50%），不传则按 0。
    """
    p = p or params()
    cap = float(p["CAP"])
    vol = vol_series(sig, p)
    tv_d = adaptive_tv(sig, p)
    raw = (tv_d / vol).clip(upper=cap)
    if bool(p.get("GATE", False)):
        raw = raw * annual_gate(sig, p, leg=leg)          # v7 两层合成：闸门 × 波动层
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
        tvd = float(tv_d.loc[d]) if np.isfinite(tv_d.loc[d]) else float(p["TV"])
        tgt, why = hold, None
        if d in ends and abs(r - hold) >= drift - 1e-12:
            tgt, why = r, "month"
        if cut_on:                                         # 快减：急跌 + 波动已高
            v5 = float(ret5.loc[d]) if np.isfinite(ret5.loc[d]) else 0.0
            if r < tgt - drift and v5 <= float(p["CUT_RET"]) \
                    and float(vol.loc[d]) >= float(p["CUT_VOLX"]) * tvd:
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
    s = pd.Series(vals, index=sig.index, dtype="float64")
    mask = cross_mask(sig, p)                              # v8 金叉：死叉日收盘清零，次日无敞口
    if mask is not None:
        s = s.where(mask, 0.0)
    return s, events


def state_of(leg, p=None):
    """-> (nav, 净值日历上的仓位, events)。状态机跑在信号日历上，再前值填充到净值日历。"""
    p = leg_params(leg, p)
    nav = load_nav(leg)
    sm, ev = machine(signal_of(leg, p), p, leg=leg)
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
            w = float(weight.get(k, 0.0))                 # 权重表可为子集，未列腿按 0
            r += w * (e * float(rets[k].iloc[i]) + (1.0 - e) * cash_d)
            e_sum += w * e
            if i > 0:                                     # 今日收盘成交的仓位变化才付成本
                r -= w * abs(float(sts[k].iloc[i]) - float(sts[k].iloc[i - 1])) * c
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
        sw = leg_params(k)
        code = "".join("1" if sw[x] else "0" for x in SW_KEYS)
        print(f"{k} {rule.LEGS[k]['name']} [{code}]: 净值 "
              f"{nav.index[0].date()}~{nav.index[-1].date()} "
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
