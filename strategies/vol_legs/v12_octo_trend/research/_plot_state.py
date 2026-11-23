# -*- coding: utf-8 -*-
"""V12 · 八态图（逐腿价态图 + 七腿总览）。

版式对齐 v13 的 `research/_plot_grid.py`（上价格 / 中许可 / 下面板色带），多出来的一层是
**八态色带** —— 八态是这条策略唯一的信息源，必须能一眼看出「哪段是急涨、哪段是慢跌」。

产物（本目录；`--years N` 时文件名加 `_{N}y` 后缀，**不覆盖**全样本那张）：
- `_v12_state_{leg}.png`   逐腿：价格 + 三均线 + 八态色带 + 许可/实际仓位 + 操作点
                           （默认画 `rule.CORE_LEGS` 全 7 腿）
- `_v12_grid_all.png`      七腿总览：每腿一格，八态净值 vs 同窗口恒持（起点归 1）
- `_v12_states.png`        沪深300 的**八态后验**（占比 + fwd20 + 胜率），规格 §3 的可视化版
                           （恒为全样本，`--years` 不影响它）

`--years 5` 只画**末端 5 年**。状态、仓位、净值与均线仍在**全样本**上算（窗口首日的态与仓位
是真实值，不是从空仓重新起算），只是截取窗口再画；窗口首日净值归 1，所以图里看到的是
「这 5 年」的收益，不是从 2006 年累积过来的净值。样本本身不足 N 年的腿（科创50 2021 起、
恒生科技 2020-07 起）照画，标题与图注会写明「本图即全部样本」。

用法：
    python strategies/vol_legs/v12_octo_trend/research/_plot_state.py              # 全样本
    python strategies/vol_legs/v12_octo_trend/research/_plot_state.py --years 5    # 近 5 年
    python strategies/vol_legs/v12_octo_trend/research/_plot_state.py --leg kc50
    python strategies/vol_legs/v12_octo_trend/research/_plot_state.py --no-grid    # 只要逐腿图
"""
import argparse
import os
import sys
import textwrap

import numpy as np
import pandas as pd

try:
    sys.stdout.reconfigure(encoding="utf-8")  # type: ignore[attr-defined]
except Exception:  # noqa: BLE001
    pass

_HERE = os.path.dirname(os.path.abspath(__file__))
_V12 = os.path.dirname(_HERE)
if _V12 not in sys.path:
    sys.path.insert(0, _V12)

import rule           # noqa: E402
import data as vdata  # noqa: E402
import engine         # noqa: E402

C_PRICE, C_MA20, C_MA60, C_MA250 = "#1b2b3a", "#1565c0", "#ef6c00", "#6d4c41"
C_UP, C_DOWN, C_IDX = "#2e7d32", "#c62828", "#5b6b7a"
C_NAV, C_HOLD = "#7b52ab", "#8a8a8a"

# 八态配色：上涨侧绿系、下跌侧红系、回调/反弹给中间色（一眼能区分"趋势"与"逆势"）
STATE_COLOR = {
    "急涨": "#1b5e20",
    "慢涨": "#81c784",
    "弱回调": "#00897b",
    "强回调": "#f9a825",
    "急跌": "#b71c1c",
    "慢跌": "#ef9a9a",
    "弱反弹": "#5c6bc0",
    "强反弹": "#26c6da",
}


def _setup():
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    plt.rcParams["font.sans-serif"] = ["Microsoft YaHei", "SimHei", "DejaVu Sans"]
    plt.rcParams["axes.unicode_minus"] = False
    return plt


def _slice_start(r, years=None):
    """绘图窗口的起始 bar：全样本 = 第一根出状态的 bar；`years` > 0 = 末端 N 年（不早于前者）。"""
    idx = r["df"].index
    st = r["start"]
    if not years:
        return st
    from_ = idx[-1] - pd.DateOffset(days=int(round(float(years) * 365.25)))
    cand = idx[min(int(idx.searchsorted(from_)), max(len(idx) - 2, 0))]
    return max(pd.Timestamp(st), pd.Timestamp(cand))


def _win_note(years, idx):
    """-> (标题前缀, 图注补一句 | None)。样本不足 N 年时不再说「近 N 年」。"""
    if not years:
        return "", None
    yrs = (idx[-1] - idx[0]).days / 365.25
    if yrs < float(years) * 0.95:
        return "", f"该腿可用样本只有 {yrs:.1f} 年（不足 {years:g} 年），本图即全部样本。"
    return (f"近 {years:g} 年 · ",
            f"本图只画末端 {yrs:.1f} 年（{idx[0].date()} 起），窗口首日净值归 1；"
            "更早的历史见同目录里无该后缀的那张。")


