"""四灯共振结果可视化：data/_inner_out.jsonl -> strategies/four_lights/_4d_dashboard.html (+PNG)

热力图(标的×四灯 红/黄/绿) + 总分排序条 + 决策分布。数据为最近一次扫描。
"""
import base64
import io
import json

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
from matplotlib.colors import ListedColormap

plt.rcParams["font.sans-serif"] = ["Microsoft YaHei", "SimHei", "DejaVu Sans"]
plt.rcParams["axes.unicode_minus"] = False

OUT = "g:/xalpha/data/_inner_out.jsonl"
PNG = "g:/xalpha/strategies/four_lights/_4d_visual.png"
HTML = "g:/xalpha/strategies/four_lights/_4d_dashboard.html"

DEC_ORDER = {"买入": 0, "持有": 1, "观望": 2, "卖出": 3}
LNAMES = ["趋势灯", "主力灯", "持续力灯", "热度灯"]
DCOLORS = {"买入": "#c62828", "持有": "#ef6c00", "观望": "#f9a825", "卖出": "#2e7d32"}
CMAP = ListedColormap(["#2e7d32", "#f9a825", "#c62828"])  # 0灭/1偏多/2亮

raw = open(OUT, "rb").read()
text = raw.decode("utf-16") if raw[:2] in (b"\xff\xfe", b"\xfe\xff") else raw.decode("utf-8")
rows = []
for ln in text.splitlines():
    if '"ok"' not in ln:
        continue
    try:
        r = json.loads(ln)
        if r.get("ok"):
            rows.append(r)
    except json.JSONDecodeError:
        pass

# 同 inner code 合并为一行展示（唯一池下无重复，兼容旧数据双行情况）
merged = []
seen = set()
for r in sorted(rows, key=lambda x: (DEC_ORDER.get(x["dec"], 9), -x["total"], x["idx"])):
    if r["inner"] in seen:
        for m in merged:
            if m["inner"] == r["inner"]:
                m["theme"] = m["theme"].split("×")[0] + f"×{len([q for q in merged if q['inner'] == r['inner']]) + 1}"
        continue
    seen.add(r["inner"])
    merged.append(dict(r))

merged.sort(key=lambda x: (DEC_ORDER.get(x["dec"], 9), -x["total"], x["idx"]))

n = len(merged)
labels = [f"{r['theme']}·{r['inner']}" for r in merged]
scores = [[r["tl"], r["cl"], r["sl"], r["hl"]] for r in merged]
totals = [r["total"] for r in merged]

fig = plt.figure(figsize=(15, max(9, 0.34 * n + 4)))
gs = fig.add_gridspec(1, 2, width_ratios=[3.1, 1], wspace=0.04)

# ---- 左：四灯热力图 ----
ax = fig.add_subplot(gs[0])
im = ax.imshow(scores, aspect="auto", cmap=CMAP, vmin=0, vmax=2)
ax.set_xticks(range(4))
ax.set_xticklabels(LNAMES, fontsize=11)
ax.set_yticks(range(n))
ax.set_yticklabels(labels, fontsize=9)
for i in range(n):
    for j in range(4):
        ax.text(j, i, str(scores[i][j]), ha="center", va="center",
                fontsize=8, color="white" if scores[i][j] == 2 else "#1a1a1a")
ax.set_title("四灯状态（2=亮·看多 / 1=偏多 / 0=灭·看空）", fontsize=12)
ax.grid(False)

# ---- 右：总分条形 ----
ax2 = fig.add_subplot(gs[1], sharey=ax)
colors = [DCOLORS[r["dec"]] for r in merged]
ax2.barh(range(n), totals, color=colors, height=0.7)
ax2.set_xlim(0, 8)
ax2.set_xlabel("总分 (0-8)", fontsize=10)
ax2.set_yticks([])
for i, (t, r) in enumerate(zip(totals, merged)):
    ax2.text(t + 0.15, i, str(t), va="center", fontsize=9)
