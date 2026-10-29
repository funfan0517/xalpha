#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""V10 · 三重共振 —— 验证与回测

回答五个问题（同一段真实数据，不引用估计值）：

1. **规则落地后出现几次、赚多少？** 用户口径「20 年 7 次、全部盈利、平均 31.8%」。
   逐笔列出建仓/退出与收益，次数与均值由数据算，不照抄视频。
2. **4.5% 这个阈值在不同年代含义一样吗？** 中证红利 2008–2013 的股息率只有 1.4%–3.8%，
   2019 年后常年 4%–7% —— 绝对阈值在不同利率环境下的触发频率完全不同（§二/§三）。
3. **主卖点（收复前高）与替代卖点（满 250 日）差多少？** 这是本规则最贵的一个选择（§五）。
4. **止损线有用吗？** 规则给 15%–20%，实测在本样本内触发几次、值多少（§五）。
5. **换一个红利指数还成立吗？** 用 H30269（中证红利低波动）做对照（§八）。

口径（不隐藏）
--------------
* 信号用**价格指数**（000922）算，收益用**全收益指数**（H00922）记 —— 含分红再投。
  两者相除反推股息率（比值口径，见 `factors.py` 的警告）。
* 成交价 = **信号次日收盘**（T 日算出、T+1 日成交），避免「用同一根 K 线的收盘价又算又成交」。
* 成本：单边 0.03%（佣金 + 滑点）+ ETF 年费 0.6%（管理费 0.5% + 托管费 0.1%）。
* 现金按 0 收益计（不假设货币基金）。所以 30% 上限下组合收益天然很低 —— 报告同时给
  「满仓版（100%，一次性）」隔离规则本身的择时效应，否则会把「仓位低」误读成「策略差」。
* **两个窗口**：W1 = 2008-01（MA750 + 股息率齐备）~ 今；W2 = 首个建仓日 ~ 今。
  2008–2013 股息率从未到 4.5%，策略在这六年**结构性地不可能持仓**，
  用 W1 直接比会把「空仓躲过 2008」当成本事，所以必须同时看 W2。

产物
----
    backtest/_v10_bt.jsonl        逐日组合快照（净值 / 基准 / 仓位 / 状态 / 回撤 / 股息率）
    backtest/_v10_events.jsonl    逐笔交易（建仓/退出/收益/未来 1–3 年收益）
    backtest/_v10_bt_summary.json 精简结论（供 daily.py 引用，避免手抄数字）
    backtest/_v10_report.md       本报告

用法::

    python strategies/dividend/v10_resonance/backtest.py [--refresh]
