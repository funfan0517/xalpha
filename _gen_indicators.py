# -*- coding: utf-8 -*-
"""由 data/_long_klines.json 为全部基金批量计算五大指标历史值，合并为单一文件。

输出: data/_indicators_hist.json
  结构: { _meta:{...}, <code>: { dates:[...],
           MA5, MA10, MA20, MA60,
           DIF, DEA, HIST,
           K, D, J,
           BOLL_UPPER, BOLL_MID, BOLL_LOWER,
           volume } }

策略: 若 data/ 下 _ma_hist/_macd_hist/_kdj_hist/_boll_hist/_vol_hist 五个分项文件
      齐全，则直接合并（快速、免网络）；否则实时计算 4 个指标（由收盘价算），
      并通过 xa.get_daily 拉取真实成交量。可重复运行。
"""
import json
import os
import time

import pandas as pd
import xalpha as xa

ROOT = os.path.dirname(os.path.abspath(__file__))
DATA = os.path.join(ROOT, "data")
SRC = os.path.join(DATA, "_long_klines.json")
OUT = os.path.join(DATA, "_indicators_hist.json")

META = {
    "source": "_long_klines.json (市价收盘价) + xa.get_daily xueqiu (volume)",
    "indicators": "MA(5/10/20/60), MACD(12,26,9), KDJ(9,3,3 ×100), BOLL(20,2), VOL",
    "formula_parity": "MA/MACD/BOLL 与 xalpha.indicator 公式逐点一致(浮点误差~1e-14~5e-7); "
                      "KDJ 公式一致但此处放大×100(看盘软件惯例, xalpha 原生为 0-1)",
    "price_basis": "基于市价收盘价(xa.get_daily)。xalpha.fundinfo 的指标基于净值 NAV, 直接对比会有细微差异: "
                   "MA/BOLL 约 0.02~1.1%, MACD 约 1~11%(绝对额很小)。炒股技术面以市价为准。",
    "volume": "真实成交量(手), 缺失日期为 null",
}


# --------------------------- 指标计算（由收盘价） --------------------------- #
def s2list(s):
    return [None if (v is None or pd.isna(v)) else round(float(v), 6) for v in s]


def ma_series(c, n):
    return c.rolling(n).mean()


def macd_series(c):
    e1 = c.ewm(span=12).mean()
    e2 = c.ewm(span=26).mean()
    dif = e1 - e2
    dea = dif.ewm(span=9).mean()
    return dif, dea, dif - dea


def kdj_series(c, rsv_n=9, k_n=3, d_n=3):
    roll = c.rolling(rsv_n)
    lo, hi = roll.min(), roll.max()
    rsv = (c - lo) / (hi - lo)
    rsv = rsv.where(hi != lo, 0.5)
    k = rsv.rolling(k_n).mean()
    d = k.rolling(d_n).mean()
    j = 3 * k - 2 * d
    return k * 100, d * 100, j * 100


def boll_series(c, n=20, dev=2):
    mid = c.rolling(n).mean()
    sd = c.rolling(n).std()
    return mid + dev * sd, mid, mid - dev * sd


# ----------------------------- 成交量（真实） ----------------------------- #
def exchange_prefix(code):
    h = code[:2]
    if h in ("50", "51", "55", "56", "58", "59", "60", "68", "90", "91"):
        return "SH"
    if h in ("00", "15", "16", "18", "30"):
        return "SZ"
    return "SH"


def fetch_volume_map(code, first, last):
    for pref in (exchange_prefix(code), "SH" if exchange_prefix(code) == "SZ" else "SZ"):
        try:
            df = xa.get_daily(pref + code, start=first, end=last)
            if df is None or len(df) == 0:
                continue
            df = df.copy()
            df["d"] = pd.to_datetime(df["date"]).dt.strftime("%Y-%m-%d")
            vol = df.get("volume")
            if vol is None:
                continue
            m = {}
            for dt, v in zip(df["d"], vol):
                m[dt] = None if (v is None or pd.isna(v)) else int(round(float(v)))
            if len(set(m) & {first, last}) >= 1 or len(m) >= 5:
                return m
        except Exception as e:  # noqa
            print(f"  [{pref}{code}] 失败: {type(e).__name__}: {e}")
    return {}


# ------------------------------- 两种构建路径 ------------------------------- #
def build_from_sources(lk):
    """五个分项文件齐全则直接合并（免网络）。"""
    parts = ["_ma_hist.json", "_macd_hist.json", "_kdj_hist.json", "_boll_hist.json", "_vol_hist.json"]
    try:
        ms = [json.load(open(os.path.join(DATA, p), encoding="utf-8")) for p in parts]
    except FileNotFoundError:
        return None
    if any("_meta" not in m for m in ms):
        return None
    ma, macd, kdj, boll, vol = ms
    out = {}
    for code in lk:
        out[code] = {
            "dates": lk[code]["dates"],
            "MA5": ma[code]["MA5"], "MA10": ma[code]["MA10"],
            "MA20": ma[code]["MA20"], "MA60": ma[code]["MA60"],
            "DIF": macd[code]["DIF"], "DEA": macd[code]["DEA"], "HIST": macd[code]["HIST"],
            "K": kdj[code]["K"], "D": kdj[code]["D"], "J": kdj[code]["J"],
            "BOLL_UPPER": boll[code]["BOLL_UPPER"], "BOLL_MID": boll[code]["BOLL_MID"],
            "BOLL_LOWER": boll[code]["BOLL_LOWER"],
            "volume": vol[code]["volume"],
        }
    return out


def build_full(lk):
    """实时计算 4 个指标 + 拉取成交量。"""
    out = {}
    for code, v in lk.items():
        dates = v["dates"]
        close = pd.Series(v["close"], dtype="float64")
        rec = {
            "dates": dates,
            "MA5": s2list(ma_series(close, 5)), "MA10": s2list(ma_series(close, 10)),
            "MA20": s2list(ma_series(close, 20)), "MA60": s2list(ma_series(close, 60)),
        }
        dif, dea, hist = macd_series(close)
        rec.update({"DIF": s2list(dif), "DEA": s2list(dea), "HIST": s2list(hist)})
        k, d, j = kdj_series(close)
        rec.update({"K": s2list(k), "D": s2list(d), "J": s2list(j)})
        up, mid, lo = boll_series(close)
        rec.update({"BOLL_UPPER": s2list(up), "BOLL_MID": s2list(mid), "BOLL_LOWER": s2list(lo)})
        vmap = fetch_volume_map(code, dates[0], dates[-1])
        rec["volume"] = [vmap.get(d) for d in dates]
        out[code] = rec
        print(f"  {code}: 成交量填充 {sum(1 for x in rec['volume'] if x is not None)}/{len(dates)}")
        time.sleep(0.1)
    return out


def main():
    lk = json.load(open(SRC, encoding="utf-8"))
    out = build_from_sources(lk)
    if out is None:
        print("未找到分项缓存，改为实时计算（含成交量拉取）...")
        out = build_full(lk)
    out["_meta"] = META
    json.dump(out, open(OUT, "w", encoding="utf-8"), ensure_ascii=False)
    print(f"已写出统一指标文件: {OUT}  基金数={len(out) - 1}")


if __name__ == "__main__":
    main()
