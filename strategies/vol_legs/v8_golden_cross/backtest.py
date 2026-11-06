# -*- coding: utf-8 -*-
"""波动资产 · V8 变种（美股两腿金叉 50/200 × 波动层 TV20）· 回测。

池：**只有纳指100 与标普500**（`golden_cross.POOL`），与 v7 / v9 / v10 同池。
规则：`仓位 = (MA50 > MA200 ? 1 : 0) × min(1, TV/vol20)`，TV = 20%，**日频**、死区 10pp。
口径：
- 信号 = **美元指数**（`.NDX` / `.INX`），执行 = 场外 QDII 人民币复权净值（`data.series_for_leg`）；
- 成本 = 单边 0.1%（文档主口径），另给 0.6% 的场外现实口径；
- 空仓按货基 `rule.CASH_ANNUAL` 计息；
- 起点口径：首日目标仓位照算，不另计建仓费。

**旧口径仍在**：`VOL_ON=False` 就是 2026-11-05 之前的「纯二元金叉」，全部对照档保留，
所以这份报告同时回答「加了波动层变好多少」和「不加会怎样」。

产物（策略类，落 `backtest/`）：
- `_v8_golden_cross_bt.md` / `_v8_golden_cross_bt.jsonl`

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
for _p in (_HERE, os.path.join(_VL, "v7_us_gate"), os.path.join(_VL, "v9_vol_target"),
           os.path.join(_VL, "v10_vol_layer")):
    if _p not in sys.path:
        sys.path.insert(0, _p)

import golden_cross as gc  # noqa: E402

OUTDIR = os.path.join(_HERE, "backtest")
OUT_MD = os.path.join(OUTDIR, "_v8_golden_cross_bt.md")
OUT_JSONL = os.path.join(OUTDIR, "_v8_golden_cross_bt.jsonl")

# 逐腿档位：以「**」开头的行会被报告正文引用
VARIANTS = [
    ("买入持有", dict(FLOOR=1.0, VOL_ON=False)),
    ("纯二元金叉（VOL_ON=False · 旧默认）", dict(VOL_ON=False)),
    ("**金叉 × 波动层 TV20（日频 · 默认）**", dict()),
    ("金叉 × 波动层 TV15", dict(TV=0.15)),
    ("金叉 × 波动层 TV25", dict(TV=0.25)),
    ("金叉 × 波动层 · 死区 0pp", dict(VOL_DEAD=0.0)),
    ("金叉 × 波动层 · 死区 20pp", dict(VOL_DEAD=0.20)),
    ("金叉 × 波动层（月末定仓）", dict(TRIGGER="month")),
    ("金叉 × 波动层 + 迟滞 2%", dict(HYST=0.02)),
    ("金叉 × 波动层 + 价格在 MA200 上", dict(PRICE_FILTER=True)),
    ("金叉 × 波动层（死叉只减半）", dict(FLOOR=0.5)),
    ("MA40/200 × 波动层", dict(FAST=40)),
    ("MA60/200 × 波动层", dict(FAST=60)),
    ("MA100/200 × 波动层", dict(FAST=100)),
    ("MA50/150 × 波动层", dict(SLOW=150)),
    ("MA50/250 × 波动层", dict(SLOW=250)),
    ("人民币净值信号（反例）", dict(SIGNAL="nav")),
    ("成本 0.6%（场外现实口径）", dict(COST=0.006)),
]

SENS = [
    ("基准（金叉 × TV20 · vol20 · 死区 10pp · 日频）", {}),
    ("**关掉波动层**（纯二元金叉 · 旧默认）", dict(VOL_ON=False)),
    ("TV 12%", dict(TV=0.12)),
    ("TV 15%", dict(TV=0.15)),
    ("TV 18%", dict(TV=0.18)),
    ("TV 22%", dict(TV=0.22)),
    ("TV 25%", dict(TV=0.25)),
    ("vol10（更快）", dict(VOL_WIN=10)),
    ("vol60（更慢）", dict(VOL_WIN=60)),
    ("死区 0pp", dict(VOL_DEAD=0.0)),
    ("死区 5pp", dict(VOL_DEAD=0.05)),
    ("死区 20pp", dict(VOL_DEAD=0.20)),
    ("月末定仓", dict(TRIGGER="month")),
    ("迟滞带 2%", dict(HYST=0.02)),
    ("价格过滤（价格也在 MA200 上）", dict(PRICE_FILTER=True)),
    ("死叉只减半（0/50%）", dict(FLOOR=0.5)),
    ("快线 20 日", dict(FAST=20)),
    ("快线 40 日", dict(FAST=40)),
    ("快线 60 日", dict(FAST=60)),
    ("快线 100 日", dict(FAST=100)),
    ("慢线 120 日", dict(SLOW=120)),
    ("慢线 150 日", dict(SLOW=150)),
    ("慢线 250 日", dict(SLOW=250)),
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
    for leg in gc.POOL:
        for name, over in VARIANTS:
            nav, st, dec = gc.build(leg, **over)
            nv = gc.run_leg(st, nav, cost=over.get("COST"), cash=over.get("CASH_ANNUAL"))
            m = gc.perf(nv)
            bh = nav / nav.iloc[0]
            mb = gc.perf(bh)
            rows.append(dict(
                leg=leg, name=gc.rule.LEGS[leg]["name"], variant=name,
                start=nav.index[0].date().isoformat(), end=nav.index[-1].date().isoformat(),
                years=round((nav.index[-1] - nav.index[0]).days / 365.25, 2),
                ann=m["ann"], mdd=m["mdd"], vol=m["vol"], sharpe=m["sharpe"],
                calmar=gc.calmar(nv), mdd_me=gc.mdd_sampled(nv),
                calmar_me=gc.calmar(nv, gc.mdd_sampled(nv)),
                turnover=gc.turnover(st), trades=gc.n_trades(st),
                expo=float(st.mean()), st_min=float(st.min()),
                ann_bh=mb["ann"], mdd_bh=mb["mdd"],
                d_ann=(m["ann"] - mb["ann"]) * 100,
                d_mdd=(m["mdd"] - mb["mdd"]) * 100,
            ))
    return rows


def common_legs(**kw):
    """两腿净值 + 仓位对齐到共同日历（v7/v9/v10 的对照也用它）。"""
    navs, states = {}, {}
    idx = None
    for leg in gc.POOL:
        nav, st, _ = gc.build(leg, **kw)
        navs[leg], states[leg] = nav, st
        idx = nav.index if idx is None else idx.intersection(nav.index)
    idx = pd.DatetimeIndex(idx).sort_values()
    return {k: v.reindex(idx).ffill() for k, v in navs.items()}, \
           {k: v.reindex(idx).ffill() for k, v in states.items()}, idx


def _prow(name, nv, inv, stx, weight=None):
    m = gc.perf(nv)
    me = gc.mdd_sampled(nv)
    w = weight or gc.W
    return dict(name=name, cum=m["cum"], ann=m["ann"], vol=m["vol"], mdd=m["mdd"],
                sharpe=m["sharpe"], calmar=gc.calmar(nv), mdd_me=me,
                calmar_me=gc.calmar(nv, me), expo=inv,
                trades=sum(gc.n_trades(s) for k, s in stx.items() if w.get(k, 0) > 0),
                turnover=sum(gc.turnover(s) for k, s in stx.items() if w.get(k, 0) > 0),
                nav=nv)


def portfolio_all(navs, idx):
    """组合层：默认 / 各对照 / 不同权重。"""
    specs = [
        ("静态 50/50（买入持有）", dict(FLOOR=1.0, VOL_ON=False), None),
        ("纯二元金叉（VOL_ON=False · 旧默认）", dict(VOL_ON=False), None),
        ("**默认（金叉 × 波动层 TV20 · 日频）**", dict(), None),
        ("TV 15%", dict(TV=0.15), None),
        ("TV 25%", dict(TV=0.25), None),
        ("月末定仓", dict(TRIGGER="month"), None),
        ("死区 0pp", dict(VOL_DEAD=0.0), None),
        ("价格过滤", dict(PRICE_FILTER=True), None),
        ("死叉只减半", dict(FLOOR=0.5), None),
        ("人民币净值信号", dict(SIGNAL="nav"), None),
    ]
    rows = []
    for name, over, w in specs:
        stx = {}
        for leg in gc.POOL:
            _, st, _ = gc.build(leg, **over)
            stx[leg] = st.reindex(idx).ffill()
        nv, inv = gc.portfolio(navs, stx, weight=w, cost=over.get("COST"),
                               cash=over.get("CASH_ANNUAL"))
        rows.append(_prow(name, nv, inv, stx))
    stx_def = {}
    for leg in gc.POOL:
        _, st, _ = gc.build(leg)
        stx_def[leg] = st.reindex(idx).ffill()
    for name, w in WEIGHT_CASES:
        nv, inv = gc.portfolio(navs, stx_def, weight=w, cost=gc.COST, cash=gc.CASH_ANNUAL)
        rows.append(_prow(f"默认规则 · {name}", nv, inv, stx_def, weight=w))
    return rows


def rival_rows(navs, idx):
    """**同尺子**对照：v7 / v9 / v10 的默认档，同一成本(0.1%)、同一空仓计息(1.40%)。"""
    import us_gate as ug            # noqa: E402
    import vol_target as vt         # noqa: E402
    import vol_layer as vl10        # noqa: E402

    out = []
    for name, mod in (("v7 年线闸门（默认档）", ug),
                      ("v9 波动率目标（默认档）", vt),
                      ("v10 纯波动层（默认档）", vl10)):
        stx = {}
        for leg in gc.POOL:
            _, st, _ = mod.build(leg)
            stx[leg] = st.reindex(idx).ffill()
        nv, inv = mod.portfolio(navs, stx, cost=gc.COST, cash=gc.CASH_ANNUAL)
        out.append(_prow(name, nv, inv, stx))
    return out


def sens_rows(navs, idx):
    out = []
    for name, over in SENS:
        stx = {}
        for leg in gc.POOL:
            _, st, _ = gc.build(leg, **over)
            stx[leg] = st.reindex(idx).ffill()
        nv, inv = gc.portfolio(navs, stx, cost=over.get("COST"), cash=over.get("CASH_ANNUAL"))
        m = gc.perf(nv)
        out.append(dict(name=name, ann=m["ann"], mdd=m["mdd"], sharpe=m["sharpe"],
                        calmar=gc.calmar(nv), mdd_me=gc.mdd_sampled(nv),
                        calmar_me=gc.calmar(nv, gc.mdd_sampled(nv)), expo=inv,
                        trades=sum(gc.n_trades(s) for s in stx.values()), nav=nv))
    return out


def check_all(navs, states, idx):
    out = []

    # 1) 恒满（关波动层、下限 100%）净值 == 买入持有
    ok, note = True, ""
    for leg in gc.POOL:
        nav, st, _ = gc.build(leg, FLOOR=1.0, VOL_ON=False)
        assert set(np.round(st.unique(), 9)) == {1.0}, (leg, st.unique())
        nv = gc.run_leg(st, nav, cost=0.0)
        bh = nav / nav.iloc[0]
        rel = abs(float(nv.iloc[-1] / bh.iloc[-1]) - 1.0)
        if rel > 1e-12:
            ok, note = False, f"{leg} 差 {rel:.2e}"
            break
    out.append(("恒满（关波动层、下限 100%）净值 == 买入持有（零费）", ok, note or "两腿逐日一致"))

    # 2) 仓位 ∈ [0,1]；关掉波动层时只能是 {1, FLOOR}
    ok, note = True, ""
    for leg in gc.POOL:
        for kw, allowed in ((dict(), None), (dict(VOL_ON=False), {1.0, gc.FLOOR}),
                            (dict(VOL_ON=False, FLOOR=0.5), {1.0, 0.5})):
            _, st, _ = gc.build(leg, **kw)
            if float(st.max()) > 1.0 + 1e-12 or float(st.min()) < -1e-12:
                ok, note = False, f"{leg} 越界 {st.min():.4f}~{st.max():.4f}"
                break
            if allowed is not None and not set(np.round(st.unique(), 9)) <= allowed:
                ok, note = False, f"{leg} 出现 {sorted(set(np.round(st.unique(), 9)) - allowed)}"
                break
        if not ok:
            break
    out.append(("仓位 ∈ [0, 100%]；关波动层时只落在 {100%, 死叉后比例}（不加杠杆）", ok,
                note or "两腿均通过"))

    # 3) 无前视：截掉最后 6 天重跑，历史仓位不变
    ok, note = True, ""
    for leg in gc.POOL:
        nav, st, _ = gc.build(leg)
        cut = nav.index[:-6]
        st_cut = gc.state_daily(cut, leg)
        common = st.index.intersection(st_cut.index)
        diff = float((st.reindex(common) - st_cut.reindex(common)).abs().max())
        if diff > 1e-12:
            ok, note = False, f"{leg} 最大差 {diff:.2e}"
            break
    out.append(("截掉最后 6 天重跑，历史仓位不变（无前视）", ok, note or "最大差 0"))

    # 4) 前视抽样：只用「该净值日之前」的信号重算，仓位必须逐位相同
    ok, note = True, ""
    for leg in gc.POOL:
        nav, st, _ = gc.build(leg)
        sig = gc.load_signal(leg)
        n = 12
        for k in range(n):
            v = nav.index[int(k * (len(nav) - 1) / (n - 1))]
            prior = sig.index[sig.index < v]
            if not len(prior):
                continue
            st2 = gc.state_daily(nav.index, leg, as_of=prior[-1])
            d = abs(float(st2.loc[v]) - float(st.loc[v]))
            if d > 1e-12:
                ok, note = False, f"{leg} {v.date()} 差 {d:.2e}"
                break
        if not ok:
            break
    out.append(("只用「当日之前」的信号重算，仓位不变（抽样 12 点 · 无前视）", ok,
                note or "全部逐位一致"))

    # 5) 波动层定义式：无死区时，金叉状态里未触顶的每一天 仓位 × vol == TV；死叉日仓位 == 0
    ok, note = True, ""
    for leg in gc.POOL:
        t = gc.target_raw(leg, gc.params(VOL_DEAD=0.0))
        base = gc.golden_raw(leg)
        v = gc.vol_series(leg).reindex(t.index)
        m = (base > 0.5) & (t < 1.0 - 1e-12) & v.notna() & (v > 0)
        if int(m.sum()) == 0:
            ok, note = False, f"{leg} 没有触发缩仓的样本（检查 vol 口径）"
            break
        err = float((t[m] * v[m] - gc.TV).abs().max())
        dead = float(t[base <= 0.5].abs().max()) if bool((base <= 0.5).any()) else 0.0
        if err > 1e-9 or dead > 1e-12:
            ok, note = False, f"{leg} 缩仓偏差 {err:.2e} / 死叉残仓 {dead:.2e}"
            break
    out.append((f"缩仓日恒有 仓位 × vol20 == TV（{gc.TV:.0%}），死叉日残仓为 0", ok,
                note or "两腿均通过"))

    # 6) 两腿恒满时组合 == 50/50 手工复利（零费）
    stx = {k: pd.Series(1.0, index=idx) for k in gc.POOL}
    nv, _ = gc.portfolio(navs, stx, cost=0.0)
    manual, rets = 1.0, {k: navs[k].pct_change().fillna(0.0) for k in gc.POOL}
    for i in range(len(idx)):
        manual *= (1.0 + sum(0.5 * float(rets[k].iloc[i]) for k in gc.POOL))
    rel = abs(manual / float(nv.iloc[-1]) - 1.0)
    out.append(("两腿恒满时组合 == 50/50 手工复利（零费）", bool(rel < 1e-10), f"差 {rel:.1e}"))

    # 7) 门的正确性：关波动层时「翻转次数 == 调仓次数」且买卖交替；
    #    开波动层时「每次金叉翻转的下一净值日，仓位必须离开/回到 0」
    ok, note = True, ""
    for leg in gc.POOL:
        nav, st, dec = gc.build(leg, VOL_ON=False)
        ops, cx = gc.actions(st), dec
        if len(ops) != len(cx):
            ok, note = False, f"{leg}（机关）调仓 {len(ops)} 次 vs 翻转 {len(cx)} 次"
            break
        for a, b in zip(ops, cx):
            if a["kind"] != b["kind"] or abs(a["to"] - b["to"]) > 1e-12:
                ok, note = False, f"{leg} {a['date'].date()} 与翻转点不一致"
                break
        if not ok:
            break
        nav2, st2, dec2 = gc.build(leg)
        idx2 = pd.DatetimeIndex(st2.index)
        for x in dec2:
            pos = int(idx2.searchsorted(x["date"], side="right"))
            if 0 < pos < len(idx2):
                got = float(st2.loc[idx2[pos]])
                if x["to"] > 0.5 and got <= 1e-9:
                    ok, note = False, f"{leg} {x['date'].date()} 金叉后仓位仍为 0"
                    break
                if x["to"] <= 0.5 and got > 1e-9:
                    ok, note = False, f"{leg} {x['date'].date()} 死叉后仓位未清零（{got:.2f}）"
                    break
        if not ok:
            break
    out.append(("门正确：关波动层时翻转次数 == 调仓次数；开波动层时死叉次日必清零", ok,
                note or "两腿逐笔一致"))

    # 8) 月末档：决策只在月末、仓位 = 最近一次决策的目标（T+1）
    ok, note = True, ""
    for leg in gc.POOL:
        sig = gc.load_signal(leg)
        nav, st, dec = gc.build(leg, TRIGGER="month")
        sig_idx = pd.DatetimeIndex(sig.index)
        ends = set(gc.month_ends(sig.index))
        for x in dec:
            if x["date"] not in ends:
                ok, note = False, f"{leg} 决策日 {x['date'].date()} 不是月末"
                break
            pos_s = int(sig_idx.searchsorted(x["date"], side="right"))
            if pos_s < len(sig_idx) and (sig_idx[pos_s].year, sig_idx[pos_s].month) == \
                    (x["date"].year, x["date"].month):
                ok, note = False, f"{leg} 决策日 {x['date'].date()} 不是信号月末"
                break
        if not ok:
            break
        dts = np.array([x["date"] for x in dec])
        tg = np.array([x["target"] for x in dec])
        exp = [float(tg[dts < d][-1]) if (dts < d).any() else 1.0 for d in st.index]
        if not np.allclose(st.to_numpy(dtype=float), np.array(exp)):
            ok, note = False, f"{leg} 月度仓位与「最近一次决策」不符"
            break
    out.append(("月末档：决策只在月末、仓位 = 最近一次决策的目标（T+1）", ok,
                note or "两腿逐日一致"))
    return out


def main():
    os.makedirs(OUTDIR, exist_ok=True)
    p0 = gc.params()
    if str(p0["TRIGGER"]) != "daily" or not p0["VOL_ON"]:
        raise RuntimeError("本脚本的硬断言覆盖 daily + 开波动层的默认档；"
                           "请把 golden_cross.TRIGGER 改回 daily、VOL_ON 改回 True")

    legs = leg_rows()
    navs, states, idx = common_legs()
    prows = portfolio_all(navs, idx)
    rivals = rival_rows(navs, idx)
    sens = sens_rows(navs, idx)
    checks = check_all(navs, states, idx)

    base_nv = next(r["nav"] for r in prows if r["name"].startswith("**默认"))
    static_nv = next(r["nav"] for r in prows if r["name"].startswith("静态"))
    binary_nv = next(r["nav"] for r in prows if r["name"].startswith("纯二元"))
    wins = []
    for label, a, b in WINDOWS:
        seg_m, seg_s = gc.window_nav(base_nv, a, b), gc.window_nav(static_nv, a, b)
        if len(seg_m) < 5 or len(seg_s) < 5:
            continue
        wins.append(dict(win=label, ret=float(seg_m.iloc[-1] / seg_m.iloc[0] - 1.0),
                         ret_s=float(seg_s.iloc[-1] / seg_s.iloc[0] - 1.0),
                         mdd=gc.perf(seg_m)["mdd"], mdd_s=gc.perf(seg_s)["mdd"],
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

    def _sens(prefix):
        return next(r for r in sens if r["name"].startswith(prefix))

    base, static = prow("**默认"), prow("静态")
    binary, month = prow("纯二元"), prow("月末定仓")
    tv15, tv25, dead0 = prow("TV 15%"), prow("TV 25%"), prow("死区 0pp")
    pflt, halff, navsig = prow("价格过滤"), prow("死叉只减半"), prow("人民币净值信号")
    v7r, v9r, v10r = rivals
    off = _sens("**关掉波动层**")
    bad = [c for c in checks if not c[1]]
    yrs = (idx[-1] - idx[0]).days / 365.25
    win = f"{idx[0].date()} ~ {idx[-1].date()}"
    yr, yr_s = gc.yearly(base_nv), gc.yearly(static_nv)

    L = [
        "# 波动资产 · V8 变种：美股两腿金叉 50/200 × 波动层 TV20（日频 · 连续仓位）",
        "> 池 = **只有纳指100 与标普500**（`golden_cross.POOL`），与 v7 / v9 / v10 同池。",
        "> 信号 = **美元指数**（`.NDX` / `.INX`）收盘；执行 = 场外 QDII **人民币复权净值**；"
        f"空仓按货基 {gc.CASH_ANNUAL:.2%} 计息 · 单边成本 {p0['COST']:.1%}。",
        "> 规则权威源 `v8_golden_cross/golden_cross.py` 顶部常量。",
        "> **2026-11-05 起波动层为默认**（`VOL_ON=True`）；旧口径 `VOL_ON=False` 全部保留作对照，",
        "> 所以这份报告同时回答「加了波动层变好多少」与「不加会怎样」。",
        "",
        "## 1. 规则（可执行版）",
        "",
        "| 环节 | 规则 |",
        "|---|---|",
        "| 信号价 | 美元指数收盘（**不是**人民币净值，见 §5 反例档） |",
        f"| 方向门 | MA{p0['FAST']} > MA{p0['SLOW']} → 1；否则 → 0 |",
        f"| 波动层 | × `min(1, TV / vol20)`，TV = {p0['TV']:.0%} |",
        f"| 死区 | 方向未变时 `|Δ仓位| < {p0['VOL_DEAD']:.0%}` 不动手 |",
        "| 看盘频率 | **每日收盘**（月末档见 §5） |",
        "| 成交 | 信号日收盘决策 → **次一交易日收盘成交** → 收益自 T+2 起 |",
        f"| 上下限 | 100% / {p0['FLOOR']:.0%}（不加杠杆） |",
        "",
        "> **为什么加这一层**（`research/_probe_vol_layer.md`）：纯二元金叉的回撤与买入持有完全相同，",
        "> 因为它治趋势、不治急跌 —— 2020 年方向信号要 5 周才到（MA50 到 04-30 才穿 MA200），",
        "> 而波动信号 5 天就到。加层后回撤与 Calmar 大幅改善，代价是年化小跌、换手上升。",
        "> **「自适应」的四种变体（估计子 / 目标 / 档位）都没有赢过常数 TV20**，所以这里写死。",
        "",
        "## 2. 逐腿（各档位均含 0.1% 单边成本）",
        "",
        "| 腿 | 档位 | 年化 | 波动 | 回撤·日频 | 回撤·月末 | Calmar(月末) | 夏普 | 仓位 | 最低 | 调仓 | vs 买入持有 |",
        "|---|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|",
    ]
    for leg in gc.POOL:
        for r in [x for x in legs if x["leg"] == leg]:
            L.append(f"| {r['name']} | {r['variant']} | {gc.fmt_pct(r['ann'])} | "
                     f"{r['vol'] * 100:.2f}% | {gc.fmt_pct(r['mdd'])} | "
                     f"{gc.fmt_pct(r['mdd_me'])} | {r['calmar_me']:.2f} | {r['sharpe']:.2f} | "
                     f"{r['expo']:.0%} | {r['st_min']:.0%} | {r['trades']} | "
                     f"{r['d_ann']:+.2f}pp / {r['d_mdd']:+.2f}pp |")
    L += [
        "",
        "> 「vs 买入持有」= 年化差 / 回撤差（正 = 回撤更浅）。两种回撤口径：`日频` = 逐日净值"
        "（风控看这个）；`月末` = 只在月末取点（与 v7/v9 报告同口径，便于并排）。"
        "`最低` = 样本内最低仓位 —— 比平均仓位更能说明崩盘时有没有真降下来。",
        "",
        "## 3. 组合层（两腿等权 50/50 · " + win + f" · {yrs:.1f} 年）",
        "",
        "| 组合 | 累计 | 年化 | 波动 | 回撤·日频 | 回撤·月末 | Calmar(月末) | 夏普 | 平均仓位 | 调仓 |",
        "|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|",
    ]
    for r in prows:
        L.append(f"| {r['name']} | {gc.fmt_pct(r['cum'], 1)} | {gc.fmt_pct(r['ann'])} | "
                 f"{r['vol'] * 100:.2f}% | {gc.fmt_pct(r['mdd'])} | {gc.fmt_pct(r['mdd_me'])} | "
                 f"{r['calmar_me']:.2f} | {r['sharpe']:.2f} | {r['expo']:.0%} | {r['trades']} |")
    L += [
        "",
        f"> **波动层做了什么**：纯二元金叉 {gc.fmt_pct(binary['ann'])} / 回撤 "
        f"{gc.fmt_pct(binary['mdd'])} / 夏普 {binary['sharpe']:.2f} / 调仓 {binary['trades']} 次 → "
        f"加层后 {gc.fmt_pct(base['ann'])} / **{gc.fmt_pct(base['mdd'])}** / 夏普 "
        f"**{base['sharpe']:.2f}** / 调仓 {base['trades']} 次。"
        f"**回撤浅 {(base['mdd'] - binary['mdd']) * 100:.2f}pp、Calmar(月末) "
        f"{binary['calmar_me']:.2f} → {base['calmar_me']:.2f}**，代价是年化 "
        f"{(base['ann'] - binary['ann']) * 100:+.2f}pp、调仓翻 "
        f"{base['trades'] / max(binary['trades'], 1):.1f} 倍。",
        f"> **但它仍打不过买入持有**：年化 {gc.fmt_pct(base['ann'])} vs {gc.fmt_pct(static['ann'])}、"
        f"夏普 {base['sharpe']:.2f} vs {static['sharpe']:.2f}。"
        f"**这一支的价值在「同回撤下更高夏普」**（Calmar {base['calmar_me']:.2f} vs "
        f"{static['calmar_me']:.2f}），不在绝对收益。",
        "",
        "### 3.1 与 v7 / v9 / v10 同尺子（同成本 0.1% · 同空仓计息 1.40%）",
        "",
        "| 组合 | 年化 | 回撤·日频 | 回撤·月末 | Calmar(月末) | 夏普 | 平均仓位 | 调仓 |",
        "|---|---:|---:|---:|---:|---:|---:|---:|",
    ]
    for r in [v7r, base, v9r, v10r]:
        L.append(f"| {r['name']} | {gc.fmt_pct(r['ann'])} | {gc.fmt_pct(r['mdd'])} | "
                 f"{gc.fmt_pct(r['mdd_me'])} | {r['calmar_me']:.2f} | {r['sharpe']:.2f} | "
                 f"{r['expo']:.0%} | {r['trades']} |")
    L += [
        "",
        f"> **v8 相对 v7**：年化 {(base['ann'] - v7r['ann']) * 100:+.2f}pp、回撤 "
        f"{(base['mdd'] - v7r['mdd']) * 100:+.2f}pp（正 = v8 更深）、夏普 "
        f"{base['sharpe'] - v7r['sharpe']:+.2f}、调仓 {v7r['trades']} → {base['trades']} 次。"
        f"**相对 v9**：年化 {(base['ann'] - v9r['ann']) * 100:+.2f}pp、夏普 "
        f"{base['sharpe'] - v9r['sharpe']:+.2f}。",
        f"> **v8 相对 v10**（只波动层、无方向门）：年化 "
        f"{(base['ann'] - v10r['ann']) * 100:+.2f}pp、夏普 "
        f"{base['sharpe'] - v10r['sharpe']:+.2f}、调仓 {base['trades']} vs {v10r['trades']} 次。"
        "**同一层波动层，加了金叉门反而更差** —— 这是 2026-11-05 的实测结论，"
        "也是 v10 单独成支的原因。**方向门在这段样本里是净扣分项**，"
        "但它买的是「长熊里的保护」（本样本没有那段，见 §8）。",
        "",
        "## 4. 参数敏感性（组合层 · 只改一格）",
        "",
        "| 档位 | 年化 | 回撤·日频 | 回撤·月末 | Calmar(月末) | 夏普 | 平均仓位 | 调仓 |",
        "|---|---:|---:|---:|---:|---:|---:|---:|",
    ]
    for r in sens:
        L.append(f"| {r['name']} | {gc.fmt_pct(r['ann'])} | {gc.fmt_pct(r['mdd'])} | "
                 f"{gc.fmt_pct(r['mdd_me'])} | {r['calmar_me']:.2f} | {r['sharpe']:.2f} | "
                 f"{r['expo']:.0%} | {r['trades']} |")
    anns = [r["ann"] for r in sens]
    cals = [r["calmar_me"] for r in sens]
    L += [
        "",
        f"> 年化 {gc.fmt_pct(min(anns))} ~ {gc.fmt_pct(max(anns))}、Calmar(月末) "
        f"{min(cals):.2f} ~ {max(cals):.2f}。",
        f"> **开关对比**：关掉波动层 → {gc.fmt_pct(off['ann'])} / 回撤 {gc.fmt_pct(off['mdd'])} / "
        f"夏普 {off['sharpe']:.2f} / 调仓 {off['trades']} 次；打开（基准）→ "
        f"{gc.fmt_pct(base['ann'])} / {gc.fmt_pct(base['mdd'])} / {base['sharpe']:.2f} / "
        f"{base['trades']} 次。**这就是这一层在 14.2 年里的全部账。**",
        f"> **TV 与死区**：TV 15% / 20% / 25% → 年化 {gc.fmt_pct(tv15['ann'])} / "
        f"{gc.fmt_pct(base['ann'])} / {gc.fmt_pct(tv25['ann'])}、回撤 {gc.fmt_pct(tv15['mdd'])} / "
        f"{gc.fmt_pct(base['mdd'])} / {gc.fmt_pct(tv25['mdd'])}、调仓 {tv15['trades']} / "
        f"{base['trades']} / {tv25['trades']} 次；死区 0pp → 调仓 {dead0['trades']} 次而收益几乎不变"
        f"（{(dead0['ann'] - base['ann']) * 100:+.2f}pp）—— **死区只省摩擦，不改结论**。",
        f"> **均线组合仍是方向门的唯一旋钮**：快线 20/40/60/100、慢线 120/150/250 都在窄带里平移，"
        f"夏普 {min(_sens('快线 20 日')['sharpe'], _sens('慢线 250 日')['sharpe']):.2f}~"
        f"{max(_sens('快线 20 日')['sharpe'], _sens('慢线 250 日')['sharpe']):.2f}，没有尖峰。",
        f"> **成本**：0.03% → {gc.fmt_pct(_sens('成本 0.03%')['ann'])}、0.6% → "
        f"{gc.fmt_pct(_sens('成本 0.60%')['ann'])}（差 "
        f"{(_sens('成本 0.03%')['ann'] - _sens('成本 0.60%')['ann']) * 100:.2f}pp）。"
        f"一年 {base['trades'] / max(yrs, 1e-9):.1f} 次调仓 —— **加层后它不再是最省的一支**"
        f"（v7 一年 {v7r['trades'] / max(yrs, 1e-9):.1f} 次）。",
        f"> **人民币净值信号（反例）**：年化 {gc.fmt_pct(navsig['ann'])}（差 "
        f"{(navsig['ann'] - base['ann']) * 100:+.2f}pp）。净值信号与执行同源，"
        "回测里天然无跟踪误差、且滞后 1~2 天 —— **用美元指数是为了口径纯净、可复核**。",
        "",
        "## 5. 压力窗口（默认档 vs 静态 50/50）",
        "",
        "| 窗口 | 区间 | 本变种 收益 | 静态 50/50 收益 | 本变种 回撤 | 静态 回撤 |",
        "|---|---|---:|---:|---:|---:|",
    ]
    for w in wins:
        L.append(f"| {w['win']} | {w['start']} ~ {w['end']} | {gc.fmt_pct(w['ret'])} | "
                 f"{gc.fmt_pct(w['ret_s'])} | {gc.fmt_pct(w['mdd'])} | {gc.fmt_pct(w['mdd_s'])} |")
    L += [
        "",
        f"> **2020 是这一层买的东西**：回撤 {gc.fmt_pct(wins[1]['mdd_s'])} → "
        f"{gc.fmt_pct(wins[1]['mdd'])}（浅 {(wins[1]['mdd'] - wins[1]['mdd_s']) * 100:.2f}pp）；"
        f"2018Q4 {gc.fmt_pct(wins[0]['mdd_s'])} → {gc.fmt_pct(wins[0]['mdd'])}、"
        f"2022 {gc.fmt_pct(wins[2]['mdd_s'])} → {gc.fmt_pct(wins[2]['mdd'])}。"
        "**慢熊里波动率一路上行，本来就该降仓** —— 这是它和「纯二元金叉」最大的差别。",
        f"> **代价年仍在牛市**：2025 年内 {gc.fmt_pct(wins[3]['ret'])} vs 静态 "
        f"{gc.fmt_pct(wins[3]['ret_s'])} —— 保险费，不是 bug。",
        "",
        "## 6. 逐年收益（默认档 vs 静态 50/50）",
        "",
        "| 年份 | 本变种 | 静态 50/50 | 差 |",
        "|---|---:|---:|---:|",
    ]
    for y in sorted(yr):
        a, b = yr.get(y), yr_s.get(y)
        diff = "—" if (a is None or b is None) else f"{(a - b) * 100:+.2f}pp"
        L.append(f"| {y} | {gc.fmt_pct(a)} | {gc.fmt_pct(b)} | {diff} |")
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
        "> 第 5 条（`仓位 × vol20 == TV`、死叉日残仓为 0）是波动层的**定义式断言**："
        "两种杠杆同时生效时，这条等式必须逐日成立。第 4 条用 `as_of` 把信号真的截断到"
        "「当日之前」再重算 —— 不是嘴上说没有前视，而是逐点验证。",
        "",
        "## 8. 长样本证据（1995-2026 · 指数口径 · 不含分红）",
        "",
        "| 策略 | 年化 | 最大回撤 | 夏普 | 调仓 |",
        "|---|---:|---:|---:|---:|",
        f"| 买入持有 纳指100 | +14.37% | −82.90% | 0.48 | 0 |",
        f"| 金叉 50/200（日频，不含波动层） | +16.08% | −35.74% | 0.72 | 36 |",
        "",
        "> 长样本里金叉三项全赢（**回撤砍掉 47pp**）—— 这正是它值得留一支的理由。"
        "**但波动层在长样本里的作用未知**（本仓库无 1995 起的 QDII 净值），别把 §3 的改善外推。",
        "> **两段证据要一起读**：短样本（2012+，含真实申赎与汇率）说「加波动层后仍打不过买入持有」，"
        "长样本（1995+，含两次腰斩）说「方向门砍掉了 47pp 回撤」——"
        "**前者缺它唯一起作用的两段行情，后者缺你真能拿到的成本与跟踪误差。**",
        "",
        "## 9. 口径与边界",
        "",
        "1. **样本只到 2012**（QDII 净值起点）：不含 2000-2002（纳指 −83%）与 2008。",
        "2. **波动层治急跌、方向门治慢熊**：两者在这一支里同时存在，但**各自的验证样本都不完整** ——"
        "短样本证明了波动层的价值（2020），长样本证明了方向门的价值（2000-2008）。",
        "3. **日频换手不低**：" + f"{base['trades']} 次 / {yrs:.1f} 年 ≈ 一年 "
        f"{base['trades'] / max(yrs, 1e-9):.1f} 次，0.6% 口径掉 "
        f"{(_sens('成本 0.03%')['ann'] - _sens('成本 0.60%')['ann']) * 100:.2f}pp。",
        "4. **只降不升、不加杠杆**：仓位恒 ∈ [0, 100%]。",
        "5. **QDII 通道风险未计**：额度关闭、限购、溢价、赎回 T+2~T+7。",
        "6. **不做 walk-forward 选优**：TV=20% 借自 v7 默认，§4 给了 28 档邻域。",
        "",
        "> 模拟结果，非投资建议。",
    ]

    with open(OUT_MD, "w", encoding="utf-8") as f:
        f.write("\n".join(L))

    print("报告:", OUT_MD)
    print("结果:", OUT_JSONL)
    print(f"窗口 {win} · {yrs:.1f} 年")
    for r in [v7r, base, binary, v9r, v10r, static]:
        print(f"  {r['name'][:34]:34s} 年化 {gc.fmt_pct(r['ann']):>9s} · "
              f"回撤·日频 {gc.fmt_pct(r['mdd']):>8s} · 回撤·月末 {gc.fmt_pct(r['mdd_me']):>8s} · "
              f"Calmar(月末) {r['calmar_me']:>5.2f} · 夏普 {r['sharpe']:>5.2f} · 仓位 {r['expo']:.0%} · "
              f"调仓 {r['trades']}")
    print(f"断言 {len(checks) - len(bad)}/{len(checks)} 通过")
    return 0 if not bad else 1


if __name__ == "__main__":
    sys.exit(main())
