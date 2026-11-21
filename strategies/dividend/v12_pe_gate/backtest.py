#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""V12 · PE 分位闸门 × V4 RSI14 —— 回测与报告生成。

做法（与 V4 只差一个变量）
--------------------------
基准 V4：横截面 RSI14 最低 2 只等权 × 板块 RSI 均值三档（≥0.70 清仓 / ≥0.50 减半）。
V12 只加一道闸门：

  per_target（主方案）: 被选中的标的若自身 PE 分位 ≥ PE_CUT → 该只仓位 × GATE_MULT
  board（对照）      : 池内 PE 分位中位数 ≥ PE_CUT → 整体仓位倍率压到 GATE_MULT
                         （不清仓；清仓仍归 RSI ≥0.70）

PE 分位 = 中证官网 peg 月末值的逐只扩张分位（≥36 月、≤120 月，含当前点），
按 as-of 前值填充到净值月末（peg 更新晚于净值时沿用上月末，不丢信号）。
无 peg 的 008163 / 005125、以及观测不足 36 月的早期月份 → 闸门倍率 1.0（不设闸门），
报告里给出覆盖只数与覆盖率，避免把「没覆盖」读成「覆盖了但没触发」。

稳健性用**月度超额**做（不是重算净值水平）：平均月超额、t 值、剔除超额最大的 3 个月后
还剩多少 —— 剔除净值水平最高的月份会把「V12 少亏的月份」一起剔掉，读不出结论。

产物::

    backtest/_v12_report.md    报告
    backtest/_v12_bt.json      主表 + 网格 + 诊断

用法::

    python strategies/dividend/v12_pe_gate/backtest.py
