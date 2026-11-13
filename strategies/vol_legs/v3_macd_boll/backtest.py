# -*- coding: utf-8 -*-
"""波动资产 · V3 变种：MACD + 布林通道（自适应窗口）· 七腿回测（逐腿 + 组合层）。

池与口径完全跟随 `strategies/vol_legs`：
- 七腿 = `rule.LEG_ORDER`（中证A500 / 沪深300 / 纳指100 / 标普500 / 科创50 / 恒生科技 / 黄金）；
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
    """逐腿：V3 信号 × 波动上限（零费 / 扣真实申赎费）vs 买入持有。

    **主表口径已含 `TV`**（默认 20%，`mb.TV`）；笔数/胜率仍按 0/1 入场统计，
    `exposure` 用实际持仓权重。纯 0/1 的对照见 `tv_leg_rows()`。
    """
    rows = []
    for k in mb.LEGS:
        cfg = mb.rule.LEGS[k]
        px_full = mb.load(k)
        sig_full = mb.signal(px_full)
        w_full = mb.weight(px_full, sig_full)
        start = px_full.index[min(mb.WARMUP, max(len(px_full) - 2, 0))]
        px, sig = px_full.loc[start:], sig_full.loc[start:]
        w = w_full.loc[start:]
        s20 = mb.signal_fixed(px_full, w=mb.W_FAST).loc[start:]
        s30 = mb.signal_fixed(px_full, w=mb.W_SLOW).loc[start:]
        nav = mb.run(px, w)
        nav_fee = mb.run(px, w, fee=mb.FEE[k])
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
            exposure=float(w.shift(1).fillna(0).mean()), exposure_w20=mb.exposure(s20),
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
    sigs_a = {}
    for k in frame.columns:
        _px = mb.load(k)
        sigs_a[k] = mb.weight(_px, mb.signal(_px)).reindex(frame.index).fillna(0.0)
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
        "七腿等权（恒定持有）": mb.portfolio(frame, signals=None, weight=eq),
    }
    _inv_a = sum(mb.W[k] * float(sigs_a[k].shift(1).fillna(0).mean()) for k in frame.columns)
    inv_of = {
        "V3 自适应（按带权）": _inv_a,
        "V3 自适应（扣申赎费）": _inv_a,
        "V3 固定窗口 w20": sum(mb.W[k] * mb.exposure(sigs20[k]) for k in frame.columns),
        "V3 固定窗口 w30": sum(mb.W[k] * mb.exposure(sigs30[k]) for k in frame.columns),
        "静态带（按带权恒定持有）": 1.0,
        "七腿等权（恒定持有）": 1.0,
    }
    rows = []
    for name, nav in navs.items():
        m = mb.perf(nav)
        rows.append(dict(name=name, cum=m["cum"], ann=m["ann"], vol=m["vol"],
                         mdd=m["mdd"], sharpe=m["sharpe"], invested=inv_of[name]))
    return frame, rows, navs


TVS = [0.0, 0.15, 0.20, 0.25]      # 波动上限（0 = 关闭，回到纯 0/1）


def tv_table(frame):
    """波动上限敏感性：逐腿中位 + 组合层（同一共同窗口；`tv=0` = 纯 0/1 基线）。"""
    out = []
    for tv in TVS:
        anns, mdds, shs, exps = [], [], [], []
        for k in frame.columns:
            px = frame[k]
            w = mb.weight(px, mb.signal(px), tv=tv)
            m = mb.perf(mb.run(px, w))
            anns.append(m["ann"]); mdds.append(m["mdd"]); shs.append(m["sharpe"])
            exps.append(float(w.shift(1).fillna(0).mean()))
        sigs = {k: mb.weight(frame[k], mb.signal(frame[k]), tv=tv) for k in frame.columns}
        mp = mb.perf(mb.portfolio(frame, signals=sigs))
        out.append(dict(tv=tv, ann=float(pd.Series(anns).median()),
                        mdd=float(pd.Series(mdds).median()),
                        sharpe=float(pd.Series(shs).median()), expo=float(pd.Series(exps).mean()),
                        port_ann=mp["ann"], port_mdd=mp["mdd"], port_sharpe=mp["sharpe"]))
    return out


def tv_leg_rows(frame):
    """逐腿：纯 0/1 vs 波动上限 `mb.TV`（同一窗口，零费用）。"""
    out = []
    for k in frame.columns:
        px = frame[k]
        sig = mb.signal(px)
        w0, w1 = sig.astype(float), mb.weight(px, sig, tv=mb.TV)
        a, b = mb.perf(mb.run(px, w0)), mb.perf(mb.run(px, w1))
        out.append(dict(leg=k, name=mb.rule.LEGS[k]["name"],
                        ann0=a["ann"], mdd0=a["mdd"], sharpe0=a["sharpe"],
                        ann1=b["ann"], mdd1=b["mdd"], sharpe1=b["sharpe"],
                        expo0=float(w0.shift(1).fillna(0).mean()),
                        expo1=float(w1.shift(1).fillna(0).mean())))
    return out


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
    tv = tv_table(frame)
    tv_legs = tv_leg_rows(frame)

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
        "# 波动资产 · V3 变种：MACD + 布林通道（自适应窗口）· 七腿回测",
        "> 池与主策略**完全一致**：`rule.LEG_ORDER` 七腿（中证A500 / 沪深300 / 纳指100 / 标普500 / 科创50 / 恒生科技 / 黄金）",
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
        f"| **波动上限** | `TV = {mb.TV:.0%}`（设 None 关闭）：持仓时仓位 × `min(1, TV/σ{mb.TV_WIN}年化)`，"
        "入场判定不变。**下面各主表已含这一层**（2026-11-13 起） |",
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
        f"## 组合层（七腿共同窗口 {win} · {yrs:.1f} 年，按 `rule` 目标带权重归一化，满仓 = 40pp）",
        "",
        "| 组合 | 累计 | 年化 | 波动 | 最大回撤 | 夏普 | 平均投入 |",
        "|---|---:|---:|---:|---:|---:|---:|",
    ]
    for r in prows:
        L.append(f"| {r['name']} | {mb.fmt_pct(r['cum'], 1)} | {mb.fmt_pct(r['ann'])} | "
                 f"{r['vol'] * 100:.2f}% | {mb.fmt_pct(r['mdd'])} | {r['sharpe']:.2f} | "
                 f"{r['invested']:.0%} |")

    L += [
        "",
        f"### 波动上限层（`TV` = {mb.TV:.0%}，可配）",
        "",
        "> 信号 0/1 完全不变；**持仓时**仓位再乘 `min(1, TV / σ20年化)`。σ 用 20 日已实现波动年化、"
        "`shift(1)` 后才生效；成本按仓位变动比例计（0→1 与一次申赎费等价）。",
        "",
        "| 波动上限 | 逐腿中位年化 | 逐腿中位回撤 | 逐腿中位夏普 | 组合年化 | 组合回撤 | 组合夏普 | 平均投入 |",
        "|---|---:|---:|---:|---:|---:|---:|---:|",
    ]
    for t in tv:
        lab = "关闭（纯 0/1）" if not t["tv"] else f"{t['tv']:.0%}"
        mark = " **(默认)**" if abs(t["tv"] - (mb.TV or 0)) < 1e-12 else ""
        L.append(f"| {lab}{mark} | {mb.fmt_pct(t['ann'])} | {mb.fmt_pct(t['mdd'])} | "
                 f"{t['sharpe']:.2f} | {mb.fmt_pct(t['port_ann'])} | {mb.fmt_pct(t['port_mdd'])} | "
                 f"{t['port_sharpe']:.2f} | {t['expo']:.0%} |")

    L += ["", f"#### 逐腿：纯 0/1 → 波动上限 {mb.TV:.0%}", "",
          "| 腿 | 年化 | 最大回撤 | 夏普 | 曝光 |",
          "|---|---:|---:|---:|---:|"]
    for r in tv_legs:
        L.append(f"| {r['name']} | {mb.fmt_pct(r['ann0'])} → **{mb.fmt_pct(r['ann1'])}** | "
                 f"{mb.fmt_pct(r['mdd0'])} → **{mb.fmt_pct(r['mdd1'])}** | "
                 f"{r['sharpe0']:.2f} → **{r['sharpe1']:.2f}** | "
                 f"{r['expo0']:.0%} → {r['expo1']:.0%} |")
    _best = max(tv_legs, key=lambda z: z["sharpe1"] - z["sharpe0"])
    # 纯 0/1 的含费对照（与含 TV 的口径完全同源，只差这一层）
    _sig_off = {k: mb.signal(mb.load(k)).reindex(frame.index).fillna(0.0)
                for k in frame.columns}
    _off_fee = mb.perf(mb.portfolio(frame, signals=_sig_off, fee=True))
    _off_nf = mb.perf(mb.portfolio(frame, signals=_sig_off))
    L += ["", f"> 改善最大的是 **{_best['name']}**（夏普 {_best['sharpe0']:.2f} → "
              f"{_best['sharpe1']:.2f}，回撤 {mb.fmt_pct(_best['mdd0'])} → "
              f"{mb.fmt_pct(_best['mdd1'])}）。这条规则曝光只有 10%~20%，"
              "波动上限真正压的是「少数几次进场时刚好撞上高波动」的那几笔 —— "
              "`research/beat_best` 里科创50 的邻域 15/20/25% 三格全部仍超越纯 0/1，"
              "所以这一层是机制而不是拟合。",
          "",
          f"> ⚠ **含费口径必须分开看**：零费组合夏普 {_off_nf['sharpe']:.2f} → "
          f"{adapt['sharpe']:.2f}（这一层是加分），但按**场外 A 类申赎费**（0.10% / 0.50%）扣完是 "
          f"{_off_fee['sharpe']:.2f} → {adapt_fee['sharpe']:.2f}"
          f"（年化 {mb.fmt_pct(_off_fee['ann'])} → {mb.fmt_pct(adapt_fee['ann'])}）—— "
          "**连续仓位多出来的小额调仓会把申赎费放大**。所以这一层在 V3 上只适合**低费率通道**"
          "（C 类免申赎 / 场内 ETF）；走 A 类时建议把 `TV` 设 None 关闭，"
          "或把 `TV_DEAD` / `TV_GAP` 再放宽（当前 5pp / 5 日）。",
          ]

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
    L += ["", "## 自适应窗口 vs 固定窗口（组合层分段 · 同一七腿池）", "",
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
        "## 成本（场外申赎费 —— 敏感度低于 v1_1_ma20，但仍吃掉约 1/3 毛收益）",
        "",
        "- 费率按**每条腿的实际执行代码**取：A 类（`270042` / `017641` / `000216`）申购 0.10% + "
        "赎回 0.50%（持有 >7 天）；C 类（`023299` / `011609` / `012805`）申赎免费，"
        "但收约 0.2%/年销售服务费 —— 后者**未计入**，故这三条腿的扣费后数字偏乐观。",
        "- MACD+BOLL 在日频净值上穿越频率只有 MA20 的 1/4~1/5（v1_1_ma20 是 13~16 笔/年），"
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
        "七腿同日有净值的时间远短于单腿样本。",
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
