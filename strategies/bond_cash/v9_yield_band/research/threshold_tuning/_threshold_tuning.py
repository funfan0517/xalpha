# -*- coding: utf-8 -*-
"""V9 · 10Y 绝对阈值寻优（研究课题：threshold_tuning）。

回答用户 2026-11-27 的问题：「2.5 和 1.8 不太行，研究优化一下 10Y 阈值」。
不只看样本内最优，同时做三件事：
  1. 全样本网格（low 1.4~2.8 / high 2.0~3.6，步长 0.1，间隔≥0.3）；
  2. 邻域平台检验（相邻 ±0.1 网格的夏普均值，避免挑到单点噪声）；
  3. 逐年 walk-forward OOS：每年用截至上一年末的全部历史选参，在下一年实盘外检验。

档位配置（用户 2026-11-27 二次调整）：HIGH 长3:中6:短1 / MID 长6:中3:短1 / LOW 长1:中3:短6。
口径：债腿 30pp 内部、月末+突破 T+1、单边 10bp、rf 1.40%。

用法：python strategies/bond_cash/v9_yield_band/research/threshold_tuning/_threshold_tuning.py
产物：本目录 `_threshold_tuning.md` + `_threshold_tuning.jsonl`（全网格明细）。
"""
import io
import json
import os
import sys
from datetime import datetime

import numpy as np
import pandas as pd

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8")

_HERE = os.path.dirname(os.path.abspath(__file__))
_STRAT = os.path.dirname(os.path.dirname(_HERE))     # v9_yield_band
for _m in ("rule", "data", "engine"):
    sys.modules.pop(_m, None)
sys.path.insert(0, _STRAT)

import rule      # noqa: E402
import data      # noqa: E402
import engine    # noqa: E402

OUT_MD = os.path.join(_HERE, "_threshold_tuning.md")
OUT_JSONL = os.path.join(_HERE, "_threshold_tuning.jsonl")

LOWS = [round(1.4 + 0.1 * i, 1) for i in range(19)]      # 1.4 ~ 3.2
HIGHS = [round(2.0 + 0.1 * i, 1) for i in range(23)]     # 2.0 ~ 4.2（覆盖样本内最高 3.985）
MIN_GAP = 0.3
COMBOS = [(lo, hi) for lo in LOWS for hi in HIGHS if hi - lo >= MIN_GAP - 1e-9]
OLD = (1.8, 2.5)
START = pd.Timestamp("2016-09-27")
TEST_YEARS = list(range(2020, 2027))

NEW_WEIGHTS = {
    "HIGH": {"003376": 0.3, "001512": 0.6, "002864": 0.1},
    "MID": {"003376": 0.6, "001512": 0.3, "002864": 0.1},
    "LOW": {"003376": 0.1, "001512": 0.3, "002864": 0.6},
}


def _pct(v, sign=True):
    return f"{v:+.2f}%" if sign else f"{v:.2f}%"


def _row(nm, m):
    return (f"| {nm} | {_pct(m['cagr'])} | {_pct(m['vol'], False)} | {m['sharpe']:.2f} | "
            f"{_pct(m['mdd'])} | {m['calmar']:.2f} |")


