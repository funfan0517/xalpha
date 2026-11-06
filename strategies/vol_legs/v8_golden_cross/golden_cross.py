# -*- coding: utf-8 -*-
"""波动资产 · V8 变种：**美股两腿的 50/200 金叉 × 波动层 TV20**（日频 · 连续仓位）。

本变种**只服务纳指100 与标普500 两条腿**（池 = `("ndx", "spx")`），与 `v7_us_gate` /
`v9_vol_target` / `v10_vol_layer` 同一个池 —— 四个变种合起来构成「美股择时矩阵」：

| | v7 年线闸门 | **v8 金叉 × 波动层（本变种）** | v9 波动率目标 | v10 纯波动层 |
|---|---|---|---|---|
| 问的问题 | 在不在年线上 | **快线在不在慢线上，且该配多少** | 这段波动值多少仓位 | 该配多少 |
| 方向判断 | 有（MA200） | **有（MA50/MA200）** | 无 | **无** |
| 仓位 | 二元 × 波动层 | **二元 × 波动层** | 连续 0~100% | **连续 0~100%** |
| 决策频率 | 月末 | **日频** | 月末 + 月中 | **日频** |

## 1. 规则（一句话）
`仓位 = (MA50 > MA200 ? 1 : 0) × min(1, TV / vol20)`，TV = 20%，死区 10pp。

| 环节 | 规则 |
|---|---|
| 信号价 | **美元指数**收盘（`.NDX` / `.INX`），不是 QDII 人民币净值 |
| 方向门 | 50 日 / 200 日简单均线：快线在上 → 1.0；在下 → 0.0 |
| 波动层 | `min(1, TV / vol20)`，`vol20` = 20 日收益标准差 × √252，TV = 20% |
| 死区 | 方向状态未变时，`|Δ仓位| < 10pp` 不动手（省换手，见 `deadzone()`） |
| 看盘频率 | **每日收盘**（经典口径；月末档作对照） |
| 成交 | `t` 日收盘决策 → **`t+1` 收盘成交** → 新仓位自 `t+2` 起承担收益 |
| 成本 | 单边 0.1%（文档主口径；0.6% 场外现实口径见回测 §5） |
| 空仓计息 | 未投部分按货基 `rule.CASH_ANNUAL`（1.40%）逐日计息 |
| 预热期 | MA200 未成形（前 199 根）按「已满仓」计，与买入持有同起点 |

**仓位上限恒为 1**（不加杠杆），下限由 `FLOOR` 决定（默认 0）。

**它不是「金叉那天买、死叉那天卖」的事件型策略**：`MA50 > MA200` 是个**持续状态**，
两者等价（状态只在穿越日翻转），用状态写少一个「信号丢失」的坑。

## 2. 为什么默认加波动层（2026-11-05 改）
纯二元金叉在 2012-2026 短样本里是「三支里最差」的（+11.34% / 回撤 −28.17% / 夏普 0.66，
回撤与买入持有完全相同）。`research/_probe_vol_layer.md` 实测：加上这一层后
**回撤 −28.17% → −18.15%、Calmar 0.71 → 0.93、夏普 0.66 → 0.73**，代价只有年化 −0.52pp。
它买的是**反应速度**：2020 那次方向信号要 5 周才到（MA50 到 04-30 才穿 MA200），
波动信号 5 天就到 —— 慢线治不了急跌，波动率能。

而「自适应」的四种变体（估计子 / 目标 / 档位）**都没有赢过常数 TV20**，
所以这里就写死 `TV=20 / vol20 / 死区 10pp`，不引入任何会漂移的锚。

## 3. 已知边界（先读，再看数字）
- **样本只到 2012**（QDII 场外净值起点）：不含 2000-2002（纳指 −83%）与 2008 ——
  而金叉在这两段里的表现（2000-2002 回撤 −35.74%）正是它最该被检验的地方，本报告是**外推**。
  波动层在那两段的作用**同样未验证**（理论上会在泡沫破裂期持续低仓，但无数据可验）。
- **方向门仍然钝**：波动层补的了急跌，补不了「阴跌 + 低波动」——
  那种行情里 `vol` 不高，仓位照旧接近满仓。
- **慢线是硬约束**：200 日慢线让「趋势反转」的确认必然滞后 2~4 个月，
  代价写进了换手与回撤；这不是参数没调好，是这类规则的结构。
- **只降不升、不加杠杆**：仓位恒 ≤ 100%。

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

LABEL = "V8 美股金叉 50/200 × 波动层 TV20"

# ---------------------------------------------------------------- 1. 参数（唯一权威源）
POOL = ("ndx", "spx")           # 策略标的池：只有这两条（与 v7 / v9 / v10 同池）
FAST = 50                       # 快线
SLOW = 200                      # 慢线
HYST = 0.0                      # 迟滞带（0.02 = 快线要高出慢线 2% 才算金叉；默认关闭）
PRICE_FILTER = False            # True = 额外要求「价格也在慢线上方」才算持仓
FLOOR = 0.0                     # 死叉后留多少仓位（0 = 清仓；0.5 = 减半）
TRIGGER = "daily"               # "daily" = 教科书口径（默认）; "month" = 月末定仓（对照）
SIGNAL = "index"                # "index" = 美元指数（正解）; "nav" = 人民币净值（反例）

# ---- 波动层（2026-11-05 起为**默认档**；来源：research/_probe_vol_layer.md）----
VOL_ON = True                   # 总开关。False = 回到纯二元金叉（旧默认，保留作对照）
TV = 0.20                       # 目标波动 20%（年化），仓位 = min(1, TV / vol)
VOL_WIN = 20                    # 波动率窗口（日）。probe 里 10/20/60 三档都试过，20 最好
VOL_DEAD = 0.10                 # 死区：金叉状态未变时，|Δ仓位| < 10pp 不动手（省换手）

COST = 0.001                    # 单边成本（文档主口径 0.1%）
CASH_ANNUAL = rule.CASH_ANNUAL  # 空仓计息（1.40%，与 v7 同口径）

W = {k: 1.0 / len(POOL) for k in POOL}     # 组合层：两腿等权（两条腿都是 10pp 带）

# `research/_probe_published_us.md` 的同规则实测（纳指 · 1995-01~2026-11 · 指数收盘**不含分红** ·
# 单边 0.10% · 空仓 1.40%）。口径与本节不同（30 年 vs 14 年、指数 vs QDII 净值），只比量级。
DOC_REF = dict(ndx=dict(ann=0.1608, vol=0.2037, mdd=-0.3574, calmar=0.45, sharpe=0.72,
                        trades=36, expo=0.77),
               sample="1995-01-03~2026-11-04")


def params(**kw):
    """参数打包：敏感性扫描用 `**{...}` 覆盖，不必改模块常量。"""
    p = dict(POOL=POOL, FAST=FAST, SLOW=SLOW, HYST=HYST, PRICE_FILTER=PRICE_FILTER,
             FLOOR=FLOOR, TRIGGER=TRIGGER, SIGNAL=SIGNAL, COST=COST, CASH_ANNUAL=CASH_ANNUAL,
             VOL_ON=VOL_ON, TV=TV, VOL_WIN=VOL_WIN, VOL_DEAD=VOL_DEAD)
    p.update(kw)
    return p


# ---------------------------------------------------------------- 2. 数据
def load_nav(leg, mode=None):
    """执行口径：场外 QDII 人民币复权净值（含分红再投 / 汇率）。"""
    return vdata.series_for_leg(leg, mode=mode).dropna()


def load_signal(leg, as_of=None):
    """信号口径：**美元指数**收盘（`.NDX` / `.INX`），缺数据就报错（不静默降级）。

    `as_of` 只保留 `<= as_of` 的观测 —— 前视检验（`backtest.py` 断言 4）靠它把「当时能看到的数据」
    真的截断掉，而不是嘴上说没有前视。
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


