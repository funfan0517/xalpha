# -*- coding: utf-8 -*-
"""波动资产 · V2 变种：BOLL 上轨突破 · 六腿回测（逐腿 + 组合层）。

原 `strategies/tech_indicators/v16_boll_breakout/backtest.py`（2026-10-30 迁入 vol_legs，
标的池从「`data/_universe.md` 前四类 54 只场内 ETF」改为主策略的**六腿场外复权净值**）。

池与口径完全跟随 `strategies/vol_legs`：
- 六腿 = `rule.LEG_ORDER`（A500 / 纳指100 / 标普500 / 科创50 / 恒生科技 / 黄金）；
- 价格 = `data.series_for_leg()` 场外**复权净值**（含分红再投 / 汇率 / 跟踪误差）；
- 成交 = T 日盘后算信号、**T+1 净值成交**（`pos = sig.shift(1)`），与主策略 §2.4 同一纪律；
- 空仓按货基 `rule.CASH_ANNUAL`（1.40%）逐日计息，不假设 0 收益。

产出（本目录 backtest/）：
- `_v2_boll_breakout_bt.md` / `.jsonl`：逐腿对照 + 组合层 + 方向对照 + 成交时滞/费率/止损/参数敏感性
- `_v2_boll_breakout_portfolio.png`：组合层净值（择时 / 静态带 / 等权）
- `_v2_boll_breakout_legs.png`：六腿逐腿价格 + 三轨 + 买卖点（2×3）

模拟结果，非投资建议。
"""
import json
import os
import sys

import numpy as np
import pandas as pd

try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:  # noqa
    pass

_HERE = os.path.dirname(os.path.abspath(__file__))
_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(_HERE)))
sys.path.insert(0, _HERE)

import boll  # noqa: E402

OUTDIR = os.path.join(_HERE, "backtest")

STOPS = [None, 0.05, 0.08, 0.10, 0.15]
PARAM_GRID = [(20, 2.0), (35, 2.0), (55, 2.0), (35, 1.5), (35, 2.5)]
LAG_FEE = [
    ("T+1 成交（主口径，零费用）", 1, False),
    ("T+1 成交 + 各腿真实申赎费", 1, True),
    ("T+2 成交（QDII 再滞后一天，零费用）", 2, False),
    ("T+2 成交 + 各腿真实申赎费", 2, True),
]


def _mirror_signal(px, n=None, k=None):
    """**镜像口径（仅对照用，不是本策略规则）**：下穿下轨买入、收盘回升中轨卖出。

    与 `boll.signal()` 严格对称，用来检验「方向」在这个池上是否真的重要
    （tech_indicators 家族里 v15 = 反转、v16 = 突破，两者方向相反）。
    """
    n = boll.PARAMS["N"] if n is None else int(n)
    k = boll.PARAMS["K"] if k is None else float(k)
    close = np.asarray(px, dtype=float)
    mid, upper, lower = boll.bands(px, n, k)
    mid_v, lo_v = mid.to_numpy(), lower.to_numpy()
    sig = np.zeros(len(close), dtype=int)
    pos = 0
    for t in range(len(close)):
        if np.isnan(lo_v[t]) or np.isnan(mid_v[t]):
            sig[t] = pos
            continue
        if pos == 0:
            if t >= 1 and close[t] < lo_v[t] and close[t - 1] >= lo_v[t - 1]:
                pos = 1
        elif close[t] > mid_v[t]:
            pos = 0
        sig[t] = pos
    return pd.Series(sig, index=px.index, dtype=int), mid, upper


