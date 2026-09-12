# -*- coding: utf-8 -*-
r"""中证红利ETF 策略 · 数据层 —— 构建三个指标的历史序列（纯 Python, 可回测 & 可复用）。

数据链路（均为公开、可自动获取）:
  中证红利价格指数 000922   —— 中证官网 index-perf(indexCode=000922) 日频, 含 `peg`(近似静态PE)
  中证红利全收益 H00922     —— 同上(indexCode=H00922), 与价格指数同源同频
  10Y 国债到期收益率        —— 中债收益率曲线(经 core_rotation 落库 data/_bt_caches/bond10y_m.csv)

指标口径（全部因果, 无前视）:
  股息率 dy (%)      = (H00922/000922) 比值近 252 交易日增长 × 100  —— 分红再投指数相对
                       价格指数的超额增长即近 12 个月股息率(TTM)的代理; 实测均值 ~4.7%,
                       落在中证红利真实股息率区间, **无需人工校准**。
  PE 分位 pe_pct      = 中证官网 peg 在「过去最多 10 年」窗口内的累计分位 (0-1)。窗口未满
                       10 年时退化为扩张窗口(只用已有历史), 与真实投资者当年可得信息一致。
  股债收益比 ratio    = 股息率(%) ÷ 10Y国债(%)。

原始缓存属**数据类**, 按 AGENTS.md §8 留在仓库 data/_bt_caches/, 与 core_rotation 共用。
"""
import os
import sys
from datetime import datetime

import numpy as np
import pandas as pd

_DIR = os.path.dirname(os.path.abspath(__file__))
if _DIR not in sys.path:
    sys.path.insert(0, _DIR)

import rule  # noqa: E402

_UA = {"User-Agent": ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                      "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120 Safari/537.36")}


def _cache(name):
    os.makedirs(rule.CACHE_DIR, exist_ok=True)
    return os.path.join(rule.CACHE_DIR, name)


def csi_index(code, start=None):
    """中证官网日频 -> DataFrame(date, close, peg); 有本地缓存则直接用(回测不联网)。"""
    start = start or rule.START
    p = _cache(f"csi_{code}.csv")
    if os.path.exists(p):
        return pd.read_csv(p, parse_dates=["date"])
    import requests
    url = (f"https://www.csindex.com.cn/csindex-home/perf/index-perf?indexCode={code}"
           f"&startDate={start.replace('-', '')}&endDate={datetime.now():%Y%m%d}")
    raw = requests.get(url, headers=_UA, timeout=20).json().get("data") or []
    df = pd.DataFrame(raw)
    if df.empty:
        raise RuntimeError(f"中证官网返回空数据: indexCode={code}")
    df["date"] = pd.to_datetime(df["tradeDate"], format="%Y%m%d")
    df["close"] = pd.to_numeric(df["close"], errors="coerce")
    df["peg"] = pd.to_numeric(df["peg"], errors="coerce")
    df = df[["date", "close", "peg"]].dropna(subset=["close"]).sort_values("date")
    df.to_csv(p, index=False, encoding="utf-8")
    return df.reset_index(drop=True)


def bond_10y_monthly():
    """月末 10Y 国债收益率 -> Series(index=date)。列名兼容历史落库格式。"""
    p = _cache("bond10y_m.csv")
    raw = pd.read_csv(p)
    if "date" not in raw.columns:
        raw = raw.rename(columns={raw.columns[0]: "date"})
    s = pd.Series(pd.to_numeric(raw["close"], errors="coerce").values,
                  index=pd.to_datetime(raw["date"])).dropna().sort_index()
    return s


def _daily_percentile(peg, window):
    """peg -> 过去最多 window 个观测的累计分位(0-1)。"""
    v = peg.to_numpy(dtype=float)
    n = len(v)
    out = np.full(n, np.nan)
    lo = 0
    for i in range(n):
        if i - window + 1 > lo:
            lo = i - window + 1
        w = v[lo:i + 1]
        out[i] = float((w < v[i]).mean())
    return pd.Series(out, index=peg.index)


def load_series(start=None, end=None):
    """构建指标日频面板 -> DataFrame(index=date, columns: price/tr/peg/y10/dy/pe_pct/ratio)。"""
    px = csi_index(rule.ASSET["price_index"], start)
    tr = csi_index(rule.ASSET["total_index"], start)
    px = px.dropna(subset=["close"]).set_index("date")
    tr = tr.dropna(subset=["close"]).set_index("date")
    idx = px.index.intersection(tr.index)
    df = pd.DataFrame({
        "price": px.loc[idx, "close"].astype(float),
        "tr": tr.loc[idx, "close"].astype(float),
        "peg": px.loc[idx, "peg"].astype(float),
    }).sort_index()
    # 股息率 TTM 代理
    ratio_tr = df["tr"] / df["price"]
    df["dy"] = (ratio_tr / ratio_tr.shift(rule.DY_LOOKBACK) - 1) * 100
    # PE 分位(过去最多 10 年)
    df["pe_pct"] = _daily_percentile(df["peg"], rule.PE_WINDOW_DAYS)
    # 10Y 国债 -> 日频 ffill
    y10 = bond_10y_monthly()
    df["y10"] = y10.reindex(df.index, method="ffill")
    df["ratio"] = df["dy"] / df["y10"]
    if end is not None:
        df = df.loc[:pd.Timestamp(end)]
    return df


def zones_at(row):
    """指标行 -> {指标: zone} + 合成 zone。"""
    z = {k: rule.ZONE_FN[k](row[k]) for k in rule.IND_KEYS}
    z["combo"] = rule.compose_zone({k: z[k] for k in rule.IND_KEYS})
    return z


def latest(series=None):
    """最新一期的指标值 + 分区(用于 daily 兜底或交叉验证)。"""
    s = load_series() if series is None else series
    row = s.dropna(subset=["dy", "ratio"]).iloc[-1]
    z = zones_at(row)
    return dict(as_of=str(row.name.date()), dy=float(row["dy"]),
                pe_pct=float(row["pe_pct"]), ratio=float(row["ratio"]),
                y10=float(row["y10"]), peg=float(row["peg"]), zones=z)


if __name__ == "__main__":
    import io
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8")
    s = load_series()
    print(f"面板: {s.index[0].date()} ~ {s.index[-1].date()} · {len(s)} 行")
    print(s[["price", "peg", "y10", "dy", "pe_pct", "ratio"]].tail(5).round(4).to_string())
    d = latest(s)
    print("\n最新: ", d["as_of"], "dy=%.2f%% pe_pct=%.1f%% ratio=%.2f y10=%.2f%%"
          % (d["dy"], d["pe_pct"] * 100, d["ratio"], d["y10"]))
    print("分区: ", {rule.IND_NAMES[k]: rule.ZONE_CN[v] for k, v in d["zones"].items() if k != "combo"})
    print("合成: ", rule.ZONE_CN[d["zones"]["combo"]])
