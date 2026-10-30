# -*- coding: utf-8 -*-
"""波动资产 · V4 变种：股债利差（ERP）通道 · 沪深300 × 10Y 国债 · 回测与校准。

口径（全部由 `erp.py` 提供，本文件只做组合与报表）：
- 价格：沪深300 收盘（中证官网）+ 合成 10Y 国债指数（`erp.bond_synthetic`，D = 8）
- 信号：ERP = 100/PE − 10Y，滚动 250 日 MA ± 2σ 通道，状态机见 `erp.signal_series`
- 成交：T 日收盘算信号 → **T+1 收盘净值成交**（无前视），买卖两侧各扣申赎费
- 再平衡：只在信号切换日动手；月末仅在偏离 > 5pp 时回锚
- 绩效：`engine.metrics`（rf = 货基 1.40%），与主策略同一套算法

产出（相对本文件）：
- backtest/_v4_erp_band_bt.md / .jsonl
- backtest/_v4_erp_band.png         全历史的「利差 → 沪深300」双面板（复刻参考图）
- backtest/_v4_erp_band_3y.png      近三年同一张图
- backtest/_v4_erp_band_nav.png     净值对比

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

import erp  # noqa: E402

OUTDIR = os.path.join(_HERE, "backtest")
YEARS_3Y = 3


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
    base = pd.concat([first, y])
    return (base.pct_change().dropna()).rename(lambda d: d.year)


def align(df, t0=None):
    """所有对比档都截到**同一个起点**（默认 = 基准参数的 ready 首日）。"""
    d = df[df["ready"]]
    return d.loc[t0:] if t0 is not None else d


def bond_series(kind="synth", duration=None, start=None):
    """债券腿 -> Series。kind ∈ synth / real。"""
    if kind == "real":
        s = erp.bond_real()
        if s is None:
            raise RuntimeError("缺 data/_bt_caches/nav_003377.csv（真实债基）")
    else:
        s = erp.bond_synthetic(erp.load_bond10y(), duration=duration)
    return s.loc[start:] if start is not None else s


def evaluate(df, bond, t0=None, rebal_freq=None, hold_neutral=None, eq=None,
             exit_frac=None, fees=True, reverse=False):
    """按给定参数跑一遍 -> dict(nav, m, trades, w, frame, fee)。

    eq = (低估, 中性, 高估) 的沪深300 占比；reverse=True 把仓位对调（对照「方向是不是噪声」）。
    """
    d = align(df, t0)
    ec, en, ex = eq or (erp.CFG["eq_cheap"], erp.CFG["eq_neutral"], erp.CFG["eq_expan"])
    if reverse:
        ec, ex = ex, ec
    tgt = erp.target_weight(d["state"], eq_cheap=ec, eq_neutral=en, eq_expan=ex,
                            hold_neutral=hold_neutral)
    pe = d["tr"].astype("float64")                     # 股票腿 = 全收益指数（含分红再投）
    pb = bond.reindex(pe.index).ffill()
    ok = pb.notna()
    pe, pb, tgt = pe[ok], pb[ok], tgt[ok]
    d = d.loc[pe.index]
    fe = erp.CFG["fee_eq"] if fees else (0.0, 0.0)
    fb = erp.CFG["fee_bd"] if fees else (0.0, 0.0)
    r = erp.run(pe, pb, tgt, period_end=erp.period_end_flags(pe.index, rebal_freq),
                fee_eq=fe, fee_bd=fb)
    r["m"] = erp.perf(r["nav"])
    r["frame"] = d
    return r


def static_bench(bond, pe, w=0.5, freq="M", fees=True):
    """静态 w 仓位的股债组合（月末回锚）-> run 结果 dict。"""
    pb = bond.reindex(pe.index).ffill()
    tgt = pd.Series(float(w), index=pe.index)
    fe = erp.CFG["fee_eq"] if fees else (0.0, 0.0)
    fb = erp.CFG["fee_bd"] if fees else (0.0, 0.0)
    r = erp.run(pe, pb, tgt, period_end=erp.period_end_flags(pe.index, freq),
                fee_eq=fe, fee_bd=fb)
    r["m"] = erp.perf(r["nav"])
    return r


def row(name, nav, note="", r=None):
    m = erp.perf(nav)
    out = dict(name=name, cum=m["cum"], ann=m["ann"], vol=m["vol"], mdd=m["mdd"],
               sharpe=m["sharpe"], calmar=(m["ann"] / abs(m["mdd"]) if m["mdd"] else None),
               note=note)
    if r is not None:
        out["trades"] = len(r["trades"])
        out["fees"] = r["fees"]
        out["w_avg"] = float(r["w"].mean())
        out["switches"] = sum(1 for t in r["trades"] if t["why"] == "signal")
        out["rebal"] = sum(1 for t in r["trades"] if t["why"] == "rebal")
    return out


# ---------------------------------------------------------------- 敏感性
def sens(df, bond, t0, key, values, base_kw=None):
    """逐档重算 -> rows。key ∈ window/k/exit_frac/rebal_freq/hold_neutral/bond。

    ⚠ 窗口/轨宽变体会改变 `ready` 首日：长窗口的样本**起点更晚**（要预热更多天）。
    所以每一档都必须在**它自己的窗口**上重算静态 50/50 作为对照（`b50`），
    否则「年化高」可能只是「少经历了几年熊市」。`start` 一并输出，便于核对。
    """
    base_kw = dict(base_kw or {})
    rows = []
    for v in values:
        label = v if isinstance(v, str) else str(v)
        kw = dict(base_kw)
        if key == "window":
            kw["df"] = erp.erp_frame(window=v)
            label = f"{v} 日"
        elif key == "k":
            kw["df"] = erp.erp_frame(k=v)
            label = f"±{v:g}σ"
        elif key == "bond":
            kw["bond"] = bond_series(v[0], duration=v[1])
            label = v[2]
        else:
            kw[key] = v
            if key == "exit_frac":
                kw["df"] = erp.erp_frame(exit_frac=v)
                label = ("回穿均线" if not v else f"MA+{v:g}σ 才退出")
        d = kw.pop("df", df)
        bd = kw.pop("bond", bond)
        r = evaluate(d, bd, t0=t0, **kw)
        fr = r["frame"]
        b50 = erp.perf(static_bench(bd, fr["tr"], 0.5)["nav"])
        yrs = (fr.index[-1] - fr.index[0]).days / 365.25
        rows.append(dict(label=label, start=fr.index[0], years=yrs,
                         ann=r["m"]["ann"], b50=b50["ann"],
                         edge=(r["m"]["ann"] - b50["ann"]) * 100,
                         mdd=r["m"]["mdd"], sharpe=r["m"]["sharpe"],
                         trades=len(r["trades"]),
                         w_avg=float(r["w"].mean()), fees=r["fees"]))
    return rows


# ---------------------------------------------------------------- 报告
def main():
    os.makedirs(OUTDIR, exist_ok=True)
    df = erp.erp_frame()
    t0 = align(df).index[0]
    bond = bond_series("synth")
    base = evaluate(df, bond, t0=t0)
    d = base["frame"]
    pe = d["tr"]                                       # 股票腿：全收益指数
    px = d["close"]                                    # 价格指数（只用于展示 / 画图）
    nav_s, m_s = base["nav"], base["m"]

    # 基准
    bh_eq = pe / pe.iloc[0]
    pb = bond.reindex(pe.index).ffill()
    bh_bd = pb / pb.iloc[0]
    st50 = static_bench(bond, pe, 0.50)
    st80 = static_bench(bond, pe, 0.80)
    rev = evaluate(df, bond, t0=t0, reverse=True)
    nofee = evaluate(df, bond, t0=t0, fees=False)

    rows = [
        row("**策略（ERP 通道 80/50/20）**", nav_s, "默认参数：250 日 / ±2σ / 回穿均线 / 月末回锚", base),
        row("策略（不计费）", nofee["nav"], "同规则，费率置 0 → 单看摩擦成本", nofee),
        row("**反向信号（低估 20 / 高估 80）**", rev["nav"], "把仓位对调：检验「方向是不是噪声」", rev),
        row("静态 50/50（月末回锚）", st50["nav"], "不择时的基准", st50),
        row("静态 80/20（月末回锚）", st80["nav"], "一直满仓股票端", st80),
        row("买入持有沪深300（全收益）", bh_eq, "纯股票，含分红再投"),
        row("买入持有债券（合成 10Y，D=8）", bh_bd, "纯债券"),
    ]

    # 前瞻收益 / 信号区间
    fwd = erp.forward_stats(d)
    eps = erp.episodes(d)
    yrs = (d.index[-1] - d.index[0]).days / 365.25
    vc = d["state"].value_counts().to_dict()
    tot = len(d)

    # 分半样本外
    mid = d.index[len(d) // 2]
    half = []
    for tag, lo, hi in (("前半段", None, mid), ("后半段", mid, None)):
        dd = df.loc[:hi] if lo is None else df.loc[lo:]
        sub = align(dd)
        if len(sub) < 300:
            continue
        rr = evaluate(dd, bond, t0=sub.index[0])
        bb = static_bench(bond, sub["tr"], 0.50)["nav"]
        half.append(dict(tag=tag, start=sub.index[0], end=sub.index[-1],
                         ann=rr["m"]["ann"], mdd=rr["m"]["mdd"], sharpe=rr["m"]["sharpe"],
                         b50=erp.perf(bb)["ann"], b50_mdd=erp.perf(bb)["mdd"],
                         eq=erp.perf(sub["tr"] / sub["tr"].iloc[0])["ann"],
                         w=float(rr["w"].mean())))

    per_year = pd.DataFrame({
        "策略": yearly_returns(nav_s),
        "静态50/50": yearly_returns(st50["nav"]),
        "沪深300": yearly_returns(bh_eq),
    }).dropna(how="all")

    sens_win = sens(df, bond, t0, "window", [120, 250, 500, 750, 1250])
    sens_k = sens(df, bond, t0, "k", [1.5, 2.0, 2.5, 3.0])
    sens_exit = sens(df, bond, t0, "exit_frac", [0.0, 0.5])
    sens_rebal = sens(df, bond, t0, "rebal_freq", ["M", "Q", None])
    sens_nh = sens(df, bond, t0, "hold_neutral", [False, True])
    sens_bond = sens(df, bond, t0, "bond",
                     [("synth", 4.0, "合成债 D=4"), ("synth", 6.0, "合成债 D=6"),
                      ("synth", 8.0, "合成债 D=8（默认）"), ("real", None, "真实债基 003377")])

    # ---------------------------------------------------------------- 图（只留两张）
    # 主图：单面板双 Y 轴（复刻 Wind 截图）—— 利差 vs 指数，看两条线怎么反向走
    cut = df.index[-1] - pd.Timedelta(days=365 * YEARS_3Y)
    p_full = os.path.join(OUTDIR, "_v4_erp_band.png")
    erp.plot_overlay(df, p_full, title="股债利差 vs 沪深300",
                     subtitle=f"{df.index[0]:%Y-%m-%d} 至 {df.index[-1]:%Y-%m-%d}（日线）"
                              f" · 通道 {erp.CFG['window']} 日 / ±{erp.CFG['k']:g}σ"
                              f" · 均线与上下轨自 {t0:%Y-%m-%d} 起（250 日预热）")
    p_3y = os.path.join(OUTDIR, "_v4_erp_band_3y.png")
    erp.plot_overlay(df, p_3y, title="近三年：股债利差 vs 沪深300", start=cut,
                     subtitle=f"{cut:%Y-%m-%d} 至 {df.index[-1]:%Y-%m-%d}（日线 · 局部放大）")

    # ---------------------------------------------------------------- 落地
    jl = os.path.join(OUTDIR, "_v4_erp_band_bt.jsonl")
    with open(jl, "w", encoding="utf-8") as f:
        f.write(json.dumps(dict(kind="summary", start=d.index[0].date().isoformat(),
                                end=d.index[-1].date().isoformat(),
                                years=round(yrs, 2), days=len(d),
                                state_share={str(k): round(v / tot, 4) for k, v in vc.items()},
                                window=erp.CFG["window"], k=erp.CFG["k"],
                                exit_frac=erp.CFG["exit_frac"],
                                params={k: v for k, v in erp.CFG.items()
                                        if not isinstance(v, tuple)},
                                source=dict(erp.SOURCE)), ensure_ascii=False) + "\n")
        for r in rows:
            f.write(json.dumps({k: (round(v, 6) if isinstance(v, float) else v)
                                for k, v in r.items()}, ensure_ascii=False) + "\n")
        for e in eps:
            f.write(json.dumps({**e, "kind": ("低估" if e["kind"] == 1 else "高估")},
                               ensure_ascii=False) + "\n")

    exit_txt = ("回穿均线（MA）" if not erp.CFG["exit_frac"]
                else f"回到 MA+{erp.CFG['exit_frac']:g}σ")

    L = [
        "# 波动资产 · V4 变种：股债利差（ERP）通道 —— 沪深300 × 10Y 国债",
        "",
        f"> 样本 **{d.index[0]:%Y-%m-%d} ~ {d.index[-1]:%Y-%m-%d}（{yrs:.1f} 年）**"
        f" · 数据：{erp.SOURCE.get('hs300')} / {erp.SOURCE.get('bond')}",
        "> 信号：`ERP = 100/PE − 10Y`，滚动 250 日 MA ± 2σ 通道；"
        "**T 日收盘算信号 → T+1 收盘成交**，买卖两侧扣申赎费，月末偏离 >5pp 才回锚。",
        "> 机械规则输出，**非投资建议**。",
        "",
        "## 1. 规则与参数（权威源 `erp.py::CFG`）",
        "",
        "| 项 | 取值 | 说明 |",
        "|---|---|---|",
        f"| 通道窗口 | {erp.CFG['window']} 交易日 | 视频没披露，先用 ≈1 年；敏感性见 §5 |",
        f"| 轨宽 | ±{erp.CFG['k']:g}σ（总体标准差） | 低估：ERP ≥ 上轨；高估：ERP ≤ 下轨（对称） |",
        f"| 退出 | {exit_txt} | 滞回带 = 进 2σ / 出 0σ，防止轨道附近反复翻信号 |",
        f"| 仓位 | 低估 **{erp.CFG['eq_cheap']:.0%}** / 中性 **{erp.CFG['eq_neutral']:.0%}** / "
        f"高估 **{erp.CFG['eq_expan']:.0%}** | 沪深300 占比（of 股债两部分） |",
        f"| 再平衡 | 信号切换日必动；{erp.CFG['rebal_freq']} 末偏离 >{erp.CFG['rebal_tol']:.0%} 才回锚 |"
        " 「避免日内频繁交易」 |",
        f"| 成本 | 申购 {erp.CFG['fee_eq'][0]:.2%} / 赎回 {erp.CFG['fee_eq'][1]:.2%}（两条腿各自） | "
        "切换一次两条腿都要付 |",
        f"| 债券腿 | 10Y 收益率合成（久期 {erp.CFG['bond_duration']:g} 年） | "
        "与信号同一个 instrument；真实债基对照见 §5 |",
        "",
        f"> 状态占比（{tot} 个交易日）：" + " / ".join(
            f"{n} **{vc.get(k, 0) / tot:.1%}**" for k, n in ((1, "低估"), (0, "中性"), (-1, "高估")))
        + "。换句话说，多数时间仓位并不在 50% 这个「中性」档上 —— 这条规则的"
        "**换手不高，但仓位会长期停在极端档**。",
        "",
        "## 2. 主回测（同一样本、同一债券腿、含费）",
        "",
        "| 组合 | 累计 | 年化 | 波动 | 最大回撤 | 夏普 | Calmar | 平均股票仓位 | 信号切换 | 月末回锚 | 累计费用 |",
        "|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|",
    ]
    for r in rows:
        w_txt = f"{r['w_avg']:.0%}" if "w_avg" in r else "—"
        fee_txt = f"{r['fees']:.2%}" if "fees" in r else "—"
        L.append(
            f"| {r['name']} | {erp.fmt_pct(r['cum'], 1)} | {erp.fmt_pct(r['ann'])} | "
            f"{erp.fmt_pct(r['vol'])} | {erp.fmt_pct(r['mdd'])} | {r['sharpe']:.2f} | "
            f"{erp.fmt_x(r['calmar'])} | {w_txt} | "
            f"{r.get('switches', '—')} | {r.get('rebal', '—')} | {fee_txt} |")

    d_ann = (m_s["ann"] - st50["m"]["ann"]) * 100
    d_eq = (m_s["ann"] - erp.perf(bh_eq)["ann"]) * 100
    d_rev = (m_s["ann"] - rev["m"]["ann"]) * 100
    L += [
        "",
        f"> **策略 vs 静态 50/50：{d_ann:+.2f}pp/年**（回撤 {erp.fmt_pct(m_s['mdd'])} vs "
        f"{erp.fmt_pct(st50['m']['mdd'])}，夏普 {m_s['sharpe']:.2f} vs "
        f"{st50['m']['sharpe']:.2f}）。",
        f"> **策略 vs 买入持有沪深300：{d_eq:+.2f}pp/年**。",
        f"> **方向检验**：反向信号（低估 20 / 高估 80）{erp.perf(rev['nav'])['ann']:+.2%}，"
        f"与正方向差 **{d_rev:+.2f}pp/年** —— "
        + ("两者差距明显，说明「利差高 → 多配股票」这个方向不是噪声。"
           if abs(d_rev) > 1.0 else
           "差距不到 1pp，说明在本样本里**这个方向基本是噪声**，请只把它当情绪刻度。"),
        "",
        "## 3. 「低估 ≠ 立刻上涨」：各状态下的未来收益（沪深300 全收益）",
        "",
        "| 未来 | 状态 | 样本数 | 平均 | 中位 | 上涨概率 | 10% 分位 | 90% 分位 |",
        "|---|---|---:|---:|---:|---:|---:|---:|",
    ]
    for _, r in fwd.iterrows():
        L.append(f"| {int(r['horizon'])} 日 | {r['state']} | {int(r['n'])} | "
                 f"{erp.fmt_pct(r['mean'])} | {erp.fmt_pct(r['median'])} | {r['win']:.0%} | "
                 f"{erp.fmt_pct(r['p10'])} | {erp.fmt_pct(r['p90'])} |")

    ch = fwd[(fwd["state"] == "低估(+1)") & (fwd["horizon"] == 60)]
    ex = fwd[(fwd["state"] == "高估(-1)") & (fwd["horizon"] == 60)]
    ch60 = float(ch["mean"].iloc[0]) if len(ch) else 0.0
    ex60 = float(ex["mean"].iloc[0]) if len(ex) else 0.0
    L += [
        "",
        f"> 60 日口径：低估态平均 **{erp.fmt_pct(ch60)}**（上涨概率 "
        f"{float(ch['win'].iloc[0]):.0%}）vs 高估态 **{erp.fmt_pct(ex60)}**"
        f"（{float(ex['win'].iloc[0]):.0%}）—— 差 **{(ch60 - ex60) * 100:+.2f}pp**。"
        "这个差值就是「利差择时」的全部原料，它并不大，且方差很宽（看 10%/90% 分位）。",
        "> 换句话说：低估只说明**赔率**变好，不保证**胜率**；历史上低估态持续一年以上是常态（见 §4 区间表）。",
        "",
        "## 4. 历史信号区间（最近的 12 段）",
        "",
        "| 状态 | 起 | 止 | 交易日 | ERP 均值 | ERP 极值 | 沪深300 起点 | 终点 | 区间涨跌 |",
        "|---|---|---|---:|---:|---:|---:|---:|---:|",
    ]
    for e in eps[:12]:
        L.append(f"| {'低估' if e['kind'] == 1 else '高估'} | {e['start']} | {e['end']} | "
                 f"{e['days']} | {e['erp_mean']:.2f}% | "
                 f"{erp.fmt_x(e['erp_max'] if e['kind'] == 1 else e['erp_min'])}% | "
                 f"{e['px_start']:.0f} | {e['px_end']:.0f} | "
                 f"{erp.fmt_pct(e['px_end'] / e['px_start'] - 1.0)} |")

    L += [
        "",
        f"> 共 {len(eps)} 段信号区间；当前（{d.index[-1]:%Y-%m-%d}）处于"
        f"**{'低估' if int(d['state'].iloc[-1]) == 1 else ('高估' if int(d['state'].iloc[-1]) == -1 else '中性')}**"
        f" 第 {int((d['state'] == d['state'].iloc[-1])[::-1].cumprod().sum())} 个交易日。",
        "",
        "## 5. 参数敏感性",
        "",
        "> ⚠ 每张表的最后一列「同窗口 50/50」是**在该档自己的样本区间上**重算的静态 50/50 基准。"
        "窗口越长、样本起点越晚（要预热更多天），拿绝对年化跨行比较会误判 —— 要比的是「超额」。",
        "",
        "### 5.1 滚动窗口",
        "",
        "| 窗口 | 起点 | 年数 | 年化 | **同窗口 50/50** | 超额 | 最大回撤 | 夏普 | 平均股票仓位 | 交易次数 |",
        "|---|---|---:|---:|---:|---:|---:|---:|---:|---:|",
    ]
    for s in sens_win:
        L.append(f"| {s['label']} | {s['start']:%Y-%m-%d} | {s['years']:.1f} | "
                 f"{erp.fmt_pct(s['ann'])} | {erp.fmt_pct(s['b50'])} | **{s['edge']:+.2f}pp** | "
                 f"{erp.fmt_pct(s['mdd'])} | {s['sharpe']:.2f} | {s['w_avg']:.0%} | {s['trades']} |")
    w_edge = [s["edge"] for s in sens_win]
    L += [
        f"> 各档超额 {min(w_edge):+.2f}pp ~ {max(w_edge):+.2f}pp（中位 "
        f"{pd.Series(w_edge).median():+.2f}pp）。"
        "窗口**越长超额越高**这个现象要打折看：长窗口的样本起点被推后 2~4 年"
        "（2012-08 → 2014-06 → 2016-06），而 1250 日又掉回"
        f" {sens_win[-1]['edge']:+.2f}pp —— **不单调**，说明多半是「赶上了哪一段」，"
        "不是「窗口越长越对」。**别按这张表去挑最优窗口**，那就是最标准的过拟合。",
        "",
        "### 5.2 轨宽 k",
        "",
        "| 轨宽 | 起点 | 年化 | 同窗口 50/50 | 超额 | 最大回撤 | 夏普 | 平均股票仓位 | 交易次数 |",
        "|---|---|---:|---:|---:|---:|---:|---:|---:|",
    ]
    for s in sens_k:
        L.append(f"| {s['label']} | {s['start']:%Y-%m-%d} | {erp.fmt_pct(s['ann'])} | "
                 f"{erp.fmt_pct(s['b50'])} | **{s['edge']:+.2f}pp** | {erp.fmt_pct(s['mdd'])} | "
                 f"{s['sharpe']:.2f} | {s['w_avg']:.0%} | {s['trades']} |")
    k1, k2 = sens_k[0], sens_k[1]
    L += [
        f"> k 从 2σ 收到 1.5σ：交易 {k2['trades']} → {k1['trades']} 次，超额 "
        f"{k2['edge']:+.2f}pp → {k1['edge']:+.2f}pp。"
        + ("**交易更多、超额没变多** —— 这就是「过路费换噪声」。"
           if k1["edge"] <= k2["edge"] else "这一档里收窄轨道反而更好，但差异在噪声量级内。"),
        "",
        "### 5.3 退出规则 / 再平衡频率 / 中性态处理",
        "",
        "| 档 | 年化 | 同窗口 50/50 | 超额 | 最大回撤 | 夏普 | 平均股票仓位 | 交易次数 |",
        "|---|---:|---:|---:|---:|---:|---:|---:|",
    ]
    for s in sens_exit:
        L.append(f"| 退出＝{s['label']} | {erp.fmt_pct(s['ann'])} | {erp.fmt_pct(s['b50'])} | "
                 f"**{s['edge']:+.2f}pp** | {erp.fmt_pct(s['mdd'])} | {s['sharpe']:.2f} | "
                 f"{s['w_avg']:.0%} | {s['trades']} |")
    for s, nm in zip(sens_rebal, ("月末回锚", "季末回锚", "只在信号切换时动")):
        L.append(f"| 再平衡＝{nm} | {erp.fmt_pct(s['ann'])} | {erp.fmt_pct(s['b50'])} | "
                 f"**{s['edge']:+.2f}pp** | {erp.fmt_pct(s['mdd'])} | {s['sharpe']:.2f} | "
                 f"{s['w_avg']:.0%} | {s['trades']} |")
    for s, nm in zip(sens_nh, ("中性态 = 50%", "中性态 = 维持上期")):
        L.append(f"| {nm} | {erp.fmt_pct(s['ann'])} | {erp.fmt_pct(s['b50'])} | "
                 f"**{s['edge']:+.2f}pp** | {erp.fmt_pct(s['mdd'])} | {s['sharpe']:.2f} | "
                 f"{s['w_avg']:.0%} | {s['trades']} |")
    L += [
        "> 「退出＝回穿均线」与「回到 MA+0.5σ 才退出」的差别就是**滞回带宽度**："
        "退得更早 → 仓位更早回到 50% → 交易更多、极端仓位更少。"
        "这两种口径的差异通常小于窗口/轨宽的差异，**不值得为它调参**。",
        "",
        "### 5.4 债券腿口径（这是最容易被忽略、但影响最大的一档）",
        "",
        "| 债券腿 | 年化 | 同窗口 50/50 | 超额 | 最大回撤 | 夏普 | 平均股票仓位 |",
        "|---|---:|---:|---:|---:|---:|---:|",
    ]
    for s in sens_bond:
        L.append(f"| {s['label']} | {erp.fmt_pct(s['ann'])} | {erp.fmt_pct(s['b50'])} | "
                 f"**{s['edge']:+.2f}pp** | {erp.fmt_pct(s['mdd'])} | {s['sharpe']:.2f} | "
                 f"{s['w_avg']:.0%} |")
    syn = [s for s in sens_bond if "默认" in s["label"]][0]
    real = [s for s in sens_bond if "003377" in s["label"]][0]
    L += [
        f"> 换债券腿对**超额**几乎没影响（合成 D=8 {syn['edge']:+.2f}pp vs 真实债基 "
        f"{real['edge']:+.2f}pp），但**绝对收益**差一个量级：真实债基那一行样本只有 2016-09 之后"
        f"（{real['years']:.1f} 年），年化 {erp.fmt_pct(real['ann'])} vs 合成 {erp.fmt_pct(syn['ann'])}"
        " —— 差异来自样本不同 + D=8 的合成券隐含了 10Y 长端的全部资本利得，而 `003377` 是久期更短的"
        "信用债基金。**结论：这条规则的价值不取决于债券腿选什么，但你的组合波动取决于它。**",
        "> 久期越长，债券腿波动越大 —— 你若用短债基金（久期 1~2 年），整个组合的波动会明显更低、"
        "「80% 股票」也会显得更激进。**先确定你实际买的债券是什么，再看这张表。**",
        "",
        "## 6. 分半样本（样本外）",
        "",
        "| 区间 | 起 ~ 止 | 年数 | 策略年化 | 策略回撤 | 夏普 | 静态50/50 年化 | 静态50/50 回撤 | 沪深300 年化 | 平均股票仓位 |",
        "|---|---|---:|---:|---:|---:|---:|---:|---:|---:|",
    ]
    for h in half:
        L.append(f"| {h['tag']} | {h['start']:%Y-%m-%d} ~ {h['end']:%Y-%m-%d} | "
                 f"{(h['end'] - h['start']).days / 365.25:.1f} | {erp.fmt_pct(h['ann'])} | "
                 f"{erp.fmt_pct(h['mdd'])} | {h['sharpe']:.2f} | {erp.fmt_pct(h['b50'])} | "
                 f"{erp.fmt_pct(h['b50_mdd'])} | {erp.fmt_pct(h['eq'])} | {h['w']:.0%} |")
    if len(half) == 2:
        a, b = half
        L += [
            "",
            f"> 前半段超额 **{(a['ann'] - a['b50']) * 100:+.2f}pp**、后半段 "
            f"**{(b['ann'] - b['b50']) * 100:+.2f}pp**（相对静态 50/50）。"
            + ("两段同号 → 规则不是只在某一段行情里成立。"
               if (a["ann"] - a["b50"]) * (b["ann"] - b["b50"]) > 0 else
               "**两段反号 → 规则的优势不稳定**，这正是「一套参数打天下」最危险的地方。"),
        ]

    L += [
        "",
        "## 7. 逐年收益",
        "",
        "| 年 | 策略 | 静态 50/50 | 沪深300 | 超额（vs 50/50） |",
        "|---|---:|---:|---:|---:|",
    ]
    for y, r in per_year.iterrows():
        gap = (r["策略"] - r["静态50/50"]) * 100
        L.append(f"| {int(y)} | {erp.fmt_pct(r['策略'])} | {erp.fmt_pct(r['静态50/50'])} | "
                 f"{erp.fmt_pct(r['沪深300'])} | {gap:+.1f}pp |")
    win_y = int((per_year["策略"] > per_year["静态50/50"]).sum())
    L += [
        "",
        f"> 逐年跑赢静态 50/50 **{win_y}/{len(per_year)}** 年。",
        "",
        "## 8. 结论与已知局限",
        "",
        f"1. **它不是收益机器**：策略年化 {erp.fmt_pct(m_s['ann'])}，相对静态 50/50 "
        f"{d_ann:+.2f}pp、相对买入持有沪深300 {d_eq:+.2f}pp（回撤 "
        f"{erp.fmt_pct(m_s['mdd'])} vs {erp.fmt_pct(erp.perf(bh_eq)['mdd'])}）。"
        "它买到的是**仓位纪律**——利差高就敢多拿股票、利差低就主动降下来，而不是预测涨跌。",
        f"2. **低估 ≠ 立刻上涨**：低估态下 60 日平均 {erp.fmt_pct(ch60)}、上涨概率 "
        f"{float(ch['win'].iloc[0]):.0%}，10% 分位仍有 {erp.fmt_pct(float(ch['p10'].iloc[0]))} —— "
        "历史上低估态动辄持续一年以上（§4 有区间长度），中途还会继续下跌。",
        "3. **PE 口径会平移整条曲线**：本实现用中证官网 `peg`（滚动 PE，2011-06 起）。"
        "换静态 PE 或换 Wind 数据，ERP 的水平会变，但**通道是相对自己的 MA±2σ**，"
        "平移在 MA/STD 里相互抵消 → 信号与时点基本不变。真正会变的是「ERP 的绝对读数」。",
        "4. **10Y 国债 vs 你的债券腿是两回事**：信号用 10Y 收益率，回测债券腿用 D=8 的合成国债指数。"
        "若你实际持有短债/货币，回测里的债券腿波动被**放大**了（§5.4）。",
        f"5. **样本只有 {yrs:.1f} 年，且起点 2011-06（PE 最早可得的日期）** —— "
        "参考图从 2005 年起（Wind 数据），含 2008 年那种「利差飙到天上」的极端低估区，"
        "本样本**没有**覆盖到。所以图中的历史结构比本报告更极端，别把本报告的结论外推到 2008 型危机。",
        "6. **未计**：场外申赎确认时滞（T+1~T+2）、大额限购、指数基金跟踪误差、"
        "分红再投的税务差异、以及「PE 数据在中证官网遇到口径调整」的风险。",
        "7. **债券腿的资本利得是近似**：D×Δy 忽略凸性与骑乘，长端利率急变时误差最大。",
        "",
        "> **模拟结果，非投资建议。**",
    ]

    path = os.path.join(OUTDIR, "_v4_erp_band_bt.md")
    with open(path, "w", encoding="utf-8") as f:
        f.write("\n".join(L))

    # 控制台摘要
    print(f"报告: {path}")
    print(f"样本 {d.index[0]:%Y-%m-%d} ~ {d.index[-1]:%Y-%m-%d} ({yrs:.1f} 年)")
    for r in rows:
        w_txt = f"{r['w_avg']:.0%}" if "w_avg" in r else "—"
        print(f"  {r['name'][:34]:34s} 年化 {erp.fmt_pct(r['ann']):>9s} · "
              f"回撤 {erp.fmt_pct(r['mdd']):>9s} · 夏普 {r['sharpe']:5.2f} · 仓位 {w_txt}")
    print("图:", p_full, "|", p_3y)


if __name__ == "__main__":
    main()