"""

from __future__ import annotations

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

import _single_signal as S  # noqa: E402  pylint: disable=wrong-import-position
import pool  # noqa: E402  pylint: disable=wrong-import-position
import rule  # noqa: E402  pylint: disable=wrong-import-position

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

MAIN_CUT = rule.PE_CUT
MAIN_MULT = rule.GATE_MULT


# ------------------------------------------------------------------ 数据
def pe_monthly() -> pd.DataFrame:
    """{基金: 月末 PE} 宽表（只有有 peg 历史的标的）。"""
    peg = pool.load_peg()
    out = {}
    for r in rule.POOL:
        code, idx = r["code"], r.get("csi")
        if not idx or idx not in peg.columns:
            continue
        s = peg[idx].dropna().resample("ME").last().dropna()
        if len(s):
            out[code] = s
    return pd.DataFrame(out).sort_index()


def expanding_pct(s: pd.Series, min_n=rule.PCT_MIN_N, max_n=rule.PCT_MAX_N) -> pd.Series:
    """逐列扩张窗口分位（含当前点），样本不足 min_n → NaN。"""
    v = s.to_numpy(dtype=float)
    r = np.full(len(v), np.nan)
    for i in range(len(v)):
        w = v[max(0, i - max_n + 1):i + 1]
        w = w[~np.isnan(w)]
        if len(w) >= min_n and not np.isnan(v[i]):
            r[i] = float((w < v[i]).mean())
    return pd.Series(r, index=s.index)


def pe_pct(rets: pd.DataFrame) -> pd.DataFrame:
    """PE 分位宽表，索引与月度收益对齐（as-of 前值填充）。"""
    pe = pe_monthly()
    pct = pd.DataFrame({c: expanding_pct(pe[c]) for c in pe.columns})
    pct = pct.reindex(pct.index.union(rets.index)).ffill().reindex(rets.index)
    return pct


def load(navs=None):
    navs = pool.load_navs(codes=rule.CODES) if navs is None else navs
    rets = S.load_rets(navs)
    rsi = S.load_signal("rsi", navs).reindex(rets.index)
    pct = pe_pct(rets)
    return navs, rets, rsi, pct


# ------------------------------------------------------------------ 权重与闸门
def base_parts(rets: pd.DataFrame, rsi: pd.DataFrame):
    """V4 骨架：贵度、可得掩码、等权、板块 RSI 倍率、横截面 top2 权重。"""
    q = (rsi / 100.0).loc[S.START:]
    xs = rsi.loc[S.START:]
    ok = S.available(q, rets)
    base_w = S.equal_w(ok)
    board_rsi = S.board_q(q, ok)
    mult = S.time_mult(board_rsi, rule.Q_OUT, rule.Q_HALF)
    top = S.top_w(xs, ok, rule.TOP_K)
    return q, ok, base_w, board_rsi, mult, top


def gate_per_target(top: pd.DataFrame, pct: pd.DataFrame, cut: float, gm: float) -> pd.DataFrame:
    """标的级闸门：被选中且 PE 分位 ≥ cut → 该只权重 × gm；无数据 → 1.0。"""
    g = pd.DataFrame(1.0, index=pct.index, columns=pct.columns)
    g[pct >= cut] = gm
    g = g.reindex(index=top.index, columns=top.columns).fillna(1.0)
    return top * g


def gate_board(mult: pd.Series, pct: pd.DataFrame, cut: float, gm: float) -> pd.Series:
    """板块级闸门：PE 分位中位数 ≥ cut → 倍率压到 min(当前, gm)；无数据 → 不动。"""
    bpe = pct.median(axis=1, skipna=True).reindex(mult.index)
    hot = bpe >= cut
    out = mult.copy()
    out[hot & (out > gm)] = gm
    return out


def weights(rets: pd.DataFrame, rsi: pd.DataFrame, pct: pd.DataFrame,
            cut=None, gm=None) -> dict:
    """各方案的「月末目标权重」矩阵（simulate 内部再 shift(1)）。"""
    cut = MAIN_CUT if cut is None else cut
    gm = MAIN_MULT if gm is None else gm
    _, _, base_w, _, mult, top = base_parts(rets, rsi)
    return {
        f"基准：{rets.shape[1]} 只等权": base_w,
        "V4 时间：板块 RSI 择时": base_w.mul(mult, axis=0),
        f"V4 横截面 top{rule.TOP_K}": top,
        f"V4 top{rule.TOP_K} + 时间择时": top.mul(mult, axis=0),
        f"V12 标的级闸门（PE≥{cut:.0%} → ×{gm:g}）":
            gate_per_target(top, pct, cut, gm).mul(mult, axis=0),
        f"V12 板块级闸门（PE≥{cut:.0%} → ×{gm:g}）":
            top.mul(gate_board(mult, pct, cut, gm), axis=0),
    }


def simulate_all(wfs: dict, rets: pd.DataFrame) -> dict:
    return {n: S.simulate(w, rets) for n, w in wfs.items()}


# ------------------------------------------------------------------ 诊断
def diagnostics(top: pd.DataFrame, pct: pd.DataFrame, cut=MAIN_CUT) -> dict:
    """闸门触发统计 + 覆盖率。"""
    hit = (pct >= cut) & top.notna() & (top > 0)
    covered = pct.notna() & top.notna() & (top > 0)
    n_sel = int((top > 0).sum().sum())
    n_cov = int(covered.sum().sum())
    board_hot = pct.median(axis=1, skipna=True) >= cut
    return {
        "n_months": len(top),
        "n_positions": n_sel,
        "n_positions_pe": n_cov,
        "n_hit": int(hit.sum().sum()),
        "hit_rate_of_covered": float(hit.sum().sum()) / n_cov if n_cov else float("nan"),
        "coverage_by_code": {c: float(pct[c].notna().mean()) for c in pct.columns},
        "board_hot_months": int(board_hot.sum()),
        "board_hot_months_list": [f"{t:%Y-%m}" for t in board_hot[board_hot].index],
        "cut": cut,
    }


def excess_stats(r: pd.Series, ref: pd.Series) -> dict:
    """月度超额（pp/月）的均值、t 值，以及剔除超额最大 3 个月后的均值/t。"""
    d = (r - ref).dropna()
    if len(d) < 6:
        return {}
    t = float(d.mean() / (d.std() / np.sqrt(len(d)))) if d.std() else float("nan")
    drop = d.nlargest(3).index
    d2 = d.drop(drop)
    t2 = float(d2.mean() / (d2.std() / np.sqrt(len(d2)))) if d2.std() and len(d2) > 3 else float("nan")
    return {"n": len(d), "mean_pp": float(d.mean() * 100), "t": t,
            "mean_pp_ex3": float(d2.mean() * 100), "t_ex3": t2,
            "top3": [(f"{ts:%Y-%m}", float(v * 100)) for ts, v in d.nlargest(3).items()]}


def subsample_codes(pct: pd.DataFrame) -> list[str]:
    cov = pct.notna().mean()
    return [c for c in rule.CODES if c in cov.index and cov[c] >= 0.8]


# ------------------------------------------------------------------ 报告
def _pick(d: dict, prefix: str, default=float("nan")):
    """按键前缀取值（方案名里带门槛/压档，不同池子字符串一致但这里仍按前缀稳健取）。"""
    for k, v in d.items():
        if k.startswith(prefix):
            return v
    return default


def render(sims: dict, wfs: dict, rets: pd.DataFrame, diag: dict, pct: pd.DataFrame,
           grid: dict, ex: dict, subs: dict, sub_codes: list[str],
           yearly_df: pd.DataFrame = None, pe_asof=None) -> str:
    base_key = S.base_key(sims)
    v4 = next(n for n in sims if n.startswith("V4 top"))
    v12 = next(n for n in sims if n.startswith("V12 标的级"))
    v12b = next(n for n in sims if n.startswith("V12 板块级"))
    s4, s12 = S.stats(sims[v4]), S.stats(sims[v12])

    L = [f"# {rule.TITLE}", ""]
    L += [f"> {s}" for s in rule.SUBTITLE]
    L += ["", f"> 生成 {datetime.now():%Y-%m-%d %H:%M} · 池内 {len(rule.CODES)} 只 · "
              f"PE 口径覆盖 {len(rule.PE_CODES)} 只"
              f"（无 peg: {'、'.join(rule.NO_PE_CODES) or '无'}）", ""]

    # 一、当前读数
    last_pct = pct.dropna(how="all").index[-1] if len(pct.dropna(how="all")) else rets.index[-1]
    pe_last = pe_monthly()
    rsi_last = S.load_signal("rsi", pool.load_navs(codes=rule.CODES)).reindex(rets.index)
    asof_txt = f"{pe_asof:%Y-%m}" if pe_asof is not None else f"{last_pct:%Y-%m}"
    L += [f"## 一、当前读数（PE 原始数据截至 {asof_txt}，净值最新 {rets.index[-1]:%Y-%m}）", "",
          "| 标的 | PE | PE 分位 | RSI14（最新月） | 闸门倍率 |", "|---|---:|---:|---:|---:|"]
    for c in rule.CODES:
        pe = pe_last[c].iloc[-1] if c in pe_last.columns else float("nan")
        p = pct[c].get(last_pct, float("nan")) if c in pct.columns else float("nan")
        r = rsi_last[c].iloc[-1] if c in rsi_last.columns else float("nan")
        g = MAIN_MULT if (p == p and p >= MAIN_CUT) else 1.0
        L.append(f"| {rule.NAME[c]}({c}) | {'—' if pe != pe else f'{pe:.2f}'} | "
                 f"{'—（无 peg/不足 36 月）' if p != p else f'{p:.0%}'} | "
                 f"{'—' if r != r else f'{r:.1f}'} | ×{g:g} |")
    L += ["", f"> 闸门只看**自身 PE 分位**：≥{MAIN_CUT:.0%} 的标的即使被 RSI 选出，仓位也压到 "
              f"×{MAIN_MULT:g}；无 peg 的标的（{'、'.join(rule.NO_PE_CODES) or '无'}）不设闸门。", ""]

    # 二、回测主表
    L += [f"## 二、回测主表（{rets.index[0]:%Y-%m} ~ {rets.index[-1]:%Y-%m}，"
          f"{len(rets)} 月，成本单边 {rule.FEE:.2%}）", "",
          "| 方案 | 年化 | 最大回撤 | Calmar | 波动 | 夏普 | 累计 | 平均仓位 |",
          "|---|---:|---:|---:|---:|---:|---:|---:|"]
    for name, r in sims.items():
        st = S.stats(r)
        pos = float(wfs[name].sum(axis=1).mean())
        L.append(f"| {name} | {S.fmt_pct(st['cagr'])} | {S.fmt_pct(st['dd'])} | "
                 f"{st['calmar']:.2f} | {S.fmt_pct(st['vol'])} | {st['sharpe']:.2f} | "
                 f"{S.fmt_pct(st['total'], 0)} | {pos:.0%} |")
    L += ["", f"- V12（标的级）相对 V4：Calmar **{s12['calmar'] - s4['calmar']:+.2f}**、"
              f"年化 {S.fmt_pct(s12['cagr'] - s4['cagr'])}、回撤 {S.fmt_pct(s12['dd'] - s4['dd'])}。"]
    delev = next((n for n in sims if n.startswith("对照：V4 × 常数去杠杆")), None)
    if delev:
        sdl = S.stats(sims[delev])
        L.append(f"- **等暴露对照**：把 V4 整体乘常数、使平均仓位与 V12 相同 → "
                 f"Calmar {sdl['calmar']:.2f} / 年化 {S.fmt_pct(sdl['cagr'])} / "
                 f"回撤 {S.fmt_pct(sdl['dd'])}。V12 若赢不过它，说明闸门只是「少拿一点仓位」，"
                 "与 PE 判别力无关。")
    L.append("")

    # 三、闸门诊断
    L += ["## 三、闸门诊断（它到底触发了几次）", "",
          f"- 决策月 {diag['n_months']} 个，标的·月 持仓位 {diag['n_positions']} 个；"
          f"其中 **PE 有数据 {diag['n_positions_pe']} 个**"
          f"（覆盖率 {diag['n_positions_pe'] / max(diag['n_positions'], 1):.0%}）",
          f"- 被闸门压深的 **{diag['n_hit']}** 个标的·月 —— 占「PE 有数据」的 "
          f"{diag['hit_rate_of_covered']:.0%}",
          f"- 板块级口径下 PE 分位中位数 ≥{MAIN_CUT:.0%} 的月份 **{diag['board_hot_months']}** 个"
          + (f"：{'、'.join(diag['board_hot_months_list'])}" if diag["board_hot_months"] else ""),
          "", "| 标的 | PE 分位覆盖率 |", "|---|---:|"]
    for c, v in diag["coverage_by_code"].items():
        L.append(f"| {rule.NAME.get(c, c)}({c}) | {v:.0%} |")
    L += ["", "> 覆盖率 = 样本月里该标的 PE 分位非空的月份占比；无 peg 的 2 只不在表内（恒 ×1.0）。", ""]

    # 四、阈值网格
    L += ["## 四、阈值网格（不挑单点）", "",
          "| 口径 | 门槛 | 压档 | 年化 | 回撤 | Calmar |", "|---|---:|---:|---:|---:|---:|"]
    for kind, rows in grid.items():
        for (cut, gm), st in rows.items():
            L.append(f"| {kind} | {cut:.0%} | ×{gm:g} | {S.fmt_pct(st['cagr'])} | "
                     f"{S.fmt_pct(st['dd'])} | {st['calmar']:.2f} |")
    L += ["", f"- 参照：V4 Calmar {s4['calmar']:.2f} / 年化 {S.fmt_pct(s4['cagr'])}；"
              f"等权基准 Calmar {S.stats(sims[base_key])['calmar']:.2f}。", ""]

    # 五、超额稳健性
    L += ["## 五、超额稳健性（月度超额，相对 V4）", "",
          "| 方案 | 月数 | 平均月超额 | t 值 | 剔除最大 3 月后 | t |",
          "|---|---:|---:|---:|---:|---:|"]
    for name in (v12, v12b):
        e = ex[name]
        L.append(f"| {name} | {e['n']} | {e['mean_pp']:+.2f}pp | {e['t']:+.2f} | "
                 f"{e['mean_pp_ex3']:+.2f}pp | {e['t_ex3']:+.2f} |")
    L += ["",
          "- |t| < 2 视为「无法与零区分」。剔除最大 3 个月后仍同号且量级接近，才算不是运气。",
          "- 超额最大的 3 个月：" + "、".join(f"{t} {v:+.1f}pp" for t, v in ex[v12]["top3"]), ""]

    # 五之二、分年度：闸门从哪一年开始起作用
    if yearly_df is not None and len(yearly_df):
        L += ["## 六、分年度（闸门从哪一年开始起作用）", "",
              "| 年份 | " + " | ".join(yearly_df.columns) + " |",
              "|---|" + "---:|" * len(yearly_df.columns)]
        for ts, row in yearly_df.iterrows():
            L.append(f"| {int(ts)} | " + " | ".join(S.fmt_pct(v) for v in row.values) + " |")
        L += ["", "> 板块 PE 分位中位数 ≥60% 的月份集中在 2024-12 之后 —— 也就是说闸门"
                  "只在这最后两年起作用，任何「变好/变差」都只能归结到这一段。", ""]

    # 七、子样本
    dsub = float("nan")
    if subs:
        c_sub_v4 = _pick(subs, f"V4 top{rule.TOP_K}").get("calmar", float("nan"))
        c_sub_v12 = _pick(subs, "V12 标的级").get("calmar", float("nan"))
        dsub = c_sub_v12 - c_sub_v4
        L += [f"## 七、子样本：PE 覆盖 ≥80% 的 {len(sub_codes)} 只", "",
              f"只保留 {'、'.join(sub_codes)}（其余标的 PE 分位多为空，闸门实际没作用，"
              "会把结论稀释）。**注意这是子集，不是样本外**。", "",
              "| 方案 | 年化 | 最大回撤 | Calmar | 平均仓位 |", "|---|---:|---:|---:|---:|"]
        for name, st in subs.items():
            L.append(f"| {name} | {S.fmt_pct(st['cagr'])} | {S.fmt_pct(st['dd'])} | "
                     f"{st['calmar']:.2f} | {st.get('pos', float('nan')):.0%} |")
        L += ["", f"- 子样本里 V12 相对 V4 的 Calmar 差：**{dsub:+.2f}**。"
                  "回撤变浅比年化更明显是正常的——闸门就是缩仓位，天然控回撤；"
                  "但仓位低本身也会降回撤，所以「回撤更浅」不等于「PE 有判别力」。"]
        sub_dl = _pick(subs, "对照：V4 × 常数去杠杆", None)
        if sub_dl:
            L.append(f"- 子样本里的等暴露对照（V4 × 常数、同均仓）Calmar "
                     f"{sub_dl['calmar']:.2f} vs V12 {c_sub_v12:.2f}："
                     f"{'V12 在这个子集里**优于**单纯降仓位' if c_sub_v12 > sub_dl['calmar'] else 'V12 **不优于**单纯降仓位'}；"
                     f"而全样本里是反过来的（V12 {s12['calmar']:.2f} vs "
                     f"{S.stats(sims[delev])['calmar']:.2f}）。两个方向不一致 → 只能算线索，不能当结论。")
        L.append("")

    # 八、读法
    L += ["## 八、读法", ""]
    if s12["calmar"] > s4["calmar"] + 0.05:
        L.append(f"- 主方案 Calmar **{s12['calmar']:.2f} vs V4 {s4['calmar']:.2f}** 表面更优；"
                 f"但看第五节：平均月超额 {ex[v12]['mean_pp']:+.2f}pp、t={ex[v12]['t']:+.2f}"
                 "—— 先判断这个 t 能不能站住，再看第七节子样本是否同向。")
    else:
        L.append(f"- **主方案没有增量**：Calmar {s12['calmar']:.2f} 对 V4 {s4['calmar']:.2f}、"
                 f"板块级 {S.stats(sims[v12b])['calmar']:.2f}；平均月超额 "
                 f"{ex[v12]['mean_pp']:+.2f}pp（t={ex[v12]['t']:+.2f}），"
                 f"剔除最大 3 个月后 {ex[v12]['mean_pp_ex3']:+.2f}pp。")
        L.append("- 机制上说得通：PE 分位是慢变量、时序 IC 只有 −0.05~−0.15，"
                 "用它压仓位 = 随机少赚一点；它压不下去的月份刚好是「贵但继续涨」的那些。")
    if delev:
        sdl = S.stats(sims[delev])
        L.append(f"- 等暴露对照 Calmar {sdl['calmar']:.2f} vs V12 {s12['calmar']:.2f}："
                 + ("**V12 赢不过单纯降仓位**，闸门没有带来额外信息。"
                    if s12["calmar"] <= sdl["calmar"] + 0.02 else
                    "V12 略优于单纯降仓位，方向上还有一点信息量（需更多样本确认）。"))
    if subs:
        L.append(f"- 子样本里闸门的方向是 {'同向（可以继续查）' if dsub > 0 else '反向（支撑结案）'}"
                 f"（ΔCalmar {dsub:+.2f}）。子样本只有 {len(sub_codes)} 只、且是事后按覆盖率筛的，"
                 "不构成证据。")
    L += [f"- 与 V11 的关系：V11 用 V9 六维温度（利差占 35%）当闸门、已否决；V12 只测 PE 单因子，"
          "两者合起来说明「估值类闸门 × RSI 择时」在红利样本里没有可用的形态。",
          f"- {rule.NOTE}", ""]
    return "\n".join(L)


# ------------------------------------------------------------------ 主流程
def main() -> int:
    navs, rets, rsi, pct = load()
    wfs = weights(rets, rsi, pct)
    sims = simulate_all(wfs, rets)
    _, _, _, _, mult, top = base_parts(rets, rsi)
    diag = diagnostics(top, pct)

    # 网格
    grid = {"标的级": {}, "板块级": {}}
    for cut in rule.GRID_CUT:
        for gm in rule.GRID_MULT:
            w = weights(rets, rsi, pct, cut, gm)
            key_pt = f"V12 标的级闸门（PE≥{cut:.0%} → ×{gm:g}）"
            key_bd = f"V12 板块级闸门（PE≥{cut:.0%} → ×{gm:g}）"
            grid["标的级"][(cut, gm)] = S.stats(S.simulate(w[key_pt], rets))
            grid["板块级"][(cut, gm)] = S.stats(S.simulate(w[key_bd], rets))

    v4 = next(n for n in sims if n.startswith("V4 top"))
    v12 = next(n for n in sims if n.startswith("V12 标的级"))
    v12b = next(n for n in sims if n.startswith("V12 板块级"))

    # 等暴露对照：把 V4 的仓位整体乘一个常数，使其平均仓位与 V12 相同。
    # 若「V4 常数去杠杆」的 Calmar 就能达到 V12 的水平，说明闸门只是降仓位、没有判别力。
    k = float(wfs[v12].sum(axis=1).mean() / wfs[v4].sum(axis=1).mean())
    delev_name = f"对照：V4 × 常数去杠杆（×{k:.2f}，同均仓）"
    sims[delev_name] = S.simulate(wfs[v4] * k, rets)
    wfs[delev_name] = wfs[v4] * k

    ex = {v12: excess_stats(sims[v12], sims[v4]),
          v12b: excess_stats(sims[v12b], sims[v4]),
          delev_name: excess_stats(sims[delev_name], sims[v4])}

    # 子样本：PE 覆盖率 ≥80% 的标的
    sub_codes = subsample_codes(pct)
    subs = {}
    if len(sub_codes) >= 3:
        _, sub_rets, sub_rsi, sub_pct = load(pool.load_navs(codes=sub_codes))
        sub_wfs = weights(sub_rets, sub_rsi, sub_pct)
        for name in (f"基准：{len(sub_codes)} 只等权", f"V4 top{rule.TOP_K} + 时间择时", v12):
            if name in sub_wfs:
                st = S.stats(S.simulate(sub_wfs[name], sub_rets))
                st["pos"] = float(sub_wfs[name].sum(axis=1).mean())
                subs[name] = st
        if v12 in sub_wfs and f"V4 top{rule.TOP_K} + 时间择时" in sub_wfs:
            ks = float(sub_wfs[v12].sum(axis=1).mean()
                       / sub_wfs[f"V4 top{rule.TOP_K} + 时间择时"].sum(axis=1).mean())
            st = S.stats(S.simulate(sub_wfs[f"V4 top{rule.TOP_K} + 时间择时"] * ks, sub_rets))
            st["pos"] = float(sub_wfs[v12].sum(axis=1).mean())
            subs[f"对照：V4 × 常数去杠杆（×{ks:.2f}）"] = st

    yearly_df = S.yearly({v4: sims[v4], v12: sims[v12], v12b: sims[v12b]})
    pe_asof = pe_monthly().index[-1] if len(pe_monthly()) else None
    text = render(sims, wfs, rets, diag, pct, grid, ex, subs, sub_codes,
                  yearly_df=yearly_df, pe_asof=pe_asof)
    S.write(rule.OUT_MD, text)

    payload = {
        "generated_at": datetime.now().strftime("%Y-%m-%d %H:%M"),
        "sample": [str(rets.index[0].date()), str(rets.index[-1].date())],
        "params": {"q_out": rule.Q_OUT, "q_half": rule.Q_HALF, "top_k": rule.TOP_K,
                   "pe_cut": MAIN_CUT, "gate_mult": MAIN_MULT, "fee": rule.FEE,
                   "pct_min_n": rule.PCT_MIN_N, "pct_max_n": rule.PCT_MAX_N},
        "rows": {n: {k: v for k, v in S.stats(r).items()} for n, r in sims.items()},
        "diagnostics": diag,
        "grid": {k: {f"{c:.2f}/{g:g}": st for (c, g), st in rows.items()}
                 for k, rows in grid.items()},
        "excess_vs_v4": ex,
        "subsample": {"codes": sub_codes, "rows": subs},
    }
    S.write(rule.OUT_JSON, json.dumps(payload, ensure_ascii=False, indent=2, default=str))

    print(f"[v12] 样本 {rets.index[0]:%Y-%m}~{rets.index[-1]:%Y-%m} ({len(rets)} 月) · "
          f"PE 覆盖 {diag['n_positions_pe']}/{diag['n_positions']} 持仓位 · "
          f"闸门命中 {diag['n_hit']} · 子样本 {len(sub_codes)} 只")
    for n, r in sims.items():
        st = S.stats(r)
        print(f"[v12] {n:44s} 年化 {st['cagr'] * 100:5.1f}%  回撤 {st['dd'] * 100:6.1f}%  "
              f"Calmar {st['calmar']:5.2f}")
    for n, e in ex.items():
        print(f"[v12] 超额 {n[:20]:20s} {e['mean_pp']:+.2f}pp/月 t={e['t']:+.2f} "
              f"| 剔除3月 {e['mean_pp_ex3']:+.2f}pp t={e['t_ex3']:+.2f}")
    print(f"[v12] 产物 -> {os.path.relpath(rule.OUT_MD, _STRAT)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
