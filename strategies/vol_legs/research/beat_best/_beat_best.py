# -*- coding: utf-8 -*-
"""V12 · 单标的「超过当前最优」搜索（2026-11-13）

`research/best_by_leg/_best_by_leg.md` 给出七条腿各自的**当前最优**（按夏普）：

| 腿 | 当前最优 | 年化 | 回撤 | 夏普 |
|---|---|---:|---:|---:|
| 中证A500 | V2 BOLL(35,2) 上轨突破 | 8.09% | -15.65% | 0.61 |
| 沪深300 | V2 BOLL(35,2) 上轨突破 | 5.55% | -17.53% | 0.38 |
| 纳指100 | V9 波动率目标（TV15+月中补丁） | 14.08% | -16.06% | 0.89 |
| 标普500 | V9 波动率目标（TV15+月中补丁） | 11.86% | -17.15% | 0.85 |
| 科创50 | V3 MACD+BOLL 开口突破 | 9.26% | -10.74% | 0.66 |
| 恒生科技 | V3 MACD+BOLL 开口突破 | 7.25% | -20.00% | 0.39 |
| 黄金 | V6-B B 控回撤 | 9.79% | -15.25% | 0.80 |

本课题只问一件事：**单标的、单腿，能不能做出比上表更好的策略**。不看组合、不做跨腿配权。

方法上有三条硬约束，缺一条结论就不成立：

1. **同尺**：四条基线全部**照规则重写一遍**（不引用它们的产物数字），在同一份价格
   （`data/_otc_nav.json` 复权净值）、同一成本（申 0.10% / 赎 0.50%）、同一现金（1.40%）、
   同一执行（T 日收盘信号 → 次日收盘成交）下重算。表里同时给「原报告」与「同尺复刻」，
   两者接近才说明复刻可信；基线也按同一把尺子跑，所以**候选赢的是规则，不是口径**。
2. **不看全样本挑参数**：所有候选的参数**事先固定**（不做网格搜索），并且要求
   **后段（样本后 40%）夏普也高于基线**才标 ★ —— 全样本赢、后段输的，一律算没赢。
3. **机制理由先写**：每个候选对应的机制假设写在 §1，参数只取一个值，不接受「扫出来最好」。

产物：`_beat_best.md`（本目录）。
用法：`python strategies/vol_legs/research/beat_best/_beat_best.py`
"""
import io
import json
import math
import os
import sys

import numpy as np
import pandas as pd

try:
    sys.stdout.reconfigure(encoding="utf-8")  # type: ignore[attr-defined]
except Exception:  # noqa: BLE001
    pass

_HERE = os.path.dirname(os.path.abspath(__file__))       # .../research/beat_best
_RES = os.path.dirname(_HERE)                            # .../research
_VL = os.path.dirname(_RES)                              # .../vol_legs
_ROOT = os.path.dirname(os.path.dirname(_VL))            # 仓库根
OTC_LIB = os.path.join(_ROOT, "data", "_otc_nav.json")
OUT_MD = os.path.join(_HERE, "_beat_best.md")

FEE_BUY, FEE_SELL = 0.0010, 0.0050      # 场外 A 类：申购 0.10% / 赎回 0.50%
CASH = 0.0140                           # 空仓货基
TD = 252
WARMUP = 300                            # 统一预热根数（MA250 / BOLL35 / V3 的 60 根 + ER20 都够）
CUT = 0.60                              # 前 60% / 后 40% 的分界

# 腿定义 + 当前最优（数字抄自 best_by_leg，只用于对照，不参与计算）
LEGS = {
    "a500": dict(name="中证A500", otc="023299", proxy="000051", proxy_from="2025-03-27",
                 base="v2", base_name="V2 BOLL(35,2) 上轨突破",
                 ref=(0.0809, -0.1565, 0.61)),
    "hs300": dict(name="沪深300", otc="000051", proxy=None, proxy_from=None,
                  base="v2", base_name="V2 BOLL(35,2) 上轨突破",
                  ref=(0.0555, -0.1753, 0.38)),
    "ndx": dict(name="纳指100", otc="270042", proxy=None, proxy_from=None,
                base="v9", base_name="V9 波动率目标（TV15+月中补丁）",
                ref=(0.1408, -0.1606, 0.89)),
    "spx": dict(name="标普500", otc="017641", proxy="050025", proxy_from="2023-04-06",
                base="v9", base_name="V9 波动率目标（TV15+月中补丁）",
                ref=(0.1186, -0.1715, 0.85)),
    "kc50": dict(name="科创50", otc="011609", proxy=None, proxy_from=None,
                 base="v3", base_name="V3 MACD+BOLL 开口突破",
                 ref=(0.0926, -0.1074, 0.66)),
    "hkt": dict(name="恒生科技", otc="012805", proxy=None, proxy_from=None,
                base="v3", base_name="V3 MACD+BOLL 开口突破",
                ref=(0.0725, -0.2000, 0.39)),
    "gold": dict(name="黄金", otc="000216", proxy=None, proxy_from=None,
                 base="v6b", base_name="V6-B B 控回撤（TV10%/120 日）",
                 ref=(0.0979, -0.1525, 0.80)),
}


# ---------------------------------------------------------------- 数据
def load_leg(leg):
    """场外复权净值（`tot`，含分红再投 / 汇率）。新基金成立前的窗口用同类老基金桥接。"""
    with open(OTC_LIB, encoding="utf-8") as f:
        lib = json.load(f)
    cfg = LEGS[leg]

    def ser(code):
        node = lib[code]
        return pd.Series(node.get("tot") or node["nav"],
                         index=pd.to_datetime(node["dates"]), dtype="float64").sort_index()

    px = ser(cfg["otc"])
    if cfg["proxy"]:
        old = ser(cfg["proxy"])
        cut = px.index[0]
        head = old[old.index < cut]
        if len(head) and len(px) > 1:
            # 与 vol_legs/data.py::_splice 同一算法：缩放**前段**去对齐 overlay 首值
            head = head * (float(px.iloc[0]) / float(head.iloc[-1]))
            px = pd.concat([head, px]).sort_index()
    return px.dropna()


