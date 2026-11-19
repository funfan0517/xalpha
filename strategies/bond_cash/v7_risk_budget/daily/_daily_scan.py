# -*- coding: utf-8 -*-
"""V7 · 门派风险预算 每日扫描 → daily/_v7_daily.{md,json}。

用法: python strategies/bond_cash/v7_risk_budget/daily/_daily_scan.py [--force]
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
BLOCKS = rule.BLOCKS
OUT_MD = os.path.join(_HERE, "_v7_daily.md")
OUT_JSON = os.path.join(_HERE, "_v7_daily.json")


def render(ctx):
    s, sig = ctx["signal"], ctx["signal"]
    L = []
    A = L.append
    A("# V7 · 门派风险预算（ERC）")
    A("")
    A(f"> 生成 {datetime.now():%Y-%m-%d %H:%M} · 数据截止 **{ctx['as_of']}** · "
      f"债腿 {ctx['bond_pp']:.0f}pp + 现金腿 {ctx['cash_pp']:.0f}pp（固定）· 月末调仓")
    A("")
    A("| 门派 | 当前(pp) | 目标(pp) | 当前权重 | 目标权重 | 风险贡献 | 平衡值 |")
    A("|---|---:|---:|---:|---:|---:|---:|")
    for b in BLOCKS:
        rc = ctx["rc_held"][b]
        flag = " ⚠" if rc > 45 else ""
        A(f"| {rule.BLOCK_NAME[b]} | **{ctx['held'][b]:.2f}** | {ctx['target'][b]:.2f} | "
          f"{ctx['weights_held'][b] * 100:.1f}% | {ctx['weights_target'][b] * 100:.1f}% | "
          f"{rc:.1f}%{flag} | {100 / len(BLOCKS):.0f}% |")
    A(f"| 现金腿 | **{ctx['cash_pp']:.2f}** | {ctx['cash_pp']:.2f} | — | — | 0%（不计入） | — |")
    A("")
    A(f"- 债腿内部波动（60 日年化）**{ctx['vol']:.2f}%**；"
      f"其中风险贡献最大的门派是 **{max(ctx['rc_held'], key=ctx['rc_held'].get)}**")
    A(f"- 当前权重与目标的偏差（pp）：" +
      "、".join(f"{rule.BLOCK_NAME[b]} {v:+.2f}" for b, v in sig["diff_pp"].items()))
    if sig.get("is_rebalance"):
        A(f"- **今天是调仓日** → {'触发' if sig['moved'] else '未超滞后带，不动手'} → 今日 **{sig['act']}**")
    else:
        A("- **今日不动手**（月末才调仓）；下面的「目标」是下个调仓日若信号不变会调到的地方")
    A(f"- 下次调仓：{sig['next_rebalance']}")
    A("")
    A("| 腿 | 标的 | 门派 | 占总资产 |")
    A("|---|---|---|---:|")
    for r in ctx["rows"]:
        A(f"| {r['role']} | {r['name']} | `{r['code']}` | **{r['pp']:.2f}pp** |")
    A("")
    A(f"> 规则来源：等风险贡献（ERC）+ 约束（转债 ≤{CFG['caps']['conv'] * 100:.1f}%、"
      f"信用 ≤{CFG['caps']['credit'] * 100:.0f}%、利率 ≥{CFG['floors']['rate'] * 100:.0f}%）；"
      f"协方差窗 {CFG['erc']['window']} 日、收缩 δ={CFG['erc']['shrink']:.2f}。"
      "利率腿内部固定 70/30（v0 口径），现金腿 10pp 固定、不参与风险预算。")
    A("")
    A("> 这是**配比体检**，不是收益增强：四门派的期望收益差只有 1~3pp/年，"
      "ERC 的价值在于暴露「谁在偷风险」。机械规则输出，不构成投资建议。")
    return "\n".join(L)


def card_rows(ctx):
    rows = []
    for r in ctx["rows"]:
        rows.append(dict(code=r["code"], name=r["name"], short=r["name"],
                         act=r["act"], label=r["label"], pp=r["pp"], nav=r["nav"],
                         r20=None, r250=None, note=r["note"], role=r["role"],
                         theme=r["theme"]))
    rows.append(dict(code="风险贡献", name="最大贡献门派", short="RC", act="none",
                     label=max(ctx["rc_held"], key=ctx["rc_held"].get), pp=0, nav=None,
                     r20=None, r250=None,
                     note=f"债腿波动 {ctx['vol']:.2f}%；" +
                          " / ".join(f"{b} {v:.0f}%" for b, v in ctx["rc_held"].items()),
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
    snap = dict(strategy="V7 · 门派风险预算（ERC）", as_of=ctx["as_of"],
                generated=datetime.now().strftime("%Y-%m-%d %H:%M"),
                source_methodology="等风险贡献（ERC）+ 硬约束；四门派分类对齐《债券种类手册》",
                vol=ctx["vol"], held=ctx["held"], target=ctx["target"],
                rc_held=ctx["rc_held"], rc_target=ctx["rc_target"],
                weights_held=ctx["weights_held"], weights_target=ctx["weights_target"],
                cash_pp=ctx["cash_pp"], bond_pp=ctx["bond_pp"],
                signal=ctx["signal"], rows=card_rows(ctx), sources=ctx["sources"])
    with open(OUT_JSON, "w", encoding="utf-8") as f:
        json.dump(snap, f, ensure_ascii=False, indent=2)
    print(f"[ok] {OUT_MD}")
    print(f"[ok] {OUT_JSON}")
    print(f"  as_of {ctx['as_of']} · " +
          " / ".join(f"{b} {ctx['held'][b]:.1f}pp" for b in BLOCKS) +
          f" / 现金 {ctx['cash_pp']:.1f}pp · 波动 {ctx['vol']:.2f}% · "
          f"max RC {max(ctx['rc_held'], key=ctx['rc_held'].get)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