def one(leg, r, years=None):
    plt = _setup()
    from matplotlib.lines import Line2D
    from matplotlib.patches import Patch

    df, ind, cx, w_full = r["df"], r["ind"], r["cx"], r["w"]
    idx = df.index[df.index >= _slice_start(r, years)]
    px = df["close"].loc[idx]
    w = w_full.loc[idx]
    st = cx["state"].loc[idx]
    reg = cx["regime_used"].loc[idx]
    # 操作点在**全样本**仓位序列上取，再按窗口过滤 —— 否则窗口首日会凭空多一个「调仓」标记
    acts = [(t, d) for t, d in engine.actions(w_full) if t >= idx[0]]
    win_pre, win_note = _win_note(years, idx)

    fig, (ax, ax2, ax3) = plt.subplots(
        3, 1, figsize=(14.6, 8.2), sharex=True,
        gridspec_kw=dict(height_ratios=[2.6, 0.95, 0.22], hspace=0.07))
    ylo, yhi = float(px.min()) * 0.96, float(px.max()) * 1.04

    ax.fill_between(idx, ylo, yhi, where=(reg == "向上").to_numpy(),
                    color=C_UP, alpha=0.06, step="mid")
    ax.fill_between(idx, ylo, yhi, where=(reg == "向下").to_numpy(),
                    color=C_DOWN, alpha=0.06, step="mid")
    ax.plot(idx, px.to_numpy(), color=C_PRICE, lw=1.15)
    for col, c, lw, lbl in (("ma_s", C_MA20, 0.85, f"MA{rule.MA_SHORT}"),
                            ("ma_m", C_MA60, 0.95, f"MA{rule.MA_MID}"),
                            ("ma_l", C_MA250, 1.15, f"MA{rule.MA_LONG}")):
        ax.plot(idx, ind[col].loc[idx].to_numpy(), color=c, lw=lw, label=lbl)

    buys = [(t, float(px.loc[t])) for t, d in acts if d > 0]
    sells = [(t, float(px.loc[t])) for t, d in acts if d < 0]
    if buys:
        ax.scatter([b[0] for b in buys], [b[1] for b in buys], marker="^", s=38,
                   color=C_UP, edgecolor="white", lw=0.5, zorder=5)
    if sells:
        ax.scatter([s[0] for s in sells], [s[1] for s in sells], marker="v", s=38,
                   color=C_DOWN, edgecolor="white", lw=0.5, zorder=5)
    ax.set_ylim(ylo, yhi)
    ax.set_ylabel("收盘（元）", fontsize=9.5)
    ax.grid(ls="--", lw=0.5, alpha=0.3)
    last_state = str(st.iloc[-1])
    ax.set_title(f"{rule.NAMES[leg]}（{rule.LEGS[leg][0]}）· {win_pre}"
                 f"{idx[0].date()} ~ {idx[-1].date()} · 当前 {last_state}"
                 f"（regime {cx['regime'].iloc[-1]}）\n"
                 f"许可 {float(w.iloc[-1]):.0%} · z20 {float(cx['z20'].iloc[-1]):+.2f}"
                 f" · rvol {float(cx['rvol'].iloc[-1]):.2f} · {rule.cap_version(leg)}",
                 fontsize=11.5, color="#1b2b3a", weight="bold", loc="left")

    handles = [Line2D([], [], color=C_PRICE, lw=1.15, label="收盘"),
               Line2D([], [], color=C_MA20, lw=0.85, label=f"MA{rule.MA_SHORT}"),
               Line2D([], [], color=C_MA60, lw=0.95, label=f"MA{rule.MA_MID}"),
               Line2D([], [], color=C_MA250, lw=1.15, label=f"MA{rule.MA_LONG}"),
               Line2D([], [], marker="^", ls="", color=C_UP, label="加仓"),
               Line2D([], [], marker="v", ls="", color=C_DOWN, label="减仓")]
    ax.legend(handles=handles, fontsize=8, frameon=False, ncol=6, loc="upper left")

    # 中格：八态许可（虚线）与**实际仓位**（实线，= min(许可, 波动层系数)）
    cap_w = engine.positions(cx["state"], cap_map=rule.cap_map(leg)) if rule.OPT else w
    ax2.step(idx, (cap_w.loc[idx] * 100).to_numpy(), where="post", color=C_IDX, lw=0.9,
             ls="--", label="八态许可")
    ax2.step(idx, (w * 100).to_numpy(), where="post", color=C_DOWN, lw=1.25, label="实际仓位")
    ax2.fill_between(idx, 0, (w * 100).to_numpy(), step="post", color=C_DOWN, alpha=0.10)
    ax2.set_ylim(-3, 108)
    ax2.set_yticks([0, 20, 50, 100])
    ax2.set_ylabel("许可 / 仓位（%）", fontsize=9.5)
    ax2.grid(ls="--", lw=0.5, alpha=0.3)
    ax2.legend(fontsize=8, frameon=False, ncol=2, loc="upper left")

    # 下格：八态色带（按连续同态分段画 axvspan —— 比 imshow 干净，不会把日期轴变成 0~n）
    runs, cur, t0 = [], None, None
    for t, s in zip(idx, st.to_numpy()):
        if s != cur:
            if cur is not None:
                runs.append((t0, t, cur))
            cur, t0 = s, t
    if cur is not None:
        runs.append((t0, idx[-1], cur))
    for a, b, s in runs:
        if s in STATE_COLOR:
            ax3.axvspan(a, b, color=STATE_COLOR[s], lw=0)
    ax3.set_xlim(idx[0], idx[-1])
    ax3.set_yticks([])
    ax3.set_ylim(0, 1)
    ax3.set_ylabel("八态", fontsize=9)
    ax3.legend(handles=[Patch(color=STATE_COLOR[s], label=f"{s} {rule.cap_of(s, leg=leg):.0%}")
                        for s in rule.STATE_ORDER],
               fontsize=7.4, frameon=False, ncol=8, loc="upper center",
               bbox_to_anchor=(0.5, -0.55))
    for a in (ax, ax2, ax3):
        for s in ("top", "right"):
            a.spines[s].set_visible(False)
    ax2.set_xlabel(f"绿底 = regime 向上 ｜ 红底 = regime 向下 ｜ 下格色带 = 八态（括号是许可上限）"
                   f" ｜ 量能：{vdata.vol_note(leg)}", fontsize=8.4, color=C_IDX)

    foot = (f"V12 八态趋势 · MA{rule.MA_SHORT}/{rule.MA_MID}/{rule.MA_LONG} · "
            f"z 阈值 ±{rule.Z_TH} · 量比 {rule.RVOL_HI:.2f}/{rule.RVOL_LO:.2f} · "
            f"多数决 {rule.VOTE_CONFIRM} 日 · 升格确认 {rule.UP_CONFIRM} 日 · "
            f"真实指数收盘（黄金 518880）· 机械规则输出，非投资建议")
    if win_note:
        foot = win_note + " " + foot
    fig.text(0.012, 0.012, foot, fontsize=7.6, color=C_IDX)
    fig.subplots_adjust(left=0.055, right=0.99, top=0.925, bottom=0.135)
    sfx = f"_{float(years):g}y" if years else ""
    out = os.path.join(_HERE, f"_v12_state_{leg}{sfx}.png")
    fig.savefig(out, dpi=125, facecolor="white")
    plt.close(fig)
    print("图:", out)


