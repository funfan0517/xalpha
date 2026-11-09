# -*- coding: utf-8 -*-
"""V1-2 · 20/60/250 量价趋势跟随 · 数据层：七腿**场内 ETF 日线 OHLCV**（本地缓存 + 增量刷新）。

## 为什么这里不走 `data.series_for_leg()`（与 V1-1 最本质的分歧）
「量价」规则要看**成交量**（放量突破 / 缩量回踩 / 放量击穿），而**场外基金净值没有成交量**。
全仓库唯一稳定有量的口径是场内 ETF 日线（`xa.get_daily`，前复权 O/H/L/C/V）。
代价是信号口径从「场外复权净值」换成「场内价格」：多了日内波动与折溢价噪声、少了分红再投，
换来的是**量能这一维信息**。V1-1（`../v1_1_ma20`）保留场外口径做纯价对照，两者不可混算。

美股两腿用**人民币 ETF 代理**（513100 / 513500）而不是美元指数 `.NDX` / `.INX`：
美元指数本地库（`data/_us_index_klines.json`）只有收盘价、没有成交量，量价规则用不了。
这也意味着美股腿的信号里掺了汇率 —— 这是「要量」必须付的第二笔代价，README §5 记录在案。

## 缓存与刷新
- 本地库：`strategies/vol_legs/data/_ohlcv.json`（**家族级共享**，v1-2 与 v13 量价变种共用；
  2026-11-07 从 `v1_2_ma_trend_pv/data/` 上移 —— 判据是「别的变种也会用」，
  见 AGENTS.md §8.1），结构 `{code: {dates, open, high, low, close, volume}}`。
- 首次：`bootstrap()` 全量拉取（起点 2013-01-01，给 250 日年线留 warmup）。
- 之后：`refresh()` 增量追加 —— 先取近 40 天窗口比对重叠收盘价，一致则只追加新交易日，
  不一致（前复权基准变了，ETF 分红会让历史整体平移）则整段重取。
  这套判据与 `data/_refresh_data.py` 相同，只是本策略只缓存自己需要的 7 只。
"""
import json
import os
import sys
from datetime import date

import pandas as pd

try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:  # noqa
    pass

_HERE = os.path.dirname(os.path.abspath(__file__))            # .../v1_2_ma_trend_pv
_FAMILY = os.path.dirname(_HERE)                              # .../v1_price_volume
_VL = os.path.dirname(_FAMILY)                                # strategies/vol_legs
_ROOT = os.path.dirname(os.path.dirname(_VL))                 # 仓库根

if _VL not in sys.path:
    sys.path.insert(0, _VL)

import rule  # noqa: E402  家族参数权威源（腿表 / 现金利率）

LIB = os.path.join(_VL, "data", "_ohlcv.json")   # 家族共享库（v1-2 / v13 量价变种）
START = "20130101"       # 抓取起点（250 日年线 + 指标 warmup 之后仍有 ~11 年样本）
WIN = 40                 # 增量比对窗口（日历日）
TOL = 1e-4               # 重叠收盘价的相对容差

# 腿 -> 场内 ETF 收码。a500 的 563360 上市于 2024-10（年线成形后样本仅约 1 年），
# 不桥接其它标的（换了标的就不是同一条腿的量价了），样本不足在报告里明说。
CODES = {
    "a500": "563360",     # A500ETF华泰柏瑞
    "hs300": "510300",    # 沪深300ETF华泰柏瑞
    "ndx": "513100",      # 纳指ETF国泰（人民币代理）
    "spx": "513500",      # 标普500ETF博时（人民币代理）
    "kc50": "588000",     # 科创50ETF华夏
    "hkt": "513180",      # 恒生科技ETF华夏
    "gold": "518880",     # 黄金ETF华安
}
LEGS = rule.LEG_ORDER

# 报告里显示的标的名称（美股两腿写 ETF 名，不写「(美元)指数」——本策略用的是人民币代理）
NAMES = {
    "a500": "A500ETF华泰柏瑞",
    "hs300": "沪深300ETF华泰柏瑞",
    "ndx": "纳指ETF国泰(人民币代理)",
    "spx": "标普500ETF博时(人民币代理)",
    "kc50": "科创50ETF华夏",
    "hkt": "恒生科技ETF华夏",
    "gold": "黄金ETF华安",
}


def sh(code):
    """6 位场内代码 -> xalpha 前缀格式（SH/SZ）。"""
    return ("SH" if code.startswith(("5", "6", "9")) else "SZ") + code


def _fetch(code, start=START):
    """拉一码日线 -> DataFrame(date, open, high, low, close, volume)。失败直接抛。"""
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


def _read():
    try:
        with open(LIB, encoding="utf-8") as f:
            return json.load(f)
    except (OSError, ValueError):
        return {}


