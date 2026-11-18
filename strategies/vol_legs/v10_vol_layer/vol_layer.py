# -*- coding: utf-8 -*-
"""波动资产 · V10 变种：**全七腿 ER 自适应波动层**（日频 · 无涨跌方向 · 连续仓位）。
（2026-11-17 由 ndx/spx 两腿扩到全七腿；同日按用户指定把 `ER20/10 + 5日死区` 设为默认。）

本变种 2026-11-17 起**池 = 全七腿**（`POOL = rule.LEG_ORDER`）；此前只服务 ndx/spx 两腿。
**回测只做分腿，不做组合层**（`backtest.py` 无组合净值、无跨腿等权、无 v7/v8/v9 组合对照）。

## 1. 规则（一句话）
`仓位 = min(1, TV(ER) / vol20)`：ER(20) 趋势态 TV=20%、震荡态 TV=10%；
**没有涨跌方向判断** —— ER 只衡量「趋势走得干不干脆」，不预测上涨或下跌。

| 环节 | 规则 |
|---|---|
| 信号价 | 该腿**真实指数**收盘（美股为 `.NDX` / `.INX` 美元指数，黄金为 `518880`） |
| 波动率 | `vol20` = 20 日收益标准差 × √252（**每条腿各算各的**） |
| 风险预算 TV | ER(20) ≥ 0.40 → 趋势态 20%；≤ 0.30 → 震荡态 10%；中间维持（滞回，k=2） |
| 仓位 | `min(1, TV(ER) / vol20)`，不加杠杆、下限 0 |
| 死区 | 幅度 `|Δ仓位| < 10pp` 不动手；时间距上次调仓 < 5 交易日不动手 |
| 看盘频率 | **每日收盘**（月末档作对照） |
| 成交 | `t` 日收盘决策 → **`t+1` 收盘成交** → 新仓位自 `t+2` 起承担收益 |
| 成本 | 单边 0.1%（文档主口径；0.6% 场外现实口径见回测） |
| 空仓计息 | 未投部分按货基 `rule.CASH_ANNUAL`（1.40%）逐日计息 |

**「自适应」有两层**：①仓位随本腿 `vol20` 变；②TV 随 ER(20) 趋势纯度在 20%/10% 间切换。
2026-11-17 用户指定默认档 = `research/probe_er/_probe_er_adaptive_7legs.py` 的 **D 方案**
（ER20/10 + 10pp 幅度死区 + 5 日时间死区）；固定 TV20 保留为 `ADAPTIVE=False` 对照档。
全腿证据：ER 在 **6/7 腿**严格过线（黄金不过）；D 档在纳指后段 0.95、A500 0.58、
科创50 0.12，且比原生少 10~15% 调仓。

## 2. 为什么单独成一支
在 v8 的波动层专项实测里出现了一个反直觉结果：**「金叉 × 波动层」反而打不过「只波动层」**。
既然价格方向门在样本里是净扣分项，v10 最初把方向门全部拿掉，只留风险预算；
2026-11-17 起按用户指定加入 **ER 趋势纯度**这一维（仍然不判涨跌），把纯风险预算升级成
「趋势干脆就敢持仓、来回磨就压仓」。

## 3. 已知边界（先读，再看数字）
- **没有涨跌方向判断，但有趋势状态门**：ER 只判「路径效率」，不判多空。
  慢熊里 ER 会切到 10% 档，比原纯波动层好，但仍不是价格方向门。
- **样本自真实指数起点起**（A500/HS300/NDX/SPX 2005、黄金 2013-07、
  科创50 `SH000688` 2019-12、恒生科技 `HKHSTECH` 2020-07）；后两条腿样本短，结论降权。
- **日频换手不低**：默认 D 档用 5 日死区压过一轮，但 ER 状态切换仍会让调仓高于固定 TV20；
  0.6% 场外口径下要重看成本，怕摩擦就调宽死区或改用固定 TV20 / v9。
- **永远满仓的错觉**：平均仓位高，但急跌那几天仓位会真降；要看最低仓位与时间序列。

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


LABEL = "V10 全七腿 ER 自适应波动层（ER20/10 + 5日死区）"

# ---------------------------------------------------------------- 1. 参数（唯一权威源）
POOL = tuple(rule.LEG_ORDER)    # 策略标的池：全部七腿（2026-11-17 扩池）
ADAPTIVE = True                 # 2026-11-17 用户指定默认：ER 切 TV20/10；False = 旧固定 TV20
TV = 0.20                       # 趋势态目标波动（年化）
TV_LOW = 0.10                   # 震荡态目标波动（年化）
ER_N = 20                       # Kaufman 效率比窗口
ER_HI, ER_LO = 0.40, 0.30       # ER 阈值（带滞回，中间维持）
VOL_WIN = 20                    # 波动率窗口（日）。probe 里 10/20/60 都试过，20 最好
DEAD = 0.10                     # 幅度死区：|Δ仓位| < 10pp 不动手
DEAD_DAYS = 5                   # 时间死区：距上次调仓 < 5 交易日不动手（默认 D 档）
FLOOR = 0.0                     # 仓位下限（0 = 极端波动时可以完全空仓）
TRIGGER = "daily"               # "daily" = 逐日重算（默认）; "month" = 月末定仓（对照）
SIGNAL = "index"                # 唯一口径：真实指数（保留字段名以兼容 params()）
COST = 0.001                    # 单边成本（文档主口径 0.1%）
CASH_ANNUAL = rule.CASH_ANNUAL  # 空仓计息（1.40%，与 v7/v8 同口径）

W = {k: 1.0 / len(POOL) for k in POOL}     # 历史字段：分腿回测已不用组合权重，保留兼容


def params(**kw):
    """参数打包：敏感性扫描用 `**{...}` 覆盖，不必改模块常量。"""
    p = dict(POOL=POOL, ADAPTIVE=ADAPTIVE, TV=TV, TV_LOW=TV_LOW,
             ER_N=ER_N, ER_HI=ER_HI, ER_LO=ER_LO,
             VOL_WIN=VOL_WIN, DEAD=DEAD, DEAD_DAYS=DEAD_DAYS, FLOOR=FLOOR,
             TRIGGER=TRIGGER, SIGNAL=SIGNAL, COST=COST, CASH_ANNUAL=CASH_ANNUAL)
    p.update(kw)
    return p


# ---------------------------------------------------------------- 2. 数据
def load_nav(leg, mode=None):
    """该腿价格序列：恒为**美元指数真实收盘**（不含汇率 / 跟踪误差 / 分红再投）。"""
    return vdata.series_for_leg(leg).dropna()


def load_signal(leg, as_of=None):
    """信号口径：**真实指数**收盘（`.NDX` / `.INX`），与 `load_nav()` 同源、同起点。

    ⚠ 必须走 `series_for_leg()`（`_global_index_klines.json`, 2005 起）。早期这里读
    `_us_index_klines.json`（只有 2010 起），导致 2005~2010 无 vol20、仓位默认满仓，
    2008 危机整段被计入净值 —— 所有参数档的最大回撤因此都等于买入持有。

    `as_of` 只保留 `<= as_of` 的观测 —— 前视检验靠它把「当时能看到的数据」真的截断掉。
    """
    s = vdata.series_for_leg(leg).dropna()
    if as_of is not None:
        s = s.loc[s.index <= pd.Timestamp(as_of)]
    return s


def signal_of(leg, p=None, as_of=None):
    """信号价恒为该市场真实指数收盘（与回测价格同源），缺数据就报错。"""
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


def efficiency_ratio(px, n=ER_N):
    """Kaufman ER(n) = |20 日净变动| / 20 日路径总变动；只判趋势纯度，不判涨跌。"""
    s = px.astype("float64")
    return (s - s.shift(int(n))).abs() / s.diff().abs().rolling(int(n)).sum()


def er_state(er, hi=ER_HI, lo=ER_LO):
    """ER≥hi → 趋势态 True；≤lo → 震荡态 False；中间维持（带滞回）。"""
    fast, out = False, []
    for v in er.to_numpy(dtype=float):
        if not np.isnan(v):
            if v >= float(hi):
                fast = True
            elif v <= float(lo):
                fast = False
        out.append(fast)
    return pd.Series(out, index=er.index, dtype=bool)


def tv_series(leg, p=None, as_of=None):
    """风险预算 TV 序列：默认 ER 切 20%/10%；`ADAPTIVE=False` 时恒为 `TV`。"""
    p = p or params()
    sig = signal_of(leg, p, as_of=as_of)
    if not len(sig):
        return pd.Series(dtype="float64", index=sig.index)
    if not p.get("ADAPTIVE", False):
        return pd.Series(float(p["TV"]), index=sig.index)
    er = efficiency_ratio(sig, p["ER_N"])
    fast = er_state(er, p["ER_HI"], p["ER_LO"])
    return pd.Series(np.where(fast, float(p["TV"]), float(p["TV_LOW"])), index=sig.index)


def raw_scale(leg, p=None, as_of=None):
    """未过死区的目标仓位 `clip(min(1, TV(ER)/vol), FLOOR, 1)`；预热 / vol=0 → 1.0。"""
    p = p or params()
    sig = signal_of(leg, p, as_of=as_of)
    if not len(sig):
        return pd.Series(dtype="float64")
    v = vol_series(leg, p, as_of=as_of).reindex(sig.index)
    tv = tv_series(leg, p, as_of=as_of).reindex(sig.index)
    s = (tv / v).replace([np.inf, -np.inf], np.nan).clip(upper=1.0).fillna(1.0)
    return s.clip(lower=float(p["FLOOR"]), upper=1.0)


def deadzone(tgt, dead, gap=0):
    """幅度死区 + 时间死区：`|新目标−现仓|≥dead` 且距上次≥gap 日才动手。"""
    g = tgt.to_numpy(dtype=float)
    out = np.empty(len(g))
    if not len(g):
        return pd.Series(dtype="float64", index=tgt.index)
    cur, last = float(g[0]), 0
    out[0] = cur
    for i in range(1, len(g)):
        if abs(g[i] - cur) >= float(dead) - 1e-12 and (i - last) >= int(gap):
            cur, last = float(g[i]), i
        out[i] = cur
    return pd.Series(out, index=tgt.index, dtype="float64")


def target_raw(leg, p=None, as_of=None):
    """信号日历上的目标仓位（`d` 日收盘定 → `d+1` 收盘成交）。"""
    p = p or params()
    t = raw_scale(leg, p, as_of=as_of)
    if not len(t):
        return t
    dead = float(p["DEAD"])
    gap = int(p.get("DEAD_DAYS", 0)) if p.get("ADAPTIVE", False) else 0
    return deadzone(t, dead, gap) if dead > 0 or gap > 0 else t


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
        tv_now = float(tv_series(k).iloc[-1])
        er_now = bool(er_state(efficiency_ratio(load_signal(k), ER_N), ER_HI, ER_LO).iloc[-1])
        print(f"{k} {rule.LEGS[k]['name']}: 净值 {nav.index[0].date()}~{nav.index[-1].date()} "
              f"n={len(nav)} · 当前 {st.iloc[-1]:.0%}（月末档 {st_m.iloc[-1]:.0%}）· "
              f"目标 {target_now(k):.0%} = min(1, {tv_now:.0%}/{v:.0%}) · "
              f"ER {'趋势态' if er_now else '震荡态'} · "
              f"日频调仓 {len(dec)} 次 / 月末决策 {len(dec_m)} 条 · "
              f"仓位区间 {st.min():.0%}~{st.max():.0%}（均值 {st.mean():.0%}）")
    print(f"ER 自适应波动层（默认 {'开' if ADAPTIVE else '关'}）· ER{ER_N} "
          f"{ER_HI:.2f}/{ER_LO:.2f} → TV {TV:.0%}/{TV_LOW:.0%} · vol{VOL_WIN} · "
          f"死区 {DEAD:.0%}/{DEAD_DAYS}日 · 下限 {FLOOR:.0%} · "
          f"触发 {TRIGGER} · 信号 {SIGNAL} · 单边成本 {COST:.1%} · 空仓 {CASH_ANNUAL:.2%}")


if __name__ == "__main__":
    _self_check()
