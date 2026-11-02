# -*- coding: utf-8 -*-
"""V5（趋势四象限闸门）· 趋势线 + 操作点单腿图：中证A500 / 科创50。

图的读法：
- **上格深色线** = 场外复权净值（含分红再投 / 汇率 / 跟踪误差）；**橙线** = MA250（年线）；
- **上格背景色带** = 当日所处象限（绿 = 上涨趋势、黄 = 反弹、橙 = 高位回调、红 = 下跌趋势）——
  四格就是「close 在年线上/下」×「年线 20 日斜率朝上/朝下」，色带的分界 = 象限切换；
- **▲/▼ 标在价格线上** = 该笔调仓的成交日（T 日盘后算、T+1 净值成交）在指数上的价位，
  旁边小字是调完之后的档位（满带 / 半仓 / 观察仓）；
- **下格阶梯** = 带内档位（占本腿目标带的比例），它不是 pp：50% = 只露半条带。

两条腿的口径差别（重要）：
- **科创50（`011609`）**：默认**套闸门**，图上就是本变种的真实操作点。
- **中证A500（`023299`）**：默认**不套闸门**（宽基有估值锚，叠趋势过滤会互相抵消，见 README §2），
  所以没有任何真实操作点；图上的 ▲/▼ 是**假设套上闸门**（`GATE_ALL=True`）时的位置，只作对照，
  不是建议。另：2025-03-27 之前是 `000051`（沪深300 联接）桥接段，用灰色底纹标出。

产物（本目录）：`_v5_trend_a500.png` / `_v5_trend_kc50.png`。机械规则输出，非投资建议。
"""
import argparse
import os
import sys

import pandas as pd

try:
    sys.stdout.reconfigure(encoding="utf-8")  # type: ignore[attr-defined]
except Exception:  # noqa: BLE001
    pass

_HERE = os.path.dirname(os.path.abspath(__file__))
_V5 = os.path.dirname(_HERE)
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

# key -> 元信息；gate=False 的腿画的是「若套闸门」的对照操作点
SPEC = {
    "a500": dict(name="中证A500", code="023299", gate=False),
    "kc50": dict(name="科创50", code="011609", gate=True),
}


def _quad_spans(ax, idx, quad, y0, y1):
    """按象限铺背景色（fill_between + where，比逐段 axvspan 快且边界精确到日）。"""
    for name, (color, _label) in QUAD_STYLE.items():
        mask = (quad == name).to_numpy()
        if mask.any():
            ax.fill_between(idx, y0, y1, where=mask, color=color, alpha=0.10, lw=0, zorder=0)


