# -*- coding: utf-8 -*-
"""波动资产 · V1 变种：MA20 穿越 · 六腿回测（逐腿 + 组合层）。

池与口径完全跟随 `strategies/vol_legs`：
- 六腿 = `rule.LEG_ORDER`（A500 / 纳指100 / 标普500 / 科创50 / 恒生科技 / 黄金）；
- 价格 = `data.series_for_leg()` 场外**复权净值**（`rule.SIGNAL_MODE`，含分红再投 / 汇率 / 跟踪误差）；
- 成交 = T 日盘后算信号、**T+1 净值成交**（`pos = sig.shift(1)`），与主策略 §2.4 同一纪律；
- 空仓按货基 `rule.CASH_ANNUAL`（1.40%）逐日计息，不假设 0 收益。

产出：
- backtest/_v1_ma20_bt.md   逐腿对照 + 组合层（择时 vs 静态带 vs 等权）
- backtest/_v1_ma20_bt.jsonl

模拟结果，非投资建议。
"""
import json
import os
import sys

import pandas as pd

try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:  # noqa
    pass

_HERE = os.path.dirname(os.path.abspath(__file__))
_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(_HERE)))
sys.path.insert(0, _HERE)

import ma20  # noqa: E402

OUTDIR = os.path.join(_HERE, "backtest")


def leg_rows():
    rows = []
    for k in ma20.LEGS:
        cfg = ma20.rule.LEGS[k]
        px_full = ma20.load(k)
        sig_full, ma_full = ma20.signal(px_full)
        start = ma_full.first_valid_index()
        px = px_full.loc[start:]
        sig = sig_full.loc[start:]
        nav = ma20.run(px, sig)
        nav_fee = ma20.run(px, sig, fee=ma20.FEE[k])
        bh = px / px.iloc[0]
        tr = ma20.trades(sig, px)
        m, b, mf = ma20.perf(nav), ma20.perf(bh), ma20.perf(nav_fee)
        st = ma20.trade_stats(tr)
        years = max((px.index[-1] - px.index[0]).days / 365.25, 1e-9)
        rows.append(dict(
            leg=k, name=cfg["name"], role=cfg["role"], band=float(cfg["band"]),
            exec_code=cfg["exec"], start=px.index[0].date().isoformat(),
            end=px.index[-1].date().isoformat(), years=round(years, 2), bars=len(px),
            ann=m["ann"], mdd=m["mdd"], vol=m["vol"], sharpe=m["sharpe"],
            ann_fee=mf["ann"], mdd_fee=mf["mdd"], sharpe_fee=mf["sharpe"],
            fee_buy=ma20.FEE[k][0], fee_sell=ma20.FEE[k][1],
            exposure=ma20.exposure(sig), trades=st["n"], win_rate=st["win_rate"],
            payoff=st["payoff"], pf=st["pf"], avg_bars=st["bars"],
            per_year=st["n"] / years,
            bh_ann=b["ann"], bh_mdd=b["mdd"], bh_vol=b["vol"], bh_sharpe=b["sharpe"],
            edge_pp=(m["ann"] - b["ann"]) * 100,
            edge_fee_pp=(mf["ann"] - b["ann"]) * 100,
            source=ma20.vdata.SOURCE_NOTE.get(k, ""),
            note=cfg.get("otc_proxy") and "含桥接前段" or "",
        ))
    return rows


def portfolio_rows():
    frame = ma20.common_frame()
    sigs = {k: ma20.signal(frame[k])[0] for k in frame.columns}
    nav_v1 = ma20.portfolio(frame, signals=sigs)
    nav_v1f = ma20.portfolio(frame, signals=sigs, fee=True)
    nav_band = ma20.portfolio(frame, signals=None)
    eq = {k: 1.0 / len(frame.columns) for k in frame.columns}
    nav_eq = ma20.portfolio(frame, signals=None, weight=eq)
    invested = sum(ma20.W[k] * ma20.exposure(sigs[k]) for k in frame.columns)
    out = [
        ("V1 MA20 择时（按带权）", nav_v1, invested),
        ("V1 MA20 择时（扣申赎费）", nav_v1f, invested),
        ("静态带（按带权恒定持有）", nav_band, 1.0),
        ("六腿等权（恒定持有）", nav_eq, 1.0),
    ]
    rows = []
    for name, nav, inv in out:
        m = ma20.perf(nav)
        rows.append(dict(name=name, cum=m["cum"], ann=m["ann"], vol=m["vol"],
                         mdd=m["mdd"], sharpe=m["sharpe"], invested=inv))
    return frame, rows


