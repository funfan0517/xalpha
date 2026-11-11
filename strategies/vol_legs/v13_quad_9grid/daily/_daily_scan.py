# -*- coding: utf-8 -*-
"""V13 · 每日操作（九宫格许可 + 等波动组合权重）。

流程（对齐 v5 的 `daily/_daily_scan.py`）：

1. 读**场内 7 腿**最新 OHLCV（`data/_ohlcv.json`，由 `tech_indicators/v18_volume_notes/ohlcv.py` 增量刷新）；
2. 逐腿算 S（0~3）/ 力度（站上·未定·跌破）/ 格 / 许可，并给出**相对昨日许可的动作**；
3. 组合层：**只对 `rule.CORE_LEGS`（hs300 + gold）** 等波动配权（滚动 60 日）× 许可 = 目标暴露；
4. 输出：今日需操作的腿 + 全腿状态表 + 近 120 日动作流水。

为什么只有两条腿：`research/_v13_walkforward.md` 的样本外检验里，只有沪深300 稳定通过
（3 窗 2 胜）、黄金次之（2/3）；恒生科技仅 1 个样本外窗口 → 降为观察；
纳指/标普/科创50 机制不适用 → 剔除。**7 腿仍在跑，但只有 CORE_LEGS 参与配权与下单**，
其余腿的信号只做诊断（留着当对照，删了就失去「为什么剔除」的证据）。

**T 日盘后出信号，T+1 收盘前执行**（与回测的 `w.shift(1)` 一致）。
产物：`daily/_v13_daily.md` / `_v13_daily.json`。机械规则输出，非投资建议。
"""
import io
import json
import os
import sys

import numpy as np
import pandas as pd

try:
    sys.stdout.reconfigure(encoding="utf-8")  # type: ignore[attr-defined]
except Exception:  # noqa: BLE001
    pass

_HERE = os.path.dirname(os.path.abspath(__file__))
_V13 = os.path.dirname(_HERE)
if _V13 not in sys.path:
    sys.path.insert(0, _V13)

import rule           # noqa: E402
import data as vdata  # noqa: E402
import engine         # noqa: E402

OUT_MD = os.path.join(_HERE, "_v13_daily.md")
OUT_JSON = os.path.join(_HERE, "_v13_daily.json")
RECENT = 120
POOL = [l for l in rule.CORE_LEGS if l in rule.LEGS]   # 只有执行范围参与等波动配权


def fmt_w(x):
    return f"{x * 100:.0f}%"


