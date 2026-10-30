#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""V9 · 因子面板（数据 → 五因子分位 → 温度）

口径（`RULES.md` §二，外部规则 §4.3 的落地）
-------------------------------------------
设某标的价格序列 `close/high/low`（中证官网日频，标普系用 ETF/LOF 代理）与 PE 序列：

| 因子 | 当期值 | 历史窗口 |
|---|---|---|
| 年线乖离 dev | `close / MA250 − 1` | 逐日滚动 MA250 的 dev |
| 价格位置 pos | `(close − 52周低) / (52周高 − 52周低) × 100` | 逐日滚动 250 根 high/low |
| PE | etf.run 等权 `equalWeightedPeTtm`（缺口最多前填 20 个交易日） | 近 2 年（730 日历日）全部日频观测 |
| PB | etf.run 等权 `equalWeightedPbTtm`（真实）；无该指数时退回 `PE × ROE 中枢` | 同 PE |
| 股债收益差 spread | `股息率 − 10Y`（pp；无股息率历史时退回 `100/PE − 10Y`） | 同 PE，**反号**计入 |

分位一律 `100 × count(w ≤ v) / len(w)`（含等于，规则 §4.2），最少 `rule.PCT_MIN_N`（300）个观测。
温度 = `Σ w_k · sub_k / Σ w_k`，权重见 `rule.W`。

⚠ 两条口径要分清：

* **默认口径（`VALUATION_SRC="etfrun"`，6/6 只）**：PE 与 PB 都是 etf.run 等权真实值，
  PB 分位**不**等于 PE 分位 —— 实测池内最大差 94.65 分，源于等权 ROE 长期下行
  （0.130→0.086，−34%）：同样的 PE 对应的 PB 越来越低。
* **回退口径（`"csindex"`，2 只或整体切回）**：`PB = PE × 常数` ⇒ 常数缩放不改变秩 ⇒
  **PB 分位 ≡ PE 分位**（恒等式，实测差 0）。此时「五因子」数学上退化为
  **PE 36% + 利差 35% + 乖离 15% + 位置 14%**。

`backtest.py` 每次都会同时跑两种口径做对照（报告 §〇）；蛋卷周频版的独立检验见
`research/_pb_real_check.md`（ROE 变异系数 9.4%~11.1%，Δ(PB−PE) 对 13 周收益 t=2.32）。

当前池内全部有等权估值，**无价格温度标的**；`temp_at` 仍保留价格温度分支（`TEMP_KIND_PX`）供未来加回无估值标的时复用。

