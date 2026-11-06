# -*- coding: utf-8 -*-
"""波动资产 · V10 变种：**美股两腿的纯波动层**（无方向判断 · 日频 · 连续仓位）。

本变种**只服务纳指100 与标普500 两条腿**（池 = `("ndx", "spx")`），与 `v7_us_gate` /
`v8_golden_cross` / `v9_vol_target` 同一个池 —— 四个变种合起来构成「美股择时矩阵」：

| | v7 年线闸门 | v8 金叉 × 波动层 | v9 波动率目标 | **v10 纯波动层（本变种）** |
|---|---|---|---|---|
| 方向判断 | 有（MA200） | 有（MA50/MA200） | 无 | **无** |
| 仓位 | 二元 × 波动层 | 二元 × 波动层 | 连续 0~100% | **连续 0~100%** |
| 决策频率 | 月末 | 日频 | 月末 + 月中 | **日频** |
| 换手（14.2 年） | 70 次 | 132 次 | 249 次 | **157 次** |

## 1. 规则（一句话）
`仓位 = min(1, TV / vol20)`，TV = 20%，**没有方向门** —— 永远做多，只按各自腿的波动率决定露多少。

| 环节 | 规则 |
|---|---|
| 信号价 | **美元指数**收盘（`.NDX` / `.INX`），不是 QDII 人民币净值 |
| 波动率 | `vol20` = 20 日收益标准差 × √252（**每条腿各算各的**：纳指与标普的波动互不代替） |
| 仓位 | `min(1, TV / vol20)`，TV = 20%；`vol20 ≤ 20%` 时满仓，`vol20 = 40%` 时半仓 |
| 死区 | `|Δ仓位| < 10pp` 不动手（省换手；probe 实测无死区 → 934 次调仓，收益不变） |
| 看盘频率 | **每日收盘**（月末档作对照） |
| 成交 | `t` 日收盘决策 → **`t+1` 收盘成交** → 新仓位自 `t+2` 起承担收益 |
| 成本 | 单边 0.1%（文档主口径；0.6% 场外现实口径见回测 §5） |
| 空仓计息 | 未投部分按货基 `rule.CASH_ANNUAL`（1.40%）逐日计息 |
| 上/下限 | 恒 ≤ 100%（不加杠杆）；下限可设 `FLOOR`（默认 0，即极端波动下可以完全空仓） |

**「自适应」指的是仓位随本腿实时波动率自适应**，不是目标自适应 ——
`research/_probe_vol_layer.md` 实测：目标随长期波动漂移（TV ∝ vol250/中位）最多与常数 TV 打平、
且换手更高；把连续系数换成五档分位则明显更差（夏普 0.50）。**所以这里 TV 写死 20%，不漂移。**

## 2. 为什么单独成一支
在 v8 的波动层专项实测里出现了一个反直觉的结果：**「金叉 × 波动层」反而打不过「只波动层」**
（夏普 0.73 vs 0.85，回撤几乎相同）。原因是 50/200 金叉这个门太钝 —— 2020 年价格 03-09 就破年线，
MA50 要到 04-30 才穿 MA200。既然方向门在短样本里是净扣分项，就有必要把它单独做成一支，
让「纯风险预算」这个维度可以被独立检验、也可以被单独使用。

**它和 v9 的关系**：两者都做「按波动缩放仓位」，但
- v9 是**月末定仓 + 月中快减补丁**（低频、换手低、反应慢）；
- v10 是**日频逐日重算**（高频、换手高、反应快）。

同尺子下 v10 夏普 0.85、v9 0.88 —— **两支几乎打平，差别在换手与反应速度**（见回测 §3.1）。

## 3. 已知边界（先读，再看数字）
- **它没有方向判断，这是最大的风险**：2000-2002 那种「跌两年」的行情里，波动率只会在
  急跌段冲高，之后长期阴跌时 vol 回落到 20% 附近 → 仓位又回到满仓。**它治急跌，不治慢熊。**
  本仓库样本（2012 起）不含这种行情，因此**不要用本报告的数字外推长熊**。
- **样本只到 2012**（QDII 净值起点）：不含 2000-2002 与 2008。
- **日频换手不低**（14.2 年 157 次 ≈ 一年 11 次）：0.6% 场外口径下年化再掉约 0.6pp，
  比 v7 敏感得多（v7 一年 5 次）。怕摩擦就用 v9 或把死区调宽。
- **永远满仓的错觉**：平均仓位 92%，看着「几乎一直满仓」，但它在 2020-03 那几天真的降到过很低 ——
  平均值会骗人，要看最低仓位与时间序列（图里下格）。

模拟结果，非投资建议。
"""
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

