# -*- coding: utf-8 -*-
"""右侧买入策略 · 逐标的回测。

口径：
  - 当日收盘算信号 -> **次日收盘**成交（十年库只有收盘价，全程无未来数据）；
  - 单边 FEE 佣金；0/1 满仓；期末持仓按最新收盘估值；
  - 每只标的用自己的交易日序列（停牌日丢弃，不 ffill）；
  - 样本 SAMPLE_FROM(2016-09-01) 起，窗口内样本 >= MIN_BARS(250) 交易日。

输出：`strategies/right_side/backtest/_right_side_bt.jsonl`（每标的一行，schema 兼容
`pipeline/flow_select.py` 与 `pipeline/bt_stats`）。
用法：python strategies/right_side/backtest.py [--out PATH] [code1,code2,...]
"""
import json
import os
import sys

import numpy as np
import pandas as pd

sys.stdout.reconfigure(encoding="utf-8")
_DIR = os.path.dirname(os.path.abspath(__file__))
_ROOT = os.path.dirname(os.path.dirname(_DIR))
for p in (_ROOT, os.path.dirname(_DIR), _DIR):
    if p not in sys.path:
        sys.path.insert(0, p)

import data as _data  # noqa: E402
import rule  # noqa: E402
from pipeline import bt_stats  # noqa: E402

OUT_DEFAULT = os.path.join(_DIR, "backtest", "_right_side_bt.jsonl")


def _stat(series, years):
    ret = float(series[-1] / series[0] - 1.0)
    ann = (1 + ret) ** (1 / years) - 1 if ret > -1 else -1.0
    mdd = float((series / np.maximum.accumulate(series) - 1).min())
    return ret, ann, mdd


def run_one(df, code, begin):
    """单标的 -> 结果 dict（None = 数据不足）。"""
    f = rule.compute_factors(df)
    sig = rule.build_signals(f)
    c = df["c"].to_numpy(float)
    if len(c) - begin < rule.MIN_BARS:
        return None
    nav, pos_ratio, trades, _holding, pos_log = rule.run_engine(c, sig, begin=begin)
    if not np.isfinite(nav[begin]) or nav[begin] <= 0:
        return None

    dates = df.index
    trade_log = [dict(code=code, entry_date=str(dates[t["entry_i"]].date()),
                      exit_date=str(dates[t["exit_i"]].date()), bars=t["bars"],
                      ret=t["ret"], reason=t["reason"]) for t in trades]
    y0, y1 = dates[begin], dates[-1]
    years = max((y1 - y0).days / 365.0, 1e-9)
    base_ret, base_ann, base_mdd = _stat((c / c[begin])[begin:], years)
    st_ret, st_ann, st_mdd = _stat((nav / nav[begin])[begin:], years)
    ts = bt_stats.trade_stats(trade_log)

    rr = c[1:] / c[:-1] - 1.0
    pl, rw = pos_log[1:][begin:], rr[begin:]
    hold_r = float(np.nanmean(rw[pl])) if pl.any() else 0.0
    flat_r = float(np.nanmean(rw[~pl])) if (~pl).any() else 0.0
    bars = [t["bars"] for t in trades]
    sc = sig["score"][begin:]
    votes = {k: round(float(np.mean(v[begin:])), 3) for k, v in sig["votes"].items()}
    reasons = {}
    for t in trades:
        reasons[t["reason"]] = reasons.get(t["reason"], 0) + 1

    return dict(
        start=str(y0.date()), end=str(y1.date()), days=int(len(c) - begin),
        years=round(years, 2),
        base_ret=round(base_ret, 4), base_ann=round(base_ann, 4), base_mdd=round(base_mdd, 4),
        st_ret=round(st_ret, 4), st_ann=round(st_ann, 4), st_mdd=round(st_mdd, 4),
        trades=ts["n"], t_stats=ts, trade_log=trade_log, exit_reasons=reasons,
        pos_ratio=round(pos_ratio, 3),
        sig_ratio=round(float(np.mean(sig["buy"][begin:])), 3),
        watch_ratio=round(float(np.mean(sig["watch"][begin:])), 3),
        veto_ratio=round(float(np.mean(sig["veto"][begin:])), 3),
        score_avg=round(float(np.mean(sc)), 2),
        score_ge4_ratio=round(float(np.mean(sc >= rule.VOTE_ENTER)), 3),
        votes=votes,
        bars_avg=round(float(np.mean(bars)), 1) if bars else None,
        bars_med=float(np.median(bars)) if bars else None,
        hold_day_ret=round(hold_r, 5), flat_day_ret=round(flat_r, 5),
        timing_edge=round(hold_r - flat_r, 5),
    )


