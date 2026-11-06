# -*- coding: utf-8 -*-
"""波动资产 · V8 变种（美股两腿金叉 50/200 × 波动层 TV20）· 每日操作。

流程：
1. **先刷新数据**：美股指数（`.NDX` / `.INX`，增量）+ 场外 QDII 净值（仅这两条腿）；
   加 `--no-refresh` 用本地缓存，报告会标「未刷新」；
2. 对两条腿各算：**方向门**（MA50 vs MA200）、`vol20`、波动层系数 `min(1, TV/vol)`、
   目标仓位（过死区）、与现仓的差、**两条触发路径各自的距离**；
3. 输出：今日是否有动作、逐腿读数、最近 8 次调仓、**两个触发器的距离**
   （方向门：MA50 还要动多少；波动层：vol 还要升到多少）。

**仓位有两个来源**：方向门（0 / 100%）与波动层（0~100% 连续缩放），两者相乘。
所以「今日动作」既可能来自翻叉，也可能来自波动率变化 —— 后者几乎天天在动，
死区 10pp 挡掉了大部分微调。

产物（相对本文件）：daily/_v8_golden_cross_daily.md / _v8_golden_cross_daily.json。
机械规则输出，非投资建议。
"""
import json
import os
import sys

import pandas as pd

try:
    sys.stdout.reconfigure(encoding="utf-8")  # type: ignore[attr-defined]
except Exception:  # noqa: BLE001
    pass

_HERE = os.path.dirname(os.path.abspath(__file__))
_V8 = os.path.dirname(_HERE)
sys.path.insert(0, _V8)

import golden_cross as gc  # noqa: E402

OUT_MD = os.path.join(_HERE, "_v8_golden_cross_daily.md")
OUT_JSON = os.path.join(_HERE, "_v8_golden_cross_daily.json")
RECENT = 8


def leg_view(leg, refreshed, fails, us_latest):
    cfg = gc.rule.LEGS[leg]
    p = gc.params()
    nav, st, _dec = gc.build(leg)
    sig = gc.load_signal(leg)
    fast, slow = gc.ma_pair(sig)
    v = float(gc.vol_series(leg).iloc[-1])

    d_sig = sig.index[-1]
    fv, sv = float(fast.iloc[-1]), float(slow.iloc[-1])
    golden = fv > sv
    gap = fv / sv - 1.0
    need_cross = (sv - fv) / max(abs(fv), 1e-9)      # 快线单独要动多少才翻叉

    tgt = gc.target_now(leg)
    state_now = float(st.iloc[-1])
    gate_now = 1.0 if golden else 0.0
    # 最新一天的目标若与「最后一根净值日收盘后的仓位」不同，就说明这笔还没成交 -> 待办。
    # 注意**不能只看金叉翻转**：绝大多数调仓来自波动层的 vol 变化（见报告 §1）。
    pending = tgt if abs(tgt - state_now) > 1e-9 else None

    # 波动层：仓位再降一档（10pp）需要 vol 升到 TV/(w-10pp)；回补一档需要 vol 降到 TV/(w+10pp)
    thr_down = float(p["TV"]) / max(state_now - float(p["VOL_DEAD"]), 1e-9) \
        if state_now > float(p["VOL_DEAD"]) + 1e-9 and gate_now > 0.5 else None
    thr_up = float(p["TV"]) / min(state_now + float(p["VOL_DEAD"]), 1.0) \
        if gate_now > 0.5 else None

    ops = gc.actions(st)
    recent = ops[-RECENT:]
    return dict(
        leg=leg, name=cfg["name"], band=float(cfg["band"]),
        exec=cfg["exec"], exec_name=cfg["exec_name"], signal=cfg["signal"],
        nav_date=nav.index[-1].date().isoformat(), sig_date=d_sig.date().isoformat(),
        nav=float(nav.iloc[-1]), close=float(sig.iloc[-1]),
        fast=fv, slow=sv, gap=gap, golden=bool(golden), need_cross=need_cross,
        vol=v, scale=min(1.0, float(p["TV"]) / v), target=tgt,
        state=state_now, level=gc.level_name(state_now), pending=pending,
        action=("下调 → " if pending is not None and pending < state_now else
                "上调 → " if pending is not None else "维持") +
               (gc.level_name(pending) if pending is not None else ""),
        thr_down=thr_down, thr_up=thr_up,
        fresh=_fresh(leg, refreshed, fails, us_latest),
        ops=[dict(date=o["date"].date().isoformat(), kind=o["kind"],
                  frm=o["frm"], to=o["to"]) for o in recent],
    )


def _fresh(leg, refreshed, fails, us_latest):
    code = gc.rule.LEGS[leg]["otc"]
    if not refreshed:
        return "·未刷新(本地缓存)"
    if code in fails:
        return f"❌净值刷新失败({code})"
    if us_latest.get("fail"):
        return "⚠指数部分失败"
    return "✅已刷新"


