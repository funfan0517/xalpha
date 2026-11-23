# -*- coding: utf-8 -*-
"""组合层实测：把 §9 的「每项最优策略」叠到永久组合的基准权重上，能不能加分、比例要不要改。

回答两个问题，**只做同窗口同口径的比较，不重写任何规则**：

1. **组合后表现** —— 现基准权重（30/20/10/10/10/10/2.5/5/2.5）下，
   「每项按 §9 的最优策略择时」 vs 「每项一直拿着」，组合层差多少。
2. **比例要不要改** —— 债券权重 15~35% 的灵敏度曲线（其余 8 项按 §1 的比例分摊余量），
   看夏普 / 回撤 / 风险贡献怎么随它移动。**只给灵敏度，不扫最优**。

数据全部来自各策略**自己的引擎与产物**（不重算规则）：
- 五项 vol_legs 腿：`v14/v16/v17/v18/v20` 的 `engine.py`（→ `_top3_bridge` 的原生引擎），
  `engine.run(px, engine.weight(px))` 得到该腿「满额择时」的净值；
- 债腿：`bond_cash/v7_risk_budget/engine.py` 的 `backtest(navs)["ret"]`（债腿 30pp 内部，含内部调仓成本）；
- 红利腿：`dividend/v4_rsi` 的 `_single_signal.run_variants(...)` 的「横截面 top2 + 时间择时」月收益；
- 现金腿：1.40% 常数（与各策略的空仓假设同）。

**频率 = 月频**（红利腿只有月频、永久组合自己的回测也是月频）：日频收益按自然月复利成月收益，
组合层每月再平衡回目标权重。所以本报告的波动/回撤会比日频口径略小，且**不是**每日盯市的真回撤。

口径边界（读数字前必须先看）：
- 共同样本受 `011983`（债腿）成立 + 60 日预热限制，只有约 5 年；首尾不完整月已剔除。
- 五项 vol 腿的价格是**真实指数**（黄金 = 场内 518880），与永久组合的场外联接/QDII 执行口径不同。
- 成本：vol 腿单边 10bp、债腿 10bp、红利腿单边 3bp（各策略自身口径）；组合层月度回锚不计费。
- 「一直拿着」的债腿取 **全利率 35/65**（v7 的同门基准），不是 §1 的短/中/长三等分。

用法::

    python strategies/permanent/research/combo_timed/_combo_timed.py
"""
from __future__ import annotations

import contextlib
import importlib
import importlib.util
import json
import math
import os
import sys

import numpy as np
import pandas as pd

try:
    sys.stdout.reconfigure(encoding="utf-8")  # type: ignore[attr-defined]
except Exception:  # noqa: BLE001
    pass

_HERE = os.path.dirname(os.path.abspath(__file__))       # .../permanent/research/combo_timed
_RES = os.path.dirname(_HERE)                            # .../permanent/research
_PERM = os.path.dirname(_RES)                            # .../permanent
_STRAT = os.path.dirname(_PERM)                          # .../strategies
_ROOT = os.path.dirname(_STRAT)                          # 仓库根
_VOL = os.path.join(_STRAT, "vol_legs")
_BOND = os.path.join(_STRAT, "bond_cash", "v7_risk_budget")
_DIV = os.path.join(_STRAT, "dividend", "v4_rsi")
_DIV_ROOT = os.path.join(_STRAT, "dividend")
for _p in (_VOL, _ROOT):
    if _p not in sys.path:
        sys.path.insert(0, _p)

OUT = os.path.join(_HERE, "_combo_timed.md")
TD = 252
CASH_ANNUAL = 0.014
RF = 0.014


@contextlib.contextmanager
def isolated(dirs, names):
    """隔离 sys.path / sys.modules（仓库里有 5 个 data.py、多个 rule/engine）。"""
    old_path = sys.path[:]
    saved = {n: sys.modules.pop(n) for n in names if n in sys.modules}
    for d in dirs:
        sys.path.insert(0, d)
    try:
        yield
    finally:
        sys.path[:] = old_path
        for n in names:
            sys.modules.pop(n, None)
        sys.modules.update(saved)


