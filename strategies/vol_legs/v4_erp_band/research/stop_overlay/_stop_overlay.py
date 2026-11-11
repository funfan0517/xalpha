# -*- coding: utf-8 -*-
"""V4 · **ATR 移动止损叠加**的网格与归因（一次性研究，不改策略本体）。

问题：V4 的仓位只由 ERP 的 z 决定，与价格趋势无关。实测它的最大回撤 **-38.96%**
落在 **2021-02-10 → 2024-02-02**（target 平均 88%，一路 100% 硬扛三年慢熊）。
于是检验：**在 z 还没到清仓带之前，先按价格纪律把股票腿砍掉，值不值？**

网格（全部 `erp.run(lag=1)`、场外费率、同一份缓存、不联网）：

- `k`（移动止损 = 峰值 − k×ATR）：3 / 4 / 5 / 6 / 8
- `arm`（浮盈多少 ATR 后才启用）：**0 = 从建仓第一天就生效**（默认） / 4 = 有浮盈后
- **解冻口径**（止损出来后什么时候再买）：
  - `ma`：收盘重回 MA20 / MA60（趋势修复就买回）；
  - `erp`：必须等 **ERP 重新给出一次「买满」**（把控制权彻底还给估值信号）。

结论先行（详见 `_stop_overlay.md`）：`ma` 解冻 = 被反复打脸（每年多 3~7 次往返，年化掉 3~11pp）；
`erp` 解冻 = 回撤砍到 -10.8%~-22.3%，但平均仓位塌到 5%~28%、年化掉到 3.5%~8.2% ——
**它本质是「降暴露 + 只在估值重置后回来」，不是免费的保险**。

产物（本目录）：`_stop_overlay.md` / `_stop_overlay.jsonl` / `_stop_overlay.png`。
机械规则输出，非投资建议。
"""
import importlib.util
import json
import os
import sys

import pandas as pd

try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:  # noqa: BLE001
    pass

_HERE = os.path.dirname(os.path.abspath(__file__))          # .../v4_erp_band/research/stop_overlay
_V4 = os.path.dirname(os.path.dirname(_HERE))               # .../v4_erp_band
_VL = os.path.dirname(_V4)                                  # strategies/vol_legs
for _p in (_V4, _VL):
    if _p not in sys.path:
        sys.path.insert(0, _p)

import atr_stop as S     # noqa: E402
import ladder as L       # noqa: E402


def _load_v4bt():
    """按文件路径加载 v4 的 backtest.py（仓库里有多个同名 backtest，必须隔离）。"""
    spec = importlib.util.spec_from_file_location("v4_bt", os.path.join(_V4, "backtest.py"))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


B = _load_v4bt()

PCT = lambda x: f"{x * 100:+.2f}%"          # noqa: E731
GRID_K = (3.0, 4.0, 5.0, 6.0, 8.0)
GRID_REENTRY = (20, 60)
# README §5 的「仓位刻度 80/维持/20」实测（另一口径，仅作参照）
REF_80_20 = dict(ann=0.0976, mdd=-0.3111)


def run_one(df, bond, **cfg):
    """跑一个止损配置 -> dict(metrics + 止损事件 + 同曝光静态基准)。"""
    s = S.apply_stop(df, **cfg)
    d2 = df.assign(target_stop=s["w_stop"].to_numpy())
    r = B.evaluate(d2, bond, target_col="target_stop")
    m = r["m"]
    avg_w = float(r["w"].mean())
    pe = r["frame"]["tr"].astype("float64")
    same = B.static_bench(bond, pe, w=avg_w)
    ms = same["m"]
    sm = S.summary(s)
    cal = lambda x: (x["ann"] / abs(x["mdd"])) if abs(x["mdd"]) > 1e-9 else 0.0   # noqa: E731
    return dict(cfg=cfg, ann=m["ann"], mdd=m["mdd"], sharpe=m["sharpe"], calmar=cal(m),
                avg_w=avg_w, n_trades=len(r["trades"]), fees=float(r["fees"]),
                same_ann=ms["ann"], same_mdd=ms["mdd"], same_sharpe=ms["sharpe"],
                same_calmar=cal(ms), n_stop=sm["n_hit"], frozen=sm["stopped_share"],
                nav=r["nav"], w=r["w"], stop=s)


