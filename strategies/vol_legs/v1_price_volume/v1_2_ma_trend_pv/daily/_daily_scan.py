# -*- coding: utf-8 -*-
"""波动资产 · V1 量价家族 · 子策略 V1-2（20/60/250 量价趋势跟随）· 每日操作标的。

流程：
1. **先刷新场内 ETF 日线**（`ohlcv.refresh()`，七只；失败沿用本地并标注）；
2. 逐腿跑状态机，取**最新一根**的仓位与动作（建仓 / 减半 / 恢复 / 清仓 / 持有 / 空仓）+ 触发原因；
3. 汇总今日动作、扣抵值（20/60/250 各自「N 天前被扣掉的价格」）、ADX 休眠状态、量比。

口径提醒：本子策略用**场内 ETF 量价**（场外净值没有成交量）；执行建议走场内 ETF，
与 V1-1（场外复权净值 + MA20 穿越）不是同一口径，两者不要混算。

产物（相对本文件）：daily/_v1_2_daily.md / _v1_2_daily.json。
机械规则输出，非投资建议。
"""
import json
import os
import sys

try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:  # noqa
    pass

_HERE = os.path.dirname(os.path.abspath(__file__))          # .../v1_2_ma_trend_pv/daily
_V2 = os.path.dirname(_HERE)                                # .../v1_2_ma_trend_pv
sys.path.insert(0, _V2)

import ma_trend_vol as mtv  # noqa: E402

OUT_MD = os.path.join(_HERE, "_v1_2_daily.md")
OUT_JSON = os.path.join(_HERE, "_v1_2_daily.json")
RECENT = 20


def classify(act):
    """把状态机动作字符串归到「建仓 / 加仓 / 减仓 / 清仓 / 持有 / 空仓」。"""
    a = str(act)
    if a.startswith("建仓"):
        return "建仓"
    if a.startswith("恢复"):
        return "加仓"
    if a.startswith("减半"):
        return "减仓"
    if a.startswith("清仓"):
        return "清仓"
    if a.startswith("持有"):
        return "持有"
    return "空仓"


def collect():
    latest, fails = mtv.ohlcv.refresh()
    rows, flow = [], {}
    for leg in mtv.LEGS:
        cfg = mtv.rule.LEGS[leg]
        ind = mtv.load(leg, refresh_=False, fails=None)
        sim = mtv.simulate(ind)
        st = mtv.state(sim)
        act = str(st["action"])
        rows.append(dict(
            leg=leg, name=cfg["name"], role=cfg["role"], band=float(cfg["band"]),
            code=mtv.ohlcv.CODES[leg], code_name=mtv.ohlcv.NAMES[leg],
            exec_code=cfg["exec"], exec_name=cfg["exec_name"],
            date=st["date"], close=st["close"],
            ma20=st["ma20"], ma60=st["ma60"], ma250=st["ma250"],
            d20=st["d20"], d60=st["d60"], d250=st["d250"],
            up20=st["up20"], up60=st["up60"], up250=st["up250"],
            dist20=st["close"] / st["ma20"] - 1.0,
            dist60=st["close"] / st["ma60"] - 1.0,
            dist250=st["close"] / st["ma250"] - 1.0 if st["ma250"] else None,
            atr_pct=st["atr_pct"], adx=st["adx"], rvol=st["rvol"], regime=st["regime"],
            w=st["w"], pos=st["pos"], stop=st["stop"], action=act,
            bucket=classify(act), reason=st["reason"],
            fresh=("❌刷新失败" if mtv.ohlcv.CODES[leg] in fails else "✅最新"),
        ))
        # 最近 RECENT 根里有切换的日期（比 20 个自然日更贴近「交易流水」）
        tail = sim.tail(RECENT)
        for ts, row in tail.iterrows():
            b = classify(row["act"])
            if b in ("建仓", "加仓", "减仓", "清仓"):
                d = ts.date().isoformat()
                flow.setdefault(d, []).append((leg, cfg["name"], b, str(row["act"])))

    maxdate = max((r["date"] for r in rows), default="")
    return rows, flow, maxdate, fails


