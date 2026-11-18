# -*- coding: utf-8 -*-
"""V5 黄金计分仓位 · 每日操作（规格 §4 报告输出）。

输出（本目录 `daily/`）：`_v5_gold_score_daily.md` · `_v5_gold_score_daily.json`。
报告字段按规格 §4 逐条：**score / 各因子符号 / 目标档 e* / 当前档 / 推荐动作 / 置信度 / 数据截止日**，
另附「不计分项」（FedWatch / 央行购金 / 内外价差）的当日提示 —— 按规格 §2 它**永不改变仓位数字**，
只在方向冲突时标注「观察，谨慎」。

用法（从仓库根跑）：
    python strategies/vol_legs/v5_gold_score/daily/_daily_scan.py            # 会先补实际利率缓存
    python strategies/vol_legs/v5_gold_score/daily/_daily_scan.py --offline  # 不联网，只用本地缓存
    python strategies/vol_legs/v5_gold_score/daily/_daily_scan.py --hold 0.5 # 覆盖当前档（默认读状态机）

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


def sign_txt(v):
    v = int(v)
    return "+1" if v > 0 else ("-1" if v < 0 else "0")


def hints():
    """不计分项（规格 §2）：FedWatch / 央行购金 / 内外价差。缺文件 = 未维护。"""
    if not os.path.exists(HINTS):
        return None
    try:
        with open(HINTS, encoding="utf-8") as f:
            d = json.load(f)
    except (OSError, ValueError):
        return None
    return d if isinstance(d, dict) else None


def flow_note(flow, shares=None, close=None):
    """资金流近期提示（**不计分**，规格 §1 括号条款）。

    份额口径（上交所 `TOT_VOL`，全史）优先：最近 5 日净申购天数 + 累计净申购金额（Δ份额×收盘价）；
    主力净流入（东财，只有 120 天）作次选。
    """
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
    ap = argparse.ArgumentParser(description="V5 黄金计分仓位 · 每日读数")
    ap.add_argument("--offline", action="store_true", help="不联网（只用本地缓存）")
    ap.add_argument("--hold", type=float, default=None,
                    help="覆盖当前档（占带比例，如 0.5）；默认按状态机回放")
    args = ap.parse_args()

    px, rate, flow, shares = S.load(refresh=not args.offline)
    p = S.params()
    if args.hold is not None:
        p["START"] = float(args.hold)
    df = S.run(px, rate, flow, p, shares=shares)
    last = df.iloc[-1]
    date = df.index[-1].date()

    score = int(last["score"])
    target = float(last["target"])
    state = float(last["state"])
    act = str(last["action"])
    conf = str(last["conf_eff"])
    dir_ = {"买入": "buy", "卖出": "sell"}.get(act, "")
    band_yrs = (df.index[-1] - df.index[0]).days / 365.25

    # ---- 触发依据
    parts = []
    if int(last["f_p"]) != 0:
        parts.append(f"价格趋势 {'贴 20 日高 + 双均线上方' if last['f_p'] > 0 else '贴 20 日低 + 双均线下方'}")
    else:
        parts.append("价格趋势不满足双向确认")
    if int(last["f_r"]) != 0:
        parts.append(f"DFII10 {F.RATE_WIN} 日变化 {last['rate_chg']:+.0f}bp"
                     f"（{'利多' if last['f_r'] > 0 else '利空'}）")
    else:
        parts.append(f"DFII10 {F.RATE_WIN} 日变化 {last['rate_chg']:+.0f}bp 未过 ±{F.RATE_TH:.0f}bp")
    blocked = bool(last["blocked"])
    if blocked:
        parts.append("事件窗口内（FOMC/非农/PCE ±{} 日）：只许持有或减仓".format(F.EVENT_FREEZE))
    cond = "；".join(parts)

    h = hints()
    hint_lines = []
    if h:
        for k, lab in (("fedwatch", "FedWatch 加息/降息概率"), ("cb_gold", "央行购金"),
                       ("spread", "内外价差")):
            if h.get(k):
                hint_lines.append(f"- {lab}：{h[k]}")
        if h.get("note"):
            hint_lines.append(f"- 备注：{h['note']}")
    conflict = False
    if h and h.get("direction") in ("bull", "bear"):
        hb = 1 if h["direction"] == "bull" else -1
        conflict = hb * score < 0
    if conflict:
        hint_lines.append("- ⚠ 与 score 方向冲突 → **观察，谨慎**（置信度降一档；**仓位数字不变**）")

    L = []
    A = L.append
    A(f"# V5 黄金计分仓位 · 每日推荐操作（{date}）")
    A("")
    A(f"> 标的：**场内 `{S.SIGNAL_CODE}` 黄金ETF华安**（收盘 {last['close']:.4f}）· "
      f"信号/执行/回测同一只 ETF · 目标带 {S.BAND_PP:g}pp（1.0 = 满带 = {S.BAND_PP:g}pp）")
    A(f"> 决策在 T 日盘后、**T+1 收盘成交**；「今日动作」= 明天要下的单。"
      f"数据截止：**价格 {date} / 实际利率 {last['rate_date'].date()}**（T 日只用 ≤T−1）。")
    A("")

    A("## 今日操作一览")
    A("")
    A("| 项 | 读数 |")
    A("|---|---|")
    A(f"| score | **{score:+d}**（区间 −3…+3；两因子口径 −2…+2） |")
    A(f"| 因子符号 | f_p 价格趋势 **{sign_txt(last['f_p'])}** · "
      f"f_r 实际利率 **{sign_txt(last['f_r'])}** · f_f 资金流 **剔除**"
      "（全史份额已复核，仍是负贡献 → 见回测 §8.2） |")
    A(f"| 目标档 e* | **{target:.0%}** 带 = {pp(target):.1f}pp / {S.BAND_PP:g}pp |")
    A(f"| 当前档 e | **{state:.0%}** 带 = {pp(state):.1f}pp |")
    A(f"| 推荐动作 | **{act}**" + (f"（{state:.0%} → {target:.0%}）" if dir_ else "（不动作）") + " |")
    A(f"| 置信度 | **{conf}**"
      + ("（缺 f_f 已降一档）" if int(last["n_f"]) < 3 else "") + " |")
    A(f"| 数据截止日 | 价格 {date} · DFII10 {last['rate_date'].date()} |")
    A("")
    A(f"**依据**：{cond}。")
    A("")

    A("## 一、规则读数")
    A("")
    A(f"- 价格：收盘 {last['close']:.4f}，MA{F.MA_FAST} / MA{F.MA_SLOW} 与 "
      f"{F.BRK_WIN} 日高/低 的双向确认 → **f_p = {sign_txt(last['f_p'])}**"
      "（只有「强且贴边」才给分，中间地带一律 0）。")
    A(f"- 实际利率：DFII10 的 {F.RATE_WIN} 日变化 **{last['rate_chg']:+.0f}bp**"
      f"（阈值 ±{F.RATE_TH:.0f}bp）→ **f_r = {sign_txt(last['f_r'])}**；"
      f"用的是 {last['rate_date'].date()} 的值（T−1 口径，规格 §6.1）。")
    A(f"- 目标档映射：{score:+d} 分 → {target:.0%}"
      + ("（缺因子 → 低置信度 → 极端档向 50% 收缩一档）"
         if int(last["n_f"]) < 3 and conf == "低" else "") + "。")
    A(f"- 滞后防抖：|e* − e| ≥ {F.HYST} 档（{F.HYST * 25}pp）才动作，"
      f"当前差 {abs(target - state) * 100:.0f}pp → {'触发' if act != '持有' else '不触发'}。")
    A("")

    A("## 二、不计分项（只作提示，永不改变仓位数字）")
    A("")
    if hint_lines:
        L.extend(hint_lines)
    else:
        A(f"- 未维护（`data/_gold_hints.json` 不存在）：FedWatch / 央行购金 / 内外价差 三项缺输入。")
    A(f"- 资金流提示：{flow_note(flow, shares, float(last['close']))}")
    A("")
    A("> 规格 §2：这些项与 score 冲突时只在报告标注「观察，谨慎」并降一档置信度，"
      "**绝不改变目标档数字**。")
    A("> 资金流为何不计分：上交所份额全史已补齐（2013-07 起 3234 日 0 缺失）并实测，"
      "顺势口径把回撤从 -17.8% 恶化到 -22.8%、逆势只修回撤不增收益 —— 两个方向都跑输"
      "「恒定半仓」。详见 `backtest/_v5_gold_score_bt.md` §8.2 与 "
      "`research/flow_source/_probe_flow.md`。")
    A("")

    A("## 三、净值与操作点")
    A("")
    A(f"![长期](_v5_gold_score.png)")
    A("")
    A(f"![近三年](_v5_gold_score_3y.png)")
    A("")

    A("## 四、一句话")
    A("")
    if act == "持有":
        tail = (f"score {score:+d}、目标 {target:.0%} 与当前 {state:.0%} 相差不到一档，**今天不动**。")
    elif act == "买入":
        tail = (f"score {score:+d} → 目标 {target:.0%}，**明天收盘买入至 {pp(target):.1f}pp**"
                f"（现在 {pp(state):.1f}pp）。")
    else:
        tail = (f"score {score:+d} → 目标 {target:.0%}，**明天收盘减至 {pp(target):.1f}pp**"
                f"（现在 {pp(state):.1f}pp）。")
    A(tail)
    A("")
    A(f"> 回测口径：{df.index[0].date()} ~ {date}（{band_yrs:.1f} 年），"
      f"单边 {S.FEE:.4%}，空仓计货基 {S.CASH_ANNUAL:.2%}；"
      "本口径与「恒定半仓」几乎等价，取舍见 `backtest/_v5_gold_score_bt.md` §0。")
    A("> 模拟结果，非投资建议。")

    md = "\n".join(L) + "\n"
    with open(OUT_MD, "w", encoding="utf-8") as f:
        f.write(md)

    payload = dict(
        date=str(date), as_of=str(date), code=S.SIGNAL_CODE, price=float(last["close"]),
        band_pp=S.BAND_PP, target_pp=pp(target), state_pp=pp(state),
        score=score, f_p=int(last["f_p"]), f_r=int(last["f_r"]), f_f=None,
        conf=conf, n_factors=int(last["n_f"]),
        rate_chg_bp=round(float(last["rate_chg"]), 2), rate_asof=str(last["rate_date"].date()),
        target=target, state=state, action=act, dir=dir_, cond=cond,
        blocked=blocked, hold_override=args.hold,
        params=dict(ma=[F.MA_FAST, F.MA_SLOW], brk=F.BRK_WIN,
                    rate_win=F.RATE_WIN, rate_th=F.RATE_TH, hyst=F.HYST,
                    shrink=F.SHRINK, fee=S.FEE, slip=S.SLIP, cash=S.CASH_ANNUAL),
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
