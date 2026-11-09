# -*- coding: utf-8 -*-
"""V13 · 成交量操盘笔记 · 每日操作清单（收盘后跑）。

流程：
1. **先增量刷新场内 ETF 日线**（`ohlcv.refresh()`，家族共享库）；失败沿用本地缓存并在表里标 ❌。
2. 对**上线池三腿**（科创50 `588000` / 恒生科技 `513180` / 黄金 `518880`）逐腿跑 `state_series()`：
   取当日触发的全部信号 ID、最近高量锚点、支撑 S / 压力 R、观察窗剩余天数、
   目标仓位（占该腿目标带）与**昨天→今天的目标仓位变化**（= 明天开盘该下的那一单）。
3. 另出一张**完整七腿表**（含四条「不接」的腿）—— 规格 §8 判定「不接」不等于不看，
   它们的状态可以作为「同一天里量能信号在不同标的上的分歧」参考，但不产生操作。
4. 附最近 10 个交易日的信号流水 + 20 日冲突率。

成交提醒：T 日收盘算出的目标仓位，**T+1 开盘**成交（规格 §5 `exec="next_open"`）。
本变种**不上线**（`pipeline/strategies.json` 不注册、`run_daily_vol_legs.ps1` 不调用），
需要时手跑本脚本；结论见 `../backtest/_v13_volume_bt.md` §9。

产物（相对本文件）：`_v13_volume_daily.md` / `_v13_volume_daily.json`。
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

_HERE = os.path.dirname(os.path.abspath(__file__))       # .../v13_volume_notes/daily
_V13 = os.path.dirname(_HERE)
_VL = os.path.dirname(_V13)
if _V13 not in sys.path:
    sys.path.insert(0, _V13)

import ohlcv       # noqa: E402
import rule        # noqa: E402
import vol_notes as vn  # noqa: E402

OUT_MD = os.path.join(_HERE, "_v13_volume_daily.md")
OUT_JSON = os.path.join(_HERE, "_v13_volume_daily.json")
RECENT = 10
CONFLICT_WIN = 20


def _fresh(code, latest, fails, refresh):
    if not refresh:
        return "缓存"
    if code in fails:
        return "❌刷新失败"
    return "✅" if latest.get(code) else "—"


def one_leg(leg, latest, fails, refresh=True):
    """单腿今日状态 -> dict。"""
    df = ohlcv.frame(leg)
    sim = vn.state_series(df, leg)
    st = vn.latest_state(sim)
    prev = sim.iloc[-2] if len(sim) > 1 else sim.iloc[-1]
    dm = st["mult"] - float(prev["mult"])
    if abs(dm) <= 1e-9:
        act = "不动"
    elif dm < 0:
        act = "清仓" if st["mult"] <= 1e-9 else "减仓"
    else:
        act = "上车" if st["mult"] > 0.75 else "加仓"
    conf20 = float(sim["conflict"].iloc[-CONFLICT_WIN:].mean())
    sig20 = float(sim[[c for c in sim.columns if c.startswith("sig_")]]
                  .any(axis=1).iloc[-CONFLICT_WIN:].mean())
    return dict(
        leg=leg, name=rule.LEGS[leg]["name"], role=rule.LEGS[leg]["role"],
        band=float(rule.LEGS[leg]["band"]), code=ohlcv.CODES[leg],
        edge=ohlcv.EDGE_LABEL[ohlcv.EDGE[leg][0]], edge_note=ohlcv.EDGE[leg][1],
        date=st["date"], close=st["close"], S=st["S"], R=st["R"],
        anchor_age=st["anchor_age"], in_window=st["in_window"], hv=st["hv"],
        rvol=st["rvol"], body=st["body"], up=st["up"], dn=st["dn"], atr=st["atr"],
        dist_S=st["dist_S"], dist_R=st["dist_R"],
        mult=st["mult"], prev_mult=st["prev_mult"], delta_mult=dm,
        action=act, rule=st["rule"], why=st["why"], conflict=st["conflict"],
        signals=st["signals"], conflict20=conf20, signal20=sig20,
        fresh=_fresh(ohlcv.CODES[leg], latest, fails, refresh),
        target_pp=st["mult"] * float(rule.LEGS[leg]["band"]),
    )


def flow(sim, n=RECENT):
    """最近 n 个交易日的信号流水（只列有信号或有动作的日子）。

    `action` 只在**仓位真的变了**时写动作名；仓位没变但信号又触发时写「（不变）」——
    否则「已经空仓又连续 10 天显示清仓」会把日报读成一串噪音。
    """
    out = []
    sub = sim.iloc[-n:]
    prev = float(sim["mult"].iloc[-n - 1]) if len(sim) > n else 0.0
    for d, row in sub.iterrows():
        ids = [k[4:] for k in sim.columns if k.startswith("sig_") and bool(row[k])]
        mult = float(row["mult"])
        moved = abs(mult - prev) > 1e-9
        prev = mult
        if not ids and not moved:
            continue
        out.append(dict(date=d.date().isoformat(),
                        action=str(row["action"]) if moved else "（不变）",
                        rule=str(row["rule"]), signals=ids,
                        mult=round(mult, 4), close=round(float(row["close"]), 4)))
    return out


def main():
    refresh = "--no-refresh" not in sys.argv
    latest, fails = ({}, {})
    if refresh:
        latest, fails = ohlcv.refresh()
        bad = [k for k in fails]
        print(f"[数据] 增量刷新 {len(latest)} 码" + (f" · 失败 {bad}" if bad else " · 全部成功"))
    else:
        print("[数据] 跳过刷新（--no-refresh），只用本地缓存")

    rows = {k: one_leg(k, latest, fails, refresh) for k in vn.LEGS}
    pool = [rows[k] for k in vn.POOL]
    ref = [rows[k] for k in ohlcv.REFERENCE]
    maxdate = max(r["date"] for r in rows.values())

    payload = dict(
        strategy="vol_legs", variant="v13_volume_notes",
        title="V13 成交量操盘笔记（阿基米德笔记2.0 第一章）· 每日操作",
        as_of=maxdate, generated_at=pd.Timestamp.now().strftime("%Y-%m-%d %H:%M"),
        pool=vn.POOL, reference=ohlcv.REFERENCE,
        rules=dict(high_vol_mode=vn.CFG["high_vol_mode"],
                   high_vol_quantile=vn.CFG["high_vol_quantile"],
                   window_days=vn.CFG["window_days"],
                   priority=vn.CFG["priority"], exec=vn.CFG["exec"],
                   position=vn.CFG["position"]),
        legs=rows,
        flow={k: flow(vn.state_series(ohlcv.frame(k), k)) for k in vn.POOL},
        note="T 日收盘信号 -> T+1 开盘成交；本变种不上线，仅留档与手跑。",
    )
    with open(OUT_JSON, "w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False, indent=2, default=str)

    L = [
        "# V13 成交量操盘笔记 · 每日操作",
        "",
        f"> 数据截至 **{maxdate}** · 生成 {payload['generated_at']} · "
        f"高量判据 `{vn.CFG['high_vol_mode']}` q{vn.CFG['high_vol_quantile']:.2f}/"
        f"{vn.CFG['high_vol_lookback']} 日 · 观察窗 {vn.CFG['window_days']} 日 · "
        f"优先级 {' > '.join(vn.CFG['priority'])}",
        f"> 成交：**T 日收盘算、T+1 开盘成交**。仓位以「占该腿目标带」表示"
        f"（{vn.CFG['position']['clear']:.0%} / {vn.CFG['position']['reduce']:.0%} / "
        f"{vn.CFG['position']['buy']:.0%}）。",
        "",
        "## 1. 上线池（规格 §8「谨慎可试」，本变种给结论的一档）",
        "",
        "| 腿 | 代码 | 判定 | 收盘 | 高量锚点 | 窗内 | 支撑 S | 压力 R | 距S | 距R | 量比 | 触发信号 | 动作 | 目标仓位 | 该腿pp | 数据 |",
        "|---|---|---|---:|---:|---|---:|---:|---:|---:|---:|---|---|---:|---:|---|",
    ]
    for r in sorted(pool, key=lambda z: z["delta_mult"]):
        anchor = "今日高量" if r["anchor_age"] == 0 else f"{r['anchor_age']} 日前"
        L.append(f"| {r['name']} | `{r['code']}` | {r['edge']} | {r['close']:.4f} | {anchor} | "
                 f"{'是' if r['in_window'] else '否'} | "
                 f"{f'{r['S']:.4f}' if r['S'] == r['S'] else '—'} | "
                 f"{f'{r['R']:.4f}' if r['R'] == r['R'] else '—'} | "
                 f"{f'{r['dist_S']:+.2%}' if r['dist_S'] is not None else '—'} | "
                 f"{f'{r['dist_R']:+.2%}' if r['dist_R'] is not None else '—'} | "
                 f"{r['rvol']:.2f} | {('+'.join(r['signals']) or '—')} | "
                 f"**{r['action']}** | {r['mult']:.0%} | {r['target_pp']:.2f}pp | {r['fresh']} |")

    L += ["", "### 动作明细", ""]
    for r in pool:
        if abs(r["delta_mult"]) > 1e-9:
            arrow = "↑" if r["delta_mult"] > 0 else "↓"
            others = [x for x in r["signals"] if x not in str(r["rule"]).split("+")]
            extra = f"（当日另有 {'+'.join(others)}）" if others else ""
            L.append(f"- **{r['name']}** {arrow} {r['prev_mult']:.0%} → {r['mult']:.0%} 带位"
                     f"（{r['prev_mult'] * r['band']:.2f}pp → {r['target_pp']:.2f}pp）"
                     f"· 生效信号 `{r['rule'] or '—'}`{extra}"
                     f" · {r['why'] or '（见规则）'}")
    if not any(abs(r["delta_mult"]) > 1e-9 for r in pool):
        L.append("- 今日上线池三腿均无仓位变化（维持原仓位）。")
    L += [
        "",
        "> 提醒：目标仓位是**该腿目标带的比例**，不是总资产比例。"
        "例：黄金 `mult=50%` 且带 10pp → 实际 5pp of 总资产。",
        "",
        "## 2. 完整七腿（含规格 §8 判定「不接」的四条 —— 只看不做）",
        "",
        "| 腿 | 代码 | 判定 | 收盘 | 窗内 | 支撑 S | 压力 R | 量比 | 触发信号 | 动作 | 目标仓位 | 20日冲突率 |",
        "|---|---|---|---:|---|---:|---:|---:|---|---|---:|---:|",
    ]
    for k in vn.LEGS:
        r = rows[k]
        L.append(f"| {r['name']} | `{r['code']}` | {r['edge']} | {r['close']:.4f} | "
                 f"{'是' if r['in_window'] else '否'} | "
                 f"{f'{r['S']:.4f}' if r['S'] == r['S'] else '—'} | "
                 f"{f'{r['R']:.4f}' if r['R'] == r['R'] else '—'} | {r['rvol']:.2f} | "
                 f"{('+'.join(r['signals']) or '—')} | {r['action']} | {r['mult']:.0%} | "
                 f"{r['conflict20']:.0%} |")

    L += ["", "## 3. 最近信号流水（上线池）", ""]
    for k in vn.POOL:
        fl = payload["flow"][k]
        L.append(f"### {rule.LEGS[k]['name']}（近 {RECENT} 日，{len(fl)} 条）")
        if not fl:
            L += ["", "（无）", ""]
            continue
        L += ["", "| 日期 | 动作 | 触发 | 仓位 | 收盘 |", "|---|---|---|---:|---:|"]
        for x in fl:
            sig = "+".join(x["signals"]) or "—"
            mark = f"（生效 {x['rule']}）" if x["rule"] and x["rule"] != sig else ""
            L.append(f"| {x['date']} | {x['action']} | `{sig}`{mark} | "
                     f"{x['mult']:.0%} | {x['close']:.4f} |")
        L.append("")

    avg_sig = sum(r["signal20"] for r in pool) / len(pool)
    avg_conf = sum(r["conflict20"] for r in pool) / len(pool)
    L += [
        "## 4. 读数提示",
        "",
        f"- 上线池近 {CONFLICT_WIN} 日：**有信号日 {avg_sig:.0%}、同日买卖冲突 {avg_conf:.0%}**"
        "（回测里这个冲突率对应「风控优先」档；改成「冲突不动」年化会高一点，见回测 §7）。",
        f"- 上线池平均动作约 {sum(r['mult'] for r in pool) / len(pool):.0%} 带位 —— "
        "本变种回测的结论是：**它压仓位、不产生超额**（`backtest/_v13_volume_bt.md` §9）。"
        "所以这张表的正确用法是**给已有持仓做「今天该不该减」的检查**，不是买入清单。",
        "- 一字板（涨跌 ≥9.8% 且 H≈L）当天的目标仓位会顺延，次日开盘才可能成交。",
        "",
        "> **机械规则输出，非投资建议。** 本变种未进每日流程（`pipeline/strategies.json` 不注册），"
        "需要时手跑：`python strategies/vol_legs/v13_volume_notes/daily/_daily_scan.py`。",
    ]

    with open(OUT_MD, "w", encoding="utf-8") as f:
        f.write("\n".join(L))

    print("报告:", OUT_MD)
    print("数据:", OUT_JSON)
    for r in pool:
        print(f"  {r['name']:8s} {r['date']} 收盘 {r['close']:.4f} "
              f"S {r['S']:.4f} R {r['R']:.4f} 量比 {r['rvol']:.2f} "
              f"[{'/'.join(r['signals']) or '—'}] {r['action']} {r['mult']:.0%} {r['fresh']}")


if __name__ == "__main__":
    main()
