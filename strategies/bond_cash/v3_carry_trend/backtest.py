# -*- coding: utf-8 -*-
"""V3 · Carry + 趋势投票回测 → backtest/_v3_bt.{md,jsonl}。

对照：v0 固定哑铃、纯 carry 票、纯 trend 票、全防守、全进攻。
口径：月末信号 / T+1 承担 / 单边 10bp / rf 1.40%。

用法: python strategies/bond_cash/v3_carry_trend/backtest.py [--force]
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
OUT_MD = os.path.join(OUT_DIR, "_v3_bt.md")
OUT_JSONL = os.path.join(OUT_DIR, "_v3_bt.jsonl")


def _pct(v, sign=True):
    return "—" if v is None else (f"{v:+.2f}%" if sign else f"{v:.2f}%")


def _row(nm, m, ex=""):
    return (f"| {nm} | {_pct(m['cagr'])} | {_pct(m['vol'], False)} | {m['sharpe']:.2f} | "
            f"{_pct(m['mdd'])} | {_pct(m['worst_year'])} | {ex} |")


def _run(navs, curve, start, w_override=None, field=None, fee=None):
    long_c, base_c = CFG["long_leg"], CFG["base_leg"]
    df = pd.concat({long_c: navs[long_c], base_c: navs[base_c]},
                   axis=1, join="inner").dropna()
    sig = engine.build_signal(curve, df.index)
    if field:
        # 单票对照：carry/trend 各 -1/0/+1 直接映射成 0/35/70
        w = sig[field].map(lambda x: {-1: 0.0, 0: 0.35, 1: 0.70}.get(int(x), 0.35)).astype(float)
        w = engine.apply_hysteresis(w)
    elif w_override is None:
        w = engine.apply_hysteresis(engine.build_weights(sig, df.index))
    else:
        w = pd.Series(float(w_override), index=df.index)
    w = w.shift(1).fillna(0.35).reindex(df.index)
    rets = df.pct_change().dropna()
    gross = w.reindex(rets.index) * rets[long_c] + (1 - w.reindex(rets.index)) * rets[base_c]
    turn = w.reindex(rets.index).diff().abs().fillna(0.0)
    fee = CFG["fee_side"] if fee is None else fee
    net = (gross - turn * 2 * fee)
    net = net[net.index >= pd.Timestamp(start)]
    return net, float(turn.reindex(net.index).sum())


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--force", action="store_true")
    args = ap.parse_args(argv)
    navs, asof, failed = data.load_all(force=args.force)
    curve = data.load_curve()
    if curve.empty:
        print("[error] 缺曲线缓存")
        return 1
    long_c, base_c = CFG["long_leg"], CFG["base_leg"]
    if long_c not in navs or base_c not in navs:
        print("[error] 缺腿净值")
        return 1

    # 起点：曲线 cgb_1y/cgb_10y 均有 250 个分位样本之后
    spread = curve[CFG["carry"]["long"]] - curve[CFG["carry"]["short"]]
    ready = engine.rolling_pct(spread).dropna()
    start = ready.index[0]
    bt, _ = engine.backtest(navs, curve, start=start)
    net = bt["ret"]
    m_v3 = engine.metrics(net)

    scenarios = [
        ("**V3 Carry+Trend（双票）**", _run(navs, curve, start)[0], bt["turn"], bt["switches"]),
        ("纯 Carry 票", _run(navs, curve, start, field="carry")[0], None, None),
        ("纯 Trend 票", _run(navs, curve, start, field="trend")[0], None, None),
        ("v0 固定哑铃 70/30", _run(navs, curve, start, w_override=0.70, fee=0)[0], None, None),
        ("固定中性 35/65", _run(navs, curve, start, w_override=0.35, fee=0)[0], None, None),
        ("全防守 100% 3-5 年", _run(navs, curve, start, w_override=0.0, fee=0)[0], None, None),
        ("全进攻 100% 7-10 年", _run(navs, curve, start, w_override=1.0, fee=0)[0], None, None),
    ]

    lines, rows = [], []
    A = lines.append
    A("# V3 · Carry + 趋势投票 · 回测")
    A("")
    A(f"> 生成 {datetime.now():%Y-%m-%d %H:%M} · 由 `strategies/bond_cash/v3_carry_trend/backtest.py` "
      "产出，**请勿手改**。")
    A(f"> 口径：中债曲线（非基金净值）· 月末信号 / T+1 承担 · 单边 {CFG['fee_side'] * 1e4:.0f}bp · "
      f"rf {CFG['rf_annual']:.2%} · 曲线截止 {curve.index[-1].date()} · 净值截止 {max(asof.values())}。")
    A(f"> 窗口 {net.index[0].date()} ~ {net.index[-1].date()}（{len(net)} 交易日）；"
      "双票映射 0 / 17.5 / 35 / 52.5 / 70%，滞后带 1.5pp。")
    A("")
    A("| 方案 | 年化 | 波动 | 夏普 | 最大回撤 | 最差单年 | 换手 |")
    A("|---|---:|---:|---:|---:|---:|---:|")
    for nm, r, turn, sw in scenarios:
        m = engine.metrics(r)
        rows.append(dict(name=nm.strip("*"), **m))
        ex = f"Σ\|Δw\|={turn:.1f}" if turn is not None else "—"
        A(_row(nm, m, ex))
    A("")

    A("**成本敏感性（V3）**")
    A("")
    A("| 单边成本 | 年化 | 最大回撤 | 夏普 |")
    A("|---|---:|---:|---:|")
    for f in (0.0, 0.001, 0.004):
        r, _ = _run(navs, curve, start, fee=f)
        m = engine.metrics(r)
        rows.append(dict(name=f"成本{f}", **m))
        A(f"| {f * 1e4:.0f}bp | {_pct(m['cagr'])} | {_pct(m['mdd'])} | {m['sharpe']:.2f} |")
    A("")

    yv = net.groupby(net.index.year).apply(lambda x: (1 + x).prod() - 1)
    y0 = scenarios[3][1].groupby(scenarios[3][1].index.year).apply(lambda x: (1 + x).prod() - 1)
    A("**逐年（V3 vs v0 固定哑铃）**")
    A("")
    A("| 年 | V3 | v0 | 差 |")
    A("|---|---:|---:|---:|")
    for y in sorted(set(yv.index) | set(y0.index)):
        a, b = float(yv.get(y, 0)) * 100, float(y0.get(y, 0)) * 100
        A(f"| {y} | {_pct(a)} | {_pct(b)} | {_pct(a - b)} |")
    A("")

    m_v0 = engine.metrics(scenarios[3][1])
    m_neu = engine.metrics(scenarios[4][1])
    A("## 结论与边界")
    A("")
    A(f"- V3 年化 **{m_v3['cagr']:+.2f}%** / 回撤 **{m_v3['mdd']:+.2f}%** / "
      f"夏普 {m_v3['sharpe']:.2f}；v0 固定哑铃 {m_v0['cagr']:+.2f}% / {m_v0['mdd']:+.2f}% / "
      f"{m_v0['sharpe']:.2f}；固定中性 {m_neu['cagr']:+.2f}% / {m_neu['mdd']:+.2f}% / "
      f"{m_neu['sharpe']:.2f}。")
    A("- 信号源是中债**收益率曲线**而非基金净值（v1 已知的噪声来源），"
      "两票都月度采样 → 天然低换手；这是 V3 相对 v1 的主要设计动机。")
    A("- Carry 票在曲线平坦/倒挂期给 −1，Trend 票在利率下行期给 +1，"
      "两票经常抵消 → score=0 → 35% 中性，所以 V3 的默认状态就是「比 v0 低一半久期」。")
    A("- 边界：只覆盖 7-10 年 vs 3-5 年两档，不能配置 30 年超长；"
      "曲线缓存从 2022-06 起（akshare 限制），分位窗 3 年意味着回测起点在 2025 年附近，"
      "样本较短；做空、ECFC 高频预期、LPPLS 泡沫过滤原文有但本仓库数据不支持，均未实现。")
    A("")

    os.makedirs(OUT_DIR, exist_ok=True)
    with open(OUT_MD, "w", encoding="utf-8") as f:
        f.write("\n".join(lines) + "\n")
    with open(OUT_JSONL, "w", encoding="utf-8") as f:
        for r in rows:
            f.write(json.dumps(r, ensure_ascii=False) + "\n")
    print(f"[ok] {OUT_MD}")
    print(f"[ok] {OUT_JSONL}")
    print(f"  V3 {m_v3['cagr']:+.2f}% / {m_v3['mdd']:+.2f}% / {m_v3['sharpe']:.2f}  "
          f"turn={bt['turn']:.1f}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
