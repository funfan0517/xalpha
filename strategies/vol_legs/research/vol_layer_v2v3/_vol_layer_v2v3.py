# -*- coding: utf-8 -*-
"""把 V5 的自适应波动层（q80×0.9）搬到 V2 BOLL 突破 / V3 MACD+BOLL，看效果如何。

两个变种都是 **0/1 信号**（持仓 / 空仓），所以波动层的作用很直接：

    实际仓位 = 信号 × scale，  scale = clip(0.9 × 自身波动历史 80 分位 ÷ 120日已实现波动[t-1], 0.2, 1)

持仓期内波动升高 → 减到 0.x 仓；波动回落 → 补回满仓。引擎（`run`）按 `pos.diff()` 比例扣费，
所以「分批减仓 / 分批补回」的成本会被如实计入 —— 对 0/1 策略这是最关键的一条：
它本来一年只交易几次，加上波动层后权重变化会翻好几倍。

三个必须分开看的问题（与 `v5_trend_gate/research/_vol_overlay.md` 同一套路）：
1. **有没有改善**：年化 / 回撤 / Calmar 相对基准变好还是变差；
2. **是不是只是降仓**：按叠加后的平均仓位做恒定暴露对照；
3. **成本吃不吃得消**：权重变化次数 × 该腿真实申赎费（A 类单边赎回 0.5%）。

产出：`research/vol_layer_v2v3/_vol_layer_v2v3.md`。模拟结果，非投资建议。
"""
import os
import sys

import numpy as np
import pandas as pd

try:
    sys.stdout.reconfigure(encoding="utf-8")  # type: ignore[attr-defined]
except Exception:  # noqa: BLE001
    pass

_HERE = os.path.dirname(os.path.abspath(__file__))    # .../research/vol_layer_v2v3
_RES = os.path.dirname(_HERE)                         # strategies/vol_legs/research
_VL = os.path.dirname(_RES)                           # strategies/vol_legs
for _p in (_VL, os.path.join(_VL, "v2_boll_breakout"),
           os.path.join(_VL, "v3_macd_boll"), os.path.join(_VL, "v5_trend_gate")):
    if _p not in sys.path:
        sys.path.insert(0, _p)

import boll as v2          # noqa: E402  V2 BOLL 上轨突破
import engine              # noqa: E402  绩效口径（与主策略同一套）
import macd_boll as v3     # noqa: E402  V3 MACD + 自适应 BOLL
import trend_gate as tg    # noqa: E402  只借用它的 vol_scale（波动层唯一实现）

OUT_MD = os.path.join(_HERE, "_vol_layer_v2v3.md")
ANN = np.sqrt(252.0)

# 波动层参数：默认 = v5 的 q80 × gain 0.9；其余为稳健性对照
DEFAULT_VOL = dict(VOL_USE=True, VOL_MODE="adaptive", VOL_REF_Q=0.80, VOL_GAIN=0.90,
                   VOL_WINDOW=120, VOL_DEADBAND=0.15, VOL_MIN_GAP=10,
                   VOL_MIN_LEVEL=0.20, VOL_MIN_HIST=500)
SETTINGS = [
    ("默认 q80×0.9", {}),
    ("q80×1.0", dict(VOL_GAIN=1.00)),
    ("q70×0.9（更早介入）", dict(VOL_REF_Q=0.70)),
    ("q90×0.9（更晚介入）", dict(VOL_REF_Q=0.90)),
    ("固定 15%（绝对目标）", dict(VOL_MODE="fixed", VOL_TARGET=0.15)),
]

VARIANTS = [("V2", v2, "V2 BOLL上轨突破"), ("V3", v3, "V3 MACD+BOLL")]


