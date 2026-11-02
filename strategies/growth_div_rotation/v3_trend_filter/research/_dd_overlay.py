# -*- coding: utf-8 -*-
"""v3 回撤再压缩实验：在 v3 之上还能不能把 -41.57% 继续往下压？

v3 的两个残余回撤来源（见 ../backtest/_v3_report.md §4）：
  1. 2015-06-12 → 2015-09-15，-41.57%：满仓红利起步，中证红利自己跌 45%，
     趋势闸门（MA200）在崩盘初天然滞后 —— 等它破线，跌幅已经吃到了。
  2. 2024-10-08 → 2025-04-07，-29.14%：急涨后回落，闸门在均线附近反复穿越，摩擦累积。

本脚本把四类「叠加手段」放在**同一套权重/费率/冷却**下对比：
  A 分层趋势闸门：一侧挂两道均线（快线减半 + 慢线再减半，因子相乘）
  B 红利侧更严/更快：红利破 MA200 直接清仓，或再叠一道 60/120 快线
  C 回撤制动（equity brake）：组合回撤达阈值 → 总仓位整体打折，修复到某水位释放
  D 波动率目标（vol target）：按 60 日已实现波动缩放总仓位

⚠ 全部为「在同一段样本上调参」的样本内结果，专治「再加一层就更好」的直觉：
  下表里若某手段只是把回撤挪个地方、或靠牺牲年化换来的，都照实列出。

用法：python strategies/growth_div_rotation/v3_trend_filter/research/_dd_overlay.py
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


# ---------------------------------------------------------------- R 层 + 多道闸门
def layer_weights(r, g, d, gates_g=((200, 0.5),), gates_d=((200, 0.5),), cool=None):
    """v3 的 R 层（二元 + 中间带保持状态）叠加**多道**趋势闸门（因子相乘）。

    gates_*: [(window, cut), ...]，破线则该侧 ×(1-cut)；多道同时破线则叠乘。
    """
    cool = rule.COOL_BARS if cool is None else cool
    n = len(r)
    rv = r.values.astype(float)
    gv = g.values.astype(float)
    dv = d.values.astype(float)
    wins = {w for w, _ in list(gates_g) + list(gates_d)}
    ma_g = {w: g.rolling(w).mean().values for w in wins}
    ma_d = {w: d.rolling(w).mean().values for w in wins}

    def fac(mas, v, gates, i):
        f = 1.0
        for w, cut in gates:
            ma = mas[w][i]
            if not np.isnan(ma) and v < ma:
                f *= 1.0 - cut
        return f

    state = 0 if rule.INIT_SIDE == "div" else 1
    cur = np.array([0.0, 1.0, 0.0])
    last = -10 ** 9
    out = np.zeros((n, 3))
    out[0] = cur
    for t in range(1, n):
        i = t - 1
        x = rv[i]
        if not np.isnan(x):
            if x <= rule.RATIO_LOW:
                state = 1
            elif x >= rule.RATIO_HIGH:
                state = 0
        b_g = 1.0 if state == 1 else 0.0
        b_d = 1.0 - b_g
        e_g = b_g * fac(ma_g, gv[i], gates_g, i) if b_g > 0 else 0.0
        e_d = b_d * fac(ma_d, dv[i], gates_d, i) if b_d > 0 else 0.0
        tgt = np.array([e_g, e_d, max(0.0, 1.0 - e_g - e_d)])
        if not np.allclose(tgt, cur) and (t - last) >= cool:
            cur, last = tgt, t
        out[t] = cur
    return pd.DataFrame(out, index=r.index, columns=["growth", "div", "cash"])


# ---------------------------------------------------------------- 组合级叠加
def brake_scale(dd, tiers, release):
    """组合回撤 dd（负数）-> 仓位缩放系数。

    tiers: [(触发深度, 缩放), ...] 例如 [(0.15, 0.6), (0.25, 0.35)]
    回撤修复到 -release 以上则恢复满仓（进退阈值不同 = 滞回，防抖）。
    """
    if dd >= -release:
        return 1.0
    for depth, s in sorted(tiers, key=lambda x: -x[0]):
        if dd <= -depth:
            return s
    return 1.0


def fixed_exposure(W, k):
    """把 v3 的目标权重组整体缩放到 k 倍暴露（其余进现金）—— 用于「只是降暴露」的对照。"""
    X = W.copy()
    X["growth"] *= k
    X["div"] *= k
    X["cash"] = 1.0 - X["growth"] - X["div"]
    return X


def simulate(g, d, W, brake=None, vol_target=None, vol_window=60, deadband=None,
             fee=None, cash_yield=None):
    """按权重净值模拟，可选组合级叠加（回撤制动 / 波动率目标）。

    - 回撤制动：用**已实现**的组合回撤决定 t 日的缩放（只看 t-1 及之前，无前视）
    - 波动率目标：用**不加缩放**的 base 净值算滚动波动，再据 t-1 的波动定 t 日缩放
    - deadband：缩放系数变化不足该幅度则不动（把「每天微调」变成「偶尔调一次」）
    """
    fee = rule.FEE if fee is None else fee
    cy = rule.CASH_YIELD if cash_yield is None else cash_yield
    idx = W.index
    gv = g.values.astype(float)
    dv = d.values.astype(float)
    rg = np.concatenate([[0.0], gv[1:] / gv[:-1] - 1.0])
    rd = np.concatenate([[0.0], dv[1:] / dv[:-1] - 1.0])
    rc = cy / 252.0
    Wv = W.values.astype(float)
    n = len(idx)

    vol = None
    if vol_target:
        base = np.ones(n)
        for t in range(1, n):
            turn = float(np.abs(Wv[t] - Wv[t - 1]).sum())
            base[t] = base[t - 1] * (1.0 + Wv[t, 0] * rg[t] + Wv[t, 1] * rd[t]
                                     + Wv[t, 2] * rc - fee * turn)
        rr = pd.Series(base, index=idx).pct_change()
        vol = (rr.rolling(vol_window).std() * np.sqrt(252)).values

    eq = np.ones(n)
    peak = 1.0
    Wn = np.zeros_like(Wv)
    Wn[0] = Wv[0]
    sw = 0
    s_cur = 1.0
    for t in range(1, n):
        s = 1.0
        if vol_target and not np.isnan(vol[t - 1]) and vol[t - 1] > 0:
            s *= min(1.0, vol_target / vol[t - 1])
        if brake is not None:
            dd = eq[t - 1] / peak - 1.0
            s *= brake_scale(dd, brake[0], brake[1])
        if deadband is not None and abs(s - s_cur) < deadband:
            s = s_cur                  # 不足一个档位就不动，避免每天微调
        s_cur = s
        w = Wv[t].copy()
        w[0] *= s
        w[1] *= s
        w[2] = 1.0 - w[0] - w[1]
        Wn[t] = w
        turn = float(np.abs(Wn[t] - Wn[t - 1]).sum())
        eq[t] = eq[t - 1] * (1.0 + w[0] * rg[t] + w[1] * rd[t] + w[2] * rc - fee * turn)
        if turn > 1e-12:
            sw += 1
        peak = max(peak, eq[t])
    return pd.Series(eq, index=idx), Wn, sw


def seg_dd(eq, a, b):
    e = eq[(eq.index >= pd.Timestamp(a)) & (eq.index <= pd.Timestamp(b))]
    s = e / e.iloc[0]
    return float((s / s.cummax() - 1).min())


def row(tag, eq, Wn, sw):
    m = engine.metrics(eq)
    dd15 = seg_dd(eq, "2015-06-12", "2016-01-28")
    dd24 = seg_dd(eq, "2024-10-08", "2025-04-07")
    exp = float(Wn[:, 0].mean() + Wn[:, 1].mean())
    print(f"{tag:<44}{m['ann'] * 100:>7.2f}%{m['dd'] * 100:>9.2f}%{m['shp']:>7.2f}"
          f"{m['calmar']:>8.2f}{sw:>6}{exp:>7.0%}{dd15 * 100:>10.1f}%{dd24 * 100:>9.1f}%")
    return m


def grid(scope):
    """快线窗口 × 削减比例的邻域检验：判断「加一道快线」是不是在挑参数。"""
    wide = datamod.load_wide()
    g, d = wide[rule.GROWTH_INDEX], wide[rule.DIV_INDEX]
    r = factors.ratio_series(g, d)
    g, d = g.reindex(r.index), d.reindex(r.index)

    print(f"\n=== 快线邻域检验（{scope}）===")
    head = (f"{'快线/削减':<22}{'年化':>8}{'回撤':>9}{'夏普':>7}{'Calmar':>8}{'切换':>6}"
            f"{'2015段':>9}{'2024段':>9}")
    print(head)
    print("-" * len(head))
    res = []
    for w in (40, 50, 60, 75, 90, 100, 120, 150):
        for cut in (0.5, 1.0):
            if scope == "both":
                kw = dict(gates_g=((w, cut), (200, 0.5)), gates_d=((w, cut), (200, 0.5)))
            else:  # 只在红利侧加快线
                kw = dict(gates_g=((200, 0.5),), gates_d=((w, cut), (200, 0.5)))
            W = layer_weights(r, g, d, **kw)
            eq, Wn, sw = simulate(g, d, W)
            m = engine.metrics(eq)
            a = seg_dd(eq, "2015-06-12", "2016-01-28")
            b = seg_dd(eq, "2024-10-08", "2025-04-07")
            print(f"MA{w}/削减{cut:.0%}".ljust(22)
                  + f"{m['ann'] * 100:>7.2f}%{m['dd'] * 100:>8.2f}%{m['shp']:>7.2f}"
                  f"{m['calmar']:>8.2f}{sw:>6}{a * 100:>8.1f}%{b * 100:>9.1f}%")
            res.append((w, cut, m, sw, a, b))
        print("-" * len(head))
    best = max(res, key=lambda x: x[2]["calmar"])
    print(f"本网格最好：MA{best[0]}/削减{best[1]:.0%} · 年化 {best[2]['ann']:.2%} · "
          f"回撤 {best[2]['dd']:.2%} · Calmar {best[2]['calmar']:.2f} · 切换 {best[3]}")


def control():
    """关键对照：波动率目标/回撤制动的效果里，有多少只是「降低暴露」？

    做法：用 v3 的权重按固定倍数 k 缩放（恒定 k 比例现金），把它的暴露调到与
    叠加方案相同，再比 Calmar —— 若叠加方案的 Calmar 赢不过固定暴露，那它就只是降风险。
    """
    wide = datamod.load_wide()
    g, d = wide[rule.GROWTH_INDEX], wide[rule.DIV_INDEX]
    r = factors.ratio_series(g, d)
    g, d = g.reindex(r.index), d.reindex(r.index)
    W = engine.target_weights(r, g, d)

    head = (f"{'方案':<40}{'年化':>8}{'回撤':>9}{'夏普':>7}{'Calmar':>8}{'切换':>6}"
            f"{'均暴露':>7}{'2015段':>9}{'2024段':>9}")
    print(head)
    print("-" * len(head))

    for k in (0.55, 0.61, 0.65, 0.80):
        Wk = fixed_exposure(W, k)
        eq, Wn, sw = simulate(g, d, Wk)
        row(f"对照：恒定 {k:.0%} 暴露", eq, Wn, sw)
    print("-" * len(head))

    for tv in (0.12, 0.15, 0.18, 0.20):
        eq, Wn, sw = simulate(g, d, W, vol_target=tv, deadband=0.15)
        row(f"波动率目标 {tv:.0%}（deadband 0.15）", eq, Wn, sw)
    print("-" * len(head))

    for dv in (40, 60, 120):
        eq, Wn, sw = simulate(g, d, W, vol_target=0.15, vol_window=dv, deadband=0.15)
        row(f"波动率目标 15%（{dv} 日窗）", eq, Wn, sw)
    print("-" * len(head))

    for depth in (0.10, 0.12, 0.15, 0.18, 0.20):
        eq, Wn, sw = simulate(g, d, W, brake=([(depth, 0.5)], depth * 0.4))
        row(f"制动：回撤破 {depth:.0%} → 半仓（释放 {depth * 40:.0f}%）", eq, Wn, sw)
    print("-" * len(head))

    eq, Wn, sw = simulate(g, d, W, vol_target=0.15, deadband=0.15,
                          brake=([(0.20, 0.5)], 0.08))
    row("组合：波动率 15% + 制动 20%→半仓", eq, Wn, sw)


def grid_vol():
    """波动率目标的「目标 × 窗口」二维邻域 + 前半/后半分段，判断是否稳健。"""
    wide = datamod.load_wide()
    g, d = wide[rule.GROWTH_INDEX], wide[rule.DIV_INDEX]
    r = factors.ratio_series(g, d)
    g, d = g.reindex(r.index), d.reindex(r.index)
    W = engine.target_weights(r, g, d)
    split = pd.Timestamp("2019-01-01")

    head = (f"{'目标/窗口':<20}{'年化':>8}{'回撤':>9}{'夏普':>7}{'Calmar':>8}{'切换':>6}"
            f"{'均暴露':>7}{'前半Calmar':>11}{'后半Calmar':>11}{'2015段':>9}{'2024段':>9}")
    print("\n=== 波动率目标：目标 × 窗口 邻域 ===")
    print(head)
    print("-" * len(head))
    for tv in (0.10, 0.12, 0.15, 0.18, 0.20, 0.25):
        for win in (60, 120, 250):
            eq, Wn, sw = simulate(g, d, W, vol_target=tv, vol_window=win, deadband=0.15)
            m = engine.metrics(eq)
            a = engine.metrics(eq[eq.index < split])
            b = engine.metrics(eq[eq.index >= split])
            exp = float(Wn[:, 0].mean() + Wn[:, 1].mean())
            print(f"目标{tv:.0%}/{win}日".ljust(20)
                  + f"{m['ann'] * 100:>7.2f}%{m['dd'] * 100:>8.2f}%{m['shp']:>7.2f}"
                  f"{m['calmar']:>8.2f}{sw:>6}{exp:>7.0%}"
                  f"{a['calmar']:>11.2f}{b['calmar']:>11.2f}"
                  f"{seg_dd(eq, '2015-06-12', '2016-01-28') * 100:>8.1f}%"
                  f"{seg_dd(eq, '2024-10-08', '2025-04-07') * 100:>9.1f}%")
        print("-" * len(head))


def verify():
    """自检：不加叠加时 simulate 必须与 engine.run 完全一致。"""
    wide = datamod.load_wide()
    g, d = wide[rule.GROWTH_INDEX], wide[rule.DIV_INDEX]
    r = factors.ratio_series(g, d)
    g, d = g.reindex(r.index), d.reindex(r.index)
    W = engine.target_weights(r, g, d)
    e1 = engine.run(g, d, W)["eq"]
    e2, _, _ = simulate(g, d, W)
    print(f"自检 simulate vs engine.run：最大净值偏差 {float((e1 - e2).abs().max()):.3e}")


def main():
    verify()
    wide = datamod.load_wide()
    g, d = wide[rule.GROWTH_INDEX], wide[rule.DIV_INDEX]
    r = factors.ratio_series(g, d)
    g, d = g.reindex(r.index), d.reindex(r.index)

    hdr = (f"{'方案':<44}{'年化':>8}{'回撤':>10}{'夏普':>7}{'Calmar':>8}{'切换':>6}"
           f"{'均暴露':>7}{'2015段':>11}{'2024段':>10}")
    print(hdr)
    print("-" * len(hdr))

    base_W = layer_weights(r, g, d)
    eq0, _, sw0 = simulate(g, d, base_W)
    m0 = row("v3 基线（双侧 MA200 减半 + 现金）", eq0, base_W.values, sw0)
    print("-" * len(hdr))

    arms = [
        ("A1 分层双侧 120减半+200减半",
         dict(gates_g=((120, 0.5), (200, 0.5)), gates_d=((120, 0.5), (200, 0.5))), {}),
        ("A2 分层双侧 200减半+250清仓",
         dict(gates_g=((200, 0.5), (250, 1.0)), gates_d=((200, 0.5), (250, 1.0))), {}),
        ("A3 双侧 60减半+200减半（更快）",
         dict(gates_g=((60, 0.5), (200, 0.5)), gates_d=((60, 0.5), (200, 0.5))), {}),
        ("B1 红利破 200 清仓（成长仍减半）",
         dict(gates_g=((200, 0.5),), gates_d=((200, 1.0),)), {}),
        ("B2 红利 60清仓+200减半",
         dict(gates_g=((200, 0.5),), gates_d=((60, 1.0), (200, 0.5))), {}),
        ("B3 红利 120减半+200减半 / 成长仅200",
         dict(gates_g=((200, 0.5),), gates_d=((120, 0.5), (200, 0.5))), {}),
    ]
    for tag, kw, ov in arms:
        W = layer_weights(r, g, d, **kw)
        eq, Wn, sw = simulate(g, d, W, **ov)
        row(tag, eq, Wn, sw)
    print("-" * len(hdr))

    ovs = [
        ("C1 制动 15%→0.6 / 25%→0.35（释放 8%）",
         dict(brake=([(0.15, 0.6), (0.25, 0.35)], 0.08))),
        ("C2 制动 20%→0.5（释放 10%）", dict(brake=([(0.20, 0.5)], 0.10))),
        ("C3 制动 12%→0.7 / 20%→0.4（释放 6%）",
         dict(brake=([(0.12, 0.7), (0.20, 0.4)], 0.06))),
        ("D1 波动率目标 15%（60日）", dict(vol_target=0.15)),
        ("D2 波动率目标 18%（60日）", dict(vol_target=0.18)),
        ("D3 波动率目标 12%（60日）", dict(vol_target=0.12)),
    ]
    for tag, ov in ovs:
        eq, Wn, sw = simulate(g, d, base_W, **ov)
        row(tag, eq, Wn, sw)
    print("-" * len(hdr))

    # 组合：最有效的分层 + 最有效的组合级叠加
    W = layer_weights(r, g, d, gates_g=((200, 0.5),), gates_d=((120, 0.5), (200, 0.5)))
    eq, Wn, sw = simulate(g, d, W, brake=([(0.15, 0.6), (0.25, 0.35)], 0.08))
    row("E1 B3 + C1（分层红利 + 回撤制动）", eq, Wn, sw)
    eq, Wn, sw = simulate(g, d, base_W, brake=([(0.15, 0.6), (0.25, 0.35)], 0.08),
                          vol_target=0.15)
    row("E2 C1 + D1（制动叠波动率目标）", eq, Wn, sw)


if __name__ == "__main__":
    if "--verify" in sys.argv:
        verify()
    elif "--grid-both" in sys.argv:
        grid("both")
    elif "--grid-div" in sys.argv:
        grid("div")
    elif "--control" in sys.argv:
        verify()
        control()
    elif "--grid-vol" in sys.argv:
        verify()
        grid_vol()
    else:
        main()
