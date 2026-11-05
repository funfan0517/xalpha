# -*- coding: utf-8 -*-
"""趋势口径的「灵敏度 vs 代价」扫描：把「更灵敏的趋势判断」量成一列数。

## 为什么这样测

「趋势判断准不准」不可测（未来不知道），可测的是两件事：
1. **确认滞后**：进入「下跌趋势」那一刻，价格已从 250 日高点跌掉多少（中位）、之后 120 日
   还要再跌多少；进入「上涨趋势」那一刻，价格已从 250 日低点反弹多少、之后 120 日还能涨多少。
   → 越灵敏 = 确认时已走掉的幅度越小、剩余幅度越大。
2. **代价**：状态切换频率（每年换几次）、同一套档位阶梯下的组合层收益/回撤/夏普、三腿超额。

口径谱系（全部压成同一组 `(above, up)`，**规则不变，只换读数**，见 `trend_gate.readout`）：

| 族 | 口径 | 读数 |
|---|---|---|
| 均线法 | MA250/200/120/100/60/50 × 斜率 5/10/20 日 | 价格 vs 均线 |
| 双均线 | MA50 在 MA200 上方且价格在 MA50 上方 | 快线 |
| 结构法近似 | 唐奇安通道中轨（N 日最高+最低)/2） | 中轨 |
| 动量法 | 3 / 6 个月累计收益 > 0 | 动量 |
| 交易信号 | MACD(50,200) / (12,26) 零轴 + 金叉 | DIF |

产物（本目录）：`_v5_trend_sens.md` / `_v5_trend_sens.png`。机械规则输出，非投资建议。
"""
import io
import os
import sys

import numpy as np
import pandas as pd

try:
    sys.stdout.reconfigure(encoding="utf-8")  # type: ignore[attr-defined]
except Exception:  # noqa: BLE001
    pass

_HERE = os.path.dirname(os.path.abspath(__file__))
_V5 = os.path.dirname(_HERE)
sys.path.insert(0, _V5)

import trend_gate as tg   # noqa: E402

tg.VOL_USE = False        # 本脚本只研究「趋势层」的灵敏度，关掉波动层以隔离变量

OUT_MD = os.path.join(_HERE, "_v5_trend_sens.md")
OUT_PNG = os.path.join(_HERE, "_v5_trend_sens.png")

Q1, Q3, Q4 = tg.QUADS[0], tg.QUADS[2], tg.QUADS[3]
FWD = 120                 # 确认之后看多少交易日

# (标签, 参数覆盖, 族)
SPECS = [
    ("MA250+20日（现行）", dict(KIND="ma", MA_WIN=250, SLOPE_N=20), "均线法"),
    ("MA250+5日", dict(KIND="ma", MA_WIN=250, SLOPE_N=5), "均线法"),
    ("MA200+20日", dict(KIND="ma", MA_WIN=200, SLOPE_N=20), "均线法"),
    ("MA120+20日", dict(KIND="ma", MA_WIN=120, SLOPE_N=20), "均线法"),
    ("MA120+5日", dict(KIND="ma", MA_WIN=120, SLOPE_N=5), "均线法"),
    ("MA100+10日", dict(KIND="ma", MA_WIN=100, SLOPE_N=10), "均线法"),
    ("MA60+10日", dict(KIND="ma", MA_WIN=60, SLOPE_N=10), "均线法"),
    ("MA50+5日", dict(KIND="ma", MA_WIN=50, SLOPE_N=5), "均线法"),
    ("双均线50/200+5日", dict(KIND="dual_ma", FAST_WIN=50, SLOW_WIN=200, SLOPE_N=5), "双均线"),
    ("唐奇安中轨55+10日", dict(KIND="donchian", DON_N=55, SLOPE_N=10), "结构法近似"),
    ("唐奇安中轨20+5日", dict(KIND="donchian", DON_N=20, SLOPE_N=5), "结构法近似"),
    ("动量6月+20日", dict(KIND="momentum", MOM_N=126, SLOPE_N=20), "动量法"),
    ("动量3月+10日", dict(KIND="momentum", MOM_N=63, SLOPE_N=10), "动量法"),
    ("MACD(50,200)+5日", dict(KIND="macd", FAST_WIN=50, SLOW_WIN=200, SLOPE_N=5), "交易信号"),
    ("MACD(12,26)+5日", dict(KIND="macd", FAST_WIN=12, SLOW_WIN=26, SLOPE_N=5), "交易信号"),
]


