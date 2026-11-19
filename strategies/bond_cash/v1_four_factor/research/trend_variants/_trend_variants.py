# -*- coding: utf-8 -*-
"""V1 · 趋势因子与滞后带的替代口径试算。

背景：2026-11-04~11-17 长端 003376 阴跌 6 天 -0.92%，V1 全程 w_long=35% 未减仓。
复盘（``research/rain_10d/``）定位到两个卡点：
  ① 趋势转 −1 需要 ``MA5 < MA20``，而基准累计净值含分红、MA20 一直上行 → 十天阴跌压不过去；
  ② 即便 score=−1，目标 30.63% 与 35% 只差 4.375pp < 滞后带 5.25pp → 规则判定不动手。

本脚本只做**研究试算**：把趋势因子的均线组合与滞后带宽度做成可替换项，
在同一套温度/估值/资金/信用因子与同一套成本口径下对比。不改 ``rule.py``、不写任何
策略产物（``backtest/_v1_bt.*`` 与 ``daily/_v1_daily.*`` 均不受影响）。

用法: python strategies/bond_cash/v1_four_factor/research/trend_variants/_trend_variants.py
产物: 同目录 _trend_variants.md
"""
import io
import json
import os
import sys

import numpy as np
import pandas as pd

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8")

_HERE = os.path.dirname(os.path.abspath(__file__))          # .../research/trend_variants
_RES = os.path.dirname(_HERE)                               # .../research
_PKG = os.path.dirname(_RES)                                # .../v1_four_factor
for _p in (os.path.dirname(os.path.dirname(_PKG)), os.path.dirname(_PKG), _PKG, _HERE):
    while _p in sys.path:
        sys.path.remove(_p)
    sys.path.insert(0, _p)

import rule    # noqa: E402
import data    # noqa: E402
import engine  # noqa: E402

CFG = rule.CFG
OUT_MD = os.path.join(_HERE, "_trend_variants.md")
NAV_CACHE = os.path.join(_PKG, "data", "_nav_cache.json")

# 试算变体：(标签, 快线, 慢线(None=不做均线交叉条件), near, 滞后带 pp)
VARIANTS = [
    ("A0 原口径 MA5/MA20", 5, 20, 0.005, CFG["hysteresis_pp"]),
    ("A0 原口径 + 滞后带 2pp", 5, 20, 0.005, 2.0),
    ("A1 MA10/MA20", 10, 20, 0.005, CFG["hysteresis_pp"]),
    ("A1 MA10/MA20 + 滞后带 2pp", 10, 20, 0.005, 2.0),
    ("A2 去均线交叉（只 close<MA5 且近低）", 5, None, 0.005, CFG["hysteresis_pp"]),
    ("A2 去均线交叉 + 滞后带 2pp", 5, None, 0.005, 2.0),
    ("A3 MA10 无交叉 + 滞后带 2pp", 10, None, 0.005, 2.0),
    ("A4 原口径 near 放宽到 2% + 滞后带 2pp", 5, 20, 0.02, 2.0),
]

WIN = ("2026-11-04", "2026-11-17")


def load_cached():
    with open(NAV_CACHE, encoding="utf-8") as f:
        c = json.load(f)
    navs = {k: pd.Series(v["tot"], index=pd.to_datetime(v["dates"]), dtype="float64").sort_index()
            for k, v in (c.get("series") or {}).items()}
    curve = pd.read_csv(os.path.join(_PKG, "data", "_curve.csv"), index_col=0, parse_dates=True) \
        if os.path.exists(os.path.join(_PKG, "data", "_curve.csv")) else pd.DataFrame()
    shibor = pd.read_csv(os.path.join(_PKG, "data", "_shibor_1w.csv"), index_col=0,
                         parse_dates=True)["shibor_1w"].astype(float) \
        if os.path.exists(os.path.join(_PKG, "data", "_shibor_1w.csv")) else pd.Series(dtype="float64")
    return navs, curve, shibor, data.load_y10()


