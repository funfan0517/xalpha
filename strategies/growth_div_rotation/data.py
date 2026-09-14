# -*- coding: utf-8 -*-
"""成长/红利风格轮动 · 行情抓取与缓存（数据类 → 本策略 data/ 子目录）

抓取三个指数日线收盘（成长 创业板指/科创50 + 红利 中证红利），
落本地缓存，回测与每日信号共用同一份数据（不重复联网）。
用法: python strategies/growth_div_rotation/data.py [--refresh]
"""
import io
import json
import os
import sys

if not isinstance(sys.stdout, io.TextIOWrapper):
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8")

_DIR = os.path.dirname(os.path.abspath(__file__))
_ROOT = os.path.dirname(os.path.dirname(_DIR))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

import pandas as pd  # noqa: E402

import rule  # noqa: E402

CACHE = os.path.join(_DIR, "data", "_gd_index_klines.json")


def _sh(code):
    return rule.index_prefix(code)


def fetch_index(code, start="2015-01-01"):
    """单指数日线 -> DataFrame(date, close)。"""
    import xalpha as xa
    df = xa.get_daily(_sh(code), start=start)
    df = df.dropna(subset=["close"]).sort_values("date").reset_index(drop=True)
    df["date"] = pd.to_datetime(df["date"])
    return df[["date", "close"]].astype({"close": float})


def load_cache():
    if os.path.exists(CACHE):
        return json.load(open(CACHE, encoding="utf-8"))
    return {}


def save_cache(d):
    os.makedirs(os.path.dirname(CACHE), exist_ok=True)
    json.dump(d, open(CACHE, "w", encoding="utf-8"), ensure_ascii=False)


def refresh(start="2015-01-01", codes=None):
    """拉取并合并缓存；codes 缺省为 [成长默认, 红利, 科创50]。"""
    codes = codes or [rule.GROWTH_INDEX, rule.DIV_INDEX, rule.STAR50_INDEX]
    cache = load_cache()
    for code in codes:
        try:
            df = fetch_index(code, start=start)
            cache[code] = {
                "name": (rule.GROWTH_NAME if code == rule.GROWTH_INDEX
                         else rule.STAR50_NAME if code == rule.STAR50_INDEX
                         else rule.DIV_NAME),
                "dates": [d.strftime("%Y-%m-%d") for d in df["date"]],
                "close": df["close"].tolist(),
            }
            print(f"[ok] {code} {cache[code]['name']}: {len(df)} bars "
                  f"{cache[code]['dates'][0]}~{cache[code]['dates'][-1]}")
        except Exception as e:  # noqa: BLE001
            print(f"[FAIL] {code}: {type(e).__name__}: {str(e)[:160]}")
    save_cache(cache)
    return cache


def load_wide(codes=None):
    """缓存 -> 对齐的收盘宽表（index=日期, columns=code）。

    codes 顺序给出；缺数据或长度不足返回空 DataFrame（调用方自行判空）。
    """
    cache = load_cache()
    codes = codes or [rule.GROWTH_INDEX, rule.DIV_INDEX, rule.STAR50_INDEX]
    series = {}
    for code in codes:
        d = cache.get(code)
        if not d or len(d["close"]) < 30:
            continue
        s = pd.Series(d["close"], index=pd.to_datetime(d["dates"]), dtype=float)
        s = s[~s.index.duplicated(keep="last")].sort_index()
        series[code] = s
    if not series:
        return pd.DataFrame()
    return pd.DataFrame(series).sort_index().ffill()


def append_latest():
    """每日信号前刷新最新若干 bar（仅补增量，不重抓全量）。"""
    cache = load_cache()
    changed = False
    for code in [rule.GROWTH_INDEX, rule.DIV_INDEX, rule.STAR50_INDEX]:
        if code not in cache:
            continue
        last = cache[code]["dates"][-1]
        try:
            df = fetch_index(code, start=last)
            new_rows = df[df["date"] > pd.Timestamp(last)]
            if len(new_rows):
                cache[code]["dates"] += [d.strftime("%Y-%m-%d") for d in new_rows["date"]]
                cache[code]["close"] += new_rows["close"].tolist()
                changed = True
                print(f"[+{len(new_rows)}] {code} -> {cache[code]['dates'][-1]}")
        except Exception as e:  # noqa: BLE001
            print(f"[skip] {code} 增量刷新失败: {type(e).__name__}: {str(e)[:120]}")
    if changed:
        save_cache(cache)
    return cache


if __name__ == "__main__":
    if "--refresh" in sys.argv or not os.path.exists(CACHE):
        refresh()
    else:
        append_latest()
