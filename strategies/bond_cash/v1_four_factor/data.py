# -*- coding: utf-8 -*-
"""V1 · 数据层（收益率曲线 / SHIBOR / 基准 ETF / 腿净值），全部本地缓存。

| 数据 | 源 | 缓存 |
|---|---|---|
| 10Y 国债收益率（日） | ``data/_bt_caches/bond10y_d.csv``（仓库既有，2013 起） | 只读 |
| AAA 中短票 3Y / 国债 1Y、3Y、10Y | akshare ``bond_china_yield``（分段抓，每次 ≤300 天） | ``data/_curve.csv`` |
| SHIBOR-1W（日） | akshare ``macro_china_shibor_all`` | ``data/_shibor_1w.csv`` |
| 基准 ETF ``511260`` 与四只腿的累计净值 | ``xalpha.fundinfo``（货基走 ``mfundinfo``） | ``data/_nav_cache.json`` |

**为什么 v1 自己一份缓存、不复用 v0 的**：v0 的缓存服务于「15 只债现全谱系」，
v1 只需要基准 + 四条腿，且要额外的曲线/SHIBOR 文件；两者混在一个文件里会让
「v1 抓失败」污染 v0 的当日缓存（v0 的卡片是收盘口径、不能因为 v1 的 akshare 抖动降级）。

所有取数失败都**保留旧缓存并打印 warn**，由 ``engine`` 按覆盖度降级（R-11），
绝不静默用默认值顶替 —— 温度缺项会直接体现在卡片上。
"""
import json
import os
import sys
from datetime import date, datetime, timedelta

import pandas as pd

_HERE = os.path.dirname(os.path.abspath(__file__))          # strategies/bond_cash/v1_four_factor
_PKG = os.path.dirname(_HERE)                               # strategies/bond_cash
_ROOT = os.path.dirname(os.path.dirname(_PKG))              # 仓库根
for _p in (_ROOT, _PKG, _HERE):
    while _p in sys.path:
        sys.path.remove(_p)
    sys.path.insert(0, _p)

import rule  # noqa: E402  (本目录 rule.py：所在目录已置 sys.path[0])

_DIR = os.path.join(_HERE, "data")
_NAV_CACHE = os.path.join(_DIR, "_nav_cache.json")
_CURVE = os.path.join(_DIR, "_curve.csv")
_SHIBOR = os.path.join(_DIR, "_shibor_1w.csv")
Y10 = os.path.join(_ROOT, "data", "_bt_caches", "bond10y_d.csv")

CURVE_START = "2022-06-01"          # 分位窗口 3 年 + 缓冲
_REFRESH_DAYS = 5                   # 缓存末端落在这个天数内就当新鲜


def _today():
    return date.today()


def _fresh(idx, days=_REFRESH_DAYS):
    return len(idx) > 0 and (pd.Timestamp(_today()) - pd.Timestamp(idx[-1])).days <= days


# ---------------------------------------------------------------- 净值（基准 + 腿）
def _read_json(path, default):
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    except (OSError, ValueError):
        return default


def _write_json(path, obj):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(obj, f, ensure_ascii=False)


def _series(node):
    return pd.Series(node["tot"], index=pd.to_datetime(node["dates"]), dtype="float64").sort_index()


def load_nav(code, force=False):
    """场外/场内 ETF 的累计净值（含分红再投）。货基自动走 mfundinfo。"""
    import xalpha as xa

    today = _today().isoformat()
    cache = _read_json(_NAV_CACHE, {"date": "", "series": {}})
    node = (cache.get("series") or {}).get(code)
    if not force and node and cache.get("date") == today:
        return _series(node)

    try:
        try:
            p = xa.fundinfo(code).price.sort_values("date")
        except Exception:  # noqa: BLE001 - 货基会抛 FundTypeError
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
        _write_json(_NAV_CACHE, cache)
        return _series(cache["series"][code])
    except Exception as exc:  # noqa: BLE001
        if node:
            print(f"[warn] {code} 抓取失败({type(exc).__name__})，沿用缓存 {node['dates'][-1]}")
            return _series(node)
        raise


def load_navs(codes=None, force=False):
    """-> ({code: Series}, 失败列表)。"""
    codes = codes or [rule.CFG["bench"], rule.CFG["long_leg"], rule.CFG["base_leg"],
                      *rule.CFG["cash"]]
    out, failed = {}, []
    for c in codes:
        try:
            out[c] = load_nav(c, force=force)
        except Exception as exc:  # noqa: BLE001
            print(f"[warn] {c} 无可用净值: {type(exc).__name__}: {exc}")
            failed.append(c)
    return out, failed


# ---------------------------------------------------------------- 10Y 国债（仓库既有）
def load_y10():
    if not os.path.exists(Y10):
        return pd.Series(dtype="float64")
    df = pd.read_csv(Y10, index_col=0, parse_dates=True)
    return df["close"].astype(float).sort_index()


