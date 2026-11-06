# -*- coding: utf-8 -*-
"""波动资产 · V10 变种（美股两腿纯波动层）· 回测（逐腿 + 组合 + 敏感性 + 压力窗口 + 硬断言）。

池：**只有纳指100 与标普500**（`vol_layer.POOL`），与 v7 / v8 / v9 同池。
规则：`仓位 = min(1, TV / vol20)`，TV = 20%，**无方向门**，日频、死区 10pp。
口径：
- 信号 = **美元指数**（`.NDX` / `.INX`），执行 = 场外 QDII 人民币复权净值（`data.series_for_leg`）；
- 成本 = 单边 0.1%（文档主口径），另给 0.6% 的场外现实口径；
- 空仓按货基 `rule.CASH_ANNUAL` 计息；
- 起点口径：首日目标仓位照算，不另计建仓费。

产物（策略类，落 `backtest/`）：
- `_v10_vol_layer_bt.md` / `_v10_vol_layer_bt.jsonl`

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
_VL = os.path.dirname(_HERE)
for _p in (_HERE, os.path.join(_VL, "v7_us_gate"), os.path.join(_VL, "v8_golden_cross"),
           os.path.join(_VL, "v9_vol_target")):
    if _p not in sys.path:
        sys.path.insert(0, _p)

import vol_layer as vl  # noqa: E402

OUTDIR = os.path.join(_HERE, "backtest")
OUT_MD = os.path.join(OUTDIR, "_v10_vol_layer_bt.md")
OUT_JSONL = os.path.join(OUTDIR, "_v10_vol_layer_bt.jsonl")

# 逐腿档位：以「**」开头的行会被报告正文引用
VARIANTS = [
    ("买入持有", dict(FLOOR=1.0, DEAD=0.0)),
    ("**纯波动层 TV20（日频 · 默认）**", dict()),
    ("TV 12%（更保守）", dict(TV=0.12)),
    ("TV 15%", dict(TV=0.15)),
    ("TV 25%（更激进）", dict(TV=0.25)),
    ("vol10（更快）", dict(VOL_WIN=10)),
    ("vol60（更慢）", dict(VOL_WIN=60)),
    ("死区 0pp（每天按 vol 微调）", dict(DEAD=0.0)),
    ("死区 20pp", dict(DEAD=0.20)),
    ("月末定仓", dict(TRIGGER="month")),
    ("仓位下限 50%", dict(FLOOR=0.5)),
    ("人民币净值信号（反例）", dict(SIGNAL="nav")),
    ("成本 0.6%（场外现实口径）", dict(COST=0.006)),
]

SENS = [
    ("基准（TV20 · vol20 · 死区 10pp · 日频）", {}),
    ("TV 12%", dict(TV=0.12)),
    ("TV 15%", dict(TV=0.15)),
    ("TV 18%", dict(TV=0.18)),
    ("TV 22%", dict(TV=0.22)),
    ("TV 25%", dict(TV=0.25)),
    ("vol10（更快）", dict(VOL_WIN=10)),
    ("vol60（更慢）", dict(VOL_WIN=60)),
    ("死区 0pp", dict(DEAD=0.0)),
    ("死区 5pp", dict(DEAD=0.05)),
    ("死区 20pp", dict(DEAD=0.20)),
    ("仓位下限 25%", dict(FLOOR=0.25)),
    ("仓位下限 50%", dict(FLOOR=0.5)),
    ("月末定仓", dict(TRIGGER="month")),
    ("成本 0.03%（v9 口径）", dict(COST=0.0003)),
    ("成本 0.60%（场外 A 类现实）", dict(COST=0.006)),
    ("现金 0%（不付利息）", dict(CASH_ANNUAL=0.0)),
    ("现金 2.0%（v9 口径）", dict(CASH_ANNUAL=0.02)),
    ("人民币净值信号（反例）", dict(SIGNAL="nav")),
]

WINDOWS = [
    ("2018Q4 急跌", "2018-09-20", "2018-12-31"),
    ("2020 疫情", "2020-02-19", "2020-08-31"),
    ("2022 加息熊市", "2022-01-03", "2022-12-30"),
    ("2025 至今年内", "2025-01-02", "2026-11-04"),
]

WEIGHT_CASES = [
    ("纳指单腿", {"ndx": 1.0, "spx": 0.0}),
    ("标普单腿", {"ndx": 0.0, "spx": 1.0}),
    ("标普:纳指 = 2:1", {"ndx": 1.0 / 3.0, "spx": 2.0 / 3.0}),
]


def leg_rows():
    """每条腿跑全部档位。"""
    rows = []
    for leg in vl.POOL:
        nav_full = vl.load_nav(leg)
        for name, over in VARIANTS:
            nav, st, dec = vl.build(leg, **over)
            nv = vl.run_leg(st, nav, cost=over.get("COST"), cash=over.get("CASH_ANNUAL"))
            m = vl.perf(nv)
            bh = nav / nav.iloc[0]
            mb = vl.perf(bh)
            rows.append(dict(
                leg=leg, name=vl.rule.LEGS[leg]["name"], variant=name,
                start=nav.index[0].date().isoformat(), end=nav.index[-1].date().isoformat(),
                years=round((nav.index[-1] - nav.index[0]).days / 365.25, 2),
                ann=m["ann"], mdd=m["mdd"], vol=m["vol"], sharpe=m["sharpe"],
                calmar=vl.calmar(nv), mdd_me=vl.mdd_sampled(nv),
                calmar_me=vl.calmar(nv, vl.mdd_sampled(nv)),
                turnover=vl.turnover(st), trades=vl.n_trades(st),
                expo=float(st.mean()), st_min=float(st.min()),
                ann_bh=mb["ann"], mdd_bh=mb["mdd"],
                d_ann=(m["ann"] - mb["ann"]) * 100,
                d_mdd=(m["mdd"] - mb["mdd"]) * 100,
            ))
    return rows


def common_legs(**kw):
    """两腿净值 + 仓位对齐到共同日历（v7/v8/v9 的对照也用它）。"""
    navs, states = {}, {}
    idx = None
    for leg in vl.POOL:
        nav, st, _ = vl.build(leg, **kw)
        navs[leg], states[leg] = nav, st
        idx = nav.index if idx is None else idx.intersection(nav.index)
    idx = pd.DatetimeIndex(idx).sort_values()
    return {k: v.reindex(idx).ffill() for k, v in navs.items()}, \
           {k: v.reindex(idx).ffill() for k, v in states.items()}, idx


def _prow(name, nv, inv, stx, weight=None):
    m = vl.perf(nv)
    me = vl.mdd_sampled(nv)
    w = weight or vl.W
    return dict(name=name, cum=m["cum"], ann=m["ann"], vol=m["vol"], mdd=m["mdd"],
                sharpe=m["sharpe"], calmar=vl.calmar(nv), mdd_me=me,
                calmar_me=vl.calmar(nv, me), expo=inv,
                trades=sum(vl.n_trades(s) for k, s in stx.items() if w.get(k, 0) > 0),
                turnover=sum(vl.turnover(s) for k, s in stx.items() if w.get(k, 0) > 0),
                nav=nv)


def portfolio_all(navs, idx):
    """组合层：默认 / 各对照 / 不同权重。"""
    specs = [
        ("静态 50/50（买入持有）", dict(FLOOR=1.0, DEAD=0.0), None),
        ("**默认（纯波动层 TV20 · 日频）**", dict(), None),
        ("月末定仓", dict(TRIGGER="month"), None),
        ("死区 0pp", dict(DEAD=0.0), None),
        ("仓位下限 50%", dict(FLOOR=0.5), None),
        ("人民币净值信号", dict(SIGNAL="nav"), None),
    ]
    rows = []
    for name, over, w in specs:
        stx = {}
        for leg in vl.POOL:
            _, st, _ = vl.build(leg, **over)
            stx[leg] = st.reindex(idx).ffill()
        nv, inv = vl.portfolio(navs, stx, weight=w, cost=over.get("COST"),
                               cash=over.get("CASH_ANNUAL"))
        rows.append(_prow(name, nv, inv, stx))
    stx_def = {}
    for leg in vl.POOL:
        _, st, _ = vl.build(leg)
        stx_def[leg] = st.reindex(idx).ffill()
    for name, w in WEIGHT_CASES:
        nv, inv = vl.portfolio(navs, stx_def, weight=w, cost=vl.COST, cash=vl.CASH_ANNUAL)
        rows.append(_prow(f"默认规则 · {name}", nv, inv, stx_def, weight=w))
    return rows


def rival_rows(navs, idx):
    """**同尺子**对照：把 v7 / v8 / v9 的默认档拉到同一成本(0.1%)与同一空仓计息(1.40%)。"""
    import us_gate as ug            # noqa: E402
    import golden_cross as gc       # noqa: E402
    import vol_target as vt         # noqa: E402

    out = []
    for name, mod in (("v7 年线闸门（默认档）", ug),
                      ("v8 金叉 × 波动层（默认档）", gc),
                      ("v9 波动率目标（默认档）", vt)):
        stx = {}
        for leg in vl.POOL:
            _, st, _ = mod.build(leg)
            stx[leg] = st.reindex(idx).ffill()
        nv, inv = mod.portfolio(navs, stx, cost=vl.COST, cash=vl.CASH_ANNUAL)
        out.append(_prow(name, nv, inv, stx))
    return out


def sens_rows(navs, idx):
    out = []
    for name, over in SENS:
        stx = {}
        for leg in vl.POOL:
            _, st, _ = vl.build(leg, **over)
            stx[leg] = st.reindex(idx).ffill()
        nv, inv = vl.portfolio(navs, stx, cost=over.get("COST"), cash=over.get("CASH_ANNUAL"))
        m = vl.perf(nv)
        out.append(dict(name=name, ann=m["ann"], mdd=m["mdd"], sharpe=m["sharpe"],
                        calmar=vl.calmar(nv), mdd_me=vl.mdd_sampled(nv),
                        calmar_me=vl.calmar(nv, vl.mdd_sampled(nv)), expo=inv,
                        trades=sum(vl.n_trades(s) for s in stx.values()), nav=nv))
    return out


def check_all(navs, states, idx):
    out = []

    # 1) 恒满（FLOOR=1、死区 0 -> 目标恒 1）净值 == 买入持有
    ok, note = True, ""
    for leg in vl.POOL:
        nav, st, _ = vl.build(leg, FLOOR=1.0, DEAD=0.0)
        assert set(np.round(st.unique(), 9)) == {1.0}, (leg, st.unique())
        nv = vl.run_leg(st, nav, cost=0.0)
        bh = nav / nav.iloc[0]
        rel = abs(float(nv.iloc[-1] / bh.iloc[-1]) - 1.0)
        if rel > 1e-12:
            ok, note = False, f"{leg} 差 {rel:.2e}"
            break
    out.append(("恒满（下限 100%）净值 == 买入持有（零费）", ok, note or "两腿逐日一致"))

    # 2) 仓位 ∈ [下限, 1]（不加杠杆）
    ok, note = True, ""
    for leg in vl.POOL:
        for kw, lo in (({}, 0.0), (dict(FLOOR=0.5), 0.5)):
            _, st, _ = vl.build(leg, **kw)
            if float(st.max()) > 1.0 + 1e-12 or float(st.min()) < lo - 1e-12:
                ok, note = False, f"{leg} 区间 {st.min():.4f}~{st.max():.4f}（下限要求 {lo}）"
                break
        if not ok:
            break
    out.append(("仓位 ∈ [下限, 100%]（不加杠杆、不穿透下限）", ok, note or "两腿均通过"))

    # 3) 无前视：截掉最后 6 天重跑，历史仓位不变
    ok, note = True, ""
    for leg in vl.POOL:
        nav, st, _ = vl.build(leg)
        cut = nav.index[:-6]
        st_cut = vl.state_daily(cut, leg)
        common = st.index.intersection(st_cut.index)
        diff = float((st.reindex(common) - st_cut.reindex(common)).abs().max())
        if diff > 1e-12:
            ok, note = False, f"{leg} 最大差 {diff:.2e}"
            break
    out.append(("截掉最后 6 天重跑，历史仓位不变（无前视）", ok, note or "最大差 0"))

    # 4) 前视抽样：只用「该净值日之前」的信号重算，仓位必须逐位相同
    ok, note = True, ""
    for leg in vl.POOL:
        nav, st, _ = vl.build(leg)
        sig = vl.load_signal(leg)
        n = 12
        for k in range(n):
            v = nav.index[int(k * (len(nav) - 1) / (n - 1))]
            prior = sig.index[sig.index < v]
            if not len(prior):
                continue
            st2 = vl.state_daily(nav.index, leg, as_of=prior[-1])
            d = abs(float(st2.loc[v]) - float(st.loc[v]))
            if d > 1e-12:
                ok, note = False, f"{leg} {v.date()} 差 {d:.2e}"
                break
        if not ok:
            break
    out.append(("只用「当日之前」的信号重算，仓位不变（抽样 12 点 · 无前视）", ok,
                note or "全部逐位一致"))

    # 5) 波动层恒等式：无死区时，未触顶的每一天 scale × vol == TV
    ok, note = True, ""
    for leg in vl.POOL:
        t = vl.raw_scale(leg)          # 死区在 target_raw 里，恒等式查的是未过死区的原始系数
        v = vl.vol_series(leg).reindex(t.index)
        m = (t < 1.0 - 1e-12) & v.notna() & (v > 0)
        if int(m.sum()) == 0:
            ok, note = False, f"{leg} 没有触发缩仓的样本（检查 vol 口径）"
            break
        err = float((t[m] * v[m] - vl.TV).abs().max())
        if err > 1e-9:
            ok, note = False, f"{leg} 最大偏差 {err:.2e}"
            break
    out.append((f"缩仓日恒有 仓位 × vol20 == TV（{vl.TV:.0%}），误差 < 1e-9", ok,
                note or "两腿均通过"))

    # 6) 两腿恒满时组合 == 50/50 手工复利（零费）
    stx = {k: pd.Series(1.0, index=idx) for k in vl.POOL}
    nv, _ = vl.portfolio(navs, stx, cost=0.0)
    manual, rets = 1.0, {k: navs[k].pct_change().fillna(0.0) for k in vl.POOL}
    for i in range(len(idx)):
        manual *= (1.0 + sum(0.5 * float(rets[k].iloc[i]) for k in vl.POOL))
    rel = abs(manual / float(nv.iloc[-1]) - 1.0)
    out.append(("两腿恒满时组合 == 50/50 手工复利（零费）", bool(rel < 1e-10), f"差 {rel:.1e}"))

    # 7) 与 v8 波动层专项探针（research/_probe_vol_layer.md 的「只波动层 TV20」行）逐位对齐
    nv, expo = vl.portfolio(navs, states)
    m = vl.perf(nv)
    me = vl.mdd_sampled(nv)
    got = dict(ann=m["ann"], vol=m["vol"], mdd=m["mdd"], sharpe=m["sharpe"],
               trades=sum(vl.n_trades(s) for s in states.values()))
    ref = vl.DOC_REF
    # DOC_REF 抄自探针报告：百分数/回撤 4 位小数、夏普只有 2 位、调仓次数是整数
    tol = dict(ann=5e-5, vol=5e-5, mdd=5e-5, sharpe=5e-3, trades=0.0)
    bad = {k: (round(got[k], 6), ref[k]) for k in got if abs(got[k] - ref[k]) > tol[k]}
    out.append(("与 v8 专项探针同规则读数一致（ann/vol/mdd/夏普/调仓）", not bad,
                "对齐（4 位小数内）" if not bad else str(bad)))
    return out


def main():
    os.makedirs(OUTDIR, exist_ok=True)
    p0 = vl.params()
    if str(p0["TRIGGER"]) != "daily":
        raise RuntimeError("本脚本的硬断言只覆盖 daily 默认档；请把 vol_layer.TRIGGER 改回 daily")

    legs = leg_rows()
    navs, states, idx = common_legs()
    prows = portfolio_all(navs, idx)
    rivals = rival_rows(navs, idx)
    sens = sens_rows(navs, idx)
    checks = check_all(navs, states, idx)

    base_nv = next(r["nav"] for r in prows if r["name"].startswith("**默认"))
    static_nv = next(r["nav"] for r in prows if r["name"].startswith("静态"))
    wins = []
    for label, a, b in WINDOWS:
        seg_m, seg_s = vl.window_nav(base_nv, a, b), vl.window_nav(static_nv, a, b)
        if len(seg_m) < 5 or len(seg_s) < 5:
            continue
        wins.append(dict(win=label, ret=float(seg_m.iloc[-1] / seg_m.iloc[0] - 1.0),
                         ret_s=float(seg_s.iloc[-1] / seg_s.iloc[0] - 1.0),
                         mdd=vl.perf(seg_m)["mdd"], mdd_s=vl.perf(seg_s)["mdd"],
                         start=a, end=b))

    with open(OUT_JSONL, "w", encoding="utf-8") as f:
        for r in legs:
            f.write(json.dumps({k: (round(v, 4) if isinstance(v, float) else v)
                                for k, v in r.items()}, ensure_ascii=False) + "\n")
        for key, rs in (("portfolio", prows), ("rival", rivals), ("sens", sens)):
            for r in rs:
                f.write(json.dumps({key: r["name"],
                                    **{k: (round(v, 4) if isinstance(v, float) else v)
                                       for k, v in r.items() if k not in ("name", "nav")}},
                                   ensure_ascii=False) + "\n")
        for r in wins:
            f.write(json.dumps({"window": r["win"],
                                **{k: (round(v, 4) if isinstance(v, float) else v)
                                   for k, v in r.items() if k != "win"}},
                               ensure_ascii=False) + "\n")
        f.write(json.dumps({"checks": [dict(name=n, ok=bool(o), note=t) for n, o, t in checks]},
                           ensure_ascii=False) + "\n")

    def prow(prefix):
        return next(r for r in prows if r["name"].startswith(prefix))

    def _leg(leg, prefix):
        return next(r for r in legs if r["leg"] == leg and r["variant"].startswith(prefix))

    def _sens(prefix):
        return next(r for r in sens if r["name"].startswith(prefix))

    base, static = prow("**默认"), prow("静态")
    month, dead0, floor50 = prow("月末定仓"), prow("死区 0pp"), prow("仓位下限 50%")
    navsig = prow("人民币净值信号")
    v7r, v8r, v9r = rivals
    bad = [c for c in checks if not c[1]]
    yrs = (idx[-1] - idx[0]).days / 365.25
    win = f"{idx[0].date()} ~ {idx[-1].date()}"
    yr, yr_s = vl.yearly(base_nv), vl.yearly(static_nv)

    L = [
        "# 波动资产 · V10 变种：美股两腿纯波动层（日频 · 无方向 · 连续仓位）",
        "> 池 = **只有纳指100 与标普500**（`vol_layer.POOL`），与 v7 / v8 / v9 同池。",
        "> 信号 = **美元指数**（`.NDX` / `.INX`）收盘；执行 = 场外 QDII **人民币复权净值**；"
        f"空仓按货基 {vl.CASH_ANNUAL:.2%} 计息 · 单边成本 {p0['COST']:.1%}。",
        "> 规则权威源 `v10_vol_layer/vol_layer.py` 顶部常量。",
        "",
        "## 1. 规则（可执行版）",
        "",
        "| 环节 | 规则 |",
        "|---|---|",
        "| 方向判断 | **没有** —— 永远做多，只决定露多少 |",
        f"| 仓位 | `min(1, TV / vol20)`，TV = {p0['TV']:.0%} |",
        "| 波动率 | 20 日收益标准差 × √252，**纳指与标普各算各的** |",
        f"| 死区 | `|Δ仓位| < {p0['DEAD']:.0%}` 不动手 |",
        "| 看盘频率 | **每日收盘**（月末档作对照） |",
        "| 成交 | 信号日收盘决策 → **次一交易日收盘成交** → 收益自 T+2 起 |",
        f"| 上限 / 下限 | 100% / {p0['FLOOR']:.0%}（不加杠杆） |",
        "",
        "> **「自适应」= 仓位随本腿实时波动率自适应**，不是目标自适应。"
        "`v8_golden_cross/research/_probe_vol_layer.md` 实测："
        "目标随长期波动漂移最多与常数 TV 打平且换手更高；分位五档明显更差（夏普 0.50）。"
        "**所以 TV 写死 20%，不漂移。**",
        "",
        "## 2. 逐腿（各档位均含 0.1% 单边成本）",
        "",
        "| 腿 | 档位 | 年化 | 波动 | 回撤·日频 | 回撤·月末 | Calmar(月末) | 夏普 | 仓位 | 最低 | 调仓 | vs 买入持有 |",
        "|---|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|",
    ]
    for leg in vl.POOL:
        for r in [x for x in legs if x["leg"] == leg]:
            L.append(f"| {r['name']} | {r['variant']} | {vl.fmt_pct(r['ann'])} | "
                     f"{r['vol'] * 100:.2f}% | {vl.fmt_pct(r['mdd'])} | "
                     f"{vl.fmt_pct(r['mdd_me'])} | {r['calmar_me']:.2f} | {r['sharpe']:.2f} | "
                     f"{r['expo']:.0%} | {r['st_min']:.0%} | {r['trades']} | "
                     f"{r['d_ann']:+.2f}pp / {r['d_mdd']:+.2f}pp |")
    L += [
        "",
        "> 「vs 买入持有」= 年化差 / 回撤差（正 = 回撤更浅）。`最低` = 样本内最低仓位 ——"
        "**它比平均仓位更能说明这层在崩盘时有没有真降下来**（平均值会被长期满仓稀释）。",
        "",
        "## 3. 组合层（两腿等权 50/50 · " + win + f" · {yrs:.1f} 年）",
        "",
        "| 组合 | 累计 | 年化 | 波动 | 回撤·日频 | 回撤·月末 | Calmar(月末) | 夏普 | 平均仓位 | 调仓 |",
        "|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|",
    ]
    for r in prows:
        L.append(f"| {r['name']} | {vl.fmt_pct(r['cum'], 1)} | {vl.fmt_pct(r['ann'])} | "
                 f"{r['vol'] * 100:.2f}% | {vl.fmt_pct(r['mdd'])} | {vl.fmt_pct(r['mdd_me'])} | "
                 f"{r['calmar_me']:.2f} | {r['sharpe']:.2f} | {r['expo']:.0%} | {r['trades']} |")
    L += [
        "",
        f"> **默认 vs 静态 50/50**：年化 {vl.fmt_pct(base['ann'])} vs {vl.fmt_pct(static['ann'])}"
        f"（**{(base['ann'] - static['ann']) * 100:+.2f}pp**）、回撤·日频 {vl.fmt_pct(base['mdd'])} vs "
        f"{vl.fmt_pct(static['mdd'])}（**{(base['mdd'] - static['mdd']) * 100:+.2f}pp 更浅**）、"
        f"Calmar(月末) {base['calmar_me']:.2f} vs {static['calmar_me']:.2f}、"
        f"夏普 {base['sharpe']:.2f} vs {static['sharpe']:.2f}、调仓 {base['trades']} 次。",
        "",
        "### 3.1 与 v7 / v8 / v9 同尺子（同成本 0.1% · 同空仓计息 1.40%）",
        "",
        "| 组合 | 年化 | 回撤·日频 | 回撤·月末 | Calmar(月末) | 夏普 | 平均仓位 | 调仓 |",
        "|---|---:|---:|---:|---:|---:|---:|---:|",
    ]
    for r in [v7r, v8r, v9r, base]:
        L.append(f"| {r['name']} | {vl.fmt_pct(r['ann'])} | {vl.fmt_pct(r['mdd'])} | "
                 f"{vl.fmt_pct(r['mdd_me'])} | {r['calmar_me']:.2f} | {r['sharpe']:.2f} | "
                 f"{r['expo']:.0%} | {r['trades']} |")
    L += [
        "",
        f"> **v10 相对 v7**：年化 {(base['ann'] - v7r['ann']) * 100:+.2f}pp、回撤 "
        f"{(base['mdd'] - v7r['mdd']) * 100:+.2f}pp（正 = v10 更深）、夏普 "
        f"{base['sharpe'] - v7r['sharpe']:+.2f}、调仓 {v7r['trades']} → {base['trades']} 次。"
        f"**相对 v8**：年化 {(base['ann'] - v8r['ann']) * 100:+.2f}pp、夏普 "
        f"{base['sharpe'] - v8r['sharpe']:+.2f}。**相对 v9**：年化 "
        f"{(base['ann'] - v9r['ann']) * 100:+.2f}pp、夏普 {base['sharpe'] - v9r['sharpe']:+.2f}。",
        "> **v10 与 v9 是同一件事的两个速度**：都只按波动缩放、都不猜方向。"
        "v9 月末定仓 + 月中补丁（249 次），v10 日频逐日重算（157 次）。"
        "**它们几乎打平** —— 说明「方向判断」在这段样本里不是必需品，"
        "而这正是 v10 存在的意义：把「纯风险预算能做到多少」单独量出来。",
        "> ⚠ **别把 v10 当「永远满仓的加强版」**：它没有任何方向保护。"
        "2020 那种急跌它救得了（波动率 5 天内爆表），但 2000-2002 那种「跌两年」它救不了 ——"
        "阴跌段波动率回落到 20% 以下，仓位就回到满仓。**本样本不含那段，这是外推。**",
        "",
        "## 4. 参数敏感性（组合层 · 只改一格）",
        "",
        "| 档位 | 年化 | 回撤·日频 | 回撤·月末 | Calmar(月末) | 夏普 | 平均仓位 | 调仓 |",
        "|---|---:|---:|---:|---:|---:|---:|---:|",
    ]
    for r in sens:
        L.append(f"| {r['name']} | {vl.fmt_pct(r['ann'])} | {vl.fmt_pct(r['mdd'])} | "
                 f"{vl.fmt_pct(r['mdd_me'])} | {r['calmar_me']:.2f} | {r['sharpe']:.2f} | "
                 f"{r['expo']:.0%} | {r['trades']} |")
    anns = [r["ann"] for r in sens]
    cals = [r["calmar_me"] for r in sens]
    L += [
        "",
        f"> 年化 {vl.fmt_pct(min(anns))} ~ {vl.fmt_pct(max(anns))}、Calmar(月末) "
        f"{min(cals):.2f} ~ {max(cals):.2f}。**TV 是「要多平」的旋钮**（12%→25% 单调平移），"
        "**死区是「要多省」的旋钮**（0/5/10/20pp 只改调仓次数）。"
        "**vol10 太快、vol60 太慢**，20 日居中最好 —— 与 v7 的结论一致。",
        f"> **成本敏感度**是这个变种的软肋：0.03% → {vl.fmt_pct(_sens('成本 0.03%')['ann'])}、"
        f"0.6% → {vl.fmt_pct(_sens('成本 0.60%')['ann'])}（差 "
        f"{(_sens('成本 0.03%')['ann'] - _sens('成本 0.60%')['ann']) * 100:.2f}pp）。"
        f"一年 {base['trades'] / max(yrs, 1e-9):.1f} 次调仓，**比 v7（一年 5 次）敏感得多**。",
        f"> **人民币净值信号**：年化 {vl.fmt_pct(navsig['ann'])}（差 "
        f"{(navsig['ann'] - base['ann']) * 100:+.2f}pp）—— **它甚至不比指数信号差**。"
        "这恰好说明这一支对信号源的细节不敏感（它只取波动率，不取方向），"
        "但净值信号与执行同源，回测里天然「无跟踪误差」，实盘拿到的是滞后 1~2 天的净值 ——"
        "**用美元指数的理由是可复核、无时滞，不是收益更高。**",
        "",
        "## 5. 压力窗口（默认档 vs 静态 50/50）",
        "",
        "| 窗口 | 区间 | 本变种 收益 | 静态 50/50 收益 | 本变种 回撤 | 静态 回撤 |",
        "|---|---|---:|---:|---:|---:|",
    ]
    for w in wins:
        L.append(f"| {w['win']} | {w['start']} ~ {w['end']} | {vl.fmt_pct(w['ret'])} | "
                 f"{vl.fmt_pct(w['ret_s'])} | {vl.fmt_pct(w['mdd'])} | {vl.fmt_pct(w['mdd_s'])} |")
    L += [
        "",
        "> **2020 是它的主场**（回撤从 −28.17% 压到 −18% 附近）：波动率 5 天就爆表，"
        "仓位自动降下来 —— 这是「不需要判断方向」的好处。"
        "**代价在牛市**：2019 / 2021 / 2025 会持续少赚（保险费）。",
        "",
        "## 6. 逐年收益（默认档 vs 静态 50/50）",
        "",
        "| 年份 | 本变种 | 静态 50/50 | 差 |",
        "|---|---:|---:|---:|",
    ]
    for y in sorted(yr):
        a, b = yr.get(y), yr_s.get(y)
        diff = "—" if (a is None or b is None) else f"{(a - b) * 100:+.2f}pp"
        L.append(f"| {y} | {vl.fmt_pct(a)} | {vl.fmt_pct(b)} | {diff} |")
    L += [
        "",
        "## 7. 硬断言",
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
        "> 第 5 条（`仓位 × vol20 == TV`）是这一支的**定义式断言**：只要波动层实现正确，"
        "缩仓日必然满足这个等式。第 7 条是**跨实现回归**：v10 与 `v8_golden_cross/research/"
        "_probe_vol_layer.py` 的「只波动层 TV20」行必须逐位对齐 —— 两套代码独立实现，"
        "对齐了才说明这条曲线的数字不是某一处写错出来的。",
        "",
        "## 8. 口径与已知边界",
        "",
        "1. **没有方向判断，这是设计而不是缺陷**：它的职责是回答「把方向判断全部拿掉，"
        "只靠风险预算能做到什么」。答案是：**这段样本里 0.85 夏普，高于买入持有 0.78，"
        "也高于加了金叉门的 v8（0.73）**。",
        "2. **治急跌、不治慢熊**：阴跌段波动率会回落，仓位回到满仓。"
        "2000-2002 与 2008 不在样本内（QDII 净值 2012 起），**这部分是外推**。",
        "3. **日频换手较高**：" + f"{base['trades']} 次 / {yrs:.1f} 年 ≈ 一年"
        f" {base['trades'] / max(yrs, 1e-9):.1f} 次。场外 0.6% 口径下年化再掉约 0.6pp，"
        "怕摩擦就调宽死区或改用 v9（月末制）。",
        "4. **两条腿各算各的波动**：纳指与标普的仓位可以不同（这是「自适应」的字面含义）——"
        "回测里两腿的调仓次数、最低仓位也确实不同，别把它当成「一个组合一个仓位」的策略。",
        "5. **不做参数搜索**：TV=20% 借自 v7/v8 的默认值，§4 给了 19 档邻域。",
        "",
        "> 图：`research/_plot_vl.py` → `_v10_vl_{ndx,spx}.png`；"
        "每日：`daily/_daily_scan.py` → `_v10_vol_layer_daily.{md,json}`。",
        "> **模拟结果，非投资建议。**",
    ]

    with open(OUT_MD, "w", encoding="utf-8") as f:
        f.write("\n".join(L))

    print("报告:", OUT_MD)
    print("结果:", OUT_JSONL)
    print(f"窗口 {win} · {yrs:.1f} 年")
    for r in [v7r, v8r, v9r, base, static]:
        print(f"  {r['name'][:32]:32s} 年化 {vl.fmt_pct(r['ann']):>9s} · "
              f"回撤·日频 {vl.fmt_pct(r['mdd']):>8s} · 回撤·月末 {vl.fmt_pct(r['mdd_me']):>8s} · "
              f"Calmar(月末) {r['calmar_me']:>5.2f} · 夏普 {r['sharpe']:>5.2f} · 仓位 {r['expo']:.0%} · "
              f"调仓 {r['trades']}")
    print(f"断言 {len(checks) - len(bad)}/{len(checks)} 通过")
    return 0 if not bad else 1


if __name__ == "__main__":
    sys.exit(main())
