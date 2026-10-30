# -*- coding: utf-8 -*-
"""波动资产 · V3 变种：MACD + 布林通道（自适应窗口）· 六腿回测（逐腿 + 组合层）。

池与口径完全跟随 `strategies/vol_legs`：
- 六腿 = `rule.LEG_ORDER`（A500 / 纳指100 / 标普500 / 科创50 / 恒生科技 / 黄金）；
- 价格 = `data.series_for_leg()` 场外**复权净值**（`rule.SIGNAL_MODE`，含分红再投 / 汇率 / 跟踪误差）；
- 成交 = T 日盘后算信号、**T+1 净值成交**（`pos = sig.shift(1)`），与主策略 §2.4 同一纪律；
- 空仓按货基 `rule.CASH_ANNUAL`（1.40%）逐日计息，不假设 0 收益。

产出：
- backtest/_v3_macd_boll_bt.md   逐腿对照 + 组合层（自适应 / 固定 w20 / 固定 w30 / 静态带 / 等权）
- backtest/_v3_macd_boll_bt.jsonl

模拟结果，非投资建议。
"""
import json
import os
import sys

import pandas as pd

try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:  # noqa: BLE001
    pass

_HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, _HERE)

import macd_boll as mb  # noqa: E402

OUTDIR = os.path.join(_HERE, "backtest")


def leg_rows():
    rows = []
    for k in mb.LEGS:
        cfg = mb.rule.LEGS[k]
        px, sig, s20, s30 = mb.leg_slice(k)
        nav = mb.run(px, sig)
        nav_fee = mb.run(px, sig, fee=mb.FEE[k])
        bh = px / px.iloc[0]
        tr = mb.trades(sig, px)
        m, b, mf = mb.perf(nav), mb.perf(bh), mb.perf(nav_fee)
        st = mb.trade_stats(tr)
        years = max((px.index[-1] - px.index[0]).days / 365.25, 1e-9)
        rows.append(dict(
            leg=k, name=cfg["name"], role=cfg["role"], band=float(cfg["band"]),
            exec_code=cfg["exec"], start=px.index[0].date().isoformat(),
            end=px.index[-1].date().isoformat(), years=round(years, 2), bars=len(px),
            ann=m["ann"], mdd=m["mdd"], vol=m["vol"], sharpe=m["sharpe"],
            ann_fee=mf["ann"], mdd_fee=mf["mdd"], sharpe_fee=mf["sharpe"],
            fee_buy=mb.FEE[k][0], fee_sell=mb.FEE[k][1],
            exposure=mb.exposure(sig), exposure_w20=mb.exposure(s20),
            exposure_w30=mb.exposure(s30),
            trades=st["n"], win_rate=st["win_rate"], payoff=st["payoff"], pf=st["pf"],
            avg_bars=st["bars"], per_year=st["n"] / years,
            bh_ann=b["ann"], bh_mdd=b["mdd"], bh_vol=b["vol"], bh_sharpe=b["sharpe"],
            edge_pp=(m["ann"] - b["ann"]) * 100,
            edge_fee_pp=(mf["ann"] - b["ann"]) * 100,
            source=mb.vdata.SOURCE_NOTE.get(k, ""),
            note=cfg.get("otc_proxy") and "含桥接前段" or "",
        ))
    return rows


def portfolio_rows():
    """-> (frame, rows, navs, sigs)。rows 里每档都带自己的平均投入。"""
    frame = mb.common_frame()
    sigs_a = {k: mb.signal(mb.load(k)).reindex(frame.index).fillna(0).astype(int)
              for k in frame.columns}
    sigs20 = {k: mb.signal_fixed(mb.load(k), w=mb.W_FAST).reindex(frame.index).fillna(0).astype(int)
              for k in frame.columns}
    sigs30 = {k: mb.signal_fixed(mb.load(k), w=mb.W_SLOW).reindex(frame.index).fillna(0).astype(int)
              for k in frame.columns}
    eq = {k: 1.0 / len(frame.columns) for k in frame.columns}

    navs = {
        "V3 自适应（按带权）": mb.portfolio(frame, signals=sigs_a),
        "V3 自适应（扣申赎费）": mb.portfolio(frame, signals=sigs_a, fee=True),
        "V3 固定窗口 w20": mb.portfolio(frame, signals=sigs20),
        "V3 固定窗口 w30": mb.portfolio(frame, signals=sigs30),
        "静态带（按带权恒定持有）": mb.portfolio(frame, signals=None),
        "六腿等权（恒定持有）": mb.portfolio(frame, signals=None, weight=eq),
    }
    inv_of = {
        "V3 自适应（按带权）": sum(mb.W[k] * mb.exposure(sigs_a[k]) for k in frame.columns),
        "V3 自适应（扣申赎费）": sum(mb.W[k] * mb.exposure(sigs_a[k]) for k in frame.columns),
        "V3 固定窗口 w20": sum(mb.W[k] * mb.exposure(sigs20[k]) for k in frame.columns),
        "V3 固定窗口 w30": sum(mb.W[k] * mb.exposure(sigs30[k]) for k in frame.columns),
        "静态带（按带权恒定持有）": 1.0,
        "六腿等权（恒定持有）": 1.0,
    }
    rows = []
    for name, nav in navs.items():
        m = mb.perf(nav)
        rows.append(dict(name=name, cum=m["cum"], ann=m["ann"], vol=m["vol"],
                         mdd=m["mdd"], sharpe=m["sharpe"], invested=inv_of[name]))
    return frame, rows, navs


