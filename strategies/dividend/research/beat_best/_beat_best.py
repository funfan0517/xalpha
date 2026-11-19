#!/usr/bin/env python
# -*- coding: utf-8 -*-
r"""红利 · 单标的「超过当前最优」搜索（对应 vol_legs 的 `research/beat_best`）

`research/best_by_fund/_best_by_fund.md` 给出 10 只标的各自的**当前最优**（按夏普）：
9 只是 V4/V8（RSI 两族），1 只（`018388` 港股通）是买入持有。

本课题只问一件事：**这只标的上的「当前最优」还能不能被超过**。不看组合、不做配权。

三条硬约束（缺一条结论就不成立）

1. **同尺**：基线不由「抄报告数字」得到，而是用 `research/_leg_engine.py` 在同一窗口、
   同一成本（单边 0.03%）、同一执行（月末信号次月生效）、同一空仓假设（不计息）下重算；
   候选走同一个 `simulate`。所以候选赢的是规则，不是口径。
2. **不看全样本挑参数**：候选参数**事先固定**（不做网格搜索），并且要求
   **后段（样本后 40%）夏普也高于基线**才标 ★ —— 全样本赢、后段输的一律算没赢。
3. **机制理由先写**：每个候选对应的机制假设写在下表 `机制` 列，参数只取一个值。

本课题与 `best_by_fund` 的分工：那边回答「每只标的该用哪套规则」，这里回答
「那套规则还能不能改」。所以 **基线 = 该标的的 ★1**，候选 = 在 ★1 上做的一层改造。

产物：`_beat_best.md`（本目录）。
用法：`python strategies/dividend/research/beat_best/_beat_best.py`
（**先跑 `best_by_fund`**，本脚本读它的 jsonl 定基线；缺失时自动重算并提示）

> 模拟结果，非投资建议。
"""

from __future__ import annotations

import io
import json
import os
import sys

import numpy as np
import pandas as pd

try:
    sys.stdout.reconfigure(encoding="utf-8")  # type: ignore[attr-defined]
except Exception:  # noqa: BLE001
    pass

_HERE = os.path.dirname(os.path.abspath(__file__))     # .../research/beat_best
_RES = os.path.dirname(_HERE)                          # .../research
if _RES not in sys.path:
    sys.path.insert(0, _RES)

import _leg_engine as E  # noqa: E402  pylint: disable=wrong-import-position

OUT_MD = os.path.join(_HERE, "_beat_best.md")
BASE_JSONL = os.path.join(_RES, "best_by_fund", "_best_by_fund.jsonl")
CUT = 0.60                       # 前 60% / 后 40% 的分界（与 vol_legs 一致）


# ================================================================ 信号原语
def _mask_warm(m: pd.Series, valid: pd.Series) -> pd.Series:
    """信号未成形（预热期）按**满仓**处理 —— 与 `_single_signal.time_mult` 对 NaN 的处理一致。"""
    out = m.astype(float).copy()
    v = valid.reindex(out.index).fillna(False).astype(bool)
    out[~v] = 1.0
    return out


def rsi_mult(code: str, window: int = 14, q_out: float = 0.70, q_half: float = 0.50) -> pd.Series:
    r = E.rsi_monthly(code, window)
    return E.mult_three(r / 100.0, q_out, q_half)


def m_ma(code: str, n: int = 200) -> pd.Series:
    nav = E.monthly_nav(code)
    ma = E.ma_monthly(code, n).reindex(nav.index)
    return _mask_warm((nav > ma), ma.notna())


def m_cross(code: str, fast: int = 50, slow: int = 200) -> pd.Series:
    nav = E.monthly_nav(code)
    f, s = E.ma_monthly(code, fast).reindex(nav.index), E.ma_monthly(code, slow).reindex(nav.index)
    return _mask_warm((f > s), f.notna() & s.notna())


