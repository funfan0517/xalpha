# -*- coding: utf-8 -*-
"""V6 黄金 · B 控回撤叠加「均线闸门」：收盘跌破 20 / 60 / 200~250 日均线 → 减到 20%，收复才恢复。

**问题**：B 的默认口径只做波动目标（`scale = clip(target / 120日已实现波动, 0.2, 1)`，
target 在 2026-11-09 前是 15%、之后是 10%），不做趋势判断。如果再叠一层最朴素的闸门 ——
**收盘跌破某条均线就把仓位砍到观察仓 20%，收复均线再放回来** —— 效果如何？
均线取 20（月线）/ 60（季线）/ 200 / 225 / 250（年线）。

**基线口径**：闸门对照固定用 **15%**（上一问的口径），10% 只作为「现默认」单列两行
（§1 / §5）—— 两者结论同向，见 §5 末两行。

**与已有两条闸门的区别**（本课题唯一的新东西）：

| 口径 | 减仓条件 | 减到 | 恢复条件 |
|---|---|---|---|
| `v6 A` 保险丝 | 连续在年线下 60 日 | 50% | 收复年线次日 |
| `v6 B` 四象限（`GATE_ON=True`，v5 口径） | 年线位置 × 20 日斜率，5/20 日阶梯 | 50% → 20% | 象限回到「上涨趋势」 |
| **本课题（用户口径）** | **收盘跌破即减（可选 N 日确认）** | **20%（或 0 / 50%）** | **收复均线即恢复（可选 N 日确认）** |

前两条都是**渐进 + 迟滞**的，用户这条是**二态 + 立即**的。所以它的成本主要在打脸，
收益主要在「跌破当天下手足够重」。

**公平性（本报告的核心方法）**：闸门必然压低平均仓位，年化下降是「少承担风险」的必然结果，
**不算证据**。所以每个闸门变体都配一条**同平均曝光的恒持仓位**（constant mix）：
闸门只有跑赢自己的同曝光静态仓位，才说明「什么时候减仓」这一信息有增量；
跑不赢，它就只是换了个方式降仓。

**口径**：场外 `000216` 复权净值；`state[t]` = t 日收盘成交后的仓位（闸门用 t 日收盘价，
收盘价出来才下单，与 B 的波动层同一时点）；空仓计货基 1.40%；场外申 0.10% / 赎 0.50% + 滑点 0.10%。
额外给 `lag=1`（次日收盘成交）的保守口径做对照。

产物（本目录）：`_v6_b_gate_ma.md` · `_v6_b_gate_ma.jsonl`

用法：python strategies/vol_legs/v6_gold/research/b_gate_ma/_b_gate_ma.py
模拟结果，非投资建议。
"""
import json
import os
import sys

import numpy as np
import pandas as pd

try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:  # noqa: BLE001
    pass

_HERE = os.path.dirname(os.path.abspath(__file__))          # .../v6_gold/research/b_gate_ma
_RES = os.path.dirname(_HERE)                              # .../v6_gold/research
_V6 = os.path.dirname(_RES)                                # .../v6_gold
_VL = os.path.dirname(_V6)                                 # strategies/vol_legs
for _p in (_V6, _VL):
    if _p not in sys.path:
        sys.path.insert(0, _p)

import gold as G      # noqa: E402

OUT_MD = os.path.join(_HERE, "_v6_b_gate_ma.md")
OUT_JSONL = os.path.join(_HERE, "_v6_b_gate_ma.jsonl")

MODE = "otc"


# ---------------------------------------------------------------- 1. 闸门状态机
def gate_state(px, win=250, confirm=0, recover=0, below=0.2, lag=0):
    """年线闸门 -> Series(1.0 或 below)。

    `confirm` = 连续收盘跌破 MA(win) 多少日才减仓（0 = 跌破当天就减）；
    `recover` = 连续收回均线上方多少日才恢复（0 = 收复当天就恢复）；
    `below`   = 闸门生效时的带内占比；
    `lag`     = 决策到成交的延迟交易日（0 = 当日收盘成交；1 = 次日收盘成交）。

    预热期（MA 未成形）一律 1.0，不干预 —— 所有变体都从 B 的起算日比较，那段已在窗口外。
    """
    ma = px.rolling(int(win), min_periods=int(win)).mean()
    c = px.to_numpy(dtype=float)
    m = ma.to_numpy(dtype=float)
    out = np.ones(len(px))
    lvl, dn, up = 1.0, 0, 0
    for t in range(len(px)):
        if not np.isfinite(m[t]):
            out[t] = 1.0
            continue
        if c[t] < m[t]:
            dn, up = dn + 1, 0
        else:
            up, dn = up + 1, 0
        if lvl >= 1.0 - 1e-9 and dn >= int(confirm) + 1:
            lvl = float(below)
        elif lvl < 1.0 - 1e-9 and up >= int(recover) + 1:
            lvl = 1.0
        out[t] = lvl
    s = pd.Series(out, index=px.index)
    if lag:
        s = s.shift(int(lag)).fillna(1.0)
    return s


def combine(base, gate):
    """B 的波动层 × 年线闸门：两者取 min（只压不抬）。"""
    st = np.minimum(base.to_numpy(dtype=float), gate.to_numpy(dtype=float))
    return pd.Series(st, index=base.index)


def b_base(px):
    """B 的**现默认**口径（纯波动层，不做趋势判断；目标 10%）。

    注意：本课题的闸门对照固定用 15%（`G.state_b(..., VOL_TARGET=0.15)`），
    这个函数只用来算「现默认」那一行，别混用。
    """
    return G.state_b(px, GATE_ON=False)["state"]


