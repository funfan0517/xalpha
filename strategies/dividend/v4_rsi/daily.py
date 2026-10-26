#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""V4 · 今日信号（RSI14 择时 + 横截面 RSI 最低 2 只）

规则（`rule.py` 是参数权威源）
----------------------------------------------------------------------
* 标的池：**择时专用 5 只**（`../pool.py::TIMING_POOL`）—— 单标的 RSI 择时 ΔCalmar 双窗口都为正的那 5 只
* 时间维度：板块 RSI 均值 ≥ **70** → 清仓；≥ **50** → 减半；< 50 满仓
* 横截面：在可得标的里选 **RSI 最低的 2 只**等权
* 执行：**月末收盘**算信号，**下月首个交易日**调仓，单边成本 0.03%

这个脚本回答的是「今天该持什么、什么时候动手」，所以分三层输出：
1. **当前生效**：最近一个**已收盘**的月末信号（今天正处于它的持有期内）
2. **进行中读数**：本月至今的 RSI 快照 —— 只是预览，**不是**可执行信号
3. **下一个决策点**：本月最后一个交易日收盘后确认，次日开盘执行

产物（AGENTS §8.4 ⑤）：
* `v4_rsi/daily/_v4_signal.md`   —— 人读的操作说明
* `v4_rsi/daily/_v4_signal.json` —— pipeline 每日看板读取（`pipeline/daily_digest.py`）

用法::

    python strategies/dividend/v4_rsi/daily.py            # 用缓存（离线）
    python strategies/dividend/v4_rsi/daily.py --refresh  # 先抓最新净值
