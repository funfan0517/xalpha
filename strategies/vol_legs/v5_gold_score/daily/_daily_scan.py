# -*- coding: utf-8 -*-
"""V5.1 黄金仓位 · 每日操作（风险预算 × 实际利率闸门）。

输出（本目录 `daily/`）：`_v5_gold_score_daily.md` · `_v5_gold_score_daily.json`。
报告字段：**实现波动 → 风险预算 / 实际利率水平 → 闸门 / 目标档 e* / 当前档 / 推荐动作 / 数据截止日**，
另附「不计分项」（FedWatch / 央行购金 / 内外价差 / 资金流）的当日提示 —— 它们**永不改变仓位数字**。

用法（从仓库根跑）：
    python strategies/vol_legs/v5_gold_score/daily/_daily_scan.py            # 会先补实际利率/份额缓存
    python strategies/vol_legs/v5_gold_score/daily/_daily_scan.py --offline  # 不联网，只用本地缓存
    python strategies/vol_legs/v5_gold_score/daily/_daily_scan.py --hold 0.8 # 覆盖当前档（如已持有 80%）

模拟结果，非投资建议。
"""
import argparse
import json
import os
import sys

try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:  # noqa: BLE001
    pass

_HERE = os.path.dirname(os.path.abspath(__file__))
_V5 = os.path.dirname(_HERE)
if _V5 not in sys.path:
    sys.path.insert(0, _V5)

import factors as F  # noqa: E402
import score as S    # noqa: E402

OUT_MD = os.path.join(_HERE, "_v5_gold_score_daily.md")
OUT_JSON = os.path.join(_HERE, "_v5_gold_score_daily.json")
HINTS = os.path.join(F._DATA, "_gold_hints.json")        # 人工维护的「不计分项」，可缺


def pp(frac):
    """占带比例 -> pp（目标带 10pp）。"""
    return frac * S.BAND_PP


def hints():
    """不计分项：FedWatch / 央行购金 / 内外价差。缺文件 = 未维护。"""
    if not os.path.exists(HINTS):
        return None
    try:
        with open(HINTS, encoding="utf-8") as f:
            d = json.load(f)
    except (OSError, ValueError):
        return None
    return d if isinstance(d, dict) else None


def flow_note(flow, shares=None, close=None):
    """资金流近期提示（**不计分**）。份额口径（上交所，全史）优先，主力净流入次选。"""
    bits = []
    if shares is not None and len(shares) > 6:
        s = shares.tail(8)
        d = s.diff().dropna()
        if len(d):
            amt = float(d.tail(5).sum()) * 1e4 * float(close or 0) / 1e8   # 万份 → 份 → 亿元
            bits.append(f"份额最近 5 日净申购 {int((d.tail(5) > 0).sum())}/5 日、"
                        f"累计 {amt:+.2f} 亿元（{s.index[-1].date()}；全史口径，仅提示）")
    if flow is not None and len(flow):
        tail = flow.tail(5)
        bits.append(f"主力净流入最近 5 日 {int((tail > 0).sum())}/5 日为正、"
                    f"最后一日 {float(flow.iloc[-1]) / 1e8:+.2f} 亿"
                    f"（{flow.index[-1].date()}；仅 120 天，仅提示）")
    return "；".join(bits) if bits else "无缓存"


