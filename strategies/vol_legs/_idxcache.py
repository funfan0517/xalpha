# -*- coding: utf-8 -*-
"""七腿「信号价格」的唯一数据层：真实指数 OHLCV（黄金用 `518880` 实物 ETF）。

## 为什么要有这一层

家族里有三处各自读指数库、各自拼 `open/high/low`：

- `vol_legs/data.py::index_series_for_leg` —— 只给 `close`（主策略 / v1~v11 用）；
- `v12_octo_trend/data.py::index_frame` —— 需要 `volume`（八态的 `rvol`）；
- `v13_quad_9grid/data.py::index_frame` —— 只用 `close`；

三处的代码表还各写一份（`IDX` / `IDX_MAP` ×2），一旦加腿就要改三处。更糟的是
`open/high/low` 在落库和读库两头都被丢掉（`xa.get_daily` 明明返回真实日内高低，
只是 `_fetch_*` 没存、`index_frame` 又用 close 回填），于是「振幅 / ATR / 跳空 / 影线」
这类特征在这套数据上**算不出来**。

本模块把「哪条腿用哪个代码、代码在哪个库、缺字段怎么补」收成唯一一份，读侧统一返回
`DataFrame(open, high, low, close, volume)`。

## 库（`data/`，属数据类，别的策略将来也会用 —— AGENTS.md §8.1）

| 库 | 内容 | 谁维护 |
|---|---|---|
| `data/_cn_index_klines.json` | `SH000300` / `SH000688` / `SH000001` | 本模块 |
| `data/_global_index_klines.json` | `SH000510` / `.NDX` / `.INX` / `HKHSTECH` | 本模块 |
| `data/_ohlcv.json` | 七腿场内 ETF 前复权 OHLCV（黄金 `518880` 的来源） | `tech_indicators/v18_volume_notes/ohlcv.py` |

schema：`{code: {dates, open, high, low, close, volume}}`。**旧库只有 `dates/close/volume`**，
读侧用 `close` 回填 `open/high/low` 并在 `SOURCE[code]` 里标注「无日内高低」—— 所以本模块
可以在「还没重取」的库上直接工作，不会因为字段缺失报错。

**只写已收盘的 bar**：`refresh()` 落库前一律过 `trim_partial()`，判据统一在
`data/_market_clock.py`（**逐代码按市场**：A 股 15:05 / 港股 16:05 / 美股 bar 收盘于北京次日 05:05）。
本库 `gidx` 把三个市场装在一起，用「全局 15:05 + 北京时间今天」判会漏掉港股的 15:05–16:00 窗口
和美股的 22:30–05:00 窗口，把半成品量能写进库（`rvol` 分母错 → 弱回调/强回调判反）。

## 用法

```bash
python strategies/vol_legs/_idxcache.py            # 自检：逐腿打印区间 / 字段 / 是否真实 OHLC
python strategies/vol_legs/_idxcache.py --refresh  # 重取七个指数（黄金 ETF 不在此列）
```
"""
import importlib.util
import json
import os
import sys

import pandas as pd

_HERE = os.path.dirname(os.path.abspath(__file__))
_ROOT = os.path.dirname(os.path.dirname(_HERE))          # 仓库根（推导，不写死）
_DATA = os.path.join(_ROOT, "data")

LIB = {
    "cn": os.path.join(_DATA, "_cn_index_klines.json"),
    "gidx": os.path.join(_DATA, "_global_index_klines.json"),
    "etf": os.path.join(_DATA, "_ohlcv.json"),
}

# 唯一的一份「腿 -> (库, 代码, 名称)」。黄金没有指数：518880 是实物黄金 ETF（无分红、
# 溢价 <0.3%），不含分红、没有联接基金的跟踪误差，是可得的最干净序列。
LEGS = {
    "a500": ("gidx", "SH000510", "中证A500 指数"),
    "hs300": ("cn", "SH000300", "沪深300 指数"),
    "ndx": ("gidx", ".NDX", "纳斯达克100 指数(美元)"),
    "spx": ("gidx", ".INX", "标普500 指数(美元)"),
    "kc50": ("cn", "SH000688", "科创50 指数"),
    "hkt": ("gidx", "HKHSTECH", "恒生科技指数(HKD)"),
    "gold": ("etf", "518880", "黄金ETF华安(实物, 无分红)"),
}

