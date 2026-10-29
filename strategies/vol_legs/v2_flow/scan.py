# -*- coding: utf-8 -*-
"""波动资产 · V2 变种（资金渗透率 / 份额拥挤度）· 每日推荐操作。

流程：
1. 刷新份额缓存（东财季报 gmbd + 新浪日 K + 腾讯实时；`REFRESH_DAYS=7` 内复用缓存，
   `--refresh` 强制重取）；
2. 对六腿（与主策略同一池）逐腿复算：头部 ETF 合计份额 → 季度净申购率 → 3 年分位（拥挤度）
   → 三档闸门（≥90% 半带 / ≤30% 满带 / 中间维持）；
3. 输出「下一交易日该下的那一笔」：哪条腿降档/回档、动多少 pp、对应多少金额。

成交提醒：T 日盘后算出的信号，**T+1 15:00 前提交**才按 T+1 净值成交（QDII 净值本身 T+1~T+2 才出）。

产物（相对本文件）：`daily/_v2_flow_daily.md` / `daily/_v2_flow_daily.json`。
机械规则输出，非投资建议。
"""
import json
import os
import sys
from datetime import date, timedelta

try:
    sys.stdout.reconfigure(encoding="utf-8")
except (AttributeError, ValueError):  # noqa: BLE001
    pass

_HERE = os.path.dirname(os.path.abspath(__file__))
_VL = os.path.dirname(_HERE)
for _p in (_HERE, _VL):
    if _p not in sys.path:
        sys.path.insert(0, _p)

import flowdata          # noqa: E402
import flowrule          # noqa: E402

DAILY = os.path.join(_HERE, "daily")
OUT_MD = os.path.join(DAILY, "_v2_flow_daily.md")
OUT_JSON = os.path.join(DAILY, "_v2_flow_daily.json")
RECENT = 8          # 最近几次档位变化


def parse_capital(argv):
    for i, a in enumerate(argv):
        if a == "--capital" and i + 1 < len(argv):
            try:
                return float(argv[i + 1])
            except ValueError:
                return None
        if a.startswith("--capital="):
            try:
                return float(a.split("=", 1)[1])
            except ValueError:
                return None
    return None


def fmt_pct(x, nd=2):
    return "—" if x is None or x != x else f"{x * 100:+.{nd}f}%"


def expected_anchor(today=None, grace_days=20):
    """「应已披露」的最近季报期：季末 + 20 天宽限（季报 15 个工作日内披露）。"""
    d = today or date.today()
    ends = [date(d.year, 3, 31), date(d.year, 6, 30),
            date(d.year, 9, 30), date(d.year, 12, 31)]
    done = [x for x in ends if x + timedelta(days=grace_days) <= d]
    return done[-1].isoformat() if done else "—"


def leg_row(p, capital=None):
    """单腿今日状态 + 动作。"""
    crowd = p["crowd"].iloc[-1] if len(p["crowd"]) else float("nan")
    g_now = p["g"].iloc[-1] if len(p["g"]) else float("nan")
    pos_now = float(p["pos"].iloc[-1]) if len(p["pos"]) else float(flowrule.CFG["start_pos"])
    act_now = p["action"].iloc[-1] if len(p["action"]) else "不执行"
    band = p["band"]
    target_pp = band * pos_now
    row = dict(
        leg=p["leg"], name=p["name"], role=p["role"], band=band,
        exec_code=p["exec"], exec_name=p["exec_name"], head=p["head"],
        head_names=p["head_names"], last_anchor=p["last_anchor"],
        nav_date=p["axis"].index[-1].date().isoformat(),
        nav=float(p["axis"].iloc[-1]),
        shares_now=(float(p["shares"].iloc[-1].sum()) if len(p["shares"]) else None),
        g_now=(None if g_now != g_now else float(g_now)),
        crowd=(None if crowd != crowd else float(crowd)),
        pos=pos_now, target_pp=target_pp, delta_pp=target_pp - band,
        action=act_now,
        ratio_now=(float(p["ratio"]["ratio_pct"].iloc[-1])
                   if p["ratio"] is not None and len(p["ratio"]) else None),
    )
    if capital:
        row["target_amount"] = target_pp / 100.0 * capital
        row["delta_amount"] = row["delta_pp"] / 100.0 * capital
    return row


def history(p, n=RECENT):
    """最近 n 次档位变化（从 action 序列里挑非「维持/不执行」）。"""
    act, out = p["action"], []
    if not len(act):
        return out
    for i in range(len(act)):
        if act.iloc[i] in ("减半", "恢复"):
            out.append((act.index[i].date().isoformat(), act.iloc[i], float(p["pos"].iloc[i])))
    return out[-n:]


