# -*- coding: utf-8 -*-
"""A 全球相对动量轮动 · 官方十年回测(唯一保留版本)
引擎: 21交易日步进 · 当日收盘算信号并换仓 · 当日收益归旧仓(无前视)
成本: 卖出 ≤7交易日 1.5% / 7日外 0% (月频≈0) · 数据 xueqiu 十年库
用法: python strategies/momentum_rotation/backtest.py
"""
import io
import json
import sys
from datetime import datetime

import numpy as np
import pandas as pd

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8")

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt

plt.rcParams["font.sans-serif"] = ["Microsoft YaHei", "SimHei", "DejaVu Sans"]
plt.rcParams["axes.unicode_minus"] = False

import os

_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

import rule
from pipeline import bt_stats


def pct(x):
    return f"{x * 100:+.2f}%"


def run_strategy(df):
    dates, n = df.index, len(df)
    ma_s = df.rolling(rule.MA).mean()
    rebal = list(range(rule.MIN_HIST, n, rule.REBAL))
    first = {c: df[c].first_valid_index() for c in df.columns}
    dec = {}
    for t in rebal:
        cutoff = dates[t - rule.MIN_HIST]
        elig = [c for c in df.columns if first[c] <= cutoff]
        mom = {}
        for c in elig:
            p0, p1 = df[c].iloc[t - rule.LOOKBACK], df[c].iloc[t]
            if p0 > 0 and not (np.isnan(p0) or np.isnan(p1)):
                mom[c] = p1 / p0 - 1.0
        ranked = sorted(mom, key=mom.get, reverse=True)
        chosen = []
        for c in ranked:
            if not np.isnan(ma_s[c].iloc[t]) and df[c].iloc[t] >= ma_s[c].iloc[t]:
                chosen = [c]
                break
        dec[t] = chosen
    eq, cur, entry, epx = [1.0], [], {}, {}
    trades = []
    for i in range(1, n):
        r = 0.0
        if cur:
            p0, p1 = df[cur[0]].iloc[i - 1], df[cur[0]].iloc[i]
            if p0 > 0 and not (np.isnan(p0) or np.isnan(p1)):
                r = p1 / p0 - 1.0
        eq.append(eq[-1] * (1 + r))
        if i in dec:
            new = dec[i]
            if new != cur:
                if cur:
                    c = cur[0]
                    if (i - entry[c]) <= rule.FEE_SHORT_DAYS:
                        eq[-1] *= 1 - rule.FEE_SHORT
                    if epx[c] > 0:
                        trades.append(dict(code=c, entry_date=str(dates[entry[c]].date()),
                                           exit_date=str(dates[i].date()), bars=i - entry[c],
                                           ret=df[c].iloc[i] / epx[c] - 1.0))
                cur = list(new)
                entry = {c: i for c in cur}
                epx = {c: df[c].iloc[i] for c in cur}
    if cur:
        c = cur[0]
        if (n - 1 - entry[c]) <= rule.FEE_SHORT_DAYS:
            eq[-1] *= 1 - rule.FEE_SHORT
        if epx[c] > 0:
            trades.append(dict(code=c, entry_date=str(dates[entry[c]].date()),
                               exit_date=str(dates[n - 1].date()), bars=n - 1 - entry[c],
                               ret=df[c].iloc[n - 1] / epx[c] - 1.0))
    return pd.Series(eq, index=dates), trades


def run_benchmark(df):
    dates, n = df.index, len(df)
    first = {c: df[c].first_valid_index() for c in df.columns}
    rebal = list(range(rule.LOOKBACK, n, rule.REBAL))
    eq, wts = [1.0], {}
    for i in range(1, n):
        r = 0.0
        if wts:
            for c, w in wts.items():
                p0, p1 = df[c].iloc[i - 1], df[c].iloc[i]
                if p0 > 0 and not (np.isnan(p0) or np.isnan(p1)):
                    r += w * (p1 / p0 - 1.0)
        eq.append(eq[-1] * (1 + r))
        if i in rebal:
            cutoff = dates[i - rule.LOOKBACK]
            elig = [c for c in df.columns if first[c] <= cutoff]
            wts = {c: 1.0 / len(elig) for c in elig} if elig else {}
    s = pd.Series(eq, index=dates)
    # 基准"伪单笔": 每 21 交易日等权调仓周期收益(与策略单笔对齐的可比胜率)
    pts = rebal + [n - 1]
    bm_trades = []
    for a, b in zip(pts[:-1], pts[1:]):
        if a < b:
            bm_trades.append(dict(code="EW-BENCH", entry_date=str(dates[a].date()),
                                  exit_date=str(dates[b].date()), bars=b - a,
                                  ret=s.iloc[b] / s.iloc[a] - 1.0))
    return s, bm_trades