# ---------------------------------------------------------------- SHIBOR-1W
def _fetch_shibor():
    import akshare as ak

    d = ak.macro_china_shibor_all()
    d = d[["日期", "1W-定价"]].copy()
    d.columns = ["date", "shibor_1w"]
    d["date"] = pd.to_datetime(d["date"])
    d["shibor_1w"] = pd.to_numeric(d["shibor_1w"], errors="coerce")
    return d.dropna().set_index("date").sort_index()


def load_shibor(force=False):
    old = pd.read_csv(_SHIBOR, index_col=0, parse_dates=True) if os.path.exists(_SHIBOR) else None
    if old is not None and _fresh(old.index) and not force:
        return old["shibor_1w"].astype(float)
    try:
        new = _fetch_shibor()
        if old is not None:
            new = pd.concat([old, new[~new.index.isin(old.index)]]).sort_index()
        os.makedirs(_DIR, exist_ok=True)
        new.to_csv(_SHIBOR)
        return new["shibor_1w"].astype(float)
    except Exception as exc:  # noqa: BLE001
        if old is not None:
            print(f"[warn] SHIBOR 抓取失败({type(exc).__name__})，沿用缓存 {old.index[-1].date()}")
            return old["shibor_1w"].astype(float)
        print(f"[warn] SHIBOR 不可用: {type(exc).__name__}: {exc}")
        return pd.Series(dtype="float64")


# ---------------------------------------------------------------- 中债收益率曲线
_CURVE_COLS = {"中债国债收益率曲线": ("cgb_1y", "cgb_3y", "cgb_10y"),
               "中债中短期票据收益率曲线(AAA)": ("aaa_1y", "aaa_3y", "aaa_10y")}


def _fetch_curve(start, end):
    """akshare 单次跨度必须 <1 年，这里按 300 天切。"""
    import akshare as ak

    frames = []
    s = pd.Timestamp(start)
    e = pd.Timestamp(end)
    while s <= e:
        chunk_end = min(s + timedelta(days=300), e)
        try:
            d = ak.bond_china_yield(start_date=s.strftime("%Y%m%d"),
                                    end_date=chunk_end.strftime("%Y%m%d"))
        except Exception as exc:  # noqa: BLE001
            print(f"[warn] 曲线 {s.date()}~{chunk_end.date()} 抓取失败: {type(exc).__name__}")
            s = chunk_end + timedelta(days=1)
            continue
        for name, cols in _CURVE_COLS.items():
            sub = d[d["曲线名称"] == name]
            if sub.empty:
                continue
            out = sub[["日期", "1年", "3年", "10年"]].copy()
            out.columns = ["date", *cols]
            frames.append(out)
        s = chunk_end + timedelta(days=1)
    if not frames:
        return pd.DataFrame()
    df = pd.concat(frames).dropna(subset=["date"])
    df["date"] = pd.to_datetime(df["date"])
    for c in ["cgb_1y", "cgb_3y", "cgb_10y", "aaa_1y", "aaa_3y", "aaa_10y"]:
        if c not in df.columns:
            df[c] = float("nan")
    return df.groupby("date").first().sort_index()


def load_curve(force=False):
    """-> DataFrame(index=date, cgb_1y/cgb_3y/cgb_10y/aaa_3y)，至少含 cgb_1y 与 aaa_3y。"""
    old = pd.read_csv(_CURVE, index_col=0, parse_dates=True) if os.path.exists(_CURVE) else None
    if old is not None and _fresh(old.index) and not force:
        return old
    start = (old.index[-1] - timedelta(days=10)).strftime("%Y-%m-%d") if old is not None else CURVE_START
    try:
        new = _fetch_curve(start, _today().isoformat())
        if new.empty:
            raise ValueError("曲线抓取为空")
        if old is not None:
            new = pd.concat([old, new[~new.index.isin(old.index)]]).sort_index()
        os.makedirs(_DIR, exist_ok=True)
        new.to_csv(_CURVE)
        return new
    except Exception as exc:  # noqa: BLE001
        if old is not None:
            print(f"[warn] 曲线抓取失败({type(exc).__name__})，沿用缓存 {old.index[-1].date()}")
            return old
        print(f"[warn] 曲线不可用: {type(exc).__name__}: {exc}")
        return pd.DataFrame()


# ---------------------------------------------------------------- 组合入口
def panel(force=False):
    """-> dict(navs, curve, shibor, y10, failed)。一次把 v1 需要的东西全取齐。"""
    navs, failed = load_navs(force=force)
    return dict(
        navs=navs,
        curve=load_curve(force=force),
        shibor=load_shibor(force=force),
        y10=load_y10(),
        failed=failed,
    )


def data_asof(p):
    """各数据源的截止日（卡面「数据」行用）。"""
    out = {}
    if len(p["y10"]):
        out["10Y国债"] = str(p["y10"].index[-1].date())
    if len(p["shibor"]):
        out["SHIBOR"] = str(p["shibor"].index[-1].date())
    c = p["curve"]
    if len(c):
        out["收益率曲线"] = str(c.index[-1].date())
    if p["navs"]:
        out["净值"] = max(str(s.index[-1].date()) for s in p["navs"].values())
    return out