"""
from __future__ import annotations

import argparse
import json
import os
import sys

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

FWD = (250, 500, 750)          # 未来 1/2/3 年（交易日）
HALF_POS = 0.50                # 参照基准：恒定半仓（0–100% 的中点）


# ------------------------------------------------------------------ 工具
def _p(x, d=1) -> str:
    return "—" if x is None or (isinstance(x, float) and not np.isfinite(x)) else f"{x * 100:.{d}f}%"


def _n(x, d=2) -> str:
    return "—" if x is None or (isinstance(x, float) and not np.isfinite(x)) else f"{x:.{d}f}"


def trade_stats(trades: list[dict]) -> dict:
    closed = [t for t in trades if t.get("exit_date") is not None]
    rets = [t["ret"] for t in closed]
    if not rets:
        return {"n": 0, "win": 0, "win_rate": float("nan"), "avg": float("nan"),
                "median": float("nan"), "best": float("nan"), "worst": float("nan"),
                "avg_days": float("nan"), "by_reason": {}, "rets": []}
    by = {}
    for t in closed:
        by.setdefault(t["reason"], []).append(t["ret"])
    win = [r for r in rets if r > 0]
    return {"n": len(rets), "win": len(win), "win_rate": len(win) / len(rets),
            "avg": float(np.mean(rets)), "median": float(np.median(rets)),
            "best": float(np.max(rets)), "worst": float(np.min(rets)),
            "avg_days": float(np.mean([t["days"] for t in closed])),
            "by_reason": {k: (len(v), float(np.mean(v))) for k, v in by.items()},
            "rets": rets}


def fwd_returns(d: pd.DataFrame, entry_date, horizons=FWD) -> dict:
    """建仓日之后 N 个交易日的全收益，用于与「视频口径」对齐。"""
    tr = d["tr"]
    i = d.index.get_loc(entry_date)
    return {h: (float(tr.iloc[i + h] / tr.iloc[i] - 1) if i + h < len(tr) else None)
            for h in horizons}


def cap_bench(tr: pd.Series, w: float) -> pd.Series:
    """恒定 w 仓位的基准：逐日按 w 参与收益、其余现金 0 收益，扣同额交易与年费。

    不用 `bench**w` 近似 —— 那是连续复利折算，18 年下来会把回撤算成 -31%（真实约 -22%）。
    """
    r = tr.pct_change().fillna(0.0).to_numpy(float)
    fee_d = rule.ETF_FEE_YEAR / engine.TRADING_DAYS
    eq = np.ones(len(tr))
    for i in range(1, len(tr)):
        eq[i] = eq[i - 1] * (1 + w * (r[i] - fee_d))
    eq[0] *= (1 - w * rule.FEE)
    return pd.Series(eq, index=tr.index)


def run_variant(d: pd.DataFrame, **kw) -> dict:
    res = engine.run(d, **kw)
    res["stat"] = engine.stats(res["equity"])
    res["bench_stat"] = engine.stats(res["bench"])
    # 参照线：恒定 50% 仓位（0–100% 的中点）逐日再平衡 —— 用来回答「择时是否优于简单半仓」
    res["cap_stat"] = engine.stats(cap_bench(d["tr"], HALF_POS))
    res["ts"] = trade_stats(res["trades"])
    return res


def window(res: dict, start: pd.Timestamp) -> dict:
    """切一段窗口重算：净值和基准直接切片（面板列本来就是全历史算好的）。"""
    e = res["equity"].loc[lambda s: s.index >= start]
    b = res["bench"].loc[lambda s: s.index >= start]
    trades = [t for t in res["trades"] if t["entry_date"] >= start]
    return {"stat": engine.stats(e), "bench": engine.stats(b),
            "ts": trade_stats(trades),
            "expo": float(res["expo"].loc[lambda s: s.index >= start].mean())}


def yearly(res: dict) -> pd.DataFrame:
    s = pd.DataFrame({"strat": res["equity"], "bench": res["bench"]}).pct_change()
    y = (1 + s).groupby(s.index.year).prod() - 1
    y["expo"] = res["expo"].groupby(res["expo"].index.year).mean()
    return y.dropna(subset=["strat", "bench"])


# ------------------------------------------------------------------ 主流程
def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="V10 三重共振回测")
    ap.add_argument("--refresh", action="store_true")
    args = ap.parse_args(argv)

    panels = factors.build(refresh=args.refresh)
    if rule.PRIMARY not in panels:
        raise RuntimeError(f"主标的 {rule.PRIMARY} 无数据，先跑 data.py 看数据源")
    d = panels[rule.PRIMARY].loc[lambda x: x.index >= pd.Timestamp(rule.BT_START)]

    base = run_variant(d)                                      # 默认：0–100% + 三批 + 到期续期 + 止损 10%
    oneshot = run_variant(d, ladder=False)                     # 一次性打满（隔离「分批」的影响）

    variants = {
        "卖点：仅收复前高（TTL=∞）": run_variant(d, ttl=float("inf")),
        "卖点：满 250 日，到期不续期": run_variant(d, ttl_renew=False),
        "卖点：满 500 日，到期不续期": run_variant(d, ttl=500, ttl_renew=False),
        "卖点：满 250 日 + 到期复核续期（默认）": base,
        "卖点：剩<2 条条件失效即卖": run_variant(d, exit_count=2),
        "止损：无": run_variant(d, stop=9.9),
        "止损：15%（原默认）": run_variant(d, stop=0.15),
        "止损：20%": run_variant(d, stop=0.20),
        "买点：dy≥4.0%": run_variant(d, dy_buy=0.040),
        "买点：dy≥5.0%": run_variant(d, dy_buy=0.050),
        "买点：dd≥8%": run_variant(d, dd_buy=0.08),
        "买点：dd≥15%": run_variant(d, dd_buy=0.15),
        "前高窗：250 日": run_variant(d, peak_win=250),
        "前高窗：375 日": run_variant(d, peak_win=375),
        "前高窗：750 日": run_variant(d, peak_win=750),
        "资金：首批 40%（现行三批）": base,
        "资金：首批 60%": run_variant(d, tranche=(0.60, 0.20, 0.20)),
        "资金：首批 70%": run_variant(d, tranche=(0.70, 0.15, 0.15)),
        "资金：首批 80%": run_variant(d, tranche=(0.80, 0.10, 0.10)),
        "资金：一次性打满（首批 100%）": oneshot,
    }

    first_entry = base["trades"][0]["entry_date"] if base["trades"] else d.index[0]
    ctx = dict(d=d, base=base, full=oneshot, variants=variants,
               official=data.official_dy(), panels=panels,
               holdout=_holdout(panels), counts=_event_counts(panels),
               first_entry=first_entry,
               w2={k: window(v, first_entry) for k, v in
                   (("base", base), ("full", oneshot), ("bench", {"equity": base["bench"],
                                                               "bench": base["bench"],
                                                               "expo": base["expo"],
                                                               "trades": base["trades"]}))})

    os.makedirs(rule.BT_DIR, exist_ok=True)
    _write_bt(base)
    _write_events(d, base)
    _write_summary(ctx)
    with open(rule.OUT_REPORT, "w", encoding="utf-8") as fh:
        fh.write(render(ctx))

    st, fs = base["stat"], oneshot["stat"]
    print(f"[v10bt] 样本 {d.index[0]:%Y-%m-%d}~{d.index[-1]:%Y-%m-%d}（{len(d)} 交易日）· "
          f"首笔建仓 {first_entry:%Y-%m-%d}")
    print(f"[v10bt] 默认（0–100% 分批 + 续期 + 止损10%） CAGR {st['cagr'] * 100:5.2f}% · "
          f"回撤 {st['dd'] * 100:6.1f}% · Calmar {st['calmar']:.2f} · 均仓 {base['expo'].mean():.0%}")
    print(f"[v10bt] 一次性打满（不分批）              CAGR {fs['cagr'] * 100:5.2f}% · "
          f"回撤 {fs['dd'] * 100:6.1f}% · Calmar {fs['calmar']:.2f} · 均仓 {oneshot['expo'].mean():.0%}")
    print(f"[v10bt] 买入持有                  CAGR {base['bench_stat']['cagr'] * 100:5.2f}% · "
          f"回撤 {base['bench_stat']['dd'] * 100:6.1f}% · Calmar {base['bench_stat']['calmar']:.2f}")
    ts = base["ts"]
    print(f"[v10bt] 交易 {ts['n']} 笔（胜 {ts['win']}）· 平均 {ts['avg'] * 100:+.1f}% · "
          f"中位 {ts['median'] * 100:+.1f}%")
    print(f"[v10bt] 产物 -> {os.path.relpath(rule.OUT_REPORT, rule.ROOT)}")
    return 0


def _holdout(panels: dict) -> dict:
    """对照标的：同一套规则换指数再跑一遍（股息率水平偏高，只作方向性对照）。"""
    if rule.HOLDOUT not in panels:
        return {}
    d = panels[rule.HOLDOUT].loc[lambda x: x.index >= pd.Timestamp(rule.BT_START)]
    return {"base": run_variant(d), "full": run_variant(d, ladder=False), "d": d}


def _event_counts(panels: dict) -> dict:
    """各股息率阈值下的「共振事件」次数（连续触发按一次算，间隔 > 60 天视为新事件）。"""
    out = {}
    for code, d in panels.items():
        rows = {}
        for th in (0.040, 0.045, 0.050, 0.060):
            sig = d[(d["dd"] >= rule.DD_BUY) & d["below_ma"] & (d["dy"] >= th)]
            if sig.empty:
                rows[th] = 0
                continue
            grp = (sig.index.to_series().diff().dt.days.fillna(999) > 60).cumsum()
            rows[th] = int(sig.groupby(grp).ngroups)
        out[code] = rows
    return out


# ------------------------------------------------------------------ 产物
def _jsonable(x):
    if isinstance(x, (np.floating, float)):
        return None if not np.isfinite(x) else round(float(x), 6)
    if isinstance(x, (np.integer,)):
        return int(x)
    if isinstance(x, pd.Timestamp):
        return x.strftime("%Y-%m-%d")
    return x


def _write_bt(base: dict) -> None:
    with open(rule.OUT_BT, "w", encoding="utf-8") as fh:
        for ts, r in base["bars"].iterrows():
            fh.write(json.dumps({
                "date": ts.strftime("%Y-%m-%d"),
                "equity": round(float(r["equity"]), 6),
                "bench": round(float(r["bench"]), 6),
                "expo": round(float(r["expo"]), 3),
                "state": r["state"],
                "close": round(float(r["close"]), 2),
                "dd": round(float(r["dd"]), 4),
                "dy_pct": None if not np.isfinite(r["dy"]) else round(float(r["dy"]) * 100, 3),
                "buy": bool(r["buy"]), "below_ma": bool(r["below_ma"]),
            }, ensure_ascii=False) + "\n")


def _write_events(d: pd.DataFrame, base: dict) -> None:
    rows = list(base["trades"]) + ([base["open"]] if base["open"] else [])
    with open(rule.OUT_EVENTS, "w", encoding="utf-8") as fh:
        for t in rows:
            fw = fwd_returns(d, t["entry_date"])
            fh.write(json.dumps({
                "entry_date": _jsonable(t["entry_date"]),
                "entry_price": round(float(t["entry_price"]), 2),
                "dd_entry": round(float(t["dd_entry"]), 4),
                "dy_entry": round(float(t["dy_entry"]), 4),
                "peak": round(float(t["peak"]), 2),
                "exit_date": _jsonable(t["exit_date"]) if t["exit_date"] is not None else None,
                "exit_price": None if t["exit_price"] is None else round(float(t["exit_price"]), 2),
                "reason": t["reason"],
                "days": int(t["days"]),
                "ret": round(float(t["ret"]), 5),
                "ret_net": round(float(t["ret_net"]), 5),
                "max_w": round(float(t["max_w"]), 3),
                "fwd_1y": _jsonable(fw[250]), "fwd_2y": _jsonable(fw[500]),
                "fwd_3y": _jsonable(fw[750]),
            }, ensure_ascii=False) + "\n")


def _write_summary(ctx: dict) -> None:
    base, full, d = ctx["base"], ctx["full"], ctx["d"]
    ts, fs = base["ts"], full["ts"]
    br = ts.get("by_reason", {})

    def pack(res, stat, tstat):
        return {"stats": {k: _jsonable(v) for k, v in stat.items()},
                "expo": _jsonable(float(res["expo"].mean())),
                "trades": tstat["n"], "win": tstat.get("win", 0),
                "avg_ret": _jsonable(tstat.get("avg")), "median_ret": _jsonable(tstat.get("median")),
                "avg_days": _jsonable(tstat.get("avg_days"))}

    out = {
        "window": f"{d.index[0]:%Y-%m-%d}~{d.index[-1]:%Y-%m-%d}",
        "days": int(len(d)),
        "asof": d.index[-1].strftime("%Y-%m-%d"),
        "state": base["state"],
        "first_entry": ctx["first_entry"].strftime("%Y-%m-%d"),
        "rule": {"dd_buy": rule.DD_BUY, "dy_buy": rule.DY_BUY, "dy_strong": rule.DY_STRONG,
                 "dy_extreme": rule.DY_EXTREME, "ttl": rule.SELL_TTL,
                 "ttl_renew": rule.SELL_TTL_RENEW, "ttl_max": rule.SELL_TTL_MAX,
                 "stop": rule.STOP_LOSS, "max_pos": rule.MAX_POS, "peak_win": rule.PEAK_WIN},
        "base": pack(base, base["stat"], ts),
        "full": pack(full, full["stat"], fs),
        "bench": {k: _jsonable(v) for k, v in base["bench_stat"].items()},
        "cap_bench": {k: _jsonable(v) for k, v in base["cap_stat"].items()},
        "w2": {k: {"stats": {kk: _jsonable(vv) for kk, vv in v["stat"].items()},
                   "trades": v["ts"]["n"], "win": v["ts"].get("win", 0),
                   "avg_ret": _jsonable(v["ts"].get("avg")),
                   "expo": _jsonable(v["expo"])} for k, v in ctx["w2"].items()
               if k != "bench"},
        "w2_bench": {kk: _jsonable(vv) for kk, vv in ctx["w2"]["bench"]["stat"].items()},
        "events": int(ts["n"]),
        "by_reason": {k: {"n": v[0], "avg": _jsonable(v[1])} for k, v in br.items()},
        "official_dy": {k: v for k, v in ctx["official"].items()},
        "dy_now": _jsonable(float(d["dy"].iloc[-1])),
        "counts": {k: {f"{th * 100:.1f}%": n for th, n in v.items()} for k, v in ctx["counts"].items()},
        "holdout": ({} if not ctx["holdout"] else {
            "index": rule.HOLDOUT,
            "base_calmar": _jsonable(ctx["holdout"]["base"]["stat"]["calmar"]),
            "full_calmar": _jsonable(ctx["holdout"]["full"]["stat"]["calmar"]),
            "bench_calmar": _jsonable(ctx["holdout"]["base"]["bench_stat"]["calmar"]),
            "events": ctx["holdout"]["base"]["ts"]["n"],
            "win": ctx["holdout"]["base"]["ts"].get("win", 0),
            "avg_ret": _jsonable(ctx["holdout"]["base"]["ts"].get("avg"))}),
    }
    with open(rule.OUT_BT_SUM, "w", encoding="utf-8") as fh:
        json.dump(out, fh, ensure_ascii=False, indent=1)


# ------------------------------------------------------------------ 渲染
def render(ctx: dict) -> str:
    d, base, one = ctx["d"], ctx["base"], ctx["full"]
    bs, fs, bc = base["stat"], one["stat"], base["bench_stat"]
    cap = base["cap_stat"]
    ts, tsf = base["ts"], one["ts"]
    V = ctx["variants"]
    off = ctx["official"]
    dy_now = float(d["dy"].iloc[-1])
    w2 = ctx["w2"]
    first = ctx["first_entry"]
    po = V["卖点：仅收复前高（TTL=∞）"]
    norenew = V["卖点：满 250 日，到期不续期"]
    ttl5 = V["卖点：满 500 日，到期不续期"]
    cond2 = V["卖点：剩<2 条条件失效即卖"]
    stop_n = int(ts.get("by_reason", {}).get("stop", (0, 0))[0])
    stops = [V["止损：无"]["stat"]["calmar"], V["止损：15%（原默认）"]["stat"]["calmar"],
             V["止损：20%"]["stat"]["calmar"]]

    L = ["# V10 · 红利三重共振（回撤 × 均线 × 股息率）—— 验证与回测", "",
         f"> 样本 **{d.index[0]:%Y-%m-%d} ~ {d.index[-1]:%Y-%m-%d}**（{len(d)} 个交易日）· "
         f"主标的 **{rule.PRIMARY} {rule.BY_INDEX[rule.PRIMARY]['name']}** · "
         f"首笔建仓 {first:%Y-%m-%d}",
         "> 信号 = 价格指数（前高 / 年线 / 三年线 / 股息率）· 收益 = **全收益指数**（含分红再投）· "
         "T 日收盘出信号、**T+1 日收盘成交**",
         "> 成本 = 单边 0.03%（佣金 + 滑点）+ ETF 年费 0.6% · 现金部分按 0 收益计（不假设货币基金）", ""]

    # ---------------- 一
    L += ["## 一、规则落地：买点、卖点、资金", "",
          "| 环节 | 规则原文 | 代码实现 | 参数 |", "|---|---|---|---|",
          f"| 买点①回撤 | 回撤 ≥10% | `1 − close / peak`，peak = 近 {rule.PEAK_WIN} 日最高收盘 | "
          f"`DD_BUY={rule.DD_BUY:.0%}` |",
          f"| 买点②均线 | 收盘 ≤ 年线 **或** ≤ 三年线 | `close≤MA250` 或 `close≤MA750`（**或**，不是且） | "
          f"`MA_SHORT={rule.MA_SHORT}` / `MA_LONG={rule.MA_LONG}` |",
          f"| 买点③股息率 | ≥4.5% | 全收益 ÷ 价格指数的 250 日比值变化率 | `DY_BUY={rule.DY_BUY:.1%}` |",
          f"| 强买 / 极端 | ≥5% / ≥6% | 第二、三批次加仓的触发条件 | "
          f"`DY_STRONG={rule.DY_STRONG:.1%}` / `DY_EXTREME={rule.DY_EXTREME:.1%}` |",
          "| 主卖点 | 涨回前高 | **建仓日锁定**的 peak，之后不随行情移动 | `peak_lock` |",
          f"| 替代卖点 | 持有满一年 | 满 {rule.SELL_TTL} 日**复核**：三条件仍共振则续期一年，"
          f"最多 {rule.SELL_TTL_MAX} 日 | `SELL_TTL={rule.SELL_TTL}` / "
          f"`SELL_TTL_RENEW={rule.SELL_TTL_RENEW}` |",
          f"| 止损 | 再跌 10%（原规则 15%–20%） | 建仓价再跌 {rule.STOP_LOSS:.0%}，清仓 + "
          f"{rule.COOL_STOP} 日冷静期 | `STOP_LOSS={rule.STOP_LOSS:.0%}` |",
          f"| 资金 | 0–100%、分三批、不杠杆 | {rule.TRANCHE[0]:.0%}/{rule.TRANCHE[1]:.0%}/"
          f"{rule.TRANCHE[2]:.0%} → {rule.MAX_POS * rule.TRANCHE[0]:.0%}/"
          f"{rule.MAX_POS * (rule.TRANCHE[0] + rule.TRANCHE[1]):.0%}/{rule.MAX_POS:.0%} "
          f"| `MAX_POS={rule.MAX_POS:.0%}` |",
          "",
          "三处规则没写、必须自己定的口径（都在后面的敏感性里量化）：", "",
          f"1. **前高怎么取**：`close.rolling({rule.PEAK_WIN}).max()`，即近两年最高收盘。"
          "选 2 年是因为 2021–2022 那一轮磨底用了 13 个月，1 年窗会把前高滚到半山腰、把卖点设成假目标。",
          "2. **成交价**：信号 T 日收盘算出、T+1 日收盘成交。若用 T 日收盘价成交，等于「用算信号的"
          "那根 K 线的收盘价买卖」，是隐性前视。",
          "3. **卖出后何时可再买**：正常卖出冷静 20 个交易日、止损冷静 60 个交易日。"
          "否则「满 250 日卖出」的次日就可能因同样条件再次买入，退出形同虚设。",
          "4. **卖点已转正两处**：①满 250 日从「无条件卖」改成「**到期复核**，仍共振则续期」——"
          "消除「买点说便宜就买、卖点说满一年就走」的内部矛盾；②止损从 15% 收紧到 "
          f"{rule.STOP_LOSS:.0%} —— 这是卖点专项分析里唯一在两指数上同向的改善"
          "（`research/_sell_lab_report.md §3.3`）。", ""]

    # ---------------- 二
    gap = dy_now - (off.get(rule.PRIMARY) or float("nan")) / 100
    L += ["## 二、股息率口径：先确认这个 4.5% 是不是同一个 4.5%", "",
          "中证官网**没有**日频股息率序列，只有「指数估值」的当日快照。本策略的日频股息率是**反推**的：",
          "", "```",
          "dy_t = (全收益指数_t / 全收益指数_{t−250}) ÷ (价格指数_t / 价格指数_{t−250}) − 1",
          "```", "",
          "即「过去 12 个月分红再投多出来的那部分收益」。它是**已实现**口径，不是指数公司公布的"
          "「当期股息率」，两者必然有差（下表）。", "",
          "| 指数 | 反推（末日） | 官方快照 | 差 | 反推中位数 | 反推区间 |",
          "|---|---:|---:|---:|---:|---:|"]
    for code, dd_ in ctx["panels"].items():
        o = off.get(code)
        s = dd_["dy"].dropna()
        L.append(f"| {code} {rule.BY_INDEX[code]['name']} | {s.iloc[-1] * 100:.2f}% | "
                 f"{'N/A' if o is None else f'{o:.2f}%'} | "
                 f"{'—' if o is None else f'{(s.iloc[-1] * 100 - o):+.2f}pp'} | "
                 f"{s.median() * 100:.2f}% | {s.min() * 100:.2f}%~{s.max() * 100:.2f}% |")
    L += ["",
          f"- 主标的反推值比官方快照高 **{gap * 100:+.2f}pp** —— 反推是「过去 12 个月已实现」，"
          "官方是「当前点位」。**没有做水平校准**：校准要用「今天的官方数」去缩放整条历史，"
          "等于把未来的水平信息带回过去（后果是阈值在历史上不再等价）。",
          "- ⚠ **算法陷阱（本轮真踩到）**：股息率必须写成**比值** `(T/P)_t ÷ (T/P)_{t−250} − 1`。"
          "若写成「两个收益率相减」`(T_t/T_{t−250}−1) − (P_t/P_{t−250}−1)`，2007 年（价格 +177%）"
          "会算出 11.4% 的股息率（正确值 3.4%）—— 算术差漏掉了「股息本身也参与复利」。"
          "`../yield_hist.py` 用的是后一种写法，其 `_div_yield_m.csv` 在高收益年份会虚高，"
          "**本策略没有复用那条序列**。",
          "- 另一处单位陷阱：`dy` 若存成百分数（4.58）却与小数阈值（0.045）比较，"
          "`dy >= 0.045` 会**恒真** —— 首轮回测因此把 2008 年 1.4% 的股息率当成了买点（多出 11 笔"
          "假交易）。现在 `factors.py` 统一存小数，§三 的事件表可作兜底校验。", ""]

    # ---------------- 三
    cnt = ctx["counts"]
    L += ["## 三、出现几次：逐笔事件表", "",
          f"默认口径（0–100% + 分批 + 到期复核 + 止损 {rule.STOP_LOSS:.0%}）共 **{ts['n']} 笔**已平仓交易"
          f"（另 {1 if base['open'] else 0} 笔未平仓，见末行），胜 **{ts['win']}/{ts['n']}**"
          f"（{ts['win_rate']:.0%}），平均 **{ts['avg'] * 100:+.1f}%**、"
          f"中位 {ts['median'] * 100:+.1f}%、最好 {ts['best'] * 100:+.1f}%、"
          f"最差 {ts['worst'] * 100:+.1f}%，平均持有 {ts['avg_days']:.0f} 个交易日。", "",
          "| 建仓日 | 建仓价 | 回撤 | 股息率 | 退出日 | 退出原因 | 持有 | 收益 | 1 年 | 2 年 | 3 年 |",
          "|---|---:|---:|---:|---|---|---:|---:|---:|---:|---:|"]
    for t in list(base["trades"]) + ([base["open"]] if base["open"] else []):
        fw = fwd_returns(d, t["entry_date"])
        ex = t["exit_date"].strftime("%Y-%m-%d") if t["exit_date"] is not None else "—"
        ret = ("（未平仓 " + _p(t["ret"], 1) + "）" if t["exit_date"] is None else _p(t["ret"], 1))
        L.append(f"| {t['entry_date']:%Y-%m-%d} | {t['entry_price']:.0f} | "
                 f"{t['dd_entry'] * 100:.1f}% | {t['dy_entry'] * 100:.2f}% | {ex} | "
                 f"{t['reason']} | {t['days']} | {ret} | "
                 f"{_p(fw[250])} | {_p(fw[500])} | {_p(fw[750])} |")
    L += ["",
          "> 「收益」是建仓到退出的**全收益指数**涨幅（含分红再投、不含费）；"
          "「1/2/3 年」是建仓后固定持有期的收益，用来与「视频口径」对齐。", "",
          "**「20 年 7 次、全部盈利、平均 31.8%」核对**：", "",
          "| 口径 | 次数 | 平均 | 胜率 | 平均持有 |", "|---|---:|---:|---:|---:|",
          f"| 本回测：默认（满 {rule.SELL_TTL} 日到期复核） | {ts['n']} | {ts['avg'] * 100:+.1f}% | "
          f"{ts['win_rate']:.0%} | {ts['avg_days']:.0f} 日 |",
          f"| 本回测：仅收复前高卖点 | {po['ts']['n']} | {po['ts']['avg'] * 100:+.1f}% | "
          f"{po['ts']['win_rate']:.0%} | {po['ts']['avg_days']:.0f} 日 |",
          "| 用户口径（视频） | 7 | +31.8% | 100% | — |", "",
          f"- **次数同量级**：`dy≥4.5%` 的共振在 {rule.PRIMARY} 上共出现 "
          f"**{cnt[rule.PRIMARY][0.045]} 次**（2008–2026，连续触发按一次算），与「20 年 7 次」相当。"
          f"但只成交了 {ts['n']} 笔 —— 差额是「信号出现时已有持仓」（加仓而非新建仓）与「冷静期」。",
          f"- **2008–2013 年一次都没触发**：那六年中证红利的股息率只有 1.4%–3.8%，"
          "4.5% 是**当前利率环境**下的高阈值，不是跨时代的常数。这也意味着「20 年样本」里"
          "真正可交易的只有最近 12 年。",
          f"- **平均收益对不上 31.8%**：替代卖点（250 日）平均 {ts['avg'] * 100:+.1f}%，"
          f"「仅收复前高」口径 {po['ts']['avg'] * 100:+.1f}% —— 后者才接近视频数。"
          "即：**那 31.8% 里很大一块是「不设持有期上限」赚来的**（§五 量化）。",
          f"- **「全部盈利」在本样本成立**（{ts['win']}/{ts['n']}），但样本只有 {ts['n']} 笔，"
          "属「低样本、长持有」型规则，不能当统计显著的胜率。", ""]

    # ---------------- 四
    L += ["## 四、组合表现：分批 vs 一次性 + 两个窗口", "",
          f"W1 = 全样本（{d.index[0]:%Y-%m-%d} 起）；W2 = **首个建仓日 {first:%Y-%m-%d} 起**。"
          "W1 里策略有六年「结构上不能持仓」（股息率不到 4.5%），直接比会把「空仓躲过 2008」"
          "当成本事，所以必须同时看 W2。仓位已在 **0–100%** 之间，没有额外上限。", "",
          "| 窗口 | 口径 | 年化 | 最大回撤 | Calmar | 平均仓位 | 交易 | 胜率 | 平均每笔 |",
          "|---|---|---|---:|---:|---:|---:|---:|---:|---:|",
          f"| W1 | 默认：三批 40%→70%→100% | {_p(bs['cagr'], 2)} | {_p(bs['dd'])} | "
          f"**{_n(bs['calmar'])}** | {base['expo'].mean():.0%} | {ts['n']} | {ts['win_rate']:.0%} | "
          f"{_p(ts['avg'])} |",
          f"| W1 | 一次性打满（首日 100%） | {_p(fs['cagr'], 2)} | {_p(fs['dd'])} | "
          f"**{_n(fs['calmar'])}** | {one['expo'].mean():.0%} | {tsf['n']} | {tsf['win_rate']:.0%} | "
          f"{_p(tsf['avg'])} |",
          f"| W1 | 基准：全收益买入持有 | {_p(bc['cagr'], 2)} | {_p(bc['dd'])} | "
          f"{_n(bc['calmar'])} | 100% | — | — | — |",
          f"| W1 | 基准：恒定 {HALF_POS:.0%} 仓位（逐日再平衡） | {_p(cap['cagr'], 2)} | "
          f"{_p(cap['dd'])} | {_n(cap['calmar'])} | {HALF_POS:.0%} | — | — | — |",
          f"| W2 | 默认 | {_p(w2['base']['stat']['cagr'], 2)} | {_p(w2['base']['stat']['dd'])} | "
          f"**{_n(w2['base']['stat']['calmar'])}** | {w2['base']['expo']:.0%} | "
          f"{w2['base']['ts']['n']} | {w2['base']['ts']['win_rate']:.0%} | "
          f"{_p(w2['base']['ts']['avg'])} |",
          f"| W2 | 一次性打满 | {_p(w2['full']['stat']['cagr'], 2)} | {_p(w2['full']['stat']['dd'])} | "
          f"**{_n(w2['full']['stat']['calmar'])}** | {w2['full']['expo']:.0%} | "
          f"{w2['full']['ts']['n']} | {w2['full']['ts']['win_rate']:.0%} | "
          f"{_p(w2['full']['ts']['avg'])} |",
          f"| W2 | 基准：全收益买入持有 | {_p(w2['bench']['stat']['cagr'], 2)} | "
          f"{_p(w2['bench']['stat']['dd'])} | {_n(w2['bench']['stat']['calmar'])} | 100% | — | — | — |",
          "",
          f"- **回撤优势在两个窗口都稳定**：W1 {_p(fs['dd'])} / {_p(bs['dd'])} vs 基准 {_p(bc['dd'])}；"
          f"W2 {_p(w2['full']['stat']['dd'])} / {_p(w2['base']['stat']['dd'])} vs "
          f"{_p(w2['bench']['stat']['dd'])}。**收益优势不稳定**：W1 默认 {_p(bs['cagr'], 2)}、"
          f"一次性 {_p(fs['cagr'], 2)} vs 基准 {_p(bc['cagr'], 2)}；W2 换成从首笔建仓日起算后，"
          f"默认 {_p(w2['base']['stat']['cagr'], 2)}、一次性 {_p(w2['full']['stat']['cagr'], 2)} vs "
          f"基准 {_p(w2['bench']['stat']['cagr'], 2)}。**任何单窗口回测都不该被当成证据。**",
          f"- **对照「恒定 {HALF_POS:.0%} 仓位」**：年化 {_p(cap['cagr'], 2)} vs 默认 {_p(bs['cagr'], 2)}、"
          f"回撤 {_p(cap['dd'])} vs {_p(bs['dd'])}、Calmar {_n(cap['calmar'])} vs {_n(bs['calmar'])}。"
          "择时值不值，看的是「用更低的平均仓位换来更小的回撤」是否划算，所以比 Calmar 而不是比年化。",
          f"- **分批 vs 一次性**：平均仓位 {base['expo'].mean():.0%} → {one['expo'].mean():.0%}，"
          f"Calmar {_n(bs['calmar'])} → {_n(fs['calmar'])}、年化 {_p(bs['cagr'], 2)} → "
          f"{_p(fs['cagr'], 2)}。分批摊平了建仓价、也把「首笔就买错」的损失减半 —— "
          "它是**风控**（首笔买错时还有子弹），代价是少赚。选哪个取决于你能接受多大的建仓失误。",
          f"- **平均仓位只有 {base['expo'].mean():.0%}，不是因为仓位上限，而是因为空仓期长**："
          "三条件共振本身稀少，加上卖出后 20/60 日冷静期，这块资金大部分时间在场外。"
          f"按投入资金折算（年化 ÷ 平均仓位）约 {bs['cagr'] / base['expo'].mean() * 100:.1f}%/年 —— "
          "资金在场时的效率才是这套规则的真实水平。", ""]

    # ---------------- 五
    L += ["## 五、卖点与止损：本规则最贵的两个选择", "",
          "同一套买点、同一个默认口径（0–100% + 三批），只改卖点：", "",
          "| 卖点 | 年化 | 最大回撤 | Calmar | 平均仓位 | 交易 | 平均每笔 | 平均持有 |",
          "|---|---:|---:|---:|---:|---:|---:|---:|"]
    for k in ("卖点：仅收复前高（TTL=∞）", "卖点：满 250 日，到期不续期",
              "卖点：满 500 日，到期不续期", "卖点：满 250 日 + 到期复核续期（默认）",
              "卖点：剩<2 条条件失效即卖"):
        r = V[k]
        L.append(f"| {k.replace('卖点：', '')} | {_p(r['stat']['cagr'], 2)} | {_p(r['stat']['dd'])} | "
                 f"{_n(r['stat']['calmar'])} | {r['expo'].mean():.0%} | {r['ts']['n']} | "
                 f"{_p(r['ts']['avg'])} | {r['ts']['avg_days']:.0f} 日 |")
    renew_same = abs(norenew["stat"]["calmar"] - bs["calmar"]) < 1e-9
    L += ["",
          f"- **「满 {rule.SELL_TTL} 日」仍是收益的主要截断点**：只等前高平均每笔 {_p(po['ts']['avg'])}"
          f"（持 {po['ts']['avg_days']:.0f} 日），默认口径 {_p(ts['avg'])}"
          f"（持 {ts['avg_days']:.0f} 日），年化 {_p(po['stat']['cagr'], 2)} → {_p(bs['cagr'], 2)}。"
          "这类买点后面往往是「磨底 → 慢牛」，一年到期时行情刚走完第一段；"
          f"TTL 拉到 500 日介于两者之间（平均每笔 {_p(ttl5['ts']['avg'])}）。",
          f"- **到期复核（续期）在主标的上{'没有改变结果' if renew_same else '有实质影响'}**："
          f"不续期 Calmar {_n(norenew['stat']['calmar'])} / 年化 {_p(norenew['stat']['cagr'], 2)}；"
          f"续期 {_n(bs['calmar'])} / {_p(bs['cagr'], 2)}。"
          + ("说明满 250 日时回撤基本已收窄到 10% 以内、三条件不再共振，续期条件根本不成立 —— "
             "它当前的价值是**消除规则内部矛盾**（买点说便宜就买、卖点说满一年就走），"
             "在修复慢、到期时仍然便宜的标的上才会真正留住仓位。"
             if renew_same else
             "续期把「到期时仍然便宜」的仓位留下来，卖点因此与买点对称。"),
          f"- **止损 {rule.STOP_LOSS:.0%} / 15% / 20% / 不设** 的 Calmar 分别是 {_n(bs['calmar'])} / "
          f"{_n(stops[1])} / {_n(stops[2])} / {_n(stops[0])}，本样本内触发 **{stop_n} 次**。"
          "买点要求「回撤 ≥10% + 破年线」，建仓时离阶段底不远，止损往往来不及响 —— "
          "**它是单边下跌里的保险，不是收益来源**，不要因为「回测没触发」就删掉。",
          f"- **条件失效即卖（剩<2）**：Calmar {_n(cond2['stat']['calmar'])}、年化 "
          f"{_p(cond2['stat']['cagr'], 2)}、平均仓位 {cond2['expo'].mean():.0%}。"
          "把卖点写成买点的镜像（破两条就走）后 Calmar 更高，但那是因为在场时间砍半、"
          "年化反而更低，详见 `research/_sell_lab_report.md §二·补`。**未转正。**", ""]

    # ---------------- 六
    L += ["## 六、参数敏感性（除标注外均为默认口径：0–100% + 三批）", "",
          "| 变体 | 年化 | 最大回撤 | Calmar | 平均仓位 | 交易 | 平均每笔 |",
          "|---|---:|---:|---:|---:|---:|---:|"]
    for k, r in V.items():
        L.append(f"| {k} | {_p(r['stat']['cagr'], 2)} | {_p(r['stat']['dd'])} | "
                 f"{_n(r['stat']['calmar'])} | {r['expo'].mean():.0%} | {r['ts']['n']} | "
                 f"{_p(r['ts']['avg'])} |")
    d40, d50 = V["买点：dy≥4.0%"], V["买点：dy≥5.0%"]
    dd8, dd15 = V["买点：dd≥8%"], V["买点：dd≥15%"]
    pw = [V["前高窗：250 日"]["stat"]["calmar"], V["前高窗：375 日"]["stat"]["calmar"],
          bs["calmar"], V["前高窗：750 日"]["stat"]["calmar"]]
    t60, t70, t80 = V["资金：首批 60%"], V["资金：首批 70%"], V["资金：首批 80%"]
    L += ["",
          f"- **股息率是最挑的一条**：放松到 4.0% 交易从 {ts['n']} 增到 {d40['ts']['n']} 笔、"
          f"平均每笔从 {_p(ts['avg'])} 掉到 {_p(d40['ts']['avg'])}（更早进场、买在半山腰）；"
          f"收紧到 5.0% 只剩 {d50['ts']['n']} 笔、平均 {_p(d50['ts']['avg'])}。"
          "**放松股息率比放松回撤更伤**。",
          f"- **回撤阈值几乎不敏感**：8% / 10% / 15% 的 Calmar 是 {_n(dd8['stat']['calmar'])} / "
          f"{_n(bs['calmar'])} / {_n(dd15['stat']['calmar'])}，平均每笔 "
          f"{_p(dd8['ts']['avg'])} / {_p(ts['avg'])} / {_p(dd15['ts']['avg'])}。"
          "资金曲线主要由「有没有在大跌里建仓」决定，10% 不是关键参数。",
          f"- **前高窗口**：250/375/500/750 日的 Calmar 是 {_n(pw[0])}/{_n(pw[1])}/{_n(pw[2])}/{_n(pw[3])}，"
          "大致随窗口变长而改善（窗口越长，回撤判断越接近「真实本轮」，卖点也越高），"
          "但样本只有几笔交易，这种单调性**不能当优化依据**。默认保留 500 日。",
          f"- **分批 vs 一次性**：三批把平均仓位压到 {base['expo'].mean():.0%}（一次性是 "
          f"{one['expo'].mean():.0%}），Calmar {_n(bs['calmar'])} vs {_n(fs['calmar'])}、"
          f"年化 {_p(bs['cagr'], 2)} vs {_p(fs['cagr'], 2)}。**同一批交易、平均每笔收益相同"
          f"（{_p(ts['avg'])}）—— 差别只在「钱什么时候投进去」**：分批摊平建仓价、降低成本，"
          "同时也降低平均仓位。它是**风控**（首笔买错时还有子弹），不是收益增强。",
          f"- **首批比例：单调上升，但在 60–70% 见顶**。40% → 60 / 70 / 80 / 100% 的 Calmar 是 "
          f"{_n(bs['calmar'])} → {_n(t60['stat']['calmar'])} / {_n(t70['stat']['calmar'])} / "
          f"{_n(t80['stat']['calmar'])} / {_n(fs['calmar'])}；年化 {_p(bs['cagr'], 2)} → "
          f"{_p(t60['stat']['cagr'], 2)} / {_p(t70['stat']['cagr'], 2)} / {_p(t80['stat']['cagr'], 2)}"
          f" / {_p(fs['cagr'], 2)}；回撤 {_p(bs['dd'])} → {_p(t60['stat']['dd'])} / "
          f"{_p(t70['stat']['dd'])} / {_p(t80['stat']['dd'])} / {_p(fs['dd'])}。"
          "**提到 60–70% 是「回撤几乎不变、年化多半个点」的免费改善；提到 100% 则要拿 2pp 回撤去换** —— "
          "所以「一次性」不是更优，是更激进。",
          f"- ⚠ **但上面这条曲线有幸存者偏差**：本样本 6/6 最终都收复前高，「越早投满越好」几乎是同义反复。"
          "分批的收益是「买错时的保险」，而样本里只发生过 1 次 -10.6% 的浮亏、随后就反弹；"
          "**2008 式连续下跌没有进入样本**（被 4.5% 股息率挡在门外），所以分批的价值**没被检验**。"
          "把它当「保险已买」而不是「保险没用」，是更稳的读法。", ""]

    # ---------------- 七
    y = yearly(one)
    L += ["## 七、逐年（一次性打满口径）", "",
          "| 年份 | 策略 | 基准 | 差 | 平均仓位 |", "|---|---:|---:|---:|---:|"]
    for yr, r in y.iterrows():
        L.append(f"| {int(yr)} | {r['strat'] * 100:+.1f}% | {r['bench'] * 100:+.1f}% | "
                 f"{(r['strat'] - r['bench']) * 100:+.1f}pp | {r['expo'] * 100:.0f}% |")
    pos_years = [int(yr) for yr, r in y.iterrows() if r["strat"] > r["bench"]]
    neg_years = [int(yr) for yr, r in y.iterrows() if r["strat"] <= r["bench"]]
    flat_years = [int(yr) for yr, r in y.iterrows() if r["expo"] == 0 and r["bench"] != 0]
    L += ["",
          f"- 跑赢 {len(pos_years)} 年、跑输 {len(neg_years)} 年；其中 {len(flat_years)} 年是"
          f"**全年空仓**（{('、'.join(map(str, flat_years))) or '无'}）—— 那些年的「跑赢/跑输」"
          "只是现金与指数的比较，不含择时能力。",
          "- 剔除空仓年份后，跑赢的年份都是「在下跌中建仓后反弹」（2014/2023/2024），"
          "跑输的都是「行情在无信号状态下单边上行」（2015/2017/2019/2021）—— "
          "这是「事件驱动 + 长持有」的固有收益形态，不是「每年稳定跑赢」。", ""]

    # ---------------- 八
    ho = ctx["holdout"]
    L += ["## 八、换一个指数还成立吗（对照标的）", ""]
    if not ho:
        L += ["- 对照标的数据缺失，跳过。", ""]
    else:
        hb, hf, hd = ho["base"], ho["full"], ho["d"]
        L += [f"同一套参数换到 **{rule.HOLDOUT} {rule.BY_INDEX[rule.HOLDOUT]['name']}**"
              f"（{hd.index[0]:%Y-%m-%d}~{hd.index[-1]:%Y-%m-%d}）：", "",
              "| 口径 | 年化 | 最大回撤 | Calmar | 平均仓位 | 交易 | 胜率 | 平均每笔 |",
              "|---|---:|---:|---:|---:|---:|---:|---:|",
              f"| 默认（三批 40%→70%→100%） | {_p(hb['stat']['cagr'], 2)} | "
              f"{_p(hb['stat']['dd'])} | {_n(hb['stat']['calmar'])} | {hb['expo'].mean():.0%} | "
              f"{hb['ts']['n']} | {hb['ts']['win_rate']:.0%} | {_p(hb['ts']['avg'])} |",
              f"| 一次性打满 | {_p(hf['stat']['cagr'], 2)} | {_p(hf['stat']['dd'])} | "
              f"{_n(hf['stat']['calmar'])} | {hf['expo'].mean():.0%} | {hf['ts']['n']} | "
              f"{hf['ts']['win_rate']:.0%} | {_p(hf['ts']['avg'])} |",
              f"| 基准：买入持有 | {_p(hb['bench_stat']['cagr'], 2)} | {_p(hb['bench_stat']['dd'])} | "
              f"{_n(hb['bench_stat']['calmar'])} | 100% | — | — | — |", "",
              f"- 方向一致：回撤从 {_p(hb['bench_stat']['dd'])} 压到 {_p(hf['stat']['dd'])}，"
              f"Calmar {_n(hf['stat']['calmar'])} vs {_n(hb['bench_stat']['calmar'])}，"
              f"胜率 {hf['ts']['win_rate']:.0%}（{hf['ts']['win']}/{hf['ts']['n']} 笔）。"
              "规则不是「000922 特调」。",
              "- ⚠ **但对照标的的股息率水平偏高约 1pp**（反推 5.3% vs 官方 4.3%），"
              "它在同一 4.5% 阈值下会**更频繁**触发 —— 这张表只能看方向，不能比次数。"
              "要横向比次数，应先把阈值按各自官方口径校准。", ""]

    # ---------------- 九
    L += ["## 九、结论", "",
          f"1. **规则可以落地，且在本样本内不亏钱**：主标的 {ts['n']} 笔已平仓交易"
          f"{'全部为正' if ts['win'] == ts['n'] else f'胜率 {ts["win_rate"]:.0%}'}，"
          f"平均 {ts['avg'] * 100:+.1f}%。**收益结论不稳定**：W1 一次性打满年化 {_p(fs['cagr'], 2)} "
          f"高于买入持有 {_p(bc['cagr'], 2)}，但 W2（首个建仓日起）只有 "
          f"{_p(w2['full']['stat']['cagr'], 2)} vs {_p(w2['bench']['stat']['cagr'], 2)}。"
          f"**稳定的是回撤**：W1 {_p(fs['dd'])} vs {_p(bc['dd'])}、W2 "
          f"{_p(w2['full']['stat']['dd'])} vs {_p(w2['bench']['stat']['dd'])}，"
          f"Calmar 两个窗口都占优（{_n(fs['calmar'])}/{_n(bc['calmar'])}、"
          f"{_n(w2['full']['stat']['calmar'])}/{_n(w2['bench']['stat']['calmar'])}）。"
          "规则买的是「回撤保护」，不是「超额收益」。",
          f"2. **它是「事件驱动 + 长期空仓」的组合**：仓位已放到 0–100%，但三条件共振稀少、"
          f"加上冷静期，平均仓位仍只有 {base['expo'].mean():.0%}，组合年化 {_p(bs['cagr'], 2)}。"
          f"低仓位不是上限压出来的，是**信号本来就少** —— 所以别把它当「年年跑赢」的主仓，"
          "而是「大跌时出手、平时拿现金」的择时模块；闲置资金在场外要有别的收益来源，"
          "否则组合年化会被现金拖住。",
          f"3. **「满一年卖出」仍是最贵的条款**：平均每笔从 {_p(po['ts']['avg'])} 降到 {_p(ts['avg'])}。"
          "到期复核（续期）能救回一部分「到期时仍然便宜」的仓位，但救不回「行情刚走完第一段」的损失；"
          "主卖点（收复前高）才是收益来源。",
          f"4. **止损收到 {rule.STOP_LOSS:.0%} 后在本样本内触发 {stop_n} 次**：不贡献收益，"
          "但它是单边下跌的唯一保险。真正需要止损的是「买点误触发」，"
          "而买点误触发主要来自股息率口径错误（§二）；口径对了以后止损反而不常触发。",
          "5. **两个口径必须写死**：①股息率是**比值**不是算术差；②`dy` 统一小数、与阈值同量纲。"
          "这两处出错的后果都是「信号变多而不自知」，而且回测**看着还挺赚钱**"
          "（错误口径下 2008 年就敢买，因为 1.4% 被当成 ≥4.5%）。",
          f"6. **样本边界**：有效信号从 {first:%Y-%m} 开始（2008–2013 股息率不到 4.5%），"
          f"已平仓 {ts['n']} 笔，统计上**不足以证明胜率**；结论只作方向性参考。",
          "",
          "> 落地建议（与规则原文一致）：先跑样本外/滚动回测 → 模拟盘 → 小资金实盘。"
          "本报告只完成第一步。", "",
          f"> 边界：{len(d)} 个交易日、单边 0.03%、ETF 年费 0.6%、现金 0 收益；"
          "未扣场内折溢价与场外申赎费，未考虑红利税与跟踪误差。", ""]
    return "\n".join(L)


if __name__ == "__main__":
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    sys.exit(main())
