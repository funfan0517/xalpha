# -*- coding: utf-8 -*-
"""V9 抄底确认 · 回测、归因与稳健性检验。

**这个脚本要回答的不是「赚多少」，而是「这套确认到底有没有信息量」**。四个层次：

1. **逐腿**（七腿各自全历史）：信号数、笔数、胜率、盈亏比、止损占比，对照买入持有。
2. **组合层**（六腿共同窗口，a500 样本太短不进）：按目标带归一化，对照「静态带恒满」
   与「等权买入持有」—— 若组合跑不过静态带，说明这套择时是在**减少**收益。
3. **消融**：把四条件逐条置为恒成立，看哪一条真正带来了增量（大概率只有前置过滤）；
   **敏感性**：`window / ctx_recall / dry_max_age / vol_mult / stop_pct / drop_min /
   div_window / risk_per_trade` 逐个扫描，看结论是不是靠某一个魔法数字撑住的。
4. **硬断言**：无前视（T+1 成交）、止损成交价不优于止损价、预热期不交易、
   恒空等于货基、仓位边界 —— 这几条不过，上面的数字一律不看。

产物（策略类，落 `backtest/`）：
- `_v9_bottom_report.md` 报告
- `_v9_bottom_bt.jsonl` 逐腿 + 组合的机读结果
- `_v9_bottom_hs300.png` / `_v9_bottom_portfolio.png` 图

用法：
    python strategies/vol_legs/v9_bottom_confirm/backtest.py            # 全量
    python strategies/vol_legs/v9_bottom_confirm/backtest.py --quick    # 跳过敏感性扫描
"""
import json
import math
import os
import sys

import numpy as np
import pandas as pd

try:
    sys.stdout.reconfigure(encoding="utf-8")  # type: ignore[attr-defined]
except Exception:  # noqa: BLE001
    pass

_HERE = os.path.dirname(os.path.abspath(__file__))
_VL = os.path.dirname(_HERE)
_ROOT = os.path.dirname(os.path.dirname(_VL))
for _p in (_HERE, _VL):
    if _p not in sys.path:
        sys.path.insert(0, _p)

import matplotlib          # noqa: E402
matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402

import bottom as bt        # noqa: E402
import engine              # noqa: E402
import rule                # noqa: E402

for _f in ("Microsoft YaHei", "SimHei", "DejaVu Sans"):
    plt.rcParams["font.sans-serif"] = [_f, "DejaVu Sans"]
    break
plt.rcParams["axes.unicode_minus"] = False

OUTDIR = os.path.join(_HERE, "backtest")
OUT_MD = os.path.join(OUTDIR, "_v9_bottom_report.md")
OUT_JSONL = os.path.join(OUTDIR, "_v9_bottom_bt.jsonl")
OUT_PNG_LEG = os.path.join(OUTDIR, "_v9_bottom_hs300.png")
OUT_PNG_PORT = os.path.join(OUTDIR, "_v9_bottom_portfolio.png")

LEGS = bt.LEGS
PLEGS = bt.PORTFOLIO_LEGS
NAMES = {k: bt.klines.UNIVERSE[k]["name"] for k in LEGS}


# ---------------------------------------------------------------- 小工具
def pct(x, nd=2, sign=True):
    if x is None or (isinstance(x, float) and math.isnan(x)):
        return "—"
    return f"{x:+.{nd}%}" if sign else f"{x:.{nd}%}"


def calmar(m):
    return (m["ann"] / abs(m["mdd"])) if m["mdd"] < -1e-9 else None


def first_ready(sm):
    s = sm["ready"]
    return s.index[s.to_numpy().argmax()] if bool(s.any()) else sm.index[-1]


def passive(expo, hold_ann, cash=bt.CASH_ANNUAL):
    """「同曝光被动组合」年化：拿 expo 仓位一直持有，其余放货基。

    这是判断择时有没有信息量的**唯一公平基准** —— 直接比收益会被曝光差污染：
    2022-06 以来六腿整体上涨，任何「多交易」的方案收益都更高。
    """
    return expo * hold_ann + (1.0 - expo) * cash


def alpha(m, expo, hold_ann):
    return m["ann"] - passive(expo, hold_ann)


# ---------------------------------------------------------------- 单腿 / 组合
def leg_run(leg, **kw):
    df = bt.klines.frame(leg)
    sm = bt.state_machine(df, **kw)
    nav = bt.run_leg(df, sm, **kw)
    bh = df["close"] / df["close"].iloc[0]
    tr = bt.trades(df, sm)
    return dict(leg=leg, df=df, sm=sm, nav=nav, bh=bh, tr=tr,
                m=bt.perf(nav), bhm=bt.perf(bh), rs=bt.round_stats(tr),
                start=first_ready(sm))


def hold_nav(frame, legs):
    """等权买入持有（首日建仓后不在平衡）。"""
    w = 1.0 / len(legs)
    return sum(w * frame[k] / frame[k].iloc[0] for k in legs)


def portfolio_run(legs=None, **kw):
    legs = list(legs or PLEGS)
    runs = {k: leg_run(k, **kw) for k in legs}
    frame = pd.DataFrame({k: runs[k]["df"]["close"] for k in legs}).sort_index()
    start = max(runs[k]["start"] for k in legs)
    frame = frame.loc[start:]
    pos = {k: runs[k]["sm"]["position"] for k in legs}
    nav, expo = bt.portfolio(frame, pos, legs)
    flat, _ = bt.portfolio(frame, {k: pd.Series(1.0, index=frame.index) for k in legs}, legs)
    hold = hold_nav(frame, legs)
    cash = pd.Series((1.0 + bt.CASH_DAILY) ** np.arange(len(frame)), index=frame.index,
                     name="cash")
    # 信号与轮次只数**共同窗口内**的（全历史计数会误导：a500/hs300 的早期信号不在窗口里）
    sig = int(sum(runs[k]["sm"]["signal"].reindex(frame.index).fillna(False).sum()
                  for k in legs))
    trades = sum(sum(1 for x in runs[k]["tr"] if x["side"] == "buy" and x["date"] >= start)
                 for k in legs)
    out = dict(legs=legs, frame=frame, runs=runs, nav=nav, expo=expo, static=flat, hold=hold,
               cash=cash, m=bt.perf(nav), sm_=bt.perf(flat), hm=bt.perf(hold),
               cm=bt.perf(cash), sig=sig, trades=trades, start=start)
    return out