def episode_ret(nav, a, b):
    x = nav.loc[a:b]
    return float(x.iloc[-1] / x.iloc[0] - 1.0) if len(x) else float("nan")


def label(c):
    nm = f"k={c['k']:g}"
    if c.get("hard_k") is not None:
        nm += f" +硬止损{c['hard_k']:g}ATR"
    if c["arm"] > 0:
        nm += f"（浮盈{c['arm']:g}ATR后启用）"
    elif c.get("reentry", "ma") == "erp":
        nm += " · 解冻=等ERP再买满"
    else:
        nm += f" · 解冻MA{c['reentry_n']}"
    return nm


def cmp_row(name, r):
    return (f"| {name} | {PCT(r['ann'])} | {PCT(r['mdd'])} | {r['calmar']:.3f} | "
            f"{r['sharpe']:.2f} | {r['avg_w']:.0%} | {r.get('n_trades', '—')} |")


def main():
    df = L.frame()
    bond = B.bond_series("synth")
    base = B.evaluate(df, bond)
    bm = base["m"]
    base_nav = base["nav"]
    base_w = float(base["w"].mean())
    base_cal = bm["ann"] / abs(bm["mdd"])
    base_row = dict(ann=bm["ann"], mdd=bm["mdd"], calmar=base_cal, sharpe=bm["sharpe"],
                    avg_w=base_w, n_trades=len(base["trades"]), same_ann=float("nan"),
                    same_mdd=float("nan"))
    a, b = "2021-02-10", "2024-02-02"          # 基准的最大回撤区间（已实测）

    rows = []
    rows.append(run_one(df, bond, k=6.0, arm=4.0, reentry="ma", reentry_n=20))  # 原 V1-2 式（V1-2 已于 2026-11-10 删除）
    for k in GRID_K:
        for rn in GRID_REENTRY:
            rows.append(run_one(df, bond, k=k, arm=0.0, reentry="ma", reentry_n=rn))
        rows.append(run_one(df, bond, k=k, arm=0.0, reentry="erp", reentry_n=20))
    rows.append(run_one(df, bond, k=6.0, arm=4.0, reentry="ma", reentry_n=20, hard_k=3.0))

    for r in rows:
        r["label"] = label(r["cfg"])
        r["reentry"] = r["cfg"].get("reentry", "ma")
        r["d_ann_pp"] = (r["ann"] - bm["ann"]) * 100
        r["d_mdd_pp"] = (r["mdd"] - bm["mdd"]) * 100          # 正 = 回撤更浅
        r["d_sharpe"] = r["sharpe"] - bm["sharpe"]

    ma_rows = [r for r in rows if r["cfg"]["arm"] == 0.0 and r["reentry"] == "ma"]
    erp_rows = [r for r in rows if r["reentry"] == "erp"]
    # 两条解冻路线分别按卡玛选（卡玛同时惩罚「降收益」与「放回撤」）。
    # ERP 路线额外要求平均仓位 ≥20% —— 否则 k 调小会把策略压成「债券+偶尔满仓」，
    # 卡玛再高也是退化成债券产品（k=3 那档平均仓位只有 5%）。
    erp_usable = [r for r in erp_rows if r["avg_w"] >= 0.20]
    best_ma = max(ma_rows, key=lambda z: z["calmar"])
    best_erp = max(erp_usable or erp_rows, key=lambda z: z["calmar"])
    base_ep = episode_ret(base_nav, a, b)

    L_ = [
        "# V4 · ATR 移动止损叠加：网格与归因",
        "",
        f"> 样本 {base_nav.index[0]:%Y-%m-%d} ~ {base_nav.index[-1]:%Y-%m-%d}"
        f"（{len(base_nav) / 252:.1f} 年）· 场外费率 · `erp.run(lag=1)` · 离线读缓存。",
        "> 生成脚本 `research/stop_overlay/_stop_overlay.py`；机械规则输出，**非投资建议**。",
        "",
        "## 0. 基准（无止损）",
        "",
        "| 年化 | 最大回撤 | 夏普 | 卡玛 | 平均股票仓位 | 调仓次数 | 累计费用 |",
        "|---:|---:|---:|---:|---:|---:|---:|",
        f"| {PCT(bm['ann'])} | {PCT(bm['mdd'])} | {bm['sharpe']:.2f} | {base_cal:.3f} | "
        f"{base_w:.0%} | {len(base['trades'])} | {base['fees']:.1%} |",
        "",
        f"> **最大回撤是 {a} → {b}**，区间内 target 平均 88%（几乎全程 100% 股票），"
        f"沪深300 全收益 {PCT(episode_ret(base_nav, a, b))}。止损要打的靶子是**2021-2024 的慢熊**，"
        "不是 2015（那年 V4 已经在清仓带里）。",
        "",
        "## 1. 网格",
        "",
        "> `同曝光静态` = 把静态股债组合的股票仓位固定在该配置的**平均仓位**上（其余放债券），"
        "用来判断「收益是不是只靠降仓位」。它不是止损的基准（那个差额含 V4 的择时 alpha）。",
        "",
        "| 配置 | 年化 | 最大回撤 | 卡玛 | 夏普 | 平均仓位 | 止损次数 | 冻结占比 | "
        "Δ年化 | Δ回撤 | Δ夏普 | 同曝光静态年化 |",
        "|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|",
    ]
    for r in rows:
        mark = "**" if r in (best_ma, best_erp) else ""
        L_.append(f"| {mark}{r['label']}{mark} | {PCT(r['ann'])} | {PCT(r['mdd'])} | "
                  f"{r['calmar']:.3f} | {r['sharpe']:.2f} | {r['avg_w']:.0%} | {r['n_stop']} | "
                  f"{r['frozen']:.1%} | {r['d_ann_pp']:+.2f}pp | {r['d_mdd_pp']:+.2f}pp | "
                  f"{r['d_sharpe']:+.2f} | {PCT(r['same_ann'])} |")

    L_ += [
        "",
        "## 2. 两条「解冻路线」的对比",
        "",
        "止损只是「什么时候出来」，真正决定成败的是「什么时候回去」。网格里两种口径的结论完全不同：",
        "",
        "### 2.1 趋势修复就买回（`reentry=ma`）—— 被反复打脸",
        "",
        "| 组合 | 年化 | 最大回撤 | 卡玛 | 夏普 | 平均仓位 | 调仓 |",
        "|---|---:|---:|---:|---:|---:|---:|",
        cmp_row("V4 基准（无止损）", base_row),
        cmp_row(f"最好的一档：{best_ma['label']}", best_ma),
    ]
    n_ma_win = sum(1 for r in ma_rows if r["calmar"] > base_cal)
    L_ += [
        "",
        f"> {len(ma_rows)} 个 `ma` 配置里，**{n_ma_win} 个**的卡玛超过基准。"
        "原因：离场后只要价格站回 MA20 就买回，慢熊里这套组合会被反复触发"
        f"（{min(r['n_stop'] for r in ma_rows)}~{max(r['n_stop'] for r in ma_rows)} 次），"
        "每次一进一出就是 1.2% 的场外往返费，还要倒过来追高。",
        "",
        "### 2.2 等 ERP 重新买满（`reentry=erp`）—— 回撤砍下来了，但仓位也砍了",
        "",
        "| 组合 | 年化 | 最大回撤 | 卡玛 | 夏普 | 平均仓位 | 调仓 |",
        "|---|---:|---:|---:|---:|---:|---:|",
        cmp_row("V4 基准（无止损）", base_row),
        cmp_row(f"最好的一档：{best_erp['label']}", best_erp),
        f"| 同曝光静态（股票固定 {best_erp['avg_w']:.0%}） | {PCT(best_erp['same_ann'])} | "
        f"{PCT(best_erp['same_mdd'])} | {best_erp['same_calmar']:.3f} | "
        f"{best_erp['same_sharpe']:.2f} | {best_erp['avg_w']:.0%} | — |",
        f"| 参照：仓位刻度 80/维持/20（README §5，另一口径） | {PCT(REF_80_20['ann'])} | "
        f"{PCT(REF_80_20['mdd'])} | {REF_80_20['ann'] / abs(REF_80_20['mdd']):.3f} | — | — | — |",
        "",
        f"> 关键一步：**同曝光静态只有 {PCT(best_erp['same_ann'])}，而「止损 + 等 ERP 复位」有 "
        f"{PCT(best_erp['ann'])}**（差 {(best_erp['ann'] - best_erp['same_ann']) * 100:+.2f}pp/年）。"
        "也就是说它**不是单纯的降暴露**：出来之后「只在估值重置后回去」这个纪律本身是有价值的；"
        f"但它把平均仓位压到 {best_erp['avg_w']:.0%}、年化压到 {PCT(best_erp['ann'])}，"
        "对「要赚 beta」的人来说是另一种策略。",
        "",
        "## 3. 逐年（基准 vs `erp` 解冻最好那档）",
        "",
        "| 年 | V4 基准 | V4 + ATR 止损 | 差 |",
        "|---|---:|---:|---:|",
    ]
    yb, ys = B.yearly_returns(base_nav), B.yearly_returns(best_erp["nav"])
    for y in yb.index:
        if y in ys.index:
            L_.append(f"| {y} | {PCT(yb[y])} | {PCT(ys[y])} | {(ys[y] - yb[y]) * 100:+.2f}pp |")
    n_better = int(sum(1 for y in yb.index if y in ys.index and ys[y] > yb[y]))

    L_ += ["", f"> 逐年跑赢 {n_better}/{len(yb)} 年。", "",
          "## 4. 止损事件（`erp` 解冻最好那档）", "",
          "> 「解冻」= 等 ERP 重新给出买满信号；若冻结期间 ERP 自己先清仓，冻结也随之结束"
          "（所以短则 1 天、长则数年）。",
          "", "| # | 触发止损 | 解冻 | 冻结交易日 |", "|---:|---|---|---:|"]
    for i, (x, y2, n) in enumerate(S.summary(best_erp["stop"])["episodes"], 1):
        L_.append(f"| {i} | {x:%Y-%m-%d} | {y2:%Y-%m-%d} | {n} |")

    L_ += ["", "## 5. 结论", ""]
    L_ += _conclusions(bm, base_row, best_ma, best_erp, ma_rows, erp_rows, a, b, base_ep, base_cal)

    md = os.path.join(_HERE, "_stop_overlay.md")
    with open(md, "w", encoding="utf-8") as f:
        f.write("\n".join(L_))
    with open(os.path.join(_HERE, "_stop_overlay.jsonl"), "w", encoding="utf-8") as f:
        for r in rows:
            rec = {k: v for k, v in r.items() if k not in ("nav", "w", "stop")}
            f.write(json.dumps(rec, ensure_ascii=False, default=str) + "\n")
    _plot(base_nav, best_ma, best_erp, a, b)

    print("报告:", md)
    print(f"基准 {PCT(bm['ann'])} / 回撤 {PCT(bm['mdd'])} / 夏普 {bm['sharpe']:.2f} "
          f"/ 仓位 {base_w:.0%} / 卡玛 {base_cal:.3f}")
    for r in rows:
        print(f"  {r['label']:26s} 年化 {PCT(r['ann']):>8s} 回撤 {PCT(r['mdd']):>8s} "
              f"卡玛 {r['calmar']:.3f} 夏普 {r['sharpe']:.2f} 仓位 {r['avg_w']:.0%} "
              f"Δ年化 {r['d_ann_pp']:+.2f}pp Δ回撤 {r['d_mdd_pp']:+.2f}pp 止损 {r['n_stop']} 次")


