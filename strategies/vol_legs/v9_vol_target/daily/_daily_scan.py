# -*- coding: utf-8 -*-
"""波动资产 · V9 变种（美股两腿波动率目标仓位）· 每日操作。

流程：
1. **先刷新数据**：场外 QDII 净值（`data.refresh_otc`，仅这两条腿）+ 美股指数
   （`data.refresh_us`，`.NDX` / `.INX`）；加 `--no-refresh` 用本地缓存，报告会标「未刷新」；
2. 对两条腿各算五个数：**vol20**（近 20 日年化已实现波动）、**按公式该持多少**
   （`min(1, 15%/vol20)`）、**当前仓位**（上一次月末决策的执行结果）、
   **两者差额**（是否达到 5pp 的死区门槛）、下一次决策日；
3. 输出：今日是否有动作（**只有月末最后一个交易日会有**）、月末预览、逐腿读数、最近 8 次操作流水。

**关键时点**：常规信号只在**每月最后一个交易日收盘**产生（默认 `TRIGGER="fast"` 另加月中两种触发：
快减 = 5 日跌 ≤ −5% 且 vol20 ≥ 1.4×TV；快补 = vol20 ≤ 60 日峰值 70% 且站上 MA10）。
均为**次一交易日收盘净值**成交，新权重自 **t+2** 起承担收益（文档 R-01）。
不符合上述时点的日子本脚本只会说「维持」——不是脚本坏了。

产物（相对本文件）：daily/_v9_vol_target_daily.md / _v9_vol_target_daily.json。
机械规则输出，非投资建议。
"""
import json
import os
import sys

import pandas as pd

try:
    sys.stdout.reconfigure(encoding="utf-8")  # type: ignore[attr-defined]
except Exception:  # noqa: BLE001
    pass

_HERE = os.path.dirname(os.path.abspath(__file__))
_V9 = os.path.dirname(_HERE)
sys.path.insert(0, _V9)

import vol_target as vt  # noqa: E402

OUT_MD = os.path.join(_HERE, "_v9_vol_target_daily.md")
OUT_JSON = os.path.join(_HERE, "_v9_vol_target_daily.json")
RECENT = 8


def next_rebal(last):
    """下一个决策日 = 当月最后一个交易日；若已过则下月末。返回 (日期, 估算交易日数)。"""
    last = pd.Timestamp(last).normalize()
    cal_end = (last + pd.offsets.MonthEnd(0)).normalize()
    if last >= cal_end:
        cal_end = (last + pd.offsets.MonthEnd(1)).normalize()
    last_td = cal_end
    while last_td.month != cal_end.month or last_td > cal_end:
        last_td -= pd.offsets.BDay(1)
    gap = int(len(pd.bdate_range(last + pd.offsets.BDay(1), last_td)))
    return last_td, gap


def _fresh(leg, refreshed, fails_otc, fails_us):
    code = vt.rule.LEGS[leg]["otc"]
    sig = vt.rule.LEGS[leg]["signal"]
    if code in fails_otc:
        return f"❌净值刷新失败({code})"
    if sig in fails_us:
        return f"⚠️指数刷新失败({sig})，用缓存"
    if not refreshed:
        return "·未刷新(本地缓存)"
    return "✅已刷新"


