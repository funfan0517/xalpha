# -*- coding: utf-8 -*-
"""波动资产 · v11-gate（支撑位闸门）· 今日操作（逐腿档位 + 为什么 + 还缺什么）。

流程：
1. **先刷新场外复权净值**（`data.refresh_otc()`，七腿 + 桥接代理），失败沿用本地并标注；
   加 `--no-refresh` 跳过联网（用本地缓存，报告里标「未刷新」）；
2. 对**七腿**（`rule.LEG_ORDER`）逐腿跑 `support_gate.state_series`：趋势（MA200 月频）×
   支撑（已确认枢轴低点）→ 目标档位 1.0 / 0.5 / 0.2 / 0.0；
3. 同时跑一列 `USE_SUPPORT=False` 的**纯趋势档**作对照 —— 默认档（完整版）含支撑层，
   而支撑层在回测里是净负贡献（README §2），纯趋势才是唯一站得住的配置；
4. 输出：今日操作（明日成交）+ 七腿档位表 + 逐腿「为什么是这个档位」+ 最近 120 日动作流水。

口径与 `backtest.py` **同一份代码**（`state_series`），报告与回测不会漂移。

产物（相对本文件）：`_v11_gate_daily.md` / `_v11_gate_daily.json`。
机械规则输出，非投资建议 —— 且本变种**不上线**（`pipeline/strategies.json` 不注册）。
"""
import json
import os
import sys

import numpy as np
import pandas as pd

try:
    sys.stdout.reconfigure(encoding="utf-8")  # type: ignore[attr-defined]
except Exception:  # noqa: BLE001
    pass

_HERE = os.path.dirname(os.path.abspath(__file__))
_GATE = os.path.dirname(_HERE)
if _GATE not in sys.path:
    sys.path.insert(0, _GATE)

import support_gate as sg  # noqa: E402

tg = sg.tg
OUT_MD = os.path.join(_HERE, "_v11_gate_daily.md")
OUT_JSON = os.path.join(_HERE, "_v11_gate_daily.json")
RECENT = 120


def level_name(x):
    """档位 -> 中文名（与图、README 共用同一张表）。"""
    return sg.LEVEL_TXT.get(round(float(x), 6), f"{float(x):.0%}")


def freshness(leg, date, fails, refreshed):
    cfg = sg.base.LEGS[leg]
    code = cfg.get("otc")
    if code in fails:
        return f"❌刷新失败({code})"
    if not refreshed:
        return "·未刷新(本地缓存)"
    exp = tg.vdata._expected_otc_latest(cfg.get("otc_qdii", False))
    return "✅最新" if date >= exp else f"⚠️滞后(应至 {exp})"


def lack(x):
    """「还缺什么才能上调一档」—— 空仓看回补条件，观察仓看趋势条件，半仓看贴线带。"""
    if x["state"] >= 1.0 - 1e-9:
        return "已满带"
    revive = (x["support"] * (1.0 + sg.NEAR)) if np.isfinite(x["support"]) else float("nan")
    if x["state"] <= 1e-9:                       # 空仓：只有破位/止损会到这里
        if x["cool"] > 0:
            return (f"破位冷却剩 {int(x['cool'])} 日；结束后需 close > 支撑+{sg.NEAR:.0%}"
                    f"（{revive:.4f}）才回补")
        return f"破位后未离开贴线带：需 close > 支撑+{sg.NEAR:.0%}（{revive:.4f}）"
    if not x["trend"]:                           # 观察仓：趋势不成立
        gaps = []
        if not x["above_ma"]:
            gaps.append(f"close 需站上 MA{sg.MA_WIN}（{x['ma']:.4f}，现距 {x['dev']:+.1%}）")
        if not x["slope_up"]:
            gaps.append(f"MA{sg.MA_WIN} 需转上行（现 {sg.SLOPE_N} 日斜率向下）")
        return " + ".join(gaps) if gaps else "趋势条件已齐，等下一个月末判定"
    if np.isfinite(x["dist"]):                   # 半仓：贴线
        return (f"距支撑需 > {sg.NEAR + sg.HYST:.0%}（现 {x['dist']:+.1%}）"
                f"才回满带 —— 滞回 {sg.HYST:.0%} 防在 {sg.NEAR:.0%} 边界来回摩擦")
    return "支撑未成形→按满带给"


