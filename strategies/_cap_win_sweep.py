# -*- coding: utf-8 -*-
"""cap_win（CMF 窗口）x cap_abs_max（门槛阈值）联合扫描。

动机: 上一轮把窗口 3 日改成 1 日, 因子标准差涨了 71%, 而门槛阈值没动 ->
      `|cap| <= 6` 的通过率从 70% 崩到 33%, 策略失效。
      这测到的是「门槛错配」而不是「窗口本身好不好」。

本脚本在每个窗口下扫一遍阈值, 并标出「通过率与该窗口 3 日基准相当」的点,
      使两个窗口在**同等暴露约束**下可比。capital 保持停用, 只隔离门槛。

用法: python strategies/_cap_win_sweep.py
输出: strategies/_cap_win_sweep_report.md
"""
import os
import sys

import numpy as np

sys.stdout.reconfigure(encoding="utf-8")
_DIR = os.path.dirname(os.path.abspath(__file__))
_ROOT = os.path.dirname(_DIR)
for p in (_ROOT, _DIR, os.path.join(_DIR, "lights")):
    if p not in sys.path:
        sys.path.insert(0, p)

import rule  # noqa: E402
from _tune import Bench, metrics  # noqa: E402

OUT = os.path.join(_DIR, "_cap_win_sweep_report.md")
WINS = (3, 1)
THRESH = (4, 6, 8, 10, 12, 14, 16, 20)


def pass_rate(bench, cfg):
    xs = []
    for meta in rule.POOL:
        df = bench.frame(meta["code"])
        if len(df) < cfg.min_bars + 60:
            continue
        f = bench.factors(meta["code"], cfg)
        xs.append(np.abs(np.asarray(f["cap3"], dtype=float)))
    x = np.concatenate(xs)
    x = x[np.isfinite(x)]
    a = np.abs(x)
    return x, a


def main():
    bench = Bench()
    base = rule.ACTIVE
    LOG = []
    print(f"池子 {len(rule.POOL)} 只 · capital 停用（只隔离门槛）\n")

    for win in WINS:
        bw = base.with_(cap_win=win)
        x, a = pass_rate(bench, bw)
        ref = float((a <= 6.0).mean())
        tgt = float(np.percentile(a, ref * 100))     # 与「3 日窗口 + 阈值6」同等通过率的阈值
        print(f"===== cap_win = {win} 日 =====")
        print(f"|cap| 均值 {a.mean():.2f} 标准差 {a.std():.2f} · "
              f"阈值 6 的通过率 {ref * 100:.1f}% · 同等通过率所需阈值 ≈ {tgt:.1f}\n")
        print(f"{'阈值':>6}{'通过率':>9}{'年化':>8}{'超额':>8}{'边际bp':>9}{'目标':>9}{'持仓':>8}")
        rows = []
        for th in THRESH:
            cfg = bw.with_(cap_abs_max=float(th))
            m = metrics(bench.run(cfg)[0])
            if m is None:
                continue
            pr = float((a <= th).mean())
            mark = "  ← 等效" if abs(th - tgt) == min(abs(t - tgt) for t in THRESH) else ""
            rows.append((th, pr, m, mark))
            print(f"{th:>6}{pr * 100:>8.1f}%{m['ann'] * 100:>7.1f}%{m['excess'] * 100:>7.1f}%"
                  f"{m['edge']:>9.1f}{m['score']:>9.3f}{m['pos'] * 100:>7.1f}%{mark}")
        print()
        LOG.append((win, ref, tgt, rows, a))

    L = ["# cap_win x cap_abs_max 联合扫描", "",
         f"> 池子 {len(rule.POOL)} 只行业 ETF · capital 灯停用（只隔离门槛效应）· "
         f"样本 {base.sample_from} ~ {bench.end.date()}", "",
         "> **动机**: 窗口 3 日 -> 1 日使因子标准差涨了 71%, 而阈值未动 -> "
         "`|cap| <= 6` 通过率从 70% 崩到 33%, 策略失效。那一轮测到的是**门槛错配**, "
         "不是窗口本身的好坏。本表在每个窗口下扫阈值, 并标出**与 3 日窗口+阈值6 "
         "同等通过率**的点, 使两个窗口在同等暴露约束下可比。", ""]
    for win, ref, tgt, rows, a in LOG:
        L += [f"## cap_win = {win} 日", "",
              f"- `|cap|` 均值 {a.mean():.2f} · 标准差 {a.std():.2f}",
              f"- 阈值 6 的通过率 **{ref * 100:.1f}%** · **同等通过率所需阈值 ≈ {tgt:.1f}**", "",
              "| 阈值 | 通过率 | 年化 | 超额 | 边际(bp) | 目标 | 持仓 |",
              "|---|---|---|---|---|---|---|"]
        for th, pr, m, mark in rows:
            L.append(f"| {th}{mark} | {pr * 100:.1f}% | {m['ann'] * 100:+.1f}% | "
                     f"{m['excess'] * 100:+.1f}% | {m['edge']:+.1f} | "
                     f"**{m['score']:.3f}** | {m['pos'] * 100:.1f}% |")
        best = max(rows, key=lambda r: r[2]["score"])
        L += ["", f"> 该窗口最优: 阈值 {best[0]}（目标 {best[2]['score']:.3f}，"
                  f"通过率 {best[1] * 100:.1f}%，年化 {best[2]['ann'] * 100:+.1f}%）", ""]
    open(OUT, "w", encoding="utf-8").write("\n".join(L))
    print(f"-> {OUT}")


if __name__ == "__main__":
    main()
