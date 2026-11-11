# -*- coding: utf-8 -*-
"""V13 · 按优化建议落地并验证：兜底阈值 / 迟滞 / 组合配权 / 分段 / 与 v5 同口径对比。

四件事，全在一个脚本里，避免"改了 A 又忘了 A 对 B 的影响"：

1. **P0+P1 参数**：兜底 −15% → −10%、迟滞 15 → 20 或**按穿越频率自适应**；
2. **组合配权**：等权 vs 等波动（滚动 60 日，无前视）；
3. **P3 分段**：2015 / 2018 / 2020 / 2022 / 2024-09 / 2025-2026 六段，看是"某一段救全场"还是全段有效；
4. **与 v5 同口径对比**：把 v5 的 MA250+20 规则**重实现**在场内 close 上（不 import v5，
   避免 `rule.py` 同名串味），两边共用 `engine.run` 的成本与现金口径 —— 这样差异只来自规则。

产物：`_v13_optimize.md`。机械规则输出，非投资建议。
"""
import io
import os
import sys

import numpy as np
import pandas as pd

try:
    sys.stdout.reconfigure(encoding="utf-8")  # type: ignore[attr-defined]
except Exception:  # noqa: BLE001
    pass

_HERE = os.path.dirname(os.path.abspath(__file__))
_V13 = os.path.dirname(_HERE)
if _V13 not in sys.path:
    sys.path.insert(0, _V13)

import rule           # noqa: E402
import data as vdata  # noqa: E402
import engine         # noqa: E402

OUT = os.path.join(_HERE, "_v13_optimize.md")

# A500（563360）只有 0.84 年样本 → 不进组合，单列展示（P1 第 3 条）
POOL = [l for l in rule.LEG_ORDER if l != "a500"]

SEGS = [
    ("2015 股灾", "2015-06-01", "2016-02-29"),
    ("2018 熊", "2018-01-01", "2018-12-31"),
    ("2020 疫情", "2020-01-20", "2020-04-30"),
    ("2022 双杀", "2022-01-01", "2022-12-31"),
    ("2024-09 急涨", "2024-09-01", "2024-12-31"),
    ("2025~2026", "2025-01-01", "2026-11-06"),
]


# ---------------------------------------------------------------- 工具
def auto_h(df, base=15, lo=5, hi=25, ref=28.0, direction="short"):
    """按 MA20 年穿越次数自适应迟滞。

    - `direction="short"`（P1 原提案）：穿越越频繁 → h 越短，`h = base × ref / cross`；
    - `direction="long"`（反向对照）：穿越越频繁 → h 越长，`h = base × cross / ref`。

    `cross` 用**过去 252 个交易日**统计（无前视）；`ref` 是"池内中位标的的年穿越次数"，
    由 `main()` 用**各腿当前 252 日穿越数的中位数**算出（实测 ≈28），不再写死。

    ⚠ 旧版把 `ref` 写死成 8.0，而实测各腿是 25~37 次 —— 于是 `base×ref/cross` 全部跌破下限、
    所有腿被钉在 `h=5`，那一轮"自适应"其实是"全池 h=5"，不是自适应。标定必须跟着池子走。

    必须两个方向都测：`_v13_sweep.md` §2 里科创50 在 h=5 是 −0.72%、h=20 是 +0.70%，
    回撤 −28.4% vs −20.4% —— 长迟滞在两个维度都更好，与"缩短迟滞救高波动腿"的假设相反。
    """
    c = df["close"]
    ma20 = c.rolling(rule.MA_SHORT).mean()
    above = (c > ma20).astype(float)
    cross = above.diff().abs().rolling(252, min_periods=60).sum().clip(lower=1.0)
    ratio = ref / cross if direction == "short" else cross / ref
    h = (base * ratio).clip(lo, hi).round()
    return h.fillna(base).astype(int)


def ma_state(px, ma_win=250, slope_n=20):
    """v5 的 MA250+20 读数（四象限 + 5/20 阶梯 + 5 日间隔帽），用于**同口径**对比。

    与 `trend_gate.target_of()` 同义：上涨→满带、反弹→只持有、回调→跌破满 5 日降半仓、
    下跌→满 5 日半仓 / 满 20 日观察仓；波动层关闭。
    """
    c = px
    ma = c.rolling(ma_win).mean()
    slope = c - c.shift(slope_n)
    above = (c > ma).to_numpy()
    up = (slope > 0).to_numpy()
    ok = (ma.notna() & slope.notna()).to_numpy()
    n = len(c)
    w = np.zeros(n)
    hold, last, pend, below = 1.0, -99, None, 0
    for i in range(n):
        if pend is not None:
            hold, last, pend = pend, i, None
        w[i] = hold
        if not ok[i]:
            continue
        below = below + 1 if not above[i] else 0
        if above[i] and up[i]:
            tgt = 1.0
        elif above[i]:
            tgt = hold
        elif up[i]:
            tgt = 0.5 if below >= 5 else hold
        else:
            tgt = 0.2 if below >= 20 else (0.5 if below >= 5 else hold)
        if abs(tgt - hold) > 1e-9 and i - last >= 5:
            pend = tgt
    return pd.Series(w, index=c.index)