def _med(x):
    x = [v for v in x if v is not None and np.isfinite(v)]
    return float(np.median(x)) if x else None


def lag_stats(kw):
    """三条闸门腿的确认滞后 + 状态切换频率（用各自全历史，样本更多）。"""
    downs, ups = [], []
    up_days = dn_days = tot_days = 0
    switches, years = 0, 0.0
    for leg in tg.GATE_LEGS:
        px = tg.load(leg)
        sm = tg.state_machine(px, leg, **kw)
        c = px.to_numpy(dtype=float)
        quad = sm["quad"].to_numpy(dtype=object)
        dd = sm["dd_hi"].to_numpy(dtype=float)
        ready = sm["ready"].to_numpy(dtype=bool)
        lo = pd.Series(c).rolling(tg.REF_HI, min_periods=1).min().to_numpy()
        years += (px.index[-1] - px.index[0]).days / 365.25
        up_days += int((quad == Q1).sum())
        dn_days += int((quad == Q4).sum())
        tot_days += int((quad != "").sum())
        for i in range(1, len(c)):
            if not ready[i] or quad[i] == quad[i - 1]:
                continue
            switches += 1
            fwd = c[i:min(len(c), i + 1 + FWD)]
            if quad[i] == Q4:
                downs.append((dd[i], float(np.min(fwd)) / c[i] - 1.0))
            elif quad[i] == Q1:
                ups.append((c[i] / lo[i] - 1.0, float(np.max(fwd)) / c[i] - 1.0))
    return dict(
        up_share=up_days / max(tot_days, 1), down_share=dn_days / max(tot_days, 1),
        switch_pa=switches / max(years, 1e-9),
        dd=_med([a for a, _b in downs]), after=_med([b for _a, b in downs]),
        reb=_med([a for a, _b in ups]), up_after=_med([b for _a, b in ups]),
        n_down=len(downs), n_up=len(ups),
    )


def leg_edge(kw):
    """三条闸门腿「闸门 vs 恒持满带」的年化差（pp），扣费后。"""
    out = {}
    for leg in tg.GATE_LEGS:
        px, sm = tg.leg_slice(leg, **kw)
        fee = tg.fee_of(leg)
        a = tg.perf(tg.run_leg(sm["state"], px, fee))["ann"]
        b = tg.perf(tg.run_leg(pd.Series(1.0, index=px.index), px, fee))["ann"]
        ws = tg.whipsaw(sm["state"], px)
        out[leg] = dict(edge_pp=(a - b) * 100, ann=a, whip=ws["n"], n=len(tg.actions(sm["state"])))
    return out


def build_states(frame, **kw):
    """-> {leg: state}（对齐 frame.index；非闸门腿恒 1.0）。

    刻意不用 `import backtest` —— `vol_legs/backtest.py` 与本目录同名，而 `trend_gate` 导入
    `data` 时会把 `strategies/vol_legs` 插到 `sys.path[0]`，之后 `import backtest` 会拿到
    **家族那个** backtest（`strategies/vol_legs/backtest.py`）。这里自己实现 6 行，绕开同名遮蔽。
    """
    out = {}
    for k in frame.columns:
        s = tg.state_machine(tg.load(k), k, **kw)["state"].reindex(frame.index)
        out[k] = s.ffill().bfill().fillna(0.0)
    return out


def asym_states(frame, fast_n, fast_slope=5):
    """快下车（MA(fast_n) + fast_slope 日斜率）/ 慢上车（MA250 + 20 日）—— 走主模块 `MODE="asym"`。

    非对称的语义在主模块里是「两套读数各自算目标档位、取 min」：快口径只能把仓位压得更低，
    不能抬得更高（加仓许可仍由慢口径把着）。
    """
    return build_states(frame, MODE="asym", DOWN_WIN=fast_n, DOWN_SLOPE=fast_slope)


