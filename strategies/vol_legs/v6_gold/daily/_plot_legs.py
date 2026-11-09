# -*- coding: utf-8 -*-
"""V6 黄金三口径 · 净值 + 操作点 + 仓位图（长期 / 近三年各一份）。

版式对齐 `v5_trend_gate/research/plot_trend/_plot_trend.py`：上格净值与规则线 + ▲▼ 操作点，
下格带内仓位阶梯，标题区放统计摘要，页脚写规则与口径。

三条口径各自的关键线：
- **A 长期配置**：MA250 年线；浅红底纹 = 保险丝生效（仓位被压在 50% 带）的区段。
- **B 控回撤**：无趋势线（它不做趋势判断），净值上只标操作点；浅蓝底纹 = 波动系数 < 1（在压仓）。
- **C 低仓位占坑**：三格 —— 净值 / MACD（DIF-DEA 柱 + 两条线）/ 仓位。

标的只有一个：黄金。主口径用场外 `000216` 复权净值（与 v5 的黄金腿同一序列），
C 另在场内 `518880` 上有更优成本（场外赎回费 0.50% 会吃掉大半），差别写在 C 图页脚。

产物（本目录 `daily/`）：
    _v6_gold_A.png / _v6_gold_A_3y.png
    _v6_gold_B.png / _v6_gold_B_3y.png
    _v6_gold_C.png / _v6_gold_C_3y.png

用法（从仓库根跑）：
    python strategies/vol_legs/v6_gold/daily/_plot_legs.py            # 用本地缓存
    python strategies/vol_legs/v6_gold/daily/_plot_legs.py --refresh  # 先刷场外净值
    python strategies/vol_legs/v6_gold/daily/_plot_legs.py --leg C --years 3

机械规则输出，非投资建议。
"""
import argparse
import os
import sys

import numpy as np
import pandas as pd

try:
    sys.stdout.reconfigure(encoding="utf-8")  # type: ignore[attr-defined]
except Exception:  # noqa: BLE001
    pass

_HERE = os.path.dirname(os.path.abspath(__file__))       # v6_gold/daily
_V6 = os.path.dirname(_HERE)                             # v6_gold
if _V6 not in sys.path:
    sys.path.insert(0, _V6)

import gold as G  # noqa: E402

C_PRICE, C_MA, C_BUY, C_SELL, C_STEP = "#1b2b3a", "#e07b39", "#d33", "#1f8a4c", "#7b8794"
C_ZERO, C_SIGNAL = "#9aa5b1", "#6b8cae"

SPEC = {
    "A": dict(title="A 长期配置",
              sub="满带持有 · 年线下只停加仓 · 60 日保险丝 · 回撤阶梯越跌越买",
              color="#c62828"),
    "B": dict(title="B 控回撤",
              sub="波动目标 15% / 120 日已实现波动 · 只压满带 · 不做趋势判断",
              color="#1565c0"),
    "C": dict(title="C 低仓位占坑",
              sub=f"MACD {G.MACD_FAST}/{G.MACD_SLOW} 金叉持有 / 死叉空仓 · 只用约一半资金",
              color="#2e7d32"),
}


# --------------------------------------------------------------------------
def _steps(ax, idx, y, color, label=None, ls="-", lw=1.2, z=3):
    ax.step(idx, y, where="post", color=color, lw=lw, ls=ls, zorder=z, label=label)


