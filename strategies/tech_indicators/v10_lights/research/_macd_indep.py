# -*- coding: utf-8 -*-
"""P3: MACD 是不是「价格趋势」的重复表达?

上一版用 `收>MA20` 做代理，结果与「恒为 1」逐位相同 —— 因为该代理与其它灯**高度共线**
（价格在涨 <=> c>ma20），活跃度与「其它灯是否有分」几乎一致，对照无效。

本版两手抓:
  1. **共线性度量**: 候选灯取值与「其它灯之和」的日度相关（样本窗口内, 全池合并）。
     与其它灯高相关的代理 = 信息重复, 不能用来判断独立价值。
  2. **不共线代理**: 用长周期趋势（c>MA60 / MA20>MA60）与长动量（ret20>0）,
     它们与现有 trend 灯（ma5/ma10/ma20/adx）的重叠远小于 c>MA20。
  另附 MACD 自身的两个变体, 用来判断零轴过滤（DIF>0）是否多余。

用法: python strategies/lights/_macd_indep.py
输出: strategies/lights/_macd_report.md
"""
import os
import sys

import numpy as np

sys.stdout.reconfigure(encoding="utf-8")
_DIR = os.path.dirname(os.path.abspath(__file__))
_ROOT = os.path.dirname(_DIR)
for p in (_ROOT, _DIR, os.path.dirname(_ROOT)):
    if p not in sys.path:
        sys.path.insert(0, p)

import rule  # noqa: E402
from _tune import Bench, metrics  # noqa: E402

OUT = os.path.join(_DIR, "_macd_report.md")
KEY = "sustain"


def _struct(base, f):
    """统一结构: base 成立 -> 1; base 且 ret3>3% -> 2。保证与真实灯同量纲。"""
    out = np.zeros(len(base), dtype=float)
    b = base.fillna(False).to_numpy()
    out[b] = 1
    out[(base & (f["ret3"] > 0.03)).fillna(False).to_numpy()] = 2
    return out


def c_real(f):
    d = (f["dif"] > f["dea"]) & (f["dif"] > 0)
    return _struct(d, f)


def c_no_zero(f):
    """去掉零轴过滤: 只要 DIF>DEA。"""
    return _struct(f["dif"] > f["dea"], f)


def c_dea_axis(f):
    """换轴过滤: DIF>DEA 且 DEA>0。"""
    return _struct((f["dif"] > f["dea"]) & (f["dea"] > 0), f)


def c_ma60(f):
    """长周期趋势（与 trend 灯的 ma5/10/20 重叠小）。"""
    return _struct(f["c"] > f["ma60"], f)


def c_ma20_60(f):
    """中期趋势方向。"""
    return _struct(f["ma20"] > f["ma60"], f)


def c_ret20(f):
    """长动量。"""
    return _struct(f["ret20"] > 0, f)


CANDS = [
    ("**真实 MACD** (DIF>DEA & DIF>0)", c_real),
    ("MACD 去零轴 (DIF>DEA)", c_no_zero),
    ("MACD 换轴 (DIF>DEA & DEA>0)", c_dea_axis),
    ("长趋势 c>MA60", c_ma60),
    ("中期趋势 MA20>MA60", c_ma20_60),
    ("长动量 ret20>0", c_ret20),
]


