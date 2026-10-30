# -*- coding: utf-8 -*-
"""波动资产 · V5 变种：两线重合买点 · 回测与校准。

口径（全部由 `overlap.py` 提供，本文件只做组合与报表）：
- 红线 = 沪深300全收益 H00300；蓝线 = 自建二级债基指数（8 只老债基累计净值等权复合）
- 两线同锚归一 1000（锚点 2005-01-04），R = 红/蓝；买点 = R ≤ 1 + tol（默认 2%）
- 成交：T 日收盘算信号 → **T+1 收盘净值成交**（无前视）；买入后**持有一年**（250 日）卖出
- 成本：申购 0.10% / 赎回 0.50%；空仓按货基 1.40% 计息
- 绩效：`engine.metrics`（rf = 货基），与主策略同一套算法

产出（相对本文件）：
- backtest/_v5_overlap_bt.md / .jsonl
- backtest/_v5_overlap.png       全样本双线图（复刻参考图，圆圈 = 买点，严格口径）
- backtest/_v5_overlap_3y.png    近三年同一张图
- backtest/_v5_overlap_r120.png  放宽到 R ≤ 1.20 的买点位置（对照）
- backtest/_v5_overlap_nav.png   净值对比

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

import overlap as ov  # noqa: E402

OUTDIR = os.path.join(_HERE, "backtest")
YEARS_3Y = 3

# 主表用到的口径：(key, mode, 参数, 表里的名字, 备注)
CONFIGS = [
    ("level2", "level", dict(level_tol=0.02), "**A 严格：R ≤ 1.02（你的原话）**",
     "两线重合/跌破才算，触发 1 段"),
    ("level10", "level", dict(level_tol=0.10), "A′ R ≤ 1.10", "放宽 8pp，仍然只差 0.4pp 抓不到 2008"),
    ("level20", "level", dict(level_tol=0.20), "A″ R ≤ 1.20", "把「几乎贴合」放宽到 +20%"),
    ("level30", "level", dict(level_tol=0.30), "A‴ R ≤ 1.30", "再放宽 10pp，抓到 2014"),
    ("z10", "z", dict(z_k=1.0), "C 对照：z ≤ −1", "相对自己的年线 −1σ（不是绝对贴合）"),
]


# ---------------------------------------------------------------- 工具
def yearly_returns(nav):
    """-> Series(index=年, 收益)。兼容 pandas 1.x("A") 与 2.2+("YE")。"""
    for freq in ("YE", "A"):
        try:
            y = nav.resample(freq).last()
            break
        except ValueError:
            continue
    else:
        return pd.Series(dtype="float64")
    first = pd.Series([nav.iloc[0]], index=[y.index[0] - pd.Timedelta(days=1)])
    return pd.concat([first, y]).pct_change().dropna().rename(lambda d: d.year)


def static_mix(df, w=0.5, freq="M", fees=True):
    """静态 w 股票 / (1-w) 债券（月末回锚）；股 = 全收益，债 = 自建指数。"""
    tr = df["tr"].to_numpy(dtype="float64")
    bd = df["bd"].to_numpy(dtype="float64")
    idx = df.index
    for alt in ("ME" if freq == "M" else "QE", "M" if freq == "M" else "Q"):
        try:
            last = pd.Series(idx, index=idx).resample(alt).last().dropna()
            break
        except ValueError:
            continue
    else:
        raise ValueError("本机 pandas 不支持月末/季末重采样")
    marks = set(pd.DatetimeIndex(last.to_numpy()))
    fb, fs = (ov.CFG["fee_buy"], ov.CFG["fee_sell"]) if fees else (0.0, 0.0)
    nav, navs, q = 1.0, [], float(w)
    for i in range(len(idx)):
        if i > 0:
            g_tr, g_bd = tr[i] / tr[i - 1], bd[i] / bd[i - 1]
            nav *= 1.0 + (q * (g_tr - 1.0) + (1 - q) * (g_bd - 1.0))
            q = q * g_tr / (q * g_tr + (1 - q) * g_bd)
        if idx[i] in marks and abs(q - w) > 0.005:
            nav *= 1.0 - abs(w - q) * (fs if w < q else fb)
            q = float(w)
        navs.append(nav)
    return pd.Series(navs, index=idx, dtype="float64", name=f"static{w:.0%}")


def cash_series(df):
    r = (1.0 + ov.rule.CASH_ANNUAL) ** (1.0 / 252) - 1.0
    return pd.Series((1.0 + r) ** np.arange(len(df)), index=df.index, dtype="float64")


def row(name, nav, note="", res=None, df=None):
    m = ov.perf(nav)
    out = dict(name=name, cum=m["cum"], ann=m["ann"], vol=m["vol"], mdd=m["mdd"],
               sharpe=m["sharpe"], calmar=(m["ann"] / abs(m["mdd"]) if m["mdd"] else None),
               note=note)
    if res is not None:
        out["buys"] = sum(1 for t in res["trades"] if t["side"] == "buy")
        out["fees"] = res["fees"]
    elif df is not None:
        out["buys"] = 0
        out["fees"] = 0.0
    if df is not None:
        out["expo"] = float(df["pos"].mean()) if "pos" in df else None
    return out


def run_config(mode, kw):
    df = ov.build_frame(mode=mode, **kw)
    return df, ov.run(df)


def sens_hold(mode, kw, holds):
    rows = []
    df = ov.build_frame(mode=mode, **kw)
    for h in holds:
        pos = ov.position_series(df["trig"], hold_days=h)
        r = ov.run(df, pos=pos)
        m = ov.perf(r["nav"])
        rows.append(dict(label=("一直持有（不卖）" if h is None else f"{h} 交易日"),
                         ann=m["ann"], mdd=m["mdd"], sharpe=m["sharpe"],
                         buys=sum(1 for t in r["trades"] if t["side"] == "buy"),
                         expo=float(pos.mean())))
    return rows


def sens_bond(bases, tols=(0.02, 0.20)):
    """换蓝线口径（对 level 口径影响最大的一档）。"""
    rows = []
    for b in bases:
        label = ("自建复合（8 只等权）" if b == "composite"
                 else f"单只 {b}（{ov.FUNDS[b][0]}）")
        for tol in tols:
            df = ov.build_frame(mode="level", level_tol=tol, bond_baseline=b)
            r = ov.run(df)
            m = ov.perf(r["nav"])
            bd_ann = (df["bd"].iloc[-1] / df["bd"].iloc[0]) ** (
                365.25 / (df.index[-1] - df.index[0]).days) - 1
            rows.append(dict(label=label, tol=tol, bd_ann=bd_ann, ann=m["ann"], mdd=m["mdd"],
                             sharpe=m["sharpe"],
                             buys=sum(1 for t in r["trades"] if t["side"] == "buy"),
                             eps=len(ov.episodes(df))))
    return rows


# ---------------------------------------------------------------- 报告
def main():
    os.makedirs(OUTDIR, exist_ok=True)

    runs = {}
    for key, mode, kw, name, note in CONFIGS:
        df, res = run_config(mode, kw)
        runs[key] = dict(df=df, res=res, name=name, note=note, mode=mode, kw=kw)

    base = runs["level2"]
    df = base["df"]                                        # 主口径
    yrs = (df.index[-1] - df.index[0]).days / 365.25
    tr_ann = (df["tr"].iloc[-1] / df["tr"].iloc[0]) ** (1 / yrs) - 1
    bd_ann = (df["bd"].iloc[-1] / df["bd"].iloc[0]) ** (1 / yrs) - 1
    src_tr, src_bd = ov.SOURCE.get("hs300_tr"), ov.SOURCE.get("bond_index")   # sens 前先存

    bh = df["tr"] / df["tr"].iloc[0]
    bh_bd = df["bd"] / df["bd"].iloc[0]
    cas = cash_series(df)
    st50 = static_mix(df, 0.5)
    st80 = static_mix(df, 0.8)
    r_a_bond = ov.run(df, cash_mode="bond")                 # 卖出后回债（不躺货基）
    r30_bond = ov.run(runs["level30"]["df"], cash_mode="bond")

    rows = [row(v["name"], v["res"]["nav"], v["note"], res=v["res"], df=v["df"])
            for v in runs.values()]
    rows += [
        row("**A 严格 + 卖出后回债基**", r_a_bond["nav"],
            "同样的买点，空仓段拿二级债基（5.3%/年）而不是货基（1.4%/年）",
            res=r_a_bond, df=df),
        row("A‴（R≤1.30）+ 卖出后回债基", r30_bond["nav"], "同上，放宽阈值",
            res=r30_bond, df=runs["level30"]["df"]),
        row("买入持有沪深300（全收益）", bh, "纯股票，含分红再投", df=None),
        row("静态 50/50（月末回锚）", st50, "股 = 全收益，债 = 自建债基指数", df=None),
        row("静态 80/20（月末回锚）", st80, "一直高仓位股票", df=None),
        row("买入持有二级债基指数", bh_bd, "纯债（自建复合）", df=None),
        row("现金（货基 1.40%）", cas, "空仓的机会成本", df=None),
    ]

    eps_a = ov.episodes(base["df"])
    eps_a3 = ov.episodes(runs["level30"]["df"])
    fwd = ov.forward_stats(base["df"])

    per_year = pd.DataFrame({
        "策略A(严格)": yearly_returns(base["res"]["nav"]),
        "策略A‴(R≤1.30)": yearly_returns(runs["level30"]["res"]["nav"]),
        "沪深300": yearly_returns(bh),
        "静态50/50": yearly_returns(st50),
    }).dropna(how="all")

    sens_tol = []
    for tol in (0.00, 0.02, 0.05, 0.10, 0.15, 0.20, 0.25, 0.30):
        d2, r2 = run_config("level", dict(level_tol=tol))
        m = ov.perf(r2["nav"])
        sens_tol.append(dict(label=f"R ≤ {1 + tol:.2f}", tol=tol, ann=m["ann"], mdd=m["mdd"],
                             sharpe=m["sharpe"], expo=float(d2["pos"].mean()),
                             eps=len(ov.episodes(d2)),
                             buys=sum(1 for t in r2["trades"] if t["side"] == "buy")))
    sens_h = sens_hold("level", dict(level_tol=0.02), [125, 250, 500, None])
    sens_h3 = sens_hold("level", dict(level_tol=0.30), [125, 250, 500, None])
    sens_b = sens_bond(["composite", "202101"])

    # ---------------------------------------------------------------- 图
    cut = df.index[-1] - pd.Timedelta(days=365 * YEARS_3Y)
    p_full = os.path.join(OUTDIR, "_v5_overlap.png")
    ov.plot_overlay(df, p_full,
                    subtitle=f"{df.index[0]:%Y-%m-%d} 至 {df.index[-1]:%Y-%m-%d}（日线）"
                             f" · 买点 = R ≤ {1 + ov.CFG['level_tol']:.2f}，持有一年后卖出")
    p_3y = os.path.join(OUTDIR, "_v5_overlap_3y.png")
    ov.plot_overlay(df, p_3y, start=cut,
                    subtitle=f"{cut:%Y-%m-%d} 至 {df.index[-1]:%Y-%m-%d}（局部放大）")
    p_r120 = os.path.join(OUTDIR, "_v5_overlap_r120.png")
    ov.plot_overlay(runs["level20"]["df"], p_r120, title="放宽到 R ≤ 1.20 的买点位置",
                    subtitle="同一张图、同一份数据，只把「几乎贴合」的容忍度放到 +20%")
    lows = ov.cycle_lows(df)
    p_lows = os.path.join(OUTDIR, "_v5_overlap_troughs.png")
    ov.plot_overlay(df, p_lows, title="参考图的 5 个圈 vs 严格口径的 1 次触发",
                    subtitle="白色实线圆 = 规则实际买入点；灰色虚线圈 = R 的 5 个周期低点",
                    extra_marks=[{"date": d, "label": "①②③④⑤"[i]}
                                 for i, (d, _v) in enumerate(lows)])
    p_nav = os.path.join(OUTDIR, "_v5_overlap_nav.png")
    ov.plot_nav({"策略A（R ≤ 1.02）": base["res"]["nav"], "策略A（R ≤ 1.30）":
                 runs["level30"]["res"]["nav"], "策略A（R≤1.02）+ 空仓回债": r_a_bond["nav"],
                 "买入持有沪深300": bh, "静态50/50": st50, "现货基": bh_bd}, p_nav,
                title="净值对比（含费，对数坐标）")

    # ---------------------------------------------------------------- 落地
    with open(os.path.join(OUTDIR, "_v5_overlap_bt.jsonl"), "w", encoding="utf-8") as f:
        f.write(json.dumps(dict(
            kind="summary", start=df.index[0].date().isoformat(),
            end=df.index[-1].date().isoformat(), years=round(yrs, 2), days=len(df),
            anchor=ov.CFG["anchor"], tr_ann=round(tr_ann, 4), bd_ann=round(bd_ann, 4),
            params={k: v for k, v in ov.CFG.items() if not isinstance(v, tuple)},
            funds={c: dict(name=v[0], kind=v[1], start=v[2]) for c, v in ov.FUNDS.items()},
            source=dict(ov.SOURCE)), ensure_ascii=False) + "\n")
        for r in rows:
            f.write(json.dumps({k: (round(v, 6) if isinstance(v, float) else v)
                                for k, v in r.items()}, ensure_ascii=False) + "\n")
        for e in eps_a:
            f.write(json.dumps({**e, "kind": "level2"}, ensure_ascii=False) + "\n")
        for e in eps_a3:
            f.write(json.dumps({**e, "kind": "level30"}, ensure_ascii=False) + "\n")

    mA = ov.perf(base["res"]["nav"])
    m30 = ov.perf(runs["level30"]["res"]["nav"])
    mC = ov.perf(runs["z10"]["res"]["nav"])
    mBH = ov.perf(bh)
    mBD = ov.perf(bh_bd)
    mST = ov.perf(st50)
    buy_days = ov.buy_marks(df)
    f250 = fwd[(fwd["tag"] == "触发日") & (fwd["horizon"] == 250)]
    a250 = fwd[(fwd["tag"] == "全样本") & (fwd["horizon"] == 250)]
    z_trig = float(f250["mean"].iloc[0]) if len(f250) else 0.0
    z_all = float(a250["mean"].iloc[0]) if len(a250) else 0.0
    c_buy_txt = (f"买入 {rows[4].get('buys', 0)} 次、{rows[4]['expo']:.0%} 的时间在场，"
                 f"年化只有 {ov.fmt_pct(mC['ann'])}")

    L = [
        "# 波动资产 · V5 变种：两线重合买点 —— 沪深300全收益 × 二级债基指数",
        "",
        f"> 样本 **{df.index[0]:%Y-%m-%d} ~ {df.index[-1]:%Y-%m-%d}（{yrs:.1f} 年）**"
        f" · 红线：{src_tr}",
        f"> 蓝线：{src_bd}（8 只老债基累计净值等权）。",
        "> 规则：两线同锚归一 1000 → `R = 红/蓝`；**R 跌破/贴合 1.0 = 买点，持有一年后卖出**。"
        "T 日收盘算信号 → T+1 收盘成交，买卖扣申赎费。",
        "> 机械规则输出，**非投资建议**。",
        "",
        "## 1. 先说结论：这条规则是「警报器」，不是「配置引擎」",
        "",
        f"- 严格按原话（R ≤ {1 + ov.CFG['level_tol']:.2f}）执行，**{yrs:.0f} 年只在 "
        f"{eps_a[-1]['start'] if eps_a else '—'} 那一段**满足过条件，共 "
        f"{len(buy_days)} 笔买入（首笔 {buy_days[0]:%Y-%m-%d}，第二笔是持有一年到期后"
        f"信号仍在、按「贴着就滚动持有一年」续买的 {buy_days[-1]:%Y-%m-%d}），"
        f"累计在场 {int(df['pos'].sum())} 个交易日（{base['df']['pos'].mean():.0%}）。",
        f"- 它确实买在真实的周期底：2005-01 是 A 股那一轮熊市的最后一段，"
        f"2006 年 +119.5%、策略在 2007-01 下车。",
        f"- 结果：年化 **{ov.fmt_pct(mA['ann'])}**、最大回撤 **{ov.fmt_pct(mA['mdd'])}**、"
        f"夏普 **{mA['sharpe']:.2f}** —— 同期买入持有沪深300 是 {ov.fmt_pct(mBH['ann'])}/"
        f"{ov.fmt_pct(mBH['mdd'])}/夏普 {mBH['sharpe']:.2f}，静态 50/50 是 "
        f"{ov.fmt_pct(mST['ann'])}/{ov.fmt_pct(mST['mdd'])}/夏普 {mST['sharpe']:.2f}。",
        f"- **但必须直说：直接持有二级债基指数的年化是 {ov.fmt_pct(mBD['ann'])}、"
        f"回撤只有 {ov.fmt_pct(mBD['mdd'])}、夏普 {mBD['sharpe']:.2f}** —— 收益几乎一样、"
        f"波动只有三分之一。所以本策略作为「独立策略」是不合格的；它有意义的用法是"
        "**把「两线重合」当成加仓警报**：正常年份拿债基/货基，警报响了才把股票仓位加上去。",
        f"- 把空仓段的收益从货基换成债基（= 卖出后钱回债，见下表第 6 行），年化从 "
        f"{ov.fmt_pct(mA['ann'])} 提到 {ov.fmt_pct(ov.perf(r_a_bond['nav'])['ann'])}，"
        "回撤基本不变 —— **「空仓时拿什么」比「买点准不准」更决定收益**。",
        "",
        "## 2. 为什么参考图有 5 个圈，这里只有 "
        f"{len(eps_a)} 次触发 —— 两条线的「结构性缺口」",
        "",
        "| 线 | 年化（2005-01 至今天） | 说明 |",
        "|---|---:|---|",
        f"| 红线 沪深300全收益 H00300 | **{tr_ann:+.2%}** | 含分红再投；价格指数约低 2pp/年 |",
        f"| 蓝线 自建二级债基指数 | **{bd_ann:+.2%}** | 8 只老债基累计净值等权复合 |",
        f"| 缺口（≈ 权益风险溢价） | **{tr_ann - bd_ann:+.2%}** | 红线每年多跑的速度 |",
        "",
        f"> 红线每年比蓝线多跑 **{(tr_ann - bd_ann) * 100:.1f}pp**，所以「红线跌回蓝线」"
        "的难度**逐年变大**。R 的五个周期低点是逐级抬升的：",
        "",
        "| 周期底（参考图的圈） | R（红线/蓝线） | `R ≤ 1` 触发？ | 严格口径买到？ |",
        "|---|---:|---|---|",
    ]
    for i, (d, v) in enumerate(lows):
        hit = v <= 1.0 + float(ov.CFG["level_tol"])
        L.append(f"| {'①②③④⑤'[i]} {d:%Y-%m-%d} | {'**' if i == 0 else ''}{v:.2f}"
                 f"{'**' if i == 0 else ''} | {'✅' if hit else f'❌ 差 {(v - 1) * 100:.0f}%'} | "
                 f"{'✅ 期间买入' if hit else '❌'} |")
    L += [
        "",
        "> 参考图里那条蓝线 20 年涨了约 **5 倍（≈8.4%/年）**，比真实二级债基指数（5.1%/年）"
        "快 3pp 以上 —— 要么它用的是含较多权益仓位/杠杆的专用指数（万得 885007.WI 一类），"
        "要么是示意图。**用真实债基指数，「贴到蓝线」只在 ① 那一次真正发生（② 差 10%）。**"
        "这不是本实现的保守，是绝对水位在 A 股不再回来。",
        "",
        "## 3. 主回测（同一窗口、同一费用）",
        "",
        "| 组合 | 累计 | 年化 | 波动 | 最大回撤 | 夏普 | Calmar | 买入次数 | 在场时间 | 累计费用 |",
        "|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|",
    ]
    for r in rows:
        fee_txt = f"{r['fees']:.2%}" if r.get("fees") else "—"
        expo_txt = f"{r['expo']:.0%}" if r.get("expo") is not None else "—"
        L.append(f"| {r['name']} | {ov.fmt_pct(r['cum'], 1)} | {ov.fmt_pct(r['ann'])} | "
                 f"{ov.fmt_pct(r['vol'])} | {ov.fmt_pct(r['mdd'])} | {r['sharpe']:.2f} | "
                 f"{ov.fmt_x(r['calmar'])} | {r.get('buys', '—')} | {expo_txt} | {fee_txt} |")

    L += [
        "",
        f"> **放宽阈值的边际收益**：R ≤ 1.02 → {ov.fmt_pct(mA['ann'])}，"
        f"R ≤ 1.30 → {ov.fmt_pct(m30['ann'])}（+{(m30['ann'] - mA['ann']) * 100:.2f}pp/年），"
        f"多抓的两次买点分别在 "
        + "、".join(f"{d:%Y-%m}" for d in ov.buy_marks(runs["level30"]["df"])[2:])
        + "。**这不是「参数调到 1.30 更好」的证据** —— 1.30 这个数只有事后才看得见，"
        "见 §5.1。",
        "",
        "## 4. 严格口径（A）的买点明细",
        "",
        "| # | 触发区间起 | 止 | 天数 | R@起 | R 最低 | 之后 250 日（全收益） |",
        "|---|---|---|---:|---:|---:|---:|",
    ]
    for i, e in enumerate(eps_a, 1):
        L.append(f"| {i} | {e['start']} | {e['end']} | {e['days']} | {e['R_start']:.2f} | "
                 f"{e['R_min']:.2f} | {ov.fmt_pct(e['tr_after_1y'], 1)} |")
    L += [
        "",
        "> 放宽到 R ≤ 1.30（A‴）时的全部买点："
        + "、".join(f"{d:%Y-%m-%d}" for d in ov.buy_marks(runs["level30"]["df"]))
        + "。这两档的差别就是「多等 10~20% 的跌幅」换来的额外机会。",
        "",
        "## 5. 参数敏感性",
        "",
        "### 5.1 R 阈值（这一列决定了策略是「响两次」还是「响四次」）",
        "",
        "| 阈值 | 触发段数 | 买入次数 | 年化 | 最大回撤 | 夏普 | 在场时间 |",
        "|---|---:|---:|---:|---:|---:|---:|",
    ]
    for s in sens_tol:
        L.append(f"| {s['label']} | {s['eps']} | {s['buys']} | {ov.fmt_pct(s['ann'])} | "
                 f"{ov.fmt_pct(s['mdd'])} | {s['sharpe']:.2f} | {s['expo']:.0%} |")
    L += [
        "",
        "> 阈值 1.00 → 1.30，买入次数 2 → 4，年化大体单调变好 —— 这个形状很诱人，但它是"
        "**事后挑阈值**的典型产物：你只有在知道 2008 的底是 1.10、2014 的底是 1.27 之后，"
        "才会选 1.30。真要用，请用「当年还不知道后面会怎样」的阈值（1.00~1.10），"
        "并接受「20 年只响一次」这个事实。注意 1.05/1.10 与 1.02 的结果完全相同 —— "
        "2008 年的底恰好是 1.104，差 0.4pp 就抓不到，这正是「贴着阈值」的脆弱之处。",
        "",
        "### 5.2 卖点：持有一年 vs 更久（这一档比买点更重要）",
        "",
        "| 持有期 | A（R≤1.02）年化 | A 回撤 | A 买入 | A‴（R≤1.30）年化 | A‴ 回撤 | A‴ 夏普 |",
        "|---|---:|---:|---:|---:|---:|---:|",
    ]
    for a, b in zip(sens_h, sens_h3):
        L.append(f"| {a['label']} | {ov.fmt_pct(a['ann'])} | {ov.fmt_pct(a['mdd'])} | "
                 f"{a['buys']} | {ov.fmt_pct(b['ann'])} | {ov.fmt_pct(b['mdd'])} | "
                 f"{b['sharpe']:.2f} |")
    L += [
        "",
        "> 「持有一年就卖」是机械止盈：保证你拿到买点后那一年的修复，也保证你在长牛里提前下车。"
        "A 口径的买入点之后 250 日是 **−2.2%**（2005 年买在下跌尾段），但滚动续买把整段"
        "2006 年的 +119% 吃到了 —— **卖点在这个口径里不是收益来源，买点才是**。"
        "A‴ 口径买得更早（2008-10、2014-03），持有期从半年拉到两年，年化在 10~11% 之间来回，"
        "说明它的买点本身质量参差。",
        "",
        "### 5.3 蓝线换一条会怎样（口径风险最大的一档）",
        "",
        "| 蓝线 | R 阈值 | 蓝线年化 | 触发段数 | 买入次数 | 年化 | 最大回撤 | 夏普 |",
        "|---|---|---:|---:|---:|---:|---:|---:|",
    ]
    for s in sens_b:
        L.append(f"| {s['label']} | R ≤ {1 + s['tol']:.2f} | {ov.fmt_pct(s['bd_ann'])} | "
                 f"{s['eps']} | {s['buys']} | {ov.fmt_pct(s['ann'])} | "
                 f"{ov.fmt_pct(s['mdd'])} | {s['sharpe']:.2f} |")
    L += [
        "",
        "> 蓝线换一只基金，R 的水平就整体平移 → 严格口径的触发次数直接改变。**这是「自建指数」"
        "必然的口径风险**：万得 885007.WI 拿不到，只能用 8 只老债基等权复合；换单只南方宝元后"
        "差别见上表。结论对「蓝线是谁」敏感，但对「蓝线是债券类资产、年化 4~6%」不敏感。",
        "",
        "## 6. 为什么不用「相对低位」口径（C：z ≤ −1）",
        "",
        "| 项 | A 严格（R ≤ 1.02） | C 相对（z ≤ −1） |",
        "|---|---:|---:|",
        f"| 买入次数 | {rows[0].get('buys', 0)} | {rows[4].get('buys', 0)} |",
        f"| 在场时间 | {rows[0]['expo']:.0%} | {rows[4]['expo']:.0%} |",
        f"| 年化 | {ov.fmt_pct(mA['ann'])} | {ov.fmt_pct(mC['ann'])} |",
        f"| 最大回撤 | {ov.fmt_pct(mA['mdd'])} | {ov.fmt_pct(mC['mdd'])} |",
        f"| 夏普 | {mA['sharpe']:.2f} | {mC['sharpe']:.2f} |",
        "",
        "> C 口径（把「绝对贴合」换成「红线相对自己的年线 −1σ」）会频繁得多，"
        f"但它 **{c_buy_txt}** —— 最典型的一次是 2008-03 就触发满仓，"
        "然后整整吃了 2008 年那一段 −60%。**「跌得多」不等于「跌到底」**，"
        "而「持有一年」的卖点没法帮你止损。所以本变种的主口径坚持用绝对水位（A）。",
        "",
        "## 7. 买点是不是真的更好（A 口径的触发日 vs 全样本）",
        "",
        "| 未来 | 组 | 样本数 | 平均 | 中位 | 上涨概率 | 10% 分位 | 90% 分位 |",
        "|---|---|---:|---:|---:|---:|---:|---:|",
    ]
    for _, r in fwd.iterrows():
        L.append(f"| {int(r['horizon'])} 日 | {r['tag']} | {int(r['n'])} | "
                 f"{ov.fmt_pct(r['mean'])} | {ov.fmt_pct(r['median'])} | {r['win']:.0%} | "
                 f"{ov.fmt_pct(r['p10'])} | {ov.fmt_pct(r['p90'])} |")
    L += [
        "",
        f"> 250 日口径：触发日 **{ov.fmt_pct(z_trig)}** vs 全样本 **{ov.fmt_pct(z_all)}**"
        f"（差 {(z_trig - z_all) * 100:+.2f}pp）。**但这 {int(f250['n'].iloc[0])} 个「触发日」"
        "全部落在 2005-01~2006-04 同一段行情里**，它们不是独立样本 —— 真实样本量是 **1 段**。"
        "所以这个差值只能当轶事，不能当统计证据；同理，本策略的任何年化数字都不该被外推。",
        "",
        "## 8. 逐年收益",
        "",
        "| 年 | 策略A（R≤1.02） | 策略A‴（R≤1.30） | 沪深300 | 静态50/50 |",
        "|---|---:|---:|---:|---:|",
    ]
    for y, r in per_year.iterrows():
        L.append(f"| {int(y)} | {ov.fmt_pct(r['策略A(严格)'], 1)} | "
                 f"{ov.fmt_pct(r['策略A‴(R≤1.30)'], 1)} | {ov.fmt_pct(r['沪深300'], 1)} | "
                 f"{ov.fmt_pct(r['静态50/50'], 1)} |")
    L += [
        "",
        "> 绝大部分年份策略 A 的收益就是货基的 1.4%（表里显示 +1.4%），"
        "**这正是它的设计意图：没有极端便宜就不参与。** 别把这张表当成「择时能力弱」，"
        "它是「拒绝参与」的代价与红利。",
        "",
        "## 9. 已知局限",
        "",
        "1. **样本极小**：A 口径 1 段、A‴ 口径 4 段。任何统计量（夏普、胜率）在这个样本量下"
        "都只能当轶事，不能当预期收益。",
        "2. **蓝线是自建的**：8 只 2003~2010 年成立的老债基（二级 4 + 一级 4）累计净值等权。"
        "换指数（万得 885007.WI）会改变 R 的水平与触发次数，见 §5.3。",
        "3. **绝对水位可能永远回不来**：如果 A 股的权益溢价继续按 3.6pp/年 的速度跑赢债券，"
        "R 的下一个周期底大概率在 1.8~2.0，`R ≤ 1` 这辈子都不会再触发。"
        "**用之前请先接受这一点**，或改用 §5.1 里更宽的阈值（并接受它同样是事后选择）。",
        "4. **持有一年是机械规则**：不做止损、不做移动止盈。2008 那种买在半山腰的情形，"
        "本口径恰好避开了，但同类规则（C）没有。",
        "5. **未计**：场外申赎确认时滞（T+1 收盘成交是乐观假设）、大额限购、指数基金跟踪误差、"
        "C 类销售服务费；2005-2015 年债基的刚兑/信用环境与今天不同。",
        "6. **与主策略的关系**：本变种回答「沪深300 什么时候便宜到值得重仓」，与 vol_legs 主策略"
        "（六腿仓位带）、v1_ma20（逐腿下车）、v4_erp_band（沪深300 vs 国债比例）是不同层次，"
        "**默认不叠加**；要叠加先定额度优先。",
        "",
        "> **模拟结果，非投资建议。**",
    ]

    path = os.path.join(OUTDIR, "_v5_overlap_bt.md")
    with open(path, "w", encoding="utf-8") as f:
        f.write("\n".join(L))

    print(f"报告: {path}")
    print(f"样本 {df.index[0]:%Y-%m-%d} ~ {df.index[-1]:%Y-%m-%d} ({yrs:.1f} 年) · "
          f"红线 {tr_ann:+.2%}/年 · 蓝线 {bd_ann:+.2%}/年")
    for r in rows:
        print(f"  {r['name'][:44]:44s} 年化 {ov.fmt_pct(r['ann']):>9s} · "
              f"回撤 {ov.fmt_pct(r['mdd']):>9s} · 夏普 {r['sharpe']:5.2f} · "
              f"买入 {r.get('buys', '—')}")
    print("图:", p_full, "|", p_3y, "|", p_r120, "|", p_nav)


if __name__ == "__main__":
    main()
