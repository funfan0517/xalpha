# -*- coding: utf-8 -*-
"""V1 · 四因子久期档 回测（对照 v0 固定哑铃）。

产物
  ``backtest/_v1_bt.jsonl``  逐档指标
  ``backtest/_v1_bt.md``     人读报告

窗口
  长窗   2018-08 ~ 今，底仓 ``001512``（011983 2021-09 才有数据；信用因子缺失 → 覆盖 3 项）
  现行窗 2023-07 ~ 今，底仓 ``011983``（信用因子的 3 年分位需要 ~250 个观测）

口径
  收益 = 场外累计净值日收益；T 日算分、T+1 起承担权重（``shift(1)``）；
  换手成本 = |Δw_long| × 2 × 单边（默认 10bp）；夏普无风险利率 1.40%。

用法
  python strategies/bond_cash/v1_four_factor/backtest.py [--force]
"""
import argparse
import io
import json
import os
import sys
from datetime import datetime

import pandas as pd

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8")

_HERE = os.path.dirname(os.path.abspath(__file__))          # v1_four_factor
_PKG = os.path.dirname(_HERE)
_ROOT = os.path.dirname(os.path.dirname(_PKG))
for _p in (_ROOT, _PKG, _HERE):
    while _p in sys.path:
        sys.path.remove(_p)
    sys.path.insert(0, _p)

import rule      # noqa: E402
import data      # noqa: E402
import engine    # noqa: E402

CFG = rule.CFG
OUT_DIR = os.path.join(_HERE, "backtest")
OUT_MD = os.path.join(OUT_DIR, "_v1_bt.md")
OUT_JSONL = os.path.join(OUT_DIR, "_v1_bt.jsonl")


def _pct(v, nd=2, sign=True):
    if v is None:
        return "—"
    return f"{v:+.{nd}f}%" if sign else f"{v:.{nd}f}%"


def _row(name, m, w):
    return (f"| {name} | {_pct(m['cagr'])} | {_pct(m['vol'], 2, False)} | {m['sharpe']:.2f} | "
            f"{_pct(m['mdd'])} | {_pct(m['worst_year'])} | {w:.2f} |")


def _const(idx, v):
    return pd.Series(float(v), index=idx, dtype="float64")


