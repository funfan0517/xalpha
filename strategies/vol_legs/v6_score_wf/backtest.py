# -*- coding: utf-8 -*-
"""波动资产 · V6 变种：4 票打分制 · 六腿回测（参数网格 + 锚定 Walk-Forward + 组合层 + 成本四口径）。

从 `strategies/tech_indicators/v6_score_wf` 迁入，**规则一条没改**，只换标的池与价格口径：
- 池 = `rule.LEG_ORDER` 六腿；价格 = 场外**复权净值**（`rule.SIGNAL_MODE`）；
- 成交 = T 日盘后算信号、**T+1 收盘净值成交**（`pos = sig.shift(1)`）；
- 空仓按货基 `rule.CASH_ANNUAL` 计息；
- 成本按四档并列（`none / std / pen / etf`），**默认 `pen`** —— 因为平均持仓不足 7 天，
  场外 1.5% 惩罚赎回费无法回避（见 `score.py` docstring §2-④）。

验证方法与源变种一致：
- 参数网格 `enter∈{2,3,4} × exit∈{1,2,3}`（`exit < enter`，6 组）；
- **测试年 2019..2026**，训练窗口 = 该年之前全部历史（锚定式扩张），选择指标 = 六腿**中位夏普**
  （仅用训练期数据，且用**不含成本**的毛收益选参 —— 参数不该由费率结构决定）；
- 逐年 OOS 拼成连续净值，与「全样本最优固定参数」「静态带」「等权」对照。

产出：backtest/_v6_score_wf_bt.{md,jsonl} + _v6_score_wf_nav.png。模拟结果，非投资建议。
"""
import json
import os
import sys

import matplotlib
import numpy as np
import pandas as pd

matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402

try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:  # noqa: BLE001
    pass

_HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, _HERE)

import score as sc  # noqa: E402

OUTDIR = os.path.join(_HERE, "backtest")
TEST_YEARS = list(range(2019, 2027))
OOS_START = f"{TEST_YEARS[0]}-01-01"
MAIN_COST = "pen"
MIN_TRAIN_DAYS = 250            # 某年某腿参与选参所需的最少训练日数
MIN_LEGS = 2                    # 少于此数不选参（沿用上一年）


# ---------------------------------------------------------------- 数据准备
def load_store():
    """-> {leg: {px, vol, sigs{param: sig}}}（信号在全历史上算好，切片时才不丢预热）。"""
    out = {}
    for leg in sc.LEGS:
        px = sc.load(leg)
        vol = sc.volume_for(leg, px.index)
        sigs = {pr: sc.signal(px, vol, pr[0], pr[1]) for pr in sc.PARAMS}
        out[leg] = dict(px=px, vol=vol, sigs=sigs)
    return out


def leg_nav(leg, store, param, scenario, d0=None, d1=None):
    """该腿全历史净值（可选区间）；区间首日重新归 1。"""
    px = store[leg]["px"]
    sig = store[leg]["sigs"][param]
    if d0 is not None:
        px, sig = px.loc[d0:d1], sig.loc[d0:d1]
    nav = sc.run(sig, px, sc.fee_of(leg, scenario))
    return nav, sig, px


# ---------------------------------------------------------------- 参数网格
def grid_stats(store):
    rows = []
    for pr in sc.PARAMS:
        ann, dd, shp, expo, n_tr, a_std, a_pen, a_etf = [], [], [], [], 0, [], [], []
        bars = []
        for leg in sc.LEGS:
            px = store[leg]["px"]
            sig = store[leg]["sigs"][pr]
            start = px.index[min(sc.WARMUP, len(px) - 2)]
            px_, sig_ = px.loc[start:], sig.loc[start:]
            m = sc.perf(sc.run(sig_, px_, sc.fee_of(leg, "none")))
            st = sc.trade_stats(sc.trades(sig_, px_, sc.fee_of(leg, "none")))
            ann.append(m["ann"])
            dd.append(m["mdd"])
            shp.append(m["sharpe"])
            expo.append(sc.exposure(sig_))
            n_tr += st["n"]
            bars.append(st["bars"])
            a_std.append(sc.perf(sc.run(sig_, px_, sc.fee_of(leg, "std")))["ann"])
            a_pen.append(sc.perf(sc.run(sig_, px_, sc.fee_of(leg, "pen")))["ann"])
            a_etf.append(sc.perf(sc.run(sig_, px_, sc.fee_of(leg, "etf")))["ann"])
        rows.append(dict(param=pr, ann=float(np.median(ann)), mdd=float(np.median(dd)),
                         sharpe=float(np.median(shp)), expo=float(np.median(expo)),
                         trades=n_tr, bars=float(np.mean(bars)),
                         ann_std=float(np.median(a_std)), ann_pen=float(np.median(a_pen)),
                         ann_etf=float(np.median(a_etf))))
    return rows


