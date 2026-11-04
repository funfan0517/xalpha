# -*- coding: utf-8 -*-
"""V17 趋势四象限闸门 · 数据装载（家族 54 只场内 ETF 收盘价面板）。

与 v5_trend_gate 的核心差别：**数据源从 vol_legs 的七腿场外复权净值，换成 tech_indicators
家族的 54 只场内 ETF 收盘价**。行情来自共享十年库 `data/_long_klines.json`（close），
标的池唯一入口是 `../_pool.py`（`data/_universe.md` 前四类），与 v9/v10/v16 同池 ——
**改池子只改 `data/_universe.md`**，这里不放任何硬编码代码。
"""
import json
import os
import sys

import pandas as pd

_HERE = os.path.dirname(os.path.abspath(__file__))                     # v17_trend_gate/
_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(_HERE)))         # 仓库根
_DATA = os.path.join(_ROOT, "data")
_PARENT = os.path.dirname(_HERE)                                        # strategies/tech_indicators
for _p in (_ROOT, _DATA, _PARENT, _HERE):
    if _p not in sys.path:
        sys.path.insert(0, _p)

import _pool  # noqa: E402  家族唯一池定义
import _refresh_data  # noqa: E402  公用行情刷新器(data/)

LK = os.path.join(_DATA, "_long_klines.json")
POOL = _pool.rows()
CODES = [r["code"] for r in POOL]
NAMES = {r["code"]: (r.get("inner_name") or r.get("off_name") or r["code"]) for r in POOL}


def _panel(refresh=True):
    """-> {code: close Series}（只含池内且有数据的代码；去重、按日期排序）。"""
    if refresh:
        try:
            _refresh_data.ensure_fresh(verbose=False)
        except Exception as e:  # noqa: BLE001
            print(f"[行情] 刷新检查失败，沿用本地数据：{type(e).__name__}: {e}")
    lk = json.load(open(LK, encoding="utf-8"))
    out = {}
    for code in CODES:
        d = lk.get(code)
        if not d or "close" not in d or len(d["close"]) < 2:
            continue
        idx = pd.to_datetime(d["dates"])
        s = pd.Series([None if v is None else float(v) for v in d["close"]],
                      index=idx, dtype=float)
        s = s[~s.index.duplicated(keep="last")].sort_index()
        out[code] = s
    return out


def codes():
    """允许进入回测/扫描的场内代码集合。"""
    return set(CODES)


def names():
    """代码 -> 场内名称。"""
    return dict(NAMES)


def series(code, refresh=False):
    """单只标的收盘价序列；缺失/刷新失败直接抛错，不静默补位。"""
    p = _panel(refresh=refresh)
    if code not in p:
        raise KeyError(f"池内无 {code} 的行情（不在 _long_klines.json 或 _universe.md 前四类）")
    return p[code]


def panel(refresh=True):
    """全池收盘价面板（index=日期并集，每列一只 ETF；缺失 NaN）。"""
    return _panel(refresh=refresh)


def complete_end(idx):
    """最后一个「已收盘」交易日（避免盘中半日 bar 污染信号）。"""
    if len(idx) == 0:
        return None
    last = idx[-1]
    now = pd.Timestamp.now()
    if last.date() == now.date() and now.hour < 15:
        return idx[-2] if len(idx) > 1 else last
    return last


def _main():
    p = _panel(refresh=False)
    print(f"池子 {len(CODES)} 只 · 有行情 {len(p)} 只 · "
          f"缺价 {[c for c in CODES if c not in p]}")
    if p:
        s = next(iter(p.values()))
        print(f"样本 {s.index[0].date()} ~ {s.index[-1].date()} · {len(s)} 天")


if __name__ == "__main__":
    _main()
