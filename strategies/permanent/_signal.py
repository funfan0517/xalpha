# -*- coding: utf-8 -*-
"""永久投资组合 · 每日最佳百分比报告。

输入: 8 项固定基准(rule.RAW) + 各腿场外净值(data.load_universe)
处理: rule.leg_scores -> rule.item_scores -> rule.target_weights(±50% 硬边界)
输出:
  strategies/permanent/daily/_permanent_report.md   人读报告(核心=每项最佳百分比表)
  strategies/permanent/daily/_permanent_signal.json  机器可读快照
  strategies/permanent/daily/_universe_permanent_active.{md,json}  8 项固定名单

用法:
  python strategies/permanent/_signal.py              # 当日(有当日缓存则秒出)
  python strategies/permanent/_signal.py --force       # 强制重抓净值
  python strategies/permanent/_signal.py --capital 2000000   # 按总资产换算金额
"""
import argparse
import io
import json
import os
import sys
from datetime import datetime

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8")

_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

import rule  # noqa: E402
import data  # noqa: E402

_DIR = os.path.dirname(os.path.abspath(__file__))
_DAILY = os.path.join(_DIR, "daily")
_OUT_MD = os.path.join(_DAILY, "_permanent_report.md")
_OUT_JSON = os.path.join(_DAILY, "_permanent_signal.json")
_ACT_MD = os.path.join(_DAILY, "_universe_permanent_active.md")
_ACT_JSON = os.path.join(_DAILY, "_universe_permanent_active.json")


def _pct(x, nd=1):
    return f"{x:.{nd}f}%"


def _sgn(x, nd=1):
    return f"{x:+.{nd}f}"


def leg_note(leg, m):
    """单腿一句话理由(趋势/动量/波动)。"""
    dev = m["close"] / m["ma"] - 1.0
    trend = "站上" if dev >= 0 else "跌破"
    return (f"{trend}MA200({dev:+.1%}) · 12月 {m['r250']:+.1%} · "
            f"60日波动 {m['vol']:.1%}")


def item_reason(item, itm_score, kk, legs_metrics, legs_scores):
    """-> (动作短语, [子腿明细])。"""
    legs = rule.ITEMS[item]["legs"]
    act = "加" if kk > 1.005 else ("减" if kk < 0.995 else "维持")
    head = f"{act}至基准×{kk:.2f}"
    if item == "cash":
        return head + f" · 现金得分 {itm_score:+.2f}(权益均值的反向)", []
    detail = []
    for leg in legs:
        if leg in legs_metrics:
            detail.append(f"{rule.LEGS[leg]['name']} {legs_scores[leg]:+.2f} "
                          f"[{leg_note(leg, legs_metrics[leg])}]")
    return head, detail


def build(capital=None):
    ser, asof = data.load_universe()
    metrics = {leg: rule.leg_metrics(s) for leg, s in ser.items()}
    scores = rule.leg_scores(metrics)
    isc = rule.item_scores(scores)
    w, k = rule.target_weights(isc)
    bw = rule.base_weights()
    bd = rule.bounds()
    lw = rule.leg_weights(w)

    total = sum(w.values())
    invest = sum(v for key, v in w.items() if key != "cash")
    return dict(asof=asof, metrics=metrics, scores=scores, item_scores=isc,
                weights=w, ks=k, base=bw, bounds=bd, leg_weights=lw,
                total=total, invest=invest, capital=capital)


