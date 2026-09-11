# -*- coding: utf-8 -*-
"""为什么 10日/前2名 口径仍跑输等权持有？—— 四层归因（空仓 / MA20 / 集中度 / 相位）

背景: 官方口径改为 10 交易日再平衡 + 「动量前 2 名中 ≥MA20」后, 十年 +12.65%/-16.53%/夏普 0.93,
      仍低于六类等权基准 +13.21%/-12.86% —— 收益更低、回撤更深, 两项同时落后。

本脚本在**当前口径(REBAL=10, HOLD_N=2)**下把差距拆层, 每层只动一个变量:
  1) 空仓层: 现行 vs 「无候选期改持等权 6 只」    -> 那 12 期空仓值多少钱
  2) 选择层: 现行 vs 「关 MA20、永远持动量前 2」  -> MA20 过滤的贡献
  3) 集中度层: topn = 1/2/3/4/5 单调扫描          -> 分散买到的是收益还是风险
  4) 运气层: 调仓相位平移 0~9 个交易日             -> 各口径的相位中位（唯一可靠的读数）
另附 REBAL=21 对照，确认问题不是 10 日频率引入的。

用法: python strategies/momentum_rotation/research/_gap_test.py
输出: research/_gap_report.md · research/_gap_nav.png
"""
import os
import sys
from datetime import datetime

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402
import numpy as np  # noqa: E402
import pandas as pd  # noqa: E402

sys.stdout.reconfigure(encoding="utf-8")
plt.rcParams["font.sans-serif"] = ["Microsoft YaHei", "SimHei", "DejaVu Sans"]
plt.rcParams["axes.unicode_minus"] = False

_DIR = os.path.dirname(os.path.abspath(__file__))
_STRAT = os.path.dirname(_DIR)
_ROOT = os.path.dirname(os.path.dirname(_STRAT))
for p in (_STRAT, _DIR, _ROOT):
    if p not in sys.path:
        sys.path.insert(0, p)

import backtest as bt  # noqa: E402
import rule  # noqa: E402

OUT_MD = os.path.join(_DIR, "_gap_report.md")
OUT_PNG = os.path.join(_DIR, "_gap_nav.png")
REBAL = rule.REBAL                       # 10
SEGS = [("十年全期", None, None),
        ("2016~2021H1", "2021-06-30", None),
        ("2021H2~2026", None, "2021-07-01")]

# 每层只动一个变量；label 用于报告
VARIANTS = {
    "top1":        dict(topn=1, use_ma=True,  cash_ew=False),
    "top2":        dict(topn=2, use_ma=True,  cash_ew=False),   # 现行
    "top3":        dict(topn=3, use_ma=True,  cash_ew=False),
    "top4":        dict(topn=4, use_ma=True,  cash_ew=False),
    "top5":        dict(topn=5, use_ma=True,  cash_ew=False),
    "allma":       dict(topn=99, use_ma=True, cash_ew=False),   # 持有全部合格候选(不做集中选择)
    "top2_noma":   dict(topn=2, use_ma=False, cash_ew=False),   # 关 MA20
    "top2_rescue": dict(topn=2, use_ma=True,  cash_ew=True),    # 空仓期改持等权篮子
}
LABEL = {"top1": "单仓·动量第1名中≥MA20（旧口径）",
         "top2": "**前2名中≥MA20（现行）**",
         "top3": "前3名中≥MA20",
         "top4": "前4名中≥MA20",
         "top5": "前5名中≥MA20",
         "allma": "持有全部合格候选（过滤但分散）",
         "top2_noma": "动量前2名（关 MA20 过滤）",
         "top2_rescue": "现行 + 无候选期改持等权6只"}
RAND_TRIALS = 200

_cache = {}


def prep(df):
    if "ma" not in _cache:
        _cache["ma"] = df.rolling(rule.MA).mean()
        _cache["first"] = {c: df[c].first_valid_index() for c in df.columns}
    return _cache["ma"], _cache["first"]


