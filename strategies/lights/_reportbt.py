# -*- coding: utf-8 -*-
"""亮灯策略 · 回测报告（渲染 backtest.py 的产物）。

输入: data/_lights_bt.jsonl（backtest.py 产出; 用 --in 可指定其它产物）
输出: strategies/lights/_bt_report.md
      strategies/lights/_bt_visual.png
      strategies/lights/_bt_dashboard.html

版式沿用仓库既有回测报告惯例: H1 + `>` 口径块 + 分类汇总（末行 **全部**）+ 逐只明细
+ 失败节 + 全体单笔合并统计（`pipeline.bt_stats.section_lines`）+ 上下双子图 + HTML 看板。
"""
import base64
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
_ROOT = os.path.dirname(os.path.dirname(_DIR))
for p in (_ROOT, os.path.dirname(_DIR), _DIR):
    if p not in sys.path:
        sys.path.insert(0, p)

import rule  # noqa: E402
import scan as _scan  # noqa: E402  （只为取门槛中文标签）
from pipeline import bt_stats  # noqa: E402

IN_DEFAULT = os.path.join(_ROOT, "data", "_lights_bt.jsonl")
MD = os.path.join(_DIR, "_bt_report.md")
PNG = os.path.join(_DIR, "_bt_visual.png")
HTML = os.path.join(_DIR, "_bt_dashboard.html")

CAT_ORDER = ("宽基/另类", "全球/QDII", "A股行业", "策略/商品", "主动/量化", "债券")
plt.rcParams["font.sans-serif"] = ["Microsoft YaHei", "SimHei", "DejaVu Sans"]
plt.rcParams["axes.unicode_minus"] = False


def pct(x, dp=1):
    return "—" if x is None else f"{x * 100:+.{dp}f}%"


def pct2(x, dp=2):
    return "—" if x is None else f"{x * 100:+.{dp}f}%"


