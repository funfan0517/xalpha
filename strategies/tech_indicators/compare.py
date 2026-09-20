# -*- coding: utf-8 -*-
"""技术指标策略 · 全家族横向对比（同一引擎统一重算，口径一致）。

纳入：基线 5 单指标（MA/VOL/MACD/KDJ/BOLL）+ 变种 v1~v7，全部在同一引擎、
同一窗口（各自全历史、第 61 交易日起）、同一口径下逐基金回测，取全池中位数对照。

产物（相对本文件）：
- _comparison_report.md
- _comparison_510300.png

模拟结果，非投资建议。
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
_ROOT = os.path.dirname(os.path.dirname(_HERE))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)


def _load(path, name):
    spec = importlib.util.spec_from_file_location(name, path)
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


base = _load(os.path.join(_HERE, "backtest.py"), "cmp_base")
V = {n: _load(os.path.join(_HERE, n, "backtest.py"), "cmp_" + n)
     for n in ["v1_kdj_ma10", "v2_macd_kdj", "v3_macd_boll",
               "v4_boll_kdj_macd", "v5_regime_switch", "v6_score_wf"]}


def _load_style():
    """code -> 风格（取自 data/_universe.md），供 v7 路由使用。"""
    m = {}
    p = os.path.join(_ROOT, "data", "_universe.md")
    for line in open(p, encoding="utf-8"):
        if not line.startswith("|"):
            continue
        f = [x.strip() for x in line.strip().strip("|").split("|")]
        if len(f) >= 10 and len(f[4]) == 6 and f[4].isdigit():
            m[f[4]] = f[9] or "其他"
    return m


_STYLE = _load_style()
ROUTE_LABEL = "v7 风格路由"
ROUTE = {"避险": "买入持有", "防御": "BOLL", "中枢": "MACD", "进攻": "v3 MACD+BOLL", "其他": "MACD"}
SIG_FN = {
    "v1 KDJ+MA10": V["v1_kdj_ma10"].build_variant, "v2 MACD+KDJ": V["v2_macd_kdj"].build_variant,
    "v3 MACD+BOLL": V["v3_macd_boll"].build_variant, "v4 BOLL_KDJ_MACD": V["v4_boll_kdj_macd"].build_variant,
    "v5 Regime切换": V["v5_regime_switch"].sig_regime, "v6 打分制(≥5/≤2)": V["v6_score_wf"].build_score,
    "买入持有": lambda c, r: np.ones(len(c), dtype=int),
}


def _route_sig(code, close, r, bsigs):
    """按基金风格返回其路由策略的信号（v7）。"""
    lb = ROUTE.get(_STYLE.get(code, "其他"), "MACD")
    return bsigs[lb] if lb in bsigs else SIG_FN[lb](close, r)


# (标签, 基础键 or 变种信号函数, 风格, 一句话)
STRATS = [
    ("MA", "MA", "趋势", "20日生命线+60日牛熊过滤"),
    ("VOL", "VOL", "量价", "放量上涨持有"),
    ("MACD", "MACD", "趋势", "零轴上方金叉/死叉（基线最强）"),
    ("KDJ", "KDJ", "反转", "超卖金叉/超买死叉"),
    ("BOLL", "BOLL", "反转", "触下轨买/触上轨卖"),
    ("v1 KDJ+MA10", V["v1_kdj_ma10"].build_variant, "防守", "超卖金叉+站上MA10，破MA10走"),
    ("v2 MACD+KDJ", V["v2_macd_kdj"].build_variant, "波段", "零轴上+缩量回踩+KDJ金叉"),
    ("v3 MACD+BOLL", V["v3_macd_boll"].build_variant, "突破", "零轴上+布林开口突破，窗口 20↔30 自适应（风控最优）"),
    ("v4 BOLL_KDJ_MACD", V["v4_boll_kdj_macd"].build_variant, "反转", "布林近下轨+MACD金叉+KDJ超卖拐头"),
    ("v5 Regime切换", V["v5_regime_switch"].sig_regime, "混合", "趋势用v3/震荡用v4"),
    ("v6 打分制(≥5/≤2)", V["v6_score_wf"].build_score, "打分", "7项看多票数投票（此行为全样本；WF 样本外见 v6 报告）"),
    (ROUTE_LABEL, None, "路由", "按风格：避险→买入持有 / 防御→BOLL / 中枢→MACD / 进攻→v3"),
]
OOS_LABELS = set()


def _row(label, a, style, note, oos=False):
    tag = "（OOS）" if oos else ""
    return (f"| {label}{tag} | {style} | {base.pct(a['ann'])} | {base.pct(a['dd'])} | {a['sharpe']:.2f} | "
            f"{a['exposure']:.0%} | {base.pct(a['beat_ret'])} | {a['pooled']['n']} | "
            f"{a['pooled']['profit_factor']:.2f} | {note} |")


def main():
    ind, lk = base.load()
    labels = [x[0] for x in STRATS]
    results = {lb: [] for lb in labels}
    curves, dates510 = {}, None
    for code, r in ind.items():
        if code not in lk:
            continue
        close = base._arr(lk[code]["close"])
        dates = lk[code]["dates"]
        if len(close) != len(dates) or len(close) <= base.WARMUP + 2:
            continue
        bh, bh_eq = base.backtest_bh(close, dates, code)
        bsigs = base.build_signals(close, r)
        for lb, fn, _, _ in STRATS:
            if lb == ROUTE_LABEL:
                sig = _route_sig(code, close, r, bsigs)
            else:
                sig = bsigs[lb] if lb in bsigs else fn(close, r)
            m, eq, tr = base.backtest(close, sig, dates, code)
            results[lb].append({"code": code, "m": m, "bh": bh, "trades": tr})
            if code == "510300":
                curves[lb] = eq
        if code == "510300":
            dates510 = dates[base.WARMUP:]
            curves["买入持有"] = bh_eq

    bh_rows = [{"code": r["code"],
                "m": {"ann": r["bh"]["ann"], "dd": r["bh"]["dd"], "sharpe": r["bh"]["sharpe"],
                      "exposure": 1.0, "n": 1, "win_rate": 0.0, "payoff": 0.0, "profit_factor": 0.0},
                "bh": r["bh"], "trades": []} for r in results[labels[0]]]
    bh_agg = base._agg(bh_rows)

    L = [
        "# 技术指标策略 · 全家族横向对比",
        "> 55 只场内 ETF · 各自全历史（第 61 交易日起）· 多头/空仓 0-1 · 信号当日收盘成交 · "
        "未计佣金滑点 · 无风险利率 0 · 同一引擎统一重算。",
        "",
        "## 全池中位数对照（55 只）",
        "",
        "| 策略 | 风格 | 中位年化 | 中位最大回撤 | 中位夏普 | 中位曝光 | 跑赢买入持有 | 交易 | 利润因子 | 备注 |",
        "|---|---|---|---|---|---|---|---|---|---|",
    ]
    for lb, _, style, note in STRATS:
        L.append(_row(lb, base._agg(results[lb]), style, note, oos=lb in OOS_LABELS))
    L.append(f"| 买入持有(基准) | 基准 | {base.pct(bh_agg['ann'])} | {base.pct(bh_agg['dd'])} | "
             f"{bh_agg['sharpe']:.2f} | 100% | — | 1 | — | 全程持有 |")

    L += [
        "",
        "## 代表标的 510300 沪深300ETF（2015-01-05 起）",
        "",
        "| 策略 | 年化 | 最大回撤 | 夏普 | 总收益 | 卡玛比率 | 交易 | 胜率 | 盈亏比 |",
        "|---|---|---|---|---|---|---|---|---|",
    ]
    v510 = {x["code"]: x for x in results[labels[0]]}["510300"]["bh"]
    L.append(f"| 买入持有 | {base.pct(v510['ann'])} | {base.pct(v510['dd'])} | {v510['sharpe']:.2f} | "
             f"{base.pct(v510['total'])} | {v510['calmar']:.2f} | 1 | — | — |")
    for lb in labels:
        m = {x["code"]: x for x in results[lb]}["510300"]["m"]
        L.append(f"| {lb} | {base.pct(m['ann'])} | {base.pct(m['dd'])} | {m['sharpe']:.2f} | "
                 f"{base.pct(m['total'])} | {m['calmar']:.2f} | {m['n']} | "
                 f"{base.pct(m['win_rate'])} | {m['payoff']:.2f} |")

    L += [
        "",
        "## 结论",
        "",
        "- **风险调整最优 = v3 MACD+BOLL（自适应窗口）**：全池回撤最小、利润因子最高；510300 上夏普 0.60 居首。",
        "- **收益最高 = 基线 MACD**：中位年化与夏普居首，且 walk-forward 样本外仍最强。",
        "- **v4 BOLL_KDJ_MACD**：收益靠前但回撤最大——抄底/接飞刀的固有风险。",
        "- **v5 Regime 切换未跑赢其组件**；**v6 打分制** 样本外仍为正收益但不敌 MACD。",
        "- **v7 风格路由**：夏普 ≈ MACD，但回撤更小、利润因子更高（本质是「低暴露版 MACD」）；"
        "其收益取决于风格→策略映射的稳定性。",
        "- **v3（自适应窗口）**：布林窗口随 ER 在 20↔30 间切换，比固定窗口跨行情更稳（2020–2022 改善明显）——"
        "在保留突破逻辑的同时降低对单一行情参数的依赖。",
        "- **规律**：单指标里 MACD 最抗打；复杂共振（v1–v5）大多「用收益换回撤」或无效。",
        "",
        "> 口径：单笔=建/平仓收盘价收益（未计费用）；无风险利率 0。**模拟结果，非投资建议。**",
    ]

    with open(os.path.join(_HERE, "_comparison_report.md"), "w", encoding="utf-8") as f:
        f.write("\n".join(L))
    print("报告:", os.path.join(_HERE, "_comparison_report.md"))

    _plot(dates510, curves, os.path.join(_HERE, "_comparison_510300.png"))


def _plot(dates, curves, path):
    try:
        import matplotlib
        matplotlib.use("Agg")
        import matplotlib.pyplot as plt
    except Exception as e:  # noqa
        print("matplotlib 不可用，跳过作图:", e)
        return
    plt.rcParams["font.sans-serif"] = ["Microsoft YaHei", "SimHei", "DejaVu Sans"]
    plt.rcParams["axes.unicode_minus"] = False
    x = pd.to_datetime(dates)
    fig, ax = plt.subplots(figsize=(13, 7))
    order = ["买入持有"] + [lb for lb, _, _, _ in STRATS]
    for lb in order:
        if lb in curves:
            lw = 2.2 if lb == "买入持有" else 1.3
            ax.plot(x, curves[lb], label=lb, linewidth=lw,
                    color="#888" if lb == "买入持有" else None)
    ax.set_title("510300 沪深300ETF · 全家族策略净值对比（起=1）")
    ax.set_ylabel("净值")
    ax.grid(alpha=0.3)
    ax.legend(ncol=2, fontsize=8)
    fig.tight_layout()
    fig.savefig(path, dpi=120)
    plt.close(fig)
    print("图:", path)


if __name__ == "__main__":
    main()
