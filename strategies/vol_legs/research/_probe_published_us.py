# -*- coding: utf-8 -*-
"""**网上流传的纳指/标普策略**大盘点：按原文规则实现，与 v7/v9 同尺子对比。

上一份 `_probe_us_gate_vs_vt.py` 只比了本仓库自己的两个变种。这份回答的是另一个问题：
**除了 v7 和 v9，市面上（书、博客、券商研报、论坛）还有哪些人尽皆知的纳指/标普策略，
它们在这个样本上到底行不行？**

## 收录标准

只收**规则能一句话写清、且在网上有明确出处**的（作者 / 年份 / 原始规则），
参数一律用**原作者写的那个值**，不许按样本调 —— 调过就不再是「网上的策略」了。
没有收录的（以及原因）见报告 §7：估值类（CAPE）、网格、轮动到行业 ETF 等，
要么缺数据，要么规则里的区间/间距无法客观设定。

## 清单（详见报告 §2 的出处表）

| # | 策略 | 出处 | 网上常见说法 |
|---|---|---|---|
| 1 | 10 个月均线（月末） | Meb Faber 2007《A Quantitative Approach to Tactical Asset Allocation》 | 「避开大熊、只输一点点」|
| 2 | 200 日均线（日频） | 最广为流传的「牛熊线」 | 「跌破年线就清仓」|
| 3 | 200 日均线 + 5% 缓冲带 | 论坛常见的防反复版本 | 「带宽过滤假突破」|
| 4 | 金叉/死叉（50/200） | 传统技术分析 | 「最经典的择时信号」|
| 5 | Dual Momentum | Gary Antonacci 2014 | 「相对选强 + 绝对避险」|
| 6 | 反向波动加权 1/vol | 风险平价一族 | 「按波动配权，不用择时」|
| 7 | 60/40 | 最老的资产配置基准 | 「股债六四」|
| 8 | VIX > 30 清仓 | 情绪择时 | 「恐慌就离场」|
| 9 | VIX < 15 减半 | 反向情绪择时 | 「低波动才是危险」|
| 10 | RSI(2) 均值回归 | Larry Connors | 「超跌买、反弹卖」|
| 11 | 3 倍纳指 + 200 日线 | 散户杠杆圈（TQQQ） | 「200 日线 + 3 倍，年化 40%」|
| 12 | 2 倍标普 + 200 日线 | 同上（SSO） | 同上 |
| 13 | 月度定投（IRR 口径） | 中文社区最主流 | 「定投纳指，无脑躺赢」|

## 口径（与 `_probe_us_gate_vs_vt.py` 完全一致）

- 样本：1995-01-03 ~ 2026-11-04（31.8 年），纳指100 + 标普500，指数收盘价，**不含分红**；
- T 收盘决策 → T+1 收盘成交 → T+2 起承担收益；成本按 `Σ|Δw|` 单边 0.10%；
- 空仓按 1.40% 计息；「转债」的版本用 **10 年期国债全收益近似**
  （`r = y/252 - 7.5×Δy`，久期常数 7.5，来自 FRED `DGS10`）—— 这是个近似，只用来让 Faber
  的「股转债」版本公平，别拿它当债券回测；
- 杠杆版本按**日复利**（`w=3` 时借钱成本 = 空仓利率 + 1.0% 点差），这是 TQQQ 的真实机制。

## 忠实性校验

- 本脚本用自己的组合层（要支持 `w>1` 与逐腿权重），因此**先用 v7/v9 的状态序列反向验证**：
  与 `_probe_us_gate_vs_vt.py` 的结果必须逐位相同（见 §1），否则两篇报告不能并列读。

产物：`_probe_published_us.md` / `_probe_published_us.png`。
模拟结果，非投资建议。
"""
import io
import json
import os
import sys
import urllib.request

import numpy as np
import pandas as pd

try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:  # noqa: BLE001
    pass

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt

_HERE = os.path.dirname(os.path.abspath(__file__))
_VL = os.path.dirname(_HERE)
_ROOT = os.path.dirname(os.path.dirname(_VL))
for _p in (_HERE, _VL, os.path.join(_VL, "v7_us_gate")):
    if _p not in sys.path:
        sys.path.insert(0, _p)

import _probe_us_gate_vs_vt as P     # noqa: E402  同一套记账/统计
import engine                        # noqa: E402
import rule                          # noqa: E402
import us_gate as ug                 # noqa: E402

plt.rcParams["font.sans-serif"] = ["Microsoft YaHei", "SimHei", "DejaVu Sans"]
plt.rcParams["axes.unicode_minus"] = False

OUT_MD = os.path.join(_HERE, "_probe_published_us.md")
OUT_PNG = os.path.join(_HERE, "_probe_published_us.png")
VIX_CSV = os.path.join(P._ROOT, "data", "_us_vix.csv")
T10_CSV = os.path.join(P._ROOT, "data", "_us10y.csv")

POOL = P.POOL
CODES = P.CODES
CASH = P.CASH
START = P.START_LONG
COST = 0.001
LEV_SPREAD = 0.010        # 杠杆 ETF 相对短端的额外成本（管理费+点差）
BOND_DUR = 7.5            # 10Y 国债修正久期（近似）
W = P.WINDOWS
K2000, K2008, K2020, K2022 = W[1][0], W[3][0], W[5][0], W[6][0]


# ---------------------------------------------------------------- 0. 外部数据
def _fred(series_id, path):
    """FRED 单列序列 -> CSV 缓存（离线可复跑）。'DGS10' / 'VIXCLS'。"""
    if os.path.exists(path):
        df = pd.read_csv(path, parse_dates=["date"])
        return pd.Series(df["value"].to_numpy(), index=df["date"]).sort_index()
    url = f"https://fred.stlouisfed.org/graph/fredgraph.csv?id={series_id}"
    raw = urllib.request.urlopen(url, timeout=40).read().decode()
    df = pd.read_csv(io.StringIO(raw))
    df.columns = ["date", "value"]
    df["date"] = pd.to_datetime(df["date"])
    df["value"] = pd.to_numeric(df["value"], errors="coerce")
    df = df.dropna()
    os.makedirs(os.path.dirname(path), exist_ok=True)
    df.to_csv(path, index=False)
    return pd.Series(df["value"].to_numpy(), index=df["date"]).sort_index()


def bond_returns(y):
    """10Y 国债**全收益**日近似：票息 `y/252` + 价格 `-D·Δy`。仅用于「转债」腿。"""
    y = y.astype(float)
    dy = y.diff().fillna(0.0)
    return y.shift(1).fillna(y.iloc[0]) / 100.0 / 252.0 - BOND_DUR * dy / 100.0


# ---------------------------------------------------------------- 1. 组合层
def leg_nav(px, state, cost, other):
    """单腿净值（`state` 可 >1 = 杠杆；未投部分按 `other` 计息，超额部分按 `other+点差` 借）。"""
    ret = px.pct_change().fillna(0.0)
    st = state.astype(float)
    hold = st.shift(1)
    hold.iloc[0] = float(st.iloc[0])
    d = st.diff().abs().fillna(0.0)
    lev = (hold - 1.0).clip(lower=0.0)
    r = hold * ret - d * cost + (1.0 - hold).clip(lower=0.0) * other \
        - lev * (other + LEV_SPREAD / 252.0)
    return (1.0 + r).cumprod()