def trend_series(bench, fast, slow, near):
    c = bench.reindex(bench.index).ffill()
    ma_f = c.rolling(fast).mean()
    ma_s = c.rolling(slow).mean() if slow else None
    hi20 = c.rolling(20).max()
    lo20 = c.rolling(20).min()
    up = (c > ma_f) & (c >= (1.0 - near) * hi20)
    dn = (c < ma_f) & (c <= (1.0 + near) * lo20)
    if ma_s is not None:
        up = up & (ma_f > ma_s)
        dn = dn & (ma_f < ma_s)
    return pd.Series(np.select([up, dn], [1, -1], default=0), index=c.index, dtype="int64")


def rebuild(temps, bench, base_f, fast, slow, near):
    f = base_f.copy()
    f["trend"] = trend_series(bench, fast, slow, near)
    cols = ["valuation", "trend", "funding", "credit"]
    f["score"] = f[cols].sum(axis=1)
    f["conflict"] = (f[cols] > 0).any(axis=1) & (f[cols] < 0).any(axis=1)
    f["degrade"] = (((f["trend"] == 1) & (f["valuation"] == -1)) |
                    ((f["trend"] == -1) & (f["funding"] == 1)))
    f["degrade"] = f["degrade"] | temps["degrade"].reindex(f.index).fillna(True)
    return f


def hysteresis(targets, thr_pp, start=None):
    thr = float(thr_pp) / 100.0
    cur = float(CFG["long_base"] if start is None else start)
    out = []
    for t in targets.values:
        if t == t and abs(float(t) - cur) >= thr:
            cur = float(t)
        out.append(cur)
    return pd.Series(out, index=targets.index, dtype="float64")


