# -*- coding: utf-8 -*-
"""V9 抄底确认 · 行情装载（OHLCV，本变种自带缓存）。

**为什么本变种不能用 `../data.py`**：抄底四要素里有三条要 `open` / `low` / `high`：
- 「放量**阳**」= `close > open`（只有收盘价就只能拿「涨了」近似，口径不同）；
- 「阶段**最低**点 ×0.97 止损」= 必须用 `low`，用 `close` 会系统性抬高止损位；
- 「斐波那契速度阻力扇 0.5 线」= `low` 与 `high` 的中点。

而仓库共享库里 `data/_long_klines.json` 只存 `close`、`data/_indicators_hist.json` 只存
`volume`。所以本变种自己取一次 `xa.get_daily`（雪球，**前复权**），落到
`<本目录>/data/_v9_klines.json`（策略类行情缓存，AGENTS §8.4 ②）。

口径三条：
1. **前复权**：`get_daily` 默认前复权，OHLC 同比例缩放，`close>open` 与 `low<close` 的
   相对关系不受除权影响；成交量是原始手数，不做复权。
2. **只补不重写**：增量拉取近 40 天与库内重叠交易日比对 `close`，一致则只追加新交易日；
   不一致（复权基准平移）则整段重取 —— 与 `data/_refresh_data.py` 同一套判据。
3. **陈旧判据对齐共享库**：以 `data/_long_klines.json` 里同代码的最后一个日期为「应该到哪天」。
   共享库由 `data/_refresh_data.py` 每日刷新；它已最新时本模块一次网络都不发。

标的池（`UNIVERSE`）刻意与主策略七腿一一对应，但**不是**七腿的场外净值：
美股两腿的美元指数库只有收盘价，因此这里用**人民币场内代理**（513100/513500），
报告里逐行标注「代理」。这是本变种唯一的口径妥协。
"""
import json
import os
import sys

import pandas as pd

try:
    sys.stdout.reconfigure(encoding="utf-8")  # type: ignore[attr-defined]
except Exception:  # noqa: BLE001
    pass

_HERE = os.path.dirname(os.path.abspath(__file__))
_VL = os.path.dirname(_HERE)                                  # strategies/vol_legs
_ROOT = os.path.dirname(os.path.dirname(_VL))                 # 仓库根
if _VL not in sys.path:
    sys.path.insert(0, _VL)

CACHE = os.path.join(_HERE, "data", "_v9_klines.json")
SHARED_LK = os.path.join(_ROOT, "data", "_long_klines.json")

# 七腿 -> 场内 OHLCV 代码。a500 的 563360 上市 2024-10，只有 2 年样本，
# 因此**不进组合层**（见 PORTFOLIO_LEGS 与 README §5）。
UNIVERSE = {
    "a500": dict(code="563360", name="A500ETF华泰柏瑞", proxy=False,
                 note="场内 2024-10 上市，样本约 2 年，只做单腿展示"),
    "hs300": dict(code="510300", name="沪深300ETF华泰柏瑞", proxy=False, note=""),
    "ndx": dict(code="513100", name="纳指ETF国泰", proxy=True,
                note="美股腿用**人民币场内代理**（美元指数库只有收盘价，无 OHLCV）"),
    "spx": dict(code="513500", name="标普500ETF博时", proxy=True,
                note="同上：人民币场内代理"),
    "kc50": dict(code="588000", name="科创50ETF华夏", proxy=False, note=""),
    "hkt": dict(code="513180", name="恒生科技ETF华夏", proxy=False, note=""),
    "gold": dict(code="518880", name="黄金ETF华安", proxy=False, note=""),
}
LEGS = list(UNIVERSE)
# 组合层只取样本足够长的六腿（a500 的 563360 太短，凑共同窗口会把整段压成 1 年）
PORTFOLIO_LEGS = ("hs300", "ndx", "spx", "kc50", "hkt", "gold")

FIELDS = ("open", "high", "low", "close", "volume")
START = "20050101"        # 全量首次拉取的起点（各标的自然从上市日开始）
ONCE = []                 # 进程内只刷新一次
_FRAMES = {}              # 进程内行情缓存


def prefix(code):
    """6 位场内代码 -> xalpha 前缀格式。ETF：5/6/9 开头在沪市，其余在深市。"""
    return ("SH" if code.startswith(("5", "6", "9")) else "SZ") + code


# ---------------------------------------------------------------- 缓存读写
def _read():
    try:
        with open(CACHE, encoding="utf-8") as f:
            return json.load(f)
    except (OSError, ValueError):
        return {}


def _write(lib):
    os.makedirs(os.path.dirname(CACHE), exist_ok=True)
    with open(CACHE, "w", encoding="utf-8") as f:
        json.dump(lib, f, ensure_ascii=False)


def _shared_last():
    """`{code: 最后一个交易日}` —— 共享库的进度，用作「应该到哪天」的判据。"""
    try:
        with open(SHARED_LK, encoding="utf-8") as f:
            lib = json.load(f)
    except (OSError, ValueError):
        return {}
    return {k: v["dates"][-1] for k, v in lib.items() if v.get("dates")}


