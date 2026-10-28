# -*- coding: utf-8 -*-
"""波动资产 · V1 变种（MA20 穿越）· 每日买卖标的。

流程：
1. **先刷新场外复权净值**（`data.refresh_otc()`，六腿 + 桥接代理），失败沿用本地并标注；
2. 对**六腿**（`rule.LEG_ORDER` —— 与主策略同一池）逐腿判定「净值 vs MA20」：
   - **买入**（0→1）：净值上穿 MA20；**卖出**（1→0）：净值下穿 MA20；
   - **持有** / **空仓**：未发生穿越，维持原仓位；
3. 输出今日动作 + 最近 20 个交易日的信号流水，并标注 QDII 净值时滞。

成交提醒：T 日盘后算出的信号，**T+1 15:00 前提交**才按 T+1 净值成交（QDII 净值本身 T+1~T+2 才出）。

产物（相对本文件）：daily/_v1_ma20_daily.md / _v1_ma20_daily.json。
机械规则输出，非投资建议。
"""
import json
import os
import sys

try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:  # noqa
    pass

_HERE = os.path.dirname(os.path.abspath(__file__))
_V1 = os.path.dirname(_HERE)
_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(_V1)))
sys.path.insert(0, _V1)

import ma20  # noqa: E402

OUT_MD = os.path.join(_HERE, "_v1_ma20_daily.md")
OUT_JSON = os.path.join(_HERE, "_v1_ma20_daily.json")
RECENT = 20


def act(sig):
    cur, prev = int(sig.iloc[-1]), int(sig.iloc[-2])
    if cur == 1 and prev == 0:
        return "买入"
    if cur == 0 and prev == 1:
        return "卖出"
    return "持有" if cur == 1 else "空仓"


def freshness(leg, date, fails):
    cfg = ma20.rule.LEGS[leg]
    code = cfg.get("otc")
    if code in fails:
        return f"❌刷新失败({code})"
    exp = ma20.vdata._expected_otc_latest(cfg.get("otc_qdii", False))
    if date >= exp:
        return "✅最新"
    return f"⚠️滞后(应至 {exp})"


def mk_table(items):
    if not items:
        return ["（无）"]
    out = ["| 腿 | 执行代码 | 净值日 | 复权净值 | MA20 | 距MA20 | 当前仓位 | 数据 |",
           "|---|---|---|---|---|---|---|---|"]
    for x in sorted(items, key=lambda z: z["dist"], reverse=True):
        out.append(f"| {x['name']} | `{x['exec']}` | {x['date']} | {x['nav']:.4f} | "
                   f"{x['ma20']:.4f} | {x['dist']:+.2%} | {'持仓' if x['pos'] else '空仓'} | "
                   f"{x['fresh']} |")
    return out


def main():
    latest, fails = ma20.vdata.refresh_otc()
    maxdate = max(latest.values()) if latest else ""
    rows, flow = [], {}
    for leg in ma20.LEGS:
        cfg = ma20.rule.LEGS[leg]
        px = ma20.load(leg)
        sig, ma = ma20.signal(px)
        if ma.iloc[-1] != ma.iloc[-1]:          # NaN 兜底（样本 < MA 窗口）
            print(f"[跳过] {leg}: MA20 未成形")
            continue
        a = act(sig)
        rows.append({
            "leg": leg, "name": cfg["name"], "role": cfg["role"], "band": float(cfg["band"]),
            "exec": cfg["exec"], "exec_name": cfg["exec_name"],
            "date": px.index[-1].date().isoformat(), "nav": float(px.iloc[-1]),
            "ma20": float(ma.iloc[-1]), "dist": float(px.iloc[-1] / ma.iloc[-1] - 1.0),
            "pos": int(sig.iloc[-1]), "action": a,
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
        f"# 波动资产 · V1（MA20 穿越）· 每日买卖（{maxdate or '无数据'}）",
        f"> 池 = 主策略六腿（{' / '.join(ma20.rule.LEGS[k]['name'] for k in ma20.LEGS)}），"
        "口径 = 场外**复权净值**（含分红再投 / 汇率 / 跟踪误差）。",
        f"> 规则：净值上穿 MA{ma20.MA_WIN} **买入** · 下穿 MA{ma20.MA_WIN} **卖出** · 其余维持原仓位（0/1）。",
        f"> 空仓按货基 {ma20.CASH_ANNUAL:.2%} 计息；成交按 **T+1 净值**（T 日盘后算，T+1 15:00 前提交）。",
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
        "## 今日买入（上穿 MA20 · 0→1）",
        "",
    ] + mk_table(buys) + ["", "## 今日卖出（下穿 MA20 · 1→0）", ""] + mk_table(sells) + [
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
        L.append(f"| — | 最近 {RECENT} 个交易日内六腿无穿越 | — |")

    L += ["", "## 六腿明细", "",
          "| 腿 | 角色 | 目标带 | 执行代码 | 净值日 | 复权净值 | MA20 | 距MA20 | 当前仓位 | 动作 | 数据 |",
          "|---|---|---:|---|---|---|---|---|---|---|---|"]
    for x in sorted(rows, key=lambda z: ma20.LEGS.index(z["leg"])):
        L.append(f"| {x['name']} | {x['role']} | {x['band']:g}pp | `{x['exec']}` "
                 f"{x['exec_name']} | {x['date']} | {x['nav']:.4f} | {x['ma20']:.4f} | "
                 f"{x['dist']:+.2%} | {'持仓' if x['pos'] else '空仓'} | {x['action']} | {x['fresh']} |")

    L += ["", "## 非最新数据", ""] + (mk_table(stale) if stale else ["（全部为最新）"])
    L += [
        "",
        "## 提醒",
        "",
        "- 本变种只管**逐腿进出时点**，主策略管**组合层仓位带**（阶梯 / 年线闸门 / 再平衡）——"
        "两者可叠加，但不要互相替代。",
        "- **场外成本不可忽略**：六腿 13~16 笔/年，A 类腿（纳指 / 标普 / 黄金）每笔往返 0.6%，"
        "回测里扣费后组合年化从 8.10% 掉到 1.42%（见 `backtest/_v1_ma20_bt.md`）。",
        "",
        "> 机械规则输出，**非投资建议**。",
    ]

    with open(OUT_MD, "w", encoding="utf-8") as f:
        f.write("\n".join(L))
    with open(OUT_JSON, "w", encoding="utf-8") as f:
        json.dump({
            "strategy": ma20.LABEL, "as_of": maxdate, "pool": list(ma20.LEGS),
            "rule": {"buy": f"净值上穿MA{ma20.MA_WIN}", "sell": f"净值下穿MA{ma20.MA_WIN}",
                     "ma": ma20.MA_WIN, "trade_at": "T+1 净值", "cash_annual": ma20.CASH_ANNUAL},
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
