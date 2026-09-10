# -*- coding: utf-8 -*-
"""四灯 5-10 天短线变种 回测结果汇总: data/_st10_out.jsonl -> md + PNG + HTML。

对照数据: data/_bt_out.jsonl(四灯共振原版, 量价代理口径), 输出两个版本的全体均值对照。
"""
import base64
import io
import json
import os
import sys

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8")

_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)
from pipeline import bt_stats  # noqa: E402

import matplotlib  # noqa: E402

matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402
import numpy as np  # noqa: E402

plt.rcParams["font.sans-serif"] = ["Microsoft YaHei", "SimHei", "DejaVu Sans"]
plt.rcParams["axes.unicode_minus"] = False

OUT = "g:/xalpha/data/_st10_out.jsonl"
OLD = "g:/xalpha/data/_bt_out.jsonl"
MD = "g:/xalpha/strategies/four_lights/short_5_10d/_st10_report.md"
PNG = "g:/xalpha/strategies/four_lights/short_5_10d/_st10_visual.png"
HTML = "g:/xalpha/strategies/four_lights/short_5_10d/_st10_dashboard.html"

CAT = lambda c: c if c in ("宽基/另类", "全球/QDII", "A股行业", "策略/商品") else "其他"
_ORDER = ["宽基/另类", "全球/QDII", "A股行业", "策略/商品"]


def load(OUT):
    rows, fails = [], []
    for ln in open(OUT, encoding="utf-8"):
        if '"ok"' not in ln:
            continue
        try:
            r = json.loads(ln)
            (rows if r.get("ok") else fails).append(r)
        except json.JSONDecodeError:
            pass
    return rows, fails


rows, fails = load(OUT)
rows.sort(key=lambda r: (CAT(r.get("cat", "")), -r["st_ann"]))
old_rows, _ = load(OLD)

L = []
pct = lambda x: f"{x * 100:+.1f}%"

lo = min(r.get("start") for r in rows) if rows else "-"
hi = max(r["end"] for r in rows) if rows else "-"
n = len(rows)
wins = sum(r["st_ann"] > r["base_ann"] for r in rows)
mdd_imp = sum(r["st_mdd"] > r["base_mdd"] for r in rows)
L.append("# 四灯共振 · 5-10 天短线变种 全池回测: 策略 vs 买入持有")
L.append("")
L.append(f"> **区间** {lo[:7]} ~ {hi[:7]}(各标的按上市日起, 最长十年) · **成本** 单边0.03%(ETF佣金) · **信号** 次日开盘执行 · **期末持仓按最新收盘估值**")
L.append("> **口径**: 主力/换手历史无资金与换手明细, 沿用四灯原版「量价代理」口径(放量/量能倍数), 文档见 `_st10_rule.py`。")
L.append(f"> **结果** 成功 {n} 只 / 失败 {len(fails)} 只; 策略年化>基准 {wins}/{n}; 策略回撤<基准 {mdd_imp}/{n}")
L.append("")

# ---- 与原版四灯 全体均值对照 ----
if old_rows:
    L.append("## 与原版四灯共振(量价代理版) 全体均值对照")
    L.append("")
    L.append("| 版本 | n | 基准年化 | 策略年化 | 超额 | 基准回撤 | 策略回撤 | 持仓占比 | 跑赢率 | 单笔胜率 | 平均持仓日 |")
    L.append("|---|---|---|---|---|---|---|---|---|---|---|")

    def agg(rs):
        if not rs:
            return None
        return dict(
            n=len(rs),
            ba=float(np.mean([r["base_ann"] for r in rs])),
            sa=float(np.mean([r["st_ann"] for r in rs])),
            bm=float(np.mean([r["base_mdd"] for r in rs])),
            sm=float(np.mean([r["st_mdd"] for r in rs])),
            pr=float(np.mean([r["pos_ratio"] for r in rs])),
            win=sum(r["st_ann"] > r["base_ann"] for r in rs),
            wr=float(np.mean([r["t_stats"]["win_rate"] for r in rs])) if all(r["t_stats"].get("n") for r in rs) else float("nan"),
            bar=float(np.mean([r["bars_avg"] for r in rs if r.get("bars_avg") is not None])) if any(r.get("bars_avg") is not None for r in rs) else float("nan"),
        )

    a_old, a_new = agg(old_rows), agg(rows)
    if a_old and a_new:
        for tag, a in (("四灯原版(量价代理)", a_old), ("5-10天短线变种", a_new)):
            bar = f"{a['bar']:.1f}" if not np.isnan(a["bar"]) else "—"
            L.append(f"| {tag} | {a['n']} | {pct(a['ba'])} | {pct(a['sa'])} | {pct(a['sa']-a['ba'])}"
                     f" | {pct(a['bm'])} | {pct(a['sm'])} | {pct(a['pr'])}"
                     f" | {a['win']}/{a['n']} | {pct(a['wr']) if not np.isnan(a['wr']) else '—'}"
                     f" | {bar} |")
        L.append("")
        L.append("> 注: 原版 `_bt_report.md` 口径为「总分>=6 且 趋势/主力>=1 开仓、总分<=3 离场」(持仓期不定长); 变种引入 5-10 日持仓上限与多档仓位。")