def _load_engine(path, name):
    """按文件路径加载某个变种的 engine.py（避免 `import engine` 串味）。"""
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _monthly(daily_ret):
    """日频收益 -> 月频收益（自然月复利）。"""
    r = daily_ret.dropna()
    g = r.groupby(r.index.to_period("M"))
    m = g.apply(lambda x: float((1.0 + x).prod() - 1.0))
    m.index = m.index.to_timestamp("M")
    return m


def _monthly_expo(daily_h):
    """日频持仓 -> 月均持仓（用于「平均实际暴露」）。"""
    h = daily_h.dropna()
    g = h.groupby(h.index.to_period("M"))
    m = g.mean()
    m.index = m.index.to_timestamp("M")
    return m


# ---------------------------------------------------------------- 各腿加载
VOL_LEGS = [
    ("a500", "中证A500", "v14_a500_e2"),
    ("hs300", "沪深300", "v15_hs300_min2"),
    ("ndx", "纳指100", "v16_ndx_sw"),
    ("spx", "标普500", "v17_spx_v12"),
    ("gold", "黄金", "v20_gold_v5"),
    ("kc50", "科创50", "v18_kc50_e2tv"),
]


def load_vol_leg(sub):
    """-> (择时月收益, 一直拿着月收益, 月均持仓)。"""
    eng = _load_engine(os.path.join(_VOL, sub, "engine.py"), f"combo_{sub}")
    px = eng.load()
    hold = eng.weight(px)                       # t 日收盘决策
    nav_timed = eng.run(px, hold)
    return (_monthly(nav_timed.pct_change()),
            _monthly(px.pct_change()),
            _monthly_expo(hold))


def load_bond():
    """-> (v7 ERC 择时月收益, 全利率 35/65 月收益)。债腿 30pp 内部。"""
    with isolated([_BOND], ("engine", "rule", "data")):
        eng = importlib.import_module("engine")
        cache = json.load(open(os.path.join(_BOND, "data", "_nav_cache.json"), encoding="utf-8"))
        navs = {c: pd.Series(node["tot"], index=pd.to_datetime(node["dates"]), dtype="float64")
                .sort_index() for c, node in cache["series"].items()}
        bt = eng.backtest(navs)                       # 月末 ERC，T+1 承担，单边 10bp
        rate = eng.backtest(navs, weights={"rate": 1.0})
    return _monthly(bt["ret"]), _monthly(rate["ret"])


def load_div():
    """-> (V4「横截面 top2 + 时间择时」月收益, 10 只等权月收益)。"""
    names = ("rule", "pool", "_single_signal", "fit", "universe", "data")
    with isolated([_DIV, _DIV_ROOT], names):
        rule = importlib.import_module("rule")
        S = importlib.import_module("_single_signal")
        pool = importlib.import_module("pool")
        navs = pool.load_navs(codes=rule.CODES)
        rets = S.load_rets(navs)
        raw = S.load_signal(rule.SIGNAL, navs).reindex(rets.index)
        q, xs = (raw / 100.0).loc[S.START:], raw.loc[S.START:]
        sims, _, _ = S.run_variants(q, rets, xs, k=rule.TOP_K, q_out=rule.Q_OUT,
                                    q_half=rule.Q_HALF, base_label=rule.BASE_LABEL)
        best = sims[f"横截面 top{rule.TOP_K} + 时间择时"]
        base = sims[S.base_key(sims)]
    return best.dropna(), base.dropna()


# ---------------------------------------------------------------- 组合层
def perf(r):
    r = r.dropna()
    n = len(r)
    yrs = n / 12.0
    nav = (1.0 + r).cumprod()
    ann = float(nav.iloc[-1] ** (1.0 / yrs) - 1.0)
    vol = float(r.std() * math.sqrt(12))
    mdd = float((nav / nav.cummax() - 1.0).min())
    roll = (1.0 + r).rolling(12).apply(np.prod, raw=True) - 1.0
    return dict(n=n, ann=ann, vol=vol, mdd=mdd,
                sharpe=(ann - RF) / vol if vol > 1e-9 else np.nan,
                calmar=ann / abs(mdd) if mdd < 0 else np.nan,
                worst12=float(roll.min()) if roll.notna().any() else np.nan,
                worst1=float(r.min()),
                total=float(nav.iloc[-1] - 1.0))