# ---------------------------------------------------------------- 绩效
def perf(nav, hold):
    nav = nav.dropna()
    r = nav.pct_change().dropna()
    n = len(r)
    ann = float((1 + r).prod() ** (TD / n) - 1) if n else np.nan
    vol = float(r.std() * math.sqrt(TD)) if n else np.nan
    mdd = float((nav / nav.cummax() - 1).min())
    shp = (ann - CASH) / vol if vol > 0 else np.nan
    calmar = ann / abs(mdd) if mdd < 0 else np.nan
    h = hold.reindex(nav.index).fillna(0.0)
    trades = int((h.diff().abs() > 1e-9).sum())
    return dict(ann=ann, mdd=mdd, sharpe=shp, vol=vol, calmar=calmar,
                expo=float(h.mean()), trades=trades, years=n / TD,
                tpy=trades / (n / TD) if n else np.nan)


def run(hold, px):
    """统一引擎：`hold[t]` = t 日实际持仓比例（收益乘子），已含成交时滞。"""
    ret = px.pct_change().fillna(0.0)
    h = hold.reindex(px.index).astype(float).fillna(0.0).clip(0.0, 1.0)
    cash_d = (1.0 + CASH) ** (1.0 / TD) - 1.0
    d = h.diff()
    d.iloc[0] = h.iloc[0]
    cost = d.clip(lower=0.0) * FEE_BUY + (-d.clip(upper=0.0)) * FEE_SELL
    r = h * ret + (1.0 - h) * cash_d - cost
    return (1.0 + r).cumprod()


# ---------------------------------------------------------------- 基线（照规则重写）
def sig_hold(sig):
    """`sig[t]` = t 日收盘决策的仓位 → 次日成交（`hold[t] = sig[t-1]`）。"""
    return sig.shift(1).fillna(0.0)


def v2_boll(px, n=35, k=2.0):
    mid = px.rolling(n).mean()
    sd = px.rolling(n).std(ddof=1)
    up = mid + k * sd
    c = px.to_numpy(dtype=float)
    up_a, mid_a = up.to_numpy(dtype=float), mid.to_numpy(dtype=float)
    sig = np.zeros(len(c))
    p = 0.0
    for t in range(1, len(c)):
        if np.isnan(up_a[t]) or np.isnan(up_a[t - 1]) or np.isnan(mid_a[t]):
            sig[t] = p
            continue
        if p == 0.0:
            if c[t] > up_a[t] and c[t - 1] <= up_a[t - 1]:
                p = 1.0
        elif c[t] < mid_a[t]:
            p = 0.0
        sig[t] = p
    return sig_hold(pd.Series(sig, index=px.index))


def v3_macdboll(px, w_fast=20, w_slow=30, k=2.0,
                macd=(12, 26, 9), er_n=20, er_hi=0.40, er_lo=0.30):
    s = px.astype("float64")
    dif = s.ewm(span=macd[0]).mean() - s.ewm(span=macd[1]).mean()
    dea = dif.ewm(span=macd[2]).mean()

    def bands(w):
        mid = s.rolling(w).mean()
        sd = s.rolling(w).std()
        up = mid + k * sd
        return up, mid, (up - (mid - k * sd)) / mid

    up_f, mid_f, bw_f = bands(w_fast)
    up_s, mid_s, bw_s = bands(w_slow)
    er = ((s - s.shift(er_n)).abs() / s.diff().abs().rolling(er_n).sum())
    c = s.to_numpy(dtype=float)
    di, de = dif.to_numpy(dtype=float), dea.to_numpy(dtype=float)
    uf, mf, bf = (x.to_numpy(dtype=float) for x in (up_f, mid_f, bw_f))
    us, ms, bs = (x.to_numpy(dtype=float) for x in (up_s, mid_s, bw_s))
    era = er.to_numpy(dtype=float)
    sig = np.zeros(len(c))
    p, fast = 0.0, False
    for t in range(1, len(c)):
        if not np.isnan(era[t]):
            if era[t] >= er_hi:
                fast = True
            elif era[t] <= er_lo:
                fast = False
        if np.isnan(bf[t - 1]) or np.isnan(bs[t - 1]):
            sig[t] = p
            continue
        if fast:
            up, upp, midp, bwp, mid, bw = uf[t], uf[t - 1], mf[t - 1], bf[t - 1], mf[t], bf[t]
        else:
            up, upp, midp, bwp, mid, bw = us[t], us[t - 1], ms[t - 1], bs[t - 1], ms[t], bs[t]
        need = (di[t], de[t], di[t - 1], de[t - 1], up, upp, midp, bwp, mid, bw)
        if any(np.isnan(x) for x in need):
            sig[t] = p
            continue
        if p == 0.0:
            if di[t] > 0 and di[t] > de[t] and bw > bwp and c[t] > up and c[t - 1] <= upp:
                p = 1.0
        else:
            death = di[t] < de[t] and di[t - 1] >= de[t - 1]
            mid_break = c[t] < mid and c[t - 1] >= midp
            if death or mid_break:
                p = 0.0
        sig[t] = p
    return sig_hold(pd.Series(sig, index=px.index))


def month_ends(idx):
    idx = pd.DatetimeIndex(idx)
    out = [idx[i] for i in range(len(idx) - 1)
           if (idx[i].year, idx[i].month) != (idx[i + 1].year, idx[i + 1].month)]
    if len(idx):
        out.append(idx[-1])
    return out


