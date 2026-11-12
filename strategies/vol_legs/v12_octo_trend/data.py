# -*- coding: utf-8 -*-
"""V12 · 数据层（场内 ETF OHLCV + A股指数量能）。

两套缓存，都是**只读 + 少量增量刷新**：

| 库 | 内容 | 谁维护 |
|---|---|---|
| `data/_ohlcv.json` | 七腿场内 ETF 前复权 OHLCV | `tech_indicators/v18_volume_notes/ohlcv.py`（主写入方）；本模块只在 `--refresh` 时增量补最后几天，schema 与它逐字一致 |
| `data/_cn_index_klines.json` | `SH000300` / `SH000001` 的收盘 + 成交量 | **本模块维护**（`refresh_cn_index()`），因为 [[V12]] 是第一个需要「指数量能」的变种 |

## 为什么「量能」不能用 ETF 自己的成交量（这是本变种最关键的一处口径）

八态的强弱回调/反弹**全靠量比 `rvol` 分档**。实测把 `510300` 的**自身成交量**当分母，
沪深300 的「弱回调 − 强回调」分离度是 **−0.77pp（符号反了）**；换成 **沪深300 指数成交量**
是 **+1.20pp**、换成上证指数成交量 **+2.02pp** —— 全部为正。原因写在
`v18_volume_notes/ohlcv.py::EDGE` 里：「宽基 ETF 的量由做市与申赎套利主导，
高量多是活跃度噪声」。所以：

- **A股 3 腿（a500 / hs300 / kc50）用 `SH000300` 的成交量**（同一市场的大盘量能，
  也是规格标定时用的那一条量能序列）；
- **其余 4 腿（ndx / spx / hkt / gold）没有对应指数量能库**，只能用 ETF 自身量，
  报告里标注「代理量能」——它们本来也只做诊断（`rule.CORE_LEGS` 只有 hs300）。

开关 `rule.VOL_MODE`：`"market"`（默认）/ `"etf"`（全部用 ETF 自身量，仅供研究对照，
`research/probe_volume/_probe_volume.py` 就是靠它跑出上面那三个数）。
"""
import json
import os
import sys
from datetime import date

import numpy as np
import pandas as pd

try:
    sys.stdout.reconfigure(encoding="utf-8")  # type: ignore[attr-defined]
except Exception:  # noqa: BLE001
    pass

import rule

ETF_LIB = os.path.join(rule._ROOT, "data", "_ohlcv.json")
CN_IDX_LIB = os.path.join(rule._ROOT, "data", "_cn_index_klines.json")

START = "20130101"       # ETF 抓取起点（与 v18 同，再早的 ETF 也没上市）
START_IDX = "20040101"   # 指数量能起点：沪深300 指数 2005-01 就有，多取一年做预热
WIN = 40                 # 增量比对窗口（日历日）
TOL = 1e-4

# 需要指数量能维护的代码（本模块新增的数据类）
CN_IDX_CODES = ["SH000300", "SH000001"]


# ---------------------------------------------------------------- 读写
def _read(path):
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    except (OSError, ValueError):
        return {}


def _write(path, lib):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(lib, f, ensure_ascii=False)


def _trim_partial(lib):
    """丢掉「今天还没收盘」的那根 bar（盘中刷新会写入半成品量能）。15:05 之后视为已收盘。"""
    now = pd.Timestamp.now()
    if now.hour * 60 + now.minute >= 15 * 60 + 5:
        return lib
    today = now.strftime("%Y-%m-%d")
    for node in lib.values():
        if (node.get("dates") or [""])[-1] == today:
            for col in ("dates", "open", "high", "low", "close", "volume"):
                if node.get(col):
                    node[col] = node[col][:-1]
    return lib


def sh(code):
    """6 位场内代码 -> xalpha 前缀格式。"""
    return ("SH" if code.startswith(("5", "6", "9")) else "SZ") + code


def _fetch_etf(code, start=START):
    import xalpha as xa
    df = xa.get_daily(sh(code), start=start)
    if df is None or not len(df):
        raise RuntimeError(f"{code} 返回空")
    out = pd.DataFrame({
        "date": pd.to_datetime(df["date"]).dt.strftime("%Y-%m-%d"),
        "open": pd.to_numeric(df["open"], errors="coerce"),
        "high": pd.to_numeric(df["high"], errors="coerce"),
        "low": pd.to_numeric(df["low"], errors="coerce"),
        "close": pd.to_numeric(df["close"], errors="coerce"),
        "volume": pd.to_numeric(df["volume"], errors="coerce"),
    }).dropna(subset=["close", "high", "low"])
    return out.sort_values("date").reset_index(drop=True)


