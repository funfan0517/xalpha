# -*- coding: utf-8 -*-
"""波动资产 · V4 变种（股债利差通道）· 每日操作 —— 沪深300 × 10Y 国债。

流程：
1. 刷新数据（中证官网沪深300 PE/全收益 + 东财 10Y 中债），失败沿用本地缓存并标注；
2. 复算 ERP 通道（`erp.erp_frame`）：MA / 上轨 / 下轨 / 状态机；
3. 输出：当前读数、当前状态与已持续天数、**目标沪深300 仓位**、
   按 `--capital` 换算的「下一交易日该下的那一单」、距退出线/切换线还有多远、
   距离下一次月末回锚的偏离；以及最近 8 段信号区间 + 近 60 日流水。

成交提醒：T 日盘后算出信号，**T+1 收盘前提交**才按 T+1 净值成交；
只有在**状态切换**（或月末偏离 > 阈值）时才动手，中间不要因为利差波动改单。

产物（相对本文件）：daily/_v4_erp_band_daily.md / _v4_erp_band_daily.json。
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
_V4 = os.path.dirname(_HERE)
sys.path.insert(0, _V4)

import erp  # noqa: E402

OUT_MD = os.path.join(_HERE, "_v4_erp_band_daily.md")
OUT_JSON = os.path.join(_HERE, "_v4_erp_band_daily.json")
RECENT_DAYS = 60
MAX_EPISODES = 8


def arg(name, default=None):
    if name in sys.argv:
        i = sys.argv.index(name)
        if i + 1 < len(sys.argv):
            return sys.argv[i + 1]
    return default


def _f(name, default=None):
    v = arg(name, default)
    try:
        return float(v)
    except (TypeError, ValueError):
        return default


def state_text(st):
    return {1: "**买入持有（+1）**", 0: "中性（0，未触发）", -1: "**卖出空仓（−1）**"}[int(st)]


def plan_row(name, w_now, w_target, capital):
    """按当前仓位与目标仓位给出金额动作。"""
    delta = (w_target - w_now) * capital
    if abs(delta) < capital * 1e-4:
        return f"| {name} | {w_now:.1%} | {w_target:.1%} | 0 | 不动 |"
    side = "买入" if delta > 0 else "卖出"
    return (f"| {name} | {w_now:.1%} | {w_target:.1%} | {delta:+,.0f} 元 | "
            f"{side} {abs(delta):,.0f} 元 |")


def main():
    capital = float(arg("--capital", 1000000) or 1000000)
    window = _f("--window", erp.CFG["window"])
    kk = _f("--k", erp.CFG["k"])
    band_hi = _f("--band-hi", erp.CFG["band_hi"])
    band_lo = _f("--band-lo", erp.CFG["band_lo"])
    trend_source = arg("--trend-source", erp.CFG["trend_source"])
    trend_window = _f("--trend-window", erp.CFG["trend_window"])
    eq_cheap = _f("--eq-cheap", erp.CFG["eq_cheap"])
    eq_neutral = _f("--eq-neutral", erp.CFG["eq_neutral"])
    eq_expan = _f("--eq-expan", erp.CFG["eq_expan"])
    hold = "--hold-neutral" in sys.argv
    refresh = "--refresh" in sys.argv

    df = erp.erp_frame(window=window, k=kk, refresh=refresh, band_hi=band_hi,
                      band_lo=band_lo, trend_source=trend_source,
                      trend_window=trend_window)
    df["target"] = erp.target_weight(df["state"], eq_cheap=eq_cheap, eq_neutral=eq_neutral,
                                     eq_expan=eq_expan, hold_neutral=hold).to_numpy()
    ready = df[df["ready"]]
    last = df.iloc[-1]
    st = int(last["state"])
    tgt = float(last["target"])
    days = erp._days_in_state(df["state"])                      # noqa: SLF001 - 内部工具
    eps = erp.episodes(ready)
    z = float(last["z"])
    if bool(last["trend_up"]):
        trend = "上行"
    elif bool(last["trend_dn"]):
        trend = "下行"
    else:
        trend = "横盘"

    # 下一对立信号还需满足的触发条件
    if st == 1:
        next_act = (f"z 降到 **≤ {band_lo:.2f}** 且趋势转**下行** → 卖出空仓，"
                    f"沪深300 降至 {eq_expan:.0%}")
        gap_txt = (f"当前 z = **{z:.2f}**（≥ {band_hi:.2f} 买入、≤ {band_lo:.2f} 卖出），"
                   f"趋势 **{trend}**；z 距卖出线还有 {(z - band_lo):+.2f}")
    elif st == -1:
        next_act = (f"z 升到 **≥ {band_hi:.2f}** 且趋势转**上行** → 买入持有，"
                    f"沪深300 加到 {eq_cheap:.0%}")
        gap_txt = (f"当前 z = **{z:.2f}**（≥ {band_hi:.2f} 买入、≤ {band_lo:.2f} 卖出），"
                   f"趋势 **{trend}**；z 距买入线还有 {(z - band_hi):+.2f}")
    else:
        next_act = (f"z ≥ **{band_hi:.2f}** 且趋势**上行** → 买入持有（沪深300 {eq_cheap:.0%}）；"
                    f"z ≤ **{band_lo:.2f}** 且趋势**下行** → 卖出空仓（{eq_expan:.0%}）")
        gap_txt = (f"当前 z = **{z:.2f}**，趋势 **{trend}**；"
                   f"距买入线 {(z - band_hi):+.2f} / 距卖出线 {(z - band_lo):+.2f}")

    # 当前仓位：默认 = **上一交易日的目标位**（即「已按上期信号持仓」），
    # 于是「不动 / 今天要动多少」只在信号切换的那一天才非零。真实持仓用 --hold 覆盖。
    w_eq = _f("--hold", None)
    w_eq = float(w_eq) if w_eq is not None else float(df["target"].iloc[-2])
    pe_now = float(last["tr"])
    plan_pp = (tgt - w_eq) * capital

    eps_rows = eps[:MAX_EPISODES]
    flow = ready.tail(RECENT_DAYS)

    L = [
        f"# 波动资产 · V4（股债利差通道）· 每日操作（{df.index[-1]:%Y-%m-%d}）",
        f"> 标的：**沪深300**（信号 = 中证 000300 的 PE；回测股票腿 = 全收益 H00300）× "
        f"**10Y 国债**（东财日频收益率 → 合成国债指数）。",
        f"> 通道：ERP = 100/PE − 10Y，**{window:.0f} 日**滚动 MA ± {kk:g}σ；"
        f"再把**下轨→上轨归一化到 0→1（z）**。买入 = z≥**{band_hi:.2f}** 且趋势**上行**；"
        f"卖出 = z≤**{band_lo:.2f}** 且趋势**下行**（趋势源 {trend_source}，窗口 {trend_window:.0f} 日）。"
        f"**T 日盘后算信号 → T+1 收盘净值成交。**",
        f"> 仓位：买入 {eq_cheap:.0%} / 中性 {eq_neutral:.0%} / 卖出 {eq_expan:.0%}"
        f"（沪深300 占比 of 股债合计，总资产 {capital:,.0f} 元）"
        + ("；中性态维持上期仓位。" if hold else "。"),
        "> 机械规则输出，**非投资建议**。",
        "",
        "## 1. 今日读数",
        "",
        "| 项 | 数值 |",
        "|---|---|",
        f"| 沪深300 收盘（价格指数） | {last['close']:.2f} |",
        f"| 沪深300 滚动 PE | {last['pe']:.2f} |",
        f"| 盈利收益率 100/PE | {100.0 / last['pe']:.2f}% |",
        f"| 10Y 国债收益率 | {last['y10']:.3f}% |",
        f"| **股债利差 ERP** | **{last['erp']:.2f}%** |",
        f"| 通道均线 MA | {last['ma']:.2f}% |",
        f"| 上轨（MA+{kk:g}σ） | {last['upper']:.2f}% |",
        f"| 下轨（MA−{kk:g}σ） | {last['lower']:.2f}% |",
        f"| 滚动标准差 σ | {last['std']:.3f}pp |",
        f"| **归一化位置 z** | **{z:.2f}**（0=下轨/最贵，1=上轨/最便宜） |",
        f"| **趋势** | {trend}（源 {trend_source} / 窗口 {trend_window:.0f} 日） |",
        f"| **当前状态** | {state_text(st)} · 已持续 **{days}** 个交易日 |",
        "",
        "## 2. 下一交易日的动作",
        "",
        f"- **目标沪深300 仓位 = {tgt:.0%}**（= {capital * tgt:,.0f} 元），债券 = "
        f"{1 - tgt:.0%}（= {capital * (1 - tgt):,.0f} 元）。",
        f"- 当前按 {w_eq:.0%} 计 → "
        + (f"**{'买入' if plan_pp > 0 else '卖出'} {abs(plan_pp):,.0f} 元沪深300**"
           f"（债券腿反向 {abs(plan_pp):,.0f} 元）" if abs(plan_pp) > capital * 1e-4
           else "**不动**（已在目标位）"),
        f"- 触发条件 / 下一步：{next_act}",
        f"- {gap_txt}",
        "- 成交纪律：**T+1 收盘前提交**（场外基金 15:00 前）拿到 T+1 净值；"
        "状态没切换就不要因为利差日内波动改单。",
        "",
        "| 腿 | 当前 | 目标 | 动作金额 | 买卖 |",
        "|---|---:|---:|---:|---|",
        plan_row("沪深300（全收益指数基金/ETF 联接）", w_eq, tgt, capital),
        plan_row("债券（10Y 国债 / 中长期纯债）", 1 - w_eq, 1 - tgt, capital),
        "",
        "## 3. 历史信号区间（最近 %d 段）" % MAX_EPISODES,
        "",
        "| 状态 | 起 | 止 | 交易日 | ERP 均值 | 沪深300 起点 | 终点 | 区间涨跌 |",
        "|---|---|---|---:|---:|---:|---:|---:|",
    ]
    for e in eps_rows:
        L.append(f"| {'买入持有' if e['kind'] == 1 else '卖出空仓'} | {e['start']} | {e['end']} | "
                 f"{e['days']} | {e['erp_mean']:.2f}% | {e['px_start']:.0f} | "
                 f"{e['px_end']:.0f} | {erp.fmt_pct(e['px_end'] / e['px_start'] - 1.0)} |")

    L += [
        "",
        "## 4. 近 %d 个交易日流水" % RECENT_DAYS,
        "",
        "| 日期 | 收盘 | PE | 10Y | ERP | MA | 上轨 | 下轨 | z | 趋势 | 状态 | 目标仓位 |",
        "|---|---:|---:|---:|---:|---:|---:|---:|---:|---|---|---:|",
    ]
    for d, r in flow.iterrows():
        if bool(r["trend_up"]):
            tr_txt = "↑"
        elif bool(r["trend_dn"]):
            tr_txt = "↓"
        else:
            tr_txt = "—"
        L.append(f"| {d:%Y-%m-%d} | {r['close']:.0f} | {r['pe']:.2f} | {r['y10']:.3f} | "
                 f"{r['erp']:.2f} | {r['ma']:.2f} | {r['upper']:.2f} | {r['lower']:.2f} | "
                 f"{r['z']:.2f} | {tr_txt} | {int(r['state']):+d} | {r['target']:.0%} |")

    L += [
        "",
        "## 5. 提醒",
        "",
        "- **买入持有 ≠ 立刻上涨**：回测里买入持有态 60 日平均收益有限、上涨概率约 6 成，"
        "但 10% 分位仍明显为负；买入持有态持续一年以上是常态（见 `../backtest/_v4_erp_band_bt.md` §3/§4）。",
        "- **这条规则不产生稳定超额**——它买的是「仓位纪律」（极便宜且趋势转好才重仓、极贵且趋势转差才降仓）"
        "和略小的回撤，别指望它择时赚钱。",
        "- PE 口径（滚动 vs 静态）只平移整条曲线，通道是相对自己的 MA±2σ，信号基本不变；"
        "但报告的 ERP 绝对读数会变。",
        "- 触发是「百分位 + 趋势」双条件：z 到了还不够，必须趋势同方向确认，避免接飞刀 / 过早下车。",
        "- 数据日期：沪深300 至 "
        + erp.SOURCE.get("hs300", "—") + "；10Y 至 " + erp.SOURCE.get("bond", "—") + "。",
        "",
        "> 机械规则输出，**非投资建议**。",
    ]

    with open(OUT_MD, "w", encoding="utf-8") as f:
        f.write("\n".join(L))
    with open(OUT_JSON, "w", encoding="utf-8") as f:
        json.dump({
            "strategy": erp.LABEL, "as_of": df.index[-1].date().isoformat(),
            "rule": {"window": window, "k": kk,
                     "band_hi": band_hi, "band_lo": band_lo,
                     "trend_source": trend_source, "trend_window": trend_window,
                     "eq_cheap": eq_cheap, "eq_neutral": eq_neutral, "eq_expan": eq_expan,
                     "hold_neutral": hold, "rebal_freq": erp.CFG["rebal_freq"],
                     "trade_at": "T+1 收盘净值"},
            "readings": {"close": float(last["close"]), "tr": pe_now,
                         "pe": float(last["pe"]), "y10": float(last["y10"]),
                         "erp": float(last["erp"]), "ma": float(last["ma"]),
                         "upper": float(last["upper"]), "lower": float(last["lower"]),
                         "std": float(last["std"]), "z": z, "trend": trend,
                         "state": st, "days_in_state": days,
                         "target_equity": tgt},
            "action": {"capital": capital, "current_equity": w_eq,
                       "delta_amount": plan_pp, "next_trigger": next_act},
            "episodes": eps_rows, "source": dict(erp.SOURCE), "warn": list(erp._WARN),
        }, f, ensure_ascii=False, indent=1)

    print("报告:", OUT_MD)
    print("JSON:", OUT_JSON)
    print(f"{df.index[-1]:%Y-%m-%d} · PE {last['pe']:.2f} · 10Y {last['y10']:.3f}% · "
          f"ERP {last['erp']:.2f}% · MA {last['ma']:.2f} · 上轨 {last['upper']:.2f} · "
          f"下轨 {last['lower']:.2f}")
    print(f"状态 {st:+d}（已持续 {days} 日）· z={z:.2f} · 趋势 {trend} · 目标沪深300 {tgt:.0%} · 动作 {plan_pp:+,.0f} 元")
    print(f"下一步：{next_act}")
    if erp._WARN:                                          # noqa: SLF001
        print("告警:", erp._WARN[:3])


if __name__ == "__main__":
    main()
