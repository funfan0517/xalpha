# -*- coding: utf-8 -*-
"""V2 · 横截面双动量轮动 每日扫描。

产物 ``daily/_v2_daily.md`` / ``_v2_daily.json``。
只在月度最后一个交易日产生新持仓；其余日子读上一次调仓结果（不日内交易）。

用法: python strategies/bond_cash/v2_cross_momentum/daily/_daily_scan.py [--force]
"""
import argparse
import io
import json
import os
import sys
from datetime import datetime

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8")

_HERE = os.path.dirname(os.path.abspath(__file__))
_PKG = os.path.dirname(_HERE)
for _p in (os.path.dirname(_PKG), _PKG, _HERE):
    while _p in sys.path:
        sys.path.remove(_p)
    sys.path.insert(0, _p)

import rule      # noqa: E402
import data      # noqa: E402
import engine    # noqa: E402

CFG = rule.CFG
OUT_MD = os.path.join(_HERE, "_v2_daily.md")
OUT_JSON = os.path.join(_HERE, "_v2_daily.json")


def render(ctx):
    m, h = ctx["momentum"], ctx["held"]
    L, A = [], None
    A = L.append
    A("# V2 · 横截面双动量轮动")
    A("")
    A(f"> 生成 {datetime.now():%Y-%m-%d %H:%M} · 数据截止 **{ctx['as_of']}** · "
      f"上次调仓 {ctx['last_rebalance']['date']} · 下次调仓 {ctx['next_rebalance']}")
    A("")
    A(f"规则：月度对 {len(CFG['universe'])} 只候选按近 **{m['win']}** 个交易日累计收益排名，"
      f"持前 **{m['top_n']}** 名等权；最强者动量 ≤{m['abs_floor'] * 100:.0f}% "
      f"→ 全切防御 `{m['defensive']}`。")
    A("")
    A("| # | 代码 | 标的 | 近%d日动量 | 债腿占比 | 状态 |" % m["win"])
    A("|---|---|---|---:|---:|---|")
    for r in ctx["rows"][:len(CFG["universe"])]:
        A(f"| {r['num']} | `{r['code']}` | {r['name']} | "
          f"{('%+.2f%%' % r['r']) if r['r'] is not None else '—'} | {r['w']:.1f}pp | "
          f"{'**入选**' if r['w'] > 0 else '未入选'} |")
    A("")
    A(f"> {ctx['last_rebalance']['why']}")
    A("")
    A("## 现金腿（与 v0/v1 一致）")
    A("")
    for r in ctx["rows"][len(CFG["universe"]):]:
        A(f"- `{r['code']}` {r['name']}：**{r['w']:.1f}pp**")
    A("")
    A("> 机械规则输出，仅供研究参考，不构成投资建议。")
    return "\n".join(L)


def card_rows(ctx):
    m = ctx["momentum"]
    rows = []
    for r in ctx["rows"]:
        rows.append(dict(code=r["code"], name=r["name"], short=r["name"], act=r["act"],
                         label=r["label"], pp=0, nav=r["nav"], r20=r["r"], r250=None,
                         note=(f"近{m['win']}日动量 " +
                               (f"{r['r']:+.2f}%" if r["r"] is not None else "—") +
                               " · " + r["note"]), role=r["role"], theme=r["theme"]))
    return rows


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--force", action="store_true")
    args = ap.parse_args(argv)

    navs, asof, failed = data.load_all(force=args.force)
    if "003376" not in navs or CFG["defensive"] not in navs:
        print("[error] 缺核心标的净值，无法出信号")
        return 1
    ctx = engine.evaluate(navs, asof=max(asof.values()) if asof else None)
    ctx["sources"] = asof

    os.makedirs(_HERE, exist_ok=True)
    with open(OUT_MD, "w", encoding="utf-8") as f:
        f.write(render(ctx) + "\n")
    snap = dict(strategy="V2 · 横截面双动量轮动", as_of=ctx["as_of"],
                generated=datetime.now().strftime("%Y-%m-%d %H:%M"),
                source_methodology="Bond ETF Dual Momentum / MDPI FinTech 2026 Median strategy（对照）",
                momentum=ctx["momentum"], held=ctx["held"],
                last_rebalance=ctx["last_rebalance"], next_rebalance=ctx["next_rebalance"],
                rows=card_rows(ctx), sources=asof)
    with open(OUT_JSON, "w", encoding="utf-8") as f:
        json.dump(snap, f, ensure_ascii=False, indent=2)

    print(f"[ok] {OUT_MD}")
    print(f"[ok] {OUT_JSON}")
    print(f"  as_of {ctx['as_of']} · 上次调仓 {ctx['last_rebalance']['date']} · "
          f"持仓 {ctx['held']}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