def _write(lib):
    os.makedirs(os.path.dirname(LIB), exist_ok=True)
    with open(LIB, "w", encoding="utf-8") as f:
        json.dump(lib, f, ensure_ascii=False)


def _node(df):
    return dict(dates=[str(x) for x in df["date"]],
                open=[float(x) for x in df["open"]],
                high=[float(x) for x in df["high"]],
                low=[float(x) for x in df["low"]],
                close=[float(x) for x in df["close"]],
                volume=[None if pd.isna(v) else float(v) for v in df["volume"]])


def refresh(codes=None):
    """增量刷新。返回 (latest, fails)；失败保留旧缓存（调用方可标注陈旧）。"""
    lib = _read()
    latest, fails = {}, {}
    for code in (codes or sorted(set(CODES.values()))):
        try:
            node = lib.get(code)
            if not node or not node.get("dates"):
                lib[code] = _node(_fetch(code))
            else:
                dates, close = node["dates"], node["close"]
                start = (pd.Timestamp(dates[-1]) - pd.Timedelta(days=WIN)).strftime("%Y%m%d")
                df = _fetch(code, start)
                fmap = dict(zip(df["date"], df["close"]))
                stored = dict(zip(dates, close))
                overlap = [d for d in dates[-5:] if d in fmap]
                shifted = (not overlap) or any(
                    abs(fmap[d] - stored[d]) > max(1e-6, TOL * abs(stored[d])) for d in overlap)
                if shifted:                       # 前复权基准变了 -> 整段重取
                    lib[code] = _node(_fetch(code))
                else:                             # 只追加新交易日（volume 一并入库）
                    add = _node(df[df["date"] > dates[-1]])
                    for col in ("dates", "open", "high", "low", "close", "volume"):
                        node[col] = list(node[col]) + add[col]
            latest[code] = lib[code]["dates"][-1]
        except Exception as exc:  # noqa: BLE001  —— 网络/口径问题退化为「用缓存」
            fails[code] = f"{type(exc).__name__}: {exc}"
            latest[code] = (lib.get(code) or {}).get("dates", [""])[-1]
    _write(lib)
    return latest, fails


def bootstrap():
    """全量重建（七只全取）。首次使用或缓存损坏时用。"""
    lib = {}
    fails = {}
    for code in sorted(set(CODES.values())):
        try:
            lib[code] = _node(_fetch(code))
        except Exception as exc:  # noqa: BLE001
            fails[code] = f"{type(exc).__name__}: {exc}"
    _write(lib)
    return {c: v["dates"][-1] for c, v in lib.items()}, fails


def frame(leg, refresh_=False, fails=None):
    """该腿 OHLCV -> DataFrame(index=date, columns=open/high/low/close/volume)。

    `refresh_=True` 时先增量刷新（每日扫描用）；刷新失败的码记在 `fails` 里，仍用缓存。
    """
    if refresh_:
        code = CODES[leg]
        _latest, f = refresh([code])
        if fails is not None:
            fails.update(f)
    lib = _read()
    node = lib.get(CODES[leg])
    if not node or not node.get("dates"):
        raise KeyError(f"{CODES[leg]} 不在 {os.path.relpath(LIB, _ROOT)}（先跑 python {os.path.relpath(__file__, _ROOT)} --bootstrap）")
    df = pd.DataFrame({
        "open": node["open"], "high": node["high"], "low": node["low"],
        "close": node["close"], "volume": node["volume"],
    }, index=pd.to_datetime(node["dates"])).sort_index()
    return df.astype(float)


def source_note(leg):
    return f"场内 `{CODES[leg]}` {NAMES[leg]}（前复权日线 · 含成交量）"


def main():
    """自检 / 手动刷新：python ohlcv.py [--bootstrap]"""
    if "--bootstrap" in sys.argv or not os.path.exists(LIB):
        latest, fails = bootstrap()
        print("全量重建:", LIB)
    else:
        latest, fails = refresh()
        print("增量刷新完成")
    for code in sorted(latest):
        print(f"  [{code}] {latest[code]}" + (f"  [失败] {fails[code]}" if code in fails else ""))
    print(f"\n缓存 {os.path.relpath(LIB, _ROOT)} · 库内 {len(_read())} 码")
    for leg in LEGS:
        try:
            df = frame(leg)
            print(f"  {leg:6s} {rule.LEGS[leg]['name']:8s} {df.index[0].date()} ~ "
                  f"{df.index[-1].date()} · {len(df):5d} 根 · {source_note(leg)}")
        except Exception as exc:  # noqa: BLE001
            print(f"  {leg:6s} 不可用 {type(exc).__name__}: {exc}")
    print(f"今日 {date.today()}")


if __name__ == "__main__":
    main()
