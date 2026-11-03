#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""成长/红利轮动 v5（v3 + 18% 波动率目标）· 每日状态快照

三层叠加，扫描时逐层复现（全部调用 `engine` 的同一批函数，不在这里重写规则）：

  1. **R 层**：R ≤ 0.35 满仓成长 / R ≥ 0.65 满仓红利 / 中间带保持状态
  2. **趋势层**：创业板指破 MA200 → 成长侧减半；中证红利破 MA200 → 红利侧减半；削掉的进现金
  3. **波动层**：`scale = min(1, 18% / 120 日已实现波动)`，等比缩放总仓位，死区 0.15
  4. **全局限速**：任何两次权重变化至少隔 10 个交易日（`MIN_REBALANCE`）

"明日"的算法与 v2 相同 —— **追加一个占位 bar** 让状态机自己走一步：
复制最新收盘成下一交易日，重跑 `target_weights` / `vol_scale` / `throttle`，
取最后一行。这样冷却、死区、限速三道状态都连续，无需在扫描脚本里复写判定。

产物（AGENTS §8.4 ⑤）：
* `daily/_gd_v5_daily.md`    —— 人读
* `daily/_gd_v5_signal.json` —— pipeline 每日看板读取（`pipeline/daily_digest.py`）
* `backtest/_v5_alloc.png`   —— **每日重画**「三资产仓位 + 创业板指/中证红利点位 + 比值 R」，
  看板板块3 的 v5 卡片直接引用它（画图逻辑只在 `backtest.plot_alloc`，这里只喂当日权重）

用法::

    python strategies/growth_div_rotation/v5_vol_target/scan.py            # 用缓存
    python strategies/growth_div_rotation/v5_vol_target/scan.py --refresh  # 先转调 v0/data.py
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from datetime import datetime

try:
    sys.stdout.reconfigure(encoding="utf-8")
except (AttributeError, OSError):  # pragma: no cover
    pass

_DIR = os.path.dirname(os.path.abspath(__file__))
if _DIR not in sys.path:
    sys.path.insert(0, _DIR)

import numpy as np  # noqa: E402
import pandas as pd  # noqa: E402

import data  # noqa: E402  pylint: disable=wrong-import-position
import engine  # noqa: E402  pylint: disable=wrong-import-position
import factors  # noqa: E402  pylint: disable=wrong-import-position
import rule  # noqa: E402  pylint: disable=wrong-import-position

OUT_MD = os.path.join(_DIR, "daily", "_gd_v5_daily.md")
OUT_JSON = os.path.join(_DIR, "daily", "_gd_v5_signal.json")
CHART = os.path.join(_DIR, "backtest", "_v5_alloc.png")

_COLS = ["growth", "div", "cash"]
_CN = {"growth": "成长（创业板）", "div": "红利（中证红利）", "cash": "现金（货基/短债）"}


def _w(row) -> dict:
    return {c: round(float(row[c]), 4) for c in _COLS}


def _next_bday(idx):
    return idx[-1] + pd.tseries.offsets.BDay(1)


def _redraw(g, d, r, W):
    """重画 backtest/_v5_alloc.png（画图逻辑只写在 backtest.plot_alloc 里，这里喂当日权重）。"""
    import backtest  # 延迟导入：只有出图时才拉起 matplotlib
    return backtest.draw_main_chart(g, d, r, W)


def _action(base_cur, base_next, cur, nxt, tgt) -> tuple:
    """动作名 + 依据。优先级：限速挂起 > 换档 > 缩放 > 不动。"""
    if np.allclose(nxt.values, cur.values):
        if np.allclose(tgt.iloc[0].values, cur.values):
            return "不动", "三层目标与当前权重一致，无需调仓"
        return "不动（限速挂起）", (f"目标权重已变化但距上次调仓不足 {rule.MIN_REBALANCE} 个交易日，"
                                   "按全局限速顺延")

    if not np.allclose(base_next.values, base_cur.values):
        dg = float(base_next["growth"] - base_cur["growth"])
        if abs(dg) >= abs(float(base_next["div"] - base_cur["div"])):
            return ("加仓成长" if dg > 0 else "减仓成长"), "R 层 + 趋势层换档（缩放后同步生效）"
        return "换档到红利", "R 层 + 趋势层换档（缩放后同步生效）"

    d_exp = float(nxt[["growth", "div"]].sum() - cur[["growth", "div"]].sum())
    if d_exp < 0:
        return f"降仓 {abs(d_exp):.0%}", "波动层：已实现波动高于目标，等比压缩成长与红利"
    return f"加仓 {d_exp:.0%}", "波动层：已实现波动回落，风险预算放宽"


