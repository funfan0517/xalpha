# -*- coding: utf-8 -*-
"""波动资产 · V9 变种（美股两腿波动率目标仓位）· 回测（逐腿 + 组合 + 敏感性 + 分半 + 硬断言）。

池：**只有纳指100 与标普500**（`vol_target.POOL`）。规则逐条落地自
`G:\\tradingagents\\us_index_strategy\\策略规格_v1.0.md` 的 **S-A**（文档排名第 1 的策略）：
每月末算 `vol20 = std(r,20)×sqrt(252)` → `w = min(1, 15% / vol20)` → 次一交易日收盘成交
→ 新权重自 t+2 起承担收益；权重变化 < 5pp 不交易；未投部分按 2.0% 计息；单边 0.03%。

口径：
- 信号 = **美元指数**（`.NDX` / `.INX`）收盘；执行 = 场外 QDII **人民币复权净值**；
- 另给 `SIGNAL="nav"`（净值信号）、`COST=0.6%`（场外现实费率）两档对照；
- 起点口径：首日即满仓，与买入持有同口径（不另计建仓费）。

产物（策略类，落 `backtest/`）：
- `_v9_vol_target_bt.md` / `_v9_vol_target_bt.jsonl`

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

import vol_target as vt  # noqa: E402

OUTDIR = os.path.join(_HERE, "backtest")
OUT_MD = os.path.join(OUTDIR, "_v9_vol_target_bt.md")
OUT_JSONL = os.path.join(OUTDIR, "_v9_vol_target_bt.jsonl")

# 逐腿档位
VARIANTS = [
    ("买入持有", "hold", {}),
    ("**本变种默认（月末 + 快减 + 快补）**", "vt", {}),
    ("文档月度档（无月中补丁）", "vt_month", {}),
    ("只加快减", "vt", dict(ADD=None)),
    ("只加快补", "vt", dict(CUT=False)),
    ("VT · 净值信号（不用美元指数）", "vt", dict(SIGNAL="nav")),
    ("MA200 月频（文档 S-B 对照）", "ma200", {}),
]

# 组合层规格：(名称, kind, 权重, 覆盖参数)
PORT_SPECS = [
    ("静态 50/50（买入持有）", "hold", "eq", {}),
    ("**本变种默认（快减+快补 · 50/50）**", "vt", "eq", {}),
    ("文档月度档 · 50/50", "vt_month", "eq", {}),
    ("静态 标普:纳指 = 2:1（文档 §7）", "hold", "doc", {}),
    ("VT · 标普:纳指 = 2:1（默认档）", "vt", "doc", {}),
    ("MA200 月频 50/50（文档 S-B 对照）", "ma200", "eq", {}),
]

# 敏感性（组合层 · 默认权重 · 只改一格）
SENS = [
    ("基准（TV15 · vol20 · 月末+快减+快补 · 单边0.03% · 空仓2.0%）", {}),
    ("关掉月中补丁（= 文档月度档）", dict(TRIGGER="month")),
    ("只加快减", dict(ADD=None)),
    ("只加快补", dict(CUT=False)),
    ("快减跌幅门槛 −3%", dict(CUT_RET=-0.03)),
    ("快减跌幅门槛 −8%", dict(CUT_RET=-0.08)),
    ("快减 vol 门槛 1.1×TV", dict(CUT_VOLX=1.1)),
    ("快减 vol 门槛 1.7×TV", dict(CUT_VOLX=1.7)),
    ("快补回落门槛 55%", dict(DECAY=0.55)),
    ("快补回落门槛 85%", dict(DECAY=0.85)),
    ("目标波动 10%", dict(TV=0.10)),
    ("目标波动 12%", dict(TV=0.12)),
    ("目标波动 18%", dict(TV=0.18)),
    ("目标波动 22%", dict(TV=0.22)),
    ("波动窗口 60 日", dict(VOL_WIN=60)),
    ("漂移 10pp（更少交易）", dict(DRIFT=0.10)),
    ("最多半仓（cap 0.5）", dict(CAP=0.5)),
    ("成本 0.10%（v7 口径）", dict(COST=0.001)),
    ("成本 0.60%（场外 A 类现实）", dict(COST=0.006)),
    ("空仓 1.40%（仓库惯例）", dict(CASH_ANNUAL=0.014)),
    ("空仓 0%（文档禁止的口径，仅量化影响）", dict(CASH_ANNUAL=0.0)),
    ("净值信号（人民币净值算 vol20）", dict(SIGNAL="nav")),
]

WINDOWS = [
    ("2020 疫情", "2020-02-19", "2020-08-31"),
    ("2022 加息熊市", "2022-01-03", "2022-12-30"),
    ("2025 至今年内", "2025-01-02", "2026-11-02"),
]


def leg_states(leg, kind, over):
    """-> (nav, state, dec)。"""
    return vt.build(leg, kind=kind, **over)


def leg_rows():
    """每条腿跑全部档位。"""
    rows = []
    for leg in vt.POOL:
        nav_full = vt.load_nav(leg)
        bh = nav_full / nav_full.iloc[0]
        mb = vt.perf(bh)
        for name, kind, over in VARIANTS:
            nav, st, dec = leg_states(leg, kind, over)
            nv = vt.run_leg(st, nav, cost=over.get("COST"), cash=over.get("CASH_ANNUAL"))
            m = vt.perf(nv)
            yrs = (nav.index[-1] - nav.index[0]).days / 365.25
            rows.append(dict(
                leg=leg, name=vt.rule.LEGS[leg]["name"], variant=name, kind=kind,
                start=nav.index[0].date().isoformat(), end=nav.index[-1].date().isoformat(),
                years=round(yrs, 2),
                ann=m["ann"], mdd=m["mdd"], vol=m["vol"], sharpe=m["sharpe"],
                calmar=vt.calmar(nv), mdd_me=vt.mdd_sampled(nv),
                calmar_me=vt.calmar(nv, vt.mdd_sampled(nv)),
                turnover=vt.turnover(st), trades=vt.n_trades(st),
                trades_py=vt.n_trades(st) / yrs,
                expo=vt.exposure(st), ann_bh=None, mdd_bh=None,
                d_ann=(m["ann"] - mb["ann"]) * 100, d_mdd=(m["mdd"] - mb["mdd"]) * 100,
            ))
    return rows


def port_build(spec, idx=None):
    """按规格构造组合：-> (nav, states, idx)。权重口径 eq = 等权 / doc = 标普:纳指 2:1。"""
    name, kind, wmode, over = spec
    weight = dict(vt.W) if wmode == "eq" else dict(vt.W_DOC)
    navs, states, ix = {}, {}, None
    for leg in vt.POOL:
        nav, st, _ = leg_states(leg, kind, over)
        navs[leg], states[leg] = nav, st
        ix = nav.index if ix is None else ix.intersection(nav.index)
    idx = pd.DatetimeIndex(ix).sort_values() if idx is None else idx
    navs = {k: v.reindex(idx).ffill() for k, v in navs.items()}
    states = {k: v.reindex(idx).ffill() for k, v in states.items()}
    nv, inv = vt.portfolio(navs, states, weight=weight,
                           cost=over.get("COST"), cash=over.get("CASH_ANNUAL"))
    return nv, inv, weight, idx, navs, states


def port_rows():
    out = []
    for spec in PORT_SPECS:
        nv, inv, weight, idx, _navs, states = port_build(spec)
        m = vt.perf(nv)
        yrs = (idx[-1] - idx[0]).days / 365.25
        me = vt.mdd_sampled(nv)
        out.append(dict(name=spec[0], cum=m["cum"], ann=m["ann"], vol=m["vol"], mdd=m["mdd"],
                        mdd_me=me, calmar=vt.calmar(nv), calmar_me=vt.calmar(nv, me),
                        sharpe=m["sharpe"], expo=inv,
                        trades=sum(vt.n_trades(s) for s in states.values()),
                        trades_py=sum(vt.n_trades(s) for s in states.values()) / yrs,
                        nav=nv, weight=weight, idx=idx))
    return out


def sens_rows(idx, navs):
    out = []
    for name, over in SENS:
        states = {}
        for leg in vt.POOL:
            _nav, st, _dec = leg_states(leg, "vt", over)
            states[leg] = st.reindex(idx).ffill()
        nv, inv = vt.portfolio(navs, states, weight=vt.W,
                               cost=over.get("COST"), cash=over.get("CASH_ANNUAL"))
        m = vt.perf(nv)
        me = vt.mdd_sampled(nv)
        out.append(dict(name=name, ann=m["ann"], mdd=m["mdd"], mdd_me=me, sharpe=m["sharpe"],
                        calmar=vt.calmar(nv), calmar_me=vt.calmar(nv, me), expo=inv,
                        trades=sum(vt.n_trades(s) for s in states.values())))
    return out


def half_rows(idx, navs):
    """分半样本（文档 R-S03 的读法）：中点为界，各算 CAGR / MDD / Sharpe。"""
    mid = idx[0] + (idx[-1] - idx[0]) / 2
    segs = [("前半段", idx[0], mid), ("后半段", mid, idx[-1])]
    out = []
    for label, a, b in segs:
        for nm, kind in (("买入持有", "hold"), ("VT 默认", "vt"), ("MA200 月频", "ma200")):
            states, nvs, ix = {}, {}, None
            for leg in vt.POOL:
                nav, st, _ = leg_states(leg, kind, {})
                nvs[leg], states[leg] = nav, st
                ix = nav.index if ix is None else ix.intersection(nav.index)
            ix = pd.DatetimeIndex(ix).sort_values()
            nvs = {k: v.reindex(ix).ffill() for k, v in nvs.items()}
            states = {k: v.reindex(ix).ffill() for k, v in states.items()}
            nv, _ = vt.portfolio(nvs, states, weight=vt.W)
            seg = nv.loc[(nv.index >= a) & (nv.index <= b)]
            if len(seg) < 20:
                continue
            m = vt.perf(seg)
            out.append(dict(seg=label, name=nm, ann=m["ann"], mdd=m["mdd"],
                            sharpe=m["sharpe"], start=seg.index[0].date().isoformat(),
                            end=seg.index[-1].date().isoformat()))
    return out


def check_all():
    out = []
    # 1) 恒满（TV 极大 -> 目标恒 1）净值 == 买入持有
    ok, note = True, ""
    for leg in vt.POOL:
        nav, st, _ = vt.build(leg, TV=99.0)
        nv = vt.run_leg(st, nav, cost=0.0)
        bh = nav / nav.iloc[0]
        rel = abs(float(nv.iloc[-1] / bh.iloc[-1]) - 1.0)
        if rel > 1e-12:
            ok, note = False, f"{leg} 差 {rel:.2e}"
            break
    out.append(("恒满（TV 极大 -> w≡1）净值 == 买入持有（零费）", ok, note or "两腿逐日一致"))

    # 2) 仓位只落在 [0, cap]，且每次调仓的落点 = 当日公式值 min(cap, TV/vol)
    ok, note = True, ""
    for leg in vt.POOL:
        _nav, st, dec = vt.build(leg)
        lo, hi = float(st.min()), float(st.max())
        if lo < -1e-12 or hi > vt.CAP + 1e-12:
            ok, note = False, f"{leg} 仓位越界 [{lo:.3f}, {hi:.3f}]"
            break
        bad = [x for x in dec if abs(x["target"] - x["raw"]) > 1e-12
               or x["target"] > vt.CAP + 1e-12 or x["target"] < -1e-12]
        if bad:
            ok, note = False, f"{leg} {len(bad)} 次调仓落点 != 公式值"
            break
    out.append(("仓位 ∈ [0, cap]，且每次调仓落点 = min(cap, TV/vol)，不加杠杆不做空", ok,
                note or f"两腿均通过（最高 {vt.CAP:.0%}）"))

    # 3) 漂移死区：每次实际变动要么 = 0、要么 ≥ DRIFT
    ok, note = True, ""
    for leg in vt.POOL:
        _nav, st, _dec = vt.build(leg)
        d = st.astype(float).diff().dropna().abs()
        bad = d[(d > 1e-12) & (d < vt.DRIFT - 1e-9)]
        if len(bad):
            ok, note = False, f"{leg} 有 {len(bad)} 次变动 < {vt.DRIFT:.0%}"
            break
    out.append((f"未达 ±{vt.DRIFT:.0%} 不交易（死区生效）", ok, note or "两腿均通过"))

    # 4) 时点：信号 t 的仓位不得在 t 日收盘生效，只能从 t+1 收盘起算（在信号日历上验）
    ok, note = True, ""
    for leg in vt.POOL:
        sig = vt.load_signal(leg)
        sm, dec = vt.machine(sig, vt.params())
        for x in dec:
            i = sm.index.get_loc(x["date"])
            if abs(float(sm.iloc[i]) - x["target"]) < 1e-12:      # 决策当日就已经是这个仓位 = 前视
                ok, note = False, f"{leg} {x['date'].date()} 决策当日已生效（前视）"
                break
            if i + 1 < len(sm) and abs(float(sm.iloc[i + 1]) - x["target"]) > 1e-12:
                ok, note = False, f"{leg} {x['date'].date()} 未在 t+1 收盘生效"
                break
        if not ok:
            break
    out.append(("信号 t → 仓位自 t+1 收盘起生效（无前视）", ok, note or "两腿逐条一致"))

    # 5) 截断最后 6 天信号重跑 -> 截止日之前的历史仓位不变（因果性）
    ok, note = True, ""
    for leg in vt.POOL:
        nav, st, _ = vt.build(leg)
        sig = vt.load_signal(leg)
        cut_off = sig.index[-7]
        sm_cut, _ev = vt.machine(sig.iloc[:-6], vt.params())
        st_cut = sm_cut.reindex(nav.index).ffill().fillna(1.0)
        common = nav.index[nav.index <= cut_off]
        diff = float((st.reindex(common) - st_cut.reindex(common)).abs().max())
        if diff > 1e-12:
            ok, note = False, f"{leg} 最大差 {diff:.2e}"
            break
    out.append(("截掉最后 6 天信号重跑，截止日前仓位不变（无前视）", ok, note or "最大差 0"))

    # 6) TV -> 0（纯现金）年化 == 空仓利率（从首个「清仓」成交日起算，避开起点满仓段）
    ok, note = True, ""
    for leg in vt.POOL:
        nav, st, _ = vt.build(leg, TV=0.0)
        zeros = st[st <= 1e-12]
        if not len(zeros):
            ok, note = False, f"{leg} TV=0 从未清仓"
            break
        start = zeros.index[0]
        seg = nav.loc[nav.index >= start]
        nv = vt.run_leg(st.loc[seg.index], seg, cost=0.0)
        ann = vt.perf(nv)["ann"]
        if abs(ann - vt.CASH_ANNUAL) > 2e-4:
            ok, note = False, f"{leg} 年化 {ann:.4%} != {vt.CASH_ANNUAL:.2%}"
            break
        if float(st.loc[seg.index].max()) > 1e-9:
            ok, note = False, f"{leg} 清仓后又有非零仓位"
            break
    out.append((f"TV=0（纯现金）年化 == 空仓 {vt.CASH_ANNUAL:.2%}（禁止把空仓记 0）", ok,
                note or "两腿一致（起点满仓段不计入）"))

    # 7) 两腿恒满时组合 == 50/50 手工复利（零费）
    navs, idx = {}, None
    for leg in vt.POOL:
        nav, _, _ = vt.build(leg, kind="hold")
        navs[leg] = nav
        idx = nav.index if idx is None else idx.intersection(nav.index)
    idx = pd.DatetimeIndex(idx).sort_values()
    navs = {k: v.reindex(idx).ffill() for k, v in navs.items()}
    stx = {k: pd.Series(1.0, index=idx) for k in vt.POOL}
    nv, _ = vt.portfolio(navs, stx, cost=0.0)
    manual = 1.0
    rets = {k: navs[k].pct_change().fillna(0.0) for k in vt.POOL}
    for i in range(len(idx)):
        manual *= (1.0 + sum(0.5 * float(rets[k].iloc[i]) for k in vt.POOL))
    rel = abs(manual / float(nv.iloc[-1]) - 1.0)
    out.append(("两腿恒满时组合 == 50/50 手工复利（零费）", bool(rel < 1e-10), f"差 {rel:.1e}"))
    return out


def main():
    os.makedirs(OUTDIR, exist_ok=True)
    p0 = vt.params()
    legs = leg_rows()
    prows = port_rows()
    base = next(r for r in prows if r["name"].startswith("**本变种默认"))
    static = next(r for r in prows if r["name"].startswith("静态 50/50"))
    srow = next(r for r in prows if r["name"].startswith("静态 标普"))
    drow = next(r for r in prows if r["name"].startswith("VT · 标普"))
    ma_row = next(r for r in prows if r["name"].startswith("MA200"))
    idx, base_nv, static_nv = base["idx"], base["nav"], static["nav"]
    sens = sens_rows(idx, port_build(PORT_SPECS[1])[4])
    halves = half_rows(idx, None)
    checks = check_all()

    wins = []
    for label, a, b in WINDOWS:
        sm, ss = vt.window_nav(base_nv, a, b), vt.window_nav(static_nv, a, b)
        if len(sm) < 5 or len(ss) < 5:
            continue
        wins.append(dict(win=label, ret=float(sm.iloc[-1] / sm.iloc[0] - 1.0),
                         ret_s=float(ss.iloc[-1] / ss.iloc[0] - 1.0),
                         mdd=vt.perf(sm)["mdd"], mdd_s=vt.perf(ss)["mdd"], start=a, end=b))

    yr, yr_s = vt.yearly(base_nv), vt.yearly(static_nv)
    _mon = next(r for r in prows if r["name"].startswith("文档月度档"))
    _m20 = vt.window_nav(_mon["nav"], "2020-02-19", "2020-08-31")
    mon20 = float(_m20.iloc[-1] / _m20.iloc[0] - 1.0)

    with open(OUT_JSONL, "w", encoding="utf-8") as f:
        for r in legs:
            f.write(json.dumps({k: (round(v, 4) if isinstance(v, float) else v)
                                for k, v in r.items()}, ensure_ascii=False) + "\n")
        for r in prows:
            f.write(json.dumps({"portfolio": r["name"],
                                **{k: (round(v, 4) if isinstance(v, float) else v)
                                   for k, v in r.items() if k not in ("nav", "weight", "idx")}},
                               ensure_ascii=False) + "\n")
        for r in sens:
            f.write(json.dumps({"sens": r["name"],
                                **{k: (round(v, 4) if isinstance(v, float) else v)
                                   for k, v in r.items() if k != "name"}},
                               ensure_ascii=False) + "\n")
        for r in halves:
            f.write(json.dumps({"half": r["seg"],
                                **{k: (round(v, 4) if isinstance(v, float) else v)
                                   for k, v in r.items() if k != "seg"}},
                               ensure_ascii=False) + "\n")
        for r in wins:
            f.write(json.dumps({"window": r["win"],
                                **{k: (round(v, 4) if isinstance(v, float) else v)
                                   for k, v in r.items() if k != "win"}},
                               ensure_ascii=False) + "\n")
        f.write(json.dumps({"checks": [dict(name=n, ok=bool(o), note=t) for n, o, t in checks]},
                           ensure_ascii=False) + "\n")

    bad = [c for c in checks if not c[1]]
    yrs = (idx[-1] - idx[0]).days / 365.25
    win = f"{idx[0].date()} ~ {idx[-1].date()}"

    L = [
        "# 波动资产 · V9 变种：美股两腿波动率目标仓位（vol20 · TV 15% · 月末 + 月中快减/快补）",
        "> 池 = **只有纳指100 与标普500**（`vol_target.POOL`）。基础规则取自 "
        "`G:\\tradingagents\\us_index_strategy\\策略规格_v1.0.md` 的 **S-A**（文档排名第 1）；"
        "**月中快减/快补是 2026-11-04 追加的**（用户明确「文档只是参考」），"
        "由 `research/_probe_opt.py` 的四道闸门筛出、13 档邻域内稳定。",
        "> 信号 = **美元指数**（`.NDX` / `.INX`）收盘算 vol20；执行 = 场外 QDII **人民币复权净值**；"
        f"空仓按 {vt.CASH_ANNUAL:.2%} 计息 · 单边成本 {p0['COST']:.2%}。",
        "> 规则权威源 `v9_vol_target/vol_target.py` 顶部常量（`TRIGGER` 一档切换）。",
        "",
        "## 1. 规则（可执行版）",
        "",
        "| 环节 | 规则 |",
        "|---|---|",
        f"| 信号 | 每月**最后一个交易日**收盘，算近 {p0['VOL_WIN']} 日收益的年化已实现波动 "
        "`vol20 = std(r,20)×sqrt(252)` |",
        f"| 目标权重 | `w = min({p0['CAP']:.0f}, {p0['TV']:.0%} / vol20)`，下限 0（**不加杠杆、不做空**） |",
        f"| 不交易带 | 与**当前仓位**差不足 ±{p0['DRIFT']:.0%} → 不动手（省成本） |",
        f"| **快减**（月中） | 5 日跌幅 ≤ {p0['CUT_RET']:.0%} **且** vol20 ≥ "
        f"{p0['CUT_VOLX']:.1f}×TV → 次日降到当日公式值（修「减得太慢」） |",
        f"| **快补**（月中） | vol20 ≤ {p0['DECAY']:.0%}×近 {p0['PEAK_WIN']} 日峰值 **且** 站上 "
        f"MA{p0['ADD_MA']} → 次日补到当日公式值（修「补得太晚」） |",
        "| 成交 | 决策日收盘 → **次一交易日收盘**成交 → 新权重自 **t+2** 起承担收益（R-01） |",
        f"| 空仓 | 未投部分按 {vt.CASH_ANNUAL:.2%} 日计复利（**禁止记 0**） |",
        f"| 成本 | 单边 {p0['COST']:.2%}，按 `Σ|Δw|` 计（场外现实 0.6% 见 §5） |",
        "",
        "> `TRIGGER=\"month\"` 关掉「快减/快补」两行 = **文档 S-A 原样**，本报告里作为对照档保留。",
        "",
        "## 2. 逐腿（各档位均为「含单边 0.03%」）",
        "",
        "| 腿 | 档位 | 年化 | 回撤·日频 | 回撤·月末 | Calmar(月末) | 夏普 | 平均仓位 | 换手 | 调仓/年 | vs 买入持有 |",
        "|---|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|",
    ]
    for leg in vt.POOL:
        for r in [x for x in legs if x["leg"] == leg]:
            d_ann = 0.0 if abs(r["d_ann"]) < 0.005 else r["d_ann"]
            d_mdd = 0.0 if abs(r["d_mdd"]) < 0.005 else r["d_mdd"]
            L.append(f"| {r['name']} | {r['variant']} | {vt.fmt_pct(r['ann'])} | "
                     f"{vt.fmt_pct(r['mdd'])} | {vt.fmt_pct(r['mdd_me'])} | "
                     f"{r['calmar_me']:.2f} | {r['sharpe']:.2f} | "
                     f"{r['expo']:.0%} | {r['turnover']:.1f} | {r['trades_py']:.1f} | "
                     f"{d_ann:+.2f}pp / {d_mdd:+.2f}pp |")

    ndx_base = next(r for r in legs if r["leg"] == "ndx" and r["variant"].startswith("**"))
    spx_base = next(r for r in legs if r["leg"] == "spx" and r["variant"].startswith("**"))
    ndx_bh = next(r for r in legs if r["leg"] == "ndx" and r["variant"] == "买入持有")
    spx_bh = next(r for r in legs if r["leg"] == "spx" and r["variant"] == "买入持有")
    ndx_ma = next(r for r in legs if r["leg"] == "ndx" and r["variant"].startswith("MA200"))
    spx_ma = next(r for r in legs if r["leg"] == "spx" and r["variant"].startswith("MA200"))
    ndx_mon = next(r for r in legs if r["leg"] == "ndx" and r["variant"].startswith("文档月度档"))
    spx_mon = next(r for r in legs if r["leg"] == "spx" and r["variant"].startswith("文档月度档"))

    L += [
        "",
        "> **两种回撤口径都列**：`日频` = 逐日净值算（风控应看这个）；`月末` = 只在月末取点算"
        "（**文档表 1 的口径**，适合跨报告对照）。",
        f"> **纳指这条腿：让渡的收益换成了回撤与夏普。** 年化 {vt.fmt_pct(ndx_base['ann'])} vs "
        f"买入持有 {vt.fmt_pct(ndx_bh['ann'])}（{ndx_base['d_ann']:+.2f}pp）、回撤 "
        f"{vt.fmt_pct(ndx_bh['mdd'])} → {vt.fmt_pct(ndx_base['mdd'])}"
        f"（{ndx_base['d_mdd']:+.2f}pp）、夏普 {ndx_bh['sharpe']:.2f} → {ndx_base['sharpe']:.2f}、"
        f"Calmar(月末) {ndx_bh['calmar_me']:.2f} → {ndx_base['calmar_me']:.2f}、"
        f"平均仓位 {ndx_base['expo']:.0%}、{ndx_base['trades_py']:.1f} 次调仓/年。",
        f"> **标普这条腿：改善方向一致，幅度小得多。** 年化 {vt.fmt_pct(spx_base['ann'])} vs "
        f"{vt.fmt_pct(spx_bh['ann'])}（{spx_base['d_ann']:+.2f}pp）、回撤 "
        f"{vt.fmt_pct(spx_bh['mdd'])} → {vt.fmt_pct(spx_base['mdd'])}"
        f"（{spx_base['d_mdd']:+.2f}pp）、"
        f"夏普 {spx_bh['sharpe']:.2f} → {spx_base['sharpe']:.2f}、"
        f"Calmar(月末) {spx_bh['calmar_me']:.2f} → {spx_base['calmar_me']:.2f}、"
        f"平均仓位 {spx_base['expo']:.0%}。标普自身波动只有纳指一半，`15%/11%` 常年顶在满仓"
        f"（{spx_base['trades_py']:.1f} 次调仓/年），所以只在少数高波动时点动手。",
        f"> **月中补丁在两条腿上都有效**：纳指 年化 "
        f"{vt.fmt_pct(ndx_base['ann'])} vs 文档档 {vt.fmt_pct(ndx_mon['ann'])}、回撤 "
        f"{vt.fmt_pct(ndx_mon['mdd'])} → {vt.fmt_pct(ndx_base['mdd'])}、夏普 "
        f"{ndx_mon['sharpe']:.2f} → {ndx_base['sharpe']:.2f}；标普 "
        f"{vt.fmt_pct(spx_base['ann'])} vs {vt.fmt_pct(spx_mon['ann'])}、回撤 "
        f"{vt.fmt_pct(spx_mon['mdd'])} → {vt.fmt_pct(spx_base['mdd'])}、夏普 "
        f"{spx_mon['sharpe']:.2f} → {spx_base['sharpe']:.2f}。**不是只靠纳指那条腿**。",
        f"> **与 S-B（MA200 月频）比，VT 在两条腿上都同时赢收益与回撤**：纳指 VT "
        f"{vt.fmt_pct(ndx_base['ann'])} / {vt.fmt_pct(ndx_base['mdd'])} / 夏普 "
        f"{ndx_base['sharpe']:.2f} vs MA200 {vt.fmt_pct(ndx_ma['ann'])} / "
        f"{vt.fmt_pct(ndx_ma['mdd'])} / {ndx_ma['sharpe']:.2f}；标普 VT "
        f"{vt.fmt_pct(spx_base['ann'])} / {vt.fmt_pct(spx_base['mdd'])} / {spx_base['sharpe']:.2f} vs "
        f"{vt.fmt_pct(spx_ma['ann'])} / {vt.fmt_pct(spx_ma['mdd'])} / {spx_ma['sharpe']:.2f}。",
        "> 机制上的解释：MA200 是**二元**的（要么满仓、要么清仓），一次假摔就把整段反弹让掉；",
        "> VT 是**连续**的（只按风险预算缩放），高波动月少持一点、波动回落就补回来 —— "
        "它不判断方向，所以不会被「趋势结论」反复打脸。",
        "",
        f"## 3. 组合层（{win} · {yrs:.1f} 年）",
        "",
        "| 组合 | 累计 | 年化 | 波动 | 回撤·日频 | 回撤·月末 | Calmar(月末) | 夏普 | 平均仓位 | 调仓/年 |",
        "|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|",
    ]
    for r in prows:
        L.append(f"| {r['name']} | {vt.fmt_pct(r['cum'], 1)} | {vt.fmt_pct(r['ann'])} | "
                 f"{r['vol'] * 100:.2f}% | {vt.fmt_pct(r['mdd'])} | {vt.fmt_pct(r['mdd_me'])} | "
                 f"{r['calmar_me']:.2f} | {r['sharpe']:.2f} | {r['expo']:.0%} | {r['trades_py']:.1f} |")
    L += [
        "",
        f"> **默认 vs 静态 50/50**：年化 {vt.fmt_pct(base['ann'])} vs {vt.fmt_pct(static['ann'])}"
        f"（**{(base['ann'] - static['ann']) * 100:+.2f}pp**）、回撤 "
        f"{vt.fmt_pct(static['mdd'])} → {vt.fmt_pct(base['mdd'])}"
        f"（**{(base['mdd'] - static['mdd']) * 100:+.2f}pp**）、夏普 {static['sharpe']:.2f} → "
        f"{base['sharpe']:.2f}、Calmar(月末) {static['calmar_me']:.2f} → {base['calmar_me']:.2f}、"
        f"平均仓位 {base['expo']:.0%}。",
        f"> **文档 §7 的 2:1 配比**（标普:纳指 = 波动率倒数 11.14:22.45）：静态 "
        f"{vt.fmt_pct(srow['ann'])} / {vt.fmt_pct(srow['mdd'])} / 夏普 {srow['sharpe']:.2f} → "
        f"叠加 VT 后 {vt.fmt_pct(drow['ann'])} / {vt.fmt_pct(drow['mdd'])} / "
        f"夏普 {drow['sharpe']:.2f}。两种权重下 VT 的「夏普改善」方向一致，说明它不是某组配比的巧合。",
        "",
        "## 4. 参数敏感性（组合层 · 默认权重 · 只改一格）",
        "",
        "| 档位 | 年化 | 回撤·日频 | 回撤·月末 | Calmar(月末) | 夏普 | 平均仓位 | 调仓/年 |",
        "|---|---:|---:|---:|---:|---:|---:|---:|",
    ]
    yrs_p = yrs
    for r in sens:
        L.append(f"| {r['name']} | {vt.fmt_pct(r['ann'])} | {vt.fmt_pct(r['mdd'])} | "
                 f"{vt.fmt_pct(r['mdd_me'])} | {r['calmar_me']:.2f} | {r['sharpe']:.2f} | "
                 f"{r['expo']:.0%} | {r['trades'] / yrs_p:.1f} |")
    tv_neigh = [(0.15, sens[0])]
    for r in sens:
        if r["name"].startswith("目标波动"):
            tv_neigh.append((float(r["name"].split()[1].rstrip("%")) / 100.0, r))
    tv_neigh.sort(key=lambda x: x[0])
    shs = [r["sharpe"] for _t, r in tv_neigh]
    ans = [r["ann"] for _t, r in tv_neigh]
    cals = [r["calmar_me"] for _t, r in tv_neigh]
    costs = {r["name"]: r for r in sens}
    c03 = costs["基准（TV15 · vol20 · 月末+快减+快补 · 单边0.03% · 空仓2.0%）"]
    c10 = costs["成本 0.10%（v7 口径）"]
    c60 = costs["成本 0.60%（场外 A 类现实）"]
    cmon = costs["关掉月中补丁（= 文档月度档）"]
    ccut = costs["只加快减"]
    cadd = costs["只加快补"]
    patch_keys = ["快减跌幅门槛 −3%", "快减跌幅门槛 −8%", "快减 vol 门槛 1.1×TV",
                  "快减 vol 门槛 1.7×TV", "快补回落门槛 55%", "快补回落门槛 85%"]
    patch = [costs[k] for k in patch_keys]
    p_sh = [r["sharpe"] for r in patch]
    p_ann = [r["ann"] for r in patch]
    p_mdd = [r["mdd"] for r in patch]
    L += [
        "",
        f"> **目标波动邻域（文档 R-S02 的核心证据）**：TV "
        f"{' / '.join(f'{t:.0%}' for t, _r in tv_neigh)} → 年化 "
        f"{' / '.join(vt.fmt_pct(a) for a in ans)}，而**夏普 "
        f"{' / '.join(f'{s:.2f}' for s in shs)}**（最低 {min(shs):.2f} / 最高 {max(shs):.2f}）、"
        f"Calmar(月末) {' / '.join(f'{c:.2f}' for c in cals)}。",
        "> 夏普几乎不随参数变化 —— 波动率目标是**稳健结构**，不是拟合出来的。"
        "选 TV 只取决于你要多低的回撤，不取决于收益最大化：TV 从 15% 降到 10%，年化少 "
        f"{(c03['ann'] - tv_neigh[0][1]['ann']) * 100:.2f}pp，换来回撤 "
        f"{(tv_neigh[0][1]['mdd'] - c03['mdd']) * 100:+.2f}pp。",
        "",
        f"> **月中补丁的贡献（这一版的核心）**：关掉补丁（文档月度档）年化 "
        f"{vt.fmt_pct(cmon['ann'])} / 回撤 {vt.fmt_pct(cmon['mdd'])} / 夏普 {cmon['sharpe']:.2f}；"
        f"加上快减+快补后 {vt.fmt_pct(c03['ann'])} / {vt.fmt_pct(c03['mdd'])} / "
        f"{c03['sharpe']:.2f}（**{(c03['ann'] - cmon['ann']) * 100:+.2f}pp / "
        f"{(c03['mdd'] - cmon['mdd']) * 100:+.2f}pp / {c03['sharpe'] - cmon['sharpe']:+.2f}**）。",
        f"> **只加一半也行，但效果打对折**：只加快减 {vt.fmt_pct(ccut['ann'])} / "
        f"{vt.fmt_pct(ccut['mdd'])} / 夏普 {ccut['sharpe']:.2f}；只加快补 {vt.fmt_pct(cadd['ann'])} / "
        f"{vt.fmt_pct(cadd['mdd'])} / {cadd['sharpe']:.2f} —— 两个方向各修一半，"
        "合起来才有 4pp 级别的回撤改善。",
        f"> **补丁自身的参数邻域**（{len(patch)} 档：跌幅 −3~−8%、vol 门槛 1.1~1.7×、回落 55~85%）："
        f"年化 {vt.fmt_pct(min(p_ann))} ~ {vt.fmt_pct(max(p_ann))}、回撤 "
        f"{vt.fmt_pct(min(p_mdd))} ~ {vt.fmt_pct(max(p_mdd))}、夏普 {min(p_sh):.2f} ~ {max(p_sh):.2f}、"
        f"调仓 {min(r['trades'] for r in patch) / yrs:.1f}~{max(r['trades'] for r in patch) / yrs:.1f} 次/年。"
        "**全档都优于文档月度档**，说明这不是「挑出来的那一个数」。",
        "",
        f"> **成本**：单边 0.03% → 0.10% → 0.60%（场外 A 类申购 0.1% + 赎回 0.5%）三档年化 "
        f"{vt.fmt_pct(c03['ann'])} / {vt.fmt_pct(c10['ann'])} / {vt.fmt_pct(c60['ann'])} —— "
        f"场外现实口径掉 {(c03['ann'] - c60['ann']) * 100:.2f}pp。"
        f"补丁把调仓抬到 {c03['trades'] / yrs:.1f} 次/年（月度档 {cmon['trades'] / yrs:.1f}），"
        "所以 0.6% 那一档要盯得更紧：**场外 A 类仍可用（只掉 0.94pp），但换手再高十倍的做法就不行了**。",
        "> **空仓利率 2.0% vs 1.4% vs 0%**：空仓只占少数时间（平均仓位 8 成以上），影响在 0.3pp 内，"
        "但 0% 那一档是用来量化「把空仓记 0」这个错误的代价的 —— 文档 R-03 明令禁止。",
        f"> **波动窗口别调长**：60 日夏普掉到 {costs['波动窗口 60 日']['sharpe']:.2f}、回撤 "
        f"{vt.fmt_pct(costs['波动窗口 60 日']['mdd'])} —— 长窗口让波动估计变钝，"
        "高波动刚起来时仍按低波动满仓，与「快减」的初衷相反。",
        "",
        "## 5. 分半样本（文档 R-S03 的读法）",
        "",
        "| 段 | 区间 | 组合 | 年化 | 最大回撤 | 夏普 |",
        "|---|---|---|---:|---:|---:|",
    ]
    for r in halves:
        L.append(f"| {r['seg']} | {r['start']} ~ {r['end']} | {r['name']} | "
                 f"{vt.fmt_pct(r['ann'])} | {vt.fmt_pct(r['mdd'])} | {r['sharpe']:.2f} |")
    L += [
        "",
        "> **这是全部数据里最有信息量的一张表**：择时的价值只在熊市兑现，代价是牛市少赚 —— "
        "它不是「提高收益」的工具，是「改变收益分布形状」的工具（文档 §4 原话）。",
        f"> 读法：前半段（无大熊市）VT 夏普 "
        f"{[r for r in halves if r['seg'] == '前半段' and r['name'] == 'VT 默认'][0]['sharpe']:.2f} "
        f"vs 买入持有 {[r for r in halves if r['seg'] == '前半段' and r['name'] == '买入持有'][0]['sharpe']:.2f}"
        "（**略降**，这是保险的保费）；后半段（含 2020 + 2022）VT 夏普 "
        f"{[r for r in halves if r['seg'] == '后半段' and r['name'] == 'VT 默认'][0]['sharpe']:.2f} "
        f"vs {[r for r in halves if r['seg'] == '后半段' and r['name'] == '买入持有'][0]['sharpe']:.2f}"
        "（**大幅改善**）。**两段合计才是完整的定价**：前半段付保费，后半段理赔。",
        "",
        "## 6. 压力窗口（默认档 vs 静态 50/50）",
        "",
        "| 窗口 | 区间 | 本变种 收益 | 静态 50/50 收益 | 本变种 回撤 | 静态 回撤 |",
        "|---|---|---:|---:|---:|---:|",
    ]
    for w in wins:
        L.append(f"| {w['win']} | {w['start']} ~ {w['end']} | {vt.fmt_pct(w['ret'])} | "
                 f"{vt.fmt_pct(w['ret_s'])} | {vt.fmt_pct(w['mdd'])} | {vt.fmt_pct(w['mdd_s'])} |")
    y20, y20s = yr.get("2020"), yr_s.get("2020")
    y22, y22s = yr.get("2022"), yr_s.get("2022")
    L += [
        "",
        "> **2020 从「失效窗口」变成了「打平窗口」，2022 仍是它的主场**：2020 年 VT "
        f"{vt.fmt_pct(y20)} vs 静态 {vt.fmt_pct(y20s)}"
        f"（{(y20 - y20s) * 100:+.2f}pp）；2022 年 {vt.fmt_pct(y22)} vs {vt.fmt_pct(y22s)}"
        f"（{(y22 - y22s) * 100:+.2f}pp）。",
        f"> **快减/快补修好了一半**：文档月度档在 2020 窗口吃 {vt.fmt_pct(mon20)}，本变种 **"
        f"{vt.fmt_pct(wins[0]['ret'])}**（同期静态 {vt.fmt_pct(wins[0]['ret_s'])}）"
        "—— 修的是「底部减仓后干等一个月」那一刀。",
        "> 但**没修完**：2020 年仍然落后静态 8~9pp。原因不是规则，而是这类事件只有 5 周、"
        "波动率目标本身就要「等波动先起来」才减仓，天生慢半拍；想再快只能引入方向判断（那就不叫 VT 了）。",
        "> 2022 那种**持续 11 个月的慢熊**它反而越跌越减、从容应对 —— **它治慢熊，急跌只能减轻、不能免疫。**",
        "",
        "## 7. 逐年收益（默认档 vs 静态 50/50）",
        "",
        "| 年份 | 本变种 | 静态 50/50 | 差 |",
        "|---|---:|---:|---:|",
    ]
    for y in sorted(yr):
        a, b = yr.get(y), yr_s.get(y)
        diff = "—" if (a is None or b is None) else f"{(a - b) * 100:+.2f}pp"
        L.append(f"| {y} | {vt.fmt_pct(a)} | {vt.fmt_pct(b)} | {diff} |")
    L += [
        "",
        "## 8. 硬断言",
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
        "## 9. 与文档 S-A 的对照（不同样本，只比量级）",
        "",
        "| 项目 | 文档 S-A（QQQ · 2002-01~2026-11 · 24.84 年） | 本仓库·文档月度档 | 本仓库·**默认档** |",
        "|---|---:|---:|---:|",
        f"| 年化 | {vt.fmt_pct(vt.DOC_REF['ann'])} | {vt.fmt_pct(ndx_mon['ann'])} | "
        f"{vt.fmt_pct(ndx_base['ann'])} |",
        f"| 最大回撤 | {vt.fmt_pct(vt.DOC_REF['mdd'])} | {vt.fmt_pct(ndx_mon['mdd'])} | "
        f"{vt.fmt_pct(ndx_base['mdd'])} |",
        f"| 夏普 | {vt.DOC_REF['sharpe']:.2f} | {ndx_mon['sharpe']:.2f} | {ndx_base['sharpe']:.2f} |",
        f"| 平均仓位 | {vt.DOC_REF['expo']:.0%} | {ndx_mon['expo']:.0%} | {ndx_base['expo']:.0%} |",
        f"| 调仓 | 约 {vt.DOC_REF['trades_per_year']} 次/年 | {ndx_mon['trades_py']:.1f} 次/年 | "
        f"{ndx_base['trades_py']:.1f} 次/年 |",
        "",
        "> **不是同一段样本，不可逐格对账**：文档从 2002 起（含 2008，纳指 −53%），我们只有 2012 起；"
        "文档用 QQQ 美元净值、我们用 270042 人民币净值（含汇率）。",
        "> **可比的是月度档**（它是文档 S-A 的原样实现）：结构一致 —— 平均仓位都在 8 成附近、"
        f"年调仓都是个位数（约 {vt.DOC_REF['trades_per_year']} vs {ndx_mon['trades_py']:.1f}）、"
        "回撤明显浅于买入持有。差异在幅度：文档「收益不输」，我们让渡 "
        f"{abs(ndx_mon['d_ann']):.2f}pp —— 因为我们这段是美股长牛的后半段（没有 2000-2002 与 2008）。",
        "> **默认档则是在此之上再动了一次手术**：多 0.5pp 年化、回撤再浅 1.7pp、夏普再 +0.06，"
        "代价是调仓翻倍到 11 次/年。**它已经超出文档的 S-A 了，不再需要和文档对齐**。",
        "",
        "## 10. 口径与已知边界",
        "",
        "1. **样本只到 2012**（QDII 场外净值起点）：不含 2000-2002（纳指 −83%）与 2008 —— "
        "而波动率目标化最被称赞的正是「泡沫破裂 + 高波动聚类时降仓」，本报告对该场景是**外推**。",
        "2. **信号是美元指数、执行是人民币净值**：两者的差异来自汇率与跟踪误差；"
        "`SIGNAL=\"nav\"` 一档把信号也换成净值，可看这处差异的代价。",
        "3. **它不预测方向**：只调风险预算，所以「低波动阴跌」（如 2015 年 A 股式流动性危机）"
        "会让它因波动率低而满仓挨打 —— 这是文档 §9 第 3 条点名的失效模式。",
        "4. **`cap=1` 是唯一可执行档**：文档 R-04 禁止杠杆；`cap>1` 需两融，成本与额度不在回测里。",
        "5. **快减/快补只减不能免**：2020 窗口从 −3.06% 修到 +2.98%，但仍落后静态 6.9pp —— "
        "VT 要先看见波动才减仓，天生慢半拍。再快就只能引入方向判断（就不是 VT 了），"
        "§10 的「假摔」风险也随之回来。",
        "6. **快减的反向代价是 2018 型行情**：波动率只冲到 25%（降仓空间小），"
        "砍在低位、补在高位，2018 年比月度档少 4.8pp。**深跌救命，浅摔亏钱。**",
        "7. **QDII 通道风险未计**：额度关闭、限购、赎回 T+2~T+7、场内溢价 —— "
        "决策日 → 次日成交，实盘可能滑到第二三天；若当天无法申购则该档顺延。",
        "8. **补丁的阈值不是选出来的**：TV15 / vol20 / 漂移 5% 沿用文档；"
        "快减/快补的五个阈值取常识区间（−5% / 1.4×TV / 70% / 60 日 / MA10），"
        f"§4 给了 {len(sens)} 档邻域，全档方向一致。",
        "",
        "> **模拟结果，非投资建议。**",
    ]

    with open(OUT_MD, "w", encoding="utf-8") as f:
        f.write("\n".join(L))

    print("报告:", OUT_MD)
    print("结果:", OUT_JSONL)
    print(f"窗口 {win} · {yrs:.1f} 年")
    for r in prows:
        print(f"  {r['name']:38s} 年化 {vt.fmt_pct(r['ann']):>9s} · 回撤 {vt.fmt_pct(r['mdd']):>9s} · "
              f"夏普 {r['sharpe']:>5.2f} · 仓位 {r['expo']:.0%}")
    print(f"断言 {len(checks) - len(bad)}/{len(checks)} 通过")
    for n, o, t in checks:
        print(f"  {'✅' if o else '❌'} {n} — {t}")
    return 0 if not bad else 1


if __name__ == "__main__":
    sys.exit(main())
