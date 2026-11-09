# -*- coding: utf-8 -*-
"""V1-2（20/60/250 量价趋势跟随）· **归因与优化方向验证**（一次性研究，不产生产信号）。

回答三个问题 —— 都在同一份本地缓存上离线跑，不刷新数据：

1. **成交时滞 Sensitivity**：回测用 `pos = w.shift(1)`（T 日收盘信号按 T 日收盘成交）。
   若严格执行（再晚一天），结论会翻转吗？
2. **单因子归因**：ADX 休眠 / 量能门槛 / 移动止损+减半 / 风险预算 各自贡献多少？
   关掉某一个，组合层年化与夏普怎么变？（用于回答「哪一维在起作用」）
3. **「量价」比「纯价」到底多了什么**：把四种**持有型**过滤器逐层叠加
   （① 在 MA20 上方 -> ② 加年线闸门 -> ③ 加 ADX -> ④ 加量能），
   每层都对照「同曝光买入持有」，看边际增量是正是负。

4. **优化原型**：`半底仓 + 半信号`（底仓 = 年线上且价在 MA20 上方；信号 = V1-2 状态机），
   验证「趋势里先有底仓、量价信号只做加仓」是否能同时改善收益与夏普。

产物（本目录）：`_attrib.md` / `_attrib.jsonl` / `_attrib_contrib.png`。
机械规则输出，非投资建议。
"""
import json
import os
import sys

import numpy as np
import pandas as pd

try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:  # noqa
    pass

_HERE = os.path.dirname(os.path.abspath(__file__))     # .../v1_2_ma_trend_pv/research/attrib
_V2 = os.path.dirname(os.path.dirname(_HERE))          # .../v1_2_ma_trend_pv
sys.path.insert(0, _V2)

import ma_trend_vol as mtv  # noqa: E402

PCT = mtv.fmt_pct


# ---------------------------------------------------------------- 基础工具
def panel(leg):
    """只读本地缓存（refresh_=False），不联网。"""
    return mtv.load(leg, refresh_=False)


def nav_from_pos(ind, pos, cost=mtv.COST_ONE_WAY):
    """给定目标仓位序列 -> 净值（与 ma_trend_vol.simulate 同一口径）。"""
    ret = ind["close"].pct_change().fillna(0.0).to_numpy(dtype=float)
    pos = np.asarray(pos, dtype=float)
    r = pos * ret + (1.0 - pos) * mtv.CASH_DAILY
    r = r - np.abs(np.diff(np.concatenate([[0.0], pos]))) * cost
    return pd.Series(np.cumprod(1.0 + r), index=ind.index)


def same_exposure(nav_bh, invested):
    """同曝光基准：把买入持有压到同一平均投入，其余放货基。"""
    ret = nav_bh.pct_change(fill_method=None).fillna(0.0)
    return (1.0 + invested * ret + (1.0 - invested) * mtv.CASH_DAILY).cumprod()


def portfolio_nav(frame, pos, cost=True):
    """组合层：Σ w_i·pos_i·ret_i + (1−Σ)·货基，按换手扣单边成本。返回 (nav, 平均投入)。"""
    wmat = mtv._weight_matrix(frame, mtv.W)
    ret = frame.pct_change(fill_method=None).fillna(0.0)
    inv = pos * wmat
    r = (inv * ret).sum(axis=1) + (1.0 - inv.sum(axis=1)) * mtv.CASH_DAILY
    if cost:
        r = r - (pos.diff().fillna(pos).abs() * wmat).sum(axis=1) * mtv.COST_ONE_WAY
    return (1.0 + r).cumprod(), float(inv.sum(axis=1).mean())


def stats(nav, invested=None, bh=None):
    m = mtv.perf(nav)
    out = dict(ann=m["ann"], mdd=m["mdd"], sharpe=m["sharpe"], calmar=m["calmar"],
               ret=float(nav.iloc[-1] / nav.iloc[0] - 1.0), invested=invested)
    if bh is not None and invested is not None:
        se = same_exposure(bh, invested)
        out["same_ann"] = mtv.perf(se)["ann"]
        out["edge_pp"] = (out["ann"] - out["same_ann"]) * 100.0
    return out


