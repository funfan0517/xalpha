# -*- coding: utf-8 -*-
"""V5 · 波动率目标回测 → backtest/_v5_bt.{md,jsonl}。

对照：月末 / 双周 / 无滞后带三档调仓；固定哑铃 70/30、固定中性 35/65、全防守、全进攻；
以及「反向波动目标」（高波动加久期）作为机制对照 —— 如果反向也跑得一样好，
说明改善不来自 vol targeting 而是别的东西（例如整段样本都在债牛里）。
另做目标波动 1.0/1.5/2.0/2.5% 的敏感性网格。

用法: python strategies/bond_cash/v5_vol_target/backtest.py [--force]
"""
import argparse
import io
import json
import os
import sys
from datetime import datetime

import numpy as np
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
OUT_MD = os.path.join(OUT_DIR, "_v5_bt.md")
OUT_JSONL = os.path.join(OUT_DIR, "_v5_bt.jsonl")


def _pct(v, sign=True):
    return "—" if v is None else (f"{v:+.2f}%" if sign else f"{v:.2f}%")


def _row(nm, m, ex=""):
    return (f"| {nm} | {_pct(m['cagr'])} | {_pct(m['vol'], False)} | {m['sharpe']:.2f} | "
            f"{_pct(m['mdd'])} | {_pct(m['worst_year'])} | {ex} |")


