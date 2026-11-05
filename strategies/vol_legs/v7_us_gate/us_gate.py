# -*- coding: utf-8 -*-
"""波动资产 · V7 变种：**美股两腿的年线闸门**（200 日均线 · 连续 2 个月确认 · 深破位免确认 · 波动层 TV20%）。

本变种**只服务纳指100 与标普500 两条腿**（池 = `("ndx", "spx")`），规则逐条落地自
`E:\\qianwen\\jijin\\纳指100与标普500_策略回测与规则.md`（2026-11-04 用户指定口径）。

## 1. 规则（文档 §最终规则 的三条纪律，一字不改）

| 环节 | 规则 |
|---|---|
| 信号价 | **美元指数**（`.NDX` / `.INX`）收盘价，**不是** QDII 人民币净值 |
| 均线 | 200 日简单均线（指数收盘，日频算、月末看） |
| 看盘频率 | **月末最后一个交易日**收盘，次一交易日成交（= 次月首个交易日） |
| 减仓条件 | **连续 2 个月**收盘都在年线**之下**才动手（单月破位不动，躲假摔） |
| 深破位豁免 | 首次破位即**低于年线 3% 以上** → **当月就减**，不等第二个月（2026-11-04 用户要求，见 §4 边界） |
| 减到多少 | 纳指100 → **清仓（0%）**；标普500 → **减半（50%）** |
| **波动层** | `最终权重 = 闸门 × min(1, 20% / vol20)`，月末定、变动 < 5pp 不动手（2026-11-05 用户选定，与 v9 同口径） |
| 回补 | 收盘重新站上年线 → **次月即回补满仓**（不等确认，但同样乘波动层系数） |
| 成本 | 单边 0.1%（文档主口径；波动层下换手约 6 次/年，0.6% 场外口径见报告 §4） |
| 空仓计息 | 未投部分按货基 `rule.CASH_ANNUAL` 计息 |

## 2. 文档明令禁止的四种做法（本模块把它们做成**反例档**，不是默认）

1. **用人民币净值算均线**（表1：纳指 12.70% → 9.32%，夏普 0.57 → 0.36）→ 参数 `SIGNAL="nav"`；
2. 用 ERP / 估值分位择美股（与本池无关，未实现）；
3. 存量越跌越买（那是主策略的阶梯，不是本变种）；
4. 把定投当收益策略（未实现）。

## 3. 与 v5 的区别（两支都叫「趋势闸门」，别混）

| | v5_trend_gate | 本变种（v7_us_gate） |
|---|---|---|
| 池 | 七腿 | **仅 ndx / spx** |
| 均线 | MA250，四象限（价/线/斜率） | **MA200，只判价格与均线** |
| 频率 | 日频 | **月末 + 次月生效** |
| 确认 | 无 | **连续 2 个月在年线下才减**（深破位 ≥3% 例外） |
| 动作 | 分档（25/50/75/100） | **闸门二元（纳指 0 / 标普 50%）× 波动层连续系数** |
| 信号价 | 场外净值（`SIGNAL_MODE="otc"`） | **美元指数**（实测这一条值 3.4pp 年化） |

## 4. 已知边界

- **样本只到 2012**（QDII 场外净值起点）：不含 2000-2002（纳指 −83%）与 2008，
  而那正是年线闸门唯一「救命」的窗口 —— 本报告对下一次泡沫破裂是**外推**。
- 只降不升（不预测方向、不加杠杆）；年化让渡是**买崩盘保险的保费**，不是 alpha。
- 标普「减半」而不是清仓，来自文档的判定：标普跌幅浅、恢复快，清仓的机会成本更高。
- 纳指 QDII 通道有额度/限购风险，本回测未计（信号是美元指数，执行是人民币净值）。
- **深破位阈值 `DEEP=3%` 是单事件标定的**（2026-11-04 加入）：2 个月的滞后只在
  2020-02→03 一次上真正付了钱（月末 −28.2% vs 1 个月口径 −22.6%），3% 这个数恰好落在
  那次破位（标普 −3.05%）与其余所有首次破位（最近的 2025-03 为 −2.58%）之间的窄缝里 ——
  **样本内漂亮，样本外没有证据**。改成波动率标准化（破位深度 / 月波动）不能复现同样的切分，
  说明「破位深度」本身区分不了真破位与假摔。所以这一档的价值是「把已知的那一次漏洞补上」，
  不是找到了稳健规律（`DEEP=0` 即退回文档原口径）。
- **波动层 `TV=20%` 是 2026-11-05 用户选定的默认**：选 20% 而不是风险调整最优的 15%，是因为
  两档 Calmar 只差 0.19（1.14 vs 1.33）而年化差 1.0pp、调仓差 65 次 —— 用户要「收益几乎不损、
  回撤明显改善」。**它是风险预算不是择时**：低波动阴跌里会满仓挨打；换手从 26 次升到 85 次，
  0.6% 的场外 A 类要另算成本。`TV=None` 即退回纯闸门口径。

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

LABEL = "V7 美股年线闸门"

# ---------------------------------------------------------------- 1. 参数（唯一权威源）
POOL = ("ndx", "spx")           # 策略标的池：只有这两条
MA_WIN = 200                    # 200 日均线（指数收盘，日频算）
CONFIRM = 2                     # 连续 N 个月在年线下才减仓
DEEP = 0.03                     # 深破位免确认：首次破位即低于年线 3% 以上 -> 当月就减（0/null = 关闭）
HYST = 0.0                      # 迟滞带（0.02 = 站上 MA×1.02 才算回补，文档里是「更差」的档）
FLOOR = {"ndx": 0.0, "spx": 0.5}    # 减仓后留多少：纳指清仓、标普减半
TV = 0.20                       # 波动层目标波动（None/0 = 关闭；0.15 = v9_vol_target 口径；20% 是 2026-11-05 选定默认）
VOL_WIN = 20                    # 波动层窗口（年化已实现波动 = std(r,N)×sqrt(252)，与 v9 的 vol20 同口径）
VOL_DEAD = 0.10                 # 波动层死区：闸门没变时，目标权重变化 < 10pp 就不动手（2026-11-05 由 5pp 放宽）
CAP = 1.0                       # 波动层上限（禁止杠杆）
SIGNAL = "index"                # "index" = 美元指数（正解）; "nav" = 人民币净值（反例）
TRIGGER = "month"               # "month" = 月末看盘 + 次月生效（文档引擎口径）; "daily" = 日频跌破即走
COST = 0.001                    # 单边成本（文档主口径 0.1%）
CASH_ANNUAL = rule.CASH_ANNUAL
CASH_DAILY = (1.0 + CASH_ANNUAL) ** (1.0 / 252) - 1.0

W = {k: 1.0 / len(POOL) for k in POOL}     # 组合层：两腿等权（both band 10pp）

# 文档表1 的对照值（QDII 口径、月末、单边 0.1%、现金用中国国债 2Y）
DOC_REF = {
    "ndx": {
        "买入持有": dict(ann=0.1612, mdd=-0.2817, calmar=0.57, turnover=1.0),
        "年线闸门": dict(ann=0.1270, mdd=-0.1352, calmar=0.94, turnover=19),
        "年线+2月确认": dict(ann=0.1391, mdd=-0.1396, calmar=1.00, turnover=13),
        "年线+2%迟滞": dict(ann=0.1309, mdd=-0.1352, calmar=0.97, turnover=17),
        "半仓闸门": dict(ann=0.1457, mdd=-0.1887, calmar=0.77, turnover=10),
        "人民币净值信号": dict(ann=0.0932, mdd=-0.2592, calmar=0.36, turnover=25),
    },
    "spx": {
        "买入持有": dict(ann=0.1323, mdd=-0.1866, calmar=0.71, turnover=1.0),
        "年线闸门": dict(ann=0.0785, mdd=-0.1820, calmar=0.43, turnover=25),
        "年线+2月确认": dict(ann=0.1007, mdd=-0.1866, calmar=0.54, turnover=13),
        "年线+2%迟滞": dict(ann=0.1141, mdd=-0.1640, calmar=0.70, turnover=11),
        "半仓闸门": dict(ann=0.1060, mdd=-0.1730, calmar=0.61, turnover=13),
        "人民币净值信号": dict(ann=0.0912, mdd=-0.1640, calmar=0.56, turnover=17),
    },
}
DOC_SAMPLE = {"ndx": "2013-07-18~2026-09-29", "spx": "2012-09-27~2026-09-29"}


def params(**kw):
    """参数打包：敏感性扫描用 `**{...}` 覆盖，不必改模块常量。"""
    p = dict(POOL=POOL, MA_WIN=MA_WIN, CONFIRM=CONFIRM, DEEP=DEEP, HYST=HYST, FLOOR=FLOOR,
             TV=TV, VOL_WIN=VOL_WIN, VOL_DEAD=VOL_DEAD, CAP=CAP,
             SIGNAL=SIGNAL, TRIGGER=TRIGGER, COST=COST, CASH_ANNUAL=CASH_ANNUAL)
    p.update(kw)
    return p


def floor_for(leg, p=None):
    """该腿减仓后保留的比例：dict 逐腿 / 标量统一 / None 沿用默认。"""
    p = p or params()
    f = p["FLOOR"]
    if f is None:
        f = FLOOR
    if isinstance(f, dict):
        return float(f.get(leg, 0.0))
    return float(f)


def load_nav(leg, mode=None):
    """执行口径：场外 QDII 人民币复权净值（含分红再投 / 汇率）。"""
    return vdata.series_for_leg(leg, mode=mode).dropna()


def load_signal(leg):
    """信号口径：**美元指数**收盘（`.NDX` / `.INX`），不退回净值 —— 缺数据就报错。"""
    code = rule.LEGS[leg]["signal"]
    return vdata.us_series(code, refresh=False).dropna()


def signal_of(leg, p=None):
    """按 `SIGNAL` 取信号价：指数（正解）或人民币净值（表1 反例）。"""
    p = p or params()
    return load_nav(leg) if p["SIGNAL"] == "nav" else load_signal(leg)


# ---------------------------------------------------------------- 2. 月末决策
def vol_series(sig, p=None):
    """年化已实现波动率 = `std(r, N, ddof=1) × sqrt(252)`（与 v9 的 vol20 同口径）。"""
    p = p or params()
    n = int(p["VOL_WIN"])
    return sig.pct_change().rolling(n, min_periods=n).std(ddof=1) * math.sqrt(252.0)


def vol_scale(vol, p=None):
    """波动层系数 `min(CAP, TV / vol)`，下限 0；vol 缺失时按 1（等同不做缩放）。"""
    p = p or params()
    tv = float(p["TV"] or 0.0)
    if tv <= 0.0:
        return 1.0
    if vol is None or not np.isfinite(vol) or vol <= 0:
        return 1.0
    return float(min(float(p["CAP"]), max(0.0, tv / float(vol))))


def month_ends(idx):
    """每月最后一个交易日（含样本末根，便于口径对齐）。"""
    idx = pd.DatetimeIndex(idx)
    out = [idx[i] for i in range(len(idx) - 1)
           if (idx[i].year, idx[i].month) != (idx[i + 1].year, idx[i + 1].month)]
    if len(idx):
        out.append(idx[-1])
    return out


def decisions(leg, **kw):
    """-> [{date, close, ma, below, gap, deep, above, gate, scale, vol, target}]，每个月末一条。

    **两层结构**：`target = gate × scale`
    - `gate`（闸门层）：站上年线 → 1.0；连续 `CONFIRM` 个月在年线下 → `FLOOR`；不足时维持上一次的 gate。
      **深破位免确认**：首次破位即 `gap <= -DEEP`（默认 −3%）时当月就减，不等第二个月；
      这一条只豁免「等」，不改变减多少。`DEEP=0/None` 即关闭，退回文档原口径。
    - `scale`（波动层，`TV=None` 时恒为 1，即完全退化成老口径）：`min(CAP, TV / vol20)`，
      月末用信号自身（美元指数）的 20 日已实现年化波动算，**不看方向、只看风险预算**。
      闸门层没变时，若目标权重变化 < `VOL_DEAD`（10pp）就不动手 —— 与 v9 的漂移带同口径。

    两层都在同一个月末收盘定、次一交易日成交，波动率也只取到当月末那根，**无前视**。
    """
    p = params(**kw)
    sig = signal_of(leg, p)
    ma = sig.rolling(int(p["MA_WIN"]), min_periods=int(p["MA_WIN"])).mean()
    deep_th = float(p["DEEP"] or 0.0)
    tv = float(p["TV"] or 0.0)
    vol = vol_series(sig, p) if tv > 0 else None
    dead = float(p["VOL_DEAD"] or 0.0)
    hold, prev_gate = 1.0, 1.0
    below = 0
    out = []
    for d in month_ends(sig.index):
        s, m = float(sig.loc[d]), float(ma.loc[d])
        if not np.isfinite(s) or not np.isfinite(m):
            continue
        above = s > m * (1.0 + float(p["HYST"]))
        gap = s / m - 1.0
        deep = False
        if above:
            below = 0
            gate = 1.0
        else:
            below += 1
            # 只有「确认月数还没到、靠深度提前动手」才算深破位豁免（by confirm 到的不算）
            deep = (deep_th > 0.0 and gap <= -deep_th
                    and below < int(p["CONFIRM"]))
            gate = floor_for(leg, p) if (deep or below >= int(p["CONFIRM"])) else prev_gate
        v = float(vol.loc[d]) if vol is not None and d in vol.index else None
        scale = vol_scale(v, p)
        tgt = gate * scale
        if (dead > 0.0 and vol is not None
                and abs(gate - prev_gate) < 1e-12 and abs(tgt - hold) < dead):
            tgt = hold                      # 闸门没变 + 波动层不到死区 -> 不动
        hold, prev_gate = tgt, gate
        out.append(dict(date=d, close=s, ma=m, below=below, gap=gap, deep=bool(deep),
                        above=bool(above), gate=float(gate), scale=float(scale),
                        vol=(None if v is None else float(v)), target=float(tgt)))
    return out


def state_series(nav_index, dec):
    """把月末决策映射到净值日历：**决策日的次一根净值日成交**，其余时间保持。

    起点口径：第一根净值日即按「已满仓」计（与买入持有基准一致，不另计建仓费）。
    """
    idx = pd.DatetimeIndex(nav_index)
    vals = {}
    for x in dec:
        pos = int(idx.searchsorted(x["date"], side="right"))
        if pos < len(idx):
            vals[idx[pos]] = x["target"]
    s = pd.Series(vals, dtype="float64").sort_index().reindex(idx).ffill()
    return s.fillna(1.0)


def deep_exit_dates(dec, nav_index):
    """深破位免确认**真正产生减仓**的那些成交日（画图 / 日志标红用）。

    注意只收「目标仓位发生变化」的决策：连续几个月都在年线下 3% 以上时，`deep` 会一直为真，
    但那几个月并没有新的动作，不该被算成一笔调仓。
    """
    idx = pd.DatetimeIndex(nav_index)
    out = set()
    prev = 1.0
    for x in dec:
        if x.get("deep") and abs(float(x["target"]) - prev) > 1e-12:
            pos = int(idx.searchsorted(x["date"], side="right"))
            if pos < len(idx):
                out.add(idx[pos])
        prev = float(x["target"])
    return out


def state_daily(nav_index, sig, leg, p=None):
    """日频触发：收盘价 < 年线 即次日降到 floor，重新站上即次日回满（不做月度确认）。"""
    p = p or params()
    ma = sig.rolling(int(p["MA_WIN"]), min_periods=int(p["MA_WIN"])).mean()
    fl = floor_for(leg, p)
    above = sig > ma * (1.0 + float(p["HYST"]))
    raw = pd.Series(np.where(above, 1.0, fl), index=sig.index, dtype="float64")
    raw[ma.isna()] = 1.0
    idx = pd.DatetimeIndex(nav_index)
    # 信号在当日收盘成立 → 次日收盘成交（与月度档同一 T+1 纪律）
    return raw.reindex(idx).ffill().fillna(1.0).shift(1).fillna(1.0)


def build(leg, **kw):
    """-> (nav, state, dec)。净值与信号各自取，state 对齐到净值日历。

    `TRIGGER="month"`（默认，文档引擎口径）：月末看盘 + 次月生效；
    `TRIGGER="daily"`：日线跌破年线即次日走、重新站上即次日回 —— 用于判断
    「文档表1 的回撤数字到底是哪种触发口径」（见 README §5）。
    """
    p = params(**kw)
    nav = load_nav(leg)
    if p["TRIGGER"] == "daily":
        st = state_daily(nav.index, signal_of(leg, p), leg, p)
        return nav, st, []
    dec = decisions(leg, **kw)
    st = state_series(nav.index, dec)
    return nav, st, dec


# ---------------------------------------------------------------- 3. 回测
def run_leg(state, nav, cost=None, cash=None):
    """单腿净值（起点 = 1）。

    `state[t]` = 第 t 根**收盘成交后**的仓位 → 它只能赚 **t+1** 的收益（`shift(1)`），
    否则就是「用当日收盘才知道的仓位吃当日收益」的前视。
    起点口径：第一根按 `state[0]` 建仓并吃当日收益（与买入持有基准同口径，不另计建仓费）。
    """
    c = COST if cost is None else float(cost)
    rf = CASH_ANNUAL if cash is None else float(cash)
    cash_d = (1.0 + rf) ** (1.0 / 252) - 1.0
    ret = nav.pct_change().fillna(0.0)
    hold = state.astype(float).shift(1)
    if len(hold):
        hold.iloc[0] = float(state.iloc[0])
    d = state.astype(float).diff().fillna(0.0).abs()
    r = hold * ret + (1.0 - hold) * cash_d - d * c
    return (1.0 + r).cumprod()


LEVEL_TXT = {1.0: "满仓", 0.5: "半仓", 0.0: "清仓", 0.2: "观察"}


def level_name(x):
    """仓位 -> 中文档位名（图上标注与每日报告共用）。"""
    return LEVEL_TXT.get(round(float(x), 6), f"{float(x):.0%}")


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


def portfolio(navs, states, weight=None, cost=None, cash=None):
    """两腿组合净值（默认等权）。逐日线性加权 + 各腿成本。"""
    weight = weight or W
    cols = list(navs)
    idx = navs[cols[0]].index
    c = COST if cost is None else float(cost)
    rf = CASH_ANNUAL if cash is None else float(cash)
    cash_d = (1.0 + rf) ** (1.0 / 252) - 1.0
    rets = {k: navs[k].pct_change().fillna(0.0) for k in cols}
    sts = {k: states[k].astype(float).reindex(idx).ffill() for k in cols}
    # 同上：state 是收盘成交后的仓位，只赚下一根收益
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
    """年化 / |最大回撤|（文档表1 的 Calmar 口径）。"""
    m = engine.metrics(nav)
    d = m["mdd"] if mdd is None else mdd
    return m["ann"] / abs(d) if d else float("nan")


def mdd_sampled(nav, freq="ME"):
    """**月末采样**的最大回撤（文档表1 的度量口径）。

    文档引擎是月频：曲线只在月末取点，所以 2020-02-19→03-16 那种「月内」坑不在样本里。
    日频口径会把整段坑计入（同一个策略在纳指上 −13.9% vs −26.5%）。两个都对，
    但**看数字时必须知道是哪一种** —— 决策与风控请用日频那个。
    """
    me = nav.resample(freq).last().dropna()
    if not len(me):
        return float("nan")
    return float((me / me.cummax() - 1.0).min())


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


def fmt_pct(x, nd=2):
    return "—" if x is None else f"{x * 100:+.{nd}f}%"


def _self_check():
    """快速自检：两条腿的样本、当前状态、决策条数。"""
    tv = float(params()["TV"] or 0.0)
    for k in POOL:
        nav, st, dec = build(k)
        _, st0, _ = build(k, TV=0)
        assert set(np.round(st0.unique(), 6)) <= {1.0, floor_for(k)}, (k, st0.unique())
        assert float(st.max()) <= 1.0 + 1e-12 and float(st.min()) >= -1e-12, (k, st.min(), st.max())
        print(f"{k} {rule.LEGS[k]['name']}: 净值 {nav.index[0].date()}~{nav.index[-1].date()} "
              f"n={len(nav)} · 当前仓位 {st.iloc[-1]:.0%}（闸门 {dec[-1]['gate']:.0%}）· "
              f"决策 {len(dec)} 条 · 末次 {dec[-1]['date'].date()} 收盘 {dec[-1]['close']:.0f} / "
              f"MA200 {dec[-1]['ma']:.0f} · 连续在年线下 {dec[-1]['below']} 月")
    print(f"MA{MA_WIN} · 连续 {CONFIRM} 月确认 · 深破 {DEEP:.0%} 免确认 · "
          f"减仓后 纳指 {floor_for('ndx'):.0%} / 标普 {floor_for('spx'):.0%} · "
          f"信号 {SIGNAL} · 单边成本 {COST:.1%} · "
          + (f"波动层 TV {tv:.0%}/vol{VOL_WIN} · 死区 {VOL_DEAD:.0%}" if tv > 0 else "波动层关闭"))


if __name__ == "__main__":
    _self_check()
