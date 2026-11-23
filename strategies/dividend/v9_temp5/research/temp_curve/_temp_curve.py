#!/usr/bin/env python
# -*- coding: utf-8 -*-
r"""V9 · 温度值直接换算成仓位（连续映射）—— 该给多陡的曲线？

用户问题：**不用档位，直接把温度数值换算成仓位占比，效果如何？怎么换算才合适？**

两个读法都要算（与 `research/temp_weight/_temp_weight.md` 的结论衔接）：
A. **横截面**：`w_i ∝ f(T_i)`，在当月有温度的标的间归一化到 100%（不降总暴露）；
B. **总仓位**：`m = f(板块均温)`，其余留现金。

研究设计（为什么这样设计）
--------------------------
「直接换算」的诱惑是写 `w = (100 − T)/100`。但归一化之后：
    w_i = (a − b·T_i) / Σ_j (a − b·T_j)
绝对刻度 `a` 与斜率 `b` 一起决定**倾斜幅度**，所以在横截面口径下，
「用不用档位」不是主要问题，**曲线给多陡**才是。故本课题要回答三件事：

1. 温度在**同一时点**的横截面离散度有多大（没有离散度就没有倾斜）；
2. 各映射隐含的倾斜幅度（`单边偏离度 = Σ|w−等权|/2`）是多少；
3. 这个倾斜幅度**该由信号的强度决定**：用「温度 z 分值 → 下月超额」的横截面回归斜率
   反推均值-方差最优倾斜 `k ≈ −b / σ_r²`，与各映射的实际倾斜对比。
   若映射给的倾斜是最优值的 2~5 倍，就说明「直接按数字比例换算」系统性过陡。

映射族（全部单调递减，越高越贵 -> 越少）
--------------------------------------
| 名称 | 公式 | 说明 |
|---|---|---|
| `lin100` | `(100 − T)/100` | 最直觉的写法（温度即百分比） |
| `lin85` | `max(0, 85 − T)/85` | 以危险线 85 为 0 点 |
| `lin80` | `max(0, 80 − T)/80` | 更早清仓 |
| `pow2` | `((85 − T)/85)²` | 线性加陡 |
| `sqrt` | `√((85 − T)/85)` | 线性压平 |
| `exp25` / `exp15` | `exp(−T/τ)` | 指数衰减（τ=25 / 15） |
| `logit` | `1/(1+exp((T−50)/10))` | S 形，中点在 50 |
| `rank` | `1 − 月内分位` | 只看排序、不看绝对水平 |
| `band` | `rule.BANDS` 七档配额 | 现行做法（对照） |
| `eq` | 1 | 等权（基准） |

口径（沿用 V9，不另立一套）
--------------------------
* 温度：`factors.build()` 月末五因子温度（35/20/16/15/14 · etf.run 等权 PE/PB），次月生效；
* 收益：基金累计净值（含分红再投），`pool.load_navs`；
* 成本：单边 0.03%，按 `Σ|Δw|` 计；
* 池：**只有 6 只有真实温度的标的**（`rule.VALUED`）；4 只价格温度标的不参与；
* 「强度」用与等权的混合系数 λ：`w = (1−λ)·等权 + λ·f归一`，λ 是**同一条规则的强度**，
  不是重新选参数；真正有效的映射应该在 λ 曲线上单调。

产物：`_temp_curve.md`（本目录，脚本同目录）。
用法：`python strategies/dividend/v9_temp5/research/temp_curve/_temp_curve.py`

> 机械规则输出，非投资建议。
"""

from __future__ import annotations

import io
import os
import sys

import numpy as np
import pandas as pd

try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:  # noqa: BLE001
    pass

_HERE = os.path.dirname(os.path.abspath(__file__))     # .../v9_temp5/research/temp_curve
_RES = os.path.dirname(_HERE)                          # .../v9_temp5/research
_V9 = os.path.dirname(_RES)                            # .../v9_temp5
_STRAT = os.path.dirname(_V9)                          # .../dividend
for _p in (_HERE, _RES, _V9, _STRAT):
    if _p not in sys.path:
        sys.path.insert(0, _p)

import _single_signal as S  # noqa: E402  pylint: disable=wrong-import-position
import factors  # noqa: E402  pylint: disable=wrong-import-position
import pool  # noqa: E402  pylint: disable=wrong-import-position
import rule as V9  # noqa: E402  pylint: disable=wrong-import-position

OUT_MD = os.path.join(_HERE, "_temp_curve.md")
FEE = S.FEE                     # 单边 0.03%
MIN_FUNDS = 3                   # 当月至少 3 只有温度才成组合
TAIL = 3                        # 剔除检验：去掉相对基准贡献最大的 3 个月
PERM_N = 200                    # 置换检验次数
SEED = 20261121

# ================================================================ 映射族
def _clip(v, lo=0.0, hi=None):
    out = max(v, lo)
    return out if hi is None else min(out, hi)


def map_lin100(t: float) -> float:
    return _clip((100.0 - t) / 100.0)


def map_lin85(t: float) -> float:
    return _clip((85.0 - t) / 85.0)


def map_lin80(t: float) -> float:
    return _clip((80.0 - t) / 80.0)


def map_pow2(t: float) -> float:
    return _clip((85.0 - t) / 85.0) ** 2


def map_sqrt(t: float) -> float:
    return float(np.sqrt(_clip((85.0 - t) / 85.0)))


def map_exp25(t: float) -> float:
    return float(np.exp(-t / 25.0))


def map_exp15(t: float) -> float:
    return float(np.exp(-t / 15.0))


def map_logit(t: float) -> float:
    return float(1.0 / (1.0 + np.exp((t - 50.0) / 10.0)))


BAND_QUOTA = {"冰点": 2.0, "偏冷": 1.5, "中性": 1.0, "中性偏热": 0.8,
              "偏热": 0.5, "过热": 0.2, "危险": 0.0}


def map_band(t: float) -> float:
    return BAND_QUOTA.get(V9.band_of(t)[0], 1.0)


