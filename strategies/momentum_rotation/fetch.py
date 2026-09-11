# -*- coding: utf-8 -*-
"""数据抓取(分批): 场内池日线 close -> json
用法: python strategies/momentum_rotation/fetch.py [codes] [start=2015-01-01] [out=cache默认]
示例(十年库): python .../fetch.py "512800,..." 2015-01-01 <仓库根>/data/_long_klines.json
重复运行按 code 增量合并(已存在且长度>=1000 跳过)。
"""
import io
import json
import os
import sys

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8")

_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

import xalpha as xa
from pipeline import universe

CACHE = os.path.join(_ROOT, "data", "_long_klines.json")
START = "2015-01-01"


def sh(code):
    return ("SH" if code.startswith(("5", "6", "9")) else "SZ") + code


def main():
    want = sys.argv[1].split(",") if len(sys.argv) > 1 and sys.argv[1] else None
    start = sys.argv[2] if len(sys.argv) > 2 else START
    out = sys.argv[3] if len(sys.argv) > 3 else CACHE
    cache = {}
    if os.path.exists(out):
        cache = json.load(open(out, encoding="utf-8"))
    codes = want or universe.inner_codes()  # 未指定时按唯一池场内代码
    for code in codes:
        if want and code in cache and len(cache[code]["close"]) >= 1000:
            continue
        try:
            df = xa.get_daily(sh(code), start=start)
            df = df.dropna(subset=["close", "open"])
            df["date"] = df["date"].astype(str)
            cache[code] = {"dates": df["date"].tolist(), "close": df["close"].tolist()}
            print(f"{code}: {len(cache[code]['close'])} bars, first {cache[code]['dates'][0]}, last {cache[code]['dates'][-1]}")
        except Exception as e:
            print(f"{code}: FAIL {type(e).__name__} {e}")
    json.dump(cache, open(out, "w", encoding="utf-8"), ensure_ascii=False)
    print(f"cache -> {out}, {len(cache)} codes")


if __name__ == "__main__":
    main()