无前视：分位只用 `≤ asof` 的观测；温度在月末算出，下月才用于决策。
"""
from __future__ import annotations

import os
import sys

import numpy as np
import pandas as pd

_DIR = os.path.dirname(os.path.abspath(__file__))
if _DIR not in sys.path:
    sys.path.insert(0, _DIR)

import data  # noqa: E402  pylint: disable=wrong-import-position
import rule  # noqa: E402  pylint: disable=wrong-import-position

PE_FFILL = 20          # PE 缺口最多前填 20 个交易日（930955 的 2018–19 长洞不硬填）
CSINDEX_ROE = 0.10     # v1.0 合成 PB 对照：PB = PE × 常数（仅 backtest 复现「PB ≡ PE」恒等式，非生产路径）
TEMP_KIND_VAL = "五因子温度"
TEMP_KIND_PX = "价格温度"


# ------------------------------------------------------------------ 日频面板
def _bond_for(index: pd.DatetimeIndex, cn: pd.Series) -> pd.Series:
    """10Y 中债前填到价格日期（池内已无港股标的，不再分支美债锚）。"""
    grid = index.union(cn.index)
    return cn.reindex(grid).ffill().reindex(index)


def fund_daily(f: dict, cn: pd.Series, refresh: bool = False,
               dy_map: dict | None = None, eva_map: dict | None = None) -> pd.DataFrame:
    """单只标的日频面板 -> DataFrame(index=date, close/high/low/pe/pb/dev/pos/dy/spread/y10)。

    价格取中证官网日频；PE/PB 取 etf.run 等权日频（**唯一**估值源）。
    该指数没有 etf.run 数据时返回空表 —— **不降级、不合成**，由 `build` 跳过并告警。
    """
    ew = (eva_map or {}).get(f["index"])
    if ew is None or not len(ew):
        print(f"[v9:warn] {f['code']}({f['index']}) 无 etf.run 等权估值，跳过", file=sys.stderr)
        return pd.DataFrame()
    raw = data.load_index(f["index"], refresh=refresh)
    if raw.empty:
        return pd.DataFrame()
    d = raw.set_index("date").sort_index()
    d = d[~d.index.duplicated(keep="last")]
    for c in ("close", "high", "low"):
        d[c] = pd.to_numeric(d[c], errors="coerce")
    d = d.dropna(subset=["close"])

    ma = d["close"].rolling(rule.MA_WIN, min_periods=rule.MA_WIN).mean()
    hi = d["high"].rolling(rule.POS_WIN, min_periods=rule.POS_WIN).max()
    lo = d["low"].rolling(rule.POS_WIN, min_periods=rule.POS_WIN).min()
    d["dev"] = d["close"] / ma - 1
    d["pos"] = (d["close"] - lo) / (hi - lo) * 100

    # PE 与 PB 同源（etf.run 等权），绝不与中证加权 PE 混用，否则分位尺度不一致
    grid = d.index.union(ew.index)
    d["pe"] = ew["pe"].reindex(grid).ffill(limit=PE_FFILL).reindex(d.index)
    d["pb"] = ew["pb"].reindex(grid).ffill(limit=PE_FFILL).reindex(d.index)
    d["val_src"] = "etfrun_ew"
    d["y10"] = _bond_for(d.index, cn)
    ent = (dy_map or {}).get(f["index"])
    if rule.SPREAD_KIND == "dy" and ent is not None and len(ent["dy"]):
        dy = ent["dy"]
        # 月度股息率 -> 日频（收盘已知，前填不回填，无前视）
        d["dy"] = dy.reindex(d.index.union(dy.index)).ffill().reindex(d.index)
        d["spread"] = d["dy"] - d["y10"]
    else:
        d["dy"] = np.nan
        d["spread"] = 100.0 / d["pe"] - d["y10"]
    return d


# ------------------------------------------------------------------ 分位
def _pct(value: float, window: np.ndarray, min_n: int = rule.PCT_MIN_N):
    """`100 × count(w ≤ v) / len(w)`；样本不足或值缺失 -> None。"""
    if value is None or not np.isfinite(value):
        return None
    w = window[np.isfinite(window)]
    if len(w) < min_n:
        return None
    return float((w <= value).sum()) / len(w) * 100.0


def _window(series: pd.Series, asof: pd.Timestamp, days: int = rule.PCT_WINDOW_DAYS) -> np.ndarray:
    s = series.loc[:asof]
    s = s[s.index > asof - pd.Timedelta(days=days)]
    return s.to_numpy(dtype=float)


def temp_at(daily: pd.DataFrame, dates, weights: dict | None = None,
            has_valuation: bool = True) -> pd.DataFrame:
    """在给定日期（月末或最新交易日）算温度 -> DataFrame(index=dates, sub_*, temp, temp_kind)。

    同一函数同时服务回测（月末）与每日读数（最新交易日），避免两条代码路径算出不同数。
    `has_valuation=True` 的标的**缺 PE 就算不出温度**（不降级成价格温度）——
    降级会让同一只基金在历史早期混进「价格温度」，既不可比也会混进排名。
    """
    weights = weights or rule.W
    rows = []
    for d in dates:
        d = pd.Timestamp(d)
        hist = daily.loc[:d]
        if hist.empty:
            continue
        asof = hist.index[-1]
        if "dev" not in hist or pd.isna(hist["dev"].iloc[-1]):
            continue
        sub = {}
        if asof in daily.index and pd.notna(daily.at[asof, "dev"]):
            sub["dev"] = _pct(float(daily.at[asof, "dev"]), _window(daily["dev"], asof))
            sub["pos"] = _pct(float(daily.at[asof, "pos"]), _window(daily["pos"], asof))
        if has_valuation:
            sub["pe"] = _pct(float(daily.at[asof, "pe"]), _window(daily["pe"], asof)) \
                if pd.notna(daily.at[asof, "pe"]) else None
            sub["pb"] = _pct(float(daily.at[asof, "pb"]), _window(daily["pb"], asof)) \
                if pd.notna(daily.at[asof, "pb"]) else None
            sub["spread"] = _pct(float(daily.at[asof, "spread"]), _window(daily["spread"], asof)) \
                if pd.notna(daily.at[asof, "spread"]) else None

        keys = [k for k in rule.FACTORS if k in weights]
        if all(sub.get(k) is not None for k in keys):
            # 统一成「越高越热」的子分（利差反号）：sub_spread = 100 − 利差分位
            sub = {k: (sub[k] if rule.SIGN[k] > 0 else 100.0 - sub[k]) for k in rule.FACTORS
                   if sub.get(k) is not None}
            rows.append({"date": d, "asof": asof,
                         "temp": sum(sub[k] * weights[k] for k in keys) / sum(weights[k] for k in keys),
                         "temp_kind": TEMP_KIND_VAL,
                         **{f"sub_{k}": sub.get(k) for k in rule.FACTORS},
                         "close": float(daily.at[asof, "close"]),
                         "pe": float(daily.at[asof, "pe"]),
                         "dev": float(daily.at[asof, "dev"]),
                         "pos": float(daily.at[asof, "pos"]),
                         "spread": float(daily.at[asof, "spread"])})
        elif not has_valuation and sub.get("dev") is not None and sub.get("pos") is not None:
            rows.append({"date": d, "asof": asof,
                         "temp": 0.5 * sub["dev"] + 0.5 * sub["pos"], "temp_kind": TEMP_KIND_PX,
                         "sub_dev": sub["dev"], "sub_pos": sub["pos"],
                         "sub_pe": None, "sub_pb": None, "sub_spread": None,
                         "close": float(daily.at[asof, "close"]), "pe": np.nan,
                         "dev": float(daily.at[asof, "dev"]), "pos": float(daily.at[asof, "pos"]),
                         "spread": np.nan})
    if not rows:
        return pd.DataFrame(columns=["date", "temp", "temp_kind"])
    return pd.DataFrame(rows).set_index("date")


def month_ends(index: pd.DatetimeIndex) -> pd.DatetimeIndex:
    """每月月末的**日期标签**（`temp_at` 内部取 ≤ 该标签的最后交易日，故跨标的可对齐）。"""
    per = index.to_period("M").unique()
    return pd.DatetimeIndex(per.to_timestamp(how="end").normalize())


# ------------------------------------------------------------------ 组装
def build(weights: dict | None = None, refresh: bool = False,
          src: str | None = None) -> dict[str, dict]:
    """-> {fund代码: {daily, monthly, fund, dy_src, val_src}}；monthly 为月末温度面板。

    `src` 透传给 `fund_daily`，便于同一个进程里对比两种估值口径（"etfrun" / "csindex"）。
    """
    kind = src or rule.VALUATION_SRC
    cn = data.load_bond_cn(refresh=refresh)
    dy_map = data.load_div_yield() if rule.SPREAD_KIND == "dy" else {}
    # etf.run 等权 PE/PB 是所有口径的估值源；csindex 仅把 PB 换成「PE × 常数」合成，
    # 用于 backtest 复现 v1.0「PB ≡ PE」恒等式（非生产路径，生产只用 etfrun）
    eva = data.load_etfrun_eva(refresh=refresh)
    eva_map = {}
    if len(eva):
        eva_map = {c: g.set_index("date")[["pe", "pb"]].sort_index()
                   for c, g in eva.groupby("index_code")}
    out = {}
    for f in rule.FUNDS:
        daily = fund_daily(f, cn, refresh=refresh, dy_map=dy_map, eva_map=eva_map)
        if daily.empty:
            print(f"[v9:warn] {f['code']} 无价格数据，跳过", file=sys.stderr)
            continue
        if kind == "csindex":   # 复现 v1.0：PB = PE × 常数（合成），分位恒与 PE 相同
            daily["pb"] = daily["pe"] * CSINDEX_ROE
        me = month_ends(daily.index)
        monthly = temp_at(daily, me, weights=weights, has_valuation=f.get("has_valuation", True))
        ent = dy_map.get(f["index"]) if f.get("has_valuation", True) else None
        dy_src = (ent["method"] if ent else ("ey" if f.get("has_valuation", True) else None))
        val_src = daily["val_src"].iloc[-1] if "val_src" in daily.columns else None
        out[f["code"]] = {"daily": daily, "monthly": monthly, "fund": f,
                          "dy_src": dy_src, "val_src": val_src}
    return out


def monthly_temp(panels: dict) -> pd.DataFrame:
    """末端温度宽表（月末索引 × 基金代码）。"""
    cols = {c: p["monthly"]["temp"] for c, p in panels.items() if not p["monthly"].empty}
    return pd.DataFrame(cols).sort_index()


def panel_long(panels: dict) -> pd.DataFrame:
    """长表（供 `_v9_panel.jsonl` 与总对比复用）。"""
    rows = []
    for code, p in panels.items():
        m = p["monthly"]
        for ts, r in m.iterrows():
            rec = {"date": ts.strftime("%Y-%m-%d"), "code": code,
                   "name": p["fund"]["short"], "index": p["fund"]["index"],
                   "temp_kind": r["temp_kind"],
                   "temp": None if pd.isna(r["temp"]) else round(float(r["temp"]), 1),
                   "close": None if pd.isna(r["close"]) else round(float(r["close"]), 4),
                   "dev": None if pd.isna(r["dev"]) else round(float(r["dev"]), 4),
                   "pos": None if pd.isna(r["pos"]) else round(float(r["pos"]), 2),
                   "pe": None if pd.isna(r["pe"]) else round(float(r["pe"]), 3),
                   "spread": None if pd.isna(r["spread"]) else round(float(r["spread"]), 3),
                   "asof": r["asof"].strftime("%Y-%m-%d")}
            for k in rule.FACTORS:
                v = r.get(f"sub_{k}")
                rec[f"sub_{k}"] = None if v is None or pd.isna(v) else round(float(v), 1)
            rows.append(rec)
    return pd.DataFrame(rows)


def align_asof(panels: dict) -> tuple[pd.Timestamp, dict]:
    """规则 §4.1：盘中（15:10 前）剔除当日半根 K，截止日取各标的最后交易日的众数。"""
    from datetime import datetime       # pylint: disable=import-outside-toplevel

    now = datetime.now()
    drop_today = now.hour * 60 + now.minute < 15 * 60 + 10
    lasts = {}
    for code, p in panels.items():
        idx = p["daily"].index
        if drop_today and len(idx) and idx[-1].normalize() == pd.Timestamp(now.date()):
            idx = idx[:-1]
        if len(idx):
            lasts[code] = idx[-1]
    if not lasts:
        raise RuntimeError("没有任何标的有价格数据")
    vals = pd.Series(list(lasts.values()))
    mode = vals.value_counts()
    top = mode[mode == mode.max()].index.max()
    stale = {c: d for c, d in lasts.items() if d != top}
    return pd.Timestamp(top), stale


if __name__ == "__main__":
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    pn = build()
    t = monthly_temp(pn)
    last = t.index[-1]
    print(f"[v9:factors] 月度温度 {t.index[0]:%Y-%m}~{last:%Y-%m} · {len(t)} 月 · "
          f"有估值标的 {t[rule.VALUED].notna().any().sum()}/{len(rule.VALUED)}")
    for f in rule.FUNDS:
        m = pn[f["code"]]["monthly"]
        if m.empty or m["temp"].isna().all():
            print(f"  {f['code']} {f['short']:<10s} 温度不可用")
            continue
        r = m.dropna(subset=["temp"]).iloc[-1]
        subs = " ".join(f"{k}={r[f'sub_{k}']:.0f}" for k in rule.FACTORS
                        if r[f"sub_{k}"] is not None and pd.notna(r[f"sub_{k}"]))
        print(f"  {f['code']} {f['short']:<10s} 温度 {r['temp']:5.1f} ({r['temp_kind']}) "
              f"asof {r['asof']:%Y-%m-%d} · 可用月 {int(m['temp'].notna().sum())} | {subs}")

    # PB 是 PE × 常数 → 两者分位应当完全相同（这是「五因子」的隐藏退化，报告里必须实测）
    m = pn["090010"]["monthly"].dropna(subset=["temp"])
    gap = float((m["sub_pb"] - m["sub_pe"]).abs().max())
    print(f"[v9:factors] PB 与 PE 分位最大差 {gap:.6f}（0 = 合成 PB 未提供任何新信息）")