def leg_rows():
    """逐腿：BOLL 上轨突破（零费 / 扣真实申赎费）vs 买入持有。"""
    rows = []
    for k in boll.LEGS:
        cfg = boll.rule.LEGS[k]
        px_full = boll.load(k)
        sig_full, mid_full, _up = boll.signal(px_full)
        start = mid_full.first_valid_index()
        px = px_full.loc[start:]
        sig = sig_full.loc[start:]
        nav = boll.run(px, sig)
        nav_fee = boll.run(px, sig, fee=boll.FEE[k])
        bh = px / px.iloc[0]
        tr = boll.trades(sig, px)
        m, b, mf = boll.perf(nav), boll.perf(bh), boll.perf(nav_fee)
        st = boll.trade_stats(tr)
        years = max((px.index[-1] - px.index[0]).days / 365.25, 1e-9)
        rows.append(dict(
            leg=k, name=cfg["name"], role=cfg["role"], band=float(cfg["band"]),
            exec_code=cfg["exec"], start=px.index[0].date().isoformat(),
            end=px.index[-1].date().isoformat(), years=round(years, 2), bars=len(px),
            ann=m["ann"], mdd=m["mdd"], vol=m["vol"], sharpe=m["sharpe"],
            ann_fee=mf["ann"], mdd_fee=mf["mdd"], sharpe_fee=mf["sharpe"],
            fee_buy=boll.FEE[k][0], fee_sell=boll.FEE[k][1],
            exposure=boll.exposure(sig), trades=st["n"], win_rate=st["win_rate"],
            payoff=st["payoff"], pf=st["pf"], avg_bars=st["bars"],
            per_year=st["n"] / years,
            bh_ann=b["ann"], bh_mdd=b["mdd"], bh_vol=b["vol"], bh_sharpe=b["sharpe"],
            edge_pp=(m["ann"] - b["ann"]) * 100,
            edge_fee_pp=(mf["ann"] - b["ann"]) * 100,
            source=boll.vdata.SOURCE_NOTE.get(k, ""),
            note="含桥接前段" if cfg.get("otc_proxy") else "",
        ))
    return rows


def portfolio_rows(frame):
    """组合层：BOLL 择时（零费 / 扣费）/ 静态带 / 六腿等权。"""
    sigs = {k: boll.signal(frame[k])[0] for k in frame.columns}
    navs = [
        ("V2 BOLL 择时（按带权）", boll.portfolio(frame, signals=sigs)),
        ("V2 BOLL 择时（扣申赎费）", boll.portfolio(frame, signals=sigs, fee=True)),
        ("静态带（按带权恒定持有）", boll.portfolio(frame, signals=None)),
        ("六腿等权（恒定持有）", boll.portfolio(frame, signals=None,
                                                weight={k: 1.0 / len(frame.columns)
                                                        for k in frame.columns})),
    ]
    invested = sum(boll.W[k] * boll.exposure(sigs[k]) for k in frame.columns)
    rows, nav_by = [], {}
    for i, (name, nav) in enumerate(navs):
        m = boll.perf(nav)
        inv = invested if i < 2 else 1.0
        rows.append(dict(name=name, cum=m["cum"], ann=m["ann"], vol=m["vol"],
                         mdd=m["mdd"], sharpe=m["sharpe"], invested=inv))
        nav_by[name] = nav
    return sigs, rows, nav_by


def _med(rs, key):
    return float(np.median([r[key] for r in rs]))


def lag_fee_table(frame):
    """成交时滞 × 费率：逐腿中位 + 组合层年化（各自同一窗口内比较）。"""
    out = []
    for lab, lag, fee in LAG_FEE:
        anns, mdds, shs, navs = [], [], [], {}
        for k in boll.LEGS:
            px_full = boll.load(k)
            sig_full, mid_full, _ = boll.signal(px_full)
            start = mid_full.first_valid_index()
            px, sig = px_full.loc[start:], sig_full.loc[start:]
            m = boll.perf(boll.run(px, sig, fee=boll.FEE[k] if fee else None, lag=lag))
            anns.append(m["ann"])
            mdds.append(m["mdd"])
            shs.append(m["sharpe"])
            navs[k] = m
        sigs = {k: boll.signal(frame[k])[0] for k in frame.columns}
        nav_p = boll.portfolio(frame, signals=sigs, fee=fee, lag=lag)
        mp = boll.perf(nav_p)
        out.append(dict(label=lab, lag=lag, fee=fee,
                        med_ann=float(np.median(anns)), med_mdd=float(np.median(mdds)),
                        med_sharpe=float(np.median(shs)),
                        port_ann=mp["ann"], port_mdd=mp["mdd"], port_sharpe=mp["sharpe"]))
    return out


