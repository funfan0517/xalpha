# -*- coding: utf-8 -*-
"""V6 黄金三口径 · 每日读数。

输出（本目录 `daily/`）：`_v6_gold_daily.md` · `_v6_gold_daily.json`。
json 里的 `A/B/C` 各带 `action`（今日动作文案）/ `dir`（buy|sell|""）/ `cond`（触发依据），
由 `pipeline/daily_digest.py` 的 `_vol_v6_row` 直接读进板块2「变种操作一览」卡 —— 阈值判断
只在本文件与 `gold.py` 里，看板侧不复刻，避免两边算法漂移。

三条口径在同一天各说一句话：
- **A 长期配置**：年线位置 / 跌破天数 / 保险丝是否生效 / 回撤阶梯还差多少触发；
- **B 控回撤**：120 日已实现波动 → 波动系数 → 今天该压到几成仓；
- **C 占坑**：MACD 金叉还是死叉、最近一次信号在什么时候（并提醒换手成本）。

用法：
    python strategies/vol_legs/v6_gold/daily/_daily_scan.py              # 常规（会尝试刷场外净值）
    python strategies/vol_legs/v6_gold/daily/_daily_scan.py --no-refresh  # 只用本地缓存

模拟结果，非投资建议。
"""
import json
import os
import sys

import numpy as np

try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:  # noqa: BLE001
    pass

_HERE = os.path.dirname(os.path.abspath(__file__))
_V6 = os.path.dirname(_HERE)
if _V6 not in sys.path:
    sys.path.insert(0, _V6)

import gold as G  # noqa: E402

OUT_MD = os.path.join(_HERE, "_v6_gold_daily.md")
OUT_JSON = os.path.join(_HERE, "_v6_gold_daily.json")


def pp(frac):
    """占带比例 -> pp（目标带 10pp）。"""
    return frac * G.BAND_PP


def read_a(df):
    last = df.iloc[-1]
    tiers = [th for th in G.LADDER_DD if float(last["dd"]) * 100 < th]
    return dict(close=float(last["close"]), ma=float(last["ma"]),
                gap=float(last["close"] / last["ma"] - 1.0),
                above=bool(last["above"]), below_days=int(last["below_days"]),
                dd=float(last["dd"]), state=float(last["state"]),
                degraded=bool(last["degraded"]),
                next_tier=(tiers[0] if tiers else None),
                room=float(1.0 - last["state"]),
                decide=str(last["decide"]), done=str(last["done"]),
                acts=G.actions(df["state"])[-3:])


def read_b(px):
    sm = G.state_b(px)
    last = sm.iloc[-1]
    vol = float(px.pct_change().rolling(G.VOL_WINDOW).std().iloc[-1] * np.sqrt(252))
    return dict(vol=vol, scale=float(last["vol_scale"]), state=float(last["state"]),
                target=G.VOL_TARGET, acts=G.actions(sm["state"])[-3:])


def read_c(px):
    st = G.state_c(px)
    dif, dea = G.macd_lines(px)
    acts = G.actions(st)
    return dict(state=float(st.iloc[-1]), dif=float(dif.iloc[-1]), dea=float(dea.iloc[-1]),
                bull=bool(dif.iloc[-1] > dea.iloc[-1]),
                last_act=(acts[-1] if acts else None), acts=acts[-3:])


def fmt_act(a):
    if not a:
        return "无"
    return f"{a['date'].date()} {'加仓' if a['kind'] == 'buy' else '减仓'} {a['frm']:.0%}→{a['to']:.0%}"


