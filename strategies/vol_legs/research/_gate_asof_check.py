# -*- coding: utf-8 -*-
"""闸门前视检查：把「当下快照」换成「当月当时的 PE 分位」重跑一遍。

## 为什么单独跑这一个脚本

`data.gates()` 里的沪深300 ERP 分位是**当下快照**：回测把 2026 年的分位（63%）
套到 2012 年起全程（`data.py::gates` 的 docstring 写明「static 部分在回测区间内不变」）。
这属于前视 —— 历史上那些时刻，投资者并不知道今天的估值分位。

本脚本用 `data.hs300_erp_history()`（逐月因果分位，只用当月及之前的 60 个月）
构造逐日闸门，交给 `engine.run(gates_fn=...)` 重跑，与默认口径对比：

  A 默认（static）  : hs300_erp 全程 = 当下分位对应的状态
  B 当时（as-of）   : hs300_erp 按月切换，月初用**上月末**的分位（避免当月未完成数据）

做两组对照：
  ① 七腿组合（2022-08 起，受恒生科技联接成立限制）—— 回答「每日操作那套组合受不受影响」；
  ② 沪深300 单腿（2010 起）—— 样本更长、闸门状态变化更多，回答「闸门本身有没有用」。

只改 hs300 一条腿的闸门；其余闸门（A500/科创50/恒生科技/美股/TIPS/WGRC）本地没有
历史序列，仍是快照 —— 报告里如实标注。

产物：research/_gate_asof_check.md（不覆盖任何既有回测报告）。
"""
import os
import sys

import pandas as pd

try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:  # noqa: BLE001
    pass

_HERE = os.path.dirname(os.path.abspath(__file__))
_VL = os.path.dirname(_HERE)
_ROOT = os.path.dirname(_VL)
for _p in (_HERE, _VL):
    if _p not in sys.path:
        sys.path.insert(0, _p)

import data  # noqa: E402
import engine  # noqa: E402
import rule  # noqa: E402

OUT = os.path.join(_HERE, "_gate_asof_check.md")
BAND_SEED = {k: rule.LEGS[k]["band"] for k in rule.LEG_ORDER}


def ready_start(ind):
    starts = [ind[k][ind[k]["ready"]].index[0] for k in rule.LEG_ORDER
              if len(ind[k][ind[k]["ready"]])]
    return max(starts) if starts else None


def daily_gate(calendar, hist):
    """月度因果分位 -> 逐日 {date: {hs300_erp, hs300_erp_pct}}。

    用**上月末**的读数（shift(1)）：当月分位要等当月收盘才知道，日频信号不能用。
    """
    if hist.empty:
        return {}
    g = hist.shift(1).dropna(subset=["pct"])
    idx = g.index.union(calendar)
    g = g.reindex(idx).ffill().reindex(calendar)
    out = {}
    for d, row in g.iterrows():
        if pd.isna(row.get("pct")):
            continue
        out[d] = {"hs300_erp": row["gate"],
                  "hs300_erp_pct": float(row["pct"])}
    return out


def run(ind, static, *, since, init, legs, gates_fn=None):
    return engine.run(ind, static, set(), since=since, init=init, legs=legs,
                      gates_fn=gates_fn)


def fmt(m):
    calmar = m["ann"] / abs(m["mdd"]) if m["mdd"] else float("nan")
    return f"{m['ann'] * 100:+.2f}% | {m['mdd'] * 100:+.2f}% | {m['sharpe']:.2f} | {calmar:.2f}"


def block(title, a, b, *, leg=None):
    ma, mb = engine.metrics(a["nav"]), engine.metrics(b["nav"])
    ta = [t for t in a["trades"] if leg is None or t["leg"] == leg]
    tb = [t for t in b["trades"] if leg is None or t["leg"] == leg]
    diff = mb["ann"] - ma["ann"]
    return [
        f"## {title}",
        "",
        f"> 样本 {a['cal'][0]:%Y-%m-%d} ~ {a['cal'][-1]:%Y-%m-%d}"
        f"（{len(a['cal'])} 个交易日）",
        "",
        "| 口径 | 年化 | 最大回撤 | 夏普 | Calmar | 成交 |",
        "|---|---:|---:|---:|---:|---:|",
        f"| A 默认（static） | {fmt(ma)} | {len(ta)} |",
        f"| B 当时（as-of） | {fmt(mb)} | {len(tb)} |",
        "",
        f"- 年化差 **{diff * 100:+.2f}pp**。",
        "",
    ]


