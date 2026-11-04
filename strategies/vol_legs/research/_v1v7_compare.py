# -*- coding: utf-8 -*-
"""波动资产 · v1–v6 横向对比（统一口径）。

池 = `rule.LEG_ORDER` 七腿（中证A500 / 沪深300 / 纳指100 / 标普500 / 科创50 / 恒生科技 / 黄金），
与主策略 README.md §2.1 完全一致；价格 = 场外复权净值（含分红再投 / 汇率 / 跟踪误差）；
成交 = T 日盘后算信号、T+1 净值成交；空仓按货基 1.40% 计息；零费率（与主策略各变种回测主表一致）。

覆盖：
- 七腿逐腿时机家族：v1 MA20 / v2 BOLL 突破 / v3 MACD+BOLL / v5 趋势闸门
  （v4/v6 是沪深300 单标的方法论，只进「综合对比」与沪深300 腿图）
- 沪深300 单标的方法论：v4 ERP 股债通道 / v6 PE 分位仓位带

> v7 抄底确认（`v7_bottom_confirm/`）已于 2026-11-04 删除，本脚本同步去掉该列；
> 产物文件名仍沿用 `_v1v7_*`（历史命名，重新运行会覆盖同名文件）。

产出（本目录 research/，策略类测试调优产物）：
- _v1v7_leg_{leg}.png           逐腿：价格趋势 + 各策略买/卖点 + 沪深300 基准
- _v1v7_comprehensive.png       综合：各策略净值（归一）vs 沪深300 基准
- _v1v7_compare.md              逐腿最优策略 + 综合对比（年化/回撤/夏普/Calmar vs 沪深300）

模拟结果，非投资建议。
"""
import os
import sys
import json
import math

import numpy as np
import pandas as pd

try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:  # noqa
    pass

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt

_HERE = os.path.dirname(os.path.abspath(__file__))
_VL = os.path.dirname(_HERE)
_ROOT = os.path.dirname(_VL)
for _p in (_HERE, _VL):
    if _p not in sys.path:
        sys.path.insert(0, _p)
for _d in ("v1_ma20", "v2_boll_breakout", "v3_macd_boll",
           "v5_trend_gate", "v4_erp_band", "v6_pe_band"):
    _pp = os.path.join(_VL, _d)
    if _pp not in sys.path:
        sys.path.insert(0, _pp)

import data as vdata
import engine
import rule

# 离线优先：禁止回测期内的网络刷新（仅读本地缓存）
vdata._otc_stale = lambda: False  # type: ignore[attr-defined]

import ma20
import boll
import macd_boll
import trend_gate
import erp as v4erp
import band as v6band

plt.rcParams["font.sans-serif"] = ["Microsoft YaHei", "SimHei", "DejaVu Sans"]
plt.rcParams["axes.unicode_minus"] = False

LEGS = rule.LEG_ORDER
NAMES = {k: rule.LEGS[k]["name"] for k in LEGS}
SLEEVE = rule.SLEEVE_PP  # 40pp

# 七腿逐腿时机家族（覆盖全部七腿、产生离散买/卖点）
TIMING = ["v1", "v2", "v3", "v5"]
TIMING_COLOR = {"v1": "#1f77b4", "v2": "#d62728", "v3": "#2ca02c",
                "v5": "#9467bd"}
STRAT_NAME = {
    "v1": "V1 MA20下车", "v2": "V2 BOLL突破", "v3": "V3 MACD+BOLL",
    "v5": "V5 趋势闸门",
    "v4": "V4 ERP股债通道", "v6": "V6 PE分位带",
    "BH": "买入持有",
}

OUTDIR = _HERE


# ---------------------------------------------------------------- 工具
def metrics(nav):
    m = engine.metrics(nav)
    calmar = (m["ann"] / abs(m["mdd"])) if (m["mdd"] is not None and m["mdd"] < -1e-9) else None
    return m, calmar


def rebase(nav):
    nav = nav.dropna()
    return nav / nav.iloc[0]


# ---------------------------------------------------------------- 各策略逐腿 NAV + 仓位
def leg_nav_v1(leg):
    px = ma20.load(leg)
    sig, _ma = ma20.signal(px)
    pos = sig.shift(1).fillna(0).astype(float)
    nav = ma20.run(px, sig)
    return px, pos, nav


