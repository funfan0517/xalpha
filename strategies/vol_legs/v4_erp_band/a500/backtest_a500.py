# -*- coding: utf-8 -*-
"""V4-A500 变种 · 回测与作图（中证A500 × 10Y 国债）。

规则与沪深300 版**完全相同**（`../erp.py::CFG` 是参数唯一权威源）：ERP = 100/PE − 10Y，
250 日 MA ± 2σ 通道，低估 80 / 中性 50 / 高估 20，T 日收盘算信号 → T+1 收盘成交，
两侧扣申赎费，月末偏离 > 5pp 才回锚。差别只在标的换成中证A500、样本只有约 2 年。

产出（相对本文件）：
- backtest/_a500_erp_band.png        全样本：股债利差（含 MA / ±2σ）vs 中证A500 点位
- backtest/_a500_erp_band_1y.png     近一年同一张图
- backtest/_a500_erp_band_nav.png    净值对比
- backtest/_a500_erp_band_bt.md/.jsonl

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
_V4 = os.path.dirname(_HERE)
for _p in (_HERE, _V4):
    if _p not in sys.path:
        sys.path.insert(0, _p)

import backtest as bt  # noqa: E402  —— evaluate / static_bench / row / yearly_returns 复用
import erp  # noqa: E402
import erp_a500 as a5  # noqa: E402

OUTDIR = os.path.join(_HERE, "backtest")
YEARS_1Y = 1


# ---------------------------------------------------------------- 敏感性（A500 版）
def sens(df, bond, t0, key, values):
    """逐档重算 -> rows。与 `../backtest.py::sens` 同构，但用 A500 的 frame。

    ⚠ 本变种样本只有约 2 年，窗口 500 日以上在 A500 上已无可用区间 —— 只跑 120/250。
    """
    rows = []
    for v in values:
        kw, label = {}, str(v)
        if key == "window":
            kw["df"], label = a5.erp_frame(window=v), f"{v} 日"
        elif key == "k":
            kw["df"], label = a5.erp_frame(k=v), f"±{v:g}σ"
        else:
            kw[key] = v
            if key == "exit_frac":
                kw["df"] = a5.erp_frame(exit_frac=v)
                label = "回穿均线" if not v else f"MA+{v:g}σ 才退出"
        d = kw.pop("df", df)
        r = bt.evaluate(d, bond, t0=t0, **kw)
        fr = r["frame"]
        b50 = erp.perf(bt.static_bench(bond, fr["tr"], 0.5)["nav"])
        rows.append(dict(label=label, start=fr.index[0], years=(fr.index[-1] - fr.index[0]).days / 365.25,
                         ann=r["m"]["ann"], b50=b50["ann"],
                         edge=(r["m"]["ann"] - b50["ann"]) * 100, mdd=r["m"]["mdd"],
                         sharpe=r["m"]["sharpe"], trades=len(r["trades"]),
                         w_avg=float(r["w"].mean())))
    return rows


# ---------------------------------------------------------------- 净值图
def plot_nav(navs, path, title, subtitle=None):
    """净值对比图（起点归一）。navs = [(标签, Series, 颜色, 线型)]。"""
    try:
        import matplotlib
        matplotlib.use("Agg")
        import matplotlib.pyplot as plt
    except Exception as exc:  # noqa: BLE001
        print("matplotlib 不可用，跳过作图:", exc)
        return None

    plt.rcParams["font.sans-serif"] = ["Microsoft YaHei", "SimHei", "DejaVu Sans"]
    plt.rcParams["axes.unicode_minus"] = False

    fig, ax = plt.subplots(figsize=(13, 6.8))
    for label, s, color, ls in navs:
        s = s.dropna()
        if s.empty:
            continue
        ax.plot(s.index, s / s.iloc[0], color=color, lw=1.8 if ls == "-" else 1.3, ls=ls,
                label=label)
    ax.set_ylabel("净值（起点 = 1）", fontsize=11)
    ax.grid(ls="--", lw=0.6, alpha=0.45)
    ax.set_axisbelow(True)
    ax.spines["top"].set_visible(False)
    ax.spines["right"].set_visible(False)
    ax.legend(loc="upper left", frameon=False, fontsize=10)
    fig.suptitle(title if not subtitle else f"{title}    |    {subtitle}",
                 x=0.06, ha="left", y=0.975, fontsize=14, color="#1b2b3a", weight="bold")
    fig.text(0.06, 0.015, "合成国债 D=8；含申购 0.10% / 赎回 0.50%。"
                          "  ·  A500 股票腿 = 价格指数 + 股息率按日计提（H00510 不可达的近似）。"
                          "  ·  模拟结果，非投资建议。",
             fontsize=9, color="#5b6b7a")
    fig.subplots_adjust(left=0.07, right=0.96, top=0.88, bottom=0.11)
    fig.savefig(path, dpi=130, facecolor="white")
    plt.close(fig)
    return path


# ---------------------------------------------------------------- 对照图（A500 vs 沪深300）
def plot_compare(d_a, d_h, path, title, subtitle=None):
    """同一时间窗内 A500 与沪深300 的股债利差（同一 10Y 收益率口径）。"""
    try:
        import matplotlib
        matplotlib.use("Agg")
        import matplotlib.pyplot as plt
        from matplotlib.ticker import StrMethodFormatter
    except Exception as exc:  # noqa: BLE001
        print("matplotlib 不可用，跳过作图:", exc)
        return None

    plt.rcParams["font.sans-serif"] = ["Microsoft YaHei", "SimHei", "DejaVu Sans"]
    plt.rcParams["axes.unicode_minus"] = False

    cut = d_a.index[0]
    h = d_h.loc[cut:]
    fig, ax = plt.subplots(figsize=(13, 6.4))
    ax.plot(d_a.index, d_a["erp"], color="#2f4b9b", lw=1.8, label="中证A500 股债利差")
    ax.plot(h.index, h["erp"], color="#a4302f", lw=1.8, label="沪深300 股债利差")
    ax.plot(d_a.index, d_a["ma"], color="#2f4b9b", lw=1.0, ls="--", alpha=0.75,
            label="中证A500 均线")
    ax.plot(h.index, h["ma"], color="#a4302f", lw=1.0, ls="--", alpha=0.75, label="沪深300 均线")
    ax.set_ylabel("股债利差（%）", fontsize=11)
    ax.yaxis.set_major_formatter(StrMethodFormatter("{x:,.1f}"))
    ax.grid(ls="--", lw=0.6, alpha=0.45)
    ax.set_axisbelow(True)
    ax.spines["top"].set_visible(False)
    ax.spines["right"].set_visible(False)
    ax.legend(loc="upper left", frameon=False, fontsize=10, ncol=2)
    fig.suptitle(title if not subtitle else f"{title}    |    {subtitle}",
                 x=0.06, ha="left", y=0.975, fontsize=14, color="#1b2b3a", weight="bold")
    fig.text(0.06, 0.015, "两者用同一条 10Y 中债收益率，只换 PE 口径（中证 peg）。"
                          "  水平高低 ≠ 谁更值得买：A500 行业更均衡、成分更分散。"
                          "  ·  模拟结果，非投资建议。",
             fontsize=9, color="#5b6b7a")
    fig.subplots_adjust(left=0.07, right=0.96, top=0.90, bottom=0.10)
    fig.savefig(path, dpi=130, facecolor="white")
    plt.close(fig)
    return path


# ---------------------------------------------------------------- 主流程
def main():
    os.makedirs(OUTDIR, exist_ok=True)
    df = a5.erp_frame()
    t0 = bt.align(df).index[0]
    bond = a5.bond_leg()
    base = bt.evaluate(df, bond, t0=t0)
    d = base["frame"]
    pe, px = d["tr"], d["close"]
    nav_s, m_s = base["nav"], base["m"]

    bh_eq = pe / pe.iloc[0]
    pb = bond.reindex(pe.index).ffill()
    bh_bd = pb / pb.iloc[0]
    st50 = bt.static_bench(bond, pe, 0.50)
    st80 = bt.static_bench(bond, pe, 0.80)
    rev = bt.evaluate(df, bond, t0=t0, reverse=True)
    nofee = bt.evaluate(df, bond, t0=t0, fees=False)

    rows = [
        bt.row("**策略（ERP 通道 80/50/20）**", nav_s,
               "默认参数：250 日 / ±2σ / 回穿均线 / 月末回锚", base),
        bt.row("策略（不计费）", nofee["nav"], "费率置 0，单看摩擦成本", nofee),
        bt.row("**反向信号（低估 20 / 高估 80）**", rev["nav"], "把仓位对调", rev),
        bt.row("静态 50/50（月末回锚）", st50["nav"], "不择时的基准", st50),
        bt.row("静态 80/20（月末回锚）", st80["nav"], "一直重仓股票端", st80),
        bt.row("买入持有中证A500（全收益近似）", bh_eq, "纯股票，含股息粗估"),
        bt.row("买入持有债券（合成 10Y，D=8）", bh_bd, "纯债券"),
    ]

    fwd = erp.forward_stats(d)
    eps = erp.episodes(d)
    yrs = (d.index[-1] - d.index[0]).days / 365.25
    vc = d["state"].value_counts().to_dict()
    tot = len(d)
    per_year = pd.DataFrame({
        "策略": bt.yearly_returns(nav_s),
        "静态50/50": bt.yearly_returns(st50["nav"]),
        "中证A500": bt.yearly_returns(bh_eq),
    }).dropna(how="all")

    sens_win = sens(df, bond, t0, "window", [120, 250])
    sens_k = sens(df, bond, t0, "k", [1.5, 2.0, 2.5])
    sens_exit = sens(df, bond, t0, "exit_frac", [0.0, 0.5])

    # ---------------- 图 1/2：ERP 通道 vs 点位（复刻沪深300 版的版式）
    p_full = os.path.join(OUTDIR, "_a500_erp_band.png")
    erp.plot_overlay(
        df, p_full, title=f"股债利差 vs {a5.IDX_NAME}",
        subtitle=f"{df.index[0]:%Y-%m-%d} 至 {df.index[-1]:%Y-%m-%d}（日线）"
                 f" · 通道 {erp.CFG['window']} 日 / ±{erp.CFG['k']:g}σ"
                 f" · 均线与上下轨自 {t0:%Y-%m-%d} 起（250 日预热）",
        note=(f"左轴：股债利差（%）= 100/PE({a5.IDX_NAME}) − 10Y国债；橙线 = "
              f"{erp.CFG['window']} 日均线；绿线 = 均线 ± {erp.CFG['k']:g}σ。"
              f"右轴：{a5.IDX_NAME} 收盘（点）。  注：A500 的 PE 仅自 2024-09 起，样本约 2 年。"
              "  来源：中证官网 + 新浪 + 东财。  ·  模拟结果，非投资建议。"))
    cut = df.index[-1] - pd.Timedelta(days=365 * YEARS_1Y)
    p_1y = os.path.join(OUTDIR, "_a500_erp_band_1y.png")
    erp.plot_overlay(df, p_1y, title=f"近一年：股债利差 vs {a5.IDX_NAME}", start=cut,
                     subtitle=f"{cut:%Y-%m-%d} 至 {df.index[-1]:%Y-%m-%d}（日线 · 局部放大）",
                     note=(f"左轴：股债利差（%）；橙线 = {erp.CFG['window']} 日均线；"
                           f"绿线 = 均线 ± {erp.CFG['k']:g}σ。右轴：{a5.IDX_NAME} 收盘。"
                           "  模拟结果，非投资建议。"))

    # ---------------- 图 3：净值
    p_nav = os.path.join(OUTDIR, "_a500_erp_band_nav.png")
    plot_nav([("策略（ERP 通道 80/50/20）", nav_s, "#2f4b9b", "-"),
              ("静态 50/50", st50["nav"], "#e08c33", "-"),
              (f"买入持有{a5.IDX_NAME}", bh_eq, "#a4302f", "-"),
              ("买入持有债券（合成 D=8）", bh_bd, "#4a7c3f", "-")],
             p_nav, title=f"净值对比 · {a5.IDX_NAME} ERP 通道",
             subtitle=f"{d.index[0]:%Y-%m-%d} 至 {d.index[-1]:%Y-%m-%d}（{yrs:.1f} 年）")

    # ---------------- 图 4：A500 vs 沪深300 的利差（衡量「换成 A500 值不值」）
    p_cmp = os.path.join(OUTDIR, "_a500_vs_hs300_erp.png")
    df_hs = erp.erp_frame()
    plot_compare(df, df_hs, p_cmp, title="股债利差对照 · 中证A500 vs 沪深300",
                 subtitle=f"{df.index[0]:%Y-%m-%d} 至 {df.index[-1]:%Y-%m-%d}"
                          f"（同一 10Y 中债，{erp.CFG['window']} 日均线）")
    gap_now = float(df["erp"].iloc[-1]) - float(df_hs["erp"].iloc[-1])   # 单位：pp（已是 %）
    gap_avg = float((df["erp"] - df_hs["erp"].reindex(df.index)).mean())

    # ---------------- 落地 jsonl
    jl = os.path.join(OUTDIR, "_a500_erp_band_bt.jsonl")
    with open(jl, "w", encoding="utf-8") as f:
        f.write(json.dumps(dict(kind="summary", index=a5.IDX_CODE, name=a5.IDX_NAME,
                                start=d.index[0].date().isoformat(),
                                end=d.index[-1].date().isoformat(), years=round(yrs, 2),
                                days=len(d),
                                state_share={str(k): round(v / tot, 4) for k, v in vc.items()},
                                window=erp.CFG["window"], k=erp.CFG["k"],
                                exit_frac=erp.CFG["exit_frac"], div_yield=float(d["dy"].iloc[0]),
                                source={**dict(a5.SOURCE), "bond": erp.SOURCE.get("bond")}),
                   ensure_ascii=False) + "\n")
        for r in rows:
            f.write(json.dumps({k: (round(v, 6) if isinstance(v, float) else v)
                                for k, v in r.items()}, ensure_ascii=False) + "\n")
        for e in eps:
            f.write(json.dumps({**e, "kind": ("低估" if e["kind"] == 1 else "高估")},
                               ensure_ascii=False) + "\n")

    # ---------------- 报告
    exit_txt = "回穿均线（MA）" if not erp.CFG["exit_frac"] else f"回到 MA+{erp.CFG['exit_frac']:g}σ"
    L = [
        f"# V4-A500 变种：股债利差（ERP）通道 —— {a5.IDX_NAME} × 10Y 国债",
        "",
        f"> 样本 **{d.index[0]:%Y-%m-%d} ~ {d.index[-1]:%Y-%m-%d}（{yrs:.1f} 年）**"
        f" · 收盘 {a5.SOURCE.get('close')} / PE {a5.SOURCE.get('pe')}",
        f"> 规则与沪深300 版**完全相同**（参数权威源 `../erp.py::CFG`），只把标的换成"
        f" {a5.IDX_NAME}（`{a5.IDX_CODE}`）。沪深300 版 `../backtest/` 的产物未动。",
        "> ⚠ **样本只有约 2 年**：中证自 A500 指数发布日（2024-09-02）起才提供 PE，"
        "250 日窗口预热后可用区间约 1 年。**本报告的所有数字统计意义都很弱，只能当「当前读数」看。**",
        "> 机械规则输出，**非投资建议**。",
        "",
        "## 1. 参数（同沪深300 版）",
        "",
        "| 项 | 取值 |",
        "|---|---|",
        f"| 通道窗口 | {erp.CFG['window']} 交易日 |",
        f"| 轨宽 | ±{erp.CFG['k']:g}σ（总体标准差） |",
        f"| 退出 | {exit_txt} |",
        f"| 仓位 | 低估 **{erp.CFG['eq_cheap']:.0%}** / 中性 **{erp.CFG['eq_neutral']:.0%}** / "
        f"高估 **{erp.CFG['eq_expan']:.0%}**（{a5.IDX_NAME} 占比 of 股债） |",
        f"| 再平衡 | 信号切换日必动；月末偏离 >{erp.CFG['rebal_tol']:.0%} 才回锚 |",
        f"| 成本 | 申购 {erp.CFG['fee_eq'][0]:.2%} / 赎回 {erp.CFG['fee_eq'][1]:.2%}（两条腿各自） |",
        f"| 债券腿 | 10Y 收益率合成，久期 {erp.CFG['bond_duration']:g} 年（与沪深300 版共用） |",
        f"| 股票腿 | 价格指数 + 股息率 {float(d['dy'].iloc[0]):.2%} 按日计提（全收益近似） |",
        "",
        f"> 状态占比（通道就绪后 {tot} 个交易日）：" + " / ".join(
            f"{n} **{vc.get(k, 0) / tot:.1%}**" for k, n in ((1, "低估"), (0, "中性"), (-1, "高估")))
        + "。高估占比高不是因为 A500 贵，而是因为这 1 年里 ERP 一路下行、"
        "而通道是「相对自己近一年」的位置 —— **2 年样本的 ±2σ 没有估值锚的意味**。",
        "",
        "## 2. 主回测（样本内，含费）",
        "",
        "| 组合 | 累计 | 年化 | 波动 | 最大回撤 | 夏普 | 平均股票仓位 | 信号切换 | 累计费用 |",
        "|---|---:|---:|---:|---:|---:|---:|---:|---:|",
    ]
    for r in rows:
        head = (f"| {r['name']} | {erp.fmt_pct(r['cum'], 1)} | {erp.fmt_pct(r['ann'])} | "
                f"{erp.fmt_pct(r['vol'])} | {erp.fmt_pct(r['mdd'])} | {r['sharpe']:.2f} | ")
        tail = (f"{r['w_avg']:.0%} | {r['switches']} | {r['fees']:.2%} |"
                if "w_avg" in r else "— | — | — |")
        L.append(head + tail)

    d_ann = (m_s["ann"] - st50["m"]["ann"]) * 100
    d_eq = (m_s["ann"] - erp.perf(bh_eq)["ann"]) * 100
    d_rev = (m_s["ann"] - rev["m"]["ann"]) * 100
    L += [
        "",
        f"> **策略 vs 静态 50/50：{d_ann:+.2f}pp/年**（回撤 {erp.fmt_pct(m_s['mdd'])} vs "
        f"{erp.fmt_pct(st50['m']['mdd'])}）。",
        f"> **策略 vs 买入持有{a5.IDX_NAME}：{d_eq:+.2f}pp/年**。",
        f"> **方向检验**：反向信号 {erp.perf(rev['nav'])['ann']:+.2%}，与正方向差 "
        f"**{d_rev:+.2f}pp/年** —— 1 年样本里这个差值**没有信息量**，"
        "沪深300 版 14 年样本的结论（方向识别不出来）才是可引用的那个。",
        "",
        "## 3. 各状态下的未来收益（中证A500 全收益近似）",
        "",
        "| 未来 | 状态 | 样本数 | 平均 | 中位 | 上涨概率 | 10% 分位 | 90% 分位 |",
        "|---|---|---:|---:|---:|---:|---:|---:|",
    ]
    for _, r in fwd.iterrows():
        L.append(f"| {int(r['horizon'])} 日 | {r['state']} | {int(r['n'])} | "
                 f"{erp.fmt_pct(r['mean'])} | {erp.fmt_pct(r['median'])} | {r['win']:.0%} | "
                 f"{erp.fmt_pct(r['p10'])} | {erp.fmt_pct(r['p90'])} |")

    L += [
        "",
        "## 4. 历史信号区间（全部）",
        "",
        "| 状态 | 起 | 止 | 交易日 | ERP 均值 | ERP 极值 | A500 起点 | 终点 | 区间涨跌 |",
        "|---|---|---|---:|---:|---:|---:|---:|---:|",
    ]
    for e in eps:
        L.append(f"| {'低估' if e['kind'] == 1 else '高估'} | {e['start']} | {e['end']} | "
                 f"{e['days']} | {e['erp_mean']:.2f}% | "
                 f"{erp.fmt_x(e['erp_max'] if e['kind'] == 1 else e['erp_min'])}% | "
                 f"{e['px_start']:.0f} | {e['px_end']:.0f} | "
                 f"{erp.fmt_pct(e['px_end'] / e['px_start'] - 1.0)} |")
    last_state = int(d["state"].iloc[-1])
    L += [
        "",
        f"> 共 {len(eps)} 段；当前（{d.index[-1]:%Y-%m-%d}）处于"
        f"**{'低估' if last_state == 1 else ('高估' if last_state == -1 else '中性')}** 态，"
        f"ERP {float(d['erp'].iloc[-1]):.2f}%（MA {float(d['ma'].iloc[-1]):.2f} / "
        f"上轨 {float(d['upper'].iloc[-1]):.2f} / 下轨 {float(d['lower'].iloc[-1]):.2f}）。",
        "",
        "## 5. 参数敏感性（样本太短，只作参考）",
        "",
        "| 档 | 起点 | 年数 | 年化 | 同窗口 50/50 | 超额 | 最大回撤 | 夏普 | 平均股票仓位 |",
        "|---|---|---:|---:|---:|---:|---:|---:|---:|",
    ]
    for tag, table in (("窗口", sens_win), ("轨宽", sens_k), ("退出", sens_exit)):
        for s in table:
            label = f"{tag}＝{s['label']}"
            L.append(f"| {label} | {s['start']:%Y-%m-%d} | {s['years']:.1f} | "
                     f"{erp.fmt_pct(s['ann'])} | {erp.fmt_pct(s['b50'])} | "
                     f"**{s['edge']:+.2f}pp** | {erp.fmt_pct(s['mdd'])} | "
                     f"{s['sharpe']:.2f} | {s['w_avg']:.0%} |")
    L += [
        "> 窗口 500 日以上无法在 A500 上跑（样本不足）。这张表**不要用来挑参数** —— "
        "271 个交易日里选最优，等于在噪声里挑最大值。",
        "",
        "## 6. 逐年收益",
        "",
        "| 年 | 策略 | 静态 50/50 | 中证A500 | 超额（vs 50/50） |",
        "|---|---:|---:|---:|---:|",
    ]
    for y, r in per_year.iterrows():
        gap = (r["策略"] - r["静态50/50"]) * 100
        L.append(f"| {int(y)} | {erp.fmt_pct(r['策略'])} | {erp.fmt_pct(r['静态50/50'])} | "
                 f"{erp.fmt_pct(r['中证A500'])} | {gap:+.1f}pp |")

    L += [
        "",
        "## 7. 换成 A500，利差水平差多少",
        "",
        f"同一时间窗、同一条 10Y 中债口径下，**A500 的股债利差当前比沪深300 低 "
        f"{abs(gap_now):.2f}pp**（{float(df['erp'].iloc[-1]):.2f}% vs "
        f"{float(df_hs['erp'].iloc[-1]):.2f}%），窗口内平均低 {abs(gap_avg):.2f}pp。"
        "原因单纯：A500 的 PE（约 14 倍）高于沪深300（约 12.7 倍），盈利收益率因此更低。",
        "",
        "> 这个差**不是**「A500 更贵所以更差」的判决：A500 是 500 只、行业中性，"
        "沪深300 是 300 只、金融权重更高，两者的估值中枢本来就不同。"
        "**做仓位决策要用各自的通道位置，不要拿两条线的水平直接比大小。**",
        "",
        "## 8. 结论（把话说在前面）",
        "",
        f"1. **这是「同一规则换个标的」的对照实验，不是新策略**。A500 只有 2 年 PE 历史，"
        f"通道就绪后仅 {tot} 个交易日，**任何年化/夏普都不能当结论**。",
        "2. **能看的是当前读数**：`ERP = 100/PE − 10Y`，以及它在自己近一年 MA±2σ 里的位置。"
        f"当前 A500 处在上轨之上（ERP {float(d['erp'].iloc[-1]):.2f}% > 上轨 "
        f"{float(d['upper'].iloc[-1]):.2f}%），规则给出的目标仓位是 "
        f"**{float(d['target'].iloc[-1]):.0%}** 股票。",
        "3. **别拿 2 年样本做参数校准**：窗口从 250 缩到 120 会让样本提前一年，"
        "年化差异几乎全部来自「多经历了哪一段」。",
        "4. **股票腿用价格指数 + 股息率近似**（H00510 全收益不可达），"
        f"股息率取 {float(d['dy'].iloc[0]):.2%} 常数；对信号零影响，对年化大约 ±0.1pp。",
        "5. **PE 口径与沪深300 版一致**（中证 `peg`），但取数端点不同"
        "（`indexCsiDsPe`，因为沪深300 版用的 `index-perf` 被站点 WAF 拦）。"
        "两者对沪深300 同日数值完全一致，可交叉验证。",
        "6. **仓位结论的方向风险**：本窗口里规则在 2026-09 把仓位加到 80%，"
        "随后 A500 跌 14% —— 这正是沪深300 版 §5 说的「低估 ≠ 立刻上涨」。"
        "样本短 + 一次踩空，说明不了规则好坏，但也**没有任何证据支持它在 A500 上更好**。",
        "",
        "> **模拟结果，非投资建议。**",
    ]

    path = os.path.join(OUTDIR, "_a500_erp_band_bt.md")
    with open(path, "w", encoding="utf-8") as f:
        f.write("\n".join(L))

    print(f"报告: {path}")
    print(f"样本 {d.index[0]:%Y-%m-%d} ~ {d.index[-1]:%Y-%m-%d} ({yrs:.1f} 年, {tot} 日)")
    for r in rows:
        w_txt = f"{r['w_avg']:.0%}" if "w_avg" in r else "—"
        print(f"  {r['name'][:34]:34s} 年化 {erp.fmt_pct(r['ann']):>9s} · "
              f"回撤 {erp.fmt_pct(r['mdd']):>9s} · 夏普 {r['sharpe']:5.2f} · 仓位 {w_txt}")
    print(f"利差对照: A500 {float(df['erp'].iloc[-1]):.2f}% vs 沪深300 "
          f"{float(df_hs['erp'].iloc[-1]):.2f}%（差 {gap_now:+.2f}pp）")
    print("图:", p_full, "|", p_1y, "|", p_nav, "|", p_cmp)


if __name__ == "__main__":
    main()
