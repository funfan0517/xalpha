# -*- coding: utf-8 -*-
"""校验 _indicators_hist.json 的指标是否正确。

A) 公式一致性: 用 xalpha 自家 indicator 公式, 套在 _long_klines.json 的收盘价上,
   与 _indicators_hist.json 逐点比对 -> 应≈0, 证明算法与 xalpha 一致。
B) 数据源一致性: 直接取 xa.fundinfo(code) 的内置指标(基于净值 NAV), 与我的(基于市价)
   按日期对齐比对 -> 看差值量级, 解释"为什么直接获取 vs 我算的"会有出入。
"""
import json
import os

import numpy as np
import pandas as pd
import xalpha as xa
from xalpha.indicator import indicator

ROOT = os.path.dirname(os.path.abspath(__file__))
DATA = os.path.join(ROOT, "data")
HIST = json.load(open(os.path.join(DATA, "_indicators_hist.json"), encoding="utf-8"))
LK = json.load(open(os.path.join(DATA, "_long_klines.json"), encoding="utf-8"))


class _Dummy(indicator):
    """把 xalpha 的 indicator MixIn 套在任意 price 表上(本地计算, 不联网)。"""

    def __init__(self, df):
        self.price = df


def maxabs(a, b):
    a = np.asarray(a, dtype=float)
    b = np.asarray(b, dtype=float)
    m = ~(np.isnan(a) | np.isnan(b))
    if m.sum() == 0:
        return None, 0
    return float(np.nanmax(np.abs(a[m] - b[m]))), int(m.sum())


# ----------------------------- A. 公式一致性 ----------------------------- #
def check_formula(code):
    h = HIST[code]
    l = LK[code]
    df = pd.DataFrame({"date": pd.to_datetime(l["dates"]), "netvalue": l["close"]})
    d = _Dummy(df)
    d.ma(5); d.ma(10); d.ma(20); d.ma(60)
    d.macd()
    d.kdj()
    d.boll(20, 2)
    p = d.price
    pairs = {
        "MA5": "MA5", "MA10": "MA10", "MA20": "MA20", "MA60": "MA60",
        "DIF": "MACD_DIFF_12_26", "DEA": "MACD_DEM_12_26", "HIST": "MACD_OSC_12_26",
        "K": "KDJ_K", "D": "KDJ_D", "J": "KDJ_J",
        "BOLL_UPPER": "BOLL_UPPER", "BOLL_MID": "MA20", "BOLL_LOWER": "BOLL_LOWER",
    }
    res = {}
    for mine, xa_col in pairs.items():
        res[mine] = maxabs(h[mine], p[xa_col].tolist())
    return res


print("=" * 70)
print("A) 公式一致性: xalpha 公式套用本套收盘价 vs 我的 _indicators_hist.json")
print("=" * 70)
allmax = {}
for code in LK:
    r = check_formula(code)
    for k, (v, n) in r.items():
        if v is None:
            continue
        allmax.setdefault(k, []).append(v)
worst = {}
for k, vs in allmax.items():
    worst[k] = (max(vs), np.mean(vs), len(vs))
print(f"{'指标':<12}{'最大偏差':>14}{'平均偏差':>14}{'样本基金数':>12}")
for k in ["MA5", "MA10", "MA20", "MA60", "DIF", "DEA", "HIST", "K", "D", "J",
          "BOLL_UPPER", "BOLL_MID", "BOLL_LOWER"]:
    if k in worst:
        mx, avg, n = worst[k]
        print(f"{k:<12}{mx:>14.2e}{avg:>14.2e}{n:>12}")
print("-> 若偏差为 1e-10 量级, 说明我的算法与 xalpha 公式逐点一致。")
# KDJ 采样确认: 我的(0-100) vs xalpha(0-1) 仅差 ×100
print("\n[采样 588000 末3日] my_K(0-100) vs xalpha_KDJ_K(0-1)")
p = _Dummy(pd.DataFrame({"date": pd.to_datetime(LK["588000"]["dates"]),
                          "netvalue": LK["588000"]["close"]}))
p.ma(5); p.macd(); p.kdj(); p.boll(20, 2)
for i in [-3, -2, -1]:
    print(f"  my K={HIST['588000']['K'][i]:7.3f}  xalpha KDJ_K={p.price['KDJ_K'].iloc[i]:.4f}  "
          f"my/100={HIST['588000']['K'][i]/100:.4f}")

# --------------------------- B. 数据源(净值)对比 --------------------------- #
print()
print("=" * 70)
print("B) 数据源一致性: xa.fundinfo(净值口径) 直接获取 vs 我的(市价口径)")
print("=" * 70)
SAMPLE = ["588000", "510300", "159915", "563360", "512100"]
cols = [("MA20", "MA20"), ("DIF", "MACD_DIFF_12_26"), ("DEA", "MACD_DEM_12_26"),
        ("HIST", "MACD_OSC_12_26"), ("K", "KDJ_K"), ("D", "KDJ_D"), ("J", "KDJ_J"),
        ("BOLL_UPPER", "BOLL_UPPER"), ("BOLL_MID", "MA20"), ("BOLL_LOWER", "BOLL_LOWER")]
for code in SAMPLE:
    if code not in LK:
        continue
    try:
        fi = xa.fundinfo(code)
    except Exception as e:  # noqa
        print(f"[{code}] fundinfo 获取失败: {e}")
        continue
    fi.ma(20); fi.macd(); fi.kdj(); fi.boll(20, 2)
    fp = fi.price.copy()
    fp["d"] = pd.to_datetime(fp["date"]).dt.strftime("%Y-%m-%d")
    fmap = {row["d"]: row for _, row in fp.iterrows()}
    h = HIST[code]
    # 仅取最近 252 个交易日(双方都已预热), 避免我的序列起始较晚导致的窗口错位
    tail = set(h["dates"][-252:])
    print(f"\n[{code}] 净值交易日={len(fp)}  市价交易日={len(h['dates'])}  (比对窗口=最近{tail and len(tail & set(fmap.keys())) or 0}日)")
    for mine, xa_col in cols:
        xs, ys, ns = [], [], 0
        for dt, yv in zip(h["dates"], h[mine]):
            if dt in tail and dt in fmap and fmap[dt][xa_col] is not None and yv is not None:
                xs.append(fmap[dt][xa_col]); ys.append(yv); ns += 1
        if ns < 5:
            print(f"  {mine:<12} 共同样本不足({ns})")
            continue
        mx = max(abs(a - b) for a, b in zip(xs, ys))
        avg = sum(abs(a - b) for a, b in zip(xs, ys)) / ns
        avgv = sum(abs(b) for b in ys) / ns
        print(f"  {mine:<12} 共{ns:>4}日  平均相对偏差={avg/max(avgv,1e-9):.4%}  最大绝对偏差={mx:.4g}")
