# -*- coding: utf-8 -*-
"""亮灯策略 · walk-forward 验证（对「调参结论是否过拟合」的加固）。

做法：把 2016-09 起的样本按时间等分成 K 段。对第 i 段（i>=1）：
  1. **选参**：只用 [起点, 第 i 段起点) 的数据, 在候选集里按目标函数挑最好的一个;
  2. **验证**：把挑出的参数**原封不动**拿到第 i 段上跑, 记录样本外表现。
同时并列两个不调参的对照：固定 c7（当前生效）与采纳前的初始配置。

判读：如果「每折选出来的最优」在下一段上并不比「固定 C7」好，说明调参收益主要来自
样本内拟合；反之则说明选参过程本身是稳的。

输出: strategies/_walkforward_report.md
用法: python strategies/_walkforward.py [--k 4]
"""
import os
import sys
import time

import numpy as np
import pandas as pd

sys.stdout.reconfigure(encoding="utf-8")
_DIR = os.path.dirname(os.path.abspath(__file__))
_ROOT = os.path.dirname(_DIR)
for p in (_ROOT, _DIR, os.path.join(_DIR, "lights")):
    if p not in sys.path:
        sys.path.insert(0, p)

import rule  # noqa: E402
from _tune import Bench, candidates, metrics, fmt  # noqa: E402

OUT = os.path.join(_DIR, "_walkforward_report.md")

HEAD = ("| 口径 | 策略年化 | 择时边际(bp) | **目标=年化×边际** | 跑赢 | 持仓 | 策略回撤 |",
        "|---|---|---|---|---|---|---|")


def pre_c7():
    """采纳前的初始配置（作为"不调参"的第二个对照）。"""
    L = rule.ACTIVE.lights
    d = dict(L)
    d["sustain"] = ("macd", 1)
    d["heat"] = ("ret5_band", 1)
    return rule.ACTIVE.with_(label="pre-c7", lights=d,
                             enter_required={"trend": 1}, ret3_max=0.12, cap_abs_max=6.0)


def row(tag, m):
    return (f"| {tag} | {fmt(m, 'ann')} | {fmt(m, 'edge')} | **{fmt(m, 'score')}** | "
            f"{fmt(m, 'beat')} | {fmt(m, 'pos')} | {fmt(m, 'mdd')} |")


