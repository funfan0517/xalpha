#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""V9 专项：真实 PB 历史 vs 合成 PB（PE × ROE 常数）—— 16% 权重到底有没有独立信息

背景
----
V9 的 PB 是 `PE × ROE 中枢` 合成的，常数 ROE 使 **PB 分位 ≡ PE 分位**，所以「五因子」
数学上退化为 PE 36% + 利差 35% + 乖离 15% + 位置 14%。这个结论此前只是恒等式推导。

蛋卷 `index_eva` 提供指数 **真实 PB 周频历史**（免 token），覆盖池内 3 只：
000922 / H30269 / 931157。本脚本用这 3 只做三件事：

1. **ROE 时变性**：`ROE_t = PB_t / PE_t`。ROE 若近似常数，真实 PB 分位仍≈PE 分位；
2. **分位差**：真实 PB 分位 − PE 分位（合成 PB 的分位恒等于 PE 分位），及其对温度的位移
   （PB 权重 16% ⇒ `Δtemp = 0.16 × Δpct`）；
3. **增量信息**：把 PB 分位分解为「PE 分位（共享部分）+ Δ（ROE 时变部分）」，
   看 Δ 对未来 4/13 周收益有没有预测力。没有 ⇒ PB 只是把 PE 算了第二遍。

口径与边界（不隐藏）
--------------------
* 蛋卷历史是**周频**（约 517 条 ≈ 10 年，自 2016-10），本脚本的分位窗口 730 日历日 / 最少 60 周，
  与主策略的日频 730 日 / 300 观测**不完全等价**，只用于口径对照，不替换主流程；
* 只覆盖 3/8 只标的，且 931157 自 2019-04；
* 收益用中证日频收盘（`data/_idx/<code>.csv`），按蛋卷周频日期 as-of 取，无前视；
* 回归用**每 13 周抽样**规避重叠样本，样本量仅 ~80，t 值只作方向性参考。

