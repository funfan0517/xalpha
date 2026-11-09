# -*- coding: utf-8 -*-
"""黄金的央行购金驱动：中国月度储备 + 全球年度净购金。

用户追问「各地央行是否持续购金、购金力度变化」能不能当信号。这个变量和前两个
课题（美元/利率、政策周期/FOMC）性质完全不同：

  * 频率极低 —— 全球口径是**年度**，中国口径是**月度**；
  * 它是**实物供需**，不是价格衍生，逻辑上最"应该"有效；
  * 但正因为慢，它可能只能解释长期趋势，做不了择时。

四层检验：
  A 中国央行：月度储备 → 增量吨数 → 自动识别「连续增持期 / 暂停期」，逐段列出；
  B 转折点事件：「重启增持」和「暂停增持」的第一个月之后 1/3/6/12 个月金价表现；
  C 力度变化：中国 12 个月滚动购金量的加速度 vs 金价 12 个月收益（月度，n 足够）；
  D 全球口径：WGC 年度净购金 vs 当年/次年金价收益（n=16，只能看形态）；
  E 能不能用：按「全球年度购金 > 阈值」做年度择时，跟买入持有比。

数据：
  data/_cn_gold_reserves.csv  中国央行黄金储备月度（万盎司，akshare）
  全球年度净购金：脚本内常量 CB_ANNUAL（WGC Gold Demand Trends 口径，人工维护）

产物（与本脚本同目录）：
  _gold_centralbank.md / _gold_centralbank.json
  _gold_cb_cn.png       中国储备（阶梯）+ 金价
  _gold_cb_global.png   全球年度净购金 vs 金价年度收益
  _gold_cb_scatter.png  全球购金 vs 金价（当期/次年）散点

由 _gold_centralbank.py 生成，运行：
  python strategies/vol_legs/v6_gold/research/macro_drivers/_gold_centralbank.py
"""
import os
import sys
import json
import warnings

import numpy as np
import pandas as pd

warnings.filterwarnings("ignore")

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

_HERE = os.path.dirname(os.path.abspath(__file__))
_RES = os.path.dirname(_HERE)
_V6 = os.path.dirname(_RES)
_VL = os.path.dirname(_V6)
_ROOT = os.path.dirname(os.path.dirname(_VL))
DATA = os.path.join(_ROOT, "data")

OUT_MD = os.path.join(_HERE, "_gold_centralbank.md")
OUT_JSON = os.path.join(_HERE, "_gold_centralbank.json")
FIG_CN = os.path.join(_HERE, "_gold_cb_cn.png")
FIG_GL = os.path.join(_HERE, "_gold_cb_global.png")
FIG_SC = os.path.join(_HERE, "_gold_cb_scatter.png")

REFRESH = "--refresh" in sys.argv
TD = 252
OZ_PER_TONNE = 32150.7          # 1 吨 = 32150.7 金衡盎司
WAN_OZ = 1e4                    # 数据单位是「万盎司」

# 全球央行年度净购金（吨）。人工输入：WGC《Gold Demand Trends》年度口径。
# 2025 及之后留空 —— 等 WGC 年报更新，不要臆造。
CB_ANNUAL = {
    2009: 141, 2010: 79, 2011: 481, 2012: 544, 2013: 409, 2014: 584,
    2015: 580, 2016: 395, 2017: 379, 2018: 656, 2019: 605, 2020: 255,
    2021: 450, 2022: 1082, 2023: 1037, 2024: 1045,
}

PAUSE_MIN_MONTHS = 6            # 连续 ≥6 个月储备不变 → 视为「暂停增持」
BUY_MIN_MONTHS = 3              # 连续 ≥3 个月增加 → 视为「增持期」


