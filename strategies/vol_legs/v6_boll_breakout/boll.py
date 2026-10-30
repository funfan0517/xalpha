# -*- coding: utf-8 -*-
"""波动资产 · V6 变种：BOLL 上轨突破（六腿逐腿择时）—— 规则与回测引擎。

原 `strategies/tech_indicators/v16_boll_breakout/`（2026-10-30 迁入 vol_legs，池从「54 只场内 ETF」
改为主策略的**六腿场外复权净值**，与 v1_ma20 同构）。

规则（0/1，多头 / 空仓，只做多，不做下轨反做）::

    mid   = MA(close, 35)
    std   = STD(close, 35)          # pandas rolling(n).std()，ddof=1
    upper = mid + K * std           # K = 2.0
    lower = mid - K * std           # 算而不用

- **买入**：`close[t] > upper[t]` 且 `close[t-1] <= upper[t-1]` —— 要求「上穿」，
  避免同一段趋势里重复发信号；
- **卖出**：`close[t] < mid[t]` —— 跌回中轨即退出，不等下轨；
- 窗口未满（NaN）时维持上一状态；**只看收盘，盘中触碰不算**；
- 可选止损（`PARAMS["STOP"]`，默认 None=关闭）：自建仓日收盘价回撤达标且当日收盘仍低于成本则平仓。

与主策略的关系：
- **标的池完全一致** = `rule.LEG_ORDER` 六腿（A500 / 纳指100 / 标普500 / 科创50 / 恒生科技 / 黄金），
  不另立池、不做 A/B 分级；
- **口径一致** = `data.series_for_leg(leg)` 场外复权净值（含分红再投 / 汇率 / 跟踪误差）；
- **执行纪律一致** = T 日盘后算信号、**T+1 净值成交**（`pos = sig.shift(1)`），与 vol_legs §2.4 相同；
- **空仓收益不假设为 0** —— 按货基 `rule.CASH_ANNUAL` 逐日计息，否则「择时」会被现金拖累误伤。

与主策略的区别：主策略管**组合层仓位带**（阶梯 / 趋势闸门 / 再平衡），本变种管**逐腿的进出时点**
（与 `v1_ma20` 同一层，指标换成 BOLL 上轨突破）。三者是可叠加的层，不是替代关系。

口径提醒：`signal()` 只输出**目标仓位**，成交价由回测/实盘层决定；本变种实盘就是「T 日盘后算、
T+1 15:00 前提交拿 T+1 净值」，回测已按该纪律折算（`run(lag=1)`）。模拟结果，非投资建议。
"""
import math
import os
import sys

import numpy as np
import pandas as pd

_HERE = os.path.dirname(os.path.abspath(__file__))
_VL = os.path.dirname(_HERE)                       # strategies/vol_legs
_ROOT = os.path.dirname(os.path.dirname(_VL))      # 仓库根

if _VL not in sys.path:
    sys.path.insert(0, _VL)

import data as vdata    # noqa: E402
import engine           # noqa: E402
import rule             # noqa: E402

# ---------------------------------------------------------------- 1. 参数（唯一权威源）
PARAMS = {"N": 35, "K": 2.0, "STOP": None}
LABEL = "V6 BOLL上轨突破"
VARIANT = "BOLL_BREAK"

CASH_ANNUAL = rule.CASH_ANNUAL
CASH_DAILY = (1.0 + CASH_ANNUAL) ** (1.0 / 252) - 1.0
LEGS = rule.LEG_ORDER
W = {k: float(rule.LEGS[k]["band"]) / float(rule.SLEEVE_PP) for k in LEGS}   # 带权归一化(Σ=1)

# 场外申赎成本（(买入, 卖出) 单边费率）。与 v1_ma20 同一套取法：
# A 类（270042 / 017641 / 000216）申购 0.10% + 赎回 0.50%（持有 >7 天）；
# C 类（023299 / 011609 / 012805）申赎通常为 0，改收 0.2%/年销售服务费 —— 后者**未计入**，
# 故 C 类腿的扣费后数字偏乐观。
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


# ---------------------------------------------------------------- 2. 规则
def bands(px, n=None, k=None):
    """三轨 (mid, upper, lower)，与 px 同 index；NaN 表示窗口未满。"""
    n = PARAMS["N"] if n is None else int(n)
    k = PARAMS["K"] if k is None else float(k)
    s = pd.Series(np.asarray(px, dtype=float), index=px.index)
    mid = s.rolling(n).mean()
    std = s.rolling(n).std()                       # ddof=1
    return mid, mid + k * std, mid - k * std