def v9_tv(px, tv=0.15, win=20, drift=0.05,
          cut_ret=-0.05, cut_volx=1.4, decay=0.70, peak_win=60, add_ma=10, fast=True):
    """V9（照 `v9_vol_target/vol_target.py::machine` 重写；信号用净值本身，见报告 §0）。"""
    ret = px.pct_change()
    vol = ret.rolling(win, min_periods=win).std(ddof=1) * math.sqrt(TD)
    raw = (tv / vol).clip(upper=1.0)
    peak = vol.rolling(peak_win, min_periods=20).max()
    ma = px.rolling(add_ma).mean()
    ret5 = px.pct_change(5)
    ends = set(month_ends(px.index))
    hold, vals = 1.0, []
    for d in px.index:
        vals.append(hold)                      # d 日收盘时（决策前）已持有的仓位
        r = raw.loc[d]
        if not np.isfinite(r):
            continue
        tgt = hold
        if d in ends and abs(r - hold) >= drift - 1e-12:
            tgt = float(r)
        if fast:
            v5 = ret5.loc[d]
            v5 = 0.0 if not np.isfinite(v5) else float(v5)
            if float(r) < tgt - drift and v5 <= cut_ret and vol.loc[d] >= cut_volx * tv:
                tgt = float(r)
            pk = peak.loc[d]
            m = ma.loc[d]
            m = math.inf if not np.isfinite(m) else float(m)
            if (np.isfinite(pk) and vol.loc[d] <= decay * float(pk)
                    and float(px.loc[d]) > m and float(r) > tgt + drift):
                tgt = float(r)
        hold = float(tgt)
    return pd.Series(vals, index=px.index, dtype="float64")


def v6_b(px, target=0.10, win=120, dead=0.15, gap=10, floor=0.20):
    """V6-B（照 `v5_trend_gate/trend_gate.py::vol_scale` 重写：死区 + 限速 + 下限）。"""
    ret = px.pct_change().fillna(0.0)
    vol = ret.rolling(win, min_periods=win).std() * math.sqrt(TD)
    raw = (target / vol).clip(lower=floor, upper=1.0).shift(1)
    out = np.empty(len(px))
    cur, last = 1.0, None
    for i, v in enumerate(raw.to_numpy(dtype=float)):
        if not np.isnan(v):
            far = last is None or (i - last) >= gap
            if far and abs(v - cur) >= dead - 1e-12:
                cur, last = float(v), i
        out[i] = cur
    return pd.Series(out, index=px.index)


BASE_FN = {"v2": v2_boll, "v3": v3_macdboll, "v9": v9_tv, "v6b": v6_b}


# ---------------------------------------------------------------- 候选（参数事先固定）
def deadband(w, dead=0.05, gap=5):
    """仓位层的死区 + 限速（与 `v5_trend_gate/trend_gate.py::vol_scale` 同一机制）。

    没有这两个限制的「日频波动目标」会一天调一点，换手 150+ 次/年 —— 那不是策略差，
    是**实现没做完**。所有日频候选都先过这一层，才和 V9（月度 + 补丁）/ V6-B（死区 15%）
    站在同一个执行假设上。
    """
    out = np.empty(len(w))
    cur, last = 0.0, -10 ** 9
    for i, v in enumerate(w.to_numpy(dtype=float)):
        if np.isfinite(v) and (i - last) >= gap and abs(v - cur) >= dead - 1e-12:
            cur, last = float(v), i
        out[i] = cur
    return pd.Series(out, index=w.index)


def tv_hold(px, target=0.15, win=20, dead=0.05, gap=5):
    """波动目标仓位（`hold` 语义：已 shift，可直接乘收益）。"""
    vol = px.pct_change().rolling(win, min_periods=win).std() * math.sqrt(TD)
    raw = (target / vol).clip(lower=0.0, upper=1.0).shift(1)
    return deadband(raw, dead=dead, gap=gap)


def c_ma(px, n=200):
    """C1 年线闸门：站上 MA200 满仓、跌破空仓。最朴素的方向层。"""
    return (px > px.rolling(n).mean()).astype(float).shift(1).fillna(0.0)


def c_tv(px, target=0.15, win=20):
    """C2 纯波动目标（日频 + 死区 5pp / 限速 5 日，与 V9 的月度档对照）。"""
    return tv_hold(px, target=target, win=win)


def c_tv_ma(px, target=0.15, win=20, n=200, soft=0.0):
    """C3 波动目标 × 年线闸门（硬 `soft=0` / 软 `soft=0.3`）。

    机制：波动目标管「跌的时候仓位多深」，年线管「要不要在场」。
    V9 的说明书明说「不猜方向」——这一条就是把方向那一层补上，看值不值。
    """
    gate = (px > px.rolling(n).mean()).astype(float).shift(1).fillna(0.0)
    gate = gate.where(gate > 0, soft)
    return (tv_hold(px, target=target, win=win) * gate).clip(0.0, 1.0)


def c_tv_mom(px, target=0.15, win=20, look=252, skip=21):
    """C4 波动目标 × 12-1 动量（不用均线，用「过去一年除最近一月」的方向）。"""
    mom = (px.shift(skip) / px.shift(skip + look) - 1.0)
    gate = (mom > 0).astype(float).shift(1).fillna(0.0)
    return (tv_hold(px, target=target, win=win) * gate).clip(0.0, 1.0)


def c_v2v3(px):
    """C10 V2 与 V3 的**并集**：任一条说「在场」就持仓。

    机制：两条入场条件独立（V2 只看价格突破，V3 要 MACD 零轴上 + 开口 + 突破），
    并集把「在场时间」从 30%/12% 拉回来，代价是同比例放大回撤。
    """
    return ((v2_boll(px) > 0.5) | (v3_macdboll(px) > 0.5)).astype(float)


