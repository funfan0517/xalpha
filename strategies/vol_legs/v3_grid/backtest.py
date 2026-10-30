# -*- coding: utf-8 -*-
"""波动资产 · V3 变种：等金额网格 · 六腿回测。

口径完全跟随 `strategies/vol_legs`（与 v1_ma20 同一套）：
- 池 = `rule.LEG_ORDER` 六腿；价格 = `data.series_for_leg()` 场外**复权净值**；
- T 日盘后按收盘净值判档 → **T+1 净值成交**（`LAG=1`），次日净值回到格线内侧则撤单（`FILL_RULE`）；
- 未投现金按货基 `rule.CASH_ANNUAL` 逐日计息；申赎费按该腿真实执行代码（C 类免申赎、A 类 0.1%/0.5%）。

产出：
- backtest/_v3_grid_bt.md   规则卡 / 逐腿 / 组合层 / 格距·出界·成交口径敏感性 / 压力测试
- backtest/_v3_grid_bt.jsonl

模拟结果，非投资建议。
"""
import json
import os
import sys

import pandas as pd

try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:  # noqa
    pass

_HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, _HERE)

import grid  # noqa: E402

OUTDIR = os.path.join(_HERE, "backtest")
CASH = grid.CASH_DAILY


def cash_curve(idx):
    """货基净值（(1+日利率)^i），用于「空仓部分」与等暴露基准。"""
    return pd.Series([(1.0 + CASH) ** i for i in range(len(idx))], index=idx)


def equal_exposure(px, exposure):
    """等暴露基准：恒定 w 仓位持有该腿 + (1-w) 货基。用来看「网格的择时」值不值。"""
    w = max(0.0, min(1.0, float(exposure)))
    return w * (px / px.iloc[0]) + (1.0 - w) * cash_curve(px.index)


# ---------------------------------------------------------------- 逐腿
def leg_rows(**kw):
    rows = []
    for k in grid.LEGS:
        cfg = grid.rule.LEGS[k]
        px = grid.load(k)
        r = grid.run(px, fee=grid.fees(k), **kw)
        nav, bh = r["nav"], px / px.iloc[0]
        m, b = grid.perf(nav), grid.perf(bh)
        m0 = grid.perf(grid.run(px, fee=(0.0, 0.0), **kw)["nav"])
        ex = equal_exposure(px, r["exposure"])
        me = grid.perf(ex)
        st = grid.trade_stats(r["fills"])
        years = max((px.index[-1] - px.index[0]).days / 365.25, 1e-9)
        rows.append(dict(
            leg=k, name=cfg["name"], role=cfg["role"], band=float(cfg["band"]),
            exec_code=cfg["exec"], start=px.index[0].date().isoformat(),
            end=px.index[-1].date().isoformat(), years=round(years, 2), bars=len(px),
            ann=m["ann"], mdd=m["mdd"], vol=m["vol"], sharpe=m["sharpe"],
            ann_nofee=m0["ann"], fee_drag_pp=(m0["ann"] - m["ann"]) * 100,
            ann_fee=m["ann"],
            bh_ann=b["ann"], bh_mdd=b["mdd"], bh_sharpe=b["sharpe"],
            edge_pp=(m["ann"] - b["ann"]) * 100,
            eq_ann=me["ann"], eq_mdd=me["mdd"], eq_sharpe=me["sharpe"],
            edge_eq_pp=(m["ann"] - me["ann"]) * 100,
            exposure=r["exposure"], fills=len(r["fills"]), sells=st["n"],
            per_year=st["n"] / years, win_rate=st["win_rate"],
            realized=st["realized"], fee_total=st["fee"], turnover=r["turnover"],
            days_sold_out=r["days_sold_out"], days_full=r["days_full"],
            sold_out_pct=r["days_sold_out"] / len(px),
            source=grid.vdata.SOURCE_NOTE.get(k, ""),
        ))
    return rows