def signal(px, n=None, k=None, stop=None):
    """BOLL 上轨突破的目标仓位（0/1，逐日因果）+ (mid, upper)。

    「上穿」判定用前一日比较；窗口未满时维持上一状态。stop 为可选止损比例（自建仓日收盘价）。
    """
    n = PARAMS["N"] if n is None else int(n)
    k = PARAMS["K"] if k is None else float(k)
    stop = PARAMS["STOP"] if stop is None else float(stop)
    close = np.asarray(px, dtype=float)
    mid, upper, _lower = bands(px, n, k)
    mid_v, up_v = mid.to_numpy(), upper.to_numpy()

    size = len(close)
    sig = np.zeros(size, dtype=int)
    pos, entry_px = 0, np.nan
    for t in range(size):
        if np.isnan(up_v[t]) or np.isnan(mid_v[t]):
            sig[t] = pos                           # 窗口未满：维持
            continue
        if pos == 0:
            if t >= 1 and close[t] > up_v[t] and close[t - 1] <= up_v[t - 1]:
                pos, entry_px = 1, close[t]        # 上穿上轨才买，避免重复信号
        else:
            hit_mid = close[t] < mid_v[t]          # 跌回中轨
            hit_stop = stop is not None and close[t] <= entry_px * (1.0 - stop)
            if hit_mid or hit_stop:
                pos, entry_px = 0, np.nan
        sig[t] = pos
    return pd.Series(sig, index=px.index, dtype=int), mid, upper


# ---------------------------------------------------------------- 3. 引擎
def run(px, sig, cash=None, fee=None, lag=1):
    """择时净值（起点 = 首个有值日，仓位按 lag 日成交）。

    lag=1（默认）= T 日盘后算信号、T+1 净值成交，与主策略 §2.4 同一纪律；
    lag=2 仅用于「QDII 净值再滞后一天」的敏感性对照。
    fee=(买入费率, 卖出费率) 时按仓位翻转在成交日扣费（0→1 收申购费、1→0 收赎回费）。
    """
    cash = CASH_DAILY if cash is None else cash
    ret = px.pct_change().fillna(0.0)
    pos = sig.shift(lag).fillna(0).astype(float)
    r = pos * ret + (1.0 - pos) * cash
    if fee:
        d = pos.diff().fillna(pos)
        r = r - (d > 0) * float(fee[0]) - (d < 0) * float(fee[1])
    return (1.0 + r).cumprod()


def trades(sig, px, lag=1):
    """按仓位翻转折出单笔（成交日 = 信号后第 lag 日），最后一段未平仓也计入（open=True）。"""
    s = sig.shift(lag).fillna(0).astype(int).to_numpy()
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


def exposure(sig, lag=1):
    """持仓占比（信号 lag 日生效口径）。"""
    pos = sig.shift(lag).fillna(0).astype(float)
    return float(pos.mean()) if len(pos) else 0.0


def common_frame(start_from_warmup=True):
    """六腿净值的共同窗口（各自 BOLL 窗口成形之后再取交集）。"""
    cols = {}
    for k in LEGS:
        px = load(k)
        if start_from_warmup and len(px) > PARAMS["N"]:
            px = px.iloc[PARAMS["N"] - 1:]
        cols[k] = px
    return pd.DataFrame(cols).dropna(how="any").sort_index()


def portfolio(frame, signals=None, weight=None, fee=False, lag=1):
    """组合层净值：Σ w_i·pos_i·ret_i + (1-Σ w_i·pos_i)·货基（fee=True 时扣申赎费）。"""
    weight = weight or W
    ret = frame.pct_change().fillna(0.0)
    if signals is None:
        return (1.0 + ret.mul(pd.Series(weight), axis=1).sum(axis=1)).cumprod()
    pos = pd.DataFrame({k: signals[k].shift(lag).fillna(0).astype(float) for k in frame.columns})
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


def fmt_pct(x, nd=2):
    return "—" if x is None else f"{x * 100:+.{nd}f}%"


def fmt_x(x, nd=2):
    if x is None:
        return "—"
    if math.isinf(x):
        return "∞"
    return f"{x:.{nd}f}"


def _self_check():
    """快速自检：六腿序列可读、三轨成形、信号与仓位对得上。"""
    for k in LEGS:
        px = load(k)
        sig, mid, upper = signal(px)
        assert len(sig) == len(px)
        assert set(sig.unique()) <= {0, 1}
        print(f"{k:6s} {rule.LEGS[k]['name']:8s} {px.index[0].date()} ~ {px.index[-1].date()} "
              f"n={len(px):5d} 中轨末值={mid.iloc[-1]:.4f} 上轨末值={upper.iloc[-1]:.4f} "
              f"仓位={int(sig.iloc[-1])} 曝光={exposure(sig):.0%}")
    print("带权(Σ=1): " + "  ".join(f"{k}={W[k]:.3f}" for k in LEGS))


if __name__ == "__main__":
    _self_check()
