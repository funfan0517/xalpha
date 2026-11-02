# -*- coding: utf-8 -*-
"""成长/红利风格轮动 v4 · R 阶梯分档 + 双侧趋势闸门 + 现金仓（无前视）

时序：用 `R[t-1]` 与两侧 `趋势[t-1]` 决策，权重 `W[t]` 从下一交易日生效。

三资产权重 W = [成长, 红利, 现金]，每行和恒为 1：

  1. **R 层**（`mode="band"`，v4 默认）按档位直接给底仓：
     R <0.35 → 5:0、0.35~0.45 → 4:1、0.45~0.55 → 1:1、0.55~0.65 → 1:4、≥0.65 → 0:5。
     `mode="binary"` 复现 v3 的 R 层（≤0.35 满仓成长 / ≥0.65 满仓红利 / 中间带保持状态），
     用于把差异**锁死在 R 层**的对照实验（趋势层、冷却期、费率完全相同）。
  2. **趋势层**按侧削减：创业板指 < MA200 → 成长部分 ×(1-cut_growth)；
     中证红利 < MA200 → 红利部分 ×(1-cut_div)。**削掉的部分进现金**，不改投另一侧。
  3. **冷却期**：距上次切换不足 cool 交易日则挂起，到期后若目标仍成立才执行。

削减用「因子相乘」，天然支持分层（如同时给 (120,0.5) 与 (200,1.0) 两道闸门）。
"""
import numpy as np
import pandas as pd

import rule
import factors


def target_weights(r, g, d, bands=None, mode="band", low=None, high=None,
                   ma_window=None, cut=None, cool=None, init_side=None,
                   use_trend=None, cut_growth=None, cut_div=None):
    """R + 两侧收盘 -> 三资产目标权重 DataFrame(growth, div, cash)，index=r.index。

    mode="band"   : v4 阶梯分档（默认），`bands` 可替换档位表做形状敏感性。
    mode="binary" : v3 的二元 R 层（中间带保持状态），用于对照。
    mode="ratchet": **诊断用，不是本变种的规则** —— 减仓按阶梯走，但只有 R 跌破最左档位线
                    才允许加成长，其余情况保持。用来分离「加仓侧」与「减仓侧」各自的贡献。
    `cut_growth` / `cut_div` 可分别覆盖两侧的削减比例（默认都取 `cut`）。
    """
    bands = rule.RATIO_BANDS if bands is None else bands
    low = rule.RATIO_LOW if low is None else low
    high = rule.RATIO_HIGH if high is None else high
    ma_window = rule.MA_TREND if ma_window is None else ma_window
    cut = rule.TREND_CUT if cut is None else cut
    cut_growth = cut if cut_growth is None else cut_growth
    cut_div = cut if cut_div is None else cut_div
    cool = rule.COOL_BARS if cool is None else cool
    init_side = rule.INIT_SIDE if init_side is None else init_side
    use_trend = rule.TREND_USE if use_trend is None else use_trend

    n = len(r)
    rv = r.values.astype(float)
    gv = g.reindex(r.index).values.astype(float)
    dv = d.reindex(r.index).values.astype(float)
    ma_g = factors.trend_ma(g.reindex(r.index), ma_window).values
    ma_d = factors.trend_ma(d.reindex(r.index), ma_window).values

    def target_from(i, base):
        """底仓 (成长, 红利) + 第 i 行的趋势状态 -> 三资产目标。"""
        e_g, e_d = float(base[0]), float(base[1])
        if use_trend:
            if e_g > 0 and not np.isnan(ma_g[i]) and gv[i] < ma_g[i]:
                e_g *= 1.0 - cut_growth
            if e_d > 0 and not np.isnan(ma_d[i]) and dv[i] < ma_d[i]:
                e_d *= 1.0 - cut_div
        return np.array([e_g, e_d, max(0.0, 1.0 - e_g - e_d)])

    # ---- 首个权重：R[0] 定档（t=0 不产生收益，无前视问题）----
    if mode == "binary":
        b0 = np.array([0.0, 1.0]) if init_side == "div" else np.array([1.0, 0.0])
    else:
        b0 = np.array(factors.band_weights(rv[0], bands) or (0.0, 1.0))
    state = 0 if init_side == "div" else 1        # binary 模式：0=红利侧 1=成长侧
    base = b0.astype(float)

    out = np.zeros((n, 3))
    out[0] = target_from(0, base)
    cur, last = out[0].copy(), -10 ** 9

    for t in range(1, n):
        i = t - 1
        x = rv[i]
        if mode == "binary":
            if not np.isnan(x):
                if x <= low:
                    state = 1
                elif x >= high:
                    state = 0
            base = np.array([1.0, 0.0]) if state == 1 else np.array([0.0, 1.0])
        else:
            bw = factors.band_weights(x, bands)
            if bw is not None:                    # R 缺失（极少数）沿用上一档
                nb = np.array(bw)
                if (mode == "ratchet" and nb[0] > base[0] + 1e-9
                        and x > bands[0][0]):
                    pass                          # 加成长只许发生在 R < 最左档位线
                else:
                    base = nb

        tgt = target_from(i, base)
        if not np.allclose(tgt, cur) and (t - last) >= cool:
            cur, last = tgt, t
        out[t] = cur

    return pd.DataFrame(out, index=r.index, columns=["growth", "div", "cash"])