def plot(key, out_dir=_HERE):
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.dates as mdates
    import matplotlib.pyplot as plt
    from matplotlib.lines import Line2D
    from matplotlib.patches import Patch

    plt.rcParams["font.sans-serif"] = ["Microsoft YaHei", "SimHei", "DejaVu Sans"]
    plt.rcParams["axes.unicode_minus"] = False

    meta = SPEC[key]
    px_full = tg.load(key)
    kw = {} if meta["gate"] else {"GATE_ALL": True}          # A500：算「若套闸门」的对照操作点
    sm_full = tg.state_machine(px_full, key, **kw)
    start = px_full.index[min(tg.WARMUP, max(len(px_full) - 2, 0))]
    px, sm = px_full.loc[start:], sm_full.loc[start:]
    st, quad = sm["state"], sm["quad"]
    idx = px.index

    # 桥接段（A500：000051 沪深300 联接 -> 023299）
    cut = None
    if tg.rule.LEGS[key].get("otc_proxy"):
        _cut = pd.Timestamp(tg.rule.LEGS[key]["otc_start"])
        if px.index[0] < _cut < px.index[-1]:
            cut = px.index[px.index.searchsorted(_cut)]

    fee = tg.fee_of(key)
    nav = tg.run_leg(st, px, fee)
    hold = tg.run_leg(pd.Series(1.0, index=px.index), px, fee)
    pn, ph = tg.perf(nav), tg.perf(hold)
    ops = tg.actions(st)
    yrs = (idx[-1] - idx[0]).days / 365.25

    lo = float(min(px.min(), sm["ma"].min())) * 0.94
    hi = float(max(px.max(), sm["ma"].max())) * 1.06

    fig = plt.figure(figsize=(13.2, 8.6))
    gs = fig.add_gridspec(2, 1, height_ratios=[4.0, 1.0], hspace=0.07,
                          left=0.058, right=0.975, top=0.792, bottom=0.135)
    ax = fig.add_subplot(gs[0])
    axp = fig.add_subplot(gs[1], sharex=ax)

    # ---- 上格：趋势线 + 象限底色 + 操作点
    _quad_spans(ax, idx, quad, lo, hi)
    ax.plot(idx, sm["ma"].to_numpy(), color=C_MA, lw=1.25, zorder=2)
    ax.plot(idx, px.to_numpy(), color=C_PRICE, lw=1.3, zorder=3)
    ax.set_ylim(lo, hi)
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
        ax.axvspan(idx[0], cut, color="#9aa5b1", alpha=0.14, lw=0, zorder=1)
        ax.axvline(cut, color="#7b8794", lw=0.9, ls="--", zorder=2)
        ax.text(cut, 0.975, f"{cut.date()} 起为 {meta['code']} 真实净值（此前 000051 桥接） ",
                transform=ax.get_xaxis_transform(), va="top", ha="right",
                fontsize=8.3, color="#5b6b7a")

    # ---- 下格：带内档位阶梯
    axp.step(idx, st.to_numpy(), where="post", color=C_STEP, lw=1.15)
    axp.fill_between(idx, 0, st.to_numpy(), step="post", color=C_STEP, alpha=0.14, lw=0)
    axp.set_ylim(-0.02, 1.10)
    axp.set_yticks([tg.OBSERVE, tg.HALF, tg.FULL])
    axp.set_yticklabels([f"观察 {tg.OBSERVE:.0%}", f"半仓 {tg.HALF:.0%}", f"满带 {tg.FULL:.0%}"],
                        fontsize=8.2, color="#6b7a8c")
    axp.set_ylabel("带内档位", fontsize=9, color="#6b7a8c")
    axp.grid(ls="--", lw=0.55, alpha=0.30)
    axp.tick_params(axis="y", colors="#6b7a8c", length=2)
    for a in (ax, axp):
        for s in ("top", "right"):
            a.spines[s].set_visible(False)
    axp.xaxis.set_major_locator(mdates.YearLocator())
    axp.xaxis.set_major_formatter(mdates.DateFormatter("%Y"))
    axp.set_xlabel("成交日 = 信号次日（T+1 净值）", fontsize=9, color="#5b6b7a")

    # ---- 图例（放在标题与坐标区之间，两行）
    handles = [
        Line2D([], [], color=C_PRICE, lw=1.3, label=f"{meta['name']} 复权净值"),
        Line2D([], [], color=C_MA, lw=1.25, label=f"MA{tg.MA_WIN} 年线"),
        Line2D([], [], ls="none", marker="^", ms=8, mfc="none", mec=C_BUY,
               label=f"加仓 ▲（{sum(1 for o in ops if o['kind'] == 'buy')}）"),
        Line2D([], [], ls="none", marker="v", ms=8, mfc="none", mec=C_SELL,
               label=f"减仓 ▼（{sum(1 for o in ops if o['kind'] == 'sell')}）"),
        Line2D([], [], color=C_STEP, lw=1.15, label="带内档位（下格）"),
    ]
    handles += [Patch(facecolor=c, alpha=0.30, label=lab) for c, lab in QUAD_STYLE.values()]
    ax.legend(handles=handles, loc="lower left", bbox_to_anchor=(0.0, 1.005, 1.0, 0.10),
              ncol=3, mode="expand", fontsize=8.4, frameon=False,
              columnspacing=1.0, handletextpad=0.5, borderaxespad=0.2)

    gate_txt = "真实操作点（默认套闸门）" if meta["gate"] else "假设套闸门的操作点（默认不套，仅对照）"
    fig.text(0.045, 0.972, f"{meta['name']}（{meta['code']}）趋势线 · 年线位置 × 年线斜率四象限 · {gate_txt}",
             ha="left", fontsize=13.2, color="#1b2b3a", weight="bold")
    fig.text(0.045, 0.935,
             f"{idx[0].date()} ~ {idx[-1].date()}（{yrs:.1f} 年）· 闸门 {pn['ann']:+.2%}/年、"
             f"回撤 {pn['mdd']:.2%}、夏普 {pn['sharpe']:.2f}、平均档位 {tg.exposure(st):.0%}"
             f"   |   恒持满带 {ph['ann']:+.2%}/年、回撤 {ph['mdd']:.2%}、夏普 {ph['sharpe']:.2f}",
             ha="left", fontsize=10, color="#3d4f5f")

    note = [
        "四象限 = 「close 在 %d 日均线上/下」×「%d 日均线近 %d 日斜率朝上/朝下」；两个都朝上（绿）"
        "才给加仓许可，其余三格不给。" % (tg.MA_WIN, tg.MA_WIN, tg.SLOPE_N),
        f"降档阶梯：跌破年线满 {tg.REDUCE_DAYS} 日减半（{tg.HALF:.0%}）→ 满 {tg.CLEAR_DAYS} 日"
        f"未收复清至观察（{tg.OBSERVE:.0%}）；同一腿两次调仓间隔 ≥{tg.ACTION_GAP} 个交易日；"
        f"扣费 = 该腿真实申赎费 + 单边滑点 {tg.SLIP:.1%}。",
    ]
    if meta["gate"]:
        note.append("科创50 无现金流锚（亏损股权导致 ERP 失真），是本变种默认作用域之一，"
                    "图上即真实档位与真实操作点。机械规则输出，非投资建议。")
    else:
        note.append("中证A500 属宽基，默认不套闸门（估值锚已隐含均值回归，再叠趋势过滤会互相抵消）；"
                    "本图的操作点与阶梯是 GATE_ALL=True 的对照结果，只看规则形状，不是建议。")
    fig.text(0.045, 0.015, "\n".join(note), fontsize=8.4, color="#5b6b7a", linespacing=1.6,
             va="bottom")

    out = os.path.join(out_dir, f"_v5_trend_{key}.png")
    fig.savefig(out, dpi=135, facecolor="white")
    plt.close(fig)

    print(f"图: {out}")
    print(f"  {meta['name']} {idx[0].date()} ~ {idx[-1].date()}（{yrs:.1f} 年）· "
          f"{'套闸门' if meta['gate'] else '不套闸门(图示为假设)'} · 操作 {len(ops)} 笔 · "
          f"闸门 {pn['ann']:+.2%}/年 回撤 {pn['mdd']:.2%} 夏普 {pn['sharpe']:.2f} 档位均值 "
          f"{tg.exposure(st):.0%} | 恒持满带 {ph['ann']:+.2%}/年 回撤 {ph['mdd']:.2%}")
    for o in ops:
        print(f"    {o['date'].date()} {'加仓' if o['kind'] == 'buy' else '减仓'} "
              f"{o['frm']:.0%}→{o['to']:.0%}  {sm['done'].loc[o['date']]}")
    return out


def main():
    ap = argparse.ArgumentParser(description="V5 趋势线 + 操作点单腿图（默认 A500 / 科创50 都画）")
    ap.add_argument("--leg", choices=list(SPEC), action="append",
                    help="只画指定腿（可重复）；默认全部")
    args = ap.parse_args()
    for key in (args.leg or list(SPEC)):
        plot(key)


if __name__ == "__main__":
    main()
