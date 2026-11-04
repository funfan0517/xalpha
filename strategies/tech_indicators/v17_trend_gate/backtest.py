# -*- coding: utf-8 -*-
"""技术指标家族 · V17 变种：趋势四象限闸门 · 54 只场内 ETF 回测。

池与口径完全跟随 `strategies/tech_indicators`：
- 54 只场内 ETF = `_pool.py`（即 `data/_universe.md` 前四类），唯一池源；
- 价格 = 家族共享 `data/_long_klines.json` 的**收盘价**；
- 闸门套**全部 54 只**（单标的分档，不存在 vol_legs 组合层带位叠加问题）；
- 成交 = 信号当日收盘确定、赚次日收益（家族「当日收盘成交」惯例，无 T+1 净值时滞）；
- 空仓/减仓部分按**无风险利率 0** 计息（家族惯例，与 v16_trin 一致）；未计佣金/滑点。

与 vol_legs/v5_trend_gate 的关系：**同一套规则、同一套分析**，数据源从「七腿场外复权净值」
换成「54 只场内 ETF 收盘价」，带内占比 {1.0/0.5/0.2} 直接作为该 ETF 的目标仓位（非 0/1）。

产出：
- backtest/_v17_trend_gate_bt.md    规则 + 逐标的 + 组合层 + 四象限收益 + 滞后成本 + 敏感性
- backtest/_v17_trend_gate_bt.jsonl
- backtest/_v17_quad_dist.png        横截面象限分布时间序列图

模拟结果，非投资建议。
"""
import json
import os
import sys

import numpy as np
import pandas as pd

try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:  # noqa: BLE001
    pass

_HERE = os.path.dirname(os.path.abspath(__file__))
_PARENT = os.path.dirname(_HERE)
_ROOT = os.path.dirname(os.path.dirname(_PARENT))
_DATA = os.path.join(_ROOT, "data")
for _p in (_HERE, _PARENT, _ROOT, _DATA):
    if _p not in sys.path:
        sys.path.insert(0, _p)

import trend_gate as tg  # noqa: E402

OUTDIR = os.path.join(_HERE, "backtest")
OUT_MD = os.path.join(OUTDIR, "_v17_trend_gate_bt.md")
OUT_JSONL = os.path.join(OUTDIR, "_v17_trend_gate_bt.jsonl")
OUT_PNG = os.path.join(OUTDIR, "_v17_quad_dist.png")

# 敏感性档：(标签, 参数覆盖)
SENS = [
    ("基准（MA250 · 斜率20日 · 5/20 日阶梯 · 间隔帽5日 · 全套54只）", {}),
    ("年线改 MA120", dict(MA_WIN=120)),
    ("年线改 MA200（美股口径）", dict(MA_WIN=200)),
    ("斜率回看 5 日", dict(SLOPE_N=5)),
    ("斜率回看 60 日", dict(SLOPE_N=60)),
    ("减半提前到 3 日", dict(REDUCE_DAYS=3, CLEAR_DAYS=20)),
    ("减半推后到 10 日", dict(REDUCE_DAYS=10, CLEAR_DAYS=20)),
    ("清仓提前到 10 日", dict(CLEAR_DAYS=10)),
    ("清仓推后到 40 日", dict(CLEAR_DAYS=40)),
    ("无阶梯（跌破即减半、不清仓）", dict(REDUCE_DAYS=1, CLEAR_DAYS=10 ** 6)),
    ("无间隔帽（ACTION_GAP=0）", dict(ACTION_GAP=0)),
    ("间隔帽 10 日", dict(ACTION_GAP=10)),
    ("观察仓改 0.3（不清到 0.2）", dict(OBSERVE=0.3)),
    ("空仓起步（不继承底仓）", dict(START="flat")),
    ("只看 close 在年线上/下（无斜率）", dict(MODE="ma_only")),
]


# ---------------------------------------------------------------- 0. 装载
def load_all(refresh=True):
    """-> (panel, codes_present)。panel: {code: close Series（各自 warmup 起）}。"""
    full = tg.vdata.panel(refresh=refresh)
    out = {}
    for code in tg.LEGS:
        if code not in full:
            continue
        px = full[code].dropna()
        if len(px) <= tg.WARMUP + 2:
            continue
        px = px.iloc[tg.WARMUP:]
        if len(px) < 252:        # 至少 1 年有效样本，避免极短历史的噪声污染头条
            continue
        out[code] = px
    return out, sorted(out)