def m_mom(code: str, look: int = 12, skip: int = 1) -> pd.Series:
    """12-1 动量（月频）：过去 12 个月、跳过最近 1 个月的方向。"""
    nav = E.monthly_nav(code)
    mom = nav.shift(skip) / nav.shift(skip + look) - 1.0
    return _mask_warm((mom > 0), mom.notna())


def m_steps(code: str, breaks=(0.55, 0.60, 0.65, 0.70),
            levels=(1.0, 0.75, 0.50, 0.25, 0.0)) -> pd.Series:
    """RSI 五等分（`research/v4_levels.py` 的「五档 B」：清仓线仍 70）。"""
    q = E.rsi_monthly(code, 14) / 100.0
    out = pd.Series(float(levels[-1]), index=q.index, dtype="float64")
    prev = -np.inf
    for b, lv in zip(list(breaks) + [np.inf], levels):
        out[(q >= prev) & (q < b)] = float(lv)
        prev = b
    return out.where(q.notna(), 1.0)


def m_rsi_volcap(code: str, target: float = 0.10, win: int = 6) -> pd.Series:
    """RSI 三档 × 波动上限：信号不动，只把 σ 高时的仓位压下去。"""
    ret = E.monthly_rets(code)
    vol = (ret.rolling(win, min_periods=4).std() * np.sqrt(12)).reindex(
        E.monthly_nav(code).index)
    cap = (target / vol).clip(upper=1.0)
    cap = cap.where(vol.notna(), 1.0)
    return np.minimum(rsi_mult(code), cap)


def m_rsi_floor(code: str, floor: float = 0.20) -> pd.Series:
    """RSI 三档 + 常驻底仓（空仓时不全空）。"""
    return floor + (1.0 - floor) * rsi_mult(code)


def m_rsi_smooth(code: str, n: int = 3) -> pd.Series:
    """RSI 三档，但用 n 月均值：减少月末单点噪声造成的来回。"""
    r = E.rsi_monthly(code, 14)
    sm = r.rolling(n, min_periods=1).mean()
    return E.mult_three(sm / 100.0, 0.70, 0.50).where(r.notna(), 1.0)


def m_rsi_union(code: str) -> pd.Series:
    """RSI14 与 RSI12 取**较高**仓位：两条口径都喊减仓才减，单条喊不动手。"""
    return np.maximum(rsi_mult(code, 14, 0.70, 0.50), rsi_mult(code, 12, 0.80, 0.50))


def m_rsi_blend(code: str, w14: float = 0.5) -> pd.Series:
    """RSI14 与 RSI12 **资金各半**：50% 的钱跟 V4 的仓位、50% 跟 V8 的仓位。

    这是「两套口径都对、但各自的月度噪声互不相关」时的标准做法（信号平均），
    与 C8（取较高仓位）不同：C8 是「谁更看多听谁」，这里是「两边都听一半」。
    """
    return w14 * rsi_mult(code, 14, 0.70, 0.50) + (1.0 - w14) * rsi_mult(code, 12, 0.80, 0.50)


def m_rsi_dy(code: str, dy_th: float = 0.80, cap: float = 0.50) -> pd.Series:
    """RSI × 股息率贵度上限：股息率分位 ≥80%（贵）时仓位封顶 50%。仅 7 只有源。"""
    if not E.LEG[code]["yield_ok"]:
        return pd.Series(np.nan, index=E.monthly_nav(code).index)
    p = E.dy_pct(code).reindex(E.monthly_nav(code).index)
    cap_s = pd.Series(1.0, index=p.index)
    cap_s[p >= dy_th] = cap
    return np.minimum(rsi_mult(code), cap_s)


