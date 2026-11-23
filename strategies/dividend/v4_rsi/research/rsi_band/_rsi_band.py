#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""V4 · 滞回带 50/70 的参数优化 + 「五档」仓位阶梯 对照研究

背景
----
上一轮 `research/rsi_gate/_rsi_gate.md` 的结论：滞回带（RSI < t_in 买、≥ t_out 才卖、
中间维持）在 4 组参数里 **50/70 最好**（Calmar 1.12、换手 287%/年、均持 7.1 只），
但仍不如 V4 现行（1.40）。用户问两件事：

1. **50/70 这个配置还能怎么优化？**
2. **「五档」是不是更好？**

「五档」在本报告里的定义：把 RSI 分五段映射到五档仓位系数
（如 RSI<40 满档 / 40–50 七五折 / 50–60 半档 / 60–70 二五折 / ≥70 零），
即把「持有 or 不持有」的 0/1 换成 0~1 的梯度。
（仓库里另两处「五档」是 `growth_div_rotation/v4_graded_bands` 的 R 阶梯和
`vol_legs` 的波动分位五档，都与 RSI 无关，本报告不复用。）

三件事
------
A. **滞回带网格**：t_in ∈ {40,45,50,55,60} × t_out ∈ {55,60,65,70,75,80}，
   看 50/70 落在平台区中央还是尖峰上（尖峰 = 拟合，平台 = 结构）。
B. **权重阶梯**：三档 / 四档 / 五档 / 线性，各两种总仓位口径 ——
   `norm=False`（系数 ÷ 标的数，**阶梯同时决定总仓位**）与
   `norm=True`（组内归一化，**阶梯只决定相对权重**，总仓位恒等于滞回的 100% 口径）。
C. **滚动参数验证**：每期只用过去 36 个月挑 Calmar 最优 (t_in,t_out)，
   用接下来 12 个月检验 —— 上一轮报告承诺的那一步，回答「参数选择本身稳不稳」。

口径（与 `backtest.py` / `_rsi_gate.py` 完全一致）
-------------------------------------------------
* 池 = `rule.CODES`（全 10 只）；收益 = 基金累计净值月度；月末信号次月生效
* 成本单边 0.03%，按 Σ|Δw|；空仓不计息
* 每个方案都给「**同暴露等权基准**」对照 —— 只看 Calmar 会被仓位高低骗

产物（AGENTS §8.4 ④）
--------------------
    v4_rsi/research/rsi_band/_rsi_band.{py,md,png}

用法::

    python strategies/dividend/v4_rsi/research/rsi_band/_rsi_band.py