# ---------------------------------------------------------------- 1. 逐标的
def leg_rows(panel, **kw):
    rows = []
    for k in panel:
        px = panel[k]
        sm = tg.state_machine(px, k, **kw)
        st = sm["state"]
        fee = tg.fee_of(k)
        nav, nav_fee = tg.run_leg(st, px), tg.run_leg(st, px, fee)
        bh = px / px.iloc[0]
        m, mf, b = tg.metrics(nav), tg.metrics(nav_fee), tg.metrics(bh)
        acts = tg.actions(st)
        ws = tg.whipsaw(st, px)
        years = max((px.index[-1] - px.index[0]).days / 365.25, 1e-9)
        rows.append(dict(
            code=k, name=tg.NAMES.get(k, k),
            start=px.index[0].date().isoformat(), end=px.index[-1].date().isoformat(),
            years=round(years, 2), bars=len(px),
            ann=mf["ann"], mdd=mf["mdd"], vol=mf["vol"], sharpe=mf["sharpe"],
            edge_pp=(mf["ann"] - b["ann"]) * 100, edge_bh_pp=(mf["ann"] - b["ann"]) * 100,
            bh_ann=b["ann"], bh_mdd=b["mdd"], bh_vol=b["vol"], bh_sharpe=b["sharpe"],
            exposure=tg.exposure(st),
            actions=len(acts), acts_per_year=len(acts) / years,
            whip=ws["n"], whip_miss=ws["avg_miss"],
            state_last=float(st.iloc[-1]),
            quad_last=str(sm["quad"].iloc[-1]),
        ))
    return rows


# ---------------------------------------------------------------- 2. 组合层（全池等权）
def build_states(panel, **kw):
    """-> {code: state Series}（对齐 panel 各自索引）。"""
    out = {}
    for k, px in panel.items():
        out[k] = tg.state_machine(px, k, **kw)["state"]
    return out


def combo_nav(panel, states=None):
    """全池等权组合净值。-> (nav_gate, nav_bh, exp_series)。

    逐标的在自己窗口内算 gate 收益与买入持有收益，按日等权平均（缺失标的不参与平均），
    自然处理各标的不同上市日期。空仓部分按 0 计息。
    """
    if states is None:
        states = build_states(panel)
    rets_gate, rets_bh, state_s = [], [], []
    idx = sorted(set().union(*[set(px.index) for px in panel.values()]))
    idx = pd.DatetimeIndex(idx)
    for k, px in panel.items():
        ret = px.pct_change().fillna(0.0)
        st = states[k].reindex(idx).ffill().fillna(0.0).astype(float).shift(1).fillna(0.0)
        rg = (st * ret.reindex(idx).fillna(0.0))
        rb = ret.reindex(idx).fillna(0.0)
        rets_gate.append(rg.rename(k))
        rets_bh.append(rb.rename(k))
        state_s.append(st.rename(k))
    g = pd.DataFrame(rets_gate).T
    b = pd.DataFrame(rets_bh).T
    s = pd.DataFrame(state_s).T
    mean_g = g.mean(axis=1, skipna=True).fillna(0.0)
    mean_b = b.mean(axis=1, skipna=True).fillna(0.0)
    nav_g = (1.0 + mean_g).cumprod()
    nav_b = (1.0 + mean_b).cumprod()
    exp = s.mean(axis=1, skipna=True).fillna(0.0)
    return nav_g, nav_b, exp


# ---------------------------------------------------------------- 3. 象限收益 / 滞后 / 口径分歧
def _fwd(px, h):
    return px.shift(-h) / px - 1.0


def long_table(panel, **kw):
    """逐标的 × 日 的长表（全部套闸门），带象限与未来收益，供分桶统计。"""
    parts = []
    for k, px in panel.items():
        sm = tg.state_machine(px, k, **kw)
        parts.append(pd.DataFrame(dict(
            code=k, quad=sm["quad"], dev=sm["dev"], below_days=sm["below_days"],
            mom6=sm["mom6"], mom12=sm["mom12"],
            f20=_fwd(px, 20), f60=_fwd(px, 60), f120=_fwd(px, 120),
        )))
    df = pd.concat(parts)
    return df[df["quad"] != ""]


def quad_table(df):
    out = []
    for q in tg.QUADS:
        sub = df[df["quad"] == q]
        if len(sub) == 0:
            out.append(dict(name=q, days=0, share=0.0, f20=None, w20=None, f60=None,
                            w60=None, worst20=None, dev=None))
            continue
        out.append(dict(
            name=q, days=len(sub), share=len(sub) / len(df),
            f20=float(sub["f20"].mean()),
            w20=float((sub["f20"] > 0).mean()),
            f60=float(sub["f60"].mean()),
            w60=float((sub["f60"] > 0).mean()),
            worst20=float(sub["f20"].min()) if sub["f20"].notna().any() else None,
            dev=float(sub["dev"].mean()) if sub["dev"].notna().any() else None,
        ))
    return out