LABEL = "V10 美股纯波动层 TV20"

# ---------------------------------------------------------------- 1. 参数（唯一权威源）
POOL = ("ndx", "spx")           # 策略标的池：只有这两条（与 v7 / v8 / v9 同池）
TV = 0.20                       # 目标波动 20%（年化）
VOL_WIN = 20                    # 波动率窗口（日）。probe 里 10/20/60 都试过，20 最好
DEAD = 0.10                     # 死区：|Δ仓位| < 10pp 不动手
FLOOR = 0.0                     # 仓位下限（0 = 极端波动时可以完全空仓）
TRIGGER = "daily"               # "daily" = 逐日重算（默认）; "month" = 月末定仓（对照）
SIGNAL = "index"                # "index" = 美元指数（正解）; "nav" = 人民币净值（反例）
COST = 0.001                    # 单边成本（文档主口径 0.1%）
CASH_ANNUAL = rule.CASH_ANNUAL  # 空仓计息（1.40%，与 v7/v8 同口径）

W = {k: 1.0 / len(POOL) for k in POOL}     # 组合层：两腿等权（两条腿都是 10pp 带）

# v8 波动层专项实测里同规则（只波动层 TV20 · 日频 · 死区 10pp）的组合层读数，供核对量级。
# 口径相同（0.1% / 1.40% / 两腿等权），所以两者应当逐位对齐 —— 回测 §7 有一条硬断言专门查这个。
DOC_REF = dict(ann=0.1351, vol=0.1431, mdd=-0.1878, sharpe=0.85, trades=157,
               sample="2012-08-15~2026-11-03")


def params(**kw):
    """参数打包：敏感性扫描用 `**{...}` 覆盖，不必改模块常量。"""
    p = dict(POOL=POOL, TV=TV, VOL_WIN=VOL_WIN, DEAD=DEAD, FLOOR=FLOOR, TRIGGER=TRIGGER,
             SIGNAL=SIGNAL, COST=COST, CASH_ANNUAL=CASH_ANNUAL)
    p.update(kw)
    return p


# ---------------------------------------------------------------- 2. 数据
def load_nav(leg, mode=None):
    """执行口径：场外 QDII 人民币复权净值（含分红再投 / 汇率）。"""
    return vdata.series_for_leg(leg, mode=mode).dropna()


def load_signal(leg, as_of=None):
    """信号口径：**美元指数**收盘（`.NDX` / `.INX`）。

    `as_of` 只保留 `<= as_of` 的观测 —— 前视检验靠它把「当时能看到的数据」真的截断掉。
    """
    s = vdata.us_series(rule.LEGS[leg]["signal"], refresh=False).dropna()
    if as_of is not None:
        s = s.loc[s.index <= pd.Timestamp(as_of)]
    return s


def signal_of(leg, p=None, as_of=None):
    """按 `SIGNAL` 取信号价：指数（正解）或人民币净值（反例）。"""
    p = p or params()
    if p["SIGNAL"] == "nav":
        s = load_nav(leg)
        return s.loc[s.index <= pd.Timestamp(as_of)] if as_of is not None else s
    return load_signal(leg, as_of=as_of)


def month_ends(idx):
    """每月最后一个交易日（含样本末根，便于口径对齐）。"""
    idx = pd.DatetimeIndex(idx)
    out = [idx[i] for i in range(len(idx) - 1)
           if (idx[i].year, idx[i].month) != (idx[i + 1].year, idx[i + 1].month)]
    if len(idx):
        out.append(idx[-1])
    return out


