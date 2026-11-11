# -*- coding: utf-8 -*-
"""V13 · 九宫格状态机（打分 → 格 → 许可 → 迟滞 → 兜底 → 仓位）。

时序：**T 日收盘判格、T+1 起承担仓位**（文档 §4「收盘判格、次日起生效」）。
所以净值里 `w` 用 `shift(1)` 去吃收益，退出用 `w.diff()` 计费。

四条容易写错的地方（对应文档的强调点）：

1. `S` 用**计数**（0~3）不是 AND —— 链式 AND 的落空率是算术必然（文档 §1）。
2. 「向下×未定 = 0.2」是**故意不对称**的承重件，不要"为了整洁"改成 hold（§7.11）。
3. 下跌格的 5/20 日计数遇「未定」**继续累加**，直到背景票回到 S ≥ 1 才清零（§2 补充 1）。
4. 急跌兜底**跨格覆盖**任何格，且触发时分两笔减仓（T+1 减半、T+2 到目标，§3.2）。
"""
import numpy as np
import pandas as pd

import rule


# ---------------------------------------------------------------- 指标与打分
def indicators(df):
    c = df["close"]
    out = pd.DataFrame({
        "close": c,
        "ma20": c.rolling(rule.MA_SHORT).mean(),
        "ma60": c.rolling(rule.MA_MID).mean(),
        "ma250": c.rolling(rule.MA_LONG).mean(),
        "ma60_ref": c.rolling(rule.MA_MID).mean().shift(rule.SLOPE_N),
    }, index=df.index)
    out["dd20"] = c / c.rolling(rule.DD_WIN).max().shift(1) - 1.0
    out["ready"] = (out["ma250"].notna() & out["ma60_ref"].notna()
                    & out["dd20"].notna() & (np.arange(len(out)) >= rule.WARMUP))
    return out


def score(ind, confirm_days=None):
    """S（0~3）、P（站上/未定/跌破）、背景、格名。

    `confirm_days`（默认 `rule.CONFIRM_DAYS`=2）：同侧**连续 N 日**才算「站上 / 跌破」，
    其余（含穿越首日）为「未定」。N=2 与原硬编码实现等价。
    调大 N 等于把「力度」这一快维度人为做慢，用来回答「迟滞是不是可以换成更慢的力度确认」。
    """
    nd = rule.CONFIRM_DAYS if confirm_days is None else int(confirm_days)
    c, ma20, ma60 = ind["close"], ind["ma20"], ind["ma60"]
    s1 = (ma20 > ma60).astype(int)
    s2 = (ind["ma60"] > ind["ma60_ref"]).astype(int)
    s3 = (c > ind["ma250"]).astype(int)
    S = (s1 + s2 + s3).astype(int)
    bg = pd.Series(np.where(S >= rule.UP_MIN, "向上",
                            np.where(S == rule.NEUTRAL_S, "中性", "向下")), index=ind.index)
    up_side = (c > ma20).astype(int)
    dn_side = (c < ma20).astype(int)
    above = up_side.rolling(nd, min_periods=nd).sum() == nd
    below = dn_side.rolling(nd, min_periods=nd).sum() == nd
    P = pd.Series(np.where(above, "站上", np.where(below, "跌破", "未定")), index=ind.index)
    cell = pd.Series([f"{b}×{p}" for b, p in zip(bg, P)], index=ind.index)
    return pd.DataFrame({"S": S, "bg": bg, "P": P, "cell": cell})


# ---------------------------------------------------------------- 许可解析
def resolve(cell, hold, down_days, callback_add):
    """-> (绝对上限 或 None=保持, 是否 no_add)。"""
    v = rule.CAP[tuple(cell.split("×"))]
    if callback_add and cell == "向上×跌破":
        v = rule.FULL
    if v == "hold":
        return None, False
    if v == "staged":
        if down_days >= rule.STAGE_OBSERVE_DAYS:
            return rule.OBSERVE, False
        if down_days >= rule.STAGE_HALF_DAYS:
            return rule.HALF, False
        return None, False
    if isinstance(v, tuple) and v[0] == "no_add":
        return (hold if v[1] is None else min(hold, v[1])), True
    return float(v), False


