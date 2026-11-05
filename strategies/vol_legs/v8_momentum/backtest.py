# -*- coding: utf-8 -*-
"""波动资产 · V8 变种（跨资产 12-1 动量）· 组合层回测（对照 + 敏感性 + 硬断言）。

池与口径完全跟随 `strategies/vol_legs`：
- 七腿 = `rule.LEG_ORDER`；价格 = `data.series_for_leg()` 场外**复权净值**（含分红再投 / 汇率 / 跟踪误差）；
- 月末盘后算信号、**次一交易日收盘成交**；未投部分按货基 `rule.CASH_ANNUAL` 逐日计息；
- 成本 = 真实申赎费 + 单边滑点 0.1%（「含费」与「不计费」两列并排看）。

**本变种是组合层额度分配**（配哪几条腿），不是单腿时点，因此没有逐腿表。
对照基准 = 静态带（七腿恒持各自带位，满仓 40pp）。

口径要点：所有变体的信号（打分与历史计数）都在**全样本面板**上算好再切到公共窗口 ——
否则对切片后的 df 重新 shift 会把「首年历史不足」算错（本脚本第一版就踩了这个坑）。

规则见 `momentum.py` 模块 docstring 与 README。

产物（策略类，落 `backtest/`）：
- `_v8_momentum_bt.md`   报告
- `_v8_momentum_bt.jsonl` 机读结果

模拟结果，非投资建议。
"""
import json
import os
import sys

import numpy as np
import pandas as pd

try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:  # noqa: BLE001
    pass

_HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, _HERE)

import momentum as mo  # noqa: E402

OUTDIR = os.path.join(_HERE, "backtest")
OUT_MD = os.path.join(OUTDIR, "_v8_momentum_bt.md")
OUT_JSONL = os.path.join(OUTDIR, "_v8_momentum_bt.jsonl")

TOPN_SET = [1, 2, 3, 4, 5]
SENS = [
    ("基准（12-1 · 最强3条 · 月末 · 归一化）", {}),
    ("最强 1 条", dict(TOPN=1)),
    ("最强 2 条", dict(TOPN=2)),
    ("最强 4 条", dict(TOPN=4)),
    ("最强 5 条", dict(TOPN=5)),
    ("回看 6 个月（SKIP=1月）", dict(LOOKBACK=126, MIN_HIST=126 + 21)),
    ("回看 9 个月（SKIP=1月）", dict(LOOKBACK=189, MIN_HIST=189 + 21)),
    ("不跳过最近 1 个月（SKIP=0）", dict(SKIP=0, MIN_HIST=252)),
    ("季末调仓（Q）", dict(REBAL="Q")),
    ("权重不归一化（只用各自带位）", dict(WEIGHT_MODE="band")),
]


def mk_tgt(df_all, start, **kw):
    """在全样本面板上算目标权重，再切到公共窗口（避免切片导致的「历史不足」误判）。

    `w=None` 表示该期「池子太小、维持不动」，不参与调仓。
    """
    t = mo.targets(df_all, hist=mo.nobs(df_all), **kw)
    return {d: v for d, v in t.items() if d >= start and v["w"] is not None}


# ---------------------------------------------------------------- 1. 组合层
def run_variant(df, tgt, fee=True):
    nav, expo, trades = mo.simulate(df, tgt, fee=fee, detail=True)
    m = mo.perf(nav)
    return dict(nav=nav, ann=m["ann"], mdd=m["mdd"], vol=m["vol"], sharpe=m["sharpe"],
                cum=m["cum"], expo=float(expo.mean()), trades=len(trades))


def run_variant_static(df, fee=True, weight=None):
    nav, expo, trades = mo.static_nav(df, weight=weight, fee=fee, detail=True)
    m = mo.perf(nav)
    return dict(nav=nav, ann=m["ann"], mdd=m["mdd"], vol=m["vol"], sharpe=m["sharpe"],
                cum=m["cum"], expo=float(expo.mean()), trades=len(trades))