# ---------------------------------------------------------------- 组合层
def portfolio_rows(frame, **kw):
    nav, detail = grid.portfolio(frame, fee_by_leg={k: grid.fees(k) for k in frame.columns}, **kw)
    nav_nofee, _ = grid.portfolio(frame, fee_by_leg={k: (0.0, 0.0) for k in frame.columns}, **kw)
    band = sum(frame[k] / frame[k].iloc[0] * grid.W[k] for k in frame.columns)   # 静态带
    eq = {k: 1.0 / len(frame.columns) for k in frame.columns}
    eqw = sum(frame[k] / frame[k].iloc[0] * eq[k] for k in frame.columns)        # 六腿等权
    inv = sum(grid.W[k] * detail[k]["exposure"] for k in detail)                 # 按带加权的平均曝光
    ex = equal_exposure(band, inv)                                               # 等暴露基准
    out = [
        ("V3 网格（按带权 · 含申赎费）", nav, inv),
        ("V3 网格（不计费）", nav_nofee, inv),
        ("等暴露基准（同平均仓位持有静态带）", ex, inv),
        ("静态带（恒定持有）", band, 1.0),
        ("六腿等权（恒定持有）", eqw, 1.0),
    ]
    rows = []
    for name, series, inv_ in out:
        m = grid.perf(series)
        rows.append(dict(name=name, cum=m["cum"], ann=m["ann"], vol=m["vol"],
                         mdd=m["mdd"], sharpe=m["sharpe"], invested=inv_))
    return nav, detail, rows


def sensitivity(frame, key, values, bench=None, **kw):
    """组合层敏感性：逐档重算六腿网格。key ∈ grid_pct / reanchor / fill(lag, rule)。

    bench = 静态带净值；给了就算「等暴露基准」（用该档自己的平均仓位），
    否则不同档之间的平均仓位差异会被误读成「参数优劣」。
    """
    rows = []
    for v in values:
        kw2 = dict(kw)
        label = str(v)
        if key == "fill":
            kw2["lag"] = v[0]
            label = v[1]
            grid_fill = v[2] if len(v) > 2 else None
        else:
            kw2[key] = v
            grid_fill = None
        if key == "grid_pct":
            label = f"{v:.0%}"
        old = grid.FILL_RULE
        if grid_fill is not None:
            grid.FILL_RULE = grid_fill
        try:
            nav, detail = grid.portfolio(
                frame, fee_by_leg={k: grid.fees(k) for k in frame.columns}, **kw2)
        finally:
            grid.FILL_RULE = old
        m = grid.perf(nav)
        fills = sum(len(d["fills"]) for d in detail.values())
        ex = sum(grid.W[k] * detail[k]["exposure"] for k in detail)
        fee = sum(d["fee_total"] for d in detail.values())
        eq_ann = grid.perf(equal_exposure(bench, ex))["ann"] if bench is not None else None
        rows.append(dict(label=label, ann=m["ann"], mdd=m["mdd"], sharpe=m["sharpe"],
                         exposure=ex, fills=fills,
                         fills_per_year=fills / max(len(frame) / 252.0, 1e-9),
                         fee_pp=fee / len(detail) * 100, eq_ann=eq_ann))
    return rows