# 不是七腿、但被量能/对照用到的指数（v12 的 rvol 分母可换成上证量能做对照）。
EXTRA = {"SH000001": ("cn", "上证指数")}

# 取数起点 = 2005-01。这不是「上游只到 2005」——上游能回到 2004（上证指数 5551 根），
# 但**既有全部报告都以 2005 为起算**，为了多一年样本再把全族数字重算一轮不划算，
# 所以固定 2005；要更长的单次研究请自己传 `start=`，别改这个常量。
START = "20050101"


def _load_clock():
    """按**路径**加载 `data/_market_clock.py`（半成品 bar 的唯一判据）。

    不写成 `import _market_clock`：那需要把 `data/` 插到 `sys.path`，而那里有 `_refresh_data.py`
    等脚本，且各策略目录里还有 5 个同名 `data.py`，插路径换来过期模块被顶掉的风险。
    """
    mod = sys.modules.get("_xalpha_market_clock")
    if mod is not None:
        return mod
    spec = importlib.util.spec_from_file_location(
        "_xalpha_market_clock", os.path.join(_DATA, "_market_clock.py"))
    mod = importlib.util.module_from_spec(spec)
    sys.modules["_xalpha_market_clock"] = mod
    spec.loader.exec_module(mod)
    return mod


clock = _load_clock()

SOURCE = {}           # {code: 一句话说明}，命令式自检与报告标注用


# ---------------------------------------------------------------- 读写
def _read(path):
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    except (OSError, ValueError):
        return {}


def _write(path, lib):
    """原子写：先 `.tmp` 再 `os.replace`，避免抓一半崩溃把 20 年历史写坏。"""
    os.makedirs(os.path.dirname(path), exist_ok=True)
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(lib, f, ensure_ascii=False)
    os.replace(tmp, path)


def trim_partial(lib):
    """丢掉每个节点末尾「尚未收盘」的 bar，**逐代码按它自己的市场**判断。

    判据在 `data/_market_clock.py`：A 股 15:05 / 港股 16:05 / 美股 bar 收盘于北京次日 05:05。

    旧实现是「现在早于 15:05 就把末根丢掉」—— 对 A 股成立，对这两个市场不成立，
    而本库 `gidx` 恰恰把三个市场装在了一起：

    - **港股** `HKHSTECH` 16:00 收盘，15:05–16:00 跑刷新会把它 15:0x 的半根写进库
      （`run_daily_vol_legs.ps1` 要求「15:05 之后跑」，正好落在这个窗口里）；
    - **美股** `.NDX/.INX` 的 bar 日期是美东交易日，22:30–次日 05:00 期间那根是半成品，
      旧判据两头都放行（22:30–24:00 已过 15:05；00:00–05:00 bar 日期是昨天）。
    """
    return clock.trim_incomplete(lib)


# ---------------------------------------------------------------- 取数
def _col(df, name):
    """DataFrame 一列 -> list[float|None]（NaN 写 null，避免 JSON 出现裸 NaN）。"""
    if name not in df.columns:
        return None
    s = pd.to_numeric(df[name], errors="coerce")
    return [None if pd.isna(v) else float(v) for v in s]


def _fetch(code, start=START):
    """单码全量 -> node(dates/open/high/low/close/volume)。"""
    import xalpha as xa
    df = xa.get_daily(code, start=start)
    if df is None or not len(df):
        raise RuntimeError(f"{code} 返回空")
    node = dict(dates=[str(pd.Timestamp(d).date()) for d in df["date"]],
                close=_col(df, "close"), volume=_col(df, "volume"))
    for k in ("open", "high", "low"):
        v = _col(df, k)
        if v is not None:
            node[k] = v
    return node