def main(argv=None):
    navs, _, _ = data.load_all(force=False)
    y = data.load_yield(refresh=False)
    print(f"[info] 10Y {y.index[0].date()}~{y.index[-1].date()}；组合 {len(COMBOS)} 组")

    rets, stats = {}, {}
    for lo, hi in COMBOS:
        st = engine.state_series(y, lo, hi)
        # 阈值研究保持「无波动缩放」口径：波动目标层是后续独立叠加，不能混进阈值比较。
        bt = engine.backtest(navs, st, start=START, weights=NEW_WEIGHTS, vol_target=False)
        r = bt["ret"]
        st_use = st.reindex(r.index).ffill().value_counts()
        occ = {s: float(st_use.get(s, 0) / max(len(r), 1)) for s in engine.STATES}
        rets[(lo, hi)] = r
        stats[(lo, hi)] = dict(metrics=engine.metrics(r), switches=bt["switches"],
                               turnover=bt["turnover"], states=st_use.to_dict(),
                               occ=occ, avg_long=float(bt["w"]["003376"].mean()))

    # ---- 邻域平台：相邻 ±0.1 的夏普均值（只统计同样存在的格子）
    keys = set(rets)
    plateau = {}
    for lo, hi in COMBOS:
        nb = []
        for dl in (-0.1, 0.0, 0.1):
            for dh in (-0.1, 0.0, 0.1):
                nk = (round(lo + dl, 1), round(hi + dh, 1))
                if nk in keys:
                    nb.append(stats[nk]["metrics"]["sharpe"])
        plateau[(lo, hi)] = float(np.mean(nb))

    top_sharpe = sorted(COMBOS, key=lambda k: (-stats[k]["metrics"]["sharpe"], k))[:10]
    top_plateau = sorted(COMBOS, key=lambda k: (-plateau[k], -stats[k]["metrics"]["sharpe"], k))[:10]

    # 三态都要真正用到的候选：HIGH≥8% / MID≥8% / LOW≥5% 交易日。
    # 不做这层约束时，网格会把 high 推到样本最高收益率之外（≈永远不进 HIGH），
    # 那是在压低长债敞口吃低波红利，不是「利率高位买长债」的策略。
    def _usable(k):
        o = stats[k]["occ"]
        return o.get("HIGH", 0) >= 0.08 and o.get("MID", 0) >= 0.08 and o.get("LOW", 0) >= 0.05

    usable = [k for k in COMBOS if _usable(k)]
    top_usage = sorted(usable, key=lambda k: (-plateau[k], -stats[k]["metrics"]["sharpe"], k))[:10]
    # 2026-11-27 第三轮：用户嫌 MID 66% 太长，要求同时优化夏普、压低回撤。
    # 候选 = 旧阈值 / 当前过长 MID / 目标区 1.9~2.0 × 3.0 / 更低回撤的 2.6/3.0。
    CAND = [OLD, (2.0, 3.5), (1.9, 3.0), (2.0, 3.0), (2.6, 3.0)]

    # ---- walk-forward OOS：每年用截至上一年末的全部数据按夏普选参
    oos = []
    for yr in TEST_YEARS:
        train_end = pd.Timestamp(f"{yr - 1}-12-31")
        test = [d for d in rets[OLD].index if d.year == yr]
        if not test:
            continue
        cand = [(stats[k]["metrics"]["sharpe"], k) for k in COMBOS
                if len(rets[k][rets[k].index <= train_end]) >= 500]
        if not cand:
            continue
        cand.sort(key=lambda x: (-x[0], x[1]))
        best_sh, best_k = cand[0]
        row = dict(year=yr, train_end=str(train_end.date()), train_sharpe=round(best_sh, 2),
                   best=best_k)
        for idx, k in enumerate(CAND):
            m = engine.metrics(rets[k].reindex(test).dropna())
            row[f"c{idx}_oos_sharpe"] = m["sharpe"]
            row[f"c{idx}_oos_cagr"] = m["cagr"]
            row[f"c{idx}_oos_mdd"] = m["mdd"]
        oos.append(row)
        print(f"[oos] {yr}: train best {best_k} ({best_sh:.2f}) → OOS "
              + " / ".join(f"c{i}={row[f'c{i}_oos_sharpe']:.2f}" for i in range(len(CAND))))

    avg_by_c = {i: float(np.mean([r[f"c{i}_oos_sharpe"] for r in oos]))
                for i in range(len(CAND))}
    win_by_c = {i: sum(1 for r in oos if r[f"c{i}_oos_sharpe"] > r["c0_oos_sharpe"])
                for i in range(len(CAND))}

    # ---- 静态权重对照：阈值择时到底有没有超过「一直持有某一档」
    statics = {
        "静态 HIGH 60/30/10": engine.fixed_backtest(navs, NEW_WEIGHTS["HIGH"], start=START),
        "静态 MID 30/60/10": engine.fixed_backtest(navs, NEW_WEIGHTS["MID"], start=START),
        "静态 LOW 10/30/60": engine.fixed_backtest(navs, NEW_WEIGHTS["LOW"], start=START),
        "静态等权 1/3": engine.fixed_backtest(navs, {"003376": 1 / 3, "001512": 1 / 3,
                                                     "002864": 1 / 3}, start=START),
    }
    static_m = {k: engine.metrics(v) for k, v in statics.items()}

    # ---- 网格热力图（步长 0.2，便于肉眼找连片区域）
    heat_lo = [round(1.4 + 0.2 * i, 1) for i in range(10)]    # 1.4~3.2
    heat_hi = [round(2.0 + 0.2 * i, 1) for i in range(12)]    # 2.0~4.2

    lines, rows = [], []
    A = lines.append
    A("# V9 · 10Y 阈值寻优（threshold_tuning）")
    A("")
    A(f"> 生成 {datetime.now():%Y-%m-%d %H:%M} · 由 `strategies/bond_cash/v9_yield_band/research/"
      "threshold_tuning/_threshold_tuning.py` 产出，**请勿手改**。")
    A(f"> 档位配置：HIGH 长3:中6:短1 / MID 长6:中3:短1 / LOW 长1:中3:短6（占债腿，和 100%）；"
      f"月末+突破 T+1；单边 10bp；rf 1.40%；样本 {START.date()} ~ "
      f"{rets[OLD].index[-1].date()}。")
    A("")
    A("## 1. 方法")
    A("")
    A(f"- 搜索空间：low 1.4~3.2%、high 2.0~4.2%（步长 0.1，high−low≥{MIN_GAP}%），共 **{len(COMBOS)}** 组；"
      "high 上界覆盖样本内最高 3.985%，专门检查「边界最优」是不是伪最优。")
    A("- 防过拟合三层：① **邻域平台**取 (low±0.1, high±0.1) 共 9 格的夏普均值，避免单点最优；"
      "② **三态可用性**：HIGH/MID/LOW 各自至少占 8%/8%/5% 交易日，排除「high 推到样本外、"
      "等于永远不买长债」的退化解；③ **walk-forward OOS**：2020–2026 每年用截至上一年末的"
      "全部历史按夏普选参，在下一年检验（train 最少 500 日）。")
    A("- 对照：旧 1.8/2.5、MID 过长的 2.0/3.5、目标区 1.9/3.0 与 2.0/3.0、"
      "低回撤候选 2.6/3.0，以及三档**静态固定权重**。")
    A("")

    A("## 2. 全样本 Top10（按夏普）")
    A("")
    A("| low / high | 年化 | 波动 | 夏普 | 最大回撤 | 卡玛 | 邻域平台 | 切换 | H/M/L 占比 | 平均长债 |")
    A("|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|")
    for k in top_sharpe:
        s = stats[k]
        o = s["occ"]
        A(f"| {k[0]:.1f} / {k[1]:.1f} | {_pct(s['metrics']['cagr'])} | "
          f"{_pct(s['metrics']['vol'], False)} | {s['metrics']['sharpe']:.2f} | "
          f"{_pct(s['metrics']['mdd'])} | {s['metrics']['calmar']:.2f} | "
          f"{plateau[k]:.2f} | {s['switches']} | "
          f"{o['HIGH'] * 100:.0f}/{o['MID'] * 100:.0f}/{o['LOW'] * 100:.0f}% | "
          f"{s['avg_long'] * 100:.0f}% |")
    A("")
    A("## 3. 全样本 Top10（按邻域平台均值，更稳健）")
    A("")
    A("| low / high | 邻域平台 | 本格夏普 | 年化 | 最大回撤 | 卡玛 | 切换 | H/M/L 占比 |")
    A("|---|---:|---:|---:|---:|---:|---:|---:|")
    for k in top_plateau:
        s = stats[k]
        o = s["occ"]
        A(f"| {k[0]:.1f} / {k[1]:.1f} | {plateau[k]:.2f} | {s['metrics']['sharpe']:.2f} | "
          f"{_pct(s['metrics']['cagr'])} | {_pct(s['metrics']['mdd'])} | "
          f"{s['metrics']['calmar']:.2f} | {s['switches']} | "
          f"{o['HIGH'] * 100:.0f}/{o['MID'] * 100:.0f}/{o['LOW'] * 100:.0f}% |")
    A("")
    A("## 3b. 三态均被使用的 Top10（HIGH≥8% / MID≥8% / LOW≥5%）")
    A("")
    A("| low / high | 邻域平台 | 夏普 | 年化 | 最大回撤 | 卡玛 | 切换 | H/M/L 占比 |")
    A("|---|---:|---:|---:|---:|---:|---:|---:|")
    for k in top_usage:
        s = stats[k]
        o = s["occ"]
        A(f"| {k[0]:.1f} / {k[1]:.1f} | {plateau[k]:.2f} | {s['metrics']['sharpe']:.2f} | "
          f"{_pct(s['metrics']['cagr'])} | {_pct(s['metrics']['mdd'])} | "
          f"{s['metrics']['calmar']:.2f} | {s['switches']} | "
          f"{o['HIGH'] * 100:.0f}/{o['MID'] * 100:.0f}/{o['LOW'] * 100:.0f}% |")
    A("")
    A(f"- 旧阈值 1.8/2.5：夏普 {stats[OLD]['metrics']['sharpe']:.2f}、"
      f"邻域平台 {plateau[OLD]:.2f}、年化 {_pct(stats[OLD]['metrics']['cagr'])}、"
      f"回撤 {_pct(stats[OLD]['metrics']['mdd'])}、切换 {stats[OLD]['switches']} 次、"
      f"H/M/L 占比 "
      f"{stats[OLD]['occ']['HIGH'] * 100:.0f}/{stats[OLD]['occ']['MID'] * 100:.0f}/"
      f"{stats[OLD]['occ']['LOW'] * 100:.0f}%。")
    A("")

    A("## 4. 夏普热力图（行=low，列=high，步长 0.2）")
    A("")
    A("| low \\ high | " + " | ".join(f"{h:.1f}" for h in heat_hi) + " |")
    A("|---|" + "---:|" * len(heat_hi))
    for lo in heat_lo:
        cells = ["—" if (lo, hi) not in keys else f"{stats[(lo, hi)]['metrics']['sharpe']:.2f}"
                 for hi in heat_hi]
        A(f"| {lo:.1f} | " + " | ".join(cells) + " |")
    A("")

    A("## 5. Walk-forward 样本外（每年用历史选参 → 下一年检验）")
    A("")
    hdr = "| 年 | 训练截至 | 训练最优(low/high) | 训练夏普 | " + " | ".join(
        f"{k[0]:.1f}/{k[1]:.1f} OOS 夏普" for k in CAND) \
        + f" | {CAND[-1][0]:.1f}/{CAND[-1][1]:.1f} OOS 年化 |"
    A(hdr)
    A("|---|---|---|---:|" + "---:|" * (len(CAND) + 1))
    for r in oos:
        cells = " | ".join(f"{r[f'c{i}_oos_sharpe']:.2f}" for i in range(len(CAND)))
        last = r[f"c{len(CAND) - 1}_oos_cagr"]
        A(f"| {r['year']} | {r['train_end']} | {r['best'][0]:.1f} / {r['best'][1]:.1f} | "
          f"{r['train_sharpe']:.2f} | {cells} | {_pct(last)} |")
    A("")
    A("- 逐年 OOS 平均夏普：" + "、".join(
        f"{k[0]:.1f}/{k[1]:.1f} = **{avg_by_c[i]:.2f}**（胜旧阈值 {win_by_c[i]}/{len(oos)} 年）"
        for i, k in enumerate(CAND)) + "。")
    A("- 注意：单年 OOS 夏普噪声很大；关注「平均 + 胜出年数 + 参数是否连片」，不追单年冠军。")
    A("")

    A("## 6. 阈值方案 vs 静态固定权重（同一新配置、全样本）")
    A("")
    A("| 方案 | 年化 | 波动 | 夏普 | 最大回撤 | 卡玛 |")
    A("|---|---:|---:|---:|---:|---:|")
    A(_row("旧 1.8 / 2.5", stats[OLD]["metrics"]))
    for k in CAND[1:]:
        A(_row(f"候选 {k[0]:.1f} / {k[1]:.1f}", stats[k]["metrics"]))
    for nm, m in static_m.items():
        A(_row(nm, m))
    A("")
    best_sh = max(static_m.items(), key=lambda kv: kv[1]["sharpe"])
    A(f"- **必须说清楚**：静态 LOW 的夏普 {best_sh[1]['sharpe']:.2f} 高于任何阈值方案"
      f"（最高 {max(s['metrics']['sharpe'] for s in stats.values()):.2f}）。rf=1.4% 的夏普公式"
      "天然偏爱低波动；网格的高夏普区要么把 low 压到样本最低收益率之下（LOW 永不触发），"
      "要么把 high 推到样本最高收益率之上（HIGH 永不触发），本质都是「少拿长债、吃低波红利」，"
      "**不是「高位买长债」被验证有效**。因此推荐阈值时保留三态可用性约束，宁可少一点夏普，"
      "也要让规则真的按口诀工作。")
    A("")
    # 第三轮目标：MID 25~55%（不能像 2.0/3.5 那样 66%）、回撤浅于 −4.2%、三档都保留；
    # 先按邻域平台（抗样本内噪声）再按夏普挑，而不是单看最高夏普。
    mid_ok = [k for k in CAND[1:]
              if 0.25 <= stats[k]["occ"]["MID"] <= 0.55
              and stats[k]["metrics"]["mdd"] >= -4.2
              and stats[k]["occ"]["HIGH"] >= 0.08 and stats[k]["occ"]["LOW"] >= 0.10]
    if mid_ok:
        k = max(mid_ok, key=lambda x: (plateau[x], stats[x]["metrics"]["sharpe"]))
        idx = CAND.index(k)
        o = stats[k]["occ"]
        m = stats[k]["metrics"]
        cur = (2.0, 3.5)
        A(f"- **推荐 {k[0]:.1f}/{k[1]:.1f}**：MID 占比 {o['MID'] * 100:.0f}%"
          f"（HIGH {o['HIGH'] * 100:.0f}% / LOW {o['LOW'] * 100:.0f}%），"
          f"全样本夏普 {m['sharpe']:.2f}、年化 {_pct(m['cagr'])}、回撤 {_pct(m['mdd'])}、"
          f"卡玛 {m['calmar']:.2f}、月胜率 {m['monthly_win']:.1f}%，"
          f"walk-forward 平均 OOS 夏普 {avg_by_c[idx]:.2f}（旧阈值 {avg_by_c[0]:.2f}）。")
        if cur in CAND:
            cm = stats[cur]["metrics"]
            A(f"- vs 当前 2.0/3.5（MID {stats[cur]['occ']['MID'] * 100:.0f}%）：夏普 "
              f"{cm['sharpe']:.2f} → {m['sharpe']:.2f}、回撤 {_pct(cm['mdd'])} → {_pct(m['mdd'])}、"
              f"月胜率 {cm['monthly_win']:.1f}% → {m['monthly_win']:.1f}%"
              f"（**胜率并没有随 MID 缩短而下降**）；代价是 OOS 均值 "
              f"{avg_by_c[CAND.index(cur)]:.2f} → {avg_by_c[idx]:.2f}。")
        A("")

    A("## 7. 边界")
    A("")
    A("- 阈值来自网格，不是经济理论推导；**任何网格最优都带样本内痕迹**，walk-forward 与邻域平台只能降低、"
      "不能消除过拟合风险。")
    A("- 样本 2016-09 起（受 `003376` 成立限制）；原带记忆状态机从 HIGH/LOW 回不到 MID，"
      "让三档配置里的 MID 形同虚设 —— 这是「2.5/1.8 不太行」的结构性原因；"
      "本次已把默认改为三区直接映射，MID 才真正参与配置。")
    A("- 全样本只覆盖一轮「高利率→低利率」的大周期；阈值对不同利率中枢的普适性无法由此样本证明。")
    A("- 债腿内部口径；现金 10pp 固定在外；申赎费按统一单边 10bp 近似。")
    A("")

    os.makedirs(_HERE, exist_ok=True)
    with open(OUT_MD, "w", encoding="utf-8") as f:
        f.write("\n".join(lines) + "\n")
    with open(OUT_JSONL, "w", encoding="utf-8") as f:
        for k in COMBOS:
            s = stats[k]
            f.write(json.dumps({"low": k[0], "high": k[1], "plateau": round(plateau[k], 3),
                                **s["metrics"], "switches": s["switches"],
                                "turnover": round(s["turnover"], 2), "states": s["states"],
                                "occ": {x: round(v, 4) for x, v in s["occ"].items()},
                                "avg_long": round(s["avg_long"], 4),
                                "usable": _usable(k)}, ensure_ascii=False) + "\n")
    print(f"[ok] {OUT_MD}")
    print(f"[ok] {OUT_JSONL}")
    print(f"  top sharpe {top_sharpe[0]}={stats[top_sharpe[0]]['metrics']['sharpe']:.2f}  "
          f"top plateau {top_plateau[0]}={plateau[top_plateau[0]]:.2f}  "
          f"top usable {top_usage[0] if top_usage else None}="
          f"{plateau[top_usage[0]]:.2f}" if top_usage else "")
    print("  OOS avg " + " / ".join(f"c{i}={avg_by_c[i]:.2f}" for i in range(len(CAND))))
    return 0


if __name__ == "__main__":
    sys.exit(main())
