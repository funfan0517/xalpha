# -*- coding: utf-8 -*-
"""无损合并校验: 逐字段比对两个回测产物 JSONL。

用法: python strategies/_merge_verify.py 基准.jsonl 新版.jsonl [--tol 1e-9]
判定: 只比 ok 行, 按 code 对齐; 数值按相对/绝对容差; dict/list 递归。
      trade_log 里含日期字符串与原顺序, 按序比对（顺序本身也是行为的一部分）。

退出码: 0 = 完全一致; 1 = 存在差异。
"""
import sys

sys.stdout.reconfigure(encoding="utf-8")


def load(path):
    raw = open(path, "rb").read()
    text = raw.decode("utf-16") if raw[:2] in (b"\xff\xfe", b"\xfe\xff") else raw.decode("utf-8")
    import json
    rows = {}
    for ln in text.splitlines():
        ln = ln.strip()
        if not ln.startswith("{"):
            continue
        try:
            r = json.loads(ln)
        except Exception:  # noqa: BLE001
            continue
        if r.get("ok"):
            rows[str(r["code"])] = r
    return rows


def diff(a, b, path, tol, out):
    if isinstance(a, dict) and isinstance(b, dict):
        for k in sorted(set(a) | set(b)):
            if k not in a:
                out.append(f"{path}.{k}: 仅新版有 ({b[k]!r})")
            elif k not in b:
                out.append(f"{path}.{k}: 仅基准有 ({a[k]!r})")
            else:
                diff(a[k], b[k], f"{path}.{k}", tol, out)
    elif isinstance(a, list) and isinstance(b, list):
        if len(a) != len(b):
            out.append(f"{path}: 长度不同 基准 {len(a)} vs 新版 {len(b)}")
        for i in range(min(len(a), len(b))):
            diff(a[i], b[i], f"{path}[{i}]", tol, out)
    elif isinstance(a, bool) or isinstance(b, bool):
        if a != b:
            out.append(f"{path}: {a!r} vs {b!r}")
    elif isinstance(a, (int, float)) and isinstance(b, (int, float)):
        if abs(a - b) > tol and abs(a - b) > tol * max(abs(a), abs(b)):
            out.append(f"{path}: {a!r} vs {b!r} (差 {a - b:+.3e})")
    else:
        if a != b:
            out.append(f"{path}: {a!r} vs {b!r}")


def main():
    args = [a for a in sys.argv[1:] if not a.startswith("--")]
    tol = 1e-9
    only = None
    for i, a in enumerate(sys.argv[1:]):
        if a.startswith("--tol"):
            tol = float(a.split("=", 1)[1] if "=" in a else sys.argv[i + 2])
        elif a == "--only":
            only = set(sys.argv[i + 2].split(","))
    if len(args) < 2:
        sys.exit(__doc__)
    base, new = load(args[0]), load(args[1])
    if only:
        base = {c: {k: v for k, v in r.items() if k in only} for c, r in base.items()}
        new = {c: {k: v for k, v in r.items() if k in only} for c, r in new.items()}
        print(f"仅比对字段: {sorted(only)}")
    print(f"基准 {args[0]}: {len(base)} 行 (ok)")
    print(f"新版 {args[1]}: {len(new)} 行 (ok)")

    only_b = sorted(set(base) - set(new))
    only_n = sorted(set(new) - set(base))
    if only_b:
        print(f"  仅基准有: {only_b}")
    if only_n:
        print(f"  仅新版有: {only_n}")

    bad = 0
    for code in sorted(set(base) & set(new)):
        out = []
        diff(base[code], new[code], code, tol, out)
        if out:
            bad += 1
            print(f"\n[{code}] {len(out)} 处差异:")
            for line in out[:12]:
                print(f"    {line}")
            if len(out) > 12:
                print(f"    ... 另有 {len(out) - 12} 处")

    same = len(set(base) & set(new)) - bad
    print(f"\n==> 逐字段一致的标的: {same} ; 有差异: {bad} ; 容差 {tol:g}")
    print("==> 结论: " + ("完全一致 ✅" if (bad == 0 and not only_b and not only_n)
                        else "存在差异 ❌"))
    return 0 if (bad == 0 and not only_b and not only_n) else 1


if __name__ == "__main__":
    sys.exit(main())