def segments(frame):
    """把共同窗口按时间三等分（首尾为不完整年度时的边界也照实标注）。"""
    idx = frame.index
    n = len(idx)
    out = []
    for i in range(3):
        i0 = i * n // 3
        i1 = (i + 1) * n // 3 - 1 if i < 2 else n - 1
        d0, d1 = idx[i0], idx[i1]
        out.append((f"{d0.year}–{d1.year}" if d0.year != d1.year else str(d0.year), d0, d1))
    return out


def main():
    os.makedirs(OUTDIR, exist_ok=True)
    rows = leg_rows()
    frame, prows, navs = portfolio_rows()

    with open(os.path.join(OUTDIR, "_v3_macd_boll_bt.jsonl"), "w", encoding="utf-8") as f:
        for r in rows:
            f.write(json.dumps({k: (round(v, 4) if isinstance(v, float) else v)
                                for k, v in r.items()}, ensure_ascii=False) + "\n")

    win = f"{frame.index[0].date()} ~ {frame.index[-1].date()}"
    yrs = (frame.index[-1] - frame.index[0]).days / 365.25
    n_beat = sum(1 for r in rows if r["edge_pp"] > 0)
    n_dd = sum(1 for r in rows if abs(r["mdd"]) < abs(r["bh_mdd"]))
    n_fee = sum(1 for r in rows if r["edge_fee_pp"] > 0)
    base_band = next(p for p in prows if p["name"].startswith("静态带"))
    adapt = prows[0]
    adapt_fee = prows[1]

    L = [
        "# 波动资产 · V3 变种：MACD + 布林通道（自适应窗口）· 六腿回测",
        "> 池与主策略**完全一致**：`rule.LEG_ORDER` 六腿（A500 / 纳指100 / 标普500 / 科创50 / 恒生科技 / 黄金）",
        "> 价格 = 场外**复权净值**（含分红再投 / 汇率 / 跟踪误差）· T 日盘后算信号、**T+1 净值成交** · "
        f"空仓按货基 {mb.CASH_ANNUAL:.2%} 计息 · 未计申赎费（成本另算，见文末）",
        "> 来源：`strategies/tech_indicators/v3_macd_boll`，只换池与口径，规则本体一条没改",
        "",
        "## 规则",
        "",
        "| 方向 | 条件 |",
        "|---|---|",
        f"| **入场** | DIF>0 且 DIF>DEA（零轴上金叉状态）**且** 布林带宽放大（开口）**且** 收盘上穿布林上轨（突破） |",
        f"| **离场** | DIF 下穿 DEA（死叉）**或** 收盘自上轨下穿中轨（转弱） |",
        f"| **观望** | 布林带平行走平、净值在通道内震荡 → 无突破即不进场（自然空仓） |",
        f"| **窗口自适应** | Kaufman 效率比 ER({mb.ER_N})：≥{mb.ER_HI:.2f} → 趋势态窗口 {mb.W_FAST}（窄通道早抓）；"
        f"≤{mb.ER_LO:.2f} → 震荡态窗口 {mb.W_SLOW}（宽通道滤假突破）；带滞回，k={mb.K:g} |",
        "",
        f"> MACD({mb.MACD_FAST},{mb.MACD_SLOW},{mb.MACD_SIG}) 与 BOLL 由净值本地自算"
        "（源变种读场内 ETF 的预计算指标库，场外基金代码不在其中）；样本自第 "
        f"{mb.WARMUP + 1} 个交易日起算。MACD 顶背离为主观信号，未纳入量化。",
        "",
        "## 逐腿对照（各自预热完成后起 · 按年化差降序）",
        "",
        "| 腿 | 角色 | 样本区间 | 年数 | 年化 | 最大回撤 | 夏普 | 曝光 | 交易 | 年笔数 | 胜率 | 盈亏比 | "
        "买入持有年化 | 买入持有回撤 | 年化差 | 扣费后年化 | 扣费后年化差 |",
        "|---|---|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|",
    ]
    for r in sorted(rows, key=lambda z: z["edge_pp"], reverse=True):
        L.append(
            f"| {r['name']} | {r['role']} | {r['start']} ~ {r['end']} | {r['years']:.2f} | "
            f"{mb.fmt_pct(r['ann'])} | {mb.fmt_pct(r['mdd'])} | {r['sharpe']:.2f} | "
            f"{r['exposure']:.0%} | {r['trades']} | {r['per_year']:.1f} | "
            f"{r['win_rate']:.0%} | {mb.fmt_x(r['payoff'])} | {mb.fmt_pct(r['bh_ann'])} | "
            f"{mb.fmt_pct(r['bh_mdd'])} | **{r['edge_pp']:+.2f}pp** | "
            f"{mb.fmt_pct(r['ann_fee'])} | **{r['edge_fee_pp']:+.2f}pp** |")

    L += [
        "",
        f"> {len(rows)} 腿中：年化跑赢买入持有 **{n_beat}/{len(rows)}**，回撤更小 "
        f"**{n_dd}/{len(rows)}**；逐腿年化差中位 "
        f"**{pd.Series([r['edge_pp'] for r in rows]).median():+.2f}pp**，"
        f"扣费后仍跑赢 **{n_fee}/{len(rows)}**。",
        "",
        f"## 组合层（六腿共同窗口 {win} · {yrs:.1f} 年，按 `rule` 目标带权重归一化，满仓 = 40pp）",
        "",
        "| 组合 | 累计 | 年化 | 波动 | 最大回撤 | 夏普 | 平均投入 |",
        "|---|---:|---:|---:|---:|---:|---:|",
    ]
    for r in prows:
        L.append(f"| {r['name']} | {mb.fmt_pct(r['cum'], 1)} | {mb.fmt_pct(r['ann'])} | "
                 f"{r['vol'] * 100:.2f}% | {mb.fmt_pct(r['mdd'])} | {r['sharpe']:.2f} | "
                 f"{r['invested']:.0%} |")

    d_ann = (adapt["ann"] - base_band["ann"]) * 100
    d_ann_fee = (adapt_fee["ann"] - base_band["ann"]) * 100
    d_dd = (adapt["mdd"] - base_band["mdd"]) * 100
    L += [
        "",
        f"> 择时组合 vs 静态带：年化 {d_ann:+.2f}pp（扣申赎费 {d_ann_fee:+.2f}pp）、"
        f"回撤 {d_dd:+.2f}pp（正 = 回撤更小）、夏普 {adapt['sharpe']:.2f} vs "
        f"{base_band['sharpe']:.2f}；平均投入 {adapt['invested']:.0%} vs 100%。",
    ]

    # 自适应 vs 固定窗口（组合层分段）
    L += ["", "## 自适应窗口 vs 固定窗口（组合层分段 · 同一六腿池）", "",
          "| 期间 | 自适应(20↔30) 年化 / 夏普 | 固定 w20 年化 / 夏普 | 固定 w30 年化 / 夏普 |",
          "|---|---|---|---|"]
    for name, d0, d1 in segments(frame):
        cells = []
        for key in ("V3 自适应（按带权）", "V3 固定窗口 w20", "V3 固定窗口 w30"):
            m = mb.seg_perf(navs[key], d0, d1)
            cells.append("—" if m is None else f"{mb.fmt_pct(m['ann'])} / {m['sharpe']:.2f}")
        L.append(f"| {name} | {cells[0]} | {cells[1]} | {cells[2]} |")

    # 逐年
    y_a = mb.yearly(navs["V3 自适应（按带权）"])
    y_b = mb.yearly(navs["静态带（按带权恒定持有）"])
    L += ["", "## 逐年收益（组合层）", "", "| 年 | V3 自适应 | 静态带 | 差 |", "|---|---:|---:|---:|"]
    for y in sorted(y_a):
        a, b = y_a[y], y_b[y]
        L.append(f"| {y} | {mb.fmt_pct(a)} | {mb.fmt_pct(b)} | **{(a - b) * 100:+.2f}pp** |")

    L += [
        "",
        "## 成本（场外申赎费 —— 敏感度低于 v1_ma20，但仍吃掉约 1/3 毛收益）",
        "",
        "- 费率按**每条腿的实际执行代码**取：A 类（`270042` / `017641` / `000216`）申购 0.10% + "
        "赎回 0.50%（持有 >7 天）；C 类（`023299` / `011609` / `012805`）申赎免费，"
        "但收约 0.2%/年销售服务费 —— 后者**未计入**，故这三条腿的扣费后数字偏乐观。",
        "- MACD+BOLL 在日频净值上穿越频率只有 MA20 的 1/4~1/5（v1_ma20 是 13~16 笔/年），"
        "但 A 类腿每条往返 0.6%，仍不是零成本。",
        "",
        "| 腿 | 执行代码 | 年笔数 | 单边费率（买/卖） | 年化拖累 | 扣费后年化 | 扣费后 vs 买入持有 |",
        "|---|---|---:|---|---:|---:|---:|",
    ]
    for r in sorted(rows, key=lambda z: z["edge_fee_pp"], reverse=True):
        drag = r["ann_fee"] - r["ann"]
        L.append(f"| {r['name']} | `{r['exec_code']}` | {r['per_year']:.1f} | "
                 f"{r['fee_buy']:.2%} / {r['fee_sell']:.2%} | {drag * 100:+.2f}pp | "
                 f"{mb.fmt_pct(r['ann_fee'])} | **{r['edge_fee_pp']:+.2f}pp** |")

    L += [
        "",
        "## 口径与已知边界",
        "",
        "- **成交口径**：场外基金 15:00 前提交才拿当日净值，盘后算出的信号只能按 T+1 净值成交，"
        "故回测 `pos = sig.shift(1)`；QDII 腿（纳指/标普/恒生科技）净值本身滞后 T+1~T+2，实盘还会再慢一步。",
        "- **A500 / 标普的样本前段是桥接**（A500 `000051`、标普 `050025`），只用于把 MACD/BOLL 窗口撑起来，"
        "那段不是本基金业绩；看绝对收益请只认基金成立日之后。",
        "- **信号是「突破追涨型」**：只在开口突破时进场，与主策略「下跌才有加仓路径」的阶梯逻辑方向相反，"
        "两条规则**不可叠加在同一份额度上**（叠加前先定谁的额度优先）。",
        "- **组合层只有共同窗口**：受恒生科技联接（`012805`，2021-08 成立）限制，"
        "六腿同日有净值的时间远短于单腿样本。",
        "- **未计**：大额限购、QDII 额度关闭时的顺延、赎回确认时滞、C 类销售服务费。",
        "",
        "> **模拟结果，非投资建议。**",
    ]

    path = os.path.join(OUTDIR, "_v3_macd_boll_bt.md")
    with open(path, "w", encoding="utf-8") as f:
        f.write("\n".join(L))

    print("报告:", path)
    print("结果:", os.path.join(OUTDIR, "_v3_macd_boll_bt.jsonl"))
    print(f"逐腿 {len(rows)} 只：年化跑赢 {n_beat}/{len(rows)} · 回撤更小 {n_dd}/{len(rows)} · "
          f"扣费后跑赢 {n_fee}/{len(rows)}")
    for r in sorted(rows, key=lambda z: z["edge_pp"], reverse=True):
        print(f"  {r['name']:8s} 年化 {mb.fmt_pct(r['ann']):>9s} vs BH {mb.fmt_pct(r['bh_ann']):>9s} · "
              f"回撤 {mb.fmt_pct(r['mdd']):>9s} vs {mb.fmt_pct(r['bh_mdd']):>9s} · "
              f"{r['trades']:3d} 笔({r['per_year']:.1f}/年) · 曝光 {r['exposure']:.0%}")
    for r in prows:
        print(f"  [组合] {r['name']:22s} 年化 {mb.fmt_pct(r['ann']):>9s} · "
              f"回撤 {mb.fmt_pct(r['mdd']):>9s} · 夏普 {r['sharpe']:.2f}")


if __name__ == "__main__":
    main()