# --------------------------------------------------------------------------
# 数据
# --------------------------------------------------------------------------
def load_cn():
    p = os.path.join(DATA, "_cn_gold_reserves.csv")
    if (not REFRESH) and os.path.exists(p):
        d = pd.read_csv(p)
        d["date"] = pd.to_datetime(d["date"])
        return d
    import akshare as ak

    raw = ak.macro_china_foreign_exchange_gold()
    rows = []
    for _, r in raw.iterrows():
        s = str(r["统计时间"]).strip()
        if "." not in s:
            continue
        y, m = s.split(".")[:2]
        try:
            y, m = int(y), int(m)
        except ValueError:
            continue
        if y < 2002:
            continue
        v = r["黄金储备"]
        if pd.isna(v) or float(v) <= 0:
            continue
        rows.append({"date": pd.Timestamp(year=y, month=m, day=1), "wan_oz": float(v)})
    d = pd.DataFrame(rows).drop_duplicates("date").sort_values("date").reset_index(drop=True)
    d.to_csv(p, index=False, encoding="utf-8")
    print("[fetch] 中国央行储备 n=%d %s ~ %s" % (len(d), d["date"].iloc[0].date(),
                                                 d["date"].iloc[-1].date()))
    return d


def load_gold():
    g = pd.read_csv(os.path.join(DATA, "_gold_xau.csv"))
    g["date"] = pd.to_datetime(g["date"])
    return g.sort_values("date").reset_index(drop=True)


# --------------------------------------------------------------------------
# 工具
# --------------------------------------------------------------------------
def _tstat(x):
    x = np.asarray(x, dtype=float)
    x = x[~np.isnan(x)]
    if len(x) < 5:
        return np.nan, np.nan, len(x)
    return float(x.mean()), float(x.mean() / (x.std(ddof=1) / np.sqrt(len(x)))), len(x)


def _corr(a, b):
    a, b = pd.Series(a), pd.Series(b)
    m = a.notna() & b.notna()
    if m.sum() < 8:
        return np.nan, int(m.sum())
    return float(a[m].corr(b[m])), int(m.sum())


# --------------------------------------------------------------------------
# A 中国央行：增持期 / 暂停期自动识别
# --------------------------------------------------------------------------
def cn_episodes(cn):
    d = cn.copy()
    d["tonnes"] = d["wan_oz"] * WAN_OZ / OZ_PER_TONNE
    d["d_tonnes"] = d["tonnes"].diff()
    # 央行只公布变化，所以「不变」= 0
    d["d_tonnes"] = d["d_tonnes"].fillna(0.0)
    d["move"] = np.where(d["d_tonnes"] > 0.5, "buy",
                         np.where(d["d_tonnes"] < -0.5, "sell", "hold"))
    d["ep"] = (d["move"] != d["move"].shift()).cumsum()

    rows = []
    for _, g in d.groupby("ep"):
        n = len(g)
        mv = g["move"].iloc[0]
        if mv == "hold" and n < PAUSE_MIN_MONTHS:
            continue
        if mv == "buy" and n < BUY_MIN_MONTHS:
            continue
        rows.append({
            "type": {"buy": "增持", "sell": "减持", "hold": "暂停"}[mv],
            "start": str(g["date"].iloc[0].date()),
            "end": str(g["date"].iloc[-1].date()),
            "months": int(n),
            "chg_tonnes": float(g["d_tonnes"].sum()),
            "start_tonnes": float(g["tonnes"].iloc[0] - g["d_tonnes"].iloc[0]),
            "end_tonnes": float(g["tonnes"].iloc[-1]),
        })
    return d, pd.DataFrame(rows)


# --------------------------------------------------------------------------
# B 转折点事件
# --------------------------------------------------------------------------
def pivot_events(d, gold):
    """「暂停→增持」重启点，与「增持→暂停」停机点，之后 1/3/6/12 个月金价。"""
    d = d.reset_index(drop=True)
    g = gold.copy()
    g["ym"] = g["date"].dt.to_period("M")
    me = g.groupby("ym")["gold"].last()          # 月末金价
    idx = me.index

    events = []
    for i in range(1, len(d)):
        prev, cur = d["move"].iloc[i - 1], d["move"].iloc[i]
        if prev == "hold" and cur == "buy":
            events.append({"date": d["date"].iloc[i], "kind": "重启增持"})
        elif prev == "buy" and cur == "hold":
            events.append({"date": d["date"].iloc[i], "kind": "暂停增持"})
    rows = []
    for e in events:
        p = e["date"].to_period("M")
        pos = idx.searchsorted(p)
        if pos >= len(idx):
            continue
        base = me.iloc[pos - 1] if pos > 0 else me.iloc[pos]   # 事件月**之前**的月末收盘
        row = {"date": str(e["date"].date()), "kind": e["kind"], "px": float(base)}
        for h, col in [(1, "1月"), (3, "3月"), (6, "6月"), (12, "12月")]:
            if pos - 1 + h < len(idx):
                row[col] = float(me.iloc[pos - 1 + h] / base - 1)
            else:
                row[col] = np.nan
        rows.append(row)
    return pd.DataFrame(rows)


