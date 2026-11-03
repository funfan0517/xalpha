# -*- coding: utf-8 -*-
"""V4 专项：趋势过滤到底在做什么 —— 信号日快照 + 趋势判据方向/换源对照。

回答一个问题：图上买卖点看着别扭，是「趋势判据」的问题还是「触发阈值」的问题。

产出（stdout）：
1. 每个信号日 T 的：z、ERP 距其 MA20、沪深300 距 MA20/60/200、其后 60/250 日涨跌
2. 统计：买入日「价格已在 MA20/MA200 下方」占比、卖出日反之，及其后 250 日收益
3. 相关性：ERP 20 日动量 vs 价格 20 日动量（为什么两者永远打架）
4. 换趋势源（erp/price × 20/60/120/200）、反向趋势、去掉趋势的绩效与**信号日期**

⚠ `bt.evaluate()` 重算 state 时**不更新 event 列**，本脚本自己算 state/event，别复用它看时点。

模拟结果，非投资建议。
"""
import os
import sys

import pandas as pd

_HERE = os.path.dirname(os.path.abspath(__file__))
_PKG = os.path.dirname(_HERE)
sys.path.insert(0, _PKG)

try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:  # noqa: BLE001
    pass

import importlib.util  # noqa: E402

import erp  # noqa: E402

# ⚠ `strategies/vol_legs/` 下也有一个 backtest.py，直接 import 会撞名，
# 这里按文件路径精确加载本策略包内的那一份。
_spec = importlib.util.spec_from_file_location("_v4_backtest", os.path.join(_PKG, "backtest.py"))
bt = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(bt)


# ---------------------------------------------------------------- 状态机
def signal_state(df, band_hi=None, band_lo=None, trend_source="erp",
                 trend_window=20, invert=False, no_trend=False):
    """-> DataFrame(state, event)。invert=True 把趋势条件反过来（利差回落才买）。"""
    bh = float(erp.CFG["band_hi"] if band_hi is None else band_hi)
    bl = float(erp.CFG["band_lo"] if band_lo is None else band_lo)
    tw = int(trend_window)
    z = df["z"].to_numpy(dtype="float64")
    ready = df["ready"].to_numpy(dtype=bool)
    if no_trend:
        tup = tdn = [True] * len(df)
    else:
        tsrc = df[trend_source] if trend_source in df.columns else df["close"]
        tma = tsrc.rolling(tw, min_periods=tw).mean()
        up = (tsrc > tma).to_numpy(dtype=bool)
        dn = (tsrc < tma).to_numpy(dtype=bool)
        tup, tdn = (dn, up) if invert else (up, dn)
    state, states, events = 0, [], []
    for i in range(len(df)):
        ev = 0
        if not ready[i]:
            states.append(0)
            events.append(0)
            continue
        if state <= 0 and z[i] >= bh and tup[i]:
            state, ev = 1, 1
        elif state >= 0 and z[i] <= bl and tdn[i]:
            state, ev = -1, -1
        states.append(state)
        events.append(ev)
    return pd.DataFrame({"state": states, "event": events}, index=df.index)


def run_state(df, bond, t0, state):
    """给定 state 序列跑组合记账（与 backtest.evaluate 同口径）。"""
    d = bt.align(df, t0).copy()
    d["state"] = state.reindex(d.index).fillna(0).astype(int)
    tgt = erp.target_weight(d["state"])
    pe = d["tr"].astype("float64")
    pb = bond.reindex(pe.index).ffill()
    ok = pb.notna()
    pe, pb, tgt = pe[ok], pb[ok], tgt[ok]
    r = erp.run(pe, pb, tgt, period_end=erp.period_end_flags(pe.index, "M"),
                fee_eq=erp.CFG["fee_eq"], fee_bd=erp.CFG["fee_bd"])
    r["m"] = erp.perf(r["nav"])
    r["frame"] = d.loc[pe.index]
    return r


# ---------------------------------------------------------------- 报告
def snap(df, row, px_ma, erp_ma):
    i = df.index.get_loc(row.name)
    p = row["close"]
    d0 = row.name
    out = dict(
        date=row.name.date(),
        dir="买入" if row["event"] > 0 else "卖出",
        close=round(p, 0),
        erp=round(row["erp"], 2),
        erp_ma250=round(erp_ma[250].loc[d0], 2),
        z=round(row["z"], 2),
        erp_vs_ma20=round((row["erp"] / erp_ma[20].loc[d0] - 1) * 100, 2),
        px_vs_ma20=round((p / px_ma[20].loc[d0] - 1) * 100, 1),
        px_vs_ma60=round((p / px_ma[60].loc[d0] - 1) * 100, 1),
        px_vs_ma200=round((p / px_ma[200].loc[d0] - 1) * 100, 1),
    )
    tr = df["tr"]
    for h in (60, 250):
        j = min(i + h, len(tr) - 1)
        out[f"fwd{h}"] = round((tr.iloc[j] / tr.iloc[i] - 1) * 100, 1) if j > i else None
    return out