def main():
    refreshed = "--no-refresh" not in sys.argv
    fails, us_latest = {}, {}
    if refreshed:
        codes = [gc.rule.LEGS[k]["otc"] for k in gc.POOL]
        try:
            _l, fails = gc.vdata.refresh_otc(codes=codes)
        except Exception as exc:                                 # noqa: BLE001
            fails = {c: f"{type(exc).__name__}: {exc}" for c in codes}
        try:
            _u, us_fails = gc.vdata.refresh_us()
            us_latest = dict(latest=_u, fail=us_fails)
        except Exception as exc:                                 # noqa: BLE001
            us_latest = dict(latest={}, fail={"__all__": f"{type(exc).__name__}: {exc}"})

    rows = [leg_view(k, refreshed, fails, us_latest) for k in gc.POOL]
    act = [r for r in rows if r["pending"] is not None]
    downs = [r for r in act if r["pending"] < r["state"]]
    ups = [r for r in act if r["pending"] > r["state"]]
    last_date = max(r["sig_date"] for r in rows)

    comb = sum(r["state"] * r["band"] for r in rows)
    comb_tgt = sum(r["target"] * r["band"] for r in rows)

    L = [
        f"# 波动资产 · V8（金叉 50/200 × 波动层 TV20）· 每日操作（{last_date}）",
        f"> 池 = **只有纳指100（{gc.rule.LEGS['ndx']['otc']}）与标普500"
        f"（{gc.rule.LEGS['spx']['otc']}，此前 050025 桥接）**；"
        "信号 = **美元指数**收盘（`.NDX` / `.INX`），执行 = 场外 QDII **人民币复权净值**。",
        f"> 规则：`仓位 = (MA{gc.FAST} > MA{gc.SLOW} ? 1 : 0) × min(1, {gc.TV:.0%} / vol{gc.VOL_WIN})`，"
        f"日频 · 死区 {gc.VOL_DEAD:.0%} · 单边成本 {gc.COST:.1%} · "
        f"空仓按货基 {gc.CASH_ANNUAL:.2%} 计息。",
        "> 成交：信号日收盘决策 → **次一交易日收盘净值**（QDII 净值滞后 1~2 日，实盘按 T+1~T+2 提交）。",
        "> 机械规则输出，**非投资建议**。",
        "",
        "## 今日结论（明日成交）",
        "",
        "| 腿 | 现仓 | 方向门 | vol20 | TV/vol | 目标 | 今日动作 | 数据 |",
        "|---|---:|---|---:|---:|---:|---|---|",
    ]
    for r in rows:
        gate = "金叉（开）" if r["golden"] else "死叉（关）"
        L.append(f"| {r['name']} | **{r['state']:.0%}** | {gate} | {r['vol']:.2%} | "
                 f"{r['scale']:.2f}× | {r['target']:.0%} | {r['action']} | {r['fresh']} |")
    L += [
        "",
        (f"**有动作：{'、'.join(r['name'] for r in downs)} 下调"
         f"{'、' + '、'.join(r['name'] for r in ups) + ' 上调' if ups else ''}"
         f"，明日（次一交易日）收盘净值成交。**" if act else
         "**今日无动作 —— 两条腿都维持现仓**（`|Δ仓位| < 10pp`，死区挡掉了微调）。"),
        "",
        f"**组合层风险预算**：现仓 {comb:.1f}pp / 满仓 20pp（{comb / 20:.0%}），"
        f"目标 {comb_tgt:.1f}pp（{comb_tgt / 20:.0%}）。",
        "",
        "> 仓位有**两个来源**，任何一个变了都会动：① 方向门（金叉/死叉，一年约 2 次）；"
        "② 波动层（vol 变化，天天在动，靠死区过滤）。**别只盯金叉** ——"
        "v8 绝大多数调仓来自第 ② 条（14.2 年 132 次里只有 28 次是翻叉）。",
        "",
        "## 两个触发器各自离触发还有多远",
        "",
        "| 腿 | 方向门：MA50 要动多少 | 波动层：vol 升到 | vol 降到 | 现状 |",
        "|---|---|---:|---:|---|",
    ]
    for r in rows:
        if r["golden"]:
            cross_txt = f"**跌 {abs(r['need_cross']) * 100:.2f}%** 才翻空（或 MA{gc.SLOW} 上行）"
        else:
            cross_txt = f"**涨 {abs(r['need_cross']) * 100:.2f}%** 才翻多（或 MA{gc.SLOW} 下行）"
        d = f"{r['thr_down']:.1%}" if r["thr_down"] is not None else "—"
        u = f"{r['thr_up']:.1%}" if r["thr_up"] is not None else "—"
        L.append(f"| {r['name']} | {cross_txt} | {d} | {u} | 门 "
                 f"{'开' if r['golden'] else '关'} · 仓位 {r['state']:.0%} |")
    L += [
        "",
        "> 「vol 升到 / 降到」= 仓位再降 / 回补一档（10pp）所需的 `vol20`（**目标带宽度的近似**："
        "实际还要看死区与当天的门）。「MA50 要动多少」= 快线单独移动的近似（慢线假设不动），"
        "真实触发点会早一点。两者都只用来判断「现在离边缘有多近」，不是预测。",
        "",
        "## 逐腿读数",
        "",
        "| 腿 | 执行代码 | 净值日 | 复权净值 | 信号代码 | 信号日 | 信号收盘 | MA50 | MA200 | 间距 | 目标带 | 折合 pp |",
        "|---|---|---|---:|---|---|---:|---:|---:|---:|---:|---:|",
    ]
    for r in rows:
        L.append(f"| {r['name']} | `{r['exec']}` {r['exec_name']} | {r['nav_date']} | "
                 f"{r['nav']:.4f} | `{r['signal']}` | {r['sig_date']} | {r['close']:.0f} | "
                 f"{r['fast']:.0f} | {r['slow']:.0f} | {r['gap']:+.2%} | {r['band']:g}pp | "
                 f"{r['state'] * r['band']:.2f} |")
    L += [
        "",
        f"## 最近 {RECENT} 次调仓（历史，非今日）",
        "",
        "| 日期 | 腿 | 动作 | 仓位变化 |",
        "|---|---|---|---|",
    ]
    allops = []
    for r in rows:
        for o in r["ops"]:
            allops.append((o["date"], r["name"], o["kind"], o["frm"], o["to"]))
    if allops:
        for d, nm, kind, frm, to in sorted(allops, reverse=True):
            L.append(f"| {d} | {nm} | {'上调' if kind == 'buy' else '下调'} | "
                     f"{frm:.0%} → {to:.0%}（{gc.level_name(to)}） |")
    else:
        L.append("| — | — | 无 | — |")
    L += [
        "",
        "## 提醒",
        "",
        "- **慢线滞后是结构性的**：200 日慢线让转向确认必然晚 2~4 个月。2020 那次是 "
        "2020-04-30 才翻空（离底部一个月）、05-21 又翻多 —— **卖在坑底、买在反弹后**。"
        "波动层就是为了补这个（当时是 vol 先爆表、仓位先降下来的）。",
        "- **波动层不是方向判断**：低波动阴跌里它照样接近满仓。它只治「急跌」。",
        "- **QDII 通道**：纳指/标普 QDII 可能限购、暂停申购或溢价 —— 触发降仓时若无法赎回，"
        "按纪律顺延到下一可交易日，**不要为追规则去买场内溢价**。",
        "- 与主策略的关系：本变种只管**这两条腿露多少目标带**（带位仍由 `rule` 决定），"
        "不改变组合层仓位带。",
        "",
        "> 机械规则输出，**非投资建议**。",
    ]

    with open(OUT_MD, "w", encoding="utf-8") as f:
        f.write("\n".join(L))
    with open(OUT_JSON, "w", encoding="utf-8") as f:
        json.dump({
            "strategy": gc.LABEL,
            "as_of": last_date,
            "rule": {"fast": gc.FAST, "slow": gc.SLOW, "hyst": gc.HYST,
                     "floor": gc.FLOOR, "trigger": gc.TRIGGER,
                     "vol_on": gc.VOL_ON, "tv": gc.TV, "vol_win": gc.VOL_WIN,
                     "vol_dead": gc.VOL_DEAD,
                     "signal": "usd_index", "trade_at": "T+1 净值",
                     "cost": gc.COST, "cash_annual": gc.CASH_ANNUAL},
            "summary": {"action": bool(act), "down": [r["leg"] for r in downs],
                        "up": [r["leg"] for r in ups],
                        "combo_pp": comb, "combo_target_pp": comb_tgt,
                        "refreshed": refreshed, "fail": list(fails),
                        "us_fail": list(us_latest.get("fail") or {})},
            "legs": [{k: v for k, v in r.items() if k != "ops"} for r in rows],
            "recent": [dict(date=d, leg=nm, kind=k, frm=a, to=b)
                       for d, nm, k, a, b in sorted(allops, reverse=True)],
        }, f, ensure_ascii=False, indent=1)

    print("报告:", OUT_MD)
    print("JSON:", OUT_JSON)
    print(f"信号日 {last_date} · 动作 {'有' if act else '无'} · 组合 {comb:.1f}pp / 目标 {comb_tgt:.1f}pp")
    for r in rows:
        print(f"  {r['name']:8s} 仓位 {r['state']:.0%} · 门 {'开' if r['golden'] else '关'} · "
              f"vol20 {r['vol']:.2%} · TV/vol {r['scale']:.2f} · 目标 {r['target']:.0%} · "
              f"{r['action']} · {r['fresh']}")
    if fails:
        print("净值刷新失败:", fails)
    if (us_latest.get("fail") or {}):
        print("指数刷新失败:", us_latest["fail"])


if __name__ == "__main__":
    main()
