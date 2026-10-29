#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""V10 卖点专项分析：现行三条卖线是否合理，能不能优化

结论写在 `research/_sell_lab_report.md`，脚本只做三件事：

1. **把现行卖点摊开看**：每笔交易的实际退出原因、期内最大浮盈/浮亏、
   以及「如果当时不卖，涨回前高要多久、能多赚多少」—— 这是判断「卖早了」的唯一硬证据。
2. **数触发率**：三条卖线（收复前高 / 满 250 日 / 止损 15%）各自触发了几次。
   一条从不触发的规则等于没写，一条每次都触发的规则等于别的规则。
3. **横向试替代方案**：TTL 长短、站上年线离场、股息率回落到阈值离场、移动止盈、
   以及这些规则的组合。**同一段数据、同一套买点**，只改卖点。

口径
----
* 主口径 = 推送的**默认**（仓位 0–100% 分三批、到期复核续期、止损 10%）；一次性打满版在末节对照。
* 收益为全收益指数（含分红再投），扣 ETF 年费 0.6%（`etf_fee_year`），不含单边费差异。
* **只有 5 笔（000922）/ 9 笔（H30269）已平仓交易** —— 任何「最优参数」都是过拟合，
  本报告只认「两条指数方向一致」的结论。

用法::

    python strategies/dividend/v10_resonance/research/_sell_lab.py [--refresh]