# ---------------------------------------------------------------- 3. 信号
def vol_series(leg, p=None, as_of=None):
    """年化已实现波动：`win` 日收益标准差 × √252（**每条腿各算各的**）。"""
    p = p or params()
    sig = signal_of(leg, p, as_of=as_of)
    if not len(sig):
        return pd.Series(dtype="float64")
    return sig.pct_change().rolling(int(p["VOL_WIN"]), min_periods=int(p["VOL_WIN"])
                                    ).std(ddof=1) * np.sqrt(252.0)


def raw_scale(leg, p=None, as_of=None):
    """未过死区的目标仓位 `clip(min(1, TV/vol), FLOOR, 1)`；预热 / vol=0 → 1.0。"""
    p = p or params()
    sig = signal_of(leg, p, as_of=as_of)
    if not len(sig):
        return pd.Series(dtype="float64")
    v = vol_series(leg, p, as_of=as_of).reindex(sig.index)
    s = (float(p["TV"]) / v).replace([np.inf, -np.inf], np.nan).clip(upper=1.0).fillna(1.0)
    return s.clip(lower=float(p["FLOOR"]), upper=1.0)


def deadzone(tgt, dead):
    """死区：`|新目标 − 现仓| < dead` 就不动手（仓位之间没有「状态切换」可言）。"""
    g = tgt.to_numpy(dtype=float)
    out = np.empty(len(g))
    if not len(g):
        return pd.Series(dtype="float64")
    hold = float(g[0])
    out[0] = hold
    for i in range(1, len(g)):
        if abs(g[i] - hold) >= float(dead) - 1e-12:
            hold = g[i]
        out[i] = hold
    return pd.Series(out, index=tgt.index, dtype="float64")


def target_raw(leg, p=None, as_of=None):
    """信号日历上的目标仓位（`d` 日收盘定 → `d+1` 收盘成交）。"""
    p = p or params()
    t = raw_scale(leg, p, as_of=as_of)
    if not len(t):
        return t
    dead = float(p["DEAD"])
    return deadzone(t, dead) if dead > 0 else t


def target_now(leg, p=None):
    """最新一根信号日上的目标仓位（每日报告用）。"""
    t = target_raw(leg, p)
    return float(t.iloc[-1]) if len(t) else 1.0


def decisions_month(leg, p=None):
    """月末档：每月最后一个交易日收盘定仓，次一交易日成交。"""
    p = p or params()
    sig = signal_of(leg, p)
    sc = raw_scale(leg, p).reindex(sig.index).ffill().fillna(1.0)
    out = []
    for d in month_ends(sig.index):
        v = float(sc.loc[d])
        if not np.isfinite(v):
            continue
        out.append(dict(date=d, close=float(sig.loc[d]), vol=float(vol_series(leg, p).loc[d]),
                        target=float(np.clip(v, 0.0, 1.0))))
    return out


def state_daily(nav_index, leg, p=None, as_of=None):
    """日频：`d` 日收盘决策 → **`d+1` 收盘成交**（`run_leg()` 再 `shift(1)` 让收益自 `d+2` 起）。"""
    raw = target_raw(leg, p, as_of=as_of)
    idx = pd.DatetimeIndex(nav_index)
    if not len(raw):
        return pd.Series(1.0, index=idx, dtype="float64")
    return raw.reindex(idx).ffill().fillna(1.0).shift(1).fillna(1.0)


def state_series(nav_index, dec, p=None):
    """把月末决策映射到净值日历：**决策日的次一根净值日成交**，其余时间保持。"""
    idx = pd.DatetimeIndex(nav_index)
    vals = {}
    for x in dec:
        if x["date"] < idx[0]:          # 早于净值起点的决策不可交易
            continue
        pos = int(idx.searchsorted(x["date"], side="right"))
        if pos < len(idx):
            vals[idx[pos]] = float(x["target"])
    s = pd.Series(vals, dtype="float64").sort_index().reindex(idx).ffill()
    return s.fillna(1.0)


