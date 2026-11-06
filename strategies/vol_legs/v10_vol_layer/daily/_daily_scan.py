# -*- coding: utf-8 -*-
"""波动资产 · V10 变种（美股两腿纯波动层）· 每日操作。

流程：
1. **先刷新数据**：美股指数（`.NDX` / `.INX`，增量）+ 场外 QDII 净值（仅这两条腿）；
   加 `--no-refresh` 用本地缓存，报告会标「未刷新」；
2. 对两条腿各算：`vol20`、目标仓位 `min(1, TV/vol20)`、与现仓的差、**下一次会动的阈值**
   （vol 要涨到多少才会再减 10pp）、数据新鲜度；
3. 输出：今日是否有动作、逐腿读数、**仓位阶梯（vol → 仓位对照）**、最近 8 次调仓。

**这一支没有方向判断**：它永远做多，只在波动率变化时调整仓位大小 ——
所以「今日动作」几乎天天有（死区 10pp 挡掉了大部分微调）。

产物（相对本文件）：daily/_v10_vol_layer_daily.md / _v10_vol_layer_daily.json。
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
_V10 = os.path.dirname(_HERE)
sys.path.insert(0, _V10)

import vol_layer as vl  # noqa: E402

OUT_MD = os.path.join(_HERE, "_v10_vol_layer_daily.md")
OUT_JSON = os.path.join(_HERE, "_v10_vol_layer_daily.json")
RECENT = 8


def leg_view(leg, refreshed, fails, us_latest):
    cfg = vl.rule.LEGS[leg]
    p = vl.params()
    nav, st, dec = vl.build(leg)
    sig = vl.load_signal(leg)
    v = float(vl.vol_series(leg).iloc[-1])
    tgt = vl.target_now(leg)

    d_sig = sig.index[-1]
    state_now = float(st.iloc[-1])
    last = dec[-1] if dec else None
    # 目标变化若落在最后一根净值日之后（指数比 QDII 净值新），这笔就还没成交 -> 待办
    pending = None
    if last is not None and last["date"] >= pd.Timestamp(nav.index[-1]) \
            and abs(float(last["to"]) - state_now) > 1e-9:
        pending = float(last["to"])

    # 下一次会动的阈值：仓位再降一档（10pp）需要 vol 升到 TV/(w-10pp)
    nxt = None
    if state_now > float(p["DEAD"]) + 1e-9:
        nxt = float(p["TV"]) / max(state_now - float(p["DEAD"]), 1e-9)
    # 反方向：仓位回补一档需要 vol 降到 TV/(w+10pp)
    up = float(p["TV"]) / min(state_now + float(p["DEAD"]), 1.0)

    ops = vl.actions(st)
    recent = ops[-RECENT:]
    return dict(
        leg=leg, name=cfg["name"], band=float(cfg["band"]),
        exec=cfg["exec"], exec_name=cfg["exec_name"], signal=cfg["signal"],
        nav_date=nav.index[-1].date().isoformat(), sig_date=d_sig.date().isoformat(),
        nav=float(nav.iloc[-1]), close=float(sig.iloc[-1]), vol=v, scale=float(p["TV"]) / v,
        target=tgt, state=state_now, level=vl.level_name(state_now), pending=pending,
        action=("下调 → " if pending is not None and pending < state_now else
                "上调 → " if pending is not None else "维持") +
               (vl.level_name(pending) if pending is not None else ""),
        thr_down=nxt, thr_up=up,
        fresh=_fresh(leg, refreshed, fails, us_latest),
        ops=[dict(date=o["date"].date().isoformat(), kind=o["kind"],
                  frm=o["frm"], to=o["to"]) for o in recent],
    )


def _fresh(leg, refreshed, fails, us_latest):
    code = vl.rule.LEGS[leg]["otc"]
    if not refreshed:
        return "·未刷新(本地缓存)"
    if code in fails:
        return f"❌净值刷新失败({code})"
    if us_latest.get("fail"):
        return "⚠指数部分失败"
    return "✅已刷新"


def ladder(vol_now):
    """仓位阶梯：vol → 目标仓位（TV / vol），标出当前 vol 落在哪一格。"""
    rows = []
    for w in (1.0, 0.9, 0.8, 0.7, 0.6, 0.5, 0.4, 0.3, 0.2):
        v = vl.TV / w
        rows.append(dict(vol=v, pos=w, now=abs(vol_now - v) < 0.02))
    return rows


def main():
    refreshed = "--no-refresh" not in sys.argv
    fails, us_latest = {}, {}
    if refreshed:
        codes = [vl.rule.LEGS[k]["otc"] for k in vl.POOL]
        try:
            _l, fails = vl.vdata.refresh_otc(codes=codes)
        except Exception as exc:                                 # noqa: BLE001
            fails = {c: f"{type(exc).__name__}: {exc}" for c in codes}
        try:
            _u, us_fails = vl.vdata.refresh_us()
            us_latest = dict(latest=_u, fail=us_fails)
        except Exception as exc:                                 # noqa: BLE001
            us_latest = dict(latest={}, fail={"__all__": f"{type(exc).__name__}: {exc}"})

    rows = [leg_view(k, refreshed, fails, us_latest) for k in vl.POOL]
    act = [r for r in rows if r["pending"] is not None]
    downs = [r for r in act if r["pending"] < r["state"]]
    ups = [r for r in act if r["pending"] > r["state"]]
    last_date = max(r["sig_date"] for r in rows)

    comb = sum(r["state"] * r["band"] for r in rows)
    comb_tgt = sum(r["target"] * r["band"] for r in rows)

    L = [
        f"# 波动资产 · V10（纯波动层 TV20）· 每日操作（{last_date}）",
        f"> 池 = **只有纳指100（{vl.rule.LEGS['ndx']['otc']}）与标普500"
        f"（{vl.rule.LEGS['spx']['otc']}，此前 050025 桥接）**；"
        "信号 = **美元指数**收盘（`.NDX` / `.INX`），执行 = 场外 QDII **人民币复权净值**。",
        f"> 规则：`仓位 = min(1, {vl.TV:.0%} / vol{vl.VOL_WIN})`，**没有方向判断**（永远做多，只调深度）；"
        f"日频 · 死区 {vl.DEAD:.0%} · 单边成本 {vl.COST:.1%} · 空仓按货基 {vl.CASH_ANNUAL:.2%} 计息。",
        "> 成交：信号日收盘决策 → **次一交易日收盘净值**（QDII 净值滞后 1~2 日，实盘按 T+1~T+2 提交）。",
        "> 机械规则输出，**非投资建议**。",
        "",
        "## 今日结论（明日成交）",
        "",
        "| 腿 | 现仓 | vol20 | TV/vol | 目标 | 今日动作 | vol 降到 | vol 升到 | 数据 |",
        "|---|---:|---:|---:|---|---|---:|---:|---|",
    ]
    for r in rows:
        L.append(f"| {r['name']} | **{r['state']:.0%}** | {r['vol']:.2%} | {r['scale']:.2f}× | "
                 f"{r['target']:.0%} | {r['action']} | {r['thr_up']:.1%} | "
                 f"{r['thr_down']:.1%} | {r['fresh']} |")
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
        "> 与 v7 / v8 不同：**这一支不看方向**。它不会因为「跌破年线」或「死叉」而清仓，"
        "只会因为「波动变大」而降仓。**慢熊里它帮不上忙**（阴跌段波动率会回落，仓位又回到满仓）。",
        "",
        "## 仓位阶梯（TV / vol → 仓位）",
        "",
        "| vol20 | 目标仓位 | 说明 |",
        "|---:|---:|---|",
    ]
    for x in ladder(rows[0]["vol"]):
        note = "← **当前纳指**" if x["now"] and rows[0]["leg"] == "ndx" else (
            "← 当前" if x["now"] else "")
        L.append(f"| {x['vol']:.1%} | {x['pos']:.0%} | {note} |")
    L += [
        "",
        f"> 读法：`vol20 ≤ {vl.TV:.0%}` → 满仓；`vol20 = {vl.TV / 0.5:.0%}` → 半仓；"
        f"`vol20 = {vl.TV / 0.25:.0%}` → 二五仓。**仓位是 vol 的倒数，不是方向的函数。**",
        "",
        "## 逐腿读数",
        "",
        "| 腿 | 执行代码 | 净值日 | 复权净值 | 信号代码 | 信号日 | 信号收盘 | 目标带 | 折合 pp |",
        "|---|---|---|---:|---|---|---:|---:|---:|",
    ]
    for r in rows:
        L.append(f"| {r['name']} | `{r['exec']}` {r['exec_name']} | {r['nav_date']} | "
                 f"{r['nav']:.4f} | `{r['signal']}` | {r['sig_date']} | {r['close']:.0f} | "
                 f"{r['band']:g}pp | {r['state'] * r['band']:.2f} |")
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
                     f"{frm:.0%} → {to:.0%}（{vl.level_name(to)}） |")
    else:
        L.append("| — | — | 无 | — |")
    L += [
        "",
        "## 提醒",
        "",
        "- **没有任何方向保护**：2000-2002 那种「跌两年」的行情里，急跌段过后波动率回落，"
        "仓位会重新回到满仓。本仓库样本（2012 起）不含这种行情，**别把回测数字外推长熊**。",
        "- **它治急跌**：2020 年 3 月波动率 5 天内爆表，仓位自动降下来 —— 不需要任何人判断方向。"
        "这是它唯一的、也是真实的优势。",
        "- **换手比 v7 高**：一年约 11 次调仓（v7 约 5 次）。场外 0.6% 口径下年化再掉约 0.6pp，"
        "怕摩擦就调宽死区（见 `research/_probe_vol_layer.md` §6）。",
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
            "strategy": vl.LABEL,
            "as_of": last_date,
            "rule": {"tv": vl.TV, "vol_win": vl.VOL_WIN, "dead": vl.DEAD, "floor": vl.FLOOR,
                     "trigger": vl.TRIGGER, "signal": "usd_index", "trade_at": "T+1 净值",
                     "cost": vl.COST, "cash_annual": vl.CASH_ANNUAL, "gate": None},
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
        print(f"  {r['name']:8s} 仓位 {r['state']:.0%} · vol20 {r['vol']:.2%} · "
              f"TV/vol {r['scale']:.2f} · 目标 {r['target']:.0%} · {r['action']} · {r['fresh']}")
    if fails:
        print("净值刷新失败:", fails)
    if (us_latest.get("fail") or {}):
        print("指数刷新失败:", us_latest["fail"])


if __name__ == "__main__":
    main()
