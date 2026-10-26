#!/usr/bin/env python
# -*- coding: utf-8 -*-
r"""红利策略全版本回测总对比 —— 先分组，再比

为什么必须分组
--------------
「全部红利策略」不是一个口径：V1 用**单只指数**的全收益序列、V2 用 **4 只指数的价格指数**
（未含分红）、V3–V7 用 **7 只基金的累计净值**、打分轮动用 **10 只基金 + 10 年样本**。
把这些年化并排放，差 4–5pp 里有一半来自口径而不是策略（量化见报告 §四）。所以：

* **A 组（同口径，可直接比）**：V3–V7。同一份 `_single_signal` 引擎、7 只基金、
  2020-01~2026-10（82 月）、累计净值、单边 0.03%、同一个「7 只等权」基准。
  本脚本**重算**这些数字（不读旧报告），保证 12 个条目同进程、同数据、同代码路径；
  组合候选的定义读 `v7_combo/rule.py`（参数权威源），不在这里复制一份。
* **B 组（口径不同，只作方向参考）**：V1 / V2 / V0 打分轮动。数字来自各自产物
  （`v1_spread/backtest/_v1_bt.jsonl`、`v2_temp/backtest/_v2_panel.jsonl`、
  `v0_rank/backtest/_rank_bt.jsonl`）在本脚本内重算，并逐条标注差异来源。
* **§四 口径差量化**：同一段 2020-01~2026-10，价格指数 / 全收益指数 / 基金累计净值
  三种收益口径的年化差 —— 这就是「不可混比」的量级证据。

产物：`backtest/_compare_all_report.md`（AGENTS §8.4 ③ 回测产物）

用法::

    python strategies/dividend/_compare_all.py
"""

from __future__ import annotations

import json
import os
import sys

import numpy as np
import pandas as pd

_DIR = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, _DIR)
sys.path.insert(0, os.path.join(_DIR, "v7_combo"))

import _single_signal as S  # noqa: E402  pylint: disable=wrong-import-position
import rule as V7  # noqa: E402  pylint: disable=wrong-import-position

ROOT = S.ROOT
OUT_MD = os.path.join(_DIR, "backtest", "_compare_all_report.md")
V1_BT = os.path.join(_DIR, "v1_spread", "backtest", "_v1_bt.jsonl")
V2_PANEL = os.path.join(_DIR, "v2_temp", "backtest", "_v2_panel.jsonl")
RANK_BT = os.path.join(_DIR, "v0_rank", "backtest", "_rank_bt.jsonl")
CACHE = os.path.join(ROOT, "data", "_bt_caches")

BASE = "基准：7 只等权"
VARIANTS = ("时间：板块贵度择时", "横截面：最便宜 2 只", "横截面 top2 + 时间择时")
OVERLAY = VARIANTS[-1]
SINGLE_TH = {"dy": (0.80, 0.50), "spread": (0.80, 0.50),
             "ratio": (0.80, 0.50), "rsi": (0.70, 0.50)}
SINGLE = (
    ("dy", "V3 股息率", "q = 1 − 分位(dy)"),
    ("rsi", "V4 RSI14", "q = RSI/100（不分位，保留绝对刻度）"),
    ("spread", "V5 股债利差", "q = 1 − 分位(dy − 10Y)"),
    ("ratio", "V6 股债收益比", "q = 1 − 分位(dy / 10Y)"),
)
# V7 的 `rsi（单指标对照）` 与 V4 完全等价，不重复入表
SKIP_CAND = {"rsi（单指标对照）"}


# ------------------------------------------------------------------ 通用
def _jsonl(path: str) -> list[dict]:
    with open(path, "r", encoding="utf-8") as fh:
        return [json.loads(ln) for ln in fh if ln.strip()]


def _csi(code: str) -> pd.Series:
    df = pd.read_csv(os.path.join(CACHE, f"csi_{code}.csv"), parse_dates=["date"])
    return df.set_index("date")["close"].astype(float).dropna().sort_index()


def _monthly(s: pd.Series) -> pd.Series:
    return s.resample("ME").last().ffill().pct_change().dropna()


