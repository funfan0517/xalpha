# -*- coding: utf-8 -*-
"""V9 · 当日读数：10Y 档位 / 历史分位 / 目标仓位（手动脚本，不进每日操作报告）。

用法：python strategies/bond_cash/v9_yield_band/daily/_daily_scan.py [--force] [--refresh]
产物：daily/_v9_daily.{md,json}
"""
import argparse
import io
import json
import os
import sys
from datetime import datetime

import pandas as pd

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8")

_HERE = os.path.dirname(os.path.abspath(__file__))
_ROOT_DIR = os.path.dirname(_HERE)
for _p in (_ROOT_DIR, _HERE):
    while _p in sys.path:
        sys.path.remove(_p)
    sys.path.insert(0, _p)

import rule      # noqa: E402
import data      # noqa: E402
import engine    # noqa: E402

OUT_MD = os.path.join(_HERE, "_v9_daily.md")
OUT_JSON = os.path.join(_HERE, "_v9_daily.json")


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--force", action="store_true")
    ap.add_argument("--refresh", action="store_true")
    args = ap.parse_args(argv)

    y = data.load_yield(refresh=args.refresh)
    navs, _, _ = data.load_all(force=args.force)
    sig = engine.signal_frame(y)
    row = sig.dropna(subset=["yield"]).iloc[-1]
    state = str(row["state"])
    base_w = dict(rule.CFG["weights"][state])
    bp = rule.CFG["sleeve_bond_pp"]
    y10 = float(row["yield"])
    pct = None if pd.isna(row["pct"]) else round(float(row["pct"]), 1)

    # 波动目标层：用当前基础档位组合的 CFG["vol_target"]["lookback"]（默认 60）日已实现波动，
    # 月末计算、下月执行；系数变动 <min_change 不调。
    vt = rule.CFG["vol_target"]
    vol_s, scale_s = None, 1.0
    if vt.get("enabled", False):
        scale_series, vol_s = engine.vol_scale(navs, engine.target_matrix(sig["state"]), cfg=vt)
        scale_s = float(scale_series.iloc[-1]) if len(scale_series) else 1.0
        if pd.isna(scale_s):
            scale_s = 1.0
    w = {c: v * scale_s for c, v in base_w.items()}
    w[rule.CFG["short_leg"]] = w.get(rule.CFG["short_leg"], 0.0) + (1.0 - scale_s)

    prev = str(sig["state"].iloc[-2]) if len(sig) > 1 else state
    if prev == state:
        action = "维持当前档位（只在月末 / 档位切换次日执行）"
    else:
        action = f"档位由 {rule.STATE_CN.get(prev, prev)} 切至 {rule.STATE_CN[state]}，下一交易日执行"

    agg = {}
    for c, frac in w.items():
        agg.setdefault(c, dict(code=c, name=rule.NAMES[c], base_frac=base_w.get(c, 0.0),
                               frac=0.0, bond_pp=0.0, cash_pp=0.0, action="买入/持有"))
        agg[c]["frac"] += frac
        agg[c]["bond_pp"] += frac * bp
    cash_code = rule.CFG["cash_leg"]
    agg.setdefault(cash_code, dict(code=cash_code, name=rule.NAMES[cash_code], base_frac=0.0,
                                   frac=0.0, bond_pp=0.0, cash_pp=0.0, action="买入/持有"))
    agg[cash_code]["cash_pp"] += rule.CFG["sleeve_cash_pp"]
    holdings = []
    for h in agg.values():
        h["base_frac"] = round(h["base_frac"], 4)
        h["frac"] = round(h["frac"], 4)
        h["bond_pp"] = round(h["bond_pp"], 2)
        h["cash_pp"] = round(h["cash_pp"], 2)
        h["pp"] = round(h["bond_pp"] + h["cash_pp"], 2)
        if h["cash_pp"] and h["bond_pp"]:
            h["action"] = "债腿内短债 + 现金腿固定"
        elif h["cash_pp"]:
            h["action"] = "现金腿固定"
        holdings.append(h)

    vol_now = None if vol_s is None or not len(vol_s) or pd.isna(vol_s.iloc[-1]) \
        else round(float(vol_s.iloc[-1]), 4)
    obj = dict(as_of=str(sig.index[-1].date()), generated=datetime.now().strftime("%Y-%m-%d %H:%M"),
               y10=round(y10, 4), pct_5y=pct, state=state, state_cn=rule.STATE_CN[state],
               prev_state=prev, action=action, thresholds=rule.CFG["threshold"],
               bond_pp=bp, cash_pp=rule.CFG["sleeve_cash_pp"], holdings=holdings,
               vol_target=dict(enabled=bool(vt.get("enabled")), target=vt.get("target"),
                               lookback=vt.get("lookback"), current_vol=vol_now,
                               scale=round(scale_s, 4),
                               base_weights=base_w, effective_weights={c: round(v, 4) for c, v in w.items()}),
               source=data.SOURCE.get("yield", "—"))

    lines = [
        "# V9 · 10Y 国债收益率阈值久期切换 · 当日读数",
        "",
        f"> 生成 {obj['generated']} · 由 `strategies/bond_cash/v9_yield_band/daily/_daily_scan.py` 产出，**请勿手改**。",
        f"> 10Y 来源：{obj['source']}；数据截止 {obj['as_of']}。",
        "",
        "| 项目 | 读数 |",
        "|---|---:|",
        f"| 10Y 国债收益率 | {y10:.4f}% |",
        f"| 近 {rule.CFG['percentile']['years']} 年历史分位 | {'—' if pct is None else f'{pct:.1f}%'} |",
        f"| 当前档位 | {rule.STATE_CN[state]}（{state}） |",
        f"| 上一档位 | {rule.STATE_CN.get(prev, prev)} |",
        f"| 波动目标 | {vt.get('target', 0) * 100:.1f}%（{vt.get('lookback', 20)} 日已实现波动） |",
        f"| 当前已实现波动 | {'—' if vol_now is None else f'{vol_now * 100:.2f}%'} |",
        f"| 缩放系数（月末执行） | {scale_s:.2f} |",
        f"| 操作提示 | {action} |",
        "",
        "| 标的 | 基础档位 | 缩放后占债腿 | 债腿 pp | 现金腿 pp | 总资产 pp | 动作 |",
        "|---|---:|---:|---:|---:|---:|---|",
    ]
    for h in holdings:
        lines.append(f"| `{h['code']}` {h['name']} | {h['base_frac'] * 100:.0f}% | "
                     f"{h['frac'] * 100:.0f}% | {h['bond_pp']:.2f}pp | "
                     f"{h['cash_pp']:.2f}pp | {h['pp']:.2f}pp | {h['action']} |")
    lines += [
        "",
        f"> 债腿 {bp:.0f}pp + 现金腿 {rule.CFG['sleeve_cash_pp']:.0f}pp（现金腿固定、不参与切换）。"
        f"三区直接映射：> {rule.CFG['threshold']['high']}% → 长/中/短 30/60/10，"
        f"{rule.CFG['threshold']['low']}%–{rule.CFG['threshold']['high']}% → 60/30/10，"
        f"< {rule.CFG['threshold']['low']}% → 10/30/60。带记忆模式仅作回测对照。",
        f"波动目标层：档位权重 × {scale_s:.2f}，剩余仓位给短债；系数按月末已实现波动计算、下月执行，"
        "非月末读数只作参考。",
        "",
    ]

    with open(OUT_MD, "w", encoding="utf-8") as f:
        f.write("\n".join(lines))
    with open(OUT_JSON, "w", encoding="utf-8") as f:
        json.dump(obj, f, ensure_ascii=False, indent=2)
    print(f"[ok] {OUT_MD}")
    print(f"[ok] {OUT_JSON}")
    print(f"  10Y={y10:.4f}%  pct={pct}%  state={state}  pp={sum(h['pp'] for h in holdings):.2f}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
