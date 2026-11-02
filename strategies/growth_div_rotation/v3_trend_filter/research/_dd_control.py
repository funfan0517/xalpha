# -*- coding: utf-8 -*-
"""v3 回撤控制实验：趋势闸门的分层设计（双侧标的 × 多均线 × 现金仓）。

背景（见 _dd_probe.py）：
  v2 最大回撤 -51.85% 发生在 2010-12~2012-12，全程满仓成长（R 一路 ≤0.39，规则只会加仓）；
  第二大 -46.51% 发生在 2015-06~2016-01，当时空仓成长、满仓红利 —— **红利自己也腰斩**。
  → 只在创业板指上加闸门治不了第二个回撤；红利侧也要一条趋势线，破了转现金。

闸门用「乘法阶梯」表达（单条即可，也可叠两条）：
  rules = [(250, 0.5)]          → 破 MA250 减到 50%
  rules = [(250, 1.0)]          → 破 MA250 清仓
  rules = [(120, 0.5), (250, 1.0)] → 破 MA120 减到 50%，再破 MA250 清到 0（分层）

用法：python strategies/growth_div_rotation/v3_trend_filter/research/_dd_control.py
产物：仅打印。
"""
import io
import os
import sys

try:
    sys.stdout.reconfigure(encoding="utf-8")
except (AttributeError, OSError):
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8")

_DIR = os.path.dirname(os.path.abspath(__file__))
_V3 = os.path.dirname(_DIR)
if _V3 not in sys.path:
    sys.path.insert(0, _V3)

import numpy as np  # noqa: E402
import pandas as pd  # noqa: E402

import rule  # noqa: E402
import data as datamod  # noqa: E402
import factors  # noqa: E402
import engine  # noqa: E402


def _mas(px, windows):
    return {w: px.rolling(w).mean().values for w in windows}


def weights3(r, g, d, lo, hi, rules_g, rules_d, cool, cash_yield=0.0, init="div"):
    """三资产目标权重 (n,3) = [成长, 红利, 现金]，R 层定方向、趋势层按阶梯削减。"""
    n = len(r)
    rv, gv, dv = r.values.astype(float), g.values.astype(float), d.values.astype(float)
    wins = {w for w, _ in list(rules_g) + list(rules_d)}
    mg, md = _mas(g, wins), _mas(d, wins)

    def factor(px, v, rules, t):
        f = 1.0
        for w, cut in rules:
            ma = (mg if px == "g" else md)[w][t]
            if not np.isnan(ma) and v < ma:
                f *= (1.0 - cut)
        return f

    state = 0 if init == "div" else 1
    cur = np.array([0.0, 1.0, 0.0])
    last = -10 ** 9
    out = np.zeros((n, 3))
    out[0] = cur

    for t in range(1, n):
        x = rv[t - 1]
        if not np.isnan(x):
            if x <= lo:
                state = 1
            elif x >= hi:
                state = 0
        b_g = 1.0 if state == 1 else 0.0
        b_d, i = 1.0 - b_g, t - 1
        e_g = b_g * factor("g", gv[i], rules_g, i) if b_g > 0 else 0.0
        e_d = b_d * factor("d", dv[i], rules_d, i) if b_d > 0 else 0.0
        target = np.array([e_g, e_d, max(0.0, 1.0 - e_g - e_d)])
        if not np.allclose(target, cur) and (t - last) >= cool:
            cur, last = target, t
        out[t] = cur
    return out


def run3(g, d, W, fee, cash_yield=0.0):
    """三资产净值模拟。"""
    gv, dv = g.values.astype(float), d.values.astype(float)
    rg = np.concatenate([[0.0], gv[1:] / gv[:-1] - 1.0])
    rd = np.concatenate([[0.0], dv[1:] / dv[:-1] - 1.0])
    rc = cash_yield / 252.0
    eq = np.ones(len(g))
    sw = 0
    for t in range(1, len(g)):
        turn = float(np.abs(W[t] - W[t - 1]).sum())
        eq[t] = eq[t - 1] * (1.0 + W[t, 0] * rg[t] + W[t, 1] * rd[t] + W[t, 2] * rc - fee * turn)
        if turn > 1e-12:
            sw += 1
    return pd.Series(eq, index=g.index), sw


def show(tag, eq, sw, W=None):
    m = engine.metrics(eq)
    dd_idx = (eq / eq.cummax() - 1).idxmin()
    cash = float(W[:, 2].mean()) if W is not None else 0.0
    print(f"{tag:<52}{m['ann'] * 100:>8.2f}%{m['dd'] * 100:>8.2f}%{m['shp']:>7.2f}"
          f"{m['calmar']:>8.2f}{sw:>6}{cash:>6.0%}  {dd_idx.date()}")
    return m


