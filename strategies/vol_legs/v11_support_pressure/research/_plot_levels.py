# -*- coding: utf-8 -*-
"""V11 · 支撑位 / 压力位快照图（单腿、近期窗口）。

用途：把 `support_pressure.pivots` 里**已经确认**的摆动低点/高点画到近期走势上，
并给出「现价离最近支撑/压力多远」的读数 —— 一句话回答「现在在什么位置」。

口径（与 v11 报告完全一致，改了会与 `_v11_support_bt.md` 对不上）：
- 价格：默认**场外复权净值**（`--mode otc`），可选 `--mode exchange` 看场内 ETF 收盘。
- 摆动点：`SWING_N=10`（21 日窗口的极值），**滞后 10 日确认** ——
  所以图上的标记画在「确认日」，不是极值日；水平线也从确认日画起（那天才可知）。
- 支撑/压力 = 已确认枢轴低点/高点，按「离现价远近」排序取前 N 个。
- 副图的 `距支撑%` 与 v11 的 `dist` 同一列：`close/support − 1`，
  绿色带 = v11 定义的「到线带」`0~BAND`（默认 2%），跌破 0 就是破位。

产物：`research/_levels_{leg}.png`（`--mode exchange` 时为 `research/_levels_{leg}_exchange.png`）

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
_V11 = os.path.dirname(_HERE)
if _V11 not in sys.path:
    sys.path.insert(0, _V11)

import support_pressure as sp  # noqa: E402

C_PX, C_SUP, C_RES, C_NOW = "#1b2b3a", "#1f8a4c", "#c0392b", "#c9a227"


def levels(px, cur, n_side=6, since=None, max_dist=0.15, merge=0.006):
    """-> (supports, resistances)，每项 dict(price, date, dist, broken)。

    - `since`：更早的枢轴不收集（图上不画太老的位置）；价位本身按**全历史**枢轴算。
    - `max_dist`：只保留距现价 ±15% 以内的位置（更远的支撑对「近期」没意义）。
    - `merge`：同侧价差 < 0.6% 的枢轴合并成一档（双底/双顶会重复报同一个价位），
      保留**最近确认**的那一次。
    - `broken`：前低落在现价**上方** = 这条支撑已经被跌穿，单独保留最多 1 档作提示
      （它不再是支撑，正常支撑仍按「现价下方」收集）；压力侧对称的「已上破」不收，
      避免与破位提示重复。
    """
    low, high = sp.pivots(px, sp.SWING_N)
    sup, res = [], []
    for s, bucket, sign in ((low, sup, -1), (high, res, 1)):
        s = s.dropna()
        if since is not None:
            s = s[s.index >= since]
        for d, v in s.items():
            dv = float(v) / cur - 1.0
            broken = dv > 0 if sign < 0 else dv < 0      # 支撑在上方=破位
            if abs(dv) > max_dist or (sign > 0 and broken):
                continue
            bucket.append(dict(price=float(v), date=d, dist=dv, broken=broken))

    def _merge(items, nearest_first):
        items.sort(key=lambda x: x["price"], reverse=nearest_first)
        out = []
        for it in items:
            if out and abs(it["price"] - out[-1]["price"]) / cur <= merge:
                if it["date"] > out[-1]["date"]:      # 保留最近确认的那一次
                    out[-1] = it
                continue
            out.append(it)
        return out

    sup = _merge(sup, True)      # 支撑：价格从高到低（离现价近的在前）
    res = _merge(res, False)     # 压力：价格从低到高
    broken = [x for x in sup if x["broken"]][:1]     # 破位的前低最多留 1 档，且排到最后
    sup = [x for x in sup if not x["broken"]][:n_side]
    return sup + broken, res[:n_side]


def plot(leg="kc50", days=500, n_side=6, mode=None, out_dir=_HERE, **kw):
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    from matplotlib.gridspec import GridSpec

    plt.rcParams["font.sans-serif"] = ["Microsoft YaHei", "SimHei", "DejaVu Sans"]
    plt.rcParams["axes.unicode_minus"] = False

    px = sp.load(leg, mode=mode).dropna()
    F = sp.build(px, leg, **kw)
    win = px.iloc[-int(days):]
    cur = float(px.iloc[-1])
    cur_date = px.index[-1]
    sup, res = levels(px, cur, n_side=n_side, since=win.index[0] - pd.Timedelta(days=120))

    name = sp.rule.LEGS[leg]["name"]
    code = sp.rule.LEGS[leg].get("otc") if mode is None else sp.rule.LEGS[leg].get("signal")
    kind = "场外复权净值" if mode is None else f"场内收盘（{sp.rule.LEGS[leg]['signal_name']}）"

    fig = plt.figure(figsize=(13.6, 8.6))
    gs = GridSpec(2, 1, figure=fig, height_ratios=[3.4, 1.0], hspace=0.16)
    ax = fig.add_subplot(gs[0])
    axd = fig.add_subplot(gs[1], sharex=ax)

    ax.plot(win.index, win.to_numpy(dtype=float), color=C_PX, lw=1.5, label=kind, zorder=5)
    ax.axhline(cur, color=C_NOW, lw=1.0, ls="--", alpha=0.8, zorder=2)
    ax.scatter([cur_date], [cur], s=40, color=C_NOW, zorder=7)

    x_right = win.index[-1]
    span = float(win.max() - win.min())
    labels = []          # (price, text, color, fontsize)

    def draw(items, col, side_txt):
        for i, it in enumerate(items):
            lw = 1.5 if i == 0 else (1.0 if i < 3 else 0.7)
            alpha = 0.95 if i == 0 else (0.7 if i < 3 else 0.45)
            d0 = max(it["date"], win.index[0])
            ax.hlines(it["price"], d0, x_right, color=col, lw=lw, ls="-" if i == 0 else "--",
                      alpha=alpha, zorder=3)
            ax.scatter([it["date"]], [it["price"]], marker="^" if col == C_SUP else "v",
                       s=26 if i < 3 else 18, color=col, alpha=alpha, zorder=6)
            tag = " 已破位" if it["broken"] else ""
            labels.append((it["price"],
                           f"{side_txt}{it['price']:.4f} {it['dist'] * 100:+.1f}%{tag}",
                           col, 8.4 if i == 0 else 7.8))

    draw(sup, C_SUP, "支撑 ")
    draw(res, C_RES, "压力 ")

    pad = span * 0.06
    lo = min([float(win.min())] + [i["price"] for i in sup]) - pad
    hi = max([float(win.max())] + [i["price"] for i in sup + res]) + pad

    # 右侧标签：按价位排序后强制最小间距（否则相邻两档的文字会叠在一起），用细引线连回真实价位
    gap = (hi - lo) * 0.038
    x_txt = x_right + pd.Timedelta(days=int(len(win) * 0.035))
    placed = []
    prev_y = None
    for price, text, col, fsz in sorted(labels, key=lambda z: z[0]):
        y = price if prev_y is None else max(price, prev_y + gap)
        prev_y = y
        placed.append((price, y, text, col, fsz))
    if placed:                                    # 文字被推挤后可能顶出/掉出坐标区，边界跟着让
        lo = min(lo, placed[0][1] - pad * 0.5)
        hi = max(hi, placed[-1][1] + pad * 0.5)
    ax.set_ylim(lo, hi)
    for price, y, text, col, fsz in placed:
        ax.annotate(text, xy=(x_right, price), xytext=(x_txt, y),
                    fontsize=fsz, va="center", ha="left", color=col,
                    arrowprops=dict(arrowstyle="-", lw=0.6, color=col, alpha=0.55,
                                    shrinkA=0, shrinkB=1), annotation_clip=False)
    ax.set_ylabel(kind, fontsize=10)
    ax.grid(ls="--", lw=0.55, alpha=0.28)
    ax.legend(loc="upper left", fontsize=8.6, frameon=False)
    ax.tick_params(labelbottom=False, labelsize=8.5)

    # 副图：距最近前低支撑的距离（v11 的 dist）
    dist = (F["dist"].reindex(win.index) * 100.0)
    axd.plot(win.index, dist.to_numpy(dtype=float), color="#4b5d6b", lw=1.0)
    axd.axhspan(0.0, float(sp.params(**kw)["BAND"]) * 100.0, color="#2e7d32", alpha=0.16, lw=0)
    axd.axhline(0.0, color=C_SUP, lw=1.0, ls=":")
    axd.set_ylim(-15, 15)
    axd.set_ylabel("距前低支撑 %", fontsize=9)
    axd.grid(ls="--", lw=0.5, alpha=0.25)
    axd.tick_params(labelsize=8)
    axd.text(0.005, 0.86, "绿带 = v11「到线带」0~%.0f%%，跌破 0 = 破位"
             % (float(sp.params(**kw)["BAND"]) * 100.0),
             transform=axd.transAxes, fontsize=7.8, color="#2e7d32", alpha=0.85)

    near = next((x for x in sup if not x["broken"]), None)   # 已破位的那档不能当「最近支撑」
    near_r = res[0] if res else None
    fig.text(0.045, 0.955, f"{name}（{code}）· 支撑位 / 压力位快照",
             fontsize=15, color="#1b2b3a", weight="bold", ha="left")
    sub = (f"{px.index[-1].date()} 收盘 {cur:.4f} · 窗口 {win.index[0].date()} ~ "
           f"{win.index[-1].date()}（{len(win)} 个交易日）· 摆动点 {sp.SWING_N} 日确认")
    fig.text(0.045, 0.925, sub, fontsize=10, color="#3d4f5f", ha="left")
    # 「贴线区」按 v11 的 dist = close/support − 1 ∈ [0, BAND]，与本图标注的
    # 「价位相对现价」（负数 = 在下方）符号相反，别把两者混用一个阈值。
    on_line = bool(near is not None and 0.0 <= cur / near["price"] - 1.0 <= sp.BAND)
    tone = "贴线区（v11 测到的最差买点）" if on_line else "非贴线区"
    txt = (f"最近支撑 {near['price']:.4f}（{near['dist'] * 100:+.1f}%，{near['date'].date()} 确认）"
           if near else "下方无已确认支撑")
    txt += (f" · 最近压力 {near_r['price']:.4f}（{near_r['dist'] * 100:+.1f}%）"
            if near_r else " · 上方无已确认压力")
    fig.text(0.045, 0.897, txt + f" · 当前位置：{tone}", fontsize=9.6, color="#5b3a29", ha="left")

    note = [
        "▲ / ▼ = 已确认的摆动低点 / 高点（21 日窗口极值，**滞后 10 个交易日确认**，无前视）；"
        "水平线从确认日画起 —— 更早的部分当时还不可知。",
        "实线（最粗）= 离现价最近的一档，虚线依次是更远的位置；右侧标注为价位与距现价的百分比。",
        "「已破位」= 该前低落在现价**上方**（收盘已跌穿它），列在最后一条作提示，不再是支撑；"
        "同侧价差 < 0.6% 的枢轴合并成一档，只保留最近确认的那次。",
        "「前低支撑」只认**收盘价**（本地无 OHLCV），所以真实影线低点会比图上更极端一点。",
        "支撑/压力是**观察位**，不是买卖信号：v11 实测「贴线买入」的前向收益低于趋势日均值（报告 §8.1）。",
    ]
    fig.text(0.045, 0.016, "\n".join(note), fontsize=8.3, color="#5b6b7a",
             linespacing=1.55, va="bottom")

    fig.subplots_adjust(left=0.07, right=0.855, top=0.86, bottom=0.175)
    # 两种价格口径共用一张文件名会互相覆盖（写图的最后一跑赢），非默认口径加后缀
    out = os.path.join(out_dir, f"_levels_{leg}{'' if mode is None else '_' + mode}.png")
    fig.savefig(out, dpi=140, facecolor="white")
    plt.close(fig)
    return out, dict(leg=leg, name=name, date=cur_date, close=cur,
                     supports=sup, resistances=res, on_line=on_line,
                     dist_near_sup=(near["dist"] if near else None),
                     dist_near_res=(near_r["dist"] if near_r else None))


def main():
    ap = argparse.ArgumentParser(description="支撑位/压力位快照（v11 口径）")
    ap.add_argument("--leg", default="kc50")
    ap.add_argument("--days", type=int, default=500, help="绘图窗口交易日（默认 500 ≈ 2 年）")
    ap.add_argument("--levels", type=int, default=6, help="每侧最多画几档")
    ap.add_argument("--mode", default=None, choices=[None, "otc", "exchange"])
    args = ap.parse_args()
    out, info = plot(args.leg, days=args.days, n_side=args.levels, mode=args.mode)
    print("出图:", out)
    print(f"{info['name']} {info['date'].date()} 收盘 {info['close']:.4f} · "
          f"{'贴线区' if info['on_line'] else '非贴线区'}")
    for lab, items in (("支撑", info["supports"]), ("压力", info["resistances"])):
        for i, it in enumerate(items):
            print(f"  {lab}{i + 1} {it['price']:.4f}  {it['dist'] * 100:+.2f}%  "
                  f"确认于 {it['date'].date()}"
                  + ("  【已破位】" if it["broken"] else ""))


if __name__ == "__main__":
    main()
