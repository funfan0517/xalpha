#!/usr/bin/env python
# -*- coding: utf-8 -*-
r"""单指标红利策略的通用数据层与回测引擎（V3–V7 共用）

落位依据 AGENTS §8：被 4 个以上策略复用的工具放 `strategies/dividend/` 根目录。

一、为什么限定 7 只标的
----------------------
池内 10 只里有 3 只（`008163` 标普红利低波50、`005125` 标普红利机会、`018388` 港股通高股息）
**没有股息率月度历史**（跟踪指数不在中证官网估值接口 / 港股不做代理）。若各自用自己的池，
「股息率 / 股债利差 / 股债收益比」三个指标覆盖的标的就不同，策略之间不可比。
故 V3–V7 一律用**有股息率历史的 7 只**，RSI 也统一用这 7 只。

二、统一样本
------------
**2020-01 ~ 2026-10（82 个月）**，由两件事共同决定：① 6 只标的在 2020-01 起有净值；
② 股息率分位需要 36 个月预热（序列从 2015-01 起）。
与 `v2_temp` 同窗口，便于横向比较。

三、统一口径
------------
* 频率：月末算信号，**下月生效**（`w.shift(1)`），无前视。
* 收益：基金**累计净值** `totvalue`（含分红再投），与 `rank.py` / `backtest_rank.py` 同源。
* 成本：单边 0.03%/次，按 Σ|Δw| 计（换一次标的=买+卖各计一次）。
* 横截面：每月选 `q` 最低（最便宜）的 top2 等权。

四、核心抽象：贵度 `q ∈ [0, 1]`
------------------------------
把四个指标统一成「**越高越贵**」，这样横截面与时间维度可以用同一套规则：

| 指标 | 原始量 | 贵度 q |
|---|---|---|
| 股息率 dy | 越高越便宜 | `1 − 分位(dy)` |
| 股债利差 spread | 越高越便宜 | `1 − 分位(spread)` |
| 股债收益比 ratio | 越高越便宜 | `1 − 分位(ratio)` |
| RSI14 | **越高越贵（原生刻度）** | `RSI / 100`（不再分位化：RSI 本身已是 0–100 标准化） |

分位为**扩张窗口**（最少 36 个月、上限 120 个月），逐列按每只基金自身历史算。

五、时间维度（板块层）
----------------------
```
板块贵度 q̄ = 7 只 q 的均值
q̄ ≥ 0.80 → 仓位 0（清仓）
0.50 ≤ q̄ < 0.80 → 仓位 0.5（减半）
q̄ < 0.50 → 仓位 1.0（满仓）
```
`RSI` 的 q̄ 用绝对 RSI 均值，等价于「板块 RSI ≥80 清仓、50–80 减半」——
与常见 RSI 70/30 口径的一致性在 `v4_rsi/RULES.md` 里说明并做敏感性。
"""

from __future__ import annotations

import os
import sys

import numpy as np
import pandas as pd

_DIR = os.path.dirname(os.path.abspath(__file__))
if _DIR not in sys.path:
    sys.path.insert(0, _DIR)
os.environ.setdefault("PYTHONIOENCODING", "utf-8")

import pool  # noqa: E402  pylint: disable=wrong-import-position

ROOT = os.path.dirname(os.path.dirname(_DIR))
YIELD_CSV = os.path.join(ROOT, "data", "_div_yield_m.csv")
Y10_CSV = os.path.join(ROOT, "data", "_bt_caches", "bond10y_m.csv")

# (基金代码, 跟踪指数, 简称)
POOL = [
    ("090010", "000922", "中证红利"),
    ("007467", "H30269", "红利低波"),
    ("008115", "930955", "红利低波100"),
    ("007760", "931157", "沪港深红利成长低波"),
    ("016441", "931468", "红利质量"),
    ("561060", "000824", "国企红利"),
    ("561580", "000825", "央企红利"),
]
CODES = [c for c, _, _ in POOL]
NAME = {c: n for c, _, n in POOL}
IDX = {c: i for c, i, _ in POOL}

START = "2020-01-01"     # 统一样本起点（见模块 docstring）
PCT_MIN_N = 36
PCT_MAX_N = 120
FEE = 0.0003
RSI_WIN = 14

# 时间维度档位（贵度 q̄）
Q_OUT, Q_HALF = 0.80, 0.50


# ------------------------------------------------------------------ 数据
def _month_end(s: pd.Series) -> pd.Series:
    return s.resample("ME").last().dropna()


