# -*- coding: utf-8 -*-
"""V17 · 数据层（家族 54 只场内 ETF 的**收盘 + 成交量**）。

八态判定要两列：`close`（结构/速度）与 `volume`（强弱回调/反弹的 `rvol`）。
家族现有的两份库都不够用：

| 库 | 内容 | 为什么不够 |
|---|---|---|
| `data/_long_klines.json` | 池内代码的**只有收盘** | 没有成交量，八态里的四个态判不出来 |
| `data/_ohlcv.json` | **七只**场内 ETF 的完整 OHLCV | 只有七只；扩到 54 只会让这份共享库从 0.9MB 涨到 ~13MB |

所以本模块维护一份**策略自有的精简库** `data/_etf_cv_klines.json`
（`{code: {dates, close, volume}}`，按 AGENTS.md §8.4② 「策略自有行情缓存」），
约 5MB —— 开高低对八态没有用，不存。

## 口径
- 价格：场内 ETF **前复权收盘**（`xa.get_daily`；与家族 `_long_klines.json` 的来源相同，
  但这里保留前复权口径，跨除权日的 z20 才不会被假的跳空污染）；
- 量能：**ETF 自身成交量**。这是本变种相对 v12 的已知折扣（v12 用同源指数量能，
  宽基 ETF 自身量由做市与申赎套利主导），见 `rule.py` 模块 docstring 的 ⚠；
- 只读消费者：**其它策略不写**这份库；池子仍只由 `data/_universe.md` 决定（经 `../_pool.py`）。

## 维护
```powershell
python strategies/tech_indicators/v17_octo_trend/data.py            # 只读自检
python strategies/tech_indicators/v17_octo_trend/data.py --refresh  # 联网增量
```
"""
import json
import os
import sys
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import date

import numpy as np
import pandas as pd

try:
    sys.stdout.reconfigure(encoding="utf-8")  # type: ignore[attr-defined]
except Exception:  # noqa: BLE001
    pass

_HERE = os.path.dirname(os.path.abspath(__file__))                 # v17_octo_trend/
_PARENT = os.path.dirname(_HERE)                                   # strategies/tech_indicators
_ROOT = os.path.dirname(os.path.dirname(_PARENT))                  # 仓库根
_DATA = os.path.join(_ROOT, "data")
for _p in (_ROOT, _DATA, _PARENT, _HERE):
    if _p not in sys.path:
        sys.path.insert(0, _p)

import _pool  # noqa: E402  家族唯一池定义（data/_universe.md 前四类）

LIB = os.path.join(_DATA, "_etf_cv_klines.json")
START = "20130101"       # 抓取起点（与 v18 同：再早的这批 ETF 也大多没上市）
WIN = 40                 # 增量比对窗口（日历日）
TOL = 1e-4               # 重叠收盘价的相对容差（判「前复权基准是否变了」）

POOL = _pool.rows()
CODES = [r["code"] for r in POOL]
NAMES = {r["code"]: (r.get("inner_name") or r.get("off_name") or r["code"]) for r in POOL}


# ---------------------------------------------------------------- 抓取
def sh(code):
    """6 位场内代码 -> xalpha 前缀格式。"""
    return ("SH" if code.startswith(("5", "6", "9")) else "SZ") + code


def _fetch(code, start=START):
    """拉一码日线 -> DataFrame(date, close, volume)。失败直接抛。"""
    import xalpha as xa
    df = xa.get_daily(sh(code), start=start)
    if df is None or not len(df):
        raise RuntimeError(f"{code} 返回空")
    out = pd.DataFrame({
        "date": pd.to_datetime(df["date"]).dt.strftime("%Y-%m-%d"),
        "close": pd.to_numeric(df["close"], errors="coerce"),
        "volume": pd.to_numeric(df["volume"], errors="coerce"),
    }).dropna(subset=["close"])
    return out.sort_values("date").reset_index(drop=True)


def _node(df):
    """DataFrame -> 库节点。收盘保留 4 位、成交量取整（rvol 只看比值，精度足够）。"""
    return dict(dates=[str(x) for x in df["date"]],
                close=[round(float(x), 4) for x in df["close"]],
                volume=[None if pd.isna(v) else int(round(float(v))) for v in df["volume"]])


def _trim_partial(lib):
    """丢掉「今天还没收盘」的那根 bar（盘中刷新会写入半成品量能）。15:05 之后视为已收盘。"""
    now = pd.Timestamp.now()
    if now.hour * 60 + now.minute >= 15 * 60 + 5:
        return lib
    today = now.strftime("%Y-%m-%d")
    for node in lib.values():
        if (node.get("dates") or [""])[-1] == today:
            for col in ("dates", "close", "volume"):
                if node.get(col):
                    node[col] = node[col][:-1]
    return lib