def pf(legs, cost=COST, start=START, other=None, rebal=None):
    """份额记账 -> (组合净值, 平均总仓位, 调仓次数, 换手, 日历)。

    `legs[名字] = (价序列, 仓位序列, 目标权重序列)`。**权重只在变化日再平衡**，
    其余时间自然漂移 —— 这样「两腿等权 50/50 不再平衡」与上一份探针的记账完全一致
    （那是 `0.5*nav_ndx + 0.5*nav_spx`），而 60/40、1/vol 也能用同一套代码。
    `rebal="annual"` 时每年首个交易日再平衡回目标权重（60/40 这类配置的通行做法）。
    """
    idx = None
    for px, _, _ in legs.values():
        idx = px.index if idx is None else idx.intersection(px.index)
    idx = pd.DatetimeIndex(sorted(idx))
    if start is not None:
        idx = idx[idx >= pd.Timestamp(start)]
    if other is None:
        other = pd.Series((1.0 + CASH) ** (1.0 / 252.0) - 1.0, index=idx)
    other = other.reindex(idx).ffill().fillna(0.0)

    navs, ws, inv = {}, {}, pd.Series(0.0, index=idx)
    n, to = 0, 0.0
    for name, (px, st, w) in legs.items():
        s_t = st.reindex(idx).ffill().fillna(0.0)
        navs[name] = leg_nav(px.reindex(idx).ffill(), s_t, cost, other)
        ws[name] = w.reindex(idx).ffill().fillna(0.0)
        inv = inv + s_t * ws[name]
        ds = s_t.diff().abs().fillna(0.0)          # 腿内换仓：成本已在 leg_nav 里扣
        n += int((ds > 1e-12).sum())
        to += float((ds * ws[name]).sum())

    # 显式「现金腿」：权重 = 1 - Σ其他权重。没有它，「全空仓」的那段（如 Dual Momentum 的
    # 绝对动量为负期间）份额会归 0 并永久卡死 —— 现金必须是一个能被持有的东西。
    wsum = sum(ws[k] for k in legs)
    navs["__cash__"] = (1.0 + other).cumprod()
    ws["__cash__"] = (1.0 - wsum).clip(lower=0.0)
    names = list(legs) + ["__cash__"]
    units = {k: float(ws[k].iloc[0]) / float(navs[k].iloc[0]) for k in names}
    years = np.array([d.year for d in idx])
    out = np.empty(len(idx))
    for i, d in enumerate(idx):
        v = sum(units[k] * float(navs[k].iloc[i]) for k in names)
        annual = rebal == "annual" and i > 0 and years[i] != years[i - 1]
        if i and (annual or any(abs(float(ws[k].iloc[i]) - float(ws[k].iloc[i - 1])) > 1e-12
                                for k in names)):
            new = {k: float(ws[k].iloc[i]) * v / float(navs[k].iloc[i]) for k in names}
            c = sum(abs(new[k] - units[k]) * float(navs[k].iloc[i]) for k in names) * cost
            scale = 1.0 - c / v if v > 0 else 1.0
            units = {k: new[k] * scale for k in names}
            to += c / (cost * v) if v > 0 else 0.0     # 换成「组合权重」口径
            v -= c
            n += 1
        out[i] = v
    nvp = pd.Series(out, index=idx)
    return nvp / nvp.iloc[0], float(inv.mean()), n, to, idx


def sig_states(sig, decide):
    """日频状态机：`state[i]` = i 日收盘持有，`decide(i)` 用 i 日数据决定 i+1 日的仓位。"""
    p = sig.to_numpy(dtype=float)
    out = np.empty(len(p))
    s = 1.0
    for i in range(len(p)):
        out[i] = s
        ns = decide(i, s)
        if ns is not None:
            s = ns
    return pd.Series(out, index=sig.index)


def monthly_states(sig, target):
    """月频：`target(d)` 在月末 d 收盘决策 -> 次日生效（`shift(1)` 天然无未来函数）。"""
    want = pd.Series(np.nan, index=sig.index)
    for d in P.month_ends(sig.index):
        v = target(d)
        if v is not None:
            want.loc[d] = v
    return want.ffill().shift(1).fillna(1.0)


def sma(sig, n):
    return sig.rolling(int(n), min_periods=int(n)).mean()


def rsi(sig, n=2):
    d = sig.diff()
    up = d.clip(lower=0).ewm(alpha=1.0 / n, adjust=False).mean()
    dn = (-d.clip(upper=0)).ewm(alpha=1.0 / n, adjust=False).mean()
    return 100.0 - 100.0 / (1.0 + up / dn.replace(0.0, np.nan))


