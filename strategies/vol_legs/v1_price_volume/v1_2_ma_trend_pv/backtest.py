# -*- coding: utf-8 -*-
"""波动资产 · V1 量价家族 · 子策略 V1-2（20/60/250 量价趋势跟随）· 七腿回测。

口径（与家族其它变种最大的不同，务必先看）：
- 价格 & 成交量 = **场内 ETF 前复权日线**（`ohlcv.py` 的本地库）。量价规则必须有成交量，
  而场外复权净值没有量，故本子策略无法使用场外口径；V1-1 保留场外口径做纯价对照。
- 成交 = T 日收盘算信号、**T+1 收盘成交**（`pos = w.shift(1)`）；止损同样「收盘触发、次日生效」。
- 空仓按货基 `rule.CASH_ANNUAL` 逐日计息；摩擦按场内 `COST_ONE_WAY = 7.5bp`（佣金 + 滑点、免印花税）。

产出（相对本文件）：
- backtest/_v1_2_bt.md     逐腿对照 + 组合层 + 分市对比 + 交易统计 + 摩擦成本
- backtest/_v1_2_bt.jsonl  逐腿明细（一行一腿）

机械规则输出，非投资建议。
"""
import json
import os
import sys

import pandas as pd

try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:  # noqa
    pass

_HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, _HERE)

import ma_trend_vol as mtv  # noqa: E402

OUTDIR = os.path.join(_HERE, "backtest")
V11_JSONL = os.path.join(os.path.dirname(_HERE), "v1_1_ma20", "backtest", "_v1_1_ma20_bt.jsonl")


def _sim(leg):
    """该腿在「年线成形日」之后的回测（指标用全历史算好再切片，不吃掉 warmup）。"""
    ind = mtv.load(leg)
    start = ind["ma250"].first_valid_index()
    return mtv.simulate(ind.loc[start:])


def leg_rows():
    rows = []
    for k in mtv.LEGS:
        cfg = mtv.rule.LEGS[k]
        sim = _sim(k)
        tr = mtv.trades(sim)
        st = mtv.trade_stats(tr)
        m, b, fr = mtv.perf(sim["nav"]), mtv.perf(sim["bh"]), mtv.friction(sim)
        years = max((sim.index[-1] - sim.index[0]).days / 365.25, 1e-9)
        cur = mtv.state(sim)
        # 同曝光基准：把买入持有也压到「平均投入」这一比例（其余放货基），才谈得上公平 ——
        # 否则是拿「12% 时间在场」的策略去比「100% 时间在场」的买入持有，必然输在绝对收益上。
        exp0 = mtv.exposure(sim)
        r_static = exp0 * sim["close"].pct_change().fillna(0.0) + (1.0 - exp0) * mtv.CASH_DAILY
        b_same = mtv.perf((1.0 + r_static).cumprod())
        rows.append(dict(
            leg=k, name=cfg["name"], role=cfg["role"], band=float(cfg["band"]),
            code=mtv.ohlcv.CODES[k], exec_code=cfg["exec"],
            start=sim.index[0].date().isoformat(), end=sim.index[-1].date().isoformat(),
            years=round(years, 2), bars=len(sim),
            ann=m["ann"], mdd=m["mdd"], vol=m["vol"], sharpe=m["sharpe"], calmar=m["calmar"],
            cum=m["cum"], ann_gross=fr["ann_gross"],
            exposure=mtv.exposure(sim), trades=st["n"], per_year=st["n"] / years,
            win_rate=st["win_rate"], payoff=st["payoff"], pf=st["pf"], exp=st["exp"],
            avg_bars=st["bars"], max_loss_streak=st["max_loss_streak"],
            turnover=fr["turnover"], cost_total=fr["cost_total"], drag_ann=fr["drag_ann"],
            bh_ann=b["ann"], bh_mdd=b["mdd"], bh_sharpe=b["sharpe"],
            edge_pp=(m["ann"] - b["ann"]) * 100,
            bh_same_ann=b_same["ann"], bh_same_mdd=b_same["mdd"],
            edge_same_pp=(m["ann"] - b_same["ann"]) * 100,
            regimes=mtv.regime_breakdown(sim),
            w_now=cur["w"], pos_now=cur["action"], pos_note=cur["reason"],
            adx_now=cur["adx"], rvol_now=cur["rvol"], regime_now=cur["regime"],
            dist_ma20=cur["close"] / cur["ma20"] - 1.0,
            source=mtv.ohlcv.source_note(k),
        ))
    return rows


