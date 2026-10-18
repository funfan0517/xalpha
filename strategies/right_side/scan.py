# -*- coding: utf-8 -*-
"""右侧买入策略 · 每日扫描（逐标的，实盘口径，与回测共用同一套 rule.py）。

回答一个问题：**今天基金池里哪些标的达到了右侧买入点？**
  READY = 五票全过（结构止跌 + 结构翻越 + 均线转向 + 零轴上多头 + 放量上涨），当日买点
  WATCH = 四票（差一票），列清缺哪一票，等次日确认
  VETO  = 票数够但被硬否决（5 日涨幅过大 / 流动性不足）
  NO    = 三票及以下

每只标的附带：五票逐条的**实际值 vs 阈值**、结构止损位与距现价距离、250 日回撤、效率比，
供 `_scan_report.py` 渲染每日操作报告。

输出: strategies/right_side/daily/_right_side_scan_out.jsonl（每标的一行）
用法:
  python strategies/right_side/scan.py                # 全池扫描（含行情刷新）
  python strategies/right_side/scan.py --cache        # 不刷新，只用本地库
  python strategies/right_side/scan.py 512800,515080  # 只扫指定标的
"""
import json
import os
import sys

import numpy as np
import pandas as pd

sys.stdout.reconfigure(encoding="utf-8")
_DIR = os.path.dirname(os.path.abspath(__file__))
_ROOT = os.path.dirname(os.path.dirname(_DIR))
for p in (_ROOT, os.path.dirname(_DIR), _DIR):
    if p not in sys.path:
        sys.path.insert(0, p)

import data as _data  # noqa: E402
import rule  # noqa: E402

OUT = os.path.join(_DIR, "daily", "_right_side_scan_out.jsonl")
ACT_JSON = os.path.join(_DIR, "daily", "_right_side_active.json")

STATUS_CN = {"READY": "右侧买点", "WATCH": "差一票", "VETO": "被否决", "NO": "未达标"}


def load_grades():
    """select 阶段产物 -> ({code: 'A'|'B'|'不适合'}, 生成时间)；缺失则空。"""
    if not os.path.exists(ACT_JSON):
        return {}, None
    try:
        d = json.load(open(ACT_JSON, encoding="utf-8"))
    except json.JSONDecodeError:
        return {}, None
    g = {}
    for tag, key in (("A", "A"), ("B", "B"), ("不适合", "not_suitable")):
        for r in d.get(key) or []:
            if r.get("code"):
                g[r["code"]] = tag
    return g, d.get("generated_at")


def _f(f, key, i):
    v = f[key].iloc[i]
    return None if pd.isna(v) else float(v)


def vote_detail(key, f, i):
    """单票 -> (是否通过, 一句话依据)。"""
    c = _f(f, "c", i)
    ok = bool(f[key].iloc[i])
    if key == "v1":
        lo20, lo60 = _f(f, "lo20", i), _f(f, "lo60_prior", i)
        if lo20 is None or lo60 is None or lo60 <= 0:
            return ok, "低点数据不足"
        gap = lo20 / lo60 - 1.0
        return ok, (f"近20日最低 {lo20:.3f} vs 再往前60日最低 {lo60:.3f}"
                    f"（{gap:+.1%}，需 >+{rule.STRUCT_EPS:.1%}）")
    if key == "v2":
        hi = _f(f, "hi60", i)
        if hi is None or c is None:
            return ok, "突破位数据不足"
        return ok, f"收盘 {c:.3f} vs 前{rule.BREAK_WIN}日最高 {hi:.3f}（{c / hi - 1:+.1%}）"
    if key == "v3":
        ma, sl = _f(f, "ma20", i), _f(f, "ma20_slope", i)
        if ma is None or sl is None or c is None:
            return ok, "均线数据不足"
        return ok, f"收盘/MA20 {c / ma - 1:+.1%} · MA20 五日变化 {sl:+.3f}（需 >0）"
    if key == "v4":
        dif, dea = _f(f, "dif", i), _f(f, "dea", i)
        if dif is None or dea is None:
            return ok, "MACD 数据不足"
        return ok, f"DIF {dif:+.4f} vs DEA {dea:+.4f} · DIF {'>' if dif > 0 else '≤'} 0"
    if key == "v5":
        v, vm, r1 = _f(f, "v", i), _f(f, "volma20", i), _f(f, "ret1", i)
        if v is None or vm is None or vm <= 0:
            return ok, "量能数据不足"
        return ok, (f"量比 {v / vm:.2f}×（需 ≥{rule.VOL_MULT:g}×）· "
                    f"当日 {r1:+.2%}" if r1 is not None else f"量比 {v / vm:.2f}×")
    return ok, ""