def percentile_state(df, win, hi=0.90, lo=0.10):
    """滚动 win 日分位（0~1）触发的状态机，无趋势。"""
    r = df["erp"].rolling(win, min_periods=win).apply(
        lambda x: float((x <= x[-1]).mean()), raw=True)
    p = r.to_numpy(dtype="float64")
    ready = r.notna().to_numpy()
    state, states, events = 0, [], []
    for i in range(len(df)):
        ev = 0
        if not ready[i]:
            states.append(0)
            events.append(0)
            continue
        if state <= 0 and p[i] >= hi:
            state, ev = 1, 1
        elif state >= 0 and p[i] <= lo:
            state, ev = -1, -1
        states.append(state)
        events.append(ev)
    return pd.DataFrame({"state": states, "event": events}, index=df.index)


def abs_gate_state(df, erp_hi=5.0, erp_lo=3.5):
    """现状 z 阈值 + 利差绝对水平闸门（买入要求 ERP≥erp_hi，卖出要求 ERP≤erp_lo）。"""
    z = df["z"].to_numpy(dtype="float64")
    e = df["erp"].to_numpy(dtype="float64")
    ready = df["ready"].to_numpy(dtype=bool)
    state, states, events = 0, [], []
    for i in range(len(df)):
        ev = 0
        if not ready[i]:
            states.append(0)
            events.append(0)
            continue
        if state <= 0 and z[i] >= 0.90 and e[i] >= erp_hi:
            state, ev = 1, 1
        elif state >= 0 and z[i] <= 0.10 and e[i] <= erp_lo:
            state, ev = -1, -1
        states.append(state)
        events.append(ev)
    return pd.DataFrame({"state": states, "event": events}, index=df.index)


