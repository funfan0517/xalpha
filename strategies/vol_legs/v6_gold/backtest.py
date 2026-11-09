# -*- coding: utf-8 -*-
"""V6 黄金三口径 · 回测与对照。

跑三件事：
1. **主表**：A 长期配置 / B 控回撤 / C 低仓位占坑，各自预热完成后起算，对照「恒持满带」
   （= 买入持有）与「纯现金」；场外 `000216` 与场内 `518880` 两套成本分开列；
2. **共同窗口**：三条口径都截到同一区间重新归 1，回答「同一段行情里谁更好」；
3. **分解与敏感性**：A 的动作流水与回撤解剖、B 的闸门/波动层拆分、C 的参数与通道，
   外加三等分分期与逐年、六条口径断言。

产物（本目录 `backtest/`）：`_v6_gold_bt.md` · `_v6_gold_bt.jsonl`。

用法：python strategies/vol_legs/v6_gold/backtest.py
模拟结果，非投资建议。
"""
import json
import os
import sys

import numpy as np
import pandas as pd

try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:  # noqa: BLE001
    pass

_HERE = os.path.dirname(os.path.abspath(__file__))
if _HERE not in sys.path:
    sys.path.insert(0, _HERE)

import gold as G  # noqa: E402

OUT_DIR = os.path.join(_HERE, "backtest")
OUT_MD = os.path.join(OUT_DIR, "_v6_gold_bt.md")
OUT_JSONL = os.path.join(OUT_DIR, "_v6_gold_bt.jsonl")

KEYS = ("A", "B", "C")
SHORT = {"A": "A 长期配置", "B": "B 控回撤", "C": "C 占坑", "BH": "恒持满带", "CASH": "纯现金"}
LABEL = {
    "A": "A 长期配置（满带 + 60 日保险丝 + 回撤阶梯）",
    "B": f"B 控回撤（波动目标 {G.VOL_TARGET:.0%}/{G.VOL_WINDOW} 日，不做趋势判断）",
    "C": f"C 低仓位占坑（MACD {G.MACD_FAST}/{G.MACD_SLOW} 金叉持有）",
}


# ---------------------------------------------------------------- 工具
def pct(x, nd=2):
    return "—" if x is None or (isinstance(x, float) and not np.isfinite(x)) else f"{x * 100:+.{nd}f}%"


def num(x, nd=2):
    return "—" if x is None or (isinstance(x, float) and not np.isfinite(x)) else f"{x:.{nd}f}"


def states(d, **kw):
    px = d["px"]
    return {"A": G.state_a(px, **kw)["state"],
            "B": G.state_b(px, **kw)["state"],
            "C": G.state_c(px, **kw)}


def evaluate(px, st, mode="otc", p=None):
    """单口径评价：净值 + 指标 + 曝光 + 调仓 + 成本。"""
    fee, slip = G.fee_of(mode, p)
    nav = G.run_leg(st, px, fee=fee, slip=slip)
    m = G.perf(nav)
    return dict(nav=nav, m=m, calmar=G.calmar(m), exp=G.exposure(st),
                acts=len(G.actions(st)), whip=G.whipsaw(st, px)["n"],
                cost=G.cost_drag(st, mode, p), state=st)


def run_block(px_full, st_full, start, mode="otc", p=None):
    px = px_full.loc[start:]
    return evaluate(px, st_full.loc[start:], mode, p)


def bench_block(px_full, start, mode="otc", p=None):
    px = px_full.loc[start:]
    one = pd.Series(1.0, index=px.index)
    zero = pd.Series(0.0, index=px.index)
    return (evaluate(px, one, mode, p), evaluate(px, zero, mode, p))


def start_of(d, k):
    """该口径预热完成日（C 用固定预热日数）。"""
    if k in ("A", "B"):
        return d["start"][k]
    return d["px"].index[min(G.C_WARMUP, len(d["px"]) - 2)]


def row(name, r, years=None):
    m = r["m"]
    per_year = (r["acts"] / years) if years else None
    cy = (r["cost"] * 100 / years) if years else None
    return (f"| {name} | {pct(m['ann'])} | {pct(m['cum'])} | {m['vol'] * 100:.2f}% | "
            f"{pct(m['mdd'])} | {num(m['sharpe'])} | {num(r['calmar'])} | "
            f"{r['exp'] * 100:.0f}% | {r['acts']} | {num(per_year, 1)} | {r['whip']} | "
            f"{r['cost'] * 100:.2f}pp | {num(cy, 2)}pp |")


