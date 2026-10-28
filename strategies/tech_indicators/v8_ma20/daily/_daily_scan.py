# -*- coding: utf-8 -*-
"""MA20 穿越策略 · 每日买入 / 卖出标的（v8 每日扫描）。

流程：
1. **先刷新行情**：`data/_refresh_data.py` 拉最新日线并重算 MA20（失败则沿用本地并标注）；
2. 对**标的池 3 只**（科创50 588000 / 恒生科技 513180 / 中证A500 563360，源自
   `backtest.py::POOL`）逐只判定「今收 vs MA20」的上穿 / 下穿：
   - **买入**（0→1）：今收 > MA20 且 昨收 ≤ 昨日 MA20；
   - **卖出**（1→0）：今收 < MA20 且 昨收 ≥ 昨日 MA20；
   - **持有**（维持 1）/ **空仓**（维持 0）：未发生穿越；
3. 输出当日买入 / 卖出名单 + 最近 20 个交易日的信号流水（哪一天买了谁、卖了谁）。

产物（相对本文件）：
- daily/_v8_ma20_daily.md
- daily/_v8_ma20_daily.json

机械规则输出，非投资建议。
"""
import importlib.util
import json
import os
import sys

import numpy as np

try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:  # noqa
    pass

_HERE = os.path.dirname(os.path.abspath(__file__))
_PARENT = os.path.dirname(_HERE)                    # strategies/tech_indicators/v8_ma20
_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(_PARENT)))   # 仓库根

_v8 = importlib.util.spec_from_file_location("ti_v8", os.path.join(_PARENT, "backtest.py"))
V8 = importlib.util.module_from_spec(_v8)
_v8.loader.exec_module(V8)
base = V8.base

sys.path.insert(0, os.path.join(_ROOT, "data"))
import _refresh_data  # noqa: E402

OUT_MD = os.path.join(_HERE, "_v8_ma20_daily.md")
OUT_JSON = os.path.join(_HERE, "_v8_ma20_daily.json")
RECENT = 20          # 近期信号流水保留的交易日数
LABEL = V8.LABEL


def act(sig):
    cur, prev = int(sig[-1]), int(sig[-2])
    if cur == 1 and prev == 0:
        return "买入"
    if cur == 0 and prev == 1:
        return "卖出"
    return "持有" if cur == 1 else "空仓"


def act_at(sig, i):
    """第 i 个 bar 的动作（i>=1）。"""
    if i < 1:
        return ""
    if sig[i] == 1 and sig[i - 1] == 0:
        return "买入"
    if sig[i] == 0 and sig[i - 1] == 1:
        return "卖出"
    return ""


def freshness(code, date, maxdate, fails):
    if code in fails:
        return "❌刷新失败"
    if date == maxdate:
        return "✅最新"
    return f"⚠️陈旧 {date}"


def mk_table(items):
    if not items:
        return ["（无）"]
    out = ["| 代码 | 主题 | 名称 | 风格 | 收盘 | MA20 | 距MA20 | 数据 |",
           "|---|---|---|---|---|---|---|---|"]
    for x in sorted(items, key=lambda z: z["dist"], reverse=True):
        out.append(f"| {x['code']} | {x['theme']} | {x['name']} | {x['style']} | "
                   f"{x['close']:.3f} | {x['ma20']:.3f} | {x['dist']:+.2%} | {x['fresh']} |")
    return out


