# -*- coding: utf-8 -*-
"""稳健资产 · 数据层（场外净值抓取 + 当日缓存）。

数据源
  场外基金 : ``xalpha.fundinfo(code).price``  → ``totvalue``（累计净值，分红再投复权）
  货币基金 : ``xalpha.mfundinfo(code).price``  → ``totvalue``（万份收益复利，等价净值指数）
             —— ``fundinfo`` 对货基会抛 ``FundTypeError``，必须走 mfundinfo。

缓存
  ``strategies/bond_cash/data/_nav_cache.json``（**当日有效**，网络失败自动回退旧缓存）
  结构与 ``strategies/permanent/data/_nav_cache.json`` 同构：
  ``{"date": "YYYY-MM-DD", "series": {code: {"dates": [...], "tot": [...], "unit": [...]}}}``

为什么一律用累计净值（totvalue）
  债券基金大量分红/份额折算。用单位净值算收益会把每次分红当成一次下跌，
  动量符号（本策略唯一的开关）会被系统性做反。文档 §8 已注明：
  ``003377``（#606）的 ``cumprod(1+日增长率)=1.4728`` 与其累计净值涨幅 1.4550 一致，
  而单位净值涨幅只有 1.3390 —— 差 11pp，足以让「动量转负」误报。

为什么场内 ETF 不能当主口径
  ``511090``/``511260`` 等场内债 ETF 的**行情涨跌幅不含分红**（511090 分红约 2.4pp/年），
  用它算年化会低估约 2.4pp/年。本模块对场内代码也走 ``fundinfo`` 的净值通道
  （基金净值日增长率含分红再投），只在 ``note`` 里标明「场内壳」。
"""
import json
import os
import sys
from datetime import date

import pandas as pd

_HERE = os.path.dirname(os.path.abspath(__file__))          # strategies/bond_cash
_ROOT = os.path.dirname(os.path.dirname(_HERE))             # 仓库根
for _p in (_ROOT, _HERE):
    if _p not in sys.path:
        sys.path.insert(0, _p)

import rule  # noqa: E402  (本目录 rule.py；各策略各自独立进程运行)

_DIR = os.path.join(_HERE, "data")
_CACHE = os.path.join(_DIR, "_nav_cache.json")
UST10Y = os.path.join(_ROOT, "data", "_bt_caches", "ust10y_m.csv")


# ---------------------------------------------------------------- 缓存读写
def _read_cache():
    try:
        with open(_CACHE, encoding="utf-8") as f:
            return json.load(f)
    except (OSError, ValueError):
        return {"date": "", "series": {}}


def _write_cache(obj):
    os.makedirs(_DIR, exist_ok=True)
    with open(_CACHE, "w", encoding="utf-8") as f:
        json.dump(obj, f, ensure_ascii=False)


def _to_series(node):
    s = pd.Series(node.get("tot") or node["nav"], index=pd.to_datetime(node["dates"]),
                  dtype="float64")
    return s.sort_index()


# ---------------------------------------------------------------- 单只净值
def _fetch(code):
    """-> (totvalue Series, 单位净值 Series)。货基走 mfundinfo。"""
    import xalpha as xa

    try:
        p = xa.fundinfo(code).price.sort_values("date")
    except Exception:  # noqa: BLE001 - 货基/mfund 会抛 FundTypeError，换通道重试
        p = xa.mfundinfo(code).price.sort_values("date")

    if "totvalue" in p.columns:
        tot = p["totvalue"].fillna(p["netvalue"])
    else:
        tot = p["netvalue"]
    unit = p["netvalue"].fillna(tot) if "netvalue" in p.columns else tot
    df = p.assign(_tot=tot, _unit=unit).dropna(subset=["_tot"])
    if df.empty:
        raise ValueError(f"{code} 净值序列为空")
    return df


def load_nav(code, force=False, allow_stale=True):
    """取单只场外基金的累计净值 Series（升序 DatetimeIndex）。"""
    today = date.today().isoformat()
    cache = _read_cache()
    node = (cache.get("series") or {}).get(code)

    if not force and node and cache.get("date") == today:
        return _to_series(node)

    try:
        df = _fetch(code)
        dates = [str(d)[:10] for d in pd.to_datetime(df["date"])]
        cache.setdefault("series", {})[code] = {
            "dates": dates,
            "tot": [round(float(x), 6) for x in df["_tot"]],
            "unit": [round(float(x), 6) for x in df["_unit"]],
        }
        cache["date"] = today
        _write_cache(cache)
        return _to_series(cache["series"][code])
    except Exception as exc:  # noqa: BLE001 - 单只失败不阻断全池
        if allow_stale and node:
            print(f"[warn] {code} 抓取失败({type(exc).__name__}), "
                  f"沿用缓存(最新 {node['dates'][-1]})")
            return _to_series(node)
        raise


def load_many(codes, force=False):
    """-> ({code: Series}, {code: 截止日})。逐只容错，缺数据的不出现在结果里。"""
    out, asof, failed = {}, {}, []
    for c in codes:
        try:
            s = load_nav(c, force=force)
        except Exception as exc:  # noqa: BLE001
            print(f"[warn] {c} 无可用净值: {type(exc).__name__}: {exc}")
            failed.append(c)
            continue
        out[c] = s
        asof[c] = str(s.index[-1].date())
    return out, asof


def load_pool(force=False):
    """按 ``rule.ORDER`` 取全谱系（11 债 + 4 现金）。"""
    return load_many(rule.ORDER, force=force)


def unit_latest(code):
    """-> (单位净值, 截止日)：申赎报价口径，无缓存返回 (None, None)。"""
    node = (_read_cache().get("series") or {}).get(code)
    if not node:
        return None, None
    unit = node.get("unit") or node.get("tot")
    return (unit[-1], node["dates"][-1]) if unit else (None, None)


# ---------------------------------------------------------------- 美债（卫星条件）
def load_ust10y():
    """10Y 美债收益率月频（%）-> Series。缺失返回空 Series（卫星自动不成立）。"""
    if not os.path.exists(UST10Y):
        return pd.Series(dtype="float64")
    df = pd.read_csv(UST10Y, index_col=0, parse_dates=True)
    if "close" not in df.columns:
        return pd.Series(dtype="float64")
    return df["close"].astype(float).sort_index()


def ust_stats(win_years=10):
    """-> dict(pct 分位, chg3m 近 3 月收益率变化(pp), last 最新值, date 数据日)。

    分位 = 最新值在过去 ``win_years`` 年窗口内的累计分位（0~100，越高＝收益率越高＝越便宜）。
    """
    s = load_ust10y()
    if s.empty:
        return {"ok": False, "note": "缺 data/_bt_caches/ust10y_m.csv"}
    cut = s.index[-1] - pd.DateOffset(years=win_years)
    w = s[s.index >= cut]
    last = float(s.iloc[-1])
    pct = float((w <= last).mean() * 100.0)
    chg3m = float(s.iloc[-1] - s.iloc[-4]) if len(s) >= 4 else None
    return {"ok": True, "pct": round(pct, 1), "chg3m": None if chg3m is None else round(chg3m, 3),
            "last": round(last, 3), "date": str(s.index[-1].date()), "n": int(len(w))}