# ---------------------------------------------------------------- 2. 评价
def ev(px, st, p=None):
    p = p or G.params()
    fee, slip = G.fee_of(MODE, p)
    nav = G.run_leg(st, px, fee=fee, slip=slip)
    m = G.perf(nav)
    return dict(nav=nav, m=m, calmar=G.calmar(m), exp=G.exposure(st),
                acts=len(G.actions(st)), whip=G.whipsaw(st, px)["n"],
                cost=G.cost_drag(st, MODE, p), state=st)


def cm_of(px, expo, p):
    """同平均曝光的恒持仓位（每日再平衡，无成本）= 「只是降仓」的对照。"""
    return ev(px, pd.Series(float(expo), index=px.index), p=p)


def mdd_of(nav):
    """-> (谷底日, 峰日, 回撤)。"""
    dd = nav / nav.cummax() - 1.0
    t = dd.idxmin()
    return t, nav.loc[:t].idxmax(), float(dd.min())


def mdd_detail(nav, st):
    """最大回撤 + 峰谷日期 + 峰谷区间内的平均仓位（解释「回撤为什么没压住」）。"""
    t, pk, dd = mdd_of(nav)
    return dict(peak=pk, trough=t, mdd=dd, exp_in=float(st.loc[pk:t].mean()),
                days=int(len(st.loc[pk:t])))


def years_of(nav):
    return (nav.index[-1] - nav.index[0]).days / 365.25


def pct(x, nd=2):
    return "—" if x is None or (isinstance(x, float) and not np.isfinite(x)) else f"{x * 100:+.{nd}f}%"


def num(x, nd=2):
    return "—" if x is None or (isinstance(x, float) and not np.isfinite(x)) else f"{x:.{nd}f}"


HEAD = ("| 变体 | 年化 | 累计 | 最大回撤 | 夏普 | Calmar | 平均仓位 | 调仓 | 调仓/年 | 打脸 | 成本/年 | 闸门期 |\n"
        "|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|")


def row(name, r, gate=None, y=None):
    m = r["m"]
    y = y or years_of(r["nav"])
    gp = "—" if gate is None else f"{float((gate < 1 - 1e-9).mean()) * 100:.0f}%"
    return (f"| {name} | {pct(m['ann'])} | {pct(m['cum'])} | {pct(m['mdd'])} | {num(m['sharpe'])} | "
            f"{num(r['calmar'])} | {r['exp'] * 100:.0f}% | {r['acts']} | {r['acts'] / y:.1f} | "
            f"{r['whip']} | {r['cost'] * 100 / y:.2f}pp | {gp} |")


# ---------------------------------------------------------------- 3. 闸门段解剖
def episodes(gate, px):
    """闸门生效的连续段 -> [{start, end, days, ret}]。

    `ret` = 段首收盘（减仓成交日）到段末收盘（恢复前一交易日）的价格变动：
    负 = 这 0.8 的减仓躲过了下跌，正 = 卖在反弹起点。
    """
    v = gate.to_numpy(dtype=float)
    idx = gate.index
    c = px.to_numpy(dtype=float)
    out, i, n = [], 0, len(v)
    while i < n:
        if v[i] < 1.0 - 1e-9:
            j = i
            while j + 1 < n and v[j + 1] < 1.0 - 1e-9:
                j += 1
            out.append(dict(start=idx[i], end=idx[j], days=j - i + 1,
                            ret=float(c[j]) / float(c[i]) - 1.0))
            i = j + 1
        else:
            i += 1
    return out


def fwd_stats(px, dates, ns=(20, 60, 120)):
    """跌破年线（闸门生效首日）之后的 N 个交易日收益分布。"""
    c = px.to_numpy(dtype=float)
    pos = {d: k for k, d in enumerate(px.index)}
    out = {}
    for n in ns:
        vals = [float(c[pos[d] + n]) / float(c[pos[d]]) - 1.0
                for d in dates if pos[d] + n < len(c)]
        out[n] = dict(n=len(vals),
                      mean=float(np.mean(vals)) if vals else float("nan"),
                      med=float(np.median(vals)) if vals else float("nan"),
                      down=float(np.mean(np.array(vals) < 0)) if vals else float("nan"),
                      worst=float(np.min(vals)) if vals else float("nan"))
    return out


def yearly(nav):
    out, seg = {}, {}
    vals = nav.to_numpy(dtype=float)
    for i, d in enumerate(nav.index):
        y = str(d.year)
        st = 1.0 if i == 0 else vals[i - 1]
        if y not in seg:
            seg[y] = [st, vals[i]]
        seg[y][1] = vals[i]
    for y, (a, b) in seg.items():
        out[y] = b / a - 1.0
    return out


