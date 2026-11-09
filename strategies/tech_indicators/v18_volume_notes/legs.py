# -*- coding: utf-8 -*-
"""V18 的腿表与成本/现金常量（**本变种的参数权威源**）。

2026-11-09 从 `strategies/vol_legs/rule.py` **冻结快照**而来（当时 `v13_volume_notes` 迁入
`tech_indicators` 改名 v18）。只保留本变种用得到的字段：腿名 / 目标带 / 角色。

> ⚠ **同步要求**：本表的 `band` 是 vol_legs 主策略的「目标带 = 该腿可持有的上限」。
> 如果 `strategies/vol_legs/rule.py::LEGS` 改了带宽或腿表，本文件必须手动跟着改 ——
> v18 是**独立变种**（自足可跑），不做跨家族 import，宁可显式同步也不埋隐式耦合。
> 改动后必须重跑 `backtest.py` 与 `daily/_daily_scan.py`（所有 pp 数字都按这张表算）。

口径提醒：v18 的**信号与成交都用场内 ETF**（`ohlcv.py`），`band` 只用来把
「带内仓位 0/50/100%」换算成占总资产的 pp（`mult × band`）。
"""
import os
import sys

_HERE = os.path.dirname(os.path.abspath(__file__))
if _HERE not in sys.path:
    sys.path.insert(0, _HERE)

LEGS = {
    "a500": dict(name="中证A500", role="核心腿", band=2.5),
    "hs300": dict(name="沪深300", role="核心腿", band=2.5),
    "ndx": dict(name="纳指100", role="核心腿", band=10.0),
    "spx": dict(name="标普500", role="核心腿", band=10.0),
    "kc50": dict(name="科创50", role="进攻腿", band=2.5),
    "hkt": dict(name="恒生科技", role="进攻腿", band=2.5),
    "gold": dict(name="黄金", role="避险腿", band=10.0),
}
LEG_ORDER = ["a500", "hs300", "ndx", "spx", "kc50", "hkt", "gold"]
SLEEVE_PP = sum(LEGS[k]["band"] for k in LEG_ORDER)      # 40pp: 与 vol_legs 主策略同额度

CASH_ANNUAL = 0.014                                      # 货基年化（空仓计息）
BAND_PP = 5.0                                            # 绝对再平衡阈值（仅报告口径引用）

# 各腿占总资产的目标带权重（组合层加权用）
W = {k: float(LEGS[k]["band"]) / float(SLEEVE_PP) for k in LEG_ORDER}


def band_of(leg):
    """该腿目标带（pp）。"""
    return float(LEGS[leg]["band"])


def rebalance_pp(leg):
    """季度再平衡触发阈值 = min(绝对 5pp, 目标带 × 50%)（与 vol_legs 主策略同公式）。"""
    return min(BAND_PP, band_of(leg) * 0.5)


if __name__ == "__main__":
    try:
        sys.stdout.reconfigure(encoding="utf-8")  # type: ignore[attr-defined]
    except Exception:  # noqa: BLE001
        pass
    print(f"七腿额度合计 {SLEEVE_PP:.1f}pp · 货基 {CASH_ANNUAL:.2%}/年")
    for k in LEG_ORDER:
        print(f"  {k:6s} {LEGS[k]['name']:8s} {LEGS[k]['role']} 带 {band_of(k):5.1f}pp "
              f"= {W[k]:6.2%} of 总资产 · 再平衡阈值 {rebalance_pp(k):.2f}pp")
