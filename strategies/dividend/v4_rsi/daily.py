#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""V4 · 今日信号（RSI14 择时 + 横截面 RSI 最低 2 只）

规则（`rule.py` 是参数权威源）
----------------------------------------------------------------------
* 标的池：**全池 10 只**（`../pool.py::TIMING_POOL_ALL`，2026-11-20 起；此前为 5 只筛池）。
  池只是**候选广度**，每期仍只持 RSI 最低的 2 只，其余权重 0 —— 报告与看板都逐只标注
  「该标的最优策略 = RSI 择时 / 买入持有」（`fit.py`）
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
import fit  # noqa: E402  pylint: disable=wrong-import-position
import pool  # noqa: E402  pylint: disable=wrong-import-position
import rule as V4  # noqa: E402  pylint: disable=wrong-import-position

OUT_MD = os.path.join(_DIR, "daily", "_v4_signal.md")
OUT_JSON = os.path.join(_DIR, "daily", "_v4_signal.json")

# 池 ≠ 持仓：这条必须在报告与看板里写死，否则「池 10 只」会被读成「持 10 只」。
POOL_NOTE = (f"池 {len(V4.CODES)} 只是**候选广度**；每月只在池内取 RSI 最低的 "
             f"{V4.TOP_K} 只等权，**其余 {len(V4.CODES) - V4.TOP_K} 只权重为 0**。")


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

    fit_rows = fit.fit_all(navs=navs)        # 逐只「RSI 择时 vs 买入持有」，与 universe/回测同源
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
        # 逐只「RSI 择时 vs 买入持有」谁更优（fit.py 唯一实现，与 universe/回测同源）
        "fit_rows": fit_rows,
        "fit": {r["code"]: {"best": r["best"], "dcalmar": r["dcalmar"],
                            "calmar_rsi": r["calmar_rsi"], "calmar_hold": r["calmar_hold"],
                            "cagr_rsi": r["cagr_rsi"], "cagr_hold": r["cagr_hold"],
                            "dd_rsi": r["dd_rsi"], "dd_hold": r["dd_hold"]}
                for r in fit_rows},
    }


def _best_txt(d: dict, code: str) -> str:
    """逐只最优策略的短标签（报告/看板/每日看板共用）。"""
    f = (d.get("fit") or {}).get(code)
    if not f:
        return "—"
    return fit.best_label(f["dcalmar"])


def _fmt_w(row: pd.Series) -> list[tuple[str, str, float]]:
    return [(c, V4.NAME.get(c, c), float(v)) for c, v in row.sort_values(ascending=False).items()
            if v > 1e-9]


def _month_last_bday(ts) -> pd.Timestamp:
    """某个自然月最后一个交易日(月末自然日往前退到工作日)。"""
    nxt = pd.Timestamp(ts).to_period("M").to_timestamp() + pd.offsets.MonthBegin(1)
    return nxt - pd.offsets.BDay(1)


def _next_decision(d: dict) -> pd.Timestamp:
    """下一次决策日 = 本月最后一个交易日; 本月信号已确认(数据已过月末)则顺延到下月。

    与「当前处于哪个月」无关 —— 月初刚确认完上月信号时, 下一个决策点是本月末,
    否则报告会在这段时间里没有决策日可写。
    """
    today = pd.Timestamp(d["today"])
    me = _month_last_bday(today)
    if pd.Timestamp(d["data_asof"]) >= me:          # 本月信号已确认 → 顺延下月
        me = _month_last_bday(me + pd.Timedelta(days=1))
    return me


def _preview_w(d: dict) -> tuple[pd.Series, str]:
    """「预计下次决策」用的预览权重 + 口径说明。

    月中 → 直接读未走完那一行(`prov_w`, 与回测同口径);
    月初刚确认完上月信号、还没有新月度数据时 → 按最新日度 RSI 重算同一套规则
    (板块均值定档 × RSI 最低 TOP_K 只等权), 只是没等到本月行情。
    """
    if d["prov_w"] is not None:
        return d["prov_w"], "按未走完的本月数据"
    hyp = pd.Series({c: 0.0 for c in V4.CODES})
    if d["picks_now"] and d["mult_now"] > 0:
        for c in d["picks_now"]:
            hyp[c] = d["mult_now"] / len(d["picks_now"])
    return hyp, f"按 {d['data_asof']:%m-%d} 最新 RSI"