def main():
    os.makedirs(DAILY, exist_ok=True)
    capital = parse_capital(sys.argv)
    if "--refresh" in sys.argv or not flowdata.cache_ready():
        print("刷新份额/日K/实时行情 ...")
        asof, fails = flowdata.refresh(force="--refresh" in sys.argv, verbose=True)
        if fails:
            print("刷新失败项:", fails)

    ps = flowrule.panels()
    rows = [leg_row(ps[k], capital) for k in flowrule.LEGS]
    asof = max(r["nav_date"] for r in rows)
    exp_anchor = expected_anchor()
    stale = [r for r in rows if r["last_anchor"] < exp_anchor or r["crowd"] is None]

    downs = [r for r in rows if r["action"] == "减半"]
    ups = [r for r in rows if r["action"] == "恢复"]
    half = [r for r in rows if r["pos"] < 1.0 - 1e-9]
    hold = [r for r in rows if r["action"] == "维持"]
    nulls = [r for r in rows if r["action"] == "不执行"]
    crowd_sum = sum(r["band"] * r["pos"] for r in rows)
    band_sum = sum(r["band"] for r in rows)

    L = [
        f"# 波动资产 · V2（资金渗透率 / 份额拥挤度）· 每日推荐操作（{asof}）",
        f"> 池 = 主策略六腿（{' / '.join(r['name'] for r in rows)}），带宽沿用主策略 "
        f"v1.3（合计 {band_sum:g}pp），**本变种只改仓位系数，不改带宽**。",
        "> 信号 = 头部 ETF 合计份额的**季度净申购率** → 近 3 年**分位**（拥挤度）。"
        "口径见 `head_fund_ratio_rules.md`；实现见 `../v2_flow/flowrule.py`。",
        f"> 规则：拥挤度 ≥{flowrule.CFG['crowd_hi']:.0%} → 半带 · "
        f"≤{flowrule.CFG['crowd_lo']:.0%} → 满带 · 中间维持。"
        "T 日盘后算、**T+1 15:00 前提交**才按 T+1 净值成交。",
        "> 机械规则输出，**非投资建议**。",
        "",
        "## 一、今日动作汇总",
        "",
        "| 今日降档（拥挤） | 今日回档（冷清） | 当前处于半带 | 维持 | 不执行（数据未成形） | "
        "当前合计仓位 |",
        "|---|---|---|---|---|---|",
        f"| {len(downs)} | {len(ups)} | {len(half)} | {len(hold)} | {len(nulls)} | "
        f"**{crowd_sum:g}pp / {band_sum:g}pp**（{crowd_sum / band_sum:.0%}） |",
        "",
        "## 二、需要下单的腿",
        "",
        "> 「今日新触发」= 今天这根净值日才跨过阈值；「存量档位」= 之前在更早的日期触发、至今仍有效。"
        "**存量档位同样是当前指令**：你的仓位若与「目标仓位」不符，今天就该调到目标位。",
        "",
    ]
    if half:
        L += ["| 腿 | 今日是否新触发 | 目标带 | 目标仓位 | 需调整 | "
              + ("金额 | " if capital else "") + "拥挤度 | 理由 |",
              "|---|---|---:|---:|---:|" + ("---:|" if capital else "") + "---|---|"]
        for r in half:
            new = r["action"] in ("减半", "恢复")
            reason = ("净申购率处于 3 年最高 10% 分位（拥挤，打对折）"
                      if r["pos"] < 0.75 else "净申购率处于 3 年最低 30% 分位（筹码出清，回满带）")
            crowd_txt = "—" if r["crowd"] is None else f"{r['crowd']:.0%}"
            L.append(f"| {r['name']} | {'**是**' if new else '否（存量）'} | "
                     f"{r['band']:g}pp | {r['target_pp']:g}pp | {r['delta_pp']:+.2f}pp | "
                     + (f"{r['delta_amount']:+,.0f} 元 | " if capital else "")
                     + f"{crowd_txt} | {reason} |")
        if capital:
            L += ["", f"> 金额按总资产 {capital:,.0f} 元折算；实际下单以份额为准，"
                      "A 类腿（纳指/标普/黄金）申购 0.10%、赎回 0.50%。"]
    else:
        L += ["（六腿当前全部为满带，无需调整）"]

    L += [
        "",
        "## 三、六腿明细",
        "",
        "| 腿 | 角色 | 带 | 执行代码 | 档位 | 目标仓位 | 拥挤度 | 净申购率(季) | "
        "头部ETF合计份额 | 渗透率 | 最新季报 | 净值日 | 数据 |",
        "|---|---|---:|---|---:|---:|---:|---:|---:|---:|---|---|---|",
    ]
    for r in rows:
        tag = "✅" if (r["crowd"] is not None and r["last_anchor"] >= exp_anchor) else "⚠️"
        crowd_txt = "—" if r["crowd"] is None else f"{r['crowd']:.0%}"
        shares_txt = "—" if r["shares_now"] is None else f"{r['shares_now']:.0f} 亿份"
        ratio_txt = "—" if r["ratio_now"] is None else f"{r['ratio_now']:.3f}%"
        L.append(
            f"| {r['name']} | {r['role']} | {r['band']:g}pp | `{r['exec_code']}` | "
            f"{r['pos']:g} | {r['target_pp']:g}pp | {crowd_txt} | {fmt_pct(r['g_now'])} | "
            f"{shares_txt} | {ratio_txt} | {r['last_anchor']} | {r['nav_date']} | {tag} |")

    L += [
        "",
        "> 「拥挤度」= 该腿头部 ETF 合计份额季度净申购率在过去 750 个交易日中的分位；"
        "「净申购率(季)」= 63 个交易日的份额加权增长率。"
        "「渗透率」仅 A 股腿可算（头部 ETF 市值 ÷ 指数流通市值），其余腿无标的指数流通市值口径。",
        "",
        "## 四、头部 ETF 名单（按最新季报净资产前 N，R-01）",
        "",
        "| 腿 | 头部 ETF | 最新季报 |",
        "|---|---|---|",
    ]
    for r in rows:
        names = "、".join(f"`{c}` {r['head_names'].get(c, c)}" for c in r["head"])
        L.append(f"| {r['name']} | {names} | {r['last_anchor']} |")

    L += ["", "## 五、最近的档位变化", ""]
    any_hist = False
    for r in rows:
        h = history(ps[r["leg"]])
        if not h:
            continue
        any_hist = True
        L.append(f"- **{r['name']}**：" + "；".join(f"{d} {a}→{p:g}" for d, a, p in h))
    if not any_hist:
        L.append("（样本内六腿从未触发档位变化）")

    L += [
        "",
        "## 六、数据时效与须知",
        "",
        f"- **份额只到季报**：六腿最新季报锚点 "
        f"{min(r['last_anchor'] for r in rows) if rows else '—'}，季内资金变化要等下一期季报才进信号；"
        "净值日则近实时（QDII 滞后 1~2 日）。",
        "- **A500 的份额史仅 2024-12 起**（4 只头部 ETF 2024-10 才上市），此前该腿按「不执行」保持满带。",
        "- **拥挤 ≠ 必跌**：回测里它只在部分腿上降低回撤（美股腿显著、慢牛腿让渡收益），"
        "组合层不占优 —— 详见 `backtest/_v2_flow_bt.md`。",
        "- **与主策略的关系**：主策略给「该持多少 pp」，本变种给「是否临时打对折」，两者可叠加；"
        "与 V1（MA20 择时）正交，不要互相替代。",
        "",
        "> 机械规则输出，**非投资建议**。",
    ]

    with open(OUT_MD, "w", encoding="utf-8") as f:
        f.write("\n".join(L))
    with open(OUT_JSON, "w", encoding="utf-8") as f:
        json.dump({
            "strategy": flowrule.LABEL,
            "as_of": asof,
            "rule": {"flow_win": flowrule.CFG["flow_win"],
                     "pct_win": flowrule.CFG["pct_win"],
                     "crowd_hi": flowrule.CFG["crowd_hi"],
                     "crowd_lo": flowrule.CFG["crowd_lo"],
                     "pos_crowd": flowrule.CFG["pos_crowd"],
                     "pos_full": flowrule.CFG["pos_full"],
                     "trade_at": "T+1 净值", "cash_annual": flowrule.CASH_ANNUAL},
            "summary": {"band_pp": band_sum, "target_pp": crowd_sum,
                        "down": len(downs), "up": len(ups), "hold": len(hold),
                        "null": len(nulls), "stale": len(stale),
                        "capital": capital},
            "legs": rows,
            "history": {r["leg"]: history(ps[r["leg"]], 20) for r in rows},
        }, f, ensure_ascii=False, indent=1)

    print("报告:", OUT_MD)
    print("JSON:", OUT_JSON)
    print(f"as_of {asof} | 今日降档 {len(downs)} · 今日回档 {len(ups)} · "
          f"当前半带 {len(half)} · 维持 {len(hold)} · 不执行 {len(nulls)} | "
          f"合计 {crowd_sum:g}/{band_sum:g}pp")
    for r in downs:
        print(f"  [降档] {r['name']:8s} {r['band']:g}pp → {r['target_pp']:g}pp "
              f"（拥挤度 {r['crowd']:.0%}）")
    for r in ups:
        print(f"  [回档] {r['name']:8s} {r['band']:g}pp → {r['target_pp']:g}pp "
              f"（拥挤度 {r['crowd']:.0%}）")


if __name__ == "__main__":
    main()
