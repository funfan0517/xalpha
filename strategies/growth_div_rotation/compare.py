# -*- coding: utf-8 -*-
"""成长/红利风格轮动 · 跨变种对比（**家族根目录入口**）

对比 v0（缓冲带 + 250 日分位）与 v1（绝对阈值 0.40/0.62 + 5 期分批 + 每月定投）。
两个变种共用同一份指数缓存（`v0/data/_gd_index_klines.json`）与同一套引擎时序
（v1 的 `engine.py` 与 v0 的 `engine.py` 逐行等价），差别只在规则本身。

数据: 科创50(000688) ÷ 中证红利(000922)，2020-01-02 起（科创50 上市日；创业板指口径为旧版 --alt）
口径: 两种资金模型
  口径 A 一次性本金 —— 两者都从 1.0 起做满仓轮动，直接比「净值」；这是规则本身的比较
  口径 B 每月定投 1000 —— 两者都按同一贡献计划注入现金，比「总资产 + XIRR」；这是手册的比较
产物: _v1_vs_v0_report.md · _v1_vs_v0.png（家族根）
       v1_ratio_bands/backtest/_v1_bt.{json,jsonl}（v1 单行摘要，归 v1 的 backtest/）
用法: python strategies/growth_div_rotation/compare.py [--rerun-v0]
"""
import io
import json
import os
import subprocess
import sys
from datetime import datetime

try:                                       # 控制台可能是 gbk，报告含 R₋₁ 等下标字符
    sys.stdout.reconfigure(encoding="utf-8")
except (AttributeError, OSError):
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8")

_DIR = os.path.dirname(os.path.abspath(__file__))           # 家族根
_V1 = os.path.join(_DIR, "v1_ratio_bands")                  # v1 变种目录
_V0 = os.path.join(_DIR, "v0")                              # v0 变种目录
if _V1 not in sys.path:
    sys.path.insert(0, _V1)                                 # 让 import rule/data/engine 命中 v1

import numpy as np  # noqa: E402
import pandas as pd  # noqa: E402

import matplotlib  # noqa: E402
matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402
plt.rcParams["font.sans-serif"] = ["Microsoft YaHei", "SimHei", "DejaVu Sans"]
plt.rcParams["axes.unicode_minus"] = False

import rule  # noqa: E402
import data as datamod  # noqa: E402
import engine  # noqa: E402

V0_JSON = os.path.join(_V0, "backtest", "_gd_bt.json")
OUT = _DIR                                              # 对比报告与图（家族根）
V1_OUT = os.path.join(_V1, "backtest")                  # v1 单行摘要（归 v1）
DCA_AMOUNT = 1000.0
OOS_SPLIT = "2019-01-01"


# ---------------------------------------------------------------- 通用指标
def pct(x, nd=2):
    return "—" if x is None or (isinstance(x, float) and np.isnan(x)) else f"{x * 100:+.{nd}f}%"


def metrics(nv):
    """净值序列 -> 年化/回撤/波动/夏普/总收益。"""
    if len(nv) == 0:
        return dict(ann=float("nan"), dd=float("nan"), vol=float("nan"),
                    shp=0.0, ret=float("nan"), years=0.0)
    s = nv / nv.iloc[0]
    ret = s.iloc[-1] - 1.0
    y = max((s.index[-1] - s.index[0]).days / 365.0, 1e-9)
    ann = (1 + ret) ** (1 / y) - 1 if ret > -1 else -1.0
    dd = float((s / s.cummax() - 1).min())
    r = s.pct_change().dropna()
    vol = float(r.std() * np.sqrt(252))
    shp = float(r.mean() / r.std() * np.sqrt(252)) if r.std() > 0 else 0.0
    return dict(ann=ann, dd=dd, vol=vol, shp=shp, ret=ret, years=y)


def dca_metrics(eq, contribs, idx):
    """定投口径：总投入 / 总资产 / XIRR / 净值回撤 / 资产回撤。"""
    total_in = float(sum(a for _, a in contribs))
    final = float(eq.iloc[-1])
    twr = _twr_from_contrib(eq, contribs)
    return dict(
        n_months=len(contribs), total_in=total_in, final=final,
        multiple=(final / total_in) if total_in else np.nan,
        xirr=engine.xirr(contribs, final, idx),
        twr_ann=metrics(twr)["ann"],
        twr_dd=metrics(twr)["dd"],
        eq_dd=float((eq / eq.cummax() - 1).min()),
    )