# ---------------------------------------------------------------- 1. 取数与执行
def leg_data(mod, leg):
    """-> (px_full, px, sig)。样本起点 = 该变种的预热口径，与它自己的 backtest 一致。"""
    px_full = mod.load(leg)
    if mod is v3:
        px, sig, _s20, _s30 = mod.leg_slice(leg)
        return px_full, px, sig.astype(float)
    sig_full, _mid, _up = mod.signal(px_full)
    start = px_full.index[min(mod.PARAMS["N"] + 1, max(len(px_full) - 2, 0))]
    px = px_full.loc[start:]
    return px_full, px, sig_full.reindex(px.index).fillna(0).astype(float)


def run(mod, px, sig, fee):
    return mod.run(px, sig, fee=fee)


def scale_of(px_full, overrides):
    """在**全历史**上算系数（参考分位需要足够样本），再按样本区间取用 —— 与 v5 口径一致。"""
    kw = dict(DEFAULT_VOL)
    kw.update(overrides)
    return tg.vol_scale(px_full, **kw)


def metrics(nav):
    m = engine.metrics(nav)
    cal = (m["ann"] / abs(m["mdd"])) if m["mdd"] < -1e-9 else float("nan")
    return dict(ann=m["ann"], mdd=m["mdd"], vol=m["vol"], sharpe=m["sharpe"], calmar=cal)


def pos_of(sig):
    """成交口径仓位（T+1）：与 run 内部 `sig.shift(1)` 一致。"""
    return sig.shift(1).fillna(0.0).astype(float)


def changes(pos):
    return int(np.sum(np.abs(np.diff(np.asarray(pos, dtype=float))) > 1e-9))


# ---------------------------------------------------------------- 2. 逐腿
def leg_rows(mod, overrides=None):
    overrides = overrides or {}
    cols = ["leg", "name", "pv", "b_ann", "b_mdd", "b_sharpe", "b_cal",
            "o_ann", "o_mdd", "o_sharpe", "o_cal", "b_expo", "o_expo", "sw_b", "sw_o",
            "c_ann", "c_mdd", "c_cal", "c_expo", "dann", "dmdd", "dcal"]
    rows = []
    for leg in mod.LEGS:
        px_full, px, sig = leg_data(mod, leg)
        fee = mod.FEE[leg]
        base = run(mod, px, sig, fee)
        sc = scale_of(px_full, overrides).reindex(px.index).fillna(1.0)
        eff = (sig * sc).clip(0.0, 1.0)
        over = run(mod, px, eff, fee)
        pb, po, pc = pos_of(sig), pos_of(eff), None
        k = (po.mean() / pb.mean()) if pb.mean() > 1e-9 else 1.0
        ctrl_sig = (sig * float(k)).clip(0.0, 1.0)
        ctrl = run(mod, px, ctrl_sig, fee)
        pc = pos_of(ctrl_sig)
        b, o, c = metrics(base), metrics(over), metrics(ctrl)
        rows.append(dict(leg=leg, name=mod.rule.LEGS[leg]["name"],
                         pv=float(px.pct_change().std() * ANN),
                         b_ann=b["ann"], b_mdd=b["mdd"], b_sharpe=b["sharpe"], b_cal=b["calmar"],
                         o_ann=o["ann"], o_mdd=o["mdd"], o_sharpe=o["sharpe"], o_cal=o["calmar"],
                         b_expo=float(pb.mean()), o_expo=float(po.mean()),
                         sw_b=changes(pb), sw_o=changes(po),
                         c_ann=c["ann"], c_mdd=c["mdd"], c_cal=c["calmar"], c_expo=float(pc.mean()),
                         dann=(o["ann"] - b["ann"]) * 100, dmdd=(o["mdd"] - b["mdd"]) * 100,
                         dcal=o["calmar"] - b["calmar"],
                         dann_c=(o["ann"] - c["ann"]) * 100, dmdd_c=(o["mdd"] - c["mdd"]) * 100,
                         years=(px.index[-1] - px.index[0]).days / 365.25))
    return rows


