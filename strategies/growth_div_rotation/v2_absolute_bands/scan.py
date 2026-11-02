#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""成长/红利轮动 v2（绝对水位 0.35/0.65）· 每日状态快照

与 v0（相对分位）、v1（绝对阈值 + 5 期分批）、v5（再叠波动率目标）并列：
v2 回答的是「R 的绝对水位到没到极端带」，动作是**一次性整仓切换**（无分批、无冷却）。

口径与回测逐行一致（`engine.signal_weights`）：用 `R[-1]` 决策、次一交易日生效。
"明日"的权重用**追加一个占位 bar** 的方式让状态机自己走一步：
把最新收盘复制成下一交易日后重跑 `signal_weights`，取最后一行 ——
这样状态机、初始侧、中间带保持原仓的语义都与回测完全同构，不需要在扫描脚本里
复写规则。

产物（AGENTS §8.4 ⑤）：
* `daily/_gd_v2_daily.md`    —— 人读
* `daily/_gd_v2_signal.json` —— pipeline 每日看板读取（`pipeline/daily_digest.py`）

用法::

    python strategies/growth_div_rotation/v2_absolute_bands/scan.py            # 用缓存
    python strategies/growth_div_rotation/v2_absolute_bands/scan.py --refresh  # 先转调 v0/data.py
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

import pandas as pd  # noqa: E402

import data  # noqa: E402  pylint: disable=wrong-import-position
import engine  # noqa: E402  pylint: disable=wrong-import-position
import rule  # noqa: E402  pylint: disable=wrong-import-position

OUT_MD = os.path.join(_DIR, "daily", "_gd_v2_daily.md")
OUT_JSON = os.path.join(_DIR, "daily", "_gd_v2_signal.json")


def _label(w: float) -> str:
    return "成长" if w >= 0.5 else "红利"


def _next_bday(idx):
    return idx[-1] + pd.tseries.offsets.BDay(1)


def build() -> dict:
    wide = data.load_wide()
    g = wide[rule.GROWTH_INDEX]
    d = wide[rule.DIV_INDEX]
    r = (g / d).dropna()
    g, d = g.reindex(r.index), d.reindex(r.index)

    w = engine.signal_weights(r)
    res = engine.run(g, d, w)

    # 明日：复制最新收盘成占位 bar，让状态机自己再走一步（与回测同构）
    nx = _next_bday(r.index)
    g2 = pd.concat([g, pd.Series([g.iloc[-1]], index=[nx])])
    d2 = pd.concat([d, pd.Series([d.iloc[-1]], index=[nx])])
    r2 = (g2 / d2).dropna()
    w_next = engine.signal_weights(r2).iloc[-1]

    cur_w = float(w.iloc[-1])
    nxt_w = float(w_next)
    r_now = float(r.iloc[-1])
    r_prev = float(r.iloc[-2]) if len(r) >= 2 else float("nan")

    if abs(nxt_w - cur_w) < 1e-9:
        action = "不动"
        reason = (f"{rule.RATIO_LOW:.2f} < R={r_now:.4f} < {rule.RATIO_HIGH:.2f}（中间带保持原仓）"
                  if rule.RATIO_LOW < r_now < rule.RATIO_HIGH
                  else f"R={r_now:.4f} 未构成反向穿越")
    elif nxt_w > cur_w:
        action, reason = "加仓成长", f"R={r_now:.4f} ≤ {rule.RATIO_LOW:.2f}（创业板低估带）"
    else:
        action, reason = "减仓成长", f"R={r_now:.4f} ≥ {rule.RATIO_HIGH:.2f}（创业板高估带）"

    # 到反向阈值的距离（只对当前持仓方向有意义）
    if cur_w <= 0.5:
        dist_pct, trig_side, trig_r = rule.RATIO_LOW / r_now - 1.0, "growth", rule.RATIO_LOW
    else:
        dist_pct, trig_side, trig_r = rule.RATIO_HIGH / r_now - 1.0, "dividend", rule.RATIO_HIGH

    sw = engine.describe_switches(res["switches"], r)
    return {
        "strategy": "growth_div_rotation/v2_absolute_bands",
        "as_of": str(r.index[-1].date()),
        "generated": datetime.now().strftime("%Y-%m-%d %H:%M"),
        "bars": int(len(r)),
        "start": str(r.index[0].date()),
        "R": round(r_now, 4),
        "R_prev": round(r_prev, 4),
        "low": rule.RATIO_LOW,
        "high": rule.RATIO_HIGH,
        "current_w": round(cur_w, 4),
        "current_label": _label(cur_w),
        "target_w": round(nxt_w, 4),
        "target_label": _label(nxt_w),
        "action": action,
        "reason": reason,
        "trigger": {"to_side": trig_side, "R": trig_r, "dist_pct": round(dist_pct, 4),
                    "dist_text": f"R 再{'跌' if dist_pct < 0 else '涨'} {abs(dist_pct):.1%} 至 {trig_r:.2f} 触发"},
        "switches": sw[-3:],
        "exec": {"growth": {"code": rule.EXEC["growth"][0], "name": rule.EXEC["growth"][1]},
                 "dividend": {"code": rule.EXEC["div"][0], "name": rule.EXEC["div"][1]}},
        "params": {"ratio_low": rule.RATIO_LOW, "ratio_high": rule.RATIO_HIGH,
                   "w_at_low": rule.W_AT_LOW, "w_at_high": rule.W_AT_HIGH,
                   "init_side": rule.INIT_SIDE, "fee": rule.FEE},
    }


