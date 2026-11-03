# -*- coding: utf-8 -*-
"""波动资产 · V6 变种：估值分位智能定投 · 回测 / 对照组 / 验收断言。

口径（全部由 `dca.py` 提供，本文件只做组合与报表）：
- 价格 = 沪深300 **全收益** H00300（含分红再投）；信号 = 000300 滚动 PE(TTM) 的历史分位；
- 每月第 1 个交易日决策、**T+1 收盘成交**；月度到账 `BUDGET_M = 2B`，未投留现金池按 `rf` 日频计息；
- 收益口径 = **份额化净值**（R-A09 口径①，主）、XIRR（②）、简单收益率（③，仅展示）。

对照组（规格 §6，同标的 / 同区间 / 同到账节奏 / 同成本 / 同现金利率）：
  G0 buy_hold_monthly（mult≡2，最强基准）· G1 equal_dca（mult≡1）· G2 smart_dca（五档）
  G3 smart_reinvest（+R-A07）· G4 lump_low（平时停投、pct<0.20 一次投完）
  G5 smart_sell（G2 + R-A04 止盈，独立对照，不与主策略混算）

产出（相对本文件）：
- backtest/_v6_valuation_dca_bt.md
- backtest/_v6_valuation_dca_bt.jsonl

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
sys.path.insert(0, _HERE)

import dca  # noqa: E402

OUTDIR = os.path.join(_HERE, "backtest")
OUT_MD = os.path.join(OUTDIR, "_v6_valuation_dca_bt.md")
OUT_JSONL = os.path.join(OUTDIR, "_v6_valuation_dca_bt.jsonl")

# 敏感性档：(标签, 参数覆盖)。区间不动，只改一格。
SENS = [
    ("基准（W=2520 / 阈值 20-40-60-80 / 倍数 2-1.5-1-.5-0）", {}),
    ("分位窗口 W=1260（5 年，2016-12 起成立）", dict(W=1260)),
    ("分位窗口 W=1890（7.5 年）", dict(W=1890)),
    ("分位窗口 W=3780（15 年，PE 不够 → 退化为全历史）", dict(W=3780)),
    ("MIN_SAMP=250（冷启动更短）", dict(MIN_SAMP=250)),
    ("MIN_SAMP=750（冷启动更长）", dict(MIN_SAMP=750)),
    ("阈值 15/35/60/85", dict(TIER_EDGES=[0.15, 0.35, 0.60, 0.85])),
    ("阈值 25/45/65/85", dict(TIER_EDGES=[0.25, 0.45, 0.65, 0.85])),
    ("MULT_DEEP=1.5（深度低估只加半倍）", dict(TIER_MULT=[1.5, 1.5, 1.0, 0.5, 0.0])),
    ("MULT_DEEP=3.0（预算约束会开始起作用）", dict(TIER_MULT=[3.0, 1.5, 1.0, 0.5, 0.0])),
    ("MULT_TOP=0.25（高估留观察仓，不停投）", dict(TIER_MULT=[2.0, 1.5, 1.0, 0.5, 0.25])),
    ("MULT_TOP=0.5", dict(TIER_MULT=[2.0, 1.5, 1.0, 0.5, 0.5])),
    ("rf=1.5%", dict(RF=0.015)),
    ("rf=2.5%", dict(RF=0.025)),
    ("滑点 s=0.02%", dict(S=0.0002)),
    ("滑点 s=0.10%", dict(S=0.0010)),
    ("决策日改每月 15 日", dict(DCA_RULE="day_15")),
    ("模拟 ETF 整手（LOT=100）", dict(LOT=100)),
]


# ---------------------------------------------------------------- 0. 工具
def over(p, **kw):
    """参数覆盖（纯 dict 拷贝，不重新读 CFG）。"""
    q = dict(p)
    q.update(kw)
    return q


def short(name):
    """`"**G2 智能定投（...）**"` -> `"G2"`（表里只放组号，规则说明单独一列）。"""
    return name.split(" ", 1)[0].strip("*")


# ---------------------------------------------------------------- 1. 窗口
def window(frame, pc, p):
    """回测起点 = 第一个「分位可用」的决策日（实测 2013-11-01）。"""
    dec = dca.decision_mask(frame.index, p["DCA_RULE"], p["DAY_OF_MONTH"]).to_numpy(dtype=bool)
    ok = pc["pct"].reindex(frame.index).notna().to_numpy(dtype=bool)
    cand = frame.index[dec & ok]
    if not len(cand):
        raise RuntimeError("没有任何「分位可用」的决策日 —— PE 历史连 MIN_SAMP 都不够")
    return cand[0]


def slice_all(frame, pc, start):
    return frame.loc[start:], pc.loc[start:]


# ---------------------------------------------------------------- 2. 对照组
def run_group(name, frame, pct, p, mode, note):
    sim = dca.simulate(frame, pct, p, mode=mode)
    m = dca.perf(sim["nav"], rf=p["RF"])
    x = dca.xirr(dca.cashflows(sim))
    return dict(name=name, mode=mode, note=note,
                start=sim["nav"].index[0].date().isoformat(),
                end=sim["nav"].index[-1].date().isoformat(),
                years=m["years"], cagr=m["ann"], mdd=m["mdd"], vol=m["vol"],
                sharpe=m["sharpe"], sortino=m["sortino"], calmar=m["calmar"],
                xirr=x, simple_ret=dca.simple_ret(sim),
                utilization=sim["utilization"],
                total_in=sim["tot_cf"], final_value=sim["final_value"],
                cost_edge=sim["cost_edge"], avg_cost=sim["avg_cost"],
                n_invest=sim["n_invest"], n_sell=sim["n_sell"],
                fee=sim["tot_fee"], interest=sim["tot_int"], sim=sim)


def groups(frame, pct, p):
    p_rev = over(p, REINVEST=True)
    p_sell = over(p, SELL_ENABLED=True)
    return [
        run_group("G0 买入持有（每月到账即满额投入 · mult≡2）", frame, pct, p, "const2",
                  "**最强基准**：定投版「满仓」，份额化净值 ≈ 全收益指数（仅差成本与时滞）"),
        run_group("G1 普通等额定投（mult≡1）", frame, pct, p, "const1",
                  "最常被引用的对照：每月固定投 B，其余入现金池"),
        run_group("**G2 智能定投（本策略 · 五档）**", frame, pct, p, "tier", "待验证"),
        run_group("G3 智能定投 + 现金池再投资（R-A07）", frame, pct, p_rev, "tier",
                  "增强版：pct<0.20 时额外投 min(cash,1.0B)"),
        run_group("G4 等低点（平时停投，pct<0.20 一次投完）", frame, pct, p, "lump",
                  "验证「攒着等低点」是否真的有效"),
        run_group("G5 智能定投 + 止盈分支（R-A04）", frame, pct, p_sell, "tier",
                  "独立对照：`pct≥0.90` 且浮盈≥30% 时卖 50%，不与主策略混算"),
    ]


# ---------------------------------------------------------------- 3. 验收断言（V-A01~V-A08）
def check_conservation(sim):
    """V-A01：Σinvest + 卖出所得 + 期末现金 == Σ到账 + 现金利息 − 佣金 − 赎回费。"""
    lhs = sim["cash"]
    rhs = sim["tot_cf"] + sim["tot_int"] - sim["tot_inv"] - sim["tot_fee"] + sim["tot_sell"]
    return abs(lhs - rhs) <= max(1e-6, 1e-9 * (sim["tot_cf"] + 1.0)), lhs, rhs


def check_mult_values(sim, p):
    vals = {round(t["mult"], 9) for t in sim["tiers"]}
    allowed = {round(x, 9) for x in p["TIER_MULT"]} | {1.0}
    return vals <= allowed, sorted(vals)


def check_degenerate(frame, pct, p, expected_mult, ref_sim):
    """V-A03 / V-A04：把五档全部改成同一个值，结果必须与对应常量组**逐日完全一致**。"""
    p2 = over(p, TIER_EDGES=[1.0], TIER_MULT=[expected_mult])
    sim = dca.simulate(frame, pct, p2, mode="tier")
    d = float((sim["nav"] - ref_sim["nav"]).abs().max())
    return d <= 1e-9, d


def check_monotone():
    """V-A05：同一 n_eff 下，PE_t 上升时 pct 不得下降。"""
    idx = pd.bdate_range("2000-01-03", periods=601)
    base = np.linspace(10.0, 20.0, 600)
    prev = -1.0
    for x in np.linspace(9.0, 21.0, 25):
        s = pd.Series(np.append(base, x), index=idx)
        pv = dca.pe_percentile(s, W=2520, MIN_SAMP=500)["pct"].iloc[-1]
        if pv != pv or pv is None or pv < prev - 1e-12:
            return False, float("nan")
        prev = pv
    return True, prev


def check_util(sim):
    """V-A06：utilization ∈ (0,1]，且 == Σinvest/Σ到账（未含卖出）。"""
    u = sim["utilization"]
    recomputed = sim["tot_inv"] / sim["tot_cf"] if sim["tot_cf"] > 0 else 0.0
    return (0.0 < u <= 1.0 + 1e-12) and abs(u - recomputed) < 1e-12, u


def check_trades(frame, sim):
    """V-A07：成交笔数 == 有 invest>0 的决策日数（+ 止盈笔数）。无幽灵交易。

    另外校验每一笔买入的成交日 = 某个决策日的**下一个交易日**（T+1 成交，无前视）。
    """
    want = sum(1 for t in sim["tiers"] if t["target"] > 1e-9)
    idx = list(frame.index)
    pos = {d: i for i, d in enumerate(idx)}
    dec = set(frame.index[dca.decision_mask(frame.index).to_numpy(dtype=bool)])
    ok_next = all(
        (pos[t["date"]] - 1) in range(len(idx)) and idx[pos[t["date"]] - 1] in dec
        for t in sim["trades"] if t["side"] == "buy")
    ok = (sim["n_invest"] == want) and (sim["n_invest"] + sim["n_sell"] == len(sim["trades"])) \
        and ok_next
    return ok, (sim["n_invest"], want, sim["n_sell"], len(sim["trades"]), ok_next)


def check_index_anchor(g0, frame):
    """V-A08：G0 的份额化年化与同期全收益指数年化相差 < 2pp。"""
    close = frame["close"]
    n = len(close)
    idx_ann = (float(close.iloc[-1]) / float(close.iloc[0])) ** (252.0 / n) - 1.0
    return abs(g0["cagr"] - idx_ann) * 100.0 < 2.0, g0["cagr"], idx_ann


# ---------------------------------------------------------------- 4. 诊断表
def tier_table(frame, pct, p):
    rows = dca.tier_stats(frame, pct, horizon=250)
    # 用 G2 的实际投入补「投入金额占比」
    sim = dca.simulate(frame, pct, p, mode="tier")
    invest_by_tier = {}
    for t in sim["tiers"]:
        if t["tier"] is None:
            continue
        buy = next((x for x in sim["trades"]
                    if x["side"] == "buy" and x["date"] > t["date"]
                    and (x["date"] - t["date"]).days <= 10), None)
        if buy:
            invest_by_tier[t["tier"]] = invest_by_tier.get(t["tier"], 0.0) + buy["amount"]
    tot = sum(invest_by_tier.values()) or 1.0
    for r in rows:
        r["invest"] = invest_by_tier.get(r["tier"], 0.0)
        r["invest_share"] = r["invest"] / tot
    return rows


def sens_rows(full, p):
    """敏感性：**必须在未切片的完整 frame 上算分位**，否则 `n_eff` 从切片起点重新计数，
    会把起点整体后推 2 年（2026-11-02 踩过的坑：先切后算，起点从 2013-11 变成 2015-12，
    年化从 +6.09% 掉到 +3.18%）。"""
    out = []
    for name, kw in SENS:
        p2 = over(p, **kw)
        pc2 = dca.pe_percentile(full["pe"], p2["W"], p2["MIN_SAMP"])
        st = window(full, pc2, p2)
        f2, q2 = full.loc[st:], pc2.loc[st:]
        sim = dca.simulate(f2, q2["pct"], p2, mode="tier")
        m = dca.perf(sim["nav"], rf=p2["RF"])
        out.append(dict(name=name, start=st.date().isoformat(), cagr=m["ann"],
                        mdd=m["mdd"], sharpe=m["sharpe"], utilization=sim["utilization"],
                        cost_edge=sim["cost_edge"], n_invest=sim["n_invest"]))
    return out


# ---------------------------------------------------------------- 5. 报告
def main():
    os.makedirs(OUTDIR, exist_ok=True)
    p = dca.params()
    full = dca.load_frame()                     # 未切片：敏感性表要用完整 PE 历史
    pc = dca.pe_percentile(full["pe"], p["W"], p["MIN_SAMP"])
    start = window(full, pc, p)
    frame, pct_all = slice_all(full, pc, start)
    pct = pct_all["pct"]

    gs = groups(frame, pct, p)
    G0, G1, G2, G3, G4, G5 = gs      # noqa: F841  —— G5 只进表，不参与断言
    base = frame["close"]
    idx_ann = (float(base.iloc[-1]) / float(base.iloc[0])) ** (252.0 / len(base)) - 1.0
    peak, mdd_bh = float(base.iloc[0]), 0.0
    for v in base:
        peak = max(peak, float(v))
        mdd_bh = min(mdd_bh, float(v) / peak - 1.0)

    # warmup 段（n_eff < W，即 10 年窗口未成立）单列 —— R-A02 要求
    warm_end = pc.index[~pc["warmup"]][0] if (~pc["warmup"]).any() else None
    seg_rows = []
    if warm_end is not None and warm_end > start:
        f_w, q_w = frame.loc[:warm_end], pct.loc[:warm_end]
        if len(f_w) > 60:
            s2 = dca.simulate(f_w, q_w, p, mode="tier")
            m2 = dca.perf(s2["nav"], rf=p["RF"])
            seg_rows.append(dict(name=f"warmup 段 {start.date()} ~ {warm_end.date()}",
                                 cagr=m2["ann"], mdd=m2["mdd"], sharpe=m2["sharpe"],
                                 utilization=s2["utilization"]))
        f_o, q_o = frame.loc[warm_end:], pct.loc[warm_end:]
        if len(f_o) > 60:
            s3 = dca.simulate(f_o, q_o, p, mode="tier")
            m3 = dca.perf(s3["nav"], rf=p["RF"])
            seg_rows.append(dict(name=f"10 年窗口成立后 {warm_end.date()} ~ {frame.index[-1].date()}",
                                 cagr=m3["ann"], mdd=m3["mdd"], sharpe=m3["sharpe"],
                                 utilization=s3["utilization"]))

    tiers = tier_table(frame, pct, p)
    sens = sens_rows(full, p)
    yrs = dca.yearly(frame, G2["sim"], pct)

    # ---- 验收断言
    checks = []
    ok_c, lhs, rhs = check_conservation(G2["sim"])
    checks.append(("V-A01 资金守恒（Σinvest + 卖出 + 现金 == Σ到账 + 利息 − 费）", ok_c,
                   f"{lhs:.6f} vs {rhs:.6f} · 差 {lhs - rhs:+.2e}"))
    ok_m, vals = check_mult_values(G2["sim"], p)
    checks.append(("V-A02 mult 只取 {0, 0.5, 1, 1.5, 2}", ok_m, f"实测 {vals}"))
    ok_a, d3 = check_degenerate(frame, pct, p, 1.0, G1["sim"])
    checks.append(("V-A03 退化测试：五档全 1.0 == G1（逐日差 <1e-9）", ok_a, f"max|Δnav|={d3:.2e}"))
    ok_b, d4 = check_degenerate(frame, pct, p, 2.0, G0["sim"])
    checks.append(("V-A04 退化测试：五档全 2.0 == G0（逐日差 <1e-9）", ok_b, f"max|Δnav|={d4:.2e}"))
    ok_s, last_pct = check_monotone()
    checks.append(("V-A05 pct 单调（同一 n_eff 下 PE↑ ⇒ pct 不减）", ok_s,
                   f"最后一个测点 pct={last_pct:.4f}"))
    ok_u, u = check_util(G2["sim"])
    checks.append(("V-A06 utilization ∈ (0,1] 且 == Σinvest/Σ到账", ok_u, f"{u:.4f}"))
    ok_t, tt = check_trades(frame, G2["sim"])
    checks.append(("V-A07 无幽灵交易（买入数 == target>0 的决策日；成交日 == 决策日+1）", ok_t,
                   f"买入 {tt[0]} vs 应买 {tt[1]} · 卖出 {tt[2]} · 成交合计 {tt[3]} · "
                   f"T+1 校验 {'通过' if tt[4] else '失败'}"))
    ok_i, c0, ci = check_index_anchor(G0, frame)
    checks.append(("V-A08 G0 年化 ≈ 同期全收益指数年化（差 <2pp）", ok_i,
                   f"{c0 * 100:+.2f}% vs {ci * 100:+.2f}% → 差 {(c0 - ci) * 100:+.2f}pp"))

    # ---- jsonl
    with open(OUT_JSONL, "w", encoding="utf-8") as f:
        for r in gs:
            f.write(json.dumps({k: (round(v, 6) if isinstance(v, float) else v)
                                for k, v in r.items()
                                if k not in ("sim", "note")}, ensure_ascii=False) + "\n")
        for r in tiers:
            f.write(json.dumps({"tier": r["name"], **{
                k: (round(v, 6) if isinstance(v, float) else v)
                for k, v in r.items() if k != "name"}}, ensure_ascii=False) + "\n")
        for r in sens:
            f.write(json.dumps({"sens": r["name"], **{
                k: (round(v, 6) if isinstance(v, float) else v)
                for k, v in r.items() if k != "name"}}, ensure_ascii=False) + "\n")
        for c in checks:
            f.write(json.dumps({"check": c[0], "pass": bool(c[1]), "detail": c[2]},
                               ensure_ascii=False) + "\n")

    # ---- markdown
    L = [
        "# 波动资产 · V6 变种：估值分位智能定投 · 沪深300 回测",
        f"> 标的：**沪深300 全收益 H00300**（含分红再投）· 信号：000300 **滚动 PE(TTM) 的历史分位**",
        f"> 到账：每月第 1 个交易日决策、**T+1 收盘成交**，月额度 `BUDGET_M = 2B = "
        f"{p['BUDGET_MULT'] * p['B']:,.0f} 元`（B = {p['B']:,.0f}）",
        f"> 未投部分留现金池，按 `rf = {p['RF']:.1%}` **日频**计息 · 成本：滑点 {p['S']:.2%} + "
        f"佣金 {p['COMM']:.4%}（赎回 {p['SELL_FEE']:.2%}）",
        f"> 收益口径：**份额化净值**（主）/ XIRR（辅）/ 简单收益率（仅展示）",
        "> 规则来源：`strategy_A_valuation_dca.md`（策略A 编码规格 v1.0）；实现见 "
        "`v6_valuation_dca/dca.py` 模块 docstring",
        "",
        "## 1. 规则（可执行版）",
        "",
        "**用估值分位决定「这个月投多少钱」，不是决定「买不买」。**",
        "",
        "| 项 | 参数 | 说明 |",
        "|---|---|---|",
        f"| 决策日 | `{p['DCA_RULE']}` | 每月第 1 个交易日；备选 `day_15`（15 日，非交易日顺延） |",
        f"| 分位窗口 | `W={p['W']}`（10 年）· `MIN_SAMP={p['MIN_SAMP']}` | "
        "`n_eff = min(W, t+1)` 自适应；`n_eff<MIN_SAMP` → 冷启动，倍数 1.0 |",
        "| 分位定义 | `count(PE窗口 ≤ PE_t)/n_eff` | **必须含当日**（`<=`），最低分是 `1/n_eff` 而非 0 |",
        f"| 五档倍数 | 2.0 / 1.5 / 1.0 / 0.5 / 0.0 | 阈值 "
        f"{' / '.join(f'{x:.0%}' for x in p['TIER_EDGES'])}，**左闭右开** |",
        f"| 单月投入 | `min(mult×B, BUDGET_M + cash_pool)` | 不能超支；`B={p['B']:,.0f}` 只影响绝对金额 |",
        "| 止盈 | 默认**关闭** | `pct≥0.90` 且浮盈 ≥30% → 卖 50%，冷却 20 日，`pct<0.60` 解除 |",
        "| 现金池再投资 | 默认**关闭** | `pct<0.20` 时额外投 `min(cash, 1.0×B)` |",
        "",
        f"样本 **{frame.index[0]:%Y-%m-%d} ~ {frame.index[-1]:%Y-%m-%d}**"
        f"（{(frame.index[-1] - frame.index[0]).days / 365.25:.1f} 年 · {len(frame)} 个交易日 · "
        f"{len(G2['sim']['tiers'])} 个决策月）。",
        "",
        "### 1.1 分位窗口的三个里程碑（PE 只有 2011-06 起，这是硬约束）",
        "",
        "| 里程碑 | 日期 | 含义 |",
        "|---|---|---|",
        f"| PE 首日 | {pc.index[0]:%Y-%m-%d} | 分位序列的物理起点（共 {len(pc)} 个交易日） |",
        f"| 第 {p['MIN_SAMP']} 个 PE 交易日 | {pc.index[p['MIN_SAMP'] - 1]:%Y-%m-%d} | "
        "`MIN_SAMP` 之后首个有效分位 |",
        f"| **回测起点**（首个分位可用的决策日） | **{start:%Y-%m-%d}** | 规则从这里开始第一次投钱 |",
        "| 第 1260 个（5 年） | 2016-12-08 | `W=1260` 才成立 |",
        f"| 第 {p['W']} 个（10 年） | {warm_end:%Y-%m-%d} | 默认 `W={p['W']}` 才真正成立 |",
        "",
        "> **推论**：本次回测的前半段（"
        f"{start:%Y-%m} ~ {warm_end:%Y-%m}）里 `n_eff` 从 {p['MIN_SAMP']} 一路扩到 {p['W']}，"
        "**分位含义逐月漂移**（同一 PE 值在 2016 年可能是 30%、2026 年是 55%）。"
        "因此该段绩效在 §4 单列，且**不得用于调参**（R-A13）。",
        "",
        "## 2. 对照组主表（三套收益口径同时给）",
        "",
        "| 组 | 规则 | 份额化年化① | XIRR② | 简单收益③ | 最大回撤 | 夏普 | "
        "资金利用率 | 成本优势 | 投入笔数 |",
        "|---|---|---:|---:|---:|---:|---:|---:|---:|---:|",
    ]
    for r in gs:
        L.append(f"| {short(r['name'])} | {r['note']} | **{dca.fmt_pct(r['cagr'])}** | "
                 f"{dca.fmt_pct(r['xirr'])} | {dca.fmt_pct(r['simple_ret'])} | "
                 f"{dca.fmt_pct(r['mdd'])} | {r['sharpe']:.2f} | {r['utilization']:.0%} | "
                 f"{dca.fmt_pct(r['cost_edge'])} | {r['n_invest']} |")

    L += [
        "",
        "| 组 | 年化 | 波动 | Sortino | Calmar | 累计到账 | 期末资产 | 累计费用 | 现金利息 |",
        "|---|---:|---:|---:|---:|---:|---:|---:|---:|",
    ]
    for r in gs:
        L.append(f"| {short(r['name'])} | {dca.fmt_pct(r['cagr'])} | "
                 f"{r['vol'] * 100:.2f}% | {r['sortino']:.2f} | "
                 f"{dca.fmt_x(r['calmar'])} | {r['total_in']:,.0f} | {r['final_value']:,.0f} | "
                 f"{r['fee']:,.0f} | {r['interest']:,.0f} |")

    d_g2_g1 = (G2["cagr"] - G1["cagr"]) * 100
    d_g2_g0 = (G2["cagr"] - G0["cagr"]) * 100
    d_g2_g3 = (G3["cagr"] - G2["cagr"]) * 100
    L += [
        "",
        f"> **买入口径的锚**：同期沪深300 全收益指数年化 **{idx_ann * 100:+.2f}%**、"
        f"最大回撤 **{mdd_bh * 100:+.2f}%**。G0（每月到账即满额投入）年化 "
        f"{G0['cagr'] * 100:+.2f}%、回撤 {G0['mdd'] * 100:+.2f}% —— 与指数几乎重合，"
        "说明**账户模型没有隐藏的额外收益或漏记成本**（这正是 V-A08 要验的东西）。",
        f"> **本策略 vs 两个基准**：对 G1（等额定投）**{d_g2_g1:+.2f}pp/年**，"
        f"对 G0（满额定投）**{d_g2_g0:+.2f}pp/年**。规格 §6 特意强调这两个都要报："
        "比 G0 才是「择时 vs 不择时」，比 G1 是「择时 vs 等额」。",
        f"> **资金利用率 {G2['utilization']:.0%}**：智能定投必然少投（高估档停投），"
        f"G0 是 {G0['utilization']:.0%}。**「收益率高但利用率低」等于「少玩了一半的钱」**，"
        "所以主口径必须是份额化净值（已把到账节奏归一化），简单收益率③在旁边只作信息展示。",
        f"> **成本优势 `cost_edge = {dca.fmt_pct(G2['cost_edge'])}`**："
        f"平均持仓成本 {G2['avg_cost']:.0f} vs 期间平均市价 {G2['sim']['mkt_mean']:.0f}"
        "（负值 = 买得比均价便宜，这是 R-A11 唯一能直接证明「择时有效」的指标）。"
        f"**但绝对值被价格趋势污染**：G0（每月机械满额买入、完全不含择时）也是 "
        f"{dca.fmt_pct(G0['cost_edge'])} —— 因为沪深300 长期上行，「早期买入」天然比「全期均价」便宜。"
        f"所以可比的是**它相对 G0 的差 = {(G2['cost_edge'] - G0['cost_edge']) * 100:+.2f}pp**，"
        "这一块才是分位择时真正贡献的降低成本。",
        f"> **③ 简单收益率 vs ① 份额化净值的排序会翻转**：G4 的③（{G4['simple_ret'] * 100:+.2f}%）"
        f"高于 G2（{G2['simple_ret'] * 100:+.2f}%），但①相反（{G4['cagr'] * 100:+.2f}% vs "
        f"{G2['cagr'] * 100:+.2f}%）—— 因为 G4 把大部分钱留到低点才投，分母（Σ投入）小，"
        "「累计赚了多少÷投入」被系统性抬高。**这就是规格 R-A09 说「只报③的结论视为不合格」的原因。**",
        f"> **XIRR② 系统性低于 ①**（G2 {dca.fmt_pct(G2['xirr'])} vs {dca.fmt_pct(G2['cagr'])}）："
        "XIRR 是**资金加权**收益，越晚到账的钱权重越大；本样本里 2021 年之后（钱最多的时候）"
        "是弱回报期，所以「个人真实体验」比净值曲线更差。两个口径都要看：①回答「这套规则行不行」，"
        "②回答「我这样投到底赚多少」。",
        f"> **增强版（R-A07 现金池再投资）** {d_g2_g3:+.2f}pp/年（"
        f"{G3['cagr'] * 100:+.2f}% vs {G2['cagr'] * 100:+.2f}%），回撤 "
        f"{G3['mdd'] * 100:+.2f}% vs {G2['mdd'] * 100:+.2f}% —— 两版按规格必须**分别报告**，"
        "不合并。G4「等低点」是 {:.2f}%，利用率 {:.0%}：".format(G4["cagr"] * 100, G4["utilization"])
        + "「攒着等低点」在 10 年维度上会长期空仓，这本身就是它的成本。",
        "",
        "## 3. 分档命中统计（R-A12 · 信号有效性检验）",
        "",
        "| 档位 | 月份数 | 占比 | 倍数 | 当期分位均值 | 分位区间 | 投入金额 | 投入占比 | "
        "**其后 12 个月收益均值** | 12 个月胜率 |",
        "|---|---:|---:|---:|---:|---|---:|---:|---:|---:|",
    ]
    edges = [0.0] + list(p["TIER_EDGES"]) + [1.0]
    for r in tiers:
        rng = "—" if r["tier"] < 0 else f"{edges[r['tier']]:.0%} ~ {edges[r['tier'] + 1]:.0%}"
        pct_txt = "—" if r["pct_mean"] is None else f"{r['pct_mean']:.1%}"
        win_txt = "—" if r["fwd12_win"] is None else f"{r['fwd12_win']:.0%}"
        L.append(f"| {r['name']} | {r['n']} | {r['share']:.0%} | {r['mult']:g}× | "
                 f"{pct_txt} | {rng} | {r['invest']:,.0f} | {r['invest_share']:.0%} | "
                 f"{dca.fmt_pct(r['fwd12_mean'])} | {win_txt} |")

    deep = next((r for r in tiers if r["tier"] == 0), None)
    top = next((r for r in tiers if r["tier"] == 4), None)
    L += [
        "",
        f"> **这是全篇最关键的一张表**：若「深度低估档」其后 12 个月收益"
        f"（{dca.fmt_pct(deep['fwd12_mean']) if deep else '—'}）并不高于「高估档」"
        f"（{dca.fmt_pct(top['fwd12_mean']) if top else '—'}），则分位对中期收益"
        "**没有预测力**，策略A 的全部超额只能来自「买得便宜」这一点点成本优势。",
        "",
        "## 4. warmup 段单列（R-A02 要求 · 该段不得用于调参）",
        "",
        "| 区间 | 份额化年化 | 最大回撤 | 夏普 | 资金利用率 |",
        "|---|---:|---:|---:|---:|",
    ]
    for r in seg_rows:
        L.append(f"| {r['name']} | {dca.fmt_pct(r['cagr'])} | {dca.fmt_pct(r['mdd'])} | "
                 f"{r['sharpe']:.2f} | {r['utilization']:.0%} |")
    L += [
        f"| 全区间（G2） | {dca.fmt_pct(G2['cagr'])} | {dca.fmt_pct(G2['mdd'])} | "
        f"{G2['sharpe']:.2f} | {G2['utilization']:.0%} |",
        "",
        "> warmup 段（`n_eff` 从 500 扩到 2520）与「10 年窗口成立后」的绩效**不可直接比较**："
        "同一 PE 值在两段里的分位读数不同，规则的行为本身就变了。若要让全区间口径一致，"
        "把 `W` 改成 1260（5 年，2016-12 起成立），代价是分位对长期估值中枢变化不敏感。",
        "",
        "## 5. 逐年（G2）",
        "",
        "| 年 | 份额化收益 | 年内最大回撤 | 资金利用率 | 年内 PE 分位均值 |",
        "|---|---:|---:|---:|---:|",
    ]
    for r in yrs:
        u_txt = "—" if r["utilization"] is None else f"{r['utilization']:.0%}"
        p_txt = "—" if r["pct_mean"] is None else f"{r['pct_mean']:.0%}"
        L.append(f"| {r['year']} | {dca.fmt_pct(r['ret'])} | {dca.fmt_pct(r['mdd'])} | "
                 f"{u_txt} | {p_txt} |")

    L += [
        "",
        "## 6. 为什么**不**做「把 B 放大到 utilization≈100%」的对照（R-A10 的份额化不动点）",
        "",
        "规格 R-A10 建议：`utilization < 60%` 时把 `B` 放大再跑一次，让利用率接近 100%，",
        "以便和「满仓」公平比较。**在份额化净值口径下这是一个恒等变换**：",
        "",
        "```",
        "B → kB  ⇒  月额度 2kB、现金池 k·cash、shares k·shares、units k·units",
        "nav_unit = (shares·px + cash) / units   ⇒   分子分母同时 ×k  ⇒  完全不变",
        "```",
        "",
        "也就是说：**`nav_unit` 与 `B` 无关**，放大额度不会改变任何一年的收益，只是把绝对金额放大。",
        "R-A10 的那个对照只在「简单收益率③」这种错误口径下才有意义（把分母做大、把虚高的",
        "收益率压回真实水平）——而本变种恰恰不把③当结论。所以这里**只披露 utilization，不做那个对照**，",
        "并把这条理由写进报告，而不是默默执行一条没有信息量的操作。",
        "",
        "## 7. 参数敏感性（组合层 · G2，只改一格）",
        "",
        "| 档位 | 起点 | 份额化年化 | 最大回撤 | 夏普 | 资金利用率 | 成本优势 | 投入笔数 |",
        "|---|---|---:|---:|---:|---:|---:|---:|",
    ]
    for r in sens:
        L.append(f"| {r['name']} | {r['start']} | {dca.fmt_pct(r['cagr'])} | "
                 f"{dca.fmt_pct(r['mdd'])} | {r['sharpe']:.2f} | {r['utilization']:.0%} | "
                 f"{dca.fmt_pct(r['cost_edge'])} | {r['n_invest']} |")

    real = [r for r in sens if r["n_invest"] > 0]
    anns = [r["cagr"] for r in real]
    L += [
        "",
        f"> {len(real)} 档的分位窗口 / 阈值 / 倍数 / rf / 滑点全部落在 "
        f"{min(anns) * 100:+.2f}% ~ {max(anns) * 100:+.2f}%（宽度 "
        f"{(max(anns) - min(anns)) * 100:.2f}pp）。**这个宽度本身就说明结论对参数不敏感**，"
        "但同时也意味着：在 13 年样本 + 每月只有 12 次决策的规则上，任何参数都没有统计显著性，"
        "只能说明「没有哪一格是灾难」。**档位阈值（20/40/60/80）是规格 §7 点名的过拟合高发区**，"
        "本表里 15/35/60/85 与 25/45/65/85 的差异就在这个宽度内 —— 不要按这张表挑参数。",
        "> **各档起点不同**（`W` 越小、`MIN_SAMP` 越小，分位越早可用 → 样本越长），"
        "所以这张表只能用来查「有没有哪一格是灾难」，**不能横向排名**（规格 R-A13 同义）。",
        "> **`LOT=100` 那一行是口径演示、不是策略**：指数点位约 4000，1 手 = 100 份 ≈ 40 万元，"
        "远超 2 万元的月度额度，于是**一次也买不进去**（n_invest=0，净值恒为 1）。"
        "这恰好说明「ETF 整手」与「指数点位口径」不兼容 —— 实际执行必须走场外基金（小数份额），"
        "这也是本变种默认 `LOT=0` 的原因（§10 口径改写 1）。",
        "",
        "## 8. 验收断言（V-A01 ~ V-A08）",
        "",
        "| 断言 | 结果 | 明细 |",
        "|---|---|---|",
    ]
    for name, ok, detail in checks:
        L.append(f"| {name} | {'**PASS**' if ok else '**FAIL**'} | {detail} |")

    L += [
        "",
        "> V-A03 / V-A04 是这套账户模型最强的单测：把五档全部改成同一个值，结果必须与对应的",
        "> 常量组**逐日**完全一致（差 <1e-9）。它证明「五档逻辑」只是套在同一个账户模型上的",
        "> 一层选择器，模型本身没有任何隐藏的加减仓。",
        "",
        "## 9. 已知失效场景（规格 §8 · 必须在报告里写）",
        "",
        "1. **低估值陷阱（2011-2014）**：沪深300 PE 长期 10~12 倍、分位长期 <20%，策略会连续加分倍买入，"
        "结果指数横盘阴跌。分位法假设「均值回复」，当**盈利周期整体下移**时，分位会长期贴底而价格不涨。",
        "2. **盈利断崖导致 PE 假高（2020Q1、2022Q2）**：EPS 骤降使 PE 被动抬升，分位跳到高位触发停投，"
        "但此时恰是价格底部 —— **分位法最典型的反向信号**。",
        "3. **分位口径漂移**：2016 年用 4.5 年窗口算的分位，与 2026 年用 10 年窗口算的分位不可比。"
        "跨期比较必须在同一 `n_eff` 下重算（§4 因此把 warmup 段单列）。",
        "4. **A500 不可用**：PE 仅 2024-09 起（月度样本 25 个 < 36 门槛），任何分位都无统计意义。"
        "本变种因此**只做沪深300**，不做 A500 平行版。",
        "5. **样本内决策次数少**：每月 1 次 × 13 年 ≈ 156 次，且高度自相关（同一轮低估会连续几十个月），"
        "有效独立样本远小于 156。任何「年化 5%」都不该当预期收益。",
        "",
        "缓解方向（不纳入主口径，规格 §8 列为扩展组）：",
        "- **双锚** `pct = 0.5·pct_pe + 0.5·pct_pb`：PB 受盈利波动更小，能部分对冲场景 2，"
        "但本仓库**没有沪深300 的 PB 日频序列**，需另找数据源（成本高，本期不做）；",
        "- **分位 + 趋势双确认**：仅在 `pct<0.40` **且** `close > MA250` 时加倍 —— "
        "这实际上是策略A 与策略B（`../v7_trend_exit/`）的融合，两者合并必须重新做完整回测，"
        "不能靠推理认为「叠加更好」（规格 §11 明令）。",
        "",
        "## 10. 口径改写清单（与规格原文的差异，逐条说明理由）",
        "",
        "| # | 规格原文 | 本实现 | 理由 |",
        "|---|---|---|---|",
        "| 1 | 成交价 `px_{t+1}×(1+s)`，ETF **整手** `floor( invest/(px·(1+s)) /100 )×100` | "
        "默认 `LOT=0`（场外小数份额），`LOT=100` 可切回整手（敏感性表最后一行） | "
        "标的用**指数全收益**而非 ETF 价格，指数量级下「整手」无经济含义；"
        "且实际执行通道是场外基金（天然小数份额）。切整手后 10000 元的月度额度会大量流拍 |",
        "| 2 | 佣金 `0.00025` | 保留，另加**赎回费 0.5%**（规格未写，但 R-A04 止盈一旦开启就会用到） | "
        "不写赎回费会让 G5 的止盈分支白拿收益 |",
        "| 3 | 未提现金池的申赎时滞 | 未计 | 月度频率下影响 <0.05pp |",
        "| 4 | `rf` 默认 1.8% | 同 | 与 v7 一致；主策略 `rule.CASH_ANNUAL=1.40%` 的差额在敏感性表 |",
        "| 5 | 报告要求 `tier_stats.csv` / `yearly.csv` | 合并进本 md + `_bt.jsonl` | 仓库产物统一为 md+jsonl（AGENTS §8） |",
        "",
        "> **模拟结果，非投资建议。**",
    ]

    with open(OUT_MD, "w", encoding="utf-8") as f:
        f.write("\n".join(L))

    print("报告:", OUT_MD)
    print("结果:", OUT_JSONL)
    print(f"样本 {frame.index[0]:%Y-%m-%d} ~ {frame.index[-1]:%Y-%m-%d} · "
          f"{len(frame)} 日 · {len(G2['sim']['tiers'])} 个决策月 · "
          f"指数年化 {idx_ann * 100:+.2f}%")
    for r in gs:
        print(f"  {r['name'][:22]:24s} 年化 {dca.fmt_pct(r['cagr']):>9s} · "
              f"XIRR {dca.fmt_pct(r['xirr']):>9s} · 回撤 {dca.fmt_pct(r['mdd']):>8s} · "
              f"利用率 {r['utilization']:.0%} · 成本优势 {dca.fmt_pct(r['cost_edge'])}")
    for name, ok, detail in checks:
        print(f"  [{'PASS' if ok else 'FAIL'}] {name}  {detail}")
    bad = [c for c in checks if not c[1]]
    if bad:
        raise AssertionError(f"{len(bad)} 条验收断言未通过: " + " / ".join(x[0] for x in bad))


if __name__ == "__main__":
    main()