# ---------------------------------------------------------------- 状态机
def _dwell(mode, cap, hold, nd):
    """这次变更要不要等待 —— 返回等待天数（0 = 立即，None = 不变）。

    `mode`：`asym` 降立即 / 升需 nd（文档默认）；`sym` 升降都要 nd；
    `reverse` 升立即 / 降需 nd（**只用于诊断"不对称是不是元凶"**，不是可选策略）。
    """
    if cap is None or abs(cap - hold) <= 1e-9:
        return None
    if mode == "sym":
        return nd
    if cap > hold:
        return 0 if mode == "reverse" else nd
    return nd if mode == "reverse" else 0


def state_machine(df, callback_add=None, hold_days=None, floor_on=True,
                  dd_trigger=None, hysteresis="asym", confirm_days=None,
                  bg_hold_days=None):
    """-> dict(w=仓位序列, start=首个可用下标, diag=逐日诊断表)。

    `hold_days` / `floor_on` / `dd_trigger` / `hysteresis` / `confirm_days` 是给
    `research/_sweep.py` 做敏感性用的，默认值全部取自 `rule.py`（不传 = 文档口径）。

    `bg_hold_days`（默认 None = 不启用）：**按变更来源分迟滞**。许可变更的 71%~100% 由
    「力度」这一快维度触发，剩下的 20% 左右由「背景」触发；后者本来就是月级别慢变量、
    不会抖，却要陪快信号一起等 `hold_days` 天，属于白等。给了这个参数后：
    背景与上次生效时不同 → 只等 `bg_hold_days` 天；同一背景内的升降 → 仍等 `hold_days` 天。
    """
    if callback_add is None:
        callback_add = rule.CALLBACK_ADD
    if hold_days is None:
        hold_days = rule.HOLD_DAYS
    if isinstance(hold_days, (pd.Series, np.ndarray, list)):
        _hd = np.asarray(hold_days, dtype=float)

        def _need_days(i):
            return int(_hd[i])
    else:
        _nd0 = int(hold_days)

        def _need_days(i):
            return _nd0

    dd_thr = rule.DD_TRIGGER if dd_trigger is None else float(dd_trigger)
    ind = indicators(df)
    sc = score(ind, confirm_days)
    n = len(ind)
    ready = ind["ready"].to_numpy()
    dd = ind["dd20"].to_numpy(dtype=float)
    cells = sc["cell"].tolist()
    bg = sc["bg"].tolist()
    ps = sc["P"].tolist()

    w = np.zeros(n)
    hold = rule.FULL                      # 初始许可（文档未定义，满带起步、降格立即生效）
    last_bg = None                        # 上次许可生效时的背景（判迟滞作用域用）
    up_val, up_cnt = None, 0
    floor = False
    plans = []                            # [(idx, 许可)] 兜底的两笔减仓
    down_days = 0
    start = None
    diag = []

    for i in range(n):
        while plans and plans[0][0] <= i:
            hold = plans.pop(0)[1]
            up_val, up_cnt = None, 0
            last_bg = bg[i]
        if not ready[i]:
            w[i] = hold
            diag.append(dict(cell="", cap=np.nan, w=hold, floor=floor, down_days=0))
            continue
        if start is None:
            start = i

        # 「下跌」格计数：向下背景里的跌破/未定都累加（未定不重置），
        # 出现「向下×站上」（反弹）视为下跌中断 → 清零；背景回到 S>=1 也清零。
        down_days = down_days + 1 if (bg[i] == "向下" and ps[i] != "站上") else 0

        # ---- 急跌兜底（唯一跨格覆盖，优先级最高）
        if floor_on and dd[i] <= dd_thr:
            if not floor:
                floor = True
                mid = max(rule.OBSERVE, (hold + rule.OBSERVE) / 2.0)
                plans = [(i + 1, mid), (i + 2, rule.FLOOR_CAP)]
            up_val, up_cnt = None, 0          # 兜底期间冻结升格
            w[i] = hold
            diag.append(dict(cell=cells[i], cap=rule.FLOOR_CAP, w=hold, floor=True,
                             down_days=down_days))
            continue
        if floor and (not floor_on or dd[i] > rule.DD_RELEASE):
            floor = False                     # 只结束兜底状态，恢复仍需 15 日升格

        cap, _no_add = resolve(cells[i], hold, down_days, callback_add) if not floor else (None, False)
        if not floor:
            nd_i = _need_days(i)
            if (bg_hold_days is not None and cap is not None and cap > hold
                    and bg[i] != last_bg):
                nd_i = int(bg_hold_days)   # 背景升级：慢变量本不抖，不必陪快信号等满 15 日
            need = _dwell(hysteresis, cap, hold, nd_i)
            if need is None:                           # hold：维持原许可，计时清零
                up_val, up_cnt = None, 0
            elif need <= 0:                            # 立即生效
                hold, up_val, up_cnt = cap, None, 0
                last_bg = bg[i]
            else:                                      # 连续 need 日维持才生效
                if up_val is not None and abs(up_val - cap) < 1e-9:
                    up_cnt += 1
                else:
                    up_val, up_cnt = cap, 1
                if up_cnt >= need:
                    hold, up_val, up_cnt = cap, None, 0
                    last_bg = bg[i]
        w[i] = hold
        diag.append(dict(cell=cells[i], cap=np.nan if cap is None else cap, w=hold,
                         floor=floor, down_days=down_days))

    return dict(w=pd.Series(w, index=ind.index), start=start,
                diag=pd.DataFrame(diag, index=ind.index))