def build() -> dict:
    wide = data.load_wide()
    g0 = wide[rule.GROWTH_INDEX]
    d0 = wide[rule.DIV_INDEX]
    r = factors.ratio_series(g0, d0)
    g, d = g0.reindex(r.index), d0.reindex(r.index)

    W = engine.target_weights(r, g, d)
    res = engine.run(g, d, W)
    Ws, scale, vol, base = res["W"], res["scale"], res["vol"], res["base_W"]

    # 每日重画板块3 的那张图（仓位堆叠 + 两条点位 + 比值 R），喂的就是这份 Ws
    _redraw(g, d, r, Ws)

    cur = Ws.iloc[-1]
    base_cur = base.iloc[-1]

    # ---- 明日：占位 bar + 三个状态机各走一步 ----
    nx = _next_bday(r.index)
    g2 = pd.concat([g, pd.Series([g.iloc[-1]], index=[nx])])
    d2 = pd.concat([d, pd.Series([d.iloc[-1]], index=[nx])])
    r2 = factors.ratio_series(g2, d2)
    base_next = engine.target_weights(r2, g2.reindex(r2.index), d2.reindex(r2.index)).iloc[-1]

    vol_ext = pd.concat([vol, pd.Series([vol.iloc[-1]], index=[nx])])
    scale_next = float(factors.vol_scale(vol_ext, rule.VOL_TARGET, rule.VOL_DEADBAND,
                                         rule.VOL_FLOOR)[-1])
    tgt = engine.apply_scale(
        pd.DataFrame([base_next.values], index=[nx], columns=_COLS),
        pd.Series([scale_next], index=[nx]))
    nxt = engine.throttle(pd.concat([Ws, tgt]), rule.MIN_REBALANCE).iloc[-1]

    action, why = _action(base_cur, base_next, cur, nxt, tgt)

    r_now = float(r.iloc[-1])
    r_prev = float(r.iloc[-2]) if len(r) >= 2 else float("nan")
    ma_g = float(factors.trend_ma(g).iloc[-1]) if len(g) > rule.MA_TREND else float("nan")
    ma_d = float(factors.trend_ma(d).iloc[-1]) if len(d) > rule.MA_TREND else float("nan")
    broken_g = bool(g.iloc[-1] < ma_g) if not np.isnan(ma_g) else False
    broken_d = bool(d.iloc[-1] < ma_d) if not np.isnan(ma_d) else False

    # 距上次调仓的交易日数（全局限速的剩余等待）
    sig = [tuple(np.round(x, 4)) for x in Ws.values]
    last_change = 0
    for i in range(1, len(sig)):
        if sig[i] != sig[i - 1]:
            last_change = i
    gap = int(len(sig) - 1 - last_change)

    sw = engine.describe_switches(res["switches"], r)
    return {
        "strategy": "growth_div_rotation/v5_vol_target",
        "as_of": str(r.index[-1].date()),
        "generated": datetime.now().strftime("%Y-%m-%d %H:%M"),
        "bars": int(len(r)),
        "start": str(r.index[0].date()),
        "R": round(r_now, 4),
        "R_prev": round(r_prev, 4),
        "low": rule.RATIO_LOW,
        "high": rule.RATIO_HIGH,
        "weights": _w(cur),
        "base_weights": _w(base_cur),
        "chart": os.path.relpath(CHART, os.path.dirname(os.path.dirname(_DIR))).replace("\\", "/"),
        "next": {"weights": _w(nxt), "base": _w(base_next), "scale": round(scale_next, 3),
                 "throttled": not np.allclose(nxt.values, tgt.iloc[0].values),
                 "gap": gap, "wait": max(0, rule.MIN_REBALANCE - gap)},
        "scale": round(float(scale[-1]), 3),
        "vol": None if np.isnan(vol.iloc[-1]) else round(float(vol.iloc[-1]), 4),
        "target_vol": rule.VOL_TARGET,
        "deadband": rule.VOL_DEADBAND,
        "trend": {"growth_broken": broken_g, "div_broken": broken_d,
                  "ma_growth": None if np.isnan(ma_g) else round(ma_g, 2),
                  "ma_div": None if np.isnan(ma_d) else round(ma_d, 2),
                  "growth_close": round(float(g.iloc[-1]), 2),
                  "div_close": round(float(d.iloc[-1]), 2)},
        "action": action,
        "reason": why,
        "switches": sw[-3:],
        "exec": {"growth": {"code": rule.EXEC["growth"][0], "name": rule.EXEC["growth"][1]},
                 "dividend": {"code": rule.EXEC["div"][0], "name": rule.EXEC["div"][1]}},
        "params": {"vol_target": rule.VOL_TARGET, "vol_window": rule.VOL_WINDOW,
                   "deadband": rule.VOL_DEADBAND, "min_rebalance": rule.MIN_REBALANCE,
                   "ma_trend": rule.MA_TREND, "trend_cut": rule.TREND_CUT,
                   "cash_yield": rule.CASH_YIELD, "fee": rule.FEE},
    }