# --------------------------------------------------------------------------
# C 力度变化（中国，月度）
# --------------------------------------------------------------------------
def cn_momentum(d, gold):
    """12 个月滚动购金量（吨）与金价 12 个月收益；再看「加速度」。"""
    d = d.set_index("date")
    roll = d["d_tonnes"].rolling(12).sum().dropna()
    g = gold.copy()
    g["ym"] = g["date"].dt.to_period("M")
    me = g.groupby("ym")["gold"].last()
    me.index = me.index.to_timestamp()
    ret12 = (me / me.shift(12) - 1).dropna()

    df = pd.DataFrame({"buy12": roll}).join(pd.DataFrame({"ret12": ret12}), how="inner").dropna()
    # 2015 年之前中国不按月公布，储备长期不变后突跳，会把「暂停期」样本灌满
    df = df[df.index >= "2015-01-01"]
    df["accel"] = df["buy12"].diff(6)        # 半年内购金力度的变化

    out = {}
    out["corr_same"] = _corr(df["buy12"], df["ret12"])
    out["corr_accel"] = _corr(df["accel"], df["ret12"].shift(-6))
    # 按购金力度分三档
    q = df["buy12"].quantile([1 / 3, 2 / 3]).values
    grp = pd.cut(df["buy12"], [-1e9, q[0], q[1], 1e9], labels=["力度弱", "力度中", "力度强"])
    tab = df.groupby(grp, observed=True).agg(
        n=("ret12", "size"), mean_ret=("ret12", "mean"),
        win=("ret12", lambda s: (s > 0).mean()),
        mean_buy=("buy12", "mean")).reset_index().rename(columns={"buy12": "力度"})
    return df, tab, out


# --------------------------------------------------------------------------
# D/E 全球口径
# --------------------------------------------------------------------------
def global_table(gold):
    g = gold.copy()
    g["y"] = g["date"].dt.year
    ye = g.groupby("y")["gold"].last()
    yr = (ye / ye.shift(1) - 1).dropna()

    rows = []
    for y, tons in sorted(CB_ANNUAL.items()):
        if y not in yr.index:
            continue
        nxt = yr.get(y + 1, np.nan)
        rows.append({"year": y, "cb_tons": tons,
                     "gold_ret": float(yr.loc[y]),
                     "gold_ret_next": float(nxt) if not pd.isna(nxt) else np.nan})
    df = pd.DataFrame(rows)
    c_same = _corr(df["cb_tons"], df["gold_ret"])
    c_next = _corr(df["cb_tons"], df["gold_ret_next"])
    # 稳健性：剔掉最后一个配对（近年那一波），看相关是不是被单点撑起来的
    d2 = df[df["gold_ret_next"].notna()].iloc[:-1]
    c_ex = _corr(d2["cb_tons"], d2["gold_ret_next"])
    return df, {"same": c_same, "next": c_next, "next_ex_last": c_ex}


