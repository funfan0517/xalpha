# -*- coding: utf-8 -*-
"""标的池对照：当前生效的六类资产池 vs 旧「唯一池全部场内标的」口径。

本脚本是「把标的收窄为六类资产」这一决策的证据/回归件：六类资产（场外主仓执行 /
场内代理作动量信号）已在 `rule.py` 生效，此处用同一引擎把两者放在同口径下对照。
标的池不再硬编码，六类资产取自 `rule.ASSETS`（由唯一池 `data/_universe.md` 派生）。

| 资产 | 场外主仓（执行） | 场内代理（信号） |
|---|---|---|
| 中长债 | 003377 广发中债7-10年国开债指数C | 511260 十年国债ETF国泰 |
| 中证红利 | 012644 招商中证红利ETF联接C | 515080 中证红利ETF招商 |
| 纳指100 | 270042 广发纳斯达克100ETF联接(QDII)A | 513100 纳指ETF国泰 |
| 中证A500 | 023299 汇添富中证A500指数增强C | 563360 A500ETF华泰柏瑞 |
| 科创50 | 011609 易方达上证科创板50ETF联接C | 588000 科创50ETF华夏 |
| 黄金 | 000216 华安黄金ETF联接A | 518880 黄金ETF华安 |

引擎/口径与官方 backtest 完全一致（120 日动量排名取首个 ≥MA20 单仓，
21 交易日再平衡，卖出 ≤7 交易日 1.5%）。只变「池」，隔离标的选择这一个变量。

用法: python strategies/momentum_rotation/research/_pool6_test.py
输出: research/_pool6_report.md · research/_pool6_nav.png
"""
import os
import sys
from datetime import datetime

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402
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
from pipeline import bt_stats, universe  # noqa: E402

# 6 资产取自 rule（单一来源，由唯一池派生）；旧全池 = 唯一池全部场内标的
POOL6 = [(r["proxy"], r["off_code"], r["name"]) for r in rule.ASSETS]
CODES6 = [c for c, _, _ in POOL6]
LABEL = rule.LABEL6
FULL = universe.inner_codes()
OUT_MD = os.path.join(_DIR, "_pool6_report.md")
OUT_PNG = os.path.join(_DIR, "_pool6_nav.png")

SEGS = [("十年全期", None, None),
        ("2016~2021H1", "2021-06-30", None),
        ("2021H2~2026", None, "2021-07-01")]


def nav(df):
    """按官方引擎跑策略 + 等权基准，返回 (策略净值, 基准净值, 策略交易, 基准周期)。"""
    s0 = df.index[rule.MIN_HIST]
    strat, trades = bt.run_strategy(df, rebal=21, topn=1)
    bm, bm_trades = bt.run_benchmark(df, step=21)
    strat = (strat / strat.iloc[0])[strat.index >= s0]
    bm = (bm / bm.iloc[0])[bm.index >= s0]
    return strat, bm, trades, bm_trades


def seg_metrics(eq, end=None, lo=None):
    e = eq
    if lo is not None:
        e = e[e.index >= pd.Timestamp(lo)]
    if end is not None:
        e = e[e.index <= pd.Timestamp(end)]
    return bt.metrics(e) if len(e) > 60 else None


def run_pool(df):
    strat, bm, trades, bm_trades = nav(df)
    rows = []
    for name, end, lo in SEGS:
        ms, mb = seg_metrics(strat, end, lo), seg_metrics(bm, end, lo)
        if ms:
            rows.append((name, ms, mb))
    return dict(strat=strat, bm=bm, trades=trades, bm_trades=bm_trades, rows=rows)


