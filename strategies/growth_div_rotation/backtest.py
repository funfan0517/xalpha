# -*- coding: utf-8 -*-
"""成长/红利风格轮动 · 官方回测（二元：满仓成长 / 红利）

数据: 创业板指(399006) ÷ 中证红利(000922) 风格比值 R（默认；--alt 切科创50 000688）
引擎: 无前视，次日开盘切换；缓冲带 ±BUFFER；分位风控 250 日；月频上限防摩擦
基准: 买入持有成长 / 买入持有红利 / 50-50 恒定
产物: backtest/_bt_report.md · backtest/_gd_bt.json · backtest/_gd_nav.png
      backtest/_gd_bt.jsonl（单行摘要，供 pipeline/flow_select 读取）
用法: python strategies/growth_div_rotation/backtest.py [--alt] [--refresh]
"""
import io
import json
import os
import sys
from datetime import datetime

import numpy as np
import pandas as pd

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8")

_DIR = os.path.dirname(os.path.abspath(__file__))
_ROOT = os.path.dirname(os.path.dirname(_DIR))
for _p in (_DIR, _ROOT):
    if _p not in sys.path:
        sys.path.insert(0, _p)

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
plt.rcParams["font.sans-serif"] = ["Microsoft YaHei", "SimHei", "DejaVu Sans"]
plt.rcParams["axes.unicode_minus"] = False

import rule  # noqa: E402
import data as datamod  # noqa: E402
import factors  # noqa: E402
import engine  # noqa: E402
from pipeline import bt_stats  # noqa: E402


def pct(x):
    return f"{x * 100:+.2f}%"


def metrics(eq):
    s = eq / eq.iloc[0]
    ret = s.iloc[-1] - 1.0
    y = max((s.index[-1] - s.index[0]).days / 365.0, 1e-9)
    ann = (1 + ret) ** (1 / y) - 1 if ret > -1 else -1.0
    dd = (s / s.cummax() - 1).min()
    r = eq.pct_change().dropna()
    vol = r.std() * np.sqrt(252)
    shp = (r.mean() / r.std() * np.sqrt(252)) if r.std() > 0 else 0.0
    return dict(ann=ann, dd=dd, vol=vol, shp=shp, ret=ret, years=y)


def compute(alt=False, refresh=False):
    if refresh or not os.path.exists(datamod.CACHE):
        datamod.refresh()
    gcode = rule.growth_code(alt)
    dcode = rule.DIV_INDEX
    wide = datamod.load_wide(codes=[gcode, dcode])
    if wide.shape[1] < 2 or len(wide) < rule.QUANTILE_WINDOW + 30:
        raise RuntimeError("数据不足：请先 python strategies/growth_div_rotation/data.py --refresh")
    gclose, dclose = wide[gcode], wide[dcode]
    sig = factors.compute_signals(gclose, dclose)

    res = engine.run_strategy(sig, gclose, dclose)
    eq, pos = res["eq"], res["pos"]

    # 基准
    gret = gclose.pct_change().fillna(0.0)
    dret = dclose.pct_change().fillna(0.0)
    bh_g = (1 + gret).cumprod()
    bh_d = (1 + dret).cumprod()
    ff = (1 + (gret + dret) / 2).cumprod()

    # 评估起点：指标成形后（去掉无信号预热段）
    warm = sig.dropna(subset=["upper", "q"]).index[0]
    m = metrics(eq[eq.index >= warm])
    mb_g = metrics(bh_g[bh_g.index >= warm])
    mb_d = metrics(bh_d[bh_d.index >= warm])
    mb_ff = metrics(ff[ff.index >= warm])

    valid = sig.dropna(subset=["upper", "q"])
    recent = []
    for d in valid.index[-12:]:
        row = valid.loc[d]
        recent.append(dict(
            date=str(d.date()), R=round(float(row["R"]), 4),
            ma20=(None if pd.isna(row["ma20"]) else round(float(row["ma20"]), 4)),
            upper=(None if pd.isna(row["upper"]) else round(float(row["upper"]), 4)),
            lower=(None if pd.isna(row["lower"]) else round(float(row["lower"]), 4)),
            ma30=(None if pd.isna(row["ma30"]) else round(float(row["ma30"]), 4)),
            q=(None if pd.isna(row["q"]) else round(float(row["q"]), 4)),
            pos=int(pos.loc[d]),
        ))

    out = dict(
        generated_at=datetime.now().strftime("%Y-%m-%d %H:%M"),
        alt=alt, growth=rule.growth_label(alt), dividend=rule.DIV_NAME,
        params=res["params"],
        window=f"{sig.index.min().date()}~{sig.index.max().date()}",
        n_bars=len(sig),
        eval_start=str(warm.date()),
        metrics=dict(strategy=m, bh_growth=mb_g, bh_dividend=mb_d, fifty_fifty=mb_ff),
        total_switches=res["total_switches"],
        month_count={f"{k[0]}-{k[1]:02d}": v for k, v in res["month_count"].items()},
        switches=res["switches"],
        trades=res["trades"],
        recent_sig=recent,
        eq={str(d.date()): round(float(v), 6) for d, v in (eq / eq.iloc[0]).items()},
        pos=list(map(int, pos.tolist())),
        dates=[str(d.date()) for d in sig.index],
    )
    return out, res, (eq, bh_g, bh_d, ff, sig, warm)


