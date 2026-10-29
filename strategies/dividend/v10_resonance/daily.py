#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""V10 · 今日状态机读数（三重共振：回撤 × 均线 × 股息率）

这个脚本回答三个问题，按状态机的**当前状态**分行：

1. **现在处在哪个状态**（idle / holding / sold）—— 由 `engine.run` 从数据起点重放到今天，
   与回测**同一套代码**，不另写一份判断逻辑（否则「回测说会卖、每日说还拿着」）。
2. **三条买点条件各差多少** —— 逐条给现值、阈值、差值与「触发所需价位」。
3. **如果已持仓，三条卖线在哪** —— 前高（锁定值）、满 250 日的复核日（到期仍共振则续期）、
   止损价；如果空仓，还差什么才够共振。

⚠ 这里的「持仓」是**规则推演出来的状态**（假设严格按规则执行），不是你券商账户的真实持仓。
真实持仓若与推演不同（比如手动加过仓、或没按 TTL 卖出），请以账户为准，并把这套读数当「规则视角」。

产物（AGENTS §8.4 ⑤）：
* `v10_resonance/daily/_v10_signal.md`   人读
* `v10_resonance/daily/_v10_signal.json` 结构化（pipeline / 看板可读）

用法::

    python strategies/dividend/v10_resonance/daily.py            # 用缓存
    python strategies/dividend/v10_resonance/daily.py --refresh  # 先抓最新行情
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from datetime import datetime

import numpy as np
import pandas as pd

_DIR = os.path.dirname(os.path.abspath(__file__))
_STRAT = os.path.dirname(_DIR)
for _p in (_DIR, _STRAT):
    if _p not in sys.path:
        sys.path.insert(0, _p)

import data  # noqa: E402  pylint: disable=wrong-import-position
import engine  # noqa: E402  pylint: disable=wrong-import-position
import factors  # noqa: E402  pylint: disable=wrong-import-position
import rule  # noqa: E402  pylint: disable=wrong-import-position

CHAIN = "价格指数（前高/年线/三年线）+ 全收益÷价格（股息率）→ 三条件共振 → 状态机"

STATE_CN = {"idle": "空仓待机", "holding": "持仓中", "sold": "已卖出（冷静期）"}


