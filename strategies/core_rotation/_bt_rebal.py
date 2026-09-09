# -*- coding: utf-8 -*-
"""对照: 仅均衡档 · 月度再平衡 vs 买入持有(不调仓)

组合A: 均衡中枢(10/25/15/25/5/20, 旧手册中央值) 每月末再平衡
组合B: 同一初始权重 买入持有(权重漂移, 永不调仓)
参考: 六腿等权 × 两种执行方式

口径: 月度; t末定权 -> 下月收益(无前视); 未计费; 样本 2019-09~2026-09(科创50上市后)
用法: python strategies/core_rotation/_bt_rebal.py
输出: strategies/core_rotation/_bt_rebal.md + data/_bt_rebal.json
"""
import json
import os
import sys
from datetime import datetime

import numpy as np
import pandas as pd

_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)
sys.path.insert(0, os.path.join(_ROOT, "strategies", "core_rotation"))
import _backtest as bt  # noqa: E402

_KEYS = bt._KEYS
_NAMES = {row["key"]: row["name"] for row in bt.rule.pool()}
_W = {"bond": 0.10, "dividend": 0.25, "ndx": 0.15, "a500": 0.25, "kc": 0.05, "gold": 0.20}  # 旧均衡中枢


def build_series():
    m = bt.build_monthly()
    retm = pd.DataFrame({k: bt._m(bt.csi_index(c) if kind == "csi" else bt.xq_bars(c))
                         for k, (kind, c) in bt._LEG.items()}).pct_change()
    common = sorted(t for t in retm.index if t >= pd.Timestamp("2019-08-01"))
    common = [t for t in common if t <= pd.Timestamp("2026-09-30")]
    if len(common) < 5:
        raise RuntimeError("样本不足")
    # 各腿逐月累计净值(基期=common[0], 值=1)
    levels = pd.DataFrame(1.0, index=pd.to_datetime(common), columns=_KEYS)
    for i in range(1, len(common)):
        r = retm.loc[common[i]]
        for k in _KEYS:
            levels.iloc[i, levels.columns.get_loc(k)] = \
                levels.iloc[i - 1, levels.columns.get_loc(k)] * (1 + (r[k] if not np.isnan(r[k]) else 0.0))
    return retm, levels, common


def perf(nav):
    mdd = float((nav / nav.cummax() - 1).min())
    vol = float(nav.pct_change().std() * np.sqrt(12) * 100)
    return float(nav.iloc[-1]), float((nav.iloc[-1] ** (1 / yrs) - 1) * 100), vol, mdd * 100


yrs = None


def main():
    global yrs
    retm, levels, common = build_series()
    yrs = (common[-1] - common[0]).days / 365.25
    idx = pd.to_datetime(common)

    w = {k: _W[k] / sum(_W.values()) for k in _KEYS}

    # A: 月度再平衡(每期固定权重)
    navr = [1.0]
    for i in range(1, len(common)):
        r = retm.loc[common[i]]
        navr.append(navr[-1] * (1 + sum(w[k] * r[k] for k in _KEYS if not np.isnan(r[k]))))
    s_rebal = pd.Series(navr, index=idx)

    # B: 买入持有(同初始权重, 权重随漂移)
    s_bh = levels.mul(pd.Series(w)).sum(axis=1)

    # 等权两种执行
    we = {k: 1 / len(_KEYS) for k in _KEYS}
    navr_e = [1.0]
    for i in range(1, len(common)):
        r = retm.loc[common[i]]
        navr_e.append(navr_e[-1] * (1 + sum(we[k] * r[k] for k in _KEYS if not np.isnan(r[k]))))
    s_erebal = pd.Series(navr_e, index=idx)
    s_ebh = levels.mul(pd.Series(we)).sum(axis=1)

    def row(name, s):
        cum, ann, vol, mdd = perf(s)
        return name, cum, ann, vol, mdd

    rows = [row("均衡档·月度再平衡", s_rebal), row("均衡档·买入持有(不调仓)", s_bh),
            row("等权·月度再平衡", s_erebal), row("等权·买入持有(不调仓)", s_ebh)]

    L = ["# 仅均衡档 · 月度再平衡 vs 买入持有", "",
         f"> 生成 {datetime.now():%Y-%m-%d %H:%M} · 样本 {common[0].date()} ~ {common[-1].date()} "
         f"({yrs:.1f}年, 月度, 未计费, 无前视)", "",
         "> 均衡档权重 = 旧手册中枢 债10/红利25/纳指15/A500 25/科创5/金20 (初始100%)。",
         "> 再平衡=每期末回到固定权重; 买入持有=初始权重随市值漂移、永不调仓。", "",
         "| 组合 | 累计 | 年化 | 年化波动 | 最大回撤 |", "|---|---:|---:|---:|---:|"]
    for name, cum, ann, vol, mdd in rows:
        L.append(f"| {name} | {cum*100:.0f}% | {ann:.1f}% | {vol:.1f}% | {mdd:.1f}% |")
    ann_rebal = rows[0][2]
    ann_bh = rows[1][2]
    drift = pd.DataFrame({k: levels[k] / levels[k].iloc[0] for k in _KEYS})
    L += ["", "## 各腿累计(买入持有视角)", "", "| 资产 | 累计涨幅 | 期末漂移权重 |", "|---|---:|---:|"]
    for k in _KEYS:
        tot = drift[k].iloc[-1]
        L.append(f"| {_NAMES[k]} | {(tot-1)*100:+.0f}% | {w[k]*tot/sum(w[j]*drift[j].iloc[-1] for j in _KEYS)*100:.1f}% |")
    L += ["", f"> 再平衡-买入持有 年化差: {ann_rebal-ann_bh:+.1f}pp (未计费; 调仓成本会再吃掉一部分)",
          "> 免责声明: 代理口径估算, 非投资建议。"]
    txt = "\n".join(L) + "\n"
    with open(os.path.join(os.path.dirname(os.path.abspath(__file__)), "_bt_rebal.md"),
              "w", encoding="utf-8") as f:
        f.write(txt)
    print(txt)
    json.dump({r[0]: {"cum": r[1], "ann": r[2], "vol": r[3], "mdd": r[4]} for r in rows},
              open(os.path.join(_ROOT, "data", "_bt_rebal.json"), "w", encoding="utf-8"),
              ensure_ascii=False, indent=2)


if __name__ == "__main__":
    main()