"""
from __future__ import annotations

import os
import sys

import matplotlib

matplotlib.use("Agg")

import matplotlib.pyplot as plt  # noqa: E402
import numpy as np  # noqa: E402
import pandas as pd  # noqa: E402

_HERE = os.path.dirname(os.path.abspath(__file__))     # .../v4_rsi/research/rsi_band
_RES = os.path.dirname(_HERE)                          # .../v4_rsi/research
_V4 = os.path.dirname(_RES)                            # .../v4_rsi
_STRAT = os.path.dirname(_V4)                          # .../dividend
_GATE = os.path.join(_RES, "rsi_gate")                 # 复用门槛研究的口径与工具
for _p in (_HERE, _GATE, _RES, _V4, _STRAT):
    if _p not in sys.path:
        sys.path.insert(0, _p)

import _single_signal as S  # noqa: E402  pylint: disable=wrong-import-position
import pool  # noqa: E402  pylint: disable=wrong-import-position
import rule as V4  # noqa: E402  pylint: disable=wrong-import-position
import _rsi_gate as G  # noqa: E402  pylint: disable=wrong-import-position

OUT_MD = os.path.join(_HERE, "_rsi_band.md")
OUT_PNG = os.path.join(_HERE, "_rsi_band.png")

OOS_START = G.OOS_START
T_IN = (40, 45, 50, 55, 60)
T_OUT = (55, 60, 65, 70, 75, 80)
GRID = tuple((a, b) for a in T_IN for b in T_OUT if b - a >= 10)

# 权重阶梯：(cut 边界, 各档系数)。档数 = len(levels) = len(cuts)+1
BANDS = {
    "三档 50/70（V4 时间维度口径）": ([50, 70], [1.0, 0.5, 0.0]),
    "三档 45/65": ([45, 65], [1.0, 0.5, 0.0]),
    "四档 45/60/70": ([45, 60, 70], [1.0, 0.67, 0.33, 0.0]),
    "五档 40/50/60/70": ([40, 50, 60, 70], [1.0, 0.75, 0.5, 0.25, 0.0]),
    "五档 45/55/65/75": ([45, 55, 65, 75], [1.0, 0.75, 0.5, 0.25, 0.0]),
    "五档 35/45/55/65": ([35, 45, 55, 65], [1.0, 0.8, 0.6, 0.35, 0.0]),
}

plt.rcParams["font.sans-serif"] = ["Microsoft YaHei", "SimHei", "DejaVu Sans"]
plt.rcParams["axes.unicode_minus"] = False

BLUE, RED, AMBER, GREEN, PURPLE, GRAY = "#1f4e79", "#c0504d", "#e8a33d", "#137333", "#6a4c93", "#8a8f98"

ROLL_TRAIN, ROLL_TEST = 36, 12      # 滚动验证：36 个月训练 → 12 个月检验


# ------------------------------------------------------------------ 权重构造
def level_w(rsi: pd.DataFrame, rets: pd.DataFrame, cuts: list[float], levels: list[float],
            norm: bool = False) -> pd.DataFrame:
    """RSI 分段 → 档位系数。

    norm=False : 系数 ÷ 标的数（阶梯同时决定总仓位，满档全池 == 100%）
    norm=True  : 组内归一化（阶梯只决定相对权重，总仓位与滞回等权同为 100%）
    """
    ok = G.avail(rsi, rets)
    bins = [-np.inf] + list(cuts) + [np.inf]
    lv = np.asarray(levels, dtype=float)
    out = pd.DataFrame(np.nan, index=rsi.index, columns=rsi.columns)
    for c in rsi.columns:
        idx = pd.cut(rsi[c], bins=bins, labels=False, right=False)
        v = pd.Series(np.nan, index=rsi.index)
        m = idx.notna()
        if m.any():
            v[m] = lv[idx[m].astype(int).to_numpy()]
        out[c] = v
    w = out.where(ok)
    if norm:
        tot = w.sum(axis=1)
        return w.div(tot.where(tot > 0), axis=0).fillna(0.0)
    return w.fillna(0.0) / len(rsi.columns)


def linear_w(rsi: pd.DataFrame, rets: pd.DataFrame, lo: float = 40.0, hi: float = 75.0,
             norm: bool = False) -> pd.DataFrame:
    """连续线性权重：RSI ≤ lo → 1、≥ hi → 0、中间线性。"""
    ok = G.avail(rsi, rets)
    w = ((hi - rsi) / (hi - lo)).clip(0.0, 1.0).where(ok).fillna(0.0)
    if norm:
        tot = w.sum(axis=1)
        return w.div(tot.where(tot > 0), axis=0).fillna(0.0)
    return w / len(rsi.columns)


# ------------------------------------------------------------------ 滚动验证
def rolling_hyst(rets: pd.DataFrame, rsi: pd.DataFrame,
                 grid=GRID, train=ROLL_TRAIN, test=ROLL_TEST) -> tuple[pd.Series, list[dict]]:
    """每期用过去 train 个月挑 Calmar 最优 (t_in,t_out)，在接下来 test 个月检验。

    检验段的权重必须带上前一个月的状态（`simulate` 内部 `shift(1)`），否则每段首月
    会被当成「从空仓开始」，系统性低估。
    """
    idx = rets.index
    pieces, picks = [], []
    t = train
    while t < len(idx):
        tr_end = idx[t - 1]
        te = idx[t:t + test]
        if len(te) == 0:
            break
        best = None
        for a, b in grid:
            w = G.hyst_w(rsi.loc[:tr_end], rets.loc[:tr_end], a, b)
            c = G.profile(w, rets.loc[:tr_end])["calmar"]
            if best is None or c > best[0]:
                best = (c, a, b)
        _, a, b = best
        w_full = G.hyst_w(rsi.loc[:te[-1]], rets.loc[:te[-1]], a, b)
        pos = w_full.index.get_indexer([te[0]])[0]
        w_win = w_full.iloc[max(pos - 1, 0):]
        r_win = S.simulate(w_win, rets.loc[w_win.index[0]:te[-1]])
        r_te = r_win.loc[te[0]:].dropna()
        pieces.append(r_te)
        picks.append({"start": te[0], "end": te[-1], "a": a, "b": b,
                      "train_calmar": best[0], "test_calmar": S.stats(r_te)["calmar"]})
        t += test
    return pd.concat(pieces).sort_index(), picks


# ------------------------------------------------------------------ 图
def plot(sims: dict, grid_cal: dict, best_key: tuple, path: str) -> str:
    fig = plt.figure(figsize=(13.2, 4.6), dpi=120)
    gs = fig.add_gridspec(1, 2, width_ratios=[1, 1.15])

    ax1 = fig.add_subplot(gs[0, 0])
    show = [("基准：10 只等权", GRAY, 1.3), ("V4 现行：top2 + 板块择时", BLUE, 1.6),
            ("滞回 50/70", RED, 1.5), ("五档 40/50/60/70（norm）", GREEN, 1.5),
            ("五档 40/50/60/70（不 norm）", AMBER, 1.4)]
    for name, color, lw in show:
        if name in sims:
            nav = sims[name]["nav"]
            ax1.plot(nav.index, nav.to_numpy(), color=color, lw=lw, label=name)
    ax1.set_yscale("log")
    ax1.set_title("净值（对数轴，含成本）", fontsize=11)
    ax1.grid(True, ls=":", color="#ddd")
    ax1.legend(fontsize=7.5, loc="upper left")
    ax1.tick_params(labelsize=8)

    ax2 = fig.add_subplot(gs[0, 1])
    mat = np.array([[grid_cal.get((a, b), np.nan) for b in T_OUT] for a in T_IN])
    im = ax2.imshow(mat, cmap="RdYlGn", aspect="auto", vmin=np.nanmin(mat), vmax=np.nanmax(mat))
    ax2.set_xticks(range(len(T_OUT)), [str(b) for b in T_OUT], fontsize=8)
    ax2.set_yticks(range(len(T_IN)), [str(a) for a in T_IN], fontsize=8)
    ax2.set_xlabel("卖出线 t_out（RSI ≥）", fontsize=9)
    ax2.set_ylabel("买入线 t_in（RSI <）", fontsize=9)
    for i, a in enumerate(T_IN):
        for j, b in enumerate(T_OUT):
            v = mat[i, j]
            if np.isnan(v):
                ax2.text(j, i, "—", ha="center", va="center", fontsize=8, color=GRAY)
                continue
            hot = (a, b) == best_key
            ax2.text(j, i, f"{v:.2f}", ha="center", va="center", fontsize=7.6,
                     fontweight="bold" if hot else "normal",
                     color="#111" if not hot else "#000")
            if hot:
                ax2.add_patch(plt.Rectangle((j - .5, i - .5), 1, 1, fill=False, ec="#111", lw=2))
    ax2.set_title(f"滞回带网格 Calmar（黑框 = 最优 {best_key[0]}/{best_key[1]}）", fontsize=11)
    fig.colorbar(im, ax=ax2, fraction=0.03, pad=0.02)

    fig.tight_layout(pad=0.5)
    fig.savefig(path, bbox_inches="tight")
    plt.close(fig)
    return path


# ------------------------------------------------------------------ 报告
def main() -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")

    rets, rsi = G.load()
    base_r = S.simulate(S.equal_w(G.avail(rsi, rets)), rets)
    eq = S.equal_w(G.avail(rsi, rets))
    mult = G.board_mult(rsi, rets)
    top = S.top_w(rsi, G.avail(rsi, rets), V4.TOP_K)

    # —— 方案集 ——
    plans: dict[str, pd.DataFrame] = {
        "基准：10 只等权": eq,
        "V4 现行：top2 + 板块择时": top.mul(mult, axis=0),
    }
    for a, b in GRID:
        plans[f"滞回 {a}/{b}"] = G.hyst_w(rsi, rets, a, b)
    for name, (cuts, levels) in BANDS.items():
        for norm in (False, True):
            tag = "norm" if norm else "不 norm"
            plans[f"{name}（{tag}）"] = level_w(rsi, rets, cuts, levels, norm=norm)
    for norm in (False, True):
        tag = "norm" if norm else "不 norm"
        plans[f"线性 40→75（{tag}）"] = linear_w(rsi, rets, 40, 75, norm=norm)

    prof = {k: G.profile(w, rets) for k, w in plans.items()}
    prof["基准：10 只等权"] = G.profile(eq, rets)

    grid_cal = {(a, b): prof[f"滞回 {a}/{b}"]["calmar"] for a, b in GRID}
    best_key = max(grid_cal, key=grid_cal.get)

    # —— 同暴露基准（每个方案一个）——
    ctrl = {}
    for k, p in prof.items():
        ctrl[k] = G.profile(eq * p["expo"], rets)["calmar"]

    # —— 滚动验证 ——
    r_roll, picks = rolling_hyst(rets, rsi)
    roll_span = (r_roll.index[0], r_roll.index[-1])
    roll_st = S.stats(r_roll.dropna())

    def on_roll(st: dict) -> dict:
        return G.seg(st, str(roll_span[0].date()))

    plot(sims=prof, grid_cal=grid_cal, best_key=best_key, path=OUT_PNG)

    # —— 主表 ——
    best_name = f"滞回 {best_key[0]}/{best_key[1]}"
    main_names = [
        "基准：10 只等权",
        "V4 现行：top2 + 板块择时",
        "滞回 50/70",
    ]
    if best_name != "滞回 50/70":
        main_names.append(best_name)
    for name in BANDS:
        main_names += [f"{name}（不 norm）", f"{name}（norm）"]
    main_names += ["线性 40→75（不 norm）", "线性 40→75（norm）"]

    L = [
        "# V4 · 滞回带 50/70 的参数优化 + 「五档」仓位阶梯",
        "",
        f"> 样本 {base_r.index[0]:%Y-%m} ~ {base_r.index[-1]:%Y-%m}（{len(base_r)} 月，末月不完整）· "
        f"检验段 {OOS_START[:4]} 起 · 全池 10 只 · 月末信号次月生效 · 单边 0.03% · 空仓不计息。",
        "> 「五档」= RSI 分五段映射到五档仓位系数；`norm` 指组内归一化（只调相对权重），"
        "`不 norm` 指系数 ÷ 10（阶梯同时决定总仓位）。两种口径都与滞回带可比。",
        "> 机械规则输出，非投资建议。",
        "",
        "## 一、主结果（含同暴露对照）",
        "",
        "| 方案 | 年化 | 回撤 | Calmar | 夏普 | 平均仓位 | 均持只数 | 空仓月 | 年化换手 |"
        " 同暴露基准 | 增量 |",
        "|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|",
    ]
    for name in main_names:
        p = prof[name]
        L.append(f"| {name} | {G.fmt(p['cagr'])} | {G.fmt(p['dd'])} | {p['calmar']:.2f} | "
                 f"{p['sharpe']:.2f} | {p['expo']:.0%} | {p['n_hold']:.1f} | {p['flat']:.0%} | "
                 f"{p['turnover'] * 100:.0f}% | {ctrl[name]:.2f} | "
                 f"{p['calmar'] - ctrl[name]:+.2f} |")

    # —— 网格 ——
    L += ["", "## 二、滞回带网格（Calmar；单元格 = t_in 行 × t_out 列）", "",
          "| t_in \\ t_out | " + " | ".join(str(b) for b in T_OUT) + " |",
          "|---|" + "---:|" * len(T_OUT)]
    for a in T_IN:
        cells = []
        for b in T_OUT:
            v = grid_cal.get((a, b))
            cells.append("—" if v is None else
                         (f"**{v:.2f}**" if (a, b) == best_key else f"{v:.2f}"))
        L.append(f"| {a} | " + " | ".join(cells) + " |")

    p50 = prof["滞回 50/70"]
    pbest = prof[f"滞回 {best_key[0]}/{best_key[1]}"]
    near = [(a, b) for a, b in GRID if abs(a - 50) <= 5 and abs(b - 70) <= 5]
    near_cal = [grid_cal[k] for k in near]
    L += ["",
          f"- 50/70 的 Calmar {p50['calmar']:.2f}；全域最优是 **{best_key[0]}/{best_key[1]} "
          f"（{pbest['calmar']:.2f}）**，差距 {pbest['calmar'] - p50['calmar']:+.2f}。",
          f"- 50/70 的 3×3 邻域（t_in 45~55、t_out 65~75，共 {len(near)} 格）Calmar 区间 "
          f"{min(near_cal):.2f}~{max(near_cal):.2f}、中位 {np.median(near_cal):.2f} —— "
          + ("**邻域平整**，50/70 不是尖峰，换成邻近参数结论不变。"
             if max(near_cal) - min(near_cal) < 0.20 else
             "**邻域起伏较大**，参数敏感，50/70 的最优可能是局部噪声。"),
          ""]

    # —— 滚动验证 ——
    L += ["## 三、滚动参数验证（每期只用过去 36 个月挑参 → 下 12 个月检验）", "",
          f"检验段 {roll_span[0]:%Y-%m} ~ {roll_span[1]:%Y-%m}（{len(r_roll.dropna())} 月，"
          f"共 {len(picks)} 期）。每期在 `t_in × t_out` 网格上挑**训练段 Calmar 最高**的一组，"
          "然后原样用到下一段 —— 参数选择本身不进样本外。", "",
          "| 方案 | 年化 | 回撤 | Calmar | 说明 |",
          "|---|---:|---:|---:|---|"]
    L.append(f"| 滚动选参滞回（真样本外） | {G.fmt(roll_st['cagr'])} | {G.fmt(roll_st['dd'])} | "
             f"**{roll_st['calmar']:.2f}** | 每期参数不同，见下表 |")
    roll_rows = [("滞回 50/70", "固定参数，同区间")]
    if best_name != "滞回 50/70":
        roll_rows.append((best_name, "全样本挑的最优，含拟合"))
    roll_rows += [("V4 现行：top2 + 板块择时", "现行口径"), ("基准：10 只等权", "满仓")]
    for name, note in roll_rows:
        s = on_roll(prof[name])
        L.append(f"| {name} | {G.fmt(s['cagr'])} | {G.fmt(s['dd'])} | {s['calmar']:.2f} | {note} |")

    L += ["", "| 期 | 检验区间 | 选中 t_in | 选中 t_out | 训练 Calmar | **检验 Calmar** |",
          "|---|---|---:|---:|---:|---:|"]
    for p in picks:
        L.append(f"| {p['start']:%Y-%m} | {p['start']:%Y-%m}~{p['end']:%Y-%m} | {p['a']} | {p['b']} | "
                 f"{p['train_calmar']:.2f} | **{p['test_calmar']:.2f}** |")

    n_pick = len(picks)
    n_5070 = sum(1 for p in picks if p["a"] == 50 and p["b"] == 70)
    uniq = {(p["a"], p["b"]) for p in picks}
    spread = max(p["b"] - p["a"] for p in picks) - min(p["b"] - p["a"] for p in picks)
    L += ["",
          f"- {n_pick} 期里选中 50/70 的有 **{n_5070} 期**，共出现 {len(uniq)} 组不同参数"
          f"（带宽 b−a 的极差 {spread}）。"
          + ("参数**高度稳定**，说明 50/70 附近是结构性的平台。"
             if n_5070 >= n_pick / 2 else
             "参数**漂移明显**，说明全样本最优参数不能直接搬用。"),
          f"- 滚动选参（真样本外）Calmar {roll_st['calmar']:.2f} vs 固定 50/70 同期 "
          f"{on_roll(p50)['calmar']:.2f} vs 样本内最优同期 "
          f"{on_roll(pbest)['calmar']:.2f} —— 差额就是「参数拟合」能带来的虚假收益。",
          ""]

    # —— 五档专项 ——
    L += ["## 四、「五档」到底是不是更好", "",
          "| 档位方案 | 口径 | 年化 | 回撤 | Calmar | 平均仓位 | 年化换手 | 同暴露增量 |",
          "|---|---|---:|---:|---:|---:|---:|---:|"]
    for name in BANDS:
        for norm in (False, True):
            tag = "norm" if norm else "不 norm"
            k = f"{name}（{tag}）"
            p = prof[k]
            L.append(f"| {name} | {tag} | {G.fmt(p['cagr'])} | {G.fmt(p['dd'])} | {p['calmar']:.2f} | "
                     f"{p['expo']:.0%} | {p['turnover'] * 100:.0f}% | {p['calmar'] - ctrl[k]:+.2f} |")
    for norm in (False, True):
        tag = "norm" if norm else "不 norm"
        k = f"线性 40→75（{tag}）"
        p = prof[k]
        L.append(f"| 线性 40→75 | {tag} | {G.fmt(p['cagr'])} | {G.fmt(p['dd'])} | {p['calmar']:.2f} | "
                 f"{p['expo']:.0%} | {p['turnover'] * 100:.0f}% | {p['calmar'] - ctrl[k]:+.2f} |")
    L += [""]

    # —— 检验段 ——
    L += ["## 五、检验段（2025 起）", "",
          "| 方案 | 年化 | 回撤 | Calmar |", "|---|---:|---:|---:|"]
    for name in ("基准：10 只等权", "V4 现行：top2 + 板块择时", "滞回 50/70",
                 "五档 40/50/60/70（不 norm）", "五档 40/50/60/70（norm）",
                 "五档 45/55/65/75（norm）", "线性 40→75（norm）"):
        s = G.seg(prof[name], OOS_START)
        L.append(f"| {name} | {G.fmt(s['cagr'])} | {G.fmt(s['dd'])} | {s['calmar']:.2f} |")
    L += [""]

    # —— 结论 ——
    b5 = prof["五档 40/50/60/70（不 norm）"]
    b5n = prof["五档 40/50/60/70（norm）"]
    v4 = prof["V4 现行：top2 + 板块择时"]
    lin = prof["线性 40→75（norm）"]
    band_names = [f"{n}（{'norm' if m else '不 norm'}）" for n in BANDS for m in (False, True)]
    band_best = max(band_names, key=lambda k: prof[k]["calmar"])
    bb = prof[band_best]
    worst_pick = min(picks, key=lambda p: p["test_calmar"])
    best_pick = max(picks, key=lambda p: p["test_calmar"])
    L += [
        "## 六、结论",
        "",
        f"1. **50/70 是平台不是尖峰。** 全域最优 {best_key[0]}/{best_key[1]}（{pbest['calmar']:.2f}）"
        f"比 50/70（{p50['calmar']:.2f}）只高 {pbest['calmar'] - p50['calmar']:+.2f}，"
        f"3×3 邻域内部极差 {max(near_cal) - min(near_cal):.2f}。"
        "换句话说：把 50/70 微调成 55/70 或 45/70 不会改变结论，**这一段没有必要再调参**，"
        "要改就得改结构（下面第 2 条）。",
        "",
        f"2. **五档没有更好 —— 同仓位口径下与二元滞回打平，降仓位口径下更差。** "
        f"五档 40/50/60/70 `norm`（仓位 {b5n['expo']:.0%}，与滞回同仓）：Calmar "
        f"{b5n['calmar']:.2f}、换手 {b5n['turnover'] * 100:.0f}%，"
        f"对滞回 50/70（{p50['calmar']:.2f} / {p50['turnover'] * 100:.0f}%）**没有改善**；"
        f"五档 `不 norm`（仓位 {b5['expo']:.0%}）：Calmar {b5['calmar']:.2f}、"
        f"同暴露增量 {b5['calmar'] - ctrl['五档 40/50/60/70（不 norm）']:+.2f}，"
        f"低于滞回的 {p50['calmar'] - ctrl['滞回 50/70']:+.2f}。"
        f"所有档位方案里最好的是 **{band_best}：{bb['calmar']:.2f}**，"
        f"仍低于 {best_name}（{pbest['calmar']:.2f}），更低于 V4 现行"
        f"（{v4['calmar']:.2f} / 增量 {v4['calmar'] - ctrl['V4 现行：top2 + 板块择时']:+.2f}）。"
        "原因和上一轮一致：**红利的 RSI 与未来收益不是单调梯度关系**（50 附近恰好是坑），"
        "把 40~70 切细只是把同一段噪声切成更细的噪声。",
        "",
        f"3. **`norm` 还是 `不 norm` 决定的是「阶梯要不要兼任仓位管理」。** 归一化版"
        f"（{b5n['calmar']:.2f}、仓位 {b5n['expo']:.0%}）只把档位当相对权重；"
        f"不归一化版（{b5['calmar']:.2f}、仓位 {b5['expo']:.0%}）让阶梯同时压总仓位，"
        f"回撤从 {G.fmt(b5n['dd'])} 收到 {G.fmt(b5['dd'])}，但年化从 {G.fmt(b5n['cagr'])} "
        f"掉到 {G.fmt(b5['cagr'])} —— 又是「用低仓位换低回撤」的老问题（与上一轮门槛研究同一个病）。"
        f"注意最好的一档（{band_best}）换手 {bb['turnover'] * 100:.0f}%，"
        f"是滞回的 {bb['turnover'] / p50['turnover']:.1f} 倍 —— 多出来的精度不够付手续费。",
        "",
        f"4. **线性权重没有额外好处。** 线性 40→75（norm）Calmar {lin['calmar']:.2f}、"
        f"换手 {lin['turnover'] * 100:.0f}%，与五档"
        f"（{b5n['calmar']:.2f} / {b5n['turnover'] * 100:.0f}%）基本同档。"
        "把 5 档换成连续梯度并没有提升，说明**瓶颈不在离散化的粒度**，"
        "而在「用 RSI 决定权重」这件事本身的信息量。",
        "",
        f"5. **滚动验证推翻了「优化参数」这条路。** {n_pick} 期里只有 {n_5070} 期选中 50/70，"
        f"共出现 {len(uniq)} 组不同参数"
        f"（{n_pick} 期里 {sum(1 for p in picks if p['b'] - p['a'] <= 15)} 期选的是 ≤15 点的窄带）；"
        f"滚动选参的真样本外 Calmar **{roll_st['calmar']:.2f}**，"
        f"而固定 50/70 在同一区间是 {on_roll(p50)['calmar']:.2f}、"
        f"样本内最优参数是 {on_roll(pbest)['calmar']:.2f}。"
        f"最差一期 {worst_pick['start']:%Y-%m}~{worst_pick['end']:%Y-%m}"
        f"（选中 {worst_pick['a']}/{worst_pick['b']}，训练 {worst_pick['train_calmar']:.2f}"
        f" → 检验 {worst_pick['test_calmar']:.2f}），最好一期 "
        f"{best_pick['start']:%Y-%m}~{best_pick['end']:%Y-%m}（{best_pick['a']}/{best_pick['b']}，"
        f"训练 {best_pick['train_calmar']:.2f} → 检验 {best_pick['test_calmar']:.2f}）—— "
        "训练到检验的映射**没有规律**，这才是参数优化的死穴，而不是「某一组参数不好」。"
        "**固定参数反而更稳**；但只有 "
        f"{n_pick} 期 / {len(r_roll.dropna())} 个月，样本太小，"
        "只能说「没有证据支持调参」，不能说「调参一定有害」。",
        "",
        "### 处置建议",
        "",
        "- 按用户硬规则（新方案未稳定不进每日报告），本课题**只留研究产物**：不改 `rule.py`、"
        "不进 `daily_digest` / `intraday_preview`。",
        f"- **50/70 不需要继续调参**（邻域平整、滚动不稳），要动就动结构；"
        f"而结构上目前所有变体都输给现行 V4（{v4['calmar']:.2f}）—— 维持现状。",
        "- **五档这条路可以停了**：同仓位口径与滞回打平、降仓位口径更差、换手更高，"
        "而滞回本身又输给 V4。再细分档位只是把同一段噪声切得更碎。",
        "- 若还想在「RSI 决定权重」上找增量，唯一没测过的方向是**把 RSI 换成另一个维度**"
        "（例如「距离 250 日高点」或「波动率分位」）—— 本报告与上一轮都指向同一个结论："
        "**RSI 的信息量集中在「极端超买」这一端，中间梯度几乎没有可用信号**。",
        "",
        f"> 复现：`python strategies/dividend/v4_rsi/research/rsi_band/_rsi_band.py` · "
        f"图：`{os.path.basename(OUT_PNG)}`",
    ]

    with open(OUT_MD, "w", encoding="utf-8") as fh:
        fh.write("\n".join(L) + "\n")

    print(f"[band] 样本 {base_r.index[0]:%Y-%m}~{base_r.index[-1]:%Y-%m}（{len(base_r)} 月）"
          f" · 网格 {len(GRID)} 组 · 滚动 {len(picks)} 期")
    for name in main_names:
        p = prof[name]
        print(f"[band] {name:34s} 年化 {p['cagr'] * 100:5.1f}%  回撤 {p['dd'] * 100:6.1f}%  "
              f"Calmar {p['calmar']:5.2f}  仓位 {p['expo'] * 100:3.0f}%  "
              f"换手 {p['turnover'] * 100:5.0f}%  增量 {p['calmar'] - ctrl[name]:+.2f}")
    print(f"[band] 网格最优 {best_key} Calmar {pbest['calmar']:.2f} · "
          f"滚动样本外 Calmar {roll_st['calmar']:.2f}（固定 50/70 同期 "
          f"{on_roll(p50)['calmar']:.2f}）")
    print(f"[band] 产物 -> {os.path.relpath(OUT_MD, _HERE)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
