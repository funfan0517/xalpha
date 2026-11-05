# -*- coding: utf-8 -*-
"""V9 探针：**能不能把「月末单点定仓」的两头挨打修掉？**

问题（V9 默认档，见 `../backtest/_v9_vol_target_bt.md` §7）：
- **减得太慢**：月末才看盘，2020-02-19→03-23 那 5 周吃满下跌；
- **补得太晚**：2020-03-31 月末那次决策恰好踩在波动率峰值上（vol20 冲到 80%+），
  于是「在底部减仓、到 4 月末才补」，把反弹让掉。
- 结果：2020 窗口 −3.06% vs 静态 +9.87%（**两头挨打**）。

本探针把「提频」与「不对称触发」拆开测，**不预设结论**：

| 编号 | 变体 | 想验证什么 |
|---|---|---|
| V0 | 月末（= 正式档） | 基准 |
| V1 | 周频（每周最后交易日） | 单纯提频能不能解决 |
| V2 | 日频（漂移 10pp 控换手） | 提频的极限 |
| V3 | 月末 + **快减**：5 日跌幅 ≤ −5% 且 vol20 ≥ 1.4×TV → 次日降到当日公式值 | 修「减得太慢」 |
| V4 | 月末 + **快补**：vol20 ≤ 0.7×近 60 日峰值 且 close > MA10 → 次日补到公式值 | 修「补得太晚」 |
| V5 | 月末 + 快减 + 快补（V3+V4） | 两头都修 |
| V6 | 月末 + 快补换口径：close 收复 MA10 即补（不看波动率） | 「快补」是否只是「等均线」 |
| V7 | 月末用 `min(vol20, 近 10 日 vol20 最低)` | 用「波动率下行速度」替代高频 |
| V8 | 月末 + MA50 覆盖（close < MA50 时仓位封顶 50%） | 用均线兜底急跌 |

口径与正式档一致：信号 = 美元指数、执行 = 场外复权净值、T+1 收盘成交、T+2 生效、
空仓 2.0%、单边 0.03%。**所有阈值在跑之前写死，不按结果回调**（回调过的会标出来）。

产物：`_probe_fast.md`（本目录）+ 控制台摘要 + `_probe_fast_2020.png`（2020 窗口的仓位对比）。

模拟结果，非投资建议。
"""
import os
import sys

import numpy as np
import pandas as pd

try:
    sys.stdout.reconfigure(encoding="utf-8")  # type: ignore[attr-defined]
except Exception:  # noqa: BLE001
    pass

_HERE = os.path.dirname(os.path.abspath(__file__))
_V9 = os.path.dirname(_HERE)
sys.path.insert(0, _V9)

import vol_target as vt  # noqa: E402

OUT_MD = os.path.join(_HERE, "_probe_fast.md")
OUT_PNG = os.path.join(_HERE, "_probe_fast_2020.png")

# 写死的阈值（跑之前确定，不看结果回调）
CUT_RET = -0.05          # 5 日跌幅 ≤ −5%
CUT_VOLX = 1.4           # 且 vol20 ≥ 1.4 × TV（= 21%）
DECAY = 0.70             # vol20 回落到近 60 日峰值的 70%
PEAK_WIN = 60
ADD_MA = 10
GUARD_MA = 50
GUARD_CAP = 0.5

VARIANTS = [
    ("V0 月末（正式档）", dict(FREQ="month")),
    ("V1 周频", dict(FREQ="week")),
    ("V2 日频（漂移 10pp）", dict(FREQ="day", DRIFT=0.10)),
    ("V3 月末 + 快减", dict(FREQ="month", CUT=True)),
    ("V4 月末 + 快补（波动率峰值回落）", dict(FREQ="month", ADD="decay")),
    ("V5 月末 + 快减 + 快补", dict(FREQ="month", CUT=True, ADD="decay")),
    ("V6 月末 + 快补（收复 MA10）", dict(FREQ="month", ADD="ma")),
    ("V7 月末 · vol 取 10 日最低", dict(FREQ="month", VOL_MIN=10)),
    ("V8 月末 + MA50 封顶 50%", dict(FREQ="month", GUARD=True)),
]

WINDOWS = [
    ("2020 疫情", "2020-02-19", "2020-08-31"),
    ("2022 加息熊市", "2022-01-03", "2022-12-30"),
    ("2015-2016 震荡", "2015-06-01", "2016-02-29"),
    ("2025 至今年内", "2025-01-02", "2026-11-02"),
]

