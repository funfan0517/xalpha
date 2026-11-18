# -*- coding: utf-8 -*-
"""波动资产 · V10 变种（全七腿 ER 自适应波动层）· 每日操作。

流程：
1. **先刷新数据**：真实指数长库 `data/_global_index_klines.json` / `_cn_index_klines.json`；
   信号与回测价格同源；加 `--no-refresh` 用本地缓存，报告会标「未刷新」；
2. 对每条腿各算：ER(20) 状态（趋势态 TV20% / 震荡态 TV10%）、`vol20`、
   目标仓位 `min(1, TV(ER)/vol20)`、与现仓的差、**下一次会动的阈值**、数据新鲜度；
3. 输出：今日是否有动作、逐腿读数、仓位阶梯、最近 8 次调仓。

默认档 = `ER20/10 + 10pp 幅度死区 + 5 日时间死区`（2026-11-17 用户指定）。
**没有涨跌方向判断**：ER 只判趋势纯度，不判多空；永远做多，只调露多少。

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
    tv_now = float(vl.tv_series(leg).iloc[-1])
    er_now = bool(vl.er_state(vl.efficiency_ratio(sig, p["ER_N"]),
                              p["ER_HI"], p["ER_LO"]).iloc[-1])
    tgt = vl.target_now(leg)

    d_sig = sig.index[-1]
    state_now = float(st.iloc[-1])
    last = dec[-1] if dec else None
    # 目标变化若落在最后一根净值日之后（指数比 QDII 净值新），这笔就还没成交 -> 待办
    pending = None
    if last is not None and last["date"] >= pd.Timestamp(nav.index[-1]) \
            and abs(float(last["to"]) - state_now) > 1e-9:
        pending = float(last["to"])

    # 下一次会动的阈值：仓位再降一档（10pp）需要 vol 升到 TV(ER)/(w-10pp)
    nxt = None
    if state_now > float(p["DEAD"]) + 1e-9:
        nxt = tv_now / max(state_now - float(p["DEAD"]), 1e-9)
    # 反方向：仓位回补一档需要 vol 降到 TV(ER)/(w+10pp)
    up = tv_now / min(state_now + float(p["DEAD"]), 1.0)

    ops = vl.actions(st)
    recent = ops[-RECENT:]
    return dict(
        leg=leg, name=cfg["name"], band=float(cfg["band"]),
        exec=cfg["exec"], exec_name=cfg["exec_name"], signal=cfg["idx"],
        nav_date=nav.index[-1].date().isoformat(), sig_date=d_sig.date().isoformat(),
        nav=float(nav.iloc[-1]), close=float(sig.iloc[-1]),
        vol=v, tv=tv_now, er_fast=er_now, scale=tv_now / v,
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
    sig = vl.rule.LEGS[leg]["idx"]
    if leg in fails or sig in fails:
        return f"⚠️真实指数刷新失败({sig})，用缓存"
    if not refreshed:
        return "·未刷新(本地缓存)"
    return "✅已刷新"


def ladder(vol_now, tv):
    """仓位阶梯：vol → 目标仓位（TV(ER) / vol），标出当前 vol 落在哪一格。"""
    rows = []
    for w in (1.0, 0.9, 0.8, 0.7, 0.6, 0.5, 0.4, 0.3, 0.2):
        v = float(tv) / w
        rows.append(dict(vol=v, pos=w, now=abs(vol_now - v) < 0.02))
    return rows


def main():
    refreshed = "--no-refresh" not in sys.argv
    fails, us_latest = {}, {}
    if refreshed:
        # 信号与回测价格都读 `_global_index_klines.json`（2005 起），刷新也必须刷这个库；
        # `_us_index_klines.json` 只有 2010 起，刷它不会让 v10 的信号变新。
        try:
            _l, fails = vl.vdata.refresh_global_index(legs=list(vl.POOL))
        except Exception as exc:                                  # noqa: BLE001
            fails = {k: f"{type(exc).__name__}: {exc}" for k in vl.POOL}

    rows = [leg_view(k, refreshed, fails, us_latest) for k in vl.POOL]
    act = [r for r in rows if r["pending"] is not None]
    downs = [r for r in act if r["pending"] < r["state"]]
    ups = [r for r in act if r["pending"] > r["state"]]
    last_date = max(r["sig_date"] for r in rows)

    comb = sum(r["state"] * r["band"] for r in rows)
    comb_tgt = sum(r["target"] * r["band"] for r in rows)

    L = [
        f"# 波动资产 · V10（ER 自适应波动层）· 每日操作（{last_date}）",
        f"> 池 = **全七腿**（{', '.join(vl.rule.LEGS[k]['name'] for k in vl.POOL)}）；"
        f"{vl.vdata.us_signal_exec_desc()}。",
        f"> 规则：`仓位 = min(1, TV(ER) / vol{vl.VOL_WIN})`；ER(20) 趋势态 TV {vl.TV:.0%} / "
        f"震荡态 TV {vl.TV_LOW:.0%}，**无涨跌方向判断**（永远做多，只调深度）；"
        f"日频 · 幅度死区 {vl.DEAD:.0%} + 时间死区 {vl.DEAD_DAYS} 日 · "
        f"单边成本 {vl.COST:.1%} · 空仓按货基 {vl.CASH_ANNUAL:.2%} 计息。",
        "> 成交：信号日收盘决策 → **次一交易日收盘净值**（QDII 净值滞后 1~2 日，实盘按 T+1~T+2 提交）。",
        "> 机械规则输出，**非投资建议**。",
        "",
        "## 今日结论（明日成交）",
        "",
        "| 腿 | ER | TV | 现仓 | vol20 | TV/vol | 目标 | 今日动作 | vol 降到 | vol 升到 | 数据 |",
        "|---|---|---:|---:|---:|---:|---|---|---:|---:|---|",
    ]
    for r in rows:
        L.append(f"| {r['name']} | {'趋势态' if r['er_fast'] else '震荡态'} | {r['tv']:.0%} | "
                 f"**{r['state']:.0%}** | {r['vol']:.2%} | {r['scale']:.2f}× | "
                 f"{r['target']:.0%} | {r['action']} | {r['thr_up']:.1%} | "
                 f"{r['thr_down']:.1%} | {r['fresh']} |")
    L += [
        "",
        (f"**有动作：{'、'.join(r['name'] for r in downs)} 下调"
         f"{'、' + '、'.join(r['name'] for r in ups) + ' 上调' if ups else ''}"
         f"，明日（次一交易日）收盘净值成交。**" if act else
         "**今日无动作 —— 七条腿都维持现仓**（`|Δ仓位| < 10pp` 或距上次调仓 < 5 日，死区挡掉了微调）。"),
        "",
        f"**组合层风险预算**：现仓 {comb:.1f}pp / 满仓 20pp（{comb / 20:.0%}），"
        f"目标 {comb_tgt:.1f}pp（{comb_tgt / 20:.0%}）。",
        "",
        "> 与 v7 / v8 不同：**这一支不看涨跌方向**，但 ER 会判趋势纯度。"
        "它不会因为「跌破年线」或「死叉」而清仓；震荡态会把 TV 压到 10%，"
        "慢熊不再必然满仓，但仍不是价格方向门。",
        "",
        "## 仓位阶梯（TV(ER) / vol → 仓位）",
        "",
        "| vol20 | 目标仓位 | 说明 |",
        "|---:|---:|---|",
    ]
    _r0 = rows[0]
    for x in ladder(_r0["vol"], _r0["tv"]):
        note = "← **当前纳指**" if x["now"] and _r0["leg"] == "ndx" else (
            "← 当前" if x["now"] else "")
        L.append(f"| {x['vol']:.1%} | {x['pos']:.0%} | {note} |")
    L += [
        "",
        f"> 读法（以 {_r0['name']} 当前 TV {_r0['tv']:.0%} 为例）：`vol20 ≤ {_r0['tv']:.0%}` → 满仓；"
        f"`vol20 = {_r0['tv'] / 0.5:.0%}` → 半仓；`vol20 = {_r0['tv'] / 0.25:.0%}` → 二五仓。"
        "**仓位是 vol 的倒数，不是涨跌方向的函数。**",
        "",
        "## 逐腿读数",
        "",
        "| 腿 | 执行代码 | 数据日 | 价格 | 信号代码 | 信号日 | 信号收盘 | 目标带 | 折合 pp |",
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
        "- **没有涨跌方向保护，但有 ER 状态门**：震荡态 TV 压到 10%，慢熊不再必然满仓；"
        "但 ER 只判趋势纯度，2000-2002 那种慢熊仍是外推，**别把回测数字外推长熊**。",
        "- **它治急跌**：2020 年 3 月波动率 5 天内爆表，仓位自动降下来 —— 不需要判断涨跌方向。",
        "- **换手不低**：默认 D 档已用 5 日时间死区压过一轮，但仍高于固定 TV20；"
        "场外 0.6% 口径的拖累见回测对应档，怕摩擦就调宽幅度死区、关掉 ER 或改用 v9。",
        "- **QDII 通道**：纳指/标普 QDII 可能限购、暂停申购或溢价 —— 触发降仓时若无法赎回，"
        "按纪律顺延到下一可交易日，**不要为追规则去买场内溢价**。",
        "- 与主策略的关系：本变种只管**全七腿各露多少目标带**（带位仍由 `rule` 决定），"
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
            "rule": {"adaptive": vl.ADAPTIVE, "tv": vl.TV, "tv_low": vl.TV_LOW,
                     "er_n": vl.ER_N, "er_hi": vl.ER_HI, "er_lo": vl.ER_LO,
                     "vol_win": vl.VOL_WIN, "dead": vl.DEAD, "dead_days": vl.DEAD_DAYS,
                     "floor": vl.FLOOR, "trigger": vl.TRIGGER, "signal": "index",
                     "trade_at": "T+1 净值", "cost": vl.COST,
                     "cash_annual": vl.CASH_ANNUAL, "gate": None},
            "summary": {"action": bool(act), "down": [r["leg"] for r in downs],
                        "up": [r["leg"] for r in ups],
                        "combo_pp": comb, "combo_target_pp": comb_tgt,
                        "refreshed": refreshed, "fail_index": list(fails)},
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
        print("真实指数刷新失败:", fails)


if __name__ == "__main__":
    main()