def _targets(legs=None, codes=None):
    """-> {code: (kind, name)}。`legs=None` 时刷全部指数（黄金 ETF 由 v18 维护，不刷）。

    `codes` 用来额外点名非腿代码（`SH000001` 这类只当量能分母的指数）。
    """
    out = {}
    for leg, (kind, code, name) in LEGS.items():
        if kind != "etf" and (legs is None or leg in legs):
            out[code] = (kind, name)
    extra = dict(EXTRA) if legs is None else {}
    for code, (kind, name) in extra.items():
        out[code] = (kind, name)
    for code in (codes or []):
        if code in EXTRA:
            out[code] = EXTRA[code]
        else:
            kind = next((k for k, c, _n in LEGS.values() if c == code), None)
            if kind is None:
                raise KeyError(f"未知指数代码 {code}")
            out[code] = (kind, code)
    return out


def refresh(legs=None, codes=None, start=START):
    """全量重取指数 OHLCV -> (latest, fails)。

    抓取失败**保留旧节点**：一次网络抖动不该把 20 年历史清空（`xa.get_daily` 走雪球适配，
    偶发 `ConnectionResetError` 已经出现过）。

    `latest` 报的是**落库后的末日**（已过 `trim_partial`）：上游在盘中会给出当天的半成品，
    旧版这里把「上游末根」当成「库里末根」报出去，日报脚注就写成了 `SH000300 2026-11-24`
    而库里其实是 11-23 —— 报错了没人看得出来。
    """
    latest, fails, dirty = {}, {}, set()
    for code, (kind, _name) in _targets(legs, codes).items():
        lib = _read(LIB[kind])
        try:
            node = _fetch(code, start)
            trim_partial({code: node})          # 未收盘的尾部 bar 不入库、也不上报
            lib[code] = node
            latest[code] = (node["dates"] or [""])[-1]
        except Exception as exc:  # noqa: BLE001
            fails[code] = f"{type(exc).__name__}: {exc}"
            latest[code] = ((lib.get(code) or {}).get("dates") or [""])[-1]
        dirty.add(kind)
        _write(LIB[kind], trim_partial(lib))
    return latest, fails


# ---------------------------------------------------------------- 读取
def _frame_gold():
    """黄金腿：收盘优先 `_long_klines.json`，日内高低取 `_ohlcv.json`（按日期对齐回填）。

    为什么要两库合并：
    - `_ohlcv.json`（v18 维护）有**真实日内高低**，但只在 `v12/v13 daily` 联网时才刷，
      主策略 `scan.py` 跑在它之前，直接用会拿到**昨天**的收盘；
    - `_long_klines.json` 每天被主流程刷新（收盘价最新），但只有 close。

    两库重叠段实测逐值相同（518880 无分红，前复权 = 原始价），所以合并安全。
    """
    etf = _read(LIB["etf"]).get("518880") or {}
    lk = _read(os.path.join(_DATA, "_long_klines.json")).get("518880") or {}
    if not etf and not lk:
        raise KeyError("518880 不在 `_ohlcv.json` / `_long_klines.json`"
                       "（先跑 python strategies/tech_indicators/v18_volume_notes/ohlcv.py --bootstrap）")
    ser = {}
    for src, cols in ((etf, ("open", "high", "low", "close", "volume")),
                      (lk, ("close",))):
        if not src.get("dates"):
            continue
        idx = pd.to_datetime(src["dates"])
        for k in cols:
            if src.get(k):
                ser[k] = pd.Series(src[k], index=idx, dtype="float64")
    close_lk = ser.get("close")
    close_etf = pd.Series(etf["close"], index=pd.to_datetime(etf["dates"]),
                          dtype="float64") if etf.get("close") else None
    if lk.get("close") and close_etf is not None:
        idx = close_lk.index.union(close_etf.index)
        close = close_lk.reindex(idx).fillna(close_etf)     # 以 _long_klines 为准（最新）
    else:
        close = close_lk if close_lk is not None else close_etf
        idx = close.index
    df = pd.DataFrame(index=idx)
    df["close"] = close
    for k in ("open", "high", "low"):
        s = pd.Series(etf[k], index=pd.to_datetime(etf["dates"]), dtype="float64") \
            if etf.get(k) else None
        df[k] = s.reindex(idx) if s is not None else df["close"]
        df[k] = df[k].fillna(df["close"])
    # 量能只来自 `_ohlcv.json`，若它比 `_long_klines.json` 晚一天，最后一天用前值 ffill
    # （黄金在 v12 里本来就是「代理量能」，不是市场总量）
    vol = (pd.Series(etf["volume"], index=pd.to_datetime(etf["dates"]), dtype="float64")
           .reindex(idx).ffill() if etf.get("volume") else None)
    df["volume"] = vol
    lag = (str(df.index[-1].date()), str(pd.to_datetime(etf["dates"]).max().date())) \
        if etf.get("dates") else ("", "")
    SOURCE["518880"] = ("黄金ETF华安(实物, 无分红)（`518880`）· 收盘 `_long_klines.json`"
                        "（最新）· 日内高低 `_ohlcv.json`"
                        + (f"（至 {lag[1]}，滞后 {lag[0]}）" if lag[1] and lag[1] < lag[0] else ""))
    return df.sort_index()


