# -*- coding: utf-8 -*-
"""V9 · 数据层：10Y 国债收益率 + 场外基金累计净值（含分红再投资）。

- 10Y：本策略自存 `data/_bond10y_long.csv`（2002 起，东财日频）；
  缺文件时只读 v4_erp_band 的 25 年缓存 / 共享 12 年缓存兜底。**不写共享缓存**。
- 基金：本策略 `data/_nav_cache.json` 当日缓存；首次缺时回退读家族 v0 的
  `../data/_nav_cache.json`（同一套 totvalue 口径），再缺才联网抓。失败一律沿用旧缓存并告警。
"""
import json
import os
import sys
from datetime import date, timedelta

import pandas as pd

_HERE = os.path.dirname(os.path.abspath(__file__))
for _p in (os.path.dirname(_HERE), _HERE):
    while _p in sys.path:
        sys.path.remove(_p)
    sys.path.insert(0, _p)

import rule  # noqa: E402

_DIR = os.path.join(_HERE, "data")
_CACHE = os.path.join(_DIR, "_nav_cache.json")
_BOND_URL = "https://datacenter.eastmoney.com/api/data/get"
_BOND_TOKEN = "894050c76af8597a853f5b408b759f5d"
_BOND_YEARS = 25
SOURCE = {}

EXTRA = ["000032"]          # 中债综合财富指数代理要用的信用纯债腿


# ---------------------------------------------------------------- 净值
def _read_cache(path):
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    except (OSError, ValueError):
        return {"date": "", "series": {}}


def _series(node):
    return pd.Series(node["tot"], index=pd.to_datetime(node["dates"]), dtype="float64").sort_index()


def _write_cache(obj):
    os.makedirs(_DIR, exist_ok=True)
    with open(_CACHE, "w", encoding="utf-8") as f:
        json.dump(obj, f, ensure_ascii=False)


def load_nav(code, force=False):
    today = date.today().isoformat()
    cache = _read_cache(_CACHE)
    node = (cache.get("series") or {}).get(code)
    if not force and node and cache.get("date") == today:
        return _series(node)

    family = _read_cache(rule.CFG["family_nav_cache"]).get("series", {}).get(code)
    try:
        import xalpha as xa

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
        _write_cache(cache)
        return _series(cache["series"][code])
    except Exception as exc:  # noqa: BLE001
        for src, nd in (("本策略缓存", node), ("家族 v0 缓存", family)):
            if nd:
                print(f"[warn] {code} 抓取失败({type(exc).__name__})，沿用{src} {nd['dates'][-1]}")
                if not node:
                    cache.setdefault("series", {})[code] = nd
                    _write_cache(cache)
                return _series(nd)
        raise


def load_all(force=False):
    codes = rule.CFG["universe"] + EXTRA
    navs, asof, failed = {}, {}, []
    for c in codes:
        try:
            navs[c] = load_nav(c, force=force)
            asof[c] = str(navs[c].index[-1].date())
        except Exception as exc:  # noqa: BLE001
            print(f"[warn] {c} 无净值: {type(exc).__name__}: {exc}")
            failed.append(c)
    return navs, asof, failed


# ---------------------------------------------------------------- 10Y 收益率
def _read_yield_csv(path):
    df = pd.read_csv(path)
    col = "close" if "close" in df.columns else df.columns[-1]
    dcol = "date" if "date" in df.columns else df.columns[0]
    s = pd.Series(pd.to_numeric(df[col], errors="coerce").to_numpy(),
                  index=pd.DatetimeIndex(pd.to_datetime(df[dcol]))).dropna().sort_index()
    return s.rename("close").rename_axis("date")


def fetch_bond10y(years=_BOND_YEARS):
    """东财中国 10Y 国债到期收益率（日频，%），失败抛异常（由 load_yield 兜底）。"""
    import requests
    import urllib3

    urllib3.disable_warnings()
    need_from = (date.today() - timedelta(days=365 * years)).isoformat()
    out, page = {}, 1
    while page <= 30:
        r = requests.get(
            _BOND_URL,
            params={"type": "RPTA_WEB_TREASURYYIELD", "sty": "ALL", "st": "SOLAR_DATE",
                    "sr": "-1", "token": _BOND_TOKEN, "p": str(page), "ps": "500"},
            headers={"User-Agent": "Mozilla/5.0", "Referer": "https://data.eastmoney.com/"},
            timeout=20, verify=False)
        r.raise_for_status()
        rows = ((r.json() or {}).get("result") or {}).get("data") or []
        if not rows:
            break
        for row in rows:
            dt = str(row.get("SOLAR_DATE") or "")[:10]
            v = row.get("EMM00166466")
            if dt and v not in (None, ""):
                try:
                    out[dt] = float(v)
                except (TypeError, ValueError):
                    continue
        if (out and min(out) <= need_from) or len(rows) < 500:
            break
        page += 1
    if not out:
        raise RuntimeError("东财 10Y 国债收益率返回空")
    return _read_yield_csv(pd.DataFrame({"date": list(out), "close": list(out.values())}))


def load_yield(refresh=False):
    """-> 日频 10Y 收益率 Series（%）；优先自存 25 年缓存，缺则只读备用缓存。"""
    path = rule.CFG["yield_csv"]
    if refresh:
        try:
            fetched = fetch_bond10y()
            old = _read_yield_csv(path) if os.path.exists(path) else pd.Series(dtype="float64", name="close")
            merged = pd.concat([old, fetched]).sort_index()
            merged = merged[~merged.index.duplicated(keep="last")]
            os.makedirs(_DIR, exist_ok=True)
            merged.rename_axis("date").to_csv(path, header=["close"])
            SOURCE["yield"] = f"东财实时抓取（--refresh）· {merged.index[0]:%Y-%m-%d}~{merged.index[-1]:%Y-%m-%d}"
        except Exception as exc:  # noqa: BLE001
            print(f"[warn] 10Y 抓取失败({type(exc).__name__}: {exc})，沿用本地缓存")
    if os.path.exists(path):
        s = _read_yield_csv(path)
        SOURCE.setdefault("yield", f"本策略缓存 `strategies/bond_cash/v9_yield_band/data/_bond10y_long.csv`"
                                   f" · {s.index[0]:%Y-%m-%d}~{s.index[-1]:%Y-%m-%d}")
        return s
    for fb in rule.CFG["yield_fallback"]:
        if os.path.exists(fb):
            s = _read_yield_csv(fb)
            SOURCE["yield"] = f"只读备用缓存 `{os.path.relpath(fb, rule._ROOT).replace(os.sep, '/')}`" \
                              f" · {s.index[0]:%Y-%m-%d}~{s.index[-1]:%Y-%m-%d}"
            return s
    raise RuntimeError("10Y 国债收益率：本策略缓存与备用缓存均不存在（可跑 --refresh 抓取）")


def load_hs300_tr():
    """沪深300 全收益指数（价格口径，已含分红再投资）-> Series。"""
    path = rule.CFG["hs300_tr_csv"]
    if not os.path.exists(path):
        print(f"[warn] 缺沪深300全收益缓存 {path}")
        return pd.Series(dtype="float64")
    s = _read_yield_csv(path)
    SOURCE.setdefault("hs300_tr", f"共享缓存 `data/_bt_caches/csi_H00300.csv` · 至 {s.index[-1]:%Y-%m-%d}")
    return s