def main():
    refresh = "--no-refresh" not in sys.argv
    px_o = G.load("otc", refresh=refresh)
    px_e = G.load("exchange", refresh=False)

    a = read_a(G.state_a(px_o))
    b = read_b(px_o)
    c = read_c(px_e)
    c_otc = read_c(px_o)
    date_o, date_e = px_o.index[-1].date(), px_e.index[-1].date()

    act_a = "无" if not a["decide"] else ("加仓" if a["decide"] == "buy" else "减仓")
    act_b = ("无" if abs(b["scale"] - b["state"]) < 0.05
             else f"调到 {b['scale']:.0%} 带（{pp(b['scale']):.1f}pp）")
    last_c = c["last_act"]
    act_c = "无" if (not last_c or last_c["date"] != px_e.index[-1]) else \
        ("买入" if last_c["kind"] == "buy" else "卖出")
    # 方向（buy / sell / ""=不动）—— 供看板汇总卡判「今天这条口径是买还是卖」，
    # 免得 pipeline 侧再复刻一遍阈值判断（口径只在本文件/ gold.py 里定义）。
    dir_a = a["decide"] if a["decide"] in ("buy", "sell") else ""
    dir_b = "" if act_b == "无" else ("buy" if b["scale"] > b["state"] else "sell")
    dir_c = "" if act_c == "无" else ("buy" if last_c["kind"] == "buy" else "sell")

    L = []
    A = L.append
    A(f"# V6 黄金三口径 · 每日推荐操作（{date_o}）")
    A("")
    A(f"> 标的只有一个：**黄金**。场外执行 `{G.EXEC_CODE}`（净值 {px_o.iloc[-1]:.4f}，{date_o}）· "
      f"场内信号 `{G.SIGNAL_CODE}`（收盘 {px_e.iloc[-1]:.4f}，{date_e}）· "
      f"目标带 {G.BAND_PP:g}pp（1.0 = 满带 = {G.BAND_PP:g}pp）")
    A("> 决策在 T 日盘后，**T+1 收盘成交**；「今日动作」= 明天要下的单。")
    A("")

    # ---- 今日操作一览
    A("## 今日操作一览")
    A("")
    cond_a = ("年线下方第 %d 日（%d/%d），满 %d 日降为 %.0f%% 带"
              % (a["below_days"], a["below_days"], G.DEGRADE_DAYS, G.DEGRADE_DAYS,
                 G.FLOOR * 100) if not a["above"] and not a["degraded"]
              else ("保险丝已生效（上限 %.0f%% 带），等收复年线" % (G.FLOOR * 100)
                    if a["degraded"] else "年线上方，按目标带持有"))
    A("| 口径 | 当前仓位 | 今日动作 | 触发条件 / 依据 |")
    A("|---|---:|---|---|")
    A(f"| **A 长期配置** | {a['state']:.0%} 带 = {pp(a['state']):.1f}pp | **{act_a}** | {cond_a} |")
    A(f"| **B 控回撤** | {b['state']:.0%} 带 = {pp(b['state']):.1f}pp | **{act_b}** | "
      f"已实现波动 {b['vol'] * 100:.1f}% → 系数 {b['scale']:.2f} |")
    A(f"| **C 低仓位占坑** | {c['state']:.0%} | **{act_c}** | "
      f"MACD {G.MACD_FAST}/{G.MACD_SLOW} {'金叉持有' if c['bull'] else '死叉空仓'} |")
    A("")
    A("> ⚠ 三条口径是**同一笔钱的三种拿法，互斥** —— 选一条执行，**不要相加**。")
    A("> 差别在「你要黄金干什么」：长期配置不想管（A）/ 压组合波动（B）/ 必须有明确进出信号（C）。")
    A("")

    # ---- A
    A("## 一、A 长期配置（满带 + 60 日保险丝 + 回撤阶梯）")
    A("")
    pos = "年线上方" if a["above"] else f"年线下方第 {a['below_days']} 日"
    A(f"- 收盘 {a['close']:.4f} / 年线(MA{G.MA_WIN}) {a['ma']:.4f} → **{a['gap'] * 100:+.2f}%**（{pos}）；"
      f"距前高回撤 **{a['dd'] * 100:.1f}%**。")
    A(f"- 当前档位 **{a['state']:.0%} 带 = {pp(a['state']):.1f}pp**；"
      f"保险丝{'已生效（上限 50% 带）' if a['degraded'] else '未生效'}"
      f"（门槛：连续 {G.DEGRADE_DAYS} 日收在年线下方）。")
    prog = "已触发" if a["below_days"] >= G.DEGRADE_DAYS else f"{a['below_days']}/{G.DEGRADE_DAYS} 日"
    if a["room"] < G.UNIT_FRAC * 0.2:
        tier = "已满带，阶梯暂时没有空位"
    elif a["next_tier"] is None:
        tier = "已全部填完"
    else:
        tier = f"dd ≥ {a['next_tier']:.0f}%（补 {G.LADDER_UNITS[G.LADDER_DD.index(a['next_tier'])]:g} 份）"
    A(f"- 保险丝进度 {prog}；回撤阶梯下一档 {tier}，剩余额度 {a['room']:.0%} 带（{pp(a['room']):.1f}pp）。")
    A(f"- **今日动作：{act_a}**" + (f"　—— {a['done']}" if a["done"] else ""))
    A(f"- 最近三笔：{' / '.join(fmt_act(x) for x in a['acts']) if a['acts'] else '无'}")
    A("")
    A("![A 长期配置](_v6_gold_A.png)")
    A("")

    # ---- B
    A("## 二、B 控回撤（波动目标 15%/120 日，不做趋势判断）")
    A("")
    A(f"- 120 日已实现波动 **{b['vol'] * 100:.1f}%** vs 目标 {b['target']:.0%} → "
      f"波动系数 **{b['scale']:.2f}**（<1 才压仓）。")
    A(f"- 当前仓位 **{b['state']:.0%} 带 = {pp(b['state']):.1f}pp**。")
    A(f"- **今日动作：{act_b}**")
    A(f"- 最近三笔：{' / '.join(fmt_act(x) for x in b['acts']) if b['acts'] else '无'}")
    A("")
    A("![B 控回撤](_v6_gold_B.png)")
    A("")

    # ---- C
    A(f"## 三、C 低仓位占坑（MACD {G.MACD_FAST}/{G.MACD_SLOW} 金叉持有）")
    A("")
    A(f"- 场内信号：DIF {c['dif']:.4f} / DEA {c['dea']:.4f} → "
      f"**{'金叉（持有）' if c['bull'] else '死叉（空仓）'}**，当前仓位 {c['state']:.0%}。")
    A(f"- 最近一次信号：{fmt_act(last_c)}")
    A(f"- 场外同口径对照：{'金叉' if c_otc['bull'] else '死叉'}，仓位 {c_otc['state']:.0%}"
      f"（DIF {c_otc['dif']:.4f} / DEA {c_otc['dea']:.4f}）")
    A(f"- **今日动作：{act_c}**")
    A(f"- ⚠ 换手提醒：这条口径每年约 7~8 次调仓，**只在场内低佣金账户执行**；"
      f"场外 A 类（赎 0.50%）累计成本 36pp、年化只剩 +5.07%，场内是 +7.49%"
      f"（MACD {G.MACD_FAST}/{G.MACD_SLOW} 口径，见 `backtest/_v6_gold_bt.md` §4）。")
    A("")
    A("![C 低仓位占坑](_v6_gold_C.png)")
    A("")

    # ---- 一句话
    A("## 四、一句话")
    A("")
    if a["degraded"]:
        tail = "A 口径已在保险丝状态（上限 50% 带），别再加仓，等收复年线。"
    elif not a["above"]:
        tail = (f"A 口径：年线下方第 {a['below_days']} 日，只停加仓不清仓；"
                f"满 {G.DEGRADE_DAYS} 日会自动降到 50% 带。")
    else:
        tail = "A 口径：年线上方，按目标带持有，跌破 -10%/-15%/-20% 时按阶梯补仓。"
    A(f"{tail} B 口径当前 {b['state']:.0%} 带（已实现波动 {b['vol'] * 100:.0f}%），"
      f"C 口径{'持有' if c['state'] > 0 else '空仓'}。")
    A("")
    A("> 模拟结果，非投资建议；三口径的取舍见 `backtest/_v6_gold_bt.md` §0。")

    md = "\n".join(L) + "\n"
    with open(OUT_MD, "w", encoding="utf-8") as f:
        f.write(md)
    with open(OUT_JSON, "w", encoding="utf-8") as f:
        json.dump(dict(date=str(date_o), date_ex=str(date_e), as_of=str(date_o),
                       price_otc=float(px_o.iloc[-1]), price_ex=float(px_e.iloc[-1]),
                       band_pp=G.BAND_PP,
                       params=dict(ma_win=G.MA_WIN, degrade_days=G.DEGRADE_DAYS,
                                   floor=G.FLOOR, vol_window=G.VOL_WINDOW,
                                   vol_target=G.VOL_TARGET,
                                   macd=[G.MACD_FAST, G.MACD_SLOW]),
                       A=dict(state=a["state"], pp=pp(a["state"]), gap=a["gap"],
                              above=a["above"], below_days=a["below_days"], dd=a["dd"],
                              degraded=a["degraded"], decide=a["decide"],
                              action=act_a, cond=cond_a, dir=dir_a,
                              acts=[fmt_act(x) for x in a["acts"]]),
                       B=dict(state=b["state"], pp=pp(b["state"]), vol=b["vol"],
                              scale=b["scale"], target=b["target"],
                              action=act_b, dir=dir_b,
                              acts=[fmt_act(x) for x in b["acts"]]),
                       C=dict(state_ex=c["state"], state_otc=c_otc["state"],
                              dif=c["dif"], dea=c["dea"], bull=c["bull"],
                              action=act_c, dir=dir_c,
                              last_act=(str(last_c["date"]) if last_c else None))),
                  f, ensure_ascii=False, indent=1)
    print(md)
    print("落盘:", OUT_MD)


if __name__ == "__main__":
    main()
