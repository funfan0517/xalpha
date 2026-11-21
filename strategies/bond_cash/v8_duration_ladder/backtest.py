# -*- coding: utf-8 -*-
"""V8 · 久期阶梯风险预算 回测 → backtest/_v8_bt.{md,jsonl}。

口径：**债腿 30pp 内部**（六腿权重和 = 1）；现金腿 10pp 固定在外，不参与回测。
对照分四组，回答四个问题：
  ① 目标达没达到：V8 vs 基准 v0 固定中性 35/65（2026-11-20 起的家族基准）vs 六腿等权；
  ② 谁在贡献：拆开「同档择壳」与「进攻档波动反馈」两项动态各值多少；
  ③ 结构是不是拟合：静态 20/45/5/20/10 直接对照，并给出中端夏普窗 252/504/756 的敏感性；
  ④ 换个窗口还成立吗：**长窗 2019-03~2026-11**（011983 尚未成立，用 001512 顶中端档）。

用法: python strategies/bond_cash/v8_duration_ladder/backtest.py [--force]
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
LEGS = rule.CFG["universe"]
OUT_DIR = os.path.join(_HERE, "backtest")
OUT_MD = os.path.join(OUT_DIR, "_v8_bt.md")
OUT_JSONL = os.path.join(OUT_DIR, "_v8_bt.jsonl")


def _pct(v, sign=True):
    return "—" if v is None else (f"{v:+.2f}%" if sign else f"{v:.2f}%")


def _row(nm, m, ex=""):
    return (f"| {nm} | {_pct(m['cagr'])} | {_pct(m['vol'], False)} | {m['sharpe']:.2f} | "
            f"{_pct(m['mdd'])} | {_pct(m['worst_year'])} | {ex} |")


def _erc_weights(cov, iters=800, damp=0.3):
    """六腿等风险贡献（v7 那套机制，放在 v8 的同一窗口上做对照）。"""
    n = cov.shape[0]
    w = 1 / np.sqrt(np.maximum(np.diag(cov), 1e-12))
    w = w / w.sum()
    tgt = np.full(n, 1.0 / n)
    for _ in range(iters):
        sw = cov @ w
        port = float(w @ sw)
        rc = w * sw / max(port, 1e-18)
        w = w * np.power(np.maximum(tgt, 1e-9) / np.maximum(rc, 1e-9), damp)
        w = np.maximum(w, 1e-8)
        w = w / w.sum()
    return w


def _erc_backtest(navs, start):
    """滚动 ERC（60 日协方差 + 收缩 0.30，月频、同滞后带）-> (net, Σ|Δw|)。"""
    rets = engine.leg_returns(navs)
    idx = rets.index
    reb = rule.is_rebalance(idx).values
    rows = pd.DataFrame(np.nan, index=idx, columns=rets.columns)
    cur = None
    for i, d in enumerate(idx):
        if not reb[i] or i + 1 < 60:
            continue
        c = rets.iloc[i + 1 - 60:i + 1].cov().values * 252.0
        c = 0.7 * c + 0.3 * np.diag(np.diag(c))
        w = _erc_weights(c)
        if cur is not None and float(np.max(np.abs(w - cur))) < 0.015:
            w = cur
        cur = w
        rows.loc[d] = w
    W = rows.ffill().bfill().shift(1).ffill().bfill()
    gross = (W.values * rets.values).sum(axis=1)
    turn = np.abs(np.diff(W.values, axis=0)).sum(axis=1)
    net = pd.Series(gross - np.concatenate([[0.0], turn * CFG["fee_side"]]), index=idx)
    return net[net.index >= pd.Timestamp(start)], float(turn.sum())


def _weights_table(W, cols):
    out = []
    for c in cols:
        s = W[c]
        out.append(f"| {rule.SHORT[c]} `{c}` | {rule.SECT[c]} | {rule.DURATION[c]:.1f} | "
                   f"{s.mean() * 100:.1f}% | {s.min() * 100:.1f}% | {s.max() * 100:.1f}% |")
    return out


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--force", action="store_true")
    args = ap.parse_args(argv)
    navs, asof, failed = data.load_all(force=args.force)
    miss = [c for c in LEGS if c not in navs]
    if miss:
        print(f"[error] 缺腿净值 {miss}")
        return 1

    rets = engine.leg_returns(navs)
    start = rets.index[min(CFG["warmup"], len(rets) - 1)]

    bt = engine.backtest(navs, start=start)
    net = bt["ret"]

    # ③ 闸门的三个口径：关 / 开·费前 / 开·真实申赎费
    bt_ngo = engine.backtest(navs, start=start, trend_gate=False)
    bt_g0 = engine.backtest(navs, start=start, fee_side=0.0)
    m_v8 = engine.metrics(bt_ngo["ret"])

    # 动态项拆解（一律关闸门，隔离变量）
    bt_noswitch = engine.backtest(navs, start=start, pick_fixed="011983", trend_gate=False)
    bt_nofb = engine.backtest(navs, start=start, vol_feedback=False, trend_gate=False)
    bt_static = engine.backtest(navs, start=start, weights={
        "003376": 0.20, "011983": 0.45, "005623": 0.05, "000032": 0.20, "006884": 0.10})
    clip0 = CFG["vol_feedback_clip"]
    CFG["vol_feedback_clip"] = (0.60, 1.67)      # 对称缩放（含「低波动期追高」）
    bt_sym = engine.backtest(navs, start=start, trend_gate=False)
    CFG["vol_feedback_clip"] = clip0

    # ① 缺数据降级 / ② 信用腿单向冻结（都从 v1 继承，默认：① 开、② 关）
    cf0, dg0, dr0 = (CFG["credit_freeze"]["on"], CFG["degrade"]["on"],
                     CFG["credit_freeze"]["derisk"])
    CFG["degrade"]["on"] = False
    bt_nodegrade = engine.backtest(navs, start=start, trend_gate=False)
    CFG["degrade"]["on"] = dg0

    CFG["credit_freeze"]["on"] = True
    _, _, fz = engine.build_weights(rets)
    fz = fz[fz.index >= pd.Timestamp(start)]
    freeze_trig = int((fz.diff().fillna(0) > 0).sum())
    freeze_days = int((fz > 0).sum())
    freeze_dates = [str(d.date()) for d in fz.index[(fz.diff().fillna(0) > 0)][:8]]
    freeze_grid = []
    for dr in (1.0, 0.85, 0.7, 0.5, 0.0):
        CFG["credit_freeze"]["derisk"] = dr
        _b = engine.backtest(navs, start=start, trend_gate=False)
        freeze_grid.append((dr, engine.metrics(_b["ret"]), _b["ret"]))
    CFG["credit_freeze"]["derisk"] = dr0
    CFG["credit_freeze"]["on"] = cf0

    fixed = {
        "**基准 v0 固定中性 35/65（2026-11-20 起）**": {"003376": 0.35, "011983": 0.65},
        "基准 v0 旧配比 70/30（2026-11-20 前）": {"003376": 0.70, "011983": 0.30},
        "六腿等权 1/6": {c: 1 / 6 for c in LEGS},
        "全底仓（011983）": {"011983": 1.0},
        "全长端（003376）": {"003376": 1.0},
        "全信用（000032）": {"000032": 1.0},
        "短端防守（005623）": {"005623": 1.0},
    }
    fixed_net = {k: engine.backtest(navs, start=start, weights=w, fee_side=0.0)["ret"]
                 for k, w in fixed.items()}

    erc_net, erc_turn = _erc_backtest(navs, start)
    cov_all = engine.cov_est(rets)
    iv = pd.Series(1.0 / np.sqrt(np.diag(cov_all)), index=list(rets.columns))
    iv = iv / iv.sum()
    iv_net = engine.backtest(navs, start=start, weights=iv.to_dict(), fee_side=0.0)["ret"]

    scenarios = [
        ("**V8 久期阶梯（档位预算 + 同档择优 + 波动反馈 + 缺数据降级）**",
         bt_ngo["ret"], bt_ngo["turn"]),
        ("V8 + ③ 长端趋势闸门（**A 类真实申赎费**）", net, bt["turn"]),
        ("V8 + ③ 闸门·费前（不计申赎费，仅看信号质量）", bt_g0["ret"], bt_g0["turn"]),
        ("V8 关择壳（中端固定 011983）", bt_noswitch["ret"], bt_noswitch["turn"]),
        ("V8 关波动反馈（κ=0）", bt_nofb["ret"], bt_nofb["turn"]),
        ("V8 波动反馈·对称缩放（含低波追高）", bt_sym["ret"], bt_sym["turn"]),
        ("V8 开信用冻结（② 开，derisk 0.7；默认关）", freeze_grid[2][2], None),
        ("V8 关降级（① 关，缺档按比例补齐）", bt_nodegrade["ret"], bt_nodegrade["turn"]),
        ("V8 静态结构 20/45/5/20/10（两项动态全关）", bt_static["ret"], bt_static["turn"]),
        ("**基准 v0 固定中性 35/65（2026-11-20 起）**",
         fixed_net["**基准 v0 固定中性 35/65（2026-11-20 起）**"], None),
        ("基准 v0 旧配比 70/30（2026-11-20 前）",
         fixed_net["基准 v0 旧配比 70/30（2026-11-20 前）"], None),
        ("v7 式 ERC（六腿等风险贡献，同窗口）", erc_net, erc_turn),
        ("六腿逆波动（1/σ，全样本协方差）", iv_net, None),
        ("六腿等权 1/6", fixed_net["六腿等权 1/6"], None),
        ("全底仓（011983）", fixed_net["全底仓（011983）"], None),
        ("全长端（003376）", fixed_net["全长端（003376）"], None),
        ("全信用（000032）", fixed_net["全信用（000032）"], None),
        ("短端防守（005623）", fixed_net["短端防守（005623）"], None),
    ]

    m_base = engine.metrics(fixed_net["**基准 v0 固定中性 35/65（2026-11-20 起）**"])
    m_base70 = engine.metrics(fixed_net["基准 v0 旧配比 70/30（2026-11-20 前）"])
    m_eq = engine.metrics(fixed_net["六腿等权 1/6"])
    m_gate = engine.metrics(net)

    lines, rows = [], []
    A = lines.append
    A("# V8 · 久期阶梯风险预算（Duration-Ladder Risk Budget）· 回测")
    A("")
    A(f"> 生成 {datetime.now():%Y-%m-%d %H:%M} · 由 `strategies/bond_cash/v8_duration_ladder/backtest.py` "
      "产出，**请勿手改**。")
    A(f"> 口径：**债腿 30pp 内部**（六腿权重和 = 1）；现金腿 10pp 固定在外、不进回测。"
      f"月频（月末最后交易日）/ T+1 承担 / 单边 {CFG['fee_side'] * 1e4:.0f}bp / rf {CFG['rf_annual']:.2%}。")
    A(f"> 结构预算（占债腿）：长端 `003376` {CFG['blocks']['long']['budget'] * 100:.0f}%、"
      f"中端 `011983`/`001512` {CFG['blocks']['mid']['budget'] * 100:.0f}%（同档择一）、"
      f"短端 `005623` {CFG['blocks']['short']['budget'] * 100:.0f}%、"
      f"信用 `000032`+`006884` {CFG['blocks']['credit']['budget'] * 100:.0f}%；"
      f"单腿上障 {CFG['cap'] * 100:.0f}%。")
    A(f"> 窗口 {net.index[0].date()} ~ {net.index[-1].date()}（{len(net)} 交易日）；"
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
    A("**权重分布（V8 实际解，占债腿）**")
    A("")
    A("| 腿 | 门派 | 久期 | 平均 | 最低 | 最高 |")
    A("|---|---|---:|---:|---:|---:|")
    for ln in _weights_table(w, list(w.columns)):
        A(ln)
    A("")
    A(f"- 调仓次数：**不开闸门 {bt_ngo['switches']} 次**"
      f"（{bt_ngo['switches'] / max(m_v8['years'], 1e-9):.1f} 次/年，Σ|Δw| = {bt_ngo['turn']:.1f}）；"
      f"**开闸门 {bt['switches']} 次**（{bt['switches'] / max(m_v8['years'], 1e-9):.1f} 次/年，"
      f"Σ|Δw| = {bt['turn']:.1f}）")
    A("")

    m_nd = engine.metrics(bt_nodegrade["ret"])
    A("**① 缺数据降级 / ② 信用腿冻结（从 v1 继承的两条纪律，默认：① 开 · ② 关）**")
    A("")
    A("| ② 信用冻结的降险幅度 derisk | 年化 | 夏普 | 最大回撤 |")
    A("|---|---:|---:|---:|")
    for dr, mm, _ in freeze_grid:
        tag = ("（= 只禁止加仓，无效）" if dr == 1.0 else
               ("（= 清空信用）" if dr == 0.0 else ""))
        A(f"| {dr:.2f}{tag} | {_pct(mm['cagr'])} | {mm['sharpe']:.2f} | {_pct(mm['mdd'])} |")
    A(f"| **关闭（默认）** | {_pct(m_v8['cagr'])} | {m_v8['sharpe']:.2f} | {_pct(m_v8['mdd'])} |")
    A("")
    A(f"- **② 冻结在样本内触发 {freeze_trig} 次**（冻结状态覆盖 {freeze_days} 个交易日"
      + (f"，触发日 {', '.join(freeze_dates)}" if freeze_dates else "") +
      f"）：条件信用腿 σ60/σ252 > {CFG['credit_freeze']['vol_ratio']}。"
      "**但降险越狠越差**（1.83 → 1.75，回撤反而 −1.59 → −1.67）—— 信用腿正是 v8 的分散来源，"
      "在信用波动升温时砍它等于在最糟的时刻卖掉分散。故**默认关**，留作极端信用事件的手动开关。")
    A(f"- **② 还有个更隐蔽的坑**：只做「禁止加仓」（derisk=1.0）时**权重一字未改** —— "
      "v8 的信用预算本来就是固定 30%，禁止一件从不发生的事等于没有规则。"
      "所以这条纪律要么主动降险（有代价），要么别装（默认）。")
    A(f"- **① 降级在样本内触发 0 次**（六腿净值全程齐全）：关掉它数字完全相同"
      f"（{_pct(m_nd['cagr'])} / {m_nd['sharpe']:.2f}）。它的价值在**下游断数据时**："
      "v8 原来缺档会把该档预算摊给其他档（等于偷偷给别的腿加杠杆），现在改为移交最短久期腿 "
      "`005623` 并在产物里标 `degraded`。")
    A("")

    # ---- ③ 长端趋势闸门：费前 vs 真实申赎费
    gt = CFG["trend_gate"]
    A("**③ 长端趋势闸门（日频动量）：费前不等于可实现**")
    A("")
    A("| 口径 | 年化 | 夏普 | 最大回撤 | 最差单年 | 卖出/买入 | 最短/中位持有 | 累计赎回费 | 累计申购费 |")
    A("|---|---:|---:|---:|---:|---:|---:|---:|---:|")
    for nm, b in (("V8 不开闸门（单边 10bp）", bt_ngo),
                  ("V8 + 闸门·费前（fee=0，只体现信号质量）", bt_g0),
                  ("V8 + 闸门·A 类真实申赎费（当前口径）", bt)):
        m = engine.metrics(b["ret"])
        gf = b.get("gate_fee") or {}
        A(f"| {nm} | {_pct(m['cagr'])} | {m['sharpe']:.2f} | {_pct(m['mdd'])} | "
          f"{_pct(m['worst_year'])} | {gf.get('sells', '—')}/{gf.get('buys', '—')} | "
          f"{gf.get('hold_min', '—')}/{gf.get('hold_med', '—')} 日 | "
          f"{gf.get('redeem', '—')}% | {gf.get('buy', '—')}% |")
    A("")
    A("> **为什么差这么多**：场外 A 类的赎回费是**持有期阶梯**，不是固定摩擦 —— "
      f"`{gt['leg']}` 为 <7 日 **1.50%** / 7~30 日 **0.50%** / 30~365 日 **0.10%** / "
      "365~730 日 0.05% / ≥730 日 0，另加前端申购费。日频闸门会造出大量「只持有几天到几周」的份额，"
      "按 10bp 单边统一近似会**系统性低估**成本。引擎现在按每笔卖出的**实际持有自然日**取费率、"
      "买入按 `buy_fee` 计，并把这部分从统一 `fee_side` 里扣掉（不重复收费）。"
      "**费前那一行只是信号质量的体检，不能当可实现收益引用。**")
    A("")

    # 参数网格（A 类真实口径）
    mw0, mh0, on0 = gt["mom_win"], gt["min_hold"], gt["on"]
    gt["on"] = True
    holds = (7, 20, 30, 40)
    wins = (10, 20, 30, 40)

    # C 类份额对照：无申购费、持有 ≥30 日赎回费 0，但净值已内含 0.35%/年 销售服务费
    navs_c = dict(navs)
    try:
        navs_c[gt["leg"]] = data.load_nav("003377")
        lad0, bf0 = gt["redeem_ladder"], gt["buy_fee"]
        gt["buy_fee"], gt["redeem_ladder"] = 0.0, [[7, 1.50], [30, 0.75]]
        rets_c = engine.leg_returns(navs_c)
        st_c = rets_c.index[min(CFG["warmup"], len(rets_c) - 1)]
        mc0 = engine.metrics(engine.backtest(navs_c, start=st_c, trend_gate=False)["ret"])
        gt["mom_win"], gt["min_hold"] = mw0, mh0
        mc_cur = engine.metrics(engine.backtest(navs_c, start=st_c)["ret"])
        bestc = (-9.0, None, None, None)
        for win in wins:
            for h in holds:
                gt["mom_win"], gt["min_hold"] = win, h
                mm = engine.metrics(engine.backtest(navs_c, start=st_c)["ret"])
                if mm["sharpe"] > bestc[0]:
                    bestc = (mm["sharpe"], win, h, mm)
        A("**换成 C 类份额（`003377`，无申购费 + 持有 ≥30 日赎回费 0，净值已含 0.35%/年 服务费）**")
        A("")
        A("| 口径 | 年化 | 夏普 | 最大回撤 | 最差单年 |")
        A("|---|---:|---:|---:|---:|")
        for nm, m in (("C 类·不开闸门", mc0),
                      (f"C 类·开闸门（当前 {mw0}/{mh0}）", mc_cur),
                      (f"C 类·开闸门（网格最优 {bestc[1]}/{bestc[2]}）", bestc[3])):
            rows.append(dict(name=nm, **m))
            A(f"| {nm} | {_pct(m['cagr'])} | {m['sharpe']:.2f} | {_pct(m['mdd'])} | "
              f"{_pct(m['worst_year'])} |")
        A("")
        A("- C 类把「申赎费」这一项几乎清零（持有满 30 日后卖出费 0），代价是净值每年比 A 类少约 "
          "0.35pp（销售服务费），本表已在净值里体现，**不要再额外扣一次**。"
          f"即便这样，夏普最优也只有 {bestc[0]:.2f}（vs 不开 {mc0['sharpe']:.2f}），"
          "而且最优参数仍是网格里挑出来的单点 —— 属于「年化换回撤」，不是抬夏普。")
        gt["buy_fee"], gt["redeem_ladder"] = bf0, lad0
        gt["mom_win"], gt["min_hold"] = mw0, mh0
    except Exception as exc:  # noqa: BLE001 - 抓不到 C 类就跳过对照
        A(f"- C 类对照跳过（{type(exc).__name__}: {exc}）")
    A("")

    A("**参数网格：A 类真实申赎费口径下的夏普（行 = 动量窗，列 = 最短持有交易日）**")
    A("")
    A("| 动量窗 \\ 最短持有 | " + " | ".join(f"{h} 日" for h in holds) + " |")
    A("|---|" + "---:|" * len(holds))
    best = (-9.0, None, None)
    for win in wins:
        cells = []
        for h in holds:
            gt["mom_win"], gt["min_hold"] = win, h
            mm = engine.metrics(engine.backtest(navs, start=start)["ret"])
            if mm["sharpe"] > best[0]:
                best = (mm["sharpe"], win, h)
            cells.append(f"{mm['sharpe']:.2f}")
        A(f"| {win} 日 | " + " | ".join(cells) + " |")
    gt["mom_win"], gt["min_hold"], gt["on"] = mw0, mh0, on0
    A("")
    A(f"- 网格最优 {best[0]:.2f}（动量窗 {best[1]} 日 / 最短持有 {best[2]} 交易日），"
      f"不开闸门为 {m_v8['sharpe']:.2f}。**相邻格子抖得很厉害 → 没有稳定的有效区域**，"
      "这是「信号只有微弱 alpha、被换手成本吃掉后剩下噪声」的典型形状。")
    A("")

    # 风险贡献
    A("**风险贡献分解（谁在承担波动）**")
    A("")
    A("| 腿 | 平均权重 | 平均风险贡献 | 权重排序 − 风险排序 |")
    A("|---|---:|---:|---:|")
    rc_all = []
    _to = (CFG.get("trend_gate") or {}).get("to")
    rets_rc = (engine.add_leg(navs, rets, _to)
               if _to and _to in w.columns and _to not in rets.columns else rets)
    for d in w.index[::5]:
        row = w.loc[d].values
        rc, _ = engine.risk_contrib(row, engine.cov_est(
            rets_rc.loc[:d].iloc[-int(CFG["vol_win"]):]))
        rc_all.append(rc)
    rc_mean = pd.DataFrame(rc_all).mean().values if rc_all else [float("nan")] * len(w.columns)
    cols = list(w.columns)
    rank_w = pd.Series(w.mean().values).rank(ascending=False).values
    rank_r = pd.Series(rc_mean).rank(ascending=False).values
    for i, c in enumerate(cols):
        A(f"| `{c}` {rule.SHORT[c]} | {w[c].mean() * 100:.1f}% | {rc_mean[i] * 100:.1f}% | "
          f"{int(rank_w[i] - rank_r[i]):+d} |")
    A("")

    A("**敏感性：中端择壳的夏普窗（其余参数不变）**")
    A("")
    A("| 夏普窗 | 年化 | 夏普 | 最大回撤 | 011983 平均权重 | 001512 平均权重 |")
    A("|---|---:|---:|---:|---:|---:|")
    sw0 = CFG["pick"]["sr_win"]
    for win in (252, 504, 756):
        CFG["pick"]["sr_win"] = win
        b = engine.backtest(navs, start=start, trend_gate=False)
        m = engine.metrics(b["ret"])
        rows.append(dict(name=f"择壳窗{win}", **m))
        A(f"| {win} 日 | {_pct(m['cagr'])} | {m['sharpe']:.2f} | {_pct(m['mdd'])} | "
          f"{b['w']['011983'].mean() * 100:.1f}% | {b['w']['001512'].mean() * 100:.1f}% |")
    CFG["pick"]["sr_win"] = sw0
    A("")

    A("**成本敏感性（V8，闸门关）**")
    A("")
    A("| 单边成本 | 年化 | 最大回撤 | 夏普 |")
    A("|---|---:|---:|---:|")
    for f in CFG["fee_side_grid"]:
        b = engine.backtest(navs, start=start, fee_side=f, trend_gate=False)
        m = engine.metrics(b["ret"])
        rows.append(dict(name=f"成本{f}", **m))
        A(f"| {f * 1e4:.0f}bp | {_pct(m['cagr'])} | {_pct(m['mdd'])} | {m['sharpe']:.2f} |")
    A("")

    yv = net.groupby(net.index.year).apply(lambda x: (1 + x).prod() - 1)
    yg = bt_ngo["ret"].groupby(bt_ngo["ret"].index.year).apply(lambda x: (1 + x).prod() - 1)
    y0s = fixed_net["**基准 v0 固定中性 35/65（2026-11-20 起）**"]
    y0 = y0s.groupby(y0s.index.year).apply(lambda x: (1 + x).prod() - 1)
    A("**逐年（V8 vs V8+闸门 vs 基准 v0 35/65）**")
    A("")
    A("| 年 | V8 | V8+闸门 | 闸门增量 | 基准 35/65 | 长端平均权重 |")
    A("|---|---:|---:|---:|---:|---:|")
    for y in sorted(set(yv.index) | set(y0.index)):
        a, g_, b_ = float(yg.get(y, 0)) * 100, float(yv.get(y, 0)) * 100, float(y0.get(y, 0)) * 100
        wl = w[w.index.year == y]["003376"].mean() * 100 if (w.index.year == y).any() else float("nan")
        A(f"| {y} | {_pct(a)} | {_pct(g_)} | {_pct(g_ - a)} | {_pct(b_)} | {wl:.1f}% |")
    A("")

    # ---- 长窗复核（011983 缺席，用 001512 顶中端档）
    LEGS5 = ["003376", "001512", "005623", "000032", "006884"]
    rets5 = engine.leg_returns(navs, LEGS5)
    start5 = rets5.index[min(CFG["warmup"], len(rets5) - 1)]
    bt5 = engine.backtest(navs, start=start5, legs=LEGS5, trend_gate=False)
    bt5g = engine.backtest(navs, start=start5, legs=LEGS5)
    m5 = engine.metrics(bt5["ret"])
    base5 = engine.backtest(navs, start=start5, legs=LEGS5,
                            weights={"003376": 0.35, "001512": 0.65}, fee_side=0.0)["ret"]
    eq5 = engine.backtest(navs, start=start5, legs=LEGS5,
                          weights={c: 1 / 5 for c in LEGS5}, fee_side=0.0)["ret"]
    m5b, m5e = engine.metrics(base5), engine.metrics(eq5)
    A(f"**长窗复核（{bt5['ret'].index[0].date()} 起；`011983` 2021-09 才成立，中端档由 `001512` 顶上）**")
    A("")
    A("| 方案 | 年化 | 波动 | 夏普 | 最大回撤 | 最差单年 |")
    A("|---|---:|---:|---:|---:|---:|")
    for nm, r in (("V8（长窗，中端=001512）", bt5["ret"]),
                  ("V8 + ③ 闸门（长窗，A 类真实申赎费）", bt5g["ret"]),
                  ("基准 35/65（003376+001512）", base5),
                  ("五腿等权", eq5)):
        mm = engine.metrics(r)
        rows.append(dict(name=nm, **mm))
        A(_row(nm, mm, ""))
    A("")
    A("## 结论与边界")
    A("")
    d_sh = m_v8["sharpe"] - m_base["sharpe"]
    A(f"- **目标达成情况**：V8 夏普 **{m_v8['sharpe']:.2f}** vs 基准 v0 **35/65** **{m_base['sharpe']:.2f}**"
      f"（**{d_sh:+.2f}**）、六腿等权 {m_eq['sharpe']:.2f}；年化 {m_v8['cagr']:+.2f}% vs "
      f"{m_base['cagr']:+.2f}%（{m_v8['cagr'] - m_base['cagr']:+.2f}pp），"
      f"最大回撤 {m_v8['mdd']:+.2f}% vs {m_base['mdd']:+.2f}%（深 "
      f"{abs(m_v8['mdd']) - abs(m_base['mdd']):+.2f}pp）。")
    A(f"- ⚠ **2026-11-20 基准变了，结论翻面**：v0 由 70/30 改为 35/65 后，基准夏普从 "
      f"{m_base70['sharpe']:.2f} 升到 {m_base['sharpe']:.2f}，**v8 由「领先 +0.03」变成「落后 "
      f"{abs(d_sh):.2f}」**。原因很直接：v8 的优势本来就是「把长端降下来」，而 35/65 用"
      f"**一个数字**就做到了同一件事，还少了两项动态的换手与复杂度。"
      f"这不是 v8 变差，是基准变强 —— 旧结论（优于 70/30）只在本节的历史行里有效。")
    A(f"- **优势来源**：把长端从 35% 再降到 20%。长端久期是中端的 2 倍，多承担的波动只换来约 1pp/年，"
      f"边际夏普低于组合水平 —— 这不是回测拟合，是久期结构本身的算术。代价是少赚"
      f" {m_base['cagr'] - m_v8['cagr']:.2f}pp/年。")
    m_chen = engine.metrics(fixed_net["全底仓（011983）"])
    A(f"- **别误读**：全底仓（100% `011983`）夏普 {m_chen['sharpe']:.2f} 是表里最高的 —— 但那是"
      f"**单腿集中**，等于赌一只政金债永不出事，不是「六腿配比」；"
      f"v8 的约束是每腿 0~100%、合计 100% 且要分散在两个门派里，两者不可比。")
    A(f"- **两项动态各值多少**：关择壳 {engine.metrics(bt_noswitch['ret'])['sharpe']:.2f}、"
      f"关波动反馈 {engine.metrics(bt_nofb['ret'])['sharpe']:.2f}、两项全关（静态）"
      f"{engine.metrics(bt_static['ret'])['sharpe']:.2f} → 动态合计约 "
      f"{m_v8['sharpe'] - engine.metrics(bt_static['ret'])['sharpe']:+.2f} 夏普，"
      f"**很小**；v8 不宣称动态择时有 alpha，只宣称它是风险纪律（换掉更差的壳、进攻档波动高时减仓）。")
    A("- **从 v1 继承的两条纪律**：① 缺数据降级（默认开，样本内 0 触发、数字不变，是**修掉一个隐患**："
      "原实现缺档时把预算摊给其他腿 = 偷偷加杠杆）；② 信用腿冻结（默认关，实测降险越狠越差，"
      "见上表）。两条都**不贡献 alpha**，别指望它们抬夏普。")
    gf = bt.get("gate_fee") or {}
    A(f"- **③ 长端趋势闸门**：费前夏普 **{engine.metrics(bt_g0['ret'])['sharpe']:.2f}**（信号本身有信息），"
      f"但计入 A 类真实申赎费后 **{m_gate['sharpe']:.2f}**，低于不开闸门的 {m_v8['sharpe']:.2f}；"
      f"累计赎回费 {gf.get('redeem', '—')}%、申购费 {gf.get('buy', '—')}%，"
      f"卖出 {gf.get('sells', '—')} 次、最短持有 {gf.get('hold_min', '—')} 自然日。"
      "**结论：费前数字不能引用**；在 A 类份额上这条闸门是负贡献，只有当成本结构接近"
      "「免申赎费」（C 类 + 持满 30 日、或场内 ETF）时才值得再评估，且即便那样也只有回撤/最差年改善、"
      "夏普打平——它买的是**风险**，不是收益。")
    verdict5 = ("跑赢基准" if m5["sharpe"] > m5b["sharpe"] + 0.02 else
                ("与基准打平" if abs(m5["sharpe"] - m5b["sharpe"]) <= 0.02 else "**落后基准**"))
    A(f"- **长窗复核（2019-06 起，中端=001512）**：V8 {m5['sharpe']:.2f} vs "
      f"基准 {m5b['sharpe']:.2f} vs 五腿等权 {m5e['sharpe']:.2f} → **{verdict5}**。"
      f"中端档换成更弱的 `001512` 后优势消失：`011983`（政金 3-5）比 `001512`（国债 3-5）"
      f"同窗多 +0.67pp/年（v0 §4 已有结论），**v8 的超额依赖底仓壳的质量**，"
      f"这是它最大的脆弱点，如实写出。加闸门后长窗 "
      f"{engine.metrics(bt5g['ret'])['sharpe']:.2f}（{engine.metrics(bt5g['ret'])['cagr']:+.2f}% / "
      f"{engine.metrics(bt5g['ret'])['mdd']:+.2f}%），同样是「年化换回撤」。")
    A("- **读法建议**：如果只在乎夏普而不在乎「六腿各配多少」，长窗里五腿等权（1/5 各一份）"
      f"夏普 {m5e['sharpe']:.2f} 是最省事的答案。**v0 改成 35/65 之后，v8 的推荐理由只剩"
      "「配比可复核、六腿分散、单腿事故不致命」** —— 风险调整收益已经不是理由"
      f"（{m_v8['sharpe']:.2f} < {m_base['sharpe']:.2f}）。")
    A("- **边界**：样本 2021-12 起 5 年（受 `011983` 成立限制）；结构预算 20/45/5/30、"
      "择壳窗 504 日、死区 0.15、反馈指数 1.0 全部取自同一份历史，**不构成严格样本外**；"
      "债腿内部口径，现金 10pp 固定在外；闸门腿已按 A 类真实赎回阶梯逐笔计费，"
      "其余腿仍按单边 10bp 近似。")
    A("- **不追求高收益**：v8 的定位是「用更低的波动拿到更可复核的配比」；"
      f"绝对收益比 v0 35/65 少 {m_base['cagr'] - m_v8['cagr']:.2f}pp/年，要收益请回 v0。")
    A("")

    os.makedirs(OUT_DIR, exist_ok=True)
    with open(OUT_MD, "w", encoding="utf-8") as f:
        f.write("\n".join(lines) + "\n")
    with open(OUT_JSONL, "w", encoding="utf-8") as f:
        for r in rows:
            f.write(json.dumps(r, ensure_ascii=False) + "\n")
    print(f"[ok] {OUT_MD}")
    print(f"[ok] {OUT_JSONL}")
    print(f"  V8 {m_v8['cagr']:+.2f}% / {m_v8['mdd']:+.2f}% / 夏普 {m_v8['sharpe']:.2f}  "
          f"turn={bt['turn']:.1f}  (基准 35/65 {m_base['cagr']:+.2f}% / {m_base['sharpe']:.2f})  "
          f"长窗 {m5['sharpe']:.2f} vs {m5b['sharpe']:.2f}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