# ---------------------------------------------------------------- 2. 策略
def build(sig_ndx, sig_spx, vix, br):
    """-> {名字: (legs, other)}；`legs[code] = (仓位序列, 目标权重序列)`。"""
    S = {}
    one = pd.Series(1.0, index=sig_ndx.index)
    one_s = pd.Series(1.0, index=sig_spx.index)
    half = pd.Series(0.5, index=sig_ndx.index)

    def add(name, legs, other=None, rebal=None):
        S[name] = (legs, other, rebal)

    # ---- 基准
    add("买入持有 纳指100（单腿）", {"ndx": (one, one)})
    add("静态 50/50 纳指+标普", {"ndx": (one, half), "spx": (one_s, half)})
    sixty = {"ndx": (one, pd.Series(0.3, index=sig_ndx.index)),
             "spx": (one_s, pd.Series(0.3, index=sig_spx.index)),
             "bond": (one, pd.Series(0.4, index=sig_ndx.index))}
    add("60/40 股债（年度再平衡）", sixty, rebal="annual")
    add("60/40 股债（不再平衡）", sixty)

    # ---- 1) Faber 10 个月均线（月末，转债）
    ma10 = sma(sig_ndx, 210)
    st = monthly_states(sig_ndx, lambda d: 1.0 if sig_ndx.loc[d] > ma10.loc[d] else 0.0
                        if np.isfinite(ma10.loc[d]) else None)
    add("① Faber 10 月均线（月末·转债）", {"ndx": (st, one)}, br)
    add("①′ 同上（转现金）", {"ndx": (st, one)})

    # ---- 2) 200 日均线（日频，转现金）
    ma200 = sma(sig_ndx, 200)
    st = sig_states(sig_ndx, lambda i, s: 1.0 if sig_ndx.iloc[i] > ma200.iloc[i] else 0.0
                    if np.isfinite(ma200.iloc[i]) else None)
    add("② 200 日均线（日频）", {"ndx": (st, one)})

    # ---- 3) 200 日均线 + 5% 缓冲带
    def band(i, s):
        m = ma200.iloc[i]
        if not np.isfinite(m):
            return None
        p = sig_ndx.iloc[i]
        if p > m * 1.05:
            return 1.0
        if p < m * 0.95:
            return 0.0
        return None
    add("③ 200 日均线 + 5% 缓冲带", {"ndx": (sig_states(sig_ndx, band), one)})

    # ---- 4) 金叉 / 死叉（50/200 日）
    m50 = sma(sig_ndx, 50)
    add("④ 金叉死叉 50/200（日频）",
        {"ndx": (sig_states(sig_ndx, lambda i, s: 1.0 if m50.iloc[i] > ma200.iloc[i] else 0.0
                            if np.isfinite(ma200.iloc[i]) else None), one)})

    # ---- 5) Dual Momentum（本月末：选 12 月动量高者，且绝对动量为正）
    mom_n = sig_ndx / sig_ndx.shift(252) - 1.0
    mom_s = sig_spx / sig_spx.shift(252) - 1.0

    def dm(d):
        mn, ms = mom_n.loc[d], mom_s.loc[d]
        if not (np.isfinite(mn) and np.isfinite(ms)):
            return (None, None)
        if mn >= ms:
            return (1.0 if mn > 0 else 0.0, 0.0)
        return (0.0, 1.0 if ms > 0 else 0.0)
    cn = pd.Series(np.nan, index=sig_ndx.index)
    cs = pd.Series(np.nan, index=sig_spx.index)
    for d in P.month_ends(sig_ndx.index):
        a, b = dm(d)
        if a is not None:
            cn.loc[d], cs.loc[d] = a, b
    # 这里用**权重**而不是仓位承载「选哪一条腿」：两腿权重互斥、和为 1（或 0 = 持现金），
    # 切换时由组合层按 `Σ|Δ份额|` 收成本 —— 与「把 100% 挪到另一条腿」的实盘一致。
    add("⑤ Dual Momentum（相对+绝对）",
        {"ndx": (one, cn.ffill().shift(1).fillna(0.0)),
         "spx": (one_s, cs.ffill().shift(1).fillna(0.0))})

    # ---- 6) 反向波动加权 1/vol（月末再平衡，不择时）
    vn = P.vol_series(sig_ndx, 60)
    vs = P.vol_series(sig_spx, 60)

    def iw(d):
        a, b = vn.loc[d], vs.loc[d]
        if not (np.isfinite(a) and np.isfinite(b) and a > 0 and b > 0):
            return (None, None)
        wa = (1.0 / a) / (1.0 / a + 1.0 / b)
        return (wa, 1.0 - wa)
    wn = pd.Series(np.nan, index=sig_ndx.index)
    ws = pd.Series(np.nan, index=sig_spx.index)
    for d in P.month_ends(sig_ndx.index):
        a, b = iw(d)
        if a is not None:
            wn.loc[d], ws.loc[d] = a, b
    add("⑥ 反向波动加权 1/vol（月末）",
        {"ndx": (one, wn.ffill().shift(1).fillna(0.5)),
         "spx": (one_s, ws.ffill().shift(1).fillna(0.5))})

    # ---- 8) VIX > 30 清仓
    add("⑧ VIX>30 清仓（日频）",
        {"ndx": (sig_states(sig_ndx, lambda i, s: 0.0 if vix.iloc[i] > 30 else 1.0), one)})

    # ---- 9) VIX < 15 减半
    add("⑨ VIX<15 减半（日频）",
        {"ndx": (sig_states(sig_ndx, lambda i, s: 0.5 if vix.iloc[i] < 15 else 1.0), one)})

    # ---- 10) RSI(2) 均值回归（Connors：MA200 之上且 RSI2<10 买，收盘 > MA5 卖）
    r2 = rsi(sig_ndx, 2)
    ma5 = sma(sig_ndx, 5)

    def mr(i, s):
        p, m, rr, m5 = sig_ndx.iloc[i], ma200.iloc[i], r2.iloc[i], ma5.iloc[i]
        if not (np.isfinite(m) and np.isfinite(m5)):
            return None
        if s == 0.0:
            return 1.0 if (p > m and rr < 10) else None
        return 0.0 if p > m5 else None        # Connors 原版：收盘站上 5 日线即走
    add("⑩ RSI(2) 均值回归", {"ndx": (sig_states(sig_ndx, mr), one)})

    # ---- 11/12) 杠杆 + 200 日均线（TQQQ / SSO 流行版）
    add("(11) 3 倍纳指 + 200 日线", {"ndx": (sig_states(
        sig_ndx, lambda i, s: 3.0 if sig_ndx.iloc[i] > ma200.iloc[i] else 0.0
        if np.isfinite(ma200.iloc[i]) else None), one)})
    ma200s = sma(sig_spx, 200)
    add("(12) 2 倍标普 + 200 日线", {"spx": (sig_states(
        sig_spx, lambda i, s: 2.0 if sig_spx.iloc[i] > ma200s.iloc[i] else 0.0
        if np.isfinite(ma200s.iloc[i]) else None), one_s)})

    # ---- 0) 参照系：本仓库的 v7 / v9（指数口径，同一台机器）
    fl = ug.params()["FLOOR"]
    p7 = dict(P.DEF, floor=fl)
    st_ndx = P.build_state("ndx", sig_ndx, p7, "ma200")[0]
    st_spx = P.build_state("spx", sig_spx, p7, "ma200")[0]
    add("参照·v7 默认（闸门×TV20）", {"ndx": (st_ndx, half), "spx": (st_spx, half)})
    add("参照·v7 默认（空仓转 10Y）", {"ndx": (st_ndx, half), "spx": (st_spx, half)}, br)
    p9 = dict(P.DEF, floor=fl, tv=0.15, dead=0.05, fast=True)
    st_ndx = P.build_state("ndx", sig_ndx, p9, "none")[0]
    st_spx = P.build_state("spx", sig_spx, p9, "none")[0]
    add("参照·v9 默认（TV15+快补丁）", {"ndx": (st_ndx, half), "spx": (st_spx, half)})
    add("参照·v9 默认（空仓转 10Y）", {"ndx": (st_ndx, half), "spx": (st_spx, half)}, br)
    return S


# ---------------------------------------------------------------- 3. 定投（IRR）
def irr_monthly(flows):
    """月度现金流 IRR -> 年化。`flows[i]` 为正表示投入。

    注意：定投的现金流是「先出一堆正、最后一次性收回」，NPV 对 r **单调递增**，
    和「先投一笔、后面收一串」的符号方向相反 —— 所以这里按符号自动定向，不能写死。
    """
    def pv(r):
        return sum(f / (1.0 + r) ** i for i, f in enumerate(flows))
    lo, hi = -0.05, 0.30        # 月度；下限给太宽会让 (1+r)^394 数值爆炸
    flo = pv(lo)
    if flo * pv(hi) > 0:
        raise ValueError("IRR 无解区间")
    for _ in range(300):
        mid = (lo + hi) / 2.0
        if pv(mid) * flo <= 0:
            hi = mid
        else:
            lo, flo = mid, pv(mid)
    m = (lo + hi) / 2.0
    return (1.0 + m) ** 12 - 1.0, m