def load_navs() -> pd.DataFrame:
    return pool.load_navs(codes=CODES)


def load_rets(navs=None) -> pd.DataFrame:
    navs = load_navs() if navs is None else navs
    me = navs.resample("ME").last().ffill()
    return me.pct_change().loc[START:]


def _y10() -> pd.Series:
    raw = pd.read_csv(Y10_CSV)
    col = "close" if "close" in raw.columns else raw.columns[-1]
    return pd.Series(pd.to_numeric(raw[col], errors="coerce").to_numpy(),
                     index=pd.to_datetime(raw.iloc[:, 0])).dropna().sort_index()


def _dy() -> pd.DataFrame:
    """指数月度股息率 → 宽表(date × 基金)。"""
    df = pd.read_csv(YIELD_CSV, parse_dates=["date"])
    out = {}
    for code, idx, _ in POOL:
        s = df[df["index_code"] == idx].set_index("date")["dy"]
        if not s.empty:
            out[code] = s.astype(float).sort_index()
    return pd.DataFrame(out)


def rsi_series(s: pd.Series, window=RSI_WIN) -> pd.Series:
    """Wilder RSI（与 `pool.rsi14` 同口径，这里是整段序列）。"""
    d = s.diff()
    au = d.clip(lower=0).ewm(alpha=1 / window, adjust=False).mean()
    ad = (-d).clip(lower=0).ewm(alpha=1 / window, adjust=False).mean()
    return (100 - 100 / (1 + au / ad.replace(0, np.nan))).clip(0, 100)


def load_signal(kind: str, navs=None) -> pd.DataFrame:
    """kind ∈ {dy, spread, ratio, rsi} -> 月末宽表(date × 基金)。"""
    if kind == "rsi":
        navs = load_navs() if navs is None else navs
        return pd.DataFrame({c: _month_end(rsi_series(navs[c].dropna()))
                             for c in navs.columns}).sort_index()
    dy = _dy()
    if kind == "dy":
        return dy
    # 10Y 国债：对齐到股息率的月份并 ffill —— 当月国债尚未更新时沿用上月末，
    # 否则 2026-10 这类「指数已有、国债未更新」的月份会整列变 NaN。
    y10 = _month_end(_y10())
    y10 = y10.reindex(y10.index.union(dy.index)).ffill().reindex(dy.index)
    if kind == "spread":
        return dy.sub(y10, axis=0)
    if kind == "ratio":
        return dy.div(y10, axis=0)
    raise ValueError(f"未知指标: {kind}")


# ------------------------------------------------------------------ 分位 / 贵度
def pct_rank(df: pd.DataFrame, min_n=PCT_MIN_N, max_n=PCT_MAX_N) -> pd.DataFrame:
    """逐列扩张窗口分位（含当前点）。样本不足 min_n → NaN。"""
    out = {}
    for col in df.columns:
        v = df[col].to_numpy(dtype=float)
        r = np.full(len(v), np.nan)
        for i in range(len(v)):
            w = v[max(0, i - max_n + 1):i + 1]
            w = w[~np.isnan(w)]
            if len(w) >= min_n and not np.isnan(v[i]):
                r[i] = float((w < v[i]).mean())
        out[col] = r
    return pd.DataFrame(out, index=df.index)


def cheapness(kind: str, sample=None) -> pd.DataFrame:
    """贵度 q：0 = 最便宜，1 = 最贵。用于**时间维度**（相对自己的历史）。"""
    sig = load_signal(kind)
    q = sig / 100.0 if kind == "rsi" else 1.0 - pct_rank(sig)
    return q.loc[sample[0]:sample[1]] if sample else q.loc[START:]


def xs_key(kind: str, sample=None) -> pd.DataFrame:
    """横截面可比键：**越小越便宜**，用于**横截面**（跨基金比）。

    必须用原始值而不是自身分位：分位是「相对自己历史」，7 只标的里很容易出现并列
    （实测股息率有 5 只同为 0.74），`nsmallest` 会把并列退化成「按列顺序取」——
    那样选出来的组合是随机的，回测差异全是噪声。
    """
    sig = load_signal(kind)
    key = sig if kind == "rsi" else -sig
    return key.loc[sample[0]:sample[1]] if sample else key.loc[START:]


# ------------------------------------------------------------------ 组合
def simulate(w: pd.DataFrame, rets: pd.DataFrame, fee=FEE) -> pd.Series:
    wl = w.shift(1).fillna(0.0)
    port = (wl * rets).sum(axis=1, min_count=1)
    turn = (wl - wl.shift(1).fillna(0.0)).abs().sum(axis=1)
    return (port - turn * fee).dropna()


