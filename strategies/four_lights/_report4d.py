"""汇总场內四灯扫描结果 _inner_out.jsonl -> _inner_report.md"""
import io
import json
import sys

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8")

OUT = "g:/xalpha/data/_inner_out.jsonl"
REPORT = "g:/xalpha/strategies/four_lights/_inner_report.md"

I2N = {
    "563360": "A500ETF华泰柏瑞", "588000": "科创50ETF华夏", "512800": "银行ETF华宝",
    "512200": "房地产ETF南方", "513100": "纳指ETF国泰", "513500": "标普500ETF博时",
    "513030": "德国ETF华安", "513880": "日经225ETF华安", "513180": "恒生科技ETF华夏",
    "501025": "香港银行LOF", "512480": "半导体ETF国联安", "515230": "软件ETF国泰",
    "515880": "通信ETF国泰", "159819": "人工智能ETF易方达", "159732": "消费电子ETF华夏",
    "562500": "机器人ETF华夏", "512660": "军工ETF国泰", "159698": "粮食ETF鹏华",
    "159869": "游戏ETF华夏", "512980": "传媒ETF广发", "515220": "煤炭ETF国泰",
    "512000": "券商ETF华宝", "161725": "白酒基金LOF", "159928": "消费ETF汇添富",
    "516160": "新能源ETF南方", "515790": "光伏ETF华泰柏瑞", "159326": "电网设备ETF华夏",
    "159870": "化工ETF鹏华", "512400": "有色金属ETF南方", "512010": "医药ETF易方达",
    "159992": "创新药ETF银华", "159201": "自由现金流ETF华夏",     "161226": "国投白银LOF",
    "518880": "黄金ETF华安",
    "515080": "中证红利ETF",
}

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
L = []
L.append("# 场外基金 → 场内对应标的 · 四灯共振「今日信号」")
L.append("")
L.append("> **标的池**：唯一池 `data/_universe.md`（场外 56 只）中筛出 **37 只「有场内对应标的」**（场内对应列，由 `pipeline/universe.py` 派生）")
L.append("> **规则**：`四灯共振短线选股方法论` §5 —— 趋势灯 / 主力灯 / 持续力灯 / 热度灯，每灯 亮2/偏多1/灭0，总分 0–8")
L.append("> **数据**：场内日线=实抓至 2026-09-07；主力净流入/换手率/量比=本地 mx 快照 2026-09-07 14:09（仅覆盖场内池 34 只，LOF 走量价代理）")
L.append("> **决策**(持仓视角)：总分≥6且趋势/主力≥1=买入 | 总分5且核心≥1=持有 | 总分≤3=卖出 | 其余=观望")
L.append("> **⚠️ 未持有标的的「卖出」= 不建议此时建仓；机械规则输出，非投资建议。**")
L.append("")
L.append("## 决策总览")
L.append("")
L.append("| # | 主题 | 场内标的 | 今日% | 5日% | 趋势 | 主力 | 持续 | 热度 | 总分 | 决策 |")
L.append("|---|---|---|---|---|---|---|---|---|---|---|")
for r in rows:
    L.append(f"| {r['idx']} | {r['theme']} | {I2N.get(r['inner'], r['inner'])}"
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
        L.append(f"| {r['idx']} | {r['theme']} | {r['off_name']} | {I2N.get(r['inner'], r['inner'])}"
                 f" | {r['total']} | T{r['tls']}/C{r['cls']}/S{r['sls']}/H{r['hls']} | {r['note']} |")
    L.append("")
L.append("## 灯细节与代理说明")
L.append("")
for r in rows:
    L.append(f"- **#{r['idx']} {r['theme']}** `{I2N.get(r['inner'], r['inner'])}` 总分{r['total']} → {r['dec']}")
    L.append(f"  - 趋势灯 {r['tl']}分({r['tls']}): {r['td']}")
    L.append(f"  - 主力灯 {r['cl']}分({r['cls']}): {r['cd']}{'' if r['has_main'] else '（无快照，量价代理）'}")
    L.append(f"  - 持续力灯 {r['sl']}分({r['sls']}): {r['sd']}")
    L.append(f"  - 热度灯 {r['hl']}分({r['hls']}): {r['hd']}")
txt = "\n".join(L)
print(txt[:4000])
with open(REPORT, "w", encoding="utf-8") as fh:
    fh.write(txt)
