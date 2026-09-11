# -*- coding: utf-8 -*-
"""亮灯策略 · 逐标的回测。

口径: 第 t 日收盘算信号 -> 第 t+1 日开盘成交（无前视）; 单边 cfg.fee; 期末按最新收盘估值。
标的: 唯一池 data/_universe.md「有场内对应」的行; 每只标的用自己的交易日序列（不 ffill）。
样本: SAMPLE_FROM(2016-09-01) 起, 窗口内样本 >= cfg.min_bars(120) 交易日。

输出: 每标的一行 JSON（schema 兼容 pipeline/flow_select.py 与 pipeline/bt_stats）。
用法: python strategies/lights/backtest.py [--preset NAME] [--out PATH] [code1,code2,...]
      --preset 缺省 = c7（生效配置）。
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

import rule  # noqa: E402
from pipeline import bt_stats  # noqa: E402

OUT_DEFAULT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "_lights_bt.jsonl")


def _stat(series, years):
    ret = float(series[-1] / series[0] - 1.0)
    ann = (1 + ret) ** (1 / years) - 1 if ret > -1 else -1.0
    mdd = float((series / np.maximum.accumulate(series) - 1).min())
    return ret, ann, mdd


def run_one(df, code, cfg, begin):
    """单标的 -> 结果 dict（None = 数据不足）。"""
    f = rule.compute_factors(df, cfg, code)
    sig = rule.build_signals(f, cfg)
    o = df["open"].to_numpy(float)
    c = df["close"].to_numpy(float)
    if len(c) - begin < cfg.min_bars:
        return None
    nav, pos_ratio, trades, _holding, pos_log = rule.run_engine(o, c, sig, begin=begin, cfg=cfg)
    if not np.isfinite(nav[begin]) or nav[begin] <= 0:
        return None

    dates = df.index
    trade_log = [dict(code=code, entry_date=str(dates[t["entry_i"]].date()),
                      exit_date=str(dates[t["exit_i"]].date()), bars=t["bars"], ret=t["ret"])
                 for t in trades]
    y0, y1 = dates[begin], dates[-1]
    years = max((y1 - y0).days / 365.0, 1e-9)
    bh = (c / c[begin])[begin:]
    st = (nav / nav[begin])[begin:]
    base_ret, base_ann, base_mdd = _stat(bh, years)
    st_ret, st_ann, st_mdd = _stat(st, years)
    ts = bt_stats.trade_stats(trade_log)

    rr = c[1:] / c[:-1] - 1.0
    pl, rw = pos_log[1:][begin:], rr[begin:]
    hold_r = float(np.nanmean(rw[pl])) if pl.any() else 0.0
    flat_r = float(np.nanmean(rw[~pl])) if (~pl).any() else 0.0
    bars = [t["bars"] for t in trade_log]

    return dict(
        start=str(y0.date()), end=str(y1.date()), days=int(len(c) - begin), years=round(years, 2),
        base_ret=round(base_ret, 4), base_ann=round(base_ann, 4), base_mdd=round(base_mdd, 4),
        st_ret=round(st_ret, 4), st_ann=round(st_ann, 4), st_mdd=round(st_mdd, 4),
        trades=ts["n"], t_stats=ts, trade_log=trade_log,
        pos_ratio=round(pos_ratio, 3),
        sig_ratio=round(float(np.mean(sig["enter"][begin:])), 3),
        filt_ratio=round(float(np.mean(sig["gate_pass"][begin:])), 3),
        score_avg=round(float(np.mean(sig["score"][begin:])), 2),
        bars_avg=round(float(np.mean(bars)), 1) if bars else None,
        bars_med=float(np.median(bars)) if bars else None,
        hold_day_ret=round(hold_r, 5), flat_day_ret=round(flat_r, 5),
        timing_edge=round(hold_r - flat_r, 5),
    )


def run_pool(panels, cfg, end, want=None):
    rows, fails = [], []
    for meta in rule.POOL:
        code = meta["code"]
        if want and code not in want:
            continue
        base = dict(code=code, idx=meta["idx"], cat=meta["cat"],
                    theme=meta["theme"], off=meta["off_name"])
        df = rule.trading_frame(panels, code, end)
        if cfg.sample_to:                      # 样本外/子区间确认
            df = df.loc[:pd.Timestamp(cfg.sample_to)]
        if len(df) < cfg.min_bars + 60:
            fails.append({**base, "ok": False, "err": "样本不足"})
            continue
        begin = int(np.argmax(np.asarray(df.index >= np.datetime64(cfg.sample_from))))
        try:
            r = run_one(df, code, cfg, begin)
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
    print(f"[{tag}] n={len(rows)} | 基准年化 {ag('base_ann') * 100:+.1f}% | 策略年化 {ag('st_ann') * 100:+.1f}%"
          f" | 超额 {ag('st_ann') - ag('base_ann'):+.1%} | 回撤 基准 {ag('base_mdd') * 100:+.1f}%"
          f" vs 策略 {ag('st_mdd') * 100:+.1f}%")
    print(f"          持仓 {ag('pos_ratio') * 100:.1f}% | 信号满足 {ag('sig_ratio') * 100:.1f}%"
          f" | 平均分 {ag('score_avg'):.2f} | 跑赢 {beat}/{len(rows)}"
          f" | 择时边际 {ag('timing_edge') * 1e4:+.1f}bp"
          f" (为正 {sum(1 for r in rows if r['timing_edge'] > 0)}/{len(rows)})")


def main():
    argv = sys.argv[1:]
    preset, out = "c7", OUT_DEFAULT
    want, sets = set(), {}
    skip = set()
    for i, a in enumerate(argv):
        if a == "--preset":
            preset = argv[i + 1]
            skip.add(i + 1)
        elif a == "--out":
            out = argv[i + 1]
            skip.add(i + 1)
        elif a == "--set":
            for kv in argv[i + 1].split(","):
                k, _, v = kv.partition("=")
                try:
                    sets[k.strip()] = json.loads(v)
                except json.JSONDecodeError:
                    sets[k.strip()] = v
            skip.add(i + 1)
        elif i not in skip and not a.startswith("--"):
            want |= set(a.split(","))
    base = rule.PRESETS.get(preset) or rule.active_config()
    cfg = base.with_(**sets) if sets else base
    if sets:
        print(f"参数覆盖: {sets}")

    panels = rule.load_panels_raw()
    end = rule._data.complete_end(panels["close"].index)
    rows, fails = run_pool(panels, cfg, end, want or None)

    with open(out, "w", encoding="utf-8") as fh:
        for r in rows + fails:
            fh.write(json.dumps(r, ensure_ascii=False) + "\n")
    summarize(rows, f"{cfg.label} -> {os.path.basename(out)}")
    if fails:
        print(f"          未纳入 {len(fails)} 只（样本不足/失败）")
    return 0


if __name__ == "__main__":
    sys.exit(main())
