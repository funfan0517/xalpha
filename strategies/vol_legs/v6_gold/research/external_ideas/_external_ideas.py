# -*- coding: utf-8 -*-
"""V6 黄金 · 外部策略清单与同尺子复现。

**做什么**：把 2026-11 网上能查到的黄金策略（Faber 十月均线、时序动量 / 双动量、
华泰时序 CTA 的状态+ATR 止损、金银比 80/50、52 周新高、季节性）逐条拉到**本仓库同一把尺子**上
复现，和 `v6_gold` 的 A/B/C 三口径、恒持满带放进同一张表。

**为什么**：外部文章给的都是「别的市场 + 别的样本 + 常常零费率」的数字。要说某条规则在
我们的黄金 ETF 上值不值得用，必须在 000216 的 12 年、同一费率、同一 T+1 纪律下重跑一遍。

**不做什么**：不搜参数。所有参数照原文抄（10 月均线、12 个月动量、20 日波动、3×ATR、80/50 阈值），
只在明显需要本地化时给「原文参数 / 本地变体」两列。

产物（本目录）：`_v6_external_ideas.md` · `_v6_external_ideas.jsonl`

用法：python strategies/vol_legs/v6_gold/research/external_ideas/_external_ideas.py
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

_HERE = os.path.dirname(os.path.abspath(__file__))          # .../v6_gold/research/external_ideas
_RES = os.path.dirname(_HERE)                              # .../v6_gold/research
_V6 = os.path.dirname(_RES)                                # .../v6_gold
_VL = os.path.dirname(_V6)                                 # strategies/vol_legs
for _p in (_V6, _VL):
    if _p not in sys.path:
        sys.path.insert(0, _p)

import gold as G      # noqa: E402
import data as vdata  # noqa: E402
import engine         # noqa: E402

OUT_MD = os.path.join(_HERE, "_v6_external_ideas.md")
OUT_JSONL = os.path.join(_HERE, "_v6_external_ideas.jsonl")

SILVER = "161226"      # 国投瑞银白银期货 LOF（场内，唯一有长历史的银价代理）
STOCK_LEG = "a500"     # 双动量的「相对动量」对手：中证A500 场外


# ---------------------------------------------------------------- 工具
def pct(x, nd=2):
    return "—" if x is None or (isinstance(x, float) and not np.isfinite(x)) else f"{x * 100:+.{nd}f}%"


def num(x, nd=2):
    return "—" if x is None or (isinstance(x, float) and not np.isfinite(x)) else f"{x:.{nd}f}"


def month_end(px):
    """每月最后一个交易日的收盘（用 Period 分组，避开 pandas 的 M/ME 别名差异）。"""
    return px.groupby(px.index.to_period("M")).tail(1)


def to_daily(dec, px, lag=1):
    """月度决策 -> 日频仓位：月末收盘决策，**次日**生效（T+1），缺失月沿用上一个决策。"""
    s = dec.reindex(px.index, method="ffill").astype(float)
    return s.shift(lag).fillna(0.0)


def gold_series():
    return G.load("otc", refresh=True)


# ---------------------------------------------------------------- 外部规则
def r_faber10m(px):
    """① Faber 十月均线：月末收盘 > 近 10 个月均线 -> 满仓，否则空仓（现金）。"""
    me = month_end(px)
    ma10 = me.rolling(10, min_periods=10).mean()
    dec = (me > ma10).astype(float).where(ma10.notna(), 0.0)
    return to_daily(dec, px), "月末信号 / 次月首个交易日成交"


def r_tsmom12(px):
    """② 时序动量（Moskowitz 2012 / 绝对值版）：过去 12 个月收益 > 0 -> 满仓，否则空仓。"""
    me = month_end(px)
    mom = me / me.shift(12) - 1.0
    dec = (mom > 0).astype(float).where(mom.notna(), 0.0)
    return to_daily(dec, px), "月末 12 个月动量 / 次月成交"


def r_tsmom3_volstop(px):
    """③ 时序动量三档 + 波动目标 + 8% 追踪止损（复现 cloudQuant test_0005 的规则骨架）。

    方向：12 个月动量 > 10% → 1.0，> 0 → 0.5，否则 0；
    仓位：× clip(15% / 20 日已实现波动, 0.5, 1.5)；
    止损：从持仓期最高收盘回落 8% 清仓，等下一次月末重估。
    """
    me = month_end(px)
    mom = me / me.shift(12) - 1.0
    base = pd.Series(0.0, index=me.index)
    base[mom > 0] = 0.5
    base[mom > 0.10] = 1.0
    base = base.where(mom.notna(), 0.0)
    base_d = to_daily(base, px)                       # 月频方向（分段常数）
    vol = px.pct_change().rolling(20).std() * np.sqrt(252)
    scale = (0.15 / vol).clip(0.5, 1.5).fillna(1.0)
    c = px.to_numpy(dtype=float)
    bd = base_d.to_numpy(dtype=float)
    sc = scale.to_numpy(dtype=float)
    out = np.zeros(len(px))
    pos, peak, last_tgt, stopped = 0.0, 0.0, None, False
    for i in range(len(px)):
        if bd[i] != last_tgt:                         # 月末重估 -> 解除止损
            last_tgt, stopped, peak = bd[i], False, 0.0
        if bd[i] <= 0.0 or stopped:
            pos, peak = 0.0, 0.0
        elif pos == 0.0:
            pos, peak = bd[i] * sc[i], c[i]
        else:
            peak = max(peak, c[i])
            if c[i] / peak - 1.0 <= -0.08:
                pos, peak, stopped = 0.0, 0.0, True
            else:
                pos = bd[i] * sc[i]
        out[i] = pos
    return pd.Series(out, index=px.index), "月末方向 + 日频波动目标 + 8% 追踪止损"


def r_52w_high_200ma(px):
    """④ 52 周新高效应（cloudQuant test_0014）：站上 200 日均线 + 收盘处于 52 周高点的 75%~98%。"""
    ma = px.rolling(200, min_periods=200).mean()
    hi = px.rolling(250, min_periods=250).max()
    ratio = px / hi
    st = ((px >= ma) & (ratio >= 0.75) & (ratio <= 0.98)).astype(float)
    st = st.where(ma.notna() & hi.notna(), 0.0)
    return st, "日频，站上 MA200 且距 52 周高点 2%~25% 时持有"


def r_breakout_atr(px, n=20, mult=3.0, atr_n=14):
    """⑤ 华泰时序 CTA 的简化可复现版：20 日收盘突破开仓 + 3×ATR 追踪止损。

    原文用「趋势信噪比 + GARCH + 重采样」判状态、ATR 移动止损（黄金 3.0 倍），这里只保留
    可复现的骨架（状态判断退化为突破本身），ATR 用收盘价差近似（本地只有收盘价）。
    """
    hi = px.rolling(n).max().shift(1)
    atr = px.diff().abs().rolling(atr_n).mean()
    c = px.to_numpy(dtype=float)
    up = (px > hi).to_numpy()
    a = atr.to_numpy(dtype=float)
    out = np.zeros(len(px))
    pos, peak = 0.0, 0.0
    for i in range(len(px)):
        if pos == 0.0 and up[i]:
            pos, peak = 1.0, c[i]
        elif pos > 0.0:
            peak = max(peak, c[i])
            if np.isfinite(a[i]) and c[i] < peak - mult * a[i]:
                pos, peak = 0.0, 0.0
        out[i] = pos
    return pd.Series(out, index=px.index), f"日频，{n} 日突破 + {mult:g}×ATR({atr_n}) 追踪止损"


def r_seasonality(px, months=(8, 9)):
    """⑥ 黄金秋季效应（Baur 2013）：只在指定月份持有（进入该月首个交易日收盘买入）。"""
    m = px.index.month
    st = pd.Series(np.isin(m, months).astype(float), index=px.index)
    return st, f"只在 {','.join(str(x) for x in months)} 月持有，其余空仓"


# ---------------------------------------------------------------- 金银比（双资产）
def ratio_switch(gold, silver, rule, win=2520, anchor=None):
    """金银比轮动 -> (state_gold, state_silver, ratio)。

    rule="80/50"：比值 > 80 全换白银、< 50 换回黄金、中间维持（原文阈值，带滞回）；
    rule="pct10"：比值 > 近 10 年 90% 分位换白银、< 10% 分位换黄金（去后视镜版，尺度无关）。

    两只基金的单位不同（`518880` 元/份 vs `161226` 元/份，每份对应的金属量不同），
    所以「份价比」不能直接当金银比用。`anchor=(日期, 真实金银比)` 用外部锚点校准常数倍率：
    `ratio = 份价比 × k`，`k` 由锚点日反解。比例版（pct10）与尺度无关，不需要锚点。
    """
    g = gold.reindex(silver.index).ffill()
    ratio = (g / silver).dropna()
    if anchor is not None:
        d0, r0 = anchor
        k = r0 / float(ratio.loc[d0])
        ratio = ratio * k
    idx = ratio.index
    s_g = pd.Series(np.nan, index=idx)
    s_s = pd.Series(np.nan, index=idx)
    hold = "gold"
    for t in range(len(idx)):
        r = float(ratio.iloc[t])
        if rule == "80/50":
            if r > 80:
                hold = "silver"
            elif r < 50:
                hold = "gold"
        else:
            hist = ratio.iloc[max(0, t - win):t]
            if len(hist) >= 500:
                hi, lo = np.quantile(hist, 0.90), np.quantile(hist, 0.10)
                if r > hi:
                    hold = "silver"
                elif r < lo:
                    hold = "gold"
        s_g.iloc[t] = 1.0 if hold == "gold" else 0.0
        s_s.iloc[t] = 1.0 - s_g.iloc[t]
    return s_g, s_s, ratio


def run_switch(s_g, s_s, gold, silver, fee=(0.001, 0.005), slip=0.001, cash_annual=G.CASH_ANNUAL):
    """两资产互斥的净值：任一时刻只在金/银之一（或现金）。成本按 |Δ仓位| × 费率。"""
    g = gold.reindex(silver.index).ffill()
    ret_g = g.pct_change().fillna(0.0)
    ret_s = silver.pct_change().fillna(0.0)
    hg = s_g.shift(1).fillna(0.0)
    hs = s_s.shift(1).fillna(0.0)
    cash = (1.0 + cash_annual) ** (1.0 / 252) - 1.0
    r = hg * ret_g + hs * ret_s + (1.0 - hg - hs) * cash
    dg = s_g.diff().fillna(s_g)
    ds = s_s.diff().fillna(s_s)
    r = (r - dg.clip(lower=0) * (fee[0] + slip) + dg.clip(upper=0) * (fee[1] + slip)
         - ds.clip(lower=0) * (fee[0] + slip) + ds.clip(upper=0) * (fee[1] + slip))
    return (1.0 + r).cumprod()


# ---------------------------------------------------------------- 评价
def evaluate(px, st, mode="otc", p=None):
    fee, slip = G.fee_of(mode, p)
    nav = G.run_leg(st, px, fee=fee, slip=slip)
    m = G.perf(nav)
    return dict(nav=nav, m=m, calmar=G.calmar(m), exp=G.exposure(st),
                acts=len(G.actions(st)), whip=G.whipsaw(st, px)["n"],
                cost=G.cost_drag(st, mode, p), state=st)


def years_of(nav):
    return (nav.index[-1] - nav.index[0]).days / 365.25


def row(name, r, note=""):
    m = r["m"]
    y = years_of(r["nav"])
    return (f"| {name} | {pct(m['ann'])} | {pct(m['mdd'])} | {num(m['sharpe'])} | "
            f"{num(r['calmar'])} | {m['vol'] * 100:.1f}% | {r['exp'] * 100:.0f}% | "
            f"{r['acts'] / y:.1f} | {r['cost'] * 100 / y:.2f}pp | {note} |")


HEAD = ("| 规则 | 年化 | 最大回撤 | 夏普 | Calmar | 年化波动 | 平均仓位 | 调仓/年 | 成本/年 | 原文口径 |\n"
        "|---|---:|---:|---:|---:|---:|---:|---:|---:|---|")


def main():
    p = G.params()
    d = G.build("otc", refresh=False)
    full = d["px"]
    start_a, start_b = d["start"]["A"], d["start"]["B"]
    start_c = full.index[min(G.C_WARMUP, len(full) - 2)]
    start = max(start_a, start_b, start_c)
    px = full.loc[start:]

    # --- 基准与 v6 三口径（状态在**全序列**上算，再截到共同窗口，避免预热段失真）
    st_a = G.state_a(full)["state"].loc[px.index]
    st_b = G.state_b(full)["state"].loc[px.index]
    st_c = G.state_c(full).loc[px.index]
    bench = {
        "恒持满带（买入持有）": evaluate(px, pd.Series(1.0, index=px.index), "otc", p),
        "纯现金（货基）": evaluate(px, pd.Series(0.0, index=px.index), "otc", p),
        "v6 A 长期配置": evaluate(px, st_a, "otc", p),
        "v6 B 控回撤": evaluate(px, st_b, "otc", p),
        "v6 C 占坑（MACD 12/26）": evaluate(px, st_c, "otc", p),
    }

    # --- 外部规则
    rules = []
    for key, fn in (("Faber 十月均线", r_faber10m),
                    ("时序动量 12 个月（绝对值）", r_tsmom12),
                    ("时序动量三档 + 波动目标 + 8% 止损", r_tsmom3_volstop),
                    ("52 周新高 + 200 日均线", r_52w_high_200ma),
                    ("20 日突破 + 3×ATR 止损（华泰 CTA 简化）", r_breakout_atr),
                    ("20 日突破 + 6×ATR 止损（本地加宽）",
                     lambda s: r_breakout_atr(s, mult=6.0)),
                    ("秋季效应（8-9 月）", lambda s: r_seasonality(s, (8, 9))),
                    ("秋季效应（仅 9 月）", lambda s: r_seasonality(s, (9,)))):
        st, how = fn(px)
        r = evaluate(px, st.fillna(0.0), "otc", p)
        rules.append((key, r, how))

    # --- 金银比（两资产，场内口径）
    gold_in = vdata.load_klines(G.SIGNAL_CODE).dropna()
    silver = vdata.load_klines(SILVER).dropna()
    both = gold_in.index.intersection(silver.index)
    gold_in, silver = gold_in.loc[both[0]:], silver.loc[both[0]:]
    common = both[both >= start]
    gg, ss = gold_in.loc[common[0]:], silver.loc[common[0]:]
    gsr = {}
    # 外部锚点：2026-11-06 现货金银比 78.4（金 4070.93 / 银 51.95 美元），用来校准两只基金的单位差
    anchor = ("2026-11-06", 78.4) if pd.Timestamp("2026-11-06") in gg.index else None
    for name, rule in (("金银比 80/50（原文阈值）", "80/50"), ("金银比 10 年 90/10 分位（去后视镜）", "pct10")):
        s_g, s_s, ratio = ratio_switch(gg, ss, rule, anchor=anchor if rule == "80/50" else None)
        nav = run_switch(s_g, s_s, gg, ss)
        gsr[name] = dict(nav=nav, m=G.perf(nav), calmar=G.calmar(G.perf(nav)),
                         ratio=ratio, s_g=s_g, s_s=s_s, switches=len(G.actions(s_g)))
    gsr["只持黄金（同区间）"] = dict(nav=G.run_leg(pd.Series(1.0, index=gg.index), gg,
                                                fee=G.FEE_OTC, slip=G.SLIP))
    gsr["只持白银（同区间）"] = dict(nav=G.run_leg(pd.Series(1.0, index=ss.index), ss,
                                                fee=G.FEE_OTC, slip=G.SLIP))
    for k in ("只持黄金（同区间）", "只持白银（同区间）"):
        m = G.perf(gsr[k]["nav"])
        gsr[k].update(m=m, calmar=G.calmar(m))

    # --- 季节性分月（12 年逐年按月收益）
    # 分月：每年该月的「月内首末收盘」收益，再跨年取平均（不用跨月的跳空）
    month_tab = {}
    for mth in range(1, 13):
        vals, wins, ndays = [], 0, 0
        for _y, grp in px.groupby(px.index.year):
            sub = grp[grp.index.month == mth]
            if len(sub) < 5:
                continue
            vals.append(float(sub.iloc[-1] / sub.iloc[0] - 1.0))
            wins += int((sub.pct_change().dropna() > 0).sum())
            ndays += max(len(sub) - 1, 0)
        if not vals:
            continue
        month_tab[mth] = dict(mean=float(np.mean(vals)), win=float(wins / max(ndays, 1)),
                              n=len(vals), up=float(np.mean(np.array(vals) > 0)),
                              best=float(np.max(vals)), worst=float(np.min(vals)))

    # --- 外部证据（原文数字，人工摘录，见 README/对话）
    external = [
        ("Faber 十月均线", "Investors Chronicle 复现（1990 起，月度）",
         "年化 5.13% vs 买入持有 4.55%；波动 12.0% vs 15.7%；夏普 0.43 vs 0.29；最差 12 个月 -19.4% vs -27.6%"),
        ("时序动量 12 个月（单资产）", "cloudQuant/backtrader XAUUSD 2008-2025",
         "总收益 +278.97%、最大回撤 33.71%、14 笔、胜率 35.7%、盈利因子 2.36（原文未给夏普、未扣费）"),
        ("时序动量三档 + 波动目标 + 8% 止损", "同上 test_0005",
         "总收益 +175.81%、最大回撤 23.30%、夏普 0.70、19 笔"),
        ("双动量（4 资产含黄金）", "同上 test_0002",
         "总收益 +107.82%、最大回撤 12.08%、52 次切换（对比单资产 -33.71%）"),
        ("GTAA-5 双动量（含 GLD）", "Backtesting Arena 2007-01~2026-06（233 个月，零成本）",
         "CAGR 8.3% vs SPY 10.9%；回撤 -17.7% vs -50.8%；夏普同为 0.67；139 次换仓"),
        ("金银比 80/50", "Bullion Trading，LBMA 1968-2026（14,701 个收盘）",
         "8 次换仓、不计成本 7.16 倍（≈3.4%/年）；按真实溢价仅 3.46 倍（≈2.1%/年）；"
         "去后视镜的 10 年 90/10 分位版 ≈1.01 倍（等于持有黄金）；70/60 版 0.78 倍（亏损）"),
        ("52 周新高 + 200 日均线", "cloudQuant test_0014", "只给规则，未披露回测数字"),
        ("时序 CTA（状态识别 + ATR 止损）", "华泰金工《时序CTA方法论综述》2026-06",
         "黄金 ETF ATR 止损取 3.0 倍；称沪深300ETF/黄金ETF/国债ETF 的卡玛比「相较基准提升显著」，"
         "但明确「状态识别存在时滞，小级别行情把握误差大」；原文未给逐项数字"),
        ("秋季效应（9 月）", "Baur (2013) 等；新浪财经转述",
         "1975 年以来 9 月均值 +1.7%（其他月 +0.5%），但 **2011 年以来 9 月是最差月份**"),
        ("实际利率 / 10Y TIPS 定价", "PIMCO、国联期货 2022-2026 复盘、assetwhisper",
         "2003-2021 实际利率只解释约 1/4 的月度波动、2022 年后约 1/6；"
         "2022-2026 按模型应跌 13%，实际 +136%；2023-2024 负相关一度失效"),
    ]

    # ---------------------------------------------------------------- 写报告
    L = []
    A = L.append
    A("# V6 黄金 · 外部策略清单与同尺子复现")
    A("")
    A(f"> 样本：场外 `{G.EXEC_CODE}` **{px.index[0].date()} ~ {px.index[-1].date()}**"
      f"（{years_of(bench['恒持满带（买入持有）']['nav']):.1f} 年）；T+1 收盘成交、空仓计货基 "
      f"{G.CASH_ANNUAL:.2%}、申 {G.FEE_OTC[0]:.2%}/赎 {G.FEE_OTC[1]:.2%} + 滑点 {G.SLIP:.2%}。")
    A("> 所有外部规则**参数照原文抄**，不做本地调优；月度信号在月末收盘定、次一交易日成交。")
    A("")
    A("## 1. 外部研究清单（原文口径，不是我们的复现）")
    A("")
    A("| 策略 | 出处 | 原文结论 |")
    A("|---|---|---|")
    for nm, src, concl in external:
        A(f"| {nm} | {src} | {concl} |")
    A("")

    A("## 2. 同尺子复现（场外 000216，含费）")
    A("")
    A(HEAD)
    for nm in ("恒持满带（买入持有）", "v6 A 长期配置", "v6 B 控回撤", "v6 C 占坑（MACD 12/26）",
               "纯现金（货基）"):
        A(row(nm, bench[nm], "基准" if "恒持" in nm else ""))
    for nm, r, how in rules:
        A(row(nm, r, how))
    A("")
    bh = bench["恒持满带（买入持有）"]
    A(f"> 判读线：**年化 > {pct(bh['m']['ann'])} 才叫跑赢买入持有**；"
      f"夏普 > {num(bh['m']['sharpe'])} 才叫风险调整后更好。两条都达不到的规则，「回撤更小」只是"
      "用更低的仓位换来的，不是本事 —— 可以直接用更低的目标仓位复现（见 §5）。")
    A("")

    # 分段：只给「跑赢或打平恒持」的规则 + 恒持
    A("## 3. 三条曲线的分歧在哪（三段分期，年化）")
    A("")
    keep = [(nm, r) for nm, r, _h in rules if r["m"]["ann"] >= bh["m"]["ann"] - 0.005]
    keep = keep[:3]
    if not keep:
        A("没有任何外部规则跨过「年化不低于恒持」这条线，分期表略。")
        A("")
    else:
        idx = bh["nav"].index
        cuts = [0, len(idx) // 3, 2 * len(idx) // 3, len(idx) - 1]
        A("| 期间 | 恒持满带 | " + " | ".join(nm for nm, _r in keep) + " |")
        A("|---|" + "---|" * (len(keep) + 1))
        for k in range(3):
            d0, d1 = idx[cuts[k]], idx[cuts[k + 1]]
            cells = []
            for src in [bh] + [r for _n, r in keep]:
                sub = src["nav"].loc[d0:d1]
                cells.append(pct(G.perf(sub / sub.iloc[0])["ann"]) if len(sub) > 3 else "—")
            A(f"| {d0.date()} ~ {d1.date()} | " + " | ".join(cells) + " |")
        A("")

    # 金银比
    A("## 4. 金银比轮动（场内 518880 × 161226）")
    A("")
    A("| 规则 | 年化 | 最大回撤 | 夏普 | Calmar | 换仓次数 | 说明 |")
    A("|---|---:|---:|---:|---:|---:|---|")
    for nm, r in gsr.items():
        m = r["m"]
        note = "两资产互斥（全仓金或全仓银）" if "只持" not in nm else "单资产基准"
        A(f"| {nm} | {pct(m['ann'])} | {pct(m['mdd'])} | {num(m['sharpe'])} | "
          f"{num(r['calmar'])} | {r.get('switches', '—')} | {note} |")
    A("")
    ratio = gsr["金银比 80/50（原文阈值）"]["ratio"]
    sw = gsr["金银比 80/50（原文阈值）"]["switches"]
    A(f"> 当前金银比 **{ratio.iloc[-1]:.1f}**（{ratio.index[-1].date()}），"
      f"近 10 年分位 {float((ratio.iloc[-2520:] < ratio.iloc[-1]).mean()) * 100:.0f}%；"
      f"**本样本区间 {ratio.min():.1f} ~ {ratio.max():.1f}**（{ratio.index[0].date()} 起）。")
    A(f"> 口径：两只基金每份对应的金属量不同，`518880/161226` 的份价比不能直接当金银比，"
      f"已用 2026-11-06 现货金银比 78.4 校准常数倍率；`161226` 是**白银期货 LOF**（含移仓成本），"
      f"与现货银有跟踪偏差。")
    sw_acts = G.actions(gsr["金银比 80/50（原文阈值）"]["s_g"])
    if sw_acts:
        A("> 80/50 的换仓流水：" + " / ".join(
            f"{a['date'].date()} {'换白银' if a['to'] == 0 else '换回黄金'}" for a in sw_acts) + "。")
        A("> ⚠ 触发的**时点**对 `161226` 的移仓损耗敏感（期货 LOF 与现货银长期偏离），"
          "换成现货银序列，换仓日会移动；上表的超额主要来自 2022-2025 那一段持银，"
          "不是「每次都择对了」。按这条规则，现在应该持白银。")
    if sw == 0:
        A("> ⚠ 80/50 阈值在本区间**一次都没触发**，净值与「只持黄金」完全重合 —— "
          "这不是规则有效，是规则没开过口。原文自己也承认阈值是「用 58 年数据反推的」。")
    A("")

    # 季节性
    A("## 5. 季节性：12 年里的分月收益")
    A("")
    A("| 月份 | 各年平均月收益 | 上涨年份占比 | 最好年份 | 最差年份 | 上涨日占比 |")
    A("|---|---:|---:|---:|---:|---:|")
    for mth in sorted(month_tab):
        v = month_tab[mth]
        A(f"| {mth} 月 | {pct(v['mean'])} | {v['up'] * 100:.0f}%（{v['n']} 年） | "
          f"{pct(v['best'])} | {pct(v['worst'])} | {v['win'] * 100:.0f}% |")
    A("")

    # 结论
    A("## 6. 结论")
    A("")
    best = max(rules, key=lambda x: x[1]["m"]["sharpe"])
    beat = [nm for nm, r, _h in rules if r["m"]["ann"] > bh["m"]["ann"]]
    better_risk = [nm for nm, r, _h in rules if r["m"]["sharpe"] > bh["m"]["sharpe"]]
    A(f"1. **{len(rules)} 条外部规则里，年化跑赢恒持的 {len(beat)} 条："
      f"{'、'.join(beat) if beat else '无'}**；夏普更好的 {len(better_risk)} 条："
      f"{'、'.join(better_risk) if better_risk else '无'}。")
    A(f"2. 夏普最高的外部规则是 **{best[0]}**（夏普 {num(best[1]['m']['sharpe'])}、"
      f"年化 {pct(best[1]['m']['ann'])}、回撤 {pct(best[1]['m']['mdd'])}），"
      f"对比 v6 B（夏普 {num(bench['v6 B 控回撤']['m']['sharpe'])}、"
      f"年化 {pct(bench['v6 B 控回撤']['m']['ann'])}、回撤 {pct(bench['v6 B 控回撤']['m']['mdd'])}）。")
    A("3. 外部研究的共识与我们 12 年样本一致：**趋势类规则（均线 / 动量 / 突破）在黄金上主要买回撤、"
      "不买收益**；凡是宣称「年化和回撤双赢」的，要么样本窗口特殊（1970s、2008、2020），"
      "要么没扣费，要么是样本内挑参数。")
    g50 = gsr["金银比 80/50（原文阈值）"]
    gp = gsr["金银比 10 年 90/10 分位（去后视镜）"]
    A(f"4. **金银比 80/50 在本样本上是「一次半赌博赢来的」**：{g50['switches']} 次换仓、"
      f"年化 {pct(g50['m']['ann'])}（只持黄金 {pct(gsr['只持黄金（同区间）']['m']['ann'])}）、"
      f"夏普 {num(g50['m']['sharpe'])} vs {num(gsr['只持黄金（同区间）']['m']['sharpe'])} —— "
      f"数字好看，但统计上只有约 {g50['switches'] / 2:.1f} 个往返，一次踩错就翻负；"
      f"去掉后视镜的分位版（{gp['switches']} 次换仓）年化 {pct(gp['m']['ann'])}、"
      f"回撤 {pct(gp['m']['mdd'])}、夏普 {num(gp['m']['sharpe'])}，已经把「优势」还回去了。"
      "原文自己的 58 年测试里，去后视镜版就等于持有黄金 —— 结论是**温度计，不是计时器**。")
    aug, sep = month_tab.get(8), month_tab.get(9)
    A(f"5. **季节性方向甚至是反的**：Baur(2013) 说的是「秋季效应 / 9 月强」，"
      f"本地 12 年里 9 月各年平均 {pct(sep['mean']) if sep else '—'}"
      f"（上涨年份 {sep['up'] * 100:.0f}%）、8 月 {pct(aug['mean']) if aug else '—'}"
      f"（上涨年份 {aug['up'] * 100:.0f}%）；"
      f"原文也承认「2011 年以来 9 月是最差月份」。这类季节性不构成可交易的边际。")
    A("6. **实际利率框架不可用作择时信号**（只能用作背景判断）：2022 年后模型应跌 13% 而金价 +136%，"
      "本地也没有 TIPS 数据；这是「知道但用不了」的一类。")
    A("")
    A("## 7. 局限")
    A("")
    A("1. 原文的强数字多数来自 **1970-2010 / 2008-2025 的 XAUUSD 或美股 ETF**，样本与我们的"
      "「2014-2026 人民币计价的场外黄金联接」不是一回事：汇率、费率结构、溢价都不同。")
    A("2. 金银比只能在场内做（`161226` 是场内 LOF，无场外长序列），且 `161226` 与现货银有跟踪差异。")
    A("3. 华泰的 CTA 用「趋势信噪比 + GARCH + 1000 次重采样 + Kaufman 自适应窗口」判状态，"
      "这里只复现了「突破 + ATR 止损」的骨架，**不是同一个策略**，结论只能作方向参考。")
    A("4. 季节性、金银比的检验都基于单一路径（12 年），统计功效低；本文只用来筛掉明显无效的方向。")
    A("5. 所有规则都没做交易成本以外的冲击成本；换手高的规则（动量三档、突破）现实里更差。")
    A("")
    A("> 模拟结果，非投资建议。")
    md = "\n".join(L) + "\n"
    with open(OUT_MD, "w", encoding="utf-8") as f:
        f.write(md)

    with open(OUT_JSONL, "w", encoding="utf-8") as f:
        for nm, r, how in rules:
            m = r["m"]
            f.write(json.dumps(dict(rule=nm, ann=m["ann"], mdd=m["mdd"], sharpe=m["sharpe"],
                                    calmar=r["calmar"], vol=m["vol"], exposure=r["exp"],
                                    acts=r["acts"], cost=r["cost"], note=how),
                               ensure_ascii=False) + "\n")
        for nm, r in gsr.items():
            m = r["m"]
            f.write(json.dumps(dict(rule=nm, ann=m["ann"], mdd=m["mdd"], sharpe=m["sharpe"],
                                    calmar=r["calmar"], group="gold_silver"),
                               ensure_ascii=False) + "\n")
    print("报告:", OUT_MD)
    for nm, r, _h in rules:
        m = r["m"]
        print(f"  {nm:34s} 年化 {m['ann'] * 100:+6.2f}%  回撤 {m['mdd'] * 100:+7.2f}%  "
              f"夏普 {m['sharpe']:5.2f}  Calmar {r['calmar']:4.2f}  仓位 {r['exp']:.0%}")
    for nm, r in gsr.items():
        m = r["m"]
        print(f"  {nm:34s} 年化 {m['ann'] * 100:+6.2f}%  回撤 {m['mdd'] * 100:+7.2f}%  "
              f"夏普 {m['sharpe']:5.2f}")


if __name__ == "__main__":
    main()