def collect(refresh: bool = False) -> dict:
    panels = factors.build(refresh=refresh)
    if rule.PRIMARY not in panels:
        raise RuntimeError(f"主标的 {rule.PRIMARY} 无数据，先跑 data.py 挑数据源")
    asof, stale = factors.align_asof(panels)
    official = data.official_dy()

    main = panels[rule.PRIMARY].loc[:asof]
    res = engine.run(main)
    na = engine.next_actions(res, main.index[-1])

    rows = []
    for code, d in panels.items():
        d = d.loc[:asof]
        if d.empty:
            continue
        r = d.iloc[-1]
        rows.append({
            "index": code, "name": rule.BY_INDEX[code]["name"],
            "close": round(float(r["close"]), 2),
            "ma250": None if pd.isna(r["ma250"]) else round(float(r["ma250"]), 2),
            "ma750": None if pd.isna(r["ma750"]) else round(float(r["ma750"]), 2),
            "peak": round(float(r["peak"]), 2),
            "dd": round(float(r["dd"]), 4),
            "dy": round(float(r["dy"]), 4),
            "dy_official": official.get(code),
            "below_ma": bool(r["below_ma"]),
            "asof": d.index[-1].strftime("%Y-%m-%d"),
            "stale": code in stale,
        })

    conds = [
        {"key": "dd", "label": f"回撤 ≥ {rule.DD_BUY:.0%}",
         "value": na["dd"], "threshold": rule.DD_BUY, "ok": na["dd"] >= rule.DD_BUY,
         "hint": f"前高 {na['peak']:.0f} → 触发价 ≤ {na['peak'] * (1 - rule.DD_BUY):.0f}"},
        {"key": "ma", "label": f"收盘 ≤ 年线({rule.MA_SHORT}) 或 三年线({rule.MA_LONG})",
         "value": None, "threshold": None, "ok": na["below_ma"],
         "hint": (f"年线 {main['ma250'].iloc[-1]:.0f} / 三年线 {main['ma750'].iloc[-1]:.0f} / "
                  f"收盘 {na['close']:.0f}")},
        {"key": "dy", "label": f"股息率 ≥ {rule.DY_BUY:.1%}",
         "value": na["dy"], "threshold": rule.DY_BUY, "ok": na["dy"] >= rule.DY_BUY,
         "hint": (f"还差 {(rule.DY_BUY - na['dy']) * 100:+.2f}pp"
                  if na["dy"] < rule.DY_BUY else f"已超 {(na['dy'] - rule.DY_BUY) * 100:+.2f}pp")},
    ]
    tranches = [
        {"stage": 1, "trigger": f"三条件共振（dy≥{rule.DY_BUY:.1%}）",
         "w": rule.MAX_POS * rule.TRANCHE[0]},
        {"stage": 2, "trigger": f"dy≥{rule.DY_STRONG:.1%}（强买）",
         "w": rule.MAX_POS * rule.TRANCHE[1]},
        {"stage": 3, "trigger": f"dy≥{rule.DY_EXTREME:.1%} 或 回撤≥{rule.DD_DEEP:.0%}",
         "w": rule.MAX_POS * rule.TRANCHE[2]},
    ]

    holding = None
    if res["state"] == "holding" and res["open"]:
        cur = res["open"]
        i = len(main.index) - 1
        entry_i = int(main.index.get_loc(cur["entry_date"]))
        due_i = entry_i + rule.SELL_TTL
        left = max(due_i - i, 0)
        # 到期日：已走完就用真实交易日；还没走完就用「今天 + 剩余交易日数」的营业日估算
        due = (main.index[due_i].strftime("%Y-%m-%d") if due_i < len(main.index)
               else (main.index[-1] + pd.offsets.BDay(max(left, 1))).strftime("%Y-%m-%d（预估）"))
        holding = {
            "entry_date": cur["entry_date"].strftime("%Y-%m-%d"),
            "entry_price": round(float(cur["entry_price"]), 2),
            "days_held": i - entry_i,
            "stage": cur["stage"],
            "peak_lock": round(float(cur["peak"]), 2),
            "ttl_due": due,
            "ttl_days_left": left,
            "stop_price": round(float(cur["entry_price"]) * (1 - rule.STOP_LOSS), 2),
            "to_peak": round(float(cur["peak"] / na["close"] - 1), 4),
            "ret_so_far": round(float(cur["ret"]), 4),
            "next_add": (rule.DY_STRONG if cur["stage"] == 1 else
                         (rule.DY_EXTREME if cur["stage"] == 2 else None)),
        }

    return {
        "generated": datetime.now().strftime("%Y-%m-%d %H:%M"),
        "as_of": str(asof.date()),
        "stale": {c: d.strftime("%Y-%m-%d") for c, d in stale.items()},
        "primary": rule.PRIMARY,
        "state": res["state"], "state_cn": STATE_CN.get(res["state"], res["state"]),
        "na": na, "conds": conds, "tranches": tranches, "holding": holding,
        "rows": rows, "bt": load_bt_summary(),
    }


def load_bt_summary() -> dict:
    """读回测摘要（`backtest.py` 产出）。缺失返回 {}，正文退化成「见报告」。"""
    if not os.path.exists(rule.OUT_BT_SUM):
        return {}
    try:
        with open(rule.OUT_BT_SUM, encoding="utf-8") as fh:
            return json.load(fh)
    except (OSError, ValueError) as exc:
        print(f"[v10:warn] 回测摘要读取失败（{type(exc).__name__}），正文退回文字说明",
              file=sys.stderr)
        return {}


def _mark(ok: bool) -> str:
    return "✅" if ok else "❌"


