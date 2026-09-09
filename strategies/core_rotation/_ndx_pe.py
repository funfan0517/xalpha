# -*- coding: utf-8 -*-
"""纳指100 Forward PE(12M 一致预期)本地时序库 —— 历史分位自动计算。

数据源: historyofmarket.com 公开 JSON 端点 /api/ndx/forward-pe.json (纯 Python, CC BY 4.0)
  - series `forward`  : 12-month blended-forward consensus P/E, 周频, 2001-04-27 至今(~1240 点)
  - series `forwardOwn`: 按其持仓自算的日频口径(2026-07 起), 仅作近期交叉参考
口径说明: 手册用「Forward PE 10 年分位」, 本模块用 `forward` 官方周频序列,
  按**近10年滚动窗口**与**2001年以来全窗**各算一个累计分位(0-1, 低=便宜)。
用法: python strategies/core_rotation/_ndx_pe.py
产物: data/_ndx_fwd_pe_hist.csv(本地缓存, 每日增量覆写; 拉取失败自动回退缓存)
"""
import os
import sys
from datetime import datetime, timedelta

import pandas as pd

_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

_URL = "https://historyofmarket.com/api/ndx/forward-pe.json"
_CSV = os.path.join(_ROOT, "data", "_ndx_fwd_pe_hist.csv")
_UA = {"User-Agent": ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                      "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120 Safari/537.36")}


def fetch():
    """返回 (series_df, meta)。series_df: date(周/日)/value/kind。"""
    import requests
    r = requests.get(_URL, headers=_UA, timeout=25)
    r.raise_for_status()
    j = r.json()
    fwd = pd.DataFrame(j["forward"])
    fwd["date"] = pd.to_datetime(fwd["date"])
    fwd["kind"] = "forward"
    own = pd.DataFrame(j.get("forwardOwn") or [])
    out = fwd[["date", "value", "kind"]].copy()
    if not own.empty:
        own["date"] = pd.to_datetime(own["date"])
        own["kind"] = "forwardOwn"
        out = pd.concat([out, own[["date", "value", "kind"]]], ignore_index=True)
    out = out.sort_values(["kind", "date"]).reset_index(drop=True)
    meta = dict(updated=j.get("updated"), current=j.get("current"),
                history_starts=j.get("historyStarts"),
                source=(j.get("source") or {}).get("method"))
    return out, meta


def load_or_fetch():
    try:
        df, meta = fetch()
        df.to_csv(_CSV, index=False, encoding="utf-8")
        return df, meta
    except Exception as e:  # noqa: BLE001 - 自愈: 回退本地缓存
        if os.path.exists(_CSV):
            print(f"[i] NDX Forward PE 拉取失败, 沿用本地缓存: {type(e).__name__}: {str(e)[:100]}")
            return pd.read_csv(_CSV, parse_dates=["date"]), {}
        raise


def daily_pe():
    """官方周频 Forward PE 的当前值 + 近10年/2001年以来分位。"""
    df, meta = load_or_fetch()
    s = df[df["kind"] == "forward"].copy().sort_values("date")
    if len(s) < 30:
        raise RuntimeError("NDX forward 序列过短")
    last = s.iloc[-1]
    pe_now = float(last["value"])
    as_of = str(last["date"].date())
    cutoff = pd.Timestamp(datetime.now() - timedelta(days=365 * 10))
    ten = s[s["date"] >= cutoff]
    vals = s["value"].to_numpy(dtype=float)
    return dict(
        as_of=as_of,
        pe=round(pe_now, 2),
        pe_pct_10y=float((ten["value"].to_numpy() < pe_now).mean()),
        pe_pct_full=float((vals < pe_now).mean()),
        n_10y=int(len(ten)), n_full=int(len(s)),
        updated=meta.get("updated"),
        basis="historyofmarket /api/ndx/forward-pe.json · 官方周频 12M 一致预期 Forward PE (CC BY 4.0)",
        source=(meta.get("source") or ""),
    )


if __name__ == "__main__":
    import io
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8")
    d = daily_pe()
    print(f"NDX Forward PE(官方周频)={d['pe']} @ {d['as_of']} · 近10年分位 {d['pe_pct_10y']:.1%}"
          f"(n={d['n_10y']}) · 2001以来 {d['pe_pct_full']:.1%}(n={d['n_full']})")
    print("basis:", d["basis"])