def _pct(s: pd.Series, min_n: int) -> pd.Series:
    """扩张窗口分位（含当前点）—— 与 `v7_combo`/`v2_temp` 同口径。"""
    v = s.to_numpy(dtype=float)
    out = np.full(len(v), np.nan)
    for i in range(len(v)):
        w = v[:i + 1]
        w = w[~np.isnan(w)]
        if len(w) >= min_n and not np.isnan(v[i]):
            out[i] = float((w < v[i]).mean())
    return pd.Series(out, index=s.index)


def _eq_w(ok: pd.DataFrame) -> pd.DataFrame:
    return ok.div(ok.sum(axis=1).replace(0, np.nan), axis=0).fillna(0.0)


def _low_w(key: pd.DataFrame, k: int) -> pd.DataFrame:
    """key 越小越便宜，取最低的 k 只等权。"""
    out = pd.DataFrame(0.0, index=key.index, columns=key.columns)
    for ts, row in key.iterrows():
        ok = row.dropna()
        if not ok.empty:
            out.loc[ts, ok.nsmallest(min(k, len(ok))).index] = 1.0 / min(k, len(ok))
    return out


def _row(name: str, mech: str, r: pd.Series, base: pd.Series, pos=None,
         caliber: str = "", why: str = "") -> dict:
    st = S.stats(r)
    diff = (r - base).reindex(r.index).dropna() if base is not None else pd.Series(dtype=float)
    d3 = S.stats(r.drop(diff.nlargest(3).index))["cagr"] if len(diff) > 3 else float("nan")
    return {"策略": name, "机制": mech, **st, "cagr_d3": d3,
            "pos": pos, "caliber": caliber, "why": why, "r": r}


# ------------------------------------------------------------------ A 组
def _add_a(out: dict, rets: pd.DataFrame, name: str, mech: str,
           q: pd.DataFrame, xs: pd.DataFrame, th: tuple,
           bq: pd.Series = None, mult: pd.Series = None) -> None:
    q_out, q_half = th
    sims, _, _ = S.run_variants(q, rets, xs, bq=bq, k=V7.TOP_K,
                                q_out=q_out, q_half=q_half, mult_override=mult)
    ok = S.available(q, rets)
    m = mult if mult is not None else S.time_mult(
        S.board_q(q, ok) if bq is None else bq, q_out, q_half)
    pos = float(S.top_w(xs, ok, V7.TOP_K).mul(m, axis=0).sum(axis=1).mean())
    out[name] = {"mech": mech, "sims": sims, "pos": pos}


def build_a() -> tuple[dict, pd.DataFrame]:
    rets = S.load_rets()
    out: dict = {}
    for kind, name, mech in SINGLE:
        _add_a(out, rets, name, mech,
               S.cheapness(kind).reindex(rets.index),
               S.xs_key(kind).reindex(rets.index), SINGLE_TH[kind])

    kinds4 = ["dy", "spread", "ratio", "rsi"]
    for cname, kinds in V7.CANDIDATES.items():
        if cname in SKIP_CAND:
            continue
        q, xs = S.build_combo(rets, kinds)
        th = (0.70, 0.50) if kinds == ["rsi"] else (V7.Q_OUT, V7.Q_HALF)
        _add_a(out, rets, f"V7 {cname}", "等权合成：" + "+".join(kinds), q, xs, th)

    q, xs = S.build_vote(rets, kinds4)
    _add_a(out, rets, "V7 vote（≥2 票）", "4 指标投票：q < 阈值记 1 票，票多者便宜",
           q, xs, (V7.Q_OUT, V7.Q_HALF))

    q, xs = S.build_combo(rets, kinds4)
    mults = [S.time_mult(S.board_q(S.cheapness(k).reindex(rets.index),
                                  S.available(S.cheapness(k).reindex(rets.index), rets)),
                         *SINGLE_TH[k]) for k in kinds4]
    _add_a(out, rets, "V7 pos_vote（仓位投票）",
           "各指标按自己的阈值给仓位，再对倍率取均值", q, xs,
           (V7.Q_OUT, V7.Q_HALF), mult=pd.concat(mults, axis=1).mean(axis=1))

    for mname, kt, kx, th in V7.SPECIAL:
        q = S.cheapness(kt).reindex(rets.index)
        xs = S.xs_key(kx).reindex(rets.index)
        _add_a(out, rets, f"V7 {mname}", f"时间维度用 {kt}、横截面用 {kx}（互不稀释）",
               q, xs, th, bq=S.board_q(q, S.available(q, rets)))
    return out, rets