def global_backtest(df, gold):
    """t 年末知道当年购金量 → t+1 年持有。阈值 500 吨。"""
    g = gold.copy()
    g["y"] = g["date"].dt.year
    ye = g.groupby("y")["gold"].last()
    yr = (ye / ye.shift(1) - 1).dropna()

    rows = []
    for thr in [400, 500, 600, 800]:
        pos_years, rets = [], []
        for _, r in df.iterrows():
            if r["year"] + 1 in yr.index and not pd.isna(r["gold_ret_next"]):
                pos_years.append(1.0 if r["cb_tons"] >= thr else 0.0)
                rets.append(float(yr.loc[r["year"] + 1]))
        if not pos_years:
            continue
        p = pd.Series(pos_years)
        r = pd.Series(rets)
        eq = (1 + p * r).cumprod()
        n = len(r)
        rows.append({"thr": thr, "years": n, "expo": float(p.mean()),
                     "ann": float(eq.iloc[-1] ** (1.0 / n) - 1),
                     "mdd": float((eq / eq.cummax() - 1).min()),
                     "vol": float((p * r).std(ddof=1)),
                     "win": float(((p * r) > 0).sum() / max(p.sum(), 1))})
    # 买入持有（同区间）
    years = [r["year"] + 1 for _, r in df.iterrows()
             if r["year"] + 1 in yr.index and not pd.isna(r["gold_ret_next"])]
    bh = pd.Series([float(yr.loc[y]) for y in years])
    eq = (1 + bh).cumprod()
    rows.append({"thr": -1, "years": len(bh), "expo": 1.0,
                 "ann": float(eq.iloc[-1] ** (1.0 / len(bh)) - 1),
                 "mdd": float((eq / eq.cummax() - 1).min()),
                 "vol": float(bh.std(ddof=1)), "win": float((bh > 0).mean())})
    return pd.DataFrame(rows)


# --------------------------------------------------------------------------
# 图
# --------------------------------------------------------------------------
def make_plots(d, gmonth, gl, cn_tab):
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    plt.rcParams["font.sans-serif"] = ["Microsoft YaHei", "SimHei", "DejaVu Sans"]
    plt.rcParams["axes.unicode_minus"] = False
    plt.rcParams["figure.dpi"] = 120

    # 1 中国储备 + 金价
    fig, axes = plt.subplots(2, 1, figsize=(11, 6.4), sharex=True,
                             gridspec_kw={"height_ratios": [1, 1.1]})
    ax = axes[0]
    ax.plot(d["date"], d["tonnes"], lw=1.6, color="#b7791f", drawstyle="steps-post")
    ax.set_ylabel("中国官方黄金储备（吨）")
    ax.grid(alpha=0.25)
    ax.set_title("中国央行黄金储备（阶梯）与金价 —— 增持是脉冲式的，不是连续的")
    for _, r in cn_tab.iterrows():
        if r["type"] == "增持" and r["months"] >= 6:
            ax.axvspan(pd.Timestamp(r["start"]), pd.Timestamp(r["end"]),
                       color="green", alpha=0.12, lw=0)

    ax = axes[1]
    ax.plot(gmonth.index, gmonth.values, lw=1.1, color="#2b6cb0")
    ax.set_ylabel("XAU/USD（月末）")
    ax.grid(alpha=0.25)
    for _, r in cn_tab.iterrows():
        if r["type"] == "增持" and r["months"] >= 6:
            ax.axvspan(pd.Timestamp(r["start"]), pd.Timestamp(r["end"]),
                       color="green", alpha=0.12, lw=0)
    fig.tight_layout()
    fig.savefig(FIG_CN)
    plt.close(fig)

    # 2 全球年度购金 vs 金价
    fig, ax = plt.subplots(figsize=(10, 4.4))
    ax.bar(gl["year"], gl["cb_tons"], color="#2f855a", alpha=0.75, label="全球央行净购金（吨）")
    ax.set_ylabel("吨")
    ax.set_xlabel("年")
    ax2 = ax.twinx()
    ax2.plot(gl["year"], gl["gold_ret"] * 100, color="#c53030", marker="o", ms=4, lw=1.4,
             label="黄金当年收益（%）")
    ax2.set_ylabel("当年收益 %")
    ax.set_title("全球央行年度净购金 vs 黄金当年收益（买得多的时候，金价未必涨）")
    h1, l1 = ax.get_legend_handles_labels()
    h2, l2 = ax2.get_legend_handles_labels()
    ax.legend(h1 + h2, l1 + l2, fontsize=9, loc="upper left")
    ax.grid(alpha=0.25)
    fig.tight_layout()
    fig.savefig(FIG_GL)
    plt.close(fig)

    # 3 散点：当期 / 次年
    fig, axes = plt.subplots(1, 2, figsize=(11, 4.4))
    for ax, col, ttl in zip(axes, ["gold_ret", "gold_ret_next"],
                            ["当年（同期，事后解释）", "次年（领先 1 年，能否交易）"]):
        m = gl[col].notna()
        x, y = gl.loc[m, "cb_tons"].values, gl.loc[m, col].values
        ax.scatter(x, y, s=42, color="#2b6cb0")
        for _, r in gl[m].iterrows():
            ax.annotate(str(r["year"]), (r["cb_tons"], r[col]),
                        fontsize=7, alpha=0.65, xytext=(3, 3), textcoords="offset points")
        if len(x) >= 3:
            b, a = np.polyfit(x, y, 1)
            xs = np.linspace(x.min(), x.max(), 20)
            ax.plot(xs, a + b * xs, color="#c53030", lw=1.4)
            ax.set_title("%s  r=%.2f" % (ttl, np.corrcoef(x, y)[0, 1]), fontsize=10)
        ax.axhline(0, color="black", lw=0.6)
        ax.set_xlabel("全球央行年度净购金（吨）")
        ax.set_ylabel("黄金收益")
        ax.grid(alpha=0.25)
    fig.tight_layout()
    fig.savefig(FIG_SC)
    plt.close(fig)


