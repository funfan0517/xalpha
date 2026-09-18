# -*- coding: utf-8 -*-
"""技术指标策略 · 变种 7：风格路由（按基金风格自动选策略）· 全池回测。

思路来自 research/_fund_fit.py 的发现：最优策略随基金**风格**分化。路由规则：
    避险 → 买入持有（不择时）· 防御 → BOLL（均值回归）
    中枢 → MACD（趋势）      · 进攻 → v3 MACD+BOLL（突破）
    其他 → MACD

回测两件事：
1) 全样本：路由组合（逐基金用其风格对应策略） vs 各统一策略（全池都用同一策略）的中位数对照；
2) 样本外：用 ≤2023 数据**推导**风格→策略映射，再在 2024+ 上测试，检验"按风格路由"能否泛化。

产物：backtest/_v7_style_route_report.md / .jsonl / .png。模拟结果，非投资建议。
"""
import importlib.util
import json
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


base = _load(os.path.join(_PARENT, "backtest.py"), "v7_base")
V = {n: _load(os.path.join(_PARENT, n, "backtest.py"), "v7_" + n)
     for n in ["v1_kdj_ma10", "v2_macd_kdj", "v3_macd_boll",
               "v4_golden_triad", "v5_regime_switch", "v6_score_wf"]}

OUTDIR = os.path.join(_HERE, "backtest")
WARMUP = base.WARMUP
BH = "买入持有"
UNIFORM = ["MA", "VOL", "MACD", "KDJ", "BOLL", "v1 KDJ+MA10", "v2 MACD+KDJ",
           "v3 MACD+BOLL", "v4 黄金三角", "v5 Regime切换", "v6 打分制"]
SIG_FN = {
    "v1 KDJ+MA10": V["v1_kdj_ma10"].build_variant,
    "v2 MACD+KDJ": V["v2_macd_kdj"].build_variant,
    "v3 MACD+BOLL": V["v3_macd_boll"].build_variant,
    "v4 黄金三角": V["v4_golden_triad"].build_variant,
    "v5 Regime切换": V["v5_regime_switch"].sig_regime,
    "v6 打分制": V["v6_score_wf"].build_score,
    BH: lambda c, r: np.ones(len(c), dtype=int),
}
STYLES = ["避险", "防御", "中枢", "进攻", "其他"]
ROUTE = {"避险": BH, "防御": "BOLL", "中枢": "MACD", "进攻": "v3 MACD+BOLL", "其他": "MACD"}
TRAIN_END, TEST_START = "2023-12-31", "2024-01-01"


def load_meta():
    m = {}
    p = os.path.join(_ROOT, "data", "_universe.md")
    for line in open(p, encoding="utf-8"):
        if not line.startswith("|"):
            continue
        f = [x.strip() for x in line.strip().strip("|").split("|")]
        if len(f) >= 10 and len(f[4]) == 6 and f[4].isdigit():
            m[f[4]] = {"name": f[5], "style": f[9] or "其他"}
    return m


def _sig(label, close, r, bsigs):
    return bsigs[label] if label in bsigs else SIG_FN[label](close, r)


def _window(eq, trades, dates, sig, d0, d1):
    i0 = next((i for i, d in enumerate(dates) if d >= d0), None)
    i1 = next((len(dates) - 1 - i for i, d in enumerate(reversed(dates)) if d <= d1), None)
    if i0 is None or i1 is None or i1 <= i0:
        return None
    k0, k1 = max(0, i0 - WARMUP), min(i1 - WARMUP, len(eq) - 1)
    if k1 <= k0:
        return None
    seg = [v / eq[k0] for v in eq[k0:k1 + 1]]
    tr = [t for t in trades if d0 <= t["entry_date"] <= d1]
    m = base._metrics(seg, tr)
    m["exposure"] = float(np.mean(sig[k0:k1 + 1]))
    return m, tr


def _row(label, a, best=False):
    tag = "**" if best else ""
    return (f"| {tag}{label}{tag} | {base.pct(a['ann'])} | {base.pct(a['dd'])} | {a['sharpe']:.2f} | "
            f"{a['exposure']:.0%} | {a['pooled']['n']} | {a['pooled']['profit_factor']:.2f} |")


