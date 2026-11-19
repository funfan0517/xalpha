# -*- coding: utf-8 -*-
"""V4 · 股债对冲三票 每日扫描 → daily/_v4_daily.{md,json}。

用法: python strategies/bond_cash/v4_stock_hedge/daily/_daily_scan.py [--force]
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
OUT_MD = os.path.join(_HERE, "_v4_daily.md")
OUT_JSON = os.path.join(_HERE, "_v4_daily.json")


def render(ctx):
    c, t, h, s = ctx["carry"], ctx["trend"], ctx["hedge"], ctx["signal"]
    L = []
    A = L.append
    A("# V4 · 股债对冲三票（久期择时）")
    A("")
    A(f"> 生成 {datetime.now():%Y-%m-%d %H:%M} · 数据截止 **{ctx['as_of']}** · "
      "信号源 = 中债国债曲线 + 沪深300 指数 · 月末调仓")
    A("")
    A("| 票 | 读数 | 投票 | 规则 |")
    A("|---|---:|---:|---|")
    A(f"| Carry | 10Y−1Y = {c['spread']}%（3年分位 {c['pct']}%） | {c['vote']:+d} | {c['rule']} |")
    A(f"| Trend | 10Y 国债 MA5/MA20/MA60 | {t['vote']:+d} | {t['rule']} |")
    A(f"| Hedge | 沪深300 252日 {(h['stock_ret'] or 0):.2%}（5年分位 {h['stock_pct']}%） | {h['vote']:+d} | "
      f"{h['rule']} |")
    A(f"| **合计** | score = carry + trend + hedge | **{s['score']:+d}** | "
      f"→ 长端目标 **{s['long_w']:.1f}%** |")
    A("")
    if s.get("is_rebalance"):
        A(f"- 当前长端 **{s['cur']:.1f}%** → 今日调仓目标 **{s['long_w']:.1f}%** "
          f"（滞后带 {CFG['hysteresis_pp']:.1f}pp → **{'触发' if s['moved'] else '不触发'}**）"
          f" → 今日 **{s['act']}**")
    else:
        A(f"- 当前长端 **{s['cur']:.1f}%**（上一调仓日决定）· 今日信号对应目标 "
          f"**{s['long_w']:.1f}%** → **今日不动手**（月末才调仓）")
        if s.get("pending") is not None:
            A(f"- **下个调仓日将调整到 {s['pending']:.1f}%**（若届时信号不变）")
    A(f"- 下次调仓：{s['next_rebalance']}")
    A("")
    A("| 腿 | 标的 | 代码 | 占总资产 |")
    A("|---|---|---|---:|")
    for r in ctx["rows"]:
        A(f"| {r['role']} | {r['name']} | `{r['code']}` | **{r['pp']:.2f}pp** |")
    A("")
    A("> 规则来源：Newfound Research (2018) carry-trend-hedge 三票框架 + 国泰海通 (2026-06) "
      "股债增强配置；Hedge 票用沪深300 指数动量分位的极端值，不含分红口径。")
    A("")
    A("> 机械规则输出，仅供研究参考，不构成投资建议。")
    return "\n".join(L)


def card_rows(ctx):
    s = ctx["signal"]
    rows = []
    for r in ctx["rows"]:
        rows.append(dict(code=r["code"], name=r["name"], short=r["name"],
                         act=r["act"], label=r["label"], pp=0, nav=r["nav"],
                         r20=None, r250=None, note=r["note"], role=r["role"],
                         theme=r["theme"]))
    rows.append(dict(code="票", name="Carry+Trend+Hedge", short="票", act="none",
                     label=f"{s['score']:+d}", pp=0, nav=None, r20=None, r250=None,
                     note=f"carry {ctx['carry']['vote']:+d} / trend {ctx['trend']['vote']:+d} / "
                          f"hedge {ctx['hedge']['vote']:+d} → 长端 {s['eff']:.1f}%",
                     role="信号", theme="信号"))
    return rows


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--force", action="store_true")
    args = ap.parse_args(argv)
    navs, asof, failed = data.load_all(force=args.force)
    curve = data.load_curve()
    stock = data.load_stock()
    ctx = engine.evaluate(navs, curve, stock, asof=max(asof.values()) if asof else None)
    ctx["sources"] = {**asof,
                      "曲线": str(curve.index[-1].date()) if len(curve) else "缺",
                      "沪深300": str(stock.index[-1].date()) if len(stock) else "缺"}

    os.makedirs(_HERE, exist_ok=True)
    with open(OUT_MD, "w", encoding="utf-8") as f:
        f.write(render(ctx) + "\n")
    snap = dict(strategy="V4 · 股债对冲三票", as_of=ctx["as_of"],
                generated=datetime.now().strftime("%Y-%m-%d %H:%M"),
                source_methodology="Newfound Research (2018) carry-trend-hedge + 国泰海通 (2026-06) 股债增强",
                carry=ctx["carry"], trend=ctx["trend"], hedge=ctx["hedge"], signal=ctx["signal"],
                held=ctx["held"], rows=card_rows(ctx), sources=ctx["sources"])
    with open(OUT_JSON, "w", encoding="utf-8") as f:
        json.dump(snap, f, ensure_ascii=False, indent=2)
    print(f"[ok] {OUT_MD}")
    print(f"[ok] {OUT_JSON}")
    print(f"  as_of {ctx['as_of']} · carry {ctx['carry']['vote']:+d} / trend "
          f"{ctx['trend']['vote']:+d} / hedge {ctx['hedge']['vote']:+d} → "
          f"score {ctx['signal']['score']:+d} · 长端 {ctx['signal']['eff']:.1f}% · "
          f"{ctx['signal']['act']}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
