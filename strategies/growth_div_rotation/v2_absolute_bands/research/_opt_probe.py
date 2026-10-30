# -*- coding: utf-8 -*-
"""v2 优化方向探针：在**不改 v2 本体**的前提下，快速对照几种规则改法。

用途：给「策略有什么优化方向」提供数字依据，而不是拍脑袋。
每个变体都守同一条时序（用 t-1 的信息决定 t 的权重），只改「怎么把比值 R 翻译成仓位」。

用法：python strategies/growth_div_rotation/v2_absolute_bands/research/_opt_probe.py
产物：仅打印对照表（不落盘）；确认方向值得做再正式写进变种。
"""
import io
import os
import sys

try:
    sys.stdout.reconfigure(encoding="utf-8")
except (AttributeError, OSError):
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8")

_DIR = os.path.dirname(os.path.abspath(__file__))
_V2 = os.path.dirname(_DIR)
if _V2 not in sys.path:
    sys.path.insert(0, _V2)

import numpy as np  # noqa: E402
import pandas as pd  # noqa: E402

import rule  # noqa: E402
import data as datamod  # noqa: E402
import factors  # noqa: E402
import engine  # noqa: E402

LO, HI = rule.RATIO_LOW, rule.RATIO_HIGH


def build_w(r, g, decide, init=0.0):
    """decide(i) 用第 i 根 bar 的信息给出下一根的目标权重（i=-1 为起点）。"""
    w = np.empty(len(r))
    w[0] = init
    for t in range(1, len(r)):
        w[t] = decide(t - 1)
    return pd.Series(w, index=r.index)


def run_variant(name, w, g, d, note=""):
    res = engine.run(g, d, w)
    m = engine.metrics(res["eq"])
    return dict(name=name, note=note, ann=m["ann"], dd=m["dd"], shp=m["shp"],
                calmar=m["calmar"], sw=len(res["switches"]), mean_w=float(w.mean()),
                eq=res["eq"])


