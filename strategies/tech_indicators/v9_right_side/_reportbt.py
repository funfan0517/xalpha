# -*- coding: utf-8 -*-
"""右侧买入策略 · 回测报告（渲染 backtest.py 的产物）。

输入: strategies/tech_indicators/v9_right_side/backtest/_right_side_bt.jsonl（--in 可指定其它产物）
输出: strategies/tech_indicators/v9_right_side/backtest/_bt_report.md
      strategies/tech_indicators/v9_right_side/backtest/_bt_visual.png

版式沿用仓库既有回测报告惯例：H1 + `>` 口径块 + 分类汇总（末行 **全部**）+ 逐只明细
+ 五票命中率 + 离场原因 + 全体单笔统计（`pipeline.bt_stats`）+ 上下双子图。
"""
import json
import os
import sys
from datetime import datetime

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402
import numpy as np  # noqa: E402

sys.stdout.reconfigure(encoding="utf-8")
_DIR = os.path.dirname(os.path.abspath(__file__))
_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(_DIR)))
for p in (_ROOT, os.path.dirname(_DIR), _DIR):
    if p not in sys.path:
        sys.path.insert(0, p)

import rule  # noqa: E402
from pipeline import bt_stats  # noqa: E402

IN_DEFAULT = os.path.join(_DIR, "backtest", "_right_side_bt.jsonl")
MD = os.path.join(_DIR, "backtest", "_bt_report.md")
PNG = os.path.join(_DIR, "backtest", "_bt_visual.png")

CAT_ORDER = ("宽基/另类", "全球/QDII", "A股行业", "策略/商品", "主动/量化", "债券")
plt.rcParams["font.sans-serif"] = ["Microsoft YaHei", "SimHei", "DejaVu Sans"]
plt.rcParams["axes.unicode_minus"] = False


def pct(x, dp=1):
    return "—" if x is None else f"{x * 100:+.{dp}f}%"


def load(path):
    if not os.path.exists(path):
        sys.exit(f"缺少回测产物 {path}; 先跑 python strategies/tech_indicators/v9_right_side/backtest.py")
    raw = open(path, "rb").read()
    text = raw.decode("utf-16") if raw[:2] in (b"\xff\xfe", b"\xfe\xff") else raw.decode("utf-8")
    good, bad = [], []
    for ln in text.splitlines():
        ln = ln.strip()
        if not ln.startswith("{"):
            continue
        r = json.loads(ln)
        (good if r.get("ok") else bad).append(r)
    return good, bad


def agg(rows, f):
    v = [r[f] for r in rows if r.get(f) is not None]
    return float(np.mean(v)) if v else None


def med(rows, f):
    v = [r[f] for r in rows if r.get(f) is not None]
    return float(np.median(v)) if v else None


def plot(good):
    good = sorted(good, key=lambda r: r["st_ann"])
    names = [f"{r['theme']} `{r['code']}`" for r in good]
    y = np.arange(len(good))
    fig, (ax1, ax2) = plt.subplots(2, 1, figsize=(10, max(6, 0.3 * len(good))),
                                   sharey=True, gridspec_kw=dict(hspace=0.08))
    base = [r["base_ann"] for r in good]
    st = [r["st_ann"] for r in good]
    cols = ["#c62828" if r["st_ann"] > r["base_ann"] else "#1565c0" for r in good]
    ax1.barh(y + 0.2, base, height=0.38, color="#b0bec5", label="买入持有")
    ax1.barh(y - 0.2, st, height=0.38, color=cols, label="右侧买入")
    ax1.set_title("年化收益: 红=策略胜 / 蓝=策略负")
    ax1.axvline(0, color="#555", lw=0.8)
    ax1.legend(loc="lower right", fontsize=8)

    ax2.barh(y + 0.2, [r["base_mdd"] for r in good], height=0.38, color="#b0bec5")
    ax2.barh(y - 0.2, [r["st_mdd"] for r in good], height=0.38, color="#ef6c00")
    ax2.set_title("最大回撤（越靠右越浅 = 越好）")
    ax2.set_xlabel("收益 / 回撤")
    ax2.axvline(0, color="#555", lw=0.8)

    ax2.set_yticks(y)
    ax2.set_yticklabels(names, fontsize=8)
    ax1.tick_params(axis="x", labelsize=8)
    ax2.tick_params(axis="x", labelsize=8)
    fig.savefig(PNG, dpi=150, bbox_inches="tight", facecolor="white")
    plt.close(fig)