ITEMS = ["bond", "div", "ndx", "spx", "gold", "cash", "a500", "kc50", "hs300"]
LABEL = {"bond": "短中长期国债", "div": "红利低波", "ndx": "纳指100", "spx": "标普500",
         "gold": "黄金", "cash": "货币基金", "a500": "中证A500", "kc50": "科创50",
         "hs300": "沪深300"}
# §1 现基准（2026-11-23 第二版，用户改）：货基 10 / A500 2.5 / 科创50 5 / 沪深300 2.5
BASE = {"bond": 30.0, "div": 20.0, "ndx": 10.0, "spx": 10.0, "gold": 10.0,
        "cash": 10.0, "a500": 2.5, "kc50": 5.0, "hs300": 2.5}
# 同日第一版（已撤回）：货基 0 / A500 5 / 科创50 10 / 沪深300 5
PREV = {"bond": 30.0, "div": 20.0, "ndx": 10.0, "spx": 10.0, "gold": 10.0,
        "cash": 0.0, "a500": 5.0, "kc50": 10.0, "hs300": 5.0}
# 11-23 之前的原始 8 项：货基 10 / A500 5 / 科创50 5 / 无沪深300
ORIG = {"bond": 30.0, "div": 20.0, "ndx": 10.0, "spx": 10.0, "gold": 10.0,
        "cash": 10.0, "a500": 5.0, "kc50": 5.0, "hs300": 0.0}
# 微调：红利 20→15，余 5pp 给 A500 +2.5 / 科创 +2.5（削单一风险源）
TW_DIV = {"bond": 30.0, "div": 15.0, "ndx": 10.0, "spx": 10.0, "gold": 10.0,
          "cash": 10.0, "a500": 5.0, "kc50": 7.5, "hs300": 2.5}


def bond_shift(b):
    """把债券权重设成 b，余量按 §1 其余 8 项的比例分摊（现金不另行固定）。"""
    rest = {k: v for k, v in BASE.items() if k != "bond"}
    tot = sum(rest.values())
    out = {k: v * (100.0 - b) / tot for k, v in rest.items()}
    out["bond"] = float(b)
    return out


def compose(rets, expo, w, timed=True):
    """月度组合：r_p = Σ w_i × r_i；w 为 pp。timed=False 时用「一直拿着」收益。"""
    cols = [c for c in ITEMS]
    R = pd.DataFrame({c: (rets[c] if timed else STATIC[c]) for c in cols}).dropna()
    wv = np.array([w[c] / 100.0 for c in cols])
    r = R.values @ wv
    r = pd.Series(r, index=R.index)
    exp = None
    if timed:
        E = pd.DataFrame({c: expo[c] for c in cols}).reindex(R.index)
        exp = pd.Series(E.fillna(1.0).values @ wv, index=R.index)
    return r, exp


def compose_drift(rets, w, timed=True):
    """买入后**不再平衡**（各项市值自己漂）：组合净值 = Σ w_i × nav_i。"""
    cols = list(ITEMS)
    R = pd.DataFrame({c: (rets[c] if timed else STATIC[c]) for c in cols}).dropna()
    wv = np.array([w[c] / 100.0 for c in cols])
    navs = (1.0 + R).cumprod()
    pre = pd.DataFrame([[1.0] * len(cols)], index=[R.index[0] - pd.offsets.MonthEnd(1)],
                       columns=cols)
    navs = pd.concat([pre, navs])
    port = (navs.values * wv).sum(axis=1)
    return pd.Series(port, index=navs.index).pct_change().dropna()


def risk_contrib(rets, w, timed=True):
    cols = list(ITEMS)
    R = pd.DataFrame({c: (rets[c] if timed else STATIC[c]) for c in cols}).dropna()
    cov = R.cov().values * 12.0
    wv = np.array([w[c] / 100.0 for c in cols])
    sw = cov @ wv
    tot = float(wv @ sw)
    return pd.Series(wv * sw / tot if tot > 0 else np.zeros(len(cols)), index=cols)


def fmt_pct(x):
    return "—" if not np.isfinite(x) else f"{x * 100:+.2f}%"