def stats(r: pd.Series) -> dict:
    nav = (1 + r).cumprod()
    yrs = max(len(r) / 12, 1e-9)
    cagr = nav.iloc[-1] ** (1 / yrs) - 1
    dd = float((nav / nav.cummax() - 1).min())
    vol = float(r.std() * np.sqrt(12))
    return {"cagr": cagr, "dd": dd, "calmar": cagr / abs(dd) if dd else float("nan"),
            "vol": vol, "sharpe": float(r.mean() * 12) / vol if vol else float("nan"),
            "total": float(nav.iloc[-1] - 1), "n": len(r)}


def available(q: pd.DataFrame, rets: pd.DataFrame) -> pd.DataFrame:
    """t 月「信号可得 + 净值已存在」的标的掩码 —— 未成立的基金不计入权重分母。"""
    return q.reindex(rets.index).notna() & rets.notna()


def equal_w(ok: pd.DataFrame) -> pd.DataFrame:
    """按可得标的等权。"""
    n = ok.sum(axis=1).replace(0, np.nan)
    return ok.div(n, axis=0).fillna(0.0)


def top_w(key: pd.DataFrame, ok: pd.DataFrame, k=2) -> pd.DataFrame:
    """key 越小越便宜（`xs_key` 的输出），在可得标的里取最便宜的 k 只等权。"""
    kk = key.reindex(ok.index).where(ok)
    out = pd.DataFrame(0.0, index=kk.index, columns=kk.columns)
    for ts, row in kk.iterrows():
        good = row.dropna()
        if good.empty:
            continue
        pick = good.nsmallest(min(k, len(good))).index
        out.loc[ts, pick] = 1.0 / len(pick)
    return out


def board_q(q: pd.DataFrame, ok: pd.DataFrame = None) -> pd.Series:
    return (q.where(ok) if ok is not None else q).mean(axis=1, skipna=True)


def time_mult(bq: pd.Series, q_out=Q_OUT, q_half=Q_HALF) -> pd.Series:
    """贵度越高仓位越低。"""
    m = pd.Series(1.0, index=bq.index)
    m[bq >= q_half] = 0.5
    m[bq >= q_out] = 0.0
    m[bq.isna()] = 1.0
    return m


def build_combo(rets: pd.DataFrame, kinds: list[str]) -> tuple[pd.DataFrame, pd.DataFrame]:
    """等权组合：q = 各指标自身分位贵度均值；xs = 各指标当月横截面排名均值。"""
    qs = [cheapness(k).reindex(rets.index) for k in kinds]
    q = pd.concat(qs, keys=range(len(qs))).groupby(level=1).mean()
    ranks = [xs_key(k).reindex(rets.index).rank(axis=1, pct=True) for k in kinds]
    xs = pd.concat(ranks, keys=range(len(ranks))).groupby(level=1).mean()
    return q, xs


def build_vote(rets: pd.DataFrame, kinds: list[str], threshold=0.50
               ) -> tuple[pd.DataFrame, pd.DataFrame]:
    """票数制：q = 1 − 便宜票占比；xs 用 −票数（票多=更便宜），平票用贵度均值做第二键。"""
    qs = [cheapness(k).reindex(rets.index) for k in kinds]
    votes = sum((q < threshold).astype(float).fillna(0.0) for q in qs)
    n = len(kinds)
    q = 1.0 - votes / n
    xs = -votes + 1e-3 * pd.concat(qs, keys=range(n)).groupby(level=1).mean()
    return q, xs


def base_key(sims: dict) -> str:
    """基准方案的键名（`run_variants` 的 base_label 可定制，池只数不同时标签要跟着变）。"""
    return next(n for n in sims if n.startswith("基准"))


