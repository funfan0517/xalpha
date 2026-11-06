# -*- coding: utf-8 -*-
"""波动资产 · 变种横向对比（**两次综合对比** + 逐腿分面图）。

家族现在有 10 个变种，标的池不是同一个，硬塞进一张图没有意义，故拆成两组：

**第一组 v1–v6 × 全部七腿**（`rule.LEG_ORDER`，README.md §2.1）
- 七腿逐腿时机家族：v1 MA20 下车 / v2 BOLL 突破 / v3 MACD+BOLL / v5 趋势闸门
  （v5 取 README 默认口径：七腿全套闸门 + 自适应波动层，**不是**纯信号层）；
- 沪深300 单标的方法论：v4 ERP 股债通道 / v6 PE 分位仓位带
  —— 它们不发生在七腿内部，只参与组合层对比与沪深300 基准对照。

**第二组 v7–v10 × 仅纳指100 + 标普500**（四支同池，才有同尺子比较的意义）
- v7 年线闸门（月末 + 2 月确认 + 波动层）/ v8 金叉 50/200（日频，对照组）
- v9 波动率目标（月末 + 月中补丁）/ v10 纯波动层（日频，无方向）

## 图为什么改了

旧版把 4 个策略的买卖点全部堆在**同一条价格曲线**上（一张图 40~80 个标记），
一半以上互相压住，读不出「谁在什么时候动」。改版：

1. **逐腿图 = 2×2 分面**：一格 = 一个策略，格内只有**它自己**的买卖点
   （▲ 空心画在价格线上 = 这笔成交时标的价格在哪；实心画在策略曲线上 = 这笔发生时组合在哪）；
2. **综合对比图 = 上格净值 + 下格回撤**：净值曲线不再只有一条「谁高谁低」，
   回撤带直接回答「回撤是不是真的被压下去了」；
3. 连续仓位策略（v5 / v9 / v10）只标 **|Δ仓位| ≥ 20pp** 的大动作，小动作交给底色仓位带。

产出（本目录 research/，策略类测试调优产物）：
- `_v1v6_leg_{leg}.png`         七腿 × 四策略分面（价格 + 各自净值 + 各自买卖点）
- `_v1v6_overlay_{leg}.png`     七腿 × 单标的口径叠加（净值 + 回撤，基准 = 该腿买入持有）
- `_v1v6_comprehensive.png`     第一组综合对比（组合层，净值 + 回撤，基准 = 沪深300 买入持有）
- `_v7v10_leg_{ndx,spx}.png`    美股两腿 × 四变种分面（价格 + 净值 + 仓位带 + 大动作）
- `_v7v10_comprehensive.png`    第二组综合对比（两腿等权组合层，基准 = 静态 50/50）
- `_v1v10_compare.md` / `.jsonl` 报告 + 机读结果

> 模拟结果，非投资建议。
"""
import json
import math
import os
import sys

import numpy as np
import pandas as pd

try:
    sys.stdout.reconfigure(encoding="utf-8")  # type: ignore[attr-defined]
except Exception:  # noqa: BLE001
    pass

import matplotlib
matplotlib.use("Agg")
import matplotlib.dates as mdates          # noqa: E402
import matplotlib.pyplot as plt           # noqa: E402
import matplotlib.ticker as mticker       # noqa: E402
from matplotlib.lines import Line2D       # noqa: E402

_HERE = os.path.dirname(os.path.abspath(__file__))    # .../research/v1v10_compare
_RES = os.path.dirname(_HERE)                         # strategies/vol_legs/research
_VL = os.path.dirname(_RES)                           # strategies/vol_legs
_ROOT = os.path.dirname(os.path.dirname(_VL))         # 仓库根（推导，不写死）
for _p in (_HERE, _VL):
    if _p not in sys.path:
        sys.path.insert(0, _p)
for _d in ("v1_ma20", "v2_boll_breakout", "v3_macd_boll", "v5_trend_gate",
           "v4_erp_band", "v6_pe_band",
           "v7_us_gate", "v8_golden_cross", "v9_vol_target", "v10_vol_layer"):
    _pp = os.path.join(_VL, _d)
    if _pp not in sys.path:
        sys.path.insert(0, _pp)

import data as vdata                      # noqa: E402
import engine                             # noqa: E402
import rule                               # noqa: E402

# 离线优先：禁止回测期内的网络刷新（仅读本地缓存）
vdata._otc_stale = lambda: False          # type: ignore[attr-defined]

import boll                               # noqa: E402
import band as v6band                     # noqa: E402
import erp as v4erp                       # noqa: E402
import golden_cross as v8gc               # noqa: E402
import ma20                               # noqa: E402
import macd_boll                          # noqa: E402
import trend_gate                         # noqa: E402
import us_gate as v7gate                  # noqa: E402
import vol_layer as v10vl                 # noqa: E402
import vol_target as v9vt                 # noqa: E402

plt.rcParams["font.sans-serif"] = ["Microsoft YaHei", "SimHei", "DejaVu Sans"]
plt.rcParams["axes.unicode_minus"] = False

OUTDIR = _HERE
LEGS = rule.LEG_ORDER
NAMES = {k: rule.LEGS[k]["name"] for k in LEGS}
SLEEVE = rule.SLEEVE_PP                    # 40pp

# ---------------------------------------------------------------- 第一组：v1–v6
TIMING = ["v1", "v2", "v3", "v5"]
TIMING_COLOR = {"v1": "#1f77b4", "v2": "#d62728", "v3": "#2ca02c", "v5": "#9467bd",
                "v5p": "#c5b0d5"}
STRAT_NAME = {
    "v1": "V1 MA20下车", "v2": "V2 BOLL突破", "v3": "V3 MACD+BOLL",
    "v5": "V5 趋势闸门", "v5p": "V5 纯趋势(关波动层)",
    "v4": "V4 ERP股债通道", "v6": "V6 PE分位带",
    "BH": "买入持有",
}
C_UP, C_DOWN = "#d33", "#1f8a4c"
C_PRICE, C_NAV, C_BENCH = "#7a8794", "#1b2b3a", "#111111"
BIG_MOVE = 0.20                            # 连续仓位策略：只标 |Δ仓位| ≥ 20pp 的大动作

# ---------------------------------------------------------------- 第二组：v7–v10
US_MODS = [
    ("v7", v7gate, "V7 年线闸门", "#1f77b4"),
    ("v8", v8gc, "V8 金叉50/200", "#bcbd22"),
    ("v9", v9vt, "V9 波动率目标", "#17becf"),
    ("v10", v10vl, "V10 纯波动层", "#d62728"),
]
US_LEGS = ("ndx", "spx")
US_COST = 0.001                            # 统一单边 0.10%（同尺子；v9 自身默认 0.03%）
US_CASH = rule.CASH_ANNUAL                 # 统一空仓 1.40%
US_COLOR = {"v7": "#1f77b4", "v8": "#bcbd22", "v9": "#17becf", "v10": "#d62728",
            "static": "#111111"}


# ================================================================ 工具
def metrics(nav):
    """-> (engine.metrics, Calmar)。"""
    m = engine.metrics(nav)
    calmar = (m["ann"] / abs(m["mdd"])) if (m["mdd"] is not None and m["mdd"] < -1e-9) else None
    return m, calmar


def rebase(nav):
    nav = nav.dropna()
    return nav / nav.iloc[0]


def fmt_pct(x, nd=2, sign=False):
    if x is None or (isinstance(x, float) and math.isnan(x)):
        return "—"
    return (f"{x:+.{nd}%}" if sign else f"{x:.{nd}%}")


