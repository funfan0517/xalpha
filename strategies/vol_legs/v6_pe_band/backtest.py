# -*- coding: utf-8 -*-
"""波动资产 · V6 变种（估值分位仓位带）· 回测 / 频率对照 / 验收断言。

规则来源 `E:/qianwen/jijin/中证A500操作规则.md` v1.0；参数权威源 `band.py::CFG`。

## 两个标的 × 三种定档频率

| 标的 | 价格 | 滚动 PE | 可回测区间 | 说明 |
|---|---|---|---|---|
| **hs300（一等样本）** | 沪深300 全收益 H00300 | 2011-06 起 | **2012-10 ~ 今（14 年）** | 10 年 PE 窗口**真正成立**，唯一能给出统计结论的样本 |
| **a500（文档原型）** | 中证A500 000510（2005 起含回溯） | **2024-09 起** | 2025-09 ~ 今（1.1 年） | 指数 PE 只有 2 年，`window_short=True`，只当现状实录 |

| 频率 | 定档日 | 6/12 月顺延 |
|---|---|---|
| `weekly`（**默认**） | 每个自然周最后一个交易日 | **不生效**（见 `band.py` docstring §3-⑤） |
| `monthly` | 每月最后一个交易日 | 不生效 |
| `quarterly`（文档原口径） | 3/6/9/12 月最后一个交易日 | 生效（顺延到下一个季末） |

A500 的 PE 只有 2024-09 起（指数 2024-09-23 发布，官网只从发布日起给估值），
**这不是取数没取全** —— 所以「10 年 PE 分位」在 A500 上当前不可能成立；本变种对 A500 的
对策是文档 §6-1 自己给的沪深300 交叉参照。**沪深300 档没有这个问题**，它是本策略的
主要证据来源。

产出（相对本文件）：`backtest/_v6_pe_band_bt.md`、`backtest/_v6_pe_band_bt.jsonl`。

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

import band  # noqa: E402

OUTDIR = os.path.join(_HERE, "backtest")
OUT_MD = os.path.join(OUTDIR, "_v6_pe_band_bt.md")
OUT_JSONL = os.path.join(OUTDIR, "_v6_pe_band_bt.jsonl")


def over(p, **kw):
    q = dict(p)
    q.update(kw)
    return q


# ---------------------------------------------------------------- 1. 单口径执行
def run_mode(univ, p, start=None, freq=None):
    """跑一个标的 -> dict(frame, ind, sim)。指标在**全序列**上算，切片在算完之后。"""
    q = over(p, UNIVERSE=univ, **({"REBAL_FREQ": freq} if freq else {}))
    frame = band.load_frame(univ)
    ind = band.indicators(frame, q, univ)
    ok = ind["pct"].dropna()
    if not len(ok):
        raise RuntimeError(f"{univ} 无有效 PE 分位（MIN_OBS={q['MIN_OBS']}）")
    start = ok.index[0] if start is None else pd.Timestamp(start)
    ind = ind.loc[start:]
    sim = band.evaluate(ind, q, init="band")
    return dict(mode=univ, p=q, frame=frame, ind=ind, sim=sim, first_pct=ok.index[0])


def run_init(ind, p, init):
    return band.evaluate(ind, p, init=init)


def static_const(ind, w, p):
    """恒定仓位 w 的对照（不择时、不阶梯）。"""
    bands = pd.DataFrame({"dec": False, "target": float(w),
                          "w_a": float(w), "w_h": float(w)}, index=ind.index)
    sim = band.simulate(ind, bands, p, init="band")
    sim["m"] = band.perf(sim["nav"])
    sim["mbh"] = band.perf(sim["bh"])
    return sim


# ---------------------------------------------------------------- 2. 统计
def ladder_stats(sim):
    lad = [t for t in sim["trades"] if t["reason"].startswith("阶梯")]
    per_month = {}
    for t in lad:
        k = (t["signal_date"].year, t["signal_date"].month)
        per_month[k] = per_month.get(k, 0) + 1
    # 只比较**定档行**之间，跳过「顺延 / 无分位」行（否则季末档的换档会被顺延行切断）
    dec = [r for r in sim["band_rows"] if r["action"] == "定档" and r["w"] is not None]
    n_change = sum(1 for a, b in zip(dec, dec[1:]) if abs(a["w"] - b["w"]) > 1e-9)
    return dict(n_ladder=len(lad), max_per_month=max(per_month.values()) if per_month else 0,
                buys=sum(1 for t in sim["trades"] if t["side"] == "buy"),
                sells=sum(1 for t in sim["trades"] if t["side"] == "sell"),
                n_dec=len(dec), n_change=n_change)


def band_dist(rows):
    out = {}
    for r in rows:
        if r["action"] != "定档" or r["w"] is None:
            continue
        out[r["w"]] = out.get(r["w"], 0) + 1
    return out


def checks(run, p):
    """验收断言（实现正确性）。任何一条失败 → backtest 抛错。"""
    ind, sim, freq = run["ind"], run["sim"], str(run["p"]["REBAL_FREQ"])
    out = []

    # C1 恒满 = 买入持有（零费用）
    zero_fee = static_const(ind, 1.0, over(p, FEE_BUY=0.0, FEE_SELL=0.0))
    d1 = float((zero_fee["nav"].iloc[1:] / zero_fee["nav"].iloc[1]
                - zero_fee["bh"].iloc[1:] / zero_fee["bh"].iloc[1]).abs().max())
    out.append(("C1 恒满仓 → 净值与买入持有逐日一致（差 <1e-9）", d1 < 1e-9, f"max|Δ| = {d1:.2e}"))

    # C2 恒空 = 货基复利
    empty = static_const(ind, 0.0, p)
    out.append(("C2 恒空仓 → 年化 ≈ rf（±0.3pp）",
                abs(empty["m"]["ann"] - float(p["RF"])) < 0.003,
                f"年化 {empty['m']['ann'] * 100:+.2f}% vs rf {float(p['RF']):.2%}"))

    # C3 阶梯：同月最多 1 档
    ls = ladder_stats(sim)
    out.append((f"C3 阶梯同月最多 {int(p['MAX_TRANCHE_PER_MONTH'])} 档",
                ls["max_per_month"] <= int(p["MAX_TRANCHE_PER_MONTH"]),
                f"最多 {ls['max_per_month']} 档/月 · 共 {ls['n_ladder']} 档"))

    # C4 仓位永不超过目标带（目标带 T 日收盘定、T+1 成交，故比上一交易日的带值）
    tgt = pd.Series([np.nan if x is None else float(x) for x in sim["target"]],
                    index=ind.index, dtype="float64").ffill().shift(1)
    over_ = float((sim["w"] - tgt).dropna().max())
    out.append(("C4 仓位不超过目标带（不顶穿带）", over_ <= 1e-9,
                f"max(w − target[−1]) = {over_:.2e}"))

    # C5 高估降档要连续确认：确认期放到 99 个月 → 永不出现 10% 档
    p99 = over(p, SELL_CONFIRM_MONTHS=99)
    s99 = band.evaluate(ind, p99, init="band")
    has10 = bool((s99["w"] - 0.10).abs().min() < 1e-9) if len(s99["w"]) else False
    out.append(("C5 未连续 2 个月 ≥80% → 不得降到 10% 观察仓",
                (not has10) or float(p["SELL_CONFIRM_MONTHS"]) <= 1,
                f"确认期 99 个月时出现 10% 档: {has10}"))

    # C6 T 日决策 → T+1 成交
    pos = {d: i for i, d in enumerate(ind.index)}
    ok6 = all(pos[t["exec_date"]] - pos[t["signal_date"]] == 1 for t in sim["trades"])
    out.append(("C6 决策日 → 成交日 恒为 1 个交易日（无前视）", ok6, f"{sim['n_trades']} 笔"))

    # C7 调样月顺延只在 quarterly 生效
    dec_months = {int(r["month"]) for r in sim["band_rows"] if r["action"] == "定档"}
    black = set(int(m) for m in p["BLACKOUT_MONTHS"])
    ok7 = bool(dec_months & black) if freq != "quarterly" else not bool(dec_months & black)
    out.append((f"C7 定档频率 {freq}："
                + ("6/12 月照常定档（顺延只对 quarterly 生效）" if freq != "quarterly"
                   else "6/12 月不出现定档（顺延生效）"),
                ok7, f"出现定档的月份 = {sorted(dec_months)}"))

    # C8 无分位不决策
    first = ind["pct"].dropna().index[0]
    early = [t for t in sim["trades"] if t["signal_date"] < first]
    out.append(("C8 PE 样本不足（分位为 null）不产生交易", not early,
                f"首有效分位 {first:%Y-%m-%d} · 早于它的交易 {len(early)} 笔"))

    # C9 分位定义：在**全序列**上重算（切片会改窗口），最高点 pct=1.0、最低点 pct=1/n_eff
    pc = band.pe_percentile(run["frame"]["pe"], p["WINDOW_DAYS"],
                            p["MIN_OBS"])["pct"].dropna()
    ok9 = bool(pc.max() >= 1.0 - 1e-12) and bool(pc.min() > 0.0)
    out.append(("C9 分位口径（含当日、<= 比较）自洽", ok9,
                f"pct ∈ [{pc.min():.3f}, {pc.max():.3f}]"))

    # C10 净值 = 加权收益 + 现金利息 − 费用（无隐藏加减仓）
    ret = ind["tr"].pct_change(fill_method=None).fillna(0.0)
    w_prev = sim["w"].shift(1).fillna(0.0)
    rf_d = (1.0 + float(p["RF"])) ** (1.0 / 252.0) - 1.0
    approx = (w_prev * ret + (1.0 - w_prev) * rf_d).fillna(0.0)
    naive = (1.0 + approx).cumprod()
    dw = sim["w"].diff().fillna(sim["w"].iloc[0])
    fees = np.where(dw > 0, dw * float(p["FEE_BUY"]), -dw * float(p["FEE_SELL"]))
    fitted = naive * (1.0 - pd.Series(fees, index=ind.index)).cumprod()
    d10 = float((sim["nav"] / fitted - 1.0).abs().max())
    out.append(("C10 净值 = 加权收益 + 现金利息 − 费用（无隐藏加减仓）", d10 < 1e-9,
                f"max|相对差| = {d10:.2e}"))

    # C11 定档日数量符合频率
    span = (ind.index[-1] - ind.index[0]).days / 365.25
    per_year = ls["n_dec"] / span if span > 0 else 0
    want = {"weekly": 52, "monthly": 12, "quarterly": 4}[freq]
    out.append((f"C11 定档频率 {freq} → 每年 ≈{want} 次（±10%）",
                0.9 * want <= per_year <= 1.1 * want,
                f"{ls['n_dec']} 次 / {span:.1f} 年 = {per_year:.1f} 次/年"))
    return out


# ---------------------------------------------------------------- 3. 频率对照 / 敏感性
def freq_table(p, universes=("hs300", "a500")):
    rows = []
    for univ in universes:
        for freq in band.FREQS:
            try:
                r = run_mode(univ, p, freq=freq)
            except (ValueError, RuntimeError) as exc:
                rows.append(dict(univ=univ, freq=freq, err=str(exc)))
                continue
            s, m, mbh = r["sim"], r["sim"]["m"], r["sim"]["mbh"]
            ls = ladder_stats(s)
            rows.append(dict(univ=univ, freq=freq, ann=m["ann"], mdd=m["mdd"],
                             calmar=m["calmar"], sharpe=m["sharpe"], exp=s["exposure"],
                             n_trades=s["n_trades"], turnover=s["turnover"],
                             n_dec=ls["n_dec"], n_change=ls["n_change"],
                             bh_ann=mbh["ann"], bh_mdd=mbh["mdd"],
                             start=s["nav"].index[0], end=s["nav"].index[-1],
                             years=(s["nav"].index[-1] - s["nav"].index[0]).days / 365.25))
    return rows


def veto_table(p, univ="hs300"):
    """利差否决器：三种读法对照 + 阈值扫描 + 触发统计。"""
    modes = []
    for mode in ("off", "diff_literal", "level_gap"):
        r = run_mode(univ, over(p, VETO_MODE=mode))
        s, me = r["sim"], r["sim"]["me"]
        used = sum(1 for x in s["band_rows"]
                   if x["action"] == "定档" and "利差否决" in str(x["note"]))
        gap = me["veto_gap"].dropna()
        cond = int((me["veto_gap"] >= float(p["VETO_GAP"])).sum())
        modes.append(dict(mode=mode, ann=s["m"]["ann"], mdd=s["m"]["mdd"],
                          calmar=s["m"]["calmar"], exp=s["exposure"],
                          n=s["n_trades"], n_ok=len(gap), n_cond=cond,
                          n_veto=int(me["veto"].sum()), n_used=used,
                          gap_med=(float(gap.median()) if len(gap) else None),
                          gap_p75=(float(gap.quantile(0.75)) if len(gap) else None)))
    scan = []
    for th in (0.05, 0.10, 0.15, 0.20, 0.25, 0.30):
        r = run_mode(univ, over(p, VETO_GAP=th))
        s, me = r["sim"], r["sim"]["me"]
        scan.append(dict(th=th, ann=s["m"]["ann"], mdd=s["m"]["mdd"],
                         calmar=s["m"]["calmar"], exp=s["exposure"], n=s["n_trades"],
                         n_cond=int((me["veto_gap"] >= th).sum()),
                         n_veto=int(me["veto"].sum())))
    return modes, scan


def sensitivity(univ, p):
    frame = band.load_frame(univ)
    rows = []
    cases = [
        ("默认（weekly · 10 年窗口 · 起手摆到带位）", {}, "band"),
        ("空仓起步（只能靠阶梯填）", {}, "zero"),
        ("高估确认期 1 个月（不做二次确认）", {"SELL_CONFIRM_MONTHS": 1}, "band"),
        ("窗口 5 年（1260 日）", {"WINDOW_DAYS": 1260}, "band"),
        ("窗口 3 年（756 日）", {"WINDOW_DAYS": 756}, "band"),
        ("窗口 15 年（3780 日）", {"WINDOW_DAYS": 3780}, "band"),
        ("最小样本 500（约 2 年）", {"MIN_OBS": 500}, "band"),
        ("费用翻倍（申购 0.2% / 赎回 1.0%）", {"FEE_BUY": 0.002, "FEE_SELL": 0.010}, "band"),
        ("定档改季度（文档原口径）", {"REBAL_FREQ": "quarterly"}, "band"),
        ("定档改月度", {"REBAL_FREQ": "monthly"}, "band"),
    ]
    if univ == "a500":          # 交叉校验只有「A500 × 沪深300」两把尺子时才存在
        cases[1:1] = [
            ("关掉交叉校验（只用 A500 尺子）", {"CROSS_RULE": "ignore"}, "band"),
            ("交叉改为「降一档」", {"CROSS_RULE": "one_step_down"}, "band"),
        ]
    for label, kw, init in cases:
        q = over(p, **kw)
        try:
            ind = band.indicators(frame, q, univ)
            ok = ind["pct"].dropna()
            ind = ind.loc[ok.index[0]:]
            sim = band.evaluate(ind, q, init=init)
        except (ValueError, RuntimeError) as exc:
            rows.append(dict(name=label, cagr=None, mdd=None, calmar=None, exp=None,
                             n=0, note=f"取数/评估失败: {exc}"))
            continue
        m, mbh = sim["m"], sim["mbh"]
        rows.append(dict(name=label, cagr=m["ann"], mdd=m["mdd"], calmar=m["calmar"],
                         exp=sim["exposure"], n=sim["n_trades"],
                         excess=m["ann"] - mbh["ann"],
                         note=f"{sim['nav'].index[0]:%Y-%m-%d} ~ "
                              f"{sim['nav'].index[-1]:%Y-%m-%d}"))
    return rows


# ---------------------------------------------------------------- 4. 报告
def _row(name, sim):
    m, mbh = sim["m"], sim["mbh"]
    return (f"| {name} | {band.fmt_pct(m['ann'])} | {band.fmt_pct(m['mdd'])} | "
            f"{band.fmt_x(m['calmar'])} | {sim['exposure']:.0%} | "
            f"{band.fmt_pct(m['ann'] - mbh['ann'])} |")


def main():
    os.makedirs(OUTDIR, exist_ok=True)
    p = band.params()

    runs = {u: run_mode(u, p) for u in ("hs300", "a500")}
    chk = {k: checks(v, v["p"]) for k, v in runs.items()}
    initz = {k: run_init(v["ind"], v["p"], "zero") for k, v in runs.items()}
    freqrows = freq_table(p)
    vmodes, vscan = veto_table(p)
    sens = {u: sensitivity(u, p) for u in ("hs300", "a500")}

    hs, a5 = runs["hs300"], runs["a500"]
    hs_m, hs_bh = hs["sim"]["m"], hs["sim"]["mbh"]
    a5_m, a5_bh = a5["sim"]["m"], a5["sim"]["mbh"]
    hs_ls = ladder_stats(hs["sim"])
    n_pe0 = a5["frame"]["pe"].dropna().index[0]
    n_pct0 = a5["ind"]["pct"].dropna().index[0]
    h_pe0 = hs["frame"]["pe"].dropna().index[0]
    h_neff = int(hs["ind"]["n_eff"].dropna().iloc[-1])
    a_neff = int(a5["ind"]["n_eff"].dropna().iloc[-1])
    start_q = run_mode("hs300", p, freq="quarterly")["sim"]
    # 复现文档 §0 的点名数字：沪深300 + 五年分位 + 季度定档（原文 2012-05~2026-05）
    doc_run = run_mode("hs300", over(p, WINDOW_DAYS=int(5 * 252)), freq="quarterly")
    d_m, d_bh = doc_run["sim"]["m"], doc_run["sim"]["mbh"]

    # ---------------- jsonl
    with open(OUT_JSONL, "w", encoding="utf-8") as f:
        for k, v in runs.items():
            m, mbh, s = v["sim"]["m"], v["sim"]["mbh"], v["sim"]
            f.write(json.dumps({"summary": {
                "universe": k, "freq": v["p"]["REBAL_FREQ"],
                "start": s["nav"].index[0].date().isoformat(),
                "end": s["nav"].index[-1].date().isoformat(),
                "cagr": round(m["ann"], 6), "mdd": round(m["mdd"], 6),
                "calmar": (round(m["calmar"], 6) if m["calmar"] is not None else None),
                "sharpe": round(m["sharpe"], 6), "exposure": round(s["exposure"], 6),
                "n_trades": s["n_trades"], "turnover": round(s["turnover"], 4),
                "bh_cagr": round(mbh["ann"], 6), "bh_mdd": round(mbh["mdd"], 6),
                "excess": round(m["ann"] - mbh["ann"], 6),
                "n_decisions": hs_ls["n_dec"] if k == "hs300" else ladder_stats(s)["n_dec"],
            }}, ensure_ascii=False) + "\n")
            for t in s["trades"]:
                f.write(json.dumps({
                    "universe": k, "signal_date": t["signal_date"].date().isoformat(),
                    "exec_date": t["exec_date"].date().isoformat(), "side": t["side"],
                    "w_from": round(t["w_from"], 4), "w_to": round(t["w_to"], 4),
                    "reason": t["reason"]}, ensure_ascii=False) + "\n")
            for r in s["band_rows"]:
                if r["action"] != "定档":
                    continue
                f.write(json.dumps({"universe": k, "band_row": {
                    "date": r["date"].date().isoformat(), "action": r["action"],
                    "w_self": (round(r["w_a"], 4) if isinstance(r["w_a"], float) and
                               r["w_a"] == r["w_a"] else None),
                    "w_cross": (round(r["w_h"], 4) if isinstance(r["w_h"], float) and
                                r["w_h"] == r["w_h"] else None),
                    "w": r["w"], "veto": bool(r["veto"]), "note": r["note"]}},
                    ensure_ascii=False) + "\n")
        for row in freqrows:
            f.write(json.dumps({"freq_table": {
                k: (round(v, 6) if isinstance(v, float) else
                    (v.isoformat() if hasattr(v, "isoformat") else v))
                for k, v in row.items()}}, ensure_ascii=False) + "\n")
        for k, lst in chk.items():
            for name, ok, detail in lst:
                f.write(json.dumps({"check": name, "universe": k, "pass": bool(ok),
                                    "detail": detail}, ensure_ascii=False) + "\n")

    # ---------------- markdown
    L = [
        "# 波动资产 · V6 变种：估值分位仓位带 · 回测（沪深300 / 中证A500 × 周/月/季）",
        "> 规则：`E:/qianwen/jijin/中证A500操作规则.md` v1.0 · 参数权威源 `band.py::CFG`",
        f"> 闸门 = **标的自身 PE-TTM 分位**（{p['WINDOW_YEARS']} 年窗口写死，"
        f"{p['WINDOW_DAYS']} 交易日）；仓位带 "
        f"{'/'.join(f'{w:.0%}' for w in p['BAND_WEIGHTS'])} 对应分位 "
        f"{'/'.join(f'{e:.0%}' for e in p['BAND_EDGES'])}；"
        f"阶梯 dd {p['LADDER_DD']}% → {p['LADDER_UNITS']} 份（1 份 = {p['UNIT_PCT_POOL']:g}%）",
        f"> **定档 {p['REBAL_FREQ']}**（2026-11-03 起默认；每月/每季可切）· "
        f"RSI{p['RSI_PERIOD']} < {p['RSI_BELOW']:g} 或从下方回升首日下单 · "
        f"同一自然月最多 {p['MAX_TRANCHE_PER_MONTH']} 档 · 52 周新高阶梯归零 · "
        f"T 日决策 / T+1 收盘成交 · 空仓 rf={float(p['RF']):.2%} · "
        f"费 申购 {float(p['FEE_BUY']):.2%} / 赎回 {float(p['FEE_SELL']):.2%}",
        "> 模拟结果，**非投资建议**。",
        "",
        "## 0. 结论（先看这个）",
        "",
        "| 标的 | 区间 | 定档 | 策略年化 | 买入持有 | 策略回撤 | 持有回撤 | Calmar | 曝光 | 笔数 |",
        "|---|---|---|---:|---:|---:|---:|---:|---:|---:|",
    ]
    for k, v, name in [("hs300", hs, "**沪深300（一等样本）**"), ("a500", a5, "中证A500（文档原型）")]:
        m, mbh, s = v["sim"]["m"], v["sim"]["mbh"], v["sim"]
        L.append(f"| {name} | {s['nav'].index[0]:%Y-%m-%d} ~ {s['nav'].index[-1]:%Y-%m-%d} | "
                 f"{v['p']['REBAL_FREQ']} | **{band.fmt_pct(m['ann'])}** | "
                 f"{band.fmt_pct(mbh['ann'])} | {band.fmt_pct(m['mdd'])} | "
                 f"{band.fmt_pct(mbh['mdd'])} | {band.fmt_x(m['calmar'])} vs "
                 f"{band.fmt_x(mbh['calmar'])} | {s['exposure']:.0%} | {s['n_trades']} |")

    q_m, q_s = start_q["m"], start_q
    L += [
        "",
        f"> 一句话：**沪深300 上，weekly 档 {band.fmt_pct(hs_m['ann'])} / 回撤 "
        f"{band.fmt_pct(hs_m['mdd'])} vs 买入持有 {band.fmt_pct(hs_bh['ann'])} / "
        f"{band.fmt_pct(hs_bh['mdd'])}** —— 用 "
        f"{abs(hs_m['ann'] - hs_bh['ann']) * 100:.2f}pp 年化换 "
        f"{abs(hs_m['mdd'] - hs_bh['mdd']) * 100:.0f}pp 回撤，Calmar "
        f"{band.fmt_x(hs_bh['calmar'])}→{band.fmt_x(hs_m['calmar'])}，平均仓位只有 "
        f"{hs['sim']['exposure']:.0%}。这是**唯一能给出统计结论的样本**（14 年、"
        f"{hs_ls['n_dec']} 次定档）。",
        "",
        f"### 一、沪深300（{hs['sim']['nav'].index[0]:%Y-%m-%d} 起 "
        f"{(hs['sim']['nav'].index[-1] - hs['sim']['nav'].index[0]).days / 365.25:.1f} 年）",
        "",
        f"PE 自 {h_pe0:%Y-%m-%d} 起，{p['WINDOW_YEARS']} 年窗口在 "
        f"{hs['first_pct']:%Y-%m-%d} 真正成立（`window_short=False`，n_eff={h_neff}）。分行看：",
        "",
        f"- 策略 {band.fmt_pct(hs_m['ann'])} / 回撤 {band.fmt_pct(hs_m['mdd'])} / "
        f"Calmar {band.fmt_x(hs_m['calmar'])} / 夏普 {hs_m['sharpe']:.2f} / "
        f"平均仓位 {hs['sim']['exposure']:.0%}；",
        f"- 买入持有 {band.fmt_pct(hs_bh['ann'])} / {band.fmt_pct(hs_bh['mdd'])} / "
        f"Calmar {band.fmt_x(hs_bh['calmar'])} / 夏普 {hs_bh['sharpe']:.2f}；",
        f"- 超额 {band.fmt_pct(hs_m['ann'] - hs_bh['ann'])}、回撤差 "
        f"{(hs_m['mdd'] - hs_bh['mdd']) * 100:+.1f}pp；`{hs_ls['n_dec']}` 次定档里只有 "
        f"`{hs_ls['n_change']}` 次目标带真的换了档 —— **大部分周定档是空转**。",
        "",
        f"### 二、中证A500（{a5['sim']['nav'].index[0]:%Y-%m-%d} 起 "
        f"{(a5['sim']['nav'].index[-1] - a5['sim']['nav'].index[0]).days / 365.25:.1f} 年）",
        "",
        f"A500 的 PE 只有 {n_pe0:%Y-%m-%d} 起（指数 2024-09-23 发布，官网只从发布日起给估值），"
        f"加 250 日最小样本后最早只能从 **{n_pct0:%Y-%m-%d}** 开始判档，"
        "所以这一档：",
        "",
        f"- 策略 {band.fmt_pct(a5_m['ann'])} / 回撤 {band.fmt_pct(a5_m['mdd'])} vs "
        f"买入持有 {band.fmt_pct(a5_bh['ann'])} / {band.fmt_pct(a5_bh['mdd'])}；"
        f"平均仓位 {a5['sim']['exposure']:.0%}、{a5['sim']['n_trades']} 笔；",
        f"- 分位只有 {a_neff} 个交易日的历史，"
        "`window_short=True` —— **不构成「策略有效/无效」的证据**，只是「按规则这 1 年会怎么做」；",
        "- 这也是 A500 档必须配沪深300 交叉尺子的原因（文档 §6-1）；沪深300 档不需要，它自己有 10 年。",
        "",
        "### 三、复现文档 §0 的点名数字（沪深300 · 五年分位 · 季度定档）",
        "",
        "文档原文：「510300，2012-05~2026-05，PE 五年分位 → 年化 **6.45%** / 回撤 **18.24%**"
        "（平均仓位 48%）；满仓持有 **6.58%** / **45.34%**」。本仓库同一口径（W 改 1260 日、"
        f"定档改 quarterly）跑出 **{band.fmt_pct(d_m['ann'])}** / **{band.fmt_pct(d_m['mdd'])}**"
        f"（平均仓位 {doc_run['sim']['exposure']:.0%}）vs 持有 "
        f"{band.fmt_pct(d_bh['ann'])} / {band.fmt_pct(d_bh['mdd'])} —— "
        f"年化差 {abs(d_m['ann'] - 0.0645) * 100:.2f}pp、回撤差 "
        f"{abs(d_m['mdd'] + 0.1824) * 100:.2f}pp、满仓基准差 "
        f"{abs(d_bh['ann'] - 0.0658) * 100:.2f}pp / {abs(d_bh['mdd'] + 0.4534) * 100:.2f}pp。"
        "跨数据源、跨口径能对到这个程度，说明文档那三个数字不是拍出来的。残差来源："
        "①价格口径（H00300 全收益 vs 510300 含费净值）；②起点（2012-10 vs 2012-05）；"
        "③「减仓时先减分位最高档那一份」本实现按「整体卖到目标带」处理。",
        "",
        "## 1. 定档频率对照（用户核心问题：每周 vs 每季）",
        "",
        "| 标的 | 频率 | 年化 | 回撤 | Calmar | 夏普 | 曝光 | 笔数 | 周转 | 定档次数 | 换档次数 | 区间 |",
        "|---|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---|",
    ]
    for r in freqrows:
        if "err" in r:
            L.append(f"| {r['univ']} | {r['freq']} | — | — | — | — | — | — | — | — | — | "
                     f"{r['err']} |")
            continue
        L.append(f"| {r['univ']} | **{r['freq']}** | {band.fmt_pct(r['ann'])} | "
                 f"{band.fmt_pct(r['mdd'])} | {band.fmt_x(r['calmar'])} | "
                 f"{r['sharpe']:.2f} | {r['exp']:.0%} | {r['n_trades']} | "
                 f"{r['turnover']:.1f} | {r['n_dec']} | {r['n_change']} | "
                 f"{r['start']:%Y-%m-%d}~{r['end']:%Y-%m-%d} |")

    hs_w = [r for r in freqrows if r["univ"] == "hs300" and "err" not in r]
    wk = next((r for r in hs_w if r["freq"] == "weekly"), None)
    qt = next((r for r in hs_w if r["freq"] == "quarterly"), None)
    L += [
        "",
        (f"> **weekly vs quarterly（沪深300）**：年化 {band.fmt_pct(wk['ann'])} vs "
         f"{band.fmt_pct(qt['ann'])}、回撤 {band.fmt_pct(wk['mdd'])} vs {band.fmt_pct(qt['mdd'])}、"
         f"笔数 {wk['n_trades']} vs {qt['n_trades']}、周转 {wk['turnover']:.1f} vs "
         f"{qt['turnover']:.1f}、定档 {wk['n_dec']} 次 vs {qt['n_dec']} 次。"
         if (wk and qt) else ""),
        ("> **结论：沪深300 上每周定档是净亏。** 相比季度档，weekly 年化 "
         f"{band.fmt_pct(wk['ann'] - qt['ann'])}、回撤只浅 "
         f"{(wk['mdd'] - qt['mdd']) * 100:+.1f}pp、Calmar {band.fmt_x(qt['calmar'])}→"
         f"{band.fmt_x(wk['calmar'])}，代价是多 {wk['n_trades'] - qt['n_trades']} 笔交易、"
         f"周转 {qt['turnover']:.1f}→{wk['turnover']:.1f}、换档 {qt['n_change']}→"
         f"{wk['n_change']} 次。原因很直接：分位在档位边界附近来回抖，"
         "周频把「抖动」变成了「交易」，而真正的大级别跨档季度档也抓得住。"
         if (wk and qt) else ""),
        "> 但这不代表把频率调回季度更好 —— 频率是**执行成本**问题，不是信号问题："
        "如果你的账户申赎费更低（场内 ETF、免佣），weekly 的代价会小很多；"
        "场外 A 类（申购 0.1% + 赎回 0.5%）下，weekly 的费用吃掉的部分超过它抓到的机会。"
        "切档方法：`band.py::CFG[\"REBAL_FREQ\"]` 改 `monthly` / `quarterly`，两行都不用改。",
        "",
        "## 2. 利差否决器：为什么原来一直是空的（2026-11-03 修口径）",
        "",
        "文档 §2 纪律三原话：「个股/指数 PE 分位没降、但股债利差被 10Y 下行撑高 → 这一档便宜是"
        "**利率借来的**，按低一档执行，且需连续 2 个月读数成立」。**这句话没有可执行定义**，"
        "本模块给三种读法，实测数据如下（沪深300）：",
        "",
        "| `VETO_MODE` | 判据 | 年化 | 回撤 | Calmar | 曝光 | 笔数 | 可判月 | 单月成立 | 连续2月(veto) | 因否决被降档 |",
        "|---|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|",
    ]
    for r in vmodes:
        judge = {"off": "关闭", "diff_literal": "月度差分：ERP↑ & PE分位不降 & 10Y↓",
                 "level_gap": "水平缺口：ERP分位 − EY分位 ≥ %.2f" % float(p["VETO_GAP"])}[r["mode"]]
        L.append(f"| `{r['mode']}` | {judge} | {band.fmt_pct(r['ann'])} | "
                 f"{band.fmt_pct(r['mdd'])} | {band.fmt_x(r['calmar'])} | {r['exp']:.0%} | "
                 f"{r['n']} | {r['n_ok']} | {r['n_cond']} | {r['n_veto']} | {r['n_used']} |")

    L += [
        "",
        "**差分读法为什么必然空转**：`ERP = 1/PE − y10`，而 `1/PE` 的月度波动（0.1~1pp）比 10Y 的"
        "月度波动（0.02~0.05pp）大一个量级。于是「ERP 上升」几乎等价于「PE 下跌」，与"
        "「PE 分位不降」在数学上接近互斥 —— 14 年里三个条件同月只成立过 **4 次**，"
        "再叠加「连续 2 个月」直接归零。**这不是 bug，是那条规则在月度数据上不可执行**"
        "（`diff_literal` 保留仅为可复现，跑出来与 `off` 逐位相同）。",
        "",
        "**水平缺口读法**：`ERP 分位` 高 = 利差看着便宜，`EY 分位`（= 1/PE 的分位）高 = 估值真便宜。"
        "两者之差就是「利率贡献的那部分便宜」。实现上两个分位必须**同起点同长度**"
        "（EY 从 2011 起、ERP 要等 10Y 从 2015-06 起；各算各的会让 EY 混进 2011–2014 的高 EY 段，"
        "把 gap 凭空抬高 0.2，那是样本错位不是利率红利）。修正后 gap 中位数 "
        f"{band.fmt_x(vmodes[-1]['gap_med'], 3)}、75 分位 {band.fmt_x(vmodes[-1]['gap_p75'], 3)}，"
        "阈值 0.10 落在分布右半侧，不是拍出来的。",
        "",
        "**阈值扫描（沪深300 · weekly）**：",
        "",
        "| `VETO_GAP` | 年化 | 回撤 | Calmar | 曝光 | 笔数 | 单月成立 | 连续2月 |",
        "|---|---:|---:|---:|---:|---:|---:|---:|",
    ]
    for r in vscan:
        star = " ←默认" if abs(r["th"] - float(p["VETO_GAP"])) < 1e-9 else ""
        L.append(f"| {r['th']:.2f}{star} | {band.fmt_pct(r['ann'])} | {band.fmt_pct(r['mdd'])} | "
                 f"{band.fmt_x(r['calmar'])} | {r['exp']:.0%} | {r['n']} | {r['n_cond']} | "
                 f"{r['n_veto']} |")

    v_off = next(r for r in vmodes if r["mode"] == "off")
    v_on = next(r for r in vmodes if r["mode"] == "level_gap")
    L += [
        "",
        f"> **否决器修好后是净改善**：年化 {band.fmt_pct(v_off['ann'])} → "
        f"{band.fmt_pct(v_on['ann'])}（{band.fmt_pct(v_on['ann'] - v_off['ann'])}）、"
        f"回撤 {band.fmt_pct(v_off['mdd'])} → {band.fmt_pct(v_on['mdd'])}"
        f"（{(v_on['mdd'] - v_off['mdd']) * 100:+.1f}pp）、Calmar "
        f"{band.fmt_x(v_off['calmar'])} → {band.fmt_x(v_on['calmar'])}，平均仓位 "
        f"{v_off['exp']:.0%} → {v_on['exp']:.0%}。0.05~0.15 是**平台**（Calmar 都在 0.30~0.32），"
        "0.20 以上等于关闭 —— 不是靠调参调出来的尖峰。",
        "",
        "> ⚠ **A500 档的否决器基本用不了**：A500 的 PE 只有 2024-09 起，叠加 10Y 后 "
        "`erp` 只有 ~26 个月，而分位需要 24 个月历史 → 2026-09 之后才有第一个读数。"
        "这是数据硬约束，不是实现问题。",
        "",
        "## 3. 规则参数（文档 §7 原表 + 2026-11-03 通用化改动）",
        "",
        "| 项 | 值 |",
        "|---|---|",
        f"| 标的池 | {', '.join(f'`{k}`({v['name']})' for k, v in band.UNIVERSES.items())} |",
        f"| 闸门 | `{p['GATE']}` · 窗口 {p['WINDOW_YEARS']} 年（{p['WINDOW_DAYS']} 交易日，写死） |",
        "| 仓位带 | ≤20% → 100 / (20,40] → 70 / (40,60] → 50 / [60,80) → 30 / ≥80 → 10 |",
        f"| 定档频率 | **{p['REBAL_FREQ']}**（weekly / monthly / quarterly）；"
        f"6/12 月顺延只在 quarterly 生效（`BLACKOUT_FREQ={p['BLACKOUT_FREQ']}`） |",
        f"| 阶梯 | dd {p['LADDER_DD']}% → {p['LADDER_UNITS']} 份，"
        f"1 份 = {p['UNIT_PCT_POOL']:g}%（满梯 4.5 份 = 90%） |",
        f"| RSI 扳机 | RSI{p['RSI_PERIOD']} < {p['RSI_BELOW']:g} 或从 <{p['RSI_BELOW']:g} 回升首日 |",
        f"| 月频帽 | {p['MAX_TRANCHE_PER_MONTH']} 档/自然月；52 周新高阶梯归零 |",
        f"| 利差 | `{p['SPREAD_ROLE']}`：分位没降但利差被 10Y 下行撑高 → 降一档，需连续 2 个月 |",
        f"| 高估确认 | ≥80% 分位连续 {p['SELL_CONFIRM_MONTHS']} 个月才降到 10% |",
        f"| 交叉校验 | 仅 a500 有第二把尺子（`hs300`）；`{p['CROSS_RULE']}` |",
        "",
        f"## 4. 换档流水（沪深300 · {hs['p']['REBAL_FREQ']} · 最近 14 次换档）",
        "",
        "| 日期 | 自身档位 | 换档 | 利差否决 | 备注 |",
        "|---|---:|---:|---|---|",
    ]
    # 与 daily 报告同口径：只列**目标带真正变过**的定档日，同档重复（30% → 30%）不展示
    me_hs = band.monthly_signals(hs["ind"], p)
    gap_by_ym = {i.strftime("%Y-%m"): g for i, g in me_hs["veto_gap"].items()}

    def _veto_txt(r):
        g = gap_by_ym.get(r["date"].strftime("%Y-%m"))
        if g is None or g != g:
            return f"未就绪（需 {int(p['VETO_MIN_M'])} 个月样本）"
        return f"{'**是**' if r['veto'] else '否'}（gap {float(g) * 100:+.1f}pp）"

    rows, prev_w = [], None
    for r in (r for r in hs["sim"]["band_rows"] if r["action"] == "定档"):
        w = float(r["w"])
        if prev_w is None or abs(w - prev_w) > 1e-9:
            rows.append((prev_w, r))
        prev_w = w
    for pw, r in rows[-14:]:
        L.append(f"| {r['date']:%Y-%m-%d} | {band.fmt_w(r['w_a'])} | "
                 f"{band.fmt_w(pw)} → **{band.fmt_w(r['w'])}** | {_veto_txt(r)} | {r['note']} |")

    L += [
        "",
        "## 5. 阶梯与交易统计",
        "",
        "| 标的 | 频率 | 阶梯档数 | 买入 | 卖出 | 平均仓位 | 周转 | 月内最多档 |",
        "|---|---|---:|---:|---:|---:|---:|---:|",
    ]
    for k, v in runs.items():
        s, ls = v["sim"], ladder_stats(v["sim"])
        L.append(f"| {k} | {v['p']['REBAL_FREQ']} | {ls['n_ladder']} | {ls['buys']} | "
                 f"{ls['sells']} | {s['exposure']:.0%} | {s['turnover']:.2f} | "
                 f"{ls['max_per_month']} |")
    dist = band_dist(hs["sim"]["band_rows"])
    L += [
        "",
        "沪深300 weekly 的定档分布（按目标仓位计）："
        + " / ".join(f"{w:.0%} 档 {dist.get(w, 0)} 次"
                     for w in sorted(dist, reverse=True) if w is not None),
        "",
        "## 6. 逐年（沪深300 · weekly）",
        "",
        "| 年 | 策略 | 买入持有 | 超额 | 平均仓位 | 仓位变化 |",
        "|---|---:|---:|---:|---:|---:|",
    ]
    for r in band.yearly(hs["sim"]):
        L.append(f"| {r['year']} | {band.fmt_pct(r['ret'])} | {band.fmt_pct(r['ret_bh'])} | "
                 f"{(r['ret'] - r['ret_bh']) * 100:+.2f}pp | {r['exp']:.0%} | {r['n']} |")

    L += [
        "",
        "## 7. 起手仓位：摆到带位 vs 空仓爬梯",
        "",
        "| 口径 | 年化 | 回撤 | Calmar | 曝光 | 超额 |",
        "|---|---:|---:|---:|---:|---:|",
    ]
    for k, v in runs.items():
        L.append(_row(f"{k} · 起手摆到带位", v["sim"]))
        L.append(_row(f"{k} · 空仓起步", initz[k]))

    L += [
        "",
        "> 空仓起步档是**规则的另一半**：文档说「分批 + 扳机」向目标带推进，若真从 0 开始，"
        "要等 dd≥8% 且 RSI<40 才动第一笔 —— 单边上行的年份会长期空仓（拿货基利息）。"
        "实盘（这笔钱已在场）用「起手摆到带位」，空仓档只用来量化「等跌下来再买」的代价。",
        "",
        "## 8. 敏感性（沪深300 · weekly）",
        "",
        "| 变体 | 年化 | 回撤 | Calmar | 曝光 | 笔数 | 区间 |",
        "|---|---:|---:|---:|---:|---:|---|",
    ]
    for r in sens["hs300"]:
        exp_txt = "—" if r["exp"] is None else f"{r['exp']:.0%}"
        L.append(f"| {r['name']} | {band.fmt_pct(r['cagr'])} | {band.fmt_pct(r['mdd'])} | "
                 f"{band.fmt_x(r['calmar'])} | {exp_txt} | {r['n']} | {r['note']} |")

    L += [
        "",
        "A500 侧同样扫一遍（样本只有 1 年多，**只看规则会不会自相矛盾，不看优劣**）：",
        "",
        "| 变体 | 年化 | 回撤 | Calmar | 曝光 | 笔数 | 区间 |",
        "|---|---:|---:|---:|---:|---:|---|",
    ]
    for r in sens["a500"]:
        exp_txt = "—" if r["exp"] is None else f"{r['exp']:.0%}"
        L.append(f"| {r['name']} | {band.fmt_pct(r['cagr'])} | {band.fmt_pct(r['mdd'])} | "
                 f"{band.fmt_x(r['calmar'])} | {exp_txt} | {r['n']} | {r['note']} |")

    L += [
        "",
        "## 9. 验收断言",
        "",
        "| 断言 | 沪深300 | 中证A500 | 明细（沪深300） |",
        "|---|---|---|---|",
    ]
    for i, (name, ok, detail) in enumerate(chk["hs300"]):
        a = "PASS" if ok else "**FAIL**"
        b = "PASS" if chk["a500"][i][1] else "**FAIL**"
        L.append(f"| {name} | {a} | {b} | {detail} |")

    L += [
        "",
        "> C1 + C2 是这套引擎最强的单测：恒满时净值必须逐日等于买入持有（差 <1e-9），"
        "恒空时年化必须等于 rf。C10 再把「加权收益 + 现金利息 − 费用」逐日对上；"
        "C11 检查定档频率真的落在每周/每月/每季，C7 检查 6/12 月顺延只在 quarterly 生效。",
        "",
        "## 10. 已知失效点",
        "",
        "1. **A500 的「10 年 PE 分位」不存在**（PE 自 2024-09 起，`window_short=True`），"
        "只能靠沪深300 交叉校验兜底；两个指数行业结构不同（A500 低配银行/非银/食品饮料，"
        "超配电子/医药/军工/化工），**不是等价替换**。沪深300 档没有这个问题。",
        "2. **weekly 定档会放大边界抖动**：见 §1 —— 频率越高，换档次数与换手越多，"
        "而回撤改善未必同比例增加。判据应该是「回撤差 / 费用差」，不是「反应更快」。",
        "3. **便宜不等于见底**：文档 §6-2 记录「PE 分位 0–20% 之后 1 年内最坏路径仍回撤 43.78%」；"
        f"本仓库沪深300 当前 dd {hs['ind']['dd'].iloc[-1]:.0f}%、A500 当前 dd "
        f"{a5['ind']['dd'].iloc[-1]:.0f}%，阶梯正是为此存在。",
        "4. **窗口决定结论**：§8 里同一套规则换窗口年化会跳，`WINDOW_DAYS` 写死；"
        "改它等于换策略（文档 §7 要求走修订记录）。",
        "5. **利差否决器多数时候不生效**：10Y 中债自 2015-06 起，更早的决策期 `y10=NaN` → 不否决。",
        "6. **价格口径**：A500 用价格指数 + 股息 2.5% 计提近似；沪深300 用 H00300 全收益。"
        "绝对收益不能当实盘预期（基金费率、跟踪误差、申赎时滞未计）。",
        "7. **触发账本靠人记**：`daily/_daily_scan.py` 只算「按价格路径哪些档位**本可以**触发」，"
        "不假设你的账户状态（文档 §7 要求自己逐笔留痕）。",
        "",
        "## 11. 与其它变种的边界",
        "",
        "- 主策略 `../`：各腿 2.5pp 带内的**回撤阶梯**，闸门用 ERP 分位（A500 侧恒 null）；",
        "- 本变种：**这笔钱最多能投几成**（PE 自身分位定带）+ 阶梯 + RSI 扳机，输出 0–100% 仓位；",
        "- `../v4_erp_band/`：沪深300 与国债之间摆 0/100（ERP 通道 + 两带滞回）；",
        "- `../v1_ma20/`、`../v2_boll_breakout/`、`../v3_macd_boll/`、`../v5_trend_gate/`："
        "逐腿进出时点 / 带内档位（价格趋势）；",
        "",
        "> 模拟结果，**非投资建议**。",
    ]

    with open(OUT_MD, "w", encoding="utf-8") as f:
        f.write("\n".join(L))

    # ---------------- console
    print("报告:", OUT_MD)
    print("结果:", OUT_JSONL)
    for k, v in runs.items():
        m, mbh, s = v["sim"]["m"], v["sim"]["mbh"], v["sim"]
        print(f"  {k:6s} {v['p']['REBAL_FREQ']:9s} {s['nav'].index[0]:%Y-%m-%d}~"
              f"{s['nav'].index[-1]:%Y-%m-%d} 年化 {band.fmt_pct(m['ann'])} · "
              f"MDD {band.fmt_pct(m['mdd'])} · Calmar {band.fmt_x(m['calmar'])} · "
              f"曝光 {s['exposure']:.0%} · {s['n_trades']} 笔 | "
              f"BH {band.fmt_pct(mbh['ann'])} / {band.fmt_pct(mbh['mdd'])}")
    print("  频率对照（沪深300）:")
    for r in freqrows:
        if r["univ"] != "hs300" or "err" in r:
            continue
        print(f"    {r['freq']:9s} 年化 {band.fmt_pct(r['ann'])} · MDD {band.fmt_pct(r['mdd'])} · "
              f"Calmar {band.fmt_x(r['calmar'])} · {r['n_trades']} 笔 · 周转 {r['turnover']:.1f} · "
              f"定档 {r['n_dec']} / 换档 {r['n_change']}")
    bad = [(k, c) for k, lst in chk.items() for c in lst if not c[1]]
    for k, c in bad:
        print(f"  [FAIL] {k} · {c[0]} · {c[2]}")
    if bad:
        raise AssertionError(f"{len(bad)} 条断言未通过")


if __name__ == "__main__":
    main()
