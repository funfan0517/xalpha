#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""利差择时回测（规则 v1.0）

只验证规则**最关键、也最没被验证过**的那一半：**时间维度的分位择时**。
（横截面「买股息率最高的那只」已由 `../research/backtest_dims.py` 验证过：股债收益比 +49.1bp。）

设定
----
* 标的：中证红利（`000922` 的股息率 + 中债锚 → 利差；持有收益用全收益指数 `H00922`）。
* 频率：月末信号，**下月生效**（`pos.shift(1)`，无前视）。
* 规则：分位 ≥ buy → 满仓；< sell → 空仓；带内**不动**（不是每天按分位调仓）。
  保险丝：月末 40 日超额 > +10% → 当期仓位减半。
* 成本：每次换手单边 0.03%（场外 C 类，未计短期赎回费）。
* 分位：扩张窗口，最少 36 个月、最多 120 个月，**排除当前月自身**。

局限（写在报告里，不隐藏）
--------------------------
* 单标的择时，不代表全池「买分位最高那只」的轮动效果；
* 分位需 36 个月预热，绩效只从**第一个有效分位**起算（否则预热期被动满仓会把
  2015 股灾算进来、锁死最大回撤），实际样本约 8 年、月度频率；
* 股息率历史对 `000922` 是真实反推序列，其余指数为代理（本回测只用 000922）。

用法::

    python strategies/dividend/v1_spread/backtest.py