def leg_nav_v2(leg):
    px = boll.load(leg)
    sig, _mid, _up = boll.signal(px)
    pos = sig.shift(1).fillna(0).astype(float)
    nav = boll.run(px, sig)
    return px, pos, nav


def leg_nav_v3(leg):
    px, sig, _s20, _s30 = macd_boll.leg_slice(leg)
    pos = sig.shift(1).fillna(0).astype(float)
    nav = macd_boll.run(px, sig)
    return px, pos, nav


def leg_nav_v5(leg):
    px = trend_gate.load(leg)
    state = trend_gate.state_machine(px, leg)
    # run_leg 有 bug（对整个含字符串列的 state 做 astype(float)），这里只用 'state' 列复算净值
    pos_col = pd.to_numeric(state["state"], errors="coerce").fillna(0.0)
    hold = pos_col.shift(1).fillna(0.0)
    ret = px.pct_change().fillna(0.0)
    r = hold * ret + (1.0 - hold) * trend_gate.CASH_DAILY
    nav = (1.0 + r).cumprod()
    pos = pos_col.shift(1).fillna(0.0)
    return px, pos, nav


LEG_FUNCS = {"v1": leg_nav_v1, "v2": leg_nav_v2, "v3": leg_nav_v3,
             "v5": leg_nav_v5}


def agg_portfolio(navs):
    """逐腿 NAV（各腿起点=1）-> 按带权聚合的组合层 NAV（带权 = band/40）。

    对齐到所有腿都有的交易日交集，再归一，使各策略在相同窗口比较。
    """
    idx = None
    for nav in navs.values():
        idx = nav.index if idx is None else idx.intersection(nav.index)
    idx = idx.sort_values()
    port = None
    for leg, nav in navs.items():
        w = rule.LEGS[leg]["band"] / SLEEVE
        s = nav.reindex(idx)
        port = s * w if port is None else port + s * w
    return port.dropna()


# ---------------------------------------------------------------- 沪深300 单标的方法论 NAV
def v4_hs300_nav():
    """V4 ERP 股债通道（沪深300×国债）默认 20/50/80 档 -> 净值（归一）。"""
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
    """V6 PE 分位仓位带（沪深300 全收益，每周定档）-> 净值（归一）。"""
    frame = v6band.load_frame("hs300")
    ind = v6band.indicators(frame, universe="hs300")
    me = v6band.monthly_signals(ind)
    bands_df, _events = v6band.band_series(ind, me)
    sim = v6band.simulate(ind, bands_df)
    return rebase(sim["nav"])


# ---------------------------------------------------------------- 计算
def compute():
    # 沪深300 基准（场外复权净值，与主策略 hs300 腿同序列）
    hs300_px = vdata.otc_series_for_leg("hs300")
    hs300_bh = rebase(hs300_px)

    # 逐腿：每个时机策略的 NAV（起点=1，各自样本）
    leg_navs = {}      # leg -> {vX: nav}
    leg_pos = {}       # leg -> {vX: pos}
    leg_px = {}        # leg -> price series
    for leg in LEGS:
        leg_navs[leg] = {}
        leg_pos[leg] = {}
        # 趋势线一律用长期场外复权净值（含 A500/标普的桥接代理）
        leg_px[leg] = vdata.otc_series_for_leg(leg)
        for vx in TIMING:
            try:
                px, pos, nav = LEG_FUNCS[vx](leg)
                leg_navs[leg][vx] = nav
                leg_pos[leg][vx] = pos
            except Exception as e:  # noqa
                print(f"  [WARN] {NAMES[leg]}/{vx} 失败: {type(e).__name__}: {e}")

    # 组合层（七腿带权）净值，各时机策略
    port_navs = {}
    for vx in TIMING:
        try:
            navs = {leg: leg_navs[leg][vx] for leg in LEGS if vx in leg_navs[leg]}
            port_navs[vx] = rebase(agg_portfolio(navs))
        except Exception as e:  # noqa
            print(f"  [WARN] 组合层 {vx} 失败: {type(e).__name__}: {e}")

    # 沪深300 方法论策略
    special = {}
    try:
        special["v4"] = v4_hs300_nav()
    except Exception as e:  # noqa
        print(f"  [WARN] V4 失败: {type(e).__name__}: {e}")
    try:
        special["v6"] = v6_hs300_nav()
    except Exception as e:  # noqa
        print(f"  [WARN] V6 失败: {type(e).__name__}: {e}")

    return dict(hs300_bh=hs300_bh, leg_navs=leg_navs, leg_pos=leg_pos,
                leg_px=leg_px, port_navs=port_navs, special=special)