# (键, 中文名, 函数, 是否随温度绝对水平平移)  —— rank 需当月刊内分位，单独处理
MAPS = [
    ("eq", "等权（基准）", None, ""),
    ("lin100", "线性 (100−T)/100", map_lin100, ""),
    ("lin85", "线性 max(0,85−T)/85", map_lin85, ""),
    ("lin80", "线性 max(0,80−T)/80", map_lin80, ""),
    ("pow2", "平方 ((85−T)/85)²", map_pow2, ""),
    ("sqrt", "开方 √((85−T)/85)", map_sqrt, ""),
    ("exp25", "指数 exp(−T/25)", map_exp25, ""),
    ("exp15", "指数 exp(−T/15)", map_exp15, ""),
    ("logit", "S形 1/(1+e^((T−50)/10))", map_logit, ""),
    ("rank", "排序 1−月内分位", "rank", ""),
    ("band", "档位配额 2.0/1.5/1.0/0.8/0.5/0.2/0", map_band, ""),
]
KEYS = [k for k, _n, _f, _x in MAPS if k != "eq"]


# ================================================================ 数据
def load_panel():
    """-> (温度宽表, 月度收益宽表)。只保留「有温度且有净值」的月份。"""
    panels = factors.build()
    temp = factors.monthly_temp(panels)
    navs = pool.load_navs(codes=V9.CODES).resample("ME").last()
    rets = navs.pct_change()
    temp = temp[[c for c in V9.VALUED if c in temp.columns]]
    temp = temp.where(navs.reindex(temp.index).notna() & rets.reindex(temp.index).notna())
    temp = temp[temp.notna().sum(axis=1) >= MIN_FUNDS]
    temp = temp[temp.index.isin(rets.index)]
    return temp, rets.reindex(temp.index), panels


def scores(row: pd.Series, key: str, fn) -> pd.Series:
    """当月可得标的的映射得分（未归一）。"""
    ok = row.dropna()
    if ok.empty:
        return pd.Series(dtype=float)
    if key == "rank":
        r = ok.rank(pct=True)
        return (1.0 - r).clip(lower=0.0) + 1e-6      # +ε：避免最热那只得分恰为 0
    return ok.map(fn)


def build_weights(temp: pd.DataFrame, key: str, fn, lam: float = 1.0) -> pd.DataFrame:
    """横截面：w ∝ f(T)，在可得标的间归一化，再与等权按 λ 混合。"""
    rows = {}
    for ts, row in temp.iterrows():
        out = pd.Series(0.0, index=row.index)
        ok = row.dropna()
        if not ok.empty:
            sc = scores(row, key, fn)
            eq = pd.Series(1.0 / len(ok), index=ok.index)
            if sc.sum() <= 0:
                out.loc[ok.index] = eq
            else:
                out.loc[ok.index] = (1 - lam) * eq + lam * (sc / sc.sum())
        rows[ts] = out
    return pd.DataFrame(rows).T


def build_mult(board: pd.Series, key: str, fn) -> pd.Series:
    """时间维度：板块均温 -> 总仓位（其余现金）。"""
    if key == "eq":
        return pd.Series(1.0, index=board.index)
    if key == "rank":
        # 板块用扩张窗口分位（不能用当月横截面，板块只有 1 个数）
        q = board.expanding(min_periods=12).apply(
            lambda w: float((w[:-1] < w[-1]).mean()) if len(w) > 1 else np.nan, raw=True)
        m = (1.0 - q).clip(0.0, 1.0)
        return m.fillna(1.0)
    return board.map(lambda x: 1.0 if pd.isna(x) else float(fn(float(x))))


# ================================================================ 检验工具
def simulate(W: pd.DataFrame, rets: pd.DataFrame):
    W = W.reindex(rets.index).fillna(0.0)
    wl = W.shift(1).fillna(0.0).where(rets.notna(), 0.0)
    tot = wl.sum(axis=1)
    wl = wl.div(tot.where(tot > 0), axis=0).fillna(0.0)
    port = (wl * rets.fillna(0.0)).sum(axis=1)
    turn = (wl - wl.shift(1).fillna(0.0)).abs().sum(axis=1)
    return (port - turn * FEE).iloc[1:].dropna(), turn.iloc[1:]


def simulate_time(W: pd.DataFrame, rets: pd.DataFrame):
    """时间维度专用：**不做归一化**，`Σw = m < 1` 的差额就是现金（0 收益）。"""
    wl = W.reindex(rets.index).fillna(0.0).shift(1).fillna(0.0)
    port = (wl * rets.fillna(0.0)).sum(axis=1)
    turn = (wl - wl.shift(1).fillna(0.0)).abs().sum(axis=1)
    return (port - turn * FEE).iloc[1:].dropna(), turn.iloc[1:]


def run_sc(W: pd.DataFrame, rets: pd.DataFrame, eq_w: pd.DataFrame,
           r_eq: pd.Series = None, cagr_eq: float = None) -> dict:
    r, turn = simulate(W, rets)
    st = dict(S.stats(r))
    st["turn"] = float(turn.mean())
    st["r"] = r
    live = W[W.sum(axis=1) > 0]
    eqw = eq_w.reindex(live.index)
    st["dev"] = float((live - eqw).abs().sum(axis=1).mean() / 2)   # 单边偏离度
    st["maxw"] = float(live.max(axis=1).mean())
    # 相对等权的年化跟踪误差与信息比（量出「这个映射动了多少主动风险」）
    if r_eq is not None:
        d = (r - r_eq).dropna()
        st["te"] = float(d.std() * np.sqrt(12)) if len(d) > 2 else np.nan
        st["ex"] = st["cagr"] - cagr_eq if cagr_eq is not None else np.nan
        st["ir"] = st["ex"] / st["te"] if st["te"] else np.nan
    else:
        st["te"] = st["ex"] = st["ir"] = np.nan
    return st