def fmt_cal(x):
    return "—" if x is None else f"{x:.2f}"


def dd_series(nav):
    return nav / nav.cummax() - 1.0


def common_index(*series):
    idx = None
    for s in series:
        idx = s.index if idx is None else idx.intersection(s.index)
    return idx.sort_values()


def _y_log(ax, lo, hi):
    """幅度够大就转对数轴（七腿里科创50/恒生科技是腰斩级，线性轴会把早段压平）。"""
    if lo > 0 and hi / lo > 3.2:
        ax.set_yscale("log")
        ax.set_ylim(lo, hi)
        ax.yaxis.set_major_locator(mticker.LogLocator(
            base=10.0, subs=(1.0, 1.5, 2.0, 3.0, 4.0, 5.0, 6.0, 8.0)))
        ax.yaxis.set_major_formatter(mticker.FuncFormatter(lambda v, _p: f"{v:g}"))
        ax.yaxis.set_minor_formatter(mticker.NullFormatter())
        return True
    ax.set_ylim(lo, hi)
    return False


def _xaxis(ax, idx):
    yrs = (idx[-1] - idx[0]).days / 365.25
    if yrs < 3:
        ax.xaxis.set_major_locator(mdates.MonthLocator(bymonth=(1, 4, 7, 10)))
        ax.xaxis.set_major_formatter(mdates.DateFormatter("%Y-%m"))
    else:
        ax.xaxis.set_major_locator(mdates.YearLocator())
        ax.xaxis.set_major_formatter(mdates.DateFormatter("%Y"))


# ================================================================ 第一组：逐腿 NAV + 仓位
def leg_nav_v1(leg):
    px = ma20.load(leg)
    sig, _ma = ma20.signal(px)
    pos = sig.shift(1).fillna(0).astype(float)
    return px, pos, ma20.run(px, sig)


def leg_nav_v2(leg):
    px = boll.load(leg)
    sig, _mid, _up = boll.signal(px)
    pos = sig.shift(1).fillna(0).astype(float)
    return px, pos, boll.run(px, sig)


def leg_nav_v3(leg):
    px, sig, _s20, _s30 = macd_boll.leg_slice(leg)
    pos = sig.shift(1).fillna(0).astype(float)
    return px, pos, macd_boll.run(px, sig)


def leg_nav_v5(leg, vol_use=True):
    """v5 趋势闸门（默认含自适应波动层；`vol_use=False` = 纯趋势对照）。"""
    px = trend_gate.load(leg)
    state = trend_gate.state_machine(px, leg, VOL_USE=vol_use)
    pos_col = pd.to_numeric(state["state"], errors="coerce").fillna(0.0)
    hold = pos_col.shift(1).fillna(0.0)
    ret = px.pct_change().fillna(0.0)
    r = hold * ret + (1.0 - hold) * trend_gate.CASH_DAILY
    return px, pos_col.shift(1).fillna(0.0), (1.0 + r).cumprod()


LEG_FUNCS = {
    "v1": leg_nav_v1,
    "v2": leg_nav_v2,
    "v3": leg_nav_v3,
    "v5": lambda leg: leg_nav_v5(leg, True),
    "v5p": lambda leg: leg_nav_v5(leg, False),
}


def agg_portfolio(navs):
    """逐腿 NAV（各腿起点 = 1）-> 按带权聚合的组合层 NAV（带权 = band/40）。"""
    idx = common_index(*navs.values())
    port = None
    for leg, nav in navs.items():
        w = rule.LEGS[leg]["band"] / SLEEVE
        s = nav.reindex(idx)
        port = s * w if port is None else port + s * w
    return port.dropna()


def v4_hs300_nav():
    """V4 ERP 股债通道（沪深300×国债）默认 20/50/80 档 -> 归一净值。"""
    df = v4erp.erp_frame(refresh=False)
    bond = v4erp.bond_synthetic(v4erp.load_bond10y())
    pe = df["tr"].astype("float64")
    pb = bond.reindex(pe.index).ffill()
    tgt = df["target"].astype("float64")
    ok = pb.notna()
    pe, pb, tgt = pe[ok], pb[ok], tgt[ok]
    r = v4erp.run(pe, pb, tgt,
                  period_end=v4erp.period_end_flags(pe.index, "M"),
                  tol=v4erp.CFG["rebal_tol"], fee_eq=(0.0, 0.0), fee_bd=(0.0, 0.0))
    return rebase(r["nav"])


def v6_hs300_nav():
    """V6 PE 分位仓位带（沪深300 全收益，每周定档）-> 归一净值。"""
    frame = v6band.load_frame("hs300")
    ind = v6band.indicators(frame, universe="hs300")
    me = v6band.monthly_signals(ind)
    bands_df, _events = v6band.band_series(ind, me)
    sim = v6band.simulate(ind, bands_df)
    return rebase(sim["nav"])


def compute_group_a():
    hs300_px = vdata.otc_series_for_leg("hs300")

    leg_navs, leg_pos, leg_px = {}, {}, {}
    for leg in LEGS:
        leg_navs[leg], leg_pos[leg] = {}, {}
        leg_px[leg] = vdata.otc_series_for_leg(leg)
        for vx in ("v1", "v2", "v3", "v5", "v5p"):
            try:
                _px, pos, nav = LEG_FUNCS[vx](leg)
                leg_navs[leg][vx], leg_pos[leg][vx] = nav, pos
            except Exception as e:  # noqa: BLE001
                print(f"  [WARN] 第一组 {NAMES[leg]}/{vx} 失败: {type(e).__name__}: {e}")

    port_navs = {}
    for vx in ("v1", "v2", "v3", "v5", "v5p"):
        navs = {leg: leg_navs[leg][vx] for leg in LEGS if vx in leg_navs[leg]}
        if not navs:
            continue
        port_navs[vx] = rebase(agg_portfolio(navs))

    special = {}
    for vx, fn in (("v4", v4_hs300_nav), ("v6", v6_hs300_nav)):
        try:
            special[vx] = fn()
        except Exception as e:  # noqa: BLE001
            print(f"  [WARN] 第一组 {vx} 失败: {type(e).__name__}: {e}")

    return dict(hs300_bh=rebase(hs300_px), leg_navs=leg_navs, leg_pos=leg_pos,
                leg_px=leg_px, port_navs=port_navs, special=special)


def leg_window(data, leg):
    """该腿四支时机策略的共同窗口（同尺子比较用）。"""
    nav_by = data["leg_navs"][leg]
    keep = [nav_by[v] for v in TIMING if v in nav_by]
    if not keep:
        return None
    idx = common_index(*keep)
    return idx if len(idx) >= 30 else None


def leg_best(leg, data):
    """逐腿推荐：同窗口年化超额最高者；跑输买入持有 >2pp 则诚实回退到买入持有。"""
    idx = leg_window(data, leg)
    if idx is None:
        return None
    px = data["leg_px"][leg]
    bh = rebase(px).reindex(idx).ffill()
    bhm, _ = metrics(bh)
    rows = []
    for vx in TIMING:
        nav = data["leg_navs"][leg].get(vx)
        if nav is None:
            continue
        r = rebase(nav).reindex(idx).ffill()
        if len(r) < 30:
            continue
        m, cal = metrics(r)
        pos = data["leg_pos"][leg].get(vx)
        expo = float(pos.reindex(idx).ffill().fillna(0).mean()) if pos is not None else None
        rows.append(dict(vx=vx, ann=m["ann"], mdd=m["mdd"], sharpe=m["sharpe"], calmar=cal,
                         vol=m["vol"], edge=(m["ann"] - bhm["ann"]) * 100, exposure=expo))
    if not rows:
        return None
    best = max(rows, key=lambda z: z["edge"])
    rec = best["vx"] if best["edge"] >= -2.0 else "BH"
    return dict(idx=idx, bh=bhm, rows=rows, best=rec)


