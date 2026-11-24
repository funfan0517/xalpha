# -*- coding: utf-8 -*-
"""V12 · 波动层叠法：`min`（现行）vs `mul`（相乘）—— A股两腿专项。

## 问题

`engine.vol_layer()`（以及 `rule.VOL_LAYER`）现在七腿一律用
`w = min(八态许可, 波动层系数)`。但 `research/er_vol_layer` 的拆解表里，
**A股两条长腿换成相乘（`w = 许可 × 系数`）反而更好**：

| 腿 | C1（min，现行） | C2（mul） |
|---|---:|---:|
| 沪深300 | 0.92 | 1.01 |
| 中证A500 | 0.87 | 0.93 |

那份报告的 `C2` 行**没有配同曝光基准** —— 相乘天然比 `min` 更压仓位
（hs300 平均 31% → 25%、a500 37% → 29%），而「仓位更低 → 夏普更高」是机械的。
所以本研究补两把尺子，回答「C2 的优势是不是只是少拿」：

1. `B2' 等比缩仓`：把现行的 `min` 仓位序列整体缩到 `mul` 的**同一平均仓位** ——
   同样平均暴露、只差「什么时候缩」。`mul` 打穿它才算有信息。
2. `mul 抬回同曝光`：反向把 `mul` 抬回 `min` 的平均仓位（诊断用，>1 会被截断，
   截断占比一并报出），看优势是不是双向稳健。

## 与 `er_vol_layer` 的口径差别（重要）

那份报告的 `S1` 是 **2026-11-19 之前**的波动层（kc50 固定 σ覆盖 25%、其余腿无覆盖），
它比较的是「装不装波动层」。本研究比较的是「装好之后用哪种合成函数」，
所以基线是**现行生效口径**：`rule.cap_map(leg)` + `rule.vol_layer_of(leg)` 的 `min`，
与 `backtest/_v12_octo_trend_bt.md` §1 应逐位一致（报告里带自检表）。

## 口径

- 价格 / 量能：`data.frame(leg)`（六条真实指数 + 黄金 `518880`）；
- 成本：`engine.run`（T 日收盘定仓、T+1 起承担收益、单边 5bp、空仓计货基 1.40%）；
- 每条腿同一 `start`（第一条出状态的 bar）；
- **不改 `rule.py`**：落地改法写在报告末尾。

产物：`_vol_mul.md`（本目录）。

用法：
    python strategies/vol_legs/v12_octo_trend/research/vol_mul/_vol_mul.py
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
_V12 = os.path.dirname(os.path.dirname(_HERE))            # v12_octo_trend
if _V12 not in sys.path:
    sys.path.insert(0, _V12)

import data as vdata  # noqa: E402
import engine          # noqa: E402
import rule            # noqa: E402

OUT_MD = os.path.join(_HERE, "_vol_mul.md")
TD = rule.TRADING_DAYS

# 回测报告 §1 的现行读数（2026-11-20 重跑），用于自检「基线是否接对了」。
# 数据每刷新一天数字会微动，所以只报差异、不 assert。
EXPECT = {"a500": 0.87, "hs300": 0.92, "ndx": 0.69, "spx": 0.64,
          "kc50": 0.89, "hkt": 0.08, "gold": 0.81}

# 本研究的主对象：A股两条长腿（样本 > 20 年、结论唯一能站住的两条）
FOCUS = ("hs300", "a500")


# ---------------------------------------------------------------- 仓位序列
def leg_series(leg):
    """-> dict(df, p, cx, w_base, coef, w_min, w_mul, start, both_bind)。

    `both_bind` = `八态许可 < 1` 且 `系数 < 1` 的日子占比 —— 这是 `min` 与 `mul`
    唯一不同的地方（任一侧为 1 时两者相等），也是「二次压缩」发生的位置。
    """
    df = vdata.frame(leg)
    p = rule.params(leg)
    ind = engine.indicators(df, p)
    cx = engine.classify(ind, p)
    w_base = engine.positions(cx["state"], cap_map=rule.cap_map(leg))
    cfg = rule.vol_layer_of(leg)
    if cfg:
        coef = engine.vol_coef(df, cfg)
        w_min = engine.vol_layer(df, w_base, cfg)
        w_mul = (w_base * coef).clip(0.0, 1.0)
    else:                                   # OPT=False 时没有波动层：两者恒等
        coef = pd.Series(1.0, index=df.index)
        w_min = w_base.copy()
        w_mul = w_base.copy()
    first = cx["state"].notna()
    start = df.index[int(np.argmax(first.to_numpy()))]
    bind = ((w_base < 1.0 - 1e-9) & (coef < 1.0 - 1e-9))
    return dict(leg=leg, df=df, p=p, cx=cx, w_base=w_base, coef=coef,
                w_min=w_min, w_mul=w_mul, start=start,
                both_bind=float(bind.loc[start:].mean()))


# ---------------------------------------------------------------- 评分
def stats(nav, w):
    pf = engine.perf(nav)
    yrs = max(pf["days"] / TD, 1e-9)
    acts = len(engine.actions(w))
    tno = float(w.diff().fillna(w).abs().sum())
    return dict(ann=pf["ann"], vol=pf["vol"], mdd=pf["mdd"], sharpe=pf["sharpe"],
                expo=float(w.mean()), acts=acts, apy=acts / yrs,
                fee=tno * rule.FEE_SIDE / yrs * 100.0, days=pf["days"])


def halves(nav):
    n = len(nav)
    if n < 20:
        return (np.nan, np.nan)
    return (engine.perf(nav.iloc[: n // 2])["sharpe"],
            engine.perf(nav.iloc[n // 2 - 1:])["sharpe"])


def score(s, w):
    """在 start 之后切片评分。"""
    w_s = w.loc[s["start"]:]
    return stats(engine.run(s["df"], w).loc[s["start"]:], w_s)


HEAD = ("| 口径 | 年化 | 波动 | 最大回撤 | 夏普 | 平均仓位 | 次/年 | 摩擦 |\n"
        "|---|---:|---:|---:|---:|---:|---:|---:|")


def row(name, st):
    return (f"| {name} | {st['ann'] * 100:+.2f}% | {st['vol'] * 100:.1f}% | "
            f"{st['mdd'] * 100:+.2f}% | **{st['sharpe']:.2f}** | {st['expo']:.0%} | "
            f"{st['apy']:.0f} | {st['fee']:.2f}pp |")


# ---------------------------------------------------------------- 主流程
def main():
    vfile = os.path.abspath(vdata.__file__)
    if os.path.dirname(vfile) != _V12:
        raise RuntimeError(f"`import data` 解析到 {vfile}，不是 v12_octo_trend/data.py")

    L = ["# V12 · 波动层叠法 `min` vs `mul`（A股两腿专项）", "",
         "> 由 `research/vol_mul/_vol_mul.py` 生成。**模拟结果，非投资建议。**",
         "> 基线是**现行生效口径**（`rule.cap_map(leg)` + `rule.vol_layer_of(leg)` 的 `min`，"
         "即 `backtest/_v12_octo_trend_bt.md` §1），成本模型 `engine.run`"
         f"（T 日收盘定仓、T+1 起承担收益、单边 {rule.FEE_SIDE * 1e4:.1f}bp、"
         f"空仓计货基 {rule.CASH_ANNUAL:.2%}）。**不改 `rule.py`。**",
         "",
         "> 与 `research/er_vol_layer` 的区别：那份的 `S1` 是 2026-11-19 **之前**的波动层"
         "（比较「装不装」），本报告比较「装好之后用 `min` 还是 `mul`」；"
         "它给出的 C2 行没有同曝光基准，这里补上。",
         ""]

    S = {}
    for leg in rule.LEG_ORDER:
        S[leg] = leg_series(leg)

    # ---------------- 自检
    L += ["## 0. 自检：基线是否接对", "",
          "「现在 min」列应等于回测 §1 的现行读数。数据每刷新一天会微动，"
          "差异 > 0.02 才说明口径接错了。", "",
          "| 腿 | 现在 min 夏普 | 回测 §1 | 差 | 样本末端 |", "|---|---:|---:|---:|---|"]
    ok = True
    for leg in rule.LEG_ORDER:
        s = S[leg]
        sc = score(s, s["w_min"])
        exp = EXPECT.get(leg)
        d = sc["sharpe"] - exp if exp is not None else np.nan
        if exp is not None and abs(d) > 0.02:
            ok = False
        L.append(f"| {rule.NAMES[leg]} | {sc['sharpe']:.2f} | "
                 f"{exp if exp is not None else '—'} | {d:+.2f} | "
                 f"{s['df'].index[-1].date()} |")
    L += ["", f"> 自检结论：**{'通过' if ok else '有腿超差 → 先查口径，别往下读'}**。", ""]

    # ---------------- 七腿一览：min vs mul
    L += ["## 1. 七腿一览：`min`（现行）vs `mul`", "",
          "`min` = `min(许可, 系数)`；`mul` = `许可 × 系数`。两者只在"
          "「许可 < 1 且 系数 < 1」的日子不同（任一侧为 1 时恒等），"
          "所以 `mul` 恒 `≤ min`、平均仓位一定更低。", "",
          "| 腿 | 现在 min 夏普 | mul 夏普 | Δ夏普 | min 仓位 | mul 仓位 | 双侧同时受限 | 样本 |",
          "|---|---:|---:|---:|---:|---:|---:|---|"]
    R = {}
    for leg in rule.LEG_ORDER:
        s = S[leg]
        a = score(s, s["w_min"])
        b = score(s, s["w_mul"])
        R[leg] = dict(min=a, mul=b, s=s)
        yrs = a["days"] / TD
        L.append(f"| {rule.NAMES[leg]} | {a['sharpe']:.2f} | {b['sharpe']:.2f} | "
                 f"{b['sharpe'] - a['sharpe']:+.2f} | {a['expo']:.0%} | {b['expo']:.0%} | "
                 f"{s['both_bind']:.0%} | {yrs:.1f}y |")
    L.append("")

    # ---------------- A股两腿：同曝光基准
    L += ["## 2. A股两腿：`mul` 的优势是不是「只是少拿」", "",
          "两把尺子：",
          "① `B2' 等比缩仓` = 现行的 `min` 仓位 × k（k = mul 平均仓位 / min 平均仓位）。"
          "同平均暴露、只差时间分布 —— **`mul − B2'` 才是合成函数的边际**；",
          "② `mul 抬回同曝光` = `mul` ÷ k（>1 截断）。反向诊断，若抬回后仍不输 `min`，"
          "说明优势不是靠低仓位。", ""]
    for leg in FOCUS:
        s, r = S[leg], R[leg]
        a, b = r["min"], r["mul"]
        k = b["expo"] / max(a["expo"], 1e-9)
        w_b2 = (s["w_min"] * k).clip(0.0, 1.0)
        b2 = score(s, w_b2)
        w_up = (s["w_mul"] / max(k, 1e-9)).clip(0.0, 1.0)
        clip_share = float((s["w_mul"] / max(k, 1e-9) > 1.0).loc[s["start"]:].mean())
        up = score(s, w_up)

        L += [f"### {rule.NAMES[leg]}（{rule.LEGS[leg][0]}）", "",
              f"样本 **{s['start'].date()} ~ {s['df'].index[-1].date()}**"
              f"（{a['days'] / TD:.1f} 年）· 价格 {vdata.vol_note(leg)}", "", HEAD,
              row("现在 min", a), row("mul（相乘）", b),
              row(f"B2' 等比缩仓 ×{k:.2f}", b2),
              row(f"mul 抬回同曝光 ÷{k:.2f}（截断 {clip_share:.1%}）", up), ""]

        ha, hb = halves(engine.run(s["df"], s["w_min"]).loc[s["start"]:]), \
            halves(engine.run(s["df"], s["w_mul"]).loc[s["start"]:])
        hb2 = halves(engine.run(s["df"], w_b2).loc[s["start"]:])
        hup = halves(engine.run(s["df"], w_up).loc[s["start"]:])
        L += ["| 口径 | 前半夏普 | 后半夏普 |", "|---|---:|---:|",
              f"| 现在 min | {ha[0]:+.2f} | {ha[1]:+.2f} |",
              f"| mul | {hb[0]:+.2f} | {hb[1]:+.2f} |",
              f"| B2' 等比缩仓 | {hb2[0]:+.2f} | {hb2[1]:+.2f} |",
              f"| mul 抬回同曝光 | {hup[0]:+.2f} | {hup[1]:+.2f} |", "",
              f"> `mul − 现在` = **{b['sharpe'] - a['sharpe']:+.2f}**，"
              f"`mul − B2'` = **{b['sharpe'] - b2['sharpe']:+.2f}**；"
              f"年化 {(b['ann'] - a['ann']) * 100:+.2f}pp、"
              f"回撤 {(b['mdd'] - a['mdd']) * 100:+.1f}pp（正 = 更浅）、"
              f"摩擦 {b['fee'] - a['fee']:+.2f}pp、调仓 {b['apy']:.0f} vs {a['apy']:.0f} 次/年；"
              f"`min` 与 `mul` 有 **{s['both_bind']:.0%}** 的日子不同。"
              "换手次数虽升，但单次幅度被 `许可` 缩小，摩擦基本不变。", ""]
        R[leg].update(b2=b2, up=up, k=k, clip=clip_share, ha=ha, hb=hb, hb2=hb2, hup=hup)

    # ---------------- 组合层
    L += ["## 3. 组合层：只把 A股两腿换成 `mul`", "",
          "七腿等权（`backtest.combo` 的同一种算法：日收益算术平均、`skipna`），"
          "只把 a500 / hs300 的仓位序列从 `min` 换成 `mul`。"
          "成分完全相同，所以差异只来自叠法。", ""]
    navs_min, navs_mul = {}, {}
    for leg in rule.LEG_ORDER:
        s = S[leg]
        navs_min[leg] = engine.run(s["df"], s["w_min"]).loc[s["start"]:]
        w = s["w_mul"] if leg in FOCUS else s["w_min"]
        navs_mul[leg] = engine.run(s["df"], w).loc[s["start"]:]

    def combo(navs):
        rets = {k: v.pct_change() for k, v in navs.items()}
        return (1.0 + pd.DataFrame(rets).mean(axis=1, skipna=True).fillna(0.0)).cumprod()

    c_min, c_mul = combo(navs_min), combo(navs_mul)
    # 共同窗口：七腿齐全（恒生科技最晚）
    cw = max(S[leg]["start"] for leg in rule.LEG_ORDER)
    L += ["| 组合 | 样本 | 年化 | 最大回撤 | 夏普 |", "|---|---|---:|---:|---:|"]
    for nm, nav in (("现在（七腿全 min）", c_min), ("A股两腿换 mul", c_mul)):
        for tag, seg in (("全样本", nav), (f"共同窗口 {cw.date()} 起", nav.loc[cw:])):
            p = engine.perf(seg)
            L.append(f"| {nm} | {tag} | {p['ann'] * 100:+.2f}% | "
                     f"{p['mdd'] * 100:+.2f}% | **{p['sharpe']:.2f}** |")
    L.append("")

    # ---------------- 结论
    L += _conclusion(R, S, c_min, c_mul, cw)

    with io.open(OUT_MD, "w", encoding="utf-8", newline="\n") as f:
        f.write("\n".join(L))
    print("报告:", OUT_MD)
    for leg in rule.LEG_ORDER:
        r = R[leg]
        extra = (f"  mul-B2' {r['mul']['sharpe'] - r['b2']['sharpe']:+.2f}"
                 if "b2" in r else "")
        print(f"  {rule.NAMES[leg]:8s} min {r['min']['sharpe']:+.2f}  "
              f"mul {r['mul']['sharpe']:+.2f} "
              f"({r['mul']['sharpe'] - r['min']['sharpe']:+.2f}){extra}")


def _conclusion(R, S, c_min, c_mul, cw):
    """结论段：数字全部取自 R / 组合净值，避免表与正文脱钩。"""
    a500, hs = R["a500"], R["hs300"]
    d500, dhs = a500["mul"]["sharpe"] - a500["min"]["sharpe"], \
        hs["mul"]["sharpe"] - hs["min"]["sharpe"]
    d2_500 = a500["mul"]["sharpe"] - a500["b2"]["sharpe"]
    d2_hs = hs["mul"]["sharpe"] - hs["b2"]["sharpe"]
    both = (d2_500 > 0.05) and (d2_hs > 0.05)
    folds = all(x["hb"][i] > x["ha"][i] for x in (a500, hs) for i in (0, 1))
    p_min, p_mul = engine.perf(c_min.loc[cw:]), engine.perf(c_mul.loc[cw:])

    others = [leg for leg in rule.LEG_ORDER if leg not in FOCUS]
    worse = [rule.NAMES[l] for l in others
             if R[l]["mul"]["sharpe"] < R[l]["min"]["sharpe"] - 0.02]

    verdict = ("**成立**：两条腿都打穿同曝光基准，且前后两折同向" if (both and folds)
               else "**不成立/不稳**：至少一条腿打不穿同曝光基准或分折反向")

    return ["## 4. 结论", "",
            f"- **A股两腿：`mul` 相对现行 `min` 夏普 {d500:+.2f}（A500）/ {dhs:+.2f}（沪深300）**；"
            f"扣掉「少拿」之后（对 B2' 等比缩仓）仍是 **{d2_500:+.2f} / {d2_hs:+.2f}**，"
            f"前/后两折{'全部同向' if folds else '**未全部同向**'} → {verdict}。",
            f"- **代价**：平均仓位 {a500['min']['expo']:.0%} → {a500['mul']['expo']:.0%}（A500）、"
            f"{hs['min']['expo']:.0%} → {hs['mul']['expo']:.0%}（沪深300），"
            f"年化 {(a500['mul']['ann'] - a500['min']['ann']) * 100:+.2f}pp / "
            f"{(hs['mul']['ann'] - hs['min']['ann']) * 100:+.2f}pp；"
            f"回撤 {(a500['mul']['mdd'] - a500['min']['mdd']) * 100:+.1f}pp / "
            f"{(hs['mul']['mdd'] - hs['min']['mdd']) * 100:+.1f}pp（正 = 更浅）；"
            f"摩擦 {a500['mul']['fee'] - a500['min']['fee']:+.2f}pp / "
            f"{hs['mul']['fee'] - hs['min']['fee']:+.2f}pp（换手次数升但单次幅度更小，摩擦基本不变）。"
            "相乘会同时压「许可低」与「波动高」的日子，所以**年化只会降**，只能用夏普/回撤评价。",
            f"- **其余五腿 `mul` 更差的有 {len(worse)} 条**（{('、'.join(worse)) or '无'}）——"
            "与 `er_vol_layer` 的结论一致，**只该给 A股两腿换，不能七腿统一**。",
            f"- **组合层（共同窗口 {cw.date()} 起）**：夏普 {p_min['sharpe']:.2f} → "
            f"{p_mul['sharpe']:.2f}（{p_mul['sharpe'] - p_min['sharpe']:+.2f}）、"
            f"年化 {p_min['ann'] * 100:+.2f}% → {p_mul['ann'] * 100:+.2f}%、"
            f"回撤 {p_min['mdd'] * 100:+.2f}% → {p_mul['mdd'] * 100:+.2f}%。"
            f"**增益在组合层基本没被稀释**（单腿 {d500:+.2f}/{dhs:+.2f} → 组合 "
            f"{p_mul['sharpe'] - p_min['sharpe']:+.2f}），说明它不是靠某一条腿的低仓位撑出来的。"
            "但组合层的**绝对**改善仍取决于 A股在七腿里的权重（现在是等权 2/7）—— "
            "这一层留给 `P0 组合配权`，本报告不动。",
            "",
            "## 5. 落地改法（若采纳；本报告**未改** `rule.py`）", "",
            "```python",
            "# rule.py · VOL_LAYER 每个 entry 加一个合成函数开关（缺省 min = 现行）",
            "VOL_LAYER = {",
            "    \"a500\":  dict(mode=\"er\", tv=0.20, tv_low=0.10, combine=\"mul\"),",
            "    \"hs300\": dict(mode=\"er\", tv=0.20, tv_low=0.10, combine=\"mul\"),",
            "    \"hkt\":   dict(mode=\"er\", tv=0.20, tv_low=0.10),      # 其余保持 min",
            "    \"gold\":  dict(mode=\"fixed\", tv=0.10),",
            "    \"kc50\":  dict(mode=\"fixed\", tv=0.20),",
            "    \"ndx\":   dict(mode=\"er\", tv=0.20, tv_low=0.10, dead=0.0, gap=0),",
            "    \"spx\":   dict(mode=\"er\", tv=0.20, tv_low=0.10, dead=0.0, gap=0),",
            "}",
            "```",
            "",
            "- `engine.vol_layer()` 读 `cfg.get(\"combine\", \"min\")`：`\"min\"` 走 `concat.min`，"
            "`\"mul\"` 走 `(w * coef).clip(0, 1)`。",
            "- 落地会**改变 A股两腿的目标仓位**：`rule.CHANGELOG` 要加一行、"
            "`vol_layer_txt()` 要带上叠法，然后重跑 `backtest.py` + `daily/_daily_scan.py` + "
            "`pipeline/daily_digest.py` + `pipeline/intraday_preview.py`（AGENTS.md §10）。",
            "- 决策口径提醒：本研究只有**两条腿、各一次分折**，不构成「两折定参」（没有独立折定档）。"
            "按「单一出处」原则，采纳前应让用户明确指定，而不是把它当成自动结论。",
            "",
            "## 6. 决定记录", "",
            "> **2026-11-24：用户评估后不采纳。** 理由：单腿 Δ夏普只有 +0.07 / +0.09"
            "（组合层 +0.08），幅度不值一次「改变每日目标仓位」的口径变更；"
            "且总仓位与各标的仓位由用户自行管理，组合层配权（`P0`）也不在本策略范围内。",
            "> 本报告与脚本**保留**，供以后复查；`rule.py` / `engine.py` 未改。",
            "",
            "> 机械规则输出，非投资建议。", ""]


if __name__ == "__main__":
    main()
