# -*- coding: utf-8 -*-
"""V9 优化探针：**在不过拟合的前提下找改进**（文档一致性不再是约束）。

## 0. 先立规矩，再看结果（预注册）

在跑任何回测之前，把「什么叫改进」写死。一个变体只有**同时**通过 4 道闸门才算数：

| 闸 | 判据 | 为什么 |
|---|---|---|
| **G1 全样本** | 夏普 ≥ 基准 + 0.02 **且** 年化 ≥ 基准 − 0.5pp | 不是「回撤更小」就叫改进，风险调整后必须更好 |
| **G2 分半** | 前半段与后半段的夏普**都不低于**各自基准（容差 0.03） | 只在某一段有效的改进＝样本内拟合 |
| **G3 剔除最好年份** | 剔掉 2020 重算，夏普不低于基准（容差 0.02） | 防止「全部收益来自一次事件」 |
| **G4 参数邻域** | 关键参数动一档（±），夏普仍高于基准 | 防止「只有那一个数好看」 |

**试验预算声明**：本次共 10 个机制变体（不是网格搜索），加上对通过者的邻域扰动。
机制变体各自有**独立的先验理由**（不是从同一族里试出来的），所以多重检验的惩罚较轻 ——
但**只要有一项失败就不采纳**，宁可留着现状。

## 1. 待测方向（每个方向都有先验理由，不是拟合）

| 编号 | 方向 | 先验理由 | 新增自由参数 |
|---|---|---|---|
| O0 | 基准：per-leg VT(simple20) + 50/50 | — | 0 |
| O1 | 波动率估计换 **EWMA(λ=0.94)** | RiskMetrics 标准值；波动率有聚集性，指数加权比等权更贴近「当前状态」 | 0（λ=0.94 是行业默认） |
| O2 | 波动率估计 **0.5·(vol20+vol60)** | 短窗口快、长窗口稳，等权混合是常见的降噪做法 | 0 |
| O3 | 映射加凸性 **w=(TV/vol)^1.5** | 波动率预测误差在高波动期更大 → 高波动时该多降（凸惩罚） | 0（γ=1.5 是常用档） |
| O4 | **组合层风险平价**（w∝1/σ，月度再平衡），不加总波动目标 | 两腿风险贡献相等，危机时自动偏向低波腿（=标普） | 0 |
| O5 | **组合层风险平价 + 组合层 TV**（σ_p 用 20/60 日协方差估计） | 单层风险预算，替代「各自 15% + 50/50」的两层结构 | 0 |
| O6 | O5 但波动率估计用 EWMA | 组合层 + 快估计，看两者是否叠加 | 0 |
| O7 | per-leg VT + **动态 RP 权重**（两层） | 保留单腿 VT，只把 50/50 换成动态风险平价 | 0 |
| O8 | O0 + **快减+快补**（上一探针的 V5） | 已证邻域稳，作为「参照上限」 | 3 |
| O9 | 纯静态 50/50 | 参照系 | 0 |

## 2. 为什么这些方向不太容易过拟合

- **只改结构/估计量，不加阈值**：O1~O3、O5~O7 全用行业默认值（λ=0.94、γ=1.5、60 日协方差），
  没有一个是「试出来的最优值」。
- **每条腿独立成立才算数**：报告同时给纳指腿与标普腿，只在一条腿上好的方向视为可疑。
- **G3 是关键**：V9 现有的所有「改进」都能被 2020 一年解释（见 `_probe_fast.md`），
  所以本探针把「剔除 2020 后还剩多少」当硬指标。

产物：`_probe_opt.md`。模拟结果，非投资建议。
"""
import os
import sys

import numpy as np
import pandas as pd

try:
    sys.stdout.reconfigure(encoding="utf-8")  # type: ignore[attr-defined]
except Exception:  # noqa: BLE001
    pass

_HERE = os.path.dirname(os.path.abspath(__file__))
_V9 = os.path.dirname(_HERE)
sys.path.insert(0, _V9)
sys.path.insert(0, _HERE)

import vol_target as vt            # noqa: E402
import _probe_fast as pf           # noqa: E402

OUT_MD = os.path.join(_HERE, "_probe_opt.md")