# ---------------------------------------------------------------- 报告
def main():
    os.makedirs(OUTDIR, exist_ok=True)
    base = dict(grid_pct=grid.GRID_PCT, total_units=grid.TOTAL_UNITS,
                reanchor=grid.REANCHOR, lag=grid.LAG)
    rows = leg_rows(**base)
    frame = grid.common_frame()
    bench = sum(frame[k] / frame[k].iloc[0] * grid.W[k] for k in frame.columns)  # 静态带
    nav, detail, prows = portfolio_rows(frame, **base)

    with open(os.path.join(OUTDIR, "_v3_grid_bt.jsonl"), "w", encoding="utf-8") as f:
        for r in rows:
            f.write(json.dumps({k: (round(v, 4) if isinstance(v, float) else v)
                                for k, v in r.items()}, ensure_ascii=False) + "\n")

    win = f"{frame.index[0].date()} ~ {frame.index[-1].date()}"
    yrs = (frame.index[-1] - frame.index[0]).days / 365.25
    n_eq = sum(1 for r in rows if r["edge_eq_pp"] > 0)
    n_bh = sum(1 for r in rows if r["edge_pp"] > 0)
    stress_base = grid.stress(4724.0, capital=100000.0)
    st = grid.trade_stats([f for d in detail.values() for f in d["fills"]])

    def E(leg, key="edge_eq_pp"):
        return next(r[key] for r in rows if r["leg"] == leg)

    def EX(leg):
        return next(r["exposure"] for r in rows if r["leg"] == leg)

    g_ann, g_inv = prows[0]["ann"], prows[0]["invested"]
    eq_ann = prows[2]["ann"]
    min_eq = min(r["edge_eq_pp"] for r in rows)
    max_eq = max(r["edge_eq_pp"] for r in rows)

    L = [
        "# 波动资产 · V3 变种：等金额网格（六腿逐腿）",
        "> 池与主策略**完全一致**：`rule.LEG_ORDER` 六腿（A500 / 纳指100 / 标普500 / 科创50 / 恒生科技 / 黄金）",
        "> 价格 = 场外**复权净值**（含分红再投 / 汇率 / 跟踪误差）· T 日盘后判档、**T+1 净值成交** · "
        f"空仓按货基 {grid.CASH_ANNUAL:.2%} 计息 · 申赎费按该腿真实代码",
        "",
        "## 1. 规则卡（与 `grid.py` 一一对应）",
        "",
        "| # | 规则 | 参数 / 实现 |",
        "|---|---|---|",
        f"| 1 | 配置 | `P0` 中间价 · `grid_pct={grid.GRID_PCT:.0%}` · `total_units={grid.TOTAL_UNITS}` · "
        f"`init_frac={grid.INIT_FRAC:g}`（= N/2 份）· **每份金额相等** = `capital/N` |",
        "| 2 | 价格线 | `P0 × (1 ± k×grid_pct)`，k=1..N/2（上 5 条 / 下 5 条，共 11 条） |",
        "| 3 | 初始状态 | 现金 = 总资金、持仓 0 → **先买 N/2 份** |",
        "| 4 | 触发 | 跌破下一格 → 买 1 份（现金够 & 未买满）；涨破上一格 → 卖 1 份（有持仓 & 未卖光）；"
        "整数档位判档（向零取整，档位恰在价格线上跳变）+ 单日最多 1 份 |",
        "| 5 | 出界 | 卖光 → **不追高**，必须跌破卖光价 "
        f"{(1 - grid.REANCHOR_DROP):.0%} 才换 P0 重画；买满 → **不加新资金** |",
        f"| 6 | 风控 | 开格前「买满后再跌 {grid.STRESS_DROP:.0%}」压力测试（`grid.stress()`），扛不住就减少总资金 |",
        f"| 7 | 成本 | 每笔扣申赎费；A 类 {grid.rule.FEE_BUY:.2%}/{grid.rule.FEE_SELL:.2%}，C 类 0/0（另计 0.2%/年服务费，未计） |",
        "",
        "> 三处对「极简伪代码」的必要修正（否则算不出净值 / 会重复触发）："
        "①**记账**从「cash ±n·unit」改成**逐批份额记账**（等金额买入 = 每份换 `unit/price` 份额，"
        "卖出走 LIFO，剩余份额即已实现收益）；②**判档**从 `round()` 改成「已跨过几条线」的整数档位"
        "（向零取整）+ 只在新档位成交，"
        "并把「同一价位买了又卖」的纯摩擦挡掉；③**出界**从 `return 'sold_out'` 改成可重画的三档（见 §5）。",
        "",
        "## 2. 逐腿（默认参数：3% / 10 份 / 出界 both / T+1 限价 / 含费）",
        "",
        "| 腿 | 角色 | 样本区间 | 年数 | 年化 | 最大回撤 | 夏普 | 平均仓位 | 卖光天数 | 成交 | 年笔数 | "
        "买入持有年化 | 买入持有回撤 | 年化差 | 等暴露年化 | **等暴露年化差** |",
        "|---|---|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|",
    ]
    for r in sorted(rows, key=lambda z: z["edge_eq_pp"], reverse=True):
        L.append(
            f"| {r['name']} | {r['role']} | {r['start']} ~ {r['end']} | {r['years']:.1f} | "
            f"{grid.fmt_pct(r['ann'])} | {grid.fmt_pct(r['mdd'])} | {r['sharpe']:.2f} | "
            f"{r['exposure']:.0%} | {r['sold_out_pct']:.0%} | {r['fills']} | {r['per_year']:.1f} | "
            f"{grid.fmt_pct(r['bh_ann'])} | {grid.fmt_pct(r['bh_mdd'])} | "
            f"**{r['edge_pp']:+.2f}pp** | {grid.fmt_pct(r['eq_ann'])} | "
            f"**{r['edge_eq_pp']:+.2f}pp** |")

    L += [
        "",
        f"> **绝对收益**：网格跑赢买入持有 **{n_bh}/{len(rows)}** 条腿 —— 单边上涨的腿全输"
        f"（纳指 {E('ndx', 'edge_pp'):+.1f}pp、标普 {E('spx', 'edge_pp'):+.1f}pp、"
        f"黄金 {E('gold', 'edge_pp'):+.1f}pp、A500 {E('a500', 'edge_pp'):+.1f}pp），"
        "因为**卖光后就空仓**（规则卡 §5 的直接后果）。",
        f"> **同等暴露**：按各自平均仓位折算成「恒定仓位持有」后，网格跑赢 **{n_eq}/{len(rows)}** 条腿，"
        f"中位 {pd.Series([r['edge_eq_pp'] for r in rows]).median():+.2f}pp/年 ——"
        f" 增量只出现在**震荡/下跌腿**（科创50 {E('kc50'):+.2f}pp、恒生科技 {E('hkt'):+.2f}pp），"
        f"单边上涨腿是 **{min(E('ndx'), E('spx'), E('gold'), E('a500')):+.2f} ~ "
        f"{max(E('ndx'), E('spx'), E('gold'), E('a500')):+.2f}pp**"
        "（网格在上涨途中卖掉、再也没买回来）。"
        "**这条规则只对均值回归的资产有增量，对趋势资产是负的。**",
        "",
        f"## 3. 组合层（六腿共同窗口 {win} · {yrs:.1f} 年，按 `rule` 目标带权重归一化，满仓 = 40pp）",
        "",
        "| 组合 | 累计 | 年化 | 波动 | 最大回撤 | 夏普 | 平均投入 |",
        "|---|---:|---:|---:|---:|---:|---:|",
    ]
    for r in prows:
        L.append(f"| {r['name']} | {grid.fmt_pct(r['cum'], 1)} | {grid.fmt_pct(r['ann'])} | "
                 f"{grid.fmt_pct(r['vol'])} | {grid.fmt_pct(r['mdd'])} | {r['sharpe']:.2f} | "
                 f"{r['invested']:.0%} |")
    d_ann = (prows[0]["ann"] - prows[3]["ann"]) * 100
    d_eq = (prows[0]["ann"] - prows[2]["ann"]) * 100
    L += [
        "",
        f"> 网格 vs 静态带：年化 {d_ann:+.2f}pp；网格 vs **等暴露基准**：{d_eq:+.2f}pp。"
        f"平均投入仅 {g_inv:.0%}（六腿共同窗口里多数腿在等回落重画，长期空仓）。",
        "",
        f"> ⚠ 这里就是最容易骗自己的地方：**组合层网格（{g_ann:.2%}）反而输给「同平均仓位恒定持有」"
        f"（{eq_ann:.2%}）**，而逐腿却有 {n_eq}/{len(rows)} 条腿跑赢等暴露基准。"
        "原因是网格的曝光是**条件性**的 —— "
        f"它总在最差的腿上满仓（恒生科技 {EX('hkt'):.0%}、科创50 {EX('kc50'):.0%}），"
        f"最好的腿上空仓（纳指 {EX('ndx'):.0%}、标普 {EX('spx'):.0%}），"
        f"于是「平均仓位 {g_inv:.0%}」这个数字背后是**仓位与收益负相关**。"
        "逐腿的超额不能直接加总成组合的超额。",
        "",
        "## 4. 格距敏感性（组合层，含费，其余参数同默认）",
        "",
        "| 格距 | 年化 | 最大回撤 | 夏普 | 平均仓位 | **等暴露年化** | 成交笔数 | 笔数/年（六腿合计） | 费用拖累 |",
        "|---|---:|---:|---:|---:|---:|---:|---:|---:|",
    ]
    pct_rows = sensitivity(frame, "grid_pct", [0.02, 0.03, 0.05, 0.08, 0.10, 0.15],
                           bench=bench, **base)
    for s in pct_rows:
        L.append(f"| {s['label']} | {grid.fmt_pct(s['ann'])} | {grid.fmt_pct(s['mdd'])} | "
                 f"{s['sharpe']:.2f} | {s['exposure']:.0%} | {grid.fmt_pct(s['eq_ann'])} | "
                 f"{s['fills']} | {s['fills_per_year']:.0f} | {s['fee_pp']:+.2f}pp |")
    best = max(pct_rows, key=lambda z: z["ann"])
    worst = min(pct_rows, key=lambda z: z["ann"])
    d_first = (pct_rows[0]["ann"] - pct_rows[0]["eq_ann"]) * 100
    d_last = (pct_rows[-1]["ann"] - pct_rows[-1]["eq_ann"]) * 100
    L += [
        "",
        f"> 格子越小摩擦越重：2% 格距的成交笔数是 15% 的 "
        f"{pct_rows[0]['fills'] / max(pct_rows[-1]['fills'], 1):.1f} 倍，"
        f"费用拖累从 {pct_rows[-1]['fee_pp']:+.2f}pp 升到 {pct_rows[0]['fee_pp']:+.2f}pp。",
        f"> **每一档都跑输自己的等暴露基准**（2%：{d_first:+.2f}pp，15%：{d_last:+.2f}pp）。"
        f"年化在格距上**不单调**（最差 {worst['label']} {worst['ann']:+.2%}、最好 {best['label']} "
        f"{best['ann']:+.2%}，中间还夹着 8% > 10% 的倒挂），说明这些差异主要是"
        "**平均仓位 + 样本噪声**，不是「找到了最优格子」。"
        "**格距不是收益来源，它只决定「平均仓位多少 + 交多少过路费」。**",
        "",
        "## 5. 出界规则的代价（组合层，含费，3% / 10 份）",
        "",
        "| 出界处理 | 年化 | 最大回撤 | 夏普 | 平均仓位 | 成交笔数 |",
        "|---|---:|---:|---:|---:|---:|",
    ]
    mode_label = {
        "off": "不重画（规则卡原样：卖光即停）",
        "period": "每 250 交易日重画 P0",
        "pullback": "卖光后回落 10% 才重画（默认·不追高）",
        "both": "两者都开（**默认**）",
        "rebase": "卖光后立刻重画（= 追高，规则卡禁止）",
    }
    mode_rows = sensitivity(frame, "reanchor", ["off", "period", "pullback", "both", "rebase"], **base)
    for s in mode_rows:
        L.append(f"| {mode_label.get(s['label'], s['label'])} | {grid.fmt_pct(s['ann'])} | "
                 f"{grid.fmt_pct(s['mdd'])} | {s['sharpe']:.2f} | {s['exposure']:.0%} | "
                 f"{s['fills']} |")
    r_off = mode_rows[0]["ann"]
    r_reb = mode_rows[-1]["ann"]
    off_ex = mode_rows[0]["exposure"]
    reb_ex = mode_rows[-1]["exposure"]
    L += [
        "",
        f"> 「卖光即停」与「立刻追高重开」的年化差 = **{(r_reb - r_off) * 100:+.2f}pp**"
        f"（平均仓位 {off_ex:.0%} → {reb_ex:.0%}）。差值几乎全部来自**仓位**而非格子："
        "追高重开在长牛资产上确实多赚，但它把网格退化成「高位不断重开」，与规则卡 §5 的初衷相反 —— "
        "所以默认取 `both`（只在跌破卖光价后才重开）。",
        f"> **规则卡 §5 的代价就在这里**：`off`（卖光即停）{r_off:.2%}、`both`（默认）"
        f"{mode_rows[3]['ann']:.2%}，而 `rebase`（追高）{r_reb:.2%} —— "
        "差额就是在「不追高」这条纪律上让渡掉的。"
        "这不是参数问题，是规则卡自己写死的取舍（编码注意第 1 条：一路涨会卖光踏空）。",
        "",
        "## 6. 成交口径与摩擦（组合层，含费）",
        "",
        "| 成交口径 | 年化 | 最大回撤 | 成交笔数 |",
        "|---|---:|---:|---:|",
    ]
    fill_rows = sensitivity(frame, "fill",
                            [(0, "lag=0 当日收盘成交（前视上界）", "market"),
                             (1, "lag=1 限价挂单（默认·场外）", "limit"),
                             (1, "lag=1 无条件按次日净值成交", "market")], **base)
    for s in fill_rows:
        L.append(f"| {s['label']} | {grid.fmt_pct(s['ann'])} | {grid.fmt_pct(s['mdd'])} | "
                 f"{s['fills']} |")
    f0, f1, f2 = fill_rows[0]["ann"], fill_rows[1]["ann"], fill_rows[2]["ann"]
    L += [
        "",
        "> `lag=0` = 当日收盘价成交（**前视**，只作上界）；`lag=1 + limit` = 次日净值仍在格线外侧"
        "才成交（默认，最贴近场外）；`lag=1 + market` = 次日净值无条件成交 —— "
        "它会产生「同一价位买了又卖」的纯摩擦，笔数虚高。",
        f"> 两个反直觉的读数：①**前视反而更差**（{f0:.2%} vs 默认 {f1:.2%}）—— "
        "网格的收益主要来自成交价与格线的距离，早一天成交并不必然多赚，这部分差异属于路径噪声；"
        f"②**真正的时滞成本在 market 口径**（{f2:.2%}，比默认低 {(f1 - f2) * 100:.2f}pp，"
        f"笔数 {fill_rows[2]['fills']} vs {fill_rows[1]['fills']}）—— "
        "无条件补票会把「价格已经回头」的那一单也吃掉。这就是为什么默认要限价撤单。",
        "",
        "## 7. 压力测试（规则卡 §6：开格前必做）",
        "",
        f"买满路径 = P0 买 N/2 份、每跌一格补 1 份，最后一格 = `P0×(1-(N-N/2)×g)`，再从该格再跌 "
        f"{grid.STRESS_DROP:.0%}。下表的「额度亏损」= 该腿**额度**（不是总资产）的亏损比例：",
        "",
        "| 格距 | 买满价（相对 P0） | 压力后价格 | 平均成本 | **额度亏损** | A500 5pp 额度的总资产损失 | 美股/黄金 10pp 额度的总资产损失 |",
        "|---|---:|---:|---:|---:|---:|---:|",
    ]
    for g in (0.02, 0.03, 0.05, 0.08, 0.10):
        s = grid.stress(100.0, grid_pct=g, capital=100.0)
        L.append(f"| {g:.0%} | {s['bottom']:.1f} | {s['worst']:.1f} | {s['avg_cost']:.1f} | "
                 f"**{s['loss_pct']:.1%}** | {s['loss_pct'] * 5:.1f}pp | "
                 f"{s['loss_pct'] * 10:.1f}pp |")
    L += [
        "",
        f"> 关键数字：3% / 10 份的网格**买满后再跌 36%，亏的不是 36% 而是 "
        f"{stress_base['loss_pct']:.1%}** —— 因为补仓价在下跌途中，平均成本远高于最后一格。"
        f"按 A500 的 5pp 额度算，这一击要吃掉总资产的 **{stress_base['loss_pct'] * 5:.1f}pp**。",
        f"> 反推额度（`grid.stress_budget`）：若你只肯让某条腿亏 3pp of 总资产，则额度上限 = "
        f"3 / {stress_base['loss_pct']:.2f} = **{grid.stress_budget(stress_base['loss_pct'], 3.0):.1f}pp**，"
        "而不是拍脑袋给 10pp。",
        "",
        "## 8. 结论（五条）",
        "",
        f"1. **它只对均值回归的腿有增量**：同暴露超额科创50 {E('kc50'):+.2f}pp、恒生科技 "
        f"{E('hkt'):+.2f}pp，而纳指 {E('ndx'):+.2f}pp、标普 {E('spx'):+.2f}pp、"
        f"黄金 {E('gold'):+.2f}pp、A500 {E('a500'):+.2f}pp —— 中位 "
        f"{pd.Series([r['edge_eq_pp'] for r in rows]).median():+.2f}pp ≈ 打平。"
        "**在单边上涨的资产上，网格的每一次卖出都是把仓位永久让渡出去。**",
        f"2. **规则卡 §5 决定了 90% 的结局**：3% / 10 份的上空只有 5 格 = +15%，"
        f"所以纳指/标普/黄金/A500 有 **{max(r['sold_out_pct'] for r in rows):.0%}** 的交易日处于「卖光」状态。"
        f"「不追高」= 长期空仓（默认 {g_ann:.2%}），「追高重开」= {mode_rows[-1]['ann']:.2%} —— "
        f"这 {(mode_rows[-1]['ann'] - mode_rows[0]['ann']) * 100:.1f}pp 的差额是规则的**取舍**，"
        "不是能调出来的参数。要真跑长牛资产，就得允许换 P0。",
        f"3. **组合层不能靠逐腿超额加总**：网格 {g_ann:.2%} vs 同平均仓位持有 {eq_ann:.2%}"
        f"（{(g_ann - eq_ann) * 100:+.2f}pp），因为曝光是被「跌出来的」——"
        "它在输家上满仓、在赢家上空仓。"
        "**网格赚的钱是「同一仓位下的成本优势」，输的钱是「仓位配错了腿」。**",
        f"4. **摩擦是硬成本**：默认口径下六腿合计 {st['n_sell']} 次卖出，费用 "
        f"{st['fee']:.1%}（≈ 平均每条腿 1% 的额度，{yrs:.1f} 年）；"
        f"格距 3%→2% 费用拖累从 {pct_rows[1]['fee_pp']:+.2f}pp 升到 {pct_rows[0]['fee_pp']:+.2f}pp，"
        f"而年化只差 {(pct_rows[0]['ann'] - pct_rows[1]['ann']) * 100:+.2f}pp（噪声量级）—— "
        "**缩格距没换来更聪明的成交，只换来更多过路费**。",
        "5. **正确的用法是「带内的执行细则」，不是替代仓位带**：主策略（v1.3）决定某条腿该有多少 pp，"
        "网格只负责在这条腿内部按 3% 的格子分批买、分批卖；单腿额度必须先用 §7 的压力测试反推"
        f"（3% / 10 份买满后再跌 36% 亏 {stress_base['loss_pct']:.1%} 的额度，不是 36%），"
        "而不是直接给 10pp。另外：**先模拟盘跑一遍再上小资金**（规则卡最后一条）。",
        "",
        "## 9. 已知局限",
        "",
        "- **起始点即 P0**：回测的 P0 = 样本首根净值，样本起点换了结果就变（README §7 有说明）；"
        "实盘请用 `daily/_daily_scan.py --p0 leg=价格 --since 开格日` 显式给中间价与开格日。",
        "- **C 类销售服务费（约 0.2%/年）未计**：A500 / 科创50 / 恒生科技 三条 C 类腿的扣费后数字偏乐观。",
        "- **桥接段**：A500（`000051`）、标普（`050025`）的早期样本是同类老基金，只用于撑样本。",
        "- **未计**：大额限购、QDII 额度关闭顺延、赎回确认时滞（T+1~T+2）、分红除权日的净值跳空。",
        "- **组合层只有共同窗口**（受恒生科技联接 012805 于 2021-08 成立限制），"
        f"{yrs:.1f} 年样本且是「A股熊转牛 + 黄金大牛 + 港股深跌 + 美股长牛」的特定组合。",
        "",
        "> **模拟结果，非投资建议。**",
    ]

    path = os.path.join(OUTDIR, "_v3_grid_bt.md")
    with open(path, "w", encoding="utf-8") as f:
        f.write("\n".join(L))

    print("报告:", path)
    print("结果:", os.path.join(OUTDIR, "_v3_grid_bt.jsonl"))
    for r in sorted(rows, key=lambda z: z["edge_eq_pp"], reverse=True):
        print(f"  {r['name']:8s} 年化 {grid.fmt_pct(r['ann']):>9s} vs BH {grid.fmt_pct(r['bh_ann']):>9s} "
              f"vs 等暴露 {grid.fmt_pct(r['eq_ann']):>9s} | 回撤 {grid.fmt_pct(r['mdd']):>9s} | "
              f"曝光 {r['exposure']:.0%} | {r['fills']:3d} 笔 | 卖光 {r['sold_out_pct']:.0%}")
    for r in prows:
        print(f"  [组合] {r['name']:34s} 年化 {grid.fmt_pct(r['ann']):>9s} · "
              f"回撤 {grid.fmt_pct(r['mdd']):>9s} · 夏普 {r['sharpe']:.2f} · 投入 {r['invested']:.0%}")


if __name__ == "__main__":
    main()
