#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""V8 · 今日信号（RSI12 80/20 口径 + 横截面 RSI 最低 2 只）

规则（`rule.py` 是参数权威源）
----------------------------------------------------------------------
* 标的池：与 V4 同一个 5 只择时池（`../pool.py::TIMING_POOL`）
* 时间维度：板块 RSI12 均值 ≥ **80** → 清仓；≥ **50** → 减半；< 50 满仓
* 横截面：在可得标的里选 **RSI 最低的 2 只**等权
* 执行：**月末收盘**算信号，**下月首个交易日**调仓，单边成本 0.03%

⚠ V8 是 V4 的**参数对照版**，不接每日流程（`run_daily_dividend.ps1` 跑的是 V4）。
本脚本用于「想看 12 日 + 80 口径今天会给什么信号」时手动跑。

产物（AGENTS §8.4 ⑤）：
* `v8_rsi12/daily/_v8_signal.md`
* `v8_rsi12/daily/_v8_signal.json`

用法::

    python strategies/dividend/v8_rsi12/daily.py            # 用缓存（离线）
    python strategies/dividend/v8_rsi12/daily.py --refresh  # 先抓最新净值
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
import rule as V8  # noqa: E402  pylint: disable=wrong-import-position

OUT_MD = os.path.join(_DIR, "daily", "_v8_signal.md")
OUT_JSON = os.path.join(_DIR, "daily", "_v8_signal.json")


def _rsi_panel(navs: pd.DataFrame) -> pd.DataFrame:
    """V8 自己的 RSI 宽表（12 日）—— `S.load_signal` 固定 14 日，这里必须自己算。"""
    return pd.DataFrame({c: S.rsi_series(navs[c].dropna(), V8.WINDOW)
                         .resample("ME").last().dropna()
                         for c in navs.columns}).sort_index()


def _tier(mult: float) -> str:
    return {0.0: "清仓（0%）", 0.5: "减半（50%）", 1.0: "满仓（100%）"}[round(mult, 2)]


def build(refresh: bool = False) -> dict:
    navs = pool.load_navs(codes=V8.CODES, refresh=refresh)
    rets = S.load_rets(navs)
    rsi_m = _rsi_panel(navs).reindex(rets.index)
    q = rsi_m / 100.0
    ok = S.available(q, rets)

    bq = S.board_q(q, ok)
    mult = S.time_mult(bq, V8.Q_OUT, V8.Q_HALF)
    w = S.top_w(rsi_m.where(ok), ok, V8.TOP_K).mul(mult, axis=0)

    today = pd.Timestamp(datetime.now().date())
    last = w.index[-1]
    partial = last > today
    i_now = -2 if partial and len(w) >= 2 else -1

    rsi_now = pd.Series({c: float(S.rsi_series(navs[c].dropna(), V8.WINDOW).iloc[-1])
                         for c in navs.columns}).reindex(V8.CODES).dropna()
    board_now = float(rsi_now.mean())
    mult_now = 0.0 if board_now / 100 >= V8.Q_OUT else (
        0.5 if board_now / 100 >= V8.Q_HALF else 1.0)
    picks_now = list(rsi_now.nsmallest(min(V8.TOP_K, len(rsi_now))).index)

    return {
        "navs": navs, "rets": rets, "rsi_m": rsi_m, "bq": bq, "mult": mult, "w": w,
        "data_asof": navs.index[-1], "today": today, "partial": partial,
        "cur_ts": w.index[i_now], "cur_w": w.iloc[i_now],
        "cur_bq": float(bq.iloc[i_now]), "cur_mult": float(mult.iloc[i_now]),
        "prov_ts": last if partial else None, "prov_w": w.iloc[-1] if partial else None,
        "rsi_now": rsi_now, "board_now": board_now, "mult_now": mult_now,
        "picks_now": picks_now,
    }


def _fmt_w(row: pd.Series) -> list[tuple[str, str, float]]:
    return [(c, V8.NAME.get(c, c), float(v)) for c, v in row.sort_values(ascending=False).items()
            if v > 1e-9]


def _next_decision(d: dict) -> pd.Timestamp | None:
    if d["prov_ts"] is None:
        return None
    return pd.Timestamp(d["prov_ts"]) + pd.offsets.MonthBegin(1) - pd.offsets.BDay(1)


