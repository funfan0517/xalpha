# -*- coding: utf-8 -*-
r"""中证红利ETF 策略 · 回测引擎 —— 回答「§4.2 三个指标哪个更好」。

对同一标的(中证红利全收益 H00922), 把每个指标**单独**当择时开关, 再加合成规则,
在完全相同的口径下比较。无前视: 第 t 日收盘算信号 -> 第 t+1 日持仓。

口径:
  收益序列   H00922 全收益指数(含分红再投) —— 红利策略必须用全收益
  持仓映射   binary: 买入/持有=满仓, 卖出=空仓(主判据)
             graded: 买入=满仓, 持有=半仓, 卖出=空仓(稳健性对照)
  成本       单边 FEE(0.03%), 按 |Δ仓位| 逐日计提
  样本       SAMPLE_FROM(2016-09-01) 起

输出:
  backtest/_dividend_bt.jsonl  每个变体一行(schema 对齐 bt_stats/flow_select)
  backtest/_dividend_bt.json   结构化结果(供 _reportbt.py 复用)

用法: python strategies/dividend/backtest.py
"""
import io
import json
import os
import sys

import numpy as np
import pandas as pd

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8")
_DIR = os.path.dirname(os.path.abspath(__file__))
if _DIR not in sys.path:
    sys.path.insert(0, _DIR)
_ROOT = os.path.dirname(os.path.dirname(_DIR))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

import data as dt          # noqa: E402
import rule                # noqa: E402
from pipeline import bt_stats  # noqa: E402

VARIANTS = ("ratio", "pe_pct", "dy", "combo")   # 三个单指标 + 合成
FWD = 20                                        # 分区前瞻收益诊断窗口(交易日)


# ----------------------------------------------------------------------
# 模拟
# ----------------------------------------------------------------------
def simulate(price, target, fee):
    """price, target(当日信号决定的目标仓位) -> pos_held / nav / ret。

    pos_held[t] = target[t-1]（当日实际持仓, 次日生效, 无前视）
    ret[t]      = pos_held[t] * price 收益 − fee * |Δpos_held|
    """
    price = np.asarray(price, dtype=float)
    target = np.asarray(target, dtype=float)
    n = len(price)
    r = np.zeros(n)
    r[1:] = price[1:] / price[:-1] - 1.0
    pos = np.zeros(n)
    pos[1:] = target[:-1]
    dpos = np.zeros(n)
    dpos[1:] = np.abs(pos[1:] - pos[:-1])
    gross = pos * r
    net = gross - fee * dpos
    nav = np.cumprod(1.0 + net)
    return pos, nav, r, net, dpos


def trades_from_pos(pos, price, fee):
    """持仓段 -> 单笔交易列表(entry/exit 均按收盘计, 双边各扣 fee)。"""
    trades = []
    n = len(pos)
    i = 1
    while i < n:
        if pos[i] > 0 and pos[i - 1] <= 0:
            start = i
            while i < n and pos[i] > 0:
                i += 1
            end = i - 1
            entry_px = price[start - 1]
            exit_px = price[end]
            ret = exit_px / entry_px * (1 - fee) * (1 - fee) - 1.0
            trades.append(dict(entry_i=start, exit_i=end, bars=int(end - start + 1), ret=float(ret)))
        else:
            i += 1
    return trades


def _perf(nav, years):
    tot = float(nav[-1] / nav[0] - 1.0)
    ann = (1 + tot) ** (1 / years) - 1 if tot > -1 else -1.0
    mdd = float((nav / np.maximum.accumulate(nav) - 1).min())
    return tot, ann, mdd


def build_target(df, name, weights):
    """当日信号 -> 目标仓位序列。"""
    n = len(df)
    if name == "buy_hold":
        return np.ones(n)
    target = np.zeros(n)
    for i, row in enumerate(df[list(rule.IND_KEYS)].to_dict("records")):
        if name == "combo":
            z = rule.compose_zone({k: rule.ZONE_FN[k](row[k]) for k in rule.IND_KEYS})
        else:
            z = rule.ZONE_FN[name](row[name])
        target[i] = weights.get(z, 0.0)
    return target


