# -*- coding: utf-8 -*-
"""逐腿口径扫描：同一个口径在七条腿上分别值不值（**不看组合年化**）。

## 为什么单独做这张表

`_sens_trend.py` 把十五档口径压成「三腿组合年化」一个数 —— 那是「把变种当组合用」的答案。
但实际选策略是**按标的选**：一条腿只会被用在它自己合适的地方，组合层的权重、货基收益、
腿间相关性全都不该参与决策。所以这里把每条腿拆开算：

- 样本 = **该腿自己的全历史**（各自年线成形后起算），不是七腿共同窗口；
- 基准 = 该腿**恒持满带**（`state≡1.0`，同窗口、同费率）；
- 口径 = 只换「读数」（均线 / 双均线 / 唐奇安中轨 / 动量 / MACD），四象限与 5/20 阶梯不变；
- 波动层关闭（`VOL_USE=False`）—— 这里问的是「温度计换成哪个」，不是「要不要再叠风险预算」；
- 谱系 = 15 档 + 3 档短周期增补（`MA20+5` / `MA60+20` / `双均线20/60+5`），共 **18 档** ——
  原 15 档在「比 MA50 更短」的一端留了缺口，而那一端正是「灵敏度」最容易骗人的地方
  （报警更早，但假警报与调仓成本同时暴涨）。

## 读法

`Δ年化` / `Δ回撤` / `Δ夏普` 都是「该口径 − 该腿恒持满带」。**Δ回撤 为正 = 回撤更浅**（mdd 是负数，
浅 = 更接近 0）。一档口径如果三项全劣，说明这条腿根本不该套趋势闸门 —— 换温度计救不回来。

产物（本目录）：`_v5_sens_legs.md` / `_v5_sens_legs.png`。机械规则输出，非投资建议。
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
import rule               # noqa: E402

tg.VOL_USE = False        # 只研究趋势层读数，关掉波动层以隔离变量（与 _sens_trend.py 一致）

OUT_MD = os.path.join(_HERE, "_v5_sens_legs.md")
OUT_PNG = os.path.join(_HERE, "_v5_sens_legs.png")

LEGS = list(rule.LEG_ORDER)
NAME = {k: rule.LEGS[k]["name"] for k in LEGS}

# 口径谱系：`_sens_trend.py` 的 15 档（组合层）+ 3 档短周期增补（2026-11-07 按用户要求补上
# MA20+5 / MA60+20 / 双均线20/60+5，把「比 MA50 更短」的那一端也铺满，否则谱系在灵敏端有缺口）。
# 增补的三档只在**逐腿**口径跑：`_sens_trend.py` 仍保留原 15 档，便于与历史报告对比。
# (标签, 参数覆盖, 族)
SPECS = [
    ("MA250+20日（现行）", dict(KIND="ma", MA_WIN=250, SLOPE_N=20), "均线法"),
    ("MA250+5日", dict(KIND="ma", MA_WIN=250, SLOPE_N=5), "均线法"),
    ("MA200+20日", dict(KIND="ma", MA_WIN=200, SLOPE_N=20), "均线法"),
    ("MA120+20日", dict(KIND="ma", MA_WIN=120, SLOPE_N=20), "均线法"),
    ("MA120+5日", dict(KIND="ma", MA_WIN=120, SLOPE_N=5), "均线法"),
    ("MA100+10日", dict(KIND="ma", MA_WIN=100, SLOPE_N=10), "均线法"),
    ("MA60+20日", dict(KIND="ma", MA_WIN=60, SLOPE_N=20), "均线法"),
    ("MA60+10日", dict(KIND="ma", MA_WIN=60, SLOPE_N=10), "均线法"),
    ("MA50+5日", dict(KIND="ma", MA_WIN=50, SLOPE_N=5), "均线法"),
    ("MA20+5日", dict(KIND="ma", MA_WIN=20, SLOPE_N=5), "均线法"),
    ("双均线50/200+5日", dict(KIND="dual_ma", FAST_WIN=50, SLOW_WIN=200, SLOPE_N=5), "双均线"),
    ("双均线20/60+5日", dict(KIND="dual_ma", FAST_WIN=20, SLOW_WIN=60, SLOPE_N=5), "双均线"),
    ("唐奇安中轨55+10日", dict(KIND="donchian", DON_N=55, SLOPE_N=10), "结构法近似"),
    ("唐奇安中轨20+5日", dict(KIND="donchian", DON_N=20, SLOPE_N=5), "结构法近似"),
    ("动量6月+20日", dict(KIND="momentum", MOM_N=126, SLOPE_N=20), "动量法"),
    ("动量3月+10日", dict(KIND="momentum", MOM_N=63, SLOPE_N=10), "动量法"),
    ("MACD(50,200)+5日", dict(KIND="macd", FAST_WIN=50, SLOW_WIN=200, SLOPE_N=5), "交易信号"),
    ("MACD(12,26)+5日", dict(KIND="macd", FAST_WIN=12, SLOW_WIN=26, SLOPE_N=5), "交易信号"),
]

TOL = 0.05        # 「不劣」的容差（pp）：±0.05pp 视同噪声，不算改善也不算变差


def load_legs():
    """-> {leg: (全历史净值, 年线成形后的切片)}。切片起点固定 `tg.WARMUP`，与口径无关，保证可比。"""
    cache = {}
    for k in LEGS:
        px_full = tg.load(k)
        start = px_full.index[min(tg.WARMUP, max(len(px_full) - 2, 0))]
        cache[k] = (px_full, px_full.loc[start:])
    return cache


def run():
    cache = load_legs()
    base = {}
    for k, (_full, sub) in cache.items():
        nav = tg.run_leg(pd.Series(1.0, index=sub.index), sub, tg.fee_of(k))
        base[k] = tg.perf(nav)
    rows = {}
    for name, kw, fam in SPECS:
        per = {}
        for k, (px_full, sub) in cache.items():
            sm = tg.state_machine(px_full, k, **kw)
            state = sm["state"].loc[sub.index[0]:]
            fee = tg.fee_of(k)
            m = tg.perf(tg.run_leg(state, sub, fee))
            per[k] = dict(
                ann=m["ann"], mdd=m["mdd"], sharpe=m["sharpe"],
                expo=tg.exposure(state), acts=len(tg.actions(state)),
                whip=tg.whipsaw(state, sub)["n"],
                dann=(m["ann"] - base[k]["ann"]) * 100.0,
                dmdd=(m["mdd"] - base[k]["mdd"]) * 100.0,
                dsh=m["sharpe"] - base[k]["sharpe"],
            )
        rows[name] = dict(fam=fam, per=per, kw=kw)
    return cache, base, rows


def _tag(r):
    """四档判定（按「值不值」分级，不是按统计显著性）：
    ✔ 收益型：年化与回撤同时不劣；
    ◇ 风险调整型：年化最多让 1pp，换 ≥5pp 回撤、且夏普不劣（宽基上常见）；
    △ 仅夏普不劣：多出来的回撤改善是靠少担风险换的，年化明显更差；
    ✗ 全劣：这条腿换温度计救不回来。
    """
    if r["dann"] >= -TOL and r["dmdd"] >= -TOL:
        return "✔"
    if r["dann"] >= -1.0 and r["dmdd"] >= 5.0 and r["dsh"] >= 0:
        return "◇"
    if r["dsh"] >= 0:
        return "△"
    return "✗"


def matrix(rows, key, fmt="{:+.2f}", avg=True, nth="改善腿数"):
    head = "| 口径 | " + " | ".join(NAME[k] for k in LEGS) + f" | 平均 | {nth} |"
    sep = "|---|" + "---:|" * len(LEGS) + "---:|---:|"
    L = [head, sep]
    for name, r in rows.items():
        vals = [r["per"][k][key] for k in LEGS]
        cells = [fmt.format(v) for v in vals]
        avg_txt = fmt.format(float(np.mean(vals))) if avg else "—"
        n_pos = sum(1 for v in vals if v > TOL)
        L.append(f"| {name} | " + " | ".join(cells) + f" | {avg_txt} | {n_pos}/{len(LEGS)} |")
    return L


def leg_table(rows, k, base):
    b = base[k]
    L = [f"### {NAME[k]}　基准（恒持满带）：年化 {tg.fmt_pct(b['ann'])} · "
         f"最大回撤 {tg.fmt_pct(b['mdd'])} · 夏普 {b['sharpe']:.2f}",
         "",
         "| 口径 | 族 | 年化 | 最大回撤 | 夏普 | 曝光 | 调仓 | 打脸 | Δ年化 | Δ回撤 | Δ夏普 | 判定 |",
         "|---|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|:--:|"]
    for name, r in sorted(rows.items(), key=lambda z: -z[1]["per"][k]["dann"]):
        p = r["per"][k]
        L.append(f"| {name} | {r['fam']} | {tg.fmt_pct(p['ann'])} | {tg.fmt_pct(p['mdd'])} | "
                 f"{p['sharpe']:.2f} | {p['expo']:.0%} | {p['acts']} | {p['whip']} | "
                 f"{p['dann']:+.2f}pp | {p['dmdd']:+.2f}pp | {p['dsh']:+.2f} | {_tag(p)} |")
    return L


def figure(base, rows):
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    plt.rcParams["font.sans-serif"] = ["Microsoft YaHei", "SimHei", "DejaVu Sans"]
    plt.rcParams["axes.unicode_minus"] = False

    names = list(rows)
    panels = [
        ("Δ年化 vs 恒持满带（pp）", lambda p: p["dann"]),
        ("Δ最大回撤（pp，正 = 更浅）", lambda p: p["dmdd"]),
        ("Δ夏普", lambda p: p["dsh"]),
    ]
    fig, axes = plt.subplots(1, 3, figsize=(19.0, 9.3))
    for ax, (title, fn) in zip(axes, panels):
        M = np.array([[fn(rows[n]["per"][k]) for k in LEGS] for n in names], dtype=float)
        lim = float(np.nanmax(np.abs(M))) if M.size else 1.0
        ax.imshow(M, cmap="RdYlGn", vmin=-lim, vmax=lim, aspect="auto")
        ax.set_xticks(range(len(LEGS)))
        ax.set_xticklabels([NAME[k] for k in LEGS], rotation=35, ha="right", fontsize=8.5)
        ax.set_yticks(range(len(names)))
        ax.set_yticklabels(names, fontsize=8.0)
        ax.set_title(title, fontsize=11, color="#1b2b3a", weight="bold", loc="left")
        for i in range(M.shape[0]):
            for j in range(M.shape[1]):
                ax.text(j, i, f"{M[i, j]:+.2f}", ha="center", va="center",
                        fontsize=6.6, color="#22303d")
        ax.set_xticks(np.arange(-.5, len(LEGS), 1), minor=True)
        ax.set_yticks(np.arange(-.5, len(names), 1), minor=True)
        ax.grid(which="minor", color="white", lw=1.2)
        ax.tick_params(which="minor", length=0)
    fig.suptitle("V5 · 逐腿口径矩阵：同一套规则、只换温度计，各自标的上比「恒持满带」多赚/多亏多少",
                 x=0.03, ha="left", fontsize=13, color="#1b2b3a", weight="bold")
    fig.text(0.03, 0.012,
             "每格 = 该口径 − 该腿恒持满带（同窗口、扣申赎费与滑点、每条腿用自己全历史）。"
             "绿 = 优于恒持，红 = 劣于。波动层关闭。机械规则输出，非投资建议。",
             fontsize=8.6, color="#5b6b7a")
    fig.subplots_adjust(left=0.135, right=0.985, top=0.90, bottom=0.10, wspace=0.35)
    fig.savefig(OUT_PNG, dpi=130, facecolor="white")
    plt.close(fig)
    print("图:", OUT_PNG)


def main():
    cache, base, rows = run()
    span = {k: (cache[k][1].index[0].date(), cache[k][1].index[-1].date()) for k in LEGS}

    L = [
        "# V5 · 逐腿口径矩阵（按标的选口径，不看组合年化）",
        "",
        "> 组合年化回答的是「把这个变种当组合用值不值」，但实际用法是**一条腿用在它自己合适的标的**上。"
        "所以这张表把七条腿拆开：每条腿用自己的全历史、自己的费率、自己的恒持满带作基准，"
        "问「这一档口径在这条腿上值不值」。",
        "> 口径只换**读数**（`trend_gate.readout` 的 `KIND`），四象限定义与 5/20 日档位阶梯**完全不变**；"
        "波动层关闭（`VOL_USE=False`），否则「温度计的差」会与「风险预算的差」混在一起。",
        "> 口径谱系 = **18 档**：`_sens_trend.py` 的 15 档 + 3 档短周期增补（`MA20+5` / `MA60+20` / "
        "`双均线20/60+5`，2026-11-07 补上「比 MA50 更短」的那一端，否则谱系在灵敏端留了缺口）。"
        "增补的三档只在逐腿口径跑；`_sens_trend.py` 保持原 15 档，便于与历史报告对比。",
        "> 判定四档：**✔ 收益型** = 年化与回撤同时不劣（±0.05pp 容差内算不劣）；"
        "**◇ 风险调整型** = 年化最多让 1pp，换 ≥5pp 回撤且夏普不劣；"
        "**△** = 只有夏普不劣（回撤改善是靠整体少担风险换的，年化明显更差）；"
        "**✗** = 三项全劣（这条腿换温度计救不回来）。",
        "> 数字随 `data/_otc_nav.json` 每日增量刷新，与 README §3.2/§3.9 的旧数字会有 0.x pp 漂移"
        "（样本长度不同）；**只做横向比口径**，不要纵向对旧表。",
        "",
        "| 腿 | 样本窗口 | 交易日 | 恒持满带年化 | 恒持满带回撤 | 恒持满带夏普 |",
        "|---|---|---:|---:|---:|---:|",
    ]
    for k in LEGS:
        b = base[k]
        L.append(f"| {NAME[k]} | {span[k][0]} ~ {span[k][1]} | {b['days']} | "
                 f"{tg.fmt_pct(b['ann'])} | {tg.fmt_pct(b['mdd'])} | {b['sharpe']:.2f} |")

    L += [
        "",
        "## 1. Δ年化矩阵（pp，正 = 套闸门比恒持满带多赚）",
        "",
    ] + matrix(rows, "dann") + [
        "",
        "## 2. Δ最大回撤矩阵（pp，正 = 回撤更浅）",
        "",
    ] + matrix(rows, "dmdd") + [
        "",
        "## 3. Δ夏普矩阵",
        "",
    ] + matrix(rows, "dsh", fmt="{:+.2f}") + [
        "",
        "## 4. 逐腿明细（按 Δ年化 排序，含绝对年化与调仓成本）",
        "",
    ]
    for k in LEGS:
        L += leg_table(rows, k, base) + [""]

    # ---------------- 结论：每条腿该用什么
    YEARS = {k: len(cache[k][1]) / 252.0 for k in LEGS}
    L += ["## 5. 结论：每条腿该用什么（先按标的选腿，再选口径）", "",
          "「推荐」= 该腿最好的一档（✔ 收益型优先，其次 ◇ 风险调整型）；"
          "「不套」= 没有任何口径能保住年化，这条腿用恒持满带（或换成估值/现金流锚）即可。",
          "",
          "| 腿 | 样本年数 | 推荐口径 | 该口径年化 | 最大回撤 | 夏普 | Δ年化 | Δ回撤 | 调仓/年 | 打脸 | 判定 |",
          "|---|---:|---|---:|---:|---:|---:|---:|---:|---:|:--:|"]
    best_of_leg = {}
    for k in LEGS:
        yrs = YEARS[k]
        cand = [(n, r) for n, r in rows.items() if _tag(r["per"][k]) in ("✔", "◇")]
        cand.sort(key=lambda z: (0 if _tag(z[1]["per"][k]) == "✔" else 1,
                                 -z[1]["per"][k]["dann"], -z[1]["per"][k]["dmdd"]))
        bi = max(rows.items(), key=lambda z: (z[1]["per"][k]["dann"], z[1]["per"][k]["dmdd"]))
        best_of_leg[k] = bi
        b = base[k]
        if cand:
            n, r = cand[0]
            p = r["per"][k]
            L.append(f"| **{NAME[k]}** | {yrs:.1f} | {_tag(p)} {n} | {tg.fmt_pct(p['ann'])} | "
                     f"{tg.fmt_pct(p['mdd'])} | {p['sharpe']:.2f} | {p['dann']:+.2f}pp | "
                     f"{p['dmdd']:+.2f}pp | {p['acts'] / yrs:.1f} | {p['whip']} | {_tag(p)} |")
        else:
            L.append(f"| **{NAME[k]}** | {yrs:.1f} | 不套（恒持满带） | {tg.fmt_pct(b['ann'])} | "
                     f"{tg.fmt_pct(b['mdd'])} | {b['sharpe']:.2f} | — | — | 0 | 0 | ✗ |")

    L += [""]
    for k in LEGS:
        yrs = YEARS[k]
        b = base[k]
        cand = [(n, r) for n, r in rows.items() if _tag(r["per"][k]) in ("✔", "◇")]
        if cand:
            n, r = best_of_leg[k]
            p = r["per"][k]
            word = "收益与回撤同时改善" if _tag(p) == "✔" else \
                f"让 {abs(p['dann']):.2f}pp 年化换 {p['dmdd']:.2f}pp 回撤（夏普 {p['dsh']:+.2f}）"
            L.append(f"- **{NAME[k]}**（{yrs:.1f} 年）：`{n}` {word}，年化 "
                     f"{tg.fmt_pct(p['ann'])} vs 恒持 {tg.fmt_pct(b['ann'])}，回撤 "
                     f"{tg.fmt_pct(p['mdd'])} vs {tg.fmt_pct(b['mdd'])}，"
                     f"{p['acts'] / yrs:.1f} 次/年、打脸 {p['whip']} 次。")
        else:
            n, r = best_of_leg[k]
            p = r["per"][k]
            L.append(f"- **{NAME[k]}**（{yrs:.1f} 年）：**没有口径能保住年化**。最好的也只是 `{n}`"
                     f"（{p['dann']:+.2f}pp / {p['dmdd']:+.2f}pp）—— 这条腿套趋势闸门就是纯付成本，"
                     f"恒持满带 {tg.fmt_pct(b['ann'])} 更好；要择时请用估值/现金流锚，不是价格趋势。")

    L += ["", "## 6. 结论：口径的跨腿稳定性（同一批标的上反复有效，才算真口径）", ""]
    stat = []
    for n, r in rows.items():
        vals = [r["per"][k]["dann"] for k in LEGS]
        ms = [r["per"][k]["dmdd"] for k in LEGS]
        stat.append(dict(name=n, n_pos=sum(1 for v in vals if v > TOL),
                         n_both=sum(1 for k in LEGS if _tag(r["per"][k]) == "✔"
                                    and r["per"][k]["dann"] > TOL),
                         avg=float(np.mean(vals)), worst=float(np.min(vals)),
                         avg_mdd=float(np.mean(ms))))
    stat.sort(key=lambda z: (-z["n_both"], -z["n_pos"], -z["avg"]))
    L += [
        "| 口径 | 年化为正的腿数 | ✔ 收益型的腿数 | 平均 Δ年化 | 最差一条腿 Δ年化 | 平均 Δ回撤 |",
        "|---|---:|---:|---:|---:|---:|",
    ]
    for s in stat:
        L.append(f"| {s['name']} | {s['n_pos']}/{len(LEGS)} | {s['n_both']}/{len(LEGS)} | "
                 f"{s['avg']:+.2f}pp | {s['worst']:+.2f}pp | {s['avg_mdd']:+.2f}pp |")

    cur = rows["MA250+20日（现行）"]
    cur_pos = sum(1 for k in LEGS if cur["per"][k]["dann"] > TOL)
    cur_avg = float(np.mean([cur["per"][k]["dann"] for k in LEGS]))
    cur_mdd = float(np.mean([cur["per"][k]["dmdd"] for k in LEGS]))
    best = stat[0]

    # 短周期三档（2026-11-07 增补）的汇总：谱系往短端推到底值不值
    short_ids = ["MA20+5日", "MA60+20日", "双均线20/60+5日"]
    short_seg = []
    for n in short_ids:
        if n not in rows:
            continue
        vals = [rows[n]["per"][k]["dann"] for k in LEGS]
        short_seg.append(f"{n} 平均 {float(np.mean(vals)):+.2f}pp、"
                         f"{sum(1 for v in vals if v > TOL)}/{len(LEGS)} 条腿年化为正")
    p20 = rows["MA20+5日"]["per"]["a500"]
    p60 = rows["MA60+20日"]["per"]["a500"]
    short_note = (
        "- **把谱系往短端推（" + " / ".join(short_ids) + "）没有救回来**："
        + "；".join(short_seg)
        + f"。短周期的代价是切换频率 —— `MA20+5` 在 A500 上 {p20['acts']} 次调仓"
          f"（{p20['acts'] / YEARS['a500']:.1f} 次/年）、被打脸 {p20['whip']} 次，"
          f"回撤只浅 {p20['dmdd']:.2f}pp、年化反而 {p20['dann']:+.2f}pp。"
          f"三档里唯一像样的是 `MA60+20` 在 A500 上"
          f"（{p60['dann']:+.2f}pp 年化换 {p60['dmdd']:+.2f}pp 回撤、夏普 {p60['dsh']:+.2f}）"
          "—— 但那是「风险调整型」，靠的是回撤不是收益，和 `MA60+10` 同一档，换它没有额外收益。")
    L += [
        "",
        f"- **没有跨腿都稳的口径。** 排第一的 {best['name']} 也只有 {best['n_both']}/{len(LEGS)} "
        f"条腿能「收益型」，平均 {best['avg']:+.2f}pp、最差一条腿 {best['worst']:+.2f}pp。"
        "这正是这张表的意义：**口径适配是标的级的** —— 「哪个均线更好」在聚合层面永远回答成"
        "「都差不多」，拆到腿上才有信号。",
        f"- **现行 MA250+20日**：{cur_pos}/{len(LEGS)} 条腿年化为正，平均 {cur_avg:+.2f}pp、"
        f"平均回撤改善 {cur_mdd:+.2f}pp。它的定位是「砍尾部」：在阴跌型资产（科创50）上正超额，"
        "在长牛宽基（纳指/标普）上是纯成本。",
        f"- **短样本腿要打折**：科创50（{YEARS['kc50']:.1f} 年）、恒生科技（{YEARS['hkt']:.1f} 年）的"
        "「最优口径」是在约 1000 个交易日上挑出来的，而 MACD(50,200) 这类慢参数几乎等于拿全样本调参 —— "
        "方向可参考，具体参数别当结论。",
        short_note,
        "- 用法：**先按标的选腿，再在该腿的明细（第 4 节）里选口径**。不要用跨腿平均挑口径，"
        "因为每条腿的绝对收益水平不同（纳指恒持 +16% 与恒生科技恒持 ≈0 的「+1pp」含义完全不同）。",
        "",
        "> 一档口径在某条腿上年化更高、但回撤也更大时，看 Δ夏普那一列；"
        "如果 Δ夏普仍 ≤ 0，说明多出来的年化只是多担了风险，不是口径变准了。",
        "",
        "> 机械规则输出，非投资建议。",
    ]

    with io.open(OUT_MD, "w", encoding="utf-8", newline="\n") as f:
        f.write("\n".join(L))
    print("表:", OUT_MD)

    print("\nΔ年化（口径 − 恒持满带，pp）:")
    print("  " + " " * 20 + "".join(f"{NAME[k]:>9s}" for k in LEGS))
    for n, r in rows.items():
        print(f"  {n:20s}" + "".join(f"{r['per'][k]['dann']:>+9.2f}" for k in LEGS))
    print("\n每腿最好的一档（按 Δ年化）:")
    for k in LEGS:
        n, r = best_of_leg[k]
        p = r["per"][k]
        print(f"  {NAME[k]:8s} {n:20s} 年化 {tg.fmt_pct(p['ann']):>8s} "
              f"差 {p['dann']:+.2f}pp · 回撤 {tg.fmt_pct(p['mdd']):>8s} 差 {p['dmdd']:+.2f}pp")

    figure(base, rows)


if __name__ == "__main__":
    main()
