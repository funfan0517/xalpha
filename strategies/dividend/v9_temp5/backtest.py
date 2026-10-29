#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""V9 · 五因子估值温度 —— 验证与回测

回答四个问题（全部同一段真实数据，不用估计值）：

1. **PB(16%) 是不是一个独立维度？** `PB = PE × ROE 常数` ⇒ PB 分位 ≡ PE 分位，
   所以「五因子」实际是 PE 36% + 利差 35% + 乖离 15% + 位置 14%。本脚本实测两者的分位差。
2. **时间维度能不能择时？** 板块温度（有估值标的均值）的自身历史分位 ≥80 减半 / ≥90 清仓；
   并与「直接拿规则 §4.7 的绝对档（≥65 减半 / ≥75 清仓）」对照 —— 后者是外部规则的原意。
3. **横截面能不能选品？** 每月选温度最低的 1~3 只等权。
4. **含 PB 与不含 PB 差多少？** 同一套规则换成四维归一权重（35/20/15/14 归一）重跑。

口径与边界（不隐藏）
--------------------
* 收益用**基金累计净值** `totvalue`（含分红再投），与 V0–V8 一致；温度算在**跟踪指数**上
  （规则 R0-2：基金有费率与跟踪误差，估值只有指数口径）。
* 样本 2020-01 起 —— 受基金净值可得性限制（008163/008115 均 2019-12 之后成立），
  与 V0–V8 的回测窗口对齐，便于横比。
* 温度受「MA250 预热 + 分位最少 300 个观测」双重限制，各标的可用起点不同
  （931468 自 2022-05、930955 自 2019-05），横截面逐月只用**可得标的**，不虚拟历史。
* 两个标普系列（008163/005125）无估值 → 价格温度，**不进排名、不进横截面**，只进等权基准。

产物
----
    backtest/_v9_panel.jsonl   月度因子面板（date/code/temp/各子分）
    backtest/_v9_bt.jsonl      逐月组合快照（基准/主方案/仓位倍率/入选标的）
    backtest/_v9_report.md     本报告

用法::

    python strategies/dividend/v9_temp5/backtest.py [--refresh]
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

import factors  # noqa: E402  pylint: disable=wrong-import-position
import pool  # noqa: E402  pylint: disable=wrong-import-position
import rule  # noqa: E402  pylint: disable=wrong-import-position

START = "2020-01-01"        # 与 V0–V8 对齐（受 008163/008115 净值起点限制）
FEE = 0.0003                # 单边 0.03%
MIN_BOARD = 3               # 板块温度至少 3 只有值才算


# ------------------------------------------------------------------ 组合工具
def stats(r: pd.Series) -> dict:
    r = r.dropna()
    if r.empty:
        return {"cagr": float("nan"), "dd": float("nan"), "calmar": float("nan"),
                "vol": float("nan"), "sharpe": float("nan"), "total": float("nan")}
    nav = (1 + r).cumprod()
    yrs = max(len(r) / 12, 1e-9)
    cagr = nav.iloc[-1] ** (1 / yrs) - 1 if nav.iloc[-1] > 0 else -1.0
    dd = float((nav / nav.cummax() - 1).min())
    vol = float(r.std() * np.sqrt(12))
    return {"cagr": cagr, "dd": dd,
            "calmar": cagr / abs(dd) if dd else float("nan"),
            "vol": vol, "sharpe": float(r.mean() * 12) / vol if vol else float("nan"),
            "total": float(nav.iloc[-1] - 1)}


def run_weights(w: pd.DataFrame, rets: pd.DataFrame, fee: float = FEE) -> pd.Series:
    """月末定权、下月生效；成本 = fee × Σ|Δw|。"""
    wl = w.shift(1).fillna(0.0)
    port = (wl * rets).sum(axis=1, min_count=1)
    turn = (wl - wl.shift(1).fillna(0.0)).abs().sum(axis=1)
    return (port - turn * fee).dropna()


def eq_weights(ok: pd.DataFrame) -> pd.DataFrame:
    n = ok.sum(axis=1).replace(0, np.nan)
    return ok.div(n, axis=0).fillna(0.0)


def lowest_weights(temp: pd.DataFrame, k: int) -> pd.DataFrame:
    out = pd.DataFrame(0.0, index=temp.index, columns=temp.columns)
    for ts, row in temp.iterrows():
        ok = row.dropna()
        if ok.empty:
            continue
        pick = ok.nsmallest(min(k, len(ok))).index
        out.loc[ts, pick] = 1.0 / len(pick)
    return out


def expanding_pct(s: pd.Series, min_n: int = rule.Q_MIN_N) -> pd.Series:
    """扩张窗口分位（含当前点）—— 时间维度用「温度自身的历史分位」。"""
    v = s.to_numpy(dtype=float)
    out = np.full(len(v), np.nan)
    for i in range(len(v)):
        w = v[:i + 1]
        w = w[np.isfinite(w)]
        if len(w) >= min_n and np.isfinite(v[i]):
            out[i] = float((w < v[i]).mean())
    return pd.Series(out, index=s.index)