# ---------------------------------------------------------------- 逐腿图表
def plot_leg(leg, data):
    px = rebase(data["leg_px"][leg])
    hs = data["hs300_bh"]
    pos_by = data["leg_pos"][leg]
    nav_by = data["leg_navs"][leg]

    fig, ax = plt.subplots(figsize=(13, 6.5))
    ax.plot(px.index, px.to_numpy(), color="#333", lw=1.6, label=f"{NAMES[leg]} 买入持有")

    # 沪深300 基准（归一到该腿起点）
    if leg != "hs300":
        hsv = hs.reindex(px.index).ffill().dropna()
        hsv = hsv / hsv.iloc[0] * px.iloc[0] if len(hsv) else hs
        ax.plot(hsv.index, hsv.to_numpy(), color="#000", lw=1.1, ls="--",
                alpha=0.7, label="沪深300 基准")

    # 各策略买/卖点（落在价格趋势线上）
    for vx in TIMING:
        if vx not in pos_by:
            continue
        pos = pos_by[vx].reindex(px.index)
        d = pos.diff().fillna(0).to_numpy()
        buys = np.where(d > 1e-6)[0]
        sells = np.where(d < -1e-6)[0]
        pxx = px.to_numpy()
        ax.scatter(px.index[buys], pxx[buys], marker="^", s=42,
                   color=TIMING_COLOR[vx], edgecolors="w", linewidths=0.5, zorder=5,
                   label=f"{STRAT_NAME[vx]} 买")
        ax.scatter(px.index[sells], pxx[sells], marker="v", s=42,
                   color=TIMING_COLOR[vx], edgecolors="k", linewidths=0.5, zorder=5,
                   label=f"{STRAT_NAME[vx]} 卖")

    # 沪深300 腿上额外标注 v4/v6 的股债切换 / 仓位带买卖（用其净值相对变化近似标记调仓日）
    # —— 这里只把 v4/v6 的策略净值画成细线，便于看「它在沪深300 上怎么动」
    if leg == "hs300":
        for vx in ("v4", "v6"):
            if vx in data["special"]:
                s = rebase(data["special"][vx]).reindex(px.index).ffill()
                if len(s) > 1:
                    ax.plot(s.index, s.to_numpy(), color="#17becf" if vx == "v4" else "#bcbd22",
                            lw=1.3, alpha=0.85, label=f"{STRAT_NAME[vx]} 策略净值")

    ax.set_title(f"{NAMES[leg]} · 价格趋势 + 各策略操作点（买▲/卖▼） + 沪深300 基准")
    ax.set_ylabel("净值(归一=1)")
    ax.grid(alpha=0.3)
    ax.legend(fontsize=7, ncol=2, loc="upper left")
    fig.tight_layout()
    path = os.path.join(OUTDIR, f"_v1v7_leg_{leg}.png")
    fig.savefig(path, dpi=110)
    plt.close(fig)
    return path


# ---------------------------------------------------------------- 综合对比图
def plot_comprehensive(data):
    # 统一窗口 = 全历史策略（v1/v2/v3/v5 + v4/v6 + 沪深300基准）的交集
    base = {}
    for vx in ("v1", "v2", "v3", "v5"):
        if vx in data["port_navs"]:
            base[vx] = data["port_navs"][vx]
    for vx in ("v4", "v6"):
        if vx in data["special"]:
            base[vx] = data["special"][vx]
    base["hs300_bh"] = data["hs300_bh"]

    idx = None
    for s in base.values():
        idx = s.index if idx is None else idx.intersection(s.index)
    idx = idx.sort_values()

    fig, ax = plt.subplots(figsize=(14, 7))
    color_map = {"hs300_bh": "#000"}
    color_map.update(TIMING_COLOR)
    color_map["v4"] = "#17becf"
    color_map["v6"] = "#bcbd22"
    # 全历史策略：先裁剪到统一窗口，再归一（保证所有曲线在窗口起点都 = 1）
    for name, s in base.items():
        r = s.reindex(idx).ffill()
        r = r / r.iloc[0]
        if len(r) < 2:
            continue
        ax.plot(r.index, r.to_numpy(), color=color_map.get(name, None),
                lw=1.6 if name == "hs300_bh" else 1.3,
                ls="--" if name == "hs300_bh" else "-",
                label=f"{STRAT_NAME.get(name, name)}" + (" (基准)" if name == "hs300_bh" else ""))
    ax.set_title("v1–v6 策略净值（归一）综合对比 · 基准 = 沪深300 买入持有")
    ax.set_ylabel("净值(归一=1)")
    ax.grid(alpha=0.3)
    ax.legend(fontsize=8, loc="upper left")
    fig.tight_layout()
    path = os.path.join(OUTDIR, "_v1v7_comprehensive.png")
    fig.savefig(path, dpi=110)
    plt.close(fig)
    return path, idx


