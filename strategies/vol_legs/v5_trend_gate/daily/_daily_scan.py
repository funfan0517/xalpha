# -*- coding: utf-8 -*-
"""波动资产 · V5 变种（趋势四象限闸门）· 每日仓位档位。

流程：
1. **先刷新场外复权净值**（`data.refresh_otc()`，七腿 + 桥接代理），失败沿用本地并标注；
   加 `--no-refresh` 可跳过联网（用本地缓存，报告会标「未刷新」）；
2. 对**七腿**（`rule.LEG_ORDER`）逐腿算两个数：`close` 与 `MA250` 的关系、`MA250` 近 20 日斜率，
   得到四象限与目标档位（满带 1.0 / 半仓 0.5 / 观察 0.2）；
3. 默认**只对没有现金流锚的三条腿**（科创50 / 恒生科技 / 黄金）给动作，宽基四条腿维持满带 ——
   这是规则的一部分（宽基的估值锚已隐含均值回归，再叠趋势过滤会互相抵消）；
4. 输出：今日调仓（明日成交）+ 各腿档位表 + 空仓/减仓腿「还缺什么」+ 最近 120 个交易日动作流水。

成交提醒：T 日盘后算出的信号，**T+1 15:00 前提交**才按 T+1 净值成交（QDII 净值本身 T+1~T+2 才出）。

产物（相对本文件）：daily/_v5_trend_gate_daily.md / _v5_trend_gate_daily.json。
机械规则输出，非投资建议。
"""
import json
import os
import sys

try:
    sys.stdout.reconfigure(encoding="utf-8")  # type: ignore[attr-defined]
except Exception:  # noqa: BLE001
    pass

_HERE = os.path.dirname(os.path.abspath(__file__))
_V5 = os.path.dirname(_HERE)
sys.path.insert(0, _V5)

import trend_gate as tg  # noqa: E402

OUT_MD = os.path.join(_HERE, "_v5_trend_gate_daily.md")
OUT_JSON = os.path.join(_HERE, "_v5_trend_gate_daily.json")
RECENT = 120

LEVEL = tg.LEVEL_TXT


def level_name(x):
    return LEVEL.get(round(float(x), 6), f"{float(x):.0%}")


def freshness(leg, date, fails, refreshed):
    cfg = tg.rule.LEGS[leg]
    code = cfg.get("otc")
    if code in fails:
        return f"❌刷新失败({code})"
    if not refreshed:
        return "·未刷新(本地缓存)"
    exp = tg.vdata._expected_otc_latest(cfg.get("otc_qdii", False))
    return "✅最新" if date >= exp else f"⚠️滞后(应至 {exp})"


def lack(x):
    """「还缺什么才给加仓许可」—— 只对需要加仓的腿有意义。"""
    if x["state"] >= tg.FULL - 1e-9:
        return "已满带"
    gaps = []
    if not x["above"]:
        gaps.append(f"close 需站上年线({x['ma']:.4f}, 现距 {x['dev']:+.1%})")
    if not x["slope_up"]:
        gaps.append("年线需转为上行(现 20 日斜率向下)")
    if x["quad"] == tg.QUADS[2]:
        gaps.append(f"高位回调第 {x['below_days']} 日")
    if x["quad"] == tg.QUADS[3]:
        gaps.append(f"下跌趋势第 {x['below_days']} 日")
    return " + ".join(gaps) if gaps else "两条件已齐, 等下一次调仓窗口"


