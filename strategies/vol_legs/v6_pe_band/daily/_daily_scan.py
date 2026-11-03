# -*- coding: utf-8 -*-
"""波动资产 · V6 变种（估值分位仓位带）· 操作提示 —— 沪深300 / 中证A500。

流程：
1. 读本地缓存（该标的收盘 + 滚动 PE / 交叉标的 PE / 10Y 中债），**无网络请求、不写共享缓存**；
2. 复算分位（`band.pe_percentile`）与两把尺子（自身 / 交叉，A500 才有交叉）；
3. 输出：当前分位与档位、在效目标带、dd / RSI / 52 周新高、阶梯走到哪一档、
   **今天该不该动手**（定档日？跨档？阶梯触发？RSI 扳机？）+ 最近换档流水。

口径提醒（规则的一部分，不是延迟）：
- 分位**每天**都在变，但只有**定档日**才看它是否跨档（`REBAL_FREQ` = weekly / monthly / quarterly）；
- 目标带只决定「最多能投几成」，**加仓必须等回撤阶梯 + RSI 扳机**；
- 减仓（目标带下调）在定档日执行，加仓（阶梯）在扳机日执行 —— 两个方向不同触发器。

用法：
    python daily/_daily_scan.py                    # 默认标的与频率（CFG）
    python daily/_daily_scan.py --index hs300      # 换标的
    python daily/_daily_scan.py --w 0.4            # 按实际仓位判断（0.4 = 40%）

产物（相对本文件）：daily/_v6_pe_band_daily.md / _v6_pe_band_daily.json。
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

import band  # noqa: E402

OUT_MD = os.path.join(_HERE, "_v6_pe_band_daily.md")
OUT_JSON = os.path.join(_HERE, "_v6_pe_band_daily.json")
RECENT = 12


def arg(name, default=None):
    if name in sys.argv:
        i = sys.argv.index(name)
        if i + 1 < len(sys.argv):
            return sys.argv[i + 1]
    return default


def _f(name, default=None):
    """仓位解析：`0.4` / `40%` / `40` 都读成 40%。"""
    try:
        raw = arg(name, default)
        if raw is None:
            return None
        s = str(raw).strip()
        v = float(s.rstrip("%"))
        return v / 100.0 if (s.endswith("%") or v > 1.0) else v
    except (TypeError, ValueError):
        return default


def ladder_scan(ind):
    """自上一个 52 周新高以来，回撤阶梯在各档上的**候选触发日**。

    规则原文只给触发条件（dd≥阈值 + RSI 扳机 + 同月最多 1 档），没给「用户已经加过几档」的账本 ——
    所以这里只回答「按价格路径，哪些档位**本可以**触发」，不假设账户状态。真实加仓以你自己的
    交易记录为准（文档 §7 要求逐笔留痕）。
    """
    hi = ind["high52w"].fillna(False).to_numpy(dtype=bool)
    start = 0
    for i in range(len(ind) - 1, -1, -1):
        if hi[i]:
            start = i
            break
    dd_lev = list(band.CFG["LADDER_DD"])
    used = {lvl: None for lvl in dd_lev}
    seen_month = set()
    for i in range(start, len(ind)):
        d = ind.index[i]
        for lvl in dd_lev:
            if used[lvl] is not None or ind["dd"].iloc[i] < lvl:
                continue
            key = (d.year, d.month)
            if key in seen_month or not bool(ind["rsi_ok"].iloc[i]):
                continue
            used[lvl] = d
            seen_month.add(key)
    return dict(anchor=ind.index[start], highest=float(ind["dd"].loc[ind.index[start]:].max()),
                used=used)


def veto_cell(me, d, p):
    """换档行的利差否决：`是/否（gap ±x.xpp）`；否决器**未就绪**时说明是「判不了」而非「没否决」。"""
    if me is None or not hasattr(me, "columns") or "veto_gap" not in me.columns:
        return "—（否决器关闭）"
    hit = [i for i in me.index if (i.year, i.month) == (d.year, d.month)]
    if not hit:
        return "—（当月无月末读数）"
    row = me.loc[hit[0]]
    gap = row["veto_gap"]
    if gap != gap:                     # NaN：扩张分位窗口还没满 VETO_MIN_M
        return f"未就绪（需 {int(p['VETO_MIN_M'])} 个月样本）"
    return f"{'**是**' if bool(row['veto']) else '否'}（gap {float(gap) * 100:+.1f}pp）"


def main():
    p = band.params()
    key = arg("--index", None) or str(p["UNIVERSE"])
    u = band.universe_info(key, p)
    p["UNIVERSE"] = u["key"]
    freq = str(p["REBAL_FREQ"])
    cur_w = _f("--w", None)          # 当前仓位（占本策略资金），默认 = 在效目标带

    r = band.latest(None, p, u["key"])
    ind = r["ind"]
    dd_lev = list(p["LADDER_DD"])
    dd_unit = list(p["LADDER_UNITS"])
    unit = float(p["UNIT_PCT_POOL"]) / 100.0

    today = r["date"]
    dec_today = band.is_decision_today(today, freq, p)
    nxt = band.next_decision_day(today, freq, p)
    ls = ladder_scan(ind)
    pending_levels = [lvl for lvl, d in ls["used"].items() if d is None]
    ladder_hit = None
    for lvl in sorted(pending_levels, reverse=True):
        if r["dd"] is not None and r["dd"] >= lvl:
            ladder_hit = lvl
            break
    target = r["target"]
    w_now = target if cur_w is None else cur_w
    gap = None if (target is None or w_now is None) else target - w_now
    can_add = (ladder_hit is not None and bool(r["rsi_ok"])
               and gap is not None and gap > 1e-9)
    add_units = min(dd_unit[dd_lev.index(ladder_hit)], gap / unit) if can_add else 0.0
    can_cut = bool(dec_today and target is not None and w_now is not None
                   and w_now > target + 1e-9)

    acts = []
    if dec_today:
        today_band = r["w_a"] if r["w_a"] is not None else target
        acts.append(f"**今天是定档日（{freq}）** → 按今日分位重算目标带 "
                    f"**{band.fmt_w(today_band)}**（当前在效 {band.fmt_w(target)}）。")
        if can_cut:
            acts.append(f"现仓 {band.fmt_w(w_now)} 高于目标 → **下个交易日减到 "
                        f"{band.fmt_w(target)}**。")
        elif gap is not None and gap > 1e-9:
            acts.append(f"目标 {band.fmt_w(target)} 高于现仓 {band.fmt_w(w_now)}，"
                        "但**加仓仍要等阶梯 + RSI 扳机**，定档日不主动补。")
        else:
            acts.append("现仓不高于目标 → 定档日不减持。")
    else:
        acts.append(f"今天不是定档日（{freq}，下一个 **{nxt:%Y-%m-%d}**）→ "
                    "分位变化不构成动作（周期内噪音不动）。")
    if can_add:
        acts.append(f"阶梯：dd **{r['dd']:.1f}%** 已越过 {ladder_hit:g}% 档且 RSI14 "
                    f"{r['rsi14']:.1f} 满足扳机 → 若本月还没执行过档，**下个交易日加 "
                    f"{add_units * unit:.0%}**（{add_units:g} 份 × {p['UNIT_PCT_POOL']:g}%）。")
    elif ladder_hit is not None and not r["rsi_ok"]:
        acts.append(f"阶梯：dd **{r['dd']:.1f}%** 已越过 {ladder_hit:g}% 档，但 RSI14 "
                    f"{r['rsi14']:.1f} 未到扳机（需 <{p['RSI_BELOW']:g} 或从其下方回升的首日）→ **等**。")
    elif not pending_levels:
        acts.append("阶梯：自上次 52 周新高以来三档都已触发过 → 等下一次创新高归零或更深回撤。"
                    + (f" 注意：目标带 {band.fmt_w(target)} 高于现仓 {band.fmt_w(w_now)}，"
                       "但阶梯已用尽 —— **规则不允许直接补到带位**，这就是「分批 + 扳机」的代价。"
                       if gap is not None and gap > 1e-9 else ""))
    else:
        acts.append(f"阶梯：dd {r['dd']:.1f}% 未到任何未触发的档位 → **等**。")
    if r["high52w"]:
        acts.append("close 创 52 周新高 → 阶梯计数归零，之后重新从最低档起算。")

    me = r["me"]
    veto_txt, veto_gap_txt = "—（无 10Y 数据，否决器不生效）", "—"
    if "veto_gap" in me.columns and me["veto_gap"].notna().any():
        ok = me.dropna(subset=["veto_gap"])
        g = float(ok["veto_gap"].iloc[-1])
        thr = float(p["VETO_GAP"])
        hit = bool(ok["veto"].iloc[-1])
        mon = f"{ok.index[-1]:%Y-%m}"
        veto_gap_txt = (f"{ok['erp_pct'].iloc[-1]:.1%} / {ok['ey_pct'].iloc[-1]:.1%} / "
                        f"**{g * 100:+.1f}pp**（阈值 {thr * 100:.0f}pp，{mon} 月末读数，"
                        f"样本 {len(ok)} 个月）")
        veto_txt = (f"**是**（gap {g * 100:+.1f}pp ≥ {thr * 100:.0f}pp，按低一档执行）" if hit
                    else f"否（gap {g * 100:+.1f}pp < {thr * 100:.0f}pp，或未连续 "
                         f"{int(p['VETO_CONFIRM_M'])} 个月）")
    erp = None
    if r["y10"] is not None and r["pe"]:
        erp = 100.0 / float(r["pe"]) - float(r["y10"])

    # 「换档流水」只留目标带**真正变过**的定档日：同档重复（50% → 50%）没有信息量，不展示。
    # 在全量历史上算换档（而不是先切片），否则切片首行的「上一档」会丢掉、凭空多一次换档。
    hist = r["rows"] if r["rows"] else []
    changers, prev_w = [], None
    for x in hist:
        w = x.get("w")
        if x.get("action") != "定档" or w is None:
            continue
        w = float(w)
        if prev_w is None or abs(w - prev_w) > 1e-9:
            changers.append((prev_w, w, x))
        prev_w = w
    changers = changers[-RECENT:]
    pe_txt = "—" if r["pe"] is None else f"{r['pe']:.2f}"
    used_txt = " / ".join(
        f"{lvl:g}%:" + (f"{d:%m-%d}" if d is not None else "未触发") for lvl, d in ls["used"].items())
    cross = u.get("cross")
    if cross:
        cross_line = (f"| 交叉尺子（{band.UNIVERSES[cross]['name']}） | "
                      f"{band.fmt_w(r['pct_h'])} → 档位 {band.fmt_w(r['w_h'])}"
                      "（文档 §6-1，两把尺子不一致时取较低者） |")
    else:
        cross_line = "| 交叉尺子 | —（本地无第二个同级长历史估值源，交叉校验自动关闭） |"

    L = [
        f"# {band.label(u['key'], p)} · 操作提示（{r['date']:%Y-%m-%d}）",
        "> 规则：`E:/qianwen/jijin/中证A500操作规则.md` v1.0 · 参数权威源 `band.py::CFG`"
        "（2026-11-03 起标的与频率可配）",
        f"> 闸门 = {u['name']} PE-TTM 自身分位（{p['WINDOW_YEARS']} 年窗口写死）· 仓位带 "
        f"{' / '.join(f'{w:.0%}' for w in p['BAND_WEIGHTS'])} · 阶梯 dd "
        f"{'/'.join(f'{d:g}%' for d in dd_lev)} → {'/'.join(f'{u2:g}份' for u2 in dd_unit)}"
        f"（1 份 = {p['UNIT_PCT_POOL']:g}%）",
        f"> **定档 {freq}** · RSI{p['RSI_PERIOD']} < {p['RSI_BELOW']:g} 扳机 · "
        f"同月最多 {p['MAX_TRANCHE_PER_MONTH']} 档 · 52 周新高归零 · T 日算 / T+1 收盘成交",
        "> 机械规则输出，**非投资建议**。",
        "",
        "## 1. 今日读数",
        "",
        "| 项 | 数值 |",
        "|---|---|",
        f"| {u['name']} 收盘 | {r['close']:.2f}（{r['date']:%Y-%m-%d}） |",
        f"| 滚动 PE(TTM) | **{pe_txt}**（{r['pe_date']:%Y-%m-%d}） |",
        f"| 自身分位 | **{band.fmt_w(r['pct'])}**（n_eff={r['n_eff']}"
        f"{'· **window_short**：10 年窗口未满，分位口径偏敏' if r['window_short'] else '，10 年窗口已成立'}）"
        f"→ 档位 {band.fmt_w(r['w_a'])} |",
        cross_line,
        f"| **在效目标带** | **{band.fmt_w(target)}**（上次定档；今天不重算） |",
        f"| 回撤 dd（近 {p['PULLBACK_WIN']} 日高点） | {r['dd']:.1f}% |",
        f"| RSI{p['RSI_PERIOD']} | {r['rsi14']:.1f} → 扳机{'**满足**' if r['rsi_ok'] else '未满足'} |",
        f"| 52 周新高 | {'**是**（阶梯归零）' if r['high52w'] else '否'} |",
        f"| 10Y 中债 / 盈利收益率 / 利差 | "
        + (f"{r['y10']:.2f}% / {100.0 / r['pe']:.2f}% / {erp:.2f}%"
           if (r["y10"] is not None and r["pe"]) else "10Y 缺失（否决器不生效）") + " |",
        f"| ERP 分位 / EY 分位 / 缺口 | {veto_gap_txt} |",
        f"| 利差否决（本期） | {veto_txt} |",
        "",
        "## 2. 今天该怎么做",
        "",
    ]
    L += [f"- {a}" for a in acts]
    L += [
        "",
        f"> 现仓按 `--w` 取值（默认 = 在效目标带 {band.fmt_w(target)}）；"
        "要按自己的实际仓位判断，跑 `-- --w 0.4`。标的用 `-- --index a500|hs300` 切换。",
        "",
        "## 3. 阶梯账本（自上一个 52 周新高起）",
        "",
        f"- 归零锚点：**{ls['anchor']:%Y-%m-%d}**（当日 close 创 52 周新高）；"
        f"此后最大回撤 **{ls['highest']:.1f}%**。",
        f"- 按价格路径 + RSI 扳机 + 同月 1 档帽，各档**可触发日**：{used_txt}。",
        "- 三档满梯合计 4.5 份 = 90%（≈100% 带），与文档 §2 的「满梯 4.5 份」一致。",
        "",
        f"## 4. 最近 {RECENT} 次换档（{u['name']} · {freq}）",
        "",
        "| 日期 | 自身档位 | 换档 | 利差否决 | 备注 |",
        "|---|---:|---:|---|---|",
    ]
    if changers:
        for pw, w, x in changers:
            note = "—" if x["note"] == "同档" else x["note"]   # 表内全是换档行，「同档」= 无修饰
            L.append(f"| {x['date']:%Y-%m-%d} | {band.fmt_w(x['w_a'])} | "
                     f"{band.fmt_w(pw)} → **{band.fmt_w(w)}** | "
                     f"{veto_cell(me, x['date'], p)} | {note} |")
    else:
        L.append(f"| — | — | — | — | 最近 {RECENT} 次定档内目标带未变（无换档） |")
    ok_idx = me["veto_gap"].dropna().index if "veto_gap" in me.columns else []
    n_ok = len(ok_idx)
    if n_ok < int(p["VETO_MIN_M"]):
        first = f"{ok_idx[0]:%Y-%m}" if n_ok else "—"
        L += [
            "",
            f"> ⚠ 本标的的利差否决器**历史不够**：扩张分位需 {int(p['VETO_MIN_M'])} 个月样本，"
            f"{u['name']} 目前只有 **{n_ok} 个月**可判（首个可判月 {first}）。"
            "表里的「未就绪」= **判不了**，不是「没否决」；"
            "回测报告 §4 的换档流水用沪深300（样本足）所以几乎每行都有否决。",
        ]

    L += [
        "",
        "## 5. 执行前检查（文档 §4 硬过滤 + §7 留痕）",
        "",
        f"- 载体：规模最大的 {u['name']} ETF（场内）或 A 类联接（场外）；**不做指数增强**。",
        "- 一票否决：规模 <2 亿 ｜ 场内 |溢价| >2% ｜ 日均成交 <1000 万 ｜ 年化跟踪误差 >2% ｜ "
        "发起式临近「合同满 3 年规模不足 2 亿自动清盘」考核线。",
        "- 触发记录模板（文档 §7 要求逐笔留痕）："
        "`日期 | 触发规则 | 动作 | 份数/金额 | 当时 PE / 分位 / dd / rsi / 利差`。",
        "- 任何一条规则连续 3 个月产生明显坏决策 → **只调那一条参数**并在规则文档的修订记录里留痕，"
        "**不加新维度**。",
        "",
        "## 6. 频率与口径提示",
        "",
        f"- 当前定档频率 **{freq}**（`band.py::CFG[\"REBAL_FREQ\"]` 可切 weekly / monthly / quarterly）。"
        "沪深300 上的对照结论见 `../backtest/_v6_pe_band_bt.md` §1："
        "weekly 比 quarterly 年化低约 0.8pp、回撤只浅约 1pp，多付 21 笔交易 —— "
        "**周频的价值取决于你的费率**，不是「反应更快」。",
        "- 6/12 月调样顺延只在 quarterly 下生效；weekly / monthly 下照常定档。",
        (f"- ⚠ 本标的的 PE 只有 {r['n_eff']} 个交易日的历史，`window_short=True`："
         "分位比文档设想更敏感，交叉尺子就是为这件事准备的 —— 见 `../README.md` §2.2。"
         if r["window_short"] else
         f"- 本标的的 10 年 PE 窗口已成立（n_eff={r['n_eff']}），分位口径与文档一致。"),
        "",
        f"> 数据：{r['source'].get('px')}｜{r['source'].get('pe')}",
        "> 机械规则输出，**非投资建议**。",
    ]

    with open(OUT_MD, "w", encoding="utf-8") as f:
        f.write("\n".join(L))

    with open(OUT_JSON, "w", encoding="utf-8") as f:
        json.dump({
            "strategy": band.label(u["key"], p), "universe": u["key"],
            "universe_name": u["name"], "as_of": today.date().isoformat(),
            "freq": freq,
            "rule": {"window_years": p["WINDOW_YEARS"], "window_days": p["WINDOW_DAYS"],
                     "min_obs": p["MIN_OBS"], "band_edges": p["BAND_EDGES"],
                     "band_weights": p["BAND_WEIGHTS"], "ladder_dd": dd_lev,
                     "ladder_units": dd_unit, "unit_pct_pool": p["UNIT_PCT_POOL"],
                     "rsi_period": p["RSI_PERIOD"], "rsi_below": p["RSI_BELOW"],
                     "rebal_freq": freq, "blackout_months": p["BLACKOUT_MONTHS"],
                     "blackout_freq": p["BLACKOUT_FREQ"],
                     "cross_rule": p["CROSS_RULE"], "trade_at": "T+1 收盘"},
            "readings": {"close": r["close"], "pe": r["pe"],
                         "pe_date": r["pe_date"].date().isoformat(),
                         "pct": r["pct"], "pct_cross": r["pct_h"], "n_eff": r["n_eff"],
                         "window_short": r["window_short"], "dd": r["dd"], "rsi14": r["rsi14"],
                         "rsi_trigger": r["rsi_ok"], "high52w": r["high52w"],
                         "y10": r["y10"], "erp": erp},
            "band": {"w_self": r["w_a"], "w_cross": r["w_h"], "target": target,
                     "current_w": w_now},
            "today": {"is_decision_day": bool(dec_today),
                      "next_decision_day": nxt.date().isoformat() if nxt is not None else None,
                      "ladder_hit": ladder_hit, "can_add": bool(can_add),
                      "add_units": float(add_units), "can_cut": bool(can_cut),
                      "actions": acts},
            "ladder": {"anchor": ls["anchor"].date().isoformat(),
                       "highest_dd": ls["highest"],
                       "used": {f"{k:g}": (v.date().isoformat() if v is not None else None)
                                for k, v in ls["used"].items()}},
            "recent_bands": [{"date": x["date"].date().isoformat(), "w_self": x["w_a"],
                              "w_cross": x["w_h"], "prev_w": pw, "w": w,
                              "veto": bool(x["veto"]), "note": x["note"],
                              "action": x["action"]} for pw, w, x in changers],
            "source": dict(r["source"]),
        }, f, ensure_ascii=False, indent=1)

    print("报告:", OUT_MD)
    print("JSON:", OUT_JSON)
    print(f"{today:%Y-%m-%d} · {u['name']} · 定档 {freq} · 分位 {band.fmt_w(r['pct'])}"
          f"（交叉 {band.fmt_w(r['pct_h'])}）· 在效目标带 {band.fmt_w(target)}"
          f" · dd {r['dd']:.1f}% · RSI {r['rsi14']:.1f}")
    for a in acts:
        print("  -", a)


if __name__ == "__main__":
    main()
