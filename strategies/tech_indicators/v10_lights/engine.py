# -*- coding: utf-8 -*-
r"""执行引擎 —— 亮灯策略的唯一状态机实现。

行为差异全部由下面这几个开关表达:

| 典型用法 | enter / exit | weight | rebal | trail | max_hold |
|---|---|---|---|---|---|
| 事件制 | 独立入/出场条件 | 1.0 满仓 | 1 | 无 | 无 |
| 目标制 | `ok` / `~ok` | 1.0 满仓 | 1 | 无 | 无 |
| 事件制 + 分批仓位 + 风控 | 独立入/出场条件 | 1/3~1 三档 | 1 | 8% | 10 日 |

公共口径（无前视）:
  * 第 t 日**收盘**算出信号 -> 第 t+1 日**开盘**成交;
  * 只有「窗口日」才动作: (t - begin) % rebal == 0; rebal=1 即每日评估;
  * 成本: 单边 fee（买入/卖出各扣一次）; 期末持仓按最新收盘估值;
  * 持仓期间越界（回撤止损 / 持仓到期）同样在次日开盘离场;
  * 卖出当日不回补, 至少等下一个窗口日的信号（三个策略都是这个语义）。
"""
import numpy as np


def run_engine(open_px, close_px, enter, exit_, begin=0, weight=None, rebal=1,
               trail_stop=None, max_hold=None, block_same_day_reentry=True,
               fee=0.0003, high_px=None, take_profit=None):
    """逐标的状态机。

    参数
    ----
    open_px, close_px : array-like，同交易日序列，升序。
    enter  : bool 序列，第 t 日收盘「是否建仓」。
    exit_  : bool 序列，第 t 日收盘「是否清仓」（仅持仓时生效）。
    begin  : 样本窗口起点索引；[0, begin) 为空仓背景期（warm-up, 不交易, 无前视）。
    weight : None=每次满仓; 或 float 序列，第 t 日建仓的目标仓位（0~1）。
             建仓时投入 cash × weight，其余留作现金 —— 支持分档仓位。
    rebal  : 评估节律（交易日）。1=每日评估; N>1=每 N 个交易日一个窗口,
             窗口之间只做持仓期越界检查之外的「不动作」。
    trail_stop : None=不设; 否则为回撤止损比例（持仓期最高收盘价回撤达此值 -> 次日开盘离场）。
    max_hold   : None=不设; 否则为最长持仓交易日数（到期 -> 次日开盘离场）。
    block_same_day_reentry : 卖出当日不再用同一开盘价回补（默认 True）。

    返回
    ----
    (nav, pos_ratio, trades, holding, pos_log)
      nav       : 每交易日净值（空仓=现金, 持仓=现金+市值）, 自首日起, 未归一。
      pos_ratio : 样本窗口 [begin, ) 内持仓时间占比。
      trades    : [{entry_i, exit_i, bars, ret}]，ret 已扣双边 fee；期末未平仓按最新收盘估值。
      holding   : 期末是否持仓。
      pos_log   : 每交易日是否持仓（供「持仓日 vs 空仓日」择时诊断）。
    """
    n = len(open_px)
    enter = np.asarray(enter, dtype=bool)
    exit_ = np.asarray(exit_, dtype=bool)
    w_arr = None if weight is None else np.asarray(weight, dtype=float)
    # fee 可以是标量, 也可以是逐日数组（逐日变化的成本: 佣金 + 与流动性反向的滑点）
    fee_arr = None if np.isscalar(fee) else np.asarray(fee, dtype=float)

    def _fee(i):
        f = float(fee) if fee_arr is None else float(fee_arr[i])
        return min(max(f, 0.0), 0.5)

    r = max(int(rebal), 1)

    cash, shares = 1.0, 0.0
    nav = np.ones(n)
    pos_log = np.zeros(n, dtype=bool)
    trades = []
    entry_i, entry_px, entry_fee, peak = None, None, 0.0, None

    for i in range(n):
        just_sold = False
        # 预设的限价止盈单: 挂单在建仓时下好, 盘中触及即成交于止盈价（无前视）。
        # 用 high 判断"是否触及", 成交价取止盈价本身 —— 保守且符合限价单的撮合方式。
        if (take_profit and shares > 0 and entry_px and high_px is not None
                and high_px[i] >= entry_px * (1 + take_profit)):
            p_tp = entry_px * (1 + take_profit)
            fe = _fee(i)
            cash = cash + shares * p_tp * (1 - fe)
            shares = 0.0
            just_sold = True
            trades.append(dict(
                entry_i=entry_i, exit_i=i, bars=int(i - entry_i),
                ret=p_tp / entry_px * (1 - entry_fee) * (1 - fe) - 1.0))
            entry_i, entry_px, entry_fee, peak = None, None, 0.0, None
        if i >= begin + 1 and (i - 1 - begin) % r == 0:
            prev = i - 1
            if shares > 0:
                # 引擎内越界条件（依赖持仓状态）: 回撤止损 / 持仓到期
                trail = (trail_stop is not None and peak is not None
                         and close_px[prev] < peak * (1 - trail_stop))
                expired = (max_hold is not None and entry_i is not None
                           and (i - entry_i) >= max_hold)
                if exit_[prev] or trail or expired:
                    fe = _fee(i)
                    cash = cash + shares * open_px[i] * (1 - fe)
                    shares = 0.0
                    just_sold = True
                    if entry_px is not None and entry_px > 0:
                        trades.append(dict(
                            entry_i=entry_i, exit_i=i, bars=int(i - entry_i),
                            ret=open_px[i] / entry_px * (1 - entry_fee) * (1 - fe) - 1.0))
                    entry_i, entry_px, entry_fee, peak = None, None, 0.0, None
            if shares == 0 and not (block_same_day_reentry and just_sold) and enter[prev]:
                wgt = 1.0 if w_arr is None else float(w_arr[prev])
                if wgt > 0:
                    fe = _fee(i)
                    invest = cash * wgt
                    shares = invest * (1 - fe) / open_px[i]
                    cash -= invest
                    entry_i, entry_px, entry_fee, peak = i, open_px[i], fe, close_px[i]

        if shares > 0 and peak is not None:
            peak = max(peak, close_px[i])
        pos_log[i] = shares > 0
        nav[i] = cash + shares * close_px[i]

    if entry_px is not None and entry_px > 0:      # 期末仍持仓: 按最新收盘估值
        trades.append(dict(entry_i=entry_i, exit_i=n - 1, bars=int(n - 1 - entry_i),
                           ret=close_px[-1] / entry_px - 1.0))
    return nav, float(pos_log[begin:].mean()), trades, bool(pos_log[-1]), pos_log