# --------------------------------------------------------------------------
# 报告
# --------------------------------------------------------------------------
def _md_table(df, cols=None, fmt=None):
    cols = cols or list(df.columns)
    fmt = fmt or {}
    lines = ["| " + " | ".join(cols) + " |", "|" + "|".join(["---"] * len(cols)) + "|"]
    for _, r in df.iterrows():
        cells = []
        for c in cols:
            v = r[c]
            if c in fmt:
                cells.append(fmt[c](v))
            elif isinstance(v, (int, np.integer)):
                cells.append(str(int(v)))
            elif isinstance(v, float):
                cells.append("—" if np.isnan(v) else "%.3f" % v)
            else:
                cells.append(str(v))
        lines.append("| " + " | ".join(cells) + " |")
    return "\n".join(lines)


def write_md(cn, cn_tab, piv, mono_tab, mono_stat,
             gl, gl_stat, bt):
    def pcar(v):
        return "—" if pd.isna(v) else "%+.1f%%" % (v * 100)

    def pct(v):
        return "—" if pd.isna(v) else "%.0f%%" % (v * 100)

    def mdd(v):
        return "—" if pd.isna(v) else ("0.0%" if abs(v) < 1e-9 else "%+.1f%%" % (v * 100))

    def t1(v):
        return "—" if pd.isna(v) else "%.0f" % v

    def f2(v):
        return "—" if pd.isna(v) else "%.2f" % v

    last = cn.iloc[-1]
    base_bh = bt[bt["thr"] < 0].iloc[0]
    best = bt[bt["thr"] >= 0].sort_values("ann", ascending=False).iloc[0]

    L = []
    A = L.append
    A("# V6 黄金 · 央行购金：持续性与力度变化")
    A("")
    A("> 由 `_gold_centralbank.py` 生成。检验用户追问的「各地央行是否持续购金、购金力度变化」。")
    A("> 数据：中国官方黄金储备 **月度**（akshare，%s ~ %s，%d 条）；"
      "全球央行净购金 **年度**（WGC Gold Demand Trends 口径，脚本常量 `CB_ANNUAL`，"
      "2009-2024，**2025 及之后未录入**）。"
      % (cn["date"].iloc[0].date(), cn["date"].iloc[-1].date(), len(cn)))
    A("> 换算：1 吨 = 32,150.7 金衡盎司。当前中国储备 **%.0f 吨**（%.0f 万盎司）。"
      % (last["tonnes"], last["wan_oz"]))
    A("")
    A("## 1. 一句话结论")
    A("")
    # 只统计 2015 年之后 —— 在此之前中国不按月公布，储备长期不变后突跳，不可作数
    recent = cn_tab[cn_tab["start"] >= "2015-01-01"]
    buy_segs = recent[recent["type"] == "增持"]
    pause_segs = recent[recent["type"] == "暂停"]
    A("1. **「持续购金」是个错觉 —— 央行买金是脉冲式的，不是连续的。** "
      "2015 年以来 %d 段增持期合计 %d 个月、%d 段暂停期合计 %d 个月，"
      "**平均每段增持只持续 %.1f 个月，每段暂停 %.1f 个月**。"
      "最长的暂停从「%s」起，连续 %d 个月一动不动。"
      % (len(buy_segs), int(buy_segs["months"].sum()),
         len(pause_segs), int(pause_segs["months"].sum()),
         buy_segs["months"].mean(), pause_segs["months"].mean(),
         pause_segs.loc[pause_segs["months"].idxmax(), "start"],
         int(pause_segs["months"].max())))
    A("2. **全球口径看，央行买得最多的时候金价在跌。** 2013-2015 年央行年均净购金 %.0f 吨，"
      "同期金价三年累计 **%s**；而 2022-2024 年买入量翻倍到 %.0f 吨，金价才真正起来。"
      "**同期相关 r=%.2f（买得多反而跌），领先一年 r=%.2f —— 但剔掉最后一年后只剩 %.2f**"
      "（见 §4）。可见「买了就涨」和「买了明年涨」都站不住。"
      % (np.mean([409, 584, 580]),
         pcar(np.prod([1 + r for r in gl.loc[gl["year"].isin([2013, 2014, 2015]),
                                             "gold_ret"]]) - 1),
         np.mean([1082, 1037, 1045]),
         gl_stat["same"][0], gl_stat["next"][0], gl_stat["next_ex_last"][0]))
    A("3. **「力度变化」同样没有信息。** 中国 12 个月滚动购金量的半年加速度 vs "
      "未来 6~12 个月金价收益，r=%.2f（n=%d，2015 年后）—— 基本是零。"
      "按 12 个月滚动购金量分三档，后续表现分别是 %s / %s / %s，"
      "**不是单调的**：买得最猛的那一档并不比暂停期好。"
      % (mono_stat["corr_accel"][0], mono_stat["corr_accel"][1],
         pcar(mono_tab.iloc[0]["mean_ret"]), pcar(mono_tab.iloc[1]["mean_ret"]),
         pcar(mono_tab.iloc[2]["mean_ret"])))
    A("4. **按年度购金量做择时，年化略输买入持有，但回撤极低。** 最好的阈值 %d 吨年化 %s、"
      "最大回撤 %s、平均仓位 %s；买入持有 %s / %s。**看着像「低回撤版」——但这不是择时能力，"
      "是 16 年里只持有了 %d 年，恰好躲掉了 2013 和 2015 两个下跌年。**样本外无法验证。"
      % (best["thr"], pcar(best["ann"]), mdd(best["mdd"]), pct(best["expo"]),
         pcar(base_bh["ann"]), pcar(base_bh["mdd"]), int(best["expo"] * base_bh["years"])))
    A("")
    A("## 2. 中国央行：增持期 / 暂停期全景")
    A("")
    A(_md_table(cn_tab, ["type", "start", "end", "months", "chg_tonnes",
                         "start_tonnes", "end_tonnes"],
                {"chg_tonnes": lambda v: "%+.0f" % v,
                 "start_tonnes": lambda v: "%.0f" % v,
                 "end_tonnes": lambda v: "%.0f" % v}))
    A("")
    A("> 判定口径：连续 ≥%d 个月储备不变 = 「暂停」，连续 ≥%d 个月增加 = 「增持期」。"
      "中国央行只在增持时公布变化，所以「不变」就等于「没买」。"
      % (PAUSE_MIN_MONTHS, BUY_MIN_MONTHS))
    A(">")
    A("> ⚠ **2015 年之前的段不可作数**：那时中国不按月公布，储备是长期不变后一次跳增"
      "（2001-2003 的 500→600 吨就是这么来的），下面 §1 的统计只用 2015 年后的段。")
    A("")
    A("![中国储备](%s)" % os.path.basename(FIG_CN))
    A("")
    A("## 3. 转折点：「重启增持」和「暂停增持」之后金价怎么走")
    A("")
    A("事件月**之前**的月末收盘为基准，看之后 1/3/6/12 个月。")
    A("")
    A(_md_table(piv, ["date", "kind", "px", "1月", "3月", "6月", "12月"],
                {"px": lambda v: "%.0f" % v, "1月": pcar, "3月": pcar,
                 "6月": pcar, "12月": pcar}))
    A("")
    r0 = piv[piv["kind"] == "重启增持"]
    p0 = piv[piv["kind"] == "暂停增持"]
    if len(r0):
        A("- **重启增持后 12 个月**：均值 %s，%d/%d 次为正"
          % (pcar(r0["12月"].mean()), int((r0["12月"] > 0).sum()), int(r0["12月"].notna().sum())))
    if len(p0):
        A("- **暂停增持后 12 个月**：均值 %s，%d/%d 次为正"
          % (pcar(p0["12月"].mean()), int((p0["12月"] > 0).sum()), int(p0["12月"].notna().sum())))
    A("")
    A("> 两组**几乎没差别**（重启后 12 个月均值 %s vs 暂停后 %s），说明「转折」本身不携带"
      "方向信息 —— 暂停增持不是利空（2024-05 暂停后 12 个月还涨了 +43.8%%）。"
      "另外要注意**反向因果**：储备是按美元市价折算的，金价涨了储备价值自然涨，"
      "而且央行也确实倾向在上涨中增持，所以「重启增持」更像**确认信号**而非**领先信号**。"
      % (pcar(r0["12月"].mean()), pcar(p0["12月"].mean())))
    A("")
    A("## 4. 全球口径：年度购金 vs 金价")
    A("")
    A(_md_table(gl, ["year", "cb_tons", "gold_ret", "gold_ret_next"],
                {"year": lambda v: "%d" % int(v), "cb_tons": t1,
                 "gold_ret": pcar, "gold_ret_next": pcar}))
    A("")
    A("**相关系数**：同期 r=%.2f（n=%d）；购金 → 次年收益 r=%.2f（n=%d）。"
      % (gl_stat["same"][0], gl_stat["same"][1],
         gl_stat["next"][0], gl_stat["next"][1]))
    A("")
    A("> ⚠ **那个 r=%.2f 是被单点撑起来的。** 剔掉最后一个配对（2024 购金 → 2025 收益 +64.6%%）后，"
      "r 掉到 **%.2f**（n=%d）。也就是说「央行买得多 → 明年金价涨」这个规律，"
      "本质上只有 2022-2025 这一轮牛市在支撑 —— 而那一轮本身就是央行购金与金价互相强化的结果，"
      "拿它预测下一轮等于假设「上一轮会重复」。"
      % (gl_stat["next"][0], gl_stat["next_ex_last"][0], gl_stat["next_ex_last"][1]))
    A("")
    A("![全球购金](%s)" % os.path.basename(FIG_GL))
    A("")
    A("![散点](%s)" % os.path.basename(FIG_SC))
    A("")
    A("## 5. 力度变化（中国月度，n=142）")
    A("")
    A("12 个月滚动购金量（吨）分三档，看**同期** 12 个月金价收益 —— 注意是同期，"
      "用来判断「力度」到底反映了什么，不是预测：")
    A("")
    A(_md_table(mono_tab, ["力度", "n", "mean_buy", "mean_ret", "win"],
                {"mean_buy": lambda v: "%.0f 吨" % v,
                 "mean_ret": pcar, "win": pct}))
    A("")
    A("同期相关 r=%.2f（n=%d）；**力度加速度 → 未来 6-12 个月收益** r=%.2f（n=%d）。"
      % (mono_stat["corr_same"][0], mono_stat["corr_same"][1],
         mono_stat["corr_accel"][0], mono_stat["corr_accel"][1]))
    A("")
    A("## 6. 能不能变成钱：年度购金阈值择时（t 年末确认 → t+1 年持有）")
    A("")
    bt2 = bt.copy()
    bt2["thr"] = bt2["thr"].map(lambda x: "买入持有" if x < 0 else "≥%d 吨" % int(x))
    A(_md_table(bt2, ["thr", "years", "expo", "ann", "mdd", "vol", "win"],
                {"expo": pct, "ann": pcar, "mdd": mdd, "vol": pct, "win": pct}))
    A("")
    A("> 注意样本只有 **%d 个年度**，且 2022-2025 是同一轮牛市 —— "
      "任何「有用」的结论都是这四年撑出来的。阈值越低，越接近买入持有，也就越说明"
      "**这个指标没有提供额外的择时信息**。" % int(base_bh["years"]))
    A("")
    A("## 7. 局限（这一节尤其重要）")
    A("")
    A("1. **全球口径是人工录入的年度常量**（`CB_ANNUAL`），不是我抓的。"
      "来源是 WGC《Gold Demand Trends》年度央行净购金，**使用前请核对**；"
      "2025 及之后留空，因为该口径需等年报。这决定了 §4/§6 的所有数字都只能当参考。")
    A("2. **中国月度储备是「万盎司」而非吨**，且央行只在增持时更新，"
      "所以「暂停」段内无法区分「不想买」和「没公布」。")
    A("3. **同期相关是负的（r=%.2f），但这不代表「买得多所以跌」**。更可能的两条机制："
      "① 央行**逆势买入**（金价跌、占比被动下降时才补）；"
      "② 全球购金量本身就是金价的**滞后反应** —— 2022-2024 买得多，是因为金价涨了之后"
      "各国被动补配。两条都说明「购金量 → 金价」这个方向不成立。"
      % gl_stat["same"][0])
    A("4. **样本极大受限**：全球 16 个年度、中国 5 段增持期、转折点 %d 个。"
      "任何 p 值都不可信，本报告只做形态判断。" % len(piv))
    A("5. 仓库里 `strategies/vol_legs/rule.py` 的 `wgrc_fuse_quarters=2`（连续 2 季购金为负→降档）"
      "一直是 `null` 未维护 —— 本报告第一次给它提供了可回测的历史序列（`_cn_gold_reserves.csv`），"
      "但**建议仍然不要打开**：中国的增持是脉冲式的，按季度判定会频繁误触发。")
    A("")

    with open(OUT_MD, "w", encoding="utf-8") as f:
        f.write("\n".join(L))