# ================================================================ 分面图公共件
def _facet_grid(nrows, ncols, figsize, hspace=0.34, wspace=0.17, left=0.062, right=0.972,
                top=0.878, bottom=0.105):
    """每格 = 主图（净值）+ 下方一条窄带（仓位阶梯 + 买卖点），共享 x 轴。

    窄带是为了把 ▲/▼ 从净值曲线上挪走：MA20 这种一年十几笔的策略，
    五个买卖点指标叠在一条线上就是一团墨点，挪到窄带里反而能一眼数清节奏。
    """
    fig = plt.figure(figsize=figsize)
    gs = fig.add_gridspec(nrows * 2, ncols, height_ratios=[3.0, 0.8] * nrows,
                          hspace=hspace, wspace=wspace,
                          left=left, right=right, top=top, bottom=bottom)
    out = []
    for r in range(nrows):
        row = []
        for c in range(ncols):
            ax = fig.add_subplot(gs[2 * r, c])
            axs = fig.add_subplot(gs[2 * r + 1, c], sharex=ax)
            row.append((ax, axs))
        out.append(row)
    return fig, out


def _draw_facet(ax, axs, base, nav, pos, color, lo, hi, bench=None, base_label="买入持有"):
    """画一格：主图 = 基准 + 策略净值；窄带 = 仓位阶梯 + ▲/▼。

    -> (买入次数, 卖出次数)。只统计 |Δ仓位| ≥ `BIG_MOVE`（20pp）的动作。
    """
    ax.plot(base.index, base.to_numpy(), color=C_PRICE, lw=1.0, zorder=2, label=base_label)
    if bench is not None:
        ax.plot(bench.index, bench.to_numpy(), color=C_BENCH, lw=0.9, ls="--",
                alpha=0.40, zorder=1, label="沪深300 基准")
    if nav is not None:
        ax.plot(nav.index, nav.to_numpy(), color=color, lw=1.5, zorder=3, label="策略净值")
    _y_log(ax, lo, hi)
    ax.grid(ls="--", lw=0.6, alpha=0.30)
    ax.set_axisbelow(True)
    ax.tick_params(labelbottom=False, labelsize=8.4)
    for sp in ("top", "right"):
        ax.spines[sp].set_visible(False)

    nb = ns = 0
    if pos is not None:
        p = pos.reindex(base.index).ffill().fillna(0.0)
        v = p.to_numpy()
        axs.fill_between(base.index, 0, v, step="post", color=color, alpha=0.22, lw=0, zorder=2)
        axs.step(base.index, v, where="post", color=color, lw=1.0, alpha=0.9, zorder=3)
        d = p.diff().fillna(0.0).to_numpy()
        b_i = np.where(d >= BIG_MOVE - 1e-12)[0]
        s_i = np.where(d <= -(BIG_MOVE - 1e-12))[0]
        nb, ns = len(b_i), len(s_i)
        if (nb + ns) <= 120:                    # 笔数太多（MA20 十七年 460 笔）只留带宽，画了也是糊成一片
            ms = 3.8 if (nb + ns) > 60 else 5.2
            axs.plot(base.index[b_i], np.full(nb, 1.20), ls="none", marker="^", ms=ms,
                     mfc=C_UP, mec="white", mew=0.4, zorder=4, clip_on=False)
            axs.plot(base.index[s_i], np.full(ns, -0.20), ls="none", marker="v", ms=ms,
                     mfc=C_DOWN, mec="white", mew=0.4, zorder=4, clip_on=False)
    axs.set_ylim(-0.55, 1.55)
    axs.set_yticks([0.0, 1.0])
    axs.set_yticklabels(["空", "满"], fontsize=7.4, color=color)
    axs.tick_params(length=2, pad=1.5)
    axs.grid(ls="--", lw=0.5, alpha=0.25)
    axs.set_axisbelow(True)
    for sp in ("top", "right", "left"):
        axs.spines[sp].set_visible(False)
    return nb, ns


# ================================================================ 第一组：分面图
def plot_leg_group_a(leg, data, info):
    """2×2 分面：一格一个策略，格内只有它自己的买卖点。"""
    idx = info["idx"]
    px = rebase(data["leg_px"][leg].reindex(idx).ffill())
    hsv = data["hs300_bh"].reindex(idx).ffill()
    hsv = hsv / hsv.iloc[0] if len(hsv) else None

    navs = {}
    for vx in TIMING:
        nav = data["leg_navs"][leg].get(vx)
        if nav is None:
            continue
        navs[vx] = rebase(nav.reindex(idx).ffill())

    lo, hi = float(px.min()), float(px.max())
    for s in navs.values():
        lo, hi = min(lo, float(s.min())), max(hi, float(s.max()))
    lo, hi = lo * 0.94, hi * 1.05

    fig, grid = _facet_grid(2, 2, (13.8, 9.8))
    cells = [grid[0][0], grid[0][1], grid[1][0], grid[1][1]]
    for (ax, axs), vx in zip(cells, TIMING):
        s = navs.get(vx)
        if s is None:
            ax.set_visible(False)
            axs.set_visible(False)
            continue
        nb, ns = _draw_facet(ax, axs, px, s, data["leg_pos"][leg].get(vx),
                             TIMING_COLOR[vx], lo, hi,
                             bench=(hsv if leg != "hs300" else None),
                             base_label=f"{NAMES[leg]} 买入持有")
        row = next((r for r in info["rows"] if r["vx"] == vx), None)
        if row is not None:
            ax.set_title(f"{STRAT_NAME[vx]}  年化 {row['ann']:+.2%} · 回撤 {row['mdd']:.2%} · "
                         f"夏普 {row['sharpe']:.2f} · 买{nb}/卖{ns}",
                         fontsize=10.4, loc="left", color=TIMING_COLOR[vx], pad=6)

    for c in (0, 1):
        grid[0][c][0].set_ylabel("净值（起点 = 1）", fontsize=9)
        grid[1][c][0].set_ylabel("净值（起点 = 1）", fontsize=9)
        grid[1][c][1].set_xlabel("成交日 = 信号次日（T+1 净值）", fontsize=9, color="#5b6b7a")
        grid[0][c][1].tick_params(labelbottom=False)

    handles = [
        Line2D([], [], color=C_PRICE, lw=1.0, label=f"{NAMES[leg]} 买入持有"),
        Line2D([], [], color=C_BENCH, lw=0.9, ls="--", alpha=0.6, label="沪深300 基准"),
        Line2D([], [], color="#888", lw=1.0, label="策略净值（每格自己的颜色 = 标题颜色）"),
        Line2D([], [], ls="none", marker="^", ms=7, mfc=C_UP, mec="white",
               label="买点 ▲（窄带上沿）"),
        Line2D([], [], ls="none", marker="v", ms=7, mfc=C_DOWN, mec="white",
               label="卖点 ▼（窄带下沿）"),
        Line2D([], [], color="#888", lw=6, alpha=0.25, label="窄带 = 带内仓位（空/满）"),
    ]
    fig.legend(handles=handles, loc="upper center", bbox_to_anchor=(0.5, 0.945), ncol=6,
               fontsize=8.8, frameon=False, columnspacing=1.1, handletextpad=0.4)

    title_txt = (f"{NAMES[leg]} · 四支时机策略分面（买卖点移出曲线，画在下方仓位窄带里）    "
                 f"{idx[0].date()} ~ {idx[-1].date()}（{(idx[-1] - idx[0]).days / 365.25:.1f} 年）")
    fig.suptitle(title_txt, x=0.045, ha="left", y=0.985, fontsize=13.5,
                 color="#1b2b3a", weight="bold")
    note = ("说明：每格 = 一个策略；上格灰线 = 标的买入持有、彩线 = 该策略净值，"
            "下格窄带 = 带内仓位，▲/▼ 画在窄带上下沿（不再压在净值曲线上）。"
            "单格买卖合计超过 120 笔时（如 MA20 十七年 460 笔）只画带宽，不再画三角。\n"
            "口径：场外复权净值（含分红再投 / 汇率），T 日盘后算信号、T+1 净值成交，空仓按货基计息，零费率。"
            "v5 用 README 默认口径（七腿全套闸门 + 自适应波动层）。")
    fig.text(0.045, 0.012, note, fontsize=8.6, color="#5b6b7a", linespacing=1.55, va="bottom")
    out = os.path.join(OUTDIR, f"_v1v6_leg_{leg}.png")
    fig.savefig(out, dpi=125, facecolor="white")
    plt.close(fig)
    return out


