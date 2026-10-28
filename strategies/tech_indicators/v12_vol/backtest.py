# -*- coding: utf-8 -*-
"""技术指标策略 · 变种 12：VOL（量价）· 全池回测。

规则本体在本目录 `rule.py`（家族引擎 `../backtest.py` 按名分发到这里）；本脚本负责
跑全池、出报告，并与其余四条单指标基线和买入持有并排对照。
共用回测 runner 见 `../_baseline_bt.py`。

产物（本目录 backtest/）：`_VOL_report.md` · `_VOL_results.jsonl` · `_VOL_510300.png`

用法：python strategies/tech_indicators/v12_vol/backtest.py
模拟结果，非投资建议。
"""
import importlib.util
import os
import sys

try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:  # noqa
    pass

_HERE = os.path.dirname(os.path.abspath(__file__))
_PARENT = os.path.dirname(_HERE)                     # strategies/tech_indicators
_ROOT = os.path.dirname(os.path.dirname(_PARENT))    # 仓库根
for _p in (_ROOT, os.path.join(_ROOT, "data")):
    if _p not in sys.path:
        sys.path.insert(0, _p)


def _load(path, name):
    spec = importlib.util.spec_from_file_location(name, path)
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


base = _load(os.path.join(_PARENT, "backtest.py"), "ti_base")
runner = _load(os.path.join(_PARENT, "_baseline_bt.py"), "ti_baseline_bt")
rule = runner.load_rule(os.path.join(_HERE, "rule.py"), "_ti_rule_" + os.path.basename(_HERE))

VARIANT = rule.VARIANT


def build_variant(close, r):
    """本变种（VOL）的目标仓位序列（0/1）。"""
    return rule.signal(close, r)


if __name__ == "__main__":
    runner.main(base, rule.FAMILY_KEY, build_variant, _HERE)