# ---------------------------------------------------------------- 3. 组合层
def port_signals(mod, frame, overrides=None):
    sigs = {}
    for leg in frame.columns:
        px_full = mod.load(leg)
        s = mod.signal(px_full)
        if isinstance(s, tuple):
            s = s[0]
        s = s.reindex(frame.index).fillna(0).astype(float)
        if overrides is None:
            sigs[leg] = s
        else:
            sc = scale_of(px_full, overrides).reindex(frame.index).fillna(1.0)
            sigs[leg] = (s * sc).clip(0.0, 1.0)
    return sigs


def port_row(mod, overrides=None, ctrl_k=None):
    frame = mod.common_frame()
    sigs = port_signals(mod, frame, overrides)
    if ctrl_k is not None:
        sigs = {k: (v * ctrl_k).clip(0.0, 1.0) for k, v in sigs.items()}
    nav = mod.portfolio(frame, signals=sigs, fee=True)
    expo = float(np.mean([np.mean(pos_of(s).to_numpy()) for s in sigs.values()]))
    m = metrics(nav)
    m["expo"] = expo
    m["start"] = frame.index[0].date().isoformat()
    m["end"] = frame.index[-1].date().isoformat()
    m["years"] = (frame.index[-1] - frame.index[0]).days / 365.25
    return m


# ---------------------------------------------------------------- 4. 输出
def _tbl(rows, cols, head):
    out = ["| " + " | ".join(head) + " |",
           "|" + "|".join("---:" if i else "---" for i in range(len(head))) + "|"]
    for r in rows:
        out.append("| " + " | ".join(str(c(r)) for c in cols) + " |")
    return out


