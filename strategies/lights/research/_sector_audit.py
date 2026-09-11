# -*- coding: utf-8 -*-
"""按类别审计亮灯策略的表现与**单笔胜率**（回答「买入后多大比例能涨」）。

现有回测产物 strategies/lights/_lights_bt.jsonl 里每只标的都带 trade_log，所以可以精确汇总
「按类别 / 按标的」的合并胜率、盈亏比与单笔期望，而不是只看平均值。

用法: python strategies/lights/_sector_audit.py [产物路径]
"""
import json
import os
import sys

import numpy as np

sys.stdout.reconfigure(encoding="utf-8")
_DIR = os.path.dirname(os.path.abspath(__file__))
_ROOT = os.path.dirname(os.path.dirname(_DIR))          # strategies（同级另一个引用是 _DIR/../backtest）
SRC = sys.argv[1] if len(sys.argv) > 1 else os.path.join(_DIR, "..", "backtest", "_lights_bt.jsonl")


def load(path):
    raw = open(path, "rb").read()
    t = raw.decode("utf-16") if raw[:2] in (b"\xff\xfe", b"\xfe\xff") else raw.decode("utf-8")
    return [json.loads(l) for l in t.splitlines() if l.strip().startswith("{") and json.loads(l).get("ok")]


def pooled(rows):
    rets = np.array([t["ret"] for r in rows for t in (r.get("trade_log") or [])])
    if not len(rets):
        return None
    w, l = rets[rets > 0], rets[rets <= 0]
    return dict(n=len(rets), win=len(w) / len(rets),
                aw=w.mean() if len(w) else 0.0, al=l.mean() if len(l) else 0.0,
                payoff=(w.mean() / abs(l.mean())) if len(l) and l.mean() != 0 else float("inf"),
                expectancy=rets.mean(), med=np.median(rets))


def main():
    rows = load(SRC)
    cats = list(dict.fromkeys(r["cat"] for r in rows))
    order = [c for c in ("宽基/另类", "全球/QDII", "A股行业", "策略/商品", "主动/量化", "债券") if c in cats]
    order += [c for c in cats if c not in order]

    print(f"产物 {os.path.basename(SRC)} · {len(rows)} 只 · 配置口径见产物\n")
    print("== 按类别（单笔口径为各类别内所有标的的所有交易合并）==")
    print(f"{'类别':<10}{'只数':>4}{'单笔数':>7}{'胜率':>8}{'平均盈':>8}{'平均亏':>8}"
          f"{'盈亏比':>7}{'单笔期望':>9}{'中位':>8}{'策略年化':>9}{'基准年化':>9}{'超额':>8}{'持仓':>7}")
    for c in order:
        rs = [r for r in rows if r["cat"] == c]
        p = pooled(rs)
        ann = float(np.mean([r["st_ann"] for r in rs]))
        base = float(np.mean([r["base_ann"] for r in rs]))
        pos = float(np.mean([r["pos_ratio"] for r in rs]))
        if p is None:
            print(f"{c:<10}{len(rs):>4}{'—':>7}")
            continue
        print(f"{c:<10}{len(rs):>4}{p['n']:>7}{p['win'] * 100:>7.1f}%{p['aw'] * 100:>7.2f}%"
              f"{p['al'] * 100:>7.2f}%{p['payoff']:>7.2f}{p['expectancy'] * 100:>8.3f}%"
              f"{p['med'] * 100:>7.2f}%{ann * 100:>8.1f}%{base * 100:>8.1f}%"
              f"{(ann - base) * 100:>7.1f}%{pos * 100:>6.1f}%")

    p = pooled(rows)
    ann = float(np.mean([r["st_ann"] for r in rows]))
    base = float(np.mean([r["base_ann"] for r in rows]))
    print(f"{'全部':<10}{len(rows):>4}{p['n']:>7}{p['win'] * 100:>7.1f}%{p['aw'] * 100:>7.2f}%"
          f"{p['al'] * 100:>7.2f}%{p['payoff']:>7.2f}{p['expectancy'] * 100:>8.3f}%"
          f"{p['med'] * 100:>7.2f}%{ann * 100:>8.1f}%{base * 100:>8.1f}%"
          f"{(ann - base) * 100:>7.1f}%"
          f"{float(np.mean([r['pos_ratio'] for r in rows])) * 100:>6.1f}%")

    sec = [r for r in rows if r["cat"] == "A股行业"]
    if sec:
        print(f"\n== 行业 ETF 子集（{len(sec)} 只）逐只 ==")
        print(f"{'代码':<8}{'主题':<10}{'单笔数':>7}{'胜率':>8}{'盈亏比':>7}{'单笔期望':>9}"
              f"{'策略年化':>9}{'基准年化':>9}{'超额':>8}")
        for r in sorted(sec, key=lambda x: -x["t_stats"]["win_rate"]):
            ts, tl = r["t_stats"], r.get("trade_log") or []
            exp = float(np.mean([t["ret"] for t in tl])) if tl else 0.0
            payoff = ts["payoff"]
            pf = "∞" if payoff == float("inf") else f"{payoff:.2f}"
            print(f"{r['code']:<8}{r['theme']:<10}{ts['n']:>7}{ts['win_rate'] * 100:>7.1f}%"
                  f"{pf:>7}{exp * 100:>8.3f}%{r['st_ann'] * 100:>8.1f}%"
                  f"{r['base_ann'] * 100:>8.1f}%{(r['st_ann'] - r['base_ann']) * 100:>7.1f}%")


if __name__ == "__main__":
    main()