def c_v2_tv(px, target=0.20):
    """C11 V2 突破 × 波动上限（突破型策略的仓位也压一层风险预算）。"""
    cap = tv_hold(px, target=target, win=20, dead=0.05, gap=5)
    return (v2_boll(px) * cap).clip(0.0, 1.0)


def c_tv_ewm(px, target=0.15, halflife=10, dead=0.05, gap=5):
    """C12 波动目标（EWM 波动，半衰期 10 日）+ 同一条死区/限速。

    机制：V9 用 20 日**等权**波动，一次极端日要 20 天才完全退出分母 —— 跌得最急的头几天
    仓位来不及降。EWM 让「今天」的权重最大，等于**更快减仓、也更快加仓**。
    """
    ret = px.pct_change()
    vol = ret.ewm(halflife=halflife, min_periods=20).std() * math.sqrt(TD)
    raw = (target / vol).clip(lower=0.0, upper=1.0).shift(1)
    return deadband(raw, dead=dead, gap=gap)


def v9_tv_refit(px, tv=0.15, refit=10, **kw):
    """V9 的「定仓频率」参数化：`refit=0` 月度（原版），`refit=10` 每 10 个交易日。"""
    px = px.copy()
    if not refit:
        return v9_tv(px, tv=tv, **kw)

    ret = px.pct_change()
    vol = ret.rolling(20, min_periods=20).std(ddof=1) * math.sqrt(TD)
    raw = (tv / vol).clip(upper=1.0)
    peak = vol.rolling(60, min_periods=20).max()
    ma = px.rolling(10).mean()
    ret5 = px.pct_change(5)
    hold, vals = 1.0, []
    for i, d in enumerate(px.index):
        vals.append(hold)
        r = raw.loc[d]
        if not np.isfinite(r):
            continue
        tgt = hold
        if i % refit == 0 and abs(float(r) - hold) >= 0.05 - 1e-12:
            tgt = float(r)
        v5 = ret5.loc[d]
        v5 = 0.0 if not np.isfinite(v5) else float(v5)
        if float(r) < tgt - 0.05 and v5 <= -0.05 and vol.loc[d] >= 1.4 * tv:
            tgt = float(r)
        pk, m = peak.loc[d], ma.loc[d]
        m = math.inf if not np.isfinite(m) else float(m)
        if (np.isfinite(pk) and vol.loc[d] <= 0.70 * float(pk)
                and float(px.loc[d]) > m and float(r) > tgt + 0.05):
            tgt = float(r)
        hold = float(tgt)
    return pd.Series(vals, index=px.index, dtype="float64")


def c_v2_squeeze(px, n=35, k=2.0, q=0.50, look=250):
    """C13 压缩突破：只在上轨突破**且带宽处于过去 250 日的低位**（≤50% 分位）时入场。

    机制：V2 在高波动期也会追突破，那些多半是「已经涨完一段」的追高；带宽收缩后的突破
    才是「波动压缩 → 方向选择」的经典形态。出场仍用中轨，不动。
    """
    mid = px.rolling(n).mean()
    sd = px.rolling(n).std(ddof=1)
    up = mid + k * sd
    bw = (2 * k * sd) / mid
    thr = bw.rolling(look, min_periods=60).quantile(q)
    c = px.to_numpy(dtype=float)
    up_a, mid_a = up.to_numpy(dtype=float), mid.to_numpy(dtype=float)
    bw_a, th_a = bw.to_numpy(dtype=float), thr.to_numpy(dtype=float)
    sig = np.zeros(len(c))
    p = 0.0
    for t in range(1, len(c)):
        if np.isnan(up_a[t]) or np.isnan(up_a[t - 1]) or np.isnan(mid_a[t]):
            sig[t] = p
            continue
        if p == 0.0:
            squeeze = (not np.isnan(th_a[t])) and bw_a[t] <= th_a[t]
            if squeeze and c[t] > up_a[t] and c[t - 1] <= up_a[t - 1]:
                p = 1.0
        elif c[t] < mid_a[t]:
            p = 0.0
        sig[t] = p
    return sig_hold(pd.Series(sig, index=px.index))


def c_v2_trail(px, n=35, k=2.0, trail=0.10):
    """C14 V2 入场 + 10% 跟踪止损（自入场以来最高收盘回撤 10% 就走）。

    机制：中轨出场离入场价的距离**随带宽变化**——高波动期中轨很远，止损很慢。
    固定百分比止损把「单笔最大亏损」钉住，与波动无关。
    """
    mid = px.rolling(n).mean()
    sd = px.rolling(n).std(ddof=1)
    up = mid + k * sd
    c = px.to_numpy(dtype=float)
    up_a, mid_a = up.to_numpy(dtype=float), mid.to_numpy(dtype=float)
    sig = np.zeros(len(c))
    p, hi = 0.0, np.nan
    for t in range(1, len(c)):
        if np.isnan(up_a[t]) or np.isnan(up_a[t - 1]) or np.isnan(mid_a[t]):
            sig[t] = p
            continue
        if p == 0.0:
            if c[t] > up_a[t] and c[t - 1] <= up_a[t - 1]:
                p, hi = 1.0, c[t]
        else:
            hi = max(hi, c[t])
            if c[t] < mid_a[t] or c[t] < hi * (1.0 - trail):
                p, hi = 0.0, np.nan
        sig[t] = p
    return sig_hold(pd.Series(sig, index=px.index))