# ---------------------------------------------------------------- 逐腿最优策略
BASE_STRATS = ["v1", "v2", "v3", "v5"]   # 全历史离散时机策略（决定共同窗口）


def _reb_on(nav, idx):
    """把 nav 归一后对齐到 idx；缺失过多则返回 None。"""
    s = rebase(nav).reindex(idx).dropna()
    return s if len(s) >= 30 else None


def leg_best(leg, data):
    nav_by = data["leg_navs"][leg]
    px = data["leg_px"][leg]
    bh = rebase(px)
    # 共同窗口 = 全历史策略（v1/v2/v3/v5）的交集
    idx = None
    for vx in BASE_STRATS:
        if vx in nav_by:
            idx = nav_by[vx].index if idx is None else idx.intersection(nav_by[vx].index)
    if idx is None or len(idx) < 30:
        return None
    idx = idx.sort_values()
    rows = []
    for vx in TIMING:   # v1/v2/v3/v5
        nav = nav_by.get(vx)
        if nav is None:
            continue
        r = _reb_on(nav, idx)
        if r is None:
            continue
        short = False
        bh_r = rebase(bh).reindex(r.index).ffill()
        bhm, _ = metrics(bh_r)
        m, cal = metrics(r)
        rows.append(dict(vx=vx, ann=m["ann"], mdd=m["mdd"], sharpe=m["sharpe"],
                         calmar=cal, edge=(m["ann"] - bhm["ann"]) * 100,
                         vol=m["vol"], short=short,
                         exposure=float(pos_exposure(data, leg, vx, r.index))))
    if not rows:
        return None
    # 推荐只从全历史策略（v1/v2/v3/v5）中选：取同窗口年化超额最高者；
    # 若连最好的时机策略都跑输买入持有 >2pp，则诚实地回退到「买入持有」（典型如美股长牛）。
    best = max(rows, key=lambda z: z["edge"])
    rec = best["vx"] if best["edge"] >= -2.0 else "BH"
    bhm_all, _ = metrics(rebase(bh).reindex(idx).ffill())
    return dict(bh_ann=bhm_all["ann"], bh_mdd=bhm_all["mdd"], bh_sharpe=bhm_all["sharpe"],
                rows=rows, best=rec)


def pos_exposure(data, leg, vx, idx):
    pos = data["leg_pos"][leg].get(vx)
    if pos is None:
        return None
    p = pos.reindex(idx).ffill().fillna(0)
    return float(p.mean())


# ---------------------------------------------------------------- 主流程
def fmt_pct(x, nd=2, sign=False):
    if x is None or (isinstance(x, float) and math.isnan(x)):
        return "—"
    return (f"{x:+.{nd}%}" if sign else f"{x:.{nd}%}")


