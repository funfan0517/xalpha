# -*- coding: utf-8 -*-
"""V18（成交量操盘笔记）· 三张图：单腿三格 / 判据三档 / 组合层。

图都在解释同一件事 —— **这套清单改变的是仓位，不是收益**：

1. `_v18_{leg}.png`（每条上线腿一张，三格）
   - 上格：V18 净值（紫）vs 该 ETF 买入持有（灰）+ ▲▼ 操作点（成交日 = 信号次日开盘）；
   - 中格：带内仓位阶梯（0 / 50% / 100%，占该腿目标带）—— **它是这套规则唯一真实改变的量**；
   - 下格：成交量柱 + MA20 量线 + **高量锚点标记**（橙点 = 当日是 D1c 高量，即观察窗 D0）。
2. `_v18_criteria.png`
   - 上格：三档高量判据（D1 / D1b / D1c）的组合净值 vs 同权重恒定持有；
   - 下格：三档在七条腿上的触发频率（虚线 = iid 理论值 25% = 「没有信息」的基准）。
3. `_v18_pool.png`
   - 上格：上线池组合（V18 / 同曝光买入持有 / 带权买入持有）；
   - 下格：组合加权仓位阶梯 —— 与 `_v18_{leg}.png` 中格同一个读数，只是加权合成。

口径：场内 ETF 前复权 OHLCV，T 日收盘信号 → T+1 **开盘**成交，成本单边 7bp，空仓计货基。
产物（本目录）：`_v18_{kc50,hkt,gold}.png` / `_v18_criteria.png` / `_v18_pool.png`。
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

_HERE = os.path.dirname(os.path.abspath(__file__))      # .../research
_V18 = os.path.dirname(_HERE)
if _V18 not in sys.path:
    sys.path.insert(0, _V18)

import ohlcv        # noqa: E402
import legs as LG         # noqa: E402
import vol_notes as vn   # noqa: E402

OUT = _HERE
C_NAV = "#7b52ab"
C_HOLD = "#555555"
C_BUY = "#d33a2c"
C_SELL = "#1f8a4c"
C_VOL = "#8fa8bf"
C_ANCHOR = "#e08c1a"


def _plt():
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    import matplotlib.dates as mdates
    plt.rcParams["font.sans-serif"] = ["Microsoft YaHei", "SimHei", "DejaVu Sans"]
    plt.rcParams["axes.unicode_minus"] = False
    return plt, mdates


def _leg_run(leg, **kw):
    df = ohlcv.frame(leg)
    sim = vn.state_series(df, leg, **kw)
    ready = sim["ready"].to_numpy(dtype=bool)
    sim = sim.iloc[int(np.argmax(ready)):]
    d2 = df.loc[sim.index]
    nav, rets, w, deferred = vn.run_leg(sim["mult"], d2)
    return sim, d2, nav, w, deferred


def plot_leg(leg, **kw):
    plt, mdates = _plt()
    sim, df, nav, w, deferred = _leg_run(leg, **kw)
    hold = df["close"] / df["close"].iloc[0]
    m_v, m_h = vn.perf(nav), vn.perf(hold)
    expo = float(w.mean())

    fig, axs = plt.subplots(3, 1, figsize=(13.6, 10.6), sharex=True,
                            gridspec_kw=dict(height_ratios=[3.2, 1.0, 1.3], hspace=0.09))

    a = axs[0]
    a.plot(hold.index, hold, color=C_HOLD, lw=1.1,
           label=f"{LG.LEGS[leg]['name']} 买入持有  {m_h['ann']:+.1%}/年 · 回撤 {m_h['mdd']:.1%} · "
                 f"夏普 {m_h['sharpe']:.2f}")
    a.plot(nav.index, nav, color=C_NAV, lw=1.5,
           label=f"V18 成交量清单  {m_v['ann']:+.1%}/年 · 回撤 {m_v['mdd']:.1%} · "
                 f"夏普 {m_v['sharpe']:.2f} · 曝光 {expo:.0%}")
    dw = w.diff().fillna(0.0)
    buys, sells = np.where(dw.to_numpy() > 1e-9)[0], np.where(dw.to_numpy() < -1e-9)[0]
    x = nav.index
    navv = nav.to_numpy()
    for idxs, mk, color, side in ((buys, "^", C_BUY, "上车"), (sells, "v", C_SELL, "减/清")):
        a.plot(x[idxs], navv[idxs], ls="none", marker=mk, ms=7, mfc=color, mec="white",
               mew=0.6, zorder=5, label=f"{side}点（{len(idxs)} 次，实心）")
    a.axhline(1.0, color="#aaa", lw=0.8, ls=":")
    a.grid(ls="--", lw=0.6, alpha=0.4)
    a.set_axisbelow(True)
    a.set_ylabel("累计净值（起点 = 1）", fontsize=10)
    for s in ("top", "right"):
        a.spines[s].set_visible(False)
    a.legend(loc="lower left", bbox_to_anchor=(0.0, 1.012, 1.0, 0.075), ncol=2, mode="expand",
             fontsize=8.8, frameon=False, columnspacing=1.2, handletextpad=0.4)

    b = axs[1]
    b.fill_between(w.index, 0, w * 100, step="post", color=C_NAV, alpha=0.30, lw=0)
    b.step(w.index, w * 100, where="post", color=C_NAV, lw=1.2)
    b.set_ylim(-6, 128)
    b.set_yticks([0, 50, 100])
    b.set_ylabel("带内仓位", fontsize=9.5)
    b.grid(ls="--", lw=0.6, alpha=0.35)
    b.set_axisbelow(True)
    for s in ("top", "right"):
        b.spines[s].set_visible(False)
    b.text(0.005, 0.86, "0 / 50% / 100% = 清仓 / 减半 / 满带（占该腿目标带）",
           transform=b.transAxes, fontsize=8.5, color="#5b6b7a")

    c = axs[2]
    up = df["close"] >= df["open"]
    vol = df["volume"] / 1e8          # 亿股
    c.bar(df.index[up], vol[up], width=1.0, color="#c0504d", alpha=0.55, lw=0)
    c.bar(df.index[~up], vol[~up], width=1.0, color=C_SELL, alpha=0.55, lw=0)
    c.plot(df.index, vol.rolling(20).mean(), color="#2c3e50", lw=1.0, label="MA20 量线")
    hv = sim["hv"].to_numpy(dtype=bool)
    c.plot(df.index[hv], vol.to_numpy()[hv], ls="none", marker="o", ms=4.2, mfc="none",
           mec=C_ANCHOR, mew=1.1, zorder=4, label=f"高量锚点 D0（{int(hv.sum())} 次）")
    c.set_ylim(0, float(np.nanmax(vol.to_numpy())) * 1.25)
    c.set_ylabel("成交量（亿股）", fontsize=9.5)
    c.grid(ls="--", lw=0.6, alpha=0.35)
    c.set_axisbelow(True)
    for s in ("top", "right"):
        c.spines[s].set_visible(False)
    c.legend(loc="upper left", fontsize=8.5, frameon=False, ncol=2)
    yrs = (df.index[-1] - df.index[0]).days / 365.25
    if yrs < 3:
        c.xaxis.set_major_locator(mdates.MonthLocator(bymonth=(1, 4, 7, 10)))
        c.xaxis.set_major_formatter(mdates.DateFormatter("%Y-%m"))
    else:
        c.xaxis.set_major_locator(mdates.YearLocator())
        c.xaxis.set_major_formatter(mdates.DateFormatter("%Y"))

    fig.suptitle(f"{LG.LEGS[leg]['name']}（场内 {ohlcv.CODES[leg]}，前复权 OHLCV）· "
                 f"V18 成交量操盘笔记 vs 买入持有    |    "
                 f"{df.index[0].date()} ~ {df.index[-1].date()}（{yrs:.1f} 年）",
                 x=0.055, ha="left", y=0.968, fontsize=14, color="#1b2b3a", weight="bold")
    note = [
        "上格：紫 = V18 清单净值（T 日收盘信号 → T+1 开盘成交，成本单边 7bp，空仓计货基），"
        "灰 = 该 ETF 买入持有；▲▼ = 成交日，实心画在策略净值上。",
        "中格：带内仓位 0/50%/100%（清仓 / 减半 / 满带）—— 这套清单的产出几乎只有这一格在动。",
        f"下格：成交量（亿股）+ MA20 量线 + 高量锚点（`{vn.CFG['high_vol_mode']}` 口径，空心橙圈）；"
        "锚点 = 观察窗 D0，窗内才判「破位清仓 / 高低点下移减仓」。",
        "机械规则输出，非投资建议。",
    ]
    fig.text(0.055, 0.008, "\n".join(note), fontsize=8.6, color="#5b6b7a", linespacing=1.55,
             va="bottom")
    fig.subplots_adjust(left=0.065, right=0.975, top=0.885, bottom=0.145)
    out = os.path.join(OUT, f"_v18_{leg}.png")
    fig.savefig(out, dpi=130, facecolor="white")
    plt.close(fig)

    print(f"图: {out}")
    print(f"  {LG.LEGS[leg]['name']}: V18 {m_v['ann']:+.2%}/年 回撤 {m_v['mdd']:.2%} "
          f"夏普 {m_v['sharpe']:.2f} 曝光 {expo:.0%} · 买入持有 {m_h['ann']:+.2%}/年 "
          f"回撤 {m_h['mdd']:.2%} · 买 {len(buys)} / 卖 {len(sells)} · 一字板顺延 {deferred}")
    return out


def plot_criteria(legs, **kw):
    """判据三档：净值对比 + 触发频率。"""
    plt, mdates = _plt()
    modes = [("d1", "D1 量 > 前三日最大", "#c0392b"),
             ("strict_b", "D1b ≥ 2×MA20", "#2e86c1"),
             ("quantile", "D1c 60日95分位（默认）", C_NAV)]
    curves = {}
    # 触发频率直接用失真检验的读数（覆盖七条腿，不只组合里的那几条）
    dist = {k: ohlcv.distortion(k) for k in vn.LEGS}
    freq_key = {"d1": "freq_d1", "strict_b": "freq_b", "quantile": "freq_q"}
    for mode, label, color in modes:
        rets = {}
        for k in legs:
            _sim, _df, nav, _w, _d = _leg_run(k, high_vol_mode=mode, **kw)
            rets[k] = nav.pct_change().fillna(0.0)
        idx = None
        for k in legs:
            idx = rets[k].index if idx is None else idx.intersection(rets[k].index)
        idx = idx.sort_values()
        wts = {k: float(LG.LEGS[k]["band"]) / sum(LG.LEGS[j]["band"] for j in legs) for k in legs}
        r = None
        for k in legs:
            rr = rets[k].reindex(idx).fillna(0.0) * wts[k]
            r = rr if r is None else r + rr
        curves[label] = (1.0 + r).cumprod()

    fig, axs = plt.subplots(2, 1, figsize=(13.5, 8.6),
                            gridspec_kw=dict(height_ratios=[2.5, 1.4], hspace=0.30))
    a = axs[0]
    for (mode, label, color) in modes:
        cv = curves[label]
        m = vn.perf(cv)
        a.plot(cv.index, cv, color=color, lw=1.5,
               label=f"{label}  {m['ann']:+.2%}/年 · 回撤 {m['mdd']:.1%} · 夏普 {m['sharpe']:.2f}")
    a.axhline(1.0, color="#aaa", lw=0.8, ls=":")
    a.set_ylabel("累计净值（起点 = 1）", fontsize=10)
    a.grid(ls="--", lw=0.6, alpha=0.4)
    a.set_axisbelow(True)
    for s in ("top", "right"):
        a.spines[s].set_visible(False)
    a.legend(loc="upper left", fontsize=9, frameon=False)
    a.set_title("三档高量判据 · 同一套规则、同一把成本尺子（上线池三腿组合）",
                fontsize=11.5, loc="left", color="#1b2b3a")

    b = axs[1]
    keys = list(vn.LEGS)
    xs = np.arange(len(keys))
    wdt = 0.26
    for i, (mode, label, color) in enumerate(modes):
        vals = [dist[k][freq_key[mode]] * 100 for k in keys]
        b.bar(xs + (i - 1) * wdt, vals, width=wdt, color=color, alpha=0.85, lw=0,
              label=label.split(" ")[0])
    b.axhline(25.0, color="#999", ls="--", lw=1.0)
    b.plot([], [], color="#999", ls="--", lw=1.0, label="iid 25% = 无信息基准")
    b.set_xticks(xs)
    b.set_xticklabels([f"{LG.LEGS[k]['name']}\n({ohlcv.EDGE_LABEL[ohlcv.EDGE[k][0]]})"
                       for k in keys], fontsize=9)
    b.set_ylabel("触发频率（%）", fontsize=9.5)
    b.grid(axis="y", ls="--", lw=0.6, alpha=0.35)
    b.set_axisbelow(True)
    for s in ("top", "right"):
        b.spines[s].set_visible(False)
    b.legend(fontsize=8.8, frameon=False, ncol=3, loc="upper center")
    b.set_title("高量触发频率：D1 贴着 25%（≈ 每天四选一的噪声），D1b/D1c 才是「异动」",
                fontsize=11, loc="left", color="#1b2b3a")

    fig.suptitle("V18 · 判据三档（规格 §6 对照组 3）", x=0.055, ha="left", y=0.97,
                 fontsize=14, color="#1b2b3a", weight="bold")
    fig.text(0.055, 0.012,
             "上格：只换「什么算高量」，其余规则/成本/权重全部不变 —— 三档年化只差 1.7pp，"
             "换手却从 23x 到 44x，收益差主要来自换手与曝光，不是信号质量。\n"
             "下格：D1 在七条腿上都落在 25%~29%，与规格 §7 对沪深300 指数的实测（25.8%）一致 —— "
             "原文的锚点在定义层不含异常信息。机械规则输出，非投资建议。",
             fontsize=8.6, color="#5b6b7a", linespacing=1.6, va="bottom")
    fig.subplots_adjust(left=0.065, right=0.975, top=0.90, bottom=0.115)
    out = os.path.join(OUT, "_v18_criteria.png")
    fig.savefig(out, dpi=130, facecolor="white")
    plt.close(fig)
    print(f"图: {out}")
    for mode, label, _c in modes:
        m = vn.perf(curves[label])
        print(f"  {label:26s} {m['ann']:+.2%}/年 回撤 {m['mdd']:.2%} 夏普 {m['sharpe']:.2f}")
    return out


def plot_pool(legs, **kw):
    """组合层：V18 / 同曝光 / 带权买入持有 + 组合仓位。"""
    plt, mdates = _plt()
    rets, ws, wts = {}, {}, {}
    for k in legs:
        sim, df, nav, w, _d = _leg_run(k, **kw)
        rets[k] = nav.pct_change().fillna(0.0)
        ws[k] = w
    idx = None
    for k in legs:
        idx = rets[k].index if idx is None else idx.intersection(rets[k].index)
    idx = idx.sort_values()
    tot = sum(float(LG.LEGS[k]["band"]) for k in legs)
    wts = {k: float(LG.LEGS[k]["band"]) / tot for k in legs}

    r = None
    for k in legs:
        rr = rets[k].reindex(idx).fillna(0.0) * wts[k]
        r = rr if r is None else r + rr
    nav_v18 = (1.0 + r).cumprod()

    frame = pd.DataFrame({k: ohlcv.frame(k)["close"].reindex(idx) for k in legs}).dropna()
    idx = frame.index
    bh_r = None
    wpos = None
    for k in legs:
        rr = frame[k].pct_change().fillna(0.0) * wts[k]
        bh_r = rr if bh_r is None else bh_r + rr
        ww = ws[k].reindex(idx).fillna(0.0) * wts[k]
        wpos = ww if wpos is None else wpos + ww
    nav_bh = (1.0 + bh_r).cumprod()
    inv = float(wpos.mean())
    same_r = inv * bh_r + (1.0 - inv) * vn.CASH_DAILY
    nav_same = (1.0 + same_r).cumprod()

    m_v, m_b, m_s = vn.perf(nav_v18), vn.perf(nav_bh), vn.perf(nav_same)
    fig, axs = plt.subplots(2, 1, figsize=(13.5, 8.0), sharex=True,
                            gridspec_kw=dict(height_ratios=[3.0, 1.0], hspace=0.10))
    a = axs[0]
    a.plot(nav_bh.index, nav_bh, color=C_HOLD, lw=1.2,
           label=f"带权买入持有（同池同权重）  {m_b['ann']:+.2%}/年 · 回撤 {m_b['mdd']:.1%} · "
                 f"夏普 {m_b['sharpe']:.2f}")
    a.plot(nav_same.index, nav_same, color="#2e86c1", lw=1.2, ls="--",
           label=f"同曝光买入持有（固定 {inv:.0%} 投入）  {m_s['ann']:+.2%}/年 · "
                 f"回撤 {m_s['mdd']:.1%} · 夏普 {m_s['sharpe']:.2f}")
    a.plot(nav_v18.index, nav_v18, color=C_NAV, lw=1.7,
           label=f"V18 成交量清单  {m_v['ann']:+.2%}/年 · 回撤 {m_v['mdd']:.1%} · "
                 f"夏普 {m_v['sharpe']:.2f}")
    a.axhline(1.0, color="#aaa", lw=0.8, ls=":")
    a.grid(ls="--", lw=0.6, alpha=0.4)
    a.set_axisbelow(True)
    a.set_ylabel("累计净值（起点 = 1）", fontsize=10)
    for s in ("top", "right"):
        a.spines[s].set_visible(False)
    a.legend(loc="upper left", fontsize=9, frameon=False)
    wtxt = "、".join(f"{LG.LEGS[k]['name']} {wts[k]:.0%}" for k in legs)

    b = axs[1]
    b.fill_between(wpos.index, 0, wpos * 100, step="post", color=C_NAV, alpha=0.30, lw=0)
    b.step(wpos.index, wpos * 100, where="post", color=C_NAV, lw=1.2)
    b.set_ylim(-2, max(30, float(np.nanmax(wpos)) * 110))
    b.set_ylabel("组合加权仓位", fontsize=9.5)
    b.grid(ls="--", lw=0.6, alpha=0.35)
    b.set_axisbelow(True)
    for s in ("top", "right"):
        b.spines[s].set_visible(False)
    b.text(0.005, 0.86, f"平均投入 {inv:.0%}（三条腿带内仓位按目标带加权后的日均值）",
           transform=b.transAxes, fontsize=8.5, color="#5b6b7a",
           bbox=dict(fc="white", ec="none", alpha=0.75, pad=1.5))
    yrs = (idx[-1] - idx[0]).days / 365.25
    if yrs < 3:
        b.xaxis.set_major_locator(mdates.MonthLocator(bymonth=(1, 4, 7, 10)))
        b.xaxis.set_major_formatter(mdates.DateFormatter("%Y-%m"))
    else:
        b.xaxis.set_major_locator(mdates.YearLocator())
        b.xaxis.set_major_formatter(mdates.DateFormatter("%Y"))

    fig.suptitle(f"V18 · 组合层：{wtxt}  ·  {idx[0].date()} ~ {idx[-1].date()}",
                 x=0.055, ha="left", y=0.968, fontsize=14, color="#1b2b3a", weight="bold")
    fig.text(0.055, 0.012,
             f"上格：灰 = 与策略同权重的恒定持有（100% 投入）；蓝虚 = 把买入持有压到同一个平均投入（{inv:.0%}）；"
             "紫 = V18。\n"
             f"紫线全程在蓝虚线下方 —— 同样的投入水平上，这套清单的择时是负贡献"
             f"（{m_v['ann']:+.2%} vs {m_s['ann']:+.2%}），它压掉的只是仓位与回撤。\n"
             "下格：组合加权仓位（0~100% 摆，均值即上面的平均投入）。机械规则输出，非投资建议。",
             fontsize=8.6, color="#5b6b7a", linespacing=1.6, va="bottom")
    fig.subplots_adjust(left=0.065, right=0.975, top=0.93, bottom=0.135)
    out = os.path.join(OUT, "_v18_pool.png")
    fig.savefig(out, dpi=130, facecolor="white")
    plt.close(fig)
    print(f"图: {out}")
    print(f"  V18 {m_v['ann']:+.2%}/年 回撤 {m_v['mdd']:.2%} · 同曝光 {m_s['ann']:+.2%}/年 "
          f"回撤 {m_s['mdd']:.2%} · 带权持有 {m_b['ann']:+.2%}/年 回撤 {m_b['mdd']:.2%}")
    return out


def main():
    ap = argparse.ArgumentParser(description="V18 成交量操盘笔记 · 出图")
    ap.add_argument("--leg", action="append", choices=list(vn.LEGS),
                    help="只画指定腿（可重复）；默认上线池三腿")
    ap.add_argument("--skip-pool", action="store_true")
    args = ap.parse_args()
    legs = args.leg or vn.POOL
    for k in legs:
        plot_leg(k)
    plot_criteria(legs)
    if not args.skip_pool:
        plot_pool(legs)


if __name__ == "__main__":
    main()
