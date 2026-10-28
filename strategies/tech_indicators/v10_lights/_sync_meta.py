# -*- coding: utf-8 -*-
"""把 `pipeline/strategies.json` 里 `lights.thresholds` 这一块从 `rule.ACTIVE` 同步过去。

为什么需要: 按 pipeline 的惯例, `strategies.json` 是「策略参数的唯一权威源」;
但 `lights` 是**参数化策略**, 权威源是 `strategies/lights/rule.py::ACTIVE`（代码里可执行、
可被 --set 覆盖、有类型）。两者不能各写一份, 否则必然漂移 —— 所以 json 里那一块是**镜像**,
改参数只改 rule.py, 然后跑本脚本同步。

用法: python strategies/lights/_sync_meta.py
"""
import collections
import json
import os
import sys

sys.stdout.reconfigure(encoding="utf-8")
_DIR = os.path.dirname(os.path.abspath(__file__))
_ROOT = os.path.dirname(os.path.dirname(_DIR))
for p in (_ROOT, os.path.dirname(_DIR), _DIR):
    if p not in sys.path:
        sys.path.insert(0, p)

import rule  # noqa: E402

CFG = os.path.join(_ROOT, "pipeline", "strategies.json")


def main():
    A = rule.ACTIVE
    d = json.load(open(CFG, encoding="utf-8"), object_pairs_hook=collections.OrderedDict)
    st = d["strategies"]["lights"]
    t = collections.OrderedDict()
    t["authoritative"] = "strategies/lights/rule.py::ACTIVE（本块为镜像, 跑 _sync_meta.py 同步）"
    t["preset"] = A.label
    t["lights"] = collections.OrderedDict(
        (k, (list(v) if v else None)) for k, v in A.lights.items())
    t["gates"] = list(A.gates)
    t["enter_min"] = A.enter_min
    t["enter_required"] = dict(A.enter_required)
    t["exit_max"] = A.exit_max
    t["exit_on_gate_fail"] = A.exit_on_gate_fail
    t["weight_rules"] = "any -> 1.0"
    t["exit_events"] = list(A.exit_events)
    t["exit_all_zero"] = list(A.exit_all_zero)
    t["liq_amt_min"] = A.liq_amt_min
    t["list_min_bars"] = A.list_min_bars
    t["ret3_max"] = A.ret3_max
    t["cap_abs_max"] = A.cap_abs_max
    t["cap_proxy_scale"] = A.cap_proxy_scale
    t["band_wide"] = list(A.band_wide)
    t["band_sector"] = list(A.band_sector)
    t["div_max"] = A.div_max
    t["rebal_days"] = A.rebal
    t["trail_stop"] = A.trail_stop
    t["max_hold"] = A.max_hold
    t["weekly_order"] = A.weekly_order
    t["fee"] = A.fee
    t["trade_at"] = "信号次日开盘"
    st["thresholds"] = t
    with open(CFG, "w", encoding="utf-8") as fh:
        json.dump(d, fh, ensure_ascii=False, indent=2)
        fh.write("\n")
    print(f"synced strategies.json[lights].thresholds <- rule.ACTIVE (label={A.label})")


if __name__ == "__main__":
    main()
