# -*- coding: utf-8 -*-
"""V6 · 双利差锚回测 → backtest/_v6_bt.{md,jsonl}。

对照分三组，回答三个不同的问题：
  ① 双轴是否有用：V6 双轴 vs 只久期轴 vs 只品种轴 vs v0 固定哑铃 vs 固定中性；
  ② 品种轴是不是「偷偷多拿了信用债」：固定 70% 003376 + 30% 000032、满仓 000032；
  ③ 参数是否脆弱：期限利差死区 5/10/20bp、信用利差死区 3/5/10bp 的网格。
口径：月末信号 / T+1 承担 / 单边 10bp / rf 1.40%。

用法: python strategies/bond_cash/v6_dual_spread/backtest.py [--force]
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
for _p in (os.path.dirname(_HERE), _HERE):
    while _p in sys.path:
        sys.path.remove(_p)
    sys.path.insert(0, _p)

import rule      # noqa: E402
import data      # noqa: E402
import engine    # noqa: E402

CFG = rule.CFG
OUT_DIR = os.path.join(_HERE, "backtest")
OUT_MD = os.path.join(OUT_DIR, "_v6_bt.md")
OUT_JSONL = os.path.join(OUT_DIR, "_v6_bt.jsonl")


def _pct(v, sign=True):
    return "—" if v is None else (f"{v:+.2f}%" if sign else f"{v:.2f}%")


def _row(nm, m, ex=""):
    return (f"| {nm} | {_pct(m['cagr'])} | {_pct(m['vol'], False)} | {m['sharpe']:.2f} | "
            f"{_pct(m['mdd'])} | {_pct(m['worst_year'])} | {ex} |")


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--force", action="store_true")
    args = ap.parse_args(argv)
    navs, asof, failed = data.load_all(force=args.force)
    curve = data.load_curve()
    if curve.empty:
        print("[error] 缺曲线缓存")
        return 1

    long_c, rate_c, credit_c = CFG["long_leg"], CFG["rate_leg"], CFG["credit_leg"]
    df = pd.concat({long_c: navs[long_c], rate_c: navs[rate_c], credit_c: navs[credit_c]},
                   axis=1, join="inner").dropna()
    spread = curve[CFG["carry"]["long"]] - curve[CFG["carry"]["short"]]
    ready = engine.rolling_pct(spread, CFG["carry"]["years"], CFG["carry"]["min_obs"]).dropna()
    start = ready.index[0]

    bt, sig = engine.backtest(navs, curve, start=start)
    net = bt["ret"]
    m_v6 = engine.metrics(net)
    bt_dur, _ = engine.backtest(navs, curve, start=start, dur_only=True)
    bt_cr, _ = engine.backtest(navs, curve, start=start, credit_only=True)

    rets = df.pct_change().dropna()
    fixed = {
        "**基准 v0 固定中性 35/65**": {long_c: 0.35, rate_c: 0.65},
        "v0 旧配比 70/30（2026-11-20 前）": {long_c: 0.70, rate_c: 0.30},
        "全防守 100% 3-5 政金": {rate_c: 1.00},
        "全进攻 100% 7-10 国开": {long_c: 1.00},
        "固定 35/65 但底仓用信用债": {long_c: 0.35, credit_c: 0.65},
        "满仓信用债 000032": {credit_c: 1.00},
    }
    fixed_net = {}
    for k, wm in fixed.items():
        s = sum(rets[c] * v for c, v in wm.items())
        fixed_net[k] = s[s.index >= pd.Timestamp(start)]

    scenarios = [
        ("**V6 双轴（期限利差 × 信用利差）**", net, bt["turn"]),
        ("只久期轴（底仓固定 011983）", bt_dur["ret"], bt_dur["turn"]),
        ("只品种轴（长端固定 70%）", bt_cr["ret"], bt_cr["turn"]),
        ("**基准 v0 固定中性 35/65**", fixed_net["**基准 v0 固定中性 35/65**"], None),
        ("v0 旧配比 70/30（2026-11-20 前）", fixed_net["v0 旧配比 70/30（2026-11-20 前）"], None),
        ("全防守 100% 3-5 政金", fixed_net["全防守 100% 3-5 政金"], None),
        ("全进攻 100% 7-10 国开", fixed_net["全进攻 100% 7-10 国开"], None),
        ("固定 35/65 但底仓用信用债", fixed_net["固定 35/65 但底仓用信用债"], None),
        ("满仓信用债 000032", fixed_net["满仓信用债 000032"], None),
    ]

    lines, rows = [], []
    A = lines.append
    A("# V6 · 双利差锚 · 回测")
    A("")
    A(f"> 生成 {datetime.now():%Y-%m-%d %H:%M} · 由 `strategies/bond_cash/v6_dual_spread/backtest.py` "
      "产出，**请勿手改**。")
    A(f"> 口径：中债曲线（期限利差 10Y−1Y、信用利差 AAA3Y−国债3Y）· 月末信号 / T+1 承担 · "
      f"单边 {CFG['fee_side'] * 1e4:.0f}bp · rf {CFG['rf_annual']:.2%} · "
      f"曲线截止 {curve.index[-1].date()} · 净值截止 {max(asof.values())}。")
    A(f"> 窗口 {net.index[0].date()} ~ {net.index[-1].date()}（{len(net)} 交易日）；"
      f"久期 score 映射 0/17.5/35/52.5/70%，死区 期限 {CFG['slope']['deadband_bp']:.0f}bp / "
      f"信用 {CFG['credit']['deadband_bp']:.0f}bp。")
    A("")
    A("| 方案 | 年化 | 波动 | 夏普 | 最大回撤 | 最差单年 | 换手 |")
    A("|---|---:|---:|---:|---:|---:|---:|")
    for nm, r, turn in scenarios:
        m = engine.metrics(r)
        rows.append(dict(name=nm.strip("*"), **m))
        A(_row(nm, m, f"Σ\\|Δw\\|={turn:.1f}" if turn is not None else "—"))
    A("")

    A("**双票状态分布（全样本采样）**")
    A("")
    A("| 票 | −1 | 0 | +1 |")
    A("|---|---:|---:|---:|")
    for c in ("carry", "slope", "credit"):
        v = sig[c].value_counts()
        A(f"| {c} | {int(v.get(-1, 0))} | {int(v.get(0, 0))} | {int(v.get(1, 0))} |")
    A("")
    sw = int((bt["base"] != bt["base"].shift(1)).sum())
    in_credit = float((bt["base"] == credit_c).mean())
    A(f"- 底仓在信用债的天数占比 **{in_credit * 100:.1f}%**，品种切换 **{sw}** 次")
    A(f"- 长端平均占比 **{bt['w'].mean() * 100:.1f}%**（v0 = 70%），"
      f"最低 {bt['w'].min() * 100:.1f}% / 最高 {bt['w'].max() * 100:.1f}%")
    A("")

    A("**死区敏感性（只改死区，其余不变）**")
    A("")
    A("| 期限死区 | 信用死区 | 年化 | 夏普 | 最大回撤 | Σ\\|Δw\\| |")
    A("|---|---:|---:|---:|---:|---:|")
    sl0, cr0 = CFG["slope"]["deadband_bp"], CFG["credit"]["deadband_bp"]
    for ds, dc in ((5.0, 5.0), (10.0, 5.0), (20.0, 5.0), (10.0, 3.0), (10.0, 10.0)):
        CFG["slope"]["deadband_bp"], CFG["credit"]["deadband_bp"] = ds, dc
        b, _ = engine.backtest(navs, curve, start=start)
        m = engine.metrics(b["ret"])
        rows.append(dict(name=f"死区 {ds:.0f}/{dc:.0f}", **m))
        A(f"| ±{ds:.0f}bp | ±{dc:.0f}bp | {_pct(m['cagr'])} | {m['sharpe']:.2f} | "
          f"{_pct(m['mdd'])} | {b['turn']:.1f} |")
    CFG["slope"]["deadband_bp"], CFG["credit"]["deadband_bp"] = sl0, cr0
    A("")

    A("**成本敏感性（V6 双轴）**")
    A("")
    A("| 单边成本 | 年化 | 最大回撤 | 夏普 |")
    A("|---|---:|---:|---:|")
    for f in CFG["fee_side_grid"]:
        b, _ = engine.backtest(navs, curve, start=start, fee_side=f)
        m = engine.metrics(b["ret"])
        rows.append(dict(name=f"成本{f}", **m))
        A(f"| {f * 1e4:.0f}bp | {_pct(m['cagr'])} | {_pct(m['mdd'])} | {m['sharpe']:.2f} |")
    A("")

    yv = net.groupby(net.index.year).apply(lambda x: (1 + x).prod() - 1)
    y0s = fixed_net["**基准 v0 固定中性 35/65**"]
    y0 = y0s.groupby(y0s.index.year).apply(lambda x: (1 + x).prod() - 1)
    A("**逐年（V6 vs 基准 v0 固定中性 35/65）**")
    A("")
    A("| 年 | V6 | v0 35/65 | 差 |")
    A("|---|---:|---:|---:|")
    for y in sorted(set(yv.index) | set(y0.index)):
        a, b = float(yv.get(y, 0)) * 100, float(y0.get(y, 0)) * 100
        A(f"| {y} | {_pct(a)} | {_pct(b)} | {_pct(a - b)} |")
    A("")

    m_dur = engine.metrics(bt_dur["ret"])
    m_cr = engine.metrics(bt_cr["ret"])
    m_v0 = engine.metrics(y0s)
    m_v0o = engine.metrics(fixed_net["v0 旧配比 70/30（2026-11-20 前）"])
    m_fixcr = engine.metrics(fixed_net["固定 35/65 但底仓用信用债"])
    A("## 结论与边界")
    A("")
    A(f"- V6 双轴 年化 **{m_v6['cagr']:+.2f}%** / 回撤 **{m_v6['mdd']:+.2f}%** / 夏普 "
      f"{m_v6['sharpe']:.2f}；只久期轴 {m_dur['cagr']:+.2f}% / {m_dur['mdd']:+.2f}% / "
      f"{m_dur['sharpe']:.2f}；只品种轴 {m_cr['cagr']:+.2f}% / {m_cr['mdd']:+.2f}% / "
      f"{m_cr['sharpe']:.2f}。")
    A(f"- 基准：v0 固定中性 35/65 {m_v0['cagr']:+.2f}% / {m_v0['mdd']:+.2f}% / {m_v0['sharpe']:.2f}；"
      f"旧配比 70/30 {m_v0o['cagr']:+.2f}% / {m_v0o['mdd']:+.2f}% / {m_v0o['sharpe']:.2f}；"
      f"固定 35/65 但底仓换信用债 {m_fixcr['cagr']:+.2f}% / {m_fixcr['mdd']:+.2f}% / "
      f"{m_fixcr['sharpe']:.2f}。")
    A("- 「只久期轴」与「只品种轴」两行用来分离贡献：双轴若跑不过任一单轴，说明两轴在互相抵消"
      "（和 v3 里 carry 与 trend 常常抵消是同一个病）。")
    A("- 边界：样本 2023-05~2026-11 仅 3.5 年（受曲线缓存与 3 年分位限制）；"
      "000032 是主动信用纯债而非指数，信用下沉程度不可控；"
      "券种切换只按**利差动量**、不含流动性/估值，实际成交的赎回费与到账时滞未建模（按 10bp 单边近似）。")
    A("- 未做：期限利差用 10Y−1Y 单点，未做 2s5s10s 蝶式分解；信用利差只用 AAA，未区分评级下沉阶梯。")
    A("")

    os.makedirs(OUT_DIR, exist_ok=True)
    with open(OUT_MD, "w", encoding="utf-8") as f:
        f.write("\n".join(lines) + "\n")
    with open(OUT_JSONL, "w", encoding="utf-8") as f:
        for r in rows:
            f.write(json.dumps(r, ensure_ascii=False) + "\n")
    print(f"[ok] {OUT_MD}")
    print(f"[ok] {OUT_JSONL}")
    print(f"  V6 {m_v6['cagr']:+.2f}% / {m_v6['mdd']:+.2f}% / {m_v6['sharpe']:.2f}  "
          f"turn={bt['turn']:.1f}  credit_days={in_credit * 100:.0f}%  (v0 {m_v0['cagr']:+.2f}%)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