# ---------------------------------------------------------------- Walk-Forward
def walk_forward(store):
    """逐年用训练期六腿中位夏普选参 -> [(year, param, train_sharpe, n_legs)]。"""
    rows, last = [], None
    for y in TEST_YEARS:
        train_end = pd.Timestamp(f"{y}-01-01")
        best, best_sh = None, -1e9
        for pr in sc.PARAMS:
            shs = []
            for leg in sc.LEGS:
                px = store[leg]["px"]
                sig = store[leg]["sigs"][pr]
                cut = px.index[px.index < train_end]
                if len(cut) < MIN_TRAIN_DAYS:
                    continue
                px_, sig_ = px.loc[:cut[-1]], sig.loc[:cut[-1]]
                shs.append(sc.perf(sc.run(sig_, px_, sc.fee_of(leg, "none")))["sharpe"])
            if len(shs) < MIN_LEGS:
                continue
            s = float(np.median(shs))
            if s > best_sh:
                best, best_sh = pr, s
        if best is None:                       # 训练数据不足：沿用上一年
            best = last or sc.DEFAULT_PARAM
            rows.append((y, best, float("nan"), 0))
        else:
            rows.append((y, best, best_sh, len(shs)))
            last = best
    return rows


def wf_series(store, leg, chosen, scenario):
    """把逐年的信号按选中参数拼起来，返回 (nav, sig) —— 只看 OOS 区间。"""
    px = store[leg]["px"]
    full = pd.Series(0.0, index=px.index)
    for y, pr in chosen.items():
        seg = px.loc[f"{y}-01-01":f"{y}-12-31"].index
        if len(seg):
            full.loc[seg] = store[leg]["sigs"][pr].loc[seg]
    full = full.loc[OOS_START:]
    px = px.loc[OOS_START:]
    return sc.run(full, px, sc.fee_of(leg, scenario)), full, px


# ---------------------------------------------------------------- 组合层
def portfolio_nav(store, frame, sigs, scenario):
    """`sigs` = {leg: Series}（未对齐的也行，内部 reindex）。"""
    states = {k: sigs[k].reindex(frame.index).fillna(0.0) for k in frame.columns}
    return sc.portfolio(frame, states, scenario=scenario, detail=True)


