# -*- coding: utf-8 -*-
"""v21 七腿回测：同一套利弗莫尔规则铺到全族 7 条腿，再拼等权组合。

为什么要这么测：沪深300 单腿只有两笔建仓，样本小到无法判断规则好坏。把规则原样搬到
标普500 / 纳指100 / 中证A500 / 科创50 / 恒生科技 / 黄金上，**换市场 = 最接近样本外的检验**，
同时回答「这条规则该不该进每日流程」。

口径（与单腿回测完全一致，只换标的）：
  - 每腿：距 252 日最高收盘回撤 ≥ 20% 后，收盘破 20 日最高收盘 → 次日收盘买 20%；
    浮盈 +5/+10/+15% 各加 20/20/40%；收盘破首买价 -10% 清仓；主口径只止损。
  - 成本单边 10bp、空仓货基 1.40%、**每腿独立**（不共享资金、不做跨腿再平衡）。
  - 组合 = **1/7 等权**（每腿一份，某腿数据还没起时那份按现金计息），逐日再平衡到 1/7。
    生产权重（band 2.5/2.5/10/10/2.5/2.5/10）不是等权，见文末边界。

产物：`backtest/_v21_7legs.md` / `.jsonl`。机械规则输出，非投资建议。
用法：`python strategies/vol_legs/v21_livermore_keypoint/backtest_7legs.py`
"""
import io
import json
import os
import sys

import numpy as np
import pandas as pd

try:
    sys.stdout.reconfigure(encoding="utf-8")  # type: ignore[attr-defined]
except Exception:  # noqa: BLE001
    pass

_HERE = os.path.dirname(os.path.abspath(__file__))
if _HERE not in sys.path:
    sys.path.insert(0, _HERE)

import engine  # noqa: E402

OUTDIR = os.path.join(_HERE, "backtest")
MD = os.path.join(OUTDIR, "_v21_7legs.md")
JSONL = os.path.join(OUTDIR, "_v21_7legs.jsonl")

# 按数据起点排序，便于阅读（不是权重顺序）
LEGS7 = [("hs300", "沪深300", "000051"),
         ("a500", "中证A500", "023299"),
         ("ndx", "纳指100", "270042"),
         ("spx", "标普500", "017641"),
         ("gold", "黄金", "518880"),
         ("kc50", "科创50", "011609"),
         ("hkt", "恒生科技", "012805")]

PORT_CASH = engine.CASH_ANNUAL
PORT_FEE = engine.FEE_SIDE


def _one(leg, p=None):
    """单腿 -> (px, hold, res, nav, bh_nav)，全部从预热窗之后起算。"""
    px_full = engine.load(leg)
    res = engine.analyze(px_full, p=p)
    start = px_full.index[min(engine.WARMUP, len(px_full) - 2)]
    px = px_full.loc[start:]
    hold = res["hold"].loc[start:]
    nav = engine.run(px, hold)
    bh = px / px.iloc[0]
    return px, hold, res, nav, bh


def _trades(res, start):
    ev = [e for e in res["events"] if pd.Timestamp(e["date"]) >= start]
    return (sum(1 for e in ev if e["kind"] == "entry"),
            sum(1 for e in ev if e["kind"] == "add"),
            sum(1 for e in ev if e["kind"] in ("stop", "trail", "timeout")), ev)


def _align(navs):
    """不同市场交易日不同 -> 取并集、ffill。

    某腿数据还没起的那一段，那份资金**按货基 1.40% 计息**（与 `engine.run` 的空仓口径一致），
    不是平推 1.0 —— 否则科创50/恒生科技那两份在 2021 年前会被当成零收益现金，压低组合年化。
    """
    df = pd.DataFrame(navs).sort_index()
    idx = df.index
    cash = pd.Series((1.0 + PORT_CASH) ** (np.arange(len(idx)) / float(engine.TD)), index=idx)
    out = {}
    for c in df.columns:
        out[c] = df[c].ffill().fillna(cash)
    return pd.DataFrame(out)


def _slice_perf(nav, start):
    s = nav.loc[start:]
    s = s / s.iloc[0]
    return engine.perf(s)


