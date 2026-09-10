# -*- coding: utf-8 -*-
"""四灯 5-10 天短线变种 · 今日信号扫描（真实主力/换手版）。

与回测引擎(_st10_rule.py)同一套规则; 差别在于: 回测历史无资金/换手明细, 走量价代理;
本扫描优先用本地 mx 快照的真实「主力净流入 / 换手率 / 量比 / 成交额」:
  D2 主力(加分)  —— 用真实主力净占比 main_pct = 主力净流入/成交额 打分(0-2);
                    无快照标的降级为量价代理(与回测一致), 并标注 has_main=False。
  D3 换手/热度   —— 核心仍用「量能相对自身60日中枢」vr60 判历史合理带(ETF 份额近似恒定,
                    换手率相对历史中枢 ≈ 成交量相对历史中枢); 有快照时追加真实换手率
                    绝对上限否决: 换手 > 20% 视为爆量出货区, 直接否决开仓。

输出: 每标的一行 JSON -> data/_st10_scan_out.jsonl(由 _st10_scan_report.py 汇总)。
用法: python strategies/four_lights/short_5_10d/_st10_scan.py [code1,code2,...]
"""
import io
import json
import os
import re
import sys

import numpy as np
import pandas as pd

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8")

_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import _st10_rule as rule  # noqa: E402

MX = "G:/tradingagents/fund_data/data/mx_snapshot_latest.json"
OUT = os.path.join(_ROOT, "data", "_st10_scan_out.jsonl")
START = "2022-01-01"          # 抓取起点: 覆盖 MA60 + 绝对动量60 + vr60 的 warm-up
TURN_VETO = 20.0              # 真实换手率 > 20% 视为爆量出货区(否决开仓)
POOL = rule.POOL


def _num_money(s):
    """'2677万元' / '-4.743亿元' / '32.43亿' -> float(元); 解析失败返回 None。"""
    if s is None:
        return None
    t = str(s).strip().replace(",", "")
    if t in ("", "-", "--", "None", "nan"):
        return None
    m = re.match(r"[-+]?[\d.]+", t)
    if not m:
        return None
    v = float(m.group())
    if "亿" in t:
        v *= 1e8
    elif "万" in t:
        v *= 1e4
    return v


def _num_pct(s):
    if s is None:
        return None
    t = str(s).strip().replace("%", "")
    try:
        return float(t)
    except ValueError:
        return None


def load_snapshot():
    """mx 快照 -> {code: {turn, vol_ratio, amount, main_net, main_pct, name}}, snap_time。"""
    d = json.load(open(MX, encoding="utf-8"))
    funds = d.get("funds") or {}
    out = {}
    for code, v in funds.items():
        amount = _num_money(v.get("amount"))
        main_net = _num_money(v.get("mainflow"))
        main_pct = (main_net / amount * 100) if (amount and main_net is not None) else None
        out[code] = dict(
            name=v.get("name"), turn=_num_pct(v.get("turnover")),
            vol_ratio=v.get("vol_ratio"), amount=amount,
            main_net=main_net, main_pct=main_pct, today=v.get("today"),
        )
    return out, d.get("snapshot_time", "?")


def real_capital_score(yang, vr5, it):
    """D2 真实主力打分(0-2): 净流入越强+量价配合越好分越高; 净流出=0(只降档不否决)。"""
    pct, net = it.get("main_pct"), it.get("main_net")
    if pct is None:
        return None  # 无快照 -> 调用方走代理
    if pct <= 0:
        return 0
    vr = it.get("vol_ratio")
    strong = yang and pct > 5 and ((vr5 is not None and vr5 >= 1.5) or (vr is not None and vr >= 1.5))
    return 2 if strong else 1


def level_weight(trend, s):
    """仓位档(与 _st10_rule 决策表一致)。"""
    if trend == 1:
        return 1.0 / 3.0
    if trend == 2 and s >= 7:
        return 1.0
    if trend == 2 and 5 <= s <= 6:
        return 2.0 / 3.0
    return 1.0 / 3.0


