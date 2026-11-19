# -*- coding: utf-8 -*-
"""V4 · 股债对冲三票回测 → backtest/_v4_bt.{md,jsonl}。

对照：V3 双票（同窗口、同 carry/trend 口径）、纯 Hedge 票、v0 固定哑铃、固定中性、
全防守、全进攻；并做 Hedge 分位阈值的敏感性网格（80/20、90/10、95/5）。
口径：月末信号 / T+1 承担 / 单边 10bp / rf 1.40%。

用法: python strategies/bond_cash/v4_stock_hedge/backtest.py [--force]
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
OUT_MD = os.path.join(OUT_DIR, "_v4_bt.md")
OUT_JSONL = os.path.join(OUT_DIR, "_v4_bt.jsonl")


def _pct(v, sign=True):
    return "—" if v is None else (f"{v:+.2f}%" if sign else f"{v:.2f}%")


def _row(nm, m, ex=""):
    return (f"| {nm} | {_pct(m['cagr'])} | {_pct(m['vol'], False)} | {m['sharpe']:.2f} | "
            f"{_pct(m['mdd'])} | {_pct(m['worst_year'])} | {ex} |")


def _net(navs, curve, stock, start, w=None, use_hedge=True, fee=None, grid=None):
    """-> (净收益 Series, Σ|Δw|)。w 给定时为固定权重基准。"""
    long_c, base_c = CFG["long_leg"], CFG["base_leg"]
    df = pd.concat({long_c: navs[long_c], base_c: navs[base_c]}, axis=1, join="inner").dropna()
    idx = df.index
    if w is not None:
        wt = pd.Series(float(w), index=idx)
    elif grid is not None:
        sig = engine.build_signal(curve, stock, idx)
        wt = sig["hedge"].map(lambda x: {-1: 0.0, 0: 0.35, 1: 0.70}.get(int(x), 0.35)).astype(float)
        wt = engine.apply_hysteresis(wt)
    else:
        sig = engine.build_signal(curve, stock, idx, use_hedge=use_hedge)
        wt = engine.apply_hysteresis(engine.build_weights(sig, idx))
    wt = wt.shift(1).fillna(CFG["long_by_score"][0]).reindex(idx)
    rets = df.pct_change().dropna()
    gross = wt.reindex(rets.index) * rets[long_c] + (1 - wt.reindex(rets.index)) * rets[base_c]
    turn = wt.reindex(rets.index).diff().abs().fillna(0.0)
    fee = CFG["fee_side"] if fee is None else fee
    net = gross - turn * 2 * fee
    net = net[net.index >= pd.Timestamp(start)]
    return net, float(turn.reindex(net.index).sum())


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--force", action="store_true")
    args = ap.parse_args(argv)
    navs, asof, failed = data.load_all(force=args.force)
    curve = data.load_curve()
    stock = data.load_stock()
    if curve.empty:
        print("[error] 缺曲线缓存")
        return 1
    long_c, base_c = CFG["long_leg"], CFG["base_leg"]
    if long_c not in navs or base_c not in navs:
        print("[error] 缺腿净值")
        return 1

    # 起点：carry 分位（3 年，250 样本）与 hedge 分位（5 年，250 样本）都就绪之后
    spread = curve[CFG["carry"]["long"]] - curve[CFG["carry"]["short"]]
    ready_c = engine.rolling_pct(spread, CFG["carry"]["years"], CFG["carry"]["min_obs"]).dropna()
    start = ready_c.index[0]

    bt, sig = engine.backtest(navs, curve, stock, start=start)
    net = bt["ret"]
    m_v4 = engine.metrics(net)

    net_v3, turn_v3 = _net(navs, curve, stock, start, use_hedge=False)
    net_hedge, turn_hedge = _net(navs, curve, stock, start, grid=True)
    scenarios = [
        ("**V4 三票（Carry+Trend+Hedge）**", net, bt["turn"]),
        ("V3 口径双票（无 Hedge）", net_v3, turn_v3),
        ("纯 Hedge 票", net_hedge, turn_hedge),
        ("v0 固定哑铃 70/30", _net(navs, curve, stock, start, w=0.70, fee=0)[0], None),
        ("固定中性 35/65", _net(navs, curve, stock, start, w=0.35, fee=0)[0], None),
        ("全防守 100% 3-5 年", _net(navs, curve, stock, start, w=0.0, fee=0)[0], None),
        ("全进攻 100% 7-10 年", _net(navs, curve, stock, start, w=1.0, fee=0)[0], None),
    ]

    lines, rows = [], []
    A = lines.append
    A("# V4 · 股债对冲三票 · 回测")
    A("")
    A(f"> 生成 {datetime.now():%Y-%m-%d %H:%M} · 由 `strategies/bond_cash/v4_stock_hedge/backtest.py` "
      "产出，**请勿手改**。")
    A(f"> 口径：中债曲线 + 沪深300 指数 · 月末信号 / T+1 承担 · 单边 {CFG['fee_side'] * 1e4:.0f}bp · "
      f"rf {CFG['rf_annual']:.2%} · 曲线截止 {curve.index[-1].date()} · 净值截止 {max(asof.values())}。")
    A(f"> 窗口 {net.index[0].date()} ~ {net.index[-1].date()}（{len(net)} 交易日）；"
      "三票映射 0/12/23/35/47/58/70%，滞后带 1.5pp。")
    A("")
    A("| 方案 | 年化 | 波动 | 夏普 | 最大回撤 | 最差单年 | 换手 |")
    A("|---|---:|---:|---:|---:|---:|---:|")
    for nm, r, turn in scenarios:
        m = engine.metrics(r)
        rows.append(dict(name=nm.strip("*"), **m))
        A(_row(nm, m, f"Σ\\|Δw\\|={turn:.1f}" if turn is not None else "—"))
    A("")

    A("**Hedge 分位阈值敏感性（三票，只改 Hedge 的 hi/lo）**")
    A("")
    A("| 阈值 | 年化 | 夏普 | 最大回撤 | Σ\\|Δw\\| |")
    A("|---|---:|---:|---:|---:|")
    base_hi, base_lo = CFG["hedge"]["hi"], CFG["hedge"]["lo"]
    for hi, lo in ((80.0, 20.0), (90.0, 10.0), (95.0, 5.0)):
        CFG["hedge"]["hi"], CFG["hedge"]["lo"] = hi, lo
        r, t = _net(navs, curve, stock, start)
        m = engine.metrics(r)
        rows.append(dict(name=f"Hedge 阈值 {hi:.0f}/{lo:.0f}", **m))
        A(f"| ≥{hi:.0f}% / ≤{lo:.0f}% | {_pct(m['cagr'])} | {m['sharpe']:.2f} | "
          f"{_pct(m['mdd'])} | {t:.1f} |")
    CFG["hedge"]["hi"], CFG["hedge"]["lo"] = base_hi, base_lo
    A("")

    A("**成本敏感性（V4）**")
    A("")
    A("| 单边成本 | 年化 | 最大回撤 | 夏普 |")
    A("|---|---:|---:|---:|")
    for f in CFG["fee_side_grid"]:
        r, _ = _net(navs, curve, stock, start, fee=f)
        m = engine.metrics(r)
        rows.append(dict(name=f"成本{f}", **m))
        A(f"| {f * 1e4:.0f}bp | {_pct(m['cagr'])} | {_pct(m['mdd'])} | {m['sharpe']:.2f} |")
    A("")

    A("**三票状态分布（调仓日采样）**")
    A("")
    A("| 票 | −1 | 0 | +1 |")
    A("|---|---:|---:|---:|")
    for c in ("carry", "trend", "hedge"):
        v = sig[c].value_counts()
        A(f"| {c} | {int(v.get(-1, 0))} | {int(v.get(0, 0))} | {int(v.get(1, 0))} |")
    A("")

    yv = net.groupby(net.index.year).apply(lambda x: (1 + x).prod() - 1)
    y0 = scenarios[3][1].groupby(scenarios[3][1].index.year).apply(lambda x: (1 + x).prod() - 1)
    A("**逐年（V4 vs v0 固定哑铃）**")
    A("")
    A("| 年 | V4 | v0 | 差 |")
    A("|---|---:|---:|---:|")
    for y in sorted(set(yv.index) | set(y0.index)):
        a, b = float(yv.get(y, 0)) * 100, float(y0.get(y, 0)) * 100
        A(f"| {y} | {_pct(a)} | {_pct(b)} | {_pct(a - b)} |")
    A("")

    m_v3 = engine.metrics(net_v3)
    m_h = engine.metrics(net_hedge)
    m_v0 = engine.metrics(scenarios[3][1])
    m_neu = engine.metrics(scenarios[4][1])
    A("## 结论与边界")
    A("")
    A(f"- V4 三票 年化 **{m_v4['cagr']:+.2f}%** / 回撤 **{m_v4['mdd']:+.2f}%** / 夏普 "
      f"{m_v4['sharpe']:.2f}；V3 口径双票 {m_v3['cagr']:+.2f}% / {m_v3['mdd']:+.2f}% / "
      f"{m_v3['sharpe']:.2f}；纯 Hedge 票 {m_h['cagr']:+.2f}% / {m_h['mdd']:+.2f}% / "
      f"{m_h['sharpe']:.2f}。")
    A(f"- 基准：v0 固定哑铃 {m_v0['cagr']:+.2f}% / {m_v0['mdd']:+.2f}% / {m_v0['sharpe']:.2f}；"
      f"固定中性 35/65 {m_neu['cagr']:+.2f}% / {m_neu['mdd']:+.2f}% / {m_neu['sharpe']:.2f}。")
    A("- Hedge 票的独立贡献 = 「V4 三票」减「V3 口径双票」：这两行 carry/trend 逐行同源，"
      "差值只来自股票动量这一条腿。")
    A("- 边界：沪深300 用的是**指数点位**（不含分红），只做动量分位、不做收益归因；"
      "曲线缓存自 2022-06 起 + carry 需 3 年分位 → 样本仅 3.5 年且几乎全债牛，"
      "股票极端动量出现的次数很少（见状态分布），统计意义有限，不能外推。")
    A("- 与 v3 相同：不做空、不配置 30 年超长、不含 ECFC/社融等宏观数据。")
    A("")

    os.makedirs(OUT_DIR, exist_ok=True)
    with open(OUT_MD, "w", encoding="utf-8") as f:
        f.write("\n".join(lines) + "\n")
    with open(OUT_JSONL, "w", encoding="utf-8") as f:
        for r in rows:
            f.write(json.dumps(r, ensure_ascii=False) + "\n")
    print(f"[ok] {OUT_MD}")
    print(f"[ok] {OUT_JSONL}")
    print(f"  V4 {m_v4['cagr']:+.2f}% / {m_v4['mdd']:+.2f}% / {m_v4['sharpe']:.2f}  "
          f"turn={bt['turn']:.1f}   (V3 口径 {m_v3['cagr']:+.2f}%)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
