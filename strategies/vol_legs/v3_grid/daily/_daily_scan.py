# -*- coding: utf-8 -*-
"""波动资产 · V3 变种（等金额网格）· 每日操作。

流程：
1. **先刷新场外复权净值**（`data.refresh_otc()`，六腿 + 桥接代理），失败沿用本地并标注；
2. 对**六腿**（`rule.LEG_ORDER` —— 与主策略同一池）逐腿**机械回放**网格账户：
   P0 = 样本首根净值（或 `--p0 leg=价格` 显式指定），按 3% / 10 份 / 先买 5 份跑到最新一根；
3. 输出：当前 P0 与上下格线、持仓份数、现金、状态（ok / 卖光 / 买满）、
   **下一交易日该下的那一单**（T+1 限价）、卖光后的重开价、压力测试与额度上限。

成交提醒：T 日盘后按收盘净值判档，**T+1 15:00 前提交**才按 T+1 净值成交；
次日净值若已回到格线内侧，本模型视为**未成交**（`grid.FILL_RULE = "limit"`），不要追着补票。

产物（相对本文件）：daily/_v3_grid_daily.md / _v3_grid_daily.json。
机械规则输出，非投资建议。
"""
import json
import os
import sys

import pandas as pd

try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:  # noqa
    pass

_HERE = os.path.dirname(os.path.abspath(__file__))
_V3 = os.path.dirname(_HERE)
sys.path.insert(0, _V3)

import grid  # noqa: E402

OUT_MD = os.path.join(_HERE, "_v3_grid_daily.md")
OUT_JSON = os.path.join(_HERE, "_v3_grid_daily.json")
RECENT_DAYS = 90        # 流水窗口（自然日）
MAX_ROWS = 40


def arg(name, default=None):
    """简单取参：--name value。"""
    if name in sys.argv:
        i = sys.argv.index(name)
        if i + 1 < len(sys.argv):
            return sys.argv[i + 1]
    return default


def p0_overrides():
    """--p0 a500=4724,ndx=1.05 → {leg: price}。"""
    raw = arg("--p0", "")
    out = {}
    for part in str(raw).split(","):
        if "=" in part:
            k, v = part.split("=", 1)
            try:
                out[k.strip()] = float(v)
            except ValueError:
                pass
    return out


def since_overrides():
    """--since a500=2026-01-05,ndx=2026-03-02 → {leg: 开格日}。"""
    raw = arg("--since", "")
    out = {}
    for part in str(raw).split(","):
        if "=" in part:
            k, v = part.split("=", 1)
            if k.strip() and v.strip():
                out[k.strip()] = v.strip()
    return out


def replay(leg, px, p0=None, since=None, **kw):
    """回放该腿网格。

    - 既没给 p0 也没给 since → **机械回放**：P0 = 样本首根净值，从第一根跑到今天
      （与 v1_ma20 同一做法，用来复现回测状态）；
    - 给了 p0 / since → **按真实开格日回放**：从 `since`（默认 = 今天）开格、P0 = 给定价，
      这才是实盘口径（否则 `--p0` 只会改到十几年前的起点，等于没用）。
    """
    fee = grid.fees(leg)
    if p0 is None and since is None:
        return grid.run(px, grid_pct=kw["grid_pct"], total_units=kw["total_units"],
                        reanchor=kw["reanchor"], fee=fee)
    sub = px.loc[since:] if since else px.iloc[-1:]
    if len(sub) >= 2:
        return grid.run(sub, p0=p0, grid_pct=kw["grid_pct"], total_units=kw["total_units"],
                        reanchor=kw["reanchor"], fee=fee)
    # 今天才开格（或 --since 正好落在最后一根）：直接建一个新鲜账户，不做无意义回放
    price = float(sub.iloc[-1])
    tr = grid.GridTrader(p0=price if p0 is None else p0, grid_pct=kw["grid_pct"],
                         total_units=kw["total_units"], fee=fee)
    tr.current_price = price
    nav = pd.Series([tr.nav(price)], index=sub.index)
    return dict(nav=nav, trader=tr, fills=tr.fills, reanchors=tr.reanchors,
                exposure=tr.invested(price) / tr.nav(price) if tr.nav(price) else 0.0,
                days_sold_out=0, days_full=0, realized=0.0, fee_total=tr.fee_total,
                turnover=tr.turnover, bars=1, p0=tr.p0)


def freshness(leg, date, fails):
    cfg = grid.rule.LEGS[leg]
    code = cfg.get("otc")
    if code in fails:
        return f"❌刷新失败({code})"
    exp = grid.vdata._expected_otc_latest(cfg.get("otc_qdii", False))
    if date >= exp:
        return "✅最新"
    return f"⚠️滞后(应至 {exp})"