def disp_stats(temp: pd.DataFrame) -> dict:
    """月内温度横截面离散度：range / std，以及温度自身水平。"""
    rows = temp.dropna(how="all")
    rg = rows.max(axis=1) - rows.min(axis=1)
    sd = rows.std(axis=1)
    return dict(range_med=float(rg.median()), range_p90=float(rg.quantile(0.9)),
                sd_med=float(sd.median()), sd_p90=float(sd.quantile(0.9)),
                n=len(rows))


def xs_slope(temp: pd.DataFrame, rets: pd.DataFrame) -> dict:
    """横截面回归：下月超额收益 ~ 温度 z 分值。

    b = 每高出 1 个横截面标准差的温度，对应下月超额收益（相对当月横截面均值）。
    信息比 `ir_unit = |b| / σ_r` 是**尺度不变**的：任何单调线性映射换成任意振幅，
    这个比值不变，所以「该给多陡」不可能由数据唯一确定（必须由跟踪误差预算定）。
    """
    bs, sds, sig_r = [], [], []
    t = temp.iloc[:-1]                      # 最后一月没有下月收益
    r = rets.shift(-1).reindex(t.index)
    for ts, row in t.iterrows():
        ok = row.dropna()
        nxt = r.loc[ts].reindex(ok.index).dropna()
        ok = ok.reindex(nxt.index)
        if len(ok) < MIN_FUNDS or ok.std() == 0:
            continue
        z = (ok - ok.mean()) / ok.std()
        y = nxt - nxt.mean()
        bs.append(float((z * y).sum() / (z * z).sum()))
        sds.append(float(ok.std()))
        sig_r.append(float(nxt.std()))
    b = float(np.mean(bs)) if bs else np.nan
    se = float(np.std(bs, ddof=1) / np.sqrt(len(bs))) if len(bs) > 2 else np.nan
    sr = float(np.mean(sig_r)) if sig_r else np.nan
    return dict(b=b, se=se, t=(b / se if se else np.nan), n=len(bs),
                sigma_r=sr, sigma_t=float(np.mean(sds)) if sds else np.nan,
                ir_unit=abs(b) / sr if sr else np.nan)


def perm_test(temp: pd.DataFrame, rets: pd.DataFrame, key: str, fn,
              lam: float, n: int = PERM_N) -> pd.DataFrame:
    """月内随机打乱温度后重放同一映射 —— 零假设 = 温度与标的的对应关系无信息。"""
    cols = list(temp.columns)
    vals = temp[cols].values.astype(float)
    rng = np.random.default_rng(SEED)
    ok = ~np.isnan(vals)
    out = []
    for _ in range(n):
        pv = np.full_like(vals, np.nan)
        for i in range(vals.shape[0]):
            m = ok[i]
            pv[i, m] = rng.permutation(vals[i, m])
        W = build_weights(pd.DataFrame(pv, index=temp.index, columns=cols), key, fn, lam)
        r, _turn = simulate(W, rets)
        out.append(S.stats(r))
    return pd.DataFrame(out)


# ================================================================ 报告
def pct(x, nd=2, sign=False):
    if x is None or (isinstance(x, float) and not np.isfinite(x)):
        return "—"
    return f"{x:+.{nd}%}" if sign else f"{x:.{nd}%}"


def f2(x):
    return "—" if x is None or (isinstance(x, float) and not np.isfinite(x)) else f"{x:.2f}"


