# -*- coding: utf-8 -*-
"""波动资产 · V6 变种（估值分位智能定投）· 每日/每月操作 —— 沪深300。

流程：
1. 读本地缓存（沪深300 全收益 H00300 + 滚动 PE 000300），无网络请求、不改共享缓存；
2. 复算分位（`dca.pe_percentile`）：`pct` / `n_eff` / warmup；
3. 输出：当前 PE 与分位、档位与倍数、**本月该投多少钱**（按 `--b` 或 `--capital` 折算）、
   距上下档位的临界 PE、最近 12 个决策月的流水、分位漂移史。

口径提醒：分位是**日频**更新的（PE 每天都有），但**只在每月第 1 个交易日决策**。
所以月中看到分位变了不要动手 —— 这是规则的一部分，不是延迟。

产物（相对本文件）：daily/_v6_valuation_dca_daily.md / _v6_valuation_dca_daily.json。
机械规则输出，非投资建议。
"""
import json
import os
import sys

import pandas as pd

try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:  # noqa: BLE001
    pass

_HERE = os.path.dirname(os.path.abspath(__file__))
_V6 = os.path.dirname(_HERE)
sys.path.insert(0, _V6)

import dca  # noqa: E402

OUT_MD = os.path.join(_HERE, "_v6_valuation_dca_daily.md")
OUT_JSON = os.path.join(_HERE, "_v6_valuation_dca_daily.json")
RECENT_MONTHS = 12


def arg(name, default=None):
    if name in sys.argv:
        i = sys.argv.index(name)
        if i + 1 < len(sys.argv):
            return sys.argv[i + 1]
    return default


def _f(name, default=None):
    try:
        return float(arg(name, default))
    except (TypeError, ValueError):
        return default


def crit_pe(df, p, target_pct):
    """使分位恰好等于 `target_pct` 的 PE 值（在**当前 n_eff 窗口**内二分求解）。

    「距下一档还有多远」比「分位是多少」更好用：它把分位翻译回 PE 点位。
    """
    pe = df["pe"].dropna()
    n_eff = min(int(p["W"]), len(pe))
    if n_eff < int(p["MIN_SAMP"]):
        return None
    win = pe.to_numpy()[-n_eff:]
    lo, hi = float(win.min()), float(win.max())
    for _ in range(60):
        mid = (lo + hi) / 2.0
        q = float((win <= mid).sum()) / n_eff
        if q < target_pct:
            lo = mid
        else:
            hi = mid
    return (lo + hi) / 2.0