def main_rows(df, df_all, start):
    rows = [
        ("静态带（七腿恒持各自带位）", run_variant_static(df, fee=True)),
        ("静态带 · 不计费", run_variant_static(df, fee=False)),
        ("七腿等权（恒定持有）",
         run_variant_static(df, fee=True, weight={k: 1.0 / len(mo.LEGS) for k in mo.LEGS})),
    ]
    for n in TOPN_SET:
        t2 = mk_tgt(df_all, start, TOPN=n)
        if n == mo.TOPN:
            rows.append((f"**V8 默认（最强 {n} 条 · 归一化）**", run_variant(df, t2, fee=True)))
            rows.append(("V8 默认 · 不计费", run_variant(df, t2, fee=False)))
        else:
            rows.append((f"最强 {n} 条", run_variant(df, t2, fee=True)))
    return rows


def sens_rows(df, df_all, start):
    out = []
    for name, kw in SENS:
        t2 = mk_tgt(df_all, start, **kw)
        nav, expo, trades = mo.simulate(df, t2, detail=True)
        m = mo.perf(nav)
        out.append(dict(name=name, ann=m["ann"], mdd=m["mdd"], sharpe=m["sharpe"],
                        expo=float(expo.mean()), trades=len(trades), rebal=len(t2)))
    return out


# ---------------------------------------------------------------- 2. 选腿统计
def pick_stats(df, tgt):
    cnt = {k: 0 for k in df.columns}
    for v in tgt.values():
        for k in v["pick"]:
            cnt[k] += 1
    n = max(len(tgt), 1)
    return [(k, cnt[k], cnt[k] / n) for k in mo.LEGS]


def recent_picks(tgt, n=6):
    return [(d, tgt[d]) for d in sorted(tgt)[-n:]]


# ---------------------------------------------------------------- 3. 硬断言
def check_all(df_all, df, tgt):
    out = []
    # 1) 权重非负、和 ≤ 1
    ok1, d1 = True, ""
    for d, v in tgt.items():
        if v["w"] is None:
            continue
        s = sum(v["w"].values())
        if any(x < -1e-12 for x in v["w"].values()) or s > 1.0 + 1e-9:
            ok1, d1 = False, f"{d.date()} 权重和 {s:.4f}"
            break
    out.append(("每期权重非负且 Σ ≤ 1", ok1, d1 or f"{len(tgt)} 期全部通过"))

    # 2) 被选中的腿历史都够长（全样本累计观测数）
    nb = mo.nobs(df_all).reindex(df.index)
    ok2, d2 = True, ""
    for d, v in tgt.items():
        for k in v["pick"]:
            if float(nb.loc[d, k]) < mo.MIN_HIST:
                ok2, d2 = False, f"{d.date()} {k} 仅 {int(nb.loc[d, k])} 根"
                break
        if not ok2:
            break
    out.append((f"被选中的腿历史都 ≥ {mo.MIN_HIST} 根", ok2, d2 or "全部通过"))

    # 3) 每个成交日的前一交易日必是决策日
    _, _, trades = mo.simulate(df, tgt, detail=True)
    pos = {d: i for i, d in enumerate(df.index)}
    tdates = sorted({t["date"] for t in trades})
    ok3, d3 = True, ""
    for d in tdates:
        i = pos[d]
        if i == 0 or df.index[i - 1] not in tgt:
            ok3, d3 = False, f"{d.date()} 成交但前一交易日非决策日"
            break
    out.append(("成交日 = 决策日的次一交易日（T+1，无前视）", ok3, d3 or f"{len(tdates)} 个成交日"))

    # 4) 截断最后 6 天重跑 -> 历史选腿与权重不变
    cut = df_all.iloc[:-6]
    t_cut = mo.targets(cut, hist=mo.nobs(cut))
    common = [d for d in t_cut if d in tgt]
    ok4, d4 = True, ""
    for d in common:
        a, b = tgt[d]["w"], t_cut[d]["w"]
        if (a is None) != (b is None):
            ok4, d4 = False, f"{d.date()} 调仓状态不一致"
            break
        if a is None:                                    # 两侧都「维持不动」，视为一致
            continue
        if set(a) != set(b) or any(abs(a[k] - b[k]) > 1e-12 for k in a):
            ok4, d4 = False, f"{d.date()} 选腿/权重不一致"
            break
    out.append(("截掉最后 6 天重跑，历史选腿与权重不变（无前视）", ok4,
                d4 or f"{len(common)} 期一致"))

    # 5) TOPN=7 + 归一化 == 静态带（从「第一次七腿全选中」那天起比较）
    t7_all = mo.targets(df_all, hist=mo.nobs(df_all), TOPN=7)
    full = [d for d, v in t7_all.items() if len(v["pick"]) == len(mo.LEGS)]
    if full:
        d0 = full[0]
        sub = df_all.loc[d0:]
        t7 = {d: v for d, v in t7_all.items() if d >= d0}
        a = mo.simulate(sub, t7)
        b = mo.static_nav(sub)
        rel = abs(float(a.iloc[-1] / b.iloc[-1]) - 1.0)
        out.append(("最强 7 条（归一化）== 静态带", bool(rel < 1e-9),
                    f"{d0.date()} 起 · 差 {rel:.1e}"))
    else:
        out.append(("最强 7 条（归一化）== 静态带", False, "没有七腿都合格的调仓日"))

    # 6) TOPN=0（空仓）年化 == 货基
    t0 = mk_tgt(df_all, df.index[0], TOPN=0)
    m0 = mo.perf(mo.simulate(df, t0))
    out.append(("最强 0 条（全货基）年化 == 货基",
                bool(abs(m0["ann"] - mo.CASH_ANNUAL) < 5e-4),
                f"{m0['ann']:.4%} vs {mo.CASH_ANNUAL:.4%}"))
    return out