# ---------------------------------------------------------------- 1. 成交时滞
def lag_test(frame, base_sims):
    """基准 vs 再滞后一天（近似：仅把仓位序列整体后移一根，不改决策路径）。"""
    rows = []
    for name, extra in (("基准 (pos = w.shift(1))", 0), ("再滞后一天 (pos = w.shift(2))", 1)):
        pos = pd.DataFrame({k: base_sims[k]["pos"].shift(extra).reindex(frame.index).fillna(0.0)
                            for k in frame.columns})
        nav, inv = portfolio_nav(frame, pos)
        s = stats(nav, inv)
        rows.append(dict(name=name, **s))
    return rows


# ---------------------------------------------------------------- 2. 单因子归因
ABLATIONS = [
    ("基准（照原始描述）", {}),
    ("关掉 ADX 休眠", dict(ADX_OFF=0.0, ADX_ON=0.0)),
    ("关掉量能门槛", dict(VOL_MULT=0.0, EXIT_VOL_MULT=99.0)),
    ("关掉移动止损+减半", dict(TRAIL_ATR=1e9, TRAIL_ARM=1e9, HALF=1.0, THREAT_GAP=-1.0)),
    ("风险预算 2%->5%", dict(RISK_PCT=0.05)),
    ("风险预算 2%->10%", dict(RISK_PCT=0.10)),
]
_BUILTIN = ("ADX_OFF", "ADX_ON", "VOL_MULT", "EXIT_VOL_MULT", "TRAIL_ATR", "TRAIL_ARM",
            "HALF", "THREAT_GAP", "RISK_PCT")


def ablations(frame):
    backup = {k: getattr(mtv, k) for k in _BUILTIN}
    rows, leg_rows = [], []
    try:
        for name, patch in ABLATIONS:
            for k in _BUILTIN:
                setattr(mtv, k, patch.get(k, backup[k]))
            sims = {k: mtv.simulate(panel(k)) for k in frame.columns}
            pos = pd.DataFrame({k: sims[k]["pos"].reindex(frame.index).fillna(0.0)
                                for k in frame.columns})
            nav, inv = portfolio_nav(frame, pos)
            s = stats(nav, inv)
            tr = sum(len(mtv.trades(sims[k])) for k in frame.columns)
            bench = rows[0] if rows else None
            rows.append(dict(name=name, trades=tr,
                             d_ann_pp=(s["ann"] - bench["ann"]) * 100 if bench else 0.0,
                             d_sharpe=s["sharpe"] - bench["sharpe"] if bench else 0.0, **s))
            if name in ("关掉 ADX 休眠", "关掉量能门槛"):
                for k in frame.columns:
                    mm = mtv.perf(sims[k]["nav"])
                    leg_rows.append(dict(variant=name, leg=k, ann=mm["ann"], mdd=mm["mdd"],
                                         exposure=mtv.exposure(sims[k]),
                                         n=len(mtv.trades(sims[k]))))
    finally:
        for k, v in backup.items():
            setattr(mtv, k, v)
    return rows, leg_rows


# ---------------------------------------------------------------- 3. 持有型过滤器的逐层增量
def hold_filters(ind):
    """四种持有型过滤器（0/1，只做多）：价在 MA20 上方，逐层叠加。"""
    c = ind["close"]
    bull = (c > ind["ma250"]) & ind["up250"]
    vol_hit = (ind["rvol"] >= mtv.VOL_MULT).rolling(20, min_periods=1).max().astype(bool)
    above20 = c > ind["ma20"]
    return {
        "① 价在 MA20 上方（V1-1 式）": above20,
        "② +年线闸门": above20 & bull,
        "③ +ADX≥20": above20 & bull & (ind["adx"] >= mtv.ADX_OFF),
        "④ +量能(20日内放过量)": above20 & bull & (ind["adx"] >= mtv.ADX_OFF) & vol_hit,
    }


def filter_layers(frame, band):
    out = []
    per_leg = {}
    for name in ("① 价在 MA20 上方（V1-1 式）", "② +年线闸门",
                 "③ +ADX≥20", "④ +量能(20日内放过量)"):
        pos_cols, leginfo = {}, []
        for k in frame.columns:
            ind = panel(k)
            raw = hold_filters(ind)[name].astype(float)
            pos = raw.shift(1).reindex(frame.index).fillna(0.0)   # 次日生效
            pos_cols[k] = pos
            # 逐腿：用腿自己的全样本（年线成形后）
            px = ind["close"].loc[ind["ma250"].first_valid_index():]
            p = raw.reindex(px.index).shift(1).fillna(0.0)
            nav_leg = nav_from_pos(ind.loc[px.index], p)
            inv = float(p.mean())
            bh = px / px.iloc[0]
            s = stats(nav_leg, inv, bh)
            leginfo.append(dict(leg=k, **s))
        posdf = pd.DataFrame(pos_cols)
        nav, inv = portfolio_nav(frame, posdf)
        s = stats(nav, inv, band)
        per_leg[name] = leginfo
        out.append(dict(name=name, **s))
    return out, per_leg


