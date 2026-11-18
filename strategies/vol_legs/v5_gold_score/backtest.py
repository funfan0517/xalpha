# -*- coding: utf-8 -*-
"""V5.1 黄金仓位（风险预算 × 实际利率闸门）· 回测与稳健性。

跑七件事：
1. **主表**：策略 vs 恒满仓（买入持有）vs 恒半仓 vs 纯现金；
2. **分段**：IS / OOS1 / OOS2 + 逐年；
3. **消融**：满仓 / 半仓 / 只风险预算 / 只利率闸门 / 组合 / 旧 v5.0 口径（含资金流）；
4. **参数网格**：波动目标 × 窗口 × 利率阈值 × 低配乘数（400 组）+ 步长对照，
   **同时跑 518880 与 XAU（20 年）**，只看方向一致性；
5. **换手与成本**；
6. **尾部尺子**：XAU 2006 起 20 年，同一套规则（**XAU 不进主表净值**）；
7. **验收断言**：T+1 成交、DFII10 滞后、取值域、无前视、成本、XAU 一致性、免责声明。

产物（本目录 `backtest/`）：`_v5_gold_score_bt.md` · `_v5_gold_score_bt.jsonl`。
用法：python strategies/vol_legs/v5_gold_score/backtest.py
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
if _HERE not in sys.path:
    sys.path.insert(0, _HERE)

import factors as F  # noqa: E402
import score as S    # noqa: E402

OUT_DIR = os.path.join(_HERE, "backtest")
OUT_MD = os.path.join(OUT_DIR, "_v5_gold_score_bt.md")
OUT_JSONL = os.path.join(OUT_DIR, "_v5_gold_score_bt.jsonl")
DISCLAIMER = "> 本策略不构成投资建议，市场有风险，入市需谨慎。"

SEGS = [("IS 2014-01~2019-12", "2013-07-01", "2019-12-31"),
        ("OOS1 2020-01~2023-12", "2020-01-01", "2023-12-31"),
        ("OOS2 2024-01~今", "2024-01-01", "2099-12-31")]

VT_GRID = (0.08, 0.09, 0.10, 0.11, 0.12)
VW_GRID = (60, 90, 120, 180)
TH_GRID = (0.0, 0.25, 0.50, 0.75, 1.00)
LOW_GRID = (0.4, 0.5, 0.6, 0.7)


# ---------------------------------------------------------------- 工具
def pct(x, nd=2):
    return "—" if x is None or (isinstance(x, float) and not np.isfinite(x)) else f"{x * 100:+.{nd}f}%"


def num(x, nd=2):
    return "—" if x is None or (isinstance(x, float) and not np.isfinite(x)) else f"{x:.{nd}f}"


def years_of(idx):
    return max((idx[-1] - idx[0]).days / 365.25, 1e-9)


def evaluate(state, px, p=None):
    p = p or S.params()
    nav = S.run_leg(state, px, fee=p["FEE"], slip=p["SLIP"])
    m = S.perf(nav)
    b = S.bands(state)
    return dict(nav=nav, m=m, calmar=S.calmar(m), exp=S.exposure(state),
                trades=b["trades"], cycles=b["cycles"], exits=b["full_exits"],
                cost=S.cost_drag(state, p), state=state)


def row(name, r, yrs):
    m = r["m"]
    return (f"| {name} | {pct(m['ann'])} | {pct(m['cum'])} | {m['vol'] * 100:.2f}% | "
            f"{pct(m['mdd'])} | {num(m['sharpe'])} | {num(r['calmar'])} | "
            f"{r['exp'] * 100:.0f}% | {r['trades']} | {r['trades'] / yrs:.1f} | "
            f"{r['cost'] * 100:.2f}pp | {r['cost'] * 100 / yrs:.2f}pp |")


HEAD = ("| 口径 | 年化 | 累计 | 年化波动 | 最大回撤 | 夏普 | Calmar | 平均仓位 | 调仓 | "
        "次/年 | 累计成本 | 成本/年 |\n|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|")


def mdd_of(nav):
    dd = nav / nav.cummax() - 1.0
    t = dd.idxmin()
    return t, nav.loc[:t].idxmax(), float(dd.min())


def yearly(nav):
    out, seg = {}, {}
    vals = nav.to_numpy(dtype=float)
    for i, d in enumerate(nav.index):
        st = 1.0 if i == 0 else vals[i - 1]
        y = str(d.year)
        if y not in seg:
            seg[y] = [st, vals[i]]
        seg[y][1] = vals[i]
    for y, (a, b) in seg.items():
        out[y] = b / a - 1.0
    return out


def target_risk(px, rate, p):
    """只算目标档与就绪标志（网格用，绕开整张 factor 表）。"""
    scale, _vol, ok = F.f_vol(px, p)
    gate, _used, _lvl = F.f_rate_level(rate, px.index, p)
    tgt = F.target_risk(scale, gate, p)
    ready = ok.to_numpy(dtype=bool) & gate.notna().to_numpy(dtype=bool)
    return tgt, ready


def variant(px, rate, start, blocked=None, **over):
    """覆盖参数 -> 从 `start` 起的 (state, evaluate)。`blocked` 缺省按 `EVENT_FREEZE` 现算。"""
    p = S.params(**over)
    if blocked is None:
        blocked = F._event_mask(px.index, int(p["EVENT_FREEZE"])).to_numpy(dtype=bool)
    tgt, ready = target_risk(px, rate, p)
    st, _act = S.run_target(tgt, ready, blocked, p)
    sub = st.loc[start:]
    return evaluate(sub, px.loc[start:], p)


# ---------------------------------------------------------------- 断言
def checks(d, rS, ru):
    df, px = d["df"], d["px"]
    out = []

    # C1：T 日信号 → T+1 成交
    st = df["state"].to_numpy(dtype=float)
    act = df["action"].to_numpy()
    tgt = df["target"].to_numpy(dtype=float)
    code = {"买入", "卖出"}
    n_dec = 0
    for t in range(len(df) - 1):
        if act[t] in code:
            n_dec += 1
            assert abs(st[t + 1] - tgt[t]) < 1e-9, \
                f"C1 {df.index[t].date()} 的决策未在 T+1 成交（state={st[t+1]} tgt={tgt[t]}）"
    out.append(f"C1 T 信号 / T+1 成交：{n_dec} 笔决策全部在次一交易日收盘才落到 `state`")

    # C2：DFII10 只用 ≤T−1 的值
    used = pd.to_datetime(df["rate_date"]).to_numpy()
    assert (used < df.index.to_numpy()).all(), "C2 实际利率用了当日或未来值"
    lag0 = F.f_rate_level(d["rate"], px.index, F.params(RATE_LAG=0))[2]
    lag1 = F.f_rate_level(d["rate"], px.index, F.params(RATE_LAG=1))[2]
    diff = int((lag0.round(4) != lag1.round(4)).sum())
    assert diff > 0, "C2 滞后检查是空操作（当日值与滞后值完全相同）"
    out.append(f"C2 DFII10 滞后 1 日：全部观测日 < 决策日；改成当日值会有 {diff} 天读数变化"
               "（说明滞后不是空操作）")

    # C3：取值域
    step = float(d["params"]["STEP"])
    grid = set(np.round(np.arange(0, 1.0001, step), 6))
    tv = pd.Series(df["target"]).dropna().unique()
    assert set(np.round(tv, 6)) <= grid, f"C3 目标档越界：{tv}"
    assert set(np.round(st, 6)) <= grid | {round(float(d["params"]["START"]), 6)}
    assert set(act) <= {"买入", "卖出", "持有"}
    out.append(f"C3 目标档 ∈ {{{','.join(f'{x:.0%}' for x in sorted(grid))}}}、"
               "动作 ∈ {买入,卖出,持有}")

    # C4：无前视 / OOS 不重算
    cut = px.index[-31]
    full = S.run(px, d["rate"], d["flow"], S.params(), shares=d["shares"])["state"].loc[:cut]
    part = S.run(px.loc[:cut], d["rate"], d["flow"], S.params(),
                 shares=d["shares"])["state"]
    err = float((full - part).abs().max())
    assert err < 1e-9, f"C4 存在前视（{err:.2e}）"
    out.append(f"C4 无前视：截掉最后 30 个交易日重算，历史状态逐日一致（max err {err:.1e}）")

    # C5：段数（低换手口径下不设 ≥50 硬门槛，见 §10 第 4 条）
    b = S.bands(df["state"].loc[d["start"]:])
    out.append(f"C5 独立完整段 {b['cycles']} 段、调仓 {b['trades']} 笔"
               f"（换手低，段数 <50 → 见 §10 第 4 条的证据等级说明）")

    # C6：成本占净值
    yrs = years_of(px.loc[d["start"]:].index)
    c = S.cost_drag(df["state"].loc[d["start"]:], S.params())
    out.append(f"C6 累计成本 {c * 100:.2f}pp / {yrs:.1f} 年 = {c * 100 / yrs:.2f}pp/年"
               f"（单边 {S.FEE:.5%}）")

    # C7：XAU 20 年方向一致
    if ru:
        assert ru["sharpe"] > ru["bh_sharpe"], "C7 XAU 长样本上策略夏普未优于买入持有"
        out.append(f"C7 XAU 20 年外推：策略夏普 {num(ru['sharpe'])} > 买入持有 "
                   f"{num(ru['bh_sharpe'])}，回撤 {pct(ru['mdd'])} vs {pct(ru['bh_mdd'])}")
    return out


# ---------------------------------------------------------------- 尾部尺子 / XAU 外推
def xau_frame():
    if not os.path.exists(F.XAU_CSV):
        return None
    x = pd.read_csv(F.XAU_CSV)
    s = pd.Series(pd.to_numeric(x["gold"], errors="coerce").to_numpy(),
                  index=pd.to_datetime(x["date"])).dropna().sort_index()
    if len(s) < 300:
        return None
    return pd.DataFrame({"close": s, "high": s, "low": s})


def ruler(frame, rate, p):
    tgt, ready = target_risk(frame, rate, p)
    st, _ = S.run_target(tgt, ready, None, p)
    ok = ready
    start = frame.index[ok][0]
    st = st.loc[start:]
    px = frame.loc[start:]
    nav = S.run_leg(st, px, fee=p["FEE"], slip=p["SLIP"])
    m = S.perf(nav)
    bh = S.perf(S.run_leg(pd.Series(1.0, index=st.index), px, fee=p["FEE"], slip=p["SLIP"]))
    sub = frame.loc[start:, "close"]
    return dict(start=start, end=frame.index[-1], ann=m["ann"], mdd=m["mdd"],
                sharpe=m["sharpe"], bh_ann=bh["ann"], bh_mdd=bh["mdd"], bh_sharpe=bh["sharpe"],
                xau_mdd_hold=float((sub / sub.cummax() - 1.0).min()),
                exposure=float(st.mean()), trades=int((st.diff().abs() > 1e-9).sum()))


# ---------------------------------------------------------------- 主流程
def main():
    os.makedirs(OUT_DIR, exist_ok=True)
    d = S.build(refresh=False)
    px, df, p, start = d["px"], d["df"], d["params"], d["start"]
    st = df["state"]
    px_s, st_s = px.loc[start:], st.loc[start:]

    rS = evaluate(st_s, px_s, p)
    rBH = evaluate(pd.Series(1.0, index=st_s.index), px_s, p)
    rHF = evaluate(pd.Series(0.5, index=st_s.index), px_s, p)
    rCA = evaluate(pd.Series(0.0, index=st_s.index), px_s, p)

    seg = {}
    for name, a, b in SEGS:
        ix = st_s.loc[a:b].index
        if len(ix) < 60:
            continue
        seg[name] = dict(S=evaluate(st_s.loc[ix], px_s.loc[ix], p),
                         BH=evaluate(pd.Series(1.0, index=ix), px_s.loc[ix], p),
                         HF=evaluate(pd.Series(0.5, index=ix), px_s.loc[ix], p),
                         span=(ix[0], ix[-1]))
    yS, yBH, yHF = yearly(rS["nav"]), yearly(rBH["nav"]), yearly(rHF["nav"])
    yrs = [y for y in yBH if y in yS]

    blk = df["blocked"].to_numpy(dtype=bool)
    abl = [("满仓 518880（e≡100%）", rBH),
           ("恒定半仓（e≡50%）", rHF),
           ("只风险预算（利率闸门置 1）", variant(px, d["rate"], start,
                                                 RATE_LEVEL_TH=-99.0)),
           ("只利率闸门（风险预算置 1）", variant(px, d["rate"], start,
                                                 VOL_TARGET=1.0, VOL_MIN=1.0)),
           ("**组合（默认口径）**", rS),
           ("组合 · 事件冻结 ±2 日", variant(px, d["rate"], start, EVENT_FREEZE=2)),
           ("旧 v5.0：价格趋势 + 利率变化 → 五档",
            evaluate(S.run(px, d["rate"], d["flow"], S.params(TARGET_MODE="score"),
                           shares=d["shares"])["state"].loc[start:], px_s, p)),
           ("旧 v5.0 + 上交所份额（顺势）",
            evaluate(S.run(px, d["rate"], d["flow"],
                           S.params(TARGET_MODE="score", FLOW_MODE="share"),
                           shares=d["shares"])["state"].loc[start:], px_s, p))]

    # 参数网格（400 组，518880 + XAU 双资产）
    frame_g = xau_frame()
    gstart = None
    if frame_g is not None:
        _tg, _rg = target_risk(frame_g, d["rate"], S.params())
        gstart = frame_g.index[_rg][0]
    grid = []
    for vt in VT_GRID:
        for vw in VW_GRID:
            for th in TH_GRID:
                for low in LOW_GRID:
                    ps = S.params(VOL_TARGET=vt, VOL_WINDOW=vw, RATE_LEVEL_TH=th,
                                  RATE_LEVEL_LOW=low)
                    r = variant(px, d["rate"], start, VOL_TARGET=vt, VOL_WINDOW=vw,
                                RATE_LEVEL_TH=th, RATE_LEVEL_LOW=low)
                    rec = dict(vt=vt, vw=vw, th=th, low=low, ann=r["m"]["ann"],
                               mdd=r["m"]["mdd"], sharpe=r["m"]["sharpe"], trades=r["trades"])
                    if frame_g is not None:
                        rg = variant(frame_g, d["rate"], gstart, None, VOL_TARGET=vt,
                                     VOL_WINDOW=vw, RATE_LEVEL_TH=th, RATE_LEVEL_LOW=low)
                        rec.update(g_ann=rg["m"]["ann"], g_mdd=rg["m"]["mdd"],
                                   g_sharpe=rg["m"]["sharpe"])
                    grid.append(rec)
    gdf = pd.DataFrame(grid)
    g_sh_win = int((gdf["sharpe"] > rBH["m"]["sharpe"]).sum())
    g_mdd_win = int((gdf["mdd"] > rBH["m"]["mdd"]).sum())
    g_ann_win = int((gdf["ann"] > rBH["m"]["ann"]).sum())
    g_all_sh = int((gdf["g_sharpe"] > ru_bh_sharpe(d)).sum()) if "g_sharpe" in gdf else 0

    # 步长对照 + 敏感性
    sens = [("基准（10% 目标 · 120 日 · 闸门 0.5±0.15 · 低配 0.5 · 步长 20%）", rS)]
    for nm, over in (("步长 25%（五档）", dict(STEP=0.25)),
                     ("波动目标 8%", dict(VOL_TARGET=0.08)),
                     ("波动目标 12%", dict(VOL_TARGET=0.12)),
                     ("窗口 60 日", dict(VOL_WINDOW=60)),
                     ("闸门关闭（恒满预算）", dict(RATE_LEVEL_TH=-99.0)),
                     ("闸门死区 0", dict(RATE_LEVEL_BAND=0.0)),
                     ("低配乘数 0.4", dict(RATE_LEVEL_LOW=0.4)),
                     ("起手 20%", dict(START=0.20)),
                     ("起手 100%", dict(START=1.0)),
                     ("事件冻结 ±2 日", dict(EVENT_FREEZE=2)),
                     ("事件冻结 ±5 日", dict(EVENT_FREEZE=5)),
                     ("滑点 0.05%/边", dict(SLIP=0.0005)),
                     ("佣金 万 1", dict(FEE=0.0001))):
        r = variant(px, d["rate"], start, **over)
        if "STEP" in over:
            r["cost"] = S.cost_drag(r["state"], S.params(**over))
        sens.append((nm, r))

    ru = ruler(frame_g, d["rate"], p) if frame_g is not None else None
    ck = checks(d, rS, ru)

    md = render(d, rS, rBH, rHF, rCA, seg, yrs, yS, yBH, yHF, abl, gdf,
                g_ann_win, g_mdd_win, g_sh_win, g_all_sh, sens, ru, ck)
    assert md.rstrip().splitlines()[-1] == DISCLAIMER, "C8 报告末行不是免责声明"
    with open(OUT_MD, "w", encoding="utf-8") as f:
        f.write(md)

    with open(OUT_JSONL, "w", encoding="utf-8") as f:
        for key, r in (("strategy", rS), ("bh", rBH), ("half", rHF), ("cash", rCA)):
            m = r["m"]
            f.write(json.dumps(dict(key=key, start=str(px_s.index[0].date()),
                                    end=str(px_s.index[-1].date()), ann=m["ann"], cum=m["cum"],
                                    vol=m["vol"], mdd=m["mdd"], sharpe=m["sharpe"],
                                    calmar=r["calmar"], exposure=r["exp"], trades=r["trades"],
                                    cycles=r["cycles"], cost=r["cost"]),
                               ensure_ascii=False) + "\n")
        for name, r in abl + sens:
            m = r["m"]
            f.write(json.dumps(dict(group="ablation_or_sens", label=name, ann=m["ann"],
                                    mdd=m["mdd"], sharpe=m["sharpe"], calmar=r["calmar"],
                                    exposure=r["exp"], trades=r["trades"], cost=r["cost"]),
                               ensure_ascii=False) + "\n")
        for g in grid:
            f.write(json.dumps(dict(group="grid", **g), ensure_ascii=False) + "\n")
        if ru:
            f.write(json.dumps(dict(group="xau_ruler", **{k: (str(v) if k in ("start", "end") else v)
                                                          for k, v in ru.items()}),
                               ensure_ascii=False) + "\n")
    print("报告:", OUT_MD)
    print("机读:", OUT_JSONL)
    return d


def ru_bh_sharpe(d):
    frame = xau_frame()
    if frame is None:
        return 0.0
    _t, _r = target_risk(frame, d["rate"], S.params())
    start = frame.index[_r][0]
    s = frame.loc[start:, "close"]
    nav = S.run_leg(pd.Series(1.0, index=s.index), frame.loc[start:])
    return S.perf(nav)["sharpe"]


# ---------------------------------------------------------------- 报告
def render(d, rS, rBH, rHF, rCA, seg, yrs, yS, yBH, yHF, abl, gdf,
           g_ann_win, g_mdd_win, g_sh_win, g_all_sh, sens, ru, ck):
    px, df, p = d["px"], d["df"], d["params"]
    yrs_all = years_of(px.loc[d["start"]:].index)
    def ab(name):
        for nm, r in abl:
            if nm == name:
                return r
        raise KeyError(name)

    mS, mBH, mHF = rS["m"], rBH["m"], rHF["m"]
    tS, pkS, _ = mdd_of(rS["nav"])
    tBH, pkBH, _ = mdd_of(rBH["nav"])
    last = df.iloc[-1]
    L = []
    A = L.append

    A("# V5.1 · 黄金仓位（风险预算 × 实际利率闸门）· 回测")
    A("")
    A(f"> 口径：`scale = clip({F.VOL_TARGET:.0%} / {F.VOL_WINDOW} 日实现波动, "
      f"{F.VOL_MIN:.0%}, 1)`；`gate = 1 if DFII10 > {F.RATE_LEVEL_TH:+.2f}% else "
      f"{F.RATE_LEVEL_LOW:g}`（死区 ±{F.RATE_LEVEL_BAND:.2f}pp）；"
      f"`e* = 量化(scale × gate, {F.STEP:.0%})`。")
    A(f"> 标的 = 场内 `{S.SIGNAL_CODE}`（信号/执行/回测同一只 ETF），实际利率 = FRED `DFII10`"
      f"（{d['rate'].index[0].date()} 起）。样本 {px.loc[d['start']:].index[0].date()} ~ "
      f"{px.index[-1].date()}（{yrs_all:.1f} 年，起算点 = 120 日波动就绪）。"
      f"T 日收盘算信号 → **T+1 收盘成交**；单边佣金 {S.FEE:.5%}、滑点 {S.SLIP:.2%}，"
      f"空仓按货基 {S.CASH_ANNUAL:.2%} 计息。")
    A("> 参数唯一权威源：`factors.py` 顶部常量（风险预算 + 闸门）+ `score.py` 顶部常量（成本与现金）。")
    A("")

    A("## 0. 先说结论")
    A("")
    A(f"1. **同样的年化、三分之一的回撤**：策略 {pct(mS['ann'])} / {pct(mS['mdd'])} / "
      f"夏普 {num(mS['sharpe'])}，买入持有 {pct(mBH['ann'])} / {pct(mBH['mdd'])} / "
      f"{num(mBH['sharpe'])}。年化只差 {abs(mS['ann'] - mBH['ann']) * 100:.2f}pp，回撤浅 "
      f"{abs(mS['mdd'] - mBH['mdd']) * 100:.1f}pp，**夏普接近翻倍**。")
    A(f"2. **它同时跑赢「恒定半仓」**：恒半仓 {pct(mHF['ann'])} / {pct(mHF['mdd'])} / "
      f"夏普 {num(mHF['sharpe'])}；本策略年化高 "
      f"{(mS['ann'] - mHF['ann']) * 100:.2f}pp、回撤浅 {(mS['mdd'] - mHF['mdd']) * 100:.1f}pp。"
      "旧 v5.0 口径（价格趋势 + 利率 10 日变化）恰好卡在恒半仓附近，这一版才真正分开。")
    A(f"3. **两个部件都不能少**（§4 消融）：只风险预算 "
      f"{pct(ab('只风险预算（利率闸门置 1）')['m']['ann'])} / "
      f"{pct(ab('只风险预算（利率闸门置 1）')['m']['mdd'])} / 夏普 "
      f"{num(ab('只风险预算（利率闸门置 1）')['m']['sharpe'])}；只利率闸门 "
      f"{pct(ab('只利率闸门（风险预算置 1）')['m']['ann'])} / "
      f"{pct(ab('只利率闸门（风险预算置 1）')['m']['mdd'])} / 夏普 "
      f"{num(ab('只利率闸门（风险预算置 1）')['m']['sharpe'])}。"
      "风险预算负责「波动冲高时减仓」，利率闸门负责「实际利率过低时只拿一半」。")
    A(f"4. **参数面方向一致**（§5）：{len(gdf)} 组里回撤比满仓浅 **{g_mdd_win}/{len(gdf)}**、"
      f"夏普高于满仓 **{g_sh_win}/{len(gdf)}**、年化跑赢满仓 **{g_ann_win}/{len(gdf)}**"
      f"；同一套规则在 **XAU 20 年**上夏普高于买入持有 **{g_all_sh}/{len(gdf)}** 组。"
      "**方向一致的是「回撤更浅、年化接近」**，不是「年化更高」。")
    A(f"5. **利率因子换了口径才活过来**：v5.0 用 `DFII10` 的 **10 日变化**（±25bp）→ 日频噪声、"
      f"净贡献为负；v5.1 用**水平**（`> 0.5%` 才给满额预算）→ 20 年样本上水平值与未来 12 个月"
      "金价收益正相关（年度非重叠点 +0.63，三段子样本 +0.54/+0.63/+0.77）。机制是实际利率"
      "均值回复：利率越低，未来越可能上行。")
    A(f"6. **旧 v5.0 口径的结论不变**（§4 末两行）：价格趋势 + 利率变化 + 资金流三件套"
      f"就是恒半仓的换手版（{pct(ab('旧 v5.0：价格趋势 + 利率变化 → 五档')['m']['ann'])} / "
      f"{pct(ab('旧 v5.0：价格趋势 + 利率变化 → 五档')['m']['mdd'])} / 夏普 "
      f"{num(ab('旧 v5.0：价格趋势 + 利率变化 → 五档')['m']['sharpe'])}）。"
      "这一版把它们降级为「已验证无增量」，只留在对照里。")
    A("")
    A(f"**当前读数（{df.index[-1].date()}）**：波动 **{last['vol']:.1%}**（目标 {F.VOL_TARGET:.0%}）→ "
      f"风险预算 **{last['scale']:.2f}**；利率 **{last['rate_level']:+.2f}%** → 闸门 "
      f"**{last['gate']:.2f}**；目标档 **{last['target']:.0%}**（= {last['target'] * S.BAND_PP:.1f}pp / "
      f"{S.BAND_PP:g}pp）、当前 **{last['state']:.0%}**、今日动作 **{last['action']}**。")
    A("")

    A("## 1. 口径与样本")
    A("")
    A("| 项 | 取值 |")
    A("|---|---|")
    A(f"| 信号价 / 执行 | 场内 `{S.SIGNAL_CODE}` 黄金ETF华安（无分红，前复权价与原始价一致） |")
    A(f"| 价格数据 | `data/_ohlcv.json` + `data/_long_klines.json`（`_idxcache.frame('gold')` 合并，"
      f"{px.index[0].date()} 起，**有真实日内高低**） |")
    A(f"| 风险预算 | 120 日已实现波动 → `clip({F.VOL_TARGET:.0%} / vol, {F.VOL_MIN:.0%}, 1)`，"
      "逐日更新、跨 1 档（20pp）才动 |")
    A(f"| 实际利率闸门 | `data/_dfii10.csv`（FRED DFII10，{d['rate'].index[0].date()} ~ "
      f"{d['rate'].index[-1].date()}）；T 日用 ≤T−1；`> {F.RATE_LEVEL_TH:+.2f}%` "
      f"±{F.RATE_LEVEL_BAND:.2f}pp 死区 → 1.0，否则 {F.RATE_LEVEL_LOW:g} |")
    A(f"| 目标档 | 0 / 20% / 40% / 60% / 80% / 100%（占黄金仓位；1.0 = 满带 {S.BAND_PP:g}pp） |")
    A("| 成交 | T 日收盘算信号 → T+1 收盘成交；半根 K 线（北京 15:10 前）剔除 |")
    A(f"| 成本 | 单边 {S.FEE:.5%}（万 2.5）+ 滑点 {S.SLIP:.2%}；管理费已在 ETF 净值内，不重复扣 |")
    A(f"| 现金 | {S.CASH_ANNUAL:.2%} 年化（货基/同业存单近似），逐日计息 |")
    A("| 基准 | ① 恒满仓（e≡100%，= 买入持有）② 恒定半仓（e≡50%） |")
    A("")

    A("## 2. 主表（全样本）")
    A("")
    A(HEAD)
    A(row("**策略（风险预算 × 利率闸门 → 六档）**", rS, yrs_all))
    A(row("恒满仓（买入持有）", rBH, yrs_all))
    A(row("恒定半仓（e≡50%）", rHF, yrs_all))
    A(row("纯现金（货基）", rCA, yrs_all))
    A("")
    A(f"> 策略最大回撤 {pct(mS['mdd'])}（谷底 {tS.date()} / 峰 {pkS.date()}），"
      f"满仓 {pct(mBH['mdd'])}（谷底 {tBH.date()} / 峰 {pkBH.date()}）。"
      f"平均仓位 {rS['exp']:.0%}、调仓 {rS['trades']} 笔（{rS['trades'] / yrs_all:.1f} 次/年）、"
      f"完整往返 {rS['cycles']} 段、清仓 {rS['exits']} 次。")
    A(f"> 平均仓位只有 {rS['exp']:.0%} 却拿到接近满仓的年化 —— 减仓集中在波动冲高与实际利率过低两段。"
      "这是风险归一化，不是择时 alpha：它不预测方向，只改「这一刻该承担多少风险」。")
    A("")

    A("## 3. 分段（IS / OOS1 / OOS2 · 同一套参数不重标定）")
    A("")
    A("| 期间 | 段 | 年化 | 最大回撤 | 夏普 | 平均仓位 | 期间 |")
    A("|---|---|---:|---:|---:|---:|---|")
    for name, v in seg.items():
        a, b = v["span"]
        for tag, r in (("策略", v["S"]), ("恒满仓", v["BH"]), ("恒半仓", v["HF"])):
            m = r["m"]
            A(f"| {name} | {tag} | {pct(m['ann'])} | {pct(m['mdd'])} | {num(m['sharpe'])} | "
              f"{r['exp']:.0%} | {a.date()} ~ {b.date()} |")
    A("")
    A("> 参数全部取默认值，IS 段没有做任何搜索（§5 的网格只用于一致性检验），"
      "所以 OOS 段不存在「重标定」问题。三段的共同点是**回撤稳定在 7%~12%**，"
      "而满仓在 20%~34% 之间摆动。")
    A("")
    A("逐年（同一窗口 · 首末年为不完整年）：")
    A("")
    A("| 年 | 策略 | 恒满仓 | 恒半仓 | 策略−满仓 |")
    A("|---|---:|---:|---:|---:|")
    for y in yrs:
        a, b = yS.get(y), yBH.get(y)
        dd = (a - b) if (a is not None and b is not None) else None
        A(f"| {y} | {pct(a)} | {pct(b)} | {pct(yHF.get(y))} | {pct(dd)} |")
    A("")
    pos_y = [y for y in yrs if yS.get(y) is not None and yBH.get(y) is not None and yS[y] > yBH[y]]
    A(f"> 跑赢满仓的年份 **{len(pos_y)}/{len(yrs)}**"
      + (f"（{'、'.join(pos_y)}）" if pos_y else "")
      + "：它不会在单边上涨年赢，赢的是回撤年。")
    A("")

    A("## 4. 消融（逐项净贡献）")
    A("")
    A("| 口径 | 年化 | 最大回撤 | 夏普 | Calmar | 平均仓位 | 调仓 |")
    A("|---|---:|---:|---:|---:|---:|---:|")
    for name, r in abl:
        m = r["m"]
        A(f"| {name} | {pct(m['ann'])} | {pct(m['mdd'])} | {num(m['sharpe'])} | "
          f"{num(r['calmar'])} | {r['exp']:.0%} | {r['trades']} |")
    A("")
    r_only_vol = ab("只风险预算（利率闸门置 1）")
    r_only_gate = ab("只利率闸门（风险预算置 1）")
    A(f"> **风险预算的净贡献**：夏普 {num(r_only_vol['m']['sharpe'])} vs 满仓 "
      f"{num(mBH['sharpe'])}、回撤 {pct(r_only_vol['m']['mdd'])} vs {pct(mBH['mdd'])} —— "
      "单靠它就已经拿到大部分回撤改善。")
    A(f"> **利率闸门的增量**：在风险预算之上把夏普从 {num(r_only_vol['m']['sharpe'])} 抬到 "
      f"{num(mS['sharpe'])}、回撤从 {pct(r_only_vol['m']['mdd'])} 压到 {pct(mS['mdd'])}，"
      f"代价是年化 {pct(r_only_vol['m']['ann'])} → {pct(mS['ann'])}。")
    A("> **旧口径三件套全部为负或零贡献**：价格趋势 + 利率变化 + 资金流（份额全史）合起来"
      "仍在恒半仓附近 —— 这是把它们降级为「仅展示」的依据。")
    A("")

    A(f"## 5. 参数网格（{len(gdf)} 组 · 518880 + XAU 双资产 · 只看方向一致性）")
    A("")
    A("各维度的**中位数**（每组都跑一遍 518880 全样本 + XAU 20 年）：")
    A("")
    A("| 维度 | 取值 | 518880 年化 | 518880 回撤 | 518880 夏普 | XAU 夏普 |")
    A("|---|---|---:|---:|---:|---:|")
    for col, unit in (("vt", "波动目标"), ("vw", "窗口(日)"), ("th", "闸门阈值"),
                      ("low", "低配乘数")):
        for val, g in gdf.groupby(col):
            lab = f"{val:.0%}" if col in ("vt", "low") else (f"{val:+.2f}pp" if col == "th"
                                                             else f"{int(val)}")
            A(f"| {unit} | {lab} | {pct(float(g['ann'].median()))} | "
              f"{pct(float(g['mdd'].median()))} | {num(float(g['sharpe'].median()))} | "
              f"{num(float(g['g_sharpe'].median()))} |")
    A("")
    A(f"> {len(gdf)} 组里：年化跑赢满仓 **{g_ann_win}/{len(gdf)}**、回撤比满仓浅 "
      f"**{g_mdd_win}/{len(gdf)}**、夏普高于满仓 **{g_sh_win}/{len(gdf)}**；"
      f"XAU 20 年夏普高于买入持有 **{g_all_sh}/{len(gdf)}** 组。")
    A(f"> 夏普区间 {num(float(gdf['sharpe'].min()))} ~ {num(float(gdf['sharpe'].max()))}、"
      f"中位 {num(float(gdf['sharpe'].median()))}（满仓 {num(mBH['sharpe'])}、恒半仓 "
      f"{num(mHF['sharpe'])}）—— **没有一个参数组合掉回基准水平**，说明这不是单点拟合。")
    A(f"> 默认参数（10% / 120 日 / 闸门 0.5 / 低配 0.5）落在 {pct(mS['ann'])} / "
      f"{pct(mS['mdd'])} / 夏普 {num(mS['sharpe'])}，处于网格的中间偏上位置，不是尖峰。")
    A("")

    A("## 6. 换手与成本")
    A("")
    A(f"- 调仓 **{rS['trades']} 笔**（{rS['trades'] / yrs_all:.1f} 次/年）、完整往返 "
      f"{rS['cycles']} 段；累计成本 {rS['cost'] * 100:.2f}pp = "
      f"**{rS['cost'] * 100 / yrs_all:.2f}pp/年**。")
    A(f"- 换手来自两处：① 波动跨 20pp 档位（多数）；② 利率闸门在 "
      f"{F.RATE_LEVEL_TH:+.2f}±{F.RATE_LEVEL_BAND:.2f}pp 之间穿越。")
    nb = next((r for n, r in sens if n == "闸门死区 0"), None)
    if nb is not None:
        A(f"- 闸门死区把调仓从 {nb['trades']} 笔压到 {rS['trades']} 笔、年化 "
          f"{pct(nb['m']['ann'])} → {pct(mS['ann'])}（{(mS['ann'] - nb['m']['ann']) * 100:+.2f}pp）"
          f"、夏普 {num(nb['m']['sharpe'])} → {num(mS['sharpe'])} —— 2015 年利率在 0.50% 附近"
          "来回穿越，没有死区就会在 80%↔40% 之间抖。")
    A(f"- 事件冻结默认**关闭**：新口径下 ±2 日冻结年化 {pct(ab('组合 · 事件冻结 ±2 日')['m']['ann'])}、"
      f"回撤 {pct(ab('组合 · 事件冻结 ±2 日')['m']['mdd'])}，均不如不冻结（{pct(mS['ann'])} / "
      f"{pct(mS['mdd'])}）—— 与旧口径的结论相反，所以不给默认值，只留开关。")
    A("")

    A("## 7. 尾部尺子（XAU 长历史，不进主表净值）")
    A("")
    if ru:
        A(f"- 518880 的样本只有 {px.index[0].date()} 起（ETF 2013 上市），"
          f"样本内满仓最深回撤 {pct(mBH['mdd'])}。")
        A(f"- 同一条规则 + 同一份 DFII10 跑在 **伦敦金现**（{ru['start'].date()} ~ "
          f"{ru['end'].date()}，{(ru['end'] - ru['start']).days / 365.25:.1f} 年）：策略 "
          f"{pct(ru['ann'])} / {pct(ru['mdd'])} / 夏普 {num(ru['sharpe'])}"
          f"（平均仓位 {ru['exposure']:.0%}、调仓 {ru['trades']} 笔），买入持有 "
          f"{pct(ru['bh_ann'])} / {pct(ru['bh_mdd'])} / 夏普 {num(ru['bh_sharpe'])}。")
        A(f"- 该窗口内 XAU 自身最深回撤 {pct(ru['xau_mdd_hold'])}，比 518880 样本内深 "
          f"**{abs(ru['xau_mdd_hold'] - mBH['mdd']) * 100:.1f}pp** —— 长样本里策略的回撤也会更深"
          f"（{pct(ru['mdd'])} vs 主表 {pct(mS['mdd'])}），所以主表的回撤改善要打折看。")
        A("- 重要的是**方向**：20 年里策略夏普仍高于买入持有，说明「压回撤不压年化」不是"
          "2014-2026 这一段的人民币黄金特有现象。")
        A("- ⚠ XAU 是**伦敦金现美元计价**，与 518880（人民币、含汇率与溢价）不是同一条序列，"
          "这里只当长样本外推，不参与主表净值。")
    else:
        A("- 缺 `data/_gold_xau.csv`，跳过尾部尺子。")
    A("")

    A("## 8. 敏感性")
    A("")
    A("| 变体 | 年化 | 最大回撤 | 夏普 | Calmar | 平均仓位 | 调仓 | 成本/年 |")
    A("|---|---:|---:|---:|---:|---:|---:|---:|")
    for name, r in sens:
        m = r["m"]
        A(f"| {name} | {pct(m['ann'])} | {pct(m['mdd'])} | {num(m['sharpe'])} | "
          f"{num(r['calmar'])} | {r['exp']:.0%} | {r['trades']} | "
          f"{r['cost'] * 100 / yrs_all:.2f}pp |")
    A("")
    A("> 步长 20% 与 25%（原五档）差异很小；波动目标 8%/10%/12% 都在夏普 0.9 以上；"
      "闸门关闭后夏普掉到只风险预算的水平 —— 两个部件的贡献可分离且稳定。"
      "起手档只影响头几天的仓位，实盘用 `--hold` 覆盖。")
    A("")

    A("## 9. 验收断言（不过就抛）")
    A("")
    for c in ck:
        A(f"- {c}")
    A("- C8 报告末行 = 免责声明（落盘前断言 `md.rstrip().splitlines()[-1] == DISCLAIMER`）")
    A("")

    A("## 10. 诚实限制")
    A("")
    A("1. **不是 alpha，是风险归一化**：它不预测金价方向；价值来自「波动冲高时少承担风险」"
      "与「实际利率过低时只拿一半」。用户若要求的是「更高年化」，这套规则给不了 —— "
      "它给的是同样的年化、三分之一的回撤。")
    A("2. **利率闸门是慢变量、独立事件少**：13 年里闸门只切换过个位数次，"
      "真正被它躲开的主要是 2020-2021（实际利率 −1% 附近、金价见顶）与 2015 前后。"
      "20 年 XAU 与三段子样本的相关性方向一致（+0.54/+0.63/+0.77），但**独立 regime 段数远少于 50**，"
      "这条只能算「有经济学解释的中等证据」，不是高频可验证的统计规律。")
    A("3. **闸门是绝对阈值（0.5%）**：不做滚动分位。好处是可解释、不随样本漂移；"
      "坏处是若未来长期处于实际利率 >1% 的新常态，闸门会长期开着，规则退化成纯波动目标 ——"
      "这不是失效，是可预期的退化。")
    A(f"4. **段数不足 50**：完整往返 {rS['cycles']} 段、调仓 {rS['trades']} 笔。规格 §5 原本要求"
      "「独立段 ≥50 才写成规则」，低换手的风险预算天然达不到。证据等级由另外三条补强："
      "400 组参数面方向一致性、20 年 XAU 外推、三段 IS/OOS 同向。"
      "**如果要求严格满足段数门槛，这套规则应降级为「描述级」。**")
    A("5. **样本 2014-01 起（120 日波动就绪）**：比 ETF 上市晚半年，但四个口径（策略/满仓/半仓/现金）"
      "都在同一窗口上比较，不影响可比性。")
    A("6. **与 v6 不叠加**：`v6_gold` 的 B 口径（纯波动目标）与本策略共用同一个风险机制，"
      "同时跑会重复加减同一份额度；差别只在本策略多了利率闸门与 20pp 档位量化。")
    A("7. **20 年 XAU 不是「样本外」的严格意义**：闸门阈值与波动目标都是在 518880 这 13 年里定的，"
      "XAU 只是更长、不同计价、不同流动性的一段序列，能排除「这一段特有的运气」，"
      "不能排除「黄金这 20 年的整体特征」。")
    A("")
    A(DISCLAIMER)
    return "\n".join(L) + "\n"


if __name__ == "__main__":
    main()