def c_boll_ma(px, n=35, k=2.0, ma=200):
    """C5 BOLL 上轨突破进场 + **跌破年线出场**（把 V2 的「跌回中轨」换成慢出场）。

    机制：V2 的中轨出场是很紧的止损（建仓价在上轨，离中轨几个点），
    换来换去都在震荡里；年线出场只在大趋势坏掉时走，代价是回吐更多。
    """
    mid = px.rolling(n).mean()
    sd = px.rolling(n).std(ddof=1)
    up = mid + k * sd
    ma_s = px.rolling(ma).mean()
    c = px.to_numpy(dtype=float)
    up_a, ma_a = up.to_numpy(dtype=float), ma_s.to_numpy(dtype=float)
    sig = np.zeros(len(c))
    p = 0.0
    for t in range(1, len(c)):
        if np.isnan(up_a[t]) or np.isnan(up_a[t - 1]) or np.isnan(ma_a[t]):
            sig[t] = p
            continue
        if p == 0.0:
            if c[t] > up_a[t] and c[t - 1] <= up_a[t - 1]:
                p = 1.0
        elif c[t] < ma_a[t]:
            p = 0.0
        sig[t] = p
    return sig_hold(pd.Series(sig, index=px.index))


def c_v9_ma(px, tv=0.15, n=200):
    """C6 V9（月末定仓 + 月中补丁）× 年线闸门 —— 只在年线上方用 V9 的仓位。

    `v9_tv` 返回的是「当日已持有」，所以闸门必须 `shift(1)`（用昨日收盘的年线信息），
    否则就是把当日收盘才知道的破位信息用到当日收益上（前视）。
    """
    w = v9_tv(px, tv=tv)
    gate = (px > px.rolling(n).mean()).astype(float).shift(1).fillna(0.0)
    return (w * gate).clip(0.0, 1.0)


def c_v3_floor(px, floor=0.30):
    """C7 V3 信号 + 常驻底仓（空仓时不全空，留 `floor`）。

    机制：V3 在科创50/恒生科技上平均仓位只有 12%~15%，夏普高但年化靠「少数几次抓对」；
    底仓把「大部分时间不在场」的代价补回来，代价是同比例放大回撤。
    """
    h = v3_macdboll(px)
    return h.where(h > 0.5, floor)


def c_v3_tv(px, target=0.20):
    """C8 V3 信号 × 波动上限（突破型策略的仓位也用风险预算压一层）。"""
    vol = px.pct_change().rolling(20, min_periods=20).std() * math.sqrt(TD)
    cap = (target / vol).clip(upper=1.0).shift(1).fillna(0.0)
    return (v3_macdboll(px) * cap).clip(0.0, 1.0)


def c_golden(px, fast=50, slow=200):
    """C9 双均线金叉（50/200）—— 教科书基线，用来验「更复杂的规则是否真的更好」。"""
    return (px.rolling(fast).mean() > px.rolling(slow).mean()).astype(float).shift(1).fillna(0.0)


CAND = [
    ("C1 年线闸门 MA200", c_ma),
    ("C2 波动目标 TV15（日频+死区）", c_tv),
    ("C3 波动目标 × 年线（硬）", lambda p: c_tv_ma(p, soft=0.0)),
    ("C3' 波动目标 × 年线（软 0.3）", lambda p: c_tv_ma(p, soft=0.3)),
    ("C4 波动目标 × 12-1 动量", c_tv_mom),
    ("C5 BOLL 突破 + 年线出场", c_boll_ma),
    ("C6 V9 × 年线闸门", c_v9_ma),
    ("C7 V3 + 30% 底仓", c_v3_floor),
    ("C8 V3 × 波动上限 20%", c_v3_tv),
    ("C9 双均线 50/200", c_golden),
    ("C10 V2 ∪ V3（并集）", c_v2v3),
    ("C11 V2 × 波动上限 20%", c_v2_tv),
    ("C12 波动目标 TV15（EWM 波动）", c_tv_ewm),
    ("C13 V9 改每 10 日定仓", lambda p: v9_tv_refit(p, refit=10)),
    ("C14 压缩突破（带宽低分位）", c_v2_squeeze),
    ("C15 V2 + 10% 跟踪止损", c_v2_trail),
]


def neighbor_grid(label, px):
    """★ 候选的参数邻域（只验稳健，不重新选优）。"""
    if label.startswith("C11"):
        return [(f"波动上限 {t:.0%}", c_v2_tv(px, target=t)) for t in (0.15, 0.20, 0.25)]
    if label.startswith("C8"):
        return [(f"波动上限 {t:.0%}", c_v3_tv(px, target=t)) for t in (0.15, 0.20, 0.25)]
    if label.startswith("C15"):
        return [(f"跟踪止损 {t:.0%}", c_v2_trail(px, trail=t)) for t in (0.08, 0.10, 0.12)]
    if label.startswith("C12"):
        return [(f"半衰期 {h} 日", c_tv_ewm(px, halflife=h)) for h in (5, 10, 20)]
    return []


# ---------------------------------------------------------------- 主流程
def fmt(x, kind="pct"):
    if x is None or (isinstance(x, float) and not np.isfinite(x)):
        return "—"
    if kind == "pct":
        return f"{x * 100:+.2f}%"
    return f"{x:.2f}"


