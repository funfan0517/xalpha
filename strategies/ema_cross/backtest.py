# -*- coding: utf-8 -*-
"""双均线趋势策略(EMA12/26 · 金叉死叉)官方回测 —— 逐场内标的状态机择时 vs 买入持有。

数据管线: 与 four_lights 相同, xa.get_daily(场内, start=2015) 提供 EMA warm-up;
样本窗口: SAMPLE_FROM(2016-09-01) ~ 至今(最长十年, 上市晚者按实有);
执行口径: 当日收盘信号 -> 次日开盘成交(回测/实盘一致, 无前视);
成本: 单边 0.03%(场内 ETF 佣金), 每笔按双边扣费; 期末持仓按最新收盘估值。
输出: 每标的一行 JSON(schema 对齐 pipeline flow_select/_reportbt):
  {code, theme, off, cat, ok, years, base_ann/base_mdd, st_ann/st_mdd,
   pos_ratio, trades, t_stats, trade_log, ...}
用法: python strategies/ema_cross/backtest.py [code1,code2,...]   # 缺省跑全池
"""
import io
import json
import sys

import numpy as np

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8")

import os

_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

import rule
from pipeline import bt_stats


def _stat(series, years):
    ret = series[-1] / series[0] - 1
    ann = (1 + ret) ** (1 / years) - 1 if ret > -1 else -1.0
    dd = float((series / np.maximum.accumulate(series) - 1).min())
    return ret, ann, dd


def run_backtest(df, code):
    """df: rule.load_bars(code) 全序列(date/open/close, 升序) -> 结果 row dict。"""
    n = len(df)
    dates = df["date"]
    o = df["open"].to_numpy(dtype=float)
    c = df["close"].to_numpy(dtype=float)

    # 样本窗口起点(此前为 EMA warm-up 背景; 窗口内信号才触发交易)
    win = (dates >= np.datetime64(rule.SAMPLE_FROM)).to_numpy()
    if not win.any():
        return None
    base = int(np.argmax(win))
    if (n - base) < 120:  # 窗口内样本不足(上市晚)
        return None

    sig = rule.build_signals(df["close"])
    nav, pos_ratio, trades0, _ = rule.run_engine(o, c, sig["buy"], sig["sell"], begin=base)
    if nav[base] <= 0:
        return None

    trade_log = []
    for tr in trades0:
        ei, xi = tr["entry_i"], tr["exit_i"]
        trade_log.append(dict(
            code=code, entry_date=str(dates.iloc[ei].date()),
            exit_date=str(dates.iloc[xi].date()), bars=int(xi - ei), ret=tr["ret"]))

    y0, y1 = dates.iloc[base], dates.iloc[-1]
    years = max((y1 - y0).days / 365.0, 1e-9)
    bh = c / c[base]
    st = nav / nav[base]
    bh = bh[base:]
    st = st[base:]
    base_ret, base_ann, base_mdd = _stat(bh, years)
    st_ret, st_ann, st_mdd = _stat(st, years)
    ts = bt_stats.trade_stats(trade_log)
    return dict(
        start=str(y0.date()), end=str(y1.date()), days=int(n - base),
        years=round(years, 2),
        base_ret=round(base_ret, 4), base_ann=round(base_ann, 4), base_mdd=round(base_mdd, 4),
        st_ret=round(st_ret, 4), st_ann=round(st_ann, 4), st_mdd=round(st_mdd, 4),
        trades=ts["n"], t_stats=ts, trade_log=trade_log, pos_ratio=round(pos_ratio, 3))


def main():
    want = set(sys.argv[1].split(",")) if len(sys.argv) > 1 and sys.argv[1].strip() else None
    for row in rule.POOL:
        code = row["code"]
        if want and code not in want:
            continue
        meta = dict(code=code, idx=row["idx"], cat=row["cat"],
                    theme=row["theme"], off=row["off_name"])
        try:
            df = rule.load_bars(code)
        except Exception as e:  # noqa: BLE001 - 单标的数据失败仅记录, 不影响其他标的
            print(json.dumps({**meta, "ok": False,
                              "err": f"{type(e).__name__}: {e}"}, ensure_ascii=False), flush=True)
            continue
        r = run_backtest(df, code)
        if r is None:
            print(json.dumps({**meta, "ok": False, "err": "样本不足(上市晚/无窗口)"},
                              ensure_ascii=False), flush=True)
            continue
        print(json.dumps({"ok": True, **meta, **r}, ensure_ascii=False), flush=True)


if __name__ == "__main__":
    main()
