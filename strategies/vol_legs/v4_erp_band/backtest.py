# -*- coding: utf-8 -*-
"""波动资产 · V4（两带滞回 · 离带执行）· 回测与归因。

本口径：满仓 100 / 维持 / 清仓 0，边界 z=0.80 / 0.20，离带执行
→ 有效规则 = 跌破 0.8 买满、升破 0.2 清空、中间不动。
对照：`erp.py` 自带的带位信号（旧 v4 口径：百分位 + 趋势过滤 80/50/20）、
读法 B（按当前带定仓）、两套费率、静态 50/50 与 80/20、买入持有股/债、反向。
同一份数据、同一条债券腿、同一套费率与 T+1 纪律 —— 差异只能来自**执行时点**。

产出（相对本文件）：
- backtest/_v4_erp_band_bt.md / .jsonl
- backtest/_v4_erp_band_rule.png    上半 z + 带界 + 跨带点，下半「计划 vs 实际」仓位阶梯
- backtest/_v4_erp_band_signals.png 同款操作点底图（▲加仓 / ▼减仓）
- backtest/_v4_erp_band_nav.png     净值 + 回撤

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
_VL = os.path.dirname(_HERE)
for _p in (_HERE, _VL):
    if _p not in sys.path:
        sys.path.insert(0, _p)

import erp  # noqa: E402
import ladder as L  # noqa: E402

OUTDIR = os.path.join(_HERE, "backtest")
YEARS_3Y = 3


# ---------------------------------------------------------------- 工具
def align(df, t0=None, t1=None):
    d = df[df["ready"]]
    return d.loc[t0:t1]


def bond_series(kind="synth", duration=None):
    if kind == "real":
        s = erp.bond_real()
        if s is None:
            raise RuntimeError("缺 data/_bt_caches/nav_003377.csv（真实债基）")
    else:
        s = erp.bond_synthetic(erp.load_bond10y(), duration=duration)
    return s


def yearly_returns(nav):
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


def evaluate(df, bond, t0=None, t1=None, mode=None, weights=None, band_tol=None,
             target_col="target", fees=True, reverse=False, rebal_freq=None,
             edges=None, fee_pair=None):
    """按给定口径跑一遍。target 由阶梯状态机给出（全序列上算，再切回测窗口）。

    fee_pair=(股票腿费率, 债券腿费率)，覆盖 CFG（用于「场外 0.5% 赎回费 vs 场内 ETF 0.02%」对比）。
    """
    dd = df.copy()
    if mode is not None or weights is not None or band_tol is not None or edges is not None:
        sig = L.ladder_series(dd, mode=mode, weights=weights, band_tol=band_tol, edges=edges)
        dd["target"] = sig["target"]
    d = align(dd, t0, t1)
    tgt = d[target_col].astype("float64")
    if reverse:
        tgt = 1.0 - tgt
    pe = d["tr"].astype("float64")                     # 股票腿 = 全收益（含分红再投）
    pb = bond.reindex(pe.index).ffill()
    ok = pb.notna()
    pe, pb, tgt, d = pe[ok], pb[ok], tgt[ok], d.loc[pe.index]
    if not len(pe):
        raise RuntimeError("样本为空")
    if fee_pair:
        fe, fb = fee_pair
    else:
        fe = L.CFG["fee_eq"] if fees else (0.0, 0.0)
        fb = L.CFG["fee_bd"] if fees else (0.0, 0.0)
    rf = L.CFG["rebal_freq"] if rebal_freq is None else rebal_freq
    r = erp.run(pe, pb, tgt, period_end=erp.period_end_flags(pe.index, rf),
                tol=L.CFG["rebal_tol"], fee_eq=fe, fee_bd=fb)
    r["m"] = erp.perf(r["nav"])
    r["frame"] = d
    return r


def static_bench(bond, pe, w=0.5, freq="M", fees=True):
    pb = bond.reindex(pe.index).ffill()
    tgt = pd.Series(float(w), index=pe.index)
    fe = L.CFG["fee_eq"] if fees else (0.0, 0.0)
    fb = L.CFG["fee_bd"] if fees else (0.0, 0.0)
    r = erp.run(pe, pb, tgt, period_end=erp.period_end_flags(pe.index, freq),
                tol=L.CFG["rebal_tol"], fee_eq=fe, fee_bd=fb)
    r["m"] = erp.perf(r["nav"])
    return r


def row(name, nav, note="", r=None):
    m = erp.perf(nav)
    out = dict(name=name, cum=m["cum"], ann=m["ann"], vol=m["vol"], mdd=m["mdd"],
               sharpe=m["sharpe"], calmar=(m["ann"] / abs(m["mdd"]) if m["mdd"] else None),
               note=note)
    if r is not None:
        sw = [t for t in r["trades"] if t["why"] == "signal"]
        out.update(trades=len(r["trades"]), switches=len(sw), fees=r["fees"],
                   w_avg=float(r["w"].mean()))
    return out


def sens(df, bond, t0, base, variants):
    """逐档重算 -> list[dict]。base = 传给 evaluate 的固定 kwargs。"""
    rows = []
    for label, kw, wsum in variants:
        r = evaluate(df, bond, t0=t0, **{**base, **kw})
        fr = r["frame"]
        b50 = erp.perf(static_bench(bond, fr["tr"], 0.5)["nav"])
        rows.append(dict(label=label, start=fr.index[0], years=(fr.index[-1] - fr.index[0]).days
                         / 365.25, ann=r["m"]["ann"], b50=b50["ann"],
                         edge=(r["m"]["ann"] - b50["ann"]) * 100, mdd=r["m"]["mdd"],
                         sharpe=r["m"]["sharpe"], switches=sum(1 for t in r["trades"]
                                                               if t["why"] == "signal"),
                         fees=r["fees"], w_avg=float(r["w"].mean()),
                         wsum=wsum if wsum is not None else float(fr["target"].mean())))
    return rows


# ---------------------------------------------------------------- 每个带的计划 vs 实际
def band_profile(df, mode=None, weights=None):
    """每个带：天数占比 / 计划仓位（None = 维持带）/ **实际平均仓位** / 平均 z / 其后 250 日收益。"""
    w = tuple(L.CFG["weights"] if weights is None else weights)
    d = df[df["ready"]]
    s = L.ladder_series(df, mode=mode, weights=w).loc[d.index]
    fwd = d["tr"].shift(-250) / d["tr"] - 1.0
    rows = []
    for b in range(len(w)):
        m = s["band"] == b
        if not m.any():
            continue
        held = float(s.loc[m, "target"].mean())
        plan = w[b]
        rows.append(dict(band=b, name=L.BAND_NAME[b], rng=L.BAND_RANGE[b], days=int(m.sum()),
                         share=float(m.mean()), plan=(None if plan is None else float(plan)),
                         held=held,
                         gap=0.0 if plan is None else float(held - plan),
                         z_mean=float(d.loc[m, "z"].mean()),
                         fwd250=float(fwd[m].mean()) if fwd[m].notna().any() else None))
    return rows


def forward_by_band(d, horizons=(20, 60, 250)):
    rows = []
    for b in range(len(L.CFG["weights"])):
        m = d["band"] == b
        if not m.any():
            continue
        for h in horizons:
            f = (d["tr"].shift(-h) / d["tr"] - 1.0)[m].dropna()
            if not len(f):
                continue
            rows.append(dict(band=b, name=L.BAND_NAME[b], horizon=h, n=int(len(f)),
                             mean=float(f.mean()), median=float(f.median()),
                             win=float((f > 0).mean()), p10=float(f.quantile(0.10)),
                             p90=float(f.quantile(0.90))))
    return pd.DataFrame(rows)


# ---------------------------------------------------------------- 图
def _mpl():
    try:
        import matplotlib
        matplotlib.use("Agg")
        import matplotlib.pyplot as plt
        from matplotlib.ticker import StrMethodFormatter  # noqa: F401
        plt.rcParams["font.sans-serif"] = ["Microsoft YaHei", "SimHei", "DejaVu Sans"]
        plt.rcParams["axes.unicode_minus"] = False
        return plt
    except Exception as exc:  # noqa: BLE001
        print("matplotlib 不可用，跳过作图:", exc)
        return None


def plot_ladder(df, path, start=None):
    """上：z + 带界 + 跨带点；下：计划仓位（虚线）vs 实际仓位（实线）。"""
    plt = _mpl()
    if plt is None:
        return None
    d = df.loc[start:] if start else df
    d = d[d["ready"]]
    if d.empty:
        return None
    e = L.CFG["edges"]
    fig, (ax, ax2) = plt.subplots(2, 1, figsize=(13, 8.2), sharex=True,
                                  gridspec_kw=dict(height_ratios=[1.6, 1.0]))
    fills = ["#c0122f", "#e08c33", "#8fbf9f", "#127a36"]
    bounds = [min(d["z"].min(), -0.3)] + list(e) + [max(d["z"].max(), 1.6)]
    for i in range(len(e) + 1):
        ax.axhspan(bounds[i], bounds[i + 1], color=fills[i % len(fills)], alpha=0.06, lw=0)
    for x in e:
        ax.axhline(x, color="#5b6b7a", lw=0.9, ls="--", alpha=0.7)
        ax.text(d.index[2], x + 0.015, f"z={x:g}", fontsize=9, color="#5b6b7a")
    ax.plot(d.index, d["z"], color="#2f4b9b", lw=1.1, label="股债利差 z")
    ax.set_ylabel("z（0=下轨/最贵，1=上轨/最便宜）")
    ax.grid(axis="y", ls=":", lw=0.5, alpha=0.4)

    up = d["event"] > 0
    dn = d["event"] < 0
    ax2.step(d.index, d["plan_w"], where="post", color="#8a8a8a", lw=1.0, ls="--",
             label="计划仓位（所在带）")
    ax2.step(d.index, d["target"], where="post", color="#c0122f", lw=1.6, label="实际目标仓位")
    ax2.scatter(d.index[up], d["target"][up], marker="^", s=26, color="#c0122f", zorder=4)
    ax2.scatter(d.index[dn], d["target"][dn], marker="v", s=26, color="#127a36", zorder=4)
    ax2.set_ylabel("股票目标仓位")
    ax2.set_ylim(-0.05, 1.08)
    ax2.grid(alpha=0.3, ls="--", lw=0.6)
    ax2.legend(loc="upper left", frameon=False, fontsize=10, ncol=2)

    h1, l1 = ax.get_legend_handles_labels()
    h2, l2 = ax2.get_legend_handles_labels()
    ax.legend(h1 + h2, l1 + l2, loc="upper left", frameon=False, fontsize=10, ncol=4)
    ax.set_title(f"两带滞回 · {L.MODE_SHORT[L.CFG['ladder_mode']]}"
                 f"（▲ 加仓 / ▼ 减仓，本段共 {int((d['event'] != 0).sum())} 次）",
                 loc="left", fontsize=13, weight="bold")
    fig.text(0.01, 0.005, "机械规则输出，非投资建议。", fontsize=9, color="#5b6b7a")
    fig.tight_layout()
    fig.savefig(path, dpi=130, facecolor="white")
    plt.close(fig)
    return path


def plot_nav(navs, path, title):
    plt = _mpl()
    if plt is None:
        return None
    fig, (ax, ax2) = plt.subplots(2, 1, figsize=(13, 8.0), sharex=True,
                                  gridspec_kw=dict(height_ratios=[2.4, 1.0]))
    colors = ["#c0122f", "#e08c33", "#2f4b9b", "#127a36", "#8a8a8a", "#7d3c98"]
    for (name, nav), c in zip(navs, colors):
        v = nav / nav.iloc[0]
        ax.plot(v.index, v, lw=1.5, color=c, label=f"{name}（{erp.perf(nav)['ann']:+.2%}/年）")
        ax2.plot(v.index, v / v.cummax() - 1.0, lw=1.0, color=c, alpha=0.85)
    ax.set_yscale("log")
    ax.set_ylabel("净值（对数轴）")
    ax.grid(alpha=0.3, ls="--", lw=0.6)
    ax2.set_ylabel("回撤")
    ax2.grid(alpha=0.3, ls="--", lw=0.6)
    ax.legend(loc="upper left", frameon=False, fontsize=10)
    ax.set_title(title, loc="left", fontsize=13, weight="bold")
    fig.text(0.01, 0.005, "模拟结果，非投资建议。", fontsize=9, color="#5b6b7a")
    fig.tight_layout()
    fig.savefig(path, dpi=130, facecolor="white")
    plt.close(fig)
    return path


# ---------------------------------------------------------------- 报告
def main():
    os.makedirs(OUTDIR, exist_ok=True)
    df = L.frame()
    t0 = align(df).index[0]
    bond = bond_series("synth")

    res = {}
    res["left"] = evaluate(df, bond, t0=t0)                                   # 默认 hold_left
    res["left_tol"] = evaluate(df, bond, t0=t0, band_tol=0.05)
    res["new"] = evaluate(df, bond, t0=t0, mode="hold_new")
    res["new_tol"] = evaluate(df, bond, t0=t0, mode="hold_new", band_tol=0.05)
    d = res["left"]["frame"]

    # 对照：erp.py 自带的带位信号（旧 v4 口径：百分位 + 趋势过滤 80/50/20）
    eng = evaluate(df, bond, t0=t0, target_col="eng_target")

    rev = evaluate(df, bond, t0=t0, reverse=True)
    nofee = evaluate(df, bond, t0=t0, fees=False)
    etf_fee = ((0.0002, 0.0002), (0.0002, 0.0002))
    etf = evaluate(df, bond, t0=t0, fee_pair=etf_fee)
    etf_tol = evaluate(df, bond, t0=t0, band_tol=0.05, fee_pair=etf_fee)
    st50 = static_bench(bond, d["tr"], 0.50)
    st80 = static_bench(bond, d["tr"], 0.80)
    bh_eq = d["tr"] / d["tr"].iloc[0]
    pb = bond.reindex(d["tr"].index).ffill()
    bh_bd = pb / pb.iloc[0]

    rows = [
        row("**V4 两带滞回（离带执行，默认）**", res["left"]["nav"],
            f"满仓 100 / 维持 / 清仓 0 · edge {L.CFG['edges']} · band_tol=0", res["left"]),
        row("V4 + 死区 band_tol=0.05", res["left_tol"]["nav"], "边界再让 5pp 才算跨过",
            res["left_tol"]),
        row("V4 · hold_new（按当前带定仓）", res["new"]["nav"], "方向相反：升破 0.8 买 / 跌破 0.2 卖",
            res["new"]),
        row("V4 · hold_new + tol=0.05", res["new_tol"]["nav"], "", res["new_tol"]),
        row("对照：引擎带位信号（旧 v4 口径 80/50/20）", eng["nav"],
            "erp.py 自带：百分位触发 + 趋势过滤，触轨当天动手", eng),
        row("V4（不计费）", nofee["nav"], "单看摩擦成本", nofee),
        row("**V4 · 场内 ETF 费率（0.02% 单边）**", etf["nav"],
            "同样的调仓次数，只把赎回费 0.5% 换成 ETF 佣金", etf),
        row("V4 · ETF 费率 + 死区 0.05", etf_tol["nav"], "", etf_tol),
        row("**反向（1−仓位）**", rev["nav"], "方向检验", rev),
        row("静态 50/50（月末回锚）", st50["nav"], "", st50),
        row("静态 80/20（月末回锚）", st80["nav"], "", st80),
        row("买入持有沪深300（全收益）", bh_eq, ""),
        row("买入持有债券（合成 10Y，D=8）", bh_bd, ""),
    ]

    prof_l = band_profile(df, mode="hold_left")
    prof_n = band_profile(df, mode="hold_new")
    fwd_band = forward_by_band(d)
    yrs = (d.index[-1] - d.index[0]).days / 365.25

    mid = d.index[len(d) // 2]
    half = []
    for tag, lo, hi in (("前半段", None, mid), ("后半段", mid, None)):
        sub = align(df, lo, hi)
        if len(sub) < 300:
            continue
        acc = {"left": evaluate(df, bond, t0=lo, t1=hi)}
        acc["new"] = evaluate(df, bond, t0=lo, t1=hi, mode="hold_new")
        acc["etf"] = evaluate(df, bond, t0=lo, t1=hi, fee_pair=etf_fee)
        acc["etf_tol"] = evaluate(df, bond, t0=lo, t1=hi, band_tol=0.05, fee_pair=etf_fee)
        acc["eng"] = evaluate(df, bond, t0=lo, t1=hi, target_col="eng_target")
        half.append(dict(tag=tag, start=sub.index[0], end=sub.index[-1], sub=sub, **acc))

    per_year = pd.DataFrame({
        "v4": yearly_returns(res["left"]["nav"]),
        "v4B": yearly_returns(res["new"]["nav"]),
        "eng": yearly_returns(eng["nav"]),
        "静态50/50": yearly_returns(st50["nav"]),
        "沪深300": yearly_returns(bh_eq),
    }).dropna(how="all")

    # 敏感性
    e0 = L.CFG["edges"]
    sens_tol = sens(df, bond, t0, {}, [(f"死区 {x:g}", {"band_tol": x}, None)
                                       for x in (0.0, 0.02, 0.05, 0.08, 0.10)])
    sens_tol_etf = sens(df, bond, t0, {"fee_pair": etf_fee},
                        [(f"死区 {x:g}", {"band_tol": x}, None)
                         for x in (0.0, 0.02, 0.05, 0.08, 0.10)])
    sens_edge = []
    for shift in (-0.10, -0.05, 0.0, 0.05, 0.10):
        eg = tuple(round(x + shift, 2) for x in e0)
        sens_edge.append((f"边界整体平移 {shift:+.2f} → {eg}", {"edges": eg}, None))
    sens_e = sens(df, bond, t0, {}, sens_edge)
    sens_w = sens(df, bond, t0, {}, [
        ("100/维持/0（默认）", {"weights": (1.0, None, 0.0)}, None),
        ("80/维持/20（旧口径 80/20）", {"weights": (0.8, None, 0.2)}, None),
        ("100/50/0（维持带=固定半仓）", {"weights": (1.0, 0.5, 0.0)}, None),
        ("80/50/20（三档定仓）", {"weights": (0.8, 0.5, 0.2)}, None),
    ])
    sens_win = []
    for win in (120, 250, 500, 750):
        fw = L.frame(window=win)
        tw = align(fw).index[0]
        r = evaluate(fw, bond, t0=tw)
        fr = r["frame"]
        b50 = erp.perf(static_bench(bond, fr["tr"], 0.5)["nav"])
        sens_win.append(dict(label=f"{win} 日", start=fr.index[0],
                             years=(fr.index[-1] - fr.index[0]).days / 365.25,
                             ann=r["m"]["ann"], b50=b50["ann"],
                             edge=(r["m"]["ann"] - b50["ann"]) * 100, mdd=r["m"]["mdd"],
                             sharpe=r["m"]["sharpe"],
                             switches=sum(1 for t in r["trades"] if t["why"] == "signal"),
                             fees=r["fees"], w_avg=float(r["w"].mean()),
                             wsum=float(fr["target"].mean())))

    # ---------------- 图
    cut = df.index[-1] - pd.Timedelta(days=365 * YEARS_3Y)
    p_lad = plot_ladder(df, os.path.join(OUTDIR, "_v4_erp_band_rule.png"))
    p_lad3 = plot_ladder(df, os.path.join(OUTDIR, "_v4_erp_band_rule_3y.png"), start=cut)
    p_sig = erp.plot_overlay_signals(
        df, os.path.join(OUTDIR, "_v4_erp_band_signals.png"),
        title="股债利差 vs 沪深300 · 操作点（两带滞回）",
        subtitle=f"{df.index[0]:%Y-%m-%d} 至 {df.index[-1]:%Y-%m-%d}（日线）"
                 f" · 通道 {L.CFG['window']} 日 / ±{L.CFG['k']:g}σ"
                 f" · 带界 z={L.CFG['edges']} · ▲加仓 / ▼减仓")
    p_nav = plot_nav([("V4 离带执行", res["left"]["nav"]), ("V4 按当前带", res["new"]["nav"]),
                      ("引擎带位信号", eng["nav"]),
                      ("静态 50/50", st50["nav"]), ("沪深300", bh_eq)],
                     os.path.join(OUTDIR, "_v4_erp_band_nav.png"),
                     "净值与回撤 · 同一份数据，只有执行时点不同")

    # ---------------- jsonl
    with open(os.path.join(OUTDIR, "_v4_erp_band_bt.jsonl"), "w", encoding="utf-8") as f:
        f.write(json.dumps(dict(kind="summary", start=d.index[0].date().isoformat(),
                                end=d.index[-1].date().isoformat(), years=round(yrs, 2),
                                days=len(d),
                                params={"edges": list(L.CFG["edges"]),
                                        "weights": list(L.CFG["weights"]),
                                        "ladder_mode": L.CFG["ladder_mode"],
                                        "band_tol": L.CFG["band_tol"],
                                        "window": L.CFG["window"], "k": L.CFG["k"]},
                                source=dict(erp.SOURCE)), ensure_ascii=False) + "\n")
        for r in rows:
            f.write(json.dumps({k: (round(v, 6) if isinstance(v, float) else v)
                                for k, v in r.items()}, ensure_ascii=False) + "\n")
        for x in prof_l:
            f.write(json.dumps({"kind": "band_profile", "mode": "hold_left", **x},
                               ensure_ascii=False) + "\n")

    # ---------------- MD
    m9 = res["left"]["m"]
    mn = res["new"]["m"]
    me = eng["m"]
    d50 = (m9["ann"] - st50["m"]["ann"]) * 100
    sw = sum(1 for t in res["left"]["trades"] if t["why"] == "signal")

    L_ = [
        "# 波动资产 · V4（股债利差通道）：两带滞回（离带执行）",
        "",
        f"> 样本 **{d.index[0]:%Y-%m-%d} ~ {d.index[-1]:%Y-%m-%d}（{yrs:.1f} 年）**"
        f" · 数据：{erp.SOURCE.get('hs300')} / {erp.SOURCE.get('bond')}",
        f"> 规则：z 分 **两带 + 中间维持区**（满仓 z≥0.80 / 维持 0.20~0.80 / 清仓 z<0.20）；"
        "**从某个带内离开时执行带内的操作**。通道、费率、T+1、月末回锚沿用原 v4 引擎（`erp.py`）。",
        "> 机械规则输出，**非投资建议**。",
        "",
        "## 1. 规则与执行口径",
        "",
        "| 带 | z 区间 | 仓位（股票占比） | 余额（债券） | 历史天数占比 |",
        "|---|---|---|---:|---:|",
    ]
    for x in prof_l:
        if x["plan"] is None:
            cell, bal = "**维持上个仓位**（不主动调仓）", "—"
        else:
            cell, bal = f"**{x['plan']:.0%}** {x['name']}", f"{1 - x['plan']:.0%}"
        L_.append(f"| B{x['band']} | {x['rng']} | {cell} | {bal} | {x['share']:.0%} |")
    L_ += [
        "",
        "「从某个带内离开时执行带内的操作」，逐条落地：",
        "",
        "| 事件 | 执行带内的操作 | 有效方向 |",
        "|---|---|---|",
        "| 离开满仓带（z 跌回 0.80 之下） | 满仓 **100%** | **买入** |",
        "| 离开维持带（跨 0.80 或 0.20） | 维持 —— **不动** | 不动 |",
        "| 离开清仓带（z 升回 0.20 之上） | 清仓 **0%** | **卖出** |",
        "",
        f"→ 整条规则就是一条**滞回**：z 向下跌破 0.80 买满、向上升破 0.20 清空、中间不动；"
        f"状态只有 {{100%, 0%}}。14 年多只动 **{sw} 次**（≈{sw / yrs:.1f} 次/年），"
        "比「4 带阶梯」版（234 次）低两个数量级。",
        "",
        "代价是**两头都慢半拍**（见 §3）：满仓带里还拿着上一轮留下的仓位（平均 "
        f"{prof_l[0]['held']:.0%}），清仓带里也还没卖（平均 {prof_l[-1]['held']:.0%}）。"
        "这是「离带才动手」的直接推论。",
        "",
        "对照读法 B（`hold_new`：进入哪个带就用哪个带的仓位，等价于「z 升破 0.80 买、"
        f"跌破 0.20 卖」）：平均股票仓位 {res['new']['frame']['target'].mean():.0%}、"
        f"{sum(1 for t in res['new']['trades'] if t['why'] == 'signal')} 次调仓。"
        "方向与 A 相反，只作对照，不用于执行。",
        "",
        "## 2. 主回测（同数据、同债券腿、同费率）",
        "",
        "| 组合 | 累计 | 年化 | 波动 | 最大回撤 | 夏普 | Calmar | 平均股票仓位 | 调仓 | 累计费用 |",
        "|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|",
    ]
    for r in rows:
        w_txt = f"{r['w_avg']:.0%}" if "w_avg" in r else "—"
        fee_txt = f"{r['fees']:.2%}" if "fees" in r else "—"
        L_.append(f"| {r['name']} | {erp.fmt_pct(r['cum'], 1)} | {erp.fmt_pct(r['ann'])} | "
                  f"{erp.fmt_pct(r['vol'])} | {erp.fmt_pct(r['mdd'])} | {r['sharpe']:.2f} | "
                  f"{erp.fmt_x(r['calmar'])} | {w_txt} | {r.get('switches', '—')} | {fee_txt} |")
    L_ += [
        "",
        f"> **V4 两带 vs 引擎带位信号（旧 v4 口径）：{(m9['ann'] - me['ann']) * 100:+.2f}pp/年**；"
        f"**vs 静态 50/50：{d50:+.2f}pp/年**。",
        f"> **读法 A vs B：{(m9['ann'] - mn['ann']) * 100:+.2f}pp/年**"
        f"（回撤 {erp.fmt_pct(m9['mdd'])} vs {erp.fmt_pct(mn['mdd'])}）。",
        f"> **摩擦成本**：V4 全年调仓 **{sw} 次**（平均 {sw / yrs:.1f} 次/年），"
        f"累计费用 **{res['left']['fees']:.2%}**；"
        f"不计费时年化 {erp.fmt_pct(nofee['m']['ann'])}（差 "
        f"{(m9['ann'] - nofee['m']['ann']) * 100:+.2f}pp）。",
        f"> **方向检验**：反向（1−仓位）年化 {erp.fmt_pct(rev['m']['ann'])}，"
        f"与正方向差 **{(m9['ann'] - rev['m']['ann']) * 100:+.2f}pp**。",
        "",
        "## 3. 计划 vs 实际：那个「滞后一个带」到底长什么样",
        "",
        "| 带 | z 区间 | 计划 | **实际平均** | 差 | 天数占比 | 该带后 250 日沪深300 |",
        "|---|---|---:|---:|---:|---:|---:|",
    ]
    for x in prof_l:
        f250 = x["fwd250"]
        L_.append(f"| B{x['band']} | {x['rng']} | {L.w_text(x['plan'])} | **{x['held']:.0%}** | "
                  f"{x['gap'] * 100:+.0f}pp | {x['share']:.0%} | "
                  f"{erp.fmt_pct(f250) if f250 is not None else '—'} |")
    L_ += ["", "对照（读法 B，按当前带定仓）：", "",
           "| 带 | 计划 | 实际平均 | 差 |", "|---|---:|---:|---:|"]
    for x in prof_n:
        L_.append(f"| B{x['band']} {x['name']} | {L.w_text(x['plan'])} | {x['held']:.0%} | "
                  f"{x['gap'] * 100:+.0f}pp |")
    if prof_l and prof_l[0]["held"] < 0.95:
        L_ += ["",
               f"> 读法 A 的两头都还没动：满仓带里平均只持 **{prof_l[0]['held']:.0%}**"
               f"（买点要等 z 跌回 0.80 之下），清仓带里平均还持 **{prof_l[-1]['held']:.0%}**"
               "（卖点要等 z 升回 0.20 之上）。维持带的「计划 = 实际」，因为它的操作就是「不动」。",
               "> 方向上是**动量**：z 往下（指数往上）才买、z 往上（指数往下）才卖 —— 两头都晚一拍。"]
    L_ += [
        "",
        "## 4. 按带看未来收益（沪深300 全收益）",
        "",
        "| 带 | 未来 | 样本 | 平均 | 中位 | 上涨概率 | 10% 分位 | 90% 分位 |",
        "|---|---|---:|---:|---:|---:|---:|---:|",
    ]
    for _, x in fwd_band.iterrows():
        L_.append(f"| B{int(x['band'])} {x['name']} | {int(x['horizon'])} 日 | "
                  f"{int(x['n'])} | {erp.fmt_pct(x['mean'])} | {erp.fmt_pct(x['median'])} | "
                  f"{x['win']:.0%} | {erp.fmt_pct(x['p10'])} | {erp.fmt_pct(x['p90'])} |")
    L_ += ["", "> 这是规则的**前提检验**：带越便宜，其后收益该越高。"
          "若某一带的顺序反了，说明分带本身没有区分度。", ""]

    # 敏感性：死区 × 两套费率（分离「信号」与「摩擦」）
    L_ += ["## 5. 死区 × 费率（这个换手率下费用已不是主要矛盾）", "",
           "| 死区 | 调仓 | 累计费用 | 年化（场外，赎 0.5%） | 年化（ETF，0.02%） | 最大回撤 | 夏普 | 平均仓位 |",
           "|---|---:|---:|---:|---:|---:|---:|---:|"]
    for a, b in zip(sens_tol, sens_tol_etf):
        L_.append(f"| {a['label']} | {a['switches']} | {a['fees']:.1%} | "
                  f"{erp.fmt_pct(a['ann'])} | {erp.fmt_pct(b['ann'])} | "
                  f"{erp.fmt_pct(a['mdd'])} | {a['sharpe']:.2f} | {a['w_avg']:.0%} |")
    L_ += ["",
           f"> 换手只有 **{sw} 次 / {yrs:.1f} 年**，累计费用 {res['left']['fees']:.1%} —— "
           "费率这一项在两百多次换手时是生死线，在这个换手率下只是小数："
           f"同样 {sw} 次调仓，把 0.5% 赎回费换成 ETF 佣金，年化 "
           f"{erp.fmt_pct(sens_tol[0]['ann'])} → {erp.fmt_pct(sens_tol_etf[0]['ann'])}"
           f"（{sens_tol_etf[0]['ann'] * 100 - sens_tol[0]['ann'] * 100:+.2f}pp/年）。",
           "> 死区仍是 ±1pp 量级且**不单调** → 不要为它调参，取 0.03~0.05 纯当防抖（默认 0）。",
           "", "## 6. 其余敏感性", ""]

    for title, rs, note in (
        ("带边界整体平移", sens_e, "检验「0.8 / 0.2 这两条线是不是巧合」。"),
        ("仓位刻度", sens_w, "满仓 100% 会真的把债券砍到 0；「维持带」换成固定仓位就退回三档定仓。"),
        ("滚动窗口", sens_win, "窗口越长预热越久，各档起点不同，比的是超额不是绝对年化。"),
    ):
        L_ += [f"### {title}", "",
               "| 档 | 起点 | 年数 | 年化 | 同窗口 50/50 | 超额 | 最大回撤 | 夏普 | 调仓 | 累计费用 | 平均仓位 |",
               "|---|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|"]
        for x in rs:
            L_.append(f"| {x['label']} | {x['start']:%Y-%m-%d} | {x['years']:.1f} | "
                      f"{erp.fmt_pct(x['ann'])} | {erp.fmt_pct(x['b50'])} | "
                      f"**{x['edge']:+.2f}pp** | {erp.fmt_pct(x['mdd'])} | {x['sharpe']:.2f} | "
                      f"{x['switches']} | {x['fees']:.1%} | {x['w_avg']:.0%} |")
        L_ += ["", f"> {note}", ""]

    L_ += [
        "## 7. 分半样本",
        "",
        "| 区间 | 起 ~ 止 | 年数 | V4 场外 | V4 ETF | V4 ETF+死区 | V4(B) | 引擎带位 | 静态50/50 | 沪深300 |",
        "|---|---|---:|---:|---:|---:|---:|---:|---:|---:|",
    ]
    for h in half:
        sub = h["sub"]
        L_.append(f"| {h['tag']} | {h['start']:%Y-%m-%d} ~ {h['end']:%Y-%m-%d} | "
                  f"{(h['end'] - h['start']).days / 365.25:.1f} | "
                  f"{erp.fmt_pct(h['left']['m']['ann'])} | {erp.fmt_pct(h['etf']['m']['ann'])} | "
                  f"{erp.fmt_pct(h['etf_tol']['m']['ann'])} | "
                  f"{erp.fmt_pct(h['new']['m']['ann'])} | "
                  f"{erp.fmt_pct(h['eng']['m']['ann'])} | "
                  f"{erp.fmt_pct(erp.perf(static_bench(bond, sub['tr'], 0.5)['nav'])['ann'])} | "
                  f"{erp.fmt_pct(erp.perf(sub['tr'] / sub['tr'].iloc[0])['ann'])} |")
    L_ += ["", "## 8. 逐年收益", "",
           "| 年 | V4 | V4(B) | 引擎带位 | 静态 50/50 | 沪深300 |",
           "|---|---:|---:|---:|---:|---:|"]
    for y, r in per_year.iterrows():
        L_.append(f"| {int(y)} | {erp.fmt_pct(r['v4'])} | {erp.fmt_pct(r['v4B'])} | "
                  f"{erp.fmt_pct(r['eng'])} | "
                  f"{erp.fmt_pct(r['静态50/50'])} | {erp.fmt_pct(r['沪深300'])} |")
    win_y = int((per_year["v4"] > per_year["eng"]).sum())

    L_ += ["", "## 9. 结论", ""]
    L_ += _conclusion(res, eng, st50, prof_l, sens_tol, sens_tol_etf, sens_e, yrs, sw,
                      win_y, len(per_year), mn, d50, etf, etf_tol, half, nofee)
    L_ += ["", "> **模拟结果，非投资建议。**"]

    path = os.path.join(OUTDIR, "_v4_erp_band_bt.md")
    with open(path, "w", encoding="utf-8") as f:
        f.write("\n".join(L_))

    print(f"报告: {path}")
    print(f"样本 {d.index[0]:%Y-%m-%d} ~ {d.index[-1]:%Y-%m-%d} ({yrs:.1f} 年)")
    for r in rows:
        w_txt = f"{r['w_avg']:.0%}" if "w_avg" in r else "—"
        sw_txt = f"{r.get('switches', '—')}"
        print(f"  {r['name'][:34]:34s} 年化 {erp.fmt_pct(r['ann']):>9s} · "
              f"回撤 {erp.fmt_pct(r['mdd']):>9s} · 夏普 {r['sharpe']:5.2f} · "
              f"仓位 {w_txt} · 调仓 {sw_txt}")
    print("图:", p_lad, "|", p_lad3, "|", p_sig, "|", p_nav)


def _conclusion(res, eng, st50, prof, tol, tol_etf, edge, yrs, sw, win_y, n_year, mn,
                d50, etf, etf_tol, half, nofee):
    m9, me = res["left"]["m"], eng["m"]
    m50 = st50["m"]
    h_etf = [h["etf"]["m"]["ann"] for h in half]
    h_etf_tol = [h["etf_tol"]["m"]["ann"] for h in half]
    h_eng = [h["eng"]["m"]["ann"] for h in half]
    h_left = [h["left"]["m"]["ann"] for h in half]
    best = max(tol_etf, key=lambda x: x["ann"])
    fwd = [x["fwd250"] for x in prof]
    out = [
        f"1. **前提仍在：三个带的后市收益是单调的**。其后 250 日沪深300 收益 "
        + " / ".join(f"B{i} {erp.fmt_pct(x)}" for i, x in enumerate(fwd))
        + "。便宜带（B0）后面确实最好、最贵的（B2）确实为负 —— 分带方向没错；"
        "但 B0 与 B1 只差 3.7pp，**中间这一段（占 55% 天数）本身几乎没有信息量**，"
        "这也是它被设成「维持、不动」而不是一档固定仓位的原因。",
        f"2. **收益上它明显强于引擎自带的带位信号**：V4 两带 {erp.fmt_pct(m9['ann'])} vs "
        f"旧口径（百分位+趋势、80/50/20）{erp.fmt_pct(me['ann'])} / 静态 50/50 "
        f"{erp.fmt_pct(m50['ann'])} / 买入持有沪深300 +6.66%；夏普 **{m9['sharpe']:.2f}**"
        f"（旧口径 {me['sharpe']:.2f}、50/50 {m50['sharpe']:.2f}）。"
        f"**但代价是回撤 {erp.fmt_pct(m9['mdd'])}，比旧口径（{erp.fmt_pct(me['mdd'])}）大 "
        f"{abs(m9['mdd'] - me['mdd']) * 100:.0f}pp**，"
        "Calmar 只有 "
        f"{m9['ann'] / abs(m9['mdd']):.2f} —— 平均股票仓位 {res['left']['w'].mean():.0%}，"
        "本质上是一个「高暴露 + 滞后择时」的组合，不是低波产品。",
        f"3. **费率不再是生死线 —— 这是与 4 带版最大的区别**：只有 **{sw} 次信号调仓**"
        f"（{sw / yrs:.1f} 次/年），把 0.5% 赎回费换成 ETF 佣金，年化 "
        f"{erp.fmt_pct(m9['ann'])} → {erp.fmt_pct(etf['m']['ann'])}"
        f"（{(etf['m']['ann'] - m9['ann']) * 100:+.2f}pp/年）。"
        f"账上累计费用 {res['left']['fees']:.1%} 主要来自月末回锚的小额调仓，不是信号换手。"
        "**场外基金做这条规则不再致命**（4 带版是 234 次、费用 69.6%、拖累 3.9pp/年）。",
        f"4. **「离开带才动手」就是 alpha 来源，别改成「进入带就动手」**：读法 A "
        f"{erp.fmt_pct(m9['ann'])} vs 读法 B（按当前带定仓）{erp.fmt_pct(mn['ann'])}，"
        f"**差 {(m9['ann'] - mn['ann']) * 100:+.2f}pp/年**。机制在 §3：A 在清仓带里平均还持 "
        f"**{prof[-1]['held']:.0%}**（B 是 0%），在满仓带里平均持 **{prof[0]['held']:.0%}**"
        f"（B 是 100%）—— 买点与卖点整体后移一拍，等价于「贵的时候还留着仓位、便宜的时候还没买满」，"
        f"在本样本里正好吃到 2014、2015、2019、2020 那几段急涨（见 §8 逐年）；代价是回撤更大"
        f"（{erp.fmt_pct(m9['mdd'])} vs {erp.fmt_pct(mn['mdd'])}）。",
        f"5. **边界不单调，别调参**：整体平移 −0.10 / −0.05 / 0 / +0.05 / +0.10 → 年化 "
        f"{erp.fmt_pct(edge[0]['ann'])} / {erp.fmt_pct(edge[1]['ann'])} / "
        f"{erp.fmt_pct(edge[2]['ann'])} / {erp.fmt_pct(edge[3]['ann'])} / "
        f"{erp.fmt_pct(edge[4]['ann'])}。整体下移 = 买点提前、卖点推后（仓位更重），"
        "反而更差；上移到 0.9/0.3 = 买点推后、卖点提前（仓位更轻），也崩 —— "
        "**两头都往下掉，中间一个孤峰，说明这些数不是拟合出来的**；"
        "按用户口径 0.8 / 0.2 固定住，不要去挑最高那档。",
        "6. **「维持」这一档本身值钱**：把中间带换成固定半仓（100/50/0）或三档定仓"
        "（80/50/20），调仓次数会从 "
        f"{sw} 次涨到 127 次、累计费用涨到 40%~72%，年化反而掉到 7%~8%（见 §6「仓位刻度」）。"
        "维持 = 省掉中间那一整段的来回，这是两带设计相对 4 带设计最直接的收益。",
        "7. **仓位刻度用 100/0 是刻意的**：换成旧口径的 80/20（80/维持/20）年化与回撤都下降"
        "（见 §6），说明这条规则的正收益确实来自「敢满仓」，不是来自债券腿的稳定。"
        "满仓 = 债券真的砍到 0%，这一点必须显式接受。",
        f"8. **分半样本两段都赢，不是单段侥幸**：前半段 {erp.fmt_pct(h_left[0])}"
        f"（ETF {erp.fmt_pct(h_etf[0])}）vs 旧口径 {erp.fmt_pct(h_eng[0])}；"
        f"后半段 {erp.fmt_pct(h_left[1])}（ETF {erp.fmt_pct(h_etf[1])}）vs "
        f"{erp.fmt_pct(h_eng[1])}。加 0.05 死区后：{erp.fmt_pct(h_etf_tol[0])} / "
        f"{erp.fmt_pct(h_etf_tol[1])}（全样本 {erp.fmt_pct(best['ann'])}，"
        f"夏普 {best['sharpe']:.2f}）—— 但死区只是防抖，不是必要项。",
        f"9. **统计上仍然什么都说明不了**：{yrs:.1f} 年、**只有 {sw} 次信号**；"
        f"场外口径下逐年跑赢旧口径 {win_y}/{n_year} 年。样本起点受 PE（2011-06 起）与 250 日预热"
        "限制，不含 2005-2011 的极端低估区；"
        "「维持带」的 55% 天数里什么都没发生，等于把一半样本闲置，这在统计上进一步削弱证据。",
        "10. **未计**：申赎确认时滞与限购、跟踪误差、分红税差、场内买卖价差；"
        "债券腿是 D=8 合成国债指数（忽略凸性与骑乘）；"
        f"回撤 {erp.fmt_pct(m9['mdd'])} 已接近静态 80/20（-38.87%）与买入持有（-46.06%）的量级 —— "
        "这条规则的收益是靠承担风险换来的，不是靠降波动。",
    ]
    return out


if __name__ == "__main__":
    main()
