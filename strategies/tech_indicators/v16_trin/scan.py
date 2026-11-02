# -*- coding: utf-8 -*-
"""TRIN 策略 · 每日扫描（实盘口径，与回测共用同一份 `rule.py`）。

回答一个问题：**今天全市场情绪到没到「恐慌放量」那一步，明天该不该买、买什么？**

输出四块：
  1. 情绪读数：TRIN、TRIN 5 日均、阈值距离、连续满足天数；
  2. 当前状态：持仓（第几日 / 到期日 / 止损价）或空仓；
  3. 明日动作：买入 510300 / 继续持有 / 空仓等待；
  4. 候选选股（池子口径）：错杀 + 放量温和 + 未破位的前 3 只，供「买什么」参考。

产物：`daily/_v16_signal.json`（机器读）+ `daily/_v16_signal.md`（人读）。
用法：
    python strategies/tech_indicators/v16_trin/scan.py            # 含广度/行情增量刷新
    python strategies/tech_indicators/v16_trin/scan.py --cache    # 只用本地库
"""
import argparse
import json
import os
import sys
from datetime import datetime

import numpy as np
import pandas as pd

try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:  # noqa
    pass

_DIR = os.path.dirname(os.path.abspath(__file__))
_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(_DIR)))
for _p in (_ROOT, os.path.join(_ROOT, "data"), os.path.dirname(_DIR), _DIR):
    if _p not in sys.path:
        sys.path.insert(0, _p)

import data as _data  # noqa: E402
import rule  # noqa: E402

OUT_JSON = os.path.join(_DIR, "daily", "_v16_signal.json")
OUT_MD = os.path.join(_DIR, "daily", "_v16_signal.md")


def _f(x, dp=3):
    return "—" if x is None or (isinstance(x, float) and not np.isfinite(x)) else f"{x:.{dp}f}"


def _num(x, dp=4):
    """NaN/inf -> None（JSON 不接受 NaN）。"""
    v = float(x)
    return round(v, dp) if np.isfinite(v) else None


