# -*- coding: utf-8 -*-
"""全球相对动量轮动(六类资产) · 调仓信号报告(每日可跑, 双周调仓提醒)。

口径与官方引擎 `backtest.run_strategy` **逐行同构**(差一处就会给出错误的换仓建议):
  - 决策点 = 宽表索引的 `range(MIN_HIST, n, REBAL)`, 即 140/150/160... 的**绝对索引**,
    不随后日新增数据漂移 —— 这是"今天算出来的建议"能与回测历史对齐的前提
  - 信号 = close[t]/close[t-120]-1 降序; 取前 HOLD_N 名中 close[t] >= MA20[t] 者等权
  - 名单集合不变则不动; 一只都不合格 -> 空仓现金
  - 卖出费率只作用于「持有 <=7 交易日」的标的, 本策略周期 10 交易日 -> 恒不触发

只读本地十年库(先跑 fetch.py 刷新), 本脚本不联网 —— 信号与回测共用同一份数据。
用法: python strategies/momentum_rotation/scan.py
输出: strategies/momentum_rotation/daily/_signal_report.md · _mom_signal.json
"""
import io
import json
import os
import sys
from datetime import datetime

import numpy as np
import pandas as pd

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8")

_DIR = os.path.dirname(os.path.abspath(__file__))
_ROOT = os.path.dirname(os.path.dirname(_DIR))
for _p in (_DIR, _ROOT):
    if _p not in sys.path:
        sys.path.insert(0, _p)

import rule  # noqa: E402

OUT_MD = os.path.join(_DIR, "daily", "_signal_report.md")
OUT_JSON = os.path.join(_DIR, "daily", "_mom_signal.json")
META = {a["proxy"]: a for a in rule.ASSETS}
_WD = "一二三四五六日"


def dstr(ts):
    return f"{ts.date()}（周{_WD[ts.weekday()]}）"


def name_of(code):
    return META[code]["name"] if code in META else code


def off_of(code):
    return META[code]["off_code"] if code in META else "—"


def held_txt(codes):
    return "、".join(f"{name_of(c)}`{c}`" for c in codes) if codes else "**空仓现金**"


def pick_at(df, ma_s, first, t):
    """逐行同构于 backtest.run_strategy 第 53~68 行 —— 索引 t 的动量表与入选名单。"""
    cutoff = df.index[t - rule.MIN_HIST]
    mom = {}
    for c in df.columns:
        if first[c] is None or first[c] > cutoff:       # 上市不满 MIN_HIST 交易日
            continue
        p0, p1 = df[c].iloc[t - rule.LOOKBACK], df[c].iloc[t]
        if p0 > 0 and not (np.isnan(p0) or np.isnan(p1)):
            mom[c] = p1 / p0 - 1.0
    pick = []
    for c in sorted(mom, key=mom.get, reverse=True):
        v = ma_s[c].iloc[t]
        if not np.isnan(v) and df[c].iloc[t] >= v:      # 站上 MA20
            pick.append(c)
            if len(pick) >= rule.HOLD_N:
                break
    return mom, pick


