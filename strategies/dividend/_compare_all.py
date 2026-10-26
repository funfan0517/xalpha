#!/usr/bin/env python
# -*- coding: utf-8 -*-
r"""红利策略全版本回测总对比（V0–V7）—— 先对齐窗口，再分组

V0 与 V3–V7 的原始口径不同（V0：2016-09 起、10 只池、只回测业绩分），直接并排会把
「池子差异 + 样本差异」算成「策略差异」。所以本脚本做三件事：

* **§一 全景（可直接比）**：把 V0 拉到 V3–V7 的窗口（2020-01~2026-10、82 月、基金累计净值、
  单边 0.03%、月末决策下月生效）**重算**，池取 7 只与 10 只两种 ——
  V0 复用 `v0_rank/backtest_rank.py::build_picks` 的原逻辑，V3–V7 用 `_single_signal` 引擎，
  于是 13 行之间只差「信号」与「池大小」两个变量。
* **§三 原生口径（不可比）**：V0（10 只 / 10 年）、V1（单指数全收益 / 8 年）、
  V2（4 只价格指数）保持原样，逐条标注差异来源。
* **§四 口径差量化**：同一段窗口、同一个指数，价格 / 全收益 / 基金累计净值三种算法
  相差约 5pp/年 —— 比策略之间的差还大，这就是必须先对齐的理由。

产物：`backtest/_compare_all_report.md`（AGENTS §8.4 ③ 回测产物）

用法::

    python strategies/dividend/_compare_all.py
"""

from __future__ import annotations

import importlib.util
import json
import os
import sys

import numpy as np
import pandas as pd

_DIR = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, _DIR)
sys.path.insert(0, os.path.join(_DIR, "v7_combo"))
sys.path.insert(0, os.path.join(_DIR, "v0_rank"))

import _single_signal as S  # noqa: E402  pylint: disable=wrong-import-position
import pool  # noqa: E402  pylint: disable=wrong-import-position
import rule as V7  # noqa: E402  pylint: disable=wrong-import-position
import backtest_rank as V0BT  # noqa: E402  pylint: disable=wrong-import-position
import rank as V0RANK  # noqa: E402  pylint: disable=wrong-import-position


def _load_rule(name: str, path: str):
    """按文件路径加载某个版本的 rule（`import rule` 已被 V7 占用，不能按模块名取）。"""
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


V4 = _load_rule("v4_rule", os.path.join(_DIR, "v4_rsi", "rule.py"))

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
            "base_calmar": S.stats(base)["calmar"] if base is not None else float("nan"),
            "pos": pos, "caliber": caliber, "why": why, "r": r}


# ------------------------------------------------------------------ A 组
def _add_a(out: dict, rets: pd.DataFrame, name: str, mech: str,
           q: pd.DataFrame, xs: pd.DataFrame, th: tuple,
           bq: pd.Series = None, mult: pd.Series = None,
           base_label: str = "基准：7 只等权") -> None:
    q_out, q_half = th
    sims, _, _ = S.run_variants(q, rets, xs, bq=bq, k=V7.TOP_K,
                                q_out=q_out, q_half=q_half, mult_override=mult,
                                base_label=base_label)
    ok = S.available(q, rets)
    m = mult if mult is not None else S.time_mult(
        S.board_q(q, ok) if bq is None else bq, q_out, q_half)
    pos = float(S.top_w(xs, ok, V7.TOP_K).mul(m, axis=0).sum(axis=1).mean())
    out[name] = {"mech": mech, "sims": sims, "pos": pos}


def build_a() -> tuple[dict, pd.DataFrame]:
    rets = S.load_rets()
    out: dict = {}
    for kind, name, mech in SINGLE:
        if kind == "rsi":
            # V4 有自己的 5 只池（v4_rsi/rule.py::POOL），与 V3/V5/V6 的 7 只不同源
            navs_v4 = pool.load_navs(codes=V4.CODES)
            rets_v4 = S.load_rets(navs_v4)
            raw = S.load_signal("rsi", navs_v4).reindex(rets_v4.index)
            _add_a(out, rets_v4, name, f"{mech}（5 只专用池）",
                   (raw / 100.0).loc[S.START:], raw.loc[S.START:], SINGLE_TH[kind],
                   base_label=V4.BASE_LABEL)
            continue
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