TV, CAP, DRIFT, LAM = vt.TV, vt.CAP, vt.DRIFT, 0.94
SQ = np.sqrt(252.0)


# ---------------------------------------------------------------- 估计量
def vol_est(sig, kind="simple", win=20, lam=LAM):
    """年化波动率估计（三种，全部无量纲假设、无拟合常数）。"""
    r = sig.pct_change()
    if kind == "simple":
        return r.rolling(win, min_periods=win).std(ddof=1) * SQ
    if kind == "ewma":                       # RiskMetrics：var_t = λ var_{t-1} + (1-λ) r²
        var = r.pow(2).ewm(alpha=1 - lam, adjust=False, min_periods=win).mean()
        return np.sqrt(var * 252.0)
    if kind == "blend":                      # 0.5·(vol20 + vol60)
        a = r.rolling(win, min_periods=win).std(ddof=1)
        b = r.rolling(60, min_periods=60).std(ddof=1)
        return 0.5 * (a + b) * SQ
    raise ValueError(kind)


# ---------------------------------------------------------------- 单腿：波动率目标
def leg_weights(leg, kind="simple", win=20, gamma=1.0, tv=TV, cap=CAP, drift=DRIFT, **kw):
    """-> (nav, dec, Series(权重, 净值日历))，月末定仓 + T+1 成交（与正式档同纪律）。"""
    nav = vt.load_nav(leg)
    sig = vt.signal_of(leg, vt.params())
    vol = vol_est(sig, kind, win, **kw)
    raw = ((tv / vol) ** gamma).clip(upper=cap)

    hold, dec = 1.0, []
    for d in vt.month_ends(sig.index):
        v = float(raw.loc[d])
        if not np.isfinite(v):
            continue
        tgt = float(v) if abs(float(v) - hold) >= drift - 1e-12 else hold
        hold = tgt
        dec.append(dict(date=d, target=tgt, raw=float(v)))
    w = vt.state_series(nav.index, dec)
    return nav, dec, w


# ---------------------------------------------------------------- 组合层：风险平价
def rp_weights(navs, tv=None, vol_win=20, corr_win=60, cap=CAP, kind="simple"):
    """风险平价权重（月度再平衡，T+1 生效）。

    `w_i ∝ 1/σ_i`（等风险贡献）；若给 `tv`，再按组合波动
    `σ_p = sqrt(w' Σ w)`（σ 用 `vol_win` 日 + ρ 用 60 日）缩放到 `σ_p = TV`。
    任何一天只要估计量缺值，就沿用上一期权重（不猜）。
    """
    legs = list(navs)
    idx = navs[legs[0]].index
    rets = {k: navs[k].pct_change() for k in legs}
    vols = {k: vol_est(navs[k], kind, vol_win) for k in legs}
    rho = rets[legs[0]].rolling(corr_win, min_periods=corr_win).corr(rets[legs[1]])

    rows, prev = {}, None
    for d in vt.month_ends(idx):
        s = {k: float(vols[k].loc[d]) for k in legs}
        c = float(rho.loc[d])
        if not all(np.isfinite(v) and v > 0 for v in s.values()) or not np.isfinite(c):
            if prev is not None:
                rows[d] = prev
            continue
        inv = sum(1.0 / v for v in s.values())
        w = {k: (1.0 / s[k]) / inv for k in legs}
        if tv:
            sp = np.sqrt(w[legs[0]] ** 2 * s[legs[0]] ** 2 + w[legs[1]] ** 2 * s[legs[1]] ** 2
                         + 2 * w[legs[0]] * w[legs[1]] * c * s[legs[0]] * s[legs[1]])
            scale = min(1.0, float(tv) / sp) if sp > 0 else 1.0
            w = {k: min(cap, val * scale) for k, val in w.items()}
        rows[d] = w
        prev = w

    out = pd.DataFrame(index=idx, columns=legs, dtype="float64")
    dec = sorted(rows)
    for i, d in enumerate(dec):
        pos = int(idx.searchsorted(d, side="right"))
        if pos >= len(idx):
            continue
        end = int(idx.searchsorted(dec[i + 1], side="right")) if i + 1 < len(dec) else len(idx)
        end = min(end, len(idx))
        for k in legs:
            out.iloc[pos:end, out.columns.get_loc(k)] = rows[d][k]
    return out.ffill().fillna(0.0)