def render(d: dict) -> str:
    w, nx = d["weights"], d["next"]
    tr = d["trend"]
    vol_txt = "—（窗口未成形）" if d["vol"] is None else f"{d['vol']:.1%}"
    L = [f"# 成长/红利轮动 · v5 每日状态（{d['as_of']}）", "",
         f"> 生成 {d['generated']} · 数据 {d['start']}~{d['as_of']}（{d['bars']} 交易日）· "
         f"成长=创业板指({rule.GROWTH_INDEX}) ÷ 红利=中证红利({rule.DIV_INDEX})",
         f"> 三层叠加：R 水位（{d['low']:.2f}/{d['high']:.2f}）+ 双侧 MA{rule.MA_TREND} 闸门"
         f"（破线该侧 ×{1 - rule.TREND_CUT:.1f}）+ 波动率目标 {d['target_vol']:.0%}"
         f"（{rule.VOL_WINDOW} 日，死区 {d['deadband']:.2f}），全局限速 {rule.MIN_REBALANCE} 日。", "",
         "![v5 三资产仓位 · 创业板指/中证红利点位 · 比值 R](../backtest/_v5_alloc.png)", "",
         "## 一、今日状态", "",
         f"- 当前权重：成长 **{w['growth']:.0%}** / 红利 **{w['div']:.0%}** / 现金 **{w['cash']:.0%}**"
         f"（总暴露 {w['growth'] + w['div']:.0%}）",
         f"- 今日缩放系数：{d['scale']:.0%}　已实现波动：{vol_txt}",
         f"- 明日动作：**{d['action']}** —— {d['reason']}",
         f"- 明日目标：成长 {nx['weights']['growth']:.0%} / 红利 {nx['weights']['div']:.0%} / "
         f"现金 {nx['weights']['cash']:.0%}（目标系数 {nx['scale']:.0%}）"
         + (f"　⚠ 已顺延，还需等 {nx['wait']} 个交易日" if nx["throttled"] else ""), "",
         "## 二、三层读数", "",
         "| 层 | 读数 | 判读 |", "|---|---|---|",
         f"| R 水位 | {d['R']:.4f} | "
         f"{'≤ 0.35 成长侧' if d['R'] <= d['low'] else '≥ 0.65 红利侧' if d['R'] >= d['high'] else '0.35~0.65 保持原侧'} |",
         f"| 趋势（成长） | 创业板指 {tr['growth_close']} vs MA{rule.MA_TREND} "
         f"{tr['ma_growth'] if tr['ma_growth'] is not None else '—'} | "
         f"{'**破线，成长侧减半**' if tr['growth_broken'] else '线上'} |",
         f"| 趋势（红利） | 中证红利 {tr['div_close']} vs MA{rule.MA_TREND} "
         f"{tr['ma_div'] if tr['ma_div'] is not None else '—'} | "
         f"{'**破线，红利侧减半**' if tr['div_broken'] else '线上'} |",
         f"| 波动 | 已实现 {vol_txt} vs 目标 {d['target_vol']:.0%} | "
         f"缩放 {d['scale']:.0%}（死区 {d['deadband']:.2f}，跨档才动） |",
         f"| 限速 | 距上次调仓 {nx['gap']} 个交易日 | 门槛 {rule.MIN_REBALANCE} 日 |", ""]

    L += ["## 三、最近调仓", "", "| 日期 | 成长 | 红利 | 现金 | 换手 | 动作 |",
          "|---|---:|---:|---:|---:|---|"]
    for s in d["switches"]:
        f, t = s["from_w"], s["to_w"]
        L.append(f"| {s['date']} | {t[0]:.0%} | {t[1]:.0%} | {t[2]:.0%} | {s['turn']:.0%} "
                 f"| {s.get('action', '—')}（{f[0]:.0%}/{f[1]:.0%}/{f[2]:.0%} →） |")
    if not d["switches"]:
        L.append("| — | — | — | — | — | — |")

    L += ["", "> 机械规则输出，非投资建议。v5 全期 +14.23% / -26.74% / Calmar 0.53（家族最优），"
          "但平均只在场 63%、年化比 v3 少 1.4pp —— 波动层是**常驻的风险预算**，不是偶发的刹车。"]
    return "\n".join(L) + "\n"


def main() -> None:
    ap = argparse.ArgumentParser(description="成长/红利轮动 v5 每日状态快照")
    ap.add_argument("--refresh", action="store_true", help="先转调 ../v0/data.py --refresh 刷新指数缓存")
    args = ap.parse_args()

    if args.refresh:
        rc = data.refresh()
        if rc != 0:
            sys.exit(f"行情刷新失败（退出码 {rc}）")

    d = build()
    os.makedirs(os.path.dirname(OUT_MD), exist_ok=True)
    with open(OUT_MD, "w", encoding="utf-8") as fh:
        fh.write(render(d))
    with open(OUT_JSON, "w", encoding="utf-8") as fh:
        json.dump(d, fh, ensure_ascii=False, indent=1)

    w = d["weights"]
    root = os.path.dirname(os.path.dirname(_DIR))
    print(f"[v5] {d['as_of']} · R {d['R']:.4f} · 持仓 {w['growth']:.0%}/{w['div']:.0%}/{w['cash']:.0%}"
          f" · {d['action']}")
    print(f"[v5] {os.path.relpath(OUT_MD, root)}")
    print(f"[v5] {os.path.relpath(OUT_JSON, root)}")
    print(f"[v5] {os.path.relpath(CHART, root)}（图已按当日数据重画）")


if __name__ == "__main__":
    main()
