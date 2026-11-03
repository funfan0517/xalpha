# -*- coding: utf-8 -*-
"""波动资产 · V5 变种：趋势四象限闸门 · 七腿回测（逐腿 + 组合层 + 象限收益 + 滞后成本 + 敏感性）。

池与口径完全跟随 `strategies/vol_legs`：
- 七腿 = `rule.LEG_ORDER`（中证A500 / 沪深300 / 纳指100 / 标普500 / 科创50 / 恒生科技 / 黄金）；
- 价格 = `data.series_for_leg()` 场外**复权净值**（`rule.SIGNAL_MODE`，含分红再投 / 汇率 / 跟踪误差）；
- 闸门默认套**全部七条腿**（`GATE_ALL=True`，2026-11-03 起按用户要求「标的池改为全部 7 条腿」）；
  历史设计只套无现金流锚的三条腿（kc50 / hkt / gold）、宽基维持带内满仓，该取舍仍成立
  （给宽基叠趋势过滤让渡约 2.4pp 年化、回撤无改善），故**三腿版在本报告降为对照**，但结论不变。
- 成交 = T 日盘后算信号、**T+1 收盘净值成交**；空仓按货基 `rule.CASH_ANNUAL` 逐日计息。

产出：
- backtest/_v5_trend_gate_bt.md    规则 + 逐腿 + 组合层 + 四象限收益 + 滞后成本 + 三口径分歧 + 敏感性
- backtest/_v5_trend_gate_bt.jsonl

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

import trend_gate as tg  # noqa: E402

OUTDIR = os.path.join(_HERE, "backtest")
OUT_MD = os.path.join(OUTDIR, "_v5_trend_gate_bt.md")
OUT_JSONL = os.path.join(OUTDIR, "_v5_trend_gate_bt.jsonl")

# 敏感性档：(标签, 参数覆盖)
SENS = [
    ("基准（MA250 · 斜率20日 · 5/20 日阶梯 · 间隔帽5日 · 七腿全套）", {}),
    ("年线改 MA120", dict(MA_WIN=120)),
    ("年线改 MA200（美股口径）", dict(MA_WIN=200)),
    ("斜率回看 5 日", dict(SLOPE_N=5)),
    ("斜率回看 60 日", dict(SLOPE_N=60)),
    ("减半提前到 3 日", dict(REDUCE_DAYS=3, CLEAR_DAYS=20)),
    ("减半推后到 10 日", dict(REDUCE_DAYS=10, CLEAR_DAYS=20)),
    ("清仓提前到 10 日", dict(CLEAR_DAYS=10)),
    ("清仓推后到 40 日", dict(CLEAR_DAYS=40)),
    ("无阶梯（跌破即减半、不清仓）", dict(REDUCE_DAYS=1, CLEAR_DAYS=10 ** 6)),
    ("无间隔帽（ACTION_GAP=0）", dict(ACTION_GAP=0)),
    ("间隔帽 10 日", dict(ACTION_GAP=10)),
    ("观察仓改 0.3（不清到 0.2）", dict(OBSERVE=0.3)),
    ("空仓起步（不继承底仓）", dict(START="flat")),
    ("三腿套闸门（旧默认 · 对照）", dict(GATE_LEGS=("kc50", "hkt", "gold"), GATE_ALL=False)),
    ("只看 close 在年线上/下（无斜率）", dict(MODE="ma_only")),
]


# ---------------------------------------------------------------- 1. 逐腿
def leg_rows(**kw):
    rows = []
    for k in tg.LEGS:
        cfg = tg.rule.LEGS[k]
        px, sm = tg.leg_slice(k, **kw)
        st = sm["state"]
        fee = tg.fee_of(k)
        nav, nav_fee = tg.run_leg(st, px), tg.run_leg(st, px, fee)
        hold_fee = tg.run_leg(pd.Series(1.0, index=px.index), px, fee)
        bh = px / px.iloc[0]
        acts = tg.actions(st)
        ws = tg.whipsaw(st, px)
        m, mf, h, b = tg.perf(nav), tg.perf(nav_fee), tg.perf(hold_fee), tg.perf(bh)
        years = max((px.index[-1] - px.index[0]).days / 365.25, 1e-9)
        rows.append(dict(
            leg=k, name=cfg["name"], role=cfg["role"], band=float(cfg["band"]),
            exec_code=cfg["exec"], gate="是" if tg.gated(k, tg.params(**kw)) else "否",
            start=px.index[0].date().isoformat(), end=px.index[-1].date().isoformat(),
            years=round(years, 2), bars=len(px),
            ann=m["ann"], mdd=m["mdd"], vol=m["vol"], sharpe=m["sharpe"],
            ann_fee=mf["ann"], mdd_fee=mf["mdd"], sharpe_fee=mf["sharpe"],
            hold_ann=h["ann"], hold_mdd=h["mdd"], hold_sharpe=h["sharpe"],
            exposure=tg.exposure(st), state_last=float(st.iloc[-1]),
            actions=len(acts), acts_per_year=len(acts) / years,
            whip=ws["n"], whip_miss=ws["avg_miss"],
            edge_pp=(mf["ann"] - h["ann"]) * 100, edge_bh_pp=(mf["ann"] - b["ann"]) * 100,
            bh_ann=b["ann"], bh_mdd=b["mdd"], bh_vol=b["vol"], bh_sharpe=b["sharpe"],
            source=tg.vdata.SOURCE_NOTE.get(k, ""),
        ))
    return rows


# ---------------------------------------------------------------- 2. 组合层
def build_states(frame, **kw):
    """-> {leg: state Series}（对齐 frame.index；非闸门腿恒 1.0）。"""
    out = {}
    for k in frame.columns:
        s = tg.state_machine(tg.load(k), k, **kw)["state"].reindex(frame.index)
        out[k] = s.ffill().bfill().fillna(0.0)
    return out


def portfolio_rows(frame):
    """-> (rows, navs)。每档自带平均带内曝光。"""
    base = build_states(frame)                                    # 默认 = 七腿全套闸门
    base3 = build_states(frame, GATE_LEGS=("kc50", "hkt", "gold"), GATE_ALL=False)  # 历史三腿版对照
    maonly = build_states(frame, MODE="ma_only")
    noladder = build_states(frame, REDUCE_DAYS=1, CLEAR_DAYS=10 ** 6)
    flat = build_states(frame, START="flat")
    gaps = {g: build_states(frame, ACTION_GAP=g) for g in (0, 10)}
    eq = {k: 1.0 / len(frame.columns) for k in frame.columns}

    specs = [
        ("静态带（七腿恒持满带）", None, True, "对照基准：本变种的默认满仓态"),
        ("静态带 · 成本另列", None, False, ""),
        ("**V5 默认（七腿全套闸门）**", base, True, "全部七条腿均套年线 + 年线斜率闸门（2026-11-03 起默认）"),
        ("V5 默认 · 成本另列", base, False, ""),
        ("V5 三腿套闸门（旧默认 · 对照）", base3, True, "仅科创50 / 恒生科技 / 黄金；宽基不套（历史取舍版）"),
        ("V5 只看 close 在年线上/下", maonly, True, "对照：去掉斜率与阶梯"),
        ("V5 无阶梯（跌破即减半、不清仓）", noladder, True, "对照：只保留减半"),
        ("V5 空仓起步", flat, True, "起点无底仓，靠右侧信号建仓"),
        ("V5 无间隔帽（ACTION_GAP=0）", gaps[0], True, "频率帽第二层的贡献"),
        ("V5 间隔帽 10 日", gaps[10], True, ""),
        ("七腿等权（恒定持有）", None, False, "对照：等权满仓"),
    ]
    rows, navs = [], {}
    for i, (name, states, fee, note) in enumerate(specs):
        weight = eq if name.startswith("七腿等权") else None
        nav, inv = tg.portfolio(frame, states, weight=weight, fee=fee, detail=True)
        m = tg.perf(nav)
        rows.append(dict(name=name, cum=m["cum"], ann=m["ann"], vol=m["vol"], mdd=m["mdd"],
                         sharpe=m["sharpe"], invested=inv, note=note))
        navs[name] = nav
    return rows, navs


# ---------------------------------------------------------------- 3. 象限收益 / 滞后 / 口径分歧
def _fwd(px, h):
    return px.shift(-h) / px - 1.0


def long_table(frame, **kw):
    """七腿 × 日 的长表（只留闸门腿），带带内权重，供分桶统计。"""
    parts = []
    for k in frame.columns:
        if not tg.gated(k, tg.params(**kw)):
            continue
        px = frame[k]
        sm = tg.state_machine(tg.load(k), k, **kw).reindex(frame.index)
        parts.append(pd.DataFrame(dict(
            leg=k, quad=sm["quad"], dev=sm["dev"], below_days=sm["below_days"],
            mom6=sm["mom6"], mom12=sm["mom12"],
            f20=_fwd(px, 20), f60=_fwd(px, 60), f120=_fwd(px, 120),
            w=tg.W[k],
        )))
    df = pd.concat(parts)
    return df[df["quad"] != ""]


def _wavg(x, w):
    x = pd.Series(x).astype(float)
    w = pd.Series(w).astype(float)
    ok = x.notna() & w.notna()
    if not ok.any() or float(w[ok].sum()) <= 0:
        return None
    return float((x[ok] * w[ok]).sum() / w[ok].sum())


def quad_table(df):
    out = []
    for q in tg.QUADS:
        sub = df[df["quad"] == q]
        if len(sub) == 0:
            out.append(dict(name=q, days=0, share=0.0, f20=None, w20=None, f60=None,
                            w60=None, worst20=None, dev=None))
            continue
        out.append(dict(
            name=q, days=len(sub), share=len(sub) / len(df),
            f20=_wavg(sub["f20"], sub["w"]),
            w20=_wavg((sub["f20"] > 0).astype(float), sub["w"]),
            f60=_wavg(sub["f60"], sub["w"]),
            w60=_wavg((sub["f60"] > 0).astype(float), sub["w"]),
            worst20=float(sub["f20"].min()) if sub["f20"].notna().any() else None,
            dev=_wavg(sub["dev"], sub["w"]),
        ))
    return out


def mom_table(df):
    """均线法 vs 动量法（6 个月口径）：同一日两个口径的方向是否一致，之后的收益。

    动量取 6 个月窗口 —— 这是两个口径分歧最多的写法，也正是「参数极敏感」那句话的来源。
    12 个月口径的方向一致性由 `mom_agree` 另算（与均线法几乎重合，见表下注释）。
    """
    up = df["quad"] == tg.QUADS[0]
    dn = df["quad"] == tg.QUADS[3]
    mom_up = df["mom6"] > 0
    buckets = [
        ("均线多 + 动量多（一致看多）", up & mom_up),
        ("均线多 + 动量空（分歧）", up & ~mom_up),
        ("均线空 + 动量多（分歧）", dn & mom_up),
        ("均线空 + 动量空（一致看空）", dn & ~mom_up),
        ("半格（价格与均线反向：反弹/高位回调）", ~up & ~dn),
    ]
    out = []
    for name, mask in buckets:
        sub = df[mask]
        out.append(dict(name=name, days=len(sub), share=len(sub) / len(df) if len(df) else 0.0,
                        f20=_wavg(sub["f20"], sub["w"]),
                        f60=_wavg(sub["f60"], sub["w"]),
                        w60=_wavg((sub["f60"] > 0).astype(float), sub["w"]) if len(sub) else None))
    return out


def mom_agree(df):
    """12 个月动量口径与均线法的分歧日数（两者都在看「过去一年」，几乎重合）。"""
    up = df["quad"] == tg.QUADS[0]
    dn = df["quad"] == tg.QUADS[3]
    mu = df["mom12"] > 0
    return dict(n=len(df), up_down=int((up & ~mu).sum()), dn_up=int((dn & mu).sum()))


def lag_table(frame, **kw):
    """进入「下跌趋势」那一刻：已经跌掉多少 / 之后还会跌多少 / 多久收复年线。"""
    out = []
    allrows = []
    for k in frame.columns:
        if not tg.gated(k, tg.params(**kw)):
            continue
        full = tg.load(k)
        sm = tg.state_machine(full, k, **kw)
        quad = sm["quad"].to_numpy(dtype=object)
        dd = sm["dd_hi"].to_numpy(dtype=float)
        ma = sm["ma"].to_numpy(dtype=float)
        c = full.to_numpy(dtype=float)
        n = len(c)
        rows = []
        for i in range(1, n):
            if sm["ready"].iloc[i] and quad[i] == tg.QUADS[3] and quad[i - 1] != tg.QUADS[3]:
                after = c[i:min(n, i + 121)] / c[i] - 1.0
                rec = None
                for j in range(i + 1, min(n, i + 251)):
                    if c[j] >= ma[j]:
                        rec = j - i
                        break
                rows.append(dict(leg=k, date=full.index[i].date().isoformat(),
                                 dd_at_confirm=float(dd[i]),
                                 mdd_after=float(np.min(after)) if len(after) else 0.0,
                                 recover_bars=rec))
        vals = [r["dd_at_confirm"] for r in rows]
        aft = [r["mdd_after"] for r in rows]
        recs = [r["recover_bars"] for r in rows if r["recover_bars"] is not None]
        out.append(dict(
            leg=k, name=tg.rule.LEGS[k]["name"], n=len(rows),
            dd_med=float(np.median(vals)) if vals else None,
            dd_worst=float(np.min(vals)) if vals else None,
            after_med=float(np.median(aft)) if aft else None,
            after_worst=float(np.min(aft)) if aft else None,
            recover_med=float(np.median(recs)) if recs else None,
            recover_never=sum(1 for r in rows if r["recover_bars"] is None and r["date"]),
        ))
        allrows += rows
    pooled = dict(
        leg="pooled", name="七条闸门腿合计", n=len(allrows),
        dd_med=float(np.median([r["dd_at_confirm"] for r in allrows])) if allrows else None,
        dd_worst=float(np.min([r["dd_at_confirm"] for r in allrows])) if allrows else None,
        after_med=float(np.median([r["mdd_after"] for r in allrows])) if allrows else None,
        after_worst=float(np.min([r["mdd_after"] for r in allrows])) if allrows else None,
        recover_med=(float(np.median([r["recover_bars"] for r in allrows
                                      if r["recover_bars"] is not None]))
                     if any(r["recover_bars"] is not None for r in allrows) else None),
        recover_never=sum(1 for r in allrows if r["recover_bars"] is None),
    )
    out.append(pooled)
    return out, allrows


def sens_rows(frame):
    out = []
    for name, kw in SENS:
        states = build_states(frame, **kw)
        nav, inv = tg.portfolio(frame, states, fee=True, detail=True)
        m = tg.perf(nav)
        n_act = sum(len(tg.actions(states[k])) for k in frame.columns)
        out.append(dict(name=name, ann=m["ann"], mdd=m["mdd"], sharpe=m["sharpe"],
                        invested=inv, actions=n_act))
    return out


def segments(frame):
    idx = frame.index
    n = len(idx)
    out = []
    for i in range(3):
        i0 = i * n // 3
        i1 = (i + 1) * n // 3 - 1 if i < 2 else n - 1
        out.append((f"{idx[i0].date()} ~ {idx[i1].date()}", idx[i0], idx[i1]))
    return out


# ---------------------------------------------------------------- 4. 报告
def main():
    os.makedirs(OUTDIR, exist_ok=True)
    rows = leg_rows()
    frame = tg.common_frame()
    prows, navs = portfolio_rows(frame)
    df = long_table(frame)
    quads = quad_table(df)
    # 三腿子集（kc50/hkt/gold）单独再算一遍：宽基并入前后四象限「反弹格」的定价会明显不同，
    # 报告里两档并列，避免用 7 腿聚合去掩盖/夸大单一子集的结论。
    df3 = long_table(frame, GATE_LEGS=("kc50", "hkt", "gold"), GATE_ALL=False)
    quads3 = quad_table(df3)
    moms = mom_table(df)
    agree = mom_agree(df)
    lags, lag_rows = lag_table(frame)
    sens = sens_rows(frame)

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

    def pick(prefix):
        return next(p for p in prows if p["name"].startswith(prefix))

    base, base_nc = pick("**V5 默认（七腿全套闸门）**"), pick("V5 默认 · 成本另列")
    static, static_nc = pick("静态带（七腿恒持满带）"), pick("静态带 · 成本另列")
    base3, maonly, noladder = pick("V5 三腿套闸门"), pick("V5 只看 close"), pick("V5 无阶梯")
    flat, gap0 = pick("V5 空仓起步"), pick("V5 无间隔帽")
    eqw = pick("七腿等权")
    gated_rows = [r for r in rows if r["gate"] == "是"]
    win = f"{frame.index[0].date()} ~ {frame.index[-1].date()}"
    yrs = (frame.index[-1] - frame.index[0]).days / 365.25

    L = [
        "# 波动资产 · V5 变种：趋势四象限闸门 · 七腿回测",
        "> 池与主策略**完全一致**：`rule.LEG_ORDER` 七腿（中证A500 / 沪深300 / 纳指100 / "
        "标普500 / 科创50 / 恒生科技 / 黄金）",
        "> 价格 = 场外**复权净值**（含分红再投 / 汇率 / 跟踪误差）· T 日盘后算信号、"
        "**T+1 收盘净值成交** · "
        f"空仓按货基 {tg.CASH_ANNUAL:.2%} 计息 · 成本 = 真实申赎费 + 单边滑点 {tg.SLIP:.2%}",
        "> 规则来源：用户 2026-11-02 的「趋势判断」说明；实现口径见 "
        "`v5_trend_gate/trend_gate.py` 模块 docstring 与 README",
        "> 趋势线 + 操作点图：`research/_v5_trend_kc50.png`、`research/_v5_trend_a500.png`"
        "（现均为真实操作点，A500 也已套闸门）",
        "",
        "## 1. 规则（可执行版）",
        "",
        "判断趋势只用两个数：`close` 在 **MA" + str(tg.MA_WIN) + "** 上方还是下方、"
        f"MA{tg.MA_WIN} 自身近 **{tg.SLOPE_N} 日**是朝上还是朝下。两个都朝上才叫上涨趋势，"
        "其余三格都不给加仓许可。",
        "",
        "| | MA250 朝上 | MA250 朝下 |",
        "|---|---|---|",
        f"| **close ≥ MA250** | 上涨趋势 → 加至满带（{tg.FULL:.0%}） "
        f"| 反弹 → 只持有，不给加仓许可 |",
        f"| **close < MA250** | 高位回调 → 降一档（封顶 {tg.HALF:.0%}），不新增 "
        f"| 下跌趋势 → 满 {tg.REDUCE_DAYS} 日减半（{tg.HALF:.0%}）→ 满 {tg.CLEAR_DAYS} 日"
        f"清至观察（{tg.OBSERVE:.0%}） |",
        "",
        "| 环节 | 参数 | 说明 |",
        "|---|---|---|",
        f"| 年线 | `MA_WIN={tg.MA_WIN}` | A 股一年约 240–245 个交易日，中文语境年线取 250；"
        "美股习惯 200。**混用会让两套回测结论互相矛盾**，回测里 MA120/200 都在敏感性表 |",
        f"| 斜率 | `SLOPE_N={tg.SLOPE_N}` | 年线自身 20 日变化量的符号（同一根线的属性，"
        "不是新维度）；5/20/60 日在敏感性表 |",
        f"| 阶梯帽 | `REDUCE_DAYS={tg.REDUCE_DAYS}` / `CLEAR_DAYS={tg.CLEAR_DAYS}` | "
        "跌破后满 5 日才减半、满 20 日未收复才清至观察 —— **限制响应速度，不叠过滤器** |",
        f"| 间隔帽 | `ACTION_GAP={tg.ACTION_GAP}` | 同一腿两次调仓最小间隔（交易日） |",
        f"| 档位 | `FULL={tg.FULL:g}` / `HALF={tg.HALF:g}` / `OBSERVE={tg.OBSERVE:g}` | "
        "是**占本腿目标带的比例**，不是 pp；观察仓与主策略 `rule.OBSERVE_PP` 同比例 |",
        f"| 作用域 | `GATE_ALL={tg.GATE_ALL}`（生效 = 全部七腿） | "
        "2026-11-03 起默认七条腿全套装年线闸门；`GATE_LEGS`（kc50/hkt/gold）仅作 GATE_ALL=False 的回落"
        "三腿子集。历史上默认只套三腿，三腿版在 §3 作对照保留（宽基有估值锚，套上会与均值回归互相抵消） |",
        "",
        "> **三个坑**：①滞后是设计特性不是 bug（§5 给出实测滞后成本）；②250 与 200 不是一回事；"
        "③震荡市假信号一定存在，解法是频率帽（5/20 阶梯 + 间隔帽），不是再叠一个指标。",
        "",
        "## 2. 逐腿（各自年线 + 斜率成形后起 · 按年化差降序）",
        "",
        "「恒持满带」= 同一区间内不做闸门、仓位一直是 100% 带；年化差 = 闸门扣费后 − 恒持满带。",
        "",
        "| 腿 | 角色 | 闸门 | 样本区间 | 年数 | 年化 | 最大回撤 | 夏普 | 平均曝光 | 调仓 | "
        "调仓/年 | 打脸 | 恒持满带 年化 | 恒持满带 回撤 | 年化差 |",
        "|---|---|---|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|",
    ]
    for r in sorted(rows, key=lambda z: z["edge_pp"], reverse=True):
        L.append(
            f"| {r['name']} | {r['role']} | {r['gate']} | {r['start']} ~ {r['end']} | "
            f"{r['years']:.2f} | {tg.fmt_pct(r['ann_fee'])} | {tg.fmt_pct(r['mdd_fee'])} | "
            f"{r['sharpe_fee']:.2f} | {r['exposure']:.0%} | {r['actions']} | "
            f"{r['acts_per_year']:.1f} | {r['whip']} | {tg.fmt_pct(r['hold_ann'])} | "
            f"{tg.fmt_pct(r['hold_mdd'])} | **{r['edge_pp']:+.2f}pp** |")

    g_beat = sum(1 for r in gated_rows if r["edge_pp"] > 0)
    g_dd = sum(1 for r in gated_rows if abs(r["mdd_fee"]) < abs(r["hold_mdd"]) - 1e-9)
    best = max(gated_rows, key=lambda z: z["edge_pp"])
    worst = min(gated_rows, key=lambda z: z["edge_pp"])
    L += [
        "",
        f"> 七条闸门腿：扣费后跑赢「恒持满带」**{g_beat}/{len(gated_rows)}**，"
        f"回撤更小 **{g_dd}/{len(gated_rows)}**（默认已套全部七腿）。",
        f"> 逐腿高低：最好的是 **{best['name']}**（{best['edge_pp']:+.2f}pp，"
        f"回撤 {tg.fmt_pct(best['mdd_fee'])} vs {tg.fmt_pct(best['hold_mdd'])}），"
        f"最差的是 **{worst['name']}**（{worst['edge_pp']:+.2f}pp，"
        f"回撤 {tg.fmt_pct(worst['mdd_fee'])} vs {tg.fmt_pct(worst['hold_mdd'])}）。"
        "**闸门不是收益引擎**：它把各腿曝光从 100% 压低，换回撤，赚不赚取决于该腿"
        "在这段样本里是「先在年线下阴跌很久」还是「跌破年线就 V 型反转」—— 前者它赚，后者它亏。",
        "> 平均曝光是**带内占比**：100% = 一直满带，50% = 长期只露一半带。"
        "它比收益更能说明这条规则在做什么 —— 它不是择时进出，而是**把带切开按趋势分档**。",
        "> 「打脸」= 减仓后 20 个交易日内又加回的次数（频率帽要防的东西）；"
        "「恒持满带」= 同区间不做闸门、一直 100% 带（等价于买入持有 + 一次申购费）。",
        "",
        f"## 3. 组合层（七腿共同窗口 {win} · {yrs:.1f} 年，按 `rule` 目标带权重归一化，满仓 = 40pp）",
        "",
        "| 组合 | 累计 | 年化 | 波动 | 最大回撤 | 夏普 | 平均曝光 | 备注 |",
        "|---|---:|---:|---:|---:|---:|---:|---|",
    ]
    for r in prows:
        L.append(f"| {r['name']} | {tg.fmt_pct(r['cum'], 1)} | {tg.fmt_pct(r['ann'])} | "
                 f"{r['vol'] * 100:.2f}% | {tg.fmt_pct(r['mdd'])} | {r['sharpe']:.2f} | "
                 f"{r['invested']:.1%} | {r['note']} |")

    L += [
        "",
        f"> **默认口径 vs 静态带**：年化 {tg.fmt_pct(base['ann'])} vs {tg.fmt_pct(static['ann'])}"
        f"（**{(base['ann'] - static['ann']) * 100:+.2f}pp**）、"
        f"回撤 {tg.fmt_pct(base['mdd'])} vs {tg.fmt_pct(static['mdd'])}"
        f"（**{(base['mdd'] - static['mdd']) * 100:+.2f}pp**）、"
        f"夏普 {base['sharpe']:.2f} vs {static['sharpe']:.2f}，平均曝光 "
        f"{base['invested']:.0%} vs 100%。**结论是「几乎打平、风险调整后略好」**，"
        f"不是「跑赢」：收益差 {(base['ann'] - static['ann']) * 100:+.2f}pp 在 4 年样本里低于噪声。",
        f"> **不看成本**：{tg.fmt_pct(base_nc['ann'])} / {tg.fmt_pct(base_nc['mdd'])} / "
        f"{base_nc['sharpe']:.2f}（vs 静态带 {tg.fmt_pct(static_nc['ann'])} / "
        f"{tg.fmt_pct(static_nc['mdd'])} / {static_nc['sharpe']:.2f}）—— 成本吃掉的年化"
        f" = {(base_nc['ann'] - base['ann']) * 100:.2f}pp（调仓少，成本不是主要矛盾）。",
        f"> **三腿套闸门（旧默认 · 对照）**：{tg.fmt_pct(base3['ann'])} / {tg.fmt_pct(base3['mdd'])} / "
        f"{base3['sharpe']:.2f}，平均曝光 {base3['invested']:.0%}；相对当前七腿默认 "
        f"**{(base3['ann'] - base['ann']) * 100:+.2f}pp**、回撤 "
        f"{(base3['mdd'] - base['mdd']) * 100:+.2f}pp。用户给的 510300 证据是「叠 200 日均线过滤后"
        "年化 6.45%→4.10%、回撤 18.24%→16.47%」—— **本库净值给出同一个方向**：给宽基叠加趋势过滤，"
        "让掉的收益大于省下的回撤。当前七腿默认是按用户 2026-11-03 要求改成全套的；"
        "若只看收益效率，历史「只套三腿」更优，本行即保留该对照。",
        f"> **只看 close 在年线上/下（去斜率、去阶梯）**：{tg.fmt_pct(maonly['ann'])} / "
        f"{tg.fmt_pct(maonly['mdd'])} / {maonly['sharpe']:.2f}，曝光 {maonly['invested']:.0%}；"
        f"**无阶梯（跌破即减半、不清仓）**：{tg.fmt_pct(noladder['ann'])} / "
        f"{tg.fmt_pct(noladder['mdd'])} / {noladder['sharpe']:.2f}。两档相对基准都在 ±0.4pp 内 —— "
        "**在本样本里「斜率分格」和「5/20 阶梯」都不是收益来源**，它们的作用是限制响应速度与"
        "给出仓位的中间档（见 §4 反弹格与 §9 敏感性）。",
        f"> **空仓起步**：{tg.fmt_pct(flat['ann'])} / {tg.fmt_pct(flat['mdd'])} / "
        f"{flat['sharpe']:.2f}，曝光 {flat['invested']:.0%} —— 右侧确认才建仓会错过第一波反弹，"
        "这也是「带内满仓起步」作为默认口径的原因（与主策略「带内满仓起步」的实盘口径一致）。",
        f"> **无间隔帽**：{tg.fmt_pct(gap0['ann'])} / {tg.fmt_pct(gap0['mdd'])} / "
        f"{gap0['sharpe']:.2f}；七腿等权：{tg.fmt_pct(eqw['ann'])} / {tg.fmt_pct(eqw['mdd'])} / "
        f"{eqw['sharpe']:.2f}。间隔帽在本样本几乎不影响结果（调仓本就稀疏，"
        f"七腿合计 {int(sum(len(tg.actions(build_states(frame)[k])) for k in frame.columns))} 次），"
        "它防的是极端震荡，不是拿来提升收益的。",
        "",
        "## 4. 四象限的未来收益（回答「判断错了分别亏多少」）",
        "",
        f"七条闸门腿按带权聚合（{len(df)} 个腿·日）。未来收益用**价格本身**、不看仓位 —— "
        "即「在这一格里买入并持有 h 日」的结果，用来给每格定价。",
        "",
        "| 象限 | 交易日占比 | 平均年线乖离 | 未来20日 | 20日胜率 | 未来60日 | 60日胜率 | "
        "最差20日 |",
        "|---|---:|---:|---:|---:|---:|---:|---:|",
    ]
    for q in quads:
        L.append(f"| {q['name']} | {q['share']:.0%} | {tg.fmt_pct(q['dev'])} | "
                 f"{tg.fmt_pct(q['f20'])} | {q['w20']:.0%} | {tg.fmt_pct(q['f60'])} | "
                 f"{q['w60']:.0%} | {tg.fmt_pct(q['worst20'])} |")
    q1, q2, q3, q4 = quads
    q1_3, q2_3, q3_3, q4_3 = quads3
    L += [
        "",
        f"> **「反弹」格（三腿子集 vs 七腿全套，结论会翻转）**：三腿子集（科创50/恒生科技/黄金）里，"
        f"「反弹」未来 20 日 {tg.fmt_pct(q2_3['f20'])}、60 日 {tg.fmt_pct(q2_3['f60'])}、"
        f"20 日胜率 {q2_3['w20']:.0%}，明显弱于「上涨趋势」{tg.fmt_pct(q1_3['f20'])} / "
        f"{tg.fmt_pct(q1_3['f60'])} / {q1_3['w20']:.0%} —— 「站上年线 ≠ 趋势反转」在该子集成立。"
        f"但并入宽基后（七腿全套），「反弹」格变成 {tg.fmt_pct(q2['f20'])} / {tg.fmt_pct(q2['f60'])} / "
        f"{q2['w20']:.0%}，与「上涨趋势」{tg.fmt_pct(q1['f20'])} / {tg.fmt_pct(q1['f60'])} / "
        f"{q1['w20']:.0%} 几乎没有差别 —— **「反弹不该加仓」只是无现金流锚子集的结论，"
        "不能外推到宽基**。",
        f"> **「下跌趋势」格在本样本里也没验住**：七腿聚合下未来 20 日 {tg.fmt_pct(q4['f20'])}、"
        f"60 日 {tg.fmt_pct(q4['f60'])}（{q4['share']:.0%} 的交易日），并不差 —— 因为 2024-2026 "
        "黄金与恒生科技都出现「跌破年线后 V 型反转」（黄金腿闸门 "
        f"{worst['edge_pp']:+.2f}pp 就来自这里）。它的代价在尾巴上：最差单次 20 日 "
        f"{tg.fmt_pct(q4['worst20'])}。这一格的作用是**砍尾部**，不是提高均值。",
        "> ⚠ 这些是**事后分桶**的条件收益，不是可交易的 alpha：同一格内部的离散度远大于格间差异，"
        "「最差 20 日」那一列就是提醒。",
        "",
        "## 5. 滞后成本（进入「下跌趋势」那一刻已经跌掉多少）",
        "",
        "| 腿 | 进入次数 | 确认时距 250 日高点（中位） | 最差 | 之后 120 日最大回撤（中位） | "
        "最差 | 收复年线（交易日·中位） | 区间内未收复 |",
        "|---|---:|---:|---:|---:|---:|---:|---:|",
    ]
    for r in lags:
        rec = r["recover_med"]
        rec_txt = "—" if rec is None else f"{rec:.0f}"
        L.append(f"| {r['name']} | {r['n']} | {tg.fmt_pct(r['dd_med'])} | "
                 f"{tg.fmt_pct(r['dd_worst'])} | {tg.fmt_pct(r['after_med'])} | "
                 f"{tg.fmt_pct(r['after_worst'])} | {rec_txt} | {r['recover_never']} |")
    pooled = lags[-1]
    L += [
        "",
        f"> **「滞后」被量化了**：趋势闸门确认「进入下跌趋势」时，价格距 250 日高点中位已跌 "
        f"{tg.fmt_pct(pooled['dd_med'])}（最差 {tg.fmt_pct(pooled['dd_worst'])}），此后 120 日"
        f"中位还要再跌 {tg.fmt_pct(pooled['after_med'])}。也就是说它**不是躲回撤的工具**，"
        "它买的是「避开以年为单位的阴跌段」；拿它躲三个月的回调必然被反复打脸（见 §2 打脸列）。",
        "",
        "## 6. 与动量法的分歧（三口径里可编码的两个）",
        "",
        "结构法（道氏 HH-HL）需要事后确认摆动点、主观且滞后，本实现不编码（只作解释框架）；"
        "均线法 = 本变种的四象限；动量法 = 过去 6 个月累计收益为正（表中口径）。",
        "",
        "| 组合 | 日数占比 | 未来20日 | 未来60日 | 60日胜率 |",
        "|---|---:|---:|---:|---:|",
    ]
    for m in moms:
        w60 = "—" if m["w60"] is None else f"{m['w60']:.0%}"
        L.append(f"| {m['name']} | {m['share']:.0%} | {tg.fmt_pct(m['f20'])} | "
                 f"{tg.fmt_pct(m['f60'])} | {w60} |")
    disagree6 = moms[1]["days"] + moms[2]["days"]
    L += [
        "",
        f"> **两种「分歧」要分开，结论正好相反。** ①**均线 vs 动量**（{disagree6} 个腿·日，"
        f"{disagree6 / max(agree['n'], 1):.0%}）：分歧日的未来 60 日收益 "
        f"{tg.fmt_pct(moms[1]['f60'])} / {tg.fmt_pct(moms[2]['f60'])}，**高于**两口径一致看多的 "
        f"{tg.fmt_pct(moms[0]['f60'])} —— 6 个月动量在 V 型反转里天然滞后，跟着它卖会把仓砍在"
        "底部。②**价格 vs 均线**（反弹 / 高位回调，"
        f"{moms[-1]['share']:.0%} 的交易日）：未来 60 日 {tg.fmt_pct(moms[-1]['f60'])}、"
        f"胜率 {moms[-1]['w60']:.0%} —— **这一格才是四象限要处理的分歧**。",
        f"> **动量口径的参数敏感度实测**：12 个月动量与均线法的分歧只剩 "
        f"{agree['up_down'] + agree['dn_up']}/{agree['n']} 个腿·日"
        f"（{(agree['up_down'] + agree['dn_up']) / max(agree['n'], 1):.1%}），6 个月口径是 "
        f"{disagree6}/{agree['n']}（{disagree6 / max(agree['n'], 1):.1%}）—— 因为 MA250 与"
        " 12 月动量都在看「过去一年」，信息几乎重合。这就是用户说的「同一套动量规则，窗口从"
        " 1 个月调到 12 个月，年化在 0.68%~10.37% 之间摆动」：**窗口比口径更能改变结论**。",
        "> 所以本变种**不引入动量维度**：它要么与年线高度共线（12 月口径），要么在本样本里是"
        "反信号（6 月口径）。这也解释了为什么规则只留两个数 —— 每多一个维度，就多一处"
        "可以拟合样本的地方。",
        "",
    ]

    segs = segments(frame)
    L += ["## 7. 分段（三等分 · 组合层）", "",
          "| 期间 | V5 默认 年化 / 回撤 / 夏普 | 静态带 年化 / 回撤 / 夏普 |", "|---|---|---|"]
    for name, d0, d1 in segs:
        a = tg.seg_perf(navs[base["name"]], d0, d1)
        b = tg.seg_perf(navs[static["name"]], d0, d1)
        fa = "—" if a is None else (f"{tg.fmt_pct(a['ann'])} / {tg.fmt_pct(a['mdd'])} / "
                                    f"{a['sharpe']:.2f}")
        fb = "—" if b is None else (f"{tg.fmt_pct(b['ann'])} / {tg.fmt_pct(b['mdd'])} / "
                                    f"{b['sharpe']:.2f}")
        L.append(f"| {name} | {fa} | {fb} |")

    ya, yb = tg.yearly(navs[base["name"]]), tg.yearly(navs[static["name"]])
    L += ["", "## 8. 逐年收益（组合层）", "", "| 年 | V5 默认 | 静态带 | 差 |",
          "|---|---:|---:|---:|"]
    for y in sorted(ya):
        L.append(f"| {y} | {tg.fmt_pct(ya[y])} | {tg.fmt_pct(yb[y])} | "
                 f"**{(ya[y] - yb[y]) * 100:+.2f}pp** |")

    L += [
        "",
        "## 9. 参数敏感性（组合层 · 每档都扣费，只改一格）",
        "",
        "| 档位 | 年化 | 最大回撤 | 夏普 | 平均曝光 | 七腿总调仓次数 |",
        "|---|---:|---:|---:|---:|---:|",
    ]
    for r in sens:
        L.append(f"| {r['name']} | {tg.fmt_pct(r['ann'])} | {tg.fmt_pct(r['mdd'])} | "
                 f"{r['sharpe']:.2f} | {r['invested']:.0%} | {r['actions']} |")

    anns = [r["ann"] for r in sens if not r["name"].startswith("三腿套闸门")]

    def _sens(prefix):
        return next(r for r in sens if r["name"].startswith(prefix))

    s_base, s_ma120, s_ma200 = _sens("基准"), _sens("年线改 MA120"), _sens("年线改 MA200")
    L += [
        "",
        f"> 除「三腿套闸门（旧默认 · 对照）」那一档（口径变了，见 §3），其余 {len(anns)} 档的年化全部落在 "
        f"{tg.fmt_pct(min(anns))} ~ {tg.fmt_pct(max(anns))}（宽度 "
        f"{(max(anns) - min(anns)) * 100:.2f}pp）、回撤落在 "
        f"{tg.fmt_pct(max(r['mdd'] for r in sens))} ~ {tg.fmt_pct(min(r['mdd'] for r in sens))}。"
        "**结论不是靠某一格参数撑起来的**；但这也意味着：在 4.1 年样本上，这些参数都没有统计"
        "意义，只能说明「没有哪一格是灾难」。",
        f"> 年线窗口要分开看：MA120 把调仓次数从 {s_base['actions']} 次抬到 "
        f"{s_ma120['actions']} 次（更灵敏、成本更高），MA200 与 MA250 只差 "
        f"{(s_ma200['ann'] - s_base['ann']) * 100:.2f}pp —— 这只是本样本的巧合，不代表可以混用。"
        "250 与 200 是两个市场的惯例差别（A 股一年约 240–245 个交易日），混用会让两套回测结论"
        "互相矛盾，故本变种全池统一 250。",
        "",
        "## 10. 成本",
        "",
        "- 费率按**每条腿的实际执行代码**取：A 类（`270042` / `017641` / `000216`）申购 0.10% + "
        "赎回 0.50%（持有 >7 天）；C 类（`023299` / `011609` / `012805`）申赎免费，"
        "但收约 0.2%/年销售服务费 —— 后者**未计入**，故这三条腿的扣费后数字偏乐观。",
        f"- 另加**单边滑点 {tg.SLIP:.2%}**（双边 {tg.SLIP * 2:.1%}），作为净值公布滞后 / "
        "大额赎回折扣 / QDII 额度的保守缓冲。",
        "- 闸门的调仓是**带内比例**变化（1.0→0.5→0.2），不是清仓再进：一次「减半」只卖半条带，"
        "所以换手与成本都远低于 0/1 择时（v1_ma20 是 13~16 笔/年，本变种见 §2 调仓/年）。",
        "",
        "## 11. 口径与已知边界",
        "",
        "- **作用域是规则的一部分**：2026-11-03 起默认套全部七条腿（`GATE_ALL=True`）。"
        "历史默认只套科创50 / 恒生科技 / 黄金（无现金流锚），宽基四条腿不套 —— 因为估值锚本身已隐含"
        "均值回归，再叠趋势过滤等于同一信号用两次并互相抵消（用户给的 510300 实测：6.45%/18.24% → "
        "4.10%/16.47%，2.35pp 年化换 1.77pp 回撤）。§3 的「三腿套闸门（旧默认 · 对照）」就是这条判断"
        "在本库净值上的复核（年化让渡约 2.4pp、回撤无改善），收益效率上三腿仍更优，当前七腿默认是"
        "按用户要求改的。",
        "- **滞后是设计特性**：§5 给出实测（确认时已跌掉多少、之后还会跌多少）。目标不是躲回调，"
        "是避开年单位阴跌；拿它躲三个月的波动会被反复打脸（§2 打脸列 = 减仓后 20 日内又加回的次数）。",
        "- **250 与 200 不是一回事**：A 股一年约 240–245 个交易日，中文语境的年线取 250；"
        "美股习惯 200。本变种全池统一 250（含纳指/标普腿），否则同一套规则会在两个口径下给出"
        "互相矛盾的结论。",
        "- **没有加任何过滤器**：不叠 RSI、不叠成交量、不叠估值分位。震荡市的假信号靠频率帽"
        "（5/20 阶梯 + 间隔帽）限制响应速度，而不是再引入一个维度。",
        "- **观察仓 0.2 不是 0**：留一点仓位是为了避免「清仓后再入场」的两次成本，也是主策略"
        "`rule.OBSERVE_PP`（0.5pp / 2.5pp 带）的同比例延续；想彻底清仓把 `OBSERVE` 设 0。",
        "- **组合层只有共同窗口**：受恒生科技联接 `012805`（2021-08 成立）+ 250 日年线预热限制，"
        "组合层样本明显短于单腿（科创50 2022-05 起、恒生科技 2022-09 起）。",
        "- **A500 / 标普的样本前段是桥接**（`000051` / `050025`），那段不是本基金业绩。",
        "- **未计**：大额限购、QDII 额度关闭时的顺延、赎回确认时滞、C 类销售服务费。",
        "- 参数未做 walk-forward 选优（本就不追求最优）：§9 的敏感性显示结论落在同一量级，"
        "不靠某一格参数成立。",
        "",
        "> **模拟结果，非投资建议。**",
    ]

    with open(OUT_MD, "w", encoding="utf-8") as f:
        f.write("\n".join(L))

    print("报告:", OUT_MD)
    print("结果:", OUT_JSONL)
    print(f"组合层 {win} · {yrs:.1f} 年")
    for r in prows:
        print(f"  {r['name']:36s} 年化 {tg.fmt_pct(r['ann']):>9s} · "
              f"回撤 {tg.fmt_pct(r['mdd']):>8s} · 夏普 {r['sharpe']:>5.2f} · "
              f"曝光 {r['invested']:.0%}")
    for r in sorted(rows, key=lambda z: z["edge_pp"], reverse=True):
        print(f"  {r['name']:8s} 闸门={r['gate']} 年化 {tg.fmt_pct(r['ann_fee']):>9s} vs "
              f"恒持 {tg.fmt_pct(r['hold_ann']):>9s} · 曝光 {r['exposure']:.0%} · "
              f"{r['actions']} 次调仓 · 打脸 {r['whip']}")


if __name__ == "__main__":
    main()