def time_mult(q: pd.Series, cut: float, out: float) -> pd.Series:
    m = pd.Series(1.0, index=q.index)
    m[q >= cut] = 0.5
    m[q >= out] = 0.0
    m[q.isna()] = 1.0
    return m


def abs_mult(board: pd.Series, cut: float, out: float) -> pd.Series:
    """绝对档对照：规则 §4.7 的原意（≥65 偏热减半 / ≥75 过热清仓）。"""
    m = pd.Series(1.0, index=board.index)
    m[board >= cut] = 0.5
    m[board >= out] = 0.0
    m[board.isna()] = 1.0
    return m


# ------------------------------------------------------------------ 检验
def ts_ic(panels: dict, rets: pd.DataFrame) -> list[dict]:
    """时间序列 IC：温度 vs 未来 1/3/6/12 月基金收益（Spearman）。"""
    rows = []
    for code in rule.CODES:
        if code not in panels or code not in rets.columns:
            continue
        t = panels[code]["monthly"]["temp"]
        if t.dropna().empty:
            continue
        r = rets[code]
        for h in (1, 3, 6, 12):
            fwd = (1 + r).rolling(h).apply(np.prod, raw=True).shift(-h) - 1
            d = pd.concat([t, fwd], axis=1).dropna()
            if len(d) > 24 and d.iloc[:, 0].nunique() > 3:
                rows.append({"code": code, "name": rule.NAME[code], "h": h,
                             "ic": float(d.iloc[:, 0].corr(d.iloc[:, 1], method="spearman")),
                             "n": len(d)})
    return rows


def cs_ic(temp: pd.DataFrame, rets: pd.DataFrame, hs=(1, 3, 6)) -> dict:
    out = {}
    for h in hs:
        fwd = ((1 + rets).rolling(h).apply(np.prod, raw=True).shift(-h) - 1)
        ics = []
        for ts in temp.index:
            a, b = temp.loc[ts], fwd.loc[ts] if ts in fwd.index else None
            if b is None:
                continue
            ok = a.notna() & b.reindex(a.index).notna()
            if ok.sum() >= 3:
                ics.append(a[ok].corr(b.reindex(a.index)[ok], method="spearman"))
        out[h] = (float(np.mean(ics)) if ics else float("nan"), len(ics))
    return out


def tercile(panels: dict, rets: pd.DataFrame, h: int = 6) -> dict:
    recs = []
    for code in rule.CODES:
        if code not in panels or code not in rets.columns:
            continue
        t = panels[code]["monthly"]["temp"]
        if t.dropna().empty:
            continue
        fwd = (1 + rets[code]).rolling(h).apply(np.prod, raw=True).shift(-h) - 1
        recs.append(pd.DataFrame({"temp": t, "fwd": fwd}).dropna())
    if not recs:
        return {}
    d = pd.concat(recs)
    d["g"] = pd.qcut(d["temp"], 3, labels=["低温", "中温", "高温"])
    g = d.groupby("g", observed=True)["fwd"].agg(["mean", "count"])
    return {str(k): (float(v["mean"]), int(v["count"])) for k, v in g.iterrows()}


# ------------------------------------------------------------------ 主流程
def load_rets() -> pd.DataFrame:
    navs = pool.load_navs(codes=rule.CODES)
    me = navs.resample("ME").last()
    return me.pct_change()


def build(weights: dict | None = None, refresh: bool = False) -> dict:
    panels = factors.build(weights=weights, refresh=refresh)
    temp = factors.monthly_temp(panels)
    rets = load_rets()
    start = pd.Timestamp(START)
    today = pd.Timestamp.today().normalize()
    idx = temp.index.union(rets.index)
    idx = idx[(idx >= start) & (idx <= today)]
    temp = temp.reindex(idx)
    rets = rets.reindex(idx)
    # 当月未走完（月末标签在未来）→ 不进回测
    complete = temp.index[temp.index <= today]
    temp, rets = temp.loc[complete], rets.loc[complete]
    rets = rets.iloc[1:]                       # 首月无收益
    temp = temp.loc[rets.index]
    return {"panels": panels, "temp": temp, "rets": rets}