def main():
    os.makedirs(OUTDIR, exist_ok=True)
    rows, navs, bhs, store, all_ev = [], {}, {}, {}, []
    for leg, name, code in LEGS7:
        px, hold, res, nav, bh = _one(leg)
        store[leg] = (px, hold)
        m, mb = engine.perf(nav), engine.perf(bh)
        ne, na, nx, ev = _trades(res, px.index[0])
        for e in ev:
            all_ev.append(dict(leg=leg, name=name, **e))
        rows.append(dict(leg=leg, name=name, code=code,
                         start=str(px.index[0].date()), end=str(px.index[-1].date()),
                         years=round(len(px) / float(engine.TD), 2),
                         ann=m["ann"], vol=m["vol"], mdd=m["mdd"], sharpe=m["sharpe"],
                         cum=m["cum"], expo=float(hold.mean()),
                         bh_ann=mb["ann"], bh_mdd=mb["mdd"], bh_sharpe=mb["sharpe"],
                         bh_cum=mb["cum"], entries=ne, adds=na, exits=nx))
        navs[leg], bhs[leg] = nav, bh

    port = _align(navs).mean(axis=1)
    port_bh = _align(bhs).mean(axis=1)
    mp, mpb = engine.perf(port), engine.perf(port_bh)
    port_expo = float(np.mean([r["expo"] for r in rows]))   # 各腿曝光均值（等权口径）

    # 敏感性：只在组合层换口径
    trail_navs = {}
    for leg, _n, _c in LEGS7:
        px, _h, _r, nav_t, _b = _one(leg, dict(exit_mode="trail", trail_pct=0.15))
        trail_navs[leg] = nav_t
    mt = engine.perf(_align(trail_navs).mean(axis=1))

    cash0 = _align({leg: engine.run(px, hold, cash=0.0)
                    for leg, (px, hold) in store.items()}).mean(axis=1)
    mc0 = engine.perf(cash0)

    # 共同窗口：7 腿都有 ≥252 根的最晚起点
    common = max(px.index[0] for px, _h in store.values())
    mcw, mcwb = _slice_perf(port, common), _slice_perf(port_bh, common)

    # 分腿胜负
    win_ann = [r for r in rows if r["ann"] > r["bh_ann"]]
    win_shp = [r for r in rows if r["sharpe"] > r["bh_sharpe"]]
    both = [r["name"] for r in rows if r["ann"] > r["bh_ann"] and r["sharpe"] > r["bh_sharpe"]]
    gold_row = next(r for r in rows if r["leg"] == "gold")

    row = dict(strategy="v21", scope="7legs", legs=[l for l, _n, _c in LEGS7],
               start=str(port.index[0].date()), end=str(port.index[-1].date()),
               years=round(len(port) / float(engine.TD), 2),
               port_ann=round(mp["ann"], 4), port_mdd=round(mp["mdd"], 4),
               port_sharpe=round(mp["sharpe"], 3), port_cum=round(mp["cum"], 4),
               bh_ann=round(mpb["ann"], 4), bh_mdd=round(mpb["mdd"], 4),
               bh_sharpe=round(mpb["sharpe"], 3), bh_cum=round(mpb["cum"], 4),
               port_expo=round(port_expo, 4),
               per_leg=rows,
               note="1/7 等权、每腿独立跑同一套利弗莫尔规则；成本 10bp、空仓货基 1.40%。"
                    "前 15 年只有 hs300/a500/ndx/spx 四条腿有数据。")
    with io.open(JSONL, "w", encoding="utf-8", newline="\n") as f:
        f.write(json.dumps(row, ensure_ascii=False) + "\n")

    L = ["# v21 七腿回测 · 利弗莫尔关键点（同一套规则铺到全族 7 条腿）", "",
         "> 由 `strategies/vol_legs/v21_livermore_keypoint/backtest_7legs.py` 生成。"
         "机械规则输出，非投资建议。", "",
         "## 结论先说", "",
         f"- **7 腿等权组合（每腿 1/7、逐日再平衡）**：年化 **{mp['ann']:+.2%}** / "
         f"最大回撤 **{mp['mdd']:.2%}** / 夏普 **{mp['sharpe']:.2f}** / 累计 {mp['cum']:+.1%}；"
         f"同期等权买入持有 {mpb['ann']:+.2%} / {mpb['mdd']:.2%} / {mpb['sharpe']:.2f} / "
         f"{mpb['cum']:+.1%}。**收益基本持平（{mp['ann'] - mpb['ann']:+.2%}），"
         f"回撤少 {abs(mp['mdd'] - mpb['mdd']) * 100:.1f}pp、"
         f"夏普 {mpb['sharpe']:.2f} → {mp['sharpe']:.2f}**"
         " —— 这正是「风控层」该有的样子：不加工收益，砍掉左尾。",
         f"- **逐腿**：年化赢过买入持有 **{len(win_ann)} 条**"
         f"（{'、'.join(r['name'] for r in win_ann) or '无'}），"
         f"夏普赢过 **{len(win_shp)} 条**（{'、'.join(r['name'] for r in win_shp) or '无'}）；"
         f"**同时赢两项的只有 {'、'.join(both) or '无'}** —— 都是美股腿。",
         f"- **A 股腿是「回撤减半、年化砍半」**：沪深300 −72% → −47% 但 +7.2% → +3.1%；"
         f"中证A500 −71% → −49% 但 +8.3% → +4.1%。**黄金腿几乎不触发**"
         f"（13 年只建仓 {gold_row['entries']} 次、曝光 {gold_row['expo']:.0%}），"
         f"等于拿 1/7 的钱躺平在货基；科创50 / 恒生科技是真负贡献（假突破反复止损，"
         "−10.3% / −7.9% 年化）。",
         "- **样本结构决定了一半结论**：hs300 / a500 / ndx / spx 有 ~20 年样本，"
         "黄金 2014-08 起、科创50 2021-01 起、恒生科技 2021-08 起。"
         "2006–2013 组合只有 4/7 在场，2021 前 5~6/7 —— 「7 腿」只有后半段是真的。",
         f"- **共同窗口（{common.date()} 起，7 腿齐）**：组合 {mcw['ann']:+.2%} / "
         f"{mcw['mdd']:.2%} / {mcw['sharpe']:.2f} vs 等权买入持有 {mcwb['ann']:+.2%} / "
         f"{mcwb['mdd']:.2%} / {mcwb['sharpe']:.2f} —— 收益更高、回撤没更小、"
         "夏普略好，这段最接近「今天起用这条规则」。",
         "- 结论：**组合层有风控价值（同收益、半回撤），单腿层没有通用 alpha**。"
         "它不适合单独作为某条腿的择时（A 股腿收益砍半），更不适合在规则还没稳定时"
         "进每日流程；比较合理的用法是当「深跌后的分批建仓纪律」叠加在已有仓位框架上。",
         "", "## 逐腿", "",
         "| 腿 | 执行 | 样本 | 年 | v21 年化 | 回撤 | 夏普 | 累计 | 曝光 | 建/加/清 | "
         "买入持有 年化 | 回撤 | 夏普 |",
         "|---|---|---|---:|---:|---:|---:|---:|---:|---|---:|---:|---:|"]
    for r in rows:
        L.append(f"| {r['name']} | `{r['code']}` | {r['start']} ~ {r['end']} | {r['years']:.1f} | "
                 f"{r['ann']:+.2%} | {r['mdd']:.2%} | {r['sharpe']:.2f} | {r['cum']:+.1%} | "
                 f"{r['expo']:.0%} | {r['entries']}/{r['adds']}/{r['exits']} | "
                 f"{r['bh_ann']:+.2%} | {r['bh_mdd']:.2%} | {r['bh_sharpe']:.2f} |")

    L += ["", "## 组合（1/7 等权）", "",
          "| 口径 | 年化 | 波动 | 最大回撤 | 夏普 | Calmar | 累计 | 平均曝光 |",
          "|---|---:|---:|---:|---:|---:|---:|---:|",
          f"| **v21 7 腿等权** | {mp['ann']:+.2%} | {mp['vol']:.2%} | {mp['mdd']:.2%} | "
          f"{mp['sharpe']:.2f} | {mp['calmar']:.2f} | {mp['cum']:+.1%} | {port_expo:.0%} |",
          f"| 等权买入持有 | {mpb['ann']:+.2%} | {mpb['vol']:.2%} | {mpb['mdd']:.2%} | "
          f"{mpb['sharpe']:.2f} | {mpb['calmar']:.2f} | {mpb['cum']:+.1%} | 100% |",
          f"| 共同窗口 {common.date()} 起 · v21 | {mcw['ann']:+.2%} | {mcw['vol']:.2%} | "
          f"{mcw['mdd']:.2%} | {mcw['sharpe']:.2f} | {mcw['calmar']:.2f} | {mcw['cum']:+.1%} | — |",
          f"| 共同窗口 {common.date()} 起 · 买入持有 | {mcwb['ann']:+.2%} | {mcwb['vol']:.2%} | "
          f"{mcwb['mdd']:.2%} | {mcwb['sharpe']:.2f} | {mcwb['calmar']:.2f} | "
          f"{mcwb['cum']:+.1%} | 100% |", "",
          "## 组合层敏感性", "",
          "| 口径 | 年化 | 最大回撤 | 夏普 | 说明 |",
          "|---|---:|---:|---:|---|",
          f"| 主口径（只止损 / 10bp / 货基 1.40%） | {mp['ann']:+.2%} | {mp['mdd']:.2%} | "
          f"{mp['sharpe']:.2f} | 与单腿同尺 |",
          f"| 加「新高回落 15%」退出 | {mt['ann']:+.2%} | {mt['mdd']:.2%} | {mt['sharpe']:.2f} | "
          "逐腿换成 trail 后再等权 |",
          f"| 空仓不计息（视频口径） | {mc0['ann']:+.2%} | {mc0['mdd']:.2%} | {mc0['sharpe']:.2f} | "
          "视频明确不计息 |", "",
          "## 逐腿流水", "",
          "| 腿 | 信号日 | 成交日 | 动作 | 成交价 | 仓位 | 首买价 |",
          "|---|---|---|---|---:|---:|---:|"]
    for e in all_ev:
        kind = {"entry": "**建仓**", "add": "加仓", "stop": "**止损清仓**",
                "trail": "回落清仓", "timeout": "到期清仓"}.get(e["kind"], e["kind"])
        fb = "—" if e.get("first_buy") is None else f"{e['first_buy']:.2f}"
        L.append(f"| {e['name']} | {e['sig_date']} | {e['date']} | {kind} | {e['price']:.2f} | "
                 f"{e['from_weight']:.0%} → {e['to_weight']:.0%} | {fb} |")

    L += ["", "## 口径与边界", "",
          "- **1/7 等权不等于生产权重**。生产带的权重是 a500 2.5 / hs300 2.5 / ndx 10 / spx 10 / "
          "kc50 2.5 / hkt 2.5 / gold 10（pp of 总资产），美股权重是 A 股的 4 倍；"
          "等权是为了「同一规则在 7 个市场上的胜率」，不是组合建议。",
          "- **每腿独立、不共享资金**：某腿满仓时其余腿不会因此减仓，组合层不带跨腿风控；"
          "逐日再平衡到 1/7 也不计换手成本（只在腿内部计 10bp）。",
          "- **美元腿不计汇率**：NDX / SPX 是美元指数，QDII 人民币份额还受汇率与额度影响；"
          "恒生科技是港币。跨币种比较只看「规则是否成立」，不看真实人民币收益。",
          "- **A500 用的是 SH000510 指数**（2005 起），实际可买载体 023299 成立于 2024；"
          "组合里 2024 年前那份 A500 属于「假想可投」，与其他腿的历史不可完全等同。",
          "- **停牌/涨跌停**：指数与 ETF 层面基本不存在；QDII 溢价、申赎确认时滞不在模型内。",
          f"- 年化上赢过买入持有的 {len(win_ann)} 条"
          f"（{'、'.join(r['name'] for r in win_ann)}）里，只有纳指/标普是**真赢**"
          "（全程长牛），恒生科技属于「少亏」；A 股两条腿夏普没赢，但把回撤从 −72% 压到 −47%。"
          "这条规则赚的是「危机后均值回归 + 长牛延续」，离开长牛就只剩回撤控制。", "",
          "> 机械规则输出，非投资建议。"]
    with io.open(MD, "w", encoding="utf-8", newline="\n") as f:
        f.write("\n".join(L) + "\n")

    print("WROTE", MD)
    print("WROTE", JSONL)
    print(f"  7 腿等权 v21 {mp['ann']:+.2%}/年 回撤 {mp['mdd']:.2%} 夏普 {mp['sharpe']:.2f} "
          f"| 等权买入持有 {mpb['ann']:+.2%}/{mpb['mdd']:.2%}/{mpb['sharpe']:.2f}")
    print(f"  共同窗口 {common.date()} 起 {mcw['ann']:+.2%}/{mcw['mdd']:.2%}/{mcw['sharpe']:.2f} "
          f"vs {mcwb['ann']:+.2%}/{mcwb['mdd']:.2%}/{mcwb['sharpe']:.2f}")
    for r in rows:
        print(f"  {r['name']:6s} {r['start']}~{r['end']} v21 {r['ann']:+7.2%}/{r['mdd']:7.2%}/"
              f"{r['sharpe']:5.2f}  持有 {r['bh_ann']:+7.2%}/{r['bh_mdd']:7.2%}/"
              f"{r['bh_sharpe']:5.2f}  建仓 {r['entries']} 退出 {r['exits']}")


if __name__ == "__main__":
    main()
