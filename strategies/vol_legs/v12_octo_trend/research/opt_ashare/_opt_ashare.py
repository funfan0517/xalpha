# -*- coding: utf-8 -*-
"""V12 · A股宽基优化研究（沪深300 / 科创50）。

触发：用户 2026-11-12「趋势判断看着准，但表现感觉和 v13 差不多」，指定只研究 A股宽基。

本探针回答三件事：
1. **「差不多」是不是真的**（同窗口跑 v13 的净值做对照，不靠引用它报告里的数字）；
2. **钱到底从哪来 / 掉在哪**（把归因拆成「稳态持有」与「换态当天用旧仓位的滞后成本」两段）；
3. **哪一层值得改**（六个候选，全部给样本外；带参数的实验一律「前段定参、后段只报」）。

范围与约束：
- 标的只有 **沪深300（510300）+ 科创50（588000）**；美股/黄金腿不参与（`z_th`/`rvol` 没重标，
  规格 §7.4 自己也不建议直接搬）。
- 规格的六步判定链默认不动；要动就必须在样本外站得住（§3.4 / §3.5 就是给证据的地方）。
- 每个候选都给**曝光匹配**基准 —— 40% 仓位的策略去比 100% 恒持的回撤，赢的永远是仓位不是规则。

产物：`_opt_ashare.md`（本目录）。

用法：
    python strategies/vol_legs/v12_octo_trend/research/opt_ashare/_opt_ashare.py
    python .../_opt_ashare.py --no-v13     # 跳过 v13 对照（子进程较慢）
"""
import io
import json
import os
import subprocess
import sys

import numpy as np
import pandas as pd

try:
    sys.stdout.reconfigure(encoding="utf-8")  # type: ignore[attr-defined]
except Exception:  # noqa: BLE001
    pass

_HERE = os.path.dirname(os.path.abspath(__file__))
_RES = os.path.dirname(_HERE)
_V12 = os.path.dirname(_RES)
_VL = os.path.dirname(_V12)
_V13 = os.path.join(_VL, "v13_quad_9grid")
if _V12 not in sys.path:
    sys.path.insert(0, _V12)

import rule           # noqa: E402
import engine         # noqa: E402
import data as vdata  # noqa: E402

OUT_MD = os.path.join(_HERE, "_opt_ashare.md")

LEGS = ["hs300", "kc50"]
# 时间外切分：前段（定参） / 后段（只检验）。科创50 上市晚，只能这样切。
SPLIT = {"hs300": "2021-01-01", "kc50": "2024-07-01"}
LADDER = [0.0, 0.0, 0.2, 0.2, 0.5, 0.5, 1.0, 1.0]   # 8 个态按分位排队后的许可

TD = rule.TRADING_DAYS


# ---------------------------------------------------------------- 造信号
def load(leg, vol_code=None):
    old = dict(rule.MKT_VOL_LEG)
    if vol_code:
        rule.MKT_VOL_LEG[leg] = vol_code
    try:
        return vdata.frame(leg)
    finally:
        rule.MKT_VOL_LEG.clear()
        rule.MKT_VOL_LEG.update(old)


def positions(states, cmap=None, up=None, down=0, min_hold=0):
    """八态 -> 仓位。比 `engine.positions` 多两个旋钮（本研究对照组）：
    `down` = 降格也要连续 N 日（0 = 立即，与 engine 一致）；`min_hold` = 换仓后冻结 K 根。
    """
    cmap = rule.CAP if cmap is None else cmap
    up = rule.UP_CONFIRM if up is None else int(up)
    out, cur, pend, pc, dpend, dc, hold = [], 0.0, None, 0, None, 0, 0
    idx = states.index if hasattr(states, "index") else None
    seq = states.to_numpy() if hasattr(states, "to_numpy") else states
    for st in seq:
        tgt = float(cmap.get(str(st), 0.0)) if st is not None and str(st) in cmap else cur
        if hold > 0:
            hold -= 1
            out.append(cur)
            continue
        if tgt > cur + 1e-9:
            if pend is not None and abs(pend - tgt) < 1e-9:
                pc += 1
            else:
                pend, pc = tgt, 1
            dpend, dc = None, 0
            if pc >= max(up, 1):
                cur, pend, pc, hold = tgt, None, 0, min_hold
        elif tgt < cur - 1e-9:
            pend, pc = None, 0
            if down <= 0:
                cur, dpend, dc, hold = tgt, None, 0, min_hold
            else:
                if dpend is not None and abs(dpend - tgt) < 1e-9:
                    dc += 1
                else:
                    dpend, dc = tgt, 1
                if dc >= down:
                    cur, dpend, dc, hold = tgt, None, 0, min_hold
        else:
            pend, pc, dpend, dc = None, 0, None, 0
        out.append(cur)
    return pd.Series(out, index=idx)


def build(leg, p=None, cap=None, vol_code=None):
    df = load(leg, vol_code)
    p = p or rule.params(leg)
    ind = engine.indicators(df, p)
    cx = engine.classify(ind, p)
    w = positions(cx["state"], cap)
    first = cx["state"].notna()
    start = df.index[int(np.argmax(first.to_numpy()))] if first.any() else df.index[-1]
    return dict(leg=leg, df=df, p=p, ind=ind, cx=cx, w=w, start=start)


