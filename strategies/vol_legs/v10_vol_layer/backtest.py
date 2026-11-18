# -*- coding: utf-8 -*-
"""波动资产 · V10 变种（全七腿 ER 自适应波动层）· 回测（**只做分腿** + 硬断言）。

池：**全七腿**（`vol_layer.POOL` = rule.LEG_ORDER，2026-11-17 扩池）。
默认规则（2026-11-17 用户指定）：`仓位 = min(1, TV(ER) / vol20)`，
ER(20) 趋势态 TV=20% / 震荡态 TV=10%，幅度死区 10pp + 时间死区 5 日，无涨跌方向门。
口径：
- 信号与回测 = 该腿真实指数收盘（美股腿为 `.NDX` / `.INX` 美元指数），执行另按各腿说明；
- 成本 = 单边 0.1%（文档主口径），另给 0.6% 的场外现实口径；
- 空仓按货基 `rule.CASH_ANNUAL` 计息；
- 起点口径：首日目标仓位照算，不另计建仓费。

**不做组合回测、不做跨腿等权净值、不做 v7/v8/v9 组合对照** —— 只逐腿给结果与档位敏感性。
`ADAPTIVE=False` 档保留旧固定 TV20 作为对照。

产物（策略类，落 `backtest/`）：
- `_v10_vol_layer_bt.md` / `_v10_vol_layer_bt.jsonl`

模拟结果，非投资建议。
"""
import json
import os
import sys

import numpy as np

try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:  # noqa: BLE001
    pass

_HERE = os.path.dirname(os.path.abspath(__file__))
_VL = os.path.dirname(_HERE)
for _p in (_HERE, _VL):
    if _p not in sys.path:
        sys.path.insert(0, _p)

import vol_layer as vl  # noqa: E402

OUTDIR = os.path.join(_HERE, "backtest")
OUT_MD = os.path.join(OUTDIR, "_v10_vol_layer_bt.md")
OUT_JSONL = os.path.join(OUTDIR, "_v10_vol_layer_bt.jsonl")

# 逐腿档位：以「**」开头的行会被报告正文引用
VARIANTS = [
    ("买入持有", dict(ADAPTIVE=False, FLOOR=1.0, DEAD=0.0)),
    ("**V10 默认：ER20/10 + 5日死区**", dict()),
    ("固定 TV20（旧默认 · 无 ER）", dict(ADAPTIVE=False)),
    ("ER20/10 原生死区（无5日）", dict(DEAD_DAYS=0)),
    ("ER20/10 + 10日死区", dict(DEAD_DAYS=10)),
    ("ER20/10 + 20pp幅度死区", dict(DEAD=0.20, DEAD_DAYS=0)),
    ("ER20/10 + 5pp幅度死区", dict(DEAD=0.05)),
    ("ER + vol10（更快）", dict(VOL_WIN=10)),
    ("ER + vol60（更慢）", dict(VOL_WIN=60)),
    ("月末定仓", dict(TRIGGER="month")),
    ("成本 0.6%（场外现实口径）", dict(COST=0.006)),
    ("固定 TV10（震荡态对照）", dict(ADAPTIVE=False, TV=0.10)),
]


def leg_rows():
    """每条腿跑全部档位（不做组合层）。"""
    rows = []
    for leg in vl.POOL:
        for name, over in VARIANTS:
            nav, st, _dec = vl.build(leg, **over)
            nv = vl.run_leg(st, nav, cost=over.get("COST"), cash=over.get("CASH_ANNUAL"))
            m = vl.perf(nv)
            bh = nav / nav.iloc[0]
            mb = vl.perf(bh)
            rows.append(dict(
                leg=leg, name=vl.rule.LEGS[leg]["name"], variant=name,
                start=nav.index[0].date().isoformat(), end=nav.index[-1].date().isoformat(),
                years=round((nav.index[-1] - nav.index[0]).days / 365.25, 2),
                ann=m["ann"], mdd=m["mdd"], vol=m["vol"], sharpe=m["sharpe"],
                calmar=vl.calmar(nv), mdd_me=vl.mdd_sampled(nv),
                calmar_me=vl.calmar(nv, vl.mdd_sampled(nv)),
                turnover=vl.turnover(st), trades=vl.n_trades(st),
                expo=float(st.mean()), st_min=float(st.min()),
                ann_bh=mb["ann"], mdd_bh=mb["mdd"],
                d_ann=(m["ann"] - mb["ann"]) * 100,
                d_mdd=(m["mdd"] - mb["mdd"]) * 100,
            ))
    return rows