def ma_pair(sig, p=None):
    """-> (快线, 慢线)。两线都在**信号日历**上算，慢线未成形前为 NaN。"""
    p = p or params()
    f = sig.rolling(int(p["FAST"]), min_periods=int(p["FAST"])).mean()
    s = sig.rolling(int(p["SLOW"]), min_periods=int(p["SLOW"])).mean()
    return f, s


def month_ends(idx):
    """每月最后一个交易日（含样本末根，便于口径对齐）。"""
    idx = pd.DatetimeIndex(idx)
    out = [idx[i] for i in range(len(idx) - 1)
           if (idx[i].year, idx[i].month) != (idx[i + 1].year, idx[i + 1].month)]
    if len(idx):
        out.append(idx[-1])
    return out


# ---------------------------------------------------------------- 3. 信号
def golden_at(sig, fast, slow, d, p=None):
    """`d` 日收盘的金叉状态：True = 快线在慢线上方。慢线未成形时返回 None（= 维持）。"""
    p = p or params()
    fv, sv = float(fast.loc[d]), float(slow.loc[d])
    if not (np.isfinite(fv) and np.isfinite(sv)):
        return None
    if fv <= sv * (1.0 + float(p["HYST"])):
        return False
    if p["PRICE_FILTER"] and float(sig.loc[d]) <= sv:
        return False
    return True


