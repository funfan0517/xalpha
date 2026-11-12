# -*- coding: utf-8 -*-
"""V12 · 敏感性扫描（三个旋钮 + 两个反证）。

扫描项全部是**本仓库自己加的那一层**，规格本身的参数（MA / z_th / rvol / 多数决）不扫 ——
它们是规格在双样本上扫出来的，本仓库没有资格用 13 年 ETF 数据去动摇它们。

1. `UP_CONFIRM`（升格确认日数）1 / 2 / 3 / 5：唯一与换手直接挂钩的旋钮；
2. **映射反证**：把「急涨 100% / 慢涨 50%」对调成「慢涨 100% / 急涨 50%」。
   如果反证版没有明显变差，说明这份 CAP 映射是拍脑袋的（规格 §4① 的结论不成立）；
3. **regime 单层基准**：只用 regime（向上满仓 / 向下空仓），不要八态 ——
   回答「八态这一层究竟比 5 日多数决多给了什么」；
4. **恒持**：下限。

产物：`_v12_sweep.md`（本目录）。

用法：`python strategies/vol_legs/v12_octo_trend/research/_sweep.py`
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
_V12 = os.path.dirname(_HERE)
if _V12 not in sys.path:
    sys.path.insert(0, _V12)

import rule           # noqa: E402
import data as vdata  # noqa: E402
import engine         # noqa: E402

OUT_MD = os.path.join(_HERE, "_v12_sweep.md")

# 反证：把「急/慢」这一刀对调（规格 §4① 说钱在「急」里，对调后应当明显变差）
CAP_REVERSED = dict(rule.CAP)
CAP_REVERSED["急涨"], CAP_REVERSED["慢涨"] = rule.CAP["慢涨"], rule.CAP["急涨"]

# 反证 2：不区分急/慢，只看方向（上涨 100% / 下跌 20%）
CAP_BINARY = {s: (1.0 if s in ("急涨", "慢涨", "弱回调", "强回调") else 0.2)
              for s in rule.STATE_ORDER}


def evaluate(df, w, start, label, kind="八态"):
    nav = engine.run(df, w).loc[start:]
    w_st = w.loc[start:]
    p = engine.perf(nav)
    return dict(label=label, kind=kind, ann=p["ann"], mdd=p["mdd"], sharpe=p["sharpe"],
                expo=float(w_st.mean()), acts=len(engine.actions(w_st)), days=p["days"])


def main():
    legs = rule.CORE_LEGS or ["hs300"]
    L = [
        "# V12 · 敏感性扫描（升格确认 / 映射反证 / regime 单层）",
        "",
        "> 由 `research/_sweep.py` 生成。扫描对象**只限本仓库加的那一层**（`UP_CONFIRM` 与八态→许可映射）；"
        "规格的 MA/z/rvol/多数决参数不动 —— 那是规格在双样本上标的，13 年 ETF 数据没资格动摇它。",
        "",
    ]
    for leg in legs:
        df = vdata.frame(leg)
        ind = engine.indicators(df)
        cx = engine.classify(ind)
        first = cx["state"].notna()
        if not first.any():
            continue
        start = df.index[int(np.argmax(first.to_numpy()))]
        rows = []

        # 1) UP_CONFIRM
        for nd in (1, 2, 3, 5):
            w = engine.positions(cx["state"], up_confirm=nd)
            rows.append(evaluate(df, w, start, f"UP_CONFIRM={nd}"
                                 + ("（默认）" if nd == rule.UP_CONFIRM else "")))

        # 2) 映射反证
        for label, cmap in (("映射反证：慢涨 100% / 急涨 50%", CAP_REVERSED),
                            ("映射简化：涨 100% / 跌 20%（不分急慢）", CAP_BINARY)):
            w = engine.positions(cx["state"], cap_map=cmap)
            rows.append(evaluate(df, w, start, label, kind="反证"))

        # 3) regime 单层
        w_reg = pd.Series((cx["regime_used"] == "向上").astype(float), index=df.index)
        w_reg.iloc[:int(np.argmax(first.to_numpy()))] = 0.0
        rows.append(evaluate(df, w_reg, start, "regime 单层：向上 100% / 向下 0%", kind="基准"))

        # 4) 恒持 + **曝光匹配**基准
        # 八态的平均许可只有 45%，拿它去和 100% 恒持比回撤是不公平的（低曝光天然低回撤）。
        # 所以再给一条「恒定等于八态平均许可」的基准 —— 这条才是「择时到底有没有用」的对手。
        w_hold = pd.Series(1.0, index=df.index)
        rows.append(evaluate(df, w_hold, start, "恒持 100%（买入持有）", kind="基准"))
        expo_def = next(r["expo"] for r in rows if r["label"].startswith("UP_CONFIRM=2"))
        w_eq = pd.Series(expo_def, index=df.index)
        rows.append(evaluate(df, w_eq, start, f"恒持 {expo_def:.0%}（曝光匹配）", kind="基准"))

        L += [f"## {rule.NAMES[leg]}（{rule.LEGS[leg][0]} · {start.date()} ~ {df.index[-1].date()}）", "",
              "| 口径 | 类型 | 年化 | 最大回撤 | 夏普 | 平均许可 | 调仓 |",
              "|---|---|---:|---:|---:|---:|---:|"]
        for r in rows:
            L.append(f"| {r['label']} | {r['kind']} | {r['ann'] * 100:+.2f}% | "
                     f"{r['mdd'] * 100:+.2f}% | {r['sharpe']:.2f} | {r['expo']:.0%} | {r['acts']} |")

        d = next(r for r in rows if r["label"].startswith("UP_CONFIRM=2"))
        rev = next(r for r in rows if r["label"].startswith("映射反证"))
        reg = next(r for r in rows if r["label"].startswith("regime 单层"))
        eq = next(r for r in rows if r["label"].startswith("恒持 ") and "曝光匹配" in r["label"])
        L += ["",
              f"- **映射反证**：慢涨满仓版夏普 {rev['sharpe']:.2f} vs 默认 {d['sharpe']:.2f}"
              f"（{rev['sharpe'] - d['sharpe']:+.2f}）—— "
              + ("反证版更差，说明「钱在急里」这一刀在本仓库数据上同样成立。"
                 if rev["sharpe"] < d["sharpe"] else
                 "⚠ 反证版没有更差，说明这份映射在本样本上不可辨（别把它当结论）。"),
              f"- **八态 vs regime 单层**：夏普 {d['sharpe']:.2f} vs {reg['sharpe']:.2f}"
              f"（{d['sharpe'] - reg['sharpe']:+.2f}）、回撤 {d['mdd'] * 100:+.2f}% vs "
              f"{reg['mdd'] * 100:+.2f}%、调仓 {d['acts']} vs {reg['acts']} —— "
              + ("八态这一层是**净增益**。" if d["sharpe"] > reg["sharpe"] else
                 "⚠ 八态相对 regime 单层没有增益，它只值「状态命名」的钱。"),
              f"- **曝光匹配后还剩多少**：八态平均许可 {d['expo']:.0%}，"
              f"对应的恒定仓位基准是 年化 {eq['ann'] * 100:+.2f}% / 回撤 {eq['mdd'] * 100:+.2f}% / "
              f"夏普 {eq['sharpe']:.2f}；八态 {d['ann'] * 100:+.2f}% / {d['mdd'] * 100:+.2f}% / "
              f"{d['sharpe']:.2f}（{d['sharpe'] - eq['sharpe']:+.2f} 夏普）。"
              "**这一行才是「择时到底有没有用」的答案** —— 拿 45% 仓位的策略去比 100% 恒持的回撤，"
              "赢的是仓位不是规则。",
              ""]

    L += [
        "## 读法",
        "",
        "- `UP_CONFIRM` 只在「换手 ↔ 滞后」之间换位置：1 日最跟手、5 日最钝。"
        "夏普的差异如果小于 0.05，别选数字最大的那一格（那是噪声），按执行摩擦选 —— 场外 A 类按 2，"
        "场内 ETF 按 1 也合理。",
        "- **两个反证的用途不同**：「慢涨满仓」验的是规格 §4① 的方向对不对；"
        "「涨 100% / 跌 20%」验的是「八态这一层值不值」。两个都通过，这条策略才有存在理由。",
        "- 扫描窗口是**沪深300 单腿**（执行范围）。组合层的差异会被 6 条诊断腿摊薄，不适合用来挑参数。",
        "",
        "> 机械规则输出，非投资建议。",
    ]

    with io.open(OUT_MD, "w", encoding="utf-8", newline="\n") as f:
        f.write("\n".join(L))
    print("报告:", OUT_MD)
    for line in L:
        if line.startswith("|") and "年化" not in line and "---" not in line:
            print("  " + line)


if __name__ == "__main__":
    main()
