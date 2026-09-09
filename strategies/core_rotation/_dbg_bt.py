# -*- coding: utf-8 -*-
"""一次性: 打印回测决策表尾部, 排查情景分布异常。"""
import sys
sys.path.insert(0, "strategies/core_rotation")
import _backtest as bt

m = bt.build_monthly()
dec = bt.build_decisions(m)
print("dy last:", round(float(m["dy"].iloc[-1]), 4), "cal:", round(m["dy_cal"], 3))
print("y10 last:", round(float(m["y10"].iloc[-1]), 4))
print("rows:", len(dec))
print(dec.tail(14)[["y10", "ratio", "red_pct", "kc_ratio", "sc"]].to_string())
print("scenario counts:")
print(dec["sc"].value_counts().to_string())