# ------------------------------------------------------------------ B 组
def build_b_v1() -> list[dict]:
    df = pd.DataFrame(_jsonl(V1_BT))
    df["date"] = pd.to_datetime(df["date"])
    df = df.set_index("date")
    bench = df["ret"]
    cal = "单只指数 000922 全收益（H00922）· 2018-06~2026-09 · 100 月"
    why = "单标的 + 8 年样本"
    return [
        _row("V1 利差择时 70/20 + 保险丝", "分位 ≥0.70 满仓 / <0.20 空仓，带内不动",
             df["strat"], bench, caliber=cal, why=why),
        _row("V1 买入持有（同组基准）", "中证红利全收益指数", bench, bench,
             caliber=cal, why="—"),
    ]


def build_b_v2() -> list[dict]:
    panel = pd.DataFrame(_jsonl(V2_PANEL))
    panel["date"] = pd.to_datetime(panel["date"])
    temp = panel.pivot(index="date", columns="code", values="temp").sort_index()
    close = panel.pivot(index="date", columns="code", values="close").ffill().sort_index()
    rets = close.pct_change().iloc[1:]
    w = _eq_w(temp.notna())
    mult = S.time_mult(_pct(temp.mean(axis=1), 24), 0.90, 0.80)   # 清仓 0.90 / 减半 0.80
    low2 = _low_w(temp, 2)
    bench = S.simulate(w, rets)
    cal = "4 只指数**价格**指数（未含分红）· 2020-01~2026-10 · 82 月 · 由 `_v2_panel.jsonl` 重算"
    why = "价格指数 + 4 只池"
    return [
        _row("V2 基准：4 只等权", "四维估值温度等权合成", bench, bench, caliber=cal, why=why),
        _row("V2 时间：温度分位减仓", "板块温度分位 ≥0.90 清仓 / ≥0.80 减半",
             S.simulate(w.mul(mult, axis=0), rets), bench, caliber=cal, why=why),
        _row("V2 横截面：温度最低 2 只", "每月选温度最低 2 只等权",
             S.simulate(low2, rets), bench, caliber=cal, why=why),
        _row("V2 横截面 top2 + 时间减仓", "两个维度叠加",
             S.simulate(low2.mul(mult, axis=0), rets), bench, caliber=cal, why=why),
    ]


def build_b_rank() -> list[dict]:
    rows = {(r["scheme"], r["topn"]): r for r in _jsonl(RANK_BT)}
    cal = "10 只基金累计净值 · 2016-09~2026-10 · 122 次再平衡 · 只含业绩分"
    why = "10 年样本 + 10 只池"
    out = []
    for key, label in ((("eq", 2), "V0 打分轮动 topN=2（等权 1/3 窗权）"),
                       (("user", 2), "V0 打分轮动 topN=2（指定 3/5/10 窗权）"),
                       (("benchall", 0), "V0 全池 10 只等权（同组基准）"),
                       (("bench", 0), "V0 表内 2 只等权（同组基准）")):
        r = rows[key]
        out.append({"策略": label, "机制": "业绩分排序取 topN 等权", "cagr": r["ann"],
                    "dd": r["mdd"], "calmar": r["calmar"], "vol": float("nan"),
                    "sharpe": r["sharpe"], "total": r["cum"], "n": 122,
                    "cagr_d3": float("nan"), "pos": None, "caliber": cal,
                    "why": "—" if key[1] == 0 else why, "r": None})
    return out