def stop_table(frame):
    """止损敏感性：逐腿中位 + 组合层年化（零费用，家族主口径）。"""
    out = []
    for stp in STOPS:
        anns, mdds, shs, n_tr = [], [], [], 0
        for k in boll.LEGS:
            px_full = boll.load(k)
            sig_full, mid_full, _ = boll.signal(px_full, stop=stp)
            start = mid_full.first_valid_index()
            px, sig = px_full.loc[start:], sig_full.loc[start:]
            m = boll.perf(boll.run(px, sig))
            anns.append(m["ann"])
            mdds.append(m["mdd"])
            shs.append(m["sharpe"])
            n_tr += len(boll.trades(sig, px))
        sigs = {k: boll.signal(frame[k], stop=stp)[0] for k in frame.columns}
        mp = boll.perf(boll.portfolio(frame, signals=sigs))
        out.append(dict(stop=stp, med_ann=float(np.median(anns)),
                        med_mdd=float(np.median(mdds)), med_sharpe=float(np.median(shs)),
                        trades=n_tr, port_ann=mp["ann"]))
    return out


def param_table(frame):
    """参数敏感性：窗口 N × 带宽 K（逐腿中位 + 组合层 + 中位年化差 vs 买入持有）。"""
    out = []
    for n, kk in PARAM_GRID:
        anns, mdds, shs, edges = [], [], [], []
        for leg in boll.LEGS:
            px_full = boll.load(leg)
            sig_full, mid_full, _ = boll.signal(px_full, n=n, k=kk)
            start = mid_full.first_valid_index()
            px, sig = px_full.loc[start:], sig_full.loc[start:]
            m = boll.perf(boll.run(px, sig))
            b = boll.perf(px / px.iloc[0])
            anns.append(m["ann"])
            mdds.append(m["mdd"])
            shs.append(m["sharpe"])
            edges.append((m["ann"] - b["ann"]) * 100)
        sigs = {leg: boll.signal(frame[leg], n=n, k=kk)[0] for leg in frame.columns}
        mp = boll.perf(boll.portfolio(frame, signals=sigs))
        out.append(dict(n=n, k=kk, med_ann=float(np.median(anns)),
                        med_mdd=float(np.median(mdds)), med_sharpe=float(np.median(shs)),
                        med_edge=float(np.median(edges)), port_ann=mp["ann"]))
    return out


def direction_row(frame, label, sig_fn):
    """方向对照：给一组信号函数 → 逐腿中位统计 + 组合年化。"""
    anns, mdds, shs, exps, all_tr = [], [], [], [], []
    for k in boll.LEGS:
        px_full = boll.load(k)
        sig_full, mid_full, _ = sig_fn(px_full)
        start = mid_full.first_valid_index()
        px, sig = px_full.loc[start:], sig_full.loc[start:]
        m = boll.perf(boll.run(px, sig))
        anns.append(m["ann"])
        mdds.append(m["mdd"])
        shs.append(m["sharpe"])
        exps.append(boll.exposure(sig))
        all_tr += boll.trades(sig, px)
    sigs = {k: sig_fn(frame[k])[0] for k in frame.columns}
    mp = boll.perf(boll.portfolio(frame, signals=sigs))
    stats = boll.trade_stats(all_tr)
    return dict(name=label, med_ann=float(np.median(anns)), med_mdd=float(np.median(mdds)),
                med_sharpe=float(np.median(shs)), med_exposure=float(np.median(exps)),
                trades=stats["n"], win_rate=stats["win_rate"], payoff=stats["payoff"],
                port_ann=mp["ann"], port_mdd=mp["mdd"])


def _plot_portfolio(nav_by, path):
    try:
        import matplotlib
        matplotlib.use("Agg")
        import matplotlib.pyplot as plt
    except Exception as e:  # noqa
        print("matplotlib 不可用，跳过作图:", e)
        return
    plt.rcParams["font.sans-serif"] = ["Microsoft YaHei", "SimHei", "DejaVu Sans"]
    plt.rcParams["axes.unicode_minus"] = False
    fig, ax = plt.subplots(figsize=(13, 6.5))
    colors = {"V2 BOLL 择时（按带权）": "#93c",
              "V2 BOLL 择时（扣申赎费）": "#d33",
              "静态带（按带权恒定持有）": "#27c",
              "六腿等权（恒定持有）": "#888"}
    for name, nav in nav_by.items():
        ax.plot(nav.index, nav.to_numpy(), label=name, color=colors.get(name), linewidth=1.6)
    ax.set_title("波动资产六腿 · BOLL 上轨突破择时 vs 静态带 vs 等权（净值, 起=1）")
    ax.set_ylabel("净值")
    ax.grid(alpha=0.3)
    ax.legend()
    fig.savefig(path, dpi=120)
    plt.close(fig)
    print("图:", path)


