# -*- coding: utf-8 -*-
"""行业 ETF 专项：把「买入后大概率能涨」当目标时, 参数该怎么走。

与 _tune.py 的区别:
  * 标的池**只保留 A股行业 ETF**（剔除全球/QDII、策略/商品、债券、宽基）;
  * 目标函数换成**单笔胜率**（P(单笔收益 > 0)）,
    并用「单笔期望 > 0」与「单笔数 >= 100」做护栏 —— 纯胜率可以靠小止盈刷到 90%,
    所以必须同时看期望与样本量。
输出: 控制台表格 + strategies/_sector_tune_report.md
用法: python strategies/_sector_tune.py
"""
import os
import sys

import numpy as np
import pandas as pd

sys.stdout.reconfigure(encoding="utf-8")
_DIR = os.path.dirname(os.path.abspath(__file__))
_ROOT = os.path.dirname(_DIR)
for p in (_ROOT, _DIR, os.path.join(_DIR, "lights")):
    if p not in sys.path:
        sys.path.insert(0, p)

import rule  # noqa: E402
from _tune import Bench  # noqa: E402

OUT = os.path.join(_DIR, "_sector_tune_report.md")
MIN_TRADES = 100          # 样本量护栏
L = rule.ACTIVE.lights


def lights(**kw):
    d = dict(L)
    d.update(kw)
    return d


VARIANTS = [("c7 基线（无止盈/无持有上限）", {})]
for _tp in (0.005, 0.01, 0.02, 0.03, 0.05):
    for _mh in (None, 3, 5, 10):
        _tag = f"止盈 {_tp * 100:.1f}%" + (f" + 持有≤{_mh}日" if _mh else "")
        _ov = dict(take_profit=_tp)
        if _mh:
            _ov["max_hold"] = _mh
        VARIANTS.append((_tag, _ov))
VARIANTS += [
    ("止盈 1% + 止损 3%", dict(take_profit=0.01, trail_stop=0.03)),
    ("止盈 2% + 止损 5%", dict(take_profit=0.02, trail_stop=0.05)),
    ("止盈 2% + enter_min=5", dict(take_profit=0.02, enter_min=5.0)),
    ("止盈 3% + enter_min=5", dict(take_profit=0.03, enter_min=5.0)),
]


def pooled(rows):
    rets = np.array([t["ret"] for r in rows for t in (r.get("trade_log") or [])])
    if not len(rets):
        return None
    w, l = rets[rets > 0], rets[rets <= 0]
    return dict(n=len(rets), win=len(w) / len(rets),
                payoff=(w.mean() / abs(l.mean())) if len(l) and l.mean() != 0 else float("inf"),
                exp=rets.mean())


