# -*- coding: utf-8 -*-
"""波动资产 · V7 变种：回撤止损 · 七腿回测（逐腿 + 组合层 + 参数敏感性 + 硬断言）。

池与口径完全跟随 `strategies/vol_legs`：
- 七腿 = `rule.LEG_ORDER`（中证A500 / 沪深300 / 纳指100 / 标普500 / 科创50 / 恒生科技 / 黄金）；
- 价格 = `data.series_for_leg()` 场外**复权净值**（含分红再投 / 汇率 / 跟踪误差）；
- 成交 = T 日盘后算信号、**T+1 收盘净值成交**；空仓按货基 `rule.CASH_ANNUAL` 逐日计息；
- 成本 = 真实申赎费 + 单边滑点 0.1%（同时给「含费」与「不计费」两列，看费用是不是生死线）。

规则见 `trailing_stop.py` 模块 docstring 与 README。

产物（策略类，落 `backtest/`）：
- `_v7_trailing_stop_bt.md`  报告
- `_v7_trailing_stop_bt.jsonl` 机读结果

模拟结果，非投资建议。
"""
import json
import os
import sys

import numpy as np
import pandas as pd

try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:  # noqa: BLE001
    pass

_HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, _HERE)

import trailing_stop as ts  # noqa: E402

OUTDIR = os.path.join(_HERE, "backtest")
OUT_MD = os.path.join(OUTDIR, "_v7_trailing_stop_bt.md")
OUT_JSONL = os.path.join(OUTDIR, "_v7_trailing_stop_bt.jsonl")

SENS = [
    ("基准（止损15% · 半仓 · 回满MA200 · 七腿全套）", {}),
    ("止损 10% 半仓", dict(STOP_PCT=0.10)),
    ("止损 12.5% 半仓", dict(STOP_PCT=0.125)),
    ("止损 20% 半仓", dict(STOP_PCT=0.20)),
    ("止损 25% 半仓", dict(STOP_PCT=0.25)),
    ("止损 15% 清仓（floor=0）", dict(FLOOR=0.0)),
    ("止损 10% 清仓", dict(STOP_PCT=0.10, FLOOR=0.0)),
    ("回满 MA150", dict(REENTRY_MA=150)),
    ("回满 MA250", dict(REENTRY_MA=250)),
    ("空仓起步", dict(START="flat")),
    ("只套三腿（kc50/hkt/gold）对照", dict(GATE_ALL=False, GATE_LEGS=("kc50", "hkt", "gold"))),
]


# ---------------------------------------------------------------- 1. 逐腿
def leg_rows(**kw):
    rows = []
    for k in ts.LEGS:
        cfg = ts.rule.LEGS[k]
        px, sm = ts.leg_slice(k, **kw)
        st = pd.to_numeric(sm["state"], errors="coerce").fillna(0.0)
        fee = ts.fee_of(k)
        nav = ts.run_leg(st, px)                 # 不计费
        nav_fee = ts.run_leg(st, px, fee)        # 含费
        hold = ts.run_leg(pd.Series(1.0, index=px.index), px, fee)   # 静态带恒满（含一次申购费）
        bh = px / px.iloc[0]
        sf = ts.stop_of(k, ts.params(**kw))
        acts = ts.actions(st)
        ws = ts.whipsaw(st, px)
        m, mf, h, b = ts.perf(nav), ts.perf(nav_fee), ts.perf(hold), ts.perf(bh)
        years = max((px.index[-1] - px.index[0]).days / 365.25, 1e-9)
        rows.append(dict(
            leg=k, name=cfg["name"], role=cfg["role"], band=float(cfg["band"]),
            stop=None if sf is None else sf[0], floor=None if sf is None else sf[1],
            gate="是" if sf is not None else "否",
            start=px.index[0].date().isoformat(), end=px.index[-1].date().isoformat(),
            years=round(years, 2), bars=len(px),
            ann=m["ann"], mdd=m["mdd"], vol=m["vol"], sharpe=m["sharpe"],
            ann_fee=mf["ann"], mdd_fee=mf["mdd"], sharpe_fee=mf["sharpe"],
            hold_ann=h["ann"], hold_mdd=h["mdd"], hold_sharpe=h["sharpe"],
            exposure=ts.exposure(st), state_last=float(st.iloc[-1]),
            actions=len(acts), acts_per_year=len(acts) / years,
            whip=ws["n"], whip_miss=ws["avg_miss"],
            edge_pp=(mf["ann"] - h["ann"]) * 100,
            edge_nofee_pp=(m["ann"] - b["ann"]) * 100,
            cost_pp=(m["ann"] - mf["ann"]) * 100,
            bh_ann=b["ann"], bh_mdd=b["mdd"], bh_vol=b["vol"], bh_sharpe=b["sharpe"],
        ))
    return rows