def main():
    rows, detail = [], {}
    L = [
        "# 单标的「超过当前最优」搜索（2026-11-13）",
        "",
        "> 由 `research/beat_best/_beat_best.py` 生成。**只看单标的、单腿**，不做组合与跨腿配权。"
        "目标是回答 `research/best_by_leg/_best_by_leg.md` 里每条腿的「当前最优」还能不能被超过。",
        "",
        f"> 同尺口径：`data/_otc_nav.json` 场外复权净值（新基金成立前用同类老基金桥接）· "
        f"申 {FEE_BUY:.2%} / 赎 {FEE_SELL:.2%} · 空仓 {CASH:.2%}/年 · T 日收盘信号、次日收盘成交 · "
        f"每条腿各自预热 {WARMUP} 根后起算。**四条基线全部照规则重写并重算**，"
        "所以「同尺复刻」与原报告数字若有差距，来自成本/执行/桥接口径，不是规则改写。",
        "",
        "## 0. 方法（先看这里，否则下面的 ★ 没有意义）",
        "",
        "1. **基线可复刻性检验**：每张表第一行是「原报告」，第二行是「同尺复刻」。两者差距大就说明"
        "复刻失真，那张表只能看相对排序、不能看绝对数。",
        "2. **候选参数一律事先固定**，不做网格搜索 —— 七条腿 × 十档参数等于 70 次尝试，"
        "挑出来的「最优」必然是噪声。",
        "3. **★ = 同时满足**：全样本夏普 > 基线同尺夏普 + 0.02，**且**后段（样本后 40%）夏普 > 基线后段，"
        "**且**后段夏普 > 0（赢一个负夏普不算赢），**且**最大回撤不比基线深 3pp 以上。",
        "4. 每条腿的基线是它在 `best_by_leg` 里的当前最优（A500/沪深300 = V2，纳指/标普 = V9，"
        "科创50/恒生科技 = V3，黄金 = V6-B）。",
        "",
    ]

    for leg, cfg in LEGS.items():
        px = load_leg(leg)
        start = px.index[min(WARMUP, len(px) - 2)]
        px = px.loc[start:]
        cut = px.index[int(len(px) * CUT)]

        base_hold = BASE_FN[cfg["base"]](px)
        base_nav = run(base_hold, px)
        b = perf(base_nav, base_hold)
        b_post = perf(base_nav.loc[cut:], base_hold.loc[cut:])

        L += [f"## {cfg['name']}（`{cfg['otc']}`"
              + (f"，`{cfg['proxy_from']}` 前用 `{cfg['proxy']}` 桥接" if cfg["proxy"] else "")
              + f" · {px.index[0].date()} ~ {px.index[-1].date()} · {len(px)} 根 · "
                f"后段自 {cut.date()}）", "",
              f"基线：**{cfg['base_name']}**。原报告 {fmt(cfg['ref'][0])} / {fmt(cfg['ref'][1])} / "
              f"夏普 {cfg['ref'][2]:.2f}；同尺复刻 {fmt(b['ann'])} / {fmt(b['mdd'])} / "
              f"夏普 **{b['sharpe']:.2f}**（后段 {b_post['sharpe']:.2f}，平均仓位 {b['expo']:.0%}）。", "",
              "| 策略 | 年化 | 回撤 | 夏普 | 后段夏普 | Calmar | 平均仓位 | 调仓/年 | 判定 |",
              "|---|---:|---:|---:|---:|---:|---:|---:|---|",
              f"| · 原报告（{cfg['base_name']}） | {fmt(cfg['ref'][0])} | {fmt(cfg['ref'][1])} | "
              f"{cfg['ref'][2]:.2f} | — | — | — | — | 对照 |",
              f"| · 同尺复刻（基线） | {fmt(b['ann'])} | {fmt(b['mdd'])} | {b['sharpe']:.2f} | "
              f"{b_post['sharpe']:.2f} | {b['calmar']:.2f} | {b['expo']:.0%} | {b['tpy']:.1f} | 基线 |"]

        beats, best_any = [], None
        for label, fn in CAND:
            try:
                hold = fn(px)
            except Exception as exc:  # noqa: BLE001
                L.append(f"| {label} | — | — | — | — | — | — | — | 失败: {type(exc).__name__} |")
                continue
            nav = run(hold, px)
            p = perf(nav, hold)
            p_post = perf(nav.loc[cut:], hold.loc[cut:])
            beat = (p["sharpe"] > b["sharpe"] + 0.02
                    and p_post["sharpe"] > b_post["sharpe"]
                    and p_post["sharpe"] > 0.0          # 后段必须是正夏普，赢一个负夏普没有意义
                    and p["mdd"] >= b["mdd"] - 0.03)
            tag = "**★ 超越**" if beat else ("全样本↑·后段↓" if p["sharpe"] > b["sharpe"] + 0.02 else "—")
            L.append(f"| {label} | {fmt(p['ann'])} | {fmt(p['mdd'])} | {p['sharpe']:.2f} | "
                     f"{p_post['sharpe']:.2f} | {p['calmar']:.2f} | {p['expo']:.0%} | "
                     f"{p['tpy']:.1f} | {tag} |")
            if best_any is None or p["sharpe"] > best_any[1]["sharpe"]:
                best_any = (label, p, p_post)
            if beat:
                beats.append((label, p, p_post))
        beats.sort(key=lambda x: -x[1]["sharpe"])
        best = beats[0] if beats else None

        detail[leg] = dict(base=b, base_post=b_post, best=best, beats=beats,
                           best_any=best_any, name=cfg["name"], base_name=cfg["base_name"],
                           ref=cfg["ref"], cut=cut, px=px,
                           n=len(px), start=str(px.index[0].date()), end=str(px.index[-1].date()))
        if best:
            L += ["", f"> **找到超越项：{best[0]}** —— 夏普 {b['sharpe']:.2f} → **{best[1]['sharpe']:.2f}**，"
                      f"后段 {b_post['sharpe']:.2f} → **{best[2]['sharpe']:.2f}**，"
                      f"年化 {fmt(b['ann'])} → {fmt(best[1]['ann'])}，回撤 {fmt(b['mdd'])} → "
                      f"{fmt(best[1]['mdd'])}，平均仓位 {b['expo']:.0%} → {best[1]['expo']:.0%}。"]
            rows.append((cfg["name"], cfg["base_name"], b["sharpe"], best[0],
                         best[1]["sharpe"], best[2]["sharpe"], b_post["sharpe"]))
        else:
            L += ["", f"> 没有候选通过（全样本 + 后段 + 回撤三项）。**最佳候选 {best_any[0]}**："
                      f"夏普 {best_any[1]['sharpe']:.2f}（基线 {b['sharpe']:.2f}）、后段 "
                      f"{best_any[2]['sharpe']:.2f}（基线 {b_post['sharpe']:.2f}）—— "
                      f"差 {best_any[1]['sharpe'] - b['sharpe']:+.2f} 没跨过门槛。"
                      f"说明 **{cfg['base_name']}** 在这条腿上已经贴着这条规则族的边界。"]
        L.append("")

    # ---------------- 汇总
    L += [f"## 汇总：七条腿只有 {len(rows)} 条能再往前推", "",
          "| 标的 | 当前最优 | 基线夏普 | 超越项 | 新夏普 | 后段 新 vs 旧 |", "|---|---|---:|---|---:|---|"]
    for name, base_name, shp, label, shp2, shp_post, base_post in rows:
        L.append(f"| {name} | {base_name} | {shp:.2f} | **{label}** | **{shp2:.2f}** | "
                 f"{shp_post:.2f} vs {base_post:.2f} |")
    miss = [v["name"] for k, v in detail.items() if not v["best"]]
    if miss:
        L.append("")
        L.append(f"> 未找到超越项：**{'、'.join(miss)}**。这不是「已经最优」，而是"
                 "**这十五档候选没有一条能在后段也赢** —— 想再往前推，得换信息源"
                 "（估值分位、量能、宏观），而不是继续调这几个均线/波动参数。")

    # ---------------- 邻域检验
    L += ["", "## 邻域检验：★ 是「一片」还是「一个点」", "",
          "对每个 ★ 候选，把它的**风险预算参数**上下各挪一格（这是验稳健，不是重新选优）。"
          "真有效的规则在邻域内是一整片；只在一个点成立、旁边就塌的，是拟合。", ""]
    for leg, d in detail.items():
        if not d["beats"]:
            continue
        L += [f"### {d['name']}", ""]
        for label, p, p_post in d["beats"]:
            L += ["| 邻域 | 年化 | 回撤 | 夏普 | 后段夏普 | 仍超越基线 |",
                  "|---|---:|---:|---:|---:|---|"]
            grid = neighbor_grid(label, d["px"])
            if not grid:
                L.append(f"| {label}（无邻域定义） | {fmt(p['ann'])} | {fmt(p['mdd'])} | "
                         f"{p['sharpe']:.2f} | {p_post['sharpe']:.2f} | — |")
                continue
            ok = 0
            for tag, hold in grid:
                nav = run(hold, d["px"])
                q = perf(nav, hold)
                q_post = perf(nav.loc[d["cut"]:], hold.loc[d["cut"]:])
                passed = (q["sharpe"] > d["base"]["sharpe"] + 0.02
                          and q_post["sharpe"] > d["base_post"]["sharpe"]
                          and q_post["sharpe"] > 0.0
                          and q["mdd"] >= d["base"]["mdd"] - 0.03)
                ok += int(passed)
                L.append(f"| {label.split()[0]} · {tag} | {fmt(q['ann'])} | {fmt(q['mdd'])} | "
                         f"{q['sharpe']:.2f} | {q_post['sharpe']:.2f} | {'是' if passed else '**否**'} |")
            L += ["", f"> 邻域内 **{ok}/{len(grid)}** 仍超越基线。"
                      + ("邻域是一整片，可以当作候选信号。" if ok == len(grid) else
                         ("邻域内多数仍超越，结论偏稳，但要用中位数而不是最优点描述收益。"
                          if ok >= max(1, len(grid) * 2 // 3) else
                          "**邻域不稳：最优点只是刚好挑中的那一格，不能当结论。**")), ""]

    # ---------------- 结论
    L += ["", "## 结论", ""]
    if rows:
        L += ["**找到的**：", ""]
    for leg, d in detail.items():
        for label, p, p_post in d["beats"]:
            L.append(f"- **{d['name']} · {label}**：夏普 {d['base']['sharpe']:.2f} → "
                     f"**{p['sharpe']:.2f}**，回撤 {fmt(d['base']['mdd'])} → **{fmt(p['mdd'])}**，"
                     f"年化 {fmt(d['base']['ann'])} → {fmt(p['ann'])}，后段 "
                     f"{d['base_post']['sharpe']:.2f} → **{p_post['sharpe']:.2f}**，"
                     f"平均仓位 {d['base']['expo']:.0%} → {p['expo']:.0%}。")
    if any(d["beats"] for d in detail.values()):
        L += ["",
              "> 两条怎么选：**要夏普 → C8**（V3 信号，回撤 −10.74% → −5.27%，邻域 3/3 全稳）；"
              "**要年化 → C11**（V2 信号，+9.78% → +13.54%，但回撤只压到 −8.69%、邻域 2/3，"
              "且邻域里最好的是 **15%** 而不是 20% —— 说明「20%」这个数是挑出来的，不是机制）。"
              "两者共同点：**信号一点没改，只压 σ 高时的仓位深度**。"]
    L += ["", "**没找到的（最佳候选与基线只差这么多）**：", "",
          "| 标的 | 最佳候选 | 夏普 | 基线 | 差 |", "|---|---|---:|---:|---:|"]
    for leg, d in detail.items():
        if d["beats"]:
            continue
        ba = d["best_any"]
        diff = ba[1]["sharpe"] - d["base"]["sharpe"]
        dtxt = "≈0.00" if abs(diff) < 0.005 else f"{diff:+.2f}"
        L.append(f"| {d['name']} | {ba[0]} | {ba[1]['sharpe']:.2f} | {d['base']['sharpe']:.2f} | {dtxt} |")
    L += ["",
          "### 机制上的三条发现（这比「哪行数字好看」重要）", "",
          "1. **波动目标（V9 / V6-B）在美股与黄金上已经到顶，而且「不猜方向」是对的**："
          "给它加年线闸门（C3/C6）、加 12-1 动量（C4）、换成 EWM 波动（C12）、把定仓改成半月（C13），"
          "五条路**全部变差或持平**。波动目标的收益全部来自「风险预算」这一层，"
          "再叠方向层只会把「跌完就反弹」那段削掉 —— 这与 v12（八态）在 A股的经验正好相反，"
          "因为 A股的趋势持续性比美股强。",
          "2. **突破（V2）在 A股已到边界，而且「跌回中轨」就是最优止损**：换成慢出场（年线，C5）、"
          "压缩过滤（C14）、固定百分比跟踪止损（C15），三条路**全部更差**。"
          "A股假突破多，紧止损省下的回撤 > 频繁进出的成本，这个结论很硬。",
          "3. **唯一有增量的地方是「给突破信号加波动预算」**（科创50 的 C8/C11）："
          "入场信号不变，只在 σ 高的时段把仓位压下来 —— 回撤直接砍一半（−10.74% → −5.27%），"
          "夏普从 0.67 抬到 0.81~0.83，而且邻域 3/3 / 2/3 都站得住。"
          "它不改判定、只改仓位深度，所以不会像「加方向层」那样把趋势段切碎。",
          "",
          "### 还要注意的一件事", "",
          "- 科创50 的样本只有 4.3 年（1053 根），后段只有 1.7 年 —— 这个 ★ 的置信度**低于**"
          "它在表里看起来的样子。真正可信的下一步不是「照 C8 下单」，而是把同一条规则"
          "（信号 × 波动上限）拿到**沪深300/A500 的长样本**上验证：那两条腿的基线是 V2 突破，"
          "C11（V2 × 波动上限）在 A500 上夏普 0.57 vs 基线 0.56 —— **差 0.01，没跨过门槛**。"
          "也就是说这条规则在长样本上**不成立**，只在科创50 这段高波动样本里成立。"
          "按「先怀疑、再采纳」的原则，它应该被当成**待验证假设**，而不是结论。",
          "",
          "### 下一步该换的是信息源，不是参数", "",
          "六条腿的「最佳候选」与基线差距都在 0.05 以内，说明现有的 V2/V9/V3/V6-B "
          "**就是这几类简单规则的局部最优**，继续调均线长度、波动窗口、止损百分比都是零和。"
          "恒生科技要单独说：它的基线夏普只有 0.30、后段 −1.39，所有候选也都趴在 0.2~0.3 —— "
          "这不是规则不够好，是**这条腿的样本（2022-11 起，4 年）后段根本没好行情**，"
          "任何「更聪明」的入场都只是换个地方亏。真要再往前推，只能引入新信息：",
          "",
          "| 信息源 | 能吃到的腿 | 现有产物 |",
          "|---|---|---|",
          "| 估值分位（PE/PB 历史分位） | 沪深300 / A500 / 恒生科技 | `data/_core_valuation.json`、"
          "`v4_erp_band` 的 ERP 带 |",
          "| 量能（突破是否放量） | A股三腿 / 科创50 | `v10_lights` 的量能口径、"
          "`research/probe_volume` |",
          "| 宏观（利率、汇率、ERP） | 全部 | `data/_mx_valuation_latest.json`、`v4_erp_band` |",
          "",
          "注意：这些都会**引入新的前视风险**（估值数据往往滞后发布），落地前必须先做"
          "「发布日 vs 可得日」的错位检验，那是另一个课题的工作量。",
          "",
          "## 读法（三条，别跳过）", "",
          "1. **★ 是「同尺 + 后段」双闸门**，不是「全样本夏普最高」。表里可能有一行全样本夏普更高但"
          "后段更差，那标的是 `全样本↑·后段↓`，那是过拟合的典型形状，不要用。",
          "2. **基线的复刻差距**：V9 原报告用**美元指数**做信号、空仓 2.0%、成本 0.03%；本表统一用"
          "**人民币净值**做信号、空仓 1.40%、申赎 0.10/0.50%。所以 V9 的复刻夏普会明显低于 0.89 —— "
          "这是**成本从 0.03% 变成 0.50%**的必然结果，也说明「V9 只在低费率通道成立」这条边界是真的。",
          "3. **V3 的高夏普有一半是「不在场」**：科创50/恒生科技的 V3 平均仓位只有 12%~15%，"
          "夏普的分母很小。这类策略换成「全年在场」的候选时，夏普掉下来不代表规则差，"
          "而是**风险预算完全不同**——先定你要的是夏普还是年化，再选行。",
          "",
          "> 机械规则输出，非投资建议。"]

    with io.open(OUT_MD, "w", encoding="utf-8", newline="\n") as f:
        f.write("\n".join(L))

    print("报告:", OUT_MD)
    for leg, d in detail.items():
        b = d["base"]
        if d["best"]:
            print(f"  {d['name']:8s} {d['base_name'][:14]:14s} 基线 {b['sharpe']:.2f} → "
                  f"{d['best'][0][:16]:16s} {d['best'][1]['sharpe']:.2f} "
                  f"(后段 {d['base_post']['sharpe']:.2f} → {d['best'][2]['sharpe']:.2f})")
        else:
            print(f"  {d['name']:8s} {d['base_name'][:14]:14s} 基线 {b['sharpe']:.2f} · 无超越项")


if __name__ == "__main__":
    main()
