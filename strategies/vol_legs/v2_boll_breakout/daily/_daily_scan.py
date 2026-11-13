# -*- coding: utf-8 -*-
"""波动资产 · V2 变种（BOLL 上轨突破 · N=35 / K=2）· 每日买卖标的。

流程：
1. 对**七腿**（`rule.LEG_ORDER`，与主策略同一池）逐腿跑 `boll.signal()`：
   - **买入**（0→1）：收盘**上穿上轨**（`close>upper` 且前一日 `<=upper`）；
   - **卖出**（1→0）：收盘**跌回中轨**（`close<mid`）；
   - **持有** / **空仓**：未触发，维持 0/1 仓位；
   - 布林中轨 = MA35、上轨 = MA35 + 2.0×STD35（只做多，不做下轨反做）。
2. 输出今日动作 + 最近 20 个交易日的信号流水，并标注 QDII 净值时滞。

成交提醒：T 日盘后算出的信号，**T+1 15:00 前提交**才按 T+1 净值成交。

产物（相对本文件）：daily/_v2_boll_daily.md / _v2_boll_daily.json。
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
_V2 = os.path.dirname(_HERE)
_VL = os.path.dirname(_V2)
sys.path.insert(0, _V2)

import boll  # noqa: E402

# 离线优先：禁止回测期内的网络刷新（只读本地缓存，与看板其余卡片口径一致）
boll.vdata._otc_stale = lambda: False  # type: ignore[attr-defined]

OUT_MD = os.path.join(_HERE, "_v2_boll_daily.md")
OUT_JSON = os.path.join(_HERE, "_v2_boll_daily.json")
RECENT = 20


def act(sig):
    cur, prev = int(sig.iloc[-1]), int(sig.iloc[-2])
    if cur == 1 and prev == 0:
        return "买入"
    if cur == 0 and prev == 1:
        return "卖出"
    return "持有" if cur == 1 else "空仓"


def freshness(leg, date):
    cfg = boll.rule.LEGS[leg]
    exp = boll.vdata._expected_otc_latest(cfg.get("otc_qdii", False))
    if date >= exp:
        return "✅最新"
    return f"⚠️滞后(应至 {exp})"


def mk_table(items):
    if not items:
        return ["（无）"]
    out = ["| 腿 | 执行代码 | 净值日 | 复权净值 | 中轨/上轨 | 距触发位 | 当前仓位 | 数据 |",
           "|---|---|---|---|---|---|---|---|"]
    for x in sorted(items, key=lambda z: z["dist"], reverse=True):
        out.append(f"| {x['name']} | `{x['exec']}` | {x['date']} | {x['nav']:.4f} | "
                   f"{x['mid']:.4f} / {x['upper']:.4f} | "
                   f"{x['dist_kind']} {x['dist']:+.2%} | {x['pos_txt']} | "
                   f"{x['fresh']} |")
    return out


def main():
    rows, flow = [], {}
    for leg in boll.LEGS:
        cfg = boll.rule.LEGS[leg]
        px = boll.load(leg)
        sig, mid, upper = boll.signal(px)
        if upper.iloc[-1] != upper.iloc[-1]:
            print(f"[跳过] {leg}: 布林窗口未成形")
            continue
        pos = int(sig.iloc[-1])
        w_now = float(boll.weight(px, sig).iloc[-1])      # 0/1 信号 × 波动上限
        up = float(upper.iloc[-1])
        md = float(mid.iloc[-1])
        dist_kind = "距中轨" if pos else "距上轨"
        dist = float(px.iloc[-1] / (md if pos else up) - 1.0)
        date = px.index[-1].date().isoformat()
        rows.append({
            "leg": leg, "name": cfg["name"], "role": cfg["role"], "band": float(cfg["band"]),
            "exec": cfg["exec"], "exec_name": cfg["exec_name"],
            "date": date, "nav": float(px.iloc[-1]),
            "mid": md, "upper": up, "dist_kind": dist_kind, "dist": dist,
            "pos": pos, "w": w_now, "level": (f"{w_now:.0%}" if pos else ""),
            "pos_txt": ("空仓" if pos == 0 else f"持仓 {w_now:.0%}"),
            "action": act(sig), "fresh": freshness(leg, date),
        })
        s = sig.to_numpy()
        for i in range(max(1, len(s) - RECENT), len(s)):
            if s[i] != s[i - 1]:
                d = px.index[i].date().isoformat()
                flow.setdefault(d, {"买入": [], "卖出": []})[
                    "买入" if s[i] == 1 else "卖出"].append((leg, cfg["name"]))

    as_of = max((x["date"] for x in rows), default="")
    buys = [x for x in rows if x["action"] == "买入"]
    sells = [x for x in rows if x["action"] == "卖出"]
    held = [x for x in rows if x["action"] == "持有"]
    empty = [x for x in rows if x["action"] == "空仓"]
    stale = [x for x in rows if x["fresh"].startswith(("⚠️", "❌"))]
    dates_desc = sorted(flow, reverse=True)

    L = [
        f"# 波动资产 · V2（BOLL 上轨突破 N={boll.PARAMS['N']} / K={boll.PARAMS['K']:g}）· 每日买卖（{as_of or '无数据'}）",
        f"> 池 = 主策略七腿（{' / '.join(boll.rule.LEGS[k]['name'] for k in boll.LEGS)}），"
        "口径 = 场外**复权净值**（含分红再投 / 汇率 / 跟踪误差）。",
        f"> 规则：收盘**上穿上轨**（MA{boll.PARAMS['N']}+{boll.PARAMS['K']:g}×STD）→ 买入；"
        "收盘**跌回中轨** → 卖出；其余维持 0/1 仓位。只做多，不做下轨反做。",
        f"> **波动上限 {boll.PARAMS['TV']:.0%}（可配 `PARAMS[\"TV\"]`，设 None 关闭）**："
        f"信号为持仓时，实际仓位 = min(1, {boll.PARAMS['TV']:.0%} / σ{boll.PARAMS['TV_WIN']}年化) "
        "—— 入场判定不变，只在波动高时压浅仓位。表内「当前仓位」即该实际仓位。",
        f"> 空仓按货基 {boll.CASH_ANNUAL:.2%} 计息；成交按 **T+1 净值**（T 日盘后算，T+1 15:00 前提交）。",
        f"> 七腿 {len(rows)} 只：最新 {len(rows) - len(stale)} 只 · 滞后/失败 {len(stale)} 只。"
        "**QDII 腿（纳指 / 标普 / 恒生科技）净值本身滞后 1~2 日，信号随之延后。**",
        "> 机械规则输出，**非投资建议**。",
        "",
        "## 今日动作汇总",
        "",
        "| 买入 | 卖出 | 持有 | 空仓 |",
        "|---|---|---|---|",
        f"| {len(buys)} | {len(sells)} | {len(held)} | {len(empty)} |",
        "",
        f"## 今日买入（上穿上轨 · 0→1）",
        "",
    ] + mk_table(buys) + [
        "",
        "## 今日卖出（跌回中轨 · 1→0）",
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
        L.append(f"| — | 最近 {RECENT} 个交易日内七腿无信号 | — |")

    L += ["", "## 七腿明细", "",
          "| 腿 | 角色 | 目标带 | 执行代码 | 净值日 | 复权净值 | 中轨/上轨 | 距触发位 | 当前仓位 | 动作 | 数据 |",
          "|---|---|---:|---|---|---|---|---|---|---|---|"]
    for x in sorted(rows, key=lambda z: boll.LEGS.index(z["leg"])):
        L.append(f"| {x['name']} | {x['role']} | {x['band']:g}pp | `{x['exec']}` "
                 f"{x['exec_name']} | {x['date']} | {x['nav']:.4f} | "
                 f"{x['mid']:.4f} / {x['upper']:.4f} | "
                 f"{x['dist_kind']} {x['dist']:+.2%} | {x['pos_txt']} | "
                 f"{x['action']} | {x['fresh']} |")

    L += ["", "## 非最新数据", ""] + (mk_table(stale) if stale else ["（全部为最新）"])
    L += [
        "",
        "## 提醒",
        "",
        "- 本变种只管**逐腿进出时点**，主策略管**组合层仓位带** —— 两者可叠加，不可互相替代。",
        "- 它是**突破追涨型**（突破了才上车），与主策略「跌了才加仓」的阶梯方向相反。",
        "- 年笔数比 MA20 少得多，曝光天然偏低：没有突破就一直空仓。",
        "",
        "> 机械规则输出，**非投资建议**。",
    ]

    with open(OUT_MD, "w", encoding="utf-8") as f:
        f.write("\n".join(L))
    with open(OUT_JSON, "w", encoding="utf-8") as f:
        json.dump({
            "strategy": boll.LABEL, "as_of": as_of, "pool": list(boll.LEGS),
            "rule": {"buy": "收盘上穿上轨(MA35+2STD35)", "sell": "收盘跌回中轨MA35",
                     "trade_at": "T+1 净值", "cash_annual": boll.CASH_ANNUAL,
                     "n": boll.PARAMS["N"], "k": boll.PARAMS["K"], "stop": boll.PARAMS["STOP"],
                     "tv": boll.PARAMS["TV"], "tv_win": boll.PARAMS["TV_WIN"],
                     "sizing": "持仓时仓位=min(1, TV/σ20年化)，入场不变"},
            "summary": {"n": len(rows), "buy": len(buys), "sell": len(sells),
                        "hold": len(held), "empty": len(empty), "stale": len(stale), "fail": 0},
            "legs": rows,
            "recent_flow": [{"date": d, "buy": flow[d]["买入"], "sell": flow[d]["卖出"]}
                            for d in dates_desc],
        }, f, ensure_ascii=False, indent=1)

    print("报告:", OUT_MD)
    print("JSON:", OUT_JSON)
    print(f"最新净值日 {as_of} | 买入 {len(buys)} · 卖出 {len(sells)} · "
          f"持有 {len(held)} · 空仓 {len(empty)} · 滞后/失败 {len(stale)}")
    print("今日买入:", [(x["leg"], x["name"]) for x in buys],
          "| 今日卖出:", [(x["leg"], x["name"]) for x in sells])


if __name__ == "__main__":
    main()