def run_variants(q: pd.DataFrame, rets: pd.DataFrame, xs: pd.DataFrame = None,
                 bq=None, k=2, q_out=Q_OUT, q_half=Q_HALF,
                 mult_override: pd.Series = None,
                 base_label: str = "基准：7 只等权") -> dict:
    """单指标标准三件套 + 基准。返回 (sims, 基准权重, 仓位倍率)。

    q      : 自身分位贵度（时间维度）
    xs     : 横截面可比键（越小越便宜），缺省时退回 q
    q_out  : 清仓线；q_half: 减半线（RSI 用 0.70/0.50 对齐文献，其余 0.80/0.50）
    mult   : 直接给定仓位倍率（组合策略用，覆盖 bq/阈值）
    base_label : 基准行名（标准池 7 只；V4 用自己的 5 只池，标签随之改）
    """
    ok = available(q, rets)
    bq = board_q(q, ok) if bq is None else bq
    base_w = equal_w(ok)
    mult = time_mult(bq, q_out, q_half) if mult_override is None else mult_override
    top = top_w(q if xs is None else xs, ok, k)
    sims = {
        base_label: simulate(base_w, rets),
        "时间：板块贵度择时": simulate(base_w.mul(mult, axis=0), rets),
        f"横截面：最便宜 {k} 只": simulate(top, rets),
        f"横截面 top{k} + 时间择时": simulate(top.mul(mult, axis=0), rets),
    }
    return sims, base_w, mult


# ------------------------------------------------------------------ 稳健性
def robustness(sims: dict) -> pd.DataFrame:
    """剔除「相对基准贡献最大」的 N 个月后，各方案还能剩多少。"""
    base = sims[base_key(sims)]
    rows = []
    for name, r in sims.items():
        if name.startswith("基准"):
            continue
        diff = (r - base).dropna()
        row = {"方案": name}
        for k in (0, 3, 5):
            drop = diff.nlargest(k).index if k else []
            row[f"剔除{k}"] = stats(r.drop(drop))["cagr"]
        rows.append(row)
    return pd.DataFrame(rows)


def yearly(sims: dict) -> pd.DataFrame:
    df = pd.DataFrame(sims)
    return (1 + df).groupby(df.index.year).prod() - 1


# ------------------------------------------------------------------ 报告
def fmt_pct(x, digits=1) -> str:
    return "—" if x is None or (isinstance(x, float) and np.isnan(x)) else f"{x * 100:.{digits}f}%"


def render_report(title: str, subtitle: list[str], q: pd.DataFrame, raw: pd.DataFrame,
                  sims: dict, meta: dict, rets: pd.DataFrame = None,
                  xs: pd.DataFrame = None, q_out=Q_OUT, q_half=Q_HALF,
                  codes: list[str] = None, name: dict = None) -> str:
    """标准报告骨架：规则 → 当前读数 → 回测 → 分年度 → 稳健性 → 读法。

    codes / name : 报告第一节点名的池（V4 用自有的 5 只，其余用标准 7 只）
    """
    codes = codes or CODES
    name = name or NAME
    L = [f"# {title}", ""]
    L += [f"> {s}" for s in subtitle]
    L.append("")

    # 一、当前读数
    last = q.index[-1]
    order = {}
    if xs is not None and last in xs.index:
        row = xs.loc[last].dropna()
        order = {c: i + 1 for i, c in enumerate(row.sort_values().index)}
    L += ["## 一、当前读数", "",
          "| 标的 | 原始值 | 自身分位贵度 q | 横截面便宜度排名 |", "|---|---:|---:|---:|"]
    for c in codes:
        if c not in q.columns or np.isnan(q[c].iloc[-1]):
            continue
        rv = raw[c].iloc[-1] if c in raw.columns else float("nan")
        rk = f"{order[c]}/{len(order)}" if c in order else "—"
        L.append(f"| {name.get(c, c)}({c}) | {meta['unit_fmt'](rv)} | {q[c].iloc[-1]:.2f} | {rk} |")
    L += ["", f"> 板块贵度（{len(codes)} 只 q 均值）**{board_q(q).iloc[-1]:.2f}**"
              f"（阈值 {q_half:.2f} 减半 / {q_out:.2f} 清仓）。"
              "**q 用于时间维度（相对自己历史），横截面排名用原始值（跨基金可比）** —— "
              "两者不能用同一个数，否则并列会让选择退化成按列序取。", ""]

    # 二、回测
    L += ["## 二、回测（2020-01 ~ 2026-10，82 月，成本单边 0.03%）", "",
          "| 方案 | 年化 | 最大回撤 | Calmar | 波动 | 夏普 | 累计 | 平均仓位 |",
          "|---|---:|---:|---:|---:|---:|---:|---:|"]
    if rets is not None:
        ok = available(q, rets)
        base_w = equal_w(ok)
        mult = time_mult(board_q(q, ok), q_out, q_half)
    else:
        base_w, mult = equal_w(q.notna()), time_mult(board_q(q), q_out, q_half)
    for name, r in sims.items():
        s = stats(r)
        pos = float(base_w.mul(mult, axis=0).sum(axis=1).mean()) if "时间" in name else 1.0
        L.append(f"| {name} | {fmt_pct(s['cagr'])} | {fmt_pct(s['dd'])} | {s['calmar']:.2f} | "
                 f"{fmt_pct(s['vol'])} | {s['sharpe']:.2f} | {fmt_pct(s['total'], 0)} | "
                 f"{pos:.0%} |")
    n_switch = int((mult.diff().abs() > 1e-9).sum())
    L += ["", f"- 时间维度仓位倍率变动 **{n_switch} 次** / 82 个月；"
              f"满仓 {int((mult >= 0.99).sum())} 月、减半 {int(((mult > 0.01) & (mult < 0.99)).sum())} 月、"
              f"清仓 {int((mult <= 0.01).sum())} 月。", ""]

    # 三、分年度
    y = yearly(sims)
    L += ["## 三、分年度收益", "", "| 年份 | " + " | ".join(y.columns) + " |",
          "|---|" + "---:|" * len(y.columns)]
    for ts, row in y.iterrows():
        L.append(f"| {int(ts)} | " + " | ".join(fmt_pct(v) for v in row.values) + " |")
    L.append("")

    # 四、稳健性
    rb = robustness(sims)
    L += ["## 四、稳健性（剔除相对基准贡献最大的 N 个月后的年化）", "",
          "| 方案 | 全部 | 剔除 3 月 | 剔除 5 月 |", "|---|---:|---:|---:|"]
    for _, row in rb.iterrows():
        L.append(f"| {row['方案']} | {fmt_pct(row['剔除0'])} | {fmt_pct(row['剔除3'])} | "
                 f"{fmt_pct(row['剔除5'])} |")
    L += ["", f"- 基准同口径：全部 {fmt_pct(stats(sims[base_key(sims)])['cagr'])}。", ""]
    return "\n".join(L)