def _run_w(df, w, fee=None):
    """给定权重序列（未 shift）-> 净收益 + Σ|Δw|。"""
    long_c, base_c = CFG["long_leg"], CFG["base_leg"]
    w = w.shift(1).fillna(0.35).reindex(df.index)
    rets = df.pct_change().dropna()
    gross = w.reindex(rets.index) * rets[long_c] + (1 - w.reindex(rets.index)) * rets[base_c]
    turn = w.reindex(rets.index).diff().abs().fillna(0.0)
    fee = CFG["fee_side"] if fee is None else fee
    return gross - turn * 2 * fee, float(turn.sum())


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--force", action="store_true")
    args = ap.parse_args(argv)
    navs, asof, failed = data.load_all(force=args.force)
    long_c, base_c = CFG["long_leg"], CFG["base_leg"]
    if long_c not in navs or base_c not in navs:
        print("[error] 缺腿净值")
        return 1

    df = pd.concat({long_c: navs[long_c], base_c: navs[base_c]}, axis=1, join="inner").dropna()
    idx = df.index
    start = idx[min(int(CFG["warmup"]), len(idx) - 1)]

    bt = engine.backtest(navs, start=start)
    net = bt["ret"]
    m_v5 = engine.metrics(net)

    bt_2w = engine.backtest(navs, start=start, mode="every_n", n=CFG["every_n"])
    bt_noh = engine.backtest(navs, start=start, hysteresis_pp=0.0)

    fixed = {
        "**基准 v0 固定中性 35/65**": pd.Series(0.35, index=idx),
        "v0 旧配比 70/30（2026-11-20 前）": pd.Series(0.70, index=idx),
        "全防守 100% 3-5 年": pd.Series(0.00, index=idx),
        "全进攻 100% 7-10 年": pd.Series(1.00, index=idx),
    }
    fixed_net = {k: _run_w(df, v, fee=0.0)[0] for k, v in fixed.items()}

    # 反向对照：高波动 → 加久期
    sg = engine.build_signal(navs[long_c], idx)
    inv = (CFG["vol"]["cap_w"] * np.minimum(
        1.0, sg["sigma"] / CFG["vol"]["target_annual"])).clip(0.0, CFG["vol"]["cap_w"])
    inv_net, inv_turn = _run_w(df, engine.apply_hysteresis(inv.ffill()), fee=CFG["fee_side"])

    scenarios = [
        ("**V5 波动率目标（月末）**", net, bt["turn"]),
        (f"V5 波动率目标（每 {CFG['every_n']} 日 / 双周）", bt_2w["ret"], bt_2w["turn"]),
        ("V5 波动率目标（无滞后带）", bt_noh["ret"], bt_noh["turn"]),
        ("反面：高波动加久期（机制对照）", inv_net, inv_turn),
        ("**基准 v0 固定中性 35/65**", fixed_net["**基准 v0 固定中性 35/65**"], None),
        ("v0 旧配比 70/30（2026-11-20 前）", fixed_net["v0 旧配比 70/30（2026-11-20 前）"], None),
        ("全防守 100% 3-5 年", fixed_net["全防守 100% 3-5 年"], None),
        ("全进攻 100% 7-10 年", fixed_net["全进攻 100% 7-10 年"], None),
    ]

    lines, rows = [], []
    A = lines.append
    A("# V5 · 波动率目标 · 回测")
    A("")
    A(f"> 生成 {datetime.now():%Y-%m-%d %H:%M} · 由 `strategies/bond_cash/v5_vol_target/backtest.py` "
      "产出，**请勿手改**。")
    A(f"> 口径：长端腿 003376 的已实现波动 · σ=max(σ21,σ63) · 目标 {CFG['vol']['target_annual']:.2%} · "
      f"上限 {CFG['vol']['cap_w']:.0%} · 熔断 {CFG['vol']['guard_mult']:.0f}× · "
      f"月末调仓 / T+1 承担 · 单边 {CFG['fee_side'] * 1e4:.0f}bp · rf {CFG['rf_annual']:.2%}。")
    A(f"> 窗口 {net.index[0].date()} ~ {net.index[-1].date()}（{len(net)} 交易日）；净值截止 "
      f"{max(asof.values())}。")
    A("")
    A("| 方案 | 年化 | 波动 | 夏普 | 最大回撤 | 最差单年 | 换手 |")
    A("|---|---:|---:|---:|---:|---:|---:|")
    for nm, r, turn in scenarios:
        m = engine.metrics(r)
        rows.append(dict(name=nm.strip("*"), **m))
        A(_row(nm, m, f"Σ\\|Δw\\|={turn:.1f}" if turn is not None else "—"))
    A("")

    A("**目标波动敏感性（月末调仓）**")
    A("")
    A("| 目标年化波动 | 平均长端 | 年化 | 夏普 | 最大回撤 | Σ\\|Δw\\| |")
    A("|---|---:|---:|---:|---:|---:|")
    tgt0 = CFG["vol"]["target_annual"]
    for t in (0.010, 0.015, 0.020, 0.025):
        CFG["vol"]["target_annual"] = t
        b = engine.backtest(navs, start=start)
        m = engine.metrics(b["ret"])
        rows.append(dict(name=f"目标{t:.1%}", **m))
        A(f"| {t:.1%} | {b['w'].mean() * 100:.1f}% | {_pct(m['cagr'])} | {m['sharpe']:.2f} | "
          f"{_pct(m['mdd'])} | {b['turn']:.1f} |")
    CFG["vol"]["target_annual"] = tgt0
    A("")

    A("**成本敏感性（月末口径）**")
    A("")
    A("| 单边成本 | 年化 | 最大回撤 | 夏普 |")
    A("|---|---:|---:|---:|")
    for f in CFG["fee_side_grid"]:
        b, _ = _run_w(df, engine.apply_hysteresis(
            engine.build_weights(engine.build_signal(navs[long_c], idx), idx)), fee=f)
        b = b[b.index >= pd.Timestamp(start)]
        m = engine.metrics(b)
        rows.append(dict(name=f"成本{f}", **m))
        A(f"| {f * 1e4:.0f}bp | {_pct(m['cagr'])} | {_pct(m['mdd'])} | {m['sharpe']:.2f} |")
    A("")

    w = bt["w"]
    guard = bt["sig"]["guard"]
    A("**敞口分布（月末口径）**")
    A("")
    A(f"- 平均长端占比 **{w.mean() * 100:.1f}%**（v0 固定 70%），中位数 {w.median() * 100:.1f}%，"
      f"最低 {w.min() * 100:.1f}%，最高 {w.max() * 100:.1f}%")
    A(f"- 熔断（σ>{CFG['vol']['guard_mult']:.0f}×{CFG['vol']['target_annual']:.2%}）天数 "
      f"**{int(guard.sum())} / {len(guard)}**（{guard.mean() * 100:.1f}%）")
    A(f"- 区间 σ 分位：25% {bt['sig']['sigma'].quantile(0.25):.2%} / 中位 "
      f"{bt['sig']['sigma'].median():.2%} / 75% {bt['sig']['sigma'].quantile(0.75):.2%}")
    A("")

    yv = net.groupby(net.index.year).apply(lambda x: (1 + x).prod() - 1)
    y0 = fixed_net["**基准 v0 固定中性 35/65**"].groupby(
        fixed_net["**基准 v0 固定中性 35/65**"].index.year).apply(lambda x: (1 + x).prod() - 1)
    A("**逐年（V5 vs 基准 v0 固定中性 35/65）**")
    A("")
    A("| 年 | V5 | v0 35/65 | 差 | 平均长端 |")
    A("|---|---:|---:|---:|---:|")
    for y in sorted(set(yv.index) | set(y0.index)):
        a, b = float(yv.get(y, 0)) * 100, float(y0.get(y, 0)) * 100
        wy = w[w.index.year == y]
        A(f"| {y} | {_pct(a)} | {_pct(b)} | {_pct(a - b)} | "
          f"{(wy.mean() * 100 if len(wy) else float('nan')):.1f}% |")
    A("")

    m_2w = engine.metrics(bt_2w["ret"])
    m_inv = engine.metrics(inv_net)
    m_v0 = engine.metrics(fixed_net["**基准 v0 固定中性 35/65**"])
    m_v0o = engine.metrics(fixed_net["v0 旧配比 70/30（2026-11-20 前）"])
    A("## 结论与边界")
    A("")
    A(f"- V5（月末）年化 **{m_v5['cagr']:+.2f}%** / 回撤 **{m_v5['mdd']:+.2f}%** / 夏普 "
      f"{m_v5['sharpe']:.2f}；双周口径 {m_2w['cagr']:+.2f}% / {m_2w['mdd']:+.2f}% / "
      f"{m_2w['sharpe']:.2f}。")
    A(f"- 基准：v0 固定中性 35/65 {m_v0['cagr']:+.2f}% / {m_v0['mdd']:+.2f}% / {m_v0['sharpe']:.2f}；"
      f"旧配比 70/30 {m_v0o['cagr']:+.2f}% / {m_v0o['mdd']:+.2f}% / {m_v0o['sharpe']:.2f}。")
    A(f"- **机制对照**：反向口径（高波动加久期）{m_inv['cagr']:+.2f}% / {m_inv['mdd']:+.2f}% / "
      f"{m_inv['sharpe']:.2f}。若反向与正向差距很小，说明这段样本的胜负主要由「平均敞口高低」"
      "决定，而不是「按波动缩放」这个机制本身。")
    A("- 边界：样本 2021-12~2026-11 只有 5 年，且长端腿 003376 与底仓 011983 的波动量级不同；"
      "目标波动 1.5% 取自 003376 的长期中枢，**在样本内标定**，不构成严格样本外。"
      "波动率只能预测风险、不能预测收益：债牛里波动抬升的那几段往往也是上涨段，V5 必然少赚。")
    A("- 未做：杠杆（cap 固定 70%）、跨资产波动目标、真正的 VaR 约束（这里用 σ 阈值近似）。")
    A("")

    os.makedirs(OUT_DIR, exist_ok=True)
    with open(OUT_MD, "w", encoding="utf-8") as f:
        f.write("\n".join(lines) + "\n")
    with open(OUT_JSONL, "w", encoding="utf-8") as f:
        for r in rows:
            f.write(json.dumps(r, ensure_ascii=False) + "\n")
    print(f"[ok] {OUT_MD}")
    print(f"[ok] {OUT_JSONL}")
    print(f"  V5 {m_v5['cagr']:+.2f}% / {m_v5['mdd']:+.2f}% / {m_v5['sharpe']:.2f}  "
          f"avg_w={w.mean() * 100:.1f}%  turn={bt['turn']:.1f}  (v0 {m_v0['cagr']:+.2f}%)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
