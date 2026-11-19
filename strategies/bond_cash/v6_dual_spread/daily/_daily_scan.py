# -*- coding: utf-8 -*-
"""V6 · 双利差锚 每日扫描 → daily/_v6_daily.{md,json}。

用法: python strategies/bond_cash/v6_dual_spread/daily/_daily_scan.py [--force]
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
OUT_MD = os.path.join(_HERE, "_v6_daily.md")
OUT_JSON = os.path.join(_HERE, "_v6_daily.json")


def render(ctx):
    c, sl, cr, s = ctx["carry"], ctx["slope"], ctx["credit"], ctx["signal"]
    L = []
    A = L.append
    A("# V6 · 双利差锚（期限利差定久期 × 信用利差定品种）")
    A("")
    A(f"> 生成 {datetime.now():%Y-%m-%d %H:%M} · 数据截止 **{ctx['as_of']}** · "
      "信号源 = 中债收益率曲线 · 月末调仓")
    A("")
    A("| 轴 | 票 | 读数 | 投票 | 规则 |")
    A("|---|---|---:|---:|---|")
    A(f"| 久期 | Carry | 10Y−1Y = {c['spread']}%（3年分位 {c['pct']}%） | {c['vote']:+d} | {c['rule']} |")
    A(f"| 久期 | Slope | Δ(10Y−1Y) 60日 = {sl['ds_bp']}bp | {sl['vote']:+d} | {sl['rule']} |")
    A(f"| 品种 | Credit | Δ(AAA3Y−国债3Y) 60日 = {cr['dcs_bp']}bp | {cr['vote']:+d} | {cr['rule']} |")
    A(f"| **久期合计** | score = carry + slope | | **{s['score']:+d}** | "
      f"→ 长端目标 **{s['long_w']:.1f}%** |")
    A(f"| **品种结果** | 底仓 | {cr['leg_name']} `{cr['leg']}` | | 信用 {'收窄→信用债' if cr['vote'] > 0 else ('走阔→利率债' if cr['vote'] < 0 else '中间→维持')} |")
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
    A("> 规则来源：国信证券《基于双利差锚的周期划分和债券投资策略》(2021) + "
      "国泰海通《债券择时从多空到轮动：信用—利率与久期的双层策略》(2026)。")
    A("")
    A("> 机械规则输出，仅供研究参考，不构成投资建议。")
    return "\n".join(L)


def card_rows(ctx):
    s, cr = ctx["signal"], ctx["credit"]
    rows = []
    for r in ctx["rows"]:
        rows.append(dict(code=r["code"], name=r["name"], short=r["name"],
                         act=r["act"], label=r["label"], pp=0, nav=r["nav"],
                         r20=None, r250=None, note=r["note"], role=r["role"],
                         theme=r["theme"]))
    rows.append(dict(code="票", name="双利差锚", short="票", act="none",
                     label=f"{s['score']:+d}", pp=0, nav=None, r20=None, r250=None,
                     note=f"carry {ctx['carry']['vote']:+d} / slope {ctx['slope']['vote']:+d} "
                          f"→ 长端 {s['eff']:.1f}% · 底仓 {cr['leg_name']}",
                     role="信号", theme="信号"))
    return rows


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--force", action="store_true")
    args = ap.parse_args(argv)
    navs, asof, failed = data.load_all(force=args.force)
    curve = data.load_curve()
    if curve.empty:
        print("[error] 缺曲线缓存（先跑 v1 daily）")
        return 1
    ctx = engine.evaluate(navs, curve, asof=max(asof.values()) if asof else None)
    ctx["sources"] = {**asof, "曲线": str(curve.index[-1].date())}

    os.makedirs(_HERE, exist_ok=True)
    with open(OUT_MD, "w", encoding="utf-8") as f:
        f.write(render(ctx) + "\n")
    snap = dict(strategy="V6 · 双利差锚", as_of=ctx["as_of"],
                generated=datetime.now().strftime("%Y-%m-%d %H:%M"),
                source_methodology="国信证券双利差锚 (2021) + 国泰海通信用-利率双层轮动 (2026)",
                carry=ctx["carry"], slope=ctx["slope"], credit=ctx["credit"],
                signal=ctx["signal"], held=ctx["held"], rows=card_rows(ctx),
                sources=ctx["sources"])
    with open(OUT_JSON, "w", encoding="utf-8") as f:
        json.dump(snap, f, ensure_ascii=False, indent=2)
    print(f"[ok] {OUT_MD}")
    print(f"[ok] {OUT_JSON}")
    print(f"  as_of {ctx['as_of']} · carry {ctx['carry']['vote']:+d} / slope "
          f"{ctx['slope']['vote']:+d} / credit {ctx['credit']['vote']:+d} → "
          f"长端 {ctx['signal']['eff']:.1f}% · 底仓 {ctx['credit']['leg']} · "
          f"{ctx['signal']['act']}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