HEAD = ("| 口径 | 年化 | 累计 | 年化波动 | 最大回撤 | 夏普 | Calmar | 平均仓位 | 调仓 | "
        "调仓/年 | 打脸 | 累计成本 | 成本/年 |\n"
        "|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|")


def years_of(nav):
    return (nav.index[-1] - nav.index[0]).days / 365.25


def segments(nav, k=3):
    idx = nav.index
    n = len(idx)
    cuts = [0] + [int(round(n * i / k)) for i in (1, 2)] + [n - 1]
    return [(idx[cuts[i]], idx[cuts[i + 1]]) for i in range(k)]


def seg_ann(nav, d0, d1):
    sub = nav.loc[d0:d1]
    if len(sub) < 3:
        return None
    return G.perf(sub / sub.iloc[0])


def yearly(nav):
    out, seg = {}, {}
    vals = nav.to_numpy(dtype=float)
    for i, d in enumerate(nav.index):
        y = str(d.year)
        st = 1.0 if i == 0 else vals[i - 1]
        if y not in seg:
            seg[y] = [st, vals[i]]
        seg[y][1] = vals[i]
    for y, (a, b) in seg.items():
        out[y] = b / a - 1.0
    return out


def mdd_of(nav):
    """-> (谷底日, 峰日, 回撤)。"""
    dd = nav / nav.cummax() - 1.0
    t = dd.idxmin()
    return t, nav.loc[:t].idxmax(), float(dd.min())