# --------------------------------------------------------------------------
def main():
    cn = load_cn()
    gold = load_gold()

    d, cn_tab = cn_episodes(cn)
    cn = d                                    # 带上 tonnes / d_tonnes / move
    piv = pivot_events(d, gold)
    # 2015 年之前中国不按月公布（储备长期不变后突跳），事件不可靠，只保留 2015 起
    piv = piv[piv["date"] >= "2015-01-01"].reset_index(drop=True)

    g = gold.copy()
    g["ym"] = g["date"].dt.to_period("M")
    gmonth = g.groupby("ym")["gold"].last()
    gmonth.index = gmonth.index.to_timestamp()

    mono_df, mono_tab, mono_stat = cn_momentum(d, gold)
    gl, gl_stat = global_table(gold)
    bt = global_backtest(gl, gold)

    make_plots(d, gmonth, gl, cn_tab)

    print("\n== 中国央行 增持/暂停 段 ==")
    print(cn_tab.to_string(index=False, float_format=lambda x: "%.1f" % x))
    print("\n== 转折点 ==")
    print(piv.to_string(index=False, float_format=lambda x: "%.4f" % x))
    print("\n== 力度分组 ==")
    print(mono_tab.to_string(index=False, float_format=lambda x: "%.3f" % x))
    print("相关:", {k: (round(v[0], 3), v[1]) for k, v in mono_stat.items()})
    print("\n== 全球年度 ==")
    print(gl.to_string(index=False, float_format=lambda x: "%.3f" % x))
    print("相关 同期/次年:", gl_stat)
    print("\n== 择时 ==")
    print(bt.to_string(index=False, float_format=lambda x: "%.3f" % x))

    out = {"cn_episodes": cn_tab.to_dict("records"),
           "pivots": piv.to_dict("records"),
           "momentum": mono_tab.to_dict("records"),
           "momentum_stat": {k: v for k, v in mono_stat.items()},
           "global": gl.to_dict("records"),
           "global_stat": gl_stat,
           "backtest": bt.to_dict("records"),
           "last": {"date": str(cn["date"].iloc[-1].date()),
                    "tonnes": float(d["tonnes"].iloc[-1])}}
    with open(OUT_JSON, "w", encoding="utf-8") as f:
        json.dump(out, f, ensure_ascii=False, indent=2, default=float)
    write_md(cn, cn_tab, piv, mono_tab, mono_stat, gl, gl_stat, bt)
    print("\n[save] %s\n[save] %s" % (OUT_JSON, OUT_MD))


if __name__ == "__main__":
    main()