def grid(results, years=None):
    """七腿总览：每腿一格，八态净值 vs 同窗口恒持（都从窗口首日归 1）。

    存在的意义是**横向比**：逐腿图（`one`）看细节，这张看「哪条腿的八态层真的赚到了」。
    副轴不放仓位 —— 那是逐腿图的活；这里只在标题写 Δ夏普与平均许可。
    """
    plt = _setup()
    import matplotlib.dates as mdates

    legs = [l for l in rule.LEG_ORDER if l in results and results[l]["nav"] is not None]
    if not legs:
        return
    ncol = 4
    nrow = int(np.ceil(len(legs) / ncol))
    fig, axes = plt.subplots(nrow, ncol, figsize=(4.5 * ncol, 3.9 * nrow + 1.6))
    axes = np.atleast_1d(axes).ravel()

    rows, spans = [], []
    for ax, leg in zip(axes, legs):
        r = results[leg]
        st0 = _slice_start(r, years)
        px = r["df"]["close"].loc[st0:]
        nav = r["nav"].loc[st0:]
        nav = nav / nav.iloc[0]
        hold = px / px.iloc[0]
        w_win = r["w"].loc[st0:]
        p1, p2 = engine.perf(nav), engine.perf(hold)
        expo = float(w_win.mean())
        dsh = p1["sharpe"] - p2["sharpe"]
        rows.append((leg, p1, p2, dsh, expo))
        spans.append((st0, px.index[-1]))

        ax.plot(hold.index, hold.to_numpy(), color=C_HOLD, lw=1.05, label="恒持")
        ax.plot(nav.index, nav.to_numpy(), color=C_NAV, lw=1.45, label="八态")
        ax.axhline(1.0, color="#cccccc", lw=0.7, ls=":")
        ax.set_title(f"{rule.NAMES[leg]}（{rule.LEGS[leg][0]}）\n"
                     f"八态 {p1['ann']:+.1%} / 回撤 {p1['mdd']:.0%} / 夏普 {p1['sharpe']:.2f}\n"
                     f"恒持 {p2['ann']:+.1%} / 回撤 {p2['mdd']:.0%} / 夏普 {p2['sharpe']:.2f}"
                     f" · Δ夏普 {dsh:+.2f}",
                     fontsize=8.6, loc="left", color="#1b2b3a")
        ax.set_ylabel("净值（起点 = 1）", fontsize=8.2)
        ax.grid(ls="--", lw=0.5, alpha=0.3)
        ax.tick_params(labelsize=7.6)
        # 小格子里默认的 AutoDateLocator 会挤出 5~7 个年份把刻度叠在一起，按跨度定档
        span_y = (px.index[-1] - px.index[0]).days / 365.25
        ax.xaxis.set_major_locator(mdates.YearLocator(base=5 if span_y > 15 else (2 if span_y > 8 else 1)))
        ax.xaxis.set_major_formatter(mdates.DateFormatter("%Y"))
        ax.set_xlim(px.index[0], px.index[-1])
        for s in ("top", "right"):
            ax.spines[s].set_visible(False)
        if leg == legs[0]:
            ax.legend(fontsize=7.6, frameon=False, loc="upper left")

    # 空格子放「Δ夏普排序」，比留白有用
    spare = [a for a in axes[len(legs):]]
    if spare:
        ax = spare[0]
        ax.axis("off")
        rank = sorted(rows, key=lambda x: -x[3])
        lines = ["Δ夏普排序（八态 vs 恒持，同窗口）", ""]
        for i, (leg, p1, p2, dsh, expo) in enumerate(rank, 1):
            verdict = "净增益" if dsh >= 0.10 else ("≈噪声" if dsh >= 0 else "负贡献")
            lines.append(f"{i}. {rule.NAMES[leg]:6s} {dsh:+.2f}  许可 {expo:>3.0%}  {verdict}")
        ax.text(0.0, 1.0, "\n".join(lines), va="top", ha="left", fontsize=8.2,
                color="#1b2b3a", linespacing=1.75, transform=ax.transAxes)
        for a in spare[1:]:
            a.axis("off")

    first = min(s[0] for s in spans)
    last = max(s[1] for s in spans)
    win_pre, _ = _win_note(years, pd.DatetimeIndex([first, last]))
    fig.suptitle(f"V12 八态趋势 · 七腿总览（{win_pre}{first.date()} ~ {last.date()}，"
                 "各腿起点归 1；样本较短的腿从自身首根起）",
                 x=0.012, ha="left", y=0.985, fontsize=13.5,
                 color="#1b2b3a", weight="bold")
    cap_txt = " · ".join(f"{rule.NAMES[l]} {rule.cap_version(l)}" for l in legs)
    txt = "\n".join(textwrap.wrap(
        "生效口径（按腿）：" + cap_txt
        + " ｜ 空仓计货基 1.40%、摩擦单边 5bp、T+1 起承担仓位 ｜ 机械规则输出，非投资建议", 190))
    fig.text(0.012, 0.014, txt, fontsize=7.4, color=C_IDX, linespacing=1.5, va="bottom")
    fig.subplots_adjust(left=0.045, right=0.99, top=0.885, bottom=0.125,
                        hspace=0.52, wspace=0.24)
    sfx = f"_{float(years):g}y" if years else ""
    out = os.path.join(_HERE, f"_v12_grid_all{sfx}.png")
    fig.savefig(out, dpi=125, facecolor="white")
    plt.close(fig)
    print("图:", out)
    for leg, p1, p2, dsh, expo in sorted(rows, key=lambda x: -x[3]):
        print(f"  {rule.NAMES[leg]:8s} 八态 {p1['ann']:+7.2%}/年 夏普 {p1['sharpe']:5.2f}"
              f"  恒持 {p2['ann']:+7.2%}/年 夏普 {p2['sharpe']:5.2f}  Δ夏普 {dsh:+.2f}"
              f"  许可 {expo:.0%}")