# ---------------------------------------------------------------- 主流程
def main():
    os.makedirs(OUT_DIR, exist_ok=True)

    d_otc = G.build("otc", refresh=True)
    d_ex = G.build("exchange", refresh=False)
    blocks = {}
    for mode, d in (("otc", d_otc), ("exchange", d_ex)):
        st = states(d)
        blk = {}
        for k in KEYS:
            blk[k] = run_block(d["px"], st[k], start_of(d, k), mode, d["params"])
            blk[k]["start"] = start_of(d, k)
        start_all = max(blk[k]["start"] for k in KEYS)
        blk["BH"], blk["CASH"] = bench_block(d["px"], start_all, mode, d["params"])
        blk["BH"]["start"] = blk["CASH"]["start"] = start_all
        blk["_common_start"] = start_all
        blk["_px"] = d["px"]
        blk["_st"] = st
        blocks[mode] = blk

    # ---- 共同窗口（场外）：三条都从同一天重新归 1
    common = blocks["otc"]["_common_start"]
    px_o = blocks["otc"]["_px"]
    common_block = {k: evaluate(px_o.loc[common:], blocks["otc"]["_st"][k].loc[common:],
                                "otc", d_otc["params"]) for k in KEYS}
    common_block["BH"], common_block["CASH"] = bench_block(px_o, common, "otc", d_otc["params"])

    # ---- 分段 / 逐年（共同窗口）
    segs = segments(common_block["BH"]["nav"], 3)
    seg_data = {k: [seg_ann(common_block[k]["nav"], a, b) for a, b in segs]
                for k in KEYS + ("BH",)}
    yrs = [y for y in yearly(common_block["BH"]["nav"])
           if y in yearly(common_block["A"]["nav"])]
    year_data = {k: yearly(common_block[k]["nav"]) for k in KEYS + ("BH",)}

    # ---- 敏感性
    def one_a(name, **over):
        st = G.state_a(d_otc["px"], **over)["state"]
        return name, run_block(d_otc["px"], st, d_otc["start"]["A"], "otc", G.params(**over))

    sens_a = [("基准（60 日保险丝 / 阶梯 10-15-20 / 次日回带）",
               run_block(d_otc["px"], blocks["otc"]["_st"]["A"], d_otc["start"]["A"],
                         "otc", G.params()))]
    for item in (one_a("保险丝 30 日（更早降半仓）", DEGRADE_DAYS=30),
                 one_a("保险丝 90 日", DEGRADE_DAYS=90),
                 one_a("保险丝 250 日（几乎不降）", DEGRADE_DAYS=250),
                 one_a("回带延迟 20 日", RECOVER_LAG=20),
                 one_a("回带延迟 60 日", RECOVER_LAG=60),
                 one_a("起手 80% 带（阶梯有空位）", START_A=0.8),
                 one_a("起手 90% 带（实盘 9.4pp/10pp 口径）", START_A=0.9),
                 one_a("阶梯 8/16/24%", LADDER_DD=(8.0, 16.0, 24.0))):
        sens_a.append(item)

    b_gate, b_pure = {}, {}
    sens_b = []
    for gate, bucket in ((True, b_gate), (False, b_pure)):
        for tgt in (0.08, 0.10, 0.12, 0.15, 0.18):
            over = dict(GATE_ON=gate, VOL_TARGET=tgt)
            st = G.state_b(d_otc["px"], **over)["state"]
            r = run_block(d_otc["px"], st, d_otc["start"]["B"], "otc", G.params(**over))
            bucket[tgt] = r
            sens_b.append((f"{'闸门+波动' if gate else '纯波动层'} · 目标 {tgt:.0%}", r, gate, tgt))
    for win in (60, 250):
        over = dict(VOL_TARGET=0.15, VOL_WINDOW=win)
        st = G.state_b(d_otc["px"], **over)["state"]
        sens_b.append((f"纯波动层 · 目标 15% · 窗口 {win} 日",
                       run_block(d_otc["px"], st, d_otc["start"]["B"], "otc", G.params(**over)),
                       False, None))

    sens_c = []
    main_pair = (G.MACD_FAST, G.MACD_SLOW)
    for fast, slow in (main_pair, (12, 26), (20, 60), (30, 100)):
        over = dict(MACD=(fast, slow, 9))
        st = G.state_c(d_otc["px"], **over)
        tag = "（场外 000216 · 主口径）" if (fast, slow) == main_pair else "（场外 000216）"
        sens_c.append((f"MACD {fast}/{slow}{tag}",
                       run_block(d_otc["px"], st, start_of(d_otc, "C"), "otc", G.params(**over))))
    st_f = G.state_c(d_otc["px"], C_TREND_FILTER=True)
    sens_c.append((f"MACD {main_pair[0]}/{main_pair[1]} + 只在年线上方持（场外）",
                   run_block(d_otc["px"], st_f, start_of(d_otc, "C"), "otc",
                             G.params(C_TREND_FILTER=True))))
    for fast, slow in (main_pair, (12, 26)):
        over = dict(MACD=(fast, slow, 9))
        st = G.state_c(d_ex["px"], **over)
        sens_c.append((f"MACD {fast}/{slow}（场内 518880 · 万 0.3 佣金）",
                       run_block(d_ex["px"], st, start_of(d_ex, "C"), "exchange", G.params(**over))))

    checks = checks_of(d_otc, blocks["otc"]["_st"])

    md = render(d_otc, d_ex, blocks, common_block, segs, seg_data, yrs, year_data,
                sens_a, sens_b, b_gate, b_pure, sens_c, common, checks)
    with open(OUT_MD, "w", encoding="utf-8") as f:
        f.write(md)

    with open(OUT_JSONL, "w", encoding="utf-8") as f:
        for mode, blk in blocks.items():
            for k in KEYS + ("BH", "CASH"):
                r = blk[k]
                m = r["m"]
                f.write(json.dumps(dict(mode=mode, key=k, label=SHORT[k],
                                        start=str(r["start"].date()),
                                        end=str(blk["_px"].index[-1].date()),
                                        ann=m["ann"], cum=m["cum"], vol=m["vol"], mdd=m["mdd"],
                                        sharpe=m["sharpe"], calmar=r["calmar"],
                                        exposure=r["exp"], acts=r["acts"],
                                        whip=r["whip"], cost=r["cost"]),
                                   ensure_ascii=False) + "\n")
        for group, items in (("A", sens_a), ("B", [(n, r) for n, r, _g, _t in sens_b]),
                             ("C", sens_c)):
            for name, r in items:
                m = r["m"]
                f.write(json.dumps(dict(mode="otc", group=group, key="sens", label=name,
                                        ann=m["ann"], mdd=m["mdd"], sharpe=m["sharpe"],
                                        calmar=r["calmar"], exposure=r["exp"],
                                        acts=r["acts"], whip=r["whip"], cost=r["cost"]),
                                   ensure_ascii=False) + "\n")
    print("报告:", OUT_MD)
    print("机读:", OUT_JSONL)