def render(d: dict) -> str:
    cur = _fmt_w(d["cur_w"])
    L = [f"# V8 · 今日信号（{d['today']:%Y-%m-%d}）", "",
         f"> 数据截至 **{d['data_asof']:%Y-%m-%d}**（基金累计净值）。池 {len(V8.CODES)} 只"
         f"（{'/'.join(V8.CODES)}，与 V4 同池）。"
         f"参数：RSI{V8.WINDOW} · 板块均值 ≥{int(V8.Q_OUT * 100)} 清仓 / "
         f"≥{int(V8.Q_HALF * 100)} 减半 · 横截面取 RSI 最低 {V8.TOP_K} 只等权。", "",
         "## 一、今天该持什么（当前生效信号）", "",
         f"**{d['cur_ts']:%Y-%m-%d} 收盘**算出的信号，整个 "
         f"{d['cur_ts'] + pd.offsets.MonthBegin(1):%Y-%m} 持有：", "",
         f"- 板块 RSI{V8.WINDOW} 均值 **{d['cur_bq'] * 100:.1f}** → 仓位档位 "
         f"**{_tier(d['cur_mult'])}**", ""]
    if cur:
        L += ["| 标的 | 名称 | 权重 |", "|---|---|---:|"]
        for code, name, v in cur:
            L.append(f"| {code} | {name} | {v * 100:.0f}% |")
    else:
        L.append("| — | **空仓** | 0% |")
    L.append("")

    L += ["## 二、进行中读数（预览，不可执行）", "",
          f"本月至今（至 {d['data_asof']:%Y-%m-%d}）的日度 RSI{V8.WINDOW} 快照：", "",
          "| 标的 | 名称 | RSI | 备注 |", "|---|---|---:|---|"]
    for code, v in d["rsi_now"].sort_values().items():
        flag = "← 入选" if code in d["picks_now"] else ""
        L.append(f"| {code} | {V8.NAME.get(code, code)} | {v:.1f} | {flag} |")
    L += ["", f"- 板块均值 **{d['board_now']:.1f}** → 若今天收盘就是月末，档位为 "
              f"**{_tier(d['mult_now'])}**；当前生效档位 {_tier(d['cur_mult'])}。", ""]

    last_day = _next_decision(d)
    if d["prov_w"] is not None:
        prov = _fmt_w(d["prov_w"])
        same = all(abs(float(d["prov_w"].get(c, 0)) - float(d["cur_w"].get(c, 0))) < 1e-9
                   for c in V8.CODES)
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
                 f"{d['cur_bq'] * 100:.1f} ≥ {int(V8.Q_OUT * 100)}）。")
    else:
        holds = "、".join(f"{n} {v * 100:.0f}%" for _, n, v in cur)
        L.append(f"1. 今天**持有 {holds}**，不调仓 —— V8 是月末策略，"
                 f"本月执行的是 {d['cur_ts']:%Y-%m-%d} 的信号。")
    L += [f"2. 下次动作在 {(last_day or d['today']):%Y-%m-%d} 收盘后。",
          "3. **本信号不接每日流程**：实盘执行以 V4（14 日 + 70/50）为准，"
          "V8 只是参数对照 —— 12 日窗口更敏感，回测 Calmar 明显低于 V4，见 "
          "`backtest/_v8_report.md` 附一/附三。", ""]
    return "\n".join(L)


def payload(d: dict) -> dict:
    """结构化快照（`_v8_signal.json`）—— 与 V4 同结构，便于并排比较。"""
    last_day = _next_decision(d)
    return {
        "strategy": "dividend/v8_rsi12",
        "as_of": f"{d['data_asof']:%Y-%m-%d}",
        "today": f"{d['today']:%Y-%m-%d}",
        "signal_date": f"{d['cur_ts']:%Y-%m-%d}",
        "hold_month": f"{d['cur_ts'] + pd.offsets.MonthBegin(1):%Y-%m}",
        "board_rsi": round(d["cur_bq"] * 100, 1),
        "tier": _tier(d["cur_mult"]),
        "mult": round(float(d["cur_mult"]), 2),
        "positions": [{"code": c, "name": n, "weight": round(v, 4)}
                      for c, n, v in _fmt_w(d["cur_w"])],
        "next_decision": f"{last_day:%Y-%m-%d}" if last_day is not None else None,
        "preview": {
            "board_rsi": round(d["board_now"], 1),
            "tier": _tier(d["mult_now"]),
            "picks": [{"code": c, "name": V8.NAME.get(c, c), "rsi": round(float(v), 1)}
                      for c, v in d["rsi_now"].sort_values().items()],
            "target": [{"code": c, "name": n, "weight": round(v, 4)}
                       for c, n, v in _fmt_w(d["prov_w"])] if d["prov_w"] is not None else [],
            "changed": bool(d["prov_w"] is not None) and any(
                abs(float(d["prov_w"].get(c, 0)) - float(d["cur_w"].get(c, 0))) > 1e-9
                for c in V8.CODES),
            "turnover": round(float((d["prov_w"] - d["cur_w"]).abs().sum()), 4)
            if d["prov_w"] is not None else 0.0,
        },
        "pool": [{"code": c, "name": V8.NAME.get(c, c),
                  "weight": round(float(d["cur_w"].get(c, 0)), 4)} for c in V8.CODES],
        "rule": {"window": V8.WINDOW, "q_out": V8.Q_OUT, "q_half": V8.Q_HALF,
                 "top_k": V8.TOP_K, "fee": S.FEE},
        "note": "V8 是 V4 的参数对照（12 日 + 80/50），不进每日流程",
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
    print(f"[v8] 池 {len(V8.CODES)} 只 · RSI{V8.WINDOW} · 数据截至 {d['data_asof']:%Y-%m-%d}")
    print(f"[v8] 当前生效：{d['cur_ts']:%Y-%m-%d} 信号 · 板块 RSI "
          f"{d['cur_bq'] * 100:.1f} → {_tier(d['cur_mult'])}")
    print("[v8] 持仓：" + ("、".join(f"{n} {v * 100:.0f}%" for _, n, v in cur) or "空仓"))
    print(f"[v8] 预览：板块 RSI {d['board_now']:.1f} → {_tier(d['mult_now'])}")
    print(f"[v8] 产物 -> {os.path.relpath(OUT_MD, _DIR)} · "
          f"{os.path.relpath(OUT_JSON, _DIR)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