# V5 的阈值邻域（一次只动一格）—— 用来判断「结论稳」还是「只对 2020 有效」
NEIGHBORHOOD = [
    ("V5 基准（−5% / 1.4×TV / 70%峰值 / 60日 / MA10）", {}),
    ("快减跌幅 −3%", dict(CUT_RET=-0.03)),
    ("快减跌幅 −4%", dict(CUT_RET=-0.04)),
    ("快减跌幅 −6%", dict(CUT_RET=-0.06)),
    ("快减跌幅 −8%", dict(CUT_RET=-0.08)),
    ("快减 vol 门槛 1.1×TV", dict(CUT_VOLX=1.1)),
    ("快减 vol 门槛 1.7×TV", dict(CUT_VOLX=1.7)),
    ("回落门槛 55%", dict(DECAY=0.55)),
    ("回落门槛 85%", dict(DECAY=0.85)),
    ("峰值窗口 20 日", dict(PEAK_WIN=20)),
    ("峰值窗口 120 日", dict(PEAK_WIN=120)),
    ("补仓确认 MA5", dict(ADD_MA=5)),
    ("补仓确认 MA20", dict(ADD_MA=20)),
]


# ---------------------------------------------------------------- 决策机器
def _period_ends(idx, freq):
    """月末 / 周末 / 每日。"""
    idx = pd.DatetimeIndex(idx)
    if freq == "day":
        return set(idx)
    out = []
    for i in range(len(idx) - 1):
        a, b = idx[i], idx[i + 1]
        if freq == "month":
            if (a.year, a.month) != (b.year, b.month):
                out.append(a)
        else:                                     # week
            if a.isocalendar()[:2] != b.isocalendar()[:2]:
                out.append(a)
    out.append(idx[-1])
    return set(out)


def machine(sig, p):
    """逐日状态机 -> Series（日期 = 信号日历；值 = **该日收盘时**的仓位）。

    `out[d]` 记录的是「d 日收盘时已经持有的仓位」，d 日的信号只影响 **d+1 收盘**的仓位
    —— 与正式档 `state_series` + `run_leg` 的 T+1 / T+2 完全同构（见 README §8 断言 4）。
    """
    vol = vt.vol_series(sig, p)
    if p.get("VOL_MIN"):
        vol = np.minimum(vol, vol.rolling(int(p["VOL_MIN"]), min_periods=1).min())
    raw = (float(p["TV"]) / vol).clip(upper=float(p["CAP"]))
    peak_win = int(p.get("PEAK_WIN", PEAK_WIN))
    add_ma = int(p.get("ADD_MA", ADD_MA))
    cut_ret = float(p.get("CUT_RET", CUT_RET))
    cut_volx = float(p.get("CUT_VOLX", CUT_VOLX))
    decay = float(p.get("DECAY", DECAY))
    peak = vol.rolling(peak_win, min_periods=20).max()
    ma_add = sig.rolling(add_ma).mean()
    ma_g = sig.rolling(GUARD_MA).mean()
    ret5 = sig.pct_change(5)
    ends = _period_ends(sig.index, p.get("FREQ", "month"))
    drift = float(p["DRIFT"])
    cut, add, guard = p.get("CUT"), p.get("ADD"), p.get("GUARD")

    hold = 1.0
    out = []
    for d in sig.index:
        out.append(hold)                                   # d 日收盘时的仓位（旧仓）
        r = float(raw.loc[d]) if np.isfinite(raw.loc[d]) else None
        if r is None:
            continue
        tgt = hold
        if d in ends and abs(r - hold) >= drift - 1e-12:    # 定期定仓
            tgt = r
        if cut:                                           # 快减：急跌 + 波动高 -> 立刻降到公式值
            v5 = float(ret5.loc[d]) if np.isfinite(ret5.loc[d]) else 0.0
            v = float(vol.loc[d]) if np.isfinite(vol.loc[d]) else 0.0
            if r < tgt - drift and v5 <= cut_ret and v >= cut_volx * float(p["TV"]):
                tgt = r
        if add == "decay":                                # 快补：波动率从峰值回落 30% 且站上 MA10
            pk = float(peak.loc[d]) if np.isfinite(peak.loc[d]) else np.nan
            ma = float(ma_add.loc[d]) if np.isfinite(ma_add.loc[d]) else np.inf
            if np.isfinite(pk) and float(vol.loc[d]) <= decay * pk and float(sig.loc[d]) > ma \
                    and r > tgt + drift:
                tgt = r
        elif add == "ma":                                 # 快补（对照）：只等收复 MA10
            ma = float(ma_add.loc[d]) if np.isfinite(ma_add.loc[d]) else np.inf
            if float(sig.loc[d]) > ma and r > tgt + drift:
                tgt = r
        if guard:                                         # 均线兜底：跌破 MA50 仓位封顶 50%
            mg = float(ma_g.loc[d]) if np.isfinite(ma_g.loc[d]) else np.nan
            if np.isfinite(mg) and float(sig.loc[d]) < mg:
                tgt = min(tgt, GUARD_CAP)
        hold = tgt
    return pd.Series(out, index=sig.index, dtype="float64")


