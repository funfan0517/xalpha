# -*- coding: utf-8 -*-
"""波动资产 · V7 变种（美股两腿年线闸门）· 每日操作。

流程：
1. **先刷新数据**：美股指数（`.NDX` / `.INX`，走 `data.us_series` 的按需增量）+ 场外 QDII 净值
   （`data.refresh_otc`，仅这两条腿）；加 `--no-refresh` 用本地缓存，报告会标「未刷新」；
2. 对两条腿各算四个数：**美元指数** close 与 MA200 的关系、月末连续在年线下的**月数**、
   当前仓位（满仓 / 半仓 / 清仓）、下一次决策日；
3. 输出：今日是否有动作（月末才可能有）、「要动的话还缺什么」、逐腿读数、最近 8 次操作流水。

**关键时点**：只有**每月最后一个交易日收盘**才产生信号，**次一交易日收盘净值**成交。
其余日子本脚本只会说「维持」——不是脚本坏了。

产物（相对本文件）：daily/_v7_us_gate_daily.md / _v7_us_gate_daily.json。
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
_V7 = os.path.dirname(_HERE)
sys.path.insert(0, _V7)

import us_gate as ug  # noqa: E402

OUT_MD = os.path.join(_HERE, "_v7_us_gate_daily.md")
OUT_JSON = os.path.join(_HERE, "_v7_us_gate_daily.json")
RECENT = 8


def next_rebal(idx, last):
    """下一个决策日 = 当月最后一个交易日；若已过则下月末。返回 (日期, 估算交易日数)。"""
    cal_end = (pd.Timestamp(last) + pd.offsets.MonthEnd(0)).normalize()
    if pd.Timestamp(last).normalize() >= cal_end:
        cal_end = (pd.Timestamp(last) + pd.offsets.MonthEnd(1)).normalize()
    # 月末若不是交易日，回退到最近的交易日（用已有日历 + 前后推）
    last_td = cal_end - pd.offsets.BDay(0)
    while last_td.month != cal_end.month:
        last_td -= pd.offsets.BDay(1)
    gap = int(len(pd.bdate_range(pd.Timestamp(last).normalize() + pd.offsets.BDay(1), last_td)))
    return last_td, gap


def leg_view(leg, refreshed, fails):
    cfg = ug.rule.LEGS[leg]
    nav, st, dec = ug.build(leg)
    sig = ug.load_signal(leg)
    ma = sig.rolling(ug.MA_WIN, min_periods=ug.MA_WIN).mean()

    d_sig = sig.index[-1]
    close, ma_v = float(sig.iloc[-1]), float(ma.iloc[-1])
    above = close > ma_v * (1.0 + ug.HYST)
    last_dec = dec[-1] if dec else None
    # 连续在年线下的月数：取最后一条决策的计数（它就是「截至上一个月末」的状态）
    below = int(last_dec["below"]) if last_dec else 0

    state_now = float(st.iloc[-1])
    # 是否已产生「待成交」的决策（决策日 = 最新信号日，成交日在其后）
    pending = None
    if last_dec and last_dec["date"] >= d_sig and abs(float(last_dec["target"]) - state_now) > 1e-9:
        pending = float(last_dec["target"])

    # 当前**已生效**的闸门层（决策未成交时，看再上一条决策）
    gate_now = float(last_dec.get("gate", last_dec["target"])) if last_dec else state_now
    if pending is not None and len(dec) >= 2:
        gate_now = float(dec[-2].get("gate", dec[-2]["target"]))

    floor = ug.floor_for(leg)
    tv = float(ug.params()["TV"] or 0.0)
    vol_now = float(ug.vol_series(sig, ug.params()).iloc[-1]) if tv > 0 else None
    scale_now = ug.vol_scale(vol_now) if tv > 0 else 1.0
    # 深破位豁免只有「这次破位还没累够确认月数」时才有意义
    deep_now = (bool(ug.DEEP) and (close / ma_v - 1.0) <= -float(ug.DEEP)
                and below + 1 < ug.CONFIRM)
    if not above:
        if deep_now:
            need = (f"下一个月末仍在年线下 → **直接减到 {ug.level_name(floor)}**"
                    f"（当前已破年线 {close / ma_v - 1:+.1%}，超过 {ug.DEEP:.0%} 免确认线，"
                    f"不用等第 {ug.CONFIRM} 个月）")
        elif below + 1 >= ug.CONFIRM:
            need = (f"下一个月末仍在年线下（当前已连续 {below} 个月，再确认 1 次即减到 "
                    f"{ug.level_name(floor)}）")
        else:
            need = (f"下一个月末仍在年线下 → 才累计到 {below + 1}/{ug.CONFIRM} 个月"
                    f"（到齐才减到 {ug.level_name(floor)}；若破位深度超 {ug.DEEP:.0%} 则当月就减）")
    else:
        need = "无（在年线上，维持满仓）"
    back = ("闸门已回满" if above else
            f"收盘重新站上年线（现 {close:.0f} vs MA{ug.MA_WIN} {ma_v:.0f}，"
            f"差 {close / ma_v - 1:+.1%}）→ 次月即回满")
    if tv > 0:
        if vol_now:
            need += (f"；**再乘波动层系数 {scale_now:.0%}**"
                     f"（vol{ug.VOL_WIN} {vol_now:.1%} vs TV {tv:.0%}）")
        back += f"（实际仓位再乘波动层系数 {scale_now:.0%}）"

    recent = ug.actions(st)[-RECENT:]
    return dict(
        leg=leg, name=cfg["name"], band=float(cfg["band"]), tv=tv,
        vol=vol_now, scale=scale_now,
        exec=cfg["exec"], exec_name=cfg["exec_name"], signal=cfg["signal"],
        nav_date=nav.index[-1].date().isoformat(), sig_date=d_sig.date().isoformat(),
        nav=float(nav.iloc[-1]), close=close, ma=ma_v, dev=close / ma_v - 1.0,
        above=bool(above), below=below, floor=floor, gate=gate_now,
        state=state_now, level=ug.level_name(state_now), pending=pending,
        action=("回补 → " if pending and pending > state_now else
                "减仓 → " if pending is not None else "维持") +
               (ug.level_name(pending) if pending is not None else ""),
        lack=need, back=back,
        fresh=_fresh(leg, refreshed, fails),
        ops=[dict(date=o["date"].date().isoformat(), kind=o["kind"],
                  frm=o["frm"], to=o["to"]) for o in recent],
    )


def _fresh(leg, refreshed, fails):
    code = ug.rule.LEGS[leg]["otc"]
    if code in fails:
        return f"❌刷新失败({code})"
    if not refreshed:
        return "·未刷新(本地缓存)"
    return "✅已刷新"


def main():
    refreshed = "--no-refresh" not in sys.argv
    fails = {}
    if refreshed:
        codes = [ug.rule.LEGS[k]["otc"] for k in ug.POOL]
        try:
            _latest, fails = ug.vdata.refresh_otc(codes=codes)
        except Exception as exc:                                 # noqa: BLE001
            fails = {c: f"{type(exc).__name__}: {exc}" for c in codes}
    else:
        try:
            ug.load_signal(ug.POOL[0])                           # 触发一次按需增量（若陈旧）
        except Exception:                                        # noqa: BLE001
            pass

    rows = [leg_view(k, refreshed, fails) for k in ug.POOL]
    act = [r for r in rows if r["pending"] is not None]
    sells = [r for r in act if r["pending"] < r["state"]]
    buys = [r for r in act if r["pending"] > r["state"]]
    last_date = max(r["sig_date"] for r in rows)
    nxt, gap = next_rebal(pd.DatetimeIndex([pd.Timestamp(last_date)]),
                          pd.Timestamp(last_date))

    L = [
        f"# 波动资产 · V7（美股两腿年线闸门）· 每日操作（{last_date}）",
        f"> 池 = **只有纳指100（{ug.rule.LEGS['ndx']['otc']}）与标普500"
        f"（{ug.rule.LEGS['spx']['otc']}，此前 050025 桥接）**；"
        "信号 = **美元指数**收盘（`.NDX` / `.INX`），执行 = 场外 QDII **人民币复权净值**。",
        f"> 规则：MA{ug.MA_WIN}（日频算、**月末看**）· 连续 {ug.CONFIRM} 个月在年线下方才减仓 → "
        f"纳指 {ug.floor_for('ndx'):.0%} / 标普 {ug.floor_for('spx'):.0%} · "
        f"**深破位豁免**：首次破位即低于年线 {ug.DEEP:.0%} 以上 → **当月就减**（不等第二个月）· "
        "重新站上年线 → **次月即回满**（不等确认）。",
        (f"> **波动层**：最终权重 = 闸门 × min(1, {float(ug.TV):.0%} / vol{ug.VOL_WIN})，"
         f"月末用美元指数算 vol{ug.VOL_WIN}、变动 < {ug.VOL_DEAD:.0%} 不动手 —— "
         "所以「清仓 / 减半 / 满仓」是**闸门层**，实际仓位还要乘右边那一列系数。"
         if float(ug.TV or 0) > 0 else "> 波动层关闭（`TV=None`）：仓位只有 满仓 / 半仓 / 清仓 三档。"),
        "> 成交：月末收盘决策 → **次一交易日收盘净值**（QDII 净值本身滞后 1~2 日，实盘按 T+1~T+2 提交）。",
        f"> 空仓部分按货基 {ug.CASH_ANNUAL:.2%} 计息。机械规则输出，**非投资建议**。",
        "",
        "## 今日结论（明日成交）",
        "",
        "| 腿 | 当前仓位 | 闸门层 | vol20 | 系数 | 今日动作 | 收盘（美元指数） | MA200 | 距年线 | 连续在年线下 | 数据 |",
        "|---|---|---|---:|---:|---|---:|---:|---:|---:|---|",
    ]
    for r in rows:
        vtxt = f"{r['vol']:.1%}" if r["vol"] else "—"
        stxt = f"{r['scale']:.0%}" if r["tv"] > 0 else "—"
        L.append(f"| {r['name']} | **{r['state']:.0%}** | {ug.level_name(r['gate'])} | "
                 f"{vtxt} | {stxt} | "
                 f"{r['action']} | {r['close']:.0f} | {r['ma']:.0f} | {r['dev']:+.1%} | "
                 f"{r['below']} 个月 | {r['fresh']} |")
    L += [
        "",
        (f"**有动作：{'、'.join(r['name'] for r in sells)} 减仓"
         f"{'、' + '、'.join(r['name'] for r in buys) + ' 回补' if buys else ''}"
         f"，明日（次一交易日）收盘净值成交。**" if act else
         f"**今日无动作 —— 两条腿都维持现仓。下一次决策日 ≈ {nxt.date()}（约 {gap} 个交易日后）。**"),
        "",
        "> 提醒：v7 **只在月末产生信号**。月中任何一天跑这个脚本，答案都会是「维持」，",
        "> 除非上一个交易日月末已触发、且成交日还没到。",
        "",
        "## 要动的话还缺什么",
        "",
        "| 腿 | 现在 | 减仓还缺 | 回补还缺 |",
        "|---|---|---|---|",
    ]
    for r in rows:
        L.append(f"| {r['name']} | {r['state']:.0%}（闸门 {ug.level_name(r['gate'])}） | "
                 f"{r['lack']} | {r['back']} |")
    L += [
        "",
        "## 逐腿读数",
        "",
        "| 腿 | 执行代码 | 净值日 | 复权净值 | 信号代码 | 信号日 | 目标带 | 带内仓位 | 折合 pp |",
        "|---|---|---|---:|---|---|---:|---:|---:|",
    ]
    for r in rows:
        L.append(f"| {r['name']} | `{r['exec']}` {r['exec_name']} | {r['nav_date']} | "
                 f"{r['nav']:.4f} | `{r['signal']}` | {r['sig_date']} | {r['band']:g}pp | "
                 f"{r['state']:.0%} | {r['state'] * r['band']:.2f} |")
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
            L.append(f"| {d} | {nm} | {'回补' if kind == 'buy' else '减仓'} | "
                     f"{frm:.0%} → {to:.0%}（{ug.level_name(to)}） |")
    else:
        L.append("| — | — | 无 | — |")
    L += [
        "",
        "## 提醒",
        "",
        "- **月度规则 ≠ 高频择时**：它治慢熊（2018Q4 / 2022），对 2020 那种 5 周急跌结构性无效"
        "（月末看盘抓不到）。**深破位豁免只补回「月末破位已经很深」的那部分** —— "
        "2020-02 标普月末破位 −3.05% 因此提前一个月减仓；纳指 2 月末还在年线上，豁免救不到。",
        "- **标普只减半**：标普跌幅浅、恢复快，清仓的机会成本更高（回测里清仓 Calmar 0.46 vs 减半 0.64）。",
        "- **波动层是风险预算、不是择时**：低波动阴跌（波动率低但一直跌）里它会满仓挨打；"
        "它每月只在月末调一次，`|Δ仓位| < 5pp` 不动手，所以「当前仓位」在月中是不变的。",
        "- **QDII 通道**：纳指/标普 QDII 可能限购、暂停申购或溢价 —— 触发减仓时若无法赎回，"
        "按文档纪律「档位顺延，不追场内溢价」。",
        "- 与主策略的关系：本变种只管**这两条腿露多少目标带**（带位仍由 `rule` 决定），"
        "不改变组合层仓位带。",
        "",
        "> 机械规则输出，**非投资建议**。",
    ]

    with open(OUT_MD, "w", encoding="utf-8") as f:
        f.write("\n".join(L))
    with open(OUT_JSON, "w", encoding="utf-8") as f:
        json.dump({
            "strategy": ug.LABEL,
            "as_of": last_date,
            "next_rebal": {"date": nxt.date().isoformat(), "approx_trading_days": gap},
            "rule": {"ma": ug.MA_WIN, "confirm_months": ug.CONFIRM, "deep": ug.DEEP,
                     "hyst": ug.HYST,
                     "floor": {k: ug.floor_for(k) for k in ug.POOL},
                     "signal": "usd_index", "trigger": ug.TRIGGER,
                     "trade_at": "T+1 净值", "cost": ug.COST,
                     "cash_annual": ug.CASH_ANNUAL},
            "summary": {"action": bool(act), "buy": [r["leg"] for r in buys],
                        "sell": [r["leg"] for r in sells],
                        "refreshed": refreshed, "fail": list(fails)},
            "legs": [{k: v for k, v in r.items() if k != "ops"} for r in rows],
            "recent": [dict(date=d, leg=nm, kind=k, frm=a, to=b)
                       for d, nm, k, a, b in sorted(allops, reverse=True)],
        }, f, ensure_ascii=False, indent=1)

    print("报告:", OUT_MD)
    print("JSON:", OUT_JSON)
    print(f"信号日 {last_date} · 动作 {'有' if act else '无'} · 下一次决策 ≈ {nxt.date()}（约 {gap} 个交易日）")
    for r in rows:
        print(f"  {r['name']:8s} 仓位 {r['state']:.0%}（闸门 {ug.level_name(r['gate'])}）"
              f" 信号 {r['close']:.0f} vs MA{ug.MA_WIN} {r['ma']:.0f}（{r['dev']:+.1%}）· "
              f"连续在年线下 {r['below']} 个月 · {r['action']}")
    if fails:
        print("刷新失败:", fails)


if __name__ == "__main__":
    main()