def dec_of(df, topn, use_ma=True, cash_ew=False, phase=0, rebal=None):
    """调仓名单: 动量降序 -> (可选 MA20 过滤) -> 取前 topn -> 空则按 cash_ew 处理。"""
    ma_s, first = prep(df)
    rebal = REBAL if rebal is None else rebal
    dates, n = df.index, len(df)
    out = {}
    for t in range(rule.MIN_HIST + phase, n, rebal):
        cutoff = dates[t - rule.MIN_HIST]
        elig = [c for c in df.columns if first[c] <= cutoff]
        mom = {}
        for c in elig:
            p0, p1 = df[c].iloc[t - rule.LOOKBACK], df[c].iloc[t]
            if p0 > 0 and not (np.isnan(p0) or np.isnan(p1)):
                mom[c] = p1 / p0 - 1.0
        ranked = sorted(mom, key=mom.get, reverse=True)
        if use_ma:
            ranked = [c for c in ranked if not np.isnan(ma_s[c].iloc[t])
                      and df[c].iloc[t] >= ma_s[c].iloc[t]]
        pick = ranked[:topn]
        if not pick and cash_ew:
            pick = elig
        out[t] = pick
    return out


def cands_of(df, use_ma=True, phase=0):
    """每个调仓日的**完整候选序列**（动量降序，可选 MA20 过滤），供随机对照抽样。"""
    ma_s, first = prep(df)
    dates, n = df.index, len(df)
    out = {}
    for t in range(rule.MIN_HIST + phase, n, REBAL):
        cutoff = dates[t - rule.MIN_HIST]
        elig = [c for c in df.columns if first[c] <= cutoff]
        mom = {}
        for c in elig:
            p0, p1 = df[c].iloc[t - rule.LOOKBACK], df[c].iloc[t]
            if p0 > 0 and not (np.isnan(p0) or np.isnan(p1)):
                mom[c] = p1 / p0 - 1.0
        ranked = sorted(mom, key=mom.get, reverse=True)
        if use_ma:
            ranked = [c for c in ranked if not np.isnan(ma_s[c].iloc[t])
                      and df[c].iloc[t] >= ma_s[c].iloc[t]]
        out[t] = ranked
    return out


def run(df, dec, always_reset=False):
    """等权买入持有引擎: 名单变化才调仓(权重随价漂移); always_reset=True 则每次重置等权。"""
    dates, n = df.index, len(df)
    eq, cur, w, entry, epx = [1.0], [], {}, {}, {}
    hold, trades, n_fee, n_action = {}, [], 0, 0
    for i in range(1, n):
        r = 0.0
        if cur:
            num = {}
            for c in cur:
                p0, p1 = df[c].iloc[i - 1], df[c].iloc[i]
                if p0 > 0 and not (np.isnan(p0) or np.isnan(p1)):
                    num[c] = w[c] * (p1 / p0)
            tot = sum(num.values())
            if tot > 0:
                w = {c: v / tot for c, v in num.items()}
                r = tot - 1.0
        eq.append(eq[-1] * (1 + r))
        hold[dates[i]] = list(cur)                      # 当日收益归旧仓, 故先记后换
        if i in dec and (always_reset or set(dec[i]) != set(cur)):
            new = dec[i]
            for c in cur:
                if c in new:
                    continue
                if (i - entry[c]) <= rule.FEE_SHORT_DAYS:
                    eq[-1] *= 1 - rule.FEE_SHORT * w[c]
                    n_fee += 1
                if epx[c] > 0:
                    trades.append(dict(code=c, entry_date=str(dates[entry[c]].date()),
                                       exit_date=str(dates[i].date()), bars=i - entry[c],
                                       ret=df[c].iloc[i] / epx[c] - 1.0))
            entry = {c: (entry[c] if c in cur else i) for c in new}
            epx = {c: (epx[c] if c in cur else df[c].iloc[i]) for c in new}
            cur = list(new)
            w = {c: 1.0 / len(cur) for c in cur} if cur else {}
            n_action += 1
    if cur:
        for c in cur:
            if (n - 1 - entry[c]) <= rule.FEE_SHORT_DAYS:
                eq[-1] *= 1 - rule.FEE_SHORT * w[c]
                n_fee += 1
            if epx[c] > 0:
                trades.append(dict(code=c, entry_date=str(dates[entry[c]].date()),
                                   exit_date=str(dates[n - 1].date()), bars=n - 1 - entry[c],
                                   ret=df[c].iloc[n - 1] / epx[c] - 1.0))
    return pd.Series(eq, index=dates), trades, pd.Series(hold), dict(n_fee=n_fee, n_action=n_action)