def _conclusions(bm, base_row, best_ma, best_erp, ma_rows, erp_rows, a, b, base_ep, base_cal):
    ep = episode_ret(best_erp["nav"], a, b)
    n_ma_win = sum(1 for r in ma_rows if r["calmar"] > base_cal)
    out = [
        f"1. **先认清靶子**：V4 的 -38.96% 不在 2015（那年它已在清仓带），而在 **{a} → {b} 的三年慢熊**"
        f"（target 平均 88%）。慢熊 = 阴跌 + 频繁反弹，是「趋势型止损最容易被打脸」的形态。",
        f"2. **`ma` 解冻（站回均线就买回）在 V4 上不可用**：{len(ma_rows)} 个配置里 "
        f"{n_ma_win} 个卡玛超过基准，最好的一档 {best_ma['label']} 也只有 "
        f"年化 {PCT(best_ma['ann'])}（{best_ma['d_ann_pp']:+.2f}pp）、回撤 {PCT(best_ma['mdd'])}"
        f"（{best_ma['d_mdd_pp']:+.2f}pp）。紧止损（k=3/4）更糟：止损 {max(r['n_stop'] for r in ma_rows)} 次、"
        "年化个位数 —— 每来回一次就付 1.2% 场外费，还要追高买回。",
        f"3. **`erp` 解冻（等估值重置）确实把回撤砍下来了**：{best_erp['label']} → "
        f"最大回撤 {PCT(bm['mdd'])} → **{PCT(best_erp['mdd'])}**"
        f"（{(best_erp['mdd'] - bm['mdd']) * 100:+.1f}pp），关键区间收益 {PCT(base_ep)} → {PCT(ep)}；"
        f"但年化 {PCT(bm['ann'])} → {PCT(best_erp['ann'])}（{best_erp['d_ann_pp']:+.2f}pp），"
        f"平均仓位 {base_row['avg_w']:.0%} → {best_erp['avg_w']:.0%}。",
        f"4. **但它不是「纯降暴露」**：同平均仓位（{best_erp['avg_w']:.0%}）的静态股债只有 "
        f"{PCT(best_erp['same_ann'])}，止损版有 {PCT(best_erp['ann'])}，"
        f"差 {(best_erp['ann'] - best_erp['same_ann']) * 100:+.2f}pp/年；卡玛 "
        f"{best_erp['calmar']:.3f} vs 同曝光静态 {best_erp['same_calmar']:.3f}、"
        f"vs 基准 {base_cal:.3f}。→ **「出来之后只在估值重置时回去」这条纪律本身有价值**，"
        "代价是把仓位和收益一起砍掉。",
        f"5. **收益/风险的两端摆在那里，选哪端是偏好不是技术**：要收益 → 基准 {PCT(bm['ann'])} / "
        f"{PCT(bm['mdd'])}；要回撤 → {best_erp['label']} {PCT(best_erp['ann'])} / "
        f"{PCT(best_erp['mdd'])}。中间没有免费午餐：`k` 调小会更早离场，但也会把"
        f"「等 ERP 复位」的等待期拉长 —— k=3 那档卡玛最高（{max(r['calmar'] for r in erp_rows):.3f}），"
        f"可平均仓位只剩 {min(r['avg_w'] for r in erp_rows):.0%}、年化 {min(r['ann'] for r in erp_rows) * 100:+.2f}%，"
        "已经退化成「债券 + 偶尔满仓」，不是同一类产品（所以选型把平均仓位 <20% 的档排除了）。",
        "6. **「浮盈才启用移动止损」（V1-2 的口径）在 V4 上不成立**：2021-07 那笔买在下跌途中，"
        "仓位从未浮盈 4ATR，止损永不 arm；这个坑的工程含义是 **arm 必须是粘性开关**"
        "（浮盈够过一次就一直生效），不能每天用「当日价 − 入场价」重算，否则一回落就自动关闭、"
        "止损形同虚设 —— 本脚本第一版正是这样，改掉之后结果才变得有意义。",
        "7. **建议**：`atr_stop.CFG['enabled']` 保持 **False**，不写进自动信号。"
        f"如果**回撤 -39% 是硬约束**，比起改止损，先试 README §5 的「仓位刻度 80/维持/20」"
        f"（{PCT(REF_80_20['ann'])} / {PCT(REF_80_20['mdd'])}，不用加任何新规则）；"
        f"若那条还不够，再考虑 `{best_erp['label']}`（{PCT(best_erp['ann'])} / "
        f"{PCT(best_erp['mdd'])}），并接受「这是一套更保守的策略」而不是「同一个策略加了保险」。",
        "",
        "### 用法建议",
        "",
        "| 项 | 建议 |",
        "|---|---|",
        "| 默认开关 | `atr_stop.CFG['enabled'] = False`；只在回测/研究里显式打开 |",
        f"| 若要启用 | `k={best_erp['cfg']['k']:g}`、`arm=0`（从建仓第一天生效）、"
        "**`reentry='erp'`**（等下一次 ERP 买满），不要用 `ma` |",
        "| 定位 | **回撤保险 / 降暴露开关**，不是收益引擎：它把 V4 从「67% 平均仓位、-39% 回撤」"
        "改造成「28% 平均仓位、-21% 回撤」 |",
        "| 已知边界 | 本包只有收盘价，ATR 是「收盘-收盘」近似（低估 20%~30%）；止损按收盘判定、"
        "次日成交，跳空不保护；`erp` 解冻在长牛里会「永远不回来」（2021-2024 就是），"
        "这是设计而非 bug |",
    ]
    return out