def main():
    df6 = rule.load_wide(codes=CODES6)
    dfall = rule.load_wide(codes=FULL)
    if df6.shape[1] < len(CODES6):
        miss = [c for c in CODES6 if c not in df6.columns]
        sys.exit(f"十年库缺少: {miss}（先 python strategies/momentum_rotation/fetch.py \"{','.join(miss)}\"）")

    r6, rall = run_pool(df6), run_pool(dfall)
    ts6, tsall = bt_stats.trade_stats(r6["trades"]), bt_stats.trade_stats(rall["trades"])
    full_tag = f"{len(FULL)} 只全池"

    print(f"6 池标的: {' '.join(LABEL[c] for c in CODES6)}")
    print(f"数据区间 {df6.index.min().date()} ~ {df6.index.max().date()}\n")

    L = [f"# 动量轮动 · 六类资产池（当前生效） vs {full_tag}（旧口径）", "",
         f"> 生成 {datetime.now().strftime('%Y-%m-%d %H:%M')} · 引擎/口径与官方 backtest 完全一致，只变标的池。",
         "",
         "## 标的（六类资产，现行池）", "",
         "| 资产 | 场外主仓（执行） | 场内代理（信号） |", "|---|---|---|"]
    for c, off, t in POOL6:
        L.append(f"| {t} | `{off}` | `{c}` |")
    L += ["",
          f"> 数据区间 {df6.index.min().date()} ~ {df6.index.max().date()}；"
          f"`511260` 为本次收窄时新补入十年库。", "",
          "## 分段业绩对照", "",
          "| 段 | 池 | 年化 | 最大回撤 | 波动 | 夏普 | 总收益 | 基准年化 |",
          "|---|---|---|---|---|---|---|---|"]
    for i, (name, m6, mb6) in enumerate(r6["rows"]):
        name_all, ma, mba = rall["rows"][i]
        L.append(f"| {name} | **六类资产池** | {bt.pct(m6['ann'])} | {bt.pct(m6['dd'])} "
                 f"| {bt.pct(m6['vol'])} | {m6['shp']:.2f} | {bt.pct(m6['ret'])} "
                 f"| {bt.pct(mb6['ann']) if mb6 else '—'} |")
        L.append(f"| {name} | {full_tag} | {bt.pct(ma['ann'])} | {bt.pct(ma['dd'])} "
                 f"| {bt.pct(ma['vol'])} | {ma['shp']:.2f} | {bt.pct(ma['ret'])} "
                 f"| {bt.pct(mba['ann']) if mba else '—'} |")
    L.append("")
    L += bt_stats.section_rows(
        [("六类资产池（单笔持仓周期）", ts6), (f"{full_tag}（单笔持仓周期）", tsall)],
        title="单笔统计对照（全期）",
        note="策略单笔=entry收盘→exit收盘的持仓期收益(未含佣金/滑点, 已含≤7交易日1.5%赎回费从净值端扣除)。"
             "数据为 xueqiu 前复权；模拟结果，非投资建议。")
    L += ["", "## 六类资产池持仓轨迹（全期）", "",
          "| 换仓日 | 持有 |", "|---|---|"]
    for t, code in _decisions(df6):
        L.append(f"| {t} | {LABEL.get(code, code)} |")
    open(OUT_MD, "w", encoding="utf-8").write("\n".join(L))
    print("\n".join(L[:24]))
    print(f"\n-> {OUT_MD}")

    fig, ax = plt.subplots(figsize=(12, 5))
    ax.plot(r6["strat"].index, r6["strat"].values, label="六类资产池", lw=1.2)
    ax.plot(rall["strat"].index, rall["strat"].values, label=full_tag, lw=1.0, alpha=0.75)
    ax.plot(r6["bm"].index, r6["bm"].values, label="六类等权基准", lw=0.9, alpha=0.5, ls="--")
    ax.legend()
    ax.set_title(f"动量轮动：六类资产池 vs {full_tag}（净值）")
    ax.grid(alpha=0.3)
    fig.tight_layout()
    fig.savefig(OUT_PNG, dpi=140, facecolor="white")
    print(f"图: {OUT_PNG}")


def _decisions(df):
    """复刻 run_strategy 的换仓决策，输出 (日期, 代码) 序列（空仓不列）。"""
    dates, n = df.index, len(df)
    ma_s = df.rolling(rule.MA).mean()
    first = {c: df[c].first_valid_index() for c in df.columns}
    dec = []
    for t in range(rule.MIN_HIST, n, 21):   # 旧口径月频 21 日
        cutoff = dates[t - rule.MIN_HIST]
        elig = [c for c in df.columns if first[c] <= cutoff]
        mom = {}
        for c in elig:
            p0, p1 = df[c].iloc[t - rule.LOOKBACK], df[c].iloc[t]
            if p0 > 0 and not pd.isna(p0) and not pd.isna(p1):
                mom[c] = p1 / p0 - 1.0
        for c in sorted(mom, key=mom.get, reverse=True):
            if not pd.isna(ma_s[c].iloc[t]) and df[c].iloc[t] >= ma_s[c].iloc[t]:
                dec.append((str(dates[t].date()), c))
                break
    return dec


if __name__ == "__main__":
    main()
