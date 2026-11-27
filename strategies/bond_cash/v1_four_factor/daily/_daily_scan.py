# -*- coding: utf-8 -*-
"""V1 · 四因子久期档 每日扫描。

产物
  ``daily/_v1_daily.md`` / ``_v1_daily.json``        看板板块6 的 V1 卡片（§四附最近一个月逐日情景概率）
  ``daily/_v1_state.json``                           **当前长端占比**（滞后带 R-18 的状态）

状态文件只有 ``w_long`` 一个字段：首次运行按基准 35% 起（并在报告里标注「按基准假设」），
之后读上一日实际执行的占比。手工改它即可把仓位校准到真实持仓（比如你实际没跟上昨日信号）。

用法
  python strategies/bond_cash/v1_four_factor/daily/_daily_scan.py [--force]
"""
import argparse
import io
import json
import os
import sys
from datetime import datetime

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8")

_HERE = os.path.dirname(os.path.abspath(__file__))          # .../v1_four_factor/daily
_PKG = os.path.dirname(_HERE)                               # .../v1_four_factor
for _p in (os.path.dirname(_PKG), _PKG, _HERE):
    while _p in sys.path:
        sys.path.remove(_p)
    sys.path.insert(0, _p)

import rule      # noqa: E402
import data      # noqa: E402
import engine    # noqa: E402

CFG = rule.CFG
STATE = os.path.join(_HERE, "_v1_state.json")
OUT_MD = os.path.join(_HERE, "_v1_daily.md")
OUT_JSON = os.path.join(_HERE, "_v1_daily.json")

_CONF_CN = {"high": "高", "medium": "中", "low": "低"}
_FACTOR_RULE = {
    "valuation": "估值温度 ≤20 → +1 / ≥80 → −1",
    "trend": "close>MA5>MA20 且近 20 日高 ×0.995 → +1；反向 → −1",
    "funding": "资金温度 ≥70 → +1 / ≤30 → −1（松=利多）",
    "credit": "信用温度 ≥70 → +1 / ≤30 → −1（利差窄=拥挤）",
}


def _load_state():
    try:
        with open(STATE, encoding="utf-8") as f:
            d = json.load(f)
        return float(d["w_long"]), False
    except (OSError, ValueError, KeyError, TypeError):
        return CFG["long_base"], True


def _save_state(as_of, w_long):
    os.makedirs(_HERE, exist_ok=True)
    with open(STATE, "w", encoding="utf-8") as f:
        json.dump(dict(as_of=as_of, w_long=round(float(w_long), 4),
                       updated=datetime.now().strftime("%Y-%m-%d %H:%M")),
                  f, ensure_ascii=False, indent=2)


def _fmt(v, nd=1):
    return "—" if v is None else f"{v:.{nd}f}"


