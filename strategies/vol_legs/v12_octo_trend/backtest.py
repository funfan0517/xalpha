# -*- coding: utf-8 -*-
"""V12 · 八态趋势 · 七腿回测。

产出（本目录 `backtest/`）：
- `_v12_octo_trend_bt.md`   逐腿 + 组合 + 八态后验 + 当前状态
- `_v12_octo_trend_bt.jsonl` 逐腿一行，便于 `strategies/_merge_verify.py` 比对

回测做三件事，缺一不可：
1. **逐腿净值**：八态 → 许可上限 → 仓位，与同窗口恒持对照；
2. **口径验伪**（规格 §3 的复现）：把八态的后验 fwd20 / 胜率 / 四组配对分离度在本仓库数据上
   重算。数字不必逐位等于规格（标的是 ETF 前复权、样本更短），但**符号与排序必须一致** ——
   这是我们判断"移植没写错"的唯一硬证据；
3. **当前状态**：最新一根 bar 的态、regime、z20、rvol、许可，直接对接每日操作。

用法：`python strategies/vol_legs/v12_octo_trend/backtest.py`
"""
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

_HERE = os.path.dirname(os.path.abspath(__file__))
if _HERE not in sys.path:
    sys.path.insert(0, _HERE)

import rule          # noqa: E402
import data as vdata  # noqa: E402
import engine        # noqa: E402

OUT_DIR = os.path.join(_HERE, "backtest")
OUT_MD = os.path.join(OUT_DIR, "_v12_octo_trend_bt.md")
OUT_JSONL = os.path.join(OUT_DIR, "_v12_octo_trend_bt.jsonl")


def pct(x, nd=2):
    if x is None or (isinstance(x, float) and not np.isfinite(x)):
        return "—"
    return f"{x * 100:+.{nd}f}%"


def bh(leg, start):
    """同窗口恒持（买入持有）。"""
    px = vdata.frame(leg)["close"].loc[start:]
    return engine.perf(px / px.iloc[0])


def combo(navs, equal_vol=False):
    """组合：等权 / 等波动（滚动 60 日波动倒数，`shift(1)` 无前视）。"""
    rets = {k: v.pct_change() for k, v in navs.items() if v is not None}
    if not rets:
        return None
    R = pd.DataFrame(rets)
    if equal_vol:
        V = R.rolling(60, min_periods=20).std()
        inv = 1.0 / V.replace(0.0, np.nan)
        wgt = inv.div(inv.sum(axis=1), axis=0).shift(1)
        port = (R * wgt).sum(axis=1, min_count=1).fillna(0.0)
    else:
        port = R.mean(axis=1, skipna=True).fillna(0.0)
    return (1.0 + port).cumprod()