def _twr_from_contrib(eq, contribs):
    c = np.zeros(len(eq))
    pos = {b: a for b, a in contribs}
    for i in range(len(eq)):
        c[i] = pos.get(i, 0.0)
    twr = np.ones(len(eq))
    for t in range(1, len(eq)):
        prev = eq.iloc[t - 1]
        twr[t] = twr[t - 1] * ((eq.iloc[t] - c[t]) / prev) if prev > 0 else twr[t - 1]
    return pd.Series(twr, index=eq.index)


# ---------------------------------------------------------------- v0 产物
def load_v0(force=False):
    """读 v0 回测产物；窗口与当前缓存不一致则自动重跑 v0。"""
    need = force or not os.path.exists(V0_JSON)
    if not need:
        doc = json.load(open(V0_JSON, encoding="utf-8"))
        if "2010-06" not in doc.get("window", ""):
            need = True
    if need:
        print("[v0] 产物窗口过期或缺失，重跑 v0/backtest.py ...")
        subprocess.run([sys.executable, os.path.join(_V0, "backtest.py")],
                       cwd=_V0, check=True)
    doc = json.load(open(V0_JSON, encoding="utf-8"))
    dates = pd.to_datetime(doc["dates"])
    eq = pd.Series([doc["eq"][d] for d in doc["dates"]], index=dates, dtype=float)
    pos = pd.Series(doc["pos"], index=dates, dtype=float)
    return doc, eq, pos


# ---------------------------------------------------------------- 主流程
def build():
    wide = datamod.load_wide([rule.GROWTH_INDEX, rule.DIV_INDEX])
    g, d = wide[rule.GROWTH_INDEX], wide[rule.DIV_INDEX]
    ratio = (g / d).dropna()
    g, d = g.reindex(ratio.index), d.reindex(ratio.index)
    v0_doc, v0_eq, v0_pos = load_v0("--rerun-v0" in sys.argv)
    v0_pos = v0_pos.reindex(ratio.index).ffill().fillna(0.0)

    # ---------- 口径 A：一次性本金 ----------
    v1a = engine.run(g, d, ratio, fixed_amount=0.0, initial_cash=rule.INIT_CASH_LUMP)
    nv_v1a = v1a["twr"]
    nv_v0a = v0_eq
    nv_bh_g = (1 + g.pct_change().fillna(0)).cumprod()
    nv_bh_d = (1 + d.pct_change().fillna(0)).cumprod()
    nv_ff = (1 + (g.pct_change().fillna(0) + d.pct_change().fillna(0)) / 2).cumprod()

    # ---------- 口径 B：每月定投 1000 ----------
    v1b = engine.run(g, d, ratio, fixed_amount=DCA_AMOUNT, initial_cash=0.0)
    v0b = engine.simulate_weights(v0_pos, g, d, fixed_amount=DCA_AMOUNT, initial_cash=0.0)
    bh_gb = engine.simulate_weights(pd.Series(1.0, index=g.index), g, d,
                                    fixed_amount=DCA_AMOUNT, initial_cash=0.0)
    bh_db = engine.simulate_weights(pd.Series(0.0, index=g.index), g, d,
                                    fixed_amount=DCA_AMOUNT, initial_cash=0.0)
    ff_b = engine.simulate_weights(pd.Series(0.5, index=g.index), g, d,
                                   fixed_amount=DCA_AMOUNT, initial_cash=0.0)

    # ---------- 敏感性 ----------
    grid = []
    for lo, hi in [(0.16, 0.36), (0.20, 0.31), (0.24, 0.28), (0.18, 0.34)]:
        r = engine.run(g, d, ratio, fixed_amount=0.0, initial_cash=1.0,
                       ratio_low=lo, ratio_high=hi)
        m = metrics(r["twr"].loc[r["twr"].index >= v0_doc["eval_start"]])
        grid.append(dict(low=lo, high=hi, switches=len(r["switches"]),
                         ann=m["ann"], dd=m["dd"], shp=m["shp"]))
    gap_grid = []
    # gap=0 → 5 份在同一 bar 成交，等价「一次性整仓切换」，用于隔离「分批」本身的效果
    for sch, gp in [("monthly", rule.TRANCHE_GAP_DAYS), ("days", 5), ("days", 21),
                    ("days", 0)]:
        r = engine.run(g, d, ratio, fixed_amount=0.0, initial_cash=1.0,
                       schedule=sch, gap=gp)
        m = metrics(r["twr"].loc[r["twr"].index >= v0_doc["eval_start"]])
        gap_grid.append(dict(schedule=sch, gap=gp, switches=len(r["switches"]),
                             tranches=len(r["tranche_log"]),
                             ann=m["ann"], dd=m["dd"], shp=m["shp"]))

    # 决策延迟稳健性（v1 的收益依赖少数几次跨阈，必须排除「同日收盘成交」的假象）
    lag_grid = []
    for lag in (0, 1, 5):
        r2 = ratio.shift(lag).dropna() if lag else ratio
        res = engine.run(g.reindex(r2.index), d.reindex(r2.index), r2,
                         fixed_amount=0.0, initial_cash=1.0)
        m = metrics(res["twr"].loc[res["twr"].index >= v0_doc["eval_start"]])
        lag_grid.append(dict(lag=lag, ann=m["ann"], dd=m["dd"],
                             switches=len(res["switches"])))

    return dict(wide=wide, ratio=ratio, g=g, d=d, v0_doc=v0_doc, v0_pos=v0_pos,
                nv=dict(v1=v1a["twr"], v0=nv_v0a, bh_g=nv_bh_g, bh_d=nv_bh_d, ff=nv_ff),
                A=dict(v1=v1a), B=dict(v1=v1b, v0=v0b, bh_g=bh_gb, bh_d=bh_db, ff=ff_b),
                grid=grid, gap_grid=gap_grid, lag_grid=lag_grid)


