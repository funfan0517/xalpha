# -*- coding: utf-8 -*-
"""v3（MACD+BOLL 突破）优化扫描。

v3 现状：DIF>0 且 DIF>DEA 且 布林带宽放大 且 收盘上穿布林上轨 → 买；DIF死叉 或 下穿中轨 → 卖。
本脚本扫描其可调点：布林窗口/倍数、开口定义、出场规则、跟踪止损，找更优配置并做样本外交叉检验。

产物：research/_v3_tune_report.md。模拟结果，非投资建议。
"""
import importlib.util
import os
import sys

import numpy as np
import pandas as pd

try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:  # noqa
    pass

_HERE = os.path.dirname(os.path.abspath(__file__))
_PARENT = os.path.dirname(_HERE)
_ROOT = os.path.dirname(os.path.dirname(_PARENT))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)


def _load(path, name):
    spec = importlib.util.spec_from_file_location(name, path)
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


base = _load(os.path.join(_PARENT, "backtest.py"), "vt_base")
WARMUP = base.WARMUP
TRAIN_END, TEST_START = "2023-12-31", "2024-01-01"


def sig_v3p(close, r, w=20, k=2.0, exit_mode="both", expand="rising", trail=0.0):
    """v3 参数化：返回 0/1 仓位序列。"""
    n = len(close)
    dif, dea = base._arr(r["DIF"]), base._arr(r["DEA"])
    s = pd.Series(close, dtype="float64")
    mid = s.rolling(w).mean().to_numpy()
    sd = s.rolling(w).std().to_numpy()
    up = mid + k * sd
    bw = pd.Series((up - (mid - k * sd)) / mid)
    bwma = bw.rolling(20).mean().to_numpy()
    bw = bw.to_numpy()
    sig = np.zeros(n, dtype=int)
    p, entry, peak = 0, None, None
    for t in range(1, n):
        if (np.isnan(up[t]) or np.isnan(mid[t]) or np.isnan(bw[t]) or np.isnan(bwma[t])
                or np.isnan(up[t - 1])):
            sig[t] = p
            continue
        expand_ok = bw[t] > bw[t - 1] if expand == "rising" else bw[t] > bwma[t]
        breakout = close[t] > up[t] and close[t - 1] <= up[t - 1]
        if p == 0:
            if dif[t] > 0 and dif[t] > dea[t] and expand_ok and breakout:
                p, entry, peak = 1, close[t], close[t]
        else:
            peak = max(peak, close[t])
            death = dif[t] < dea[t] and dif[t - 1] >= dea[t - 1]
            midbrk = close[t] < mid[t] and close[t - 1] >= mid[t - 1]
            trailbrk = trail > 0 and close[t] < peak * (1 - trail)
            hit = trailbrk or (exit_mode in ("both", "death") and death) or \
                (exit_mode in ("both", "mid") and midbrk)
            if hit:
                p, entry, peak = 0, None, None
        sig[t] = p
    return sig


def evaluate(ind, lk, **kw):
    rows = []
    for code, r in ind.items():
        if code not in lk:
            continue
        close = base._arr(lk[code]["close"])
        dates = lk[code]["dates"]
        if len(close) != len(dates) or len(close) <= WARMUP + 2:
            continue
        m, eq, tr = base.backtest(close, sig_v3p(close, r, **kw), dates, code)
        bh, _ = base.backtest_bh(close, dates, code)
        rows.append({"code": code, "m": m, "bh": bh, "trades": tr})
    return base._agg(rows)


