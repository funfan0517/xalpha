# -*- coding: utf-8 -*-
"""V10 专项：用「近几年波动的 max/min/mean」锚定 TV —— 能否自适应？

用户提问：既然 TV10 后段赢、TV20 全样本赢，能不能用近几年波动的最大/最小/平均
动态调 TV，让波动结构变了也能跟上？

数学警示（要在报告里讲清）：
  V10 仓位 = min(1, TV / vol20)。若 TV ∝ vol20（正相关），分子分母同步 → 仓位≈常数，
  波动层失效；若 TV ∝ 1/vol20（负相关），仓位被双重压低/放大，比固定 TV 更极端。
  用历史 max/min/mean 做锚，逃不出这两类，只是换了个映射函数。

⚠ 本探针是 2026-11-16 的**两腿历史探索**（ndx/spx），结论不得单独外推到全池；
波动锚反相的全腿版本见 `_probe_vol_anchor_7legs.py` / `_v10_vol_anchor_7legs.md`。

本探针测五种映射（全部 shift(1) 无前视，窗口 3 年 = 756 日）：
  A 固定 TV10 / TV20（对照）
  B 分位反相关：TV = 20% - 10%·p，p = (vol_now-min)/(max-min)   [高波动→低TV]
  C 分位正相关：TV = 10% + 10%·p                                [高波动→高TV，≈v8目标自适应]
  D 均值比例反相：TV = clip(15%·mean/vol_now, 8%, 25%)           [高波动→低TV]
  E 均值比例正相：TV = clip(15%·vol_now/mean, 8%, 25%)           [高波动→高TV，≈常数仓位]
  F 极值夹逼：TV = clip(15%·(max-vol_now)/(max-min), 8%, 25%)    [近低波→高TV]

判定（严格）：后段夏普 > 固定 TV10 与 TV20，且全样本夏普 ≥ 两者最大值。
产物：`_v10_vol_anchored_tv.md`（本目录）。
"""
import importlib.util
import io
import math
import os
import sys

try:
    sys.stdout.reconfigure(encoding="utf-8")  # type: ignore[attr-defined]
except Exception:  # noqa: BLE001
    pass

_HERE = os.path.dirname(os.path.abspath(__file__))
_V10 = os.path.dirname(os.path.dirname(_HERE))
_VL = os.path.dirname(_V10)
sys.path.insert(0, _VL)
import data as vd  # noqa: E402
sys.modules["data"] = vd
_sp = importlib.util.spec_from_file_location("vlc", os.path.join(_V10, "vol_layer.py"))
vl = importlib.util.module_from_spec(_sp)
sys.modules["vlc"] = vl
_sp.loader.exec_module(vl)

import numpy as np  # noqa: E402
import pandas as pd  # noqa: E402

OUT = os.path.join(_HERE, "_v10_vol_anchored_tv.md")
LEGS = dict(ndx="纳指100", spx="标普500")
TD, WARMUP, CUT, LOOKBACK = 252, 300, 0.60, 756


def perf(nav):
    r = nav.dropna().pct_change().dropna()
    n = len(r)
    if n < 30:
        return dict(ann=float("nan"), mdd=float("nan"), sharpe=float("nan"))
    ann = float((1 + r).prod() ** (TD / n) - 1)
    vol = float(r.std() * math.sqrt(TD))
    mdd = float((nav / nav.cummax() - 1).min())
    return dict(ann=ann, mdd=mdd, sharpe=(ann - vl.CASH_ANNUAL) / vol if vol > 0 else float("nan"))


def run_tv_series(leg, tv_s, win=20, dead=0.10):
    px = vl.load_nav(leg)
    sig = vl.signal_of(leg)
    vol = sig.pct_change().rolling(win, min_periods=win).std(ddof=1) * math.sqrt(TD)
    vol = vol.reindex(sig.index)
    s = (tv_s.reindex(sig.index) / vol).replace([np.inf, -np.inf], np.nan).clip(upper=1.0).fillna(1.0).clip(0.0, 1.0)
    st = vl.deadzone(s, dead).reindex(px.index).ffill().fillna(1.0).shift(1).fillna(1.0)
    nv = vl.run_leg(st, px)
    return nv, st


