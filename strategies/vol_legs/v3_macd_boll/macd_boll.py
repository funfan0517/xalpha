# -*- coding: utf-8 -*-
"""波动资产 · V3 变种：MACD + 布林通道（自适应窗口）—— 规则与回测引擎。

来源：`strategies/tech_indicators/v3_macd_boll`（技术指标家族的第三个变种）。迁入 vol_legs 后
**只换标的池与口径**，规则本体（MACD 定多空 + 布林开口突破买入 / 死叉或下穿中轨离场 +
ER 自适应窗口）一条没改：

- **池** = `rule.LEG_ORDER` 六腿（A500 / 纳指100 / 标普500 / 科创50 / 恒生科技 / 黄金），
  不另立池、不做 A/B 分级，增删腿只改 `rule.LEGS`；
- **价格** = `data.series_for_leg(leg)` 场外**复权净值**（`rule.SIGNAL_MODE`，含分红再投 /
  汇率 / 跟踪误差），与主策略、v1_ma20 同一列；
- **成交** = T 日盘后算信号、**T+1 净值成交**（`pos = sig.shift(1)`），与 vol_legs §2.4 同一纪律；
- **空仓**按货基 `rule.CASH_ANNUAL`（1.40%）逐日计息，不假设 0 收益（否则择时会被现金拖累误伤）。

与源变种的两处**必然**差异（都是换池后绕不开的）：

1. MACD(12,26,9) / BOLL / ER 改为**本地自算**。源变种读 `data/_indicators_hist.json`——那是
   场内 ETF 的预计算指标库，场外基金代码（`023299` 等）不在其中。EMA 取
   `ewm(span=...)` 默认参数、BOLL 取 `rolling(w).mean()/std()`，与 `_gen_indicators.py`
   同一算式，故同一序列上两者结果逐值一致。
2. 样本从 `WARMUP = 60` 个交易日后起算（MACD 的 EMA 与 ER(20) 需要收敛），源变种由基线引擎
   的「第 61 个交易日」起算，同口径。

与主策略的关系：主策略管**组合层仓位带**（阶梯 / 趋势闸门 / 再平衡），本变种管**逐腿的进出时点**，
两者是可叠加的两层，不是替代关系（同 v1_ma20 的定位，只是信号从 MA20 换成 MACD+BOLL）。

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

LABEL = "V3 MACD+BOLL"

# ---------------------------------------------------------------- 1. 参数（唯一权威源）
MACD_FAST, MACD_SLOW, MACD_SIG = 12, 26, 9      # 与 _gen_indicators.macd_series 一致
W_FAST, W_SLOW, K = 20, 30, 2.0                 # 布林自适应窗口（趋势态 20 / 震荡态 30）
ER_N, ER_HI, ER_LO = 20, 0.40, 0.30             # Kaufman 效率比：≥0.40 趋势态、≤0.30 震荡态
WARMUP = 60                                     # 与 tech_indicators 基线引擎同口径

CASH_ANNUAL = rule.CASH_ANNUAL
CASH_DAILY = (1.0 + CASH_ANNUAL) ** (1.0 / 252) - 1.0
LEGS = rule.LEG_ORDER
W = {k: float(rule.LEGS[k]["band"]) / float(rule.SLEEVE_PP) for k in LEGS}   # 带权归一化(Σ=1)

# 场外申赎成本（(买入, 卖出) 单边费率）。A 类：申购 0.10% + 赎回 0.50%（持有 >7 天）；
# C 类（023299 / 011609 / 012805）申赎通常为 0，改收 0.2%/年销售服务费 —— 后者**未计入**本表，
# 故 C 类腿的扣费后数字偏乐观（与 v1_ma20 同一取法）。
FEE = {
    "a500": (0.0, 0.0),
    "ndx": (0.001, 0.005),
    "spx": (0.001, 0.005),
    "kc50": (0.0, 0.0),
    "hkt": (0.0, 0.0),
    "gold": (0.001, 0.005),
}


def load(leg, mode=None):
    """该腿价格序列（默认场外复权净值）；缺失/刷新失败直接抛错，不静默补位。"""
    return vdata.series_for_leg(leg, mode=mode).dropna()


# ---------------------------------------------------------------- 2. 指标
def macd(px, fast=MACD_FAST, slow=MACD_SLOW, sig=MACD_SIG):
    """-> (DIF, DEA)。与 `_gen_indicators.macd_series` 同一算式（ewm 默认 adjust=True）。"""
    s = px.astype("float64")
    dif = s.ewm(span=fast).mean() - s.ewm(span=slow).mean()
    dea = dif.ewm(span=sig).mean()
    return dif, dea


def bands(px, w, k=K):
    """布林三轨 -> (上轨, 中轨, 相对带宽 (上轨-下轨)/中轨)。"""
    s = px.astype("float64")
    mid = s.rolling(w).mean()
    sd = s.rolling(w).std()
    upper = mid + k * sd
    bw = (upper - (mid - k * sd)) / mid
    return upper, mid, bw


def efficiency_ratio(px, n=ER_N):
    """Kaufman 效率比 ER：|n 日净变动| / n 日路径总变动，衡量趋势性（0~1）。"""
    s = px.astype("float64")
    return ((s - s.shift(n)).abs() / s.diff().abs().rolling(n).sum())


def _arrays(px):
    close = px.to_numpy(dtype=float)
    dif, dea = (x.to_numpy(dtype=float) for x in macd(px))
    up_f, mid_f, bw_f = (x.to_numpy(dtype=float) for x in bands(px, W_FAST))
    up_s, mid_s, bw_s = (x.to_numpy(dtype=float) for x in bands(px, W_SLOW))
    er = efficiency_ratio(px).to_numpy(dtype=float)
    return close, dif, dea, (up_f, mid_f, bw_f), (up_s, mid_s, bw_s), er


def signal(px):
    """0/1 目标仓位（自适应窗口）。

    入场：DIF>0 且 DIF>DEA（零轴上金叉状态）且 带宽放大（开口）且 收盘上穿布林上轨；
    离场：DIF 下穿 DEA（死叉）或 收盘自上轨下穿中轨。
    窗口按 ER(20) 滞回切换：≥0.40 → 趋势态(20)，≤0.30 → 震荡态(30)，中间维持上次状态。
    """
    close, dif, dea, (up_f, mid_f, bw_f), (up_s, mid_s, bw_s), er = _arrays(px)
    n = len(close)
    sig = np.zeros(n, dtype=int)
    p = 0
    fast = False
    for t in range(1, n):
        if not np.isnan(er[t]):
            if er[t] >= ER_HI:
                fast = True
            elif er[t] <= ER_LO:
                fast = False
        if np.isnan(bw_f[t - 1]) or np.isnan(bw_s[t - 1]):
            sig[t] = p
            continue
        if fast:
            up, upp, midp, bwp = up_f[t], up_f[t - 1], mid_f[t - 1], bw_f[t - 1]
            mid = mid_f[t]
            bw = bw_f[t]
        else:
            up, upp, midp, bwp = up_s[t], up_s[t - 1], mid_s[t - 1], bw_s[t - 1]
            mid = mid_s[t]
            bw = bw_s[t]
        need = (dif[t], dea[t], dif[t - 1], dea[t - 1], up, upp, midp, bwp, mid, bw)
        if any(np.isnan(x) for x in need):
            sig[t] = p
            continue
        breakout = close[t] > up and close[t - 1] <= upp      # 上穿上轨
        expand = bw > bwp                                     # 开口放大
        if p == 0:
            if dif[t] > 0 and dif[t] > dea[t] and expand and breakout:
                p = 1
        else:
            death = dif[t] < dea[t] and dif[t - 1] >= dea[t - 1]
            mid_break = close[t] < mid and close[t - 1] >= midp
            if death or mid_break:
                p = 0
        sig[t] = p
    return pd.Series(sig, index=px.index)


def signal_fixed(px, w=W_SLOW):
    """固定布林窗口（对照用，禁用自适应）：窗口恒为 w。"""
    close = px.to_numpy(dtype=float)
    dif, dea = (x.to_numpy(dtype=float) for x in macd(px))
    up, mid, bw = (x.to_numpy(dtype=float) for x in bands(px, w))
    n = len(close)
    sig = np.zeros(n, dtype=int)
    p = 0
    for t in range(1, n):
        need = (dif[t], dea[t], dif[t - 1], dea[t - 1], up[t], up[t - 1], mid[t - 1], bw[t - 1])
        if any(np.isnan(x) for x in need):
            sig[t] = p
            continue
        breakout = close[t] > up[t] and close[t - 1] <= up[t - 1]
        expand = bw[t] > bw[t - 1]
        if p == 0:
            if dif[t] > 0 and dif[t] > dea[t] and expand and breakout:
                p = 1
        else:
            death = dif[t] < dea[t] and dif[t - 1] >= dea[t - 1]
            mid_break = close[t] < mid[t] and close[t - 1] >= mid[t - 1]
            if death or mid_break:
                p = 0
        sig[t] = p
    return pd.Series(sig, index=px.index)


# ---------------------------------------------------------------- 3. 回测引擎
def run(px, sig, cash=None, fee=None):
    """次日成交的 0/1 择时净值（起点 = 传入区间首日）。

    fee=(买入费率, 卖出费率) 时，按仓位翻转在当日扣费（0→1 收申购费、1→0 收赎回费）。
    """
    cash = CASH_DAILY if cash is None else cash
    ret = px.pct_change().fillna(0.0)
    pos = sig.shift(1).fillna(0).astype(float)
    r = pos * ret + (1.0 - pos) * cash
    if fee:
        d = pos.diff().fillna(pos)
        r = r - (d > 0) * float(fee[0]) - (d < 0) * float(fee[1])
    return (1.0 + r).cumprod()


def trades(sig, px):
    """按仓位翻转折出单笔（成交日 = 信号次日），最后一段未平仓也计入（open=True）。"""
    s = sig.shift(1).fillna(0).astype(int).to_numpy()
    idx, val = px.index, px.to_numpy(dtype=float)
    out, entry = [], None
    for i, v in enumerate(s):
        if v == 1 and entry is None:
            entry = i
        elif v == 0 and entry is not None:
            out.append(dict(entry=idx[entry], exit=idx[i], bars=i - entry,
                            ret=val[i] / val[entry] - 1.0, open=False))
            entry = None
    if entry is not None:
        out.append(dict(entry=idx[entry], exit=idx[-1], bars=len(s) - 1 - entry,
                        ret=val[-1] / val[entry] - 1.0, open=True))
    return out


def trade_stats(tr):
    """单笔统计：笔数 / 胜率 / 盈亏比 / 利润因子 / 平均持仓交易日。"""
    if not tr:
        return dict(n=0, win_rate=0.0, avg_win=0.0, avg_loss=0.0, payoff=0.0,
                    pf=0.0, bars=0.0)
    wins = [t["ret"] for t in tr if t["ret"] > 0]
    loss = [t["ret"] for t in tr if t["ret"] <= 0]
    gp, gl = sum(wins), -sum(loss)
    aw = gp / len(wins) if wins else 0.0
    al = gl / len(loss) if loss else 0.0
    return dict(n=len(tr), win_rate=len(wins) / len(tr),
                avg_win=aw, avg_loss=-al if loss else 0.0,
                payoff=(aw / al) if al > 1e-12 else float("inf") if aw else 0.0,
                pf=(gp / gl) if gl > 1e-12 else float("inf") if gp else 0.0,
                bars=sum(t["bars"] for t in tr) / len(tr))


def perf(nav):
    """绩效指标（复用主策略 engine.metrics：无风险利率 = 货基 CASH_ANNUAL）。"""
    return engine.metrics(nav)


def exposure(sig):
    """持仓占比（信号次日生效口径）。"""
    pos = sig.shift(1).fillna(0).astype(float)
    return float(pos.mean()) if len(pos) else 0.0


def leg_slice(leg, warmup=WARMUP, mode=None):
    """-> (px, sig_adaptive, sig_w20, sig_w30)。样本从第 warmup 个交易日之后起算。"""
    px_full = load(leg, mode=mode)
    start = px_full.index[min(warmup, max(len(px_full) - 2, 0))]
    sig_full = signal(px_full)
    s20_full = signal_fixed(px_full, w=W_FAST)
    s30_full = signal_fixed(px_full, w=W_SLOW)
    px = px_full.loc[start:]
    return (px, sig_full.reindex(px.index).fillna(0).astype(int),
            s20_full.reindex(px.index).fillna(0).astype(int),
            s30_full.reindex(px.index).fillna(0).astype(int))


def common_frame(start_from_warmup=True):
    """六腿净值的共同窗口（各自预热之后再取交集）。"""
    cols = {}
    for k in LEGS:
        px = load(k)
        if start_from_warmup and len(px) > WARMUP:
            px = px.iloc[WARMUP:]
        cols[k] = px
    return pd.DataFrame(cols).dropna(how="any").sort_index()


def portfolio(frame, signals=None, weight=None, fee=False):
    """组合层净值：Σ w_i·pos_i·ret_i + (1-Σ w_i·pos_i)·货基（fee=True 时扣申赎费）。"""
    weight = weight or W
    ret = frame.pct_change().fillna(0.0)
    if signals is None:
        return (1.0 + ret.mul(pd.Series(weight), axis=1).sum(axis=1)).cumprod()
    pos = pd.DataFrame({k: signals[k].shift(1).fillna(0).astype(float) for k in frame.columns})
    pos = pos.reindex(frame.index).fillna(0.0)
    inv = pos.mul(pd.Series(weight), axis=1)
    r = (inv * ret).sum(axis=1) + (1.0 - inv.sum(axis=1)) * CASH_DAILY
    if fee:
        d = pos.diff().fillna(pos)
        buy = sum((d[k] > 0) * float(FEE[k][0]) * weight[k] for k in frame.columns)
        sell = sum((d[k] < 0) * float(FEE[k][1]) * weight[k] for k in frame.columns)
        r = r - buy - sell
    return (1.0 + r).cumprod()


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
    """快速自检：六腿序列可读、指标成形、信号与仓位对得上。"""
    for k in LEGS:
        px, sig, s20, s30 = leg_slice(k)
        assert len(sig) == len(px) and set(sig.unique()) <= {0, 1}
        print(f"{k:6s} {rule.LEGS[k]['name']:8s} {px.index[0].date()} ~ {px.index[-1].date()} "
              f"n={len(px):5d} 仓位={int(sig.iloc[-1])} 曝光={exposure(sig):.0%} "
              f"(w20 {exposure(s20):.0%} / w30 {exposure(s30):.0%})")
    print(f"带权(Σ=1): " + "  ".join(f"{k}={W[k]:.3f}" for k in LEGS))


if __name__ == "__main__":
    _self_check()