def _node_etf(df):
    return dict(dates=[str(x) for x in df["date"]],
                open=[float(x) for x in df["open"]],
                high=[float(x) for x in df["high"]],
                low=[float(x) for x in df["low"]],
                close=[float(x) for x in df["close"]],
                volume=[None if pd.isna(v) else float(v) for v in df["volume"]])


def refresh_etf(codes=None):
    """增量刷新 ETF OHLCV（schema 与 v18 的 `_ohlcv.json` 完全一致，可互相追加）。

    返回 (latest, fails)。失败保留旧缓存，由调用方标注陈旧 —— 与 `vol_legs/data.py::refresh_us`
    同一种「网络失败不影响使用缓存」的约定。
    """
    lib = _trim_partial(_read(ETF_LIB))
    latest, fails = {}, {}
    for code in (codes or sorted({v[0] for v in rule.LEGS.values()})):
        try:
            node = lib.get(code)
            if not node or not node.get("dates"):
                lib[code] = _node_etf(_fetch_etf(code))
            else:
                dates, close = node["dates"], node["close"]
                start = (pd.Timestamp(dates[-1]) - pd.Timedelta(days=WIN)).strftime("%Y%m%d")
                df = _fetch_etf(code, start)
                fmap = dict(zip(df["date"], df["close"]))
                stored = dict(zip(dates, close))
                overlap = [d for d in dates[-5:] if d in fmap]
                shifted = (not overlap) or any(
                    abs(fmap[d] - stored[d]) > max(1e-6, TOL * abs(stored[d])) for d in overlap)
                if shifted:                        # 前复权基准变了 -> 整段重取
                    lib[code] = _node_etf(_fetch_etf(code))
                else:
                    add = _node_etf(df[df["date"] > dates[-1]])
                    for col in ("dates", "open", "high", "low", "close", "volume"):
                        node[col] = list(node[col]) + add[col]
            latest[code] = lib[code]["dates"][-1]
        except Exception as exc:  # noqa: BLE001
            fails[code] = f"{type(exc).__name__}: {exc}"
            latest[code] = (lib.get(code) or {}).get("dates", [""])[-1]
    _write(ETF_LIB, _trim_partial(lib))
    return latest, fails


def _fetch_index(code):
    import xalpha as xa
    df = xa.get_daily(code, start=START_IDX)
    if df is None or not len(df):
        raise RuntimeError(f"{code} 返回空")
    return pd.DataFrame({
        "date": pd.to_datetime(df["date"]).dt.strftime("%Y-%m-%d"),
        "close": pd.to_numeric(df["close"], errors="coerce"),
        "volume": pd.to_numeric(df["volume"], errors="coerce"),
    }).dropna(subset=["close"]).sort_values("date").reset_index(drop=True)


def refresh_cn_index(codes=None):
    """刷新 A股指数收盘 + 成交量（`SH000300` / `SH000001`）。-> (latest, fails)。

    指数量能是**本变种的核心输入**（rvol 的分母），所以它不能像 ETF 库那样「等别人刷」。
    全量重取（一个指数十年数据只有几百 KB，一次 HTTP 就够，不需要增量逻辑）。
    """
    lib = _trim_partial(_read(CN_IDX_LIB))
    latest, fails = {}, {}
    for code in (codes or CN_IDX_CODES):
        try:
            df = _fetch_index(code)
            lib[code] = dict(dates=[str(x) for x in df["date"]],
                             close=[float(x) for x in df["close"]],
                             volume=[None if pd.isna(v) else float(v) for v in df["volume"]])
            latest[code] = lib[code]["dates"][-1]
        except Exception as exc:  # noqa: BLE001
            fails[code] = f"{type(exc).__name__}: {exc}"
            latest[code] = (lib.get(code) or {}).get("dates", [""])[-1]
    # 盘中（<15:05）取到的当日 bar 是半成品：成交量只有半天，会把 rvol 压到 0.5 以下 -> 直接丢
    _write(CN_IDX_LIB, _trim_partial(lib))
    return latest, fails


