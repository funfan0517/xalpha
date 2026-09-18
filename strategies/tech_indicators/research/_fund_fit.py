# -*- coding: utf-8 -*-
"""技术指标策略 · 「不同基金是否适合不同策略」分析。

对 55 只 ETF，逐只跑全部策略（基线5 + v1~v5 + 打分制 + 买入持有），看最优策略是否随
基金风格（避险/防御/中枢/进攻，取自 data/_universe.md）分化。

产物（相对本文件）：_fund_fit_report.md、_fund_fit_heatmap.png。模拟结果，非投资建议。
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
_PARENT = os.path.dirname(_HERE)                    # strategies/tech_indicators
_ROOT = os.path.dirname(os.path.dirname(_PARENT))   # 仓库根
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)


def _load(path, name):
    spec = importlib.util.spec_from_file_location(name, path)
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


base = _load(os.path.join(_PARENT, "backtest.py"), "ff_base")
V = {n: _load(os.path.join(_PARENT, n, "backtest.py"), "ff_" + n)
     for n in ["v1_kdj_ma10", "v2_macd_kdj", "v3_macd_boll",
               "v4_boll_kdj_macd", "v5_regime_switch", "v6_score_wf"]}

ITEMS = [
    ("MA", "MA"), ("VOL", "VOL"), ("MACD", "MACD"), ("KDJ", "KDJ"), ("BOLL", "BOLL"),
    ("v1 KDJ+MA10", V["v1_kdj_ma10"].build_variant),
    ("v2 MACD+KDJ", V["v2_macd_kdj"].build_variant),
    ("v3 MACD+BOLL", V["v3_macd_boll"].build_variant),
    ("v4 BOLL_KDJ_MACD", V["v4_boll_kdj_macd"].build_variant),
    ("v5 Regime切换", V["v5_regime_switch"].sig_regime),
    ("v6 打分制", V["v6_score_wf"].build_score),
]
BH = "买入持有"
LABELS = [lb for lb, _ in ITEMS] + [BH]
STYLE_ORDER = ["避险", "防御", "中枢", "进攻", "其他"]


def load_meta():
    """code -> {name, theme, style}。取自 data/_universe.md 场内对应列。"""
    m = {}
    p = os.path.join(_ROOT, "data", "_universe.md")
    for line in open(p, encoding="utf-8"):
        if not line.startswith("|"):
            continue
        f = [x.strip() for x in line.strip().strip("|").split("|")]
        if len(f) >= 10 and len(f[4]) == 6 and f[4].isdigit():
            m[f[4]] = {"name": f[5], "theme": f[1], "style": f[9] or "其他"}
    return m


def main():
    ind, lk = base.load()
    meta = load_meta()
    data = {}   # code -> {label: metrics}
    for code, r in ind.items():
        if code not in lk:
            continue
        close = base._arr(lk[code]["close"])
        dates = lk[code]["dates"]
        if len(close) != len(dates) or len(close) <= base.WARMUP + 2:
            continue
        bsigs = base.build_signals(close, r)
        row = {}
        for lb, fn in ITEMS:
            sig = bsigs[lb] if lb in bsigs else fn(close, r)
            m, eq, tr = base.backtest(close, sig, dates, code)
            row[lb] = {"ann": m["ann"], "dd": m["dd"], "sharpe": m["sharpe"],
                       "pf": base.trade_stats(tr)["profit_factor"], "n": m["n"]}
        bh, _ = base.backtest_bh(close, dates, code)
        row[BH] = {"ann": bh["ann"], "dd": bh["dd"], "sharpe": bh["sharpe"], "pf": float("inf"), "n": 1}
        data[code] = row

    codes = list(data)
    # 1) 各策略「获胜次数」（按夏普最优 / 按年化最优）
    win_sharpe = {lb: 0 for lb in LABELS}
    win_ann = {lb: 0 for lb in LABELS}
    beat_bh = {lb: 0 for lb in LABELS}
    best_of = {}
    for c in codes:
        row = data[c]
        b_s = max(LABELS, key=lambda lb: row[lb]["sharpe"])
        b_a = max(LABELS, key=lambda lb: row[lb]["ann"])
        win_sharpe[b_s] += 1
        win_ann[b_a] += 1
        best_of[c] = b_s
        for lb in LABELS[:-1]:
            if row[lb]["sharpe"] > row[BH]["sharpe"]:
                beat_bh[lb] += 1

    # 2) 风格 × 策略：中位夏普
    style_of = {c: meta.get(c, {}).get("style", "其他") for c in codes}
    mat = {}
    for st in STYLE_ORDER:
        sub = [c for c in codes if style_of[c] == st]
        if not sub:
            continue
        mat[st] = {lb: float(np.median([data[c][lb]["sharpe"] for c in sub])) for lb in LABELS}

    # 3) 逐基金最优策略
    L = [
        "# 技术指标策略 · 「不同基金是否适合不同策略」分析",
        "> 55 只 ETF · 逐只跑全部策略（含买入持有）· 各自全历史 · 未计费用 · 无风险利率 0。",
        "",
        "## 结论速览",
        "",
        f"- 优化目标取**夏普**时，55 只里被不同策略胜出的有 "
        f"**{sum(1 for v in win_sharpe.values() if v > 0)} 种策略** → 最优策略**确实因基金而异**。",
        f"- 「买入持有」在 **{win_sharpe[BH]} 只**上是夏普最优；策略胜出的有 **{55 - win_sharpe[BH]} 只**。",
        "",
        "## 各策略获胜次数（55 只中）",
        "",
        "| 策略 | 夏普最优次数 | 年化最优次数 | 夏普跑赢买入持有 |",
        "|---|---|---|---|",
    ]
    for lb in sorted(LABELS, key=lambda x: -win_sharpe[x]):
        b = "—" if lb == BH else f"{beat_bh[lb]}/55"
        L.append(f"| {lb} | {win_sharpe[lb]} | {win_ann[lb]} | {b} |")

    L += ["", "## 风格 × 策略（中位夏普）", "",
          "| 风格 | " + " | ".join(LABELS) + " | 最优策略 |",
          "|---" * (len(LABELS) + 2) + "|"]
    for st in STYLE_ORDER:
        if st not in mat:
            continue
        cells = [f"{mat[st][lb]:.2f}" for lb in LABELS]
        best = max(LABELS, key=lambda lb: mat[st][lb])
        L.append(f"| {st} | " + " | ".join(cells) + f" | **{best}** |")

    L += ["", "## 逐基金最优策略（按夏普）", "",
          "| 代码 | 名称 | 风格 | 最优策略 | 夏普 | 次优 | 买入持有夏普 |",
          "|---|---|---|---|---|---|---|"]
    order = sorted(codes, key=lambda c: (STYLE_ORDER.index(style_of[c]), c))
    for c in order:
        row = data[c]
        ranked = sorted(LABELS, key=lambda lb: -row[lb]["sharpe"])
        b, b2 = ranked[0], ranked[1]
        L.append(f"| {c} | {meta.get(c, {}).get('name', '')} | {style_of[c]} | {b} | "
                 f"{row[b]['sharpe']:.2f} | {b2} | {row[BH]['sharpe']:.2f} |")

    L += ["",
          "> 夏普按无风险利率 0 计；单笔=建/平仓收盘价收益（未计费用）。**模拟结果，非投资建议。**"]

    with open(os.path.join(_HERE, "_fund_fit_report.md"), "w", encoding="utf-8") as f:
        f.write("\n".join(L))
    print("报告:", os.path.join(_HERE, "_fund_fit_report.md"))
    print("夏普最优分布:", {k: v for k, v in win_sharpe.items() if v})

    _heatmap(codes, data, meta, style_of, os.path.join(_HERE, "_fund_fit_heatmap.png"))


def _heatmap(codes, data, meta, style_of, path):
    try:
        import matplotlib
        matplotlib.use("Agg")
        import matplotlib.pyplot as plt
    except Exception as e:  # noqa
        print("matplotlib 不可用，跳过作图:", e)
        return
    plt.rcParams["font.sans-serif"] = ["Microsoft YaHei", "SimHei", "DejaVu Sans"]
    plt.rcParams["axes.unicode_minus"] = False
    order = sorted(codes, key=lambda c: (STYLE_ORDER.index(style_of[c]), c))
    M = np.array([[data[c][lb]["sharpe"] for lb in LABELS] for c in order])
    fig, ax = plt.subplots(figsize=(11, 14))
    vmax = np.nanpercentile(np.abs(M), 95) or 1.0
    im = ax.imshow(M, aspect="auto", cmap="RdYlGn", vmin=-vmax, vmax=vmax)
    ax.set_xticks(range(len(LABELS)))
    ax.set_xticklabels(LABELS, rotation=40, ha="right", fontsize=9)
    ax.set_yticks(range(len(order)))
    ax.set_yticklabels([f"{c} {(meta.get(c, {}).get('name') or '')[:8]}[{style_of[c]}]" for c in order], fontsize=7)
    ax.set_title("55 只 ETF × 策略 · 夏普（绿=优 / 红=差）", fontsize=12)
    fig.colorbar(im, ax=ax, shrink=0.5, label="Sharpe")
    fig.tight_layout()
    fig.savefig(path, dpi=110)
    plt.close(fig)
    print("图:", path)


if __name__ == "__main__":
    main()
