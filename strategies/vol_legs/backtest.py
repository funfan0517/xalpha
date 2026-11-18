# -*- coding: utf-8 -*-
"""波动资产操作规则 v1.4 · 组合层回测。

同一份数据、同一个执行框架(engine.py, 与每日操作共用), 只换「是否执行规则」, 隔离规则本身的贡献:

  strategy : 按 §2~§7 全部规则(阶梯 / 趋势闸门 / 保险丝 / 季度再平衡)
  seed     : 同上, 但起点就按带满仓(已持有 2.5/2.5/10/10/2.5/2.5/10 的人)
  open_us  : 同上, 但美股 ERP 闸门不设限(null) —— 隔离「§6 当前读数(erp<0 → 暂停新增)」
             对美股腿的锁定效应
  static   : 七腿恒定 2.5/2.5/10/10/2.5/2.5/10, 每季回锚 —— 「仓位带是主锚」的朴素实现
  hold     : 首日按带建仓后完全不动
  equal    : 七腿等权(各 5.71pp), 每季回锚

价格序列只有一套: **该市场的真实指数** —— 沪深300 `SH000300` / A500 `SH000510` /
科创50 `SH000688` / 纳指 `.NDX` / 标普 `.INX` / 恒生科技 `HKHSTECH` / 黄金 `518880`(实物 ETF)。
没有溢价、跟踪误差与汇率污染, 历史比 ETF/场外基金长 3~10 年; 除黄金外都是**价格指数**
(不含分红再投), 绝对收益要按 A股 -2.0~2.5pp/年、美股 -1.3pp/年 折算(执行仍是场外 A 类)。

单腿专项: 中证A500 / 沪深300 / 纳指100 / 标普500 的「阶梯 vs 首日一次性满仓」。

用法:
  python strategies/vol_legs/backtest.py            # 全量
  python strategies/vol_legs/backtest.py --quick    # 跳过闸门敏感性扫描
输出:
  strategies/vol_legs/backtest/_vol_legs_bt.md / .json
"""
import argparse
import io
import json
import os
import sys
from datetime import datetime

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8")

_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

import rule  # noqa: E402
import data  # noqa: E402
import engine  # noqa: E402

_DIR = os.path.dirname(os.path.abspath(__file__))
_BT_DIR = os.path.join(_DIR, "backtest")
_OUT_MD = os.path.join(_BT_DIR, "_vol_legs_bt.md")
_OUT_JSON = os.path.join(_BT_DIR, "_vol_legs_bt.json")

MODES = [
    dict(key="strategy", name="**策略(§2~§7 规则 · 空仓起步)**", mode="strategy"),
    dict(key="seed", name="策略(带内满仓起步)", mode="strategy", seed=True),
    dict(key="open_us", name="策略(美股闸门不设限)", mode="strategy",
         gates={"ndx_erp": None, "spx_erp": None}),
    dict(key="static", name="静态带(季度回锚)", mode="static"),
    dict(key="hold", name="买入持有(建仓后不动)", mode="hold"),
    dict(key="equal", name="七腿等权(季度回锚)", mode="equal"),
]
SEED = {leg: rule.LEGS[leg]["band"] for leg in rule.LEG_ORDER}
SINGLE = ["a500", "hs300", "ndx", "spx"]


# ---------------------------------------------------------------- 工具
def _pct(x, nd=1):
    return f"{x * 100:.{nd}f}%"


def ready_start(ind_by_leg, legs):
    """所有腿「指标就绪」的第一天 —— 公平起点。"""
    common = None
    for leg in legs:
        idx = ind_by_leg[leg][ind_by_leg[leg]["ready"]].index
        if not len(idx):
            raise ValueError(f"{leg} 无就绪区间")
        common = idx[0] if common is None else max(common, idx[0])
    return common


