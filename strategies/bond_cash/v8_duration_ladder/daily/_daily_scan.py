# -*- coding: utf-8 -*-
"""V8 · 久期阶梯风险预算 每日扫描 → daily/_v8_daily.{md,json}。

用法: python strategies/bond_cash/v8_duration_ladder/daily/_daily_scan.py [--force]
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
OUT_MD = os.path.join(_HERE, "_v8_daily.md")
OUT_JSON = os.path.join(_HERE, "_v8_daily.json")


def render(ctx):
    sig = ctx["signal"]
    L = []
    A = L.append
    A("# V8 · 久期阶梯风险预算（六腿 0~100%，合计 100%）")
    A("")
    A(f"> 生成 {datetime.now():%Y-%m-%d %H:%M} · 数据截止 **{ctx['as_of']}** · "
      f"债腿 {ctx['bond_pp']:.0f}pp + 现金腿 {ctx['cash_pp']:.0f}pp（固定）· 月末调仓")
    A("")
    A("| 腿 | 门派 | 久期 | 当前(pp) | 目标(pp) | 当前权重 | 目标权重 | 风险贡献 | 动作 |")
    A("|---|---|---:|---:|---:|---:|---:|---:|---|")
    for r in ctx["rows"]:
        if r["sect"] == "现金":
            continue
        A(f"| `{r['code']}` {r['short']} | {r['sect']} | {r['duration']:.1f} | "
          f"**{r['pp']:.2f}** | {r['pp_target']:.2f} | {r['weight'] * 100:.1f}% | "
          f"{r['target'] * 100:.1f}% | {r['rc']:.1f}% | {r['act']} |")
    cash_pp = ctx["cash_pp"]
    A(f"| 现金腿 | 现金 | — | **{cash_pp:.2f}** | {cash_pp:.2f} | — | — | 不计入 | hold |")
    A("")
    A(f"- 中端档当前持 **`{ctx['pick']}`**（{rule.SHORT[ctx['pick']]}）；"
      f"近 {CFG['pick']['sr_win']} 日夏普：" +
      "、".join(f"`{c}` {v:+.2f}" for c, v in ctx["sr"].items()))
    if ctx["vol_feedback"]:
        A(f"- 进攻档波动反馈：" +
          "、".join(f"`{c}` ×{v:.2f}" for c, v in ctx["vol_feedback"].items()) +
          f"（σ{CFG['vol_win']}/σ{CFG['vol_long_win']}，只煞车不追高）")
    # ①② 两条纪律的状态（默认：① 开 · ② 关）
    cr = ctx["credit_vol_ratio"]
    cf = CFG["credit_freeze"]
    if ctx["degraded"]:
        miss_names = "、".join(f"{k}({[c for c in CFG['blocks'][k]['legs']]})" for k in ctx["degraded"])
        A(f"- ⚠ **数据降级**：{'、'.join(ctx['missing']) or miss_names} 缺净值 → 该档预算已移交 "
          f"`{CFG['degrade']['safe_leg']}`（最短久期腿），**没有**摊给其他档")
    else:
        A("- 数据完整（六腿齐全，无降级）")
    if ctx["frozen"]:
        A(f"- ⚠ **信用腿冻结中**（剩 {ctx['freeze_left']} 个调仓周期）：信用预算打 "
          f"{cf['derisk']:.0%} 折、只许减不许加（信用 σ60/σ252 = {cr:.2f} > {cf['vol_ratio']}）")
    else:
        A(f"- 信用腿未冻结（σ60/σ252 = {cr:.2f}，阈值 {cf['vol_ratio']}"
          + ("" if cf["on"] else "；该机制默认关闭") + "）")
    A(f"- 债腿内部波动（{CFG['vol_win']} 日年化）**{ctx['vol']:.2f}%**；"
      f"风险贡献最大的是 " +
      f"**`{max(ctx['rc_held'], key=ctx['rc_held'].get)}`**")
    tg = ctx.get("trend_gate")
    if tg:
        st_txt = ("✅ **开启**（长端在仓）" if tg["state"]
                  else f"⛔ **关闭**（长端 20% 已退 `{tg['to']}`）")
        mom_txt = f"{tg['mom']:+.2f}%" if tg.get("mom") is not None else "—（净值不足）"
        A(f"- ③ 长端趋势闸门：{st_txt}；`{tg['leg']}` {tg['mom_win']} 日动量 "
          f"**{mom_txt}**（门槛 0）· 双向最短持有 {CFG['trend_gate']['min_hold']} 个交易日"
          + (f" · 上次翻转 {tg['last_flip']}" if tg.get("last_flip") else ""))
    else:
        A("- ③ 长端趋势闸门：未启用")
    A("- 当前与目标的偏差（pp）：" +
      "、".join(f"`{c}` {v:+.2f}" for c, v in sig["diff_pp"].items()))
    if sig.get("is_rebalance"):
        A(f"- **今天是调仓日** → {'触发' if sig['moved'] else '未超滞后带，不动手'} → 今日 **{sig['act']}**")
    else:
        A("- **今日不动手**（月末才调仓）；下面的「目标」是下个调仓日若信号不变会调到的地方")
    A(f"- 下次调仓：{sig['next_rebalance']}")
    A("")
    A("| 腿 | 标的 | 编号 | 角色 | 占总资产 |")
    A("|---|---|---|---|---:|")
    for r in ctx["rows"]:
        if r["sect"] == "现金":
            A(f"| 现金腿 | {r['name']} | `{r['code']}` | 现金 | **{r['pp']:.2f}pp** |")
        else:
            A(f"| {r['block']} | {r['name']} | #{r['num']} `{r['code']}` | {r['act']} | {r['pp']:.2f}pp |")
    A("")
    A(f"> 结构预算（占债腿）：长端 {CFG['blocks']['long']['budget'] * 100:.0f}% / "
      f"中端 {CFG['blocks']['mid']['budget'] * 100:.0f}%（同档择一）/ "
      f"短端 {CFG['blocks']['short']['budget'] * 100:.0f}% / "
      f"信用 {CFG['blocks']['credit']['budget'] * 100:.0f}%；单腿上障 {CFG['cap'] * 100:.0f}%。"
      "现金腿 10pp 固定、不参与配比。")
    A("")
    A("> 机械规则输出，不构成投资建议。回测结论见 "
      "`strategies/bond_cash/v8_duration_ladder/backtest/_v8_bt.md`："
      "⚠ 2026-11-20 v0 改 35/65 后基准夏普升到 1.87，**v8（1.83）已不跑赢基准**："
      "两者做的是同一件事（降长端），v8 多了两项动态与换手，长窗则相反（1.58 vs 1.41）。")
    return "\n".join(L)


def card_rows(ctx):
    rows = []
    for r in ctx["rows"]:
        rows.append(dict(code=r["code"], name=r["name"], short=r["short"],
                         act=r["act"], label=r["label"], pp=r["pp"], nav=None,
                         r20=None, r250=None, note=r["note"], role=r["block"],
                         theme=r["sect"]))
    rows.append(dict(code="中端档", name="同档择一", short="择壳", act="none",
                     label=f"持 {ctx['pick']}", pp=0, nav=None, r20=None, r250=None,
                     note="债腿波动 " + f"{ctx['vol']:.2f}%；" +
                          " / ".join(f"{c} {v:.0f}%" for c, v in ctx["rc_held"].items()),
                     role="信号", theme="信号"))
    rows.append(dict(code="纪律", name="降级/冻结", short="风控", act="none",
                     label=("降级" if ctx["degraded"] else "正常")
                           + ("/冻结" if ctx["frozen"] else ""),
                     pp=0, nav=None, r20=None, r250=None,
                     note=f"信用 σ比 {ctx['credit_vol_ratio']:.2f}（阈值 "
                          f"{CFG['credit_freeze']['vol_ratio']}）；降级 "
                          f"{'触发 ' + ','.join(ctx['degraded']) if ctx['degraded'] else '未触发'}",
                     role="信号", theme="信号"))
    tg = ctx.get("trend_gate")
    rows.append(dict(code="闸门", name="长端动量", short="闸门", act="none",
                     label=("长端在仓" if tg["state"] else f"退 {tg['to']}") if tg else "未启用",
                     pp=0, nav=None, r20=None, r250=None,
                     note=(f"{tg['mom_win']} 日动量 "
                           + (f"{tg['mom']:+.2f}%" if tg.get("mom") is not None else "—")
                           + f"；最短持有 {CFG['trend_gate']['min_hold']} 交易日；"
                           "真实口径下不抬夏普，只作回撤纪律") if tg else "CFG['trend_gate']['on']=False",
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
    snap = dict(strategy="V8 · 久期阶梯风险预算", as_of=ctx["as_of"],
                generated=datetime.now().strftime("%Y-%m-%d %H:%M"),
                source_methodology="久期阶梯结构预算 + 同档择一 + 进攻档波动煞车；两门派（利率债 / 信用债）",
                vol=ctx["vol"], bond_pp=ctx["bond_pp"], cash_pp=ctx["cash_pp"],
                weights_held=ctx["weights_held"], weights_target=ctx["weights_target"],
                pp_held=ctx["pp_held"], pp_target=ctx["pp_target"],
                rc_held=ctx["rc_held"], rc_target=ctx["rc_target"],
                pick=ctx["pick"], sr=ctx["sr"], vol_feedback=ctx["vol_feedback"],
                trend_gate=ctx.get("trend_gate"),
                signal=ctx["signal"], rows=card_rows(ctx), sources=ctx["sources"])
    with open(OUT_JSON, "w", encoding="utf-8") as f:
        json.dump(snap, f, ensure_ascii=False, indent=2)
    print(f"[ok] {OUT_MD}")
    print(f"[ok] {OUT_JSON}")
    print(f"  as_of {ctx['as_of']} · " +
          " / ".join(f"{c} {ctx['pp_held'][c]:.1f}pp" for c in ctx["pp_held"]) +
          f" / 现金 {ctx['cash_pp']:.1f}pp · 波动 {ctx['vol']:.2f}% · 中端持 {ctx['pick']}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