# ================================================================ 综合对比图（净值 + 回撤）
def plot_comprehensive(items, idx, title, sub, out_path, notes, bench=None,
                       figsize=(13.8, 9.2)):
    """`items` = [(key, label, nav)]；上格净值、下格回撤，同窗口同起点归 1。"""
    idx = idx.sort_values()
    if len(idx) < 20:
        raise ValueError("统一窗口为空")

    rows = []
    for key, label, s in items:
        r = rebase(s.reindex(idx).ffill())
        m, cal = metrics(r)
        rows.append(dict(key=key, label=label, nav=r, m=m, calmar=cal))
    rows.sort(key=lambda z: z["m"]["ann"], reverse=True)

    lo = min(float(z["nav"].min()) for z in rows)
    hi = max(float(z["nav"].max()) for z in rows)
    lo, hi = lo * 0.93, hi * 1.06

    fig, (ax, axd) = plt.subplots(
        2, 1, figsize=figsize, sharex=True,
        gridspec_kw=dict(height_ratios=[3.0, 1.15], hspace=0.09))
    for z in rows:
        r, m = z["nav"], z["m"]
        color = (TIMING_COLOR.get(z["key"]) or US_COLOR.get(z["key"])
                 or (C_BENCH if z["key"] == bench else "#333"))
        ls = "--" if z["key"] == bench else "-"
        lw = 2.0 if z["key"] == bench else 1.35
        ax.plot(r.index, r.to_numpy(), color=color, lw=lw, ls=ls, zorder=3,
                label=f"{z['label']}  {m['ann']:+.2%}/年 · 回撤 {m['mdd']:.1%} · 夏普 {m['sharpe']:.2f}")
        _y_log(ax, lo, hi)
        dd = dd_series(r)
        axd.plot(dd.index, dd.to_numpy(), color=color, lw=1.0, alpha=0.9)
        axd.fill_between(dd.index, dd.to_numpy(), 0, color=color, alpha=0.06, lw=0)

    ax.grid(ls="--", lw=0.6, alpha=0.32)
    ax.set_axisbelow(True)
    ax.set_ylabel("净值（统一窗口起点 = 1）", fontsize=10)
    ax.legend(loc="lower left", bbox_to_anchor=(0.0, 1.005, 1.0, 0.10), ncol=2,
              mode="expand", fontsize=8.4, frameon=False, columnspacing=1.2,
              handletextpad=0.5, borderaxespad=0.2)
    for sp in ("top", "right"):
        ax.spines[sp].set_visible(False)

    axd.axhline(0, color="#999", lw=0.8)
    axd.grid(ls="--", lw=0.55, alpha=0.28)
    axd.set_axisbelow(True)
    axd.set_ylabel("回撤", fontsize=10)
    axd.yaxis.set_major_formatter(mticker.FuncFormatter(lambda v, _p: f"{v:.0%}"))
    for sp in ("top", "right"):
        axd.spines[sp].set_visible(False)
    _xaxis(axd, idx)
    axd.tick_params(labelsize=8.6)

    fig.suptitle(title, x=0.055, ha="left", y=0.988, va="top", fontsize=14.5,
                 color="#1b2b3a", weight="bold")
    fig.text(0.055, 0.945, sub, ha="left", va="top", fontsize=10, color="#3d4f5f")
    txt = fig.text(0.055, 0.014, "\n".join(notes), fontsize=8.6, color="#5b6b7a",
                   linespacing=1.55, va="bottom")
    fig.subplots_adjust(left=0.065, right=0.978, top=0.805, bottom=0.20)
    # 先画一次量出图注实际高度，再把坐标区抬到它上沿之上（行数变化时不会压到日期刻度）
    fig.canvas.draw()
    bb = txt.get_window_extent(fig.canvas.get_renderer()).transformed(
        fig.transFigure.inverted())
    fig.subplots_adjust(bottom=bb.y1 + 0.045)
    fig.savefig(out_path, dpi=125, facecolor="white")
    plt.close(fig)
    return out_path, idx, rows


# ================================================================ 逐腿叠加对比（单标的口径）
def plot_leg_overlay(leg, data, info):
    """单标的口径：该腿买入持有（基准）+ 四支时机策略，上格净值 / 下格回撤。

    版式与 `_v1v6_comprehensive.png`（组合层）完全一致，只是把「七腿组合」换成「这一条腿」。
    它回答的是**这只标的上，哪支策略真的赢过一直拿着** —— 组合层那张回答的是「七腿拼起来谁强」，
    两者不能互相替代：单腿图的虚线是该标的买入持有，这条线就是每支策略的及格线。
    """
    idx = info["idx"]
    bh = rebase(data["leg_px"][leg].reindex(idx).ffill())
    items = []
    for vx in ("v1", "v2", "v3", "v5", "v5p"):
        nav = data["leg_navs"][leg].get(vx)
        if nav is None:
            continue
        items.append((vx, STRAT_NAME[vx], rebase(nav.reindex(idx).ffill())))
    items.append(("bench_leg", f"{NAMES[leg]} 买入持有（基准）", bh))

    yrs = (idx[-1] - idx[0]).days / 365.25
    notes = [
        "上格 = 净值（统一窗口起点归 1），下格 = 回撤；虚线 = 该标的买入持有，"
        "这条虚线就是及格线：落在它下面的策略，择时还不如一直拿着。",
        "四支策略都跑在同一份场外复权净值上，T 日盘后算信号、T+1 净值成交，空仓按货基计息，零费率。",
        "v5 取 README 默认口径（含自适应波动层）；v5p 是关掉波动层的纯趋势对照，用来隔离波动层的贡献。",
    ]
    if leg == "hs300":
        notes.append("v4（ERP 股债通道）/ v6（PE 分位带）是沪深300 单标的方法论，"
                     "与逐腿择时不同层，见 `_v1v6_comprehensive.png`，不在本图。")
    else:
        notes.append("v4（ERP 股债通道）/ v6（PE 分位带）只对沪深300 成立，套不到这条腿，故不在本图。")
    if leg in ("a500", "hs300"):
        notes.append("注意：A500 与沪深300 的场外序列在 2025-03-27 之前是同一只 `000051`，两图前段会高度相似。")

    return plot_comprehensive(
        items, idx,
        f"{NAMES[leg]} · 各时机策略对比（单标的口径）",
        f"统一窗口 {idx[0].date()} ~ {idx[-1].date()}（{yrs:.1f} 年）· "
        "上格净值 / 下格回撤 · 虚线 = 该标的买入持有",
        os.path.join(OUTDIR, f"_v1v6_overlay_{leg}.png"),
        notes, bench="bench_leg")[0]


