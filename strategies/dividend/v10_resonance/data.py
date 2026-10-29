#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""V10 · 数据层（指数日频 + 全收益指数日频，免 key）

只要一样东西：中证官网 `index-perf` 的日频收盘。价格指数出信号，全收益指数记收益，
两者相除反推股息率 —— 一个端点解决三个输入，口径最干净（`../v9_temp5/data.py` 同源）。

| 用途 | 端点 | 落到 |
|---|---|---|
| 价格指数（前高 / 年线 / 三年线） | 中证 `index-perf` `000922` / `H30269` | `v10_resonance/data/_idx/<code>.csv` |
| 全收益指数（收益 / 股息率分子） | 中证 `index-perf` `H00922` / `H20269` | 同上 |

缓存策略与 V9 一致：新鲜度 3 天；`--refresh` 强制重抓；抓取失败**沿用旧缓存**并在 stderr
提示，不静默假装成功。

不做的事
--------
* 不用新浪 ETF 价格做代理 —— 这里要的是指数，ETF 有费率与跟踪误差，且 4.5% 是绝对阈值。
* 不碰没有全收益序列的指数（930955 / 931157 / 931468 / 000824 / 000825 / 930914）：
  它们的股息率只能靠 000922 变化率代理，水平不可信，套绝对阈值等于瞎猜（`RULES.md §五`）。