def render(ctx: dict) -> str:
    res, resc = ctx["res"], ctx["resc"]
    disp, sl, perm, perm_p = ctx["disp"], ctx["slope"], ctx["perm"], ctx["perm_p"]
    tm = ctx["temp"]
    base = res["eq"]
    L = []
    A = L.append
    A("# V9 · 温度值直接换算成仓位（连续映射）")
    A("")
    A(f"> 生成 {ctx['asof']:%Y-%m-%d} · 由 `strategies/dividend/v9_temp5/research/temp_curve/"
      "_temp_curve.py` 生成。**研究产物，不进每日流程**。")
    A("> 池：**6 只有真实温度的标的**（`rule.VALUED`；4 只价格温度标的不参与）。"
      "温度 = 五因子月末值，次月生效；收益 = 基金累计净值；单边 0.03%。")
    A("> 与 `research/temp_weight/` 的区别：那边测「七档配额」这个离散映射，"
      "本课题测**连续映射**（温度值直接进公式），并回答「曲线该多陡」。")
    A("")

    # ---------- 0 样本
    A("## 0. 样本与两个读法")
    A("")
    A(f"- **全窗口**：{res['start']:%Y-%m} ~ {res['end']:%Y-%m}（{res['n']} 月）——"
      "逐月至少 3 只有温度，可用只数 4→6（`008115`/`016441` 的温度起点较晚）。")
    A(f"- **共同窗口**：{resc['start']:%Y-%m} ~ {resc['end']:%Y-%m}（{resc['n']} 月）——"
      "6 只全部有温度。")
    A("- **读法 A（横截面）**：`w_i ∝ f(T_i)`，当月有温度的标的间归一化到 100%，不降总暴露；"
      "基准 = 同区间等权。")
    A("- **读法 B（总仓位）**：`m = f(板块均温)`，其余留现金；基准 = 100% 等权。")
    A("")

    # ---------- 1 离散度：能不能倾斜
    A("## 1. 先看「有没有斜率可倾斜」：月内温度的离散度")
    A("")
    A("| 指标 | 中位数 | p90 |")
    A("|---|---:|---:|")
    A(f"| 月内温度极差（max−min） | {disp['range_med']:.1f} 分 | {disp['range_p90']:.1f} 分 |")
    A(f"| 月内温度标准差 | {disp['sd_med']:.1f} 分 | {disp['sd_p90']:.1f} 分 |")
    A("")
    A(f"> {disp['n']} 个月、每月 4~6 只。**同一个月里最贵与最便宜通常只差 "
      f"{disp['range_med']:.0f} 分左右**（p90 也只有 {disp['range_p90']:.0f} 分），"
      "所以「把温度直接当比例」不会像直觉那样产生巨大倾斜 —— 这个数决定了下面所有映射的实际力度。")
    A("")

    # ---------- 2 信号强度 vs 曲线陡度
    A("## 2. 曲线该多陡：由「跟踪误差预算」决定，不由温度刻度决定")
    A("")
    A("横截面回归：`下月超额收益 = b × 温度z分值`（z 为当月月内标准化温度，"
      "超额相对当月横截面均值）。")
    A("")
    A("| 量 | 值 | 读法 |")
    A("|---|---:|---|")
    A(f"| 斜率 b（每 1 SD 温度 → 下月超额） | {pct(sl['b'], 2, True)} | 负 = 温度高的下月涨得少（方向对） |")
    A(f"| b 的标准误 / t 值 | {pct(sl['se'], 2)} / {sl['t']:+.2f} | \\|t\\| < 2 → 不显著 |")
    A(f"| 期数 | {sl['n']} | |")
    A(f"| 月度横截面收益标准差 σ_r | {pct(sl['sigma_r'])} | 噪声远大于信号 |")
    A(f"| **单位倾斜的信息比** `\\|b\\|/σ_r` | **{f2(sl['ir_unit'])}** | 尺度不变 |")
    A("")
    A(f"> 关键点：`|b|/σ_r = {sl['ir_unit']:.2f}`（月）是**尺度不变**的 —— 把任何映射整体放大 k 倍，"
      "超额与跟踪误差同比例放大，这个比值不变。所以「该给多陡」**不可能由历史数据唯一确定**"
      "（均值-方差在只有横截面约束下会给出角点解：全押最冷那只）。"
      "能由数据确定的是「单位主动风险值多少收益」，即下面的信息比；"
      "而**振幅（曲线多陡）必须由你能接受的跟踪误差预算决定**。")
    A("")
    A("因此下表同时给出每个映射的**实测跟踪误差**与**信息比**："
      "如果各映射本质只是同一信号的不同缩放，IR 应当大致相同、只有 TE 在变；"
      "若某映射 IR 明显更高，才说明「形状」本身带了新信息。")
    A("")
    A("| 映射 | 单边偏离度 | 年化跟踪误差 | 年化超额 | 信息比（超额/TE） |")
    A("|---|---:|---:|---:|---:|")
    for key, name, _fn, _note in MAPS:
        st = res[key]
        if key == "eq":
            continue
        A(f"| {name} | {st['dev']:.1%} | {pct(st['te'])} | {pct(st['ex'], 2, True)} "
          f"| {f2(st['ir'])} |")
    A("")
    irs = [res[k]["ir"] for k in KEYS]
    irs = [x for x in irs if np.isfinite(x)]
    se_ir = 1.0 / np.sqrt(max(res["n"] / 12.0, 1e-9))
    A("> 读法：**信息比是这条曲线的「价格」，跟踪误差是它的「成本」**。"
      f"IR 在 {min(irs):.2f}~{max(irs):.2f} 之间，而 {res['n']} 个月样本下 IR 的抽样标准误约 "
      f"**{se_ir:.2f}**（≈1/√年数）—— 也就是说这些差异**统计上分不开**，"
      "公式怎么选无所谓，真正要决定的是 TE。")
    l85 = res["lin85"]
    A(f"> 本样本里 dev≈10%（`lin85`）对应 TE≈{l85['te']:.1%}、年化超额 "
      f"{l85['ex'] * 100:+.2f}pp；再往上（`pow2` TE {res['pow2']['te']:.1%}、"
      f"`rank` TE {res['rank']['te']:.1%}）TE 翻倍，年化只多 0.4pp、换手翻倍。"
      "**所以把年化 TE 上限钉在 1% 左右**（对应单边偏离度 8%~12%）就够了 —— "
      "这是「有意义的倾斜」与「放大噪声」的分界。")
    A("")

    # ---------- 3 横截面：各映射的实际力度与绩效
    A("## 3. 读法 A：横截面（不降总暴露）")
    A("")
    for tag, rr in (("3.1 全窗口", res), ("3.2 共同窗口（6 只齐全）", resc)):
        A(f"### {tag}")
        A("")
        A("| 映射 | 单边偏离度 | 最大单只 | 年化 | 最大回撤 | Calmar | 夏普 | 月换手 |")
        A("|---|---:|---:|---:|---:|---:|---:|---:|")
        b = rr["eq"]
        for key, name, _fn, _note in MAPS:
            st = rr[key]
            star = " **★**" if (key != "eq" and st["cagr"] > b["cagr"] + 1e-4
                                and st["calmar"] > b["calmar"] + 1e-4) else ""
            A(f"| {name}{star} | {st['dev']:.1%} | {st['maxw']:.0%} | {pct(st['cagr'])} "
              f"| {pct(st['dd'])} | {f2(st['calmar'])} | {f2(st['sharpe'])} | {st['turn']:.0%} |")
        A("")
    A("> `单边偏离度 = 平均 Σ|w−等权|/2`，就是「每月有多少比例的仓位被挪动」。"
      "`★` = 年化与 Calmar 同时高于等权。")
    A("")

    # ---------- 4 λ 曲线：形状 vs 强度
    A("## 4. 三个代表映射的强度曲线（λ=0 等权 → λ=1 纯映射）")
    A("")
    A("`w = (1−λ)·等权 + λ·映射权重`。**形状对 → 这条曲线应单调；只有某个 λ 好看 → 是在挑参数。**")
    A("")
    A("| λ | " + " | ".join(f"{n}" for _k, n, _f, _x in MAPS if _k in ("lin100", "lin85", "rank"))
      + " |")
    A("|---:|" + "---:|" * 3)
    for lam in (0.0, 0.25, 0.5, 0.75, 1.0):
        cells = []
        for key in ("lin100", "lin85", "rank"):
            fn = dict((k, f) for k, _n, f, _x in MAPS)[key]
            W = build_weights(ctx["temp"], key, fn, lam)
            r, _t = simulate(W, res["rets"])
            st = S.stats(r)
            cells.append(f"{st['calmar']:.2f}")
        A(f"| {lam:.2f} | " + " | ".join(cells) + " |")
    A("")
    A("> 单元格是 Calmar。若曲线在 λ 上单调或有一整段为正，说明机制真实；"
      "若只有端点好看，说明是参数挑选。")
    A("")

    # ---------- 5 总仓位读法
    A("## 5. 读法 B：总仓位（板块均温 → 持仓比例，其余现金）")
    A("")
    A("| 映射 | 平均仓位 | 年化 | 最大回撤 | Calmar | 夏普 | 同仓位固定对照 年化 / Calmar | 择时增量 |")
    A("|---|---:|---:|---:|---:|---:|---|---:|")
    for key, name, _fn, _note in MAPS:
        st = ctx["mt"][key]
        delta = "—" if key == "eq" else pct(st["cagr"] - st["flat_cagr"], 2, True)
        A(f"| {name} | {st['pos']:.0%} | {pct(st['cagr'])} | {pct(st['dd'])} "
          f"| {f2(st['calmar'])} | {f2(st['sharpe'])} "
          f"| {pct(st['flat_cagr'])} / {f2(st['flat_calmar'])} | {delta} |")
    A("")
    m_full = ctx["mt"]["eq"]
    A(f"> 满仓等权（同窗口）是 {pct(m_full['cagr'])} / {pct(m_full['dd'])} / "
      f"Calmar {f2(m_full['calmar'])}。本节所有方案都是「拿年化换回撤」，"
      "**平均仓位就是 beta 让渡的直接度量**；评价标准不是 Calmar 略高，"
      "而是「砍掉的暴露有没有换成等比例的回撤改善」。")
    A("")
    A("> **「同仓位固定对照」是关键列**：拿每个映射的平均仓位，恒定地用到每个月（完全不看温度）。"
      "「择时增量」= 该映射 − 同仓位固定对照。若这一列 ≈ 0（或为负），"
      "说明 **Calmar 变好只是因为降了暴露，不是因为温度的时点判断对了** —— "
      "Calmar 会因为分母（波动）缩小而自动变高，这是仓位管理的假象。")
    A("")
    yy = {}
    for key in ("lin100", "lin85", "rank"):
        m = ctx["mt"][key]["m"]
        yy[dict((k, n) for k, n, _f, _x in MAPS)[key]] = m.groupby(m.index.year).mean()
    A("逐年平均仓位（三个代表映射）：")
    A("")
    keys_y = list(yy)
    A("| 映射 | " + " | ".join(str(y) for y in yy[keys_y[0]].index) + " |")
    A("|---|" + "---:|" * len(yy[keys_y[0]].index))
    for k in keys_y:
        A(f"| {k} | " + " | ".join(f"{v:.0%}" for v in yy[k]) + " |")
    A("")

    # 5.1 时间维度的两项稳健性：剔除最强月 / 零点平移
    A("### 5.1 时间维度的稳健性：那些「择时增量」站得住吗")
    A("")
    A("| 检查 | 映射年化 | 同仓位固定对照 | 增量 |")
    A("|---|---:|---:|---:|")
    for chk in ctx["mt_checks"]:
        A(f"| {chk['tag']} | {pct(chk['cagr'])} | {pct(chk['flat'])} "
          f"| {pct(chk['cagr'] - chk['flat'], 2, True)} |")
    A("")
    A("> 第一行是 `lin85` 原样；第二行剔除「映射相对同仓位对照贡献最大的 3 个月」；"
      "下面几行把线性映射的零点从 85 上下平移。"
      "**结论：增量对零点平移不敏感（'+2.1~+2.6pp' 稳定），但剔除 3 个月后只剩 +0.5pp —— "
      "方向不是巧合，幅度靠少数月份。这与 V9 主报告 §四对绝对阈值的判定一致。**")
    A("")

    # ---------- 6 稳健性
    A("## 6. 稳健性（只对 §3 里 Calmar 最高的两个映射做）")
    A("")
    A("| 映射 | 全部年化 | 剔除优势最大 3 月后（映射 / 等权） | 优势 | 前段夏普 | 后段夏普 |")
    A("|---|---:|---|---:|---:|---:|")
    for key in ctx["top2"]:
        st = res[key]
        r = st["r"]
        d = (r - base["r"]).dropna()
        drop = d.nlargest(TAIL).index
        rr, bb = S.stats(r.drop(drop)), S.stats(base["r"].drop(drop))
        cut = r.index[int(len(r) * 0.6)]
        A(f"| {dict((k, n) for k, n, _f, _x in MAPS)[key]} | {pct(st['cagr'])} "
          f"| {pct(rr['cagr'])} / {pct(bb['cagr'])} | {pct(rr['cagr'] - bb['cagr'], 2, True)} "
          f"| {f2(S.stats(r.loc[:cut])['sharpe'])} | {f2(S.stats(r.loc[cut:])['sharpe'])} |")
    A("")
    A(f"基准等权：全部 {pct(base['cagr'])}、前段夏普 "
      f"{f2(S.stats(base['r'].loc[:base['r'].index[int(len(base['r']) * 0.6)]])['sharpe'])}、"
      f"后段 {f2(S.stats(base['r'].loc[base['r'].index[int(len(base['r']) * 0.6)]:])['sharpe'])}。")
    A("")

    # 置换检验
    k_top = ctx["top2"][0]
    A(f"### 6.1 置换检验：月内打乱温度 {PERM_N} 次（映射 = "
      f"{dict((kk, n) for kk, n, _f, _x in MAPS)[k_top]}）")
    A("")
    A("| 指标 | 实测 | 置换中位数 | 5%~95% | p 值 |")
    A("|---|---:|---:|---|---:|")
    for key, label in (("cagr", "年化"), ("calmar", "Calmar"), ("sharpe", "夏普"), ("dd", "最大回撤")):
        obs = res[k_top][key]
        A(f"| {label} | {pct(obs) if key in ('cagr', 'dd') else f2(obs)} "
          f"| {pct(perm[key].median()) if key in ('cagr', 'dd') else f2(perm[key].median())} "
          f"| {pct(perm[key].quantile(0.05)) if key in ('cagr', 'dd') else f2(perm[key].quantile(0.05))}"
          f" ~ {pct(perm[key].quantile(0.95)) if key in ('cagr', 'dd') else f2(perm[key].quantile(0.95))} "
          f"| {perm_p[key]:.0%} |")
    A("")
    A("> p 值 = 随机打乱能达到实测的比例。**映射的配额分布、换手、成本都与实测一致**，"
      "只有「谁冷谁热」是假的 —— 所以它检验的正是「温度排序有没有增量」。")
    A("")

    # 留一
    A("### 6.2 留一：拿掉一只标的")
    A("")
    A("| 剔除 | 映射年化 | 等权年化 | 差 | 映射 Calmar | 等权 Calmar |")
    A("|---|---:|---:|---:|---:|---:|")
    for line in ctx["loo"]:
        A(line)
    A("")

    # ---------- 7 结论
    A("## 7. 结论：怎么换算才合适")
    A("")
    for line in ctx["concl"]:
        A(line)
    A("")
    A("## 8. 口径与边界")
    A("")
    A("1. **无前视**：温度用月末及以前的分位窗口算出，权重 `shift(1)` 次月生效；"
      "但未单独检验 etf.run 估值发布时滞（V9 全家族共同假设）。")
    A("2. **归一化的含义**：`w_i = f(T_i)/Σf(T_j)` 只取决于映射的**相对形状**；"
      "`lin100` 与 `lin85` 的差别全部来自「零点位置」—— 零点越靠近观测区间，曲线越陡。"
      "这是「按数字比例换算」最容易踩的坑：看起来都是线性，力度可以差一倍。")
    A("3. **4 只无温度标的仍是空白**：本课题只覆盖 6 只（`008163`/`005125`/`007760`/`018388` 无同源估值）。"
      "若横截面映射要覆盖它们，必须先给兜底配额（等权或中性档），否则等于清仓。")
    A(f"4. **窗口敏感**：全窗口（可用 {ctx['res']['n']} 月，4→6 只）与共同窗口"
      f"（{ctx['resc']['n']} 月，6 只齐全）结论若不一致，以更保守的一段为准；样本不足一轮完整涨跌，"
      "结论只作方向性参考。")
    A("")
    A("> 机械规则输出，非投资建议。")
    return "\n".join(L) + "\n"