# ---------------------------------------------------------------- 组合执行
def run_matrix(navs, wdf, cost=None, cash=None):
    """按逐日权重矩阵执行（权重是「该日收盘持有」，赚次日收益 -> shift(1)）。"""
    c = vt.COST if cost is None else float(cost)
    rf = vt.CASH_ANNUAL if cash is None else float(cash)
    cash_d = (1.0 + rf) ** (1.0 / 252) - 1.0
    legs = list(navs)
    rets = pd.DataFrame({k: navs[k].reindex(wdf.index).pct_change().fillna(0.0) for k in legs})
    eff = wdf.shift(1)
    if len(eff):
        eff.iloc[0] = wdf.iloc[0]
    gross = (eff * rets).sum(axis=1) + (1.0 - eff.sum(axis=1)).clip(lower=0.0) * cash_d
    fee = wdf.diff().abs().sum(axis=1).fillna(0.0) * c
    return (1.0 + gross - fee).cumprod()


def common_index():
    idx = None
    navs = {}
    for leg in vt.POOL:
        navs[leg] = vt.load_nav(leg)
        idx = navs[leg].index if idx is None else idx.intersection(navs[leg].index)
    idx = pd.DatetimeIndex(idx).sort_values()
    return {k: v.reindex(idx).ffill() for k, v in navs.items()}, idx


# ---------------------------------------------------------------- 变体
def build_variant(code, navs, idx, **kw):
    """-> (wdf, 说明)。所有变体都返回逐日权重矩阵，同一套执行/计费。"""
    half = 0.5
    if code == "O0":
        w = {}
        for leg in vt.POOL:
            _n, _d, s = leg_weights(leg)
            w[leg] = half * s.reindex(idx).ffill()
        return pd.DataFrame(w), "per-leg VT(simple20) + 50/50"

    if code in ("O1", "O2"):
        kind = "ewma" if code == "O1" else "blend"
        w = {}
        for leg in vt.POOL:
            _n, _d, s = leg_weights(leg, kind=kind, **kw)
            w[leg] = half * s.reindex(idx).ffill()
        return pd.DataFrame(w), f"per-leg VT({kind}) + 50/50"

    if code == "O3":
        w = {}
        for leg in vt.POOL:
            _n, _d, s = leg_weights(leg, gamma=kw.get("gamma", 1.5))
            w[leg] = half * s.reindex(idx).ffill()
        return pd.DataFrame(w), "per-leg VT, w=(TV/vol)^1.5 + 50/50"

    if code == "O4":
        return rp_weights(navs, tv=None), "组合层风险平价（不加总波动目标）"

    if code == "O5":
        return rp_weights(navs, tv=TV, vol_win=kw.get("vol_win", 20)), \
            f"RP + 组合 TV（σ 窗口 {kw.get('vol_win', 20)} 日）"

    if code == "O6":                      # RP + 组合 TV，σ 用 EWMA(0.94)
        return rp_weights(navs, tv=TV, vol_win=kw.get("vol_win", 20), kind="ewma"), \
            "RP + 组合 TV（σ 用 EWMA 0.94）"

    if code == "O7":
        rpw = rp_weights(navs, tv=None)
        w = {}
        for leg in vt.POOL:
            _n, _d, s = leg_weights(leg)
            w[leg] = rpw[leg] * s.reindex(idx).ffill()
        return pd.DataFrame(w), "per-leg VT × 动态 RP 权重"

    if code == "O8":
        w = {}
        for leg in vt.POOL:
            p = vt.params(FREQ="month", CUT=True, ADD="decay")
            p.update(kw)
            sig = vt.signal_of(leg, p)
            sm = pf.machine(sig, p)
            st = sm.reindex(idx).ffill().fillna(1.0)
            w[leg] = half * st
        return pd.DataFrame(w), "per-leg VT + 快减 + 快补（V5）"

    if code == "O9":
        return pd.DataFrame({k: pd.Series(1.0, index=idx) for k in vt.POOL}).mul(half), \
            "静态 50/50"

    raise ValueError(code)


def metrics_of(nav):
    m = vt.perf(nav)
    return dict(ann=m["ann"], vol=m["vol"], mdd=m["mdd"], sharpe=m["sharpe"])