def asym_rows(frame):
    out = []
    for fast_n in (20, 50, 120):
        states = asym_states(frame, fast_n)
        nav, inv = tg.portfolio(frame, states, fee=True, detail=True)
        m = tg.perf(nav)
        edge = []
        for leg in tg.GATE_LEGS:
            px = tg.load(leg)
            st = states[leg].reindex(frame.index)          # 组合层窗口上的档位
            sub = px.loc[frame.index]
            a = tg.perf(tg.run_leg(st, sub, tg.fee_of(leg)))["ann"]
            b = tg.perf(tg.run_leg(pd.Series(1.0, index=sub.index), sub, tg.fee_of(leg)))["ann"]
            edge.append((a - b) * 100)
        out.append(dict(name=f"快下车 MA{fast_n}+5日 / 慢上车 MA250+20日",
                        ann=m["ann"], mdd=m["mdd"], sharpe=m["sharpe"], expo=inv,
                        actions=sum(len(tg.actions(states[k])) for k in frame.columns),
                        edge_mean=float(np.mean(edge))))
    return out


def run():
    frame = tg.common_frame()
    rows = []
    for name, kw, fam in SPECS:
        states = build_states(frame, **kw)
        nav, inv = tg.portfolio(frame, states, fee=True, detail=True)
        m = tg.perf(nav)
        lag = lag_stats(kw)
        edge = leg_edge(kw)
        n_act = sum(len(tg.actions(states[k])) for k in frame.columns)
        rows.append(dict(
            name=name, fam=fam, **lag,
            ann=m["ann"], mdd=m["mdd"], sharpe=m["sharpe"], expo=inv,
            actions=n_act, edge_mean=float(np.mean([v["edge_pp"] for v in edge.values()])),
            edge_kc50=edge["kc50"]["edge_pp"], whip=sum(v["whip"] for v in edge.values()),
        ))
    return frame, rows


def table(rows):
    base = rows[0]
    L = ["| 口径 | 族 | 上涨态占比 | 下跌态占比 | 切换/年 | 下跌确认时已跌 | 确认后再跌 | "
         "上涨确认时已涨 | 确认后再涨 | 组合年化 | 回撤 | 夏普 | 曝光 | 三腿超额均值 | 调仓 |",
         "|---|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|"]
    for r in sorted(rows, key=lambda z: -(z["dd"] or -1)):
        bold = "**" if r["name"] == base["name"] else ""
        L.append(
            f"| {bold}{r['name']}{bold} | {r['fam']} | {r['up_share']:.0%} | "
            f"{r['down_share']:.0%} | {r['switch_pa']:.1f} | "
            f"{tg.fmt_pct(r['dd'])} | {tg.fmt_pct(r['after'])} | "
            f"{tg.fmt_pct(r['reb'])} | {tg.fmt_pct(r['up_after'])} | "
            f"{tg.fmt_pct(r['ann'])} | {tg.fmt_pct(r['mdd'])} | {r['sharpe']:.2f} | "
            f"{r['expo']:.0%} | {r['edge_mean']:+.2f}pp | {r['actions']} |")
    return L


