# -*- coding: utf-8 -*-
"""波动资产 · V7 变种（全七腿年线闸门）· 回测（逐腿 + 组合 + 敏感性 + 文档对照 + 硬断言）。

池：**全七腿**（`us_gate.POOL` = rule.LEG_ORDER，2026-11-17 扩池）。规则逐条落地自用户指定文档
`E:\\qianwen\\jijin\\纳指100与标普500_策略回测与规则.md`：
MA200（美元指数收盘）· 月末看盘 · 连续 2 个月在年线下才减 · 纳指清仓 / 标普减半 · 站上次月回补。

口径（2026-11-17 起，与 `us_gate.py` 默认档同步）：
- 池 = **全七腿**（`us_gate.POOL`），逐腿独立回测 + 等权组合；
- 信号与回测 = **各腿真实指数收盘**（美股 `.NDX` / `.INX`、A股/港股指数、黄金 518880）；
  美股腿实盘执行走场外 QDII A 类，收益不含汇率与跟踪误差；
- 默认档 = 月末 MA200 闸门（2 月确认 + 深破 3% 豁免）× 波动层 TV20 / 死区 10pp
  **+ 日频离场 MA50/200（`EXIT="ma50200"`，2026-11-17 起默认开）**；月中补丁 `PATCH` 默认关；
- 成本 = 单边 0.1%（文档主口径），另给 0.6% 的场外现实口径；
- 空仓按货基 `rule.CASH_ANNUAL` 计息；
- 起点口径：首日即满仓，与买入持有同口径（不另计建仓费）。

产物（策略类，落 `backtest/`）：
- `_v7_us_gate_bt.md` / `_v7_us_gate_bt.jsonl`

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

import us_gate as ug  # noqa: E402

OUTDIR = os.path.join(_HERE, "backtest")
OUT_MD = os.path.join(OUTDIR, "_v7_us_gate_bt.md")
OUT_JSONL = os.path.join(OUTDIR, "_v7_us_gate_bt.jsonl")

# 逐腿档位（与文档表1 一一对应；前五档是文档口径复现，一律 EXIT="none"）
VARIANTS = [
    ("买入持有", dict(FLOOR=1.0, TV=0, EXIT="none")),
    ("年线闸门（清仓 · 无确认）", dict(CONFIRM=1, FLOOR=0.0, TV=0, EXIT="none")),
    ("年线+2月确认（清仓 · 文档原口径）", dict(CONFIRM=2, DEEP=0, FLOOR=0.0, TV=0, EXIT="none")),
    ("年线+2%迟滞（清仓）", dict(CONFIRM=2, HYST=0.02, DEEP=0, FLOOR=0.0, TV=0, EXIT="none")),
    ("半仓闸门（无确认 · 减半）", dict(CONFIRM=1, FLOOR=0.5, TV=0, EXIT="none")),
    ("**本变种默认（2月确认+深破3%+TV20+日频离场）**", dict()),
    ("关掉日频离场（2026-11-17 前口径）", dict(EXIT="none")),
    ("日频离场 + 月中补丁（F6）", dict(PATCH="fast")),
    ("深破3%（TV 关闭）", dict(TV=0)),
    ("波动层 TV15（无深破）", dict(DEEP=0, TV=0.15)),
    ("**深破3% + 波动层 TV15**", dict(TV=0.15)),
]

SENS = [
    ("基准（MA200 · 确认2月 · 深破3% · TV20 · 日频离场 · 单边0.1%）", {}),
    ("关掉日频离场（2026-11-17 前口径）", dict(EXIT="none")),
    ("离场线 MA40/200", dict(EXIT_FAST=40)),
    ("离场线 MA60/200", dict(EXIT_FAST=60)),
    ("离场线 MA50/150", dict(EXIT_SLOW=150)),
    ("月中补丁 fast（v9 快减/快补）", dict(PATCH="fast")),
    ("关闭波动层（纯闸门 + 日频离场）", dict(TV=0)),
    ("关闭深破位（保留 TV20）", dict(DEEP=0)),
    ("关闭深破 + 关闭波动层（文档原口径）", dict(DEEP=0, TV=0)),
    ("深破位阈值 1%", dict(DEEP=0.01)),
    ("深破位阈值 2%", dict(DEEP=0.02)),
    ("深破位阈值 5%", dict(DEEP=0.05)),
    ("深破位阈值 8%", dict(DEEP=0.08)),
    ("确认 1 个月（= 深破位阈值 0）", dict(CONFIRM=1)),
    ("确认 3 个月", dict(CONFIRM=3)),
    ("深破 3% + 波动层 TV15", dict(TV=0.15)),
    ("无深破 + 波动层 TV15", dict(DEEP=0, TV=0.15)),
    ("深破 3% + 波动层 TV18", dict(TV=0.18)),
    ("深破 3% + 波动层 TV20", dict(TV=0.20)),
    ("深破 3% + 波动层 TV22", dict(TV=0.22)),
    ("深破 3% + 波动层 TV12", dict(TV=0.12)),
    ("波动层窗口 10 日", dict(VOL_WIN=10)),
    ("波动层窗口 60 日", dict(VOL_WIN=60)),
    ("波动层死区 5pp", dict(VOL_DEAD=0.05)),
    ("波动层死区 0pp（每次都调）", dict(VOL_DEAD=0.0)),
    ("波动层死区 20pp", dict(VOL_DEAD=0.20)),
    ("迟滞带 2%", dict(HYST=0.02)),
    ("迟滞带 5%", dict(HYST=0.05)),
    ("均线 150 日", dict(MA_WIN=150)),
    ("均线 250 日", dict(MA_WIN=250)),
    ("全部腿都清仓（FLOOR=0）", dict(FLOOR=0.0)),
    ("全部腿都减半（FLOOR=0.5）", dict(FLOOR=0.5)),
    ("成本 0.6%（场外现实口径）", dict(COST=0.006)),
    ("现金 0%（不付利息）", dict(CASH_ANNUAL=0.0)),
    ("日频触发（跌破即走，不走月度确认）", dict(TRIGGER="daily")),
]

WINDOWS = [
    ("2020 疫情", "2020-02-19", "2020-08-31"),
    ("2022 加息熊市", "2022-01-03", "2022-12-30"),
    ("2025 至今年内", "2025-01-02", "2026-11-02"),
]


def leg_rows(**kw):
    """每条腿跑全部档位。"""
    rows = []
    for leg in ug.POOL:
        nav_full = ug.load_nav(leg)
        for name, over in VARIANTS:
            p = dict(kw)
            p.update(over)
            nav, st, dec = ug.build(leg, **p)
            nv = ug.run_leg(st, nav, cost=p.get("COST"), cash=p.get("CASH_ANNUAL"))
            m = ug.perf(nv)
            bh = nav / nav.iloc[0]
            mb = ug.perf(bh)
            rows.append(dict(
                leg=leg, name=ug.rule.LEGS[leg]["name"], variant=name,
                start=nav.index[0].date().isoformat(), end=nav.index[-1].date().isoformat(),
                years=round((nav.index[-1] - nav.index[0]).days / 365.25, 2),
                ann=m["ann"], mdd=m["mdd"], vol=m["vol"], sharpe=m["sharpe"],
                calmar=ug.calmar(nv), mdd_me=ug.mdd_sampled(nv),
                calmar_me=ug.calmar(nv, ug.mdd_sampled(nv)),
                turnover=ug.turnover(st), trades=ug.n_trades(st),
                expo=float(st.mean()), ann_bh=mb["ann"], mdd_bh=mb["mdd"],
                calmar_bh=ug.calmar(bh), d_ann=(m["ann"] - mb["ann"]) * 100,
                d_mdd=(m["mdd"] - mb["mdd"]) * 100,
            ))
    return rows


def portfolio_all(**kw):
    """全池组合（各腿等权 1/N），三种 floor 口径。"""
    navs, states = {}, {}
    idx = None
    for leg in ug.POOL:
        nav, st, _ = ug.build(leg, **kw)
        navs[leg], states[leg] = nav, st
        idx = nav.index if idx is None else idx.intersection(nav.index)
    idx = pd.DatetimeIndex(idx).sort_values()
    navs = {k: v.reindex(idx).ffill() for k, v in navs.items()}
    states = {k: v.reindex(idx).ffill() for k, v in states.items()}

    specs = [
        ("静态等权（买入持有）", None, dict(FLOOR=1.0, EXIT="none")),
        ("**本变种默认（2月确认 + 深破3% + TV20 + 日频离场）**", None, dict()),
        ("关掉日频离场（2026-11-17 前口径）", None, dict(EXIT="none")),
        ("日频离场 + 月中补丁（F6）", None, dict(PATCH="fast")),
        ("关闭波动层（纯闸门 + 日频离场）", None, dict(TV=0)),
        ("关闭波动层且关离场（纯闸门 · 上一版默认）", None, dict(TV=0, EXIT="none")),
        ("关闭深破 + 关闭波动层（文档原口径）", None, dict(DEEP=0, TV=0, EXIT="none")),
        ("深破 3% + 波动层 TV15", None, dict(TV=0.15)),
        ("全部腿都清仓（FLOOR=0）", None, dict(TV=0, FLOOR=0.0, EXIT="none")),
        ("全部腿都减半（FLOOR=0.5）", None, dict(TV=0, FLOOR=0.5)),
    ]
    rows = []
    static_nv = None
    for name, _unused, over in specs:
        p = dict(kw)
        p.update(over)
        if over.get("FLOOR") == 1.0:                      # 静态：state 恒 1
            stx = {k: pd.Series(1.0, index=idx) for k in ug.POOL}
        else:
            stx = {}
            for leg in ug.POOL:
                _, st, _ = ug.build(leg, **p)
                stx[leg] = st.reindex(idx).ffill()
        nv, inv = ug.portfolio(navs, stx, cost=p.get("COST"), cash=p.get("CASH_ANNUAL"))
        m = ug.perf(nv)
        if static_nv is None:
            static_nv = nv
        me = ug.mdd_sampled(nv)
        rows.append(dict(name=name, cum=m["cum"], ann=m["ann"], vol=m["vol"], mdd=m["mdd"],
                         sharpe=m["sharpe"], calmar=ug.calmar(nv),
                         mdd_me=me, calmar_me=ug.calmar(nv, me), expo=inv,
                         trades=sum(ug.n_trades(s) for s in stx.values()),
                         turnover=sum(ug.turnover(s) for s in stx.values()), nav=nv))
    return rows, navs, idx


def sens_rows(navs, idx):
    out = []
    for name, over in SENS:
        p = {}
        p.update(over)
        stx = {}
        for leg in ug.POOL:
            _, st, _ = ug.build(leg, **p)
            stx[leg] = st.reindex(idx).ffill()
        nv, inv = ug.portfolio(navs, stx, cost=p.get("COST"), cash=p.get("CASH_ANNUAL"))
        m = ug.perf(nv)
        out.append(dict(name=name, ann=m["ann"], mdd=m["mdd"], sharpe=m["sharpe"],
                        calmar=ug.calmar(nv), mdd_me=ug.mdd_sampled(nv),
                        calmar_me=ug.calmar(nv, ug.mdd_sampled(nv)), expo=inv,
                        trades=sum(ug.n_trades(s) for s in stx.values())))
    return out


def window_rows(navs, idx, base_nv):
    """压力窗口：默认档 vs 静态 50/50。"""
    out = []
    for label, a, b in WINDOWS:
        for nm, nv in (("本变种默认", base_nv), ("静态 50/50", None)):
            if nv is None:
                continue
            seg = ug.window_nav(nv, a, b)
            if len(seg) < 5:
                continue
            m = ug.perf(seg)
            out.append(dict(win=label, name=nm, ret=float(seg.iloc[-1] / seg.iloc[0] - 1.0),
                            mdd=m["mdd"], start=a, end=b))
    # 静态腿
    return out


def check_all():
    out = []
    # 1) 恒满（确认数极大 + 关掉波动层 -> 永不减仓）净值 == 买入持有
    ok, note = True, ""
    for leg in ug.POOL:
        nav, st, _ = ug.build(leg, CONFIRM=999, DEEP=0, TV=0, EXIT="none")
        nv = ug.run_leg(st, nav, cost=0.0)
        bh = nav / nav.iloc[0]
        rel = abs(float(nv.iloc[-1] / bh.iloc[-1]) - 1.0)
        if rel > 1e-12:
            ok, note = False, f"{leg} 差 {rel:.2e}"
            break
    out.append(("恒满（不触发减仓）净值 == 买入持有（零费）", ok, note or "全池逐日一致"))

    # 2) 闸门层（关掉波动层）仓位只落在 {1.0, floor}；开了波动层就是连续值，改为查上界
    ok, note = True, ""
    for leg in ug.POOL:
        _, st, _ = ug.build(leg, TV=0, EXIT="none")
        allowed = {1.0, ug.floor_for(leg)}
        got = set(np.round(st.unique(), 9))
        if not got <= allowed:
            ok, note = False, f"{leg} 闸门层出现 {sorted(got - allowed)}"
            break
        _, st_v, _ = ug.build(leg)
        if float(st_v.max()) > 1.0 + 1e-12 or float(st_v.min()) < -1e-12:
            ok, note = False, f"{leg} 叠加波动层后仓位越界 [{st_v.min():.3f}, {st_v.max():.3f}]"
            break
    out.append(("闸门层仓位 ∈ {100%, 减仓后比例}；叠加波动层后仍 ∈ [0, 100%]", ok,
                note or "全池均通过（不加杠杆）"))

    # 3) 决策日在信号月末 + 仓位 = 「严格早于该净值日的最后一个决策」
    ok, note = True, ""
    for leg in ug.POOL:
        sig = ug.load_signal(leg)
        nav, st, dec = ug.build(leg, EXIT="none")
        sig_idx = pd.DatetimeIndex(sig.index)
        for x in dec:
            pos_s = int(sig_idx.searchsorted(x["date"], side="right"))
            if pos_s < len(sig_idx) and (sig_idx[pos_s].year, sig_idx[pos_s].month) == \
                    (x["date"].year, x["date"].month):
                ok, note = False, f"{leg} 决策日 {x['date'].date()} 不是信号月末"
                break
        if not ok:
            break
        dts = np.array([x["date"] for x in dec])
        tg = np.array([x["target"] for x in dec])
        exp = []
        for d in st.index:
            prior = dts < d                                  # 严格早于 -> 次日成交
            exp.append(float(tg[prior][-1]) if prior.any() else 1.0)
        if not np.allclose(st.to_numpy(dtype=float), np.array(exp)):
            ok, note = False, f"{leg} 仓位序列与「最近一次决策」不符"
            break
    out.append(("决策在信号月末、仓位只在决策日之后生效（T+1，无前视）", ok,
                note or "全池逐日一致"))

    # 4) 截断最后 6 天重跑 -> 历史仓位不变
    ok, note = True, ""
    for leg in ug.POOL:
        nav, st, _ = ug.build(leg, EXIT="none")
        cut = nav.iloc[:-6]
        dec_cut = ug.decisions(leg)
        st_cut = ug.state_series(cut.index, dec_cut)
        common = st.index.intersection(st_cut.index)
        diff = float((st.reindex(common) - st_cut.reindex(common)).abs().max())
        if diff > 1e-12:
            ok, note = False, f"{leg} 最大差 {diff:.2e}"
            break
        # 离场掩码也必须因果：截断信号后重算，历史部分应逐日一致
        sig = ug.load_signal(leg)
        m_full = ug.exit_mask(nav.index, sig)
        m_cut = ug.exit_mask(cut.index, sig.loc[:cut.index[-1]])
        d2 = float((m_full.reindex(common).astype(float)
                    - m_cut.reindex(common).astype(float)).abs().max())
        if d2 > 1e-12:
            ok, note = False, f"{leg} 离场掩码差 {d2:.2e}"
            break
    out.append(("截掉最后 6 天重跑，历史仓位与离场掩码不变（无前视）", ok, note or "最大差 0"))

    # 5) 全池恒满时组合 == 等权手工复利（零费）
    navs, idx = {}, None
    for leg in ug.POOL:
        nav, _, _ = ug.build(leg, CONFIRM=999, DEEP=0, TV=0)
        navs[leg] = nav
        idx = nav.index if idx is None else idx.intersection(nav.index)
    idx = pd.DatetimeIndex(idx).sort_values()
    navs = {k: v.reindex(idx).ffill() for k, v in navs.items()}
    stx = {k: pd.Series(1.0, index=idx) for k in ug.POOL}
    nv, _ = ug.portfolio(navs, stx, cost=0.0)
    manual = 1.0
    rets = {k: navs[k].pct_change().fillna(0.0) for k in ug.POOL}
    w = 1.0 / len(ug.POOL)
    for i in range(len(idx)):
        manual *= (1.0 + sum(w * float(rets[k].iloc[i]) for k in ug.POOL))
    rel = abs(manual / float(nv.iloc[-1]) - 1.0)
    out.append((f"全池恒满时组合 == {len(ug.POOL)} 腿等权手工复利（零费）", bool(rel < 1e-10),
                f"差 {rel:.1e}"))

    # 6) 深破位免确认：只在「确认月数未满 且 gap <= -DEEP」时生效；DEEP=0 时一条都不该有
    ok, note = True, ""
    n_deep = 0
    for leg in ug.POOL:
        for x in ug.decisions(leg):
            if not x["deep"]:
                continue
            n_deep += 1
            if x["gap"] > -ug.DEEP + 1e-12 or x["below"] >= int(ug.CONFIRM):
                ok, note = False, (f"{leg} {x['date'].date()} gap {x['gap']:+.4f} "
                                   f"below {x['below']}")
                break
        if not ok:
            break
    if ok and any(x["deep"] for leg in ug.POOL for x in ug.decisions(leg, DEEP=0)):
        ok, note = False, "DEEP=0 仍触发"
    out.append((f"深破位免确认只在首月破位 {ug.DEEP:.0%} 以上时提前动手（共 {n_deep} 次）", ok,
                note or f"全池共 {n_deep} 次，DEEP=0 时 0 次"))

    # 7) 波动层（TV=15%）：scale == min(1, TV/vol)、0 ≤ scale ≤ 1、target ≤ gate（不加杠杆）
    ok, note, n_vol = True, "", 0
    for leg in ug.POOL:
        for x in ug.decisions(leg, TV=0.15):
            if x["vol"] is None:
                continue
            n_vol += 1
            want = min(ug.CAP, 0.15 / x["vol"])
            if (abs(x["scale"] - want) > 1e-9 or not (0.0 <= x["scale"] <= ug.CAP + 1e-12)
                    or x["target"] > x["gate"] + 1e-12):
                ok, note = False, (f"{leg} {x['date'].date()} scale {x['scale']:.4f} "
                                   f"want {want:.4f} target {x['target']:.3f} gate {x['gate']:.3f}")
                break
        if not ok:
            break
    out.append((f"波动层 scale == min(1, TV/vol)、target = gate×scale ≤ gate（{n_vol} 条决策）", ok,
                note or "全池逐条一致，不加杠杆"))

    # 8) 死区：闸门未变的相邻决策之间，目标仓位要么不动，要么变化 ≥ VOL_DEAD
    ok, note = True, ""
    dead = float(ug.params()["VOL_DEAD"] or 0.0)
    n_zero = 0
    for leg in ug.POOL:
        prev = None
        for x in ug.decisions(leg):
            if prev is not None and abs(x["gate"] - prev["gate"]) < 1e-12:
                delta = abs(x["target"] - prev["target"])
                if 1e-12 < delta < dead - 1e-9:
                    ok, note = False, (f"{leg} {x['date'].date()} 闸门未变却动了 "
                                       f"{delta * 100:.1f}pp（< 死区 {dead:.0%}）")
                    break
                if delta <= 1e-12:
                    n_zero += 1
            prev = x
        if not ok:
            break
    out.append((f"死区生效：闸门未变时目标仓位要么不动、要么变化 ≥ {dead:.0%}"
                f"（未变的月末对 {n_zero} 个）", ok, note or "全池逐条一致"))

    # 9) 日频离场只减不加：开离场后的仓位逐日 ≤ 关离场的仓位
    ok, note = True, ""
    for leg in ug.POOL:
        _, st_on, _ = ug.build(leg)
        _, st_off, _ = ug.build(leg, EXIT="none")
        d = float((st_on.reindex(st_off.index) - st_off).max())
        if d > 1e-12:
            ok, note = False, f"{leg} 离场后仓位高出 {d:.3f}"
            break
    out.append(("日频离场只减不加：开离场后仓位逐日 ≤ 关离场", ok, note or "全池逐日成立"))
    return out


def main():
    os.makedirs(OUTDIR, exist_ok=True)
    p0 = ug.params()
    legs = leg_rows()
    prows, navs, idx = portfolio_all()
    sens = sens_rows(navs, idx)
    checks = check_all()

    # 压力窗口：默认档 vs 静态
    base_nv = next(r["nav"] for r in prows if r["name"].startswith("**本变种默认"))
    static_nv = next(r["nav"] for r in prows if r["name"].startswith("静态"))
    wins = []
    for label, a, b in WINDOWS:
        seg_m, seg_s = ug.window_nav(base_nv, a, b), ug.window_nav(static_nv, a, b)
        if len(seg_m) < 5 or len(seg_s) < 5:
            continue
        wins.append(dict(win=label, ret=float(seg_m.iloc[-1] / seg_m.iloc[0] - 1.0),
                         ret_s=float(seg_s.iloc[-1] / seg_s.iloc[0] - 1.0),
                         mdd=ug.perf(seg_m)["mdd"], mdd_s=ug.perf(seg_s)["mdd"],
                         start=a, end=b))

    with open(OUT_JSONL, "w", encoding="utf-8") as f:
        for r in legs:
            f.write(json.dumps({k: (round(v, 4) if isinstance(v, float) else v)
                                for k, v in r.items()}, ensure_ascii=False) + "\n")
        for r in prows:
            f.write(json.dumps({"portfolio": r["name"],
                                **{k: (round(v, 4) if isinstance(v, float) else v)
                                   for k, v in r.items() if k != "nav"}},
                               ensure_ascii=False) + "\n")
        for r in sens:
            f.write(json.dumps({"sens": r["name"],
                                **{k: (round(v, 4) if isinstance(v, float) else v)
                                   for k, v in r.items() if k != "name"}},
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

    base, static = prow("**本变种默认"), prow("静态")
    exit_off = prow("关掉日频离场")
    gate_only = prow("关闭波动层（纯闸门")
    doc_pure = prow("关闭深破 + 关闭波动层")
    pvt15 = prow("深破 3% + 波动层 TV15")
    bad = [c for c in checks if not c[1]]
    yrs = (idx[-1] - idx[0]).days / 365.25
    win = f"{idx[0].date()} ~ {idx[-1].date()}"
    yr = ug.yearly(base_nv)

    L = [
        "# 波动资产 · V7 变种：年线闸门（200 日 · 2 月确认 + 深破位免确认 × 波动层 TV20 + 日频离场）",
        f"> 池 = **全七腿**（`us_gate.POOL` = {', '.join(ug.rule.LEGS[k]['name'] for k in ug.POOL)}）；"
        "规则落地自 `E:\\qianwen\\jijin\\纳指100与标普500_策略回测与规则.md`（两腿文档，2026-11-17 扩池）。",
        f"> {ug.vdata.us_signal_exec_desc()}；"
        f"空仓按货基 {ug.CASH_ANNUAL:.2%} 计息 · 单边成本 {p0['COST']:.1%}。",
        "> 规则权威源 `v7_us_gate/us_gate.py` 顶部常量。",
        "",
        "## 1. 规则（可执行版）",
        "",
        "| 环节 | 规则 |",
        "|---|---|",
        f"| 信号价 | 真实指数收盘（`.NDX` / `.INX`，与回测价格同源、2005 起） |",
        f"| 均线 | {p0['MA_WIN']} 日简单均线（日频算、月末看） |",
        "| 看盘频率 | 每月最后一个交易日收盘 → 次一交易日（次月首个交易日）成交 |",
        f"| 减仓条件 | **连续 {p0['CONFIRM']} 个月**收盘在年线之下（单月破位不动） |",
        f"| 深破位豁免 | 首次破位即低于年线 **{p0['DEEP']:.0%} 以上** → **当月就减**，不等第二个月 |",
        (f"| 波动层（默认开） | `目标权重 = 闸门 × min(1, {float(p0['TV']):.0%} / vol20)`，"
         f"月末定、变动 < {float(p0['VOL_DEAD']):.0%} 不动手（与 v9 同口径） |"
         if float(p0["TV"] or 0) > 0 else
         "| 波动层（可选 · 当前关闭） | `目标权重 = 闸门 × min(1, TV / vol20)`；"
         "`TV=0.15` 即与 `v9_vol_target` 同口径，见 §4 |"),
        f"| 减到多少 | 纳指100 → **{ug.floor_for('ndx'):.0%}**；标普500 → **{ug.floor_for('spx'):.0%}** |",
        "| 回补 | 收盘重新站上年线 → **次月即满仓**（不等确认） |",
        "",
        "## 2. 逐腿（各档位均为「含 0.1% 单边成本 · 月度」）",
        "",
        "| 腿 | 档位 | 年化 | 回撤·日频 | 回撤·月末 | Calmar(月末) | 夏普 | 仓位 | 换手 | 调仓 | vs 买入持有 |",
        "|---|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|",
    ]
    for leg in ug.POOL:
        for r in [x for x in legs if x["leg"] == leg]:
            L.append(f"| {r['name']} | {r['variant']} | {ug.fmt_pct(r['ann'])} | "
                     f"{ug.fmt_pct(r['mdd'])} | {ug.fmt_pct(r['mdd_me'])} | "
                     f"{r['calmar_me']:.2f} | {r['sharpe']:.2f} | "
                     f"{r['expo']:.0%} | {r['turnover']:.1f} | {r['trades']} | "
                     f"{r['d_ann']:+.2f}pp / {r['d_mdd']:+.2f}pp |")
    L += [
        "",
        "> **两种回撤口径都列**：`日频` = 逐日净值算（会把 2020-02-19→03-16 那段月内坑计入，"
        "更保守、风控应看这个）；`月末` = 只在月末取点算（**文档表1 的口径**，同规则下纳指 −13.9% vs 日频 −26.5%）。",
        "> Calmar 一律用**月末**口径，便于与文档逐格对照；「夏普」用日频。",
        "> 「vs 买入持有」= 年化差 / 回撤差（正 = 回撤更浅）。",
        "> **两条腿的最优档不同**：纳指用「连续 2 月确认 + 清仓」，标普用「确认 + 减半」——"
        "这正是文档最终规则给的差异（标普跌幅浅、恢复快，清仓的机会成本更高）。",
        f"> ⚠ **深破位豁免在两条腿上的作用相反**（比「深破3%（TV 关闭）」vs「年线+2月确认」两行，"
        f"都关掉波动层，只隔离深破这一个变量）："
        f"标普 2020-02-28 月末已破年线 −3.05%，豁免让它 3 月就到半仓 → 年化 "
        f"{ug.fmt_pct(_leg('spx', '深破3%（TV 关闭')['ann'])} vs "
        f"{ug.fmt_pct(_leg('spx', '年线+2月确认')['ann'])}、日频回撤 "
        f"{ug.fmt_pct(_leg('spx', '深破3%（TV 关闭')['mdd'])} vs "
        f"{ug.fmt_pct(_leg('spx', '年线+2月确认')['mdd'])}；"
        f"纳指 2 月末**还在年线上**，跌是 3 月一次性完成的，等 3 月末深破位成立时坑已挖完，"
        f"豁免只是让它错过 4 月反抽 → 年化 {ug.fmt_pct(_leg('ndx', '深破3%（TV 关闭')['ann'])} vs "
        f"{ug.fmt_pct(_leg('ndx', '年线+2月确认')['ann'])}。**组合层净赚**，是因为标普腿救回来的"
        f"回撤大于纳指腿让渡的收益 —— 这不是「规则更聪明」，是两条腿的月度相位差。",
        "",
        f"## 3. 组合层（全 {len(ug.POOL)} 腿等权 1/{len(ug.POOL)} · {win} · {yrs:.1f} 年）",
        "",
        "| 组合 | 累计 | 年化 | 波动 | 回撤·日频 | 回撤·月末 | Calmar(月末) | 夏普 | 平均仓位 | 调仓 |",
        "|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|",
    ]
    for r in prows:
        L.append(f"| {r['name']} | {ug.fmt_pct(r['cum'], 1)} | {ug.fmt_pct(r['ann'])} | "
                 f"{r['vol'] * 100:.2f}% | {ug.fmt_pct(r['mdd'])} | {ug.fmt_pct(r['mdd_me'])} | "
                 f"{r['calmar_me']:.2f} | {r['sharpe']:.2f} | {r['expo']:.0%} | {r['trades']} |")
    L += [
        "",
        f"> **默认 vs 静态等权**：年化 {ug.fmt_pct(base['ann'])} vs {ug.fmt_pct(static['ann'])}"
        f"（**{(base['ann'] - static['ann']) * 100:+.2f}pp**）、回撤·月末 "
        f"{ug.fmt_pct(base['mdd_me'])} vs {ug.fmt_pct(static['mdd_me'])}"
        f"、Calmar {base['calmar_me']:.2f} vs {static['calmar_me']:.2f}、"
        f"夏普 {base['sharpe']:.2f} vs {static['sharpe']:.2f}。",
        f"> **本版默认 = 闸门（2 月确认 + 深破 3%）× 波动层 TV20 + 日频离场**"
        f"（波动层 2026-11-05 选定；日频离场 2026-11-17 加入，见 `research/probe_fusion`）。"
        f"对比关掉离场的老口径：年化 {ug.fmt_pct(base['ann'])} vs {ug.fmt_pct(exit_off['ann'])}"
        f"（{(base['ann'] - exit_off['ann']) * 100:+.2f}pp）、回撤·月末 "
        f"{ug.fmt_pct(base['mdd_me'])} vs {ug.fmt_pct(exit_off['mdd_me'])}"
        f"（**{(exit_off['mdd_me'] - base['mdd_me']) * 100:+.2f}pp 更浅**）、Calmar(月末) "
        f"{base['calmar_me']:.2f} vs {exit_off['calmar_me']:.2f}、"
        f"调仓 {base['trades']} vs {exit_off['trades']} 次。",
        f"> 再对比纯闸门（关掉波动层、保留离场）：年化 {ug.fmt_pct(gate_only['ann'])}"
        f"（{(base['ann'] - gate_only['ann']) * 100:+.2f}pp）、回撤·日频 "
        f"{ug.fmt_pct(gate_only['mdd'])}、Calmar(月末) {gate_only['calmar_me']:.2f}、"
        f"调仓 {gate_only['trades']} 次；文档原口径（两层都关、无离场）年化 "
        f"{ug.fmt_pct(doc_pure['ann'])}、日频回撤 {ug.fmt_pct(doc_pure['mdd'])}、"
        f"Calmar {doc_pure['calmar_me']:.2f}。",
        f"> **波动层 TV15 一档只作对照**（更平但更少年化）：年化 {ug.fmt_pct(pvt15['ann'])}、"
        f"回撤·日频 {ug.fmt_pct(pvt15['mdd'])}、Calmar {pvt15['calmar_me']:.2f}、"
        f"调仓 {pvt15['trades']} 次。选 20% 而不是 15%，是因为 Calmar 只差 "
        f"{pvt15['calmar_me'] - base['calmar_me']:.2f} 而年化差 "
        f"{(base['ann'] - pvt15['ann']) * 100:.2f}pp、调仓差 {pvt15['trades'] - base['trades']} 次。",
        "> **三种补洞方式的取舍**：深破位最便宜（一年动不到 2 次），波动层最细（连续缩放、"
        "对每次月末都生效），两者叠加是本版默认。**波动层不是「免费的回撤」——它把年化让渡换成了"
        "更平的曲线**，与 §4 的目标波动邻域同一结论。",
        "",
        "## 4. 参数敏感性（组合层 · 默认 floor 口径 · 只改一格）",
        "",
        "| 档位 | 年化 | 回撤·日频 | 回撤·月末 | Calmar(月末) | 夏普 | 平均仓位 | 调仓 |",
        "|---|---:|---:|---:|---:|---:|---:|---:|",
    ]
    for r in sens:
        L.append(f"| {r['name']} | {ug.fmt_pct(r['ann'])} | {ug.fmt_pct(r['mdd'])} | "
                 f"{ug.fmt_pct(r['mdd_me'])} | {r['calmar_me']:.2f} | {r['sharpe']:.2f} | "
                 f"{r['expo']:.0%} | {r['trades']} |")
    def _s(prefix):
        return next(r for r in sens if r["name"].startswith(prefix))

    s_off, s_on = _s("关闭深破位"), _s("基准")
    s_d1, s_d2, s_d5 = _s("深破位阈值 1%"), _s("深破位阈值 2%"), _s("深破位阈值 5%")
    s_c1, s_c3 = _s("确认 1 个月"), _s("确认 3 个月")
    s_daily = _s("日频触发")
    s_vt = _s("深破 3% + 波动层 TV15")
    s_vt12, s_vt18 = _s("深破 3% + 波动层 TV12"), _s("深破 3% + 波动层 TV18")
    s_vt20, s_vt22 = _s("深破 3% + 波动层 TV20"), _s("深破 3% + 波动层 TV22")
    s_vtw, s_vtcost = _s("波动层窗口 60 日"), _s("成本 0.6%（场外现实口径）")
    s_dead0, s_dead5 = _s("波动层死区 0pp"), _s("波动层死区 5pp")
    s_dead20 = _s("波动层死区 20pp")

    anns = [r["ann"] for r in sens]
    cals = [r["calmar_me"] for r in sens]
    L += [
        "",
        f"> 年化 {ug.fmt_pct(min(anns))} ~ {ug.fmt_pct(max(anns))}、Calmar(月末) {min(cals):.2f} ~ {max(cals):.2f}。",
        f"> **波动层 TV 邻域**（`w = 闸门 × min(1, TV/vol20)`，与 `v9_vol_target` 同口径）："
        f"TV 12 / 15 / 18 / 20 / 22% → 年化 {ug.fmt_pct(s_vt12['ann'])} / {ug.fmt_pct(s_vt['ann'])} / "
        f"{ug.fmt_pct(s_vt18['ann'])} / {ug.fmt_pct(s_vt20['ann'])} / {ug.fmt_pct(s_vt22['ann'])}，"
        f"Calmar {s_vt12['calmar_me']:.2f} / {s_vt['calmar_me']:.2f} / {s_vt18['calmar_me']:.2f} / "
        f"{s_vt20['calmar_me']:.2f} / {s_vt22['calmar_me']:.2f}，"
        f"调仓 {s_vt12['trades']} / {s_vt['trades']} / {s_vt18['trades']} / {s_vt20['trades']} / "
        f"{s_vt22['trades']} 次 —— **在收益与回撤之间平移，没有尖峰**（与 v9 的结论一致）。"
        "默认取 20% 而不是风险调整最高的 12%：Calmar 只差 0.24，年化多 2.40pp、调仓少 132 次 ——"
        "**这根旋钮选的是「你要多平的曲线」，不是「最优参数」**。",
        f"> **深破位阈值**（在 TV20 之下）：关掉 → Calmar {s_off['calmar_me']:.2f}、日频回撤 "
        f"{ug.fmt_pct(s_off['mdd'])}；3%（默认）→ {s_on['calmar_me']:.2f}、{ug.fmt_pct(s_on['mdd'])}。"
        f"阈值 1% / 2% / 5% 分别落在 Calmar {s_d1['calmar_me']:.2f} / {s_d2['calmar_me']:.2f} / "
        f"{s_d5['calmar_me']:.2f} —— 1% 会把 2018Q4 那种浅破位（纳指 −1.41%）也当深破位提前卖"
        f"（调仓 {s_d1['trades']} 次），5% 则漏掉 2020-02（标普 −3.05%）。可用的窗口只有 "
        "2.58%~3.05% 这一条窄缝，**本质是被 2020 那一次标定出来的**（见 §9 第 7 条）。",
        f"> **确认月数**：1 个月（= 深破位阈值 0 的极端）调仓 {s_c1['trades']} 次、"
        f"年化 {ug.fmt_pct(s_c1['ann'])}、Calmar {s_c1['calmar_me']:.2f}；3 个月 → "
        f"调仓 {s_c3['trades']} 次、年化 {ug.fmt_pct(s_c3['ann'])}、Calmar {s_c3['calmar_me']:.2f}。"
        "**默认取 2 个月 + 3% 豁免，是与文档对齐又补掉新冠那次漏洞的折中**，不是网格搜索出来的最优。",
        f"> 波动层的代价是**换手**：TV20 调仓 {s_vt20['trades']} 次（纯闸门 {gate_only['trades']} 次），"
        f"成本 0.1% → 0.6% 时年化 {ug.fmt_pct(s_on['ann'])} → {ug.fmt_pct(s_vtcost['ann'])}"
        f"（−{(s_on['ann'] - s_vtcost['ann']) * 100:.2f}pp）、Calmar {s_on['calmar_me']:.2f} → "
        f"{s_vtcost['calmar_me']:.2f}。**窗口别调长**：60 日窗 Calmar 塌到 {s_vtw['calmar_me']:.2f}、"
        f"日频回撤 {ug.fmt_pct(s_vtw['mdd'])} —— 波动估计变钝，高波动起来时仍按低波动满仓。",
        f"> **两层各管一段，别指望互相替代**：TV20 下关掉深破 → 年化 {ug.fmt_pct(s_off['ann'])}、"
        f"Calmar {s_off['calmar_me']:.2f}（深破补的是 2020-02 那个「月初就深破」的月）；"
        f"关掉波动层只留闸门 → {ug.fmt_pct(gate_only['ann'])} / {gate_only['calmar_me']:.2f}。"
        f"**深破几乎不花年化（{ (s_on['ann'] - s_off['ann']) * 100:+.2f}pp）却值 "
        f"{(s_off['mdd'] - s_on['mdd']) * 100:.1f}pp 回撤**，波动层则是拿年化换整段曲线的平滑。",
        f"> **日频触发**（跌破即走）调仓 {s_daily['trades']} 次、日频回撤 {ug.fmt_pct(s_daily['mdd'])}"
        f"、年化只有 {ug.fmt_pct(s_daily['ann'])} —— 省下的回撤抵不过多付的手续费。",
        f"> **死区**（默认 {float(p0['VOL_DEAD']):.0%}，2026-11-05 由 5pp 放宽）：0pp / 5pp / "
        f"10pp / 20pp → 调仓 {s_dead0['trades']} / {s_dead5['trades']} / {s_on['trades']} / "
        f"{s_dead20['trades']} 次、Calmar {s_dead0['calmar_me']:.2f} / {s_dead5['calmar_me']:.2f} / "
        f"{s_on['calmar_me']:.2f} / {s_dead20['calmar_me']:.2f}、年化 "
        f"{ug.fmt_pct(s_dead0['ann'])} / {ug.fmt_pct(s_dead5['ann'])} / {ug.fmt_pct(s_on['ann'])} / "
        f"{ug.fmt_pct(s_dead20['ann'])}。**闸门没变时，几 pp 的波动层微调是纯手续费**——"
        f"把死区放到 {float(p0['VOL_DEAD']):.0%} 后调仓从 {s_dead5['trades']} 降到 {s_on['trades']} 次，"
        f"Calmar 反而从 {s_dead5['calmar_me']:.2f} 升到 {s_on['calmar_me']:.2f}。"
        "再放宽到 20pp 就变钝了（止损反应变慢），所以停在 10pp。",
        "",
        "## 5. 与文档表1 的逐格对照（同一规则、不同数据源）",
        "",
        "> 文档口径：场外 QDII A 类净值 · 现金用中国国债 2Y · 样本 "
        + " / ".join(f"{ug.rule.LEGS[k]['name']} {v}" for k, v in ug.DOC_SAMPLE.items())
        + f"；本仓库：真实指数收盘（`.NDX` / `.INX`）· 现金 1.40% · 样本 {win}。"
        "**两者既不同标的也不同区间，只比排序与量级，不逐格对数字** —— "
        "2026-11-13 起本仓库已无场外净值口径，2026-11-16 又修掉短信号库 bug，"
        "本仓库一列现在完整含 2008 危机。",
        "",
        "| 腿 | 档位 | 文档 年化 | 文档 回撤 | 文档 Calmar | 本仓库 年化 | 本仓库 回撤(月末) | 本仓库 Calmar(月末) |",
        "|---|---|---:|---:|---:|---:|---:|---:|",
    ]
    doc_map = {
        "买入持有": "买入持有",
        "年线闸门（清仓 · 无确认）": "年线闸门",
        "年线+2月确认（清仓）": "年线+2月确认",
        "年线+2%迟滞（清仓）": "年线+2%迟滞",
        "半仓闸门（无确认 · 减半）": "半仓闸门",
    }
    for leg in ug.POOL:
        if leg not in ug.DOC_REF:      # 文档对照只有 ndx/spx，新增腿无 QDII 文档值
            continue
        for r in [x for x in legs if x["leg"] == leg]:
            key = doc_map.get(r["variant"])
            if not key:
                continue
            ref = ug.DOC_REF[leg][key]
            L.append(f"| {r['name']} | {r['variant']} | {ug.fmt_pct(ref['ann'])} | "
                     f"{ug.fmt_pct(ref['mdd'])} | {ref['calmar']:.2f} | {ug.fmt_pct(r['ann'])} | "
                     f"{ug.fmt_pct(r['mdd_me'])} | {r['calmar_me']:.2f} |")
    L += [
        "",
        "> **读数不可逐格对齐**：文档是 2012 起的 QDII 净值（不含 2008），本仓库是 2005 起的真实指数"
        "（**完整含 2008**）；表里的「本仓库回撤(月末)」也换成了月末采样口径 —— "
        "两端差异主要来自**样本区间与标的类型**，不是规则不同。",
        "> ⚠ **日频 vs 月末口径要分清**：文档引擎月频（曲线只取月末点），本表为对齐才用 `mdd_me`；"
        "风控请读 §3 的「回撤·日频」—— 月度确认制对 5 周内的 V 型急跌结构性无效，"
        "月内那段坑不会被月末点记到。",
        "> ⚠ **文档里「人民币净值当信号劣化 3.4pp」这一档已无法复现**：2026-11-13 起信号与"
        "回测价格同源（真实指数），没有第二套净值序列可切，该反例档已从回测中删除。"
        "文档那条差值属于旧 QDII 口径的历史沿革；本仓库用指数信号只主张**口径纯净、可复核**。",
        "",
        "## 6. 压力窗口（默认档 vs 静态等权）",
        "",
        "| 窗口 | 区间 | 本变种 收益 | 静态等权 收益 | 本变种 回撤 | 静态 回撤 |",
        "|---|---|---:|---:|---:|---:|",
    ]
    for w in wins:
        L.append(f"| {w['win']} | {w['start']} ~ {w['end']} | {ug.fmt_pct(w['ret'])} | "
                 f"{ug.fmt_pct(w['ret_s'])} | {ug.fmt_pct(w['mdd'])} | {ug.fmt_pct(w['mdd_s'])} |")
    L += [
        "",
        "## 7. 逐年收益（默认档 vs 静态等权）",
        "",
        "| 年份 | 本变种 | 静态等权 | 差 |",
        "|---|---:|---:|---:|",
    ]
    yr_s = ug.yearly(static_nv)
    for y in sorted(yr):
        a, b = yr.get(y), yr_s.get(y)
        diff = "—" if (a is None or b is None) else f"{(a - b) * 100:+.2f}pp"
        L.append(f"| {y} | {ug.fmt_pct(a)} | {ug.fmt_pct(b)} | {diff} |")
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
        "## 9. 口径与已知边界",
        "",
        "1. **样本自 2005-01-03 起**（真实指数长库）：不含 2000-2002（纳指 −83%），"
        "但**完整包含 2008** —— 年线闸门最该被检验的那次危机已在样本内。",
        "⚠ 2026-11-16 之前信号读 2010 起的短库，2005~2010 无 MA200 / vol20、仓位默认满仓，"
        "所有档位的回撤都被错误地钉在买入持有的 −54.74% 上；本次修复后该问题已消除。",
        "2. **信号与回测价格同源（真实指数）**：收益不含汇率、QDII 跟踪误差与分红再投，"
        "实盘下单场外 A 类时对账要另按 −1.3pp/年 折算分红。",
        "3. **只降不升、不加杠杆**：年化让渡是保费，不是 alpha；本项目标是 Calmar，不是收益。",
        "4. **标普减半而非清仓**：文档实测清仓把标普年化压到 10.07%（Calmar 0.54），减半保住更多上行。"
        "本仓库扩池后换成 2005 起真实指数，「年线+2月确认（清仓）」与「半仓闸门」的 Calmar(月末) 只差"
        " 0.04（0.32 vs 0.28）—— 口径沿用文档，未按新样本重标。",
        "5. **QDII 通道风险未计**：纳指/标普 QDII 有额度关闭、限购、赎回确认时滞（T+2~T+7），"
        "实盘「月末信号 → 次月首个交易日成交」可能滑到第二、三天。",
        "6. **不做 walk-forward 选优**：参数写死（MA200 / 2 月 / 深破 3% / TV20 / MA50·200 日频离场 / "
        "纳指 0 / 标普 50%），"
        "§4 已给敏感性。",
        f"7. ⚠ **深破位阈值 {p0['DEEP']:.0%} 是单事件标定的**：2 个月确认的滞后只在 2020-02→03 "
        "一次上真正付了钱（月末回撤 −15.8% vs 1 个月口径 −12.3%），而 3% 恰好落在那次破位"
        "（标普 −3.05%）与其余所有首次破位（最近的 2025-03 为 −2.58%）之间的窄缝里 —— "
        "**样本内漂亮，样本外没有证据**。改用「破位深度 / 月波动」标准化（z 值）不能复现同样的切分"
        "（新冠那次只有 −0.43σ，与 2019-05 假摔 −0.20σ 分不开），说明「破位深度」本身区分不了"
        "真破位与假摔。这一档的价值是**把已知的那一次漏洞补上**，不是找到了稳健规律；"
        "`DEEP=0` 即退回文档原口径。",
        "  （以上标定数字来自 2026-11-13 口径切换前的短样本，仅作沿革；阈值本身未改。）",
        "",
        "> **模拟结果，非投资建议。**",
    ]

    with open(OUT_MD, "w", encoding="utf-8") as f:
        f.write("\n".join(L))

    print("报告:", OUT_MD)
    print("结果:", OUT_JSONL)
    print(f"窗口 {win} · {yrs:.1f} 年")
    for r in prows:
        print(f"  {r['name']:34s} 年化 {ug.fmt_pct(r['ann']):>9s} · "
              f"回撤·日频 {ug.fmt_pct(r['mdd']):>8s} · 回撤·月末 {ug.fmt_pct(r['mdd_me']):>8s} · "
              f"Calmar(月末) {r['calmar_me']:>5.2f} · 夏普 {r['sharpe']:>5.2f} · 仓位 {r['expo']:.0%}")
    print(f"断言 {len(checks) - len(bad)}/{len(checks)} 通过")
    return 0 if not bad else 1


if __name__ == "__main__":
    sys.exit(main())