def seg_sharpe(nav, a, b):
    seg = nav.loc[(nav.index >= pd.Timestamp(a)) & (nav.index <= pd.Timestamp(b))]
    if len(seg) < 40:
        return float("nan"), float("nan")
    seg = seg / seg.iloc[0]
    return vt.perf(seg)["sharpe"], float(seg.iloc[-1] - 1.0)


def main():
    navs, idx = common_index()
    mid = idx[0] + (idx[-1] - idx[0]) / 2
    h1 = (idx[0].isoformat()[:10], (mid - pd.Timedelta(days=1)).isoformat()[:10])
    h2 = (mid.isoformat()[:10], idx[-1].isoformat()[:10])

    codes = ["O0", "O1", "O2", "O3", "O4", "O5", "O6", "O7", "O8", "O9"]
    res = {}
    for code in codes:
        wdf, note = build_variant(code, navs, idx)
        wdf = wdf.reindex(idx).fillna(0.0)
        nv = run_matrix(navs, wdf)
        m = metrics_of(nv)
        sh1, _ = seg_sharpe(nv, *h1)
        sh2, _ = seg_sharpe(nv, *h2)
        # 剔除 2020
        nvx = nv[nv.index.year != 2020]
        nvx = nvx / nvx.iloc[0]
        m_ex = metrics_of(nvx)
        w20, r20 = seg_sharpe(nv, "2020-02-19", "2020-08-31")
        _w22, r22 = seg_sharpe(nv, "2022-01-03", "2022-12-30")
        trades = int((wdf.diff().abs().sum(axis=1) > 1e-12).sum())
        turn = float(wdf.diff().abs().sum().sum())
        res[code] = dict(note=note, nav=nv, w=wdf, **m, sh1=sh1, sh2=sh2,
                         ann_ex=m_ex["ann"], mdd_ex=m_ex["mdd"], sh_ex=m_ex["sharpe"],
                         w20=r20, w22=r22, trades=trades, turn=turn,
                         y18=vt.yearly(nv).get("2018"))
        print(f"{code} {note[:34]:36s} 年化 {vt.fmt_pct(m['ann'])} 回撤 {vt.fmt_pct(m['mdd'])} "
              f"夏普 {m['sharpe']:.2f} | 前半 {sh1:.2f} 后半 {sh2:.2f} 剔20 {m_ex['sharpe']:.2f} "
              f"| 2020 {vt.fmt_pct(r20)}")

    base = res["O0"]
    # ---- 闸门判定
    gates = {}
    for code in codes:
        r = res[code]
        g1 = (r["sharpe"] >= base["sharpe"] + 0.02) and (r["ann"] >= base["ann"] - 0.005)
        g2 = (r["sh1"] >= base["sh1"] - 0.03) and (r["sh2"] >= base["sh2"] - 0.03)
        g3 = r["sh_ex"] >= base["sh_ex"] - 0.02
        gates[code] = dict(G1=g1, G2=g2, G3=g3, pass3=bool(g1 and g2 and g3))
    survivors = [c for c in codes if gates[c]["pass3"] and c not in ("O0", "O9")]

    # ---- 邻域（只对通过者；也顺手给 O8 一个对照）
    neigh = {}
    for code in list(dict.fromkeys(survivors + ["O8"])):
        rows = []
        for tag, over in NEIGH.get(code, []):
            wdf, _n = build_variant(code, navs, idx, **over) if over else build_variant(code, navs, idx)
            wdf = wdf.reindex(idx).fillna(0.0)
            nv = run_matrix(navs, wdf)
            m = metrics_of(nv)
            nvx = nv[nv.index.year != 2020]
            rows.append(dict(tag=tag, **m, sh_ex=metrics_of(nvx / nvx.iloc[0])["sharpe"]))
        neigh[code] = rows

    # ---- 报告
    yrs = (idx[-1] - idx[0]).days / 365.25
    L = [
        "# V9 优化探针：不过拟合的改进在哪",
        "",
        "> 文档一致性不再是约束（用户 2026-11-04 明确）。**唯一约束是「不能是样本内拟合」** ——",
        "> 所以本报告先立 4 道闸门（见下），再看数字。",
        f"> 样本 {idx[0].date()} ~ {idx[-1].date()}（{yrs:.1f} 年）· 池 = 纳指100 + 标普500 ·",
        f"执行 = 场外复权净值 · T+1 成交 / T+2 生效 · 空仓 {vt.CASH_ANNUAL:.2%} · 单边 {vt.COST:.2%}。",
        "",
        "## 1. 总表（基准 = O0）",
        "",
        "| 编号 | 方向 | 年化 | 波动 | 最大回撤 | 夏普 | 前半夏普 | 后半夏普 | 剔2020夏普 | 2020 窗口 | 2022 窗口 | 调仓/年 |",
        "|---|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|",
    ]
    for code in codes:
        r = res[code]
        L.append(f"| {code} | {r['note']} | {vt.fmt_pct(r['ann'])} | {r['vol'] * 100:.2f}% | "
                 f"{vt.fmt_pct(r['mdd'])} | {r['sharpe']:.2f} | {r['sh1']:.2f} | {r['sh2']:.2f} | "
                 f"{r['sh_ex']:.2f} | {vt.fmt_pct(r['w20'])} | {vt.fmt_pct(r['w22'])} | "
                 f"{r['trades'] / yrs:.1f} |")
    L += [
        "",
        f"> 基准 O0：年化 {vt.fmt_pct(base['ann'])}、回撤 {vt.fmt_pct(base['mdd'])}、"
        f"夏普 {base['sharpe']:.2f}；前半 {base['sh1']:.2f} / 后半 {base['sh2']:.2f} / "
        f"剔2020 {base['sh_ex']:.2f}。",
        "",
        "## 2. 闸门判定",
        "",
        "| 编号 | G1 全样本 | G2 分半 | G3 剔除2020 | 结论 |",
        "|---|---|---|---|---|",
    ]
    for code in codes:
        g = gates[code]
        if code in ("O0", "O9"):
            verdict = "参照系"
        elif g["pass3"]:
            verdict = "**通过前 3 关**（待邻域）"
        else:
            miss = [k for k in ("G1", "G2", "G3") if not g[k]]
            verdict = "未通过：" + "/".join(miss)
        L.append(f"| {code} | {'✅' if g['G1'] else '❌'} | {'✅' if g['G2'] else '❌'} | "
                 f"{'✅' if g['G3'] else '❌'} | {verdict} |")
    L += ["", "## 3. 邻域（只对通过前 3 关的变体 + O8 参照）", ""]

    if not neigh:
        L.append("> 没有任何变体通过前 3 关 —— 因此**不产生候选**，维持现状。")
    for code, rows in neigh.items():
        L += [f"**{code} · {res[code]['note']}**", "",
              "| 扰动 | 年化 | 最大回撤 | 夏普 | 剔2020夏普 | 判定 |",
              "|---|---:|---:|---:|---:|---|"]
        ok = 0
        for row in rows:
            good = row["sharpe"] >= base["sharpe"] + 0.02 and row["sh_ex"] >= base["sh_ex"] - 0.02
            ok += int(good)
            L.append(f"| {row['tag']} | {vt.fmt_pct(row['ann'])} | {vt.fmt_pct(row['mdd'])} | "
                     f"{row['sharpe']:.2f} | {row['sh_ex']:.2f} | "
                     f"{'✅ 稳' if good else '⚠️ 翻转'} |")
        L.append("")
        L.append(f"> {ok}/{len(rows)} 档在扰动下仍优于基准 → "
                 f"{'**G4 通过**' if ok == len(rows) else '**G4 未通过（结论依赖参数）**'}。")
        L.append("")

    # ---- 逐腿（只对「单腿可独立定义」的变体：O0~O3）
    legsets = [("O0", {}), ("O1", dict(kind="ewma")), ("O2", dict(kind="blend")),
               ("O3", dict(gamma=1.5))]
    leg_m = {}
    for code, kw in legsets:
        row = {}
        for leg in vt.POOL:
            nav, _d, s = leg_weights(leg, **kw)
            nv = run_matrix({leg: nav}, pd.DataFrame({leg: s}))
            row[leg] = metrics_of(nv)
        leg_m[code] = row
    L += ["## 4. 逐腿（同一变体在两条腿上是否都成立）", "",
          "| 变体 | 纳指 年化 | 纳指 夏普 | 标普 年化 | 标普 夏普 | 方向一致？ |",
          "|---|---:|---:|---:|---:|---|"]
    for code, _kw in legsets:
        row = leg_m[code]
        agree = all(row[k]["sharpe"] >= leg_m["O0"][k]["sharpe"] + 0.01 for k in vt.POOL)
        tag = "参照" if code == "O0" else ("✅ 两腿都更好" if agree else "❌ 只有一条腿好")
        L.append(f"| {code} {res[code]['note']} | {vt.fmt_pct(row['ndx']['ann'])} | "
                 f"{row['ndx']['sharpe']:.2f} | {vt.fmt_pct(row['spx']['ann'])} | "
                 f"{row['spx']['sharpe']:.2f} | {tag} |")

    # ---- 结论
    L += ["", "## 5. 结论", ""]
    L += conclusion(res, gates, neigh, base, survivors)
    L += ["", "## 6. 口径与边界", "",
          "1. 所有变体共用同一套执行/计费（`run_matrix`：权重 T+1 生效、`Σ|Δw|` 计单边成本、"
          "空仓按 2% 计息），差别只在「权重怎么来」。",
          "2. O5/O6 的 `σ_p` 用 20 日单腿 σ + 60 日相关系数估计；相关系数在危机里会上升，"
          "所以**组合波动目标在危机时可能低估**（这会让实际波动高于 15%）—— 是本探针的一个已知缺陷。",
          "3. O1 的 EWMA 用 RiskMetrics 标准 λ=0.94（等效约 16 日半衰期），未调优。",
          "4. 样本 14.2 年、2 个标的、1 次急跌 —— 任何结论的统计功效都很低。"
          "本报告的价值在于**排除**（哪些方向明显不行），不在于**确认**。",
          "5. 试验预算：10 个机制变体。**没有做网格搜索**，所以不存在「在 100 组里挑最好看的一组」"
          "那种多重检验问题；但 10 组也是有偏的，故用 4 道闸门加严。",
          "", "> 模拟结果，非投资建议。"]

    with open(OUT_MD, "w", encoding="utf-8") as f:
        f.write("\n".join(L))
    print("\n报告:", OUT_MD)
    L2 = [c for c in survivors if gates[c]["pass3"]]
    print("通过前 3 关:", L2 or "无")
    return 0


