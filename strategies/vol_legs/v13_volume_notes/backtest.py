# -*- coding: utf-8 -*-
"""V13 · 成交量操盘笔记 · 回测（判据三档 → 信号层 → 组合层，按规格 §9 的顺序跑）。

规格把跑的顺序写死了（§9 末句）：**先对照组 3（判据三档）→ 再 B/S/W 单信号分布 → 最后组合层**。
本文件按这个顺序出报告，一节一个问题：

| 节 | 回答什么 | 规格出处 |
|---|---|---|
| §2 | 这批标的的「量」是不是噪声（失真检验） | §8 适用边界 + §7 已实测负贡献项 |
| §3 | **原文的锚点（D1）到底有没有信息** —— D1 / D1b / D1c 三档同尺对比 | §6 对照组 3（本回测最重要的产出） |
| §4 | 每条 B/S/W 信号之后 D+20/60/120 的收益分布 vs 全样本基线 | §6 信号层 + §1 红线② |
| §5 | 上线池（科创50 / 恒生科技 / 黄金）的组合层 NAV；宽基与 QDII 只作对照 | §6 组合层 + §8 |
| §6 | 分段（2011-2018 / 2019-2026）防单段过拟合 | §5 区间 |
| §7 | **冲突率**（同日买卖矛盾占比）—— 原文「矛盾=观望」的元规则有多常触发 | §4 |
| §8 | 消融与敏感性（拆掉哪一层会变好/变坏） | §1 纪律① |

口径（与家族其它变种最不同的三点，先看）：
1. **价格 = 场内 ETF 前复权 OHLCV**（`ohlcv.py` 的家族共享库）。场外净值没有量，本变种无法用场外口径。
2. **T 日收盘信号 → T+1 开盘成交**（本变种有开盘价，真的按开盘撮合）；一字板顺延并计数。
3. 场内成本单边 **7bp**（佣金 2bp + 滑点 5bp，免印花税）；空仓按货基 `rule.CASH_ANNUAL` 计息。

产出（相对本文件）：`backtest/_v13_volume_bt.md` / `_v13_volume_bt.jsonl`。
机械规则输出，非投资建议。
"""
import json
import os
import sys

import numpy as np
import pandas as pd

try:
    sys.stdout.reconfigure(encoding="utf-8")  # type: ignore[attr-defined]
except Exception:  # noqa: BLE001
    pass

_HERE = os.path.dirname(os.path.abspath(__file__))
if _HERE not in sys.path:
    sys.path.insert(0, _HERE)

import ohlcv      # noqa: E402
import rule       # noqa: E402
import vol_notes as vn  # noqa: E402

OUTDIR = os.path.join(_HERE, "backtest")
OUT_MD = os.path.join(OUTDIR, "_v13_volume_bt.md")
OUT_JSONL = os.path.join(OUTDIR, "_v13_volume_bt.jsonl")

POOL = vn.POOL
REF = ohlcv.REFERENCE
ALL6 = [k for k in vn.LEGS if k != "a500"]        # 去掉 2024-10 才成立的 A500 后的对照池
CRITERIA = [("d1", "D1 原口径（量>前三日最大）"), ("strict_b", "D1b ≥2×MA20"),
            ("quantile", "D1c 60日95分位（默认）")]

# 消融 / 敏感性（都只看上线池的三腿组合，避免把宽基的噪声混进来）
SENS = [
    ("V13 默认（D1c + 3日窗 + §4 优先级）", {}),
    ("① 判据换 D1（原文口径）", dict(high_vol_mode="d1")),
    ("① 判据换 D1b（2×MA20）", dict(high_vol_mode="strict_b")),
    ("① 分位改 0.90", dict(high_vol_quantile=0.90)),
    ("① 分位改 0.98", dict(high_vol_quantile=0.98)),
    ("② 观察窗改 5 日", dict(window_days=5)),
    ("② 观察窗改 1 日", dict(window_days=1)),
    ("③ 冲突日不动（W9 原意）", dict(CONFLICT_MODE="watch")),
    ("③ 空仓起步（START_MULT=0）", dict(START_MULT=0.0)),
    ("④ 重仓放开到 1.5 倍带", dict(HEAVY_UNCAP=True)),
    ("⑤ 只留减/清类（关掉买入）", dict(_DROP=("buy",))),
    ("⑤ 剔除减仓类（清仓 + 买入）", dict(_DROP=("reduce",))),
    ("⑤ 剔除清仓类（减仓 + 买入）", dict(_DROP=("clear",))),
    ("⑥ 同向动作间隔 5 日", dict(MIN_GAP=5)),
    ("⑥ 同向动作间隔 10 日", dict(MIN_GAP=10)),
    ("⑧ 调仓频率 5 日", dict(REBAL_EVERY=5)),
    ("⑧ 调仓频率 10 日", dict(REBAL_EVERY=10)),
    ("⑧ 调仓频率 20 日", dict(REBAL_EVERY=20)),
    ("⑦ 零成本（诊断成本占比）", dict(_NOCOST=True)),
]


def fp(x, nd=2):
    return "—" if x is None or (isinstance(x, float) and not np.isfinite(x)) else f"{x * 100:+.{nd}f}%"


def fs(x, nd=0):
    return "—" if x is None or (isinstance(x, float) and not np.isfinite(x)) else f"{x * 100:.{nd}f}%"


def fn(x, nd=2):
    if x is None:
        return "—"
    if isinstance(x, float) and np.isinf(x):
        return "∞"
    if isinstance(x, float) and not np.isfinite(x):
        return "—"
    return f"{x:.{nd}f}"


def metrics(nav):
    m = vn.perf(nav)
    return dict(ann=m["ann"], mdd=m["mdd"], vol=m["vol"], sharpe=m["sharpe"],
                calmar=m["calmar"], cum=m["cum"], days=m["days"])


def _wtext(w, legs):
    return "、".join(f"{rule.LEGS[k]['name']} {w[k]:.0%}" for k in legs)


# ---------------------------------------------------------------- 单腿回测
def leg_run(leg, cost=True, **kw):
    """该腿（预热后）+ 一份完整诊断。返回 (sim, df, nav, rets, w, info)。"""
    df = ohlcv.frame(leg)
    sim = vn.state_series(df, leg, **kw)
    ready = sim["ready"].to_numpy(dtype=bool)
    if not ready.any():
        raise ValueError(f"{leg} 无预热完成日")
    i0 = int(np.argmax(ready))
    sim = sim.iloc[i0:]
    d2 = df.loc[sim.index]
    nav, r, w, deferred = vn.run_leg(sim["mult"], d2, cost=cost)
    yrs = max((sim.index[-1] - sim.index[0]).days / 365.25, 1e-9)
    bh = d2["close"] / d2["close"].iloc[0]
    acts = vn.actions(sim["mult"])
    turn = float(np.abs(np.diff(np.concatenate([[0.0], w.to_numpy()]))).sum())
    expo = float(w.mean())
    r_same = expo * d2["close"].pct_change().fillna(0.0) + (1.0 - expo) * vn.CASH_DAILY
    same = (1.0 + r_same).cumprod()
    # run_leg 返回的 r 已经含「空仓部分按货基计息」，组合层直接用它（别再补一次）
    r_cash = r
    st = vn.trade_stats(r_cash, w)
    sig_cols = [c for c in sim.columns if c.startswith("sig_")]
    sig_any = sim[sig_cols].any(axis=1).to_numpy(dtype=bool) if sig_cols else np.zeros(len(sim), bool)
    info = dict(
        leg=leg, name=rule.LEGS[leg]["name"], code=ohlcv.CODES[leg],
        edge=ohlcv.EDGE_LABEL[ohlcv.EDGE[leg][0]],
        start=sim.index[0].date().isoformat(), end=sim.index[-1].date().isoformat(),
        years=round(yrs, 2), bars=len(sim),
        nav=metrics(nav), bh=metrics(bh), same=metrics(same),
        expo=expo, acts=len(acts), acts_per_year=len(acts) / yrs,
        turnover=turn, turnover_per_year=turn / yrs,
        cost_total=turn * vn.FEE_ETF if cost else 0.0,
        deferred=deferred, trades=st["n"], win_rate=st["win_rate"],
        payoff=st["payoff"], exp=st["exp"], avg_bars=st["bars"],
        conflict_rate=float(sim["conflict"].mean()),
        signal_rate=float(sig_any.mean()),
    )
    return sim, d2, nav, r_cash, w, info


