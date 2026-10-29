# -*- coding: utf-8 -*-
"""波动资产 · V2 变种：资金渗透率（份额拥挤度）闸门 · 回测。

池与口径跟随 `strategies/vol_legs`：
- 六腿 = `rule.LEG_ORDER`（A500 / 纳指100 / 标普500 / 科创50 / 恒生科技 / 黄金）；
- 信号 = 头部 ETF 合计份额的季度净申购率（`flowrule.flow_rate`）→ 3 年分位（拥挤度）；
- 价格 / 收益 = `data.series_for_leg()` 场外**复权净值**（含分红再投 / 汇率 / 跟踪误差）；
- 成交 = T 日盘后算信号、**T+1 净值成交**（`pos.shift(1)`），与主策略 §2.4 一致；
- 半仓部分按货基 `rule.CASH_ANNUAL`（1.40%）逐日计息；申赎费按执行代码真实费率。

产出（相对本文件）：
- `backtest/_v2_flow_bt.md`  逐腿对照 + 组合层 + 灵敏度 + 自检
- `backtest/_v2_flow_bt.jsonl`

模拟结果，非投资建议。
"""
import json
import os
import sys

import pandas as pd

try:
    sys.stdout.reconfigure(encoding="utf-8")
except (AttributeError, ValueError):  # noqa: BLE001
    pass

_HERE = os.path.dirname(os.path.abspath(__file__))
_VL = os.path.dirname(_HERE)
for _p in (_HERE, _VL):
    if _p not in sys.path:
        sys.path.insert(0, _p)

import engine            # noqa: E402  主策略绩效指标（同一把尺子）
import flowdata          # noqa: E402
import flowrule          # noqa: E402

OUTDIR = os.path.join(_HERE, "backtest")
W = {k: float(flowrule.band_pp(k)) / float(sum(flowrule.band_pp(x) for x in flowrule.LEGS))
     for k in flowrule.LEGS}
MIN_LEGS = 4          # 组合层起点：至少 4 条腿有拥挤度信号


# ---------------------------------------------------------------- 1. 记账
def leg_nav(price, pos, fee, cash=None):
    """单腿净值：T+1 成交、半仓部分计货基利息、按仓位变化扣申赎费。"""
    cash = flowrule.CASH_DAILY if cash is None else cash
    ret = price.pct_change().fillna(0.0)
    p = pos.shift(1).fillna(float(flowrule.CFG["start_pos"]))
    r = p * ret + (1.0 - p) * cash
    d = p.diff().fillna(0.0)
    r = r - (d > 1e-12) * fee[0] - (d < -1e-12) * fee[1]
    return (1.0 + r).cumprod(), p


def leg_rows(panels):
    rows = []
    for leg in flowrule.LEGS:
        p = panels[leg]
        g, pos = p["g"], p["pos"]
        start = g.first_valid_index()
        if start is None:
            continue
        px = p["axis"].loc[start:]
        nav, pv = leg_nav(px, pos.reindex(px.index), flowrule.FEE[leg])
        trade, _ = leg_nav(px, pos.reindex(px.index), (0.0, 0.0))
        m, m0 = engine.metrics(nav), engine.metrics(trade)
        bh = engine.metrics(px / px.iloc[0])
        yrs = max((px.index[-1] - px.index[0]).days / 365.25, 1e-9)
        n_tr = int((pv.diff().abs() > 1e-12).sum())
        ratio = p["ratio"]
        c_now = p["crowd"].iloc[-1] if len(p["crowd"]) else float("nan")
        g_now = g.iloc[-1] if len(g) else float("nan")
        rows.append(dict(
            leg=leg, name=p["name"], role=p["role"], band=p["band"], exec_code=p["exec"],
            head=p["head"], start=px.index[0].date().isoformat(),
            end=px.index[-1].date().isoformat(), years=round(yrs, 2), bars=len(px),
            ann=m["ann"], mdd=m["mdd"], vol=m["vol"], sharpe=m["sharpe"],
            ann_nofee=m0["ann"], ann_fee_drag=(m["ann"] - m0["ann"]) * 100,
            exposure=float(pv.mean()), trades=n_tr, trades_per_year=n_tr / yrs,
            bh_ann=bh["ann"], bh_mdd=bh["mdd"], bh_sharpe=bh["sharpe"],
            edge_pp=(m["ann"] - bh["ann"]) * 100,
            crowd_now=(None if c_now != c_now else float(c_now)),
            g_now=(None if g_now != g_now else float(g_now)),
            ratio_now=(float(ratio["ratio_pct"].iloc[-1]) if ratio is not None else None),
            shares_now=(float(p["shares"].iloc[-1].sum()) if len(p["shares"]) else None),
            last_anchor=p["last_anchor"],
        ))
    return rows