def main():
    wide = datamod.load_wide()
    g, d = wide[rule.GROWTH_INDEX], wide[rule.DIV_INDEX]
    r = factors.ratio_series(g, d)
    g, d = g.reindex(r.index), d.reindex(r.index)
    lo, hi = rule.RATIO_LOW, rule.RATIO_HIGH

    hdr = (f"{'方案':<52}{'年化':>9}{'回撤':>9}{'夏普':>7}{'Calmar':>8}{'切换':>6}"
           f"{'现金':>6}  谷底")
    print(hdr)
    print("-" * len(hdr))

    res = engine.run(g, d, engine.target_weights(r, g, d, use_trend=False))
    show("v2 基线（0.35/0.65，无趋势）", res["eq"], len(res["switches"]))
    print("-" * len(hdr))

    plans = [
        ("单侧 成长MA250 减半", [(250, 0.5)], []),
        ("双侧 MA250/MA250 减半", [(250, 0.5)], [(250, 0.5)]),
        ("双侧 MA250/MA250 清仓", [(250, 1.0)], [(250, 1.0)]),
        ("分层 120减半+250清仓 / 250减半", [(120, 0.5), (250, 1.0)], [(250, 0.5)]),
        ("分层 120减半+250清仓 / 两侧同", [(120, 0.5), (250, 1.0)], [(120, 0.5), (250, 1.0)]),
        ("分层 200减半+250清仓 / 250减半", [(200, 0.5), (250, 1.0)], [(250, 0.5)]),
        ("双侧 MA200/MA200 减半", [(200, 0.5)], [(200, 0.5)]),
        ("分层 120减半+250减半 / 250减半", [(120, 0.5), (250, 0.5)], [(250, 0.5)]),
    ]
    for tag, rg_, rd_ in plans:
        W = weights3(r, g, d, lo, hi, rg_, rd_, rule.COOL_BARS)
        eq, sw = run3(g, d, W, rule.FEE)
        show(tag, eq, sw, W)

    print("-" * len(hdr))
    print("冷却期敏感性（分层 120减半+250清仓 / 250减半，现金年化 1.5%）：")
    for cool in (20, 40, 60, 90):
        W = weights3(r, g, d, lo, hi, [(120, 0.5), (250, 1.0)], [(250, 0.5)], cool)
        eq, sw = run3(g, d, W, rule.FEE, cash_yield=0.015)
        show(f"  cool={cool}", eq, sw, W)

    print("\n现金计息敏感性（同上，cool=40）：")
    for cy in (0.0, 0.015, 0.02):
        W = weights3(r, g, d, lo, hi, [(120, 0.5), (250, 1.0)], [(250, 0.5)], 40)
        eq, sw = run3(g, d, W, rule.FEE, cash_yield=cy)
        show(f"  现金年化 {cy:.3f}", eq, sw, W)

    # ---------------- 最终候选：双侧 MA200 减半 + 现金 1.5% ----------------
    print("\n" + "=" * len(hdr))
    print("最终候选：双侧 MA200/MA200 减半（减掉的仓位转现金 1.5%）")
    print("-" * len(hdr))
    for cool in (15, 20, 30, 40):
        W = weights3(r, g, d, lo, hi, [(200, 0.5)], [(200, 0.5)], cool, cash_yield=0.015)
        eq, sw = run3(g, d, W, rule.FEE, cash_yield=0.015)
        show(f"  cool={cool}", eq, sw, W)

    # 持仓段落分析（最短持有、分段）
    W = weights3(r, g, d, lo, hi, [(200, 0.5)], [(200, 0.5)], 20, cash_yield=0.015)
    eq, sw = run3(g, d, W, rule.FEE, cash_yield=0.015)
    # 用状态签名切段
    sig = [tuple(np.round(x, 2)) for x in W]
    legs, start = [], 0
    for t in range(1, len(sig)):
        if sig[t] != sig[t - 1]:
            legs.append((start, t - 1))
            start = t
    legs.append((start, len(sig) - 1))
    bars = [(b - a) for a, b in legs]
    print(f"\n  持仓段数 {len(legs)}，最短 {min(bars)} 交易日，中位 {int(np.median(bars))}，"
          f"最长 {max(bars)}")
    print(f"  不足 7 个交易日的段：{sum(1 for b in bars if b < 7)} 个（场外 C 类 1.5% 赎回费风险）")

    split = pd.Timestamp("2019-01-01")
    for name, sl in [("前半 2010-2018", eq.index < split), ("后半 2019-2026", eq.index >= split)]:
        m2 = engine.metrics(eq[sl])
        print(f"  {name}: 年化 {m2['ann']:+.2%}  回撤 {m2['dd']:.2%}")
    m2 = engine.metrics(eq)
    print(f"  全期: 年化 {m2['ann']:+.2%}  回撤 {m2['dd']:.2%}  夏普 {m2['shp']:.2f}  "
          f"Calmar {m2['calmar']:.2f}")


if __name__ == "__main__":
    main()
