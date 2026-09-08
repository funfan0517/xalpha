"""四灯(量价代理)回测结果汇总: data/_bt_out.jsonl -> md + PNG"""
import base64
import io
import json
import os
import sys

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)
from pipeline import bt_stats

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np

plt.rcParams["font.sans-serif"] = ["Microsoft YaHei", "SimHei", "DejaVu Sans"]
plt.rcParams["axes.unicode_minus"] = False

OUT = "g:/xalpha/data/_bt_out.jsonl"
MD = "g:/xalpha/4d/_bt_report.md"
PNG = "g:/xalpha/4d/_bt_visual.png"
HTML = "g:/xalpha/4d/_bt_dashboard.html"

CAT = lambda idx: ("宽基/另类" if idx <= 4 else "全球/QDII" if idx <= 16 else "A股行业" if idx <= 37 else "策略/商品")

raw = open(OUT, "rb").read()
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

rows.sort(key=lambda r: (CAT(r["idx"]), -r["st_ann"]))
order = ["宽基/另类", "全球/QDII", "A股行业", "策略/商品"]
L = []
L.append("# 四灯共振(量价代理版) 近5年回测: 策略 vs 买入持有")
L.append("")
L.append("> **区间** 2021-08 ~ 2026-09(样本至 2026-09-08) · **成本** 单次0.03%(ETF佣金) · **信号** 次日开盘执行")
L.append("> **口径说明**: 主力/热度灯历史无主力资金与换手数据，用「放量上涨/5日动量+量能」代理(0-2)；趋势/持续力灯用真实 MA/ADX/MACD/周线。回测为**量价代理版**，与当日快照版不完全一致。")
L.append(f"> 成功 {len(rows)} 只 / 失败 {len(fails)} 只; 跑赢率(策略年化>基准) = "
         f"{sum(r['st_ann'] > r['base_ann'] for r in rows)}/{len(rows)}")
L.append("")
L.append("## 分类汇总(均值)")
L.append("")
L.append("| 类别 | n | 基准年化 | 策略年化 | 超额 | 基准回撤 | 策略回撤 | 回撤改善 | 持仓占比 | 跑赢率 | 单笔胜率 | 盈亏比 |")
L.append("|---|---|---|---|---|---|---|---|---|---|---|---|")

def pct(x):
    return f"{x * 100:+.1f}%"

def avg_wr(sub):
    vs = [r["t_stats"]["win_rate"] for r in sub if r.get("t_stats") and r["t_stats"]["n"]]
    return float(np.mean(vs)) if vs else None

def avg_po(sub):
    vs = [r["t_stats"]["payoff"] for r in sub
          if r.get("t_stats") and r["t_stats"]["payoff"] != float("inf")]
    return float(np.mean(vs)) if vs else None

def tcols(sub):
    wr, po = avg_wr(sub), avg_po(sub)
    return f" | {pct(wr) if wr is not None else '—'} | {po:.2f}" if po is not None \
        else f" | {pct(wr) if wr is not None else '—'} | —"

for c in order:
    sub = [r for r in rows if CAT(r["idx"]) == c]
    if not sub:
        continue
    n = len(sub)
    ag = lambda f: np.mean([r[f] for r in sub])
    win = sum(r["st_ann"] > r["base_ann"] for r in sub)
    L.append(f"| {c} | {n} | {pct(ag('base_ann'))} | {pct(ag('st_ann'))} | {pct(ag('st_ann')-ag('base_ann'))}"
             f" | {pct(ag('base_mdd'))} | {pct(ag('st_mdd'))} | {pct(ag('st_mdd')-ag('base_mdd'))}"
             f" | {pct(ag('pos_ratio'))} | {win}/{n}{tcols(sub)} |")
L.append(f"| **全部** | {len(rows)} | {pct(np.mean([r['base_ann'] for r in rows]))} | {pct(np.mean([r['st_ann'] for r in rows]))}"
         f" | {pct(np.mean([r['st_ann']-r['base_ann'] for r in rows]))}"
         f" | {pct(np.mean([r['base_mdd'] for r in rows]))} | {pct(np.mean([r['st_mdd'] for r in rows]))}"
         f" | {pct(np.mean([r['st_mdd']-r['base_mdd'] for r in rows]))}"
         f" | {pct(np.mean([r['pos_ratio'] for r in rows]))} | "
         f"{sum(r['st_ann']>r['base_ann'] for r in rows)}/{len(rows)}{tcols(rows)} |")