def main():
    refreshed = "--no-refresh" not in sys.argv
    fails = {}
    if refreshed:
        latest, fails = tg.vdata.refresh_otc()
        maxdate = max(latest.values()) if latest else ""
    else:
        maxdate = ""

    rows, flow = [], {}
    for leg in tg.LEGS:
        cfg = tg.rule.LEGS[leg]
        px = tg.load(leg)
        sm = tg.state_machine(px, leg)
        r = sm.iloc[-1]
        if not bool(r.get("ready", False)):
            print(f"[跳过] {leg}: 年线/斜率未成形")
            continue
        close = float(r["close"])
        state = float(r["state"])
        band = float(cfg["band"])
        quad = str(r["quad"])
        item = dict(
            leg=leg, name=cfg["name"], role=cfg["role"], band=band,
            exec=cfg["exec"], exec_name=cfg["exec_name"],
            date=px.index[-1].date().isoformat(), nav=close,
            ma=float(r["ma"]), slope=float(r["slope"]), slope_up=bool(r["slope"] > 0),
            above=bool(r["close"] >= r["ma"]), dev=close / float(r["ma"]) - 1.0,
            quad=quad, below_days=int(r["below_days"]),
            dd_hi=float(r["dd_hi"]),
            gated=bool(tg.gated(leg)),
            state=state, level=level_name(state),
            pos_pp=state * band, target=(None if r["target"] != r["target"]
                                         else float(r["target"])),
            decide=str(r["decide"]), done=str(r["done"]),
            fresh=freshness(leg, px.index[-1].date().isoformat(), fails, refreshed),
        )
        item["action"] = {"buy": "加仓", "sell": "减仓"}.get(item["decide"],
                                                         "维持" if item["gated"] else "不套闸门")
        item["lack"] = lack(item) if item["gated"] else "宽基不套闸门(估值锚已含均值回归)"
        # 档位显示：今日成交后的档位；有决策时补上「→ 明日档位」
        nxt = item["target"] if item["decide"] else state
        item["next_level"] = level_name(nxt)
        item["level_txt"] = level_name(state) + (
            f" → {level_name(nxt)}" if item["decide"] else "")
        rows.append(item)

        dec = sm["decide"].to_numpy()
        for i in range(max(1, len(dec) - RECENT), len(dec)):
            if dec[i] in ("buy", "sell"):
                d = px.index[i].date().isoformat()
                flow.setdefault(d, {"加仓": [], "减仓": []})[
                    "加仓" if dec[i] == "buy" else "减仓"].append(cfg["name"])

    buys = [x for x in rows if x["decide"] == "buy"]
    sells = [x for x in rows if x["decide"] == "sell"]
    stale = [x for x in rows if x["fresh"].startswith(("⚠️", "❌"))]
    dates_desc = sorted(flow, reverse=True)
    quad_cnt = {q: sum(1 for x in rows if x["quad"] == q) for q in tg.QUADS}

    L = [
        f"# 波动资产 · V5（趋势四象限闸门）· 每日档位（{maxdate or rows[0]['date'] if rows else '无数据'}）",
        f"> 池 = 主策略七腿（{' / '.join(tg.rule.LEGS[k]['name'] for k in tg.LEGS)}），"
        "口径 = 场外**复权净值**（含分红再投 / 汇率 / 跟踪误差）。",
        f"> 判断趋势只看两个数：`close` 在 **MA{tg.MA_WIN}** 上/下、MA{tg.MA_WIN} 近 "
        f"**{tg.SLOPE_N} 日**朝上/朝下；两个都朝上才是上涨趋势，其余三格都不给加仓许可。",
        "> **但不是每腿都该用这两个数**：中证A500 的读数建议换成双均线 50/150，"
        "其余五腿维持本口径 —— 逐腿依据见板块 2 的「各腿趋势口径」表。",
        f"> 档位：上涨趋势 → 满带；反弹 → 只持有；高位回调 → 降一档（半仓）；下跌趋势 → "
        f"满 {tg.REDUCE_DAYS} 日减半、满 {tg.CLEAR_DAYS} 日清至观察仓。"
        f"同一腿两次调仓间隔 ≥{tg.ACTION_GAP} 个交易日（频率帽）。",
        f"> 作用域：**全部七腿**均套闸门（{' / '.join(tg.rule.LEGS[k]['name'] for k in tg.LEGS)}；"
        "2026-11-03 起默认七腿全套，`GATE_ALL=True`）。宽基（中证A500 / 沪深300 / 纳指100 / 标普500）"
        "套上趋势过滤会与估值锚的均值回归互相抵消 —— 实测让渡约 2.4pp 年化、回撤无改善；要回到"
        "「只套无现金流锚三腿」的老口径，把 `trend_gate.GATE_ALL` 设 False。",
        f"> 空仓部分按货基 {tg.CASH_ANNUAL:.2%} 计息；成交按 **T+1 净值**（T 日盘后算，"
        "T+1 15:00 前提交）。",
        f"> 七腿 {len(rows)} 只：最新 {len(rows) - len(stale)} 只 · 滞后/失败 {len(stale)} 只。"
        "**QDII 腿（纳指 / 标普 / 恒生科技）净值本身滞后 1~2 日，信号随之延后。**",
        "> 机械规则输出，**非投资建议**。",
        "",
        "## 今日调仓（明日成交）",
        "",
        "| 加仓 | 减仓 |",
        "|---|---|",
        f"| {('、'.join(x['name'] for x in buys) or '—')} | "
        f"{('、'.join(x['name'] for x in sells) or '—')} |",
        "",
        "## 七腿档位（带内占比 = 该腿露出多少目标带）",
        "",
        "| 腿 | 执行代码 | 闸门 | 净值日 | 复权净值 | 年线 | 年线斜率 | 象限 | 连续在年线下 | "
        "距年线 | 档位（今日 → 明日） | 占带 | pp | 今日动作 | 还缺什么 | 数据 |",
        "|---|---|---|---|---|---|---|---|---:|---:|---|---:|---:|---|---|---|",
    ]
    for x in rows:
        L.append(
            f"| {x['name']} | `{x['exec']}` | {'是' if x['gated'] else '否'} | {x['date']} | "
            f"{x['nav']:.4f} | {x['ma']:.4f} | {'↑' if x['slope_up'] else '↓'} | {x['quad']} | "
            f"{x['below_days']} | {x['dev']:+.1%} | **{x['level_txt']}** | {x['state']:.0%} | "
            f"{x['pos_pp']:.2f} | {x['action']} | {x['lack']} | {x['fresh']} |")

    L += [
        "",
        "**各腿趋势口径（判趋势该用哪种读数 · 历史回测结论，不是每日信号）**",
        "",
        "| 腿 | 判趋势用 | 该腿实测（年化 / 回撤 / 夏普 · 调仓/年） | 为什么 |",
        "|---|---|---|---|",
    ]
    for leg in tg.LEGS:
        rd = tg.TREND_READ[leg]
        L.append(f"| {tg.rule.LEGS[leg]['name']} | {rd['label']} | {rd['stats']} | {rd['why']} |")
    L += [
        "",
        "> 一句话：**七腿里中证A500 值得换快口径（沪深300 沿用）（双均线 50/150）**，其余七腿保留年线 250+20 —— "
        "「快」能不能换成收益取决于标的的噪声结构（A 股宽基的趋势段更长、美股慢牛更平滑、"
        "科创50 / 恒生科技的噪声最大），不是普适优点。宽基三腿当前不套闸门，"
        "口径只影响「读数」；要真的切换闸门口径，需改 `trend_gate.KIND / FAST_WIN / SLOW_WIN` 并重跑回测。",
    ]
    L += [
        "",
        f"> 象限分布：{' · '.join(f'{q} {quad_cnt[q]} 只' for q in tg.QUADS)}。",
        "> 「反弹」格（价格回到年线上、年线还在下行）是唯一不给加仓许可的均线上方格："
        "回测里这一格未来 60 日期望收益为负、胜率最低，所以「站上年线」不等于「趋势反转」。",
        "",
        f"## 近期动作流水（最近 {RECENT} 个交易日 · 仅列有动作的日期）",
        "",
        "| 日期 | 加仓 | 减仓 |",
        "|---|---|---|",
    ]
    if dates_desc:
        for d in dates_desc:
            b = "、".join(flow[d]["加仓"]) or "—"
            s = "、".join(flow[d]["减仓"]) or "—"
            L.append(f"| {d} | {b} | {s} |")
    else:
        L.append(f"| — | 最近 {RECENT} 个交易日内七腿无动作 | — |")

    L += [
        "",
        "## 提醒",
        "",
        "- 本变种只管**逐腿露出多少目标带**，主策略管**组合层仓位带**（阶梯 / 年线闸门 / 再平衡）"
        "—— 两者可叠加，但不要互相替代：主策略定带位，本变种定带内档位。",
        "- **滞后是设计特性**：年线确认「进入下跌趋势」时，价格通常已从高点跌掉两位数"
        "（回测实测中位 −13.5%）。它的目标是避开以年为单位的阴跌段，不是躲三个月的回调。",
        "- **默认已套全部七腿**（2026-11-03 起 `GATE_ALL=True`）：回测里给七腿全套闸门，"
        "年化让渡约 2.4pp、回撤没有改善（与用户给的 510300 证据同向）—— 收益效率上「只套三腿」更优，"
        "当前七腿默认是按需求改的。回到三腿版：把 `trend_gate.GATE_ALL` 设 False 再跑。",
        "- 档位变化很少（七腿合计 4 年约 25 次），长期「满带」是常态，不是脚本坏了。",
        "",
        "> 机械规则输出，**非投资建议**。",
    ]

    with open(OUT_MD, "w", encoding="utf-8") as f:
        f.write("\n".join(L))
    with open(OUT_JSON, "w", encoding="utf-8") as f:
        json.dump({
            "strategy": tg.LABEL, "as_of": maxdate or (rows[0]["date"] if rows else ""),
            "pool": list(tg.LEGS), "gate_all": tg.GATE_ALL,
            "gate_legs_fallback": list(tg.GATE_LEGS),
            "gate_effective": list(tg.LEGS) if tg.GATE_ALL else list(tg.GATE_LEGS),
            "rule": {"ma": tg.MA_WIN, "slope_n": tg.SLOPE_N,
                     "reduce_days": tg.REDUCE_DAYS, "clear_days": tg.CLEAR_DAYS,
                     "action_gap": tg.ACTION_GAP,
                     "levels": {"full": tg.FULL, "half": tg.HALF, "observe": tg.OBSERVE},
                     "trade_at": "T+1 净值", "cash_annual": tg.CASH_ANNUAL},
            "summary": {"n": len(rows), "buy": len(buys), "sell": len(sells),
                        "stale": len(stale), "fail": len(fails),
                        "quads": quad_cnt},
            "trend_read": {k: dict(label=v["label"], stats=v["stats"], why=v["why"])
                           for k, v in tg.TREND_READ.items()},
            "legs": rows,
            "recent_flow": [{"date": d, "buy": flow[d]["加仓"], "sell": flow[d]["减仓"]}
                            for d in dates_desc],
        }, f, ensure_ascii=False, indent=1)

    print("报告:", OUT_MD)
    print("JSON:", OUT_JSON)
    print(f"净值日 {maxdate or (rows[0]['date'] if rows else '—')} | 加仓 "
          f"{[(x['leg'], x['name']) for x in buys]} · 减仓 {[(x['leg'], x['name']) for x in sells]}")
    for x in rows:
        print(f"  {x['name']:8s} 闸门={'是' if x['gated'] else '否'} {x['quad']:5s} "
              f"档位={x['level']:3s}({x['state']:.0%}) {x['action']} · {x['lack']}")
    if fails:
        print("刷新失败:", fails)


if __name__ == "__main__":
    main()