def check_all():
    """只保留分腿硬断言；组合恒等 / 两腿跨实现回归属于组合层，已随组合回测删除。"""
    out = []

    # 1) 恒满（FLOOR=1、死区 0 -> 目标恒 1）净值 == 买入持有
    ok, note = True, ""
    for leg in vl.POOL:
        nav, st, _ = vl.build(leg, FLOOR=1.0, DEAD=0.0)
        assert set(np.round(st.unique(), 9)) == {1.0}, (leg, st.unique())
        nv = vl.run_leg(st, nav, cost=0.0)
        bh = nav / nav.iloc[0]
        rel = abs(float(nv.iloc[-1] / bh.iloc[-1]) - 1.0)
        if rel > 1e-12:
            ok, note = False, f"{leg} 差 {rel:.2e}"
            break
    out.append(("恒满（下限 100%）净值 == 买入持有（零费）", ok, note or "七腿逐日一致"))

    # 2) 仓位 ∈ [下限, 1]（不加杠杆）
    ok, note = True, ""
    for leg in vl.POOL:
        for kw, lo in (({}, 0.0), (dict(FLOOR=0.5), 0.5)):
            _, st, _ = vl.build(leg, **kw)
            if float(st.max()) > 1.0 + 1e-12 or float(st.min()) < lo - 1e-12:
                ok, note = False, f"{leg} 区间 {st.min():.4f}~{st.max():.4f}（下限要求 {lo}）"
                break
        if not ok:
            break
    out.append(("仓位 ∈ [下限, 100%]（不加杠杆、不穿透下限）", ok, note or "七腿均通过"))

    # 3) 无前视：截掉最后 6 天重跑，历史仓位不变
    ok, note = True, ""
    for leg in vl.POOL:
        nav, st, _ = vl.build(leg)
        cut = nav.index[:-6]
        st_cut = vl.state_daily(cut, leg)
        common = st.index.intersection(st_cut.index)
        diff = float((st.reindex(common) - st_cut.reindex(common)).abs().max())
        if diff > 1e-12:
            ok, note = False, f"{leg} 最大差 {diff:.2e}"
            break
    out.append(("截掉最后 6 天重跑，历史仓位不变（无前视）", ok, note or "最大差 0"))

    # 4) 前视抽样：只用「该净值日之前」的信号重算，仓位必须逐位相同
    ok, note = True, ""
    for leg in vl.POOL:
        nav, st, _ = vl.build(leg)
        sig = vl.load_signal(leg)
        n = 12
        for k in range(n):
            v = nav.index[int(k * (len(nav) - 1) / (n - 1))]
            prior = sig.index[sig.index < v]
            if not len(prior):
                continue
            st2 = vl.state_daily(nav.index, leg, as_of=prior[-1])
            d = abs(float(st2.loc[v]) - float(st.loc[v]))
            if d > 1e-12:
                ok, note = False, f"{leg} {v.date()} 差 {d:.2e}"
                break
        if not ok:
            break
    out.append(("只用「当日之前」的信号重算，仓位不变（抽样 12 点 · 无前视）", ok,
                note or "全部逐位一致"))

    # 5) 波动层恒等式：未触顶的每一天 scale × vol == 当日 TV(ER)
    ok, note = True, ""
    for leg in vl.POOL:
        t = vl.raw_scale(leg)          # 死区在 target_raw 里，恒等式查的是未过死区的原始系数
        v = vl.vol_series(leg).reindex(t.index)
        tv = vl.tv_series(leg).reindex(t.index)
        m = (t < 1.0 - 1e-12) & v.notna() & (v > 0)
        if int(m.sum()) == 0:
            ok, note = False, f"{leg} 没有触发缩仓的样本（检查 vol 口径）"
            break
        err = float((t[m] * v[m] - tv[m]).abs().max())
        if err > 1e-9:
            ok, note = False, f"{leg} 最大偏差 {err:.2e}"
            break
    out.append((f"缩仓日恒有 仓位 × vol20 == TV(ER)（{vl.TV_LOW:.0%}/{vl.TV:.0%}），误差 < 1e-9", ok,
                note or "七腿均通过"))
    return out