L.append("")
L.append("## 逐只明细(按策略年化排序)")
L.append("")
L.append("| 类别 | 标的 | 样本年 | 基准年化 | 策略年化 | 超额 | 基准回撤 | 策略回撤 | 单笔数 | 胜率 | 盈亏比 | 持仓占比 |")
L.append("|---|---|---|---|---|---|---|---|---|---|---|---|")
for r in rows:
    ts = r.get("t_stats") or {}
    if ts.get("n"):
        po = ts["payoff"]
        po_s = "∞" if po == float("inf") else f"{po:.2f}"
        wc = pct(ts["win_rate"])
    else:
        po_s, wc = "—", "—"
    L.append(f"| {CAT(r['idx'])} | {r['theme']} `{r['code']}` | {r['years']} | {pct(r['base_ann'])}"
             f" | {pct(r['st_ann'])} | {pct(r['st_ann']-r['base_ann'])} | {pct(r['base_mdd'])}"
             f" | {pct(r['st_mdd'])} | {ts.get('n', r['trades'])} | {wc} | {po_s} | {pct(r['pos_ratio'])} |")
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
        title="全体单笔合并统计（所有标的一并计，样本 " + str(len(rows)) + " 只）",
        note="单笔=每标的每次持仓周期；已计 0.03% 双边佣金；期末未平仓按最新收盘估值计入。")
else:
    L.append("")
    L.append("> 旧版 _bt_out.jsonl 未含单笔明细(trade_log)；请用升级后引擎重跑回测并 `--report` 以输出胜率/盈亏比。")

# ---- 图 ----
labels = [f"{r['theme']}" for r in rows]
ba = np.array([r["base_ann"] for r in rows]) * 100
sa = np.array([r["st_ann"] for r in rows]) * 100
bm = np.array([r["base_mdd"] for r in rows]) * 100
sm = np.array([r["st_mdd"] for r in rows]) * 100
y = np.arange(len(rows))
fig, (ax1, ax2) = plt.subplots(2, 1, figsize=(11, max(7, len(rows) * 0.28)), sharey=True)
h = 0.38
ax1.barh(y + h / 2, ba, height=h, color="#b0bec5", label="买入持有年化")
win_c = np.where(sa >= ba, "#c62828", "#1565c0")
ax1.barh(y - h / 2, sa, height=h, color=win_c, label="四灯策略年化")
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
fig.suptitle("四灯共振(量价代理) 近5年回测  vs 买入持有", fontsize=13)
fig.tight_layout(rect=(0, 0, 1, 0.97))
fig.savefig(PNG, dpi=150, bbox_inches="tight", facecolor="white")
plt.close(fig)

b64 = base64.b64encode(open(PNG, "rb").read()).decode()
html = f"""<!DOCTYPE html><html lang="zh"><head><meta charset="utf-8">
<title>四灯回测</title>
<style>
body{{font-family:"Microsoft YaHei",sans-serif;background:#f5f6fa;margin:24px;color:#222}}
img{{max-width:100%;border-radius:10px;box-shadow:0 2px 10px rgba(0,0,0,.08)}}
pre{{background:#fff;padding:16px;border-radius:10px;overflow:auto;font-size:12px;line-height:1.5}}
</style></head><body>
<h1>四灯共振(量价代理版) 近5年回测</h1>
<img src="data:image/png;base64,{b64}">
<pre>{("\n".join(L)).replace("&","&amp;").replace("<","&lt;").replace(">","&gt;")}</pre>
</body></html>"""
open(HTML, "w", encoding="utf-8").write(html)

txt = "\n".join(L)
with open(MD, "w", encoding="utf-8") as fh:
    fh.write(txt)
print("\n".join(L[:28]))
print(f"... 明细共 {len(rows)} 行; PNG/HTML/MD 已写入 4d/")
