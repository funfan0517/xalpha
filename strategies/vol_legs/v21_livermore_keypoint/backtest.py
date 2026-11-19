# -*- coding: utf-8 -*-
"""v21 回测：沪深300 · 利弗莫尔关键点买入法。

产物：`backtest/_v21_bt.md` / `.jsonl`。机械规则输出，非投资建议。
用法：`python strategies/vol_legs/v21_livermore_keypoint/backtest.py`
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
MD = os.path.join(OUTDIR, "_v21_bt.md")
JSONL = os.path.join(OUTDIR, "_v21_bt.jsonl")
A_FEE = (0.0010, 0.0050)      # 场外 A 类：申购 0.10% / 赎回 0.50%


def _slice(px, hold, start):
    return px.loc[start:], hold.loc[start:]


def main():
    os.makedirs(OUTDIR, exist_ok=True)
    px_full = engine.load()
    res = engine.analyze(px_full)                    # 全样本算状态，避免预热窗把首买价截断
    hold_full = res["hold"]
    start = px_full.index[min(engine.WARMUP, len(px_full) - 2)]
    px, hold = _slice(px_full, hold_full, start)

    nav = engine.run(px, hold)
    bh = px / px.iloc[0]
    dip_h = engine.hold_buy_dip(px_full).loc[start:]
    dip_nav = engine.run(px, dip_h)
    # 一把抄底的成交日/成交价（视频说「回撤 20% 当天满仓」）与自买入价的最大浮亏
    _dd = px_full / px_full.rolling(engine.PARAMS["dd_win"], min_periods=engine.PARAMS["dd_win"]).max() - 1.0
    _hit = int(np.where((_dd <= -engine.PARAMS["dd_trigger"]).to_numpy())[0][0])
    dip_date = px_full.index[_hit]
    dip_px = float(px_full.iloc[_hit])
    dip_worst = float(px_full.loc[dip_date:].min() / dip_px - 1.0)

    m, mb, md_ = engine.perf(nav), engine.perf(bh), engine.perf(dip_nav)
    dh = hold.diff().fillna(hold)
    years = len(px) / float(engine.TD)
    acts = int((dh.abs() > 1e-9).sum())
    ev = [e for e in res["events"] if pd.Timestamp(e["date"]) >= px.index[0]]
    n_entry = sum(1 for e in ev if e["kind"] == "entry")
    n_add = sum(1 for e in ev if e["kind"] == "add")
    n_stop = sum(1 for e in ev if e["kind"] == "stop")
    expo = float(hold.mean())

    # 敏感性（同一批买卖点，只换成本/计息/成交滞后）
    nav_cash0 = engine.run(px, hold, cash=0.0)
    nav_afee = engine.run(px, hold, fee=A_FEE)
    res0 = engine.analyze(px_full, p=dict(exec_lag=0))
    nav_lag0 = engine.run(px, res0["hold"].loc[start:])
    m_cash0, m_afee, m_lag0 = (engine.perf(nav_cash0), engine.perf(nav_afee),
                               engine.perf(nav_lag0))

    cut = px.index[int(len(px) * 0.60)]
    post = engine.perf(nav.loc[cut:])
    pre = engine.perf(nav.loc[:cut])
    post_bh = engine.perf(bh.loc[cut:])

    row = dict(leg=engine.LEG, name=engine.LABEL, status=engine.STATUS,
               code="000051", strategy="v21",
               start=str(px.index[0].date()), end=str(px.index[-1].date()),
               years=round(years, 2), bars=len(px),
               ann=round(m["ann"], 4), vol=round(m["vol"], 4),
               mdd=round(m["mdd"], 4), sharpe=round(m["sharpe"], 3),
               calmar=round(m["calmar"], 3), expo=round(expo, 4),
               acts=acts, acts_py=round(acts / years, 2),
               entries=n_entry, adds=n_add, stops=n_stop,
               bh_ann=round(mb["ann"], 4), bh_mdd=round(mb["mdd"], 4),
               bh_sharpe=round(mb["sharpe"], 3),
               dip_ann=round(md_["ann"], 4), dip_mdd=round(md_["mdd"], 4),
               dip_sharpe=round(md_["sharpe"], 3),
               fee_side=engine.FEE_SIDE, cash_annual=engine.CASH_ANNUAL,
               note="用户 2026-11-19 视频口径（利弗莫尔关键点买入法）。样本内、样本量极小，"
                    "先做样本外验证再谈实盘。")
    with io.open(JSONL, "w", encoding="utf-8", newline="\n") as f:
        f.write(json.dumps(row, ensure_ascii=False) + "\n")

    L = [
        "# v21 回测报告 · 沪深300 · 利弗莫尔关键点买入法", "",
        f"> 由 `strategies/vol_legs/v21_livermore_keypoint/backtest.py` 生成。"
        f"**用户 2026-11-19 视频口径**，样本内结果，非投资建议。", "",
        "## 规则（口径已统一）", "",
        "| 变量 | 落定口径 |", "|---|---|",
        f"| 触发场景 | 距 **{engine.PARAMS['dd_win']} 日**最高收盘回撤 ≥ "
        f"**{engine.PARAMS['dd_trigger']:.0%}** 才允许新开仓 |",
        f"| 关键点 | 过去 **{engine.PARAMS['lookback']} 日最高收盘价**（不含当日），"
        "`收盘价 > 关键点` 才算突破（不用盘中最高） |",
        f"| 建仓 | T 日收盘确认 → **T+1 收盘**成交 **{engine.PARAMS['first_size']:.0%}** |",
        "| 加仓 | 相对**首买价** +5% / +10% / +15% 各一次，+20% / +20% / +40%（每根 K 线最多一档） |",
        "| 止损 | **收盘价** ≤ 首买价 × 0.90 → T+1 收盘清仓（不用盘中最低价） |",
        "| 退出 | 主口径只止损（视频无固定止盈）；备选 `trail` / `hold_days` 见 `research/_sweep.md` |",
        f"| 成本/计息 | 单边 {engine.FEE_SIDE:.2%}（研究同尺）· 空仓货基 "
        f"{engine.CASH_ANNUAL:.2%} |",
        "| 价格 | 沪深300 真实指数（`SH000300`，价格指数，不含分红再投） |", "",
        "## 结果", "",
        "| 口径 | 年化 | 波动 | 最大回撤 | 夏普 | Calmar | 累计 | 平均仓位 |",
        "|---|---:|---:|---:|---:|---:|---:|---:|",
        f"| **v21 利弗莫尔关键点** | {m['ann']:+.2%} | {m['vol']:.2%} | {m['mdd']:.2%} | "
        f"{m['sharpe']:.2f} | {m['calmar']:.2f} | {m['cum']:+.1%} | {expo:.0%} |",
        f"| 一把抄底（回撤 20% 当天满仓） | {md_['ann']:+.2%} | {md_['vol']:.2%} | "
        f"{md_['mdd']:.2%} | {md_['sharpe']:.2f} | {md_['calmar']:.2f} | {md_['cum']:+.1%} | 100% |",
        f"| 买入持有 | {mb['ann']:+.2%} | {mb['vol']:.2%} | {mb['mdd']:.2%} | "
        f"{mb['sharpe']:.2f} | {mb['calmar']:.2f} | {mb['cum']:+.1%} | 100% |", "",
        f"样本 {px.index[0].date()} ~ {px.index[-1].date()}（{len(px)} 根 / {years:.1f} 年）；"
        "起点不是 2005-04 而是 2006-01：一年高点窗口要 252 根，本地库自 2005-01-04 起，"
        "第一个可判定的回撤读数就在 2006-01（视频的 2005.04 起点同样得往前借一年数据）；"
        f"建仓 **{n_entry}** 次 / 加仓 **{n_add}** 次 / 止损 **{n_stop}** 次，"
        f"合计调仓 {acts} 次（{acts / years:.1f} 次/年）。", "",
        f"前段（至 {cut.date()}）夏普 **{pre['sharpe']:.2f}** vs 后段 **{post['sharpe']:.2f}**"
        f"（后段买入持有 {post_bh['sharpe']:.2f}）；后段是**样本外近似**，"
        "但阈值仍是样本内选的，不等于真样本外。", "",
        "## 敏感性（只换口径，买卖点不变）", "",
        "| 口径 | 年化 | 最大回撤 | 夏普 | 说明 |",
        "|---|---:|---:|---:|---|",
        f"| 主口径（10bp / 货基 1.40% / 次日成交） | {m['ann']:+.2%} | {m['mdd']:.2%} | "
        f"{m['sharpe']:.2f} | 与族内同尺 |",
        f"| 视频口径（10bp / **空仓不计息** / 次日成交） | {m_cash0['ann']:+.2%} | "
        f"{m_cash0['mdd']:.2%} | {m_cash0['sharpe']:.2f} | 视频明确不计息 |",
        f"| 场外 A 类真实费率（申 0.10% / 赎 0.50%） | {m_afee['ann']:+.2%} | "
        f"{m_afee['mdd']:.2%} | {m_afee['sharpe']:.2f} | 执行载体 `000051` |",
        f"| **当日收盘视同成交**（少一天滞后） | {m_lag0['ann']:+.2%} | "
        f"{m_lag0['mdd']:.2%} | {m_lag0['sharpe']:.2f} | 族内多数变种的实际口径 |", "",
        "## 交易流水", "",
        "| 信号日 | 成交日 | 动作 | 成交价 | 仓位 | 首买价 | 均价 |",
        "|---|---|---|---:|---:|---:|---:|",
    ]
    for e in ev:
        fb = "—" if e.get("first_buy") is None else f"{e['first_buy']:.2f}"
        ac = "—" if e.get("avg_cost") is None else f"{e['avg_cost']:.2f}"
        L.append(f"| {e['sig_date']} | {e['date']} | {_kind_txt(e['kind'])} | "
                 f"{e['price']:.2f} | {e['from_weight']:.0%} → {e['to_weight']:.0%} | "
                 f"{fb} | {ac} |")
    L += [
        "", "## 口径与边界", "",
        "- **样本内**：阈值直接取自视频，没有做参数搜索；但 2005–2026 只有**两笔**建仓，"
        "`research/_sweep.py` 的邻域对照只用于判断稳健性，**不能**当样本外证据。",
        "- **不含分红再投**：沪深300 价格指数低估约 2.0~2.5pp/年；v21 长期在场（曝光 86%，"
        "2009-02 加满后未再退出），所以和买入持有一样被这条压低。",
        "- **指数不会停牌/涨跌停**：本条腿执行载体是场外 `000051`（按收盘净值成交），"
        "个股的涨跌停/停牌问题在指数层面不存在，但 QDII/联接的跟踪误差与申赎确认时滞不在模型内。",
        "- **一把抄底基准**定义：首次回撤 ≥ 20% 当天满仓"
        f"（本样本是 {dip_date.date()} / {dip_px:.2f}）、"
        f"之后一直满仓、无止损。自买入价的最大浮亏 **{dip_worst:.1%}**，"
        "表里 −71.6% 是**净值口径**（自净值高点）—— 视频说的 −57% 是浮亏口径，两者不可直接对账。",
        "- 全场只有 6 笔成交，A 类真实费率与 10bp 的差异 <0.1pp（成本不是这条策略的问题）。",
        "- 不加仓不摊平、只在浮盈时加仓是这套规则的核心；把 `add_anchor` 换成加权均价后"
        "金字塔更容易半途而废（见 `research/_sweep.md`）。", "",
        "> 机械规则输出，非投资建议。",
    ]
    with io.open(MD, "w", encoding="utf-8", newline="\n") as f:
        f.write("\n".join(L) + "\n")
    print("WROTE", MD)
    print("WROTE", JSONL)
    print(f"  v21 {m['ann']:+.2%}/年 回撤 {m['mdd']:.2%} 夏普 {m['sharpe']:.2f} "
          f"曝光 {expo:.0%} | 抄底 {md_['ann']:+.2%}/{md_['mdd']:.2%} | "
          f"买入持有 {mb['ann']:+.2%}/{mb['mdd']:.2%}")
    print(f"  建仓 {n_entry} / 加仓 {n_add} / 止损 {n_stop} / 调仓 {acts}")


def _kind_txt(kind):
    return {"entry": "**建仓**", "add": "加仓", "stop": "**止损清仓**",
            "trail": "回落清仓", "timeout": "到期清仓"}.get(kind, kind)


if __name__ == "__main__":
    main()
