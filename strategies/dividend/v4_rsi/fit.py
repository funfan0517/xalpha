#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""V4 · 单标的适配度：**RSI 择时 vs 买入持有**（ΔCalmar）—— 唯一实现。

回答的问题
----------------------------------------------------------------------
同一套「自身 RSI14 ≥70 清仓 / ≥50 减半、其余满仓」规则，落在**每一只**标的上，
比买入持有是**更好还是更差**？

    ΔCalmar = Calmar(择时) − Calmar(买入持有)
    ΔCalmar ≥ +0.02  → 最优策略 = RSI 择时（`rsi`）
    否则             → 最优策略 = 买入持有（`hold`）

> 这就是 2026-10-26 用来筛 5 只池的那条判据，2026-11-20 起**不再用它筛池**
> （池 = 全 10 只，见 `rule.POOL_MODE`），但**逐只**读它仍有意义：
> 它告诉你「池里哪几只根本不该被择时」——那些标的被 RSI 减仓是负和。

口径（与 V4 完全一致）
----------------------------------------------------------------------
* 收益源：基金**累计净值**（含分红再投）
* 分数：该标的自己的 Wilder RSI14，日频算、月末取值决策
* 执行：月末信号、**次月生效**、单边 0.03%（Σ|Δw|）
* 基准：同标的买入持有

被谁用
----------------------------------------------------------------------
* `universe.py`   → 10 只适配度页（`daily/_v4_universe.html`）
* `backtest.py`   → 报告附节「逐只最优策略」
* `daily.py`      → 每日报告/看板逐只标注「最优：RSI / 持有」
* `dashboard.py`  → 读 `daily/_v4_signal.json` 里的 `fit` 字段

产物：`v4_rsi/daily/_v4_fit.json`

用法::

    python strategies/dividend/v4_rsi/fit.py            # 打印 10 只并刷新 _v4_fit.json
