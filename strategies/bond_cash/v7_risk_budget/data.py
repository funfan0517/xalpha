# -*- coding: utf-8 -*-
"""V7 · 数据层：四类腿的场外累计净值（利率 2 只 + 信用 + 转债 + 现金 2 只）。

转债腿用**场外可转债基金净值**而不是场内 511380 行情：本家族一律用累计净值算收益
（场内行情不含分红、含份额折算，v0 §5-8 有实测教训），转债基金净值口径和其余腿一致，
协方差才有可比性。
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
        except Exception:  # noqa: BLE001 - 货基走 mfundinfo
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
