#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""红利指数股息率月度历史 + 10Y 美债 —— 为 v1.0 利差分位提供真实序列

数据源与口径
------------
**股息率历史**：中证官网 **全收益指数 − 价格指数**的 250 日滚动差，即指数成分股
过去 12 个月**真实实现**的股息率（不含派息率假设）。

* 已验证存在全收益版本：``000922 → H00922``、``H30269 → H20269``。
* 其余跟踪指数（930955 / 931157 / 931468 / 000824 / 000825）官网**没有**全收益代码 →
  用中证红利(000922)的真实股息率**相对变化**做代理，水平用各自官网快照校准。
  这比「PE 反推 + 派息率恒定」更贴近 2024 年分红新规后的实际：实测 000922 上两种口径的
  利差分位相差 16.6pp（47.9% vs 64.5%）、日频差分相关系数 −0.35，PE 反推不可用。
* 港股 930914 的分红节奏与 A 股不同，**不做代理**（宁可缺，不可错）。

**10Y 美债**：FRED ``DGS10``（日频，1962 起），取月末值，缺失日向前填充。

产物（数据类，跨策略可用）
--------------------------
* ``data/_div_yield_m.csv``        date, index_code, dy, method(real/proxy), src_index
* ``data/_bt_caches/ust10y_m.csv`` date, close
* ``data/_bt_caches/csi_{code}.csv`` 指数日线缓存（date, close, peg），与 core_rotation 共用

用法::

    python strategies/dividend/yield_hist.py            # 增量刷新（缓存 3 天内直接复用）
    python strategies/dividend/yield_hist.py --force    # 强制全量重抓
