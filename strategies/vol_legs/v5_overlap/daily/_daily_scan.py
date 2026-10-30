# -*- coding: utf-8 -*-
"""波动资产 · V5 变种（两线重合买点）· 每日操作 —— 沪深300全收益 × 二级债基指数。

流程：
1. 刷新数据（中证 H00300 全收益 + 8 只老债基累计净值），失败沿用本地缓存并标注；
2. 复算两线（同锚归一 1000）与 R = 红/蓝，套规则：`R ≤ 1 + tol` → 买，持有一年后卖；
3. 输出：今天两条线在哪、R 距买点还差多少、当前是空仓还是持仓中、
   持仓还剩多少交易日、下一交易日的动作与金额，以及历史买点表 + 近 60 日流水。

成交提醒：T 日盘后算出信号，**T+1 收盘前提交**（场外基金 15:00 前）才按 T+1 净值成交。

产物（相对本文件）：daily/_v5_overlap_daily.md / _v5_overlap_daily.json。
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
_V5 = os.path.dirname(_HERE)
sys.path.insert(0, _V5)

import overlap as ov  # noqa: E402

OUT_MD = os.path.join(_HERE, "_v5_overlap_daily.md")
OUT_JSON = os.path.join(_HERE, "_v5_overlap_daily.json")
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


def forced_position(df, buy_date):
    """把持仓改成「buy_date 买入、满 hold_days 后卖出」-> pos 序列。"""
    idx = df.index
    d0 = pd.Timestamp(buy_date)
    if d0 not in idx:
        later = idx[idx >= d0]
        if not len(later):
            raise SystemExit(f"--buy {buy_date} 晚于最新交易日 {idx[-1]:%Y-%m-%d}")
        d0 = later[0]
    i0 = int(idx.get_loc(d0))
    hd = int(ov.CFG["hold_days"])
    pos = pd.Series(0, index=idx, dtype="int8")
    pos.iloc[i0:min(i0 + hd, len(idx))] = 1
    return pos


def main():
    capital = float(arg("--capital", 1000000) or 1000000)
    tol = _f("--tol", ov.CFG["level_tol"])
    refresh = "--refresh" in sys.argv
    buy_date = arg("--buy", None)

    df = ov.build_frame(mode="level", level_tol=tol, refresh=refresh)
    if buy_date:
        df = df.copy()
        df["pos"] = forced_position(df, buy_date)
    last = df.iloc[-1]
    held = float(last["pos"]) == 1
    R = float(last["R"])
    thr = 1.0 + float(tol)
    gap = R - thr                                     # >0 = 还没到位
    eps = ov.episodes(df)
    flow = df.tail(RECENT_DAYS)

    # 持仓期的剩余天数与卖出日
    st = ov.hold_state(df)
    left = st["left"]
    buy_day, sell_day, next_act = None, None, ""
    if held:
        buy_day = st["start"]
        if left > 0:
            next_act = (f"继续持有；已持有 {st['held_n']} 个交易日，"
                        f"**还需 {left} 个交易日**才满一年（届时收盘卖出）")
        else:
            next_act = "**已满一年（今日到期）→ 下一交易日收盘卖出**"
        to_do = "持有"
    elif bool(last["trig"]):
        next_act = "**今天满足买点 → 明日收盘买入（满仓沪深300全收益）**"
        to_do = "买入"
    else:
        need = (1.0 - (thr / R)) * 100.0             # 红线相对蓝线还要再跌多少
        next_act = (f"空仓等待；红线需相对蓝线再跌 **{need:.1f}%**（R 从 {R:.3f} 到 {thr:.3f}）"
                    f"才触发买点")
        to_do = "不动"

    L = [
        f"# 波动资产 · V5（两线重合买点）· 每日操作（{df.index[-1]:%Y-%m-%d}）",
        f"> 红线 = 沪深300全收益（{ov.SOURCE.get('hs300_tr')}）× "
        f"蓝线 = 二级债基指数（{ov.SOURCE.get('bond_index')}）。",
        f"> 两线同锚归一 1000（锚点 {ov.CFG['anchor']}），`R = 红/蓝`；"
        f"**买点 = R ≤ {thr:.2f}**（跌破或几乎贴合），**买入后持有一年"
        f"（{ov.CFG['hold_days']} 个交易日）卖出**。T 日盘后算信号 → T+1 收盘净值成交。",
        f"> 总资产 {capital:,.0f} 元；空仓段按货基 {ov.rule.CASH_ANNUAL:.2%} 计息。"
        "机械规则输出，**非投资建议**。",
        "",
        "## 1. 今日两条线",
        "",
        "| 项 | 数值 |",
        "|---|---|",
        f"| 红线 沪深300全收益 | {last['tr']:.2f}（归一后 {last['tr_n']:.0f}） |",
        f"| 蓝线 二级债基指数 | {last['bd']:.4f}（归一后 {last['bd_n']:.0f}） |",
        f"| **R = 红/蓝** | **{R:.4f}**（1.0000 = 两线完全重合） |",
        f"| 买点阈值 | R ≤ {thr:.4f}（1 + {tol:.0%}） |",
        "| 距买点 | "
        + ("**已触发**" if R <= thr
           else f"红线需再跑输蓝线 **{gap * 100 / R:.1f}%**（R 从 {R:.3f} 降到 {thr:.3f}）")
        + " |",
        f"| 当前状态 | **{'持仓中' if held else '空仓'}**"
        + (f" · 已持有 {st['held_n']}/{int(ov.CFG['hold_days'])} 个交易日"
           f"（{buy_day:%Y-%m-%d} 买入）" if held else "") + " |",
        "",
        "## 2. 下一交易日的动作",
        "",
        f"- {next_act}",
        f"- 仓位：{'满仓沪深300全收益指数基金 ≈ ' + format(capital, ',.0f') + ' 元' if to_do == '买入'
                  else ('沪深300全收益指数基金 ' + format(capital, ',.0f') + ' 元' if held
                        else '0 元（空仓，货基/债基）')}",
        "- 成交纪律：**T+1 收盘前提交**（场外 15:00 前）拿 T+1 净值；"
        "没触发就不要因为红线日内大跌提前抢跑 —— 这条规则的定义是「R ≤ 阈值」，不是「跌得多」。",
        "",
        "## 3. 历史买点（严格口径，最近的在前）",
        "",
        "| # | 首次触发 | 区间止 | 区间天数 | R@起 | R 最低 | 之后 250 日（全收益） |",
        "|---|---|---|---:|---:|---:|---:|",
    ]
    for i, e in enumerate(eps[:MAX_EPISODES], 1):
        L.append(f"| {i} | {e['start']} | {e['end']} | {e['days']} | {e['R_start']:.2f} | "
                 f"{e['R_min']:.2f} | {ov.fmt_pct(e['tr_after_1y'], 1)} |")
    if not eps:
        L.append("| — | 从未触发 | — | — | — | — | — |")

    L += [
        "",
        "## 4. 近 %d 个交易日流水" % RECENT_DAYS,
        "",
        "| 日期 | 红线(归一) | 蓝线(归一) | R | 阈值 | 触发 | 持仓 |",
        "|---|---:|---:|---:|---:|---|---|",
    ]
    for d, r in flow.iterrows():
        L.append(f"| {d:%Y-%m-%d} | {r['tr_n']:.0f} | {r['bd_n']:.0f} | {r['R']:.3f} | "
                 f"{thr:.3f} | {'✅' if r['trig'] else ''} | {'✔' if r['pos'] else ''} |")

    L += [
        "",
        "## 5. 提醒（比读数重要）",
        "",
        "- **这条规则 21 年只响过一次（2005-01）**。它不是「每年找买点」的策略，"
        "而是「等极端便宜」的警报器 —— 空仓等待是常态，不要因为「很久没动」就放宽阈值。",
        f"- 红线年化（2005 起）约 8.7%、蓝线约 5.1%，缺口 3.6pp/年 —— "
        "**R 的周期低点逐年抬升**（0.81 → 1.10 → 1.27 → 1.56 → 1.65），"
        "所以「跌破 1.0」未来可能永远不发生。若只想用它做参考刻度，"
        "可把阈值放宽到 1.20（`--tol 0.20`），但那已经不是「重合」。",
        "- 卖出后钱去哪很关键：回债基（而不是躺货基）能把同一套买点的年化从 5.5% 提到 8.3%"
        "（见 `../backtest/_v5_overlap_bt.md` §3）。",
        f"- 数据日期：红线 {ov.SOURCE.get('hs300_tr')}；蓝线 {ov.SOURCE.get('bond_index')}。",
        "",
        "> 机械规则输出，**非投资建议**。",
    ]

    with open(OUT_MD, "w", encoding="utf-8") as f:
        f.write("\n".join(L))
    with open(OUT_JSON, "w", encoding="utf-8") as f:
        json.dump({
            "strategy": ov.LABEL, "as_of": df.index[-1].date().isoformat(),
            "rule": {"mode": "level", "level_tol": tol, "anchor": ov.CFG["anchor"],
                     "hold_days": ov.CFG["hold_days"], "allow_rebuy": ov.CFG["allow_rebuy"],
                     "trade_at": "T+1 收盘净值"},
            "readings": {"tr": float(last["tr"]), "tr_n": float(last["tr_n"]),
                         "bd": float(last["bd"]), "bd_n": float(last["bd_n"]),
                         "R": R, "threshold": thr, "trig": bool(last["trig"]),
                         "position": int(last["pos"]),
                         "days_left": int(left),
                         "held_days": int(st["held_n"]),
                         "buy_date": (buy_day.date().isoformat() if buy_day is not None else None),
                         "sell_date": (sell_day.date().isoformat() if sell_day is not None else None)},
            "action": {"capital": capital, "todo": to_do, "text": next_act},
            "episodes": eps[:MAX_EPISODES], "source": dict(ov.SOURCE), "warn": list(ov._WARN),
        }, f, ensure_ascii=False, indent=1)

    print("报告:", OUT_MD)
    print("JSON:", OUT_JSON)
    print(f"{df.index[-1]:%Y-%m-%d} · 红线归一 {last['tr_n']:.0f} · 蓝线归一 {last['bd_n']:.0f} · "
          f"R {R:.4f}（阈值 {thr:.2f}）· {'持仓中' if held else '空仓'}")
    print("动作:", next_act)
    if ov._WARN:
        print("告警:", ov._WARN[:3])


if __name__ == "__main__":
    main()