def _plot(base_nav, best_ma, best_erp, a, b):
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    plt.rcParams["font.sans-serif"] = ["Microsoft YaHei", "SimHei", "DejaVu Sans"]
    plt.rcParams["axes.unicode_minus"] = False

    fig, (ax1, ax2) = plt.subplots(2, 1, figsize=(13, 7.5), sharex=True,
                                   gridspec_kw={"height_ratios": [2, 1]})
    series = [(base_nav, "#455a64", "V4 基准（无止损）"),
              (best_erp["nav"].reindex(base_nav.index).ffill(), "#c2185b",
               f"ATR 止损 · {best_erp['label']}"),
              (best_ma["nav"].reindex(base_nav.index).ffill(), "#1e88e5",
               f"ATR 止损 · {best_ma['label']}")]
    for nav, c, lb in series:
        ax1.plot(nav.index, nav, color=c, lw=1.3, label=lb)
    ax1.axvspan(pd.Timestamp(a), pd.Timestamp(b), color="#ffb300", alpha=0.12,
                label=f"基准最大回撤段 {a}~{b}")
    ax1.set_yscale("log")
    ax1.set_title("V4 叠加 ATR 移动止损 · 净值（对数轴）", fontsize=11)
    ax1.set_ylabel("净值")
    ax1.grid(alpha=0.3, which="both")
    ax1.legend(fontsize=9, loc="upper left")

    for nav, c, lb in series:
        dd = nav / nav.cummax() - 1.0
        ax2.plot(dd.index, dd * 100, color=c, lw=1.1, label=lb.split("·")[-1].strip())
    ax2.set_ylabel("回撤 %")
    ax2.grid(alpha=0.3)
    ax2.legend(fontsize=8, loc="lower left")

    fig.tight_layout()
    out = os.path.join(_HERE, "_stop_overlay.png")
    fig.savefig(out, dpi=130, facecolor="white")
    plt.close(fig)
    print("图:", out)


if __name__ == "__main__":
    main()