def main():
    os.makedirs(OUT_DIR, exist_ok=True)
    legs = vdata.available()
    dfs = {leg: vdata.frame(leg) for leg in legs}

    results, rows, cur, specs = {}, [], [], {}
    for leg in legs:
        r = engine.backtest_leg(leg, df=dfs[leg])
        rs = engine.backtest_leg(leg, df=dfs[leg], opt=False)   # 规格口径（对照）
        results[leg] = r
        specs[leg] = rs
        if r["perf"] is None:
            rows.append(dict(leg=leg, name=r["name"], bars=len(dfs[leg]),
                             start=None, end=str(dfs[leg].index[-1].date()),
                             ann=None, mdd=None, sharpe=None, expo=None, acts=0,
                             bh_ann=None, note=f"预热不足（需 {rule.WARMUP} 根）"))
            continue
        b = bh(leg, r["start"])
        rows.append(dict(leg=leg, name=r["name"], bars=r["bars"],
                         start=str(r["start"].date()), end=str(r["end"].date()),
                         ann=r["perf"]["ann"], mdd=r["perf"]["mdd"],
                         sharpe=r["perf"]["sharpe"], expo=r["expo"], acts=len(r["acts"]),
                         bh_ann=b["ann"], bh_mdd=b["mdd"], cap=r["cap_txt"],
                         ann_spec=rs["perf"]["ann"], mdd_spec=rs["perf"]["mdd"],
                         sharpe_spec=rs["perf"]["sharpe"], note=""))
        last = r["cx"].iloc[-1]
        cur.append(dict(leg=leg, name=r["name"], state=last["state"],
                        regime=last["regime"], z20=float(last["z20"]),
                        rvol=float(last["rvol"]), w=float(r["w"].iloc[-1]),
                        expo=r["expo"], end=str(r["end"].date())))

    L = [
        "# V12 · 八态趋势（急涨/慢涨/弱回调/强回调 ｜ 急跌/慢跌/弱反弹/强反弹）",
        "",
        "> 规则：`G:\\tradingagents\\hs300_trend\\八态趋势参数规格.md` v1.1；参考实现 "
        "`hs300_trend/state8_final.py::classify`。文字 → 参数的映射见 `rule.py`，"
        "六步判定链见 `engine.py::classify`。",
        f"> 口径：**场内 ETF 前复权日线**（`data/_ohlcv.json`）—— 八态里的回调/反弹强度"
        f"**必须用量能**，场外净值没有量，这是家族里唯一硬依赖成交量的变种。"
        f"T 日收盘判态、T+1 起承担仓位 · 空仓计货基 {rule.CASH_ANNUAL:.2%} · "
        f"摩擦单边 {rule.FEE_SIDE * 1e4:.1f}bp · 预热 {rule.WARMUP} 根。",
        "> 许可映射（**规格原表，按 §3/§4 的后验统计定，不按回测调**）："
        + " · ".join(f"{k} {v:.0%}" for k, v in rule.CAP.items())
        + f" · 升格确认 {rule.UP_CONFIRM} 日（降格立即）。"
        + (f"\n> **生效口径 = v12.1**（`rule.OPT={rule.OPT}`）："
           + "；".join(f"{rule.NAMES[l]} {rule.cap_version(l)}"
                      for l in rule.LEGS if rule.cap_version(l) != "规格 CAP")
           + "。§1b 给两版对照，`rule.OPT=False` 可一键回到规格口径。"
           if rule.OPT else "\n> **生效口径 = 规格 CAP**（`rule.OPT=False`）。"),
        "",
        "## 1. 逐腿（按当前生效口径）",
        "",
        "| 腿 | 样本 | 年化 | 最大回撤 | 夏普 | 平均许可 | 调仓 | 恒持年化 | 恒持回撤 | 差 |",
        "|---|---|---:|---:|---:|---:|---:|---:|---:|---:|",
    ]
    for r in rows:
        if r["ann"] is None:
            L.append(f"| {r['name']} | {r['bars']} 根 | — | — | — | — | — | — | — | {r['note']} |")
            continue
        edge = (r["ann"] - r["bh_ann"]) * 100
        L.append(f"| {r['name']} | {r['start']} ~ {r['end']}（{r['bars']} 根） | "
                 f"{pct(r['ann'])} | {pct(r['mdd'])} | {r['sharpe']:.2f} | {r['expo']:.0%} | "
                 f"{r['acts']} | {pct(r['bh_ann'])} | {pct(r['bh_mdd'])} | {edge:+.2f}pp |")

    # ---------------- v12.1 vs 规格口径
    if rule.OPT:
        L += ["", "## 1b. v12.1 vs 规格口径（同一窗口/同一成本，只有许可与波动覆盖不同）", "",
              "来源：`research/opt_ashare/_opt_ashare.md`（次日/5 日期望重标）→ "
              "`strategies/vol_legs/research/v12v13_compare/_v12v13_compare.md`（20.7 年指数样本裁决）。",
              "",
              "| 腿 | 规格年化 | 规格回撤 | 规格夏普 | v12.1年化 | v12.1回撤 | v12.1夏普 | Δ夏普 | 生效许可 |",
              "|---|---:|---:|---:|---:|---:|---:|---:|---|"]
        for r in rows:
            if r["ann"] is None:
                continue
            L.append(f"| {r['name']} | {pct(r['ann_spec'])} | {pct(r['mdd_spec'])} | "
                     f"{r['sharpe_spec']:.2f} | {pct(r['ann'])} | {pct(r['mdd'])} | "
                     f"{r['sharpe']:.2f} | {r['sharpe'] - r['sharpe_spec']:+.2f} | {r.get('cap', '—')} |")

    # ---------------- 组合层
    L += ["", "## 2. 组合层（A500 样本不足 1 年，不进组合；两种配权）", ""]
    navs = {l: results[l]["nav"] for l in legs if results[l]["nav"] is not None and l != "a500"}
    bhs = {}
    for l in navs:
        px = dfs[l]["close"].loc[results[l]["start"]:]
        bhs[l] = px / px.iloc[0]
    for ev, lbl in ((False, "等权"), (True, "等波动")):
        n1 = combo(navs, ev)
        n2 = combo(bhs, ev)
        if n1 is None:
            continue
        p1, p2 = engine.perf(n1), engine.perf(n2)
        L.append(f"- **八态 · {lbl}**：累计 {pct(p1['cum'], 1)} · 年化 {pct(p1['ann'])} · "
                 f"最大回撤 {pct(p1['mdd'])} · 夏普 {p1['sharpe']:.2f} · 样本 {p1['days']} 根")
        L.append(f"  - 恒持对照：累计 {pct(p2['cum'], 1)} · 年化 {pct(p2['ann'])} · "
                 f"最大回撤 {pct(p2['mdd'])} · 夏普 {p2['sharpe']:.2f}")

    # ---------------- 八态后验（口径验伪）
    core = rule.CORE_LEGS[0] if rule.CORE_LEGS else "hs300"
    rc = results.get(core)
    L += ["", f"## 3. 八态后验（本仓库口径 · {rule.NAMES[core]} 全样本）", "",
          "规格 §3 的两张表在这里重算一遍。**用途是验口径而不是炫技**：数字不必逐位等于规格"
          "（标的是 510300 前复权而非沪深300 指数，样本 2013 起而非 2010/2006 起），"
          "但**相对排序与符号必须一致** —— 不一致就是移植写错了。"
          "量能用的是 `SH000300` 指数成交量（用 ETF 自身量会让「弱回调 − 强回调」反号，"
          "见 `research/probe_volume/_probe_volume.md`）。",
          ""]
    if rc is not None and rc["nav"] is not None:
        ss = engine.state_stats(rc["df"]["close"].loc[rc["start"]:],
                               rc["cx"].loc[rc["start"]:])
        L += [f"有效 {ss['days']} 天 · 基准 fwd20 {pct(ss['base'])}", "",
              "| 状态 | 占比 | fwd20 | 胜率 | 许可·规格 | 许可·v12.1 |",
              "|---|---:|---:|---:|---:|---:|"]
        for x in ss["rows"]:
            L.append(f"| {x['state']} | {x['share']:.1%} | {pct(x['fwd'])} | "
                     f"{x['win']:.1%} | {rule.cap_of(x['state']):.0%} | "
                     f"{rule.cap_of(x['state'], leg=core):.0%} |")
        L += ["", "**四组配对分离度**（fwd20 差 / 胜率差）：", "",
              "| 配对 | fwd20 差 | 胜率差 | 样本 |", "|---|---:|---:|---|"]
        for p in ss["pairs"]:
            L.append(f"| {p['a']} − {p['b']} | {p['dfwd'] * 100:+.2f}pp | "
                     f"{p['dwin'] * 100:+.1f}pp | {p['na']}/{p['nb']} |")
        sign = all(p["dfwd"] > 0 for p in ss["pairs"]) if ss["pairs"] else None
        L += ["", "> 规格四组配对（急涨−慢涨、弱回调−强回调、强反弹−弱反弹、急跌−慢跌）"
                  "在两个样本里**全部为正**。本表"
                  + ("**四组全部为正 → 移植成立**。" if sign else
                     "**有配对为负 → 与规格不一致，先查数据口径与预热量，别急着解释成「市场变了」**。")]

    L += ["", "**其余腿的 fwd20（诊断，不作执行依据）**：", "",
          "| 腿 | " + " | ".join(rule.STATE_ORDER) + " |",
          "|---|" + "---:|" * len(rule.STATE_ORDER)]
    for leg in legs:
        r = results[leg]
        if r["nav"] is None:
            continue
        ss = engine.state_stats(r["df"]["close"].loc[r["start"]:], r["cx"].loc[r["start"]:])
        by = {x["state"]: x["fwd"] for x in ss["rows"]}
        L.append(f"| {r['name']} | " + " | ".join(pct(by.get(s)) for s in rule.STATE_ORDER) + " |")

    L += ["", "## 4. 当前状态（最新一根 bar）", "",
          "| 腿 | 状态 | regime | z20 | rvol | 许可 | 规格口径许可 | 样本内平均许可 | 截至 |",
          "|---|---|---|---:|---:|---:|---:|---:|---|"]
    for c in cur:
        w_sp = float(specs[c["leg"]]["w"].iloc[-1])
        L.append(f"| {c['name']} | {c['state']} | {c['regime']} | {c['z20']:+.2f} | "
                 f"{c['rvol']:.2f} | {c['w']:.0%} | {w_sp:.0%} | {c['expo']:.0%} | {c['end']} |")

    L += [
        "",
        "## 5. 读法（七条，先看这里再看数字）",
        "",
        f"1. **执行范围只有 {rule.NAMES[core]}**：规格 §7.4 自己写了「z_th / rvol 阈值建议在"
        "标普/纳指上重标」。没重标之前把同一条阈值套到美股/黄金上属于跨样本搬运，"
        "所以其余 6 腿只做诊断（`rule.CORE_LEGS`）。",
        "2. **八态是「状态判定」，不是择时信号**：它回答「现在是哪种趋势」，"
        "仓位映射是本仓库按规格后验统计加的一层。所以评价它的第一标准是"
        "**§3 的分离度是否还成立**，第二标准才是年化 —— 一个把 8 个态压成 2 个态的映射"
        "（比如只留 急涨满仓/其余半仓）几乎不会差多少，因为真正的 alpha 在「急 vs 慢」这一刀。",
        "3. **年化跑输恒持是预期内的**：平均许可只有 40%~50%，空仓段拿的是货基 1.40%。"
        "这条规则的卖点是**回撤与「慢跌不参与」**，不是年化。规格 §4② 的「急跌会反抽、"
        "慢跌继续跌」是本策略敢在急跌留半仓、慢跌压到 2 成的唯一依据。",
        "4. **口径不可与场外净值策略混算**：ETF 价格口径无分红再投、含折溢价，"
        "美股两腿 513100 / 513500 是人民币代理，信号里掺汇率；v1/v2/v3/v5 的场外复权净值"
        "比这张表系统性高 0.4~1.1pp/年（README §2.2）。",
        "5. **态层的抖动靠 `UP_CONFIRM` 兜，不靠参数微调**：急涨/慢涨的分界就是 z20 穿 1.2，"
        "这是规格的硬边界（1.0→1.2 分离度翻倍所以不能放宽）。要降换手只能加确认日，"
        "`research/_sweep.py` 扫 1/2/3/5 的代价写在那边。",
        "6. **§3 里有一格与规格不一致，而且故意没改**：本样本 `弱反弹` 的 fwd20 是正的"
        "（规格 −0.28%），映射仍按规格压到 20%。理由是不拿 13 年 ETF 数据反推参数 —— "
        "真按本样本改就该给它 50%，那等于用后验统计调参，换台机器/换个窗口就翻回去。"
        "代价是这条腿少赚一点，收益是参数仍然只有**一个出处**（规格）。",
        "7. **v12.1 只改了两个旋钮，且每个都有样本外依据**：`强回调` 50%→20%（两腿都亏）、"
        "科创50 额外 `强反弹` 100% + `急跌` 20% + `σ` 覆盖 25%。沪深300 **不加** `强反弹` —— "
        "换到 2006-2026 的指数样本（含 2008）后它会带来 −41.1% 的回撤（`research/"
        "v12v13_compare` §F）。`rule.OPT=False` 一键回退规格口径。",
        "",
        "> 机械规则输出，非投资建议。",
    ]

    with io.open(OUT_MD, "w", encoding="utf-8", newline="\n") as f:
        f.write("\n".join(L))
    with io.open(OUT_JSONL, "w", encoding="utf-8", newline="\n") as f:
        for r in rows:
            f.write(json.dumps(r, ensure_ascii=False) + "\n")
    print("报告:", OUT_MD)
    print("明细:", OUT_JSONL)
    for r in rows:
        if r["ann"] is None:
            print(f"  {r['name']:8s} 跳过：{r['note']}")
            continue
        print(f"  {r['name']:8s} 年化 {pct(r['ann']):>8s} · 回撤 {pct(r['mdd']):>8s} · "
              f"夏普 {r['sharpe']:4.2f} · 许可 {r['expo']:.0%} · 调仓 {r['acts']:3d} · "
              f"恒持 {pct(r['bh_ann'])}")


if __name__ == "__main__":
    main()