def _dy_gap(d: dict) -> float:
    """反推股息率 − 官方快照（pp）；官方缺失时返回 nan。"""
    off = next((r["dy_official"] for r in d["rows"] if r["index"] == d["primary"]), None)
    return float("nan") if off is None else d["na"]["dy"] * 100 - off


def render(d: dict) -> str:
    na = d["na"]
    hold = d["holding"]
    lag = ""
    if d["stale"]:
        lag = (f"\n> ⚠ 数据滞后：{'、'.join(f'{c} 至 {v}' for c, v in d['stale'].items())} "
               f"未更新到 {d['as_of']}")
    L = [f"# V10 · 三重共振今日读数（{d['as_of']}）", "",
         f"> 生成 {d['generated']} · 数据截至 **{d['as_of']}** · 链路：{CHAIN}{lag}", ""]

    L += ["## 一、状态机", "",
          f"**当前状态：{d['state_cn']}**（`{d['state']}`）"
          f" · 规则推演仓位 **{na['expo']:.0%}**（仓位区间 0–100%）", ""]
    if hold:
        renew_note = (f"（到期若三条件仍共振则续期一年，最多 {rule.SELL_TTL_MAX} 日）"
                      if rule.SELL_TTL_RENEW else "")
        L += [f"- 建仓 {hold['entry_date']} @ **{hold['entry_price']:.2f}**，已持有 {hold['days_held']} 个交易日"
              f"（第 {hold['stage']}/3 批），累计 **{hold['ret_so_far'] * 100:+.1f}%**（全收益口径）。",
              f"- 三条卖线：①收复前高 **{hold['peak_lock']:.0f}**（还差 {hold['to_peak'] * 100:+.1f}%）"
              f" ②满 {rule.SELL_TTL} 日复核 **{hold['ttl_due']}**（剩 {hold['ttl_days_left']} 个交易日）"
              f"{renew_note}"
              f" ③止损 **{hold['stop_price']:.0f}**（较建仓价 −{rule.STOP_LOSS:.0%}）。",
              (f"- 下一批加仓触发：**股息率 ≥{hold['next_add']:.1%}**。"
               if hold.get("next_add") else "- 三批已打完，不再加仓。")]
    else:
        miss = [c["label"] for c in d["conds"] if not c["ok"]]
        L += [f"- 未触发建仓。缺的条件：{'、'.join(miss) if miss else '（三条都满足，但处于卖出冷静期）'}。",
              "- 空仓是常态：规则只在「深跌 + 破均线 + 高股息」同时出现时出手，"
              "本样本内平均仓位只有两成上下（见 §四）。"]
    L += [""]

    L += ["## 二、三条买点条件（必须**同时**满足）", "",
          "| # | 条件 | 现值 | 阈值 | 状态 | 说明 |", "|---:|---|---:|---:|:--:|---|"]
    for i, c in enumerate(d["conds"], 1):
        if c["key"] == "dy":
            val = f"{c['value'] * 100:.2f}%"
            th = f"{c['threshold']:.1%}"
        elif c["key"] == "dd":
            val = f"{c['value'] * 100:.1f}%"
            th = f"{c['threshold']:.0%}"
        else:
            val, th = "—", "—"
        L.append(f"| {i} | {c['label']} | {val} | {th} | {_mark(c['ok'])} | {c['hint']} |")
    L += ["",
          f"> 三条件全绿才是买点。股息率 ≥{rule.DY_STRONG:.1%} 为强买、≥{rule.DY_EXTREME:.1%} 为极端机会"
          "（决定加仓批次，不是独立买点）。", ""]

    L += ["## 三、分批计划（仓位 0–100%）", "",
          "| 批次 | 触发 | 本批占比 | 累计 |", "|---:|---|---:|---:|"]
    acc = 0.0
    for t in d["tranches"]:
        acc += t["w"]
        L.append(f"| {t['stage']} | {t['trigger']} | {t['w']:.0%} | {acc:.0%} |")
    L += ["", "> 不加杠杆；每批只在触发日按收盘价执行（回测口径：信号次日收盘）。"
          f"止损线 −{rule.STOP_LOSS:.0%}，卖出后正常 20 日 / 止损后 60 日冷静期。", ""]

    L += ["## 四、全部标的读数（含对照）", "",
          "| 指数 | 收盘 | 年线 | 三年线 | 前高 | 回撤 | 股息率(反推) | 官方快照 | 破均线 |",
          "|---|---:|---:|---:|---:|---:|---:|---:|:--:|"]
    for r in d["rows"]:
        mark = "**" if r["index"] == d["primary"] else ""
        off = "N/A" if r["dy_official"] is None else f"{r['dy_official']:.2f}%"
        L.append(f"| {mark}{r['index']} {r['name']}{mark} | {r['close']:.0f} | "
                 f"{'—' if r['ma250'] is None else f'{r["ma250"]:.0f}'} | "
                 f"{'—' if r['ma750'] is None else f'{r["ma750"]:.0f}'} | {r['peak']:.0f} | "
                 f"{r['dd'] * 100:.1f}% | {r['dy'] * 100:.2f}% | {off} | "
                 f"{'✅' if r['below_ma'] else '❌'} |")
    L += ["",
          "> 只有「有真实全收益指数」的标的才能算反推股息率（`rule.TARGETS`）。"
          "其余红利指数没有全收益序列，股息率只能靠 000922 变化率代理、水平不可信，"
          "套 4.5% 绝对阈值等于瞎猜，故**不纳入**。对照标的的股息率水平偏高约 1pp，"
          "同一阈值下会更频繁触发，不要用它和主标的比次数。", ""]

    L += ["## 五、口径与回测结论（必读）", ""]
    bt = d.get("bt") or {}
    if bt:
        b, f, w2 = bt["base"], bt["full"], bt.get("w2", {})
        L += [f"- 回测窗口 **{bt['window']}**（{bt['days']} 个交易日），首笔建仓 {bt['first_entry']}；"
              f"默认口径 {b['trades']} 笔、胜 {b['win']}、平均 {b['avg_ret'] * 100:+.1f}%；"
              f"一次性打满 Calmar {f['stats']['calmar']:.2f} vs 买入持有 {bt['bench']['calmar']:.2f}。",
              f"- 仓位 0–100%，但平均仓位只有 {b['expo']:.0%}、组合年化 "
              f"{b['stats']['cagr'] * 100:.2f}% —— 低仓位来自**信号稀少 + 冷静期**，不是上限压的。",
              f"- 卖出原因分布：收复前高 {bt['by_reason'].get('peak', {}).get('n', 0)} 笔、"
              f"到期未续期 {bt['by_reason'].get('ttl', {}).get('n', 0)} 笔、"
              f"止损 {bt['by_reason'].get('stop', {}).get('n', 0)} 笔。",
              f"- 同一配置内按退出原因分组：到期退出的平均 "
              f"{bt['by_reason'].get('ttl', {}).get('avg', 0) * 100:+.1f}%，"
              f"收复前高退出的平均 {bt['by_reason'].get('peak', {}).get('avg', 0) * 100:+.1f}% —— "
              "**替代卖点拿到的是一段，不是一整轮**。"]
        if w2:
            wb = bt.get("w2_bench") or {}
            L.append(f"- 若从首笔建仓日起算（W2）：一次性打满年化 "
                     f"{w2['full']['stats']['cagr'] * 100:.2f}% vs 基准 {wb.get('cagr', float('nan')) * 100:.2f}%"
                     f"，回撤 {w2['full']['stats']['dd'] * 100:.1f}% vs {wb.get('dd', float('nan')) * 100:.1f}% "
                     "—— **收益优势跨窗口不稳定，回撤优势稳定**。")
    else:
        L += ["- 尚未跑回测（`backtest.py`）。数字说话前不要照规则下单。"]
    L += ["",
          "1. **股息率是反推的已实现口径**（全收益 ÷ 价格的 250 日比值变化率），"
          f"与官方「当期股息率」差 {_dy_gap(d):+.2f}pp（{d['na']['dy'] * 100:.2f}% vs 官方快照）；"
          "4.5% 阈值只在这个口径下成立，不能直接与指数公司公布的股息率比。",
          "2. **本读数不接每日流程**：`run_daily_dividend.ps1` 与 `pipeline/strategies.json` 仍指向 V4。",
          "3. **持仓是规则推演**，不是账户实际情况；手动干预过的话以账户为准。",
          "",
          "> 仅为个人研究记录，不构成投资建议；市场有风险，投资需谨慎。", ""]
    return "\n".join(L)