def _scenario(rets, w, fee):
    net, turns = engine.run(w, rets, fee_side=fee)
    m = engine.metrics(net)
    m.update(turns=turns, avg_w=round(float(w.reindex(rets.index).ffill().mean()), 3),
             start=str(net.index[0].date()), end=str(net.index[-1].date()), fee_side=fee)
    return m, net


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--force", action="store_true")
    args = ap.parse_args(argv)

    p = data.panel(force=args.force)
    navs, failed = p["navs"], p["failed"]
    for c in (CFG["bench"], CFG["long_leg"], CFG["base_leg"], "001512"):
        if c not in navs and c != "001512":
            print(f"[error] 缺净值 {c}；failed={failed}")
            return 1
    if "001512" not in navs:
        navs["001512"] = data.load_nav("001512")

    bench = navs[CFG["bench"]]
    temps = engine.build_temps(navs, p["y10"], p["shibor"], p["curve"])
    factors = engine.build_factors(temps, bench)
    sig = engine.build_signal(factors)

    asof = str(bench.index[-1].date())
    lines, rows = [], []
    A = lines.append
    A("# V1 · 四因子久期档 · 回测")
    A("")
    A(f"> 生成 {datetime.now():%Y-%m-%d %H:%M} · 由 `strategies/bond_cash/v1_four_factor/backtest.py` "
      "产出，**请勿手改**。")
    A(f"> 口径：场外累计净值 · T 日算分 / T+1 承担 · 单边 {CFG['fee_side'] * 1e4:.0f}bp · "
      f"夏普 rf {CFG['rf_annual']:.2%} · 净值截止 {asof}。")
    A(f"> 数据：10Y 国债 {str(p['y10'].index[-1].date()) if len(p['y10']) else '缺'} · "
      f"SHIBOR {str(p['shibor'].index[-1].date()) if len(p['shibor']) else '缺'} · "
      f"曲线 {str(p['curve'].index[-1].date()) if len(p['curve']) else '缺'} · "
      f"基准 {CFG['bench']} {str(bench.index[0].date())} 起 {len(bench)} 个交易日")
    A("")

    windows = [
        ("一、长窗（底仓 001512）", "001512", "2018-08-01"),
        ("二、现行口径窗（底仓 011983，信用因子可用）", CFG["base_leg"], "2023-07-01"),
    ]
    for title, base_code, start in windows:
        if base_code not in navs:
            print(f"[warn] 缺 {base_code}，跳过 {title}")
            continue
        rets = engine.leg_returns(navs, CFG["long_leg"], base_code)
        idx = rets.index[rets.index >= pd.Timestamp(start)]
        idx = idx[idx >= bench.index[0] + pd.Timedelta(days=CFG["mom_window"])]
        if len(idx) < 250:
            print(f"[warn] {title} 样本不足（{len(idx)} 行）")
            continue
        w_v1 = engine.backtest_weights(sig, idx)
        A(f"## {title}")
        A("")
        A(f"窗口 {idx[0].date()} ~ {idx[-1].date()}（{len(idx)} 个交易日）")
        A("")
        A("| 方案 | 年化 | 波动 | 夏普 | 最大回撤 | 最差单年 | 均长端占比 |")
        A("|---|---:|---:|---:|---:|---:|---:|")
        m_v1, r_v1 = _scenario(rets, w_v1, CFG["fee_side"])
        m_v0, r_v0 = _scenario(rets, _const(idx, CFG["long_cap"]), CFG["fee_side"])
        m_neu, _ = _scenario(rets, _const(idx, CFG["long_base"]), CFG["fee_side"])
        m_def, _ = _scenario(rets, _const(idx, 0.0), CFG["fee_side"])
        m_all, _ = _scenario(rets, _const(idx, 1.0), CFG["fee_side"])
        for nm, m in ((f"**V1 四因子（滞后带 {CFG['hysteresis_pp']:.2f}pp）**", m_v1),
                      (f"v0 固定哑铃 {CFG['long_cap']:.0%}/{1 - CFG['long_cap']:.0%}", m_v0),
                      (f"固定中性 {CFG['long_base']:.0%}/{1 - CFG['long_base']:.0%}", m_neu),
                      ("全防守 100% 3-5 年", m_def),
                      ("全进攻 100% 7-10 年", m_all)):
            rows.append(dict(window=title[:6], name=nm.strip("*"), **m))
            A(_row(nm, m, m["avg_w"]))
        A("")
        A(f"> V1 换手 **{m_v1['turns']} 次**（{m_v1['years']} 年 → 约 "
          f"{m_v1['turns'] / max(m_v1['years'], 0.1):.1f} 次/年），"
          f"成本已按单边 {CFG['fee_side'] * 1e4:.0f}bp 计入。")
        A("")

        # 成本敏感性
        A("**成本敏感性（V1）**")
        A("")
        A("| 单边成本 | 年化 | 最大回撤 | 夏普 |")
        A("|---|---:|---:|---:|")
        for f in (0.0, 0.0010, 0.0040):
            m, _ = _scenario(rets, w_v1, f)
            rows.append(dict(window=title[:6] + "-成本", fee=f, **m))
            A(f"| {f * 1e4:.0f}bp | {_pct(m['cagr'])} | {_pct(m['mdd'])} | {m['sharpe']:.2f} |")
        A("")

        # 分档分布与逐年
        sub = sig.reindex(idx)
        A("**信号分布**：" + "、".join(
            f"{k} {int((sub['side'] == k).sum())} 天" for k in ("偏多", "震荡观望", "偏空")) +
          f"；置信度 高 {int((sub['conf'] == 'high').sum())} / 中 {int((sub['conf'] == 'medium').sum())}"
          f" / 低 {int((sub['conf'] == 'low').sum())}；覆盖 "
          f"{temps['coverage'].reindex(idx).mean():.1f}/4 项")
        A("")
        yv = r_v1.groupby(r_v1.index.year).apply(lambda x: (1 + x).prod() - 1)
        y0 = r_v0.groupby(r_v0.index.year).apply(lambda x: (1 + x).prod() - 1)
        A("**逐年（V1 vs v0 固定哑铃）**")
        A("")
        A("| 年 | V1 | v0 | 差 |")
        A("|---|---:|---:|---:|")
        for y in sorted(set(yv.index) | set(y0.index)):
            a, b = float(yv.get(y, 0)) * 100, float(y0.get(y, 0)) * 100
            A(f"| {y} | {_pct(a)} | {_pct(b)} | {_pct(a - b)} |")
        A("")
        rows.append(dict(window=title[:6], name="metrics_v1", **m_v1))
        rows.append(dict(window=title[:6], name="metrics_v0", **m_v0))

    A("## 结论与边界")
    A("")
    A("- **V1 的定位是「在 30pp 债腿内部调久期」**，不改 40pp 额度、不碰现金腿；"
      "因此它只能在「7-10 年 vs 3-5 年」之间做选择，收益差的上限就是两者的利差。")
    A("- 四因子里**趋势因子与估值因子天然打架**（趋势偏多往往估值偏贵），"
      "互斥规则（R-08）会把相当多的日子压成「震荡观望」→ 目标回到 35% 中性，"
      "这是 V1 相比 v0 的主要差异来源，也是它换手偏低的原因。")
    A("- 信用因子只有 2023 年以后才有（akshare 曲线长度限制），"
      "长窗里覆盖度 3/4、权重归一后仍可用，但不可与现行窗的 4/4 直接比。")
    A("- 情景概率（R-12~R-15）只用于**提示**，不进入仓位公式 —— 文档自己承认"
      "估值分位对债券短期方向的预测力弱（R-03/R-15）。")
    A("- 样本内标定：温度权重 40/20/25/15、因子阈值 20/80、30/70 全部来自文档；"
      "唯一的换算 = R-18 的滞后带 1.5pp 按带宽比例 35/10 放缩为 5.25pp（否则天天翻仓）；"
      "但文档参数本身是在 2013-2026 上定的，不构成严格样本外。")
    A("")

    os.makedirs(OUT_DIR, exist_ok=True)
    with open(OUT_MD, "w", encoding="utf-8") as f:
        f.write("\n".join(lines) + "\n")
    with open(OUT_JSONL, "w", encoding="utf-8") as f:
        for r in rows:
            f.write(json.dumps(r, ensure_ascii=False) + "\n")

    print(f"[ok] {OUT_MD}")
    print(f"[ok] {OUT_JSONL}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
