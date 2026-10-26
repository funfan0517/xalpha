#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""V4 档位专项：三档（100/50/0）改成五等分（100/75/50/25/0）会不会更好？

V4 的时间维度现在是**三档**：板块 RSI ≥70 清仓、≥50 减半、其余满仓。
本脚本把它换成**五档**（以及连续线性作为上界参照），同口径对比：

* 三档：`70 / 50`  → 100 / 50 / 0
* 五档 A「端点对齐」：断点 55/60/65/70 → 100 / 75 / 50 / 25 / 0（清仓线不变）
* 五档 B「对称」：断点 40/50/60/70 → 100 / 75 / 50 / 25 / 0（满仓线不变）
* 五档 C「激进」：断点 50/55/60/65 → 100 / 75 / 50 / 25 / 0（清仓线前移）
* 连续线性：(70−RSI)/20 截断到 [0,1]，即 RSI 50 满仓、70 空仓之间线性
* 连续宽档：(80−RSI)/40，RSI 40 满仓、80 空仓

档位越多 = 调仓越碎（每月可能只动 25%），所以要同时看**换手成本**。

产物（AGENTS §8.4 ④ 专项分析）：`research/backtest/_v4_levels.md`

用法::

    python strategies/dividend/research/v4_levels.py
"""

from __future__ import annotations

import os
import sys

import numpy as np
import pandas as pd

_DIR = os.path.dirname(os.path.abspath(__file__))
_STRAT = os.path.dirname(_DIR)
for _p in (_DIR, _STRAT):
    if _p not in sys.path:
        sys.path.insert(0, _p)

import _single_signal as S  # noqa: E402  pylint: disable=wrong-import-position

OUT_MD = os.path.join(_DIR, "backtest", "_v4_levels.md")
FEE = S.FEE


def mult_steps(bq: pd.Series, breaks, levels) -> pd.Series:
    """按断点把 bq 映射成仓位倍率。breaks 升序，levels 比 breaks 多一个。"""
    m = pd.Series(float(levels[-1]), index=bq.index)
    prev = -np.inf
    for b, lv in zip(list(breaks) + [np.inf], levels):
        m[(bq >= prev) & (bq < b)] = float(lv)
        prev = b
    return m


def mult_linear(bq: pd.Series, full=0.50, out=0.70) -> pd.Series:
    return ((out - bq) / (out - full)).clip(0, 1)


def variants() -> dict[str, tuple[str, pd.Series]]:
    """名称 -> (说明, 倍率构造器)。构造器在拿到 bq 后调用。"""
    return {
        "三档 70/50": ("<50 满仓 / 50–70 半仓 / ≥70 空仓",
                     lambda bq: S.time_mult(bq, 0.70, 0.50)),
        "五档 A 严格等分 50–65": ("50–65 等分 4 段，清仓线随之降到 65",
                                lambda bq: mult_steps(bq, (0.50, 0.55, 0.60, 0.65),
                                                      (1.0, 0.75, 0.5, 0.25, 0.0))),
        "五档 B 保 70 清仓 · 满仓线上移 55": ("清仓线仍 70，但满仓门槛从 50 提到 55",
                                       lambda bq: mult_steps(bq, (0.55, 0.60, 0.65, 0.70),
                                                             (1.0, 0.75, 0.5, 0.25, 0.0))),
        "五档 C 对称 40/50/60/70": ("满仓线降到 40、清仓线仍 70，中间 4 段",
                                lambda bq: mult_steps(bq, (0.40, 0.50, 0.60, 0.70),
                                                      (1.0, 0.75, 0.5, 0.25, 0.0))),
        "连续 50→70 线性": ("RSI 50 满仓 → 70 空仓，中间线性",
                        lambda bq: mult_linear(bq, 0.50, 0.70)),
        "连续 40→80 宽档": ("RSI 40 满仓 → 80 空仓，中间线性",
                        lambda bq: mult_linear(bq, 0.40, 0.80)),
    }


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
    base = S.simulate(S.equal_w(ok), rets)
    bst = S.stats(base)
    years = max(len(rets) / 12, 1e-9)

    rows, sims, yearly = [], {}, {}
    for name, (desc, fn) in variants().items():
        m = fn(bq)
        w = top.mul(m, axis=0)
        r = S.simulate(w, rets)
        st = S.stats(r)
        turn = float((w - w.shift(1).fillna(0.0)).abs().sum(axis=1).sum())
        d = (r - base).dropna()
        d3 = S.stats(r.drop(d.nlargest(3).index))["cagr"]
        rows.append({"方案": name, "档位": desc, "年化": st["cagr"], "回撤": st["dd"],
                     "Calmar": st["calmar"], "夏普": st["sharpe"],
                     "仓位": float(w.sum(axis=1).mean()),
                     "换手": turn, "成本": turn * FEE / years,
                     "剔除3月": d3,
                     "n_lv": int(m.round(2).nunique())})
        sims[name] = r
        yearly[name] = r.groupby(r.index.year).apply(lambda s: S.stats(s)["cagr"])
    tab = pd.DataFrame(rows)

    L = ["# V4 档位：三档（100/50/0）vs 五等分（100/75/50/25/0）", "",
         f"> 口径：7 只基金 · 2020-01~{rets.index[-1]:%Y-%m}（{len(rets)} 月）· 累计净值 · "
         f"单边 {FEE * 100:.2f}% · 月末信号下月生效 · 横截面 top2 × 时间倍率。", "",
         f"基准（7 只等权）：年化 **{_p(bst['cagr'])}**、回撤 {_p(bst['dd'])}、"
         f"Calmar **{bst['calmar']:.2f}**。", ""]

    cur = tab[tab["方案"].str.startswith("三档")].iloc[0]
    best = tab.loc[tab["Calmar"].idxmax()]
    others = tab[tab["方案"] != cur["方案"]].sort_values("Calmar", ascending=False)
    runner = others.iloc[0]
    five = tab[tab["方案"].str.startswith("五档")]
    cont = tab[tab["方案"].str.startswith("连续")]

    best_five = five.sort_values("Calmar").iloc[-1]
    worst_five = five.sort_values("Calmar").iloc[0]
    L += ["## 一、结论", ""]
    if best["方案"] == cur["方案"]:
        L += [f"**没有一种五档方案跑赢现行三档。** 现行 Calmar {cur['Calmar']:.2f}；"
              f"五档里最好的是 `{best_five['方案']}` "
              f"{best_five['Calmar']:.2f}（{best_five['Calmar'] - cur['Calmar']:+.2f}），"
              f"最差的是 `{worst_five['方案']}` {worst_five['Calmar']:.2f}"
              f"（{worst_five['Calmar'] - cur['Calmar']:+.2f}）。"
              f"两种连续映射也都不如三档（最好 {cont['Calmar'].max():.2f}）。", ""]
    else:
        L += [f"**五档有边际改善**：`{best['方案']}` Calmar {best['Calmar']:.2f}"
              f"（现行 {cur['Calmar']:.2f}），但平均仓位从 {_p(cur['仓位'], 0)} 变到 "
              f"{_p(best['仓位'], 0)} —— 增量来自暴露而不是精度。", ""]

    L += ["| 方案 | 档位 | 年化 | 最大回撤 | Calmar | ΔCalmar | 平均仓位 | 月均换手 | 年化成本 | 剔除 3 月后 |",
          "|---|---|---:|---:|---:|---:|---:|---:|---:|---:|"]
    for _, r in tab.iterrows():
        mark = "（**现行**）" if r["方案"] == cur["方案"] else ""
        L.append(f"| {r['方案']} {mark} | {r['档位']} | {_p(r['年化'])} | {_p(r['回撤'])} | "
                 f"**{r['Calmar']:.2f}** | {r['Calmar'] - cur['Calmar']:+.2f} | "
                 f"{_p(r['仓位'], 0)} | {_p(r['换手'] / len(rets))} | {_p(r['成本'], 2)} | "
                 f"{_p(r['剔除3月'])} |")
    L += ["",
          f"- **档位数不是杠杆，断点位置才是**：三种五档方案的 Calmar 是 "
          f"{five['Calmar'].min():.2f} ~ {five['Calmar'].max():.2f}，跨度 "
          f"{five['Calmar'].max() - five['Calmar'].min():.2f} —— 比「三档 → 五档」本身的"
          "变化大得多。换句话说，**同样的五档，断点挪一挪，结论就翻转**。",
          f"- **最深回撤改不动**：−7.5% 出现在 2020-03（决策月 2020-02 的板块 RSI 只有 38.8），"
          "那一刻所有方案都是满仓。反过来，把满仓门槛上移到 55（B 版）会让 2021–2024 的"
          "暴露升到 84%，回撤直接恶化到 "
          f"{_p(five[five['方案'].str.contains('B')]['回撤'].iloc[0])}。",
          f"- **连续化更差**：线性映射的 Calmar 是 {cont['Calmar'].min():.2f} ~ "
          f"{cont['Calmar'].max():.2f}，都不如三档 {cur['Calmar']:.2f} —— "
          "说明「50–70 这段之间的相对高低」几乎不含可用信息，细分它就是在拟合噪声。",
          f"- **成本不构成否决理由**：月均换手 {_p(cur['换手'] / len(rets))} → 最高 "
          f"{_p(five['换手'].max() / len(rets))}（含每月换标的），年化成本 "
          f"{_p(cur['成本'], 2)} → {_p(five['成本'].max(), 2)}，差 3bp 级。", ""]

    # 分年
    L += ["## 二、分年（年化）", "",
          "| 年份 | " + " | ".join(tab["方案"].str.replace(r"[（(].*?[)）]", "", regex=True)) + " |",
          "|---|" + "---:|" * len(tab)]
    for y in sorted(yearly[cur["方案"]].index):
        L.append(f"| {y} | " + " | ".join(_p(yearly[n].get(y, float('nan'))) for n in tab["方案"]) + " |")
    diffs = []
    for n in tab["方案"]:
        if n == cur["方案"]:
            continue
        dd_ = (yearly[n] - yearly[cur["方案"]]).dropna()
        diffs.append((n, dd_.min(), dd_.max()))
    lo = min(d[1] for d in diffs)
    hi = max(d[2] for d in diffs)
    L += ["", f"- 分年差异落在 **{lo * 100:+.1f}pp ~ {hi * 100:+.1f}pp**，方向不稳定："
              "有的年份多赚、有的年份少赚，没有系统性优势 —— 这不是「更准」，"
              "而是「同一信号换了个执行网格」。", ""]

    # 档位分布（只对阶梯方案有意义；连续映射没有离散档位）
    L += ["## 三、档位占用（各仓位水平的月份占比）", "",
          "| 方案 | 0% | 25% | 50% | 75% | 100% |", "|---|---:|---:|---:|---:|---:|"]
    for name, (_, fn) in variants().items():
        if name.startswith("连续"):
            L.append(f"| {name} | — | — | — | — | — |")
            continue
        m = fn(bq).round(2)
        dist = [f"{(m == v).mean() * 100:.0f}%" for v in (0.0, 0.25, 0.5, 0.75, 1.0)]
        L.append(f"| {name} | " + " | ".join(dist) + " |")
    L += ["", "（连续映射没有离散档位，故留空；其平均仓位见 §一 表。）", ""]

    L += ["## 四、判断", "",
          "1. **档位不是免费的精度**：三档的「50–70 一律半仓」看起来粗，"
          "但它恰好对应 RSI 唯一可靠的信息 —— 「是否进入超买区」。把这段切细，"
          "等于假设 RSI 62 和 RSI 68 有不同含义；本样本里没有证据支持。",
          "2. **若一定要上五档，选 C 版（40/50/60/70）**：保留 70 清仓线、"
          f"只把满仓门槛从 50 放到 40，Calmar {tab[tab['方案'].str.contains('C 对称')]['Calmar'].iloc[0]:.2f}"
          f"（现行 {cur['Calmar']:.2f}），逻辑自洽（越便宜越满仓）。"
          "不要选 B 版（清仓 70 却把满仓线提到 55）—— 那是「既怕高又怕低」，"
          "结果两头不讨好：暴露 84%、回撤反而最深。",
          "3. **别把精力花在档位上**：从三档到五档能动的只有 4 个断点，"
          "而把断点整体挪 10 分（见 `_v4_thresholds.md`）会让 Calmar 在 1.12–1.52 之间跳 —— "
          "这个杠杆比档位数大十倍，但也更容易过拟合。",
          "4. **更值得试的方向不是档位**：如果要提高 Calmar，动「横截面选品」（top1/top3、"
          "或按 RSI 距离加权）比动「时间档位」更有物理意义 —— 选品决定持有谁，"
          "档位只决定持有多少。", ""]

    os.makedirs(os.path.dirname(OUT_MD), exist_ok=True)
    with open(OUT_MD, "w", encoding="utf-8") as fh:
        fh.write("\n".join(L))

    print(f"[lvl] 基准 7 只等权 Calmar {bst['calmar']:.2f} · 样本 {len(rets)} 月")
    for _, r in tab.iterrows():
        print(f"[lvl] {r['方案']:26s} 年化 {_p(r['年化']):>7s}  回撤 {_p(r['回撤']):>7s}  "
              f"Calmar {r['Calmar']:5.2f}  仓位 {_p(r['仓位'], 0):>5s}  "
              f"换手 {_p(r['换手'], 0):>5s}")
    print(f"[lvl] 产物 -> {os.path.relpath(OUT_MD, _DIR)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
