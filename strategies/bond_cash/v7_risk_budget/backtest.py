# -*- coding: utf-8 -*-
"""V7 · 门派风险预算回测 → backtest/_v7_bt.{md,jsonl}。

口径：**债腿 30pp 内部**（利率 / 信用 / 转债三类，权重和=1）；现金腿 10pp 固定在外，不参与回测。
对照分三组，回答三个问题：
  ① 约束值不值：V7（ERC + 约束）vs ERC 无约束 vs 逆波动 vs 等权；
  ② 门派有没有超额：全利率（=v0 口径）vs 全信用 vs 全转债 vs 人工静态倾斜 70/20/10；
  ③ 参数是否脆弱：协方差窗 60 / 120 / 252 日 × 单边成本 0 / 10 / 40bp。
另输出各类平均权重、换手次数与**风险贡献分解**（ERC 的意义在第 3 张表，不在收益表）。

用法: python strategies/bond_cash/v7_risk_budget/backtest.py [--force]
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
BLOCKS = rule.BLOCKS
OUT_DIR = os.path.join(_HERE, "backtest")
OUT_MD = os.path.join(OUT_DIR, "_v7_bt.md")
OUT_JSONL = os.path.join(OUT_DIR, "_v7_bt.jsonl")


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
    need = [CFG["long_leg"], CFG["base_leg"], CFG["credit_leg"], CFG["conv_leg"]]
    miss = [c for c in need if c not in navs]
    if miss:
        print(f"[error] 缺腿净值 {miss}")
        return 1

    rets = engine.block_returns(navs)
    start = rets.index[min(CFG["warmup"], len(rets) - 1)]

    bt = engine.backtest(navs, start=start)
    net = bt["ret"]
    m_v7 = engine.metrics(net)
    bt_free = engine.backtest(navs, start=start, constrained=False)

    # 逆波动（同约束）作为机制对照
    cov = engine.cov_est(rets)
    iv = 1.0 / pd.Series([cov[i, i] ** 0.5 for i in range(len(BLOCKS))])
    bt_iv_w = engine.project(iv.values)

    fixed = {
        "等权 1/3": {"rate": 1 / 3, "credit": 1 / 3, "conv": 1 / 3},
        "全利率（=v0 口径 35/65 哑铃）": {"rate": 1.0},
        "全信用（000032）": {"credit": 1.0},
        "全转债（470058）": {"conv": 1.0},
        "人工静态倾斜 70/20/10": {"rate": 0.70, "credit": 0.20, "conv": 0.10},
    }
    fixed_net = {k: engine.backtest(navs, start=start, weights=w, fee_side=0.0)["ret"]
                 for k, w in fixed.items()}

    scenarios = [
        ("**V7 ERC + 约束**", net, bt["turn"]),
        ("V7 ERC 无约束（只看解本身）", bt_free["ret"], bt_free["turn"]),
        ("逆波动 + 同约束（机制对照）", engine.backtest(
            navs, start=start, weights=bt_iv_w)["ret"], None),
        ("等权 1/3", fixed_net["等权 1/3"], None),
        ("**全利率（=v0 口径 35/65 哑铃）**", fixed_net["全利率（=v0 口径 35/65 哑铃）"], None),
        ("全信用（000032）", fixed_net["全信用（000032）"], None),
        ("全转债（470058）", fixed_net["全转债（470058）"], None),
        ("人工静态倾斜 70/20/10", fixed_net["人工静态倾斜 70/20/10"], None),
    ]

    lines, rows = [], []
    A = lines.append
    A("# V7 · 门派风险预算（ERC）· 回测")
    A("")
    A(f"> 生成 {datetime.now():%Y-%m-%d %H:%M} · 由 `strategies/bond_cash/v7_risk_budget/backtest.py` "
      "产出，**请勿手改**。")
    A(f"> 口径：**债腿 30pp 内部**（利率 / 信用 / 转债 三类，权重和 = 1）；现金腿 10pp 固定在外、不进回测。"
      f"月末解 ERC（协方差窗 {CFG['erc']['window']} 日、收缩 δ={CFG['erc']['shrink']:.2f}）/ T+1 承担 / "
      f"单边 {CFG['fee_side'] * 1e4:.0f}bp / rf {CFG['rf_annual']:.2%}。")
    A(f"> 约束：转债 ≤ {CFG['caps']['conv'] * 100:.1f}%（=总资产 5pp）、信用 ≤ "
      f"{CFG['caps']['credit'] * 100:.0f}%、利率 ≥ {CFG['floors']['rate'] * 100:.0f}%。"
      f"窗口 {net.index[0].date()} ~ {net.index[-1].date()}（{len(net)} 交易日）；"
      f"净值截止 {max(asof.values())}。")
    A("")
    A("| 方案 | 年化 | 波动 | 夏普 | 最大回撤 | 最差单年 | 换手 |")
    A("|---|---:|---:|---:|---:|---:|---:|")
    for nm, r, turn in scenarios:
        m = engine.metrics(r)
        rows.append(dict(name=nm.strip("*"), **m))
        A(_row(nm, m, f"Σ\\|Δw\\|={turn:.1f}" if turn is not None else "—"))
    A("")

    w = bt["w"]
    A("**权重与风险贡献（V7，按债腿 30pp 折算前）**")
    A("")
    A("| 门派 | 平均权重 | 最低 | 最高 | 平均风险贡献 | 平衡值 |")
    A("|---|---:|---:|---:|---:|---:|")
    rc_all = []
    for d in w.index[::5]:
        row = w.loc[d].values
        rc, _ = engine.risk_contrib(row, engine.cov_est(
            rets.loc[:d].iloc[-int(CFG["erc"]["window"]):]))
        rc_all.append(rc)
    rc_mean = pd.DataFrame(rc_all).mean().values if rc_all else [float("nan")] * len(BLOCKS)
    for i, b in enumerate(BLOCKS):
        A(f"| {rule.BLOCK_NAME[b]} | {w[b].mean() * 100:.1f}% | {w[b].min() * 100:.1f}% | "
          f"{w[b].max() * 100:.1f}% | {rc_mean[i] * 100:.1f}% | {100 / len(BLOCKS):.1f}% |")
    A("")
    A(f"- 调仓次数 **{bt['switches']}**（{bt['switches'] / max(engine.metrics(net)['years'], 1e-9):.1f} 次/年），"
      f"Σ|Δw| = {bt['turn']:.1f}")
    A("")

    A("**协方差窗敏感性（ERC + 约束）**")
    A("")
    A("| 窗口 | 年化 | 夏普 | 最大回撤 | 平均转债权重 | Σ\\|Δw\\| |")
    A("|---|---:|---:|---:|---:|---:|")
    w0 = CFG["erc"]["window"]
    for win in (60, 120, 252):
        CFG["erc"]["window"] = win
        b = engine.backtest(navs, start=start)
        m = engine.metrics(b["ret"])
        rows.append(dict(name=f"窗{win}", **m))
        A(f"| {win} 日 | {_pct(m['cagr'])} | {m['sharpe']:.2f} | {_pct(m['mdd'])} | "
          f"{b['w']['conv'].mean() * 100:.1f}% | {b['turn']:.1f} |")
    CFG["erc"]["window"] = w0
    A("")

    A("**成本敏感性（V7）**")
    A("")
    A("| 单边成本 | 年化 | 最大回撤 | 夏普 |")
    A("|---|---:|---:|---:|")
    for f in CFG["fee_side_grid"]:
        b = engine.backtest(navs, start=start, fee_side=f)
        m = engine.metrics(b["ret"])
        rows.append(dict(name=f"成本{f}", **m))
        A(f"| {f * 1e4:.0f}bp | {_pct(m['cagr'])} | {_pct(m['mdd'])} | {m['sharpe']:.2f} |")
    A("")

    yv = net.groupby(net.index.year).apply(lambda x: (1 + x).prod() - 1)
    y0s = fixed_net["全利率（=v0 口径 35/65 哑铃）"]
    y0 = y0s.groupby(y0s.index.year).apply(lambda x: (1 + x).prod() - 1)
    A("**逐年（V7 vs 全利率 = v0 口径 35/65）**")
    A("")
    A("| 年 | V7 | 全利率 | 差 | 平均转债权重 |")
    A("|---|---:|---:|---:|---:|")
    for y in sorted(set(yv.index) | set(y0.index)):
        a, b_ = float(yv.get(y, 0)) * 100, float(y0.get(y, 0)) * 100
        wc = w[w.index.year == y]["conv"].mean() * 100 if (w.index.year == y).any() else float("nan")
        A(f"| {y} | {_pct(a)} | {_pct(b_)} | {_pct(a - b_)} | {wc:.1f}% |")
    A("")

    m_free = engine.metrics(bt_free["ret"])
    m_iv = engine.metrics(engine.backtest(navs, start=start, weights=bt_iv_w)["ret"])
    m_eq = engine.metrics(fixed_net["等权 1/3"])
    m_v0 = engine.metrics(y0s)
    m_cr = engine.metrics(fixed_net["全信用（000032）"])
    m_cv = engine.metrics(fixed_net["全转债（470058）"])
    m_tilt = engine.metrics(fixed_net["人工静态倾斜 70/20/10"])
    A("## 结论与边界")
    A("")
    A(f"- V7（ERC+约束）年化 **{m_v7['cagr']:+.2f}%** / 回撤 **{m_v7['mdd']:+.2f}%** / 夏普 "
      f"{m_v7['sharpe']:.2f}；无约束 ERC {m_free['cagr']:+.2f}% / {m_free['mdd']:+.2f}% / "
      f"{m_free['sharpe']:.2f}；逆波动 {m_iv['cagr']:+.2f}% / {m_iv['mdd']:+.2f}% / {m_iv['sharpe']:.2f}。")
    A(f"- 门派基准：全利率 {m_v0['cagr']:+.2f}% / {m_v0['mdd']:+.2f}% / {m_v0['sharpe']:.2f}；"
      f"全信用 {m_cr['cagr']:+.2f}% / {m_cr['mdd']:+.2f}% / {m_cr['sharpe']:.2f}；"
      f"全转债 {m_cv['cagr']:+.2f}% / {m_cv['mdd']:+.2f}% / {m_cv['sharpe']:.2f}；"
      f"等权 {m_eq['cagr']:+.2f}% / {m_eq['mdd']:+.2f}% / {m_eq['sharpe']:.2f}；"
      f"人工 70/20/10 倾斜 {m_tilt['cagr']:+.2f}% / {m_tilt['mdd']:+.2f}% / {m_tilt['sharpe']:.2f}。")
    A("- ERC 的价值在**风险贡献表**：平均权重与平均风险贡献的差就是「谁在偷风险」。"
      "如果某一类权重只有 10% 却贡献 40% 的风险，那就是配比失衡的客观证据 —— 这比收益表有用。")
    A("- 边界：回测只覆盖**债腿内部**，现金 10pp 在外；转债只有 1 只主动型壳（470058），"
      "它的收益含少量股票 beta，不等于转债指数；样本 2021-12 起（受 011983 成立限制），"
      "窗 60 日的协方差估计在样本内选定，不构成严格样本外。")
    A("- 不追求跑赢全利率：4 类资产的期望收益差只有 1~3pp/年，而估计误差更大；"
      "V7 的定位是「配比体检 + 失衡告警」，不是收益增强。")
    A("")

    os.makedirs(OUT_DIR, exist_ok=True)
    with open(OUT_MD, "w", encoding="utf-8") as f:
        f.write("\n".join(lines) + "\n")
    with open(OUT_JSONL, "w", encoding="utf-8") as f:
        for r in rows:
            f.write(json.dumps(r, ensure_ascii=False) + "\n")
    print(f"[ok] {OUT_MD}")
    print(f"[ok] {OUT_JSONL}")
    print(f"  V7 {m_v7['cagr']:+.2f}% / {m_v7['mdd']:+.2f}% / {m_v7['sharpe']:.2f}  "
          f"turn={bt['turn']:.1f}  conv_w={w['conv'].mean() * 100:.1f}%  "
          f"(全利率 {m_v0['cagr']:+.2f}%)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