def action_text(act):
    """下一交易日的挂单描述（含限价条件）。"""
    if not act:
        return "无"
    side = {"buy": "买入", "sell": "卖出", "reanchor": "重画网格并买入"}.get(act["side"], act["side"])
    return f"{side} {int(act.get('n') or 0)} 份"


def limit_price(tr, act):
    """限价挂单的触发价（次日净值需越过该价才成交）。"""
    if not act or act["side"] == "reanchor":
        return None
    return tr.p0 * (1.0 + int(act["level"]) * tr.grid_pct)


def main():
    capital = float(arg("--capital", 1000000) or 1000000)
    grid_pct = float(arg("--grid-pct", grid.GRID_PCT) or grid.GRID_PCT)
    units = int(arg("--units", grid.TOTAL_UNITS) or grid.TOTAL_UNITS)
    reanchor = arg("--reanchor", grid.REANCHOR)
    p0s = p0_overrides()
    sincs = since_overrides()
    gsince = arg("--since", "")
    gsince = gsince if gsince and "=" not in gsince else None      # 全局开格日

    latest, fails = grid.vdata.refresh_otc()
    maxdate = max(latest.values()) if latest else ""

    rows, runs = [], {}
    for leg in grid.LEGS:
        cfg = grid.rule.LEGS[leg]
        px = grid.load(leg)
        since = sincs.get(leg) or gsince
        r = replay(leg, px, p0=p0s.get(leg), since=since, grid_pct=grid_pct,
                   total_units=units, reanchor=reanchor)
        runs[leg] = r
        tr = r["trader"]
        price = float(px.iloc[-1])
        tr.current_price = price
        act = tr.last_act if tr.last_act_bar == len(r["nav"]) - 1 else None
        k = tr.level(price)
        buy_px, sell_px, _k = tr.next_levels(price)
        band = float(cfg["band"])
        leg_capital = capital * band / 100.0            # 该腿额度 = band pp of 总资产
        unit_amount = leg_capital / tr.total_units      # 每份金额
        st = grid.stress(price, grid_pct=grid_pct, total_units=units,
                         capital=leg_capital, fee_buy=grid.fees(leg)[0])
        rows.append(dict(
            leg=leg, name=cfg["name"], role=cfg["role"], band=band,
            exec_code=cfg["exec"], exec_name=cfg["exec_name"],
            date=px.index[-1].date().isoformat(), nav=price,
            p0=tr.p0, level=k,
            upper=tr.p0 * (1 + (units // 2) * grid_pct),
            lower=tr.p0 * (1 - (units // 2) * grid_pct),
            next_buy=buy_px, next_sell=sell_px,
            holdings=tr.holdings(), total_units=tr.total_units,
            cash=tr.cash, equity=tr.nav(price),
            exposure=tr.invested(price) / tr.nav(price) if tr.nav(price) else 0.0,
            state=tr.state(), action=action_text(act),
            limit=limit_price(tr, act),
            revive=tr.revive_price(),
            sold_out_at=tr.sold_out_at,
            grid_nav=float(r["nav"].iloc[-1]),
            grid_ann=grid.perf(r["nav"])["ann"],
            fees_paid=tr.fee_total, fills=len(tr.fills),
            capital_leg=leg_capital, unit_amount=unit_amount,
            stress_loss_pct=st["loss_pct"], stress_loss_leg=st["loss_amount"],
            stress_loss_pp=st["loss_pct"] * band,
            budget_pp=grid.stress_budget(st["loss_pct"], 3.0),
            fresh=freshness(leg, px.index[-1].date().isoformat(), fails),
        ))

    buys = [x for x in rows if x["action"].startswith("买入")]
    sells = [x for x in rows if x["action"].startswith("卖出")]
    rebase = [x for x in rows if x["action"].startswith("重画")]
    sold_out = [x for x in rows if x["state"] == "sold_out"]
    full = [x for x in rows if x["state"] == "buy_full"]
    stale = [x for x in rows if x["fresh"].startswith(("⚠️", "❌"))]

    last_day = max(pd.Timestamp(r["nav"].index[-1]) for r in runs.values())
    cut = (last_day - pd.Timedelta(days=RECENT_DAYS)).date().isoformat()
    flow = {}
    for leg, r in runs.items():
        for f in r["fills"]:
            if f["date"] == "None" or f["date"] < cut:      # 跳过初始建仓（无日期）
                continue
            flow.setdefault(f["date"], {"buy": [], "sell": []})[
                "buy" if f["side"] == "buy" else "sell"].append(
                    (grid.rule.LEGS[leg]["name"], f["n"], f["price"], f["level"]))
    dates_desc = sorted(flow, reverse=True)[:MAX_ROWS]

    L = [
        f"# 波动资产 · V3（等金额网格）· 每日操作（{maxdate or '无数据'}）",
        f"> 池 = 主策略六腿（{' / '.join(grid.rule.LEGS[k]['name'] for k in grid.LEGS)}），"
        "价格 = 场外**复权净值**（含分红再投 / 汇率 / 跟踪误差）。",
        f"> 规则：**{grid_pct:.0%} 等比例网格 / {units} 份 / 先买 {units // 2} 份 / 每份金额相等** —— "
        "跌破下一格买 1 份、涨破上一格卖 1 份；卖光不追高（跌破卖光价 "
        f"{(1 - grid.REANCHOR_DROP):.0%} 才重画）、买满不加新资金。",
        f"> 成交：T 日盘后判档 → **T+1 净值成交**；次日净值若回到格线内侧则**撤单**（`FILL_RULE=limit`）。"
        f" 总资产 {capital:,.0f} 元 → 该腿额度 = band pp × 总资产，每份 = 额度 / {units}。",
        f"> 六腿 {len(rows)} 只：最新 {len(rows) - len(stale)} 只 · 滞后/失败 {len(stale)} 只。"
        "**QDII 腿（纳指 / 标普 / 恒生科技）净值本身滞后 1~2 日，信号随之延后。**",
        "> 机械规则输出，**非投资建议**。",
        "",
        "## 今日动作汇总",
        "",
        "| 买入 | 卖出 | 重画 | 卖光(等回落重开) | 买满(不加新钱) | 无动作 |",
        "|---|---|---|---|---|---|",
        f"| {len(buys)} | {len(sells)} | {len(rebase)} | {len(sold_out)} | {len(full)} | "
        f"{len(rows) - len(buys) - len(sells) - len(rebase)} |",
        "",
        "## 下一交易日挂单（T+1 限价）",
        "",
        "| 腿 | 执行代码 | 净值日 | 复权净值 | P0（中间价） | 档位 | 持仓 | 现金 | 动作 | 限价条件 | 数据 |",
        "|---|---|---|---:|---:|---:|---:|---:|---|---|---|",
    ]
    for x in sorted(rows, key=lambda z: (z["action"] == "无", grid.LEGS.index(z["leg"]))):
        lim = (f"净值 ≤ {x['limit']:.4f}" if x["action"].startswith("买入") else
               f"净值 ≥ {x['limit']:.4f}" if x["action"].startswith("卖出") else "—")
        L.append(f"| {x['name']} | `{x['exec_code']}` | {x['date']} | {x['nav']:.4f} | "
                 f"{x['p0']:.4f} | {x['level']:+d} | {x['holdings']}/{x['total_units']} | "
                 f"{x['cash']:.3f} | {x['action']} | {lim} | {x['fresh']} |")

    L += [
        "",
        "## 网格状态明细",
        "",
        "| 腿 | 角色 | 额度 | 每份金额 | 状态 | 下格买价 | 上格卖价 | 账户累计 | 卖光后重开价 | 累计费用 | 成交 |",
        "|---|---|---:|---:|---|---:|---:|---:|---:|---:|---:|",
    ]
    for x in sorted(rows, key=lambda z: grid.LEGS.index(z["leg"])):
        state = {"ok": "正常", "sold_out": "**卖光**（不加仓）", "buy_full": "**买满**（不追加）"}[x["state"]]
        revive = (f"{x['revive']:.4f}" if x["revive"] and x["state"] == "sold_out" else "—")
        L.append(f"| {x['name']} | {x['role']} | {x['band']:g}pp | {x['unit_amount']:,.0f} 元 | {state} | "
                 f"{x['next_buy']:.4f} | {x['next_sell']:.4f} | {x['grid_nav'] - 1.0:+.2%} | "
                 f"{revive} | {x['fees_paid']:.3f} | {x['fills']} |")

    # 出界清单（规则卡 §5）：卖光 → 只有跌破重开价才续命；买满 → 不再加钱，等涨破上格卖出
    L += ["", "## 出界清单与出路（规则卡 §5）", ""]
    outs = [x for x in rows if x["state"] != "ok"]
    if not outs:
        L.append("（无出界：六腿都还有买/卖空间）")
    else:
        L += ["| 腿 | 状态 | 卖光价（成交） | 重开价（跌破才续命） | 距重开价 | 出路 |",
              "|---|---|---:|---:|---:|---|"]
        for x in sorted(outs, key=lambda z: grid.LEGS.index(z["leg"])):
            if x["state"] == "sold_out" and x["revive"]:
                dist = x["nav"] / x["revive"] - 1.0
                way = (f"等净值跌到 **{x['revive']:.4f}** 以下自动重画并买入 "
                       f"{x['total_units'] // 2} 份；不想等就用 `--p0 {x['leg']}=<当前净值>` "
                       "手动重开（= 追高，代价见回测 §5）")
                L.append(f"| {x['name']} | 卖光 | {x['sold_out_at']:.4f} | {x['revive']:.4f} | "
                         f"{dist:+.1%} | {way} |")
            else:
                L.append(f"| {x['name']} | **买满** | — | — | — | 不注入新资金；"
                         f"涨破 {x['next_sell']:.4f} 卖 1 份释放额度 |")

    L += [
        "",
        "## 开格前压力测试（规则卡 §6：买满后再跌 36%）",
        "",
        "| 腿 | 额度 | 买满后再跌 36% 的额度亏损 | 占总资产损失 | 若只肯亏 3pp → 额度上限 |",
        "|---|---:|---:|---:|---:|",
    ]
    for x in sorted(rows, key=lambda z: grid.LEGS.index(z["leg"])):
        L.append(f"| {x['name']} | {x['band']:g}pp | {x['stress_loss_pct']:.1%} "
                 f"（{x['stress_loss_leg']:,.0f} 元） | **{x['stress_loss_pp']:.1f}pp** | "
                 f"{x['budget_pp']:.1f}pp |")

    L += [
        "",
        f"> 3% / {units} 份的网格，买满（= 最后一格 {1 - (units // 2) * grid_pct:.0%}）后再跌 36%，"
        "亏的是额度的 **40%~43%**，不是 36% —— 因为补仓价在下跌途中。"
        "**开格前先用这张表反推额度，扛不住就减少总资金。**",
        "",
        f"## 近期成交流水（最近 {RECENT_DAYS} 个自然日 · 仅列有成交的日期）",
        "",
        "| 日期 | 买入 | 卖出 |",
        "|---|---|---|",
    ]
    if dates_desc:
        for d in dates_desc:
            b = "、".join(f"{n} {c}份@{p:.4f}(格{lv:+d})" for c, n, p, lv in flow[d]["buy"]) or "—"
            s = "、".join(f"{n} {c}份@{p:.4f}(格{lv:+d})" for c, n, p, lv in flow[d]["sell"]) or "—"
            L.append(f"| {d} | {b} | {s} |")
    else:
        L.append(f"| — | 最近 {RECENT_DAYS} 个自然日六腿无成交 | — |")

    L += ["", "## 非最新数据", ""]
    L += ([f"- {x['name']}：{x['fresh']}" for x in stale] if stale else ["（全部为最新）"])
    L += [
        "",
        "## 提醒",
        "",
        "- 本变种只管**带内按格子高抛低吸**；哪条腿该有多少 pp 由主策略（v1.3 仓位带）决定，"
        "两者是「带 → 格」的两层，不要互相替代。",
        "- **没给 `--p0` / `--since` 时是「从样本首日机械回放」**（P0 = 首根净值），"
        "与你账户的真实持仓无关。首次使用请用 `--since 2026-06-01 --p0 hkt=0.72` 说清"
        "「哪天、以什么中间价开的格」，再核对「账户累计」。",
        "- 场外成本：A 类（纳指 / 标普 / 黄金）每笔往返 0.60%，3% 的格子意味着**每格毛利要交掉两成**。"
        "回测见 `../backtest/_v3_grid_bt.md`。",
        "",
        "> 机械规则输出，**非投资建议**。",
    ]

    with open(OUT_MD, "w", encoding="utf-8") as f:
        f.write("\n".join(L))
    with open(OUT_JSON, "w", encoding="utf-8") as f:
        json.dump({
            "strategy": grid.LABEL, "as_of": maxdate, "pool": list(grid.LEGS),
            "rule": {"grid_pct": grid_pct, "total_units": units,
                     "init_units": units // 2, "reanchor": reanchor,
                     "reanchor_drop": grid.REANCHOR_DROP,
                     "fill_rule": grid.FILL_RULE, "trade_at": "T+1 净值",
                     "stress_drop": grid.STRESS_DROP, "cash_annual": grid.CASH_ANNUAL},
            "capital": capital,
            "summary": {"n": len(rows), "buy": len(buys), "sell": len(sells),
                        "reanchor": len(rebase), "sold_out": len(sold_out),
                        "buy_full": len(full), "stale": len(stale), "fail": len(fails)},
            "legs": rows,
        }, f, ensure_ascii=False, indent=1)

    print("报告:", OUT_MD)
    print("JSON:", OUT_JSON)
    print(f"最新净值日 {maxdate} | 买入 {len(buys)} · 卖出 {len(sells)} · 卖光 {len(sold_out)} · "
          f"买满 {len(full)} · 滞后/失败 {len(stale)}")
    for x in rows:
        print(f"  {x['name']:8s} P0={x['p0']:.4f} 档位{x['level']:+d} 持仓 {x['holdings']}/{x['total_units']} "
              f"状态 {x['state']:8s} 动作 {x['action']}")
    if fails:
        print("刷新失败:", fails)


if __name__ == "__main__":
    main()