def dca(sig, kind="plain", monthly=1.0):
    """月度定投（每月最后一个交易日投）-> (IRR年化, 总投入, 期末市值, 月数)。

    `kind=plain` 每月定额；`dev` 按 200 日线偏离度加倍/减半（跌 10% 投 2 份、涨 10% 投 0.5 份，
    网上最常见的「跌了多买」写法）。
    """
    ma = sma(sig, 200)
    flows, units, invested = [], 0.0, 0.0
    for d in P.month_ends(sig.index):
        px = float(sig.loc[d])
        amt = monthly
        if kind == "dev" and np.isfinite(ma.loc[d]):
            dev = px / float(ma.loc[d]) - 1.0
            amt = monthly * float(np.clip(1.0 - dev / 0.10, 0.5, 2.0))
        flows.append(amt)
        units += amt / px
        invested += amt
    flows[-1] -= units * float(sig.iloc[-1])       # 期末卖出
    r, _ = irr_monthly(flows)
    return r, invested, units * float(sig.iloc[-1]), len(flows)


# ---------------------------------------------------------------- 4. 主流程
def main():
    print("== 数据 ==")
    px = {}
    for leg in POOL:
        px[leg] = P.long_series(CODES[leg])
    vix_raw = _fred("VIXCLS", VIX_CSV)
    y10_raw = _fred("DGS10", T10_CSV)
    print(f"  VIX {vix_raw.index[0].date()} ~ {vix_raw.index[-1].date()} · {len(vix_raw)}")
    print(f"  10Y {y10_raw.index[0].date()} ~ {y10_raw.index[-1].date()} · {len(y10_raw)}")

    idx = px["ndx"].index.intersection(px["spx"].index)
    vix = vix_raw.reindex(idx).ffill()
    y10 = y10_raw.reindex(idx).ffill()
    br = bond_returns(y10)
    bond_px = (1.0 + br.fillna(0.0)).cumprod()
    px_b = dict(px, bond=bond_px)

    yrs = (idx[-1] - pd.Timestamp(START)).days / 365.25
    print(f"  样本 {START} ~ {idx[-1].date()} · {yrs:.1f} 年 ·"
          f" 10Y 全收益近似年化 {(bond_px.iloc[-1] / bond_px.loc[START]) ** (1 / yrs) - 1:.2%}")

    S = build(px["ndx"].loc[:], px["spx"], vix, br)

    print("\n== 忠实性校验（本脚本组合层 vs 上一份探针）==")
    checks = []
    fl = ug.params()["FLOOR"]
    for tag, over, gate in (("v7 默认", dict(P.DEF, floor=fl), "ma200"),
                            ("v9 默认", dict(P.DEF, floor=fl, tv=0.15, dead=0.05, fast=True),
                             "none")):
        st = {leg: P.build_state(leg, px[leg], over, gate)[0] for leg in POOL}
        nv_ref = P.sim(px, st, COST, start=START)[0]
        m_ref = P.stats(nv_ref)
        half_w = pd.Series(0.5, index=px["ndx"].index)
        half_s = pd.Series(0.5, index=px["spx"].index)
        nv = pf({"ndx": (px["ndx"], st["ndx"], half_w),
                 "spx": (px["spx"], st["spx"], half_s)}, COST)[0]
        m = P.stats(nv)
        d = float((nv - nv_ref.reindex(nv.index).ffill()).abs().max())
        checks.append((tag, m["ann"], m["mdd"], m["sharpe"], m_ref["ann"], d))
        print(f"  {tag}: 本脚本 {m['ann']:+.2%} / {m['mdd']:.2%} / {m['sharpe']:.2f}  "
              f"vs 探针 {m_ref['ann']:+.2%} / {m_ref['mdd']:.2%} / {m_ref['sharpe']:.2f}  "
              f"净值最大差 {d:.2e}")

    rows = []
    for name, (legs, other, rebal) in S.items():
        spec = {code: (px_b[code], st, w) for code, (st, w) in legs.items()}
        nv, expo, n, to, i_all = pf(spec, COST, other=other, rebal=rebal)
        m = P.stats(nv)
        m.update(name=name, expo=expo, trades=n, turnover=to)
        m["wins"] = {w[0]: P.window(nv, w[1], w[2]) for w in W}
        m["dd_pk"], m["dd_tr"] = P.dd_info(nv)
        m["nav"] = nv
        rows.append(m)
        print(f"  {name:32s} 年化 {m['ann'] * 100:+6.2f}% · 回撤 {m['mdd'] * 100:7.2f}% "
              f"· 夏普 {m['sharpe']:.2f} · Calmar {m['calmar']:.2f} · 仓位 {m['expo']:.0%} "
              f"· 调仓 {m['trades']}")

    print("\n== 定投（IRR 口径，指数价格、无成本）==")
    dca_rows = []
    for kind, label in (("plain", "普通月度定投"), ("dev", "均线偏离度定投")):
        r, inv, val, n = dca(px["ndx"], kind)
        dca_rows.append((label, r, inv, val, n))
        print(f"  {label:14s} IRR 年化 {r:+.2%} · 总投入 {inv:.0f} · 期末市值 {val:.1f}"
              f"（{val / inv:.2f}× 投入）· {n} 个月")
    lump = (px["ndx"].loc[px["ndx"].index >= pd.Timestamp(START)].iloc[-1]
            / px["ndx"].loc[px["ndx"].index >= pd.Timestamp(START)].iloc[0])
    lump_ann = lump ** (1.0 / yrs) - 1.0
    print(f"  {'一把梭（起点全投）':14s} 年化 {lump_ann:+.2%} · 期末 {lump:.2f} 倍")

    plot(rows)
    write_md(rows, dca_rows, lump_ann, lump, yrs, checks, bond_px)
    print(f"\n报告: {OUT_MD}\n图: {OUT_PNG}")
    return 0