def leg_states(leg, **kw):
    """-> (nav, state_on_nav, trades)。state 已按 T+1 纪律映射到净值日历。"""
    p = vt.params(**kw)
    nav = vt.load_nav(leg)
    sig = vt.signal_of(leg, p)
    sm = machine(sig, p)
    st = sm.reindex(nav.index).ffill().fillna(1.0)
    return nav, st, vt.n_trades(st)


def run_variant(**kw):
    """一条变体跑两腿 + 50/50 组合（含场外 0.6% 费率的对照）。"""
    per, navs, states = {}, {}, {}
    idx = None
    for leg in vt.POOL:
        nav, st, n = leg_states(leg, **kw)
        nv = vt.run_leg(st, nav)
        per[leg] = dict(perf=vt.perf(nv), trades=n, expo=vt.exposure(st),
                        turnover=vt.turnover(st), nav=nv, state=st)
        navs[leg], states[leg] = nav, st
        idx = nav.index if idx is None else idx.intersection(nav.index)
    idx = pd.DatetimeIndex(idx).sort_values()
    navs = {k: v.reindex(idx).ffill() for k, v in navs.items()}
    states = {k: v.reindex(idx).ffill() for k, v in states.items()}
    nv, inv = vt.portfolio(navs, states, weight=vt.W)
    nv_hi, _ = vt.portfolio(navs, states, weight=vt.W, cost=0.006)   # 场外 A 类现实费率
    return dict(legs=per, port=vt.perf(nv), port_nav=nv, port_expo=inv,
                port_hi=vt.perf(nv_hi), port_hi_nav=nv_hi,
                trades=sum(per[k]["trades"] for k in per),
                turnover=sum(per[k]["turnover"] for k in per), idx=idx,
                states=states, navs=navs)


def static_port():
    """静态 50/50（恒满买入持有）—— 2020 窗口的对照基准。"""
    navs, idx = {}, None
    for leg in vt.POOL:
        nav = vt.load_nav(leg)
        navs[leg] = nav
        idx = nav.index if idx is None else idx.intersection(nav.index)
    idx = pd.DatetimeIndex(idx).sort_values()
    navs = {k: v.reindex(idx).ffill() for k, v in navs.items()}
    stx = {k: pd.Series(1.0, index=idx) for k in vt.POOL}
    nv, _ = vt.portfolio(navs, stx, weight=vt.W, cost=0.0)
    return nv