"""

from __future__ import annotations

import json
import os
import sys
from datetime import datetime

import pandas as pd

_DIR = os.path.dirname(os.path.abspath(__file__))
_STRAT = os.path.dirname(_DIR)
for _p in (_DIR, _STRAT):
    if _p not in sys.path:
        sys.path.insert(0, _p)

import _single_signal as S  # noqa: E402  pylint: disable=wrong-import-position
import pool  # noqa: E402  pylint: disable=wrong-import-position
import rule as V4  # noqa: E402  pylint: disable=wrong-import-position

OUT_MD = os.path.join(_DIR, "daily", "_v4_signal.md")
OUT_JSON = os.path.join(_DIR, "daily", "_v4_signal.json")


def _load(refresh: bool) -> pd.DataFrame:
    return pool.load_navs(codes=V4.CODES, refresh=refresh)


def _tier(mult: float) -> str:
    return {0.0: "清仓（0%）", 0.5: "减半（50%）", 1.0: "满仓（100%）"}[round(mult, 2)]


def build(refresh: bool = False) -> dict:
    navs = _load(refresh)
    rets = S.load_rets(navs)
    rsi_m = S.load_signal("rsi", navs).reindex(rets.index)
    q = rsi_m / 100.0
    ok = S.available(q, rets)

    bq = S.board_q(q, ok)                       # 板块 RSI 均值（0-100 的绝对刻度 → 除以 100）
    mult = S.time_mult(bq, V4.Q_OUT, V4.Q_HALF)
    w = S.top_w(rsi_m.where(ok), ok, V4.TOP_K).mul(mult, axis=0)

    today = pd.Timestamp(datetime.now().date())
    last = w.index[-1]
    partial = last > today                      # 当月未走完 → 最后一行为预览
    i_now = -2 if partial and len(w) >= 2 else -1

    # 日度最新 RSI（预览用的同一口径序列，只是不等到月末）
    rsi_now = pd.Series({c: float(S.rsi_series(navs[c].dropna()).iloc[-1])
                         for c in navs.columns})
    rsi_now = rsi_now.reindex(V4.CODES).dropna()
    board_now = float(rsi_now.mean())
    mult_now = 0.0 if board_now / 100 >= V4.Q_OUT else (
        0.5 if board_now / 100 >= V4.Q_HALF else 1.0)
    picks_now = list(rsi_now.nsmallest(min(V4.TOP_K, len(rsi_now))).index)

    return {
        "navs": navs, "rets": rets, "rsi_m": rsi_m, "bq": bq, "mult": mult, "w": w,
        "data_asof": navs.index[-1], "today": today, "partial": partial,
        "cur_ts": w.index[i_now], "cur_w": w.iloc[i_now],
        "cur_bq": float(bq.iloc[i_now]), "cur_mult": float(mult.iloc[i_now]),
        "prev_ts": w.index[i_now - 1] if len(w) + i_now >= 1 else None,
        "prev_w": w.iloc[i_now - 1] if len(w) + i_now >= 1 else None,
        "prov_ts": last if partial else None, "prov_w": w.iloc[-1] if partial else None,
        "rsi_now": rsi_now, "board_now": board_now, "mult_now": mult_now,
        "picks_now": picks_now,
    }


def _fmt_w(row: pd.Series) -> list[tuple[str, str, float]]:
    return [(c, V4.NAME.get(c, c), float(v)) for c, v in row.sort_values(ascending=False).items()
            if v > 1e-9]


def _next_decision(d: dict) -> pd.Timestamp | None:
    if d["prov_ts"] is None:
        return None
    return pd.Timestamp(d["prov_ts"]) + pd.offsets.MonthBegin(1) - pd.offsets.BDay(1)


def render(d: dict) -> str:
    cur = _fmt_w(d["cur_w"])
    L = [f"# V4 · 今日信号（{d['today']:%Y-%m-%d}）", "",
         f"> 数据截至 **{d['data_asof']:%Y-%m-%d}**（基金累计净值）。"
         f"池 {len(V4.CODES)} 只（{'/'.join(V4.CODES)}）。"
         f"参数：RSI14 · 板块均值 ≥{int(V4.Q_OUT * 100)} 清仓 / ≥{int(V4.Q_HALF * 100)} 减半 · "
         f"横截面取 RSI 最低 {V4.TOP_K} 只等权。", "",
         "## 一、今天该持什么（当前生效信号）", "",
         f"**{d['cur_ts']:%Y-%m-%d} 收盘**算出的信号，整个 {d['cur_ts'] + pd.offsets.MonthBegin(1):%Y-%m} 持有：", "",
         f"- 板块 RSI 均值 **{d['cur_bq'] * 100:.1f}** → 仓位档位 **{_tier(d['cur_mult'])}**",
         ""]
    if cur:
        L += ["| 标的 | 名称 | 权重 |", "|---|---|---:|"]
        for code, name, v in cur:
            L.append(f"| {code} | {name} | {v * 100:.0f}% |")
    else:
        L.append("| — | **空仓** | 0% |")
    L.append("")

    L += ["## 二、进行中读数（预览，不可执行）", "",
          f"本月至今（至 {d['data_asof']:%Y-%m-%d}）的日度 RSI 快照：", "",
          "| 标的 | 名称 | RSI14 | 备注 |", "|---|---|---:|---|"]
    for code, v in d["rsi_now"].sort_values().items():
        flag = "← 入选" if code in d["picks_now"] else ""
        L.append(f"| {code} | {V4.NAME.get(code, code)} | {v:.1f} | {flag} |")
    L += ["", f"- 板块均值 **{d['board_now']:.1f}** → 若今天收盘就是月末，档位为 "
              f"**{_tier(d['mult_now'])}**；当前生效档位 {_tier(d['cur_mult'])}。", ""]

    last_day = _next_decision(d)
    if d["prov_w"] is not None:
        prov = _fmt_w(d["prov_w"])
        same = all(abs(float(d["prov_w"].get(c, 0)) - float(d["cur_w"].get(c, 0))) < 1e-9
                   for c in V4.CODES)
        L += ["## 三、下一个决策点", "",
              f"本月信号在 **{last_day:%Y-%m-%d} 收盘**确认，**次日开盘**执行。", ""]
        if same:
            L.append(f"- 用 {d['data_asof']:%m-%d} 数据预览：**持仓不变**，无需动作。")
        else:
            L.append(f"- 用 {d['data_asof']:%m-%d} 数据预览：**会变**，预览目标 —— "
                     + "、".join(f"{c}（{n}）{v * 100:.0f}%" for c, n, v in prov)
                     + "；最终以月末收盘值为准。")
        turn = float((d["prov_w"] - d["cur_w"]).abs().sum())
        L += [f"- 若按预览调仓，单边换手 {turn * 100:.0f}%，成本约 "
              f"{turn * S.FEE * 100:.3f}%。", ""]

    L += ["## 四、操作结论", ""]
    if not cur:
        L.append(f"1. 今天**保持空仓**（{d['cur_ts']:%Y-%m} 信号：板块 RSI "
                 f"{d['cur_bq'] * 100:.1f} ≥ {int(V4.Q_OUT * 100)}）。")
    else:
        holds = "、".join(f"{n} {v * 100:.0f}%" for _, n, v in cur)
        L.append(f"1. 今天**持有 {holds}**，不调仓 —— V4 是月末策略，"
                 f"本月执行的是 {d['cur_ts']:%Y-%m-%d} 的信号。")
    L += [f"2. 下次动作在 {(last_day or d['today']):%Y-%m-%d} 收盘后；"
          "若届时板块 RSI 均值跨过 50/70，才需要改档位。",
          "3. 这条信号只控仓位，不预测方向：RSI 在强单边趋势里会钝化，"
          "连续数月在 50–70 之间意味着「长期半仓」，这是本策略的已知代价。", ""]
    return "\n".join(L)


def payload(d: dict) -> dict:
    """给 pipeline 每日看板的结构化快照（`_v4_signal.json`）。"""
    last_day = _next_decision(d)
    cur = [{"code": c, "name": n, "weight": round(v, 4)}
           for c, n, v in _fmt_w(d["cur_w"])]
    prov = [{"code": c, "name": n, "weight": round(v, 4)}
            for c, n, v in _fmt_w(d["prov_w"])] if d["prov_w"] is not None else []
    return {
        "strategy": "dividend/v4_rsi",
        "as_of": f"{d['data_asof']:%Y-%m-%d}",
        "today": f"{d['today']:%Y-%m-%d}",
        "signal_date": f"{d['cur_ts']:%Y-%m-%d}",
        "hold_month": f"{d['cur_ts'] + pd.offsets.MonthBegin(1):%Y-%m}",
        "board_rsi": round(d["cur_bq"] * 100, 1),
        "tier": _tier(d["cur_mult"]),
        "mult": round(float(d["cur_mult"]), 2),
        "positions": cur,
        "next_decision": f"{last_day:%Y-%m-%d}" if last_day is not None else None,
        "preview": {
            "board_rsi": round(d["board_now"], 1),
            "tier": _tier(d["mult_now"]),
            "mult": round(float(d["mult_now"]), 2),
            "picks": [{"code": c, "name": V4.NAME.get(c, c), "rsi": round(float(v), 1)}
                      for c, v in d["rsi_now"].sort_values().items()],
            "target": prov,
            "changed": bool(prov) and any(
                abs(float(d["prov_w"].get(c, 0)) - float(d["cur_w"].get(c, 0))) > 1e-9
                for c in V4.CODES),
            "turnover": round(float((d["prov_w"] - d["cur_w"]).abs().sum()), 4)
            if d["prov_w"] is not None else 0.0,
        },
        "pool": [{"code": c, "name": V4.NAME.get(c, c),
                  "weight": round(float(d["cur_w"].get(c, 0)), 4)} for c in V4.CODES],
        "rule": {
            "window": V4.WINDOW,
            "q_out": V4.Q_OUT,
            "q_half": V4.Q_HALF,
            "top_k": V4.TOP_K,
            "fee": S.FEE,
        },
    }


def main() -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    d = build("--refresh" in sys.argv)
    S.write(OUT_MD, render(d))
    os.makedirs(os.path.dirname(OUT_JSON), exist_ok=True)
    with open(OUT_JSON, "w", encoding="utf-8") as fh:
        json.dump(payload(d), fh, ensure_ascii=False, indent=2)

    cur = _fmt_w(d["cur_w"])
    print(f"[v4] 池 {len(V4.CODES)} 只 · 数据截至 {d['data_asof']:%Y-%m-%d} · "
          f"今天 {d['today']:%Y-%m-%d}")
    print(f"[v4] 当前生效：{d['cur_ts']:%Y-%m-%d} 信号 · 板块 RSI "
          f"{d['cur_bq'] * 100:.1f} → {_tier(d['cur_mult'])}")
    print("[v4] 持仓：" + ("、".join(f"{n} {v * 100:.0f}%" for _, n, v in cur) or "空仓"))
    print(f"[v4] 预览：板块 RSI {d['board_now']:.1f} → {_tier(d['mult_now'])}；"
          f"目标 " + ("、".join(f"{V4.NAME.get(c, c)} {100 / len(d['picks_now']):.0f}%"
                                for c in d["picks_now"]) or "空仓"))
    print(f"[v4] 产物 -> {os.path.relpath(OUT_MD, _DIR)} · "
          f"{os.path.relpath(OUT_JSON, _DIR)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