def main():
    codes = [r["code"] for r in rule.POOL if r["cat"] == "A股行业"]
    broad = [r["code"] for r in rule.POOL if r["cat"] == "宽基/另类"]
    bench = Bench(want=set(codes))

    print(f"标的池: A股行业 ETF {len(codes)} 只（剔除 全球QDII/策略商品/债券）\n")
    H = (f"{'变体':<26}{'单笔数':>7}{'胜率':>8}{'盈亏比':>7}{'单笔期望':>9}"
         f"{'年化':>8}{'基准':>8}{'超额':>8}{'持仓':>7}")
    print(H)
    print("-" * len(H.encode("gbk", "ignore")) if False else "-" * 88)

    res = []
    for tag, ov in VARIANTS:
        rows, _ = bench.run(rule.ACTIVE.with_(**ov))
        p = pooled(rows)
        if not p:
            print(f"{tag:<26}  无有效结果")
            continue
        ann = float(np.mean([r["st_ann"] for r in rows]))
        base = float(np.mean([r["base_ann"] for r in rows]))
        pos = float(np.mean([r["pos_ratio"] for r in rows]))
        pf = "∞" if p["payoff"] == float("inf") else f"{p['payoff']:.2f}"
        res.append((tag, p, ann, base, pos, rows))
        print(f"{tag:<26}{p['n']:>7}{p['win'] * 100:>7.1f}%{pf:>7}{p['exp'] * 100:>8.3f}%"
              f"{ann * 100:>7.1f}%{base * 100:>7.1f}%{(ann - base) * 100:>7.1f}%"
              f"{pos * 100:>6.1f}%")

    # 护栏过滤后排序
    ok = [r for r in res if r[1]["n"] >= MIN_TRADES and r[1]["exp"] > 0]
    ok.sort(key=lambda r: -r[1]["win"])
    print(f"\n== 通过护栏（单笔数>={MIN_TRADES} 且 单笔期望>0）按胜率排序 ==")
    for tag, p, ann, base, pos, _r in ok:
        print(f"  {tag:<26} 胜率 {p['win'] * 100:.1f}%  期望 {p['exp'] * 100:+.3f}%  "
              f"超额 {(ann - base) * 100:+.1f}%  持仓 {pos * 100:.1f}%")

    # ---- 「买入后是不是涨过」：持有期内最高价触及买入价×(1+eps) 的比例 ----
    base_rows = [r for tag, p, ann, b, pos, rr in res if tag.startswith("c7") for r in rr]
    if base_rows:
        tr, tot = touch_rates(base_rows, bench)
        print(f"\n== 买入后「曾经涨过」的比例（c7 基线, {tot} 笔, 看持有期内最高价）==")
        for eps_v in sorted(tr):
            print(f"  曾 ≥ 买入价 +{eps_v * 100:.0f}% : {tr[eps_v] * 100:5.1f}%")
        print("  ⚠ 这只是持有期内最高价的触及情况, 不等于能在该价位卖出; "
              "按收盘口径结算的胜率只有 47.9%")

    # ---- 报告 ----
    lines = ["# 行业 ETF 专项：把「买入后大概率能涨」当目标", "",
             f"> 标的池 = A股行业 ETF {len(codes)} 只（剔除 全球/QDII、策略/商品、债券）;"
             f" 基准 = 同期买入持有。",
             f"> 目标函数 = **单笔胜率**（P(单笔收益>0)）, 护栏 = 单笔数≥{MIN_TRADES} 且 单笔期望>0"
             f"（纯胜率可被小止盈刷高, 必须配期望与样本量一起看）。", "",
             "| 变体 | 单笔数 | 胜率 | 盈亏比 | 单笔期望 | 年化 | 基准年化 | 超额 | 持仓 |",
             "|---|---|---|---|---|---|---|---|---|"]
    for tag, p, ann, base, pos, _r in res:
        pf = "∞" if p["payoff"] == float("inf") else f"{p['payoff']:.2f}"
        lines.append(f"| {tag} | {p['n']} | **{p['win'] * 100:.1f}%** | {pf} | "
                     f"{p['exp'] * 100:+.3f}% | {ann * 100:+.1f}% | {base * 100:+.1f}% | "
                     f"{(ann - base) * 100:+.1f}% | {pos * 100:.1f}% |")
    lines += ["", f"> 通过护栏者按胜率排序: " + "；".join(
        f"{t} {p['win'] * 100:.1f}%" for t, p, *_ in ok), ""]
    open(OUT, "w", encoding="utf-8").write("\n".join(lines))
    print(f"\n-> {OUT}")


def touch_rates(rows, bench, eps=(0.0, 0.01, 0.02, 0.03, 0.05)):
    """持有期内**最高价**是否曾达到 买入价×(1+eps) —— 即「买入后是不是涨过」。

    这是"买入后大概率能涨"最贴近字面的读法, 通常远高于按收盘计的胜率 ——
    但它只看盘中最高价, 不等于能在这个价位卖出, 必须与单笔期望一起看。
    """
    hit = {e: 0 for e in eps}
    tot = 0
    for r in rows:
        df = bench.frame(r["code"])
        o = df["open"].to_numpy(float)
        h = df["high"].to_numpy(float)
        for t in r.get("trade_log") or []:
            e, x = t["entry_i"], t["exit_i"]
            px = o[e] if 0 <= e < len(o) else None
            if not px or not np.isfinite(px) or px <= 0:
                continue
            tot += 1
            seg = h[e:x + 1]
            if len(seg):
                for eps_v in eps:
                    if seg.max() >= px * (1 + eps_v):
                        hit[eps_v] += 1
    return ({k: (v / tot if tot else 0.0) for k, v in hit.items()}, tot)


if __name__ == "__main__":
    main()