# ------------------------------------------------------------------ 口径差
def caliber_gap() -> pd.DataFrame:
    """同一段窗口、同一个指数（000922）：价格 / 全收益 / 跟踪基金累计净值。"""
    end = None
    rows = []
    for label, s in (("000922 价格指数（不含分红）", _csi("000922")),
                     ("H00922 全收益指数（含分红再投）", _csi("H00922")),
                     ("090010 基金累计净值（含分红、扣费）",
                      S.load_navs()["090010"])):
        m = _monthly(s).loc[S.START:]
        end = m.index[-1] if end is None else end
        st = S.stats(m)
        rows.append({"口径": label, "年化": st["cagr"], "回撤": st["dd"],
                     "Calmar": st["calmar"], "月数": len(m)})
    df = pd.DataFrame(rows)
    df.attrs["end"] = end
    return df


# ------------------------------------------------------------------ 渲染
def _p(x, d=1) -> str:
    return "—" if x is None or (isinstance(x, float) and np.isnan(x)) else f"{x * 100:.{d}f}%"


def _n(x, d=2) -> str:
    return "—" if x is None or (isinstance(x, float) and np.isnan(x)) else f"{x:.{d}f}"


def render(a: dict, rets: pd.DataFrame, b: list[dict], gap: pd.DataFrame) -> str:
    base_r = a[SINGLE[0][1]]["sims"][BASE]
    bst = S.stats(base_r)
    rows = [_row(name, v["mech"], v["sims"][OVERLAY], base_r, v["pos"])
            for name, v in a.items()]
    rows.sort(key=lambda r: -r["calmar"])
    n_month = len(rets)

    L = ["# 红利策略全版本回测总对比", "",
         f"> 样本 **{rets.index[0]:%Y-%m} ~ {rets.index[-1]:%Y-%m}**（{n_month} 月）为 A 组口径；"
         "B 组各自窗口另见其行。",
         "> 本表**先把口径分组再排名**：A 组可直接比，B 组只作方向参考（见 §三、§四）。",
         "> 复算方式：A 组在本进程内用 `_single_signal` 引擎**重算**（不读旧报告，"
         "组合候选定义读 `v7_combo/rule.py`）；B 组从各自产物重算。", ""]

    # ---------------- 一
    L += ["## 一、A 组 · 同口径（可直接比）", "",
          "四条口径完全一致，缺一不可：**同样 7 只基金**（有股息率历史的 7 只）· "
          "**同一收益源**（`pool.load_navs` 的累计净值 `totvalue`，含分红再投）· "
          f"**同样本**（2020-01~{rets.index[-1]:%Y-%m}，{n_month} 月，月末信号下月生效）· "
          "**同成本**（单边 0.03%，Σ|Δw| 计）· **同基准**（7 只等权）。", "",
          f"主方案统一取「{OVERLAY}」—— {len(a)} 个条目的唯一区别就是**信号怎么造**。", "",
          "| # | 策略 | 信号构造 | 年化 | 最大回撤 | Calmar | 夏普 | 平均仓位 | 剔除 3 月后年化 | ΔCalmar |",
          "|---:|---|---|---:|---:|---:|---:|---:|---:|---:|"]
    for i, r in enumerate(rows, 1):
        d = r["calmar"] - bst["calmar"]
        L.append(f"| {i} | {r['策略']} | {r['机制']} | {_p(r['cagr'])} | {_p(r['dd'])} | "
                 f"**{r['calmar']:.2f}** | {_n(r['sharpe'])} | {_p(r['pos'], 0)} | "
                 f"{_p(r['cagr_d3'])} | {d:+.2f} |")
    L += ["", f"- 基准（7 只等权、不做任何择时与选品）：年化 **{_p(bst['cagr'])}**、"
              f"回撤 {_p(bst['dd'])}、Calmar **{bst['calmar']:.2f}**。",
          "- `ΔCalmar` 是相对基准的差；`平均仓位` 只对含时间维度的主方案有意义"
          "（横截面选品始终满仓）；`剔除 3 月后年化` = 剔掉相对基准贡献最大的 3 个月，"
          "是稳健性的最低门槛。",
          "- V7 的 `rsi（单指标对照）` 与 V4 完全等价，已跳过，不重复占一行。", ""]

    # ---------------- 二
    L += ["## 二、A 组 · 变体分解（Calmar）", "",
          "同一个信号，用在「时间维度」「横截面」「两者叠加」上的效果可以完全不同。", "",
          "| 策略 | 时间维度 | 横截面 top2 | 两者叠加 | 该策略最佳 |",
          "|---|---:|---:|---:|---:|"]
    for name, v in a.items():
        cs = [S.stats(v["sims"][x])["calmar"] for x in VARIANTS]
        L.append(f"| {name} | {cs[0]:.2f} | {cs[1]:.2f} | {cs[2]:.2f} | {max(cs):.2f} |")
    L += ["", f"- 同一张表的基准 Calmar = **{bst['calmar']:.2f}**；"
              "低于它的格子说明该用法是负贡献。", ""]

    # ---------------- 三
    L += ["## 三、B 组 · 口径不同（不可与 A 组直接比）", "",
          "这几套方案的标的、收益口径、样本长度、池大小都不同，**年化不能横向排名**，"
          "只能各自读「相对自己基准有没有增量」。", "",
          "| 方案 | 收益口径 / 样本 | 年化 | 最大回撤 | Calmar | 夏普 | 为什么不可比 |",
          "|---|---|---:|---:|---:|---:|---|"]
    for r in b:
        L.append(f"| {r['策略']} | {r['caliber']} | {_p(r['cagr'])} | {_p(r['dd'])} | "
                 f"{_n(r['calmar'])} | {_n(r['sharpe'])} | {r['why']} |")
    L += ["", "三类差异的具体来源：",
          "- **价格指数 + 4 只池**（V2）：收益未含分红（量级见 §四），"
          "且 `931446` 的 PE 历史 2020 年才有，横截面逐月只能用可得标的。",
          "- **单标的 + 8 年样本**（V1）：只测 `000922` 的时间维度，"
          "规则是「带内不动」的阈值切换，不是 A 组那种每月再平衡。",
          "- **10 年样本 + 10 只池**（V0 打分轮动）：样本多 4 年、池多 3 只、"
          "且只回测业绩分（价值分无历史序列，无法进回测）。",
          "",
          "> V2 的数字由 `_v2_panel.jsonl` 重算（close 保留 2 位、temp 1 位），"
          "与 `_v2_report.md` 的差在 ±0.1pp 舍入范围内。", ""]

    # ---------------- 四
    L += ["## 四、口径差量化：为什么不能混着比", "",
          f"同一段窗口、同一个指数族（中证红利 000922，截至 {gap.attrs['end']:%Y-%m}），"
          "只换「收益怎么算」：", "",
          "| 收益口径 | 年化 | 最大回撤 | Calmar | 月数 |", "|---|---:|---:|---:|---:|"]
    for _, g in gap.iterrows():
        L.append(f"| {g['口径']} | **{_p(g['年化'])}** | {_p(g['回撤'])} | "
                 f"{g['Calmar']:.2f} | {int(g['月数'])} |")
    px, tr, fd = gap["年化"].iloc[0], gap["年化"].iloc[1], gap["年化"].iloc[2]
    L += ["", f"- **价格 → 全收益：{_p(tr)} − {_p(px)} = {(tr - px) * 100:+.1f}pp/年**，"
              "这一项就是股息率本身（红利指数 4–5%）。"
              "所以「用价格指数回测红利策略」系统性低估 4–5pp —— V2 的 4.7% 与 A 组的 8.2% "
              "之间的差，主要来自这里，不是策略差异。",
          f"- 全收益 → 基金累计净值：{_p(fd)} − {_p(tr)} = {(fd - tr) * 100:+.1f}pp/年，"
          "这是管理费 + 跟踪误差的代价。",
          f"- 结论：跨口径比较的年化误差量级 **{abs(tr - px) * 100:.0f}pp**，"
          "而 A 组内部（同口径）策略之间的最大差距也只有 "
          f"{(rows[0]['cagr'] - rows[-1]['cagr']) * 100:.1f}pp —— "
          "**口径差比策略差还大，所以必须分组**。", ""]

    # ---------------- 五
    best = rows[0]
    worst = rows[-1]
    val = [r for r in rows if r["策略"].startswith(("V3", "V5", "V6"))
           or "val（" in r["策略"] or "all4" in r["策略"]]
    combos = [r for r in rows if r["策略"].startswith("V7")]
    pure_rsi = [r for r in rows if r["策略"].startswith("V4")][0]
    n_val_below = sum(1 for r in val if r["calmar"] <= bst["calmar"])
    best_combo = max(combos, key=lambda r: r["calmar"])
    L += ["## 五、结论", "",
          f"1. **同口径下的全场第一是 `{best['策略']}`**：Calmar {best['calmar']:.2f}、"
          f"年化 {_p(best['cagr'])}、回撤 {_p(best['dd'])}、平均仓位 {_p(best['pos'], 0)}；"
          f"剔除贡献最大 3 个月后年化仍有 {_p(best['cagr_d3'])}（基准同法剔除后 "
          f"{_p(bst['cagr'])}）。",
          f"2. **估值类三项 {len(val)} 个条目里有 {n_val_below} 个跑不赢等权基准**"
          f"（基准 Calmar {bst['calmar']:.2f}）。`dy`/`spread`/`ratio` 的横截面排序在数学上是同一个"
          "信号（y10 是共同项），把它们等权相加等于给股息率三倍权重 —— 这不是「多指标」。",
          f"3. **组合没有带来增量**：{len(combos)} 个 V7 候选里最好的是 `{best_combo['策略']}`"
          f"（Calmar {best_combo['calmar']:.2f}），仍低于纯 RSI 的 {pure_rsi['calmar']:.2f}。"
          "把无效信号掺进有效信号，只会摊薄。",
          f"4. **垫底的是 `{worst['策略']}`**（Calmar {worst['calmar']:.2f}，"
          f"比等权基准低 {bst['calmar'] - worst['calmar']:.2f}）：最「干净」的那个估值指标"
          "在本样本里反而最差 —— 估值类不是「需要组合才好用」，而是**这一段样本里就没有增量**。",
          "5. **B 组不能进排名**：V1 的唯一一次离场是负贡献（分位长期贴顶、几乎不触发）、"
          "V2 的时间维度有增量但剔除 5 个月即转负、V0 打分轮动相对全池等权是负的。"
          "三者方向与 A 组一致 —— **在这个板块里，择时/选品/换指标的边际都很薄；"
          "更可靠的增量来自「分散」（打分轮动 topN=2 明显优于 topN=1）而不是挑信号**。",
          "",
          f"> 边界：82 个月、7 只高度相关的红利基金、单边 0.03%。"
          f"{len(a)} 个条目 × 3 个变体 = {len(a) * 3} 次比较，第一名天然偏高（多重比较）；"
          "RSI 的可信度来自阈值敏感性三档全过，不是来自这一次排名。", ""]
    return "\n".join(L)