US_COLOR = {"v7": "#1f77b4", "v8": "#bcbd22", "v9": "#17becf", "v10": "#d62728",
            "static": "#111111"}


# ================================================================ 第二组：v7–v10（ndx/spx）
def align_us(navs, sts):
    """两腿净值的日期并集交集对齐（`portfolio()` 内部不重排 rets，必须先对齐再喂）。"""
    idx = common_index(*navs.values())
    out_navs = {k: v.reindex(idx).ffill() for k, v in navs.items()}
    out_sts = {k: v.astype(float).reindex(idx).ffill().fillna(1.0) for k, v in sts.items()}
    return out_navs, out_sts


def compute_group_b():
    """-> {vx: dict(navs={leg: nav}, states={leg: st}, port=Series, expo=float)}。"""
    out = {}
    for vx, mod, label, _c in US_MODS:
        navs, sts = {}, {}
        for leg in US_LEGS:
            nav, st, _dec = mod.build(leg)
            navs[leg] = nav
            sts[leg] = st.astype(float)
        try:
            nv, st = align_us(navs, sts)
            port, expo = mod.portfolio(nv, st, cost=US_COST, cash=US_CASH)
        except Exception as e:  # noqa: BLE001
            print(f"  [WARN] 第二组 {vx} 组合失败: {type(e).__name__}: {e}")
            port, expo = None, None
        out[vx] = dict(label=label, mod=mod, navs=navs, states=sts, port=port, expo=expo)
    return out


def static_us_port():
    """两腿等权买入持有（第二组的基准）。"""
    navs, sts = {}, {}
    for leg in US_LEGS:
        nav = vdata.series_for_leg(leg).dropna()
        navs[leg] = nav
        sts[leg] = pd.Series(1.0, index=nav.index)
    nv, st = align_us(navs, sts)
    port, _expo = v10vl.portfolio(nv, st, cost=0.0, cash=US_CASH)
    return navs, port


def leg_metrics_us(grp, leg):
    """单腿：策略净值 + 同腿买入持有 的年化/回撤/夏普。"""
    nav = grp["navs"][leg]
    nv = grp["mod"].run_leg(grp["states"][leg], nav, cost=US_COST, cash=US_CASH)
    hold = grp["mod"].run_leg(pd.Series(1.0, index=nav.index), nav, cost=0.0, cash=US_CASH)
    return nv, hold


def us_actions(grp, leg):
    """仓位变化（成交日）-> 只留 |Δ| ≥ 20pp 的大动作。"""
    st = grp["states"][leg].astype(float)
    d = st.diff().fillna(0.0)
    big = d.abs() >= BIG_MOVE - 1e-12
    return int(big.sum()), int((d.abs() > 1e-12).sum())


def plot_leg_group_b(leg, grp_all):
    """2×2 分面：一格一个变种（价格 + 自己的净值 + 仓位底色 + 仅 ≥20pp 的大动作）。"""
    nav0 = grp_all["v7"]["navs"][leg]
    idx = nav0.index
    px = rebase(nav0)
    lo, hi = float(px.min()), float(px.max())

    series = {}
    for vx, _mod, _label, _c in US_MODS:
        g = grp_all[vx]
        nv, hold = leg_metrics_us(g, leg)
        series[vx] = dict(nav=rebase(nv), hold=rebase(hold),
                          st=g["states"][leg].astype(float), m=engine.metrics(nv))
        lo = min(lo, float(series[vx]["nav"].min()))
        hi = max(hi, float(series[vx]["nav"].max()))
    lo, hi = lo * 0.94, hi * 1.05

    fig, grid = _facet_grid(2, 2, (13.8, 9.8))
    cells = [grid[0][0], grid[0][1], grid[1][0], grid[1][1]]
    for (ax, axs), (vx, _mod, label, color) in zip(cells, US_MODS):
        s = series[vx]
        nb, ns = _draw_facet(ax, axs, s["hold"], s["nav"], s["st"], color, lo, hi,
                             base_label="恒持满仓（买入持有对照）")
        nall = int((s["st"].diff().fillna(0.0).abs() > 1e-12).sum())
        m = s["m"]
        ax.set_title(f"{label}  年化 {m['ann']:+.2%} · 回撤 {m['mdd']:.2%} · "
                     f"夏普 {m['sharpe']:.2f} · 调仓 {nall}（标 ≥20pp 的 {nb + ns} 次）",
                     fontsize=10.4, loc="left", color=color, pad=6)

    for c in (0, 1):
        grid[0][c][0].set_ylabel("净值（起点 = 1）", fontsize=9)
        grid[1][c][0].set_ylabel("净值（起点 = 1）", fontsize=9)
        grid[1][c][1].set_xlabel("成交日（各变种自己的 T+1/T+2 纪律）", fontsize=9, color="#5b6b7a")
        grid[0][c][1].tick_params(labelbottom=False)

    handles = [
        Line2D([], [], color=C_NAV, lw=1.5, label="变种净值（每格自己的颜色 = 标题颜色）"),
        Line2D([], [], color=C_PRICE, lw=1.0, label="恒持满仓（买入持有对照）"),
        Line2D([], [], ls="none", marker="^", ms=7, mfc=C_UP, mec="white",
               label="加仓 ≥20pp ▲（窄带上沿）"),
        Line2D([], [], ls="none", marker="v", ms=7, mfc=C_DOWN, mec="white",
               label="减仓 ≥20pp ▼（窄带下沿）"),
        Line2D([], [], color="#888", lw=6, alpha=0.25, label="窄带 = 带内仓位（0~100%）"),
    ]
    fig.legend(handles=handles, loc="upper center", bbox_to_anchor=(0.5, 0.945), ncol=5,
               fontsize=8.8, frameon=False, columnspacing=1.1, handletextpad=0.4)

    fig.suptitle(f"{NAMES[leg]} · 美股两腿四变种分面（仓位窄带在下方，买卖点不再压曲线）    "
                 f"{idx[0].date()} ~ {idx[-1].date()}（{(idx[-1] - idx[0]).days / 365.25:.1f} 年）",
                 x=0.045, ha="left", y=0.985, fontsize=13.5, color="#1b2b3a", weight="bold")
    note = ("说明：每格 = 一个变种（同池：只有这两条腿），窄带 = 该变种的带内仓位（0~100%，"
            "连续仓位策略一眼能看出「危机里降到多少」），▲/▼ 只标 ≥20pp 的调仓方向。\n"
            f"同尺子：四支统一按单边 {US_COST:.2%} · 空仓 {US_CASH:.2%} 计息（v9 自身默认是 0.03% / 2.0%，"
            "故本图 v9 读数略低于它自己的 README 表）。")
    fig.text(0.045, 0.012, note, fontsize=8.6, color="#5b6b7a", linespacing=1.55, va="bottom")
    out = os.path.join(OUTDIR, f"_v7v10_leg_{leg}.png")
    fig.savefig(out, dpi=125, facecolor="white")
    plt.close(fig)
    return out