def main():
    avail = vdata.available()
    dfs = {l: vdata.frame(l) for l in avail}
    sms, diags = {}, {}
    for l in avail:
        sm = engine.state_machine(dfs[l])
        sms[l] = sm
        diags[l] = sm["diag"]

    date = max(dfs[l].index[-1] for l in avail)

    # ---- 等波动权重（滚动 60 日收益波动倒数，A500 不进池）
    vols = {}
    for l in POOL:
        if l not in dfs:
            continue
        r = dfs[l]["close"].pct_change().tail(60)
        vols[l] = float(r.std())
    inv = {k: (1.0 / v if v and v > 0 else 0.0) for k, v in vols.items()}
    tot = sum(inv.values())
    wgt = {k: (v / tot if tot else 0.0) for k, v in inv.items()}

    rows = []
    for l in avail:
        d = diags[l].iloc[-1]
        w_now = float(sms[l]["w"].iloc[-1])
        w_prev = float(sms[l]["w"].iloc[-2])
        acts = engine.actions(sms[l]["w"])
        last_act = acts[-1][0] if acts else None
        gap = (dfs[l].index[-1] - last_act).days if last_act is not None else None
        rows.append(dict(
            leg=l, name=rule.NAMES[l], code=rule.LEGS[l][0], cell=d["cell"] or "—",
            scope=("核心" if l in rule.CORE_LEGS else
                   "观察" if l in rule.WATCH_LEGS else "诊断"),
            S=0, bg="", P="",                       # 下面用 engine.score 补
            cap=w_now, cap_prev=w_prev, act=w_now - w_prev,
            floor=bool(d["floor"]), weight=wgt.get(l), target=wgt.get(l, 0.0) * w_now,
            last_act=last_act, gap_days=gap,
        ))
    # 背景分与力度从 state_machine 的诊断表补（diag 只存了格名与许可）
    for r in rows:
        ind = engine.indicators(dfs[r["leg"]])
        sc = engine.score(ind).iloc[-1]
        r["S"] = int(sc["S"])
        r["bg"] = sc["bg"]
        r["P"] = sc["P"]

    # 兜底触发当天许可还不会变（T+1 减半、T+2 到 0.2），必须单独提示，否则会漏掉减仓
    for r in rows:
        r["pending_floor"] = bool(r["floor"] and r["cap"] > rule.FLOOR_CAP + 1e-9)
    todo = [r for r in rows
            if r["scope"] == "核心" and (abs(r["act"]) > 1e-9 or r["pending_floor"])]
    others = [r for r in rows
              if r["scope"] != "核心" and (abs(r["act"]) > 1e-9 or r["pending_floor"])]
    combo_target = sum(r["target"] for r in rows if r["leg"] in POOL)

    L = [
        f"# V13 · 趋势许可九宫格 · 每日操作（{date.date()}）",
        "",
        f"> **执行范围 = {' + '.join(rule.NAMES[l] for l in rule.CORE_LEGS)}**"
        "（2026-11-10 用户指定。⚠ 中证A500 样本不足 1 年、信号仅作参考；"
        "其余腿只做诊断，不参与配权与下单）。",
        f"> 口径：兜底阈值 {rule.DD_TRIGGER:.0%} · 迟滞 {rule.HOLD_DAYS} 日（只约束升格）· "
        f"空仓 {rule.CASH_ANNUAL:.2%} 货基。**T 日收盘出信号、T+1 收盘前执行**。",
        "",
        "## 1. 今日需操作（仅执行范围）",
        "",
    ]
    if todo:
        L += ["| 腿 | 代码 | 来自 → 到 | 幅度 | 依据 |", "|---|---|---|---:|---|"]
        for r in todo:
            if r["pending_floor"]:
                why = (f"兜底触发（20 日回撤 ≤ {rule.DD_TRIGGER:.0%}）："
                       f"T+1 减至 {max(rule.OBSERVE, (r['cap'] + rule.OBSERVE) / 2):.0%}，"
                       f"T+2 减至 {rule.FLOOR_CAP:.0%}")
                arrow = f"{fmt_w(r['cap_prev'])} → {fmt_w(rule.FLOOR_CAP)}"
                mag = f"{(rule.FLOOR_CAP - r['cap']) * 100:+.0f}pp"
            else:
                why = f"{r['cell']} → 许可 {fmt_w(r['cap'])}"
                arrow = f"{fmt_w(r['cap_prev'])} → {fmt_w(r['cap'])}"
                mag = f"{r['act'] * 100:+.0f}pp"
            L.append(f"| {r['name']} | `{r['code']}` | {arrow} | {mag} | {why} |")
    else:
        L += ["今日执行范围内无许可变化 —— 维持昨日仓位。"]
    if others:
        L += ["", "> 诊断腿今日也有信号变化（**不执行**，仅供观察）："
              + "；".join(
                  (f"{r['name']} 兜底减仓在途" if r["pending_floor"]
                   else f"{r['name']} {r['act'] * 100:+.0f}pp")
                  for r in others) + "。"]

    L += [
        "",
        f"组合目标暴露（等波动基准，仅执行范围）合计 **{combo_target:.0%}**"
        f"（= Σ 权重 × 许可；权重按滚动 60 日波动倒数归一）。",
        "",
        "## 2. 全腿状态",
        "",
        "| 腿 | 范围 | 代码 | S | 背景 | 力度 | 格子 | 许可 | 昨日 | 兜底 | 组合权重 | 目标暴露 | 距上次动作 |",
        "|---|---|---|---:|---|---|---|---:|---:|:--:|---:|---:|---:|",
    ]
    for r in rows:
        w_txt = "—" if r["weight"] is None else fmt_w(r["weight"])
        t_txt = "—" if r["weight"] is None else fmt_w(r["target"])
        gap = "—" if r["gap_days"] is None else f"{r['gap_days']} 天前"
        L.append(f"| {r['name']} | {r['scope']} | `{r['code']}` | {r['S']} | {r['bg']} | {r['P']} | {r['cell']} | "
                 f"{fmt_w(r['cap'])} | {fmt_w(r['cap_prev'])} | {'⚠' if r['floor'] else ''} | "
                 f"{w_txt} | {t_txt} | {gap} |")

    L += [
        "",
        "「兜底」列 ⚠ = 今日触发或仍在兜底状态。若此时「许可」仍高于 20%，说明两笔减仓在途"
        "（T+1 减半、T+2 到 20%），以 §1 为准 —— 表格里的许可是**当日仍可持有**的上限，不是明日目标。",
        "",
        "## 3. 近 120 个交易日动作流水",
        "",
    ]
    for l in avail:
        acts = [a for a in engine.actions(sms[l]["w"])][-8:]
        if not acts:
            continue
        items = "；".join(f"{t.date()} {v * 100:+.0f}pp" for t, v in acts)
        L.append(f"- **{rule.NAMES[l]}**：{items}")

    L += [
        "",
        f"> ⚠ 「目标暴露」= 权重 × 许可，是**上限**语义（许可只约束上限，不给目标仓位）；"
        "实际下单请对照自己的当前持仓，别把上限当指令。",
        "> 机械规则输出，非投资建议。",
    ]

    with io.open(OUT_MD, "w", encoding="utf-8", newline="\n") as f:
        f.write("\n".join(L))
    payload = dict(date=str(date.date()), dd_trigger=rule.DD_TRIGGER,
                   hold_days=rule.HOLD_DAYS, combo_target=combo_target,
                   legs=[{k: (str(v.date()) if hasattr(v, "date") else v)
                          for k, v in r.items()} for r in rows])
    with io.open(OUT_JSON, "w", encoding="utf-8", newline="\n") as f:
        f.write(json.dumps(payload, ensure_ascii=False, indent=1))

    print("操作表:", OUT_MD)
    print(f"  组合目标暴露 {combo_target:.0%}")
    for r in rows:
        act = f"{r['act'] * 100:+.0f}pp" if abs(r["act"]) > 1e-9 else "—"
        print(f"  {r['name']:8s} {r['cell']:8s} 许可 {fmt_w(r['cap']):>4s} 动作 {act:>6s}"
              f"{'  ⚠兜底' if r['floor'] else ''}")


if __name__ == "__main__":
    main()