def load(path):
    if not os.path.exists(path):
        sys.exit(f"缺少回测产物 {path}; 先跑 python strategies/lights/backtest.py")
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
    ax1.barh(y - 0.2, st, height=0.38, color=cols, label="亮灯策略")
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
    cfg = rule.ACTIVE
    span = f"{min(r['start'] for r in good)} ~ {max(r['end'] for r in good)}"
    beat = sum(r["st_ann"] > r["base_ann"] for r in good)
    plot(good)

    L = ["# 亮灯策略（Lights）· 逐标的回测: 策略 vs 买入持有", "",
         f"> 样本区间 {span}（最长十年, 上市晚者按实有） · 成本 单边 {cfg.fee:.2%}（ETF 佣金）",
         f"> 执行: 当日收盘算信号 → **次日开盘**成交（无前视）; 每 {cfg.rebal} 交易日评估; "
         f"目标仓位由 weight_rules 分层给出; 期末持仓按最新收盘估值",
         f"> 配置 **{cfg.label}** · 门槛层 "
         f"{' + '.join(_scan.GATE_LABELS.get(g, (g,))[0] for g in cfg.gates)}"
         f" · 得分门槛 {cfg.enter_min:g}"
         f" · 离场 {'门槛不过即清仓' if cfg.exit_on_gate_fail else ('得分 ≤ ' + str(cfg.exit_max))}"
         f" · 止损 {cfg.trail_stop if cfg.trail_stop else '无'} · 持仓上限 {cfg.max_hold or '无'}",
         f"> 成功 {len(good)} 只 / 失败 {len(bad)} 只 · 跑赢率 **{beat}/{len(good)}**"
         f" · 平均超额 {pct(agg(good, 'st_ann') - agg(good, 'base_ann'))}", ""]

    cats = [c for c in CAT_ORDER if any(r["cat"] == c for r in good)]
    cats += [c for c in dict.fromkeys(r["cat"] for r in good) if c not in cats]

    L += ["## 分类汇总（均值）", "",
          "| 类别 | n | 基准年化 | 策略年化 | 超额 | 基准回撤 | 策略回撤 | 回撤改善 | "
          "持仓占比 | 信号满足率 | 单笔数 | 胜率 | 盈亏比 |",
          "|---" * 13 + "|"]
    for c in cats + [None]:
        rs = good if c is None else [r for r in good if r["cat"] == c]
        if not rs:
            continue
        b, s = agg(rs, "base_ann"), agg(rs, "st_ann")
        bm, sm = agg(rs, "base_mdd"), agg(rs, "st_mdd")
        wr = [r["t_stats"]["win_rate"] for r in rs]
        po = [r["t_stats"]["payoff"] for r in rs if r["t_stats"]["n"] > 0]
        lab = "**全部**" if c is None else c
        inf = lambda x: "∞" if (x is not None and x == float("inf")) else (f"{x:.2f}" if x is not None else "—")  # noqa: E731
        L.append(f"| {lab} | {len(rs)} | {pct(b)} | {pct(s)} | {pct(s - b)} | {pct(bm)} | "
                 f"{pct(sm)} | {pct(sm - bm)} | {pct(agg(rs, 'pos_ratio'), 1)} | "
                 f"{pct(agg(rs, 'sig_ratio'), 1)} | {agg(rs, 'trades'):.1f} | "
                 f"{pct(np.mean(wr), 1)} | {inf(float(np.mean(po)) if po else None)} |")
    L.append("")

    L += ["## 逐只明细（按类别 / 策略年化排序）", "",
          "| 类别 | 标的 | 样本年 | 基准年化 | 策略年化 | 超额 | 基准回撤 | 策略回撤 | "
          "持仓占比 | 信号满足率 | 平均得分 | 择时边际(bp) | 单笔数 | 胜率 | 盈亏比 | 平均持仓日 |",
          "|---" * 16 + "|"]
    for c in cats:
        for r in sorted([x for x in good if x["cat"] == c], key=lambda x: -x["st_ann"]):
            ts = r["t_stats"]
            inf = "∞" if ts["payoff"] == float("inf") else f"{ts['payoff']:.2f}"
            L.append(f"| {r['cat']} | {r['theme']} `{r['code']}` | {r['years']} | "
                     f"{pct(r['base_ann'])} | **{pct(r['st_ann'])}** | "
                     f"{pct(r['st_ann'] - r['base_ann'])} | {pct(r['base_mdd'])} | "
                     f"{pct(r['st_mdd'])} | {pct(r['pos_ratio'], 1)} | {pct(r['sig_ratio'], 1)} | "
                     f"{r['score_avg']:.2f} | {r['timing_edge'] * 1e4:+.1f} | {ts['n']} | "
                     f"{pct(ts['win_rate'], 1)} | {inf} | "
                     f"{r['bars_avg'] if r['bars_avg'] is not None else '—'} |")
    L.append("")
    L.append("> **择时边际** = 持仓日日均收益 − 空仓日日均收益（bp = 0.01%）。为正是「持仓时确实更会涨」"
             "的择时证据；该口径不受仓位高低影响，是判断低暴露策略有无 alpha 的关键指标。")
    L.append("")

    if bad:
        L += ["## 失败 / 样本不足", "", "| 标的 | 主题 | 原因 |", "|---|---|---|"]
        L.extend(f"| `{r['code']}` | {r['theme']} | {r.get('err')} |" for r in bad)
        L.append("")

    trades = [t for r in good for t in (r.get("trade_log") or [])]
    ts = bt_stats.trade_stats(trades)
    L += bt_stats.section_lines(
        ts, title=f"全体单笔合并统计（所有标的 {len(good)} 只一并计）",
        note=(f"单笔按持仓期收益计（已扣双边 {cfg.fee:.2%} 成本）；胜率=盈利笔数/总笔数；"
              f"盈亏比=平均盈利/|平均亏损|；利润因子=总盈利/|总亏损|。"))
    if trades:
        bars = np.array([t["bars"] for t in trades])
        L += ["", f"> 持仓天数: 均值 {bars.mean():.1f} | 中位 {np.median(bars):.0f} | "
                  f"≤5日 {np.mean(bars <= 5) * 100:.0f}% | ≤10日 {np.mean(bars <= 10) * 100:.0f}%"
                  f" | >20日 {np.mean(bars > 20) * 100:.0f}%"]
    L += ["", f"> 生成 {datetime.now():%Y-%m-%d %H:%M} · 非投资建议，实盘前须人工复核。", ""]

    txt = "\n".join(L)
    open(MD, "w", encoding="utf-8").write(txt)
    b64 = base64.b64encode(open(PNG, "rb").read()).decode()
    esc = txt.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
    open(HTML, "w", encoding="utf-8").write(
        '<!DOCTYPE html><html lang="zh"><head><meta charset="utf-8">'
        '<title>亮灯策略 · 回测报告</title><style>'
        'body{font-family:"Microsoft YaHei",sans-serif;background:#f5f6fa;margin:24px;color:#222}'
        'h1{font-size:20px}img{max-width:100%;border-radius:10px;box-shadow:0 2px 10px rgba(0,0,0,.08)}'
        'pre{background:#fff;padding:16px;border-radius:10px;overflow:auto;font-size:12px;line-height:1.5}'
        f'</style></head><body><h1>亮灯策略 · 回测报告（{cfg.label}）</h1>'
        f'<img src="data:image/png;base64,{b64}"><pre>{esc}</pre></body></html>')

    print(f"[回测报告] {len(good)} 只 · 跑赢 {beat}/{len(good)}"
          f" · 基准年化 {pct(agg(good, 'base_ann'))} vs 策略 {pct(agg(good, 'st_ann'))}")
    print(f"  -> {MD}")
    print(f"  -> {PNG}")
    print(f"  -> {HTML}")


if __name__ == "__main__":
    main()
