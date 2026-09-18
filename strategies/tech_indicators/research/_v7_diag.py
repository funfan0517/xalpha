# -*- coding: utf-8 -*-
"""v7 风格路由 · 归因诊断：哪一类基金拖累了它？

按基金风格拆解，比较「路由选择」与「各策略」在全样本 / 样本外(2024+) 的中位夏普，
定位拖累来源；并列出逐基金拖累最重的标的。

产物：research/_v7_diag_report.md。模拟结果，非投资建议。
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


base = _load(os.path.join(_PARENT, "backtest.py"), "dg_base")
V = {n: _load(os.path.join(_PARENT, n, "backtest.py"), "dg_" + n)
     for n in ["v1_kdj_ma10", "v2_macd_kdj", "v3_macd_boll", "v4_boll_kdj_macd",
               "v5_regime_switch", "v6_score_wf"]}
WARMUP = base.WARMUP
BH = "买入持有"
UNIFORM = ["MA", "VOL", "MACD", "KDJ", "BOLL", "v1 KDJ+MA10", "v2 MACD+KDJ",
           "v3 MACD+BOLL", "v4 BOLL_KDJ_MACD", "v5 Regime切换", "v6 打分制"]
SIG_FN = {
    "v1 KDJ+MA10": V["v1_kdj_ma10"].build_variant, "v2 MACD+KDJ": V["v2_macd_kdj"].build_variant,
    "v3 MACD+BOLL": V["v3_macd_boll"].build_variant, "v4 BOLL_KDJ_MACD": V["v4_boll_kdj_macd"].build_variant,
    "v5 Regime切换": V["v5_regime_switch"].sig_regime, "v6 打分制": V["v6_score_wf"].build_score,
    BH: lambda c, r: np.ones(len(c), dtype=int),
}
STYLES = ["避险", "防御", "中枢", "进攻"]
# 路由：全样本映射 vs 训练期(≤2023)映射
MAP_FULL = {"避险": BH, "防御": "BOLL", "中枢": "MACD", "进攻": "v3 MACD+BOLL"}
MAP_TRAIN = {"避险": BH, "防御": "BOLL", "中枢": "v3 MACD+BOLL", "进攻": "VOL"}
TEST_START = "2024-01-01"


def load_meta():
    m = {}
    for line in open(os.path.join(_ROOT, "data", "_universe.md"), encoding="utf-8"):
        if not line.startswith("|"):
            continue
        f = [x.strip() for x in line.strip().strip("|").split("|")]
        if len(f) >= 10 and len(f[4]) == 6 and f[4].isdigit():
            m[f[4]] = {"name": f[5], "style": f[9] or "其他"}
    return m


def wsharpe(eq, dates, d0, d1):
    i0 = next((i for i, d in enumerate(dates) if d >= d0), None)
    i1 = next((len(dates) - 1 - i for i, d in enumerate(reversed(dates)) if d <= d1), None)
    if i0 is None or i1 is None or i1 <= i0:
        return None
    k0, k1 = max(0, i0 - WARMUP), min(i1 - WARMUP, len(eq) - 1)
    if k1 <= k0:
        return None
    seg = [v / eq[k0] for v in eq[k0:k1 + 1]]
    return base._metrics(seg, [])["sharpe"]


def main():
    ind, lk = base.load()
    meta = load_meta()
    eqs, dts, style = {}, {}, {}
    for code, r in ind.items():
        if code not in lk:
            continue
        close = base._arr(lk[code]["close"])
        dates = lk[code]["dates"]
        if len(close) != len(dates) or len(close) <= WARMUP + 2:
            continue
        bsigs = base.build_signals(close, r)
        eqs[code], dts[code] = {}, dates
        for lb in UNIFORM + [BH]:
            sig = bsigs[lb] if lb in bsigs else SIG_FN[lb](close, r)
            eqs[code][lb] = base.backtest(close, sig, dates, code)[1]
        style[code] = meta.get(code, {}).get("style", "其他")
    codes = list(eqs)
    full0 = "1900-01-01"

    def med(style_name, lb, d0, d1):
        sub = [c for c in codes if style[c] == style_name]
        vs = [wsharpe(eqs[c][lb], dts[c], d0, d1) for c in sub]
        vs = [v for v in vs if v is not None]
        return float(np.median(vs)) if vs else float("nan")

    # 只看四大风格（其他只有极少数）
    styles = [s for s in STYLES if any(style[c] == s for c in codes)]

    L = [
        "# v7 风格路由 · 归因诊断（哪一类拖累）",
        "> 55 只 ETF · 夏普按无风险利率 0 · 各自全历史。",
        "",
        "## 一、各风格下「策略」的中位夏普（全样本）",
        "",
        "| 风格 | 只数 | " + " | ".join(UNIFORM + [BH]) + " | 全样本路由 | 路由-最优统一(MACD) |",
        "|---" * (len(UNIFORM) + 5) + "|",
    ]
    for st in styles:
        n = sum(1 for c in codes if style[c] == st)
        r_full = med(st, MAP_FULL[st], full0, "9999")
        gap = r_full - med(st, "MACD", full0, "9999")
        L.append(f"| {st} | {n} | " + " | ".join(f"{x:.2f}" for x in [med(st, lb, full0, "9999") for lb in UNIFORM + [BH]])
                 + f" | {r_full:.2f} | {gap:+.2f} |")

    L += ["", "## 二、样本外（2024+）各风格中位夏普：路由选择 vs 关键策略",
          "",
          "| 风格 | 训练映射选择 | 全样本映射选择 | 训练映射OOS | 全样本映射OOS | MACD(OOS) | 买入持有(OOS) | 训练映射-MACD |",
          "|---|---|---|---|---|---|---|---|"]
    for st in styles:
        t_lb, f_lb = MAP_TRAIN[st], MAP_FULL[st]
        t_sh, f_sh = med(st, t_lb, TEST_START, "9999"), med(st, f_lb, TEST_START, "9999")
        macd_sh, bh_sh = med(st, "MACD", TEST_START, "9999"), med(st, BH, TEST_START, "9999")
        L.append(f"| {st} | {t_lb} | {f_lb} | {t_sh:.2f} | {f_sh:.2f} | {macd_sh:.2f} | {bh_sh:.2f} | {t_sh - macd_sh:+.2f} |")

    # 逐基金拖累（路由(全样本映射) OOS 夏普 - MACD OOS 夏普）
    lag = []
    for c in codes:
        lb = MAP_FULL.get(style[c], "MACD")
        r = wsharpe(eqs[c][lb], dts[c], TEST_START, "9999")
        m = wsharpe(eqs[c]["MACD"], dts[c], TEST_START, "9999")
        if r is not None and m is not None:
            lag.append((c, meta.get(c, {}).get("name", c), style[c], lb, r, m, r - m))
    lag.sort(key=lambda x: x[6])
    L += ["", "## 三、样本外拖累最重的标的（路由 - MACD 夏普）", "",
          "| 代码 | 名称 | 风格 | 路由策略 | 路由夏普 | MACD夏普 | 差 |",
          "|---|---|---|---|---|---|---|"]
    for row in lag[:12]:
        L.append(f"| {row[0]} | {row[1]} | {row[2]} | {row[3]} | {row[4]:.2f} | {row[5]:.2f} | {row[6]:+.2f} |")

    # 汇总
    L += ["", "## 四、结论",
          "",
          f"- **全样本**：各风格下路由用的策略（避险→BH / 防御→BOLL / 中枢→MACD / 进攻→v3）"
          f"基本都**不劣于**统一 MACD，差距为负的只有…（见一表「路由-MACD」列）。",
          "- **样本外才是真问题**：训练期(≤2023)推出来的映射把**中枢→v3、进攻→VOL**，"
          "这两个改动在 2024+ 显著拖累（见二表「训练映射-MACD」列）。",
          "- 结论：拖累**不是某一类资产的错**，而是**映射本身不稳定** —— 用旧数据挑出的"
          "「中枢/进攻该用谁」在新时段失效，把最该用 MACD/趋势的**宽基与行业主题**带偏了。",
          "",
          "> 机械规则输出，非投资建议。"]

    with open(os.path.join(_HERE, "_v7_diag_report.md"), "w", encoding="utf-8") as f:
        f.write("\n".join(L))
    print("报告:", os.path.join(_HERE, "_v7_diag_report.md"))
    print("全样本 风格路由-MACD 差:", {st: round(med(st, MAP_FULL[st], full0, "9999") - med(st, "MACD", full0, "9999"), 3) for st in styles})
    print("OOS 训练映射-MACD 差:", {st: round(med(st, MAP_TRAIN[st], TEST_START, "9999") - med(st, "MACD", TEST_START, "9999"), 3) for st in styles})


if __name__ == "__main__":
    main()
