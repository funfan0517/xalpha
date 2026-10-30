#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""成长/红利轮动 v1（绝对阈值 0.20/0.31 + 5 期分批 + 定投）· 每日状态快照

与 v0（`../v0/scan.py`）并列：v0 回答「R 相对自身轨道在哪」，v1 回答「R 的绝对水位到没到极端」。
本脚本只把「v1 状态机推到最新交易日」的结果落盘，不做规则解释（解释见 README / RULES）。

口径与回测逐行一致（`engine.run`）：用 `R_{-1}` 决策、同 bar 收盘成交。
区别只在规则（绝对水位而非均线缓冲带），信号源与 v0 共用同一份指数缓存。

产物（AGENTS §8.4 ⑤）：
* `daily/_gd_v1_daily.md`    —— 人读
* `daily/_gd_v1_signal.json` —— pipeline 每日看板读取（`pipeline/daily_digest.py`）

用法::

    python strategies/growth_div_rotation/v1_ratio_bands/scan.py            # 用缓存
    python strategies/growth_div_rotation/v1_ratio_bands/scan.py --refresh  # 先转调 v0/data.py 刷新
"""

from __future__ import annotations

import argparse
import importlib.util
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

import data  # noqa: E402  pylint: disable=wrong-import-position
import engine  # noqa: E402  pylint: disable=wrong-import-position
import rule  # noqa: E402  pylint: disable=wrong-import-position

OUT_MD = os.path.join(_DIR, "daily", "_gd_v1_daily.md")
OUT_JSON = os.path.join(_DIR, "daily", "_gd_v1_signal.json")

SIDE_CN = {"growth": "成长", "dividend": "红利"}


def _exec_pair():
    """执行通道沿用 v0 的 `rule.EXEC`（只读加载，避免两个 rule 模块在同一进程里撞名）。"""
    path = os.path.join(os.path.dirname(_DIR), "v0", "rule.py")
    spec = importlib.util.spec_from_file_location("_gd_v0_rule", path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)  # type: ignore[union-attr]
    return mod.EXEC["kc"], mod.EXEC["div"]


def build() -> dict:
    wide = data.load_wide()
    g = wide[rule.GROWTH_INDEX]
    d = wide[rule.DIV_INDEX]
    ratio = (g / d).dropna()
    res = engine.run(g, d, ratio)

    as_of = ratio.index[-1]
    r_now = float(ratio.iloc[-1])
    r_prev = float(ratio.iloc[-2]) if len(ratio) >= 2 else float("nan")

    # 当前标的 = 状态机最后一次换仓的落点（无换仓则为起始标的）
    target = rule.TARGET_INIT
    for s in res["switches"]:
        target = s["to_side"]
    pending_left = int(res["pending_left"])

    # 今日动作：分批未完成时只描述进度；完成后按 R 的绝对水位判定
    if pending_left > 0:
        action, reason = "分批中", f"5 期分批还剩 {pending_left} 份，继续按定投日买入{SIDE_CN[target]}"
    else:
        nxt = rule.check_signal(r_now, target, None)
        if nxt is None:
            action = "不动"
            reason = f"{rule.RATIO_LOW:.2f} < R={r_now:.4f} < {rule.RATIO_HIGH:.2f}（中间地带）"
        else:
            action = f"切{SIDE_CN[nxt]}"
            reason = (f"R={r_now:.4f} ≤ {rule.RATIO_LOW:.2f}" if nxt == "growth"
                      else f"R={r_now:.4f} ≥ {rule.RATIO_HIGH:.2f}")

    # 到下一次换仓的距离（只对当前持仓方向有意义）：R 需要变动多少百分比才碰到阈值
    if target == "dividend":
        dist_pct = rule.RATIO_LOW / r_now - 1.0     # 负值 = R 还要再跌多少
        trigger_side, trigger_R = "growth", rule.RATIO_LOW
    else:
        dist_pct = rule.RATIO_HIGH / r_now - 1.0    # 正值 = R 还要再涨多少
        trigger_side, trigger_R = "dividend", rule.RATIO_HIGH

    kc, dv = _exec_pair()
    return {
        "strategy": "growth_div_rotation/v1_ratio_bands",
        "as_of": str(as_of.date()),
        "generated": datetime.now().strftime("%Y-%m-%d %H:%M"),
        "bars": int(len(ratio)),
        "start": str(ratio.index[0].date()),
        "R": round(r_now, 4),
        "R_prev": round(r_prev, 4),
        "low": rule.RATIO_LOW,
        "high": rule.RATIO_HIGH,
        "target": target,
        "target_label": SIDE_CN[target],
        "pending_left": pending_left,
        "action": action,
        "reason": reason,
        "trigger": {"to_side": trigger_side, "R": trigger_R,
                    "dist_pct": round(dist_pct, 4),
                    "dist_text": f"R 再{'跌' if dist_pct < 0 else '涨'} {abs(dist_pct):.1%} 至 {trigger_R:.2f} 触发"},
        "switches": res["switches"][-3:],
        "tranche_last": res["tranche_log"][-3:],
        "exec": {"growth": {"code": kc[0], "name": kc[1]},
                 "dividend": {"code": dv[0], "name": dv[1]}},
        "params": {"tranche": rule.TRANCHE, "schedule": rule.TRANCHE_SCHEDULE,
                   "fixed_amount": rule.FIXED_AMOUNT, "fee": rule.FEE,
                   "interrupt_pending": rule.INTERRUPT_PENDING},
    }


def render(d: dict) -> str:
    t = d["trigger"]
    L = [f"# 成长/红利轮动 · v1 每日状态（{d['as_of']}）", "",
         f"> 生成 {d['generated']} · 数据 {d['start']}~{d['as_of']}（{d['bars']} 交易日）· "
         f"成长=科创50({rule.GROWTH_INDEX}) ÷ 红利=中证红利({rule.DIV_INDEX})",
         f"> 口径：R 的**绝对水位**；R ≤ {d['low']:.2f} 清红利分 {d['params']['tranche']} 期买成长 / "
         f"R ≥ {d['high']:.2f} 清成长分 {d['params']['tranche']} 期买红利 / 中间地带不动、只定投；次日执行。",
         "> 阈值是**量纲相关**的拟合值（只对本对指数成立），换指数/基期须重标定（见 README §6）。", "",
         "## 一、今日状态", "",
         f"- 当前标的：**{d['target_label']}**"
         + (f"（5 期分批还剩 {d['pending_left']} 份）" if d["pending_left"] else ""),
         f"- 今日动作：**{d['action']}** —— {d['reason']}",
         f"- 下次换仓：{t['dist_text']}（转{SIDE_CN[t['to_side']]}）",
         f"- 执行通道：成长 `{d['exec']['growth']['code']}` {d['exec']['growth']['name']}　"
         f"红利 `{d['exec']['dividend']['code']}` {d['exec']['dividend']['name']}", "",
         "## 二、读数", "",
         "| 项 | 值 |", "|---|---|",
         f"| R（今 / 昨） | {d['R']:.4f} / {d['R_prev']:.4f} |",
         f"| RATIO_LOW / HIGH | {d['low']:.2f} / {d['high']:.2f} |",
         f"| 当前标的 | {d['target_label']} |",
         f"| 分批剩余 | {d['pending_left']} 份 |", ""]

    L += ["## 三、最近换仓", "", "| 日期 | 由 | 切到 | R | 依据 |", "|---|---|---|---:|---|"]
    for s in d["switches"]:
        L.append(f"| {s['date']} | {SIDE_CN[s['from_side']]} | **{SIDE_CN[s['to_side']]}** "
                 f"| {float(s['R']):.4f} | {s['reason']} |")
    if not d["switches"]:
        L.append("| — | — | — | — | — |")

    L += ["", "## 四、最近分批", "", "| 日期 | 标的 | 金额 | 剩余份数 |", "|---|---|---:|---:|"]
    for s in d["tranche_last"]:
        L.append(f"| {s['date']} | {SIDE_CN[s['side']]} | {float(s['amount']):,.0f} | {s['left']} |")
    if not d["tranche_last"]:
        L.append("| — | — | — | — |")

    L += ["", "> 机械规则输出，非投资建议。v1 全期极少触发（阈值只在极端水位动作），"
          "统计上接近「事后挑两个日期」，实盘价值请自行折价判断。"]
    return "\n".join(L) + "\n"


def main() -> None:
    ap = argparse.ArgumentParser(description="成长/红利轮动 v1 每日状态快照")
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

    print(f"[v1] {d['as_of']} · R {d['R']:.4f} · 持 {d['target_label']} · {d['action']} · {d['reason']}")
    print(f"[v1] {os.path.relpath(OUT_MD, os.path.dirname(os.path.dirname(_DIR)))}")
    print(f"[v1] {os.path.relpath(OUT_JSON, os.path.dirname(os.path.dirname(_DIR)))}")


if __name__ == "__main__":
    main()