def render(d: dict) -> str:
    cur = _fmt_w(d["cur_w"])
    last_day = _next_decision(d)
    dec_txt = f"{last_day:%Y-%m-%d}"
    prev_w, prev_tag = _preview_w(d)
    L = [f"# V4 · 今日信号（{d['today']:%Y-%m-%d}）", "",
         f"> 数据截至 **{d['data_asof']:%Y-%m-%d}**（基金累计净值）。"
         f"池 {len(V4.CODES)} 只（{'/'.join(V4.CODES)}）。"
         f"参数：RSI14 · 板块均值 ≥{int(V4.Q_OUT * 100)} 清仓 / ≥{int(V4.Q_HALF * 100)} 减半 · "
         f"横截面取 RSI 最低 {V4.TOP_K} 只等权。",
         f"> ⚠ {POOL_NOTE}",
         f"> 「最优策略」= 该标的单独跑同一套 RSI 规则与买入持有的对比（`fit.py`）；"
         f"标 **买入持有** 的标的被 RSI 减仓是负和，V4 买它只是因为横截面相对超卖。", "",
         "## 一、今天该持什么（当前生效信号）", "",
         f"**{d['cur_ts']:%Y-%m-%d} 收盘**算出的信号，整个 {d['cur_ts'] + pd.offsets.MonthBegin(1):%Y-%m} 持有：", "",
         f"- 板块 RSI 均值 **{d['cur_bq'] * 100:.1f}** → 仓位档位 **{_tier(d['cur_mult'])}**",
         ""]
    if cur:
        L += ["| 标的 | 名称 | 权重 | 该标的最优策略 |", "|---|---|---:|---|"]
        for code, name, v in cur:
            L.append(f"| {code} | {name} | {v * 100:.0f}% | {_best_txt(d, code)} |")
    else:
        L.append("| — | **空仓** | 0% | — |")
    if cur:
        L.append(f"\n其余 {len(V4.CODES) - len(cur)} 只本月权重 0（候选，不持有）。\n")

    L += ["## 二、逐标的 RSI 与预计下次决策（预览，不可执行）", "",
          f"日度 RSI14 快照（至 {d['data_asof']:%Y-%m-%d}）。**预计下次决策** = {prev_tag} 预演 "
          f"**{dec_txt} 收盘** 的判定（板块均值 {d['board_now']:.1f} → 档位 "
          f"{_tier(d['mult_now'])}，横截面取 RSI 最低 {min(V4.TOP_K, len(d['rsi_now']))} 只等权）"
          "；最终以月末收盘值为准。", "",
          f"| 标的 | 名称 | RSI14 | 当前权重 | 该标的最优策略 | 预计下次决策（{dec_txt} 收盘） |",
          "|---|---|---:|---:|---|---|"]
    for code, v in d["rsi_now"].sort_values().items():
        w_cur = float(d["cur_w"].get(code, 0.0))
        w_pre = float(prev_w.get(code, 0.0))
        dec = f"**入选 → {w_pre * 100:.0f}%**" if w_pre > 1e-9 else "落选 → 0%"
        L.append(f"| {code} | {V4.NAME.get(code, code)} | {v:.1f} | "
                 f"{w_cur * 100:.0f}% | {_best_txt(d, code)} | {dec} |")
    L += ["", f"- 板块均值 **{d['board_now']:.1f}** → 若今天收盘就是月末，档位为 "
              f"**{_tier(d['mult_now'])}**；当前生效档位 {_tier(d['cur_mult'])}。", ""]

    L += ["## 三、下一个决策点", "",
          f"下一个决策点：**{dec_txt} 收盘**确认，**次日开盘**执行。", ""]
    if d["prov_w"] is not None:
        prov = _fmt_w(d["prov_w"])
        same = all(abs(float(d["prov_w"].get(c, 0)) - float(d["cur_w"].get(c, 0))) < 1e-9
                   for c in V4.CODES)
        if same:
            L.append(f"- 用 {d['data_asof']:%m-%d} 数据预览：**持仓不变**，无需动作。")
        else:
            L.append(f"- 用 {d['data_asof']:%m-%d} 数据预览：**会变**，预览目标 —— "
                     + "、".join(f"{c}（{n}）{v * 100:.0f}%" for c, n, v in prov)
                     + "；最终以月末收盘值为准。")
        turn = float((d["prov_w"] - d["cur_w"]).abs().sum())
        L += [f"- 若按预览调仓，单边换手 {turn * 100:.0f}%，成本约 "
              f"{turn * S.FEE * 100:.3f}%。", ""]
    else:
        L += ["- 本月信号刚在上一月末确认，还没有新月度数据；上表「预计下次决策」是按"
              "最新日度 RSI 的预演，只作方向参考。", ""]

    L += ["## 四、操作结论", ""]
    if not cur:
        L.append(f"1. 今天**保持空仓**（{d['cur_ts']:%Y-%m} 信号：板块 RSI "
                 f"{d['cur_bq'] * 100:.1f} ≥ {int(V4.Q_OUT * 100)}）。")
    else:
        holds = "、".join(f"{n} {v * 100:.0f}%" for _, n, v in cur)
        L.append(f"1. 今天**持有 {holds}**，不调仓 —— V4 是月末策略，"
                 f"本月执行的是 {d['cur_ts']:%Y-%m-%d} 的信号。")
    L += [f"2. 下次动作在 {last_day:%Y-%m-%d} 收盘后；"
          "若届时板块 RSI 均值跨过 50/70，才需要改档位。",
          "3. 这条信号只控仓位，不预测方向：RSI 在强单边趋势里会钝化，"
          "连续数月在 50–70 之间意味着「长期半仓」，这是本策略的已知代价。",
          f"4. {POOL_NOTE} 不要按「池里有几只就买几只」执行。", ""]
    return "\n".join(L)


