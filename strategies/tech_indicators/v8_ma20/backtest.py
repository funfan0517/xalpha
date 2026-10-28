# -*- coding: utf-8 -*-
"""技术指标策略 · 变种 8：MA20 单均线穿越 · 标的池回测。

标的池（`POOL`，权威源，当前 4 只）：科创50 588000 / 恒生科技 513180 / 中证A500 563360 / 黄金 518880
—— 2026-10-28 由全池 55 只收窄，`daily/_daily_scan.py` 与 `research/backtest.py` 均由此派生。

规则（严格按口径，**不叠加 MA60 牛熊过滤**）：
- **买入**：收盘价上穿 20 日均线（今收 > 今日 MA20 且 昨收 <= 昨日 MA20）；
- **卖出**：收盘价下穿 20 日均线（今收 < 今日 MA20 且 昨收 >= 昨日 MA20）；
- 其余时间维持原仓位（多头 / 空仓 0-1，不做金字塔加减仓）。

与基线 `MA` 策略的唯一差别：基线要求「上穿 MA20 **且** 价 > MA60」，本变种按口径
**去掉 MA60 过滤** —— 因此信号更多、曝光更高、震荡市被打脸次数也更多，报告如实对比。

口径：
- 各自全历史（第 61 个交易日 = `base.WARMUP` 起，与基线同窗口可比；MA20 本身只需 20 根）；
- 信号当日收盘成交，收盘价对收盘价计收益；信号仅用当日及之前数据（无未来函数）；
- 未计佣金 / 滑点；无风险利率 0；年化按 252 交易日；
- 单笔统计统一走 `pipeline/bt_stats.py`。

产物（相对本文件）：
- backtest/_v8_ma20_report.md
- backtest/_v8_ma20_results.jsonl
- backtest/_v8_ma20_pool.png

模拟结果，非投资建议。
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

# 以独立命名载入基线引擎（只读复用，不修改基线文件）
_spec = importlib.util.spec_from_file_location("ti_base_v8", os.path.join(_PARENT, "backtest.py"))
base = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(base)

DATA = base.DATA
OUTDIR = os.path.join(_HERE, "backtest")
WARMUP = base.WARMUP
MA_WIN = 20
LABEL = "v8 MA20穿越"
BUY_DESC = f"收盘价上穿 MA{MA_WIN}（今收 > MA20 且 昨收 ≤ 昨日 MA20）"
SELL_DESC = f"收盘价下穿 MA{MA_WIN}（今收 < MA20 且 昨收 ≥ 昨日 MA20）"

# ★ 标的池（权威源）：2026-10-28 由全池 55 只收窄，后加入黄金 ETF，
#   `backtest.py` / `daily/_daily_scan.py` / `research/backtest.py` 全部由它派生，增删只改这一处。
POOL = {
    "588000": "科创50",
    "513180": "恒生科技",
    "563360": "中证A500",
    "518880": "黄金",
}


def load_meta():
    """代码 -> (场内名称, 风格)，源自 data/_universe.md（与每日流程同一份元数据）。"""
    m = {}
    p = os.path.join(DATA, "_universe.md")
    for line in open(p, encoding="utf-8"):
        if not line.startswith("|"):
            continue
        f = [x.strip() for x in line.strip().strip("|").split("|")]
        if len(f) >= 10 and len(f[4]) == 6 and f[4].isdigit():
            m[f[4]] = (f[5] or f[4], f[9] or "其他")
    return m


def aligned(code, ind, lk):
    """按日期对齐行情库与指标库；返回 (共同日期, 对齐后收盘价, 对齐后指标字典)。

    两个库理论上同步写入，但刷新失败 / 盘中兜底时可能出现长度差 —— 按日期取交集，
    避免把指标错位到别的交易日上（错位不会报错，只会静默污染信号）。
    """
    di = {d: i for i, d in enumerate(lk[code]["dates"])}
    ii = {d: i for i, d in enumerate(ind[code]["dates"])}
    common = [d for d in ind[code]["dates"] if d in di]
    close = base._arr([lk[code]["close"][di[d]] for d in common])
    r = {col: [vals[ii[d]] for d in common] for col, vals in ind[code].items() if col != "dates"}
    return common, close, r


def build_signal(close, r=None, ma_win=MA_WIN):
    """MA20 上穿买入 / 下穿卖出 -> 目标仓位序列（0/1，逐日因果）。

    缺 `r["MA20"]` 时按收盘价现算（供其他脚本只给 close 的场景复用）。
    """
    close = np.asarray(close, dtype=float)
    n = len(close)
    if r is not None and "MA20" in r:
        ma = base._arr(r["MA20"])
    else:
        ma = pd.Series(close).rolling(ma_win).mean().to_numpy()
    sig = np.zeros(n, dtype=int)
    p = 0
    for t in range(1, n):
        if np.isnan(ma[t]) or np.isnan(ma[t - 1]):
            sig[t] = p
            continue
        if p == 0 and close[t] > ma[t] and close[t - 1] <= ma[t - 1]:
            p = 1
        elif p == 1 and close[t] < ma[t] and close[t - 1] >= ma[t - 1]:
            p = 0
        sig[t] = p
    return sig


def build_variant(close, r):
    """变种统一签名（compare.py / daily 复用）。"""
    return build_signal(close, r)


def _row(label, a):
    return (f"| {label} | {base.pct(a['ann'])} | {base.pct(a['dd'])} | {a['sharpe']:.2f} | "
            f"{a['exposure']:.0%} | {a['beat_ret']:.0%} | {a['beat_dd']:.0%} | "
            f"{a['pooled']['n']} | {a['pooled']['win_rate']:.0%} |")


def _head():
    return ["| 策略 | 中位年化 | 中位最大回撤 | 中位夏普 | 中位曝光 | 跑赢买入持有 | 回撤更小 | 总交易 | 合计胜率 |",
            "|---|---|---|---|---|---|---|---|---|"]


def _plot_pool(curves, path):
    """每只标的一个子图：MA20 穿越 vs 买入持有（净值, 起=1）。"""
    try:
        import matplotlib
        matplotlib.use("Agg")
        import matplotlib.pyplot as plt
    except Exception as e:  # noqa
        print("matplotlib 不可用，跳过作图:", e)
        return
    plt.rcParams["font.sans-serif"] = ["Microsoft YaHei", "SimHei", "DejaVu Sans"]
    plt.rcParams["axes.unicode_minus"] = False
    n = len(curves)
    fig, axes = plt.subplots(n, 1, figsize=(12, 4.2 * n), sharex=False)
    if n == 1:
        axes = [axes]
    for ax, (code, theme, name, dates, eq, bh_eq) in zip(axes, curves):
        x = pd.to_datetime(dates)
        ax.plot(x, bh_eq, label="买入持有", color="#888", linewidth=1.2)
        ax.plot(x, eq, label=LABEL, color="#c2185b", linewidth=1.5)
        ax.set_title(f"{code} {name}（{theme}）· {LABEL} vs 买入持有（净值, 起=1）", fontsize=10)
        ax.set_ylabel("净值")
        ax.grid(alpha=0.3)
        ax.legend(fontsize=8)
    fig.tight_layout()
    fig.savefig(path, dpi=120)
    plt.close(fig)
    print("图:", path)


def main():
    ind, lk = base.load()
    meta = load_meta()
    rows, rows_ma, base_rows = [], [], {s: [] for s in base.STRAT}
    curves = []

    for code, theme in POOL.items():
        if code not in ind or code not in lk:
            print(f"[缺失] {code} 不在行情库，跳过")
            continue
        dates, close, r = aligned(code, ind, lk)
        if len(close) != len(dates) or len(close) <= WARMUP + 2:
            print(f"[跳过] {code}: 样本不足（{len(close)} 根）")
            continue
        bh, bh_eq = base.backtest_bh(close, dates, code)

        m, eq, trades = base.backtest(close, build_signal(close, r), dates, code)
        d = dates[WARMUP:]
        years = max((pd.Timestamp(dates[-1]) - pd.Timestamp(d[0])).days / 365.0, 1e-9)
        rows.append({"code": code, "theme": theme, "m": m, "bh": bh, "trades": trades,
                     "start": d[0], "end": dates[-1], "years": years, "bars": len(d)})

        bsigs = base.build_signals(close, r)
        m_ma, _eq_ma, tr_ma = base.backtest(close, bsigs["MA"], dates, code)
        rows_ma.append({"code": code, "m": m_ma, "bh": bh, "trades": tr_ma})
        for s in base.STRAT:
            m5, _eq5, tr5 = base.backtest(close, bsigs[s], dates, code)
            base_rows[s].append({"code": code, "m": m5, "bh": bh, "trades": tr5})

        name = meta.get(code, (code, "其他"))[0]
        curves.append((code, theme, name, d, eq, bh_eq))

    os.makedirs(OUTDIR, exist_ok=True)

    mmap = {x["code"]: x for x in rows_ma}
    with open(os.path.join(OUTDIR, "_v8_ma20_results.jsonl"), "w", encoding="utf-8") as f:
        for row in rows:
            code = row["code"]
            ma_row = mmap[code]["m"]
            f.write(json.dumps({
                "code": code, "name": meta.get(code, ("", ""))[0], "theme": row["theme"],
                "strategy": LABEL, "start": row["start"], "end": row["end"],
                "years": round(row["years"], 2), "bars": row["bars"],
                "ann": round(row["m"]["ann"], 4), "dd": round(row["m"]["dd"], 4),
                "sharpe": round(row["m"]["sharpe"], 3), "exposure": round(row["m"]["exposure"], 3),
                "n_trades": row["m"]["n"], "win_rate": round(row["m"]["win_rate"], 4),
                "ma_ann": round(ma_row["ann"], 4), "ma_dd": round(ma_row["dd"], 4),
                "bh_ann": round(row["bh"]["ann"], 4), "bh_dd": round(row["bh"]["dd"], 4),
            }, ensure_ascii=False) + "\n")

    a, a_ma = base._agg(rows), base._agg(rows_ma)

    L = [
        "# 技术指标策略 · 变种 8：MA20 单均线穿越 · 标的池回测",
        f"> 标的池 **{len(rows)} 只**（{' / '.join(row['theme'] for row in rows)}）· 各自全历史"
        f"（第 {WARMUP + 1} 个交易日起，与基线同窗口）· 多头/空仓 0-1 · 信号当日收盘成交 · "
        "未计佣金滑点 · 无风险利率 0",
        f"> ⚠️ 各标的上市时间不同（样本 {min(r['years'] for r in rows):.1f}~"
        f"{max(r['years'] for r in rows):.1f} 年），**跨标的只比「各自区间内策略 vs 买入持有」**，"
        "不是同窗口横向排名。",
        "",
        "## 策略规则",
        "",
        f"- **买入**：{BUY_DESC}",
        f"- **卖出**：{SELL_DESC}",
        "- **仓位**：信号为 0/1 目标仓位，不设加减仓、不设止损止盈；价格未穿线时维持原仓位。",
        "- **与基线 MA 的差别**：基线多一道「价 > MA60（牛熊线）」过滤，本变种按口径**去掉**——"
        "信号更密、曝光更高，代价是震荡市反复穿越（来回打脸）。",
        "",
        "## 标的池与样本",
        "",
        "| 代码 | 主题 | 名称 | 风格 | 样本区间 | 年数 | 交易日 |",
        "|---|---|---|---|---|---|---|",
    ]
    for x in rows:
        nm, st = meta.get(x["code"], (x["code"], "其他"))
        L.append(f"| {x['code']} | {x['theme']} | {nm} | {st} | {x['start']} ~ {x['end']} | "
                 f"{x['years']:.2f} | {x['bars']} |")

    L += [
        "",
        "## 逐标的对照（策略 vs 买入持有 · 按年化差降序）",
        "",
        "| 代码 | 主题 | 年化 | 最大回撤 | 夏普 | 卡玛 | 总收益 | 交易 | 胜率 | 盈亏比 | 持仓占比 | "
        "买入持有年化 | 买入持有回撤 | 年化差 |",
        "|---|---|---|---|---|---|---|---|---|---|---|---|---|---|",
    ]
    for x in sorted(rows, key=lambda z: z["m"]["ann"] - z["bh"]["ann"], reverse=True):
        L.append(f"| {x['code']} | {x['theme']} | {base.pct(x['m']['ann'])} | {base.pct(x['m']['dd'])} | "
                 f"{x['m']['sharpe']:.2f} | {x['m']['calmar']:.2f} | {base.pct(x['m']['total'])} | "
                 f"{x['m']['n']} | {base.pct(x['m']['win_rate'])} | {x['m']['payoff']:.2f} | "
                 f"{x['m']['exposure']:.0%} | {base.pct(x['bh']['ann'])} | {base.pct(x['bh']['dd'])} | "
                 f"{(x['m']['ann'] - x['bh']['ann']) * 100:+.2f}pp |")

    n_beat = sum(1 for x in rows if x["m"]["ann"] > x["bh"]["ann"])
    n_dd = sum(1 for x in rows if abs(x["m"]["dd"]) < abs(x["bh"]["dd"]))
    L += [
        "",
        f"> {len(rows)} 只中：年化跑赢买入持有 **{n_beat}/{len(rows)}** 只，回撤更小 "
        f"**{n_dd}/{len(rows)}** 只；逐只年化差中位 {_med_edge(rows):+.2f}pp。",
        "",
        f"## 同池基线对照（同样这 {len(rows)} 只 · 中位数）",
        "",
    ] + _head() + [
        _row(f"**{LABEL}（本变种）**", a),
        _row("MA（基线：MA20 + MA60 过滤）", a_ma),
    ] + [_row(s, base._agg(base_rows[s])) for s in base.STRAT if s != "MA"] + [
        f"| 买入持有(基准) | {base.pct(_bh_med(rows, 'ann'))} | {base.pct(_bh_med(rows, 'dd'))} | "
        f"{_bh_med(rows, 'sharpe'):.2f} | 100% | — | — | 1 | — |",
        "",
        f"> 基线 5 指标在同一 {len(rows)} 只、同一窗口上重跑，仅供同池对照；"
        "各标的样本长度不同，中位数只作参考，结论以上面的逐标的表为准。",
    ]

    pooled = base.trade_stats([t for x in rows for t in x["trades"]])
    L += base.section_lines(pooled, title=f"单笔交易统计（{len(rows)} 只合并 · {LABEL}）")
    L += ["", "> 口径：单笔=建/平仓收盘价收益（未计费用）；持仓占比=处于 1 仓的交易日比例；"
          "年化按 252 交易日、无风险利率 0。**模拟结果，非投资建议。**"]

    with open(os.path.join(OUTDIR, "_v8_ma20_report.md"), "w", encoding="utf-8") as f:
        f.write("\n".join(L))

    if curves:
        _plot_pool(curves, os.path.join(OUTDIR, "_v8_ma20_pool.png"))
    print("报告:", os.path.join(OUTDIR, "_v8_ma20_report.md"))
    print("结果:", os.path.join(OUTDIR, "_v8_ma20_results.jsonl"))
    print(f"{LABEL} 标的池 {len(rows)} 只 · 中位年化 {base.pct(a['ann'])} · "
          f"中位回撤 {base.pct(a['dd'])} · 中位曝光 {a['exposure']:.0%} · "
          f"交易 {a['pooled']['n']} 笔")


def _bh_med(rows, key):
    return float(np.median([x["bh"][key] for x in rows]))


def _med_edge(rows):
    return float(np.median([(x["m"]["ann"] - x["bh"]["ann"]) * 100 for x in rows]))


if __name__ == "__main__":
    main()