def leg_view(leg, refreshed, fails_otc, fails_us):
    cfg = vt.rule.LEGS[leg]
    # MONTH_END_LAST=False: 每日视图里「今天」不算月末, 否则非月末日也会报一次定仓决策
    nav, st, dec = vt.build(leg, MONTH_END_LAST=False)
    sig = vt.load_signal(leg)
    vol = vt.vol_series(sig)
    d_sig = sig.index[-1]
    close = float(sig.iloc[-1])
    v = float(vol.iloc[-1])
    raw = vt.raw_weight(v) or 0.0
    state_now = float(st.iloc[-1])

    last_dec = dec[-1] if dec else None
    # 是否已产生「待成交」的决策：决策日 = 最新信号日且与当前仓位不同
    pending, pending_why = None, ""
    if last_dec and last_dec["date"] >= d_sig and abs(float(last_dec["target"]) - state_now) > 1e-9:
        pending = float(last_dec["target"])
        pending_why = {"month": "月末", "cut": "快减", "add": "快补"}.get(
            str(last_dec.get("reason")), "")

    # 今日的触发条件自查（即使不满足，也让人知道离触发有多远）
    ret5 = float(sig.pct_change(5).iloc[-1])
    ma_add = float(sig.rolling(int(vt.ADD_MA)).mean().iloc[-1])
    peak = float(vol.rolling(int(vt.PEAK_WIN), min_periods=20).max().iloc[-1])
    cut_ok = (ret5 <= vt.CUT_RET and v >= vt.CUT_VOLX * vt.TV
              and raw < state_now - vt.DRIFT)
    add_ok = (v <= vt.DECAY * peak and close > ma_add and raw > state_now + vt.DRIFT)
    trig = ("✅快减条件满足" if cut_ok else "") + ("✅快补条件满足" if add_ok else "")
    trig = trig or (f"未触发（5 日 {ret5:+.1%} / vol20 {v:.1%} / "
                    f"峰值比 {v / peak if peak else float('nan'):.0%}）")

    # 月末预览：若今天就是决策日，目标 = raw（若差额未达死区则维持现仓）
    preview = raw if abs(raw - state_now) >= vt.DRIFT - 1e-12 else state_now
    diff = preview - state_now
    zone = "不动手（差额 < 死区）" if abs(diff) < vt.DRIFT - 1e-12 else \
           ("加仓" if diff > 0 else "减仓")

    ops = vt.actions(st)[-RECENT:]
    band = float(cfg["band"])
    return dict(
        leg=leg, name=cfg["name"], band=band,
        exec=cfg["exec"], exec_name=cfg["exec_name"], signal=cfg["signal"],
        nav_date=nav.index[-1].date().isoformat(), sig_date=d_sig.date().isoformat(),
        nav=float(nav.iloc[-1]), close=close, vol=v,
        tv=vt.TV, raw=raw, preview=preview, diff=diff, zone=zone,
        state=state_now, level=vt.level_name(state_now),
        pending=pending, trig=trig, ret5=ret5, peek=float(v / peak) if peak else float("nan"),
        action=(f"→ {vt.level_name(pending)}（{pending_why}）" if pending is not None else "维持"),
        fresh=_fresh(leg, refreshed, fails_otc, fails_us),
        ops=[dict(date=o["date"].date().isoformat(), kind=o["kind"], frm=o["frm"], to=o["to"])
             for o in ops],
    )