# ---------------------------------------------------------------- 取数
def _fetch(code, start):
    """xalpha 雪球日线 -> DataFrame(date, open, high, low, close, volume)。"""
    import xalpha as xa
    df = xa.get_daily(prefix(code), start=start)
    if df is None or not len(df):
        raise RuntimeError(f"{code} 返回空")
    out = pd.DataFrame({
        "date": pd.to_datetime(df["date"]).dt.strftime("%Y-%m-%d"),
        "open": pd.to_numeric(df["open"], errors="coerce"),
        "high": pd.to_numeric(df["high"], errors="coerce"),
        "low": pd.to_numeric(df["low"], errors="coerce"),
        "close": pd.to_numeric(df["close"], errors="coerce"),
        "volume": pd.to_numeric(df["volume"], errors="coerce"),
    }).dropna(subset=["open", "high", "low", "close"]).sort_values("date")
    out = out[out["close"] > 0]
    return out.reset_index(drop=True)


def refresh(codes=None, verbose=False):
    """增量刷新缓存。返回 (latest, fails)；失败沿用旧缓存，不静默造数。"""
    lib = _read()
    shared = _shared_last()
    latest, fails = {}, {}
    for key in (codes or LEGS):
        code = UNIVERSE[key]["code"]
        node = lib.get(code)
        try:
            if not node or not node.get("dates"):
                df = _fetch(code, START)
                lib[code] = {k: list(df[k]) for k in ("date", *FIELDS)}
                lib[code]["dates"] = lib[code].pop("date")
            else:
                dates = node["dates"]
                start = (pd.Timestamp(dates[-1]) - pd.Timedelta(days=40)).strftime("%Y%m%d")
                df = _fetch(code, start)
                fmap = dict(zip(df["date"], df["close"]))
                overlap = [d for d in dates[-5:] if d in fmap]
                shifted = (not overlap) or any(
                    abs(fmap[d] - node["close"][dates.index(d)]) > 1e-6 * max(1.0, abs(fmap[d]))
                    for d in overlap)
                if shifted:                              # 复权基准变了 -> 整段重取
                    df = _fetch(code, START)
                    lib[code] = {k: list(df[k]) for k in ("date", *FIELDS)}
                    lib[code]["dates"] = lib[code].pop("date")
                else:                                    # 一致 -> 只追加新交易日
                    add = [d for d in df["date"] if d > dates[-1]]
                    if add:
                        node["dates"] = dates + add
                        for k in FIELDS:
                            node[k] = list(node[k]) + [float(x) for x in
                                                       df.set_index("date").loc[add, k]]
            latest[key] = lib[code]["dates"][-1]
            if verbose:
                print(f"  [{code}] {latest[key]}")
        except Exception as exc:  # noqa: BLE001  网络/口径问题都退化为「用缓存」
            fails[key] = f"{type(exc).__name__}: {exc}"
            latest[key] = (node or {}).get("dates", [""])[-1]
            if verbose:
                print(f"  [{code}] 失败，沿用缓存: {fails[key]}")
    _write(lib)
    return latest, fails


def stale(codes=None):
    """-> [key]，缓存落后于共享库（或缺失）的标的。"""
    lib = _read()
    shared = _shared_last()
    out = []
    for key in (codes or LEGS):
        code = UNIVERSE[key]["code"]
        node = lib.get(code)
        if not node or not node.get("dates"):
            out.append(key)
            continue
        want = shared.get(code)
        if want and node["dates"][-1] < want:
            out.append(key)
    return out


def frame(key, refresh_=True):
    """该标的 -> DataFrame(index=DatetimeIndex, columns=open/high/low/close/volume)。

    进程内缓存：回测里同一标的会被参数扫描反复取用，重复读 7 MB JSON 太浪费。
    """
    if refresh_ and not ONCE:
        todo = stale()
        ONCE.append(refresh(todo) if todo else ({}, {}))
    if key in _FRAMES:
        return _FRAMES[key].copy()
    lib = _read()
    code = UNIVERSE[key]["code"]
    node = lib.get(code)
    if not node or not node.get("dates"):
        raise KeyError(f"{code} 不在 {os.path.relpath(CACHE, _ROOT)} 且刷新失败")
    idx = pd.DatetimeIndex(pd.to_datetime(node["dates"]))
    df = pd.DataFrame({k: pd.to_numeric(node[k], errors="coerce") for k in FIELDS}, index=idx)
    df = df.dropna(subset=["open", "high", "low", "close"]).sort_index()
    _FRAMES[key] = df
    return df.copy()


def closes(keys=None):
    """-> DataFrame(index=交易日并集, columns=leg)，各腿只在自身有数据的日期有值。"""
    keys = list(keys or PORTFOLIO_LEGS)
    return pd.DataFrame({k: frame(k)["close"] for k in keys}).sort_index()


def _main():
    todo = stale()
    latest, fails = refresh(todo, verbose=True) if todo else ({}, {})
    print(f"缓存 {os.path.relpath(CACHE, _ROOT)} · 本轮刷新 {len(todo)} 只 · 失败 {len(fails)}")
    for key in LEGS:
        df = frame(key, refresh_=False)
        print(f"  {UNIVERSE[key]['name']:14s} {UNIVERSE[key]['code']} "
              f"{df.index[0].date()} ~ {df.index[-1].date()} · {len(df):5d} 根"
              + ("  [代理]" if UNIVERSE[key]["proxy"] else ""))


if __name__ == "__main__":
    _main()