def main():
    latest, fails = _refresh_data.ensure_fresh()
    maxdate = max(latest.values()) if latest else ""
    ind, lk = base.load()
    meta = V8.load_meta()

    rows, flow = [], {}
    for code, theme in V8.POOL.items():
        if code not in ind or code not in lk:
            print(f"[缺失] {code} 不在行情库，跳过")
            continue
        dates, close, r = V8.aligned(code, ind, lk)
        if len(close) < 22:
            print(f"[跳过] {code}: 样本不足（{len(close)} 根）")
            continue
        sig = V8.build_signal(close, r)
        ma = base._arr(r["MA20"])
        if np.isnan(ma[-1]):
            print(f"[跳过] {code}: MA20 未成形")
            continue
        name, style = meta.get(code, (code, "其他"))
        rows.append({
            "code": code, "theme": theme, "name": name, "style": style, "date": dates[-1],
            "close": float(close[-1]), "ma20": float(ma[-1]),
            "dist": float(close[-1] / ma[-1] - 1), "pos": int(sig[-1]),
            "action": act(sig), "fresh": freshness(code, dates[-1], maxdate, fails),
        })
        for i in range(max(1, len(dates) - RECENT), len(dates)):
            a = act_at(sig, i)
            if a:
                flow.setdefault(dates[i], {"买入": [], "卖出": []})[a].append((code, name))

    buys = [x for x in rows if x["action"] == "买入"]
    sells = [x for x in rows if x["action"] == "卖出"]
    stale = [x for x in rows if x["fresh"].startswith(("⚠️", "❌"))]
    held = [x for x in rows if x["action"] == "持有"]
    empty = [x for x in rows if x["action"] == "空仓"]
    dates_desc = sorted(flow, reverse=True)

    L = [
        f"# MA20 穿越策略 · 每日买卖标的（{maxdate or '无数据'}）",
        f"> 规则：{V8.BUY_DESC} **买入** · {V8.SELL_DESC} **卖出** · 其余维持原仓位（0/1）。",
        f"> 标的池 {len(rows)} 只（{' / '.join(V8.POOL.values())}）：最新 {len(rows) - len(stale)} 只 · "
        f"⚠️陈旧 {sum(1 for x in stale if x['fresh'].startswith('⚠️'))} 只 · "
        f"❌刷新失败 {len(fails)} 只。**陈旧 / 失败的信号不代表最新，请谨慎使用。**",
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
    ] + mk_table(buys) + [
        "",
        "## 今日卖出（下穿 MA20 · 1→0）",
        "",
    ] + mk_table(sells) + [
        "",
        f"## 近期信号流水（最近 {RECENT} 个交易日 · 仅列有动作的交易日）",
        "",
        "| 交易日 | 买入 | 卖出 |",
        "|---|---|---|",
    ]
    if dates_desc:
        for d in dates_desc:
            b = "、".join(f"{c} {n}" for c, n in flow[d]["买入"]) or "—"
            s = "、".join(f"{c} {n}" for c, n in flow[d]["卖出"]) or "—"
            L.append(f"| {d} | {b} | {s} |")
    else:
        L.append(f"| — | 最近 {RECENT} 个交易日内全池无穿越信号 | — |")

    L += [
        "",
        "## 标的池明细",
        "",
        "| 代码 | 主题 | 名称 | 风格 | 收盘 | MA20 | 距MA20 | 当前仓位 | 今日动作 | 数据 |",
        "|---|---|---|---|---|---|---|---|---|---|",
    ]
    for x in sorted(rows, key=lambda z: z["code"]):
        L.append(f"| {x['code']} | {x['theme']} | {x['name']} | {x['style']} | {x['close']:.3f} | "
                 f"{x['ma20']:.3f} | {x['dist']:+.2%} | "
                 f"{'持仓' if x['pos'] else '空仓'} | {x['action']} | {x['fresh']} |")

    L += ["", "## 非最新数据（陈旧 / 刷新失败）", ""] + (mk_table(stale) if stale else ["（全部为最新）"])
    L += ["", "> 口径：收盘价对 MA20 的穿越，信号当日收盘成交；未计佣金滑点。**模拟结果，非投资建议。**"]

    with open(OUT_MD, "w", encoding="utf-8") as f:
        f.write("\n".join(L))
    with open(OUT_JSON, "w", encoding="utf-8") as f:
        json.dump({
            "strategy": LABEL, "latest_date": maxdate, "pool": V8.POOL,
            "rule": {"buy": V8.BUY_DESC, "sell": V8.SELL_DESC, "ma": V8.MA_WIN},
            "summary": {"n": len(rows), "buy": len(buys), "sell": len(sells),
                        "hold": len(held), "empty": len(empty), "stale": len(stale),
                        "fail": len(fails)},
            "actions": rows,
            "recent_flow": [{"date": d, "buy": flow[d]["买入"], "sell": flow[d]["卖出"]}
                            for d in dates_desc],
        }, f, ensure_ascii=False, indent=1)

    print("建议:", OUT_MD)
    print("JSON:", OUT_JSON)
    print(f"最新交易日 {maxdate} | 买入 {len(buys)} · 卖出 {len(sells)} · "
          f"持有 {len(held)} · 空仓 {len(empty)} · 陈旧 {len(stale)} · 失败 {len(fails)}")
    print("今日买入:", [x["code"] for x in buys], "| 今日卖出:", [x["code"] for x in sells])
    if fails:
        print("刷新失败:", fails)


if __name__ == "__main__":
    main()