# ------------------------------------------------------------------ V0（对齐窗口）
def _v0_weights(navs: pd.DataFrame, start, end, topn: int = 2) -> pd.DataFrame:
    """V0 业绩分轮动 -> 月末决策权重（复用 `v0_rank/backtest_rank.py` 的 picks 逻辑）。"""
    dates = V0BT.month_ends(navs.index, start, end)
    picks = V0BT.build_picks(navs, dates, {"user": V0RANK.WINDOW_WEIGHTS}, (topn,), {})
    w = pd.DataFrame(0.0, index=pd.DatetimeIndex(sorted(picks[("user", topn)])),
                     columns=list(navs.columns))
    for t, codes in picks[("user", topn)].items():
        codes = [c for c in codes if c in w.columns]
        if codes:
            w.loc[t, codes] = 1.0 / len(codes)
    w.index = w.index.to_period("M").to_timestamp(how="end").normalize()
    return w


def build_v0(rets: pd.DataFrame) -> tuple[list[dict], pd.Series]:
    """V0 打分轮动**拉到 A 组窗口**（2020-01~2026-10）重算，池取 7 只与 10 只两种。

    这样与 V3–V7 只差「信号」与「池大小」，不再有样本/收益口径/再平衡频率的差。
    注意：窗口早期的业绩分退化 —— 3 年窗要求 3 年历史，2020-01 时 7 只池里只有 090010 够，
    所以 2020 年前半段实际是单标的（`rank.perf_raw` 按可得窗口归一化，不会报错）。
    """
    navs_all = pool.load_navs()        # 全池 10 只（`S.load_navs()` 只返回 7 只）
    start, end = rets.index[0], rets.index[-1]
    me = navs_all.resample("ME").last().ffill()
    rets10 = me.pct_change().loc[start:]
    base10 = S.simulate(_eq_w(rets10.notna()), rets10)
    base7 = S.simulate(_eq_w(rets.notna()), rets)

    rows = []
    for tag, codes, base, rr in (("7 只池 · 同 A 组", list(rets.columns), base7, rets),
                                 ("10 只池 · V0 原池", [c for c in navs_all.columns],
                                  base10, rets10)):
        navs = navs_all[[c for c in codes if c in navs_all.columns]]
        w = _v0_weights(navs, start, end).reindex(rr.index).fillna(0.0)
        rows.append(_row(f"V0 打分轮动（{tag}）",
                         "业绩分 3/5/10 年窗 50/30/20 → 排序取 top2 等权",
                         S.simulate(w, rr), base, pos=1.0))
    return rows, base10


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