def main():
    K = 4
    if "--k" in sys.argv:
        K = int(sys.argv[sys.argv.index("--k") + 1])

    bench = Bench()
    t0 = time.time()
    start, end = pd.Timestamp(rule.SAMPLE_FROM), bench.end
    span = (end - start) / K
    bounds = [start + span * i for i in range(K + 1)]
    windows = [(bounds[i], bounds[i + 1]) for i in range(K)]

    fixed = rule.ACTIVE
    pre = pre_c7()
    cands = candidates()

    L = ["# 亮灯策略 · walk-forward 验证", "",
         f"> 样本 {start:%Y-%m-%d} ~ {end:%Y-%m-%d} 等分为 {K} 段（每段 ~{span.days} 天）。"
         f"第 i 段先只用它**之前**的数据选参（候选 {len(cands)} 个），再把选出的参数原样拿到该段验证。",
         f"> 目标函数 = 策略年化 × 择时边际。对照：**固定 C7**（当前生效，不调参）与 **初始配置（pre-c7）**。", ""]

    # 全样本基准
    L += ["## 0. 全样本（参考）", "", *HEAD,
          row("**固定 C7（当前生效）**", metrics(bench.run(fixed)[0])),
          row("初始配置（pre-c7）", metrics(bench.run(pre)[0])), ""]

    detail, sum_sel, sum_c7, sum_pre = [], [], [], []
    for i, (w0, w1) in enumerate(windows):
        L += [f"## 第 {i + 1} 段 · 验证窗口 {w0:%Y-%m-%d} ~ {w1:%Y-%m-%d}", ""]
        sel_tag, sel_cfg = None, None
        if i > 0:
            L += [f"### 选参（只用 {start:%Y-%m-%d} ~ {w0:%Y-%m-%d}）", "", *HEAD]
            scored = []
            for tag, ov in [(t, o) for t, o in cands]:
                cfg = fixed.with_(**ov).with_(sample_from=rule.SAMPLE_FROM, sample_to=str(w0.date()))
                m = metrics(bench.run(cfg)[0])
                if m:
                    scored.append((tag, ov, m))
            for tag, ov, m in sorted(scored, key=lambda x: -x[2]["score"]):
                L.append(row(tag, m))
            L.append("")
            sel_tag, sel_ov, _ = max(scored, key=lambda x: x[2]["score"])
            sel_cfg = fixed.with_(**sel_ov)
            L.append(f"> 选中 **{sel_tag}**")
            L.append("")
        else:
            L.append("> 第 1 段没有「之前的数据」可用来选参，只报告固定口径的验证结果。")
            L.append("")

        L += ["### 验证（本段样本外）", "", *HEAD]
        m_c7 = metrics(bench.run(fixed.with_(sample_from=str(w0.date()), sample_to=str(w1.date())))[0])
        m_pre = metrics(bench.run(pre.with_(sample_from=str(w0.date()), sample_to=str(w1.date())))[0])
        L.append(row("固定 C7（不调参）", m_c7))
        L.append(row("初始配置（pre-c7）", m_pre))
        m_sel = None
        if sel_cfg is not None:
            m_sel = metrics(bench.run(sel_cfg.with_(sample_from=str(w0.date()),
                                                    sample_to=str(w1.date())))[0])
            L.append(row(f"本段选出的 **{sel_tag}**", m_sel))
            detail.append((i + 1, sel_tag, m_sel, m_c7))
        L.append("")
        sum_c7.append(m_c7)
        sum_pre.append(m_pre)
        if m_sel:
            sum_sel.append(m_sel)

    ag = lambda lst, k: float(np.mean([m[k] for m in lst])) if lst else float("nan")  # noqa: E731
    L += ["## 汇总（各段样本外的平均）", "",
          "| 口径 | 平均年化 | 平均边际(bp) | 平均目标 | 参与段数 |",
          "|---|---|---|---|---|",
          f"| 固定 C7（不调参） | {ag(sum_c7, 'ann') * 100:+.1f}% | {ag(sum_c7, 'edge'):+.1f} | "
          f"**{ag(sum_c7, 'score'):.3f}** | {len(sum_c7)} |",
          f"| 初始配置（pre-c7） | {ag(sum_pre, 'ann') * 100:+.1f}% | {ag(sum_pre, 'edge'):+.1f} | "
          f"**{ag(sum_pre, 'score'):.3f}** | {len(sum_pre)} |",
          f"| 每段重新选参 | {ag(sum_sel, 'ann') * 100:+.1f}% | {ag(sum_sel, 'edge'):+.1f} | "
          f"**{ag(sum_sel, 'score'):.3f}** | {len(sum_sel)} |", ""]

    if detail:
        L += ["## 选参稳定性", "", "| 段 | 选中的候选 | 该段目标 | 固定 C7 该段目标 | 选参是否更好 |",
              "|---|---|---|---|---|"]
        for i, tag, m_sel, m_c7 in detail:
            better = "是" if m_sel["score"] > m_c7["score"] else "**否**"
            L.append(f"| {i} | {tag} | {fmt(m_sel, 'score')} | {fmt(m_c7, 'score')} | {better} |")
        L.append("")
        wins = sum(1 for _i, _t, ms, mc in detail if ms["score"] > mc["score"])
        L.append(f"> 每段重新选参优于固定 C7 的段数: **{wins}/{len(detail)}**。"
                 f"若明显不足半数, 说明单变量扫描给出的「最优」主要来自样本内拟合, "
                 f"应以固定 C7 为准（它本来就是从稳健区域里选的）。")
        L.append("")

    L += ["", f"> 生成 {pd.Timestamp.now():%Y-%m-%d %H:%M} · 运行 {time.time() - t0:.0f}s · "
              f"候选集见 strategies/_tune.py::candidates()", ""]
    open(OUT, "w", encoding="utf-8").write("\n".join(L))
    print("\n".join(L[-14:]))
    print(f"\n-> {OUT}  ({time.time() - t0:.0f}s)")


if __name__ == "__main__":
    main()