def main():
    os.makedirs(OUTDIR, exist_ok=True)
    rows = leg_rows()
    frame, prows = portfolio_rows()

    with open(os.path.join(OUTDIR, "_v1_ma20_bt.jsonl"), "w", encoding="utf-8") as f:
        for r in rows:
            f.write(json.dumps({k: (round(v, 4) if isinstance(v, float) else v)
                                for k, v in r.items()}, ensure_ascii=False) + "\n")

    win = f"{frame.index[0].date()} ~ {frame.index[-1].date()}"
    yrs = (frame.index[-1] - frame.index[0]).days / 365.25
    n_beat = sum(1 for r in rows if r["edge_pp"] > 0)
    n_dd = sum(1 for r in rows if abs(r["mdd"]) < abs(r["bh_mdd"]))

    L = [
        "# 波动资产 · V1 变种：MA20 穿越（六腿逐腿择时）",
        "> 池与主策略**完全一致**：`rule.LEG_ORDER` 六腿（A500 / 纳指100 / 标普500 / 科创50 / 恒生科技 / 黄金）",
        "> 价格 = 场外**复权净值**（含分红再投 / 汇率 / 跟踪误差）· T 日盘后算信号、**T+1 净值成交** · "
        f"空仓按货基 {ma20.CASH_ANNUAL:.2%} 计息 · 未计申赎费（成本另算，见文末）",
        "",
        "## 规则",
        "",
        "| 方向 | 条件 |",
        "|---|---|",
        f"| **买入** | 复权净值上穿 MA{ma20.MA_WIN}（今值 > MA20 且 昨值 ≤ 昨日 MA20） |",
        f"| **卖出** | 复权净值下穿 MA{ma20.MA_WIN} |",
        "| **持有** | 未发生穿越时维持原仓位（0/1，不加减仓、不止损止盈、**无 MA60 过滤**） |",
        "",
        "> 「上穿买 / 下穿卖 + 其余维持」等价于「净值在 MA20 上方即持有」的状态序列（首次站上必是上穿）。",
        "",
        "## 逐腿对照（各自 MA20 成形日起 · 按年化差降序）",
        "",
        "| 腿 | 角色 | 样本区间 | 年数 | 年化 | 最大回撤 | 夏普 | 曝光 | 交易 | 年笔数 | 胜率 | 盈亏比 | "
        "买入持有年化 | 买入持有回撤 | 年化差 | 扣费后年化 | 扣费后年化差 |",
        "|---|---|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|",
    ]
    for r in sorted(rows, key=lambda z: z["edge_pp"], reverse=True):
        L.append(
            f"| {r['name']} | {r['role']} | {r['start']} ~ {r['end']} | {r['years']:.2f} | "
            f"{ma20.fmt_pct(r['ann'])} | {ma20.fmt_pct(r['mdd'])} | {r['sharpe']:.2f} | "
            f"{r['exposure']:.0%} | {r['trades']} | {r['per_year']:.1f} | "
            f"{r['win_rate']:.0%} | {ma20.fmt_x(r['payoff'])} | {ma20.fmt_pct(r['bh_ann'])} | "
            f"{ma20.fmt_pct(r['bh_mdd'])} | **{r['edge_pp']:+.2f}pp** | "
            f"{ma20.fmt_pct(r['ann_fee'])} | **{r['edge_fee_pp']:+.2f}pp** |")

    L += [
        "",
        f"> {len(rows)} 腿中：年化跑赢买入持有 **{n_beat}/{len(rows)}**，回撤更小 "
        f"**{n_dd}/{len(rows)}**；逐腿年化差中位 "
        f"**{pd.Series([r['edge_pp'] for r in rows]).median():+.2f}pp**。",
        "",
        f"## 组合层（六腿共同窗口 {win} · {yrs:.1f} 年，按 `rule` 目标带权重归一化，满仓 = 40pp）",
        "",
        "| 组合 | 累计 | 年化 | 波动 | 最大回撤 | 夏普 | 平均投入 |",
        "|---|---:|---:|---:|---:|---:|---:|",
    ]
    for r in prows:
        L.append(f"| {r['name']} | {ma20.fmt_pct(r['cum'], 1)} | {ma20.fmt_pct(r['ann'])} | "
                 f"{ma20.fmt_pct(r['vol'])} | {ma20.fmt_pct(r['mdd'])} | {r['sharpe']:.2f} | "
                 f"{r['invested']:.0%} |")
    base_row = prows[2]                      # 静态带
    d_ann = (prows[0]["ann"] - base_row["ann"]) * 100
    d_ann_fee = (prows[1]["ann"] - base_row["ann"]) * 100
    d_dd = (prows[0]["mdd"] - base_row["mdd"]) * 100
    L += [
        "",
        f"> 择时组合 vs 静态带：年化 {d_ann:+.2f}pp（扣申赎费 {d_ann_fee:+.2f}pp）、"
        f"回撤 {d_dd:+.2f}pp、夏普 {prows[0]['sharpe']:.2f} vs {base_row['sharpe']:.2f}；"
        f"平均投入 {prows[0]['invested']:.0%} vs 100%。",
        "",
        "## 成本（场外口径下这条规则的最大风险）",
        "",
        "- 费率按**每条腿的实际执行代码**取：A 类（`270042` / `017641` / `000216`）申购 0.10% + "
        "赎回 0.50%（持有 >7 天）；C 类（`023299` / `011609` / `012805`）申赎免费，"
        "但收约 0.2%/年销售服务费 —— 后者**未计入**，故这三条腿的扣费后数字偏乐观。",
        "- 逐腿年笔数全部在 **13~16 笔/年**（MA20 在场外净值上穿越频繁），费用不是零头。",
        "",
        "| 腿 | 执行代码 | 年笔数 | 单边费率（买/卖） | 年化拖累 | 扣费后年化 | 扣费后 vs 买入持有 |",
        "|---|---|---:|---|---:|---:|---:|",
    ]
    for r in sorted(rows, key=lambda z: z["edge_fee_pp"], reverse=True):
        drag = r["ann_fee"] - r["ann"]
        L.append(f"| {r['name']} | `{r['exec_code']}` | {r['per_year']:.1f} | "
                 f"{r['fee_buy']:.2%} / {r['fee_sell']:.2%} | {drag * 100:+.2f}pp | "
                 f"{ma20.fmt_pct(r['ann_fee'])} | **{r['edge_fee_pp']:+.2f}pp** |")
    L += [
        "",
        f"> 扣费后逐腿只有 **{sum(1 for r in rows if r['edge_fee_pp'] > 0)}/{len(rows)}** "
        f"仍跑赢买入持有（不计费时 {n_beat}/{len(rows)}）—— 结论对费率高度敏感，"
        "这是「逐腿 MA20 择时放在场外基金上」的硬约束。",
    ]

    L += [
        "",
        "## 口径与已知边界",
        "",
        "- **成交口径**：场外基金 15:00 前提交才拿当日净值，盘后算出的信号只能按 T+1 净值成交，"
        "故回测 `pos = sig.shift(1)`；QDII 腿（纳指/标普/恒生科技）净值本身滞后 T+1~T+2，实盘还会再慢一步。",
        "- **A500 / 标普的样本前段是桥接**（A500 `000051`、标普 `050025`），只用于把 MA 窗口撑起来，"
        "那段不是本基金业绩；看绝对收益请只认基金成立日之后。",
        "- **组合层只有共同窗口**：受恒生科技联接（`012805`，2021-08 成立）限制，"
        "六腿同日有净值的时间远短于单腿样本。",
        "- **未计**：大额限购、QDII 额度关闭时的顺延、赎回确认时滞、C 类销售服务费。",
        "",
        "> **模拟结果，非投资建议。**",
    ]

    path = os.path.join(OUTDIR, "_v1_ma20_bt.md")
    with open(path, "w", encoding="utf-8") as f:
        f.write("\n".join(L))

    print("报告:", path)
    print("结果:", os.path.join(OUTDIR, "_v1_ma20_bt.jsonl"))
    print(f"逐腿 {len(rows)} 只：年化跑赢 {n_beat}/{len(rows)} · 回撤更小 {n_dd}/{len(rows)}")
    for r in sorted(rows, key=lambda z: z["edge_pp"], reverse=True):
        print(f"  {r['name']:8s} 年化 {ma20.fmt_pct(r['ann']):>9s} vs BH {ma20.fmt_pct(r['bh_ann']):>9s} · "
              f"回撤 {ma20.fmt_pct(r['mdd']):>9s} vs {ma20.fmt_pct(r['bh_mdd']):>9s} · "
              f"{r['trades']:3d} 笔({r['per_year']:.1f}/年) · 曝光 {r['exposure']:.0%}")
    for r in prows:
        print(f"  [组合] {r['name']:22s} 年化 {ma20.fmt_pct(r['ann']):>9s} · "
              f"回撤 {ma20.fmt_pct(r['mdd']):>9s} · 夏普 {r['sharpe']:.2f}")


if __name__ == "__main__":
    main()