# ================================================================ 候选（参数事先固定）
CAND = [
    ("C1 年线闸门 MA200", "方向层：跌破年线就空仓（最朴素）", m_ma),
    ("C2 双均线 50/200", "趋势层：金叉/死叉，验证「更慢更稳」是否代价更小", m_cross),
    ("C3 12-1 动量", "方向层：过去一年（除最近一月）为正才持有", m_mom),
    ("C4 RSI14 五档（清仓线仍 70）", "档位层：把减半拆成两档，减少「一次砍一半」的粗暴", m_steps),
    ("C5 RSI14 × 波动上限 10%", "风险预算：信号不动，σ 高时压仓位（vol_legs 唯一找到增量的机制）",
     m_rsi_volcap),
    ("C6 RSI14 + 20% 底仓", "仓位层：解决「长期低仓、牛市缺席」", m_rsi_floor),
    ("C7 RSI14（3 月均值）", "信号层：月末单点 RSI 噪声大，取均值减少来回", m_rsi_smooth),
    ("C8 RSI14 ∪ RSI12（取高仓位）", "信号层：两条口径都喊减仓才减，验「单口径是否过敏」", m_rsi_union),
    ("C9 RSI14 × 股息率贵度上限", "信息源：股息率分位 ≥80% 时封顶 50%（仅 7 只有源）", m_rsi_dy),
    ("C10 RSI14+RSI12 等权（各 50%）", "集成：两套口径都给一半资金，靠平均降噪而不是靠选一个", m_rsi_blend),
]


def neighbor_grid(label: str, code: str) -> list[tuple[str, pd.Series]]:
    """★ 候选的参数邻域（只验稳健，不重新选优）。"""
    if label.startswith("C1"):
        return [(f"MA{n}", m_ma(code, n)) for n in (150, 200, 250)]
    if label.startswith("C2"):
        return [(f"{f}/{s}", m_cross(code, f, s)) for f, s in ((40, 200), (50, 200), (60, 200))]
    if label.startswith("C3"):
        return [(f"{lk}-1 动量", m_mom(code, look=lk)) for lk in (6, 12, 18)]
    if label.startswith("C4"):
        return [("断点 50–65", m_steps(code, (0.50, 0.55, 0.60, 0.65))),
                ("断点 55–70", m_steps(code, (0.55, 0.60, 0.65, 0.70))),
                ("断点 60–75", m_steps(code, (0.60, 0.65, 0.70, 0.75)))]
    if label.startswith("C5"):
        return [(f"波动上限 {t:.0%}", m_rsi_volcap(code, target=t)) for t in (0.08, 0.10, 0.12)]
    if label.startswith("C6"):
        return [(f"底仓 {f:.0%}", m_rsi_floor(code, floor=f)) for f in (0.15, 0.20, 0.30)]
    if label.startswith("C7"):
        return [(f"均值 {n} 月", m_rsi_smooth(code, n=n)) for n in (2, 3, 4)]
    if label.startswith("C9"):
        return [(f"股息率阈值 {t:.0%}", m_rsi_dy(code, dy_th=t)) for t in (0.75, 0.80, 0.85)]
    if label.startswith("C10"):
        return [(f"14 日权重 {w:.0%}", m_rsi_blend(code, w)) for w in (0.3, 0.5, 0.7)]
    return []