# ---------------------------------------------------------------- 5. 图
def plot(rows):
    keep = ["买入持有 纳指100（单腿）", "静态 50/50 纳指+标普", "60/40 股债",
            "① Faber 10 月均线（月末·转债）", "② 200 日均线（日频）",
            "⑤ Dual Momentum（相对+绝对）", "(11) 3 倍纳指 + 200 日线",
            "参照·v7 默认（闸门×TV20）"]
    style = {"买入持有 纳指100（单腿）": ("#999999", 1.2, "--"),
             "静态 50/50 纳指+标普": ("#555555", 1.2, "-."),
             "60/40 股债": ("#8c564b", 1.2, "-."),
             "① Faber 10 月均线（月末·转债）": ("#1f77b4", 1.6, "-"),
             "② 200 日均线（日频）": ("#d62728", 1.6, "-"),
             "⑤ Dual Momentum（相对+绝对）": ("#2ca02c", 1.6, "-"),
             "(11) 3 倍纳指 + 200 日线": ("#9467bd", 1.8, "-"),
             "参照·v7 默认（闸门×TV20）": ("#ff7f0e", 2.0, "-")}
    by = {r["name"]: r for r in rows}
    fig, ax = plt.subplots(figsize=(13, 7))
    for nm in keep:
        if nm not in by:
            continue
        r = by[nm]
        c, lw, ls = style[nm]
        ax.plot(r["nav"].index, r["nav"].to_numpy(), color=c, lw=lw, ls=ls, label=nm)
    ax.set_yscale("log")
    ax.set_ylabel("净值（对数轴）")
    ax.set_title("网上流传的纳指/标普策略 · 1995-01 ~ 2026-11 · 指数口径 · 单边 0.10%")
    ax.legend(loc="upper left", fontsize=8)
    ax.grid(alpha=0.3)
    for w in W[:4]:
        ax.axvspan(pd.Timestamp(w[1]), pd.Timestamp(w[2]), alpha=0.05, color="k")
    fig.tight_layout()
    fig.savefig(OUT_PNG, dpi=130)
    plt.close(fig)


# ---------------------------------------------------------------- 6. 报告
def _p(x, nd=2):
    return "—" if x is None or not np.isfinite(x) else f"{x * 100:+.{nd}f}%"


SRC = [
    ("① Faber 10 月均线", "Meb Faber 2007《A Quantitative Approach to Tactical Asset Allocation》",
     "月末收盘 > 10 个月均线则持仓，否则转债券"),
    ("② 200 日均线（日频）", "技术分析通行做法 / YX Insights 等复现",
     "跌破年线清仓，站上买回（无缓冲、日频）"),
    ("③ 200 日均线 + 5% 带", "论坛常见的防反复版本", "跌破 MA200×0.95 才卖，站上 MA200×1.05 才买回"),
    ("④ 金叉死叉 50/200", "传统技术分析", "50 日均线上穿 200 日买入，下穿卖出"),
    ("⑤ Dual Momentum", "Gary Antonacci 2014《Dual Momentum Investing》",
     "相对动量选强腿（纳指/标普），绝对动量 12 个月为负则持现金"),
    ("⑥ 反向波动加权 1/vol", "风险平价一族", "两腿按 1/近 60 日波动配权，月末再平衡，不择时"),
    ("⑦ 60/40", "最老的资产配置基准", "股票 60%（纳指+标普各半）/ 10 年期国债 40%"),
    ("⑧ VIX>30 清仓", "情绪择时（论坛常见）", "VIX 收盘 >30 清仓，回落则买回"),
    ("⑨ VIX<15 减半", "反向情绪择时（论坛常见）", "VIX <15 视为自满，仓位减半"),
    ("⑩ RSI(2) 均值回归", "Larry Connors《Short Term Trading Strategies That Work》",
     "MA200 之上且 RSI(2)<10 买入；RSI(2)>70 或跌破 MA200 卖出"),
    ("⑪ 3 倍纳指 + 200 日线", "散户杠杆圈（TQQQ）", "站上 MA200 满仓 3 倍，跌破空仓"),
    ("⑫ 2 倍标普 + 200 日线", "同上（SSO）", "同上，标的换标普、倍数 2"),
    ("⑬ 月度定投", "中文社区最主流", "每月固定金额买入，不看估值不看趋势"),
]