def sims_of(d: dict) -> dict:
    temp, rets = d["temp"], d["rets"]
    valued = [c for c in rule.VALUED if c in temp.columns]
    tv = temp[valued]
    board = tv.mean(axis=1, skipna=True)
    board[tv.notna().sum(axis=1) < MIN_BOARD] = np.nan
    bq = expanding_pct(board)

    ok_all = rets.notna()
    w_bench = eq_weights(ok_all)
    w_val = eq_weights(rets[valued].notna())
    m_time = time_mult(bq, rule.CUT_Q, rule.OUT_Q)

    out = {
        "基准：10 只等权": run_weights(w_bench, rets),
        "基准：8 只有估值标的等权": run_weights(w_val, rets),
        "时间：板块温度分位减仓": run_weights(w_bench.mul(m_time, axis=0), rets),
        "横截面：温度最低 2 只": run_weights(lowest_weights(tv, 2), rets),
        "横截面：温度最低 3 只": run_weights(lowest_weights(tv, 3), rets),
        "横截面 top2 + 时间减仓": run_weights(
            lowest_weights(tv, 2).mul(m_time, axis=0), rets),
    }
    return {"sims": out, "board": board, "board_q": bq, "temp_valued": tv,
            "m_time": m_time}


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="V9 五因子估值温度回测")
    ap.add_argument("--refresh", action="store_true", help="强制重抓指数/国债数据")
    args = ap.parse_args(argv)

    base = build(weights=rule.W, refresh=args.refresh)
    nopb = build(weights=rule.W_NO_PB, refresh=False)
    S = sims_of(base)
    S4 = sims_of(nopb)

    temp, rets = base["temp"], base["rets"]
    board, bq = S["board"], S["board_q"]

    # 绝对档对照（规则 §4.7 原意）
    m_abs = abs_mult(board, rule.ABS_CUT, rule.ABS_OUT)
    w_bench = eq_weights(rets.notna())
    sim_abs = run_weights(w_bench.mul(m_abs, axis=0), rets)

    # 阈值敏感性（分位口径）
    sens = {}
    for cut, out in ((0.75, 0.85), (0.80, 0.90), (0.85, 0.95)):
        sens[f"{cut * 100:.0f}/{out * 100:.0f}"] = stats(
            run_weights(w_bench.mul(time_mult(bq, cut, out), axis=0), rets))
    # 阈值敏感性（绝对温度口径）—— 检验「温度水平」本身能不能当阈值
    sens_abs = {}
    for cut, out in ((50, 60), (55, 65), (60, 70), (65, 75)):
        m = abs_mult(board, cut, out)
        r = run_weights(w_bench.mul(m, axis=0), rets)
        bench_r = S["sims"]["基准：10 只等权"]
        diff_abs = (r - bench_r).dropna()
        drop = diff_abs.nlargest(3).index
        adv = stats(r)["cagr"] - stats(bench_r)["cagr"]
        adv_d3 = (stats(r.drop(drop))["cagr"] - stats(bench_r.drop(drop))["cagr"])
        sens_abs[f"{cut}/{out}"] = dict(stats(r), pos=float(m.mean()),
                                        adv=adv, adv_d3=adv_d3)

    # 横截面敏感性：top1 / top2 / top3
    xs = {f"top{k}": stats(run_weights(lowest_weights(S["temp_valued"], k), rets))
          for k in (1, 2, 3)}

    # 逐年 + 剔除最大贡献月
    pos_eff = S["m_time"].shift(1).reindex(rets.index)
    strat = S["sims"]["横截面 top2 + 时间减仓"]
    bench = S["sims"]["基准：10 只等权"]
    yearly = pd.DataFrame({"bench": bench, "strat": strat, "expo": pos_eff}).dropna(
        subset=["bench", "strat"])
    yearly = (1 + yearly[["bench", "strat"]]).groupby(yearly.index.year).prod() - 1
    yearly["expo"] = pos_eff.groupby(pos_eff.index.year).mean()
    diff = (strat - bench).dropna()
    robust = {}
    for k in (1, 3, 5):
        drop = diff.nlargest(k).index
        robust[k] = (stats(bench.drop(drop))["cagr"], stats(strat.drop(drop))["cagr"])

    # PB ≡ PE 实测
    pb_gap = 0.0
    for code in rule.VALUED:
        m = base["panels"][code]["monthly"].dropna(subset=["temp"])
        if not m.empty:
            pb_gap = max(pb_gap, float((m["sub_pb"] - m["sub_pe"]).abs().max()))

    ctx = dict(base=base, nopb=nopb, S=S, S4=S4, sens=sens, sens_abs=sens_abs, xs=xs, yearly=yearly,
               robust=robust, sim_abs=sim_abs, m_abs=m_abs, pb_gap=pb_gap,
               ic=ts_ic(base["panels"], rets),
               cs=cs_ic(S["temp_valued"], rets), tri=tercile(base["panels"], rets),
               bench=bench, strat=strat, pos_eff=pos_eff)

    os.makedirs(rule.BT_DIR, exist_ok=True)
    _write_panel(base["panels"])
    _write_bt(base)
    _write_summary(ctx)
    with open(rule.OUT_REPORT, "w", encoding="utf-8") as fh:
        fh.write(render(ctx))

    print(f"[v9bt] 样本 {rets.index[0]:%Y-%m}~{rets.index[-1]:%Y-%m}（{len(rets)} 月）· "
          f"有估值标的 {len(rule.VALUED)} 只 · 价格温度 2 只")
    for name, r in S["sims"].items():
        st = stats(r)
        print(f"[v9bt] {name:26s} 年化 {st['cagr'] * 100:5.1f}%  回撤 {st['dd'] * 100:6.1f}%  "
              f"Calmar {st['calmar']:5.2f}")
    st = stats(sim_abs)
    print(f"[v9bt] {'对照：绝对档 65/75 减仓':26s} 年化 {st['cagr'] * 100:5.1f}%  "
          f"回撤 {st['dd'] * 100:6.1f}%  Calmar {st['calmar']:5.2f}")
    print(f"[v9bt] PB 分位与 PE 分位最大差 {pb_gap:.6f}")
    print(f"[v9bt] 产物 -> {os.path.relpath(rule.OUT_REPORT, rule.ROOT)}")
    return 0


