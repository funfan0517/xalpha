# -*- coding: utf-8 -*-
"""波动资产 · V3 变种（MACD + 布林通道 · 自适应窗口）· 每日买卖标的。

流程：
1. **先刷新场外复权净值**（`data.refresh_otc()`，六腿 + 桥接代理），失败沿用本地并标注；
2. 对**六腿**（`rule.LEG_ORDER` —— 与主策略同一池）逐腿跑 `macd_boll.signal()`：
   - **买入**（0→1）：`DIF>0` 且 `DIF>DEA`（零轴上金叉状态）且 布林带宽放大 且 收盘上穿上轨；
   - **卖出**（1→0）：`DIF` 下穿 `DEA`（死叉）或 收盘自上轨下穿中轨；
   - **持有** / **空仓**：未触发，维持原仓位（0/1）；
   - 布林窗口按 Kaufman 效率比 `ER(20)` 滞回切换：≥0.40 → 20（趋势态）、≤0.30 → 30（震荡态）；
3. 输出今日动作 + 最近 20 个交易日的信号流水，并标注 QDII 净值时滞。

成交提醒：T 日盘后算出的信号，**T+1 15:00 前提交**才按 T+1 净值成交（QDII 净值本身 T+1~T+2 才出）。

产物（相对本文件）：daily/_v3_macd_boll_daily.md / _v3_macd_boll_daily.json。
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
_V3 = os.path.dirname(_HERE)
_VL = os.path.dirname(_V3)
sys.path.insert(0, _V3)

import macd_boll  # noqa: E402

OUT_MD = os.path.join(_HERE, "_v3_macd_boll_daily.md")
OUT_JSON = os.path.join(_HERE, "_v3_macd_boll_daily.json")
RECENT = 20


def act(sig):
    cur, prev = int(sig.iloc[-1]), int(sig.iloc[-2])
    if cur == 1 and prev == 0:
        return "买入"
    if cur == 0 and prev == 1:
        return "卖出"
    return "持有" if cur == 1 else "空仓"


def window_state(er):
    """报告用的窗口状态（真实信号走 signal() 内部滞回，这里只标「当前 ER 落在哪一档」）。"""
    if er != er:                                    # NaN
        return "—"
    if er >= macd_boll.ER_HI:
        return f"趋势态 w{macd_boll.W_FAST}"
    if er <= macd_boll.ER_LO:
        return f"震荡态 w{macd_boll.W_SLOW}"
    return f"维持上档（中间带 ER {er:.2f}）"


def freshness(leg, date, fails):
    cfg = macd_boll.rule.LEGS[leg]
    code = cfg.get("otc")
    if code in fails:
        return f"❌刷新失败({code})"
    exp = macd_boll.vdata._expected_otc_latest(cfg.get("otc_qdii", False))
    if date >= exp:
        return "✅最新"
    return f"⚠️滞后(应至 {exp})"


def mk_table(items):
    if not items:
        return ["（无）"]
    out = ["| 腿 | 执行代码 | 净值日 | 复权净值 | DIF/DEA | ER（窗口） | 距触发位 | 当前仓位 | 数据 |",
           "|---|---|---|---|---|---|---|---|---|"]
    for x in sorted(items, key=lambda z: z["dist"], reverse=True):
        out.append(f"| {x['name']} | `{x['exec']}` | {x['date']} | {x['nav']:.4f} | "
                   f"{x['dif']:+.4f} / {x['dea']:+.4f} | {x['er']:.2f}（{x['window']}） | "
                   f"{x['dist_kind']} {x['dist']:+.2%} | {'持仓' if x['pos'] else '空仓'} | "
                   f"{x['fresh']} |")
    return out


def main():
    latest, fails = macd_boll.vdata.refresh_otc()
    maxdate = max(latest.values()) if latest else ""
    rows, flow = [], {}
    for leg in macd_boll.LEGS:
        cfg = macd_boll.rule.LEGS[leg]
        px = macd_boll.load(leg)
        sig = macd_boll.signal(px)
        dif, dea = macd_boll.macd(px)
        er = macd_boll.efficiency_ratio(px)
        up_f, mid_f, _ = macd_boll.bands(px, macd_boll.W_FAST)
        up_s, mid_s, _ = macd_boll.bands(px, macd_boll.W_SLOW)
        if up_f.iloc[-1] != up_f.iloc[-1]:
            print(f"[跳过] {leg}: 布林窗口未成形")
            continue
        er_last = float(er.iloc[-1]) if er.iloc[-1] == er.iloc[-1] else float("nan")
        fast_now = (er_last >= macd_boll.ER_HI) if er_last == er_last else True
        up = float((up_f if fast_now else up_s).iloc[-1])
        mid = float((mid_f if fast_now else mid_s).iloc[-1])
        pos = int(sig.iloc[-1])
        # 空仓时看「还要涨多少能上穿上轨」，持仓时看「跌多少跌破中轨」
        dist_kind = "距中轨" if pos else "距上轨"
        dist = float(px.iloc[-1] / (mid if pos else up) - 1.0)
        rows.append({
            "leg": leg, "name": cfg["name"], "role": cfg["role"], "band": float(cfg["band"]),
            "exec": cfg["exec"], "exec_name": cfg["exec_name"],
            "date": px.index[-1].date().isoformat(), "nav": float(px.iloc[-1]),
            "dif": float(dif.iloc[-1]), "dea": float(dea.iloc[-1]), "er": er_last,
            "window": window_state(er_last), "upper": up, "mid": mid,
            "dist_kind": dist_kind, "dist": dist,
            "pos": pos, "action": act(sig),
            "fresh": freshness(leg, px.index[-1].date().isoformat(), fails),
        })
        s = sig.to_numpy()
        for i in range(max(1, len(s) - RECENT), len(s)):
            if s[i] != s[i - 1]:
                d = px.index[i].date().isoformat()
                flow.setdefault(d, {"买入": [], "卖出": []})[
                    "买入" if s[i] == 1 else "卖出"].append((leg, cfg["name"]))

    buys = [x for x in rows if x["action"] == "买入"]
    sells = [x for x in rows if x["action"] == "卖出"]
    held = [x for x in rows if x["action"] == "持有"]
    empty = [x for x in rows if x["action"] == "空仓"]
    stale = [x for x in rows if x["fresh"].startswith(("⚠️", "❌"))]
    dates_desc = sorted(flow, reverse=True)

    L = [
        f"# 波动资产 · V3（MACD+BOLL 自适应窗口）· 每日买卖（{maxdate or '无数据'}）",
        f"> 池 = 主策略六腿（{' / '.join(macd_boll.rule.LEGS[k]['name'] for k in macd_boll.LEGS)}），"
        "口径 = 场外**复权净值**（含分红再投 / 汇率 / 跟踪误差）。",
        "> 规则：`DIF>0 且 DIF>DEA` + 布林**开口放大** + 收盘**上穿上轨** → 买入；"
        "`死叉` 或 收盘**跌破中轨** → 卖出；其余维持 0/1 仓位。",
        f"> 布林窗口按 ER({macd_boll.ER_N}) 滞回：≥{macd_boll.ER_HI:.2f} 用 {macd_boll.W_FAST}（趋势态）/ "
        f"≤{macd_boll.ER_LO:.2f} 用 {macd_boll.W_SLOW}（震荡态）/ 中间维持上档。",
        f"> 空仓按货基 {macd_boll.CASH_ANNUAL:.2%} 计息；成交按 **T+1 净值**（T 日盘后算，T+1 15:00 前提交）。",
        f"> 六腿 {len(rows)} 只：最新 {len(rows) - len(stale)} 只 · 滞后/失败 {len(stale)} 只。"
        "**QDII 腿（纳指 / 标普 / 恒生科技）净值本身滞后 1~2 日，信号随之延后。**",
        "> 机械规则输出，**非投资建议**。",
        "",
        "## 今日动作汇总",
        "",
        "| 买入 | 卖出 | 持有 | 空仓 |",
        "|---|---|---|---|",
        f"| {len(buys)} | {len(sells)} | {len(held)} | {len(empty)} |",
        "",
        "## 今日买入（零轴上金叉 + 开口 + 上穿上轨 · 0→1）",
        "",
    ] + mk_table(buys) + [
        "",
        "## 今日卖出（死叉 或 跌破中轨 · 1→0）",
        "",
    ] + mk_table(sells) + [
        "",
        f"## 近期信号流水（最近 {RECENT} 个交易日 · 仅列有动作的日期）",
        "",
        "| 日期 | 买入 | 卖出 |",
        "|---|---|---|",
    ]
    if dates_desc:
        for d in dates_desc:
            b = "、".join(f"{n} {c}" for c, n in flow[d]["买入"]) or "—"
            s = "、".join(f"{n} {c}" for c, n in flow[d]["卖出"]) or "—"
            L.append(f"| {d} | {b} | {s} |")
    else:
        L.append(f"| — | 最近 {RECENT} 个交易日内六腿无信号 | — |")

    L += ["", "## 六腿明细", "",
          "| 腿 | 角色 | 目标带 | 执行代码 | 净值日 | 复权净值 | DIF/DEA | ER（窗口） | 距触发位 | 当前仓位 | 动作 | 数据 |",
          "|---|---|---:|---|---|---|---|---|---|---|---|---|"]
    for x in sorted(rows, key=lambda z: macd_boll.LEGS.index(z["leg"])):
        L.append(f"| {x['name']} | {x['role']} | {x['band']:g}pp | `{x['exec']}` "
                 f"{x['exec_name']} | {x['date']} | {x['nav']:.4f} | "
                 f"{x['dif']:+.4f} / {x['dea']:+.4f} | {x['er']:.2f}（{x['window']}） | "
                 f"{x['dist_kind']} {x['dist']:+.2%} | {'持仓' if x['pos'] else '空仓'} | "
                 f"{x['action']} | {x['fresh']} |")

    L += ["", "## 非最新数据", ""] + (mk_table(stale) if stale else ["（全部为最新）"])
    L += [
        "",
        "## 提醒",
        "",
        "- 本变种只管**逐腿进出时点**，主策略管**组合层仓位带**（阶梯 / 年线闸门 / 再平衡）——"
        "两者可叠加，但不要互相替代。",
        "- 它是**突破追涨型**（突破了才上车），与主策略「跌了才加仓」的阶梯方向相反，"
        "**不可叠加在同一份额度上**。",
        "- 曝光天然很低（回测 12%~19%）：没有突破就一直空仓，收益对少数几笔高度敏感。",
        "- 年笔数 2~4/年（约 MA20 的 1/5），扣费后组合年化 5.55%→3.74%（-1.81pp，"
        "C 类销售服务费未计）。",
        "",
        "> 机械规则输出，**非投资建议**。",
    ]

    with open(OUT_MD, "w", encoding="utf-8") as f:
        f.write("\n".join(L))
    with open(OUT_JSON, "w", encoding="utf-8") as f:
        json.dump({
            "strategy": macd_boll.LABEL, "as_of": maxdate, "pool": list(macd_boll.LEGS),
            "rule": {"buy": "DIF>0且DIF>DEA+开口+上穿上轨", "sell": "死叉或跌破中轨",
                     "trade_at": "T+1 净值", "cash_annual": macd_boll.CASH_ANNUAL,
                     "window_fast": macd_boll.W_FAST, "window_slow": macd_boll.W_SLOW,
                     "er_hi": macd_boll.ER_HI, "er_lo": macd_boll.ER_LO},
            "summary": {"n": len(rows), "buy": len(buys), "sell": len(sells),
                        "hold": len(held), "empty": len(empty), "stale": len(stale),
                        "fail": len(fails)},
            "legs": rows,
            "recent_flow": [{"date": d, "buy": flow[d]["买入"], "sell": flow[d]["卖出"]}
                            for d in dates_desc],
        }, f, ensure_ascii=False, indent=1)

    print("报告:", OUT_MD)
    print("JSON:", OUT_JSON)
    print(f"最新净值日 {maxdate} | 买入 {len(buys)} · 卖出 {len(sells)} · "
          f"持有 {len(held)} · 空仓 {len(empty)} · 滞后/失败 {len(stale)}")
    print("今日买入:", [(x["leg"], x["name"]) for x in buys],
          "| 今日卖出:", [(x["leg"], x["name"]) for x in sells])
    if fails:
        print("刷新失败:", fails)


if __name__ == "__main__":
    main()