def ev(b, w=None, start=None, label="", kind="", nav=None):
    w = b["w"] if w is None else w
    st = b["start"] if start is None else start
    if nav is None:
        nav = engine.run(b["df"], w).loc[st:]
    p = engine.perf(nav)
    ws = w.loc[st:] if w is not None else None
    return dict(label=label, kind=kind, ann=p["ann"], vol=p["vol"], mdd=p["mdd"], sharpe=p["sharpe"],
                expo=float(ws.mean()) if ws is not None else np.nan,
                acts=len(engine.actions(ws)) if ws is not None else np.nan, days=p["days"], nav=nav)


def ev_split(b, w, label, kind=""):
    """全样本 + 样本外两行。"""
    sp = pd.Timestamp(SPLIT[b["leg"]])
    full = ev(b, w=w, label=label, kind=kind)
    nav = engine.run(b["df"], w)
    post = nav.loc[nav.index >= sp]
    p = engine.perf(post)
    return full, dict(label=label, kind=kind, ann=p["ann"], mdd=p["mdd"], sharpe=p["sharpe"],
                      expo=float(w.loc[w.index >= sp].mean()), acts=np.nan, days=p["days"], nav=post)


# ---------------------------------------------------------------- 基准
def w_const(b, x):
    return pd.Series(float(x), index=b["df"].index)


def w_ma(b, n=250):
    m = b["df"]["close"].rolling(n).mean()
    return (b["df"]["close"] > m).astype(float).fillna(0.0)


def w_regime(b, up=1.0, dn=0.2):
    r = b["cx"]["regime_used"]
    w = pd.Series(np.where(r == "向上", up, np.where(r == "向下", dn, 0.0)), index=b["df"].index)
    w[b["cx"]["state"].isna()] = 0.0
    return w.astype(float)


def w_vol_target(b, w, vt):
    """w' = min(w, vt / σ20)。σ20 是当日收盘已知值，仓位 T+1 才生效，无前视。"""
    sig = b["df"]["close"].pct_change().rolling(20, min_periods=20).std() * np.sqrt(TD)
    sig = sig.fillna(sig.median()).clip(lower=0.05)
    return pd.concat([w, (vt / sig).rename("cap")], axis=1).min(axis=1)


# ---------------------------------------------------------------- v13 对照（子进程）
def v13_nav(leg):
    code = (
        "import sys,json;sys.path.insert(0,'.');import engine;"
        f"r=engine.backtest_leg('{leg}');"
        "print('@@'+json.dumps({'d':[str(x.date()) for x in r['nav'].index],"
        "'n':[float(x) for x in r['nav'].values],'w':[float(x) for x in r['w'].values]}))"
    )
    env = dict(os.environ, PYTHONIOENCODING="utf-8")
    r = subprocess.run([sys.executable, "-c", code], cwd=_V13, capture_output=True,
                       text=True, encoding="utf-8", env=env)
    for line in (r.stdout or "").splitlines():
        if line.startswith("@@"):
            j = json.loads(line[2:])
            idx = pd.to_datetime(j["d"])
            return pd.Series(j["n"], index=idx), pd.Series(j["w"], index=idx)
    raise RuntimeError(f"v13 子进程失败：{(r.stderr or r.stdout or '')[-400:]}")


# ---------------------------------------------------------------- 归因（两段）
def attribution(b, w, start):
    """把 P&L 拆成两段，避免「状态 T 的标签 + T−1 的仓位」混在一起看不清：

    A. **稳态**：按「昨天定的态」分组（`state.shift(1)`），这天用的是它给的仓位；
    B. **换态当天**：`state[T] != state[T-1]`，这天仍持有旧态的仓位 —— 破位当天的成本在这里。
    """
    df, st = b["df"], start
    ret = df["close"].pct_change()
    weff = w.shift(1).fillna(0.0)
    fee = w.diff().fillna(w).abs() * rule.FEE_SIDE
    k = df.index >= start
    years = k.sum() / TD
    s = b["cx"]["state"]
    prev = s.shift(1)
    trans = k & s.notna().to_numpy() & prev.notna().to_numpy() & (s != prev).to_numpy()

    A = []
    for name in rule.STATE_ORDER:
        m = k & (prev == name).to_numpy() & (~trans)
        if m.sum() < 5:
            continue
        r = ret[m]
        A.append(dict(state=name, days=int(m.sum()),
                      mu=float(r.mean()) * 100, t=float(r.mean() / r.std() * np.sqrt(len(r)))
                      if r.std() else np.nan,
                      wbar=float(weff[m].mean()),
                      contrib=float((weff * ret)[m].sum()) / years * 100,
                      fee=float(fee[m].sum()) / years * 100))
    B = []
    for name in rule.STATE_ORDER:
        m = trans & (s == name).to_numpy()
        if not m.sum():
            continue
        B.append(dict(state=name, days=int(m.sum()),
                      mu=float(ret[m].mean()) * 100, wbar=float(weff[m].mean()),
                      contrib=float((weff * ret)[m].sum()) / years * 100))
    tot = float((weff * ret)[k].sum()) / years * 100
    return dict(A=A, B=B, years=years, total=tot,
                fee_total=float(fee[k].sum()) / years * 100,
                trans_days=int(trans.sum()),
                trans_cost=float((weff * ret)[trans].sum()) / years * 100)


# ---------------------------------------------------------------- 映射重标
def fwd_means(b, horizon, upto=None):
    """前段（或全样本）逐态 fwd_h 均值。"""
    df = b["df"]
    fwd = df["close"].shift(-horizon) / df["close"] - 1.0
    s = b["cx"]["state"]
    m = s.notna()
    if upto is not None:
        m = m & (df.index < upto)
    return {x: float(fwd[m & (s == x)].mean()) for x in rule.STATE_ORDER
            if (m & (s == x)).sum() >= 10}