def _write_summary(ctx: dict) -> None:
    """给 `daily.py` 用的精简结论（避免每日脚本里手抄回测数字而漂移）。"""
    rets = ctx["base"]["rets"]
    sims = {k: {kk: (None if not np.isfinite(vv) else round(float(vv), 4))
                for kk, vv in stats(v).items()} for k, v in ctx["S"]["sims"].items()}
    out = {
        "window": f"{rets.index[0]:%Y-%m}~{rets.index[-1]:%Y-%m}",
        "months": int(len(rets)),
        "sims": sims,
        "xs_calmar": {k: round(float(v["calmar"]), 4) for k, v in ctx["xs"].items()},
        "abs_band": {"pos": round(float(ctx["m_abs"].mean()), 4),
                     "stats": {k: round(float(v), 4) for k, v in stats(ctx["sim_abs"]).items()}},
        "pb_gap": round(float(ctx["pb_gap"]), 9),
        "flags": {"xs": rule.XS_ENABLED, "time": rule.TIME_ENABLED},
    }
    with open(rule.OUT_BT_SUM, "w", encoding="utf-8") as fh:
        json.dump(out, fh, ensure_ascii=False, indent=1)


def _write_panel(panels: dict) -> None:
    df = factors.panel_long(panels)
    with open(rule.OUT_PANEL, "w", encoding="utf-8") as fh:
        for _, r in df.iterrows():
            fh.write(json.dumps(r.to_dict(), ensure_ascii=False) + "\n")


def _write_bt(d: dict) -> None:
    S = sims_of(d)
    temp, rets = d["temp"], d["rets"]
    pick = lowest_weights(S["temp_valued"], 2)
    with open(rule.OUT_BT, "w", encoding="utf-8") as fh:
        for ts in rets.index:
            row = {"date": ts.strftime("%Y-%m-%d"),
                   "bench": round(float(S["sims"]["基准：10 只等权"].get(ts, np.nan)), 6),
                   "strat": round(float(S["sims"]["横截面 top2 + 时间减仓"].get(ts, np.nan)), 6),
                   "mult": round(float(S["m_time"].reindex([ts]).iloc[0]), 2),
                   "board": None if pd.isna(S["board"].get(ts)) else round(float(S["board"].get(ts)), 1),
                   "board_q": None if pd.isna(S["board_q"].get(ts)) else round(float(S["board_q"].get(ts)), 3),
                   "picks": [c for c in pick.columns if pick.at[ts, c] > 1e-9],
                   "temp": {c: (None if pd.isna(temp.at[ts, c]) else round(float(temp.at[ts, c]), 1))
                            for c in temp.columns if pd.notna(temp.at[ts, c])}}
            fh.write(json.dumps(row, ensure_ascii=False) + "\n")


# ------------------------------------------------------------------ 渲染
def _p(x, d=1) -> str:
    return "—" if x is None or (isinstance(x, float) and not np.isfinite(x)) else f"{x * 100:.{d}f}%"


def _n(x, d=2) -> str:
    return "—" if x is None or (isinstance(x, float) and not np.isfinite(x)) else f"{x:.{d}f}"


def _pp(x) -> str:
    """带符号的百分点差；避免出现 "-0.0pp" 这种读着别扭的东西。"""
    if x is None or not np.isfinite(x):
        return "—"
    v = 0.0 if abs(x * 100) < 0.05 else x * 100
    return f"{v:+.1f}pp"