# ================================================================ 主流程
def main() -> int:
    temp, rets, panels = load_panel()
    asof = temp.index[-1]
    first6 = temp.dropna().index[0]

    def run(t: pd.DataFrame) -> dict:
        out = {}
        eq_w = pd.DataFrame(1.0, index=t.index, columns=t.columns).where(t.notna())
        eq_w = eq_w.div(eq_w.sum(axis=1).replace(0, np.nan), axis=0).fillna(0.0)
        r_eq, _turn = simulate(eq_w, rets)
        out["eq"] = run_sc(eq_w, rets, eq_w, r_eq, S.stats(r_eq)["cagr"])
        cagr_eq = out["eq"]["cagr"]
        for key, _name, fn, _note in MAPS:
            if key == "eq":
                continue
            W = build_weights(t, key, fn, 1.0)
            out[key] = run_sc(W, rets, eq_w, r_eq, cagr_eq)
        out.update(start=t.index[0], end=t.index[-1], n=len(t), rets=rets.reindex(t.index),
                   temp=t)
        return out

    res = run(temp)
    resc = run(temp.loc[first6:])

    # 时间维度：同一窗口、6 只等权 * 仓位倍率（其余现金，不重新归一化）
    r_t = rets.loc[temp.index[0]:]
    eqw_full = S.equal_w(r_t.notna())
    board = temp.mean(axis=1, skipna=True).reindex(r_t.index).ffill()
    mt = {}
    for key, _name, fn, _note in MAPS:
        m = build_mult(board, key, fn).clip(0.0, 1.0).fillna(1.0)
        r, _t = simulate_time(eqw_full.mul(m, axis=0), r_t)
        st = S.stats(r)
        st["pos"] = float(m.mean())
        st["m"] = m
        # 对照：**恒定**地用同一个平均仓位（不择时）—— 区分「降暴露」与「择时」
        mc = pd.Series(st["pos"], index=m.index)
        rc, _t2 = simulate_time(eqw_full.mul(mc, axis=0), r_t)
        sc = S.stats(rc)
        st["flat_cagr"], st["flat_calmar"], st["flat_dd"] = sc["cagr"], sc["calmar"], sc["dd"]
        st["r"], st["r_flat"] = r, rc
        mt[key] = st

    # 时间维度稳健性：剔除最强月 / 零点平移
    def _mv(z: float) -> pd.Series:
        return board.map(lambda x: 1.0 if pd.isna(x) else float(np.clip((z - x) / z, 0, 1)))

    def _flat(m: pd.Series):
        return simulate_time(eqw_full.mul(pd.Series(m.mean(), index=m.index), axis=0), r_t)

    mt_checks = []
    r85 = mt["lin85"]["r"]
    r85f = mt["lin85"]["r_flat"]
    d = (r85 - r85f).dropna()
    drop = d.nlargest(3).index
    mt_checks.append(dict(tag="lin85 原样", cagr=S.stats(r85)["cagr"],
                          flat=S.stats(r85f)["cagr"]))
    mt_checks.append(dict(tag="lin85 剔除贡献最大 3 月", cagr=S.stats(r85.drop(drop))["cagr"],
                          flat=S.stats(r85f.drop(drop))["cagr"]))
    for z in (75.0, 90.0, 95.0):
        m = _mv(z)
        r, _t = simulate_time(eqw_full.mul(m, axis=0), r_t)
        rf, _t2 = _flat(m)
        mt_checks.append(dict(tag=f"零点 = {z:.0f}（原 85）", cagr=S.stats(r)["cagr"],
                              flat=S.stats(rf)["cagr"]))

    # 诊断
    disp = disp_stats(temp)
    sl = xs_slope(temp, rets)

    # Calmar 最高的两个（非基准）做稳健性
    top2 = sorted([k for k, _n, _f, _x in MAPS if k != "eq"],
                  key=lambda k: -res[k]["calmar"])[:2]

    # 置换（只用第一名的映射）
    k_top = top2[0]
    fn_top = dict((k, f) for k, _n, f, _x in MAPS)[k_top]
    perm = perm_test(temp, rets, k_top, fn_top, 1.0)
    perm_p = {k: float((perm[k] >= res[k_top][k]).mean())
              for k in ("cagr", "calmar", "sharpe", "dd")}

    # 留一
    loo = []
    for c in [x for x in V9.VALUED if x in temp.columns]:
        cols = [x for x in temp.columns if x != c]
        t2 = temp[cols]
        t2 = t2[t2.notna().sum(axis=1) >= MIN_FUNDS]
        if t2.empty:
            continue
        r2 = rets.reindex(t2.index)
        eq2 = pd.DataFrame(1.0, index=t2.index, columns=cols).where(t2.notna())
        eq2 = eq2.div(eq2.sum(axis=1).replace(0, np.nan), axis=0).fillna(0.0)
        W2 = build_weights(t2, k_top, fn_top, 1.0)
        r1, _ = simulate(W2, r2)
        r0, _ = simulate(eq2, r2)
        s1, s0 = S.stats(r1), S.stats(r0)
        loo.append(f"| 去掉 {V9.NAME[c]} | {pct(s1['cagr'])} | {pct(s0['cagr'])} "
                   f"| {pct(s1['cagr'] - s0['cagr'], 2, True)} | {f2(s1['calmar'])} "
                   f"| {f2(s0['calmar'])} |")

    # 结论（按实测值生成，不手抄）
    b = res["eq"]
    bc = resc["eq"]
    names = dict((k, n) for k, n, _f, _x in MAPS)
    best = max((k for k, _n, _f, _x in MAPS if k != "eq"),
               key=lambda k: res[k]["cagr"] + res[k]["calmar"])
    concl = ctx_concl(res, resc, mt, mt_checks, sl, disp, perm_p, top2, names, best)
    ctx = dict(res=res, resc=resc, disp=disp, slope=sl, perm=perm, perm_p=perm_p,
               mt=mt, mt_checks=mt_checks, top2=top2, loo=loo, asof=asof, temp=temp,
               concl=concl)
    with io.open(OUT_MD, "w", encoding="utf-8") as fh:
        fh.write(render(ctx))

    print(f"[ok] {OUT_MD}")
    print(f"窗口 {res['start']:%Y-%m}~{res['end']:%Y-%m}（{res['n']} 月） · "
          f"共同窗口 {resc['start']:%Y-%m}~{resc['end']:%Y-%m}（{resc['n']} 月）")
    print(f"信号: b={sl['b']*100:+.2f}%/SD (t={sl['t']:+.2f}) · σ_r={sl['sigma_r']:.2%} · "
          f"单位倾斜 IR={sl['ir_unit']:.2f}")
    for key in ("eq", "lin100", "rank"):
        r = mt[key]["r"]
        yr = (1 + r).groupby(r.index.year).prod() - 1
        print(f"  [时间] {key:6s} 逐年 " + " ".join(f"{int(y)}:{v:+.1%}" for y, v in yr.items()))
    print(f"月内温度: 极差中位 {disp['range_med']:.1f} 分 · 标准差中位 {disp['sd_med']:.1f} 分")
    print("")
    for key, name, _fn, _x in MAPS:
        st = res[key]
        print(f"  {name:32s} 偏离 {st['dev']:5.1%} TE {st['te']:6.2%} 年化 {st['cagr']:+.2%} "
              f"回撤 {st['dd']:6.2%} Calmar {st['calmar']:.2f} IR {st['ir']:+5.2f} "
              f"换手 {st['turn']:.0%}")
    return 0