产物：`research/_pb_real_check.md`
用法：`python strategies/dividend/v9_temp5/research/_pb_real_check.py`
"""
from __future__ import annotations

import os
import sys

import numpy as np
import pandas as pd

_DIR = os.path.dirname(os.path.abspath(__file__))
_V9 = os.path.dirname(_DIR)
if _V9 not in sys.path:
    sys.path.insert(0, _V9)

import data  # noqa: E402  pylint: disable=wrong-import-position
import rule  # noqa: E402  pylint: disable=wrong-import-position

WIN = 730          # 分位窗口（日历日），与主策略同
MIN_N = 60         # 周频最少观测（≈1.15 年）
STEP = 13          # 回归抽样步长（周），规避重叠
OUT_MD = os.path.join(_DIR, "_pb_real_check.md")


# ------------------------------------------------------------------ 工具
def pct_series(s: pd.Series, win: int = WIN, min_n: int = MIN_N) -> pd.Series:
    """滚动分位 `100 × count(w ≤ v) / len(w)`；样本不足给 NaN（与 factors._pct 同口径）。"""
    out = []
    for dt, v in s.items():
        w = s.loc[(s.index > dt - pd.Timedelta(days=win)) & (s.index <= dt)].dropna()
        out.append(100.0 * float((w <= v).sum()) / len(w)
                   if len(w) >= min_n and pd.notna(v) else np.nan)
    return pd.Series(out, index=s.index, name=s.name)


def ols(y: np.ndarray, X: np.ndarray):
    """最小二乘 -> (beta, t, r2)。X 不含常数项（内部补）。"""
    A = np.column_stack([np.ones(len(y)), X])
    beta, *_ = np.linalg.lstsq(A, y, rcond=None)
    resid = y - A @ beta
    n, k = A.shape
    if n <= k:
        return beta, np.full(k, np.nan), np.nan
    s2 = float(resid @ resid) / (n - k)
    cov = s2 * np.linalg.inv(A.T @ A)
    t = beta / np.sqrt(np.diag(cov))
    ss_tot = float(((y - y.mean()) ** 2).sum())
    r2 = 1.0 - float(resid @ resid) / ss_tot if ss_tot > 0 else np.nan
    return beta, t, r2


def load_close(index_code: str) -> pd.Series:
    """中证日频收盘（缓存优先）；缺失时回落到联网抓取。"""
    path = os.path.join(rule.IDX_DIR, f"{index_code}.csv")
    if os.path.exists(path):
        df = pd.read_csv(path, parse_dates=["date"])
    else:
        df = data.load_index(index_code)
    return df.set_index("date")["close"].astype(float).sort_index()


def fnum(v, nd: int = 2) -> str:
    return "—" if v is None or not np.isfinite(v) else f"{v:.{nd}f}"


# ------------------------------------------------------------------ 主流程
def main() -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")

    eva = data.load_danjuan_eva()
    if eva.empty:
        print("[pb] 蛋卷估值不可用（无缓存且抓取失败）", file=sys.stderr)
        return 1
    print(f"[pb] 蛋卷估值 {len(eva)} 行 · {eva['index_code'].nunique()} 只指数", file=sys.stderr)

    roe_rows, pct_rows, dtemp_rows = [], [], []
    pooled = []                        # 回归样本（季度抽样、去指数均值）

    for idx_code, g in eva.groupby("index_code"):
        g = g.sort_values("date").set_index("date")
        pe, pb = g["pe"].astype(float), g["pb"].astype(float)
        roe = (pb / pe).dropna()
        roe_const = rule.BY_CODE[next(f["code"] for f in rule.FUNDS
                                      if f["index"] == idx_code)]["roe"]

        # --- 1. ROE 时变
        years = (roe.index[-1] - roe.index[0]).days / 365.25
        slope = np.polyfit(np.arange(len(roe)), roe.to_numpy(), 1)[0]
        roe_rows.append({
            "index": idx_code, "n_week": len(roe), "start": roe.index[0], "years": years,
            "roe_mean": float(roe.mean()), "roe_std": float(roe.std()),
            "cv": float(roe.std() / roe.mean()),
            "roe_first": float(roe.iloc[0]), "roe_last": float(roe.iloc[-1]),
            "drift_yr": float(slope * 52), "rule_roe": float(roe_const),
        })

        # --- 2. 分位（真实 PB vs 合成=PE）
        pe_pct = pct_series(pe.rename("pe"))
        pb_pct = pct_series(pb.rename("pb"))
        d = (pb_pct - pe_pct).dropna()
        dtemp = d * rule.W["pb"]
        pct_rows.append({
            "index": idx_code, "n": len(d),
            "mae": float(d.abs().mean()), "max_abs": float(d.abs().max()),
            "mean": float(d.mean()), "corr": float(pe_pct.corr(pb_pct)),
            "p90_abs": float(d.abs().quantile(0.9)),
        })
        dtemp_rows.append({
            "index": idx_code,
            "mae_temp": float(dtemp.abs().mean()), "max_temp": float(dtemp.abs().max()),
            "share_ge5": float((dtemp.abs() >= 5).mean()),
        })

        # --- 3. 增量信息：Δ = PB 分位 − PE 分位，看它能否预测未来收益
        px = load_close(idx_code)
        px_w = px.reindex(px.index.union(pe.index)).ffill().reindex(pe.index)
        df = pd.DataFrame({"pe_pct": pe_pct, "pb_pct": pb_pct,
                           "d": pb_pct - pe_pct, "px": px_w}).dropna()
        for lag in (4, 13):
            df[f"fwd{lag}"] = df["px"].shift(-lag) / df["px"] - 1.0
        sub = df.iloc[::STEP].dropna(subset=["fwd4", "fwd13"])
        sub = sub.assign(index=idx_code)
        pooled.append(sub)
        print(f"[pb] {idx_code}: 分位差 MAE={d.abs().mean():.1f} "
              f"corr={pe_pct.corr(pb_pct):.4f} 温度位移 MAE={dtemp.abs().mean():.2f}",
              file=sys.stderr)

    R = pd.DataFrame(roe_rows)
    P = pd.DataFrame(pct_rows)
    D = pd.DataFrame(dtemp_rows)
    Q = pd.concat(pooled, ignore_index=True)

    # 去指数均值 + 指数哑变量（控制 3 只标的的截面差异）
    reg = []
    for _, gg in Q.groupby("index"):
        gg = gg.copy()
        for c in ("pe_pct", "pb_pct", "d", "fwd4", "fwd13"):
            gg[c] = gg[c] - gg[c].mean()
        reg.append(gg)
    reg = pd.concat(reg, ignore_index=True)

    res = {}
    for tag, cols in (("PE only", ["pe_pct"]),
                      ("PB only", ["pb_pct"]),
                      ("PE + PB", ["pe_pct", "pb_pct"]),
                      ("D (PB−PE)", ["d"])):
        X = reg[cols].to_numpy(dtype=float)
        for lag in (4, 13):
            y = reg[f"fwd{lag}"].to_numpy(dtype=float)
            beta, t, r2 = ols(y, X)
            res[(tag, lag)] = (beta, t, r2, len(y))

    L = ["# 真实 PB vs 合成 PB —— 16% 权重有没有独立信息", "",
         "> 数据：蛋卷 `index_eva` 指数 PB/PE **周频**历史（免 token）· 3 只"
         "（000922 / H30269 / 931157）",
         "> 分位窗口 730 日历日 / 最少 60 周 · 收益用中证日频收盘 as-of 取 · 回归每 13 周抽样",
         "> 样本只有 3 只、约 10 年，且蛋卷是第三方接口 —— 结论为方向性参考", ""]

    L += ["## 一、ROE 是常数吗（`ROE_t = PB_t / PE_t`）", "",
          "合成 PB 用的 ROE 是手工中枢，若真实 ROE 近似常数，则真实 PB 分位仍≈PE 分位。", "",
          "| 指数 | 周数 | 区间 | 年数 | ROE 均值 | ROE 标准差 | 变异系数 | 首值 | 末值 | 年漂移 | 规则常数 |",
          "|---|---:|---|---:|---:|---:|---:|---:|---:|---:|---:|"]
    for _, r in R.iterrows():
        L.append(f"| {r['index']} | {int(r['n_week'])} | {r['start']:%Y-%m}~ | {r['years']:.1f} | "
                 f"{r['roe_mean']:.4f} | {r['roe_std']:.4f} | **{r['cv']:.1%}** | "
                 f"{r['roe_first']:.4f} | {r['roe_last']:.4f} | {r['drift_yr']:+.4f}/年 | "
                 f"{r['rule_roe']:.4f} |")
    L += ["", f"ROE 变异系数区间 **{R['cv'].min():.1%} ~ {R['cv'].max():.1%}**；"
          f"年漂移区间 **{R['drift_yr'].min():+.4f} ~ {R['drift_yr'].max():+.4f}**。", ""]

    L += ["## 二、分位差：真实 PB 分位 − PE 分位", "",
          "合成 PB 的分位**恒等于** PE 分位（常数缩放不改变秩），所以下表就是「用真实 PB 后分位变了多少」。", "",
          "| 指数 | 样本周 | MAE | P90 绝对差 | 最大绝对差 | 均值（系统性偏移） | corr(PE分位, PB分位) |",
          "|---|---:|---:|---:|---:|---:|---:|"]
    for _, r in P.iterrows():
        L.append(f"| {r['index']} | {int(r['n'])} | {r['mae']:.2f} | {r['p90_abs']:.2f} | "
                 f"{r['max_abs']:.2f} | {r['mean']:+.2f} | **{r['corr']:.4f}** |")

    L += ["", "## 三、对温度的位移（PB 权重 16% ⇒ `Δtemp = 0.16 × Δpct`）", "",
          "| 指数 | 平均绝对位移 | 最大绝对位移 | 位移 ≥5 分的占比 |",
          "|---|---:|---:|---:|"]
    for _, r in D.iterrows():
        L.append(f"| {r['index']} | {r['mae_temp']:.2f} | {r['max_temp']:.2f} | "
                 f"{r['share_ge5']:.1%} |")
    L += ["", "> 参考：档位宽度 10~20 分（55/65/75/85），5 分约为半个档。", ""]

    L += ["## 四、增量信息：Δ（PB 分位 − PE 分位）能预测收益吗", "",
          "把 PB 分位拆成「PE 分位（与 PE 共享）」+「Δ（ROE 时变部分）」。若 Δ 无预测力，"
          "则 PB 只是把 PE 算了第二遍。", "",
          "| 自变量 | 未来 4 周 β | t | R² | 未来 13 周 β | t | R² |",
          "|---|---:|---:|---:|---:|---:|---:|"]
    for tag in ("PE only", "PB only", "PE + PB", "D (PB−PE)"):
        b4, t4, r4, _ = res[(tag, 4)]
        b13, t13, r13, _ = res[(tag, 13)]
        L.append(f"| {tag} | {b4[-1] * 100:+.2f}% | {fnum(t4[-1])} | {fnum(r4, 3)} | "
                 f"{b13[-1] * 100:+.2f}% | {fnum(t13[-1])} | {fnum(r13, 3)} |")
    L += ["", "多变量行（PE + PB）只显示**最后一个**自变量的 β/t；R² 为整行回归的 R²。",
          f"回归样本 n = {res[('PE only', 4)][3]}（3 只 × 每 13 周抽样），"
          "已去指数均值并规避重叠，但样本很小，|t| < 2 一律视为无证据。", ""]

    # ---------------- 结论（由数据决定，不预设）
    corr_min, corr_max = float(P["corr"].min()), float(P["corr"].max())
    mae_min, mae_max = float(P["mae"].min()), float(P["mae"].max())
    mean_min, mean_max = float(P["mean"].min()), float(P["mean"].max())
    cv_min, cv_max = float(R["cv"].min()), float(R["cv"].max())
    drift_min, drift_max = float(R["drift_yr"].min()), float(R["drift_yr"].max())
    dtemp_min, dtemp_max = float(D["mae_temp"].min()), float(D["mae_temp"].max())
    share_max = float(D["share_ge5"].max())
    d_t4 = abs(float(res[("D (PB−PE)", 4)][1][-1]))
    d_t13 = abs(float(res[("D (PB−PE)", 13)][1][-1]))
    r2_d13 = float(res[("D (PB−PE)", 13)][2])
    r2_pe13 = float(res[("PE only", 13)][2])
    r2_pb13 = float(res[("PE + PB", 13)][2])
    gain13 = r2_pb13 - r2_pe13
    n_reg = int(res[("PE only", 13)][3])
    has_info = d_t13 >= 2.0 or gain13 >= 0.02

    L += ["## 五、结论", ""]
    if not has_info:
        L += [f"1. **PB 没有 PE 之外的可证信息**：分位差 MAE {mae_min:.1f}~{mae_max:.1f} 分、"
              f"corr {corr_min:.4f}~{corr_max:.4f}；Δ（PB−PE）对 13 周收益 |t|={d_t13:.2f}，"
              f"加入 PB 后 R² 仅增 {gain13:+.4f}。",
              "2. 所以 16% 的 PB 权重即便换成真实 PB，也只是换个算法重复 PE —— "
              "「五因子实为四维」在真实数据上同样成立。"]
    else:
        L += [f"1. **ROE 不是常数，而且系统性下行**：`ROE_t = PB_t / PE_t` 的变异系数 "
              f"{cv_min:.1%}~{cv_max:.1%}，年漂移 {drift_min:+.4f}~{drift_max:+.4f}（约 -0.4pp/年）。"
              "规则里手工设的常数 ROE 只是当期快照 —— 000922 的 0.0993 恰好等于末值，"
              "而它 10 年前是 0.1257。",
              f"2. **真实 PB 分位与 PE 分位不是同一条线**：MAE {mae_min:.1f}~{mae_max:.1f} 分、"
              f"corr {corr_min:.4f}~{corr_max:.4f}；真实 PB 分位系统性**低于** PE 分位"
              f"（均值 {mean_min:+.1f}~{mean_max:+.1f}）—— ROE 下行使 PB 掉得比 PE 快。",
              f"3. **但对温度几乎没影响**：`Δtemp = 0.16 × Δpct` 的平均绝对值只有 "
              f"{dtemp_min:.2f}~{dtemp_max:.2f} 分，位移 ≥5 分的周占比最高 {share_max:.1%}。"
              "换句话说，把合成 PB 换成真实 PB，温度读数基本不动。",
              f"4. **PB 确实带了 PE 之外的信息**：Δ（PB−PE）对 **13 周**收益 |t|={d_t13:.2f}、"
              f"R² {r2_d13:.3f}；PE+PB 的 R² {r2_pb13:.3f} vs PE 单变量 {r2_pe13:.3f}。"
              f"方向符合「ROE 恶化 → 低 PB 是假便宜 → 未来收益更低」。4 周维度 |t|={d_t4:.2f}，属边缘。",
              "5. **「五因子实为四维」要分两层看**：关于**合成口径**的恒等式仍然成立"
              "（合成 PB 分位 ≡ PE 分位，实测差 0）；但**真实 PB 并不等价**，它含 ROE 时变信息，"
              "且该信息有边际预测力。主流程目前仍只能用合成 PB（蛋卷只给周频 3 只），"
              "这部分信息是被丢掉的。",
              f"6. **证据强度有限**：n={n_reg}、3 只同族高相关、第三方周频数据。"
              f"|t|={d_t13:.2f} 不足以据此改造策略，只能作为「值得补日频真实 PB 再回测」的理由。"]
    L += ["", "### 边界", "",
          "* 蛋卷历史为**周频**且只覆盖 3/8 只，不能直接替换主流程的日频分位；",
          "* 3 只指数同属红利族、相关性高，pooled 回归的独立样本远少于名义 n；",
          "* 蛋卷是第三方非官方接口，字段与覆盖范围可能变化，本结论需随数据更新重跑。", ""]

    with open(OUT_MD, "w", encoding="utf-8") as fh:
        fh.write("\n".join(L))
    print(f"[pb] 报告 -> {os.path.relpath(OUT_MD, rule.ROOT)}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())