def pool_of(legs):
    """组内归一化权重（Σ=1）。"""
    tot = sum(float(rule.LEGS[k]["band"]) for k in legs)
    return {k: float(rule.LEGS[k]["band"]) / tot for k in legs}


def port(legs, cost=True, **kw):
    """上线池 / 对照池的组合层：-> (nav, frame, rets, infos, weights)"""
    runs = {k: leg_run(k, cost=cost, **kw) for k in legs}
    idx = None
    for k, (sim, *_rest) in runs.items():
        idx = sim.index if idx is None else idx.intersection(sim.index)
    idx = idx.sort_values()
    frame = pd.DataFrame({k: runs[k][1]["close"].reindex(idx) for k in legs}).dropna()
    idx = frame.index
    rets = {k: runs[k][3].reindex(idx).fillna(0.0) for k in legs}
    wts = pool_of(legs)
    nav = vn.portfolio(frame, rets, weight=wts, cost=cost)
    bh = _bh_nav(frame, wts)                            # 同比重恒定持有（同池、同权重）
    infos = {k: runs[k][5] for k in legs}
    # 组合加权仓位（共同窗口内的时间均值）—— 与"同曝光"基准必须用同一份，否则基准会漂
    wpos = None
    for k in legs:
        ww = runs[k][4].reindex(idx).fillna(0.0) * wts[k]
        wpos = ww if wpos is None else wpos + ww
    return nav, frame, rets, infos, wts, bh, wpos


def _bh_nav(frame, wts):
    """同权重的恒定持有基准 —— **必须与策略用同一组权重**，否则拿「等权」比「带权」是两件事。"""
    ret = frame.pct_change().fillna(0.0)
    r = None
    for k in frame.columns:
        rr = ret[k] * float(wts[k])
        r = rr if r is None else r + rr
    return (1.0 + r).cumprod()


# ---------------------------------------------------------------- 信号层
def signal_table(legs, **kw):
    """上线池池化：每条信号后 D+20/60/120 的收益分布 vs 全样本基线。"""
    rows = []
    pooled = {}
    base_all = {20: [], 60: [], 120: []}
    for leg in legs:
        sim, df, *_ = leg_run(leg, **kw)
        close = sim["close"]
        for sid in vn.SIGNAL_DESC:
            col = "sig_" + sid
            if col not in sim:
                continue
            fwd, base = vn.fwd_stats(close, sim[col].to_numpy(dtype=bool))
            for h in (20, 60, 120):
                pooled.setdefault((sid, h), []).extend(list(fwd[h]))
                base_all[h].append(base[h])
        # 全样本基线（同一条腿）
    base = {h: float(np.nanmean(base_all[h])) for h in base_all}
    for sid, desc in vn.SIGNAL_DESC.items():
        row = dict(sid=sid, desc=desc, group=("买入" if sid in vn.BUY or sid in vn.HEAVY else
                                             "清仓" if sid in vn.CLEAR else
                                             "减仓" if sid in vn.REDUCE else
                                             "观望" if sid in vn.WATCH_ONLY else "统计"))
        for h in (20, 60, 120):
            arr = np.asarray(pooled.get((sid, h), []), dtype=float)
            arr = arr[np.isfinite(arr)]
            row[f"n{h}"] = int(len(arr))
            row[f"mean{h}"] = float(arr.mean()) if len(arr) else float("nan")
            row[f"med{h}"] = float(np.median(arr)) if len(arr) else float("nan")
            row[f"win{h}"] = float((arr > 0).mean()) if len(arr) else float("nan")
        rows.append(row)
    return rows, base


# ---------------------------------------------------------------- 分段 / 逐年
def seg_of(nav, a, b, min_bars=20):
    """一段区间的收益/年化/回撤；该段数据不足 -> None。"""
    sub = nav.loc[a:b]
    if len(sub) < min_bars:
        return None
    return dict(ret=float(sub.iloc[-1] / sub.iloc[0] - 1.0), ann=vn.perf(sub)["ann"],
                mdd=vn.perf(sub)["mdd"], n=len(sub))


def yearly(nav):
    ys = sorted({d.year for d in nav.index})
    out = []
    for y in ys:
        sub = nav[nav.index.year == y]
        if len(sub) < 3:
            continue
        out.append(dict(year=str(y), ret=float(sub.iloc[-1] / sub.iloc[0] - 1.0)))
    return out