def golden_raw(leg, p=None, as_of=None):
    """信号日历上的 0/1 **方向状态**（`d` 日收盘时按 `d` 日数据算出的状态）。

    预热期（慢线 NaN）记为 1.0：起点即满仓，与买入持有同口径，不另计建仓费。
    这里只算**门**（0 / FLOOR / 1），波动层在 `target_raw()` 里乘上去 —— 分开算的理由是
    `crosses()` 只该报「方向翻转」，不能被几十次 px 级的仓位微调污染。
    """
    p = p or params()
    sig = signal_of(leg, p, as_of=as_of)
    if not len(sig):
        return pd.Series(dtype="float64")
    fast, slow = ma_pair(sig, p)
    raw = pd.Series(np.nan, index=sig.index, dtype="float64")
    for d in sig.index:
        v = golden_at(sig, fast, slow, d, p)
        if v is not None:
            raw.loc[d] = 1.0 if v else float(p["FLOOR"])
    return raw.ffill().fillna(1.0)


def vol_series(leg, p=None, as_of=None):
    """年化已实现波动：`win` 日收益标准差 × √252（信号日历上）。"""
    p = p or params()
    sig = signal_of(leg, p, as_of=as_of)
    if not len(sig):
        return pd.Series(dtype="float64")
    return sig.pct_change().rolling(int(p["VOL_WIN"]), min_periods=int(p["VOL_WIN"])
                                    ).std(ddof=1) * np.sqrt(252.0)


def vol_scale(leg, p=None, as_of=None):
    """波动层系数 `min(1, TV / vol)`。`VOL_ON=False` 时恒 1.0（= 纯二元金叉）。"""
    p = p or params()
    sig = signal_of(leg, p, as_of=as_of)
    if not p["VOL_ON"]:
        return pd.Series(1.0, index=sig.index, dtype="float64")
    v = vol_series(leg, p, as_of=as_of).reindex(sig.index)
    return (float(p["TV"]) / v).replace([np.inf, -np.inf], np.nan).clip(upper=1.0).fillna(1.0)


def deadzone(base, tgt, dead):
    """死区：**方向状态未变**时，`|新目标 − 现仓| < dead` 就不动手；方向一变立刻执行。

    这是波动层唯一的「省换手」机制 —— 没有它，vol 每天抖 0.5% 就会天天扣费
    （probe 实测：死区 0 → 934 次调仓，10pp → 132 次，收益几乎不变）。
    """
    b = base.to_numpy(dtype=float)
    g = tgt.to_numpy(dtype=float)
    out = np.empty(len(b))
    hold, prev = float(g[0]), float(b[0])
    out[0] = hold
    for i in range(1, len(b)):
        if abs(b[i] - prev) > 1e-12 or abs(g[i] - hold) >= float(dead) - 1e-12:
            hold = g[i]
        out[i] = hold
        prev = b[i]
    return pd.Series(out, index=base.index, dtype="float64")


def target_raw(leg, p=None, as_of=None):
    """信号日历上的**目标仓位** = 方向状态 × 波动层系数，再过死区。

    语义：`d` 日收盘定下的目标 → `d+1` 收盘成交（映射到净值日历由 `state_daily()` 做）。
    """
    p = p or params()
    base = golden_raw(leg, p, as_of=as_of)
    if not len(base):
        return base
    sc = vol_scale(leg, p, as_of=as_of).reindex(base.index).ffill().fillna(1.0)
    tgt = (base.astype(float) * sc).clip(0.0, 1.0)
    dead = float(p["VOL_DEAD"])
    return deadzone(base, tgt, dead) if dead > 0 else tgt


def target_now(leg, p=None):
    """最新一根信号日上的目标仓位（每日报告用；与 `build()` 最后一位一致）。"""
    t = target_raw(leg, p)
    return float(t.iloc[-1]) if len(t) else 1.0


def crosses(leg, p=None, since=None):
    """金叉/死叉**翻转点**（信号日，不是成交日）-> [{date, kind, frm, to, close, fast, slow}]。

    `since` 用于丢掉**净值起点之前**的翻转：那些点没有可交易的净值，算进来会让
    「翻转次数 == 调仓次数」这条断言对不上（信号日历比 QDII 净值日历长）。
    """
    p = p or params()
    sig = signal_of(leg, p)
    fast, slow = ma_pair(sig, p)
    raw = golden_raw(leg, p)
    s = raw.to_numpy(dtype=float)
    out, prev_i = [], 0
    for i in range(1, len(s)):
        if abs(s[i] - s[i - 1]) > 1e-12:
            d = raw.index[i]
            if since is not None and d < pd.Timestamp(since):
                prev_i = i
                continue
            out.append(dict(date=d, kind="buy" if s[i] > s[i - 1] else "sell",
                            frm=float(s[i - 1]), to=float(s[i]), close=float(sig.loc[d]),
                            fast=float(fast.loc[d]), slow=float(slow.loc[d]),
                            gap=float(fast.loc[d] / slow.loc[d] - 1.0), gap_days=i - prev_i))
            prev_i = i
    return out