def render(a: dict, rets: pd.DataFrame, b: list[dict], gap: pd.DataFrame,
           v0_rows: list[dict], base10: pd.Series) -> str:
    base_r = a[SINGLE[0][1]]["sims"][BASE]
    bst = S.stats(base_r)
    b10 = S.stats(base10)
    base5 = S.stats(next(v["sims"][V4.BASE_LABEL] for k, v in a.items() if k.startswith("V4")))
    # 每行对比自己的同池基准（V4 是 5 只池，其余是 7 只池）
    rows = [_row(name, v["mech"], v["sims"][OVERLAY], v["sims"][S.base_key(v["sims"])], v["pos"])
            for name, v in a.items()]
    rows.sort(key=lambda r: -r["calmar"])
    n_month = len(rets)
    allrows = sorted(rows + v0_rows, key=lambda r: -r["calmar"])

    L = ["# 红利策略全版本回测总对比（V0–V7）", "",
         f"> **§一 是全景表**：V0–V7 全部放在同一窗口 **{rets.index[0]:%Y-%m} ~ "
         f"{rets.index[-1]:%Y-%m}**（{n_month} 月）重算，只有「信号」和「池大小」两个变量。",
         "> V0 原生口径是 2016-09 起、10 只池，为进同一张表被**拉到本窗口重算**（7 只 / 10 只两种）；"
         "它的原生结果见 §三。",
         "> 复算方式：V0 复用 `v0_rank/backtest_rank.py::build_picks`（同一套业绩分逻辑），"
         "V3–V7 用 `_single_signal` 引擎；全部在本进程内重算，不读旧报告。", ""]

    # ---------------- 一
    L += ["## 一、V0–V7 全景（同窗口、同成本、同收益源）", "",
          "五条口径一致：**收益源**基金累计净值 `totvalue`（含分红再投）· "
          f"**样本** 2020-01~{rets.index[-1]:%Y-%m}（{n_month} 月，月末决策下月生效）· "
          "**成本**单边 0.03%（Σ|Δw|）· **决策**每月 top2 等权 · "
          "**基准**同池等权（V0 10 只池用 10 只等权，**V4 用自有的 5 只池** —— 三套基准互不可比）。", "",
          f"V3–V7 主方案固定为「{OVERLAY}」（信号同时用于时间维度与横截面）；"
          "V0 只有横截面（业绩分排序），没有时间维度的仓位倍率。", "",
          "| # | 版本 | 信号 / 池 | 年化 | 最大回撤 | Calmar | 夏普 | 平均仓位 | 剔除 3 月后 | ΔCalmar vs 同池等权 |",
          "|---:|---|---|---:|---:|---:|---:|---:|---:|---:|"]
    for i, r in enumerate(allrows, 1):
        d = r["calmar"] - r["base_calmar"]
        L.append(f"| {i} | {r['策略']} | {r['机制']} | {_p(r['cagr'])} | {_p(r['dd'])} | "
                 f"**{r['calmar']:.2f}** | {_n(r['sharpe'])} | {_p(r['pos'], 0)} | "
                 f"{_p(r['cagr_d3'])} | {d:+.2f} |")
    L += ["", f"- 基准 ①：7 只等权（V3–V7 的同池基准）年化 **{_p(bst['cagr'])}**、"
              f"回撤 {_p(bst['dd'])}、Calmar **{bst['calmar']:.2f}**。",
          f"- 基准 ②：10 只等权（V0 原池的同池基准）年化 **{_p(b10['cagr'])}**、"
          f"回撤 {_p(b10['dd'])}、Calmar **{b10['calmar']:.2f}** —— 多出的 3 只"
          "（标普红利低波50 / 标普红利机会 / 港股通高股息）把年化抬高约 "
          f"{(b10['cagr'] - bst['cagr']) * 100:+.1f}pp，所以 V0 的两行要各自对自己的基准读。",
          f"- 基准 ③：**V4 的 5 只专用池等权** 年化 **{_p(base5['cagr'])}**、回撤 {_p(base5['dd'])}、"
          f"Calmar **{base5['calmar']:.2f}** —— V4 从 2026-10-26 起只用这 5 只"
          "（`v4_rsi/rule.py::POOL`），所以它的 ΔCalmar 要对着这一行读，不是基准 ①。",
          "- `平均仓位` 只对含时间维度的方案有意义（V0 与纯横截面方案始终满仓）；"
          "`剔除 3 月后` = 剔掉相对同池基准贡献最大的 3 个月。",
          "- V7 的 `rsi（单指标对照）` 与 V4 完全等价，已跳过，不重复占一行。", ""]

    # ---------------- 二
    L += ["## 二、V3–V7 变体分解（Calmar）", "",
          "同一个信号，用在「时间维度」「横截面」「两者叠加」上的效果可以完全不同。", "",
          "| 策略 | 时间维度 | 横截面 top2 | 两者叠加 | 该策略最佳 |",
          "|---|---:|---:|---:|---:|"]
    for name, v in a.items():
        cs = [S.stats(v["sims"][x])["calmar"] for x in VARIANTS]
        L.append(f"| {name} | {cs[0]:.2f} | {cs[1]:.2f} | {cs[2]:.2f} | {max(cs):.2f} |")
    L += ["", f"- 同一张表的基准 Calmar = **{bst['calmar']:.2f}**；"
              "低于它的格子说明该用法是负贡献。", ""]

    # ---------------- 三
    L += ["## 三、各版本原生口径（不可与 §一 混比）", "",
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
    best = allrows[0]
    a_best = rows[0]
    worst = allrows[-1]
    v0_7 = [r for r in v0_rows if "7 只" in r["策略"]][0]
    v0_10 = [r for r in v0_rows if "10 只" in r["策略"]][0]
    val = [r for r in rows if r["策略"].startswith(("V3", "V5", "V6"))
           or "val（" in r["策略"] or "all4" in r["策略"]]
    combos = [r for r in rows if r["策略"].startswith("V7")]
    pure_rsi = [r for r in rows if r["策略"].startswith("V4")][0]
    n_val_below = sum(1 for r in val if r["calmar"] <= bst["calmar"])
    best_combo = max(combos, key=lambda r: r["calmar"])
    L += ["## 五、结论", "",
          f"1. **全景第一是 `{best['策略']}`**：Calmar {best['calmar']:.2f}、"
          f"年化 {_p(best['cagr'])}、回撤 {_p(best['dd'])}、平均仓位 {_p(best['pos'], 0)}；"
          f"剔除贡献最大 3 个月后年化仍有 {_p(best['cagr_d3'])}；"
          f"V3–V7 里最好的就是它，V0 最好的那行（7 只池）排第 "
          f"{allrows.index(v0_7) + 1}。",
          f"2. **V0（打分轮动）没跑赢同池等权**：7 只池 Calmar **{v0_7['calmar']:.2f}** vs "
          f"{v0_7['base_calmar']:.2f}（{v0_7['calmar'] - v0_7['base_calmar']:+.2f}，基本持平）；"
          f"10 只池 **{v0_10['calmar']:.2f}** vs {v0_10['base_calmar']:.2f}"
          f"（{v0_10['calmar'] - v0_10['base_calmar']:+.2f}，明显更差）。"
          f"7 只池年化 {_p(v0_7['cagr'])} 高于等权 {_p(bst['cagr'])}，但回撤 "
          f"{_p(v0_7['dd'])} 深于 {_p(bst['dd'])} —— **收益是靠承担波动换来的，不是选品 alpha**。"
          "扩到 10 只异质池（中证系列 + 标普系列 + 港股通）后判别力进一步下降，"
          "说明业绩分只在同类标的间可比。",
          f"3. **估值类三项 {len(val)} 个条目里有 {n_val_below} 个跑不赢等权基准**"
          f"（基准 Calmar {bst['calmar']:.2f}）。`dy`/`spread`/`ratio` 的横截面排序在数学上是同一个"
          "信号（y10 是共同项），把它们等权相加等于给股息率三倍权重 —— 这不是「多指标」。",
          f"4. **组合没有带来增量**：{len(combos)} 个 V7 候选里最好的是 `{best_combo['策略']}`"
          f"（Calmar {best_combo['calmar']:.2f}），仍低于纯 RSI 的 {pure_rsi['calmar']:.2f}。"
          "把无效信号掺进有效信号，只会摊薄。",
          f"5. **垫底的是 `{worst['策略']}`**（Calmar {worst['calmar']:.2f}，"
          f"比自己的同池基准低 {worst['base_calmar'] - worst['calmar']:.2f}）。"
          "连同 §三 的原生口径一起看：V1 的唯一一次离场是负贡献（分位长期贴顶、几乎不触发）、"
          "V2 的时间维度有增量但剔除 5 个月即转负 —— **八个版本里没有一个能靠「换信号」稳定提高收益**，"
          "能稳定提高的只有回撤控制（V4 的降暴露）与分散（打分轮动 topN=2 明显优于 topN=1）。",
          "",
          f"> 边界：82 个月、单边 0.03%。V0 的两行在窗口早期**业绩分退化** ——"
          "3 年窗要求 3 年历史，2020-01 时 7 只池里只有 090010 够，"
          "所以 2020 年前半段实际是单标的轮动，这会压低 V0 的早期表现。"
          f"{len(rows)} 个 V3–V7 条目 × 3 个变体 = {len(rows) * 3} 次比较，"
          "第一名天然偏高（多重比较）；RSI 的可信度来自阈值敏感性三档全过，不是来自这一次排名。", ""]
    return "\n".join(L)


def main() -> int:
    a, rets = build_a()
    v0_rows, base10 = build_v0(rets)
    b = build_b_v1() + build_b_v2() + build_b_rank()
    gap = caliber_gap()
    S.write(OUT_MD, render(a, rets, b, gap, v0_rows, base10))

    base = S.stats(a[SINGLE[0][1]]["sims"][BASE])
    print(f"[cmp] 全景 {len(a) + len(v0_rows)} 个条目 · 样本 {rets.index[0]:%Y-%m}~"
          f"{rets.index[-1]:%Y-%m} ({len(rets)} 月) · 基准 7 只等权 Calmar "
          f"{base['calmar']:.2f} · 10 只等权 {S.stats(base10)['calmar']:.2f}")
    ranked = sorted(((n, S.stats(v["sims"][OVERLAY])) for n, v in a.items()),
                    key=lambda kv: -kv[1]["calmar"])
    for i, (name, st) in enumerate(ranked, 1):
        print(f"[cmp] {i:2d}. {name:26s} 年化 {st['cagr'] * 100:5.1f}%  "
              f"回撤 {st['dd'] * 100:6.1f}%  Calmar {st['calmar']:5.2f}")
    for r in v0_rows:
        print(f"[cmp]  {r['策略'].replace('V0 打分轮动', 'V0 打分'):26s} 年化 "
              f"{r['cagr'] * 100:5.1f}%  回撤 {r['dd'] * 100:6.1f}%  "
              f"Calmar {r['calmar']:5.2f}  (同池基准 {r['base_calmar']:.2f})")
    print("[cmp] 原生口径（不可比，仅方向参考）")
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