# ---------------------------------------------------------------- 预演注入（只进内存，绝不落库）
# `pipeline/intraday_preview.py` 把「今日盘中 bar」用**环境变量**传进来，本模块在 `frame()` 出口
# 追加一根合成 bar（价格 = 实时点位、量能 = 按交易进度折算的当日量），然后各策略照常算。
#
# 为什么用环境变量而不是模块全局：仓库里有多份 `_idxcache` 实例（v12 按路径加载一份、
# `vol_legs/data.py` import 一份、`_top3_bridge` 又一份），模块全局只能打进其中一份，
# 环境变量在**同进程内对所有实例同时生效**，且不产生任何磁盘文件。
INTRADAY_ENV = "XALPHA_INTRADAY_BARS"


def intraday_bars():
    """`{leg: {"date", "close", "volume"}}` —— 预演注入表；正式跑时为空。"""
    raw = os.environ.get(INTRADAY_ENV)
    if not raw:
        return {}
    try:
        return json.loads(raw) or {}
    except ValueError:
        return {}


def _with_intraday_bar(leg, df):
    """把预演的今日 bar 接到已收盘序列末尾（只影响返回值）。"""
    bar = intraday_bars().get(leg)
    if not bar or df is None or not len(df):
        return df
    ts = pd.Timestamp(bar["date"])
    close = float(bar["close"])
    vol = float(bar["volume"]) if bar.get("volume") not in (None, "") else None
    if df.index[-1] == ts:                      # 兼容旧库：今天那根已在 -> 原地替换
        df = df.copy()
        df.loc[ts, "close"] = close
        for k in ("open", "high", "low"):
            if k in df.columns:
                df.loc[ts, k] = close
        if vol is not None and "volume" in df.columns:
            df.loc[ts, "volume"] = vol
    else:
        row = {c: float("nan") for c in df.columns}
        for k in ("open", "high", "low", "close"):
            if k in row:
                row[k] = close
        if "volume" in row:
            row["volume"] = vol if vol is not None else float("nan")
        df = pd.concat([df, pd.DataFrame([row], index=pd.DatetimeIndex([ts]))]).sort_index()
    code = LEGS[leg][1]
    SOURCE[code] = (SOURCE.get(code) or "") + f" · **预演注入 {bar['date']} 盘中 bar**"
    return df