def main():
    df = rule.load_wide()
    if len(df) == 0:
        sys.exit("缺少十年库数据: 先跑 python strategies/momentum_rotation/fetch.py")
    dates, n = df.index, len(df)
    today = n - 1
    ma_s = df.rolling(rule.MA).mean()
    first = {c: df[c].first_valid_index() for c in df.columns}

    sched = list(range(rule.MIN_HIST, n, rule.REBAL))   # 绝对索引决策点
    if len(sched) < 2:
        sys.exit("历史不足以形成两次调仓决策")
    picks = {t: pick_at(df, ma_s, first, t)[1] for t in sched}
    last_t = sched[-1]
    is_rebal = last_t == today
    cur_t = sched[-2] if is_rebal else last_t
    cur, tgt = picks[cur_t], picks[last_t]
    next_in = last_t + rule.REBAL - today
    # 交易日 -> 日历日粗折(每周 5 个交易日); 无交易日历, 仅供参考, 遇节假日顺延
    next_est = dates[today] + pd.Timedelta(days=round(next_in * 7 / 5))
    mom_now, preview = pick_at(df, ma_s, first, today)

    sell = [c for c in cur if c not in tgt]
    buy = [c for c in tgt if c not in cur]
    keep = [c for c in tgt if c in cur]

    # ---- 决策序列(最近 6 次) ----
    hist = []
    for i, t in enumerate(sched[-6:], 1):
        prev = picks[sched[sched.index(t) - 1]] if sched.index(t) > 0 else []
        out = [c for c in prev if c not in picks[t]]
        inn = [c for c in picks[t] if c not in prev]
        hist.append(dict(date=str(dates[t].date()), picks=picks[t], out=out, in_=inn))

    L = ["# 全球相对动量轮动 · 六类资产 · 调仓信号报告", "",
         f"> 生成 {datetime.now().strftime('%Y-%m-%d %H:%M')} · 数据 "
         f"{dates.min().date()} ~ {dates.max().date()}（{n} 个交易日）· 口径 "
         f"{rule.LOOKBACK} 日动量前 {rule.HOLD_N} 名中 ≥MA{rule.MA} 者等权 · "
         f"{rule.REBAL} 交易日再平衡",
         "> 只读本地十年库 `data/_long_klines.json`（先跑 `fetch.py` 刷新）"
         "· 信号用**场内代理**日线, 执行一律走**场外**主仓 · 机械规则输出, 非投资建议。", ""]

    # ---- 一、今日结论 ----
    L += ["## 一、今日结论", ""]
    if is_rebal:
        L += [f"### {dstr(dates[today])} **是调仓日** —— 收盘后执行", ""]
        if sell or buy:
            L += ["| 动作 | 资产 | 信号代理 | 场外执行 |", "|---|---|---|---|"]
            for c in sell:
                L.append(f"| **卖出** | {name_of(c)} | `{c}` | `{off_of(c)}` |")
            for c in buy:
                L.append(f"| **买入** | {name_of(c)} | `{c}` | `{off_of(c)}` |")
            for c in keep:
                L.append(f"| 保留 | {name_of(c)} | `{c}` | `{off_of(c)}` |")
        else:
            L.append(f"**名单未变** —— 继续持有 {held_txt(tgt)}, 无需交易。")
    else:
        L += [f"### {dstr(dates[today])} **不是调仓日 → 今日无需操作**", "",
              f"- **当前持仓**（{dstr(dates[cur_t])} 调仓确定）：{held_txt(cur)}",
              f"- 距上次调仓 **{today - cur_t} 个交易日**（{dates[cur_t].date()} → 今）",
              f"- 距下次调仓还需 **{next_in} 个交易日**"
              f"（≈ {next_est.date()}，按每周 5 交易日折算，遇节假日顺延）"]
        if set(preview) == set(cur):
            L.append(f"- 按今日收盘数据**预演**：目标与当前持仓**一致**，届时大概率也不动")
        else:
            L.append(f"- 按今日收盘数据**预演**：若今天就调，会换成 {held_txt(preview)}"
                     f"（换出 {held_txt([c for c in cur if c not in preview])}）"
                     " —— 中间几日排名仍可能变，以调仓日收盘为准")
    L.append("")

    # ---- 二、动量与均线榜 ----
    L += [f"## 二、动量与均线榜（{dates[today].date()} 收盘 · 今日数据）", "",
          "| 动量排名 | 资产 | 信号代理 | 场外执行 | 收盘 | 120 日动量 | MA20 | 站上 MA20 | 今日目标 |",
          "|---|---|---|---|---|---|---|---|---|"]
    for i, c in enumerate(sorted(mom_now, key=mom_now.get, reverse=True), 1):
        close = float(df[c].iloc[today])
        ma = ma_s[c].iloc[today]
        above = (not np.isnan(ma)) and close >= ma
        L.append(f"| {i} | {name_of(c)} | `{c}` | `{off_of(c)}` | {close:.4f} "
                 f"| {mom_now[c] * 100:+.2f}% | {ma:.4f} | {'是' if above else '**否**'} "
                 f"| {'✅ 入选' if c in preview else '—'} |")
    for c in df.columns:
        if c not in mom_now:
            L.append(f"| — | {name_of(c)} | `{c}` | `{off_of(c)}` | — | — | — | — "
                     f"| 上市不满 {rule.MIN_HIST} 交易日 |")
    L.append("")
    L.append(f"> 今日目标持仓 = **{held_txt(preview)}**（按动量降序逐名检查 ≥MA20，取满 "
             f"{rule.HOLD_N} 只即止）。")

    # ---- 三、决策序列 ----
    L += ["", "## 三、最近 6 次调仓决策", "",
          "| 决策日 | 当日目标持仓 | 换出 | 换入 |", "|---|---|---|---|"]
    for h in hist:
        L.append(f"| {h['date']} | {held_txt(h['picks'])} | {held_txt(h['out']) if h['out'] else '—'} "
                 f"| {held_txt(h['in_']) if h['in_'] else '—'} |")
    L.append("")
    L.append(f"> 决策点 = 宽表第 140/150/160… 个交易日（绝对索引，全历史固定）。"
             f"本次决策点 `{dates[cur_t].date()}` → 目标 {held_txt(cur)}。")

    # ---- 四、执行提示 ----
    L += ["", "## 四、执行提示", "",
          f"- **费率**：卖出费只对「持有 ≤{rule.FEE_SHORT_DAYS} 交易日」的标的按 {rule.FEE_SHORT:.1%} 计"
          f"（惩罚性赎回费）；本策略周期 {rule.REBAL} 交易日 > {rule.FEE_SHORT_DAYS} → **本次换仓成本 ≈ 0**",
          "- **时点**：场外申赎在 **T 日 15:00 前**提交，按当日净值确认；信号用当日**收盘**价 → 当日提交无前视",
          "- **QDII 滞后**：纳指100 主仓 `270042` 净值天然滞后约 1 个交易日，属正常"
          "（信号用场内代理 `513100`，不受影响）",
          f"- **口径**：信号池六只由唯一池 `data/_universe.md` 派生，不在此硬编码；"
          f"改标的只维护唯一池。参数权威源 = `rule.py`（REBAL={rule.REBAL} / HOLD_N={rule.HOLD_N}）",
          "- 若实际持仓与上表不同，请以实际为准，按「目标持仓」对齐即可。", "",
          "> 模拟结果，非投资建议。"]
    txt = "\n".join(L) + "\n"

    os.makedirs(os.path.dirname(OUT_MD), exist_ok=True)
    with open(OUT_MD, "w", encoding="utf-8") as fh:
        fh.write(txt)
    print(txt)

    json.dump({
        "generated_at": datetime.now().strftime("%Y-%m-%d %H:%M"),
        "as_of": str(dates[today].date()),
        "is_rebalance_day": bool(is_rebal),
        "last_decision": {"date": str(dates[cur_t].date()), "picks": cur},
        "next_rebalance_in_trading_days": int(next_in),
        "holdings": cur,
        "target_if_rebalance_today": preview,
        "actions": ([{"side": "sell", "proxy": c, "off": off_of(c), "name": name_of(c)}
                     for c in sell] +
                    [{"side": "buy", "proxy": c, "off": off_of(c), "name": name_of(c)}
                     for c in buy]),
        "last_decisions": hist,
        "snapshot": [{"rank": i, "name": name_of(c), "proxy": c, "off": off_of(c),
                      "close": float(df[c].iloc[today]),
                      "mom120": mom_now[c],
                      "ma20": (None if np.isnan(ma_s[c].iloc[today])
                               else float(ma_s[c].iloc[today]))}
                     for i, c in enumerate(sorted(mom_now, key=mom_now.get, reverse=True), 1)],
    }, open(OUT_JSON, "w", encoding="utf-8"), ensure_ascii=False, indent=2)


if __name__ == "__main__":
    main()
