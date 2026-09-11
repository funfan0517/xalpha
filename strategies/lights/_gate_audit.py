# -*- coding: utf-8 -*-
"""门槛审计: 每个门槛在样本窗口内实际挡住了多少交易日, 以及 indicators_ready 的
warm-up 到底有多长（用数据回答"这个门槛什么意思", 而不是复述文档）。

用法: python strategies/lights/_gate_audit.py
"""
import os
import sys

import numpy as np
import pandas as pd

sys.stdout.reconfigure(encoding="utf-8")
_DIR = os.path.dirname(os.path.abspath(__file__))
_ROOT = os.path.dirname(_DIR)
for p in (_ROOT, _DIR, os.path.join(_DIR, "lights")):
    if p not in sys.path:
        sys.path.insert(0, p)

import rule  # noqa: E402

CFG = rule.ACTIVE
READY = list(CFG.ready_fields)


def main():
    panels = rule.load_panels_raw()
    end = rule._data.complete_end(panels["close"].index)

    block = {g: 0 for g in CFG.gates}          # 各门槛在样本窗口内挡住的交易日合计
    worst = {g: [] for g in CFG.gates}
    rows = []                                  # (code, theme, warm_bars, ready_date, n_blocked, total)
    for meta in rule.POOL:
        code = meta["code"]
        df = rule.trading_frame(panels, code, end)
        if CFG.sample_to:
            df = df.loc[:pd.Timestamp(CFG.sample_to)]
        if len(df) < CFG.min_bars + 60:
            continue
        begin = int(np.argmax(np.asarray(df.index >= np.datetime64(CFG.sample_from))))
        f = rule.compute_factors(df, CFG, code)

        ready = rule.GATE_REGISTRY["indicators_ready"](f, CFG)
        warm = int(np.argmax(ready.to_numpy())) if ready.any() else len(ready)
        ready_date = str(df.index[warm].date()) if warm < len(df) else "—"

        n_ready_block = int((~ready.to_numpy()[begin:]).sum())
        for g in CFG.gates:
            m = rule.GATE_REGISTRY[g](f, CFG).to_numpy()[begin:]
            nb = int((~m).sum())
            block[g] += nb
            if nb:
                worst[g].append((nb, code, meta["theme"]))
        rows.append((code, meta["theme"], warm, ready_date, n_ready_block,
                     int(len(df) - begin), df.index[warm].date() if warm < len(df) else None,
                     df.index[0].date()))

    print(f"配置 {CFG.label} · 池子 {len(rows)} 只 · 样本 {CFG.sample_from} ~ {str(end.date())}")
    print(f"ready_fields = {READY}\n")

    print("== 各门槛在样本窗口内挡住的交易日（全池合计）==")
    tot_days = sum(r[5] for r in rows)
    print(f"{'门槛':<20}{'挡住日数':>9}{'占样本':>8}   最受影响标的")
    for g in CFG.gates:
        pct = block[g] / tot_days * 100 if tot_days else 0.0
        top = "、".join(f"{c}({t}) {n}日" for n, c, t in sorted(worst[g], reverse=True)[:3])
        print(f"{g:<20}{block[g]:>9}{pct:>7.1f}%   {top or '—'}")

    print(f"\n== indicators_ready 的 warm-up 实测 ==")
    print(f"{'代码':<8}{'主题':<10}{'warm-up':>8}{'首个就绪日':>13}{'样本内挡住':>10}{'数据起点':>12}")
    for code, theme, warm, rdate, nb, ndays, _rd, d0 in sorted(rows, key=lambda x: -x[2]):
        print(f"{code:<8}{theme:<10}{warm:>7}日{rdate:>13}{nb:>10}{str(d0):>12}")

    nz = [r for r in rows if r[2] > 0]
    print(f"\nwarm-up 长度: 最短 {min(r[2] for r in rows)} 日 / 最长 {max(r[2] for r in rows)} 日 "
          f"/ 中位 {int(np.median([r[2] for r in rows]))} 日")
    print(f"样本内真的被 indicators_ready 挡住的标的: {len([r for r in rows if r[4] > 0])} / {len(rows)} 只")

    print("\n== 各 ready 字段的 leading NaN 天数（warm-up 由谁决定）==")
    for meta in rule.POOL[:1]:
        pass
    acc = {k: [] for k in READY}
    for meta in rule.POOL:
        df = rule.trading_frame(panels, meta["code"], end)
        f = rule.compute_factors(df, CFG, meta["code"])
        for k in READY:
            s = f[k].to_numpy()
            acc[k].append(int(np.argmax(~np.isnan(s))) if np.isnan(s).any() else 0)
    for k in READY:
        v = acc[k]
        print(f"  {k:<8} 中位 {int(np.median(v)):>3} 日  最长 {max(v):>3} 日")


if __name__ == "__main__":
    main()