def metrics(eq):
    s = eq / eq.iloc[0]
    ret = s.iloc[-1] - 1
    y = max((s.index[-1] - s.index[0]).days / 365.0, 1e-9)
    ann = (1 + ret) ** (1 / y) - 1 if ret > -1 else -1
    dd = (s / s.cummax() - 1).min()
    r = eq.pct_change().dropna()
    return dict(ann=ann, dd=dd, vol=r.std() * np.sqrt(252),
                shp=r.mean() / r.std() * np.sqrt(252), ret=ret)


def main():
    df = rule.load_wide()
    if len(df) == 0:
        sys.exit("缺少十年库数据: 请先 python strategies/momentum_rotation/fetch.py <codes> 2015-01-01"
                 " <仓库根>/data/_long_klines.json")
    s0 = df.index[rule.MIN_HIST]
    strat, trades = run_strategy(df)
    bm, bm_trades = run_benchmark(df)
    strat, bm = (strat / strat.iloc[0])[strat.index >= s0], (bm / bm.iloc[0])[bm.index >= s0]

    def seg(e, end=None, lo=None):
        if lo is not None:
            e = e[e.index >= pd.Timestamp(lo)]
        if end is not None:
            e = e[e.index <= pd.Timestamp(end)]
        return metrics(e) if len(e) > 60 else None

    rows = [("十年全期(2015.07起)", None, None),
            ("2016~2021H1", "2021-06-30", None),
            ("2021H2~2026", None, "2021-07-01")]
    L = []
    L.append("# A 全球相对动量轮动 · 官方十年回测(唯一保留版本)")
    L.append(f"> 池 {df.shape[1]} 只 · 120日动量排名首个≥MA20 单仓 · 21交易日再平衡 · xueqiu 十年库({df.index.min().date()}~{df.index.max().date()})")
    L.append("")
    L.append("| 段 | 年化 | 最大回撤 | 波动 | 夏普 | 总收益 | 基准年化 | 基准最大回撤 |")
    L.append("|---|---|---|---|---|---|---|---|")
    out = {"generated_at": datetime.now().strftime("%Y-%m-%d %H:%M")}
    for name, end, lo in rows:
        ms, mb = seg(strat, end, lo), seg(bm, end, lo)
        if not ms:
            continue
        L.append(f"| {name} | {pct(ms['ann'])} | {pct(ms['dd'])} | {pct(ms['vol'])} | {ms['shp']:.2f}"
                 f" | {pct(ms['ret'])} | {pct(mb['ann']) if mb else '—'} | {pct(mb['dd']) if mb else '—'} |")
        out[name] = ms
    ts = bt_stats.trade_stats(trades)
    bts = bt_stats.trade_stats(bm_trades)
    L += bt_stats.section_rows(
        [("策略(单笔持仓周期)", ts), ("基准(等权调仓周期)", bts)],
        title="单笔/周期统计对照（全期）",
        note="策略单笔=entry收盘→exit收盘的持仓期收益(未含佣金/滑点, 已含≤7交易日1.5%赎回费从净值端扣除)；"
             "基准无开平仓交易，以其每21交易日等权调仓周期收益作为同口径可比胜率。"
             "数据为 xueqiu 前复权；模拟结果，非投资建议。")
    txt = "\n".join(L)
    open(os.path.join(os.path.dirname(os.path.abspath(__file__)), "backtest", "report.md"),
         "w", encoding="utf-8").write(txt)
    print(txt)
    out["trades_stats"] = ts
    out["trades"] = trades
    out["bench_stats"] = bts
    out["bench_trades"] = bm_trades
    json.dump(out, open(os.path.join(os.path.dirname(os.path.abspath(__file__)), "backtest", "_mom_out.json"), "w", encoding="utf-8"),
              ensure_ascii=False, indent=2)

    fig, ax = plt.subplots(figsize=(12, 5))
    ax.plot(strat.index, strat.values, label="A 全球相对动量", lw=1.1)
    ax.plot(bm.index, bm.values, label="等权基准", alpha=0.7)
    ax.legend()
    ax.set_title("A 全球相对动量 vs 等权基准 (净值)")
    ax.grid(alpha=0.3)
    fig.tight_layout()
    fig.savefig(os.path.join(os.path.dirname(os.path.abspath(__file__)), "backtest", "_mom_nav.png"),
                dpi=140, facecolor="white")
    print("\n图: _mom_nav.png")


if __name__ == "__main__":
    main()