def render(ctx, init_state, hist=None, fc=None):
    t, f, s, sc, pos = (ctx[k] for k in ("temp", "factors", "signal", "scenario", "position"))
    L, A = [], None
    A = L.append
    A("# V1 · 四因子久期档（晴雨表）")
    A("")
    A(f"> 生成 {datetime.now():%Y-%m-%d %H:%M} · 数据截止 **{ctx['as_of']}** · "
      "规则来源 `G:\\tradingagents\\bond_strategy\\bond_rules.md` v1.0")
    A("")
    A("## 一、债券晴雨表（温度只定赔率、不定方向）")
    A("")
    A("| 子温度 | 读数 | 档位 | 权重 |")
    A("|---|---:|---|---:|")
    for k, nm in rule.TEMP_NAMES:
        A(f"| {nm} | {_fmt(t[k])} | {rule.temp_band(t[k])} | {CFG['temp_weights'][k]:.0%} |")
    A(f"| **合成** | **{_fmt(t['composite'])}** | **{t['band']}** | 100% |")
    A("")
    A(f"> 覆盖 {t['coverage']}/4 项；{'**已降级**（覆盖≤2 项或 R-11 背离）' if t['degrade'] else '无降级'}。"
      "R-03：估值温度 >80 时未来 20 日上涨概率仍约 58~60% —— 温度高≠看空，只表示赔率变差。")
    A("")

    A("## 二、四因子与信号")
    A("")
    A("| 因子 | 值 | 依据 |")
    A("|---|---:|---|")
    for k, nm in rule.FACTOR_NAMES:
        v = f[k]
        mark = "+1（利多债）" if v == 1 else ("−1（利空债）" if v == -1 else "0（中间区）")
        A(f"| {nm} | {v:+d} | {_FACTOR_RULE[k]} → {mark} |")
    A("")
    A(f"- score = **{s['score']:+d}**（四因子之和）；信号 = **{s['side']}**；"
      f"置信度 = **{_CONF_CN[s['conf']]}**"
      + ("；**互斥 → 强制震荡观望**" if s["conflict"] else "")
      + ("；**R-11 降级**" if s["degrade"] else ""))
    A(f"- 事件冻结（R-19）：**{'是，只允许减久期' if s['freeze'] else '否'}**")
    A("")

    A("## 三、目标仓位（映射到债腿内部久期）")
    A("")
    A(f"- 当前长端占比：**{_fmt(pos['cur'])}%**"
      + ("（首次运行，按基准 35% 假设，见 `daily/_v1_state.json`）" if init_state else "（读状态文件）"))
    A(f"- 规则目标：**{_fmt(pos['target'])}%** = 35% + score/4 × (70% − 35%) × 折扣"
      f"（{_CONF_CN[s['conf']]} {CFG['discount'][s['conf']]:.2f}）")
    A(f"- 滞后带：**{CFG['hysteresis_pp']:.2f}pp**（R-18 的 1.5pp 按带宽 35/10 放缩）→ "
      f"**{'触发' if pos['moved'] else '不触发'}**")
    A("")
    A("| 腿 | 标的 | 代码 | 占总资产 | 占债腿 |")
    A("|---|---|---|---:|---:|")
    bp = CFG["sleeve_bond_pp"]
    A(f"| 债腿·长端 | 广发中债7-10年国开债A | `{CFG['long_leg']}` | **{pos['long_pp']:.2f}pp** | "
      f"{pos['w_long']:.2f}% |")
    A(f"| 债腿·底仓 | 永赢中债3-5年政金债A | `{CFG['base_leg']}` | **{pos['base_pp']:.2f}pp** | "
      f"{100 - pos['w_long']:.2f}% |")
    for c, pp in pos["cash_pp"].items():
        A(f"| 现金腿 | {rule.NAMES[c]} | `{c}` | **{pp:.2f}pp** | — |")
    A("")

    A("## 四、情景预测（只出概率，不作为仓位主依据）")
    A("")
    A(f"- 未来 {CFG['scenario']['horizon']} 日债指上涨概率：**{sc['p_up']:.1f}%**"
      + ("" if sc["ok"] else "（退化中性）"))
    A(f"- 三情景：债牛 **{sc['bull']:.1f}%** / 震荡 **{sc['flat']:.1f}%** / "
      f"债熊 **{sc['bear']:.1f}%**（和 = 100%）")
    A(f"- {sc['note']}。R-12：严禁点位预测；R-15：估值分位对债券短期方向预测力弱。")
    A("")
    if hist:
        A("最近一个月逐日读数（**point-in-time**：每天只用当日及之前的样本重算桶统计，不含前视）：")
        A("")
        A("| 日期 | 估值温度 | 桶 | 样本n | 上涨概率 | 牛/震荡/熊 | 20日后实际 |")
        A("|---|---:|---:|---:|---:|---|---:|")
        for r in hist:
            bk = "—" if r["bucket"] is None else f"{r['bucket']:.0f}"
            fw = "未到期" if r["fwd20"] is None else f"{r['fwd20']:+.2f}%"
            A(f"| {r['date']} | {_fmt(r['valuation'])} | {bk} | {r['n']} | "
              f"{r['p_up']:.1f}% | {r['bull']:.1f}/{r['flat']:.1f}/{r['bear']:.1f} | {fw} |")
        A("")
        A("> 「20日后实际」= 该日之后 20 个交易日 `511260` 累计净值涨跌，仅用于事后核对；"
          "概率不是交易指令，未到期的留空。")
        A("")
    if fc:
        n = fc["next_week"]
        A(f"> 下周晴雨表概率预测（生成 {fc['generated']}，详见 "
          "`strategies/bond_cash/v1_four_factor/research/next_week_forecast/_next_week_forecast.md`）："
          f"合成温度中位 {n['composite']:.1f} · 主信号 {n['modal_side']}（{n['modal_prob']:.0f}%）"
          f" · 长端目标中位 {n['target']:.1f}% · 触发滞后带概率 {n['p_move']:.0f}%。")
        A("")
    A(f"> {ctx['note']}")
    A("")
    A("> 机械规则输出，仅供研究参考，不构成投资建议。")
    return "\n".join(L)