def build_report(out, res):
    m, mg, md, mf = (out["metrics"]["strategy"], out["metrics"]["bh_growth"],
                     out["metrics"]["bh_dividend"], out["metrics"]["fifty_fifty"])
    L = ["# 成长/红利风格轮动 · 官方回测",
         f"> 成长={out['growth']} ÷ 红利={out['dividend']} 风格比值 R · "
         f"缓冲带 ±{out['params']['buffer']:.1%} · 分位窗口 {rule.QUANTILE_WINDOW} 日 · "
         f"入场 Q<{out['params']['enter_q']:.2f} / 离场 Q>{out['params']['exit_q']:.2f} · "
         f"月频上限 {out['params']['max_trades_month']} 次 · 单边费 {out['params']['fee']:.2%}",
         f"> 数据 {out['window']}（{out['n_bars']} 交易日）· 评估起点（指标成形）{out['eval_start']} · "
         "信号用指数收盘、收益用指数收益（价格口径，未含分红再投与跟踪误差）；"
         "R 绝对值量级随数据源指数点位而变，但缓冲带/分位均无量纲，信号与缩放无关。", ""]

    L += ["## 一、绩效对比（评估起点起）", "",
          "| 组合 | 年化 | 最大回撤 | 波动 | 夏普 | 总收益 |",
          "|---|---|---|---|---|---|",
          f"| **策略(风格轮动)** | {pct(m['ann'])} | {pct(m['dd'])} | {pct(m['vol'])} | {m['shp']:.2f} | {pct(m['ret'])} |",
          f"| 买入持有成长({out['growth']}) | {pct(mg['ann'])} | {pct(mg['dd'])} | {pct(mg['vol'])} | {mg['shp']:.2f} | {pct(mg['ret'])} |",
          f"| 买入持有红利({out['dividend']}) | {pct(md['ann'])} | {pct(md['dd'])} | {pct(md['vol'])} | {md['shp']:.2f} | {pct(md['ret'])} |",
          f"| 50-50 恒定 | {pct(mf['ann'])} | {pct(mf['dd'])} | {pct(mf['vol'])} | {mf['shp']:.2f} | {pct(mf['ret'])} |",
          ""]
    excess_d = m["ann"] - md["ann"]
    excess_g = m["ann"] - mg["ann"]
    L.append(f"> 策略相对「买入持有红利」超额 **{pct(excess_d)}**/年；"
             f"相对「买入持有成长」超额 **{pct(excess_g)}**/年。"
             "风格轮动目标是用更低回撤换取介于两者之间的稳健收益，而非赌单边。")

    # 单段统计
    ts = bt_stats.trade_stats(res["trades"])
    L += bt_stats.section_rows(
        [("策略(逐段持仓 leg)", ts)],
        title="逐段持仓统计（全期）",
        note="每段=一次连续持有某侧(成长/红利)直到切换；胜率=盈利段/总段；"
             "未含切换费外的滑点；价格口径。数据为 xueqiu/指数历史，模拟结果，非投资建议。")

    # 切换概览
    L += ["", "## 二、切换概览（全期）", "",
          f"- 总切换次数：**{out['total_switches']}** 次（评估起点起）",
          f"- 月频上限：{out['params']['max_trades_month']} 次/月（超则暂停一次，防摩擦）",
          "- 最近 8 次切换：", ""]
    if out["switches"]:
        L += ["| 切换日(次日开盘执行) | 由 | 切到 | R_{-1} | 上轨 | 下轨 | Q_{-1} |",
              "|---|---|---|---|---|---|---|"]
        name = {0: "红利/现金", 1: "成长"}
        for s in out["switches"][-8:]:
            L.append(f"| {s['date']} | {name[s['from_side']]} | **{name[s['to_side']]}** | "
                     f"{s['R']:.4f} | {s['upper']:.4f} | {s['lower']:.4f} | {s['q']:.2%} |")
    else:
        L.append("- 无切换（全程持有红利/现金）")

    # 风格比值近期
    L += ["", "## 三、风格比值 R 近期轨迹（最近 12 个有效交易日）", "",
          "| 日期 | R | MA20 | 上轨 | 下轨 | MA30 | Q(250日) | 仓位 |",
          "|---|---|---|---|---|---|---|---|"]
    name = {0: "红利/现金", 1: "成长"}
    for r in out.get("recent_sig", []):
        L.append(f"| {r['date']} | {r['R']:.4f} | {r['ma20']} | {r['upper']} | "
                 f"{r['lower']} | {r['ma30']} | {r['q']} | {name[r['pos']]} |")
    L.append("")
    L.append("> 机械规则输出，仅供研究参考，不构成投资建议；市场有风险，投资需谨慎。")
    return "\n".join(L) + "\n", ts


