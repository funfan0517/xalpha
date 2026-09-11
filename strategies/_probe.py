# -*- coding: utf-8 -*-
"""探针: 逐日比对两个消融配置的 score / enter / exit，定位「结果完全相同」的原因。"""
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
import _ablate  # noqa: E402

CODE = sys.argv[1] if len(sys.argv) > 1 else "512800"


def main():
    a = _ablate.register("sustain", rule.LIGHT_REGISTRY["sustain"][rule.ACTIVE.lights["sustain"][0]][0])
    panels = rule.load_panels_raw()
    end = rule._data.complete_end(panels["close"].index)
    df = rule.trading_frame(panels, CODE, end)
    f = rule.compute_factors(df, rule.ACTIVE, CODE)

    outs = {}
    for tag, name, w in (("const1", "ab_const1", 2), ("px_ma20", "ab_px_ma20", 2),
                         ("const0", "ab_const0", 2), ("real", rule.ACTIVE.lights["sustain"][0], 2)):
        L = dict(rule.ACTIVE.lights)
        L["sustain"] = (name, w)
        sig = rule.build_signals(f, rule.ACTIVE.with_(lights=L))
        outs[tag] = sig

    for tag, s in outs.items():
        lv = s["light_vals"]["sustain"]
        print(f"{tag:<10} sustain 取值分布 " +
              " ".join(f"{v}:{(np.round(lv, 3) == v).mean() * 100:5.1f}%" for v in (0.0, 1.0, 2.0)) +
              f"  均值 {lv.mean():.3f}  score均值 {s['score'].mean():.3f}  "
              f"enter {s['enter'].mean() * 100:.1f}%  exit {s['exit'].mean() * 100:.1f}%")

    print()
    for x, y in (("const1", "px_ma20"), ("const0", "px_ma20"), ("real", "px_ma20")):
        sx, sy = outs[x], outs[y]
        print(f"{x} vs {y}: score 不同 {(sx['score'] != sy['score']).mean() * 100:5.1f}%  "
              f"enter 不同 {(sx['enter'] != sy['enter']).mean() * 100:5.1f}%  "
              f"exit 不同 {(sx['exit'] != sy['exit']).mean() * 100:5.1f}%")

    print(f"\nenter_min={rule.ACTIVE.enter_min}  exit_max={rule.ACTIVE.exit_max}  "
          f"enter_required={rule.ACTIVE.enter_required}  exit_all_zero={rule.ACTIVE.exit_all_zero}")

    # 关键检验: enter_min 到底有没有约束力？(纯算术, 不重跑引擎)
    s = outs["real"]
    req = np.ones(len(s["score"]), dtype=bool)
    for k, mv in (rule.ACTIVE.enter_required or {}).items():
        req &= s["light_vals"][k] >= mv
    only_req = s["gate_pass"] & req
    print(f"\n仅 gate_pass & enter_required 的入场率: {only_req.mean() * 100:.1f}%")
    print(f"{'enter_min':>10}{'入场率':>9}{'较仅要求':>10}")
    for em in (1, 2, 3, 4, 5, 6, 7, 8):
        e = only_req & (s["score"] >= em)
        print(f"{em:>10}{e.mean() * 100:>8.1f}%{(e.mean() - only_req.mean()) * 100:>9.2f}pp")

    # exit 端: score <= exit_max 之外还有多少是 gate 贡献的
    sc_exit = s["score"] <= rule.ACTIVE.exit_max
    print(f"\nexit 分解: gate 失败 {s['gate_pass'].mean() * 100:5.1f}% 空仓, "
          f"score<= {rule.ACTIVE.exit_max} 触发 {sc_exit.mean() * 100:.1f}%, "
          f"两者并集 {s['exit'].mean() * 100:.1f}%")


if __name__ == "__main__":
    main()