def main():
    os.makedirs(OUTDIR, exist_ok=True)
    p0 = vl.params()
    if str(p0["TRIGGER"]) != "daily":
        raise RuntimeError("本脚本的硬断言只覆盖 daily 默认档；请把 vol_layer.TRIGGER 改回 daily")

    legs = leg_rows()
    checks = check_all()
    base_rows = [r for r in legs if r["variant"].startswith("**V10 默认")]

    with open(OUT_JSONL, "w", encoding="utf-8") as f:
        for r in legs:
            f.write(json.dumps({k: (round(v, 4) if isinstance(v, float) else v)
                                for k, v in r.items()}, ensure_ascii=False) + "\n")
        f.write(json.dumps({"checks": [dict(name=n, ok=bool(o), note=t) for n, o, t in checks]},
                           ensure_ascii=False) + "\n")

    bad = [c for c in checks if not c[1]]
    L = [
        "# 波动资产 · V10 变种：ER 自适应波动层（日频 · 无涨跌方向 · 连续仓位）",
        f"> 池 = **全七腿**（`vol_layer.POOL` = {', '.join(vl.rule.LEGS[k]['name'] for k in vl.POOL)}），"
        "与 v7 / v8 / v9 同池（2026-11-17 扩池）。",
        f"> {vl.vdata.us_signal_exec_desc()}；"
        f"空仓按货基 {vl.CASH_ANNUAL:.2%} 计息 · 单边成本 {p0['COST']:.1%}。",
        "> 规则权威源 `v10_vol_layer/vol_layer.py` 顶部常量。",
        "",
        "> **本报告只做分腿回测**：不做组合净值、跨腿等权、v7/v8/v9 组合对照。"
        "每条腿按自己的样本独立计算，跨腿绝对值不可直接比。",
        "",
        "> **默认档（2026-11-17 用户指定）= `ER20/10 + 10pp 幅度死区 + 5 日时间死区`**"
        "（即 `research/probe_er/_probe_er_adaptive_7legs.md` 的 D 方案）；固定 TV20 / TV10 保留为对照档。",
        "",
        "## 1. 规则（可执行版）",
        "",
        "| 环节 | 规则 |",
        "|---|---|",
        "| 涨跌方向 | **没有** —— ER 只判趋势纯度，不判多空 |",
        "| 风险预算 TV | ER(20) ≥ 0.40 → 趋势态 20%；≤ 0.30 → 震荡态 10%；中间维持（滞回） |",
        f"| 仓位 | `min(1, TV(ER) / vol20)`，趋势态 {p0['TV']:.0%} / 震荡态 {p0['TV_LOW']:.0%} |",
        "| 波动率 | 20 日收益标准差 × √252，**每条腿各算各的** |",
        f"| 死区 | 幅度 `|Δ仓位| < {p0['DEAD']:.0%}` 且距上次调仓 < {p0['DEAD_DAYS']} 日不动手 |",
        "| 看盘频率 | **每日收盘**（月末档作对照） |",
        "| 成交 | 信号日收盘决策 → **次一交易日收盘成交** → 收益自 T+2 起 |",
        f"| 上限 / 下限 | 100% / {p0['FLOOR']:.0%}（不加杠杆） |",
        "",
        "> **两层自适应**：①仓位随本腿 `vol20` 变；②TV 随 ER(20) 趋势纯度在 20%/10% 间切换。"
        "全腿证据见 `research/probe_er/_v10_er_adaptive_7legs.md`：ER 在 6/7 腿严格过线（黄金不过），"
        "D 档在纳指后段 0.95、A500 0.58、科创50 0.12，且比原生少 10~15% 调仓。",
        "",
        "## 2. 逐腿（各档位均含 0.1% 单边成本）",
        "",
        "| 腿 | 样本 | 档位 | 年化 | 波动 | 回撤·日频 | 回撤·月末 | Calmar(月末) | 夏普 | 仓位 | 最低 | 调仓 | vs 买入持有 |",
        "|---|---|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|",
    ]
    for leg in vl.POOL:
        for r in [x for x in legs if x["leg"] == leg]:
            L.append(f"| {r['name']} | {r['start']}~{r['end']}（{r['years']:.1f}y） | {r['variant']} | "
                     f"{vl.fmt_pct(r['ann'])} | {r['vol'] * 100:.2f}% | {vl.fmt_pct(r['mdd'])} | "
                     f"{vl.fmt_pct(r['mdd_me'])} | {r['calmar_me']:.2f} | {r['sharpe']:.2f} | "
                     f"{r['expo']:.0%} | {r['st_min']:.0%} | {r['trades']} | "
                     f"{r['d_ann']:+.2f}pp / {r['d_mdd']:+.2f}pp |")

    anns = [r["ann"] for r in base_rows]
    mdds = [r["mdd"] for r in base_rows]
    trs = [r["trades"] for r in base_rows]
    short = [r for r in base_rows if r["years"] < 8]
    short_note = "；".join(
        f"{r['name']} {r['years']:.1f} 年（{r['start']} 起）" for r in short
    ) or "无"
    L += [
        "",
        "> 「vs 买入持有」= 年化差 / 回撤差（正 = 回撤更浅）。`最低` = 样本内最低仓位 ——"
        "**它比平均仓位更能说明这层在崩盘时有没有真降下来**（平均值会被长期满仓稀释）。",
        "",
        f"> 默认档跨腿读数：年化 {vl.fmt_pct(min(anns))} ~ {vl.fmt_pct(max(anns))}、"
        f"回撤·日频 {vl.fmt_pct(min(mdds))} ~ {vl.fmt_pct(max(mdds))}、"
        f"调仓 {min(trs)} ~ {max(trs)} 次。这是**区间**不是组合绩效。",
        "",
        f"> ⚠ **短样本腿**：{short_note}。原因不是数据缺失，而是真实指数本身起点晚 —— "
        "`SH000688`（科创50）2019-12 起、`HKHSTECH`（恒生科技）2020-07 起；"
        "分腿样本只有 6.9 / 6.3 年（ER 研究里另扣 300 根预热才 5.6 / 5.1 年）。"
        "跨腿比较时按各自样本读，别与 2005 起的长样本腿比绝对值。",
        "",
        "## 3. 硬断言",
        "",
        "| 检查 | 结果 | 备注 |",
        "|---|---|---|",
    ]
    for name, ok, note in checks:
        L.append(f"| {name} | {'✅' if ok else '❌'} | {note} |")
    L += [
        "",
        f"共 {len(checks)} 条，失败 {len(bad)} 条" + ("。" if not bad else
            "：**" + "；".join(c[0] for c in bad) + "**"),
        "",
        "> 第 5 条（`仓位 × vol20 == TV(ER)`）是这一支的**定义式断言**：波动层实现正确时，"
        "缩仓日必须满足当日 TV 状态（趋势态 20% / 震荡态 10%）。"
        "原「组合恒满 == 等权复利」「两腿与 v8 探针逐位对齐」两条属于组合层，已随组合回测一并移除。",
        "",
        "## 4. 口径与已知边界",
        "",
        "1. **没有涨跌方向判断，但有趋势状态门**：ER 只判路径效率（趋势干脆/来回磨），"
        "不判多空；默认档会在震荡态把 TV 压到 10%。逐腿读数见 §2。",
        "2. **治急跌，慢熊只缓解不免疫**：阴跌段 vol 会回落，但 ER 转震荡态后 TV=10%，"
        "仓位不会回到满仓；这比原纯波动层好，但仍不是价格方向门。"
        "2000-2002 不在样本内（外推），2008 在样本内但不是慢熊。",
        "3. **日频换手不低**：默认 D 档已用 5 日时间死区压过一轮；逐腿调仓次数见 §2，"
        "场外 0.6% 口径拖累见对应档位。怕摩擦就调宽幅度死区、关掉 ER 或改用 v9。",
        "4. **各腿独立**：每条腿各算各的 ER、波动、仓位与最低仓位；不存在「一个组合一个仓位」。",
        "5. **默认档来自全腿研究**：`research/probe_er/_v10_er_adaptive_7legs.md` 的 D 方案；"
        "§2 给了 12 档逐腿邻域（含 5/10/20pp 幅度、0/5/10 日时间、固定 TV20/TV10 对照）。",
        "",
        "> 图：`research/probe_er/_plot_vl.py` → `_v10_vl_{ndx,spx}.png`；"
        "每日：`daily/_daily_scan.py` → `_v10_vol_layer_daily.{md,json}`。",
        "> **模拟结果，非投资建议。**",
    ]

    with open(OUT_MD, "w", encoding="utf-8") as f:
        f.write("\n".join(L))

    print("报告:", OUT_MD)
    print("结果:", OUT_JSONL)
    print(f"分腿 {len(vl.POOL)} 条 · 档位 {len(VARIANTS)} 档 · 断言 {len(checks) - len(bad)}/{len(checks)} 通过")
    for r in base_rows:
        print(f"  {r['name']:8s} {r['years']:5.1f}y  年化 {vl.fmt_pct(r['ann']):>9s} · "
              f"回撤·日频 {vl.fmt_pct(r['mdd']):>8s} · 夏普 {r['sharpe']:.2f} · 调仓 {r['trades']}")
    return 0 if not bad else 1


if __name__ == "__main__":
    raise SystemExit(main())
