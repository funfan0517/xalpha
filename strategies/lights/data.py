# -*- coding: utf-8 -*-
r"""标的池与行情面板（三个策略共用）。

标的池唯一入口是 data/_universe.md（由 pipeline/universe.py 派生），**禁止在此硬编码**。

两条数据路径, 保持与原实现一致（无损合并阶段不改变任何取数口径）:
  * load_bars(code)  —— 逐标的 `xa.get_daily` 原始日线。
  * load_panels()    —— 全池宽表面板, 本地缓存（命中缓存时不联网）。
"""
import json
import os
import sys
from datetime import datetime

import pandas as pd

import xalpha as xa

_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)
from pipeline import universe  # noqa: E402

START = "2015-01-01"          # 抓取起点（提前给指标 warm-up）
SAMPLE_FROM = "2016-09-01"    # 样本窗口起点（最长十年口径）
# 面板缓存: 命中则离线, --refresh 或文件缺失时联网抓取。
CACHE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "data", "_lights_klines.json")

_FIELDS = ("open", "high", "low", "close", "volume")

# 唯一池: data/_universe.md 中「有场内对应」的行
ALL_POOL = universe.inner_rows()

# ---- 标的池口径（2026-09-10 收窄）----
# 按类别审计（10 年样本, 单笔口径）: 只有 A 股行业产生正超额
#   行业 +2.6% / 宽基 -1.2% / 全球QDII -8.5% / 策略商品 -8.5% / 债券 -4.1%
# 原因: QDII(标普/纳指/日经) 与商品(黄金/白银) 是长期单边上涨资产,
#   任何降低暴露的择时都是负贡献 —— 这是该类策略的固有属性, 不是参数问题。
# 恢复全池: 把 KEEP_CATS 设为 None（下游一切按 POOL 驱动, 无需改别处）。
KEEP_CATS = ("A股行业",)

POOL = [r for r in ALL_POOL if KEEP_CATS is None or r["cat"] in KEEP_CATS]
CATS = {r["code"]: r["cat"] for r in POOL}
NAMES = {r["code"]: r["inner_name"] for r in POOL}
THEMES = {r["code"]: r["theme"] for r in POOL}
CODES = [r["code"] for r in POOL]


def sh(code):
    """6 位场内代码 -> xalpha 前缀格式（SH/SZ）。"""
    return ("SH" if code.startswith(("5", "6", "9")) else "SZ") + code


def load_bars(code, start=START):
    """标的代码 -> 日线 DataFrame(date/open/high/low/close/volume, 升序, RangeIndex)。"""
    df = xa.get_daily(sh(code), start=start)
    df = df.dropna(subset=["close", "open", "high", "low", "volume"])
    df["date"] = pd.to_datetime(df["date"])
    df = df.sort_values("date").reset_index(drop=True)
    return df[["date", "open", "high", "low", "close", "volume"]].reset_index(drop=True)


def fetch_all(codes=None):
    """逐标的抓取日线 -> {code: {dates, open, high, low, close, volume}}。"""
    out = {}
    for code in (codes or CODES):
        try:
            df = load_bars(code)
        except Exception as e:  # noqa: BLE001 - 单标的失败仅记录，不影响全池
            print(f"[warn] {code} 抓取失败: {type(e).__name__}: {e}", file=sys.stderr)
            continue
        out[code] = {"dates": [str(d.date()) for d in df["date"]]}
        for f in _FIELDS:
            out[code][f] = [float(x) for x in df[f].to_list()]
    return out


def load_panels(refresh=False, cache=CACHE, ffill=True):
    """OHLCV 宽表: {field: DataFrame(index=日期, columns=code)}。

    缺失值处理:
      ffill=True: 价格类前向填充（停牌沿用前收），成交量 NaN 记 0。
      ffill=False: **保持原样**（NaN 不填）—— 统一策略用这个, 再按标的丢弃 NaN 行,
        即「标的自身交易日」序列。避免停牌日被补齐后 volume=0 污染量比类因子。
    """
    if not refresh and os.path.exists(cache):
        raw = json.load(open(cache, encoding="utf-8"))
    else:
        raw = fetch_all()
        if raw:
            os.makedirs(os.path.dirname(cache), exist_ok=True)
            with open(cache, "w", encoding="utf-8") as fh:
                json.dump(raw, fh, ensure_ascii=False)
    if not raw:
        sys.exit("无可用行情数据（抓取失败且无缓存）")
    keep = set(CODES)                       # 只保留当前池子, 防止旧缓存的已剔除标的混入
    raw = {k: v for k, v in raw.items() if k in keep}
    panels = {}
    for f in _FIELDS:
        ser = {}
        for code, d in raw.items():
            if f not in d:
                continue
            s = pd.Series(d[f], index=pd.to_datetime(d["dates"]), dtype=float)
            s = s[~s.index.duplicated(keep="last")].sort_index()
            ser[code] = s
        df = pd.DataFrame(ser).sort_index()
        if not ffill:
            panels[f] = df
        else:
            panels[f] = df.fillna(0.0) if f == "volume" else df.ffill()
    return panels


def complete_end(idx, close_hour=15):
    """最后一个「已收盘」交易日（回测用，避免盘中半日 bar 污染指标）。

    若最后一根 bar 的日期就是今天且当前未到收盘（<15:00），说明它是盘中未完成 bar
    （成交量/涨幅都会失真，例如成交额只有正常日的 5~6 成），回测应回退一天。
    """
    if len(idx) == 0:
        return None
    last = idx[-1]
    now = datetime.now()
    if last.date() == now.date() and now.hour < close_hour:
        return idx[-2] if len(idx) > 1 else last
    return last