def main():
    ind, lk = base.load()
    meta = load_meta()
    eqs, trs, ms, sigs, dts, style = {}, {}, {}, {}, {}, {}
    for code, r in ind.items():
        if code not in lk:
            continue
        close = base._arr(lk[code]["close"])
        dates = lk[code]["dates"]
        if len(close) != len(dates) or len(close) <= WARMUP + 2:
            continue
        bsigs = base.build_signals(close, r)
        eqs[code], trs[code], ms[code], sigs[code], dts[code] = {}, {}, {}, {}, dates
        for lb in UNIFORM + [BH]:
            sig = _sig(lb, close, r, bsigs)
            m, eq, tr = base.backtest(close, sig, dates, code)
            sigs[code][lb] = sig
            eqs[code][lb] = eq
            trs[code][lb] = tr
            ms[code][lb] = m
        style[code] = meta.get(code, {}).get("style", "其他")
    codes = list(eqs)

    # 1) 全样本：路由 vs 统一
    route_lb = {c: ROUTE.get(style[c], "MACD") for c in codes}

    def row_full(c, lb):
        return {"code": c, "m": ms[c][lb], "bh": ms[c][BH], "trades": trs[c][lb]}

    rows_route = [row_full(c, route_lb[c]) for c in codes]
    a_route = base._agg(rows_route)
    a_uni = {lb: base._agg([row_full(c, lb) for c in codes]) for lb in UNIFORM}
    a_bh = base._agg([row_full(c, BH) for c in codes])

    # 2) 样本外：用 ≤2023 推导 风格->策略 映射，再在 2024+ 测试
    def derive_map(d0, d1):
        mp = {}
        for st in STYLES:
            sub = [c for c in codes if style[c] == st]
            if not sub:
                continue
            best, bs = None, -9
            for lb in UNIFORM + [BH]:
                sh = [_window(eqs[c][lb], trs[c][lb], dts[c], sigs[c][lb], d0, d1) for c in sub]
                sh = [s[0]["sharpe"] for s in sh if s]
                if sh and np.median(sh) > bs:
                    bs, best = np.median(sh), lb
            mp[st] = best or "MACD"
        return mp

    train_start = min(dts[c][WARMUP] for c in codes)
    map_train = derive_map(train_start, TRAIN_END)
    map_full = ROUTE

    def oos(map_used):
        rows = []
        for c in codes:
            lb = map_used.get(style[c], "MACD")
            w = _window(eqs[c][lb], trs[c][lb], dts[c], sigs[c][lb], TEST_START, "9999-99-99")
            if w:
                rows.append({"code": c, "m": w[0], "bh": {"ann": 0, "dd": -1, "sharpe": 0}, "trades": w[1]})
        return base._agg(rows)

    def oos_label(lb):
        rows = []
        for c in codes:
            w = _window(eqs[c][lb], trs[c][lb], dts[c], sigs[c][lb], TEST_START, "9999-99-99")
            if w:
                rows.append({"code": c, "m": w[0], "bh": {"ann": 0, "dd": -1, "sharpe": 0}, "trades": w[1]})
        return base._agg(rows)

    a_oos_train = oos(map_train)
    a_oos_full = oos(map_full)
    # 样本外里最好的统一策略
    oos_uni = {lb: oos_label(lb) for lb in UNIFORM}
    best_oos_lb = max(oos_uni, key=lambda lb: oos_uni[lb]["sharpe"])
    a_oos_bh = oos_label(BH)

    best_full_lb = max(UNIFORM, key=lambda lb: a_uni[lb]["sharpe"])

    os.makedirs(OUTDIR, exist_ok=True)
    with open(os.path.join(OUTDIR, "_v7_style_route_results.jsonl"), "w", encoding="utf-8") as f:
        for c in codes:
            f.write(json.dumps({"code": c, "style": style[c], "strategy": route_lb[c],
                                "ann": round(base._metrics(eqs[c][route_lb[c]], trs[c][route_lb[c]])["ann"], 4),
                                "sharpe": round(base._metrics(eqs[c][route_lb[c]], trs[c][route_lb[c]])["sharpe"], 3)},
                               ensure_ascii=False) + "\n")

    L = [
        "# 技术指标策略 · 变种 7：风格路由 · 全池回测",
        "> 55 只 ETF · 逐基金用其**风格对应策略** · 各自全历史 · 未计费用 · 无风险利率 0。",
        "",
        "## 路由规则",
        "",
        "| 风格 | 策略 | 依据 |",
        "|---|---|---|",
        "| 避险 | 买入持有 | 债/金银长期单边上涨，择时为负贡献 |",
        "| 防御 | BOLL | 红利/银行等区间震荡，均值回归有效 |",
        "| 中枢 | MACD | 宽基温和趋势 |",
        "| 进攻 | v3 MACD+BOLL | 行业主题高波动强趋势，突破跟随 |",
        "",
        "## 全样本对照（中位数 · 55 只）",
        "",
        "| 组合/策略 | 中位年化 | 中位最大回撤 | 中位夏普 | 中位曝光 | 交易 | 利润因子 |",
        "|---|---|---|---|---|---|---|",
        _row("风格路由组合", a_route, best=True),
    ]
    for lb in sorted(UNIFORM, key=lambda x: -a_uni[x]["sharpe"]):
        L.append(_row(f"统一：{lb}", a_uni[lb]))
    L.append(_row("统一：买入持有", a_bh))

    L += [
        "",
        f"## 样本外验证（{TEST_START} 起 · 用 ≤{TRAIN_END} 数据推导路由）",
        "",
        f"- 训练期推导出的映射：`{map_train}`",
        f"- 全样本映射（对照）：`{map_full}`",
        "",
        "| 口径（OOS） | 中位年化 | 中位最大回撤 | 中位夏普 | 中位曝光 | 交易 | 利润因子 |",
        "|---|---|---|---|---|---|---|",
        _row("风格路由（训练期映射）", a_oos_train, best=True),
        _row("风格路由（全样本映射）", a_oos_full),
        _row(f"最佳统一策略（{best_oos_lb}）", oos_uni[best_oos_lb]),
        _row("买入持有", a_oos_bh),
        "",
        "## 结论",
        "",
        f"- 全样本：风格路由中位夏普 **{a_route['sharpe']:.2f}**，"
        f"对比最佳统一策略（{best_full_lb}）的 {a_uni[best_full_lb]['sharpe']:.2f}。",
        f"- 样本外：路由（训练期映射）夏普 **{a_oos_train['sharpe']:.2f}**，"
        f"最佳统一策略（{best_oos_lb}）{oos_uni[best_oos_lb]['sharpe']:.2f}，买入持有 {a_oos_bh['sharpe']:.2f}。",
        "> ⚠️ 路由映射本身由**同一历史**归纳得到，存在样本内偏好；样本外一行才是较可信的检验。",
        "",
        "> 口径：单笔=建/平仓收盘价收益（未计费用）；无风险利率 0。**模拟结果，非投资建议。**",
    ]

    with open(os.path.join(OUTDIR, "_v7_style_route_report.md"), "w", encoding="utf-8") as f:
        f.write("\n".join(L))
    print("报告:", os.path.join(OUTDIR, "_v7_style_route_report.md"))
    print("全样本 路由夏普=%.2f vs 最佳统一(%s)=%.2f" % (a_route["sharpe"], best_full_lb, a_uni[best_full_lb]["sharpe"]))
    print("样本外 路由(训练映射)夏普=%.2f vs 最佳统一(%s)=%.2f vs BH=%.2f"
          % (a_oos_train["sharpe"], best_oos_lb, oos_uni[best_oos_lb]["sharpe"], a_oos_bh["sharpe"]))
    print("训练期映射:", map_train)

    _plot([("风格路由", a_route)] + [(lb, a_uni[lb]) for lb in UNIFORM] + [(BH, a_bh)],
          os.path.join(OUTDIR, "_v7_style_route.png"))


def _plot(rows, path):
    try:
        import matplotlib
        matplotlib.use("Agg")
        import matplotlib.pyplot as plt
    except Exception as e:  # noqa
        print("matplotlib 不可用，跳过作图:", e)
        return
    plt.rcParams["font.sans-serif"] = ["Microsoft YaHei", "SimHei", "DejaVu Sans"]
    plt.rcParams["axes.unicode_minus"] = False
    labels = [r[0] for r in rows]
    vals = [r[1]["sharpe"] for r in rows]
    order = np.argsort(vals)
    fig, ax = plt.subplots(figsize=(10, 7))
    colors = ["#c2185b" if labels[i] == "风格路由" else "#4a7" for i in order]
    ax.barh([labels[i] for i in order], [vals[i] for i in order], color=colors)
    ax.set_xlabel("中位夏普（全样本）")
    ax.set_title("风格路由 vs 各统一策略 · 中位夏普")
    ax.grid(alpha=0.3, axis="x")
    fig.tight_layout()
    fig.savefig(path, dpi=120)
    plt.close(fig)
    print("图:", path)


if __name__ == "__main__":
    main()
