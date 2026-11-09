# -*- coding: utf-8 -*-
"""V5（趋势四象限闸门）· 趋势线 + 操作点单腿图：中证A500 / 科创50 / 双均线口径。

图的读法：
- **上格深色线** = 场外复权净值（含分红再投 / 汇率 / 跟踪误差）；
- **橙线** = 趋势读数主线（默认 MA250 年线；`--dual 50 150` 时为 MA50 快线，另一条蓝线是 MA150 慢线）；
- **上格背景色带** = 当日所处象限（绿 = 上涨趋势、黄 = 反弹、橙 = 高位回调、红 = 下跌趋势）——
  四格 = 「读数在上方态与否」×「读数斜率朝上/朝下」，色带的分界 = 象限切换；
- **▲/▼ 标在价格线上** = 该笔调仓的成交日（T 日盘后算、T+1 净值成交）在指数上的价位，
  旁边小字是调完之后的档位（满带 / 半仓 / 观察仓）；
- **下格阶梯** = 带内仓位（占本腿目标带的比例），它不是 pp：50% = 只露半条带。
  其中**虚线 = 趋势档位**（1.0 / 0.5 / 0.2），**实线 = 乘上波动系数后的实际仓位** ——
  两条线的差就是波动层压掉的仓位（`VOL_USE=False` 时两者重合）。

两条腿的口径差别（重要）：
- **科创50（`011609`）**：默认**套闸门**，图上就是本变种的真实操作点。
- **中证A500（`023299`）**：默认**不套闸门**（宽基有估值锚，叠趋势过滤会互相抵消，见 README §2），
  所以没有任何真实操作点；图上的 ▲/▼ 是**假设套上闸门**（`GATE_ALL=True`）时的位置，只作对照，
  不是建议。另：2025-03-27 之前是 `000051`（沪深300 联接）桥接段，用灰色底纹标出。

两种读数口径：

| 口径 | 上方态（above） | 斜率 | 产物后缀 |
|---|---|---|---|
| `ma`（默认，五腿推荐） | close ≥ MA250 | MA250 的 20 日变化 | 无（`_v5_trend_{leg}.png`） |
| `dual_ma`（A500 推荐） | close ≥ MA50 **且** MA50 ≥ MA150 | MA50 的 10 日变化 | `_dual{FAST}-{SLOW}` |

双均线是**复合条件**：MA50 在 MA150 之下时，价格再高也进不了「上涨趋势」格 —— 这正是它比年线
快、又比单纯「站上 MA50」稳的原因（`_a500_trend.md` 的双侧检验里它两侧都进 Pareto 前沿）。

七腿的推荐口径见 `trend_gate.TREND_READ`（每日报告板块 2 引用同一张表）：只有中证A500 换成双均线
50/150，其余七腿保留年线 250+20 —— 快口径在美股与高波动腿上会被反复洗（见该常量里的实测数字）。

产物（**本目录** = 默认口径）：每条腿按推荐口径各出**长期 + 近三年**两份
- `_v5_trend_a500_dual50-150.png` / `_v5_trend_a500_dual50-150_3y.png`（A500 年线对照版仍保留 `_v5_trend_a500.png`）
- `_v5_trend_{ndx,spx,kc50,hkt,gold}.png` / `..._3y.png`

非对称口径（**快下车 + 慢上车**，`MODE="asym"`）的同类图在隔壁
`research/plot_trend_asym/`，那个脚本直接复用本文件的 `plot()`，只换参数 —— 两套图的版式、
配色、坐标口径完全一致，可以并排对照。本文件的 `plot()` 在 `kw["MODE"]=="asym"` 时会多画一条
快线（`DOWN_WIN`）并把标题/脚注换成非对称语义。

用法（**从仓库根跑**，产物落在本目录）：
- `python strategies/vol_legs/v5_trend_gate/research/plot_trend/_plot_trend.py` → 各腿长期（默认年线口径）；
- `... _plot_trend.py --dual 50 150` → A500 双均线口径；
- `... _plot_trend.py --years 3 --leg a500` → 只出 A500 近三年。

机械规则输出，非投资建议。
"""
import argparse
import os
import sys

import pandas as pd

try:
    sys.stdout.reconfigure(encoding="utf-8")  # type: ignore[attr-defined]
except Exception:  # noqa: BLE001
    pass