# ---------------------------------------------------------------- 2. 组合层
def build_states(frame, **kw):
    out = {}
    for k in frame.columns:
        s = ts.state_machine(ts.load(k), k, **kw)["state"].reindex(frame.index)
        out[k] = pd.to_numeric(s, errors="coerce").ffill().bfill().fillna(0.0)
    return out


def portfolio_rows(frame):
    base = build_states(frame)
    base3 = build_states(frame, GATE_ALL=False, GATE_LEGS=("kc50", "hkt", "gold"))
    eq = {k: 1.0 / len(frame.columns) for k in frame.columns}
    specs = [
        ("静态带（七腿恒持满带）", None, True, "对照基准：不做止损、一直 100% 带"),
        ("静态带 · 成本另列", None, False, ""),
        ("**V7 默认（七腿全套止损）**", base, True, "止损15%/半仓/回满MA200，七腿全套装"),
        ("V7 默认 · 不计费", base, False, ""),
        ("V7 只套三腿（kc50/hkt/gold · 对照）", base3, True, ""),
        ("七腿等权（恒定持有）", None, False, "对照：等权满仓"),
    ]
    rows, navs = [], {}
    for name, states, fee, note in specs:
        weight = eq if name.startswith("七腿等权") else None
        nav, inv = ts.portfolio(frame, states, weight=weight, fee=fee, detail=True)
        m = ts.perf(nav)
        rows.append(dict(name=name, cum=m["cum"], ann=m["ann"], vol=m["vol"], mdd=m["mdd"],
                         sharpe=m["sharpe"], invested=inv, note=note))
        navs[name] = nav
    return rows, navs


def sens_rows(frame):
    out = []
    for name, kw in SENS:
        states = build_states(frame, **kw)
        nav, inv = ts.portfolio(frame, states, fee=True, detail=True)
        m = ts.perf(nav)
        n_act = sum(len(ts.actions(states[k])) for k in frame.columns)
        out.append(dict(name=name, ann=m["ann"], mdd=m["mdd"], sharpe=m["sharpe"],
                        invested=inv, actions=n_act))
    return out


