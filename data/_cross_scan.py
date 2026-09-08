"""对场外基金池执行 MA20/MA60 金叉死叉扫描，输出每只标的的信号。

用法:
  python _cross_scan.py            # 扫描全部
  python _cross_scan.py 023299,001595   # 只扫描指定代码
每条结果一行 JSON 打印到 stdout。

信号规则（基于净值 netvalue）:
  MA20 上穿 MA60(今日金叉) -> 买入
  MA20 下穿 MA60(今日死叉) -> 卖出
  金叉死叉之间且 MA20>MA60   -> 持有
  金叉死叉之间且 MA20<MA60   -> 观望
  历史不足 60 个净值日        -> 数据不足
"""
import io
import json
import re
import sys

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8")

import pandas as pd

import xalpha as xa

MD = "g:/xalpha/data/_universe.md"


def parse_universe(path):
    items = []
    cat = ""
    for line in open(path, encoding="utf-8"):
        line = line.rstrip("\n")
        m = re.match(r"^##\s+(.+)$", line)
        if m:
            cat = m.group(1).strip()
            continue
        m = re.match(
            r"^\|\s*(\d+)\s*\|\s*([^|]+?)\s*\|\s*(\d{6})\s*\|\s*([^|]+?)\s*\|", line
        )
        if m:
            items.append(
                {
                    "idx": int(m.group(1)),
                    "cat": cat,
                    "theme": m.group(2).strip(),
                    "code": m.group(3),
                    "name": m.group(4).strip(),
                }
            )
    return items


def analyze(code, name):
    try:
        f = xa.fundinfo(code, priceonly=True)
    except Exception as e:
        return {"ok": False, "err": f"{type(e).__name__}: {e}"}

    p = f.price[["date", "netvalue"]].dropna().reset_index(drop=True)
    if p.empty:
        return {"ok": False, "err": "无净值数据"}

    latest = p.iloc[-1]
    last_date = pd.Timestamp(latest["date"]).date()
    nav = float(latest["netvalue"])
    today = pd.Timestamp.now().date()
    lag = (today - last_date).days

    base = {"ok": True, "name": getattr(f, "name", name), "latest": str(last_date),
            "nav": nav, "lag": lag, "rows": len(p)}

    if len(p) < 61:
        base.update({"action": "数据不足", "note": f"仅 {len(p)} 个净值日，不足以计算 MA60"})
        return base

    p["ma20"] = p["netvalue"].rolling(20).mean()
    p["ma60"] = p["netvalue"].rolling(60).mean()
    d = p.dropna(subset=["ma60"]).reset_index(drop=True)
    diff = d["ma20"] - d["ma60"]
    prev_d, cur_d = diff.iloc[-2], diff.iloc[-1]
    ma20, ma60 = float(d["ma20"].iloc[-1]), float(d["ma60"].iloc[-1])

    if prev_d <= 0 < cur_d:
        action = "买入"
    elif prev_d >= 0 > cur_d:
        action = "卖出"
    elif cur_d > 0:
        action = "持有"
    else:
        action = "观望"

    base.update({
        "action": action,
        "ma20": round(ma20, 4),
        "ma60": round(ma60, 4),
        "gap_pct": round((ma20 / ma60 - 1) * 100, 2),
        "above20": float(nav) > ma20,
    })
    return base


def main():
    universe = parse_universe(MD)
    want = sys.argv[1].split(",") if len(sys.argv) > 1 and sys.argv[1] else None
    for it in universe:
        if want and it["code"] not in want:
            continue
        r = analyze(it["code"], it["name"])
        out = {"idx": it["idx"], "cat": it["cat"], "theme": it["theme"],
               "code": it["code"], **r}
        print(json.dumps(out, ensure_ascii=False), flush=True)


if __name__ == "__main__":
    main()
