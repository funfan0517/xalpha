# -*- coding: utf-8 -*-
"""永久组合 · 数据可达性探针(一次性核查, 可重复运行)。

用法: python strategies/permanent/_probe.py
输出: 终端打印 8 类标的的场外净值 / 场内代理日线覆盖区间与条数。
"""
import io
import os
import sys

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8")

_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

import xalpha as xa  # noqa: E402

# (key, 场外执行代码, 场内代理代码 or "")
TARGETS = [
    ("cash", "012265", ""),
    ("bond_mid", "001512", ""),
    ("bond_long", "003377", "511260"),
    ("div_lv", "008163", "515080"),
    ("ndx", "270042", "513100"),
    ("spx", "017641", "513500"),
    ("gold", "000216", "518880"),
    ("a500", "023299", "563360"),
    ("kc", "011609", "588000"),
]


def _sh(code):
    return ("SH" if code.startswith(("5", "6", "9")) else "SZ") + code


def probe_nav(code):
    f = xa.fundinfo(code)
    df = f.price.dropna(subset=["netvalue"]).sort_values("date")
    return len(df), str(df["date"].iloc[0])[:10], str(df["date"].iloc[-1])[:10]


def probe_bars(proxy):
    df = xa.get_daily(_sh(proxy), start="2018-01-01")
    df = df.dropna(subset=["close"]).sort_values("date")
    return len(df), str(df["date"].iloc[0])[:10], str(df["date"].iloc[-1])[:10]


def main():
    print("=" * 78)
    print("[场外净值] xa.fundinfo(code).price")
    for key, off, _ in TARGETS:
        try:
            n, a, b = probe_nav(off)
            print(f"  {key:10s} {off}  n={n:5d}  {a} ~ {b}")
        except Exception as e:  # noqa: BLE001 探针需要打印所有失败
            print(f"  {key:10s} {off}  ERR {type(e).__name__}: {e}")

    print("=" * 78)
    print("[场内代理日线] xa.get_daily(SH/SZ+proxy)")
    for key, _, proxy in TARGETS:
        if not proxy:
            print(f"  {key:10s} (无场内代理)")
            continue
        try:
            n, a, b = probe_bars(proxy)
            print(f"  {key:10s} {proxy}  n={n:5d}  {a} ~ {b}")
        except Exception as e:  # noqa: BLE001
            print(f"  {key:10s} {proxy}  ERR {type(e).__name__}: {e}")


if __name__ == "__main__":
    main()