def render(ctx: dict) -> str:
    base, nopb, S, S4 = ctx["base"], ctx["nopb"], ctx["S"], ctx["S4"]
    temp, rets = base["temp"], base["rets"]
    st_bench = stats(ctx["bench"])
    st_strat = stats(ctx["strat"])
    st_abs = stats(ctx["sim_abs"])
    sens = ctx["sens"]
    n_cut = int((S["board_q"] >= rule.CUT_Q).sum())
    n_out = int((S["board_q"] >= rule.OUT_Q).sum())
    expo = float(ctx["pos_eff"].mean())

    L = ["# V9 · 红利五因子估值温度 —— 验证与回测", "",
         f"> 样本 **{rets.index[0]:%Y-%m} ~ {rets.index[-1]:%Y-%m}**（{len(rets)} 个月）· "
         f"池 {len(rule.CODES)} 只（{len(rule.VALUED)} 只有估值口径）",
         "> 收益口径：**基金累计净值**（含分红再投）· 温度口径：**跟踪指数**（规则 R0-2）· "
         "月末决策下月生效 · 成本单边 0.03%",
         "> 温度 = 35%·股债收益差(反号) + 20%·PE + 16%·PB + 15%·年线乖离 + 14%·价格位置，"
         "各取近 2 年日频分位（窗口内不足 300 个观测不给分位）", ""]

    # ---------------- 一
    L += ["## 一、先说 PB：16% 权重没有提供新信息", "",
          f"合成口径 `PB = PE × ROE 常数` ⇒ 常数缩放不改变秩 ⇒ **PB 分位 ≡ PE 分位**。"
          f"全池实测最大差 **{ctx['pb_gap']:.6f}**（0 即恒等）。", "",
          "所以外部规则的「五因子」在数学上是四维：", "",
          "| 维度 | 规则权重 | 实际等价权重 |", "|---|---:|---:|",
          f"| PE（含 PB 合并） | 20% + 16% | **{(rule.W['pe'] + rule.W['pb']) * 100:.0f}%** |",
          f"| 股债收益差 | 35% | {rule.W['spread'] * 100:.0f}% |",
          f"| 年线乖离 | 15% | {rule.W['dev'] * 100:.0f}% |",
          f"| 价格位置 | 14% | {rule.W['pos'] * 100:.0f}% |",
          "",
          "这不是实现 bug，是口径的必然结果 —— 要 PB 真正独立，必须拿到**指数 PB 历史序列**"
          "（中证官网只给 PE(`peg`)，东财只有当前快照）。规则 §4.5 自己也写了「等价 PE 权重 36%」。",
          "",
          "对照：去掉 PB、四维按 35/20/15/14 归一（PE 权重降到 23%）重跑同一套规则 ——", "",
          "| 方案 | 年化 | 最大回撤 | Calmar |", "|---|---:|---:|---:|",
          f"| 含 PB（35/20/16/15/14，V9 本体） | {_p(stats(S['sims']['横截面 top2 + 时间减仓'])['cagr'])} | "
          f"{_p(stats(S['sims']['横截面 top2 + 时间减仓'])['dd'])} | "
          f"{_n(stats(S['sims']['横截面 top2 + 时间减仓'])['calmar'])} |",
          f"| 去 PB（四维归一） | {_p(stats(S4['sims']['横截面 top2 + 时间减仓'])['cagr'])} | "
          f"{_p(stats(S4['sims']['横截面 top2 + 时间减仓'])['dd'])} | "
          f"{_n(stats(S4['sims']['横截面 top2 + 时间减仓'])['calmar'])} |",
          f"| 两者差 | {(stats(S4['sims']['横截面 top2 + 时间减仓'])['cagr'] - stats(S['sims']['横截面 top2 + 时间减仓'])['cagr']) * 100:+.1f}pp | "
          f"— | {(stats(S4['sims']['横截面 top2 + 时间减仓'])['calmar'] - stats(S['sims']['横截面 top2 + 时间减仓'])['calmar']):+.2f} |",
          "",
          "> 差别全部来自「PE 权重 36% vs 23%」，因为 PB 那一维和 PE 完全同步。"
          "换言之：**选 PB 合成口径 = 选「更信 PE」**。", ""]

    # ---------------- 二
    readings = []
    for code in rule.CODES:
        m = base["panels"][code]["monthly"].dropna(subset=["temp"])
        if not m.empty:
            readings.append((code, m.iloc[-1]))
    latest_asof = max(r["asof"] for _, r in readings)
    valued_rows = sorted([x for x in readings if x[1]["temp_kind"] == factors.TEMP_KIND_VAL],
                         key=lambda x: x[1]["temp"])
    px_rows = [x for x in readings if x[1]["temp_kind"] != factors.TEMP_KIND_VAL]

    def _row(i, code, r, mark=""):
        def sub(k):
            v = r.get(f"sub_{k}")
            return "—" if v is None or pd.isna(v) else f"{float(v):.0f}"

        if r["temp_kind"] == factors.TEMP_KIND_VAL:
            band, act = rule.band_of(float(r["temp"]))
        else:       # 规则 §4.4：价格温度禁止套用五因子的状态档措辞
            band, act = "价格温度", "仅在无估值序列时参考，不与五因子并列"
        return (f"| {i} | {code} {rule.NAME[code]}{mark} | {rule.BY_CODE[code]['index_name']} | "
                f"**{r['temp']:.1f}** | {band} | {act} | {sub('spread')} | {sub('pe')} | "
                f"{sub('dev')} | {sub('pos')} |")

    L += ["## 二、当前读数", "",
          f"> 数据截止 **{latest_asof:%Y-%m-%d}**（当月未走完，按最新收盘算温度；不进回测）。",
          "> 有估值口径的按温度升序（越冷越靠前）；† 为价格温度，**不与五因子温度比大小、不参与排名**。", "",
          "| # | 标的 | 跟踪指数 | 温度 | 状态 | 动作 | 利差 | PE | 乖离 | 位置 |",
          "|---:|---|---|---:|---|---|---:|---:|---:|---:|"]
    for i, (code, r) in enumerate(valued_rows, 1):
        L.append(_row(i, code, r))
    for code, r in px_rows:
        L.append(_row("†", code, r, mark=" †"))
    L += ["", "> 子分列已统一成「越高越热」（利差列 = 100 − 利差分位，利差越宽越冷）。"
              "动作列是规则 §4.7 的固定映射，**不是本策略的建议** —— 见 §七 的定位结论。", ""]

    # ---------------- 三
    L += [f"## 三、回测（{rets.index[0]:%Y-%m} ~ {rets.index[-1]:%Y-%m}，与 V0–V8 同窗口）", "",
          "| 方案 | 年化 | 最大回撤 | Calmar | 夏普 | 平均仓位 |",
          "|---|---:|---:|---:|---:|---:|"]
    for name, r in S["sims"].items():
        st = stats(r)
        pos = "100%" if name.startswith(("基准", "横截面：")) else (
            f"{expo:.0%}" if "时间" in name else "—")
        L.append(f"| {name} | {_p(st['cagr'])} | {_p(st['dd'])} | **{_n(st['calmar'])}** | "
                 f"{_n(st['sharpe'])} | {pos} |")
    L += [f"| 对照：绝对档 ≥{rule.ABS_CUT:.0f} 减半 / ≥{rule.ABS_OUT:.0f} 清仓 | "
          f"{_p(st_abs['cagr'])} | {_p(st_abs['dd'])} | {_n(st_abs['calmar'])} | "
          f"{_n(st_abs['sharpe'])} | {float(ctx['m_abs'].mean()):.0%} |",
          "",
          f"- 基准 {_p(st_bench['cagr'])} / {_p(st_bench['dd'])} / Calmar **{_n(st_bench['calmar'])}**。",
          f"- 时间维度触发：板块温度分位 ≥{rule.CUT_Q:.0%} 减半 **{n_cut} 月**、"
          f"≥{rule.OUT_Q:.0%} 清仓 **{n_out} 月**（占 {n_cut / len(temp):.0%}）。",
          f"- 主方案（top2 + 时间减仓）{_p(st_strat['cagr'])} / {_p(st_strat['dd'])} / "
          f"Calmar **{_n(st_strat['calmar'])}**，平均仓位 {expo:.0%}。", ""]

    # ---------------- 四
    L += ["## 四、时间维度：分位阈值 vs 绝对档", "",
          "| 减半/清仓分位 | 年化 | 最大回撤 | Calmar |", "|---|---:|---:|---:|"]
    for k, s in sens.items():
        L.append(f"| {k}% | {_p(s['cagr'])} | {_p(s['dd'])} | {_n(s['calmar'])} |")
    sens_list = list(sens.items())
    L += ["",
          f"- 分位阈值三档（由严到松）Calmar 分别是 "
          + "、".join(f"{k}% → {s['calmar']:.2f}" for k, s in sens_list)
          + f"；基准 {st_bench['calmar']:.2f}。",
          "- **方向是反的**：阈值越严（减仓越多）Calmar 越低、越松（几乎不减）越接近甚至高于基准 —— "
          "说明「板块温度高了就减仓」在本样本里是**负贡献**，不是「择时有效、只是阈值没调好」。"
          f"主方案在 {rule.CUT_Q:.0%}/{rule.OUT_Q:.0%} 上把 {n_cut / len(temp):.0%} 的月份降了暴露，"
          "换来的只是回撤略小、年化更低。",
          "", "绝对温度口径（直接拿板块温度数字当阈值，不转分位）：", "",
          "| 减半/清仓温度 | 年化 | 最大回撤 | Calmar | 平均仓位 | 年化优势 | 剔除贡献最大 3 月后优势 |",
          "|---|---:|---:|---:|---:|---:|---:|"]
    for k, s in ctx["sens_abs"].items():
        L.append(f"| {k} | {_p(s['cagr'])} | {_p(s['dd'])} | {_n(s['calmar'])} | {s['pos']:.0%} | "
                 f"{_pp(s['adv'])} | {_pp(s['adv_d3'])} |")
    best_abs = max(ctx["sens_abs"], key=lambda k: ctx["sens_abs"][k]["calmar"])
    ba = ctx["sens_abs"][best_abs]
    L += ["",
          f"- **绝对口径 4 档全部 ≥ 基准**（Calmar {min(s['calmar'] for s in ctx['sens_abs'].values()):.2f}"
          f"~{max(s['calmar'] for s in ctx['sens_abs'].values()):.2f} vs {st_bench['calmar']:.2f}），"
          f"最好的是 **{best_abs}**（年化 {_p(ba['cagr'])}、回撤 {_p(ba['dd'])}、"
          f"Calmar {ba['calmar']:.2f}、平均仓位 {ba['pos']:.0%}、年化优势 {ba['adv'] * 100:+.1f}pp）。"
          "这与 §五 的三分档单调同向 —— **温度的「水平」有信息**。",
          f"- 但这 4 个阈值是**样本内**选的（80 个月、一轮涨跌），而且剔除「{best_abs} 相对基准贡献最大的 "
          f"3 个月」后，年化优势从 {ba['adv'] * 100:+.1f}pp 掉到 {ba['adv_d3'] * 100:+.1f}pp —— "
          "优势集中在少数几个月，不构成「可以照抄的阈值」。",
          f"- 规则原意的 ≥{rule.ABS_CUT:.0f}/≥{rule.ABS_OUT:.0f} 在最后一行：Calmar "
          f"{st_abs['calmar']:.2f}、平均仓位 {float(ctx['m_abs'].mean()):.0%} —— "
          "**几乎从不触发**（板块均温很少到 65），「没伤害」只是「什么都没做」。",
          "- 结论：**分位口径无效**（越松越好，0.92~1.19 且跨基准）；**绝对口径看着有效但经不起剔除检验**。"
          "两者都达不到「可以拿来管仓位」的标准，故 `TIME_ENABLED = False`。", ""]

    # ---------------- 五
    cs = ctx["cs"]
    L += ["## 五、横截面能否选品", "",
          "| 方案 | 年化 | 最大回撤 | Calmar |", "|---|---:|---:|---:|"]
    for k, s in ctx["xs"].items():
        L.append(f"| {k} | {_p(s['cagr'])} | {_p(s['dd'])} | {_n(s['calmar'])} |")
    L += ["", "横截面 IC（月度秩相关，只在有估值标的之间、≥3 只才计）：", "",
          "| 期限 | 平均 IC | 期数 |", "|---|---:|---:|"]
    for h in (1, 3, 6):
        v, n = cs[h]
        L.append(f"| {h}M | {_n(v)} | {n} |")
    L += ["", "温度三分档的平均未来 6 月收益（全样本 pooled）：", "",
          "| 档位 | 平均 6M 收益 | 样本数 |", "|---|---:|---:|"]
    for k in ("低温", "中温", "高温"):
        if k in ctx["tri"]:
            L.append(f"| {k} | {ctx['tri'][k][0] * 100:+.1f}% | {ctx['tri'][k][1]} |")
    L.append("")

    # ---------------- 六
    L += ["## 六、逐年与稳健性（主方案 vs 基准）", "",
          "| 年份 | 基准 | 主方案 | 差 | 主方案平均仓位 |", "|---|---:|---:|---:|---:|"]
    for yr, r in ctx["yearly"].iterrows():
        L.append(f"| {int(yr)} | {r['bench'] * 100:+.1f}% | {r['strat'] * 100:+.1f}% | "
                 f"{(r['strat'] - r['bench']) * 100:+.1f}pp | {r['expo'] * 100:.0f}% |")
    L += ["", "剔除「主方案相对基准贡献最大」的 N 个月后还剩多少：", "",
          "| 剔除 | 基准年化 | 主方案年化 | 优势 |", "|---|---:|---:|---:|"]
    for k, (b, s) in ctx["robust"].items():
        L.append(f"| 贡献最大的 {k} 个月 | {b * 100:.1f}% | {s * 100:.1f}% | {(s - b) * 100:+.1f}pp |")
    L.append("")

    # ---------------- 七
    ic = pd.DataFrame(ctx["ic"])
    ic6 = ic[ic["h"] == 6].sort_values("ic")
    ic12 = ic[ic["h"] == 12].sort_values("ic")
    best_cs = max(ctx["xs"], key=lambda k: ctx["xs"][k]["calmar"])
    worst_cs = min(ctx["xs"], key=lambda k: ctx["xs"][k]["calmar"])
    d_year = {int(y): (r["strat"] - r["bench"]) * 100 for y, r in ctx["yearly"].iterrows()}
    pos_years = "、".join(f"{y} {v:+.1f}pp" for y, v in d_year.items() if v > 0) or "无"
    neg_years = "、".join(f"{y} {v:+.1f}pp" for y, v in d_year.items() if v <= 0) or "无"
    icd = pd.DataFrame(ctx["ic"])
    ic_mean = {h: float(icd.loc[icd["h"] == h, "ic"].mean()) for h in (1, 3, 6, 12)}
    n_neg = {h: int((icd.loc[icd["h"] == h, "ic"] < 0).sum()) for h in (1, 3, 6, 12)}
    n_all = {h: int((icd["h"] == h).sum()) for h in (1, 3, 6, 12)}
    L += ["## 七、读法（结论）", "",
          f"1. **「五因子」实为四维，PE 实际占 {(rule.W['pe'] + rule.W['pb']) * 100:.0f}%**"
          f"（PB 分位 ≡ PE 分位，实测差 {ctx['pb_gap']:.6f}）。要 PB 独立必须补指数 PB 历史；"
          "在拿到之前，这套温度实质是「PE 36% + 利差 35% + 位置类 29%」，比 V2 的四维等权**更偏 PE**。"
          "去 PB、四维归一的对照在同一套规则下 Calmar 从 "
          f"{stats(S['sims']['横截面 top2 + 时间减仓'])['calmar']:.2f} 变成 "
          f"{stats(S4['sims']['横截面 top2 + 时间减仓'])['calmar']:.2f} —— "
          "差别只来自 PE 权重 36% vs 23%，不是「多了一个维度」。",
          f"2. **时间序列 IC 方向对、幅度弱。** 温度 vs 未来 1/3/6/12 月的平均 IC 为 "
          f"{ic_mean[1]:+.2f} / {ic_mean[3]:+.2f} / {ic_mean[6]:+.2f} / {ic_mean[12]:+.2f}"
          f"（负 = 温度高则未来收益低，方向符合预期；为负的只数 "
          f"{n_neg[1]}/{n_all[1]}、{n_neg[3]}/{n_all[3]}、{n_neg[6]}/{n_all[6]}、"
          f"{n_neg[12]}/{n_all[12]}）。"
          f"6 月窗口两端是 `{ic6.iloc[0]['name']}` {ic6.iloc[0]['ic']:+.2f} 与 "
          f"`{ic6.iloc[-1]['name']}` {ic6.iloc[-1]['ic']:+.2f}。"
          "**IC 为负 ≠ 可以拿来择时**：IC 说的是横截面上「贵的那只后面涨得少」，"
          "而组合层要靠「什么时候整体降暴露」赚钱，这是两件事（见第 3、4 条）。",
          f"3. **横截面选品：无效，且明显负贡献。** 横截面 IC "
          f"{_n(cs[1][0])}（1M）/ {_n(cs[3][0])}（3M）/ {_n(cs[6][0])}（6M）≈ 0；"
          f"top1/top2/top3 的 Calmar 是 {ctx['xs']['top1']['calmar']:.2f}/"
          f"{ctx['xs']['top2']['calmar']:.2f}/{ctx['xs']['top3']['calmar']:.2f}，"
          f"全部低于基准 {st_bench['calmar']:.2f}（最好 {best_cs}、最差 {worst_cs}）。"
          "8 只红利指数月收益高度相关，「同一板块里谁更便宜」换不来收益 —— 与 V2 结论一致。",
          f"4. **时间维度：分位口径无效、绝对口径经不起剔除检验。** 分位 "
          f"{rule.CUT_Q:.0%}/{rule.OUT_Q:.0%} 把 Calmar 从 {st_bench['calmar']:.2f} 变成 "
          f"{stats(S['sims']['时间：板块温度分位减仓'])['calmar']:.2f}（年化 "
          f"{_p(st_bench['cagr'])} → {_p(stats(S['sims']['时间：板块温度分位减仓'])['cagr'])}，"
          f"回撤 {_p(st_bench['dd'])} → {_p(stats(S['sims']['时间：板块温度分位减仓'])['dd'])}），"
          f"三档阈值 {min(s['calmar'] for s in sens.values()):.2f}~"
          f"{max(s['calmar'] for s in sens.values()):.2f} 且**越严越差** —— 分位化把温度变成了"
          "「常年贴在高位」（利率下行把利差维度顶死），减仓等于长期低配。"
          f"绝对口径 4 档反而全部 ≥ 基准（最好 {best_abs}：Calmar {ba['calmar']:.2f}、"
          f"仓位 {ba['pos']:.0%}、年化优势 {ba['adv'] * 100:+.1f}pp），但剔除贡献最大的 3 个月后"
          f"优势只剩 {ba['adv_d3'] * 100:+.1f}pp —— 集中在少数几个月、阈值又是样本内选的，不能照抄。"
          f"主方案（top2 + 时间）{_p(st_strat['cagr'])} / {_p(st_strat['dd'])} / "
          f"Calmar {st_strat['calmar']:.2f}，平均仓位 {expo:.0%} —— "
          f"砍掉 {(1 - expo) * 100:.0f}% 暴露只换来回撤 {(st_bench['dd'] - st_strat['dd']) * 100:.1f}pp 的改善、"
          f"年化让渡 {(st_bench['cagr'] - st_strat['cagr']) * 100:.1f}pp；逐年 {pos_years} 为正、"
          f"{neg_years} 为负；剔除贡献最大的 3 个月后优势 "
          f"{(ctx['robust'][3][1] - ctx['robust'][3][0]) * 100:+.1f}pp/年。",
          "5. **定位：这是一张「贵不贵」的体检表，不是择时器、也不是选品器。** "
          "`rule.XS_ENABLED = False`、`rule.TIME_ENABLED = False` 就是本回测的结论落地 —— "
          "每日只输出「逐只温度 + 档位 + 规则字面动作」，不输出仓位倍率、不做横截面推荐。"
          "要控回撤请走 V4（RSI14，同池 Calmar 1.44），要选品请走 V0 打分；"
          "V9 与它们不同源、不可互相替代。",
          "- 已知失效模式：①利率单边下行期利差分位贴顶、方差极小（V1/V5/V6/V7 同源问题）；"
          "②PB 不可得使估值维度实际只有 PE 一个源；③温度经验分布不是 0–100 均匀，"
          "绝对档位只能当标签、不能当跨期阈值；④两个标普系列无估值，只能算价格温度。",
          "",
          f"> 边界：{len(rets)} 个月、单边 0.03%、未扣跟踪误差与场外申赎费；"
          "样本只覆盖一轮涨跌，结论只作方向性参考。", ""]
    return "\n".join(L)


if __name__ == "__main__":
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    sys.exit(main())
