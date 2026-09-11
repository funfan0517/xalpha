# -*- coding: utf-8 -*-
"""全球相对动量轮动(六类资产) · 官方十年回测(唯一保留版本)
标的: rule.POOL 六类资产场内代理(场外主仓执行口径见 README)
引擎: 10交易日步进 · 每期买动量前2名中站上MA20者等权 · 当日收盘算信号并换仓 · 当日收益归旧仓(无前视)
成本: 卖出 ≤7交易日 1.5% / 7日外 0% (周期≥10交易日≈0) · 数据 xueqiu 十年库
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


def run_strategy(df, rebal=None, topn=None):
    """官方引擎: 每 rebal 交易日调仓, 买「动量排名前 topn 且 close≥MA20」的标的等权持有。

    rebal / topn 缺省取 rule.REBAL / rule.HOLD_N; 传 (21, 1) 即旧「单仓·月频」口径
    (供 research 脚本的自检回归)。名单不变则不动, 合格标的不足 topn 只按实际只数持,
    一只都不合格 -> 空仓现金。当日收益归旧仓、收盘后换仓(无前视), 卖出 ≤7 交易日扣 1.5%。
    """
    rebal = rule.REBAL if rebal is None else rebal
    topn = rule.HOLD_N if topn is None else topn
    dates, n = df.index, len(df)
    ma_s = df.rolling(rule.MA).mean()
    first = {c: df[c].first_valid_index() for c in df.columns}
    dec = {}
    for t in range(rule.MIN_HIST, n, rebal):
        cutoff = dates[t - rule.MIN_HIST]
        elig = [c for c in df.columns if first[c] <= cutoff]
        mom = {}
        for c in elig:
            p0, p1 = df[c].iloc[t - rule.LOOKBACK], df[c].iloc[t]
            if p0 > 0 and not (np.isnan(p0) or np.isnan(p1)):
                mom[c] = p1 / p0 - 1.0
        ranked = sorted(mom, key=mom.get, reverse=True)
        pick = []
        for c in ranked:
            if not np.isnan(ma_s[c].iloc[t]) and df[c].iloc[t] >= ma_s[c].iloc[t]:
                pick.append(c)
                if len(pick) >= topn:
                    break
        dec[t] = pick
    eq, cur, w, entry, epx = [1.0], [], {}, {}, {}
    trades = []
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
                w = {c: v / tot for c, v in num.items()}      # 权重随价格漂移
                r = tot - 1.0
        eq.append(eq[-1] * (1 + r))
        if i in dec:
            new = dec[i]
            if set(new) != set(cur):                         # 名单(集合)不变则不动
                for c in cur:                                # 只卖被剔除的标的
                    if c in new:
                        continue
                    if (i - entry[c]) <= rule.FEE_SHORT_DAYS:
                        eq[-1] *= 1 - rule.FEE_SHORT * w[c]
                    if epx[c] > 0:
                        trades.append(dict(code=c, entry_date=str(dates[entry[c]].date()),
                                           exit_date=str(dates[i].date()), bars=i - entry[c],
                                           ret=df[c].iloc[i] / epx[c] - 1.0))
                entry = {c: (entry[c] if c in cur else i) for c in new}
                epx = {c: (epx[c] if c in cur else df[c].iloc[i]) for c in new}
                cur = list(new)
                w = {c: 1.0 / len(cur) for c in cur} if cur else {}
    if cur:
        for c in cur:                                        # 收尾清仓
            if (n - 1 - entry[c]) <= rule.FEE_SHORT_DAYS:
                eq[-1] *= 1 - rule.FEE_SHORT * w[c]
            if epx[c] > 0:
                trades.append(dict(code=c, entry_date=str(dates[entry[c]].date()),
                                   exit_date=str(dates[n - 1].date()), bars=n - 1 - entry[c],
                                   ret=df[c].iloc[n - 1] / epx[c] - 1.0))
    return pd.Series(eq, index=dates), trades


def run_benchmark(df, step=None):
    step = rule.REBAL if step is None else step
    dates, n = df.index, len(df)
    first = {c: df[c].first_valid_index() for c in df.columns}
    rebal = list(range(rule.LOOKBACK, n, step))
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
    # 基准"伪单笔": 每 step 交易日等权调仓周期收益(与策略单笔对齐的可比胜率)
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
    L.append("# 全球相对动量轮动 · 六类资产 · 官方十年回测(唯一保留版本)")
    L.append(f"> 池 {df.shape[1]} 只 · {rule.LOOKBACK}日动量排名前{rule.HOLD_N}名中≥MA20者等权 · {rule.REBAL}交易日再平衡 · xueqiu 十年库({df.index.min().date()}~{df.index.max().date()})")
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
             f"基准无开平仓交易，以其每{rule.REBAL}交易日等权调仓周期收益作为同口径可比胜率。"
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
    ax.plot(strat.index, strat.values, label="六类资产动量轮动", lw=1.1)
    ax.plot(bm.index, bm.values, label="等权基准", alpha=0.7)
    ax.legend()
    ax.set_title("六类资产动量轮动 vs 等权基准 (净值)")
    ax.grid(alpha=0.3)
    fig.tight_layout()
    fig.savefig(os.path.join(os.path.dirname(os.path.abspath(__file__)), "backtest", "_mom_nav.png"),
                dpi=140, facecolor="white")
    print("\n图: _mom_nav.png")


if __name__ == "__main__":
    main()
