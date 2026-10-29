#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""V9 · 数据层（采集 + 缓存）—— 全部免 key、不耗 mx 积分

四个源，对应 `implementation_rules.md` §3：

| 用途 | 端点 | 落到 |
|---|---|---|
| 指数日频 OHLC + PE(`peg`) | 中证官网 `index-perf` | `v9_temp5/data/_idx/<code>.csv` |
| 标普系价格代理（ETF/LOF） | 新浪 `getKLineData` | `v9_temp5/data/_sina/<symbol>.csv` |
| 10Y 中债（日频） | 东财 `RPTA_WEB_TREASURYYIELD` | `data/_bt_caches/bond10y_d.csv`（跨策略数据类） |
| 10Y 美债（月频，港股锚） | 复用 `ust10y_m.csv` | 只读 |

东财 `push2his` 日 K 在本机会 `ProxyError`（规则 §2 已记录），故指数价格一律走中证官网
（同一接口连 PE 一起给，口径最干净），仅两个标普系列用新浪 ETF/LOF 价格代理。

安全阀（规则 §3.3）：**新浪只放行 5/1 开头的 6 位场内代码**，指数代码一律不走新浪 ——
新浪上指数代码会命中同号股票或返回错位序列。

缓存策略：
* 新鲜度 3 天（盘中 15:10 前当天的半根 K 由 `factors.align_asof` 剔除，不在这里处理）；
* `--refresh` 强制重抓；抓取失败**沿用旧缓存**并在 stderr 提示，不静默假装成功。
"""
from __future__ import annotations

import os
import sys
import time
from datetime import date, timedelta

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
except ImportError:                                  # pragma: no cover - requests 是硬依赖
    _SESSION = None

_START = "20100101"
_CSI_URL = "https://www.csindex.com.cn/csindex-home/perf/index-perf"
_SINA_URL = ("https://money.finance.sina.com.cn/quotes_service/api/json_v2.php/"
             "CN_MarketData.getKLineData")
_BOND_URL = "https://datacenter.eastmoney.com/api/data/get"


def _warn(msg: str) -> None:
    print(f"[v9:warn] {msg}", file=sys.stderr)


def _get_json(url: str, params: dict, referer: str, tries: int = 4, timeout: int = 25):
    """带退避重试的 GET -> json（规则 R2-3：单点失败不许炸掉整轮采集）。"""
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
        except Exception as exc:  # noqa: BLE001 - SSL EOF / ProxyError 靠重试消化
            last = exc
        time.sleep(1.2 * (i + 1))
    raise RuntimeError(f"GET 失败 {url}: {last}")


# ------------------------------------------------------------------ 中证指数日频
def fetch_index_perf(code: str, start: str = _START) -> pd.DataFrame:
    """中证官网日频 -> DataFrame(date, close, high, low, pe)。非中证系返回空表。"""
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
            out.append({
                "date": dt,
                "close": float(x.get("close")),
                "high": float(x.get("high") or x.get("close")),
                "low": float(x.get("low") or x.get("close")),
                "pe": float(x["peg"]) if x.get("peg") not in (None, "") else float("nan"),
            })
        except (TypeError, ValueError):             # 坏行跳过，不整批作废（R2-4）
            continue
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
        df = fetch_index_perf(code)
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


# ------------------------------------------------------------------ 新浪价格代理
def fetch_sina(symbol: str, datalen: int = 3000) -> pd.DataFrame:
    """新浪日 K -> DataFrame(date, open, close, high, low)。只接受 5/1 开头的场内代码。"""
    num = str(symbol)[-6:]
    if len(num) != 6 or num[0] not in ("5", "1"):
        raise ValueError(f"安全阀：新浪只放行 ETF/LOF 代码，收到 {symbol}")
    rows = _get_json(_SINA_URL,
                     {"symbol": symbol, "scale": "240", "ma": "no", "datalen": str(datalen)},
                     referer="https://finance.sina.com.cn/")
    out = []
    for x in rows or []:
        try:
            out.append({"date": x["day"][:10], "open": float(x["open"]), "close": float(x["close"]),
                        "high": float(x["high"]), "low": float(x["low"])})
        except (KeyError, TypeError, ValueError):
            continue
    if not out:
        return pd.DataFrame(columns=["date", "open", "close", "high", "low"])
    df = pd.DataFrame(out)
    df["date"] = pd.to_datetime(df["date"])
    return df.sort_values("date").reset_index(drop=True)


def load_proxy(symbol: str, refresh: bool = False, days: int = 3) -> pd.DataFrame:
    """标普系价格代理（缓存优先）。"""
    os.makedirs(rule.SINA_DIR, exist_ok=True)
    path = os.path.join(rule.SINA_DIR, f"{symbol}.csv")
    if not refresh and _cache_fresh(path, days):
        return pd.read_csv(path, parse_dates=["date"])
    try:
        df = fetch_sina(symbol)
        if df.empty:
            raise RuntimeError("返回空数据")
        df.to_csv(path, index=False, encoding="utf-8")
        return df
    except Exception as exc:  # noqa: BLE001
        if os.path.exists(path):
            old = pd.read_csv(path, parse_dates=["date"])
            _warn(f"{symbol} 抓取失败（{type(exc).__name__}），沿用缓存至 {old['date'].max():%Y-%m-%d}")
            return old
        _warn(f"{symbol} 抓取失败且无缓存：{type(exc).__name__}: {exc}")
        return pd.DataFrame(columns=["date", "open", "close", "high", "low"])


def load_proxy_best(candidates, refresh: bool = False, days: int = 3):
    """多候选价格代理（标普系）：逐个抓取，取**历史最长**的可用序列作主代理，
    其余作交叉校验 / 回退。返回 (df, meta)。

    meta = {chosen, used_fallback, candidates:{sym:n}, cross_corr}
      - chosen: 实际选用的 symbol（历史最长者）
      - used_fallback: chosen 是否不是列表里第一个（首选源失败才回退）
      - candidates: 各候选成功抓到的行数
      - cross_corr: chosen 与其余候选在重叠区的日收益相关系数均值（None 表示无第二源）
    """
    candidates = list(candidates)
    got = {}
    for sym in candidates:
        df = load_proxy(sym, refresh=refresh, days=days)
        if not df.empty:
            got[sym] = df
    meta = {"chosen": None, "used_fallback": False,
            "candidates": {s: len(df) for s, df in got.items()}, "cross_corr": None}
    if not got:
        return pd.DataFrame(columns=["date", "open", "close", "high", "low"]), meta

    chosen = max(got, key=lambda s: len(got[s]))
    meta["chosen"] = chosen
    meta["used_fallback"] = bool(candidates) and chosen != candidates[0]

    if len(got) > 1:
        base = got[chosen].set_index("date")["close"].rename("c")
        cors = []
        for s, df in got.items():
            if s == chosen:
                continue
            o = df.set_index("date")["close"].rename("o")
            m = pd.concat([base, o], axis=1).dropna()
            if len(m) >= 30:
                cors.append(m["c"].pct_change().corr(m["o"].pct_change()))
        if cors:
            meta["cross_corr"] = float(sum(cors) / len(cors))
    return got[chosen], meta


# ------------------------------------------------------------------ 10Y 国债
def fetch_bond10y(years: int = 12) -> pd.Series:
    """东财数据中心：中国 10 年期国债收益率（日频，%）。"""
    need_from = (date.today() - timedelta(days=365 * years)).strftime("%Y-%m-%d")
    out, page = {}, 1
    while page <= 30:
        d = _get_json(_BOND_URL,
                      {"type": "RPTA_WEB_TREASURYYIELD", "sty": "ALL", "st": "SOLAR_DATE",
                       "sr": "-1", "token": "894050c76af8597a853f5b408b759f5d",
                       "p": str(page), "ps": "500"},
                      referer="https://data.eastmoney.com/")
        rows = ((d or {}).get("result") or {}).get("data") or []
        if not rows:
            break
        for r in rows:
            dt = str(r.get("SOLAR_DATE") or "")[:10]
            v = r.get("EMM00166466")
            if dt and v not in (None, ""):
                try:
                    out[dt] = float(v)
                except (TypeError, ValueError):
                    continue
        if min(out) <= need_from or len(rows) < 500:
            break
        page += 1
    if not out:
        raise RuntimeError("国债收益率返回空")
    s = pd.Series(out, dtype=float)
    s.index = pd.to_datetime(s.index)
    return s.sort_index()


def load_bond_cn(refresh: bool = False, days: int = 4) -> pd.Series:
    """10Y 中债日频（%）—— 落到仓库 `data/_bt_caches/bond10y_d.csv`（数据类，跨策略可用）。"""
    path = rule.BOND_CN
    os.makedirs(os.path.dirname(path), exist_ok=True)
    if not refresh and _cache_fresh(path, days):
        df = pd.read_csv(path, parse_dates=["date"])
        return df.set_index("date")["close"].astype(float).sort_index()
    try:
        s = fetch_bond10y()
        s.rename("close").rename_axis("date").reset_index().to_csv(
            path, index=False, encoding="utf-8")
        return s
    except Exception as exc:  # noqa: BLE001
        if os.path.exists(path):
            df = pd.read_csv(path, parse_dates=["date"])
            _warn(f"10Y 中债抓取失败（{type(exc).__name__}），沿用缓存至 "
                  f"{df['date'].max():%Y-%m-%d}")
            return df.set_index("date")["close"].astype(float).sort_index()
        _warn(f"10Y 中债抓取失败且无缓存，退回月频 bond10y_m.csv：{type(exc).__name__}: {exc}")
        df = pd.read_csv(rule.BOND_CN_M)
        col = "close" if "close" in df.columns else df.columns[-1]
        idx = pd.to_datetime(df.iloc[:, 0])
        return pd.Series(pd.to_numeric(df[col], errors="coerce").to_numpy(), index=idx).dropna()


def load_bond_us(days: int = 60) -> pd.Series | None:
    """10Y 美债月频（%）—— 港股锚。缓存过旧（>60 天）返回 None，由调用方退回中债。"""
    if not os.path.exists(rule.BOND_US_M):
        return None
    df = pd.read_csv(rule.BOND_US_M)
    col = "close" if "close" in df.columns else df.columns[-1]
    s = pd.Series(pd.to_numeric(df[col], errors="coerce").to_numpy(),
                  index=pd.to_datetime(df.iloc[:, 0])).dropna()
    if s.empty or s.index[-1] < pd.Timestamp.today().normalize() - pd.Timedelta(days=days):
        _warn(f"10Y 美债缓存过旧（至 {s.index[-1]:%Y-%m-%d} 若可得），930914 改用中债锚")
        return None
    return s


def load_div_yield() -> dict:
    """指数月度股息率（`data/_div_yield_m.csv`，跨策略数据类）-> {index: {dy, method}}。

    * `real`  ：中证全收益指数 − 价格指数 的 250 日滚动差（000922/H30269 可用）；
    * `proxy` ：用 000922 的真实股息率变化率代理、水平用各自官网快照校准
                （930955/931157/931468/000824/000825）—— **形状近似中证红利**；
    * 港股 930914 与两个标普系列不在文件内，`spread` 退回 `100/PE − 10Y`。
    """
    if not os.path.exists(rule.DY_CSV):
        _warn(f"股息率历史缺失（{rule.DY_CSV}），spread 退回 100/PE − 10Y")
        return {}
    df = pd.read_csv(rule.DY_CSV, parse_dates=["date"])
    out = {}
    for code, g in df.groupby("index_code"):
        g = g.sort_values("date")
        method = str(g["method"].iloc[-1]) if "method" in g.columns else "unknown"
        out[str(code)] = {"dy": g.set_index("date")["dy"].astype(float),
                          "method": method}
    return out


# ------------------------------------------------------------------ 蛋卷指数估值（PB 历史）
_DJ_BASE = "https://danjuanfunds.com/djapi/index_eva"
_DJ_REF = "https://danjuanfunds.com/djmodule/value-center"


def _dj_series(payload, key: str, field: str) -> pd.Series:
    """蛋卷 history 响应 -> Series(index=日期, values=字段)。坏行跳过。"""
    rows = ((payload or {}).get("data") or {}).get(key) or []
    out = {}
    for x in rows:
        try:
            out[pd.to_datetime(int(x["ts"]), unit="ms").normalize()] = float(x[field])
        except (KeyError, TypeError, ValueError):
            continue
    return pd.Series(out).sort_index()


def load_danjuan_eva(refresh: bool = False, days: int = 7) -> pd.DataFrame:
    """蛋卷指数 PE/PB **周频**历史（免 token）-> DataFrame(date, index_code, pe, pb)。

    * 只覆盖 `rule.DJ_CODE` 里的 3 只（000922 / H30269 / 931157），其余 5 只无条目；
    * 周频（约 517 条 ≈ 10 年，自 2016-10），**不能当日频用**，只做口径对照；
    * 单只失败沿用旧缓存里该只的数据，全失败才整体回退。
    """
    cols = ["date", "index_code", "pe", "pb"]
    old = None
    if os.path.exists(rule.DJ_CSV):
        try:
            old = pd.read_csv(rule.DJ_CSV, parse_dates=["date"])
        except Exception:  # noqa: BLE001 - 坏缓存当没有
            old = None
    if not refresh and old is not None and len(old):
        if old["date"].max() >= pd.Timestamp.today().normalize() - pd.Timedelta(days=days):
            return old[cols]

    frames, ok = [], set()
    for idx_key, dj_code in rule.DJ_CODE.items():
        try:
            pe = _dj_series(_get_json(f"{_DJ_BASE}/pe_history/{dj_code}", {"day": "all"},
                                      referer=_DJ_REF), "index_eva_pe_growths", "pe")
            pb = _dj_series(_get_json(f"{_DJ_BASE}/pb_history/{dj_code}", {"day": "all"},
                                      referer=_DJ_REF), "index_eva_pb_growths", "pb")
            df = pd.concat([pe.rename("pe"), pb.rename("pb")], axis=1, join="inner").dropna()
            if df.empty:
                raise RuntimeError("PE/PB 序列无重叠")
            df = df.reset_index().rename(columns={"index": "date"})
            df["index_code"] = idx_key
            frames.append(df[cols])
            ok.add(idx_key)
        except Exception as exc:  # noqa: BLE001 - 单只失败不炸整轮
            _warn(f"蛋卷估值 {idx_key}({dj_code}) 失败：{type(exc).__name__}: {exc}")

    if not frames:
        if old is not None and len(old):
            _warn(f"蛋卷估值全失败，沿用缓存至 {old['date'].max():%Y-%m-%d}")
            return old[cols]
        _warn("蛋卷估值全失败且无缓存")
        return pd.DataFrame(columns=cols)

    out = pd.concat(frames, ignore_index=True)
    missed = set(rule.DJ_CODE) - ok
    if missed and old is not None and len(old):
        keep = old[old["index_code"].isin(missed)]
        if len(keep):
            _warn(f"蛋卷估值 {'/'.join(sorted(missed))} 本次失败，沿用缓存")
            out = pd.concat([out, keep[cols]], ignore_index=True)
    out = out.sort_values(["index_code", "date"]).reset_index(drop=True)
    os.makedirs(os.path.dirname(rule.DJ_CSV), exist_ok=True)
    out.to_csv(rule.DJ_CSV, index=False, encoding="utf-8")
    return out


def official_valuation() -> dict:
    """中证《指数估值》官方股息率（仅展示，不进温度）—— 复用 `pool.load_csindex_value`。

    akshare 是可选依赖：未安装 / 抓取失败时返回空 dict，展示层渲染 N/A。
    """
    try:
        sys.path.insert(0, os.path.dirname(_DIR))
        import pool  # noqa: E402  pylint: disable=import-outside-toplevel

        return pool.load_csindex_value()
    except Exception as exc:  # noqa: BLE001
        _warn(f"官方股息率不可用（{type(exc).__name__}: {exc}），展示层留空")
        return {}


def main(refresh: bool = False) -> int:
    """采集探针：抓一遍全部源，打印可得性（不做任何计算）。"""
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    print(f"[v9:data] 抓取 {len(rule.FUNDS)} 只标的的指数价格 / PE")
    for f in rule.FUNDS:
        if f["has_valuation"]:
            df = load_index(f["index"], refresh=refresh)
            n_pe = int(df["pe"].notna().sum())
            span = f"{df['date'].min():%Y-%m-%d}~{df['date'].max():%Y-%m-%d}" if len(df) else "空"
            print(f"  {f['code']} {f['short']:<10s} {f['index']:<10s} {len(df):5d} 根"
                  f"（含 PE {n_pe}）{span}")
        else:
            proxy = f["price_proxy"]
            if isinstance(proxy, (list, tuple)):
                df, meta = load_proxy_best(proxy, refresh=refresh)
                cand = " / ".join(f"{s}:{n}" for s, n in meta["candidates"].items())
                extra = "" if meta["cross_corr"] is None else f" · 交叉校验 corr={meta['cross_corr']:.2f}"
                print(f"  {f['code']} {f['short']:<10s} {meta['chosen'] or '—':<10s} "
                      f"{len(df):5d} 根（候选 {cand}{extra}）")
            else:
                df = load_proxy(proxy, refresh=refresh)
                span = f"{df['date'].min():%Y-%m-%d}~{df['date'].max():%Y-%m-%d}" if len(df) else "空"
                print(f"  {f['code']} {f['short']:<10s} {proxy:<10s} {len(df):5d} 根"
                      f"（价格温度，无估值）{span}")
    cn = load_bond_cn(refresh=refresh)
    print(f"[v9:data] 10Y 中债 {len(cn)} 条 {cn.index[0]:%Y-%m-%d}~{cn.index[-1]:%Y-%m-%d} "
          f"末值 {cn.iloc[-1]:.3f}%")
    us = load_bond_us()
    print(f"[v9:data] 10Y 美债 {'缺失（930914 退回中债锚）' if us is None else f'{us.iloc[-1]:.3f}%'}")
    return 0


if __name__ == "__main__":
    sys.exit(main("--refresh" in sys.argv))