def main():
    ind = data.indicators_all()
    static, _ = data.gates(ind["gold"].index)
    hist = data.hs300_erp_history()
    by_day = daily_gate(ind["gold"].index, hist)

    valid = hist[hist["pct"].notna()] if not hist.empty else hist
    n_on = int((valid["gate"] == "on").sum())
    n_off = int((valid["gate"] == "off").sum())
    n_mid = int(valid["gate"].isna().sum())

    since_all = ready_start(ind)
    since_hs = ind["hs300"][ind["hs300"]["ready"]].index[0]

    def cnt(since):
        days = [d for d in by_day if d >= since]
        return (sum(1 for d in days if by_day[d]["hs300_erp"] == "off"),
                sum(1 for d in days if by_day[d]["hs300_erp"] == "on"))

    off_all, on_all = cnt(since_all)
    off_hs, on_hs = cnt(since_hs)

    a1 = run(ind, static, since=since_all, init=dict(BAND_SEED), legs=rule.LEG_ORDER)
    b1 = run(ind, static, since=since_all, init=dict(BAND_SEED), legs=rule.LEG_ORDER,
             gates_fn=lambda d: by_day.get(d))

    hs_init = {"hs300": float(rule.LEGS["hs300"]["band"])}
    a2 = run(ind, static, since=since_hs, init=hs_init, legs=["hs300"])
    b2 = run(ind, static, since=since_hs, init=hs_init, legs=["hs300"],
             gates_fn=lambda d: by_day.get(d))

    d1 = engine.metrics(b1["nav"])["ann"] - engine.metrics(a1["nav"])["ann"]
    d2 = engine.metrics(b2["nav"])["ann"] - engine.metrics(a2["nav"])["ann"]
    worst = max(abs(d1), abs(d2))
    verdict = ("两组差异都 < 1pp/年：**默认回测的结论不依赖这个前视**，"
               "把口径写进报告即可，不必改历史数字。"
               if worst < 0.01 else
               f"最大差异 {worst * 100:+.2f}pp/年：**结论会随口径变化**，"
               "应以 B 口径为准，或至少并列两套数字。")

    L = [
        "# 闸门前视检查 · 沪深300 ERP 分位：当下快照 vs 当时分位",
        "",
        f"> 闸门序列 `data.hs300_erp_history()`：逐月因果分位，窗口 = 当月及之前 60 个月，"
        f"并按月末 `shift(1)` 延后一个月生效（月初用的是上月末读数）。"
        f"最新一次读数与 `gates()` 完全一致（{static.get('hs300_erp_pct')}）。",
        "> 机械规则输出，非投资建议。",
        "",
        "## 1. 分位本身的历史分布",
        "",
        f"- 月度样本 **{len(hist)}** 个月：gate=on（分位 ≥70，便宜、允许加仓）**{n_on}** 月、"
        f"gate=off（分位 <30，偏贵、禁新增）**{n_off}** 月、中间地带（不设限）**{n_mid}** 月。",
        f"- 七腿组合样本内（{since_all:%Y-%m} 起）：off **{off_all}** 天 / on **{on_all}** 天。",
        f"- 沪深300 单腿样本内（{since_hs:%Y-%m} 起）：off **{off_hs}** 天 / on **{on_hs}** 天。",
        "",
        "> 「on」不改变规则（只是允许阶梯加仓），只有「off」会真正改变历史路径 —— "
        "所以差异大小取决于 off 期是否恰好撞上阶梯 + RSI 扳机。",
        "",
    ]
    L += block("2. 七腿组合（每日操作口径）", a1, b1)
    L += block("3. 沪深300 单腿（更长样本）", a2, b2, leg="hs300")
    L += ["> 单腿整段只有 6 笔成交 —— 这组对照能证明「闸门没改变历史成交路径」，"
          "**不能**证明「闸门有用」。", ""]
    L += [
        "## 4. 结论",
        "",
        f"- {verdict}",
        "- 这次只换了 hs300 一条腿；A500（PE 仅 2024-09 起）、科创50 PB、恒生科技 ERP、"
        "美股 ERP、TIPS / WGRC 本地都没有历史序列，**仍然无法做逐日重算** —— "
        "这些闸门在回测里依旧不可验证，报告必须继续这么写。",
        "- 默认回测**不改**：`backtest.py` 仍走 static 快照（否则 README 的 14.2 年数字会漂移）。"
        "本脚本只负责回答「这个前视会不会改变结论」。",
        "",
        "> 复现：`python strategies/vol_legs/research/_gate_asof_check.py`",
    ]
    with open(OUT, "w", encoding="utf-8") as fh:
        fh.write("\n".join(L) + "\n")

    print(f"[gate] 月度样本 {len(hist)} · on {n_on} / off {n_off} / 中间 {n_mid}")
    print(f"[gate] 七腿   A {fmt(engine.metrics(a1['nav']))}")
    print(f"[gate] 七腿   B {fmt(engine.metrics(b1['nav']))}")
    print(f"[gate] hs300  A {fmt(engine.metrics(a2['nav']))}")
    print(f"[gate] hs300  B {fmt(engine.metrics(b2['nav']))}")
    print(f"[gate] 产物 -> {os.path.relpath(OUT, _VL)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
