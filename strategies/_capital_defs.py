# -*- coding: utf-8 -*-
"""资金灯：窗口（cap_win = 3 日 / 1 日）x 定义（四个）的目标增量对比。

背景: `cap3` 是 CMF 窗口代理, 键名历史遗留, 实际窗口由 cfg.cap_win 决定。
      窗口一换, **因子分布整体改变** -> 门槛 `|cap| <= 6` 的通过率与灯亮率都会变,
      所以必须把分布一起报出来, 否则无法判断结果差异来自「灯」还是「门槛」。

判据用**目标增量**（含 capital vs 不含 capital 的目标差）:
      灯只有在「改变判定、且改对了」时才产生正增量。

用法: python strategies/_capital_defs.py
输出: strategies/_capital_defs_report.md
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

OUT = os.path.join(_DIR, "_capital_defs_report.md")
DEFS = ["cap3", "vr5_ge07", "vr5_any", "vr_cmf"]
WINS = (3, 1)
W = 1.0


def cap_stats(bench, cfg):
    """cap 因子的分布（全池合并）+ 门槛通过率 + 灯亮率。"""
    xs = []
    for meta in rule.POOL:
        code = meta["code"]
        df = bench.frame(code)
        if len(df) < cfg.min_bars + 60:
            continue
        f = bench.factors(code, cfg)
        xs.append(np.asarray(f["cap3"], dtype=float))
    x = np.concatenate(xs)
    x = x[np.isfinite(x)]
    a = np.abs(x)
    return dict(mean=float(x.mean()), std=float(x.std()),
                p95=float(np.percentile(a, 95)),
                gate_ok=float((a <= cfg.cap_abs_max).mean()),
                lit=float((x >= 1).mean()))


def main():
    bench = Bench()
    base = rule.ACTIVE
    print(f"池子 {len(rule.POOL)} 只 · 权重 {W} · cap_abs_max={base.cap_abs_max:g} · "
          f"exit_max={base.exit_max:g}\n")

    LOG = []
    for win in WINS:
        bw = base.with_(cap_win=win)
        m_off = metrics(bench.run(bw)[0])
        st = cap_stats(bench, bw)
        print(f"===== cap_win = {win} 日 =====")
        print(f"cap 因子: 均值 {st['mean']:+.2f} 标准差 {st['std']:.2f} |cap| 的 P95 {st['p95']:.2f} "
              f"· 门槛 |cap|<={base.cap_abs_max:g} 通过率 {st['gate_ok'] * 100:.1f}% "
              f"· 灯基准率(cap>=1) {st['lit'] * 100:.1f}%")
        print(f"基线（capital 停用）: 年化 {m_off['ann'] * 100:+.1f}% · "
              f"边际 {m_off['edge']:+.1f}bp · 目标 {m_off['score']:.3f}")
        print(f"{'定义':<10}{'灯分布 0/1/2':>16}{'灯亮率':>8}{'相关':>8}{'年化':>8}"
              f"{'边际bp':>9}{'目标':>8}{'目标增量':>10}")
        rows = []
        for d in DEFS:
            on = bw.with_(lights={**dict(bw.lights), "capital": (d, W)})
            m = metrics(bench.run(on)[0])
            if m is None:
                continue
            cnt = {0: 0, 1: 0, 2: 0}
            cs, n = [], 0
            for meta in rule.POOL:
                code = meta["code"]
                df = bench.frame(code)
                if len(df) < bw.min_bars + 60:
                    continue
                f = bench.factors(code, bw)
                lv = np.asarray(rule.LIGHT_REGISTRY["capital"][d][0](f), dtype=float)
                for v in (0.0, 1.0, 2.0):
                    cnt[int(v)] += int((np.round(lv, 3) == v).sum())
                n += len(lv)
                sig = rule.build_signals(f, on)
                others = sig["score"] - W * lv
                if lv.std() > 0 and others.std() > 0:
                    cs.append(float(np.corrcoef(lv, others)[0, 1]))
            dist = f"{cnt[0] / n * 100:.0f}%/{cnt[1] / n * 100:.0f}%/{cnt[2] / n * 100:.0f}%"
            lit = (cnt[1] + cnt[2]) / n
            corr = float(np.mean(cs)) if cs else float("nan")
            ds = m["score"] - m_off["score"]
            rows.append((d, dist, lit, corr, m, ds))
            print(f"{d:<10}{dist:>16}{lit * 100:>7.1f}%{corr:>8.3f}{m['ann'] * 100:>7.1f}%"
                  f"{m['edge']:>9.1f}{m['score']:>8.3f}{ds:>+10.3f}")
        print()
        LOG.append((win, st, m_off, rows))

    L = ["# 资金灯：CMF 窗口（3 日 / 1 日）x 四个定义", "",
         f"> 池子 {len(rule.POOL)} 只行业 ETF · 权重 {W} · `cap_abs_max`="
         f"{base.cap_abs_max:g} · `exit_max`={base.exit_max:g} · "
         f"样本 {base.sample_from} ~ {bench.end.date()}", "",
         "> 判据是**目标增量**（含 capital 与不含 capital 的目标差, 目标 = 年化 x 择时边际）。",
         "> 窗口一换因子分布整体改变, 故同时给出分布、门槛通过率与灯亮率 —— "
         "否则无法区分结果差异来自「灯」还是「门槛」。", ""]
    for win, st, m_off, rows in LOG:
        L += [f"## cap_win = {win} 日", "",
              f"- cap 因子: 均值 {st['mean']:+.2f} · 标准差 {st['std']:.2f} · "
              f"`|cap|` 的 P95 = {st['p95']:.2f}",
              f"- 门槛 `|cap| <= {base.cap_abs_max:g}` 通过率 **{st['gate_ok'] * 100:.1f}%** · "
              f"灯基准率（cap>=1）**{st['lit'] * 100:.1f}%**",
              f"- 基线（capital 停用）: 目标 {m_off['score']:.3f} · "
              f"年化 {m_off['ann'] * 100:+.1f}% · 边际 {m_off['edge']:+.1f}bp", "",
              "| 定义 | 灯分布 0/1/2 | 灯亮率 | 与其它灯相关 | 年化 | 边际(bp) | 目标 | **目标增量** |",
              "|---|---|---|---|---|---|---|---|"]
        for d, dist, lit, corr, m, ds in rows:
            L.append(f"| `{d}` | {dist} | {lit * 100:.1f}% | {corr:+.3f} | "
                     f"{m['ann'] * 100:+.1f}% | {m['edge']:+.1f} | {m['score']:.3f} | "
                     f"**{ds:+.3f}** |")
        best = max(rows, key=lambda r: r[5])
        L += ["", f"> 该窗口下最优: `{best[0]}`（目标增量 {best[5]:+.3f}）", ""]
    open(OUT, "w", encoding="utf-8").write("\n".join(L))
    print(f"-> {OUT}")


if __name__ == "__main__":
    main()