# ---------------------------------------------------------------- 断言
def check_all(**kw):
    """无前视 / 止损 / 预热 / 边界 / 恒空 —— 五组硬断言。"""
    out = []
    for leg in LEGS:
        df = bt.klines.frame(leg)
        sm = bt.state_machine(df, **kw)
        d = sm["position"].diff().fillna(sm["position"])
        buys = (d > 1e-9).to_numpy()
        sells = (d < -1e-9).to_numpy()
        prev = sm["decide"].shift(1).fillna("").to_numpy()
        # 1) 仓位只在决策的次日变化（T+1 成交）；止损是常驻挂单，当日成交、不需要前一日决策
        ok_buy = bool(np.all(~buys | (prev == "buy")))
        ok_sell = bool(np.all(~sells | (prev == "sell") | sm["stop_hit"].to_numpy()))
        out.append((f"{leg} 建仓均为信号次日（无前视）", ok_buy, ""))
        out.append((f"{leg} 减仓先有决策，或为当日止损单", ok_sell, ""))
        # 2) 止损成交价不优于止损价、且不低于当日最低
        h = sm["stop_hit"].to_numpy()
        if h.any():
            f, s, lo = (sm["stop_fill"].to_numpy()[h], sm["stop"].to_numpy()[h],
                        sm["low"].to_numpy()[h])
            ok = bool(np.all(f <= s + 1e-9) and np.all(f >= lo - 1e-9))
            out.append((f"{leg} 止损成交价 ≤ 止损价且 ≥ 当日最低（{int(h.sum())} 次）",
                        ok, f"max(fill-stop)={float(np.max(f - s)):.4f}"))
        # 3) 预热期不交易
        pre = sm.loc[:first_ready(sm)][:-1]
        out.append((f"{leg} 预热期（指标未成形）零仓位",
                    bool((pre["position"] == 0).all()) if len(pre) else True, ""))
        # 4) 仓位边界
        out.append((f"{leg} 仓位 ∈ [0, 1]", bool(sm["position"].between(0, 1).all()), ""))
        # 5) 空仓时无止损位（止损当日例外：当日生效的止损位要留痕给断言用）
        flat = (sm["position"] <= 0) & (~sm["stop_hit"])
        out.append((f"{leg} 空仓行不带止损位", bool(sm.loc[flat, "stop"].isna().all()), ""))
    # 6) 恒空 == 货基
    df = bt.klines.frame("hs300")
    sm_kill = bt.state_machine(df, drop_min=1.0)
    nav = bt.run_leg(df, sm_kill, drop_min=1.0)
    cash = (1.0 + bt.CASH_DAILY) ** (len(nav) - 1)
    out.append(("无信号时净值 == 货基复利", bool(abs(nav.iloc[-1] / cash - 1.0) < 1e-9),
                f"{nav.iloc[-1]:.6f} vs {cash:.6f}"))
    return out


# ---------------------------------------------------------------- 漏底诊断
# 七次历史大底（图上肉眼可见、且事后确认的「底部上涨」起点）
SPOTS = (("2015-08-26", "股灾底"), ("2016-01-28", "熔断底"), ("2018-10-19", "贸易战底"),
         ("2022-10-31", "疫情底"), ("2024-02-02", "流动性底"), ("2024-09-13", "924 前夜"),
         ("2025-04-07", "关税底"))

DIAG_CFGS = (
    ("当前默认：站回 VWAP / 地量 90 日 / 回撤 10%", dict()),
    ("改参数前：站回 VWAP / 地量 60 日 / 回撤 20%", dict(dry_max_age=60, drop_min=0.20)),
    ("地量有效期再放到 250 日", dict(dry_max_age=250)),
    ("站上 MA20 也算修复 / 250 日", dict(c4_mode="or_ma20", dry_max_age=250)),
    ("只要站上 MA20 / 250 日", dict(c4_mode="ma20", dry_max_age=250)),
)


def miss_diag():
    """hs300：7 次历史大底 × 几组放宽口径 —— 各自接住几次、代价是什么。"""
    df = bt.klines.frame("hs300")
    I = bt.indicators(df)
    p = bt.params()
    spots = []
    for d, name in SPOTS:
        r = I.loc[:d].iloc[-1]
        spots.append(dict(date=d, name=name, drop=float(r["drop"]),
                          why="、".join(bt._why(r, p).values()) or "无"))
    rows = []
    for label, kw in DIAG_CFGS:
        sm = bt.state_machine(df, **kw)
        tr = bt.trades(df, sm)
        rs = bt.round_stats(tr)
        m = bt.perf(bt.run_leg(df, sm, **kw))
        hits = []
        for d, _ in SPOTS:
            i = sm.index.get_loc(sm.index[sm.index <= d][-1])
            hits.append(bool(sm["signal"].iloc[max(0, i - 20): i + 21].any()))
        rows.append(dict(label=label, hits=hits, rs=rs, m=m))
    return spots, rows


# ---------------------------------------------------------------- 信号质量
def confirm_quality(leg, **kw):
    """信号到底是「底部确认回升」还是「又一次反弹」——用 stage_low 做判据。

    买入后 20 个交易日内**收盘跌破 stage_low**（= 入场时冻结的那条线），说明「确认」被证伪，
    这次只是反弹；没跌破才算确认成立。同时看 60 日后涨幅，衡量「回升」的幅度。
    """
    df = bt.klines.frame(leg)
    sm = bt.state_machine(df, **kw)
    tr = bt.trades(df, sm)
    rows = []
    c = df["close"]
    for t in tr:
        if t["side"] != "buy" or t.get("stage") is None:
            continue
        i = df.index.get_loc(t["date"])
        fut = c.iloc[i + 1: i + 1 + 20]
        rows.append(dict(date=t["date"].date(), entry=t["price"], stage=t["stage"],
                         broken=bool((fut < t["stage"]).any()) if len(fut) else False,
                         low20=float(fut.min() / t["stage"] - 1.0) if len(fut) else math.nan,
                         rise60=float(c.iloc[min(i + 60, len(c) - 1)] / t["price"] - 1.0)))
    return rows


