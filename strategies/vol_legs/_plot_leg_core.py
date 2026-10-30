# -*- coding: utf-8 -*-
"""vol_legs 家族通用「单腿图」：买入持有 vs 某个变种的择时净值，标出买卖点。

各变种的 `research/_plot_leg.py` 只是给本模块传一份 `spec`（信号函数 / 净值函数 / 预热天数 /
文案），图的形式、口径、配色、标注方式全部在这里统一，避免三个变种各抄一份再各自漂移。

三条腿（`--leg` 任选，默认全跑）：

| key | 标的 | 序列 | 说明 |
|---|---|---|---|
| `a500` | 中证A500 | `023299` 场外复权净值 | 六腿之一；2025-03-27 前为 `000051` 桥接段（默认不画） |
| `hs300` | 沪深300 | `000051` 场外复权净值 | **不属六腿**，同一套规则的同口径外推（也是 A500 的桥接基金） |
| `hkt` | 恒生科技 | `012805` 场外复权净值 | 六腿之一；QDII 净值滞后 T+1~T+2 |

每张图两条线、同起点归 1：
- **灰线** = 该标的自己的买入持有（复权净值，含分红再投）；
- **紫线** = 同一序列按变种规则 0/1 进出的组合净值，**T+1 净值成交**，空仓按货基计息。

买卖点**同时画在两条线上**（成交日 = 信号次日）：▲ = 买入（0→1）、▼ = 卖出（1→0）；
- 空心、画在**指数曲线**上 = 这一笔成交时指数在什么价位（看「买低卖高」是否成立）；
- 实心、画在**策略线**上 = 同一笔发生时组合净值的位置（看「这笔赚了还是亏了」）；
- 细竖线把同一笔的两个点连起来，竖线越长 = 当时策略净值与指数偏离越大。

主口径不计申赎费（与族内回测一致）；A 类的腿在图注里给出扣费后的年化/回撤。

产物：`<变种目录>/research/_<prefix>_<key>.png`。机械规则输出，非投资建议。
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

if _HERE not in sys.path:
    sys.path.insert(0, _HERE)

import data as vdata  # noqa: E402
import rule           # noqa: E402

SERIES = {
    "a500": dict(name="中证A500", code="023299", leg="a500",
                 fee=(0.0, 0.0), fee_note="C 类，申赎费 0"),
    "hs300": dict(name="沪深300", code="000051", leg=None,
                  fee=(0.001, 0.005), fee_note="A 类，申购 0.10% / 赎回 0.50%"),
    "hkt": dict(name="恒生科技", code="012805", leg="hkt",
                fee=(0.0, 0.0), fee_note="C 类，申赎费 0；QDII 净值滞后 T+1~T+2"),
}


def load_px(key, mod):
    """该腿/该标的的场外复权净值（六腿走 rule.LEGS，其余按基金代码直取）。"""
    leg = SERIES[key]["leg"]
    if leg:
        return mod.load(leg)
    return vdata.otc_series(SERIES[key]["code"]).dropna()


def plot(key, spec, prefix, out_dir, with_bridge=False):
    """画一条腿：`spec` 见模块 docstring / 各变种 `research/_plot_leg.py`。"""
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    import matplotlib.dates as mdates

    plt.rcParams["font.sans-serif"] = ["Microsoft YaHei", "SimHei", "DejaVu Sans"]
    plt.rcParams["axes.unicode_minus"] = False

    mod, tag = spec["mod"], spec["tag"]
    meta = SERIES[key]
    px_full = load_px(key, mod)
    sig_full = spec["signal"](px_full)                      # 全样本算信号，保证预热正确
    warm = px_full.index[min(int(spec["warmup"]), len(px_full) - 2)]

    # 桥接段（目前只有 A500：2025-03-27 之前是 000051 沪深300）
    leg = meta["leg"]
    cut = None
    if leg and rule.LEGS[leg].get("otc_proxy"):
        _cut = pd.Timestamp(rule.LEGS[leg]["otc_start"])
        if px_full.index[0] < _cut < px_full.index[-1]:
            cut = _cut
    real_only = bool(cut) and not with_bridge

    # 先在「预热后」的整段上跑净值（保证起始日的仓位是真实仓位），再截到要画的区间
    px_w, sig_w = px_full.loc[warm:], sig_full.loc[warm:]
    nav_w = spec["run"](px_w, sig_w)                        # 主口径：不计申赎费
    ini = cut if real_only else warm
    px, sig = px_w.loc[ini:], sig_w.loc[ini:]
    nav = nav_w.loc[ini:]
    pos = sig_w.shift(1).fillna(0).astype(float).loc[ini:]
    if real_only:
        nav = nav / nav.iloc[0]                             # 以成立日为 1 重新归位

    hold = px / px.iloc[0]
    d = pos.diff().fillna(0).to_numpy()
    buys, sells = np.where(d > 0)[0], np.where(d < 0)[0]
    nav_v = nav.to_numpy()

    ph, pn = spec["perf"](hold), spec["perf"](nav)
    yrs = (px.index[-1] - px.index[0]).days / 365.25
    n_round = (len(buys) + len(sells)) / 2.0
    expo = float(pos.mean())

    hold_v = hold.to_numpy()
    x = px.index

    fig, ax = plt.subplots(figsize=(13.5, 7.2))
    ax.plot(hold.index, hold_v, color="#555", lw=1.15,
            label=f"{meta['name']}（买入持有）  {ph['ann']:+.1%}/年 · 回撤 {ph['mdd']:.1%} · "
                  f"夏普 {ph['sharpe']:.2f}")
    ax.plot(nav.index, nav_v, color="#7b52ab", lw=1.5,
            label=f"{spec['line']}  {pn['ann']:+.1%}/年 · 回撤 {pn['mdd']:.1%} · "
                  f"夏普 {pn['sharpe']:.2f} · 曝光 {expo:.0%} · {n_round / yrs:.1f} 往返/年")

    # 买卖点画两处：细竖线把「指数价位」与「策略净值」上同一笔连起来
    conn_alpha = 0.22 if (len(buys) + len(sells)) <= 40 else 0.10   # 笔数多时线淡一些，别糊住曲线
    for idxs, color in ((buys, "#d33"), (sells, "#1f8a4c")):
        for i in idxs:
            ax.plot([x[i], x[i]], [hold_v[i], nav_v[i]], color=color, lw=0.5,
                    alpha=conn_alpha, zorder=2, solid_capstyle="butt")
    for idxs, mk, color, side in ((buys, "^", "#d33", "买入"), (sells, "v", "#1f8a4c", "卖出")):
        ax.plot(x[idxs], hold_v[idxs], ls="none", marker=mk, ms=6.5, mfc="none",
                mec=color, mew=1.2, zorder=4,
                label=f"{side}点 · 指数价位（{len(idxs)} 次，空心）")
        ax.plot(x[idxs], nav_v[idxs], ls="none", marker=mk, ms=7.5, mfc=color,
                mec="white", mew=0.6, zorder=5,
                label=f"{side}点 · 策略净值（{len(idxs)} 次，实心）")

    # 桥接段 —— 仅 --with-bridge 时画
    if cut is not None and not real_only:
        ax.axvspan(px.index[0], cut, color="#999", alpha=0.07, lw=0)
        ax.axvline(cut, color="#888", lw=0.9, ls="--")
        ax.text(cut, 0.99, f"{cut.date()} 起才是 {meta['code']} 真实净值  ", va="top",
                ha="right", transform=ax.get_xaxis_transform(), fontsize=8.5, color="#666")

    ax.axhline(1.0, color="#aaa", lw=0.8, ls=":")
    ax.set_ylabel("累计净值（起点 = 1）", fontsize=10)
    ax.grid(ls="--", lw=0.6, alpha=0.4)
    ax.set_axisbelow(True)
    # 图例放在坐标区上方（笔数多的腿——V1 恒生科技 82 笔——画在框里会压住曲线和标记）
    ax.legend(loc="lower left", bbox_to_anchor=(0.0, 1.01, 1.0, 0.10), ncol=3,
              mode="expand", fontsize=9, frameon=False,
              columnspacing=1.2, handletextpad=0.4)
    for s in ("top", "right"):
        ax.spines[s].set_visible(False)
    if yrs < 3:                                             # 短样本：按季度刻度，否则只有两三个年份
        ax.xaxis.set_major_locator(mdates.MonthLocator(bymonth=(1, 4, 7, 10)))
        ax.xaxis.set_major_formatter(mdates.DateFormatter("%Y-%m"))
    else:
        ax.xaxis.set_major_locator(mdates.YearLocator())
        ax.xaxis.set_major_formatter(mdates.DateFormatter("%Y"))

    fee = meta["fee"]
    pf = None
    if fee[0] or fee[1]:
        pf = spec["perf"](spec["run"](px, sig, fee=fee))
        fee_txt = (f" 若按 {meta['fee_note']} 扣费：年化 {pf['ann']:+.2f}% / 回撤 {pf['mdd']:.2%}"
                   "（图上紫线未扣费）。")
    else:
        fee_txt = f" {meta['fee_note']}，故扣费前后同一条线。"

    fig.suptitle(f"{meta['name']}（{meta['code']}, 场外复权净值）· {spec['short']}择时 vs 买入持有"
                 + ("    |    成立以来 " if real_only else "    |    ")
                 + f"{px.index[0].date()} ~ {px.index[-1].date()}",
                 x=0.055, ha="left", y=0.965, fontsize=14, color="#1b2b3a", weight="bold")
    note = ["两条线同起点归 1：灰 = 该标的复权净值（含分红再投），紫 = 该变种择时的组合净值；"
            "成交日 = 信号次日（T+1 净值），空仓按货基 "
            f"{spec['cash']:.2%} 计息。",
            "▲/▼ 同时画在两处：空心 = 该笔在指数上的价位，实心 = 同一笔在策略净值上的位置，"
            "细竖线把两点连起来。",
            fee_txt.strip() + "  ·  机械规则输出，非投资建议。"]
    if real_only:
        note.append(f"本图只画 {meta['code']} 成立（{cut.date()}）之后的真实净值，桥接段不显示；"
                    "两条线以成立日为 1 重新归位。")
        note.append("信号仍按族内主口径在含桥接的全样本上算（桥接只用来撑指标窗口）；"
                    f"样本仅 {yrs:.1f} 年，年化与夏普的统计意义有限。")
    txt = fig.text(0.055, 0.012, "\n".join(note), fontsize=8.6, color="#5b6b7a",
                   linespacing=1.55, va="bottom")
    fig.subplots_adjust(left=0.065, right=0.975, top=0.855, bottom=0.12)
    # 先画一次量出图注实际高度，再把坐标区抬到它上沿之上（行数变化时不会压到日期刻度）
    fig.canvas.draw()
    bb = txt.get_window_extent(fig.canvas.get_renderer()).transformed(
        fig.transFigure.inverted())
    fig.subplots_adjust(left=0.065, right=0.975, top=0.855, bottom=bb.y1 + 0.045)

    out = os.path.join(out_dir, f"_{prefix}_{key}.png")
    fig.savefig(out, dpi=130, facecolor="white")
    plt.close(fig)

    print(f"图: {out}")
    print(f"  {meta['name']} {px.index[0].date()} ~ {px.index[-1].date()}（{yrs:.1f} 年）· "
          f"买入持有 {ph['ann']:+.2%}/年 · 回撤 {ph['mdd']:.2%} · 夏普 {ph['sharpe']:.2f} · "
          f"累计 {ph['cum']:+.1%}")
    print(f"  {tag} 择时 {pn['ann']:+.2%}/年 · 回撤 {pn['mdd']:.2%} · 夏普 {pn['sharpe']:.2f} · "
          f"累计 {pn['cum']:+.1%} · 曝光 {expo:.0%} · 买 {len(buys)} / 卖 {len(sells)}")
    if pf:
        print(f"  扣费后 {pf['ann']:+.2%}/年 · 回撤 {pf['mdd']:.2%} · 夏普 {pf['sharpe']:.2f}")
    return out


def cli(spec, prefix, out_dir, desc="单腿图（买入持有 vs 择时，标买卖点）"):
    """各变种 `research/_plot_leg.py` 的公共入口。"""
    ap = argparse.ArgumentParser(description=desc)
    ap.add_argument("--leg", choices=list(SERIES), action="append",
                    help="只画指定腿（可重复）；默认全部")
    ap.add_argument("--with-bridge", action="store_true",
                    help="A500 也画桥接段（默认只画 023299 成立之后的真实净值）")
    args = ap.parse_args()
    for key in (args.leg or list(SERIES)):
        plot(key, spec, prefix, out_dir, with_bridge=args.with_bridge)
