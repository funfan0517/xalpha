# -*- coding: utf-8 -*-
"""V5 · 波动率目标 每日扫描 → daily/_v5_daily.{md,json}。

用法: python strategies/bond_cash/v5_vol_target/daily/_daily_scan.py [--force]
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
OUT_MD = os.path.join(_HERE, "_v5_daily.md")
OUT_JSON = os.path.join(_HERE, "_v5_daily.json")


def render(ctx):
    v, s = ctx["vol"], ctx["signal"]
    L = []
    A = L.append
    A("# V5 · 波动率目标（久期敞口按风险预算缩放）")
    A("")
    A(f"> 生成 {datetime.now():%Y-%m-%d %H:%M} · 数据截止 **{ctx['as_of']}** · "
      "信号源 = 长端腿 003376 的已实现波动（不预测方向）· 月末调仓")
    A("")
    A("| 项 | 读数 |")
    A("|---|---:|")
    A(f"| σ21（年化） | {v['sigma21']:.2%} |" if v.get("sigma21") is not None else "| σ21 | — |")
    A(f"| σ63（年化） | {v['sigma63']:.2%} |" if v.get("sigma63") is not None else "| σ63 | — |")
    A(f"| σ = max(σ21, σ63) | **{v['sigma']:.2%}** |" if v.get("sigma") is not None else "| σ | — |")
    A(f"| 目标波动 | {v['target']:.2%} |")
    A(f"| σ / 目标 | **{(v.get('ratio') or 0):.2f}×** |")
    A(f"| 熔断（σ > {CFG['vol']['guard_mult']:.0f}× 目标） | {'**是**（长端清零）' if v['guard'] else '否'} |")
    A("")
    A(f"- 规则：{v['rule']}")
    A(f"- 目标长端 **{s['w_raw']:.1f}%** · 当前 **{s['cur']:.1f}%** → 生效 **{s['eff']:.1f}%**")
    if s.get("is_rebalance"):
        A(f"- 今日是调仓日，滞后带 {CFG['hysteresis_pp']:.1f}pp → "
          f"**{'触发' if s['moved'] else '不触发'}** → 今日 **{s['act']}**")
    else:
        A("- **今日不动手**（月末才调仓）")
        if s.get("pending") is not None:
            A(f"- **下个调仓日将调整到 {s['pending']:.1f}%**（若届时波动不变）")
    A(f"- 下次调仓：{s['next_rebalance']}")
    A("")
    A("| 腿 | 标的 | 代码 | 占总资产 |")
    A("|---|---|---|---:|")
    for r in ctx["rows"]:
        A(f"| {r['role']} | {r['name']} | `{r['code']}` | **{r['pp']:.2f}pp** |")
    A("")
    A("> 规则来源：Moreira & Muir (2017) volatility-managed portfolios + "
      "目标波动率资产组合管理（中国股/债/商品 7.5 年实证）+ 兴证 VaR 久期择时。")
    A("")
    A("> 机械规则输出，仅供研究参考，不构成投资建议。")
    return "\n".join(L)


def card_rows(ctx):
    s, v = ctx["signal"], ctx["vol"]
    rows = []
    for r in ctx["rows"]:
        rows.append(dict(code=r["code"], name=r["name"], short=r["name"],
                         act=r["act"], label=r["label"], pp=0, nav=r["nav"],
                         r20=None, r250=None, note=r["note"], role=r["role"],
                         theme=r["theme"]))
    rows.append(dict(code="σ", name="已实现波动", short="σ", act="none",
                     label=(f"{v['sigma']:.2%}" if v.get("sigma") is not None else "—"),
                     pp=0, nav=None, r20=None, r250=None,
                     note=f"σ/目标 {(v.get('ratio') or 0):.2f}× → 长端 {s['eff']:.1f}%"
                          + ("（熔断）" if v.get("guard") else ""),
                     role="信号", theme="信号"))
    return rows


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--force", action="store_true")
    args = ap.parse_args(argv)
    navs, asof, failed = data.load_all(force=args.force)
    ctx = engine.evaluate(navs, asof=max(asof.values()) if asof else None)
    ctx["sources"] = dict(asof)

    os.makedirs(_HERE, exist_ok=True)
    with open(OUT_MD, "w", encoding="utf-8") as f:
        f.write(render(ctx) + "\n")
    snap = dict(strategy="V5 · 波动率目标", as_of=ctx["as_of"],
                generated=datetime.now().strftime("%Y-%m-%d %H:%M"),
                source_methodology="Moreira & Muir (2017) vol-managed portfolios + 目标波动率法 + 兴证 VaR 久期择时",
                vol=ctx["vol"], signal=ctx["signal"],
                held=ctx["held"], rows=card_rows(ctx), sources=ctx["sources"])
    with open(OUT_JSON, "w", encoding="utf-8") as f:
        json.dump(snap, f, ensure_ascii=False, indent=2)
    print(f"[ok] {OUT_MD}")
    print(f"[ok] {OUT_JSON}")
    print(f"  as_of {ctx['as_of']} · σ {ctx['vol']['sigma']:.2%} "
          f"({ctx['vol']['ratio']:.2f}×目标) → 长端 {ctx['signal']['eff']:.1f}% · "
          f"{ctx['signal']['act']}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