ax2.invert_yaxis()
ax2.set_title("总分", fontsize=11)
ax2.grid(axis="x", alpha=0.3)

# ---- 顶部信息 & 底部统计 ----
from collections import Counter
cnt = Counter(r["dec"] for r in merged)
date_info = merged[0]["date"] if merged else "?"
date_info = merged[0].get("date", "?")
snap = merged[0].get("snap", "?")
info = (f"场内映射四灯共振 · {n} 只标的 · 场内日线截至 {date_info}"
        f" · 主力/换手快照 {snap}\n"
        + "   ".join(f"{k} {v}只" for k, v in cnt.items()))
fig.suptitle(info, fontsize=13, y=0.985)

fig.savefig(PNG, dpi=150, bbox_inches="tight", facecolor="white")
plt.close(fig)

b64 = base64.b64encode(open(PNG, "rb").read()).decode()
summary = "".join(
    f"<div class='card' style='background:{DCOLORS[d]}'>{cnt.get(d, 0)}<span>{d}</span></div>"
    for d in ["买入", "持有", "观望", "卖出"]
)
trs = []
for r in merged:
    row = (
        f"<tr><td>{r['idx']}</td><td>{r['theme']}</td><td>{r['inner']}</td>"
        f"<td>{r['tl']}</td><td>{r['cl']}</td><td>{r['sl']}</td><td>{r['hl']}</td>"
        f"<td><b>{r['total']}</b></td>"
        f"<td><span class='tag' style='background:{DCOLORS[r['dec']]}'>{r['dec']}</span></td>"
        f"<td style='color:#888;font-size:12px'>{r['note']}</td></tr>"
    )
    trs.append(row)

html = f"""<!DOCTYPE html>
<html lang="zh"><head><meta charset="utf-8">
<title>四灯共振 · 场内映射信号</title>
<style>
body{{font-family:"Microsoft YaHei",sans-serif;margin:24px;background:#f5f6fa;color:#222}}
h1{{font-size:22px}} h2{{font-size:16px;margin-top:28px}}
.cards{{display:flex;gap:14px;margin:16px 0}}
.card{{color:#fff;border-radius:10px;padding:14px 22px;font-size:30px;font-weight:700;line-height:1.1}}
.card span{{display:block;font-size:13px;font-weight:400;opacity:.9}}
img{{max-width:100%;border-radius:10px;box-shadow:0 2px 10px rgba(0,0,0,.08);margin-top:10px}}
table{{border-collapse:collapse;width:100%;background:#fff;border-radius:10px;overflow:hidden;font-size:13px;box-shadow:0 2px 10px rgba(0,0,0,.05)}}
th,td{{padding:7px 10px;border-bottom:1px solid #eee;text-align:center}}
th{{background:#2c3e50;color:#fff;font-weight:500}}
td:nth-child(2){{text-align:left}}
.tag{{color:#fff;padding:2px 10px;border-radius:20px;font-size:12px}}
.note{{color:#888;font-size:12px;margin-top:10px}}
</style></head><body>
<h1>四灯共振 · 场外基金→场内标的信号面板</h1>
<div class='note'>日线截至 <b>{date_info}</b> · 主力/换手快照 {snap} · 机械规则输出，非投资建议</div>
<div class='cards'>{summary}</div>
<img src="data:image/png;base64,{b64}" alt="四灯热力图">
<h2>标的明细</h2>
<table><tr><th>#</th><th>主题</th><th>场内代码</th><th>趋势</th><th>主力</th><th>持续</th><th>热度</th><th>总分</th><th>决策</th><th>触发依据</th></tr>
{''.join(trs)}
</table>
</body></html>"""

open(HTML, "w", encoding="utf-8").write(html)
print(f"PNG : {PNG}")
print(f"HTML: {HTML}")
print("dec:", dict(cnt))
