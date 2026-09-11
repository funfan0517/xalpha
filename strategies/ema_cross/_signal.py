# -*- coding: utf-8 -*-
"""双均线趋势(EMA12/26)每日信号扫描 —— 输出 A 类名单当前状态与次日操作建议。

与回测同一套 rule 引擎(收盘信号 → 次日开盘执行), 保证实盘口径一致:
- 期末状态 = 状态机(含阈值/趋势/锁仓过滤)推进到最新交易日的持仓结果;
- 今日信号 = 最新交易日收盘是否新产生金叉/死叉;
- 建议 = 依据手册 Step3: 金叉且空仓→次日开盘全仓买入; 死叉且持仓→次日开盘清仓;
          持仓遇金叉/空仓遇死叉均不动; 无信号维持; 距上次交易<5 交易日则锁仓忽略。
用法: python strategies/ema_cross/_signal.py [code1,code2,...]   # 缺省用 select 名单 A 类
输出: strategies/ema_cross/_signal_report.md + stdout
"""
import io
import json
import os
import sys
from datetime import datetime

import numpy as np

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8")

_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

import rule

_ACTIVE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "_universe_ema_cross_active.json")
_OUT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "_signal_report.md")


def _scan(code):
    """单标的扫描 -> dict 或 None(数据失败/样本不足)。"""
    df = rule.load_bars(code)
    if len(df) < 60:
        return None
    dates = df["date"]
    n = len(df)
    o = df["open"].to_numpy(dtype=float)
    c = df["close"].to_numpy(dtype=float)
    sig = rule.build_signals(df["close"])
    win = (dates >= np.datetime64(rule.SAMPLE_FROM)).to_numpy()
    base = int(np.argmax(win)) if win.any() else 0
    base = min(base, n - 1)
    _, pos_ratio, trades0, holding = rule.run_engine(
        o, c, sig["buy"], sig["sell"], begin=base)

    e12, e26 = float(sig["fast"][-1]), float(sig["slow"][-1])
    gap = e12 / e26 - 1 if e26 > 0 else 0.0
    last_date = dates.iloc[-1]

    # 最近一次实际交易动作日(信号日 = 动作日-1); 用于今日新信号的锁仓判断
    last_action = None
    hold_since = None
    if trades0:
        last_tr = trades0[-1]
        last_action = last_tr["entry_i"] if holding else last_tr["exit_i"]
        if holding:
            hold_since = dates.iloc[last_tr["entry_i"]].date()

    today_buy, today_sell = bool(sig["buy"][-1]), bool(sig["sell"][-1])
    locked = last_action is not None and (n - last_action) < rule.LOCK_BARS

    if today_buy and not holding and not locked:
        action = "**次日开盘全仓买入**"
    elif today_sell and holding and not locked:
        action = "**次日开盘全部清仓**"
    elif (today_buy or today_sell) and locked:
        action = f"今日新信号但锁仓期(<{rule.LOCK_BARS}日), 忽略"
    elif today_buy and holding:
        action = "已持仓, 金叉不追加, 维持"
    elif today_sell and not holding:
        action = "已空仓, 死叉无需操作, 维持"
    else:
        action = "无新信号, 维持不动"

    state = "持仓" if holding else "空仓"
    if holding:
        state_note = f"持有中(买点 {hold_since})"
    else:
        state_note = "暂无有效开仓信号" if not trades0 else "空仓观望"

    return dict(
        code=code, date=str(last_date.date()), close=round(float(c[-1]), 4),
        gap=round(gap, 4),
        trend="多头(EMA12>EMA26)" if gap > 0 else "空头(EMA12<EMA26)",
        state=state, state_note=state_note, pos_ratio=pos_ratio,
        signal=("金叉" if today_buy else "死叉" if today_sell else "—"),
        action=action)


def main():
    want = set(sys.argv[1].split(",")) if len(sys.argv) > 1 and sys.argv[1].strip() else None
    meta = {}
    if want:
        rows = [r for r in rule.POOL if r["code"] in want]
        for r in rows:
            meta[r["code"]] = r
        codes = [r["code"] for r in rows]
        src = f"参数 codes {len(codes)} 只"
    else:
        try:
            act = json.load(open(_ACTIVE, encoding="utf-8"))
            meta = {x["code"]: x for x in act["A"]}
            codes = list(meta.keys())
            src = f"select 名单 A 类 {len(codes)} 只 ({_ACTIVE})"
        except Exception:  # noqa: BLE001 - 名单缺失时降级为全池扫描
            meta = {r["code"]: r for r in rule.POOL}
            codes = list(meta.keys())
            src = "select 名单缺失, 降级全池扫描"

    res = []
    for code in codes:
        try:
            r = _scan(code)
            if r:
                r["theme"] = meta.get(code, {}).get("theme", code)
                res.append(r)
        except Exception as e:  # noqa: BLE001
            print(f"FAIL {code}: {type(e).__name__}: {e}")

    res.sort(key=lambda r: (r["state"] != "持仓", r["signal"] != "—", -abs(r["gap"])))
    now = datetime.now().strftime("%Y-%m-%d %H:%M")
    L = ["# 双均线趋势(EMA12/26)每日信号扫描", "",
         f"> 生成 {now} · 标的 {src} · 规则与回测同源(rule.py): "
         "金叉=昨EMA12≤EMA26且今EMA12>EMA26(需差距≥0.3%、金叉须收盘>EMA26); "
         "信号次日开盘执行; 交易间隔<5日锁仓。机械规则输出, 非投资建议。", ""]
    L.append("| 标的 | 最新收盘 | 距差(EMA12/26) | 排列 | 期末状态 | 今日信号 | 操作建议 |")
    L.append("|---|---|---|---|---|---|---|")
    for r in res:
        L.append(f"| {r['theme']} `{r['code']}` | {r['date']} {r['close']} "
                 f"| {r['gap'] * 100:+.2f}% | {r['trend']} | {r['state']}({r['state_note']}) "
                 f"| {r['signal']} | {r['action']} |")
    L.append("")
    L.append("> 依据手册: 均线向上顺势持仓、向下空仓规避; 无金叉/死叉不手动止盈止损加减仓; "
             "标的须为高流动性宽基/行业 ETF(禁用于低流动性小标的)。")

    txt = "\n".join(L) + "\n"
    with open(_OUT, "w", encoding="utf-8") as fh:
        fh.write(txt)
    print(txt)


if __name__ == "__main__":
    main()
