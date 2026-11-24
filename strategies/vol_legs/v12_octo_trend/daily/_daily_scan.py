# -*- coding: utf-8 -*-
"""V12 · 每日操作（八态 → 许可上限 → 今日该动多少）。

流程：

1. 刷新数据（默认联网增量；`--no-refresh` 复用缓存）：
   - 七腿场内 ETF OHLCV（`data/_ohlcv.json`，与 v13/v18 共享；黄金信号用它）；
   - 六条指数腿的**真实指数收盘 + 同源指数量能**（`data/_cn_index_klines.json` /
     `data/_global_index_klines.json`，由 `_idxcache.py` 统一维护）。
2. 逐腿跑六步判定 → 得到今日态 / regime / z20 / rvol / 许可；
3. 与昨日许可比较 → 今日动作（**T 日收盘出信号，T+1 收盘前执行**，与 `engine.run` 的 `w.shift(1)` 一致）；
4. 输出 `_v12_daily.md` / `_v12_daily.json`（后者被 `pipeline/daily_digest.py` 的
   「变种操作一览」卡读取，字段：`leg/name/action/pos/level_txt`）。

报告头写的是**数据日**（各腿末根日期）而不是运行日，并逐腿列出该 bar 在北京时间几点起被视为
已收盘 —— 七腿跨三个市场（A 股 15:00 / 港股 16:00 / 美股 bar 是美东交易日、收盘在北京次日
05:00）。14:35 跑时 A股/港股是 T-1 收盘、美股是**今天凌晨**收盘，这正是设计口径：
`T 日收盘出信号、T+1 收盘前执行`。

**执行范围 = 全七腿**（`rule.CORE_LEGS`，2026-11-18 由「只做沪深300」扩到七腿）；
额度分配按回测 §1c 的 Δ夏普排，别七腿等权。
"""
import argparse
import io
import json
import os
import sys

import numpy as np
import pandas as pd

try:
    sys.stdout.reconfigure(encoding="utf-8")  # type: ignore[attr-defined]
except Exception:  # noqa: BLE001
    pass

_HERE = os.path.dirname(os.path.abspath(__file__))
_V12 = os.path.dirname(_HERE)
if _V12 not in sys.path:
    sys.path.insert(0, _V12)

import rule           # noqa: E402
import data as vdata  # noqa: E402
import engine         # noqa: E402

OUT_MD = os.path.join(_HERE, "_v12_daily.md")
OUT_JSON = os.path.join(_HERE, "_v12_daily.json")


def fmt(x, nd=0):
    return "—" if x is None or (isinstance(x, float) and not np.isfinite(x)) else f"{x:.{nd}f}"