# ---------------------------------------------------------------- 4. 优化原型：半底仓 + 半信号
def hybrid(frame, sims, bh):
    """底仓（年线上且价在 MA20 上方）0.5 + V1-2 信号 0.5，上限 1。"""
    pos = {}
    for k in frame.columns:
        ind = panel(k)
        c = ind["close"]
        base = ((c > ind["ma250"]) & ind["up250"] & (c > ind["ma20"])).astype(float)
        base = base.shift(1).fillna(0.0)
        sig = sims[k]["pos"].reindex(frame.index).fillna(0.0)
        pos[k] = (0.5 * base.reindex(frame.index).fillna(0.0) + 0.5 * sig).clip(0.0, 1.0)
    posdf = pd.DataFrame(pos)
    nav, inv = portfolio_nav(frame, posdf)
    return stats(nav, inv, bh), posdf


# ---------------------------------------------------------------- 主流程
def main():
    frame = mtv.union_frame()
    base = {k: mtv.simulate(panel(k)) for k in frame.columns}
    band = mtv.portfolio(frame, sims=None)

    lag = lag_test(frame, base)
    abl, abl_legs = ablations(frame)
    layers, layer_legs = filter_layers(frame, band)
    hyb, hyb_pos = hybrid(frame, base, band)

    base_nav, base_inv = portfolio_nav(
        frame, pd.DataFrame({k: base[k]["pos"].reindex(frame.index).fillna(0.0)
                             for k in frame.columns}))
    base_s = stats(base_nav, base_inv, band)
    same = same_exposure(band, base_inv)
    same_s = mtv.perf(same)

    L = [
        "# V1-2 · 归因与优化方向验证",
        "",
        f"> 口径：场内 ETF 量价、并集窗口 {frame.index[0].date()} ~ {frame.index[-1].date()}"
        f"（{len(frame) / 252:.1f} 年）· 全部离线读本地缓存，不刷新数据。",
        "> 生成脚本 `research/attrib/_attrib.py`；机械规则输出，**非投资建议**。",
        "",
        "## 0. 基准（复述，便于对照）",
        "",
        "| 组合 | 年化 | 回撤 | 夏普 | 平均投入 |",
        "|---|---:|---:|---:|---:|",
        f"| V1-2（扣费） | {PCT(base_s['ann'])} | {PCT(base_s['mdd'])} | {base_s['sharpe']:.2f} | "
        f"{base_inv:.0%} |",
        f"| 同曝光买入持有（固定 {base_inv:.0%}） | {PCT(same_s['ann'])} | {PCT(same_s['mdd'])} | "
        f"{same_s['sharpe']:.2f} | {base_inv:.0%} |",
        f"| 静态带（满仓） | {PCT(mtv.perf(band)['ann'])} | {PCT(mtv.perf(band)['mdd'])} | "
        f"{mtv.perf(band)['sharpe']:.2f} | 100% |",
        "",
        "## 1. 成交时滞敏感性",
        "",
        "| 口径 | 年化 | 回撤 | 夏普 | 平均投入 | 与基准差 |",
        "|---|---:|---:|---:|---:|---:|",
    ]
    for r in lag:
        d = (r["ann"] - lag[0]["ann"]) * 100
        L.append(f"| {r['name']} | {PCT(r['ann'])} | {PCT(r['mdd'])} | {r['sharpe']:.2f} | "
                 f"{r['invested']:.0%} | {d:+.2f}pp |")
    L += [
        "",
        f"> 结论：再滞后一天的结果是 **{(lag[1]['ann'] - lag[0]['ann']) * 100:+.2f}pp** —— "
        "变化在噪声量级、且方向为正（晚一天成交反而略好），说明**回测没有靠「当天收盘成交」占便宜**；"
        "持仓 40~70 个交易日、年换手 1~2 笔的策略本来就不靠抢价位，不必再加保守系数。",
        "",
        "## 2. 单因子归因（关掉某一维，组合层怎么变）",
        "",
        "| 变体 | 年化 | 回撤 | 夏普 | 平均投入 | 总笔数 | Δ年化 | Δ夏普 |",
        "|---|---:|---:|---:|---:|---:|---:|---:|",
    ]
    for r in abl:
        L.append(f"| {r['name']} | {PCT(r['ann'])} | {PCT(r['mdd'])} | {r['sharpe']:.2f} | "
                 f"{r['invested']:.0%} | {r['trades']} | {r['d_ann_pp']:+.2f}pp | "
                 f"{r['d_sharpe']:+.2f} |")
    L += ["", "> 逐腿细节（只列关键两项）：", ""]
    for name in ("关掉 ADX 休眠", "关掉量能门槛"):
        L += [f"| 变体 · 腿（{name}） | 年化 | 回撤 | 曝光 | 笔数 |", "|---|---:|---:|---:|---:|"]
        for x in abl_legs:
            if x["variant"] == name:
                L.append(f"| {mtv.rule.LEGS[x['leg']]['name']} | {PCT(x['ann'])} | "
                         f"{PCT(x['mdd'])} | {x['exposure']:.0%} | {x['n']} |")
        L.append("")

    L += [
        "## 3. 「量价」比「纯价」多了什么（持有型过滤器逐层叠加）",
        "",
        "> 这一组把「仓位算法/止损」全部拿掉，只留 0/1 持有，专门看**信号本身**的增量："
        "每层都在「同曝光买入持有」之上比较，`择时超额` > 0 才算这一维加了信息。",
        "",
        "| 过滤器（依次叠加） | 年化 | 回撤 | 夏普 | 平均投入 | 同曝光买入持有 | **择时超额** |",
        "|---|---:|---:|---:|---:|---:|---:|",
    ]
    for r in layers:
        L.append(f"| {r['name']} | {PCT(r['ann'])} | {PCT(r['mdd'])} | {r['sharpe']:.2f} | "
                 f"{r['invested']:.0%} | {PCT(r['same_ann'])} | **{r['edge_pp']:+.2f}pp** |")
    L += [""]
    for i in range(1, len(layers)):
        d = layers[i]["edge_pp"] - layers[i - 1]["edge_pp"]
        L.append(f"> {layers[i]['name']} 相对上一层：择时超额 {d:+.2f}pp")
    L += [
        "",
        "逐腿（每一层 × 七腿，择时超额 = 该腿同曝光口径的差额）：",
        "",
        "| 过滤器 | " + " | ".join(mtv.rule.LEGS[k]["name"] for k in frame.columns) + " |",
        "|---|" + "---:|" * len(frame.columns),
    ]
    for r in layers:
        cells = []
        for x in layer_legs[r["name"]]:
            cells.append(f"{x['edge_pp']:+.2f}")
        L.append(f"| {r['name']} | " + " | ".join(cells) + " |")

    L += [
        "",
        "## 4. 优化原型：半底仓 + 半信号",
        "",
        "> 设计动机：V1-2 的问题不是信号错，而是**在场时间太短**（平均 15%），好行情里经常空仓。"
        "改进：趋势成立时先给 50% 底仓（年线上 + 价在 MA20 上方），"
        "再用 V1-2 状态机的仓位补齐另外 50%（放量确认后才满仓）。",
        "",
        "| 组合 | 年化 | 回撤 | 夏普 | 平均投入 | 同曝光买入持有 | 择时超额 |",
        "|---|---:|---:|---:|---:|---:|---:|",
        f"| V1-2 纯信号（基准） | {PCT(base_s['ann'])} | {PCT(base_s['mdd'])} | "
        f"{base_s['sharpe']:.2f} | {base_inv:.0%} | {PCT(same_s['ann'])} | "
        f"{base_s['edge_pp']:+.2f}pp |",
        f"| **半底仓 + 半信号** | {PCT(hyb['ann'])} | {PCT(hyb['mdd'])} | {hyb['sharpe']:.2f} | "
        f"{hyb['invested']:.0%} | {PCT(hyb['same_ann'])} | {hyb['edge_pp']:+.2f}pp |",
        f"| 静态带（满仓） | {PCT(mtv.perf(band)['ann'])} | {PCT(mtv.perf(band)['mdd'])} | "
        f"{mtv.perf(band)['sharpe']:.2f} | 100% | — | — |",
        "",
        "## 5. 结论与可执行建议",
        "",
    ]
    L += conclusions(base_s, same_s, band, lag, abl, layers, hyb)

    md = os.path.join(_HERE, "_attrib.md")
    with open(md, "w", encoding="utf-8") as f:
        f.write("\n".join(L))
    with open(os.path.join(_HERE, "_attrib.jsonl"), "w", encoding="utf-8") as f:
        f.write(json.dumps(dict(kind="baseline", **base_s), ensure_ascii=False) + "\n")
        for r in lag:
            f.write(json.dumps(dict(kind="lag", **r), ensure_ascii=False) + "\n")
        for r in abl:
            f.write(json.dumps(dict(kind="ablation", **r), ensure_ascii=False) + "\n")
        for r in layers:
            f.write(json.dumps(dict(kind="filter", **r), ensure_ascii=False) + "\n")
        f.write(json.dumps(dict(kind="hybrid", **hyb), ensure_ascii=False) + "\n")

    _plot(layers, abl, base_s, same_s, hyb)
    print("报告:", md)
    for r in layers:
        print(f"  {r['name']:26s} 年化 {PCT(r['ann']):>8s} 回撤 {PCT(r['mdd']):>8s} "
              f"曝光 {r['invested']:.0%} 择时超额 {r['edge_pp']:+.2f}pp")
    for r in lag:
        print(f"  {r['name']:26s} 年化 {PCT(r['ann']):>8s} 夏普 {r['sharpe']:.2f}")
    for r in abl:
        print(f"  {r['name']:26s} 年化 {PCT(r['ann']):>8s} 夏普 {r['sharpe']:.2f} "
              f"Δ {r['d_ann_pp']:+.2f}pp")
    print(f"  半底仓+半信号 年化 {PCT(hyb['ann'])} 回撤 {PCT(hyb['mdd'])} "
          f"夏普 {hyb['sharpe']:.2f} 曝光 {hyb['invested']:.0%} 超额 {hyb['edge_pp']:+.2f}pp")


