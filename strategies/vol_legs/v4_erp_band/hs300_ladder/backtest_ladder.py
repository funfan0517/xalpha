# -*- coding: utf-8 -*-
"""V4-L 阶梯加减仓 · 回测与作图（专属沪深300 · 单腿 0~100%）。

口径见 `ladder.py`：ERP 通道内位置 0~100% 刻度，75% 线建 20% → 上线 30% →
每多一日 +10pp 至满仓；下穿中线减半 → 跌破 25% 再减半 → 跌破下线清仓。
**不涉及债券**：未投部分 = 货币基金（1.40%）。

产出（相对本文件）：
- backtest/_ladder_rule.png        主图：ERP 四线 + 仓位阶梯（全样本）
- backtest/_ladder_rule_3y.png     同一张图的近三年
- backtest/_ladder_nav.png         净值对比
- backtest/_ladder_bt.md / .jsonl

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

import backtest as bt  # noqa: E402  —— yearly_returns / align / row 复用
import erp  # noqa: E402
import ladder as ld  # noqa: E402

OUTDIR = os.path.join(_HERE, "backtest")
YEARS_3Y = 3


# ---------------------------------------------------------------- 工具
def static(df, w, fees=True, rebal=True):
    """常仓 w（不择时）：`rebal=True` 时月末回锚，否则权重随涨跌漂移。"""
    tgt = pd.Series(float(w), index=df.index)
    return ld.run(df, target=tgt, fees=fees, month_end=rebal, tol=0.0)


def v4_hold(df):
    """对照：v4 原规则的 20/50/80 三档，当「仓位」用（把债券腿换成现金）。"""
    tgt = erp.target_weight(df["state"])
    return ld.run(df, target=tgt)


def evaluate(df, pos=None, fees=True, **kw):
    """按给定参数跑一遍 -> dict(nav, m, trades, w, target, events, pos)。"""
    pos = ld.position(df) if pos is None else pos
    tgt, events = ld.ladder_target(df, pos=pos, detail=True, **kw)
    r = ld.run(df, target=tgt, fees=fees)
    r["events"] = events
    r["pos"] = pos
    return r


def cycles(events, nav):
    """把动作切成「一次完整周期」：首个建仓 -> 清仓。返回 [(起, 止, 天数, 策略收益, 期间最高仓位)]。"""
    out, starts, peak = [], [], 0.0
    for e in events:
        if e["action"].startswith("清仓") and starts:
            d0 = starts[-1][0]
            seg = nav.loc[d0:e["date"]]
            out.append(dict(start=d0, end=e["date"], days=int(len(seg)),
                            ret=float(seg.iloc[-1] / seg.iloc[0] - 1.0), peak=peak))
            starts.pop()
            peak = 0.0
            continue
        if e["before"] < 1e-9 and e["after"] > 1e-9:      # 空仓后的第一笔
            starts.append((e["date"], e["action"]))
        if starts:
            peak = max(peak, e["after"])
    if starts:
        d0 = starts[-1][0]
        seg = nav.loc[d0:]
        out.append(dict(start=d0, end=seg.index[-1], days=int(len(seg)),
                        ret=float(seg.iloc[-1] / seg.iloc[0] - 1.0), peak=peak, open_=True))
    return out


# ---------------------------------------------------------------- 图：规则 + 仓位
def plot_rule(df, tgt, path, title, subtitle=None, start=None, end=None):
    """上：股债利差 + 中线/75%线/上线 + 25%线/下线；下：目标仓位阶梯。"""
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

    d, t = df.loc[start:end], tgt.loc[start:end]
    if d.empty:
        return None
    fig, (ax, axw) = plt.subplots(2, 1, figsize=(13, 8.8), sharex=True,
                                  gridspec_kw={"height_ratios": [2.3, 1.0]})
    ax2 = ax.twinx()

    ax.plot(d.index, d["erp"], color="#2f4b9b", lw=1.5, label="股债利差")
    ax.plot(d.index, d["ma"], color="#e08c33", lw=1.9, label="中线（MA）")
    ax.plot(d.index, d["upper"], color="#4a7c3f", lw=1.6, label="上线（MA+2σ）")
    ax.plot(d.index, d["lower"], color="#4a7c3f", lw=1.1, ls="--", label="下线（MA−2σ）")
    k = float(ld.cfg("channel_k"))
    e1, lo = float(ld.cfg("line_entry1")), float(ld.cfg("line_low"))
    amp = d["upper"] - d["ma"]           # = k×σ
    u75 = d["ma"] + amp * (e1 - 0.5) * 2.0
    l25 = d["ma"] - amp * (0.5 - lo) * 2.0
    ax.plot(d.index, u75, color="#7a5cae", lw=1.1, ls=":",
            label=f"75% 线（MA+{(e1 - 0.5) * 2 * k:.1f}σ）")
    ax.plot(d.index, l25, color="#7a5cae", lw=1.1, ls="-.",
            label=f"25% 线（MA−{(0.5 - lo) * 2 * k:.1f}σ）")
    ax2.plot(d.index, d["close"], color="#a4302f", lw=1.2, alpha=0.85)

    ax.set_ylabel("股债利差（%）", color="#2f4b9b", fontsize=11)
    ax.tick_params(axis="y", colors="#2f4b9b")
    ax.yaxis.set_major_formatter(StrMethodFormatter("{x:,.1f}"))
    ax2.set_ylabel("沪深300（点）", color="#a4302f", fontsize=10)
    ax2.tick_params(axis="y", colors="#a4302f", labelsize=9)
    ax.grid(axis="y", ls="--", lw=0.6, alpha=0.45)
    ax.set_axisbelow(True)
    ax.spines["top"].set_visible(False)
    ax2.spines["top"].set_visible(False)

    h1, l1 = ax.get_legend_handles_labels()
    ax.legend(h1, l1, loc="lower left", bbox_to_anchor=(0.0, 1.004, 1.0, 0.09),
              ncol=6, mode="expand", frameon=False, fontsize=9.2,
              handlelength=1.6, columnspacing=1.0)

    axw.fill_between(t.index, 0.0, t.to_numpy() * 100.0, step="mid",
                     color="#2f4b9b", alpha=0.18)
    axw.step(t.index, t.to_numpy() * 100.0, where="mid", color="#2f4b9b", lw=1.4,
             label="目标仓位")
    axw.set_ylabel("目标仓位（%）", color="#2f4b9b", fontsize=11)
    axw.set_ylim(-3, 105)
    axw.set_yticks([0, 20, 30, 50, 100])
    axw.grid(axis="y", ls="--", lw=0.6, alpha=0.45)
    axw.set_axisbelow(True)
    for s in ("top", "right"):
        axw.spines[s].set_visible(False)
    axw.tick_params(axis="y", colors="#2f4b9b")

    head = title if not subtitle else f"{title}    |    {subtitle}"
    fig.suptitle(head, x=0.06, ha="left", y=0.985, fontsize=14, color="#1b2b3a",
                 weight="bold")
    fig.text(0.06, 0.026,
             "上：股债利差 = 100/PE(沪深300) − 10Y国债，红线 = 沪深300 点位（右轴）；"
             "下：目标仓位阶梯。\n"
             "仓位规则：上穿 75% 投 20% → 上穿上线 30% → 每多一日 +10pp 至 100%；"
             "下穿中线减半 → 跌破 25% 再减半 → 跌破下线清仓。  ·  模拟结果，非投资建议。",
             fontsize=8.8, color="#5b6b7a")
    fig.subplots_adjust(left=0.068, right=0.932, top=0.885, bottom=0.10, hspace=0.08)
    fig.savefig(path, dpi=130, facecolor="white")
    plt.close(fig)
    return path


def plot_nav(navs, path, title, subtitle=None, note=None):
    """净值对比（起点归一）。"""
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
    for label, s, color, ls, lw in navs:
        s = s.dropna()
        if not s.empty:
            ax.plot(s.index, s / s.iloc[0], color=color, lw=lw, ls=ls, label=label)
    ax.set_ylabel("净值（起点 = 1）", fontsize=11)
    ax.grid(ls="--", lw=0.6, alpha=0.45)
    ax.set_axisbelow(True)
    ax.spines["top"].set_visible(False)
    ax.spines["right"].set_visible(False)
    ax.legend(loc="upper left", frameon=False, fontsize=10)
    fig.suptitle(title if not subtitle else f"{title}    |    {subtitle}",
                 x=0.06, ha="left", y=0.975, fontsize=14, color="#1b2b3a", weight="bold")
    fig.text(0.06, 0.015,
             note or ("单腿：股票 = 沪深300 全收益（含分红再投），未投部分 = 货币基金 1.40%；"
                      "申购 0.10% / 赎回 0.50%。  ·  模拟结果，非投资建议。"),
             fontsize=9, color="#5b6b7a")
    fig.subplots_adjust(left=0.07, right=0.96, top=0.88, bottom=0.11)
    fig.savefig(path, dpi=130, facecolor="white")
    plt.close(fig)
    return path


# ---------------------------------------------------------------- 敏感性
def sens(df, key, values, base_kw=None, t0=None):
    """逐档重算 -> rows。每档都用自己的 `ready` 区间重算 50% 常仓做对照。"""
    base_kw = dict(base_kw or {})
    rows = []
    for v in values:
        label = v if isinstance(v, str) else str(v)
        kw = dict(base_kw)
        if key == "window":
            kw["df"] = ld.frame(window=v)
            label = f"{v} 日"
        elif key == "k":
            kw["df"] = ld.frame(k=v)
            label = f"±{v:g}σ"
        elif key == "pos_mode":
            kw["pos_mode"] = v
            label = "通道位置（默认）" if v == "band" else f"ERP 分位（{ld.cfg('rank_window')} 日）"
        else:
            kw[key] = v
            label = f"{label}"
        d = kw.pop("df", df)
        sub = d[d["ready"]]
        if sub.empty:
            continue
        start = t0 if t0 is not None else sub.index[0]
        sub = sub.loc[start:]
        r = evaluate(d.loc[sub.index[0]:], **kw)
        fr = r["target"].loc[sub.index[0]:]
        nav = r["nav"].loc[fr.index[0]:]
        b50 = static(d.loc[fr.index[0]:], 0.5)["m"]
        bh = d.loc[fr.index[0]:, "tr"]
        rows.append(dict(label=label, start=fr.index[0],
                         years=(fr.index[-1] - fr.index[0]).days / 365.25,
                         ann=erp.perf(nav)["ann"], b50=b50["ann"],
                         edge=(erp.perf(nav)["ann"] - b50["ann"]) * 100,
                         bh=(bh.iloc[-1] / bh.iloc[0]) ** (252 / len(bh)) - 1,
                         mdd=erp.perf(nav)["mdd"], sharpe=erp.perf(nav)["sharpe"],
                         w_avg=float(r["w"].loc[nav.index].mean()),
                         trades=len(r["trades"]),
                         peak=float(r["target"].loc[nav.index].max())))
    return rows


# ---------------------------------------------------------------- 主流程
def main():
    os.makedirs(OUTDIR, exist_ok=True)
    df = ld.frame()
    ready = df[df["ready"]]
    t0 = ready.index[0]

    base = evaluate(df)
    d = df.loc[t0:]
    nav_s = base["nav"].loc[t0:]
    m_s = erp.perf(nav_s)
    tgt = base["target"].loc[t0:]

    # 基准（同一区间: 通道就绪起）
    bh_eq = d["tr"] / d["tr"].iloc[0]
    st50 = static(d, 0.50)
    st80 = static(d, 0.80)
    v4h = v4_hold(d)
    nofee = evaluate(df, fees=False)
    nav_nf = nofee["nav"].loc[t0:]
    # 两个可选开关（默认关）：保留底仓 / 减仓后允许回补
    base30 = evaluate(df, base_weight=0.30)
    refill = evaluate(df, refill_after_reduce=True)

    rows = [
        bt.row("**阶梯加减仓（本规则）**", nav_s,
               "75% 投 20% → 上线 30% → 每多一日 +10pp 至满仓；中线/25%/下线逐级减", base),
        bt.row("同上（不计费）", nav_nf, "费率置 0，单看摩擦成本", nofee),
        bt.row("改版：底仓 30%（不清到 0）", base30["nav"],
               "减仓下限 30%，其余不动", base30),
        bt.row("改版：减仓后允许回补", refill["nav"],
               "refill_after_reduce=True，其余不动", refill),
        bt.row("对照：v4 原规则 20/50/80 三档", v4h["nav"],
               "同一标的、同一现金腿，只换仓位规则", v4h),
        bt.row("常仓 50%（月末回锚）", st50["nav"], "不择时", st50),
        bt.row("常仓 80%（月末回锚）", st80["nav"], "不择时", st80),
        bt.row("买入持有沪深300（全收益）", bh_eq, "满仓不动"),
    ]

    eps_cycles = cycles(base["events"], base["nav"])
    yrs = (d.index[-1] - d.index[0]).days / 365.25
    per_year = pd.DataFrame({
        "阶梯": bt.yearly_returns(nav_s),
        "常仓50%": bt.yearly_returns(st50["nav"]),
        "沪深300": bt.yearly_returns(bh_eq),
    }).dropna(how="all")

    # 分半
    mid = d.index[len(d) // 2]
    half = []
    for tag, seg in (("前半段", d.loc[:mid]), ("后半段", d.loc[mid:])):
        if len(seg) < 300:
            continue
        r = evaluate(seg)
        nn = r["nav"]
        b = static(seg, 0.5)["m"]
        half.append(dict(tag=tag, start=seg.index[0], end=seg.index[-1], ann=erp.perf(nn)["ann"],
                         mdd=erp.perf(nn)["mdd"], sharpe=erp.perf(nn)["sharpe"],
                         b50=b["ann"], b50_mdd=b["mdd"],
                         bh=(seg["tr"].iloc[-1] / seg["tr"].iloc[0])
                            ** (252 / len(seg)) - 1,
                         w=float(r["w"].mean())))

    sens_win = sens(df, "window", [120, 250, 500, 750], t0=t0)
    sens_k = sens(df, "k", [1.5, 2.0, 2.5, 3.0], t0=t0)
    sens_step = sens(df, "step", [0.05, 0.10, 0.20, 0.25], t0=t0)
    sens_e1 = sens(df, "line_entry1", [0.60, 0.75, 0.85], t0=t0)
    sens_base = sens(df, "base_weight", [0.0, 0.20, 0.30], t0=t0)

    # ---------------- 图
    p_full = os.path.join(OUTDIR, "_ladder_rule.png")
    plot_rule(df, base["target"], p_full,
              title="沪深300 · 股债利差通道 → 目标仓位阶梯",
              subtitle=f"通道 {ld.cfg('channel_window')} 日 / ±{ld.cfg('channel_k'):g}σ · "
                       f"就绪自 {t0:%Y-%m-%d} 至 {df.index[-1]:%Y-%m-%d}")
    cut = df.index[-1] - pd.Timedelta(days=365 * YEARS_3Y)
    p_3y = os.path.join(OUTDIR, "_ladder_rule_3y.png")
    plot_rule(df, base["target"], p_3y, start=cut,
              title="近三年 · 股债利差通道 → 目标仓位阶梯",
              subtitle=f"{cut:%Y-%m-%d} 至 {df.index[-1]:%Y-%m-%d}（局部放大）")
    p_nav = os.path.join(OUTDIR, "_ladder_nav.png")
    plot_nav([("阶梯加减仓", nav_s, "#2f4b9b", "-", 2.0),
              ("v4 原规则 20/50/80", v4h["nav"], "#7a5cae", "-", 1.3),
              ("常仓 50%", st50["nav"], "#e08c33", "--", 1.3),
              ("买入持有沪深300", bh_eq, "#a4302f", "-", 1.3)],
             p_nav, title="净值对比 · 阶梯加减仓 vs 常仓 vs 买入持有",
             subtitle=f"{d.index[0]:%Y-%m-%d} 至 {d.index[-1]:%Y-%m-%d}（{yrs:.1f} 年，含费）")

    # ---------------- 落地
    jl = os.path.join(OUTDIR, "_ladder_bt.jsonl")
    with open(jl, "w", encoding="utf-8") as f:
        f.write(json.dumps(dict(kind="summary", start=d.index[0].date().isoformat(),
                                end=d.index[-1].date().isoformat(), years=round(yrs, 2),
                                days=len(d), params={k: v for k, v in ld.L_CFG.items()},
                                state_share={
                                    "空仓": round(float((tgt < 1e-9).mean()), 4),
                                    "满仓": round(float((tgt > 1 - 1e-9).mean()), 4),
                                    "平均仓位": round(float(tgt.mean()), 4)},
                                cycles=len(eps_cycles),
                                source=dict(erp.SOURCE)), ensure_ascii=False) + "\n")
        for r in rows:
            f.write(json.dumps({k: (round(v, 6) if isinstance(v, float) else v)
                                for k, v in r.items()}, ensure_ascii=False) + "\n")
        for e in base["events"]:
            f.write(json.dumps({"kind": "action", **{k: (v.isoformat() if hasattr(v, "isoformat")
                                                         else v) for k, v in e.items()}},
                               ensure_ascii=False) + "\n")

    # ---------------- 报告
    acts = base["events"]
    n_add = sum(1 for e in acts if e["action"] in ("加仓", "建仓满档"))
    n_half = sum(1 for e in acts if "减半" in e["action"])
    n_exit = sum(1 for e in acts if e["action"] == "清仓")
    d_ann = (m_s["ann"] - st50["m"]["ann"]) * 100
    d_bh = (m_s["ann"] - erp.perf(bh_eq)["ann"]) * 100
    d_v4 = (m_s["ann"] - erp.perf(v4h["nav"])["ann"]) * 100

    L = [
        "# V4-L · 沪深300 阶梯加减仓（专属沪深300 · 单腿 0~100%）",
        "",
        f"> 样本 **{d.index[0]:%Y-%m-%d} ~ {d.index[-1]:%Y-%m-%d}（{yrs:.1f} 年）**"
        f" · 股票腿 = 沪深300 全收益（H00300）· 未投部分 = 货币基金 "
        f"{ld.cfg('cash_annual'):.2%} · 申购 {ld.cfg('fee_buy'):.2%} / 赎回 "
        f"{ld.cfg('fee_sell'):.2%}",
        "> 与 v4 的区别：**不做股债配比**，只回答「现在拿多少股票」。债基的份额与收益不进模型。",
        "> 机械规则输出，**非投资建议**。",
        "",
        "## 1. 规则卡",
        "",
        "| # | 触发 | 动作 |",
        "|---|---|---|",
        f"| 1 | ERP **上穿 75% 线**（通道内位置，k=2 时 = MA+1σ） | 投 "
        f"**{ld.cfg('w_entry1'):.0%}** |",
        f"| 2 | ERP **上穿上线**（MA+{ld.cfg('channel_k'):g}σ） | 提到 "
        f"**{ld.cfg('w_entry2'):.0%}** |",
        f"| 3 | 持续处在上线之上，**每多 1 个交易日 +{ld.cfg('step'):.0%}** | 到 "
        f"**{ld.cfg('w_max'):.0%}** 后持仓等待 |",
        f"| 4 | **第一次下穿中线**（MA） | 仓位 **减半** |",
        f"| 5 | **第一次跌破 25% 线**（MA−1σ） | 再 **减半** |",
        f"| 6 | **第一次跌破下线**（MA−{ld.cfg('channel_k'):g}σ） | **清仓**，本轮结束 |",
        "",
        f"> 位置刻度：`pos = (ERP − 下线)/(上线 − 下线)`，0=下线 / 0.5=中线 / 1=上线；"
        f"通道 **{ld.cfg('channel_window')} 日 / ±{ld.cfg('channel_k'):g}σ**"
        f"（窗口与 v4 的 250 日不同，见 §5.1 的敏感性 —— 250 日在这条规则上明显太短）。",
        f"> 成交：T 日收盘算 → **T+1 收盘成交**；加仓只加不减（同日跨档取高者），"
        f"减仓各触发一次且减仓后不再回补（清仓后重置）。",
        "",
        f"> 平均仓位 **{tgt.mean():.0%}**，空仓 {float((tgt < 1e-9).mean()):.1%}、"
        f"满仓 {float((tgt > 1 - 1e-9).mean()):.1%}；"
        f"共 {n_add} 次加仓 / {n_half} 次减半 / {n_exit} 次清仓（{len(eps_cycles)} 个完整周期）。",
        "",
        "## 2. 主回测（同一区间、同一股票腿、含费）",
        "",
        "| 组合 | 累计 | 年化 | 波动 | 最大回撤 | 夏普 | Calmar | 平均股票仓位 | 交易次数 | 累计费用 |",
        "|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|",
    ]
    for r in rows:
        w_txt = f"{r['w_avg']:.0%}" if "w_avg" in r else "—"
        fee_txt = f"{r['fees']:.2%}" if "fees" in r else "—"
        L.append(f"| {r['name']} | {erp.fmt_pct(r['cum'], 1)} | {erp.fmt_pct(r['ann'])} | "
                 f"{erp.fmt_pct(r['vol'])} | {erp.fmt_pct(r['mdd'])} | {r['sharpe']:.2f} | "
                 f"{erp.fmt_x(r['calmar'])} | {w_txt} | "
                 f"{r.get('trades', '—')} | {fee_txt} |")

    L += [
        "",
        f"> **vs 常仓 50%：{d_ann:+.2f}pp/年**（回撤 {erp.fmt_pct(m_s['mdd'])} vs "
        f"{erp.fmt_pct(st50['m']['mdd'])}，夏普 {m_s['sharpe']:.2f} vs "
        f"{st50['m']['sharpe']:.2f}）。",
        f"> **vs 买入持有沪深300：{d_bh:+.2f}pp/年**"
        f"（回撤 {erp.fmt_pct(m_s['mdd'])} vs {erp.fmt_pct(erp.perf(bh_eq)['mdd'])}）。",
        f"> **vs v4 原规则（20/50/80 三档、同样现金腿）：{d_v4:+.2f}pp/年**。",
        "",
        "## 3. 完整买卖周期（首投 → 清仓）",
        "",
        "| # | 首投 | 清仓/至今 | 交易日 | 周期收益 | 期间最高仓位 |",
        "|---|---|---|---:|---:|---:|",
    ]
    for i, c in enumerate(eps_cycles, 1):
        L.append(f"| {i} | {c['start']:%Y-%m-%d} | {c['end']:%Y-%m-%d}"
                 f"{'（未清仓）' if c.get('open_') else ''} | {c['days']} | "
                 f"{erp.fmt_pct(c['ret'])} | {c['peak']:.0%} |")

    L += [
        "",
        "## 4. 全部动作（共 {} 次）".format(len(acts)),
        "",
        "| 日期 | 动作 | 仓位变化 | pos | ERP |",
        "|---|---|---|---:|---:|",
    ]
    for e in acts:
        L.append(f"| {e['date']:%Y-%m-%d} | {e['action']} | "
                 f"{e['before']:.0%} → {e['after']:.0%} | {e['pos']:.2f} | {e['erp']:.2f}% |")

    L += [
        "",
        "## 5. 参数敏感性",
        "",
        "> 每档都在**自己的 ready 区间**上重算「常仓 50%」做对照，看的是**超额**，不是年化绝对值。",
        "",
        "| 档 | 起点 | 年数 | 年化 | 常仓50% | 超额 | 沪深300 | 最大回撤 | 夏普 | 平均仓位 | 最高仓位 | 交易 |",
        "|---|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|",
    ]
    for tag, table in (("窗口", sens_win), ("轨宽", sens_k), ("每日加仓步长", sens_step),
                       ("首档线", sens_e1), ("底仓", sens_base)):
        for s in table:
            L.append(f"| {tag}＝{s['label']} | {s['start']:%Y-%m-%d} | {s['years']:.1f} | "
                     f"{erp.fmt_pct(s['ann'])} | {erp.fmt_pct(s['b50'])} | "
                     f"**{s['edge']:+.2f}pp** | {erp.fmt_pct(s['bh'])} | "
                     f"{erp.fmt_pct(s['mdd'])} | {s['sharpe']:.2f} | {s['w_avg']:.0%} | "
                     f"{s['peak']:.0%} | {s['trades']} |")
    L += [
        "> **窗口是强参数**：120/250 日 → 年化 2.7%/1.2%（夏普 0.12/−0.02），"
        "500/750 日 → 5.9%/4.8%（夏普 0.51/0.44）。方向有机制解释：250 日 MA 在趋势行情里滞后，"
        "ERP 会长期贴在轨道外侧，于是「刚建仓就减仓、减完仓又长期回不来」；"
        "窗口拉长后通道更接近「周期估值」，穿越次数减少。"
        "**但 500 与 750 的起点差 1 年，绝对年化不可直接比**，这张表不能当调参依据。",
        "> **轨宽也敏感，而且是「越宽越保守」**：±1.5σ 年化 5.42%、回撤只有 −13.74%、夏普 0.57；"
        "±2.5σ 年化 3.86%、回撤 −6.68%、夏普 0.63（但仓位只剩 12%）。"
        "换句话说，**同样是这条规则，把轨道放宽就是在拿收益换回撤**，没有免费的午餐。",
        "> **`首档线` 是第二个强参数**：75%（默认）→ 回撤 −26.58%、夏普 0.51；"
        "60% → 回撤 **−10.00%**、夏普 **0.73**，年化几乎不变（5.95%）。"
        "差别在于**更早拿到那 20% 的底仓**（2014 年 3 月 vs 11 月），而不是加仓更快。"
        "**若你愿意把首档线放到 60%，这是本表里性价比最高的一处改动** —— 但它已经越过了「75% 线」"
        "这个原始设定，属于改规则，所以默认没有采用。",
        "> **`step`（每日加仓步长）基本无关**：0.05~0.25 各档年化 5.9~6.1%、夏普 0.51~0.53。"
        "这一点很重要：**用户最初担心的「别刚低估就投满」对最终收益影响很小**，"
        "它真正改变的是**加仓期的回撤路径**（0.05 档回撤 −23.2% vs 0.10 档 −26.6%）。",
        "> **`底仓`**：0.2 档年化 5.81%/夏普 0.46（几乎无差），0.3 档年化 4.19%（会吞掉前两档，"
        "因为 30% 已经高于首档 20% 与次档 30%），所以**这条规则不适合用底仓修饰**。",
        "",
        "## 6. 分半样本",
        "",
        "| 区间 | 起 ~ 止 | 年数 | 策略年化 | 策略回撤 | 夏普 | 常仓50% | 常仓50% 回撤 | 沪深300 | 平均仓位 |",
        "|---|---|---:|---:|---:|---:|---:|---:|---:|---:|",
    ]
    for h in half:
        L.append(f"| {h['tag']} | {h['start']:%Y-%m-%d} ~ {h['end']:%Y-%m-%d} | "
                 f"{(h['end'] - h['start']).days / 365.25:.1f} | {erp.fmt_pct(h['ann'])} | "
                 f"{erp.fmt_pct(h['mdd'])} | {h['sharpe']:.2f} | {erp.fmt_pct(h['b50'])} | "
                 f"{erp.fmt_pct(h['b50_mdd'])} | {erp.fmt_pct(h['bh'])} | {h['w']:.0%} |")
    if len(half) == 2:
        a, b = half
        L += [
            f"> 前半段超额 **{(a['ann'] - a['b50']) * 100:+.2f}pp**、后半段 "
            f"**{(b['ann'] - b['b50']) * 100:+.2f}pp**。"
            + ("两段同号。"
               if (a["ann"] - a["b50"]) * (b["ann"] - b["b50"]) > 0 else
               "**两段反号 —— 规则的优势不稳定**，见 §8 的第三条。"),
        ]

    win_y = int((per_year["阶梯"] > per_year["常仓50%"]).sum())
    L += [
        "",
        "## 7. 逐年收益",
        "",
        "| 年 | 阶梯 | 常仓 50% | 沪深300 | 超额（vs 常仓50%） |",
        "|---|---:|---:|---:|---:|",
    ]
    for y, r in per_year.iterrows():
        L.append(f"| {int(y)} | {erp.fmt_pct(r['阶梯'])} | {erp.fmt_pct(r['常仓50%'])} | "
                 f"{erp.fmt_pct(r['沪深300'])} | "
                 f"{(r['阶梯'] - r['常仓50%']) * 100:+.1f}pp |")
    L += [
        "",
        f"> 逐年跑赢常仓 50% **{win_y}/{len(per_year)}** 年。",
        "",
        "## 8. 结论（哪些是数据说的，哪些不是）",
        "",
        f"1. **它的优势是「单位风险收益」，不是绝对收益**：年化 "
        f"{erp.fmt_pct(m_s['ann'])}（低于买入持有的 {erp.fmt_pct(erp.perf(bh_eq)['ann'])} "
        f"约 {abs(d_bh):.2f}pp），但最大回撤 {erp.fmt_pct(m_s['mdd'])} 只有买入持有 "
        f"{erp.fmt_pct(erp.perf(bh_eq)['mdd'])} 的六成，夏普 **{m_s['sharpe']:.2f}**"
        f" 在主对比里最高（常仓50% {st50['m']['sharpe']:.2f}、买入持有 "
        f"{erp.perf(bh_eq)['sharpe']:.2f}、v4 三档 {erp.perf(v4h['nav'])['sharpe']:.2f}；"
        f"同规则不计费 {erp.perf(nav_nf)['sharpe']:.2f}）。"
        f"代价是平均只有 {tgt.mean():.0%} 的仓位 —— **它大部分时间不在场**。",
        f"2. **比 v4 三档规则更好用**：同一标的、同一条现金腿下，阶梯口径 "
        f"{erp.fmt_pct(m_s['ann'])} vs v4 三档 {erp.fmt_pct(erp.perf(v4h['nav'])['ann'])}"
        f"（{d_v4:+.2f}pp/年），夏普 {m_s['sharpe']:.2f} vs "
        f"{erp.perf(v4h['nav'])['sharpe']:.2f}。差别不在「加仓更激进」，而在"
        "**减仓分三档、每档只触发一次**：v4 是「信号一变就整段切换」，ERP 反复穿越中线时被来回打脸。",
        "3. **「减仓后不回补」是加分项，不是漏洞**：改成可回补后年化从 5.90% 掉到 5.28%"
        "（夏普 0.51 → 0.47）—— 减仓后重新追高，等于把已经止盈的仓位在更贵的位置买回来。"
        "**这一条与直觉相反，是本次回测里最值得记住的发现。**",
        "4. **但逐年极不稳定，收益靠少数几年**：2019 +37.8%、2022 −0.5%、2023 −9.1%；"
        "2015~2017 连续三年几乎空仓（每年 +1.4%），2017 年落后常仓 50% 达 10.9pp。"
        "分半样本前半段超额 +2.08pp、后半段 −0.83pp（**反号**）—— "
        "**没有证据说它在最近 6 年还有效**。",
        "5. **参数明显敏感，方向都是「拿收益换回撤」**（§5）：窗口 500→120 年化 5.90%→2.67%；"
        "轨宽 2σ→2.5σ 回撤 −26.6%→−6.7% 但仓位只剩 12%；首档线 75%→60% 回撤 −26.6%→−10.0%、"
        "夏普 0.51→0.73。**这几档都比默认好看，但我没有采用** —— 在 13 年、5 个周期的样本上挑参数，"
        "就是标准的过拟合行为。默认值保持你给的 75%/25%/2σ，只把窗口从 250 改到 500"
        "（理由见 §1 与 §5.1）。",
        f"6. **换手与成本可控**：{n_add + n_half + n_exit} 次动作、累计费用 "
        f"{base['fees']:.2%}（{yrs:.1f} 年，年均 {base['fees'] / yrs:.2%}）。"
        f"清仓后再建仓要付一次完整往返（约 "
        f"{(ld.cfg('fee_buy') + ld.cfg('fee_sell')):.1%}），{n_exit} 个周期约 "
        f"{n_exit * (ld.cfg('fee_buy') + ld.cfg('fee_sell')):.1%} —— 与收益不在一个量级，"
        "但它会在**信号抖动频繁的年份**变成真实的拖累（见不计费那一行的 0.17pp 差）。",
        "7. **未计**：场外申赎确认时滞（T+1~T+2）、限购、指数基金跟踪误差、分红税。",
        "8. **现金腿简化**：1.40% 货基，忽略申赎与费率；若你把空仓的钱买短债，"
        "每年大约多 1~2pp，这会**系统性抬高**本策略的绝对收益（对超额影响很小）。",
        "",
        "> **一句话**：这条规则在本样本里做到了「用四分之一的仓位拿到四分之三的收益、"
        "六成的回撤」，但它的收益集中在 2018-2019 与 2022 两段，"
        "**不能保证未来复制**。机械规则输出，**非投资建议**。",
    ]

    path = os.path.join(OUTDIR, "_ladder_bt.md")
    with open(path, "w", encoding="utf-8") as f:
        f.write("\n".join(L))

    print(f"报告: {path}")
    print(f"样本 {d.index[0]:%Y-%m-%d} ~ {d.index[-1]:%Y-%m-%d} ({yrs:.1f} 年) · "
          f"平均仓位 {tgt.mean():.0%} · 动作 {len(acts)} 次 · 周期 {len(eps_cycles)} 个")
    for r in rows:
        w_txt = f"{r['w_avg']:.0%}" if "w_avg" in r else "—"
        print(f"  {r['name'][:34]:34s} 年化 {erp.fmt_pct(r['ann']):>9s} · "
              f"回撤 {erp.fmt_pct(r['mdd']):>9s} · 夏普 {r['sharpe']:5.2f} · "
              f"仓位 {w_txt}")
    print("图:", p_full, "|", p_3y, "|", p_nav)


if __name__ == "__main__":
    main()