def decisions_month(leg, p=None):
    """月末档：每月最后一个交易日收盘定仓（**含波动层**），次一交易日成交。

    仓位 = 方向状态 × `min(1, TV/vol)`，与日频档同一套权重口径；未成形的月份跳过。
    """
    p = p or params()
    sig = signal_of(leg, p)
    fast, slow = ma_pair(sig, p)
    sc = vol_scale(leg, p).reindex(sig.index).ffill().fillna(1.0)
    out = []
    for d in month_ends(sig.index):
        v = golden_at(sig, fast, slow, d, p)
        if v is None:
            continue
        gate = 1.0 if v else float(p["FLOOR"])
        out.append(dict(date=d, close=float(sig.loc[d]), fast=float(fast.loc[d]),
                        slow=float(slow.loc[d]), gap=float(fast.loc[d] / slow.loc[d] - 1.0),
                        golden=bool(v), vol=float(sc.loc[d]),
                        target=float(np.clip(gate * float(sc.loc[d]), 0.0, 1.0))))
    return out


def state_daily(nav_index, leg, p=None, as_of=None):
    """日频：信号在 `d` 日收盘成立 → **`d+1` 收盘成交**（与月度档同一 T+1 纪律）。

    返回序列的语义是「该净値日**收盘成交后**的仓位」，`run_leg()` 再 `shift(1)` 让新仓位
    从下一根收益开始生效 —— 两步合起来 = 决策 t → 成交 t+1 → 收益自 t+2 起。
    """
    raw = target_raw(leg, p, as_of=as_of)
    idx = pd.DatetimeIndex(nav_index)
    if not len(raw):
        return pd.Series(1.0, index=idx, dtype="float64")
    return raw.reindex(idx).ffill().fillna(1.0).shift(1).fillna(1.0)


def state_series(nav_index, dec, p=None):
    """把月末决策映射到净值日历：**决策日的次一根净值日成交**，其余时间保持。"""
    p = p or params()
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

    `TRIGGER="daily"`（默认）：日频状态（金叉门 × 波动层，默认 `VOL_ON=True`），
    `dec` = **金叉/死叉翻转点**（信号日，不含波动层的仓位微调）；
    `TRIGGER="month"`：月末定仓 + 次月生效（对照），`dec` = 月末决策。
    """
    p = params(**kw)
    nav = load_nav(leg)
    if str(p["TRIGGER"]) == "month":
        dec = decisions_month(leg, p)
        return nav, state_series(nav.index, dec, p), dec
    return nav, state_daily(nav.index, leg, p), crosses(leg, p, since=nav.index[0])


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
    """**月末采样**的最大回撤（与 v7/v9 同一度量口径，便于并排比较）。"""
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


def fmt_pct(x, nd=2):
    return "—" if x is None else f"{x * 100:+.{nd}f}%"


LEVEL_TXT = {1.0: "满仓", 0.5: "半仓", 0.0: "清仓"}


def level_name(x):
    """仓位 -> 中文档位名（图上标注与每日报告共用）。"""
    return LEVEL_TXT.get(round(float(x), 6), f"{float(x):.0%}")


def _self_check():
    """快速自检：两条腿的样本、当前仓位、方向翻转次数、波动层系数。"""
    for k in POOL:
        nav, st, dec = build(k)
        assert float(st.max()) <= 1.0 + 1e-12 and float(st.min()) >= -1e-12, (k, st.min(), st.max())
        _n, st_m, dec_m = build(k, TRIGGER="month")
        fast, slow = ma_pair(load_signal(k))
        vol = float(vol_series(k).iloc[-1])
        print(f"{k} {rule.LEGS[k]['name']}: 净值 {nav.index[0].date()}~{nav.index[-1].date()} "
              f"n={len(nav)} · 当前 {st.iloc[-1]:.0%}（月末档 {st_m.iloc[-1]:.0%}）· "
              f"目标 {target_now(k):.0%} = 门 {dec[-1]['to']:.0%} × "
              f"min(1, {TV:.0%}/{vol:.0%}) · "
              f"方向翻转 {len(dec)} 次 / 月末决策 {len(dec_m)} 条 · "
              f"末次 MA{FAST} {fast.iloc[-1]:.1f} vs MA{SLOW} {slow.iloc[-1]:.1f}"
              f"（{fast.iloc[-1] / slow.iloc[-1] - 1.0:+.1%}）")
    print(f"MA{FAST}/{SLOW} · 触发 {TRIGGER} · 迟滞 {HYST:.0%} · 死叉后 {FLOOR:.0%} · "
          f"波动层 {'开' if VOL_ON else '关'}（TV {TV:.0%} / vol{VOL_WIN} / 死区 {VOL_DEAD:.0%}）· "
          f"信号 {SIGNAL} · 单边成本 {COST:.1%} · 空仓 {CASH_ANNUAL:.2%}")


if __name__ == "__main__":
    _self_check()