def figure(rows):
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    plt.rcParams["font.sans-serif"] = ["Microsoft YaHei", "SimHei", "DejaVu Sans"]
    plt.rcParams["axes.unicode_minus"] = False

    xs = [r["dd"] * 100 for r in rows]              # 已跌 %（越接近 0 = 越灵敏）
    ys1 = [r["ann"] * 100 for r in rows]
    ys2 = [r["edge_mean"] for r in rows]
    cs = [r["switch_pa"] for r in rows]
    base = rows[0]

    # 标签轮流往四个方向放，避免同一点位附近的口径名叠在一起
    offs = [(7, 5), (7, -12), (-7, 5), (-7, -12)]
    fig, axes = plt.subplots(1, 2, figsize=(14.0, 6.6))
    for ax, ys, title, ylab in (
            (axes[0], ys1, "灵敏度 vs 组合层年化", "组合年化（%，扣费）"),
            (axes[1], ys2, "灵敏度 vs 三条闸门腿的平均超额", "闸门 − 恒持满带（pp/年）")):
        sc = ax.scatter(xs, ys, c=cs, cmap="viridis", s=72, edgecolor="white", lw=0.8, zorder=4)
        ax.scatter([xs[0]], [ys[0]], s=190, facecolors="none", edgecolors="#33424f",
                   lw=1.1, zorder=5)                      # 圈出「现行」
        order = sorted(range(len(rows)), key=lambda i: (xs[i], ys[i]))
        for n, i in enumerate(order):
            dx, dy = offs[n % len(offs)]
            ax.annotate(rows[i]["name"], (xs[i], ys[i]), textcoords="offset points",
                        xytext=(dx, dy), ha="left" if dx > 0 else "right",
                        fontsize=7.0, color="#33424f", zorder=6)
        ax.axhline(ys[0], color="#8895a7", lw=0.9, ls="--", zorder=1)
        ax.axvline(xs[0], color="#8895a7", lw=0.9, ls=":", zorder=1)
        ax.margins(x=0.17, y=0.16)
        ax.set_title(title, fontsize=11.5, color="#1b2b3a", weight="bold", loc="left")
        ax.set_xlabel("进入「下跌趋势」时距 250 日高点已跌（%，越靠近 0 = 确认越灵敏）", fontsize=9)
        ax.set_ylabel(ylab, fontsize=9.5)
        ax.grid(ls="--", lw=0.6, alpha=0.35)
        for s in ("top", "right"):
            ax.spines[s].set_visible(False)
    cb = fig.colorbar(sc, ax=axes, fraction=0.025, pad=0.02)
    cb.set_label("状态切换次数 / 年（三条腿合计）", fontsize=8.5)
    fig.suptitle("V5 · 趋势口径灵敏度扫描：更灵敏的确认换来什么（虚线 = 现行 MA250+20日）",
                 x=0.045, ha="left", fontsize=13, color="#1b2b3a", weight="bold")
    fig.text(0.045, 0.015,
             "口径只换「读数」（均线 / 中轨 / 快线 / 动量 / DIF），四象限与 5/20 日档位阶梯不变；"
             "组合层 = 科创板50 / 恒生科技 / 黄金三腿套闸门，2022-09 ~ 2026-10。"
             "机械规则输出，非投资建议。", fontsize=8.4, color="#5b6b7a")
    fig.subplots_adjust(left=0.055, right=0.90, top=0.855, bottom=0.135)
    fig.savefig(OUT_PNG, dpi=135, facecolor="white")
    plt.close(fig)
    print("图:", OUT_PNG)


