# -*- coding: utf-8 -*-
"""稳健资产 · 引擎（信号 + 记账 + 绩效），每日扫描与回测**共用同一代码路径**。

三层职责
  1. ``momentum`` / ``gate_state`` / ``down_streak`` —— 指标与状态机
  2. ``evaluate``                                   —— 当日决策：档位、各腿 pp、卫星条件、纪律提示
  3. ``backtest_leg`` / ``metrics``                 —— 债腿历史记账（T 日信号、T+1 起承担收益）与绩效

无前视
  动量用 T 日及以前的累计净值；仓位 ``shift(1)`` 后才承担收益（场外按净值成交，T+1 生效）。
"""
import math
import os
import sys

import numpy as np
import pandas as pd

_HERE = os.path.dirname(os.path.abspath(__file__))
if _HERE not in sys.path:
    sys.path.insert(0, _HERE)


# ---------------------------------------------------------------- 指标
def momentum(nav, win=63):
    """近 ``win`` 个交易日的累计收益（nav[-1] / nav[-1-win] − 1）；样本不足返回 None。"""
    if nav is None or len(nav) <= win:
        return None
    return float(nav.iloc[-1] / nav.iloc[-1 - win] - 1.0)


def gate_state(nav, win=63, deadband=0.0, init=True):
    """动量符号状态机 -> (bool Series, 动量 Series)。

    ``deadband``: 双侧死区。进攻档只在动量跌破 −d 时翻防守，防守档只在升破 +d 时翻进攻；
    中间地带维持原档（不交易）。详见 backtest §一「死区敏感性」——
    零死区在这批真实壳上 10 年翻 53 次，成本把毛 alpha 吃穿。
    """
    mom = nav / nav.shift(win) - 1.0
    state, cur = [], bool(init)
    for x in mom.values:
        if x == x:                                   # 非 NaN
            if cur and x < -deadband:
                cur = False
            elif (not cur) and x > deadband:
                cur = True
        state.append(cur)
    return pd.Series(state, index=nav.index, dtype=bool), mom


def down_streak(nav):
    """末端连续下跌的交易日数（收益 < 0 即计数，遇非负即停）。"""
    if nav is None or len(nav) < 2:
        return 0
    r = nav.pct_change().dropna().values
    n = 0
    for v in reversed(r):
        if v < 0:
            n += 1
        else:
            break
    return n


def window_return(nav, win):
    if nav is None or len(nav) <= win:
        return None
    return float(nav.iloc[-1] / nav.iloc[-1 - win] - 1.0)


