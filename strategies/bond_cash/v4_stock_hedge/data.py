# -*- coding: utf-8 -*-
"""V4 · 数据层：只读 v1 的曲线缓存 + 仓库既有沪深300 日线；腿净值独立当日缓存。

三份数据都是**只读**（v4 不写任何共享缓存），所以 v4 挂掉不会污染 v0/v1/v3 的口径。
"""
import json
import os
import sys
from datetime import date

import pandas as pd

_HERE = os.path.dirname(os.path.abspath(__file__))
for _p in (os.path.dirname(_HERE), _HERE):
    while _p in sys.path:
        sys.path.remove(_p)
    sys.path.insert(0, _p)

import rule  # noqa: E402

_DIR = os.path.join(_HERE, "data")
_CACHE = os.path.join(_DIR, "_nav_cache.json")


def _read():
    try:
        with open(_CACHE, encoding="utf-8") as f:
            return json.load(f)
    except (OSError, ValueError):
        return {"date": "", "series": {}}


def _write(obj):
    os.makedirs(_DIR, exist_ok=True)
    with open(_CACHE, "w", encoding="utf-8") as f:
        json.dump(obj, f, ensure_ascii=False)


def _series(node):
    return pd.Series(node["tot"], index=pd.to_datetime(node["dates"]), dtype="float64").sort_index()


def load_nav(code, force=False):
    import xalpha as xa

    today = date.today().isoformat()
    cache = _read()
    node = (cache.get("series") or {}).get(code)
    if not force and node and cache.get("date") == today:
        return _series(node)
    try:
        try:
            p = xa.fundinfo(code).price.sort_values("date")
        except Exception:  # noqa: BLE001
            p = xa.mfundinfo(code).price.sort_values("date")
        tot = p["totvalue"] if "totvalue" in p.columns else p["netvalue"]
        df = p.assign(_tot=tot.fillna(p["netvalue"])).dropna(subset=["_tot"])
        if df.empty:
            raise ValueError("净值序列为空")
        cache.setdefault("series", {})[code] = {
            "dates": [str(d)[:10] for d in pd.to_datetime(df["date"])],
            "tot": [round(float(x), 6) for x in df["_tot"]],
        }
        cache["date"] = today
        _write(cache)
        return _series(cache["series"][code])
    except Exception as exc:  # noqa: BLE001
        if node:
            print(f"[warn] {code} 抓取失败({type(exc).__name__})，沿用缓存 {node['dates'][-1]}")
            return _series(node)
        raise


def load_all(force=False):
    codes = rule.CFG["universe"] + list(rule.CFG["cash"])
    navs, asof, failed = {}, {}, []
    for c in codes:
        try:
            navs[c] = load_nav(c, force=force)
            asof[c] = str(navs[c].index[-1].date())
        except Exception as exc:  # noqa: BLE001
            print(f"[warn] {c} 无净值: {type(exc).__name__}: {exc}")
            failed.append(c)
    return navs, asof, failed


def load_curve():
    """只读 v1 的曲线缓存；缺失返回空 DataFrame（由 engine 降级为中性）。"""
    path = rule.CFG["curve_csv"]
    if not os.path.exists(path):
        print(f"[warn] 缺曲线缓存 {path}（先跑 v1 daily）")
        return pd.DataFrame()
    return pd.read_csv(path, index_col=0, parse_dates=True)


def load_stock():
    """沪深300 收盘（指数点位，用动量分位，不需要分红口径）。"""
    path = rule.CFG["stock_csv"]
    if not os.path.exists(path):
        print(f"[warn] 缺沪深300 日线 {path} -> Hedge 票降级为 0")
        return pd.Series(dtype="float64")
    df = pd.read_csv(path, index_col=0, parse_dates=True)
    return df["close"].astype(float).dropna().sort_index()