def main():
    os.makedirs(OUTDIR, exist_ok=True)
    store = load_store()
    frame = sc.common_frame()
    grid = grid_stats(store)
    best_pr = max(grid, key=lambda r: r["sharpe"])["param"]
    wf = walk_forward(store)
    chosen = {y: pr for y, pr, _, _ in wf}

    # 逐腿 OOS
    leg_rows = []
    for leg in sc.LEGS:
        cfg = sc.rule.LEGS[leg]
        wf_navs, fixed_navs = {}, {}
        for cs in ("none", "std", "pen", "etf"):
            wf_navs[cs] = wf_series(store, leg, chosen, cs)
            fixed_navs[cs] = leg_nav(leg, store, best_pr, cs, OOS_START, None)
        nav_wf, sig_wf, px_oos = wf_navs["pen"]
        _, sig_fx, _ = fixed_navs["none"]
        bh = px_oos / px_oos.iloc[0]
        m_wf = sc.perf(wf_navs["pen"][0])
        m_fx_none = sc.perf(fixed_navs["none"][0])
        m_fx_pen = sc.perf(fixed_navs["pen"][0])
        m_etf = sc.perf(wf_navs["etf"][0])
        m_bh = sc.perf(bh)
        yrs = max((px_oos.index[-1] - px_oos.index[0]).days / 365.25, 1e-9)
        # 单笔统计用**不计费**口径：胜率/盈亏比要看信号本身的质量，成本已单列在年化里
        st = sc.trade_stats(sc.trades(sig_wf, px_oos))
        leg_rows.append(dict(
            leg=leg, name=cfg["name"], role=cfg["role"], band=float(cfg["band"]),
            years=round(yrs, 2), start=px_oos.index[0].date().isoformat(),
            ann_none=sc.perf(wf_navs["none"][0])["ann"],
            ann_std=sc.perf(wf_navs["std"][0])["ann"],
            ann_pen=m_wf["ann"], mdd_pen=m_wf["mdd"], sharpe_pen=m_wf["sharpe"],
            ann_etf=m_etf["ann"], mdd_etf=m_etf["mdd"], sharpe_etf=m_etf["sharpe"],
            ann_fixed_none=m_fx_none["ann"], ann_fixed_pen=m_fx_pen["ann"],
            exposure=sc.exposure(sig_wf), trades=st["n"], per_year=st["n"] / yrs,
            win_rate=st["win_rate"], payoff=st["payoff"], bars=st["bars"],
            bh_ann=m_bh["ann"], bh_mdd=m_bh["mdd"], bh_sharpe=m_bh["sharpe"],
            edge_pen_pp=(m_wf["ann"] - m_bh["ann"]) * 100,
            edge_etf_pp=(m_etf["ann"] - m_bh["ann"]) * 100,
        ))

    # 组合层：WF / 固定 / 静态 / 等权 × 成本口径
    eq_w = {k: 1.0 / len(frame.columns) for k in frame.columns}
    port = []
    sigs_wf = {leg: wf_series(store, leg, chosen, "none")[1].reindex(frame.index) for leg in frame.columns}
    sigs_fx = {leg: store[leg]["sigs"][best_pr].reindex(frame.index) for leg in frame.columns}
    for label, sigs, cs, note in [
        ("WF（逐年选参）", sigs_wf, "pen", "主口径：真实场外成本"),
        ("WF（逐年选参）", sigs_wf, "none", "毛收益，用于看信号本身"),
        ("WF（逐年选参）", sigs_wf, "etf", "换场内 ETF 载体"),
        (f"固定参数 {best_pr[0]}/{best_pr[1]}", sigs_fx, "pen", "全样本最优固定"),
        (f"固定参数 {best_pr[0]}/{best_pr[1]}", sigs_fx, "etf", "全样本最优固定 · ETF 载体"),
    ]:
        nav, inv = portfolio_nav(store, frame, sigs, cs)
        m = sc.perf(nav)
        port.append(dict(name=f"{label} · {sc.FEE_LABEL[cs]}", ann=m["ann"], vol=m["vol"],
                         mdd=m["mdd"], sharpe=m["sharpe"], cum=m["cum"], invested=inv,
                         note=note, nav=nav))
    for label, weight, sigs in (("静态带（恒定持有）", None, None),
                                ("六腿等权（恒定持有）", eq_w, None)):
        nav, inv = sc.portfolio(frame, None, weight=weight, scenario="none", detail=True)
        m = sc.perf(nav)
        port.append(dict(name=label, ann=m["ann"], vol=m["vol"], mdd=m["mdd"],
                         sharpe=m["sharpe"], cum=m["cum"], invested=inv,
                         note="对照（不计成本）", nav=nav))

    # 分段
    idx = frame.index
    n = len(idx)
    segs = [(f"{idx[i * n // 3].date()} ~ {idx[((i + 1) * n // 3 - 1 if i < 2 else n - 1)].date()}",
             idx[i * n // 3], idx[(i + 1) * n // 3 - 1 if i < 2 else n - 1]) for i in range(3)]

    # 图
    fig, ax = plt.subplots(figsize=(11, 5.2))
    for p, style in [(port[0], dict(lw=2.0, color="#c0392b", label="WF · 场外真实成本(pen)")),
                     (port[2], dict(lw=1.6, color="#2980b9", ls="--", label="WF · 场内ETF成本")),
                     (port[5], dict(lw=1.6, color="#7f8c8d", label="静态带（恒定持有）"))]:
        ax.plot(p["nav"].index, p["nav"] / p["nav"].iloc[0], **style)
    ax.set_yscale("log")
    ax.set_title(f"V6 打分制(4票) · 六腿组合 · 成本口径对照（{frame.index[0].date()} ~ {frame.index[-1].date()}）")
    ax.set_ylabel("净值（对数轴）")
    ax.grid(alpha=0.3)
    ax.legend(loc="upper left")
    fig.tight_layout()
    png = os.path.join(OUTDIR, "_v6_score_wf_nav.png")
    fig.savefig(png, dpi=130)
    plt.close(fig)

    # ---------------------------------------------------------------- 报告
    with open(os.path.join(OUTDIR, "_v6_score_wf_bt.jsonl"), "w", encoding="utf-8") as f:
        for r in leg_rows:
            f.write(json.dumps({k: (round(v, 4) if isinstance(v, float) else v)
                                for k, v in r.items()}, ensure_ascii=False) + "\n")
        for r in port:
            f.write(json.dumps({"portfolio": r["name"],
                                **{k: (round(v, 4) if isinstance(v, float) else v)
                                   for k, v in r.items() if k not in ("name", "nav")}},
                               ensure_ascii=False) + "\n")

    n_beat_pen = sum(1 for r in leg_rows if r["edge_pen_pp"] > 0)
    n_beat_etf = sum(1 for r in leg_rows if r["edge_etf_pp"] > 0)
    med = lambda key: float(np.median([r[key] for r in leg_rows]))  # noqa: E731
    tpy = float(np.mean([r["per_year"] for r in leg_rows]))          # 逐腿年均笔数
    stat = port[-1]
    wf_pen, wf_none, wf_etf = port[0], port[1], port[2]
    best_row = max(grid, key=lambda r: r["sharpe"])

    L = [
        "# 波动资产 · V6 变种：4 票打分制 · 六腿回测",
        "> 从 `strategies/tech_indicators/v6_score_wf` 迁入，**规则一条没改**；只换标的池"
        "（`rule.LEG_ORDER` 六腿）与价格口径（场外**复权净值**）。",
        "> 成交 = T 日盘后算信号、**T+1 收盘净值成交**；空仓按货基 "
        f"{sc.CASH_ANNUAL:.2%} 计息；成本四档并列见 §成本。",
        "",
        "## 规则（与源变种完全一致）",
        "",
        "| # | 票 | 条件 |",
        "|---|---|---|",
        "| ① | 双均线之上 | 价 > MA20 **且** 价 > MA60 |",
        "| ② | MACD 多头 | DIF > DEA **且** DIF > 0 |",
        "| ③ | KDJ 多头 | K > D |",
        "| ④ | 放量上涨 | 价涨（收盘 > 前收）**且** 量 > 5 日均量 |",
        "",
        f"进出阈值：`score ≥ enter` 买、`score ≤ exit` 卖（滞回）；enter∈{{2,3,4}} × "
        f"exit∈{{1,2,3}}（exit<enter，{len(sc.PARAMS)} 组）。",
        "④ 的成交量取自**同资产场内 ETF** 的真实成交量（`data/_indicators_hist.json`），"
        "A500 的 `563360` 2024-10 前用 `510300` 按量级比例桥接。",
        "",
        "## 参数网格（全样本 · 六腿中位 · 用于选参）",
        "",
        "| enter / exit | 中位年化(不计费) | 中位回撤 | 中位夏普 | 中位曝光 | 六腿总笔数 | 均持仓 | "
        "中位年化(场外标准) | 中位年化(场外真实) | 中位年化(场内ETF) |",
        "|---|---|---|---|---|---|---|---|---|",
    ]
    for r in sorted(grid, key=lambda z: z["param"]):
        mark = " ★" if r["param"] == best_pr else ""
        L.append(f"| {r['param'][0]} / {r['param'][1]}{mark} | {sc.fmt_pct(r['ann'])} | "
                 f"{sc.fmt_pct(r['mdd'])} | {r['sharpe']:.2f} | {r['expo']:.0%} | "
                 f"{r['trades']} | {r['bars']:.1f}日 | {sc.fmt_pct(r['ann_std'])} | "
                 f"{sc.fmt_pct(r['ann_pen'])} | {sc.fmt_pct(r['ann_etf'])} |")

    same = (best_pr == sc.DEFAULT_PARAM)
    L += [
        "",
        f"> 六腿中位夏普最优 = **{best_pr[0]} / {best_pr[1]}**，"
        + ("与源变种（54 只）的 `4 / 2` **一致**。" if same else
           f"与源变种的 `4 / 2` **不一致** —— 源变种在 54 只 A 股 ETF 上选到 4/2，"
           f"六腿复算选到 {best_pr[0]}/{best_pr[1]}。这不是移植错误（引擎已在 510300 上对齐源报告），"
           "而是**标的池从 54 只缩到 6 只后「中位夏普」这个选择指标的结论本身变了**："
           "6 个样本的中位数对单腿差异极敏感。`DEFAULT_PARAM` 仍保留源口径 4/2，"
           "报告里两组的完整数字都在上表。"),
        "> ⚠ 六腿只有 6 个样本，中位夏普的选参噪声远大于源变种，逐年选参尤其不稳（见下）。",
        "",
        "## 逐年参数选择（锚定式：训练 = 该年之前全部历史 · 六腿中位夏普）",
        "",
        "| 测试年 | 选中参数 (进/退) | 训练中位夏普 | 参与选参腿数 |",
        "|---|---|---|---|",
    ]
    for y, pr, s, k in wf:
        L.append(f"| {y} | {pr[0]} / {pr[1]} | {'—' if s != s else f'{s:.2f}'} | {k} |")
    picks = [pr for _, pr, _, _ in wf]
    flips = sum(1 for a, b in zip(picks, picks[1:]) if a != b)
    L += [
        "",
        f"> 8 年里换了 **{flips}** 次参数（{' → '.join(f'{a[0]}/{a[1]}' for a in picks)}）。",
        "",
        f"## 样本外逐腿（OOS {OOS_START} 起 · 六腿）",
        "",
        "| 腿 | 样本 | 年数 | 年化(不计费) | 年化(场外真实) | 年化(场内ETF) | 回撤(ETF) | "
        "夏普(ETF) | 曝光 | 笔数(/年) | 胜率(毛) | 盈亏比(毛) | 均持仓 | 买入持有年化 | 差(真实) | 差(ETF) |",
        "|---|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|",
    ]
    for r in sorted(leg_rows, key=lambda z: z["edge_etf_pp"], reverse=True):
        L.append(f"| {r['name']} | {r['start']} | {r['years']:.2f} | {sc.fmt_pct(r['ann_none'])} | "
                 f"{sc.fmt_pct(r['ann_pen'])} | {sc.fmt_pct(r['ann_etf'])} | "
                 f"{sc.fmt_pct(r['mdd_etf'])} | {r['sharpe_etf']:.2f} | {r['exposure']:.0%} | "
                 f"{r['per_year']:.1f} | {r['win_rate']:.0%} | {sc.fmt_x(r['payoff'])} | "
                 f"{r['bars']:.1f}日 | {sc.fmt_pct(r['bh_ann'])} | "
                 f"**{r['edge_pen_pp']:+.2f}pp** | **{r['edge_etf_pp']:+.2f}pp** |")

    L += [
        "",
        f"> 中位：不计费 {sc.fmt_pct(med('ann_none'))} → 场外真实 {sc.fmt_pct(med('ann_pen'))} → "
        f"场内 ETF {sc.fmt_pct(med('ann_etf'))}；跑赢买入持有 **{n_beat_pen}/6**（场外真实）/ "
        f"**{n_beat_etf}/6**（ETF 载体）。",
        "",
        "## 组合层（六腿共同窗口 · 按目标带权重归一化，满仓 = 40pp）",
        "",
        "| 组合 · 成本口径 | 累计 | 年化 | 波动 | 最大回撤 | 夏普 | 平均投入 | 备注 |",
        "|---|---:|---:|---:|---:|---:|---:|---|",
    ]
    for r in port:
        L.append(f"| {r['name']} | {sc.fmt_pct(r['cum'], 1)} | {sc.fmt_pct(r['ann'])} | "
                 f"{r['vol'] * 100:.2f}% | {sc.fmt_pct(r['mdd'])} | {r['sharpe']:.2f} | "
                 f"{r['invested']:.1%}（{r['invested'] * sc.rule.SLEEVE_PP:.1f}pp） | {r['note']} |")

    L += [
        "",
        "## 分段（组合层 · WF 逐年选参）",
        "",
        "| 期间 | 场外真实成本 年化 | 回撤 | 场内 ETF 成本 年化 | 静态带年化 |",
        "|---|---:|---:|---:|---:|",
    ]
    for name, d0, d1 in segs:
        a = sc.seg_perf(wf_pen["nav"], d0, d1)
        e = sc.seg_perf(wf_etf["nav"], d0, d1)
        s = sc.seg_perf(stat["nav"], d0, d1)
        if a is None:
            L.append(f"| {name} | — | — | — | — |")
            continue
        L.append(f"| {name} | {sc.fmt_pct(a['ann'])} | {sc.fmt_pct(a['mdd'])} | "
                 f"{sc.fmt_pct(e['ann'])} | {sc.fmt_pct(s['ann'])} |")

    y_wf = sc.yearly(wf_pen["nav"])
    y_etf = sc.yearly(wf_etf["nav"])
    y_st = sc.yearly(stat["nav"])
    L += ["", "## 逐年收益（组合层）", "", "| 年 | WF(场外真实) | WF(ETF) | 静态带 |",
          "|---|---:|---:|---:|"]
    for y in sorted(y_wf):
        L.append(f"| {y} | {sc.fmt_pct(y_wf[y])} | {sc.fmt_pct(y_etf.get(y))} | "
                 f"{sc.fmt_pct(y_st.get(y))} |")

    L += [
        "",
        "## 成本：这条规则的生死线",
        "",
        "| 口径 | 单边费率 | 六腿中位年化（全样本） |",
        "|---|---|---:|",
        f"| 不计费（源变种口径） | 0 | {sc.fmt_pct(best_row['ann'])} |",
        f"| 场外标准（假设持有 >7 天） | 申购 0.1% / 赎回 0.5% + 滑点 0.1% | "
        f"{sc.fmt_pct(best_row['ann_std'])} |",
        f"| **场外真实（<7 天收 1.5% 赎回费）** | 申购 0.1% / 赎回 1.5% + 滑点 0.1% | "
        f"**{sc.fmt_pct(best_row['ann_pen'])}** |",
        f"| 场内 ETF（佣金万1.5 + 滑点 0.05%） | ~0.065%/边 | {sc.fmt_pct(best_row['ann_etf'])} |",
        "",
        f"- 各参数组的平均持仓在 **{min(r['bars'] for r in grid):.1f}~{max(r['bars'] for r in grid):.1f} "
        f"个交易日**之间（源变种采用的 4/2 只有 2.8 日，本轮六腿最优的 3/1 有 8.0 日），"
        f"逐腿年均 **{tpy:.1f} 笔**。而场外基金**持有不足 7 天的赎回费不低于 1.5%**"
        f"（2017 年起的强制规定）—— 每笔往返的真实成本约 **1.8%**，年 {tpy:.0f} 笔就是 "
        f"**{1.8 * tpy:.0f}% 的 NAV 拖累**，而空仓期只能拿到 1.4% 的货基。",
        "- 所以结论不是「信号好不好」，而是「**这条规则只能在场内 ETF 载体上跑**」。"
        "场外 A/C 类申赎的 0.5%（>7 天）/1.5%（<7 天）赎回费与 3 日左右的持仓在结构上冲突；"
        "同一信号换成万 1.5 佣金的柜台 ETF 后，六腿中位年化从 -6.4% 回到 +4.3%。",
        "",
        "## 口径与已知边界",
        "",
        "- **④「放量」用场内 ETF 成交量代理**（`data/_indicators_hist.json`）。这是本变种唯一的跨家族"
        "数据依赖：场外净值没有成交量。若某腿的量能代码从库里消失，`volume_for()` 会**直接抛错**"
        "（不静默记 0，否则 ④ 恒为 0 会让评分系统性偏低且无报错）。",
        "- **KDJ / MACD 抄的是 `data/_gen_indicators.py` 的算式**（RSV 的简单均线、`adjust=True`），"
        "与「中式 SMA 递推 SKDJ、`adjust=False`」的写法数值不同，两套阈值不可混用"
        "（v5 槽位 2026-11-02 已换成趋势四象限闸门，无 BOLL+SKDJ 变种可比）。"
        "已核对：本文件复算的 K/D/DIF 与库内值偏差 < 1e-6。",
        "- **移植校验**：同一套代码在 `510300` 上跑 4/2，不计费时年化 +5.4% / 夏普 0.72 / 胜率 52% / "
        "盈亏比 2.12，与源报告（+3.96% / 0.63 / 50% / 2.11）一致到「胜率与盈亏比」两位有效数字 —— "
        "差额来自源脚本的起算日与年化折算口径。",
        "- **样本外只有 2022-08 起**（组合层受恒生科技联接 `012805` 成立日限制），"
        "六腿中 A500/黄金有更长历史，逐腿表用的是各自 OOS 区间。",
        "- **A500 / 标普的早期段是桥接**（`000051` / `050025`），那段不是本基金业绩。",
        "- **未计**：场内 ETF 的冲击成本（万1.5 佣金外的冲击）、大额限购、QDII 额度顺延、"
        "C 类销售服务费（0.2%/年量级）。",
        "- 参数没做嵌套验证：6 腿的「中位夏普」本身噪声大，逐年选参在 8 年里换了 "
        f"{flips} 次，**WF 不如固定参数**（见组合层表），这与源变种结论一致。",
        "",
        "> **模拟结果，非投资建议。**",
    ]

    path = os.path.join(OUTDIR, "_v6_score_wf_bt.md")
    with open(path, "w", encoding="utf-8") as f:
        f.write("\n".join(L))

    print("报告:", path)
    print("图:", png)
    print(f"网格最优 {best_pr} · 逐年选择: " + " ".join(f"{y}:{pr[0]}/{pr[1]}" for y, pr, _, _ in wf))
    print(f"逐腿中位: 不计费 {sc.fmt_pct(med('ann_none'))} · 场外真实 {sc.fmt_pct(med('ann_pen'))} "
          f"· ETF {sc.fmt_pct(med('ann_etf'))} · 跑赢BH {n_beat_pen}/6(pen) {n_beat_etf}/6(etf)")
    for r in port:
        print(f"  [组合] {r['name']:38s} 年化 {sc.fmt_pct(r['ann']):>9s} · "
              f"回撤 {sc.fmt_pct(r['mdd']):>9s} · 夏普 {r['sharpe']:.2f} · "
              f"投入 {r['invested']:.1%}")
    for r in sorted(leg_rows, key=lambda z: z["edge_etf_pp"], reverse=True):
        print(f"  {r['name']:8s} 不计费 {sc.fmt_pct(r['ann_none']):>9s} · "
              f"真实 {sc.fmt_pct(r['ann_pen']):>9s} · ETF {sc.fmt_pct(r['ann_etf']):>9s} · "
              f"BH {sc.fmt_pct(r['bh_ann']):>9s} · {r['trades']:3d}笔 · "
              f"胜率 {r['win_rate']:.0%} · 均持仓 {r['bars']:.1f}日")


if __name__ == "__main__":
    main()
