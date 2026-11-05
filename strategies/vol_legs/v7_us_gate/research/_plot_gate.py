# -*- coding: utf-8 -*-
"""V7（美股两腿年线闸门）· 年线 + 操作点单腿图：纳指100 / 标普500。

图的读法（与 `v5_trend_gate/research/_plot_trend.py` 同一版式，便于并排比较）：
- **上格深色线** = 场外 QDII 复权净值（含分红再投 / 汇率 / 跟踪误差），这是你真能拿到的收益；
- **橙线** = MA200 年线（画在**美元指数**上再投影到净值坐标系会失真，所以橙线画在净值的年线上，
  而**决策用的是美元指数**的 MA200 —— 两者驱动同一段行情，看图形状一致，数值以报告为准）；
- **底色** = 当天美元指数在年线上（浅绿）/ 年线下（浅红）；
- **▼/▲ 标在价格线上** = 该笔调仓的**成交日**（月末盘后决策 → 次一交易日成交）的净值点位；
  只有 `|Δ仓位| ≥ 30pp` 的大动作才标文字（波动层下每月都在微调，全标会糊成一片）；
  **实心加大的 ▼** = 由「深破位免确认」（首次破位即低于年线 `ug.DEEP` 以上）触发的减仓；
- **下格** = 带内仓位（左轴，占本腿目标带的比例）＋ 波动层右轴（橙线 vol20、虚线 = 目标波动 TV）：
  开波动层时仓位是连续的，`仓位 ≈ 闸门 × TV / vol20` 这条镜像关系一眼可见。

产物（本目录）：
- `_v7_gate_ndx.png` / `_v7_gate_spx.png`

用法：
- `python _plot_gate.py`            → 两腿都画
- `python _plot_gate.py --leg ndx`  → 只画纳指

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

_HERE = os.path.dirname(os.path.abspath(__file__))
_V7 = os.path.dirname(_HERE)
sys.path.insert(0, _V7)

import us_gate as ug  # noqa: E402

C_PRICE, C_MA, C_BUY, C_SELL, C_STEP = "#1b2b3a", "#e07b39", "#d33", "#1f8a4c", "#7b8794"
C_UP, C_DOWN = "#2e7d32", "#c62828"
C_VOL = "#b5651d"


def plot(leg, out_dir=_HERE):
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.dates as mdates
    import matplotlib.pyplot as plt
    from matplotlib.lines import Line2D
    from matplotlib.patches import Patch

    plt.rcParams["font.sans-serif"] = ["Microsoft YaHei", "SimHei", "DejaVu Sans"]
    plt.rcParams["axes.unicode_minus"] = False

    cfg = ug.rule.LEGS[leg]
    p = ug.params()
    nav, st, dec = ug.build(leg)
    sig = ug.load_signal(leg)
    ma_sig = sig.rolling(ug.MA_WIN, min_periods=ug.MA_WIN).mean()
    _a = (sig > ma_sig).astype("float64").reindex(nav.index).ffill()
    above = _a.fillna(1.0) > 0.5                      # 预热期按「在年线上」画底色

    fee = (ug.COST, ug.COST)
    nv = ug.run_leg(st, nav, cost=fee[0])
    hold = ug.run_leg(pd.Series(1.0, index=nav.index), nav, cost=fee[0])
    pn, ph = ug.perf(nv), ug.perf(hold)
    ops = ug.actions(st)
    idx = nav.index
    yrs = (idx[-1] - idx[0]).days / 365.25
    floor = ug.floor_for(leg)

    ma_nav = nav.rolling(ug.MA_WIN, min_periods=ug.MA_WIN).mean()
    lo = float(min(nav.min(), ma_nav.min())) * 0.94
    hi = float(max(nav.max(), ma_nav.max())) * 1.06

    has_vol = float(p["TV"] or 0) > 0
    fig = plt.figure(figsize=(13.2, 9.0))
    if has_vol:
        gs = fig.add_gridspec(3, 1, height_ratios=[4.0, 0.95, 0.78], hspace=0.13,
                              left=0.082, right=0.952, top=0.792, bottom=0.175)
        ax = fig.add_subplot(gs[0])
        axp = fig.add_subplot(gs[1], sharex=ax)
        axv = fig.add_subplot(gs[2], sharex=ax)
        ax.tick_params(labelbottom=False)
        axp.tick_params(labelbottom=False)
    else:
        gs = fig.add_gridspec(2, 1, height_ratios=[4.0, 1.0], hspace=0.07,
                              left=0.082, right=0.975, top=0.792, bottom=0.175)
        ax = fig.add_subplot(gs[0])
        axp = fig.add_subplot(gs[1], sharex=ax)
        axv = None

    # ---- 上格：年线 + 上/下底色 + 操作点
    up = above.to_numpy()
    ax.fill_between(idx, lo, hi, where=up, color=C_UP, alpha=0.09, lw=0, zorder=0)
    ax.fill_between(idx, lo, hi, where=~up, color=C_DOWN, alpha=0.09, lw=0, zorder=0)
    ax.plot(idx, ma_nav.to_numpy(), color=C_MA, lw=1.25, zorder=2)
    ax.plot(idx, nav.to_numpy(), color=C_PRICE, lw=1.3, zorder=3)
    ax.set_ylim(lo, hi)
    if hi / lo > 4.0:
        ax.set_yscale("log")
        ax.set_ylim(lo, hi)
        ticks = [t for t in (0.3, 0.4, 0.5, 0.6, 0.8, 1, 1.2, 1.5, 2, 2.5, 3, 4, 5, 6, 8, 10, 12, 15)
                 if lo <= t <= hi]
        ax.set_yticks(ticks)
        ax.set_yticklabels([f"{t:g}" for t in ticks])
        ax.set_ylabel("场外复权净值（对数轴）", fontsize=10)
    else:
        ax.set_ylabel("场外复权净值", fontsize=10)
    ax.grid(ls="--", lw=0.6, alpha=0.30, zorder=1)
    ax.tick_params(labelbottom=False)

    deep_dates = ug.deep_exit_dates(dec, idx)
    if ops:
        bd = [o["date"] for o in ops if o["kind"] == "buy"]
        sd = [o["date"] for o in ops if o["kind"] == "sell"]
        sd_d = [d for d in sd if d in deep_dates]          # 深破位免确认触发的减仓
        sd_n = [d for d in sd if d not in deep_dates]
        ax.plot(bd, [float(nav.loc[d]) for d in bd], ls="none", marker="^", ms=7.5,
                mfc="none", mec=C_BUY, mew=1.4, zorder=6)
        ax.plot(sd_n, [float(nav.loc[d]) for d in sd_n], ls="none", marker="v", ms=7.5,
                mfc="none", mec=C_SELL, mew=1.4, zorder=6)
        ax.plot(sd_d, [float(nav.loc[d]) for d in sd_d], ls="none", marker="v", ms=10.5,
                mfc=C_SELL, mec="#0b3d22", mew=1.2, zorder=7)
        for o in ops:                                   # 小动作（<30pp）不标字，免得糊成一片
            if abs(o["to"] - o["frm"]) < 0.30:
                continue
            up_o = o["kind"] == "buy"
            ax.annotate(f"{ug.level_name(o['to'])}",
                        (o["date"], float(nav.loc[o["date"]])), textcoords="offset points",
                        xytext=(0, 10 if up_o else -16), ha="center", fontsize=7.2,
                        color=C_BUY if up_o else C_SELL, zorder=7)

    # 桥接段（标普：050025 -> 017641）
    cut = None
    if cfg.get("otc_proxy"):
        _cut = pd.Timestamp(cfg.get("otc_start"))
        if idx[0] < _cut < idx[-1]:
            cut = idx[idx.searchsorted(_cut)]
            ax.axvspan(idx[0], cut, color="#9aa5b1", alpha=0.10, lw=0, zorder=1)
            ax.axvline(cut, color="#7b8794", lw=0.9, ls="--", zorder=2)
            ax.text(cut, 0.975, f"{cut.date()} 起为 {cfg['otc']} 真实净值"
                                f"（此前 {cfg.get('otc_proxy')} 桥接） ",
                    transform=ax.get_xaxis_transform(), va="top", ha="right",
                    fontsize=8.3, color="#5b6b7a")

    # ---- 下格：仓位阶梯
    axp.step(idx, st.to_numpy(), where="post", color=C_STEP, lw=1.15)
    axp.fill_between(idx, 0, st.to_numpy(), step="post", color=C_STEP, alpha=0.14, lw=0)
    axp.set_ylim(-0.05, 1.12)
    ticks = sorted({0.0, floor, 1.0})
    axp.set_yticks(ticks)
    axp.set_yticklabels([f"{t:.0%}" for t in ticks], fontsize=8.2, color="#6b7a8c")
    axp.set_ylabel("")
    axp.grid(ls="--", lw=0.55, alpha=0.30)
    axp.tick_params(axis="y", colors="#6b7a8c", length=2)
    for a in (ax, axp):
        for s in ("top", "right"):
            a.spines[s].set_visible(False)

    # ---- 第三格：波动层（vol20 vs 目标波动 TV）
    if axv is not None:
        v = ug.vol_series(sig, p).reindex(idx).ffill()
        axv.plot(idx, v.to_numpy(), color=C_VOL, lw=1.0, alpha=0.9, zorder=3)
        axv.axhline(float(p["TV"]), color=C_VOL, lw=1.0, ls="--", alpha=0.9, zorder=2)
        vmax = float(np.nanmax(v.to_numpy())) if np.isfinite(np.nanmax(v.to_numpy())) else 0.6
        axv.set_ylim(0.0, max(0.45, vmax * 1.15))
        tk = [t for t in (0.0, 0.2, 0.4, 0.6) if t <= axv.get_ylim()[1]]
        axv.set_yticks(tk)
        axv.set_yticklabels([f"{t:.0%}" for t in tk], fontsize=8.0, color=C_VOL)
        axv.yaxis.tick_right()
        axv.yaxis.set_label_position("right")
        axv.set_ylabel(f"vol{ug.VOL_WIN}（年化）", fontsize=8.4, color=C_VOL, labelpad=2)
        axv.grid(ls="--", lw=0.55, alpha=0.25)
        axv.tick_params(axis="y", colors=C_VOL, length=2)
        for s in ("top", "right"):
            axv.spines[s].set_visible(False)
    axb = axv if axv is not None else axp
    axb.xaxis.set_major_locator(mdates.YearLocator())
    axb.xaxis.set_major_formatter(mdates.DateFormatter("%Y"))
    axb.set_xlabel("成交日 = 月末决策的次一交易日（T+1 净值）", fontsize=9, color="#5b6b7a")

    # ---- 图例
    handles = [
        Line2D([], [], color=C_PRICE, lw=1.3, label=f"{cfg['name']} 复权净值"),
        Line2D([], [], color=C_MA, lw=1.25, label=f"MA{ug.MA_WIN} 年线（净值口径）"),
        Line2D([], [], ls="none", marker="^", ms=8, mfc="none", mec=C_BUY,
               label=f"回补 ▲（{sum(1 for o in ops if o['kind'] == 'buy')}）"),
        Line2D([], [], ls="none", marker="v", ms=8, mfc="none", mec=C_SELL,
               label=f"减仓 ▼（{sum(1 for o in ops if o['kind'] == 'sell') - len(deep_dates)}）"),
        Line2D([], [], ls="none", marker="v", ms=10, mfc=C_SELL, mec="#0b3d22",
               label=f"深破位免确认 ▼（{len(deep_dates)}）"),
        Line2D([], [], color=C_STEP, lw=1.15,
               label=f"带内仓位（{'中' if has_vol else '下'}格）"),
        Patch(facecolor=C_UP, alpha=0.30, label=f"美元指数在 MA{ug.MA_WIN} 上"),
        Patch(facecolor=C_DOWN, alpha=0.30, label=f"美元指数在 MA{ug.MA_WIN} 下"),
    ]
    ax.legend(handles=handles, loc="lower left", bbox_to_anchor=(0.0, 1.005, 1.0, 0.10),
              ncol=4, mode="expand", fontsize=8.4, frameon=False,
              columnspacing=1.0, handletextpad=0.5, borderaxespad=0.2)

    fig.text(0.045, 0.972,
             f"{cfg['name']}（{cfg['otc']}）年线闸门 · MA{ug.MA_WIN} × 连续 {ug.CONFIRM} 月确认"
             f" + 深破 {ug.DEEP:.0%} 免确认"
             + (f" × 波动层 TV {float(p['TV']):.0%}" if float(p["TV"] or 0) > 0 else "")
             + " · 月末决策（真实操作点）",
             ha="left", fontsize=13.2, color="#1b2b3a", weight="bold")
    fig.text(0.045, 0.941,
             f"{idx[0].date()} ~ {idx[-1].date()}（{yrs:.1f} 年）· 闸门 {pn['ann']:+.2%}/年、"
             f"回撤 日频 {pn['mdd']:.2%} / 月末 {ug.mdd_sampled(nv):.2%}、夏普 {pn['sharpe']:.2f}、"
             f"平均仓位 {ug.exposure(st):.0%}、操作 {len(ops)} 笔",
             ha="left", fontsize=10, color="#3d4f5f")
    fig.text(0.045, 0.914,
             f"恒持满仓 {ph['ann']:+.2%}/年、回撤 日频 {ph['mdd']:.2%} / 月末 "
             f"{ug.mdd_sampled(hold):.2%}、夏普 {ph['sharpe']:.2f}"
             "   ｜   月度闸门对 5 周内的 V 型急跌结构性无效，深破位豁免只能救回一部分",
             ha="left", fontsize=9.4, color="#5b6b7a")

    note = [
        f"规则：每月最后一个交易日收盘看**美元指数**（{cfg['signal']}）与其 MA{ug.MA_WIN}；"
        f"连续 {ug.CONFIRM} 个月在年线下方才减仓 → {ug.level_name(floor)}（{floor:.0%}）；"
        f"**首次破位即低于年线 {ug.DEEP:.0%} 以上 → 当月就减（实心 ▼，不等第二个月）**；"
        "重新站上年线 → 次月即回满，不等确认。",
        (f"**波动层**：最终权重 = 闸门 × min(1, {float(p['TV']):.0%} / vol{ug.VOL_WIN})，"
         f"月末用美元指数算 vol{ug.VOL_WIN}、变动 < {float(p['VOL_DEAD']):.0%} 不动手"
         "（第三格橙线 = vol20，虚线 = 目标波动）。"
         if float(p["TV"] or 0) > 0 else
         "波动层当前关闭（`TV=None`）：仓位只有 满仓 / 半仓 / 清仓 三档。"),
        "成交 = 月末决策的**次一交易日收盘净值**（QDII 净值本身滞后 1~2 日，实盘按 T+1~T+2 提交）；"
        f"扣费按单边 {ug.COST:.1%}（场外 A 类 0.6% 的口径见回测报告 §4，年化再掉约 0.6pp）。",
        f"底色用的是**美元指数**的 MA{ug.MA_WIN}（决策依据）；橙线是**净值口径**的 MA{ug.MA_WIN}"
        "（只作视觉参照，形状一致、数值略有偏离）。机械规则输出，非投资建议。",
    ]
    fig.text(0.045, 0.014, "\n".join(note), fontsize=8.4, color="#5b6b7a", linespacing=1.5,
             va="bottom")

    out = os.path.join(out_dir, f"_v7_gate_{leg}.png")
    fig.savefig(out, dpi=135, facecolor="white")
    plt.close(fig)

    print(f"图: {out}")
    print(f"  {cfg['name']} {idx[0].date()} ~ {idx[-1].date()}（{yrs:.1f} 年）· 操作 {len(ops)} 笔 · "
          f"闸门 {pn['ann']:+.2%}/年 回撤(日){pn['mdd']:.2%} 夏普 {pn['sharpe']:.2f} 仓位均值 "
          f"{ug.exposure(st):.0%} | 恒持 {ph['ann']:+.2%}/年 回撤(日){ph['mdd']:.2%}")
    for o in ops:
        tag = "深破" if o["date"] in deep_dates else ""
        print(f"    {o['date'].date()} {'回补' if o['kind'] == 'buy' else '减仓'}{tag} "
              f"{o['frm']:.0%}→{o['to']:.0%}")
    return out


def main():
    ap = argparse.ArgumentParser(description="V7 美股年线闸门单腿图")
    ap.add_argument("--leg", choices=list(ug.POOL), action="append",
                    help="只画指定腿（可重复）；默认两腿都画")
    args = ap.parse_args()
    for leg in (args.leg or list(ug.POOL)):
        plot(leg)


if __name__ == "__main__":
    main()
