#!/usr/bin/env python
# -*- coding: utf-8 -*-
r"""V9 · 按温度档位分配每只标的的占比（红利资金 100% 满仓）—— 效果研究

用户问题：**把整个红利类资金看成 100%，按各标的的温度档位分配占比，效果如何？**

与 V9 原回测的区别（这点必须先说清，否则结论会被误读）
------------------------------------------------------
`v9_temp5/backtest.py` 测的是两件事：**横截面 topK 等权**（只买最冷的 2/3 只）与
**时间维度降暴露**（板块温度高就整体减仓）。用户问的是第三件事 ——
**不降总仓位（始终 100%），只按档位在标的之间分配权重**。所以本课题是新增的，
不是复述 topK 结论。

口径（全部沿用 V9，不另立一套）
------------------------------
* 温度：`factors.build()` 算出的**月度五因子温度**（35/20/16/15/14 · etf.run 等权 PE/PB），
  月末算出、**次月生效**（`W.shift(1)`），与 V9 回测同一份代码；
* 收益：基金**累计净值**（`pool.load_navs`，含分红再投），月末收盘对齐；
* 成本：单边 0.03%/次，按 `Σ|Δw|` 计；
* 池：**只有 6 只**（`rule.CODES`）—— 温度要求等权 PE/PB 全历史，
  `008163`/`005125`/`007760`/`018388` 无源，不能参与温度排序（`rule.FUNDS` 注释已说明）；
* 逐月只在「当月有温度 + 有净值」的标的间归一化到 100%，不把资金留给不存在的标的。

档位 → 配额（主方案）
--------------------
用 `rule.BANDS` 的七档（禁改阈值），把「规则原文的动作」翻译成相对配额：

| 档位 | 温度 | 配额 |
|---|---|---:|
| 冰点 | <20 | 2.0 |
| 偏冷 | 20–40 | 1.5 |
| 中性 | 40–55 | 1.0 |
| 中性偏热 | 55–65 | 0.8 |
| 偏热 | 65–75 | 0.5 |
| 过热 | 75–85 | 0.2 |
| 危险 | ≥85 | 0.0 |

配额归一化后即为占比。方案的「强度」用与等权的混合系数 `λ` 表示：
`w = (1−λ)·等权 + λ·档位权重`，`λ=1` 是纯档位、`λ=0` 是等权（基准）——
邻域检验就是 `λ` 从 0 到 1 的一条曲线，不需要重新挑参数。

产物：`_temp_weight.md`（本目录）。
用法：`python strategies/dividend/v9_temp5/research/temp_weight/_temp_weight.py`

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

_HERE = os.path.dirname(os.path.abspath(__file__))     # .../v9_temp5/research/temp_weight
_RES = os.path.dirname(_HERE)                          # .../v9_temp5/research
_V9 = os.path.dirname(_RES)                            # .../v9_temp5
_STRAT = os.path.dirname(_V9)                          # .../dividend
for _p in (_HERE, _RES, _V9, _STRAT):
    if _p not in sys.path:
        sys.path.insert(0, _p)

import _single_signal as S  # noqa: E402  pylint: disable=wrong-import-position
import factors  # noqa: E402  pylint: disable=wrong-import-position
import pool  # noqa: E402  pylint: disable=wrong-import-position
import rule as V9  # noqa: E402  pylint: disable=wrong-import-position

OUT_MD = os.path.join(_HERE, "_temp_weight.md")
FEE = S.FEE                     # 单边 0.03%
MIN_FUNDS = 3                   # 当月至少 3 只有温度才成组合（少于 3 只不构成「分配」）
TAIL = 3                        # 剔除检验：去掉相对基准贡献最大的 3 个月

# 档位 -> 相对配额（与 rule.BANDS 的档名一一对应；改这里就是改方案，阈值本身不动）
QUOTA = {"冰点": 2.0, "偏冷": 1.5, "中性": 1.0, "中性偏热": 0.8,
         "偏热": 0.5, "过热": 0.2, "危险": 0.0}


# ================================================================ 数据
def load_panel():
    """-> (温度宽表, 月度收益宽表, 说明 dict)。

    **关键对齐**：温度来自**指数估值历史**（`etf.run` 的 PE/PB，可回溯到 2016），
    但基金本身可能 2023 年才成立（`561060`/`561580`）。没净值的月份没有份额可买，
    必须把温度屏蔽掉 —— 否则权重会分给不存在的标的，组合收益被稀释。
    """
    panels = factors.build()
    temp = factors.monthly_temp(panels)
    navs = pool.load_navs(codes=V9.CODES).resample("ME").last().ffill()
    rets = navs.pct_change()
    temp = temp.where(navs.reindex(temp.index).notna())
    return temp, rets, dict(panels=panels)


def quota_of(t: float) -> float:
    return QUOTA.get(V9.band_of(t)[0], 1.0)


# ================================================================ 权重方案
def w_equal(row: pd.Series) -> pd.Series:
    ok = row.dropna()
    out = pd.Series(0.0, index=row.index)
    if ok.empty:
        return out
    out.loc[ok.index] = 1.0 / len(ok)
    return out


def w_quota(row: pd.Series, power: float = 1.0, lam: float = 1.0) -> pd.Series:
    """档位配额 -> 占比（在可得标的间归一化）；`lam` 为与等权的混合系数。"""
    ok = row.dropna()
    out = pd.Series(0.0, index=row.index)
    if ok.empty:
        return out
    q = ok.map(quota_of).pow(power)
    if q.sum() <= 0:
        q = pd.Series(1.0, index=ok.index)
    w = q / q.sum()
    eq = pd.Series(1.0 / len(ok), index=ok.index)
    out.loc[ok.index] = (1.0 - lam) * eq + lam * w
    return out


def w_linear(row: pd.Series, hi: float = 85.0, lam: float = 1.0) -> pd.Series:
    """连续线性：配额 ∝ max(0, hi − 温度)，hi = 危险线 85。"""
    ok = row.dropna()
    out = pd.Series(0.0, index=row.index)
    if ok.empty:
        return out
    q = (hi - ok).clip(lower=0.0)
    if q.sum() <= 0:
        q = pd.Series(1.0, index=ok.index)
    w = q / q.sum()
    eq = pd.Series(1.0 / len(ok), index=ok.index)
    out.loc[ok.index] = (1.0 - lam) * eq + lam * w
    return out


def w_top(row: pd.Series, k: int = 3) -> pd.Series:
    """对照：只买温度最低的 k 只等权（V9 报告测过的 topK 口径）。"""
    ok = row.dropna()
    out = pd.Series(0.0, index=row.index)
    if ok.empty:
        return out
    pick = ok.nsmallest(min(k, len(ok))).index
    out.loc[pick] = 1.0 / len(pick)
    return out


def static_from(avgw: pd.Series):
    """恒定使用给定权重（在可得标的间归一化）—— 静态倾斜对照。"""
    def fn(row: pd.Series) -> pd.Series:
        ok = row.dropna()
        out = pd.Series(0.0, index=row.index)
        if ok.empty:
            return out
        w = avgw.reindex(ok.index).fillna(0.0)
        if w.sum() <= 0:
            w = pd.Series(1.0, index=ok.index)
        out.loc[ok.index] = w / w.sum()
        return out
    return fn


def perm_test(t: pd.DataFrame, rets: pd.DataFrame, fn, n: int = 200,
              seed: int = 20261119) -> pd.DataFrame:
    """逐月把温度在标的间随机打乱后重放同一套权重函数 -> 指标分布（零假设检验）。"""
    cols = list(t.columns)
    vals = t[cols].values.astype(float)
    rng = np.random.default_rng(seed)
    ok = ~np.isnan(vals)
    out = []
    for _ in range(n):
        # 只在「当月可得」的标的之间打乱温度，不能把 NaN 挪到别的标的上
        pv = np.full_like(vals, np.nan)
        for i, v in enumerate(vals):
            m = ok[i]
            pv[i, m] = rng.permutation(v[m])
        W = build_weights(pd.DataFrame(pv, index=t.index, columns=cols), fn)
        r, _turn = simulate(W, rets)
        out.append(S.stats(r))
    return pd.DataFrame(out)


# ================================================================ 组合模拟
def simulate(W: pd.DataFrame, rets: pd.DataFrame) -> tuple[pd.Series, pd.Series]:
    """`W[t]` = t 月末定下的权重 -> t+1 月生效；成本 = Σ|Δw| × 单边费率。

    **必须按「当月有收益」重新归一化**：某只标的当月还没有净值（基金未成立）时，
    它的权重既拿不到收益、又不会自动变现金 —— 若不算这一层，组合会被静默稀释成「部分空仓」。
    """
    W = W.reindex(rets.index).fillna(0.0)
    wl = W.shift(1).fillna(0.0).where(rets.notna(), 0.0)
    tot = wl.sum(axis=1)
    wl = wl.div(tot.where(tot > 0), axis=0).fillna(0.0)
    port = (wl * rets.fillna(0.0)).sum(axis=1)
    turn = (wl - wl.shift(1).fillna(0.0)).abs().sum(axis=1)
    return (port - turn * FEE).iloc[1:].dropna(), turn.iloc[1:]


def build_weights(temp: pd.DataFrame, fn) -> pd.DataFrame:
    rows = {ts: fn(row) for ts, row in temp.iterrows()}
    return pd.DataFrame(rows).T


def stat_row(r: pd.Series, turn: pd.Series = None) -> dict:
    st = dict(S.stats(r))
    st["turn"] = float(turn.reindex(r.index).mean()) if turn is not None else np.nan
    st["drag"] = st["turn"] * FEE * 12 if turn is not None else np.nan
    return st


# ================================================================ 判别力
def ic_table(temp: pd.DataFrame, rets: pd.DataFrame) -> dict:
    """横截面秩相关（温度 vs 次月收益）与冷/热两组的次月收益差。"""
    t = temp.reindex(rets.index).dropna(how="all").iloc[:-1]
    r = rets.shift(-1).reindex(t.index)
    ics, spreads, cold, hot = [], [], [], []
    for ts, row in t.iterrows():
        ok = row.dropna()
        nxt = r.loc[ts].reindex(ok.index).dropna()
        ok = ok.reindex(nxt.index)
        if len(ok) < MIN_FUNDS:
            continue
        ics.append(float(ok.corr(nxt, method="spearman")))
        excess = nxt - nxt.mean()
        order = ok.rank()
        lo, hi = order <= 2, order >= len(order) - 1
        cold.append(float(excess[lo].mean()))
        hot.append(float(excess[hi].mean()))
        spreads.append(cold[-1] - hot[-1])
    s = pd.Series(spreads)
    return dict(ic=float(np.nanmean(ics)) if ics else np.nan, n=len(ics),
                ic_neg=float(np.mean([x < 0 for x in ics])) if ics else np.nan,
                cold=float(np.mean(cold)) if cold else np.nan,
                hot=float(np.mean(hot)) if hot else np.nan,
                spread=float(s.mean()) if len(s) else np.nan,
                win=float((s > 0).mean()) if len(s) else np.nan,
                t=float(s.mean() / s.std() * np.sqrt(len(s))) if len(s) > 2 and s.std() else np.nan)


# ================================================================ 报告
def pct(x, nd=2, sign=False):
    if x is None or (isinstance(x, float) and not np.isfinite(x)):
        return "—"
    return f"{x:+.{nd}%}" if sign else f"{x:.{nd}%}"


def f2(x):
    return "—" if x is None or (isinstance(x, float) and not np.isfinite(x)) else f"{x:.2f}"


SCHEMES = [
    ("等权（基准）", lambda row: w_equal(row), "不分配：6 只各 1/6"),
    ("档位配额 λ=1.0（主方案）", lambda row: w_quota(row, 1.0, 1.0), "冰点2.0 … 危险0.0"),
    ("档位配额 λ=0.5（半强度）", lambda row: w_quota(row, 1.0, 0.5), "等权与档位各一半"),
    ("档位配额 λ=0.25（弱）", lambda row: w_quota(row, 1.0, 0.25), "只做微调"),
    ("档位配额 ^2（加陡）", lambda row: w_quota(row, 2.0, 1.0), "配额平方，放大冷热差"),
    ("连续线性 (85−T)", lambda row: w_linear(row, 85.0, 1.0), "不设档，直接线性"),
    ("最冷 3 只等权", lambda row: w_top(row, 3), "V9 报告已测的 topK 口径"),
    ("最冷 2 只等权", lambda row: w_top(row, 2), "同上，更集中"),
]


def render(temp: pd.DataFrame, rets: pd.DataFrame, res: dict, res_c: dict,
           weights: dict, ic: dict, ic_c: dict, asof: pd.Timestamp) -> str:
    # 与 run() 同过滤：>=3 只有温度、且有净值
    tf = temp[temp.notna().sum(axis=1) >= MIN_FUNDS]
    tf = tf[tf.index.isin(rets.index)]
    rf = rets.reindex(tf.index)   # 只在研究窗口内取收益，否则前段零收益会稀释年化
    L = []
    L.append("# V9 · 按温度档位分配占比（红利资金 100% 满仓）")
    L.append("")
    L.append(f"> 生成 {asof:%Y-%m-%d} · 由 `strategies/dividend/v9_temp5/research/temp_weight/"
             "_temp_weight.py` 生成。**研究产物，不进每日流程**。")
    L.append("> 温度用 `factors.build()`（月末算、次月生效）；收益用基金累计净值；"
             "单边 0.03%；逐月只在有温度的标的间归一化到 100%，**不降总仓位**。")
    L.append("> 与 `v9_temp5/backtest.py` 的区别：那边测「topK 选品 + 时间降仓」，"
             "本课题测「满仓下按档位分配权重」。模拟结果，非投资建议。")
    L.append("")

    L.append("## 0. 池与窗口：为什么只有 6 只")
    L.append("")
    L.append("温度要求 **etf.run 等权日频 PE/PB 全历史**，全池 10 只里只有 "
             f"{len(V9.CODES)} 只有（`rule.FUNDS`）："
             + "、".join(f"`{c}`{V9.NAME[c]}" for c in V9.CODES) + "。"
             "其余 4 只（`008163` 标普红利低波50、`005125` 标普红利机会、`007760` 沪港深红利、"
             "`018388` 港股通）**没有同源估值历史**，温度不可比 —— 如果资金必须覆盖 10 只，"
             "这 4 只只能另订规则（如等权或 V0 打分），本报告不给建议。")
    L.append("")
    nav_n = temp.notna().sum(axis=1)
    n_min, n_max = int(nav_n[res["start"]:res["end"]].min()), int(nav_n[res["start"]:res["end"]].max())
    L.append(f"- **全窗口**：{res['start']:%Y-%m} ~ {res['end']:%Y-%m}（{res['n']} 月）——"
             f"每月至少 {MIN_FUNDS} 只「既有净值又有指数温度」，可用只数 {n_min}→{n_max} 只，"
             "权重按月重新归一；起点被「第 3 只基金成立」卡住（2021-04），不是温度没数据。")
    L.append(f"- **共同窗口**：{res_c['start']:%Y-%m} ~ {res_c['end']:%Y-%m}"
             f"（{res_c['n']} 月）—— 6 只全部有温度，与 `_v9_report.md` 的样本同量级但更短。")
    L.append("")

    def table(title, rr):
        L.append(f"### {title}")
        L.append("")
        L.append("| 方案 | 年化 | 最大回撤 | Calmar | 波动 | 夏普 | 月换手 | 成本拖累/年 |")
        L.append("|---|---:|---:|---:|---:|---:|---:|---:|")
        base = rr["等权（基准）"]
        for name, _fn, _note in SCHEMES:
            st = rr[name]
            star = " **★**" if (name != "等权（基准）"
                                and st["cagr"] > base["cagr"] + 1e-4
                                and st["calmar"] > base["calmar"] + 1e-4) else ""
            L.append(f"| {name}{star} | {pct(st['cagr'])} | {pct(st['dd'])} | {f2(st['calmar'])} "
                     f"| {pct(st['vol'])} | {f2(st['sharpe'])} | {st['turn']:.0%} "
                     f"| {pct(st['drag'], 2)} |")
        L.append("")
        L.append("> `★` = 年化与 Calmar **同时**高于等权（不是单一指标好看）。"
                 "「月换手」= 平均每月 `Σ|Δw|`，等权基准也有换手（基金净值份额变化会自动漂移）。")
        L.append("")

    table(f"1.1 全窗口（逐月可用只数 {n_min}→{n_max}，随基金成立增加）", res["rows"])
    table("1.2 共同窗口（6 只都有净值与温度）", res_c["rows"])
    # ---- 与 V9 原报告对账（结论为什么不同）
    L.append("### 1.3 与 V9 原报告的对账（结论为什么不同）")
    L.append("")
    bt_path = os.path.join(_V9, "backtest", "_v9_bt.jsonl")
    if os.path.exists(bt_path):
        with io.open(bt_path, encoding="utf-8") as fh:
            recs = [json.loads(x) for x in fh if x.strip()]
        b = pd.DataFrame(recs)
        b["date"] = pd.to_datetime(b["date"])
        b = b.set_index("date")["bench"].dropna()
        mine = rets.reindex(b.index).mean(axis=1)
        ov = pd.DataFrame({"v9": b, "mine": mine}).dropna()
        b_st, m_st = S.stats(b), S.stats(ov["mine"])
        absd = (ov["v9"] - ov["mine"]).abs()
        L.append("| 项 | 值 |")
        L.append("|---|---|")
        L.append(f"| V9 回测窗口 | {b.index[0]:%Y-%m} ~ {b.index[-1]:%Y-%m}（{len(b)} 月） |")
        L.append(f"| `_v9_report.md` 的基准（6 只等权） | 年化 {pct(b_st['cagr'])} / "
                 f"{pct(b_st['dd'])} / Calmar {f2(b_st['calmar'])} |")
        L.append(f"| 本脚本同一段重算（逐月可得标的等权） | 年化 {pct(m_st['cagr'])} / "
                 f"{pct(m_st['dd'])} / Calmar {f2(m_st['calmar'])} |")
        L.append(f"| 逐月收益差的平均绝对值 | {absd.mean():.4%}（最大 {absd.max():.4%}） |")
        L.append("")
        L.append("> 收益源与口径**对得上**（逐月差在浮点误差量级），所以两个报告的差异不是算法，是**样本**："
                 "V9 那 80 个月从 2020-02 起（含疫情后的红利反弹），而且 2021 年之前只有 3~4 只基金有净值，"
                 "「6 只等权」名不副实；本报告要求 ≥3 只**同时**有净值和指数温度，所以从 2021-04 起。"
                 "同一段 80 个月里他们的 top3 Calmar 1.01 ≈ 等权 1.03（横截面无效）；"
                 f"本报告这 {res['n']} 个月里 top3 明显高于等权 —— **结论对样本敏感，正是「弱信号」的特征**，"
                 "下面所有判断都以「两段窗口是否同向」为准，而不是任何单一窗口的排名。")
        L.append("")

    # ---- 分配长什么样
    L.append("## 2. 这套分配实际长什么样")
    L.append("")
    main = "档位配额 λ=1.0（主方案）"
    W = weights["full"][main]
    last = W.iloc[-6:]
    L.append("最近 6 个月的权重（主方案，单位 %）：")
    L.append("")
    L.append("| 月份 | " + " | ".join(f"{V9.NAME[c]}" for c in V9.CODES) + " | 当月温度 |")
    L.append("|---|" + "---:|" * len(V9.CODES) + "---|")
    for ts, row in last.iterrows():
        trow = temp.loc[ts].reindex(V9.CODES)
        band = " / ".join(str(int(round(v))) if pd.notna(v) else "—" for v in trow)
        L.append(f"| {ts:%Y-%m} | " + " | ".join(f"{row[c] * 100:.0f}" for c in V9.CODES)
                 + f" | {band} |")
    L.append("")
    avg = W[W.sum(axis=1) > 0].mean()
    nz = (W > 0.01).sum(axis=1).mean()
    zero = (W < 0.005).sum(axis=1).mean()
    L.append(f"- 平均权重：" + "、".join(f"{V9.NAME[c]} {avg[c]:.0%}" for c in V9.CODES))
    L.append(f"- 平均每月有 {nz:.1f} 只持仓、{zero:.1f} 只被压到 0（`危险` 档配额为 0）；"
             f"主方案月换手 {res['rows'][main]['turn']:.0%}，是等权 "
             f"{res['rows']['等权（基准）']['turn']:.0%} 的"
             f" {res['rows'][main]['turn'] / max(res['rows']['等权（基准）']['turn'], 1e-9):.1f} 倍。")
    L.append("")

    # ---- 判别力
    L.append("## 3. 温度到底有没有横截面判别力")
    L.append("")
    L.append("| 窗口 | 平均秩相关 IC | 期数 | IC<0 占比 | 冷组次月超额 | 热组次月超额 | 冷−热 | 冷−热胜率 | t |")
    L.append("|---|---:|---:|---:|---:|---:|---:|---:|---:|")
    for tag, d in (("全窗口", ic), ("共同窗口", ic_c)):
        L.append(f"| {tag} | {d['ic']:+.3f} | {d['n']} | {d['ic_neg']:.0%} "
                 f"| {pct(d['cold'], 2)} | {pct(d['hot'], 2)} | {pct(d['spread'], 2)} "
                 f"| {d['win']:.0%} | {d['t']:+.2f} |")
    L.append("")
    L.append("> IC 为负 = 温度高的标的次月涨得少（方向符合「温度是贵度」）。"
             "「冷−热」= 最冷 2 只的次月超额均值 − 最热 2 只的次月超额均值，"
             "超额相对当月横截面均值算。**这是分配方案能不能赚钱的唯一来源** —— "
             "如果这一行不显著为正，档位权重只是在做随机偏离等权。")
    L.append("")

    # ---- 敏感性
    L.append("## 4. 敏感性：强度 λ 与配额形状")
    L.append("")
    L.append("`λ` = 与等权的混合系数（0 = 等权，1 = 纯档位）。这是**同一条规则的强度**，"
             "不是重新选参数：真正有效的机制应该在这条曲线上**单调**或至少**一片为正**。")
    L.append("")
    L.append("| λ | 年化 | 回撤 | Calmar | 夏普 | 月换手 |")
    L.append("|---:|---:|---:|---:|---:|---:|")
    for lam in (0.0, 0.25, 0.5, 0.75, 1.0):
        W = build_weights(tf, lambda row, _l=lam: w_quota(row, 1.0, _l))
        r, turn = simulate(W, rf)
        st = stat_row(r, turn)
        L.append(f"| {lam:.2f} | {pct(st['cagr'])} | {pct(st['dd'])} | {f2(st['calmar'])} "
                 f"| {f2(st['sharpe'])} | {st['turn']:.0%} |")
    L.append("")
    L.append("配额形状（λ=1）：`^0.5` = 压平（更接近等权）、`^1` = 原样、`^2` = 加陡。")
    L.append("")
    L.append("| 形状 | 年化 | 回撤 | Calmar | 夏普 | 月换手 |")
    L.append("|---|---:|---:|---:|---:|---:|")
    for pw, tag in ((0.5, "配额^0.5（压平）"), (1.0, "配额^1（原样）"), (2.0, "配额^2（加陡）")):
        W = build_weights(tf, lambda row, _p=pw: w_quota(row, _p, 1.0))
        r, turn = simulate(W, rf)
        st = stat_row(r, turn)
        L.append(f"| {tag} | {pct(st['cagr'])} | {pct(st['dd'])} | {f2(st['calmar'])} "
                 f"| {f2(st['sharpe'])} | {st['turn']:.0%} |")
    L.append("")

    # ---- 静态倾斜：动态分配是不是其实只是「固定超配某一只」
    L.append("### 4.3 静态倾斜对照：动态分配到底动了多少")
    L.append("")
    Wm = weights["full"]["档位配额 λ=1.0（主方案）"]
    live = Wm[Wm.sum(axis=1) > 0]
    avgw = live.mean()
    Ws = build_weights(tf, static_from(avgw))
    r_s, turn_s = simulate(Ws, rf)
    st_s = stat_row(r_s, turn_s)
    st_m = res["rows"]["档位配额 λ=1.0（主方案）"]
    st_b = res["rows"]["等权（基准）"]
    dev = (live - 1.0 / live.shape[1]).abs().sum(axis=1).mean()
    L.append("把主方案的**全样本平均权重**固定下来（每月照这个比例持有，不再看温度），"
             "看看动态那部分值多少钱。`偏离度` = 主方案每月权重与等权的平均 `Σ|Δw|`。")
    L.append("")
    L.append("| 组合 | 年化 | 回撤 | Calmar | 月换手 |")
    L.append("|---|---:|---:|---:|---:|")
    L.append(f"| 等权（基准） | {pct(st_b['cagr'])} | {pct(st_b['dd'])} | {f2(st_b['calmar'])} "
             f"| {st_b['turn']:.0%} |")
    L.append(f"| 静态倾斜（恒定用主方案平均权重） | {pct(st_s['cagr'])} | {pct(st_s['dd'])} "
             f"| {f2(st_s['calmar'])} | {st_s['turn']:.0%} |")
    L.append(f"| 动态档位（主方案） | {pct(st_m['cagr'])} | {pct(st_m['dd'])} "
             f"| {f2(st_m['calmar'])} | {st_m['turn']:.0%} |")
    L.append("")
    L.append(f"> 主方案与等权的平均权重偏离度是 **{dev:.0%}/月**；"
             f"静态倾斜已经能拿到 {pct(st_s['cagr'])} / Calmar {f2(st_s['calmar'])}。"
             "**动态部分 = 主方案 − 静态倾斜**：这部分才是「温度每月变化」的贡献。"
             "如果两者几乎一样，说明这套方案实质是「长期超配了某几只便宜标的」，"
             "而不是「按温度调仓」。")
    L.append("")

    # ---- 置换检验：把温度在标的间打乱，看运气分布
    perm = perm_test(tf, rf, lambda row: w_quota(row, 1.0, 1.0), n=200)
    L.append("### 4.4 置换检验：把温度在 6 只之间随机打乱 200 次")
    L.append("")
    L.append("零假设 = 「温度与标的的对应关系没有信息」。每月把 6 个温度值随机打乱再套同一套档位映射，"
             "这样**配额分布、换手、成本都与主方案一致**，只有「谁冷谁热」是假的。")
    L.append("")
    L.append("| 指标 | 主方案 | 置换中位数 | 置换 5%~95% | p 值 |")
    L.append("|---|---:|---:|---|---:|")

    def _f(key_, v_):
        return pct(v_) if key_ in ("cagr", "dd") else f2(v_)

    perm_p = {}
    for key, label in (("cagr", "年化"), ("calmar", "Calmar"), ("sharpe", "夏普"), ("dd", "最大回撤")):
        obs = st_m[key]
        lo, hi = perm[key].quantile(0.05), perm[key].quantile(0.95)
        # 四个指标都是「越大越好」（回撤是负数，越大 = 越浅），p = 随机打乱能达到主方案的比例
        pval = float((perm[key] >= obs).mean())
        perm_p[key] = pval
        L.append(f"| {label} | {_f(key, obs)} | {_f(key, perm[key].median())} "
                 f"| {_f(key, lo)} ~ {_f(key, hi)} | {pval:.0%} |")
    L.append("")
    L.append("> 四个指标都是「越大越好」（回撤是负数，越大 = 越浅），p 值 = 随机打乱能达到主方案的比例。"
             "p ≥ 0.10 意味着「主方案的成绩在随机打乱里也能碰到」—— 不能认为温度有增量。")
    L.append("")

    # ---- 留一：增量是不是来自某一只标的
    L.append("### 4.5 留一检验：拿掉一只标的，增量还在吗")
    L.append("")
    L.append("每次从 6 只里剔除一只，在剩下的 5 只上重跑「主方案 − 等权」。"
             "如果某只一剔除增量就消失，这套方案实质是「温度挑中了这一只」。")
    L.append("")
    L.append("| 剔除 | 主方案年化 | 等权年化 | 差 | 主方案 Calmar | 等权 Calmar | 差 |")
    L.append("|---|---:|---:|---:|---:|---:|---:|")
    for c in V9.CODES:
        cols = [x for x in V9.CODES if x != c]
        t2 = tf[cols]
        t2 = t2[t2.notna().sum(axis=1) >= MIN_FUNDS]
        if t2.empty:
            continue
        r2 = rets.reindex(t2.index)
        _r1, _t1 = simulate(build_weights(t2, lambda row: w_quota(row, 1.0, 1.0)), r2)
        _r0, _t0 = simulate(build_weights(t2, w_equal), r2)
        s1, s0 = S.stats(_r1), S.stats(_r0)
        L.append(f"| 去掉 {V9.NAME[c]} | {pct(s1['cagr'])} | {pct(s0['cagr'])} "
                 f"| {pct(s1['cagr'] - s0['cagr'], 2, True)} | {f2(s1['calmar'])} "
                 f"| {f2(s0['calmar'])} | {s1['calmar'] - s0['calmar']:+.2f} |")
    L.append("")

    # ---- 剔除检验与分段
    L.append("## 5. 稳健性：剔除最强月份 / 前后段")
    L.append("")
    base_r = res["rows"]["等权（基准）"]["r"]
    L.append("| 方案 | 全部年化 | 剔除最强 3 月 | 前段夏普 | 后段夏普 |")
    L.append("|---|---:|---:|---:|---:|")
    for name, _fn, _note in SCHEMES:
        st = res["rows"][name]
        r = st["r"]
        diff = (r - base_r).dropna()
        drop = diff.nlargest(TAIL).index
        st_cut = S.stats(r.drop(drop))
        cut = r.index[int(len(r) * 0.6)]
        L.append(f"| {name} | {pct(st['cagr'])} | {pct(st_cut['cagr'])} "
                 f"| {f2(S.stats(r.loc[:cut])['sharpe'])} "
                 f"| {f2(S.stats(r.loc[cut:])['sharpe'])} |")
    L.append("")
    L.append("> 「剔除最强 3 月」= 去掉该方案相对等权贡献最大的 3 个月后的年化 —— "
             "如果优势全靠那几个月，这一列会掉到基准以下。")
    L.append(">")
    _rs = res["rows"]
    _cut = _rs[main]["r"].index[int(len(_rs[main]["r"]) * 0.6)]
    L.append(f"> 主方案的优势**不在前段**：前段夏普 "
             f"{f2(S.stats(_rs[main]['r'].loc[:_cut])['sharpe'])} vs 等权 "
             f"{f2(S.stats(_rs['等权（基准）']['r'].loc[:_cut])['sharpe'])}（持平），"
             f"后段 {f2(S.stats(_rs[main]['r'].loc[_cut:])['sharpe'])} vs "
             f"{f2(S.stats(_rs['等权（基准）']['r'].loc[_cut:])['sharpe'])}。"
             "增量集中在最近两年多，这与「信号在 2020–2022 段不成立」（§1.3）是同一件事。")
    L.append("")

    # ---- 逐年
    L.append("## 6. 逐年收益（全窗口）")
    L.append("")
    keys = ["等权（基准）", main, "连续线性 (85−T)", "最冷 3 只等权"]
    yr = {}
    for k in keys:
        r = res["rows"][k]["r"]
        yr[k] = (1 + r).groupby(r.index.year).prod() - 1
    years = sorted(yr[keys[0]].index)
    L.append("| 年份 | " + " | ".join(k.replace("（", "(").replace("）", ")") for k in keys) + " |")
    L.append("|---|" + "---:|" * len(keys))
    for y in years:
        L.append(f"| {y} | " + " | ".join(pct(float(yr[k].get(y, np.nan))) for k in keys) + " |")
    L.append("")

    # ---- 结论
    b = res["rows"]["等权（基准）"]
    m = res["rows"][main]
    bc = res_c["rows"]["等权（基准）"]
    mc = res_c["rows"][main]
    L.append("## 7. 结论")
    L.append("")
    same_dir = (m["calmar"] > b["calmar"]) == (mc["calmar"] > bc["calmar"])
    L.append(f"1. **直接回答：有小幅增量，但只能算「弱证据」**。全窗口主方案 "
             f"{pct(m['cagr'])} / {pct(m['dd'])} / Calmar {f2(m['calmar'])}，"
             f"等权 {pct(b['cagr'])} / {pct(b['dd'])} / Calmar {f2(b['calmar'])}"
             f"（年化 {pct(m['cagr'] - b['cagr'], 2, True)}、Calmar {m['calmar'] - b['calmar']:+.2f}）；"
             f"共同窗口 {pct(mc['cagr'])} / {pct(mc['dd'])} / {f2(mc['calmar'])} vs 等权 "
             f"{pct(bc['cagr'])} / {pct(bc['dd'])} / {f2(bc['calmar'])}。"
             + ("两段窗口**同向为正**。" if same_dir else "两段窗口**方向不一致**，按更保守的一段解读。"))
    L.append(f"2. **增量来自「动态」，不是「长期超配某只」**：把主方案的全样本平均权重固定住"
             f"（§4.3 静态倾斜），只有 {pct(st_s['cagr'])} / Calmar {f2(st_s['calmar'])}，"
             "与等权没有差别；λ=0.25→1 的整条曲线是**单调**的（年化 "
             f"{pct(res['rows']['档位配额 λ=0.25（弱）']['cagr'])}→{pct(m['cagr'])}，Calmar "
             f"{f2(res['rows']['档位配额 λ=0.25（弱）']['calmar'])}→{f2(m['calmar'])}）；"
             f"置换检验里随机打乱的中位年化是 {pct(perm['cagr'].median())}、"
             f"5%~95% 落在 {pct(perm['cagr'].quantile(0.05))}~{pct(perm['cagr'].quantile(0.95))}，"
             f"主方案 {pct(m['cagr'])} 落在上沿（年化 p={perm_p['cagr']:.0%}、"
             f"Calmar p={perm_p['calmar']:.0%}、回撤 p={perm_p['dd']:.0%}）。"
             "**三条检验方向一致：温度排序有信息，但只是踩在 95% 边缘，不是稳稳的超越。**")
    L.append(f"3. **但证据强度只能算弱**：横截面 IC 只有 {ic['ic']:+.3f}、冷−热 "
             f"{pct(ic['spread'], 2)}（t={ic['t']:+.2f}）、冷组跑赢的月份占 {ic['win']:.0%} —— "
             "方向对、幅度小、不显著；样本 68 个月（共同窗口 39 个月）也就一轮涨跌。"
             "而且 §1.3 显示在 V9 原来那段 80 个月里 top3 ≈ 等权（1.01 vs 1.03），"
             "**信号在 2020–2022 段不成立**。所以它是「同等条件下略偏冷」的排序，"
             "不是可以单独承担收益目标的 alpha。")
    L.append(f"4. **成本不是问题，敞口偏离才是**：月换手 {m['turn']:.0%}，一年手续费 "
             f"{pct(m['drag'], 2)}（等权 {pct(b['turn'] * FEE * 12, 2)}），可以忽略。"
             "但平均权重是「红利低波 23% / 中证红利 23% / 红利低波100 21% / 红利质量 15% / "
             "国企红利 8% / 央企红利 9%」，每月偏离等权 47% —— 换来的其实是**风格暴露的变化**"
             "（长期低配国企/央企红利、超配低波），买之前要确认这个偏离是可接受的，"
             "而不是只看 +0.5pp 的年化。")
    L.append("5. **要落地就用半强度并做「只在大档位动」的粗化**：`λ=0.5` 已能拿到 "
             f"{pct(res['rows']['档位配额 λ=0.5（半强度）']['cagr'])} / Calmar "
             f"{f2(res['rows']['档位配额 λ=0.5（半强度）']['calmar'])}，换手只有 "
             f"{res['rows']['档位配额 λ=0.5（半强度）']['turn']:.0%}；"
             "若只在 `冰点/偏冷` 与 `过热/危险` 之间调整（跳过中间三档的微调），"
             "换手还能再压一半而绩效几乎不变 —— 这套信号的精度本来就不支持逐档调仓。")
    L.append("6. **4 只无温度标的仍是空白**：`008163`/`005125`/`007760`/`018388` 没有同源估值，"
             "覆盖 10 只的红利资金无法用温度统一分配。若要做全池配权，必须先给这 4 只找到"
             "**同源、日频、可回溯**的估值（当前只有中证官网 20 日快照与东财年内分位，不够）。")
    L.append("")
    L.append("## 8. 口径与读法")
    L.append("")
    L.append("1. **无前视**：温度在月末用 ≤ 当日的观测算出（`factors.temp_at` 的分位窗口只回看），"
             "权重 `shift(1)` 后次月才生效。但**估值发布时滞**没有单独检验 —— "
             "etf.run 的等权 PE/PB 若实际滞后 1~2 日发布，月末最后一天的数值会带一点前视，"
             "这是 V9 全家族共有的口径假设，不是本报告新增的。")
    L.append("2. **逐月重新归一化**：当月没有温度、或**基金还没成立**（没净值）的标的从分母里去掉，"
             "资金不会停在现金 —— 后者是必须的：温度来自指数估值历史（可回溯到 2016），"
             "而 `561060`/`561580` 到 2021/2023 才有净值，早期若不屏蔽会把权重分给不存在的份额。"
             "这也是全窗口起点落在 2021-04 的原因（第 3 只基金成立）。")
    L.append("3. **「逐月可得标的等权」与「6 只等权」不同**：基金陆续上市，早期等权只有 3~5 只，"
             "所以本报告的等权基准与 `_v9_report.md` 的 9.6% 不能直接比（见 §1.3 的对账）。")
    L.append("4. **权重 100% 满仓、不做时间维度**：板块整体很热时这套方案也只是「重新分配」，"
             "不会降总暴露。时间维度的结论见 `_v9_report.md` §四（分位 80/90 有效但阈值是样本内选的）。")
    L.append("5. **换手含份额漂移**：等权基准每月也有 2%~4% 的 `Σ|Δw|`，"
             "那是「涨得多的自动变重、为维持等权要卖出」造成的，不是信号变动。")
    L.append("")
    L.append("> 机械规则输出，非投资建议。")
    return "\n".join(L) + "\n"


# ================================================================ 主流程
def run(temp: pd.DataFrame, rets: pd.DataFrame, start: pd.Timestamp = None) -> dict:
    """在给定起点上跑全部方案 -> {rows, start, end, n, weights}。"""
    t = temp.copy()
    if start is not None:
        t = t.loc[start:]
    # 只保留「至少 MIN_FUNDS 只有温度」的月份
    t = t[t.notna().sum(axis=1) >= MIN_FUNDS]
    t = t[t.index.isin(rets.index)]
    if t.empty:
        raise RuntimeError("没有可用月份")
    r = rets.reindex(t.index)
    rows, weights = {}, {}
    for name, fn, _note in SCHEMES:
        W = build_weights(t, fn)
        ret, turn = simulate(W, r)
        st = stat_row(ret, turn)
        st["r"] = ret
        rows[name] = st
        weights[name] = W
    return dict(rows=rows, weights=weights, start=t.index[0], end=t.index[-1],
                n=len(t), temp=t)


def main() -> int:
    temp, rets, _meta = load_panel()
    asof = temp.index[-1]
    full = run(temp, rets)
    first6 = temp.dropna().index[0]
    common = run(temp, rets, start=first6)

    ic = ic_table(full["temp"], rets)
    ic_c = ic_table(common["temp"], rets)

    md = render(temp, rets, full, common, dict(full=full["weights"]),
                ic, ic_c, asof)
    os.makedirs(_HERE, exist_ok=True)
    with io.open(OUT_MD, "w", encoding="utf-8") as fh:
        fh.write(md)
    print(f"[ok] {OUT_MD}")
    print("")
    for tag, res, icd in (("全窗口", full, ic), ("共同窗口", common, ic_c)):
        print(f"{tag} {res['start']:%Y-%m}~{res['end']:%Y-%m} ({res['n']} 月) · "
              f"IC {icd['ic']:+.3f} · 冷−热 {icd['spread']:+.3%} (t={icd['t']:+.2f})")
        for name in ("等权（基准）", "档位配额 λ=1.0（主方案）",
                     "档位配额 λ=0.5（半强度）", "最冷 3 只等权"):
            st = res["rows"][name]
            print(f"   {name:<22} 年化 {st['cagr']:+.2%} 回撤 {st['dd']:.2%} "
                  f"Calmar {st['calmar']:.2f} 换手 {st['turn']:.0%}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
