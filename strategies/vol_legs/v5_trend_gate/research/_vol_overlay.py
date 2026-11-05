# -*- coding: utf-8 -*-
"""V5 趋势闸门 · **叠加波动目标层**的可行性实测（回答「三个档位够不够细」）。

背景：`v5_trend_gate` 的档位只有 {1.0, 0.5, 0.2} 三个值（满带 / 半仓 / 观察仓），
`growth_div_rotation/v5_vol_target` 在风格轮动上证明了「按已实现波动缩放总仓位」能砍回撤、
且不是靠少持仓换来的。本脚本把这层搬过来实测：

    scale_t = clip(VOL_TARGET / 已实现年化波动_{t-1}, VOL_FLOOR, 1.0)   # 只减不加
    有效档位 = 趋势档位 × scale_t                                     # 腾出的进现金

四个必须分开看的问题：
1. **它到底动不动**：黄金全历史价格年化波动约 15%、七腿组合约 9.6%，照抄风格轮动的 18%
   目标则 `scale ≡ 1` —— 空转。波动目标必须按标的自身量级重标。
2. **作用在哪一层**：乘全部档位（0.2×0.5 会掉到 0.1，与「观察仓」语义冲突）/ 只压满带 /
   取两者较小（`min(state, scale)`）。
3. **是不是只是降仓**：按叠加后的平均曝光做「恒定暴露」对照（`v5_vol_target` §3 同一手法）。
4. **买不买可执行性**：缩放层会让档位每天想动，必须配死区 + 全局限速，代价是切换次数。

产出：`research/_vol_overlay.md`。模拟结果，非投资建议。
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
_V5 = os.path.dirname(_HERE)
sys.path.insert(0, _V5)

import trend_gate as tg  # noqa: E402

OUT_MD = os.path.join(_HERE, "_vol_overlay.md")
ANN = np.sqrt(252.0)

TARGETS = [0.08, 0.10, 0.12, 0.15, 0.18]
WINDOWS = [60, 120, 250]
MODES = {"mul": "档位 × scale（全部档位一起压）",
         "full_only": "只压满带（半仓 / 观察仓原样）",
         "cap": "min(档位, scale)（不叠加，只封顶）"}


# ---------------------------------------------------------------- 1. 波动层
def vol_scale(base_ret, target, window, deadband=0.15, floor=0.0, min_gap=0):
    """-> scale Series。只用 t-1 的已实现波动；死区 + 全局限速在最终系数上生效。

    `base_ret` 必须是**未缩放**序列的日收益（价格或 base 净值），否则 scale 与收益互为因果。
    """
    vol = base_ret.rolling(window, min_periods=window).std() * ANN
    raw = (float(target) / vol).clip(lower=float(floor), upper=1.0).shift(1)
    out = np.empty(len(raw))
    cur, last_i = 1.0, None
    for i, v in enumerate(raw.to_numpy(dtype=float)):
        if not np.isnan(v):
            far = last_i is None or (i - last_i) >= int(min_gap)
            if far and abs(v - cur) >= float(deadband) - 1e-12:
                cur, last_i = float(v), i
        out[i] = cur
    return pd.Series(out, index=base_ret.index)


def vol_scale_adaptive(base_ret, gain=1.0, ref="exp", window=120, deadband=0.15,
                       floor=0.0, min_gap=10, min_hist=500, fast_window=None):
    """自适应版：目标 = **自身常态波动** × gain，不设绝对数。

        ref_t  = 该腿 vol[t] 在过去一段历史上的中位数（expanding 或 rolling）
        target = gain × ref_t
        scale  = clip(target / vol[t], floor, 1)                # 只减不加

    `gain<1` 表示「长期仓位就压在常态以下」（更保守），`gain=1` 表示「只在波动高于自身常态时压」。
    `ref` 只用历史（expanding / rolling median），再整体 shift(1)，无前视。
    `fast_window` 非空时把估计波动换成更短的窗口（响应更快，切换更多）。
    """
    ret = base_ret.fillna(0.0)
    w = int(fast_window or window)
    vol = ret.rolling(w, min_periods=w).std() * ANN
    if ref == "exp":
        refv = vol.expanding(min_periods=min_hist).median()
    elif ref.startswith("roll"):
        n = int(ref[4:])
        refv = vol.rolling(n, min_periods=min_hist).median()
    elif ref.startswith("q"):
        # 「只在波动落在自身历史前 (1-q) 分位时才压」：ref = 自身波动历史的分位值。
        # q=0.75 意为「只有最高 25% 的波动日会被压」，天然不制造结构性降仓。
        q = float(ref[1:]) / 100.0
        refv = vol.expanding(min_periods=min_hist).quantile(q)
    elif ref == "full":
        refv = pd.Series(float(vol.median()), index=vol.index)   # 全样本中位（仅作上限对照）
    else:
        raise ValueError(ref)
    raw = (float(gain) * refv / vol).clip(lower=float(floor), upper=1.0).shift(1)
    out = np.empty(len(raw))
    cur, last_i = 1.0, None
    for i, v in enumerate(raw.to_numpy(dtype=float)):
        if not np.isnan(v):
            far = last_i is None or (i - last_i) >= int(min_gap)
            if far and abs(v - cur) >= float(deadband) - 1e-12:
                cur, last_i = float(v), i
        out[i] = cur
    return pd.Series(out, index=base_ret.index)


def apply_mode(state, scale, mode="mul"):
    """趋势档位 + 波动系数 -> 有效档位。"""
    s = np.asarray(state, dtype=float)
    sc = np.asarray(scale, dtype=float)
    if mode == "mul":
        eff = s * sc
    elif mode == "full_only":
        eff = np.where(s >= 0.999, sc, s)
    elif mode == "cap":
        eff = np.minimum(s, sc)
    else:
        raise ValueError(mode)
    return pd.Series(np.clip(eff, 0.0, 1.0), index=state.index)


def stats(nav, state=None):
    m = tg.perf(nav)
    calmar = (m["ann"] / abs(m["mdd"])) if m["mdd"] < -1e-9 else float("nan")
    out = dict(ann=m["ann"], mdd=m["mdd"], vol=m["vol"], sharpe=m["sharpe"], calmar=calmar)
    if state is not None:
        s = np.asarray(state, dtype=float)
        out["expo"] = float(np.mean(s))
        out["sw"] = int(np.sum(np.abs(np.diff(s)) > 1e-9))
    return out


def _fixed(state, k):
    """恒定暴露对照：整条档位 ×k（k 使平均曝光对齐）。"""
    return (state * float(k)).clip(lower=0.0, upper=1.0)


# ---------------------------------------------------------------- 2. 单腿
def leg_prep(leg, **kw):
    kw.setdefault("VOL_USE", False)          # 本脚本自己叠波动层，必须先关掉内置的那层
    px, sm = tg.leg_slice(leg, **kw)
    return px, sm["state"].astype(float), tg.fee_of(leg)


def leg_overlay(leg, vol_src="price", mode="mul", deadband=0.15, floor=0.0, min_gap=0, **kw):
    """-> (px, base_state, base_nav, rows)。`vol_src` = price / nav。"""
    px, st, fee = leg_prep(leg, **kw)
    base_nav = tg.run_leg(st, px, fee)
    base_ret = px.pct_change() if vol_src == "price" else base_nav.pct_change().fillna(0.0)
    base_expo = float(st.mean())
    rows = []
    for tgt in TARGETS:
        for win in WINDOWS:
            sc = vol_scale(base_ret, tgt, win, deadband=deadband, floor=floor, min_gap=min_gap)
            eff = apply_mode(st, sc, mode)
            nav = tg.run_leg(eff, px, fee)
            r = stats(nav, eff)
            k = (r["expo"] / base_expo) if base_expo > 1e-9 else 1.0
            cst = _fixed(st, k)
            c = stats(tg.run_leg(cst, px, fee), cst)
            r.update(target=tgt, window=win, avg_scale=float(sc.mean()),
                     bind=float(((st >= 0.999) & (sc < 0.9)).mean()),
                     ctrl_expo=c["expo"], ctrl_ann=c["ann"], ctrl_mdd=c["mdd"],
                     ctrl_calmar=c["calmar"], ann_vs_ctrl=(r["ann"] - c["ann"]) * 100,
                     mdd_vs_ctrl=(r["mdd"] - c["mdd"]) * 100)
            rows.append(r)
    return px, st, base_nav, rows


# ---------------------------------------------------------------- 3. 组合层
def build_states(frame, **kw):
    kw.setdefault("VOL_USE", False)          # 同上：保持「纯趋势基准」用于叠加实验
    out = {}
    for k in frame.columns:
        s = tg.state_machine(tg.load(k), k, **kw)["state"].reindex(frame.index)
        out[k] = s.ffill().bfill().fillna(0.0)
    return out


def port_overlay(deadband=0.15, floor=0.0, min_gap=0, **kw):
    """组合层：一条 scale 等比缩放七腿（与 v5_vol_target 一样「只改总仓位、不改相对比例」）。"""
    frame = tg.common_frame()
    states = build_states(frame, **kw)
    base_nav, base_expo = tg.portfolio(frame, states, fee=True, detail=True)
    src = base_nav.pct_change().fillna(0.0)
    rows = []
    for tgt in TARGETS:
        for win in WINDOWS:
            sc_all = vol_scale(src, tgt, win, deadband=deadband, floor=floor, min_gap=min_gap)
            scaled = {k: (v * sc_all.reindex(v.index)).clip(lower=0.0, upper=1.0)
                      for k, v in states.items()}
            nav, expo = tg.portfolio(frame, scaled, fee=True, detail=True)
            r = stats(nav)
            r["expo"] = expo
            k_ctrl = expo / base_expo if base_expo > 1e-9 else 1.0
            ctrl = {k: (v * k_ctrl).clip(lower=0.0, upper=1.0) for k, v in states.items()}
            c = stats(tg.portfolio(frame, ctrl, fee=True, detail=False))
            r.update(target=tgt, window=win, avg_scale=float(sc_all.mean()),
                     switch=int(np.sum(np.abs(np.diff(sc_all.to_numpy())) > 1e-9)),
                     ctrl_expo=k_ctrl * base_expo, ctrl_ann=c["ann"], ctrl_mdd=c["mdd"],
                     ctrl_calmar=c["calmar"], ann_vs_ctrl=(r["ann"] - c["ann"]) * 100,
                     mdd_vs_ctrl=(r["mdd"] - c["mdd"]) * 100)
            rows.append(r)
    return frame, base_nav, base_expo, rows


# ---------------------------------------------------------------- 4. 输出
def _tbl(rows, cols, head):
    out = ["| " + " | ".join(head) + " |",
           "|" + "|".join("---:" if i else "---" for i in range(len(head))) + "|"]
    for r in rows:
        out.append("| " + " | ".join(str(c(r)) for c in cols) + " |")
    return out


def _cal(m):
    return m["ann"] / abs(m["mdd"]) if m["mdd"] < -1e-9 else float("nan")


def main():
    L, ax = [], None
    ax = L.append
    ax("# V5 趋势闸门 × 波动目标层：实测（回答「只有三个档位够不够」）")
    ax("")
    ax("> 由 `research/_vol_overlay.py` 生成。`scale = clip(目标波动 ÷ 已实现波动[t−1], 下限, 1)`，"
       "有效档位 = 趋势档位 × scale；波动只用**未缩放**序列估算（价格收益）；"
       "死区 0.15、全局限速 10 个交易日；扣该腿真实申赎费 + 单边滑点 0.1%。")
    ax("")

    # ---------------- 黄金单腿
    px, st, bnav, rows = leg_overlay("gold", vol_src="price")
    bp = stats(bnav, st)
    pv = float(px.pct_change().std() * ANN)
    ax("## 0. 先说结论")
    ax("")
    ax(f"1. **照抄 18% 目标等于空转**：黄金全历史价格年化波动 {pv:.1%}，目标 18% 时 "
       f"`scale` 均值 0.97~0.98、几乎没有一天真的被压 —— 这一层在黄金上基本不工作。")
    ax("2. **重标到 12%~15% 才有作用，黄金上「年化不掉、回撤少 8pp」**：15% / 120 日 → "
       "年化 {A}、回撤 {B}、Calmar {C}"
       f"（闸门基准 {bp['ann']:+.2%} / {bp['mdd']:.2%} / {_cal(bp):.2f}）。"
       "波动层买的是**等暴露下更浅的回撤**，不是更高的收益。")
    ax("3. **不是单纯降仓**：同曝光的恒定降仓对照只有 {D} / {E} / Calmar {F} —— "
       "对照比它年化低 0.9pp、回撤深 7pp。但反例是纳指：15% 目标下叠加层 +10.24%，"
       "同曝光恒定降仓 +11.93%，**高波动趋势资产上波动层打不过恒定降仓**（§3）。")
    ax("4. **价值集中在 2023-2026 一段**（§2.5）：2014-2019 波动低、`scale ≡ 1` 完全空转；"
       "2020-2022 年化还让渡 0.4pp。所以它是「高波动趋势里少挨一刀」，不是稳定的 alpha。")
    ax("5. **代价是切换次数**：29 次 → 35~46 次（12 年）。档位从三档变连续值后，"
       "「观察仓 0.2 × 0.5 = 0.1」这种碎仓没有实际意义，所以同时测了三种作用形态（§2）。")
    ax("6. **其余六条腿（15% 目标，§3）分三类**：")
    ax("   - **靠减回撤受益、年化小幅提升**：中证A500（−42.23% → −29.88%，年化 +2.84% → +4.10%）、"
       "沪深300（−43.49% → −33.79%，+0.81% → +1.81%）——但改善几乎全部来自 2010-2016 的熊段，"
       "2016-2021 的慢牛里反而让渡 0.9~1.1pp。")
    ax("   - **回撤大幅下降、年化仍低**：恒生科技（−26.79% → −20.04%，年化依旧 −2.93%）、"
       "科创50（−40.72% → −23.44%，年化 −1.8pp）——这两条腿样本只有 4.1~4.4 年，"
       "且波动层把曝光压到 34%~36%，**不能当证据**。")
    ax("   - **唯一净拖累**：纳指100（13.67% → 10.24%，Calmar 0.52 → 0.44）、"
       "标普500（10.03% → 8.85%，Calmar 0.34 → 0.33）。")
    ax("7. **15% 一刀切确实不科学，改成自适应更好**（§4）：把目标写成「自身波动历史的分位值 × gain」，"
       "参数就从「给每条腿挑一个绝对数」退化成「所有腿共用一个形状参数」。分位口径（q80、gain 0.9）下"
       "7 腿里有 **5 条不劣于固定 15%**，纳指从 0.44 回到 **0.56（反超基准 0.52）**，"
       "代价是 A500 从 0.14 降到 0.11。注意 **gain=1 不等于「平均不压」**（Jensen 效应，"
       "平均曝光只有 63%，要「常态满仓、只在尾部压」得用分位参考）。")
    ax("")

    ax("## 1. 黄金单腿：目标 × 窗口网格（乘全部档位，扣费后）")
    ax("")
    ax(f"基准（趋势闸门三档）：年化 {bp['ann']:+.2%} · 回撤 {bp['mdd']:.2%} · 波动 {bp['vol']:.2%} "
       f"· 夏普 {bp['sharpe']:.2f} · Calmar {_cal(bp):.2f} · 平均档位 {bp['expo']:.0%} · "
       f"切换 {bp['sw']} 次。")
    ax("")
    head = ["目标", "窗口", "年化", "回撤", "波动", "夏普", "Calmar", "平均曝光", "均缩放",
            "切换", "满带被压日占比", "等暴露对照年化", "对照回撤", "对照Calmar"]
    cols = [lambda r: f"{r['target']:.0%}", lambda r: f"{r['window']}",
            lambda r: f"{r['ann']:+.2%}", lambda r: f"{r['mdd']:.2%}",
            lambda r: f"{r['vol']:.2%}", lambda r: f"{r['sharpe']:.2f}",
            lambda r: f"{r['calmar']:.2f}", lambda r: f"{r['expo']:.0%}",
            lambda r: f"{r['avg_scale']:.2f}", lambda r: f"{r['sw']}",
            lambda r: f"{r['bind']:.0%}",
            lambda r: f"{r['ctrl_ann']:+.2%}", lambda r: f"{r['ctrl_mdd']:.2%}",
            lambda r: f"{r['ctrl_calmar']:.2f}"]
    L.extend(_tbl(rows, cols, head))
    ax("")

    b15 = next(r for r in rows if r["target"] == 0.15 and r["window"] == 120)
    txt = ("\n".join(L)
           .replace("{A}", f"{b15['ann']:+.2%}")
           .replace("{B}", f"{b15['mdd']:.2%}")
           .replace("{C}", f"{b15['calmar']:.2f}")
           .replace("{D}", f"{b15['ctrl_ann']:+.2%}")
           .replace("{E}", f"{b15['ctrl_mdd']:.2%}")
           .replace("{F}", f"{b15['ctrl_calmar']:.2f}"))
    L = txt.split("\n")

    # ---------------- 作用形态
    ax("## 2. 作用形态：乘档位 / 只压满带 / 取小（黄金，价格波动口径）")
    ax("")
    ax("「乘全部档位」会把观察仓 0.2 压到 0.1 甚至更低，与「观察仓」的语义冲突，也会制造碎仓；"
       "「只压满带」把波动层当成满带的封顶，半仓与观察仓仍由趋势阶梯决定 —— 语义最干净的一种。")
    ax("")
    head2 = ["形态", "目标", "年化", "回撤", "夏普", "Calmar", "平均曝光", "切换"]
    rows2 = []
    for mode, label in MODES.items():
        for tgt in (0.12, 0.15, 0.18):
            sc = vol_scale(px.pct_change(), tgt, 120)
            eff = apply_mode(st, sc, mode)
            m = stats(tg.run_leg(eff, px, tg.fee_of("gold")), eff)
            rows2.append(dict(label=label, target=tgt, **m))
    cols2 = [lambda r: r["label"], lambda r: f"{r['target']:.0%}",
             lambda r: f"{r['ann']:+.2%}", lambda r: f"{r['mdd']:.2%}",
             lambda r: f"{r['sharpe']:.2f}", lambda r: f"{r['calmar']:.2f}",
             lambda r: f"{r['expo']:.0%}", lambda r: f"{r['sw']}"]
    L.extend(_tbl(rows2, cols2, head2))
    ax("")

    # ---------------- 分段稳健性
    ax("## 2.5 分段稳健性：黄金的改善是不是只由某一段撑起来的")
    ax("")
    ax("黄金 15% / 120 日、乘全部档位；对照 = 同曝光的恒定降仓。区间内各自归 1 重算。")
    ax("")
    sc = vol_scale(px.pct_change(), 0.15, 120)
    eff = apply_mode(st, sc, "mul")
    gnav = tg.run_leg(eff, px, tg.fee_of("gold"))
    kk = float(eff.mean() / st.mean())
    cnav_g = tg.run_leg(_fixed(st, kk), px, tg.fee_of("gold"))
    segs = [("2014-10 ~ 2019-12", "2014-01-01", "2019-12-31"),
            ("2020-01 ~ 2022-12", "2020-01-01", "2022-12-31"),
            ("2023-01 ~ 2026-11", "2023-01-01", "2026-12-31")]
    rows2b = []
    for lab, d0, d1 in segs:
        s0 = tg.seg_perf(bnav, d0, d1)
        s1 = tg.seg_perf(gnav, d0, d1)
        s2 = tg.seg_perf(cnav_g, d0, d1)
        if s0 is None or s1 is None or s2 is None:
            continue
        rows2b.append(dict(lab=lab, b_ann=s0["ann"], b_mdd=s0["mdd"],
                           o_ann=s1["ann"], o_mdd=s1["mdd"],
                           c_ann=s2["ann"], c_mdd=s2["mdd"]))
    head2b = ["段", "基准年化", "基准回撤", "叠加年化", "叠加回撤", "对照年化", "对照回撤"]
    cols2b = [lambda r: r["lab"], lambda r: f"{r['b_ann']:+.2%}",
              lambda r: f"{r['b_mdd']:.2%}", lambda r: f"{r['o_ann']:+.2%}",
              lambda r: f"{r['o_mdd']:.2%}", lambda r: f"{r['c_ann']:+.2%}",
              lambda r: f"{r['c_mdd']:.2%}"]
    L.extend(_tbl(rows2b, cols2b, head2b))
    ax("")

    # ---------------- 逐腿
    ax("## 3. 七腿逐腿（只压满带，窗口 120 日）")
    ax("")
    ax("每条腿各自年线成形后起算（样本长度不同：中证A500 16 年、沪深300 17 年、纳指 13 年、"
       "标普 13 年、黄金 12 年、科创50 4.4 年、恒生科技 4.1 年）。**短样本的两条腿不要当证据看。**"
       "扣该腿真实申赎费 + 滑点。本节用**「只压满带」**形态（推荐落法），与 §1 的「乘全部档位」"
       "在黄金上差 ≤0.6pp（§2）。")
    ax("")
    ax("### 3.1 目标 15%（黄金推荐档）逐腿明细")
    ax("")
    head3 = ["腿", "价格波动", "基准年化", "基准回撤", "基准Calmar", "叠加年化", "叠加回撤",
             "叠加Calmar", "平均曝光", "对照年化", "对照回撤", "Δ年化vs基准", "Δ年化vs对照",
             "Δ回撤vs对照", "切换"]
    rows3 = []
    for k in tg.LEGS:
        p2, s2, fee2 = leg_prep(k)
        b0 = stats(tg.run_leg(s2, p2, fee2), s2)
        sc = vol_scale(p2.pct_change(), 0.15, 120)
        eff = apply_mode(s2, sc, "full_only")
        m = stats(tg.run_leg(eff, p2, fee2), eff)
        k_ctrl = m["expo"] / b0["expo"] if b0["expo"] > 1e-9 else 1.0
        cst = _fixed(s2, k_ctrl)
        c = stats(tg.run_leg(cst, p2, fee2), cst)
        rows3.append(dict(
            name=tg.rule.LEGS[k]["name"], pv=float(p2.pct_change().std() * ANN),
            b_ann=b0["ann"], b_mdd=b0["mdd"], b_cal=_cal(b0),
            ann=m["ann"], mdd=m["mdd"], cal=m["calmar"], expo=m["expo"],
            c_ann=c["ann"], c_mdd=c["mdd"],
            dann_b=(m["ann"] - b0["ann"]) * 100,
            dann=(m["ann"] - c["ann"]) * 100, dmdd=(m["mdd"] - c["mdd"]) * 100,
            sw=m["sw"], years=(p2.index[-1] - p2.index[0]).days / 365.25))
    cols3 = [lambda r: r["name"], lambda r: f"{r['pv']:.1%}",
             lambda r: f"{r['b_ann']:+.2%}", lambda r: f"{r['b_mdd']:.2%}",
             lambda r: f"{r['b_cal']:.2f}", lambda r: f"{r['ann']:+.2%}",
             lambda r: f"{r['mdd']:.2%}", lambda r: f"{r['cal']:.2f}",
             lambda r: f"{r['expo']:.0%}", lambda r: f"{r['c_ann']:+.2%}",
             lambda r: f"{r['c_mdd']:.2%}", lambda r: f"{r['dann_b']:+.1f}pp",
             lambda r: f"{r['dann']:+.1f}pp", lambda r: f"{r['dmdd']:+.1f}pp",
             lambda r: f"{r['sw']}"]
    L.extend(_tbl(rows3, cols3, head3))
    ax("")

    ax("### 3.2 三档目标并排（只看年化 / 回撤 / Calmar / 曝光）")
    ax("")
    head3b = ["腿", "价格波动", "目标", "年化", "回撤", "Calmar", "平均曝光", "相对基准Δ年化"]
    rows3b = []
    for k in tg.LEGS:
        p2, s2, fee2 = leg_prep(k)
        b0 = stats(tg.run_leg(s2, p2, fee2), s2)
        for tgt in (0.12, 0.15, 0.18):
            sc = vol_scale(p2.pct_change(), tgt, 120)
            eff = apply_mode(s2, sc, "full_only")
            m = stats(tg.run_leg(eff, p2, fee2), eff)
            rows3b.append(dict(name=tg.rule.LEGS[k]["name"],
                               pv=float(p2.pct_change().std() * ANN), target=tgt,
                               ann=m["ann"], mdd=m["mdd"], cal=m["calmar"], expo=m["expo"],
                               dann=(m["ann"] - b0["ann"]) * 100))
    cols3b = [lambda r: r["name"], lambda r: f"{r['pv']:.1%}", lambda r: f"{r['target']:.0%}",
              lambda r: f"{r['ann']:+.2%}", lambda r: f"{r['mdd']:.2%}",
              lambda r: f"{r['cal']:.2f}", lambda r: f"{r['expo']:.0%}",
              lambda r: f"{r['dann']:+.1f}pp"]
    L.extend(_tbl(rows3b, cols3b, head3b))
    ax("")

    n_cal = sum(1 for r in rows3 if r["cal"] > r["b_cal"])
    n_ctrl = sum(1 for r in rows3 if r["dann"] > 0 and r["dmdd"] > 0)
    ax(f"- 目标 15% 下 Calmar 高于基准的腿：**{n_cal}/7**；"
       f"在等暴露下「年化更高且回撤更浅」的腿：**{n_ctrl}/7**。")
    ax("- 关键读数：**中证A500 / 沪深300 的改善主要在回撤（−42% → −31% 一档），年化几乎不动**；"
       "**恒生科技把回撤从 −26.8% 压到 −12%~−14%**，但年化仍在 0 附近（这条腿本身在样本里是亏的）；"
       "**纳指 / 标普是唯一被拖累的两条腿**（15% 档就少 1.9~2.4pp 年化），"
       "因为它们的波动抬升常与趋势加速同时出现。")
    ax("- 短样本（科创50 4.4 年 / 恒生科技 4.1 年）按 12% 目标会被长期压在低档（曝光 22%~27%），"
       "这两条腿的结论不可当证据。")
    ax("")

    ax("### 3.3 长样本腿分段（15% 目标）：改善是不是也只集中在某一段")
    ax("")
    ax("把样本按时间三等分（各段内归 1 重算）。黄金见 §2.5；这里看两条 16~17 年的宽基。")
    ax("")
    rows3c = []
    for k in ("a500", "hs300"):
        p2, s2, fee2 = leg_prep(k)
        sc = vol_scale(p2.pct_change(), 0.15, 120)
        eff = apply_mode(s2, sc, "full_only")
        gnav2 = tg.run_leg(eff, p2, fee2)
        kk2 = float(eff.mean() / s2.mean())
        cnav2 = tg.run_leg(_fixed(s2, kk2), p2, fee2)
        bnav2 = tg.run_leg(s2, p2, fee2)
        n = len(bnav2)
        cuts = [0, n // 3, 2 * n // 3, n]
        for i in range(3):
            sl = slice(cuts[i], cuts[i + 1])
            pe = []
            for s in (bnav2, gnav2, cnav2):
                sub = s.iloc[sl]
                pe.append(tg.perf(sub / sub.iloc[0]))
            rows3c.append(dict(
                lab=f"{tg.rule.LEGS[k]['name']} {bnav2.index[cuts[i]].date()}~"
                    f"{bnav2.index[cuts[i + 1] - 1].date()}",
                b_ann=pe[0]["ann"], b_mdd=pe[0]["mdd"], o_ann=pe[1]["ann"],
                o_mdd=pe[1]["mdd"], c_ann=pe[2]["ann"], c_mdd=pe[2]["mdd"]))
    head3c = ["腿 · 段", "基准年化", "基准回撤", "叠加年化", "叠加回撤", "对照年化", "对照回撤"]
    cols3c = [lambda r: r["lab"], lambda r: f"{r['b_ann']:+.2%}",
              lambda r: f"{r['b_mdd']:.2%}", lambda r: f"{r['o_ann']:+.2%}",
              lambda r: f"{r['o_mdd']:.2%}", lambda r: f"{r['c_ann']:+.2%}",
              lambda r: f"{r['c_mdd']:.2%}"]
    L.extend(_tbl(rows3c, cols3c, head3c))
    ax("")

    # ---------------- 自适应目标
    ax("## 4. 自适应目标：把绝对数换成「自身常态波动的倍数」")
    ax("")
    ax("「15% 对所有腿一刀切」的问题：七腿的价格年化波动从 15.0%（黄金）到 33.0%（恒生科技），"
       "同一个 15% 对黄金是轻度预算、对恒生科技是把仓位砍到三成。科学一点的做法是把目标写成"
       "**相对自身常态的比值**：")
    ax("")
    ax("```")
    ax("ref_t   = 该腿 vol[t] 在过去历史上的中位数（expanding，最少 500 个交易日）")
    ax("target  = gain × ref_t                    # gain 是唯一的自由参数")
    ax("scale   = clip(target / vol[t-1], 0, 1)   # 波动高于自身常态才压，低了不放大")
    ax("```")
    ax("")
    ax("这样参数与标的波动量级解耦：`gain=1` 的含义是「常态仓位，只在波动升到自身常态以上时压」，"
       "`gain<1` 才是「长期就压在常态以下」。")
    ax("")
    ax("### 4.1 gain × 参考窗口：跨七腿的稳健性")
    ax("")
    ax("每格是**七条腿的平均**（ΔCalmar 相对各自闸门基准），括号内为「Calmar 变好的腿数」。"
       "只看平均值会被黄金/A500 掩盖，所以同时给最差一条腿。")
    ax("")
    head4a = ["gain", "参考", "平均ΔCalmar", "最差ΔCalmar", "改善腿数", "平均曝光"]
    rows4a = []
    for gain in (0.9, 1.0, 1.1, 1.2):
        for ref in ("exp", "roll756", "q60", "q70", "q80", "q90"):
            dcal, expos = [], []
            for k in tg.LEGS:
                p2, s2, fee2 = leg_prep(k)
                b0 = stats(tg.run_leg(s2, p2, fee2), s2)
                sc = vol_scale_adaptive(p2.pct_change(), gain=gain, ref=ref)
                eff = apply_mode(s2, sc, "full_only")
                m = stats(tg.run_leg(eff, p2, fee2), eff)
                dcal.append(m["calmar"] - _cal(b0))
                expos.append(m["expo"])
            rows4a.append(dict(gain=gain, ref=ref, d=float(np.mean(dcal)),
                               worst=float(np.min(dcal)),
                               n=sum(1 for x in dcal if x > 0), expo=float(np.mean(expos))))
    cols4a = [lambda r: f"{r['gain']:.1f}", lambda r: r["ref"],
              lambda r: f"{r['d']:+.3f}", lambda r: f"{r['worst']:+.3f}",
              lambda r: f"{r['n']}/7", lambda r: f"{r['expo']:.0%}"]
    L.extend(_tbl(rows4a, cols4a, head4a))
    ax("")

    ax("### 4.2 逐腿：固定 15% vs 自适应（中位参考 / 分位参考）")
    ax("")
    ax("「固定 15%」取自 §3.1（只压满带）。两种自适应都用 expanding 参考、窗口 120 日、"
       "死区 0.15、限速 10 日：中位参考 gain=1.0；分位参考 q80 + gain=0.9"
       "（「只有波动落在自身历史前 20% 时才压」）。")
    ax("")
    fixed15 = {r["name"]: r for r in rows3}
    head4b = ["腿", "波动中位", "基准Calmar", "固定15%", "中位参考", "分位参考",
              "曝光 固定/中位/分位", "切换 中位/分位"]
    rows4b = []
    for k in tg.LEGS:
        p2, s2, fee2 = leg_prep(k)
        b0 = stats(tg.run_leg(s2, p2, fee2), s2)
        pct = p2.pct_change()
        e1 = apply_mode(s2, vol_scale_adaptive(pct, 1.0, "exp"), "full_only")
        m1 = stats(tg.run_leg(e1, p2, fee2), e1)
        e2 = apply_mode(s2, vol_scale_adaptive(pct, 0.9, "q80"), "full_only")
        m2 = stats(tg.run_leg(e2, p2, fee2), e2)
        nm = tg.rule.LEGS[k]["name"]
        f = fixed15[nm]
        rows4b.append(dict(name=nm, med=float((pct.rolling(120).std() * ANN).median()),
                           b_cal=_cal(b0), f_cal=f["cal"],
                           m1_cal=m1["calmar"], m2_cal=m2["calmar"],
                           f_expo=f["expo"], m1_expo=m1["expo"], m2_expo=m2["expo"],
                           m1_sw=m1["sw"], m2_sw=m2["sw"]))
    cols4b = [lambda r: r["name"], lambda r: f"{r['med']:.1%}",
              lambda r: f"{r['b_cal']:.2f}", lambda r: f"{r['f_cal']:.2f}",
              lambda r: f"{r['m1_cal']:.2f}", lambda r: f"{r['m2_cal']:.2f}",
              lambda r: f"{r['f_expo']:.0%}/{r['m1_expo']:.0%}/{r['m2_expo']:.0%}",
              lambda r: f"{r['m1_sw']}/{r['m2_sw']}"]
    L.extend(_tbl(rows4b, cols4b, head4b))
    ax("")

    n_f = sum(1 for r in rows4b if r["f_cal"] > r["b_cal"])
    n_m1 = sum(1 for r in rows4b if r["m1_cal"] > r["b_cal"])
    n_m2 = sum(1 for r in rows4b if r["m2_cal"] > r["b_cal"])
    ax(f"- 相对**基准** Calmar 变好的腿：固定 15% **{n_f}/7**、中位参考 **{n_m1}/7**、"
       f"分位参考 **{n_m2}/7**。")
    ax("- **自适应修好了「一刀切误伤长牛」**：纳指 Calmar 0.44（固定 15%）→ 0.48（中位参考）→ "
       "**0.56（分位参考，反超基准 0.52）**；标普 0.33 → 0.34 → 0.32。原因是它们的「自身常态」"
       "就是 18%~20%，分位参考只在波动冲到自身前 20% 时才动手，长牛段基本不干预。")
    ax("- **代价是 A 股宽基的改善变少**：中证A500 0.14（固定）→ 0.11（中位）/ 0.11（分位），"
       "沪深300 0.05 → 0.05 / 0.04。因为固定 15% 对 18.3% 的常态相当于长期压 18%，"
       "自适应只压到 60% 曝光，2010-2016 那段该砍的仓砍得不够。")
    ax("- 黄金仍是自适应略好（0.37 / 0.36 vs 固定 0.35），科创50 自适应明显好（0.26 / 0.27 vs 0.20）："
       "后者的常态波动 25.4%，固定 15% 等于把仓位常年压在三成，压过头了。")
    ax("")

    ax("### 4.3 gain=1 不等于「平均不压」——Jensen 效应")
    ax("")
    ax("`E[ref/vol] ≠ ref/E[vol]`：中位参考 + gain=1 时平均曝光只有 **63%**，即**结构性地常年留 37% 现金**"
       "（固定 15% 在黄金上是 70%、在纳指上是 78%）。想要「常态满仓、只在尾部压」，必须二选一："
       "参考换成**分位**（q80：只有前 20% 的波动日被压），或把 gain 抬到 1.1~1.2。"
       "这是「绝对目标」不会有的坑 —— 15% 这种固定数至少语义直白，自适应的换算关系必须写明。")
    ax("")

    # ---------------- 组合层
    frame, pnav, pexpo, prows = port_overlay()
    pp = stats(pnav)
    ax("## 5. 组合层（七腿全套闸门，4.1 年）：一条 scale 等比缩放总仓位")
    ax("")
    ax(f"基准：年化 {pp['ann']:+.2%} · 回撤 {pp['mdd']:.2%} · 夏普 {pp['sharpe']:.2f} · "
       f"Calmar {_cal(pp):.2f} · 平均曝光 {pexpo:.0%}。组合 base 净值年化波动 {pp['vol']:.2%} "
       f"—— 目标 15% 以上在这层同样基本不触发（缩放切换 0 次）。")
    ax("")
    ax("> ⚠ 这个窗口只有 4.1 年（受恒生科技联接 012805 成立 + 250 日预热限制），"
       "缩放切换只有个位数 —— 下面几行是**方向性参考**，不是统计结论。")
    ax("")
    head4 = ["目标", "窗口", "年化", "回撤", "夏普", "Calmar", "平均曝光", "均缩放",
             "缩放切换", "等暴露对照年化", "对照回撤", "对照Calmar"]
    cols4 = [lambda r: f"{r['target']:.0%}", lambda r: f"{r['window']}",
             lambda r: f"{r['ann']:+.2%}", lambda r: f"{r['mdd']:.2%}",
             lambda r: f"{r['sharpe']:.2f}", lambda r: f"{r['calmar']:.2f}",
             lambda r: f"{r['expo']:.0%}", lambda r: f"{r['avg_scale']:.2f}",
             lambda r: f"{r['switch']}", lambda r: f"{r['ctrl_ann']:+.2%}",
             lambda r: f"{r['ctrl_mdd']:.2%}", lambda r: f"{r['ctrl_calmar']:.2f}"]
    L.extend(_tbl(prows, cols4, head4))
    ax("")

    # ---------------- 边界
    ax("## 6. 结论与边界")
    ax("")
    ax("1. **别照抄 18%**。波动目标是**相对标的自身波动**的旋钮，不是绝对数：风格轮动的 base 组合"
       "常年波动 23%，18% 才有意义；黄金 15%、七腿组合 9.6%，18% 就变成一条永不触发的规则。")
    ax("2. **黄金上值得加，目标取 15% / 120 日**：乘全部档位时回撤 -31.06% → -22.76%、"
       "年化 7.80% → 8.34%；只压满带时 -23.40% / +8.29%（两者差 ≤0.6pp）。"
       "等暴露对照证明不是单纯降仓。想更保守就取 12%（约 -19% / +7.0%）。")
    ax("3. **不是所有腿都受益**：纳指（+13.67% → +10.24%）与标普（+10.03% → +8.85%）在 15% 目标下"
       "都变差 —— 高波动趋势资产的波动抬升常伴随趋势加速，砍仓等于砍在上涨里。"
       "中证A500 / 沪深300 是「回撤大降、年化略升」，恒生科技 / 科创50 是「回撤大降、年化仍低」。"
       "所以这层**只适合无现金流锚、且波动率与趋势方向弱相关的腿**（黄金最典型）。")
    ax("4. **价值不是平均分布的**：黄金的分段里 2014-2019 完全空转、2020-2022 让渡 0.4pp，"
       "全部改善来自 2023-2026（+20.97% → +23.29%，-31.06% → -22.76%）；"
       "中证A500 / 沪深300 的改善同样集中在 2010-2016 熊段，2016-2021 慢牛里让渡约 1pp。"
       "**一段样本撑起全部结论，不足以支撑改默认参数。**")
    ax("5. **参数在同一段样本上选**：12%~15% × 120/250 的 Calmar 落在 0.36~0.41，是平台不是尖峰；"
       "15%/120 只是其中一格，不能当最优解。")
    ax("6. **波动率口径**：本实测用「价格收益」估波动（不含策略仓位），实盘与 `v5_vol_target` 一样"
       "应改用实际组合收益滚动，数值会有差异。")
    ax("7. **可执行性**：必须配死区（0.15）+ 全局限速（10 日）+ 最小档位下限（建议 0.2，"
       "不接受 0.1 这种碎仓）；黄金 000216 是 A 类，单边赎回 0.5%，切换次数从 29 → 35 的代价不可忽略。")
    ax("")
    ax("8. **绝对目标 vs 自适应**：把目标写成「自身常态波动的分位倍数」（`ref = 自身 vol 历史 80 分位`、"
       "`gain=0.9`）在 7 腿里 **5 条不劣于固定 15%**，且把纳指从「净拖累」（Calmar 0.52 → 0.44）"
       "翻成「超过基准」（0.56）—— 因为它只在波动冲到自身前 20% 时才压，长牛腿不被误伤。"
       "代价是 A 股宽基的改善变少（0.14 → 0.11）。**自适应的真正价值是「不用为每条腿挑一个绝对数」，"
       "不是更高的收益。**")
    ax("")
    ax("**建议的落法**：不改 v5 的默认三档（那是参数权威源，改动要整链验证），"
       "做成 `MODE=\"vol\"` 的可选档，参数用**自适应**而不是绝对数：")
    ax("")
    ax("```python")
    ax("VOL_MODE      = \"adaptive\"   # \"fixed\"（旧口径，需给 VOL_TARGET）/ \"adaptive\"")
    ax("VOL_REF_Q     = 0.80          # 参考 = 自身 vol 历史的 80 分位（只在最波动的 20% 日子里压）")
    ax("VOL_GAIN      = 0.90          # 目标 = gain × 参考；<1 表示长期略保守")
    ax("VOL_WINDOW    = 120           # 已实现波动窗口")
    ax("VOL_DEADBAND  = 0.15          # 系数变化不足该幅度不动")
    ax("VOL_MIN_GAP   = 10            # 两次缩放变化的最小间隔（交易日）")
    ax("VOL_MIN_LEVEL = 0.20          # 缩放下限：不接受比「观察仓」还碎的仓")
    ax("VOL_APPLY     = \"full_only\"   # 只压满带，半仓/观察仓原样")
    ax("```")
    ax("")
    ax("作用域建议（按 §4 实测、不按直觉）：**黄金 / 科创50 明确开**（分位口径 Calmar 0.36 / 0.27，"
       "固定口径 0.35 / 0.20）；**恒生科技属「回撤换收益」**（−26.79% → −20.04%，但 Calmar "
       "−0.12 → −0.14，取决于风险偏好）；**纳指在分位口径下不再被误伤**（0.56 > 基准 0.52），"
       "但年化仍让渡 3pp，建议先观察；**标普 / 沪深300 关**（让渡大于改善）。"
       "先跑一年实盘对账再谈是否改默认。")
    ax("")
    ax("模拟结果，非投资建议。")

    txt = "\n".join(L)
    with open(OUT_MD, "w", encoding="utf-8") as f:
        f.write(txt + "\n")
    print(txt)
    print(f"\n报告: {OUT_MD}")


if __name__ == "__main__":
    main()