# ================================================================ 主流程
def main():
    os.makedirs(OUTDIR, exist_ok=True)
    print("第一组：v1–v6 × 七腿（场外复权净值，T+1 成交，零费率）…")
    a = compute_group_a()

    print("第二组：v7–v10 × 纳指/标普（统一 0.10% / 1.40%）…")
    b = compute_group_b()
    us_navs, us_static = static_us_port()

    # ---- 第一组：逐腿最优 + 分面图
    print("第一组：逐腿分面图…")
    leg_pngs_a, leg_info = {}, {}
    for leg in LEGS:
        info = leg_best(leg, a)
        if info is None:
            print(f"  [WARN] {NAMES[leg]} 窗口不足，跳过")
            continue
        leg_info[leg] = info
        try:
            leg_pngs_a[leg] = plot_leg_group_a(leg, a, info)
        except Exception as e:  # noqa: BLE001
            print(f"  [WARN] 分面图 {NAMES[leg]} 失败: {type(e).__name__}: {e}")

    # ---- 第一组：逐腿叠加对比（版式同沪深300 的组合层对比图）
    print("第一组：逐腿叠加对比图（单标的口径）…")
    leg_overlay = {}
    for leg in LEGS:
        info = leg_info.get(leg)
        if info is None:
            continue
        try:
            leg_overlay[leg] = plot_leg_overlay(leg, a, info)
        except Exception as e:  # noqa: BLE001
            print(f"  [WARN] 叠加对比图 {NAMES[leg]} 失败: {type(e).__name__}: {e}")

    # ---- 第一组：综合对比
    print("第一组：综合对比图…")
    items_a = []
    for vx in ("v1", "v2", "v3", "v5", "v5p"):
        if vx in a["port_navs"]:
            items_a.append((vx, STRAT_NAME[vx] + ("（七腿组合）" if vx != "v5p" else "（七腿组合·对照）"),
                            a["port_navs"][vx]))
    for vx in ("v4", "v6"):
        if vx in a["special"]:
            items_a.append((vx, STRAT_NAME[vx] + "（沪深300方法论）", a["special"][vx]))
    items_a.append(("bench_a", "沪深300 买入持有（基准）", a["hs300_bh"]))

    idx_a = common_index(*[s for _k, _l, s in items_a])
    png_a, idx_a, rows_a = plot_comprehensive(
        items_a, idx_a,
        "第一次综合对比 · v1–v6（七腿时机 + 沪深300 方法论）",
        f"统一窗口 {idx_a[0].date()} ~ {idx_a[-1].date()}"
        f"（{(idx_a[-1] - idx_a[0]).days / 365.25:.1f} 年，受 v4/v6 起点约束）· "
        "上格净值 / 下格回撤 · 基准 = 沪深300 买入持有",
        os.path.join(OUTDIR, "_v1v6_comprehensive.png"),
        notes=[
            "上格 = 净值（统一窗口起点归 1），下格 = 回撤；虚线 = 沪深300 买入持有基准。",
            "v1/v2/v3/v5 是七腿组合（按 rule 目标带权重：带权 = band/40）；v4/v6 是沪深300 单标的方法论，"
            "与基准同池不同层，只作方法论对照。",
            "v5 用 README 默认口径（七腿全套闸门 + 自适应波动层）；v5p 是关掉波动层的纯趋势对照。",
            "零费率口径；含申赎费时结论会变（v1 MA20 年笔数最高，见各变种 README 成本节）。",
        ],
        bench="bench_a")

    # ---- 第二组：逐腿 + 综合对比
    print("第二组：逐腿分面图…")
    leg_pngs_b = {}
    for leg in US_LEGS:
        try:
            leg_pngs_b[leg] = plot_leg_group_b(leg, b)
        except Exception as e:  # noqa: BLE001
            print(f"  [WARN] 分面图 {NAMES[leg]} 失败: {type(e).__name__}: {e}")

    print("第二组：综合对比图…")
    items_b = [(vx, label + "（两腿 50/50）", b[vx]["port"])
               for vx, _m, label, _c in US_MODS if b[vx]["port"] is not None]
    items_b.append(("static", "静态 50/50 买入持有（基准）", us_static))
    idx_b = common_index(*[s for _k, _l, s in items_b])
    png_b, idx_b, rows_b = plot_comprehensive(
        items_b, idx_b,
        "第二次综合对比 · v7–v10（仅纳指100 + 标普500）",
        f"统一窗口 {idx_b[0].date()} ~ {idx_b[-1].date()}"
        f"（{(idx_b[-1] - idx_b[0]).days / 365.25:.1f} 年）· "
        "上格净值 / 下格回撤 · 基准 = 静态 50/50 买入持有",
        os.path.join(OUTDIR, "_v7v10_comprehensive.png"),
        bench="static",
        notes=[
            "上格 = 净值（统一窗口起点归 1），下格 = 回撤；虚线 = 静态 50/50 买入持有基准。",
            f"同尺子：四支统一单边 {US_COST:.2%}、空仓 {US_CASH:.2%}（v9 自身默认 0.03% / 2.0%，故此处读数略低）。",
            "四支同池（ndx + spx 各 10pp 带）才有可比性：v7 判方向（月频）、v8 判方向（日频）、"
            "v9/v10 只调风险预算（v9 月频、v10 日频）。",
            "模拟结果，非投资建议。",
        ])

    # ---- 指标汇总
    def group_a_rows(idx):
        out = []
        for vx in ("v1", "v2", "v3", "v5", "v5p"):
            if vx not in a["port_navs"]:
                continue
            r = rebase(a["port_navs"][vx]).reindex(idx).ffill()
            if len(r) < 30:
                continue
            m, cal = metrics(r)
            out.append(dict(vx=vx, kind="七腿组合", ann=m["ann"], mdd=m["mdd"],
                            sharpe=m["sharpe"], calmar=cal, vol=m["vol"]))
        for vx in ("v4", "v6"):
            if vx not in a["special"]:
                continue
            r = rebase(a["special"][vx]).reindex(idx).ffill()
            if len(r) < 30:
                continue
            m, cal = metrics(r)
            out.append(dict(vx=vx, kind="沪深300方法论", ann=m["ann"], mdd=m["mdd"],
                            sharpe=m["sharpe"], calmar=cal, vol=m["vol"]))
        return out

    rows_a_tbl = group_a_rows(idx_a)
    bh_m, bh_cal = metrics(rebase(a["hs300_bh"]).reindex(idx_a).ffill())

    rows_b_tbl = []
    for z in rows_b:
        key = z["key"]
        expo = b[key]["expo"] if key in b else 1.0
        rows_b_tbl.append(dict(vx=key, kind="两腿50/50", ann=z["m"]["ann"], mdd=z["m"]["mdd"],
                               sharpe=z["m"]["sharpe"], calmar=z["calmar"], vol=z["m"]["vol"],
                               exposure=expo))
    us_rows = []
    for vx, _mod, label, _c in US_MODS:
        for leg in US_LEGS:
            nv, hold = leg_metrics_us(b[vx], leg)
            m, cal = metrics(nv)
            mh, calh = metrics(hold)
            nbig, nall = us_actions(b[vx], leg)
            us_rows.append(dict(vx=vx, label=label, leg=leg, name=NAMES[leg],
                                ann=m["ann"], mdd=m["mdd"], sharpe=m["sharpe"], calmar=cal,
                                vol=m["vol"], bh_ann=mh["ann"], bh_mdd=mh["mdd"],
                                bh_sharpe=mh["sharpe"], trades=nall, big=nbig,
                                exposure=float(b[vx]["states"][leg].mean())))

    # ---- 写报告
    L = [
        "# 波动资产 · 变种横向对比（两次综合对比 · v1–v10）",
        "",
        "> 家族有 10 个变种，但**标的池不是同一个**，所以拆成两次综合对比：",
        "> **第一组 v1–v6 × 全部七腿**、**第二组 v7–v10 × 仅纳指100 + 标普500**。",
        "> 价格 = 场外**复权净值**（含分红再投 / 汇率 / 跟踪误差）；成交 = T 日盘后算信号、T+1 净值成交；"
        "空仓按货基 1.40% 计息。",
        "> 第一组零费率（与各变种回测主表一致）；第二组统一单边 0.10%（与各变种 README 的「同尺子」表一致）。",
        "",
        "## 1. 第一次综合对比 · v1–v6（七腿时机 + 沪深300 方法论）",
        "",
        f"![v1–v6 综合对比](_v1v6_comprehensive.png)",
        "",
        f"统一窗口 **{idx_a[0].date()} ~ {idx_a[-1].date()}**"
        f"（{(idx_a[-1] - idx_a[0]).days / 365.25:.1f} 年；受 v4/v6 起点约束）。"
        "上格净值、下格回撤，虚线 = 沪深300 买入持有基准。",
        "",
        "| 策略 | 类型 | 年化 | 最大回撤 | 夏普 | Calmar | 波动 | vs 沪深300 年化差 |",
        "|---|---|---:|---:|---:|---:|---:|---:|",
    ]
    for row in sorted(rows_a_tbl, key=lambda z: z["ann"], reverse=True):
        edge = (row["ann"] - bh_m["ann"]) * 100
        L.append(f"| {STRAT_NAME[row['vx']]} | {row['kind']} | {fmt_pct(row['ann'])} | "
                 f"{fmt_pct(row['mdd'])} | {row['sharpe']:.2f} | {fmt_cal(row['calmar'])} | "
                 f"{fmt_pct(row['vol'])} | **{edge:+.2f}pp** |")
    L.append(f"| 沪深300 买入持有（基准） | — | {fmt_pct(bh_m['ann'])} | {fmt_pct(bh_m['mdd'])} | "
             f"{bh_m['sharpe']:.2f} | {fmt_cal(bh_cal)} | {fmt_pct(bh_m['vol'])} | — |")
    L += [
        "",
        "> v1/v2/v3/v5 是**七腿组合**（按 `rule` 目标带权重：带权 = band/40，满仓 = 40pp）；"
        "v4/v6 是**沪深300 单标的方法论**，与基准同池、不同层，只作方法论对照，不与七腿组合比排名。",
        "> v5 取 README 默认口径（七腿全套闸门 + 自适应波动层）；**v5p = 关掉波动层的纯趋势对照**，"
        "用来隔离「波动层到底贡献了什么」。",
        "",
        "### 1.1 逐腿最优策略（同窗口内比较）",
        "",
        "| 标的 | 买入持有 年化/回撤/夏普 | 推荐策略 | "
        + " | ".join(STRAT_NAME[v] for v in TIMING) + " |",
        "|---|---:|---:|" + "---:|" * len(TIMING),
    ]
    for leg in LEGS:
        info = leg_info.get(leg)
        if not info:
            continue
        cells = []
        for vx in TIMING:
            row = next((x for x in info["rows"] if x["vx"] == vx), None)
            cells.append("—" if row is None else
                         f"{fmt_pct(row['ann'])}/{fmt_pct(row['mdd'])}/"
                         f"{row['sharpe']:.2f}({row['edge']:+.1f})")
        L.append(f"| {NAMES[leg]} | {fmt_pct(info['bh']['ann'])}/{fmt_pct(info['bh']['mdd'])}/"
                 f"{info['bh']['sharpe']:.2f} | **{STRAT_NAME[info['best']]}** | "
                 + " | ".join(cells) + " |")
    L += [
        "",
        "> 单元格 = 年化 / 最大回撤 / 夏普（括号内 = 年化超额 pp vs 买入持有），全部为**同窗口**口径。",
        "> 推荐 = v1/v2/v3/v5 中同窗口年化超额最高者；若最好的时机策略仍跑输买入持有 >2pp，"
        "回退到**买入持有**（典型如美股长牛，减仓纯拖累）。",
        "> ⚠ A500 与沪深300 两列互为镜像（2025-03-27 前两条腿场外序列同源自 `000051`），不是两份独立证据。",
        "",
        "### 1.2 逐腿策略对比图（单标的口径 · 版式同沪深300 那张）",
        "",
        "每张图 = 一条腿：上格净值（该腿买入持有 = 虚线基准）+ 四支时机策略，下格回撤。",
        "与 §1 组合层图的区别在及格线 —— 这里的基准是**该标的自己**，所以能直接看出"
        "「这条腿上，择时到底赢没赢过一直拿着」。",
        "",
    ]
    for leg in LEGS:
        if leg in leg_overlay:
            L.append(f"![{NAMES[leg]} 各时机策略对比]({os.path.basename(leg_overlay[leg])})")
            L.append("")
    L += [
        "> v4/v6 只对沪深300 成立（ERP 股债通道、PE 分位带），不进这些图；"
        "要看 v4/v6 请回 §1 的组合层对比图。",
        "> 判读：策略线在虚线之上 = 该腿存在时机红利；全线在虚线之下 = 这条腿适合买入持有"
        "（典型如纳指/标普的长牛段）。",
        "",
        "## 2. 第二次综合对比 · v7–v10（仅纳指100 + 标普500）",
        "",
        "![v7–v10 综合对比](_v7v10_comprehensive.png)",
        "",
        f"统一窗口 **{idx_b[0].date()} ~ {idx_b[-1].date()}**"
        f"（{(idx_b[-1] - idx_b[0]).days / 365.25:.1f} 年）。上格净值、下格回撤，"
        "虚线 = 静态 50/50 买入持有基准。",
        "",
        "| 组合（两腿 50/50） | 年化 | 最大回撤 | 夏普 | Calmar | 波动 | 平均仓位 | vs 静态 年化差 |",
        "|---|---:|---:|---:|---:|---:|---:|---:|",
    ]
    st_ann = next((z["ann"] for z in rows_b_tbl if z["vx"] == "static"), None)
    for row in sorted(rows_b_tbl, key=lambda z: z["ann"], reverse=True):
        label = ("静态 50/50 买入持有（基准）" if row["vx"] == "static"
                 else next(lb for vx, _m, lb, _c in US_MODS if vx == row["vx"]))
        edge = (row["ann"] - st_ann) * 100 if st_ann is not None else float("nan")
        expo_txt = "—" if row["exposure"] is None else f"{row['exposure']:.0%}"
        L.append(f"| {label} | {fmt_pct(row['ann'])} | {fmt_pct(row['mdd'])} | "
                 f"{row['sharpe']:.2f} | {fmt_cal(row['calmar'])} | {fmt_pct(row['vol'])} | "
                 f"{expo_txt} | **{edge:+.2f}pp** |")
    L += [
        "",
        "### 2.1 逐腿（同池四支变种）",
        "",
        "| 腿 | 变种 | 年化 | 最大回撤 | 夏普 | Calmar | 买入持有 年化/回撤/夏普 | 年化差 | 调仓 总数(≥20pp) | 平均仓位 |",
        "|---|---|---:|---:|---:|---:|---:|---:|---:|---:|",
    ]
    for row in sorted(us_rows, key=lambda z: (z["leg"], -z["ann"])):
        L.append(f"| {row['name']} | {row['label']} | {fmt_pct(row['ann'])} | {fmt_pct(row['mdd'])} | "
                 f"{row['sharpe']:.2f} | {fmt_cal(row['calmar'])} | "
                 f"{fmt_pct(row['bh_ann'])}/{fmt_pct(row['bh_mdd'])}/{row['bh_sharpe']:.2f} | "
                 f"{(row['ann'] - row['bh_ann']) * 100:+.2f}pp | "
                 f"{row['trades']}({row['big']}) | {row['exposure']:.0%} |")
    L += [
        "",
        "> 同尺子：四支统一单边 0.10% / 空仓 1.40%（v9 自身默认 0.03% / 2.0%，故本表 v9 读数略低于它自己的 README 表）。",
        "> 四支同池才有可比性：v7 判方向（月频，2 月确认）、v8 判方向（日频，教科书基线）、"
        "v9/v10 只调风险预算（v9 月频 + 月中补丁、v10 日频）。",
        "> 「调仓(≥20pp)」= 总调仓次数（括号内 = 其中仓位变动 ≥20pp 的大动作数）。",
        "",
        "## 3. 跨组结论",
        "",
        "### 3.1 七腿（v1–v6）",
        "",
    ]
    best_by_leg = {leg: STRAT_NAME[info["best"]] for leg, info in leg_info.items()}
    bh_legs = [NAMES[leg] for leg, info in leg_info.items() if info["best"] == "BH"]
    L.append(f"- **逐腿推荐**：" + "、".join(f"{NAMES[k]}→{v}" for k, v in best_by_leg.items()) + "。")
    if bh_legs:
        L.append(f"- **回退到买入持有**：{' / '.join(bh_legs)} —— 长牛里任何减仓都是纯拖累，"
                 "时机策略的年化超额为负。")
    L += [
        "- **A 股宽基（A500 / 沪深300）**：BOLL 突破（v2）通常最稳 —— 年化超额为正且回撤压得最狠。",
        "- **港股（恒生科技）与科创50**：波动最大、时机红利最高（超额两位数 pp 的常客），但绝对回撤仍大。",
        "- **v4 / v6 不是择时**：它们是「沪深300 与国债之间摆多少」「这笔钱最多投几成」，"
        "只应与沪深300 基准对照，不要拿去和七腿组合比排名。",
        "",
        "### 3.2 美股两腿（v7–v10）",
        "",
    ]
    us_rank = sorted([z for z in rows_b_tbl if z["vx"] != "static"],
                     key=lambda z: z["sharpe"], reverse=True)
    if us_rank:
        L.append("- **夏普排序**：" + " > ".join(
            f"{next(lb for vx, _m, lb, _c in US_MODS if vx == z['vx'])} {z['sharpe']:.2f}"
            for z in us_rank) + "（基准静态 "
            + f"{next((z['sharpe'] for z in rows_b_tbl if z['vx'] == 'static'), float('nan')):.2f}）。")
    L += [
        "- **年化排序通常与回撤排序相反**：降风险换来的平滑就是「牛市持续少赚」，看下格回撤带才公平。",
        "- **频率决定成本**：v8/v10 日频换手最高，v7/v9 月频换手低 —— 场外 A 类费率下这一条能吃掉半年的超额。",
        "- **2020 型 5 周急跌是月频策略的结构性缺口**，v9/v10 靠月中/日频补丁才补得上；"
        "2000-2002 与 2008 不在 QDII 样本内，结论对下一次泡沫破裂属外推"
        "（长样本证据见 `research/probe_us_gate_vs_vt/_probe_us_gate_vs_vt.md`、"
        "`research/probe_published_us/_probe_published_us.md`）。",
        "",
        "## 4. 图表",
        "",
    ]
    for leg in LEGS:
        if leg in leg_pngs_a:
            L.append(f"- 第一组分面 · {NAMES[leg]}（v1/v2/v3/v5）：`{os.path.basename(leg_pngs_a[leg])}`")
    L.append(f"- 第一组综合对比（组合层 · 净值 + 回撤）：`{os.path.basename(png_a)}`")
    for leg in LEGS:
        if leg in leg_overlay:
            L.append(f"- 单腿策略对比 · {NAMES[leg]}：`{os.path.basename(leg_overlay[leg])}`")
    for leg in US_LEGS:
        if leg in leg_pngs_b:
            L.append(f"- 第二组分面 · {NAMES[leg]}（v7/v8/v9/v10）：`{os.path.basename(leg_pngs_b[leg])}`")
    L.append(f"- 第二组综合对比（净值 + 回撤）：`{os.path.basename(png_b)}`")
    L += [
        "",
        "> 模拟结果，非投资建议。",
    ]

    md_path = os.path.join(OUTDIR, "_v1v10_compare.md")
    with open(md_path, "w", encoding="utf-8") as f:
        f.write("\n".join(L))

    # ---- 机读结果
    json_path = os.path.join(OUTDIR, "_v1v10_compare.jsonl")
    with open(json_path, "w", encoding="utf-8") as f:
        for leg in LEGS:
            info = leg_info.get(leg)
            if not info:
                continue
            f.write(json.dumps(dict(group="v1v6", leg=leg, name=NAMES[leg],
                                    window=[str(info["idx"][0].date()), str(info["idx"][-1].date())],
                                    bh_ann=info["bh"]["ann"], bh_mdd=info["bh"]["mdd"],
                                    bh_sharpe=info["bh"]["sharpe"], best=info["best"],
                                    candidates=info["rows"]),
                               ensure_ascii=False, default=str) + "\n")
        f.write(json.dumps(dict(group="v1v6", comprehensive=rows_a_tbl,
                                hs300_bh=dict(ann=bh_m["ann"], mdd=bh_m["mdd"],
                                              sharpe=bh_m["sharpe"], calmar=bh_cal)),
                           ensure_ascii=False, default=str) + "\n")
        f.write(json.dumps(dict(group="v7v10", comprehensive=rows_b_tbl,
                                window=[str(idx_b[0].date()), str(idx_b[-1].date())]),
                           ensure_ascii=False, default=str) + "\n")
        for row in us_rows:
            f.write(json.dumps(dict(group="v7v10", **row), ensure_ascii=False,
                               default=str) + "\n")

    print("报告:", md_path)
    print("结果:", json_path)
    print("图  :", [os.path.basename(p) for p in leg_pngs_a.values()]
          + [os.path.basename(p) for p in leg_overlay.values()]
          + [os.path.basename(png_a)]
          + [os.path.basename(p) for p in leg_pngs_b.values()]
          + [os.path.basename(png_b)])
    print("\n第一组逐腿推荐：")
    for leg, info in leg_info.items():
        print(f"  {NAMES[leg]:8s} -> {STRAT_NAME[info['best']]}")
    print("\n第二组组合层（两腿 50/50）：")
    for row in sorted(rows_b_tbl, key=lambda z: z["ann"], reverse=True):
        print(f"  {row['vx']:7s} {row['ann']:+.2%}/年  回撤 {row['mdd']:+.2%}  夏普 {row['sharpe']:.2f}")


if __name__ == "__main__":
    main()