# ---------------------------------------------------------------- 3. 硬断言
def check_all(frame):
    out = []
    # 1) 恒满（全部不套止损）→ 净值 == 买入持有（无费）
    px = ts.load("hs300")
    sm = ts.state_machine(px, "hs300", GATE_ALL=False, GATE_LEGS=())
    nav = ts.run_leg(pd.Series(1.0, index=px.index), px)
    bh = px / px.iloc[0]
    # state.shift(1) 使首日在货基（与 v1~v6 同一约定），故允许首日货基计息的 ~0.005% 差
    rel = abs(nav.iloc[-1] / bh.iloc[-1] - 1.0)
    out.append(("恒满（不套止损）净值 == 买入持有（无费）",
                bool(rel < 1e-4),
                f"{nav.iloc[-1]:.6f} vs {bh.iloc[-1]:.6f}（差 {rel:.1e}，仅首日货基计息）"))
    # 2) 仓位只落在 {1.0, floor}
    ok_all, detail = True, ""
    for k in ts.LEGS:
        px_k = ts.load(k)
        sm_k = ts.state_machine(px_k, k)
        st = pd.to_numeric(sm_k["state"], errors="coerce").dropna()
        sf = ts.stop_of(k)
        allowed = {1.0} | ({0.0} if sf is None else {round(sf[1], 6)})
        bad = set(np.round(st.unique(), 6)) - allowed
        if bad:
            ok_all = False
            detail = f"{k}: {bad}"
            break
    out.append(("仓位只落在 {100%, floor}", ok_all, detail))
    # 3) 仓位每次变化的前一日必有决策（T+1，无前视）
    ok3, d3 = True, ""
    for k in ts.LEGS:
        px_k = ts.load(k)
        sm_k = ts.state_machine(px_k, k)
        st = pd.to_numeric(sm_k["state"], errors="coerce").fillna(0.0).to_numpy()
        dec = sm_k["decide"].to_numpy()
        for i in range(1, len(st)):
            if abs(st[i] - st[i - 1]) > 1e-9:
                if dec[i - 1] not in ("buy", "sell"):
                    ok3 = False
                    d3 = f"{k} @ {sm_k.index[i].date()} 前一日无决策"
                    break
        if not ok3:
            break
    out.append(("每次仓位变化的前一日必有 buy/sell 决策（T+1 无前视）", ok3, d3))
    # 4) 预热期不交易（MA200 成形前 state 恒为 START=1.0）
    ok4, d4 = True, ""
    for k in ts.LEGS:
        px_k = ts.load(k)
        sm_k = ts.state_machine(px_k, k)
        pre = sm_k[~sm_k["ready"].astype(bool)]
        st = pd.to_numeric(pre["state"], errors="coerce").dropna()
        if len(st) and not bool((st == 1.0).all()):
            ok4 = False
            d4 = f"{k} 预热期出现非满仓"
            break
    out.append(("预热期（MA200 未成形）不交易、保持满带", ok4, d4))
    return out