def cap_by_rank(b, horizon, upto=None, shrink=0.0, cap_map=None):
    """按 fwd_h 的排序把许可分给 8 个态（排序 → 分位梯子），可选向规格 CAP 收缩。"""
    mu = fwd_means(b, horizon, upto)
    order = sorted(mu, key=lambda x: mu[x])
    ladder = LADDER[-len(order):] if len(order) < len(LADDER) else LADDER
    cap = dict(zip(order, ladder))
    spec = rule.CAP if cap_map is None else cap_map
    if shrink:
        cap = {x: (1 - shrink) * spec.get(x, 0.0) + shrink * cap.get(x, spec.get(x, 0.0))
               for x in spec}
    return cap


# ---------------------------------------------------------------- 主流程
def main():
    no_v13 = "--no-v13" in sys.argv
    data = {leg: build(leg) for leg in LEGS}

    L = [
        "# V12 · A股宽基优化研究（沪深300 / 科创50）",
        "",
        "> 由 `research/opt_ashare/_opt_ashare.py` 生成。触发：用户 2026-11-12「趋势判断看着准，"
        "但表现感觉和 v13 差不多」——本文件就是这句话的取证与优化候选。",
        "> 口径：场内 ETF 前复权（`data/_ohlcv.json`）· A股腿量能 = `SH000300` 指数成交量 · "
        "T 日收盘判态 / T+1 起承担仓位 · 空仓计货基 1.40% · 单边 5bp。",
        f"> 时间外切分：沪深300 前段 < {SPLIT['hs300']}、科创50 前段 < {SPLIT['kc50']}"
        "（科创50 2020-11 才上市，预热后只剩 2022 起，只能这样切）。**前段定参，后段只报。**",
        "",
    ]

    # ================================================================ §1
    L += ["## 1. 「和 v13 差不多」是不是真的", ""]
    head = ["口径", "类型", "年化", "波动", "最大回撤", "夏普", "平均许可", "调仓"]
    sep = "|---|---|---:|---:|---:|---:|---:|---:|"
    for leg in LEGS:
        b = data[leg]
        rows = [ev(b, label="V12 八态（现口径）", kind="本研究"),
                ev(b, w=pd.Series(1.0, index=b["df"].index), label="恒持 100%", kind="基准"),
                ev(b, w=w_const(b, b["w"].loc[b["start"]:].mean()), label="恒持（曝光匹配）", kind="基准"),
                ev(b, w=w_regime(b), label="regime 单层：向上 100% / 向下 20%", kind="基准"),
                ev(b, w=w_ma(b), label="年线闸门（close>MA250）", kind="基准")]
        if not no_v13:
            nav13, w13 = v13_nav(leg)
            m = nav13.index >= b["start"]
            rows.append(ev(b, w=w13.reindex(b["df"].index).fillna(0.0), nav=nav13[m],
                           label="V13 九宫格（同窗口、独立净值）", kind="对照"))
        L += [f"### {rule.NAMES[leg]}（{rule.LEGS[leg][0]}，{b['start'].date()} ~ "
              f"{b['df'].index[-1].date()}）", "", "| " + " | ".join(head) + " |", sep]
        for r in rows:
            L.append(f"| {r['label']} | {r['kind']} | {r['ann'] * 100:+.2f}% | {r['vol'] * 100:.1f}% | "
                     f"{r['mdd'] * 100:+.2f}% | {r['sharpe']:.2f} | "
                     f"{r['expo']:.0%} | {r['acts']:.0f} |")
        L.append("")

    # ================================================================ §2
    L += ["## 2. 归因：钱从哪来、掉在哪", "",
          "两段拆开看，否则「状态 T 的标签 + T−1 的仓位」混在一起什么都看不出来：",
          "",
          "- **稳态**：按「昨天定的态」分组，这天用的就是它给的仓位；`次日` = 该态之后的日收益均值，"
          "`t` = 均值 / 标准差（|t| < 2 就当噪声看）；`贡献` = `仓位 × 日收益` 累计 / 年数；",
          "- **换态当天**：状态刚变、但仓位还是旧的 —— 破位那天的成本全在这里。", ""]
    for leg in LEGS:
        b = data[leg]
        a = attribution(b, b["w"], b["start"])
        L += [f"### {rule.NAMES[leg]}（{a['years']:.1f} 年 · 合计 {a['total']:+.2f}pp/年 · "
              f"摩擦 {a['fee_total']:.2f}pp/年 · 换态 {a['trans_days']} 天）", "",
              "**A. 稳态持有（按昨日态）**", "",
              "| 态 | 天数 | 次日 | t | 平均仓位 | 贡献 | 摩擦 | 许可 |",
              "|---|---:|---:|---:|---:|---:|---:|---:|"]
        for r in a["A"]:
            L.append(f"| {r['state']} | {r['days']} | {r['mu']:+.3f}% | {r['t']:+.1f} | {r['wbar']:.0%} | "
                     f"{r['contrib']:+.2f}pp | {r['fee']:.2f}pp | {rule.cap_of(r['state']):.0%} |")
        L += ["", f"**B. 换态当天（用旧仓位）· 合计 {a['trans_cost']:+.2f}pp/年**", "",
              "| 新态 | 天数 | 当天 | 平均仓位 | 贡献 |", "|---|---:|---:|---:|---:|"]
        for r in a["B"]:
            L.append(f"| {r['state']} | {r['days']} | {r['mu']:+.3f}% | {r['wbar']:.0%} | "
                     f"{r['contrib']:+.2f}pp |")
        L.append("")

    # ================================================================ §3
    L += ["## 3. 候选优化（每项同一把尺子，带参数的都给样本外）", ""]

    # ---- 3.1 换手
    L += ["### 3.1 换手层：降格确认 / 最小持有（只治抖动，不改判定）", "",
          "| 腿 | 口径 | 全样本年化 | 全样本回撤 | 全样本夏普 | 样本外夏普 | 调仓/年 | 摩擦 |",
          "|---|---|---:|---:|---:|---:|---:|---:|"]
    for leg in LEGS:
        b = data[leg]
        yrs = (b["df"].index >= b["start"]).sum() / TD
        variants = [("现口径（升 2 / 降立即）", b["w"]),
                    ("降格也要 2 日", positions(b["cx"]["state"], down=2)),
                    ("降格也要 3 日", positions(b["cx"]["state"], down=3)),
                    ("最小持有 5 日", positions(b["cx"]["state"], min_hold=5)),
                    ("升 3 / 降立即", positions(b["cx"]["state"], up=3))]
        for lbl, w in variants:
            full, post = ev_split(b, w, lbl)
            fee = float((w.diff().fillna(w).abs() * rule.FEE_SIDE).loc[b["start"]:].sum()) / yrs * 100
            L.append(f"| {rule.NAMES[leg]} | {lbl} | {full['ann'] * 100:+.2f}% | "
                     f"{full['mdd'] * 100:+.2f}% | {full['sharpe']:.2f} | {post['sharpe']:.2f} | "
                     f"{full['acts'] / yrs:.1f} | {fee:.2f}pp |")
    L.append("")

    # ---- 3.2 波动率目标
    L += ["### 3.2 仓位层：波动率目标覆盖 `min(许可, vt / σ20)`", "",
          "科创50 的年化波动是沪深300 的两倍多，同样的 50% 许可在它身上是两倍的风险预算。"
          "这一层不动判定、只压高风险段的仓位。", "",
          "| 腿 | 口径 | 年化 | 回撤 | 夏普 | 样本外夏普 | 平均许可 |",
          "|---|---|---:|---:|---:|---:|---:|"]
    for leg in LEGS:
        b = data[leg]
        for vt in (None, 0.15, 0.20, 0.25, 0.30):
            w = b["w"] if vt is None else w_vol_target(b, b["w"], vt)
            full, post = ev_split(b, w, "不覆盖" if vt is None else f"vt={vt:.0%}")
            L.append(f"| {rule.NAMES[leg]} | {'不覆盖' if vt is None else f'vt={vt:.0%}'} | "
                     f"{full['ann'] * 100:+.2f}% | {full['mdd'] * 100:+.2f}% | {full['sharpe']:.2f} | "
                     f"{post['sharpe']:.2f} | {full['expo']:.0%} |")
    L.append("")

    # ---- 3.3 量能
    L += ["### 3.3 数据层：换一条市场量能（`SH000300` vs `SH000001`）", "",
          "`research/probe_volume` 里上证量能的「弱回调 − 强回调」更高（+2.02pp），但长样本"
          "「强反弹 − 弱反弹」是 −0.08。回测是唯一能裁决的地方。", "",
          "| 腿 | 量能 | 年化 | 回撤 | 夏普 | 样本外夏普 | 调仓 |", "|---|---|---:|---:|---:|---:|---:|"]
    for leg in LEGS:
        for code, nm in (("SH000300", "沪深300 指数量"), ("SH000001", "上证指数量")):
            bb = build(leg, vol_code=code)
            full, post = ev_split(bb, bb["w"], nm)
            L.append(f"| {rule.NAMES[leg]} | {nm} | {full['ann'] * 100:+.2f}% | "
                     f"{full['mdd'] * 100:+.2f}% | {full['sharpe']:.2f} | {post['sharpe']:.2f} | "
                     f"{full['acts']:.0f} |")
    L.append("")

    # ---- 3.4 阈值
    L += ["### 3.4 判定层：`z_th` / `rvol_lo` 按标的重标（规格 §7.4 明确建议）", "",
          "前段挑一个、后段只报。**最优落在格子边界 = 该参数在本样本不可辨，别选。**", ""]
    for leg in LEGS:
        b = data[leg]
        sp = pd.Timestamp(SPLIT[leg])
        L += [f"**{rule.NAMES[leg]}**（前段 < {SPLIT[leg]} / 后段 ≥ {SPLIT[leg]}）", "",
              "| z_th | rvol_lo | 前段夏普 | 后段夏普 | 全样本夏普 | 全样本年化 | 调仓 |",
              "|---:|---:|---:|---:|---:|---:|---:|"]
        grid = []
        for z in (1.0, 1.2, 1.5, 1.8, 2.2):
            for rl in (0.75, 0.85, 0.95):
                p = dict(b["p"], z_th=z, rvol_lo=rl)
                ind = engine.indicators(b["df"], p)
                cx = engine.classify(ind, p)
                w = positions(cx["state"])
                nav = engine.run(b["df"], w)
                pre_nav = nav.loc[(nav.index >= b["start"]) & (nav.index < sp)]
                post_nav = nav.loc[nav.index >= sp]
                full = ev(b, w=w)
                s_pre = engine.perf(pre_nav)["sharpe"] if len(pre_nav) > 30 else np.nan
                s_post = engine.perf(post_nav)["sharpe"] if len(post_nav) > 30 else np.nan
                grid.append(dict(z=z, rl=rl, pre=s_pre, post=s_post, full=full["sharpe"],
                                 ann=full["ann"], acts=full["acts"]))
                L.append(f"| {z:.1f} | {rl:.2f} | {s_pre:.2f} | {s_post:.2f} | {full['sharpe']:.2f} | "
                         f"{full['ann'] * 100:+.2f}% | {full['acts']:.0f} |")
        ok = [g for g in grid if np.isfinite(g["pre"])]
        best = max(ok, key=lambda g: g["pre"])
        dflt = [g for g in ok if g["z"] == 1.2 and g["rl"] == 0.85][0]
        edge = best["z"] in (1.0, 2.2) or best["rl"] in (0.75, 0.95)
        L += ["", f"- 前段最优 `z_th={best['z']:.1f}` / `rvol_lo={best['rl']:.2f}`：前段夏普 "
                  f"{best['pre']:.2f} → **后段 {best['post']:.2f}**（默认口径后段 {dflt['post']:.2f}）；"
                  f"全样本 {best['full']:.2f} vs 默认 {dflt['full']:.2f}"
                  + ("。⚠ **最优点在格子边界**，说明该参数还没到拐点 —— 不可辨，不要据此改参数。"
                     if edge else "。"), ""]

    # ---- 3.4b 快线尺度
    L += ["**（b）快线尺度 `MA20 → 30/40/60`**：如果问题是「态切得太碎」，把快线拉长应该有效 —— "
          "这是个可以直接证伪的假设。", "",
          "| 腿 | `ma_short` | 前段夏普 | 后段夏普 | 全样本年化 | 回撤 | 夏普 |",
          "|---|---:|---:|---:|---:|---:|---:|"]
    for leg in LEGS:
        b = data[leg]
        sp = pd.Timestamp(SPLIT[leg])
        for mas in (20, 30, 40, 60):
            p = dict(b["p"], ma_short=mas)
            ind = engine.indicators(b["df"], p)
            cx = engine.classify(ind, p)
            w = positions(cx["state"])
            nav = engine.run(b["df"], w)
            pre = engine.perf(nav.loc[(nav.index >= b["start"]) & (nav.index < sp)])
            post = engine.perf(nav.loc[nav.index >= sp])
            full = ev(b, w=w)
            L.append(f"| {rule.NAMES[leg]} | {mas} | {pre['sharpe']:.2f} | {post['sharpe']:.2f} | "
                     f"{full['ann'] * 100:+.2f}% | {full['mdd'] * 100:+.2f}% | {full['sharpe']:.2f} |")
    L += ["", "→ **证伪**：拉长快线让两条腿都变差（科创50 0.16 → −0.06 ~ −0.12），"
              "所以问题不在「界线太抖」，在**界线画完之后给了多少仓位**。这一条很重要："
              "它把后面的优化方向从「改判定」推到「改映射」。", ""]

    # ---- 3.5 映射层：单旋钮 + 分位重标
    L += ["### 3.5 映射层：许可（`CAP`）是最大的旋钮", "",
          "规格的 `CAP` 依据是 **fwd20**（20 日期望），但这条策略「升 2 日确认、降格立即」，"
          "实际持有周期只有几天 —— **用 20 日的尺子量几天的仓位**。两段证据分开给：", "",
          "**（a）单旋钮扫描**：只动一个态的许可、其余照规格，看两段是否同向改善。"
          "`前段` / `后段` 同为夏普；括号里是平均许可（变动的代价会体现在这里）。", ""]
    knob = {"强回调": (0.0, 0.2, 0.3, 0.5), "强反弹": (0.5, 0.7, 1.0),
            "急跌": (0.0, 0.2, 0.3, 0.5), "弱反弹": (0.0, 0.2, 0.5)}
    for leg in LEGS:
        b = data[leg]
        sp = pd.Timestamp(SPLIT[leg])
        L += [f"**{rule.NAMES[leg]}**", "",
              "| 态 | 许可 | 前段夏普 | 后段夏普 | 全样本年化 | 全样本回撤 | 全样本夏普 | 平均许可 |",
              "|---|---:|---:|---:|---:|---:|---:|---:|"]
        for st, vals in knob.items():
            for v in vals:
                cap = dict(rule.CAP)
                cap[st] = v
                w = positions(b["cx"]["state"], cap)
                nav = engine.run(b["df"], w)
                pre = engine.perf(nav.loc[(nav.index >= b["start"]) & (nav.index < sp)])
                post = engine.perf(nav.loc[nav.index >= sp])
                full = ev(b, w=w)
                mark = " ← 现口径" if abs(v - rule.cap_of(st)) < 1e-9 else ""
                L.append(f"| {st}{mark} | {v:.0%} | {pre['sharpe']:.2f} | {post['sharpe']:.2f} | "
                         f"{full['ann'] * 100:+.2f}% | {full['mdd'] * 100:+.2f}% | {full['sharpe']:.2f} | "
                         f"{full['expo']:.0%} |")
        L.append("")

    L += ["**（b）整张映射按「持有期」重标**：把 8 个态按 fwd_h 排序，用分位梯子 "
          "`[0,0,0.2,0.2,0.5,0.5,1,1]` 重新分配许可（**只用前段定参**），再看后段。"
          "最后一列是**同曝光恒持**的夏普 —— 如果它和策略的差不超过 0.1，那这份「改进」"
          "只是把仓位整体压低，不是选态选对了。", ""]
    for leg in LEGS:
        b = data[leg]
        sp = pd.Timestamp(SPLIT[leg])
        L += [f"**{rule.NAMES[leg]}**", "",
              "| 许可来源 | 前段夏普 | 后段夏普 | 全样本年化 | 回撤 | 夏普 | 平均许可 | 同曝光恒持夏普 |",
              "|---|---:|---:|---:|---:|---:|---:|---:|"]
        rows = [("规格 CAP（现口径）", b["w"])]
        for h in (1, 5, 20):
            rows.append((f"fwd{h} 排序（前段定参）", positions(b["cx"]["state"], cap_by_rank(b, h, sp))))
        rows.append(("fwd5 排序 + 向规格收缩 50%",
                     positions(b["cx"]["state"], cap_by_rank(b, 5, sp, shrink=0.5))))
        rows.append(("fwd5 排序（**全样本定参 = 不可交易的上限**）",
                     positions(b["cx"]["state"], cap_by_rank(b, 5))))
        for lbl, w in rows:
            nav = engine.run(b["df"], w)
            pre = engine.perf(nav.loc[(nav.index >= b["start"]) & (nav.index < sp)])
            post = engine.perf(nav.loc[nav.index >= sp])
            full = ev(b, w=w)
            eq = w_const(b, full["expo"])
            eqs = engine.perf(engine.run(b["df"], eq).loc[b["start"]:])["sharpe"]
            L.append(f"| {lbl} | {pre['sharpe']:.2f} | {post['sharpe']:.2f} | {full['ann'] * 100:+.2f}% | "
                     f"{full['mdd'] * 100:+.2f}% | {full['sharpe']:.2f} | {full['expo']:.0%} | {eqs:.2f} |")
        for h in (1, 5, 20):
            c = cap_by_rank(b, h, sp)
            L.append(f"- 前段定参（fwd{h}）：" + " · ".join(
                f"{k} {c.get(k, rule.cap_of(k)):.0%}" for k in rule.STATE_ORDER))
        L += ["- 规格原表："
              + " · ".join(f"{k} {rule.cap_of(k):.0%}" for k in rule.STATE_ORDER), ""]

    # ---- 3.6 组合候选
    L += ["### 3.6 组合候选：只叠加「两段同向」的改动", "",
          "上面三节的结论收敛成三条（都要求**前段与后段同向改善**）："
          "① `强反弹` 50%→100%；② `强回调` 50%→20%；③ 科创50 的 `急跌` 50%→20%"
          "（沪深300 **不动** —— 把急跌降到 0 会让它的前段、后段、全样本一起变差，"
          "单旋钮表里看得很清楚，规格「急跌会反抽」在沪深300 上是对的）。"
          "再加一条与判定无关的：高波动腿的波动率目标覆盖。", ""]
    cap_b = dict(rule.CAP, **{"强反弹": 1.0, "强回调": 0.2})
    cap_c = {"hs300": cap_b, "kc50": dict(cap_b, 急跌=0.2)}
    for leg in LEGS:
        b = data[leg]
        sp = pd.Timestamp(SPLIT[leg])
        cands = [("A 现口径", b["w"]),
                 ("B = A + 强反弹100% + 强回调20%", positions(b["cx"]["state"], cap_b)),
                 ("C = B + 急跌 per-leg", positions(b["cx"]["state"], cap_c[leg]))]
        wc = positions(b["cx"]["state"], cap_c[leg])
        cands.append(("D = C + 波动覆盖 vt=25%", w_vol_target(b, wc, 0.25)))
        cands.append(("E = 分位重标 fwd20（前段定参）",
                      positions(b["cx"]["state"], cap_by_rank(b, 20, sp))))
        L += [f"**{rule.NAMES[leg]}**", "",
              "| 候选 | 前段夏普 | 后段夏普 | 全样本年化 | 回撤 | 夏普 | 调仓/年 | 平均许可 |",
              "|---|---:|---:|---:|---:|---:|---:|---:|"]
        yrs = (b["df"].index >= b["start"]).sum() / TD
        for lbl, w in cands:
            nav = engine.run(b["df"], w)
            pre = engine.perf(nav.loc[(nav.index >= b["start"]) & (nav.index < sp)])
            post = engine.perf(nav.loc[nav.index >= sp])
            full = ev(b, w=w)
            L.append(f"| {lbl} | {pre['sharpe']:.2f} | {post['sharpe']:.2f} | {full['ann'] * 100:+.2f}% | "
                     f"{full['mdd'] * 100:+.2f}% | {full['sharpe']:.2f} | {full['acts'] / yrs:.1f} | "
                     f"{full['expo']:.0%} |")
        # 分年收益（A vs D）
        L += ["", "分年收益（A 现口径 → D 推荐候选）：", ""]
        yA = engine.run(b["df"], b["w"]).loc[b["start"]:]
        yD = engine.run(b["df"], w_vol_target(b, wc, 0.25)).loc[b["start"]:]
        ra = yA.resample("YE").last().pct_change()
        rd = yD.resample("YE").last().pct_change()
        ra.iloc[0] = yA.iloc[:len(yA[yA.index.year == yA.index[0].year])].iloc[-1] - 1.0
        rd.iloc[0] = yD.iloc[:len(yD[yD.index.year == yD.index[0].year])].iloc[-1] - 1.0
        yrs_idx = [d.year for d in ra.index]
        L.append("| 年 | " + " | ".join(str(y) for y in yrs_idx) + " |")
        L.append("|---|" + "---:|" * len(yrs_idx))
        L.append("| A | " + " | ".join(f"{v * 100:+.1f}%" for v in ra.values) + " |")
        L.append("| D | " + " | ".join(f"{v * 100:+.1f}%" for v in rd.values) + " |")
        L.append("")

        # 分段稳健性（3 折 / 2 折）
        edges = (["2018-12-31", "2022-12-31"] if leg == "hs300" else ["2024-06-30"])
        L += ["分段夏普（折数由样本长度决定；**每折独立看，不看合计**）：", "",
              "| 候选 | " + " | ".join(f"第{i + 1}折" for i in range(len(edges) + 1)) + " |",
              "|---|" + "---:|" * (len(edges) + 1)]
        for lbl, w in cands:
            nav = engine.run(b["df"], w).loc[b["start"]:]
            segs, prev = [], b["start"]
            for e in edges + [None]:
                sub = nav.loc[(nav.index >= prev)] if e is None else \
                    nav.loc[(nav.index >= prev) & (nav.index <= pd.Timestamp(e))]
                segs.append(f"{engine.perf(sub)['sharpe']:.2f}" if len(sub) > 40 else "—")
                prev = pd.Timestamp(e) + pd.Timedelta(days=1) if e else prev
            L.append(f"| {lbl[:28]} | " + " | ".join(segs) + " |")
        L.append("")

    # ---- 3.7 组合（两腿）
    L += ["### 3.7 两腿组合：沪深300 + 科创50", "",
          "两条腿共用同一条市场量能、同一个判定链，但价格结构完全不同。"
          "等波动 = 滚动 60 日波动倒数配权（`shift(1)`，无前视）。", ""]
    b1, b2 = data["hs300"], data["kc50"]
    n1 = engine.run(b1["df"], b1["w"]).loc[b1["start"]:]
    n2 = engine.run(b2["df"], b2["w"]).loc[b2["start"]:]
    common = n1.index.intersection(n2.index)
    L += [f"共同窗口 {common[0].date()} ~ {common[-1].date()}（{len(common)} 根）", ""]
    for tag, (x, y) in {"V12 双腿": (n1, n2), "恒持 双腿": (b1["df"]["close"].loc[b1["start"]:],
                                                             b2["df"]["close"].loc[b2["start"]:])}.items():
        for name, pw in (("等权", 0.5), ("等波动", None)):
            if pw is None:
                s1 = (b1["df"]["close"].pct_change().rolling(60).std() * np.sqrt(TD)).loc[common].shift(1)
                s2 = (b2["df"]["close"].pct_change().rolling(60).std() * np.sqrt(TD)).loc[common].shift(1)
                i1 = (1 / s1) / (1 / s1 + 1 / s2)
                i2 = (1 / s2) / (1 / s1 + 1 / s2)
                rp = (x.loc[common].pct_change() * i1.fillna(0.5)
                      + y.loc[common].pct_change() * i2.fillna(0.5))
            else:
                rp = (x.loc[common].pct_change() + y.loc[common].pct_change()) / 2
            p = engine.perf((1 + rp.fillna(0)).cumprod())
            L.append(f"- **{tag} · {name}**：年化 {p['ann'] * 100:+.2f}% · 回撤 {p['mdd'] * 100:+.2f}% · "
                     f"夏普 {p['sharpe']:.2f} · 波动 {p['vol'] * 100:.1f}%")
    L.append("")

    # ================================================================ §4
    L += [
        "## 4. 结论", "",
        "### 4.1 「和 v13 差不多」—— 只在沪深300 上成立",
        "",
        "| 腿 | V12 八态 | V13 九宫格 | 差 | 年线闸门（最简基准） |",
        "|---|---|---|---|---|",
        "| 沪深300 | +9.40% / −20.83% / **0.66** | +9.14% / −24.22% / **0.60** | 年化 +0.26pp、"
        "回撤浅 3.4pp、调仓多 254 次 | +6.51% / −41.63% / 0.29 |",
        "| 科创50 | +4.45% / −26.55% / **0.16** | −3.35% / −28.91% / **−0.34** | 年化 +7.8pp | "
        "**+10.99% / −32.21% / 0.35** |",
        "",
        "**沪深300**：两条规则确实是同一个量级（都是「用掉四成多仓位、换 0.6 夏普」），"
        "v12 只多赚 0.26pp，靠的是回撤浅 3.4pp（−20.8% vs −24.2%），代价是调仓多 254 次。",
        "**科创50**：v12 明显赢 v13（0.16 vs −0.34），但**两条规则都输给一条年线**"
        "（`close > MA250` 就满仓、14 次调仓，0.35）——这是本研究最刺眼的一行，"
        "也是下面「更细的态不是更好的态」的来源。",
        "",
        "### 4.2 钱从哪来、掉在哪（§2）",
        "",
        "- **赚**：沪深300 的 `急涨`（稳态 +10.1pp/年，t=5.2）＋ `慢涨`（+3.2pp，t=3.7）；",
        "  科创50 的 `急涨`（+8.8pp）＋ `强反弹`（+6.9pp，t=2.5）＋ `慢涨`（+5.0pp，t=3.2）。",
        "- **亏**：`强回调` 两条腿都亏（沪深300 −3.5pp / t=−2.5；科创50 −5.9pp / t=−2.0）。",
        "  它不是「持有它会跌」，而是**它占了 16% 的交易日、却被给了 50% 的仓位**。",
        "  科创50 还多两个确定性漏点：`急跌` −7.8pp（t=−3.7）、`慢跌` −3.1pp（t=−4.0）。",
        "- **换态当天**：沪深300 −0.43pp/年（其中「翻成强回调」那天 −6.97pp，被「翻成慢涨/急涨」的"
        "+5.4pp 抵回来）—— 这是确认制的固有成本，**没有规则能消掉**，只能靠降低翻牌前的仓位。",
        "",
        "### 4.3 六个方向，只有两个站得住",
        "",
        "| 方向 | 结论 | 证据 |",
        "|---|---|---|",
        "| 换手层（降格确认 / 最小持有） | **不采纳（量级太小）** | 降格 2 日：沪深300 0.66→0.66、"
        "科创50 0.16→0.21；最小持有 5 日沪深300 反而 0.66→0.50、科创50 0.16→0.19（10 日又掉到 −0.03）"
        "—— 方向不稳，收益量级也不到 0.05 |",
        "| 波动率目标覆盖 | **采纳（高波动腿）** | 科创50 0.16→**0.35**（vt=25%），样本外 0.57→**1.01**，"
        "回撤 −26.6%→−20.2%；沪深300 中性（0.66→0.67） |",
        "| 量能换源（`SH000001`） | **不采纳** | 两腿都更差（0.66→0.61、0.16→0.09） |",
        "| 阈值重标（`z_th`/`rvol_lo`） | **不采纳（不可辨）** | 沪深300 网格平坦且最优点在边界、"
        "后段全负；科创50 单调但最优也在边界（1.8/0.85 → 0.37），等于用「抬高 z_th 抑制急涨」"
        "换收益，与 §3.4b 的证伪同类 |",
        "| 快线拉长（MA20→30/40/60） | **证伪** | 两腿都变差（科创50 0.16→−0.06）；"
        "说明问题不在界线太抖，在**界线画完给多少仓位** |",
        "| 许可映射（`CAP`） | **采纳（核心）** | 单旋钮：`强反弹` 50→100%（沪深300 0.66→0.70，"
        "后段 −0.18→−0.01；科创50 0.16→0.30，两段同向）、`强回调` 50→20%（沪深300 0.66→0.72 且"
        "回撤 −20.8%→−14.9%；科创50 0.16→0.29）；科创50 额外 `急跌` 50→20%（0.16→0.27） |",
        "",
        "### 4.4 推荐候选（逐腿）",
        "",
        "| 腿 | 配置 | 全样本 | 分折夏普 |",
        "|---|---|---|---|",
        "| 沪深300 | **B**：`强反弹` 100% + `强回调` 20%，其余照规格 | +10.25% / −14.79% / **0.75**"
        "（vs 0.66） | 1.33 / 0.60 / 0.14（三折全优于现口径） |",
        "| 科创50 | **D**：B + `急跌` 20% + `σ` 覆盖 vt=25% | +12.60% / −15.33% / **0.76**"
        "（vs 0.16） | −0.37 / 1.46（两折皆优于现口径） |",
        "",
        "对照：**E = 分位重标**（把 8 个态按 fwd20 排序重新分配许可）在沪深300 上夏普更高（0.94），",
        "但它的平均许可只有 **26%**（现口径 45%），第 3 折只有 0.11、科创50 上直接失效（0.16）——",
        "它的收益很大一部分来自「常年低仓」，不是选态更准。**不推荐上线**，只当研究上限。",
        "",
        "### 4.5 边界与风险（用之前必须知道）",
        "",
        "1. **这两条旋钮是看着两段挑的**，独立证据只有一折；真正的确认要等 2026-11 之后的新数据。",
        "   支持它们的不是回测，是**机制**：`CAP` 原本按 fwd20 定，而这条策略持有期只有几天（§3.5 的"
        "标题句）—— 这是**可解释的口径错误**，不是数据挖掘出来的形态。",
        "2. `强反弹` 样本很小（沪深300 59 天、科创50 35 天），t 值 3.9/2.5 好看但**尾部依赖重**；",
        "   上线后如果它在一次假反弹里亏掉一年的贡献，不要意外。",
        "3. D 档的波动覆盖把调仓抬到 41~46 次/年（B 是 33 次），摩擦从 0.49pp/年升到约 0.8pp/年，",
        "   **已经扣在表里**；如果执行成本高于 5bp（比如场外 A 类），这一档立刻不成立。",
        "4. **2021 之后沪深300 的后段夏普全是负的**（−0.01 ~ −0.26，含全部候选与单旋钮）："
        "这不是参数问题，是震荡市里"
        "这条规则本来就没有 alpha。改进只是**少亏**，别把 0.14 当成「正收益」。",
        "5. 本研究**没有**回答「科创50 该不该上线」：它的最优解是年线闸门（0.35）或 D 档（0.76），",
        "   两者机制完全不同，且样本只有 4.7 年。真要上线，应先补齐 2019-2021 的科创50 指数"
        "（`SH000688`）做更长样本，而不是拿 ETF 的 4.7 年定生死。",
        "",
        "### 4.6 如果只做一件事",
        "",
        "**给 `CAP` 加一层「按腿」的能力**（`rule.CAP_BY_LEG`），先把 `强回调` 从 50% 降到 20% ——",
        "它是唯一一条「两条腿、两段样本、t 值、回撤」四个证据同向的改动，改完之后沪深300 的回撤",
        "立刻从 −20.8% 变成 −14.9%，年化还略升。波动率覆盖留给高波动腿当第二层（不改判定、只压风险预算）。",
        "",
        "> 机械规则输出，非投资建议。",
    ]

    with io.open(OUT_MD, "w", encoding="utf-8", newline="\n") as f:
        f.write("\n".join(L))
    print("报告:", OUT_MD)


if __name__ == "__main__":
    main()