def main():
    df = erp.erp_frame()
    t0 = bt.align(df).index[0]
    bond = bt.bond_series("synth")
    d = bt.align(df, t0)
    px = d["close"]
    # 均线在**全长** df 上算，d 只是切片（否则前 250 天是 NaN）
    _px = df["close"]
    _erp = df["erp"]
    px_ma = {w: _px.rolling(w, min_periods=w).mean() for w in (20, 60, 120, 200)}
    erp_ma = {w: _erp.rolling(w, min_periods=w).mean() for w in (20, 250)}

    print("=" * 104)
    print("1. 现状信号日快照（T 日收盘触发，T+1 成交）")
    print("=" * 104)
    base_sig = signal_state(d)
    t1 = pd.DataFrame([snap(d, r, px_ma, erp_ma) for _, r in d[base_sig["event"] != 0].iterrows()])
    print(t1.to_string(index=False))

    buy = t1[t1["dir"] == "买入"]
    sell = t1[t1["dir"] == "卖出"]
    print()
    print(f"买入 {len(buy)} 次：价格在 MA20 下方 {int((buy['px_vs_ma20'] < 0).sum())} 次"
          f"（{100 * (buy['px_vs_ma20'] < 0).mean():.0f}%），MA200 下方 {int((buy['px_vs_ma200'] < 0).sum())} 次"
          f"（{100 * (buy['px_vs_ma200'] < 0).mean():.0f}%）")
    print(f"卖出 {len(sell)} 次：价格在 MA20 上方 {int((sell['px_vs_ma20'] > 0).sum())} 次"
          f"（{100 * (sell['px_vs_ma20'] > 0).mean():.0f}%），MA200 上方 {int((sell['px_vs_ma200'] > 0).sum())} 次"
          f"（{100 * (sell['px_vs_ma200'] > 0).mean():.0f}%）")
    print(f"买入信号后 250 日平均 {buy['fwd250'].mean():+.1f}%（最差 {buy['fwd250'].min():+.1f}%）"
          f" / 卖出信号后 250 日平均 {sell['fwd250'].mean():+.1f}%（最好 {sell['fwd250'].max():+.1f}%）")

    print()
    print("=" * 104)
    print("2. 两个「趋势」的关系：ERP 20 日动量 vs 价格 20 日动量")
    print("=" * 104)
    erp_mom = d["erp"].pct_change(20)
    px_mom = px.pct_change(20)
    print(f"corr(ERP 20 日动量, 价格 20 日动量) = {erp_mom.corr(px_mom):+.3f}"
          f"   ← 负值 = 利差上行时价格在跌，「利差趋势」与「价格趋势」天生反向")
    print(f"「ERP 在 MA20 上方（利差上行）」的日子占比 {100 * df['trend_up'].mean():.1f}%")
    below200 = _px < px_ma[200]
    print(f"  其中价格同时在 MA200 下方 {100 * (df['trend_up'] & below200)[df['trend_up']].mean():.1f}%")

    print()
    print("=" * 104)
    print("3. 换趋势判据 / 反向趋势 / 去掉趋势：绩效（同一样本、同一债券腿、含费）")
    print("=" * 104)
    variants = [
        ("现状：erp 上行才买", dict(trend_source="erp", trend_window=20)),
        ("erp/60", dict(trend_source="erp", trend_window=60)),
        ("price/20", dict(trend_source="price", trend_window=20)),
        ("price/60", dict(trend_source="price", trend_window=60)),
        ("price/120", dict(trend_source="price", trend_window=120)),
        ("price/200", dict(trend_source="price", trend_window=200)),
        ("反向 erp/20（利差回落才买）", dict(trend_source="erp", trend_window=20, invert=True)),
        ("反向 erp/60", dict(trend_source="erp", trend_window=60, invert=True)),
        ("无趋势（纯 z）", dict(no_trend=True)),
    ]
    rows, sigs = [], {}
    for label, kw in variants:
        sig = signal_state(d, **kw)
        sigs[label] = sig
        r = run_state(d, bond, t0, sig["state"])
        fr = r["frame"]
        b50 = erp.perf(bt.static_bench(bond, fr["tr"], 0.5)["nav"])["ann"]
        ev = sig.loc[fr.index]
        ev = ev[ev["event"] != 0]
        nb = int((ev["event"] > 0).sum())
        nbuy_below = sum(1 for dt in ev[ev["event"] > 0].index
                         if fr.at[dt, "close"] < px_ma[200].loc[dt])
        rows.append(dict(
            label=label,
            ann=r["m"]["ann"] * 100,
            edge=(r["m"]["ann"] - b50) * 100,
            mdd=r["m"]["mdd"] * 100,
            sharpe=r["m"]["sharpe"],
            switches=len(ev),
            buys=nb,
            buy_below_ma200=f"{nbuy_below}/{nb}",
            w_avg=float(r["w"].mean()) * 100,
        ))
    t2 = pd.DataFrame(rows)
    print(t2.to_string(index=False, float_format=lambda x: f"{x:.2f}"))

    print()
    print("=" * 104)
    print("4. 各方案的信号时点（+/−，仅列到月）")
    print("=" * 104)
    used = ["现状：erp 上行才买", "price/20", "price/60", "反向 erp/20（利差回落才买）", "无趋势（纯 z）"]
    for label in used:
        sig = sigs[label].loc[d.index]
        ev = sig[sig["event"] != 0]
        s = " ".join(f"{dt:%Y-%m}{'+' if e > 0 else '-'}" for dt, e in ev["event"].items())
        print(f"{label:>26} | {s}")

    print()
    print("=" * 104)
    print("5. 换锚：滚动分位 / 加绝对水平闸门（都不加趋势；阈值是事后挑的，只用于定位病灶，不是推荐参数）")
    print("=" * 104)
    alts = [
        ("现状 z（250 日 MA±2σ）", base_sig),
        ("滚动 250 日分位 0.9/0.1", percentile_state(df, 250)),
        ("滚动 750 日分位 0.9/0.1", percentile_state(df, 750)),
        ("滚动 1250 日分位 0.9/0.1", percentile_state(df, 1250)),
        ("z + 绝对闸门 ERP≥5.0 / ≤3.5", abs_gate_state(df)),
        ("z + 绝对闸门 ERP≥5.5 / ≤3.0", abs_gate_state(df, 5.5, 3.0)),
    ]
    rows = []
    for label, sig in alts:
        r = run_state(df, bond, t0, sig["state"])
        fr = r["frame"]
        b50 = erp.perf(bt.static_bench(bond, fr["tr"], 0.5)["nav"])["ann"]
        ev = sig.loc[fr.index]
        ev = ev[ev["event"] != 0]
        nb = int((ev["event"] > 0).sum())
        below = sum(1 for dt in ev[ev["event"] > 0].index
                    if fr.at[dt, "close"] < px_ma[200].loc[dt])
        rows.append(dict(label=label, ann=r["m"]["ann"] * 100,
                         edge=(r["m"]["ann"] - b50) * 100, mdd=r["m"]["mdd"] * 100,
                         sharpe=r["m"]["sharpe"], switches=len(ev), buys=nb,
                         buy_below_ma200=f"{below}/{nb}",
                         w_avg=float(r["w"].mean()) * 100))
    print(pd.DataFrame(rows).to_string(index=False, float_format=lambda x: f"{x:.2f}"))


if __name__ == "__main__":
    main()
