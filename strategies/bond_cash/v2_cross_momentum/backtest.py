# -*- coding: utf-8 -*-
"""V2 · 双动量轮动回测：追最强(top1/top2) vs 中位(Median) vs v0 固定哑铃。

产物 ``backtest/_v2_bt.{md,jsonl}``。
口径：月度最后交易日排名 → 下个交易日承担；成本 Σ|Δw| × 单边 10bp。

用法: python strategies/bond_cash/v2_cross_momentum/backtest.py [--force]
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
OUT_MD = os.path.join(OUT_DIR, "_v2_bt.md")
OUT_JSONL = os.path.join(OUT_DIR, "_v2_bt.jsonl")


def _pct(v, sign=True):
    return "—" if v is None else (f"{v:+.2f}%" if sign else f"{v:.2f}%")


def _mrow(name, m, extra=""):
    return (f"| {name} | {_pct(m['cagr'])} | {_pct(m['vol'], False)} | {m['sharpe']:.2f} | "
            f"{_pct(m['mdd'])} | {_pct(m['worst_year'])} | {extra} |")


def _const_weights(df, weights):
    return pd.DataFrame([weights] * len(df), index=df.index, columns=df.columns)


def _run(df, target, fee, start):
    w = target.shift(1).fillna(0.0)
    # 仓位自检：任何一天权重和都不能 >1，否则就是持仓矩阵叠加成了杠杆
    leak = float(w.sum(axis=1).max())
    if leak > 1.001:
        raise ValueError(f"权重和 {leak:.3f} > 1，持仓矩阵有叠加 bug")
    rets = df.pct_change()
    ret = (w * rets).sum(axis=1, min_count=1).dropna()
    turn = w.diff().abs().sum(axis=1).reindex(ret.index).fillna(0.0)
    net = ret - turn * fee
    net = net[net.index >= pd.Timestamp(start)]
    return net, float(turn.reindex(net.index).sum())


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--force", action="store_true")
    args = ap.parse_args(argv)

    navs, asof, failed = data.load_all(force=args.force)
    if "003376" not in navs or CFG["defensive"] not in navs:
        print("[error] 缺核心净值")
        return 1
    df = engine.nav_frame(navs)
    # 回测起点：核心四只（不含 2023 才成立的 511090）都有完整 126 日动量
    core = ["003376", "011983", "002864", "000032"]
    start = df[core].dropna().index[CFG["lookback"]]
    start = max(pd.Timestamp(start), pd.Timestamp("2019-06-01"))

    bt = engine.backtest(df, start=start)
    net_v2 = bt["ret"]
    m_v2 = engine.metrics(net_v2)

    # top1 / Median（对照组）
    mom = engine.momentum_table(df)
    reb = rule.is_rebalance(df.index)
    reb_dates = df.index[reb & mom.notna().any(axis=1)]
    def _rot_weights(n_pick):
        # NaN 初始化（不是 0）——否则 ffill 带不动持仓，回测退化成月度单日持仓
        t = pd.DataFrame(np.nan, index=df.index, columns=df.columns)
        for d in reb_dates:
            row = mom.loc[d].dropna().sort_values(ascending=False)
            if row.empty:
                continue
            # 每次调仓先把整行清零再赋入选者：只写入选列会让没被写到的列沿用
            # 上一次的权重（ffill），几个 1.0 叠起来等于偷偷加杠杆（实测 top1 被抬到 +25%/年）。
            t.loc[d, :] = 0.0
            if n_pick == 1 and float(row.iloc[0]) <= CFG["abs_floor"]:
                t.loc[d, CFG["defensive"]] = 1.0
                continue
            picks = list(row.index[:n_pick])
            for c in picks:
                t.loc[d, c] = 1.0 / len(picks)
        return t.ffill().fillna(0.0)
    w_top1, w_med = _rot_weights(1), _rot_weights(4)
    r_top1, tr_top1 = _run(df, w_top1, CFG["fee_side"], start)
    r_med, tr_med = _run(df, w_med, CFG["fee_side"], start)
    m_top1, m_med = engine.metrics(r_top1), engine.metrics(r_med)

    # 基准 v0（2026-11-20 起固定中性 35/65）/ 旧配比 / 单只买入持有
    w_v0 = _const_weights(df, {"003376": 0.35, "011983": 0.65})
    r_v0, _ = _run(df, w_v0, 0.0, start)
    m_v0 = engine.metrics(r_v0)
    w_v0o = _const_weights(df, {"003376": 0.70, "011983": 0.30})
    r_v0o, _ = _run(df, w_v0o, 0.0, start)
    m_v0o = engine.metrics(r_v0o)
    w_hold = _const_weights(df, {"003376": 1.0})
    r_hold, _ = _run(df, w_hold, 0.0, start)
    m_hold = engine.metrics(r_hold)
    w_def = _const_weights(df, {CFG["defensive"]: 1.0})
    r_def, _ = _run(df, w_def, 0.0, start)
    m_def = engine.metrics(r_def)

    lines, rows = [], []
    A = lines.append
    A("# V2 · 横截面双动量轮动 · 回测")
    A("")
    A(f"> 生成 {datetime.now():%Y-%m-%d %H:%M} · 由 `strategies/bond_cash/v2_cross_momentum/backtest.py` "
      "产出，**请勿手改**。")
    A(f"> 口径：场外累计净值 · 月末排名 / T+1 承担 · 成本 Σ|Δw|×单边 {CFG['fee_side'] * 1e4:.0f}bp · "
      f"rf {CFG['rf_annual']:.2%} · 净值截止 {max(asof.values())}。")
    A(f"> 候选 {len(CFG['universe'])} 只（511090 自 2023-05 起才参与）；"
      f"动量 {CFG['lookback']} 日、持前 {CFG['top_n']} 名、绝对过滤 ≤{CFG['abs_floor'] * 100:.0f}%。")
    A("")
    A(f"窗口 {net_v2.index[0].date()} ~ {net_v2.index[-1].date()}（{len(net_v2)} 个交易日）")
    A("")
    A("| 方案 | 年化 | 波动 | 夏普 | 最大回撤 | 最差单年 | 备注 |")
    A("|---|---:|---:|---:|---:|---:|---|")
    items = [
        (f"**V2 双动量 top{CFG['top_n']}**", m_v2, f"{bt['switches']} 次调仓 / Σ|Δw| {bt['turn']:.1f}"),
        ("双动量 top1", m_top1, f"Σ|Δw| {tr_top1:.1f}"),
        ("中位组 Median(第4名)", m_med, "MDPI 2026 的赢家策略（本市场对照）"),
        ("**基准 v0 固定中性 35/65**", m_v0, "零换手 · 2026-11-20 起"),
        ("v0 旧配比 70/30（2026-11-20 前）", m_v0o, "零换手"),
        ("满仓 003376", m_hold, "零换手"),
        ("满仓 002864 短债", m_def, "零换手"),
    ]
    for nm, m, ex in items:
        rows.append(dict(name=nm.strip("*"), **m))
        A(_mrow(nm, m, ex))
    A("")

    A("**成本敏感性（V2）**")
    A("")
    A("| 单边成本 | 年化 | 最大回撤 | 夏普 |")
    A("|---|---:|---:|---:|")
    for f in (0.0, 0.001, 0.004):
        r, _ = _run(df, engine.build_weights(df)[0], f, start)
        m = engine.metrics(r)
        rows.append(dict(name=f"成本{f}", **m))
        A(f"| {f * 1e4:.0f}bp | {_pct(m['cagr'])} | {_pct(m['mdd'])} | {m['sharpe']:.2f} |")
    A("")

    yv = net_v2.groupby(net_v2.index.year).apply(lambda x: (1 + x).prod() - 1)
    y0 = r_v0.groupby(r_v0.index.year).apply(lambda x: (1 + x).prod() - 1)
    A("**逐年（V2 vs v0）**")
    A("")
    A("| 年 | V2 | v0 | 差 |")
    A("|---|---:|---:|---:|")
    for y in sorted(set(yv.index) | set(y0.index)):
        a, b = float(yv.get(y, 0)) * 100, float(y0.get(y, 0)) * 100
        A(f"| {y} | {_pct(a)} | {_pct(b)} | {_pct(a - b)} |")
    A("")

    A("## 结论与边界")
    A("")
    A(f"- V2 年化 **{m_v2['cagr']:+.2f}%** / 回撤 **{m_v2['mdd']:+.2f}%** / 夏普 {m_v2['sharpe']:.2f}；"
      f"v0 固定哑铃 {m_v0['cagr']:+.2f}% / {m_v0['mdd']:+.2f}% / {m_v0['sharpe']:.2f}。")
    A(f"- 追最强的 top1 年化 {m_top1['cagr']:+.2f}%、MDPI 的 Median（第 4 名）"
      f"{m_med['cagr']:+.2f}%。若 Median 明显胜出，说明债基横截面动量的「赢家诅咒」在境内也成立"
      "（涨多的债基往往已经把利差压缩完），此时应该把「追最强」改成「持中间」。")
    A("- 制度化差异：MDPI 用半年度调仓，V2 用月度（更贴近本家族其它变种的节奏）；"
      "调仓频率本身会改变结论，别把两者数字直接相减。")
    A("- 绝对动量降落伞是双动量的关键：债熊里所有候选都负时切短债，"
      "避免「最不坏的就是最好」变成满仓亏损。")
    A("- 511090（30 年国债 ETF）2023-05 才成立，早期样本它缺席；"
      "它的高弹性会显著影响 2023 年后的排名结果，不能把全-window 结论外推到更久以前。")
    A("- 候选含信用债（000032/006884），与利率债的利差在赎回潮里会同时走阔；"
      "双动量没有信用利差风控，靠短债降落伞兜底。")
    A("")

    os.makedirs(OUT_DIR, exist_ok=True)
    with open(OUT_MD, "w", encoding="utf-8") as f:
        f.write("\n".join(lines) + "\n")
    with open(OUT_JSONL, "w", encoding="utf-8") as f:
        for r in rows:
            f.write(json.dumps(r, ensure_ascii=False) + "\n")
    print(f"[ok] {OUT_MD}")
    print(f"[ok] {OUT_JSONL}")
    print(f"  V2  {m_v2['cagr']:+.2f}% / {m_v2['mdd']:+.2f}% / {m_v2['sharpe']:.2f}")
    print(f"  v0  {m_v0['cagr']:+.2f}% / {m_v0['mdd']:+.2f}% / {m_v0['sharpe']:.2f}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