def is_window(i, begin, rebal=1):
    """第 i 个交易日是否为调仓窗口（与 run_engine 同一口径）。"""
    return (i - begin) % max(int(rebal), 1) == 0


def timing_edge(close_px, pos_log, begin=0):
    """择时有效性: 持仓日日均收益 − 空仓日日均收益（为正 = 持仓时确实更会涨）。

    该口径不受仓位高低影响, 是判断低暴露策略有无 alpha 的关键指标,
    也是单条件信息量的「状态机版本」。
    """
    c = np.asarray(close_px, dtype=float)
    rr = c[1:] / c[:-1] - 1.0
    pl = np.asarray(pos_log, dtype=bool)[1:][begin:]
    rw = rr[begin:]
    hold = float(np.nanmean(rw[pl])) if pl.any() else 0.0
    flat = float(np.nanmean(rw[~pl])) if (~pl).any() else 0.0
    return hold - flat


def ann_stats(nav, years):
    """净值 -> (总收益, 年化, 最大回撤)。"""
    nav = np.asarray(nav, dtype=float)
    tot = float(nav[-1] / nav[0] - 1.0)
    ann = (1 + tot) ** (1 / years) - 1 if tot > -1 else -1.0
    mdd = float((nav / np.maximum.accumulate(nav) - 1).min())
    return tot, ann, mdd
