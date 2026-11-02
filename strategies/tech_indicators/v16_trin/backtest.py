# -*- coding: utf-8 -*-
"""TRIN（阿姆氏指标）市场广度择时 · 回测。

三组口径并排（诚实对照，不挑好看的报）：

1. **主口径 index**：全市场 TRIN 出信号，执行标的 510300（沪深300ETF），
   信号次日**开盘**买入、第 HOLD_DAYS 根 K 线收盘卖出 —— 与视频口径一致。
2. **池子口径 pool**：同信号，从家族 54 只 ETF 池里按「错杀 + 放量温和 + 未破位」挑 3 只等权，
   信号次日**收盘**买入（共享十年库无开盘价，口径必须标注）。
3. **基准**：510300 买入持有 / 池子等权买入持有。

另出参数敏感性：字段(amt/vol) × 阈值 × 连续天数 × 持有期，用于判断 1.4/20 是不是「唯一能看」的格子。

产物（本目录 backtest/）：
  `_v16_bt.jsonl`   —— 逐口径逐参数格的指标（每行一个格子）
  `_v16_bt_summary.json` —— 主口径的净值/逐年等明细，供 `_reportbt.py` 画图

用法：
  python strategies/tech_indicators/v16_trin/backtest.py
  python strategies/tech_indicators/v16_trin/backtest.py --no-refresh   # 只用本地广度库/行情
模拟结果，非投资建议。
"""
import argparse
import json
import os
import sys

import numpy as np
import pandas as pd

try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:  # noqa
    pass

_DIR = os.path.dirname(os.path.abspath(__file__))
_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(_DIR)))
for _p in (_ROOT, os.path.join(_ROOT, "data"), os.path.dirname(_DIR), _DIR):
    if _p not in sys.path:
        sys.path.insert(0, _p)

import data as _data  # noqa: E402
import rule  # noqa: E402
from pipeline import bt_stats  # noqa: E402

OUT = os.path.join(_DIR, "backtest", "_v16_bt.jsonl")
SUMMARY = os.path.join(_DIR, "backtest", "_v16_bt_summary.json")


def _metrics(nav, pos, trades, dates, begin):
    """净值 + 持仓 -> 年化/回撤/夏普/持仓占比/单笔统计。"""
    nav = np.asarray(nav, float)
    n = len(nav)
    y0, y1 = dates[begin], dates[n - 1]
    years = max((y1 - y0).days / 365.0, 1e-9)
    ret = float(nav[-1] / nav[begin] - 1.0)
    ann = (1 + ret) ** (1 / years) - 1 if ret > -1 else -1.0
    mdd = float((nav[begin:] / np.maximum.accumulate(nav[begin:]) - 1).min())
    daily = np.diff(nav[begin:]) / nav[begin:-1]
    vol = float(np.std(daily, ddof=0) * np.sqrt(252)) if len(daily) else 0.0
    sharpe = float(np.mean(daily) / np.std(daily, ddof=0) * np.sqrt(252)) if len(daily) and np.std(daily) > 0 else 0.0
    ts = bt_stats.trade_stats(trades)
    return dict(years=round(years, 2), ret=round(ret, 4), ann=round(ann, 4), mdd=round(mdd, 4),
                vol=round(vol, 4), sharpe=round(sharpe, 2),
                pos_ratio=round(float(np.mean(np.asarray(pos, bool)[begin:])), 3),
                trades=ts["n"], t_stats=ts,
                bars_avg=round(float(np.mean([t["bars"] for t in trades])), 1) if trades else None)


def _yearly(nav, dates, begin):
    s = pd.Series(np.asarray(nav, float)[begin:], index=pd.DatetimeIndex(dates[begin:]))
    out = {}
    for y, part in s.groupby(s.index.year):
        if len(part) > 1:
            out[str(y)] = round(float(part.iloc[-1] / part.iloc[0] - 1.0), 4)
    return out