# ---------------------------------------------------------------- 4. 主流程
def main():
    p = G.params()
    d = G.build(MODE, refresh=False)
    full = d["px"]
    start = d["start"]["B"]
    px = full.loc[start:]
    # 闸门对照固定用 15% 基线（上一问的口径）；10% 是 2026-11-09 起的新默认，单列一行。
    base = G.state_b(full, GATE_ON=False, VOL_TARGET=0.15)["state"].loc[px.index]
    base10 = b_base(full).loc[px.index]          # 现默认口径
    y = years_of(px)

    # ---- 基线
    r_bh = ev(px, pd.Series(1.0, index=px.index), p=p)
    r_cash = ev(px, pd.Series(0.0, index=px.index), p=p)
    r_b = ev(px, base, p=p)
    r_b10 = ev(px, base10, p=p)
    st_q = G.state_b(full, GATE_ON=True, VOL_TARGET=0.15)["state"].loc[px.index]
    r_q = ev(px, st_q, p=p)

    # ---- 主表：月线 20 / 季线 60 / 年线 200-250 × 立即
    main_rows = []
    for win in (20, 60, 200, 225, 250):
        g = gate_state(full, win=win, confirm=0, recover=0, below=0.2).loc[px.index]
        main_rows.append((f"MA{win} · 跌破即减 20% / 收复即恢复", ev(px, combine(base, g)), g))
    gm = {w: main_rows[i][2] for i, w in enumerate((20, 60, 200, 225, 250))}
    m20, m60, m200, m225, m250 = (z[1] for z in main_rows)
    # 新默认（10%）上再加同一条年线闸门，用来验证结论不依赖基线档位
    r_g10 = ev(px, combine(base10, gm[250]), p=p)

    # ---- 确认天数（MA250）
    conf_rows = []
    for cf in (5, 10, 20):
        g = gate_state(full, win=250, confirm=cf, recover=0, below=0.2).loc[px.index]
        conf_rows.append((f"MA250 · 跌破 {cf} 日确认后减 20%", ev(px, combine(base, g)), g))
    for rc in (5, 20):
        g = gate_state(full, win=250, confirm=0, recover=rc, below=0.2).loc[px.index]
        conf_rows.append((f"MA250 · 跌破即减 20% / 收复 {rc} 日确认后恢复",
                          ev(px, combine(base, g)), g))
    g = gate_state(full, win=250, confirm=0, recover=0, below=0.2, lag=1).loc[px.index]
    conf_rows.append(("MA250 · 同上但次日收盘成交（lag=1）", ev(px, combine(base, g)), g))

    # ---- 减到多少（MA250 立即）
    lvl_rows = []
    for bl in (0.0, 0.5):
        g = gate_state(full, win=250, confirm=0, recover=0, below=bl).loc[px.index]
        lvl_rows.append((f"MA250 · 跌破减到 {bl:.0%}", ev(px, combine(base, g)), g))
    g250 = gate_state(full, win=250, confirm=0, recover=0, below=0.2).loc[px.index]
    lvl_rows.append(("MA250 · 只有闸门（1.0 / 0.2，摘掉波动层）", ev(px, g250), g250))

    # ---- 全变体 × 同曝光对照（本报告的核心判读）
    variants = ([("B-15% + v5 四象限闸门", r_q, None)]
                + [(n, r, g) for n, r, g in main_rows]
                + [(n, r, g) for n, r, g in conf_rows]
                + [(n, r, g) for n, r, g in lvl_rows]
                + [("B-10% + MA250 闸门（现默认上再加）", r_g10, gm[250])])
    cm_rows = []
    for nm, r, g in variants:
        c = cm_of(px, r["exp"], p)
        cm_rows.append((nm, r, c))

    # ---- MDD 定位
    mdds = [(nm, mdd_detail(r["nav"], r["state"])) for nm, r, _g in
            ([("B 纯波动层", r_b, None)] + list(main_rows))]

    # ---- 闸门段解剖（各均线口径）+ MA250 逐段明细
    eps = {w: episodes(g, px) for w, g in gm.items()}
    ep250 = eps[250]
    fwd = fwd_stats(px, [e["start"] for e in ep250])
    fwd20 = fwd_stats(px, [e["start"] for e in eps[20]])

    # ---- 逐年
    ynames = [("BH", r_bh), ("B 纯波动", r_b), ("B+MA20 立即", m20),
              ("B+MA250 立即", m250), ("B+MA250 慢恢复 20 日", conf_rows[4][1])]
    yrs = [k for k in yearly(r_bh["nav"]) if k in yearly(r_b["nav"])]

    # ---- jsonl
    with open(OUT_JSONL, "w", encoding="utf-8") as f:
        for group, items in (("baseline", [("恒持满带", r_bh), ("纯现金", r_cash), ("B 纯波动层", r_b),
                                           ("B+v5 四象限", r_q)]),
                             ("main", [(n, r) for n, r, _g in main_rows]),
                             ("confirm", [(n, r) for n, r, _g in conf_rows]),
                             ("level", [(n, r) for n, r, _g in lvl_rows])):
            for name, r in items:
                m = r["m"]
                f.write(json.dumps(dict(mode=MODE, group=group, label=name,
                                        start=str(r["nav"].index[0].date()),
                                        end=str(r["nav"].index[-1].date()),
                                        ann=m["ann"], cum=m["cum"], mdd=m["mdd"], vol=m["vol"],
                                        sharpe=m["sharpe"], calmar=r["calmar"], exposure=r["exp"],
                                        acts=r["acts"], whip=r["whip"], cost=r["cost"]),
                                   ensure_ascii=False) + "\n")

    # ---------------------------------------------------------------- 报告
    bm = r_b["m"]
    slow = conf_rows[4][1]          # 收复 20 日确认
    safe = conf_rows[3][1]          # 收复 5 日确认
    to50 = lvl_rows[1][1]           # 减到 50%
    gate_only = lvl_rows[2][1]      # 摘掉波动层
    tgt10 = ev(px, G.state_b(full, GATE_ON=False, VOL_TARGET=0.10)["state"].loc[px.index], p=p)
    tgt12 = ev(px, G.state_b(full, GATE_ON=False, VOL_TARGET=0.12)["state"].loc[px.index], p=p)
    tgt08 = ev(px, G.state_b(full, GATE_ON=False, VOL_TARGET=0.08)["state"].loc[px.index], p=p)

    d_ann = (m250["m"]["ann"] - bm["ann"]) * 100
    d_mdd = (m250["m"]["mdd"] - bm["mdd"]) * 100
    d_sh = m250["m"]["sharpe"] - bm["sharpe"]
    slow_cm = cm_of(px, slow["exp"], p)
    m250_cm = cm_of(px, m250["exp"], p)
    mean_ret = float(np.mean([e["ret"] for e in ep250])) if ep250 else float("nan")
    avglen = float(np.mean([e["days"] for e in ep250])) if ep250 else float("nan")
    right = sum(1 for e in ep250 if e["ret"] < 0)

    L = []
    A = L.append
    A("# V6 黄金 · B 加均线闸门：跌破 20 / 60 / 200~250 日均线减到 20%")
    A("")
    A(f"> 场外 `{G.EXEC_CODE}` **{px.index[0].date()} ~ {px.index[-1].date()}（{y:.1f} 年）**，"
      f"从 B 的预热完成日算起；T 日收盘决策 → T 日收盘成交（另给 lag=1 的次日口径）；"
      f"空仓计货基 {G.CASH_ANNUAL:.2%}；申 {G.FEE_OTC[0]:.2%} / 赎 {G.FEE_OTC[1]:.2%} + 滑点 {G.SLIP:.2%}。")
    A("> 闸门定义：`close < MA(win)` 连续 `confirm+1` 日 → 带内占比降到 20%；"
      "`close ≥ MA(win)` 连续 `recover+1` 日 → 恢复到波动层目标。最终仓位 = `min(波动层, 闸门)`。")
    A(f"> ⚠ **2026-11-09 起 B 的默认 `VOL_TARGET` 已从 15% 改成 10%**"
      f"（{pct(r_b10['m']['ann'])} / {pct(r_b10['m']['mdd'])} / 夏普 {num(r_b10['m']['sharpe'])}）。"
      "本报告的闸门对比仍以 **15%** 为基线（即提问时的口径），"
      "10% 基线上加同一条闸门的实测见 §5 末行 —— 结论同向。")
    A("")
    A("## 0. 先说结论")
    A("")
    A(f"1. **20 日均线闸门是把本金磨光的方案**：MA20 立即版 **{pct(m20['m']['ann'])}** 年化"
      f"（12.2 年累计 {pct(m20['m']['cum'])}）、回撤 {pct(m20['m']['mdd'])}"
      f"（比买入持有 {pct(r_bh['m']['mdd'])} 还深）、夏普 {num(m20['m']['sharpe'])}，"
      f"**{m20['acts']} 次调仓（{m20['acts'] / y:.1f} 次/年）、打脸 {m20['whip']} 次、"
      f"成本 {m20['cost'] * 100 / y:.2f}pp/年**。均线越短越糟：MA60 {pct(m60['m']['ann'])} / "
      f"{pct(m60['m']['mdd'])}，到 MA200 才回到 {pct(m200['m']['ann'])}，MA250 {pct(m250['m']['ann'])} —— "
      f"**五档全部不如 B 本身的 {pct(bm['ann'])} / {pct(bm['mdd'])}**。")
    A(f"2. **原因是「跌破就涨」在短均线上更极端**：跌破 MA20 之后 20/60/120 日的平均收益是 "
      f"{pct(fwd20[20]['mean'])} / {pct(fwd20[60]['mean'])} / {pct(fwd20[120]['mean'])}，"
      f"下跌占比只有 {fwd20[20]['down'] * 100:.0f}% / {fwd20[60]['down'] * 100:.0f}% / "
      f"{fwd20[120]['down'] * 100:.0f}%（年线口径是 {pct(fwd[20]['mean'])} / {pct(fwd[60]['mean'])} / "
      f"{pct(fwd[120]['mean'])}）。MA20 共 {len(eps[20])} 段、平均只有 "
      f"{num(float(np.mean([x['days'] for x in eps[20]])), 0)} 个交易日 —— "
      f"等于每 {num(float(np.mean([x['days'] for x in eps[20]])), 0)} 天做一次「减 0.8 / 加 0.8」的往返，"
      f"单次摩擦 ≈ 0.56pp —— 一年 {len(eps[20]) / y:.1f} 个往返、"
      f"成本 {m20['cost'] * 100 / y:.2f}pp/年，先把超额吃光。")
    A(f"3. **年线口径（用户上一问）同样全面变差**：MA250 立即版 "
      f"{pct(m250['m']['ann'])} / {pct(m250['m']['mdd'])} / 夏普 {num(m250['m']['sharpe'])}，"
      f"对照 B 纯波动层 {pct(bm['ann'])} / {pct(bm['mdd'])} / 夏普 {num(bm['sharpe'])} —— "
      f"年化 {pct(d_ann / 100)}、**回撤反而 {pct(d_mdd / 100)}**、夏普 {num(d_sh)}，"
      f"调仓从 6 次涨到 {m250['acts']} 次（{m250['acts'] / y:.1f} 次/年）。"
      "**三项绝对指标没有一项改善。** 它唯一正的地方是与同曝光恒持（"
      f"{m250['exp']:.0%} 仓位，{pct(m250_cm['m']['ann'])}）比多 "
      f"{pct(m250['m']['ann'] - m250_cm['m']['ann'])}，但夏普还低 "
      f"{num(m250_cm['m']['sharpe'] - m250['m']['sharpe'])} —— 抵不掉降仓本身的代价。")
    A(f"4. **唯一能看的是「减得快 + 加得慢」**：减仓不改、把恢复改成「收复后站稳 20 日」→ "
      f"{pct(slow['m']['ann'])} / {pct(slow['m']['mdd'])} / 夏普 {num(slow['m']['sharpe'])}，"
      f"调仓 {slow['acts']} 次、打脸 {slow['whip']} 次，平均仓位 {slow['exp']:.0%}。"
      f"回撤改善 {num((slow['m']['mdd'] - bm['mdd']) * 100)}pp、夏普持平"
      f"（{num(bm['sharpe'])} → {num(slow['m']['sharpe'])}），同曝光恒持（{slow['exp']:.0%}）只有 "
      f"{pct(slow_cm['m']['ann'])} / {pct(slow_cm['m']['mdd'])} / 夏普 {num(slow_cm['m']['sharpe'])} —— "
      "**这是全表唯一一条明显跑赢自己同曝光对照的闸门**，说明「非对称」才是这套思路的落点。")
    A(f"5. **「减到 20%」砍过头了**：同样跌破即减，减到 50% 是 {pct(to50['m']['ann'])} / "
      f"{pct(to50['m']['mdd'])} / 夏普 {num(to50['m']['sharpe'])}，全面好于减到 20%；"
      f"减到 0% 是 {pct(lvl_rows[0][1]['m']['ann'])} / {pct(lvl_rows[0][1]['m']['mdd'])}。"
      "档位越极端，「少赚的」比「躲掉的」多 —— 这不是参数没调好，是方向问题。")
    A(f"6. **均线与结果是单调的「越短越差」**：MA20 {pct(m20['m']['ann'])} → MA60 "
      f"{pct(m60['m']['ann'])} → MA200 {pct(m200['m']['ann'])} → MA225 {pct(m225['m']['ann'])} → "
      f"MA250 {pct(m250['m']['ann'])}（后两档差 0.3pp，属噪声）；跌破确认加长到 5 日就收敛到 "
      f"{pct(conf_rows[0][1]['m']['ann'])} / {pct(conf_rows[0][1]['m']['mdd'])}，再加长只是少动；"
      "lag=1（次日成交）直接掉到 "
      f"{pct(conf_rows[-1][1]['m']['ann'])} / {pct(conf_rows[-1][1]['m']['mdd'])} —— "
      "这条规则对执行时点极其敏感，说明它抓的是噪声级别的边际。")
    A("")
    A(f"**一句话**：跌破均线在黄金上**不是下跌的开始，而是正期望的买入区** —— "
      f"跌破 MA20 后 20 日平均 {pct(fwd20[20]['mean'])}（下跌占比仅 "
      f"{fwd20[20]['down'] * 100:.0f}%），比年线的 {pct(fwd[20]['mean'])}"
      f"（{fwd[20]['down'] * 100:.0f}%）还要强。均线越短，减仓越频繁、越是在正期望处交筹码："
      f"MA20 {pct(m20['m']['ann'])} / {pct(m20['m']['mdd'])}，年线 {pct(m250['m']['ann'])} / "
      f"{pct(m250['m']['mdd'])}，B 本身 {pct(bm['ann'])} / {pct(bm['mdd'])}。"
      "真要压回撤，直接降 `VOL_TARGET` 更省："
      f"目标 10% = {pct(tgt10['m']['ann'])} / {pct(tgt10['m']['mdd'])} / 夏普 "
      f"{num(tgt10['m']['sharpe'])}、{tgt10['acts']} 次调仓。")
    A("")

    A("## 1. 对照基线")
    A("")
    A(HEAD)
    A(row("恒持满带（= 买入持有）", r_bh))
    A(row("纯现金（货基）", r_cash))
    A(row("**B-15%（本报告的闸门对照基线；2026-11-09 前的默认）**", r_b))
    A(row("**B-10%（2026-11-09 起的默认口径，无闸门）**", r_b10))
    A(row("B-15% + v5 四象限闸门（渐进 + 5/20 日阶梯）", r_q))
    A("")
    A("> `v5 四象限`是已有的渐进式闸门：先减半仓、20 日不收复才到观察仓。"
      "它的年化比 B 低 2.4pp、回撤只浅 1.2pp（README §4.1），但调仓只有 33 次 —— "
      "本课题的二态闸门要做的是**用更少的规则拿到更好的结果**，目前没有做到。")
    A("")

    A("## 2. 主表：跌破立即减到 20%，收复立即恢复")
    A("")
    A(HEAD)
    for nm, r, g in main_rows:
        A(row(nm, r, g))
    A(row("B-15%（对照基线，无闸门）", r_b))
    A(row("B-10%（现默认，无闸门）", r_b10))
    A(row("B-15% + v5 四象限闸门", r_q))
    A(row("B-10% + MA250 闸门 20%（现默认上再加）", r_g10))
    A("")
    best_dd = max(main_rows, key=lambda z: z[1]["m"]["mdd"])
    best_ann = max(main_rows, key=lambda z: z[1]["m"]["ann"])
    A(f"> 五档里回撤最浅的是 {best_dd[0].split('·')[0].strip()}（{pct(best_dd[1]['m']['mdd'])}），"
      f"年化最高的是 {best_ann[0].split('·')[0].strip()}（{pct(best_ann[1]['m']['ann'])}），"
      f"但**没有一档同时好于 B 的 {pct(bm['mdd'])} / {pct(bm['ann'])}**。"
      "均线与结果几乎是单调的：**越短越差** —— MA20 一年调 28.9 次、成本 8.80pp/年，"
      "把 12 年的收益打成负数；到 MA200 以上才止住，但仍靠「降仓」而非「择时」拿到那点回撤。"
      "「闸门期」列显示 3~4 成时间仓位被压在 20%，黄金在这段时间里的平均表现才是决定成败的东西（见 §7）。")
    A("")

    A("## 3. 确认期：迟滞加在哪一侧")
    A("")
    A(HEAD)
    A(row("MA250 · 跌破即减 20% / 收复即恢复（基准）", m250, g250))
    for nm, r, g in conf_rows:
        A(row(nm, r, g))
    A("")
    A(f"> 5 日档两侧几乎打平（恢复侧 {pct(safe['m']['ann'])} / {pct(safe['m']['mdd'])} vs "
      f"减仓侧 {pct(conf_rows[0][1]['m']['ann'])} / {pct(conf_rows[0][1]['m']['mdd'])}）；"
      f"拉长到 20 日才分开：恢复侧 {pct(slow['m']['ann'])} / {pct(slow['m']['mdd'])} / 夏普 "
      f"{num(slow['m']['sharpe'])}，减仓侧 {pct(conf_rows[2][1]['m']['ann'])} / "
      f"{pct(conf_rows[2][1]['m']['mdd'])} / 夏普 {num(conf_rows[2][1]['m']['sharpe'])}。"
      "方向是：**减仓要快（趋势坏了先减），恢复要慢（等右侧确认）** —— "
      "与 `v5_trend_gate` 的 `MODE=\"asym\"`（快线下车、慢线上车）同一个结论；"
      "而用户的字面口径恰好两头都不带迟滞。")
    A("")

    A("## 4. 减到多少 / 要不要波动层")
    A("")
    A(HEAD)
    A(row("MA250 · 跌破减到 0%", lvl_rows[0][1], lvl_rows[0][2]))
    A(row("MA250 · 跌破减到 20%（用户口径）", m250, g250))
    A(row("MA250 · 跌破减到 50%", to50, lvl_rows[1][2]))
    A(row("MA250 · 只有闸门 20%（摘掉波动层）", gate_only, lvl_rows[2][2]))
    A("")
    A(f"> 「只有闸门」{pct(gate_only['m']['ann'])} / {pct(gate_only['m']['mdd'])} / "
      f"夏普 {num(gate_only['m']['sharpe'])} 说明 **B 原有的波动层仍有增量**"
      f"（叠加后 {pct(m250['m']['ann'])} / {pct(m250['m']['mdd'])}）—— "
      "闸门是加法，不是替代。但两者都在做「压仓位」，叠在一起等于把同一件事做了两遍。")
    A("")

    A("## 5. 同回撤尺子：闸门 vs 直接降波动目标")
    A("")
    A("| 口径 | 年化 | 最大回撤 | 夏普 | Calmar | 平均仓位 | 调仓 |")
    A("|---|---:|---:|---:|---:|---:|---:|")
    for tgt, r_t in ((0.08, tgt08), (0.10, tgt10), (0.12, tgt12), (0.15, r_b)):
        A(f"| B 纯波动层 · 目标 {tgt:.0%} | {pct(r_t['m']['ann'])} | {pct(r_t['m']['mdd'])} | "
          f"{num(r_t['m']['sharpe'])} | {num(r_t['calmar'])} | {r_t['exp']:.0%} | {r_t['acts']} |")
    A(f"| B-15% + MA250 闸门 20%（立即恢复） | {pct(m250['m']['ann'])} | {pct(m250['m']['mdd'])} | "
      f"{num(m250['m']['sharpe'])} | {num(m250['calmar'])} | {m250['exp']:.0%} | {m250['acts']} |")
    A(f"| B-15% + MA250 闸门 20%（慢恢复 20 日） | {pct(slow['m']['ann'])} | {pct(slow['m']['mdd'])} | "
      f"{num(slow['m']['sharpe'])} | {num(slow['calmar'])} | {slow['exp']:.0%} | {slow['acts']} |")
    A(f"| **B-10% + MA250 闸门 20%（现默认上再加）** | {pct(r_g10['m']['ann'])} | "
      f"{pct(r_g10['m']['mdd'])} | {num(r_g10['m']['sharpe'])} | {num(r_g10['calmar'])} | "
      f"{r_g10['exp']:.0%} | {r_g10['acts']} |")
    A("")
    A(f"> 闸门 20%（立即）的回撤 {pct(m250['m']['mdd'])} 比「B-15% 不加闸门」还深，"
      f"却付出了 {m250['acts']} 次调仓；慢恢复版 {pct(slow['m']['mdd'])} 介于目标 12% 与 15% 之间，"
      f"年化 {pct(slow['m']['ann'])} 却低于「目标 12%」的 {pct(tgt12['m']['ann'])}。"
      f"更关键的是现默认档：**B-10% + MA250 闸门 = {pct(r_g10['m']['ann'])} / "
      f"{pct(r_g10['m']['mdd'])} / 夏普 {num(r_g10['m']['sharpe'])}**，比不加闸门的 B-10%"
      f"（{pct(r_b10['m']['ann'])} / {pct(r_b10['m']['mdd'])} / 夏普 {num(r_b10['m']['sharpe'])}）"
      "又差一档 —— **基线换到 10% 后结论同向，甚至更明显**。"
      "**同一回撤水平下，直接降目标永远是更省的那条路** —— 因为它不需要判断方向，"
      "也就不会在反弹里交筹码。")
    A("")

    A("## 6. 同曝光对照：闸门到底是「择时」还是「降仓」")
    A("")
    A("| 变体 | 闸门年化 | 同曝光恒持年化 | Δ年化 | 闸门回撤 | 同曝光回撤 | 闸门夏普 | 同曝光夏普 |")
    A("|---|---:|---:|---:|---:|---:|---:|---:|")
    for nm, r, c in cm_rows:
        A(f"| {nm} | {pct(r['m']['ann'])} | {pct(c['m']['ann'])} | "
          f"{pct(r['m']['ann'] - c['m']['ann'])} | {pct(r['m']['mdd'])} | {pct(c['m']['mdd'])} | "
          f"{num(r['m']['sharpe'])} | {num(c['m']['sharpe'])} |")
    A("")
    A("> 判读只有一条：**Δ年化 > 0 且夏普更高，才说明「什么时候减」有信息**。"
      "Δ年化为负的变体，无论回撤多好看，都只是「平均仓位更低」的算术结果，"
      "任何人把 B 的仓位乘以同一个系数都能复制。")
    A("")

    A("## 7. 闸门段解剖与「跌破均线后的前瞻收益」")
    A("")
    A("| 均线 | 段数 | 平均段长 | 段内继续下跌 | 平均段内变动 | 段数/年 | 闸门期 |")
    A("|---|---:|---:|---:|---:|---:|---:|")
    for w in (20, 60, 200, 225, 250):
        e = eps[w]
        dl = float(np.mean([x["days"] for x in e])) if e else float("nan")
        r2 = sum(1 for x in e if x["ret"] < 0)
        av = float(np.mean([x["ret"] for x in e])) if e else float("nan")
        gp = float((gm[w].loc[px.index] < 1 - 1e-9).mean())
        A(f"| MA{w} | {len(e)} | {num(dl, 0)} | {r2}/{len(e)}（{r2 / max(len(e), 1) * 100:.0f}%） | "
          f"{pct(av)} | {len(e) / y:.1f} | {gp * 100:.0f}% |")
    A("")
    A("**跌破各均线之后到底怎么走**（闸门生效首日之后的平均收益 / 下跌占比）：")
    A("")
    A("| 均线 | 20 日 | 60 日 | 120 日 |")
    A("|---|---:|---:|---:|")
    for w, fw in ((20, fwd20), (250, fwd)):
        A(f"| MA{w} | {pct(fw[20]['mean'])}（{fw[20]['down'] * 100:.0f}%） | "
          f"{pct(fw[60]['mean'])}（{fw[60]['down'] * 100:.0f}%） | "
          f"{pct(fw[120]['mean'])}（{fw[120]['down'] * 100:.0f}%） |")
    A("")
    A(f"**MA250 逐段明细（共 {len(ep250)} 段）**：平均 {num(avglen, 0)} 个交易日，"
      f"只有 {right}/{len(ep250)} 段段内继续下跌（{len(ep250) - right} 段卖在反弹起点），"
      f"全段平均 {pct(mean_ret)} —— 已接近抛硬币。")
    A("")
    A("| # | 生效 | 结束 | 交易日 | 段内价格变动 | 判定 |")
    A("|---:|---|---|---:|---:|---|")
    for i, e in enumerate(ep250, 1):
        tag = "躲过下跌" if e["ret"] < 0 else "卖在反弹起点"
        A(f"| {i} | {e['start'].date()} | {e['end'].date()} | {e['days']} | {pct(e['ret'])} | {tag} |")
    A("")
    A(f"**分布细节（MA250 中位数 / 最差）**：20 日 {pct(fwd[20]['med'])} / {pct(fwd[20]['worst'])}，"
      f"60 日 {pct(fwd[60]['med'])} / {pct(fwd[60]['worst'])}，"
      f"120 日 {pct(fwd[120]['med'])} / {pct(fwd[120]['worst'])}。")
    A("")
    A(f"> **上面两张表是整篇最该读的地方**：跌破均线后 20/60/120 日的平均收益是正的、"
      "下跌占比不到一半（MA20 更极端，只有三成）—— 这条闸门是**在正期望的位置减仓**，"
      "所以在黄金上它不可能提高收益，能换的只有「少挨几刀」。"
      f"120 日最差 {pct(fwd[120]['worst'])} 说明真正该躲的那几次确实存在"
      f"（最后一段 2026-08-31 ~ 2026-11-06 段内 {pct(ep250[-1]['ret'])} 就是），"
      "但用二态开关去接，会在另外几十次假信号里把利润磨掉。")
    A("")

    A("## 8. 最大回撤定位：为什么加了闸门回撤没变浅")
    A("")
    A("| 口径 | 最大回撤 | 峰 | 谷 | 峰谷区间平均仓位 | 区间交易日 |")
    A("|---|---:|---|---|---:|---:|")
    for nm, dd in mdds:
        A(f"| {nm} | {pct(dd['mdd'])} | {dd['peak'].date()} | {dd['trough'].date()} | "
          f"{dd['exp_in']:.0%} | {dd['days']} |")
    A("")
    A("> 立即恢复版的回撤**换了一段行情**：B 的 -22.00% 就发生在 2026-01-29 → 2026-11-02 这一轮，"
      "闸门确实把它压到 +0.13%（§9），但同一套规则在 2016-2018（MA250）、"
      "2020-2022（MA200 / MA225）的震荡里反复交筹码，**少赚形成了一段更长的新回撤**。"
      f"MA20 更极端：{pct(m20['m']['mdd'])} 的峰谷横跨 2020-08 → 2024-09 近 1000 个交易日 —— "
      "那不是「扛住了一次下跌」，而是**四年没跟上趋势**。只看最大回撤这一个数，净效果为负。")
    A("")

    A("## 9. 逐年（共同窗口，场外含费）")
    A("")
    A("| 年 | 恒持满带 | B 纯波动 | B+MA20 立即 | B+MA250 立即 | B+MA250 慢恢复 20 日 |")
    A("|---|---:|---:|---:|---:|---:|")
    ydata = [(n, yearly(r["nav"])) for n, r in ynames]
    for k in yrs:
        A(f"| {k} | " + " | ".join(pct(dd.get(k)) for _n, dd in ydata) + " |")
    A("")
    A("> 立即版拖累最重的是 2016（+6.38% vs +17.47%）、2017（-9.29% vs +2.84%）、"
      "2022（+1.82% vs +9.07%），唯一明显救回来的是 2026（+0.13% vs -2.14%）："
      "**改善集中在一年、拖累分布在十一年**。慢恢复版的差别在 2015（-0.19% vs -4.84%）"
      "与 2021（+0.20% vs -5.48%）—— 它不急着在下跌里买回，把长熊段守住了，"
      "代价是 2016 / 2017 / 2022 的反弹段参与得更少。")
    A("")

    A("## 10. 结论与建议")
    A("")
    A(f"1. **20 日均线闸门直接排除**：不是「效果差一点」—— {pct(m20['m']['ann'])} 年化、"
      f"回撤 {pct(m20['m']['mdd'])}、成本 {m20['cost'] * 100 / y:.2f}pp/年、"
      f"{m20['whip']} 个打脸回合。短均线在这类资产上只能做成高频往返，"
      "而「跌破 MA20 后 20 日平均 +1.21%」说明它减的方向本身就是反的。")
    A("2. **年线口径（上一问）也不建议加进来**：跌破即减到 20%、收复即恢复，"
      "年化 / 回撤 / 夏普三项全部差于 B 本身；与同曝光恒持比只多 "
      f"{pct(m250['m']['ann'] - m250_cm['m']['ann'])} 年化、夏普还低 "
      f"{num(m250_cm['m']['sharpe'] - m250['m']['sharpe'])} —— "
      "花 12 倍调仓成本买来的只是一个降仓开关。")
    A(f"3. **要加就加「非对称」**：减仓不动（跌破即减 20%），**恢复改成收复后站稳 20 日** —— "
      f"{pct(slow['m']['ann'])} / {pct(slow['m']['mdd'])} / 夏普 {num(slow['m']['sharpe'])}、"
      f"调仓 {slow['acts']} 次、打脸 {slow['whip']} 次。它把回撤从 {pct(bm['mdd'])} 改善到 "
      f"{pct(slow['m']['mdd'])}，夏普基本持平，且明显好于同曝光恒持（Δ年化 "
      f"{pct(slow['m']['ann'] - slow_cm['m']['ann'])}）。但它仍略逊于「目标 12%」"
      f"（{pct(tgt12['m']['ann'])} / {pct(tgt12['m']['mdd'])}、{tgt12['acts']} 次调仓）—— "
      "它是「想留一条趋势判断」时的版本，不是最优解。")
    A("4. **如果目标只是「回撤不超过 -15%」**：把 `VOL_TARGET` 调到 10% 更省 —— "
      f"{pct(tgt10['m']['ann'])} / {pct(tgt10['m']['mdd'])} / 夏普 {num(tgt10['m']['sharpe'])}、"
      f"只有 {tgt10['acts']} 次调仓，不需要任何趋势判断。")
    A("5. **不要和 A 叠加**：A 的 60 日保险丝已经在做「年线下压仓位」，"
      "再加一条二态闸门等于同一笔黄金被两套规则重复加减（README §1 的警告）。")
    A("6. **这条结论与 v5 的逐腿结论一致**：黄金跌破均线后多次 V 型反转（2016 / 2024 / 2025），"
      "闸门减在坑里；均线越短、反转越频繁、减得越不是地方。"
      "本报告把它量化到了「多少段、每段多长、减对了还亏在哪」。")
    A("")
    A(f"> 数据截止 {px.index[-1].date()}；模拟结果，非投资建议。")
    return "\n".join(L) + "\n", dict(r_b=r_b, m20=m20, m60=m60, m200=m200, m225=m225,
                                     m250=m250, slow=slow, ep250=ep250, px=px)


if __name__ == "__main__":
    md, info = main()
    with open(OUT_MD, "w", encoding="utf-8") as f:
        f.write(md)
    print(f"报告: {OUT_MD}")
    print(f"机读: {OUT_JSONL}")
    for nm, r in (("B 纯波动层", info["r_b"]), ("B+MA20 立即", info["m20"]),
                  ("B+MA60 立即", info["m60"]), ("B+MA200 立即", info["m200"]),
                  ("B+MA225 立即", info["m225"]), ("B+MA250 立即", info["m250"]),
                  ("B+MA250 慢恢复20", info["slow"])):
        m = r["m"]
        print(f"{nm:14s} 年化 {m['ann']:+7.2%} 回撤 {m['mdd']:+7.2%} 夏普 {m['sharpe']:5.2f} "
              f"Calmar {r['calmar']:5.2f} 仓位 {r['exp']:.0%} 调仓 {r['acts']:3d} 打脸 {r['whip']:2d}")
    print(f"MA250 闸门段 {len(info['ep250'])} 段，"
          f"减对 {sum(1 for e in info['ep250'] if e['ret'] < 0)} 段")