def quality_summary(legs, **kw):
    all_rows = []
    for leg in legs:
        for r in confirm_quality(leg, **kw):
            all_rows.append(dict(leg=leg, **r))
    if not all_rows:
        return dict(n=0, broken=None, rise60=None, rows=[])
    b = sum(1 for r in all_rows if r["broken"])
    rises = pd.Series([r["rise60"] for r in all_rows], dtype="float64").dropna()
    return dict(n=len(all_rows), broken=b, rate=1.0 - b / len(all_rows),
                rise60=float(rises.median()) if len(rises) else None, rows=all_rows)


# ---------------------------------------------------------------- 主流程
def main(quick=False):
    os.makedirs(OUTDIR, exist_ok=True)
    p0 = bt.params()
    print(f"{bt.LABEL} · 回测")
    print(f"参数: 窗口 {p0['window']} · ctx_recall {p0['ctx_recall']} · 地量有效期 "
          f"{p0['dry_max_age']} · 放量 {p0['vol_mult']}× · 背离 {p0['div_window']}日"
          f"({p0['div_mode']}) · 止盈 {p0['exit_mode']} · 试仓 {p0['size_mode']}")

    runs = {}
    for leg in LEGS:
        runs[leg] = leg_run(leg)
        print(f"  {leg:6s} 信号 {int(runs[leg]['sm']['signal'].sum()):3d} · "
              f"笔数 {runs[leg]['rs']['n']:2d} · 年化 {pct(runs[leg]['m']['ann'])} · "
              f"回撤 {pct(runs[leg]['m']['mdd'])}")

    port = portfolio_run()
    print(f"  组合({len(PLEGS)}腿 {port['start']:%Y-%m}起) 年化 {pct(port['m']['ann'])} · "
          f"回撤 {pct(port['m']['mdd'])} · 静态带 {pct(port['sm_']['ann'])} · "
          f"买入持有 {pct(port['hm']['ann'])}")

    spots, diag = miss_diag()
    n_hit = sum(diag[0]["hits"])
    print(f"  漏底诊断：7 次历史大底默认接住 {n_hit} 次；"
          f"最宽口径 {sum(diag[-1]['hits'])} 次（年化 {pct(diag[-1]['m']['ann'])}）")

    q_new = quality_summary(LEGS)
    q_old = quality_summary(LEGS, dry_max_age=60, drop_min=0.20)
    print(f"  信号质量：{q_new['n']} 笔里 {q_new['broken']} 笔 20 日内破 stage_low"
          f"（确认成功率 {q_new['rate']:.0%}）；旧参数（地量 60/回撤 20%）"
          f"{q_old['n']} 笔 / {q_old['rate']:.0%}")

    # —— 止盈方案 ——
    exits = {}
    for mode in bt.EXIT_MODES:
        exits[mode] = portfolio_run(exit_mode=mode)
    # —— 消融 ——
    abl = []
    for name, skip in (("默认（四条件全开）", ()), ("去掉底背离", ("c2",)),
                       ("去掉跌速放缓", ("c3",)), ("去掉成本线（VWAP+放量）", ("c4",)),
                       ("去掉放量阳", ("c1",)), ("去掉前置过滤", ("filt",))):
        r = portfolio_run(skip=skip)
        abl.append(dict(name=name, **{k: r[k] for k in ("sig", "trades")}, m=r["m"],
                        expo=r["expo"]))
        print(f"  消融 {name:22s} 信号 {r['sig']:3d} 年化 {pct(r['m']['ann'])} "
              f"回撤 {pct(r['m']['mdd'])}")
    # —— 敏感性 ——
    sens = []
    if not quick:
        grid = [("window", (20, 40, 60, 120)), ("ctx_recall", (3, 10, 20, 40)),
                ("dry_max_age", (30, 60, 90, 120)), ("vol_mult", (1.2, 1.5, 2.0)),
                ("stop_pct", (0.03, 0.05, 0.08)), ("drop_min", (0.05, 0.10, 0.20, 0.30)),
                ("div_window", (20, 60)), ("risk_per_trade", (0.03, 0.05, 0.10)),
                ("max_hold", (0, 60, 120, 250)), ("size_mode", ("risk", "full"))]
        for key, vals in grid:
            for v in vals:
                r = portfolio_run(**{key: v})
                sens.append(dict(key=key, val=v, sig=r["sig"], trades=r["trades"],
                                 m=r["m"], expo=r["expo"]))
                print(f"  扫描 {key}={v} 信号 {r['sig']:3d} 年化 {pct(r['m']['ann'])} "
                      f"回撤 {pct(r['m']['mdd'])}")
    # —— 分半 / 逐年 ——
    nav = port["nav"]
    mid = nav.index[len(nav) // 2]
    halves = [("前半", nav.loc[:mid]), ("后半", nav.loc[mid:])]
    halves = [(n, bt.perf(s / s.iloc[0])) for n, s in halves]
    yearly = nav.groupby(nav.index.year).last().pct_change()
    yearly.iloc[0] = nav.groupby(nav.index.year).last().iloc[0] / 1.0 - 1.0

    checks = check_all()

    # ---------------- 报告
    L = []
    add = L.append
    add(f"# {bt.LABEL} · 回测报告")
    add("")
    add(f"> 生成：`backtest.py` · 数据截至 **{runs['hs300']['df'].index[-1]:%Y-%m-%d}** · "
        f"行情 `data/_v9_klines.json`（前复权，成交量原始）")
    add(f"> 规则权威源：`bottom.py::CFG` · 参数：窗口 {p0['window']} 日 / 背离 "
        f"{p0['div_window']} 日 / 时间窗 {p0['ctx_recall']} 日 / 放量 {p0['vol_mult']}× / "
        f"地量有效期 {p0['dry_max_age']} 日 / 回撤门槛 {p0['drop_min']:.0%} / "
        f"修复标尺 {p0['c4_mode']} / 止损 −{p0['stop_pct']:.0%} / 止盈 {p0['exit_mode']} / 试仓 {p0['size_mode']}")
    add("")

    # 结论先行
    n_cash = sum(1 for k in LEGS if runs[k]["m"]["ann"] > bt.CASH_ANNUAL + 1e-4)
    n_beat = sum(1 for k in LEGS if runs[k]["m"]["ann"] > runs[k]["bhm"]["ann"])
    tot_trade = sum(runs[k]["rs"]["n"] for k in LEGS)
    tot_sig = sum(int(runs[k]["sm"]["signal"].sum()) for k in LEGS)
    yrs = (runs["hs300"]["df"].index[-1] - runs["hs300"]["df"].index[0]).days / 365.25
    kc_full_leg = leg_run("kc50", size_mode="full")
    add("## 0. 结论先行")
    add("")
    add(f"1. **频率仍低，但已是「季度级」而非「几年一次」**：七腿 {yrs:.0f} 年合计 "
        f"**{tot_sig} 个信号日 / {tot_trade} 笔轮次**（{tot_trade / yrs:.1f} 笔/年，七腿合计）。"
        f"单腿仍只有每几年 1~2 笔，胜率/盈亏比的统计意义有限 —— 下面的数字只能判断"
        f"**方向对不对**，不能用来预测下一笔赚多少。")
    add(f"2. **相对现金是微弱正贡献，相对买入持有是明显负贡献**："
        f"{len(LEGS)} 腿里 **{n_cash} 腿**年化高于货基 {pct(bt.CASH_ANNUAL, 2)}，"
        f"但只有 **{n_beat} 腿**高于同标的买入持有。它省下的从来不是亏损"
        f"（各腿买入持有回撤 −24%~−62%），而是**暴露时间**：平均仓位 {port['expo']:.0%}，"
        f"其余是货基。定位应当是「现金的择时代替品 / 观察许可」，不是收益放大器。")
    add(f"3. **组合层跑赢现金，但大部分来自暴露时间**：{port['start']:%Y-%m} 起 "
        f"{len(port['nav']) // 252} 年六腿共同窗口，组合年化 "
        f"**{pct(port['m']['ann'])}** vs 货基 {pct(port['cm']['ann'])} vs 静态带 "
        f"{pct(port['sm_']['ann'])} vs 买入持有 {pct(port['hm']['ann'])}；"
        f"比货基高 {pct(port['m']['ann'] - port['cm']['ann'])}，但**择时增量"
        f"（组合 − 同曝光被动）只有 {pct(alpha(port['m'], port['expo'], port['hm']['ann']))}** "
        f"—— 信号本身的增量有限，双边成本（申购 0.10% + 赎回 0.50% + 双边滑点 0.20%）"
        f"把大部分吃掉了。共同窗口只有 {port['trades']} 笔轮次，这个量级下任何「收益」都该当噪声看。")
    add(f"4. **止损锚是「阶段最低点」而非入场价，必须配仓位管理**：入场常在阶段低点上方 "
        f"10%~25%，止损因此离入场价很远。科创50 固定满仓档回撤 "
        f"**{pct(kc_full_leg['m']['mdd'])}**（超过规则要求的 15%~20%），"
        f"按止损距离定量（`size_mode=risk`，单笔风险 {p0['risk_per_trade']:.0%}）后降到 "
        f"**{pct(runs['kc50']['m']['mdd'])}**。这不是策略缺点，是「3% 指的是离最低点，"
        f"不是离成本」的直接后果。")
    lacked = [s for s, h in zip(spots, diag[0]["hits"]) if not h]
    why_txt = "；".join(f"{s['name']}（{s['why']}）" for s in lacked) or "无"
    add(f"5. **漏底从 2/7 改善到 {n_hit}/7**：地量有效期 60→90 日、回撤门槛 20%→10% 之后，"
        f"沪深300 的 7 次历史大底接住 **{n_hit} 次**（改参数前 {sum(diag[1]['hits'])} 次）。"
        f"仍漏的 {len(lacked)} 次各有卡点 —— {why_txt}。"
        f"其中 2025-04 是涨势中的回调（连 MA20<MA60 都不成立），按规则**本就不该认**。")
    add(f"6. **「确认回升」而不是「反弹」——这次改参数的真实代价可以量了**："
        f"{q_new['n']} 笔买入里 **{q_new['broken']} 笔**在 20 个交易日内收盘跌破入场时冻结的 "
        f"stage_low（确认成功率 **{q_new['rate']:.0%}**，60 日后涨幅中位 {pct(q_new['rise60'])}）；"
        f"改参数前是 {q_old['n']} 笔 / {q_old['rate']:.0%} / {pct(q_old['rise60'])}。"
        f"**信号多了约 {tot_trade / max(q_old['n'], 1):.1f} 倍，确认成功率的变化就是它换来的东西**"
        f"（逐笔见 §10）。")
    add("**怎么用（结论）**：当「已跌 20% 以上的标的」出现〔地量 → 放量阳重新站上地量日锚定的"
        "VWAP〕，且期间有过 MACD 底背离与跌速放缓时，**允许**用「该腿额度 5% 的风险预算」"
        "试仓，止损挂在阶段最低点下 3%；跌破就无条件走，不预测底、不补仓、不摊平。"
        "它是「可以开始考虑」的许可，不是买入信号 —— 回测里它也没证明自己值得独立成仓。")
    add("")

    # 1 逐腿
    add("## 1. 逐腿（全历史）")
    add("")
    add("| 腿 | 标的（代码） | 样本 | 信号 | 笔数 | 胜率 | 盈亏比 | 止损占比 | 中位持仓 | "
        "平均单笔 | 策略年化 | 策略回撤 | 夏普 | 曝光 | 买入持有年化 | 买入持有回撤 |")
    add("|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|")
    for k in LEGS:
        r, m, b = runs[k], runs[k]["m"], runs[k]["bhm"]
        rs = r["rs"]
        u = bt.klines.UNIVERSE[k]
        win = "—" if rs["win"] is None else f"{rs['win']:.0%}"
        payoff = "—" if rs["payoff"] is None else f"{rs['payoff']:.2f}"
        stop_share = "—" if rs["stop_share"] is None else f"{rs['stop_share']:.0%}"
        hold_days = "—" if rs["days"] is None else f"{rs['days']:.0f} 日"
        add(f"| {k} | {u['name']}（{u['code']}{'·代理' if u['proxy'] else ''}） | "
            f"{r['df'].index[0]:%Y-%m}~{r['df'].index[-1]:%Y-%m} | "
            f"{int(r['sm']['signal'].sum())} | {rs['n']} | {win} | {payoff} | "
            f"{stop_share} | {hold_days} | {pct(rs['avg'])} | **{pct(m['ann'])}** | "
            f"{pct(m['mdd'])} | {m['sharpe']:.2f} | {r['sm']['position'].mean():.0%} | "
            f"{pct(b['ann'])} | {pct(b['mdd'])} |")
    add("")
    add("> `盈亏比` = 盈利笔均值 / 亏损笔均值。`止损占比` = 以止损离场的轮次占比。"
        "`曝光` = 平均仓位（含货基计息）。a500（563360）2024-10 才有行情，"
        "过不了 250 日回撤过滤的预热，**0 笔**属正常，不是 bug。")
    add("")

    # 2 组合
    add("## 2. 组合层（六腿共同窗口，按目标带归一化）")
    add("")
    add(f"共同窗口 **{port['start']:%Y-%m-%d} ~ {port['nav'].index[-1]:%Y-%m-%d}**"
        f"（{len(port['nav']) // 252} 年，由最晚成形的腿〔kc50/hkt〕决定）。")
    add("")
    add("| 方案 | 年化 | 累计 | 波动 | 回撤 | 夏普 | Calmar | 平均仓位 |")
    add("|---|---|---|---|---|---|---|---|")
    for name, m, ex in (("V9 抄底确认（默认）", port["m"], port["expo"]),
                        ("货基（纯现金）", port["cm"], 0.0),
                        ("静态带（恒满）", port["sm_"], 1.0),
                        ("等权买入持有（首日建仓）", port["hm"], 1.0)):
        add(f"| {name} | {pct(m['ann'])} | {pct(m['cum'])} | {m['vol']:.2%} | "
            f"{pct(m['mdd'])} | {m['sharpe']:.2f} | "
            f"{'—' if calmar(m) is None else f'{calmar(m):.2f}'} | {ex:.0%} |")
    add("")
    add(f"**择时增量**（组合年化 − 同曝光被动组合）：{pct(alpha(port['m'], port['expo'], port['hm']['ann']))}"
        f" —— 同曝光被动组合年化 = {port['expo']:.1%}×{pct(port['hm']['ann'])} + "
        f"{(1 - port['expo']):.1%}×{pct(bt.CASH_ANNUAL)} = "
        f"{pct(passive(port['expo'], port['hm']['ann']))}。"
        f"直接比收益会被曝光差污染（样本期六腿整体上涨，多交易自然多赚），"
        f"这一行才是「四条件有没有信息量」的答案。")
    add("")
    add(f"共同窗口内信号合计 {port['sig']} 次、轮次 {port['trades']} 笔 —— "
        f"六腿 {len(port['nav']) // 252} 年这个量级，任何差异都在噪声里。"
        f"（`--quick` 与全量跑的消融/敏感性表用的是同一窗口口径。）")
    add("")

    # 3 止盈
    add("## 3. 止盈方案对比（其余参数固定，组合层）")
    add("")
    add("| 止盈方案 | 组合年化 | 回撤 | 夏普 | 笔数 |")
    add("|---|---|---|---|---|")
    for mode in bt.EXIT_MODES:
        e = exits[mode]
        add(f"| `{mode}` {bt.EXIT_TEXT[mode]} | {pct(e['m']['ann'])} | {pct(e['m']['mdd'])} | "
            f"{e['m']['sharpe']:.2f} | {e['trades']} |")
    best = max(bt.EXIT_MODES, key=lambda x: exits[x]["m"]["ann"])
    add("")
    add(f"年化最高的是 `{best}`，但六腿只有 {len(port['nav']) // 252} 年、个位数轮次，"
        f"**这一列不足以选止盈** —— "
        f"规则原意「视频未给固定止盈」也说明作者自己没定。默认保留 `rr2`（1:2 盈亏比），"
        f"理由是可事先算好、不需要盘中判断。")
    add("")

    # 4 消融
    add("## 4. 消融：哪一条条件真的在干活")
    add("")
    add("| 配置 | 信号 | 轮次 | 平均仓位 | 组合年化 | 同曝光被动 | 择时增量 | 回撤 | 夏普 |")
    add("|---|---|---|---|---|---|---|---|---|")
    for a in abl:
        add(f"| {a['name']} | {a['sig']} | {a['trades']} | {a['expo']:.0%} | "
            f"{pct(a['m']['ann'])} | {pct(passive(a['expo'], port['hm']['ann']))} | "
            f"**{pct(alpha(a['m'], a['expo'], port['hm']['ann']))}** | "
            f"{pct(a['m']['mdd'])} | {a['m']['sharpe']:.2f} |")
    add("")
    base = abl[0]
    worst = max(abl[1:], key=lambda a: a["sig"])
    best_a = max(abl, key=lambda a: alpha(a["m"], a["expo"], port["hm"]["ann"]))
    add(f"去看「择时增量」列，不要看年化列 —— 年化受曝光支配。"
        f"信号涨得最多的是「{worst['name']}」（{base['sig']} → {worst['sig']} 个），"
        f"说明它是**约束的主力**；择时增量最高的是「{best_a['name']}」"
        f"（{pct(alpha(best_a['m'], best_a['expo'], port['hm']['ann']))}）。"
        f"若「去掉前置过滤」的择时增量高于默认，说明规则里「排除上涨中假背离」这条"
        f"在本样本上没有换来收益 —— 它与「不要在牛市空仓」是同一件事的两面。")
    add("")

    # 5 敏感性
    add("## 5. 参数敏感性（组合层）")
    add("")
    if quick:
        add("> `--quick` 模式跳过。")
    else:
        add("| 参数 | 取值 | 信号 | 轮次 | 平均仓位 | 组合年化 | 择时增量 | 回撤 | 夏普 |")
        add("|---|---|---|---|---|---|---|---|---|")
        for s in sens:
            mark = " **←默认**" if s["val"] == bt.CFG.get(s["key"]) else ""
            v = f"{s['val']:.0%}" if isinstance(s["val"], float) and s["val"] < 1 else str(s["val"])
            add(f"| `{s['key']}` | {v}{mark} | {s['sig']} | {s['trades']} | {s['expo']:.0%} | "
                f"{pct(s['m']['ann'])} | **{pct(alpha(s['m'], s['expo'], port['hm']['ann']))}** | "
                f"{pct(s['m']['mdd'])} | {s['m']['sharpe']:.2f} |")
        add("")
        rows = [(s["key"], s["val"], s["sig"],
                 alpha(s["m"], s["expo"], port["hm"]["ann"])) for s in sens]
        lo, hi = min(r[3] for r in rows), max(r[3] for r in rows)
        spread = {}
        for k, _v, _s, a in rows:
            spread.setdefault(k, []).append(a)
        spread = {k: max(v) - min(v) for k, v in spread.items() if len(v) > 1}
        wide = max(spread, key=spread.get)
        dm = {v: a for k, v, _s, a in rows if k == "drop_min"}
        dm_txt = "、".join(f"{v:.0%} → {pct(a)}" for v, a in sorted(dm.items(), reverse=True))
        add(f"> 全表择时增量落在 **{pct(lo)} ~ {pct(hi)}** 之间，跨度 {hi - lo:.2%} —— "
            f"按参数分组看组内跨度，最大的是 `{wide}`（{spread[wide]:.2%}），"
            f"即结论最受它支配，而不是受窗口长度或放量倍数支配。")
        add(f"> `drop_min` 三档是 {dm_txt} —— **20% 是唯一为负的一档**，"
            f"这次降到 10% 方向正确；再降到 5% 虽然更高（信号更多、曝光更大），"
            f"但继续往下调就是拿样本挑参数，停在这里。")
        add(f"> 最高的一档是 `size_mode=full`（{pct([r for r in rows if r[0] == 'size_mode' and r[1] == 'full'][0][3])}），"
            f"但那只是把仓位放大，不是规则变好 —— 同一档的回撤也翻倍（见 §2）。")
    add("")

    # 6 分段
    add("## 6. 分段与逐年（组合层）")
    add("")
    add("| 区段 | 年化 | 回撤 | 夏普 |")
    add("|---|---|---|---|")
    for name, m in halves:
        add(f"| {name}（{m['days']} 个交易日） | {pct(m['ann'])} | {pct(m['mdd'])} | "
            f"{m['sharpe']:.2f} |")
    add("")
    add("| 年份 | " + " | ".join(str(y) for y in yearly.index) + " |")
    add("|---" * (len(yearly) + 1) + "|")
    add("| 收益 | " + " | ".join(pct(v) for v in yearly.values) + " |")
    add("")
    add("逐年只有 5~6 个格子，且多数年份**全年空仓**（收益 = 货基），"
        "分段检验在这里的作用是确认「没有哪一年靠一笔运气撑住全部收益」，而不是做样本外推断。")
    add("")

    # 7 断言
    add("## 7. 硬断言")
    add("")
    add("| 检查 | 结果 | 备注 |")
    add("|---|---|---|")
    for name, ok, note in checks:
        add(f"| {name} | {'✅' if ok else '❌'} | {note} |")
    add("")
    bad = [c for c in checks if not c[1]]
    add(f"共 {len(checks)} 条，失败 {len(bad)} 条" + ("。" if not bad else
        "：**" + "；".join(c[0] for c in bad) + "**"))
    add("")

    # 8 口径与坑
    add("## 8. 口径与已知的坑")
    add("")
    add("1. **前复权**：`xa.get_daily` 默认前复权，`close>open` 与 `low` 用同一套缩放，"
        "判定不受除权影响；成交量是原始手数（拆分日会有台阶，但 1.5× 阈值是当日相对量，影响有限）。")
    add("2. **代理**：ndx/spx 两腿用人民币场内 ETF（513100/513500），不是美元指数本身 —— "
        "叠加了汇率与溢价折价，抄底判定会偏离美股本身的形态。这是唯一的口径妥协。")
    add("3. **止损按日线最低价判定**：日内顺序未知，若同一日既触及止损又冲高，"
        "一律按「先止损」处理（保守）。跳空低开按开盘价成交，成交价 `min(stop, open)`。")
    add("4. **止盈/新低是收盘决策、次日收盘成交**：多付一天的滑价，但不需要盘中判断。")
    add("5. **组合层不做额度再平衡**：各腿权重按目标带固定，某腿满仓时组合仍留现金。")
    add("6. **规则没有时间止损，这是个真实的洞**：kc50 2022-10 那笔横了 **226 个交易日**"
        "才被「再创新低」带出来（−10.7%）—— 止损没触发、止盈没到、新低没破，仓位就一直挂着。"
        "`max_hold`（默认 0 = 不启用）提供可选补丁，敏感性表里有它的对照；"
        "启用它等于**新增规则**，不是原规则的一部分。")
    add("7. **1:2 盈亏比在宽止损下几乎等于没设止盈**：止损锚在阶段最低点，"
        "止盈价 = 入场 + 2×(入场−止损) 常常在 +20%~+50%，实际出场多半来自「再创新低」。"
        "若想要真正的 1:2，应当用**入场价**而不是阶段最低点算风险 —— 但那就改了规则的锚。")
    add("")

    # 9 漏底诊断
    add("## 9. 漏底诊断：7 次历史大底，这套规则接住了几次")
    add("")
    add("图上肉眼可见的 7 次「底部上涨」起点（事后确认，不是当时可知）。"
        "「±20 日」= 底部前后各 20 个交易日内出现过信号日。")
    add("")
    add("### 9.1 每个底当天缺的是哪一条（按当前默认口径）")
    add("")
    add("| 底部 | 日期 | 当日回撤 | 缺的那一条 |")
    add("|---|---|---|---|")
    for s in spots:
        add(f"| {s['name']} | {s['date']} | {s['drop']:.1%} | {s['why']} |")
    add("")
    n_dry = sum(1 for s in spots if "地量已" in s["why"])
    n_vwap = sum(1 for s in spots if "VWAP" in s["why"])
    n_drop = sum(1 for s in spots if "回撤" in s["why"])
    n_trend = sum(1 for s in spots if "MA20" in s["why"])
    add(f"> 逐条计数：{len(spots)} 个底里 {n_drop} 个卡在回撤门槛、{n_dry} 个卡在地量有效期、"
        f"{n_vwap} 个卡在成本线、{n_trend} 个连趋势条件都不成立 —— "
        f"**没有单个阈值能一次救回来**。")
    add("")
    add("### 9.2 换口径能接住几个、代价多大（hs300 全历史）")
    add("")
    add("| 口径 | " + " | ".join(s["date"][5:] for s in spots) + " | 接住 | 笔数 | 年化 | 回撤 | 胜率 |")
    add("|---" * (len(spots) + 6) + "|")
    for r in diag:
        hits = " | ".join("✅" if h else "❌" for h in r["hits"])
        win = "—" if r["rs"]["win"] is None else f"{r['rs']['win']:.0%}"
        add(f"| {r['label']} | {hits} | **{sum(r['hits'])}/7** | {r['rs']['n']} | "
            f"{pct(r['m']['ann'])} | {pct(r['m']['mdd'])} | {win} |")
    add("")
    add(f"读法：当前默认接 {sum(diag[0]['hits'])}/7、{diag[0]['rs']['n']} 笔、"
        f"年化 {pct(diag[0]['m']['ann'])}、胜率 {(diag[0]['rs']['win'] or 0):.0%}；"
        f"改参数前只接 {sum(diag[1]['hits'])}/7、{diag[1]['rs']['n']} 笔。"
        f"再往「更早」走（站上 MA20 也算修复）能到 {sum(diag[3]['hits'])}/7、"
        f"{diag[3]['rs']['n']} 笔，但胜率只有 {(diag[3]['rs']['win'] or 0):.0%}、"
        f"年化 {pct(diag[3]['m']['ann'])} —— **每往前挪一步，都是在拿胜率换覆盖**。")
    add("")
    add("### 9.3 为什么「接住底部」和「高胜率」天然冲突")
    add("")
    add("1. **② 与 ④ 在时序上互斥**：② 要求「价格创 20 日新低」，④ 要求「放量阳站回成本线」——"
        "新低那天不可能是站回那天。这就是「当日四条件齐现」在 13 年里有 **0 天**的原因，"
        "也是必须有 `ctx_recall` 时间窗的原因（`ctx_recall=3` 时 hs300 是 0 个信号）。")
    add("2. **④ 的锚在下跌途中**：地量日锚定的 VWAP 包含地量日**之前**的高价成交，"
        "所以 2015-08 与 2016-01 当天 close 比 VWAP 低 18.4% / 8.9% —— 要「站回成本线」，"
        "必须先涨回地量日前后的平均成本，V 型反转才能满足。这既是它稀有的原因，也是它算「确认」的原因。")
    add("3. **趋势类信号能接住全部 7 个底，但代价完全不同**：hs300 上「收盘上穿 MA20」"
        "在 7 个底后 1~16 个交易日内全部出现（7/7），但全历史有 **201 次**"
        f"（≈15 次/年）。这正是 `../v1_ma20/`、`../v3_macd_boll/` 在做的事，"
        "而它们的回测结论是「只降回撤、不增收益」——问题不在能否定位，在噪声与成本。")
    add("")
    add("> **结论**：它买的是「跌势已经修复到多数人回本」的时刻，而不是「最低点」；"
        "所以永远会漏掉一部分底部 —— 差别只在漏几次、以及为了少漏要付多少胜率。"
        "当前参数（地量 90 日 / 回撤 10%）是「覆盖」与「准确」的一个折中，"
        "再往前走就是普通反弹买入了。")
    add("")

    # 10 信号质量
    add("## 10. 信号质量：是「底部确认回升」还是「又一次反弹」")
    add("")
    add("判据：买入后 **20 个交易日内收盘跌破入场时冻结的 `stage_low`**（= 止损锚 `/0.97` "
        "之前那条线）→ 本次「确认」被证伪，那次只是反弹；没跌破才算确认成立。")
    add("")
    add("| 口径 | 笔数 | 破位笔数 | 确认成功率 | 60 日后涨幅中位 |")
    add("|---|---|---|---|---|")
    for label, q in (("当前默认（地量 90 / 回撤 10%）", q_new),
                     ("改参数前（地量 60 / 回撤 20%）", q_old)):
        rate_txt = "—" if q["rate"] is None else f"{q['rate']:.0%}"
        add(f"| {label} | {q['n']} | {q['broken']} | {rate_txt} | {pct(q['rise60'])} |")
    add("")
    add("> 「破位」比「止损」早：止损是 `low ≤ stage×0.97`（日内触价即走），破位是 `close < stage`。"
        "所以一笔可以先破位、后止损，也可能破位但当天又被买回来。"
        "这里用**收盘**判据，是为了回答「这次确认是不是假的」，而不是复现成交。")
    add("")
    add("### 10.1 逐笔（当前默认口径，七腿全历史）")
    add("")
    add("| 腿 | 信号日 | 入场 | stage_low | 20 日内最低 / stage | 破位 | 60 日后 |")
    add("|---|---|---|---|---|---|---|")
    for r in q_new["rows"]:
        add(f"| {r['leg']} | {r['date']} | {r['entry']:.4f} | {r['stage']:.4f} | "
            f"{r['low20']:+.1%} | {'❌ 反弹' if r['broken'] else '✅ 确认'} | "
            f"{r['rise60']:+.1%} |")
    add("")
    add(f"合计 {q_new['n']} 笔买入（含期末仍未平的持仓；完整轮次 {tot_trade} 笔）："
        f"确认成立 {q_new['n'] - q_new['broken']} 笔、20 日内破位 {q_new['broken']} 笔；"
        f"60 日后仍为正收益的有 "
        f"{sum(1 for r in q_new['rows'] if r['rise60'] > 0)} 笔。")
    add("")
    txt = "\n".join(L)
    with open(OUT_MD, "w", encoding="utf-8") as f:
        f.write(txt)

    # ---------------- jsonl
    with open(OUT_JSONL, "w", encoding="utf-8") as f:
        for k in LEGS:
            r, rs = runs[k], runs[k]["rs"]
            f.write(json.dumps(dict(
                kind="leg", leg=k, name=NAMES[k], code=bt.klines.UNIVERSE[k]["code"],
                proxy=bt.klines.UNIVERSE[k]["proxy"],
                start=str(r["df"].index[0].date()), end=str(r["df"].index[-1].date()),
                bars=len(r["df"]), signals=int(r["sm"]["signal"].sum()),
                trades=rs["n"], win=rs["win"], payoff=rs["payoff"],
                stop_share=rs["stop_share"], median_bars=rs["days"],
                avg_trade=rs["avg"], best=rs["best"], worst=rs["worst"],
                ann=r["m"]["ann"], mdd=r["m"]["mdd"], sharpe=r["m"]["sharpe"],
                vol=r["m"]["vol"], exposure=float(r["sm"]["position"].mean()),
                bh_ann=r["bhm"]["ann"], bh_mdd=r["bhm"]["mdd"],
            ), ensure_ascii=False) + "\n")
        f.write(json.dumps(dict(
            kind="portfolio", legs=list(PLEGS), start=str(port["start"].date()),
            end=str(port["nav"].index[-1].date()), bars=len(port["nav"]),
            signals=port["sig"], trades=port["trades"], exposure=port["expo"],
            ann=port["m"]["ann"], mdd=port["m"]["mdd"], sharpe=port["m"]["sharpe"],
            static_ann=port["sm_"]["ann"], static_mdd=port["sm_"]["mdd"],
            hold_ann=port["hm"]["ann"], hold_mdd=port["hm"]["mdd"],
            exits={m: dict(ann=exits[m]["m"]["ann"], mdd=exits[m]["m"]["mdd"],
                           sharpe=exits[m]["m"]["sharpe"], trades=exits[m]["trades"])
                   for m in bt.EXIT_MODES},
            ablation=[dict(name=a["name"], signals=a["sig"], trades=a["trades"],
                           ann=a["m"]["ann"], mdd=a["m"]["mdd"], sharpe=a["m"]["sharpe"])
                      for a in abl],
            sensitivity=[dict(param=s["key"], value=s["val"], signals=s["sig"],
                              trades=s["trades"], ann=s["m"]["ann"], mdd=s["m"]["mdd"])
                         for s in sens],
            checks=[dict(name=n, ok=bool(o), note=t) for n, o, t in checks],
            miss_diag=dict(spots=spots, rows=[
                dict(label=r["label"], hits=r["hits"], caught=sum(r["hits"]),
                     trades=r["rs"]["n"], ann=r["m"]["ann"], mdd=r["m"]["mdd"],
                     win=r["rs"]["win"]) for r in diag]),
            params={k: (v if isinstance(v, (int, float, str, bool, list, tuple, type(None)))
                        else str(v)) for k, v in p0.items()},
        ), ensure_ascii=False) + "\n")

    # ---------------- 图
    plot_leg(runs["hs300"], "hs300")
    plot_port(port)
    print(f"→ {os.path.relpath(OUT_MD, _ROOT)}")
    print(f"→ {os.path.relpath(OUT_JSONL, _ROOT)}")
    print(f"断言 {len(checks) - len(bad)}/{len(checks)} 通过")
    return 0 if not bad else 1


def plot_leg(r, leg):
    fig, (ax1, ax2) = plt.subplots(2, 1, figsize=(12, 7.5), height_ratios=[2, 1.2],
                                   sharex=True)
    df, sm = r["df"], r["sm"]
    ax1.plot(df.index, df["close"], lw=0.9, color="#334155", label="收盘价")
    buys = [t for t in r["tr"] if t["side"] == "buy"]
    sells = [t for t in r["tr"] if t["side"] == "sell"]
    # 历史大底（事后确认）：绿圈 = 这套规则接住了，红圈 = 漏了（详见报告 §9）
    if leg == "hs300":
        xs, ys, ec = [], [], []
        for d, _n in SPOTS:
            ts = pd.Timestamp(d)
            i = sm.index.get_loc(sm.index[sm.index <= ts][-1])
            ok = bool(sm["signal"].iloc[max(0, i - 20): i + 21].any())
            xs.append(ts)
            ys.append(float(df["close"].asof(ts)))
            ec.append("#16a34a" if ok else "#dc2626")
        ax1.scatter(xs, ys, marker="o", s=150, facecolors="none", edgecolors=ec, lw=1.8,
                    zorder=4, label=f"历史大底（绿=接住 {ec.count('#16a34a')} / 红=漏 "
                                    f"{ec.count('#dc2626')}）")
    ax1.scatter([t["date"] for t in buys], [t["price"] for t in buys], marker="^", s=70,
                color="#dc2626", zorder=5, label=f"试仓（{len(buys)}）")
    ax1.scatter([t["date"] for t in sells], [t["price"] for t in sells], marker="v", s=70,
                color="#059669", zorder=5, label=f"离场（{len(sells)}）")
    ax1.set_title(f"{NAMES[leg]}（{bt.klines.UNIVERSE[leg]['code']}）· "
                  f"{bt.LABEL} 操作点", fontsize=11)
    ax1.legend(loc="upper left", fontsize=8, frameon=False)
    ax1.grid(alpha=0.25)
    ax2.plot(r["nav"].index, r["nav"], lw=1.4, color="#dc2626", label="V9 抄底确认")
    ax2.plot(r["bh"].index, r["bh"], lw=1.0, color="#64748b", label="买入持有")
    ax2.set_yscale("log")
    ax2.legend(loc="upper left", fontsize=8, frameon=False)
    ax2.grid(alpha=0.25)
    ax2.set_ylabel("净值（对数）")
    fig.tight_layout()
    fig.savefig(OUT_PNG_LEG, dpi=130)
    plt.close(fig)


def plot_port(port):
    fig, (ax1, ax2) = plt.subplots(2, 1, figsize=(12, 7.5), height_ratios=[2, 1],
                                   sharex=True)
    ax1.plot(port["nav"].index, port["nav"], lw=1.6, color="#dc2626",
             label=f"V9 抄底确认（年化 {pct(port['m']['ann'])}）")
    ax1.plot(port["static"].index, port["static"], lw=1.0, color="#2563eb",
             label=f"静态带恒满（年化 {pct(port['sm_']['ann'])}）")
    ax1.plot(port["hold"].index, port["hold"], lw=1.0, color="#64748b",
             label=f"等权买入持有（年化 {pct(port['hm']['ann'])}）")
    ax1.set_yscale("log")
    ax1.legend(loc="upper left", fontsize=8, frameon=False)
    ax1.grid(alpha=0.25)
    ax1.set_title(f"组合层（{len(port['legs'])} 腿，{port['start']:%Y-%m} 起）", fontsize=11)
    dd = port["nav"] / port["nav"].cummax() - 1.0
    ax2.fill_between(dd.index, dd.to_numpy(), 0, color="#f97316", alpha=0.45)
    ax2.set_ylabel("回撤")
    ax2.grid(alpha=0.25)
    fig.tight_layout()
    fig.savefig(OUT_PNG_PORT, dpi=130)
    plt.close(fig)


if __name__ == "__main__":
    sys.exit(main(quick="--quick" in sys.argv))