def main():
    ap = argparse.ArgumentParser(description="V12 八态趋势 · 每日操作")
    ap.add_argument("--no-refresh", action="store_true", help="不联网，直接用缓存")
    args = ap.parse_args()

    notes = []
    if not args.no_refresh:
        latest, fails = vdata.refresh_etf()
        bad = [c for c in fails]
        notes.append(f"ETF OHLCV 刷新 {len(latest) - len(bad)}/{len(latest)} 成功"
                     + (f"（失败 {', '.join(bad)}，用缓存）" if bad else ""))
        ilatest, ifails = vdata.refresh_cn_index()
        notes.append("指数量能刷新 " + " / ".join(f"{k} {v}" for k, v in sorted(ilatest.items()))
                     + (f"（失败 {', '.join(ifails)}）" if ifails else ""))

    avail = vdata.available()
    results = {leg: engine.backtest_leg(leg) for leg in avail}

    rows = []
    for leg in avail:
        r = results[leg]
        if r["start"] is None:
            rows.append(dict(leg=leg, name=rule.NAMES[leg], code=rule.LEGS[leg][0],
                             state="暖机", regime="—", z20=None, rvol=None,
                             pos=0.0, pos_prev=0.0, act=0.0, action="空仓",
                             level_txt="预热不足",
                             scope="执行" if leg in rule.CORE_LEGS else "诊断",
                             note="预热不足"))
            continue
        cx, w = r["cx"], r["w"]
        last, prev = cx.iloc[-1], cx.iloc[-2]
        pos, pos_prev = float(w.iloc[-1]), float(w.iloc[-2])
        act = pos - pos_prev
        if act > 1e-9:
            action = "加仓"
        elif act < -1e-9:
            action = "减仓"
        else:
            action = "持有" if pos > 1e-9 else "空仓"
        state = str(last["state"])
        wv = r.get("w_vol")
        coef = float(wv.iloc[-1]) if wv is not None else None
        rows.append(dict(
            leg=leg, name=rule.NAMES[leg], code=rule.LEGS[leg][0],
            state=state, regime=str(last["regime"]), regime_used=str(last["regime_used"]),
            z20=float(last["z20"]), rvol=float(last["rvol"]),
            dd20=float(last["dd20"]) * 100.0, vote=int(last["vote"]),
            vs_ma20=float(r["ind"]["close"].iloc[-1] / r["ind"]["ma_s"].iloc[-1] - 1.0) * 100.0,
            burst=bool(last["burst"]), near_ph=bool(last["near_ph"]),
            pos=pos, pos_prev=pos_prev, act=act, action=action,
            level_txt=f"{state} {pos:.0%}",
            cap_prev_state=str(prev["state"]),
            vol_coef=coef,
            vol_layer=rule.vol_layer_txt(leg),
            scope=("执行" if leg in rule.CORE_LEGS else
                   "观察" if leg in rule.WATCH_LEGS else "诊断"),
            vol=("指数量能" if leg in vdata.IDX_MAP else "ETF 自身量"),
            asof=str(r["end"].date()),
        ))

    data_date = max(r["end"] for r in results.values() if r["end"] is not None)
    # 逐腿数据日 + 该 bar 在北京时间几点收盘。七腿跨三个市场（A股 15:05 / 港股 16:05 /
    # 美股 bar 日期是美东交易日、收盘在北京次日 05:05），只写一个日期会被读成「都同一天」。
    clock = vdata._idxcache.clock
    dparts = []
    for _leg in rule.LEG_ORDER:
        _r = results.get(_leg)
        if not _r or _r["end"] is None:
            continue
        _code = vdata.IDX_MAP[_leg][1] if _leg in vdata.IDX_MAP else rule.LEGS[_leg][0]
        _t = clock.bar_close_at(_code, _r["end"].date())
        _tag = " 美东" if clock.market_of(_code) == "US" else ""
        dparts.append(f"{rule.NAMES[_leg]} {_r['end'].date():%m-%d}{_tag}"
                      f"（北京 {_t:%m-%d %H:%M} 起）")
    todo = [x for x in rows if x["scope"] == "执行" and abs(x.get("act") or 0) > 1e-9]
    pending_note = [x for x in rows if x["scope"] == "执行" and abs(x.get("act") or 0) <= 1e-9]

    L = [
        f"# V12 · 八态趋势 · 每日操作（数据日 {data_date.date()}）",
        "",
        "> 规则：`G:\\tradingagents\\hs300_trend\\八态趋势参数规格.md` v1.1（八态：急涨/慢涨/弱回调/强回调 ｜ "
        "急跌/慢跌/弱反弹/强反弹）。状态机 `engine.py::classify`，参数 `rule.py`。",
        "> 口径：**真实指数收盘 + 同源指数量能**（黄金用场内 `518880`；宽基 ETF 自身量是做市噪声，"
        "会把弱回调/强回调的符号做反）。**T 日收盘出信号，T+1 收盘前执行**。",
        f"> 许可映射（生效基线 = 规格 ⊕ `慢跌` 0，2026-11-19）："
        f"{' · '.join(f'{k} {v:.0%}' for k, v in rule.CAP.items())}"
        f" · 升格确认 {rule.UP_CONFIRM} 日（降格立即）。",
        ("**生效口径 = v12.1**（`rule.OPT=True`）："
         + "；".join(f"{rule.NAMES[l]} {rule.cap_version(l)}"
                    for l in rule.LEGS if rule.cap_version(l).startswith("v12.1"))
         + "。依据 `research/opt_ashare/` 与 `strategies/vol_legs/research/v12v13_compare/`；"
           "括号里的许可已按腿生效，`rule.OPT=False` 可回退基线口径。")
        if rule.OPT else "生效口径 = 基线 CAP（`rule.OPT=False`）。",
        ("**波动层（2026-11-20 按腿，`rule.VOL_LAYER`）**："
         + " · ".join(f"{rule.NAMES[l]} {rule.vol_layer_txt(l)}"
                      for l in rule.LEGS if rule.vol_layer_of(l))
         + ("" if all(rule.vol_layer_of(l) for l in rule.LEGS) else
            "；" + " · ".join(rule.NAMES[l] for l in rule.LEGS if not rule.vol_layer_of(l)) + " 不加")
         + "。**实际仓位 = min(八态许可, 波动层系数)**，系数 ≤ 1 只压上限；"
         "下表「系数」列是今天生效的那个数，判断「该不该减」看的是**实际仓位**。"
         "美股两腿走 C5（ER20/10，**无死区**）。依据 `research/er_vol_layer/_er_vol_layer.md`。"
         if rule.OPT else "波动层：未启用（`rule.OPT=False`）。"),
        f"> 执行范围：**{' + '.join(rule.NAMES[l] for l in rule.CORE_LEGS)}**"
        "（`rule.CORE_LEGS`，2026-11-18 由「只做沪深300」扩到七腿；"
        "`z_th`/`rvol` 仍是规格在沪深300 上的标定值，额度按回测 §1c 的 Δ夏普排）。",
        "",
        f"> **数据日（逐腿，末根都是已收盘的 bar）**：{' · '.join(dparts)}。"
        "括号里的北京时间 = 该 bar 在数据层**被视为已收盘**的起点（各市场收盘 + 5 分钟缓冲，"
        "判据 `data/_market_clock.py`）。",
        "> **美股两腿的日期是「美东交易日」**，不是北京日期：`11-23` 这根就是**北京时间 11-24 凌晨**"
        "（美东 16:00 收盘）的那一根 —— 也就是「昨夜美股」，不是「前天的数据」。"
        "同理 A股/港股腿的 `11-23` 是它们各自的 11-23 收盘。",
        "> **14:35 跑时 A股/港股是 T-1 收盘**（当日 bar 还没收盘，数据层不写也不读未收盘的 bar），"
        "美股是今天凌晨收盘 —— 这不是「数据没刷新」，是设计口径。"
        "**T 日收盘出信号、T+1 收盘前执行**，所以今天下单用的就是上表这些 bar。",
        "",
        "## 1. 今日动作（执行范围）",
        "",
    ]
    if todo:
        L += ["| 腿 | 代码 | 状态（昨日 → 今日） | 实际仓位 | 幅度 | 依据 |",
              "|---|---|---|---:|---:|---|"]
        for x in todo:
            why = (f"`{x['state']}` 许可 {rule.cap_of(x['state'], leg=x['leg']):.0%}"
                   + (f" · 波动层 {x['vol_coef']:.0%}" if x.get("vol_coef") is not None else "")
                   + f"；z20 {fmt(x['z20'], 2)} · rvol {fmt(x['rvol'], 2)} · "
                     f"距MA20 {fmt(x['vs_ma20'], 1)}%")
            L.append(f"| {x['name']} | `{x['code']}` | {x['cap_prev_state']} → **{x['state']}** | "
                     f"{x['pos_prev']:.0%} → **{x['pos']:.0%}** | {x['act'] * 100:+.0f}pp | {why} |")
    else:
        x = pending_note[0] if pending_note else None
        if x:
            L.append(f"**无许可变化 —— 维持昨日的 {x['pos']:.0%}**"
                     f"（{x['state']}，z20 {fmt(x['z20'], 2)} · rvol {fmt(x['rvol'], 2)}）。")
        else:
            L.append("执行范围今日无信号。")

    L += ["", "## 2. 全腿八态", "",
          "| 腿 | 范围 | 执行标的 | 状态 | regime | z20 | rvol | 距MA20 | 许可 | 系数 "
          "| 实际仓位 | 昨日 | 今日动作 | 量能口径 |",
          "|---|---|---|---|---|---:|---:|---:|---:|---:|---:|---:|---|---|"]
    for x in rows:
        act_txt = (f"{x['act'] * 100:+.0f}pp" if abs(x.get("act") or 0) > 1e-9
                   else ("持有" if (x.get("pos") or 0) > 1e-9 else "空仓"))
        coef = x.get("vol_coef")
        L.append(f"| {x['name']} | {x['scope']} | `{x['code']}` | {x['state']} | {x['regime']} | "
                 f"{fmt(x.get('z20'), 2)} | {fmt(x.get('rvol'), 2)} | "
                 f"{fmt(x.get('vs_ma20'), 1)}% | {rule.cap_of(x['state'], leg=x['leg']):.0%} | "
                 f"{'—' if coef is None else f'{coef:.0%}'} | {x.get('pos', 0):.0%} | "
                 f"{x.get('pos_prev', 0):.0%} | {act_txt} | {x.get('vol', '—')} |")

    # 标定腿最近的状态切换流水（固定沪深300，与执行范围解耦）
    core = rule.CALIB_LEG
    rc = results.get(core)
    L += ["", f"## 3. 标定腿（{rule.NAMES[core]}）的状态流水（最近 8 次切换）", ""]
    if rc is not None and rc["start"] is not None:
        st = rc["cx"]["state"].loc[rc["start"]:]
        chg = st[st != st.shift(1)].dropna()
        items = "；".join(f"{t.date()} {s}（许可 {rule.cap_of(s, leg=core):.0%}）"
                          for t, s in list(chg.items())[-8:])
        L.append(f"- **{rule.NAMES[core]}**：{items}")
        L.append(f"- 最近 20 个交易日的态：{' → '.join(str(s) for s in st.tail(20).tolist())}")
    else:
        L.append("—")

    L += [
        "",
        "## 4. 读法与边界",
        "",
        "- **「许可」是上限语义**：今天读到的许可 = 明天起可以持有的最大带内比例，"
        "不是「明天必须买到这个数」。低于当前持仓就该减，高于当前持仓只是**解锁**加仓资格。",
        *[f"- **参数变更 {d}**：{t}" for d, t in rule.CHANGELOG],
        "- **八态不是涨跌预测**：`慢跌` 压到 0%（规格 20%，本仓库 2026-11-19 下调）的依据是"
        "规格 §3 的长样本 fwd20（−1.00%）与胜率（45%），`急跌` 反而留 50% 的依据是同一张表的"
        "+0.89%（急跌会反抽）。这条不对称是规格的核心结论 —— **不是「跌了就清仓」**："
        "清仓只针对钝刀割肉的慢跌，急跌仍留半仓。",
        "- **七腿都执行，但额度不该等权**：价格/量能已是同源真实指数，`z_th`/`rvol` 仍是"
        "规格在沪深300 上的标定值（规格 §7.4 建议在美股重标；两折样本外无一腿能稳定赢过原值）。"
        "哪条腿值得多给额度看回测 §1c 的 Δ夏普排序 —— 恒生科技是唯一拖累组合的一条。",
        "- **「许可」与「实际仓位」不是一回事**：`min(八态许可, 波动层系数)`，"
        "系数由 `vol20` 与 ER 状态决定（A股两腿/恒科带 10pp/5日 死区，美股两腿无死区，"
        "黄金/科创50 是固定 TV + 死区）。"
        "所以「许可 100% 却只有 60% 仓位」是正常的 —— 看「实际仓位」决定买卖，"
        "看「系数」理解为什么。**波动层只压不加**，它不会把仓位推到许可之上。",
        "- **黄金的量能是 ETF 自身量**：现货黄金没有成交量，`rvol` 只能用 `518880` 的量代理，"
        "所以黄金腿的强弱回调判定比其它六腿弱一档。",
    ]
    if notes:
        L += ["", "> 数据：" + " · ".join(notes)]

    with io.open(OUT_MD, "w", encoding="utf-8", newline="\n") as f:
        f.write("\n".join(L))

    payload = dict(
        # `as_of` = **数据日**（各腿末根的最大日期），不是运行日：14:35 跑时它是 T-1。
        as_of=str(data_date.date()),
        data_dates={l: str(r["end"].date()) for l, r in results.items() if r["end"] is not None},
        variant="v12_octo_trend",
        core=rule.CORE_LEGS,
        cap_opt={l: rule.cap_map(l) for l in rule.LEGS} if rule.OPT else None,
        cap_spec=rule.CAP_SPEC,
        cap_base=rule.CAP,
        cap_version={l: rule.cap_version(l) for l in rule.LEGS},
        changelog=[dict(date=d, note=t) for d, t in rule.CHANGELOG],
        vol_layer={l: rule.vol_layer_of(l) for l in rule.LEGS if rule.vol_layer_of(l)},
        vol_layer_txt={l: rule.vol_layer_txt(l) for l in rule.LEGS},
        up_confirm=rule.UP_CONFIRM,
        vol_mode=rule.VOL_MODE,
        legs=[{k: (str(v) if isinstance(v, (np.str_,)) else
                   (bool(v) if isinstance(v, (bool, np.bool_)) else
                    (None if isinstance(v, float) and not np.isfinite(v) else v)))
               for k, v in x.items()} for x in rows],
        notes=notes,
    )
    with io.open(OUT_JSON, "w", encoding="utf-8", newline="\n") as f:
        f.write(json.dumps(payload, ensure_ascii=False, indent=1))

    print("操作表:", OUT_MD)
    print(f"  数据日 {data_date.date()}（T 日收盘出信号、T+1 收盘前执行）")
    print("  逐腿：" + " · ".join(dparts))
    for x in rows:
        act_txt = (f"{x['act'] * 100:+.0f}pp" if abs(x.get("act") or 0) > 1e-9 else "—")
        print(f"  {x['name']:8s} {x['state']:4s} regime {x.get('regime', '—'):2s} "
              f"许可 {x.get('pos', 0):4.0%} 动作 {act_txt:>6s}"
              f"{'  ← 执行' if x['scope'] == '执行' else ''}")


if __name__ == "__main__":
    main()
