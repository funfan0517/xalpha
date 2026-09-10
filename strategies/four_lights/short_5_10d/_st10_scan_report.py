# -*- coding: utf-8 -*-
"""四灯 5-10 天短线变种 · 今日信号报告: data/_st10_scan_out.jsonl -> md + html。"""
import io
import json
import os
import sys

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8")

_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
OUT = os.path.join(_ROOT, "data", "_st10_scan_out.jsonl")
MD = "g:/xalpha/strategies/four_lights/short_5_10d/_st10_scan_report.md"
HTML = "g:/xalpha/strategies/four_lights/short_5_10d/_st10_scan_dashboard.html"

rows = []
for ln in open(OUT, encoding="utf-8"):
    if '"ok"' not in ln:
        continue
    try:
        rows.append(json.loads(ln))
    except json.JSONDecodeError:
        pass

ok = [r for r in rows if r.get("ok")]
bad = [r for r in rows if not r.get("ok")]
order = {"买入": 0, "持有": 1, "观望": 2, "卖出": 3}
ok.sort(key=lambda r: (order.get(r["dec"], 9), -r["s"], r["idx"]))

max_date = max((r["date"] for r in ok), default="-")
snap = max((r.get("snap") or "" for r in ok), default="")
n_main = sum(1 for r in ok if r["has_main"])
n_veto = sum(1 for r in ok if r.get("turn_veto"))
grp = {}
for r in ok:
    grp.setdefault(r["dec"], []).append(r)

L = []
L.append("# 四灯 5-10 天短线变种 · 今日信号")
L.append("")
L.append(f"> **标的池**：`data/_universe.md` 派生内池（有场内对应）共 **{len(ok)} 只**（失败 {len(bad)} 只）")
L.append("> **规则**：L1 双均线 MA20/60 + 绝对动量趋势门 → L2 三维短线（动量/主力/换手）→ 多档仓位；出场=MA死叉/动量转负/回撤止损/趋势转熊")
L.append(f"> **数据**：日线=实抓至 **{max_date}**；主力净流入/换手率/量比=本地 mx 快照 **{snap}**（{n_main}/{len(ok)} 只有真实快照，其余走量价代理）")
L.append("> **决策**(持仓视角)：买入=满足开仓硬条件(次日开盘建仓) | 持有=趋势尚可但短线条件不足 | 卖出=触发离场信号 | 观望=无买点")
L.append("> **⚠️ 未持有标的的「卖出」= 不建议此时建仓；机械规则输出，非投资建议。**")
L.append("")
L.append("## 决策总览")
L.append("")
L.append("| # | 主题 | 场内标的 | 今日% | 5日% | 趋势 | 动量 | 主力 | 换手 | 总分 | 目标仓位 | 决策 |")
L.append("|---|---|---|---|---|---|---|---|---|---|---|---|")
for r in ok:
    c_s = f"{r['c']}{'' if r['has_main'] else 'ᵖ'}"
    w_s = f"{r['weight']:.0%}" if r["weight"] else "—"
    L.append(f"| {r['idx']} | {r['theme']} | {r['inner_name']} | {r['today']:+.1f} | {r['mom5']:+.1f}"
             f" | {r['trend']} | {r['m']} | {c_s} | {r['h']} | **{r['s']}** | {w_s} | **{r['dec']}** |")
L.append("")
L.append("> 主力列: `ᵖ`=无快照走量价代理；换手列已计入真实换手率 >20% 的爆量出货否决。")
L.append("")

L.append("## 决策分组")
L.append("")
for dec in ["买入", "持有", "观望", "卖出"]:
    sub = grp.get(dec, [])
    if not sub:
        continue
    L.append(f"### {dec}（{len(sub)} 只）")
    L.append("")
    L.append("| # | 主题 | 场内标的 | 总分 | 灯状态 | 触发依据 |")
    L.append("|---|---|---|---|---|---|")
    for r in sub:
        L.append(f"| {r['idx']} | {r['theme']} | {r['inner_name']} | {r['s']}"
                 f" | T{r['trend']}/M{r['m']}/C{r['c']}/H{r['h']} | {r['note']} |")
    L.append("")

L.append("## 明细与代理说明")
L.append("")
for r in ok:
    L.append(f"- **#{r['idx']} {r['theme']}** `{r['inner_name']}` 总分{r['s']} → {r['dec']}")
    L.append(f"  - 趋势门 {r['trend']}档 | 近端动量 m={r['m']}(5日{r['mom5']:+.1f}%/3日{r['mom3']:+.1f}%)")
    main_s = (f"真实主力净占比{r['main_pct']:+.1f}% → c={r['c']}" if r["has_main"]
              else f"量价代理 c={r['c']}(无快照)")
    L.append(f"  - 主力(加分) {main_s}")
    turn_s = f"真实换手{r['turn']:.2f}%" if r.get("turn") is not None else "换手N/A"
    L.append(f"  - 换手/热度 h={r['h']}({turn_s}{'; 爆量否决' if r.get('turn_veto') else ''})")
if bad:
    L.append("")
    L.append("## 失败/样本不足")
    L.append("")
    for r in bad:
        L.append(f"- {r.get('theme', '?')} `{r.get('inner', '?')}`: {r.get('err')}")

txt = "\n".join(L)
with open(MD, "w", encoding="utf-8") as fh:
    fh.write(txt)

esc = txt.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
html = f"""<!DOCTYPE html><html lang="zh"><head><meta charset="utf-8">
<title>四灯5-10天短线 · 今日信号</title>
<style>
body{{font-family:"Microsoft YaHei",sans-serif;background:#f5f6fa;margin:24px;color:#222}}
pre{{background:#fff;padding:16px;border-radius:10px;overflow:auto;font-size:13px;line-height:1.6}}
</style></head><body>
<h1>四灯 5-10 天短线变种 · 今日信号</h1>
<pre>{esc}</pre>
</body></html>"""
open(HTML, "w", encoding="utf-8").write(html)

print("\n".join(L[:46]))
print(f"... 共 {len(ok)} 只; 决策分布 {[(k, len(v)) for k, v in sorted(grp.items(), key=lambda x: order.get(x[0], 9))]}")
print(f"MD/HTML 已写入 strategies/four_lights/short_5_10d/")