def main():
    ind, lk = base.load()
    grid = []
    for w in (15, 20, 30):
        for k in (1.8, 2.0, 2.4):
            for ex in ("both", "death", "mid"):
                for exp in ("rising", "above_ma"):
                    for tr in (0.0, 0.10):
                        kw = {"w": w, "k": k, "exit_mode": ex, "expand": exp, "trail": tr}
                        a = evaluate(ind, lk, **kw)
                        grid.append((kw, a))
    grid.sort(key=lambda x: -x[1]["sharpe"])

    def fmt(kw):
        exmap = {"both": "死叉|中轨", "death": "仅死叉", "mid": "仅中轨"}
        expmap = {"rising": "带宽上升", "above_ma": "带宽>均线"}
        return (f"w{kw['w']}/k{kw['k']} · 出场{exmap[kw['exit_mode']]} · 开口{expmap[kw['expand']]} · "
                f"跟踪止损{kw['trail'] or '无'}")

    L = [
        "# v3（MACD+BOLL 突破）优化扫描",
        "> 55 只 ETF · 各自全历史 · 中位数 · 未计费用 · 无风险利率 0。",
        "",
        "## 全样本 Top 15 配置",
        "",
        "| 排名 | 配置 | 中位年化 | 中位最大回撤 | 中位夏普 | 曝光 | 交易 | 利润因子 |",
        "|---|---|---|---|---|---|---|---|",
    ]
    for i, (kw, a) in enumerate(grid[:15], 1):
        L.append(f"| {i} | {fmt(kw)} | {base.pct(a['ann'])} | {base.pct(a['dd'])} | {a['sharpe']:.2f} | "
                 f"{a['exposure']:.0%} | {a['pooled']['n']} | {a['pooled']['profit_factor']:.2f} |")

    # 基线 v3（w20 k2 both rising trail0）位置
    base_kw = {"w": 20, "k": 2.0, "exit_mode": "both", "expand": "rising", "trail": 0.0}
    a_base = evaluate(ind, lk, **base_kw)
    rank = next((i for i, (kw, _) in enumerate(grid, 1) if kw == base_kw), None)
    L += ["", f"**基线 v3（{fmt(base_kw)}）排名 {rank}/{len(grid)}**："
              f"年化 {base.pct(a_base['ann'])} · 回撤 {base.pct(a_base['dd'])} · "
              f"夏普 {a_base['sharpe']:.2f} · 利润因子 {a_base['pooled']['profit_factor']:.2f}"]

    # 样本外交叉检验：用 ≤2023 选最优，测 2024+
    def median_sharpe(sigkw, d0, d1):
        sh = []
        for code, r in ind.items():
            if code not in lk:
                continue
            close = base._arr(lk[code]["close"])
            dates = lk[code]["dates"]
            if len(close) != len(dates) or len(close) <= WARMUP + 2:
                continue
            eq = base.backtest(close, sig_v3p(close, r, **sigkw), dates, code)[1]
            i0 = next((i for i, d in enumerate(dates) if d >= d0), None)
            i1 = next((len(dates) - 1 - i for i, d in enumerate(reversed(dates)) if d <= d1), None)
            if i0 is None or i1 is None or i1 <= i0:
                continue
            k0, k1 = max(0, i0 - WARMUP), min(i1 - WARMUP, len(eq) - 1)
            if k1 <= k0:
                continue
            seg = [v / eq[k0] for v in eq[k0:k1 + 1]]
            sh.append(base._metrics(seg, [])["sharpe"])
        return float(np.median(sh)) if sh else float("nan")

    train_rank = sorted(grid, key=lambda x: -median_sharpe(x[0], "1900-01-01", TRAIN_END))
    best_train_kw = train_rank[0][0]
    L += [
        "",
        "## 样本外交叉检验（用 ≤2023 选参数，测 2024+）",
        "",
        "| 配置 | 训练期(≤2023)夏普 | 样本外(2024+)夏普 |",
        "|---|---|---|",
        f"| 训练期最优：{fmt(best_train_kw)} | {median_sharpe(best_train_kw, '1900-01-01', TRAIN_END):.2f} | "
        f"{median_sharpe(best_train_kw, TEST_START, '9999'):.2f} |",
        f"| 全样本最优：{fmt(grid[0][0])} | {median_sharpe(grid[0][0], '1900-01-01', TRAIN_END):.2f} | "
        f"{median_sharpe(grid[0][0], TEST_START, '9999'):.2f} |",
        f"| 基线 v3：{fmt(base_kw)} | {median_sharpe(base_kw, '1900-01-01', TRAIN_END):.2f} | "
        f"{median_sharpe(base_kw, TEST_START, '9999'):.2f} |",
    ]

    with open(os.path.join(_HERE, "_v3_tune_report.md"), "w", encoding="utf-8") as f:
        f.write("\n".join(L))
    print("报告:", os.path.join(_HERE, "_v3_tune_report.md"))
    print("全样本最优:", fmt(grid[0][0]), "夏普 %.2f" % grid[0][1]["sharpe"],
          "PF %.2f" % grid[0][1]["pooled"]["profit_factor"], "回撤 %.2f%%" % (grid[0][1]["dd"] * 100))
    print("基线 v3 排名:", rank, "/", len(grid))
    print("训练期最优:", fmt(best_train_kw))


if __name__ == "__main__":
    main()
