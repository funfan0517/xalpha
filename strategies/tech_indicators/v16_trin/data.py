# -*- coding: utf-8 -*-
"""TRIN 策略 · 数据装载（广度库 + 执行标的行情）。

两类数据，来源与落位不同：

1. **全市场广度库**（跨策略数据类）→ `data/_breadth_daily.json`
   涨跌家数 / 涨跌成交额，由 `data/_refresh_breadth.py` 回补与每日维护（本策略只读 + 触发增量）。
   这是 TRIN 的唯一输入；缺了它任何信号都算不出来，所以缺失时直接报错而不是静默降级。

2. **执行标的 OHLC**（本策略自己的行情缓存）→ `v16_trin/data/_v16_vehicle.json`
   默认 510300（沪深300ETF）。视频要求「次日开盘买入」，共享十年库只有收盘价，
   故本策略自取东财**前复权**日线（open/close/volume/amount 一次取全，单次请求即可）。

池子口径（可选）复用家族十年库：`data/_long_klines.json`（收盘）+ `data/_indicators_hist.json`（成交量），
标的池唯一入口是 `../_pool.py`（`data/_universe.md` 前四类），与 v9/v10 同池。
"""
import datetime as dt
import json
import os
import sys

import pandas as pd
import requests

_HERE = os.path.dirname(os.path.abspath(__file__))                    # v16_trin/
_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(_HERE)))      # 仓库根
_DATA = os.path.join(_ROOT, "data")
_PARENT = os.path.dirname(_HERE)                                      # strategies/tech_indicators
_STRAT = os.path.dirname(_PARENT)                                     # strategies/
for _p in (_ROOT, _DATA, _PARENT, _HERE, _STRAT):
    if _p not in sys.path:
        sys.path.insert(0, _p)

import _pool  # noqa: E402  家族唯一池定义
import _panel  # noqa: E402  面板口径唯一实现（与 v9_right_side 共用）
import rule  # noqa: E402

BREADTH = os.path.join(_DATA, "_breadth_daily.json")
LK = os.path.join(_DATA, "_long_klines.json")
IND = os.path.join(_DATA, "_indicators_hist.json")
VEH_CACHE = os.path.join(_HERE, "data", "_v16_vehicle.json")

API_KLINE = "https://push2his.eastmoney.com/api/qt/stock/kline/get"
UT = "bd1d9ddb04089700cf9c27f6f7426281"
POOL = _pool.rows()
CODES = [r["code"] for r in POOL]
NAMES = {r["code"]: r["inner_name"] for r in POOL}


def _session():
    """直连会话：绕过系统代理（东财为国内站点，走代理常被断开）。"""
    s = requests.Session()
    s.trust_env = False
    s.headers.update({"User-Agent": "Mozilla/5.0"})
    return s


_S = _session()


def _secid(code):
    """代码 -> 东财 secid。

    显式写 `1.000300` 这类带市场前缀的形式时直接透传 —— 上证/中证**指数**（000300 / 000510 /
    000688）以 000 开头，按 ETF 的后缀规则会被误判成深市个股，必须允许显式指定。
    """
    if "." in code:
        return code
    return ("1." if code.startswith(("5", "6", "9")) else "0.") + code


# ------------------------------------------------------------------ 广度库
def load_breadth(refresh=True):
    """-> DataFrame(index=date, adv/dec/flat/adv_vol/dec_vol/adv_amt/dec_amt)。"""
    if refresh:
        try:
            import _refresh_breadth as rb
            rb.update_today(verbose=False)
        except Exception as e:  # noqa: BLE001 - 增量失败不阻断只读回测
            print(f"[广度] 增量更新失败，沿用本地库：{type(e).__name__}: {e}")
    if not os.path.exists(BREADTH):
        raise FileNotFoundError(
            f"缺全市场广度库 {BREADTH} —— 先跑 python data/_refresh_breadth.py --full")
    import _refresh_breadth as rb
    df = rb.load()
    if df.empty:
        raise RuntimeError("广度库为空，先跑 python data/_refresh_breadth.py --full")
    return df


