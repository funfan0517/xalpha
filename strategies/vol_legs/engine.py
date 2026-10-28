# -*- coding: utf-8 -*-
"""波动资产操作规则 · 组合层逐日复算引擎。

**回测与每日操作共用这一份代码**(同 momentum_rotation 的「决策点复算器」口径):
  - backtest.py 用它跑全历史, 出净值/指标;
  - scan.py 用它跑到最新交易日, 把 `pending`(待次日执行的挂单)当作「今日动作」输出。
两条路径逐行同构, 不存在「回测一套、实盘另一套」的口径漂移。

记账口径:
  - 仓位一律用 **pp of 总资产**(与 `rule.unit_pp(leg)` 同单位), 管辖额度 `rule.SLEEVE_PP` = 40pp
    (A500 5 / 纳指100 10 / 标普500 10 / 科创50 2.5 / 恒生科技 2.5 / 黄金 10); 未投部分留货币基金,
    按 `rule.CASH_ANNUAL` 逐日计息; 红利 20 / 国债 30 / 货币基金 10 不在本策略内。
  - 各腿日历不同(A 股 / 美股节假日不同), 无行情的日子不决策、不成交, 持仓按最近收盘价计值。
  - **信号在收盘后算, 次日收盘成交**(文档 §0「收盘后计算、次日执行」)。实现上: t 日产生的挂单
    在 t+1 日收盘价成交, 因此 t+1 的收益不被 t+1 才使用的信息污染, 无前视。
  - 成本: 申购 0.10%; 赎回按持有天数 7 天内 1.5% / 之后 0.5%(场外 A 类量级, 见 rule)。
  - 季度末(3/6/9/12 月最后一个交易日)执行统一再平衡接口; 与同日信号方向相反时,
    按文档 §6「执行绝对值较小者」处理。
"""
import math
import os
import sys

import pandas as pd

_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

import rule  # noqa: E402


# ---------------------------------------------------------------- 挂单成交
def _delta_of(op, cur):
    if op.get("target_pp") is not None:
        return float(op["target_pp"]) - cur
    return float(op.get("delta_pp") or 0.0)


def _fill(op, pos, meta, cash, date, cost=True):
    """把挂单折算成成交额并落账; 返回 (trade | None, 更新后的现金)。

    `pos` 是 dict(原地改), `cash` 是标量(必须回传 —— 早期版本漏了这一步, 结果买入不扣现金、
    净值凭空上涨; 单元测试见 backtest.py::--self-test)。
    """
    leg = op["leg"]
    cur = pos[leg]
    delta = _delta_of(op, cur)
    if abs(delta) < 1e-9:
        return None, cash
    fee = 0.0
    if delta > 0:                                    # 买入
        if cash <= 1e-9:
            return None, cash
        fee_rate = rule.FEE_BUY if cost else 0.0
        amt = min(delta, cash / (1.0 + fee_rate))    # 现金不足时按可用额度成交
        if amt <= 1e-9:
            return None, cash
        fee = amt * fee_rate
        pos[leg] += amt
        cash -= amt + fee
        meta[leg]["buy_date"] = date
        meta[leg]["buys"] += 1
        kind = "buy"
    else:                                            # 卖出
        amt = min(-delta, cur)
        if amt <= 1e-9:
            return None, cash
        held = (date - meta[leg]["buy_date"]).days if meta[leg]["buy_date"] else 999
        fee_rate = (rule.FEE_SELL_7D if held < rule.MIN_HOLD_FOR_LOW_FEE
                    else rule.FEE_SELL) if cost else 0.0
        fee = amt * fee_rate
        pos[leg] -= amt
        cash += amt - fee
        meta[leg]["sells"] += 1
        kind = "sell"
    return dict(date=date, leg=leg, kind=kind, amount_pp=amt, fee_pp=fee,
                rule=op.get("rule", ""), detail=op.get("detail", ""),
                units=op.get("units")), cash