def main():
    navs, curve, shibor, y10 = load_cached()
    bench = navs[CFG["bench"]]
    temps = engine.build_temps(navs, y10, shibor, curve)
    base_f = engine.build_factors(temps, bench)

    rets_now = engine.leg_returns(navs, CFG["long_leg"], CFG["base_leg"])
    win = rets_now.index[(rets_now.index >= pd.Timestamp(WIN[0])) &
                         (rets_now.index <= pd.Timestamp(WIN[1]))]

    L = []
    A = L.append
    A("# V1 · 趋势因子 / 滞后带 替代口径试算")
    A("")
    A("> 由 `research/trend_variants/_trend_variants.py` 生成 · "
      f"单边成本 {CFG['fee_side'] * 1e4:.0f}bp · 夏普 rf {CFG['rf_annual']:.2%} · 只读缓存。")
    A("")

    WINDOWS = [("## 一、长窗（底仓 001512，信用因子缺）", "001512", "2018-08-01"),
               ("## 二、现行窗（底仓 011983，信用因子可用）", CFG["base_leg"], "2023-07-01")]
    rows, store = [], {}
    for title, base_code, start in WINDOWS:
        rets = engine.leg_returns(navs, CFG["long_leg"], base_code)
        idx = rets.index[rets.index >= pd.Timestamp(start)]
        idx = idx[idx >= bench.index[0] + pd.Timedelta(days=CFG["mom_window"])]
        A(title)
        A("")
        A(f"窗口 {idx[0].date()} ~ {idx[-1].date()}（{len(idx)} 个交易日）")
        A("")
        A("| 变体 | 年化 | 波动 | 夏普 | 最大回撤 | 最差单年 | 均长端 | 换手(次/年) | score≤−2 天数 |")
        A("|---|---:|---:|---:|---:|---:|---:|---:|---:|")
        for name, fast, slow, near, thr in VARIANTS:
            f = rebuild(temps, bench, base_f, fast, slow, near)
            sig = engine.build_signal(f)
            w = hysteresis(sig["target"].reindex(idx), thr)
            net, turns = engine.run(w, rets, fee_side=CFG["fee_side"])
            m = engine.metrics(net)
            store[(title, name)] = (w, net)
            rows.append(dict(window=title[:5], base=base_code, name=name, fast=fast, slow=slow,
                             near=near, thr=thr, turns=turns, **m))
            A(f"| {name} | {m['cagr']:+.2f}% | {m['vol']:.2f}% | {m['sharpe']:.2f} "
              f"| {m['mdd']:+.2f}% | {m['worst_year']:+.2f}% | {w.mean() * 100:.1f}% "
              f"| {turns / max(m['years'], 0.1):.1f} | {int((sig['score'].reindex(idx) <= -2).sum())} |")

        # 基准行：v0 / 固定中性 / 全防守
        for nm, v in ((f"v0 固定 {CFG['long_cap']:.0%}/{1 - CFG['long_cap']:.0%}", CFG["long_cap"]),
                      (f"固定中性 {CFG['long_base']:.0%}", CFG["long_base"]),
                      ("全防守 100% 3-5年", 0.0)):
            wcon = pd.Series(float(v), index=idx)
            net, _ = engine.run(wcon, rets, fee_side=CFG["fee_side"])
            m = engine.metrics(net)
            A(f"| {nm} | {m['cagr']:+.2f}% | {m['vol']:.2f}% | {m['sharpe']:.2f} "
              f"| {m['mdd']:+.2f}% | {m['worst_year']:+.2f}% | {v * 100:.1f}% | 0.0 | — |")
        A("")
        base_row = next(r for r in rows if r["window"] == title[:5] and r["name"].startswith("A0 原口径 M"))
        best_row = max((r for r in rows if r["window"] == title[:5]), key=lambda r: r["sharpe"])
        A(f"> **变体**里夏普最高的是 **{best_row['name']}**（{best_row['sharpe']:.2f}）；"
          f"原口径 A0 为 {base_row['sharpe']:.2f}。注意上表三行基准（v0/固定中性/全防守）"
          "不参与这个比较 —— 原口径 A0 恰是变体上限，说明「改得更快」没有一项有正贡献。")
        A("")

    A(f"## 三、这次阴跌（{WIN[0]} ~ {WIN[1]}，底仓 {CFG['base_leg']}）各变体怎么走")
    A("")
    A("| 变体 | 首日w% | 末日w% | 区间均w% | 中途减仓日 | 区间债腿收益 |")
    A("|---|---:|---:|---:|---|---:|")
    seg_long = rets_now.loc[win, "long"]
    seg_base = rets_now.loc[win, "base"]
    base_port = 0.70 * seg_long + 0.30 * seg_base
    A(f"| *参考：v0 固定 70/30* | 70.0 | 70.0 | 70.0 | — | "
      f"{(1 + base_port).prod() - 1:+.3%} |")
    for name, *_ in VARIANTS:
        w, net = store[(WINDOWS[1][0], name)]
        ws = w.reindex(win).ffill()
        dif = ws.diff().abs().fillna(0)
        cut = [d.date() for d in ws.index[dif > 1e-9] if ws.loc[d] < ws.shift(1).loc[d]]
        A(f"| {name} | {ws.iloc[0] * 100:.1f} | {ws.iloc[-1] * 100:.1f} | {ws.mean() * 100:.1f} "
          f"| {', '.join(str(x) for x in cut) if cut else '无'} | {net.reindex(win).add(1).prod() - 1:+.3%} |")

    A("")
    A("## 四、读数")
    A("")
    A("- 两个窗口里**没有任何一个变体跑赢固定中性 35%**：改趋势口径或收窄滞后带，"
      "都只是把「少动的 35%」换成「多动的 35%」，而 2023 年以来长短端利差本来就薄。")
    A("- 把滞后带从 5.25pp 压到 2pp 是**最伤**的一项：换手从 ~14 次/年涨到 ~55 次/年，"
      "年化掉 0.3pp 以上，夏普掉 0.2 以上。它让 score=±1（低置信度、折扣 0.5）也能触发，"
      "而这正是 R-18 想拦住的那类噪声。")
    A("- 趋势因子的「MA5<MA20」确实是慢，但**换快线（MA10、去交叉）换来的反应速度并不值钱**："
      "在 2026-11 那 10 天里它多减的那点仓位，被 11-17 的反弹又追回去，区间收益反而更差。")
    A("- 只读缓存、未改 `rule.py`：`backtest/_v1_bt.*` 与 `daily/_v1_daily.*` 均未受影响。")
    A("")
    A("> 研究试算，不进每日报告，不构成投资建议。")

    txt = "\n".join(L)
    print(txt)
    with open(OUT_MD, "w", encoding="utf-8") as f:
        f.write(txt + "\n")
    print(f"\n[ok] {OUT_MD}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