def mom_table(df):
    up = df["quad"] == tg.QUADS[0]
    dn = df["quad"] == tg.QUADS[3]
    mom_up = df["mom6"] > 0
    buckets = [
        ("均线多 + 动量多（一致看多）", up & mom_up),
        ("均线多 + 动量空（分歧）", up & ~mom_up),
        ("均线空 + 动量多（分歧）", dn & mom_up),
        ("均线空 + 动量空（一致看空）", dn & ~mom_up),
        ("半格（价格与均线反向：反弹/高位回调）", ~up & ~dn),
    ]
    out = []
    for name, mask in buckets:
        sub = df[mask]
        out.append(dict(name=name, days=len(sub), share=len(sub) / len(df) if len(df) else 0.0,
                        f20=float(sub["f20"].mean()) if len(sub) else None,
                        f60=float(sub["f60"].mean()) if len(sub) else None,
                        w60=float((sub["f60"] > 0).mean()) if len(sub) else None))
    return out


def mom_agree(df):
    up = df["quad"] == tg.QUADS[0]
    dn = df["quad"] == tg.QUADS[3]
    mu = df["mom12"] > 0
    return dict(n=len(df), up_down=int((up & ~mu).sum()), dn_up=int((dn & mu).sum()))


def lag_table(panel, **kw):
    out = []
    allrows = []
    for k, px in panel.items():
        full = px
        sm = tg.state_machine(full, k, **kw)
        quad = sm["quad"].to_numpy(dtype=object)
        dd = sm["dd_hi"].to_numpy(dtype=float)
        ma = sm["ma"].to_numpy(dtype=float)
        c = full.to_numpy(dtype=float)
        n = len(c)
        rows = []
        for i in range(1, n):
            if sm["ready"].iloc[i] and quad[i] == tg.QUADS[3] and quad[i - 1] != tg.QUADS[3]:
                after = c[i:min(n, i + 121)] / c[i] - 1.0
                rec = None
                for j in range(i + 1, min(n, i + 251)):
                    if c[j] >= ma[j]:
                        rec = j - i
                        break
                rows.append(dict(code=k, date=full.index[i].date().isoformat(),
                                 dd_at_confirm=float(dd[i]),
                                 mdd_after=float(np.min(after)) if len(after) else 0.0,
                                 recover_bars=rec))
        vals = [r["dd_at_confirm"] for r in rows]
        aft = [r["mdd_after"] for r in rows]
        recs = [r["recover_bars"] for r in rows if r["recover_bars"] is not None]
        out.append(dict(
            code=k, name=tg.NAMES.get(k, k), n=len(rows),
            dd_med=float(np.median(vals)) if vals else None,
            dd_worst=float(np.min(vals)) if vals else None,
            after_med=float(np.median(aft)) if aft else None,
            after_worst=float(np.min(aft)) if aft else None,
            recover_med=float(np.median(recs)) if recs else None,
            recover_never=sum(1 for r in rows if r["recover_bars"] is None and r["date"]),
        ))
        allrows += rows
    pooled = dict(
        code="pooled", name="54 只闸门标的合计", n=len(allrows),
        dd_med=float(np.median([r["dd_at_confirm"] for r in allrows])) if allrows else None,
        dd_worst=float(np.min([r["dd_at_confirm"] for r in allrows])) if allrows else None,
        after_med=float(np.median([r["mdd_after"] for r in allrows])) if allrows else None,
        after_worst=float(np.min([r["mdd_after"] for r in allrows])) if allrows else None,
        recover_med=(float(np.median([r["recover_bars"] for r in allrows
                                      if r["recover_bars"] is not None]))
                     if any(r["recover_bars"] is not None for r in allrows) else None),
        recover_never=sum(1 for r in allrows if r["recover_bars"] is None),
    )
    out.append(pooled)
    return out, allrows


def sens_rows(panel, **kw):
    out = []
    for name, kwo in SENS:
        states = build_states(panel, **kwo)
        nav_g, nav_b, _ = combo_nav(panel, states)
        mg, mb = tg.metrics(nav_g), tg.metrics(nav_b)
        n_act = sum(len(tg.actions(states[k])) for k in panel)
        out.append(dict(name=name, ann=mg["ann"], mdd=mg["mdd"], sharpe=mg["sharpe"],
                        bh_ann=mb["ann"], bh_mdd=mb["mdd"], actions=n_act))
    return out


