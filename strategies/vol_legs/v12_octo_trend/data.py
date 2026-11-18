# -*- coding: utf-8 -*-
"""V12 · 数据层（真实指数收盘 + 同源指数量能；黄金用场内 `518880`）。

价格口径只有一套（2026-11-13 起）：

| 值 | 价格 | 量能 | 用途 |
|---|---|---|---|
| `"index"` | 该市场**真实指数**收盘（`_cn_index_klines.json` / `_global_index_klines.json`） | 同源指数成交量 | 回测 + 每日信号 |
| `"etf"` | 场内 ETF 前复权 OHLCV（`data/_ohlcv.json`） | ETF 自身量 / 市场量（`VOL_MODE`） | 历史对照 |

`index` 口径的 OHLCV 由 `strategies/vol_legs/_idxcache.py` **统一提供**（唯一数据层）：
真实日内高低从 2026-11-13 起落库（`xa.get_daily` 一直返回，早先被落库丢了）；八态只用
close 与 volume，所以这次补字段不改变任何回测数字。

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

# 七腿信号价格的唯一数据层（指数 OHLCV / 黄金用 518880 实物 ETF）。
# 本模块不再自己维护 `_cn_index_klines.json`、也不再自己拼 open/high/low。
def _load_idxcache():
    """按**路径**加载唯一数据层，不往 `sys.path` 插 vol_legs 目录。

    ⚠ 不能写成 `import _idxcache`：那需要把 `strategies/vol_legs` 插到 `sys.path` 最前，
    而那里有同名的 `engine.py` / `data.py` / `rule.py`，会把本变种的同名模块顶掉
    （症状是 `module 'engine' has no attribute 'backtest_leg'`）。
    """
    import importlib.util
    mod = sys.modules.get("_vl_idxcache")
    if mod is not None:
        return mod
    path = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                        "_idxcache.py")
    spec = importlib.util.spec_from_file_location("_vl_idxcache", path)
    mod = importlib.util.module_from_spec(spec)
    sys.modules["_vl_idxcache"] = mod
    spec.loader.exec_module(mod)
    return mod


_idxcache = _load_idxcache()

ETF_LIB = os.path.join(rule._ROOT, "data", "_ohlcv.json")
CN_IDX_LIB = os.path.join(rule._ROOT, "data", "_cn_index_klines.json")

START = "20130101"       # ETF 抓取起点（与 v18 同，再早的 ETF 也没上市）
START_IDX = "20040101"   # 指数量能起点：沪深300 指数 2005-01 就有，多取一年做预热
WIN = 40                 # 增量比对窗口（日历日）
TOL = 1e-4

# 需要指数量能维护的代码（本模块新增的数据类）。
# `SH000688`（科创50 指数）2026-11-12 加入：它比 ETF（588000，2020-11 上市后再扣预热）多出
# 近两年样本，是 `research/v12v13_compare` 做「指数口径」对比的前提。
CN_IDX_CODES = ["SH000300", "SH000001", "SH000688"]


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


def refresh_cn_index(codes=None):
    """刷新 A股指数 OHLCV（`SH000300` / `SH000001` / `SH000688`）。-> (latest, fails)。

    实现在 `_idxcache.refresh`：本变种不再自己维护 `_cn_index_klines.json`
    （否则「三处各写一份、字段还各不相同」必然漂）。指数量能是 rvol 的分母，
    所以仍是本变种主动刷，不等 ETF 库。
    """
    return _idxcache.refresh(["hs300", "kc50"], codes=codes or CN_IDX_CODES)


# ---------------------------------------------------------------- 读取
def index_volume(code):
    """指数成交量序列（空库时抛，由 `frame` 决定是否退化为 ETF 自身量）。"""
    return _idxcache.volume(code)


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


GIDX_LIB = os.path.join(rule._ROOT, "data", "_global_index_klines.json")

# 腿 -> (库, 指数代码)。**权威表在 `_idxcache.LEGS`**，这里派生，别手写第二份。
# 黄金没有「指数」：现货是 OTC 且没有成交量，退回 518880（实物黄金 ETF，无分红）。
IDX_MAP = {leg: (kind, code)
           for leg, (kind, code, _name) in _idxcache.LEGS.items() if kind != "etf"}


def index_frame(leg):
    """该腿的**真实指数** OHLCV -> DataFrame(open/high/low/close/volume)。

    实现统一在 `_idxcache.frame`：真实日内高低（`xa.get_daily` 一直返回，早先被落库丢了），
    库缺 `open/high/low` 时才用 close 回填。指数没有溢价/跟踪误差/汇率污染，历史比 ETF 长 3~10 年；
    量能用同源指数量能，`rvol` 不用再拿 ETF 自身量代理（后者在沪深300 上会把
    「弱回调 − 强回调」做成 −0.77pp，见 `research/probe_volume`）。
    """
    return _idxcache.frame(leg)


def frame(leg):
    """该腿信号用 OHLCV -> DataFrame(index=date, columns=open/high/low/close/volume)。

    价格恒为该市场**真实指数**、量能用同源指数量能；黄金没有指数，
    退回场内 `518880` 实物 ETF（与全族黄金腿同一条序列）。
    """
    if leg in IDX_MAP:
        return index_frame(leg)
    if leg == "gold":
        return _idxcache.frame("gold")
    raise KeyError(f"{leg} 没有真实指数序列（黄金用 518880）")


def etf_frame(leg):
    """**仅供研究对照**：场内 ETF 前复权 OHLCV（`research/probe_volume` 用）。

    不是信号口径 —— 规则 / 回测 / 每日操作 / 图形一律走 `frame()` 的真实指数。
    """
    code = rule.LEGS[leg][0]
    node = _read(ETF_LIB).get(code)
    if not node or not node.get("dates"):
        raise KeyError(f"{code} 不在 {os.path.relpath(ETF_LIB, rule._ROOT)}"
                       f"（先跑 python strategies/tech_indicators/v18_volume_notes/ohlcv.py --bootstrap）")
    idx = pd.to_datetime(node["dates"])
    return pd.DataFrame({
        "open": node["open"], "high": node["high"], "low": node["low"],
        "close": node["close"], "volume": node["volume"],
    }, index=idx).sort_index().astype(float)


def vol_note(leg):
    """该腿量能口径的一句话说明（每日报告/回测脚注用）。"""
    if leg in IDX_MAP:
        return f"`{IDX_MAP[leg][1]}` 指数成交量（与价格同源）"
    return "ETF 自身量（现货黄金无成交量，唯一可得）"


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
        code = IDX_MAP[leg][1] if leg in IDX_MAP else rule.LEGS[leg][0]
        print(f"  {rule.NAMES[leg]:8s} {code:9s} {df.index[0].date()} ~ "
              f"{df.index[-1].date()}  {len(df):5d} 根 · {vol_note(leg)}")
    print(f"今日 {date.today()}")


if __name__ == "__main__":
    main()
