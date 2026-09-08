"""汇总 _cross_out.jsonl 扫描结果，生成可读报告。"""
import io
import json
import sys

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8")

OUT = "g:/xalpha/data/_cross_out.jsonl"
REPORT = "g:/xalpha/data/_scan_report.txt"

rows = []
raw = open(OUT, "rb").read()
if raw.startswith((b"\xff\xfe", b"\xfe\xff")):
    text = raw.decode("utf-16")
else:
    text = raw.decode("utf-8")
for ln in text.splitlines():
    ln = ln.strip()
    if '"idx"' not in ln:
        continue
    try:
        rows.append(json.loads(ln))
    except json.JSONDecodeError:
        continue

rows.sort(key=lambda r: r["idx"])
missing = [i for i in range(1, 53) if i not in {r["idx"] for r in rows}]

lines = []
lines.append("=" * 100)
lines.append("场外基金 MA20/MA60 金叉死叉扫描结果")
lines.append(f"共 {len(rows)} 只  缺失: {missing or '无'}  信号日基准: 最新净值")
lines.append("=" * 100)
lines.append("信号规则: MA20上穿MA60=买入 | MA20下穿MA60=卖出 | MA20在MA60上方=持有 | 下方=观望 | 净值不足60期=数据不足")
lines.append("")

def fmt(r):
    if not r.get("ok"):
        return f"  #{r['idx']:>2} {r['theme']:<8} {r['code']}   FAIL: {r.get('err')}"
    if r["action"] == "数据不足":
        return (f"  #{r['idx']:>2} [{r['action']}] {r['theme']:<8} {r['code']} "
                f"{r['name'][:18]:<20} {r.get('note', '')}  净值@{r['latest']}")
    pos = ">MA20" if r["above20"] else "<MA20"
    return (f"  #{r['idx']:>2} [{r['action']}] {r['theme']:<8} {r['code']} "
            f"MA20/60 {r['gap_pct']:+.2f}% 净{pos}  "
            f"NAV={r['nav']:.4f}@{r['latest']}(滞后{r['lag']}天)")

cur_cat = None
stats = {}
for r in rows:
    if r["cat"] != cur_cat:
        cur_cat = r["cat"]
        lines.append(f"■ {cur_cat}")
    lines.append(fmt(r))
    a = r["action"] if r.get("ok") else "FAIL"
    stats[a] = stats.get(a, 0) + 1

lines.append("")
lines.append("-" * 100)
lines.append("信号分布: " + "  ".join(f"{k}={v}" for k, v in stats.items()))
txt = "\n".join(lines)
print(txt)
with open(REPORT, "w", encoding="utf-8") as fh:
    fh.write(txt)