def main():
    b = _f("--b", None)
    capital = _f("--capital", None)
    p = dca.params()
    if b is None:
        # 未给 --b 时，用「月度额度 = 总资产的 1/120」反推（10 年投完一轮的量级），只作默认值
        b = (capital / 120.0 / p["BUDGET_MULT"]) if capital else p["B"]
    p = dict(p, B=float(b))

    full = dca.load_frame()
    pc = dca.pe_percentile(full["pe"], p["W"], p["MIN_SAMP"])
    r = dca.latest(full, p)
    pv = r["pct"]
    ti = r["tier"]

    dec = dca.decision_mask(full.index, p["DCA_RULE"], p["DAY_OF_MONTH"])
    dec_days = list(full.index[dec.to_numpy(dtype=bool)])
    last_dec = dec_days[-1]
    # 下一个决策日：交易日历未知，按「下个自然月」推并把周末顺延，报告里标「预计」
    if p["DCA_RULE"] == "first_trading_day":
        nxt = last_dec + pd.offsets.BMonthBegin(1)
    else:
        nxt = (last_dec + pd.offsets.MonthBegin(1)) + pd.Timedelta(
            days=int(p["DAY_OF_MONTH"]) - 1)
        while nxt.weekday() >= 5:
            nxt += pd.Timedelta(days=1)

    edges = list(p["TIER_EDGES"])
    mults = p["TIER_MULT"]
    pe_now = r["pe"]
    up_pe = crit_pe(full, p, edges[ti]) if ti is not None and ti < len(edges) else None
    dn_pe = crit_pe(full, p, edges[ti - 1]) if ti is not None and ti > 0 else None

    # 最近 12 个决策月流水
    rows = []
    pct_s = pc["pct"]
    for d in dec_days[-RECENT_MONTHS:]:
        pvv = pct_s.loc[d]
        ok = pvv == pvv
        t = dca.tier_index(float(pvv), edges) if ok else None
        rows.append(dict(date=d, pe=float(full.loc[d, "pe"]),
                         pct=(float(pvv) if ok else None),
                         n_eff=int(pc.loc[d, "n_eff"]),
                         tier=(dca.TIER_NAMES[t] if t is not None else "冷启动"),
                         mult=dca.mult_of(float(pvv) if ok else None, p),
                         amount=dca.mult_of(float(pvv) if ok else None, p) * p["B"]))

    # 分位漂移：把当年的分位按季度取均值，看「同一 PE 值的分位怎么变」
    mp = dca.month_pct_series(full, p)
    drift = []
    for y in sorted({d.year for d in mp.index})[-8:]:
        s = mp.loc[str(y)]
        if len(s):
            drift.append(dict(year=y, pct_mean=float(s.mean()),
                              pct_end=float(s.iloc[-1]), n=len(s)))

    target = r["mult"] * p["B"]
    L = [
        f"# 波动资产 · V6（估值分位智能定投）· 操作提示（{full.index[-1]:%Y-%m-%d}）",
        "> 标的：**沪深300**（信号 = 中证 000300 滚动 PE 的历史分位；价格 = 全收益 H00300）。",
        f"> 分位窗口 `W={p['W']}` · `MIN_SAMP={p['MIN_SAMP']}` · 五档 "
        f"{' / '.join(f'{m:g}×' for m in mults)}（阈值 "
        f"{' / '.join(f'{e:.0%}' for e in edges)}，左闭右开）",
        f"> 月额度 `BUDGET_M = 2B = {2 * p['B']:,.0f} 元`（B = **{p['B']:,.0f}** 元）· "
        f"决策日 = `{p['DCA_RULE']}` · T 日盘后算、**T+1 收盘成交**",
        "> 机械规则输出，**非投资建议**。",
        "",
        "## 1. 今日读数",
        "",
        "| 项 | 数值 |",
        "|---|---|",
        f"| 沪深300 全收益指数 | {r['close']:.2f} |",
        f"| 滚动 PE(TTM) | **{pe_now:.2f}**（{r['pe_date']:%Y-%m-%d}） |",
        f"| 历史分位 | **{pv:.1%}**（n_eff={r['n_eff']}"
        f"{'· **warmup 段**：10 年窗口未满，分位含义会漂移' if r['warmup'] else '，10 年窗口已成立'}） |",
        f"| 档位 | **{r['tier_name']}** → 倍数 **{r['mult']:g}×** |",
        f"| 本月目标投入 | **{target:,.0f} 元**（= {r['mult']:g} × {p['B']:,.0f}） |",
        f"| 距上一档（更低分位） | "
        + (f"PE 需跌破 **{dn_pe:.2f}**（{edges[ti - 1]:.0%} 分位）→ 倍数升到 {mults[ti - 1]:g}×"
           if dn_pe else "已是最低档"),
        "| 距下一档（更高分位） | "
        + (f"PE 升破 **{up_pe:.2f}**（{edges[ti]:.0%} 分位）→ 倍数降到 {mults[ti + 1]:g}×"
           if up_pe else "已是最高档"),
        "",
        "## 2. 这个月该怎么做",
        "",
        f"- 最近决策日：**{last_dec:%Y-%m-%d}**；下一个决策日：**预计 {nxt:%Y-%m-%d}**"
        "（当月第一个交易日，以交易日历为准）。",
        f"- 若今天就是决策日 → **下一个交易日收盘前申购 {target:,.0f} 元**"
        f"（沪深300 指数基金 / ETF 联接）。",
        f"- 若今天不是决策日 → **什么也不做**。分位月中会变，但规则只在月度决策日动手；"
        "提前或延后都会破坏「五档」的定义。",
        f"- 本月额度上限 = `BUDGET_M + 现金池 = {2 * p['B']:,.0f} + 存量`，"
        "所以 2× 档（深度低估）刚好能用完当月到账，不会透支未来。",
        "",
        "> `--b` 未指定时的默认值 = 总资产 / 120 / 2"
        + (f"（按 `--capital {capital:,.0f}` 折算 → B = {p['B']:,.0f}）" if capital else
           f"（= `CFG['B'] = {p['B']:,.0f}`）")
        + "。**B 只影响绝对金额，不改变任何收益率**（份额化净值对 B 是恒等的）。",
        "",
        f"## 3. 最近 {RECENT_MONTHS} 个决策月",
        "",
        "| 决策日 | PE | 分位 | n_eff | 档位 | 倍数 | 目标投入 |",
        "|---|---:|---:|---:|---|---:|---:|",
    ]
    for x in rows:
        p_txt = "—" if x["pct"] is None else f"{x['pct']:.1%}"
        L.append(f"| {x['date']:%Y-%m-%d} | {x['pe']:.2f} | {p_txt} | "
                 f"{x['n_eff']} | {x['tier']} | {x['mult']:g}× | {x['amount']:,.0f} |")

    L += [
        "",
        "## 4. 分位漂移（同一 PE 值在不同年份的分位不可比）",
        "",
        "| 年 | 分位均值 | 年末分位 | 月末样本 |",
        "|---|---:|---:|---:|",
    ]
    for x in drift:
        L.append(f"| {x['year']} | {x['pct_mean']:.1%} | {x['pct_end']:.1%} | {x['n']} |")

    L += [
        "",
        "> `n_eff` 在 2022-02 之前从 500 一路扩到 2520，所以「同一 PE 值得出的分位」逐年不同。"
        "跨年比较多空结论时必须检查 `n_eff`，否则会得出「2016 年 PE 12 倍算高估、2026 年算低估」"
        "这类自相矛盾的判断。",
        "",
        "## 5. 提醒",
        "",
        "- **低估值陷阱**：2011-2014 沪深300 PE 长期 10~12 倍、分位长期 <20%，规则会连续加倍买入，"
        "而指数横盘阴跌 3 年。分位法假设「均值回复」，盈利周期整体下移时它会长期贴底而价格不涨。",
        "- **盈利断崖导致 PE 假高**：2020Q1、2022Q2 的 EPS 骤降使 PE 被动抬升 → 分位跳高 → 停投，"
        "而那恰是价格底部。这是分位法最典型的反向信号，**无法用 PE 单锚消除**（需 PB 双锚或趋势确认）。",
        "- **高估档停投 ≠ 看空**：它只是「这个月不加钱」。已有持仓不动，止盈（R-A04）默认关闭。",
        "- 回测结论与对照组（G0/G1/G4）见 `../backtest/_v6_valuation_dca_bt.md`；"
        "**本策略对 G0（满额定投）不占优**，它买的是「跌得多投得多」的纪律与更小的回撤。",
        f"- 数据：{dca.SOURCE.get('pe')}｜{dca.SOURCE.get('tr')}",
        "",
        "> 机械规则输出，**非投资建议**。",
    ]

    with open(OUT_MD, "w", encoding="utf-8") as f:
        f.write("\n".join(L))
    with open(OUT_JSON, "w", encoding="utf-8") as f:
        json.dump({
            "strategy": dca.LABEL, "as_of": full.index[-1].date().isoformat(),
            "rule": {"W": p["W"], "MIN_SAMP": p["MIN_SAMP"],
                     "tier_edges": edges, "tier_mult": mults,
                     "dca_rule": p["DCA_RULE"], "trade_at": "T+1 收盘",
                     "sell_enabled": p["SELL_ENABLED"], "reinvest": p["REINVEST"]},
            "readings": {"close": r["close"], "pe": pe_now, "pe_date": r["pe_date"].isoformat(),
                         "pct": pv, "n_eff": r["n_eff"], "warmup": r["warmup"],
                         "tier": r["tier_name"], "mult": r["mult"]},
            "action": {"B": p["B"], "budget_m": 2 * p["B"], "target_amount": target,
                       "last_decision": last_dec.date().isoformat(),
                       "next_decision_est": nxt.date().isoformat(),
                       "crit_pe_down": dn_pe, "crit_pe_up": up_pe},
            "recent": [{**x, "date": x["date"].date().isoformat()} for x in rows],
            "drift": drift, "source": dict(dca.SOURCE),
        }, f, ensure_ascii=False, indent=1)

    print("报告:", OUT_MD)
    print("JSON:", OUT_JSON)
    print(f"{full.index[-1]:%Y-%m-%d} · PE {pe_now:.2f}（{r['pe_date']:%Y-%m-%d}）· "
          f"分位 {pv:.1%}（n_eff={r['n_eff']}{'·warmup' if r['warmup'] else ''}）· "
          f"{r['tier_name']} {r['mult']:g}× → 本月 {target:,.0f} 元")
    print(f"最近决策日 {last_dec:%Y-%m-%d} · 下一个 {nxt:%Y-%m-%d}")


if __name__ == "__main__":
    main()
