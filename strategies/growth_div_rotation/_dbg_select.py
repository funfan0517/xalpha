import json, os
ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
BT = os.path.join(ROOT, "strategies", "growth_div_rotation", "backtest", "_gd_bt.jsonl")
MIN_YEARS = 3.0
raw = open(BT, "rb").read()
text = raw.decode("utf-8") if raw[:2] not in (b"\xff\xfe", b"\xfe\xff") else raw.decode("utf-16")
rows = []
for ln in text.splitlines():
    if '"ok"' not in ln:
        continue
    try:
        r = json.loads(ln)
        if r.get("ok"):
            rows.append(r)
    except json.JSONDecodeError as e:
        print("JSON ERR:", e)
print("rows:", len(rows))
for r in rows:
    y = r.get("years", 0)
    ex = r.get("st_ann", 0) - r.get("base_ann", 0)
    print(f"years={y} (<3? {y < MIN_YEARS}) st_ann={r.get('st_ann')} base_ann={r.get('base_ann')} excess={ex} -> ",
          "A" if (not (y < MIN_YEARS) and ex > 0 and r.get("st_ann") > 0) else "NA/B")
