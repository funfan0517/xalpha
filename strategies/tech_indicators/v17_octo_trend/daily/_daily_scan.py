# -*- coding: utf-8 -*-
"""V17 · 每日操作（八态 → 许可上限 → 今日该动多少）。

流程：

1. 刷新数据（默认联网增量；`--no-refresh` 复用缓存）：家族 54 只场内 ETF 的
   **收盘 + 成交量**（`data/_etf_cv_klines.json`，本策略自有库）。
2. 逐标的跑六步判定 → 得到今日态 / regime / z20 / rvol / 许可；
3. 与昨日许可比较 → 今日动作（**T 日收盘出信号，T+1 收盘前执行**，与 `engine.run` 的 `w.shift(1)` 一致）；
4. 输出 `_v17_octo_daily.md` / `_v17_octo_daily.json`。

**执行范围 = 全池 54 只**（`rule.CORE_LEGS`），额度分配按回测 §1c 的 Δ夏普排，别等权。
⚠ 本变种的量能是 **ETF 自身量**（不是 v12 的同源指数量能），宽基/QDII 的强弱分档含做市噪声；
上板（进每日操作报告）需用户明确同意 —— 当前 `strategies.json::daily.enabled=false`。
"""
import argparse
import io
import json
import os
import sys

import numpy as np

try:
    sys.stdout.reconfigure(encoding="utf-8")  # type: ignore[attr-defined]
except Exception:  # noqa: BLE001
    pass

_HERE = os.path.dirname(os.path.abspath(__file__))
_V17 = os.path.dirname(_HERE)
if _V17 not in sys.path:
    sys.path.insert(0, _V17)

import rule           # noqa: E402
import data as vdata  # noqa: E402
import engine         # noqa: E402

OUT_MD = os.path.join(_HERE, "_v17_octo_daily.md")
OUT_JSON = os.path.join(_HERE, "_v17_octo_daily.json")


def fmt(x, nd=0):
    return "—" if x is None or (isinstance(x, float) and not np.isfinite(x)) else f"{x:.{nd}f}"


