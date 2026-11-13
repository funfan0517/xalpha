# -*- coding: utf-8 -*-
"""vol_legs 家族通用「单腿图」：买入持有 vs 某个变种的择时净值，标出买卖点。

各变种的 `research/_plot_leg.py` 只是给本模块传一份 `spec`（信号函数 / 净值函数 / 预热天数 /
文案），图的形式、口径、配色、标注方式全部在这里统一，避免三个变种各抄一份再各自漂移。

七条腿（`--leg` 任选，默认全跑）：

| key | 标的 | 序列 | 说明 |
|---|---|---|---|
| `a500` | 中证A500 | `023299` 场外复权净值 | 七腿之一；2025-03-27 前为 `000051` 桥接段（默认不画） |
| `hs300` | 沪深300 | `000051` 场外复权净值 | **v1.4 起是独立的一条腿**（自带 2.5pp 目标带），同时也是 A500 的桥接基金 |
| `ndx` | 纳指100 | `270042` 场外复权净值 | 七腿之一；QDII 净值滞后 T+1~T+2；无桥接，2012-08 起 |
| `spx` | 标普500 | `017641` 场外复权净值 | 七腿之一；2023-04-06 前为 `050025` 桥接段（默认不画） |
| `kc50` | 科创50 | `011609` 场外复权净值 | 七腿之一；2021-03 起 |
| `hkt` | 恒生科技 | `012805` 场外复权净值 | 七腿之一；QDII 净值滞后 T+1~T+2 |
| `gold` | 黄金 | `000216` 场外复权净值 | 七腿之一（避险腿）；2013-08 起 |

每张图两条线、同起点归 1：
- **灰线** = 该标的自己的买入持有（复权净值，含分红再投）；
- **紫线** = 同一序列按变种规则 0/1 进出的组合净值，**T+1 净值成交**，空仓按货基计息。

买卖点**同时画在两条线上**（成交日 = 信号次日）：▲ = 买入（0→1）、▼ = 卖出（1→0）；
- 空心、画在**指数曲线**上 = 这一笔成交时指数在什么价位（看「买低卖高」是否成立）；
- 实心、画在**策略线**上 = 同一笔发生时组合净值的位置（看「这笔赚了还是亏了」）；
- 细竖线把同一笔的两个点连起来，竖线越长 = 当时策略净值与指数偏离越大。

**下格 = 仓位**（2026-11-13 起，与 v8/v9/v10 的单腿图版式对齐），与上格共享 x 轴：
- **灰虚线台阶** = 0/1 入场状态（`mark_signal` 滞后一天），只有 100% / 0% 两档；
- **蓝线 + 面积** = 实际生效仓位 = 0/1 信号 × `min(1, TV/σ20年化)`（带 5pp 死区 / 5 日限速，所以是阶梯）；
- **右轴橙线** = σ20 年化、**橙色虚线** = `TV` 目标波动 —— 两条线的缝隙就是波动上限层压掉的仓位。
没有波动上限层的变种（V1-1）下格只画 0/1 仓位，右轴不出现。

主口径不计申赎费（与族内回测一致）；A 类的腿在图注里给出扣费后的年化/回撤。

产物：`<变种目录>/research/_<prefix>_<key>.png`。机械规则输出，非投资建议。

`--years 3` 只画**末端 3 年**，产物为 `_<prefix>_<key>_3y.png`（不覆盖全样本那张）：
信号与净值仍在**全样本**上算（窗口首日的仓位是真实仓位），只是截取窗口并把窗口首日净值
归 1，避免窗口起点带着此前累积的收益；样本本身不足 N 年的腿（如 A500 仅 1.6 年）照画，
标题不写「近 N 年」，图注注明「本图即全部样本」。
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
    "hs300": dict(name="沪深300", code="000051", leg="hs300",
                  fee=(0.001, 0.005), fee_note="A 类，申购 0.10% / 赎回 0.50%"),
    "ndx": dict(name="纳指100", code="270042", leg="ndx",
                fee=(0.001, 0.005),
                fee_note="A 类，申购 0.10% / 赎回 0.50%；QDII 净值滞后 T+1~T+2"),
    "spx": dict(name="标普500", code="017641", leg="spx",
                fee=(0.001, 0.005),
                fee_note="A 类，申购 0.10% / 赎回 0.50%；QDII 净值滞后 T+1~T+2"),
    "kc50": dict(name="科创50", code="011609", leg="kc50",
                 fee=(0.0, 0.0), fee_note="C 类，申赎费 0"),
    "hkt": dict(name="恒生科技", code="012805", leg="hkt",
                fee=(0.0, 0.0), fee_note="C 类，申赎费 0；QDII 净值滞后 T+1~T+2"),
    "gold": dict(name="黄金", code="000216", leg="gold",
                 fee=(0.001, 0.005), fee_note="A 类，申购 0.10% / 赎回 0.50%"),
}


def load_px(key, mod):
    """该腿/该标的的场外复权净值（七腿走 rule.LEGS，其余按基金代码直取）。"""
    leg = SERIES[key]["leg"]
    if leg:
        return mod.load(leg)
    return vdata.otc_series(SERIES[key]["code"]).dropna()


def _tv_params(mod):
    """读变种的波动上限层参数 `(TV, TV_WIN)`；没有这一层时返回 `(None, 20)`。

    V2 把参数放在 `PARAMS` dict（`PARAMS["TV"]`），V3 是模块级同名变量（`TV`、`TV_WIN`），
    这里两种写法都兼容，免得绘图再去 import 各变种的具体常量名。
    """
    p = getattr(mod, "PARAMS", None)
    if isinstance(p, dict):
        tv = p.get("TV")
        win = p.get("TV_WIN", 20)
    else:
        tv = getattr(mod, "TV", None)
        win = getattr(mod, "TV_WIN", 20)
    try:
        tv = float(tv) if tv else None
    except (TypeError, ValueError):
        tv = None
    try:
        win = int(win or 20)
    except (TypeError, ValueError):
        win = 20
    return tv, win


def plot(key, spec, prefix, out_dir, with_bridge=False, years=None):
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
    # 净值用 `signal`（可以是连续仓位，如波动上限层）；▲▼ 标点用 `mark_signal`（0/1 入场判定，
    # 否则每一次仓位微调都会被画成一个买卖点，图会被几百个标记糊住）。未提供时两者相同。
    mark_full = spec.get("mark_signal", spec["signal"])(px_full)
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
    mk_w = mark_full.loc[warm:]
    nav_w = spec["run"](px_w, sig_w)                        # 主口径：不计申赎费
    ini = cut if real_only else warm
    # 近 N 年窗口（`--years 3`）：信号、净值仍在**全样本**上算（否则起点仓位/指标不是真实值），
    # 算完再从窗口首日截取，净值以窗口首日为 1 重新归位 —— 否则窗口起点会带着之前累积的收益。
    win_years = float(years or 0.0)
    start = ini
    if win_years > 0:
        _from = px_w.index[-1] - pd.DateOffset(days=int(round(win_years * 365.25)))
        _p = min(int(px_w.index.searchsorted(_from)), len(px_w.index) - 2)
        start = max(pd.Timestamp(ini), pd.Timestamp(px_w.index[_p]))
    px, sig = px_w.loc[start:], sig_w.loc[start:]
    nav = nav_w.loc[start:]
    pos = mk_w.shift(1).fillna(0).astype(float).loc[start:]   # 标点用：0/1 入场序列
    # 下格用：实际生效仓位 =「信号 × 波动上限」（`sig`）再按 T+1 成交滞后一天，与 `expo` 同口径
    pos_eff = sig_w.shift(1).fillna(0.0).clip(0.0, 1.0).loc[start:]
    if real_only or win_years > 0:
        nav = nav / nav.iloc[0]                             # 以起点（成立日 / 窗口首日）为 1 重新归位

    hold = px / px.iloc[0]
    d = pos.diff().fillna(0).to_numpy()
    buys, sells = np.where(d > 0)[0], np.where(d < 0)[0]
    nav_v = nav.to_numpy()

    ph, pn = spec["perf"](hold), spec["perf"](nav)
    yrs = (px.index[-1] - px.index[0]).days / 365.25
    n_round = (len(buys) + len(sells)) / 2.0
    expo = float(sig.shift(1).fillna(0).mean())             # 实际持仓（含波动上限），不是入场天数占比

    hold_v = hold.to_numpy()
    x = px.index

    # 波动上限层参数：V2 在 PARAMS dict 里（TV/TV_WIN），V3 是模块级同名变量，
    # V1-1 没有这一层 → tv=None，下格退化成纯 0/1 仓位。
    tv, vwin = _tv_params(mod)
    vol = None
    if tv:
        vol = (px_full.astype(float).pct_change()
               .rolling(vwin, min_periods=vwin).std() * float(np.sqrt(252.0))).loc[start:]

    # 上下两格共享 x 轴：上格 = 净值 + 买卖点；下格 = 仓位（0/1 入场台阶 vs 含波动上限的实际仓位）+ σ
    fig = plt.figure(figsize=(13.5, 9.0))
    gs = fig.add_gridspec(2, 1, height_ratios=[3.55, 1.25], hspace=0.09)
    ax = fig.add_subplot(gs[0])
    axp = fig.add_subplot(gs[1], sharex=ax)
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
    ax.tick_params(labelbottom=False)                       # x 刻度交给下格

    # ---- 下格：仓位（左轴）+ σ 年化（右轴）
    # 虚线台阶 = 0/1 入场状态（`mark_signal`，未经波动上限压制）；实线/面积 = 实际仓位。
    # 两条线的缝隙就是「波动上限层」压掉的那部分仓位，压了多少一眼可见。
    C_POS, C_LVL, C_SIG = "#2c6fbb", "#9aa5b1", "#e07b39"
    pe_v = pos_eff.to_numpy() * 100.0
    axp.fill_between(x, 0.0, pe_v, color=C_POS, alpha=0.13, lw=0, zorder=1)
    axp.step(x, pos.to_numpy() * 100.0, where="post", color=C_LVL, lw=0.9, ls="--",
             alpha=0.95, zorder=2)
    axp.plot(x, pe_v, color=C_POS, lw=1.25, zorder=4)
    axp.set_ylim(-6.0, 112.0)
    axp.set_yticks([0, 25, 50, 75, 100])
    axp.set_yticklabels([f"{t}%" for t in (0, 25, 50, 75, 100)],
                        fontsize=8.2, color=C_POS)
    axp.set_ylabel("实际仓位", fontsize=9, color=C_POS)
    axp.grid(ls="--", lw=0.55, alpha=0.28)
    axp.set_axisbelow(True)
    axp.tick_params(axis="y", length=2)
    axp.spines["top"].set_visible(False)
    axp.set_xlabel("成交日 = 信号次日（T+1 净值）｜下格虚线 = 0/1 入场状态，"
                   "实线/面积 = 乘波动上限后的实际仓位", fontsize=9, color="#5b6b7a")

    if vol is not None:
        axv = axp.twinx()
        vol_v = vol.to_numpy(dtype=float) * 100.0
        axv.plot(x, vol_v, color=C_SIG, lw=0.95, alpha=0.85, zorder=3)
        axv.axhline(tv * 100.0, color=C_SIG, lw=1.0, ls="--", alpha=0.75, zorder=2)
        _vmax = float(np.nanmax(vol_v)) if np.isfinite(vol_v).any() else 70.0
        axv.set_ylim(0.0, max(70.0, _vmax * 1.05))
        axv.set_ylabel(f"σ{vwin}（年化）", fontsize=9, color=C_SIG)
        axv.tick_params(axis="y", colors=C_SIG, labelsize=8.2, length=2)
        axv.spines["top"].set_visible(False)
        axv.text(x[-1], tv * 100.0, f" 目标 {tv:.0%}", va="center", ha="left",
                 fontsize=8.0, color=C_SIG)

    fee = meta["fee"]
    pf = None
    if fee[0] or fee[1]:
        pf = spec["perf"](spec["run"](px, sig, fee=fee))
        fee_txt = (f" 若按 {meta['fee_note']} 扣费：年化 {pf['ann']:+.2f}% / 回撤 {pf['mdd']:.2%}"
                   "（图上紫线未扣费）。")
    else:
        fee_txt = f" {meta['fee_note']}，故扣费前后同一条线。"

    # 样本本身不足 N 年时（A500 只有 1.6 年），别再在标题写「近 3 年」，否则和「成立以来」自相矛盾
    win_eff = bool(win_years) and yrs >= win_years * 0.95
    win_txt = f"近 {win_years:g} 年 " if win_eff else ""
    fig.suptitle(f"{meta['name']}（{meta['code']}, 场外复权净值）· {win_txt}{spec['short']}择时 vs 买入持有"
                 + ("    |    成立以来 " if (real_only and start == ini) else "    |    ")
                 + f"{px.index[0].date()} ~ {px.index[-1].date()}",
                 x=0.055, ha="left", y=0.965, fontsize=14, color="#1b2b3a", weight="bold")
    note = ["两条线同起点归 1：灰 = 该标的复权净值（含分红再投），紫 = 该变种择时的组合净值；"
            "成交日 = 信号次日（T+1 净值），空仓按货基 "
            f"{spec['cash']:.2%} 计息。",
            "▲/▼ 同时画在两处：空心 = 该笔在指数上的价位，实心 = 同一笔在策略净值上的位置，"
            "细竖线把两点连起来。",
            ("下格：虚线台阶 = 0/1 入场状态（100% / 0%），蓝线 = 实际生效仓位 = 0/1 信号 × "
             f"min(1, {tv:.0%}/σ{vwin}年化)，右轴橙线 = σ{vwin} 年化、橙色虚线 = {tv:.0%} 目标；"
             "σ 高于目标才压仓，且带 5pp 死区 / 5 日限速（所以是阶梯而非日频曲线）。"
             if tv else
             "下格：蓝线 = 实际生效仓位（本变种无波动上限层，与 0/1 入场状态重合）。"),
            fee_txt.strip() + "  ·  机械规则输出，非投资建议。"]
    if real_only:
        note.append(f"本图只画 {meta['code']} 成立（{cut.date()}）之后的真实净值，桥接段不显示；"
                    "两条线以成立日为 1 重新归位。")
        note.append("信号仍按族内主口径在含桥接的全样本上算（桥接只用来撑指标窗口）；"
                    f"样本仅 {yrs:.1f} 年，年化与夏普的统计意义有限。")
    if win_years:
        if yrs < win_years * 0.95:
            note.append(f"该腿可用样本只有 {yrs:.1f} 年（不足 {win_years:g} 年），本图即全部样本；"
                        "净值与仓位以起点为 1 起算。")
        else:
            note.append(f"本图只画末端 {yrs:.1f} 年（{px.index[0].date()} 起）：净值与仓位都以窗口首日"
                        "为 1 重新起算，更早的历史见同目录里无 `_3y` 后缀的那张。")
    txt = fig.text(0.055, 0.012, "\n".join(note), fontsize=8.6, color="#5b6b7a",
                   linespacing=1.55, va="bottom")
    gs.update(left=0.065, right=0.945, top=0.855, bottom=0.12)
    # 先画一次量出图注实际高度，再把坐标区抬到它上沿之上（行数变化时不会压到日期刻度）
    fig.canvas.draw()
    bb = txt.get_window_extent(fig.canvas.get_renderer()).transformed(
        fig.transFigure.inverted())
    gs.update(left=0.065, right=0.945, top=0.855, bottom=bb.y1 + 0.045)

    sfx = f"_{win_years:g}y" if win_years else ""
    out = os.path.join(out_dir, f"_{prefix}_{key}{sfx}.png")
    fig.savefig(out, dpi=130, facecolor="white")
    plt.close(fig)

    print(f"图: {out}")
    print(f"  {meta['name']} {px.index[0].date()} ~ {px.index[-1].date()}（{yrs:.1f} 年）"
          + (f" · 近 {win_years:g} 年窗口" if win_eff else "") + " · "
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
    ap.add_argument("--years", type=float, metavar="N",
                    help="只画末端 N 年（如 3），文件名加 _3y 后缀，不覆盖全样本那张")
    args = ap.parse_args()
    for key in (args.leg or list(SERIES)):
        plot(key, spec, prefix, out_dir, with_bridge=args.with_bridge, years=args.years)