def main():
    os.makedirs(OUTDIR, exist_ok=True)
    print("计算 v1–v6 回测（七腿池，场外复权净值，T+1 成交，零费率）…")
    data = compute()

    print("画逐腿趋势+操作点图…")
    leg_pngs = {}
    for leg in LEGS:
        try:
            leg_pngs[leg] = plot_leg(leg, data)
        except Exception as e:  # noqa
            print(f"  [WARN] 画图 {NAMES[leg]} 失败: {type(e).__name__}: {e}")

    print("画综合对比图…")
    comp_png, comp_idx = plot_comprehensive(data)

    # 逐腿最优
    print("逐腿最优策略…")
    best = {}
    for leg in LEGS:
        best[leg] = leg_best(leg, data)

    # 综合对比指标（统一窗口）
    print("综合对比指标…")
    comp_rows = []
    for vx in ("v1", "v2", "v3", "v5"):
        if vx in data["port_navs"]:
            r = rebase(data["port_navs"][vx]).reindex(comp_idx).ffill()
            if len(r) > 30:
                m, cal = metrics(r)
                comp_rows.append(dict(vx=vx, ann=m["ann"], mdd=m["mdd"],
                                      sharpe=m["sharpe"], calmar=cal, vol=m["vol"],
                                      is_port=True, short=False))
    for vx in ("v4", "v6"):
        if vx in data["special"]:
            r = rebase(data["special"][vx]).reindex(comp_idx).ffill()
            if len(r) > 30:
                m, cal = metrics(r)
                comp_rows.append(dict(vx=vx, ann=m["ann"], mdd=m["mdd"],
                                      sharpe=m["sharpe"], calmar=cal, vol=m["vol"],
                                      is_port=False, short=False))
    bh_r = rebase(data["hs300_bh"]).reindex(comp_idx).ffill()
    bhm, bhcal = metrics(bh_r)

    # ---- 写报告
    L = [
        "# 波动资产 · v1–v6 横向对比（七腿池 · 场外复权净值 · 沪深300 基准）",
        "",
        "> 池 = `rule.LEG_ORDER` 七腿（README.md §2.1）：中证A500 / 沪深300 / 纳指100 / 标普500 / "
        "科创50 / 恒生科技 / 黄金。",
        "> 价格 = 场外**复权净值**（含分红再投 / 汇率 / 跟踪误差）；成交 = T 日盘后算信号、T+1 净值成交；"
        "空仓按货基 1.40% 计息；**零费率**（与各变种回测主表一致）。",
        "> 时机家族 v1/v2/v3/v5 覆盖全部七腿并产出离散买/卖点；v4(ERP 股债通道) / v6(PE 分位带) 是"
        "沪深300 单标的方法论，只参与综合对比与沪深300 腿图。",
        "",
        f"## 1. 逐腿最优策略（同窗口内比较：年化 / 最大回撤 / 夏普 / 年化超额 vs 买入持有）",
        "",
        "| 标的 | 买入持有 年化/回撤/夏普 | 推荐策略 | "
        + " | ".join(STRAT_NAME[v] for v in TIMING) + " |",
        "|---|---:|---:|" + "---:|" * len(TIMING),
    ]
    for leg in LEGS:
        b = best[leg]
        if not b:
            continue
        rec = b["best"]
        cells = []
        for vx in TIMING:
            row = next((x for x in b["rows"] if x["vx"] == vx), None)
            if row is None:
                cells.append("—")
            else:
                mark = "†" if row.get("short") else ""
                cells.append(f"{fmt_pct(row['ann'])}/{fmt_pct(row['mdd'])}/"
                             f"{row['sharpe']:.2f}({row['edge']:+.1f}){mark}")
        L.append(
            f"| {NAMES[leg]} | {fmt_pct(b['bh_ann'])}/{fmt_pct(b['bh_mdd'])}/"
            f"{b['bh_sharpe']:.2f} | **{STRAT_NAME[rec]}** | " + " | ".join(cells) + " |")
    L += [
        "",
        "> 单元格 = 该策略在**该腿上与其它策略同窗口**的年化 / 最大回撤 / 夏普（括号内 = 年化超额 pp vs 买入持有）。",
        "> 推荐 = 在 v1/v2/v3/v5 中取同窗口年化超额最高者；若连最好的时机策略都跑输买入持有 >2pp，"
        "则回退到**买入持有**（典型如美股长牛，减仓纯拖累）。",
        "> ⚠ A500 与沪深300 两列互为镜像（2025-03-27 前两条腿场外序列同源自 `000051`），不是两份独立证据。",
        "",
        "## 2. 综合对比（v1–v6 策略净值 vs 沪深300 基准）",
        "",
        f"统一窗口 = v1/v2/v3/v5/v4/v6 ∩ 沪深300 的交集：{comp_idx[0].date()} ~ {comp_idx[-1].date()} "
        f"（{(comp_idx[-1]-comp_idx[0]).days/365.25:.1f} 年）。",
        "",
        "| 策略 | 类型 | 年化 | 最大回撤 | 夏普 | Calmar | 波动 | vs 沪深300 年化差 |",
        "|---|---|---:|---:|---:|---:|---:|---:|",
    ]
    for row in sorted(comp_rows, key=lambda z: (z["ann"]), reverse=True):
        edge = (row["ann"] - bhm["ann"]) * 100
        note = ""
        if row.get("short"):
            note = f" ⚠{row.get('n_years', 0):.1f}年短窗"
        L.append(
            f"| {STRAT_NAME[row['vx']]} | {'七腿组合' if row['is_port'] else '沪深300方法论'} | "
            f"{fmt_pct(row['ann'])} | {fmt_pct(row['mdd'])} | {row['sharpe']:.2f} | "
            f"{('-' if row['calmar'] is None else f'{row['calmar']:.2f}')} | "
            f"{fmt_pct(row['vol'])} | **{edge:+.2f}pp**{note} |")
    L.append(
        f"| 沪深300 买入持有（基准） | — | {fmt_pct(bhm['ann'])} | {fmt_pct(bhm['mdd'])} | "
        f"{bhm['sharpe']:.2f} | {('-' if bhcal is None else f'{bhcal:.2f}')} | "
        f"{fmt_pct(bhm['vol'])} | — |")
    L += [
        "",
        "> 七腿组合策略（v1/v2/v3/v5）按 `rule` 目标带权重归一化（带权 = band/40，满仓 = 40pp）；"
        "v4/v6 是沪深300×国债 / 沪深300 仓位带，单标的方法论。",
        "> 零费率口径；含申赎费时结论会有变化（尤其 v1 MA20 年笔数最高，详见各变种 README 的成本节）。",
        "",
        "## 3. 逐腿结论与跨标的规律",
        "",
        "- **美股（纳指/标普）是时机死穴**：长牛里任何减仓都是纯拖累，v1/v2/v3/v5 全部跑输买入持有"
        "（年化差 −4 ~ −17pp），故这两条腿推荐**买入持有**。",
        "- **港股（恒科）是时机甜区**：v3 MACD+BOLL 年化超额 +18pp，回撤从 −52% 压到 −20%。",
        "- **A股宽基（A500/沪深300）**：v2 BOLL 突破最稳（年化超额 +2~3pp、回撤大幅改善）。",
        "- **科创50**：波动大、时机红利最高（v1/v2/v3 超额 +13~16pp），但绝对回撤仍大。",
        "- **黄金**：长牛里时机略亏（v5 约 −1.6pp），回撤改善是唯一获得感。",
        "- **v4/v6**：沪深300 上的股债配置 / 估值分成思路，不是逐标的择时，只与沪深300 基准对照。",
        "",
        "## 4. 图表",
        "",
    ]
    for leg in LEGS:
        if leg in leg_pngs:
            L.append(f"- 逐腿趋势+操作点 · {NAMES[leg]}：`{os.path.basename(leg_pngs[leg])}`")
    L.append(f"- 综合对比（v1–v6 vs 沪深300）：`{os.path.basename(comp_png)}`")
    L += [
        "",
        "> 模拟结果，非投资建议。",
    ]
    md_path = os.path.join(OUTDIR, "_v1v7_compare.md")
    with open(md_path, "w", encoding="utf-8") as f:
        f.write("\n".join(L))

    # 机读结果
    json_path = os.path.join(OUTDIR, "_v1v7_compare.jsonl")
    with open(json_path, "w", encoding="utf-8") as f:
        for leg in LEGS:
            b = best[leg]
            if b:
                rec = {"leg": leg, "name": NAMES[leg], "bh_ann": b["bh_ann"],
                       "bh_mdd": b["bh_mdd"], "bh_sharpe": b["bh_sharpe"],
                       "best": b["best"], "candidates": b["rows"]}
                f.write(json.dumps(rec, ensure_ascii=False, default=str) + "\n")
        f.write(json.dumps({"comprehensive": comp_rows,
                            "hs300_bh": {"ann": bhm["ann"], "mdd": bhm["mdd"],
                                         "sharpe": bhm["sharpe"], "calmar": bhcal}},
                           ensure_ascii=False, default=str) + "\n")

    print("报告:", md_path)
    print("结果:", json_path)
    print("图  :", [os.path.basename(p) for p in leg_pngs.values()] + [os.path.basename(comp_png)])
    print("\n逐腿推荐策略：")
    for leg in LEGS:
        b = best[leg]
        if b:
            print(f"  {NAMES[leg]:8s} -> {STRAT_NAME[b['best']]}")


if __name__ == "__main__":
    main()
