# -*- coding: utf-8 -*-
"""中证A500 本地日频 PE 库（发布以来分位, 每日自动增量, 纯 Python 不依赖 MCP）。

数据链路:
  - 日频序列: 中证指数官网 index-perf 接口(indexCode=000510), 自 2024-09-23 发布起,
    返回全部交易日(实测 ~476 日), 字段 `peg` 为中证官方披露的近似静态市盈率。
  - 说明: 该口径与东财/蛋卷的 PE(TTM) 数值不同, 但**自始至终同一指标**, 故其"发布以来
    累计分位"是自洽且可比的高低度量; 报告会标注口径, 避免与 TTM 分位混淆。
  - 增量: 每次运行实时拉取(接口已返回全量), 成功后覆写 data/_a500_pe_hist.csv 本地缓存;
    拉取失败自动回退缓存, 绝不中断。
用法: python strategies/core_rotation/_a500_pe.py   # 打印最新分位与区间
"""
import os
import sys
from datetime import datetime

import pandas as pd

_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

_CSV = os.path.join(_ROOT, "data", "_a500_pe_hist.csv")
_UA = {"User-Agent": ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                      "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120 Safari/537.36")}


def fetch():
    """中证官网 -> DataFrame(date/close/peg), 升序。"""
    import requests
    end = datetime.now().strftime("%Y%m%d")
    url = ("https://www.csindex.com.cn/csindex-home/perf/index-perf?indexCode=000510"
           "&startDate=20240923&endDate=" + end)
    r = requests.get(url, headers=_UA, timeout=15)
    r.raise_for_status()
    rows = r.json().get("data") or []
    df = pd.DataFrame(rows)
    df["date"] = pd.to_datetime(df["tradeDate"], format="%Y%m%d")
    df["close"] = pd.to_numeric(df["close"], errors="coerce")
    df["peg"] = pd.to_numeric(df["peg"], errors="coerce")
    return df[["date", "close", "peg"]].dropna(subset=["peg", "close"])\
        .sort_values("date").reset_index(drop=True)


def load_or_fetch():
    try:
        df = fetch()
        df.to_csv(_CSV, index=False, encoding="utf-8")
        return df
    except Exception as e:  # noqa: BLE001 - 自愈: 回退本地缓存
        if os.path.exists(_CSV):
            print(f"[i] A500 PE 日频拉取失败, 沿用本地缓存 {_CSV}: {type(e).__name__}: {str(e)[:100]}")
            return pd.read_csv(_CSV, parse_dates=["date"])
        raise


def daily_pe():
    """最新 PE / 发布以来累计分位(0-1) / 区间统计。"""
    df = load_or_fetch()
    pe = df["peg"].to_numpy(dtype=float)
    return dict(
        as_of=str(df["date"].iloc[-1].date()),
        pe=round(float(pe[-1]), 2),
        pe_pct=float((pe < pe[-1]).mean()),
        pe_min=round(float(pe.min()), 2),
        pe_max=round(float(pe.max()), 2),
        n=int(len(pe)),
        basis="中证官网日频 peg(近似静态PE), 2024-09 发布起; 非 TTM 口径",
    )


if __name__ == "__main__":
    import io
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8")
    d = daily_pe()
    print(f"A500 PE(官方日频) 最新={d['pe']} @ {d['as_of']} · "
          f"发布以来分位={d['pe_pct']:.1%} · 区间[{d['pe_min']}, {d['pe_max']}] · n={d['n']}")
    print(f"缓存: {_CSV}")
