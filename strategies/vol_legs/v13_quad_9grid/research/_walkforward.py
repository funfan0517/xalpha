# -*- coding: utf-8 -*-
"""V13 · walk-forward：固定参数在样本外还站得住吗。

为什么写这个：`h=15` / `confirm=5` / 兜底 `−10%` 全部是在 2013-2026 全样本上挑出来的。
邻域检验（`_v13_sweep.md`、`_optimize.md`）只能排除「孤峰」，排除不了「整段样本的运气」。
本脚本做两件事，缺一不可：

1. **固定口径**：把参数钉死，等分窗口，只看第 2 个窗口起的**样本外**表现；
2. **重新选参**：每折只用该窗之前的数据在候选集里选 `(h, confirm)`，再拿到该窗验证。
   如果每折选出来的参数乱跳、且样本外不如固定口径，说明「这套参数」不可选，
   只能当「一次性的、无样本外支撑的结论」使用。

判读规则（**事先写死，不做事后解释**）：

- 固定口径在 **≥2/3** 的样本外窗口里夏普赢恒持 → 判定「通过」；
- 每折选参赢固定口径的窗口数 **< 一半** → 判定「参数不可选，只认固定值」；
- 任一窗口固定口径夏普为负 → 单独标注（不因此否定，但要看见）。

口径：场内 ETF 前复权日线 · 扣费 · 空仓 1.40%，与 `_optimize.md` 一致。
所有指标都是 rolling 计算，截断评估**不构成前视**。

产物：`_v13_walkforward.md`
用法：`python strategies/vol_legs/v13_quad_9grid/research/_walkforward.py`
"""
import io
import os
import sys

import numpy as np

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

OUT = os.path.join(_HERE, "_v13_walkforward.md")

# 冻结口径（本次待验证的三个值）。`callback_add` 显式钉死在定稿值上，
# 否则 `rule.CALLBACK_ADD` 一改、这份历史结论就跟着漂。
FIX = dict(hold_days=15, confirm_days=5, dd_trigger=-0.10, callback_add=False)
# 改动前的口径，作为「不加 confirm」的对照
OLD = dict(hold_days=15, confirm_days=2, dd_trigger=-0.10, callback_add=False)
# 选参候选集：故意取窄，因为邻域检验已显示 h≈15、confirm≈4~6 是平台
CAND = [(h, c) for h in (10, 15, 20) for c in (3, 5, 7)]


def perf(nav):
    return engine.perf(nav)


def seg(nav, a, b):
    s = nav.iloc[a:b]
    return perf(s) if len(s) >= 40 else None