def _trade_log(trades, dates):
    return [dict(entry_date=str(pd.Timestamp(dates[t["entry_i"]]).date()),
                 exit_date=str(pd.Timestamp(dates[t["exit_i"]]).date()),
                 bars=t["bars"], ret=round(t["ret"], 4), reason=t["reason"]) for t in trades]


def aligned(breadth, veh):
    """广度信号与执行标的按交易日对齐（取交集，信号缺失补 False）。"""
    idx = veh.index.intersection(breadth.index)
    veh = veh.loc[idx]
    br = breadth.loc[idx]
    return br, veh


def eval_single(br, veh, **kw):
    params = dict(win=rule.TRIN_WIN, th=rule.TRIN_TH, confirm=rule.TRIN_CONFIRM,
                  field=rule.FIELD, hold=rule.HOLD_DAYS, stop=rule.STOP, size=rule.SIZE,
                  fee=rule.FEE, edge=rule.EDGE_TRIGGER)
    params.update({k: v for k, v in kw.items() if v is not None})
    sig = rule.compute_signals(br, win=params["win"], th=params["th"], confirm=params["confirm"],
                               field=params["field"], edge=params["edge"])
    buy = sig["buy"].reindex(veh.index).fillna(False).to_numpy(bool)
    begin = int(np.argmax(sig["trin_ma"].reindex(veh.index).notna().to_numpy())) if sig[
        "trin_ma"].notna().any() else 0
    nav, pos, trades = rule.run_single(veh["open"].to_numpy(float), veh["close"].to_numpy(float),
                                       buy, hold=params["hold"], stop=params["stop"],
                                       fee=params["fee"], size=params["size"])
    m = _metrics(nav, pos, trades, veh.index, begin)
    cc = veh["close"].to_numpy(float)
    rr = cc[1:] / cc[:-1] - 1.0
    pl, rw = np.asarray(pos, bool)[1:][begin:], rr[begin:]
    hold_r = float(np.nanmean(rw[pl])) if pl.any() else 0.0
    flat_r = float(np.nanmean(rw[~pl])) if (~pl).any() else 0.0
    m.update(hold_day_ret=round(hold_r, 5), flat_day_ret=round(flat_r, 5),
             timing_edge=round(hold_r - flat_r, 5))
    base = (cc / cc[begin])
    mb = _metrics(base, np.ones(len(base), bool), [], veh.index, begin)
    m.update(kind="index", code=rule.VEHICLE, label=rule.LABEL, buy_signals=int(buy.sum()),
             exposure=round(m["pos_ratio"], 3), yearly=_yearly(nav, veh.index, begin),
             base_ann=mb["ann"], base_mdd=mb["mdd"], base_sharpe=mb["sharpe"],
             trade_log=_trade_log(trades, veh.index), params=params)
    m["excess_ann"] = round(m["ann"] - mb["ann"], 4)
    return m, nav, pos, trades, begin, base