def payload(d: dict) -> dict:
    """给 pipeline 每日看板的结构化快照（`_v4_signal.json`）。"""
    last_day = _next_decision(d)
    prev_w, prev_tag = _preview_w(d)
    fit_ = d.get("fit") or {}
    cur = [{"code": c, "name": n, "weight": round(v, 4),
            "best": (fit_.get(c) or {}).get("best"),
            "dcalmar": round((fit_.get(c) or {}).get("dcalmar", 0.0), 4)}
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
        "next_decision": f"{last_day:%Y-%m-%d}",
        "preview_basis": prev_tag,
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
                  "weight": round(float(d["cur_w"].get(c, 0)), 4),
                  "rsi": None if d["rsi_now"].get(c) is None
                  else round(float(d["rsi_now"].get(c)), 1),
                  "preview_weight": round(float(prev_w.get(c, 0.0)), 4),
                  "best": (fit_.get(c) or {}).get("best"),
                  "best_txt": _best_txt(d, c),
                  "dcalmar": round((fit_.get(c) or {}).get("dcalmar", 0.0), 4),
                  "best_detail": None if not fit_.get(c) else {
                      "cagr_rsi": round(fit_[c]["cagr_rsi"], 4),
                      "dd_rsi": round(fit_[c]["dd_rsi"], 4),
                      "calmar_rsi": round(fit_[c]["calmar_rsi"], 4),
                      "cagr_hold": round(fit_[c]["cagr_hold"], 4),
                      "dd_hold": round(fit_[c]["dd_hold"], 4),
                      "calmar_hold": round(fit_[c]["calmar_hold"], 4)}}
                 for c in V4.CODES],
        # 「池 ≠ 持仓」：池 = 候选广度，实际只持 TOP_K 只，其余权重 0
        "pool_note": POOL_NOTE.replace("**", ""),
        "pool_size": len(V4.CODES),
        "hold_k": V4.TOP_K,
        "pool_mode": V4.POOL_MODE,
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
    fit.write_json(d["fit_rows"])        # 逐只最优策略快照（universe/看板/外部读取共用）
    os.makedirs(os.path.dirname(OUT_JSON), exist_ok=True)
    with open(OUT_JSON, "w", encoding="utf-8") as fh:
        json.dump(payload(d), fh, ensure_ascii=False, indent=2)

    cur = _fmt_w(d["cur_w"])
    print(f"[v4] 池 {len(V4.CODES)} 只 · 数据截至 {d['data_asof']:%Y-%m-%d} · "
          f"今天 {d['today']:%Y-%m-%d}")
    print(f"[v4] 当前生效：{d['cur_ts']:%Y-%m-%d} 信号 · 板块 RSI "
          f"{d['cur_bq'] * 100:.1f} → {_tier(d['cur_mult'])}")
    print("[v4] 持仓：" + ("、".join(f"{n} {v * 100:.0f}%" for _, n, v in cur) or "空仓"))
    tgt_pct = d["mult_now"] * 100 / len(d["picks_now"]) if d["picks_now"] else 0.0
    print(f"[v4] 预览：板块 RSI {d['board_now']:.1f} → {_tier(d['mult_now'])}；目标 "
          + ("、".join(f"{V4.NAME.get(c, c)} {tgt_pct:.0f}%" for c in d["picks_now"])
             if tgt_pct > 0 else "空仓"))
    print(f"[v4] 产物 -> {os.path.relpath(OUT_MD, _DIR)} · "
          f"{os.path.relpath(OUT_JSON, _DIR)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