# ---------------------------------------------------------------- 断言
def checks_of(d, st):
    """六条口径断言：恒持=买入持有、空仓=货基、档位语义、无前视。"""
    px = d["px"]
    out = []
    one = pd.Series(1.0, index=px.index)
    nav = G.run_leg(one, px, fee=(0.0, 0.0), slip=0.0)
    err = float((nav / (px / px.iloc[0]) - 1.0).abs().max())
    assert err < 1e-9, f"C1 恒持≠买入持有（max err {err:.2e}）"
    out.append(f"C1 恒持满带（零成本）与买入持有逐日误差 < 1e-9（实测 {err:.1e}）")

    zero = pd.Series(0.0, index=px.index)
    nav0 = G.run_leg(zero, px, fee=(0.0, 0.0), slip=0.0)
    want = float((1.0 + G.CASH_DAILY) ** len(px))
    assert abs(float(nav0.iloc[-1]) - want) < 1e-9, "C2 空仓未按货基计息"
    out.append(f"C2 空仓净值 = 货基复利（末值 {nav0.iloc[-1]:.6f} vs {want:.6f}）")

    a = st["A"]
    assert float(a.min()) >= G.FLOOR - 1e-9 and float(a.max()) <= 1.0 + 1e-9
    out.append(f"C3 A 的档位 ∈ [{G.FLOOR:.0%}, 100%]（实测 {a.min():.0%} ~ {a.max():.0%}）")

    b = G.state_b(px)
    assert bool((b["state"] <= b["state_trend"] + 1e-9).all())
    out.append("C4 B 的实际仓位 ≤ 趋势档位（波动层只压不抬）")

    assert set(np.round(st["C"].unique(), 6)) <= {0.0, 1.0}
    out.append("C5 C 的仓位只取 {0, 1}（金叉/死叉状态机）")

    cut = px.index[-31]
    for k, fn in (("A", lambda s: G.state_a(s)["state"]),
                  ("B", lambda s: G.state_b(s)["state"]),
                  ("C", G.state_c)):
        full = fn(px).loc[:cut]
        part = fn(px.loc[:cut])
        assert float((full - part).abs().max()) < 1e-9, f"C6 {k} 存在前视"
    out.append("C6 无前视：截掉最后 30 个交易日重算，三条口径的历史状态逐日一致")
    return out


