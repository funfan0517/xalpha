# -*- coding: utf-8 -*-
"""永久组合 · 数据层(场外净值抓取 + 当日缓存)。

数据源: xa.fundinfo(code).price  (天天基金, 场外真实执行口径)
缓存  : strategies/permanent/data/_nav_cache.json  (当日有效, 失败自动回退)

为什么用场外净值而不是场内 ETF 做信号:
  本策略实际申赎走场外(按净值成交), 场内 ETF 含溢价/折价噪声, 用它算动量会把
  溢价波动当成趋势。场外净值虽滞后 1 个交易日(含 QDII 更久), 但口径与执行一致。
"""
import json
import os
import sys
from datetime import date

import pandas as pd

_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

import xalpha as xa  # noqa: E402

_DIR = os.path.dirname(os.path.abspath(__file__))
_CACHE_DIR = os.path.join(_DIR, "data")
_CACHE = os.path.join(_CACHE_DIR, "_nav_cache.json")


def _read_cache():
    try:
        with open(_CACHE, encoding="utf-8") as f:
            return json.load(f)
    except (OSError, ValueError):
        return {"date": "", "series": {}}


def _write_cache(obj):
    os.makedirs(_CACHE_DIR, exist_ok=True)
    with open(_CACHE, "w", encoding="utf-8") as f:
        json.dump(obj, f, ensure_ascii=False)


def _to_series(node):
    s = pd.Series(node.get("tot") or node["nav"], index=pd.to_datetime(node["dates"]),
                  dtype="float64")
    return s.sort_index()


def load_nav(code, force=False, allow_stale=True):
    """取单只场外**累计净值**(totvalue, 分红再投复权) -> pandas.Series。

    为什么用 totvalue 而不是单位净值:
      008163(红利低波)2020-01 至今累计 +78.3% 而单位净值只 +7.2% —— 分红会让单位净值
      跳跌。用未复权净值算收益/动量, 每次分红都被当成一次下跌, 会持续误判「跌破均线」,
      并把真实年化从 8.6% 压到 1.3%。这是必须复权的硬理由。
      申赎报价仍看单位净值, 由 load_unit_latest() 单独给出。

    force=True 强制走网络; allow_stale=True 时网络失败回退到旧缓存(可能滞后)。
    """
    today = date.today().isoformat()
    cache = _read_cache()
    node = cache.get("series", {}).get(code)

    if not force and node and cache.get("date") == today:
        return _to_series(node)

    try:
        f = xa.fundinfo(code)
        p = f.price.sort_values("date")
        if "totvalue" in p.columns:
            tot = p["totvalue"].fillna(p["netvalue"])
        else:
            tot = p["netvalue"]
        df = p.assign(_tot=tot).dropna(subset=["_tot"])
        if df.empty:
            raise ValueError(f"{code} 净值序列为空")
        dates = [str(d)[:10] for d in pd.to_datetime(df["date"])]
        cache.setdefault("series", {})[code] = {
            "dates": dates,
            "tot": [round(float(x), 6) for x in df["_tot"]],
            "unit": [round(float(x), 6) for x in df["netvalue"].fillna(df["_tot"])],
        }
        # 只要有一只当日成功抓取, 就把缓存日期推到今天(逐只重试会各自更新)
        cache["date"] = today
        _write_cache(cache)
        return _to_series(cache["series"][code])
    except Exception:
        if allow_stale and node:
            print(f"[warn] {code} 抓取失败, 沿用缓存(最新 {node['dates'][-1]})")
            return _to_series(node)
        raise


def load_unit_latest(code):
    """-> (单位净值, 截止日) —— 申赎报价口径; 无缓存返回 (None, None)。"""
    node = _read_cache().get("series", {}).get(code)
    if not node:
        return None, None
    unit = node.get("unit") or node.get("nav") or node.get("tot")
    return (unit[-1], node["dates"][-1]) if unit else (None, None)


def load_many(codes, force=False):
    """-> {code: Series}, 以及 {code: 数据截止日 str}。"""
    out, asof = {}, {}
    for c in codes:
        s = load_nav(c, force=force)
        out[c] = s
        asof[c] = str(s.index[-1].date())
    return out, asof


def load_universe(force=False):
    """按 rule.LEGS 取全部有净值代码的腿 -> ({leg: Series}, {leg: 截止日})。"""
    import rule
    legs = [l for l in rule.LEG_ORDER if rule.LEGS[l]["code"]]
    ser, asof = load_many([rule.LEGS[l]["code"] for l in legs], force=force)
    out = {}
    for leg in legs:
        out[leg] = ser[rule.LEGS[leg]["code"]]
    return out, {leg: asof[rule.LEGS[leg]["code"]] for leg in legs}
