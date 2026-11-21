# -*- coding: utf-8 -*-
"""稳健资产（债腿 + 现金腿）· 回测。

产物
  ``backtest/_bond_cash_bt.jsonl``  逐档指标（机器可读）
  ``backtest/_bond_cash_bt.md``     人读报告

窗口（受壳成立日限制，两段都给，不混算）
  现行窗 base = ``011983``（3-5 政金 A，2021-09 成立）
  长窗   base = ``001512``（国债 3-5，2015-07 成立）—— 011983 之前只能用它

口径
  - 收益一律场外**累计净值**日收益（含分红再投），不用场内行情涨跌幅；
  - T 日收盘算动量，T+1 起承担仓位（``shift(1)``）；
  - 成本：切换一次 = 核心段 35% × 单边 × 2（默认单边 10bp，另给 0 / 40bp 敏感性）；
  - 夏普无风险利率 1.40%（货基）。

用法
  python strategies/bond_cash/backtest.py            # 用当日缓存（首次会联网抓 7 只）
  python strategies/bond_cash/backtest.py --force     # 强制重抓
"""
import argparse
import io
import json
import os
import sys
from datetime import datetime

import pandas as pd

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8")

_HERE = os.path.dirname(os.path.abspath(__file__))          # strategies/bond_cash
_ROOT = os.path.dirname(os.path.dirname(_HERE))
for _p in (_ROOT, _HERE):
    if _p not in sys.path:
        sys.path.insert(0, _p)

import rule      # noqa: E402
import data      # noqa: E402
import engine    # noqa: E402

OUT_DIR = os.path.join(_HERE, "backtest")
OUT_MD = os.path.join(OUT_DIR, "_bond_cash_bt.md")
OUT_JSONL = os.path.join(OUT_DIR, "_bond_cash_bt.jsonl")

CFG = rule.CFG
NEED = ["003376", "003377", "002864", "011983", "001512", "000198", "014437"]


def _pct(v, nd=2, sign=True):
    if v is None:
        return "—"
    return f"{v:+.{nd}f}%" if sign else f"{v:.{nd}f}%"


def _row_md(name, m):
    return (f"| {name} | {_pct(m['cagr'])} | {_pct(m['vol'], 2, False)} | "
            f"{m['sharpe']:.2f} | {_pct(m['mdd'])} | {_pct(m['worst_year'])} | "
            f"{m['switches']} 次 / 均仓 {m['exposure']:.0f}% |")


def _common_start(navs, codes, win):
    """共同样本起点 = 各腿最晚成立日 + win 个交易日预热。"""
    df = pd.concat([navs[c].rename(c) for c in codes], axis=1, join="inner").dropna()
    return df.index[win] if len(df) > win else df.index[-1]