def build(leg, **kw):
    """-> (nav, state, dec)。

    `TRIGGER="daily"`（默认）：日频状态，`dec` = **目标仓位的变化点**（信号日）；
    `TRIGGER="month"`：月末定仓 + 次月生效（对照），`dec` = 月末决策。
    """
    p = params(**kw)
    nav = load_nav(leg)
    if str(p["TRIGGER"]) == "month":
        dec = decisions_month(leg, p)
        return nav, state_series(nav.index, dec, p), dec
    tgt = target_raw(leg, p)
    return nav, state_daily(nav.index, leg, p), actions(tgt, since=nav.index[0])


# ---------------------------------------------------------------- 4. 回测
def run_leg(state, nav, cost=None, cash=None):
    """单腿净值（起点 = 1）。

    `state[t]` = 第 t 根**收盘成交后**的仓位 → 它只能赚 **t+1** 的收益（`shift(1)`），
    否则就是「用当日收盘才知道的仓位吃当日收益」的前视。
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
    """年化 / |最大回撤|。"""
    m = engine.metrics(nav)
    d = m["mdd"] if mdd is None else mdd
    return m["ann"] / abs(d) if d else float("nan")


def mdd_sampled(nav, freq="ME"):
    """**月末采样**的最大回撤（与 v7/v8/v9 同一度量口径，便于并排比较）。"""
    me = nav.resample(freq).last().dropna()
    if not len(me):
        return float("nan")
    return float((me / me.cummax() - 1.0).min())


def actions(state, eps=1e-9, since=None):
    """把仓位序列折成调仓动作（信号日历上；`since` 裁掉净值起点之前的点）。"""
    s = state.astype(float).to_numpy()
    idx = state.index
    out, prev = [], 0
    for i in range(1, len(s)):
        if abs(s[i] - s[i - 1]) > eps:
            d = idx[i]
            if since is not None and d < pd.Timestamp(since):
                prev = i
                continue
            out.append(dict(date=d, frm=float(s[i - 1]), to=float(s[i]),
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


def fmt_pct(x, nd=2):
    return "—" if x is None else f"{x * 100:+.{nd}f}%"


LEVEL_TXT = {1.0: "满仓", 0.75: "七五仓", 0.5: "半仓", 0.25: "二五仓", 0.0: "清仓"}


def level_name(x):
    """仓位 -> 中文档位名（图上标注与每日报告共用）。"""
    return LEVEL_TXT.get(round(float(x), 2), f"{float(x):.0%}")


def _self_check():
    """快速自检：样本、当前目标仓位、波动率、调仓次数。"""
    for k in POOL:
        nav, st, dec = build(k)
        assert float(st.max()) <= 1.0 + 1e-12 and float(st.min()) >= -1e-12, (k, st.min(), st.max())
        _n, st_m, dec_m = build(k, TRIGGER="month")
        v = float(vol_series(k).iloc[-1])
        print(f"{k} {rule.LEGS[k]['name']}: 净值 {nav.index[0].date()}~{nav.index[-1].date()} "
              f"n={len(nav)} · 当前 {st.iloc[-1]:.0%}（月末档 {st_m.iloc[-1]:.0%}）· "
              f"目标 {target_now(k):.0%} = min(1, {TV:.0%}/{v:.0%}) · "
              f"日频调仓 {len(dec)} 次 / 月末决策 {len(dec_m)} 条 · "
              f"仓位区间 {st.min():.0%}~{st.max():.0%}（均值 {st.mean():.0%}）")
    print(f"纯波动层 · TV {TV:.0%} / vol{VOL_WIN} / 死区 {DEAD:.0%} / 下限 {FLOOR:.0%} · "
          f"触发 {TRIGGER} · 信号 {SIGNAL} · 单边成本 {COST:.1%} · 空仓 {CASH_ANNUAL:.2%}")


if __name__ == "__main__":
    _self_check()