def main():
    args = [a for a in sys.argv[1:] if not a.startswith("--")]
    refresh = "--cache" not in sys.argv
    want = set(args[0].split(",")) if args and args[0].strip() else None

    panels = _data.load_panels(refresh=refresh)
    end = _data.complete_end(panels["close"].index)
    grades, grade_at = load_grades()
    os.makedirs(os.path.dirname(OUT), exist_ok=True)

    rows = []
    with open(OUT, "w", encoding="utf-8") as fh:
        for meta in _data.POOL:
            code = meta["code"]
            if want and code not in want:
                continue
            base = dict(idx=meta["idx"], cat=meta["cat"], theme=meta["theme"],
                        inner=code, inner_name=meta["inner_name"], off_name=meta["off_name"])
            try:
                df = _data.load_code(panels, code).loc[:end]
                if len(df) < rule.MIN_BARS:
                    raise ValueError(f"样本不足({len(df)}日)")
                f = rule.compute_factors(df)
                sig = rule.build_signals(f)
                i = len(df) - 1
                votes = [dict(key=k, name=rule.VOTE_NAMES[k], short=rule.VOTE_SHORT[k],
                              **{"pass": bool(f[k].iloc[i])},
                              detail=vote_detail(k, f, i)[1])
                         for k in rule.VOTE_NAMES]
                score = int(f["score"].iloc[i])
                veto_hot = bool(f["veto_hot"].iloc[i])
                veto_liq = bool(f["veto_liq"].iloc[i])
                veto = veto_hot or veto_liq
                status = rule.classify(score, veto)
                stop = _f(f, "stop_ref", i)
                close = float(f["c"].iloc[i])
                miss = [v["name"] for v in votes if not v["pass"]]
                if status == "READY":
                    act = f"右侧买点 · 建议买入（结构止损 {stop:.3f}，距现价 {stop / close - 1:+.1%}）" if stop else "右侧买点 · 建议买入"
                elif status == "WATCH":
                    act = "差一票：" + "、".join(miss) + " —— 等次日确认，不提前埋伏"
                elif status == "VETO":
                    why = []
                    if veto_hot:
                        why.append(f"5日涨幅超 {rule.RET5_MAX:.0%}")
                    if veto_liq:
                        why.append(f"20日均额 < {rule.AMT_MIN / 1e8:.1f}亿")
                    act = "票数够但否决：" + "、".join(why)
                else:
                    act = f"未达标（{score}/5）：" + (("、".join(miss)) if score else "全部未过")
                rows.append(dict(
                    ok=True, **base, date=str(df.index[i].date()), close=round(close, 3),
                    score=score, status=status, status_cn=STATUS_CN[status], action=act,
                    votes=votes, missing=miss, veto_hot=veto_hot, veto_liq=veto_liq,
                    stop=None if stop is None else round(stop, 3),
                    stop_dist=None if not stop else round(stop / close - 1, 4),
                    ret5=_f(f, "ret5", i), ret20=_f(f, "ret20", i),
                    dd250=_f(f, "dd250", i), er20=_f(f, "er20", i),
                    bw=_f(f, "bw", i), amt20_yi=round((_f(f, "amt20", i) or 0) / 1e8, 2),
                    ma20=_f(f, "ma20", i), ma60=_f(f, "ma60", i),
                    grade=grades.get(code, "—"), grade_at=grade_at,
                ))
            except Exception as e:  # noqa: BLE001 - 单标的失败不影响其他
                rows.append(dict(ok=False, **base, err=f"{type(e).__name__}: {e}"))
            fh.write(json.dumps(rows[-1], ensure_ascii=False) + "\n")

    good = [r for r in rows if r.get("ok")]
    ready = [r for r in good if r["status"] == "READY"]
    watch = [r for r in good if r["status"] == "WATCH"]
    veto = [r for r in good if r["status"] == "VETO"]
    print(f"[右侧买入] 扫描 {len(good)} 只 | 数据日 {good[0]['date'] if good else '—'}"
          f"{'（已刷新）' if refresh else '（本地缓存）'} | 参数 {rule.LABEL}"
          f"（{rule.VOTE_ENTER}票买入 / {rule.VOTE_WATCH}票观察）")
    print(f"  右侧买点 {len(ready)} 只：" + (" ".join(f"{r['inner']}" for r in ready) or "（无）"))
    print(f"  差一票   {len(watch)} 只：" + (" ".join(
        f"{r['inner']}(缺{r['missing'][0] if len(r['missing']) == 1 else '/'})" for r in watch) or "（无）"))
    if veto:
        print(f"  被否决   {len(veto)} 只：" + " ".join(r["inner"] for r in veto))
    print(f"  明细 -> {OUT}")


if __name__ == "__main__":
    main()