def render(d: dict) -> str:
    t = d["trigger"]
    L = [f"# 成长/红利轮动 · v2 每日状态（{d['as_of']}）", "",
         f"> 生成 {d['generated']} · 数据 {d['start']}~{d['as_of']}（{d['bars']} 交易日）· "
         f"成长=创业板指({rule.GROWTH_INDEX}) ÷ 红利=中证红利({rule.DIV_INDEX})",
         f"> 口径：R 的**绝对水位**；R ≤ {d['low']:.2f} 加仓到满仓成长 / "
         f"R ≥ {d['high']:.2f} 减仓到满仓红利 / 中间带保持原仓、不交易；次一交易日生效。",
         "> ⚠ 阈值是**全样本内**挑出来的绝对值（量纲相关），`R ≤ 0.35` 占 23.6% 交易日、"
         "`R ≥ 0.65` 占 5.0%，换指数/基期须重标定（见 README 与 backtest/_v2_report.md）。", "",
         "## 一、今日状态", "",
         f"- 当前持仓：**{d['current_label']}**（成长权重 {d['current_w']:.0%}）",
         f"- 明日动作：**{d['action']}** —— {d['reason']}",
         f"- 明日目标：{d['target_label']}（成长权重 {d['target_w']:.0%}）",
         f"- 下次换仓：{t['dist_text']}（转{'成长' if t['to_side'] == 'growth' else '红利'}）",
         f"- 执行通道：成长 `{d['exec']['growth']['code']}` {d['exec']['growth']['name']}　"
         f"红利 `{d['exec']['dividend']['code']}` {d['exec']['dividend']['name']}", "",
         "## 二、读数", "",
         "| 项 | 值 |", "|---|---|",
         f"| R（今 / 昨） | {d['R']:.4f} / {d['R_prev']:.4f} |",
         f"| RATIO_LOW / HIGH | {d['low']:.2f} / {d['high']:.2f} |",
         f"| 当前成长权重 | {d['current_w']:.0%} |",
         f"| 明日成长权重 | {d['target_w']:.0%} |", ""]

    L += ["## 三、最近换仓", "", "| 日期 | 由 | 切到 | 决策用 R | 单边换手 |", "|---|---|---|---:|---:|"]
    for s in d["switches"]:
        L.append(f"| {s['date']} | {_label(s['from_w'])} | **{_label(s['to_w'])}** "
                 f"| {s.get('R_prev') if s.get('R_prev') is not None else '—'} | {s['turn']:.0%} |")
    if not d["switches"]:
        L.append("| — | — | — | — | — |")

    L += ["", "> 机械规则输出，非投资建议。v2 全期 6 次换仓、年化 +17.27% 但回撤 -51.85%："
          "它是「信号层裸基准」，不是可直接实盘的仓位方案；风控层见 v3（趋势闸门）与 v5（波动率目标）。"]
    return "\n".join(L) + "\n"


def main() -> None:
    ap = argparse.ArgumentParser(description="成长/红利轮动 v2 每日状态快照")
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

    root = os.path.dirname(os.path.dirname(_DIR))
    print(f"[v2] {d['as_of']} · R {d['R']:.4f} · 持 {d['current_label']}（成长 {d['current_w']:.0%}）· "
          f"{d['action']} → {d['target_label']}（成长 {d['target_w']:.0%}）")
    print(f"[v2] {os.path.relpath(OUT_MD, root)}")
    print(f"[v2] {os.path.relpath(OUT_JSON, root)}")


if __name__ == "__main__":
    main()