"""

from __future__ import annotations

import json
import os
import sys

import numpy as np
import pandas as pd

_DIR = os.path.dirname(os.path.abspath(__file__))
_STRAT = os.path.dirname(_DIR)
_ROOT = os.path.dirname(os.path.dirname(_STRAT))
sys.path.insert(0, _STRAT)
sys.path.insert(0, _DIR)
import pool  # noqa: E402  pylint: disable=wrong-import-position
import yielddata  # noqa: E402  pylint: disable=wrong-import-position

OUT_MD = os.path.join(_DIR, "backtest", "_v1_report.md")
OUT_JSONL = os.path.join(_DIR, "backtest", "_v1_bt.jsonl")

COST = 0.0003          # 单边 0.03%
FUSE = 10.0            # 40 日超额阈值(%)
MIN_N = 36             # 分位最少样本（月）
MAX_N = 120            # 分位窗口上限（月）
BASE_BUY, BASE_SELL = 0.70, 0.20


# ------------------------------------------------------------------ 数据
def load_data():
    """-> (月度收益, 利差, 分位, 40日超额)。"""
    yh = yielddata.load()
    dy = yielddata.series(yh, "000922")

    raw = pd.read_csv(pool.BOND_10Y_CACHE)
    y10 = pd.Series(pd.to_numeric(raw[raw.columns[-1]], errors="coerce").to_numpy(),
                    index=pd.to_datetime(raw.iloc[:, 0])).dropna().sort_index()

    tr = yielddata.csi_series("H00922")
    px = yielddata.csi_series("000922")
    mkt = yielddata.csi_series("000300")

    month_end = lambda s: s.resample("ME").last().dropna()  # noqa: E731
    ret = month_end(tr).pct_change().dropna()
    fuse = ((px.pct_change(40) - mkt.pct_change(40)) * 100).dropna()
    fuse_m = month_end(fuse)

    df = pd.concat([dy.rename("dy"), y10.rename("y10"), ret.rename("ret")], axis=1,
                   join="inner").dropna()
    df["spread"] = df["dy"] - df["y10"]
    df["fuse"] = fuse_m.reindex(df.index, method="ffill")
    df["pct"] = rolling_pct(df["spread"])
    return df


def rolling_pct(s, min_n=MIN_N, max_n=MAX_N):
    """扩张窗口分位（排除当前点自身）。"""
    vals = s.to_numpy(dtype=float)
    out = np.full(len(vals), np.nan)
    for i in range(len(vals)):
        lo = max(0, i - max_n + 1)
        win = vals[lo:i]
        if len(win) >= min_n:
            out[i] = float((win < vals[i]).mean())
    return pd.Series(out, index=s.index)


# ------------------------------------------------------------------ 回测
def run(df, buy=BASE_BUY, sell=BASE_SELL, use_fuse=True):
    """带内不动 + 阈值切换 + 保险丝。返回逐月明细。"""
    pos, cur = [], 1.0                     # 期初满仓（与基准同起点）
    for pct, fuse in zip(df["pct"], df["fuse"]):
        if not np.isnan(pct):
            if pct >= buy:
                cur = 1.0
            elif pct < sell:
                cur = 0.0
        base = cur
        if use_fuse and not np.isnan(fuse) and fuse > FUSE:
            base *= 0.5
        pos.append(base)
    out = df.copy()
    out["pos"] = pos
    out["pos_lag"] = out["pos"].shift(1).fillna(1.0)      # 下月生效
    out["turnover"] = (out["pos_lag"] - out["pos_lag"].shift(1).fillna(1.0)).abs()
    out["strat"] = out["pos_lag"] * out["ret"] - out["turnover"] * COST
    return out


def stats(r):
    nav = (1 + r).cumprod()
    yrs = len(r) / 12
    cagr = nav.iloc[-1] ** (1 / yrs) - 1
    dd = float((nav / nav.cummax() - 1).min())
    vol = r.std() * np.sqrt(12)
    return {
        "cagr": cagr, "dd": dd, "calmar": cagr / abs(dd) if dd else float("nan"),
        "vol": vol, "sharpe": (r.mean() * 12) / vol if vol else float("nan"),
        "total": nav.iloc[-1] - 1,
    }


def main():
    full = load_data()
    # 分位需要 36 个月预热，预热期不可能产生信号 → 绩效只从第一个有效分位起算，
    # 否则 2015 股灾期间被动满仓会把最大回撤锁死，让择时看起来毫无作用。
    start = full["pct"].first_valid_index()
    df = full.loc[start:]
    os.makedirs(os.path.dirname(OUT_MD), exist_ok=True)

    main_bt = run(df)
    bench = df["ret"]

    rows = []
    for name, series in (("利差择时(70/20)+保险丝", main_bt["strat"]),
                         ("利差择时(70/20) 无保险丝", run(df, use_fuse=False)["strat"]),
                         ("买入持有(中证红利全收益)", bench)):
        s = stats(series)
        s["name"] = name
        rows.append(s)

    # 阈值敏感性
    sens = []
    for buy, sell in ((0.60, 0.20), (0.70, 0.20), (0.70, 0.30), (0.80, 0.20), (0.70, 0.10)):
        r = run(df, buy=buy, sell=sell)["strat"]
        s = stats(r)
        s.update({"buy": buy, "sell": sell})
        sens.append(s)

    switches = int((main_bt["pos_lag"].diff().abs() > 1e-9).sum())
    avg_pos = float(main_bt["pos_lag"].mean())

    # ---- 诊断一：分位到底有没有在给信号？
    # 若分位长期贴顶，这条规则在时间维度上就退化成「一直满仓」，绩效自然约等于买入持有。
    n_hi = int((df["pct"] >= BASE_BUY).sum())
    n_lo = int((df["pct"] < BASE_SELL).sum())
    med_pct = float(df["pct"].median())
    # 换成固定滚动窗口（而非扩张窗口）复核，排除「是窗口设定造成的假象」。
    alts = []
    for win, mn in ((60, 36), (120, 84)):
        d = full.copy()
        d["pct"] = rolling_pct(d["spread"], mn, win)
        d = d.loc[d["pct"].first_valid_index():]
        rr = run(d)
        alts.append({"name": f"固定滚动 {win} 月（≥{mn}）", "start": d.index[0], "n": len(d),
                     "hi": int((d["pct"] >= BASE_BUY).sum()),
                     "avg_pos": float(rr["pos_lag"].mean()),
                     "calmar": stats(rr["strat"])["calmar"]})

    # ---- 诊断二：保险丝贡献多少
    fuse_months = [ts.strftime("%Y-%m") for ts in df.index[df["fuse"] > FUSE]]
    cagr_fuse = rows[0]["cagr"] * 100
    cagr_nofuse = rows[1]["cagr"] * 100

    # ---- 诊断三：最大回撤发生在哪、当时仓位多少（判断择时有没有覆盖到风险）
    nav = (1 + bench).cumprod()
    dd_ser = nav / nav.cummax() - 1
    trough = dd_ser.idxmin()
    peak = nav.loc[:trough].idxmax()
    dd_span = len(nav.loc[peak:trough])
    dd_pos = sorted({round(float(v), 2) for v in main_bt.loc[peak:trough, "pos_lag"]})
    dd_pct = main_bt.loc[peak:trough, "pct"]

    # ---- 诊断四：第一次离场到回满仓这一整段（whipsaw 成本）
    out_m = (main_bt["pos"].to_numpy() < 0.999)
    i0 = int(out_m.argmax()) if out_m.any() else 0
    i1 = i0
    while i1 < len(main_bt) and out_m[i1]:
        i1 += 1
    # 区间取到「重新满仓后的第一个月」，这样回场立刻挨打的那一下也计入 whipsaw 成本
    j = min(i1 + 1, len(main_bt) - 1)
    seg = main_bt.iloc[i0:min(i1 + 2, len(main_bt))]
    seg_strat = float((1 + seg["strat"]).prod() - 1) * 100
    seg_bench = float((1 + seg["ret"]).prod() - 1) * 100
    flat = main_bt.iloc[i0 + 1:i1 + 1]          # 仓位真正为 0 的月份（清仓当月仍持有）
    flat_bench = float((1 + flat["ret"]).prod() - 1) * 100
    reentry = main_bt.iloc[j]                   # 回场后第一个满仓月
    reentry2 = main_bt.iloc[min(j + 1, len(main_bt) - 1)]

    L = ["# 利差择时回测（规则 v1.0）", "",
         f"> 生成 {pd.Timestamp.now():%Y-%m-%d %H:%M} · 样本 **{df.index[0]:%Y-%m} ~ "
         f"{df.index[-1]:%Y-%m}**（{len(df)} 个月）· 标的 中证红利(000922)，"
         "持有收益用**全收益指数 H00922**", "",
         "> 样本起点由分位预热决定：分位至少需要 36 个月历史，之前的月份不可能有信号，"
         "所以绩效一律从**第一个有效分位**起算（否则预热期被动满仓会把 2015 股灾算进来、锁死回撤）。", "",
         "> 只验证「时间维度的分位择时」。「横截面买股息率最高那只」不在本回测范围"
         "（见 `../research/backtest_dims.py`：股债收益比单指标 +49.1bp）。", ""]

    L += ["## 一、主参数（买入 ≥70% / 卖出 <20%）", "",
          "| 方案 | 年化 | 最大回撤 | Calmar | 波动 | 夏普 | 累计 |",
          "|---|---:|---:|---:|---:|---:|---:|"]
    for s in rows:
        L.append(f"| {s['name']} | {s['cagr'] * 100:.1f}% | {s['dd'] * 100:.1f}% | "
                 f"{s['calmar']:.2f} | {s['vol'] * 100:.1f}% | {s['sharpe']:.2f} | "
                 f"{s['total'] * 100:+.0f}% |")
    L += ["", f"- 调仓次数 {switches} 次（{len(df)} 个月）、平均仓位 {avg_pos * 100:.0f}%；"
              f"成本按单边 {COST * 100:.2f}%/次计。", ""]

    L += ["## 二、信号密度：这条规则到底有没有在择时", "",
          "| 分位口径 | 起始 | 月数 | 分位≥70% 月数 | 平均仓位 | Calmar |",
          "|---|---|---:|---:|---:|---:|",
          f"| 扩张窗口（本回测：≥36 月起，上限 120 月） | {df.index[0]:%Y-%m} | {len(df)} | "
          f"{n_hi} | {avg_pos * 100:.0f}% | {rows[0]['calmar']:.2f} |"]
    for a in alts:
        L.append(f"| {a['name']} | {a['start']:%Y-%m} | {a['n']} | {a['hi']} | "
                 f"{a['avg_pos'] * 100:.0f}% | {a['calmar']:.2f} |")
    L += ["", f"- 分位中位数 **{med_pct * 100:.0f}%**：{n_hi}/{len(df)} 个月 ≥70%，"
              f"仅 {n_lo} 个月 <20%。换固定窗口也压不下来（见上表），所以不是窗口设定的假象，"
              "而是**利差本身是趋势序列** —— 2018 年后中债 10Y 从 3.5% 一路下行到 1.7%，"
              "利息端塌陷把利差长期顶在历史高位。",
          "- 后果：70/20 这条规则在这段样本里几乎不会发出卖出信号，时间维度上等同「一直满仓」。", ""]

    L += ["## 三、阈值敏感性（比单点结果更可信）", "",
          "| 买入阈值 | 卖出阈值 | 年化 | 最大回撤 | Calmar | 夏普 |",
          "|---:|---:|---:|---:|---:|---:|"]
    for s in sens:
        L.append(f"| ≥{s['buy'] * 100:.0f}% | <{s['sell'] * 100:.0f}% | {s['cagr'] * 100:.1f}% | "
                 f"{s['dd'] * 100:.1f}% | {s['calmar']:.2f} | {s['sharpe']:.2f} |")
    L.append("")

    best = max(sens, key=lambda x: x["calmar"])
    bh = rows[-1]
    L += ["## 四、读法（本样本的真实结论）", "",
          f"1. **时间维度的择时基本没发生。** 平均仓位 {avg_pos * 100:.0f}%，{n_hi}/{len(df)} 个月分位"
          f" ≥70% 直接满仓。策略与买入持有的收益差（{rows[0]['cagr'] * 100:.1f}% vs "
          f"{bh['cagr'] * 100:.1f}%）不是来自持续高低切换，而是来自下面那一次离场。",
          f"2. **唯一一次真实信号是负贡献的 whipsaw。** {seg.index[0]:%Y-%m} 分位 "
          f"{seg['pct'].iloc[0] * 100:.0f}% 跌破 20% → 清仓；随后空仓 {len(flat)} 个月"
          f"（{flat.index[0]:%Y-%m}~{flat.index[-1]:%Y-%m}），这段基准 **{flat_bench:+.1f}%**、"
          f"策略约 0（清仓当月仍持有，起步就 {seg['strat'].iloc[0] * 100:+.1f}%）。"
          f"{main_bt.index[min(i1, len(main_bt) - 1)]:%Y-%m} 分位重回 70% 以上才进场、"
          f"{reentry.name:%Y-%m} 生效，回场首月 {reentry['ret'] * 100:+.1f}%、次月 "
          f"{reentry2['ret'] * 100:+.1f}%。整段（清仓月~回场生效月）策略 {seg_strat:+.1f}%、"
          f"买入持有 {seg_bench:+.1f}% —— **离场容易回场难**，这是「只认 70% 才买回」的结构性代价，不是运气差。",
          f"3. **保险丝是唯一稳定为正的部件。** 触发 {len(fuse_months)} 次"
          f"（{', '.join(fuse_months)}），年化 {cagr_fuse:.1f}% vs 不带保险丝 {cagr_nofuse:.1f}%"
          f"（+{cagr_fuse - cagr_nofuse:.1f}pp/年）。此前报告写「影响在噪声内」是错的，更正于此。",
          f"4. **回撤维度上这条规则完全没起作用。** 最大回撤 {bh['dd'] * 100:.1f}% 落在 "
          f"{peak:%Y-%m} ~ {trough:%Y-%m}（{dd_span} 个月），当时仓位 {dd_pos}，分位区间 "
          f"{dd_pct.min() * 100:.0f}–{dd_pct.max() * 100:.0f}% 始终在带内 → 无任何卖出触发。"
          "三个方案最大回撤完全相同，就是最直接的证据。",
          "- 阈值敏感性表里 Calmar 最高的 70/10 恰好是**交易最少**的一档（卖出线压到 10% 等于"
          "几乎不离场）—— 与「择时无效」同向，不能解读成「10% 更优」；在同一段样本上挑最优就是过拟合。",
          f"- 样本 {len(df)} 个月（{df.index[0]:%Y-%m}~{df.index[-1]:%Y-%m}），受分位 36 个月预热限制，"
          "起不到 2015 那轮的作用；且 2018 年后中债单边下行对利差是系统性顺风，"
          "结论不能外推到利率上行周期（那时利差分位会被压低，规则反而会频繁触发）。", ""]

    with open(OUT_MD, "w", encoding="utf-8") as fh:
        fh.write("\n".join(L))

    recs = []
    for ts, r in main_bt.iterrows():
        recs.append({"date": ts.strftime("%Y-%m-%d"), "dy": round(r["dy"], 4),
                     "y10": round(r["y10"], 4), "spread": round(r["spread"], 4),
                     "pct": None if np.isnan(r["pct"]) else round(r["pct"], 4),
                     "fuse": None if np.isnan(r["fuse"]) else round(r["fuse"], 2),
                     "pos": round(r["pos_lag"], 3), "ret": round(r["ret"], 6),
                     "strat": round(r["strat"], 6)})
    with open(OUT_JSONL, "w", encoding="utf-8") as fh:
        for r in recs:
            fh.write(json.dumps(r, ensure_ascii=False) + "\n")

    print(f"[v1bt] 样本 {df.index[0]:%Y-%m}~{df.index[-1]:%Y-%m} ({len(df)} 月)")
    for s in rows:
        print(f"[v1bt] {s['name']:26s} 年化 {s['cagr'] * 100:5.1f}%  回撤 {s['dd'] * 100:6.1f}%  "
              f"Calmar {s['calmar']:5.2f}")
    print(f"[v1bt] 产物 -> {os.path.relpath(OUT_MD, _DIR)}")


if __name__ == "__main__":
    main()