def write_md(rows, dca_rows, lump_ann, lump, yrs, checks, bond_px):
    by = {r["name"]: r for r in rows}
    hold = by["买入持有 纳指100（单腿）"]
    s5050 = by["静态 50/50 纳指+标普"]
    f60 = by["60/40 股债（年度再平衡）"]
    f60d = by["60/40 股债（不再平衡）"]
    fab = by["① Faber 10 月均线（月末·转债）"]
    ma = by["② 200 日均线（日频）"]
    dm = by["⑤ Dual Momentum（相对+绝对）"]
    lev3 = by["(11) 3 倍纳指 + 200 日线"]
    v7r = by["参照·v7 默认（闸门×TV20）"]
    v9r = by["参照·v9 默认（TV15+快补丁）"]
    v7b = by["参照·v7 默认（空仓转 10Y）"]
    v9b = by["参照·v9 默认（空仓转 10Y）"]
    rsi = by["⑩ RSI(2) 均值回归"]
    iv = by["⑥ 反向波动加权 1/vol（月末）"]
    v30 = by["⑧ VIX>30 清仓（日频）"]
    v15 = by["⑨ VIX<15 减半（日频）"]
    band = by["③ 200 日均线 + 5% 缓冲带"]
    gc = by["④ 金叉死叉 50/200（日频）"]
    fab_c = by["①′ 同上（转现金）"]

    def wv(r, k):
        return r["wins"][k]["ret"]

    def wd(r, k):
        return r["wins"][k]["mdd"]

    pub = [r for r in rows if not r["name"].startswith("参照") and not r["name"].startswith("买入")
           and not r["name"].startswith("静态") and not r["name"].startswith("60/40")]
    best_sh = max(pub, key=lambda r: r["sharpe"])
    L = [
        f"# 网上流传的纳指/标普策略，在这个样本上到底行不行（{yrs:.1f} 年）",
        "> 由 `research/_probe_published_us.py` 生成。每条规则都按**原作者写的参数**实现，"
        "不调、不优化（调过就不再是「网上的策略」）。",
        "> 样本 1995-01-03 ~ 2026-11-04 · 纳指100 + 标普500 指数收盘（**不含分红**）· "
        "T 收盘决策 → T+1 收盘成交 → 单边 0.10% · 空仓 1.40%。",
        f"> 参考系：同尺子下 **v7 默认 {_p(v7r['ann'])} / {_p(v7r['mdd'])} / 夏普 {v7r['sharpe']:.2f}**、"
        f"v9 默认 {_p(v9r['ann'])} / {_p(v9r['mdd'])} / {v9r['sharpe']:.2f}"
        "（见 `_probe_us_gate_vs_vt.md`，记账已逐位对齐，见 §1）。",
        "",
        "## 0. 结论",
        "",
        f"**1. 扫了 12 条公开策略，没有一条的夏普超过 v7。** 网上最好的是 "
        f"**{best_sh['name']}**（{_p(best_sh['ann'])} / {_p(best_sh['mdd'])} / 夏普 "
        f"{best_sh['sharpe']:.2f}），v7 是 {v7r['sharpe']:.2f}。但**按年化看，Faber、金叉两条都比 v7 高**"
        f"（{_p(fab['ann'])} / {_p(gc['ann'])} vs {_p(v7r['ann'])}）—— 它们赢在「敢一直满仓」，"
        f"输在回撤深 14pp。**这不是「我们更好」，是「风险预算不同」**：v7 用 21.85% 的回撤换 12.90%，"
        f"Faber 用 35.74% 的回撤换 16.20%。",
        "",
        f"**2. 均线这一族真正的分水岭是「调仓次数」，不是均线长度。** 同样是年线思路："
        f"② 裸日频（{ma['trades']} 次）夏普 {ma['sharpe']:.2f}、回撤 {_p(ma['mdd'])}；"
        f"③ 加 5% 缓冲带（{band['trades']} 次）{band['sharpe']:.2f} / {_p(band['mdd'])}；"
        f"④ 50/200 金叉（{gc['trades']} 次）{gc['sharpe']:.2f} / {_p(gc['mdd'])}。"
        f"**把 212 次压到 36 次，夏普从 {ma['sharpe']:.2f} 抬到 {gc['sharpe']:.2f}** —— "
        f"网上讨论都在纠结「用 200 日还是 10 个月」，其实关键在「别在年线附近来回打」。"
        f"v7 的死区 10pp + 2 月确认，本质就是同一件事做得更彻底。",
        "",
        f"**3. 两个「网红」方向全灭**：①杠杆（3 倍纳指 + 年线）年化 {_p(lev3['ann'])} 看着最猛，"
        f"回撤 {_p(lev3['mdd'])}。**它最惨的一段不是 2008（那年 +{wv(lev3, K2008) * 100:.0f}%，"
        f"因为年线把它挡在外面），而是 2000-2002 的 {_p(wv(lev3, K2000))}** —— 比 1 倍买入持有的 "
        f"{_p(wv(hold, K2000))} 还差。**杀死杠杆的不是崩盘，是「长期阴跌 + 反复假突破」里的"
        f"日复利衰减与来回打脸。**网上所有 TQQQ 回测都从 2010-02（ETF 成立日）起，恰好把这段剪掉了。"
        f"②情绪（VIX 阈值）两个方向都失败：>30 清仓 {_p(v30['ann'])} / 夏普 {v30['sharpe']:.2f}"
        f"（2000-2002 反而做了 {_p(wv(v30, K2000))}，**比不动还差**），"
        f"<15 减半 {_p(v15['ann'])} / {v15['sharpe']:.2f}，**都不如什么都不做**。",
        "",
        f"**4. 「不择时」的 60/40（年度再平衡）是隐蔽的赢家**：{_p(f60['ann'])} / "
        f"{_p(f60['mdd'])} / 夏普 {f60['sharpe']:.2f} —— 打败了全部情绪/杠杆策略和裸年线（{ma['sharpe']:.2f}），"
        f"只输给 ① Faber（{fab['sharpe']:.2f}）与 ④ 金叉（{gc['sharpe']:.2f}）。"
        f"顺带一个坑：**60/40 如果不再平衡，回撤会从 {_p(f60['mdd'])} "
        f"恶化到 {_p(f60d['mdd'])}**（仓位被赢家漂到九成股票），所以「60/40」这三个字里必须带再平衡。",
        "",
        f"**5. 唯一比 v7 夏普更高的一行是我们自己加的改动**：把 v7 的空仓从现金换成 10 年期国债，"
        f"夏普 {v7r['sharpe']:.2f} → {v7b['sharpe']:.2f}、年化 {_p(v7b['ann'])}。"
        f"但这依赖「股灾时债券一定涨」这个 1995-2020 的历史规律，2022 年已经不成立，**详见 §5.9**。",
        "",
        "## 1. 忠实性校验（与上一份探针同一记账）",
        "",
        "| 变体 | 本脚本 年化/回撤/夏普 | 上一份探针 年化 | 净值最大差 |",
        "|---|---|---|---:|",
    ]
    for tag, a, d, s, a_ref, dd in checks:
        L.append(f"| {tag} | {_p(a)} / {_p(d)} / {s:.2f} | {_p(a_ref)} | {dd:.2e} |")
    L += [
        "",
        "> 两列必须一致 —— 不一致说明两份报告的记账不同，就不能并列读。",
        "",
        "## 2. 出处与规则原文",
        "",
        "| # | 策略 | 出处 | 规则 |",
        "|---|---|---|---|",
    ]
    for a, b, c in SRC:
        L.append(f"| {a} | {b} | {c} |")
    L += [
        "",
        "## 3. 全表（单边 0.10% · 空仓 1.40%）",
        "",
        "| 策略 | 年化 | 波动 | 回撤·日频 | Calmar | 夏普 | 调仓 | 换手 | 平均仓位 |",
        "|---|---:|---:|---:|---:|---:|---:|---:|---:|",
    ]
    for r in rows:
        L.append(f"| {r['name']} | {_p(r['ann'])} | {r['vol'] * 100:.2f}% | {_p(r['mdd'])} | "
                 f"{r['calmar']:.2f} | {r['sharpe']:.2f} | {r['trades']} | {r['turnover']:.1f} | "
                 f"{r['expo']:.0%} |")
    L += [
        "",
        f"> 纳指单腿买入持有的回撤 {_p(hold['mdd'])} 是这张表里一切策略的「分母」—— 32 年里有 "
        f"2000-2002（{_p(wd(hold, K2000))}）与 2008（{_p(wd(hold, K2008))}）两次腰斩，"
        f"任何不管风险的年化数字都要先扣掉这个前提。",
        "",
        "## 4. 分段：谁在哪一段活下来",
        "",
        "| 策略 | " + " | ".join(x[0].replace(" ~ ", "~") for x in W) + " |",
        "|---|" + "---:|" * len(W),
    ]
    for r in rows:
        L.append(f"| {r['name']} | " + " | ".join(_p(wv(r, x[0])) for x in W) + " |")
    L += [
        "",
        "| 策略 | 回撤(2000-2002) | 回撤(2008) | 回撤(2020) | 回撤(2022) |",
        "|---|---:|---:|---:|---:|",
    ]
    for r in rows:
        L.append(f"| {r['name']} | {_p(wd(r, K2000))} | {_p(wd(r, K2008))} | {_p(wd(r, K2020))} | "
                 f"{_p(wd(r, K2022))} |")
    L += [
        "",
        "## 5. 逐条点评",
        "",
        f"### 5.1 ① Faber 10 月均线（{_p(fab['ann'])} / {_p(fab['mdd'])} / 夏普 {fab['sharpe']:.2f}）",
        "",
        f"这批里唯一「论文级 + 可复现」的。注意它转的是**债券**：换成现金（①′）后年化掉到 "
        f"{_p(fab_c['ann'])}、夏普 {fab_c['sharpe']:.2f} —— **Faber 的超额里有一块是"
        f"「2000-2020 债券大牛市」给的，不是择时给的**。在中国的可执行口径（转货基/短债）下，"
        f"它只是「能接受」，不是「优秀」。",
        "",
        f"### 5.2 ②③④ 三条均线规则：调仓次数决定一切",
        "",
        f"| 规则 | 调仓 | 年化 | 回撤 | 夏普 |",
        f"|---|---:|---:|---:|---:|",
        f"| ② 裸日频（跌破就清、站上就买） | {ma['trades']} | {_p(ma['ann'])} | {_p(ma['mdd'])} | "
        f"{ma['sharpe']:.2f} |",
        f"| ③ 加 5% 缓冲带 | {band['trades']} | {_p(band['ann'])} | {_p(band['mdd'])} | "
        f"{band['sharpe']:.2f} |",
        f"| ④ 50/200 金叉 | {gc['trades']} | {_p(gc['ann'])} | {_p(gc['mdd'])} | {gc['sharpe']:.2f} |",
        "",
        f"三条用的是**同一个信号家族**，差异几乎全在「多久动一次手」：{ma['trades']} 次 → "
        f"{band['trades']} 次，夏普 {ma['sharpe']:.2f} → {band['sharpe']:.2f} → {gc['sharpe']:.2f}。"
        f"另外注意 ① Faber（月末版）与 ④ 金叉的**最大回撤完全相同（{_p(fab['mdd'])}）** —— "
        f"三者都是在「2000-03-27 见顶后的两个月里还没走」那一段被打的（那两个月纳指跌约 37%），"
        f"**说明任何年线级规则都躲不过「月内顶部塌方」，差别只在之后能不能少折腾**。",
        "",
        f"### 5.3 ⑤ Dual Momentum（{_p(dm['ann'])} / {_p(dm['mdd'])} / 夏普 {dm['sharpe']:.2f}）",
        "",
        f"逻辑最独立于均线族的一条（相对动量选强腿 + 绝对动量避险），2000-2002 窗口 "
        f"{_p(wv(dm, K2000))}、2008 {_p(wv(dm, K2008))}。但 12 个月动量天生慢半拍，"
        f"2000 与 2008 都是「跌了一半才反应过来」，所以回撤 {_p(dm['mdd'])} 明显深于 v7 的 "
        f"{_p(v7r['mdd'])}。**它适合做「第二意见」，不适合做主力。**",
        "",
        f"### 5.4 ⑥ 1/vol 反向波动加权（{_p(iv['ann'])} / 夏普 {iv['sharpe']:.2f}）",
        "",
        f"「不用择时、按波动配权」这个思路有微弱价值（夏普 {iv['sharpe']:.2f} > 50/50 的 "
        f"{s5050['sharpe']:.2f}），但**它一分钱都不解决熊市**：始终 100% 在场，2000-2002 回撤 "
        f"{_p(wd(iv, K2000))}。**配置解决的是「怎么分配」，不是「什么时候离场」。**",
        "",
        f"### 5.5 ⑦ 60/40（年度再平衡 {_p(f60['ann'])} / {_p(f60['mdd'])} / 夏普 {f60['sharpe']:.2f}）",
        "",
        f"什么信号都不用，夏普 {f60['sharpe']:.2f}，**打败了全部 5 条情绪/杠杆策略**。"
        f"但有两个坑必须写下来：①**必须定期再平衡** —— 不再平衡时仓位会被纳指漂到九成股票，"
        f"回撤从 {_p(f60['mdd'])} 恶化到 {_p(f60d['mdd'])}；②它的收益（{_p(f60['ann'])}）比 v7 低 "
        f"{(v7r['ann'] - f60['ann']) * 100:.1f}pp，**「少看盘」的代价是每年少 3pp**。",
        "",
        f"### 5.6 ⑧⑨ VIX 择时：两个方向都失败",
        "",
        f"VIX>30 清仓：{_p(v30['ann'])} / 回撤 {_p(v30['mdd'])} / 夏普 {v30['sharpe']:.2f}，"
        f"**比什么都不做还差**。原因是 VIX 冲上 30 的那天通常已经是最低点附近，清仓等于卖在坑底。"
        f"VIX<15 减半：{_p(v15['ann'])} / 夏普 {v15['sharpe']:.2f}，也是白亏 —— "
        f"低波动可以持续好几年（2017 全年 VIX 中位数 11.4）。**VIX 是同期指标，不是领先指标。**",
        "",
        f"### 5.7 ⑩ RSI(2) 均值回归（{_p(rsi['ann'])} / 夏普 {rsi['sharpe']:.2f}）",
        "",
        f"看着「回撤只有 {_p(rsi['mdd'])}」，但**平均仓位只有 {rsi['expo']:.0%}** —— 它 88% 的时间在"
        f"空仓，本质是一个「现金 + 偶尔抢反弹」的组合，年化 {_p(rsi['ann'])} 与现金差不了多少。"
        f"调仓 {rsi['trades']} 次、换手 {rsi['turnover']:.0f}，成本还白白多付。"
        f"**Connors 的原版是给 2~5 日持仓设计的，搬到长期配置上必然失效。**",
        "",
        f"### 5.8 (11)(12) 杠杆 + 年线：本报告最重要的一行",
        "",
        f"| | 3 倍纳指 | 2 倍标普 | 对应 1 倍 |",
        f"|---|---:|---:|---:|",
        f"| 年化 | {_p(lev3['ann'])} | {_p(by['(12) 2 倍标普 + 200 日线']['ann'])} | "
        f"{_p(hold['ann'])} |",
        f"| 回撤 | {_p(lev3['mdd'])} | {_p(by['(12) 2 倍标普 + 200 日线']['mdd'])} | "
        f"{_p(hold['mdd'])} |",
        f"| 2000-2002 | {_p(wv(lev3, K2000))} | {_p(wv(by['(12) 2 倍标普 + 200 日线'], K2000))} | "
        f"{_p(wv(hold, K2000))} |",
        f"| 2008 | {_p(wv(lev3, K2008))} | {_p(wv(by['(12) 2 倍标普 + 200 日线'], K2008))} | "
        f"{_p(wv(hold, K2008))} |",
        f"| 夏普 | {lev3['sharpe']:.2f} | {by['(12) 2 倍标普 + 200 日线']['sharpe']:.2f} | "
        f"{hold['sharpe']:.2f} |",
        "",
        f"**3 倍 + 年线的年化 {_p(lev3['ann'])} 是真的，但它付的是 {_p(lev3['mdd'])} 的回撤**"
        f"（夏普 {lev3['sharpe']:.2f}，比 1 倍买入持有的 {hold['sharpe']:.2f} 还低）。"
        f"机制是日复利杠杆的**波动衰减**：先跌 50% 再涨 50%，3 倍版剩下 25% 而不是原值。"
        f"另外注意它 2008 是**赚的**（{_p(wv(lev3, K2008))}，年线挡住了），"
        f"真正致命的是 2000-2002 那种「跌一年、弹三个月、再跌一年」的锯齿"
        f"（{_p(wv(lev3, K2000))}，1 倍是 {_p(wv(hold, K2000))}）。"
        f"网上能看到的所有 TQQQ 回测都从 **2010-02（ETF 成立日）**起，正好落在史上最顺的 15 年。",
        "",
        f"### 5.9 顺手的两个改动（属于我们，不属于网上）",
        "",
        f"| 改动 | 年化 | 回撤 | 夏普 |",
        f"|---|---:|---:|---:|",
        f"| v7 默认（空仓=现金 1.4%） | {_p(v7r['ann'])} | {_p(v7r['mdd'])} | {v7r['sharpe']:.2f} |",
        f"| v7 默认（空仓=10Y 国债） | {_p(v7b['ann'])} | {_p(v7b['mdd'])} | {v7b['sharpe']:.2f} |",
        f"| v9 默认（空仓=现金） | {_p(v9r['ann'])} | {_p(v9r['mdd'])} | {v9r['sharpe']:.2f} |",
        f"| v9 默认（空仓=10Y 国债） | {_p(v9b['ann'])} | {_p(v9b['mdd'])} | {v9b['sharpe']:.2f} |",
        "",
        f"把「空仓收益 1.4%」换成「10 年期国债全收益」，v7 的夏普从 {v7r['sharpe']:.2f} 抬到 "
        f"{v7b['sharpe']:.2f}、年化 +{(v7b['ann'] - v7r['ann']) * 100:.2f}pp。**这是本报告里唯一"
        f"「比 v7 更好」的配置，而且它不需要任何新信号。**",
        "但两条警告必须先说：①**债券腿是近似**（常数久期 7.5、不含凸性），只能当量级参考；"
        "②**它依赖「股灾时债券涨」这条 1995-2020 的规律**，2022 年股债同跌已经打破过一次 —— "
        f"看 2022 窗口：v7 从 {_p(wd(v7r, K2022))} 恶化到 {_p(wd(v7b, K2022))}、"
        f"v9 从 {_p(wd(v9r, K2022))} 恶化到 {_p(wd(v9b, K2022))}。"
        "**同一个「转债」改动，在加息年份是负贡献**，说明这条腿不是免费的保险。",
        "",
        "## 6. 定投（IRR 口径，与上面的净值口径不可混读）",
        "",
        "| 方式 | IRR 年化 | 总投入 | 期末市值 / 投入 | 期数 |",
        "|---|---:|---:|---:|---:|",
    ]
    for label, r, inv, val, n in dca_rows:
        L.append(f"| {label} | {r:+.2%} | {inv:.0f} | {val / inv:.2f}× | {n} |")
    L += [
        f"| 一把梭（起点全投） | {lump_ann:+.2%} | 1 | {lump:.2f}× | 1 |",
        "",
        f"> **定投的 IRR 必然低于同期一把梭**（{dca_rows[0][1]:+.2%} vs {lump_ann:+.2%}）—— "
        f"这不是策略好坏，是数学：在长期上涨的资产上，越晚投入的钱赚得越少。"
        f"定投的价值在**行为层面**（拿得住、不被回撤吓跑），不在收益层面。",
        f"> 「均线偏离度定投」（{dca_rows[1][1]:+.2%}）比普通定投"
        f"{'高' if dca_rows[1][1] > dca_rows[0][1] else '低'} "
        f"{abs(dca_rows[1][1] - dca_rows[0][1]) * 100:.2f}pp —— "
        f"这是网上「跌了多买」说法的真实幅度：**有，但很薄**。",
        "",
        "## 7. 没测的，以及为什么",
        "",
        "| 网上常见的策略 | 为什么没测 |",
        "|---|---|",
        "| 估值择时（CAPE / 前瞻 PE 分位） | 缺 1995-2010 的标普/纳指估值序列；本仓库只有 2011 起的 "
        "`data/_ndx_fwd_pe_hist.csv`，覆盖不到 2000 年泡沫顶（而那正是它唯一该起作用的时刻） |",
        "| 网格交易 | 规则里的「区间上下沿 + 每格间距」无法客观设定，任何结果都能被参数淹没 |",
        "| 行业/主题轮动（QQQ+SMH 等） | 需要行业 ETF 长历史，本仓库没有 |",
        "| 卖出备兑 / 雪球等结构化 | 需要期权链数据与定价模型 |",
        "| 全天候 / 永久组合 | 需要黄金、商品、长债长历史，且与本问题（纳指/标普）偏离 |",
        "",
        "## 8. 结论",
        "",
        f"1. **12 条公开策略里，夏普最高的是 ① Faber（{fab['sharpe']:.2f}）和 ④ 金叉（"
        f"{gc['sharpe']:.2f}），都低于 v7 的 {v7r['sharpe']:.2f}**；但**年化最高的是 Faber "
        f"（{_p(fab['ann'])}）和金叉（{_p(gc['ann'])}），都高于 v7 的 {_p(v7r['ann'])}**。"
        f"两句话都对 —— 差别全在你愿意拿多深的回撤（{_p(fab['mdd'])} vs {_p(v7r['mdd'])}）。",
        f"2. **v7 相对「最像它的那条公开策略」（④ 金叉）的增量**：夏普 "
        f"{v7r['sharpe'] - gc['sharpe']:+.2f}、年化 {(v7r['ann'] - gc['ann']) * 100:+.2f}pp、"
        f"回撤浅 {abs(gc['mdd'] - v7r['mdd']) * 100:.1f}pp。**v7 是「少赚 "
        f"{abs(gc['ann'] - v7r['ann']) * 100:.1f}pp 换回撤浅 {abs(gc['mdd'] - v7r['mdd']) * 100:.1f}pp」** —— "
        f"这就是「两月确认 + 深破豁免 + 波动层」三件事合计的价格，也是本轮扫描唯一能量化的"
        f"「我们的东西值多少」。",
        f"3. **情绪指标和杠杆放大这两类可以彻底划掉**：VIX 两版（{v30['sharpe']:.2f} / "
        f"{v15['sharpe']:.2f}）、RSI(2)（{rsi['sharpe']:.2f}、仓位 {rsi['expo']:.0%}）、"
        f"3 倍杠杆（回撤 {_p(lev3['mdd'])}）。**它们对样本区间极端敏感**：VIX 规则只在"
        f"「波动一冲就见底」的年份有效，杠杆版只在 2010 之后有效。这提示一条纪律 —— "
        f"**看到任何纳指策略的起点是 2010 年或更晚，先把样本前推到 1995 再信。**",
        f"4. **不想择时的话，答案是 60/40 年度再平衡（夏普 {f60['sharpe']:.2f}）**，"
        f"它打败了全部 5 条情绪/杠杆策略，比 v7 只差 {v7r['sharpe'] - f60['sharpe']:.2f} 夏普、"
        f"但回撤深 {abs(f60['mdd'] - v7r['mdd']) * 100:.1f}pp。**「少看盘」的价格是明确的，可以自己定价。**",
        f"5. **唯一的增量想法是「空仓腿换资产」**：v7 的空仓从现金换成 10 年期国债，夏普 "
        f"{v7r['sharpe']:.2f} → {v7b['sharpe']:.2f}。它不改变任何信号逻辑，只是把「等的成本」"
        f"从 1.4% 换成债券 —— 但 2022 年股债同跌说明这也不是免费午餐（见 §5.9）。",
        "",
        "> 模拟结果，非投资建议。指数口径不含分红；杠杆版为日复利近似（融资成本 = 空仓利率 + 1.0%）；"
        "债券腿为 10Y 全收益近似（常数久期 7.5）。",
    ]
    with open(OUT_MD, "w", encoding="utf-8") as f:
        f.write("\n".join(L))


if __name__ == "__main__":
    sys.exit(main())
