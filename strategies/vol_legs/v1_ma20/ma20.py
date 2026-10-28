# -*- coding: utf-8 -*-
"""波动资产 · V1 变种：MA20 穿越（六腿逐腿择时）—— 规则与回测引擎。

规则（0/1，多头 / 空仓）：
- **买入**：场外复权净值上穿 MA20（今值 > MA20 且 昨值 ≤ 昨日 MA20）；
- **卖出**：场外复权净值下穿 MA20；
- 其余时间维持原仓位；不设止损止盈、不做加减仓、**无 MA60 牛熊过滤**。

与主策略的关系：
- **标的池完全一致** = `rule.LEG_ORDER` 六腿（A500 / 纳指100 / 标普500 / 科创50 / 恒生科技 / 黄金），
  不另立池、不做 A/B 分级；
- **口径一致** = `data.series_for_leg(leg)` 场外复权净值（`rule.SIGNAL_MODE`，含分红再投 / 汇率 / 跟踪误差）；
- **执行纪律一致** = T 日盘后算信号、**T+1 净值成交**（`pos = sig.shift(1)`），与 vol_legs §2.4 相同，无前视；
- **空仓收益不假设为 0** —— 按货基 `rule.CASH_ANNUAL` 逐日计息，否则「择时」会被现金拖累误伤。

与主策略的区别只有一条：主策略管**组合层仓位带**（阶梯 / 趋势闸门 / 再平衡），本变种管**逐腿的进出时点**，
两者是可叠加的两层，不是替代关系。
"""
import math
import os
import sys

import pandas as pd

_HERE = os.path.dirname(os.path.abspath(__file__))
_VL = os.path.dirname(_HERE)                       # strategies/vol_legs
_ROOT = os.path.dirname(os.path.dirname(_VL))      # 仓库根

if _VL not in sys.path:
    sys.path.insert(0, _VL)

import data as vdata    # noqa: E402
import engine           # noqa: E402
import rule             # noqa: E402

MA_WIN = 20
LABEL = "V1 MA20穿越"
CASH_ANNUAL = rule.CASH_ANNUAL
CASH_DAILY = (1.0 + CASH_ANNUAL) ** (1.0 / 252) - 1.0
LEGS = rule.LEG_ORDER
W = {k: float(rule.LEGS[k]["band"]) / float(rule.SLEEVE_PP) for k in LEGS}   # 带权归一化(Σ=1)

# 场外申赎成本（(买入, 卖出) 单边费率）。A 类：申购 0.10% + 赎回 0.50%（持有 >7 天）；
# C 类（023299 / 011609 / 012805）申赎通常为 0，改收 0.2%/年销售服务费 —— 后者**未计入**本表，
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


def signal(px, win=MA_WIN):
    """0/1 目标仓位 + MA20。

    「上穿买入 / 下穿卖出 + 其余维持」在数学上等价于 `px > MA20` 的状态序列（从首个有效
    MA 起）：第一次站上必是上穿、之后穿越才改状态。用状态式实现，免去逐日循环。
    """
    ma = px.rolling(win).mean()
    sig = (px > ma).astype(int)
    sig[ma.isna()] = 0
    return sig, ma


def run(px, sig, cash=None, fee=None):
    """次日成交的 0/1 择时净值（起点 = 第一个 MA 成形日）。

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


def common_frame(start_from_ma=True):
    """六腿净值的共同窗口（各自 MA 成形之后再取交集）。"""
    cols = {}
    for k in LEGS:
        px = load(k)
        if start_from_ma and len(px) > MA_WIN:
            px = px.iloc[MA_WIN - 1:]
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


def fmt_pct(x, nd=2):
    return "—" if x is None else f"{x * 100:+.{nd}f}%"


def fmt_x(x, nd=2):
    if x is None:
        return "—"
    if math.isinf(x):
        return "∞"
    return f"{x:.{nd}f}"


def _self_check():
    """快速自检：六腿序列可读、MA 成形、信号与仓位对得上。"""
    for k in LEGS:
        px = load(k)
        sig, ma = signal(px)
        assert len(sig) == len(px)
        assert set(sig.unique()) <= {0, 1}
        print(f"{k:6s} {rule.LEGS[k]['name']:8s} {px.index[0].date()} ~ {px.index[-1].date()} "
              f"n={len(px):5d} MA20末值={ma.iloc[-1]:.4f} 仓位={int(sig.iloc[-1])} "
              f"曝光={exposure(sig):.0%}")
    print(f"带权(Σ=1): " + "  ".join(f"{k}={W[k]:.3f}" for k in LEGS))


if __name__ == "__main__":
    _self_check()