def main():
    refreshed = "--no-refresh" not in sys.argv
    fails_otc, fails_us = {}, {}
    if refreshed:
        codes = [vt.rule.LEGS[k]["otc"] for k in vt.POOL]
        sigs = [vt.rule.LEGS[k]["signal"] for k in vt.POOL]
        try:
            _l, fails_otc = vt.vdata.refresh_otc(codes=codes)
        except Exception as exc:                                  # noqa: BLE001
            fails_otc = {c: f"{type(exc).__name__}: {exc}" for c in codes}
        try:
            _l, fails_us = vt.vdata.refresh_us(codes=sigs)
        except Exception as exc:                                  # noqa: BLE001
            fails_us = {c: f"{type(exc).__name__}: {exc}" for c in sigs}

    rows = [leg_view(k, refreshed, fails_otc, fails_us) for k in vt.POOL]
    act = [r for r in rows if r["pending"] is not None]
    last_date = max(r["sig_date"] for r in rows)
    nxt, gap = next_rebal(last_date)

    L = [
        f"# 波动资产 · V9（美股两腿波动率目标仓位）· 每日操作（{last_date}）",
        f"> 池 = **只有纳指100（{vt.rule.LEGS['ndx']['otc']}）与标普500"
        f"（{vt.rule.LEGS['spx']['otc']}）**；信号 = **美元指数**收盘（`.NDX` / `.INX`）算 vol20，"
        "执行 = 场外 QDII **人民币复权净值**。",
        f"> 规则：月末收盘算近 {vt.VOL_WIN} 日年化波动 → `w = min(1, {vt.TV:.0%} / vol20)`，"
        f"与当前仓位差不足 ±{vt.DRIFT:.0%} 不交易；次一交易日收盘成交、新权重自 t+2 起生效。",
        f"> **月中也会动**：快减（5 日 ≤ {vt.CUT_RET:.0%} 且 vol20 ≥ {vt.CUT_VOLX:.1f}×TV）、"
        f"快补（vol20 ≤ {vt.DECAY:.0%}×近 {vt.PEAK_WIN} 日峰值 且 站上 MA{vt.ADD_MA}）。",
        f"> 未投部分按 {vt.CASH_ANNUAL:.2%} 计息；单边成本 {vt.COST:.2%}。"
        "机械规则输出，**非投资建议**。",
        "",
        "## 今日结论（次日成交）",
        "",
        "| 腿 | 当前仓位 | 今日动作 | 信号收盘 | vol20 | 按公式该持 | 差额 | 触发自查 | 数据 |",
        "|---|---|---|---:|---:|---:|---:|---|---|",
    ]
    for r in rows:
        L.append(f"| {r['name']} | **{r['level']}** | {r['action']} | "
                 f"{r['close']:.0f} | {r['vol']:.1%} | {r['raw']:.0%} | "
                 f"{r['diff'] * 100:+.1f}pp | {r['trig']} | {r['fresh']} |")
    L += [
        "",
        (f"**有动作：{'、'.join(r['name'] for r in act)} 明日（次一交易日）收盘净值成交。**"
         if act else
         f"**今日无动作 —— 两条腿都维持现仓。下一次月末定仓 ≈ {nxt.date()}（约 {gap} 个交易日）。**"),
        "",
        "> 提醒：月末定仓之外，**月中只有两种情形会动** —— 急跌（快减）与恐慌消退（快补）。"
        "「触发自查」列就是今天的实时判定，不满足时给出离门槛还有多远。",
        "> 「按公式该持」是**当前波动率下的预览值**，只有到触发的那个时点它才算数 —— 波动率每天都在变。",
        "",
        "## 月末预览（如果月末的波动率与今天相同）",
        "",
        "| 腿 | 当前仓位 | 月末目标 | 变动 | 折合 pp（按目标带） | 判定 |",
        "|---|---|---|---:|---:|---|",
    ]
    for r in rows:
        L.append(f"| {r['name']} | {r['state']:.0%} | {r['preview']:.0%} | "
                 f"{r['diff'] * 100:+.1f}pp | {r['diff'] * r['band']:+.2f}pp | {r['zone']} |")
    L += [
        "",
        "## 逐腿读数",
        "",
        "| 腿 | 执行代码 | 净值日 | 复权净值 | 信号代码 | 信号日 | 目标带 | 带内仓位 | 折合 pp | 现金腿 |",
        "|---|---|---|---:|---|---|---:|---:|---:|---:|",
    ]
    for r in rows:
        L.append(f"| {r['name']} | `{r['exec']}` {r['exec_name']} | {r['nav_date']} | "
                 f"{r['nav']:.4f} | `{r['signal']}` | {r['sig_date']} | {r['band']:g}pp | "
                 f"{r['state']:.0%} | {r['state'] * r['band']:.2f} | "
                 f"{(1 - r['state']) * r['band']:.2f} |")
    L += [
        "",
        f"## 最近操作流水（每条腿最近 {RECENT} 次 · 历史，非今日）",
        "",
        "| 成交日 | 腿 | 动作 | 仓位变化 |",
        "|---|---|---|---|",
    ]
    allops = []
    for r in rows:
        for o in r["ops"]:
            allops.append((o["date"], r["name"], o["kind"], o["frm"], o["to"]))
    if allops:
        for d, nm, kind, frm, to in sorted(allops, reverse=True):
            L.append(f"| {d} | {nm} | {'加仓' if kind == 'buy' else '减仓'} | "
                     f"{frm:.0%} → {to:.0%} |")
    else:
        L.append("| — | — | 无 | — |")
    L += [
        "",
        "## 提醒",
        "",
        "- **它是风险预算规则，不是方向判断**：高波动降仓、低波动补仓，不预测涨跌；"
        "所以「低波动阴跌」时它会满仓挨打（文档 §9 第 3 条点名的失效模式）。",
        "- **2020 型急跌只能减轻、不能免疫**：快减把该窗口从 −3.06% 修到 +2.98%，"
        "但仍落后静态 6.9pp（VT 要先看见波动才减仓）。2022 型慢熊才是它的主场（全年 +7.6pp vs 静态）。",
        "- **快减在 2018 型「急跌后快速 V 反」里是负贡献**（比月度档少 4.8pp）—— "
        "浅摔时降仓空间小，来回一折腾就亏。这是已知保费，不是 bug。",
        "- **与 v7 的关系**：v7（年线闸门）问「在不在年线上」，V9 问「这段波动值多少仓位」。"
        "两者共用同两条腿的同一份额度，**只能选一个当主口径**，不要叠加。",
        "- **QDII 通道**：月末要减仓时若遇暂停申购/限购，赎回照常但申购受限；"
        "本规则只减不加的情形更多，通道风险主要在执行时滞（T+2）而非无法下单。",
        "- 带位仍由主策略 `rule.py` 决定（纳指 10pp / 标普 10pp），本变种只调**带内露多少**。",
        "",
        "> 机械规则输出，**非投资建议**。",
    ]

    with open(OUT_MD, "w", encoding="utf-8") as f:
        f.write("\n".join(L))
    with open(OUT_JSON, "w", encoding="utf-8") as f:
        json.dump({
            "strategy": vt.LABEL,
            "as_of": last_date,
            "next_rebal": {"date": nxt.date().isoformat(), "approx_trading_days": gap},
            "rule": {"tv": vt.TV, "vol_win": vt.VOL_WIN, "drift": vt.DRIFT, "cap": vt.CAP,
                     "signal": "usd_index" if vt.SIGNAL == "index" else "otc_nav",
                     "trade_at": "T+1 收盘成交 / T+2 生效", "cost": vt.COST,
                     "cash_annual": vt.CASH_ANNUAL, "trigger": vt.TRIGGER,
                     "fast_cut": {"ret5": vt.CUT_RET, "volx": vt.CUT_VOLX, "on": bool(vt.CUT)},
                     "fast_add": {"decay": vt.DECAY, "peak_win": vt.PEAK_WIN,
                                  "ma": vt.ADD_MA, "on": bool(vt.ADD)}},
            "summary": {"action": bool(act), "buy": [r["leg"] for r in act if r["diff"] > 0],
                        "sell": [r["leg"] for r in act if r["diff"] < 0],
                        "refreshed": refreshed,
                        "fail_otc": list(fails_otc), "fail_us": list(fails_us)},
            "legs": [{k: v for k, v in r.items() if k != "ops"} for r in rows],
            "recent": [dict(date=d, leg=nm, kind=k, frm=a, to=b)
                       for d, nm, k, a, b in sorted(allops, reverse=True)],
        }, f, ensure_ascii=False, indent=1)

    print("报告:", OUT_MD)
    print("JSON:", OUT_JSON)
    print(f"信号日 {last_date} · 动作 {'有' if act else '无'} · 下一次决策 ≈ {nxt.date()}（约 {gap} 个交易日）")
    for r in rows:
        print(f"  {r['name']:8s} {r['level']:4s}({r['state']:.0%}) · 信号 {r['close']:.0f} · "
              f"vol20 {r['vol']:.1%} → 公式 {r['raw']:.0%}（差额 {r['diff'] * 100:+.1f}pp, {r['zone']}）")
    if fails_otc or fails_us:
        print("刷新失败:", {**fails_otc, **fails_us})


if __name__ == "__main__":
    main()