def main():
    wide = datamod.load_wide()
    g, d = wide[rule.GROWTH_INDEX], wide[rule.DIV_INDEX]
    r = factors.ratio_series(g, d)
    g, d = g.reindex(r.index), d.reindex(r.index)
    rv, gv = r.values, g.values
    ma250 = g.rolling(250).mean().values
    ma120 = g.rolling(120).mean().values
    rank = factors.rolling_rank(r).values
    rows = []

    # 0) 基线
    rows.append(run_variant("基线 0.30/0.70", engine.signal_weights(r), g, d, "复现图口径"))

    # 1) 单点更宽/更窄（只是挪线，用于量化「挪线的收益」）
    for lo, hi in [(0.28, 0.68), (0.32, 0.75), (0.35, 0.65)]:
        rows.append(run_variant(f"单点 {lo:.2f}/{hi:.2f}",
                                engine.signal_weights(r, lo, hi), g, d, "仅挪阈值"))

    # 2) 连续仓位：R 从 0.70 线性降到 0.30 时，成长权重从 0 升到 1
    w_lin = build_w(r, g, lambda i: (0.0 if i < 0 else
                                     float(np.clip((HI - rv[i]) / (HI - LO), 0.0, 1.0))))
    rows.append(run_variant("连续仓位 linear", w_lin, g, d, "w=(0.70-R)/0.40，0.30/0.70 全同"))

    # 3) 五档阶梯（0.30 以下满仓 → 0.70 以上空仓，每 0.08 降 20%）
    edges = [0.30, 0.38, 0.46, 0.54, 0.62, 0.70]
    steps = [1.0, 0.8, 0.6, 0.4, 0.2, 0.0]

    def _step(i):
        if i < 0:
            return 0.0
        x = rv[i]
        for k in range(len(edges) - 1):
            if x < edges[k + 1]:
                return steps[k]
        return 0.0

    rows.append(run_variant("五档阶梯 0.3~0.7", build_w(r, g, _step), g, d, "每档 20%"))

    # 4) 趋势过滤：创业板指跌破 250 日均线时，成长权重封顶 0.5 / 0
    base_w = engine.signal_weights(r).values

    def _ma_cap(cap):
        def f(i):
            if i < 0:
                return 0.0
            w0 = base_w[i]
            if not np.isnan(ma250[i]) and gv[i] < ma250[i]:
                return min(w0, cap)
            return w0
        return f

    rows.append(run_variant("基线 + 破 MA250 封顶 50%", build_w(r, g, _ma_cap(0.5)), g, d,
                            "趋势过滤（弱）"))
    rows.append(run_variant("基线 + 破 MA250 清仓", build_w(r, g, _ma_cap(0.0)), g, d,
                            "趋势过滤（强）"))

    # 5) 连续仓位 + 趋势封顶（组合）
    def _combo(i):
        if i < 0:
            return 0.0
        w0 = float(np.clip((HI - rv[i]) / (HI - LO), 0.0, 1.0))
        if not np.isnan(ma250[i]) and gv[i] < ma250[i]:
            w0 = min(w0, 0.5)
        return w0

    rows.append(run_variant("连续仓位 + 破 MA250 封顶 50%", build_w(r, g, _combo), g, d,
                            "两个改动叠加"))

    # 6) 迟滞带：卖出后 R 回落到 0.55 就恢复半仓（打破「0.70 卖了要等 0.30」的死锁）
    def _hyst(i):
        if i < 0:
            return 0.0
        x = rv[i]
        if x <= LO:
            return 1.0
        if x >= HI:
            return 0.0
        if x <= 0.55:
            return 0.5
        return 0.0 if x >= 0.65 else 0.5

    rows.append(run_variant("迟滞带 0.30/0.55/0.65/0.70", build_w(r, g, _hyst), g, d,
                            "中间恢复半仓，破死锁"))

    # 7) 滚动分位（无前视）几个分位档
    for ql, qh in [(0.10, 0.90), (0.05, 0.95)]:
        w = engine.signal_weights(r, rank=pd.Series(rank, index=r.index),
                                  rank_low=ql, rank_high=qh)
        rows.append(run_variant(f"滚动分位 {ql:.0%}/{qh:.0%}", w, g, d, "无前视"))

    # 8) 净值回撤熔断（叠加层）：策略自身从峰值回撤 > 30% 时清仓，R 回到低位再开
    def _circuit():
        w = np.zeros(len(r))
        eq_peak, cur = 1.0, 0.0
        equity = 1.0
        rg = np.concatenate([[0.0], gv[1:] / gv[:-1] - 1.0])
        rd = np.concatenate([[0.0], d[1:] / d[:-1] - 1.0])
        halted = False
        for t in range(1, len(r)):
            x = rv[t - 1]
            if halted:
                if x <= LO:                   # 熔断后要等 R 回到低估区才重新开火
                    cur, halted = 1.0, False
                else:
                    cur = 0.0
            else:
                if x <= LO:
                    cur = 1.0
                elif x >= HI:
                    cur = 0.0
                ddown = equity / eq_peak - 1
                if ddown < -0.30:
                    cur, halted = 0.0, True
            w[t] = cur
            equity *= (1 + cur * rg[t] + (1 - cur) * rd[t])
            eq_peak = max(eq_peak, equity)
        return pd.Series(w, index=r.index)

    rows.append(run_variant("基线 + 净值回撤 30% 熔断", _circuit(), g, d,
                            "风控层（会引入额外拟合）"))

    # 基准
    bh_g = engine.run(g, d, pd.Series(1.0, index=r.index), fee=0)["eq"]
    bh_d = engine.run(g, d, pd.Series(0.0, index=r.index), fee=0)["eq"]
    mbg, mbd = engine.metrics(bh_g), engine.metrics(bh_d)

    hdr = (f"{'变体':<32}{'年化':>9}{'回撤':>9}{'夏普':>7}{'Calmar':>8}"
           f"{'切换':>6}{'成长暴露':>9}  备注")
    print(hdr)
    print("-" * len(hdr))
    for x in rows:
        print(f"{x['name']:<32}{x['ann'] * 100:>8.2f}%{x['dd'] * 100:>8.2f}%"
              f"{x['shp']:>7.2f}{x['calmar']:>8.2f}{x['sw']:>6}{x['mean_w']:>8.1%}  {x['note']}")
    print("-" * len(hdr))
    print(f"{'买入持有创业板':<32}{mbg['ann'] * 100:>8.2f}%{mbg['dd'] * 100:>8.2f}%"
          f"{mbg['shp']:>7.2f}{mbg['calmar']:>8.2f}{0:>6}{1.0:>8.1%}")
    print(f"{'买入持有中证红利':<32}{mbd['ann'] * 100:>8.2f}%{mbd['dd'] * 100:>8.2f}%"
          f"{mbd['shp']:>7.2f}{mbd['calmar']:>8.2f}{0:>6}{0.0:>8.1%}")

    # 阈值曲面：判断「挪线」到底是平台还是孤峰（孤峰 = 过拟合，不能采信）
    lows = [0.28, 0.30, 0.32, 0.34, 0.35, 0.36, 0.38]
    highs = [0.62, 0.64, 0.65, 0.66, 0.68, 0.70, 0.72, 0.75]
    print("\nCalmar 曲面（行=低估线，列=高估线）：")
    print("      " + "".join(f"{h:>7.2f}" for h in highs))
    for lo in lows:
        line = f"{lo:>5.2f} "
        for hi in highs:
            res = engine.run(g, d, engine.signal_weights(r, lo, hi))
            m = engine.metrics(res["eq"])
            line += f"{m['calmar']:>7.2f}"
        print(line)
    print("\n年化 曲面（%）：")
    print("      " + "".join(f"{h:>7.2f}" for h in highs))
    for lo in lows:
        line = f"{lo:>5.2f} "
        for hi in highs:
            res = engine.run(g, d, engine.signal_weights(r, lo, hi))
            line += f"{engine.metrics(res['eq'])['ann'] * 100:>7.2f}"
        print(line)

    # 分段稳健性：前半段标定思路能否延续到后半段
    split = pd.Timestamp("2019-01-01")
    print("\n分段（2019-01-01 切）：")
    print(f"{'变体':<32}{'前半 年化/回撤':>20}{'后半 年化/回撤':>20}")
    for x in rows:
        eq = x["eq"]
        a = engine.metrics(eq[eq.index < split])
        b = engine.metrics(eq[eq.index >= split])
        print(f"{x['name']:<32}{a['ann'] * 100:>9.2f}%/{a['dd'] * 100:>7.2f}%"
              f"{b['ann'] * 100:>13.2f}%/{b['dd'] * 100:>7.2f}%")


if __name__ == "__main__":
    main()