def main():
    legs = [l for l in rule.LEG_ORDER if l in vdata.available()]
    rows, sel_rows, verdict = [], [], []

    for l in legs:
        df = vdata.frame(l)
        nav_fix = engine.backtest_leg(l, df=df, **FIX)["nav"]
        if nav_fix is None:
            continue
        nav_old = engine.backtest_leg(l, df=df, **OLD)["nav"]
        cand_nav = {f"h{h}c{c}": engine.backtest_leg(l, df=df, hold_days=h,
                                                    confirm_days=c, dd_trigger=-0.10)["nav"]
                    for h, c in CAND}
        px = df["close"].loc[nav_fix.index]
        bh = px / px.iloc[0]
        n = len(nav_fix)
        K = 4 if n >= 2000 else 2
        cuts = [int(n * i / K) for i in range(K + 1)]

        for i in range(K):
            a, b = cuts[i], cuts[i + 1]
            pf, pb = seg(nav_fix, a, b), seg(bh, a, b)
            po = seg(nav_old, a, b) if nav_old is not None else None
            if pf is None or pb is None:
                continue
            tag = "参照（参数即挑于本段）" if i == 0 else f"样本外 {i}"
            rows.append((rule.NAMES[l], l, tag, nav_fix.index[a].date(), nav_fix.index[b - 1].date(),
                         pf, po, pb))

            # ---- 重新选参（只用本段之前的数据；第 1 段无历史，跳过）
            if i == 0:
                continue
            scored = []
            for name, nav in cand_nav.items():
                m = seg(nav, 0, a)
                if m is not None:
                    scored.append((name, m["sharpe"]))
            if not scored:
                continue
            best = max(scored, key=lambda z: z[1])[0]
            ps = seg(cand_nav[best], a, b)
            sel_rows.append((rule.NAMES[l], l, f"样本外 {i}", best, ps, pf))

        # ---- 该腿判定
        oos = [r for r in rows if r[1] == l and r[2].startswith("样本外")]
        if oos:
            wins = sum(1 for r in oos if r[5] and r[7] and r[5]["sharpe"] > r[7]["sharpe"])
            verdict.append((rule.NAMES[l], l, len(oos), wins))

    # ---------------------------------------------------------------- 报告
    L = [
        "# V13 · walk-forward（固定参数 vs 每折重新选参）",
        "",
        f"> 冻结口径：`h={FIX['hold_days']}` · `confirm={FIX['confirm_days']}` · "
        f"兜底 `{FIX['dd_trigger']:.0%}`；对照为改动前 `confirm={OLD['confirm_days']}`。",
        "> 窗口按各腿自身样本等分（≥2000 根切 4 段，否则 2 段）。第 1 段是「参数挑于此」的参照窗，"
        "**不参与判定**；第 2 段起才是样本外。",
        "> 判读规则事先写死：固定口径在 ≥2/3 样本外窗口夏普赢恒持 → 通过；"
        "每折选参赢固定口径 < 一半 → 参数不可选，只认固定值。",
        "",
        "## 1. 逐窗样本外表现",
        "",
        "| 腿 | 窗口 | 区间 | 固定 年化 | 固定 回撤 | 固定 夏普 | 旧口径 夏普 | 恒持 年化 | 恒持 夏普 | 赢恒持 |",
        "|---|---|---|---:|---:|---:|---:|---:|---:|:--:|",
    ]
    for name, _l, tag, d0, d1, pf, po, pb in rows:
        ok = "✔" if pf["sharpe"] > pb["sharpe"] else "✘"
        L.append(f"| {name} | {tag} | {d0}~{d1} | {pf['ann'] * 100:+.2f}% | {pf['mdd'] * 100:+.2f}% | "
                 f"{pf['sharpe']:.2f} | {(po['sharpe'] if po else float('nan')):.2f} | "
                 f"{pb['ann'] * 100:+.2f}% | {pb['sharpe']:.2f} | {ok} |")

    L += ["", "## 2. 每折重新选参（只用该窗之前的数据）", "",
          "| 腿 | 窗口 | 选中 | 该窗样本外 夏普 | 固定口径 同窗 夏普 | 选参更好 |",
          "|---|---|---|---:|---:|:--:|"]
    for name, _l, tag, best, ps, pf in sel_rows:
        better = "是" if (ps and ps["sharpe"] > pf["sharpe"]) else "**否**"
        L.append(f"| {name} | {tag} | {best} | "
                 f"{(ps['sharpe'] if ps else float('nan')):.2f} | {pf['sharpe']:.2f} | {better} |")

    L += ["", "## 3. 判定", "",
          "| 腿 | 样本外窗口数 | 夏普赢恒持 | 结论 |",
          "|---|---:|---:|---|"]
    for name, _l, n_oos, wins in verdict:
        ok = wins >= max(1, int(np.ceil(n_oos * 2 / 3)))
        L.append(f"| {name} | {n_oos} | {wins}/{n_oos} | {'**通过**' if ok else '不通过'} |")

    n_sel = len(sel_rows)
    n_better = sum(1 for _n, _l, _t, _b, ps, pf in sel_rows if ps and ps["sharpe"] > pf["sharpe"])
    L += ["",
          f"> 每折重新选参优于固定口径的窗口数：**{n_better}/{n_sel}**。"
          + ("选参没有稳定增益 → 参数不可选，只认固定值。"
             if n_sel and n_better * 2 < n_sel else
             "选参在多数窗口更好 → 参数本身可能是可选的，需要更多窗口确认。"),
          "",
          "> 口径：滚动指标、截断评估无前视；机械规则输出，非投资建议。",
          ""]

    with io.open(OUT, "w", encoding="utf-8", newline="\n") as f:
        f.write("\n".join(L))
    print("报告:", OUT)
    for name, _l, n_oos, wins in verdict:
        print(f"  {name:6s} 样本外 {wins}/{n_oos} 赢恒持")
    print(f"  选参优于固定: {n_better}/{n_sel}")


if __name__ == "__main__":
    main()