# ---------------------------------------------------------------- 再平衡
def _quarter_end_mask(cal):
    out = []
    n = len(cal)
    for i in range(n):
        if i == n - 1:
            out.append(True)
            continue
        a, b = cal[i], cal[i + 1]
        out.append((a.year, (a.month - 1) // 3) != (b.year, (b.month - 1) // 3))
    return out


def _rebalance_ops(pos, policies, date, buy_back=True):
    """§6 季度再平衡。目标位 = 战略目标带(policy.band_target())。

    buy_back=False(默认, 见 rule.REBAL_BUY_BACK): 只减持超配, 不强行把空仓的腿拉回带位 ——
    补仓必须由阶梯/趋势闸门授权, 否则 §2 的阶梯与 §5 的「年线下只停加仓」同时失效。
    """
    ops = []
    for leg in pos:
        tgt = policies[leg].band_target() if policies else rule.LEGS[leg]["band"]
        dev = pos[leg] - tgt
        tol = rule.rebalance_pp(leg)
        if abs(dev) <= tol:
            continue
        if dev < 0 and not buy_back:
            continue
        ops.append(dict(leg=leg, kind="sell" if dev > 0 else "buy",
                        rule=f"季度再平衡(偏离 >{tol:g}pp)",
                        detail=f"当前 {pos[leg]:.1f}pp vs 目标带 {tgt:.1f}pp, 偏离 {dev:+.1f}pp",
                        target_pp=tgt, delta_pp=None, units=None))
    return ops


def _resolve_conflicts(ops, pos):
    """同日同腿多个方向相反的挂单 -> 取绝对值较小者(文档 §6)。"""
    by_leg = {}
    for op in ops:
        by_leg.setdefault(op["leg"], []).append(op)
    out = []
    for leg, lst in by_leg.items():
        if len(lst) == 1:
            out.append(lst[0])
            continue
        deltas = [_delta_of(o, pos[leg]) for o in lst]
        signs = {1 if d > 1e-9 else -1 for d in deltas if abs(d) > 1e-9}
        if len(signs) <= 1:
            keep = max(lst, key=lambda o: abs(_delta_of(o, pos[leg])))
            if len(lst) > 1:
                keep = dict(keep)
                keep["detail"] += "(同日多条同向规则, 合并执行)"
        else:
            keep = min(lst, key=lambda o: abs(_delta_of(o, pos[leg])))
            keep = dict(keep)
            keep["detail"] += "(与反方向规则冲突, 取绝对值较小者)"
        out.append(keep)
    return out


# ---------------------------------------------------------------- 主循环
def run(ind_by_leg, static_gates=None, fomc_set=None, *, mode="strategy",
        init=None, cash_rate=None, cost=True, since=None, legs=None):
    """逐日复算。

    参数:
      ind_by_leg   : {leg: rule.add_indicators 的输出}, 各腿日期可不同(指标表自带 index)
      static_gates : data.gates() 的静态部分; None = 全部 null(不设限)
      fomc_set     : 禁新仓日期集合(set of Timestamp)
      mode         : "strategy" 按规则 / "static" 季度回锚 / "hold" 建仓后不动 / "equal" 等权
      init         : 起始持仓 {leg: pp}(默认全空仓)
      since        : 复算起点(默认各腿数据并集的最早一天)
      legs         : 参与复算的腿(默认 rule.LEG_ORDER); 单腿专项回测传 ["a500"]
    返回 dict(nav, records, trades, pending, policies, notes, cal)
    """
    static_gates = static_gates or {}
    fomc_set = fomc_set or set()
    cash_rate = rule.CASH_ANNUAL if cash_rate is None else cash_rate
    legs = list(legs or rule.LEG_ORDER)
    ind_by_leg = {k: v for k, v in ind_by_leg.items() if k in legs}
    sleeve_pp = sum(rule.LEGS[l]["band"] for l in legs)

    cal = pd.DatetimeIndex(sorted(set().union(*[set(ind.index) for ind in ind_by_leg.values()])))
    if since is not None:
        cal = cal[cal >= pd.Timestamp(since)]
    if len(cal) < 2:
        raise ValueError("可用交易日不足 2 天")

    px = {leg: ind["close"] for leg, ind in ind_by_leg.items()}
    rows_by_leg = {leg: {d: rule.row_of(ind, d) for d in ind.index}
                   for leg, ind in ind_by_leg.items()}

    init = init or {}
    policies = ({k: v for k, v in rule.make_policies(init).items() if k in legs}
                if mode == "strategy" else None)
    pos = {leg: 0.0 for leg in legs}
    cash = float(sleeve_pp)
    meta = {leg: dict(buy_date=None, buys=0, sells=0) for leg in legs}

    # 静态/等权/买入持有: 首日按带建仓(有价格才建)
    if mode in ("static", "hold", "equal"):
        for leg in legs:
            if cal[0] not in px[leg].index:
                continue
            tgt = (sleeve_pp / len(legs) if mode == "equal" else rule.LEGS[leg]["band"])
            # 建仓额按「含费后正好用满额度」折算, 否则现金会被手续费顶成负数
            fee_rate = rule.FEE_BUY if cost else 0.0
            amt = tgt / (1.0 + fee_rate)
            fee = amt * fee_rate
            pos[leg] = amt
            cash -= amt + fee
            meta[leg]["buy_date"] = cal[0]
            meta[leg]["buys"] += 1
    elif init:
        for leg, w in init.items():
            if leg not in pos:
                continue
            pos[leg] = float(w)
            cash -= float(w)
            meta[leg]["buy_date"] = cal[0]

    qend = _quarter_end_mask(cal)
    pending, trades, records, notes = [], [], [], []
    nav_hist = []

    for i, d in enumerate(cal):
        # 1) 市值随价格变动 + 现金计息(第一日无前收盘)
        if i > 0:
            for leg in legs:
                s = px[leg]
                if d in s.index:
                    prev = s[s.index < d]
                    if len(prev):
                        r = float(s.loc[d]) / float(prev.iloc[-1]) - 1.0
                        pos[leg] *= (1.0 + r)
            cash *= (1.0 + cash_rate) ** (1.0 / 252.0)

        # 2) 执行昨日挂单(次日收盘成交, 无前视)
        acted = []
        if pending:
            remain = []
            for op in pending:
                if d not in px[op["leg"]].index:
                    remain.append(op)                 # 该腿今日无行情, 顺延
                    continue
                tr, cash = _fill(op, pos, meta, cash, d, cost=cost)
                if tr:
                    trades.append(tr)
                    acted.append(tr)
            pending = remain

        # 3) 决策 -> 明日挂单(未成交的挂单顺延, 同腿不再叠加新单)
        if i < len(cal) - 1:
            new_ops = []
            if mode == "strategy":
                gates = dict(static_gates)
                gates["fomc_blackout"] = d.normalize() in fomc_set
                for leg in legs:
                    row = rows_by_leg[leg].get(d)
                    if row is None:
                        continue
                    new_ops.extend(policies[leg].step(row, gates, d, pos[leg]))
                if rule.QUARTERLY and qend[i]:
                    new_ops.extend(_rebalance_ops(pos, policies, d,
                                                  buy_back=rule.REBAL_BUY_BACK))
                new_ops = _resolve_conflicts(new_ops, pos)
            elif mode in ("static", "equal") and rule.QUARTERLY and qend[i]:
                new_ops = _rebalance_ops(pos, None, d, buy_back=True)
            busy = {o["leg"] for o in pending}
            pending = pending + [o for o in new_ops if o["leg"] not in busy]

        nav = cash + sum(pos.values())
        nav_hist.append((d, nav))
        records.append(dict(date=d, nav=nav, cash=cash, invested=sum(pos.values()),
                            pos=dict(pos), actions=acted))

    for leg, pol in (policies or {}).items():
        for nd in getattr(pol, "notes", []):
            notes.append(dict(date=nd[0], leg=leg, text=nd[1]))

    nav_s = pd.Series([v for _, v in nav_hist], index=[d for d, _ in nav_hist],
                      name="nav").astype("float64")
    return dict(nav=nav_s, records=records, trades=trades, pending=pending,
                policies=policies, notes=sorted(notes, key=lambda x: x["date"]),
                cal=cal, cash=cash, pos=pos, meta=meta)


# ---------------------------------------------------------------- 绩效指标
def metrics(nav, rf=None, periods=252):
    rf = rule.CASH_ANNUAL if rf is None else rf
    nav = nav.dropna()
    if len(nav) < 3:
        return dict(cum=0.0, ann=0.0, vol=0.0, mdd=0.0, sharpe=0.0, days=len(nav))
    rs = nav.pct_change().dropna().tolist()
    n = len(rs)
    growth = float(nav.iloc[-1] / nav.iloc[0])
    ann = growth ** (periods / n) - 1.0
    mu = sum(rs) / n
    sd = math.sqrt(sum((x - mu) ** 2 for x in rs) / n) * math.sqrt(periods)
    peak, mdd = float(nav.iloc[0]), 0.0
    for v in nav:
        peak = max(peak, float(v))
        mdd = min(mdd, float(v) / peak - 1.0)
    return dict(cum=growth - 1.0, ann=ann, vol=sd, mdd=mdd,
                sharpe=(ann - rf) / sd if sd > 1e-9 else 0.0, days=n)