def band_series(r, bands=None):
    """R 序列 -> 档位序号 Series（-1 = 缺失），用于统计各档停留天数。"""
    bands = rule.RATIO_BANDS if bands is None else bands
    return pd.Series([factors.band_of(x, bands) for x in r.values],
                     index=r.index, dtype=int)


def run(g, d, W, fee=None, cash_yield=None):
    """按三资产权重模拟净值（起点 1.0，与 v0~v3 同口径）。"""
    fee = rule.FEE if fee is None else fee
    cash_yield = rule.CASH_YIELD if cash_yield is None else cash_yield
    idx = W.index
    gv = g.reindex(idx).astype(float).values
    dv = d.reindex(idx).astype(float).values
    Wv = W.values.astype(float)
    rg = np.concatenate([[0.0], gv[1:] / gv[:-1] - 1.0])
    rd = np.concatenate([[0.0], dv[1:] / dv[:-1] - 1.0])
    rc = cash_yield / 252.0

    eq = np.ones(len(idx))
    switches = []
    for t in range(1, len(idx)):
        turn = float(np.abs(Wv[t] - Wv[t - 1]).sum())
        eq[t] = eq[t - 1] * (1.0 + Wv[t, 0] * rg[t] + Wv[t, 1] * rd[t]
                             + Wv[t, 2] * rc - fee * turn)
        if turn > 1e-12:
            switches.append(dict(date=str(idx[t].date()),
                                 from_w=[round(float(x), 4) for x in Wv[t - 1]],
                                 to_w=[round(float(x), 4) for x in Wv[t]],
                                 turn=round(turn, 4), fee=round(float(fee * turn), 6)))
    return dict(eq=pd.Series(eq, index=idx), W=W, switches=switches,
                params=dict(fee=fee, cash_yield=cash_yield))


def describe_switches(switches, r):
    """给切换事件补上决策用 R 值与动作名（按主导方向）。"""
    out = []
    for s in switches:
        dt = pd.Timestamp(s["date"])
        prev = r.index[r.index < dt]
        val = float(r.loc[prev[-1]]) if len(prev) else float("nan")
        dg = s["to_w"][0] - s["from_w"][0]
        dd = s["to_w"][1] - s["from_w"][1]
        if abs(dg) >= abs(dd):
            action = "加仓成长" if dg > 0 else "减仓成长"
        else:
            action = "加仓红利" if dd > 0 else "减仓红利"
        out.append(dict(s, R_prev=None if np.isnan(val) else round(val, 4), action=action))
    return out


def hold_legs(W):
    """按权重签名切持仓段，返回 [(起始位置, 结束位置, 段内权重)]。"""
    sig = [tuple(np.round(x, 3)) for x in W.values]
    legs, start = [], 0
    for t in range(1, len(sig)):
        if sig[t] != sig[t - 1]:
            legs.append((start, t - 1, sig[start]))
            start = t
    legs.append((start, len(sig) - 1, sig[start]))
    return legs


def metrics(eq):
    """净值 -> 年化 / 最大回撤 / 波动 / 夏普 / Calmar / 总收益。"""
    s = eq / eq.iloc[0]
    ret = float(s.iloc[-1] - 1.0)
    y = max((s.index[-1] - s.index[0]).days / 365.0, 1e-9)
    ann = (1 + ret) ** (1 / y) - 1 if ret > -1 else -1.0
    dd = float((s / s.cummax() - 1).min())
    rr = s.pct_change().dropna()
    vol = float(rr.std() * np.sqrt(252))
    shp = float(rr.mean() / rr.std() * np.sqrt(252)) if rr.std() > 0 else 0.0
    return dict(ann=ann, dd=dd, vol=vol, shp=shp, ret=ret, years=y,
                calmar=ann / abs(dd) if dd else float("nan"))