def segments(nav_g, nav_b):
    idx = nav_g.index
    n = len(idx)
    out = []
    for i in range(3):
        i0 = i * n // 3
        i1 = (i + 1) * n // 3 - 1 if i < 2 else n - 1
        out.append((f"{idx[i0].date()} ~ {idx[i1].date()}", idx[i0], idx[i1]))
    return out


def quad_dist_panel(panel, **kw):
    """逐日各象限标的数量（横截面分布）。-> DataFrame(index=date, columns=QUADS)。"""
    frames = []
    for k, px in panel.items():
        sm = tg.state_machine(px, k, **kw)
        q = sm["quad"].where(sm["ready"], "")
        frames.append(q.rename(k))
    quad_panel = pd.DataFrame(frames).T.fillna("")      # index=date, columns=code
    out = pd.DataFrame(index=quad_panel.index, columns=tg.QUADS, dtype=int)
    for q in tg.QUADS:
        out[q] = (quad_panel == q).sum(axis=1).astype(int)
    return out.sort_index()


# ---------------------------------------------------------------- 4. 报告
def main():
    os.makedirs(OUTDIR, exist_ok=True)
    panel, codes = load_all(refresh=False)
    if not panel:
        print("[错误] 池内无可用行情，先跑 data/_refresh_data.py 或检查 _long_klines.json")
        return
    rows = leg_rows(panel)
    nav_g, nav_b, exp = combo_nav(panel)
    df = long_table(panel)
    quads = quad_table(df)
    moms = mom_table(df)
    agree = mom_agree(df)
    lags, lag_rows = lag_table(panel)
    sens = sens_rows(panel)
    seg = segments(nav_g, nav_b)
    dist = quad_dist_panel(panel)

    # 横截面象限分布图
    _plot_quad(dist)

    with open(OUT_JSONL, "w", encoding="utf-8") as f:
        for r in rows:
            f.write(json.dumps({k: (round(v, 4) if isinstance(v, float) else v)
                                for k, v in r.items()}, ensure_ascii=False) + "\n")
        f.write(json.dumps({"combo": dict(
            ann=round(tg.metrics(nav_g)["ann"], 4), mdd=round(tg.metrics(nav_g)["mdd"], 4),
            sharpe=round(tg.metrics(nav_g)["sharpe"], 3),
            bh_ann=round(tg.metrics(nav_b)["ann"], 4), bh_mdd=round(tg.metrics(nav_b)["mdd"], 4),
            exposure=round(float(exp.mean()), 3))}, ensure_ascii=False) + "\n")
        for r in sens:
            f.write(json.dumps({"sens": r["name"],
                                **{k: (round(v, 4) if isinstance(v, float) else v)
                                   for k, v in r.items() if k != "name"}},
                               ensure_ascii=False) + "\n")

    cg, cb = tg.metrics(nav_g), tg.metrics(nav_b)
    win = f"{nav_g.index[0].date()} ~ {nav_g.index[-1].date()}"
    yrs = (nav_g.index[-1] - nav_g.index[0]).days / 365.25
    n_beat = sum(1 for r in rows if r["edge_pp"] > 0)
    n_dd = sum(1 for r in rows if abs(r["mdd"]) < abs(r["bh_mdd"]) - 1e-9)
    best = max(rows, key=lambda z: z["edge_pp"])
    worst = min(rows, key=lambda z: z["edge_pp"])
    gated_rows = rows  # 全部套闸门

    L = [
        f"# 技术指标家族 · V17 变种：趋势四象限闸门 · {len(codes)} 只场内 ETF 回测",
        f"> 池 = `strategies/tech_indicators/_pool.py`（即 `data/_universe.md` 前四类，"
        f"**{len(codes)} 只有行情**）；价格 = 家族共享 `data/_long_klines.json` **收盘价**",
        "> 规则来源：用户 2026-11-02 的「趋势判断」说明；实现口径见 `v17_trend_gate/trend_gate.py` "
        "模块 docstring 与 README",
        f"> 判断趋势只用两个数：`close` 在 **MA{tg.MA_WIN}** 上方还是下方、"
        f"MA{tg.MA_WIN} 自身近 **{tg.SLOPE_N} 日**朝上还是朝下；两个都朝上才叫上涨趋势，"
        "其余三格都不给加仓许可。",
        "> 这是从 `vol_legs/v5_trend_gate` **照搬**的规则与分析，仅把数据源从「七腿场外复权净值」"
        "换成「54 只场内 ETF 收盘价」；带内占比 {1.0/0.5/0.2} 直接作为该 ETF 目标仓位（非 0/1）。",
        f"> 成交 = 信号当日收盘确定、赚次日收益（家族当日收盘成交惯例）；减仓/空仓部分按无风险利率 "
        f"**{tg.CASH_ANNUAL:.0%}** 计息（家族惯例）；未计佣金/滑点。",
        "",
        "## 1. 规则（可执行版）",
        "",
        "判断趋势只用两个数：`close` 在 **MA" + str(tg.MA_WIN) + "** 上方还是下方、"
        f"MA{tg.MA_WIN} 自身近 **{tg.SLOPE_N} 日**是朝上还是朝下。两个都朝上才叫上涨趋势，"
        "其余三格都不给加仓许可。",
        "",
        "| | MA250 朝上 | MA250 朝下 |",
        "|---|---|---|",
        f"| **close ≥ MA250** | 上涨趋势 → 加至满仓（{tg.FULL:.0%}） "
        f"| 反弹 → 只持有，不给加仓许可 |",
        f"| **close < MA250** | 高位回调 → 降一档（封顶 {tg.HALF:.0%}），不新增 "
        f"| 下跌趋势 → 满 {tg.REDUCE_DAYS} 日减半（{tg.HALF:.0%}）→ 满 {tg.CLEAR_DAYS} 日"
        f"清至观察（{tg.OBSERVE:.0%}） |",
        "",
        "| 环节 | 参数 | 说明 |",
        "|---|---|---|",
        f"| 年线 | `MA_WIN={tg.MA_WIN}` | A 股一年约 240–245 个交易日，中文语境年线取 250；"
        "美股习惯 200。**混用会让两套回测结论互相矛盾** |",
        f"| 斜率 | `SLOPE_N={tg.SLOPE_N}` | 年线自身 20 日变化量的符号 |",
        f"| 阶梯帽 | `REDUCE_DAYS={tg.REDUCE_DAYS}` / `CLEAR_DAYS={tg.CLEAR_DAYS}` | "
        "跌破后满 5 日才减半、满 20 日未收复才清至观察 —— **限制响应速度** |",
        f"| 间隔帽 | `ACTION_GAP={tg.ACTION_GAP}` | 同一标的两次调仓最小间隔 |",
        f"| 档位 | `FULL={tg.FULL:g}` / `HALF={tg.HALF:g}` / `OBSERVE={tg.OBSERVE:g}` | "
        "是该 ETF 的**目标仓位比例**（不是 pp，不是 0/1） |",
        f"| 作用域 | `GATE_ALL={tg.GATE_ALL}` | 全部 {len(codes)} 只场内 ETF 套闸门 |",
        "",
        "> **三个坑**：①滞后是设计特性不是 bug（§5 给出实测滞后成本）；②250 与 200 不是一回事；"
        "③震荡市假信号一定存在，解法是频率帽（5/20 阶梯 + 间隔帽），不是再叠一个指标。",
        "",
        f"## 2. 逐标的（各自年线 + 斜率成形后起 · 按年化差降序 · {len(rows)} 只）",
        "",
        "「买入持有满仓」= 同区间一直 1.0 仓位；年化差 = 闸门 − 买入持有满仓。"
        "样本区间 / 调仓次数 / 打脸等明细见同目录 `_v17_trend_gate_bt.jsonl`。",
        "",
        "| 标的 | 年数 | 年化(闸门) | 最大回撤 | 夏普 | 平均仓位 | 调仓/年 | "
        "买入持有 年化 / 回撤 | 年化差 |",
        "|---|---:|---:|---:|---:|---:|---:|---|---:|",
    ]
    for r in sorted(rows, key=lambda z: z["edge_pp"], reverse=True):
        L.append(
            f"| {r['code']} {r['name']} | {r['years']:.2f} | {tg.fmt_pct(r['ann'])} | "
            f"{tg.fmt_pct(r['mdd'])} | {r['sharpe']:.2f} | {r['exposure']:.0%} | "
            f"{r['acts_per_year']:.1f} | {tg.fmt_pct(r['bh_ann'])} / {tg.fmt_pct(r['bh_mdd'])} | "
            f"{r['edge_pp']:+.2f}pp |")

    L += [
        "",
        f"> 54 只闸门标的：扣费后（本变种费用=0）跑赢「买入持有满仓」**{n_beat}/{len(rows)}**，"
        f"回撤更小 **{n_dd}/{len(rows)}**。",
        f"> 逐标的差异取决于该标的是「先在年线下阴跌很久」还是「跌破年线就 V 型反转」—— 前者闸门赚（少亏），"
        f"后者闸门亏（少赚）。最好的是 **{best['name']}**（{best['edge_pp']:+.2f}pp，"
        f"回撤 {tg.fmt_pct(best['mdd'])} vs {tg.fmt_pct(best['bh_mdd'])}），"
        f"最差的是 **{worst['name']}**（{worst['edge_pp']:+.2f}pp，"
        f"回撤 {tg.fmt_pct(worst['mdd'])} vs {tg.fmt_pct(worst['bh_mdd'])}）。",
        "> **闸门不是收益引擎**：它把各标的仓位从 100% 压低，换回撤，赚不赚取决于该标的"
        "在这段样本里属于哪一类。",
        "",
        f"## 3. 组合层（全池等权 · {win} · {yrs:.1f} 年）",
        "",
        "| 组合 | 年化 | 最大回撤 | 夏普 | 平均仓位 |",
        "|---|---:|---:|---:|---:|",
        f"| **V17 闸门（全池等权）** | **{tg.fmt_pct(cg['ann'])}** | **{tg.fmt_pct(cg['mdd'])}** | "
        f"**{cg['sharpe']:.2f}** | {float(exp.mean()):.0%} |",
        f"| 全池等权买入持有（基准） | {tg.fmt_pct(cb['ann'])} | {tg.fmt_pct(cb['mdd'])} | "
        f"{cb['sharpe']:.2f} | 100% |",
        "",
        f"> 全池等权下，闸门组合年化 {tg.fmt_pct(cg['ann'])} vs 买入持有 {tg.fmt_pct(cb['ann'])}"
        f"（**{(cg['ann'] - cb['ann']) * 100:+.2f}pp**）、"
        f"回撤 {tg.fmt_pct(cg['mdd'])} vs {tg.fmt_pct(cb['mdd'])}"
        f"（**{(cg['mdd'] - cb['mdd']) * 100:+.2f}pp**）、"
        f"夏普 {cg['sharpe']:.2f} vs {cb['sharpe']:.2f}，平均仓位 {float(exp.mean()):.0%}。",
        "> 等权把 54 只高度重叠的宽基/行业 ETF 摊成 broad 代理，结论方向与逐标的一致：闸门降波动、"
        "是否增收益取决于样本里「阴跌 vs V 型」的比例。**机械对照，非投资建议**。",
        "",
        "## 4. 四象限的未来收益（回答「判断错了分别亏多少」）",
        "",
        f"全部闸门标的按日聚合（{len(df)} 个标的·日）。未来收益用**价格本身**（在这一格里买入并持有 h 日）。",
        "",
        "| 象限 | 交易日占比 | 平均年线乖离 | 未来20日 | 20日胜率 | 未来60日 | 60日胜率 | "
        "最差20日 |",
        "|---|---:|---:|---:|---:|---:|---:|---:|",
    ]
    for q in quads:
        L.append(f"| {q['name']} | {q['share']:.0%} | {tg.fmt_pct(q['dev'])} | "
                 f"{tg.fmt_pct(q['f20'])} | {q['w20']:.0%} | {tg.fmt_pct(q['f60'])} | "
                 f"{q['w60']:.0%} | {tg.fmt_pct(q['worst20'])} |")
    q1, q2, q3, q4 = quads
    L += [
        "",
        f"> 「下跌趋势」格（{q4['share']:.0%} 的交易日）未来 20 日 {tg.fmt_pct(q4['f20'])}、"
        f"60 日 {tg.fmt_pct(q4['f60'])}：它的代价在尾巴上（最差单次 20 日 {tg.fmt_pct(q4['worst20'])}），"
        "作用是**砍尾部**，不是提高均值。",
        "> ⚠ 这些是**事后分桶**的条件收益，不是可交易的 alpha：同一格内部的离散度远大于格间差异，"
        "「最差 20 日」那一列就是提醒。",
        "",
        "## 5. 滞后成本（进入「下跌趋势」那一刻已经跌掉多少）",
        "",
        "| 标的 | 进入次数 | 确认时距 250 日高点（中位） | 最差 | 之后 120 日最大回撤（中位） | "
        "最差 | 收复年线（交易日·中位） | 区间内未收复 |",
        "|---|---:|---:|---:|---:|---:|---:|---:|",
    ]
    for r in lags:
        rec = r["recover_med"]
        rec_txt = "—" if rec is None else f"{rec:.0f}"
        L.append(f"| {r['name']} | {r['n']} | {tg.fmt_pct(r['dd_med'])} | "
                 f"{tg.fmt_pct(r['dd_worst'])} | {tg.fmt_pct(r['after_med'])} | "
                 f"{tg.fmt_pct(r['after_worst'])} | {rec_txt} | {r['recover_never']} |")
    pooled = lags[-1]
    L += [
        "",
        f"> **「滞后」被量化了**：趋势闸门确认「进入下跌趋势」时，价格距 250 日高点中位已跌 "
        f"{tg.fmt_pct(pooled['dd_med'])}（最差 {tg.fmt_pct(pooled['dd_worst'])}），此后 120 日"
        f"中位还要再跌 {tg.fmt_pct(pooled['after_med'])}。也就是说它**不是躲回撤的工具**，"
        "它买的是「避开以年为单位的阴跌段」；拿它躲三个月的回调必然被反复打脸（见 §2 打脸列）。",
        "",
        "## 6. 与动量法的分歧（三口径里可编码的两个）",
        "",
        "结构法（道氏 HH-HL）需要事后确认摆动点、主观且滞后，本实现不编码；"
        "均线法 = 本变种的四象限；动量法 = 过去 6 个月累计收益为正。",
        "",
        "| 组合 | 日数占比 | 未来20日 | 未来60日 | 60日胜率 |",
        "|---|---:|---:|---:|---:|",
    ]
    for m in moms:
        w60 = "—" if m["w60"] is None else f"{m['w60']:.0%}"
        L.append(f"| {m['name']} | {m['share']:.0%} | {tg.fmt_pct(m['f20'])} | "
                 f"{tg.fmt_pct(m['f60'])} | {w60} |")
    disagree6 = moms[1]["days"] + moms[2]["days"]
    L += [
        "",
        f"> **两种「分歧」要分开。** ①**均线 vs 动量**（{disagree6} 个标的·日，"
        f"{disagree6 / max(agree['n'], 1):.0%}）：分歧日未来 60 日 "
        f"{tg.fmt_pct(moms[1]['f60'])} / {tg.fmt_pct(moms[2]['f60'])}，与一致看多 "
        f"{tg.fmt_pct(moms[0]['f60'])} 同量级 —— 6 个月动量在 V 型反转里天然滞后。②**价格 vs 均线**"
        f"（反弹 / 高位回调，{moms[-1]['share']:.0%} 的交易日）：这一格才是四象限要处理的分歧。",
        f"> **动量口径的参数敏感度**：12 个月动量与均线法的分歧只剩 "
        f"{agree['up_down'] + agree['dn_up']}/{agree['n']} 个标的·日"
        f"（{(agree['up_down'] + agree['dn_up']) / max(agree['n'], 1):.1%}），6 个月口径是 "
        f"{disagree6}/{agree['n']}（{disagree6 / max(agree['n'], 1):.1%}）—— 因为 MA250 与"
        " 12 月动量都在看「过去一年」。本变种**不引入动量维度**。",
        "",
    ]

    L += ["## 7. 分段（三等分 · 组合层）", "",
          "| 期间 | V17 闸门 年化 / 回撤 / 夏普 | 买入持有 年化 / 回撤 / 夏普 |", "|---|---|---|"]
    for name, d0, d1 in seg:
        a = tg.seg_perf(nav_g, d0, d1)
        b = tg.seg_perf(nav_b, d0, d1)
        fa = "—" if a is None else (f"{tg.fmt_pct(a['ann'])} / {tg.fmt_pct(a['mdd'])} / "
                                    f"{a['sharpe']:.2f}")
        fb = "—" if b is None else (f"{tg.fmt_pct(b['ann'])} / {tg.fmt_pct(b['mdd'])} / "
                                    f"{b['sharpe']:.2f}")
        L.append(f"| {name} | {fa} | {fb} |")

    ya, yb = tg.yearly(nav_g), tg.yearly(nav_b)
    L += ["", "## 8. 逐年收益（组合层）", "", "| 年 | V17 闸门 | 买入持有 | 差 |",
          "|---|---:|---:|---:|"]
    for y in sorted(ya):
        L.append(f"| {y} | {tg.fmt_pct(ya[y])} | {tg.fmt_pct(yb[y])} | "
                 f"**{(ya[y] - yb[y]) * 100:+.2f}pp** |")

    L += [
        "",
        "## 9. 参数敏感性（组合层 · 每档都看组合年化，只改一格）",
        "",
        "| 档位 | 年化 | 最大回撤 | 夏普 | 买入持有 年化 | 买入持有 回撤 | 全池总调仓次数 |",
        "|---|---:|---:|---:|---:|---:|---:|",
    ]
    for r in sens:
        L.append(f"| {r['name']} | {tg.fmt_pct(r['ann'])} | {tg.fmt_pct(r['mdd'])} | "
                 f"{r['sharpe']:.2f} | {tg.fmt_pct(r['bh_ann'])} | {tg.fmt_pct(r['bh_mdd'])} | "
                 f"{r['actions']} |")

    anns = [r["ann"] for r in sens]
    L += [
        "",
        f"> 15 档的年化全部落在 {tg.fmt_pct(min(anns))} ~ {tg.fmt_pct(max(anns))}"
        f"（宽度 {(max(anns) - min(anns)) * 100:.2f}pp）、回撤落在 "
        f"{tg.fmt_pct(max(r['mdd'] for r in sens))} ~ {tg.fmt_pct(min(r['mdd'] for r in sens))}。"
        "**结论不是靠某一格参数撑起来的**；但这也意味着在样本上这些参数都没有统计意义，"
        "只能说明「没有哪一格是灾难」。",
        "",
        "## 10. 横截面象限分布（全池每日，见 `backtest/_v17_quad_dist.png`）",
        "",
    ]
    last = dist.iloc[-1]
    L.append(
        f"> 最新交易日（{dist.index[-1].date()}）："
        + " · ".join(f"{q} {int(last[q])} 只" for q in tg.QUADS)
        + "。横截面「下跌趋势」占比高 = 全市场多数 ETF 在年线下且均线朝下，是系统性的减仓信号。")

    L += [
        "",
        "## 11. 口径与已知边界",
        "",
        "- **作用域全池套闸门**：原 vol_legs v5 在「宽基 vs 无现金流锚」上有取舍，本变种是单标的分档、"
        "没有 vol_legs 那种组合层带位叠加问题，故 54 只统一套上；结论由 §2/§3 自行验证。",
        "- **滞后是设计特性**：§5 给出实测。目标不是躲回调，是避开年单位阴跌；拿它躲短期波动必然被反复打脸。",
        "- **250 与 200 不是一回事**：A 股一年约 240–245 个交易日，中文语境年线取 250；"
        "美股习惯 200。本变种全池统一 250。",
        "- **没有加任何过滤器**：不叠 RSI、不叠成交量、不叠估值分位。震荡市的假信号靠频率帽"
        "（5/20 阶梯 + 间隔帽）限制响应速度。",
        "- **观察仓 0.2 不是 0**：留一点仓位避免「清仓后再入场」的两次成本；想彻底清仓把 `OBSERVE` 设 0。",
        "- **未计佣金/滑点、无风险利率按 0**：家族惯例；场内 ETF 实际有佣金与冲击成本，结论偏乐观。",
        "- 参数未做 walk-forward 选优：§9 的敏感性显示结论落在同一量级，不靠某一格参数成立。",
        "",
        "> **模拟结果，非投资建议。**",
    ]

    with open(OUT_MD, "w", encoding="utf-8") as f:
        f.write("\n".join(L))

    print("报告:", OUT_MD)
    print("结果:", OUT_JSONL)
    print(f"组合层 {win} · {yrs:.1f} 年 · 闸门 {tg.fmt_pct(cg['ann'])} / "
          f"{tg.fmt_pct(cg['mdd'])} / {cg['sharpe']:.2f} · 买入持有 {tg.fmt_pct(cb['ann'])}")
    for r in sorted(rows, key=lambda z: z["edge_pp"], reverse=True)[:10]:
        print(f"  {r['code']:8s} {r['name']:10s} 年化 {tg.fmt_pct(r['ann']):>9s} vs "
              f"持有 {tg.fmt_pct(r['bh_ann']):>9s} · 曝光 {r['exposure']:.0%} · "
              f"{r['actions']} 次调仓 · 打脸 {r['whip']}")