def run_variant(df, name, mode):
    """单个变体 -> 结果 dict。"""
    price = df["tr"].to_numpy(float)
    target = build_target(df, name, rule.MODES[mode])
    pos, nav, r, net, dpos = simulate(price, target, rule.FEE)
    years = max((df.index[-1] - df.index[0]).days / 365.25, 1e-9)
    tot, ann, mdd = _perf(nav, years)
    vol = float(net[1:].std() * np.sqrt(252))
    sharpe = float(net[1:].mean() / net[1:].std() * np.sqrt(252)) if net[1:].std() > 0 else 0.0
    calmar = ann / abs(mdd) if mdd < 0 else float("inf")
    pos_ratio = float(np.mean(pos))
    hold_m, flat_m = pos[1:] > 0, pos[1:] == 0
    hold = float(np.mean(r[1:][hold_m])) if hold_m.any() else 0.0
    flat = float(np.mean(r[1:][flat_m])) if flat_m.any() else 0.0
    trades = trades_from_pos(pos, price, rule.FEE)
    ts = bt_stats.trade_stats(trades)
    return dict(variant=name, mode=mode,
                start=str(df.index[0].date()), end=str(df.index[-1].date()),
                years=round(years, 2),
                cum=round(tot, 4), ann=round(ann, 4), vol=round(vol, 4),
                mdd=round(mdd, 4), sharpe=round(sharpe, 2), calmar=round(calmar, 2),
                pos_ratio=round(pos_ratio, 3), timing_edge=round(hold - flat, 5),
                turnover=round(float(np.sum(dpos) / years), 2),
                trades=ts["n"], t_stats=ts)


def _zones(df, name):
    """每行 -> 分区列表(combo 走合成规则)。"""
    if name == "combo":
        return [rule.compose_zone({k: rule.ZONE_FN[k](row[k]) for k in rule.IND_KEYS})
                for row in df[list(rule.IND_KEYS)].to_dict("records")]
    return [rule.ZONE_FN[name](v) for v in df[name]]


def zone_forward(df, name):
    """诊断: 各分区的未来 FWD 日收益均值(%) —— 好的指标应让 buy区 > hold区 > sell区。"""
    price = df["tr"].to_numpy(float)
    fwd = np.full(len(df), np.nan)
    fwd[:-FWD] = price[FWD:] / price[:-FWD] - 1.0
    zones = np.array(_zones(df, name), dtype=object)
    out = {}
    for zone in ("buy", "hold", "sell"):
        mask = (zones == zone) & ~np.isnan(fwd)
        vals = fwd[mask]
        out[zone] = dict(n=int(len(vals)), fwd=round(float(np.mean(vals)) * 100, 2) if len(vals) else None)
    return out


def main():
    df = dt.load_series()
    df = df.loc[df.index >= pd.Timestamp(rule.SAMPLE_FROM)]
    df = df.dropna(subset=["dy", "ratio", "pe_pct"])
    rows, fwd = [], {}
    for mode in rule.MODES:
        for name in ("buy_hold",) + VARIANTS:
            rows.append(run_variant(df, name, mode))
    for name in VARIANTS:
        fwd[name] = zone_forward(df, name)

    os.makedirs(rule.BACKTEST_DIR, exist_ok=True)
    with open(rule.OUT_BT_JSONL, "w", encoding="utf-8") as fh:
        for r in rows:
            fh.write(json.dumps({"code": rule.ASSET["inner_code"], **r}, ensure_ascii=False) + "\n")
    json.dump({"sample": [str(df.index[0].date()), str(df.index[-1].date())],
               "rows": rows, "forward_by_zone": fwd},
              open(os.path.join(rule.BACKTEST_DIR, "_dividend_bt.json"), "w", encoding="utf-8"),
              ensure_ascii=False, indent=2)

    print(f"样本 {df.index[0].date()} ~ {df.index[-1].date()} ({len(df)} 交易日)")
    for mode in rule.MODES:
        print(f"\n=== 模式 {mode} ===")
        print(f"{'变体':<10}{'累计':>9}{'年化':>8}{'波动':>8}{'回撤':>9}{'夏普':>7}{'Calmar':>8}{'持仓':>7}{'择时bp':>8}{'年换手':>8}{'笔数':>6}")
        for r in rows:
            if r["mode"] != mode:
                continue
            print(f"{r['variant']:<10}{r['cum']*100:>8.0f}%{r['ann']*100:>7.1f}%"
                  f"{r['vol']*100:>7.1f}%{r['mdd']*100:>8.1f}%{r['sharpe']:>7.2f}{r['calmar']:>8.2f}"
                  f"{r['pos_ratio']*100:>6.0f}%{r['timing_edge']*1e4:>8.1f}{r['turnover']:>8.1f}{r['trades']:>6}")
    print("\n=== 各分区未来20日收益均值(%) ===")
    for name in VARIANTS:
        f = fwd[name]
        label = rule.IND_NAMES.get(name, "三指标合成")
        print(f"{label:<18} 买入={f['buy']['fwd']} (n={f['buy']['n']})  "
              f"持有={f['hold']['fwd']} (n={f['hold']['n']})  卖出={f['sell']['fwd']} (n={f['sell']['n']})")
    return 0


if __name__ == "__main__":
    sys.exit(main())