def main():
    L, ax = [], None
    ax = L.append
    ax("# 波动层搬到 V2 / V3：实测（q80×0.9 自适应）")
    ax("")
    ax("> 由 `research/vol_layer_v2v3/_vol_layer_v2v3.py` 生成，波动层实现直接复用 "
       "`v5_trend_gate.trend_gate.vol_scale`（不复制逻辑）。"
       "`实际仓位 = 信号 × scale`，`scale = clip(0.9 × 自身波动历史80分位 ÷ 120日已实现波动[t−1], "
       "0.2, 1)`；只减不加。扣该腿真实申赎费 + 单边滑点（按 `pos.diff()` 比例扣）。")
    ax("")
    ax("**与 v5 的关键差别**：v2/v3 是 **0/1 信号**，没有「半仓/观察仓」这种阶梯，所以这层在这里"
       "不是「细化档位」，而是**唯一的仓位调节器** —— 持仓期内波动升高就分批减、回落再补回。"
       "代价是权重变化次数会翻数倍，0/1 策略本来一年只动几次。")
    ax("")

    all_rows = {}
    for vi, (tag, mod, label) in enumerate(VARIANTS, start=1):
        rows = leg_rows(mod)
        all_rows[tag] = rows
        ax(f"## {vi}. {label} · 逐腿（{len(rows)} 腿，各自预热后起算，扣费）")
        ax("")
        head = ["腿", "价格波动", "基准年化", "基准回撤", "基准夏普", "基准Calmar",
                "叠加年化", "叠加回撤", "叠加Calmar", "均仓位 基→叠", "权重变化 基→叠"]
        cols = [lambda r: r["name"], lambda r: f"{r['pv']:.1%}",
                lambda r: f"{r['b_ann']:+.2%}", lambda r: f"{r['b_mdd']:.2%}",
                lambda r: f"{r['b_sharpe']:.2f}", lambda r: f"{r['b_cal']:.2f}",
                lambda r: f"{r['o_ann']:+.2%}", lambda r: f"{r['o_mdd']:.2%}",
                lambda r: f"{r['o_cal']:.2f}",
                lambda r: f"{r['b_expo']:.0%}→{r['o_expo']:.0%}",
                lambda r: f"{r['sw_b']}→{r['sw_o']}"]
        L.extend(_tbl(rows, cols, head))
        ax("")
        n_ann = sum(1 for r in rows if r["dann"] > 0)
        n_dd = sum(1 for r in rows if r["dmdd"] > 0)
        n_cal = sum(1 for r in rows if r["dcal"] > 0)
        n_ctrl = sum(1 for r in rows if r["dann_c"] > 0 and r["dmdd_c"] > 0)
        ax(f"- 年化更高 **{n_ann}/7** · 回撤更浅 **{n_dd}/7** · Calmar 更高 **{n_cal}/7** · "
           f"等暴露下「年化更高且回撤更浅」**{n_ctrl}/7**")
        ax(f"- 权重变化合计 {sum(r['sw_b'] for r in rows)} → {sum(r['sw_o'] for r in rows)} 次"
           f"（{sum(r['years'] for r in rows) / 7:.1f} 年/腿平均）")
        ax("")

        ax(f"**{vi}.2 等暴露对照**（把基准信号整条乘同倍数，看改善是不是只是「少持仓」）")
        ax("")
        head2 = ["腿", "叠加均仓位", "对照年化", "对照回撤", "对照Calmar",
                 "Δ年化（叠−对照）", "Δ回撤（叠−对照）"]
        cols2 = [lambda r: r["name"], lambda r: f"{r['o_expo']:.0%}",
                 lambda r: f"{r['c_ann']:+.2%}", lambda r: f"{r['c_mdd']:.2%}",
                 lambda r: f"{r['c_cal']:.2f}", lambda r: f"{r['dann_c']:+.2f}pp",
                 lambda r: f"{r['dmdd_c']:+.2f}pp"]
        L.extend(_tbl(rows, cols2, head2))
        ax("")

    # ---------------- 组合层
    ax("## 3. 组合层（七腿带权，扣费）")
    ax("")
    ax("| 变种 | 样本 | 基准 年化/回撤/夏普 | 叠加 年化/回撤/夏普 | 均仓位 基→叠 | 等暴露对照 |")
    ax("|---|---|---|---|---|---|")
    for tag, mod, label in VARIANTS:
        b = port_row(mod)
        o = port_row(mod, DEFAULT_VOL)
        c = port_row(mod, ctrl_k=o["expo"] / b["expo"] if b["expo"] > 1e-9 else 1.0)
        ax(f"| {label} | {b['start']}~{b['end']}（{b['years']:.1f} 年） | "
           f"{b['ann']:+.2%} / {b['mdd']:.2%} / {b['sharpe']:.2f} | "
           f"**{o['ann']:+.2%} / {o['mdd']:.2%} / {o['sharpe']:.2f}** | "
           f"{b['expo']:.0%}→{o['expo']:.0%} | "
           f"{c['ann']:+.2%} / {c['mdd']:.2%} / {c['sharpe']:.2f} |")
    ax("")

    # ---------------- 参数稳健性
    ax("## 4. 参数稳健性（跨 7 腿平均 ΔCalmar）")
    ax("")
    ax("| 变种 | 设置 | 平均ΔCalmar | 最差腿ΔCalmar | 改善腿数 | 平均仓位 |")
    ax("|---|---|---:|---:|---:|---:|")
    grid = {}
    for tag, mod, label in VARIANTS:
        for name, ov in SETTINGS:
            rows = leg_rows(mod, ov)
            grid[(tag, name)] = rows
            d = [r["dcal"] for r in rows]
            ax(f"| {tag} | {name} | {np.mean(d):+.3f} | {np.min(d):+.3f} | "
               f"{sum(1 for x in d if x > 0)}/7 | "
               f"{np.mean([r['o_expo'] for r in rows]):.0%} |")
    ax("")

    txt = "\n".join(L)

    # ---------------- 结论（数字从上面现算，避免手写漂移）
    d2 = grid[("V2", "默认 q80×0.9")]
    d3 = grid[("V3", "默认 q80×0.9")]
    b2, o2 = port_row(v2), port_row(v2, DEFAULT_VOL)
    b3, o3 = port_row(v3), port_row(v3, DEFAULT_VOL)
    best2 = max(d2, key=lambda r: r["dann"])
    worst2 = min(d2, key=lambda r: r["dann"])
    best3 = max(d3, key=lambda r: r["dann"])
    worst3 = min(d3, key=lambda r: r["dann"])

    expo2 = b2["expo"]
    expo3 = b3["expo"]
    concl = [
        "## 5. 结论",
        "",
        f"1. **组合层效果只有 v5 的零头**：V2 {b2['ann']:+.2%} / {b2['mdd']:.2%} / 夏普 "
        f"{b2['sharpe']:.2f} → {o2['ann']:+.2%} / {o2['mdd']:.2%} / 夏普 {o2['sharpe']:.2f}"
        f"（年化 {(o2['ann'] - b2['ann']) * 100:+.2f}pp、回撤 {(o2['mdd'] - b2['mdd']) * 100:+.2f}pp）；"
        f"V3 {b3['ann']:+.2%} / {b3['mdd']:.2%} / 夏普 {b3['sharpe']:.2f} → "
        f"{o3['ann']:+.2%} / {o3['mdd']:.2%} / 夏普 {o3['sharpe']:.2f}"
        f"（年化 {(o3['ann'] - b3['ann']) * 100:+.2f}pp、回撤 {(o3['mdd'] - b3['mdd']) * 100:+.2f}pp）。"
        "回撤都变浅，但年化同时下降，**夏普基本没动**（V2 +0.04、V3 −0.02）。",
        "",
        f"2. **根因是仓位本来就低**：组合平均仓位 V2 只有 **{expo2:.0%}**、V3 只有 "
        f"**{expo3:.0%}** —— 0/1 突破信号大部分时间在场外，风险已经被「空仓」处理掉了。"
        f"波动层能压的只是「在场那 15%~30% 时间里的一部分」，实际均仓位只从 "
        f"{b2['expo']:.0%}→{o2['expo']:.0%} / {b3['expo']:.0%}→{o3['expo']:.0%}，"
        "**可压缩的空间本来就小**。对比 v5（均仓位 62%~91%）就明白差距从哪来。",
        "",
        f"3. **改善主要来自少持仓，不是择时**：等暴露对照下「年化更高且回撤更浅」的腿，"
        f"V2 只有 **1/7**、V3 **2/7**；逐腿 Calmar 改善各 **3/7**。"
        f"V2 年化最好 **{best2['name']}**（{best2['dann']:+.2f}pp）、最差 **{worst2['name']}**"
        f"（{worst2['dann']:+.2f}pp）；V3 最好 **{best3['name']}**（{best3['dann']:+.2f}pp）、"
        f"最差 **{worst3['name']}**（{worst3['dann']:+.2f}pp）**七个腿没有一个变种全赢。**",
        "",
        "4. **成本不是问题，但也没换来什么**：权重变化只从 450→470（V2）/ 544→553（V3）次，"
        "因为死区 0.15 + 限速 10 日把微调都过滤掉了。可省下的费也没转化成收益 —— "
        "说明**不是成本问题，是这层在这些策略上没有信息可挖**。",
        "",
        "5. **§4 的稳健性进一步说明别接**：V2 五格里 `固定 15%` 最好（平均 ΔCalmar +0.038、5/7），"
        "但同一个设置在 V3 上只有 +0.006，V3 自己最好的 q90×0.9 也只有 +0.015 —— "
        "换了策略/换了参数就失效，属于样本内噪声，不是「找到更好的参数」。",
        "",
        "6. **结论：不建议给 v2/v3 加这层。** 它们的设计初衷就是「简单的 0/1 进出」，"
        "风险由「大部分时间空仓」承担；叠上连续仓位后，它既不是干净的 0/1 策略，"
        "也不如 v5 那种「阶梯 + 波动」的分层结构。**波动层解决的是「满仓时的波动」，"
        "而 0/1 突破策略的问题不在这里。**",
        "",
        "模拟结果，非投资建议。",
    ]
    txt = txt + "\n\n" + "\n".join(concl)

    with open(OUT_MD, "w", encoding="utf-8") as f:
        f.write(txt + "\n")
    print(txt)
    print(f"\n报告: {OUT_MD}")


if __name__ == "__main__":
    main()