def main():
    reg = rule.LIGHT_REGISTRY[KEY]
    for i, (_tag, fn) in enumerate(CANDS):
        reg[f"p3_c{i}"] = (fn, f"P3 候选 {i}")

    bench = Bench()
    base = rule.ACTIVE
    W = base.lights[KEY][1]

    print(f"池子 {len(rule.POOL)} 只 · sustain 权重 {W} · 基线目标 "
          f"{metrics(bench.run(base)[0])['score']:.3f}\n")
    print(f"{'候选灯定义':<30}{'共线(与其它灯)':>14}{'年化':>8}{'超额':>8}"
          f"{'边际bp':>9}{'目标':>8}{'胜率':>7}{'笔数':>7}")

    res = []
    for i, (tag, _fn) in enumerate(CANDS):
        L = dict(base.lights)
        L[KEY] = (f"p3_c{i}", W)
        cfg = base.with_(lights=L)
        rows, _ = bench.run(cfg)
        m = metrics(rows)
        if m is None:
            print(f"{tag:<30}  无有效结果")
            continue

        # 共线性: 候选灯取值 vs 「其它灯之和」, 样本窗口内全池合并
        xs, ys = [], []
        for meta in rule.POOL:
            code = meta["code"]
            df = bench.frame(code)
            if len(df) < cfg.min_bars + 60:
                continue
            begin = int(np.argmax(np.asarray(df.index >= np.datetime64(cfg.sample_from))))
            f = bench.factors(code, cfg)
            sig = rule.build_signals(f, cfg)
            xs.append(sig["light_vals"][KEY][begin:])
            ys.append(sig["score"][begin:] - W * sig["light_vals"][KEY][begin:])
        xs, ys = np.concatenate(xs), np.concatenate(ys)
        corr = float(np.corrcoef(xs, ys)[0, 1]) if xs.std() > 0 and ys.std() > 0 else float("nan")

        r = np.array([t["ret"] for x in rows for t in (x.get("trade_log") or [])])
        win = (r > 0).mean() if len(r) else float("nan")
        res.append((tag, corr, m, win, len(r)))
        print(f"{tag:<30}{corr:>14.3f}{m['ann'] * 100:>7.1f}%{m['excess'] * 100:>7.1f}%"
              f"{m['edge']:>9.1f}{m['score']:>8.3f}{win * 100:>6.1f}%{len(r):>7}")

    real = res[0]
    proxies = [r for r in res if r[0].startswith(("长趋势", "中期趋势", "长动量"))]
    print(f"\n== 判定: 真实 MACD 目标 {real[2]['score']:.3f} vs 不共线代理 ==")
    for tag, corr, m, win, n in proxies:
        d = real[2]["score"] - m["score"]
        print(f"  {tag:<22} 共线 {corr:+.3f}  目标 {m['score']:.3f}  "
              f"MACD 领先 {d:+.3f}  {'MACD 有独立价值' if d > 0.05 else '★ 无实质独立价值'}")
    bm = max(proxies, key=lambda r: r[2]["score"])
    print(f"  最强的代理是 {bm[0]}（目标 {bm[2]['score']:.3f}）")

    L = ["# P3: MACD 是不是「价格趋势」的重复表达", "",
         f"> 池子 {len(rule.POOL)} 只 · `sustain` 权重 {W} · "
         f"基线目标 {metrics(bench.run(base)[0])['score']:.3f}", "",
         "> **上一版代理失败的教训**: 用 `收>MA20` 做代理时结果与「恒为 1」逐位相同 —— "
         "因为该代理与其它灯**高度共线**（价格在涨 <=> c>ma20），它的活跃度与"
         "「其它灯是否有分」几乎一致，于是 `exit_max` 的越界方向相同，对照失效。", "",
         "> **本版**: 同时报告**共线性**（候选灯取值 vs 其它灯之和的日度相关），"
         "并用长周期趋势 / 长动量做**不共线代理**。若 MACD 赢不过最强的代理，"
         "说明它只是趋势的重复表达。", "",
         "| 候选灯定义 | 共线(与其它灯) | 年化 | 超额 | 边际(bp) | 目标 | 胜率 | 笔数 |",
         "|---|---|---|---|---|---|---|---|"]
    for tag, corr, m, win, n in res:
        L.append(f"| {tag} | {corr:+.3f} | {m['ann'] * 100:+.1f}% | {m['excess'] * 100:+.1f}% | "
                 f"{m['edge']:+.1f} | **{m['score']:.3f}** | {win * 100:.1f}% | {n} |")
    L += ["", f"> 最强的不共线代理: **{bm[0]}**（目标 {bm[2]['score']:.3f}）；"
              f"真实 MACD 领先 {real[2]['score'] - bm[2]['score']:+.3f}。", ""]
    open(OUT, "w", encoding="utf-8").write("\n".join(L))
    print(f"\n-> {OUT}")


if __name__ == "__main__":
    main()