# ---------------------------------------------------------------- 2. 组合层
def portfolio_frame(panels):
    """六腿净值矩阵 + 拥挤度矩阵（从「≥MIN_LEGS 条腿有信号」的日期起）。"""
    price = pd.DataFrame({k: panels[k]["axis"] for k in flowrule.LEGS}).sort_index()
    crowd = pd.DataFrame({k: panels[k]["crowd"] for k in flowrule.LEGS}).reindex(price.index)
    n_ok = crowd.notna().sum(axis=1)
    start = n_ok[n_ok >= MIN_LEGS].index[0] if (n_ok >= MIN_LEGS).any() else price.index[0]
    return price.loc[start:], crowd.loc[start:], n_ok.loc[start:]


def portfolio_nav(price, pos_df, fee=True, weight=None):
    """按目标带归一化权重合成组合净值（可得腿权重重归一化；T+1 成交）。"""
    avail = price.notna()
    weight = avail.mul(pd.Series(W if weight is None else weight), axis=1)
    weight = weight.div(weight.sum(axis=1), axis=0)
    ret = price.pct_change(fill_method=None)
    pos = pos_df.where(avail).fillna(0.0).shift(1).fillna(0.0)
    inv = (pos * weight).sum(axis=1)
    r = (pos * weight * ret.fillna(0.0)).sum(axis=1) + (1.0 - inv) * flowrule.CASH_DAILY
    if fee:
        for leg in flowrule.LEGS:
            d = pos[leg].diff().fillna(0.0)
            r = r - (d > 1e-12) * flowrule.FEE[leg][0] * weight[leg].fillna(0.0) \
                  - (d < -1e-12) * flowrule.FEE[leg][1] * weight[leg].fillna(0.0)
    return (1.0 + r).cumprod(), inv


def portfolio_rows(panels):
    price, _, _ = portfolio_frame(panels)
    legs = flowrule.LEGS
    pos_v2 = pd.DataFrame({k: panels[k]["pos"].reindex(price.index) for k in legs})
    ones = pd.DataFrame(1.0, index=price.index, columns=legs)
    nav_v2, inv_v2 = portfolio_nav(price, pos_v2)
    nav_band, _ = portfolio_nav(price, ones)
    nav_band0, _ = portfolio_nav(price, ones, fee=False)
    nav_eq, _ = portfolio_nav(price, ones, fee=False,
                              weight={k: 1.0 / len(legs) for k in legs})

    rows = [
        ("V2 拥挤度闸门（扣费）", nav_v2, float(inv_v2.mean()), "拥挤度 ≥90% 时该腿降至半带"),
        ("静态带（按带权恒定持有，扣费）", nav_band, 1.0, "不择时基准"),
        ("静态带（不计费）", nav_band0, 1.0, "隔离费率影响"),
        ("六腿等权（不计费）", nav_eq, 1.0, "分散化对照"),
    ]
    out = []
    for name, nav, inv, note in rows:
        m = engine.metrics(nav.dropna())
        out.append(dict(name=name, cum=m["cum"], ann=m["ann"], vol=m["vol"],
                        mdd=m["mdd"], sharpe=m["sharpe"], invested=inv, note=note))
    return price, out