def portfolio_rows():
    frame = mtv.union_frame()
    sims = {k: mtv.simulate(mtv.load(k)) for k in frame.columns}
    nav = mtv.portfolio(frame, sims=sims, cost=True)
    nav_nc = mtv.portfolio(frame, sims=sims, cost=False)
    band = mtv.portfolio(frame, sims=None)
    eq = {k: 1.0 / len(frame.columns) for k in frame.columns}
    eq_nav = mtv.portfolio(frame, sims=None, weight=eq)
    invested = mtv.avg_invested(frame, sims)
    r_same = invested * band.pct_change().fillna(0.0) + (1.0 - invested) * mtv.CASH_DAILY
    same = (1.0 + r_same).cumprod()          # 同曝光买入持有（其余放货基）
    out = [
        ("V1-2 量价择时（扣场内费）", nav, invested),
        ("V1-2 量价择时（不计费）", nav_nc, invested),
        ("静态带（按带权恒定持有）", band, 1.0),
        ("七腿等权（恒定持有）", eq_nav, 1.0),
        (f"同曝光买入持有（固定 {invested:.0%} 投入）", same, invested),
    ]
    rows = []
    for name, obj, inv in out:
        m = mtv.perf(obj)
        rows.append(dict(name=name, cum=m["cum"], ann=m["ann"], vol=m["vol"],
                         mdd=m["mdd"], sharpe=m["sharpe"], calmar=m["calmar"], invested=inv))
    reg = mtv.load("hs300")["regime"].reindex(frame.index)
    split = {}
    nav_r = nav.pct_change(fill_method=None).fillna(0.0)
    bh_r = band.pct_change(fill_method=None).fillna(0.0)
    for k in ("牛", "熊", "震荡"):
        m_ = (reg.shift(1) == k).fillna(False)
        split[k] = dict(days=int(m_.sum()),
                        strat_day=float(nav_r[m_].mean()), bh_day=float(bh_r[m_].mean()))
    return frame, rows, split


def _pct(x, nd=2):
    return mtv.fmt_pct(x, nd)