# ------------------------------------------------------------------ 执行标的
def fetch_vehicle(code=rule.VEHICLE, start="20140101", end="20500101"):
    """东财前复权日线 -> DataFrame(index=date, open/close/volume/amount)。"""
    d = _S.get(API_KLINE, params={"secid": _secid(code),
                                  "fields1": "f1,f2,f3,f4,f5,f6",
                                  "fields2": "f51,f52,f53,f54,f55,f56,f57",
                                  "klt": 101, "fqt": 1, "beg": start, "end": end,
                                  "lmt": 10000}, timeout=20).json()
    data = (d or {}).get("data") or {}
    rows = []
    for line in data.get("klines") or []:
        p = line.split(",")
        try:
            rows.append((p[0], float(p[1]), float(p[2]), float(p[5]), float(p[6])))
        except (IndexError, ValueError):
            continue
    df = pd.DataFrame(rows, columns=["date", "open", "close", "volume", "amount"])
    return df.set_index(pd.to_datetime(df.pop("date"))).sort_index()


def load_vehicle(code=rule.VEHICLE, refresh=True):
    """执行标的 OHLC；缓存策略目录 data/ 下，命中缓存且已最新时不联网。"""
    cache = {}
    if os.path.exists(VEH_CACHE):
        try:
            cache = json.load(open(VEH_CACHE, encoding="utf-8"))
        except json.JSONDecodeError:
            cache = {}
    hit = cache.get(code)
    if hit and not refresh:
        return _vehicle_frame(hit)
    if hit:
        last = pd.Timestamp(hit["dates"][-1]).date()
        if last >= dt.date.today() or (last.weekday() >= 5 and (dt.date.today() - last).days <= 2):
            return _vehicle_frame(hit)
    df = fetch_vehicle(code)
    cache[code] = {"dates": [d.strftime("%Y-%m-%d") for d in df.index],
                   "open": [round(float(x), 4) for x in df["open"]],
                   "close": [round(float(x), 4) for x in df["close"]],
                   "volume": [round(float(x), 0) for x in df["volume"]],
                   "amount": [round(float(x), 0) for x in df["amount"]]}
    os.makedirs(os.path.dirname(VEH_CACHE), exist_ok=True)
    json.dump(cache, open(VEH_CACHE, "w", encoding="utf-8"), ensure_ascii=False)
    return df


def _vehicle_frame(hit):
    idx = pd.to_datetime(hit["dates"])
    return pd.DataFrame({"open": hit["open"], "close": hit["close"],
                         "volume": hit["volume"], "amount": hit["amount"]}, index=idx)


# ------------------------------------------------------------------ 池子（收盘口径）
def load_pool(refresh=True):
    """-> {close, volume} 面板（index=日期, columns=池内代码），取自共享十年库。

    口径实现在 `strategies/_panel.py::load_pair`（v9/v16 共用的唯一实现）：
    每只标的保留自身交易日（不 ffill），volume 与 dates 长度不一致时**直接抛错**，
    不再静默丢列（静默丢列会让池子悄悄变小、回测数字变了没人知道）。
    """
    return _panel.load_pair(CODES, refresh=refresh, lk=LK, ind=IND, verbose=False)


def complete_end(idx, close_hour=15):
    """最后一个「已收盘」交易日（避免盘中半日 bar 污染信号与统计）。

    实现在 `strategies/_panel.py::complete_end`（v9/v16 共用）。
    """
    return _panel.complete_end(idx, close_hour=close_hour)


def _main():
    b = load_breadth(refresh=False)
    print(f"广度库 {b.index[0].date()} ~ {b.index[-1].date()} · {len(b)} 天")
    v = load_vehicle(refresh=False)
    print(f"{rule.VEHICLE} {v.index[0].date()} ~ {v.index[-1].date()} · {len(v)} 天")
    p = load_pool(refresh=False)
    print(f"池子 {p['close'].shape} · 缺价 {[c for c in CODES if c not in p['close']]}")


if __name__ == "__main__":
    _main()
