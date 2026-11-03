# -*- coding: utf-8 -*-
"""波动资产 · V4（ERP 两带滞回）· 每日操作 —— 沪深300 × 10Y 国债。

流程：
1. 读缓存复算 ERP 通道与两带状态机（`ladder.frame`），显式 `--refresh` 才联网；
2. 输出：今日读数、所在带与「本带操作 vs 实际目标仓位」、
   **下一次跨带会落到多少仓**、按 `--capital` 换算的下一交易日那一单、
   最近 12 次换仓记录、近 60 日内的换仓日（同仓位的跨带/逐日重复行不展示）；
3. 换死区 `--tol 0.05`，换带界 `--edges 0.8,0.2`。
   读法固定为 `hold_left`（读法 A，用户 2026-11-03 确认执行）；`--mode hold_new` 只作回测对照。

⚠ **本策略只在跨带时调仓（14 年 15 次，约 1 次/年）**：换手低，场外 0.5% 赎回费不再致命
（场外 vs 场内只差 0.70pp/年，见 `../backtest/_v4_erp_band_bt.md` §5）。
真正的代价是**回撤**（-38.96%，≈ 静态 80/20）—— 满仓时债券腿清零。

成交提醒：T 日盘后算信号，**T+1 收盘前提交**才按 T+1 净值成交。

产物（相对本文件）：daily/_v4_erp_band_daily.md / _v4_erp_band_daily.json。
机械规则输出，非投资建议。
"""
import json
import os
import sys

try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:  # noqa: BLE001
    pass

_HERE = os.path.dirname(os.path.abspath(__file__))
_PKG = os.path.dirname(_HERE)
_VL = os.path.dirname(_PKG)
for _p in (_PKG, _VL):
    if _p not in sys.path:
        sys.path.insert(0, _p)

import erp  # noqa: E402
import ladder as L  # noqa: E402

OUT_MD = os.path.join(_HERE, "_v4_erp_band_daily.md")
OUT_JSON = os.path.join(_HERE, "_v4_erp_band_daily.json")
RECENT_DAYS = 60
MAX_CROSS = 12


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


def plan_row(name, w_now, w_target, capital):
    delta = (w_target - w_now) * capital
    if abs(delta) < capital * 1e-4:
        return f"| {name} | {w_now:.1%} | {w_target:.1%} | 0 | 不动 |"
    side = "买入" if delta > 0 else "卖出"
    return (f"| {name} | {w_now:.1%} | {w_target:.1%} | {delta:+,.0f} 元 | "
            f"{side} {abs(delta):,.0f} 元 |")


def src_band(target, weights, cur):
    """离带执行时，这一单的仓位来自哪个带（hold_left 的关键信息）。找不到就 None。"""
    for j, x in enumerate(weights):
        if x is not None and j != cur and abs(float(x) - target) < 1e-9:
            return j
    return None


def next_cross(b, z, up, dn, weights, mode, held):
    """下一次跨带的两种可能 -> 文本（含执行后仓位；维持带 = 不动）。"""
    out = []
    for bound, nb, word in ((up, b - 1, "升破"), (dn, b + 1, "跌破")):
        if bound is None:
            continue
        x = weights[b] if mode == "hold_left" else weights[nb]
        w_new = float(held) if x is None else float(x)
        act = ("**不动**（该带的操作是「维持」）" if abs(w_new - held) < 1e-9
               else f"目标仓位 **{w_new:.0%}**（{'加仓' if w_new > held else '减仓'}）")
        out.append(f"z {word} **{bound:.2f}**（还差 {abs(bound - z):.2f}）→ 跨进 "
                   f"B{nb} {L.BAND_NAME[nb]}，执行后{act}")
    return out