# ---- 分类汇总 ----
L.append("## 分类汇总(均值)")
L.append("")
L.append("| 类别 | n | 基准年化 | 策略年化 | 超额 | 基准回撤 | 策略回撤 | 持仓占比 | 跑赢率 | 单笔胜率 | 盈亏比 | 平均持仓日 |")
L.append("|---|---|---|---|---|---|---|---|---|---|---|---|")


def avg_wr(sub):
    vs = [r["t_stats"]["win_rate"] for r in sub if r.get("t_stats") and r["t_stats"]["n"]]
    return float(np.mean(vs)) if vs else None


def avg_po(sub):
    vs = [r["t_stats"]["payoff"] for r in sub
          if r.get("t_stats") and r["t_stats"]["payoff"] != float("inf")]
    return float(np.mean(vs)) if vs else None


def avg_bar(sub):
    vs = [r["bars_avg"] for r in sub if r.get("bars_avg") is not None]
    return float(np.mean(vs)) if vs else None


def tcols(sub):
    wr, po = avg_wr(sub), avg_po(sub)
    return f" | {pct(wr) if wr is not None else '—'} | {po:.2f}" if po is not None \
        else f" | {pct(wr) if wr is not None else '—'} | —"


for c in _ORDER:
    sub = [r for r in rows if r.get("cat", "") == c]
    if not sub:
        continue
    nc = len(sub)
    ag = lambda f: np.mean([r[f] for r in sub])
    win = sum(r["st_ann"] > r["base_ann"] for r in sub)
    L.append(f"| {c} | {nc} | {pct(ag('base_ann'))} | {pct(ag('st_ann'))} | {pct(ag('st_ann')-ag('base_ann'))}"
             f" | {pct(ag('base_mdd'))} | {pct(ag('st_mdd'))} | {pct(ag('pos_ratio'))} | {win}/{nc}"
             f"{tcols(sub)} | {avg_bar(sub):.1f} |")
agg_all = lambda f: np.mean([r[f] for r in rows])
L.append(f"| **全部** | {n} | {pct(agg_all('base_ann'))} | {pct(agg_all('st_ann'))}"
         f" | {pct(agg_all('st_ann')-agg_all('base_ann'))} | {pct(agg_all('base_mdd'))}"
         f" | {pct(agg_all('st_mdd'))} | {pct(agg_all('pos_ratio'))} | {wins}/{n}"
         f"{tcols(rows)} | {avg_bar(rows):.1f} |")
L.append("")

# ---- 逐只明细 ----
L.append("## 逐只明细(按分类-策略年化排序)")
L.append("")
L.append("| 类别 | 标的 | 样本年 | 基准年化 | 策略年化 | 超额 | 基准回撤 | 策略回撤 | 单笔数 | 胜率 | 盈亏比 | 平均持仓日 | 持仓占比 |")
L.append("|---|---|---|---|---|---|---|---|---|---|---|---|---|---|")
for r in rows:
    ts = r.get("t_stats") or {}
    if ts.get("n"):
        po = ts["payoff"]
        po_s = "∞" if po == float("inf") else f"{po:.2f}"
        wc = pct(ts["win_rate"])
    else:
        po_s, wc = "—", "—"
    bar = f"{r['bars_avg']:.1f}" if r.get("bars_avg") is not None else "—"
    L.append(f"| {r.get('cat', '')} | {r['theme']} `{r['code']}` | {r['years']} | {pct(r['base_ann'])}"
             f" | {pct(r['st_ann'])} | {pct(r['st_ann']-r['base_ann'])} | {pct(r['base_mdd'])}"
             f" | {pct(r['st_mdd'])} | {ts.get('n', r['trades'])} | {wc} | {po_s} | {bar}"
             f" | {pct(r['pos_ratio'])} |")