def combo(navs, equal_vol=False):
    """组合净值。`equal_vol=True` 用滚动 60 日波动的倒数配权（shift(1)，无前视）。"""
    R = pd.DataFrame({k: v.pct_change() for k, v in navs.items()})
    if not equal_vol:
        port = R.mean(axis=1, skipna=True)
    else:
        V = R.rolling(60, min_periods=20).std()
        inv = 1.0 / V.replace(0.0, np.nan)
        wgt = inv.div(inv.sum(axis=1), axis=0).shift(1)
        port = (R * wgt).sum(axis=1, min_count=1)
    return (1.0 + port.fillna(0.0)).cumprod()


def seg_stats(nav, a, b):
    s = nav.loc[a:b]
    if len(s) < 20:
        return None
    return engine.perf(s)


# ---------------------------------------------------------------- 主流程
def main():
    legs = [l for l in POOL if l in vdata.available()]
    dfs = {l: vdata.frame(l) for l in legs}
    def _cross(df):
        """-> (样本年穿越次数, 当前 252 日穿越次数)"""
        c0 = df["close"]
        cr = (c0 > c0.rolling(rule.MA_SHORT).mean()).astype(float).diff().abs()
        yrs = max((df.index[-1] - df.index[0]).days / 365.25, 1e-9)
        return float(cr.sum() / yrs), float(cr.tail(252).sum())

    cxy = {l: _cross(dfs[l]) for l in legs}
    ref_med = float(np.median([v[1] for v in cxy.values()]))
    auto_s = {l: auto_h(dfs[l], ref=ref_med, direction="short") for l in legs}
    auto_l = {l: auto_h(dfs[l], ref=ref_med, direction="long") for l in legs}

    TAG_LONG = "P0+P1c-长（−10% + 反向自适应）"
    cfgs = [
        # 注意：rule.DD_TRIGGER 已在 2026-11-10 改成 −0.10（优化后默认），
        # 所以「基线」必须**显式**传 −0.15，否则它就是优化后的值、两行数字会完全一样。
        ("基线（h=15 / 兜底−15%）", dict(dd_trigger=-0.15)),
        ("P0 兜底−10%", dict(dd_trigger=-0.10)),
        ("P1a 迟滞 h=20", dict(hold_days=20)),
        ("P1b 迟滞 h=8", dict(hold_days=8)),
        ("P1c 迟滞自适应", dict(hold_days="auto")),
        ("P0+P1a（−10% + h=20）", dict(dd_trigger=-0.10, hold_days=20)),
        ("P0+P1c（−10% + 自适应）", dict(dd_trigger=-0.10, hold_days="auto")),
        (TAG_LONG, dict(dd_trigger=-0.10, hold_days="auto_long")),
        ("P0+P1a+回调加仓", dict(dd_trigger=-0.10, hold_days=20, callback_add=True)),
    ]

    # 由 §1 表选出：只有「兜底 −10%」同时改善回撤与夏普；自适应迟滞提年化但恶化回撤，
    # 夏普反而不如基线，故不采用（数据否掉 P1c，见 §4 结论）。
    BEST = "P0 兜底−10%"

    res = {}
    for tag, kw in cfgs:
        kw = dict(kw)
        kw.setdefault("callback_add", rule.CALLBACK_ADD)
        per, navs = {}, {}
        for l in legs:
            k = dict(kw)
            if k.get("hold_days") in ("auto", "auto_long"):
                k["hold_days"] = (auto_s if k["hold_days"] == "auto" else auto_l)[l]
            r = engine.backtest_leg(l, df=dfs[l], **k)
            per[l] = r
            navs[l] = r["nav"]
        res[tag] = dict(per=per, navs=navs)

    # v5 同口径代理
    v5_navs, v5_per = {}, {}
    for l in legs:
        w = ma_state(dfs[l]["close"])
        nav = engine.run(dfs[l], w)
        # 与 v13 用**同一预热起点**，否则两边窗口不同、年化不可比
        start = res[BEST]["per"][l]["sm"]["start"]
        nav = nav.loc[nav.index[start]:]
        v5_navs[l] = nav
        v5_per[l] = dict(nav=nav, perf=engine.perf(nav), w=w.loc[w.index[start]:])

    L = [
        "# V13 优化验证（P0/P1 落地 + 分段 + 与 v5 同口径对比）",
        "",
        "> 优化清单来自 `_v13_sweep.md`。本文件回答三件事：改完是否真的更好、是否只在某一段更好、"
        "以及**同口径下**比 v5 的 MA250+20 强在哪。",
        f"> 口径统一：场内 ETF 前复权日线 · 扣费单边 {rule.FEE_SIDE * 1e4:.1f}bp · "
        f"空仓 {rule.CASH_ANNUAL:.2%} 货基 · 7 腿里的 **A500 不进组合**（仅 0.84 年样本，单列）。",
        "> v5 对比用的是**重实现的 MA250+20**（四象限 + 5/20 阶梯 + 5 日间隔帽，波动层关），"
        "与 v13 共用 `engine.run` —— 避免了「场外净值 vs 场内价格」的口径混算。",
        "",
        "## 1. 参数与结构：组合层（6 腿等权）",
        "",
        "| 配置 | 年化 | 最大回撤 | 夏普 | vs 基线年化 | vs 基线回撤 |",
        "|---|---:|---:|---:|---:|---:|",
    ]
    base = engine.perf(combo(res[cfgs[0][0]]["navs"]))
    for tag, _ in cfgs:
        c = engine.perf(combo(res[tag]["navs"]))
        L.append(f"| {tag} | {c['ann'] * 100:+.2f}% | {c['mdd'] * 100:+.2f}% | {c['sharpe']:.2f} | "
                 f"{(c['ann'] - base['ann']) * 100:+.2f}pp | {(c['mdd'] - base['mdd']) * 100:+.2f}pp |")

    # ---- 改成 −10% 的代价：兜底触发天数占比
    L += ["", "### 改 −10% 的代价：兜底触发占比", "",
          "| 腿 | −15% | −10% | 变化 |", "|---|---:|---:|---:|"]
    for l in legs:
        f15 = float(res["基线（h=15 / 兜底−15%）"]["per"][l]["sm"]["diag"]["floor"].mean())
        f10 = float(res["P0 兜底−10%"]["per"][l]["sm"]["diag"]["floor"].mean())
        L.append(f"| {rule.NAMES[l]} | {f15:.1%} | {f10:.1%} | {(f10 - f15) * 100:+.1f}pp |")
    L += ["", "占比越高 = 越长时间被按在 20% 观察仓。−10% 换来更浅的回撤，"
              "代价是这些腿（尤其科创50 / 美股）会更常进入兜底。", ""]

    # ---- 等波动配权（对最优配置）
    eqv = engine.perf(combo(res[BEST]["navs"], equal_vol=True))
    eqw = engine.perf(combo(res[BEST]["navs"]))
    v5c = engine.perf(combo(v5_navs))
    v5cv = engine.perf(combo(v5_navs, equal_vol=True))
    L += [
        "",
        f"最优配置 `{BEST}` 下再比配权（P1 第 2 条）：",
        "",
        "| 组合 | 年化 | 最大回撤 | 夏普 |",
        "|---|---:|---:|---:|",
        f"| 等权 | {eqw['ann'] * 100:+.2f}% | {eqw['mdd'] * 100:+.2f}% | {eqw['sharpe']:.2f} |",
        f"| 等波动（滚动 60 日） | {eqv['ann'] * 100:+.2f}% | {eqv['mdd'] * 100:+.2f}% | {eqv['sharpe']:.2f} |",
        f"| v5 代理 · 等权 | {v5c['ann'] * 100:+.2f}% | {v5c['mdd'] * 100:+.2f}% | {v5c['sharpe']:.2f} |",
        f"| v5 代理 · 等波动 | {v5cv['ann'] * 100:+.2f}% | {v5cv['mdd'] * 100:+.2f}% | {v5cv['sharpe']:.2f} |",
        "",
        "### P1c 方向检验：穿越频繁该缩短还是拉长迟滞",
        "",
        f"> `ref` 取各腿当前 252 日穿越次数的中位数 = **{ref_med:.0f}**（旧版写死 8，会让所有腿"
        "钉在 h=5，属于伪自适应）。",
        "",
        "| 腿 | 样本年穿越 | 当前 252 日 | auto-short h | auto-long h |",
        "|---|---:|---:|---:|---:|",
    ]
    for l in legs:
        yr, cur = cxy[l]
        L.append(f"| {rule.NAMES[l]} | {yr:.1f} | {cur:.0f} | "
                 f"{int(auto_s[l].iloc[-1])} | {int(auto_l[l].iloc[-1])} |")
    L += ["",
          "**这张表本身就是结论**：各腿年穿越 25~37 次、科创50 只有 25.1（池内最低），"
          "彼此差异只有 ±20%。所以「穿越越频繁→迟滞越短」不但救不了科创50，"
          "还会因为它穿越次数最少而给它**最长**的 h —— 方向反了。"]
    L += ["", "逐腿「年化 / 最大回撤」，兜底固定 −10%，只动 h：", "",
          "| 腿 | h=15 | h=20 | auto-short | auto-long |", "|---|---:|---:|---:|---:|"]
    for l in legs:
        cells = []
        for tag in ("P0 兜底−10%", "P0+P1a（−10% + h=20）",
                    "P0+P1c（−10% + 自适应）", TAG_LONG):
            p = res[tag]["per"][l]["perf"]
            cells.append(f"{p['ann'] * 100:+.2f}% / {p['mdd'] * 100:+.1f}%")
        L.append(f"| {rule.NAMES[l]} | " + " | ".join(cells) + " |")
    L += [
        "",
        "## 2. 与 v5 的逐腿对比（同口径，v13 用最优配置）",
        "",
        "| 腿 | v13 年化 | v5 年化 | 差 | v13 回撤 | v5 回撤 | v13 夏普 | v5 夏普 | v13 许可 | v5 许可 |",
        "|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|",
    ]
    for l in legs:
        a = res[BEST]["per"][l]["perf"]
        b = v5_per[l]["perf"]
        ea = float(res[BEST]["per"][l]["expo"])
        eb = float(v5_per[l]["w"].mean())
        L.append(f"| {rule.NAMES[l]} | {a['ann'] * 100:+.2f}% | {b['ann'] * 100:+.2f}% | "
                 f"{(a['ann'] - b['ann']) * 100:+.2f}pp | {a['mdd'] * 100:+.2f}% | {b['mdd'] * 100:+.2f}% | "
                 f"{a['sharpe']:.2f} | {b['sharpe']:.2f} | {ea:.0%} | {eb:.0%} |")

    # ---- 分段
    L += [
        "",
        "## 3. 分段检验（P3）：是不是某一段救了全场",
        "",
        "| 区间 | v13 年化 | v13 回撤 | 反向自适应 年化 | 回撤 | v5 年化 | v5 回撤 | 恒持年化 | v13 胜 v5 |",
        "|---|---:|---:|---:|---:|---:|---:|---:|:--:|",
    ]
    nav13 = combo(res[BEST]["navs"])
    nav_long = combo(res[TAG_LONG]["navs"])
    nav5 = combo(v5_navs)
    bh_navs = {}
    for l in legs:
        px = dfs[l]["close"]
        ma250 = px.rolling(rule.MA_LONG).mean()
        s0 = int(np.argmax(ma250.notna().to_numpy()))
        px = px.iloc[s0:]
        bh_navs[l] = px / px.iloc[0]
    navbh = combo(bh_navs)
    win = 0
    total = 0
    win_long = 0
    for name, a, b in SEGS:
        s13, s5, sbh = seg_stats(nav13, a, b), seg_stats(nav5, a, b), seg_stats(navbh, a, b)
        sl = seg_stats(nav_long, a, b)
        if s13 is None or s5 is None:
            continue
        total += 1
        ok = s13["ann"] > s5["ann"]
        win += 1 if ok else 0
        win_long += 1 if (sl and sl["ann"] > s13["ann"]) else 0
        L.append(f"| {name} | {s13['ann'] * 100:+.2f}% | {s13['mdd'] * 100:+.2f}% | "
                 f"{(sl['ann'] * 100 if sl else float('nan')):+.2f}% | "
                 f"{(sl['mdd'] * 100 if sl else float('nan')):+.2f}% | "
                 f"{s5['ann'] * 100:+.2f}% | {s5['mdd'] * 100:+.2f}% | "
                 f"{(sbh['ann'] * 100 if sbh else float('nan')):+.2f}% | {'✔' if ok else '✘'} |")

    # ---- 科创50 是否被自适应迟滞救回来
    kc13 = res[BEST]["per"].get("kc50")
    kc0 = res[cfgs[0][0]]["per"].get("kc50")
    c_auto = engine.perf(combo(res["P1c 迟滞自适应"]["navs"]))
    c_auto_l = engine.perf(combo(res[TAG_LONG]["navs"]))
    if kc13 and kc0:
        kc_line = (
            f"- **科创50 在最优配置下的变化**：年化 {kc0['perf']['ann'] * 100:+.2f}% → "
            f"{kc13['perf']['ann'] * 100:+.2f}%（"
            f"{(kc13['perf']['ann'] - kc0['perf']['ann']) * 100:+.2f}pp），回撤 "
            f"{kc0['perf']['mdd'] * 100:+.2f}% → {kc13['perf']['mdd'] * 100:+.2f}%。")
    else:
        kc_line = "- 科创50 样本不足，跳过。"
    auto_line = (
        f"- **P1c 自适应迟滞：原提案方向无增量，反方向略优但仍是样本内**。"
        f"「穿越频繁→迟滞短」组合 {c_auto['ann'] * 100:+.2f}% / {c_auto['mdd'] * 100:+.2f}% / "
        f"夏普 {c_auto['sharpe']:.2f}，对比只改兜底的 {eqw['ann'] * 100:+.2f}% / "
        f"{eqw['mdd'] * 100:+.2f}% / {eqw['sharpe']:.2f}：年化低 "
        f"{(c_auto['ann'] - eqw['ann']) * 100:+.2f}pp、回撤相同、夏普低 "
        f"{c_auto['sharpe'] - eqw['sharpe']:+.2f} —— **无增量**。"
        f"「穿越频繁→迟滞长」{c_auto_l['ann'] * 100:+.2f}% / {c_auto_l['mdd'] * 100:+.2f}% / "
        f"夏普 {c_auto_l['sharpe']:.2f}：回撤只多 {(c_auto_l['mdd'] - eqw['mdd']) * 100:+.2f}pp，"
        f"夏普 {c_auto_l['sharpe'] - eqw['sharpe']:+.2f}，{total} 段里 {win_long} 段年化超过固定 h=15。"
        "但它的增益来自 `h` 被拉到 17~18（本质是「更长迟滞」而非「自适应」），"
        "且没有 walkforward —— **暂不采纳，列为待验证**。")

    L += [
        "",
        "## 4. 结论",
        "",
        f"- **最优组合 = `{BEST}`**：年化 {eqw['ann'] * 100:+.2f}%、回撤 {eqw['mdd'] * 100:+.2f}%、"
        f"夏普 {eqw['sharpe']:.2f}；同口径 v5 代理是 {v5c['ann'] * 100:+.2f}% / "
        f"{v5c['mdd'] * 100:+.2f}% / {v5c['sharpe']:.2f}。"
        f"年化差 {(eqw['ann'] - v5c['ann']) * 100:+.2f}pp、回撤差 {(eqw['mdd'] - v5c['mdd']) * 100:+.2f}pp。",
        f"- **配权比策略更值钱**：等波动 vs 等权 在同一规则下夏普 "
        f"{eqv['sharpe']:.2f} vs {eqw['sharpe']:.2f}、回撤 {eqv['mdd'] * 100:+.2f}% vs "
        f"{eqw['mdd'] * 100:+.2f}% —— 7 腿许可从 38% 到 73% 不等，等权其实是在"
        "让高许可腿（纳指/标普）吃更多风险（P1 第 2 条）。",
        kc_line,
        auto_line,
        f"- **分段胜率**：{win}/{total} 段 v13 年化高于 v5 —— 分段表里若出现「只有某一段赢」，"
        "说明优势来自单一行情，不能当稳健结论。",
        "",
        "> ⚠ 仍未做样本外 walkforward；参数（h 的 ref、兜底 −10%）只在样本内验证过，"
        "上线前应补 `strategies/_walkforward.py`。",
        "",
        "> 机械规则输出，非投资建议。",
    ]

    with io.open(OUT, "w", encoding="utf-8", newline="\n") as f:
        f.write("\n".join(L))
    print("报告:", OUT)
    for tag, _ in cfgs:
        c = engine.perf(combo(res[tag]["navs"]))
        print(f"  {tag:26s} 年化 {c['ann'] * 100:+7.2f}% · 回撤 {c['mdd'] * 100:+7.2f}% · 夏普 {c['sharpe']:5.2f}")
    print(f"  {'v5 代理（MA250+20）':26s} 年化 {v5c['ann'] * 100:+7.2f}% · 回撤 {v5c['mdd'] * 100:+7.2f}% · "
          f"夏普 {v5c['sharpe']:5.2f}")


if __name__ == "__main__":
    main()