_HERE = os.path.dirname(os.path.abspath(__file__))       # research/plot_trend
_RES = os.path.dirname(_HERE)                            # research
_V5 = os.path.dirname(_RES)                              # strategies/vol_legs/v5_trend_gate
sys.path.insert(0, _V5)

import trend_gate as tg  # noqa: E402

# 象限 -> (底色, 图例名)
QUAD_STYLE = {
    tg.QUADS[0]: ("#2e7d32", "上涨趋势（年线上 + 年线上行）"),
    tg.QUADS[1]: ("#f9a825", "反弹（年线上 + 年线下行）"),
    tg.QUADS[2]: ("#ef6c00", "高位回调（年线下 + 年线上行）"),
    tg.QUADS[3]: ("#c62828", "下跌趋势（年线下 + 年线下行）"),
}
C_PRICE, C_MA, C_BUY, C_SELL, C_STEP = "#1b2b3a", "#e07b39", "#d33", "#1f8a4c", "#7b8794"
C_FAST, C_SLOW = "#e07b39", "#6b8cae"

# key -> 元信息；gate=False 的腿画的是「若套闸门」的对照操作点
SPEC = {
    "a500": dict(name="中证A500", code="023299", gate=False),
    "ndx": dict(name="纳指100", code="270042", gate=False),
    "spx": dict(name="标普500", code="017641", gate=False),
    "kc50": dict(name="科创50", code="011609", gate=True),
    "hkt": dict(name="恒生科技", code="012805", gate=True),
    "gold": dict(name="黄金", code="000216", gate=True),
}


def _quad_style_dual(fast, slow):
    """双均线口径的四象限图例：above 是「价 ≥ 快线 且 快线 ≥ 慢线」的复合条件。"""
    f, s = f"MA{fast}", f"MA{slow}"
    return {
        tg.QUADS[0]: ("#2e7d32", f"上涨趋势（价 ≥ {f} 且 {f} ≥ {s}，{f} 上行）"),
        tg.QUADS[1]: ("#f9a825", f"反弹（{f} 上行但价 < {f} 或 {f} < {s}）"),
        tg.QUADS[2]: ("#ef6c00", f"高位回调（{f} 下降但价 ≥ {f} 且 {f} ≥ {s}）"),
        tg.QUADS[3]: ("#c62828", f"下跌趋势（{f} 下行，价 < {f} 或 {f} < {s}）"),
    }


def _quad_spans(ax, idx, quad, y0, y1, style=None):
    """按象限铺背景色（fill_between + where，比逐段 axvspan 快且边界精确到日）。"""
    for name, (color, _label) in (style or QUAD_STYLE).items():
        mask = (quad == name).to_numpy()
        if mask.any():
            ax.fill_between(idx, y0, y1, where=mask, color=color, alpha=0.10, lw=0, zorder=0)


