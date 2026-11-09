# -*- coding: utf-8 -*-
"""V18 · 成交量操盘笔记 · 数据层：七腿**场内 ETF 前复权日线 OHLCV**（家族共享库）。

为什么必须走场内 ETF（与主策略最本质的分歧）：
  本变种的**全部判据都建立在成交量与影线上**（D1 高量 / D3 实体上影下影 / D8 观察窗），
  而场外基金复权净值只有一条收盘净值 —— 没有量、没有开高低，规则一条也算不出来。
  全仓库唯一有 O/H/L/C/V 的口径就是场内 ETF 日线，故本变种与 `v1_price_volume/v1_2` 同源，
  与主策略（场外净值）**不可混算**。

为什么美股两腿用人民币 ETF 代理（513100 / 513500）：
  美元指数库（`data/_us_index_klines.json`）只有收盘价，没有成交量。这是「要量」的第二笔代价：
  美股腿的信号里掺了汇率。文档 §8 明确「QDII 不接」，所以这两条腿只作**对照**，不进上线池。

## 共享库
`data/_ohlcv.json`（结构 `{code: {dates, open, high, low, close, volume}}`）。
2026-11-07 从 `v1_price_volume/v1_2_ma_trend_pv/data/` 上移到家族级 `data/` ——
判据是「别的变种也会用」（AGENTS.md §8.1），v1-2 与本变种共用同一份，不各存一份漂移。

## 适用边界（文档 §8，逐腿写死在这里，回测与每日报告都读这一份）
| 文档说法 | 本仓库对应的腿 |
|---|---|
| 个股可（原文本意） | 无（本地无个股 OHLCV 库） |
| 宽基 ETF **不接** | 中证A500 `563360`、沪深300 `510300` —— 量由做市与申赎套利主导 |
| QDII **不接** | 纳指100 `513100`、标普500 `513500` —— 量被外汇额度与溢价套利污染 |
| 主题/行业 ETF 谨慎可试 | 科创50 `588000`、恒生科技 `513180` —— 较贴原意 |
| 文档未点名 | 黄金 `518880` —— 按「谨慎可试」一档处理（量来自实物申赎与场内交易，不是做市噪声） |

「谨慎可试」不等于「能接」：先用 `distortion()` 过量能失真检验，再谈收益。
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

_HERE = os.path.dirname(os.path.abspath(__file__))          # .../v18_volume_notes
_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(_HERE)))   # 仓库根

if _HERE not in sys.path:
    sys.path.insert(0, _HERE)

import legs as LG  # noqa: E402  本变种腿表 / 现金利率

LIB = os.path.join(_ROOT, "data", "_ohlcv.json")     # 仓库级行情库（v1-2 与本变种共用）
START = "20130101"       # 抓取起点（D1c 的 60 日分位 + ATR20 预热之后仍有 ~12 年样本）
WIN = 40                 # 增量比对窗口（日历日）
TOL = 1e-4               # 重叠收盘价的相对容差

CODES = {
    "a500": "563360",     # A500ETF华泰柏瑞（2024-10 上市）
    "hs300": "510300",    # 沪深300ETF华泰柏瑞
    "ndx": "513100",      # 纳指ETF国泰（人民币代理）
    "spx": "513500",      # 标普500ETF博时（人民币代理）
    "kc50": "588000",     # 科创50ETF华夏
    "hkt": "513180",      # 恒生科技ETF华夏
    "gold": "518880",     # 黄金ETF华安
}
NAMES = {
    "a500": "A500ETF华泰柏瑞",
    "hs300": "沪深300ETF华泰柏瑞",
    "ndx": "纳指ETF国泰(人民币代理)",
    "spx": "标普500ETF博时(人民币代理)",
    "kc50": "科创50ETF华夏",
    "hkt": "恒生科技ETF华夏",
    "gold": "黄金ETF华安",
}
LEGS = LG.LEG_ORDER

# 文档 §8 的适用边界（权威源）。"ok" 可接 / "try" 谨慎可试 / "no" 不接。
EDGE = {
    "a500": ("no", "宽基 ETF：量由做市与申赎套利主导，高量多是活跃度噪声"),
    "hs300": ("no", "宽基 ETF：同上（文档点名的 510300）"),
    "ndx": ("no", "QDII：量被外汇额度与溢价套利污染"),
    "spx": ("no", "QDII：同上（文档点名的 513500）"),
    "kc50": ("try", "主题/行业 ETF：较贴原意，需先过量能失真检验"),
    "hkt": ("try", "主题/行业 ETF：同上"),
    "gold": ("try", "商品 ETF：文档未点名，按「谨慎可试」处理"),
}
EDGE_LABEL = {"ok": "可接", "try": "谨慎可试", "no": "不接"}
POOL = [k for k in LEGS if EDGE[k][0] != "no"]      # 上线池（默认口径）
REFERENCE = [k for k in LEGS if EDGE[k][0] == "no"]  # 只作对照，不出结论


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


def _trim_partial(lib):
    """丢掉「今天还没收盘」的那根 bar。

    盘中刷新会把半成品 bar 写进库（当日量只有收盘的四分之一），
    它会污染所有用到末根的判据（高量分位 / 量比 / 支撑压力）。15:05 之后视为已收盘。
    """
    now = pd.Timestamp.now()
    if now.hour * 60 + now.minute >= 15 * 60 + 5:
        return lib
    today = now.strftime("%Y-%m-%d")
    for node in lib.values():
        dates = node.get("dates") or []
        if dates and dates[-1] == today:
            for col in ("dates", "open", "high", "low", "close", "volume"):
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


def _node(df):
    return dict(dates=[str(x) for x in df["date"]],
                open=[float(x) for x in df["open"]],
                high=[float(x) for x in df["high"]],
                low=[float(x) for x in df["low"]],
                close=[float(x) for x in df["close"]],
                volume=[None if pd.isna(v) else float(v) for v in df["volume"]])


def refresh(codes=None):
    """增量刷新。返回 (latest, fails)；失败保留旧缓存（调用方标注陈旧）。"""
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
                else:                             # 只追加新交易日
                    add = _node(df[df["date"] > dates[-1]])
                    for col in ("dates", "open", "high", "low", "close", "volume"):
                        node[col] = list(node[col]) + add[col]
            latest[code] = lib[code]["dates"][-1]
        except Exception as exc:  # noqa: BLE001  —— 网络/口径问题退化为「用缓存」
            fails[code] = f"{type(exc).__name__}: {exc}"
            latest[code] = (lib.get(code) or {}).get("dates", [""])[-1]
    _write(lib)          # 内部会裁掉「今天还没收盘」的半成品 bar
    for _c in list(latest):                     # 裁完之后重取「最新日期」，避免报一个没入库的日期
        if lib.get(_c, {}).get("dates"):
            latest[_c] = lib[_c]["dates"][-1]
    return latest, fails


def bootstrap():
    """全量重建（七只全取）。首次使用或缓存损坏时用。"""
    lib, fails = {}, {}
    for code in sorted(set(CODES.values())):
        try:
            lib[code] = _node(_fetch(code))
        except Exception as exc:  # noqa: BLE001
            fails[code] = f"{type(exc).__name__}: {exc}"
    _write(lib)
    return {c: v["dates"][-1] for c, v in lib.items()}, fails


def frame(leg, refresh_=False, fails=None):
    """该腿 OHLCV -> DataFrame(index=date, columns=open/high/low/close/volume)。"""
    if refresh_:
        _latest, f = refresh([CODES[leg]])
        if fails is not None:
            fails.update(f)
    lib = _read()
    node = lib.get(CODES[leg])
    if not node or not node.get("dates"):
        raise KeyError(f"{CODES[leg]} 不在 {os.path.relpath(LIB, _ROOT)}"
                       f"（先跑 python {os.path.relpath(__file__, _ROOT)} --bootstrap）")
    df = pd.DataFrame({
        "open": node["open"], "high": node["high"], "low": node["low"],
        "close": node["close"], "volume": node["volume"],
    }, index=pd.to_datetime(node["dates"])).sort_index()
    return df.astype(float)


def source_note(leg):
    return f"场内 `{CODES[leg]}` {NAMES[leg]}（前复权日线 · 含成交量）"


# ---------------------------------------------------------------- 量能失真检验
def distortion(leg, q=0.95, lookback=60, mult=2.0):
    """文档 §7「样本池先过量能失真检验」的最小实现 -> dict。

    三组读数（都不含未来信息）:
      freq_d1  : `V > max(前3日)` 的触发频率。文档实测沪深300 指数是 25.8%（iid 理论 25.0%）
                 —— 指数级「高量」在数学上不含异常信息，这是文档要求换判据的直接证据。
      freq_q   : D1c（60 日 95 分位）触发频率，理论值 5%（留出并列后略高）。
      amp      : 高量日均 |日收益| ÷ 非高量日均 |日收益|。真实「异动」应当 >1；
                 接近 1 说明量能与价格波动脱钩（做市噪声）。
      corr     : corr(rvol, |ret|)，同上，>0.2 才算量价同步。
    """
    df = frame(leg)
    c, v = df["close"], df["volume"]
    ret = c.pct_change()
    hv = v > v.shift(1).rolling(3).max()                       # D1
    hvq = v >= v.shift(1).rolling(lookback, min_periods=lookback // 2).quantile(q)   # D1c
    mav = v.rolling(20).mean()
    hvb = v >= mult * mav                                      # D1b
    a = ret.abs()
    ok = a.notna() & hv.notna()
    amp = float(a[ok & hv].mean() / a[ok & ~hv].mean()) if (ok & hv).any() else float("nan")
    rv = (v / mav)
    both = rv.notna() & a.notna()
    corr = float(np.corrcoef(rv[both], a[both])[0, 1]) if both.sum() > 30 else float("nan")
    return dict(leg=leg, name=NAMES[leg], edge=EDGE[leg][0], edge_label=EDGE_LABEL[EDGE[leg][0]],
                n=int(len(df)), start=str(df.index[0].date()), end=str(df.index[-1].date()),
                freq_d1=float(hv[ok].mean()), freq_q=float(hvq[a.notna()].mean()),
                freq_b=float(hvb[a.notna()].mean()), amp=amp, corr=corr,
                note=EDGE[leg][1])


def main():
    """自检 / 手动刷新：python v18_volume_notes/ohlcv.py [--bootstrap] [--refresh]"""
    if "--bootstrap" in sys.argv or not os.path.exists(LIB):
        latest, fails = bootstrap()
        print("全量重建:", LIB)
    elif "--refresh" in sys.argv:
        latest, fails = refresh()
        print("增量刷新完成")
    else:
        latest, fails = {}, {}
        print("只读模式（--refresh 才会联网）")
    for code in sorted(latest):
        print(f"  [{code}] {latest[code]}" + (f"  [失败] {fails[code]}" if code in fails else ""))
    print(f"\n共享库 {os.path.relpath(LIB, _ROOT)} · 库内 {len(_read())} 码")
    for leg in LEGS:
        try:
            df = frame(leg)
            d = distortion(leg)
            print(f"  {leg:6s} {LG.LEGS[leg]['name']:8s} [{EDGE_LABEL[d['edge']]}] "
                  f"{df.index[0].date()} ~ {df.index[-1].date()} · {len(df):5d} 根 · "
                  f"D1 {d['freq_d1']:.1%} / D1b {d['freq_b']:.1%} / D1c {d['freq_q']:.1%} · "
                  f"振幅比 {d['amp']:.2f} · corr(量比,|r|) {d['corr']:+.2f}")
        except Exception as exc:  # noqa: BLE001
            print(f"  {leg:6s} 不可用 {type(exc).__name__}: {exc}")
    print(f"上线池 POOL = {POOL} · 仅对照 = {REFERENCE} · 今日 {date.today()}")


if __name__ == "__main__":
    main()