def main():
    capital = float(arg("--capital", 1000000) or 1000000)
    mode = arg("--mode", L.CFG["ladder_mode"])
    tol = _f("--tol", L.CFG["band_tol"])
    edges_s = arg("--edges", None)
    edges = tuple(float(x) for x in edges_s.split(",")) if edges_s else None
    refresh = "--refresh" in sys.argv

    r = L.latest_reading(refresh=refresh, mode=mode, edges=edges, band_tol=tol)
    df = r["df"]
    e = tuple(edges or L.CFG["edges"])
    w = tuple(L.CFG["weights"])
    st, tgt, z, b = r["state"] if "state" in r else None, r["target"], r["z"], r["band"]

    w_now = _f("--hold", None)
    w_now = float(w_now) if w_now is not None else float(df["target"].iloc[-2])
    amt = (tgt - w_now) * capital

    nxt = next_cross(b, z, r["up"], r["dn"], w, mode, tgt)
    ready = df[df["ready"]].copy()
    # 换仓判定必须在**全量**序列上做：只在切片窗口内 diff，首行会被当成「假变化」
    ready["_chg"] = ready["target"].ne(ready["target"].shift())
    # 跨带 ≠ 一定换仓：只列**实际目标仓位变过**的跨带，同仓位的跨带不占行
    crosses = ready[ready["cross"] & ready["_chg"]].tail(MAX_CROSS)
    plan_txt = "维持（不动）" if w[b] is None else f"{w[b]:.0%}"

    L_ = [
        f"# 波动资产 · V4（ERP 两带滞回）· 每日操作（{df.index[-1]:%Y-%m-%d}）",
        f"> 标的：**沪深300**（信号 = 中证 000300 的 PE；回测股票腿 = 全收益 H00300）× "
        f"**10Y 国债**（东财日频收益率 → 合成国债指数）。",
        f"> 通道：ERP = 100/PE − 10Y，**{L.CFG['window']} 日**滚动 MA ± {L.CFG['k']:g}σ，"
        "下轨→上轨归一化成 z（0=最贵，1=最便宜）。",
        f"> 两带：z ≥ **{e[0]:.2f}** 满仓 {L.w_text(w[0])} · "
        f"{e[1]:.2f} ≤ z < {e[0]:.2f} **维持（不动）** · z < **{e[1]:.2f}** 清仓 "
        f"{L.w_text(w[len(w) - 1])}；**从某个带内离开时执行带内的操作**"
        f"（读法 `{mode}`，死区 {tol:g}）。",
        f"> 仓位 = 沪深300 占比 of 股债合计，总资产 {capital:,.0f} 元。**T+1 收盘净值成交。**",
        "> ⚠ 换手很低（回测 14.2 年 15 次），费率不是主要矛盾；代价是**回撤**"
        "（-38.96%，满仓时债券腿清零），详见 `../backtest/_v4_erp_band_bt.md`。",
        "> 机械规则输出，**非投资建议**。",
        "",
        "## 1. 今日读数",
        "",
        "| 项 | 数值 |",
        "|---|---|",
        f"| 沪深300 收盘（价格指数） | {r['df'].iloc[-1]['close']:.2f} |",
        f"| 沪深300 滚动 PE | {r['pe']:.2f} |",
        f"| 10Y 国债收益率 | {r['y10']:.3f}% |",
        f"| **股债利差 ERP** | **{r['erp']:.2f}%** |",
        f"| 通道均线 MA / 上轨 / 下轨 | {r['ma']:.2f} / {r['upper']:.2f} / {r['lower']:.2f}% |",
        f"| **归一化位置 z** | **{z:.2f}** |",
        f"| **所在带** | **B{b} {r['band_name']}**（本带操作：{plan_txt}） |",
        f"| **实际目标仓位** | **{tgt:.0%}**（{r['side']}） |",
        f"| 已在本带 | {r['days_in_band']} 个交易日 |",
        "",
        "## 2. 下一交易日的动作",
        "",
        f"- **目标沪深300 仓位 = {tgt:.0%}**（= {capital * tgt:,.0f} 元），债券 = "
        f"{1 - tgt:.0%}（= {capital * (1 - tgt):,.0f} 元）。",
        f"- 当前按 {w_now:.0%} 计 → "
        + (f"**{'买入' if amt > 0 else '卖出'} {abs(amt):,.0f} 元沪深300**"
           f"（债券腿反向 {abs(amt):,.0f} 元）" if abs(amt) > capital * 1e-4
           else "**不动**（已在目标位）"),
        "- **下一次跨带会怎样**：",
    ]
    for x in nxt:
        L_.append(f"  - {x}")
    if not nxt:
        L_.append("  - 已到刻度两端，没有更极端的带")
    L_ += [
        "- 成交纪律：**T+1 收盘前提交**拿到 T+1 收盘净值；只在跨带那天动手，带内不动。",
        "",
        "| 腿 | 当前 | 目标 | 动作金额 | 买卖 |",
        "|---|---:|---:|---:|---|",
        plan_row("沪深300（全收益 ETF）", w_now, tgt, capital),
        plan_row("债券（10Y 国债 / 中长期纯债）", 1 - w_now, 1 - tgt, capital),
        "",
        f"## 3. 最近 {MAX_CROSS} 次换仓（跨带且目标仓位变化）",
        "",
        "| 日期 | z | 跨到 | 执行的仓来自 | 执行后目标 | 方向 |",
        "|---|---:|---|---|---:|---|",
    ]
    for ts, x in crosses.iterrows():
        j = src_band(float(x["target"]), w, int(x["band"]))
        src = "—" if j is None else f"离开 B{j} {L.BAND_NAME[j]}（{L.w_text(w[j])}）"
        L_.append(f"| {ts:%Y-%m-%d} | {x['z']:.2f} | B{int(x['band'])} "
                  f"{L.BAND_NAME[int(x['band'])]} | {src} | "
                  f"{x['target']:.0%} | "
                  f"{'加仓' if x['event'] > 0 else ('减仓' if x['event'] < 0 else '不再变动')} |")

    # 逐日流水只留**换仓日**：目标仓位没变的日子（上次 50% 这次还是 50%）不展示
    flow = ready.tail(RECENT_DAYS)
    flow = flow[flow["_chg"]]
    L_ += ["", f"## 4. 近 {RECENT_DAYS} 个交易日内的换仓日", "",
           "| 日期 | 收盘 | ERP | z | 带 | 本带操作 | 实际目标 |",
           "|---|---:|---:|---:|---|---:|---:|"]
    if len(flow):
        for ts, x in flow.iterrows():
            L_.append(f"| {ts:%Y-%m-%d} | {x['close']:.0f} | {x['erp']:.2f} | {x['z']:.2f} | "
                      f"B{int(x['band'])} | {L.w_text(w[int(x['band'])])} | {x['target']:.0%} |")
    else:
        last_ = ready.iloc[-1]
        L_ += ["| — | — | — | — | — | — | — |",
               "",
               f"> 近 {RECENT_DAYS} 个交易日内**未换仓**：一直停在 "
               f"B{int(last_['band'])} {L.BAND_NAME[int(last_['band'])]}"
               f"（已 {r['days_in_band']} 个交易日），实际目标 {last_['target']:.0%} 未变。"]

    L_ += [
        "",
        "## 5. 提醒",
        "",
        "- **「实际目标」常与本带操作不一致**：执行规则固定为读法 A（`hold_left`，"
        "「从某个带内离开时执行带内的操作」），所以满仓带里平均只持 89%（等 z 跌回 0.80 之下才买）、"
        "清仓带里平均还持 74%（等 z 升回 0.20 之上才卖）。这不是 bug，是「离带才动手」的直接推论；"
        "回测里它比「按当前带定仓」高 5.00pp/年（`../backtest/_v4_erp_band_bt.md` §3/§9）。",
        "- **换手极低**（14.2 年 15 次），所以场外 0.5% 赎回费不再是生死线（场外 vs ETF 差 0.70pp/年）；"
        "真正的代价是**回撤**：-38.96%，与静态 80/20 同级，比旧口径 80/50/20（-27.15%）大 12pp。",
        "- 带边界 0.8/0.2 两头都往下掉（整体平移 −0.10 → +6.81%，+0.10 → +5.39%），"
        "中间是孤峰 —— 别调参。死区 0.03~0.05 只当防抖。",
        "- 数据日期：沪深300 至 " + erp.SOURCE.get("hs300", "—")
        + "；10Y 至 " + erp.SOURCE.get("bond", "—") + "。",
        "",
        "> 机械规则输出，**非投资建议**。",
    ]

    with open(OUT_MD, "w", encoding="utf-8") as f:
        f.write("\n".join(L_))
    with open(OUT_JSON, "w", encoding="utf-8") as f:
        json.dump({
            "strategy": L.LABEL, "as_of": df.index[-1].date().isoformat(),
            "rule": {"edges": list(e), "weights": list(w), "ladder_mode": mode,
                     "band_tol": tol, "window": L.CFG["window"], "k": L.CFG["k"],
                     "trade_at": "T+1 收盘净值"},
            "readings": {"close": r["df"].iloc[-1]["close"], "pe": r["pe"], "y10": r["y10"],
                         "erp": r["erp"], "ma": r["ma"], "upper": r["upper"],
                         "lower": r["lower"], "z": z, "band": b,
                         "band_name": r["band_name"], "plan_weight": r["plan_w"],
                         "target_equity": tgt, "days_in_band": r["days_in_band"],
                         "next_up": r["up"], "next_dn": r["dn"]},
            "action": {"capital": capital, "current_equity": w_now,
                       "delta_amount": amt, "next_cross": nxt},
            "crosses": [{"date": ts.date().isoformat(), "z": float(x["z"]),
                         "band": int(x["band"]), "target": float(x["target"]),
                         "event": int(x["event"])} for ts, x in crosses.iterrows()],
            "source": dict(erp.SOURCE),
        }, f, ensure_ascii=False, indent=1, default=float)

    print("报告:", OUT_MD)
    print("JSON:", OUT_JSON)
    print(f"{df.index[-1]:%Y-%m-%d} · ERP {r['erp']:.2f}% · z={z:.2f} · "
          f"B{b} {r['band_name']}（本带操作 {plan_txt}）· 实际目标 {tgt:.0%} · "
          f"动作 {amt:+,.0f} 元")
    for x in nxt:
        print("  下次:", x)


if __name__ == "__main__":
    main()