# ---------------------------------------------------------------- 当日决策
def evaluate(navs, asof=None, gate_on=None):
    """-> 当日决策 dict（只读入净值 Series，不联网）。

    ``gate_on``: None 用 ``rule.CFG['gate_on']``（默认 **False** = 固定哑铃，不做择时）。
    无论开不开，都会算出「影子档位」（若启用动量开关会处于哪一档）供参考。
    """
    import rule

    cfg = rule.CFG
    on_c, off_c, base_c = cfg["core_on"], cfg["core_off"], cfg["base"]
    for c in (on_c, off_c, base_c):
        if c not in navs:
            raise KeyError(f"缺少核心腿 {c}（{rule.POOL[c]['name']}）的净值，无法出信号")

    gate_on = cfg["gate_on"] if gate_on is None else gate_on
    proxy = navs[on_c]
    state, _ = gate_state(proxy, cfg["mom_win"], cfg["gate_deadband"])
    mom = momentum(proxy, cfg["mom_win"])
    if mom is None:
        raise ValueError(f"动量样本不足（需 >{cfg['mom_win']} 个交易日，当前 {len(proxy)}）")

    # 影子档位：状态机末端值；翻转 = 与前一交易日不同
    shadow = bool(state.iloc[-1])
    flip = bool(state.iloc[-1] != state.iloc[-2]) if len(state) > 1 else False

    eff = bool(gate_on and shadow) if gate_on else True
    if not gate_on:
        core_act = "hold"
    elif flip:
        core_act = "buy" if shadow else "sell"
    else:
        core_act = "hold"

    down_days = down_streak(proxy)
    no_add = down_days >= cfg["no_add_down_days"]
    no_catch = down_days >= cfg["no_catch_down_days"]

    bond_frac = rule.bond_target(eff)
    bond_pp = rule.to_pp(bond_frac, cfg["sleeve_bond_pp"])
    cash_frac = rule.cash_target()
    cash_pp = rule.to_pp(cash_frac, cfg["sleeve_cash_pp"])

    rows = []
    for code in rule.ORDER:
        spec = rule.POOL[code]
        role = spec["role"]
        act, label = "none", "—"
        if role in ("core_on", "core_off", "base"):
            pp = bond_pp.get(code, 0.0)
            if pp > 0:
                act = "buy" if (gate_on and flip and core_act == "buy" and code == on_c) else "hold"
                label = f"{pp:.1f}pp"
            else:
                act, label = "none", "0pp（防守档时不持）"
        elif role == "cash":
            pp = cash_pp.get(code, 0.0)
            act, label = "hold", f"{pp:.1f}pp"
        elif role == "satellite":
            act, label = "none", "0pp（条件未满足/需人工复核）"
        elif role == "excluded":
            act, label = "none", "不计入债腿（计权益）"
        else:  # spare
            act, label = "none", "替补（不动手）"

        r20 = window_return(navs.get(code), 20)
        r250 = window_return(navs.get(code), 250)
        nav_v = float(navs[code].iloc[-1]) if code in navs else None
        rows.append(dict(
            code=code, num=spec["num"], name=spec["name"], short=rule.SHORT.get(code, spec["name"]),
            leg=("bond" if spec["num"] < 700 else "cash"), theme=spec["theme"], role=role,
            role_label=rule.ROLE_LABEL[role], style=spec["style"],
            note=spec["note"], index=spec["index"],
            nav=None if nav_v is None else round(nav_v, 4),
            r20=None if r20 is None else round(r20 * 100, 2),
            r250=None if r250 is None else round(r250 * 100, 2),
            pp=round(bond_pp.get(code, cash_pp.get(code, 0.0)), 2),
            act=act, label=label,
        ))

    sats = _satellites(cfg)
    as_of = asof or str(proxy.index[-1].date())
    return dict(
        as_of=as_of,
        config=dict(gate_on=bool(gate_on), deadband=cfg["gate_deadband"],
                    mom_win=cfg["mom_win"], core_w=cfg["core_w"], base_w=cfg["base_w"]),
        gate=dict(on=bool(gate_on), shadow=shadow, flip=flip,
                  mom=round(mom * 100, 3), win=cfg["mom_win"],
                  deadband=round(cfg["gate_deadband"] * 100, 3),
                  down_days=down_days, action=core_act,
                  text=(f"进攻档（{cfg['core_w']:.0%} 7-10 国开 + {cfg['base_w']:.0%} 3-5 政金）" if eff
                        else "防守档（100% 3-5 政金）")),
        discipline=dict(no_add=bool(no_add), no_catch=bool(no_catch), down_days=down_days,
                        text=_disc_text(down_days, no_add, no_catch, cfg)),
        bond=dict(pp=cfg["sleeve_bond_pp"], frac=bond_frac, items=bond_pp),
        cash=dict(pp=cfg["sleeve_cash_pp"], frac=cash_frac, items=cash_pp),
        satellites=sats,
        rows=rows,
    )


def _satellites(cfg):
    import rule
    import data as _d

    out = []
    for code, sp in rule.SAT.items():
        item = dict(code=code, name=sp["name"], theme=sp["theme"], channel=sp["channel"],
                    max_pp=round(sp["max_frac"] * cfg["sleeve_bond_pp"], 2),
                    cond=sp["cond"], note=sp["note"], auto=sp["auto"])
        if code == "100050":
            st = _d.ust_stats()
            item["ust"] = st
            ok = bool(st.get("ok")) and st["pct"] > 90 and (st.get("chg3m") is not None
                                                           and st["chg3m"] < 0)
            item["ok"] = ok
            item["state"] = ("可加至上限" if ok else
                             f"不满足（分位 {st.get('pct')} / 近3月 {st.get('chg3m')}pp）")
        else:
            item["ok"] = None
            item["state"] = "需人工复核（本仓库缺 30Y−10Y 利差数据源）"
        out.append(item)
    return out


def _disc_text(down_days, no_add, no_catch, cfg):
    if no_catch:
        return (f"7-10 年国开已连跌 **{down_days}** 个交易日（≥{cfg['no_catch_down_days']}）"
                "→ 不接刀：即便动量符号刚转正，今日也不追加债腿（文档 §3-5）")
    if no_add:
        return (f"已连跌 **{down_days}** 个交易日（≥{cfg['no_add_down_days']}）"
                "→ 不新增债腿仓位（文档 §3-5）")
    return (f"连跌 **{down_days}** 个交易日（阈值 {cfg['no_add_down_days']}/"
            f"{cfg['no_catch_down_days']}）→ 无纪律限制")