def run_pool(panels, want=None):
    rows, fails = [], []
    end = _data.complete_end(panels["close"].index)
    for meta in _data.POOL:
        code = meta["code"]
        if want and code not in want:
            continue
        base = dict(code=code, idx=meta["idx"], cat=meta["cat"],
                    theme=meta["theme"], off=meta["off_name"])
        try:
            df = _data.load_code(panels, code)
        except KeyError:
            fails.append({**base, "ok": False, "err": "行情库无此标的"})
            continue
        df = df.loc[:end]
        if len(df) < rule.MIN_BARS + 60:
            fails.append({**base, "ok": False, "err": "样本不足"})
            continue
        begin = int(np.argmax(np.asarray(df.index >= np.datetime64(rule.SAMPLE_FROM))))
        try:
            r = run_one(df, code, begin)
        except Exception as e:  # noqa: BLE001 - 单标的失败仅记录
            fails.append({**base, "ok": False, "err": f"{type(e).__name__}: {e}"})
            continue
        if r is None:
            fails.append({**base, "ok": False, "err": "样本不足/数据无效"})
            continue
        rows.append({"ok": True, **base, **r})
    return rows, fails


def summarize(rows, tag):
    if not rows:
        print(f"[{tag}] 无有效标的")
        return
    ag = lambda k: float(np.mean([r[k] for r in rows]))  # noqa: E731
    beat = sum(r["st_ann"] > r["base_ann"] for r in rows)
    print(f"[{tag}] n={len(rows)} | 基准年化 {ag('base_ann') * 100:+.1f}%"
          f" | 策略年化 {ag('st_ann') * 100:+.1f}% | 超额 {ag('st_ann') - ag('base_ann'):+.1%}"
          f" | 回撤 基准 {ag('base_mdd') * 100:+.1f}% vs 策略 {ag('st_mdd') * 100:+.1f}%")
    print(f"          持仓 {ag('pos_ratio') * 100:.1f}% | 买点命中 {ag('sig_ratio') * 100:.1f}%"
          f" | 观察 {ag('watch_ratio') * 100:.1f}% | 平均票数 {ag('score_avg'):.2f}"
          f" | 跑赢 {beat}/{len(rows)} | 择时边际 {ag('timing_edge') * 1e4:+.1f}bp"
          f"（为正 {sum(1 for r in rows if r['timing_edge'] > 0)}/{len(rows)}）")


def main():
    argv = sys.argv[1:]
    out, want = OUT_DEFAULT, set()
    skip = set()
    for i, a in enumerate(argv):
        if a == "--out":
            out = argv[i + 1]
            skip.add(i + 1)
        elif i not in skip and not a.startswith("--"):
            want |= set(a.split(","))
    os.makedirs(os.path.dirname(out), exist_ok=True)

    panels = _data.load_panels(refresh=True)
    rows, fails = run_pool(panels, want or None)
    new_rows = rows

    # 分批回测（指定 --codes 且产物已存在）-> **按代码 upsert**，不覆盖其他标的的结果。
    # 全池跑（无 codes）或 --fresh（文件已被 run_flow 删除）-> 直接重写。
    if want and os.path.exists(out):
        old = [json.loads(l) for l in open(out, encoding="utf-8")
               if l.strip().startswith("{")]
        keep = [r for r in old if r.get("code") not in want]
        rows = keep + rows
        print(f"[分批] 合并已有产物：保留 {len(keep)} 行 + 本次 {len(new_rows)} 行")

    with open(out, "w", encoding="utf-8") as fh:
        for r in rows + fails:
            fh.write(json.dumps(r, ensure_ascii=False) + "\n")
    summarize(new_rows, f"{rule.LABEL} [本次 {len(new_rows)} 只] -> {os.path.basename(out)}")
    if fails:
        print(f"          未纳入 {len(fails)} 只（样本不足/失败）")
    return 0


if __name__ == "__main__":
    sys.exit(main())
