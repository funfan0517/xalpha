# -*- coding: utf-8 -*-
"""右侧买入策略 · 行情装载（只读公用十年库，策略自身不联网）。

数据来源（**数据类**，留在 `data/`，跨策略共享）：
  - `data/_long_klines.json`    收盘价（2015-01 起，前复权）
  - `data/_indicators_hist.json` 成交量（本策略只取 volume，指标一律自算，避免两套口径打架）
刷新：`data/_refresh_data.py::ensure_fresh()` —— 增量 + 复权一致性校验，已最新时只做日期比较。

标的池唯一入口：`data/_universe.md` 的「场内对应」列（由 `pipeline/universe.py` 派生），**禁止硬编码**。
"""
import json
import os
import sys
from datetime import datetime

import pandas as pd

_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)
if os.path.join(_ROOT, "data") not in sys.path:
    sys.path.insert(0, os.path.join(_ROOT, "data"))

from pipeline import universe  # noqa: E402

LK = os.path.join(_ROOT, "data", "_long_klines.json")
IND = os.path.join(_ROOT, "data", "_indicators_hist.json")

POOL = universe.inner_rows()                       # [{idx, code, cat, theme, off_name, inner_name}]
CODES = [r["code"] for r in POOL]
CATS = {r["code"]: r["cat"] for r in POOL}
NAMES = {r["code"]: r["inner_name"] for r in POOL}
THEMES = {r["code"]: r["theme"] for r in POOL}


def sh(code):
    """6 位场内代码 -> xalpha 前缀格式（SH/SZ）。"""
    return ("SH" if code.startswith(("5", "6", "9")) else "SZ") + code


def _refresh(verbose=True):
    """触发公用行情库刷新检查（进程内幂等，已最新时几乎零成本）。"""
    try:
        import _refresh_data
        return _refresh_data.ensure_fresh(verbose=verbose)
    except Exception as e:  # noqa: BLE001 - 刷新失败不应中断只读策略
        print(f"[行情] 刷新检查失败，沿用本地数据：{type(e).__name__}: {e}")
        return {}, {}


def load_panels(refresh=True):
    """-> {field: DataFrame(index=DatetimeIndex, columns=code)}，field ∈ {close, volume}。

    每只标的保留**自身交易日**（不 ffill）：停牌日留 NaN，下游按行丢弃，
    避免补齐后的 0 成交量污染量比类因子（与 lights 同一教训）。
    """
    if refresh:
        _refresh()
    lk = json.load(open(LK, encoding="utf-8"))
    ind = json.load(open(IND, encoding="utf-8"))
    ind.pop("_meta", None)
    keep = set(CODES)                       # 只保留当前池子，防旧缓存里已剔除的标的混入
    close, volume = {}, {}
    for code in keep:
        d = lk.get(code)
        if not d:
            continue
        idx = pd.to_datetime(d["dates"])
        s = pd.Series(d["close"], index=idx, dtype=float)
        close[code] = s[~s.index.duplicated(keep="last")].sort_index()
        v = (ind.get(code) or {}).get("volume")
        if v and len(v) == len(d["dates"]):
            vs = pd.Series([None if x is None else float(x) for x in v], index=idx, dtype=float)
            volume[code] = vs[~vs.index.duplicated(keep="last")].sort_index()
    return {"close": pd.DataFrame(close).sort_index(),
            "volume": pd.DataFrame(volume).sort_index()}


def load_code(panels, code):
    """单标的 -> DataFrame(index=date, c, v)，丢弃 close 缺失行。"""
    df = pd.DataFrame({"c": panels["close"][code], "v": panels["volume"][code]}).dropna(subset=["c"])
    return df.sort_index()


def complete_end(idx, close_hour=15):
    """最后一个「已收盘」交易日（避免盘中半日 bar 污染指标与扫描结论）。

    若最后一根 bar 就是今天且当前未到 15:00，它是盘中未完成 bar（成交量与涨幅都失真），
    回测/扫描应回退一天；实盘「今日盘中」结论另行标注。
    """
    if len(idx) == 0:
        return None
    last = idx[-1]
    now = datetime.now()
    if last.date() == now.date() and now.hour < close_hour:
        return idx[-2] if len(idx) > 1 else last
    return last


def _main():
    p = load_panels(refresh=False)
    print(f"标的池: {len(CODES)} 只（{os.path.join(_ROOT, 'data', '_universe.md')}）")
    print(f"收盘面板: {p['close'].shape} · {p['close'].index[0].date()} ~ {p['close'].index[-1].date()}")
    print(f"成交面板: {p['volume'].shape} · 缺失列 {[c for c in CODES if c not in p['volume']]}")


if __name__ == "__main__":
    _main()