def one_line(x):
    """逐腿一句话：今天是什么档位、为什么、两个关键量各在哪。"""
    seg = [f"**{x['name']}** {x['nav']:.4f} · {level_name(x['state'])}（{x['why']}）"]
    if np.isfinite(x["support"]):
        seg.append(f"支撑 {x['support']:.4f}（距 {x['dist']:+.1%}）")
    else:
        seg.append("支撑未成形")
    seg.append(f"MA{sg.MA_WIN} {x['ma']:.4f}（距 {x['dev']:+.1%}，斜率{'↑' if x['slope_up'] else '↓'}）")
    if abs(x["state"] - x["state_trend"]) > 1e-9:
        seg.append(f"纯趋势档为 {level_name(x['state_trend'])}")
    return " · ".join(seg)


def main():
    refreshed = "--no-refresh" not in sys.argv
    fails = {}
    if refreshed:
        latest, fails = tg.vdata.refresh_otc()
        maxdate = max(latest.values()) if latest else ""
    else:
        maxdate = ""

    rows, flow = [], {}
    for leg in sg.LEGS:
        cfg = sg.base.LEGS[leg]
        px = sg.load(leg)
        sm = sg.state_series(px, leg)
        sm_tr = sg.state_series(px, leg, USE_SUPPORT=False)
        r = sm.iloc[-1]
        state = float(r["state"])
        prev = float(sm["state"].iloc[-2]) if len(sm) > 1 else state
        close = float(px.iloc[-1])
        ma = float(r["ma"]) if np.isfinite(r["ma"]) else float("nan")
        sup = float(r["support"]) if np.isfinite(r["support"]) else float("nan")
        x = dict(
            leg=leg, name=cfg["name"], role=cfg["role"], band=float(cfg["band"]),
            exec=cfg["exec"], exec_name=cfg["exec_name"],
            date=px.index[-1].date().isoformat(), nav=close,
            ma=ma, slope_up=bool(np.isfinite(r["slope"]) and r["slope"] > 0),
            above_ma=bool(np.isfinite(ma) and close >= ma),
            dev=(close / ma - 1.0) if np.isfinite(ma) else float("nan"),
            trend=bool(r["trend"]), support=sup,
            dist=float(r["dist"]) if np.isfinite(r["dist"]) else float("nan"),
            cool=float(r["cool"]), state=state, prev=prev,
            state_trend=float(sm_tr["state"].iloc[-1]),
            why=str(r["why"]),
            level=level_name(state),
            pos_pp=state * float(cfg["band"]),
            fresh=freshness(leg, px.index[-1].date().isoformat(), fails, refreshed),
        )
        x["action"] = "加仓" if state > prev + 1e-9 else ("减仓" if state < prev - 1e-9 else "维持")
        x["lack"] = lack(x)
        rows.append(x)

        # 动作流水按**决策日**记（state 变动当日盘后算、次日成交），与 v5 的 decide 同一折法
        for a in sg.actions(sm["state"]):
            if a["date"] >= sm.index[max(len(sm) - RECENT, 0)]:
                d = a["date"].date().isoformat()
                flow.setdefault(d, {"加仓": [], "减仓": []})[
                    "加仓" if a["kind"] == "buy" else "减仓"].append(cfg["name"])

    buys = [x for x in rows if x["action"] == "加仓"]
    sells = [x for x in rows if x["action"] == "减仓"]
    holds = [x for x in rows if x["action"] == "维持"]
    n_empty = sum(1 for x in rows if x["state"] <= 1e-9)
    stale = [x for x in rows if x["fresh"].startswith(("⚠️", "❌"))]
    dates_desc = sorted(flow, reverse=True)
    as_of = maxdate or (rows[0]["date"] if rows else "")

    L = [
        f"# 波动资产 · v11-gate（支撑位闸门）· 今日操作（{as_of}）",
        f"> 池 = 主策略七腿（{' / '.join(sg.base.LEGS[k]['name'] for k in sg.LEGS)}），"
        "口径 = 场外**复权净值**（含分红再投 / 汇率 / 跟踪误差）。",
        f"> 判定链（**收盘后算、次日收盘成交**）：①趋势 = `close > MA{sg.MA_WIN}` 且 "
        f"MA{sg.MA_WIN} 近 {sg.SLOPE_N} 日上行（**月频**：月末判定、次月整月沿用）→ 不成立则观察仓 0.2；"
        f"②趋势成立且距支撑 ≤{sg.NEAR:.0%}（贴线）→ 半仓 0.5，否则满带 1.0；"
        f"③`close < support`（跌破已确认前低）→ 空仓 0 + 冷却 {sg.COOL} 日，"
        f"要等 close 重新站上「支撑 + {sg.NEAR:.0%}」才回补。",
        f"> 限速：非风控换档至少隔 {sg.ACTION_GAP} 个交易日（滞回 {sg.HYST:.0%} 防在贴线边界抖动）；"
        "**破位清仓不受限速** —— 风控优先。",
        "> 档位是「占本腿目标带的比例」（0.2/0.5/1.0），**不是 pp**：本变种是仓位带上的闸门，"
        "主策略定带位、本变种定带内露出多少。",
        "> ⚠ **完整版（含支撑层）已被回测证伪**（七腿年化 +9.01% vs 纯趋势 +13.02%，换手 61 次/年，"
        "见 README §2）—— 下表左列是「规则怎么说」，右列「纯趋势档」才是唯一站得住的配置；"
        "本变种**不上线**，此表用于复盘与规则核对。",
        f"> 空仓部分按货基 {tg.CASH_ANNUAL:.2%} 计息；成交按 **T+1 收盘净值**。"
        "**QDII 腿（纳指 / 标普 / 恒生科技）净值本身滞后 1~2 日，信号随之延后。**",
        f"> 数据：{len(rows)} 只 · 最新 {len(rows) - len(stale)} 只 · 滞后/失败 {len(stale)} 只。",
        "> 机械规则输出，**非投资建议**。",
        "",
        "## 今日操作（明日成交）",
        "",
        "| 加仓 | 减仓 | 其余维持 |",
        "|---|---|---|",
        f"| {('、'.join(x['name'] + '→' + x['level'] for x in buys) or '—')} | "
        f"{('、'.join(x['name'] + '→' + x['level'] for x in sells) or '—')} | "
        f"{('、'.join(x['name'] for x in holds) or '—')} |",
        "",
        (f"> 今天 {len(rows)} 条腿里 **{n_empty} 条空仓** —— 「同时破位」是普跌的常态（一次回调就同时击穿各自的前低），"
         "这也正是「破位清仓」被证伪的直观原因：同时清仓、再同时错过反弹第一段。"
         if n_empty >= 4 else f"> 今天 {len(rows)} 条腿里 {n_empty} 条空仓。"),
        "",
        "**逐腿一句话（档位 · 成因 · 距支撑 · 距 MA200）**",
        "",
    ]
    L += [f"- {one_line(x)}" for x in rows]

    L += [
        "",
        f"## 七腿档位（带内占比 = 该腿露出多少目标带）",
        "",
        "| 腿 | 执行代码 | 净值日 | 复权净值 | "
        f"MA{sg.MA_WIN} | 斜率 | 距MA{sg.MA_WIN} | 趋势 | 支撑 | 距支撑 | 冷却 | "
        "档位（完整） | 档位（纯趋势） | 占带 | pp | 今日动作 | 还缺什么 | 数据 |",
        "|---|---|---|---:|---:|:--:|---:|:--:|---:|---:|---:|---|---|---:|---:|---|---|---|",
    ]
    for x in rows:
        sup_txt = f"{x['support']:.4f}" if np.isfinite(x["support"]) else "未成形"
        dist_txt = f"{x['dist']:+.1%}" if np.isfinite(x["dist"]) else "—"
        cool_txt = f"{int(x['cool'])}" if x["cool"] > 0 else "—"
        L.append(
            f"| {x['name']} | `{x['exec']}` | {x['date']} | {x['nav']:.4f} | "
            f"{x['ma']:.4f} | {'↑' if x['slope_up'] else '↓'} | {x['dev']:+.1%} | "
            f"{'成立' if x['trend'] else '不成立'} | {sup_txt} | {dist_txt} | {cool_txt} | "
            f"**{x['level']}** | {level_name(x['state_trend'])} | "
            f"{x['state']:.0%} | {x['pos_pp']:.2f} | {x['action']} | {x['lack']} | {x['fresh']} |")

    L += [
        "",
        "> 「档位（完整）」= 趋势 + 支撑层（贴线减半 / 破位清仓）；「档位（纯趋势）」= 只用 MA200 月频闸门。"
        "两列相同说明今天支撑层没说话；不同则是支撑层在扣仓 —— 而这一层在七腿上净负，"
        "所以差异列的作用是**提醒你它在扣**，不是要你听它。",
        "> 「还缺什么」按优先级给：空仓看回补条件（支撑 +%），观察仓看趋势两个条件，半仓看贴线带退出。",
        "",
        f"## 近期动作流水（最近 {RECENT} 个交易日 · 仅列有动作的日期 · 日期 = 决策日，次日成交）",
        "",
        "| 日期 | 加仓 | 减仓 |",
        "|---|---|---|",
    ]
    if dates_desc:
        for d in dates_desc:
            L.append(f"| {d} | {('、'.join(flow[d]['加仓']) or '—')} | "
                     f"{('、'.join(flow[d]['减仓']) or '—')} |")
    else:
        L.append(f"| — | 最近 {RECENT} 个交易日内七腿无动作 | — |")

    L += [
        "",
        "## 提醒",
        "",
        "- **本变种不上线**：`pipeline/strategies.json` 不注册、`run_daily_all` 不含它、"
        "不出看板卡片。这份表是「规则怎么说」的复盘，不是操作指令。",
        "- **支撑层是负贡献，不是「信号太弱」**：破位清仓单独测 -5.82pp、回撤反而更深"
        "（卖在低点，再等价格站回支撑上方才回补，正好错过反弹第一段）—— 详见 `../../backtest/_v11_gate_bt.md` §7。",
        "- **趋势层与 v5 等价但更省交易**：MA200 月频判定 + "
        f"{sg.ACTION_GAP} 日换档间隔把换手压到 v5 的 45~60%，年化在约 1pp 内互有胜负。"
        "要把它当 v5 的慢速对照用，请直接看「档位（纯趋势）」列。",
        "- 数据滞后：QDII（纳指 / 标普 / 恒生科技）净值 T+1~T+2 才出，国内腿 21:00 后基本可得；"
        "表里「数据」列标了每只的刷新状态。",
        "",
        "> 机械规则输出，**非投资建议**。",
    ]

    with open(OUT_MD, "w", encoding="utf-8") as f:
        f.write("\n".join(L) + "\n")
    with open(OUT_JSON, "w", encoding="utf-8") as f:
        json.dump({
            "strategy": "v11-gate 支撑位闸门（不上线 · 复盘用）",
            "as_of": as_of, "pool": list(sg.LEGS),
            "rule": {"ma": sg.MA_WIN, "slope_n": sg.SLOPE_N, "trend_freq": sg.TREND_FREQ,
                     "swing_n": sg.SWING_N, "sup_mode": sg.SUP_MODE,
                     "use_support": sg.USE_SUPPORT, "clear_on_break": sg.CLEAR_ON_BREAK,
                     "near": sg.NEAR, "hyst": sg.HYST, "cool": sg.COOL,
                     "action_gap": sg.ACTION_GAP,
                     "levels": {"full": sg.FULL, "half": sg.HALF, "observe": sg.OBSERVE},
                     "stop_use": sg.STOP_USE, "trade_at": "T+1 收盘净值",
                     "cash_annual": tg.CASH_ANNUAL},
            "summary": {"n": len(rows), "buy": len(buys), "sell": len(sells),
                        "stale": len(stale), "fail": len(fails),
                        "levels": {name: sum(1 for x in rows if abs(x["state"] - lvl) < 1e-9)
                                   for lvl, name in sg.LEVEL_TXT.items()}},
            "legs": rows,
            "recent_flow": [{"date": d, "buy": flow[d]["加仓"], "sell": flow[d]["减仓"]}
                            for d in dates_desc],
        }, f, ensure_ascii=False, indent=1, default=str)

    print("报告:", OUT_MD)
    print("JSON:", OUT_JSON)
    print(f"净值日 {as_of} | 加仓 {[(x['leg'], x['level']) for x in buys]} · "
          f"减仓 {[(x['leg'], x['level']) for x in sells]}")
    for x in rows:
        print(f"  {x['name']:8s} {x['level']:4s}（纯趋势 {level_name(x['state_trend']):4s}）"
              f" 距支撑 {x['dist']:+.1%} 距MA{sg.MA_WIN} {x['dev']:+.1%} "
              f"{x['action']} · {x['why']}")
    if fails:
        print("刷新失败:", fails)


if __name__ == "__main__":
    main()