def conclusion(sims: dict, note: str = "") -> list[str]:
    """自动结论：以 Calmar 排序，并标注是否只是在「降暴露」。"""
    base = stats(sims[base_key(sims)])
    ranked = sorted(((n, stats(r)) for n, r in sims.items() if not n.startswith("基准")),
                    key=lambda x: -x[1]["calmar"])
    best_name, best = ranked[0]
    L = ["## 五、读法", ""]
    if best["calmar"] <= base["calmar"]:
        L.append(f"- **没有任何变体跑赢等权基准**（最好的是 {best_name}，Calmar {best['calmar']:.2f} "
                 f"vs 基准 {base['calmar']:.2f}）→ 这个指标单独用没有增量。")
    else:
        L.append(f"- 最好的是 **{best_name}**：年化 {fmt_pct(best['cagr'])}、回撤 {fmt_pct(best['dd'])}、"
                 f"Calmar {best['calmar']:.2f}（基准 {base['calmar']:.2f}）。")
        diff = (sims[best_name] - sims[base_key(sims)]).dropna()
        top3 = diff.nlargest(3)
        L.append(f"- 但 {len(diff)} 个月里，超额集中在少数月份："
                 + "、".join(f"{ts:%Y-%m} {v * 100:+.1f}pp" for ts, v in top3.items())
                 + "；剔除这 3 个月后年化 "
                 f"{fmt_pct(stats(sims[best_name].drop(diff.nlargest(3).index))['cagr'])}"
                 f"（基准同口径 {fmt_pct(stats(sims[base_key(sims)].drop(diff.nlargest(3).index))['cagr'])}）。")
    if note:
        L.append(f"- {note}")
    return L


def write(path: str, text: str) -> str:
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as fh:
        fh.write(text)
    return path


if __name__ == "__main__":
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    rr = load_rets()
    print(f"净值收益 {rr.index[0]:%Y-%m} ~ {rr.index[-1]:%Y-%m} · {rr.shape[1]} 只 · {len(rr)} 月")
    for k in ("dy", "spread", "ratio", "rsi"):
        q = cheapness(k)
        sims, _, _ = run_variants(q, rr, xs_key(k))
        base = stats(sims["基准：7 只等权"])
        line = " ".join(f"{n.split('：')[-1][:6]} {s['calmar']:.2f}"
                        for n, s in ((n, stats(r)) for n, r in sims.items()))
        print(f"{k:7s} 当前板块贵度 {board_q(q).iloc[-1]:.2f} | 基准 Calmar {base['calmar']:.2f} | {line}")