def main():
    lines = [
        "# V10：用近几年波动 max/min/mean 锚定 TV —— 实测回答",
        "",
        "> ⚠ 两腿历史探索（ndx/spx），结论不得单独外推到全池；"
        "波动锚反相全腿结果见 `_v10_vol_anchor_7legs.md`。",
        "> 2026-11-16 生成。用 V10 原生机制（vol20 + 死区10pp + T+1 + 单边0.1% + 空仓1.40%），"
        "唯一变化是 TV 从常数换成按过去 3 年（756 日）vol20 的 max/min/mean 映射的序列。",
        "",
        "## 0. 为什么这件事有内在矛盾", "",
        "- 仓位 = `min(1, TV / vol20)`。TV 与 vol20 **正相关** → 分子分母同步放大 → 仓位≈常数，"
        "**波动层被自己抵消**；TV 与 vol20 **负相关** → 仓位被双重压低/放大，比固定 TV 走极端。",
        "- v8 探针已测过 `TV ∝ vol250/中位`（正相关）→ 最多打平、调仓翻倍；分位五档 → 明显更差。"
        "本探针把映射换成用户提出的 max/min/mean 锚，看能否逃出这个矛盾。",
        "- 严格判定：后段夏普赢固定 TV10 与 TV20，且全样本夏普 ≥ 两者最大值。",
        "",
    ]
    for leg, name in LEGS.items():
        px = vl.load_nav(leg)
        sig = vl.signal_of(leg)
        vol = sig.pct_change().rolling(20, min_periods=20).std(ddof=1) * math.sqrt(TD)
        vmin = vol.rolling(LOOKBACK, min_periods=LOOKBACK).min().shift(1)
        vmax = vol.rolling(LOOKBACK, min_periods=LOOKBACK).max().shift(1)
        vmean = vol.rolling(LOOKBACK, min_periods=LOOKBACK).mean().shift(1)
        span = (vmax - vmin).replace(0, np.nan)
        p = ((vol - vmin) / span).clip(0, 1).shift(1)
        const = lambda x: pd.Series(x, index=px.index)
        schemes = {
            "固定 TV10": const(0.10),
            "固定 TV20": const(0.20),
            "B 分位反相 20→10%": 0.20 - 0.10 * p,
            "C 分位正相 10→20%": 0.10 + 0.10 * p,
            "D 均值反相 15%·mean/vol": (0.15 * vmean / vol).clip(0.08, 0.25),
            "E 均值正相 15%·vol/mean": (0.15 * vol / vmean).clip(0.08, 0.25),
            "F 极值夹逼": (0.15 * (vmax - vol) / span).clip(0.08, 0.25),
        }
        cut = WARMUP + int((len(px) - WARMUP) * CUT)
        rows = []
        for lab, tvs in schemes.items():
            nv, st = run_tv_series(leg, tvs)
            f, q = perf(nv.iloc[WARMUP:]), perf(nv.iloc[cut:])
            pos = st.shift(1).reindex(px.index).iloc[WARMUP:].mean()
            trades = int((st.diff().abs() > 1e-9).sum())
            rows.append((lab, f, q, pos, trades))
        b10, b20 = rows[0], rows[1]
        lines += [f"## {name}", "",
                  "| 方案 | 全样本夏普 | 全样本年化 | 全样本回撤 | 后段夏普 | 后段年化 | 后段回撤 | 平均仓位 | 调仓 | 严格赢? |",
                  "|---|---:|---:|---:|---:|---:|---:|---:|---:|:---:|"]
        for lab, f, q, pos, tr in rows:
            strict = (lab.startswith(("B", "C", "D", "E", "F"))
                      and q["sharpe"] > b10[2]["sharpe"] and q["sharpe"] > b20[2]["sharpe"]
                      and f["sharpe"] >= max(b10[1]["sharpe"], b20[1]["sharpe"]) - 1e-9)
            lines.append(f"| {lab} | {f['sharpe']:.2f} | {f['ann']:+.2%} | {f['mdd']:.2%} | "
                         f"{q['sharpe']:.2f} | {q['ann']:+.2%} | {q['mdd']:.2%} | "
                         f"{pos:.1%} | {tr} | {'✓' if strict else ''} |")
        lines.append("")

    lines += ["## 结论", "",
              "- **方向是反的才有用**：正相（C/E，波动高→TV高）让分子分母同步，仓位被推向常数，"
              "全样本夏普全线垫底（纳指 0.68/0.65、标普 0.47/0.49）；反相（B/D/F，波动高→TV低）才有意义。",
              "- **纳指：没有一个波动锚方案过线**。最好的反相 B 后段 0.79 < 固定 TV10 的 0.81、"
              "全样本 0.73 < 0.75；纳指只能固定 TV。",
              "- **标普：B（分位反相）与 D（均值反相）两个方案过线** —— B 全样本 0.54/后段 0.70、"
              "D 全样本 0.54/后段 0.66，都超过两个固定档（0.50/0.51、0.60/0.62）。"
              "用户提的「用近几年 max/min/mean 自适应」在标普上是**可行的**。",
              "- **与趋势档对比**：上次 `../probe_adaptive/_v10_adaptive_tv.md` 的标普 MA120 两档是全样本 0.55 / 后段 0.72，"
              "略优于本探针最好的 B（0.54 / 0.70），但差距很小（0.01 夏普）。两条路都有效，"
              "趋势档略胜，波动锚胜在不需要引入均线状态机。",
              "- **本质**：反相映射等价于「波动升高时比固定 TV 更保守」，它缓释了 2022 那种"
              "'高波动+下跌'的伤害；但和趋势档一样，代价是低波动慢牛里仓位偏低。",
              "> 本探针只给结论，未改任何策略代码。", ""]
    with io.open(OUT, "w", encoding="utf-8") as f:
        f.write("\n".join(lines) + "\n")
    print(f"WROTE {OUT}")


if __name__ == "__main__":
    main()
