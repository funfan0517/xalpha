#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""V4 择时池的**样本外**检验：用 2020–2024 选池，2025 起检验。

## 要回答的问题

`pool.TIMING_POOL` 的 5 只是用 **2020-01~2026-10 全样本**的 ΔCalmar 筛出来的
（`universe.py`，判据 = 全区间与共同窗口都 ≥ +0.10）。两个窗口都在同一段样本里，
所以 V4 报的 11.4% / Calmar 1.44 **含选择偏差**（`RULES.md` §四.4 已承认）。

本脚本把选择过程真的做一次样本外：

  - 训练期 2020-01 ~ 2024-12：按与 `universe.py` 相同的判据（ΔCalmar ≥ +0.10）选池；
  - 检验期 2025-01 ~ 最新：**池固定**，只跑 V4 的「横截面 top2 + 时间择时」；
  - 对照：现在用的全样本 5 只池、全部标的池、等权基准。

如果「训练期选出的池」在检验期明显差于「全样本选出的池」，差额就是选择偏差的量级。

产物：research/_oos_pool.md（不覆盖 `backtest/_v4_report.md`）。
用法：`python strategies/dividend/v4_rsi/research/_oos_pool.py`
"""
from __future__ import annotations

import os
import sys

import pandas as pd

_DIR = os.path.dirname(os.path.abspath(__file__))
_V4 = os.path.dirname(_DIR)
_STRAT = os.path.dirname(_V4)
for _p in (_DIR, _V4, _STRAT):
    if _p not in sys.path:
        sys.path.insert(0, _p)

import _single_signal as S  # noqa: E402  pylint: disable=wrong-import-position
import pool  # noqa: E402  pylint: disable=wrong-import-position
import rule as V4  # noqa: E402  pylint: disable=wrong-import-position

OUT = os.path.join(_DIR, "_oos_pool.md")
TRAIN_END = "2024-12-31"
OOS_START = "2025-01-01"
MIN_MONTHS = 30          # 训练期至少这么多月才参与选池
PICK_DCALMAR = 0.10      # 与 universe.py / RULES.md 同一判据


def load():
    navs = pool.load_navs()
    rets = S.load_rets(navs)
    rsi = S.load_signal("rsi", navs).reindex(rets.index)
    return rets, rsi


def one(code, rets, rsi):
    """单标的「RSI≥70 清仓 / ≥50 减半」→ (ΔCalmar, 月数)。"""
    r = rets[code].dropna()
    q = rsi[code].reindex(r.index) / 100.0
    if q.notna().sum() < MIN_MONTHS:
        return None, int(q.notna().sum())
    mult = S.time_mult(q, V4.Q_OUT, V4.Q_HALF)
    rs = S.simulate(pd.DataFrame({code: mult}), rets[[code]].reindex(r.index))
    if len(rs) < 3:
        return None, len(rs)
    bh = r.reindex(rs.index)
    return S.stats(rs)["calmar"] - S.stats(bh)["calmar"], len(rs)


def combo(codes, rets, rsi, label):
    """V4 组合（top2 + 时间择时）在给定池/区间上的表现。"""
    rets = rets[codes].dropna(how="all")
    q = rsi[codes].reindex(rets.index) / 100.0
    xs = q.copy()                     # V4 用原始 RSI 做横截面键（越小越便宜）
    sims, _, _ = S.run_variants(q, rets, xs, k=V4.TOP_K, q_out=V4.Q_OUT, q_half=V4.Q_HALF,
                                base_label=f"基准：{len(codes)} 只等权")
    r = sims[f"横截面 top{V4.TOP_K} + 时间择时"]
    st = S.stats(r)
    return dict(label=label, ann=st["cagr"], calmar=st["calmar"], dd=st["dd"], n=len(r))


def main() -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")

    rets, rsi = load()
    codes = list(rets.columns)
    tr_rets, tr_rsi = rets.loc[:TRAIN_END], rsi.loc[:TRAIN_END]
    oo_rets, oo_rsi = rets.loc[OOS_START:], rsi.loc[OOS_START:]

    rows = []
    for c in codes:
        d, n = one(c, tr_rets, tr_rsi)
        rows.append({"code": c, "months": n, "dcalmar": d,
                     "picked": d is not None and d >= PICK_DCALMAR})
    rows.sort(key=lambda x: (-(x["dcalmar"] if x["dcalmar"] is not None else -9)))
    picked = [r["code"] for r in rows if r["picked"]]
    current = [c for c in pool.TIMING_CODES if c in codes]

    pools = []
    for label, cs in (("训练期筛出（真样本外）", picked),
                      ("当前全样本 5 只池（含选择偏差）", current),
                      ("全部标的", codes)):
        if not cs:
            continue
        tr = combo(cs, tr_rets, tr_rsi, label)
        oo = combo(cs, oo_rets, oo_rsi, label)
        pools.append({"label": label, "codes": cs, "train": tr, "oos": oo})

    name = {r["code"]: r.get("name", r["code"]) for r in pool.POOL}
    oos_months = len(oo_rets)

    L = [
        "# V4 择时池 · 样本外检验（2020–2024 选池 → 2025 起检验）",
        "",
        f"> 训练期 2020-01 ~ {TRAIN_END}（{len(tr_rets)} 月）· "
        f"检验期 {OOS_START} ~ 最新（{oos_months} 月）· 判据 ΔCalmar ≥ {PICK_DCALMAR:+.2f}"
        "（与 `universe.py` 同一条）。",
        "> 组合口径 = V4「横截面 top2 + 时间择时」，月末信号、次月生效、单边 0.03%。",
        "> 机械规则输出，非投资建议。",
        "",
        "## 1. 训练期逐标的（选池依据）",
        "",
        "| 代码 | 名称 | 训练月数 | 训练 ΔCalmar | 入选 |",
        "|---|---|---:|---:|:---:|",
    ]
    for r in rows:
        d = "—" if r["dcalmar"] is None else f"{r['dcalmar']:+.2f}"
        L.append(f"| {r['code']} | {name.get(r['code'], '')} | {r['months']} | {d} | "
                 f"{'✓' if r['picked'] else ''} |")

    L += ["", "## 2. 组合：训练期 vs 检验期", "",
          "| 池 | 只数 | 训练年化 | 训练 Calmar | **检验年化** | **检验 Calmar** | 检验回撤 |",
          "|---|---:|---:|---:|---:|---:|---:|"]
    for p in pools:
        L.append(f"| {p['label']} | {len(p['codes'])} | "
                 f"{p['train']['ann'] * 100:+.1f}% | {p['train']['calmar']:.2f} | "
                 f"**{p['oos']['ann'] * 100:+.1f}%** | **{p['oos']['calmar']:.2f}** | "
                 f"{p['oos']['dd'] * 100:+.1f}% |")

    cur = next((p for p in pools if p["label"].startswith("当前")), None)
    tru = next((p for p in pools if p["label"].startswith("训练期")), None)
    allp = next((p for p in pools if p["label"].startswith("全部")), None)
    gap = (cur["oos"]["calmar"] - tru["oos"]["calmar"]) if (cur and tru) else None
    dmap = {r["code"]: r["dcalmar"] for r in rows}
    mons = {r["code"]: r["months"] for r in rows}
    too_short = [c for c in current if mons.get(c, 0) < MIN_MONTHS]
    flipped_out = [c for c in picked if c not in current]
    L += ["", "## 3. 结论", ""]
    if not picked:
        L += [f"- 训练期没有任何标的同时满足「≥{MIN_MONTHS} 月 + ΔCalmar ≥ "
              f"{PICK_DCALMAR:+.2f}」—— 说明这条判据在小样本下本身不稳，"
              "V4 的池子不能宣称有样本外支撑。"]
    else:
        L += [f"- 训练期筛出 **{len(picked)}** 只：{' / '.join(picked)}；"
              f"检验期年化 {tru['oos']['ann'] * 100:+.1f}%、Calmar {tru['oos']['calmar']:.2f}。"]
        if gap is not None:
            L += [f"- 当前全样本池在**同一检验期**的 Calmar 比训练期池高 **{gap:+.2f}**、"
                  f"年化高 **{(cur['oos']['ann'] - tru['oos']['ann']) * 100:+.1f}pp**。"
                  "这就是「池子用到了检验期信息」能带来的差额量级。"
                  if gap > 0 else
                  f"- 当前全样本池在检验期反而低 **{gap:+.2f}** —— 本次没有观察到选择偏差，"
                  f"但检验期只有 {oos_months} 个月，不能据此说池子稳健。"]
    if too_short:
        L += [f"- 当前池里 **{' / '.join(too_short)}** 在训练期只有 "
              f"{'、'.join(str(mons.get(c, 0)) for c in too_short)} 个月，"
              "**根本进不了 2020–2024 的候选集**（历史不够）—— 它们的「有效」只在包含 "
              "2025–2026 的全样本里才成立，这部分不是窗口翻转，是**样本不足**。"]
    if flipped_out:
        L += [f"- 反过来，训练期入选但被全样本判据淘汰的：**{' / '.join(flipped_out)}**"
              f"（训练 ΔCalmar {'、'.join(f'{dmap[c]:+.2f}' for c in flipped_out)}）—— "
              "同一套判据、换一段窗口就翻号，说明**单标的 ΔCalmar 本身不是稳定量**，"
              "用它筛池等于在噪声里挑最大值。"]
    if cur and allp and abs(cur["oos"]["ann"] - allp["oos"]["ann"]) < 0.01:
        L += ["- 但「当前 5 只」在检验期并不比「全部标的」更好"
              f"（{cur['oos']['ann'] * 100:+.1f}% vs {allp['oos']['ann'] * 100:+.1f}%）—— "
              "**筛选动作本身没有贡献**，检验期的收益主要来自池里刚好有 2025 年后的新基金。"]
    L += [f"- 检验期仅 **{oos_months} 个月**（2025-01 起），一轮涨跌都没走完；"
          "这里只给方向与量级，不构成「改池」的依据。V4 的 11.4% / Calmar 1.44 仍应"
          "按「含选择偏差」解读。",
          "",
          "> 复现：`python strategies/dividend/v4_rsi/research/_oos_pool.py`"]

    with open(OUT, "w", encoding="utf-8") as fh:
        fh.write("\n".join(L) + "\n")

    print(f"[oos] 训练 {len(tr_rets)} 月 / 检验 {oos_months} 月")
    for r in rows:
        d = "—" if r["dcalmar"] is None else f"{r['dcalmar']:+.2f}"
        print(f"[oos] {r['code']} {name.get(r['code'], '')[:14]:14s} "
              f"{r['months']:3d}m  ΔCalmar {d}  {'✓' if r['picked'] else ''}")
    for p in pools:
        print(f"[oos] {p['label']}: 训练 {p['train']['ann'] * 100:+.1f}% / "
              f"检验 {p['oos']['ann'] * 100:+.1f}%  检验 Calmar {p['oos']['calmar']:.2f}")
    print(f"[oos] 产物 -> {os.path.relpath(OUT, _V4)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