def self_test(res):
    """记账自检: 现金 + 持仓 = 净值; 现金不为负。"""
    bad = 0
    for r in res["records"]:
        if abs(r["cash"] + sum(r["pos"].values()) - r["nav"]) > 1e-6:
            bad += 1
        if r["cash"] < -1e-9:
            bad += 1
    if bad:
        raise AssertionError(f"记账自检失败: {bad} 处不平")
    return True


def attribution(res, px):
    """按日归因: 各腿贡献(pp) = Σ 昨仓 × 当日涨幅。"""
    out = {leg: 0.0 for leg in res["pos"]}
    recs = res["records"]
    for i in range(1, len(recs)):
        prev, cur = recs[i - 1], recs[i]
        for leg in out:
            s = px[leg]
            if cur["date"] not in s.index:
                continue
            hist = s[s.index <= prev["date"]]
            if not len(hist):
                continue
            out[leg] += prev["pos"][leg] * (float(s.loc[cur["date"]]) / float(hist.iloc[-1]) - 1.0)
    return out


def trade_stats(res):
    per = {leg: dict(buys=0, sells=0, fee=0.0, amt=0.0) for leg in res["pos"]}
    for t in res["trades"]:
        p = per[t["leg"]]
        p["buys" if t["kind"] == "buy" else "sells"] += 1
        p["fee"] += t["fee_pp"]
        p["amt"] += t["amount_pp"]
    years = max(res["nav"].shape[0] / 252.0, 1e-9)
    return per, dict(fee_pp=sum(p["fee"] for p in per.values()),
                     amount_pp=sum(p["amt"] for p in per.values()),
                     per_year=sum(p["amt"] for p in per.values()) / years, years=years)


def _row(name, m):
    return (f"| {name} | {_pct(m['cum'], 0)} | {_pct(m['ann'], 2)} | {_pct(m['vol'], 1)} | "
            f"{_pct(m['mdd'], 1)} | {m['sharpe']:.2f} |")


def _hold_avg(res):
    recs = res["records"]
    return sum(r["invested"] for r in recs) / len(recs)


def run_block(ind, gates, fomc, since):
    """跑一组对比 -> (runs, perfs)。"""
    runs, perfs = {}, {}
    for spec in MODES:
        g = dict(gates, **(spec.get("gates") or {}))
        res = engine.run(ind, g, fomc, mode=spec["mode"], since=since,
                         **({"init": dict(SEED)} if spec.get("seed") else {}))
        self_test(res)
        runs[spec["key"]] = res
        perfs[spec["key"]] = engine.metrics(res["nav"])
    return runs, perfs


def single_leg(key, ind, gates, fomc):
    """单腿全周期: 该腿的「阶梯 vs 首日一次性满仓」(该腿自己的 ERP 闸门置 null, 隔离阶梯机制)。"""
    start = ind[key][ind[key]["ready"]].index[0]
    g = dict(gates, **{f"{key}_erp": None})
    lad = engine.run({key: ind[key]}, g, fomc, since=start, legs=[key])
    hold = engine.run({key: ind[key]}, g, fomc, since=start, mode="hold", legs=[key])
    self_test(lad)
    return dict(key=key, ladder=lad, hold=hold,
                m_ladder=engine.metrics(lad["nav"]), m_hold=engine.metrics(hold["nav"]))