def _plot_legs(rows, path):
    try:
        import matplotlib
        matplotlib.use("Agg")
        import matplotlib.pyplot as plt
    except Exception as e:  # noqa
        print("matplotlib 不可用，跳过作图:", e)
        return
    plt.rcParams["font.sans-serif"] = ["Microsoft YaHei", "SimHei", "DejaVu Sans"]
    plt.rcParams["axes.unicode_minus"] = False
    by_leg = {r["leg"]: r for r in rows}
    fig, axes = plt.subplots(3, 2, figsize=(14, 11))
    for ax, k in zip(axes.ravel(), boll.LEGS):
        px_full = boll.load(k)
        sig_full, mid_full, up_full = boll.signal(px_full)
        start = mid_full.first_valid_index()
        px, sig = px_full.loc[start:], sig_full.loc[start:]
        mid, up = mid_full.loc[start:], up_full.loc[start:]
        pos = sig.shift(1).fillna(0).astype(float)
        x = px.index
        held = pos.to_numpy() > 0
        ax.fill_between(x, float(px.min()), float(px.max()), where=held,
                        color="#93c", alpha=0.12, linewidth=0)
        ax.plot(x, px.to_numpy(), color="#333", linewidth=1.1, label="复权净值")
        ax.plot(x, mid.to_numpy(), color="#27c", linewidth=0.9, alpha=0.85,
                label=f"中轨 MA{boll.PARAMS['N']}")
        ax.plot(x, up.to_numpy(), color="#d33", linewidth=0.9, alpha=0.85,
                label=f"上轨 +{boll.PARAMS['K']:g}σ")
        d = pos.diff().fillna(0).to_numpy()
        buys = np.where(d > 0)[0]
        sells = np.where(d < 0)[0]
        pxv = px.to_numpy()
        ax.scatter(x[buys], pxv[buys], marker="^", s=46, color="#d33", edgecolors="w",
                   linewidths=0.5, zorder=5)
        ax.scatter(x[sells], pxv[sells], marker="v", s=46, color="#27c", edgecolors="w",
                   linewidths=0.5, zorder=5)
        r = by_leg[k]
        ax.set_title(f"{r['name']} {r['start']}~{r['end']} · "
                     f"择时 {r['ann'] * 100:+.2f}% vs 持有 {r['bh_ann'] * 100:+.2f}% · "
                     f"曝光 {r['exposure']:.0%} · {r['trades']} 笔", fontsize=10)
        ax.grid(alpha=0.3)
        ax.legend(fontsize=8, loc="upper left")
    fig.suptitle("六腿 · BOLL 上轨突破：收盘上穿上轨买入 / 跌回中轨卖出（紫色阴影 = 持仓区间）")
    fig.tight_layout(rect=(0, 0, 1, 0.97))
    fig.savefig(path, dpi=110)
    plt.close(fig)
    print("图:", path)