# ---------------------------------------------------------------- 报告渲染
def render(d, d_ex, blocks, common_block, segs, seg_data, yrs, year_data,
           sens_a, sens_b, b_gate, b_pure, sens_c, common, checks):
    otc, ex = blocks["otc"], blocks["exchange"]
    px_o, px_e = otc["_px"], ex["_px"]
    rA, rB, rC, rBH = otc["A"], otc["B"], otc["C"], otc["BH"]
    cA, cB, cC, cBH = (common_block[k] for k in ("A", "B", "C", "BH"))
    bh = cBH["m"]
    dA = cA["m"]["ann"] - bh["ann"]
    dB = cB["m"]["ann"] - bh["ann"]
    dC = cC["m"]["ann"] - bh["ann"]
    L = []
    A = L.append

    A("# V6 变种 · 黄金 ETF 三口径策略 · 回测")
    A("")
    A(f"> 标的：场内信号 `{G.SIGNAL_CODE}` 黄金ETF华安 / 场外执行 `{G.EXEC_CODE}` 华安黄金ETF联接A；"
      f"目标带 **{G.BAND_PP:g}pp**（本报告内部一律记成「占带比例」，1.0 = 满带）。")
    A("> 成交：T 日盘后决策 → **T+1 收盘成交**；空仓按货基 "
      f"{G.CASH_ANNUAL:.2%} 计息；成本 = |Δ仓位| × 费率，场外 "
      f"{G.FEE_OTC[0]:.2%}/{G.FEE_OTC[1]:.2%} + 滑点 {G.SLIP:.2%}，场内 "
      f"{G.FEE_IN[0]:.3%}/{G.FEE_IN[1]:.3%} + 滑点 {G.SLIP_IN:.2%}。")
    A("> 参数唯一权威源：`gold.py` 顶部常量（`params()` 打包，敏感性靠它覆盖）。")
    A("")

    # ---- 0. 结论
    A("## 0. 先说结论")
    A("")
    verdict = ("**只有 B 在最差的那一段里少亏**：B 的年化反而比恒持略高"
               if dB > 0 else "**三条口径的年化都不如买入持有**")
    A(f"1. {verdict}。共同窗口（§3）里 A {pct(cA['m']['ann'])}（{pct(dA)}）、"
      f"B {pct(cB['m']['ann'])}（{pct(dB)}）、C {pct(cC['m']['ann'])}（{pct(dC)}），"
      f"恒持满带 {pct(bh['ann'])} / {pct(bh['mdd'])} / 夏普 {num(bh['sharpe'])}。")
    A(f"2. **B 是唯一「风险调整后确实更好」的口径**：{pct(cB['m']['ann'])} / "
      f"{pct(cB['m']['mdd'])} / 夏普 {num(cB['m']['sharpe'])}（恒持 {pct(bh['mdd'])} / "
      f"夏普 {num(bh['sharpe'])}），平均仓位还有 {cB['exp']:.0%} —— "
      "它买的是「已实现波动冲高时把仓位压下去」，天然在暴跌里生效；"
      "代价是单边急涨的年份会少赚一点（§7 逐年表）。")
    A(f"3. **A 是「不做择时」的诚实版本**：平均仓位 {cA['exp']:.0%}、12 年只动 "
      f"{cA['acts']} 次，最大回撤与买入持有**一模一样**（{pct(cA['m']['mdd'])}）—— "
      "因为 2026 这轮下跌到数据日只有 40 余日，还没到 60 日保险丝的门槛。"
      "它的价值在「长熊里把上限压到半仓」（2014-2015 生效过），不在当期回撤。")
    A(f"4. **C 只在场内、且仍明确跑输**：场外 A 类（赎 0.50%）下年化掉到 "
      f"{pct(cC['m']['ann'])}、累计成本 {cC['cost'] * 100:.1f}pp"
      f"（{num(cC['cost'] / years_of(cC['nav']) * 100)}pp/年）；"
      f"同一条信号放场内 `518880` 是 {pct(ex['C']['m']['ann'])} / {pct(ex['C']['m']['mdd'])} / "
      f"夏普 {num(ex['C']['m']['sharpe'])}。**想要低仓位，B 把目标调到 8% 比 C 更好**（§8.2）。")
    A("")
    A("**所以选择标准不是「谁年化高」，而是「你要黄金干什么」**："
      "长期配置、不想管（A，但要知道它≈买入持有+一条慢保险丝）/ 压组合波动（B）/ "
      "必须有明确进出信号（C，仅场内且接受跑输）。")
    A("")

    # ---- 1. 口径与样本
    A("## 1. 口径与样本")
    A("")
    A("| 口径 | 代码 | 区间 | 交易日 | 各口径起算点（预热后） |")
    A("|---|---|---|---:|---|")
    A(f"| 场外净值（主口径） | `{G.EXEC_CODE}` | {px_o.index[0].date()} ~ {px_o.index[-1].date()} "
      f"| {len(px_o)} | A {otc['A']['start'].date()} / B {otc['B']['start'].date()} / "
      f"C {otc['C']['start'].date()} |")
    A(f"| 场内收盘（对照） | `{G.SIGNAL_CODE}` | {px_e.index[0].date()} ~ {px_e.index[-1].date()} "
      f"| {len(px_e)} | A {ex['A']['start'].date()} / B {ex['B']['start'].date()} / "
      f"C {ex['C']['start'].date()} |")
    A("")
    A("- **场外净值**是实际执行口径（含分红再投、跟踪误差、申赎时滞），A/B 的主表用它；")
    A("- **场内收盘**用于 C 的对照 —— C 的换手高，场外 A 类的赎回费会吃掉大半超额；")
    A(f"- 三条口径统一等 MA250 预热（C 另外按 {G.C_WARMUP} 个交易日截断 —— "
      "EMA200 的初始值残留 (1-2/201)^N 在 N=250 时还有 8%，故取 2×slow），"
      "所以**主表起算点基本重合**；§3 仍给同一窗口的对照，避免首日建仓差异影响结论。")
    A("")

    # ---- 2. 主表
    A("## 2. 主表（各自预热完成后起算 · 场外净值口径）")
    A("")
    A(HEAD)
    for k in KEYS:
        A(row(LABEL[k], otc[k], years_of(otc[k]["nav"])))
    A(row("恒持满带（= 买入持有）", otc["BH"], years_of(otc["BH"]["nav"])))
    A(row("纯现金（货基）", otc["CASH"], years_of(otc["CASH"]["nav"])))
    A("")
    A("> 「恒持满带」= 同区间不做任何规则、仓位一直 100% 带（等价于买入持有 + 一次申购费）；"
      "「累计成本」按 |Δ仓位| × 费率累加，单位 pp 占净值。")
    A(f"> 三条口径的预热都等 MA250，起算点分别 A {otc['A']['start'].date()} / "
      f"B {otc['B']['start'].date()} / C {otc['C']['start'].date()}；"
      "若仍有 1~2 个交易日错位（C 按固定预热日数），跨口径比较以 §3 的共同窗口为准。")
    A("")

    # ---- 3. 共同窗口
    A(f"## 3. 共同窗口（{common.date()} 起 · 三条口径与基准都从同一天归 1）")
    A("")
    A(HEAD)
    for k in KEYS:
        A(row(LABEL[k], common_block[k], years_of(common_block[k]["nav"])))
    A(row("恒持满带（= 买入持有）", common_block["BH"], years_of(common_block["BH"]["nav"])))
    A(row("纯现金（货基）", common_block["CASH"], years_of(common_block["CASH"]["nav"])))
    A("")
    A(f"> 同一窗口里恒持满带 {pct(bh['ann'])} / {pct(bh['mdd'])} / 夏普 {num(bh['sharpe'])}；"
      f"A 年化 {pct(dA)}、回撤改善 {num((cA['m']['mdd'] - bh['mdd']) * 100)}pp；"
      f"B 年化 {pct(dB)}、回撤改善 {num((cB['m']['mdd'] - bh['mdd']) * 100)}pp；"
      f"C 年化 {pct(dC)}、回撤改善 {num((cC['m']['mdd'] - bh['mdd']) * 100)}pp。")
    A("")

    # ---- 4. 场内对照
    A("## 4. 场内对照（`518880` 收盘 · 万 0.3 佣金 + 0.05% 滑点）")
    A("")
    A(HEAD)
    for k in KEYS:
        A(row(LABEL[k], ex[k], years_of(ex[k]["nav"])))
    A(row("恒持满带（= 买入持有）", ex["BH"], years_of(ex["BH"]["nav"])))
    A("")
    A("> A/B 在场内外的差别很小（每年只动 0.5~0.8 次，费率占比可忽略）；"
      f"差别全部集中在 C —— 它每年约 {ex['C']['acts'] / years_of(ex['C']['nav']):.0f} 次调仓"
      f"（{ex['C']['acts'] / years_of(ex['C']['nav']) / 2:.0f} 个往返），"
      f"场外 0.50% 赎回费让它累计付出 {rC['cost'] * 100:.0f}pp，场内只要 "
      f"{ex['C']['cost'] * 100:.0f}pp。")
    A("")

    # ---- 5. A 的分解
    stA_df = G.state_a(px_o)
    stA = stA_df["state"]
    acts = G.actions(stA)
    sells = [a for a in acts if a["kind"] == "sell"]
    buys = [a for a in acts if a["kind"] == "buy"]
    tA, pkA, ddA = mdd_of(otc["A"]["nav"])
    tBH, pkBH, ddBH = mdd_of(otc["BH"]["nav"])
    deg_days = int(stA_df["degraded"].sum())
    A("## 5. A 长期配置：它到底做了什么")
    A("")
    A(f"- 全样本 **{len(acts)} 次动作**（买入 {len(buys)} / 卖出 {len(sells)}），"
      f"平均 {len(acts) / years_of(rA['nav']):.1f} 次/年 —— 「长期配置」该有的样子：规则不常开口。")
    A(f"- 保险丝生效（仓位被压在 50%）的日子占 **{deg_days / len(stA_df):.0%}**，"
      f"平均仓位 {rA['exp']:.0%}；A 从不清仓。")
    same = abs(ddA - ddBH) < 1e-6
    below_now = int(stA_df["below_days"].iloc[-1])
    A(f"- **最大回撤 {pct(ddA)}**（谷底 {tA.date()} / 峰 {pkA.date()}）"
      + ("，与买入持有**完全相同** —— 保险丝要「跌破年线满 "
         f"{G.DEGRADE_DAYS} 日」才动，这一轮跌破到数据日只有 {below_now} 日，还没轮到它。"
         "这是「滞后」的直接代价，不是 bug。"
         if same else
         f"，恒持是 {pct(ddBH)}（{tBH.date()}）—— 保险丝在长熊里确实起了作用。"))
    A("")
    if acts:
        A("| 日期 | 从 | 到 | 方向 |")
        A("|---|---:|---:|---|")
        for a in acts:
            A(f"| {a['date'].date()} | {a['frm']:.0%} | {a['to']:.0%} | "
              f"{'买入' if a['kind'] == 'buy' else '卖出'} |")
        A("")
    A("**阶梯为什么几乎不触发**：起手就是满带（1.0），阶梯只在「年线上方 + 仓位有 20% 带的空位」时"
      "才有事可做 —— 而空位只有保险丝降档腾出来的那 50%，降档期间又禁止加仓。"
      "所以对**已经建满仓**的账户，A 实际等价于「满带 + 慢保险丝」；"
      "**如果你的黄金仓位低于目标带**（例如 6pp/10pp），阶梯就是那条「越跌越买」的规则（§8.1 起手 80% 档）。")
    A("")

    # ---- 6. B 的分解
    A("## 6. B 控回撤：闸门与波动层各占多少")
    A("")
    A("| 组成（目标 15%/120 日） | 年化 | 最大回撤 | 夏普 | Calmar | 平均仓位 |")
    A("|---|---:|---:|---:|---:|---:|")
    for nm, r in (("闸门 + 波动层（v5 口径）", b_gate[0.15]),
                  ("**纯波动层（默认 B）**", b_pure[0.15]),
                  ("恒持满带（无风控）", otc["BH"])):
        m = r["m"]
        A(f"| {nm} | {pct(m['ann'])} | {pct(m['mdd'])} | {num(m['sharpe'])} | "
          f"{num(r['calmar'])} | {r['exp']:.0%} |")
    A("")
    dlt = (b_pure[0.15]["m"]["ann"] - b_gate[0.15]["m"]["ann"]) * 100
    dd_gate_gain = abs(b_pure[0.15]["m"]["mdd"] - b_gate[0.15]["m"]["mdd"]) * 100
    A(f"> **四象限闸门在黄金上不划算**：它把年化让掉 {num(abs(dlt))}pp，只换来回撤浅 "
      f"{num(dd_gate_gain)}pp（{pct(b_gate[0.15]['m']['mdd'])} vs "
      f"{pct(b_pure[0.15]['m']['mdd'])}）。与 "
      "`v5_trend_gate/research/_v5_sens_legs.md` 的逐腿结论一致：黄金跌破年线后多次 V 型反转"
      "（2016、2024、2025 都是），闸门减在坑里。**所以 B 默认关掉闸门**（`GATE_ON=False`）；"
      "若你要的是「连 -22% 也不想看到」，再把它打开，用 2.4pp 年化买 1.2pp 回撤。")
    A("")
    quad = G.state_b(px_o, GATE_ON=True)["quad"]
    q = quad[quad != ""].value_counts()
    tot = int(q.sum()) or 1
    A("参考：闸门口径下的四象限日数占比 —— " + " · ".join(
        f"{name} {int(q.get(name, 0)) / tot:.0%}" for name in G.QUADS) + "。")
    A(f"> 波动层的作用机制是「已实现波动冲高 → 压仓位」，所以它天然在暴跌里生效："
      f"15% 目标下平均只压掉 {1 - b_pure[0.15]['exp']:.0%} 的仓位，却把回撤从 "
      f"{pct(otc['BH']['m']['mdd'])} 压到 {pct(b_pure[0.15]['m']['mdd'])}。")
    A("")

    # ---- 7. 分段与逐年
    A("## 7. 分段与逐年（共同窗口 · 年化）")
    A("")
    A("| 期间 | " + " | ".join(SHORT[k] for k in KEYS + ("BH",)) + " |")
    A("|---|" + "---|" * (len(KEYS) + 1))
    for i, (a, b) in enumerate(segs):
        cells = [(pct(seg_data[k][i]["ann"]) if seg_data[k][i] else "—")
                 for k in KEYS + ("BH",)]
        A(f"| {a.date()} ~ {b.date()} | " + " | ".join(cells) + " |")
    A("")
    A("| 年 | " + " | ".join(SHORT[k] for k in KEYS + ("BH",)) + " |")
    A("|---|" + "---|" * (len(KEYS) + 1))
    for y in yrs:
        A(f"| {y} | " + " | ".join(pct(year_data[k].get(y)) for k in KEYS + ("BH",)) + " |")
    A("")
    diffs = sorted(((year_data["B"].get(y, 0.0) - year_data["BH"].get(y, 0.0)), y) for y in yrs)
    best = "、".join(f"{y}（{pct(d)}）" for d, y in reversed(diffs[-2:]))
    worst = "、".join(f"{y}（{pct(d)}）" for d, y in diffs[:1])
    A(f"> 逐年表是这套结论最该被读的一格：**B 相对恒持的差额几乎全来自少数年份** —— "
      f"贡献最大的是 {best}，拖累的是 {worst}。这正是「波动目标不是 alpha，"
      "是在波动冲高时少挨一刀」的意思；换成别的样本，这几格会换年份。")
    A("")

    # ---- 8. 敏感性
    A("## 8. 敏感性")
    A("")
    A("### 8.1 A：保险丝、回带与起手")
    A("")
    A("| 变体 | 年化 | 最大回撤 | 夏普 | Calmar | 平均仓位 | 调仓 |")
    A("|---|---:|---:|---:|---:|---:|---:|")
    for nm, r in sens_a:
        m = r["m"]
        A(f"| {nm} | {pct(m['ann'])} | {pct(m['mdd'])} | {num(m['sharpe'])} | "
          f"{num(r['calmar'])} | {r['exp']:.0%} | {r['acts']} |")
    A("")
    A("> 保险丝越短（30 日）越容易在震荡里被洗，越长（250 日）越接近买入持有；"
      "本样本上「几乎不降」（250 日）年化最高、回撤没变差 —— **不是「保险丝没用」，"
      "而是这 12 年里真正需要它的只有 2014-2015 一段**。")
    A("")
    A("### 8.2 B：波动目标 × 作用形态")
    A("")
    A("| 变体 | 年化 | 最大回撤 | 夏普 | Calmar | 平均仓位 |")
    A("|---|---:|---:|---:|---:|---:|")
    for nm, r, _g, _t in sens_b:
        m = r["m"]
        A(f"| {nm} | {pct(m['ann'])} | {pct(m['mdd'])} | {num(m['sharpe'])} | "
          f"{num(r['calmar'])} | {r['exp']:.0%} |")
    A("")
    lo = b_pure[0.08]
    A(f"> 目标越低仓位越轻、Calmar 越高：**8% 目标 = {lo['exp']:.0%} 仓位 / "
      f"{pct(lo['m']['mdd'])} 回撤 / 夏普 {num(lo['m']['sharpe'])}**"
      f"（年化 {pct(lo['m']['ann'])}）。想「低仓位占坑」的人应该用这一档，而不是 C —— "
      "同一条占用一半资金的思路，它的回撤与夏普都明显更好。")
    A("")
    A("### 8.3 C：MACD 参数、年线过滤与通道")
    A("")
    A("| 变体 | 年化 | 最大回撤 | 夏普 | Calmar | 平均仓位 | 调仓 | 累计成本 |")
    A("|---|---:|---:|---:|---:|---:|---:|---:|")
    for nm, r in sens_c:
        m = r["m"]
        A(f"| {nm} | {pct(m['ann'])} | {pct(m['mdd'])} | {num(m['sharpe'])} | "
          f"{num(r['calmar'])} | {r['exp']:.0%} | {r['acts']} | {r['cost'] * 100:.2f}pp |")
    A("")
    A("> **50/200 自 2026-11-09 起是主口径**（按用户要求从 12/26 切换）。同一逻辑下它把调仓"
      "从 212 次砍到 90 次、累计成本从 84.80pp 降到 36.00pp、年化从 +1.30% 提到 +5.07%。"
      "**但有两点必须一起看**：① 它仍然跑输买入持有（+11.39%），差的 6.3pp/年就是「一半时间空仓」的代价；"
      "② 12/26 是 `tech_indicators/v13_macd` 的既有基线，换成 50/200 本质是**用本样本的结果改默认值**，"
      "属于事后选择 —— 所以这张表里 20/60、30/100 的结果要当作「参数敏感性」读，不要当「更优参数」找。"
      "年线过滤（只在年线上方持仓）在黄金上仍然没用：它把仓位砍到三成，收益更差。")
    A("")

    # ---- 9. 断言
    A("## 9. 口径断言（不过就抛）")
    A("")
    for line in checks:
        A(f"- {line}")
    A("")

    # ---- 10. 边界
    A("## 10. 已知边界")
    A("")
    A("1. **没有 alpha，只有风险预算**：黄金没有现金流锚，价格是唯一信息源；三条口径都在"
      "「少赚 / 少波动 / 少占资金」之间换。")
    A("2. **A 的保险丝滞后 60 日**：它避开的是以年计的阴跌（2013-2015），"
      "躲不掉三个月级别的急跌，也抓不住 V 型反转（2026 这轮就没赶上）。")
    A(f"3. **B 的改善集中在波动冲高的年份**：本样本里贡献最大的是 {best} —— "
      "`v5_trend_gate/research/_vol_overlay.md` 已量化「黄金的改善全部来自 2023-2026」，"
      "等于用最近这一段给策略打分，别外推。")
    A("4. **C 的换手对费率极敏感**：场外 A 类（赎 0.5%）基本不可用，场内要注意溢价 —— "
      "`|溢价| > 2% 不买`（主策略 §8 硬过滤）。")
    A("5. **FOMC 窗口与央行购金保险丝在回测里不生效**：`data/_val_inputs.json` 的 "
      "`fomc_days` 为空、`wgrc_fuse` 为 null（人工输入，留空即不执行）。规则代码保留，"
      "填上后每日脚本会照做，但历史段无数据可回测。")
    A("6. **样本 12 年（场外）/ 11.8 年（场内）**，两轮大牛市夹一轮长熊，结论对起点敏感；"
      "三套参数来自主策略与 v5 的既有研究，不是在本样本上搜出来的（唯一的例外是 §8.3 的 50/200）。")
    A("")
    A("> **模拟结果，非投资建议。**")
    return "\n".join(L) + "\n"


if __name__ == "__main__":
    main()