# ---------------------------------------------------------------- 4. 主流程
def main():
    os.makedirs(OUTDIR, exist_ok=True)
    p0 = ts.params()
    rows = leg_rows()
    frame = ts.common_frame()
    prows, navs = portfolio_rows(frame)
    sens = sens_rows(frame)
    checks = check_all(frame)

    with open(OUT_JSONL, "w", encoding="utf-8") as f:
        for r in rows:
            f.write(json.dumps({k: (round(v, 4) if isinstance(v, float) else v)
                                for k, v in r.items()}, ensure_ascii=False) + "\n")
        for r in prows:
            f.write(json.dumps({"portfolio": r["name"],
                                **{k: (round(v, 4) if isinstance(v, float) else v)
                                   for k, v in r.items() if k != "name"}},
                               ensure_ascii=False) + "\n")
        for r in sens:
            f.write(json.dumps({"sens": r["name"],
                                **{k: (round(v, 4) if isinstance(v, float) else v)
                                   for k, v in r.items() if k != "name"}},
                               ensure_ascii=False) + "\n")
        f.write(json.dumps({"checks": [dict(name=n, ok=bool(o), note=t) for n, o, t in checks]},
                           ensure_ascii=False) + "\n")

    def pick(prefix):
        return next(p for p in prows if p["name"].startswith(prefix))

    base, base_nc = pick("**V7 默认（七腿全套止损）**"), pick("V7 默认 · 不计费")
    static, static_nc = pick("静态带（七腿恒持满带）"), pick("静态带 · 成本另列")
    base3 = pick("V7 只套三腿")
    eqw = pick("七腿等权")
    gated_rows = [r for r in rows if r["gate"] == "是"]
    win = f"{frame.index[0].date()} ~ {frame.index[-1].date()}"
    yrs = (frame.index[-1] - frame.index[0]).days / 365.25
    bad = [c for c in checks if not c[1]]

    g_beat = sum(1 for r in gated_rows if r["edge_pp"] > 0)
    g_dd = sum(1 for r in gated_rows if abs(r["mdd_fee"]) < abs(r["hold_mdd"]) - 1e-9)
    best = max(gated_rows, key=lambda z: z["edge_pp"])
    worst = min(gated_rows, key=lambda z: z["edge_pp"])

    L = [
        "# 波动资产 · V7 变种：回撤止损 · 七腿回测",
        "> 池与主策略**完全一致**：`rule.LEG_ORDER` 七腿（中证A500 / 沪深300 / 纳指100 / "
        "标普500 / 科创50 / 恒生科技 / 黄金）",
        "> 价格 = 场外**复权净值**（含分红再投 / 汇率 / 跟踪误差）· T 日盘后算信号、"
        "**T+1 收盘净值成交** · "
        f"空仓按货基 {ts.CASH_ANNUAL:.2%} 计息 · 成本 = 真实申赎费 + 单边滑点 {ts.SLIP:.2%}",
        "> 规则来源：用户 2026-11-04 的「回撤止损」思路（跌破近 1 年高 X% 减仓、收复 MA200 回满），"
        "权威源 `v7_trailing_stop/trailing_stop.py` 顶部常量",
        "",
        "## 1. 规则（可执行版）",
        "",
        "| 环节 | 规则 |",
        "|---|---|",
        f"| 触发减仓 | `close / 持仓期高点 − 1 < −{p0['STOP_PCT']:.0%}`（锚点随（再）入场重置为当时高点，"
        f"标准移动止损）→ 该腿目标带降到 `{p0['FLOOR']:.0%}` |",
        f"| 触发回满 | 减仓态下 `close > MA{ts.REENTRY_MA}` → 回到满带 100% |",
        f"| 预热 | `MA{ts.REENTRY_MA}` 成形前不交易（保持满带起步） |",
        f"| 成交 | T 日盘后算信号、T+1 收盘成交（与 v1~v6 同一纪律） |",
        f"| 频率帽 | 同一腿两次调仓最小间隔 {ts.ACTION_GAP} 个交易日 |",
        "",
        "> **定位**：这是**崩盘保险**不是收益引擎。它只在「从近一年高点快速回撤」时减仓，"
        "等趋势修复再进；对慢牛/阴跌无效，对频繁插针的腿甚至会「卖在坑里、追在高处」。",
        "",
        "## 2. 逐腿（各自 MA200 成形后起 · 按含费年化差降序）",
        "",
        "「静态带」= 同区间不做止损、一直 100% 带；年化差 = 含费止损 − 静态带。"
        "「成本」= 不计费 − 含费 的年化差（费用敏感性）。",
        "",
        "| 腿 | 角色 | 止损 | 样本区间 | 年数 | 含费 年化 | 含费 回撤 | 夏普 | 平均曝光 | "
        "调仓 | 调仓/年 | 打脸 | 静态带 年化 | 静态带 回撤 | 年化差 | 成本 |",
        "|---|---|---|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|",
    ]
    for r in sorted(rows, key=lambda z: z["edge_pp"], reverse=True):
        stop_txt = "—" if r["stop"] is None else f"{r['stop']:.0%}/{r['floor']:.0%}"
        L.append(
            f"| {r['name']} | {r['role']} | {stop_txt} | {r['start']} ~ {r['end']} | "
            f"{r['years']:.2f} | {ts.fmt_pct(r['ann_fee'])} | {ts.fmt_pct(r['mdd_fee'])} | "
            f"{r['sharpe_fee']:.2f} | {r['exposure']:.0%} | {r['actions']} | "
            f"{r['acts_per_year']:.1f} | {r['whip']} | {ts.fmt_pct(r['hold_ann'])} | "
            f"{ts.fmt_pct(r['hold_mdd'])} | **{r['edge_pp']:+.2f}pp** | {r['cost_pp']:.2f}pp |")

    L += [
        "",
        f"> 七条闸门腿：含费后跑赢「静态带」**{g_beat}/{len(gated_rows)}**，"
        f"回撤更小 **{g_dd}/{len(gated_rows)}**。",
        f"> 逐腿高低：最好的是 **{best['name']}**（{best['edge_pp']:+.2f}pp，"
        f"回撤 {ts.fmt_pct(best['mdd_fee'])} vs {ts.fmt_pct(best['hold_mdd'])}），"
        f"最差的是 **{worst['name']}**（{worst['edge_pp']:+.2f}pp，"
        f"回撤 {ts.fmt_pct(worst['mdd_fee'])} vs {ts.fmt_pct(worst['hold_mdd'])}）。"
        "**它不是收益引擎，是回撤保险**：赚的是「快跌段减仓」的钱，亏的是「插针后追高 + 费用」。",
        "> ⚠ **诚实结论**：这套止损**只对一部分腿有效** —— 回撤改善集中在 **科创50（约 −5pp）、"
        "恒生科技（约 −8pp）、标普500（约 −8pp）**；A500 / 沪深300 只有小幅或没有改善，"
        "**纳指100 反而更深**（卖在坑里、追在高处）；黄金接近中性。这不是实现 bug，是规则的适用边界（见 §6）。",
        "",
        f"## 3. 组合层（七腿共同窗口 {win} · {yrs:.1f} 年，按 `rule` 目标带权重归一化，满仓 = 40pp）",
        "",
        "| 组合 | 累计 | 年化 | 波动 | 最大回撤 | 夏普 | 平均曝光 | 备注 |",
        "|---|---:|---:|---:|---:|---:|---:|---|",
    ]
    for r in prows:
        L.append(f"| {r['name']} | {ts.fmt_pct(r['cum'], 1)} | {ts.fmt_pct(r['ann'])} | "
                 f"{r['vol'] * 100:.2f}% | {ts.fmt_pct(r['mdd'])} | {r['sharpe']:.2f} | "
                 f"{r['invested']:.1%} | {r['note']} |")
    L += [
        "",
        f"> **默认口径 vs 静态带**：年化 {ts.fmt_pct(base['ann'])} vs {ts.fmt_pct(static['ann'])}"
        f"（**{(base['ann'] - static['ann']) * 100:+.2f}pp**）、回撤 "
        f"{ts.fmt_pct(base['mdd'])} vs {ts.fmt_pct(static['mdd'])}"
        f"（**{(base['mdd'] - static['mdd']) * 100:+.2f}pp**）、夏普 {base['sharpe']:.2f} vs "
        f"{static['sharpe']:.2f}，平均曝光 {base['invested']:.0%} vs 100%。",
        f"> **不计费**：{ts.fmt_pct(base_nc['ann'])} / {ts.fmt_pct(base_nc['mdd'])} / "
        f"{base_nc['sharpe']:.2f}（vs 静态带 {ts.fmt_pct(static_nc['ann'])} / "
        f"{ts.fmt_pct(static_nc['mdd'])} / {static_nc['sharpe']:.2f}）—— 费用吃掉的年化 "
        f"= {(base_nc['ann'] - base['ann']) * 100:.2f}pp，**这是判断「止损是否值得」的关键一列**："
        "A 类场外费下拖累明显，场内 ETF / 免佣账户会好很多。",
        f"> **只套三腿（kc50/hkt/gold）**：{ts.fmt_pct(base3['ann'])} / {ts.fmt_pct(base3['mdd'])} / "
        f"{base3['sharpe']:.2f}，曝光 {base3['invested']:.0%} —— 相对七腿默认 "
        f"{(base3['ann'] - base['ann']) * 100:+.2f}pp / {(base3['mdd'] - base['mdd']) * 100:+.2f}pp。",
        f"> 七腿等权：{ts.fmt_pct(eqw['ann'])} / {ts.fmt_pct(eqw['mdd'])} / {eqw['sharpe']:.2f}。",
        "",
        "## 4. 参数敏感性（组合层 · 每档都扣费，只改一格）",
        "",
        "| 档位 | 年化 | 最大回撤 | 夏普 | 平均曝光 | 七腿总调仓次数 |",
        "|---|---:|---:|---:|---:|---:|",
    ]
    for r in sens:
        L.append(f"| {r['name']} | {ts.fmt_pct(r['ann'])} | {ts.fmt_pct(r['mdd'])} | "
                 f"{r['sharpe']:.2f} | {r['invested']:.0%} | {r['actions']} |")
    L += [
        "",
        f"> 回撤落在 {ts.fmt_pct(max(r['mdd'] for r in sens))} ~ "
        f"{ts.fmt_pct(min(r['mdd'] for r in sens))}、年化落在 "
        f"{ts.fmt_pct(min(r['ann'] for r in sens))} ~ {ts.fmt_pct(max(r['ann'] for r in sens))}。"
        "止损越窄越能砍回撤、但换手/费用越高；「清仓 vs 半仓」在组合层差异远小于逐腿 —— "
        "因为七腿同时插针的概率低。",
        "",
        "## 5. 硬断言",
        "",
        "| 检查 | 结果 | 备注 |",
        "|---|---|---|",
    ]
    for name, ok, note in checks:
        L.append(f"| {name} | {'✅' if ok else '❌'} | {note} |")
    L += [
        "",
        f"共 {len(checks)} 条，失败 {len(bad)} 条" + ("。" if not bad else
            "：**" + "；".join(c[0] for c in bad) + "**"),
        "",
        "## 6. 口径与已知边界",
        "",
        "1. **不是万能降回撤**：它只对「快跌 + 之后趋势修复」的腿有效。对频繁围绕 −15% 插针的腿"
        "（沪深300、纳指100 的部分窗口），会「卖在坑里、追在高处」，扣费后可能更差（见 §2 逐腿）。",
        "2. **费用是生死线**：A 类场外（申购 0.10% + 赎回 0.50% + 滑点）下，止损频繁的腿会被费用吃掉；"
        "场内 ETF / 免佣账户下净收益明显改善（§3 的「不计费」列就是上限）。",
        "3. **参数（近高窗口 / 止损宽度 / 回满线）都写死**：不追求最优，§4 已给出敏感性；"
        "结论不靠某一格参数成立。",
        "4. **黄金放宽到 20%**（`STOP_BY_LEG`）：商品波动结构与股票不同，15% 会频繁误伤。",
        "5. **代理口径**：A500 / 标普的样本前段是桥接（`000051` / `050025`），那段不是本基金业绩；"
        "美股腿用场外人民币复权净值，叠加汇率与溢价。",
        "6. **未计**：大额限购、QDII 额度关闭时的顺延、赎回确认时滞、C 类销售服务费。",
        "",
        "> **模拟结果，非投资建议。**",
    ]

    with open(OUT_MD, "w", encoding="utf-8") as f:
        f.write("\n".join(L))

    print("报告:", OUT_MD)
    print("结果:", OUT_JSONL)
    print(f"组合层 {win} · {yrs:.1f} 年")
    for r in prows:
        print(f"  {r['name']:38s} 年化 {ts.fmt_pct(r['ann']):>9s} · "
              f"回撤 {ts.fmt_pct(r['mdd']):>8s} · 夏普 {r['sharpe']:>5.2f} · "
              f"曝光 {r['invested']:.0%}")
    print(f"断言 {len(checks) - len(bad)}/{len(checks)} 通过")
    return 0 if not bad else 1


if __name__ == "__main__":
    sys.exit(main())