"""
from __future__ import annotations

import argparse
import os
import sys

import numpy as np
import pandas as pd

_DIR = os.path.dirname(os.path.abspath(__file__))
_PKG = os.path.dirname(_DIR)                 # v10_resonance
_STRAT = os.path.dirname(_PKG)               # strategies/dividend
for _p in (_PKG, _STRAT):
    if _p not in sys.path:
        sys.path.insert(0, _p)

import backtest  # noqa: E402  pylint: disable=wrong-import-position
import engine  # noqa: E402  pylint: disable=wrong-import-position
import factors  # noqa: E402  pylint: disable=wrong-import-position
import rule  # noqa: E402  pylint: disable=wrong-import-position

REPORT = os.path.join(_DIR, "_sell_lab_report.md")
FULL = {}                                    # 默认口径（0–100% 分三批）：只改卖点，其余不动


def _p(x, d=1):
    return "—" if x is None or (isinstance(x, float) and not np.isfinite(x)) else f"{x * 100:.{d}f}%"


def _n(x, d=2):
    return "—" if x is None or (isinstance(x, float) and not np.isfinite(x)) else f"{x:.{d}f}"


# ------------------------------------------------------------------ 逐笔后验
def trade_forensics(panel: pd.DataFrame, res: dict) -> list[dict]:
    """给每笔已平仓交易补上「期内浮盈/浮亏 + 若不卖会怎样」。"""
    close = panel["close"].to_numpy(float)
    tr = panel["tr"].to_numpy(float)
    dy = panel["dy"].to_numpy(float)
    rows = []
    for t in res["trades"]:
        i0, i1 = int(panel.index.get_loc(t["entry_date"])), int(t["exit_i"])
        hold_tr = tr[i0:i1 + 1] / tr[i0] - 1
        hold_dy = dy[i0:i1 + 1]
        # 建仓后（到本轮样本末）何时才收复前高（price 口径，与卖点同尺子）
        target = t["peak"]
        after = np.where(close[i0 + 1:] >= target)[0]
        j = int(i0 + 1 + after[0]) if len(after) else None
        rows.append({
            "seq": len(rows) + 1, **t,
            "hi_tr": float(hold_tr.max()),           # 期内最大浮盈（全收益口径）
            "lo_tr": float(hold_tr.min()),
            "dy_lo": float(np.nanmin(hold_dy)) if np.isfinite(hold_dy).any() else float("nan"),
            "peak_days": None if j is None else j - i0,
            "peak_ret": None if j is None else float(tr[j] / tr[i0] - 1),
            "peak_date": None if j is None else panel.index[j],
            "end_ret": float(tr[-1] / tr[i0] - 1),
            "missed": None if j is None else float(tr[j] / tr[i0] - 1 - t["ret"]),
        })
    return rows


def fingerprint(res: dict) -> dict:
    """退出原因分布 + 触发率。"""
    out = {}
    for t in res["trades"]:
        out.setdefault(t["reason"], []).append(t["ret"])
    n = len(res["trades"])
    return {"n": n, "by_reason": out,
            "rate": {k: len(v) / n for k, v in out.items()} if n else {}}


# ------------------------------------------------------------------ 变体
def variants() -> dict[str, dict]:
    """只改卖点（买点/仓位/成本不变）。当前默认：满 250 日**到期复核续期** + 止损 10%。"""
    return {
        "现行：满250日到期复核 + 止损10%": {},
        "仅前高（不设期限）": dict(ttl=float("inf")),
        "满 250 日，不续期": dict(ttl_renew=False),
        "满 500 日，不续期": dict(ttl=500, ttl_renew=False),
        "满 750 日，不续期": dict(ttl=750, ttl_renew=False),
        "满 125 日，不续期": dict(ttl=125, ttl_renew=False),
        # 期限缩短扫描（含「到期复核」两版：短期限 + 续期 = 更早开始复核）
        "满 120 日，不续期": dict(ttl=120, ttl_renew=False),
        "满 90 日，不续期": dict(ttl=90, ttl_renew=False),
        "满 60 日，不续期": dict(ttl=60, ttl_renew=False),
        "满 120 日 + 到期复核": dict(ttl=120),
        "满 90 日 + 到期复核": dict(ttl=90),
        "满 60 日 + 到期复核": dict(ttl=60),
        "站上年线即走": dict(exit_ma=True),
        "股息率回落到 4.0%": dict(exit_dy=0.040),
        "股息率回落到 3.5%": dict(exit_dy=0.035),
        "移动止盈 8%": dict(trail=0.08),
        "移动止盈 10%": dict(trail=0.10),
        "移动止盈 15%": dict(trail=0.15),
        "止损放宽到 15%（原默认）": dict(stop=0.15),
        "止损放宽到 20%": dict(stop=0.20),
        "止损：不设": dict(stop=9.9),
        "止损 10% + 移动止盈 10%": dict(trail=0.10),
        # 到期续期上限：默认已续到 750，这里看更长会怎样
        "到期续期上限拉到 1250 日": dict(ttl_max=1250),
        # 条件失效即卖：卖点与买点对称 —— 买入要 3/3 共振，卖出看「还剩几条」
        "条件失效 剩<3 就卖（破1条走）": dict(exit_count=3),
        "条件失效 剩<2 才卖（破2条走）": dict(exit_count=2),
        "条件失效 剩<1 才卖（3条全破）": dict(exit_count=1),
        "条件失效 剩<3 + 不设期限": dict(exit_count=3, ttl=float("inf")),
        "条件失效 剩<2 + 不设期限": dict(exit_count=2, ttl=float("inf")),
    }


def evaluate(panel: pd.DataFrame, name: str, kw: dict) -> dict:
    res = engine.run(panel, **{**FULL, **kw})
    st = engine.stats(res["equity"])
    ts = backtest.trade_stats(res["trades"])
    fp = fingerprint(res)
    expo = float(res["expo"].mean())
    closed = [t for t in res["trades"] if t["exit_date"] is not None]
    # 每笔「年化」：把单笔收益按持有天数折算成年化 —— 衡量「占用资金的效率」，
    # 与「单笔赚多少」是两件事：持有 476 日赚 35% 不如 226 日赚 17%。
    # 用**几何平均**而非算术平均：算术平均会被一笔「短持有 + 大赚」拉爆（2014 那笔 125 日 +33%）。
    ta = [((1 + t["ret"]) ** (engine.TRADING_DAYS / max(t["days"], 1)) - 1) for t in closed]
    ta_gm = (float(np.prod([1 + x for x in ta])) ** (1 / len(ta)) - 1) if ta else float("nan")
    return {"name": name, "kw": kw, "res": res, "stat": st, "ts": ts, "fp": fp, "expo": expo,
            "ta": ta_gm,
            # 资金效率：组合年化 ÷ 平均仓位（≈ 每一块钱在场上时的年化）
            "capeff": st["cagr"] / expo if expo else float("nan")}


def ttl_scan(panel: pd.DataFrame, renew: bool = False,
             lo: int = 60, hi: int = 280, step: int = 10) -> pd.DataFrame:
    """期限扫描：逐个 ttl 跑一遍完整回测，返回 index=ttl（Calmar / 年化 / 笔数 / 均仓）。"""
    rows = {}
    for t in range(lo, hi + 1, step):
        res = engine.run(panel, ttl=t, ttl_renew=renew)
        st = engine.stats(res["equity"])
        rows[t] = {"calmar": st["calmar"], "cagr": st["cagr"], "dd": st["dd"],
                   "n": len(res["trades"]), "expo": float(res["expo"].mean())}
    return pd.DataFrame.from_dict(rows, orient="index")


# ------------------------------------------------------------------ 报告
def render(panels: dict, rows: dict, grids: dict, alt: dict, alt_s: dict) -> str:
    d = panels[rule.PRIMARY]
    f0 = rows[rule.PRIMARY]
    L = ["# V10 卖点专项分析：现行三条卖线是否合理，能不能优化", "",
         f"> 样本 **{d.index[0]:%Y-%m-%d} ~ {d.index[-1]:%Y-%m-%d}** · 主标的 "
         f"**{rule.PRIMARY} {rule.BY_INDEX[rule.PRIMARY]['name']}** · 主口径：默认（0–100% 分三批 + "
         f"到期复核 + 止损 {rule.STOP_LOSS:.0%}）",
         f"> 对照标的 **{rule.HOLDOUT} {rule.BY_INDEX[rule.HOLDOUT]['name']}**（9 笔）",
         "> 收益 = 全收益指数（含分红再投）、扣 ETF 年费 0.6%；买点、仓位、成本与回测完全一致，**只改卖点**", ""]

    # ---------- §1 逐笔真相
    L += ["## 一、现行卖点逐笔真相", "",
          f"{rule.PRIMARY} 已平仓 {len(f0)} 笔。下表第 6–8 列是关键：**期内最大浮盈**说明「卖之前浮盈到过多少」，"
          "**期内最大浮亏**说明「止损线离得有多远」，**到前高**说明「如果不止盈，什么时候能等到主卖点」。", "",
          "| # | 建仓 | 退出 | 原因 | 持有 | 实际收益 | 期内最高浮盈 | 期内最大浮亏 | 到前高 | 若到前高 | 少赚 |",
          "|---:|---|---|---|---:|---:|---:|---:|---:|---:|---:|"]
    for r in f0:
        pk = ("至今未收复" if r["peak_days"] is None
              else f"{r['peak_days']} 日（{r['peak_date']:%Y-%m}）")
        L.append(f"| {r['seq']} | {r['entry_date']:%Y-%m-%d} @{r['entry_price']:.0f} | "
                 f"{r['exit_date']:%Y-%m-%d} @{r['exit_price']:.0f} | {r['reason']} | "
                 f"{r['days']} | {_p(r['ret'])} | {_p(r['hi_tr'])} | {_p(r['lo_tr'])} | "
                 f"{pk} | {_p(r['peak_ret'])} | {_p(r['missed'])} |")

    fp0 = grids[rule.PRIMARY][0]["fp"]
    fpH = grids[rule.HOLDOUT][0]["fp"]
    n_stop = len(fp0["by_reason"].get("stop", []))
    miss = [r["missed"] for r in f0 if r["missed"] is not None]
    reached = [r for r in f0 if r["peak_days"] is not None]
    maxdd_n = min(r["lo_tr"] for r in f0)
    L += ["",
          f"- **三条卖线的真实分工（{rule.PRIMARY}）**：收复前高 **{len(fp0['by_reason'].get('peak', []))}** 笔、"
          f"满 {rule.SELL_TTL} 日 **{len(fp0['by_reason'].get('ttl', []))}** 笔、"
          f"止损 **{len(fp0['by_reason'].get('stop', []))}** 笔；对照标的 {rule.HOLDOUT} 是 "
          f"{len(fpH['by_reason'].get('peak', []))} / {len(fpH['by_reason'].get('ttl', []))} / "
          f"{len(fpH['by_reason'].get('stop', []))}。"
          f"**主标的上前高基本没参与（{len(fp0['by_reason'].get('peak', []))}/{fp0['n']}），"
          "实际退出由「满 250 日」决定** —— 在这只指数上，规则等价于「买入后持有一年，前高到了算提前止盈」；"
          f"对照标的正相反（{len(fpH['by_reason'].get('peak', []))}/{fpH['n']} 由前高卖出）。",
          f"- **尺度冲突（结构性）**：建仓时回撤已 ≥{rule.DD_BUY:.0%}，所以「收复前高」至少要涨 "
          f"{(1 / (1 - rule.DD_BUY) - 1) * 100:.0f}% 以上。{len(reached)}/{len(f0)} 笔**最终**都收复了前高，"
          f"但平均要 **{np.mean([r['peak_days'] for r in reached]):.0f} 个交易日**"
          f"（最快 {min(r['peak_days'] for r in reached)} 日、最慢 {max(r['peak_days'] for r in reached)} 日），"
          f"而期限只有 {rule.SELL_TTL} 日。**前高必然被期限截断，这不是参数没调好，是两条规则的时间常数不同。**",
          f"- **卖早的代价是实打实的**：实际每笔 {_p(np.mean([r['ret'] for r in f0]))}，"
          f"「涨回前高」能拿 {_p(np.mean([r['peak_ret'] for r in reached]))}，"
          f"平均少赚 **{_p(np.mean(miss))}**（单笔最大的少赚 {_p(max(miss))}）。",
          f"- **止损 {rule.STOP_LOSS:.0%} 本样本触发 {n_stop} 次**：期内最大浮亏最深 "
          f"**{_p(maxdd_n)}**。这套买点（深跌 + 高股息）几乎没买在半山腰，止损多数时候来不及响；"
          "**「没触发」不等于「该删」** —— 它是 2008 式单边下跌里唯一的兜底，"
          "而那种场景在本样本恰好被 4.5% 股息率挡在门外。", ""]

    # ---------- §2 家族对比
    L += ["## 二、替代卖点横向对比（同一买点，只改卖点）", "",
          "每一行都是一个完整回测。四个指标各管一件事，**单看任何一个都会误判**：", "",
          "- `Calmar` = 年化 / 最大回撤 —— 风险调整后收益，但**偏爱低仓位方案**（不投资就不会亏）；",
          "- `每笔均` = 单笔赚多少 —— 偏爱**长持有**（拿两年赚 35% 看着比拿一年赚 17% 好）；",
          "- `每笔年化` = 单笔收益按持有天数折算 —— 衡量**占用资金的效率**，才是同一笔钱能不能更快滚下一次；",
          "- `年化÷均仓` = 每一块钱在场时的年化 —— 剔除暴露差异后的组合效率。", "",
          "| 卖点方案 | Calmar | 年化 | 回撤 | 交易 | 胜率 | 每笔均 | 每笔年化 | 均持 | 均仓 | 年化÷均仓 |",
          "|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|"]
    for g in grids[rule.PRIMARY]:
        st, ts = g["stat"], g["ts"]
        star = " **←现行**" if g["name"].startswith("现行") else ""
        L.append(f"| {g['name']}{star} | {_n(st['calmar'])} | {_p(st['cagr'], 2)} | "
                 f"{_p(st['dd'])} | {ts['n']} | {ts['win_rate']:.0%} | {_p(ts['avg'])} | "
                 f"{_p(g['ta'])} | {ts['avg_days']:.0f} | {g['expo']:.0%} | {_p(g['capeff'])} |")

    GA = {g["name"]: g for g in grids[rule.PRIMARY]}
    GB = {g["name"]: g for g in grids[rule.HOLDOUT]}
    base_a, base_b = grids[rule.PRIMARY][0], grids[rule.HOLDOUT][0]
    L += ["", f"同一张表在对照标的 {rule.HOLDOUT} 上再跑一遍。**只有两个指数同向的改动才值得写进规则**：", "",
          "| 卖点方案 | 000922 Calmar / 每笔年化 | H30269 Calmar / 每笔年化 | Calmar 双升 | 每笔年化 双升 |",
          "|---|---:|---:|:--:|:--:|"]
    both_c, both_t = [], []
    for name in GA:
        a, b = GA[name], GB[name]
        ok_c = a["stat"]["calmar"] > base_a["stat"]["calmar"] and b["stat"]["calmar"] > base_b["stat"]["calmar"]
        ok_t = a["ta"] > base_a["ta"] and b["ta"] > base_b["ta"]
        if ok_c:
            both_c.append(name)
        if ok_t:
            both_t.append(name)
        L.append(f"| {name} | {_n(a['stat']['calmar'])} / {_p(a['ta'])} | "
                 f"{_n(b['stat']['calmar'])} / {_p(b['ta'])} | {'✅' if ok_c else '—'} | "
                 f"{'✅' if ok_t else '—'} |")
    L += ["", f"→ Calmar 双升：{('、'.join(both_c) or '**没有**')}；"
              f"每笔年化双升：{('、'.join(both_t) or '**没有**')}。", ""]

    # ---------- §2.5 条件失效即卖（卖点与买点对称）
    def _row(name, g):
        st, ts_ = g["stat"], g["ts"]
        return (f"| {name} | {_n(st['calmar'])} | {_p(st['cagr'], 2)} | {_p(st['dd'])} | {ts_['n']} | "
                f"{ts_['win_rate']:.0%} | {_p(ts_['avg'])} | {_p(g['ta'])} | {ts_['avg_days']:.0f} | "
                f"{g['expo']:.0%} | {_p(g['capeff'])} |")

    k3 = "条件失效 剩<3 就卖（破1条走）"
    k2 = "条件失效 剩<2 才卖（破2条走）"
    k1 = "条件失效 剩<1 才卖（3条全破）"
    c3, c2, c1 = GA[k3], GA[k2], GA[k1]
    h3, h2, h1 = GB[k3], GB[k2], GB[k1]
    dp = panels[rule.PRIMARY]

    def _cond_at(t):
        r = dp.loc[t["exit_date"]]
        return (bool(r["dd"] >= rule.DD_BUY), bool(r["below_ma"]), bool(r["dy"] >= rule.DY_BUY))

    c2t = [t for t in c2["res"]["trades"] if t["reason"] == "cond"]
    n_only_dy = sum(1 for t in c2t if _cond_at(t) == (False, False, True))
    n_dd_left = sum(1 for t in c2t if _cond_at(t)[0])
    same_ttl = (np.isclose(c3["stat"]["cagr"], GA["条件失效 剩<3 + 不设期限"]["stat"]["cagr"])
                and np.isclose(c2["stat"]["cagr"], GA["条件失效 剩<2 + 不设期限"]["stat"]["cagr"]))
    L += ["## 二·补、条件失效即卖：卖点与买点对称", "",
          "买点要求三条件 **3/3 共振**，很自然的一个想法是：卖点也用同一把尺子 —— "
          "**还剩几条就决定去留**。`engine.run(exit_count=k)` 的语义是「剩几条 < k 即卖」："
          "`3` = 破 1 条就走、`2` = 破 2 条才走、`1` = 三条全破才走。"
          "它排在**止损之后、收复前高之前**（止损是风控，「条件失效」是「买它的理由没了」）。", "",
          "| 卖点方案（默认口径） | Calmar | 年化 | 回撤 | 交易 | 胜率 | 每笔均 | 每笔年化 "
          "| 均持 | 均仓 | 年化÷均仓 |",
          "|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|",
          _row("现行：满250日到期复核 + 止损10%", base_a),
          _row("条件失效 剩<3（破 1 条就走）", c3),
          _row("条件失效 剩<2（破 2 条才走）", c2),
          _row("条件失效 剩<1（3 条全破）", c1), "",
          f"- **方向明确，且两指数同向**：Calmar {_n(base_a['stat']['calmar'])} → "
          f"剩<3 {_n(c3['stat']['calmar'])} / 剩<2 {_n(c2['stat']['calmar'])} / "
          f"剩<1 {_n(c1['stat']['calmar'])}（主标的）；对照标的 {_n(base_b['stat']['calmar'])} → "
          f"{_n(h3['stat']['calmar'])} / {_n(h2['stat']['calmar'])} / {_n(h1['stat']['calmar'])}。"
          "**剩<2 是唯一在两指数上都明显更好的档。**",
          f"- **但年化是降的**：{_p(base_a['stat']['cagr'], 2)} → {_p(c3['stat']['cagr'], 2)} / "
          f"{_p(c2['stat']['cagr'], 2)} / {_p(c1['stat']['cagr'], 2)}。原因不在单笔收益，在**在场时间**："
          f"平均仓位 {base_a['expo']:.0%} → {c3['expo']:.0%} / {c2['expo']:.0%} / {c1['expo']:.0%}。"
          "条件失效时价格已回到年线上方、回撤也修复了，**下一次共振要等一轮新的下跌**，"
          "于是大部分时间在拿现金（按 0 收益计）。",
          f"- **真正变好的是资金效率，不是择时能力**：每笔年化 {_p(base_a['ta'])} → {_p(c2['ta'])}、"
          f"年化÷均仓 {_p(base_a['capeff'])} → {_p(c2['capeff'])}。即**在场时赚得更快，但在场的时间少了一半**。"
          "这与 §3.4「站上年线就走」是同一个陷阱（Calmar 被低仓位抬高），只是没走到那么极端。",
          f"- **机制：它其实是「站上年线 + 回撤修复」的宽松版**。剩<2 的 {len(c2t)} 笔条件卖出里，"
          f"{n_only_dy} 笔的退出日是「收盘回到年线上方 **且** 回撤收窄到 {rule.DD_BUY:.0%} 以内、"
          f"只剩股息率一条还成立」；另有 {n_dd_left} 笔是回撤仍在 {rule.DD_BUY:.0%} 以上、"
          f"均线或息率先失效。平均持有 {c2['ts']['avg_days']:.0f} 日，现行是 "
          f"{base_a['ts']['avg_days']:.0f} 日。",
          f"- **剩<3 太敏感、剩<1 等于没改**：剩<3 平均只持 {c3['ts']['avg_days']:.0f} 日、"
          f"{c3['ts']['n']} 笔、单笔只剩 {_p(c3['ts']['avg'])}，已接近「一有反弹就走」的短线择时；"
          f"剩<1 要三条全破才卖，条件卖出很少发生，指标几乎回到现行"
          f"（Calmar {_n(c1['stat']['calmar'])} vs {_n(base_a['stat']['calmar'])}）。",
          ("- **它不能与「满 250 日」并存**：把 `ttl` 设成 ∞ 后，剩<3 / 剩<2 的指标**完全不变**"
           "（见 §二 表末几行），说明条件卖点几乎总比期限先到 —— 启用 `exit_count` 等于把期限卖点删掉。"
           if same_ttl else
           "- **它与期限卖点的关系**：把 `ttl` 设成 ∞ 后指标有变化 → 两条卖点会互相抢，需要分离考察。"),
          ""]

    # ---------- §3 结论
    cur, curH = base_a, base_b
    peak_only = GA["仅前高（不设期限）"]
    ttl5, ttl125 = GA["满 500 日，不续期"], GA["满 125 日，不续期"]
    ma, tr10, tr8 = GA["站上年线即走"], GA["移动止盈 10%"], GA["移动止盈 8%"]
    dy40, dy35 = GA["股息率回落到 4.0%"], GA["股息率回落到 3.5%"]
    norenew = GA["满 250 日，不续期"]
    pk_a = len(cur["fp"]["by_reason"].get("peak", []))
    pk_b = len(curH["fp"]["by_reason"].get("peak", []))
    stop15 = GA["止损放宽到 15%（原默认）"]
    stop15H = GB["止损放宽到 15%（原默认）"]
    norenewH = GB["满 250 日，不续期"]
    L += ["## 三、结论", "",
          "### 3.1 结构性毛病：主卖点和期限不在同一个时间尺度上", "",
          f"「收复前高」的目标距离由买入时的回撤决定（≥{rule.DD_BUY:.0%} → 至少涨 "
          f"{(1 / (1 - rule.DD_BUY) - 1) * 100:.0f}%），而到前高需要多少时间，**取决于指数修复得快不快**：",
          "",
          f"| 指数 | 主卖点退出占比 | 典型修复时间 |", "|---|---:|---|",
          f"| {rule.PRIMARY} 中证红利 | {pk_a}/{cur['ts']['n']}（{pk_a / cur['ts']['n']:.0%}） | "
          f"平均 {np.mean([r['peak_days'] for r in reached]):.0f} 日（最慢 "
          f"{max(r['peak_days'] for r in reached)} 日） |",
          f"| {rule.HOLDOUT} 中证红利低波 | {pk_b}/{curH['ts']['n']}（{pk_b / curH['ts']['n']:.0%}） | "
          f"多为 " + "/".join(str(int(x)) for x in sorted(
              t["days"] for t in GB["现行：满250日到期复核 + 止损10%"]["res"]["trades"]
              if t["reason"] == "peak")[:5]) + " 日（快） |",
          "",
          f"**同一个「收复前高」在两个指数上的角色完全不同**：中证红利是「深跌 + 慢修复」，"
          f"前高平均要等 {np.mean([r['peak_days'] for r in reached]):.0f} 个交易日、远超 "
          f"{rule.SELL_TTL} 日期限，于是「主卖点」形同虚设（{pk_a}/{cur['ts']['n']}）；"
          f"红利低波修复快，前高反而是主要退出方式（{pk_b}/{curH['ts']['n']}）。"
          "**这不是参数没调好，是「目标涨幅」和「持有期限」两个时间常数不匹配。**", "",
          "### 3.2 「卖早了」在主标的上成立，但换指数就不成立", "",
          f"| 指标 | {rule.PRIMARY} 现行 | {rule.PRIMARY} 只等前高 | 差 | {rule.HOLDOUT} 现行 → 只等前高 |",
          "|---|---:|---:|---:|---:|",
          f"| 每笔平均收益 | {_p(cur['ts']['avg'])} | {_p(peak_only['ts']['avg'])} | "
          f"{(peak_only['ts']['avg'] - cur['ts']['avg']) * 100:+.1f}pp | "
          f"{_p(curH['ts']['avg'])} → {_p(GB['仅前高（不设期限）']['ts']['avg'])} |",
          f"| 每笔年化（几何） | {_p(cur['ta'])} | {_p(peak_only['ta'])} | "
          f"{(peak_only['ta'] - cur['ta']) * 100:+.1f}pp | "
          f"{_p(curH['ta'])} → {_p(GB['仅前高（不设期限）']['ta'])} |",
          f"| Calmar | {_n(cur['stat']['calmar'])} | {_n(peak_only['stat']['calmar'])} | "
          f"{peak_only['stat']['calmar'] - cur['stat']['calmar']:+.2f} | "
          f"{_n(curH['stat']['calmar'])} → {_n(GB['仅前高（不设期限）']['stat']['calmar'])} |",
          f"| 平均持有（日） | {cur['ts']['avg_days']:.0f} | {peak_only['ts']['avg_days']:.0f} | "
          f"{peak_only['ts']['avg_days'] - cur['ts']['avg_days']:+.0f} | "
          f"{curH['ts']['avg_days']:.0f} → {GB['仅前高（不设期限）']['ts']['avg_days']:.0f} |",
          "",
          f"在中证红利上，「只等前高」把每笔从 {_p(cur['ts']['avg'])} 提到 "
          f"{_p(peak_only['ts']['avg'])}、每笔年化 {_p(cur['ta'])} → {_p(peak_only['ta'])}，"
          f"但 Calmar {_n(cur['stat']['calmar'])} → {_n(peak_only['stat']['calmar'])}、"
          f"平均持有 {cur['ts']['avg_days']:.0f} → {peak_only['ts']['avg_days']:.0f} 日"
          f"（仓位 {cur['expo']:.0%} → {peak_only['expo']:.0%}，同一段时间只能做一轮）—— "
          "**单笔收益变多，风险调整后不一定更好。**"
          f"但在红利低波上，同一改动让 Calmar 从 {_n(curH['stat']['calmar'])} 掉到 "
          f"{_n(GB['仅前高（不设期限）']['stat']['calmar'])} —— 因为它有一笔拿了 "
          f"{max(t['days'] for t in GB['仅前高（不设期限）']['res']['trades'])} 个交易日，"
          "期间的回撤暴露把 Calmar 拉下去了。",
          "",
          "**所以「卖太早」这个判断不能一刀切**：它成立于「跌得深、修复慢」的标的（中证红利），"
          "不成立于「修复快」的标的（红利低波，一年内前高就摸到了，期限根本没约束住它）。"
          "现行 250 日期限的真正问题是：**它对慢修复标的等于强制早卖，对快修复标的基本无感。**", "",
          "### 3.3 已转正的两处改动（本轮落地为默认）", "",
          f"- **① 止损 15% → {rule.STOP_LOSS:.0%}**：主标的 Calmar {_n(stop15['stat']['calmar'])}"
          f"（15%）→ {_n(cur['stat']['calmar'])}（{rule.STOP_LOSS:.0%}）、对照标的 "
          f"{_n(stop15H['stat']['calmar'])} → {_n(curH['stat']['calmar'])}；年化 "
          f"{_p(stop15['stat']['cagr'], 2)} → {_p(cur['stat']['cagr'], 2)}、"
          f"{_p(stop15H['stat']['cagr'], 2)} → {_p(curH['stat']['cagr'], 2)}。"
          "机制清楚：2019 那笔买在下跌中继（期内最深浮亏 "
          f"{_p(maxdd_n)} / 对照 {_p(min(r['lo_tr'] for r in rows[rule.HOLDOUT]))}），"
          f"15% 线够不着，{rule.STOP_LOSS:.0%} 线把它换出来，随后又以更低价重新建仓吃到反弹。"
          f"代价：主标的胜率变成 {cur['ts']['win_rate']:.0%}（真实地认亏一次）。"
          "**⚠ 对照标的其实是持平（不是同向改善），且这条改善来自同一次 2019–2020 磨底，"
          "只能算 1 个独立样本** —— 所以它的定位是「免赔额定准 + 风险管理」，"
          "不是「经过检验的更优参数」。",
          f"- **② 满 {rule.SELL_TTL} 日 → 到期复核续期**：把「无条件卖出」改成"
          f"「到期检查：三条件仍共振则续一年，最多 {rule.SELL_TTL_MAX} 日」。主标的**结果基本不变**"
          f"（{_n(norenew['stat']['calmar'])} / {_p(norenew['stat']['cagr'], 2)} → "
          f"{_n(cur['stat']['calmar'])} / {_p(cur['stat']['cagr'], 2)}，因为一年后回撤基本已收窄到 "
          f"{rule.DD_BUY:.0%} 以内、条件不再成立），说明它不制造额外交易；对照标的上 Calmar "
          f"{_n(norenewH['stat']['calmar'])} → {_n(curH['stat']['calmar'])}、年化 "
          f"{_p(norenewH['stat']['cagr'], 2)} → {_p(curH['stat']['cagr'], 2)} —— "
          "「到期时仍然便宜」的仓位被留住了。**这是唯一「不会变差、只会变好」的改动。**",
          f"- **③ 缩短期限（120 / 90 / 60 日，未采纳）**：Calmar "
          f"{_n(cur['stat']['calmar'])} / {_n(curH['stat']['calmar'])}（现行）→ "
          f"{_n(ttl125['stat']['calmar'])} / {_n(GB['满 125 日，不续期']['stat']['calmar'])}（125 日），"
          f"而且这个维度对 5 天都敏感 —— 完整扫描见 **§3.7**。", "",
          "### 3.4 不建议的几种（看着聪明，实测站不住）", "",
          f"- **站上年线就走**：Calmar {_n(ma['stat']['calmar'])}（现行 {_n(cur['stat']['calmar'])}），"
          f"但每笔只赚 {_p(ma['ts']['avg'])}、平均持有 {ma['ts']['avg_days']:.0f} 日、平均仓位只有 "
          f"{ma['expo']:.0%} —— **Calmar 是被低仓位抬起来的**，组合年化只有 "
          f"{_p(ma['stat']['cagr'], 2)}（现行 {_p(cur['stat']['cagr'], 2)}）。"
          "这是「被 Calmar 骗」的教科书案例：分母（回撤）被仓位压小了。",
          f"- **移动止盈**：{rule.PRIMARY} 上明显更差（8%：Calmar {_n(tr8['stat']['calmar'])}、"
          f"每笔 {_p(tr8['ts']['avg'])}；10%：{_n(tr10['stat']['calmar'])}、{_p(tr10['ts']['avg'])}），"
          f"而 {rule.HOLDOUT} 上更好（Calmar {_n(GB['移动止盈 8%']['stat']['calmar'])}）—— "
          "**两指数打架**。原因是「回撤止盈」在震荡市里不断被小回撤触发，"
          "而红利指数的反弹本身就是锯齿状的。",
          f"- **股息率回落到阈值就走**：3.5% 版与现行**完全相同**（{_n(dy35['stat']['calmar'])} / "
          f"{_p(dy35['ts']['avg'])}），因为**持仓期内**反推股息率最低只到 "
          f"{_p(min(r['dy_lo'] for r in f0))}、从未跌破 3.5% —— **这条规则等于没写**；"
          f"把阈值提到 4.0% 才真的触发，但 Calmar 掉到 {_n(dy40['stat']['calmar'])}。"
          "想让「不便宜了就走」生效，得先把阈值定在「实际会穿过的位置」，而那正是最容易被过拟合的地方。",
          f"- **放宽期限到 500 日**：主标的变好（Calmar {_n(cur['stat']['calmar'])} → "
          f"{_n(ttl5['stat']['calmar'])}），对照标的变差（{_n(curH['stat']['calmar'])} → "
          f"{_n(GB['满 500 日，不续期']['stat']['calmar'])}）。跨指数不一致，只能算偏好。", "",
          "### 3.5 三种自洽的写法（现行 = A）", "",
          "| 写法 | 卖点 | 主标的实测 | 适合谁 |", "|---|---|---|---|",
          f"| **A · 期限止盈（现行默认）** | 前高算提前止盈；满 {rule.SELL_TTL} 日**检查一次**："
          f"仍共振则续一年，否则走 | 每笔 {_p(cur['ts']['avg'])}、每笔年化 {_p(cur['ta'])}、"
          f"Calmar {_n(cur['stat']['calmar'])} | 把这块当「机会资金」、要资金周转 |",
          f"| B · 目标止盈 | 只等前高（+止损），不设期限 | 每笔 {_p(peak_only['ts']['avg'])}、"
          f"持 {peak_only['ts']['avg_days']:.0f} 日、每笔年化 {_p(peak_only['ta'])}、"
          f"Calmar {_n(peak_only['stat']['calmar'])} | 能接受「一套两三年」的长期资金 |",
          f"| C · 高周转 | 满 125 日走 | 每笔 {_p(ttl125['ts']['avg'])}、每笔年化 "
          f"{_p(ttl125['ta'])}、笔数 {ttl125['ts']['n']} 笔 | 想在同一轮磨底里反复吃反弹 |",
          "",
          f"**默认已经是 A + 止损 {rule.STOP_LOSS:.0%}**（本轮转正），三项指标：Calmar "
          f"{_n(cur['stat']['calmar'])} / 每笔 {_p(cur['ts']['avg'])} / 每笔年化 {_p(cur['ta'])}。"
          "剩下的可选项只有 B、C 两种风格，以及 §二·补 的「条件失效即卖」—— **三者互斥，别混着用**。", "",
          "**但要说清楚**：这套规则真正的杠杆不在卖点，而在「什么时候在场」。"
          f"默认（分三批）平均仓位 {cur['expo']:.0%}、年化 {_p(cur['stat']['cagr'], 2)}；"
          f"一次性打满均仓 {alt['expo']:.0%}、年化 {_p(alt['stat']['cagr'], 2)}。"
          "**同一批交易、每笔收益一样** —— 差别全在「钱什么时候投进去」。", "",
          "### 3.6 为什么这里给不出「最优参数」", "",
          f"已平仓交易只有 {cur['ts']['n']} 笔（主标的）+ {curH['ts']['n']} 笔（对照），"
          f"上表里输赢的幅度（Calmar {_n(cur['stat']['calmar'])} vs "
          f"{_n(peak_only['stat']['calmar'])}）小于单笔交易的偶然性。"
          "再叠加两个具体的坑：①「止损收紧」的改善其实来自同一次 2019–2020 磨底；"
          "②「站上年线」这类改动主要改变的是仓位暴露，不是择时能力。"
          "提高可信度的顺序：① 扩样本（更多有全收益序列的红利指数 / 更长历史）；"
          "② 滚动起点检验（换建仓年份结论是否一致）；③ 模拟盘。"
          "在此之前，卖点只适合做「止损免赔额定准」和「到期复核」这种低风险微调。", ""]

    # ---------- §3.7 期限扫描
    _bt = pd.Timestamp(rule.BT_START)
    scA = ttl_scan(dp.loc[lambda x: x.index >= _bt])
    scB = ttl_scan(panels[rule.HOLDOUT].loc[lambda x: x.index >= _bt])
    corr = float(np.corrcoef(scA["calmar"], scB["calmar"])[0, 1])
    bestA, bestB = int(scA["calmar"].idxmax()), int(scB["calmar"].idxmax())
    jumpA = float(scA["calmar"].diff().abs().max())
    L += ["### 3.7 把「满 250 日」缩短到 120 / 90 / 60 会更好吗（专项）", "",
          f"买点、仓位、止损、续期全部固定，只把期限从 60 到 280 个交易日按 10 日步长扫一遍"
          f"（{len(scA)} 个完整回测），看 Calmar 曲面长什么样：", "",
          "| 期限（不续期） | 000922 Calmar | 年化 | 笔数 | H30269 Calmar | H30269 年化 |",
          "|---|---:|---:|---:|---:|---:|"]
    for k in ("满 60 日，不续期", "满 90 日，不续期", "满 120 日，不续期", "满 250 日，不续期"):
        a, bb = GA[k], GB[k]
        L.append(f"| {k.replace('，不续期', '')} | {_n(a['stat']['calmar'])} | {_p(a['stat']['cagr'], 2)} | "
                 f"{a['ts']['n']} | {_n(bb['stat']['calmar'])} | {_p(bb['stat']['cagr'], 2)} |")
    L += ["",
          f"- **曲面是锯齿，不是山峰**：{rule.PRIMARY} 的 Calmar 在 "
          f"{_n(scA['calmar'].min())}–{_n(scA['calmar'].max())} 之间乱跳（均值 {_n(scA['calmar'].mean())}、"
          f"标准差 {_n(scA['calmar'].std())}），相邻 10 个交易日的最大跳幅就有 **{_n(jumpA)}** —— "
          "比任何「档位之间」的差距都大。**换 5 个交易日就能让结论翻转**，说明这个维度上测到的不是能力，"
          "而是「卖点正好落在哪根 K 线」。",
          f"- **两条指数的最优解互相矛盾**：{rule.PRIMARY} 最优 {bestA} 日、{rule.HOLDOUT} 最优 {bestB} 日，"
          f"两条 Calmar 曲线相关系数 **{corr:+.2f}**（≈0，甚至微负）。真存在「更好的期限」时，"
          "两个指数应该指向同一区间 —— 它们没有。",
          f"- **缩短的机制代价是确定的**：笔数 {cur['ts']['n']} → "
          f"{GA['满 90 日，不续期']['ts']['n']}（90 日）/ {GA['满 60 日，不续期']['ts']['n']}（60 日），"
          f"每笔均 {_p(cur['ts']['avg'])} → {_p(GA['满 90 日，不续期']['ts']['avg'])} / "
          f"{_p(GA['满 60 日，不续期']['ts']['avg'])}，平均仓位 {cur['expo']:.0%} → "
          f"{GA['满 90 日，不续期']['expo']:.0%} / {GA['满 60 日，不续期']['expo']:.0%} —— "
          "**把一轮行情切碎成几段，每段都要重新等一次共振**，而共振一年只出现个位数天数。",
          f"- **唯一「双指数 Calmar 同升」的缩短方案是 60 日 + 到期复核**（"
          f"{_n(GA['满 60 日 + 到期复核']['stat']['calmar'])} / "
          f"{_n(GB['满 60 日 + 到期复核']['stat']['calmar'])}，现行 {_n(cur['stat']['calmar'])} / "
          f"{_n(curH['stat']['calmar'])}），但年化 {_p(cur['stat']['cagr'], 2)} → "
          f"{_p(GA['满 60 日 + 到期复核']['stat']['cagr'], 2)}、笔数 {cur['ts']['n']} → "
          f"{GA['满 60 日 + 到期复核']['ts']['n']}。**仍是「少在场 + 切碎」换 Calmar，不是收益改善。**",
          "",
          "**结论：不改。** 250 日的角色是**资金周转上限**，不是收益优化参数 —— 它确实截断了慢修复标的的"
          "收益（§3.1），但也保证资金不会一套三年。想要周转，用「到期复核」（已默认）比缩短期限更有依据："
          "前者只在「条件失效」时卖，后者是无条件到点就走。", ""]

    # ---------- §4 一次性口径
    b = alt
    L += ["## 四、一次性打满口径下这些结论变不变", "",
          f"默认是**分三批**（40%→70%→100%）：Calmar **{_n(cur['stat']['calmar'])}**、年化 "
          f"{_p(cur['stat']['cagr'], 2)}、回撤 {_p(cur['stat']['dd'])}、平均仓位 {cur['expo']:.0%}、"
          f"每笔均 {_p(cur['ts']['avg'])}。换成**一次性打满**：Calmar {_n(b['stat']['calmar'])}、"
          f"年化 {_p(b['stat']['cagr'], 2)}、回撤 {_p(b['stat']['dd'])}、平均仓位 {b['expo']:.0%}。"
          "换的只是「钱什么时候投进去」，每笔交易的收益完全相同，所以上面对卖点的结论不需要重做。", "",
          "条件失效卖点在一次性口径下的实测（确认它不是「分批」造出来的）：", "",
          "| 卖点方案 | Calmar | 年化 | 回撤 | 交易 | 平均仓位 |", "|---|---:|---:|---:|---:|---:|"]
    for nm, g in [("默认（三批）", cur), *alt_s.items()]:
        L.append(f"| {nm} | {_n(g['stat']['calmar'])} | {_p(g['stat']['cagr'], 2)} | "
                 f"{_p(g['stat']['dd'])} | {g['ts']['n']} | {g['expo']:.1%} |")
    s3 = alt_s["条件失效 剩<3 就卖（破1条走）"]
    s2 = alt_s["条件失效 剩<2 才卖（破2条走）"]
    L += ["",
          f"- 与默认口径**同向**：Calmar {_n(b['stat']['calmar'])} → 剩<3 {_n(s3['stat']['calmar'])} / "
          f"剩<2 {_n(s2['stat']['calmar'])}，年化 {_p(b['stat']['cagr'], 2)} → "
          f"{_p(s3['stat']['cagr'], 2)} / {_p(s2['stat']['cagr'], 2)}。",
          f"- **同一个机制**：平均仓位 {b['expo']:.1%} 被压到 {s3['expo']:.1%} / {s2['expo']:.1%}，"
          "Calmar 变好、绝对收益变低，都是「少在场」换来的，不是择时更准。", ""]
    return "\n".join(L)


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="V10 卖点专项分析")
    ap.add_argument("--refresh", action="store_true")
    args = ap.parse_args(argv)

    panels = factors.build(refresh=args.refresh)
    rows, grids = {}, {}
    for code in (rule.PRIMARY, rule.HOLDOUT):
        if code not in panels:
            continue
        d = panels[code].loc[lambda x: x.index >= pd.Timestamp(rule.BT_START)]
        rows[code] = trade_forensics(d, engine.run(d, **FULL))
        grids[code] = [evaluate(d, name, kw) for name, kw in variants().items()]

    d30 = panels[rule.PRIMARY].loc[lambda x: x.index >= pd.Timestamp(rule.BT_START)]
    alt = evaluate(d30, "一次性打满", dict(ladder=False))
    alt_s = {nm: evaluate(d30, nm, dict(ladder=False, exit_count=kw["exit_count"]))
             for nm, kw in variants().items() if nm.startswith("条件失效 剩<")
             and "期限" not in nm}

    with open(REPORT, "w", encoding="utf-8") as fh:
        fh.write(render(panels, rows, grids, alt, alt_s))

    cur = grids[rule.PRIMARY][0]
    print(f"[sell-lab] 现行：{cur['ts']['n']} 笔 · 每笔均 {cur['ts']['avg'] * 100:+.1f}% · "
          f"Calmar {cur['stat']['calmar']:.2f} · 退出原因 "
          f"{ {k: len(v) for k, v in cur['fp']['by_reason'].items()} }")
    best = sorted(grids[rule.PRIMARY], key=lambda g: -g["stat"]["calmar"])[:3]
    print("[sell-lab] Calmar 前三：" + " · ".join(
        f"{g['name']}={g['stat']['calmar']:.2f}" for g in best))
    print(f"[sell-lab] 报告 -> {os.path.relpath(REPORT, rule.ROOT)}")
    return 0


if __name__ == "__main__":
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    sys.exit(main())
