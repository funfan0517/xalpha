# -*- coding: utf-8 -*-
"""波动资产操作规则 v1.3 · 组合层回测(**场外净值口径**)。

同一份数据、同一个执行框架(engine.py, 与每日操作共用), 只换「是否执行规则」, 隔离规则本身的贡献:

  strategy : 按 §2~§7 全部规则(阶梯 / 趋势闸门 / 保险丝 / 季度再平衡)
  seed     : 同上, 但起点就按带满仓(已持有 5/10/10/2.5/2.5/10 的人)
  open_us  : 同上, 但美股 ERP 闸门不设限(null) —— 隔离「§6 当前读数(erp<0 → 暂停新增)」
             对美股腿的锁定效应
  static   : 六腿恒定 5/10/10/2.5/2.5/10, 每季回锚 —— 「仓位带是主锚」的朴素实现
  hold     : 首日按带建仓后完全不动
  equal    : 六腿等权(各 6.67pp), 每季回锚

价格序列有两种口径, 本文件**两套都跑并逐档对比**:
  otc(主口径, rule.SIGNAL_MODE="otc") : 场外基金复权净值(累计净值, 含分红再投)。
      回测收益 = 你真能拿到的收益(含汇率、跟踪误差、申赎时滞)。A500/标普 的场外基金
      成立晚(2025-03 / 2023-04), 前段用同类场外老基金桥接补齐 250 日窗口(见 data.py)。
  exchange : A500/科创50/恒生科技/黄金 用场内 ETF 收盘, 美股用美元指数(NDX/SPX) —— 文档原口径,
      信号更纯净, 但与你实际持有的净值有偏离(汇率 + 溢价 + 跟踪误差)。

单腿专项: A500 / 纳指100 / 标普500 的「阶梯 vs 首日一次性满仓」, 用各自场外净值全历史。

用法:
  python strategies/vol_legs/backtest.py            # 两种口径都跑
  python strategies/vol_legs/backtest.py --quick    # 跳过闸门敏感性扫描
  python strategies/vol_legs/backtest.py --mode otc # 只跑场外口径
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
    dict(key="equal", name="六腿等权(季度回锚)", mode="equal"),
]
SEED = {leg: rule.LEGS[leg]["band"] for leg in rule.LEG_ORDER}
SINGLE = ["a500", "ndx", "spx"]
MODE_LABEL = {"otc": "场外净值(主口径)", "exchange": "场内ETF/美元指数"}


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
    ap.add_argument("--mode", default="both", choices=["both", "otc", "exchange"])
    args = ap.parse_args()

    mode_list = ["otc", "exchange"] if args.mode == "both" else [args.mode]
    blocks = {}
    for m in mode_list:
        ind = data.indicators_all(mode=m)
        gates, fomc = data.gates(ind["gold"].index)
        since = ready_start(ind, rule.LEG_ORDER)
        runs, perfs = run_block(ind, gates, fomc, since)
        blocks[m] = dict(ind=ind, gates=gates, fomc=fomc, since=since,
                         runs=runs, perfs=perfs,
                         px={leg: ind[leg]["close"] for leg in rule.LEG_ORDER})

    main_m = "otc" if "otc" in blocks else mode_list[0]
    B = blocks[main_m]
    ind, gates, fomc, since = B["ind"], B["gates"], B["fomc"], B["since"]
    runs, perfs, px = B["runs"], B["perfs"], B["px"]

    singles = {k: single_leg(k, ind, gates, fomc) for k in SINGLE}

    sweep = {}
    if not args.quick:
        for tag, val in (("on", "on"), ("off", "off"), ("null", None)):
            g = dict(gates, a500_erp=val)
            sweep[tag] = engine.metrics(engine.run(ind, g, fomc, since=since)["nav"])

    res = runs["strategy"]
    nav0 = float(res["nav"].iloc[0])
    attrib = attribution(res, px)
    per, tstats = trade_stats(res)
    n_days = len(res["nav"])
    years = n_days / 252.0
    erp = gates["_erp"]
    us = gates["_us_erp"]
    vi = gates["_inputs"]
    nav_tab = data.otc_nav_table()

    L = []
    A = L.append
    A("# 波动资产操作规则 v1.3 · 组合层回测(场外净值口径)")
    A("")
    A(f"> 生成 {datetime.now():%Y-%m-%d %H:%M} · 主口径 = **{MODE_LABEL[main_m]}** · 样本 "
      f"**{res['nav'].index[0]:%Y-%m-%d} ~ {res['nav'].index[-1]:%Y-%m-%d}**"
      f"({years:.1f} 年, {n_days} 个交易日)")
    band_txt = " / ".join(f"{rule.LEGS[k]['name']} {rule.LEGS[k]['band']:g}" for k in rule.LEG_ORDER)
    A(f"> 管辖额度 {rule.SLEEVE_PP:.0f}pp({band_txt}), 未投部分按货基 "
      f"{rule.CASH_ANNUAL:.2%} 计息; 成本 申购 {rule.FEE_BUY:.2%} / 赎回 {rule.FEE_SELL:.1%}"
      f"(持有 7 天内 {rule.FEE_SELL_7D:.1%})。机械规则输出, 非投资建议。")
    A("")
    A("## 〇、口径：全部用场外基金净值")
    A("")
    A("| 腿 | 场外执行/信号 | 复权净值(最新) | 序列起止 | 桥接前段 |")
    A("|---|---|---:|---|---|")
    for leg in rule.LEG_ORDER:
        cfg = rule.LEGS[leg]
        s = px[leg]
        bridge = (f"`{cfg['otc_proxy']}` {cfg.get('otc_proxy_name', '')}(本基金 "
                  f"{cfg.get('otc_start', '—')} 起)") if cfg.get("otc_proxy") else "—"
        A(f"| {cfg['name']} | `{cfg['otc']}` {cfg['exec_name']} | "
          f"{nav_tab.get(cfg['otc']) or 0:.4f} | {s.index[0]:%Y-%m-%d} ~ {s.index[-1]:%Y-%m-%d} | "
          f"{bridge} |")
    A("")
    A("> 净值序列 = **累计净值(复权, 含分红再投)**, 与每日报告里的「收盘」同一列。回测收益因此"
      "= 你实际能拿到的收益(含汇率、跟踪误差、申赎时滞), 不再是场内 ETF 价。"
      "A500(023299, 2025-03)与标普(017641, 2023-04)成立晚, 前段用同类场外老基金桥接补齐 "
      "250 日窗口 —— 只影响窗口早期段, 拼接日之后是真实执行标的。"
      "QDII 净值 T+1~T+2 公布, 回测里该腿在无净值的日子不决策、不成交。")
    A("")

    A("## 一、六档对比(场外净值口径)")
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

    if len(blocks) > 1:
        other = [m for m in blocks if m != main_m][0]
        O = blocks[other]
        A(f"## 二、口径对比：{MODE_LABEL[main_m]} vs {MODE_LABEL[other]}")
        A("")
        so, soo = O["since"], since
        A(f"> 场外样本 {soo:%Y-%m-%d} 起, 场内样本 {so:%Y-%m-%d} 起(各自「六腿就绪」的最早一天)"
          f" —— 起点不同, 差异既含「净值 vs 价格」的口径差, 也含起点差, 只做量级参考。")
        A("")
        A("| 组合 | 场外 年化 | 场外 回撤 | 场外 夏普 | 场内 年化 | 场内 回撤 | 场内 夏普 | Δ年化 |")
        A("|---|---:|---:|---:|---:|---:|---:|---:|")
        for spec in MODES:
            k = spec["key"]
            a, b = perfs[k], O["perfs"][k]
            A(f"| {spec['name'].replace('**', '')} | {_pct(a['ann'], 2)} | {_pct(a['mdd'], 1)} | "
              f"{a['sharpe']:.2f} | {_pct(b['ann'], 2)} | {_pct(b['mdd'], 1)} | {b['sharpe']:.2f} | "
              f"**{a['ann'] - b['ann']:+.2%}** |")
        A("")
        A("> 差异来源: ①**汇率** —— 场外 QDII 净值是人民币口径(含美元兑人民币波动), 场内口径用"
          "美元指数, 两者在人民币贬值/升值段会系统性分叉; ②**溢价与跟踪误差** —— ETF 价格含"
          "申赎溢价, 场外净值是真实净值; ③**分红** —— 场外用累计净值(含再投), ETF 用价格指数(除息); "
          "④**起点**。结论: 场外口径更适合回答「我能拿到多少」, 场内口径更适合看「信号有多纯」。")
        A("")

    sec = "三" if len(blocks) > 1 else "二"
    A(f"## {sec}、分腿贡献(策略口径 · 场外净值)")
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

    sec = "四" if len(blocks) > 1 else "三"
    A(f"## {sec}、单腿全周期(阶梯 vs 一次性建仓 · 场外净值)")
    A("")
    A("> 每条腿用**自己那只场外基金**的复权净值全历史; 该腿的 ERP 闸门置 null(隔离阶梯机制)。"
      "A500(023299 2025-03 成立)、标普(017641 2023-04 成立)的前段是同类老基金桥接, "
      "**该段的绝对收益不代表本基金业绩**, 只用来读「阶梯 vs 满仓」的相对关系。")
    A("")
    for key in SINGLE:
        s = singles[key]
        lad = s["ladder"]
        A(f"### {rule.LEGS[key]['name']}（`{rule.LEGS[key]['otc']}`）")
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
        sec = "五" if len(blocks) > 1 else "四"
        A(f"## {sec}、A500 ERP 启动闸门敏感性(场外净值)")
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

    sec = ("六" if sweep else "五") if len(blocks) > 1 else ("五" if sweep else "四")
    A(f"## {sec}、策略全部成交(场外口径)")
    A("")
    A("| 日期 | 资产 | 动作 | 金额(pp) | 规则 | 说明 |")
    A("|---|---|---|---:|---|---|")
    for t in res["trades"]:
        A(f"| {t['date']:%Y-%m-%d} | {rule.LEGS[t['leg']]['name']} | "
          f"{'买入' if t['kind'] == 'buy' else '卖出'} | {t['amount_pp']:.2f} | "
          f"{t['rule']} | {t['detail']} |")
    A("")

    sec = ("七" if sweep else "六") if len(blocks) > 1 else ("六" if sweep else "五")
    A(f"## {sec}、结论与局限")
    A("")
    A("- **主要结论**: 规则把「什么时候买」交给了回撤/趋势, 代价是长期拿着大量现金 —— "
      f"平均投入 {_hold_avg(res):.1f}pp 对 {rule.SLEEVE_PP:.0f}pp 额度。买点确实落在低位的暴跌段, "
      "但也因此错过了震荡上行段。")
    A("- **成本不可忽略**: 恒生科技的年线闸门是「跌破 5 日减半、收复即加满」的非对称设计, "
      "样本内围绕 MA250 反复穿越产生多次往返(见成交流水), 单边 "
      f"{rule.FEE_BUY:.1%}+{rule.FEE_SELL:.1%} 的成本在窄幅震荡里会持续磨损。")
    A(f"- **样本**: 场外口径组合层 {years:.1f} 年(受恒生科技联接 012805 于 2021-08 成立 + 250 日"
      "窗口限制); 单腿专项样本更长。")
    A("- **估值闸门大部分空转**: A500 ERP(样本不足)、科创50 PB、恒生科技 ERP、TIPS、WGRC 当前均为 "
      "null; 美股两条腿的 ERP 分位是人工月度复核值(当前 0.05 → 暂停新增)。")
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
            signal_mode=main_m,
            windows={m: [f"{blocks[m]['runs']['strategy']['nav'].index[0]:%Y-%m-%d}",
                         f"{blocks[m]['runs']['strategy']['nav'].index[-1]:%Y-%m-%d}"]
                     for m in blocks},
            days=n_days, years=round(years, 2), sleeve_pp=rule.SLEEVE_PP,
            perf={spec["key"]: {kk: round(vv, 6) for kk, vv in perfs[spec["key"]].items()}
                  for spec in MODES},
            perf_by_mode={m: {spec["key"]: {kk: round(vv, 6)
                                            for kk, vv in blocks[m]["perfs"][spec["key"]].items()}
                              for spec in MODES} for m in blocks},
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
            attribution={k: round(v, 3) for k, v in attrib.items()},
            trades=[dict(date=f"{t['date']:%Y-%m-%d}", leg=t["leg"], kind=t["kind"],
                         amount_pp=round(t["amount_pp"], 3), rule=t["rule"],
                         detail=t["detail"]) for t in res["trades"]],
            cost=tstats,
            valuation=dict(erp=erp, us_erp=us, ndx_erp_pct=vi["ndx_erp_pct"],
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
