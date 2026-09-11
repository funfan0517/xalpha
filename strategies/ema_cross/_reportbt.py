# -*- coding: utf-8 -*-
"""双均线趋势(EMA12/26)回测结果汇总: data/_ema_cross_bt.jsonl -> _bt_report.md + PNG + HTML。

对齐 lights/_reportbt.py 惯例, 供 run_flow backtest --report --strategy ema_cross 复用。
汇总节: 参数/口径说明 + 分类均值表 + 逐只明细 + 全体单笔合并统计(bt_stats 统一口径)。
"""
import base64
import json
import os
import sys

_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

import numpy as np

from pipeline import bt_stats

_OUT = os.path.join(_ROOT, "data", "_ema_cross_bt.jsonl")
_DIR = os.path.dirname(os.path.abspath(__file__))
_MD = os.path.join(_DIR, "_bt_report.md")
_PNG = os.path.join(_DIR, "_bt_visual.png")
_HTML = os.path.join(_DIR, "_bt_dashboard.html")

_CAT_ORDER = ["宽基/另类", "全球/QDII", "A股行业", "策略/商品"]


def _cat(c):
    return c if c in _CAT_ORDER else "其他"


def pct(x):
    return f"{x * 100:+.1f}%"


def main():
    raw = open(_OUT, "rb").read()
    text = raw.decode("utf-16") if raw[:2] in (b"\xff\xfe", b"\xfe\xff") else raw.decode("utf-8")
    rows, fails = [], []
    for ln in text.splitlines():
        if '"ok"' not in ln:
            continue
        try:
            r = json.loads(ln)
            (rows if r.get("ok") else fails).append(r)
        except json.JSONDecodeError:
            pass
    if not rows:
        sys.exit(f"无成功回测结果: {_OUT}")

    rows.sort(key=lambda r: (_cat(r.get("cat", "")), -r["st_ann"]))
    lo = min(r.get("start", "2021-08-01") for r in rows)
    hi = max(r["end"] for r in rows)
    beat = sum(r["st_ann"] > r["base_ann"] for r in rows)

    L = ["# 双均线趋势(EMA12/26 · 金叉死叉)最长十年回测: 策略 vs 买入持有", ""]
    L.append("> **参数** 快线 EMA12 / 慢线 EMA26(经典值, 未做参数寻优); "
             "增强过滤: 金叉须 close>EMA26(趋势) · 两线差距≥0.3% 才判交叉(阈值) · "
             "交易间隔<5 交易日锁仓")
    L.append(f"> **区间** {lo[:7]} ~ {hi[:7]}(各标的按上市日起, 最长十年) · "
             "**成本** 单边0.03%(ETF佣金, 每笔双边) · **执行** 当日收盘信号→次日开盘成交(无前视)")
    L.append(f"> **样本** 成功 {len(rows)} 只 / 失败 {len(fails)} 只; "
             f"跑赢率(策略年化>基准) = {beat}/{len(rows)}")
    L.append("")

    # ---- 分类均值汇总 ----
    L.append("## 分类汇总(均值)")
    L.append("")
    L.append("| 类别 | n | 基准年化 | 策略年化 | 超额 | 基准回撤 | 策略回撤 | 回撤改善 | "
             "持仓占比 | 单笔数 | 单笔胜率 | 盈亏比 |")
    L.append("|---|---|---|---|---|---|---|---|---|---|---|---|")

    def avg_wr(sub):
        vs = [r["t_stats"]["win_rate"] for r in sub if r.get("t_stats") and r["t_stats"]["n"]]
        return float(np.mean(vs)) if vs else None

    def avg_po(sub):
        vs = [r["t_stats"]["payoff"] for r in sub
              if r.get("t_stats") and r["t_stats"]["payoff"] != float("inf")]
        return float(np.mean(vs)) if vs else None

    def avg_tr(sub):
        return int(np.mean([r["trades"] for r in sub])) if sub else 0

    def tcell(sub):
        wr, po = avg_wr(sub), avg_po(sub)
        a = pct(wr) if wr is not None else "—"
        b = f"{po:.2f}" if po is not None else "—"
        return f"{a} | {b}"

    for c in _CAT_ORDER:
        sub = [r for r in rows if _cat(r.get("cat", "")) == c]
        if not sub:
            continue
        n = len(sub)
        ag = lambda f: np.mean([r[f] for r in sub])
        win = sum(r["st_ann"] > r["base_ann"] for r in sub)
        L.append(f"| {c} | {n} | {pct(ag('base_ann'))} | {pct(ag('st_ann'))} | {pct(ag('st_ann') - ag('base_ann'))}"
                 f" | {pct(ag('base_mdd'))} | {pct(ag('st_mdd'))} | {pct(ag('st_mdd') - ag('base_mdd'))}"
                 f" | {pct(ag('pos_ratio'))} | {avg_tr(sub)} | {tcell(sub)} |")
    n = len(rows)
    ag = lambda f: np.mean([r[f] for r in rows])
    L.append(f"| **全部** | {n} | {pct(ag('base_ann'))} | {pct(ag('st_ann'))} | {pct(ag('st_ann') - ag('base_ann'))}"
             f" | {pct(ag('base_mdd'))} | {pct(ag('st_mdd'))} | {pct(ag('st_mdd') - ag('base_mdd'))}"
             f" | {pct(ag('pos_ratio'))} | {avg_tr(rows)} | {tcell(rows)} |")
    L.append("")

    # ---- 逐只明细 ----
    L.append("## 逐只明细(按类别/策略年化排序)")
    L.append("")
    L.append("| 类别 | 标的 | 样本年 | 基准年化 | 策略年化 | 超额 | 基准回撤 | 策略回撤 | "
             "单笔数 | 胜率 | 盈亏比 | 持仓占比 |")
    L.append("|---|---|---|---|---|---|---|---|---|---|---|---|")
    for r in rows:
        ts = r.get("t_stats") or {}
        if ts.get("n"):
            po = ts["payoff"]
            po_s = "∞" if po == float("inf") else f"{po:.2f}"
            wc = pct(ts["win_rate"])
        else:
            po_s, wc = "—", "—"
        L.append(f"| {_cat(r.get('cat', ''))} | {r['theme']} `{r['code']}` | {r['years']} | {pct(r['base_ann'])}"
                 f" | {pct(r['st_ann'])} | {pct(r['st_ann'] - r['base_ann'])} | {pct(r['base_mdd'])}"
                 f" | {pct(r['st_mdd'])} | {ts.get('n', r['trades'])} | {wc} | {po_s} | {pct(r['pos_ratio'])} |")
    if fails:
        L.append("")
        L.append("## 失败/样本不足")
        L.append("")
        for r in fails:
            L.append(f"- {r['theme']} `{r['code']}`: {r.get('err')}")

    # ---- 全体单笔合并统计(统一口径) ----
    merged = [t for r in rows for t in (r.get("trade_log") or [])]
    if merged:
        L += bt_stats.section_lines(
            bt_stats.trade_stats(merged),
            title=f"全体单笔合并统计(所有标的{len(rows)}只一并计)",
            note="单笔=每标的每次完整持仓周期(信号次日开盘买入→次日开盘/期末收盘卖出)；"
                 "ret 已扣 0.03%×2 佣金(期末未平仓仅扣买入费)；过滤后交易偏少是阈值0.3%+锁仓5日的预期效果(减少毛刺磨损)。")
    L.append("")

    txt = "\n".join(L)
    with open(_MD, "w", encoding="utf-8") as fh:
        fh.write(txt)

    # ---- 图: 年化/回撤横向条形 ----
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    plt.rcParams["font.sans-serif"] = ["Microsoft YaHei", "SimHei", "DejaVu Sans"]
    plt.rcParams["axes.unicode_minus"] = False

    labels = [r["theme"] for r in rows]
    ba = np.array([r["base_ann"] for r in rows]) * 100
    sa = np.array([r["st_ann"] for r in rows]) * 100
    bm = np.array([r["base_mdd"] for r in rows]) * 100
    sm = np.array([r["st_mdd"] for r in rows]) * 100
    y = np.arange(len(rows))
    fig, (ax1, ax2) = plt.subplots(2, 1, figsize=(11, max(7, len(rows) * 0.28)), sharey=True)
    h = 0.38
    ax1.barh(y + h / 2, ba, height=h, color="#b0bec5", label="买入持有年化")
    win_c = np.where(sa >= ba, "#c62828", "#1565c0")
    ax1.barh(y - h / 2, sa, height=h, color=win_c, label="双均线策略年化")
    ax1.axvline(0, color="k", lw=0.6)
    ax1.set_yticks(y)
    ax1.set_yticklabels(labels, fontsize=9)
    ax1.invert_yaxis()
    ax1.set_title("年化收益: 红=策略胜 / 蓝=策略负")
    ax1.legend(fontsize=8, loc="lower right")
    ax2.barh(y + h / 2, bm, height=h, color="#b0bec5", label="持有最大回撤")
    ax2.barh(y - h / 2, sm, height=h, color="#ef6c00", label="策略最大回撤")
    ax2.axvline(0, color="k", lw=0.6)
    ax2.set_title("最大回撤(越靠右越浅=越好)")
    ax2.legend(fontsize=8, loc="lower right")
    fig.suptitle("双均线趋势(EMA12/26)最长十年回测 vs 买入持有", fontsize=13)
    fig.tight_layout(rect=(0, 0, 1, 0.97))
    fig.savefig(_PNG, dpi=150, bbox_inches="tight", facecolor="white")
    plt.close(fig)

    b64 = base64.b64encode(open(_PNG, "rb").read()).decode()
    html = f"""<!DOCTYPE html><html lang="zh"><head><meta charset="utf-8">
<title>双均线趋势回测</title>
<style>
body{{font-family:"Microsoft YaHei",sans-serif;background:#f5f6fa;margin:24px;color:#222}}
img{{max-width:100%;border-radius:10px;box-shadow:0 2px 10px rgba(0,0,0,.08)}}
pre{{background:#fff;padding:16px;border-radius:10px;overflow:auto;font-size:12px;line-height:1.5}}
</style></head><body>
<h1>双均线趋势(EMA12/26)最长十年回测</h1>
<img src="data:image/png;base64,{b64}">
<pre>{txt.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")}</pre>
</body></html>"""
    with open(_HTML, "w", encoding="utf-8") as fh:
        fh.write(html)
    print("\n".join(txt.splitlines()[:30]))
    print(f"... 明细共 {len(rows)} 行; PNG/HTML/MD 已写入 strategies/ema_cross/")


if __name__ == "__main__":
    main()