def scan_one(code, it):
    df = rule.load_bars(code, start=START)
    n = len(df)
    if n < 130:
        return dict(ok=False, err="样本不足(<130日)")
    c = df["close"].astype(float)
    o = df["open"].astype(float)
    v = df["volume"].astype(float)
    ma10 = c.rolling(10).mean()
    v5 = v.rolling(5).mean()

    sig = rule.build_signals(df)
    i = -1
    close = float(c.iloc[i])
    yang = bool(close > float(o.iloc[i]))
    vr5 = float(v.iloc[i] / v5.iloc[i]) if v5.iloc[i] > 0 else None

    trend = int(sig["trend"][i])
    m = int(sig["m"][i])
    h = int(sig["h"][i])
    c_proxy = int(sig["c"][i])

    # ---- D2 真实主力覆盖 ----
    c_real = real_capital_score(yang, vr5, it)
    has_main = c_real is not None
    cscore = c_real if has_main else c_proxy

    # ---- D3 真实换手率上限否决(爆量出货区) ----
    turn = it.get("turn")
    turn_veto = turn is not None and turn > TURN_VETO
    if turn_veto:
        h = 0

    s = trend + m + cscore + h
    close_ma10 = close > float(ma10.iloc[i]) if not np.isnan(ma10.iloc[i]) else False
    ready = (trend >= 1) and (m >= 1) and (h >= 1) and close_ma10
    weight = level_weight(trend, s) if ready else 0.0

    # ---- 出场信号(与回测一致) ----
    exits = []
    if trend == 0:
        exits.append("趋势转熊(L1强制清仓)")
    if bool(sig["death"][i]):
        exits.append("MA5/MA10死叉")
    if bool(sig["mom_neg"][i]):
        exits.append("动量转负(近5日<0且破MA10)")

    mom5 = float(c.pct_change(rule.MOM_MAIN).iloc[i]) * 100
    mom3 = float(c.pct_change(rule.MOM_SUB).iloc[i]) * 100
    if exits:
        dec, note = "卖出", " / ".join(exits)
    elif ready:
        dec, note = "买入", f"趋势{trend}档 & 动量{m} & 换手{h} & 主力{'(真实)' if has_main else '(代理)'}{cscore} → 仓位{weight:.0%}"
    elif trend >= 1 and (m >= 1 or h >= 1):
        dec, note = "持有", f"趋势{trend}档尚可, 短线条件不足(动量{m}/换手{h})"
    else:
        dec, note = "观望", f"趋势{trend}档但短线无买点(动量{m}/换手{h})"

    return dict(
        ok=True, inner=code, date=str(df["date"].iloc[i].date()), close=round(close, 3),
        today=round((close / float(c.iloc[i - 1]) - 1) * 100, 2),
        mom5=round(mom5, 2), mom3=round(mom3, 2),
        trend=trend, m=m, c=cscore, c_proxy=c_proxy, h=h, s=s,
        weight=round(weight, 3), dec=dec, note=note,
        turn=turn, vol_ratio=it.get("vol_ratio"), main_pct=it.get("main_pct"),
        has_main=has_main, turn_veto=bool(turn_veto),
        death=bool(sig["death"][i]), mom_neg=bool(sig["mom_neg"][i]),
    )


def main():
    snap, snap_time = load_snapshot()
    want = set(sys.argv[1].split(",")) if len(sys.argv) > 1 and sys.argv[1].strip() else None
    fh = open(OUT, "w", encoding="utf-8")
    for row in POOL:
        code = row["code"]
        if want and code not in want:
            continue
        it = snap.get(code, {})
        meta = dict(idx=row["idx"], cat=row["cat"], theme=row["theme"],
                    off_name=row["off_name"], inner_name=row["inner_name"], snap=snap_time)
        try:
            r = scan_one(code, it)
        except Exception as e:  # noqa: BLE001 - 单标的失败不影响其他
            r = dict(ok=False, err=f"{type(e).__name__}: {e}")
        ln = json.dumps({**meta, **r}, ensure_ascii=False)
        print(ln, flush=True)
        fh.write(ln + "\n")
    fh.close()


if __name__ == "__main__":
    main()