# ---------------------------------------------------------------- 读取
def index_volume(code):
    """指数成交量序列（空库时抛，由 `frame` 决定是否退化为 ETF 自身量）。"""
    node = _read(CN_IDX_LIB).get(code)
    if not node or not node.get("dates"):
        raise KeyError(f"{code} 不在 {os.path.relpath(CN_IDX_LIB, rule._ROOT)}"
                       f"（先跑 python {os.path.relpath(__file__, rule._ROOT)} --refresh）")
    return pd.Series(node["volume"], index=pd.to_datetime(node["dates"]),
                     dtype="float64").sort_index()


def market_volume(leg):
    """该腿该用的「量能」序列 -> (Series | None, 说明)。

    A股 3 腿 -> 沪深300 指数成交量（市场总量，避开宽基 ETF 的做市噪声）；
    其余腿 -> None（调用方退回 ETF 自身量，并在报告里标「代理量能」）。
    """
    if rule.VOL_MODE != "market":
        return None, "ETF 自身量（`VOL_MODE=etf`，研究对照口径）"
    code = rule.MKT_VOL_LEG.get(leg)
    if not code:
        return None, "ETF 自身量（本地无该市场指数量能库 -> **代理量能**）"
    try:
        return index_volume(code), f"`{code}` 指数成交量（市场量能）"
    except KeyError as exc:
        return None, f"ETF 自身量（指数量能缺失: {exc.args[0][:24]}… -> **代理量能**）"


def frame(leg, vol_mode=None):
    """该腿信号用 OHLCV -> DataFrame(index=date, columns=open/high/low/close/volume)。

    `volume` 按 `rule.VOL_MODE` 换成市场量能（缺失时退回 ETF 自身量）。
    """
    code = rule.LEGS[leg][0]
    node = _read(ETF_LIB).get(code)
    if not node or not node.get("dates"):
        raise KeyError(f"{code} 不在 {os.path.relpath(ETF_LIB, rule._ROOT)}"
                       f"（先跑 python strategies/tech_indicators/v18_volume_notes/ohlcv.py --bootstrap）")
    idx = pd.to_datetime(node["dates"])
    df = pd.DataFrame({
        "open": node["open"], "high": node["high"], "low": node["low"],
        "close": node["close"], "volume": node["volume"],
    }, index=idx).sort_index().astype(float)

    old = rule.VOL_MODE
    if vol_mode is not None:
        rule.VOL_MODE = vol_mode
    try:
        vol, _note = market_volume(leg)
    finally:
        rule.VOL_MODE = old
    if vol is not None:
        mapped = vol.reindex(df.index).ffill()
        if mapped.notna().sum() > 0.5 * len(df):
            df["volume"] = mapped
    return df


def vol_note(leg, vol_mode=None):
    """该腿量能口径的一句话说明（每日报告/回测脚注用）。"""
    old = rule.VOL_MODE
    if vol_mode is not None:
        rule.VOL_MODE = vol_mode
    try:
        return market_volume(leg)[1]
    finally:
        rule.VOL_MODE = old


def available():
    lib = _read(ETF_LIB)
    return [k for k in rule.LEG_ORDER if rule.LEGS[k][0] in lib]


def last_date():
    lib = _read(ETF_LIB)
    return max(pd.Timestamp(v["dates"][-1]) for v in lib.values() if v.get("dates"))


def main():
    """自检 / 手动刷新：python v12_octo_trend/data.py [--refresh]"""
    if "--refresh" in sys.argv:
        latest, fails = refresh_etf()
        for c in sorted(latest):
            print(f"  [ETF {c}] {latest[c]}" + (f"  [失败] {fails[c]}" if c in fails else ""))
        latest, fails = refresh_cn_index()
        for c in sorted(latest):
            print(f"  [IDX {c}] {latest[c]}" + (f"  [失败] {fails[c]}" if c in fails else ""))
    print(f"\nETF 库 {os.path.relpath(ETF_LIB, rule._ROOT)} · 最新 {last_date().date()}")
    print(f"指数库 {os.path.relpath(CN_IDX_LIB, rule._ROOT)} · "
          f"{'存在' if os.path.exists(CN_IDX_LIB) else '缺失（跑 --refresh）'}")
    print(f"量能口径 VOL_MODE = {rule.VOL_MODE}")
    for leg in available():
        df = frame(leg)
        print(f"  {rule.NAMES[leg]:8s} {rule.LEGS[leg][0]}  {df.index[0].date()} ~ "
              f"{df.index[-1].date()}  {len(df):5d} 根 · {vol_note(leg)}")
    print(f"今日 {date.today()}")


if __name__ == "__main__":
    main()
