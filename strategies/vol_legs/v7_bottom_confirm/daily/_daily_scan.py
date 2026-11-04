# -*- coding: utf-8 -*-
"""波动资产 · V7 变种（抄底确认）· 每日读数。

流程：
1. **先刷行情**（`klines.refresh()`，七腿 OHLCV，前复权），失败沿用本地并标注；
   加 `--no-refresh` 跳过联网；
2. 对**七腿**逐腿跑状态机，把四条件逐条打勾，并给出「差多少」：
   `close/VWAP−1`、`close/扇线−1`、`当日量/地量`、`当前回撤 vs 门槛`；
3. 输出：今日动作（明日成交）+ 四条件明细 + 当前仓位与止损位 + 「只缺哪一条」+
   最近 250 个交易日的动作流水。

**它是「许可」，不是买入信号**：四条件齐现只代表「这只标的可以开始考虑试仓」，
真正的下单还要过主策略的组合层额度。回测结论见 `../README.md §4`。

成交提醒：T 日盘后算出的信号，**T+1 收盘前**成交（回测口径 = T+1 收盘；实盘可 T+1 开盘或收盘）。

产物（相对本文件）：`_v7_bottom_daily.md` / `_v7_bottom_daily.json`。
机械规则输出，非投资建议。
"""
import json
import os
import sys
from datetime import datetime

try:
    sys.stdout.reconfigure(encoding="utf-8")  # type: ignore[attr-defined]
except Exception:  # noqa: BLE001
    pass

_HERE = os.path.dirname(os.path.abspath(__file__))
_V7 = os.path.dirname(_HERE)
_VL = os.path.dirname(_V7)
for _p in (_V7, _VL):
    if _p not in sys.path:
        sys.path.insert(0, _p)

import bottom as bt   # noqa: E402

OUT_MD = os.path.join(_HERE, "_v7_bottom_daily.md")
OUT_JSON = os.path.join(_HERE, "_v7_bottom_daily.json")
RECENT = 250

COND_KEYS = ("filt", "e1", "e2", "e3", "e4", "flat_veto")
COND_SHORT = {"filt": "前置过滤", "e1": "地量窗口", "e2": "底背离",
              "e3": "跌速衰竭", "e4": "触发(放量阳站回VWAP)", "flat_veto": "走平否决"}


def tick(x):
    return "✅" if x else "❌"


def gap_text(g):
    """「还差多少」——把每个阈值换算成距离，方便一眼看出「再涨多少就齐了」。"""
    out = []
    if g.get("vwap") is not None:
        out.append(f"距VWAP {g['vwap']:+.1%}")
    if g.get("fib") is not None:
        out.append(f"距扇线 {g['fib']:+.1%}")
    if g.get("vol_mult") is not None:
        out.append(f"量/地量 {g['vol_mult']:.2f}×")
    if g.get("drop") is not None:
        out.append(f"当前回撤 {g['drop']:.1%}")
    return " · ".join(out)


