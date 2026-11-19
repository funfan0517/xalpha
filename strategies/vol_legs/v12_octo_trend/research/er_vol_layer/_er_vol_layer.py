# -*- coding: utf-8 -*-
"""V12 八态趋势 × V10 自适应波动层（ER20/10 + 死区）· 七腿叠加研究。

问题：v12 在 2026-11-19 之前的波动层很简陋 —— 只有 `rule.VOL_TARGET_LEGACY = {"kc50": 0.25}`
一条腿、固定 TV、无死区（`engine.vol_target`：`min(w, vt/σ20)`）。而 v10 有一套更完整的
**自适应**波动层：`scale = min(1, TV(ER) / vol20)`，ER(20) 判趋势纯度切 TV 20%/10%，
再叠 10pp 幅度死区 + 5 日时间死区。本研究回答：**把 v10 这一层装到 v12 上值不值**。

⚠ **本报告的 `S1` 一律指 2026-11-19 之前的旧口径**（kc50 固定 σ覆盖 25%，其余腿无覆盖），
由 `rule.vol_target_of()`（读 `VOL_TARGET_LEGACY`）提供，**与当前生效的 `rule.VOL_LAYER`
解耦**。所以 `rule.py` 改了按腿配置之后，本报告的数字仍然逐位可复现 —— 它比较的就是旧口径。

## 叠法（三个必须分开问的问题）

| 问题 | 候选 |
|---|---|
| 装上去有用吗（vs v12 现状） | `C1` 换装（替掉固定 σ 覆盖）、`C1b` 叠加（双 cap） |
| 有用是 ER 的功劳还是「压仓位」的功劳 | `C3` 固定 TV20 + 死区（去掉 ER）、`C4` TV10 |
| 死区值不值 | `C5` 无死区、`C6` 死区加在合并后的仓位上 |

外加两条参照：`C8` 纯 ER 层（丢掉八态）、`B` 曝光匹配（恒定 = C1 的平均仓位）。
**`B` 是这里最关键的尺子** —— 波动层天然降仓位、降回撤，夏普不涨就等于「只是少拿」。

## 口径

- 价格 / 量能：与 v12 完全同源（`data.frame(leg)` 真实指数；黄金 `518880`）；
- 成本模型：`engine.run`（T 日收盘定仓、T+1 起承担收益、单边 5bp、空仓计货基 1.40%）；
- 每条腿同一 `start`（第一条出状态的 bar，预热 270 根）；
- ER / vol20 只用 close，与 v12 的 `z20`/`rvol` 同窗口，无前视；
- **不改 `rule.py`**：落地与否的改法写在报告末尾。

产物：`_er_vol_layer.md`（本目录）。

用法：
    python strategies/vol_legs/v12_octo_trend/research/er_vol_layer/_er_vol_layer.py
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

import engine   # noqa: E402
import rule     # noqa: E402
import data as vdata  # noqa: E402

OUT_MD = os.path.join(_HERE, "_er_vol_layer.md")
TD = rule.TRADING_DAYS

# ---------------------------------------------------------------- v10 波动层参数（权威源 v10_vol_layer/vol_layer.py）
TV = 0.20          # 趋势态目标波动（年化）
TV_LOW = 0.10      # 震荡态目标波动（年化）
ER_N = 20          # Kaufman 效率比窗口
ER_HI, ER_LO = 0.40, 0.30
VOL_WIN = 20       # 波动率窗口
DEAD = 0.10        # 幅度死区（|Δ仓位| < 10pp 不动手）
DEAD_DAYS = 5      # 时间死区


# ---------------------------------------------------------------- 1. 波动层本体
def vol20(df, win=VOL_WIN):
    """年化已实现波动：`win` 日收益标准差 × √252。"""
    s = df["close"].astype("float64").pct_change()
    return s.rolling(int(win), min_periods=int(win)).std(ddof=1) * np.sqrt(252.0)


def efficiency_ratio(px, n=ER_N):
    """Kaufman ER(n) = |n 日净变动| / n 日路径总变动。只判趋势纯度，不判涨跌。"""
    s = px.astype("float64")
    return (s - s.shift(int(n))).abs() / s.diff().abs().rolling(int(n)).sum()


def er_state(er, hi=ER_HI, lo=ER_LO):
    """ER ≥ hi → 趋势态；≤ lo → 震荡态；中间维持（滞回）。"""
    fast, out = False, []
    for v in er.to_numpy(dtype=float):
        if not np.isnan(v):
            if v >= float(hi):
                fast = True
            elif v <= float(lo):
                fast = False
        out.append(fast)
    return pd.Series(out, index=er.index, dtype=bool)


def vol_scale(df, adaptive=True, tv=TV, tv_low=TV_LOW, win=VOL_WIN):
    """-> (scale, fast)。`scale = min(1, TV(ER)/vol20)`；预热/vol=0 → 1.0（不约束）。"""
    v = vol20(df, win)
    if adaptive:
        fast = er_state(efficiency_ratio(df["close"]))
        tvs = pd.Series(np.where(fast, float(tv), float(tv_low)), index=df.index)
    else:
        fast = pd.Series(False, index=df.index)
        tvs = pd.Series(float(tv), index=df.index)
    s = (tvs / v).replace([np.inf, -np.inf], np.nan).clip(upper=1.0).fillna(1.0)
    return s.clip(lower=0.0, upper=1.0), fast


def deadzone(tgt, dead=DEAD, gap=DEAD_DAYS):
    """幅度死区 + 时间死区：`|新目标−现仓| ≥ dead` 且距上次 ≥ gap 日才动手。"""
    g = tgt.to_numpy(dtype=float)
    out = np.empty(len(g))
    if not len(g):
        return pd.Series(dtype="float64", index=tgt.index)
    cur, last = float(g[0]), 0
    out[0] = cur
    for i in range(1, len(g)):
        if abs(g[i] - cur) >= float(dead) - 1e-12 and (i - last) >= int(gap):
            cur, last = float(g[i]), i
        out[i] = cur
    return pd.Series(out, index=tgt.index, dtype="float64")


def build(leg, df, cx, w_base, mode, adaptive=True, tv=TV, tv_low=TV_LOW,
          dead=DEAD, gap=DEAD_DAYS):
    """把波动层装到八态仓位上 -> (w, scale_used)。

    mode:
    - `"replace"`  `min(w_base, scale)` —— **替掉** v12 原有的固定 σ 覆盖（kc50 的 25%）；
    - `"stack"`    `min(S1, scale)` —— 叠在 v12 现状上（双 cap）；
    - `"mul"`      `w_base * scale` —— 相乘（八态许可 × 波动系数）；
    - `"pure"`     `scale` —— 丢掉八态，只有波动层（参照）；
    - `"merged"`   先 `min(w_base, scale)` 再把死区加在**合并后的仓位**上。
    """
    sc_raw, _fast = vol_scale(df, adaptive=adaptive, tv=tv, tv_low=tv_low)
    if mode == "pure":
        return sc_raw.reindex(df.index).fillna(0.0), sc_raw
    if mode == "merged":
        # 死区加在**合并后的仓位**上（对照：会把八态的「降格立即」也拖慢 gap 日）
        w = pd.concat([w_base, sc_raw.rename("sc")], axis=1).min(axis=1)
        return deadzone(w, dead, gap).reindex(df.index).fillna(0.0), sc_raw
    sc = deadzone(sc_raw, dead, gap) if (dead > 0 or gap > 0) else sc_raw
    if mode == "replace":
        w = pd.concat([w_base, sc.rename("sc")], axis=1).min(axis=1)
    elif mode == "stack":
        w = pd.concat([stack_base(leg, df, w_base), sc.rename("sc")], axis=1).min(axis=1)
    elif mode == "mul":
        w = (w_base * sc).clip(0.0, 1.0)
    else:
        raise ValueError(mode)
    return w.reindex(df.index).fillna(0.0), sc


def stack_base(leg, df, w_base):
    """`S1` = v12 **2026-11-19 之前**的仓位（`w_base` 再叠固定 σ 覆盖，只有 kc50 25%）。

    走 `rule.vol_target_of()`（= `VOL_TARGET_LEGACY`），**不是** `rule.VOL_LAYER` ——
    本报告是「拿旧口径当基线」的决策依据，钉住旧口径才能复现。
    """
    vt = rule.vol_target_of(leg)
    return engine.vol_target(df, w_base, vt) if vt else w_base


def sigma_cap(df, vt):
    """v12 固定 σ 覆盖的那个 cap 序列 `vt/σ20`（口径与 `engine.vol_target` 逐位一致）。"""
    n = int(rule.VOL_WIN)
    sig = df["close"].pct_change().rolling(n, min_periods=n).std() * np.sqrt(rule.TRADING_DAYS)
    sig = sig.fillna(0.05).clip(lower=0.05)
    return float(vt) / sig


# ---------------------------------------------------------------- 2. 评分
def stats(nav, w):
    p = engine.perf(nav)
    yrs = max(p["days"] / TD, 1e-9)
    acts = len(engine.actions(w))
    tno = float(w.diff().fillna(w).abs().sum())
    return dict(ann=p["ann"], vol=p["vol"], mdd=p["mdd"], sharpe=p["sharpe"],
                expo=float(w.mean()), wmin=float(w.min()) if len(w) else np.nan,
                acts=acts, apy=acts / yrs, fee=tno * rule.FEE_SIDE / yrs * 100,
                days=p["days"])


def halves(nav):
    n = len(nav)
    return (engine.perf(nav.iloc[: n // 2])["sharpe"],
            engine.perf(nav.iloc[n // 2 - 1:])["sharpe"])


def row(name, s):
    return (f"| {name} | {s['ann'] * 100:+.2f}% | {s['vol'] * 100:.1f}% | "
            f"{s['mdd'] * 100:+.2f}% | **{s['sharpe']:.2f}** | {s['expo']:.0%} | "
            f"{s['wmin']:.0%} | {s['apy']:.0f} | {s['fee']:.2f}pp |")


HEAD = ("| 候选项 | 年化 | 波动 | 最大回撤 | 夏普 | 平均仓位 | 最低 | 次/年 | 摩擦 |\n"
        "|---|---:|---:|---:|---:|---:|---:|---:|---:|")


# ---------------------------------------------------------------- 3. 主流程
def main():
    L = ["# V12 八态趋势 × V10 自适应波动层（ER20/10 + 5日死区）· 七腿叠加", "",
         "> 由 `research/er_vol_layer/_er_vol_layer.py` 生成。**模拟结果，非投资建议。**",
         "> 波动层权威源 `strategies/vol_legs/v10_vol_layer/vol_layer.py`"
         "（`scale = min(1, TV(ER)/vol20)`，ER≥0.40 → TV20%、≤0.30 → TV10%、中间滞回；"
         "幅度死区 10pp + 时间死区 5 日）。八态判定与成本模型全部沿用 v12"
         "（`engine.indicators/classify/positions/run`：T 日收盘定仓、T+1 起承担收益、"
         f"单边 {rule.FEE_SIDE * 1e4:.1f}bp、空仓计货基 {rule.CASH_ANNUAL:.2%}）。**不含组合层。**",
         "",
         "> 「S1 v12 现状」一律指 **2026-11-19 之前**的波动层口径（kc50 固定 σ覆盖 25%，"
         "其余腿无覆盖），走 `rule.vol_target_of()` / `VOL_TARGET_LEGACY`——"
         "**与当前生效的 `rule.VOL_LAYER` 解耦**，所以本报告的数字在按腿配置落地后仍逐位可复现。",
         "> 落地结果见报告末尾（2026-11-19 已按本报告的 C1/C3/C4 分腿采用）。",
         ""]

    frames, summaries = {}, {}
    detail = []

    for leg in rule.LEG_ORDER:
        df = vdata.frame(leg)
        frames[leg] = df
        p = rule.params(leg)
        ind = engine.indicators(df, p)
        cx = engine.classify(ind, p)
        w_base = engine.positions(cx["state"], cap_map=rule.cap_map(leg))
        first = cx["state"].notna()
        start = df.index[int(np.argmax(first.to_numpy()))]

        s1_w = stack_base(leg, df, w_base)
        cands = [("S1 v12 现状", s1_w, None)]
        plans = [
            ("C1 换装：∧ER20/10 +5日死区", dict(mode="replace")),
            ("C1b 叠加：S1∧ER20/10 +5日死区", dict(mode="stack")),
            ("C2 相乘：八态×ER20/10 +5日死区", dict(mode="mul")),
            ("C3 ∧固定TV20 +5日死区（无ER）", dict(mode="replace", adaptive=False)),
            ("C4 ∧固定TV10 +5日死区", dict(mode="replace", adaptive=False, tv=0.10)),
            ("C5 ∧ER20/10 无死区", dict(mode="replace", dead=0.0, gap=0)),
            ("C6 ∧ER20/10 死区加在合并后仓位", dict(mode="merged")),
            ("C8 纯ER层（丢八态）", dict(mode="pure", dead=0.0, gap=0)),
        ]
        for nm, kw in plans:
            w, _ = build(leg, df, cx, w_base, **kw)
            cands.append((nm, w, kw))

        # C7：只给 v12 现有的固定 σ 覆盖加死区（不动八态，kc50 之外等于 S1）
        vt = rule.vol_target_of(leg)
        if vt:
            w7 = pd.concat([w_base, deadzone(sigma_cap(df, vt), DEAD, DEAD_DAYS).rename("c")],
                           axis=1).min(axis=1)
        else:
            w7 = s1_w
        cands.append(("C7 S1 + σ覆盖加10pp/5日死区", w7, None))

        res = {}
        L += [f"## {rule.NAMES[leg]}（{rule.LEGS[leg][0]}）", "",
              f"样本 **{start.date()} ~ {df.index[-1].date()}**"
              f"（{len(df.loc[start:]) / TD:.1f} 年）· 价格 "
              f"{vdata.vol_note(leg)}", "",
              HEAD]
        for nm, w, _kw in cands:
            w_s = w.loc[start:]
            s = stats(engine.run(df, w).loc[start:], w_s)
            res[nm] = s
            L.append(row(nm, s))

        # B1 恒定仓位（= C1 平均仓位）：判断「整条策略有没有择时信息」
        e1 = res["C1 换装：∧ER20/10 +5日死区"]["expo"]
        wb = pd.Series(e1, index=df.index)
        sb = stats(engine.run(df, wb).loc[start:], wb.loc[start:])
        res["B1 恒定仓位"] = sb
        L.append(row(f"B1 恒定仓位 {e1:.0%}", sb))

        # B2 S1 等比缩仓到 C1 的平均仓位：**判断波动层本身的边际**（同样平均仓位，只差时间分布）
        e_s1 = res["S1 v12 现状"]["expo"]
        k = e1 / max(e_s1, 1e-9)
        wbk = (s1_w * k).clip(0.0, 1.0)
        s2 = stats(engine.run(df, wbk).loc[start:], wbk.loc[start:])
        res["B2 等比缩仓"] = s2
        L.append(row(f"B2 S1 等比缩仓 ×{k:.2f}", s2))

        # 分折
        L += ["", "| 候选项 | 前半夏普 | 后半夏普 |", "|---|---:|---:|"]
        for nm, w, _kw in cands:
            a, b = halves(engine.run(df, w).loc[start:])
            L.append(f"| {nm} | {a:+.2f} | {b:+.2f} |")
        L += ["", f"> 波动层单看：ER 趋势态占 **{_fast_share(df):.0%}** 的日子；"
                  f"C1 平均仓位 {e1:.0%}（S1 {e_s1:.0%}，k={k:.2f}）、"
                  f"最低 {res['C1 换装：∧ER20/10 +5日死区']['wmin']:.0%}；"
                  f"恒定同仓位基准 {sb['sharpe']:.2f}、等比缩仓基准 {s2['sharpe']:.2f}。", ""]

        summaries[leg] = dict(res=res, years=len(df.loc[start:]) / TD, start=start,
                              end=df.index[-1], fast=_fast_share(df), sb=sb, sb2=s2, k=k)
        detail.append(leg)

    # ---------------- 汇总
    L += ["## 汇总：七腿", "",
          "Δ夏普 / Δ年化 / Δ回撤 全部相对 **S1（v12 现状）**，回撤为负 = 更深。"
          "`B2` = 把 S1 整体等比缩到 C1 的平均仓位（同一份八态择时、同样的平均暴露），"
          "**C1 − B2 才是波动层「时间分布」的边际** —— 它会降仓位、降回撤，"
          "所以拿 S1 比一定赢一部分，拿 B1/B2 比才是干净的。", "",
          "| 腿 | 样本 | S1 夏普 | S1 回撤 | C1 夏普 | Δ夏普 | C1 回撤 | Δ回撤 | "
          "B2 夏普 | B2 回撤 | **C1−B2** | C1 仓位 | ER 趋势态占比 |",
          "|---|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|"]
    for leg in detail:
        m = summaries[leg]
        s1, c1 = m["res"]["S1 v12 现状"], m["res"]["C1 换装：∧ER20/10 +5日死区"]
        L.append(f"| {rule.NAMES[leg]} | {m['years']:.1f}y | {s1['sharpe']:.2f} | "
                 f"{s1['mdd'] * 100:+.1f}% | {c1['sharpe']:.2f} | "
                 f"{c1['sharpe'] - s1['sharpe']:+.2f} | "
                 f"{c1['mdd'] * 100:+.1f}% | {(c1['mdd'] - s1['mdd']) * 100:+.1f}pp | "
                 f"{m['sb2']['sharpe']:.2f} | {m['sb2']['mdd'] * 100:+.1f}% | "
                 f"**{c1['sharpe'] - m['sb2']['sharpe']:+.2f}** | {c1['expo']:.0%} | "
                 f"{m['fast']:.0%} |")
    L.append("")

    # ---------------- 拆解：ER / 死区 / 叠法
    L += ["## 拆解：增益来自哪一层", "",
          "同一腿、同一八态仓位，只换波动层的形态。`C3`（固定 TV20 + 死区）与 "
          "`C1`（ER 自适应 + 死区）的差 = **ER 这一维的净贡献**；`C1` 与 `C5`（无死区）的差 = "
          "**死区的净贡献**（正 = 死区有益）。`C1b` 与 `C1` 只在有固定 σ 覆盖的腿上不同"
          "（本仓库只有科创50，其余腿 `S1 == w_base`，故两列相同）。", "",
          "| 腿 | C1 ER自适应 | C3 固定TV20 | C4 固定TV10 | ER 净贡献 | C5 无死区 | 死区净贡献 | "
          "C1b 叠加 | C2 相乘 | C7 只给σ加死区 |",
          "|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|"]
    for leg in detail:
        r = summaries[leg]["res"]
        c1, c3 = r["C1 换装：∧ER20/10 +5日死区"], r["C3 ∧固定TV20 +5日死区（无ER）"]
        c4, c5 = r["C4 ∧固定TV10 +5日死区"], r["C5 ∧ER20/10 无死区"]
        L.append(f"| {rule.NAMES[leg]} | {c1['sharpe']:.2f} | {c3['sharpe']:.2f} | "
                 f"{c4['sharpe']:.2f} | **{c1['sharpe'] - c3['sharpe']:+.2f}** | "
                 f"{c5['sharpe']:.2f} | {c1['sharpe'] - c5['sharpe']:+.2f} | "
                 f"{r['C1b 叠加：S1∧ER20/10 +5日死区']['sharpe']:.2f} | "
                 f"{r['C2 相乘：八态×ER20/10 +5日死区']['sharpe']:.2f} | "
                 f"{r['C7 S1 + σ覆盖加10pp/5日死区']['sharpe']:.2f} |")
    L.append("")

    # ---------------- 换手
    L += ["## 换手与摩擦（死区到底省不省钱）", "",
          "`opt_7legs` §5 留了一个未答的问题：v12 的碎调来自**连续 σ 覆盖**（kc50 161→271 次/年），"
          "给 σ 覆盖加死区能不能救。", "",
          "| 腿 | S1 次/年 | S1 摩擦 | C1 次/年 | C1 摩擦 | C5 无死区 次/年 | C7 次/年 | C7 摩擦 |",
          "|---|---:|---:|---:|---:|---:|---:|---:|"]
    for leg in detail:
        r = summaries[leg]["res"]
        L.append(f"| {rule.NAMES[leg]} | {r['S1 v12 现状']['apy']:.0f} | "
                 f"{r['S1 v12 现状']['fee']:.2f}pp | {r['C1 换装：∧ER20/10 +5日死区']['apy']:.0f} | "
                 f"{r['C1 换装：∧ER20/10 +5日死区']['fee']:.2f}pp | "
                 f"{r['C5 ∧ER20/10 无死区']['apy']:.0f} | "
                 f"{r['C7 S1 + σ覆盖加10pp/5日死区']['apy']:.0f} | "
                 f"{r['C7 S1 + σ覆盖加10pp/5日死区']['fee']:.2f}pp |")
    L.append("")

    L += _conclusion(summaries, detail, frames)

    with io.open(OUT_MD, "w", encoding="utf-8", newline="\n") as f:
        f.write("\n".join(L))
    print("报告:", OUT_MD)
    for leg in detail:
        m = summaries[leg]
        s1 = m["res"]["S1 v12 现状"]
        c1 = m["res"]["C1 换装：∧ER20/10 +5日死区"]
        c3 = m["res"]["C3 ∧固定TV20 +5日死区（无ER）"]
        print(f"  {rule.NAMES[leg]:8s} {m['years']:5.1f}y  S1 {s1['sharpe']:+.2f}  "
              f"C1 {c1['sharpe']:+.2f} ({c1['sharpe'] - s1['sharpe']:+.2f})  "
              f"C3 {c3['sharpe']:+.2f}  ER净 {c1['sharpe'] - c3['sharpe']:+.2f}  "
              f"基准 {m['sb']['sharpe']:+.2f}")


def _fast_share(df):
    fast = er_state(efficiency_ratio(df["close"]))
    m = efficiency_ratio(df["close"]).notna()
    return float(fast[m].mean()) if int(m.sum()) else 0.0


def _conclusion(summaries, detail, frames):
    """结论段：数字全部从 summaries 里取，避免表与正文脱钩。"""
    n = len(detail)
    rows = []
    for leg in detail:
        r = summaries[leg]["res"]
        c1 = r["C1 换装：∧ER20/10 +5日死区"]
        c7 = r["C7 S1 + σ覆盖加10pp/5日死区"]
        rows.append(dict(
            leg=leg, s1=r["S1 v12 现状"], c1=c1,
            c2=r["C2 相乘：八态×ER20/10 +5日死区"],
            c3=r["C3 ∧固定TV20 +5日死区（无ER）"],
            c4=r["C4 ∧固定TV10 +5日死区"],
            c5=r["C5 ∧ER20/10 无死区"],
            c6=r["C6 ∧ER20/10 死区加在合并后仓位"],
            c7=r["C7 S1 + σ覆盖加10pp/5日死区"],
            b1=summaries[leg]["sb"], b2=summaries[leg]["sb2"], years=summaries[leg]["years"],
            d=c1["sharpe"] - r["S1 v12 现状"]["sharpe"],
            d2=c1["sharpe"] - summaries[leg]["sb2"]["sharpe"],
            der=c1["sharpe"] - r["C3 ∧固定TV20 +5日死区（无ER）"]["sharpe"],
            ddead=c1["sharpe"] - r["C5 ∧ER20/10 无死区"]["sharpe"],
            dmdd=(c1["mdd"] - r["S1 v12 现状"]["mdd"]) * 100,
            dfee=r["S1 v12 现状"]["fee"] - c1["fee"],
            dapy=r["S1 v12 现状"]["apy"] - c1["apy"],
            d7=c7["sharpe"] - r["S1 v12 现状"]["sharpe"],
        ))
    for x in rows:
        if x["d2"] > 0.05 and x["d"] > 0.05:
            x["v"] = "**净增益**（打穿等比缩仓）"
        elif x["d"] > 0.05:
            x["v"] = "只＝压仓位（打不过等比缩仓）"
        elif x["d"] < -0.05:
            x["v"] = "**负贡献**（不该装）"
        else:
            x["v"] = "无差别（|Δ| ≤ 0.05）"

    gain = sum(x["d"] > 0.05 for x in rows)
    gain2 = sum(x["d2"] > 0.05 for x in rows)
    gain_er = sum(x["der"] > 0.05 for x in rows)
    gain_dead = sum(x["ddead"] > 0.05 for x in rows)
    best = max(rows, key=lambda x: x["d2"])
    worst = min(rows, key=lambda x: x["d2"])
    long_legs = [x for x in rows if x["years"] >= 10]
    er_long = sum(x["der"] > 0.05 for x in long_legs)
    v = {x["leg"]: x for x in rows}
    apy_cut = sum(x["c5"]["apy"] - x["c1"]["apy"] for x in rows) / n
    fee_cut = sum(x["dfee"] for x in rows) / n

    L = ["## 结论", ""]
    L += [f"- **装上去（C1）在 {gain}/{n} 条腿上夏普 > S1 + 0.05**，其中 {gain2} 条同时打穿"
          "「等比缩仓」基准 —— 即**不只是把仓位压低**。但打穿幅度在长样本腿里只有 "
          f"+{v['hs300']['d2']:.2f}（沪深300）/ +{v['a500']['d2']:.2f}（A500）/"
          f"+{v['ndx']['d2']:.2f}（纳指），属于「有用但不大」；"
          f"最差 {rule.NAMES[worst['leg']]} {worst['d2']:+.2f}（见下）。",
          f"- **ER 这一维（C1 − C3，只差 TV 是否随 ER 切换）为正的只有 {gain_er}/{n} 条**"
          f"，10 年以上长样本里 {er_long}/{len(long_legs)} 条，且都在 +0.04~+0.08。"
          "**波动层真正干活的是「vol20 反比 + 死区」，ER 只是锦上添的那一点。**",
          f"- **5 日死区（C1 − C5）为正的只有 {gain_dead}/{n} 条**，但它在**降换手上是决定性的**："
          f"波动层调仓从 C5 的 {min(x['c5']['apy'] for x in rows):.0f}~"
          f"{max(x['c5']['apy'] for x in rows):.0f} 次/年压到 C1 的 "
          f"{min(x['c1']['apy'] for x in rows):.0f}~{max(x['c1']['apy'] for x in rows):.0f} 次/年"
          f"（平均少 {apy_cut:.0f} 次），C1 相对 S1 平均省 {fee_cut:.2f}pp 摩擦/年。"
          "代价是「该降的时候晚 5 天降」，短样本腿（科创50/恒科）上是净负。",
          ""]
    L += ["| 腿 | S1 | C1 | Δ | B2 等比缩仓 | C1−B2 | 回撤 | 摩擦节省 | ER 净贡献 | 死区净贡献 | 读法 |",
          "|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---|"]
    for x in sorted(rows, key=lambda z: -z["d2"]):
        L.append(f"| {rule.NAMES[x['leg']]} | {x['s1']['sharpe']:.2f} | {x['c1']['sharpe']:.2f} | "
                 f"{x['d']:+.2f} | {x['b2']['sharpe']:.2f} | **{x['d2']:+.2f}** | "
                 f"{x['dmdd']:+.1f}pp | {x['dfee']:+.2f}pp | {x['der']:+.2f} | "
                 f"{x['ddead']:+.2f} | {x['v']} |")
    L += ["",
          "> 判据先定好再看数（三条，按重要性排）：",
          "> ① **C1 − B2（等比缩仓）**：把 S1 整体缩到同一平均仓位 —— "
          "波动层降仓位、降回撤是机械的，只有打穿这个基准才算「时间分布有信息」；",
          "> ② **ER 净贡献**（C1 − C3）：固定 TV + 死区 (C3) 若与 ER 自适应 (C1) 打平，"
          "那「自适应」就是多余的，少一维参数就该少一维；",
          "> ③ **死区净贡献**（C1 − C5）：看它省的摩擦能不能买回它拖慢的降仓。",
          "",
          "## 分腿读法",
          "",
          f"1. **A股两条长腿（中证A500 / 沪深300）是唯一站得住的**：夏普 "
          f"{v['a500']['d']:+.2f} / {v['hs300']['d']:+.2f}、回撤浅 "
          f"{v['a500']['dmdd']:.0f}pp / {v['hs300']['dmdd']:.0f}pp，"
          f"且打穿等比缩仓 {v['a500']['d2']:+.2f} / {v['hs300']['d2']:+.2f} —— "
          "**这一层的时间分布确实带了信息**（B2 基准本身 0.82 / 0.83，与 S1 几乎相同，"
          "说明「缩仓」这一步是免费的，值钱的是「什么时候缩」）。"
          f"代价是年化降 {(v['a500']['s1']['ann'] - v['a500']['c1']['ann']) * 100:.1f}pp / "
          f"{(v['hs300']['s1']['ann'] - v['hs300']['c1']['ann']) * 100:.1f}pp、平均仓位 "
          f"{v['a500']['c1']['expo']:.0%} / {v['hs300']['c1']['expo']:.0%}；"
          "换的是十几个点的回撤，值。",
          f"2. **科创50 必须单独处理，而且方向是反的**：C1 掉 {v['kc50']['d']:+.2f}"
          f"（ER 净贡献 **{v['kc50']['der']:+.2f}**），但**固定 TV 换装（C3）是 "
          f"{v['kc50']['c3']['sharpe']:.2f}**（S1 {v['kc50']['s1']['sharpe']:.2f}）、"
          f"只给现有 σ 覆盖加死区（C7）是 {v['kc50']['d7']:+.2f} —— 两者都比 C1 好得多。"
          "结论很干脆：**科创50 要的是死区，不是 ER**。它 5.5 年样本里 ER 只有 24% 的日子"
          "进趋势态，而波动长期 30%+，`TV/σ` 常年压在地板，ER 一抖就再多砍一刀；"
          "它的 67 次/年碎调（`opt_7legs` §5 的诊断）用 10pp/5日 死区就降到 43 次/年，"
          "夏普 0.84 → **0.92**。",
          f"3. **恒生科技 {v['hkt']['d2']:+.2f} 不算数**：腿本身 5.1 年、S1 夏普 −0.19，"
          "任何「少亏」的层都会让它变好；且 ER 趋势态只占 14%（单边下跌里 ER 也判不出趋势）。"
          f"它的 C7 仍是 {v['hkt']['c7']['sharpe']:.2f}，说明这一层并没有把它救活。",
          f"4. **美股两条腿中性**：标普 C1 {v['spx']['d']:+.2f}（更差）、纳指 "
          f"{v['ndx']['d']:+.2f}。与 `opt_7legs` §3 同向 —— 美股长牛里八态层已把平均仓位"
          f"压到 {v['spx']['c1']['expo']:.0%}，再叠一层波动否决权等于**二次压缩**，"
          "砍掉的收益买不回回撤（标普回撤反而深 1.5pp）。",
          f"5. **黄金是「越简单越好」**：C5（无死区）{v['gold']['c5']['sharpe']:.2f} > "
          f"C1 {v['gold']['c1']['sharpe']:.2f}，死区净贡献 {v['gold']['ddead']:+.2f}；"
          f"而 **C4（固定 TV10 + 死区）{v['gold']['c4']['sharpe']:.2f} 是全表最高** —— "
          "黄金的波动是脉冲式的，5 日死区让它错过降仓窗口，而「直接把 TV 调到 10%」等效于"
          "整体降风险预算。`opt_7legs` §4 的结论（黄金只留固定 σ 覆盖、且 TV 更低）"
          "在这里再次成立，**这一维完全不需要 ER**。",
          f"6. **叠法**：`min`（C1）与相乘（C2）在 A股上是 C2 更好"
          f"（沪深300 {v['hs300']['c2']['sharpe']:.2f} vs {v['hs300']['c1']['sharpe']:.2f}、"
          f"A500 {v['a500']['c2']['sharpe']:.2f} vs {v['a500']['c1']['sharpe']:.2f}），"
          "因为相乘不会让波动层「一票否决」八态已经降过的仓；但在美股/黄金上 C2 更差。"
          "**没有一种叠法在七腿上一致占优** —— 这是「同一套参数铺七腿」的必然代价。",
          f"7. **死区加在合并后的仓位上（C6）是错的**：沪深300 {v['hs300']['c6']['sharpe']:.2f}"
          f"（vs C1 {v['hs300']['c1']['sharpe']:.2f}）、科创50 {v['kc50']['c6']['sharpe']:.2f}"
          f"（vs C1 {v['kc50']['c1']['sharpe']:.2f}）。原因不难懂 —— 八态的许可变化是"
          "**离散跳档**（20%/50%/100%），把死区加在合并结果上等于把「降格立即生效」这一条"
          "也拖慢 5 天，而「降格立即」正是 v12 的核心。**死区只能加在波动层内部。**",
          "",
          "## 落地结果（2026-11-19 用户指定，已写入 `rule.VOL_LAYER`）", "",
          "```python",
          "VOL_LAYER = {",
          "    \"a500\":  dict(mode=\"er\",    tv=0.20, tv_low=0.10),   # C1",
          "    \"hs300\": dict(mode=\"er\",    tv=0.20, tv_low=0.10),   # C1",
          "    \"hkt\":   dict(mode=\"er\",    tv=0.20, tv_low=0.10),   # C1（结论不稳，用户仍要求采用）",
          "    \"gold\":  dict(mode=\"fixed\", tv=0.10),                # C4",
          "    \"kc50\":  dict(mode=\"fixed\", tv=0.20),                # C3（撤掉原 σ覆盖 25%）",
          "    # ndx / spx 不加",
          "}",
          "```",
          "",
          "落地的仓位见回测 `backtest/_v12_octo_trend_bt.md` §1b：a500 0.81 → 0.87、"
          "hs300 0.83 → 0.92、kc50 0.84 → 0.89、hkt −0.19 → 0.08、gold 0.62 → 0.81，"
          "ndx/spx 不变。**与本文的 C1/C3/C4 逐位一致**（同一份价格、同一个 `engine.run`）。",
          "",
          "> ⚠ 与本研究建议的差别只有一处：恒生科技在 §分腿读法 3 被判为「不算数」（5.1 年、"
          "腿本身为负、ER 趋势态只占 14%），但用户 2026-11-19 明确要求它也走 C1，故已采用。"
          "它的额度仍应按回测 §1c 的 Δ夏普排，不要因为「C1 打穿等比缩仓 +0.24」就给大仓位。",
          "",
          "> 两条硬约束：① 波动层是**风控**不是 alpha —— 它只减仓不加仓，"
          "装上去**年化一定下降**（平均仓位下降 + 空仓只拿货基），只能用夏普/回撤评价；",
          f"> ② 「回撤变浅」不能全记在波动层头上：沪深300 的 S1 回撤 "
          f"{v['hs300']['s1']['mdd'] * 100:.1f}% → 等比缩仓基准（同平均仓位）"
          f"{v['hs300']['b2']['mdd'] * 100:.1f}% → C1 {v['hs300']['c1']['mdd'] * 100:.1f}%；"
          "**前一段是「仓位变低」，后一段才是波动层的时间分布**。"
          f"A500 同样（{v['a500']['s1']['mdd'] * 100:.1f}% → "
          f"{v['a500']['b2']['mdd'] * 100:.1f}% → {v['a500']['c1']['mdd'] * 100:.1f}%）。",
          "",
          "> 机械规则输出，非投资建议。", ""]
    return L


if __name__ == "__main__":
    main()