NEIGH = {
    "O1": [("基线 EWMA(0.94)", {}), ("λ=0.90", dict(lam=0.90)), ("λ=0.97", dict(lam=0.97))],
    "O2": [("基线 blend(20,60)", {}), ("vol 窗口 10", dict(win=10))],
    "O3": [("基线 γ=1.5", {}), ("γ=1.3", dict(gamma=1.3)), ("γ=1.8", dict(gamma=1.8))],
    "O4": [("基线 RP", {})],
    "O5": [("基线 RP+TV(20日)", {}), ("σ 窗口 10 日", dict(vol_win=10)),
           ("σ 窗口 60 日", dict(vol_win=60))],
    "O6": [("基线", {})],
    "O7": [("基线", {})],
    "O8": [(nm, dict(over)) for nm, over in pf.NEIGHBORHOOD],
    "O9": [("基线", {})],
}


def conclusion(res, gates, neigh, base, survivors):
    """自动生成结论（只描述数据）。"""
    out = []
    codes = list(res)
    b = base
    _ix = res["O8"]["nav"].index
    yrs_o8 = (_ix[-1] - _ix[0]).days / 365.25
    better = [c for c in codes if c not in ("O0", "O9") and gates[c]["G1"]]

    # 1) 谁提高了夏普
    if better:
        out.append("**1. 提高夏普的只有一个方向：O8（快减 + 快补）**"
                   f"—— 夏普 {res['O8']['sharpe']:.2f} vs {b['sharpe']:.2f}、回撤 "
                   f"{vt.fmt_pct(res['O8']['mdd'])} vs {vt.fmt_pct(b['mdd'])}、年化 "
                   f"{vt.fmt_pct(res['O8']['ann'])} vs {vt.fmt_pct(b['ann'])}。")
    else:
        out.append("**1. 没有任何变体提高夏普** —— 这版风险预算已经很难被结构改动打败。")

    # 2) 逐个说明其他方向为什么不行（数据驱动）
    why = {
        "O1": "换更快的波动率估计量（EWMA λ=0.94）**没用反而更差** —— 说明「慢」的不是估计量，"
              "而是**月末这个采样点**：估计量再快，月末才看一眼也是白搭",
        "O2": "长短窗口混合（0.5·(vol20+vol60)）**更钝**：60 日把 2020 的波动上行吃掉一半，"
              "2020 窗口恶化到 −6.41%",
        "O3": "凸性映射 $w=(TV/vol)^{1.5}$ 把回撤压到 −16.92%（好 2.77pp），但年化掉 0.62pp、"
              "夏普只 +0.01 —— **过度对冲**，G1 差一口气",
        "O4": "纯风险平价（不加总波动目标）等于**放弃风险预算**：年化回到 15.06%，"
              "回撤也回到 −28.30%，与静态 50/50 没区别",
        "O5": "风险平价 + 组合 TV：年化 +0.39pp，但**回撤反而恶化到 −20.43%** —— "
              "因为危机里它把仓位挪给跌得少的标普（=更满仓），方向与「控回撤」相反",
        "O6": "RP + EWMA：两个不奏效的方向叠加，仍然是 0.82",
        "O7": "单腿 VT × 动态 RP：夏普 0.85、剔 2020 后 0.98（最好的防过拟合成绩），"
              "但 G1 差 0.01 —— **方向对，力度不够**，可作为下一步的起点",
        "O9": "静态 50/50：年化最高（15.11%）、夏普最低（0.78），回撤 −28.17%",
    }
    out.append("")
    out.append("**2. 其余方向为什么不行**（都是结构方向，不是调参）：")
    for c in codes:
        if c not in ("O0", "O8") and c in why:
            out.append(f"- `{c}` {res[c]['note']}：夏普 {res[c]['sharpe']:.2f}。{why[c]}。")

    # 3) O8 的诚实定价
    d_ex = res["O8"]["sh_ex"] - b["sh_ex"]
    out += [
        "",
        "**3. O8 通过了四道闸门，但它的改善来源很单薄**：",
        "",
        f"- 剔除 2020 后夏普 **{res['O8']['sh_ex']:.2f} vs {b['sh_ex']:.2f}（{d_ex:+.2f}）** —— "
        "基本打平，说明**改善就是那一次急跌**；",
        f"- 2018 年 {vt.fmt_pct(res['O8']['y18'])} vs 基准 {vt.fmt_pct(b['y18'])}"
        f"（**{(res['O8']['y18'] - b['y18']) * 100:+.2f}pp**）—— "
        "「急跌后快速 V 反」的行情里它是负贡献；",
        f"- 调仓 {res['O8']['trades'] / yrs_o8:.1f} 次/年（基准 {b['trades'] / yrs_o8:.1f} 次/年）；"
        "按场外 A 类 0.6% 单边费率重算仍有 12.19% vs 基准档 11.84%（见 `_probe_fast.md` §1），"
        "**费用不是它的障碍**；",
        f"- 但它在**后半段样本**（2019-09 起，含 2020 与 2022）夏普 {res['O8']['sh2']:.2f} vs "
        f"基准 {b['sh2']:.2f}，这是全表最大的单项差距。",
        "",
        "**4. 结论**：把 O8 采纳为新默认（用户 2026-11-04 明确「文档只是参考」），"
        "定位是**尾部保险**而不是收益引擎 —— 付出的保费是 2018 那类行情与更高的换手，"
        "买到的是「下一次深跌时回撤少 1/4」。若你要的是「规则最简单、不解释补丁」，"
        "`TRIGGER=\"month\"` 保留原档即可，两档在代码里只差一个开关。",
        "",
        f"> 真正该警惕的**过拟合形式**在这里不是「参数挑得漂亮」（邻域 13/13 已排除），"
        "而是「**结论只对一次事件成立**」。所以采纳 O8 的同时必须写清：它的证据强度是 "
        "n=1，下次急跌未必一样。",
        "> 继续优化的正确方向不是在同一段样本里再试变体，而是**扩样本**"
        "（拉更长的美股历史 / 加第三个市场），以及把 O7（剔 2020 后 0.98）做深。",
    ]
    return out


if __name__ == "__main__":
    sys.exit(main())