def _plot_quad(dist):
    try:
        import matplotlib
        matplotlib.use("Agg")
        import matplotlib.pyplot as plt
    except Exception as e:  # noqa
        print("matplotlib 不可用, 跳过作图:", e)
        return
    plt.rcParams["font.sans-serif"] = ["Microsoft YaHei", "SimHei", "DejaVu Sans"]
    plt.rcParams["axes.unicode_minus"] = False
    x = dist.index
    cols = {"上涨趋势": "#2a2", "反弹": "#e90", "高位回调": "#27c", "下跌趋势": "#d33"}
    fig, ax = plt.subplots(figsize=(12, 6))
    bottom = np.zeros(len(x))
    for q in tg.QUADS:
        vals = dist[q].to_numpy(dtype=float)
        ax.bar(x, vals, width=1.0, bottom=bottom, label=q, color=cols.get(q, "#888"))
        bottom += vals
    ax.set_title("V17 趋势四象限闸门 · 全池每日象限分布（标的只数）")
    ax.set_ylabel("处于该象限的 ETF 只数")
    ax.grid(axis="y", alpha=0.3)
    ax.legend()
    fig.tight_layout()
    fig.savefig(OUT_PNG, dpi=120)
    plt.close(fig)
    print("图:", OUT_PNG)


if __name__ == "__main__":
    main()