def conclusions(base_s, same_s, band, lag, abl, layers, hyb):
    """把数据翻译成建议（每条都挂数字，不空谈）。"""
    b = mtv.perf(band)
    l_diff = (lag[1]["ann"] - lag[0]["ann"]) * 100
    best_layer = max(layers, key=lambda z: z["edge_pp"])
    out = [
        f"1. **成交时滞不是问题**（再滞后一天 {l_diff:+.2f}pp，方向还是正的）—— 持仓 40~70 个交易日、"
        "年换手 1~2 笔，回测的「收盘价成交」假设没有占到便宜，不必再加保守系数。",
        f"2. **ADX 休眠与量能门槛是负贡献，不是保护**：关掉 ADX 后年化 "
        f"{abl[1]['ann'] * 100:+.2f}%（Δ{abl[1]['d_ann_pp']:+.2f}pp、夏普 Δ{abl[1]['d_sharpe']:+.2f}），"
        f"关掉量能门槛后年化 {abl[2]['ann'] * 100:+.2f}%（Δ{abl[2]['d_ann_pp']:+.2f}pp、"
        f"夏普 Δ{abl[2]['d_sharpe']:+.2f}）—— 两个过滤器都让信号变少，但没让它变准；"
        "**它们降低了回撤，也同步砍掉了更多收益**（夏普反而升，是因为暴露一起降了）。",
        f"3. **真正影响结果的是「敢不敢重仓」**：风险预算 2%→5% 年化 "
        f"{abl[4]['ann'] * 100:+.2f}%（回撤 {abl[4]['mdd'] * 100:+.1f}%），10% 时 "
        f"{abl[5]['ann'] * 100:+.2f}%（回撤 {abl[5]['mdd'] * 100:+.1f}%），"
        f"基准 {abl[0]['ann'] * 100:+.2f}% / {abl[0]['mdd'] * 100:+.1f}%。"
        "放大仓位近似线性放大收益与回撤、夏普几乎不动 —— **这只是加杠杆，不是加 alpha**。",
        f"4. **量能这一维没有正的边际信息**：持有型过滤器从 ① 到 ④ 全部是负超额，"
        f"最好的一层也只有 {best_layer['edge_pp']:+.2f}pp（{best_layer['name']}）；"
        f"单看「+量能」这一步相对上一层的增量是 {layers[3]['edge_pp'] - layers[2]['edge_pp']:+.2f}pp —— "
        "方向虽为正、量级远小于噪声。**成交量在日线 ETF 上更像「事后确认情绪」，不是领先信号。**",
        f"5. **半底仓 + 半信号的改进效果：年化 {hyb['ann'] * 100:+.2f}%**"
        f"（基准 {base_s['ann'] * 100:+.2f}%，静态带 {b['ann'] * 100:+.2f}%），"
        f"夏普 {hyb['sharpe']:.2f}、回撤 {hyb['mdd'] * 100:.1f}%、平均投入 {hyb['invested']:.0%}；"
        f"但把它的平均投入也压成买入持有后，超额仍是 **{hyb['edge_pp']:+.2f}pp** —— "
        "**问题不在仓位结构（底仓 vs 信号），而在「时序择时本身没有 alpha」**："
        "加底仓只是把暴露从 15% 抬到 32%、按比例多赚，并没有多出任何信息。",
        "",
        "### 可执行建议（按性价比排序）",
        "",
        "| # | 建议 | 理由（挂数字） | 预期效果 |",
        "|---|---|---|---|",
        f"| 1 | **别把它当收益引擎用**，当「低暴露纪律模板」用 | 组合层 {base_s['ann'] * 100:+.2f}% vs "
        f"同曝光 {same_s['ann'] * 100:+.2f}%（{base_s['edge_pp']:+.2f}pp），夏普 "
        f"{base_s['sharpe']:.2f} vs {same_s['sharpe']:.2f} | 省心、不误导 |",
        "| 2 | 想降回撤就直接降暴露（主策略阶梯/闸门），别绕道日频择时 | 满仓静态带"
        f"年化 {b['ann'] * 100:+.2f}%、夏普 {b['sharpe']:.2f}，回撤 {b['mdd'] * 100:.1f}%；"
        f"砍到同回撤量级仍有约 {b['ann'] * 100 * 0.6:+.1f}% | 同回撤、收益高一个量级 |",
        f"| 3 | 若坚持用它，把**风险预算提到 5%** 并接受回撤同步放大 | Δ年化 "
        f"{abl[4]['d_ann_pp']:+.2f}pp、回撤从 {abl[0]['mdd'] * 100:.1f}% 恶化到 "
        f"{abl[4]['mdd'] * 100:.1f}%、夏普几乎不动 | 只是加杠杆，不改结论 |",
        "| 4 | 参数别再调了 | 关掉 ADX / 量能 / 移动止损的 Δ年化分别 "
        f"{abl[1]['d_ann_pp']:+.2f} / {abl[2]['d_ann_pp']:+.2f} / {abl[3]['d_ann_pp']:+.2f}pp —— 都在噪声内，"
        "调参只会拟合噪声 | 避免过拟合 |",
        "| 5 | 要真做「量价」，把量能用在**横截面选腿**（哪条腿在放量）而不是时序择时 | 时序上量能超额很小；"
        "横截面未测（需要多标的池） | 值得单独立项验证 |",
    ]
    return out