if fails:
    L.append("")
    L.append("## 失败/样本不足")
    L.append("")
    for r in fails:
        L.append(f"- {r['theme']} `{r['code']}`: {r.get('err')}")

# ---- 全体单笔合并统计 ----
merged_log = [t for r in rows for t in (r.get("trade_log") or [])]
if merged_log:
    L += bt_stats.section_lines(
        bt_stats.trade_stats(merged_log),
        title="全体单笔合并统计（所有标的一并计, 样本 " + str(len(rows)) + " 只）",
        note="单笔=每次建仓→离场持仓期; 已计 0.03% 双边佣金; 期末未平仓按最新收盘估值计入; ret=开盘价成交净收益。")
    bars_all = [t.get("bars", 0) for t in merged_log if t.get("bars") is not None]
    if bars_all:
        L += ["", f"> 持仓交易日数: 均值 {np.mean(bars_all):.1f} | 中位 {np.median(bars_all):.0f} | "
                  f"<=3日占 {100*np.mean(np.array(bars_all) <= 3):.0f}% | 4-7日占 {100*np.mean((np.array(bars_all) >= 4) & (np.array(bars_all) <= 7)):.0f}% | "
                  f"8-10日占 {100*np.mean(np.array(bars_all) >= 8):.0f}%", ""]

# ---- 图 ----
labels = [f"{r['theme']}" for r in rows]
ba = np.array([r["base_ann"] for r in rows]) * 100
sa = np.array([r["st_ann"] for r in rows]) * 100
bm = np.array([r["base_mdd"] for r in rows]) * 100
sm = np.array([r["st_mdd"] for r in rows]) * 100
y = np.arange(len(rows))
fig, (ax1, ax2) = plt.subplots(2, 1, figsize=(11, max(7, len(rows) * 0.36 + 1.5)), sharey=True)
h = 0.38
ax1.barh(y + h / 2, ba, height=h, color="#b0bec5", label="买入持有年化")
win_c = np.where(sa >= ba, "#c62828", "#1565c0")
ax1.barh(y - h / 2, sa, height=h, color=win_c, label="5-10天短线变种年化")
ax1.axvline(0, color="k", lw=0.6)
ax1.set_yticks(y)
ax1.set_yticklabels(labels, fontsize=8)
ax1.invert_yaxis()
ax1.set_title("年化收益: 红=策略胜 / 蓝=策略负", fontsize=11)
ax1.legend(fontsize=8, loc="lower right")
ax2.barh(y + h / 2, bm, height=h, color="#b0bec5", label="持有最大回撤")
ax2.barh(y - h / 2, sm, height=h, color="#ef6c00", label="策略最大回撤")
ax2.axvline(0, color="k", lw=0.6)
ax2.set_title("最大回撤(越靠右越浅=越好)", fontsize=11)
ax2.legend(fontsize=8, loc="lower right")
fig.suptitle("四灯共振 · 5-10 天短线变种 回测 vs 买入持有", fontsize=13)
fig.tight_layout(rect=(0, 0, 1, 0.97))
fig.savefig(PNG, dpi=150, bbox_inches="tight", facecolor="white")
plt.close(fig)

b64 = base64.b64encode(open(PNG, "rb").read()).decode()
html = f"""<!DOCTYPE html><html lang="zh"><head><meta charset="utf-8">
<title>四灯5-10天短线变种回测</title>
<style>
body{{font-family:"Microsoft YaHei",sans-serif;background:#f5f6fa;margin:24px;color:#222}}
img{{max-width:100%;border-radius:10px;box-shadow:0 2px 10px rgba(0,0,0,.08)}}
pre{{background:#fff;padding:16px;border-radius:10px;overflow:auto;font-size:12px;line-height:1.5}}
</style></head><body>
<h1>四灯共振 · 5-10 天短线变种 全池回测</h1>
<img src="data:image/png;base64,{b64}">
<pre>{("\n".join(L)).replace("&","&amp;").replace("<","&lt;").replace(">","&gt;")}</pre>
</body></html>"""
open(HTML, "w", encoding="utf-8").write(html)

txt = "\n".join(L)
with open(MD, "w", encoding="utf-8") as fh:
    fh.write(txt)
print("\n".join(L[:30]))
print(f"... 明细共 {len(rows)} 行; PNG/HTML/MD 已写入 strategies/four_lights/short_5_10d/")