def _read():
    try:
        with open(LIB, encoding="utf-8") as f:
            return _trim_partial(json.load(f))
    except (OSError, ValueError):
        return {}


def _write(lib):
    os.makedirs(os.path.dirname(LIB), exist_ok=True)
    with open(LIB, "w", encoding="utf-8") as f:
        json.dump(_trim_partial(lib), f, ensure_ascii=False)


def _update_one(code, node):
    """单只增量更新 -> 新节点（失败抛异常，由调用方回退旧缓存）。"""
    if not node or not node.get("dates"):
        return _node(_fetch(code))
    dates, close = node["dates"], node["close"]
    start = (pd.Timestamp(dates[-1]) - pd.Timedelta(days=WIN)).strftime("%Y%m%d")
    df = _fetch(code, start)
    fmap = dict(zip(df["date"], df["close"]))
    stored = dict(zip(dates, close))
    overlap = [d for d in dates[-5:] if d in fmap]
    shifted = (not overlap) or any(
        abs(fmap[d] - stored[d]) > max(1e-6, TOL * abs(stored[d])) for d in overlap)
    if shifted:                                    # 前复权基准变了 -> 整段重取
        return _node(_fetch(code))
    add = _node(df[df["date"] > dates[-1]])
    return {col: list(node[col]) + add[col] for col in ("dates", "close", "volume")}


def refresh_cv(codes=None, workers=6):
    """增量刷新收盘 + 成交量。-> (latest, fails)；失败保留旧缓存（调用方标注陈旧）。

    54 只串行约 4 分钟、6 路并发约 45 秒（每只都只是一次 HTTP 拉取，互不依赖）；
    写库仍在主线程一次性完成，避免并发写文件。
    """
    lib = _read()
    latest, fails = {}, {}
    todo = list(codes or CODES)
    with ThreadPoolExecutor(max_workers=max(1, int(workers))) as ex:
        futs = {ex.submit(_update_one, c, lib.get(c)): c for c in todo}
        for fut in as_completed(futs):
            code = futs[fut]
            try:
                lib[code] = fut.result()
            except Exception as exc:  # noqa: BLE001  —— 网络/口径问题退化为「用缓存」
                fails[code] = f"{type(exc).__name__}: {exc}"
    _write(lib)                                    # 内部会裁掉盘中半成品 bar
    for code in todo:
        latest[code] = (lib.get(code) or {}).get("dates", [""])[-1]
    return latest, fails


# ---------------------------------------------------------------- 读取
def frame(code):
    """该标的八态用的 OHLCV -> DataFrame(index=date, columns=close / volume)。

    只给两列是刻意的：八态不需要开高低（`engine.indicators` 只用 close 与 volume）。
    """
    node = _read().get(code)
    if not node or not node.get("dates"):
        raise KeyError(f"{code} 不在 {os.path.relpath(LIB, _ROOT)}"
                       f"（先跑 python strategies/tech_indicators/v17_octo_trend/data.py --refresh）")
    idx = pd.to_datetime(node["dates"])
    df = pd.DataFrame({"close": node["close"], "volume": node["volume"]}, index=idx)
    return df.sort_index().astype(float)


def names():
    """代码 -> 场内名称（副本）。"""
    return dict(NAMES)


def available():
    """有行情的标的（按池顺序）。"""
    lib = _read()
    return [c for c in CODES if c in lib and len(lib[c].get("dates") or []) > 2]


def vol_note(code):
    """该标的量能口径的一句话说明（报告/日报脚注用）。"""
    return f"ETF 自身量（`{code}`，无指数量能可用 -> 宽基含做市噪声）"


def last_date():
    lib = _read()
    return max(pd.Timestamp(v["dates"][-1]) for v in lib.values() if v.get("dates"))


def main():
    """自检 / 手动刷新：python v17_octo_trend/data.py [--refresh]"""
    if "--refresh" in sys.argv:
        latest, fails = refresh_cv()
        for c in sorted(latest):
            print(f"  [{c}] {latest[c]}" + (f"  [失败] {fails[c]}" if c in fails else ""))
    print(f"\n库 {os.path.relpath(LIB, _ROOT)} · "
          + (f"最新 {last_date().date()}" if os.path.exists(LIB) else "缺失（跑 --refresh）"))
    print(f"池 {len(CODES)} 只 · 有行情 {len(available())} 只 · 今日 {date.today()}")
    for code in available():
        df = frame(code)
        print(f"  {code:8s} {NAMES.get(code, code):14s} {df.index[0].date()} ~ "
              f"{df.index[-1].date()}  {len(df):5d} 根")


if __name__ == "__main__":
    main()