"""
from __future__ import annotations

import os
import sys
import time
from datetime import date

import pandas as pd

_DIR = os.path.dirname(os.path.abspath(__file__))
if _DIR not in sys.path:
    sys.path.insert(0, _DIR)

import rule  # noqa: E402  pylint: disable=wrong-import-position

try:
    import requests
    import urllib3

    urllib3.disable_warnings()
    _SESSION = requests.Session()
    _SESSION.headers.update({
        "User-Agent": ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                       "(KHTML, like Gecko) Chrome/124.0 Safari/537.36"),
        "Accept": "application/json, text/plain, */*",
    })
    _SESSION.verify = False
except ImportError:                                    # pragma: no cover
    _SESSION = None

_START = "20050101"                                    # 20 年样本（000922 指数基点 2004-12-31）
_CSI_URL = "https://www.csindex.com.cn/csindex-home/perf/index-perf"


def _warn(msg: str) -> None:
    print(f"[v10:warn] {msg}", file=sys.stderr)


def _get_json(url: str, params: dict, referer: str, tries: int = 4, timeout: int = 40):
    """带退避重试的 GET -> json（单点失败不许炸掉整轮采集）。"""
    if _SESSION is None:
        raise RuntimeError("requests 不可用")
    last = None
    for i in range(tries):
        try:
            r = _SESSION.get(url, params=params, headers={"Referer": referer},
                             timeout=timeout, verify=False)
            if r.status_code == 200:
                return r.json()
            last = RuntimeError(f"HTTP {r.status_code}")
        except Exception as exc:  # noqa: BLE001 - SSL EOF 靠重试消化
            last = exc
        time.sleep(1.2 * (i + 1))
    raise RuntimeError(f"GET 失败 {url}: {last}")


def fetch_index(code: str, start: str = _START) -> pd.DataFrame:
    """中证官网日频 -> DataFrame(date, close, high, low, pe)。"""
    d = _get_json(_CSI_URL,
                  {"indexCode": code, "startDate": start,
                   "endDate": date.today().strftime("%Y%m%d")},
                  referer="https://www.csindex.com.cn/")
    rows = (d.get("data") or []) if isinstance(d, dict) else []
    seen, out = set(), []
    for x in rows:
        dt = str(x.get("tradeDate") or "")
        if not dt or dt in seen:
            continue
        seen.add(dt)
        try:
            close = float(x.get("close"))
        except (TypeError, ValueError):
            continue
        out.append({
            "date": dt,
            "close": close,
            "high": float(x.get("high") or close),
            "low": float(x.get("low") or close),
            "pe": float(x["peg"]) if x.get("peg") not in (None, "") else float("nan"),
        })
    if not out:
        return pd.DataFrame(columns=["date", "close", "high", "low", "pe"])
    df = pd.DataFrame(out)
    df["date"] = pd.to_datetime(df["date"], format="%Y%m%d")
    return df.sort_values("date").drop_duplicates("date", keep="last").reset_index(drop=True)


def _cache_fresh(path: str, days: int = 3) -> bool:
    if not os.path.exists(path):
        return False
    df = pd.read_csv(path, usecols=["date"], parse_dates=["date"])
    return bool(len(df)) and df["date"].max() >= pd.Timestamp.today().normalize() - pd.Timedelta(days=days)


def load_index(code: str, refresh: bool = False, days: int = 3) -> pd.DataFrame:
    """指数日频（缓存优先）-> DataFrame(date, close, high, low, pe)。"""
    os.makedirs(rule.IDX_DIR, exist_ok=True)
    path = os.path.join(rule.IDX_DIR, f"{code}.csv")
    if not refresh and _cache_fresh(path, days):
        return pd.read_csv(path, parse_dates=["date"])
    try:
        df = fetch_index(code)
        if df.empty:
            raise RuntimeError("返回空数据（该指数可能不在中证官网）")
        df.to_csv(path, index=False, encoding="utf-8")
        return df
    except Exception as exc:  # noqa: BLE001 - 失败沿用缓存
        if os.path.exists(path):
            old = pd.read_csv(path, parse_dates=["date"])
            _warn(f"{code} 抓取失败（{type(exc).__name__}: {exc}），沿用缓存至 "
                  f"{old['date'].max():%Y-%m-%d}")
            return old
        _warn(f"{code} 抓取失败且无缓存：{type(exc).__name__}: {exc}")
        return pd.DataFrame(columns=["date", "close", "high", "low", "pe"])


def official_dy() -> dict:
    """中证《指数估值》官方股息率（%），仅作口径校验，不进信号。

    akshare 是可选依赖：未安装 / 抓取失败返回空 dict。复用 `../pool.py` 的缓存层，
    避免每个变种各自抓一遍。
    """
    try:
        if rule.STRAT not in sys.path:
            sys.path.insert(0, rule.STRAT)
        import pool  # noqa: E402  pylint: disable=import-outside-toplevel
    except Exception as exc:  # noqa: BLE001
        _warn(f"pool 不可用（{type(exc).__name__}），跳过官方股息率校验")
        return {}
    try:
        snap = pool.load_csindex_value(codes=[t["index"] for t in rule.TARGETS])
    except Exception as exc:  # noqa: BLE001
        _warn(f"官方股息率不可用（{type(exc).__name__}: {exc}），展示层留空")
        return {}
    return {k: v.get("dy") for k, v in snap.items() if v.get("dy") is not None}


def main(refresh: bool = False) -> int:
    """采集探针：抓一遍价格指数 + 全收益指数，打印可得性与官方口径校验。"""
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    official = official_dy()
    print(f"[v10:data] 标的 {len(rule.TARGETS)} 个（价格指数 + 全收益指数）")
    for t in rule.TARGETS:
        px = load_index(t["index"], refresh=refresh)
        tr = load_index(t["total"], refresh=refresh)
        if px.empty or tr.empty:
            print(f"  {t['index']} {t['name']:<10s} 数据缺失（价格 {len(px)} / 全收益 {len(tr)}）")
            continue
        m = pd.concat([px.set_index("date")["close"].rename("p"),
                       tr.set_index("date")["close"].rename("t")], axis=1).dropna()
        ratio = m["t"] / m["p"]
        dy = (ratio / ratio.shift(rule.DY_WIN) - 1) * 100
        off = official.get(t["index"])
        print(f"  {t['index']} {t['name']:<10s} 价格 {len(px):5d} 根 {px['date'].min():%Y-%m-%d}~"
              f"{px['date'].max():%Y-%m-%d} · 全收益 {len(tr):5d} 根 · "
              f"日频股息率 {dy.notna().sum():5d} 天，末值 {dy.dropna().iloc[-1]:.2f}%"
              f"（官方快照 {off if off is None else f'{off:.2f}%'}）")
    return 0


if __name__ == "__main__":
    sys.exit(main("--refresh" in sys.argv))