def main():
    refreshed = "--no-refresh" not in sys.argv
    fails = {}
    if refreshed:
        latest, fails = bt.klines.refresh(verbose=False)
    else:
        latest = {}

    stale_now = set() if not refreshed else set(bt.klines.stale())
    rows = []
    for leg in bt.LEGS:
        if leg in fails:
            print(f"[刷新失败] {leg}: {fails[leg]}（用本地缓存）")
        d = bt.diagnose(leg)
        u = bt.klines.UNIVERSE[leg]
        cur_stop = d["stop"]
        rows.append(dict(
            leg=leg, name=d["name"], code=d["code"], proxy=u["proxy"],
            date=d["date"].date().isoformat(), close=d["close"],
            band=d["leg_band"], position=d["position"],
            stop=cur_stop, target=d["target"], vwap=d["vwap"], fib=d["fib"],
            lowest=d["lowest"], dry_age=d["dry_age"],
            conds=d["conds"], signal=d["signal"], decide=d["decide"], done=d["done"],
            miss=d["miss"], why=d["why"], gap=gap_text(d["gap"]),
            fresh=("❌刷新失败" if leg in fails else
                   ("·未刷新" if not refreshed else
                    ("✅最新" if leg not in stale_now else "⚠️滞后"))),
            action={"buy": "**试仓（明日收盘）**", "sell": "**离场（明日收盘）**"}.get(
                d["decide"], "维持"),
        ))

    buys = [x for x in rows if x["decide"] == "buy"]
    sells = [x for x in rows if x["decide"] == "sell"]
    armed = [x for x in rows if x["position"] > 0]
    near = [x for x in rows if x["signal"] and not x["decide"]]

    # 近期流水
    flow = {}
    for leg in bt.LEGS:
        df = bt.klines.frame(leg, refresh_=False)
        sm = bt.state_machine(df)
        dec = sm["decide"].to_numpy()
        for i in range(max(1, len(dec) - RECENT), len(dec)):
            if dec[i] in ("buy", "sell"):
                d = df.index[i].date().isoformat()
                flow.setdefault(d, {"试仓": [], "离场": []})[
                    "试仓" if dec[i] == "buy" else "离场"].append(
                    bt.klines.UNIVERSE[leg]["name"])
    dates = sorted(flow, reverse=True)
    asof = max(x["date"] for x in rows)
    p = bt.params()

    L = [
        f"# 波动资产 · V7（抄底确认）· 每日读数（{asof}）",
        "",
        f"> 池 = 主策略七腿的**场内 OHLCV**（{' / '.join(x['name'] for x in rows)}）。"
        "抄底判定要开/高/低/量，场外净值只有收盘价，所以这里用场内代码；"
        "**ndx / spx 两腿是人民币代理（513100 / 513500）**，不是美元指数本身。",
        f"> 四要素（证据计数，非四道 AND）：① 地量锚点有效（地量分位 q{p['dry_quantile']:.2f}/"
        f"{p['dry_lookback']}日，冻结锚点 d0，t−d0≤{p['max_age_days']}日）为窗口开关；"
        f"② 底背离（价格 {p['div_window']} 日新低 + DIF 不新低，且背离后未再创新低）；"
        f"③ 跌速衰竭（收盘站上 {p['fibo_frac']:.1f} 真下压斜线 或 MACD 柱连 3 日收敛）；"
        f"④ 触发（放量 >{p['vol_mult_vs_prev5']}×前5日均量 + 站回锚定 VWAP + 阳线，唯一当日条件）。",
        f"> 前置过滤：MA{p['ma_fast']}<MA{p['ma_slow']} 且 距 {p['drop_win']} 日高点回撤 "
        f"≥{p['drop_min']:.0%}，且带 **{p.get('filt_memory_days', 0)} 日记忆期**（近 N 日内曾满足即可）；"
        f"走平否决：锚定 VWAP 近 20 日变动 <{p['flat_veto_pct']:.0%} 不入场。",
        f"> 试仓：`size_mode={p['size_mode']}`，首仓 = 风险预算 {p['risk_per_trade']:.0%} "
        f"×{p['first_fill']:.0%}" + ("×0.75（score==2）" if p['score2_discount'] < 1 else "") +
        f"；止损 = 阶段最低点 ×(1−{p['stop_struct_pct']:.0%})；"
        f"回踩不破（low≥VWAP 且收阳）{p['addon_days']} 日内补满；跌破锚定 VWAP 收盘 → 次日清仓。",
        "> **这是「可以开始考虑」的许可，不是买入信号**；回测里它没证明自己值得独立成仓"
        "（见 `../README.md §4`）。机械规则输出，**非投资建议**。",
        "",
        "## 今日动作（明日收盘成交）",
        "",
        "| 试仓 | 离场 |",
        "|---|---|",
        f"| {('、'.join(x['name'] for x in buys) or '—')} | "
        f"{('、'.join(x['name'] for x in sells) or '—')} |",
        "",
    ]
    if not buys and not sells:
        L.append("> 今日七腿均无动作。" + (
            f"（{len(near)} 只在仓、继续持有：{('、'.join(x['name'] for x in near))}）"
            if near else ""))
        L.append("")

    L += [
        "## 四条件明细",
        "",
        "| 腿 | 代码 | 日期 | 收盘 | " + " | ".join(COND_SHORT[k] for k in COND_KEYS)
        + " | 齐现 | 仓位 | 止损位 | 离阈值 | 数据 |",
        "|---" * (9 + len(COND_KEYS)) + "|",
    ]
    for x in rows:
        conds = " | ".join(tick(x["conds"][k]) for k in COND_KEYS)
        stop_txt = "—" if x["stop"] is None else f"{x['stop']:.4f}"
        L.append(
            f"| {x['name']}{'（代理）' if x['proxy'] else ''} | `{x['code']}` | {x['date']} | "
            f"{x['close']:.4f} | {conds} | {'**是**' if x['signal'] else '否'} | "
            f"{x['position']:.0%} | {stop_txt} | {x['gap']} | {x['fresh']} |")

    L += [
        "",
        "## 只缺哪一条",
        "",
        "| 腿 | 今日动作 | 还缺 | 当前仓位 | 止损位 | 止盈位（1:2） |",
        "|---|---|---|---|---|---|",
    ]
    for x in rows:
        lack = ("、".join(f"{COND_SHORT[k]}（{v}）" for k, v in x["why"].items())
                if x["why"] else "四条件已齐")
        stop_txt = "—" if x["stop"] is None else f"{x['stop']:.4f}"
        tgt_txt = "—" if x["target"] is None else f"{x['target']:.4f}"
        L.append(f"| {x['name']} | {x['action']} | {lack} | {x['position']:.0%} | "
                 f"{stop_txt} | {tgt_txt} |")

    L += [
        "",
        f"## 近期动作流水（最近 {RECENT} 个交易日 · 仅列有动作的日期）",
        "",
        "| 日期 | 试仓 | 离场 |",
        "|---|---|---|",
    ]
    if dates:
        for d in dates:
            L.append(f"| {d} | {('、'.join(flow[d]['试仓'])) or '—'} | "
                     f"{('、'.join(flow[d]['离场'])) or '—'} |")
    else:
        L.append(f"| — | 最近 {RECENT} 个交易日七腿无动作 | — |")

    L += [
        "",
        "## 提醒",
        "",
        "- **低频确认型信号**：④ 要求「放量>1.5×前5日均量 且 站回锚定 VWAP 且 阳线」同日成立，"
        "单腿约每年 1~2 次（全历史七腿 57 笔、hs300 胜率仅 36%）。连续几周全 ❌ 是常态，不是脚本坏了；"
        "这与规则 §7 自述的「宽基 ETF 最不适用」一致。",
        "- **止损锚在阶段最低点**，不是入场价：入场常在低点上方 10%~25%，止损离成本很远，"
        "所以仓位要按止损距离反推（`size_mode=risk`），否则单笔亏损会到两位数。",
        "- **在仓的腿**不重新判四条件，只盯止损位与「收盘跌破 stage_low」；这两条任一触发就离场，"
        "不看别的条件是否变好。",
        "- `a500`（563360）2024-10 才上市，250 日回撤过滤要到 2025-10 后才可能成形，"
        "此前恒为 ❌ 属正常。",
        "",
        "> 机械规则输出，**非投资建议**。",
        f"> 生成时间：{datetime.now():%Y-%m-%d %H:%M}",
    ]

    with open(OUT_MD, "w", encoding="utf-8") as f:
        f.write("\n".join(L))
    with open(OUT_JSON, "w", encoding="utf-8") as f:
        json.dump({
            "strategy": bt.LABEL, "as_of": asof, "pool": list(bt.LEGS),
            "refreshed": refreshed, "fails": fails,
            "rule": {k: (v if isinstance(v, (int, float, str, bool, list, tuple, type(None)))
                         else str(v)) for k, v in p.items()},
            "summary": {"buy": len(buys), "sell": len(sells), "in_position": len(armed),
                        "stale": sum(1 for x in rows if x["fresh"].startswith(("⚠️", "❌")))},
            "legs": rows,
            "recent_flow": [{"date": d, **flow[d]} for d in dates],
        }, f, ensure_ascii=False, indent=1)

    print("报告:", OUT_MD)
    print("JSON:", OUT_JSON)
    print(f"数据日 {asof} | 试仓 {[x['name'] for x in buys]} · 离场 {[x['name'] for x in sells]}")
    for x in rows:
        lack = "、".join(f"{COND_SHORT[k]}:{v}" for k, v in x["why"].items()) or "无"
        print(f"  {x['name']:14s} 齐现={'是' if x['signal'] else '否'} "
              f"仓位={x['position']:.0%} 缺={lack}"
              + (f" · {x['gap']}" if x["gap"] else ""))
    if fails:
        print("刷新失败:", fails)


if __name__ == "__main__":
    main()