def cut(x, start):
    return (x / x.iloc[0])[x.index >= start]


def seg(eq, end=None, lo=None):
    e = eq
    if lo is not None:
        e = e[e.index >= pd.Timestamp(lo)]
    if end is not None:
        e = e[e.index <= pd.Timestamp(end)]
    return bt.metrics(e) if len(e) > 60 else None


def calmar(m):
    return m["ann"] / abs(m["dd"]) if m["dd"] < 0 else float("inf")


def cash_runs(hold):
    """空仓(无持仓)区间 [(起, 止)]"""
    out, st = [], None
    days = list(hold.items())
    for j, (d, cs) in enumerate(days):
        if not cs and st is None:
            st = d
        elif cs and st is not None:
            out.append((st, days[j - 1][0]))
            st = None
    if st is not None:
        out.append((st, days[-1][0]))
    return out


def main():
    df = rule.load_wide()
    if len(df) == 0:
        sys.exit("缺少十年库数据: 先跑 python strategies/momentum_rotation/fetch.py")
    s0 = df.index[rule.MIN_HIST]
    years = (df.index[-1] - s0).days / 365.0
    name = {r["proxy"]: r["name"] for r in rule.ASSETS}

    bm = cut(bt.run_benchmark(df, step=REBAL)[0], s0)

    # ---- 自检: 本脚本引擎在现行参数下必须与官方逐点一致 ----
    ref = cut(bt.run_strategy(df, rebal=REBAL, topn=rule.HOLD_N)[0], s0)
    eq_chk, _, _, _ = run(df, dec_of(df, rule.HOLD_N, use_ma=True))
    d = float(np.abs(cut(eq_chk, s0).values - ref.values).max())
    if d > 1e-12:
        sys.exit(f"自检失败: 本地引擎与官方偏离 {d:.3e}")
    print("引擎自检通过（现行参数与官方 backtest 逐点一致）\n")

    res = {}
    for k, kw in VARIANTS.items():
        eq, tr, hold, info = run(df, dec_of(df, **kw))
        res[k] = dict(eq=cut(eq, s0), trades=tr, hold=hold.reindex(cut(eq, s0).index),
                      info=info,
                      avg_n=float(np.mean([len(v) for v in dec_of(df, **kw).values()])))

    L = ["# 为什么 10 日 / 前2名 口径仍跑输等权持有 · 四层归因", "",
         f"> 生成 {datetime.now().strftime('%Y-%m-%d %H:%M')} · 引擎与官方 `backtest.py` 同构"
         f"（现行参数已逐点自检）· 数据 {df.index.min().date()} ~ {df.index.max().date()}"
         f" · 评估区间 {s0.date()} ~ {df.index[-1].date()}（{years:.1f} 年）", "",
         f"**锁定**：动量回看 {rule.LOOKBACK} 日、{REBAL} 交易日再平衡、"
         f"费用口径（卖出 ≤{rule.FEE_SHORT_DAYS} 交易日 {rule.FEE_SHORT:.1%} / 其外 0%）。"
         "**每层只动一个变量。**", ""]

    # ---- 一、主表 ----
    L += ["## 一、主表：分层对照", "",
          "| 段 | 口径 | 年化 | 最大回撤 | 波动 | 夏普 | 年化/回撤 | 总收益 | 平均持仓数 |",
          "|---|---|---|---|---|---|---|---|---|"]
    for sname, end, lo in SEGS:
        for k in VARIANTS:
            m = seg(res[k]["eq"], end, lo)
            if not m:
                continue
            L.append(f"| {sname} | {LABEL[k]} | {bt.pct(m['ann'])} | {bt.pct(m['dd'])} | "
                     f"{bt.pct(m['vol'])} | {m['shp']:.2f} | {calmar(m):.2f} | "
                     f"{bt.pct(m['ret'])} | {res[k]['avg_n']:.1f} |")
        mb = seg(bm, end, lo)
        if mb:
            L.append(f"| {sname} | **六类等权·{REBAL}日再平衡（基准）** | {bt.pct(mb['ann'])} | "
                     f"{bt.pct(mb['dd'])} | {bt.pct(mb['vol'])} | {mb['shp']:.2f} | "
                     f"{calmar(mb):.2f} | {bt.pct(mb['ret'])} | 6.0 |")
    L.append("")

    # ---- 二、空仓层 ----
    eq_s, hold = res["top2"]["eq"], res["top2"]["hold"]
    n_days = len(hold)
    m_cash = np.array([len(v) == 0 for v in hold])
    cps = cash_runs(hold)
    eqr = eq_s.pct_change().iloc[1:]
    bmr = bm.pct_change().reindex(eqr.index)
    cmask = pd.Series(m_cash, index=eq_s.index)[eqr.index]
    prod = lambda s: (1 + s).prod() - 1
    s_inv, b_inv, b_cash = prod(eqr[~cmask]), prod(bmr[~cmask]), prod(bmr[cmask])
    mc = bt.metrics(eq_s)
    mb = bt.metrics(bm)
    rel_inv = (1 + s_inv) / (1 + b_inv) - 1
    rel_gap = (1 + mb["ret"]) / (1 + mc["ret"]) - 1
    L += ["## 二、空仓层：那几段空仓值多少钱", "",
          f"现行口径全期空仓 **{len(cps)} 段 / {int(m_cash.sum())} 个交易日 / "
          f"{m_cash.sum() / n_days * 100:.2f}%**。", "",
          "| 时段 | 交易日 | 占比 | 策略累计 | 同期等权基准累计 | 差 |",
          "|---|---|---|---|---|---|",
          f"| 持仓日 | {int((~cmask).sum())} | {(~cmask).sum() / n_days * 100:.1f}% | "
          f"{bt.pct(s_inv)} | {bt.pct(b_inv)} | {bt.pct(s_inv - b_inv)} |",
          f"| 空仓日（收益 0） | {int(cmask.sum())} | {cmask.sum() / n_days * 100:.1f}% | 0.00% | "
          f"{bt.pct(b_cash)} | {bt.pct(-b_cash)} |",
          f"| **全期** | {n_days} | 100% | **{bt.pct(mc['ret'])}** | **{bt.pct(mb['ret'])}** "
          f"| **{bt.pct(mc['ret'] - mb['ret'])}** |", "",
          f"> 持仓日策略相对基准 **{bt.pct(rel_inv)}**，全期相对落后 **{bt.pct(rel_gap)}**；"
          f"差额即那 {int(cmask.sum())} 天空仓（同期基准 {bt.pct(b_cash)}）。", "",
          "| 空仓区间（起→止） | 交易日 | 同期等权基准 |", "|---|---|---|"]
    for a, b in cps:
        sg = bm[(bm.index >= a) & (bm.index <= b)]
        r = sg.iloc[-1] / sg.iloc[0] - 1 if len(sg) > 1 else 0.0
        L.append(f"| {a.date()} → {b.date()} | {len(sg)} | {bt.pct(r)} |")
    L.append("")

    # ---- 三、集中度层 ----
    L += ["## 三、集中度层：把持仓只数从 1 扫到 5", "",
          "| 持仓只数 | 年化 | 最大回撤 | 波动 | 夏普 | 平均实际持仓 |",
          "|---|---|---|---|---|---|"]
    for k in ["top1", "top2", "top3", "top4", "top5"]:
        m = bt.metrics(res[k]["eq"])
        L.append(f"| {k[3:]} 只 | {bt.pct(m['ann'])} | {bt.pct(m['dd'])} | {bt.pct(m['vol'])} | "
                 f"{m['shp']:.2f} | {res[k]['avg_n']:.1f} |")
    L.append(f"| **基准（等权 6 只）** | {bt.pct(mb['ann'])} | {bt.pct(mb['dd'])} | "
             f"{bt.pct(mb['vol'])} | {mb['shp']:.2f} | 6.0 |")
    L.append("")

    # ---- 四、相位层 ----
    L += ["## 四、运气层：调仓相位平移（唯一可靠的读数）", "",
          f"主表用的是 offset=0 这一个相位。把调仓网格整体平移 0~{REBAL - 1} 个交易日，"
          "每个口径各跑一次 —— **同一策略只换几天调仓，结果可能完全不同**。", "",
          "| 口径 | 年化最小 | 年化中位 | 年化最大 | 极差 |", "|---|---|---|---|---|"]
    ph = {}
    for k in ["top1", "top2", "top3", "top5", "allma", "top2_noma", "top2_rescue"]:
        kw = dict(VARIANTS[k])
        vals = []
        for off in range(REBAL):
            eq, _, _, _ = run(df, dec_of(df, phase=off, **kw))
            vals.append(bt.metrics(cut(eq, df.index[rule.MIN_HIST + off]))["ann"])
        ph[k] = np.array(vals)
        L.append(f"| {LABEL[k]} | {bt.pct(ph[k].min())} | **{bt.pct(float(np.median(ph[k])))}** | "
                 f"{bt.pct(ph[k].max())} | {float(ph[k].max() - ph[k].min()) * 100:.2f}% |")
    bmv = []
    for off in range(REBAL):
        bmv.append(bt.metrics(cut(bt.run_benchmark(df, step=REBAL)[0],
                                  df.index[rule.MIN_HIST + off]))["ann"])
    bmv = np.array(bmv)
    L.append(f"| **基准（等权 6 只）** | {bt.pct(bmv.min())} | **{bt.pct(float(np.median(bmv)))}** | "
             f"{bt.pct(bmv.max())} | {float(bmv.max() - bmv.min()) * 100:.2f}% |")
    L += ["",
          "> **看中位列。**主表的 offset=0 只是众多相位之一，"
          "相位中位才是这个口径的真实水平；极差越宽，说明它越依赖「你恰好在哪天调仓」。", ""]

    # ---- 五、REBAL=21 对照 ----
    L += ["## 五、对照：换回 21 日再平衡，结论会变吗", "",
          f"| 再平衡 | 口径 | 年化 | 最大回撤 | 夏普 |", "|---|---|---|---|---|"]
    ref21 = {}
    for k, kw in [("top1", VARIANTS["top1"]), ("top2", VARIANTS["top2"]),
                  ("top3", VARIANTS["top3"])]:
        eq, _, _, _ = run(df, dec_of(df, rebal=21, **kw))
        m = bt.metrics(cut(eq, s0))
        ref21[k] = m
        L.append(f"| 21 日 | {LABEL[k]} | {bt.pct(m['ann'])} | {bt.pct(m['dd'])} | {m['shp']:.2f} |")
    bm21 = bt.metrics(cut(bt.run_benchmark(df, step=21)[0], s0))
    L.append(f"| 21 日 | **基准（等权 6 只）** | {bt.pct(bm21['ann'])} | {bt.pct(bm21['dd'])} | "
             f"{bm21['shp']:.2f} |")
    L.append("")

    # ---- 六、动量排名有 α 吗：前 N 名 vs 同候选内随机 N 只 ----
    base_cands = cands_of(df, use_ma=True)
    L += ["## 六、动量排名本身有 α 吗：前 N 名 vs 同候选内随机 N 只", "",
          "同样从「当期满足 MA20 的候选」里选，一边取**动量前 N 名**，"
          f"一边**随机抽 N 只**（重复 {RAND_TRIALS} 次，offset=0 相位）。", "",
          "| 口径 | 实际年化 | 随机中位 | 随机 5%~95% 区间 | 所处分位 | 结论 |",
          "|---|---|---|---|---|---|"]
    for k in (2, 3):
        anns = []
        for trial in range(RAND_TRIALS):
            rng = np.random.default_rng(3000 + trial)
            dec = {t: (list(rng.choice(cs, size=min(k, len(cs)), replace=False)) if cs else [])
                   for t, cs in base_cands.items()}
            eq, _, _, _ = run(df, dec)
            anns.append(bt.metrics(cut(eq, s0))["ann"])
        arr = np.array(anns)
        top_ann = bt.metrics(res[f"top{k}"]["eq"])["ann"]
        pctl = float(np.mean(arr < top_ann))
        verdict = ("**优于随机**（>95 分位）" if pctl > 0.95 else
                   "劣于随机（<5 分位）" if pctl < 0.05 else "**落在随机区间内 → 无 α**")
        L.append(f"| 前 {k} 名 | {bt.pct(top_ann)} | {bt.pct(float(np.median(arr)))} | "
                 f"{bt.pct(float(np.percentile(arr, 5)))} ~ {bt.pct(float(np.percentile(arr, 95)))} | "
                 f"{pctl * 100:.0f}% | {verdict} |")
    L.append("")

    # ---- 七、关键下跌段：退出能力兑现了吗 ----
    CRISIS = [("2015H2 股灾", "2015-07-30", "2016-02-29"),
              ("2018 熊市", "2018-01-01", "2018-12-31"),
              ("2020 疫情", "2020-02-01", "2020-03-31"),
              ("2022 熊市", "2022-01-01", "2022-12-31")]

    def win(eq, a, b):
        e = eq[(eq.index >= pd.Timestamp(a)) & (eq.index <= pd.Timestamp(b))]
        return float(e.iloc[-1] / e.iloc[0] - 1) if len(e) > 1 else float("nan")

    L += ["## 七、关键下跌段：策略的「退出能力」兑现了吗", "",
          "策略宣称的价值是「跌破 MA20 就退出、少亏」，那就该在下跌段里检验。", "",
          "| 区间 | 现行(前2名) | 基准(等权6只) | 差 | 现行段内最大回撤 | 基准段内最大回撤 |",
          "|---|---|---|---|---|---|"]
    for sname, a, b in CRISIS:
        es = res["top2"]["eq"]
        ew = win(es, a, b)
        bw = win(bm, a, b)
        ds = bt.metrics(es[(es.index >= pd.Timestamp(a)) & (es.index <= pd.Timestamp(b))])["dd"]
        db = bt.metrics(bm[(bm.index >= pd.Timestamp(a)) & (bm.index <= pd.Timestamp(b))])["dd"]
        L.append(f"| {sname} | {bt.pct(ew)} | {bt.pct(bw)} | {bt.pct(ew - bw)} | "
                 f"{bt.pct(ds)} | {bt.pct(db)} |")
    L.append("")

    # ---- 八、结论 ----
    t2, t2r = bt.metrics(res["top2"]["eq"]), bt.metrics(res["top2_rescue"]["eq"])
    m1, m3 = bt.metrics(res["top1"]["eq"]), bt.metrics(res["top3"]["eq"])
    mn = bt.metrics(res["top2_noma"]["eq"])
    ma_all = bt.metrics(res["allma"]["eq"])
    L += ["## 八、结论", "",
          f"1. **空仓是最容易被误判的一项，但它不是主因**：全期空仓 "
          f"{m_cash.sum() / n_days * 100:.2f}%（{int(m_cash.sum())} 天），"
          f"同期基准 {bt.pct(b_cash)}；把空仓期改为持有等权篮子（`top2_rescue`），"
          f"年化 {bt.pct(t2['ann'])} → {bt.pct(t2r['ann'])}，回撤 "
          f"{bt.pct(t2['dd'])} → {bt.pct(t2r['dd'])}。**改善有限，说明跑输不是因为坐在现金上。**",
          "",
          f"2. **持仓日就没有超额**：现行口径在 {int((~cmask).sum())} 个持仓交易日里相对基准 "
          f"{bt.pct(rel_inv)}，全期 {bt.pct(rel_gap)} —— 与旧单仓口径（持仓日领先 +2.89%）不同，"
          "**分散到 2 只之后，那点微弱超额也基本消失了**。",
          "",
          f"3. **MA20 过滤在当前口径下不再是「必需」**：关掉它、永远持动量前 2（`top2_noma`），"
          f"年化 {bt.pct(mn['ann'])}、回撤 {bt.pct(mn['dd'])}、夏普 {mn['shp']:.2f} —— "
          f"对比现行 {bt.pct(t2['ann'])} / {bt.pct(t2['dd'])} / {t2['shp']:.2f}。"
          "旧报告里「MA20 必需」是**单仓框架**下的结论（它替单仓挡掉尾部资产）；"
          "一旦持 2 只，这个功能部分由分散接管，过滤的边际价值随之下降。",
          "",
          f"4. **集中度仍然是主要风险来源**：top1 {bt.pct(m1['dd'])} / 夏普 {m1['shp']:.2f} → "
          f"top2 {bt.pct(t2['dd'])} / {t2['shp']:.2f} → top3 {bt.pct(m3['dd'])} / {m3['shp']:.2f}。"
          "回撤随只数单调收窄，而年化几乎不动 —— **多持一只买到的是风险下降，不是收益下降**。",
          "",
          f"5. **相位中位才是真实水平**："
          + "；".join(f"{LABEL[k].replace('**', '')} 中位 {bt.pct(float(np.median(ph[k])))}"
                      for k in ["top1", "top2", "top3", "top5"]) + "，"
          f"基准中位 {bt.pct(float(np.median(bmv)))}。**主表的 +{t2['ann'] * 100:.2f}% 是 "
          f"{REBAL} 个相位里的一个，不代表策略的期望水平。**",
          "",
          f"6. **动量排名在候选内部没有 α**（第六节）：从同样的 MA20 候选里随机抽 N 只"
          f"（{RAND_TRIALS} 次），实际年化落在随机分布的中间区间 —— **「取前 N 名」这个动作"
          "本身不产生超额**，它只决定你承担多少集中度风险。与 `_topn_report.md` 在 21 日口径下"
          "的结论一致。",
          "",
          f"7. **保留 MA20 过滤、放弃「取前 2 名」的集中选择，是更优的组合**：`allma`"
          f"（持有全部合格候选）全期年化 {bt.pct(ma_all['ann'])}、回撤 {bt.pct(ma_all['dd'])}、"
          f"夏普 {ma_all['shp']:.2f}，相位中位 {bt.pct(float(np.median(ph['allma'])))}、"
          f"极差 {float(ph['allma'].max() - ph['allma'].min()) * 100:.2f}% —— "
          "既守住了「过滤掉跌破 MA20 的资产」这一能力，又拿到了分散化的好处。",
          "",
          "> 模拟结果，非投资建议。", ""]

    open(OUT_MD, "w", encoding="utf-8").write("\n".join(L))
    print("\n".join(L))
    print(f"\n-> {OUT_MD}")

    # ---- 图 ----
    fig, (ax1, ax2) = plt.subplots(1, 2, figsize=(15, 5.2))
    style = {"top1": (1.2, 0.7, "-"), "top2": (2.0, 1.0, "-"), "top2_noma": (1.4, 0.9, "-."),
             "top2_rescue": (1.4, 0.9, "--"), "allma": (1.4, 0.9, "-")}
    for k in ["top1", "top2", "top2_noma", "top2_rescue", "allma"]:
        lw, al, ls = style[k]
        ax1.plot(res[k]["eq"].index, res[k]["eq"].values, lw=lw, alpha=al, ls=ls,
                 label=LABEL[k].replace("**", ""))
    ax1.plot(bm.index, bm.values, lw=1.4, ls=":", color="k", label="六类等权·10日再平衡（基准）")
    ax1.legend(fontsize=8)
    ax1.set_title("10日/前2名 口径分层对照（净值）")
    ax1.grid(alpha=0.3)

    for k in ["top1", "top2", "top3", "top5"]:
        m = bt.metrics(res[k]["eq"])
        ax2.scatter(abs(m["dd"]) * 100, m["ann"] * 100, s=80, zorder=3)
        ax2.annotate(k, (abs(m["dd"]) * 100, m["ann"] * 100),
                     textcoords="offset points", xytext=(7, 4), fontsize=9)
    ax2.scatter(abs(mb["dd"]) * 100, mb["ann"] * 100, s=110, color="crimson", zorder=3)
    ax2.annotate("基准", (abs(mb["dd"]) * 100, mb["ann"] * 100),
                 textcoords="offset points", xytext=(7, 4), fontsize=9)
    ax2.set_xlabel("最大回撤（绝对值 %）")
    ax2.set_ylabel("年化 %")
    ax2.set_title("收益-回撤：左上角更优")
    ax2.grid(alpha=0.3)
    fig.tight_layout()
    fig.savefig(OUT_PNG, dpi=140, facecolor="white")
    print(f"图: {OUT_PNG}")


if __name__ == "__main__":
    main()