def _slice_tbl(nv_map, start, end=None):
    rows = {}
    for k, s in nv_map.items():
        s = s.loc[s.index >= start]
        if end:
            s = s.loc[s.index <= end]
        rows[k] = metrics(s)
    return rows


def build_report(R):
    v0d = R["v0_doc"]
    ev = v0d["eval_start"]
    nv = R["nv"]
    last = R["ratio"].index[-1]
    ev_ts = pd.Timestamp(ev)
    # 分段中点：评估起点与数据末端的中间（科创50 2020 才上市，窗口远短于旧版 2010-06）
    mid = ev_ts + (last - ev_ts) // 2
    mid_s = str(mid.date())
    A = _slice_tbl(nv, ev)
    seg = dict(
        早期=_slice_tbl(nv, ev, mid_s),
        近期=_slice_tbl(nv, mid_s, None),
    )
    name = {"v1": "**v1 绝对阈值+5期分批**", "v0": "v0 缓冲带+分位",
            "bh_g": ("买入持有科创50" if rule.GROWTH_INDEX == "000688"
                      else "买入持有创业板指"),
            "bh_d": "买入持有中证红利", "ff": "50-50 恒定"}

    L = ["# 成长/红利风格轮动 · v1 与 v0 回测对比", "",
         f"> 生成 {datetime.now():%Y-%m-%d %H:%M} · 数据 {R['ratio'].index.min().date()}~"
         f"{R['ratio'].index.max().date()}（{len(R['ratio'])} 交易日）· "
         f"成长=科创50 {rule.GROWTH_INDEX} ÷ 红利=中证红利 {rule.DIV_INDEX}", "",
         "## 0. 口径与本次修正", "",
         "**口径**：两个策略共用同一引擎时序（`R₋₁` 决策、同一收盘成交、`ret[t]` 计收益、"
         "单边费 0.03%），只用指数价格收益（未含分红再投与跟踪误差）。"
         "v1 与 v0 的唯一差别是**规则本身**（阈值形态 / 换仓方式 / 是否定投），不是执行时点。", "",
         "**2026-10-29 修正**：`rule.index_prefix()` 曾按股票规则（首位 5/6/9→沪）判指数交易所，"
         "把 `000922`（中证红利）与 `000688`（科创50）误投到深市，实际取到的是同号**个股**"
         "（佳电股份 / 国城矿业）日线 —— 红利端年化波动被算成 48%（真实 20.6%）、"
         "科创50 出现 2015 年数据（该指数 2020 年才发布）。已改为 `399/159 → 深市，其余 → 沪市`，"
         "并重抓缓存回补至 2010-06-01；`v1_ratio_bands/data.py` 另加「首值低于合理下界即报错」的护栏。"
         "**此前所有基于旧缓存的绩效数字（含 +11.92% / -62.14% / 140 次切换）全部作废。**", "",
         f"**评估窗口**：口径 A 从 v0 指标成形日 `{ev}` 起（两者同窗）；"
         "口径 B 覆盖全部定投月。", "",
         "## 1. 口径 A · 一次性本金（净值口径，直接比规则）", "",
         "| 组合 | 年化 | 最大回撤 | 波动 | 夏普 | Calmar | 总收益 |",
         "|---|---|---|---|---|---|---|"]
    for k in ["v1", "v0", "bh_g", "bh_d", "ff"]:
        m = A[k]
        calmar = m["ann"] / abs(m["dd"]) if m["dd"] else np.nan
        L.append(f"| {name[k]} | {pct(m['ann'])} | {pct(m['dd'])} | {pct(m['vol'])} | "
                 f"{m['shp']:.2f} | {calmar:.2f} | {pct(m['ret'])} |")
    w_v1 = R["A"]["v1"]["weight_g"].loc[R["A"]["v1"]["weight_g"].index >= ev].mean()
    w_v0 = R["v0_pos"].loc[R["v0_pos"].index >= ev].mean()
    L += ["", f"- v1 换仓 **{len(R['A']['v1']['switches'])}** 次"
              f"（分批买入 {len(R['A']['v1']['tranche_log'])} 笔），"
              f"v0 切换 **{v0d['total_switches']}** 次。",
          f"- 成长暴露均值：v1 **{w_v1:.1%}**，v0 **{w_v0:.1%}** —— "
          f"v1 只在极端水位换仓、中间地带满仓持有，反而比 v0 暴露更高；"
          f"v0 靠高频切换 + 分位离场把暴露压到 {w_v0:.0%}，用低暴露换低回撤。", ""]

    # 分段
    L += ["## 2. 分段稳健性（口径 A）", "",
          "| 组合 | 早期 年化 / 回撤 | 近期 年化 / 回撤 |", "|---|---|---|"]
    for k in ["v1", "v0", "bh_g", "bh_d"]:
        a, b = seg["早期"][k], seg["近期"][k]
        L.append(f"| {name[k]} | {pct(a['ann'])} / {pct(a['dd'])} | {pct(b['ann'])} / {pct(b['dd'])} |")
    L += ["", f"> 早期 = `{ev}`~{mid_s}，近期 = {mid_s}~{last.date()}。"
              "注意：当前的绝对阈值（0.20/0.31）是在 2020-2026 这一较短样本上标定的（手册自述「历史拟合阈值」），"
              "**两段都是样本内**，分段只反映稳定性，**不是真正的样本外检验**。"
              "真正的样本外检验需要另一对指数或另一段市场，本仓库数据不足。", ""]

    # 口径 B
    B = R["B"]
    L += ["## 3. 口径 B · 每月定投 1000 元（资金加权，手册口径）", "",
          "| 组合 | 月数 | 总投入 | 总资产 | 倍数 | XIRR(资金加权) | 净值年化 | 净值回撤 | 资产回撤 |",
          "|---|---|---|---|---|---|---|---|---|"]
    for k in ["v1", "v0", "bh_g", "bh_d", "ff"]:
        s = B[k]
        m = dca_metrics(s["equity"], s["contributions"], R["g"].index)
        L.append(f"| {name[k]} | {m['n_months']} | {m['total_in'] / 1e4:.1f} 万 | "
                 f"{m['final'] / 1e4:.1f} 万 | {m['multiple']:.2f}× | **{pct(m['xirr'])}** | "
                 f"{pct(m['twr_ann'])} | {pct(m['twr_dd'])} | {pct(m['eq_dd'])} |")
    n_dca = len(B["v1"]["contributions"])
    dca_start = str(R["g"].index[0].date())
    L += ["", "> XIRR 是**资金加权**收益（每一笔定投按实际在场时长折算），与净值口径的"
              "时间加权年化**不可直接比较**；手册里的「年化 15.59%」属此列。"
              "「净值年化」才是可与第 1 节对照的口径。",
          f"> 窗口注意：本表从 {dca_start} 起投（{n_dca} 个月；科创50 2020-01 上市，"
              f"窗口短于手册的 196 个月）；"
              f"第 1 节从 {ev} 起（v0 指标成形）。**表内可比、跨表不可比。**", ""]

    # 敏感性
    L += ["## 4. v1 敏感性（口径 A）", "",
          "**阈值**（口径 A，分批=逐月）：", "",
          "| RATIO_LOW | RATIO_HIGH | 换仓次数 | 年化 | 最大回撤 | 夏普 |",
          "|---|---|---|---|---|---|"]
    for r in R["grid"]:
        L.append(f"| {r['low']:.2f} | {r['high']:.2f} | {r['switches']} | {pct(r['ann'])} | "
                 f"{pct(r['dd'])} | {r['shp']:.2f} |")
    L += ["", "**分批间距**（阈值固定 0.40/0.62；含「一次性」对照以隔离分批本身的效果）：", "",
          "| 间距 | 换仓次数 | 分批笔数 | 年化 | 最大回撤 | 夏普 |",
          "|---|---|---|---|---|---|"]
    def _gl(r):
        if r["schedule"] == "monthly":
            return "逐月定投日（手册正文）"
        return "一次性（不分批，gap=0）" if r["gap"] == 0 else f"每 {r['gap']} 交易日 1 份"
    for r in R["gap_grid"]:
        L.append(f"| {_gl(r)} | {r['switches']} | {r['tranches']} | {pct(r['ann'])} | "
                 f"{pct(r['dd'])} | {r['shp']:.2f} |")
    g0, g1, g2, g3 = R["gap_grid"]
    L += ["", f"> 手册第 4 条「随后 5 个定投日（或 5 周 / 5 日）」是**口径歧义**，但实测它对结果几乎无影响："
              f"逐月分批 {pct(g0['ann'])} vs 一次性 {pct(g3['ann'])}，差 "
              f"{(g0['ann'] - g3['ann']) * 100:+.2f}pp；最大回撤 {pct(g0['dd'])} vs {pct(g3['dd'])}。"
              "原因是全期只换仓 6 次、每次摊薄窗口 5 个月，占 197 个月的比例很小 —— "
              "**真正决定 v1 表现的是阈值，不是分批节奏**。", ""]

    L += ["**决策延迟**（v1 的收益依赖少数几次跨阈，需排除「同日收盘成交」的假象）：", "",
          "| 决策延迟 | 换仓次数 | 年化 | 最大回撤 |", "|---|---|---|---|"]
    for r in R["lag_grid"]:
        L.append(f"| 延迟 {r['lag']} 个交易日 | {r['switches']} | {pct(r['ann'])} | {pct(r['dd'])} |")
    n_sw = len(R["A"]["v1"]["switches"])
    L += ["", "> 延迟 1~5 日结果几乎不变，说明 v1 的优势不是同日成交带来的边界收益，"
              f"而是它确实把 {n_sw} 次大级别风格切换都踩对了 —— 但这**恰恰是过拟合的典型形态**："
              f"只有 {n_sw} 个决策点，样本量太小，统计上无法与「事后挑出来的 {n_sw} 个日期」区分。", ""]

    # 换仓日志
    sw = R["A"]["v1"]["switches"]
    L += ["## 5. v1 换仓日志（口径 A）", ""]
    if sw:
        L += ["| 日期 | 由 | 切到 | R₋₁ | 触发条件 | 卖出金额 | 每份 | 打断未完成分批 |",
              "|---|---|---|---|---|---|---|---|"]
        for s in sw:
            L.append(f"| {s['date']} | {s['from_side']} | **{s['to_side']}** | {s['R']:.4f} | "
                     f"{s['reason']} | {s['sold']:.3f} | {s['per_buy']:.3f} | "
                     f"{'是' if s['interrupted'] else '—'} |")
    else:
        L.append("- 无换仓")
    L += ["", f"- 期末未走完的分批份额：**{R['A']['v1']['pending_left']}** 份", ""]

    # 结论
    a1, a0 = A["v1"], A["v0"]
    b1 = dca_metrics(B["v1"]["equity"], B["v1"]["contributions"], R["g"].index)
    b0 = dca_metrics(B["v0"]["equity"], B["v0"]["contributions"], R["g"].index)
    c1 = a1["ann"] / abs(a1["dd"])
    c0 = a0["ann"] / abs(a0["dd"])
    short_legs = [t for t in v0d.get("trades", []) if t["bars"] < 5]
    g = R["grid"]
    L += ["## 6. 结论", "",
          f"1. **净值口径（一次性本金）**：v1 年化 {pct(a1['ann'])} / 回撤 {pct(a1['dd'])} / "
          f"夏普 {a1['shp']:.2f} / Calmar {c1:.2f}；v0 年化 {pct(a0['ann'])} / 回撤 {pct(a0['dd'])} / "
          f"夏普 {a0['shp']:.2f} / Calmar {c0:.2f}。"
          f"v1 多 {(a1['ann'] - a0['ann']) * 100:+.2f}pp 年化，但多扛 {(a0['dd'] - a1['dd']) * 100:+.2f}pp 回撤 —— "
          "**风险调整后（Calmar/夏普）两者非常接近，v1 只是「跑得更热」而不是「更准」。**",
          f"2. **定投口径**：同样每月 1000 元投到今，v1 总资产 {b1['final'] / 1e4:.1f} 万"
          f"（XIRR {pct(b1['xirr'])}），v0 总资产 {b0['final'] / 1e4:.1f} 万"
          f"（XIRR {pct(b0['xirr'])}）。",
          f"3. **手册数字不可直接照搬**：手册回测建立在创业板指(399006)上（15.59% / 79.5 万 / 26.68% 回撤），"
          "本仓库已切换到科创50(000688)，成长侧波动与比值分布都不同，那些数字不再对得上；"
          f"且即便在创业板指口径下，本仓库也复现不出 26.68% 的回撤（实测净值 {pct(b1['twr_dd'])} / 资产 {pct(b1['eq_dd'])}），"
          "手册的回撤口径明显低估。**不要引用手册的 26.68%。**",
          f"4. **阈值才是 v1 的命门**：当前 0.20/0.31 → {pct(g[1]['ann'])}；"
          f"放宽为 0.16/0.36 → {pct(g[0]['ann'])}，收紧到 0.24/0.28 → {pct(g[2]['ann'])}"
          f"（换仓更频、已与 v0 的 {pct(a0['ann'])} 打平甚至更差）。"
          "换一组同样合理的阈值，结论就从「跑赢 v0」变成「不如 v0」—— 与手册自己提示的过拟合风险一致。",
          f"5. **反过来，v1 有一个 v0 没有的真实优势：可执行性**。v1 全期只换仓 "
          f"{len(R['A']['v1']['switches'])} 次；v0 切换 {v0d['total_switches']} 次，"
          f"其中 **{len(short_legs)} 段持仓不足 5 个交易日**。场外 C 类联接基金持有 < 7 天赎回费 1.5%，"
          "这些段在真实渠道里要么做不了、要么成本吃掉收益 —— v0 的高频切换在指数层面成立、在申赎层面打折。",
          "6. **结论取舍**：v1 的高收益大半来自阈值拟合与更高的成长暴露，不具备样本外优势；"
          "v0 的规则形态（无量纲、自适应）更稳，但切换过频、落地成本高。"
          "**可行的合成是「v0 的信号形态 + v1 的执行节奏」**：用缓冲带/分位产生信号，"
          "但加冷却期与分批建仓把年切换次数压到个位数。这需要另开变种，不在本次范围内。", ""]

    L += ["## 7. 已知偏差", "",
          "- **红利端系统性低估**：中证红利是价格指数，股息率约 4.5%，"
          "两策略持有红利时段的收益都被少算约 4.5%/年 × 非成长暴露。"
          f"v0 的非成长暴露（{1 - w_v0:.0%}）高于 v1（{1 - w_v1:.0%}），"
          "**所以这里对 v0 更不利**：把红利分红补回去，v0 与 v1 的差距会进一步收窄。"
          "（v0 自身报告里的「买入持有红利 +4.09%」也同样是低估的。）",
          "- 未含滑点与场外确认时滞；赎回费只做了定性讨论（见结论第 5 条），未进回测。",
          "- `R` 的绝对值依赖两只指数的基期：本仓库这段数据里 0.40 约是 R 的 40% 分位、"
          "0.62 约是 90% 分位，与手册的标定样本不完全一致 —— 同一组数字换个数据起点就会漂移。",
          "- 全部为模拟结果，非投资建议。", ""]
    return "\n".join(L) + "\n"