def plot(key, years=None, out_dir=_HERE, refresh=False):
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.dates as mdates
    import matplotlib.pyplot as plt
    from matplotlib.lines import Line2D
    from matplotlib.patches import Patch

    plt.rcParams["font.sans-serif"] = ["Microsoft YaHei", "SimHei", "DejaVu Sans"]
    plt.rcParams["axes.unicode_minus"] = False

    meta = SPEC[key]
    d = G.build("otc", refresh=refresh)
    px_full = d["px"]

    if key == "A":
        sm_full, st_full = d["A"], d["A"]["state"]
    elif key == "B":
        sm_full, st_full = d["B"], d["B"]["state"]
    else:
        sm_full, st_full = None, d["C"]

    start = d["start"][key]
    if years:                                   # 只截最近 N 年做近看
        i0 = px_full.index.searchsorted(px_full.index[-1] - pd.Timedelta(days=int(years * 365.25)))
        start = px_full.index[max(i0, px_full.index.get_loc(start), 0)]

    px = px_full.loc[start:]
    st = st_full.loc[start:]
    idx = px.index
    yrs = (idx[-1] - idx[0]).days / 365.25

    fee, slip = G.fee_of("otc")
    if years:                                   # 区间图：净值切片后归 1，档位接续历史
        nav = G.run_leg(st_full, px_full, fee, slip).loc[idx]
        nav = nav / nav.iloc[0]
        hold = px / px.iloc[0]
    else:
        nav = G.run_leg(st, px, fee, slip)
        hold = G.run_leg(pd.Series(1.0, index=px.index), px, fee, slip)
    pn, ph = G.perf(nav), G.perf(hold)
    ops = G.actions(st)
    expo = float(st.mean())

    # ---- 版面
    three = key == "C"
    fig = plt.figure(figsize=(13.2, 8.9 if three else 8.6))
    if three:
        gs = fig.add_gridspec(3, 1, height_ratios=[3.3, 1.15, 1.0], hspace=0.10,
                              left=0.058, right=0.975, top=0.792, bottom=0.185)
        ax = fig.add_subplot(gs[0])
        axm = fig.add_subplot(gs[1], sharex=ax)
        axp = fig.add_subplot(gs[2], sharex=ax)
    else:
        gs = fig.add_gridspec(2, 1, height_ratios=[4.0, 1.0], hspace=0.07,
                              left=0.058, right=0.975, top=0.792, bottom=0.165)
        ax = fig.add_subplot(gs[0])
        axm, axp = None, fig.add_subplot(gs[1], sharex=ax)

    # ---- 上格：净值 + 规则线 + 底纹
    if key == "A":
        ax.fill_between(idx, 0, 1, where=(st <= G.FLOOR + 1e-9),
                        transform=ax.get_xaxis_transform(), color="#c62828",
                        alpha=0.07, lw=0, zorder=0)
        ma = px_full.rolling(int(G.MA_WIN), min_periods=int(G.MA_WIN)).mean().loc[start:]
        ax.plot(idx, ma.to_numpy(), color=C_MA, lw=1.25, zorder=2)
    elif key == "B":
        sc = sm_full["vol_scale"].loc[start:]
        ax.fill_between(idx, 0, 1, where=(sc < 0.999).to_numpy(),
                        transform=ax.get_xaxis_transform(), color="#1565c0",
                        alpha=0.06, lw=0, zorder=0)
    hold_n = hold / hold.iloc[0]
    ax.plot(idx, hold_n.to_numpy(), color="#9aa5b1", lw=0.95, ls="--", zorder=2,
            label="恒持满带（对照）")
    ax.plot(idx, nav.to_numpy(), color=C_PRICE, lw=1.35, zorder=3, label="策略净值")

    lo = float(min(nav.min(), hold_n.min())) * 0.94
    hi = float(max(nav.max(), hold_n.max())) * 1.06
    ax.set_ylim(lo, hi)
    if hi / lo > 3.2:                            # 长牛段线性轴会压扁前几年，换对数
        ax.set_yscale("log")
        ax.set_ylim(lo, hi)
        ticks = [t for t in (1, 1.5, 2, 3, 4, 5, 6, 8) if lo <= t <= hi]
        ax.set_yticks(ticks)
        ax.set_yticklabels([f"{t:g}" for t in ticks])
        ax.set_ylabel("场外复权净值（对数轴）", fontsize=10)
    else:
        ax.set_ylabel("场外复权净值", fontsize=10)
    ax.grid(ls="--", lw=0.6, alpha=0.30, zorder=1)
    ax.tick_params(labelbottom=False)

    # ---- 操作点（C 太密，只标最近 12 个）
    show = ops if len(ops) <= 14 else ops[-12:]
    if show:
        bd = [o["date"] for o in show if o["kind"] == "buy"]
        sd = [o["date"] for o in show if o["kind"] == "sell"]
        if bd:
            ax.plot(bd, [float(nav.loc[b]) for b in bd], ls="none", marker="^", ms=7.5,
                    mfc="none", mec=C_BUY, mew=1.4, zorder=6)
        if sd:
            ax.plot(sd, [float(nav.loc[b]) for b in sd], ls="none", marker="v", ms=7.5,
                    mfc="none", mec=C_SELL, mew=1.4, zorder=6)
        # 文字标注：点数多时只标最近 6 个，避免挤成一团（符号仍全画）
        lab = show if len(show) <= 8 else show[-6:]
        for o in lab:
            up = o["kind"] == "buy"
            ax.annotate(f"{o['to']:.0%}", (o["date"], float(nav.loc[o["date"]])),
                        textcoords="offset points", xytext=(0, 9 if up else -15),
                        ha="center", fontsize=7.2, color=C_BUY if up else C_SELL, zorder=7)

    # ---- 中格（仅 C）：MACD
    if three:
        dif, dea = G.macd_lines(px_full)
        dif, dea = dif.loc[start:], dea.loc[start:]
        hist = (dif - dea)
        axm.bar(idx, hist.to_numpy(), width=1.4,
                color=np.where(hist.to_numpy() >= 0, "#c62828", "#1f8a4c"), alpha=0.70, lw=0)
        axm.plot(idx, dif.to_numpy(), color=C_MA, lw=1.25,
                 label=f"DIF(EMA{G.MACD_FAST}-EMA{G.MACD_SLOW})")
        axm.plot(idx, dea.to_numpy(), color=C_SIGNAL, lw=1.15, ls="--",
                 label=f"DEA(EMA{G.MACD_SIGNAL})")
        axm.axhline(0, color="#5b6b7a", lw=0.7)
        axm.set_ylabel("MACD", fontsize=9, color="#6b7a8c")
        axm.grid(ls="--", lw=0.55, alpha=0.30)
        axm.tick_params(axis="y", labelsize=8, colors="#6b7a8c")
        axm.tick_params(labelbottom=False)
        axm.legend(fontsize=8, loc="upper left", frameon=False, ncol=2)
        for s in ("top", "right"):
            axm.spines[s].set_visible(False)

    # ---- 下格：带内仓位
    if key == "B":
        _steps(axp, idx, sm_full["state_trend"].loc[start:].to_numpy(), "#b6c0cb",
               label="趋势档位（=满带）", ls="--", lw=0.9, z=2)
    axp.fill_between(idx, 0, st.to_numpy(), step="post", color=C_STEP, alpha=0.14, lw=0, zorder=1)
    _steps(axp, idx, st.to_numpy(), C_STEP, lw=1.2,
           label="带内仓位" if key != "B" else "实际仓位（× 波动系数）")
    axp.set_ylim(-0.02, 1.10)
    yt = [0.0, G.FLOOR, 1.0] if key != "B" else [0.0, G.HALF, G.FULL]
    axp.set_yticks(sorted(set(yt)))
    axp.set_yticklabels([(f"空仓 0%" if y < 1e-9 else f"{'半仓' if y < 0.99 else '满带'} {y:.0%}")
                         for y in sorted(set(yt))], fontsize=8.2, color="#6b7a8c")
    axp.set_ylabel("带内仓位", fontsize=9, color="#6b7a8c")
    axp.grid(ls="--", lw=0.55, alpha=0.30)
    axp.tick_params(axis="y", colors="#6b7a8c", length=2)
    for a in (ax, axp) + ((axm,) if three else ()):
        for s in ("top", "right"):
            a.spines[s].set_visible(False)
    if years:
        axp.xaxis.set_major_locator(mdates.MonthLocator(interval=6))
        axp.xaxis.set_major_formatter(mdates.DateFormatter("%Y-%m"))
    else:
        axp.xaxis.set_major_locator(mdates.YearLocator())
        axp.xaxis.set_major_formatter(mdates.DateFormatter("%Y"))
    axp.set_xlabel("成交日 = 信号次日（T+1 净值）", fontsize=9, color="#5b6b7a")

    # ---- 图例
    hd = [Line2D([], [], color=C_PRICE, lw=1.35, label="策略净值（扣真实申赎费 + 滑点）"),
          Line2D([], [], color="#9aa5b1", lw=0.95, ls="--", label="恒持满带（对照）")]
    if key == "A":
        hd.append(Line2D([], [], color=C_MA, lw=1.25, label=f"MA{G.MA_WIN} 年线"))
    hd += [
        Line2D([], [], ls="none", marker="^", ms=8, mfc="none", mec=C_BUY,
               label=f"加仓 ▲（{sum(1 for o in ops if o['kind'] == 'buy')}）"),
        Line2D([], [], ls="none", marker="v", ms=8, mfc="none", mec=C_SELL,
               label=f"减仓 ▼（{sum(1 for o in ops if o['kind'] == 'sell')}）"),
        Line2D([], [], color=C_STEP, lw=1.2, label="带内仓位（下格实线）"),
    ]
    if key == "A":
        hd.append(Patch(facecolor="#c62828", alpha=0.20, label="保险丝生效（上限 50% 带）"))
    if key == "B":
        hd.append(Patch(facecolor="#1565c0", alpha=0.18, label="波动系数 < 1（在压仓）"))
        hd.append(Line2D([], [], color="#b6c0cb", lw=0.9, ls="--", label="趋势档位（=满带）"))
    if key == "C":
        hd.append(Line2D([], [], color=C_MA, lw=1.0, label=f"DIF（中格）"))
        hd.append(Line2D([], [], color=C_SIGNAL, lw=1.0, label=f"DEA（中格）"))
    ax.legend(handles=hd, loc="lower left", bbox_to_anchor=(0.0, 1.005, 1.0, 0.10),
              ncol=min(len(hd), 5), mode="expand", fontsize=8.2, frameon=False,
              columnspacing=1.0, handletextpad=0.5, borderaxespad=0.2)

    # ---- 标题
    win_txt = f"近 {years:g} 年 · " if years else ""
    fig.text(0.045, 0.972, f"黄金（{G.EXEC_CODE}）· {meta['title']} · {meta['sub']}",
             ha="left", fontsize=13.2, color="#1b2b3a", weight="bold")
    fig.text(0.045, 0.935,
             f"{idx[0].date()} ~ {idx[-1].date()}（{yrs:.1f} 年）· {win_txt}"
             f"策略 {pn['ann']:+.2%}/年、回撤 {pn['mdd']:.2%}、夏普 {pn['sharpe']:.2f}、"
             f"平均仓位 {expo:.0%}、调仓 {len(ops)} 次"
             f"   |   恒持满带 {ph['ann']:+.2%}/年、回撤 {ph['mdd']:.2%}、夏普 {ph['sharpe']:.2f}",
             ha="left", fontsize=10, color="#3d4f5f")

    # ---- 页脚
    note = []
    if key == "A":
        note.append(f"规则：起手满带；close < MA{G.MA_WIN} 只停加仓不清仓；"
                    f"连续在年线下方满 {G.DEGRADE_DAYS} 个交易日 → 目标带上限降到 "
                    f"{G.FLOOR:.0%}（保险丝），收复年线后次日回带。")
        note.append(f"回撤阶梯：dd = 1 − close/近 {G.NEW_HIGH_WIN} 日最高，"
                    f"dd ≥ {'/'.join('%g' % x for x in G.LADDER_DD)}% 且 close ≥ "
                    f"MA{G.MA_WIN} 时补 {'/'.join('%g' % x for x in G.LADDER_UNITS)} 份"
                    f"（1 份 = 带的 {G.UNIT_FRAC:.0%}）；同一档两次补仓间隔 ≥ {G.ACTION_GAP_A} 个交易日。")
    elif key == "B":
        note.append(f"规则：vol = std(日收益, {G.VOL_WINDOW}) × √252；系数 = clip("
                    f"{G.VOL_TARGET:.0%} / vol, {G.VOL_MIN_LEVEL:g}, 1)，只压满带。"
                    f"系数变化 < {G.VOL_DEADBAND:.0%} 不动，两次变化间隔 ≥ {G.VOL_MIN_GAP} 个交易日。")
        note.append("这条口径**不做趋势判断**：它买的是「已实现波动冲高时把仓位压下去」，"
                    "天然在暴跌里生效；代价是急涨年份会少赚。不是止损，也不是择时。")
    else:
        note.append(f"规则：DIF = EMA{G.MACD_FAST} − EMA{G.MACD_SLOW}、DEA = EMA{G.MACD_SIGNAL}(DIF)；"
                    f"金叉持有、死叉空仓，不看零轴；预热 {G.C_WARMUP} 个交易日"
                    f"（EMA{G.MACD_SLOW} 初始值需长预热才收敛）。")
        note.append("注意换手：场外 A 类赎回费 0.50% 会吃掉大半超额 —— 本图按场外真实费率画，"
                    "同一信号放场内 518880（万 0.3 佣金 + 0.05% 滑点）年化 +7.49%，"
                    "场外只有 +5.07%（见 `backtest/_v6_gold_bt.md` §4）。这条口径**只在场内执行**。")
    note.append(f"成本 = |Δ仓位| × 费率，场外申 {G.FEE_OTC[0]:.2%} / 赎 {G.FEE_OTC[1]:.2%} "
                f"+ 单边滑点 {G.SLIP:.2%}；空仓按货基 {G.CASH_ANNUAL:.2%} 计息；"
                "决策在 T 日盘后、T+1 收盘成交，无前视。")
    if years:
        note.insert(1, f"图为最近 {years:g} 年切片：仓位自更早历史延续（不在窗口首日重置），"
                       "净值按该区间重新归 1，年化 / 回撤 / 夏普只对该区间有效，与全历史图不可直接比较。")
    note.append("黄金没有现金流锚，价格是唯一信息源。机械规则输出，非投资建议。")
    fig.text(0.045, 0.014, "\n".join(note), fontsize=8.3, color="#5b6b7a",
             linespacing=1.5, va="bottom")

    out = os.path.join(out_dir, f"_v6_gold_{key}{f'_{years:g}y' if years else ''}.png")
    fig.savefig(out, dpi=135, facecolor="white")
    plt.close(fig)

    print(f"图: {out}")
    print(f"  {meta['title']} {idx[0].date()} ~ {idx[-1].date()}（{yrs:.1f} 年）· "
          f"{pn['ann']:+.2%}/年 回撤 {pn['mdd']:.2%} 夏普 {pn['sharpe']:.2f} "
          f"均仓位 {expo:.0%} 调仓 {len(ops)} 次 | 恒持 {ph['ann']:+.2%}/年")
    return out


def main():
    ap = argparse.ArgumentParser(description="V6 黄金三口径净值图")
    ap.add_argument("--leg", choices=list(SPEC), action="append", help="只画指定口径（可重复）")
    ap.add_argument("--years", type=float, action="append",
                    help="额外出近 N 年版（可重复，如 --years 3）")
    ap.add_argument("--refresh", action="store_true", help="先刷场外净值（否则只用缓存）")
    args = ap.parse_args()

    for key in (args.leg or list(SPEC)):
        plot(key, refresh=args.refresh)
        for y in (args.years or []):
            plot(key, years=y, refresh=args.refresh)


if __name__ == "__main__":
    main()
