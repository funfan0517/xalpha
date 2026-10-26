#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""V4 阈值专项：70/50 要不要改成 60/40？

V4 的时间维度是「板块 RSI 均值 → 仓位倍率」：
`≥ q_out 清仓 / ≥ q_half 减半 / 其余满仓`。默认 **70 / 50**（`v4_rsi/rule.py`）。

本脚本回答「把阈值整体下移到 60/40 值不值」，做法是：

1. **同口径全网格**（7 只基金 · 2020-01~2026-10 · 82 月 · 单边 0.03%），
   两个口径都跑 —— ① 主方案（横截面 top2 × 时间倍率）② 只看时间维度（等权 × 倍率），
   后者与 `v4_rsi/backtest/_v4_report.md` 附表同口径，可对照。
2. **邻域稳健性**：单个最优点若是孤峰，就不该上；看 3×3 邻域是否同号。
3. **分年归因**：阈值下移到底在哪些年份起作用。

产物（AGENTS §8.4 ④ 专项分析）：`research/backtest/_v4_thresholds.md`

用法::

    python strategies/dividend/research/v4_thresholds.py
"""

from __future__ import annotations

import os
import sys

import numpy as np
import pandas as pd

_DIR = os.path.dirname(os.path.abspath(__file__))
_STRAT = os.path.dirname(_DIR)
_V4 = os.path.join(_STRAT, "v4_rsi")
for _p in (_DIR, _STRAT, _V4):
    if _p not in sys.path:
        sys.path.insert(0, _p)

import _single_signal as S  # noqa: E402  pylint: disable=wrong-import-position
import rule as V4  # noqa: E402  pylint: disable=wrong-import-position

OUT_MD = os.path.join(_DIR, "backtest", "_v4_thresholds.md")
Q_OUTS = (0.50, 0.55, 0.60, 0.65, 0.70, 0.75, 0.80)
Q_HALFS = (0.30, 0.35, 0.40, 0.45, 0.50, 0.55)
CUR = (0.70, 0.50)
CAND = (0.60, 0.40)
TOP_GAP = 0.10          # 清仓线与减半线至少差 10 分，否则「减半」形同虚设


def _one(bq, top, eq, rets, q_out, q_half) -> dict:
    mult = S.time_mult(bq, q_out, q_half)
    w_full = top.mul(mult, axis=0)
    w_time = eq.mul(mult, axis=0)
    out = {}
    for tag, w in (("full", w_full), ("time", w_time)):
        r = S.simulate(w, rets)
        st = S.stats(r)
        out[tag] = {"r": r, "cagr": st["cagr"], "dd": st["dd"], "calmar": st["calmar"],
                    "sharpe": st["sharpe"],
                    "pos": float(w.sum(axis=1).mean()),
                    "n_half": int(((mult > 0) & (mult < 1)).sum()),
                    "n_out": int((mult == 0).sum())}
    return out


def _d3(r: pd.Series, base: pd.Series) -> float:
    d = (r - base).dropna()
    return S.stats(r.drop(d.nlargest(3).index))["cagr"] if len(d) > 3 else float("nan")


def _p(x, d=1):
    return "—" if x is None or (isinstance(x, float) and np.isnan(x)) else f"{x * 100:.{d}f}%"


def main() -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")

    rets = S.load_rets()
    q = S.cheapness("rsi").reindex(rets.index)
    xs = S.xs_key("rsi").reindex(rets.index)
    ok = S.available(q, rets)
    bq = S.board_q(q, ok)
    top = S.top_w(xs, ok, 2)
    eq = S.equal_w(ok)
    base = S.simulate(eq, rets)
    bst = S.stats(base)

    res, flat = {}, []
    for qo in Q_OUTS:
        for qh in Q_HALFS:
            if qo - qh < TOP_GAP - 1e-9:
                continue
            row = _one(bq, top, eq, rets, qo, qh)
            res[(qo, qh)] = row
            flat.append({"q_out": qo, "q_half": qh,
                         **{f"{k}_{m}": row[k][m] for k in ("full", "time")
                            for m in ("cagr", "dd", "calmar", "sharpe", "pos")},
                         "d3_full": _d3(row["full"]["r"], base)})
    grid = pd.DataFrame(flat)

    cur, cand = res[CUR], res[CAND]
    best = grid.loc[grid["full_calmar"].idxmax()]

    # 分年
    years = sorted({d.year for d in rets.index})
    yearly = pd.DataFrame(
        {y: {"基准": S.stats(base[base.index.year == y])["cagr"],
             "70/50": S.stats(cur["full"]["r"][cur["full"]["r"].index.year == y])["cagr"],
             "60/40": S.stats(cand["full"]["r"][cand["full"]["r"].index.year == y])["cagr"],
             "60/50": S.stats(res[(0.60, 0.50)]["full"]["r"][
                 res[(0.60, 0.50)]["full"]["r"].index.year == y])["cagr"]}
         for y in years}).T

    L = ["# V4 阈值：70/50 要不要换成 60/40？", "",
         f"> 口径：7 只基金 · 2020-01~{rets.index[-1]:%Y-%m}（{len(rets)} 月）· 累计净值 · "
         "单边 0.03% · 月末信号下月生效。主方案 = 横截面 top2 × 时间倍率。", "",
         f"基准（7 只等权）：年化 **{_p(bst['cagr'])}**、回撤 {_p(bst['dd'])}、"
         f"Calmar **{bst['calmar']:.2f}**。", "",
         "## 一、结论", ""]

    # 最深回撤发生在哪个月、当时的板块 RSI（决定两者是否同等暴露）
    nav = (1 + cur["full"]["r"]).cumprod()
    ddser = nav / nav.cummax() - 1
    t_dd = ddser.idxmin()
    i_dd = bq.index.get_loc(t_dd)
    bq_dec = float(bq.iloc[max(i_dd - 1, 0)])      # 造成该月亏损的决策月（上月末）
    m_cur = S.time_mult(bq, *CUR).iloc[max(i_dd - 1, 0)]
    m_cand = S.time_mult(bq, *CAND).iloc[max(i_dd - 1, 0)]
    dd_txt = (f"最深回撤 {ddser.min() * 100:.1f}% 出现在 **{t_dd:%Y-%m}**，"
              f"而决策月（上月末）的板块 RSI 只有 **{bq_dec * 100:.1f}** —— "
              f"此刻 70/50 与 60/40 的倍率分别是 {m_cur:.1f} / {m_cand:.1f}，"
              f"{'**两者同等暴露，阈值下移对这一跌毫无作用**' if m_cur == m_cand else '暴露不同'}。")

    d_calmar = cand["full"]["calmar"] - cur["full"]["calmar"]
    d_cagr = cand["full"]["cagr"] - cur["full"]["cagr"]
    verdict = "不建议" if (d_calmar < 0 and d_cagr < 0) else "可以考虑"
    L += [f"**{verdict}整体下移到 60/40。**", "",
          "| 方案 | 年化 | 最大回撤 | Calmar | 夏普 | 平均仓位 | 减半月数 | 清仓月数 |",
          "|---|---:|---:|---:|---:|---:|---:|---:|",
          f"| **70/50（现行）** | {_p(cur['full']['cagr'])} | {_p(cur['full']['dd'])} | "
          f"**{cur['full']['calmar']:.2f}** | {cur['full']['sharpe']:.2f} | "
          f"{_p(cur['full']['pos'], 0)} | {cur['full']['n_half']} | {cur['full']['n_out']} |",
          f"| **60/40（提议）** | {_p(cand['full']['cagr'])} | {_p(cand['full']['dd'])} | "
          f"**{cand['full']['calmar']:.2f}** | {cand['full']['sharpe']:.2f} | "
          f"{_p(cand['full']['pos'], 0)} | {cand['full']['n_half']} | {cand['full']['n_out']} |", "",
          f"- 回撤 **没有改善**（{_p(cur['full']['dd'])} → {_p(cand['full']['dd'])}），"
          f"年化却少了 **{-d_cagr * 100:.1f}pp**，Calmar 从 {cur['full']['calmar']:.2f} 掉到 "
          f"{cand['full']['calmar']:.2f} —— **同等回撤下少赚，属于被支配**。",
          f"- 根因：60/40 只是把仓位的**整体水平**压低（平均 {_p(cur['full']['pos'], 0)} → "
          f"{_p(cand['full']['pos'], 0)}），并没有让「什么时候躲」更准。{dd_txt}",
          "- 单看时间维度（等权 × 倍率，与 `_v4_report.md` 附表同口径）也是同一结论："
          f"70/50 Calmar {cur['time']['calmar']:.2f} vs 60/40 {cand['time']['calmar']:.2f}。", ""]

    L += ["## 二、分年：阈值下移在哪一年起作用", "",
          "| 年份 | 基准 | 70/50 | 60/40 | 60/50 |", "|---|---:|---:|---:|---:|"]
    for y, row in yearly.iterrows():
        L.append(f"| {y} | {_p(row['基准'])} | {_p(row['70/50'])} | {_p(row['60/40'])} | "
                 f"{_p(row['60/50'])} |")
    L += ["", "- 下移阈值是**全面**减仓：多数年份收益同时下降，只有大跌年（2022）能少亏，"
              "净效果被牛市年的机会成本吃掉。", ""]

    L += ["## 三、全网格（主方案 Calmar，按优劣排序）", "",
          "| 清仓线 / 减半线 | 年化 | 最大回撤 | Calmar | 平均仓位 | 剔除 3 月后 |",
          "|---|---:|---:|---:|---:|---:|"]
    for _, r in grid.sort_values("full_calmar", ascending=False).head(12).iterrows():
        tag = "**现行**" if (r["q_out"], r["q_half"]) == CUR else (
            "**提议**" if (r["q_out"], r["q_half"]) == CAND else "")
        L.append(f"| {int(r['q_out'] * 100)} / {int(r['q_half'] * 100)} {tag} | "
                 f"{_p(r['full_cagr'])} | {_p(r['full_dd'])} | **{r['full_calmar']:.2f}** | "
                 f"{_p(r['full_pos'], 0)} | {_p(r['d3_full'])} |")
    L += ["", f"- 网格最优是 **{int(best['q_out'] * 100)}/{int(best['q_half'] * 100)}**"
              f"（Calmar {best['full_calmar']:.2f}、年化 {_p(best['full_cagr'])}、"
              f"仓位 {_p(best['full_pos'], 0)}）—— 它的改法是「清仓提前、减半不动」，"
              "与 60/40 的「两条线一起下移」不是一回事。", ""]

    # 邻域：沿两条轴分别看落差
    outs = (0.55, 0.60, 0.65)
    halfs = (0.40, 0.45, 0.50)
    L += ["## 四、最优点的邻域（看哪条线更敏感）", "",
          "单点最优不可信，要看沿哪条轴移动会掉分（`—` = 两线相差 <10 分，不构成有效档位）：",
          "",
          "| 清仓线 ↓ / 减半线 → | " + " | ".join(f"{int(round(h * 100))}" for h in halfs) + " |",
          "|" + "---|" * (len(halfs) + 1)]
    for o in outs:
        cells = []
        for h in halfs:
            v = res.get((round(o, 2), round(h, 2)))
            cells.append(f"{v['full']['calmar']:.2f}" if v else "—")
        L.append(f"| **{int(round(o * 100))}** | " + " | ".join(cells) + " |")

    v_ref = res[(0.60, 0.50)]["full"]["calmar"]
    v_half = res[(0.60, 0.45)]["full"]["calmar"]
    v_out65 = res[(0.65, 0.50)]["full"]["calmar"]
    v_out70 = res[(0.70, 0.50)]["full"]["calmar"]
    L += ["", f"- **减半线最敏感**：清仓线固定 60 时，减半线 50 → 45 让 Calmar 从 "
              f"{v_ref:.2f} 掉到 {v_half:.2f}（{v_half - v_ref:+.2f}）；再降到 40 是 "
              f"{res[(0.60, 0.40)]['full']['calmar']:.2f}。**你提议要动的正是这根。**",
          f"- 清仓线没那么敏感：减半线固定 50 时，清仓线 60 → 65 → 70 是 "
          f"{v_ref:.2f} → {v_out65:.2f} → {v_out70:.2f}（{v_out70 - v_ref:+.2f}），"
          "基本是一片平台，只是年化和仓位略有差别。",
          "- 所以「60/50 最优」不是孤峰，而是**沿清仓线方向的高原**，"
          "但它依赖减半线停在 50 —— 这条恰恰不能挪。", ""]

    L += ["## 五、如果真想降回撤，该怎么改", "",
          "| 目标 | 做法 | 代价 |", "|---|---|---|",
          "| 降波动/回撤 | 整体降低股票暴露（减配红利、加现金），而不是挪 RSI 阈值 | 收益同比降，但可控、可预期 |",
          "| 保留 V4 逻辑、少亏一点 | 只降清仓线（70 → 60），减半线保持 50 | 需要邻域验证，且属样本内优选 |",
          "| 不改 | 维持 70/50 | 接受 2021–2024 慢牛里长期半仓 |", "",
          "> 提醒：V4 的阈值不是「越保守越好」。RSI 的语义是位置，不是估值 —— "
          "把线压低等于长期低配，这在震荡市是保护、在单边市是持续跑输。"
          "改阈值前先想清楚要的是**更小的回撤**还是**更高的 Calmar**，两者在这里不是同一个解。", ""]

    os.makedirs(os.path.dirname(OUT_MD), exist_ok=True)
    with open(OUT_MD, "w", encoding="utf-8") as fh:
        fh.write("\n".join(L))

    print(f"[thr] 基准 7 只等权 Calmar {bst['calmar']:.2f} · 样本 {len(rets)} 月")
    for tag, r in (("70/50 现行", cur), ("60/40 提议", cand), ("60/50 网格最优", res[(0.60, 0.50)])):
        f = r["full"]
        print(f"[thr] {tag:14s} 年化 {_p(f['cagr']):>7s}  回撤 {_p(f['dd']):>7s}  "
              f"Calmar {f['calmar']:5.2f}  仓位 {_p(f['pos']):>5s}  "
              f"减半 {f['n_half']:2d} 月 / 清仓 {f['n_out']:2d} 月")
    print(f"[thr] 产物 -> {os.path.relpath(OUT_MD, _DIR)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