def payload(d: dict) -> dict:
    bt = d.get("bt") or {}
    return {
        "strategy": "dividend/v10_resonance",
        "as_of": d["as_of"], "generated": d["generated"],
        "version": rule.TITLE,
        "positioning": "状态机择时：仓位 0–100%，信号稀少、多数时间空仓（不是主仓策略）",
        "state": d["state"], "state_cn": d["state_cn"],
        "primary": {"index": d["primary"], "name": rule.BY_INDEX[d["primary"]]["name"],
                    "close": d["na"]["close"], "peak": d["na"]["peak"],
                    "dd": d["na"]["dd"], "dy": d["na"]["dy"],
                    "dy_official": next((r["dy_official"] for r in d["rows"]
                                         if r["index"] == d["primary"]), None),
                    "below_ma": d["na"]["below_ma"], "expo": d["na"]["expo"]},
        "conditions": d["conds"],
        "tranches": d["tranches"],
        "holding": d["holding"],
        "peers": d["rows"],
        "stale": d["stale"],
        "backtest": {
            "window": bt.get("window"), "days": bt.get("days"),
            "first_entry": bt.get("first_entry"),
            "trades": (bt.get("base") or {}).get("trades"),
            "win": (bt.get("base") or {}).get("win"),
            "avg_ret": (bt.get("base") or {}).get("avg_ret"),
            "base_calmar": ((bt.get("base") or {}).get("stats") or {}).get("calmar"),
            "full_calmar": ((bt.get("full") or {}).get("stats") or {}).get("calmar"),
            "bench_calmar": (bt.get("bench") or {}).get("calmar"),
            "summary": "strategies/dividend/v10_resonance/backtest/_v10_bt_summary.json",
            "report": "strategies/dividend/v10_resonance/backtest/_v10_report.md",
        },
    }


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="V10 三重共振今日读数")
    ap.add_argument("--refresh", action="store_true")
    args = ap.parse_args(argv)

    d = collect(refresh=args.refresh)
    os.makedirs(rule.DAILY_DIR, exist_ok=True)
    with open(rule.OUT_MD, "w", encoding="utf-8") as fh:
        fh.write(render(d))
    with open(rule.OUT_JSON, "w", encoding="utf-8") as fh:
        json.dump(payload(d), fh, ensure_ascii=False, indent=1)

    na = d["na"]
    print(f"[v10] 数据截至 {d['as_of']} · 状态 {d['state']} · 仓位 {na['expo']:.0%}")
    print(f"[v10] 收盘 {na['close']:.2f} · 回撤 {na['dd'] * 100:.1f}% · 股息率 {na['dy'] * 100:.2f}% · "
          f"{'破' if na['below_ma'] else '在'}均线{'下' if na['below_ma'] else '上'}")
    print(f"[v10] 三条件：" + " ".join(f"{c['key']}={'✅' if c['ok'] else '❌'}" for c in d["conds"]))
    print(f"[v10] 产物 -> {os.path.relpath(rule.OUT_MD, rule.ROOT)} · "
          f"{os.path.relpath(rule.OUT_JSON, rule.ROOT)}")
    return 0


if __name__ == "__main__":
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    sys.exit(main())