# ---------------------------------------------------------------- 净值与指标
def run(df, w):
    px = df["close"]
    ret = px.pct_change().fillna(0.0)
    w_eff = w.shift(1).fillna(0.0)          # T 日收盘决定，T+1 起承担收益
    dw = w.diff().fillna(w).abs()
    cash_d = (1.0 + rule.CASH_ANNUAL) ** (1.0 / rule.TRADING_DAYS) - 1.0
    port = w_eff * ret + (1.0 - w_eff) * cash_d - dw * rule.FEE_SIDE
    return (1.0 + port).cumprod()


def perf(nav):
    nav = nav.dropna()
    ret = nav.pct_change().dropna()
    n = len(ret)
    ann = float((1.0 + ret).prod() ** (rule.TRADING_DAYS / n) - 1.0) if n else np.nan
    vol = float(ret.std() * np.sqrt(rule.TRADING_DAYS)) if n else np.nan
    mdd = float((nav / nav.cummax() - 1.0).min())
    sharpe = float((ann - rule.CASH_ANNUAL) / vol) if vol and vol > 0 else np.nan
    return dict(ann=ann, vol=vol, mdd=mdd, sharpe=sharpe, days=n,
                cum=float(nav.iloc[-1] / nav.iloc[0] - 1.0))


def actions(w):
    """相邻变化 > 1e-9 的调仓点。"""
    d = w.diff().fillna(w)
    idx = d.index[d.abs() > 1e-9]
    return [(t, float(d.loc[t])) for t in idx]


def backtest_leg(leg, callback_add=None, df=None, **kw):
    import data as vdata
    df = vdata.frame(leg) if df is None else df
    sm = state_machine(df, callback_add=callback_add, **kw)
    st = sm["start"]
    if st is None:                       # 预热不足（A500 563360 2024-10 上市，只有 502 根）
        return dict(leg=leg, name=rule.NAMES[leg], df=df, sm=sm, nav=None, w=None,
                    perf=None, acts=[], expo=np.nan, start=None, end=df.index[-1], bars=0)
    nav = run(df, sm["w"])
    nav_st = nav.loc[nav.index[st]:]
    w_st = sm["w"].loc[sm["w"].index[st]:]
    return dict(leg=leg, name=rule.NAMES[leg], df=df, sm=sm, nav=nav_st, w=w_st,
                perf=perf(nav_st), acts=actions(w_st),
                expo=float(w_st.mean()), start=df.index[st], end=df.index[-1],
                bars=len(nav_st))