def eval_pool(br, panels, **kw):
    params = dict(win=rule.TRIN_WIN, th=rule.TRIN_TH, confirm=rule.TRIN_CONFIRM,
                  field=rule.FIELD, hold=rule.HOLD_DAYS, stop=rule.STOP, fee=rule.FEE,
                  k=rule.PICK_K, edge=rule.EDGE_TRIGGER)
    params.update({k: v for k, v in kw.items() if v is not None})
    close, volume = panels["close"], panels["volume"]
    idx = close.index.intersection(br.index)
    close, volume = close.loc[idx], volume.loc[idx]
    sig = rule.compute_signals(br.loc[idx], win=params["win"], th=params["th"],
                               confirm=params["confirm"], field=params["field"], edge=params["edge"])
    buy = sig["buy"].fillna(False).to_numpy(bool)
    fac = rule.pool_factors(close, volume)
    begin = max(int(rule.WARMUP), int(np.argmax(sig["trin_ma"].notna().to_numpy())))
    nav, pos, trades, picks = rule.run_pool(close, buy, fac, hold=params["hold"],
                                            stop=params["stop"], fee=params["fee"],
                                            k=params["k"], begin=begin)
    m = _metrics(nav, pos, trades, idx, begin)
    # 基准：全池等权买入持有（每只等权、各自可得日起算，用等权日收益合成）
    rets = close.pct_change(fill_method=None)
    eq_ret = rets.mean(axis=1, skipna=True).fillna(0.0).to_numpy(float)
    pl, rw = np.asarray(pos, bool)[1:][begin:], eq_ret[1:][begin:]
    hold_r = float(np.nanmean(rw[pl])) if pl.any() else 0.0
    flat_r = float(np.nanmean(rw[~pl])) if (~pl).any() else 0.0
    m.update(hold_day_ret=round(hold_r, 5), flat_day_ret=round(flat_r, 5),
             timing_edge=round(hold_r - flat_r, 5))
    eq = (1 + rets.mean(axis=1, skipna=True).fillna(0.0)).cumprod()
    base = (eq / eq.iloc[begin]).to_numpy(float)
    mb = _metrics(base, np.ones(len(base), bool), [], idx, begin)
    m.update(kind="pool", code=f"POOL{params['k']}", label=rule.LABEL,
             buy_signals=int(buy.sum()), exposure=round(m["pos_ratio"], 3),
             yearly=_yearly(nav, idx, begin), base_ann=mb["ann"], base_mdd=mb["mdd"],
             base_sharpe=mb["sharpe"], picks=picks, trade_log=_trade_log(trades, idx),
             params=params)
    m["excess_ann"] = round(m["ann"] - mb["ann"], 4)
    return m, nav, pos, trades, begin, base, idx


def grid(br, veh):
    """参数敏感性：字段 × 阈值 × 连续天数 × 持有期（止损/触发方式固定默认）。"""
    rows = []
    for field in ("amt", "vol"):
        for th in (1.2, 1.3, 1.4, 1.5, 1.6, 1.8):
            for confirm in (1, 2, 3):
                for hold in (10, 20, 30):
                    m, *_ = eval_single(br, veh, field=field, th=th, confirm=confirm, hold=hold)
                    rows.append(dict(kind="grid", field=field, th=th, confirm=confirm, hold=hold,
                                     ann=m["ann"], mdd=m["mdd"], sharpe=m["sharpe"],
                                     pos_ratio=m["pos_ratio"], trades=m["trades"],
                                     win_rate=round(m["t_stats"]["win_rate"], 3),
                                     payoff=round(m["t_stats"]["payoff"], 2),
                                     buy_signals=m["buy_signals"], excess_ann=m["excess_ann"],
                                     timing_edge=m["timing_edge"]))
    return rows


def size_grid(br, veh):
    """仓位上限敏感性：视频说「不满仓」，这里把投入比例做成 4 档。"""
    rows = []
    for size in (0.4, 0.6, 0.8, 1.0):
        m, *_ = eval_single(br, veh, size=size)
        rows.append(dict(kind="size", size=size, ann=m["ann"], mdd=m["mdd"],
                         sharpe=m["sharpe"], pos_ratio=m["pos_ratio"], trades=m["trades"],
                         avg_alloc=round(m["pos_ratio"] * size, 3),
                         win_rate=round(m["t_stats"]["win_rate"], 3),
                         payoff=round(m["t_stats"]["payoff"], 2),
                         excess_ann=m["excess_ann"], timing_edge=m["timing_edge"]))
    return rows