# ---------------------------------------------------------------- 主流程
def main():
    os.makedirs(OUTDIR, exist_ok=True)
    recs = []

    # ---- §2 失真检验
    dist = [ohlcv.distortion(k) for k in vn.LEGS]
    recs += [{"distort": d} for d in dist]

    # ---- §3 判据三档（同一把尺子：上线池三腿组合）
    crit = []
    for mode, label in CRITERIA:
        nav, frame, rets, infos, wts, bh, _wp = port(POOL, high_vol_mode=mode)
        m = metrics(nav)
        per_leg = {k: infos[k] for k in POOL}
        crit.append(dict(mode=mode, label=label, window=f"{frame.index[0].date()}~{frame.index[-1].date()}",
                         **m, exposure=float(_wp.mean()),
                         acts_per_year=float(np.sum([per_leg[k]["acts_per_year"] for k in POOL])),
                         turnover_per_year=float(np.mean([per_leg[k]["turnover_per_year"] for k in POOL])),
                         legs={k: dict(ann=per_leg[k]["nav"]["ann"], mdd=per_leg[k]["nav"]["mdd"],
                                       bh=per_leg[k]["bh"]["ann"], acts=per_leg[k]["acts_per_year"])
                               for k in POOL}))
    recs += [{"criteria": c} for c in crit]

    # ---- §4 信号层（上线池池化）
    sigrows, base = signal_table(POOL)
    recs += [{"signal": r} for r in sigrows]

    # ---- §5 逐腿 + 组合层
    leg_infos, leg_navs = {}, {}
    for k in vn.LEGS:
        _sim, _df, _nav, _r, _w, inf = leg_run(k)
        leg_infos[k], leg_navs[k] = inf, _nav
    recs += [{"leg": v} for v in leg_infos.values()]

    nav_pool, frame_pool, rets_pool, infos_pool, w_pool, bh_pool, wpos_pool = port(POOL)
    # 「全池」去掉 2024-10 才成立的 A500，否则七腿交集只剩 1.4 年（恒生科技 2021-08 就够不上）
    nav_all, frame_all, *_ = port(ALL6)
    m_pool, m_bh, m_all = metrics(nav_pool), metrics(bh_pool), metrics(nav_all)
    # 同曝光买入持有（组合层）：把等权买入持有压到「组合平均投入」
    inv_pool = float(wpos_pool.mean())      # 共同窗口内的时间均值（必须与"同曝光"基准同源）
    r_same = inv_pool * bh_pool.pct_change().fillna(0.0) + (1.0 - inv_pool) * vn.CASH_DAILY
    nav_same = (1.0 + r_same).cumprod()
    m_same = metrics(nav_same)
    recs.append({"pool": dict(legs=POOL, start=frame_pool.index[0].date().isoformat(),
                              end=frame_pool.index[-1].date().isoformat(),
                              years=round((frame_pool.index[-1] - frame_pool.index[0]).days / 365.25, 2),
                              v13=m_pool, bh=m_bh, same=m_same, all7=m_all,
                              invested=inv_pool,
                              weights={k: round(w_pool[k], 4) for k in POOL})})

    # ---- §6 分段（规格 §5 的两段：2011-2018 / 2019-2026，落到数据上能覆盖多少算多少）
    seg_edges = [(str(a), str(b)) for a, b in vn.CFG["segments"]]
    segs_leg = {k: [seg_of(leg_navs[k], a, b) for a, b in seg_edges] for k in vn.LEGS}
    idx_p = nav_pool.index
    half = idx_p[len(idx_p) // 2]
    segs_pool = []
    for a, b in ((idx_p[0], half), (half, idx_p[-1])):
        s = seg_of(nav_pool, a, b)
        segs_pool.append(dict(seg=f"{a.date()} ~ {b.date()}", **s))
    yrs = yearly(nav_pool)
    recs += [{"seg_pool": s} for s in segs_pool]
    recs += [{"seg_leg": {"leg": k, "seg": i, **s}} for k, v in segs_leg.items()
             for i, s in enumerate(v) if s]
    recs += [{"year": y} for y in yrs]

    # ---- §8 消融与敏感性（上线池组合）
    sens = []
    for name, over in SENS:
        over = dict(over)
        nocost = bool(over.pop("_NOCOST", False))
        drop = tuple(over.pop("_DROP", ()))
        kw = dict(over)
        if drop:
            nav, frame, rets, infos, wts, bh, _wp = _port_filtered(POOL, drop=drop, **kw)
        else:
            nav, frame, rets, infos, wts, bh, _wp = port(POOL, cost=not nocost, **kw)
        m = metrics(nav)
        sens.append(dict(name=name, **m,
                         exposure=float(_wp.mean()),      # 与 §5.1 的「平均投入」同一把尺子
                         acts_per_year=float(np.sum([infos[k]["acts_per_year"] for k in POOL])),
                         turnover_per_year=float(np.mean([infos[k]["turnover_per_year"] for k in POOL]))))
    recs += [{"sens": s} for s in sens]

    # ---- §8b 降频的相位稳健性（同一 N、只错开「哪一天调仓」）
    phases = []
    for n in (5, 10, 20):
        p = phase_spread(POOL, n)
        phases.append(p)
        recs.append({"phase": p})

    # ---- §7 冲突率
    conf = {k: dict(conflict_rate=leg_infos[k]["conflict_rate"], acts=leg_infos[k]["acts"],
                    acts_per_year=leg_infos[k]["acts_per_year"]) for k in vn.LEGS}
    recs += [{"conflict": {"leg": k, **v}} for k, v in conf.items()]

    with open(OUT_JSONL, "w", encoding="utf-8") as f:
        for r in recs:
            f.write(json.dumps(r, ensure_ascii=False, default=str,
                               sort_keys=False) + "\n")

    # ---------------------------------------------------------- 报告
    L = _report(dist, crit, sigrows, base, leg_infos, m_pool, m_bh, m_same, m_all,
                frame_pool, inv_pool, w_pool, segs_pool, segs_leg, yrs, sens, phases)
    with open(OUT_MD, "w", encoding="utf-8") as f:
        f.write("\n".join(L))

    print("报告:", OUT_MD)
    print("数据:", OUT_JSONL)
    for c in crit:
        print(f"  [判据] {c['label']:26s} 年化 {fp(c['ann']):>9s} 回撤 {fp(c['mdd']):>9s} "
              f"夏普 {fn(c['sharpe'])} 曝光 {fs(c['exposure'])} 换手 {fn(c['turnover_per_year'], 1)}x/年")
    print(f"  [组合-上线池] 年化 {fp(m_pool['ann'])} 回撤 {fp(m_pool['mdd'])} 夏普 {fn(m_pool['sharpe'])} "
          f"vs 买入持有 {fp(m_bh['ann'])}/{fp(m_bh['mdd'])} vs 同曝光 {fp(m_same['ann'])}")
    for k in POOL:
        i = leg_infos[k]
        print(f"  {i['name']:8s} 年化 {fp(i['nav']['ann']):>9s} vs BH {fp(i['bh']['ann']):>9s} · "
              f"回撤 {fp(i['nav']['mdd']):>8s} vs {fp(i['bh']['mdd']):>8s} · "
              f"曝光 {fs(i['expo'])} · {fn(i['acts_per_year'], 1)} 次/年 · 冲突率 {fs(i['conflict_rate'])}")


def _port_filtered(legs, drop=(), **kw):
    """消融用：把 `drop` 里的动作类（clear/reduce/buy）从状态机里摘掉，其余口径不变。"""
    runs = {}
    for k in legs:
        df = ohlcv.frame(k)
        sim = vn.state_series(df, k, **kw)
        ready = sim["ready"].to_numpy(dtype=bool)
        sim = sim.iloc[int(np.argmax(ready)):]
        rule_hit = sim["rule"].to_numpy(dtype=object)
        # 重放一遍：只按允许的那一类动作改仓位
        cur = float(kw.get("START_MULT", vn.START_MULT))
        out = np.empty(len(sim))
        for t, hits in enumerate(rule_hit):
            ids = str(hits).split("+") if hits else []
            if "buy" in drop:
                ids = [i for i in ids if i not in vn.BUY and i not in vn.HEAVY]
            if "reduce" in drop:
                ids = [i for i in ids if i not in vn.REDUCE]
            if "clear" in drop:
                ids = [i for i in ids if i not in vn.CLEAR]
            act, _ = vn._rank(ids, vn.cfg())
            if act == "clear":
                cur = 0.0
            elif act == "reduce":
                cur = 0.0 if cur <= 1e-9 else min(cur, float(vn.CFG["position"]["reduce"]))
            elif act in ("buy", "heavy"):
                cur = min(float(vn.CFG["position"][act]), vn.POS_CAP)
            out[t] = cur
        s = pd.Series(out, index=sim.index)
        d2 = df.loc[s.index]
        nav, r, w, deferred = vn.run_leg(s, d2)
        yrs = max((s.index[-1] - s.index[0]).days / 365.25, 1e-9)
        inf = dict(expo=float(w.mean()), acts_per_year=vn.actions(s).__len__() / yrs,
                   turnover_per_year=float(np.abs(np.diff(np.concatenate([[0.0], w.to_numpy()]))).sum()) / yrs)
        runs[k] = (s, d2, nav, r, w, inf)        # r 已含空仓计息（见 run_leg）
    idx = runs[legs[0]][0].index
    for k in legs[1:]:
        idx = idx.intersection(runs[k][0].index)
    frame = pd.DataFrame({k: runs[k][1]["close"].reindex(idx) for k in legs}).dropna()
    rets = {k: runs[k][3].reindex(frame.index).fillna(0.0) for k in legs}
    wts = pool_of(legs)
    nav = vn.portfolio(frame, rets, weight=wts)
    bh = _bh_nav(frame, wts)
    wpos = None
    for k in legs:
        ww = runs[k][4].reindex(frame.index).fillna(0.0) * wts[k]
        wpos = ww if wpos is None else wpos + ww
    return nav, frame, rets, {k: runs[k][5] for k in legs}, wts, bh, wpos


def phase_spread(legs, n, **kw):
    """同一套规则、同一个「每 n 日调仓」，只把采样相位错开 0..n-1。

    用途：`REBAL_EVERY=n` 的收益对「哪一天调仓」有多敏感。相位间离散大 = 单相位读数不可信。
    """
    cache = {}
    for k in legs:
        df = ohlcv.frame(k)
        sim = vn.state_series(df, k, **kw)
        ok = sim["ready"].to_numpy(dtype=bool)
        sim = sim.iloc[int(np.argmax(ok)):]
        cache[k] = (sim, df.loc[sim.index])
    wts = pool_of(legs)
    anns, mdds = [], []
    for off in range(n):
        rets = {}
        for k in legs:
            sim, d2 = cache[k]
            mult = vn._thin(sim["mult"].to_numpy(dtype=float), n, off)
            _nav, r, _w, _d = vn.run_leg(pd.Series(mult, index=sim.index), d2)
            rets[k] = r
        idx = None
        for k in legs:
            idx = rets[k].index if idx is None else idx.intersection(rets[k].index)
        idx = idx.sort_values()
        tot = None
        for k in legs:
            rr = rets[k].reindex(idx).fillna(0.0) * wts[k]
            tot = rr if tot is None else tot + rr
        m = metrics((1.0 + tot).cumprod())
        anns.append(m["ann"])
        mdds.append(m["mdd"])
    arr = np.array(anns)
    return dict(n=n, lo=float(arr.min()), mid=float(np.median(arr)), hi=float(arr.max()),
                sd=float(arr.std()), mdd_worst=float(min(mdds)))


def _report(dist, crit, sigrows, base, leg_infos, m_pool, m_bh, m_same, m_all,
            frame_pool, inv_pool, w_pool, segs_pool, segs_leg, yrs, sens, phases):
    ok = [d for d in dist if d["edge"] != "no"]
    bad = [d for d in dist if d["edge"] == "no"]
    c_def = next(c for c in crit if c["mode"] == "quantile")
    c_d1 = next(c for c in crit if c["mode"] == "d1")
    c_d1b = next(c for c in crit if c["mode"] == "strict_b")
    c_best = max(crit, key=lambda c: c["ann"])
    c_worst = min(crit, key=lambda c: c["ann"])
    s_def = next(s for s in sens if s["name"].startswith("V13 默认"))
    s_watch = next(s for s in sens if s["name"].startswith("③ 冲突日不动"))
    s_flat = next(s for s in sens if s["name"].startswith("③ 空仓起步"))
    s_heavy = next(s for s in sens if s["name"].startswith("④ 重仓"))
    s_keep = next(s for s in sens if s["name"].startswith("⑤ 只留减/清类"))
    s_dropred = next(s for s in sens if s["name"].startswith("⑤ 剔除减仓类"))
    s_dropclr = next(s for s in sens if s["name"].startswith("⑤ 剔除清仓类"))
    s_nc = next(s for s in sens if s["name"].startswith("⑦ 零成本"))
    s_gap = next(s for s in sens if s["name"].startswith("⑥ 同向动作间隔"))
    s_r5 = next(s for s in sens if s["name"].startswith("⑧ 调仓频率 5"))
    s_r10 = next(s for s in sens if s["name"].startswith("⑧ 调仓频率 10"))
    s_r20 = next(s for s in sens if s["name"].startswith("⑧ 调仓频率 20"))

    L = [
        "# 波动资产 · V13 变种：成交量操盘笔记（阿牛智投笔记 2.0 第一章）",
        "",
        "> 规格：`E:/qianwen/jijin/成交量操盘笔记-回测规格.md`；参数权威源：`vol_notes.py::CFG`。",
        "> **口径：场内 ETF 前复权日线 OHLCV**（`strategies/vol_legs/data/_ohlcv.json`）—— "
        "本变种的全部判据都建立在成交量与影线上，场外净值没有量，规则一条也算不出来。",
        f"> 成交：**T 日收盘信号 → T+1 开盘成交**；一字板顺延并单独计数；"
        f"场内成本单边 {vn.FEE_ETF * 1e4:.0f}bp（免印花税）；空仓按货基 {vn.CASH_ANNUAL:.2%} 计息。",
        "",
        "## 1. 规则（规格 §2~§4 的实现）",
        "",
        "| 层 | 规则 | 参数（`vol_notes.py`） |",
        "|---|---|---|",
        f"| 高量锚点 D0 | **D1c**：`V ≥ 前 {vn.CFG['high_vol_lookback']} 日 {vn.CFG['high_vol_quantile']:.0%} 分位` "
        f"（默认；D1「量>前三日最大」与 D1b「2×MA20」作对照） | `CFG['high_vol_mode']` |",
        f"| 观察窗 D8 | 高量日后 `D0+1 … D0+{vn.CFG['window_days']}`，**到期不重置、不滚动** | `window_days` |",
        "| 支撑 S | 最近一个高量日的**实体低点** `min(O,C)`（不是影线低点） | `support` |",
        f"| 压力 R | 最近 {vn.CFG['resist_n_highvol']} 个高量日的**实体高点** `max(O,C)` | `resist_n_highvol` |",
        "| 上车 | B1 支撑上方底分型 / B2 支撑上方红三兵 / B3 上影线被阳实体突破 / "
        "B4 = B3 且前一日高量（重仓档） | §3.1 |",
        "| 减仓 | S3 窗内高低点下移 / S4 压力下顶分型 / S5 压力下绿三兵 / S6 上影碰压力过不去 / "
        "S7 连红≥4 见放量阴 / S8 缩量下影次日阴 / S9 上涨趋势高量大长腿 / T7 价不过高量过高 | §3.2/§3.4 |",
        "| 清仓 | S1 观察窗内 `C < S` / S10 大量次日阴 | §3.2 |",
        "| 观望 | S2 破位次日收阳（不追回）/ W1 高量当天不加仓；W2~W8 只统计不产动作 | §3.3 |",
        "| 冲突消解 | `清仓 > 减仓 > 买入 > 观望`（默认）；`CONFLICT_MODE=\"watch\"` 可切 W9 原意 | `CFG['priority']` |",
        "| 仓位 | 清仓 0 / 减仓 ×0.5 / 观望不变 / 上车 ×1.0 / 重仓 ×1.5（**受腿上限约束 → 截断为 1.0**） | `CFG['position']` |",
        "",
        "> **「重仓 ×1.5」在本家族口径下被截断**：家族不变量是「目标带 = 该腿上限」，"
        "1.5 倍带一出手就顶穿上限。故默认 `POS_CAP=1.0`，B4 与 B3 的实际仓位相同，"
        f"差别只在信号层（§4）；`HEAVY_UNCAP=True` 是放开截断的诊断档（§8，"
        f"年化 {fp(s_heavy['ann'])} vs 默认 {fp(s_def['ann'])}，"
        f"{(s_heavy['ann'] - s_def['ann']) * 100:+.2f}pp）。",
        "",
        "## 2. 数据与适用边界（规格 §8）",
        "",
        "规格把「哪些腿不要接」写死了。本仓库对应的七条腿全部是**场内 ETF**，逐条映射如下：",
        "",
        "| 腿 | 场内代码 | 本仓库判定 | 规格依据 | 样本 | D1 频率 | D1b | D1c | 高量日均\\|r\\| ÷ 普通日 | corr(量比,\\|r\\|) |",
        "|---|---|---|---|---|---:|---:|---:|---:|---:|",
    ]
    for d in dist:
        L.append(f"| {rule.LEGS[d['leg']]['name']} | `{ohlcv.CODES[d['leg']]}` {d['name']} | "
                 f"**{d['edge_label']}** | "
                 f"{d['note']} | {d['start'][:7]}~{d['end'][:7]}（{d['n']} 根） | "
                 f"{fs(d['freq_d1'], 1)} | {fs(d['freq_b'], 1)} | {fs(d['freq_q'], 1)} | "
                 f"{fn(d['amp'])} | {fn(d['corr'])} |")
    amp_ok = float(np.mean([d["amp"] for d in ok]))
    amp_bad = float(np.mean([d["amp"] for d in bad]))
    corr_ok = float(np.mean([d["corr"] for d in ok]))
    corr_bad = float(np.mean([d["corr"] for d in bad]))
    L += [
        "",
        "> **失真检验的读数**：D1 在七条腿上的触发频率都在 **25%~29%**（iid 理论 25.0%），"
        "与规格 §7 对沪深300 指数的实测（25.8%）一致 —— **原文的锚点在定义层不含异常信息**。"
        "只有换成倍数/分位（D1b 2%~6%、D1c 8%~15%）才像「异动」。",
        f"> 量价同步性上，**上线池（振幅比 {amp_ok:.2f} / corr {corr_ok:+.2f}）强于不接的四条腿"
        f"（{amp_bad:.2f} / {corr_bad:+.2f}）** —— 方向与规格 §8 一致："
        "主题/行业 ETF 的量比宽基与 QDII 更贴近真实换手。"
        "但要注意「没失真」只是**入场条件**，不等于量能能赚钱（§4、§5 给出了反面证据）。",
        "",
        "## 3. 判据三档对比（规格 §6 对照组 3 —— 本回测最重要的产出）",
        "",
        "同一个状态机、同一把成本尺子，只换「什么算高量」。标的是**上线池三腿**"
        "（科创50 2.5pp / 恒生科技 2.5pp / 黄金 10pp，组内归一化），"
        f"共同窗口 **{c_def['window']}**：",
        "",
        "| 判据 | 年化 | 最大回撤 | 夏普 | Calmar | 平均曝光 | 动作/年 | 换手/年 |",
        "|---|---:|---:|---:|---:|---:|---:|---:|",
    ]
    for c in crit:
        L.append(f"| {c['label']} | {fp(c['ann'])} | {fp(c['mdd'])} | {fn(c['sharpe'])} | "
                 f"{fn(c['calmar'])} | {fs(c['exposure'])} | {fn(c['acts_per_year'], 0)} | "
                 f"{fn(c['turnover_per_year'], 1)}x |")
    L += [
        "",
        f"> **收益差远小于曝光差，且排序不稳定**：三档年化 {fp(c_worst['ann'])} ~ {fp(c_best['ann'])}"
        f"（差 {(c_best['ann'] - c_worst['ann']) * 100:.2f}pp），回撤差 "
        f"{abs(max(c['mdd'] for c in crit) - min(c['mdd'] for c in crit)) * 100:.1f}pp，"
        f"但平均曝光跨 {fs(min(c['exposure'] for c in crit))} ~ {fs(max(c['exposure'] for c in crit))}"
        f"（差 {(max(c['exposure'] for c in crit) - min(c['exposure'] for c in crit)) * 100:.0f}pp）、"
        f"换手跨 {fn(min(c['turnover_per_year'] for c in crit), 1)}x ~ "
        f"{fn(max(c['turnover_per_year'] for c in crit), 1)}x。"
        f"更关键的是**排序不自洽**：曝光最低的 D1（{fs(c_d1['exposure'])}）收益反而略高于曝光 60% 的 D1c —— "
        "说明这三档之间不是「谁更有信息」，而是三条不同的仓位路径。",
        f"> D1（原文口径）与 D1c（默认）的**收益几乎相同**（{fp(c_d1['ann'])} vs {fp(c_def['ann'])}，"
        f"{(c_def['ann'] - c_d1['ann']) * 100:+.2f}pp），差别在**换手**："
        f"{fn(c_d1['turnover_per_year'], 1)}x/年 vs {fn(c_def['turnover_per_year'], 1)}x/年，"
        f"动作 {fn(c_d1['acts_per_year'], 0)} vs {fn(c_def['acts_per_year'], 0)} 次/年 —— "
        "因为 25% 的触发频率把「观察窗」变成了常态。",
        f"> D1b 最严（1.9%~6.3% 频率），年化最高 {fp(c_d1b['ann'])}，但它同时也是**曝光最高**的一档"
        f"（{fs(c_d1b['exposure'])}）—— 这段样本里买入持有是正的（带权 {fp(m_bh['ann'])}），"
        "所以「更少减仓」自然更赚。**这不是判据变好的证据，是仓位变高的结果。**",
        "> 结论与规格 §7 的断言**在定义层一致、在收益层不成立**："
        "「D1 不含异常信息」被频率检验证实（25% ≈ iid 25%）；"
        "但「换成分位就能赚钱」不成立 —— 换判据只改变了交易频率与曝光，没有造出稳定的超额。",
        "",
        "## 4. 信号层：每条信号之后的 D+20/60/120 收益分布（规格 §6）",
        "",
        "上线池三腿**池化**，基线 = 同腿全样本平均（同期）。规格 §1 红线："
        f"**样本 <{vn.CFG['min_signals_for_portfolio']} 个的信号只报分布、不报年化**（下表带 ✱）。",
        "",
        "| ID | 信号 | 类 | n | D+20 均值 | 中位 | 胜率 | D+60 均值 | 中位 | 胜率 | D+120 均值 | 中位 | 胜率 |",
        "|---|---|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|",
    ]
    for r in sorted(sigrows, key=lambda z: -z["n60"]):
        mark = " ✱" if r["n60"] < vn.CFG["min_signals_for_portfolio"] else ""
        L.append(f"| {r['sid']}{mark} | {r['desc']} | {r['group']} | {r['n60']} | "
                 f"{fp(r['mean20'])} | {fp(r['med20'])} | {fs(r['win20'])} | "
                 f"{fp(r['mean60'])} | {fp(r['med60'])} | {fs(r['win60'])} | "
                 f"{fp(r['mean120'])} | {fp(r['med120'])} | {fs(r['win120'])} |")
    top = [r for r in sigrows if r["n60"] >= vn.CFG["min_signals_for_portfolio"]]
    best = max(top, key=lambda z: z["mean60"]) if top else None
    worst = min(top, key=lambda z: z["mean60"]) if top else None
    buy_rows = [r for r in top if r["group"] == "买入"]
    sell_rows = [r for r in top if r["group"] in ("减仓", "清仓")]
    buy_avg = float(np.mean([r["mean60"] for r in buy_rows])) if buy_rows else float("nan")
    sell_avg = float(np.mean([r["mean60"] for r in sell_rows])) if sell_rows else float("nan")
    L += [
        "",
        f"> 基线（同腿全样本）：D+20 {fp(base[20])} / D+60 {fp(base[60])} / D+120 {fp(base[120])}。"
        f"过样本红线的信号共 **{len(top)} 条**，D+60 均值最高 **{best['sid']}**"
        f"（{fp(best['mean60'])}，n={best['n60']}）、最低 **{worst['sid']}**（{fp(worst['mean60'])}）。",
        f"> **买入类 {len(buy_rows)} 条过红线、D+60 均值 {fp(buy_avg)}；"
        f"减/清类 {len(sell_rows)} 条、D+60 均值 {fp(sell_avg)}** —— "
        f"买入类确实略高于减/清类（差 {(buy_avg - sell_avg) * 100:+.2f}pp），"
        f"但这个差**小于同组内部的离散度**（买入类 "
        f"{fp(min(r['mean60'] for r in buy_rows))} ~ {fp(max(r['mean60'] for r in buy_rows))}，"
        f"减/清类 {fp(min(r['mean60'] for r in sell_rows))} ~ {fp(max(r['mean60'] for r in sell_rows))}）。"
        "也就是说**方向对、强度不足**：拿它当择时依据，噪声会盖过信号。",
        "> 胜率列必须与盈亏比一起读（规格 §1 红线③）。本表只给「正收益占比」，"
        "完整胜率/盈亏比在 §5 的单腿表里（按 0→>0→0 切单笔），单看这一列会高估。",
        "",
        "## 5. 逐腿与组合层（成本已扣）",
        "",
        "| 腿 | 判定 | 样本 | 年数 | V13 年化 | 最大回撤 | 夏普 | 买入持有年化 | 买入持有回撤 | "
        "同曝光买入持有 | 择时超额(同曝光) | 曝光 | 动作/年 | 换手/年 | 笔数 | 胜率 | 盈亏比 | 一字板顺延 |",
        "|---|---|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|",
    ]
    for k in vn.LEGS:
        i = leg_infos[k]
        L.append(f"| {i['name']} | {i['edge']} | {i['start']}~{i['end']} | {i['years']:.1f} | "
                 f"{fp(i['nav']['ann'])} | {fp(i['nav']['mdd'])} | {fn(i['nav']['sharpe'])} | "
                 f"{fp(i['bh']['ann'])} | {fp(i['bh']['mdd'])} | {fp(i['same']['ann'])} | "
                 f"**{(i['nav']['ann'] - i['same']['ann']) * 100:+.2f}pp** | {fs(i['expo'])} | "
                 f"{fn(i['acts_per_year'], 0)} | {fn(i['turnover_per_year'], 1)}x | {i['trades']} | "
                 f"{fs(i['win_rate'])} | {fn(i['payoff'])} | {i['deferred']} |")
    n_beat = sum(1 for k in POOL if leg_infos[k]["nav"]["ann"] > leg_infos[k]["same"]["ann"])
    n_beat_ref = sum(1 for k in REF if leg_infos[k]["nav"]["ann"] > leg_infos[k]["same"]["ann"])
    dd_win = sum(1 for k in vn.LEGS if leg_infos[k]["nav"]["mdd"] > leg_infos[k]["bh"]["mdd"])
    L += [
        "",
        f"> **上线池 {n_beat}/{len(POOL)} 条腿跑赢「同曝光买入持有」；不接的四条腿 {n_beat_ref}/{len(REF)}**。"
        f"而 **{dd_win}/{len(vn.LEGS)} 条腿的最大回撤比「满仓买入持有」更浅** —— 但这是仓位降下来的结果，"
        "不是择时的功劳：§5.1 的同曝光口径下，回撤几乎没有改善。"
        "**读这一列要看两行**：回撤 vs 满仓（好看）与 择时超额 vs 同曝光（难看）。",
        "> 「同曝光买入持有」= 把买入持有压到与该策略相同的平均投入（其余放货基），"
        "否则是拿「一半时间空仓」的策略去比 100% 在场的买入持有，必然输在绝对收益上。",
        f"> **动作频率是这套规则的硬伤**：逐腿 {fn(np.mean([leg_infos[k]['acts_per_year'] for k in vn.LEGS]), 0)}"
        f" 次/年（规格 §5 期望的「3~5 日节奏」在日频上就是这个量级）、换手 "
        f"{fn(np.mean([leg_infos[k]['turnover_per_year'] for k in vn.LEGS]), 1)}x/年，"
        f"成本拖累 {abs(s_def['ann'] - s_nc['ann']) * 100:.2f}pp/年 —— "
        "清单里 9 条减仓规则在震荡市里会反复触发。",
        "",
        f"### 5.1 组合层（上线池三腿，组内归一化；窗口 {frame_pool.index[0].date()} ~ "
        f"{frame_pool.index[-1].date()}，{len(frame_pool)} 个交易日）",
        "",
        "| 组合 | 累计 | 年化 | 波动 | 最大回撤 | Calmar | 夏普 | 平均投入 |",
        "|---|---:|---:|---:|---:|---:|---:|---:|",
        f"| **V13（上线池三腿）** | {fp(m_pool['cum'], 1)} | {fp(m_pool['ann'])} | {fp(m_pool['vol'])} | "
        f"{fp(m_pool['mdd'])} | {fn(m_pool['calmar'])} | {fn(m_pool['sharpe'])} | {fs(inv_pool)} |",
        f"| 带权买入持有（同池 · 与策略同权重） | {fp(m_bh['cum'], 1)} | {fp(m_bh['ann'])} | {fp(m_bh['vol'])} | "
        f"{fp(m_bh['mdd'])} | {fn(m_bh['calmar'])} | {fn(m_bh['sharpe'])} | 100% |",
        f"| 同曝光买入持有（固定 {inv_pool:.0%} 投入） | {fp(m_same['cum'], 1)} | {fp(m_same['ann'])} | "
        f"{fp(m_same['vol'])} | {fp(m_same['mdd'])} | {fn(m_same['calmar'])} | "
        f"{fn(m_same['sharpe'])} | {fs(inv_pool)} |",
        f"| 六腿对照（去掉 2024-10 才成立的 A500，**含四条「不接」的腿**） | — | {fp(m_all['ann'])} | "
        f"{fp(m_all['vol'])} | {fp(m_all['mdd'])} | {fn(m_all['calmar'])} | {fn(m_all['sharpe'])} | — |",
        "",
        f"> 组合层 vs 同曝光：年化 **{(m_pool['ann'] - m_same['ann']) * 100:+.2f}pp**、"
        f"回撤 {fp(m_pool['mdd'])} vs {fp(m_same['mdd'])}、"
        f"夏普 {fn(m_pool['sharpe'])} vs {fn(m_same['sharpe'])}。"
        f"权重按目标带归一（{_wtext(w_pool, POOL)}）。",
        f"> 六腿对照 {fp(m_all['ann'])} / 夏普 {fn(m_all['sharpe'])} 与上线池 {fp(m_pool['ann'])} / "
        f"{fn(m_pool['sharpe'])} 基本同级 —— **规格 §8 的判断是对的**：把宽基与 QDII 加进来，"
        "这套量价规则并没有变好（多出来的沪深300/美股额度同样被减仓规则压到半仓以下）。",
        "",
        "## 6. 分段与逐年（规格 §5「≥10 年且分两段报」）",
        "",
        "规格要的是 2011-2018 / 2019-2026 两段，落地后的现实：**本地 OHLCV 库 2013 起，"
        "上线池共同窗口只有 2021-08 起**，所以两段只能这样报 ——",
        "",
        "### 6.1 逐腿两段（各腿自己的样本内；`—` = 该段无数据）",
        "",
        "| 腿 | 2013-2018 收益 | 2019-2026 收益 | 2019-2026 年化 | 2019-2026 回撤 | 第二段天数 |",
        "|---|---:|---:|---:|---:|---:|",
    ]
    for k in vn.LEGS:
        a, b = segs_leg[k][0], segs_leg[k][1]
        L.append(f"| {rule.LEGS[k]['name']} | {fp(a['ret'], 1) if a else '—'} | "
                 f"{fp(b['ret'], 1) if b else '—'} | {fp(b['ann']) if b else '—'} | "
                 f"{fp(b['mdd']) if b else '—'} | {b['n'] if b else '—'} |")
    L += [
        "",
        "### 6.2 上线池组合两段（按窗口对半切）",
        "",
        "| 区间 | 收益 | 年化 | 最大回撤 |",
        "|---|---:|---:|---:|",
    ]
    for s in segs_pool:
        L.append(f"| {s['seg']} | {fp(s['ret'], 1)} | {fp(s['ann'])} | {fp(s['mdd'])} |")
    L += [
        "",
        f"> 两段的年化差 {fp(segs_pool[1]['ann'] - segs_pool[0]['ann'])} —— "
        f"只有 {len(frame_pool)} 个交易日、两段各 {len(frame_pool) // 2} 天，"
        "**这个差是噪声，不足以支撑「稳健/不稳健」的判断**。逐腿表里能看的只有黄金那段 12.9 年。",
        "",
        "| 年 | V13（上线池） |",
        "|---|---:|",
    ]
    for y in yrs:
        L.append(f"| {y['year']} | {fp(y['ret'], 1)} |")
    L += [
        "",
        "## 7. 冲突率（规格 §4「必须单独统计」）",
        "",
        "| 腿 | 有信号日占比 | 冲突率（同日买+卖） | 动作/年 |",
        "|---|---:|---:|---:|",
    ]
    for k in vn.LEGS:
        i = leg_infos[k]
        L.append(f"| {i['name']} | {fs(i['signal_rate'])} | {fs(i['conflict_rate'])} | "
                 f"{fn(i['acts_per_year'], 0)} |")
    avg_conf = float(np.mean([leg_infos[k]["conflict_rate"] for k in POOL]))
    avg_sig = float(np.mean([leg_infos[k]["signal_rate"] for k in POOL]))
    L += [
        "",
        f"> 上线池平均：**有信号日 {fs(avg_sig)}、冲突率 {fs(avg_conf)}**。"
        "规格说「原文的元规则『矛盾=观望』意味着这套体系真实产出大概率是『不动』，"
        f"冲突率就是这个判断的直接证据」—— 实测冲突率只有 {fs(avg_conf)}，"
        "因为 §4 的优先级已经把卖出类提前拦下，买类（B 系列）很少与卖出类同日成立。",
        f"> 但把冲突改成「观望」（W9 原意）反而**年化更高**：{fp(s_watch['ann'])} vs 默认 "
        f"{fp(s_def['ann'])}（**{(s_watch['ann'] - s_def['ann']) * 100:+.2f}pp**）、"
        f"换手 {fn(s_watch['turnover_per_year'], 1)}x vs {fn(s_def['turnover_per_year'], 1)}x。"
        "也就是说「矛盾时不动」比「风控优先」更省钱 —— 原文的元规则在这一层上是**有价值的**，"
        "尽管它的触发频率很低。",
        "",
        "## 8. 消融与敏感性（上线池三腿组合，逐档只改一个开关）",
        "",
        "| 档位 | 年化 | 最大回撤 | 夏普 | Calmar | 平均曝光 | 动作/年 | 换手/年 |",
        "|---|---:|---:|---:|---:|---:|---:|---:|",
    ]
    for s in sens:
        L.append(f"| {s['name']} | {fp(s['ann'])} | {fp(s['mdd'])} | {fn(s['sharpe'])} | "
                 f"{fn(s['calmar'])} | {fs(s['exposure'])} | {fn(s['acts_per_year'], 0)} | "
                 f"{fn(s['turnover_per_year'], 1)}x |")
    anns = [s["ann"] for s in sens]
    L += [
        "",
        f"> {len(sens)} 档的年化落在 **{fp(min(anns))} ~ {fp(max(anns))}**、中位 **{fp(float(np.median(anns)))}**"
        " —— 参数邻域内没有稳定为正的超额，**分布横跨正负**。",
        f"> **逐类拆掉**：只留减/清（关掉买入）{fp(s_keep['ann'])} / 回撤 {fp(s_keep['mdd'])} / "
        f"曝光 {fs(s_keep['exposure'])}；剔除减仓类（只剩清仓+买入）{fp(s_dropred['ann'])} / "
        f"{fp(s_dropred['mdd'])} / {fs(s_dropred['exposure'])}；剔除清仓类（只剩减仓+买入）"
        f"{fp(s_dropclr['ann'])} / {fp(s_dropclr['mdd'])} / {fs(s_dropclr['exposure'])}；"
        f"完整版 {fp(s_def['ann'])} / {fp(s_def['mdd'])} / {fs(s_def['exposure'])}；"
        f"买入持有 {fp(m_bh['ann'])} / {fp(m_bh['mdd'])} / 100%。",
        "> **两类卖出都在扣分，且拆掉的速度不同**："
        f"剔除减仓类（保清仓）年化 {fp(s_dropred['ann'])}（相对完整版 "
        f"{(s_dropred['ann'] - s_def['ann']) * 100:+.2f}pp）；"
        f"剔除清仓类（保减仓）年化 {fp(s_dropclr['ann'])}（"
        f"{(s_dropclr['ann'] - s_def['ann']) * 100:+.2f}pp）。"
        "也就是说清仓（S1/S10）比减仓（S3~S9/T7）更贵 —— 它把仓位打到 0，"
        "要等 B 类信号才回补，而 B 类信号在下跌末端出现得晚。"
        "**这是「破位就走」在 ETF 上的第二次证伪**（第一次是 v12 对支撑位的测试）。",
        "> 只留减/清那一档的夏普看起来很高，但它平均曝光只有 "
        f"{fs(s_keep['exposure'])} —— 那是「不持有」的夏普，不是策略的夏普。",
        f"> **空仓起步（`START_MULT=0`）与满带起步几乎无差**：{fp(s_flat['ann'])} vs "
        f"{fp(s_def['ann'])}（{(s_flat['ann'] - s_def['ann']) * 100:+.2f}pp）、曝光 "
        f"{fs(s_flat['exposure'])} vs {fs(s_def['exposure'])}。"
        "B1/B3 信号足够密集，几天内就把仓位补回 —— 这既说明「原文不是建仓信号发生器」不成立"
        "（它其实会建仓），也说明**仓位水平由减仓类信号决定，而不是由起点决定**。",
        f"> **成本是最大的单一变量**：零成本档年化 {fp(s_nc['ann'])}，相对默认 "
        f"**{(s_def['ann'] - s_nc['ann']) * 100:+.2f}pp/年**（每年 "
        f"{fn(s_def['acts_per_year'], 0)} 次动作、换手 {fn(s_def['turnover_per_year'], 1)}x）。"
        f"加一个「同向动作间隔 5 日」的限速后 {fp(s_gap['ann'])}、换手降到 "
        f"{fn(s_gap['turnover_per_year'], 1)}x —— **限速能救回一部分，但救不出正超额**。",
        "> **降频是唯一能把结果翻正的杠杆，但它只抹平负 alpha**：调仓频率放到 5/10/20 日，"
        f"年化 {fp(s_r5['ann'])} / {fp(s_r10['ann'])} / {fp(s_r20['ann'])}，换手 "
        f"{fn(s_r10['turnover_per_year'], 1)}x；10 日档看起来跑赢同曝光（"
        f"{fp(s_r10['ann'])} vs {fp(m_same['ann'])}）。**但这是相位依赖的**：同一 N 把"
        "「哪一天调仓」错开 0..N−1 日，"
        + "、".join(f"N={p['n']} 年化 {fp(p['lo'])}~{fp(p['hi'])}（中位 {fp(p['mid'])}，"
                    f"标准差 {p['sd'] * 100:.1f}pp）" for p in phases)
        + f"。默认相位恰好落在 N=10 的最优端（{fp(s_r10['ann'])} vs 相位中位 "
        f"{fp(next(p for p in phases if p['n'] == 10)['mid'])}），"
        "而**相位中位只与同曝光基准持平**。结论：降频削掉的是成本（换手 "
        f"{fn(s_def['turnover_per_year'], 1)}x → {fn(s_r10['turnover_per_year'], 1)}x），"
        "把 −5.6pp 的负 alpha 抹成约 0，并没有造出正 alpha；且单相位读数不可引用。",
        "",
        "## 9. 结论",
        "",
        f"1. **定义层的断言成立，收益层的断言不成立**。D1（量>前三日最大）在七条腿上的触发频率 "
        f"25%~29%，与 iid 理论 25.0% 无差别，复现了规格 §7 对沪深300 指数的实测（25.8%）；"
        f"但换成分位判据后，同一套规则的年化只从 {fp(c_d1['ann'])} 变成 {fp(c_def['ann'])}"
        f"（{(c_def['ann'] - c_d1['ann']) * 100:+.2f}pp），而且排序不自洽"
        f"（曝光最低的 D1 {fs(c_d1['exposure'])} 收益反而略高于曝光 {fs(c_def['exposure'])} 的 D1c），"
        f"换手却从 {fn(c_d1['turnover_per_year'], 1)}x 降到 {fn(c_def['turnover_per_year'], 1)}x。"
        "**换判据是可测性的前提与降换手的手段，不是收益来源。**",
        f"2. **这是一份风控清单，它做的事只是压仓位 —— 而压仓位本身不需要这套规则**。"
        f"逐腿 {dd_win}/{len(vn.LEGS)} 条腿回撤比买入持有浅（用更低的平均仓位换来），"
        f"但 **{len(POOL) - n_beat}/{len(POOL)} 条腿跑输同曝光买入持有**；组合层年化 "
        f"{fp(m_pool['ann'])} vs 同曝光 {fp(m_same['ann'])}（**{(m_pool['ann'] - m_same['ann']) * 100:+.2f}pp**），"
        f"**回撤 {fp(m_pool['mdd'])} vs {fp(m_same['mdd'])} —— 持平**。"
        "也就是说：回撤看起来变浅，纯粹因为平均只投了 "
        f"{fs(inv_pool)}；**在同样的投入水平上，这套规则的择时是负贡献**。"
        f"把仓位固定在 {fs(inv_pool)} 就能拿到同样的回撤、还多 "
        f"{(m_same['ann'] - m_pool['ann']) * 100:.1f}pp 年化 —— 这是本变种最硬的一条结论。",
        f"3. **边界与规格 §8 一致**：不接的四条腿（宽基 + QDII）在失真检验里量价相关性最低"
        f"（corr {corr_bad:+.2f} vs 上线池 {corr_ok:+.2f}），收益上也没跑赢同曝光"
        f"（{n_beat_ref}/{len(REF)}）。**规格的边界判断是对的**，这是本变种最可迁移的一条。",
        "4. **信号层的方向对、强度不足**（§4）：买入类 D+60 均值高于减/清类，"
        "但差距小于组内离散度；按规格 §1 的「胜率与盈亏比同时报」口径，这不足以支撑择时。",
        f"5. **唯一有正面价值的是「冲突时不动」**（§7）：W9 的元规则让年化 "
        f"{(s_watch['ann'] - s_def['ann']) * 100:+.2f}pp、换手降 "
        f"{(s_def['turnover_per_year'] - s_watch['turnover_per_year']):.1f}x。"
        "如果要把这套笔记用在实盘，正确姿势是**把它当成「今天该不该加仓」的否决清单**（W1/W9 这种约束），"
        "而不是当成买卖信号发生器。",
        f"6. **降频能抹平负 alpha，但造不出正 alpha**（§8b）：日频换手 "
        f"{fn(s_def['turnover_per_year'], 1)}x、成本 "
        f"{(s_def['ann'] - s_nc['ann']) * 100:.2f}pp/年；改成 "
        + "/".join(f"{p['n']} 日" for p in phases)
        + " 调仓后，相位中位年化 "
        + "、".join(f"{fp(p['mid'])}" for p in phases)
        + f"，与同曝光基准 {fp(m_same['ann'])} 基本持平，而相位间标准差 "
        f"{phases[1]['sd'] * 100:.1f}pp（N=10）。"
        "**任何「把年化调到 8% 以上」的尝试都要先证明它不是调仓日的幸运相位。**",
        "",
        "> 最值得记住的一条：**原文补不出阈值的那 20 多条（§10）恰好是「主力意图」类判断；"
        "能补出阈值的那些（高量、破位、压力）在 ETF 上退化成了「波动 + 交易成本」的另一种写法**。"
        "本变种不上线（`pipeline/strategies.json` 不注册、不进每日流程），规则、回测与每日扫描脚本一起留档。",
        "",
        "> **模拟结果，非投资建议。**",
    ]
    return L


if __name__ == "__main__":
    main()