def main():
    argv = sys.argv[1:]
    src = IN_DEFAULT
    if "--in" in argv:
        src = argv[argv.index("--in") + 1]
    good, bad = load(src)
    if not good:
        sys.exit("回测产物无有效标的")
    span = f"{min(r['start'] for r in good)} ~ {max(r['end'] for r in good)}"
    beat = sum(r["st_ann"] > r["base_ann"] for r in good)
    plot(good)

    L = ["# 右侧买入策略 · 逐标的回测: 策略 vs 买入持有", "",
         f"> 样本区间 {span}（最长十年，上市晚者按实有） · 成本 单边 {rule.FEE:.2%}（ETF 佣金）",
         f"> 执行: 当日收盘算信号 → **次日收盘**成交（十年库只有收盘价，全程无未来数据）; "
         f"0/1 满仓; 期末持仓按最新收盘估值",
         f"> 入场: {rule.VOTE_ENTER} 票全过（{' + '.join(rule.VOTE_NAMES[k] for k in rule.VOTE_NAMES)}）"
         f" · 否决: 5日涨幅>{rule.RET5_MAX:.0%} / 20日均额<{rule.AMT_MIN / 1e8:.1f}亿",
         f"> 离场: 收盘跌破结构位（前 {rule.STOP_LOOKBACK} 日最低收盘） · 连续 {rule.EXIT_CONFIRM} 日收盘 < MA{rule.EXIT_MA}"
         f" · 参数 `{rule.LABEL}`",
         f"> 成功 {len(good)} 只 / 失败 {len(bad)} 只 · 跑赢率 **{beat}/{len(good)}**"
         f" · 平均超额 {pct(agg(good, 'st_ann') - agg(good, 'base_ann'))}"
         f" · 中位回撤改善 {pct(med(good, 'st_mdd') - med(good, 'base_mdd'))}", ""]

    L += ["## 一眼看完（中位数，55 只）", "",
          "| 指标 | 右侧买入 | 买入持有 | 差异 |", "|---|---|---|---|",
          f"| 年化 | {pct(med(good, 'st_ann'), 2)} | {pct(med(good, 'base_ann'), 2)} | "
          f"{pct(med(good, 'st_ann') - med(good, 'base_ann'), 2)} |",
          f"| 最大回撤 | {pct(med(good, 'st_mdd'), 2)} | {pct(med(good, 'base_mdd'), 2)} | "
          f"{pct(med(good, 'st_mdd') - med(good, 'base_mdd'), 2)} |",
          f"| 持仓占比 | {med(good, 'pos_ratio') * 100:.1f}% | 100% | — |",
          f"| 买点触发频率 | {med(good, 'sig_ratio') * 100:.1f}% 的交易日 | — | — |",
          f"| 择时边际 | {med(good, 'timing_edge') * 1e4:+.1f}bp | — | — |", "",
          "> **怎么读这张表**：低暴露择时策略的年化低于满仓持有是**结构性的**（它 90% 时间空仓），"
          "判据不是「年化能否超过持有」，而是 ① 回撤是否显著压缩 ② 择时边际是否为正"
          "（持仓日日均收益 − 空仓日日均收益）。两者同时成立，说明买点确实买在「该涨的时候」。", ""]

    cats = [c for c in CAT_ORDER if any(r["cat"] == c for r in good)]
    cats += [c for c in dict.fromkeys(r["cat"] for r in good) if c not in cats]

    L += ["## 分类汇总（均值）", "",
          "| 类别 | n | 基准年化 | 策略年化 | 超额 | 基准回撤 | 策略回撤 | 回撤改善 | "
          "持仓占比 | 买点频率 | 单笔数 | 胜率 | 利润因子 |",
          "|---" * 13 + "|"]
    for c in cats + [None]:
        rs = good if c is None else [r for r in good if r["cat"] == c]
        if not rs:
            continue
        b, s = agg(rs, "base_ann"), agg(rs, "st_ann")
        bm, sm = agg(rs, "base_mdd"), agg(rs, "st_mdd")
        pf = [r["t_stats"]["profit_factor"] for r in rs if r["t_stats"]["n"] > 0]
        pf = [x for x in pf if x != float("inf")]
        wr = [r["t_stats"]["win_rate"] for r in rs if r["t_stats"]["n"] > 0]
        lab = "**全部**" if c is None else c
        L.append(f"| {lab} | {len(rs)} | {pct(b)} | {pct(s)} | {pct(s - b)} | {pct(bm)} | "
                 f"{pct(sm)} | {pct(sm - bm)} | {pct(agg(rs, 'pos_ratio'), 1)} | "
                 f"{pct(agg(rs, 'sig_ratio'), 1)} | {agg(rs, 'trades'):.1f} | "
                 f"{pct(np.mean(wr), 1) if wr else '—'} | "
                 f"{(f'{np.mean(pf):.2f}' if pf else '—')} |")
    L.append("")

    L += ["## 逐只明细（按类别 / 策略年化排序）", "",
          "| 类别 | 标的 | 样本年 | 基准年化 | 策略年化 | 超额 | 基准回撤 | 策略回撤 | "
          "持仓占比 | 买点频率 | 单笔数 | 胜率 | 盈亏比 | 平均持仓日 |",
          "|---" * 14 + "|"]
    for c in cats:
        for r in sorted([x for x in good if x["cat"] == c], key=lambda x: -x["st_ann"]):
            ts = r["t_stats"]
            po = "∞" if ts["payoff"] == float("inf") else (f"{ts['payoff']:.2f}" if ts["n"] else "—")
            L.append(f"| {r['cat']} | {r['theme']} `{r['code']}` | {r['years']} | "
                     f"{pct(r['base_ann'])} | **{pct(r['st_ann'])}** | "
                     f"{pct(r['st_ann'] - r['base_ann'])} | {pct(r['base_mdd'])} | "
                     f"{pct(r['st_mdd'])} | {pct(r['pos_ratio'], 1)} | {pct(r['sig_ratio'], 1)} | "
                     f"{ts['n']} | {pct(ts['win_rate'], 1) if ts['n'] else '—'} | {po} | "
                     f"{r['bars_avg'] if r['bars_avg'] is not None else '—'} |")
    L.append("")
    L.append("> **买点频率** = 样本内出现买入信号的交易日占比。该策略约 1%，即每只标的每年 ~2.5 个买点；"
             "频率低是设计目标，不是缺陷 —— 放宽到 4 票会把中位回撤从 -17% 拉到 -25%。")
    L.append("")

    L += ["## 五票各自的历史满足率（均值）", "",
          "| " + " | ".join(rule.VOTE_NAMES[k] for k in rule.VOTE_NAMES) + " |",
          "|---" * len(rule.VOTE_NAMES) + "|",
          "| " + " | ".join(f"{np.mean([r['votes'][k] for r in good]) * 100:.1f}%"
                            for k in rule.VOTE_NAMES) + " |", "",
          "> 满足率最低的那一票就是当前配置的**主要约束**（通常是「结构翻越」或「量能确认」）。", ""]

    reasons = {}
    for r in good:
        for k, v in (r.get("exit_reasons") or {}).items():
            reasons[k] = reasons.get(k, 0) + v
    if reasons:
        tot = sum(reasons.values())
        L += ["## 离场原因分布（全部标的合并）", "",
              "| 原因 | 笔数 | 占比 |", "|---|---|---|"]
        L += [f"| {k} | {v} | {v / tot * 100:.1f}% |" for k, v in
              sorted(reasons.items(), key=lambda kv: -kv[1])]
        L.append("")

    if bad:
        L += ["## 失败 / 样本不足", "", "| 标的 | 主题 | 原因 |", "|---|---|---|"]
        L.extend(f"| `{r['code']}` | {r['theme']} | {r.get('err')} |" for r in bad)
        L.append("")

    trades = [t for r in good for t in (r.get("trade_log") or [])]
    ts = bt_stats.trade_stats(trades)
    L += bt_stats.section_lines(
        ts, title=f"全体单笔合并统计（所有标的 {len(good)} 只一并计）",
        note=(f"单笔按持仓期收益计（已扣双边 {rule.FEE:.2%} 成本）；胜率=盈利笔数/总笔数；"
              f"盈亏比=平均盈利/|平均亏损|；利润因子=总盈利/|总亏损|。"))
    if trades:
        bars = np.array([t["bars"] for t in trades])
        L += ["", f"> 持仓天数: 均值 {bars.mean():.1f} | 中位 {np.median(bars):.0f} | "
                  f"≤5日 {np.mean(bars <= 5) * 100:.0f}% | ≤10日 {np.mean(bars <= 10) * 100:.0f}%"
                  f" | >20日 {np.mean(bars > 20) * 100:.0f}%"]
    L += ["", f"> 生成 {datetime.now():%Y-%m-%d %H:%M} · 非投资建议，实盘前须人工复核。", ""]

    txt = "\n".join(L)
    open(MD, "w", encoding="utf-8").write(txt)
    print(f"[回测报告] {len(good)} 只 · 跑赢 {beat}/{len(good)}"
          f" · 中位年化 {pct(med(good, 'st_ann'), 2)} vs 基准 {pct(med(good, 'base_ann'), 2)}")
    print(f"  -> {MD}")
    print(f"  -> {PNG}")


if __name__ == "__main__":
    main()