# ---------------------------------------------------------------- 主流程
def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--quick", action="store_true", help="跳过闸门敏感性扫描")
    args = ap.parse_args()

    ind = data.indicators_all()
    gates, fomc = data.gates(ind["gold"].index)
    since = ready_start(ind, rule.LEG_ORDER)
    runs, perfs = run_block(ind, gates, fomc, since)
    px = {leg: ind[leg]["close"] for leg in rule.LEG_ORDER}

    singles = {k: single_leg(k, ind, gates, fomc) for k in SINGLE}

    sweep, sweep3 = {}, {}
    if not args.quick:
        for tag, val in (("on", "on"), ("off", "off"), ("null", None)):
            g = dict(gates, a500_erp=val)
            sweep[tag] = engine.metrics(engine.run(ind, g, fomc, since=since)["nav"])
            g3 = dict(gates, hs300_erp=val)
            sweep3[tag] = engine.metrics(engine.run(ind, g3, fomc, since=since)["nav"])

    res = runs["strategy"]
    nav0 = float(res["nav"].iloc[0])
    attrib = attribution(res, px)
    per, tstats = trade_stats(res)
    n_days = len(res["nav"])
    years = n_days / 252.0
    erp = gates["_erp"]
    erp3 = gates["_hs300_erp"]
    us = gates["_us_erp"]
    vi = gates["_inputs"]
    label = data.mode_label()

    L = []
    A = L.append
    A(f"# 波动资产操作规则 v1.4 · 组合层回测({label})")
    A("")
    A(f"> 生成 {datetime.now():%Y-%m-%d %H:%M} · 价格口径 = **{label}** · 样本 "
      f"**{res['nav'].index[0]:%Y-%m-%d} ~ {res['nav'].index[-1]:%Y-%m-%d}**"
      f"({years:.1f} 年, {n_days} 个交易日)")
    band_txt = " / ".join(f"{rule.LEGS[k]['name']} {rule.LEGS[k]['band']:g}" for k in rule.LEG_ORDER)
    A(f"> 管辖额度 {rule.SLEEVE_PP:.0f}pp({band_txt}), 未投部分按货基 "
      f"{rule.CASH_ANNUAL:.2%} 计息; 成本 申购 {rule.FEE_BUY:.2%} / 赎回 {rule.FEE_SELL:.1%}"
      f"(持有 7 天内 {rule.FEE_SELL_7D:.1%})。机械规则输出, 非投资建议。")
    A("")
    A(f"## 〇、口径：{label}")
    A("")
    A("| 腿 | 指数 | 最新收盘 | 序列起止 | 分红处理 |")
    A("|---|---|---:|---|---|")
    for leg in rule.LEG_ORDER:
        cfg = rule.LEGS[leg]
        s = px[leg]
        _src, code, name = data.IDX[leg]
        div = ("无分红" if leg == "gold" else "**价格指数(不含分红)**")
        A(f"| {cfg['name']} | `{code}` {name} | {float(s.iloc[-1]):.3f} | "
          f"{s.index[0]:%Y-%m-%d} ~ {s.index[-1]:%Y-%m-%d} | {div} |")
    A("")
    exec_txt = " / ".join("`" + rule.LEGS[k]["exec"] + "`" for k in rule.LEG_ORDER if k != "gold")
    A("> **信号与回测都用该市场的真实指数**：没有溢价、没有跟踪误差、没有汇率污染"
      "（美股腿是美元口径），历史比 ETF/场外基金长 3~10 年。执行: 六条指数腿走场外 A 类基金"
      f"（{exec_txt}），黄金走场内 `518880`；指数只用于「信号 + 回测」。")
    A("> ⚠ 除黄金外都是**价格指数**：A股腿低估约 2.0~2.5pp/年（分红再投）、美股约 1.3pp/年。"
      "同腿内一致，所以**规则优劣与跨腿排序不受影响**，但绝对收益要按这条折算。")
    A("")

    A(f"## 一、六档对比({label})")
    A("")
    A("| 组合 | 累计 | 年化 | 年化波动 | 最大回撤 | 夏普 | 平均投入 | 单位投入年化 |")
    A("|---|---:|---:|---:|---:|---:|---:|---:|")
    for spec in MODES:
        m = perfs[spec["key"]]
        expo = _hold_avg(runs[spec["key"]]) / rule.SLEEVE_PP
        A(_row(spec["name"], m) + f" {_hold_avg(runs[spec['key']]):.1f}pp | "
          f"{m['ann'] / expo if expo > 1e-9 else 0:.2%} |")
    A("")
    A("| 组合 | 交易笔数 | 累计成交额 | 年换手 | 累计手续费 |")
    A("|---|---:|---:|---:|---:|")
    for spec in MODES:
        r = runs[spec["key"]]
        _, ts = trade_stats(r)
        A(f"| {spec['name'].replace('**', '')} | {len(r['trades'])} | "
          f"{ts['amount_pp']:.0f}pp | {ts['per_year']:.0f}pp/年 | {ts['fee_pp']:.2f}pp |")
    A("")
    d_ann = perfs["strategy"]["ann"] - perfs["static"]["ann"]
    A(f"> **空仓起步** vs 静态带: 年化 **{d_ann:+.2%}**、回撤 "
      f"{perfs['strategy']['mdd'] - perfs['static']['mdd']:+.2%}、夏普 "
      f"{perfs['strategy']['sharpe']:.2f} vs {perfs['static']['sharpe']:.2f}。策略平均只投入 "
      f"{_hold_avg(res):.1f}pp(额度 {rule.SLEEVE_PP:.0f}pp), 差额停在货基 —— 空仓起步的直接后果。")
    seed_txt = "/".join(f"{rule.LEGS[k]['band']:g}" for k in rule.LEG_ORDER)
    A(f"> **带内满仓起步**(起点即 {seed_txt})vs 静态带: 年化 "
      f"{perfs['seed']['ann'] - perfs['static']['ann']:+.2%}、回撤 "
      f"{perfs['seed']['mdd'] - perfs['static']['mdd']:+.2%}、夏普 {perfs['seed']['sharpe']:.2f} vs "
      f"{perfs['static']['sharpe']:.2f} —— 这一行才回答「已经按带持有了, 规则还能加多少」。")
    A(f"> **美股闸门不设限** vs **按当前读数**: 年化差 "
      f"{perfs['open_us']['ann'] - perfs['strategy']['ann']:+.2%} —— 文档 §6 当前读数把两条美股腿"
      f"锁在「只减不加」, 这个差额就是该读数的机会成本(反过来是它省下的风险)。")
    A("")

    A(f"## 二、分腿贡献(策略口径 · {label})")
    A("")
    A("| 腿 | 仓位带 | 当前仓位 | 买入/卖出 | 贡献(pp) | 贡献占比 | 平均仓位 |")
    A("|---|---:|---:|---:|---:|---:|---:|")
    total_attr = sum(attrib.values())
    avg_pos = {leg: 0.0 for leg in rule.LEG_ORDER}
    for r in res["records"]:
        for leg in rule.LEG_ORDER:
            avg_pos[leg] += r["pos"][leg]
    for leg in rule.LEG_ORDER:
        avg_pos[leg] /= len(res["records"])
        share = attrib[leg] / total_attr if abs(total_attr) > 1e-9 else 0.0
        A(f"| {rule.LEGS[leg]['name']} | {rule.LEGS[leg]['band']:g}pp | "
          f"{res['pos'][leg]:.1f}pp | {per[leg]['buys']}/{per[leg]['sells']} | "
          f"{attrib[leg]:+.2f} | {share:+.0%} | {avg_pos[leg]:.1f}pp |")
    A(f"| 货基(未投) | — | {res['cash']:.1f}pp | — | "
      f"{(res['nav'].iloc[-1] - nav0) - total_attr:+.2f} | — | "
      f"{rule.SLEEVE_PP - sum(avg_pos.values()):.1f}pp |")
    A("")
    A(f"> 累计手续费 {tstats['fee_pp']:.2f}pp(占起始净值 {tstats['fee_pp'] / nav0:.2%}); "
      f"成交额 {tstats['amount_pp']:.0f}pp / {years:.1f} 年 = {tstats['per_year']:.0f}pp/年。")
    if abs(avg_pos["ndx"]) < 0.05 and abs(avg_pos["spx"]) < 0.05:
        A("> 美股两腿平均仓位为 0 = 文档 §6「当前状态读数」的直接结果(erp 为负 → 暂停新增), "
          "不是回测缺陷; 想看阶梯潜力请看第一节的「美股闸门不设限」行。")
    A("")

    A(f"## 三、单腿全周期(阶梯 vs 一次性建仓 · {label})")
    A("")
    A("> 每条腿用**该市场的真实指数**全历史(黄金腿用 `518880` 实物 ETF); "
      "该腿的 ERP 闸门置 null(隔离阶梯机制)。执行仍走标题里那只场外基金, "
      "指数不含分红再投 —— 阶梯 vs 满仓的**相对关系**可直接读, 绝对收益按 §〇 折算。")
    A("")
    for key in SINGLE:
        s = singles[key]
        lad = s["ladder"]
        src_txt = f"`{data.IDX[key][1]}` 指数 → 执行 `{rule.LEGS[key]['exec']}`"
        A(f"### {rule.LEGS[key]['name']}（{src_txt}）")
        A("")
        A(f"样本 {lad['nav'].index[0]:%Y-%m-%d} ~ {lad['nav'].index[-1]:%Y-%m-%d}"
          f"({len(lad['nav']) / 252.0:.1f} 年)。对比口径: 同样 {rule.LEGS[key]['band']:g}pp 额度, "
          f"一个由阶梯(dd {rule.CFG[key]['ladder_dd']}%)填, 一个首日一次性满仓。")
        A("")
        A("| 组合 | 累计 | 年化 | 年化波动 | 最大回撤 | 夏普 |")
        A("|---|---:|---:|---:|---:|---:|")
        A(_row(f"**{rule.LEGS[key]['name']} 回撤阶梯**", s["m_ladder"]))
        A(_row("首日一次性满仓", s["m_hold"]))
        A("")
        A(f"- 阶梯版平均仓位 {_hold_avg(lad):.1f}pp / 额度 {rule.LEGS[key]['band']:g}pp, "
          f"共 {len(lad['trades'])} 笔成交。")
        if lad["trades"]:
            A("")
            A("| 触发日 | 动作 | 规则 | 金额(pp) | 当时关键值 |")
            A("|---|---|---|---:|---|")
            for t in lad["trades"][:20]:
                A(f"| {t['date']:%Y-%m-%d} | {'买入' if t['kind'] == 'buy' else '卖出'} | "
                  f"{t['rule']} | {t['amount_pp']:.2f} | {t['detail']} |")
            if len(lad["trades"]) > 20:
                A(f"| … | 另有 {len(lad['trades']) - 20} 笔, 详见 _vol_legs_bt.json | | | |")
        A("")

    if sweep:
        A(f"## 四、ERP 启动闸门敏感性({label})")
        A("")
        A("### 中证A500（分位恒 null）")
        A("")
        A("样本内 A500 的 PE 历史自 2024-09 起(月度 "
          f"{erp['n_months']} 个点), 不到 3 年门槛 → 分位 = null、闸门不执行。下表把闸门强行钉成 "
          "on / off / null, 说明当前它等价于「不设限」:")
        A("")
        A("| ERP 闸门 | 累计 | 年化 | 年化波动 | 最大回撤 | 夏普 |")
        A("|---|---:|---:|---:|---:|---:|")
        for tag, m in sweep.items():
            A(_row({"on": "钉成 on(≥70 才买)", "off": "钉成 off(<30 暂停)",
                    "null": "null(现状)"}[tag], m))
        A("")
        A(f"> 现状 ERP = {erp['erp']:.2f}%(PE {erp['pe']:.2f} TTM − 10Y 中债 "
          f"{erp['bond10y']:.2f}%), 但只有 {erp['n_months']} 个月度样本, 分位不可信 → 按文档 §1 "
          f"输出 null。{'等月度样本满 36 个, 闸门自动生效。' if erp['n_months'] < 36 else ''}")
        A("")
        if sweep3:
            pct3 = erp3.get("erp_pct")
            A("### 沪深300（真分位, v1.4 新增腿）")
            A("")
            A(f"沪深300 的 PE 自 2005 年就有(月度 {erp3.get('n_months')} 个点, 远超 3 年门槛), "
              f"所以这条腿的分位**是真的**: 现状 ERP {erp3.get('erp'):.2f}% → 分位 "
              f"{pct3 * 100:.0f}% → "
              f"{'≥70 → on' if pct3 >= 0.70 else '<30 → off' if pct3 < 0.30 else '30~70 → 不设限'}。"
              "下表同样把闸门钉成 on / off / null 作对照:")
            A("")
            A("| ERP 闸门 | 累计 | 年化 | 年化波动 | 最大回撤 | 夏普 |")
            A("|---|---:|---:|---:|---:|---:|")
            for tag, m in sweep3.items():
                A(_row({"on": "钉成 on(≥70 才买)", "off": "钉成 off(<30 暂停)",
                        "null": "钉成 null(不设限)"}[tag], m))
            A("")
            A("> 与 A500 的区别值得记住: A500 的「不设限」是**没数据**, 沪深300 的「不设限」是"
              "**数据说中间**。前者修数据源就能改变行为, 后者只能等分位跑到两端。")
            A("")

    sec = "五" if sweep else "四"
    A(f"## {sec}、策略全部成交({label} · 执行: 六腿场外 A 类 / 黄金场内 518880)")
    A("")
    A("| 日期 | 资产 | 动作 | 金额(pp) | 规则 | 说明 |")
    A("|---|---|---|---:|---|---|")
    for t in res["trades"]:
        A(f"| {t['date']:%Y-%m-%d} | {rule.LEGS[t['leg']]['name']} | "
          f"{'买入' if t['kind'] == 'buy' else '卖出'} | {t['amount_pp']:.2f} | "
          f"{t['rule']} | {t['detail']} |")
    A("")

    sec = "六" if sweep else "五"
    A(f"## {sec}、结论与局限")
    A("")
    A("- **主要结论**: 规则把「什么时候买」交给了回撤/趋势, 代价是长期拿着大量现金 —— "
      f"平均投入 {_hold_avg(res):.1f}pp 对 {rule.SLEEVE_PP:.0f}pp 额度。买点确实落在低位的暴跌段, "
      "但也因此错过了震荡上行段。")
    A("- **成本不可忽略**: 恒生科技的年线闸门是「跌破 5 日减半、收复即加满」的非对称设计, "
      "样本内围绕 MA250 反复穿越产生多次往返(见成交流水), 单边 "
      f"{rule.FEE_BUY:.1%}+{rule.FEE_SELL:.1%} 的成本在窄幅震荡里会持续磨损。")
    A(f"- **样本**: {label}组合层 {years:.1f} 年(受**最晚就绪的那条腿** + 250 日窗口限制: "
      "恒生科技 `HKHSTECH` 2020-07 起); 单腿专项样本更长。")
    A("- **估值闸门大部分空转**: A500 ERP(样本不足)、科创50 PB、恒生科技 ERP、TIPS、WGRC 当前均为 "
      "null; 美股两条腿的 ERP 分位是人工月度复核值(当前 0.05 → 暂停新增)。")
    A("- **闸门口径是「当下快照」，严格说是前视**: 估值分位取最新读数并套用到全程，"
      "历史上那些时刻并不知道今天的读数。逐日 as-of 对照见 "
      "`research/gate_asof_check/_gate_asof_check.md`（沪深300：组合 0.00pp、单腿 +0.01pp/年 —— "
      "该闸门在这两段样本里没改变过成交路径，但 A500/科创50/恒科/美股的闸门仍无法验证）。")
    if us.get("ust10y") is not None:
        A(f"- **美股闸门的辅助读数**(仅报告, 不参与判定): 10Y 美债 {us['ust10y']:.2f}%"
          f"({us['ust10y_asof']}); NDX forward PE {us['ndx_fwd_pe']:.2f}"
          f"({us['ndx_fwd_pe_asof']}) → 前瞻盈利收益率 {us['ndx_ey']:.2f}%。"
          "forward PE 会系统性抬高 ERP, 不能顶替 §6 要求的 PE(TTM)。")
    A("- **未计**: 场外申赎确认时滞(QDII T+2)、大额限购、销售服务费(C 类 0.2%/年量级)、"
      "以及 A500/标普桥接段的指数差异。")
    A("- **不做的事**: 不预测顶底、不用杠杆、不因「跌得多」跳过扳机(扳机就是 RSI<40)。")
    A("")
    A("> 免责声明: 历史回测不预示未来; 机械规则输出仅供研究参考, 不构成投资建议。")
    md = "\n".join(L)

    os.makedirs(_BT_DIR, exist_ok=True)
    with open(_OUT_MD, "w", encoding="utf-8") as f:
        f.write(md + "\n")
    with open(_OUT_JSON, "w", encoding="utf-8") as f:
        json.dump(dict(
            generated=datetime.now().strftime("%Y-%m-%d %H:%M"),
            signal_mode="index",
            window=[f"{res['nav'].index[0]:%Y-%m-%d}", f"{res['nav'].index[-1]:%Y-%m-%d}"],
            days=n_days, years=round(years, 2), sleeve_pp=rule.SLEEVE_PP,
            perf={spec["key"]: {kk: round(vv, 6) for kk, vv in perfs[spec["key"]].items()}
                  for spec in MODES},
            avg_invested={spec["key"]: round(_hold_avg(runs[spec["key"]]), 2) for spec in MODES},
            singles={k: dict(window=[f"{singles[k]['ladder']['nav'].index[0]:%Y-%m-%d}",
                                     f"{singles[k]['ladder']['nav'].index[-1]:%Y-%m-%d}"],
                             ladder={kk: round(vv, 6) for kk, vv in singles[k]["m_ladder"].items()},
                             hold={kk: round(vv, 6) for kk, vv in singles[k]["m_hold"].items()},
                             avg_invested=round(_hold_avg(singles[k]["ladder"]), 2),
                             trades=len(singles[k]["ladder"]["trades"]))
                      for k in SINGLE},
            erp_gate_sweep={k: {kk: round(vv, 6) for kk, vv in v.items()}
                            for k, v in sweep.items()},
            erp_gate_sweep_hs300={k: {kk: round(vv, 6) for kk, vv in v.items()}
                                  for k, v in sweep3.items()},
            attribution={k: round(v, 3) for k, v in attrib.items()},
            trades=[dict(date=f"{t['date']:%Y-%m-%d}", leg=t["leg"], kind=t["kind"],
                         amount_pp=round(t["amount_pp"], 3), rule=t["rule"],
                         detail=t["detail"]) for t in res["trades"]],
            cost=tstats,
            valuation=dict(erp=erp, hs300_erp=erp3, us_erp=us,
                           ndx_erp_pct=vi["ndx_erp_pct"],
                           spx_erp_pct=vi["spx_erp_pct"]),
        ), f, ensure_ascii=False, indent=2)

    print(f"[ok] {_OUT_MD}")
    for spec in MODES:
        m = perfs[spec["key"]]
        print(f"  {spec['name'].replace('**', ''):26s} 累计 {m['cum']:7.1%}  年化 {m['ann']:6.2%}  "
              f"回撤 {m['mdd']:7.1%}  夏普 {m['sharpe']:.2f}  "
              f"平均投入 {_hold_avg(runs[spec['key']]):.1f}pp")
    for key in SINGLE:
        m = singles[key]["m_ladder"]
        print(f"  {rule.LEGS[key]['name'] + ' 阶梯(全周期)':22s} 累计 {m['cum']:7.1%}  "
              f"年化 {m['ann']:6.2%}  回撤 {m['mdd']:7.1%}  夏普 {m['sharpe']:.2f}")


if __name__ == "__main__":
    main()