"""

from __future__ import annotations

import json
import os
import sys

import pandas as pd

_DIR = os.path.dirname(os.path.abspath(__file__))
_STRAT = os.path.dirname(_DIR)
for _p in (_DIR, _STRAT):
    if _p not in sys.path:
        sys.path.insert(0, _p)

import _single_signal as S  # noqa: E402  pylint: disable=wrong-import-position
import pool  # noqa: E402  pylint: disable=wrong-import-position
import rule as V4  # noqa: E402  pylint: disable=wrong-import-position

OUT_JSON = os.path.join(_DIR, "daily", "_v4_fit.json")
MIN_DAYS = 260          # 日频样本不足（< 约 1 年）不做判断
BEST_EPS = 0.02         # |ΔCalmar| 小于这个数视为「与持有等价」，记作持有


def run_one(code: str, nav: pd.Series, start: str = S.START,
            window: int | None = None, q_out: float | None = None,
            q_half: float | None = None, fee: float | None = None) -> dict | None:
    """单标的「自身 RSI 择时」vs「买入持有」。返回含中间序列的完整 dict（供画图复用）。"""
    window = V4.WINDOW if window is None else window
    q_out = V4.Q_OUT if q_out is None else q_out
    q_half = V4.Q_HALF if q_half is None else q_half
    fee = S.FEE if fee is None else fee

    nav = nav.dropna().loc[start:]
    if len(nav) < MIN_DAYS:
        return None

    rsi_d = S.rsi_series(nav, window)
    me = nav.resample("ME").last().ffill()
    rets = me.pct_change().dropna()
    rsi_m = rsi_d.resample("ME").last().reindex(rets.index)
    mult = S.time_mult(rsi_m / 100.0, q_out, q_half).fillna(1.0)

    pos = mult.shift(1).fillna(1.0)                 # 次月生效；首个信号月按满仓
    turn = (pos - pos.shift(1).fillna(0.0)).abs()
    r_strat = (pos * rets - turn * fee).dropna()
    r_bh = rets.reindex(r_strat.index)

    st, bh = S.stats(r_strat), S.stats(r_bh)
    return {
        "code": code,
        "months": len(r_strat),
        "start": r_strat.index[0], "end": r_strat.index[-1],
        "cagr_rsi": st["cagr"], "dd_rsi": st["dd"], "calmar_rsi": st["calmar"],
        "cagr_hold": bh["cagr"], "dd_hold": bh["dd"], "calmar_hold": bh["calmar"],
        "dcalmar": st["calmar"] - bh["calmar"],
        "pos": float(pos.mean()),
        "n_cut": int((mult == 0).sum()), "n_half": int(((mult > 0) & (mult < 1)).sum()),
        # —— 中间序列（不落 JSON，供 universe.py 画图 / 分档统计）——
        "nav": nav, "rsi_d": rsi_d, "rsi_m": rsi_m, "mult": mult,
        "rets": rets, "r_strat": r_strat, "r_bh": r_bh,
    }


def best_of(dcalmar: float) -> str:
    """最优策略标签：`rsi` = RSI 择时，`hold` = 买入持有。"""
    return "rsi" if dcalmar >= BEST_EPS else "hold"


def best_label(dcalmar: float) -> str:
    """一行式标签：`RSI 择时（Δ+0.42）` / `买入持有（Δ-0.05）` / `买入持有（与择时持平）`。"""
    tag = "RSI 择时" if best_of(dcalmar) == "rsi" else "买入持有"
    if abs(dcalmar) < BEST_EPS:
        return f"{tag}（与择时持平）"
    return f"{tag}（Δ{dcalmar:+.2f}）"


def fit_all(navs: pd.DataFrame | None = None, codes: list[str] | None = None,
            **kw) -> list[dict]:
    """池内逐只适配度，按 ΔCalmar 降序。`navs` 缺省时按 `rule.CODES` 加载。"""
    codes = list(codes or V4.CODES)
    navs = pool.load_navs(codes=codes) if navs is None else navs
    rows = []
    for code in codes:
        if code not in navs.columns:
            continue
        r = run_one(code, navs[code], **kw)
        if r is None:
            continue
        r["name"] = pool.BY_CODE.get(code, {}).get("name", code)
        r["cat"] = pool.BY_CODE.get(code, {}).get("cat", "")
        r["best"] = best_of(r["dcalmar"])
        rows.append(r)
    return sorted(rows, key=lambda x: -x["dcalmar"])


def payload(rows: list[dict]) -> dict:
    """只保留可序列化字段（中间序列丢掉）。"""
    keep = ("code", "name", "cat", "months", "cagr_rsi", "dd_rsi", "calmar_rsi",
            "cagr_hold", "dd_hold", "calmar_hold", "dcalmar", "best",
            "pos", "n_cut", "n_half")
    return {r["code"]: {k: (round(v, 4) if isinstance(v, float) else v) for k, v in r.items()
                        if k in keep} for r in rows}


def write_json(rows: list[dict], path: str = OUT_JSON) -> str:
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as fh:
        json.dump({"as_of": str(max(r["end"] for r in rows).date()),
                   "basis": "单标的 RSI14 70/50 择时 vs 买入持有（ΔCalmar）",
                   "funds": payload(rows)}, fh, ensure_ascii=False, indent=2)
    return path


def load_json(path: str = OUT_JSON) -> dict:
    with open(path, encoding="utf-8") as fh:
        return json.load(fh)


def main() -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    rows = fit_all()
    path = write_json(rows)
    print(f"[fit] {len(rows)} 只 · 判据 ΔCalmar ≥ +{BEST_EPS:.2f} 记 RSI 择时，否则买入持有")
    for r in rows:
        mark = "RSI " if r["best"] == "rsi" else "持有"
        print(f"[fit] {r['code']} {r['name'][:16]:16s} {r['months']:3d}m  "
              f"持有 {r['cagr_hold'] * 100:5.1f}%/{r['dd_hold'] * 100:6.1f}%/"
              f"{r['calmar_hold']:5.2f}  择时 {r['cagr_rsi'] * 100:5.1f}%/"
              f"{r['dd_rsi'] * 100:6.1f}%/{r['calmar_rsi']:5.2f}  "
              f"Δ {r['dcalmar']:+.2f}  最优 {mark}")
    print(f"[fit] 产物 -> {os.path.relpath(path, _DIR)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