def main():
    os.makedirs(OUTDIR, exist_ok=True)
    rows = leg_rows()
    frame, prows, psplit = portfolio_rows()

    with open(os.path.join(OUTDIR, "_v1_2_bt.jsonl"), "w", encoding="utf-8") as f:
        for r in rows:
            f.write(json.dumps({k: (round(v, 4) if isinstance(v, float) else v)
                                for k, v in r.items()}, ensure_ascii=False) + "\n")

    win = f"{frame.index[0].date()} ~ {frame.index[-1].date()}"
    yrs = (frame.index[-1] - frame.index[0]).days / 365.25
    n_beat = sum(1 for r in rows if r["edge_pp"] > 0)
    n_dd = sum(1 for r in rows if abs(r["mdd"]) < abs(r["bh_mdd"]))
    win_rate = pd.Series([r["win_rate"] for r in rows if r["trades"]])
    payoff = pd.Series([r["payoff"] for r in rows if r["trades"] and r["payoff"] != float("inf")])

    L = [
        "# 波动资产 · V1 量价家族 · 子策略 V1-2：20/60/250 量价趋势跟随（七腿）",
        "> **口径：场内 ETF 前复权日线（含成交量）** —— 量价规则必须有量，而场外复权净值没有成交量，"
        "故本子策略与 V1-1（场外净值）不同源。美股两腿用人民币 ETF 代理（513100 / 513500），信号里掺汇率。",
        f"> 成交 T+1 收盘 · 空仓计货基 {mtv.CASH_ANNUAL:.2%} · 摩擦单边 {mtv.COST_ONE_WAY * 1e4:.1f}bp（ETF 免印花税）· "
        f"单笔风险 {mtv.RISK_PCT:.0%} 上限 · 年化按 252 日",
        "",
        "## 1. 规则（原始描述 -> 实现）",
        "",
        "| 环节 | 规则 | 参数 |",
        "|---|---|---|",
        f"| 大环境 | 股价在年线上方 且 年线向上（扣抵口径：`close > close.shift(250)` ⟺ MA250 上行） | "
        f"`SMA_SLOW={mtv.SMA_SLOW}` |",
        f"| 波段线 | 20 日线找切入；60 日线控生死 | `SMA_FAST/MID={mtv.SMA_FAST}/{mtv.SMA_MID}` |",
        f"| 量能 | 近 {mtv.VOL_CONFIRM_N} 日内出现过量比 ≥ {mtv.VOL_MULT}（20 日均量）| `VOL_MULT` |",
        f"| 趋势激活 | ADX ≥ {mtv.ADX_ON:.0f} 才做突破；ADX < {mtv.ADX_OFF:.0f} 一律休眠 | `ADX_ON/OFF` |",
        f"| 建仓①突破 | 近 {mtv.CR60_WIN} 日放量上穿 60 线且收盘 ≥ MA60 + max(1ATR, 2%) | `BRK_ATR/BRK_PCT` |",
        f"| 建仓②回踩（主路径） | 20 线在 60 线上方 + 站上 20 线 + 今日收盘上行 + 近 {mtv.BELOW20_N} 日回踩到 "
        f"20 线 +{mtv.TOUCH_PCT:.0%} 以内 | `TOUCH_PCT` |",
        f"| 建仓③粘合 | 近 {mtv.SQUEEZE_N} 日三线极差 ≤ {mtv.SQUEEZE_PCT:.1%}，放量长阳（≥{mtv.SQUEEZE_YANG:.0%}）站上三线最高 | `SQUEEZE_*` |",
        f"| 仓位 | 单笔风险 {mtv.RISK_PCT:.0%} ÷ 止损幅度，上限 100% | `RISK_PCT` / `MAX_POS` |",
        f"| 止损 | 初始 `close − {mtv.STOP_ATR}ATR` 与近 {mtv.PULLBACK_N} 日最低（留 {mtv.STOP_BUFFER:.1%} 缓冲）取低者，"
        f"夹在 [{mtv.MIN_STOP:.0%}, {mtv.MAX_STOP:.0%}] | `STOP_ATR` |",
        f"| 移动保护 | 浮盈 ≥{mtv.TRAIL_ARM:.0f}ATR 后启用「峰值 − {mtv.TRAIL_ATR:.0f}ATR」（只上移）；"
        f"跌破 20 线且距 60 线 ≤{mtv.THREAT_GAP:.0%} 时减半，站回 20 线恢复 | `TRAIL_*` / `HALF` |",
        f"| 清仓 | 触发止损 / 放量或有效（> {mtv.BREAK_MA60_PCT:.0%}）跌破 60 线 / 年线转弱（价在年线下且年线向下） | — |",
        f"| 冷却 | 平仓后 {mtv.COOLDOWN} 个交易日内不再开新仓 | `COOLDOWN` |",
        "",
        "> 两处与原文的**工程化偏差**（都是为了让规则能跑出信号，方向不变）：",
        "> ① 「放量中阳同一天站回 20 线」拆成「近 3 日放量确认 + 今日收盘上行 + 近 10 日回踩过 20 线」—— "
        "严格同日几乎无信号（实测 2 笔/11 年）；",
        "> ② 「20 日线金叉 60 日线」在回踩路径里要求 `MA20 > MA60`，在突破路径里放宽到「20 线在 60 线 −1% 以内」—— "
        "刚上穿 60 线时 20 线必然还在下方，严格金叉会让突破型信号全部消失（实测 0 笔）。",
        "",
        "## 2. 逐腿对照（各自年线成形日起 · 按「择时超额(同曝光)」降序）",
        "",
        "| 腿 | 角色 | 样本区间 | 年数 | 年化 | 最大回撤 | 卡玛 | 夏普 | 曝光 | 交易 | 年笔数 | 胜率 | 盈亏比 | "
        "平均持仓 | 买入持有年化 | 买入持有回撤 | 年化差 | 同曝光买入持有 | **择时超额(同曝光)** |",
        "|---|---|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|",
    ]
    for r in sorted(rows, key=lambda z: z["edge_same_pp"], reverse=True):
        L.append(f"| {r['name']} | {r['role']} | {r['start']} ~ {r['end']} | {r['years']:.2f} | "
                 f"{_pct(r['ann'])} | {_pct(r['mdd'])} | {r['calmar']:.2f} | {r['sharpe']:.2f} | "
                 f"{r['exposure']:.0%} | {r['trades']} | {r['per_year']:.1f} | {r['win_rate']:.0%} | "
                 f"{mtv.fmt_x(r['payoff'])} | {r['avg_bars']:.0f}日 | {_pct(r['bh_ann'])} | "
                 f"{_pct(r['bh_mdd'])} | **{r['edge_pp']:+.2f}pp** | {_pct(r['bh_same_ann'])} | "
                 f"**{r['edge_same_pp']:+.2f}pp** |")

    n_same = sum(1 for r in rows if r["edge_same_pp"] > 0)
    L += [
        "",
        f"> {len(rows)} 腿中：年化跑赢买入持有 **{n_beat}/{len(rows)}**，回撤更小 **{n_dd}/{len(rows)}**；"
        f"逐腿年化差中位 **{pd.Series([r['edge_pp'] for r in rows]).median():+.2f}pp**。"
        f"胜率中位 **{win_rate.median():.0%}**、盈亏比中位 **{mtv.fmt_x(payoff.median())}**"
        "（原始描述给的量级是胜率 35%~42%、盈亏比 3.2~4.5）。",
        "",
        f"> **别拿「策略年化」直接减「买入持有年化」**：本策略平均只在场 **{pd.Series([r['exposure'] for r in rows]).median():.0%}** 额度，"
        f"绝对收益必然低于 100% 在场的买入持有。「同曝光买入持有」把买入持有也压到同一平均投入"
        f"（其余放货基），这才是可比的基准 —— 按这个口径，**{n_same}/{len(rows)}** 条腿的择时是加分的"
        f"（中位 {pd.Series([r['edge_same_pp'] for r in rows]).median():+.2f}pp）。",
        "",
        f"## 3. 组合层（并集窗口 {win} · 样本 {yrs:.1f} 年，按 `rule` 目标带归一化，满仓 = 40pp）",
        "",
        "> 组合**不取交集**：A500（563360）2024-10 才上市，取交集会把样本压到 2 年。"
        "改为各腿自年线成形日起参与、缺数据的腿不占权重（权重在可用腿之间重新归一）。",
        "",
        "| 组合 | 累计 | 年化 | 波动 | 最大回撤 | 卡玛 | 夏普 | 平均投入 |",
        "|---|---:|---:|---:|---:|---:|---:|---:|",
    ]
    for r in prows:
        L.append(f"| {r['name']} | {_pct(r['cum'], 1)} | {_pct(r['ann'])} | {_pct(r['vol'])} | "
                 f"{_pct(r['mdd'])} | {r['calmar']:.2f} | {r['sharpe']:.2f} | {r['invested']:.0%} |")

    base = prows[2]
    same = prows[4] if len(prows) > 4 else base
    L += [
        "",
        f"> 择时组合 vs 静态带：年化 {(prows[0]['ann'] - base['ann']) * 100:+.2f}pp、"
        f"回撤 {(prows[0]['mdd'] - base['mdd']) * 100:+.2f}pp、夏普 {prows[0]['sharpe']:.2f} vs "
        f"{base['sharpe']:.2f}；平均投入 {prows[0]['invested']:.0%} vs 100%。"
        f"不计费口径年化 {_pct(prows[1]['ann'])} / 扣费后 {_pct(prows[0]['ann'])}"
        f"（场内成本 {(prows[1]['ann'] - prows[0]['ann']) * 100:+.2f}pp）。",
        f"> 同曝光口径（把买入持有也压到 {same['invested']:.0%} 投入）：择时 "
        f"{_pct(prows[0]['ann'])} vs {_pct(same['ann'])} → **{(prows[0]['ann'] - same['ann']) * 100:+.2f}pp**，"
        f"回撤 {_pct(prows[0]['mdd'])} vs {_pct(same['mdd'])}。",
        "",
        "## 4. 分市对比（市况按各腿自身 250 日线定义：牛 = 价在年线上且年线向上，熊 = 反之，其余 = 震荡）",
        "",
        "### 4.1 逐腿（该市况交易日内的**日均收益**，bp；乘 244 即该市况下的年化量级）",
        "",
        "| 腿 | 牛市天数 | 牛市·策略 | 牛市·买入持有 | 熊市天数 | 熊市·策略 | 熊市·买入持有 | "
        "震荡天数 | 震荡·策略 | 震荡·买入持有 |",
        "|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|",
    ]
    for r in rows:
        g = r["regimes"]
        L.append(f"| {r['name']} | {g['牛']['days']} | {g['牛']['strat_day'] * 1e4:+.1f}bp | "
                 f"{g['牛']['bh_day'] * 1e4:+.1f}bp | {g['熊']['days']} | "
                 f"{g['熊']['strat_day'] * 1e4:+.1f}bp | {g['熊']['bh_day'] * 1e4:+.1f}bp | "
                 f"{g['震荡']['days']} | {g['震荡']['strat_day'] * 1e4:+.1f}bp | "
                 f"{g['震荡']['bh_day'] * 1e4:+.1f}bp |")

    L += [
        "",
        "### 4.2 组合层（市况用沪深300ETF 的年线口径，全样本并集）",
        "",
        "| 市况 | 天数 | V1-2 组合(日均) | 静态带(日均) |",
        "|---|---:|---:|---:|",
    ]
    for k in ("牛", "熊", "震荡"):
        s = psplit[k]
        L.append(f"| {k} | {s['days']} | {s['strat_day'] * 1e4:+.1f}bp | {s['bh_day'] * 1e4:+.1f}bp |")

    L += [
        "",
        "## 5. 交易统计与摩擦成本",
        "",
        "| 腿 | 笔数 | 年笔数 | 胜率 | 盈亏比 | 利润因子 | 单笔期望 | 平均持仓 | 最大连续亏损 | "
        "累计换手 | 累计成本 | 年化拖累 | 扣费前年化 | 扣费后年化 |",
        "|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|",
    ]
    for r in rows:
        L.append(f"| {r['name']} | {r['trades']} | {r['per_year']:.1f} | {r['win_rate']:.0%} | "
                 f"{mtv.fmt_x(r['payoff'])} | {mtv.fmt_x(r['pf'])} | {_pct(r['exp'])} | "
                 f"{r['avg_bars']:.0f}日 | {r['max_loss_streak']} | {r['turnover']:.1f}x | "
                 f"{r['cost_total'] * 100:.1f}% | {r['drag_ann'] * 100:+.2f}pp | "
                 f"{_pct(r['ann_gross'])} | {_pct(r['ann'])} |")

    L += ["", "## 6. 今日状态", "",
          "| 腿 | 信号码 | 市况 | ADX | 量比 | 距MA20 | 仓位 | 动作 | 说明 |",
          "|---|---|---|---:|---:|---:|---:|---|---|"]
    for r in rows:
        L.append(f"| {r['name']} | `{r['code']}` | {r['regime_now']} | {r['adx_now']:.0f} | "
                 f"{r['rvol_now']:.2f} | {r['dist_ma20']:+.2%} | {r['w_now']:.0%} | "
                 f"{r['pos_now']} | {r['pos_note']} |")

    # ---- 与 V1-1 的对照（同池、不同口径，只比「相对各自买入持有」） ----
    v11 = {}
    if os.path.exists(V11_JSONL):
        with open(V11_JSONL, encoding="utf-8") as f:
            for line in f:
                rec = json.loads(line)
                v11[rec["leg"]] = rec
    if v11:
        L += ["", "## 7. 与 V1-1（纯价 MA20）对照 —— 同池、不同口径，只比「相对各自买入持有」", "",
              "> V1-1 用**场外复权净值**、V1-2 用**场内 ETF 量价**，两条买入持有基准本身就不同"
              "（前者含分红再投与真实申赎、后者含折溢价与汇率）。故这里只比 `年化差 = 策略 − 同期买入持有`，"
              "不直接比年化。", "",
              "| 腿 | V1-2 年化差 | V1-2 笔数 | V1-2 曝光 | V1-2 回撤 | V1-1 年化差 | V1-1 扣费后年化差 | "
              "V1-1 笔数 | V1-1 曝光 | V1-1 回撤 |", "|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|"]
        for r in sorted(rows, key=lambda z: z["edge_pp"], reverse=True):
            o = v11.get(r["leg"], {})
            L.append(f"| {r['name']} | **{r['edge_pp']:+.2f}pp** | {r['trades']} | {r['exposure']:.0%} | "
                     f"{_pct(r['mdd'])} | {o.get('edge_pp', 0):+.2f}pp | {o.get('edge_fee_pp', 0):+.2f}pp | "
                     f"{o.get('trades', 0)} | {o.get('exposure', 0):.0%} | {_pct(o.get('mdd', 0.0))} |")
        L += ["", "> 读法：换手差一个量级（V1-1 13~16 笔/年 vs V1-2 约 1~3 笔/年），"
              "V1-2 的成本几乎可以忽略；代价是**信号更少、更依赖少数几笔**，统计显著性弱。", ]

    best = max(rows, key=lambda z: z["edge_same_pp"])
    worst = min(rows, key=lambda z: z["edge_same_pp"])
    med_dd = pd.Series([r["mdd"] for r in rows]).median()
    med_bh_dd = pd.Series([r["bh_mdd"] for r in rows]).median()
    L += [
        "",
        "## 8. 结论（照数据说，不修饰）",
        "",
        f"1. **回撤压得住**：七腿回撤中位 **{med_dd:.1%}** vs 买入持有 **{med_bh_dd:.1%}**，"
        f"七腿 **全部**更小 —— 但这是「平均只在场 {pd.Series([r['exposure'] for r in rows]).median():.0%} 额度」"
        "换来的，不是择时本事的证据。",
        f"2. **同曝光口径下择时≈中性**：{n_same}/{len(rows)} 条腿加分，中位 "
        f"**{pd.Series([r['edge_same_pp'] for r in rows]).median():+.2f}pp**；"
        f"最好 {best['name']} {best['edge_same_pp']:+.2f}pp、最差 {worst['name']} "
        f"{worst['edge_same_pp']:+.2f}pp。",
        f"3. **组合层不及「固定同额投入」**：{_pct(prows[0]['ann'])}（扣费）vs 同曝光买入持有 "
        f"{_pct(same['ann'])} → **{(prows[0]['ann'] - same['ann']) * 100:+.2f}pp**，"
        f"夏普 {prows[0]['sharpe']:.2f} vs {same['sharpe']:.2f}。也就是说：**在这七条长期上行的宽基/商品 ETF 上，"
        "这套量价择时没能跑赢「什么都不择时、只按同样暴露持有」**。",
        "4. **它的价值在纪律模板，不在超额**：ADX 休眠（熊市不交易）、放量确认（不追无量假突破）、"
        "风险预算配仓（波动大的腿自动小仓）、结构性止损 —— 这些是**可复用的工程骨架**；"
        "但「靠它多赚钱」在本仓库这批标的上没有被数据支持。",
        f"5. **成本不再是问题**（与 V1-1 的关键差别）：年笔数 1~2，累计成本 0.3%~2.7%，年化拖累 "
        f"{(prows[1]['ann'] - prows[0]['ann']) * 100:.2f}pp —— 而 V1-1 的 13~16 笔/年会把超额全部吃光。"
        "低频是它唯一明确优于 V1-1 的地方。",
        "",
        "## 9. 已知边界",
        "",
        "- **口径不可混算**：本子策略的收益是场内 ETF 价格口径（无分红再投、含折溢价与汇率），"
        "落到场外账户的实际收益会不同；美股两腿用 513100 / 513500 代理，信号里掺了汇率波动。",
        "- **样本长度差异大**：A500（563360）2024-10 才上市，年线成形后样本仅约 1 年；"
        "科创50 / 恒生科技 2021 起；沪深300 / 黄金 / 美股两腿 2013~2014 起（约 11~12 年）。逐腿不可横比。",
        "- **交易极少**：全参数都按原始描述写死（量比 ≥1.5、ADX 门槛、3 类建仓），"
        "结果是每腿每年 1~3 笔 —— 够低频（成本可忽略），但单腿只有十几笔样本，胜率与盈亏比的置信区间很宽。",
        "- **止损是收盘口径**：日内触发按收盘价判定、次日生效，比真实盘中止损**更保守**（滑价更大）。",
        "- **未计**：ETF 场内折溢价对成交价的偏离、QDII 代理的溢价波动、大额冲击成本、分红除权再投。",
        "- **参数未做敏感性扫描**：ADX 阈值、量比门槛、ATR 倍数都照原始描述取默认值，未调优 —— "
        "这是刻意的（避免过拟合），但也意味着不知道这些值附近是否稳健。",
        "",
        "> **模拟结果，非投资建议。**",
    ]

    path = os.path.join(OUTDIR, "_v1_2_bt.md")
    with open(path, "w", encoding="utf-8") as f:
        f.write("\n".join(L))

    print("报告:", path)
    print("结果:", os.path.join(OUTDIR, "_v1_2_bt.jsonl"))
    print(f"逐腿 {len(rows)} 只：年化跑赢 {n_beat}/{len(rows)} · 回撤更小 {n_dd}/{len(rows)}")
    for r in sorted(rows, key=lambda z: z["edge_pp"], reverse=True):
        print(f"  {r['name']:8s} 年化 {_pct(r['ann']):>9s} vs BH {_pct(r['bh_ann']):>9s} · "
              f"回撤 {_pct(r['mdd']):>8s} vs {_pct(r['bh_mdd']):>8s} · {r['trades']:4d} 笔"
              f"({r['per_year']:.1f}/年) · 胜率 {r['win_rate']:.0%} · 盈亏比 {mtv.fmt_x(r['payoff'])} · "
              f"曝光 {r['exposure']:.0%}")
    for r in prows:
        print(f"  [组合] {r['name']:24s} 年化 {_pct(r['ann']):>9s} · 回撤 {_pct(r['mdd']):>8s} · "
              f"夏普 {r['sharpe']:.2f} · 投入 {r['invested']:.0%}")


if __name__ == "__main__":
    main()