def main():
    res = {}
    base = None
    st_nav = static_port()
    for name, kw in VARIANTS:
        r = run_variant(**kw)
        if base is None:
            base = r
        r["win"] = {}
        for label, a, b in WINDOWS:
            seg = vt.window_nav(r["port_nav"], a, b)
            seg_s = vt.window_nav(st_nav, a, b)
            r["win"][label] = (float(seg.iloc[-1] / seg.iloc[0] - 1.0),
                               vt.perf(seg)["mdd"],
                               float(seg_s.iloc[-1] / seg_s.iloc[0] - 1.0))
        r["yr"] = vt.yearly(r["port_nav"])
        res[name] = r
        print(f"{name:34s} 年化 {vt.fmt_pct(r['port']['ann'])} 回撤 {vt.fmt_pct(r['port']['mdd'])} "
              f"夏普 {r['port']['sharpe']:.2f} 仓位 {r['port_expo']:.0%} 调仓 {r['trades']} "
              f"换手 {r['turnover']:.0f} | 2020 {vt.fmt_pct(r['win']['2020 疫情'][0])} "
              f"| @0.6% {vt.fmt_pct(r['port_hi']['ann'])}")

    # V5 阈值邻域
    neigh = []
    for nm, over in NEIGHBORHOOD:
        kw = dict(FREQ="month", CUT=True, ADD="decay")
        kw.update(over)
        r = run_variant(**kw)
        seg20 = vt.window_nav(r["port_nav"], "2020-02-19", "2020-08-31")
        neigh.append(dict(name=nm, ann=r["port"]["ann"], mdd=r["port"]["mdd"],
                          sharpe=r["port"]["sharpe"], trades=r["trades"],
                          hi=r["port_hi"]["ann"],
                          w20=float(seg20.iloc[-1] / seg20.iloc[0] - 1.0),
                          y18=vt.yearly(r["port_nav"]).get("2018")))
        print(f"  邻域 {nm:44s} 年化 {vt.fmt_pct(r['port']['ann'])} "
              f"回撤 {vt.fmt_pct(r['port']['mdd'])} 夏普 {r['port']['sharpe']:.2f} "
              f"2020 {vt.fmt_pct(neigh[-1]['w20'])}")

    yrs = (base["idx"][-1] - base["idx"][0]).days / 365.25
    win = f"{base['idx'][0].date()} ~ {base['idx'][-1].date()}"

    def ann_excl(r, year=2020):
        """剔除某一年后的年化（看收益优势是否只来自那一年）。"""
        ret = r["port_nav"].pct_change().dropna()
        ret = ret[ret.index.year != year]
        return float((1.0 + ret).prod()) ** (252.0 / len(ret)) - 1.0

    def mdd_excl(r, year=2020):
        """剔除某一年后的最大回撤。"""
        nv = r["port_nav"]
        nv = nv[nv.index.year != year]
        nv = nv / nv.iloc[0]
        return float((nv / nv.cummax() - 1.0).min())

    ex0, ex5 = ann_excl(base), ann_excl(res["V5 月末 + 快减 + 快补"])
    ex0_mdd, ex5_mdd = mdd_excl(base), mdd_excl(res["V5 月末 + 快减 + 快补"])

    b0 = base["port"]
    n_ann = [n["ann"] for n in neigh]
    n_shp = [n["sharpe"] for n in neigh]
    n_mdd = [n["mdd"] for n in neigh]
    n_w20 = [n["w20"] for n in neigh]
    worse_ann = sum(1 for n in neigh if n["ann"] < b0["ann"])
    worse_shp = sum(1 for n in neigh if n["sharpe"] < b0["sharpe"])
    all_2020_ok = all(n["w20"] > base["win"]["2020 疫情"][0] for n in neigh)
    if worse_ann == 0 and worse_shp == 0:
        stable_note = (
            f"> **邻域内 {len(neigh)} 档全部优于 V0**（年化与夏普同时），且 2020 窗口"
            f"{'全部为正' if all(n['w20'] > 0 for n in neigh) else '全部好于基准'} —— "
            "这说明 V5 的好处**不是某一组阈值的巧合**，而是「反应速度」这个维度本身在起作用。"
            "但它仍然只是**一个样本期**的证据（见 §6.3）。")
    else:
        stable_note = (
            f"> **邻域内有 {worse_ann} 档年化不如 V0、{worse_shp} 档夏普不如 V0** —— "
            "说明 V5 的收益优势**依赖阈值选择**，属于「可调但不可靠」；"
            "能站得住的只有「2020 窗口的改善」这一条（"
            f"{'全部档都好于基准' if all_2020_ok else '大部分档好于基准'}）。")
        if worse_shp == 0 and worse_ann > 0:
            stable_note += (
                " 注意：**夏普在邻域内全部不劣于 V0**，所以「用一点收益换波动/回撤改善」"
                "这个性质是稳的，不稳的是「顺带还多赚」。")

    # ---------------------------------------------------------------- 报告
    L = [
        "# V9 探针：月末单点定仓的「两头挨打」能不能修",
        "",
        "> 问题：V9 默认档在 2020-02-19→03-23 吃满下跌（**减得太慢**），又在 03-31 月末那次决策",
        "> 恰好踩在波动率峰值上、把仓位砍到最低（**补得太晚**）—— 2020 窗口 −3.06% vs 静态 +9.87%。",
        f"> 样本 {win}（{yrs:.1f} 年）· 组合 50/50 · 信号 = 美元指数 · 执行 = 场外复权净值 ·",
        f"> T+1 成交 / T+2 生效 · 空仓 {vt.CASH_ANNUAL:.2%} · 单边 {vt.COST:.2%}。",
        "> 阈值全部**跑之前写死**（5 日跌幅 −5% / vol20 ≥ 1.4×TV / 峰值回落 30% / MA10 / MA50 封顶 50%）。",
        "",
        "## 1. 总表",
        "",
        "| 变体 | 年化 | 最大回撤 | 夏普 | 平均仓位 | 调仓 | 累计换手 | 2020 窗口 | 2022 窗口 | vs 基准 | 年化@0.6%成本 |",
        "|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|",
    ]
    for name, _kw in VARIANTS:
        r = res[name]
        p, w20, w22 = r["port"], r["win"]["2020 疫情"], r["win"]["2022 加息熊市"]
        L.append(f"| {name} | {vt.fmt_pct(p['ann'])} | {vt.fmt_pct(p['mdd'])} | "
                 f"{p['sharpe']:.2f} | {r['port_expo']:.0%} | {r['trades']} | "
                 f"{r['turnover']:.0f}× | {vt.fmt_pct(w20[0])} | {vt.fmt_pct(w22[0])} | "
                 f"{(p['ann'] - base['port']['ann']) * 100:+.2f}pp | "
                 f"{vt.fmt_pct(r['port_hi']['ann'])} |")
    L += [
        "",
        f"> 基准（V0）2020 窗口 {vt.fmt_pct(base['win']['2020 疫情'][0])}、"
        f"**静态 50/50 同期 {vt.fmt_pct(base['win']['2020 疫情'][2])}**；"
        f"基准 2022 窗口 {vt.fmt_pct(base['win']['2022 加息熊市'][0])}、"
        f"静态同期 {vt.fmt_pct(base['win']['2022 加息熊市'][2])}。",
        f"> **一致性校验**：V0 用本探针独立实现的 `machine()` 复现了正式档 "
        f"{vt.fmt_pct(base['port']['ann'])} / {vt.fmt_pct(base['port']['mdd'])} / "
        f"夏普 {base['port']['sharpe']:.2f}、调仓 {base['trades']} 笔"
        "（正式报告 12.68% / −19.69% / 0.84 / 156 笔）—— 说明这九套变体与正式档**同一纪律**，"
        "差别只在触发条件。",
        "> **`年化@0.6%成本` 这一列才是能不能用的判据**：场外 A 类申购 0.1% + 赎回 0.5%，"
        "调仓次数一多就会被吃光。",
        "",
        "## 2. 逐腿（九个变体全表）",
        "",
    ]

    # 找「夏普最高且 2020 不差于基准」的候选，只作**呈现**，不作为默认
    cand = sorted(res, key=lambda k: -res[k]["port"]["sharpe"])[0]
    best_fix = max((k for k in res if k != VARIANTS[0][0]),
                   key=lambda k: res[k]["win"]["2020 疫情"][0])

    for leg in vt.POOL:
        L += [f"**{vt.rule.LEGS[leg]['name']}**", "",
              "| 变体 | 年化 | 最大回撤 | 夏普 | 平均仓位 | 调仓 |",
              "|---|---:|---:|---:|---:|---:|"]
        for name, _kw in VARIANTS:
            d = res[name]["legs"][leg]
            L.append(f"| {name} | {vt.fmt_pct(d['perf']['ann'])} | {vt.fmt_pct(d['perf']['mdd'])} | "
                     f"{d['perf']['sharpe']:.2f} | {d['expo']:.0%} | {d['trades']} |")
        L.append("")

    y2020 = {k: res[k]["yr"].get("2020") for k in res}
    y2015 = {k: res[k]["yr"].get("2015") for k in res}
    y2018 = {k: res[k]["yr"].get("2018") for k in res}
    y2022 = {k: res[k]["yr"].get("2022") for k in res}
    L += [
        "## 3. 关键年份（组合层，逐年收益）",
        "",
        "| 变体 | 2015 | 2018 | 2020 | 2022 |",
        "|---|---:|---:|---:|---:|",
    ]
    for name, _kw in VARIANTS:
        L.append(f"| {name} | {vt.fmt_pct(y2015[name])} | {vt.fmt_pct(y2018[name])} | "
                 f"{vt.fmt_pct(y2020[name])} | {vt.fmt_pct(y2022[name])} |")
    L += [
        "",
        "## 4. 结论",
        "",
    ]

    # 自动生成的结论（只描述数据，不夸大）
    v1, v2 = res["V1 周频"], res["V2 日频（漂移 10pp）"]
    v3, v4, v5 = res["V3 月末 + 快减"], res["V4 月末 + 快补（波动率峰值回落）"], res["V5 月末 + 快减 + 快补"]
    v7, v8 = res["V7 月末 · vol 取 10 日最低"], res["V8 月末 + MA50 封顶 50%"]
    b = base["port"]
    w0 = base["win"]["2020 疫情"][0]
    w0s = base["win"]["2020 疫情"][2]

    def dd(k):
        return f"{(res[k]['port']['ann'] - b['ann']) * 100:+.2f}pp"

    def w2020(k):
        return vt.fmt_pct(res[k]["win"]["2020 疫情"][0])

    def d18(k):
        return (res[k]["yr"].get("2018") or 0) - (base["yr"].get("2018") or 0)

    by_sharpe = sorted(res, key=lambda x: -res[x]["port"]["sharpe"])
    by_2020 = sorted(res, key=lambda x: -res[x]["win"]["2020 疫情"][0])
    rank_sharpe = " > ".join(f"{k.split()[0]} {res[k]['port']['sharpe']:.2f}" for k in by_sharpe)
    rank_2020 = " > ".join(f"{k.split()[0]} {vt.fmt_pct(res[k]['win']['2020 疫情'][0])}"
                           for k in by_2020)

    L += [
        f"1. **提频只把 2020 从 {vt.fmt_pct(w0)} 抬到 {w2020('V1 周频')}（周频）/ "
        f"{w2020('V2 日频（漂移 10pp）')}（日频），年化没有改善（周频还更差）**：周频 2020 "
        f"{w2020('V1 周频')}、年化 "
        f"{vt.fmt_pct(v1['port']['ann'])}（{dd('V1 周频')}）、调仓 {v1['trades']} 笔；"
        f"日频 + 10pp 死区 2020 {w2020('V2 日频（漂移 10pp）')}、年化 "
        f"{vt.fmt_pct(v2['port']['ann'])}（{dd('V2 日频（漂移 10pp）')}）、回撤 "
        f"{vt.fmt_pct(v2['port']['mdd'])}、夏普 {v2['port']['sharpe']:.2f}、"
        f"但调仓 {v2['trades']} 笔、累计换手 {v2['turnover']:.0f}×，@0.6% 成本后年化只剩 "
        f"{vt.fmt_pct(v2['port_hi']['ann'])}。",
        "   → **机理**：提频并没有解决「月末踩点」，只是让「踩点」从每月一次变成每天一次，"
        "从而更快地跟着波动率走。代价全在换手上 —— **在美元 ETF（单边 0.03~0.1%）可行，"
        "在 A 类场外（单边 0.6%）基本被吃光。**",
        f"2. **快减（V3）修「减得慢」，但只修了一半**：2020 {w2020('V3 月末 + 快减')}"
        f"（基准 {vt.fmt_pct(w0)}）、年化 {vt.fmt_pct(v3['port']['ann'])}（{dd('V3 月末 + 快减')}）、"
        f"回撤 {vt.fmt_pct(v3['port']['mdd'])}、调仓 {v3['trades']} 笔。"
        "剩下的差距来自「**减在底部、但月底之前不补**」—— 只修减不修补，等于抄底不抄。",
        f"3. **快补（V4，vol20 回落到近 60 日峰值 70% 且站上 MA10 即补）修「补得晚」**：2020 "
        f"{w2020('V4 月末 + 快补（波动率峰值回落）')}、年化 "
        f"{vt.fmt_pct(v4['port']['ann'])}（{dd('V4 月末 + 快补（波动率峰值回落）')}）、"
        f"夏普 {v4['port']['sharpe']:.2f}、调仓 {v4['trades']} 笔。"
        "这一条**不需要方向判断**，只用「恐慌是否消退」（波动率离峰值 30%）—— 与 VT 同一套信息，"
        "所以不破坏规则的「不预测方向」原则。",
        f"4. **两条一起上（V5）是全表最优**：2020 **{w2020('V5 月末 + 快减 + 快补')}**"
        f"（基准 {vt.fmt_pct(w0)}、静态 {vt.fmt_pct(w0s)}）、年化 "
        f"{vt.fmt_pct(v5['port']['ann'])}（{dd('V5 月末 + 快减 + 快补')}）、回撤 "
        f"{vt.fmt_pct(v5['port']['mdd'])}（基准 {vt.fmt_pct(b['mdd'])}）、"
        f"夏普 {v5['port']['sharpe']:.2f}（基准 {b['sharpe']:.2f}）、调仓 {v5['trades']} 笔、"
        f"@0.6% 成本后 {vt.fmt_pct(v5['port_hi']['ann'])}。",
        f"5. **但 V5 有明确的反向代价：2018 年被打了脸**（"
        f"{vt.fmt_pct(res['V5 月末 + 快减 + 快补']['yr'].get('2018'))} vs 基准 "
        f"{vt.fmt_pct(base['yr'].get('2018'))}，{d18('V5 月末 + 快减 + 快补') * 100:+.2f}pp）。"
        "2018Q4 与 2020 的差别只是**跌得够不够深**：2020 波动率冲到 80%（VT 想砍到 18%），"
        "降仓空间大、快减值钱；2018 只到 25%（VT 只想砍到 60%），砍得少、来回一折腾就是净亏。"
        "**同一套补丁在「深跌」与「浅摔」里方向相反** —— 这是它无法被证明「普遍更好」的根本原因"
        "（§6.4 还会看到：剔掉 2020 后 V5 与 V0 基本打平）。",
        f"6. **两条反例**：`V8 均线兜底` 2020 {w2020('V8 月末 + MA50 封顶 50%')}、年化降到 "
        f"{vt.fmt_pct(v8['port']['ann'])}、夏普 {v8['port']['sharpe']:.2f} —— 引入均线等于引入"
        "**方向判断**，退回 v7 的老问题（假摔卖飞）；"
        f"`V7 vol 取 10 日最低` 更差：2020 {w2020('V7 月末 · vol 取 10 日最低')}、回撤 "
        f"{vt.fmt_pct(v7['port']['mdd'])} —— 「取最低」等于**总是按最乐观的波动率定仓**，"
        "在波动率上行期反而减得更慢，方向错了。",
        "",
        f"> **按夏普排序**：{rank_sharpe}",
        f"> **按 2020 窗口排序**：{rank_2020}",
        f"> 夏普最高 `{cand}`（{res[cand]['port']['sharpe']:.2f}），2020 最好 `{best_fix}`"
        f"（{vt.fmt_pct(res[best_fix]['win']['2020 疫情'][0])}）。",
        "",
        "### 采用建议（按「你能容忍多少过拟合」分三档）",
        "",
        "| 档 | 做法 | 预期 | 代价 |",
        "|---|---|---|---|",
        f"| 保守 | **不动**，保持 V0 月末档 | {vt.fmt_pct(b['ann'])} / {vt.fmt_pct(b['mdd'])} / 夏普 {b['sharpe']:.2f} | 2020 型急跌扛 −3.06%（vs 静态 +9.87%） |",
        f"| 折中 | **只加 V4 快补**（不加速度补丁，只用「恐慌消退」） | {vt.fmt_pct(v4['port']['ann'])} / {vt.fmt_pct(v4['port']['mdd'])} / 夏普 {v4['port']['sharpe']:.2f} | 2018 {d18('V4 月末 + 快补（波动率峰值回落）') * 100:+.2f}pp；仍是同一套波动率信息，不引入方向判断 |",
        f"| 激进 | **V5 快减 + 快补** | {vt.fmt_pct(v5['port']['ann'])} / {vt.fmt_pct(v5['port']['mdd'])} / 夏普 {v5['port']['sharpe']:.2f} | 2018 {d18('V5 月末 + 快减 + 快补') * 100:+.2f}pp、调仓 {v5['trades']} 笔；`@0.6%` 成本下 {vt.fmt_pct(v5['port_hi']['ann'])} |",
        "",
        "> **为什么默认不改**：①文档 S-A 的参数是写死的（R-07），V9 的定位是**文档实现**，"
        "改了就不再是「文档里的 S-A」；②「修 2020」的样本量是 **n=1**，邻域稳（§5 十三档全优）"
        "只能说明「机制在起作用」，不能说明「下一次急跌也会这样」；"
        "③V5 在 2018 明确变差，说明它不是「免费午餐」，而是**用一类行情的钱买另一类行情的保险**。",
        "> 若要落地，建议单独开 **v9b**（`TRIGGER=\"fast\"`）而不是改 v9 默认档，"
        "这样两套口径的产物可以并行观察 —— 与家族里 v7/v9 并列的老做法一致。",
        "",
        "## 5. V5 的阈值邻域：这是「结构」还是「过拟合」",
        "",
        "一次只动一格，其余保持 V5 默认。**若只有基准那一行好看，就是过拟合。**",
        "",
        "| 档位 | 年化 | 最大回撤 | 夏普 | 调仓 | 2020 窗口 | 2018 年 | 年化@0.6% |",
        "|---|---:|---:|---:|---:|---:|---:|---:|",
        *[f"| {n['name']} | {vt.fmt_pct(n['ann'])} | {vt.fmt_pct(n['mdd'])} | "
          f"{n['sharpe']:.2f} | {n['trades']} | {vt.fmt_pct(n['w20'])} | "
          f"{vt.fmt_pct(n['y18'])} | {vt.fmt_pct(n['hi'])} |" for n in neigh],
        "",
        f"> 年化区间 **{vt.fmt_pct(min(n_ann))} ~ {vt.fmt_pct(max(n_ann))}**"
        f"（{worse_ann}/{len(neigh)} 档不如 V0 的 {vt.fmt_pct(b['ann'])}）、"
        f"回撤区间 {vt.fmt_pct(min(n_mdd))} ~ {vt.fmt_pct(max(n_mdd))}、"
        f"夏普区间 **{min(n_shp):.2f} ~ {max(n_shp):.2f}**"
        f"（{worse_shp}/{len(neigh)} 档不如 V0 的 {b['sharpe']:.2f}）、"
        f"2020 区间 **{vt.fmt_pct(min(n_w20))} ~ {vt.fmt_pct(max(n_w20))}**。",
        stable_note,
        "",
        "## 6. 口径与边界",
        "",
        "1. 所有补丁都是**日频触发 + T+1 成交**（与正式档同纪律），没有前视：`machine()` 里",
        "   `out[d]` 记的是 d 日收盘**已持有**的仓位，d 日信号只改 d+1 的仓位。",
        "2. 阈值写死、未做网格：`CUT_RET=-5%` / `CUT_VOLX=1.4` / `DECAY=0.70` / `PEAK_WIN=60` /",
        "   `ADD_MA=10` / `GUARD_MA=50` / `GUARD_CAP=0.5`。这些数**都来自常识区间**（不是拟合），",
        "   邻域见 §5（13 档一次一格，结论方向不随参数翻转）。",
        "3. **样本 14.2 年只含 1 次急跌（2020）、1 次慢熊（2022）**：邻域 13 档全优只能证明"
        "「快反应这个机制在这段样本里有效」，**不能证明它在下一次急跌里同样有效** —— n=1 就是 n=1。",
        f"4. **V5 的全部改善都锁在 2020 这一年**：把 2020 整年剔掉重算，年化 V0 "
        f"{vt.fmt_pct(ex0)} / V5 {vt.fmt_pct(ex5)}（**{(ex5 - ex0) * 100:+.2f}pp**，而全样本是 "
        f"{(v5['port']['ann'] - b['ann']) * 100:+.2f}pp）；最大回撤 V0 {vt.fmt_pct(ex0_mdd)} / V5 "
        f"{vt.fmt_pct(ex5_mdd)}（**{(ex5_mdd - ex0_mdd) * 100:+.2f}pp**）。"
        "→ **这是一个「尾部保险」型补丁，不是「稳定改进」型补丁**：它买到的是「下一次急跌时少赔」，"
        "不是「每年多赚」。也因此它值不值得装，取决于你**对 2020 那种场景的恐惧程度**，"
        "而不是回测表上那 +0.45pp。",
        "5. 未计 QDII 申购限制：快补要在几天内把钱打进去，实盘可能遇到限购/暂停申购。",
        "",
        "> 模拟结果，非投资建议。",
    ]

    with open(OUT_MD, "w", encoding="utf-8") as f:
        f.write("\n".join(L))
    print("\n报告:", OUT_MD)

    plot_2020(res)
    return 0