def _write_products(out, res, plot_data):
    eq, bh_g, bh_d, ff, sig, warm = plot_data
    os.makedirs(os.path.join(_DIR, "backtest"), exist_ok=True)
    # 报告
    txt, ts = build_report(out, res)
    rep = os.path.join(_DIR, "backtest", "_bt_report.md")
    open(rep, "w", encoding="utf-8").write(txt)
    print(txt)
    # 详细 json
    json.dump(out, open(os.path.join(_DIR, "backtest", "_gd_bt.json"), "w", encoding="utf-8"),
              ensure_ascii=False, indent=2)
    # 图
    fig, ax = plt.subplots(figsize=(12, 5))
    ax.plot(eq.index, (eq / eq.iloc[0]).values, label="风格轮动", lw=1.1)
    ax.plot(bh_g.index, (bh_g / bh_g.iloc[0]).values, label=f"持有{out['growth']}", alpha=0.7)
    ax.plot(bh_d.index, (bh_d / bh_d.iloc[0]).values, label=f"持有{out['dividend']}", alpha=0.7)
    ax.plot(ff.index, (ff / ff.iloc[0]).values, label="50-50", alpha=0.6, ls="--")
    ax.axvline(warm, color="gray", alpha=0.4, lw=0.8)
    ax.legend()
    ax.set_title("成长/红利风格轮动 vs 基准 (净值)")
    ax.grid(alpha=0.3)
    fig.tight_layout()
    fig.savefig(os.path.join(_DIR, "backtest", "_gd_nav.png"), dpi=140, facecolor="white")
    print("\n图: backtest/_gd_nav.png")
    # 单行摘要（供 pipeline/flow_select）
    m = out["metrics"]["strategy"]
    md = out["metrics"]["bh_dividend"]
    summary = dict(
        ok=True, code="GD_ROT", theme="成长/红利风格轮动",
        off="—", years=round(m["years"], 1),
        base_ann=md["ann"], base_mdd=md["dd"],
        st_ann=m["ann"], st_mdd=m["dd"],
        params=out["params"], alt=out["alt"],
    )
    raw = os.path.join(_DIR, "backtest", "_gd_bt.jsonl")
    with open(raw, "w", encoding="utf-8") as f:
        f.write(json.dumps(summary, ensure_ascii=False) + "\n")
    print(f"摘要: {raw}")
    return rep


def main():
    alt = "--alt" in sys.argv
    refresh = "--refresh" in sys.argv
    out, res, plot_data = compute(alt=alt, refresh=refresh)
    _write_products(out, res, plot_data)


if __name__ == "__main__":
    main()