# ---------------------------------------------------------------- 回测记账
def backtest_leg(navs, on, off, base, win=63, base_w=None, fee_side=0.0010,
                 gate=True, deadband=0.0):
    """债腿（30pp 归一到 100%）回测。

    ``gate=True``  core 段（``1-base_w``）在 ``on``/``off`` 之间按动量符号切换
    ``gate=False`` 静态哑铃：``on``/``off`` 不切换（只用 ``on``）
    ``base_w=None`` 时取 ``rule.CFG["base_w"]``（2026-11-20 起 0.65 = 中性 35/65）。

    -> dict(ret=每日净收益 Series, switches=int, exposure=平均核心段仓位, ...)
    """
    if base_w is None:
        import rule  # noqa: PLC0415 - 避免模块级循环导入
        base_w = float(rule.CFG["base_w"])
    # 用角色名当列名：on/off/base 可能是同一只（如「全防守」档），用代码当列名会撞列
    df = pd.concat({"on": navs[on], "off": navs[off], "base": navs[base]},
                   axis=1, join="inner").dropna()
    if len(df) <= win + 2:
        raise ValueError(f"{on}/{off}/{base} 共同样本不足（{len(df)} 行）")

    r = df.pct_change().dropna()
    if gate:
        state, _ = gate_state(df["on"], win, deadband)
        eff = state.shift(1).fillna(True).astype(bool)      # T 日信号 → T+1 起承担收益
    else:
        eff = pd.Series(True, index=df.index)

    r = r.reindex(eff.index).dropna()
    eff = eff.reindex(r.index).fillna(False)
    core_w = 1.0 - base_w
    leg_ret = np.where(eff.values, r["on"].values, r["off"].values)
    ret = core_w * leg_ret + base_w * r["base"].values

    switch = eff.astype(int).diff().abs().fillna(0.0)
    n_switch = int(switch.sum())
    cost = switch.values * core_w * 2.0 * fee_side                # 换一次 = 卖旧 + 买新
    ret_net = pd.Series(ret - cost, index=r.index)

    return dict(
        ret=ret_net, ret_gross=pd.Series(ret, index=r.index), switches=n_switch,
        exposure=float(core_w * eff.mean() + base_w),
        start=str(r.index[0].date()), end=str(r.index[-1].date()),
        base=base, on=on, off=off, gate=gate, fee_side=fee_side, deadband=deadband,
    )


def buy_hold(navs, code, cols=None):
    """单只买入持有（同窗口）-> 每日收益 Series。"""
    nav = navs[code]
    if cols is not None:
        nav = nav.reindex(cols).dropna()
    return nav.pct_change().dropna()


def blend(rets, weights):
    """多腿加权收益（按给定权重，不调仓）。"""
    df = pd.concat(rets, axis=1, join="inner")
    w = np.array(weights, dtype=float)
    return pd.Series(df.values @ w, index=df.index)


def metrics(ret, rf=0.014):
    """-> 年化 / 波动 / 夏普 / 最大回撤 / 最差单年 / 累计。"""
    if len(ret) < 2:
        return dict(years=0.0, cagr=0.0, vol=0.0, sharpe=0.0, mdd=0.0, worst_year=0.0,
                    total=0.0, n=0)
    nav = (1.0 + ret).cumprod()
    years = (ret.index[-1] - ret.index[0]).days / 365.25
    cagr = float(nav.iloc[-1] ** (1.0 / years) - 1.0) if years > 0 else 0.0
    vol = float(ret.std() * math.sqrt(252))
    sharpe = (cagr - rf) / vol if vol > 1e-9 else 0.0
    mdd = float((nav / nav.cummax() - 1.0).min())
    yearly = ret.groupby(ret.index.year).apply(lambda x: (1.0 + x).prod() - 1.0)
    return dict(years=round(years, 2), cagr=round(cagr * 100, 2), vol=round(vol * 100, 2),
                sharpe=round(sharpe, 2), mdd=round(mdd * 100, 2),
                worst_year=round(float(yearly.min()) * 100, 2) if len(yearly) else 0.0,
                total=round(float(nav.iloc[-1] - 1.0) * 100, 2), n=int(len(ret)))
