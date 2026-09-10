# -*- coding: utf-8 -*-
"""四灯共振 5-10 天短线变种 · 全池回测: 逐场内标的状态机择时 vs 买入持有。

数据管线: 与 four_lights/resonance_4d/_backtest.py 相同, xa.get_daily(场内, start=2015) warm-up;
样本窗口: 2016-09 ~ 至今(最长十年, 上市晚者按实有数据, 窗口内样本 >= 120 日);
执行口径: 当日收盘信号 -> 次日开盘成交(无前视); 成本: 单边 0.03%(ETF 佣金);
规则细节见 _st10_rule.py(docstring 决策表)。

用法: python strategies/four_lights/short_5_10d/_st10_backtest.py [code1,code2,...]  # 缺省跑全池
输出: 每标的一行 JSON -> 由 _st10_report.py 汇总。
"""
import io
import json
import sys

import numpy as np

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8")

import os

_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

import _st10_rule as rule
from pipeline import bt_stats

START = rule.START
SAMPLE_FROM = rule.SAMPLE_FROM


def _stat(series, years):
    ret = series[-1] / series[0] - 1
    ann = (1 + ret) ** (1 / years) - 1 if ret > -1 else -1.0
    dd = float((series / np.maximum.accumulate(series) - 1).min())
    return ret, ann, dd


def run_backtest(df, code):
    """df: rule.load_bars(code) 全序列(date/open/close/volume, 升序) -> 结果 row dict。"""
    n = len(df)
    dates = df["date"]
    o = df["open"].to_numpy(dtype=float)
    c = df["close"].to_numpy(dtype=float)

    win = (dates >= np.datetime64(SAMPLE_FROM)).to_numpy()
    if not win.any():
        return None
    base = int(np.argmax(win))
    if (n - base) < 120:  # 窗口内样本不足(上市晚)
        return None

    sig = rule.build_signals(df)
    nav, pos_ratio, trades0, holding, bars_log = rule.run_engine(o, c, sig, begin=base)
    if nav[base] <= 0:
        return None

    trade_log = []
    for tr in trades0:
        ei, xi = tr["entry_i"], tr["exit_i"]
        trade_log.append(dict(
            code=code, entry_date=str(dates.iloc[ei].date()),
            exit_date=str(dates.iloc[xi].date()), bars=int(tr["bars"]), ret=tr["ret"]))

    y0, y1 = dates.iloc[base], dates.iloc[-1]
    years = max((y1 - y0).days / 365.0, 1e-9)
    bh = (c / c[base])[base:]
    st = (nav / nav[base])[base:]
    base_ret, base_ann, base_mdd = _stat(bh, years)
    st_ret, st_ann, st_mdd = _stat(st, years)
    ts = bt_stats.trade_stats(trade_log)
    return dict(
        start=str(y0.date()), end=str(y1.date()), days=int(n - base),
        years=round(years, 2),
        base_ret=round(base_ret, 4), base_ann=round(base_ann, 4), base_mdd=round(base_mdd, 4),
        st_ret=round(st_ret, 4), st_ann=round(st_ann, 4), st_mdd=round(st_mdd, 4),
        trades=ts["n"], t_stats=ts, trade_log=trade_log,
        pos_ratio=round(pos_ratio, 3),
        bars_avg=round(float(np.mean(bars_log)), 1) if bars_log else None,
        bars_med=float(np.median(bars_log)) if bars_log else None,
    )


OUT = os.path.join(_ROOT, "data", "_st10_out.jsonl")


def main():
    want = set(sys.argv[1].split(",")) if len(sys.argv) > 1 and sys.argv[1].strip() else None
    fh = open(OUT, "w", encoding="utf-8")
    for row in rule.POOL:
        code = row["code"]
        if want and code not in want:
            continue
        meta = dict(code=code, idx=row["idx"], cat=row["cat"],
                    theme=row["theme"], off=row["off_name"])
        try:
            df = rule.load_bars(code)
        except Exception as e:  # noqa: BLE001 - 单标的数据失败仅记录, 不影响其他标的
            ln = json.dumps({**meta, "ok": False,
                             "err": f"{type(e).__name__}: {e}"}, ensure_ascii=False)
            print(ln, flush=True)
            fh.write(ln + "\n")
            continue
        r = run_backtest(df, code)
        if r is None:
            ln = json.dumps({**meta, "ok": False, "err": "样本不足(上市晚/无窗口)"},
                            ensure_ascii=False)
            print(ln, flush=True)
            fh.write(ln + "\n")
            continue
        ln = json.dumps({"ok": True, **meta, **r}, ensure_ascii=False)
        print(ln, flush=True)
        fh.write(ln + "\n")
    fh.close()


if __name__ == "__main__":
    main()