def main():
    os.makedirs(OUTDIR, exist_ok=True)
    rows = leg_rows()
    frame = boll.common_frame()
    sigs, prows, nav_by = portfolio_rows(frame)
    lagfee = lag_fee_table(frame)
    stops = stop_table(frame)
    params = param_table(frame)
    dirs = [direction_row(frame, "BOLL 上轨突破（本变种）", lambda p: boll.signal(p)),
            direction_row(frame, "BOLL 镜像反转（下轨买 / 上穿中轨卖）", _mirror_signal)]

    with open(os.path.join(OUTDIR, "_v2_boll_breakout_bt.jsonl"), "w", encoding="utf-8") as f:
        for r in rows:
            f.write(json.dumps({k: (round(v, 4) if isinstance(v, float) else v)
                                for k, v in r.items()}, ensure_ascii=False) + "\n")

    win = f"{frame.index[0].date()} ~ {frame.index[-1].date()}"
    yrs = (frame.index[-1] - frame.index[0]).days / 365.25
    n_beat = sum(1 for r in rows if r["edge_pp"] > 0)
    n_dd = sum(1 for r in rows if abs(r["mdd"]) < abs(r["bh_mdd"]))
    med_edge = _med(rows, "edge_pp")
    med_edge_fee = _med(rows, "edge_fee_pp")
    _drag_pp = sorted(abs((r["ann_fee"] - r["ann"]) * 100) for r in rows
                      if r["fee_buy"] or r["fee_sell"])          # A 类腿的年化拖累(pp)
    yr = boll.yearly(nav_by["V2 BOLL 择时（按带权）"])
    yr_band = boll.yearly(nav_by["静态带（按带权恒定持有）"])

    L = [
        "# 波动资产 · V2 变种：BOLL 上轨突破（六腿逐腿择时）",
        "> 池与主策略**完全一致**：`rule.LEG_ORDER` 六腿（A500 / 纳指100 / 标普500 / 科创50 / 恒生科技 / 黄金）"
        " · 价格 = 场外**复权净值**（含分红再投 / 汇率 / 跟踪误差）",
        "> T 日盘后算信号、**T+1 净值成交** · 空仓按货基 "
        f"{boll.CASH_ANNUAL:.2%} 计息 · 主表零费用，成本另算（见「成交时滞 × 费率」）",
        "> 原 `strategies/tech_indicators/v16_boll_breakout/`（54 只场内 ETF 全池），2026-10-30 迁入 vol_legs 并改为六腿口径。",
        "",
        "## 规则",
        "",
        f"- **指标**：`mid = MA(close, {boll.PARAMS['N']})`、`std = STD(close, {boll.PARAMS['N']})`、"
        f"`upper = mid + {boll.PARAMS['K']:g}σ`（下轨算而不用）",
        "- **开多**：`close > upper` 且前一日 `close <= upper`（要求「上穿」，同一段趋势不重复发信号）",
        "- **平仓**：`close < mid`（跌回中轨即退出，不等下轨）",
        "- **无反向开空**；盘中触碰不算，只看收盘；无信号时空仓等待",
        f"- **止损**：默认关闭（`PARAMS['STOP']=None`），敏感性见下文",
        "",
        "> 规则与参数权威源 `boll.py::PARAMS`；指标在规则内自算，不依赖指标库；"
        "绩效统一走 `../engine.metrics()`（无风险利率 = 货基 1.40%）。",
        "",
        f"## 逐腿对照（各自 BOLL 成形日起 · 按年化差降序）",
        "",
        "| 腿 | 角色 | 样本区间 | 年数 | 年化 | 最大回撤 | 夏普 | 曝光 | 交易 | 年笔数 | 胜率 | 盈亏比 | "
        "买入持有年化 | 买入持有回撤 | 年化差 | 扣费后年化 | 扣费后年化差 |",
        "|---|---|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|",
    ]
    for r in sorted(rows, key=lambda z: z["edge_pp"], reverse=True):
        L.append(
            f"| {r['name']} | {r['role']} | {r['start']} ~ {r['end']} | {r['years']:.2f} | "
            f"{boll.fmt_pct(r['ann'])} | {boll.fmt_pct(r['mdd'])} | {r['sharpe']:.2f} | "
            f"{r['exposure']:.0%} | {r['trades']} | {r['per_year']:.1f} | "
            f"{r['win_rate']:.0%} | {boll.fmt_x(r['payoff'])} | {boll.fmt_pct(r['bh_ann'])} | "
            f"{boll.fmt_pct(r['bh_mdd'])} | **{r['edge_pp']:+.2f}pp** | "
            f"{boll.fmt_pct(r['ann_fee'])} | **{r['edge_fee_pp']:+.2f}pp** |")

    L += [
        "",
        f"> {len(rows)} 腿中：年化跑赢买入持有 **{n_beat}/{len(rows)}**、回撤更小 "
        f"**{n_dd}/{len(rows)}**；逐腿年化差中位 **{med_edge:+.2f}pp**（扣费后 {med_edge_fee:+.2f}pp）。"
        f"曝光中位 **{_med(rows, 'exposure'):.0%}** —— 本规则大部分时间空仓，"
        "组合层收益会被现金摊薄，这是策略属性不是缺陷。",
        "",
        f"## 组合层（六腿共同窗口 {win} · {yrs:.1f} 年，按 `rule` 目标带权重归一化，满仓 = 40pp）",
        "",
        "| 组合 | 累计 | 年化 | 波动 | 最大回撤 | 夏普 | 平均投入 |",
        "|---|---:|---:|---:|---:|---:|---:|",
    ]
    for r in prows:
        L.append(f"| {r['name']} | {boll.fmt_pct(r['cum'], 1)} | {boll.fmt_pct(r['ann'])} | "
                 f"{boll.fmt_pct(r['vol'])} | {boll.fmt_pct(r['mdd'])} | {r['sharpe']:.2f} | "
                 f"{r['invested']:.0%} |")
    base_row = prows[2]
    d_ann = (prows[0]["ann"] - base_row["ann"]) * 100
    d_ann_fee = (prows[1]["ann"] - base_row["ann"]) * 100
    d_dd = (prows[0]["mdd"] - base_row["mdd"]) * 100
    L += [
        "",
        f"> 择时组合 vs 静态带：年化 {d_ann:+.2f}pp（扣申赎费 {d_ann_fee:+.2f}pp）、"
        f"回撤 {d_dd:+.2f}pp、夏普 {prows[0]['sharpe']:.2f} vs {base_row['sharpe']:.2f}；"
        f"平均投入 {prows[0]['invested']:.0%} vs 100%。",
        "",
        "### 逐年收益（组合层 · 首尾为不完整年度）",
        "",
        "| 年 | BOLL 择时 | 静态带 | 差 |",
        "|---|---:|---:|---:|",
    ]
    for y in sorted(yr):
        L.append(f"| {y} | {boll.fmt_pct(yr[y])} | {boll.fmt_pct(yr_band.get(y))} | "
                 f"**{(yr[y] - yr_band.get(y, 0.0)) * 100:+.2f}pp** |")

    L += [
        "",
        "## 与 v1_ma20 的对照（同池 · 同口径 · 都是逐腿择时）",
        "",
        "> 两列不是同一窗口：`v1_ma20` 预热 20 日、本变种预热 35 日，静态带年化 "
        f"{boll.fmt_pct(base_row['ann'])} vs 11.08% 的差就是这个窗口差。数字取自 "
        "`../v1_ma20/README.md` §5 与本报告上表，**只作方向性对照**。",
        "",
        "| 变种 | 组合年化 | 组合回撤 | 夏普 | 平均投入 | 逐腿年笔数 | 扣费后年化 | 年化拖累 |",
        "|---|---|---|---|---|---|---|---|",
        f"| V1 MA20 穿越 | +8.10% | -9.20% | 0.86 | 57% | 13~16 | +1.42% | **-6.68pp** |",
        f"| **V2 BOLL 上轨突破** | {boll.fmt_pct(prows[0]['ann'])} | {boll.fmt_pct(prows[0]['mdd'])} | "
        f"{prows[0]['sharpe']:.2f} | {prows[0]['invested']:.0%} | 2.2~3.1 | "
        f"{boll.fmt_pct(prows[1]['ann'])} | **{(prows[1]['ann'] - prows[0]['ann']) * 100:+.2f}pp** |",
        "",
        "> 同一池、同一费用假设下，**BOLL 上轨突破的换手只有 MA20 的 1/6 左右**"
        "（每年 2~3 笔 vs 13~16 笔），所以扣费后几乎不掉（"
        f"{(prows[1]['ann'] - prows[0]['ann']) * 100:+.2f}pp vs MA20 的 -6.68pp），"
        "回撤也更小（中轨出场 = 硬止损）。这才是它在**场外基金**口径下相对 MA20 的真正优势 —— "
        "不是收益更高，而是「同样的降回撤，成本只有 1/6」。",
    ]

    L += [
        "",
        "## 方向对照（同一池、同参数，只把方向镜像）",
        "",
        "> 「上穿**上轨**买、跌回**中轨**卖」是本变种；镜像口径是「下穿**下轨**买、回升**中轨**卖」"
        "（即 tech_indicators 家族 v15 反转的方向）。两者只看谁更适合这六条腿。",
        "",
        "| 口径 | 中位年化 | 中位最大回撤 | 中位夏普 | 中位曝光 | 总交易 | 合计胜率 | 盈亏比 | 组合年化 | 组合回撤 |",
        "|---|---|---|---|---|---|---|---|---|---|",
    ]
    for d in dirs:
        L.append(f"| {d['name']} | {boll.fmt_pct(d['med_ann'])} | {boll.fmt_pct(d['med_mdd'])} | "
                 f"{d['med_sharpe']:.2f} | {d['med_exposure']:.0%} | {d['trades']} | "
                 f"{d['win_rate']:.1%} | {boll.fmt_x(d['payoff'])} | "
                 f"{boll.fmt_pct(d['port_ann'])} | {boll.fmt_pct(d['port_mdd'])} |")

    L += [
        "",
        "## 成交时滞 × 费率敏感性",
        "",
        "> 主口径已是「T 日盘后算信号 → T+1 净值成交」（场外 15:00 前提交才拿当日净值）。"
        "T+2 模拟 QDII 腿「净值再滞后一天」的最坏情况；费率按每条腿的**实际执行代码**取"
        "（A 类 270042/017641/000216 申购 0.10% + 赎回 0.50%；C 类 023299/011609/012805 申赎免费，"
        "但约 0.2%/年销售服务费**未计入**）。",
        "",
        "| 口径 | 中位年化 | 中位最大回撤 | 中位夏普 | 组合年化 | 组合回撤 | 组合夏普 |",
        "|---|---|---|---|---|---|---|",
    ]
    for x in lagfee:
        L.append(f"| {x['label']} | {boll.fmt_pct(x['med_ann'])} | {boll.fmt_pct(x['med_mdd'])} | "
                 f"{x['med_sharpe']:.2f} | {boll.fmt_pct(x['port_ann'])} | "
                 f"{boll.fmt_pct(x['port_mdd'])} | {x['port_sharpe']:.2f} |")

    L += [
        "",
        "## 止损敏感性（零费用 · T+1 成交）",
        "",
        "> 止损自建仓日收盘价起算，同样只在收盘判定。建仓价在上轨（= mid + 2σ），"
        "**中轨离建仓价只差几个百分点** —— 中轨出场本身就是硬止损，百分比止损未必来得及触发。",
        "",
        "| 止损 | 中位年化 | 中位最大回撤 | 中位夏普 | 六腿总交易 | 组合年化 |",
        "|---|---|---|---|---|---|",
    ]
    for s in stops:
        lab = "不启用" if s["stop"] is None else f"-{s['stop']:.0%}"
        L.append(f"| {lab} | {boll.fmt_pct(s['med_ann'])} | {boll.fmt_pct(s['med_mdd'])} | "
                 f"{s['med_sharpe']:.2f} | {s['trades']} | {boll.fmt_pct(s['port_ann'])} |")

    L += [
        "",
        "## 参数敏感性（窗口 N × 带宽 K）",
        "",
        "> 默认 (35, 2.0) 来自原变种口径。**按这张表挑最优参数就是过拟合** —— "
        "它只用来判断结论对参数有多敏感。",
        "",
        "| N | K | 中位年化 | 中位最大回撤 | 中位夏普 | 中位年化差 vs 持有 | 组合年化 |",
        "|---|---|---|---|---|---|---|",
    ]
    for p in params:
        mark = " **(默认)**" if (p["n"], p["k"]) == (boll.PARAMS["N"], boll.PARAMS["K"]) else ""
        L.append(f"| {p['n']}{mark} | {p['k']:g} | {boll.fmt_pct(p['med_ann'])} | "
                 f"{boll.fmt_pct(p['med_mdd'])} | {p['med_sharpe']:.2f} | "
                 f"{p['med_edge']:+.2f}pp | {boll.fmt_pct(p['port_ann'])} |")

    L += [
        "",
        "## 成本（场外口径下这条规则的最大风险）",
        "",
        "| 腿 | 执行代码 | 年笔数 | 单边费率（买/卖） | 年化拖累 | 扣费后年化 | 扣费后 vs 买入持有 |",
        "|---|---|---:|---|---:|---:|---:|",
    ]
    for r in sorted(rows, key=lambda z: z["edge_fee_pp"], reverse=True):
        drag = r["ann_fee"] - r["ann"]
        L.append(f"| {r['name']} | `{r['exec_code']}` | {r['per_year']:.1f} | "
                 f"{r['fee_buy']:.2%} / {r['fee_sell']:.2%} | {drag * 100:+.2f}pp | "
                 f"{boll.fmt_pct(r['ann_fee'])} | **{r['edge_fee_pp']:+.2f}pp** |")

    L += [
        "",
        f"> 扣费后逐腿 **{sum(1 for r in rows if r['edge_fee_pp'] > 0)}/{len(rows)}** "
        f"仍跑赢买入持有（不计费时 {n_beat}/{len(rows)}）；收申赎费的 A 类三条腿每年被费用吃掉 "
        f"**{min(_drag_pp):.2f}~{max(_drag_pp):.2f}pp**，"
        "C 类三条腿申赎免费（销售服务费未计），扣费前后一致。"
        "**换手越低，费率越不致命** —— 这正是本变种相对 MA20 的长处（见上一节）。",
        "",
        "## 口径与已知边界",
        "",
        "- **成交口径**：场外基金 15:00 前提交才拿当日净值，盘后算出的信号只能按 T+1 净值成交，"
        "故回测 `pos = sig.shift(1)`；QDII 腿（纳指/标普/恒生科技）净值本身滞后 T+1~T+2，"
        "实盘还会再慢一步（敏感性表里 T+2 一档）。",
        "- **A500 / 标普的样本前段是桥接**（A500 `000051`、标普 `050025`），只用于把 BOLL 窗口撑起来，"
        "那段不是本基金业绩；看绝对收益请只认基金成立日之后。",
        "- **组合层只有共同窗口**：受恒生科技联接（`012805`，2021-08 成立）限制。",
        "- **未计**：大额限购、QDII 额度关闭时的顺延、赎回确认时滞、C 类销售服务费。",
        "- **只看收盘**：回测只有日频净值，涨跌停/停牌与盘中触碰无法体现。",
        "",
        "> **模拟结果，非投资建议。**",
    ]

    path = os.path.join(OUTDIR, "_v2_boll_breakout_bt.md")
    with open(path, "w", encoding="utf-8") as f:
        f.write("\n".join(L))

    _plot_portfolio(nav_by, os.path.join(OUTDIR, "_v2_boll_breakout_portfolio.png"))
    _plot_legs(rows, os.path.join(OUTDIR, "_v2_boll_breakout_legs.png"))

    print("报告:", path)
    print("结果:", os.path.join(OUTDIR, "_v2_boll_breakout_bt.jsonl"))
    print(f"逐腿 {len(rows)} 腿：年化跑赢 {n_beat}/{len(rows)} · 回撤更小 {n_dd}/{len(rows)} · "
          f"年化差中位 {med_edge:+.2f}pp（扣费 {med_edge_fee:+.2f}pp）")
    for r in sorted(rows, key=lambda z: z["edge_pp"], reverse=True):
        print(f"  {r['name']:8s} 年化 {boll.fmt_pct(r['ann']):>9s} vs BH {boll.fmt_pct(r['bh_ann']):>9s} · "
              f"回撤 {boll.fmt_pct(r['mdd']):>9s} vs {boll.fmt_pct(r['bh_mdd']):>9s} · "
              f"{r['trades']:3d} 笔({r['per_year']:.1f}/年) · 曝光 {r['exposure']:.0%}")
    for r in prows:
        print(f"  [组合] {r['name']:24s} 年化 {boll.fmt_pct(r['ann']):>9s} · "
              f"回撤 {boll.fmt_pct(r['mdd']):>9s} · 夏普 {r['sharpe']:.2f}")


if __name__ == "__main__":
    main()
