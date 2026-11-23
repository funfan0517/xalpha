# -*- coding: utf-8 -*-
"""V17 · 八态趋势 · 家族 54 只场内 ETF 回测。

从 `vol_legs/v12_octo_trend/backtest.py` 复制（2026-11-21，用户要求把 v12 搬进本家族）：
**分析与判定口径不变**，改的只有两处 ——
- 标的：七腿 → `../_pool.py` 的 54 只场内 ETF（`data/_universe.md` 前四类）；
- 数据：真实指数收盘 + 同源指数量能 → 场内 ETF 前复权收盘 + **ETF 自身成交量**
  （`data/_etf_cv_klines.json`；家族没有 54 只标的的指数量能库）。

产出（本目录 `backtest/`）：
- `_v17_octo_bt.md`     逐标的 + 组合 + 八态后验 + 当前状态
- `_v17_octo_bt.jsonl`  逐标的一行，便于 `strategies/_merge_verify.py` 比对

回测做三件事，缺一不可：
1. **逐标的净值**：八态 → 许可上限 → 仓位，与同窗口恒持对照；
2. **口径验伪**（规格 §3 的复现）：把八态的后验 fwd20 / 胜率 / 四组配对分离度在本库数据上重算。
   本变种的量能是 ETF 自身量，**宽基上「弱回调 − 强回调」反号是预期内的口径折扣**（见 `rule.py` ⚠）；
3. **当前状态**：最新一根 bar 的态、regime、z20、rvol、许可，直接对接每日操作。

用法：`python strategies/tech_indicators/v17_octo_trend/backtest.py`
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

_PX_DESC = "**场内 ETF 前复权收盘** + **ETF 自身成交量**（无指数量能；宽基/QDII 的量含做市噪声）"
_SRC = "`vol_legs/v12_octo_trend/backtest.py`（2026-11-21 复制并扩池）"

OUT_DIR = os.path.join(_HERE, "backtest")
OUT_MD = os.path.join(OUT_DIR, "_v17_octo_bt.md")
OUT_JSONL = os.path.join(OUT_DIR, "_v17_octo_bt.jsonl")

# v12 的研究产物不在本目录（它们是指数口径）；引用时写全路径。
_V12RES = "strategies/vol_legs/v12_octo_trend/research"


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
        scope = ("执行" if leg in rule.CORE_LEGS else
                 "观察" if leg in rule.WATCH_LEGS else "诊断")
        if r["perf"] is None:
            rows.append(dict(leg=leg, name=r["name"], bars=len(dfs[leg]), scope=scope,
                             start=None, end=str(dfs[leg].index[-1].date()),
                             ann=None, mdd=None, sharpe=None, expo=None, acts=0,
                             bh_ann=None, note=f"预热不足（需 {rule.WARMUP} 根）"))
            continue
        b = bh(leg, r["start"])
        rows.append(dict(leg=leg, name=r["name"], bars=r["bars"], scope=scope,
                         start=str(r["start"].date()), end=str(r["end"].date()),
                         ann=r["perf"]["ann"], mdd=r["perf"]["mdd"],
                         sharpe=r["perf"]["sharpe"], expo=r["expo"], acts=len(r["acts"]),
                         bh_ann=b["ann"], bh_mdd=b["mdd"], bh_sharpe=b["sharpe"],
                         cap=r["cap_txt"],
                         ann_spec=rs["perf"]["ann"], mdd_spec=rs["perf"]["mdd"],
                         sharpe_spec=rs["perf"]["sharpe"], note=""))
        last = r["cx"].iloc[-1]
        cur.append(dict(leg=leg, name=r["name"], state=last["state"],
                        regime=last["regime"], z20=float(last["z20"]),
                        rvol=float(last["rvol"]), w=float(r["w"].iloc[-1]),
                        expo=r["expo"], end=str(r["end"].date())))

    n_opt = sum(1 for l in rule.LEGS if rule.vol_layer_of(l))
    L = [
        "# V17 · 八态趋势（急涨/慢涨/弱回调/强回调 ｜ 急跌/慢跌/弱反弹/强反弹）",
        "",
        "> 规则：`G:\\tradingagents\\hs300_trend\\八态趋势参数规格.md` v1.1；参考实现 "
        "`hs300_trend/state8_final.py::classify`。文字 → 参数的映射见 `rule.py`，"
        "六步判定链见 `engine.py::classify`；本文件复制自 " + _SRC + "，**判定一行未改**。",
        f"> 口径：{_PX_DESC} —— 八态里的回调/反弹强度**必须用量能**，"
        "家族 `data/_long_klines.json` 只有收盘价，故本变种自带 "
        "`data/_etf_cv_klines.json`（收盘 + 成交量）。"
        f"T 日收盘判态、T+1 起承担仓位 · 空仓计货基 {rule.CASH_ANNUAL:.2%} · "
        f"摩擦单边 {rule.FEE_SIDE * 1e4:.1f}bp · 预热 {rule.WARMUP} 根。",
        "> 许可映射（**生效基线 = 规格原表 ⊕ `慢跌` 20%→0**，2026-11-19）："
        + " · ".join(f"{k} {v:.0%}" for k, v in rule.CAP.items())
        + f" · 升格确认 {rule.UP_CONFIRM} 日（降格立即）。"
        "规格原值见 §3 的「许可·规格」列（`rule.CAP_SPEC`），除 `慢跌` 外两表一致。",
        ("> **生效口径 = v12.1 的按标的层**（`rule.OPT=True`）："
         + "；".join(f"{rule.NAMES[l]} {rule.cap_version(l)}"
                    for l in rule.LEGS if rule.cap_version(l).startswith("v12.1"))
         + "。⚠ 这些配置**只在原来那七只 ETF 上有证据，且证据来自 v12 的指数口径**；"
         "本变种是 ETF 量能口径、参数未重标 → 只能当「结构保留」。"
         "其余标的走基线 `CAP`、不加波动层。")
        if rule.OPT else "> **生效口径 = 规格 CAP**（`rule.OPT=False`）。",
        (f"> **波动层（按标的，`rule.VOL_LAYER`，{n_opt}/{len(rule.LEGS)} 只有配置）**："
         + " · ".join(f"{rule.NAMES[l]} {rule.vol_layer_txt(l)}"
                      for l in rule.LEGS if rule.vol_layer_of(l))
         + "。形态恒为 `min(八态许可, 波动层系数)`，**只压上限**；死区只作用于系数本身。"
         f"依据 {_V12RES}/er_vol_layer/_er_vol_layer.md（指数口径）。")
        if rule.OPT else "> 波动层：未启用（`rule.OPT=False`）。",
        "",
        f"## 1. 逐标的（按当前生效口径 · {len(rows)} 只）",
        "",
        "> 执行范围 = **全池**（`rule.CORE_LEGS`，54 只场内 ETF）。"
        "「Δ夏普 / Δ年化」= 与同窗口恒持的差 —— **Δ夏普为正**才说明择时改善风险调整后收益。",
        "",
        "| 标的 | 范围 | 样本 | 年化 | 最大回撤 | 夏普 | Δ夏普 | 平均许可 | 调仓 "
        "| 恒持年化 | 恒持回撤 | Δ年化 |",
        "|---|---|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|",
    ]
    for r in rows:
        if r["ann"] is None:
            L.append(f"| {r['name']} | {r['scope']} | {r['bars']} 根 | — | — | — | — | — | — "
                     "| — | — | " + f"{r['note']} |")
            continue
        edge = (r["ann"] - r["bh_ann"]) * 100
        dsh = r["sharpe"] - r["bh_sharpe"]
        L.append(f"| {r['name']} | {r['scope']} | {r['start']} ~ {r['end']}（{r['bars']} 根） | "
                 f"{pct(r['ann'])} | {pct(r['mdd'])} | {r['sharpe']:.2f} | {dsh:+.2f} | "
                 f"{r['expo']:.0%} | {r['acts']} | {pct(r['bh_ann'])} | {pct(r['bh_mdd'])} | "
                 f"{edge:+.2f}pp |")

    # ---------------- v12.1 vs 基线口径
    if rule.OPT:
        L += ["", "## 1b. 按标的优化（v12.1 结构）vs 基线口径（同一窗口/同一成本）", "",
              "「基线」= `rule.cap_map` 的 `OPT=False` 版（`CAP`，含 `慢跌` 0%，**无波动层**）。"
              f"依据 {_V12RES}/opt_ashare/_opt_ashare.md 与 {_V12RES}/er_vol_layer/"
              "（指数口径，本变种未重标）。",
              "",
              "| 标的 | 基线年化 | 基线回撤 | 基线夏普 | 现行年化 | 现行回撤 | 现行夏普 | Δ夏普 | 波动层 | 生效许可 |",
              "|---|---:|---:|---:|---:|---:|---:|---:|---|---|"]
        for r in rows:
            if r["ann"] is None:
                continue
            L.append(f"| {r['name']} | {pct(r['ann_spec'])} | {pct(r['mdd_spec'])} | "
                     f"{r['sharpe_spec']:.2f} | {pct(r['ann'])} | {pct(r['mdd'])} | "
                     f"{r['sharpe']:.2f} | {r['sharpe'] - r['sharpe_spec']:+.2f} | "
                     f"{rule.vol_layer_txt(r['leg'])} | {r.get('cap', '—')} |")

    # ---------------- 全池增量排序
    ok = [r for r in rows if r["ann"] is not None]
    if len(ok) > 1:
        rank = sorted(ok, key=lambda x: x["sharpe"] - x["bh_sharpe"], reverse=True)
        L += ["", f"## 1c. 全池增量排序（八态层到底改善了谁 · {len(ok)} 只）", "",
              "按 **Δ夏普（vs 同窗口恒持）** 排序 —— 这才是「八态这一层值不值」的尺子；"
              "只看年化会被「平均许可只有几成、空仓拿货基」带偏。"
              "⚠ 本变种的量能是 ETF 自身量，**这张表只能当「在这套量能口径下谁受益」**，"
              "不能与 v12 的指数口径名次直接比。"
              "**样本 < 500 根的标的（近年新上市）不进「最强/最弱」结论**：几百根样本算出的夏普"
              "在排序里会和「真的有效」长得一模一样。", "",
              "| 名次 | 标的 | 样本 | Δ夏普 | Δ年化 | 最大回撤（八态 vs 恒持） | 平均许可 | 结论 |",
              "|---:|---|---:|---:|---:|---:|---:|---|"]
        for i, r in enumerate(rank, 1):
            dsh, dan = r["sharpe"] - r["bh_sharpe"], (r["ann"] - r["bh_ann"]) * 100
            if r["bars"] < 500:
                verdict = "样本短（<2年，勿据此配权）"
            elif dsh >= 0.10:
                verdict = "净增益（可以执行）"
            elif dsh >= 0:
                verdict = "微正（≈噪声，权重给小）"
            else:
                verdict = "负贡献（只该做参照）"
            L.append(f"| {i} | {r['name']} | {r['bars']} 根 | {dsh:+.2f} | {dan:+.2f}pp | "
                     f"{pct(r['mdd'])} vs {pct(r['bh_mdd'])} | {r['expo']:.0%} | {verdict} |")
        robust = [r for r in rank if r["bars"] >= 500] or rank
        best, worst = robust[0], robust[-1]
        L += ["",
              f"> 长效样本（≥500 根）里最强 **{best['name']}**"
              f"（Δ夏普 {best['sharpe'] - best['bh_sharpe']:+.2f}）· "
              f"最弱 **{worst['name']}**（{worst['sharpe'] - worst['bh_sharpe']:+.2f}）。"
              "**执行范围扩到全池不等于全池等权**：这张表是额度分配的依据，"
              "排末位的标的可以留小仓位（或不参与配权），但信号照算 —— 删了就没法复查「为什么不用它」。",
              "> 与家族里其它变种的**横向名次**见 "
              "`strategies/vol_legs/research/best_by_leg/_best_by_leg.md` —— 但 v12 那边是七腿、"
              "指数口径，**本变种尚未参与那张排名**。"]

    # ---------------- 组合层
    L += ["", f"## 2. 组合层（全池 {len(legs)} 只等权 / 等波动，两种配权）", "",
          "> 池里宽基高度重叠（A500/沪深300/上证50/中证500/800/1000/2000 成分互相包含），"
          "等权等于给 A股宽基加了很大权重；想看真实分散度，先按 §1c 的 Δ夏普调权重。", ""]
    navs = {l: results[l]["nav"] for l in legs if results[l]["nav"] is not None}
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
    core = rule.CALIB_LEG          # 规格的标定腿（510300）
    rc = results.get(core)
    L += ["", f"## 3. 八态后验（本库口径 · {rule.NAMES.get(core, core)} 全样本）", "",
          "规格 §3 的两张表在这里重算一遍。**用途是验口径而不是炫技**：数字不必逐位等于规格"
          + "（标的是 `510300` 场内 ETF 前复权、样本 2013 起；规格标定用沪深300 指数 2010-2026 / 2006-2026）。",
          "⚠ **本变种的量能是 ETF 自身成交量**，不是 v12 用的 `SH000300` 指数量能。v12 实测："
          "用 ETF 自身量时「弱回调 − 强回调」分离度是 **−0.77pp（反号）**，换指数量能才 +1.20pp。"
          "所以下表**该组为负不是移植错误，是量能口径的折扣**；其余三组仍应与规格同号。", ""]
    if rc is not None and rc["nav"] is not None:
        ss = engine.state_stats(rc["df"]["close"].loc[rc["start"]:],
                                rc["cx"].loc[rc["start"]:])
        L += [f"有效 {ss['days']} 天 · 基准 fwd20 {pct(ss['base'])}", "",
              "| 状态 | 占比 | fwd20 | 胜率 | 许可·规格 | 许可·现行 |",
              "|---|---:|---:|---:|---:|---:|"]
        for x in ss["rows"]:
            L.append(f"| {x['state']} | {x['share']:.1%} | {pct(x['fwd'])} | "
                     f"{x['win']:.1%} | {rule.CAP_SPEC.get(x['state'], 0.0):.0%} | "
                     f"{rule.cap_of(x['state'], leg=core):.0%} |")
        L += ["", "**四组配对分离度**（fwd20 差 / 胜率差）：", "",
              "| 配对 | fwd20 差 | 胜率差 | 样本 |", "|---|---:|---:|---|"]
        for p in ss["pairs"]:
            L.append(f"| {p['a']} − {p['b']} | {p['dfwd'] * 100:+.2f}pp | "
                     f"{p['dwin'] * 100:+.1f}pp | {p['na']}/{p['nb']} |")
        neg = [f"{p['a']}−{p['b']}" for p in ss["pairs"] if p["dfwd"] <= 0]
        L += ["", ("> **有配对为负：" + "、".join(neg) + "**。"
                   if neg else "> 四组全部为正。")
              + "若只有「弱回调 − 强回调」为负，就是上面说的量能口径折扣（宽基 ETF 的做市噪声）；"
              "若「急涨 − 慢涨」或「急跌 − 慢跌」也反号，才需要查移植与预热量。", ]

    L += ["", "**其余标的的 fwd20（同一套规则铺到全池，作对照；口径验伪仍只看标定腿）**：", "",
          "| 标的 | " + " | ".join(rule.STATE_ORDER) + " |",
          "|---|" + "---:|" * len(rule.STATE_ORDER)]
    for leg in legs:
        r = results[leg]
        if r["nav"] is None:
            continue
        ss = engine.state_stats(r["df"]["close"].loc[r["start"]:], r["cx"].loc[r["start"]:])
        by = {x["state"]: x["fwd"] for x in ss["rows"]}
        L.append(f"| {r['name']} | " + " | ".join(pct(by.get(s)) for s in rule.STATE_ORDER) + " |")

    L += ["", "## 4. 当前状态（最新一根 bar）", "",
          "> 「许可」= 该态的上限；「仓位」= 许可 × 波动层，且**升格要等 2 日确认** —— "
          "所以「许可 50% / 仓位 0%」表示今天刚升格、明天才生效，不是数据错。"
          "「许可·原表」= 规格 §3 的原值（`CAP_SPEC`，`慢跌` 仍是 20%）；"
          "「仓位·规格」= 把按标的层关掉（`OPT=False`）后的同一读取。", "",
          "| 标的 | 状态 | regime | z20 | rvol | 许可 | 许可·原表 | 仓位 | 仓位·规格 | 样本内平均仓位 | 截至 |",
          "|---|---|---|---:|---:|---:|---:|---:|---:|---:|---|"]
    for c in cur:
        w_sp = float(specs[c["leg"]]["w"].iloc[-1])
        L.append(f"| {c['name']} | {c['state']} | {c['regime']} | {c['z20']:+.2f} | "
                 f"{c['rvol']:.2f} | {rule.cap_of(c['state'], leg=c['leg']):.0%} | "
                 f"{rule.cap_of(c['state']):.0%} | {c['w']:.0%} | {w_sp:.0%} | "
                 f"{c['expo']:.0%} | {c['end']} |")

    L += [
        "",
        "## 5. 读法（先看这里再看数字）",
        "",
        "1. **这是 v12 八态趋势的扩池版**：参数、映射、时序、成本全部照搬 "
        f"`strategies/vol_legs/v12_octo_trend`；唯一的结构差异是标的池（七腿 → 54 只）"
        "与量能口径（指数量能 → ETF 自身量）。**所以本报告的数字不可与 v12 的 7 腿结果逐位比较。**",
        f"2. **后验表（§3）只画标定腿 {rule.NAMES.get(core, core)}**（`rule.CALIB_LEG` = 规格的标定样本）；"
        "其余标的的 fwd20 在 §3 末尾做诊断。",
        "3. **八态是「状态判定」，不是择时信号**：它回答「现在是哪种趋势」，"
        "仓位映射是按规格后验统计加的一层。评价它的第一标准是**§3 的分离度是否还成立**，"
        "第二标准才是年化 —— 而本变种的分离度受 ETF 量能口径限制，宽基那一组大概率不成立，"
        "**这是这条扩池路线本身的代价，不是参数没调好**。",
        f"4. **年化跑输恒持是预期内的**：样本内平均许可只有几成，空仓段拿的是货基 "
        f"{rule.CASH_ANNUAL:.2%}。这条规则的卖点是**回撤与「慢跌不参与」**，不是年化。",
        "5. **口径不可与家族其它变种混算**：价格是 ETF 前复权收盘（不含分红再投之外的场外净值口径）、"
        "量能是 ETF 自身量、成本是 v12 的单边 5bp + 货基 1.40%（家族五基线取 0）。",
        "6. **态层的抖动靠 `UP_CONFIRM` 兜，不靠参数微调**：急涨/慢涨的分界就是 z20 穿 1.2，"
        "这是规格的硬边界（1.0→1.2 分离度翻倍所以不能放宽）。要降换手只能加确认日。",
        "7. **§3 里有一格与规格不一致，而且故意没改**：`弱反弹` 的映射仍按规格压到 20%，"
        "不拿本样本的后验数据反推参数。",
        "8. **v12.1 的两层只保留结构**：`CAP_OPT` 与 `VOL_LAYER` 只在原来那七只 ETF 上有配置，"
        "证据来自指数口径、本变种未重标 → 其余 47 只走基线 `CAP`、不加波动层；"
        "`rule.OPT=False` 一键回退到纯规格口径（全池都不加）。",
        "9. **54 只里有多少只真的适合套八态，本报告不替你做结论**：§1c 的 Δ夏普排序就是答案的起点，"
        "但 ETF 自身量对宽基/QDII 的污染是**系统性**的，不能靠排名洗掉。"
        "要恢复 v12 的指数口径，只能回 vol_legs（那边有 `_idxcache` 的指数量能）。",
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
            print(f"  {r['name']:14s} 跳过：{r['note']}")
            continue
        print(f"  {r['name']:14s} 年化 {pct(r['ann']):>8s} · 回撤 {pct(r['mdd']):>8s} · "
              f"夏普 {r['sharpe']:4.2f} · 许可 {r['expo']:.0%} · 调仓 {r['acts']:3d} · "
              f"恒持 {pct(r['bh_ann'])}")


if __name__ == "__main__":
    main()
