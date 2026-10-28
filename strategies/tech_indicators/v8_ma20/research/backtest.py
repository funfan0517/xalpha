# -*- coding: utf-8 -*-
"""MA20 穿越策略 · 标的池逐只单独汇报与单独回测（本策略的专项分析）。

覆盖标的池全部标的（源自 `v8_ma20/backtest.py::POOL`，当前 4 只）：
科创50 588000 · 恒生科技 513180 · 中证A500 563360 · 黄金 518880。

每只标的**单独回测**（各自全历史、第 61 个交易日起），并与买入持有逐项对照：
年化 / 波动 / 夏普 / 最大回撤 / 卡玛 / 总收益 / 交易次数 / 胜率 / 盈亏比 / 持仓占比；
另附逐年收益对照、最近 8 笔交易、当前仓位状态，以及每只标的独立的净值+回撤图。

规则：收盘上穿 MA20 买入 · 收盘下穿 MA20 卖出（0/1，无 MA60 过滤）。

产物（相对本文件 · 本目录即「7 只重点标的」的专项报告目录）：
- research/_v8_focus_report.md
- research/_v8_focus_bt.jsonl
- research/_v8_focus_<code>.png（每只一张）
- research/_v8_focus_all.png（7 只净值一览）

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
_V8DIR = os.path.dirname(_HERE)
_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(_V8DIR)))

_spec = importlib.util.spec_from_file_location("ti_v8_focus", os.path.join(_V8DIR, "backtest.py"))
V8 = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(V8)
base = V8.base

OUTDIR = _HERE
LABEL = V8.LABEL
WARMUP = V8.WARMUP
RECENT_TRADES = 8

# 标的池与主题：权威源 `v8_ma20/backtest.py::POOL`；名称 / 风格优先取 data/_universe.md，
# 下面是 meta 缺失时的风格兜底档。
_STYLE_FALLBACK = {"588000": "进攻", "513180": "进攻", "563360": "中枢", "518880": "避险"}
FOCUS = [(code, theme, _STYLE_FALLBACK.get(code, "其他")) for code, theme in V8.POOL.items()]


def yearly(eq, dates):
    """按自然年切分的年度收益（首个年份从窗口起点起算）。"""
    eq = np.asarray(eq, dtype=float)
    seg = {}
    for i, d in enumerate(dates):
        y = d[:4]
        st = 1.0 if i == 0 else eq[i - 1]
        if y not in seg:
            seg[y] = [st, eq[i]]
        seg[y][1] = eq[i]
    return {y: v[1] / v[0] - 1 for y, v in seg.items()}


def _m_table(m, bh):
    rows = [
        ("年化收益", base.pct(m["ann"]), base.pct(bh["ann"])),
        ("年化波动", base.pct(m["vol"], signed=False), base.pct(bh["vol"], signed=False)),
        ("夏普比率", f"{m['sharpe']:.2f}", f"{bh['sharpe']:.2f}"),
        ("最大回撤", base.pct(m["dd"]), base.pct(bh["dd"])),
        ("卡玛比率", f"{m['calmar']:.2f}", f"{bh['calmar']:.2f}"),
        ("总收益", base.pct(m["total"]), base.pct(bh["total"])),
        ("交易笔数", f"{m['n']}", "1（买入持有）"),
        ("胜率", f"{m['win_rate'] * 100:.2f}%" if m["n"] else "—", "—"),
        ("盈亏比", f"{m['payoff']:.2f}" if m["n"] else "—", "—"),
        ("利润因子", f"{m['profit_factor']:.2f}" if m["n"] else "—", "—"),
        ("持仓占比", f"{m['exposure']:.0%}", "100%"),
    ]
    L = [f"| 指标 | {LABEL} | 买入持有 |", "|---|---|---|"]
    L += [f"| {a} | {b} | {c} |" for a, b, c in rows]
    return L


def _plot_one(code, name, dates, eq, bh_eq, path):
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
    eq, bh_eq = np.asarray(eq, float), np.asarray(bh_eq, float)
    fig, (ax1, ax2) = plt.subplots(2, 1, figsize=(12, 7), sharex=True,
                                   gridspec_kw={"height_ratios": [3, 1]})
    ax1.plot(x, bh_eq, label="买入持有", color="#888", linewidth=1.3)
    ax1.plot(x, eq, label=LABEL, color="#c2185b", linewidth=1.5)
    ax1.set_title(f"{code} {name} · MA20 穿越 vs 买入持有（净值, 起=1）")
    ax1.set_ylabel("净值")
    ax1.grid(alpha=0.3)
    ax1.legend()
    for v, c, lb in ((eq, "#c2185b", LABEL), (bh_eq, "#888", "买入持有")):
        ax2.plot(x, v / np.maximum.accumulate(v) - 1, color=c, linewidth=1.1, label=lb)
    ax2.set_ylabel("回撤")
    ax2.grid(alpha=0.3)
    fig.tight_layout()
    fig.savefig(path, dpi=120)
    plt.close(fig)
    print("图:", path)


def _split_line(out):
    """按「买入持有年化是否为正」分组，说明择时在哪类资产上有效（池内一般只有 2~3 只）。"""
    lose = [x for x in out if x["bh_ann"] < 0]
    win = [x for x in out if x["bh_ann"] >= 0]
    parts = []
    if lose:
        parts.append("买入持有为负的 {} 只（{}）择时{}（{}）".format(
            len(lose), "、".join(x["theme"] for x in lose),
            "全部翻正" if all(x["edge_pp"] > 0 for x in lose) else "部分翻正",
            "、".join("{} {:+.2f}pp".format(x["theme"], x["edge_pp"]) for x in lose)))
    if win:
        parts.append("买入持有为正的 {} 只（{}）择时{}（{}）".format(
            len(win), "、".join(x["theme"] for x in win),
            "普遍少赚" if all(x["edge_pp"] < 0 for x in win) else "有增有减",
            "、".join("{} {:+.2f}pp".format(x["theme"], x["edge_pp"]) for x in win)))
    return "- " + "；".join(parts) + "。" if parts else "-（无样本）"


def _plot_all(curves, path):
    try:
        import matplotlib
        matplotlib.use("Agg")
        import matplotlib.pyplot as plt
    except Exception as e:  # noqa
        print("matplotlib 不可用，跳过作图:", e)
        return
    plt.rcParams["font.sans-serif"] = ["Microsoft YaHei", "SimHei", "DejaVu Sans"]
    plt.rcParams["axes.unicode_minus"] = False
    fig, ax = plt.subplots(figsize=(12, 6))
    for code, (theme, dates, eq) in curves.items():
        ax.plot(pd.to_datetime(dates), eq, label=f"{theme} {code}", linewidth=1.4)
    ax.set_title(f"{len(curves)} 只标的 · {LABEL} 净值一览（各自起=1，起点不同）")
    ax.set_ylabel("净值")
    ax.grid(alpha=0.3)
    ax.legend(fontsize=8, ncol=2)
    fig.tight_layout()
    fig.savefig(path, dpi=120)
    plt.close(fig)
    print("图:", path)


def main():
    ind, lk = base.load()
    meta = V8.load_meta()
    out, sections, curves = [], [], {}
    run_rows = []

    for code, theme, style0 in FOCUS:
        if code not in ind or code not in lk:
            print(f"[缺失] {code} 不在行情库，跳过")
            continue
        dates, close, r = V8.aligned(code, ind, lk)
        if len(close) <= WARMUP + 2:
            print(f"[跳过] {code}: 样本不足（{len(close)} 根）")
            continue
        name, style = meta.get(code, (code, style0))
        sig = V8.build_signal(close, r)
        m, eq, trades = base.backtest(close, sig, dates, code)
        bh, bh_eq = base.backtest_bh(close, dates, code)
        d = dates[WARMUP:]
        ma = base._arr(r["MA20"])
        years = max((pd.Timestamp(dates[-1]) - pd.Timestamp(d[0])).days / 365.0, 1e-9)

        out.append({
            "code": code, "theme": theme, "name": name, "style": style,
            "start": d[0], "end": dates[-1], "years": round(years, 2), "bars": len(d),
            "ann": round(m["ann"], 4), "dd": round(m["dd"], 4), "sharpe": round(m["sharpe"], 3),
            "calmar": round(m["calmar"], 3), "total": round(m["total"], 4),
            "bh_ann": round(bh["ann"], 4), "bh_dd": round(bh["dd"], 4),
            "bh_sharpe": round(bh["sharpe"], 3), "bh_total": round(bh["total"], 4),
            "edge_pp": round((m["ann"] - bh["ann"]) * 100, 2),
            "trades": m["n"], "win_rate": round(m["win_rate"], 4),
            "payoff": round(m["payoff"], 3), "exposure": round(m["exposure"], 3),
            "pos_now": int(sig[-1]), "close": float(close[-1]), "ma20": float(ma[-1]),
            "trade_log": trades,
        })

        # ---- 单标的章节 ----
        y_s, y_b = yearly(eq, d), yearly(bh_eq, d)
        sections += [
            "",
            f"### {theme} · {code} {name}（{style}）",
            "",
            f"样本：**{d[0]} ~ {dates[-1]}** · {len(d)} 个交易日 · 约 {years:.2f} 年"
            f"（第 {WARMUP + 1} 个交易日起，与回测口径一致）。",
            "",
        ]
        sections += _m_table(m, bh)
        sections += ["", "逐年收益（首尾年份为不完整年度）：", "",
                     "| 年份 | MA20穿越 | 买入持有 | 差 |", "|---|---|---|---|"]
        for y in sorted(y_s):
            sections.append(f"| {y} | {base.pct(y_s[y])} | {base.pct(y_b.get(y, 0.0))} | "
                            f"{(y_s[y] - y_b.get(y, 0.0)) * 100:+.2f}pp |")
        sections += ["", f"最近 {RECENT_TRADES} 笔交易：", ""]
        if trades:
            sections += ["| 买入日 | 卖出日 | 持仓交易日 | 单笔收益 |", "|---|---|---|---|"]
            for t in trades[-RECENT_TRADES:]:
                sections.append(f"| {t['entry_date']} | {t['exit_date']} | {t['bars']} | "
                                f"{base.pct(t['ret'])} |")
        else:
            sections.append("（样本期内无完整交易）")
        sections += [
            "",
            f"当前状态：收盘 **{close[-1]:.3f}** · MA20 **{ma[-1]:.3f}** · "
            f"距 MA20 **{close[-1] / ma[-1] - 1:+.2%}** · 仓位 **{'持仓' if sig[-1] else '空仓'}**。",
        ]

        png = os.path.join(OUTDIR, f"_v8_focus_{code}.png")
        _plot_one(code, name, d, eq, bh_eq, png)
        curves[code] = (theme, d, eq)
        run_rows.append((theme, code, name, style, m, bh, years, len(d)))

    os.makedirs(OUTDIR, exist_ok=True)
    with open(os.path.join(OUTDIR, "_v8_focus_bt.jsonl"), "w", encoding="utf-8") as f:
        for x in out:
            f.write(json.dumps(x, ensure_ascii=False) + "\n")

    n_beat = sum(1 for x in out if x["edge_pp"] > 0)
    n_dd = sum(1 for x in out if abs(x["dd"]) < abs(x["bh_dd"]))

    L = [
        "# MA20 穿越策略 · 标的池单独回测与汇报",
        f"> {len(out)} 只标的（{' / '.join(x['theme'] for x in out)}）· 各自全历史"
        f"（第 {WARMUP + 1} 个交易日起）· 0/1 多头/空仓 · 信号当日收盘成交 · 未计佣金滑点 · "
        "无风险利率 0 · 年化 252 交易日",
        f"> ⚠️ 各标的上市时间不同（样本 {min(x['years'] for x in out):.1f}~"
        f"{max(x['years'] for x in out):.1f} 年）：只比「各自区间内策略 vs 买入持有」，"
        "不是同窗口横向排名。",
        "",
        "## 规则",
        "",
        f"- **买入**：{V8.BUY_DESC}",
        f"- **卖出**：{V8.SELL_DESC}",
        "- 其余时间维持原仓位；无 MA60 牛熊过滤、无止损止盈、无加减仓。",
        "",
        "## 汇总（按逐只年化差降序）",
        "",
        "| 主题 | 代码 | 名称 | 风格 | 样本区间 | 年数 | 择时年化 | 买入持有年化 | 年化差 | "
        "择时回撤 | 买入持有回撤 | 择时夏普 | 卡玛 | 交易 | 胜率 | 持仓占比 | 当前 |",
        "|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|",
    ]
    for x in sorted(out, key=lambda z: z["edge_pp"], reverse=True):
        L.append(f"| {x['theme']} | {x['code']} | {x['name']} | {x['style']} | "
                 f"{x['start']} ~ {x['end']} | {x['years']:.2f} | {base.pct(x['ann'])} | "
                 f"{base.pct(x['bh_ann'])} | {x['edge_pp']:+.2f}pp | {base.pct(x['dd'])} | "
                 f"{base.pct(x['bh_dd'])} | {x['sharpe']:.2f} | {x['calmar']:.2f} | "
                 f"{x['trades']} | {x['win_rate']:.0%} | {x['exposure']:.0%} | "
                 f"{'持仓' if x['pos_now'] else '空仓'} |")

    L += [
        "",
        f"> {len(out)} 只中：年化跑赢买入持有 **{n_beat}/{len(out)}** 只，回撤更小 **{n_dd}/{len(out)}** 只。",
        "",
        "## 逐标的单独回测",
    ]
    L += sections

    L += [
        "",
        "## 结论",
        "",
    ]
    if out:
        best = max(out, key=lambda z: z["edge_pp"])
        worst = min(out, key=lambda z: z["edge_pp"])
        per_year = float(np.median([x["trades"] / max(x["years"], 1e-9) for x in out]))
        cost_pp = per_year * 2 * 0.03
        L += [
            f"- **最好**：{best['theme']}（{best['code']}）年化差 **{best['edge_pp']:+.2f}pp**"
            f"（{base.pct(best['ann'])} vs 买入持有 {base.pct(best['bh_ann'])}），"
            f"回撤 {base.pct(best['dd'])} vs {base.pct(best['bh_dd'])}。",
            f"- **最差**：{worst['theme']}（{worst['code']}）年化差 **{worst['edge_pp']:+.2f}pp**"
            f"（{base.pct(worst['ann'])} vs 买入持有 {base.pct(worst['bh_ann'])}），"
            f"回撤 {base.pct(worst['dd'])} vs {base.pct(worst['bh_dd'])}。",
            _split_line(out),
            "- **规律**：MA20 穿越是「降回撤」工具而非「增收益」工具 —— 高波动权益的回撤改善最明显，"
            "单边上涨的资产则只是「少赚」（每次跌破 MA20 下车都会切掉一段上涨），属策略固有属性。",
            f"- 横向看：{len(out)} 只的择时年化中位 "
            f"{base.pct(float(np.median([x['ann'] for x in out])))}，买入持有中位 "
            f"{base.pct(float(np.median([x['bh_ann'] for x in out])))}。",
            f"- **成本极敏感**：{len(out)} 只平均每年 {per_year:.1f} 笔、往返按双边计，单边 0.03% 佣金即约 "
            f"-{cost_pp:.2f}pp/年 —— 上面所有数字均**未扣成本**，扣完后多数标的的超额会被吃掉。",
        ]
    L += ["", "> **模拟结果，非投资建议。**"]

    with open(os.path.join(OUTDIR, "_v8_focus_report.md"), "w", encoding="utf-8") as f:
        f.write("\n".join(L))

    if curves:
        _plot_all(curves, os.path.join(OUTDIR, "_v8_focus_all.png"))
    print("报告:", os.path.join(OUTDIR, "_v8_focus_report.md"))
    print("结果:", os.path.join(OUTDIR, "_v8_focus_bt.jsonl"))
    for theme, code, name, style, m, bh, years, bars in sorted(
            run_rows, key=lambda z: z[4]["ann"] - z[5]["ann"], reverse=True):
        print(f"  {theme:12s} {code} 年化 {base.pct(m['ann']):>8s} vs BH {base.pct(bh['ann']):>8s} · "
              f"回撤 {base.pct(m['dd']):>8s} vs {base.pct(bh['dd']):>8s} · {m['n']} 笔")


if __name__ == "__main__":
    main()
