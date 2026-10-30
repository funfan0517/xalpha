# -*- coding: utf-8 -*-
"""成长/红利风格轮动 v2 · 行情读取（**只读** v0 的共享指数缓存）

指数日线缓存 `../v0/data/_gd_index_klines.json` 是**跨版本共享数据**（v0/v1/v2 同一份信号源），
归 `../v0/data.py` 抓取与维护；本变种不自建缓存，避免多版数据分叉。

刷新：python strategies/growth_div_rotation/v2_absolute_bands/data.py --refresh
      （等价于转调 ../v0/data.py --refresh）
用法：from data import load_wide; wide = load_wide()
"""
import io
import json
import os
import subprocess
import sys

try:
    sys.stdout.reconfigure(encoding="utf-8")
except (AttributeError, OSError):
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8")

_DIR = os.path.dirname(os.path.abspath(__file__))
_PARENT = os.path.dirname(_DIR)                    # 家族根 strategies/growth_div_rotation
_V0 = os.path.join(_PARENT, "v0")                  # 缓存与抓取器的唯一所有者

import pandas as pd  # noqa: E402

import rule  # noqa: E402  （本变种自己的 rule.py）

CACHE = os.path.join(_V0, "data", "_gd_index_klines.json")

# 指数点位的合理下界：中证红利从未低于 1000 点，创业板指从未低于 500 点。
# 缓存里读到个位数 = 抓到同代码**个股**（历史上真发生过：000922 被当成佳电股份），
# 必须在入口 fail-fast，不能静默污染回测。
_MIN_SANE = {"399006": 500.0, "000922": 1000.0, "000688": 500.0}


def load_wide(codes=None):
    """缓存 -> 对齐收盘宽表（index=日期, columns=code），只做 ffill 对齐。"""
    codes = codes or [rule.GROWTH_INDEX, rule.DIV_INDEX]
    if not os.path.exists(CACHE):
        raise FileNotFoundError(
            f"缺少指数缓存 {CACHE}；先跑 python strategies/growth_div_rotation/v0/data.py --refresh")
    cache = json.load(open(CACHE, encoding="utf-8"))
    series = {}
    for code in codes:
        d = cache.get(code)
        if not d or len(d["close"]) < 30:
            raise KeyError(f"缓存缺 {code} 或样本不足 30 根；先跑 data.py --refresh")
        s = pd.Series(d["close"], index=pd.to_datetime(d["dates"]), dtype=float)
        s = s[~s.index.duplicated(keep="last")].sort_index()
        low = _MIN_SANE.get(code)
        if low is not None and s.iloc[0] < low:
            raise ValueError(
                f"{code} 首值 {s.iloc[0]:.2f} 低于合理下界 {low}："
                "缓存疑似存了同代码个股的股价（交易所前缀错判），请重跑 data.py --refresh")
        series[code] = s
    return pd.DataFrame(series).sort_index().ffill()


def refresh():
    """转调 v0 的抓取器（唯一抓取实现，避免各版各抓一套）。"""
    r = subprocess.run([sys.executable, os.path.join(_V0, "data.py"), "--refresh"],
                       cwd=_V0, check=False)
    return r.returncode


if __name__ == "__main__":
    if "--refresh" in sys.argv:
        sys.exit(refresh())
    wide = load_wide()
    print(wide.tail(3).to_string())
    print(f"bars={len(wide)} {wide.index.min().date()}~{wide.index.max().date()}")
