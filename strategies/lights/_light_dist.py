# -*- coding: utf-8 -*-
"""诊断: 各候选灯在样本窗口内的取值分布（0/1/2 各占多少）。

用来确认消融实验里的「代理灯」是否真的产生了有效变化 —— 如果一个代理灯的取值
分布退化成常数, 那它等价于 const, 对照就是无效的。
"""
import os
import sys
import collections

import numpy as np

sys.stdout.reconfigure(encoding="utf-8")
_DIR = os.path.dirname(os.path.abspath(__file__))
_ROOT = os.path.dirname(_DIR)
for p in (_ROOT, _DIR, os.path.join(_DIR, "lights")):
    if p not in sys.path:
        sys.path.insert(0, p)

import rule  # noqa: E402
import _ablate  # noqa: E402

CFG = rule.ACTIVE
SUS = CFG.lights["sustain"][0]


def sample_window(f, df, code):
    begin = int(np.argmax(np.asarray(df.index >= np.datetime64(CFG.sample_from))))
    return begin


def main():
    panels = rule.load_panels_raw()
    end = rule._data.complete_end(panels["close"].index)

    cands = {
        f"real:{SUS}": rule.LIGHT_REGISTRY["sustain"][SUS][0],
        "px: 收>MA20": _ablate._mk_px_ma20,
        "px: ret5>0": _ablate._mk_px_ret5,
    }
    for nm, spec in rule.LIGHT_REGISTRY["sustain"].items():
        if nm.startswith(("macd_weekly", "macd_binary")):
            cands[f"alt:{nm}"] = spec[0]

    acc = {k: collections.Counter() for k in cands}
    for meta in rule.POOL:
        code = meta["code"]
        df = rule.trading_frame(panels, code, end)
        if len(df) < CFG.min_bars + 60:
            continue
        begin = sample_window(None, df, code)
        f = rule.compute_factors(df, CFG, code)
        for k, fn in cands.items():
            v = np.asarray(fn(f), dtype=float)[begin:]
            acc[k].update(np.round(v, 3).tolist())

    print(f"池子 {len(rule.POOL)} 只 · 样本 {CFG.sample_from} 起 · 灯维度 sustain\n")
    print(f"{'灯定义':<26}{'总日数':>8}{'=0':>9}{'=1':>9}{'=2':>9}{'均值':>8}")
    for k, c in acc.items():
        tot = sum(c.values())
        p0, p1, p2 = c.get(0.0, 0), c.get(1.0, 0), c.get(2.0, 0)
        mean = (p1 + 2 * p2) / tot if tot else 0.0
        print(f"{k:<26}{tot:>8}{p0 / tot * 100:>8.1f}%{p1 / tot * 100:>8.1f}%"
              f"{p2 / tot * 100:>8.1f}%{mean:>8.3f}")

    # 关键: ret3 的量纲与分布（决定 2 分档是否可达）
    print("\n== 2 分档条件 ret3 > 0.03 的可达性 ==")
    for meta in rule.POOL[:1]:
        pass
    n_tot = n_gt = 0
    med = []
    for meta in rule.POOL:
        df = rule.trading_frame(panels, meta["code"], end)
        if len(df) < CFG.min_bars + 60:
            continue
        begin = sample_window(None, df, meta["code"])
        f = rule.compute_factors(df, CFG, meta["code"])
        r3 = np.asarray(f["ret3"], dtype=float)[begin:]
        n_tot += len(r3)
        n_gt += int(np.nansum(r3 > 0.03))
        med.append(float(np.nanmedian(r3)))
    print(f"  各标的 ret3 中位的平均: {np.mean(med):.4f}（量纲自检: 应接近 0）")
    print(f"  ret3 > 0.03 的交易日占比: {n_gt / n_tot * 100:.1f}%  ({n_gt}/{n_tot})")


if __name__ == "__main__":
    main()