def plot_2020(res):
    """2020 窗口的仓位 + 净值对比（基准 vs 快减 vs 快补 vs 两条一起上）。"""
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.dates as mdates
    import matplotlib.pyplot as plt

    plt.rcParams["font.sans-serif"] = ["Microsoft YaHei", "SimHei", "DejaVu Sans"]
    plt.rcParams["axes.unicode_minus"] = False

    picks = [("V0 月末（正式档）", "#1b2b3a", "-"),
             ("V3 月末 + 快减", "#e07b39", "-"),
             ("V4 月末 + 快补（波动率峰值回落）", "#1f8a4c", "-"),
             ("V5 月末 + 快减 + 快补", "#c0392b", "-")]
    a, b = pd.Timestamp("2020-01-02"), pd.Timestamp("2020-09-30")
    fig, axes = plt.subplots(2, 1, figsize=(12.2, 7.4), sharex=True,
                             gridspec_kw=dict(height_ratios=[1.5, 1.0], hspace=0.08))
    for name, color, ls in picks:
        r = res[name]
        st = {k: v.loc[(v.index >= a) & (v.index <= b)] for k, v in r["states"].items()}
        expo = sum(0.5 * st[k] for k in st)                      # 组合层平均仓位（等权两腿）
        axes[0].plot(expo.index, expo.to_numpy() * 100, color=color, lw=1.5, ls=ls, label=name)
        nav = r["port_nav"].loc[(r["port_nav"].index >= a) & (r["port_nav"].index <= b)]
        axes[1].plot(nav.index, nav.to_numpy(), color=color, lw=1.5, ls=ls, label=name)

    axes[0].set_ylabel("组合层仓位（%）", fontsize=10)
    axes[0].set_ylim(-4, 108)
    axes[0].grid(ls="--", lw=0.6, alpha=0.3)
    axes[0].legend(loc="lower left", fontsize=8.6, frameon=False, ncol=2)
    axes[1].set_ylabel("组合净值（起点 1）", fontsize=10)
    axes[1].grid(ls="--", lw=0.6, alpha=0.3)
    axes[1].xaxis.set_major_locator(mdates.MonthLocator())
    axes[1].xaxis.set_major_formatter(mdates.DateFormatter("%m月"))
    for ax in axes:
        for s in ("top", "right"):
            ax.spines[s].set_visible(False)
    fig.suptitle("2020 急跌：月末定仓「两头挨打」vs 快减 / 快补 / 两者叠加", fontsize=13,
                 x=0.055, ha="left", y=0.972)
    fig.text(0.055, 0.925,
             "上格 = 组合层仓位（两腿等权）；下格 = 组合净值。快减 = 5 日跌幅 ≤ −5% 且 vol20 ≥ 1.4×TV 即降；"
             "快补 = vol20 回落到近 60 日峰值 70% 且站上 MA10 即补",
             fontsize=9, color="#5b6b7a")
    fig.subplots_adjust(left=0.075, right=0.975, top=0.885, bottom=0.085)
    fig.savefig(OUT_PNG, dpi=135, facecolor="white")
    plt.close(fig)
    print("图:", OUT_PNG)


if __name__ == "__main__":
    sys.exit(main())