def stop_grid(br, veh):
    """止损与触发方式敏感性（其余参数取默认）。"""
    rows = []
    for stop in (0.0, 0.05, 0.08, 0.10, 0.15):
        for edge in (True, False):
            m, *_ = eval_single(br, veh, stop=stop, edge=edge)
            rows.append(dict(kind="stop", stop=stop, edge=edge, ann=m["ann"], mdd=m["mdd"],
                             sharpe=m["sharpe"], pos_ratio=m["pos_ratio"], trades=m["trades"],
                             win_rate=round(m["t_stats"]["win_rate"], 3),
                             payoff=round(m["t_stats"]["payoff"], 2),
                             excess_ann=m["excess_ann"], timing_edge=m["timing_edge"]))
    return rows


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--no-refresh", action="store_true")
    ap.add_argument("--no-grid", action="store_true")
    a = ap.parse_args()
    os.makedirs(os.path.dirname(OUT), exist_ok=True)

    breadth = _data.load_breadth(refresh=not a.no_refresh)
    veh = _data.load_vehicle(rule.VEHICLE, refresh=not a.no_refresh)
    br, veh = aligned(breadth, veh.loc[rule.START:])
    end = _data.complete_end(br.index)
    br, veh = br.loc[:end], veh.loc[:end]
    print(f"[TRIN] 广度 {br.index[0].date()} ~ {br.index[-1].date()} · {len(br)} 天"
          f" · 标的 {rule.VEHICLE}")

    main_row, nav, pos, trades, begin, base = eval_single(br, veh)
    print(f"[主口径] 年化 {main_row['ann']:+.2%} · 回撤 {main_row['mdd']:+.2%}"
          f" · 夏普 {main_row['sharpe']:.2f} · 持仓 {main_row['pos_ratio']:.1%}"
          f" · {main_row['trades']} 笔 · 胜率 {main_row['t_stats']['win_rate']:.1%}"
          f" · 择时边际 {main_row['timing_edge'] * 1e4:+.1f}bp"
          f" | 买入持有 {main_row['base_ann']:+.2%}/{main_row['base_mdd']:+.2%}")

    panels = _data.load_pool(refresh=not a.no_refresh)
    pool_row, pnav, ppos, ptrades, pbegin, pbase, pidx = eval_pool(br, panels)
    print(f"[池子口径] 年化 {pool_row['ann']:+.2%} · 回撤 {pool_row['mdd']:+.2%}"
          f" · 持仓 {pool_row['pos_ratio']:.1%} · {pool_row['trades']} 笔"
          f" | 全池等权 {pool_row['base_ann']:+.2%}/{pool_row['base_mdd']:+.2%}")

    rows = [main_row, pool_row]
    if not a.no_grid:
        g = grid(br, veh)
        rows += g + stop_grid(br, veh) + size_grid(br, veh)
        best = max(g, key=lambda r: r["sharpe"])
        print(f"[参数网格] {len(g)} 格 · 夏普最优 field={best['field']} th={best['th']} "
              f"confirm={best['confirm']} hold={best['hold']} -> 年化 {best['ann']:+.2%}")

    with open(OUT, "w", encoding="utf-8") as fh:
        for r in rows:
            clean = r
            if r["kind"] in ("grid", "stop", "size"):
                clean = {k: v for k, v in r.items() if k != "trade_log"}
            fh.write(json.dumps(clean, ensure_ascii=False) + "\n")
    summary = dict(
        generated_at=pd.Timestamp.now().strftime("%Y-%m-%d %H:%M"),
        label=rule.LABEL, params=rule.PARAMS,
        window=dict(start=str(br.index[begin].date()), end=str(br.index[-1].date()),
                    days=int(len(br) - begin)),
        index=dict(dates=[str(d.date()) for d in veh.index],
                   nav=[round(float(x), 6) for x in nav],
                   base=[round(float(x), 6) for x in base],
                   pos=[bool(x) for x in pos]),
        pool=dict(dates=[str(d.date()) for d in pidx],
                  nav=[round(float(x), 6) for x in pnav],
                  base=[round(float(x), 6) for x in pbase],
                  pos=[bool(x) for x in ppos]),
        main={k: v for k, v in main_row.items() if k != "trade_log"},
        pool_main={k: v for k, v in pool_row.items() if k != "trade_log"},
        main_trades=main_row["trade_log"], pool_trades=pool_row["trade_log"],
        pool_picks=pool_row.get("picks", []),
    )
    json.dump(summary, open(SUMMARY, "w", encoding="utf-8"), ensure_ascii=False)
    print(f"           -> {OUT}\n           -> {SUMMARY}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