def render(ctx):
    m, sc, isc = ctx["metrics"], ctx["scores"], ctx["item_scores"]
    w, k, bw, bd = ctx["weights"], ctx["ks"], ctx["base"], ctx["bounds"]
    asof = ctx["asof"]
    data_asof = max(asof.values()) if asof else "—"
    now = datetime.now().strftime("%Y-%m-%d %H:%M")
    cap = ctx["capital"]

    L = []
    A = L.append
    A("# 永久投资组合 · 每日最佳百分比")
    A("")
    A(f"> 生成 {now} · 净值数据截止 **{data_asof}**(场外口径, QDII 滞后 1-2 日) · "
      f"8 项基准 → ±{int(rule.TILT * 100)}% 倾斜 · 机械规则输出, 非投资建议。")
    A("")

    # ---- 摘要表
    A("## 一、当前最佳百分比")
    A("")
    if rule.CASH_OUTSIDE:
        A(f"> 口径: 8 项基准合计 {sum(rule.RAW.values()):.0f}%。投资池 7 项"
          f"每项在基准 ×[0.5, 1.5] 内倾斜、互相归一化, 合计 **{ctx['invest']:.1f}%**; "
          f"货币基金单独计 **{w['cash']:.1f}%** → 两者相加 "
          f"**{ctx['total']:.1f}%**。")
    else:
        A(f"> 口径: 8 项基准合计 {sum(rule.RAW.values()):.0f}%(你的原始设定), 倾斜后互相"
          f"归一化, 建议值合计恒为 **{ctx['total']:.1f}%**。当前投资池 7 项 "
          f"{ctx['invest']:.1f}% + 货币基金 {w['cash']:.1f}%。")
    A("")
    A("| # | 资产 | 场外代码 | 基准 | **建议** | 允许区间 | 相对基准 | 倾斜倍数 | 得分 |")
    A("|---|---|---|---:|---:|---|---:|---:|---:|")
    for i, item in enumerate(rule.ITEM_ORDER, 1):
        spec = rule.ITEMS[item]
        codes = " / ".join(rule.LEGS[leg]["code"] or "—" for leg in spec["legs"])
        delta = w[item] - bw[item]
        A(f"| {i} | {spec['name']} | `{codes}` | {_pct(bw[item])} | "
          f"**{_pct(w[item])}** | {_pct(bd[item][0])}–{_pct(bd[item][1])} | "
          f"{_sgn(delta)}pp | ×{k[item]:.2f} | {isc[item]:+.2f} |")
    A("")
    A(f"> 检查: 建议值合计 {ctx['total']:.1f}% ; "
      f"全部落在 ±{int(rule.TILT * 100)}% 硬边界内 ✓")
    A("")

    # ---- 金额
    if cap:
        A("## 二、按总资产折算")
        A("")
        A(f"以总资产 **{cap:,.0f}** 元按上表比例折算(8 项比例合计 "
          f"{ctx['total']:.1f} 份):")
        A("")
        A("| 资产 | 场外代码 | 比例 | 金额(元) |")
        A("|---|---|---:|---:|")
        for item in rule.ITEM_ORDER:
            spec = rule.ITEMS[item]
            codes = " / ".join(rule.LEGS[leg]["code"] or "—" for leg in spec["legs"])
            A(f"| {spec['name']} | `{codes}` | {_pct(w[item])} | "
              f"{cap * w[item] / ctx['total']:,.0f} |")
        A("")

    # ---- 得分构成
    sec = "三" if cap else "二"
    A(f"## {sec}、得分构成（趋势 {rule.W_TREND:.2f} / 12月动量 {rule.W_MOM:.2f}）")
    A("")
    A("| 资产 | 得分 | 明细 |")
    A("|---|---:|---|")
    for item in rule.ITEM_ORDER:
        head, detail = item_reason(item, isc[item], k[item], m, sc)
        cell = f"{head}<br>" + "<br>".join(detail) if detail else head
        A(f"| {spec_name(item)} | {isc[item]:+.2f} | {cell} |")
    A("")

    # ---- 分腿明细
    sec = "四" if cap else "三"
    A(f"## {sec}、分腿明细（场外净值）")
    A("")
    A("| 腿 | 场外代码 | 数据截止 | 单位净值 | 累计净值 | 20日 | 120日 | 250日 | "
      "60日波动 | 偏离MA200 | 得分 |")
    A("|---|---|---|---:|---:|---:|---:|---:|---:|---:|---:|")
    for leg in rule.LEG_ORDER:
        if leg not in m:
            continue
        mm = m[leg]
        unit, _ = data.load_unit_latest(rule.LEGS[leg]["code"] or "")
        unit_s = f"{unit:.4f}" if unit else "—"
        A(f"| {rule.LEGS[leg]['name']} | `{rule.LEGS[leg]['code']}` | {asof.get(leg, '—')} | "
          f"{unit_s} | {mm['close']:.4f} | {mm['r20']:+.1%} | {mm['r120']:+.1%} | "
          f"{mm['r250']:+.1%} | {mm['vol']:.1%} | {mm['close'] / mm['ma'] - 1:+.1%} | "
          f"{sc[leg]:+.2f} |")
    A("")
    A("> **信号用「累计净值」(totvalue, 分红再投复权)，申赎看「单位净值」**。"
      "两者在分红基金上差极大：`008163` 红利低波 2020-01 至今累计 +78% 而单位净值仅 +7%，"
      "用未复权净值会把每次分红当成下跌，持续误判「跌破均线」。")
    A("")
    A("> 货币基金无有效价格序列(万份收益口径), 不参与价格信号; "
      f"回测按年化 {rule.CASH_ANNUAL:.2%} 常数计。")
    A("")

    # ---- 规则边界
    sec = "五" if cap else "四"
    A(f"## {sec}、规则边界（不可突破）")
    A("")
    A(f"- 每项区间 = 基准 × [{1 - rule.TILT:.1f}, {1 + rule.TILT:.1f}]，"
      f"即「上下浮动 {int(rule.TILT * 100)}%」—— 这是硬边界，任何信号都不能突破。")
    if rule.CASH_OUTSIDE:
        A("- 投资池 7 项合计 = 总资产 − 货基；货基浮动会等量反噬投资池合计，"
          "故它不等于 100 而是随之游走。")
    A(f"- 倾斜倍数 k = 1 + {rule.TILT} × score，score ∈ (−1, 1) 由 tanh 压缩，"
      "因此 k 实际略窄于 [0.5, 1.5]，边界靠投影兜底。")
    A("- 触发顺序：先按 k 缩放 → 再归一化 → 再对越界项做 water-filling 投影，"
      "保证任何信号下都不破边界。")
    A("")
    A("> 判断口径说明：本策略**不使用估值分位**做权重驱动（8 项里仅 4 项有免费可回测的"
      "估值序列，标普 Forward PE / 黄金实际利率无历史源），只用同口径的价格趋势。"
      "估值/拥挤度请作为人工复核项叠加。")
    A("")
    A("> 免责声明：机械规则输出仅供研究参考，不构成投资建议；市场有风险，投资需谨慎。")
    return "\n".join(L)