def frame(leg):
    """该腿信号用 OHLCV -> DataFrame(index=date, columns=open/high/low/close/volume)。

    指数库没有 `open/high/low`（旧库）时用 close 回填，并在 `SOURCE[code]` 里标注 ——
    调用方可以据此在报告里写「振幅用收盘近似」。
    """
    kind, code, name = LEGS[leg]
    if kind == "etf":                       # 黄金：两库合并（见 `_frame_gold`）
        return _with_intraday_bar(leg, _frame_gold())
    node = _read(LIB[kind]).get(code)
    if not node or not node.get("dates"):
        raise KeyError(f"{code} 不在 {os.path.basename(LIB[kind])}"
                       f"（先跑 python strategies/vol_legs/_idxcache.py --refresh）")
    idx = pd.to_datetime(node["dates"])
    cols = {}
    for k in ("open", "high", "low", "close", "volume"):
        if node.get(k):
            cols[k] = pd.Series(node[k], index=idx, dtype="float64")
    if "close" not in cols:
        raise KeyError(f"{code} 无 close 列")
    real_ohl = all(k in cols for k in ("open", "high", "low"))
    for k in ("open", "high", "low"):
        cols.setdefault(k, cols["close"])
    SOURCE[code] = (f"{name}（`{code}`）· " +
                    ("真实 OHLCV" if real_ohl else "库无日内高低，OHL 已用收盘回填"))
    return _with_intraday_bar(leg, pd.DataFrame(cols).sort_index())


def series(leg, dropna=True):
    """该腿收盘价 Series（主策略 / v1~v11 只吃收盘）。"""
    s = frame(leg)["close"]
    return s.dropna() if dropna else s


def volume(code, kind=None):
    """按代码取成交量（v12 的量能分母要能跨腿取 `SH000300` / `SH000001`）。"""
    if kind is None:
        if code in EXTRA:
            kind = EXTRA[code][0]
        else:
            kind = next((k for k, c, _n in LEGS.values() if c == code), None)
    if kind is None:
        raise KeyError(f"未知指数代码 {code}")
    node = _read(LIB[kind]).get(code)
    if not node or not node.get("dates"):
        raise KeyError(f"{code} 不在 {os.path.basename(LIB[kind])}"
                       f"（先跑 python strategies/vol_legs/_idxcache.py --refresh）")
    return pd.Series(node["volume"], index=pd.to_datetime(node["dates"]),
                     dtype="float64").sort_index()


def describe(leg):
    """一句话说明该腿的信号源（报告/日报用）。耗时只读库，不联网。"""
    kind, code, name = LEGS[leg]
    try:
        df = frame(leg)
    except Exception as exc:  # noqa: BLE001
        return f"`{code}` {name} · 不可用 {type(exc).__name__}"
    ohl = "真实 OHLCV" if bool((df["high"] > df["low"]).any()) else "无日内高低"
    tag = "指数" if kind != "etf" else "实物ETF"
    return (f"{tag} `{code}` {name} · {df.index[0].date()} ~ {df.index[-1].date()}"
            f"（{len(df)} 根）· {ohl}")


# ---------------------------------------------------------------- 自检
def main():
    try:
        import sys
        sys.stdout.reconfigure(encoding="utf-8")   # type: ignore[attr-defined]
    except Exception:  # noqa: BLE001
        pass
    if "--refresh" in os.sys.argv:
        latest, fails = refresh()
        for code, d in latest.items():
            print(f"  {code:10s} 最新 {d or '—'}  {fails.get(code, 'ok')}")
        print(f"失败 {len(fails)} 个")
    print("七腿信号价格：")
    for leg, (_kind, code, _name) in LEGS.items():
        try:
            df = frame(leg)
            ohl = ((df["high"] > df["low"]).mean())
            print(f"  {describe(leg)} · H>L 占比 {ohl:.1%}")
        except Exception as exc:  # noqa: BLE001
            print(f"  {code:10s} 不可用 {type(exc).__name__}: {exc}")


if __name__ == "__main__":
    main()