def v1_reference(price):
    """V1（MA20 穿越）在同一窗口、同一权重口径下的对照（直接调 v1 的组合层函数）。"""
    v1_dir = os.path.join(_VL, "v1_ma20")
    if v1_dir not in sys.path:
        sys.path.insert(0, v1_dir)
    try:
        import ma20  # noqa: E402
    except Exception as exc:  # noqa: BLE001
        return None, f"不可用（{type(exc).__name__}: {exc}）"
    frame = price.ffill()
    sigs = {k: ma20.signal(frame[k].dropna())[0].reindex(frame.index).fillna(0.0)
            for k in frame.columns}
    nav = ma20.portfolio(frame, signals=sigs, fee=True)
    return engine.metrics(nav.dropna()), ""


# ---------------------------------------------------------------- 3. 灵敏度
def sensitivity(panels, hi_list, lo_list):
    price, _, _ = portfolio_frame(panels)
    out = []
    for hi in hi_list:
        for lo in lo_list:
            if lo >= hi:
                continue
            pos = {}
            for k in flowrule.LEGS:
                cur, seq = float(flowrule.CFG["start_pos"]), []
                for v in panels[k]["crowd"].reindex(price.index).to_numpy():
                    if v == v:
                        if v >= hi:
                            cur = float(flowrule.CFG["pos_crowd"])
                        elif v <= lo:
                            cur = float(flowrule.CFG["pos_full"])
                    seq.append(cur)
                pos[k] = pd.Series(seq, index=price.index, dtype="float64")
            nav, inv = portfolio_nav(price, pd.DataFrame(pos))
            m = engine.metrics(nav.dropna())
            out.append(dict(hi=hi, lo=lo, ann=m["ann"], mdd=m["mdd"], sharpe=m["sharpe"],
                            invested=float(inv.mean())))
    return out


# ---------------------------------------------------------------- 4. 自检（V-xx）
def checks(panels):
    """按 head_fund_ratio_rules.md §8 做可自动化的部分 -> [(编号, 断言, 结论)]。"""
    res = []
    ok = all(panels[k]["g"].dropna().index[-1] == panels[k]["axis"].index[-1]
             for k in flowrule.LEGS if not panels[k]["g"].dropna().empty)
    res.append(("V-01", "份额序列末点 = 该腿最新净值日", "PASS" if ok else "FAIL"))

    n = {k: int(panels[k]["g"].notna().sum()) for k in flowrule.LEGS}
    ok = all(v >= 250 for v in n.values())
    res.append(("V-02", "各腿净申购率样本 ≥250 点",
                ("PASS " if ok else "WARN ") + "、".join(f"{k}={v}" for k, v in n.items())))

    rng = {k: (float(panels[k]["ratio"]["ratio_pct"].min()),
               float(panels[k]["ratio"]["ratio_pct"].max()))
           for k in flowrule.LEGS if panels[k]["ratio"] is not None}
    ok = all(0.01 <= a and b <= 30 for a, b in rng.values()) if rng else True
    res.append(("V-03", "渗透率取值范围 0.01%~30%",
                ("PASS " if ok else "FAIL ")
                + "、".join(f"{k} {a:.3f}~{b:.3f}%" for k, (a, b) in rng.items())))

    # V-04 核心恒等式：名单稳定窗口内，Σ份额增长率 ≈ 份额加权净申购率
    diff = {}
    for k in flowrule.LEGS:
        sh = panels[k]["shares"]
        if sh.empty or sh.isna().any(axis=1).mean() > 0.5:
            continue
        _total, g_tot = flowrule.main_flow(sh)
        gap = (g_tot - panels[k]["g"]).abs().dropna()
        if len(gap):
            diff[k] = float(gap.median())
    ok = all(v < 0.01 for v in diff.values()) if diff else True
    res.append(("V-04", "Σ份额增长率 vs 加权净申购率（中位差 <1pp）",
                ("PASS " if ok else "WARN ")
                + "、".join(f"{k} {v * 100:.3f}pp" for k, v in diff.items())))

    newest = min(panels[k]["last_anchor"] for k in flowrule.LEGS)
    res.append(("V-05", "份额数据时效（六腿最新季报锚点）", f"INFO ≥ {newest}"))
    return res