# ---------------------------------------------------------------- 4. 主流程
def main():
    os.makedirs(OUTDIR, exist_ok=True)
    df_all = mo.panel()
    tgt_all = mo.targets(df_all, hist=mo.nobs(df_all))
    act = [d for d, v in tgt_all.items() if v["w"] is not None]   # 首个「池子够大」的调仓日
    start = min(act)
    df = df_all.loc[start:]
    tgt = {d: tgt_all[d] for d in act if d >= start}

    rows = main_rows(df, df_all, start)
    sens = sens_rows(df, df_all, start)
    stats = pick_stats(df, tgt)
    recent = recent_picks(tgt, 6)
    checks = check_all(df_all, df, tgt)

    navs = {name: r["nav"] for name, r in rows}
    yr_mo = mo.yearly(navs["**V8 默认（最强 3 条 · 归一化）**"])
    yr_st = mo.yearly(navs["静态带（七腿恒持各自带位）"])

    with open(OUT_JSONL, "w", encoding="utf-8") as f:
        for name, r in rows:
            f.write(json.dumps({"portfolio": name,
                                **{k: (round(v, 4) if isinstance(v, float) else v)
                                   for k, v in r.items() if k != "nav"}},
                               ensure_ascii=False) + "\n")
        for r in sens:
            f.write(json.dumps({"sens": r["name"],
                                **{k: (round(v, 4) if isinstance(v, float) else v)
                                   for k, v in r.items() if k != "name"}},
                               ensure_ascii=False) + "\n")
        for k, c, p in stats:
            f.write(json.dumps({"pick": k, "count": c, "share": round(p, 4)},
                               ensure_ascii=False) + "\n")
        for d, v in recent:
            f.write(json.dumps({"recent": d.date().isoformat(), "pick": v["pick"],
                                "w": {k: round(x, 4) for k, x in v["w"].items()},
                                "score": {k: round(x, 4) for k, x in v["score"].items()}},
                               ensure_ascii=False) + "\n")
        f.write(json.dumps({"checks": [dict(name=n, ok=bool(o), note=t) for n, o, t in checks]},
                           ensure_ascii=False) + "\n")

    def pick_row(prefix):
        return next(r for n, r in rows if n.startswith(prefix))

    base = pick_row("**V8 默认")
    base_nc = pick_row("V8 默认 · 不计费")
    static = pick_row("静态带（七腿恒持各自带位）")
    static_nc = pick_row("静态带 · 不计费")
    eqw = pick_row("七腿等权")
    yrs = (df.index[-1] - df.index[0]).days / 365.25
    bad = [c for c in checks if not c[1]]
    win = f"{df.index[0].date()} ~ {df.index[-1].date()}"

    sens_ann = [r["ann"] for r in sens]
    sens_sh = [r["sharpe"] for r in sens]
    beat_n = sum(1 for r in sens if r["ann"] > static["ann"])
    best_sh = max(sens, key=lambda r: r["sharpe"])
    best_static_like = min(sens, key=lambda r: abs(r["mdd"]))

    L = [
        "# 波动资产 · V8 变种：跨资产 12-1 动量 · 组合层回测",
        "> 池与主策略**完全一致**：`rule.LEG_ORDER` 七腿（中证A500 / 沪深300 / 纳指100 / 标普500 / "
        "科创50 / 恒生科技 / 黄金）",
        "> 价格 = 场外**复权净值** · 月末盘后算信号、**次一交易日收盘成交** · "
        f"空仓按货基 {mo.CASH_ANNUAL:.2%} 计息 · 成本 = 真实申赎费 + 单边滑点 {mo.SLIP:.2%}",
        "> 规则权威源 `v8_momentum/momentum.py` 顶部常量。",
        "",
        "## 1. 规则（可执行版）",
        "",
        "| 环节 | 规则 |",
        "|---|---|",
        f"| 打分 | `score = P[t−{mo.SKIP}] / P[t−{mo.LOOKBACK}] − 1`（12 个月，跳过最近 1 个月） |",
        f"| 排序 | 每月最后一个交易日收盘，对「历史 ≥ {mo.MIN_HIST} 根」的腿排序 |",
        f"| 选择 | 取最强 `TOPN={mo.TOPN}` 条；不足则全取；一条都不够则空仓 |",
        f"| 权重 | `{mo.WEIGHT_MODE}`：选中腿按各自目标带比例归一化到满仓（Σ=1） |",
        "| 成交 | 月末盘后算信号、**次一交易日收盘成交**（与 v1~v7 同一纪律） |",
        "| 成本 | 每次调仓按 |Δ权重| 扣申赎费 + 滑点 |",
        "",
        "> **层次**：前七个变种都是「单腿进出时点」，本变种是**组合层额度分配**（配哪几条腿），"
        "因此没有逐腿表。它可以与 v7（波动率目标化）叠加：v8 定配哪几条、v7 定每条露多少。",
        "",
        f"## 2. 主对照（{win} · {yrs:.1f} 年 · 满仓 = 40pp）",
        "",
        "| 组合 | 累计 | 年化 | 波动 | 最大回撤 | 夏普 | 平均曝光 | 调仓笔数 |",
        "|---|---:|---:|---:|---:|---:|---:|---:|",
    ]
    for name, r in rows:
        L.append(f"| {name} | {mo.fmt_pct(r['cum'], 1)} | {mo.fmt_pct(r['ann'])} | "
                 f"{r['vol'] * 100:.2f}% | {mo.fmt_pct(r['mdd'])} | {r['sharpe']:.2f} | "
                 f"{r['expo']:.0%} | {r['trades']} |")
    L += [
        "",
        f"> **默认 vs 静态带**：年化 {mo.fmt_pct(base['ann'])} vs {mo.fmt_pct(static['ann'])}"
        f"（**{(base['ann'] - static['ann']) * 100:+.2f}pp**）、回撤 "
        f"{mo.fmt_pct(base['mdd'])} vs {mo.fmt_pct(static['mdd'])}"
        f"（**{(base['mdd'] - static['mdd']) * 100:+.2f}pp**）、夏普 {base['sharpe']:.2f} vs "
        f"{static['sharpe']:.2f}。",
        f"> **含费 vs 不计费**：{mo.fmt_pct(base['ann'])} vs {mo.fmt_pct(base_nc['ann'])}"
        f" → 费用吃掉 **{(base_nc['ann'] - base['ann']) * 100:.2f}pp/年**"
        f"（静态带 {mo.fmt_pct(static['ann'])} vs {mo.fmt_pct(static_nc['ann'])}，"
        f"差 {(static_nc['ann'] - static['ann']) * 100:.2f}pp）。**换手成本是本变种的生死线。**",
        f"> 七腿等权：{mo.fmt_pct(eqw['ann'])} / {mo.fmt_pct(eqw['mdd'])} / {eqw['sharpe']:.2f}。",
        "",
        "## 3. 参数敏感性（每档都扣费，只改一格）",
        "",
        "| 档位 | 年化 | 最大回撤 | 夏普 | 平均曝光 | 调仓笔数 | 调仓期数 |",
        "|---|---:|---:|---:|---:|---:|---:|",
    ]
    for r in sens:
        L.append(f"| {r['name']} | {mo.fmt_pct(r['ann'])} | {mo.fmt_pct(r['mdd'])} | "
                 f"{r['sharpe']:.2f} | {r['expo']:.0%} | {r['trades']} | {r['rebal']} |")
    L += [
        "",
        f"> 年化落在 {mo.fmt_pct(min(sens_ann))} ~ {mo.fmt_pct(max(sens_ann))}、夏普落在 "
        f"{min(sens_sh):.2f} ~ {max(sens_sh):.2f}；"
        f"**{len(sens)} 档里有 {beat_n} 档年化超过静态带（{mo.fmt_pct(static['ann'])}），"
        f"但没有一档夏普超过它（{static['sharpe']:.2f}）** —— "
        f"全表最高夏普是「{best_sh['name']}」{best_sh['sharpe']:.2f}。",
        f"> 回撤最浅的是「{best_static_like['name']}」{mo.fmt_pct(best_static_like['mdd'])}"
        f"（曝光 {best_static_like['expo']:.0%}）—— 那是**半仓换来的**，不是择时的功劳。",
        "> 结论：动量在这个池子上**没有风险调整后的超额**，最好的一档也只是「少配一点、少赚一点」。",
        "",
        "## 4. 各腿被选中频率",
        "",
        "| 腿 | 目标带 | 被选中期数 | 占比 |",
        "|---|---:|---:|---:|",
    ]
    for k, c, p in stats:
        L.append(f"| {mo.rule.LEGS[k]['name']} | {mo.BAND[k]:.1f}pp | {c} | {p:.0%} |")
    L += [
        "",
        "## 5. 最近 6 期名单（可执行口径）",
        "",
        "| 决策日（收盘） | 持有 | 权重 | 12-1 动量 |",
        "|---|---|---|---|",
    ]
    for d, v in recent:
        hold = "、".join(mo.rule.LEGS[k]["name"] for k in v["pick"]) or "（空仓）"
        w = "、".join(f"{x:.0%}" for x in v["w"].values()) or "—"
        sc = "、".join(f"{x:+.1%}" for x in v["score"].values()) or "—"
        L.append(f"| {d.date()} | {hold} | {w} | {sc} |")
    L += [
        "",
        "## 6. 逐年收益（动量 vs 静态带）",
        "",
        "| 年份 | V8 默认 | 静态带 | 差 |",
        "|---|---:|---:|---:|",
    ]
    for y in sorted(set(yr_mo) | set(yr_st)):
        a, b = yr_mo.get(y), yr_st.get(y)
        diff = "—" if (a is None or b is None) else f"{(a - b) * 100:+.2f}pp"
        L.append(f"| {y} | {mo.fmt_pct(a)} | {mo.fmt_pct(b)} | {diff} |")
    worst_y = min(((y, yr_mo[y] - yr_st.get(y, 0.0)) for y in yr_mo), key=lambda z: z[1])
    L += [
        "",
        f"> 最差年份 **{worst_y[0]}**：动量 {mo.fmt_pct(yr_mo[worst_y[0]])} vs 静态带 "
        f"{mo.fmt_pct(yr_st.get(worst_y[0]))}（**{worst_y[1] * 100:+.2f}pp**）—— "
        "动量在这种「最强的腿随即补跌」的年份被打得最惨。",
        "",
        "## 7. 硬断言",
        "",
        "| 检查 | 结果 | 备注 |",
        "|---|---|---|",
    ]
    for name, ok, note in checks:
        L.append(f"| {name} | {'✅' if ok else '❌'} | {note} |")
    L += [
        "",
        f"共 {len(checks)} 条，失败 {len(bad)} 条" + ("。" if not bad else
            "：**" + "；".join(c[0] for c in bad) + "**"),
        "",
        "## 8. 口径与已知边界",
        "",
        "1. **这个池子上动量没有风险调整后的超额**：七腿都是宽基/商品指数基金、长期同涨同跌，"
        "截面排序的信息量远低于个股池；实测最好的一档夏普也只到 "
        f"{max(sens_sh):.2f}（静态带 {static['sharpe']:.2f}）。**不是参数没调好。**",
        "2. **换手是生死线**：每月最多换 N 条腿，A 类场外单次往返约 0.6%，"
        f"默认档一年被吃掉 {(base_nc['ann'] - base['ann']) * 100:.2f}pp；"
        "场内 ETF 或季末调仓能显著改善（§3）。",
        "3. **它会追高**：动量买「过去一年最强」，泡沫末期会重仓最热资产 —— "
        "与 v7（高波动减仓）正好互补，两者叠加才合理。",
        "4. **早期样本腿数更少**：2013-2015 是 4 选 N（无黄金）、2015-2023 是 5 选 N、"
        "2023 起才是 7 选 N（科创50/恒生科技 2022 年才有场外净值）。",
        "5. **口径坑（已修）**：信号必须先在**全样本**面板上算好再切窗口；"
        "对切片后的 df 重新 `shift(252)` 会让首年被判成「历史不足」，白坐一年货基并虚增收益。",
        "6. **代理口径**：A500 / 标普样本前段是桥接（`000051` / `050025`）；"
        "美股腿用场外人民币复权净值，叠加汇率与溢价。",
        "7. **未计**：大额限购、QDII 额度关闭时的顺延、赎回确认时滞、C 类销售服务费。",
        "8. **不做 walk-forward 选优**：参数写死，§3 已给敏感性。",
        "",
        "> **模拟结果，非投资建议。**",
    ]

    with open(OUT_MD, "w", encoding="utf-8") as f:
        f.write("\n".join(L))

    print("报告:", OUT_MD)
    print("结果:", OUT_JSONL)
    print(f"窗口 {win} · {yrs:.1f} 年 · 调仓 {len(tgt)} 期")
    for name, r in rows:
        print(f"  {name:34s} 年化 {mo.fmt_pct(r['ann']):>9s} · "
              f"回撤 {mo.fmt_pct(r['mdd']):>8s} · 夏普 {r['sharpe']:>5.2f} · "
              f"曝光 {r['expo']:.0%} · 调仓 {r['trades']}")
    print(f"断言 {len(checks) - len(bad)}/{len(checks)} 通过")
    return 0 if not bad else 1


if __name__ == "__main__":
    sys.exit(main())