def summary(leg, r):
    """八态后验图：占比（柱）+ fwd20（折线，右轴）+ 胜率标注。恒为全样本。"""
    plt = _setup()
    ss = engine.state_stats(r["df"]["close"].loc[r["start"]:], r["cx"].loc[r["start"]:])
    by = {x["state"]: x for x in ss["rows"]}
    states = [s for s in rule.STATE_ORDER if s in by]
    shares = [by[s]["share"] * 100 for s in states]
    fwd = [by[s]["fwd"] * 100 for s in states]
    win = [by[s]["win"] * 100 for s in states]

    fig, ax = plt.subplots(figsize=(12.6, 4.8))
    x = np.arange(len(states))
    ax.bar(x, shares, color=[STATE_COLOR[s] for s in states], alpha=0.85, width=0.62,
           label="占比（左轴）")
    ax.set_ylabel("占比（%）", fontsize=9.5)
    ax.set_xticks(x)
    ax.set_xticklabels([f"{s}\n许可 {rule.cap_of(s, leg=leg):.0%}" for s in states], fontsize=9)
    ax.set_ylim(0, max(shares) * 1.22)
    ax.grid(axis="y", ls="--", lw=0.5, alpha=0.3)
    for s_ in ("top", "right"):
        ax.spines[s_].set_visible(False)

    ax2 = ax.twinx()
    ax2.plot(x, fwd, color="#b71c1c", marker="o", lw=1.5, label="fwd20（右轴）")
    for i, v in enumerate(fwd):
        ax2.annotate(f"{v:+.2f}%", (i, v), textcoords="offset points", xytext=(0, 9),
                     ha="center", fontsize=8.4, color="#b71c1c")
    ax2.axhline(ss["base"] * 100, color=C_IDX, ls=":", lw=1.0)
    ax2.set_ylabel("fwd20（%）", fontsize=9.5, color="#b71c1c")
    ax2.set_ylim(min(fwd + [0]) - 0.8, max(fwd) + 1.2)
    ax2.tick_params(axis="y", colors="#b71c1c", labelsize=8.5)
    for s_ in ("top",):
        ax2.spines[s_].set_visible(False)
    ax2.text(len(states) - 0.5, ss["base"] * 100, f"基准 {ss['base'] * 100:+.2f}%",
             fontsize=8.2, color=C_IDX, ha="right", va="bottom")

    ax.set_title(f"V12 八态后验 · {rule.NAMES[leg]}（{r['start'].date()} ~ {r['end'].date()}，"
                 f"{ss['days']} 天）· 柱=占比 红点=fwd20 蓝字=胜率",
                 fontsize=12, color="#1b2b3a", weight="bold", loc="left")
    for i, w_ in enumerate(win):
        ax.text(i, 0.6, f"胜 {w_:.0f}%", ha="center", fontsize=7.8, color="white")
    fig.text(0.012, 0.015,
             "用途是验口径：四组配对（急涨−慢涨 / 弱回调−强回调 / 强反弹−弱反弹 / 急跌−慢跌）"
             "必须全为正，否则先查量能口径。",
             fontsize=8.0, color=C_IDX)
    fig.subplots_adjust(left=0.06, right=0.93, top=0.88, bottom=0.20)
    out = os.path.join(_HERE, "_v12_states.png")
    fig.savefig(out, dpi=125, facecolor="white")
    plt.close(fig)
    print("图:", out)