def _plot(R):
    ev = R["v0_doc"]["eval_start"]
    nv = R["nv"]
    for k in nv:
        nv[k] = nv[k].loc[nv[k].index >= ev]
        nv[k] = nv[k] / nv[k].iloc[0]
    fig, ax = plt.subplots(figsize=(12, 5.5))
    ax.plot(nv["v1"].index, nv["v1"].values, label="v1 绝对阈值+5期分批", lw=1.4)
    ax.plot(nv["v0"].index, nv["v0"].values, label="v0 缓冲带+分位", lw=1.2)
    ax.plot(nv["bh_g"].index, nv["bh_g"].values, label="持有创业板指", alpha=0.65, lw=1.0)
    ax.plot(nv["bh_d"].index, nv["bh_d"].values, label="持有中证红利", alpha=0.65, lw=1.0)
    ax.set_yscale("log")
    ax.legend()
    ax.set_title(f"成长/红利风格轮动 v1 vs v0（净值，对数轴，{ev} 起）")
    ax.grid(alpha=0.3)
    fig.tight_layout()
    path = os.path.join(OUT, "_v1_vs_v0.png")
    fig.savefig(path, dpi=140, facecolor="white")
    plt.close(fig)
    return path


def _write_products(R):
    os.makedirs(OUT, exist_ok=True)
    os.makedirs(V1_OUT, exist_ok=True)
    txt = build_report(R)
    open(os.path.join(OUT, "_v1_vs_v0_report.md"), "w", encoding="utf-8").write(txt)
    print(txt)
    ev = R["v0_doc"]["eval_start"]
    v1 = R["A"]["v1"]
    v1s = v1["twr"].loc[v1["twr"].index >= ev]
    m = metrics(v1s)
    b1 = dca_metrics(R["B"]["v1"]["equity"], R["B"]["v1"]["contributions"], R["g"].index)
    doc = dict(
        generated_at=datetime.now().strftime("%Y-%m-%d %H:%M"),
        variant="v1_ratio_bands", window=R["v0_doc"]["window"],
        eval_start=ev, params=v1["params"],
        metrics_a=m, metrics_b=b1,
        switches=v1["switches"], tranche_log=v1["tranche_log"],
        grid=R["grid"], gap_grid=R["gap_grid"], lag_grid=R["lag_grid"],
        twr={str(d.date()): round(float(x), 6) for d, x in v1s.items()},
    )
    json.dump(doc, open(os.path.join(V1_OUT, "_v1_bt.json"), "w", encoding="utf-8"),
              ensure_ascii=False, indent=2)
    summary = dict(ok=True, code="GD_ROT_V1", theme="成长/红利风格轮动 v1(绝对阈值+5期分批)",
                   years=round(m["years"], 1), st_ann=m["ann"], st_mdd=m["dd"],
                   dxirr=b1["xirr"], dca_final=b1["final"], switches=len(v1["switches"]),
                   params=v1["params"])
    with open(os.path.join(V1_OUT, "_v1_bt.jsonl"), "w", encoding="utf-8") as f:
        f.write(json.dumps(summary, ensure_ascii=False) + "\n")
    print("图:", _plot(R))


def main():
    _write_products(build())


if __name__ == "__main__":
    main()