def spec_name(item):
    return rule.ITEMS[item]["name"]


def active_md(ctx):
    L = ["# 永久投资组合 · 固定 8 项名单", "",
         f"> 生成 {datetime.now():%Y-%m-%d} · 由《永久投资组合》设定直接定档（非回测 select）。",
         "> 场外代码为唯一执行通道；具体比例每日由 `_signal.py` 按 ±50% 边界给出。", "",
         "| # | 资产 | 执行代码(场外) | 基准权重 |", "|---|---|---|---:|"]
    bw = ctx["base"]
    for i, item in enumerate(rule.ITEM_ORDER, 1):
        codes = " / ".join(rule.LEGS[leg]["code"] or "货基自选" for leg in rule.ITEMS[item]["legs"])
        L.append(f"| {i} | {spec_name(item)} | `{codes}` | {bw[item]:.2f}% |")
    L += ["", "> 对应 JSON: `strategies/permanent/daily/_universe_permanent_active.json`"]
    return "\n".join(L)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--force", action="store_true", help="强制重抓净值(忽略当日缓存)")
    ap.add_argument("--capital", type=float, default=None, help="总资产(元), 用于换算金额")
    args = ap.parse_args()

    ctx = build(args.capital)
    os.makedirs(_DAILY, exist_ok=True)

    md = render(ctx)
    with open(_OUT_MD, "w", encoding="utf-8") as f:
        f.write(md + "\n")

    snap = dict(
        as_of=max(ctx["asof"].values()),
        generated=datetime.now().strftime("%Y-%m-%d %H:%M"),
        cash_outside=rule.CASH_OUTSIDE,
        tilt=rule.TILT,
        base=ctx["base"],
        bounds={k: list(v) for k, v in ctx["bounds"].items()},
        weights={k: round(v, 2) for k, v in ctx["weights"].items()},
        ks={k: round(v, 4) for k, v in ctx["ks"].items()},
        item_scores={k: round(v, 4) for k, v in ctx["item_scores"].items()},
        leg_weights={k: round(v, 3) for k, v in ctx["leg_weights"].items()},
        leg_codes={leg: rule.LEGS[leg]["code"] for leg in rule.LEG_ORDER},
        data_asof=ctx["asof"],
    )
    with open(_OUT_JSON, "w", encoding="utf-8") as f:
        json.dump(snap, f, ensure_ascii=False, indent=2)

    with open(_ACT_MD, "w", encoding="utf-8") as f:
        f.write(active_md(ctx) + "\n")
    with open(_ACT_JSON, "w", encoding="utf-8") as f:
        json.dump(dict(strategy="永久投资组合", as_of=snap["as_of"],
                       items=[dict(key=item, name=spec_name(item),
                                   codes=[rule.LEGS[l]["code"] for l in rule.ITEMS[item]["legs"]],
                                   base=round(ctx["base"][item], 2))
                              for item in rule.ITEM_ORDER]),
                  f, ensure_ascii=False, indent=2)

    print(f"[ok] {_OUT_MD}")
    for item in rule.ITEM_ORDER:
        print(f"  {spec_name(item):8s} {ctx['weights'][item]:6.2f}%  "
              f"(基准 {ctx['base'][item]:5.2f}%, k={ctx['ks'][item]:.2f})")


if __name__ == "__main__":
    main()