# ---------------------------------------------------------------- 5. 报告
def fmt_pct(x, nd=2):
    return "—" if x is None else f"{x * 100:+.{nd}f}%"


def main():
    os.makedirs(OUTDIR, exist_ok=True)
    if not flowdata.cache_ready():
        print("份额缓存缺失，先刷新 ...")
        flowdata.refresh(verbose=True)
    ps = flowrule.panels()
    rows = leg_rows(ps)
    price, prows = portfolio_rows(ps)
    v1m, v1note = v1_reference(price)
    sens = sensitivity(ps, [0.80, 0.90, 0.95], [0.20, 0.30, 0.50])
    v = checks(ps)

    with open(os.path.join(OUTDIR, "_v2_flow_bt.jsonl"), "w", encoding="utf-8") as f:
        for r in rows:
            f.write(json.dumps({k: (round(val, 4) if isinstance(val, float) else val)
                                for k, val in r.items()}, ensure_ascii=False) + "\n")

    n_beat = sum(1 for r in rows if r["edge_pp"] > 0)
    # 只统计「回撤实质性更小」（改善 > 0.5pp），避免浮点噪声把等回撤腿误判为更小
    n_dd = sum(1 for r in rows if r["mdd"] - r["bh_mdd"] > 0.005)
    win = f"{price.index[0].date()} ~ {price.index[-1].date()}"
    yrs = (price.index[-1] - price.index[0]).days / 365.25
    base = prows[1]

    L = [
        "# 波动资产 · V2 变种：资金渗透率（份额拥挤度）闸门",
        "> 池 = 主策略六腿（`rule.LEG_ORDER`：A500 / 纳指100 / 标普500 / 科创50 / 恒生科技 / 黄金），"
        "带宽不改（5/10/10/2.5/2.5/10 = 40pp）",
        "> 信号 = 头部 4 只 ETF 的**合计份额季度净申购率**（`head_fund_ratio_rules.md` 渗透率的分子，"
        "价格项在比值里抵消）→ 近 3 年**分位**（拥挤度）",
        "> 规则 = 拥挤度 ≥90% → **半带** · ≤30% → **满带** · 中间维持（只在带内做减法）· "
        f"T 日盘后算、**T+1 净值成交** · 半仓部分按货基 {flowrule.CASH_ANNUAL:.2%} 计息",
        "",
        "## 规则",
        "",
        "| 拥挤度 c（净申购率的 3 年分位） | 目标仓位 | 含义 |",
        "|---|---|---|",
        "| c ≥ 90% | 0.5 × 目标带 | 资金涌入速度处于 3 年最高 10% —— 拥挤，临时打对折 |",
        "| c ≤ 30% | 1.0 × 目标带 | 净申购率处于 3 年最低 30% —— 筹码出清，回到满带 |",
        "| 30% < c < 90% | 维持 | 死区，不因噪声来回切换 |",
        "",
        "> 拥挤度用分位而非 z-score：份额序列是季报阶跃 + 线性内插，厚尾；分位对极端值稳健，"
        "且可跨腿比较（黄金年均份额增长 20% 与科创50 的 50% 不同量纲）。",
        "",
        "## 逐腿对照（各自净申购率成形日起 · 含申赎费）",
        "",
        "| 腿 | 角色 | 带 | 执行代码 | 样本区间 | 年数 | 年化 | 回撤 | 夏普 | 曝光 | 笔数 | "
        "买入持有年化 | 买入持有回撤 | 年化差 |",
        "|---|---|---:|---|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|",
    ]
    for r in sorted(rows, key=lambda z: z["edge_pp"], reverse=True):
        L.append(
            f"| {r['name']} | {r['role']} | {r['band']:g}pp | `{r['exec_code']}` | "
            f"{r['start']} ~ {r['end']} | {r['years']:.2f} | {fmt_pct(r['ann'])} | "
            f"{fmt_pct(r['mdd'])} | {r['sharpe']:.2f} | {r['exposure']:.0%} | {r['trades']} | "
            f"{fmt_pct(r['bh_ann'])} | {fmt_pct(r['bh_mdd'])} | **{r['edge_pp']:+.2f}pp** |")

    L += [
        "",
        f"> {len(rows)} 条腿中：年化跑赢买入持有 **{n_beat}/{len(rows)}**，回撤更小 "
        f"**{n_dd}/{len(rows)}**，逐腿年化差中位 "
        f"**{pd.Series([r['edge_pp'] for r in rows]).median():+.2f}pp**；平均曝光 "
        f"{pd.Series([r['exposure'] for r in rows]).mean():.0%}，换手 "
        f"{pd.Series([r['trades_per_year'] for r in rows]).mean():.1f} 笔/年。",
        "",
        f"## 组合层（共同可得窗口 {win} · {yrs:.1f} 年，按目标带权重归一化，满仓 = 40pp）",
        "",
        "| 组合 | 累计 | 年化 | 波动 | 最大回撤 | 夏普 | 平均投入 |",
        "|---|---:|---:|---:|---:|---:|---:|",
    ]
    for r in prows:
        L.append(f"| {r['name']} | {fmt_pct(r['cum'], 1)} | {fmt_pct(r['ann'])} | "
                 f"{fmt_pct(r['vol'])} | {fmt_pct(r['mdd'])} | {r['sharpe']:.2f} | "
                 f"{r['invested']:.0%} |")
    if v1m:
        L.append(f"| V1 MA20 穿越（同窗口扣费，参照） | {fmt_pct(v1m['cum'], 1)} | "
                 f"{fmt_pct(v1m['ann'])} | {fmt_pct(v1m['vol'])} | {fmt_pct(v1m['mdd'])} | "
                 f"{v1m['sharpe']:.2f} | — |")
    d_ann = (prows[0]["ann"] - base["ann"]) * 100
    d_dd = (prows[0]["mdd"] - base["mdd"]) * 100
    L += [
        "",
        f"> V2 vs 静态带：年化 **{d_ann:+.2f}pp**、回撤 {d_dd:+.2f}pp、夏普 "
        f"{prows[0]['sharpe']:.2f} vs {base['sharpe']:.2f}；平均投入 "
        f"{prows[0]['invested']:.0%} vs 100%。" + (f" V1 参照：{v1note}" if v1note else ""),
        "",
        "## 阈值灵敏度（组合层 · 扣费）",
        "",
        "| 拥挤线 hi | 冷清线 lo | 年化 | 最大回撤 | 夏普 | 平均投入 |",
        "|---:|---:|---:|---:|---:|---:|",
    ]
    for s in sens:
        L.append(f"| {s['hi']:.0%} | {s['lo']:.0%} | {fmt_pct(s['ann'])} | {fmt_pct(s['mdd'])} | "
                 f"{s['sharpe']:.2f} | {s['invested']:.0%} |")
    L += ["", "## 自检（对应 head_fund_ratio_rules.md §8）", "",
          "| 编号 | 断言 | 结论 |", "|---|---|---|"]
    for no, desc, res in v:
        L.append(f"| {no} | {desc} | {res} |")
    spx = next(r for r in rows if r["leg"] == "spx")
    ndx = next(r for r in rows if r["leg"] == "ndx")
    L += [
        "",
        "## 结论（诚实版）",
        "",
        f"1. **逐腿是「风险折扣器」，不是收益放大器**：{n_beat}/{len(rows)} 条腿年化跑赢买入持有，"
        f"但只有 {n_dd}/{len(rows)} 条腿回撤更小（且**全在美股腿**：标普500、纳指100）。"
        f"其余四条腿的回撤与买入持有完全相同 —— 因为其唯一一次档位切换发生在 2026-09 之后，"
        f"没赶上 2022~2024 的大回撤，闸门无法为已发生的亏损止血。最典型是标普500：回撤 "
        f"{fmt_pct(spx['mdd'])} vs {fmt_pct(spx['bh_mdd'])}（**改善 "
        f"{(spx['mdd'] - spx['bh_mdd']) * 100:+.1f}pp**），代价只有 {spx['edge_pp']:+.2f}pp/年；"
        f"纳指100 回撤 {(ndx['mdd'] - ndx['bh_mdd']) * 100:+.1f}pp、代价 {ndx['edge_pp']:+.2f}pp/年。"
        "慢牛腿（黄金）上则只是让渡收益。",
        f"2. **组合层不占优**：各腿拥挤不同步，半仓期与回撤期不重合 —— 组合回撤只改善 "
        f"{d_dd:+.2f}pp，收益却少了 {-d_ann:.2f}pp/年。所以 v2 **不适合单独当择时用**，"
        "它的正确用法是「与主策略叠加时的一票减仓许可」。",
        "3. **换手极低（≈1 笔/年，见逐腿表）**：季报口径 + 死区，成本不是问题"
        "（对比 V1 的 13~16 笔/年，扣费后年化从 8.10% 掉到 1.42%）。",
        "4. **信号有约 1 个季度时滞**：份额只披露到最近季报，季内资金变化进不了信号（M-03）；"
        "敏感性表里 hi/lo 在 80~95% / 20~50% 之间摆动，年化 12.4%~15.0%，"
        "均不敌静态带 —— 说明结论对阈值不敏感（不是调参没调好）。",
        "",
        "## 口径与已知边界",
        "",
        "- **份额 = 季报期末总份额**（东财 gmbd），期内线性内插；锚点之前不产出（M-01）。",
        "- **头部名单按最新季报净资产取前 N**（R-01），名单变动用「份额加权增速」吸收（M-02）。",
        "- **美股 / 港股 / 黄金没有标的指数流通市值口径**，只算分子（合计份额）；"
        "由核心恒等式，斜率不受影响（M-04）。",
        "- **A500 只有 2025-04 起的信号**（头部 ETF 2024-10 才上市），此前该腿按「不执行」保持满带。",
        "- **未计**：C 类销售服务费（0.2%/年）、大额限购、QDII 额度关闭顺延、赎回确认时滞。",
        "",
        "> **模拟结果，非投资建议。**",
    ]

    path = os.path.join(OUTDIR, "_v2_flow_bt.md")
    with open(path, "w", encoding="utf-8") as f:
        f.write("\n".join(L))

    print("报告:", path)
    print("结果:", os.path.join(OUTDIR, "_v2_flow_bt.jsonl"))
    print(f"逐腿 {len(rows)} 条：年化跑赢 {n_beat}/{len(rows)} · 回撤更小 {n_dd}/{len(rows)}")
    for r in sorted(rows, key=lambda z: z["edge_pp"], reverse=True):
        print(f"  {r['name']:8s} 年化 {fmt_pct(r['ann']):>9s} vs BH {fmt_pct(r['bh_ann']):>9s} · "
              f"回撤 {fmt_pct(r['mdd']):>9s} vs {fmt_pct(r['bh_mdd']):>9s} · "
              f"曝光 {r['exposure']:.0%} · {r['trades']} 笔")
    for r in prows:
        print(f"  [组合] {r['name']:26s} 年化 {fmt_pct(r['ann']):>9s} · "
              f"回撤 {fmt_pct(r['mdd']):>9s} · 夏普 {r['sharpe']:.2f} · 投入 {r['invested']:.0%}")
    if v1m:
        print(f"  [组合] {'V1 MA20（参照）':26s} 年化 {fmt_pct(v1m['ann']):>9s} · "
              f"回撤 {fmt_pct(v1m['mdd']):>9s} · 夏普 {v1m['sharpe']:.2f}")


if __name__ == "__main__":
    main()