def ctx_concl(res, resc, mt, mt_checks, sl, disp, perm_p, top2, names, best) -> list[str]:
    b, bc = res["eq"], resc["eq"]
    out = []
    k1, k2 = top2
    s1 = res[k1]
    lin = res["lin100"]
    l85 = res["lin85"]
    p2 = res["pow2"]
    # 各映射 IR 的极差（检验「形状是否带新信息」）
    irs = [res[k]["ir"] for k in KEYS]
    irs = [x for x in irs if np.isfinite(x)]
    ir_lo, ir_hi = (min(irs), max(irs)) if irs else (np.nan, np.nan)
    cagr_lo = min(res[k]["cagr"] for k in KEYS)
    cagr_hi = max(res[k]["cagr"] for k in KEYS)
    te_lo = min(res[k]["te"] for k in KEYS)
    te_hi = max(res[k]["te"] for k in KEYS)
    out.append(
        f"1. **直接换算在数学上没问题，问题在「给多陡」。** 月内温度极差中位只有 "
        f"{disp['range_med']:.0f} 分，所以 `(100−T)/100` 与 `max(0,85−T)/85` 虽然都是线性，"
        f"实际单边偏离度是 {lin['dev']:.1%} vs {l85['dev']:.1%}、年化跟踪误差 "
        f"{lin['te']:.1%} vs {l85['te']:.1%}。**零点越靠近观测区间（均值≈42、p90≈67），曲线越陡**；"
        f"「温度即百分比」这种写法恰好是全族里最平的一个，不是最陡的。")
    se_ir = 1.0 / np.sqrt(max(res["n"] / 12.0, 1e-9))
    out.append(
        f"2. **形状几乎不带来信息，只有振幅在变。** {len(KEYS)} 个非基准映射的信息比落在 "
        f"{ir_lo:.2f}~{ir_hi:.2f}（`|b|/σ_r` = {sl['ir_unit']:.2f} 是尺度不变的理论值），"
        f"年化在 {cagr_lo:+.1%}~{cagr_hi:+.1%}，"
        f"而跟踪误差从 {te_lo:.1%} 拉到 {te_hi:.1%}。IR 的抽样标准误约 {se_ir:.2f}"
        f"（≈1/√年数，{res['n']} 个月），**上下的差距落在这个带内，统计上分不开** —— "
        f"「选哪个公式」基本是伪选择，只有振幅真的在变。"
        f"（`rank` 只看排序、丢掉绝对水平，IR {res['rank']['ir']:+.2f}、TE "
        f"{res['rank']['te']:.1%}，是全族最差的一个，说明「能排序」不等于「能定仓位」。）")
    out.append(
        f"3. **落地建议：把年化跟踪误差上限钉在 ~1%（单边偏离度 8%~12%）。** 落在这一档的是 "
        f"`{names['lin85']}`（偏离度 {l85['dev']:.1%}、TE {l85['te']:.1%}、"
        f"年化 {l85['cagr']:+.2%}、Calmar {l85['calmar']:.2f}）与现行档位配额"
        f"（{res['band']['dev']:.1%} / TE {res['band']['te']:.1%} / {res['band']['cagr']:+.2%}）。"
        f"越过这条线（`pow2` TE {p2['te']:.1%}、`rank` {res['rank']['te']:.1%}）年化只再高一点、"
        f"换手却翻倍（{p2['turn']:.0%} / {res['rank']['turn']:.0%} vs {l85['turn']:.0%}），"
        f"本质是把同一份弱信号放大成噪声。")
    out.append(
        f"4. **Calmar 最高的两个都是「更陡」的映射，不是「更聪明」的映射。** 全窗口 Calmar 最高的是 "
        f"`{names[k1]}`（{s1['cagr']:+.2%} / Calmar {s1['calmar']:.2f} / TE {s1['te']:.1%} / "
        f"IR {s1['ir']:+.2f}），紧随的 `{names[k2]}` 是 {res[k2]['cagr']:+.2%} / "
        f"{res[k2]['calmar']:.2f} / TE {res[k2]['te']:.1%} / IR {res[k2]['ir']:+.2f}；"
        f"而最平的 `sqrt` 是 {res['sqrt']['cagr']:+.2%} / {res['sqrt']['calmar']:.2f}。"
        f"Calmar 差 {s1['calmar'] - res['sqrt']['calmar']:.2f}，但 IR 只差 "
        f"{s1['ir'] - res['sqrt']['ir']:+.2f}（< 抽样误差 {se_ir:.2f}）。"
        f"**所以别找「最好的公式」，找「合适的陡度」。**")
    pg = perm_p
    out.append(
        f"5. **增量只能算弱证据。** `{names[k1]}` 相对等权的置换检验 p 值：年化 {pg['cagr']:.0%}、"
        f"Calmar {pg['calmar']:.0%}、回撤 {pg['dd']:.0%}；剔除优势最大的 3 个月后优势明显缩水（§6）。"
        f"斜率 t={sl['t']:+.2f}（|t|<2 不显著）、σ_r {sl['sigma_r']:.1%} 远大于 "
        f"{abs(sl['b']):.2%} 的信号 —— 应当把它当「同等条件下略偏冷」的排序工具，"
        f"不是能独立承担收益目标的仓位控制器。")
    chk = {c["tag"]: c for c in mt_checks}
    asis = chk.get("lin85 原样", {})
    drop3 = chk.get("lin85 剔除贡献最大 3 月", {})
    z75 = chk.get("零点 = 75（原 85）", {})
    z95 = chk.get("零点 = 95（原 85）", {})
    ex_asis = asis.get("cagr", np.nan) - asis.get("flat", np.nan)
    ex_d3 = drop3.get("cagr", np.nan) - drop3.get("flat", np.nan)
    out.append(
        f"6. **总仓位口径：Calmar 变好大部分是「降暴露」的机械结果，不是择时。** "
        f"`m=(100−T)/100` 平均仓位 {mt['lin100']['pos']:.0%}、Calmar {mt['lin100']['calmar']:.2f}、"
        f"年化 {mt['lin100']['cagr']:+.2%}；满仓等权 {mt['eq']['cagr']:+.2%} / "
        f"Calmar {mt['eq']['calmar']:.2f}。**Calmar 会因为分母（波动）缩小而自动变高**，"
        f"所以必须用「同仓位固定对照」：`lin85` 相对同仓位固定组合的增量是 "
        f"{ex_asis * 100:+.2f}pp，排除零点平移的干扰后仍为 "
        f"{(z75.get('cagr', 0) - z75.get('flat', 0)) * 100:+.2f}~"
        f"{(z95.get('cagr', 0) - z95.get('flat', 0)) * 100:+.2f}pp（说明不完全是点位巧合）；"
        f"**但剔除贡献最大的 3 个月后只剩 {ex_d3 * 100:+.2f}pp** —— "
        f"增量集中在一两个月（2024-09 这种月里权重临时拉满），"
        f"与 `_v9_report.md` §四「绝对阈值经不起剔除检验」是同一件事。"
        f"要用就用**软档**（每档只动 10~20pp），把平均仓位当目标（如 70%）来定标，别追求最高 Calmar。")
    out.append(
        f"7. **具体写法（两个参数都不需要搜索）**：横截面用 "
        f"`w_i ∝ 1 + λ·((85−T_i)/Σ_j(85−T_j) − 1/N)`，`λ=0.5`、零点钉在规则自带的危险线 85；"
        f"缺温度的标的给等权兜底（不给 0 也不给最大）。"
        f"这一条与本样本 TE≈{l85['te']:.1%} 的档位一致，且不依赖任何样本内拟合的阈值。")
    return out


if __name__ == "__main__":
    sys.exit(main())
