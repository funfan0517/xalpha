"""汇总场內四灯扫描结果 _inner_out.jsonl -> _inner_report.md"""
import io
import json
import sys

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8")

OUT = "g:/xalpha/data/_inner_out.jsonl"
REPORT = "g:/xalpha/strategies/four_lights/_inner_report.md"

# 场内标的显示名: 优先取扫描行自带 inner_name(= _universe.md「场内对应名称」, 全内池唯一来源,
# 自动覆盖新增标的); 旧数据缺该字段时兜底显示代码。不再维护本地 代码->名称 硬编码表。
def nm(r):
    return r.get("inner_name") or r["inner"]

raw = open(OUT, "rb").read()
if raw.startswith((b"\xff\xfe", b"\xfe\xff")):
    text = raw.decode("utf-16")
else:
    text = raw.decode("utf-8")
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

order = {"买入": 0, "持有": 1, "观望": 2, "卖出": 3, "失败": 4}
rows.sort(key=lambda r: (order.get(r.get("dec", "失败"), 9), -r.get("total", 0), r["idx"]))
n_scan = len(rows)
max_date = max(((r.get("date") or "") for r in rows), default="")
max_snap = max(((r.get("snap") or "") for r in rows), default="")
n_main = sum(1 for r in rows if r.get("has_main"))
L = []
L.append("# 场外基金 → 场内对应标的 · 四灯共振「今日信号」")
L.append("")
L.append(f"> **标的池**：唯一池 `data/_universe.md`（场外清单）派生内池，本次扫描 **{n_scan} 只「有场内对应标的」**（场内对应列，由 `pipeline/universe.py` 派生）")
L.append("> **规则**：`四灯共振短线选股方法论` §5 —— 趋势灯 / 主力灯 / 持续力灯 / 热度灯，每灯 亮2/偏多1/灭0，总分 0–8")
L.append(f"> **数据**：场内日线=实抓至 {max_date}；主力净流入/换手率/量比=本地 mx 快照 {max_snap}（{n_main}/{n_scan} 只有真实快照，其余 LOF/未覆盖走量价代理）")
L.append("> **决策**(持仓视角)：总分≥6且趋势/主力≥1=买入 | 总分5且核心≥1=持有 | 总分≤3=卖出 | 其余=观望")
L.append("> **⚠️ 未持有标的的「卖出」= 不建议此时建仓；机械规则输出，非投资建议。**")
L.append("")
L.append("## 决策总览")
L.append("")
L.append("| # | 主题 | 场内标的 | 今日% | 5日% | 趋势 | 主力 | 持续 | 热度 | 总分 | 决策 |")
L.append("|---|---|---|---|---|---|---|---|---|---|---|")
for r in rows:
    L.append(f"| {r['idx']} | {r['theme']} | {nm(r)}"
             f" | {r['today']:+.1f} | {r['w5']:+.1f} | {r['tl']} | {r['cl']} | {r['sl']} | {r['hl']}"
             f" | **{r['total']}** | **{r['dec']}** |")
L.append("")
L.append("## 决策分组")
L.append("")

from collections import defaultdict
grp = defaultdict(list)
for r in rows:
    grp[r["dec"]].append(r)
for dec in ["买入", "持有", "观望", "卖出"]:
    if not grp.get(dec):
        continue
    L.append(f"### {dec}（{len(grp[dec])} 只）")
    L.append("")
    L.append("| # | 主题 | 场外基金 | 场内标的 | 总分 | 灯状态 | 触发依据 |")
    L.append("|---|---|---|---|---|---|---|")
    for r in grp[dec]:
        L.append(f"| {r['idx']} | {r['theme']} | {r['off_name']} | {nm(r)}"
                 f" | {r['total']} | T{r['tls']}/C{r['cls']}/S{r['sls']}/H{r['hls']} | {r['note']} |")
    L.append("")
L.append("## 灯细节与代理说明")
L.append("")
for r in rows:
    L.append(f"- **#{r['idx']} {r['theme']}** `{nm(r)}` 总分{r['total']} → {r['dec']}")
    L.append(f"  - 趋势灯 {r['tl']}分({r['tls']}): {r['td']}")
    L.append(f"  - 主力灯 {r['cl']}分({r['cls']}): {r['cd']}{'' if r['has_main'] else '（无快照，量价代理）'}")
    L.append(f"  - 持续力灯 {r['sl']}分({r['sls']}): {r['sd']}")
    L.append(f"  - 热度灯 {r['hl']}分({r['hls']}): {r['hd']}")
txt = "\n".join(L)
print(txt[:4000])
with open(REPORT, "w", encoding="utf-8") as fh:
    fh.write(txt)