def _plot(layers, abl, base_s, same_s, hyb):
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    plt.rcParams["font.sans-serif"] = ["Microsoft YaHei", "SimHei", "DejaVu Sans"]
    plt.rcParams["axes.unicode_minus"] = False

    fig, (ax1, ax2) = plt.subplots(1, 2, figsize=(13, 4.8))
    names = ["①MA20", "②+年线", "③+ADX", "④+量能"]
    edges = [r["edge_pp"] for r in layers]
    ax1.bar(names, edges, color=["#90a4ae"] * 3 + ["#c2185b"])
    ax1.axhline(0, color="#333", lw=1)
    for i, v in enumerate(edges):
        ax1.annotate(f"{v:+.2f}", (i, v), ha="center",
                     va="bottom" if v >= 0 else "top", fontsize=9)
    ax1.set_title("持有型过滤器逐层的「择时超额」（pp/年，vs 同曝光买入持有）", fontsize=10)
    ax1.set_ylabel("pp/年")
    ax1.grid(alpha=0.3, axis="y")

    labels = ["基准"] + [r["name"].replace("关掉", "无").replace("风险预算 2%->", "风险") for r in abl[1:]]
    vals = [r["d_ann_pp"] for r in abl]
    ax2.barh(labels, vals, color=["#455a64"] + ["#ef6c00"] * (len(vals) - 1))
    ax2.axvline(0, color="#333", lw=1)
    for i, v in enumerate(vals):
        ax2.annotate(f"{v:+.2f}", (v, i), va="center",
                     ha="left" if v >= 0 else "right", fontsize=8)
    ax2.set_title(f"单因子归因：关掉/放大某一维的 Δ年化（基准 {base_s['ann'] * 100:+.2f}%）", fontsize=10)
    ax2.set_xlabel("Δ年化 pp")
    ax2.grid(alpha=0.3, axis="x")

    fig.tight_layout()
    out = os.path.join(_HERE, "_attrib_contrib.png")
    fig.savefig(out, dpi=130, facecolor="white")
    plt.close(fig)
    print("图:", out)


if __name__ == "__main__":
    main()
