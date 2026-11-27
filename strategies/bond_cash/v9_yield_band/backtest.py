# -*- coding: utf-8 -*-
"""V9 · 10Y 国债收益率阈值久期切换 → backtest/_v9_bt.{md,jsonl}。

对照分五组：
  ① 目标达没达到：V9 vs 长债/短债买入持有、v0 35/65、债券指数代理、货币、股债 60/40；
  ② 哪个口径有效：三区直接映射（默认） / 带记忆阈值 / 滚动分位；
  ③ 分段：全期 / 2016–2020 / 2021–2026；
  ④ 稳健性：阈值邻域 1.9/3.0 与 2.1/3.0、分位 25/75 与 35/65、月末/周末、0/10/30bp；
  ⑤ 滚动 3 年窗口（非严格样本外）逐段比较。

用法：python strategies/bond_cash/v9_yield_band/backtest.py [--force] [--refresh]
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
OUT_MD = os.path.join(OUT_DIR, "_v9_bt.md")
OUT_JSONL = os.path.join(OUT_DIR, "_v9_bt.jsonl")


def _pct(v, sign=True):
    return "—" if v is None else (f"{v:+.2f}%" if sign else f"{v:.2f}%")


def _row(nm, m, ex=""):
    return (f"| {nm} | {_pct(m['cagr'])} | {_pct(m['vol'], False)} | {m['sharpe']:.2f} | "
            f"{_pct(m['mdd'])} | {m['calmar']:.2f} | {m['daily_win']:.1f}% | "
            f"{m['monthly_win']:.1f}% | {m['dd_days']} | {ex} |")


_HEADER = ("| 方案 | 年化 | 波动 | 夏普 | 最大回撤 | 卡玛 | 日胜率 | 月胜率 | 最长水下(日) | 备注 |\n"
           "|---|---:|---:|---:|---:|---:|---:|---:|---:|---|")


def _slice(s, start, end=None):
    s = s[s.index >= pd.Timestamp(start)]
    if end:
        s = s[s.index <= pd.Timestamp(end)]
    return s


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--force", action="store_true", help="强制刷新基金净值缓存")
    ap.add_argument("--refresh", action="store_true", help="强制抓取 10Y 国债收益率")
    args = ap.parse_args(argv)

    navs, asof, failed = data.load_all(force=args.force)
    y = data.load_yield(refresh=args.refresh)
    hs = data.load_hs300_tr()
    need = CFG["universe"] + ["000032"]
    if [c for c in need if c not in navs]:
        print(f"[error] 缺腿净值 {[c for c in need if c not in navs]}")
        return 1

    sig = engine.signal_frame(y)                              # 默认：三区直接映射
    sig_hyst = engine.signal_frame(y, hysteresis=True)        # 对照：带记忆状态机
    sig_pct = engine.signal_frame(y, mode="percentile")

    bt = engine.backtest(navs, sig["state"])
    start = max(pd.Timestamp(CFG["windows"][0][1]), bt["ret"].index[0])
    net = _slice(bt["ret"], start)
    m_v9 = engine.metrics(net)

    def run(sig_x, **kw):
        return engine.backtest(navs, sig_x["state"], **kw)["ret"]

    def fixed(weights):
        return engine.fixed_backtest(navs, weights, start=start)

    net_novt = _slice(engine.backtest(navs, sig["state"], vol_target=False)["ret"], start)
    net_vt12 = _slice(engine.backtest(
        navs, sig["state"], vol_target=dict(CFG["vol_target"], target=0.012))["ret"], start)
    net_vt14 = _slice(engine.backtest(
        navs, sig["state"], vol_target=dict(CFG["vol_target"], target=0.014))["ret"], start)
    net_hyst = _slice(run(sig_hyst), start)
    net_pct = _slice(run(sig_pct), start)
    net_g0 = _slice(engine.backtest(navs, sig["state"], fee_side=0.0)["ret"], start)

    sc = [
        ("**V9 阈值+波动目标 1.0%（默认）**", net,
         f"缩放均值 {bt['scale'].mean():.2f} / Σ\\|Δw\\|={bt['turnover']:.1f} / {bt['switches']} 次"),
        ("V9 阈值·无波动缩放（对照）", net_novt, "上一版口径"),
        ("V9 阈值+波动目标 1.2%", net_vt12, "收益/回撤平衡"),
        ("V9 阈值+波动目标 1.4%", net_vt14, "更少换手"),
        ("V9 阈值·带记忆状态机（对照）", net_hyst, "对照：MID 档不可达"),
        ("V9 分位·直接映射（5 年 30/70）", net_pct, "对照：相对自身历史高低"),
        ("V9 阈值·费前（fee=0）", net_g0, "只看信号质量，不可实现"),
        ("**基准 v0 固定中性 35/65**", fixed({"003376": 0.35, "001512": 0.65}), "家族基准"),
        ("买入持有长债 003376", fixed({"003376": 1.0}), "全 7-10 年国开"),
        ("买入持有短债 002864", fixed({"002864": 1.0}), "短债 A"),
        ("利率债代理（长/中 50/50）", fixed({"003376": 0.5, "001512": 0.5}),
         "中债国债及政金债指数代理"),
        ("综合债代理（中/信用/短 50/30/20）", fixed({"001512": 0.5, "000032": 0.3, "002864": 0.2}),
         "中债综合财富指数代理"),
        ("货币基金 000198", fixed({"000198": 1.0}), "余额宝"),
    ]
    if not hs.empty:
        eq_ret = _slice(hs.pct_change().dropna(), start)
        money_ret = _slice(navs["000198"].pct_change().dropna(), start)
        sc.append(("股债 60/40（沪深300全收益/余额宝）",
                   engine.blended_backtest({"eq": (eq_ret, 0.6), "cash": (money_ret, 0.4)},
                                           start=start),
                   "跨资产对照，非债腿同类"))

    def pick(prefix):
        return next(r for n, r, _ in sc if n.strip("*").startswith(prefix))

    lines, rows = [], []
    A = lines.append
    A("# V9 · 10Y 国债收益率阈值久期切换 · 回测")
    A("")
    A(f"> 生成 {datetime.now():%Y-%m-%d %H:%M} · 由 `strategies/bond_cash/v9_yield_band/backtest.py` 产出，"
      "**请勿手改**。")
    A(f"> 信号（三区直接映射）：10Y > {CFG['threshold']['high']}% → HIGH 长/中/短 30/60/10；"
      f"{CFG['threshold']['low']}%–{CFG['threshold']['high']}% → MID 60/30/10；"
      f"< {CFG['threshold']['low']}% → LOW 10/30/60。"
      f"阈值 2.0/3.0 来自 research/threshold_tuning 的 317 组网格 + 邻域平台 + walk-forward 检验；"
      f"旧 1.8/2.5 和带记忆状态机仅作对照。")
    A(f"> 低回撤叠加（默认）：按基础档位组合过去 {CFG['vol_target']['lookback']} 日已实现波动，"
      f"月末缩放到 {CFG['vol_target']['target'] * 100:.1f}%（下限 "
      f"{CFG['vol_target']['min_scale']:.0%}，变动 <{CFG['vol_target']['min_change'] * 100:.0f}pp 不调），"
      f"剩余权重给短债 `{CFG['short_leg']}`；缩放只压波动，不改阈值与三档比例。")
    A(f"> 口径：债腿 30pp 内部（权重和 100%）；月末再平衡 + 档位突破 T+1；单边 "
      f"{CFG['fee_side'] * 1e4:.0f}bp；rf {CFG['rf_annual']:.2%}；"
      f"10Y 来源 {data.SOURCE.get('yield', '—')}；净值截止 {max(asof.values())}。")
    A(f"> 全窗口 {net.index[0].date()} ~ {net.index[-1].date()}（{len(net)} 交易日）；"
      "旧标准 3.2% 只作历史对照，不参与信号。固定权重对照按目标权重持有、不计再平衡成本。")
    A("")
    A("## 1. 全期对照")
    A("")
    A(_HEADER)
    for nm, r, ex in sc:
        m = engine.metrics(r)
        rows.append(dict(window="全期", name=nm.strip("*"), **m))
        A(_row(nm, m, ex))
    A("")

    # ---- 状态画像
    st_now = sig["state"].reindex(net.index).ffill().bfill()
    A("**状态画像（阈值口径）**")
    A("")
    A("| 状态 | 交易日 | 占比 | 区间平均 10Y | 目标权重 |")
    A("|---|---:|---:|---:|---|")
    yy = sig["yield"].reindex(net.index).ffill()
    for s in engine.STATES:
        mask = st_now == s
        n = int(mask.sum())
        avg = float(yy[mask].mean()) if n else float("nan")
        wtxt = " / ".join(f"{rule.SHORT[c]} {v * 100:.0f}%" for c, v in CFG["weights"][s].items())
        A(f"| {rule.STATE_CN[s]} | {n} | {n / max(len(net), 1) * 100:.1f}% | "
          f"{'—' if not n else f'{avg:.3f}%'} | {wtxt} |")
    A("")
    A(f"- 档位切换/缩放调仓 {bt['switches']} 次（{bt['switches'] / max(m_v9['years'], 1e-9):.1f} 次/年），"
      f"累计单边换手 Σ|Δw| = {bt['turnover']:.1f}；"
      f"10Y 区间 {yy.min():.3f}% ~ {yy.max():.3f}%，中位数 {yy.median():.3f}%。")
    sc_mean = float(bt["scale"].mean())
    A(f"- 波动缩放系数：均值 **{sc_mean:.2f}**、最低 {bt['scale'].min():.2f}、最高 "
      f"{bt['scale'].max():.2f}；实际平均长债权重 **{bt['w']['003376'].mean() * 100:.0f}%**"
      f"（基础档位口径下为 "
      f"{engine.target_matrix(st_now)['003376'].mean() * 100:.0f}%）。")
    A("")

    # ---- 分段
    A("## 2. 分段检验")
    A("")
    for label, s0, s1 in CFG["windows"]:
        seg = _slice(net, s0, s1)
        if len(seg) < 30:
            continue
        A(f"**{label}（{seg.index[0].date()} ~ {seg.index[-1].date()}，{len(seg)} 日）**")
        A("")
        A(_HEADER)
        seg_rows = [("V9 阈值+波动目标 1.0%（默认）", net), ("基准 35/65", pick("基准 v0")),
                    ("买入持有长债", pick("买入持有长债")), ("买入持有短债", pick("买入持有短债")),
                    ("股债 60/40", pick("股债 60/40"))]
        for nm, r in seg_rows:
            rr = _slice(r, s0, s1)
            m = engine.metrics(rr)
            rows.append(dict(window=label, name=nm, **m))
            A(_row(nm, m))
        A("")

    # ---- 稳健性
    A("## 3. 稳健性（全窗口，同一成本/同一状态机）")
    A("")
    A("| 阈值 (低,高) | 年化 | 夏普 | 最大回撤 | 卡玛 | 切换次数 |")
    A("|---|---:|---:|---:|---:|---:|")
    for lo, hi in CFG["threshold"]["grid"]:
        s = engine.signal_frame(y, lo=lo, hi=hi)
        b = engine.backtest(navs, s["state"])
        m = engine.metrics(_slice(b["ret"], start))
        rows.append(dict(window="阈值网格", name=f"阈值{lo}/{hi}", **m))
        A(f"| ({lo:.1f}%, {hi:.1f}%) | {_pct(m['cagr'])} | {m['sharpe']:.2f} | "
          f"{_pct(m['mdd'])} | {m['calmar']:.2f} | {b['switches']} |")
    A("")
    A("| 波动目标 | 年化 | 夏普 | 最大回撤 | 缩放均值 | 调仓次数 |")
    A("|---|---:|---:|---:|---:|---:|")
    b0 = engine.backtest(navs, sig["state"], vol_target=False)
    m0 = engine.metrics(_slice(b0["ret"], start))
    rows.append(dict(window="波动目标", name="无缩放", **m0))
    A(f"| 无缩放（对照） | {_pct(m0['cagr'])} | {m0['sharpe']:.2f} | {_pct(m0['mdd'])} | "
      f"1.00 | {b0['switches']} |")
    for tv in CFG["vol_target"]["target_grid"]:
        b = engine.backtest(navs, sig["state"], vol_target=dict(CFG["vol_target"], target=tv))
        m = engine.metrics(_slice(b["ret"], start))
        rows.append(dict(window="波动目标", name=f"target{tv}", **m))
        A(f"| {tv * 100:.1f}% | {_pct(m['cagr'])} | {m['sharpe']:.2f} | {_pct(m['mdd'])} | "
          f"{b['scale'].mean():.2f} | {b['switches']} |")
    A("")
    A("| 分位 (低,高) | 观察窗 | 年化 | 夏普 | 最大回撤 |")
    A("|---|---:|---:|---:|---:|")
    for years in CFG["percentile"]["years_grid"]:
        for lo, hi in CFG["percentile"]["grid"]:
            s = engine.signal_frame(y, mode="percentile", lo=lo, hi=hi, years=years)
            m = engine.metrics(_slice(run(s), start))
            rows.append(dict(window="分位网格", name=f"分位{lo}/{hi}@{years}Y", **m))
            A(f"| ({lo:.0f}%, {hi:.0f}%) | {years} 年 | {_pct(m['cagr'])} | "
              f"{m['sharpe']:.2f} | {_pct(m['mdd'])} |")
    A("")
    A("| 再平衡 | 年化 | 夏普 | 最大回撤 | 切换次数 |")
    A("|---|---:|---:|---:|---:|")
    for freq in ("month_end", "week_end"):
        b = engine.backtest(navs, sig["state"], rebalance=freq)
        m = engine.metrics(_slice(b["ret"], start))
        rows.append(dict(window="调仓频率", name=freq, **m))
        A(f"| {freq} | {_pct(m['cagr'])} | {m['sharpe']:.2f} | {_pct(m['mdd'])} | {b['switches']} |")
    A("")
    A("| 单边成本 | 年化 | 夏普 | 最大回撤 |")
    A("|---|---:|---:|---:|")
    for f in CFG["fee_grid"]:
        m = engine.metrics(_slice(engine.backtest(navs, sig["state"], fee_side=f)["ret"], start))
        rows.append(dict(window="成本网格", name=f"fee{f}", **m))
        A(f"| {f * 1e4:.0f}bp | {_pct(m['cagr'])} | {m['sharpe']:.2f} | {_pct(m['mdd'])} |")
    A("")

    # ---- 滚动分段（非严格样本外）
    A("## 4. 滚动 3 年窗口（非严格样本外）")
    A("")
    A("> 阈值 2.0/3.0 由 317 组网格 + 邻域平台 + walk-forward 选参得到，套用到分段窗口只能算"
      "**分段稳健性检验**，不是严格样本外；网格最高夏普落在退化边界（LOW/HIGH 永不触发），"
      "故选参时保留了三档可用性约束。")
    A("")
    A("| 窗口 | V9 年化 | 夏普 | 长债持有 年化 | 夏普 | 短债持有 年化 | 夏普 |")
    A("|---|---:|---:|---:|---:|---:|---:|")
    step = 365
    win = CFG["roll_years"] * 365
    t0 = pd.Timestamp(start)
    t1 = net.index[-1]
    cur = t0
    while cur + pd.Timedelta(days=win) <= t1:
        end = cur + pd.Timedelta(days=win)
        a = engine.metrics(_slice(net, cur, end))
        b = engine.metrics(_slice(fixed({"003376": 1.0}), cur, end))
        c = engine.metrics(_slice(fixed({"002864": 1.0}), cur, end))
        rows.append(dict(window=f"{cur.date()}~{end.date()}", name="V9", **a))
        A(f"| {cur.date()}~{end.date()} | {_pct(a['cagr'])} | {a['sharpe']:.2f} | "
          f"{_pct(b['cagr'])} | {b['sharpe']:.2f} | {_pct(c['cagr'])} | {c['sharpe']:.2f} |")
        cur += pd.Timedelta(days=step)
    A("")

    # ---- 逐年
    A("## 5. 逐年（V9 vs 长债持有 vs 10Y 均值）")
    A("")
    A("| 年 | V9 | 长债持有 | 短债持有 | 10Y 均值 | 年末状态 |")
    A("|---|---:|---:|---:|---:|---|")
    yr9 = net.groupby(net.index.year).apply(lambda x: (1 + x).prod() - 1)
    yrl = sc[5][1].groupby(sc[5][1].index.year).apply(lambda x: (1 + x).prod() - 1)
    yrs = sc[6][1].groupby(sc[6][1].index.year).apply(lambda x: (1 + x).prod() - 1)
    for y in sorted(yr9.index):
        yv = float(yy[yy.index.year == y].mean())
        s_end = st_now[st_now.index.year == y].iloc[-1] if (st_now.index.year == y).any() else "—"
        A(f"| {y} | {_pct(float(yr9[y]) * 100)} | {_pct(float(yrl.get(y, 0)) * 100)} | "
          f"{_pct(float(yrs.get(y, 0)) * 100)} | {yv:.3f}% | {rule.STATE_CN.get(s_end, s_end)} |")
    A("")

    # ---- 结论（全部按实际数字生成，不写死结论）
    A("## 结论与边界")
    A("")
    m_l = engine.metrics(pick("买入持有长债"))
    m_s = engine.metrics(pick("买入持有短债"))
    m_b = engine.metrics(pick("基准 v0"))
    verdict = ("跑赢" if m_v9["sharpe"] > m_b["sharpe"] + 0.02 else
               ("打平" if abs(m_v9["sharpe"] - m_b["sharpe"]) <= 0.02 else "落后"))
    A(f"- **是否达到「抓大趋势」的目标**：V9 年化 {_pct(m_v9['cagr'])} / 回撤 "
      f"{_pct(m_v9['mdd'])} / 夏普 {m_v9['sharpe']:.2f} / 卡玛 {m_v9['calmar']:.2f}；"
      f"vs 家族基准 35/65 {m_b['sharpe']:.2f}（**{verdict}**）、"
      f"长债持有 {m_l['sharpe']:.2f}、短债持有 {m_s['sharpe']:.2f}。")
    m_novt = engine.metrics(net_novt)
    A(f"- **波动目标层（默认 1.0%）**：vs 无缩放，夏普 {m_novt['sharpe']:.2f} → "
      f"{m_v9['sharpe']:.2f}、回撤 {_pct(m_novt['mdd'])} → {_pct(m_v9['mdd'])}、年化 "
      f"{_pct(m_novt['cagr'])} → {_pct(m_v9['cagr'])}、缩放均值 {bt['scale'].mean():.2f}；"
      f"平均长债 {bt['w']['003376'].mean() * 100:.0f}%。")
    A(f"- **直接映射 vs 带记忆**：默认（三档都可用）{m_v9['sharpe']:.2f} / "
      f"{engine.metrics(net_hyst)['sharpe']:.2f}（带记忆，MID 不可达），默认切换 "
      f"{bt['switches']} 次；费前 {engine.metrics(net_g0)['sharpe']:.2f} —— "
      "费前只是信号体检，不能当可实现收益引用。")
    A(f"- **分位口径**：{engine.metrics(net_pct)['sharpe']:.2f} / "
      f"{_pct(engine.metrics(net_pct)['cagr'])}，与绝对阈值口径的差异来自"
      "「相对历史高低」和「绝对档位」不是同一件事；两者分歧时以绝对阈值口径为准，分位只作诊断。")
    A("- **边界**：① 10Y 只是大方向，不是保本信号；信用违约、流动性、基金杠杆/久期漂移"
      "都会改变实际结果；② 中债综合财富/国债及政金债指数在仓库里没有可复现净值，"
      "回测用代表基金合成**代理**，不是指数本身；③ 2016 年段受 `003376` 2016-09-26 成立限制，"
      "实际起点晚于 2016-01；④ 2.0%/3.0% 是网格选参结果（旧 1.8%/2.5%、上一版 3.5% 与 3.2% 只作对照），"
      "已做邻域平台与 walk-forward，但仍不构成严格样本外；⑤ 波动目标是路径依赖缩放，"
      "波动飙升后若市场快速反弹会被动低配、跑输无缩放口径；1.0% 目标也是样本内选的，"
      "虽 1.0~1.6 呈平台；⑥ 场外 A 类申赎费按统一单边成本近似，未逐笔按持有期阶梯计费"
      "（v8 已有真实阶梯引擎，需要时可复用）。")
    A("")

    os.makedirs(OUT_DIR, exist_ok=True)
    with open(OUT_MD, "w", encoding="utf-8") as f:
        f.write("\n".join(lines) + "\n")
    with open(OUT_JSONL, "w", encoding="utf-8") as f:
        for r in rows:
            f.write(json.dumps(r, ensure_ascii=False) + "\n")
    print(f"[ok] {OUT_MD}")
    print(f"[ok] {OUT_JSONL}")
    print(f"  V9 {m_v9['cagr']:+.2f}% / {m_v9['mdd']:+.2f}% / 夏普 {m_v9['sharpe']:.2f} "
          f"vs 基准 35/65 {m_b['sharpe']:.2f} / 长债 {m_l['sharpe']:.2f} / 短债 {m_s['sharpe']:.2f}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