def main():
    ap = argparse.ArgumentParser(description="V5.1 黄金仓位 · 每日读数")
    ap.add_argument("--offline", action="store_true", help="不联网（只用本地缓存）")
    ap.add_argument("--hold", type=float, default=None,
                    help="覆盖当前档（占带比例，如 0.8）；默认按状态机回放")
    args = ap.parse_args()

    px, rate, flow, shares = S.load(refresh=not args.offline)
    p = S.params()
    if args.hold is not None:
        p["START"] = float(args.hold)
    df = S.run(px, rate, flow, p, shares=shares)
    last = df.iloc[-1]
    date = df.index[-1].date()

    target = float(last["target"])
    state = float(last["state"])
    act = str(last["action"])
    vol = float(last["vol"])
    scale = float(last["scale"])
    lvl = float(last["rate_level"])
    gate = float(last["gate"])
    rate_date = last["rate_date"].date()
    dir_ = {"买入": "buy", "卖出": "sell"}.get(act, "")
    ready = df["ready"].to_numpy(dtype=bool)
    run_start = df.index[ready][0] if ready.any() else df.index[0]
    band_yrs = (df.index[-1] - run_start).days / 365.25
    gap_pp = abs(target - state) * S.BAND_PP
    threshold_pp = F.HYST * F.STEP * S.BAND_PP

    cond = (f"120 日实现波动 {vol:.1%}（目标 {F.VOL_TARGET:.0%}）→ 风险预算 {scale:.2f}；"
            f"DFII10 {lvl:+.2f}%（{rate_date}）"
            f"{'高于' if gate >= 1.0 else '不高于'} {F.RATE_LEVEL_TH:+.1f}% → 闸门 {gate:.2f}"
            f"（死区 ±{F.RATE_LEVEL_BAND:.2f}pp）")
    if int(p["EVENT_FREEZE"]) > 0 and bool(last["blocked"]):
        cond += f"；事件窗口 ±{int(p['EVENT_FREEZE'])} 日内只许持有或减仓"

    h = hints()
    hint_lines = []
    if h:
        for k, lab in (("fedwatch", "FedWatch 加息/降息概率"), ("cb_gold", "央行购金"),
                       ("spread", "内外价差")):
            if h.get(k):
                hint_lines.append(f"- {lab}：{h[k]}")
        if h.get("note"):
            hint_lines.append(f"- 备注：{h['note']}")
    dir_sig = 1 if target > state + 1e-9 else (-1 if target < state - 1e-9 else 0)
    conflict = False
    if h and h.get("direction") in ("bull", "bear") and dir_sig != 0:
        hb = 1 if h["direction"] == "bull" else -1
        conflict = hb * dir_sig < 0
    if conflict:
        hint_lines.append("- ⚠ 与本次调仓方向相反 → **观察，谨慎**（**仓位数字不变**）")

    L = []
    A = L.append
    A(f"# V5.1 黄金仓位 · 每日推荐操作（{date}）")
    A("")
    A(f"> 标的：**场内 `{S.SIGNAL_CODE}` 黄金ETF华安**（收盘 {last['close']:.4f}）· "
      f"信号/执行/回测同一只 ETF · 目标带 {S.BAND_PP:g}pp（1.0 = 满带 = {S.BAND_PP:g}pp）")
    A(f"> 决策在 T 日盘后、**T+1 收盘成交**；「今日动作」= 明天要下的单。"
      f"数据截止：**价格 {date} / 实际利率 {rate_date}**（T 日只用 ≤T−1）。")
    A("")

    A("## 今日操作一览")
    A("")
    A("| 项 | 读数 |")
    A("|---|---|")
    A(f"| 实现波动（120 日） | **{vol:.1%}**（目标 {F.VOL_TARGET:.0%}，下限 {F.VOL_MIN:.0%}） |")
    A(f"| 风险预算 scale | **{scale:.2f}** = clip({F.VOL_TARGET:.0%} / {vol:.1%}, "
      f"{F.VOL_MIN:.0%}, 1) |")
    A(f"| 实际利率水平 | **{lvl:+.2f}%**（{rate_date}；阈值 {F.RATE_LEVEL_TH:+.1f}%"
      f" ±{F.RATE_LEVEL_BAND:.2f}pp 死区） |")
    A(f"| 利率闸门 gate | **{gate:.2f}**（{'满额预算' if gate >= 1.0 else '只给一半预算'}） |")
    A(f"| 目标档 e* | **{target:.0%}** 带 = {pp(target):.1f}pp / {S.BAND_PP:g}pp |")
    A(f"| 当前档 e | **{state:.0%}** 带 = {pp(state):.1f}pp |")
    A(f"| 推荐动作 | **{act}**" + (f"（{state:.0%} → {target:.0%}）" if dir_ else "（不动作）") + " |")
    A(f"| 数据截止日 | 价格 {date} · DFII10 {rate_date} |")
    A("")
    A(f"**依据**：{cond}。")
    A("")

    A("## 一、规则读数")
    A("")
    A(f"- 风险预算：120 日已实现波动 **{vol:.1%}**，目标 {F.VOL_TARGET:.0%} → "
      f"`scale = clip({F.VOL_TARGET:.0%} / {vol:.1%}, {F.VOL_MIN:.0%}, 1) = {scale:.2f}`。"
      "波动越高、能承担的风险越小 —— 这是「风险归一化」，不是方向判断。")
    A(f"- 利率闸门：DFII10 水平 **{lvl:+.2f}%**（{rate_date}，T−1 口径）。"
      f"高于 {F.RATE_LEVEL_TH:+.1f}%+{F.RATE_LEVEL_BAND:.2f}pp 才开闸；"
      f"当前 **{gate:.2f}**"
      + ("" if gate >= 1.0 else "（实际利率偏低 → 未来更可能上行，对金不利 → 只拿一半预算）") + "。")
    A(f"- 目标档 = 量化({scale:.2f} × {gate:.2f} = {scale * gate:.2f}, 步长 {F.STEP:.0%}) = "
      f"**{target:.0%}**。")
    A(f"- 滞后防抖：|e* − e| ≥ {F.HYST} 档（{F.STEP:.0%} 仓位 = {threshold_pp:.1f}pp 带）才动作，"
      f"当前差 {gap_pp:.1f}pp → {'触发' if act != '持有' else '不触发'}。")
    A("")

    A("## 二、不计分项（只作提示，永不改变仓位数字）")
    A("")
    if hint_lines:
        L.extend(hint_lines)
    else:
        A("- 未维护（`data/_gold_hints.json` 不存在）：FedWatch / 央行购金 / 内外价差 三项缺输入。")
    A(f"- 资金流提示：{flow_note(flow, shares, float(last['close']))}")
    A("")
    A("> 这些项与本次调仓方向冲突时只标注「观察，谨慎」，**绝不改变目标档数字**。")
    A("> 为什么资金流不进仓位：上交所份额全史已补齐并实测 —— 顺势口径显著恶化回撤、"
      "逆势只修回撤不增收益，两个方向都跑输恒定半仓。"
      "详见 `research/flow_source/_probe_flow.md`。")
    A("")

    A("## 三、净值与操作点")
    A("")
    A("![长期](_v5_gold_score.png)")
    A("")
    A("![近三年](_v5_gold_score_3y.png)")
    A("")

    A("## 四、一句话")
    A("")
    if act == "持有":
        tail = (f"波动 {vol:.0%}、预算 {scale:.2f}、闸门 {gate:.2f} → 目标 {target:.0%}，"
                f"与当前 {state:.0%} 相差不到一档，**今天不动**。")
    elif act == "买入":
        tail = (f"目标 {target:.0%} > 当前 {state:.0%}，**明天收盘加至 {pp(target):.1f}pp**"
                f"（现在 {pp(state):.1f}pp）。")
    else:
        tail = (f"目标 {target:.0%} < 当前 {state:.0%}，**明天收盘减至 {pp(target):.1f}pp**"
                f"（现在 {pp(state):.1f}pp）。")
    A(tail)
    A("")
    A(f"> 回测口径：{run_start.date()} ~ {date}（{band_yrs:.1f} 年），"
      f"单边 {S.FEE:.4%}，空仓计货基 {S.CASH_ANNUAL:.2%}；"
      "同期买入持有年化相近、回撤约为其三倍，取舍见 `backtest/_v5_gold_score_bt.md` §0。")
    A("> 模拟结果，非投资建议。")

    md = "\n".join(L) + "\n"
    with open(OUT_MD, "w", encoding="utf-8") as f:
        f.write(md)

    payload = dict(
        date=str(date), as_of=str(date), code=S.SIGNAL_CODE, price=float(last["close"]),
        band_pp=S.BAND_PP, target_pp=pp(target), state_pp=pp(state),
        vol=round(vol, 6), scale=round(scale, 4),
        rate_level=round(lvl, 4), gate=gate, rate_asof=str(rate_date),
        target=target, state=state, action=act, dir=dir_, cond=cond,
        blocked=bool(last["blocked"]), hold_override=args.hold,
        params=dict(vol_target=F.VOL_TARGET,
                    vol_window=F.VOL_WINDOW, vol_min=F.VOL_MIN,
                    rate_th=F.RATE_LEVEL_TH, rate_band=F.RATE_LEVEL_BAND,
                    rate_low=F.RATE_LEVEL_LOW, step=F.STEP, hyst=F.HYST,
                    event_freeze=F.EVENT_FREEZE,
                    fee=S.FEE, slip=S.SLIP, cash=S.CASH_ANNUAL),
        acts=[f"{a['date'].date()} {'加仓' if a['kind'] == 'buy' else '减仓'} "
              f"{a['frm']:.0%}→{a['to']:.0%}" for a in S.actions(df["state"])[-3:]],
        hints=h, hint_conflict=conflict,
    )
    with open(OUT_JSON, "w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False, indent=1)

    print(md)
    print("落盘:", OUT_MD)


if __name__ == "__main__":
    main()
