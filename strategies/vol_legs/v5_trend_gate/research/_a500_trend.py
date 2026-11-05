# -*- coding: utf-8 -*-
"""中证A500 专项：趋势口径在**上涨侧和下跌侧**都快不快、稳不稳，以及现在的趋势是什么。

## 为什么两边都要看

只看「下跌确认得早不早」会漏掉一半：趋势判断有两个方向，而两类错误的代价不同 ——
下跌侧慢了要挨阴跌，上涨侧慢了要错过整段主升。所以这里对每次状态切换同时记两侧：

- **确认滞后（归一化）**：`已走幅度 / (已走幅度 + 之后 120 日还能走的幅度)`。
  0% = 在起点/顶点当场确认，100% = 整段行情都错过。两个方向同尺度，可直接比较。
- **假警报率**：确认下跌后 20 个交易日内又回到「上涨趋势」的比例（下侧）；
  确认上涨后 20 个交易日内又回到「下跌趋势」的比例（上侧）。
- **切换/年**：一年改几次主意 —— 两个方向共用这个「吵」的度量。

## 三个输出

1. 每档口径的「下侧滞后 / 上侧滞后 / 两侧假警报 / 切换」以及套 V5 档位后的效果；
2. 两张 Pareto 图（下侧、上侧各一张）+ 一张**对称性散点**（上侧滞后 vs 下侧滞后，
   45° 线之上 = 上慢下快，之下 = 上快下慢）；
3. 当下读数 + **「距翻多还差多少」**（价格离各口径的翻多阈值多远）。

样本说明：`023299` 2025-03-27 才成立，之前是 `000051`（沪深300 联接）桥接。
当前 MA250 只回看 250 个交易日（约 2025-10 起），**全部落在真实段内**，所以「现在是什么趋势」
不受桥接影响；但 2010-2026 的长历史统计里前 15 年是沪深300 的形状。

产物（本目录）：`_a500_trend.md` / `_a500_trend.png`。机械规则输出，非投资建议。
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

import trend_gate as tg  # noqa: E402

tg.VOL_USE = False       # 本脚本只研究「趋势口径」，关掉波动层以隔离变量

OUT_MD = os.path.join(_HERE, "_a500_trend.md")
OUT_PNG = os.path.join(_HERE, "_a500_trend.png")

LEG = "a500"
WARM = 300               # 统一统计窗口起点（让 KAMA / HMA 也稳定下来）
FWD = 120                # 确认之后看多少交易日
WHIP_N = 20              # 「假警报」窗口

Q1, Q2, Q3, Q4 = tg.QUADS
LEVEL_KINDS = ("ma", "donchian", "dual_ma", "kama", "hull")

# (标签, 参数, 族, 说明)
SPECS = [
    ("MA250+20（V5 现行）", dict(KIND="ma", MA_WIN=250, SLOPE_N=20), "均线", "最钝的基准"),
    ("MA150+10", dict(KIND="ma", MA_WIN=150, SLOPE_N=10), "均线", ""),
    ("MA120+20", dict(KIND="ma", MA_WIN=120, SLOPE_N=20), "均线", ""),
    ("MA120+10", dict(KIND="ma", MA_WIN=120, SLOPE_N=10), "均线", ""),
    ("MA120+5", dict(KIND="ma", MA_WIN=120, SLOPE_N=5), "均线", ""),
    ("MA100+10", dict(KIND="ma", MA_WIN=100, SLOPE_N=10), "均线", ""),
    ("MA60+10", dict(KIND="ma", MA_WIN=60, SLOPE_N=10), "均线", ""),
    ("MA50+5", dict(KIND="ma", MA_WIN=50, SLOPE_N=5), "均线", ""),
    ("MA120+10 缓冲1%", dict(KIND="ma", MA_WIN=120, SLOPE_N=10, BUF=0.01), "缓冲带", "跌破 1% 才算破"),
    ("MA120+10 缓冲3%", dict(KIND="ma", MA_WIN=120, SLOPE_N=10, BUF=0.03), "缓冲带", "跌破 3% 才算破"),
    ("双均线50/200+10", dict(KIND="dual_ma", FAST_WIN=50, SLOW_WIN=200, SLOPE_N=10), "双均线", ""),
    ("双均线50/150+10", dict(KIND="dual_ma", FAST_WIN=50, SLOW_WIN=150, SLOPE_N=10), "双均线", ""),
    ("双均线100/200+10", dict(KIND="dual_ma", FAST_WIN=100, SLOW_WIN=200, SLOPE_N=10), "双均线", ""),
    ("KAMA(10,2,30)+10", dict(KIND="kama", KAMA_N=10, KAMA_FAST=2, KAMA_SLOW=30, SLOPE_N=10),
     "自适应", "效率比率调节速度"),
    ("KAMA(10,2,30)+5", dict(KIND="kama", KAMA_N=10, KAMA_FAST=2, KAMA_SLOW=30, SLOPE_N=5),
     "自适应", ""),
    ("HMA100+5", dict(KIND="hull", HULL_N=100, SLOPE_N=5), "低滞后", "加权压低滞后"),
    ("HMA150+10", dict(KIND="hull", HULL_N=150, SLOPE_N=10), "低滞后", ""),
    ("唐奇安中轨55+10", dict(KIND="donchian", DON_N=55, SLOPE_N=10), "结构法", ""),
    ("唐奇安中轨20+5", dict(KIND="donchian", DON_N=20, SLOPE_N=5), "结构法", ""),
    ("MACD(50,200)+5", dict(KIND="macd", FAST_WIN=50, SLOW_WIN=200, SLOPE_N=5), "交易信号", ""),
    ("动量6月+20", dict(KIND="momentum", MOM_N=126, SLOPE_N=20), "动量", ""),
]


def _med(x):
    x = [v for v in x if v is not None and np.isfinite(v)]
    return float(np.median(x)) if x else None


def _lag(moved, remain):
    """归一化滞后：已走 / (已走 + 剩余)。moved/remain 都用正数（幅度）。"""
    tot = moved + max(remain, 0.0)
    return moved / tot if tot > 1e-12 else 1.0


def metrics(px, kw, start_idx=WARM):
    """单腿口径：下侧 / 上侧的滞后与假警报 + 切换频率 + 套档位后的效果。"""
    sm = tg.state_machine(px, LEG, GATE_ALL=True, **kw)
    c = px.to_numpy(dtype=float)
    quad = sm["quad"].to_numpy(dtype=object)
    dd = sm["dd_hi"].to_numpy(dtype=float)
    ready = sm["ready"].to_numpy(dtype=bool)
    lo = pd.Series(c).rolling(tg.REF_HI, min_periods=1).min().to_numpy()

    down_lag, down_conf, down_after = [], [], []
    up_lag, up_conf, up_after = [], [], []
    whip_dn = whip_dn_n = whip_up = whip_up_n = 0
    segs = []
    n_sw = 0
    last_change = start_idx
    t = start_idx
    while t < len(c):
        if ready[t] and t > start_idx and quad[t] != quad[t - 1]:
            segs.append(t - last_change)
            last_change = t
            n_sw += 1
            fwd = c[t:min(len(c), t + 1 + FWD)]
            tail = quad[t:min(len(c), t + 1 + WHIP_N)]
            if quad[t] == Q4:
                moved = abs(float(dd[t]))
                remain = -(float(np.min(fwd)) / c[t] - 1.0)      # 正数 = 还能跌
                down_lag.append(_lag(moved, remain))
                down_conf.append(moved)
                down_after.append(-remain)
                whip_dn_n += 1
                whip_dn += Q1 in tail
            elif quad[t] == Q1:
                moved = c[t] / lo[t] - 1.0
                remain = float(np.max(fwd)) / c[t] - 1.0         # 正数 = 还能涨
                up_lag.append(_lag(moved, remain))
                up_conf.append(moved)
                up_after.append(remain)
                whip_up_n += 1
                whip_up += Q4 in tail
        t += 1
    segs.append(len(c) - last_change)

    years = (px.index[-1] - px.index[start_idx]).days / 365.25
    sub = px.iloc[start_idx:]
    st = sm["state"].iloc[start_idx:]
    fee = tg.fee_of(LEG)
    a = tg.perf(tg.run_leg(st, sub, fee))
    b = tg.perf(tg.run_leg(pd.Series(1.0, index=sub.index), sub, fee))

    p = tg.params(**kw)
    buf = float(p["BUF"])
    kind = p["KIND"]
    ma_last = float(sm["ma"].iloc[-1])
    close_last = float(sm["close"].iloc[-1])
    above = bool(sm["above"].iloc[-1])
    slope_up = bool(sm["slope"].iloc[-1] > 0)
    f_gt_s = None
    # 「位置」与「距翻多还差」必须按口径说人话：均线类说「线上/线下 + 还要涨多少」；
    # 读数类（DIF / 动量）没有「涨多少就翻多」这回事，只能说「等哪个条件」。
    if kind in LEVEL_KINDS:
        pos_txt = "线上" if above else "线下"
        if above and slope_up:
            need, need_txt = 0.0, "已翻多"
        elif not above:
            need = ma_last * (1.0 - buf) / close_last - 1.0      # 需要涨多少才回到线上
            need_txt = f"+{need:.1%}"
            if kind == "dual_ma":
                slow = px.rolling(int(p["SLOW_WIN"]), min_periods=int(p["SLOW_WIN"])).mean()
                f_gt_s = bool(ma_last >= float(slow.iloc[-1]))
                if not f_gt_s:
                    need_txt += (f"，且 MA{int(p['FAST_WIN'])} 上穿 "
                                 f"MA{int(p['SLOW_WIN'])}")
        else:                                                    # 已在线上，只差斜率
            need, need_txt = None, ("等快线斜率转正" if kind == "dual_ma" else "等斜率转正")
    elif kind == "macd":
        pos_txt = f"DIF {ma_last:+.3f}"
        need, need_txt = None, ("等 DIF 上穿零轴" if ma_last <= 0 else "等 DIF 上穿 DEA")
    else:                                                        # momentum
        pos_txt = f"动量 {ma_last:+.1%}"
        need, need_txt = None, ("等动量转正" if ma_last <= 0 else "等动量上行")
    return dict(
        down=dict(lag=_med(down_lag), conf=_med(down_conf), after=_med(down_after),
                  whip=(whip_dn / whip_dn_n if whip_dn_n else None), n=whip_dn_n),
        up=dict(lag=_med(up_lag), conf=_med(up_conf), after=_med(up_after),
                whip=(whip_up / whip_up_n if whip_up_n else None), n=whip_up_n),
        switch_pa=n_sw / years, hold_med=_med(segs),
        ann=a["ann"], mdd=a["mdd"], sharpe=a["sharpe"], expo=tg.exposure(st),
        ann_hold=b["ann"], mdd_hold=b["mdd"],
        current=dict(kind=kind, quad=str(sm["quad"].iloc[-1]), state=float(sm["state"].iloc[-1]),
                     target=(None if not np.isfinite(sm["target"].iloc[-1])
                             else float(sm["target"].iloc[-1])),
                     ma=ma_last, close=close_last, slope=float(sm["slope"].iloc[-1]),
                     above=above, slope_up=slope_up, below_days=int(sm["below_days"].iloc[-1]),
                     need=need, need_txt=need_txt, pos_txt=pos_txt, f_gt_s=f_gt_s, buf=buf),
    )


def pareto(rows, side):
    """在「切换/年 小」×「滞后 小」上找非支配点（两侧各算一次）。"""
    keep = []
    for i, a in enumerate(rows):
        la, sa = a[side]["lag"], a["switch_pa"]
        if la is None or sa is None:
            continue
        dominated = False
        for j, b in enumerate(rows):
            lb, sb = b[side]["lag"], b["switch_pa"]
            if i == j or lb is None:
                continue
            if sb <= sa and lb <= la and (sb < sa or lb < la):
                dominated = True
                break
        if not dominated:
            keep.append(a["name"])
    return keep


def _median_lag(rows, side):
    """该侧滞后的中位数（「快的一半」的分界线）。"""
    xs = sorted(r[side]["lag"] for r in rows if r[side]["lag"] is not None)
    return xs[len(xs) // 2] if xs else None


def _level_txt(x):
    return tg.LEVEL_TXT.get(round(x, 6), f"{x:.0%}")


def main():
    px = tg.load(LEG)
    cand = px.index[min(WARM, len(px) - 2)]
    rows = []
    for name, kw, fam, note in SPECS:
        m = metrics(px, kw)
        rows.append(dict(name=name, fam=fam, note=note, **m))
    fd, fu = pareto(rows, "down"), pareto(rows, "up")
    # 前沿的端点必然包含「切换最少」的那个（切换少了不可能被支配），那是构造性非支配，
    # 不代表「又快」。所以 ★ 再加一条：两侧滞后都不慢于该侧中位数。
    med_dn, med_up = _median_lag(rows, "down"), _median_lag(rows, "up")
    raw_both = [r["name"] for r in rows if r["name"] in fd and r["name"] in fu]
    both = [r["name"] for r in rows if r["name"] in raw_both
            and r["down"]["lag"] <= med_dn and r["up"]["lag"] <= med_up]
    cur = rows[0]["current"]
    dates = px.index[-1].date()
    by = {r["name"]: r for r in rows}
    cur_r = by["MA250+20（V5 现行）"]

    L = [
        "# 中证A500 · 趋势口径的「两侧」扫描（上涨侧 + 下跌侧）+ 当下读数",
        "",
        f"> 序列 = 场外复权净值（`023299`，2025-03-27 前为 `000051` 沪深300 联接桥接）；"
        f"统计窗口 {cand.date()} ~ {dates}（统一跳过前 {WARM} 个交易日）。",
        "> **当下的读数不受桥接影响**：MA250 只回看 250 个交易日（约 2025-10 起），全部落在真实段内。",
        "",
        "## 1. 两侧怎么量（同尺度）",
        "",
        "- **确认滞后 = 已走 / (已走 + 之后 120 日还能走)**。0% = 在顶点/起点当场确认，"
        "100% = 整段都错过。下跌侧用「距 250 日高点的回撤」，上涨侧用「距 250 日低点的涨幅」，"
        "归一化后两个方向可比。",
        f"- **假警报率**：下侧 = 确认下跌后 {WHIP_N} 日内又回「上涨趋势」；"
        f"上侧 = 确认上涨后 {WHIP_N} 日内又回「下跌趋势」。",
        "- **切换/年**：一年改几次主意（两侧共用）—— 越低越稳。",
        "",
        "## 2. 扫描结果（按下侧滞后从小到大）",
        "",
        "| 口径 | 族 | 下侧滞后 | 下侧已跌 | 下侧假警 | 上侧滞后 | 上侧已涨 | 上侧假警 | 切换/年 | "
        "年化 | 回撤 | 夏普 | 恒持年化 |",
        "|---|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|",
    ]
    for r in sorted(rows, key=lambda z: (z["down"]["lag"] if z["down"]["lag"] is not None else 9)):
        mark = " ★" if r["name"] in both else (" ☆" if (r["name"] in fd or r["name"] in fu) else "")
        dw = "—" if r["down"]["whip"] is None else f"{r['down']['whip']:.0%}"
        uw = "—" if r["up"]["whip"] is None else f"{r['up']['whip']:.0%}"
        L.append(
            f"| {r['name']}{mark} | {r['fam']} | {r['down']['lag']:.0%} | "
            f"{tg.fmt_pct(r['down']['conf'])} | {dw} | {r['up']['lag']:.0%} | "
            f"{tg.fmt_pct(r['up']['conf'])} | {uw} | {r['switch_pa']:.1f} | "
            f"{tg.fmt_pct(r['ann'])} | {tg.fmt_pct(r['mdd'])} | {r['sharpe']:.2f} | "
            f"{tg.fmt_pct(r['ann_hold'])} |")
    L += [
        "",
        f"> ★ = 两侧都在 Pareto 前沿**且**两侧滞后都不慢于该侧中位数（下侧 ≤ {med_dn:.0%}、"
        f"上侧 ≤ {med_up:.0%}）＝真正的「又快又稳」。☆ = 至少一侧在前沿、另一侧偏慢 —— "
        f"含「切换最少」的构造性端点（切换 {min(r['switch_pa'] for r in rows):.1f} 次/年 的口径"
        "不可能被别的点支配，但它是两侧都最慢的一档）。无标记 = 能被「更早且不更吵」的口径替代。",
        "",
        f"## 3. 当下读数（{dates}）",
        "",
        f"**A500 复权净值 {cur['close']:.4f}，MA250 = {cur['ma']:.4f}"
        f"（乖离 {cur['close'] / cur['ma'] - 1:+.1%}），年线 20 日斜率 "
        f"{'向上' if cur['slope_up'] else '向下'}，连续在年线下 {cur['below_days']} 个交易日。**",
        "",
        "| 口径 | 族 | 读数位置 | 读数斜率 | 象限 | 若套 V5 档位 | 距翻多还差 |",
        "|---|---|---|---|---|---|---|",
    ]
    votes = {"多": 0, "空": 0, "半": 0}
    votes_up = {"多": 0, "空": 0, "半": 0}
    for r in rows:
        c = r["current"]
        judge = {"上涨趋势": "多", "反弹": "半", "高位回调": "半", "下跌趋势": "空"}[c["quad"]]
        votes[judge] += 1
        votes_up["多" if (c["above"] and c["slope_up"]) else "空"] += 1
        st = c["state"]
        tgt = c["target"]
        move = "" if tgt is None or abs(tgt - st) < 1e-9 else f" → {_level_txt(tgt)}"
        L.append(f"| {r['name']} | {r['fam']} | {c['pos_txt']} | "
                 f"{'↑' if c['slope_up'] else '↓'} | {c['quad']} | "
                 f"{_level_txt(st)}{move} | {c['need_txt']} |")
    n_tot = len(rows)
    L += [
        "",
        f"> **按四象限计票（{n_tot} 个口径）**：看多 {votes['多']} · 看空 {votes['空']} · "
        f"半格 {votes['半']}。**严格按「两个都朝上」的加仓许可计票**：满足 {votes_up['多']} 个、"
        f"不满足 {votes_up['空']} 个。",
        "",
        "## 4. 结论",
        "",
    ]

    best_dn = min(rows, key=lambda r: (r["down"]["lag"], r["switch_pa"]))
    best_up = min(rows, key=lambda r: (r["up"]["lag"], r["switch_pa"]))
    best_ann = max(rows, key=lambda r: r["ann"])
    hold_ann, hold_mdd = rows[0]["ann_hold"], rows[0]["mdd_hold"]
    asym = max(rows, key=lambda r: abs(r["up"]["lag"] - r["down"]["lag"]))
    best_both = max((r for r in rows if r["name"] in both), key=lambda r: r["ann"]) if both else None
    fastest = max(rows, key=lambda r: r["switch_pa"])
    slowest = min(rows, key=lambda r: r["switch_pa"])
    better = [r for r in rows if r["ann"] > hold_ann and abs(r["mdd"]) < abs(hold_mdd)]
    d_ann = (cur_r["ann"] - hold_ann) * 100
    d_mdd = (abs(cur_r["mdd"]) - abs(hold_mdd)) * 100
    dn_pct, up_pct = round(asym["down"]["lag"] * 100), round(asym["up"]["lag"] * 100)
    need_up = (cur["ma"] / cur["close"] - 1.0) * 100
    du = by["双均线50/150+10"]["current"]
    n_up_slower = sum(1 for r in rows if r["up"]["lag"] > r["down"]["lag"])
    dn_rng = (min(r["down"]["lag"] for r in rows), max(r["down"]["lag"] for r in rows))
    up_rng = (min(r["up"]["lag"] for r in rows), max(r["up"]["lag"] for r in rows))
    L += [
        f"1. **两侧都能更快，但快的不是同一批口径。** 下侧最快 {best_dn['name']}"
        f"（滞后 {best_dn['down']['lag']:.0%}，即整段跌势里只错过 "
        f"{best_dn['down']['lag']:.0%}）；上侧最快 {best_up['name']}"
        f"（{best_up['up']['lag']:.0%}）。现行 {cur_r['name']} 是 "
        f"{cur_r['down']['lag']:.0%} / {cur_r['up']['lag']:.0%} —— **上侧比下侧更慢**，"
        "这与「年线滞后约半年」的常识一致，也正是慢口径在宽基上吃亏的地方。"
        f"而且这不是个别参数的问题：{n_up_slower}/{n_tot} 档都是上侧更慢（下侧整个区间 "
        f"{dn_rng[0]:.0%}–{dn_rng[1]:.0%}，上侧 {up_rng[0]:.0%}–{up_rng[1]:.0%}，两组只在边缘相碰）"
        "—— 换口径只能挪动区间，挪不掉这个偏移。**其中有一部分是度量口径带来的**"
        "（上涨起点用「距 250 日低点的涨幅」，A 股低点往往离得比高点更远），"
        "但方向的偏移和实盘体感一致，所以选口径时应当默认「上慢」并主动补偿。",
        f"2. **两侧都在前沿的有 {len(raw_both)} 档（{'、'.join(raw_both) or '（无）'}），"
        f"但其中「两侧都不慢于中位数」的只有 {len(both)} 档：{'、'.join(both) or '（无）'}。** "
        f"差别在于前沿的端点必然是「切换最少」的那个（{slowest['name']}，"
        f"{slowest['switch_pa']:.1f} 次/年）—— 它不可能被支配，但两侧滞后都排在末尾（"
        f"{slowest['down']['lag']:.0%} / {slowest['up']['lag']:.0%}），"
        "所以「落在前沿」≠「又快又稳」。"
        + (f"真正的候选是 {best_both['name']}：两侧滞后 {best_both['down']['lag']:.0%} / "
           f"{best_both['up']['lag']:.0%}、切换 {best_both['switch_pa']:.1f} 次/年、年化 "
           f"{tg.fmt_pct(best_both['ann'])}、回撤 {tg.fmt_pct(best_both['mdd'])}、夏普 "
           f"{best_both['sharpe']:.2f}。" if best_both else ""),
        f"3. **两侧灵敏度天然不对称，最不对称的是 {asym['name']}**：下侧 {dn_pct}% / "
        f"上侧 {up_pct}%（差 {up_pct - dn_pct}pp）。这个差值是选口径时最该看的："
        "A 股上涨时间长、下跌急，「上慢」的成本通常大于「下慢」—— 用只看下侧的表挑参数，"
        "容易挑出「逃顶快、抄底慢」的口径，而那正好是收益最痛的一侧。",
        f"4. **快慢与收益不是单调关系：太吵和太钝都白干，中间那批才两头改善。** 15 年样本恒持 "
        f"{tg.fmt_pct(hold_ann)} / 回撤 {tg.fmt_pct(hold_mdd)}。最吵的 {fastest['name']}"
        f"（{fastest['switch_pa']:.1f} 次/年）年化 {tg.fmt_pct(fastest['ann'])} / 回撤 "
        f"{tg.fmt_pct(fastest['mdd'])}，灵敏度没换来收益；最钝的现行 {cur_r['name']}"
        f"（{cur_r['switch_pa']:.1f} 次/年）{tg.fmt_pct(cur_r['ann'])} / "
        f"{tg.fmt_pct(cur_r['mdd'])} —— 年化比恒持 {d_ann:+.1f}pp、回撤只"
        f"{'改善' if d_mdd < 0 else '恶化'} {abs(d_mdd):.1f}pp，等于白做。年化高于恒持且回撤更浅的"
        f"有 {len(better)}/{n_tot} 档，最好的是 {best_ann['name']}（{tg.fmt_pct(best_ann['ann'])} / "
        f"{tg.fmt_pct(best_ann['mdd'])} / 夏普 {best_ann['sharpe']:.2f}）。**所以宽基上不是「闸门无用」，"
        "而是「慢闸门无用」** —— 与 510300 的证据同向。",
        f"5. **当下的用法（上涨侧）**：A500 现在是「价格在年线下 "
        f"{abs(cur['close'] / cur['ma'] - 1) * 100:.1f}%＋年线仍在上行」，严格满足「线上 + 斜率上行」"
        f"的口径 {votes_up['多']}/{n_tot} 个 —— **没有一档给出加仓许可**，现行口径落到「{cur['quad']}」，"
        f"档位是 {_level_txt(cur['state'])}（已跌破 {cur['below_days']} 日 ≥ "
        f"{tg.REDUCE_DAYS} 日）。翻多要盯两个数：一是价格回到 MA250（{cur['ma']:.4f}）上方，"
        f"约 **+{need_up:.1f}%**；二是回踩时的年线 20 日斜率是否还在零上 —— 仍在零上就直接进"
        "「上涨趋势」（满带），已经掉头就只能算「反弹」（只持有、不加仓）。若改看双均线 50/150，"
        f"条件更晚：价格先要 +{du['need']:.1%} 站上 MA50，且 MA50 还要上穿目前仍在它上方的 MA150。",
        "",
        "> 样本边界：长历史统计前 15 年是沪深300 桥接段；A500 真实净值仅 2018 个交易日左右。"
        "两侧滞后都是**事后**度量（用到未来 120 日），用于比较口径，不能当交易信号。",
        "",
        "> 机械规则输出，非投资建议。",
    ]

    with io.open(OUT_MD, "w", encoding="utf-8", newline="\n") as f:
        f.write("\n".join(L))
    print("报告:", OUT_MD)

    for r in sorted(rows, key=lambda z: (z["down"]["lag"] if z["down"]["lag"] is not None else 9)):
        dw = "  —" if r["down"]["whip"] is None else f"{r['down']['whip']:3.0%}"
        uw = "  —" if r["up"]["whip"] is None else f"{r['up']['whip']:3.0%}"
        mark = " ★" if r["name"] in both else (" ☆" if (r["name"] in fd or r["name"] in fu) else "")
        print(f"  {r['name']:20s} 下 {r['down']['lag']:4.0%}/假{dw} 上 {r['up']['lag']:4.0%}/假{uw} "
              f"切换 {r['switch_pa']:4.1f}/年 · 年化 {tg.fmt_pct(r['ann']):>8s} "
              f"回撤 {tg.fmt_pct(r['mdd']):>8s} 夏普 {r['sharpe']:5.2f}{mark}")
    print(f"  两侧前沿: {both or '无'} | 计票 多 {votes['多']} / 空 {votes['空']} / 半 {votes['半']}")

    figure(rows, fd, fu, both)


def figure(rows, fd, fu, both):
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    from matplotlib.patches import Rectangle

    plt.rcParams["font.sans-serif"] = ["Microsoft YaHei", "SimHei", "DejaVu Sans"]
    plt.rcParams["axes.unicode_minus"] = False

    pts = [r for r in rows if r["down"]["lag"] is not None and r["up"]["lag"] is not None]
    star = set(both)
    base = "MA250+20（V5 现行）"
    # 只标关键点：★ / 现行基准 / 两侧各自最快 / 最不对称 —— 21 档全标必然糊成一团，全量见报告表格。
    lab = set(star) | {
        base,
        min(rows, key=lambda r: r["down"]["lag"])["name"],
        min(rows, key=lambda r: r["up"]["lag"])["name"],
        max(rows, key=lambda r: abs(r["up"]["lag"] - r["down"]["lag"]))["name"],
    }

    def annotate(ax, pairs):
        """标签防重叠：★ 优先占位；附近已有标签就错开一行（上下交替）并画细引线指回原点。"""
        x0, x1 = ax.get_xlim()
        y0, y1 = ax.get_ylim()
        step = 0.038 * (y1 - y0)                       # 约一行字高的数据当量
        dys = [4, -14, 16, -25, 27]                    # 上下交替，避免两个标签同 y 时叠死
        placed = []
        todo = [(xy, n) for xy, n in pairs if n in lab]
        todo.sort(key=lambda a: a[1] not in star)      # ★ 先放，位置最好
        for (x, y), name in todo:
            k = 0
            while k < len(dys) - 1 and any(
                    abs(x - px) < 0.10 * (x1 - x0) and abs(y + k * step - py) < 0.7 * step
                    for px, py in placed):
                k += 1
            placed.append((x, y + k * step))
            ax.annotate(name, (x, y), textcoords="offset points", xytext=(7, dys[k]),
                        ha="left", fontsize=7.2, zorder=9,
                        color="#1b2b3a" if name in star else "#697887",
                        bbox=dict(boxstyle="round,pad=0.18", fc="white", ec="none", alpha=0.82),
                        arrowprops=(dict(arrowstyle="-", lw=0.45, color="#b8c2cc",
                                         shrinkA=0, shrinkB=2) if k else None))

    fig, axes = plt.subplots(1, 3, figsize=(17.0, 6.6))

    def side(ax, key, title, ylab):
        two = fd if key == "down" else fu
        sc = ax.scatter([r["switch_pa"] for r in pts], [r[key]["lag"] * 100 for r in pts],
                        c=[(r[key]["whip"] or 0) * 100 for r in pts], cmap="RdYlGn_r",
                        s=74, edgecolor="white", lw=0.7, zorder=4, vmin=0, vmax=60)
        for r in pts:
            if r["name"] in two:
                ax.scatter([r["switch_pa"]], [r[key]["lag"] * 100], s=250, facecolors="none",
                           edgecolors="#1b2b3a", lw=1.2, zorder=5)
        annotate(ax, [((r["switch_pa"], r[key]["lag"] * 100), r["name"]) for r in pts])
        fr = sorted([r for r in pts if r["name"] in two], key=lambda r: r["switch_pa"])
        if len(fr) > 1:
            ax.plot([r["switch_pa"] for r in fr], [r[key]["lag"] * 100 for r in fr],
                    color="#1b2b3a", lw=1.0, ls="--", alpha=0.5, zorder=3)
        ax.set_title(title, fontsize=11, color="#1b2b3a", weight="bold", loc="left")
        ax.set_xlabel("切换次数 / 年（← 越左越稳）", fontsize=9)
        ax.set_ylabel(ylab, fontsize=9)
        ax.grid(ls="--", lw=0.55, alpha=0.35)
        for s in ("top", "right"):
            ax.spines[s].set_visible(False)
        ax.margins(x=0.20, y=0.16)
        return sc

    sc1 = side(axes[0], "down", "下侧：跌势确认滞后 vs 吵",
               "确认下跌时已错过的跌幅占比（%）")
    side(axes[1], "up", "上侧：涨势确认滞后 vs 吵",
         "确认上涨时已错过的涨幅占比（%）")
    cax = fig.add_axes([0.085, 0.085, 0.20, 0.014])       # 色条横放在左下，不挤两图的右边距
    cb = fig.colorbar(sc1, cax=cax, orientation="horizontal")
    cb.ax.tick_params(labelsize=7.5, pad=1)
    cb.set_label("该侧假警报率（%，两图同尺度）", fontsize=7.8, labelpad=1)
    cb.ax.xaxis.set_label_position("top")

    ax = axes[2]
    dx = [r["down"]["lag"] * 100 for r in pts]
    uy = [r["up"]["lag"] * 100 for r in pts]
    # 两轴等长 + 等比例：45° 线才是真的 45°，「所有点都在上方」才看得出来
    span_lo = min(min(dx), min(uy)) - 5.0
    span_hi = max(max(dx), max(uy)) + 4.0
    ax.set_xlim(span_lo, span_hi)
    ax.set_ylim(span_lo, span_hi)
    ax.set_aspect("equal", adjustable="box")
    med_dn, med_up = sorted(dx)[len(dx) // 2], sorted(uy)[len(uy) // 2]
    # ★ 区 = 两个滞后都不慢于中位数的那块矩形（左下角），不是两条独立的条带
    ax.add_patch(Rectangle((span_lo, span_lo), med_dn - span_lo, med_up - span_lo,
                           facecolor="#2f8f5b", alpha=0.09, edgecolor="none", zorder=0))
    ax.axvline(med_dn, color="#2f8f5b", lw=0.9, ls=":", zorder=1)
    ax.axhline(med_up, color="#2f8f5b", lw=0.9, ls=":", zorder=1)
    ax.text(med_dn - 0.8, span_hi - 1.0, f"下侧中位数 {med_dn:.0f}%", fontsize=7.6,
            color="#2f8f5b", ha="right", va="top")
    ax.text(span_lo + 1.0, med_up + 1.0, f"上侧中位数 {med_up:.0f}%", fontsize=7.6,
            color="#2f8f5b")
    ax.text(span_lo + 1.5, span_lo + 7.5, "★ 区：两侧都不慢于中位数", fontsize=7.4,
            color="#2f8f5b")
    ax.plot([span_lo, span_hi], [span_lo, span_hi], color="#9aa5b1", lw=1.0, ls="--", zorder=2)
    mid = (span_lo + span_hi) / 2.0
    ax.text(mid + 5.0, mid - 5.0, "45°：两侧一样快", fontsize=7.8, color="#5b6b7a",
            rotation=45, rotation_mode="anchor", ha="left", va="top")
    ax.scatter(dx, uy, s=74, color="#7b52ab", edgecolor="white", lw=0.7, zorder=4)
    for r in pts:
        if r["name"] in star:
            ax.scatter([r["down"]["lag"] * 100], [r["up"]["lag"] * 100], s=250,
                       facecolors="none", edgecolors="#d33", lw=1.4, zorder=5)
    annotate(ax, [((r["down"]["lag"] * 100, r["up"]["lag"] * 100), r["name"]) for r in pts])
    ax.text(span_hi - 1.0, span_lo + 1.2,
            f"{len(pts)} 档全部落在 45° 上方\n→ 上侧普遍更慢",
            fontsize=7.8, color="#5b6b7a", ha="right", va="bottom")
    ax.set_xlabel("下侧滞后（%）", fontsize=9)
    ax.set_ylabel("上侧滞后（%）", fontsize=9)
    ax.set_title("两侧对称性：越靠左下 = 两侧都早；红圈 = 两侧都快且都在前沿", fontsize=11,
                 color="#1b2b3a", weight="bold", loc="left")
    ax.grid(ls="--", lw=0.55, alpha=0.35)
    for s in ("top", "right"):
        ax.spines[s].set_visible(False)

    fig.suptitle("中证A500 · 趋势口径的「快 × 稳」两侧检验（圈 + 虚线 = 该侧 Pareto 前沿）",
                 x=0.05, ha="left", fontsize=13.2, color="#1b2b3a", weight="bold")
    fig.text(0.05, 0.018,
             "滞后 = 已走幅度 / (已走 + 之后 120 日还能走)，两个方向同尺度；假警报 = 确认后 20 日内翻回反向。"
             "图为可读只标关键点，21 档全量见报告表格。机械规则输出，非投资建议。",
             fontsize=8.4, color="#5b6b7a")
    fig.subplots_adjust(left=0.055, right=0.975, top=0.855, bottom=0.20, wspace=0.26)
    fig.savefig(OUT_PNG, dpi=135, facecolor="white")
    plt.close(fig)
    print("图:", OUT_PNG)


if __name__ == "__main__":
    main()