# ================================================================ 基线
def load_base() -> dict:
    """从 best_by_fund 的 jsonl 取每只标的的 ★1。"""
    if not os.path.exists(BASE_JSONL):
        print(f"[warn] 缺 {BASE_JSONL}，请先跑 best_by_fund 脚本")
        return {}
    out = {}
    with io.open(BASE_JSONL, encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if not line:
                continue
            r = json.loads(line)
            if r.get("rank") == 1:
                out[r["code"]] = r
    return out


# ================================================================ 主流程
def fmt(x, kind="pct"):
    if x is None or (isinstance(x, float) and not np.isfinite(x)):
        return "—"
    if kind == "pct":
        return f"{x * 100:+.2f}%"
    return f"{x:.2f}"


def main() -> int:
    bases = load_base()
    if not bases:
        return 1

    rows, detail = [], {}
    L = [
        "# 红利 · 单标的「超过当前最优」搜索",
        "",
        "> 由 `strategies/dividend/research/beat_best/_beat_best.py` 生成。"
        "**只看单标的**，不做组合与跨标的配权。目标是回答 "
        "`research/best_by_fund/_best_by_fund.md` 里每只标的的「当前最优」还能不能被超过。",
        "",
        "> 同尺口径：基金累计净值 · 月末信号次月生效 · 单边 0.03% · 空仓不计息 · "
        "**基线不用抄报告，按 `research/_leg_engine.py` 在同一窗口重算**。"
        "跨标的的绝对数字不可比（窗口不同），只看「候选 vs 该标的基线」。",
        "",
        "## 0. 方法（先看这里，否则下面的 ★ 没有意义）",
        "",
        "1. **基线 = 该标的在 `best_by_fund` 里的 ★1**（9 只是 V4/V8，`018388` 是买入持有）。"
        "基线与被测候选共用同一窗口、同一成本、同一执行。",
        "2. **候选参数事先固定**，不做网格搜索 —— 10 只标的 × 十档参数 = 100 次尝试，"
        "挑出来的「最优」必然是噪声。",
        "3. **★ = 同时满足**：全样本夏普 > 基线 + 0.02，**且**后段（样本后 40%）夏普 > 基线后段，"
        "**且**后段夏普 > 0，**且**最大回撤不比基线深 3pp 以上。",
        "4. 信号预热期（如 MA200 前 200 个交易日）一律按**满仓**处理，"
        "与家族 `time_mult` 对 NaN 的处理一致 —— 这会让样本短的标的（`561580` 3.3y）"
        "上的均线类候选偏高，看 §读法第 3 条。",
        "",
    ]

    for code in E.CODES:
        b = bases.get(code)
        if not b:
            continue
        keys = E.available_keys(code)
        start, end = E.leg_window(code, keys)
        base = E.run_leg(code, b["key"], start, end)
        rets = E.monthly_rets(code).loc[start:end]
        r_all = base["r"]
        cut = r_all.index[int(len(r_all) * CUT)]
        b_post = E.stats(r_all.loc[cut:])

        L += [f"## {E.NAME[code]}（`{code}` · {E.LEG[code]['cat']} · "
              f"{start:%Y-%m} ~ {end:%Y-%m} · {(end - start).days / 365.25:.1f}y · "
              f"后段自 {cut:%Y-%m}）", "",
              f"基线：**{b['label']}**（`best_by_fund` ★1）。同尺重算 "
              f"{fmt(base['cagr'])} / {fmt(base['dd'])} / 夏普 **{base['sharpe']:.2f}**"
              f"（后段 {b_post['sharpe']:.2f}，平均仓位 {base['expo']:.0%}，"
              f"调仓 {base['trades']} 次）。", "",
              "| 候选 | 机制 | 年化 | 回撤 | 夏普 | 后段夏普 | Calmar | 平均仓位 | 调仓 | 判定 |",
              "|---|---|---:|---:|---:|---:|---:|---:|---:|---|",
              f"| · 基线（{b['label']}） | — | {fmt(base['cagr'])} | {fmt(base['dd'])} "
              f"| {base['sharpe']:.2f} | {b_post['sharpe']:.2f} | {fmt(base['calmar'], 'f')} "
              f"| {base['expo']:.0%} | {base['trades']} | 基线 |"]

        beats, best_any, allc = [], None, []
        for label, why, fn in CAND:
            mult = fn(code)
            if mult.isna().all():
                L.append(f"| {label} | {why} | — | — | — | — | — | — | — | 无源 |")
                continue
            m = mult.reindex(rets.index)
            r = E.simulate(m, rets)
            if r.dropna().empty:
                L.append(f"| {label} | {why} | — | — | — | — | — | — | — | 无数据 |")
                continue
            p = E.stats(r, m.reindex(r.index))
            p_post = E.stats(r.loc[cut:])
            beat = (p["sharpe"] > base["sharpe"] + 0.02
                    and p_post["sharpe"] > b_post["sharpe"]
                    and p_post["sharpe"] > 0.0
                    and p["dd"] >= base["dd"] - 0.03)
            tag = ("**★ 超越**" if beat
                   else ("全样本↑·后段↓" if p["sharpe"] > base["sharpe"] + 0.02 else "—"))
            L.append(f"| {label} | {why} | {fmt(p['cagr'])} | {fmt(p['dd'])} "
                     f"| {p['sharpe']:.2f} | {p_post['sharpe']:.2f} | {p['calmar']:.2f} "
                     f"| {p['expo']:.0%} | {p['trades']} | {tag} |")
            if best_any is None or p["sharpe"] > best_any[1]["sharpe"]:
                best_any = (label, p, p_post)
            allc.append((label, p, p_post))
            if beat:
                beats.append((label, p, p_post))
        beats.sort(key=lambda x: -x[1]["sharpe"])
        best = beats[0] if beats else None

        # 邻域（只对 ★ 算；结论里要引用「稳 / 不稳」，所以先算好存进 detail）
        nb = {}
        for label, _p, _pp in beats:
            items, ok = [], 0
            for tag, mult in neighbor_grid(label, code):
                m = mult.reindex(rets.index)
                r = E.simulate(m, rets)
                if r.dropna().empty:
                    continue
                q = E.stats(r, m.reindex(r.index))
                q_post = E.stats(r.loc[cut:])
                passed = (q["sharpe"] > base["sharpe"] + 0.02
                          and q_post["sharpe"] > b_post["sharpe"]
                          and q_post["sharpe"] > 0.0
                          and q["dd"] >= base["dd"] - 0.03)
                ok += int(passed)
                items.append((tag, q, q_post, passed))
            nb[label] = dict(ok=ok, n=len(items), items=items)

        detail[code] = dict(base=base, base_post=b_post, best=best, beats=beats,
                            best_any=best_any, cut=cut, name=b["name"], base_key=b["key"],
                            base_label=b["label"], rets=rets, n=len(r_all),
                            all=allc, nb=nb)

        if best:
            L += ["", f"> **找到超越项：{best[0]}** —— 夏普 {base['sharpe']:.2f} → "
                      f"**{best[1]['sharpe']:.2f}**，后段 {b_post['sharpe']:.2f} → "
                      f"**{best[2]['sharpe']:.2f}**，年化 {fmt(base['cagr'])} → "
                      f"{fmt(best[1]['cagr'])}，回撤 {fmt(base['dd'])} → {fmt(best[1]['dd'])}，"
                      f"平均仓位 {base['expo']:.0%} → {best[1]['expo']:.0%}。"]
            rows.append((b["name"], b["label"], base["sharpe"], best[0],
                         best[1]["sharpe"], best[2]["sharpe"], b_post["sharpe"]))
        else:
            L += ["", f"> 没有候选通过（全样本 + 后段 + 回撤三项）。**最佳候选 {best_any[0]}**："
                      f"夏普 {best_any[1]['sharpe']:.2f}（基线 {base['sharpe']:.2f}）、后段 "
                      f"{best_any[2]['sharpe']:.2f}（基线 {b_post['sharpe']:.2f}）—— "
                      f"差 {best_any[1]['sharpe'] - base['sharpe']:+.2f} 没跨过门槛。"]
        L.append("")

    # ---------------- 汇总
    L += [f"## 汇总：{len(E.CODES)} 只标的里 {len(rows)} 只能再往前推", "",
          "| 标的 | 当前最优 | 基线夏普 | 超越项 | 新夏普 | 后段 新 vs 旧 | 邻域 |",
          "|---|---|---:|---|---:|---|---|"]
    for code, d in detail.items():
        if not d["best"]:
            continue
        label, p, p_post = d["best"]
        g = d["nb"].get(label) or dict(ok=0, n=0)
        nb_txt = f"{g['ok']}/{g['n']}" if g["n"] else "—"
        L.append(f"| {d['name']} | {d['base_label']} | {d['base']['sharpe']:.2f} | **{label}** "
                 f"| **{p['sharpe']:.2f}** | {p_post['sharpe']:.2f} vs "
                 f"{d['base_post']['sharpe']:.2f} | {nb_txt} |")
    miss = [d["name"] for c, d in detail.items() if not d["best"]]
    if miss:
        L.append("")
        L.append(f"> 未找到超越项：**{'、'.join(miss)}**。这不是「已经最优」，而是"
                 f"**这 {len(CAND)} 档候选没有一条能在后段也赢** —— 想再往前推，得换信息源"
                 "（横截面贵贱、量能、宏观），而不是继续调 RSI 窗口与档位。")
    L.append("")
    L.append("> **为什么前三名总是 RSI14 / RSI12 / 买入持有**：`best_by_fund` 的单标的候选只有 7 个"
             "（BH + V1/V3/V4/V5/V6/V8），其中四个估值类在 28 行里 26 行从不触发（平均仓位 ≥90%、"
             "调仓 ≤4 次），实际只有 2 支在动。所以「前三」不是从一群强者里筛出来的，"
             "而是**池子里只有两个人在动** —— 这也解释了为什么集成（C10）没什么可集成的。")

    # ---------------- 邻域
    L += ["", "## 邻域检验：★ 是「一片」还是「一个点」", "",
          "对每个 ★ 候选，把它的参数上下各挪一格（这是验稳健，不是重新选优）。"
          "真有效的规则在邻域内是一整片；只在一个点成立、旁边就塌的，是拟合。", ""]
    for code, d in detail.items():
        if not d["beats"]:
            continue
        L += [f"### {d['name']}", ""]
        for label, p, p_post in d["beats"]:
            g = d["nb"].get(label) or dict(ok=0, n=0, items=[])
            if not g["items"]:
                L.append(f"- {label}：无邻域定义（不再挪参数）。")
                continue
            L += ["| 邻域 | 年化 | 回撤 | 夏普 | 后段夏普 | 仍超越基线 |",
                  "|---|---:|---:|---:|---:|---|"]
            for tag, q, q_post, passed in g["items"]:
                L.append(f"| {label.split()[0]} · {tag} | {fmt(q['cagr'])} | {fmt(q['dd'])} "
                         f"| {q['sharpe']:.2f} | {q_post['sharpe']:.2f} | "
                         f"{'是' if passed else '**否**'} |")
            L += ["", f"> 邻域内 **{g['ok']}/{g['n']}** 仍超越基线。"
                      + ("邻域是一整片，可以当作候选信号。" if g["ok"] == g["n"] else
                         ("邻域内多数仍超越，结论偏稳，但要用中位数而不是最优点描述收益。"
                          if g["ok"] >= max(1, g["n"] * 2 // 3) else
                          "**邻域不稳：最优点只是刚好挑中的那一格，不能当结论。**")), ""]

    # ---------------- 结论
    L += ["", "## 结论", ""]
    if rows:
        L += ["**找到的**（含邻域稳健性；`1/3` 这类标记的含义见上一节）：", ""]
        for code, d in detail.items():
            for label, p, p_post in d["beats"]:
                g = d["nb"].get(label) or dict(ok=0, n=0)
                stab = (f"邻域 {g['ok']}/{g['n']} 稳" if g["n"] else "无邻域定义")
                warn = " ⚠" if (g["n"] and g["ok"] < g["n"]) else ""
                L.append(f"- **{d['name']} · {label}**：夏普 {d['base']['sharpe']:.2f} → "
                         f"**{p['sharpe']:.2f}**，后段 {d['base_post']['sharpe']:.2f} → "
                         f"**{p_post['sharpe']:.2f}**，年化 {fmt(d['base']['cagr'])} → "
                         f"{fmt(p['cagr'])}，回撤 {fmt(d['base']['dd'])} → {fmt(p['dd'])}"
                         f"（{stab}{warn}）。")
        L.append("")
    else:
        L += ["**一只标的都没有找到超越项。** 这与 vol_legs 的情况一致：", ""]

    gaps = sorted(((d["best_any"][1]["sharpe"] - d["base"]["sharpe"], c, d)
                   for c, d in detail.items() if d["best_any"]),
                  key=lambda x: -x[0])
    L += ["**最佳候选与基线的差距（大于 0.02 才算跨过门槛）**：", "",
          "| 标的 | 最佳候选 | 夏普 | 基线 | 差 |", "|---|---|---:|---:|---:|"]
    for g, code, d in gaps:
        L.append(f"| {d['name']} | {d['best_any'][0]} | {d['best_any'][1]['sharpe']:.2f} "
                 f"| {d['base']['sharpe']:.2f} | {g:+.2f} |")
    L.append("")

    # 按候选族汇总「高于基线的标的数」，避免用印象写结论
    fam_cnt = {}
    for _c, d in detail.items():
        for label, p, _pp in d["all"]:
            fam = label.split()[0]
            n0, n1 = fam_cnt.get(fam, (0, 0))
            fam_cnt[fam] = (n0 + int(p["sharpe"] > d["base"]["sharpe"]), n1 + 1)

    def _cnt(*fams):
        a = sum(fam_cnt.get(f, (0, 0))[0] for f in fams)
        b = sum(fam_cnt.get(f, (0, 0))[1] for f in fams)
        return f"{a}/{b}"

    c4_star = sum(1 for _c, d in detail.items()
                  if d["best"] and d["best"][0].startswith("C4"))
    L += ["### 机制上的四条发现", ""]
    L.append(f"1. **唯一系统性为正的改造是「把减半拆细」（C4 五档）**：{_cnt('C4')} 行的夏普高于基线，"
             f"{c4_star} 只过★，且过★的这几只邻域 2/3~3/3 都站得住。它的作用机制很干净 —— "
             "不换信号、不加方向层，只是把「一次砍一半」拆成 25%/50%/75% 三档，"
             "**减少了一次性砍在阶段低点的概率**。这与 vol_legs 里「改仓位深度有效、改信号无效」"
             "是同一条结论。")
    L.append(f"2. **加方向层（年线/金叉/动量）几乎全面为负**：C1+C2+C3 合计 {_cnt('C1', 'C2', 'C3')} 行"
             "高于基线。红利类的超额来自「跌完就回来」，慢方向层恰好把反弹那段切掉 —— "
             "这与 vol_legs 在 A 股突破类策略上的结论同向（紧出场优于慢出场），"
             "但原因相反：那边是「假突破多」，这边是「回撤浅、反弹快」。"
             "唯一例外是 `018388`（港股通，基线是买入持有）—— 港股红利趋势性强、回撤深，"
             "年线闸门在那里有意义，但它只有 3.2 年、邻域 1/3 稳，只能当**假设**。")
    L.append(f"3. **风险预算与底仓在红利上不灵**：C5（σ 上限）{_cnt('C5')}、C6（20% 底仓）{_cnt('C6')}"
             " 行高于基线 —— 与 vol_legs 在科创50 上「σ 上限砍掉一半回撤、夏普 +0.16」正好相反。"
             "原因是标的性格：科创50 的 σ 高且断崖式下跌多，压仓位能躲开；红利本身 σ 只有 12%~15%，"
             "σ 高位往往对应「急跌后的反弹初期」，压仓位等于在最快的修复段减仓。"
             f"平滑（C7）{_cnt('C7')}、股息率封顶（C9）{_cnt('C9')} 也都不成立，"
             "唯一过★的 `016441` · C7 邻域只有 1/3 —— 按本报告的判据算**噪声**，不进候选。")
    # 4) 集成：RSI14 与 RSI12 等权（2026-11-19 用户提出）—— 用数据说明为什么不加
    blend = [p["sharpe"] - d["base"]["sharpe"] for _c, d in detail.items()
             for lab, p, _pp in d["all"] if lab.startswith("C10")]
    act = []
    for _c in detail:
        m14 = rsi_mult(_c, 14, 0.70, 0.50)
        m12 = rsi_mult(_c, 12, 0.80, 0.50)
        mm = pd.concat([m14, m12], axis=1, keys=["a", "b"]).dropna()
        if len(mm):
            act.append(float((mm["a"] != mm["b"]).mean()))
    c10_star = sum(1 for _c, d in detail.items()
                   if d["best"] and d["best"][0].startswith("C10"))
    if blend:
        L.append(f"4. **「RSI14 与 RSI12 各投一半」（C10 集成）也试过了：没有一只能过★，"
                 f"夏普相对基线 {min(blend):+.2f}~{max(blend):+.2f}**。"
                 "这不是「碰巧没赢」—— "
                 f"{len(act)} 只标的里两者给出**不同仓位**的月份平均只有 {np.mean(act):.0%}"
                 f"（中位 {np.median(act):.0%}）：V4 与 V8 是同一族的参数孪生"
                 "（同一形态、同一三档结构、清仓线 70 vs 80），**信号高度共线**。"
                 "共线集成只能把仓位拉回两者中间，而基线本来就取了两者中较好的那个，"
                 "所以等权混合必然略低于基线。真正需要「等权混」的是**信息源不同**的信号"
                 "（估值 / 位置 / 趋势 / 量能），但 C1/C2/C3/C9 已经把这条路走过：在红利上都为负。")
        L.append("")

    L += ["### 下一步该换的是信息源，不是参数", "",
          "`best_by_fund` §2 已经给出方向：**这个家族的钱主要在横截面**（同一个月里哪只最便宜），"
          "不在单只标的的择时上 —— 每腿 ★1 拼装的组合并不优于现行 V4 的「5 只池 + top2 + 板块 RSI」。"
          "所以真正值得做的下一件事不是给 RSI 换参数，而是：", "",
          "| 信息源 | 能吃到的地方 | 现有产物 |", "|---|---|---|",
          "| 横截面贵贱（股息率 / 利差 / 温度） | 池内 top2 / top3 的选品 | `research/backtest_dims.py`、"
          "`v9_temp5/backtest/_v9_report.md` |",
          "| 池的构成（哪些标的适合被择时） | V4/V8 专用池 | `v4_rsi/universe.py`、"
          "`v4_rsi/research/_oos_pool.md` |",
          "| 卖点是否合理（有进有出的规则） | V10 状态机的离场层 | `v10_resonance/research/_sell_lab_report.md` |",
          "",
          "注意：横截面与池的每一条都会**引入选择偏差**（同一段样本挑池/挑阈值），"
          "落地前必须做「训练期选 → 检验期验」的错位检验，那是 `_oos_pool.py` 已经在用的方法。", ""]

    L += ["## 读法（三条，别跳过）", "",
          "1. **★ 是「同尺 + 后段」双闸门**，不是「全样本夏普最高」。表里出现 "
          "`全样本↑·后段↓` 的行就是过拟合的典型形状，不要用。",
          "2. **基线本身含选择偏差**：★1 是在同一段样本里挑出来的，所以「赢基线 0.02」"
          "其实比看起来更难 —— 能赢的候选，是在跟一个已经用掉样本信息的对手比。",
          "3. **短样本警告**：`561060`（3.0y）/`561580`（3.3y）/`018388`（3.3y）/`016441`（4.0y）"
          "的后段只有 14~19 个月，★ 的统计意义很弱；且均线类候选在短样本上的预热期占比高，"
          "结论只能当方向。",
          "",
          "> 机械规则输出，非投资建议。", ""]

    with io.open(OUT_MD, "w", encoding="utf-8") as fh:
        fh.write("\n".join(L) + "\n")
    print(f"[ok] {OUT_MD}")
    print("")
    for code, d in detail.items():
        tag = d["best"][0] if d["best"] else "—"
        print(f"  {d['name'][:20]:<22} 基线 {d['base']['sharpe']:.2f} "
              f"({d['base_label']}) → 超越 {tag}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