def card_rows(ctx):
    t, f, s, sc, pos = (ctx[k] for k in ("temp", "factors", "signal", "scenario", "position"))
    rows = []
    rows.append(dict(c="温度", n="估值/资金/动能/信用", act="none",
                     a=f"{_fmt(t['composite'])} {t['band']}",
                     d=f"估值 {_fmt(t['valuation'])} · 资金 {_fmt(t['funding'])} · "
                       f"动能 {_fmt(t['momentum'])} · 信用 {_fmt(t['credit'])} · 覆盖 {t['coverage']}/4"))
    for k, nm in rule.FACTOR_NAMES:
        v = f[k]
        rows.append(dict(c=f"{nm}因子", n=f"温度 {_fmt(t[k if k != 'trend' else 'momentum'])}",
                         act=("buy" if v == 1 else "sell" if v == -1 else "none"),
                         a=f"{v:+d}",
                         d=_FACTOR_RULE[k].replace(" → +1", "").replace(" → −1", "")))
    rows.append(dict(c=CFG["long_leg"], n=rule.NAMES[CFG["long_leg"]],
                     act=pos["act"] if pos["moved"] else "hold",
                     a=f"{pos['long_pp']:.1f}pp",
                     d=f"长端占比 {pos['cur']:.1f}% → {pos['target']:.1f}% · "
                       f"{'触发' if pos['moved'] else '滞后带内不动'}"))
    rows.append(dict(c=CFG["base_leg"], n=rule.NAMES[CFG["base_leg"]],
                     act="hold", a=f"{pos['base_pp']:.1f}pp", d="债腿剩余（3-5 年政金）"))
    for c, pp in pos["cash_pp"].items():
        rows.append(dict(c=c, n=rule.NAMES[c], act="hold", a=f"{pp:.1f}pp", d="现金腿（与 v0 一致）"))
    rows.append(dict(c="20D", n="未来20日情景", act="none", a=f"牛 {sc['bull']:.1f}%",
                     d=f"震荡 {sc['flat']:.1f}% / 熊 {sc['bear']:.1f}% · n={sc['n']} · "
                       f"score {s['score']:+d} {s['side']}（{_CONF_CN[s['conf']]}）"))
    return [dict(code=r["c"], name=r["n"], short=r["c"], act=r["act"], label=r["a"],
                 pp=0, note=r["d"], r20=None, r250=None, nav=None, role="", theme="")
            for r in rows]


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--force", action="store_true")
    args = ap.parse_args(argv)

    p = data.panel(force=args.force)
    cur, init_state = _load_state()
    ctx = engine.evaluate(p, cur_w=cur)
    src = data.data_asof(p)
    ctx["sources"] = src

    bench = p["navs"][CFG["bench"]]
    temps = engine.build_temps(p["navs"], p["y10"], p["shibor"], p["curve"])
    hist = engine.scenario_history(temps, bench)
    if hist and hist[-1]["p_up"] != ctx["scenario"]["p_up"]:
        print(f"[warn] 逐日情景末行 {hist[-1]['p_up']} 与当日 {ctx['scenario']['p_up']} 不一致")
    fc_path = os.path.join(_PKG, "research", "next_week_forecast", "_next_week_forecast.json")
    fc = None
    if os.path.exists(fc_path):
        with open(fc_path, encoding="utf-8") as f:
            fc = json.load(f)

    os.makedirs(_HERE, exist_ok=True)
    with open(OUT_MD, "w", encoding="utf-8") as f:
        f.write(render(ctx, init_state, hist, fc) + "\n")

    snap = dict(
        strategy="V1 · 四因子久期档（债券晴雨表）",
        as_of=ctx["as_of"],
        generated=datetime.now().strftime("%Y-%m-%d %H:%M"),
        source_methodology="G:/tradingagents/bond_strategy/bond_rules.md v1.0（2026-11-17）",
        temp=ctx["temp"], factors=ctx["factors"], signal=ctx["signal"],
        scenario=ctx["scenario"], scenario_history=hist, position=ctx["position"], note=ctx["note"],
        rows=card_rows(ctx), sources=src,
        state=dict(w_long=ctx["position"]["w_long"] / 100.0, init=init_state),
    )
    with open(OUT_JSON, "w", encoding="utf-8") as f:
        json.dump(snap, f, ensure_ascii=False, indent=2)
    _save_state(ctx["as_of"], ctx["position"]["w_long"] / 100.0)

    print(f"[ok] {OUT_MD}")
    print(f"[ok] {OUT_JSON}")
    if hist:
        print(f"  情景逐日: {len(hist)} 天（{hist[0]['date']} ~ {hist[-1]['date']}）")
    s, pos, t = ctx["signal"], ctx["position"], ctx["temp"]
    print(f"  as_of {ctx['as_of']} · 合成温度 {_fmt(t['composite'])} {t['band']} · "
          f"score {s['score']:+d} {s['side']}（{s['conf']}）")
    print(f"  长端占比 {pos['cur']:.1f}% → 目标 {pos['target']:.1f}% · "
          f"{'触发' if pos['moved'] else '滞后带内不动'} · "
          f"债腿 {CFG['long_leg']} {pos['long_pp']:.1f}pp + {CFG['base_leg']} {pos['base_pp']:.1f}pp")
    return 0


if __name__ == "__main__":
    sys.exit(main())