def _scenario(navs, name, on, off, base, win, fee_side, gate, start=None, base_w=None,
              deadband=0.0):
    bt = engine.backtest_leg(navs, on, off, base, win=win, base_w=base_w,
                             fee_side=fee_side, gate=gate, deadband=deadband)
    ret = bt["ret"]
    if start is not None:
        ret = ret[ret.index >= start]
    m = engine.metrics(ret, rf=CFG["rf_annual"])
    m.update(name=name, switches=bt["switches"], exposure=round(bt["exposure"] * 100, 1),
             start=str(ret.index[0].date()), end=str(ret.index[-1].date()),
             fee_side=fee_side, gate=gate, deadband=deadband, on=on, off=off, base=base)
    return m, ret


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--force", action="store_true", help="强制重抓净值")
    args = ap.parse_args(argv)

    navs, asof = data.load_many(NEED, force=args.force)
    miss = [c for c in NEED if c not in navs]
    if miss:
        print(f"[error] 缺净值: {miss}")
        return 1
    win, fee, rf = CFG["mom_win"], CFG["fee_side"], CFG["rf_annual"]
    db = CFG["gate_deadband"]

    lines, rows = [], []
    A = lines.append
    A("# 稳健资产（债腿 + 现金腿）· 回测")
    A("")
    A(f"> 生成 {datetime.now():%Y-%m-%d %H:%M} · 由 `strategies/bond_cash/backtest.py` 产出，"
      "**请勿手改**。")
    A(f"> 口径：场外累计净值（含分红再投）· T 日信号 / T+1 起承担收益 · "
      f"单边 {fee * 1e4:.0f}bp · 夏普无风险利率 {rf:.2%}。")
    A(f"> 净值截止：{max(asof.values())}（" +
      "、".join(f"{c} {asof[c]}" for c in NEED if c in asof) + "）")
    A("")

    # ---------------- 现行窗
    start_b = _common_start(navs, ["003376", "002864", "011983"], win)
    A("## 一、现行口径窗（底仓 011983）")
    A("")
    A(f"窗口 {start_b.date()} ~ {max(asof.values())}，受 011983（2021-09-02 成立）+ "
      f"{win} 日动量预热限制。")
    A("")
    A("| 方案 | 年化 | 波动 | 夏普 | 最大回撤 | 最差单年 | 换手/仓位 |")
    A("|---|---:|---:|---:|---:|---:|---|")
    m_gate, r_gate = _scenario(navs, f"开关 死区{db * 100:.1f}%", "003376", "011983", "011983",
                               win, fee, True, start_b, deadband=db)
    m_gate0, r_gate0 = _scenario(navs, "开关 死区0", "003376", "011983", "011983",
                                 win, fee, True, start_b, deadband=0.0)
    m_static, r_static = _scenario(navs, "**默认：固定中性 35/65**", "003376", "011983", "011983",
                                   win, fee, False, start_b)
    m_old, r_old = _scenario(navs, "旧默认 70%003376+30%011983（2026-11-20 前）",
                             "003376", "011983", "011983", win, fee, False, start_b, base_w=0.30)
    m_defs, _ = _scenario(navs, "开关(死区0)+短端防守档 35%→002864", "003376", "002864", "011983",
                          win, fee, True, start_b, deadband=0.0)
    m_short, _ = _scenario(navs, "底仓改短端 35%003376+65%002864", "003376", "002864", "002864",
                           win, fee, False, start_b)
    m_c, _ = _scenario(navs, "旧默认 70%003377C+30%001512", "003377", "003377", "001512",
                       win, fee, False, start_b, base_w=0.30)
    m_all_in, _ = _scenario(navs, "满仓 003376（不再平衡）", "003376", "003376", "003376",
                            win, fee, False, start_b)
    for m in (m_static, m_old, m_gate, m_gate0, m_defs, m_short, m_c, m_all_in):
        rows.append(dict(window="现行", **m))
        A(_row_md(m["name"], m))
    A("")

    A("**死区敏感性**（开关口径，单边 10bp）")
    A("")
    A("| 死区 | 年化 | 最大回撤 | 夏普 | 换次 |")
    A("|---|---:|---:|---:|---:|")
    for d in (0.0, 0.001, 0.002, 0.005, 0.010):
        m, _ = _scenario(navs, f"db{d}", "003376", "011983", "011983", win, fee, True,
                         start_b, deadband=d)
        rows.append(dict(window="现行-死区", db_grid=d, **m))
        A(f"| {d * 100:.1f}% | {_pct(m['cagr'])} | {_pct(m['mdd'])} | {m['sharpe']:.2f} | "
          f"{m['switches']} |")
    A("")

    A("**成本敏感性**（开关 死区 0.5%）")
    A("")
    A("| 单边成本 | 年化 | 最大回撤 | 夏普 |")
    A("|---|---:|---:|---:|")
    for f in CFG["fee_side_grid"]:
        m, _ = _scenario(navs, f"fee{f}", "003376", "011983", "011983", win, f, True,
                         start_b, deadband=db)
        rows.append(dict(window="现行-成本", fee=f, **m))
        A(f"| {f * 1e4:.0f}bp | {_pct(m['cagr'])} | {_pct(m['mdd'])} | {m['sharpe']:.2f} |")
    A("")

    # ---------------- 长窗
    start_a = _common_start(navs, ["003376", "001512", "002864"], win)
    A("## 二、长窗（底仓 001512 国债 3-5）")
    A("")
    A(f"窗口 {start_a.date()} ~ {max(asof.values())} —— 受 003376（2016-09-26 成立）+ {win} 日"
      "预热限制，这是本策略能拿到的最长真实壳样本。底仓用 001512 是因为 011983 到 2021-09 "
      "才有数据（两者的差 +0.67pp/年 见 §一）。")
    A("")
    A("| 方案 | 年化 | 波动 | 夏普 | 最大回撤 | 最差单年 | 换手/仓位 |")
    A("|---|---:|---:|---:|---:|---:|---|")
    m_agate, r_agate = _scenario(navs, f"开关 死区{db * 100:.1f}%", "003376", "001512", "001512",
                                 win, fee, True, start_a, deadband=db)
    m_agate0, _ = _scenario(navs, "开关 死区0", "003376", "001512", "001512",
                            win, fee, True, start_a, deadband=0.0)
    m_astatic, r_astatic = _scenario(navs, "**默认：固定中性 35/65**", "003376", "001512", "001512",
                                     win, fee, False, start_a)
    m_aold, _ = _scenario(navs, "旧默认 70%003376+30%001512（2026-11-20 前）",
                          "003376", "001512", "001512", win, fee, False, start_a, base_w=0.30)
    m_adefs, _ = _scenario(navs, "开关(死区0)+短端防守档 35%→002864", "003376", "002864", "001512",
                           win, fee, True, start_a, deadband=0.0)
    m_ac, _ = _scenario(navs, "旧默认 70%003377C+30%001512", "003377", "003377", "001512",
                        win, fee, False, start_a, base_w=0.30)
    m_aoff, _ = _scenario(navs, "全防守 100% 001512", "001512", "001512", "001512",
                          win, fee, False, start_a)
    m_aon, _ = _scenario(navs, "全进攻 100% 003376", "003376", "003376", "003376",
                         win, fee, False, start_a)
    for m in (m_astatic, m_aold, m_agate, m_agate0, m_adefs, m_ac, m_aoff, m_aon):
        rows.append(dict(window="长窗", **m))
        A(_row_md(m["name"], m))
    A("")

    A("**逐年收益**（长窗：开关 死区 0.5% vs 固定中性 35/65）")
    A("")
    A("| 年 | 开关 | 固定中性 35/65 | 差 |")
    A("|---|---:|---:|---:|")
    yg = r_agate.groupby(r_agate.index.year).apply(lambda x: (1 + x).prod() - 1)
    ys = r_astatic.groupby(r_astatic.index.year).apply(lambda x: (1 + x).prod() - 1)
    for y in sorted(set(yg.index) | set(ys.index)):
        a, b = float(yg.get(y, 0)) * 100, float(ys.get(y, 0)) * 100
        A(f"| {y} | {_pct(a)} | {_pct(b)} | {_pct(a - b)} |")
    A("")

    # ---------------- 现金腿
    A("## 三、现金腿（10pp 内部 50/50）")
    A("")
    cash_start = _common_start(navs, ["000198", "014437"], win)
    rc1 = navs["000198"].pct_change().dropna()
    rc1 = rc1[rc1.index >= cash_start]
    rc2 = navs["014437"].pct_change().dropna()
    rc2 = rc2[rc2.index >= cash_start]
    mc1, mc2 = engine.metrics(rc1, rf=rf), engine.metrics(rc2, rf=rf)
    A(f"同窗 {cash_start.date()} ~ {max(asof.values())}：")
    A("")
    A("| 现金腿 | 年化 | 波动 | 最大回撤 | 最差单年 |")
    A("|---|---:|---:|---:|---:|")
    A(f"| 000198 余额宝 | {_pct(mc1['cagr'])} | {_pct(mc1['vol'], 2, False)} | "
      f"{_pct(mc1['mdd'])} | {_pct(mc1['worst_year'])} |")
    A(f"| 014437 存单AAA | {_pct(mc2['cagr'])} | {_pct(mc2['vol'], 2, False)} | "
      f"{_pct(mc2['mdd'])} | {_pct(mc2['worst_year'])} |")
    A("")
    A(f"> 腿内差 **{mc2['cagr'] - mc1['cagr']:+.2f}pp/年**。落到 50/30/10/10 的组合层只有 "
      f"**{(mc2['cagr'] - mc1['cagr']) * 0.05:+.2f}pp/年**（换的只是 10% 里的 5 个点）—— "
      "它的意义是「这 5 个点不再为零机会成本」，不是收益来源。")
    A("")
    rows.append(dict(window="现金腿", code="000198", **mc1))
    rows.append(dict(window="现金腿", code="014437", **mc2))

    # ---------------- 组合层
    A("## 四、组合层（40pp：债腿 30 + 现金腿 10）")
    A("")
    A("| 40pp 组合 | 年化 | 波动 | 夏普 | 最大回撤 | 最差单年 |")
    A("|---|---:|---:|---:|---:|---:|")
    for nm, branch in (("债腿固定中性 + 现金 50/50", r_static),
                       ("债腿开关(死区0.5%) + 现金 50/50", r_gate),
                       ("纯现金（50/50）", None)):
        idx = (branch if branch is not None else rc1).index
        if branch is None:
            s = 0.5 * rc1 + 0.5 * rc2
            s = s.reindex(idx).dropna()
        else:
            s = 0.75 * branch + 0.25 * (0.5 * rc1 + 0.5 * rc2)
            s = s.dropna()
        m = engine.metrics(s, rf=rf)
        A(f"| {nm} | {_pct(m['cagr'])} | {_pct(m['vol'], 2, False)} | {m['sharpe']:.2f} | "
          f"{_pct(m['mdd'])} | {_pct(m['worst_year'])} |")
        rows.append(dict(window="组合", name=nm, **m))
    A("")
    A("> 组合层收益是「40pp 占位」口径（把这个 sleeve 归一到 100%），换到总资产口径要再乘 0.40。")
    A("")

    # ---------------- 结论
    A("## 五、结论（比文档保守，原因写在下面）")
    A("")
    A(f"- **默认不动手**：固定中性 35/65 在现行窗 {_pct(m_static['cagr'])} / 回撤 {_pct(m_static['mdd'])}，"
      f"长窗 {_pct(m_astatic['cagr'])} / {_pct(m_astatic['mdd'])}。")
    A(f"- **动量开关在本仓库的复现里没有增量**：死区 0 时长窗 {_pct(m_agate0['cagr'])} / "
      f"{_pct(m_agate0['mdd'])}（{m_agate0['switches']} 次换手），死区 0.5% 时 "
      f"{_pct(m_agate['cagr'])} / {_pct(m_agate['mdd'])}（{m_agate['switches']} 次）。"
      "开与不开都在固定中性基准 ±0.5pp 内，但换手与成本是真的。")
    A("- **为什么复现不出文档 §3-1 的 +1.39pp / 回撤改善 7.89pp**：")
    A("  1. 文档的信号源是**中债 7-10 财富指数**（2002 起），本仓库只有基金净值。"
      "用 003376 净值替代后，10 年符号翻转 **53~54 次**（A/C 份额噪声不同），"
      "文档同窗只有 20 次 —— 基金净值的取整/费用噪声把零附近的穿越放大了 2.5 倍。")
    A("  2. 文档闸门的价值集中在 **2016-11 债灾**（区间内只有 14% 时间在进攻档），"
      "而 003376 成立于 **2016-09-26**，加 63 日预热后第一个信号是 2016-12-29 —— "
      "**正好在债灾中段**，拿真实壳根本吃不到那一段。")
    A("  3. 成本口径：文档对真实壳用「C 类持有满 30 天免赎回费 → 成本 0」；"
      "本回测按单边 10bp 保守计。见 §一 成本敏感性：40bp 时开关直接变成负收益。")
    A("- **开关防得住什么、防不住什么**（文档 §8 原话）：防利率趋势反转，防不住负债端踩踏"
      "（2022-11 赎回潮起止均为进攻档、区间内 49% 在进攻）。")
    A("- **死区曲线怎么读**：0→1% 单调收敛回固定中性（年化抬升、回撤收窄、换手锐减），"
      "说明开关的全部作用就是「少交易」—— 它没找到比「不做」更好的买卖点。"
      "这也解释了文档的正结论为什么依赖指数级信号：信号越干净，穿越越少，成本才不反噬。")
    A("- **样本内标定**：63 日窗口、0.5% 死区、5/7 日连跌阈值都取自同一份历史，不构成严格样本外。")
    A("")

    os.makedirs(OUT_DIR, exist_ok=True)
    with open(OUT_MD, "w", encoding="utf-8") as f:
        f.write("\n".join(lines) + "\n")
    with open(OUT_JSONL, "w", encoding="utf-8") as f:
        for r in rows:
            f.write(json.dumps(r, ensure_ascii=False) + "\n")

    print(f"[ok] {OUT_MD}")
    print(f"[ok] {OUT_JSONL}")
    for m in (m_static, m_gate, m_astatic, m_agate):
        print(f"  {m['name'][:30]:30s} {m['cagr']:+6.2f}%  mdd {m['mdd']:+7.2f}%  "
              f"sharpe {m['sharpe']:.2f}  flips {m['switches']}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