"""

from __future__ import annotations

import argparse
import io
import os
import sys
from datetime import datetime

import numpy as np
import pandas as pd

_DIR = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, _DIR)
import pool  # noqa: E402  pylint: disable=wrong-import-position

_ROOT = os.path.dirname(os.path.dirname(_DIR))
CACHE = os.path.join(_ROOT, "data", "_bt_caches")
OUT_YIELD = os.path.join(_ROOT, "data", "_div_yield_m.csv")
OUT_UST = os.path.join(CACHE, "ust10y_m.csv")

START = "2014-01-01"          # 多取两年，保证 250 日滚动后仍有 10 年
TOTAL_MAP = {"000922": "H00922", "H30269": "H20269"}   # 已验证存在
BASE_INDEX = "000922"         # 代理基准（中证红利）
SKIP_PROXY = {"930914"}       # 港股：分红节奏不同，不做代理
UA = {"User-Agent": "Mozilla/5.0"}


# ------------------------------------------------------------------ 抓取
def _fetch_perf(code, retries=4):
    """中证官网 index-perf -> DataFrame(date, close, peg)。SSL 偶发 EOF，退避重试。"""
    import time

    import requests

    url = (f"https://www.csindex.com.cn/csindex-home/perf/index-perf?indexCode={code}"
           f"&startDate={START.replace('-', '')}&endDate={datetime.now():%Y%m%d}")
    last = None
    for i in range(retries):
        try:
            data = requests.get(url, headers=UA, timeout=25).json().get("data") or []
        except Exception as exc:  # noqa: BLE001 - SSL 偶发 EOF，重试
            last = exc
            time.sleep(1.2 * (i + 1))
            continue
        if data:
            df = pd.DataFrame(data)
            df["date"] = pd.to_datetime(df["tradeDate"], format="%Y%m%d")
            df["close"] = pd.to_numeric(df["close"], errors="coerce")
            df["peg"] = pd.to_numeric(df.get("peg"), errors="coerce")
            return df[["date", "close", "peg"]].dropna(subset=["close"]).sort_values("date")
    if last is not None:
        raise RuntimeError(f"{code} 抓取失败: {last}")
    return None


def csi_series(code, force=False, max_stale_days=3):
    """指数日线 close 序列（缓存优先，过期重抓）。"""
    os.makedirs(CACHE, exist_ok=True)
    path = os.path.join(CACHE, f"csi_{code}.csv")
    if os.path.exists(path) and not force:
        df = pd.read_csv(path, parse_dates=["date"])
        if df["date"].iloc[-1] >= pd.Timestamp.today() - pd.Timedelta(days=max_stale_days):
            return df.set_index("date")["close"].astype(float).dropna()
    new = _fetch_perf(code)
    if new is None or new.empty:
        if os.path.exists(path):
            df = pd.read_csv(path, parse_dates=["date"])
            print(f"[yield] {code} 抓取为空，沿用缓存（至 {df['date'].iloc[-1].date()}）")
            return df.set_index("date")["close"].astype(float).dropna()
        raise RuntimeError(f"{code} 无数据且无缓存")
    new.to_csv(path, index=False, encoding="utf-8")
    return new.set_index("date")["close"].astype(float).dropna()


def _ust_from_fred(retries=3):
    """FRED DGS10 日频；SSL 偶发 EOF，重试。"""
    import time

    import requests

    last = None
    for i in range(retries):
        try:
            txt = requests.get("https://fred.stlouisfed.org/graph/fredgraph.csv?id=DGS10",
                               timeout=30).text
            if "DGS10" in txt and len(txt) > 1000:
                return txt
        except Exception as exc:  # noqa: BLE001 - 换源前先重试
            last = exc
            time.sleep(1.5 * (i + 1))
    raise RuntimeError(f"FRED 抓取失败: {last}")


def _ust_from_treasury(years):
    """美国财政部官方 CSV（按年）—— FRED 不可达时的备用源。"""
    import time

    import requests

    frames = []
    for y in years:
        url = ("https://home.treasury.gov/resource-center/data-chart-center/interest-rates/"
               f"daily-treasury-rates.csv/{y}/all?type=daily_treasury_yield_curve"
               f"&field_tdr_date_value={y}&page&_format=csv")
        try:
            r = requests.get(url, timeout=30)
            if r.status_code == 200 and "10 Yr" in r.text:
                frames.append(pd.read_csv(io.StringIO(r.text)))
        except Exception:  # noqa: BLE001 - 单年失败跳过
            pass
        time.sleep(0.4)
    if not frames:
        raise RuntimeError("财政部 CSV 也取不到")
    df = pd.concat(frames, ignore_index=True)
    df["date"] = pd.to_datetime(df["Date"], format="%m/%d/%Y", errors="coerce")
    df["close"] = pd.to_numeric(df["10 Yr"], errors="coerce")
    s = df.dropna(subset=["date"]).set_index("date")["close"].sort_index().ffill().dropna()
    return s.rename("close").reset_index()


def ust10y(force=False, max_stale_days=5):
    """10Y 美债月末序列（%）。主源 FRED DGS10，备源美国财政部官方 CSV。"""
    os.makedirs(CACHE, exist_ok=True)
    if os.path.exists(OUT_UST) and not force:
        df = pd.read_csv(OUT_UST, parse_dates=["date"])
        if df["date"].iloc[-1] >= pd.Timestamp.today() - pd.Timedelta(days=max_stale_days):
            return df.set_index("date")["close"].astype(float).dropna()

    try:
        txt = _ust_from_fred()
        df = pd.read_csv(io.StringIO(txt))
        df.columns = ["date", "close"]
        df["date"] = pd.to_datetime(df["date"], errors="coerce")
        df["close"] = pd.to_numeric(df["close"], errors="coerce")
        s = df.dropna(subset=["date"]).set_index("date")["close"].ffill().dropna()
        src = "FRED DGS10"
    except Exception as exc:  # noqa: BLE001 - 切备用源
        print(f"[yield] FRED 不可用（{type(exc).__name__}），改用美国财政部 CSV")
        this_year = datetime.now().year
        s = _ust_from_treasury(range(this_year - 11, this_year + 1)) \
            .set_index("date")["close"].astype(float).dropna()
        src = "US Treasury"

    m = s.resample("ME").last().dropna()
    out = m.rename("close").reset_index()
    out.to_csv(OUT_UST, index=False, encoding="utf-8")
    print(f"[yield] 10Y 美债（{src}）-> {os.path.relpath(OUT_UST, _ROOT)} "
          f"最新 {m.iloc[-1]:.2f}% ({m.index[-1].date()})")
    return m


# ------------------------------------------------------------------ 股息率
def _implied_yield(price, total):
    """全收益 − 价格 的 250 日滚动差(%) = 实现股息率。"""
    d = pd.concat([price.rename("p"), total.rename("t")], axis=1, join="inner").dropna()
    dy = ((d["t"] / d["t"].shift(250) - 1) - (d["p"] / d["p"].shift(250) - 1)) * 100
    return dy.dropna()


def build(force=False):
    # valuation() 以**基金代码**为键，这里映射到跟踪指数代码
    val = pool.valuation()
    snap = {}
    for rec in pool.POOL:
        if rec.get("csi"):
            dy = (val.get(rec["code"]) or {}).get("dy")
            if dy is not None:
                snap.setdefault(rec["csi"], dy)
    real = {}
    for idx, total in TOTAL_MAP.items():
        try:
            real[idx] = _implied_yield(csi_series(idx, force), csi_series(total, force))
        except Exception as exc:  # noqa: BLE001 - 单个指数失败不阻断
            print(f"[yield] {idx}/{total} 失败: {exc}")

    base = real.get(BASE_INDEX)
    if base is None or base.empty:
        raise RuntimeError("中证红利(000922)真实股息率序列缺失，无法构建代理")

    rows = []
    for idx in sorted({r["csi"] for r in pool.POOL if r["csi"]}):
        dy_now = snap.get(idx)
        if dy_now is None:
            print(f"[yield] {idx} 无股息率快照，跳过")
            continue
        if idx in real:
            ser, method = real[idx], "real"
        elif idx in SKIP_PROXY:
            print(f"[yield] {idx} 港股，不做代理，留空")
            continue
        else:
            ser, method = base * (dy_now / base.iloc[-1]), "proxy"
        # 水平校准到官网快照（保形状、统一水平；单调变换不改分位）
        ser = ser * (dy_now / ser.iloc[-1])
        monthly = ser.resample("ME").last().dropna()
        for ts, val in monthly.items():
            rows.append({
                "date": ts.strftime("%Y-%m-%d"),
                "index_code": idx,
                "dy": round(float(val), 4),
                "method": method,
                "src_index": BASE_INDEX if method == "proxy" else idx,
            })

    df = pd.DataFrame(rows).sort_values(["index_code", "date"])
    os.makedirs(os.path.dirname(OUT_YIELD), exist_ok=True)
    df.to_csv(OUT_YIELD, index=False, encoding="utf-8")
    for idx, g in df.groupby("index_code"):
        print(f"[yield] {idx:8s} {g['method'].iloc[0]:5s} {len(g):4d} 月 "
              f"{g['date'].iloc[0]} ~ {g['date'].iloc[-1]}  最新 {g['dy'].iloc[-1]:.2f}%")
    print(f"[yield] 产物 -> {os.path.relpath(OUT_YIELD, _ROOT)}")
    return df


def load(force=False):
    """未过期的本地缓存直接读，否则重建。"""
    if os.path.exists(OUT_YIELD) and not force:
        df = pd.read_csv(OUT_YIELD, parse_dates=["date"])
        if df["date"].max() >= pd.Timestamp.today() - pd.Timedelta(days=35):
            return df
    return build(force=force)


def series(df, index_code):
    g = df[df["index_code"] == index_code]
    return g.set_index("date")["dy"].astype(float).sort_index()


def main():
    ap = argparse.ArgumentParser(description="构建股息率月度历史与 10Y 美债")
    ap.add_argument("--force", action="store_true", help="强制全量重抓")
    args = ap.parse_args()
    ust10y(force=args.force)
    build(force=args.force)


if __name__ == "__main__":
    main()
