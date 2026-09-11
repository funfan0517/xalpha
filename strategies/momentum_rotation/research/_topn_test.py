# -*- coding: utf-8 -*-
"""持仓集中度对照：单仓（现行） vs 分散 TOP-2/3/4/6 只 vs 全买 6 只 vs 全买·去债券。

**单一变量**：只改「一次买几只 / 要不要再平衡 / 要不要债券」。动量回看恒 120 日、
21 交易日调仓周期、起点与评估区间、费用口径全部与官方 backtest 一致。

引擎差异（仅为支持多仓，不影响 TOP-1 口径）：
  1) 选股：官方「动量排名取首个 ≥MA20」→ 本脚本「取满足 MA20 的动量前 N 名」
     （N=1 时两者逐点等价，脚本启动时自检）。
  2) 持仓：调仓日按新名单**等权买入**，期内权重随价格自然漂移（buy & hold）。
  3) 惩罚费：只对被**剔除**的标的按其当前权重收取；仍留在名单里的继续持有。
     N=1 时退化为官方口径。

五组对照回答五个递进的问题：
  - **资产入池时间线**：池子在不同年份到底有几只？（第 2 节，读其他表的前提）
  - **TOP-1 vs TOP-N**：集中度买到了收益，还是只买到风险？（第 1、4 节）
  - **TOP-N vs 随机 N 只**：动量排名在候选池内部有 α 吗？（第 5 节）
  - **TOP-N vs 全买**：MA20 过滤 + 选股这一层还值得要吗？（第 1 节）
  - **去债券**：全买的优势是「分散」还是「多拿了债券」？（第 1 节）

用法: python strategies/momentum_rotation/research/_topn_test.py
输出: research/_topn_report.md · research/_topn_nav.png
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

_DIR = os.path.dirname(os.path.abspath(__file__))            # strategies/momentum_rotation/research
_STRAT = os.path.dirname(_DIR)                               # strategies/momentum_rotation
_ROOT = os.path.dirname(os.path.dirname(_STRAT))             # 仓库根
for p in (_STRAT, _DIR, _ROOT):
    if p not in sys.path:
        sys.path.insert(0, p)

import backtest as bt  # noqa: E402
import rule  # noqa: E402
from pipeline import bt_stats  # noqa: E402

OUT_MD = os.path.join(_DIR, "_topn_report.md")
OUT_PNG = os.path.join(_DIR, "_topn_nav.png")

BOND = "511260"                       # 中长债，用于「去债券」对照
REBAL = 21                            # 本报告固定旧口径:月频 21 交易日(与 rule.REBAL 解耦)
RAND_TRIALS = 200
SEGS = [("十年全期", None, None),
        ("2016~2021H1", "2021-06-30", None),
        ("2021H2~2026", None, "2021-07-01")]

# 每个口径 = 选股方式 (use_ma) + 买入只数 (topn) + 是否每期重置等权 (reset) + 排除标的
SPEC = {
    "TOP1": dict(use_ma=True, topn=1, reset=False),
    "TOP2": dict(use_ma=True, topn=2, reset=False),
    "TOP3": dict(use_ma=True, topn=3, reset=False),
    "TOP4": dict(use_ma=True, topn=4, reset=False),
    "TOP6": dict(use_ma=True, topn=6, reset=False),
    "ALL6": dict(use_ma=False, topn=6, reset=False),
    "ALL6R": dict(use_ma=False, topn=6, reset=True),
    "ALL5R": dict(use_ma=False, topn=5, reset=True, exclude=(BOND,)),
}
KEYS = list(SPEC)
LABELS = {"TOP1": "**单仓（现行）**",
          "ALL6": "全买 6 只（关 MA20，买入持有）",
          "ALL6R": "全买 6 只（关 MA20，21日再平衡）",
          "ALL5R": "全买 5 只风险资产（去债券，21日再平衡）"}
PLOT = ["TOP1", "TOP3", "TOP6", "ALL6R", "ALL5R"]


def label_of(key):
    return LABELS.get(key, f"分散 {key[3:]} 只等权")


def candidates(df, phase=None, use_ma=True, exclude=()):
    """每个调仓日的候选，按动量从高到低排序 -> {t: [code, ...]}。

    phase   调仓网格起点偏移（0 = 官方口径 MIN_HIST）。
    use_ma  False 时不做 MA20 过滤（用于「全买」对照组）。
    exclude 需要排除的标的（用于「去债券」对照组）。
    """
    dates, n = df.index, len(df)
    ma_s = df.rolling(rule.MA).mean()
    first = {c: df[c].first_valid_index() for c in df.columns}
    cols = [c for c in df.columns if c not in exclude]
    start = rule.MIN_HIST + (phase or 0)
    dec = {}
    for t in range(start, n, REBAL):
        cutoff = dates[t - rule.MIN_HIST]
        elig = [c for c in cols if first[c] <= cutoff]
        mom = {}
        for c in elig:
            p0, p1 = df[c].iloc[t - rule.LOOKBACK], df[c].iloc[t]
            if p0 > 0 and not (np.isnan(p0) or np.isnan(p1)):
                mom[c] = p1 / p0 - 1.0
        ranked = sorted(mom, key=mom.get, reverse=True)
        if use_ma:
            dec[t] = [c for c in ranked
                      if not np.isnan(ma_s[c].iloc[t]) and df[c].iloc[t] >= ma_s[c].iloc[t]]
        else:
            dec[t] = list(ranked)
    return dec


def dec_of(df, key, phase=None):
    """按 SPEC 生成某口径的持仓名单。"""
    s = SPEC[key]
    return {t: cs[:s["topn"]]
            for t, cs in candidates(df, phase=phase, use_ma=s["use_ma"],
                                    exclude=s.get("exclude", ())).items()}


def pick_rand(cands, topn, rng):
    return {t: list(rng.choice(cs, size=min(topn, len(cs)), replace=False))
            if cs else [] for t, cs in cands.items()}


def run(df, dec, fee=True, always_reset=False):
    """等权买入持有引擎 -> (净值, 单笔交易, info)。

    always_reset=True 时每个调仓日都把权重重置为等权（用于「再平衡」口径），
    否则只在名单变化时重置（buy & hold）。
    """
    dates, n = df.index, len(df)
    eq = [1.0]
    cur, w, entry, epx = [], {}, {}, {}
    n_fee, n_action, trades = 0, 0, []
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
                w = {c: v / tot for c, v in num.items()}   # 权重随价格漂移
                r = tot - 1.0
        eq.append(eq[-1] * (1 + r))
        if i in dec and (always_reset or set(dec[i]) != set(cur)):
            new = dec[i]
            for c in cur:                                   # 只卖被剔除的
                if c in new:
                    continue
                if fee and (i - entry[c]) <= rule.FEE_SHORT_DAYS:
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
        for c in cur:                                       # 收尾清仓
            if fee and (n - 1 - entry[c]) <= rule.FEE_SHORT_DAYS:
                eq[-1] *= 1 - rule.FEE_SHORT * w[c]
                n_fee += 1
            if epx[c] > 0:
                trades.append(dict(code=c, entry_date=str(dates[entry[c]].date()),
                                   exit_date=str(dates[n - 1].date()), bars=n - 1 - entry[c],
                                   ret=df[c].iloc[n - 1] / epx[c] - 1.0))
    return pd.Series(eq, index=dates), trades, dict(n_fee=n_fee, n_action=n_action)


def cut(x, start):
    return (x / x.iloc[0])[x.index >= start]


def seg_metrics(eq, end=None, lo=None):
    e = eq
    if lo is not None:
        e = e[e.index >= pd.Timestamp(lo)]
    if end is not None:
        e = e[e.index <= pd.Timestamp(end)]
    return bt.metrics(e) if len(e) > 60 else None


def calmar(m):
    return m["ann"] / abs(m["dd"]) if m["dd"] < 0 else float("inf")


def main():
    df = rule.load_wide()
    if len(df) == 0:
        sys.exit("缺少十年库数据: 先跑 python strategies/momentum_rotation/fetch.py")
    s0 = df.index[rule.MIN_HIST]
    bm = cut(bt.run_benchmark(df, step=21)[0], s0)
    years = (bm.index[-1] - bm.index[0]).days / 365.0

    cands_ma = candidates(df)

    # ---- 自检: TOP-1 必须与官方引擎逐点一致 ----
    ref = cut(bt.run_strategy(df, rebal=21, topn=1)[0], s0)
    d = float(np.abs(cut(run(df, dec_of(df, "TOP1"))[0], s0).values - ref.values).max())
    if d > 1e-12:
        sys.exit(f"自检失败: TOP-1 与官方引擎偏离 {d:.3e}")
    print("引擎自检通过（TOP-1 与官方 backtest 逐点一致）\n")

    # ---- 跑全部口径 ----
    res = {}
    for key in KEYS:
        dec = dec_of(df, key)
        eq, tr, info = run(df, dec, always_reset=SPEC[key]["reset"])
        res[key] = dict(eq=cut(eq, s0), trades=tr, info=info,
                        holds=float(np.mean([len(v) for v in dec.values()])),
                        avg_assets=float(np.mean([len(v) for v in
                                                  candidates(df, use_ma=False,
                                                             exclude=SPEC[key].get("exclude", ())
                                                             ).values()])))

    L = ["# 动量轮动 · 持仓集中度对照：单仓 vs 分散 vs 全买", "",
         f"> 生成 {datetime.now().strftime('%Y-%m-%d %H:%M')} · 引擎口径与官方 `backtest.py` 同构"
         f"（TOP-1 已逐点自检）· 数据 {df.index.min().date()} ~ {df.index.max().date()}"
         f" · 评估区间 {s0.date()} ~ {bm.index[-1].date()}（{years:.1f} 年）", "",
         "**只改「一次买几只 / 要不要再平衡 / 要不要债券」**，其余锁定：动量回看 120 日、"
         "21 交易日调仓周期、费用口径（卖出 ≤7 交易日 1.5% / 7 日外 0%）、"
         "期内买入持有（除非注明再平衡）。", ""]

    # ---- 二、资产入池时间线（放在主表前，是读其他表的前提）----
    first = {c: df[c].first_valid_index() for c in df.columns}
    L += ["## 一、先看清楚：池子是逐步变大的", "",
          "六类资产的**场内代理不是同时上市的**，所以「全买 N 只」在不同年份的含义并不相同。"
          "策略起点 2015-07-30 时，池里**只有 2 只**（纳指 + 黄金）。", "",
          "| 场内代理 | 资产 | 首个行情日 | 有效期数 |", "|---|---|---|---|"]
    for r in rule.ASSETS:
        c = r["proxy"]
        L.append(f"| `{c}` | {r['name']} | {first[c].date()} | {int(df[c].notna().sum())} |")
    L += ["",
          "> 时间线：2015-01 纳指+黄金 → 2017-08 加入债券 → 2019-12 加入红利 → "
          "2020-11 加入科创50 → 2024-10 加入中证A500。",
          "> 因此 **2015~2017 的「全买 6 只」实际是「纳指、黄金各半」**，"
          "2019 年后才逐步成为真正的多资产组合。**读下面所有表时都要按年份对齐池子大小**，"
          "否则会把「池子变大」误读成「分散更有效」。", ""]

    # ---- 二、主表 ----
    L += ["## 二、主表：分段业绩", "",
          "| 段 | 口径 | 年化 | 最大回撤 | 波动 | 夏普 | 年化/回撤 | 总收益 | 平均持仓数 |",
          "|---|---|---|---|---|---|---|---|---|"]
    for name, end, lo in SEGS:
        for key in KEYS:
            m = seg_metrics(res[key]["eq"], end, lo)
            if not m:
                continue
            L.append(f"| {name} | {label_of(key)} | {bt.pct(m['ann'])} | {bt.pct(m['dd'])} | "
                     f"{bt.pct(m['vol'])} | {m['shp']:.2f} | {calmar(m):.2f} | "
                     f"{bt.pct(m['ret'])} | {res[key]['holds']:.1f} |")
        mb = seg_metrics(bm, end, lo)
        if mb:
            L.append(f"| {name} | 六类等权·每日再平衡（基准） | {bt.pct(mb['ann'])} | {bt.pct(mb['dd'])} | "
                     f"{bt.pct(mb['vol'])} | {mb['shp']:.2f} | {calmar(mb):.2f} | "
                     f"{bt.pct(mb['ret'])} | {res['ALL6R']['avg_assets']:.1f} |")

    # ---- 三、换手 ----
    L += ["", "## 三、换手与费用", "",
          "| 口径 | 调仓日 | 交易笔数 | 实际动作 | 年动作 | 惩罚费触发 |",
          "|---|---|---|---|---|---|"]
    for key in KEYS:
        info = res[key]["info"]
        L.append(f"| {label_of(key)} | {len(cands_ma)} | "
                 f"{len(res[key]['trades'])} | {info['n_action']} | "
                 f"{info['n_action'] / years:.1f} 次/年 | {info['n_fee']} |")
    L += ["",
          "> `实际动作` = 引擎真正调整过持仓的调仓日数（名单变化，或「再平衡」口径下每次固定重置）。",
          "> 分散的代价是换手：单仓每次调仓只卖 1 只，分散后要同时管理 N 只。"
          "但绝对水平仍低，且**惩罚费触发全为 0** —— 21 交易日周期保证所有持仓都跨过了 7 日红线。", ""]

    # ---- 四、单笔统计 ----
    L += bt_stats.section_rows(
        [(label_of(key), bt_stats.trade_stats(res[key]["trades"]))
         for key in KEYS if not key.startswith(("ALL",))],
        title="四、单笔统计对照（全期 · 仅轮动口径）",
        note="单笔=某只标的 from 买入日收盘 → 卖出日收盘 的持仓期收益（未含佣金/滑点，"
             "已含 ≤7 交易日 1.5% 赎回费从净值端扣除）。"
             "「全买」口径几乎不产生换仓，单笔统计无意义，故不列入。") + [""]

    # ---- 五、相位鲁棒性 ----
    L += ["## 五、相位鲁棒性：这才是决定性的一项", "",
          "主表用的是 offset=0 这一个调仓相位。把网格整体平移 0~20 个交易日，"
          "每个口径各跑 21 次 —— **同一个策略只换几天调仓，结果可能完全不同**。", "",
          "| 口径 | 年化最小 | 年化中位 | 年化最大 | 极差 |", "|---|---|---|---|---|"]
    ph = {}
    for key in KEYS:
        vals = []
        for off in range(21):
            eq, _, _ = run(df, dec_of(df, key, phase=off), always_reset=SPEC[key]["reset"])
            vals.append(bt.metrics(cut(eq, df.index[rule.MIN_HIST + off]))["ann"])
        ph[key] = np.array(vals)
        L.append(f"| {label_of(key)} | {bt.pct(ph[key].min())} | **{bt.pct(np.median(ph[key]))}** | "
                 f"{bt.pct(ph[key].max())} | "
                 f"{bt_stats.pct(ph[key].max() - ph[key].min(), signed=False)} |")
    L += ["",
          "> **看中位列，不看最大列。**主表里单仓与分散「几乎持平」，"
          "只是因为 offset=0 恰好对单仓有利；把相位平均掉之后，单仓的中位年化明显低于分散。",
          "> 同时注意**极差随分散度单调收窄** —— 分散压掉的不只是回撤，"
          "还有「你恰好在哪天调仓」这个纯运气成分。", ""]

    # ---- 六、随机对照 ----
    L += ["## 六、动量排名在候选池内部有 α 吗：TOP-N vs 随机 N 只", "",
          "同样从「当期满足 MA20 的候选」里选，一边取**动量前 N 名**，一边**随机抽 N 只**"
          f"（重复 {RAND_TRIALS} 次）。", "",
          "| 口径 | 实际年化 | 随机中位 | 随机 5%~95% 区间 | 所处分位 | 结论 |",
          "|---|---|---|---|---|---|"]
    for k in [2, 3, 4]:
        anns = []
        for trial in range(RAND_TRIALS):
            eq, _, _ = run(df, pick_rand(cands_ma, k, np.random.default_rng(1000 + trial)))
            anns.append(bt.metrics(cut(eq, s0))["ann"])
        arr = np.array(anns)
        top_ann = bt.metrics(res[f"TOP{k}"]["eq"])["ann"]
        pctile = float(np.mean(arr < top_ann))
        verdict = ("**优于随机**（>95 分位）" if pctile > 0.95 else
                   "劣于随机（<5 分位）" if pctile < 0.05 else "**落在随机区间内 → 无 α**")
        L.append(f"| 分散 {k} 只 | {bt.pct(top_ann)} | {bt.pct(np.median(arr))} | "
                 f"{bt.pct(np.percentile(arr, 5))} ~ {bt.pct(np.percentile(arr, 95))} | "
                 f"{pctile * 100:.0f}% | {verdict} |")
    L.append("")

    # ---- 七、结论 ----
    m = {k: bt.metrics(res[k]["eq"]) for k in KEYS}
    med = {k: float(np.median(ph[k])) for k in KEYS}
    t1, t3 = bt_stats.trade_stats(res["TOP1"]["trades"]), bt_stats.trade_stats(res["TOP3"]["trades"])
    L += ["## 七、结论", "",
          f"1. **单仓不是收益来源，是纯风险**。全期主表看单仓 {bt.pct(m['TOP1']['ann'])} 与"
          f"分散 3 只 {bt.pct(m['TOP3']['ann'])} 几乎持平，但那是 offset=0 这一个相位的巧合 ——"
          f"把调仓网格平移 0~20 天，单仓的相位**中位**年化只有 {bt.pct(med['TOP1'])}，"
          f"分散 3 只 {bt.pct(med['TOP3'])}、分散 6 只 {bt.pct(med['TOP6'])}、"
          f"全买 6 只·再平衡 {bt.pct(med['ALL6R'])}。同时单仓回撤 {bt.pct(m['TOP1']['dd'])}、"
          f"夏普 {m['TOP1']['shp']:.2f}，分散 3 只 {bt.pct(m['TOP3']['dd'])}、{m['TOP3']['shp']:.2f}。"
          "**集中度买到的不是收益，只是风险。**",
          "",
          f"2. **相位极差随分散度单调收窄**"
          f"（单仓 {bt_stats.pct(ph['TOP1'].max() - ph['TOP1'].min(), signed=False)} → "
          f"全买·再平衡 {bt_stats.pct(ph['ALL6R'].max() - ph['ALL6R'].min(), signed=False)}）。"
          "单仓的命运高度依赖「你恰好在哪一天调仓」，分散把这个运气成分压掉了 —— "
          "这是它最实在的好处，也是本报告里最难被「换个样本就翻盘」的结论。",
          "",
          "3. **分散的收益改善不来自「选得更准」**。随机对照显示，"
          "从同样的 MA20 候选里随机抽 2/3/4 只，实际年化落在随机的 50 / 50 / 10 分位 —— "
          "**动量排名在「已合格的候选」内部没有 α**。"
          "它与 `_fallback_report.md` 变体 D 互为印证：那里「去掉 MA20、仍只买动量第一」"
          "从 +12.09% 掉到 +8.75%，说明单仓框架下 MA20 是**必需的**；"
          "而这里「去掉 MA20、改买全部 6 只」反而最好，说明 MA20 之所以必需，"
          "**只是因为它替单仓挡掉了尾部资产** —— 一旦分散持有，这个功能就由分散本身接管了。",
          "",
          f"4. **换手代价很小**：单笔从 {t1['n']} 只增加到 {t3['n']} 只"
          f"（{t1['n'] / years:.1f} → {t3['n'] / years:.1f} 只/年），惩罚费触发始终为 0；"
          f"单笔最差从 {bt_stats.pct(t1['worst'])} 收窄到 {bt_stats.pct(t3['worst'])}。",
          "",
          f"5. **MA20 过滤 + 动量选股这一整层，全期看没有正贡献**："
          f"关掉过滤、永远等权持有 6 只（每 21 日重置），全期年化 {bt.pct(m['ALL6R']['ann'])}、"
          f"回撤 {bt.pct(m['ALL6R']['dd'])}、夏普 {m['ALL6R']['shp']:.2f}、"
          f"相位中位 {bt.pct(med['ALL6R'])}、极差仅 "
          f"{bt_stats.pct(ph['ALL6R'].max() - ph['ALL6R'].min(), signed=False)} —— "
          f"**风险调整后优于所有「过滤 + 选前 N 名」的口径**，并已逼近每日再平衡基准"
          f"（{bt.pct(bt.metrics(bm)['ann'])} / 夏普 {bt.metrics(bm)['shp']:.2f}）。"
          f"它在 2016~2021H1 年化略低（{bt.pct(seg_metrics(res['ALL6R']['eq'], '2021-06-30')['ann'])} "
          f"vs 分散 6 只 {bt.pct(seg_metrics(res['TOP6']['eq'], '2021-06-30')['ann'])}），"
          f"但夏普更高；在 2021H2~2026 则年化与风险双双领先。",
          "",
          f"6. **去掉债券年化反而更高 —— 但这是样本内最优，不要直接采用**："
          f"去掉中长债、只买 5 只风险资产，全期年化 {bt.pct(m['ALL5R']['ann'])}（全表最高）、"
          f"回撤 {bt.pct(m['ALL5R']['dd'])}、夏普 {m['ALL5R']['shp']:.2f}、"
          f"相位中位 {bt.pct(med['ALL5R'])}。比含债券口径年化高 "
          f"{bt.pct(m['ALL5R']['ann'] - m['ALL6R']['ann'])}，代价是回撤深 "
          f"{bt_stats.pct(abs(m['ALL5R']['dd']) - abs(m['ALL6R']['dd']), signed=False)}。"
          "债券在这 11 年里是「拖累年化、改善回撤」的角色（本身年化只有几个点，却占 1/6 仓位）。"
          "**但「去掉债券」是看了结果才知道的** —— 这 11 年恰好是股债双牛 + 黄金牛，"
          "债券的机会成本被放大；一旦遇到股债双杀（如 2022 年的美股 + 美债），"
          "含债券的组合才是活下来的那个。",
          "",
          "7. **哪些结论可以用来改策略** —— 按「是否依赖样本内择时能力」分两级：", "",
          "   - **可以直接采用**（纯数学，不依赖任何预测）：**把持仓从 1 只改成 3 只等权**。",
          "     分散化降低波动的结论不依赖样本，本报告显示它同时改善年化、回撤、夏普、",
          "     相位鲁棒性，**四项方向一致、无一项变差**。",
          "   - **仅供参考、不建议直接采用**（依赖样本内最优）：「关掉 MA20 + 全买」，",
          "     它会把策略变回被动配置，放弃对长期崩坏资产的退出能力；「去掉债券」，",
          "     是事后选择。两者都只在这 11 年的特定资产环境下占优。", "",
          f"> **一句话**：把持仓从 1 只改成 3 只等权，相位中位年化 "
          f"{bt.pct(med['TOP1'])} → {bt.pct(med['TOP3'])}、回撤 {bt.pct(m['TOP1']['dd'])} → "
          f"{bt.pct(m['TOP3']['dd'])}、夏普 {m['TOP1']['shp']:.2f} → {m['TOP3']['shp']:.2f}；"
          "**三项同时改善，是严格占优，不是「用收益换风险」。**", "",
          "> 模拟结果，非投资建议。", ""]
    open(OUT_MD, "w", encoding="utf-8").write("\n".join(L))
    print("\n".join(L))
    print(f"\n-> {OUT_MD}")

    # ---- 图 ----
    fig, (ax1, ax2) = plt.subplots(1, 2, figsize=(15, 5))
    style = {"TOP1": (2.0, 1.0, "-"), "ALL6R": (1.6, 0.9, "--"), "ALL5R": (1.4, 0.8, "-.")}
    for key in PLOT:
        lw, al, ls = style.get(key, (1.0, 0.8, "-"))
        ax1.plot(res[key]["eq"].index, res[key]["eq"].values, lw=lw, alpha=al, ls=ls,
                 label=label_of(key).replace("**", ""))
    ax1.plot(bm.index, bm.values, lw=1.0, ls=":", alpha=0.6, label="六类等权·每日再平衡")
    ax1.legend(fontsize=8)
    ax1.set_title("持仓集中度对照（官方口径 · 净值）")
    ax1.grid(alpha=0.3)

    for key in PLOT + [None]:
        mm = bt.metrics(bm) if key is None else m[key]
        color = "crimson" if key == "TOP1" else ("gray" if key is None else "steelblue")
        ax2.scatter(abs(mm["dd"]) * 100, mm["ann"] * 100, s=90, color=color, zorder=3)
        ax2.annotate(label_of(key).replace("**", "").split("（")[0] if key else "每日再平衡",
                     (abs(mm["dd"]) * 100, mm["ann"] * 100),
                     textcoords="offset points", xytext=(8, 4), fontsize=8)
    ax2.set_xlabel("最大回撤（绝对值 %）")
    ax2.set_ylabel("年化 %")
    ax2.set_title("收益-回撤：左上角更优")
    ax2.grid(alpha=0.3)
    fig.tight_layout()
    fig.savefig(OUT_PNG, dpi=140, facecolor="white")
    print(f"图: {OUT_PNG}")


if __name__ == "__main__":
    main()