def main():
    global STATIC
    STATIC = {}
    print("[combo] 加载各腿（原生引擎）…")
    timed, expo = {}, {}
    for key, _name, sub in VOL_LEGS:
        t, s, e = load_vol_leg(sub)
        timed[key], STATIC[key], expo[key] = t, s, e
        print(f"  {key:5s} {sub:18s} {t.index[0]:%Y-%m}~{t.index[-1]:%Y-%m} "
              f"平均持仓 {e.mean():.0%}")
    timed["bond"], STATIC["bond"] = load_bond()
    expo["bond"] = pd.Series(1.0, index=timed["bond"].index)
    print(f"  bond  v7_risk_budget    {timed['bond'].index[0]:%Y-%m}~{timed['bond'].index[-1]:%Y-%m}")
    timed["div"], STATIC["div"] = load_div()
    expo["div"] = pd.Series(1.0, index=timed["div"].index)   # 仓位倍率已含在收益里
    print(f"  div   v4_rsi            {timed['div'].index[0]:%Y-%m}~{timed['div'].index[-1]:%Y-%m}")
    idx = timed[ITEMS[0]].index
    for k in ITEMS[1:]:
        if k == "cash":                       # 现金腿在窗口确定后才构造
            continue
        idx = idx.intersection(timed[k].index)
    idx = idx.sort_values()
    # 首尾月可能不完整（各腿起点不同 / 最新月未走完）→ 丢掉
    win = idx[1:-1]
    cash_m = (1.0 + CASH_ANNUAL) ** (1.0 / 12.0) - 1.0
    timed["cash"] = pd.Series(cash_m, index=win)
    STATIC["cash"] = pd.Series(cash_m, index=win)
    expo["cash"] = pd.Series(1.0, index=win)
    for k in ITEMS:
        timed[k] = timed[k].reindex(win)
        STATIC[k] = STATIC[k].reindex(win)
        expo[k] = expo[k].reindex(win)
    print(f"[combo] 共同窗口 {win[0]:%Y-%m} ~ {win[-1]:%Y-%m}（{len(win)} 月）")

    # ---------------- 组合层：现基准（静态/择时）+ 同日第一版 + 原始 8 项 + 微调
    rows = []
    for timed_flag, name in ((False, "一直拿着"), (True, "按 §9 择时")):
        r, exp = compose(timed, expo, BASE, timed=timed_flag)
        rows.append(("§1 现基准（货基 10 / A500 2.5 / 科创50 5 / 沪深300 2.5）", name,
                     perf(r), exp))
    for tag, w in (("同日第一版·已撤回（货基 0 / 科创50 10 / 沪深300 5）", PREV),
                   ("11-23 前原始 8 项（货基 10 / A500 5 / 科创50 5 / 无沪深300）", ORIG),
                   ("微调：红利 20→15（A500/科创各 +2.5）", TW_DIV)):
        r, exp = compose(timed, expo, w)
        rows.append((tag, "按 §9 择时", perf(r), exp))

    # ---------------- 债券权重灵敏度（其余 7 项按比例分摊）
    sens = []
    for b in (15.0, 20.0, 25.0, 30.0, 35.0):
        w = bond_shift(b)
        r, exp = compose(timed, expo, w)
        p = perf(r)
        rc = risk_contrib(timed, w)
        sens.append((b, w, p, rc, float(exp.mean())))

    # ---------------- 现行权重下：逐项表现 / 风险贡献
    w0 = BASE
    per_item = []
    for k in ITEMS:
        pt = perf(timed[k])
        ps = perf(STATIC[k])
        per_item.append((k, pt, ps, float(expo[k].mean())))
    rc0 = risk_contrib(timed, w0)
    rc0s = risk_contrib(timed, w0, timed=False)
    rc_old = risk_contrib(timed, ORIG)
    # 2026-11-23 第二版改动的近似归因：Δw × (该腿年化 − 现金 1.40%)，只列权重真变了的腿
    def _ann(s):
        s = s.dropna()
        return float((1.0 + s).prod() ** (12.0 / len(s)) - 1.0) if len(s) else np.nan

    attr = []
    for k in ITEMS:
        dw = (BASE[k] - ORIG[k]) / 100.0
        if abs(dw) < 1e-9:
            continue
        r_k = _ann(timed[k])
        attr.append((k, dw, r_k, dw * (r_k - CASH_ANNUAL)))
    r0, exp0 = compose(timed, expo, w0)
    p0 = perf(r0)
    r0s, _ = compose(timed, expo, w0, timed=False)
    p0s = perf(r0s)
    # 再平衡频率：每月回锚 vs 买入后不再平衡（权重自己漂）
    drift_t = perf(compose_drift(timed, w0))
    drift_s = perf(compose_drift(timed, w0, timed=False))
    # 分年度（现行权重）
    yr_t = (1.0 + r0).groupby(r0.index.year).apply(lambda x: float(x.prod() - 1.0))
    yr_s = (1.0 + r0s).groupby(r0s.index.year).apply(lambda x: float(x.prod() - 1.0))

    L = [
        "# 永久组合 · 「每项最优策略」叠加实测（组合层）",
        "",
        f"> 由 `strategies/permanent/research/combo_timed/_combo_timed.py` 生成（{pd.Timestamp.now():%Y-%m-%d}）。"
        "各腿逐日权重直接调各策略**原生引擎**（`vol_legs/_top3_bridge`、`bond_cash/v7_risk_budget`、"
        "`dividend/v4_rsi`），不重写规则；月频，首尾不完整月已剔除。",
        "",
        "## 0. 口径",
        "",
        f"- 共同窗口 **{win[0]:%Y-%m} ~ {win[-1]:%Y-%m}（{len(win)} 个月）** —— 受债腿 `011983` 成立 + 60 日预热限制，"
        f"这是 {len(ITEMS)} 项同时可用的最长样本。",
        "- 「按 §9 择时」= 每项用 `strategies/permanent/README.md` §9 的最优策略仓位（0~100%），"
        "空仓部分按 1.40% 计息；「一直拿着」= 同一条价格序列满仓持有（债腿 = v7 的全利率 35/65 基准）。",
        "- 月频组合每月回锚到目标权重、**不计组合层调仓费**；各腿自身的调仓费已在各自的收益里。",
        "- 波动 = 月收益 ×√12；最大回撤用**月末净值**（比日频口径浅）；最差 12 个月 = 滚动 12 月收益最小值。",
        "- vol 腿价格是真实指数（黄金 = 518880），与场外联接/QDII 的执行口径不同，不是实盘对账数。",
        "",
        "## 1. 组合层结果",
        "",
        "| 权重方案 | 口径 | 年化 | 波动 | 最大回撤 | 夏普 | Calmar | 最差 12 月 | 最差单月 | 实际投入 均值（区间） |",
        "|---|---|---:|---:|---:|---:|---:|---:|---:|---:|",
    ]
    for tag, name, p, exp in rows:
        e = (f"{exp.mean():.0%}（{exp.min():.0%}~{exp.max():.0%}）"
             if exp is not None else "100%")
        L.append(f"| {tag} | {name} | {fmt_pct(p['ann'])} | {p['vol'] * 100:.2f}% | "
                 f"{fmt_pct(p['mdd'])} | {p['sharpe']:.2f} | {p['calmar']:.2f} | "
                 f"{fmt_pct(p['worst12'])} | {fmt_pct(p['worst1'])} | {e} |")
    L += [
        "",
        "> 「实际投入」= Σ 权重 × 该项平均持仓，括号内为历史**最低~最高**。与 100% 的差额就是被动躺在"
        "货基里的钱（现基准 = 10pp 独立货基 + 择时腿空仓两道缓冲）。",
        "",
        "**再平衡频率（现行权重）**：",
        "",
        "| 口径 | 年化 | 波动 | 最大回撤 | 夏普 |",
        "|---|---:|---:|---:|---:|",
    ]
    for name, p in (("按 §9 择时 · 每月回锚", rows[1][2]),
                    ("按 §9 择时 · 不回锚", drift_t),
                    ("一直拿着 · 每月回锚", rows[0][2]),
                    ("一直拿着 · 不回锚", drift_s)):
        L.append(f"| {name} | {fmt_pct(p['ann'])} | {p['vol'] * 100:.2f}% | "
                 f"{fmt_pct(p['mdd'])} | {p['sharpe']:.2f} |")
    L += [
        "",
        "> 两者差额 = **再平衡溢价**（每月把涨多的卖回、跌多的补回）。真实执行按 §2/§8.1-④ 的「偏离 >5pp 才调」，"
        "更接近「不回锚」那一行，所以**保守读法取不回锚的数字**。",
        "",
        "**分年度（现行权重）**：",
        "",
        "| 年份 | 一直拿着 | 按 §9 择时 | 差额 |",
        "|---|---:|---:|---:|",
    ]
    for y in sorted(set(int(v) for v in yr_s.index) | set(int(v) for v in yr_t.index)):
        a = float(yr_s.get(y, np.nan))
        b = float(yr_t.get(y, np.nan))
        diff = b - a
        L.append(f"| {y} | {fmt_pct(a)} | {fmt_pct(b)} | "
                 f"{fmt_pct(diff) if np.isfinite(diff) else '—'} |")
    L += [
        "",
        "**2026-11-23 第二版（A500 5→2.5 / 新增沪深300 2.5，即 A股大盘腿对半分）的近似归因**"
        "（Δw × 该腿同期年化 − 现金 1.40%，不含再平衡与复利交叉项）：",
        "",
        "| 项 | 权重变化 | 该腿择时年化（同窗口） | 近似贡献 |",
        "|---|---:|---:|---:|",
    ]
    for k, dw, r_k, c in attr:
        L.append(f"| {LABEL[k]} | {dw * 100:+.1f}pp | {fmt_pct(r_k)} | {c * 100:+.2f}pp/年 |")
    tot_attr = sum(x[3] for x in attr)
    L += [
        f"| **合计** | — | — | **{tot_attr * 100:+.2f}pp/年** |",
        "",
        "> 归因说明：这一版只把「A股大盘腿」从 A500 独占 5pp 拆成 A500 / 沪深300 各 2.5pp，"
        "两条腿同窗择时年化几乎一样（"
        f"{fmt_pct(attr[0][2])} vs {fmt_pct(attr[1][2])}）、月收益相关 0.73，所以**组合层近似中性**。"
        "它的意义是分散单一指数编制/成分漂移的风险，不是提高收益。",
        "",
        "## 2. 债券权重灵敏度（余量按 §1 其余 8 项的比例分摊）",
        "",
        "| 债腿权重 | 年化 | 波动 | 最大回撤 | 夏普 | 最差 12 月 | 平均实际投入 | 债腿风险贡献 |",
        "|---:|---:|---:|---:|---:|---:|---:|---:|",
    ]
    for b, w, p, rc, e in sens:
        L.append(f"| {b:.0f}% | {fmt_pct(p['ann'])} | {p['vol'] * 100:.2f}% | {fmt_pct(p['mdd'])} | "
                 f"{p['sharpe']:.2f} | {fmt_pct(p['worst12'])} | {e:.1%} | {rc['bond']:.1%} |")
    L += [
        "",
        "## 3. 逐项：择时 vs 一直拿着（同一窗口、同一价格序列）",
        "",
        "| 项 | 基准 | 择时 年化 | 择时 回撤 | 择时 夏普 | 一直拿着 年化 | 一直拿着 回撤 | 一直拿着 夏普 | 平均持仓 |",
        "|---|---:|---:|---:|---:|---:|---:|---:|---:|",
    ]
    for k, pt, ps, e in per_item:
        L.append(f"| {LABEL[k]} | {w0[k]:g}% | {fmt_pct(pt['ann'])} | {fmt_pct(pt['mdd'])} | "
                 f"{pt['sharpe']:.2f} | {fmt_pct(ps['ann'])} | {fmt_pct(ps['mdd'])} | "
                 f"{ps['sharpe']:.2f} | {e:.0%} |")
    corr_ah = float(timed["a500"].corr(timed["hs300"]))
    corr_ak = float(timed["a500"].corr(timed["kc50"]))
    L += [
        "",
        f"> **A股三条腿的择时后相关性**：A500 vs 沪深300 **{corr_ah:.2f}**、A500 vs 科创50 **{corr_ak:.2f}** "
        "（月收益，两者都约 0.7）。沪深300 与 A500 成分重叠约八成（§1 已写），相关性略高、且这段"
        "只有 "+fmt_pct(attr[1][2])+" 的年化 —— 现基准把「A股大盘腿」一分为二（各 2.5pp），"
        "**是同一份 beta 的两个壳，不是两个因子**；科创50 独立性强一点、弹性大得多。",
        "",
    ]

    # ---------------- 3.1 收益风险比排名（同窗口、同口径）
    def _calmar(p):
        return p["ann"] / abs(p["mdd"]) if p["mdd"] < 0 else np.nan

    PT = {x[0]: x[1] for x in per_item}
    PS = {x[0]: x[2] for x in per_item}
    E = {x[0]: x[3] for x in per_item}
    items_ = [x for x in per_item if x[0] != "cash"]      # 货基无波动、夏普无定义，不参与排名
    rk_t = sorted(items_, key=lambda x: -x[1]["sharpe"])
    rk_s = sorted(items_, key=lambda x: -x[2]["sharpe"])
    solid = [x[0] for x in rk_s if PS[x[0]]["sharpe"] > 0.5]                      # 按持有夏普降序
    hollow = [x[0] for x in rk_s if PS[x[0]]["sharpe"] < 0 and PT[x[0]]["sharpe"] < 0.1]
    gain = max(items_, key=lambda x: x[1]["sharpe"] - x[2]["sharpe"])
    notes31 = []
    if solid:
        notes31.append(f"- **标的本身就够好（一直拿着夏普 > 0.5）**：{'、'.join(LABEL[k] for k in solid)}"
                       " —— 这几项不靠择时也有正的收益风险比。")
    notes31.append(f"- **择时增益最大**：{LABEL[gain[0]]} 持有夏普 {PS[gain[0]]['sharpe']:.2f} → "
                   f"择时 {PT[gain[0]]['sharpe']:.2f}（+{PT[gain[0]]['sharpe'] - PS[gain[0]]['sharpe']:.2f}），"
                   f"但择时后平均只持仓 {E[gain[0]]:.0%} —— 收益主要来自躲开下跌段，不是拿住了上涨。")
    if hollow:
        notes31.append(f"- **两个口径都救不回来**：{'、'.join(LABEL[k] for k in hollow)}"
                       f" —— 持有夏普为负（回撤约 {abs(np.mean([PS[k]['mdd'] for k in hollow])) * 100:.0f}%），"
                       f"择时后也只有 {max(PT[k]['sharpe'] for k in hollow):.2f}；"
                       "它们留在组合里是为宽基 / 编制分散，不是靠收益风险比（§9.5 实测拆壳对组合层近似中性）。")
    sec31 = [
        "### 3.1 收益风险比排名（同窗口、同口径）",
        "",
        "夏普 = (年化 − 1.40%) / 波动；Calmar = 年化 / |最大回撤|。货币基金无波动、夏普无定义，不参与排名"
        "（按 1.40% 常数计息，等价现金基准）。",
        "",
        "**按 §9 择时口径** —— 可比的是夏普 / Calmar；年化被平均持仓摊薄，**不可跨腿比大小**：",
        "",
        "| 排名 | 项 | 年化 | 最大回撤 | 夏普 | Calmar | 平均持仓 |",
        "|---:|---|---:|---:|---:|---:|---:|",
    ]
    for i, (k, pt, _ps, e) in enumerate(rk_t, 1):
        sec31.append(f"| {i} | {LABEL[k]} | {fmt_pct(pt['ann'])} | {fmt_pct(pt['mdd'])} | "
                     f"{pt['sharpe']:.2f} | {_calmar(pt):.2f} | {e:.0%} |")
    sec31 += [
        "",
        "**按「一直拿着」口径** —— 标的本身的收益风险比，不含任何择时：",
        "",
        "| 排名 | 项 | 年化 | 最大回撤 | 夏普 | Calmar |",
        "|---:|---|---:|---:|---:|---:|",
    ]
    for i, (k, _pt, ps, _e) in enumerate(rk_s, 1):
        sec31.append(f"| {i} | {LABEL[k]} | {fmt_pct(ps['ann'])} | {fmt_pct(ps['mdd'])} | "
                     f"{ps['sharpe']:.2f} | {_calmar(ps):.2f} |")
    sec31 += ["", *notes31, "",
              "> ⚠ 这张排名**只用于判断某项该不该留**，**不能用来定权重**：按夏普排序加仓会把钱堆到债券和黄金，"
              "正是 §7.1 判据 1 明确禁止的做法（当年「按单项夏普加权」档位年化只有 4.5%）。",
              "> 口径边界同 §5：月频、月末净值算回撤、真实指数（非场外联接净值）、样本仅 61 个月单一周期；"
              "恒生科技不在本窗口，未入排名。",
              ""]
    L += sec31

    L += [
        "## 4. 谁在承担风险（现基准 vs 原始 8 项，均为择时口径）",
        "",
        "| 项 | 基准 | 现基 风险贡献 | 原始 8 项 风险贡献 | 一直拿着 风险贡献 |",
        "|---|---:|---:|---:|---:|",
    ]
    for k in ITEMS:
        L.append(f"| {LABEL[k]} | {w0[k]:g}% | {rc0[k]:.1%} | {rc_old[k]:.1%} | {rc0s[k]:.1%} |")
    L += [
        "",
        f"组合层：择时 年化 {fmt_pct(p0['ann'])} / 回撤 {fmt_pct(p0['mdd'])} / 夏普 {p0['sharpe']:.2f} / "
        f"Calmar {p0['calmar']:.2f}；一直拿着 {fmt_pct(p0s['ann'])} / {fmt_pct(p0s['mdd'])} / "
        f"{p0s['sharpe']:.2f} / {p0s['calmar']:.2f}。",
        "",
        "## 5. 边界（不读这一节就别引用上面的数）",
        "",
        "1. **样本只有约 5 年**，且这段是「A股熊转牛 + 美股长牛 + 黄金大牛 + 港股深跌 + 债牛转债熊」的特定组合；"
        "债券这段年化 4.24%、回撤仅 -1.14%，加债券几乎是免费降波，所以灵敏度曲线在**夏普上单调偏袒高债券**"
        f"（15%→35%：夏普 {sens[0][2]['sharpe']:.2f}→{sens[-1][2]['sharpe']:.2f}）——那是行情的属性，不是配置的结论。"
        "任何「扫出最优权重」的做法都是在拟合这 5 年（§7.1 判据 1 明确禁止）。",
        "2. **月频口径**：组合层没有日内盯市，最大回撤比日频浅，短窗口的夏普也会略好看。"
        "与 `backtest/_permanent_bt.md`（§4，也是月频）可比，与日频产品口径不可比。",
        "3. **择时会摊薄暴露**：六项 vol 腿的月均持仓只有 11%~61%（沪深300 11%、科创50 12%、A500 22%、"
        "标普 47%、纳指 54%、黄金 68%），组合平均实际投入远低于 100%，"
        "所以「择时组合收益更低」是必然的，不能直接读成「择时是负贡献」。要比就比同暴露或比夏普/Calmar。",
        "4. **执行口径差异**：真实指数 vs 场外联接/QDII（净值滞后、汇率、跟踪误差、指数不含分红），"
        "本报告是「规则本身」的组合层，不是实盘对账。",
        "5. **红利腿是月频、债腿含内部调仓**，两条腿的「一直拿着」基准也不是同一个东西（全利率 vs 10 只等权），"
        "逐项那一栏只用于看方向，不做横比。",
        "",
        "> 机械规则输出，非投资建议。",
    ]
    with open(OUT, "w", encoding="utf-8") as fh:
        fh.write("\n".join(L) + "\n")
    print("WROTE", OUT)
    for tag, name, p, exp in rows:
        print(f"  {tag} | {name:8s} 年化 {p['ann'] * 100:+6.2f}% 回撤 {p['mdd'] * 100:+7.2f}% "
              f"夏普 {p['sharpe']:5.2f}")
    for b, _w, p, rc, e in sens:
        print(f"  bond {b:.0f}% -> 年化 {p['ann'] * 100:+6.2f}% 回撤 {p['mdd'] * 100:+7.2f}% "
              f"夏普 {p['sharpe']:5.2f} 实际投入 {e:.1%} 债RC {rc['bond']:.1%}")


if __name__ == "__main__":
    main()