def state_of(veh, buy):
    """由引擎重算当前持仓状态（与回测同一路径，避免两套口径）。"""
    nav, pos, trades = rule.run_single(veh["open"].to_numpy(float), veh["close"].to_numpy(float),
                                       buy, hold=rule.HOLD_DAYS, stop=rule.STOP, fee=rule.FEE)
    n = len(veh)
    holding = bool(pos[-1])
    detail = dict(holding=holding, exposure=round(float(np.mean(pos)), 3),
                  nav=round(float(nav[-1]), 4), trades=len(trades))
    if holding:
        t = trades[-1] if trades and trades[-1]["reason"] == "期末持仓" else None
        if t:
            ei = t["entry_i"]
            entry_px = float(veh["open"].iloc[ei]) * (1 + rule.FEE)
            hold_n = int(n - ei)
            detail.update(entry_date=str(veh.index[ei].date()), entry_px=round(entry_px, 3),
                          held_bars=hold_n, left_bars=int(rule.HOLD_DAYS - hold_n),
                          exit_date=str(veh.index[min(ei + rule.HOLD_DAYS - 1, n - 1)].date()),
                          stop_px=round(entry_px * (1 - rule.STOP), 3) if rule.STOP else None,
                          pnl=round(float(veh["close"].iloc[-1] / entry_px - 1), 4))
    return detail


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--cache", action="store_true", help="不联网，只用本地库")
    a = ap.parse_args()
    refresh = not a.cache

    breadth = _data.load_breadth(refresh=refresh)
    veh = _data.load_vehicle(rule.VEHICLE, refresh=refresh)
    idx = veh.index.intersection(breadth.index)
    br, veh = breadth.loc[idx].loc[rule.START:], veh.loc[idx].loc[rule.START:]
    end = _data.complete_end(br.index)
    br, veh = br.loc[:end], veh.loc[:end]
    if len(br) < 30:
        sys.exit("广度库/行情样本不足 30 天，先跑 data/_refresh_breadth.py --full")

    sig = rule.compute_signals(br)
    last = sig.index[-1]
    row = sig.iloc[-1]
    buy = sig["buy"].to_numpy(bool)
    st = state_of(veh, buy)

    hot, streak = bool(row["hot"]), int(row["streak"])
    need = int(rule.TRIN_CONFIRM) - streak if streak < rule.TRIN_CONFIRM else 0
    trin, ma = float(row["trin"]), float(row["trin_ma"])
    gap = ma - rule.TRIN_TH
    today_buy = bool(row["buy"])

    # 候选选股（池子口径，若明日真要买，买什么）
    cands = []
    try:
        panels = _data.load_pool(refresh=refresh)
        close, volume = panels["close"], panels["volume"]
        fac = rule.pool_factors(close, volume)
        j = close.index.get_indexer([last])[0]
        if j >= 0:
            codes, snap = rule.pick_codes(fac, j)
            for code in codes:
                px, ma250 = float(fac["_close"].iloc[j][code]), float(fac["ma250"].iloc[j][code])
                amt = float(fac["amt20"].iloc[j][code])
                cands.append(dict(code=code, name=_data.NAMES.get(code, ""),
                                  ret20=_num(fac["ret20"].iloc[j][code], 4),
                                  vr=_num(fac["vr"].iloc[j][code], 2),
                                  amt20_yi=_num(amt / 1e8 if np.isfinite(amt) else np.nan, 2),
                                  px_ma250=_num(px / ma250 - 1 if ma250 else np.nan, 3)))
            sel_note = f"合格 {snap.get('eligible', 0)} 只（错杀阈值 ret20 ≤ {snap.get('threshold', 0):+.1%}）"
        else:
            sel_note = "池子行情缺该交易日"
    except Exception as e:  # noqa: BLE001 - 选股失败不影响择时结论
        sel_note = f"选股不可用：{type(e).__name__}: {e}"

    if today_buy:
        action = f"买入 · 明日开盘买入 {rule.VEHICLE}（{_data.NAMES.get(rule.VEHICLE, '沪深300ETF')}），" \
                 f"持有 {rule.HOLD_DAYS} 个交易日后收盘卖出"
    elif st["holding"]:
        action = (f"持有 · 第 {st['held_bars']}/{rule.HOLD_DAYS} 日，"
                  f"到期日 {st['exit_date']}"
                  + (f"，止损价 {st['stop_px']}" if st.get("stop_px") else ""))
    elif hot:
        action = f"等待 · TRIN 5 日均已连续 {streak} 日 > {rule.TRIN_TH}，再连续 {need} 日触发"
    else:
        action = "空仓 · 未触发（不猜底，满足条件再买）"

    # 最近 5 次触发
    hist = []
    for t in np.where(buy)[0][-5:]:
        d = sig.index[t]
        p = int(veh.index.get_indexer([d])[0])
        nxt = veh.index[p + 1] if p >= 0 and p + 1 < len(veh) else None
        hist.append(dict(signal_date=str(d.date()), exec_date=str(nxt.date()) if nxt is not None else "—",
                         trin_ma=round(float(sig["trin_ma"].iloc[t]), 3)))

    payload = dict(
        generated_at=datetime.now().strftime("%Y-%m-%d %H:%M"),
        date=str(last.date()), label=rule.LABEL, params=rule.PARAMS,
        breadth=dict(adv=int(br["adv"].iloc[-1]), dec=int(br["dec"].iloc[-1]),
                     flat=int(br["flat"].iloc[-1]),
                     adv_amt_yi=round(float(br["adv_amt"].iloc[-1]) / 1e8, 1),
                     dec_amt_yi=round(float(br["dec_amt"].iloc[-1]) / 1e8, 1)),
        trin=round(trin, 3), trin_ma=round(ma, 3), threshold=rule.TRIN_TH,
        hot=hot, streak=streak, need_more=need, gap=round(gap, 3), triggered=today_buy,
        state=st, action=action, candidates=cands, candidate_note=sel_note,
        recent_triggers=hist,
    )
    os.makedirs(os.path.dirname(OUT_JSON), exist_ok=True)
    json.dump(payload, open(OUT_JSON, "w", encoding="utf-8"), ensure_ascii=False, indent=1)

    L = [
        f"# TRIN 市场广度择时 · 每日读数（{last.date()}）",
        "",
        f"> 参数 `{rule.LABEL}`：TRIN {rule.TRIN_WIN} 日均 > {rule.TRIN_TH} 连续 {rule.TRIN_CONFIRM} 日 → "
        f"次日开盘买入 {rule.VEHICLE}，持有 {rule.HOLD_DAYS} 日，止损 -{rule.STOP:.0%} · "
        f"生成于 {payload['generated_at']}",
        "",
        "## 一、今天的情绪读数",
        "",
        "| 项 | 值 |", "|---|---|",
        f"| 数据日 | {last.date()} |",
        f"| 涨 / 跌 / 平（家数） | {payload['breadth']['adv']} / {payload['breadth']['dec']} / "
        f"{payload['breadth']['flat']} |",
        f"| 涨 / 跌 成交额（亿） | {payload['breadth']['adv_amt_yi']} / {payload['breadth']['dec_amt_yi']} |",
        f"| TRIN（当日） | {trin:.3f} |",
        f"| TRIN {rule.TRIN_WIN} 日均 | **{ma:.3f}**（阈值 {rule.TRIN_TH}，{gap:+.3f}） |",
        f"| 是否处于高位 | {'是' if hot else '否'} · 连续 {streak} 日 |",
        f"| 今日是否触发 | {'**触发**' if today_buy else '否'} |",
        "",
        "## 二、当前状态与明日动作",
        "",
        f"- 状态：**{'持仓' if st['holding'] else '空仓'}**（全期平均持仓 {st['exposure']:.1%}，"
        f"累计 {st['trades']} 笔）",
        f"- 动作：**{action}**",
        "",
        "## 三、候选选股（池子口径 · 若明日真要买）",
        "",
        f"> 筛选：错杀（ret20 后 {rule.SELECT_Q:.0%} 分位）+ 放量温和（量比 "
        f"{rule.VOL_LO}~{rule.VOL_HI}）+ 未破位（收盘 ≥ MA250×{rule.BROKEN}）+ "
        f"20 日均额 ≥ {rule.AMT_MIN / 1e8:.1f} 亿 · {sel_note}",
        "",
    ]
    if cands:
        L += ["| 代码 | 名称 | 20日收益 | 量比 | 20日均额(亿) | 距MA250 |",
              "|---|---|---|---|---|---|"]
        for c in cands:
            L.append(f"| `{c['code']}` | {c['name']} | {c['ret20']:+.1%} | {c['vr']:.2f}× | "
                     f"{c['amt20_yi']:.2f} | {c['px_ma250']:+.1%} |")
    else:
        L.append("（无合格标的）")

    L += ["", "## 四、最近 5 次触发", "",
          "| 信号日 | 执行日 | 当时 TRIN5 |", "|---|---|---|"]
    for h in hist:
        L.append(f"| {h['signal_date']} | {h['exec_date']} | {h['trin_ma']:.3f} |")

    L += ["",
          "> 背景：TRIN =（上涨家数 ÷ 下跌家数）÷（上涨成交额 ÷ 下跌成交额）。"
          "它衡量全市场涨跌的**量与面是否背离**，不是个股买卖按钮；"
          "连续高位只说明「下跌端在放量」，是否见底还要看结构与量能。机械输出，**非投资建议**。",
          ]
    open(OUT_MD, "w", encoding="utf-8").write("\n".join(L) + "\n")

    print(f"[TRIN] {last.date()} · TRIN {trin:.3f} · 5日均 {ma:.3f}（阈值 {rule.TRIN_TH}，"
          f"连续 {streak} 日）· {'触发' if today_buy else '未触发'} · {action}")
    print(f"           -> {OUT_MD}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