def plot(key, out_dir=_HERE, kw=None, tag="", years=None):
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.dates as mdates
    import matplotlib.pyplot as plt
    from matplotlib.lines import Line2D
    from matplotlib.patches import Patch

    plt.rcParams["font.sans-serif"] = ["Microsoft YaHei", "SimHei", "DejaVu Sans"]
    plt.rcParams["axes.unicode_minus"] = False

    meta = SPEC[key]
    kw = dict(kw or {})
    if not meta["gate"]:
        kw["GATE_ALL"] = True                                # A500：算「若套闸门」的对照操作点
    dual = kw.get("KIND") == "dual_ma"
    asym = kw.get("MODE") == "asym"                 # 快下车 + 慢上车（research/plot_trend_asym）
    dw = int(kw.get("DOWN_WIN", tg.DOWN_WIN))
    ds = int(kw.get("DOWN_SLOPE", tg.DOWN_SLOPE))
    f_win = int(kw.get("FAST_WIN", tg.FAST_WIN))
    s_win = int(kw.get("SLOW_WIN", tg.SLOW_WIN))
    s_n = int(kw.get("SLOPE_N", tg.SLOPE_N))
    style = _quad_style_dual(f_win, s_win) if dual else QUAD_STYLE

    px_full = tg.load(key)
    sm_full = tg.state_machine(px_full, key, **kw)
    start = px_full.index[min(tg.WARMUP, max(len(px_full) - 2, 0))]   # 与年线口径同一样本窗口
    if years:                                                          # 只截最近 N 年做近看
        i0 = px_full.index.searchsorted(px_full.index[-1] - pd.Timedelta(days=int(years * 365.25)))
        start = px_full.index[max(i0, px_full.index.get_loc(start), 0)]
    px, sm = px_full.loc[start:], sm_full.loc[start:]
    st, st_tr = sm["state"], sm["state_trend"]      # st = 趋势档位 × 波动系数（实际仓位）
    sc, quad = sm["vol_scale"], sm["quad"]
    idx = px.index
    slow = (px_full.rolling(s_win, min_periods=s_win).mean().loc[start:] if dual else None)
    fast_dn = (px_full.rolling(dw, min_periods=dw).mean().loc[start:] if asym else None)

    # 桥接段（A500：000051 沪深300 联接 -> 023299）
    cut = None
    if tg.rule.LEGS[key].get("otc_proxy"):
        _cut = pd.Timestamp(tg.rule.LEGS[key]["otc_start"])
        if px.index[0] < _cut < px.index[-1]:
            cut = px.index[px.index.searchsorted(_cut)]

    fee = tg.fee_of(key)
    if years:
        # 区间图：档位接续历史（不在窗口首日重置成满带），净值切片后归 1
        nav = tg.run_leg(sm_full["state"], px_full, fee).loc[idx]
        nav = nav / nav.iloc[0]
        hold = px / px.iloc[0]
    else:
        nav = tg.run_leg(st, px, fee)
        hold = tg.run_leg(pd.Series(1.0, index=px.index), px, fee)
    pn, ph = tg.perf(nav), tg.perf(hold)
    ops = tg.actions(st_tr)                          # 操作点只标「趋势换档」，缩放变化看下格
    ops_all = tg.actions(st)
    yrs = (idx[-1] - idx[0]).days / 365.25

    lines = [px, sm["ma"]] + ([slow] if dual else []) + ([fast_dn] if asym else [])
    lo = float(min(s.min() for s in lines)) * 0.94
    hi = float(max(s.max() for s in lines)) * 1.06

    fig = plt.figure(figsize=(13.2, 8.6))
    gs = fig.add_gridspec(2, 1, height_ratios=[4.0, 1.0], hspace=0.07,
                          left=0.058, right=0.975, top=0.792, bottom=0.165)
    ax = fig.add_subplot(gs[0])
    axp = fig.add_subplot(gs[1], sharex=ax)

    # ---- 上格：趋势线 + 象限底色 + 操作点
    _quad_spans(ax, idx, quad, lo, hi, style)
    if dual:
        ax.plot(idx, slow.to_numpy(), color=C_SLOW, lw=1.1, zorder=2)      # 慢线在下层
        ax.plot(idx, sm["ma"].to_numpy(), color=C_FAST, lw=1.3, zorder=2)  # 快线
    elif asym:
        ax.plot(idx, fast_dn.to_numpy(), color=C_SLOW, lw=1.05, ls="--", zorder=2)  # 快线（只压）
        ax.plot(idx, sm["ma"].to_numpy(), color=C_FAST, lw=1.30, zorder=2)          # 慢线（把门）
    else:
        ax.plot(idx, sm["ma"].to_numpy(), color=C_MA, lw=1.25, zorder=2)
    ax.plot(idx, px.to_numpy(), color=C_PRICE, lw=1.3, zorder=3)
    ax.set_ylim(lo, hi)
    if hi / lo > 4.0:                      # 长牛腿（纳指/标普）线性轴会把前几年压扁，换对数轴
        ax.set_yscale("log")
        ax.set_ylim(lo, hi)
        ticks = [t for t in (1, 1.5, 2, 3, 4, 5, 6, 8, 10, 12, 15) if lo <= t <= hi]
        ax.set_yticks(ticks)
        ax.set_yticklabels([f"{t:g}" for t in ticks])
        ax.set_ylabel("场外复权净值（对数轴）", fontsize=10)
    else:
        ax.set_ylabel("场外复权净值", fontsize=10)
    ax.grid(ls="--", lw=0.6, alpha=0.30, zorder=1)
    ax.tick_params(labelbottom=False)

    if ops:
        bd = [o["date"] for o in ops if o["kind"] == "buy"]
        sd = [o["date"] for o in ops if o["kind"] == "sell"]
        ax.plot(bd, [float(px.loc[d]) for d in bd], ls="none", marker="^", ms=7.5,
                mfc="none", mec=C_BUY, mew=1.4, zorder=6)
        ax.plot(sd, [float(px.loc[d]) for d in sd], ls="none", marker="v", ms=7.5,
                mfc="none", mec=C_SELL, mew=1.4, zorder=6)
        show = ops if len(ops) <= 14 else ops[-6:]
        for o in show:
            up = o["kind"] == "buy"
            ax.annotate(tg.LEVEL_TXT.get(round(o["to"], 6), f"{o['to']:.0%}"),
                        (o["date"], float(px.loc[o["date"]])), textcoords="offset points",
                        xytext=(0, 9 if up else -15), ha="center", fontsize=7.2,
                        color=C_BUY if up else C_SELL, zorder=7)

    if cut is not None:
        ax.axvspan(idx[0], cut, color="#9aa5b1", alpha=0.10, lw=0, zorder=1)
        ax.axvline(cut, color="#7b8794", lw=0.9, ls="--", zorder=2)
        ax.text(cut, 0.975, f"{cut.date()} 起为 {meta['code']} 真实净值（此前 000051 桥接） ",
                transform=ax.get_xaxis_transform(), va="top", ha="right",
                fontsize=8.3, color="#5b6b7a")

    # ---- 下格：带内档位阶梯（虚线灰 = 趋势档位，实线 = 趋势 × 波动层后的实际仓位）
    axp.step(idx, st_tr.to_numpy(), where="post", color="#b6c0cb", lw=0.9, ls="--",
             zorder=2, label="趋势档位")
    axp.fill_between(idx, 0, st.to_numpy(), step="post", color=C_STEP, alpha=0.14, lw=0, zorder=1)
    axp.step(idx, st.to_numpy(), where="post", color=C_STEP, lw=1.2, zorder=3,
             label="实际仓位（× 波动系数）")
    axp.set_ylim(-0.02, 1.10)
    axp.set_yticks([tg.OBSERVE, tg.HALF, tg.FULL])
    axp.set_yticklabels([f"观察 {tg.OBSERVE:.0%}", f"半仓 {tg.HALF:.0%}", f"满带 {tg.FULL:.0%}"],
                        fontsize=8.2, color="#6b7a8c")
    axp.set_ylabel("带内仓位", fontsize=9, color="#6b7a8c")
    axp.grid(ls="--", lw=0.55, alpha=0.30)
    axp.tick_params(axis="y", colors="#6b7a8c", length=2)
    for a in (ax, axp):
        for s in ("top", "right"):
            a.spines[s].set_visible(False)
    if years:
        axp.xaxis.set_major_locator(mdates.MonthLocator(interval=6))
        axp.xaxis.set_major_formatter(mdates.DateFormatter("%Y-%m"))
    else:
        axp.xaxis.set_major_locator(mdates.YearLocator())
        axp.xaxis.set_major_formatter(mdates.DateFormatter("%Y"))
    axp.set_xlabel("成交日 = 信号次日（T+1 净值）", fontsize=9, color="#5b6b7a")

    # ---- 图例（放在标题与坐标区之间，两行）
    if dual:
        line_hd = [
            Line2D([], [], color=C_PRICE, lw=1.3, label=f"{meta['name']} 复权净值"),
            Line2D([], [], color=C_FAST, lw=1.3, label=f"MA{f_win} 快线（读数主线）"),
            Line2D([], [], color=C_SLOW, lw=1.1, label=f"MA{s_win} 慢线"),
        ]
    elif asym:
        line_hd = [
            Line2D([], [], color=C_PRICE, lw=1.3, label=f"{meta['name']} 复权净值"),
            Line2D([], [], color=C_FAST, lw=1.3, label=f"MA{tg.MA_WIN} 慢线（加仓把门）"),
            Line2D([], [], color=C_SLOW, lw=1.05, ls="--", label=f"MA{dw} 快线（只压不抬）"),
        ]
    else:
        line_hd = [
            Line2D([], [], color=C_PRICE, lw=1.3, label=f"{meta['name']} 复权净值"),
            Line2D([], [], color=C_MA, lw=1.25, label=f"MA{tg.MA_WIN} 年线"),
        ]
    handles = line_hd + [
        Line2D([], [], ls="none", marker="^", ms=8, mfc="none", mec=C_BUY,
               label=f"加仓 ▲（{sum(1 for o in ops if o['kind'] == 'buy')}）"),
        Line2D([], [], ls="none", marker="v", ms=8, mfc="none", mec=C_SELL,
               label=f"减仓 ▼（{sum(1 for o in ops if o['kind'] == 'sell')}）"),
        Line2D([], [], color=C_STEP, lw=1.2, label="实际仓位（下格实线）"),
        Line2D([], [], color="#b6c0cb", lw=0.9, ls="--", label="趋势档位（下格虚线）"),
    ]
    handles += [Patch(facecolor=c, alpha=0.30, label=lab) for c, lab in style.values()]
    ax.legend(handles=handles, loc="lower left", bbox_to_anchor=(0.0, 1.005, 1.0, 0.10),
              ncol=4, mode="expand", fontsize=8.2, frameon=False,
              columnspacing=1.0, handletextpad=0.5, borderaxespad=0.2)

    gate_txt = "真实操作点（默认套闸门）" if meta["gate"] else "假设套闸门的操作点（默认不套，仅对照）"
    if dual:
        quad_txt = f"双均线 {f_win}/{s_win} × {s_n} 日斜率四象限"
    elif asym:
        quad_txt = f"非对称：减仓 MA{dw}+{ds} 日 / 加仓 MA{tg.MA_WIN}+{s_n} 日（两套取 min）"
    else:
        quad_txt = "年线位置 × 年线斜率四象限"
    win_txt = f"近 {years:g} 年 · " if years else ""
    if tg.VOL_USE and tg.VOL_MODE == "adaptive":
        vol_txt = f" + 波动层 q{tg.VOL_REF_Q * 100:.0f}×{tg.VOL_GAIN:g}"
    elif tg.VOL_USE:
        vol_txt = f" + 波动层 {tg.VOL_TARGET:.0%}"
    else:
        vol_txt = "（波动层关）"
    fig.text(0.045, 0.972,
             f"{meta['name']}（{meta['code']}）{win_txt}趋势线 · {quad_txt} · {gate_txt}{vol_txt}",
             ha="left", fontsize=13.2, color="#1b2b3a", weight="bold")
    fig.text(0.045, 0.935,
             f"{idx[0].date()} ~ {idx[-1].date()}（{yrs:.1f} 年）· 闸门 {pn['ann']:+.2%}/年、"
             f"回撤 {pn['mdd']:.2%}、夏普 {pn['sharpe']:.2f}、均趋势档位 {tg.exposure(st_tr):.0%}、"
             f"均实际仓位 {tg.exposure(st):.0%}（均缩放 {sc.mean():.2f}）"
             f"   |   恒持满带 {ph['ann']:+.2%}/年、回撤 {ph['mdd']:.2%}、夏普 {ph['sharpe']:.2f}",
             ha="left", fontsize=10, color="#3d4f5f")

    if dual:
        head = (f"四象限 = 「close ≥ MA{f_win} 且 MA{f_win} ≥ MA{s_win}」×「MA{f_win} 近 {s_n} 日"
                f"斜率朝上/朝下」；两个条件都成立（绿）才给加仓许可，其余三格不给。")
    elif asym:
        head = (f"非对称口径：两套读数各自算一次目标档位、取 min（快线只能压更低、不能抬更高）—— "
                f"减仓由 MA{dw} 近 {ds} 日斜率把门（跌破满 {tg.REDUCE_DAYS} 日减半 → 满 "
                f"{tg.CLEAR_DAYS} 日未收复清至观察），加仓仍由 MA{tg.MA_WIN} 近 {s_n} 日斜率把门。"
                "背景色带标的仍是慢口径（主读数）的象限。")
    else:
        head = ("四象限 = 「close 在 %d 日均线上/下」×「%d 日均线近 %d 日斜率朝上/朝下」；"
                "两个都朝上（绿）才给加仓许可，其余三格不给。" % (tg.MA_WIN, tg.MA_WIN, tg.SLOPE_N))
    note = [
        head,
        f"降档阶梯：跌破快线/年线满 {tg.REDUCE_DAYS} 日减半（{tg.HALF:.0%}）→ 满 "
        f"{tg.CLEAR_DAYS} 日未收复清至观察（{tg.OBSERVE:.0%}）；同一腿两次调仓间隔 ≥"
        f"{tg.ACTION_GAP} 个交易日；扣费 = 该腿真实申赎费 + 单边滑点 {tg.SLIP:.1%}。",
    ]
    if tg.VOL_USE:
        if tg.VOL_MODE == "adaptive":
            vol_rule = (f"波动层：目标 = 该腿波动历史 {tg.VOL_REF_Q:.0%} 分位 × {tg.VOL_GAIN:g}，"
                        f"除以 {tg.VOL_WINDOW} 日已实现波动得系数（≥{tg.VOL_MIN_LEVEL:g}、≤1，"
                        f"死区 {tg.VOL_DEADBAND:g}、限速 {tg.VOL_MIN_GAP} 日）")
        else:
            vol_rule = (f"波动层：目标 = 固定 {tg.VOL_TARGET:.0%} 年化波动，除以 "
                        f"{tg.VOL_WINDOW} 日已实现波动得系数（≥{tg.VOL_MIN_LEVEL:g}、≤1）")
        note.append(vol_rule + "；下格虚线 = 趋势档位，实线 = 乘上系数后的实际仓位（只压满带）。")
    if years:
        note.insert(1, f"图为最近 {years:g} 年的切片：档位自更早的历史延续（不在窗口首日重置成满带），"
                       "年化 / 回撤 / 夏普按该区间净值计算，与全历史图不可直接比较。")
    if meta["gate"]:
        note.append(f"{meta['name']} 在默认作用域内（无可靠现金流 / 估值锚，价格是主要信息源），"
                    "图上即真实档位与真实操作点。机械规则输出，非投资建议。")
    else:
        note.append(f"{meta['name']} 属宽基，默认不套闸门（估值锚已隐含均值回归，再叠趋势过滤会互相抵消）；"
                    "本图的操作点与阶梯是 GATE_ALL=True 的对照结果，只看规则形状，不是建议。")

    fig.text(0.045, 0.014, "\n".join(note), fontsize=8.4, color="#5b6b7a", linespacing=1.5,
             va="bottom")

    out = os.path.join(out_dir, f"_v5_trend_{key}{tag}{f'_{years:g}y' if years else ''}.png")
    fig.savefig(out, dpi=135, facecolor="white")
    plt.close(fig)

    print(f"图: {out}")
    print(f"  {meta['name']} {idx[0].date()} ~ {idx[-1].date()}（{yrs:.1f} 年）· "
          f"{'套闸门' if meta['gate'] else '不套闸门(图示为假设)'} · 趋势操作 {len(ops)} 笔"
          f"（含缩放共 {len(ops_all)} 次权重变化）· "
          f"闸门 {pn['ann']:+.2%}/年 回撤 {pn['mdd']:.2%} 夏普 {pn['sharpe']:.2f} "
          f"均档位 {tg.exposure(st_tr):.0%} → 均仓位 {tg.exposure(st):.0%} | "
          f"恒持满带 {ph['ann']:+.2%}/年 回撤 {ph['mdd']:.2%}")
    if tg.VOL_USE:
        print(f"  波动层 均缩放 {sc.mean():.2f} · 最低 {sc.min():.2f} · "
              f"满带被压日占比 {float(((st_tr >= 1 - 1e-9) & (sc < 0.999)).mean()):.0%}")
    for o in ops:
        why = sm["done"].loc[o["date"]]
        if dual:                                    # target_of 的理由是年线措辞，换成快线说法
            why = why.replace("年线", f"MA{f_win}")
        print(f"    {o['date'].date()} {'加仓' if o['kind'] == 'buy' else '减仓'} "
              f"{o['frm']:.0%}→{o['to']:.0%}  {why}")
    return out


def main():
    ap = argparse.ArgumentParser(description="V5 趋势线 + 操作点单腿图（默认 A500 / 科创50 都画）")
    ap.add_argument("--leg", choices=list(SPEC), action="append",
                    help="只画指定腿（可重复）；默认全部")
    ap.add_argument("--dual", nargs=2, type=int, metavar=("FAST", "SLOW"),
                    help="改用双均线读数（如 --dual 50 150）；给了它默认只画 a500")
    ap.add_argument("--slope", type=int, default=10, help="--dual 时的斜率回看日数（默认 10）")
    ap.add_argument("--years", type=float, help="只画最近 N 年（如 --years 3）")
    args = ap.parse_args()

    kw, tag = {}, ""
    if args.dual:
        kw = dict(KIND="dual_ma", FAST_WIN=args.dual[0], SLOW_WIN=args.dual[1], SLOPE_N=args.slope)
        tag = f"_dual{args.dual[0]}-{args.dual[1]}"
    for key in (args.leg or (["a500"] if args.dual else list(SPEC))):
        plot(key, kw=kw, tag=tag, years=args.years)


if __name__ == "__main__":
    main()