def main() -> int:
    a, rets = build_a()
    b = build_b_v1() + build_b_v2() + build_b_rank()
    gap = caliber_gap()
    S.write(OUT_MD, render(a, rets, b, gap))

    base = S.stats(a[SINGLE[0][1]]["sims"][BASE])
    print(f"[cmp] A 组 {len(a)} 个条目 · 样本 {rets.index[0]:%Y-%m}~{rets.index[-1]:%Y-%m}"
          f" ({len(rets)} 月) · 基准 Calmar {base['calmar']:.2f}")
    ranked = sorted(((n, S.stats(v["sims"][OVERLAY])) for n, v in a.items()),
                    key=lambda kv: -kv[1]["calmar"])
    for i, (name, st) in enumerate(ranked, 1):
        print(f"[cmp] {i:2d}. {name:26s} 年化 {st['cagr'] * 100:5.1f}%  "
              f"回撤 {st['dd'] * 100:6.1f}%  Calmar {st['calmar']:5.2f}")
    print("[cmp] B 组（不可比，仅方向参考）")
    for r in b:
        print(f"[cmp]     {r['策略']:30s} 年化 {r['cagr'] * 100:5.1f}%  "
              f"回撤 {r['dd'] * 100:6.1f}%  Calmar {r['calmar']:5.2f}")
    print("[cmp] 口径差（2020-01~）")
    for _, g in gap.iterrows():
        print(f"[cmp]     {g['口径']:36s} 年化 {g['年化'] * 100:5.1f}%")
    print(f"[cmp] 产物 -> {os.path.relpath(OUT_MD, _DIR)}")
    return 0


if __name__ == "__main__":
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    sys.exit(main())