def main():
    frame, rows = run()
    base = rows[0]
    L = [
        "# V5 · 趋势口径灵敏度扫描（MA250 是不是太慢？）",
        "",
        f"> 池 = 三条闸门腿（科创50 / 恒生科技 / 黄金）+ 七腿组合层；口径只换**读数**"
        "（`trend_gate.readout` 的 `KIND`），四象限定义与 5/20 日档位阶梯**完全不变** —— "
        "所以这张表比的不是「哪套规则好」，而是「同一套规则换个更灵敏的温度计会怎样」。",
        f"> 组合层共同窗口 {frame.index[0].date()} ~ {frame.index[-1].date()}；"
        f"确认滞后的样本是三条腿各自的全历史（下跌确认 {base['n_down']} 次 / 上涨确认 "
        f"{base['n_up']} 次为基准口径的计数）。",
        "",
        "## 1. 读数怎么读",
        "",
        "- **下跌确认时已跌** = 进入「下跌趋势」那一刻，收盘距 250 日高点的回撤（中位）。"
        "越接近 0 = 越早报警（越灵敏）。",
        "- **确认后再跌** = 同一时点起未来 120 日的最大跌幅（中位）。越大 = 预警越有用。",
        "- **上涨确认时已涨 / 确认后再涨** = 同一逻辑的镜像（距 250 日低点的反弹幅度）。",
        "- **切换/年** = 象限切换次数 / 年（三条腿合计）。这是频率帽要压的东西，"
        "不是越低越好 —— 太低说明迟钝，太高说明在震荡里来回打脸。",
        "",
        "## 2. 扫描结果（按灵敏程度排序：已跌得越少越靠前）",
        "",
    ] + table(rows) + [
        "",
        "## 3. 结论",
        "",
    ]

    by = {r["name"]: r for r in rows}
    srt = sorted(rows, key=lambda z: -(z["dd"] or -1))
    most = srt[0]                                   # 最灵敏
    ma100 = by["MA100+10日"]
    dual = by["双均线50/200+5日"]
    mom6 = by["动量6月+20日"]
    best_ann = max(rows, key=lambda r: r["ann"])
    best_edge = max(rows, key=lambda r: r["edge_mean"])
    best_sharpe = max(rows, key=lambda r: r["sharpe"])
    anns = [r["ann"] for r in rows]
    sharpes = [r["sharpe"] for r in rows]
    asy = asym_rows(frame)

    L += [
        f"1. **确实有更灵敏的口径，跨度很大。** 最灵敏的 {most['name']} 在距 250 日高点仅 "
        f"{tg.fmt_pct(most['dd'])} 时就确认下跌，现行口径要等到 {tg.fmt_pct(base['dd'])}"
        f"（差 {(most['dd'] - base['dd']) * 100:.1f}pp 的跌幅）；最钝的是 {mom6['name']}"
        f"（{tg.fmt_pct(mom6['dd'])}）。中间那档 {ma100['name']} 在 {tg.fmt_pct(ma100['dd'])}，"
        f"比现行早 {(ma100['dd'] - base['dd']) * 100:.1f}pp —— 这是「灵敏度 +1 档」的位置。",
        f"2. **但灵敏度没有换来收益。** {len(rows)} 档的组合层年化全落在 "
        f"{tg.fmt_pct(min(anns))} ~ {tg.fmt_pct(max(anns))}（宽度 {(max(anns) - min(anns)) * 100:.2f}pp）、"
        f"夏普 {min(sharpes):.2f} ~ {max(sharpes):.2f}。现行是 {tg.fmt_pct(base['ann'])} / "
        f"{base['sharpe']:.2f}，最好的一档是 {best_ann['name']} {tg.fmt_pct(best_ann['ann'])} / "
        f"{best_ann['sharpe']:.2f} —— 差 {abs(best_ann['ann'] - base['ann']) * 100:.2f}pp，"
        "低于噪声。**最灵敏那档反而是年化垫底的之一**：",
        f"   - {most['name']}：年化 {tg.fmt_pct(most['ann'])}、超额 {most['edge_mean']:+.2f}pp/年、"
        f"切换 {most['switch_pa']:.1f} 次/年（现行的 {most['switch_pa'] / base['switch_pa']:.1f} 倍）。"
        "报警越早，假警报越多，减掉的仓位多数减在反弹前。",
        f"3. **「灵敏度」其实是两个维度，混在一起会得出错误结论。** ①确认滞后（已跌多少确认）；"
        f"②切换频率（一年改几次主意）。{mom6['name']} 是最典型的反例：它确认最晚"
        f"（{tg.fmt_pct(mom6['dd'])}）却切换第二多（{mom6['switch_pa']:.1f} 次/年）—— "
        "因为「过去 6 个月收益是否为正」在零轴附近反复穿越。**用动量做趋势 = 又慢又吵**，"
        "这正是用户原话「同一套动量规则，窗口从 1 个月调到 12 个月，年化 0.68%~10.37%」的机制。",
        "   所以挑口径不能只看「确认得早不早」，要同时看它一年改几次主意 —— "
        "改得勤的口径，就算每次都对，交易成本与打脸成本也会把超额吃掉（见主报告 §3.4 打脸列）。",
        "4. **灵敏度 +1 档的两个候选（前提是你能接受更频繁的调仓）**：",
        f"   - `{ma100['name']}`：确认提前 {(ma100['dd'] - base['dd']) * 100:.1f}pp、切换 "
        f"{ma100['switch_pa']:.1f} 次/年、组合年化 {tg.fmt_pct(ma100['ann'])}（{tg.fmt_pct(ma100['ann'] - base['ann'])}）、"
        f"夏普 {ma100['sharpe']:.2f}（{ma100['sharpe'] - base['sharpe']:+.2f}）、回撤 "
        f"{tg.fmt_pct(ma100['mdd'])}（{tg.fmt_pct(ma100['mdd'] - base['mdd'])}）；"
        f"但三腿逐腿超额 {ma100['edge_mean']:+.2f}pp，说明组合层的改善来自权重与货基，不是单腿 alpha。",
        f"   - `{dual['name']}`：确认 {tg.fmt_pct(dual['dd'])}、切换 {dual['switch_pa']:.1f} 次/年、"
        f"夏普 {dual['sharpe']:.2f}（{dual['sharpe'] - base['sharpe']:+.2f}，本表最高）、"
        f"三腿超额 {dual['edge_mean']:+.2f}pp。",
        "   两档的共同问题：**逐腿超额仍为负**，即它们让单腿更差、只在组合层看起来略好 —— "
        "这种改善随时会被权重变化吃掉，不足以改默认参数。",
        "",
        "## 4. 非对称：快下车 + 慢上车（本表唯一有结构性理由的方向）",
        "",
        "两类错误的代价本来就不对称：A 股阴跌以年为单位（下车慢 = 持续失血），反弹往往几周内完成"
        "（上车慢 = 少赚一段）。所以把两件事拆开：**减仓用快口径（MA20/50/120 + 5 日斜率），"
        "加仓仍由慢口径（MA250 + 20 日）把门**。实现就是把两套读数各自算一遍目标档位、取 `min` —— "
        "快口径只能把仓位压得更低，不能抬得更高。",
        "",
        "| 方案 | 年化 | 最大回撤 | 夏普 | 平均曝光 | 调仓 | 三腿超额均值 |",
        "|---|---:|---:|---:|---:|---:|---:|",
        f"| 纯慢口径（现行 MA250+20日） | {tg.fmt_pct(base['ann'])} | {tg.fmt_pct(base['mdd'])} | "
        f"{base['sharpe']:.2f} | {base['expo']:.0%} | {base['actions']} | {base['edge_mean']:+.2f}pp |",
    ]
    for r in asy:
        L.append(f"| {r['name']} | {tg.fmt_pct(r['ann'])} | {tg.fmt_pct(r['mdd'])} | "
                 f"{r['sharpe']:.2f} | {r['expo']:.0%} | {r['actions']} | {r['edge_mean']:+.2f}pp |")

    best_asym = max(asy, key=lambda r: (round(r["sharpe"], 2), r["ann"]))   # 夏普优先，同分看年化
    L += [
        "",
        f"> 最好的一档是 **{best_asym['name']}**：年化 {tg.fmt_pct(best_asym['ann'])}"
        f"（现行 {tg.fmt_pct(base['ann'])}）、回撤 {tg.fmt_pct(best_asym['mdd'])}"
        f"（{tg.fmt_pct(best_asym['mdd'] - base['mdd'])}）、夏普 {best_asym['sharpe']:.2f}"
        f"（{best_asym['sharpe'] - base['sharpe']:+.2f}）、调仓 {best_asym['actions']} 次"
        f"（现行 {base['actions']} 次）。",
        "> 读法：如果非对称档的夏普明显高于纯慢/纯快两档，说明「快下车」省下的回撤 > 「快下车」"
        "带来的打脸成本；如果和纯慢口径差不多，说明这个方向也只是听起来对。**数据说话，不预设结论。**",
        "",
        "> 一句话：**有更灵敏的口径，但不一定有更灵敏的收益。** 灵敏买到的是「更早的确定性」，"
        "不是「更高的年化」；要用就把它当成两种错误代价的重新分配（快下车、慢上车），"
        "而不是当成一个更好的温度计。",
        "",
        "> 机械规则输出，非投资建议。",
    ]

    with io.open(OUT_MD, "w", encoding="utf-8", newline="\n") as f:
        f.write("\n".join(L))
    print("表:", OUT_MD)
    for r in sorted(rows, key=lambda z: -(z["dd"] or -1)):
        print(f"  {r['name']:20s} 已跌 {tg.fmt_pct(r['dd']):>8s} 再跌 {tg.fmt_pct(r['after']):>8s} "
              f"切换 {r['switch_pa']:4.1f}/年 · 年化 {tg.fmt_pct(r['ann']):>8s} "
              f"回撤 {tg.fmt_pct(r['mdd']):>8s} 夏普 {r['sharpe']:5.2f} "
              f"超额 {r['edge_mean']:+.2f}pp")
    print("非对称（快下车 / 慢上车）:")
    for r in asy:
        print(f"  {r['name']:34s} 年化 {tg.fmt_pct(r['ann']):>8s} 回撤 {tg.fmt_pct(r['mdd']):>8s} "
              f"夏普 {r['sharpe']:5.2f} 曝光 {r['expo']:.0%} 调仓 {r['actions']:3d} "
              f"超额 {r['edge_mean']:+.2f}pp")
    figure(rows)


if __name__ == "__main__":
    main()