def _row(x):
    return (f"| {x['name']} | `{x['code']}` | {x['date']} | {x['close']:.3f} | "
            f"{x['ma20']:.3f} / {x['ma60']:.3f} / {x['ma250']:.3f} | {x['dist20']:+.2%} | "
            f"**{x['w']:.0%}** | {x['action']} | {x['reason']} |")


def main():
    rows, flow, maxdate, fails = collect()
    buckets = {}
    for x in rows:
        buckets.setdefault(x["bucket"], []).append(x)
    stale = [x for x in rows if x["fresh"].startswith("❌")]

    L = [
        f"# 波动资产 · V1-2（20/60/250 量价趋势跟随）· 每日操作（{maxdate or '无数据'}）",
        f"> 池 = 主策略七腿（{' / '.join(mtv.rule.LEGS[k]['name'] for k in mtv.LEGS)}），"
        "信号 = **场内 ETF 前复权日线 + 成交量**（量价规则必须有量，场外净值没有成交量）。",
        f"> 规则：年线定牛熊（价在 MA{mtv.SMA_SLOW} 上且扣抵向上）· 20 线找切入 · 60 线控生死 · "
        f"放量（量比 ≥{mtv.VOL_MULT}）确认 · ADX ≥{mtv.ADX_OFF:.0f} 才开新仓。",
        f"> 仓位 = 单笔风险 {mtv.RISK_PCT:.0%} ÷ 止损幅度（上限 100%），初始止损 3%~"
        f"{mtv.MAX_STOP:.0%}；跌破 20 线且威胁 60 线减半；放量/有效破 60 线清仓。",
        f"> 七腿 {len(rows)} 只 · 最新 {len(rows) - len(stale)} 只 · 刷新失败 {len(stale)} 只。"
        "**QDII 代理腿（513100 / 513500）信号里掺汇率。**",
        "> 机械规则输出，**非投资建议**。",
        "",
        "## 今日动作汇总",
        "",
        "| 建仓 | 加仓 | 减仓 | 清仓 | 持有 | 空仓 |",
        "|---|---|---|---|---|---|",
        f"| {len(buckets.get('建仓', []))} | {len(buckets.get('加仓', []))} | "
        f"{len(buckets.get('减仓', []))} | {len(buckets.get('清仓', []))} | "
        f"{len(buckets.get('持有', []))} | {len(buckets.get('空仓', []))} |",
        "",
        "## 需要动手的腿",
        "",
    ]
    acts = [x for x in rows if x["bucket"] in ("建仓", "加仓", "减仓", "清仓")]
    if acts:
        L += ["| 腿 | 信号码 | 数据日 | 收盘 | MA20/60/250 | 距MA20 | 仓位 | 动作 | 说明 |",
              "|---|---|---|---:|---|---:|---:|---|---|"]
        L += [_row(x) for x in sorted(acts, key=lambda z: z["bucket"])]
    else:
        L.append("（今日无建仓/加仓/减仓/清仓动作）")

    L += ["", "## 七腿明细", "",
          "| 腿 | 信号码 | 数据日 | 收盘 | MA20/60/250 | 距MA20 | 仓位 | 动作 | 说明 |",
          "|---|---|---|---:|---|---:|---:|---|---|"]
    L += [_row(x) for x in sorted(rows, key=lambda z: mtv.LEGS.index(z["leg"]))]

    L += ["", "## 扣抵表（「N 天前被扣掉的价格」，用于预判均线拐头）", "",
          "> 判据：`最新收盘 > N 天前的收盘` ⟺ `MA_N` 今日上行（数学等价）。"
          "扣低值（现价高于被扣价）→ 均线继续抬升；扣高值 → 均线被动下弯。", "",
          "| 腿 | 收盘 | 20日扣抵 | 20线 | 60日扣抵 | 60线 | 250日扣抵 | 250线 | 20线 | 60线 | 250线 |",
          "|---|---:|---:|---:|---:|---:|---:|---:|---|---|---|"]
    for x in sorted(rows, key=lambda z: mtv.LEGS.index(z["leg"])):
        flag = lambda up: "↑" if up else "↓"      # noqa: E731
        L.append(f"| {x['name']} | {x['close']:.3f} | {x['d20']:.3f} | {x['ma20']:.3f} | "
                 f"{x['d60']:.3f} | {x['ma60']:.3f} | {x['d250']:.3f} | {x['ma250']:.3f} | "
                 f"{flag(x['up20'])} | {flag(x['up60'])} | {flag(x['up250'])} |")

    L += ["", "## 情绪与趋势读数", "",
          "| 腿 | 市况 | ADX | 量比 | ATR% | 距60线 | 距年线 | 止损价 |",
          "|---|---|---:|---:|---:|---:|---:|---:|"]
    for x in sorted(rows, key=lambda z: mtv.LEGS.index(z["leg"])):
        stop = f"{x['stop']:.3f}" if x["stop"] else "—"
        d250 = f"{x['dist250']:+.1%}" if x["dist250"] is not None else "—"
        L.append(f"| {x['name']} | {x['regime']} | {x['adx']:.0f} | {x['rvol']:.2f} | "
                 f"{x['atr_pct']:.2%} | {x['dist60']:+.1%} | {d250} | {stop} |")

    L += ["", f"## 最近 {RECENT} 个交易日的动作流水", ""]
    if flow:
        L += ["| 日期 | 腿 | 动作 | 状态机输出 |", "|---|---|---|---|"]
        for d in sorted(flow, reverse=True):
            for leg, name, b, raw in flow[d]:
                L.append(f"| {d} | {name} | {b} | {raw} |")
    else:
        L.append(f"（最近 {RECENT} 个交易日内七腿无动作）")

    L += [
        "",
        "## 提醒",
        "",
        "- 本子策略只管**单腿进出时点与仓位**；组合层仓位带仍由主策略（`vol_legs`）管，两者可叠加不可替代。",
        "- **信号源与执行源**：信号用场内 ETF 量价，执行也建议走场内 ETF（同源、成本约 7.5bp 单边）；"
        "若走场外联接，净值没有成交量、且申赎成本高一个量级。",
        "- QDII 代理（513100 / 513500）的溢价波动会影响信号，极端溢价时人工复核。",
        "",
        "> 机械规则输出，**非投资建议**。",
    ]

    with open(OUT_MD, "w", encoding="utf-8") as f:
        f.write("\n".join(L))
    with open(OUT_JSON, "w", encoding="utf-8") as f:
        json.dump({
            "strategy": mtv.LABEL, "as_of": maxdate, "pool": list(mtv.LEGS),
            "rule": {
                "ma": [mtv.SMA_FAST, mtv.SMA_MID, mtv.SMA_SLOW],
                "vol_mult": mtv.VOL_MULT, "adx_on": mtv.ADX_ON, "adx_off": mtv.ADX_OFF,
                "risk_pct": mtv.RISK_PCT, "stop_atr": mtv.STOP_ATR,
                "min_stop": mtv.MIN_STOP, "max_stop": mtv.MAX_STOP,
                "trade_at": "T+1 收盘", "cash_annual": mtv.CASH_ANNUAL,
                "mode": "场内 ETF 量价（前复权日线 + 成交量）",
            },
            "summary": {b: sum(1 for x in rows if x["bucket"] == b)
                        for b in ("建仓", "加仓", "减仓", "清仓", "持有", "空仓")},
            "legs": rows,
            "recent_flow": [{"date": d, "items": [dict(leg=l, name=n, action=lb, raw=rw)
                                                 for l, n, lb, rw in flow[d]]}
                            for d in sorted(flow, reverse=True)],
        }, f, ensure_ascii=False, indent=1)

    print("报告:", OUT_MD)
    print("JSON:", OUT_JSON)
    print(f"最新数据日 {maxdate} | " + " · ".join(
        f"{b} {sum(1 for x in rows if x['bucket'] == b)}"
        for b in ("建仓", "加仓", "减仓", "清仓", "持有", "空仓")))
    for x in acts:
        print(f"  [{x['bucket']}] {x['name']} 仓位 {x['w']:.0%} · {x['reason']}")
    if fails:
        print("刷新失败:", fails)


if __name__ == "__main__":
    main()