def main():
    ap = argparse.ArgumentParser(description="V17 八态趋势 · 每日操作（54 只场内 ETF）")
    ap.add_argument("--no-refresh", action="store_true", help="不联网，直接用缓存")
    args = ap.parse_args()

    notes = []
    if not args.no_refresh:
        latest, fails = vdata.refresh_cv()
        notes.append(f"ETF 收盘+量能 刷新 {len(latest) - len(fails)}/{len(latest)} 成功"
                     + (f"（失败 {', '.join(sorted(fails))}，用缓存）" if fails else ""))

    avail = vdata.available()
    results = {leg: engine.backtest_leg(leg) for leg in avail}

    rows = []
    for leg in avail:
        r = results[leg]
        if r["start"] is None:
            rows.append(dict(leg=leg, name=rule.NAMES.get(leg, leg), code=rule.LEGS[leg][0],
                             state="暖机", regime="—", z20=None, rvol=None,
                             pos=0.0, pos_prev=0.0, act=0.0, action="空仓",
                             level_txt="预热不足",
                             scope="执行" if leg in rule.CORE_LEGS else "诊断",
                             vol="ETF 自身量", note="预热不足"))
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
            leg=leg, name=rule.NAMES.get(leg, leg), code=rule.LEGS[leg][0],
            state=state, regime=str(last["regime"]), regime_used=str(last["regime_used"]),
            z20=float(last["z20"]), rvol=float(last["rvol"]),
            dd20=float(last["dd20"]) * 100.0, vote=int(last["vote"]),
            vs_ma20=float(r["ind"]["close"].iloc[-1] / r["ind"]["ma_s"].iloc[-1] - 1.0) * 100.0,
            burst=bool(last["burst"]), near_ph=bool(last["near_ph"]),
            pos=pos, pos_prev=pos_prev, act=act, action=action,
            cap=rule.cap_of(state, leg=leg),
            level_txt=f"{state} {pos:.0%}",
            cap_prev_state=str(prev["state"]),
            vol_coef=coef,
            vol_layer=rule.vol_layer_txt(leg),
            scope=("执行" if leg in rule.CORE_LEGS else
                   "观察" if leg in rule.WATCH_LEGS else "诊断"),
            vol="ETF 自身量",
            asof=str(r["end"].date()),
        ))

    date = max(r["end"] for r in results.values() if r["end"] is not None)
    todo = [x for x in rows if x["scope"] == "执行" and abs(x.get("act") or 0) > 1e-9]
    pending_note = [x for x in rows if x["scope"] == "执行" and abs(x.get("act") or 0) <= 1e-9]

    L = [
        f"# V17 · 八态趋势 · 每日操作（{date.date()}）",
        "",
        "> 规则：`G:\\tradingagents\\hs300_trend\\八态趋势参数规格.md` v1.1（八态：急涨/慢涨/弱回调/强回调 ｜ "
        "急跌/慢跌/弱反弹/强反弹）。状态机 `engine.py::classify`，参数 `rule.py`。"
        "整套规则复制自 `strategies/vol_legs/v12_octo_trend`（2026-11-21）。",
        "> 口径：**场内 ETF 前复权收盘 + ETF 自身成交量**（`data/_etf_cv_klines.json`）。"
        "⚠ v12 用的是同源指数量能；本变种没有 54 只标的的指数量能，宽基/QDII 的量含做市噪声，"
        "「弱回调 − 强回调」分离度可能反号（回测 §3 如实体现）。**T 日收盘出信号，T+1 收盘前执行**。",
        f"> 许可映射（生效基线 = 规格 ⊕ `慢跌` 0，2026-11-19）："
        f"{' · '.join(f'{k} {v:.0%}' for k, v in rule.CAP.items())}"
        f" · 升格确认 {rule.UP_CONFIRM} 日（降格立即） · 成本 单边 {rule.FEE_SIDE * 1e4:.1f}bp / "
        f"货基 {rule.CASH_ANNUAL:.2%}（v12 口径，勿与家族零成本口径横比）。",
        ("> **生效口径 = v12.1 结构**（`rule.OPT=True`）："
         + "；".join(f"{rule.NAMES.get(l, l)} {rule.cap_version(l)}"
                    for l in rule.LEGS if rule.cap_version(l).startswith("v12.1"))
         + "。⚠ 这些配置只在原来那七只 ETF 上有证据、且证据来自指数口径，本变种未重标 → 结构保留；"
           "其余标的走基线 `CAP`、不加波动层。`rule.OPT=False` 可回退纯规格口径。")
        if rule.OPT else "> 生效口径 = 基线 CAP（`rule.OPT=False`）。",
        f"> 执行范围：**全池 {len(rule.CORE_LEGS)} 只场内 ETF**（`rule.CORE_LEGS` = `../_pool.py` 前四类）。"
        "`z_th`/`rvol` 是规格在沪深300 上的标定值，未按标的重标；额度按回测 §1c 的 Δ夏普排，别等权。",
        "",
        "## 1. 今日动作（执行范围）",
        "",
    ]
    if todo:
        L += ["| 标的 | 代码 | 状态（昨日 → 今日） | 实际仓位 | 幅度 | 依据 |",
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
            L.append(f"**无许可变化** —— 执行范围内 {len(pending_note)} 只全部维持。"
                     f"（样本：{x['name']} {x['state']}，z20 {fmt(x['z20'], 2)} · rvol {fmt(x['rvol'], 2)}）")
        else:
            L.append("执行范围今日无信号。")

    L += ["", "## 2. 全池八态", "",
          "> 「许可」是态的上限；「实际仓位」= 许可 × 系数，且**升格要等 2 日确认** —— "
          "升格首日会出现「许可 50% / 仓位 0%」，那是待确认，不是数据错。",
          "",
          "| 标的 | 范围 | 代码 | 状态 | regime | z20 | rvol | 距MA20 | 许可 | 系数 "
          "| 实际仓位 | 昨日 | 今日动作 |",
          "|---|---|---|---|---|---:|---:|---:|---:|---:|---:|---:|---|"]
    for x in rows:
        act_txt = (f"{x['act'] * 100:+.0f}pp" if abs(x.get("act") or 0) > 1e-9
                   else ("持有" if (x.get("pos") or 0) > 1e-9 else "空仓"))
        coef = x.get("vol_coef")
        L.append(f"| {x['name']} | {x['scope']} | `{x['code']}` | {x['state']} | {x['regime']} | "
                 f"{fmt(x.get('z20'), 2)} | {fmt(x.get('rvol'), 2)} | "
                 f"{fmt(x.get('vs_ma20'), 1)}% | {rule.cap_of(x['state'], leg=x['leg']):.0%} | "
                 f"{'—' if coef is None else f'{coef:.0%}'} | {x.get('pos', 0):.0%} | "
                 f"{x.get('pos_prev', 0):.0%} | {act_txt} |")

    # 持有许可名单：急涨(100%) / 慢涨(50%) / 弱回调(100%) —— 这三个态允许持有
    hold_states = ("急涨", "慢涨", "弱回调")
    hold_ok = [x for x in rows if x.get("state") in hold_states and x.get("pos") is not None]
    L += ["", "## 2b. 持有许可名单（急涨 / 慢涨 / 弱回调）", "",
          "> 这三个态是「允许持有」的：急涨 100% · 慢涨 50% · 弱回调 100%。"
          "注意是**上限**，不是买入指令 —— 实际仓位还要过波动层与升格确认（2 日）。", ""]
    if hold_ok:
        L += ["| 标的 | 代码 | 状态 | 许可 | 实际仓位 | z20 | rvol | 距MA20 |",
              "|---|---|---|---:|---:|---:|---:|---:|"]
        for x in sorted(hold_ok, key=lambda y: -(y.get("pos") or 0)):
            L.append(f"| {x['name']} | `{x['code']}` | {x['state']} | "
                     f"{rule.cap_of(x['state'], leg=x['leg']):.0%} | {x.get('pos', 0):.0%} | "
                     f"{fmt(x.get('z20'), 2)} | {fmt(x.get('rvol'), 2)} | "
                     f"{fmt(x.get('vs_ma20'), 1)}% |")
    else:
        L.append("今日全池没有标的落在急涨 / 慢涨 / 弱回调。")

    # 标定腿最近的状态切换流水（固定沪深300，与执行范围解耦）
    core = rule.CALIB_LEG
    rc = results.get(core)
    L += ["", f"## 3. 标定腿（{rule.NAMES.get(core, core)}）的状态流水（最近 8 次切换）", ""]
    if rc is not None and rc["start"] is not None:
        st = rc["cx"]["state"].loc[rc["start"]:]
        chg = st[st != st.shift(1)].dropna()
        items = "；".join(f"{t.date()} {s}（许可 {rule.cap_of(s, leg=core):.0%}）"
                          for t, s in list(chg.items())[-8:])
        L.append(f"- **{rule.NAMES.get(core, core)}**：{items}")
        L.append(f"- 最近 20 个交易日的态：{' → '.join(str(s) for s in st.tail(20).tolist())}")
    else:
        L.append("—")

    L += [
        "",
        "## 4. 读法与边界",
        "",
        "- **「许可」是上限语义**：今天读到的许可 = 明天起可以持有的最大比例，"
        "不是「明天必须买到这个数」。低于当前持仓就该减，高于当前持仓只是**解锁**加仓资格。",
        *[f"- **参数变更 {d}**：{t}" for d, t in rule.CHANGELOG],
        "- **八态不是涨跌预测**：`慢跌` 压到 0%（规格 20%，本仓库 2026-11-19 下调）的依据是"
        "规格 §3 的长样本 fwd20（−1.00%）与胜率（45%），`急跌` 反而留 50% 的依据是同一张表的"
        "+0.89%（急跌会反抽）。这条不对称是规格的核心结论 —— **不是「跌了就清仓」**。",
        "- **全池执行，但额度不该等权**：54 只宽基高度重叠（A500/沪深300/上证50/中证500…），"
        "等权等于给 A股宽基加很大权重。看法回测 §1c 的 Δ夏普排序决定「谁多做、谁只做参照」。",
        "- **量能口径是本变种最大折扣**：ETF 自身量在宽基/QDII 上由做市与申赎套利主导，"
        "`rvol` 的强弱分档会偏噪声；只有主题/行业 ETF 比较贴 v12 的指数口径。"
        "看到「强回调/强反弹」判得和直觉不符，先怀疑量能，不要改参数。",
        "- **「许可」与「实际仓位」不是一回事**：`min(八态许可, 波动层系数)`；"
        "系数由 `vol20` 与 ER 状态决定（只压不加）。「许可 100% 却只有 60% 仓位」是正常的。",
    ]
    if notes:
        L += ["", "> 数据：" + " · ".join(notes)]

    with io.open(OUT_MD, "w", encoding="utf-8", newline="\n") as f:
        f.write("\n".join(L))

    payload = dict(
        as_of=str(date.date()),
        variant="v17_octo_trend",
        core=rule.CORE_LEGS,
        cap_opt={l: rule.cap_map(l) for l in rule.LEGS} if rule.OPT else None,
        cap_spec=rule.CAP_SPEC,
        cap_base=rule.CAP,
        cap_version={l: rule.cap_version(l) for l in rule.LEGS},
        changelog=[dict(date=d, note=t) for d, t in rule.CHANGELOG],
        vol_layer={l: rule.vol_layer_of(l) for l in rule.LEGS if rule.vol_layer_of(l)},
        vol_layer_txt={l: rule.vol_layer_txt(l) for l in rule.LEGS},
        # 持有许可名单（急涨/慢涨/弱回调）：看板一行摘要要列名字，避免再扫 54 行 legs
        hold_ok=[dict(code=x.get("code"), name=x.get("name"), state=x.get("state"),
                      cap=float(rule.cap_of(x["state"], leg=x["leg"])),
                      pos=float(x.get("pos") or 0.0))
                 for x in hold_ok],
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
    print(f"  信号日 {date.date()}（T+1 收盘前执行） · 执行范围 {len(rule.CORE_LEGS)} 只")
    for x in rows:
        act_txt = (f"{x['act'] * 100:+.0f}pp" if abs(x.get("act") or 0) > 1e-9 else "—")
        print(f"  {x['name']:14s} {x['state']:4s} regime {x.get('regime', '—'):2s} "
              f"许可 {x.get('cap') or 0:4.0%} 仓位 {x.get('pos', 0):4.0%} 动作 {act_txt:>6s}")


if __name__ == "__main__":
    main()