def main():
    ap = argparse.ArgumentParser(description="V12 八态单腿图 + 七腿总览")
    ap.add_argument("--leg", action="append", choices=list(rule.LEGS), help="只画指定腿（可重复）")
    ap.add_argument("--all", action="store_true", help="画全 7 腿（默认就是执行范围 = 全 7 腿）")
    ap.add_argument("--years", type=float, metavar="N",
                    help="只画末端 N 年（如 5）：文件名加 _Ny 后缀，不覆盖全样本那张")
    ap.add_argument("--no-grid", action="store_true", help="不画七腿总览（只要逐腿图）")
    args = ap.parse_args()

    avail = vdata.available()
    pool = avail if args.all else [l for l in rule.CORE_LEGS if l in avail]
    pick = args.leg or pool
    results = {}
    for leg in pick:
        r = engine.backtest_leg(leg)
        if r["start"] is None:
            print(f"  {rule.NAMES[leg]}: 预热不足，跳过")
            continue
        results[leg] = r
        one(leg, r, years=args.years)
    # 八态后验图固定画**标定腿**（`rule.CALIB_LEG`），与执行范围解耦：
    # 执行范围一改（2026-11-18 扩到七腿），不能让它悄悄变成 CORE_LEGS[0] = 中证A500。
    core = rule.CALIB_LEG
    if core not in results:
        r = engine.backtest_leg(core)
        if r["start"] is not None:
            results[core] = r
    if core in results and results[core]["start"] is not None:
        summary(core, results[core])
    if not args.no_grid and len(results) > 1:
        grid(results, years=args.years)


if __name__ == "__main__":
    main()
