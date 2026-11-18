# -*- coding: utf-8 -*-
"""V5 黄金计分仓位 · 回测与稳健性（规格 §5 回测协议 / §6 验收）。

跑六件事：
1. **主表**：策略 vs 恒满仓（买入持有）vs 恒半仓 vs 纯现金；
2. **分段**：IS 2013-07~2019 / OOS1 2020~2023 / OOS2 2024~今 + 逐年；
3. **消融**：满仓 / 单因子 / 两因子 / 两因子不收缩，逐项报净贡献；
4. **参数网格**：MA(5,10 / 10,20) × 突破(20/40/60) × 利率阈值(15/25/40bp) × 滞后(1/2 档)
   = 24 组，只看**方向一致性**，不用来选参；
5. **尾部尺子**：518880 只有 2013-07 起，用 `data/_gold_xau.csv`（伦敦金现 2006 起）
   当「跌幅尺子」复核尾部回撤是否被低估（**XAU 不进净值**）；
6. **验收断言**：T+1 成交、DFII10 滞后、取值域、无前视、段数、成本占比、免责声明。

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

SEGS = [("IS 2013-07~2019-12", "2013-07-01", "2019-12-31"),
        ("OOS1 2020-01~2023-12", "2020-01-01", "2023-12-31"),
        ("OOS2 2024-01~今", "2024-01-01", "2099-12-31")]


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


def window(state, px, start):
    st = state.loc[start:]
    return evaluate(st, px.loc[start:])


# ---------------------------------------------------------------- 断言（规格 §6）
def checks(d):
    """C1~C7：不过就抛。"""
    df, px = d["df"], d["px"]
    out = []

    # C1：T 日信号 → T+1 成交
    st = df["state"].to_numpy(dtype=float)
    act = df["action"].to_numpy()
    tgt = df["target"].to_numpy(dtype=float)
    code = {"买入": 1, "卖出": -1}
    n_dec = 0
    for t in range(len(df) - 1):
        a = act[t]
        if a in code:
            n_dec += 1
            assert abs(st[t + 1] - tgt[t]) < 1e-9, \
                f"C1 {df.index[t].date()} 的决策未在 T+1 成交（state={st[t+1]} tgt={tgt[t]}）"
    out.append(f"C1 T 信号 / T+1 成交：{n_dec} 笔决策全部在次一交易日收盘才落到 `state`")

    # C2：DFII10 只用 ≤T−1 的值
    used = pd.to_datetime(df["rate_date"]).to_numpy()
    assert (used < df.index.to_numpy()).all(), "C2 实际利率用了当日或未来值"
    same_day = F.f_rate(d["rate"], px.index, F.params(RATE_LAG=0))[2]
    diff = int((same_day.round() != df["rate_chg"].round()).sum())
    assert diff > 0, "C2 滞后检查是空操作（当日值与滞后值完全相同）"
    out.append(f"C2 DFII10 滞后 1 日：全部观测日 < 决策日；改成当日值会有 {diff} 天读数变化"
               "（说明滞后不是空操作）")

    # C3：取值域
    assert int(df["score"].min()) >= -3 and int(df["score"].max()) <= 3
    grid = {0.0, 0.25, 0.5, 0.75, 1.0}
    assert set(np.round(df["target"].unique(), 6)) <= grid
    assert set(np.round(st, 6)) <= grid
    assert set(act) <= {"买入", "卖出", "持有"}
    out.append("C3 score ∈ [−3,3]、e* ∈ {0,.25,.5,.75,1}、动作 ∈ {买入,卖出,持有}")

    # C4：无前视 / OOS 不重算
    cut = px.index[-31]
    full = S.run(px, d["rate"], d["flow"], S.params(), shares=d["shares"])["state"].loc[:cut]
    part = S.run(px.loc[:cut], d["rate"], d["flow"], S.params(),
                 shares=d["shares"])["state"]
    err = float((full - part).abs().max())
    assert err < 1e-9, f"C4 存在前视（{err:.2e}）"
    out.append(f"C4 无前视：截掉最后 30 个交易日重算，历史状态逐日一致（max err {err:.1e}）；"
               "参数取规格默认值、OOS 段不重标定")

    # C5：段数
    b = S.bands(df["state"])
    assert b["cycles"] >= 50, f"C5 独立完整段只有 {b['cycles']} 段（<50），只能降级为描述级"
    out.append(f"C5 独立完整段 {b['cycles']} 段（≥50，够写成规则）、调仓 {b['trades']} 笔")

    # C6：成本占净值
    yrs = years_of(px.loc[d["start"]:].index)
    c = S.cost_drag(df["state"], S.params())
    out.append(f"C6 累计成本 {c * 100:.2f}pp / {yrs:.1f} 年 = {c * 100 / yrs:.2f}pp/年"
               f"（单边 {S.FEE:.5%}）")
    return out


# ---------------------------------------------------------------- 尾部尺子
def ruler(px, p):
    """用 XAU 长历史当「跌幅尺子」：同一条规则跑在 2006 起的伦敦金现上（**不进净值**）。"""
    if not os.path.exists(F.XAU_CSV):
        return None
    x = pd.read_csv(F.XAU_CSV)
    s = pd.Series(pd.to_numeric(x["gold"], errors="coerce").to_numpy(),
                  index=pd.to_datetime(x["date"])).dropna().sort_index()
    if len(s) < 300:
        return None
    frame = pd.DataFrame({"close": s, "high": s, "low": s})
    df = S.run(frame, F.read_rate(), None, p).dropna(subset=["target"])
    st = df["state"]
    nav = S.run_leg(st, s.loc[st.index])
    m = S.perf(nav)
    bh = S.perf(S.run_leg(pd.Series(1.0, index=st.index), s.loc[st.index]))
    # 同一段窗口内 518880 的最深回撤，用来比「短样本低估了多少」
    a, b = df.index[0], df.index[-1]
    sub = s.loc[a:b]
    return dict(start=a, end=b, ann=m["ann"], mdd=m["mdd"], sharpe=m["sharpe"],
                bh_ann=bh["ann"], bh_mdd=bh["mdd"], bh_sharpe=bh["sharpe"],
                xau_mdd_hold=float((sub / sub.cummax() - 1.0).min()),
                exposure=float(st.mean()))


# ---------------------------------------------------------------- 主流程
def main():
    os.makedirs(OUT_DIR, exist_ok=True)
    d = S.build(refresh=False)
    px, df, p = d["px"], d["df"], d["params"]
    start = d["start"]
    st = df["state"]

    px_s, st_s = px.loc[start:], st.loc[start:]
    rS = evaluate(st_s, px_s, p)
    rBH = evaluate(pd.Series(1.0, index=st_s.index), px_s, p)
    rHF = evaluate(pd.Series(0.5, index=st_s.index), px_s, p)
    rCA = evaluate(pd.Series(0.0, index=st_s.index), px_s, p)

    # 分段（IS / OOS1 / OOS2）
    seg = {}
    for name, a, b in SEGS:
        ix = st_s.loc[a:b].index
        if len(ix) < 60:
            continue
        seg[name] = dict(S=evaluate(st_s.loc[ix], px_s.loc[ix], p),
                         BH=evaluate(pd.Series(1.0, index=ix), px_s.loc[ix], p),
                         HF=evaluate(pd.Series(0.5, index=ix), px_s.loc[ix], p),
                         span=(ix[0], ix[-1]))
    yrs = [y for y in yearly(rBH["nav"]) if y in yearly(rS["nav"])]
    yS, yBH, yHF = yearly(rS["nav"]), yearly(rBH["nav"]), yearly(rHF["nav"])

    # 消融
    def variant(**kw):
        ps = S.params(**kw)
        f = S.run(px, d["rate"], d["flow"], ps, shares=d["shares"])
        sub = f.loc[start:]
        return evaluate(sub["state"], px_s, ps)

    abl = [("满仓 518880（e≡100%）", rBH),
           ("恒定半仓（e≡50%）", rHF),
           ("只价格趋势 f_p（f_r 记 0）", variant(USE_R=False)),
           ("只实际利率 f_r（f_p 记 0）", variant(USE_P=False)),
           ("两因子（默认 · 缺因子→低置信度→极端档收缩）", rS),
           ("两因子 · 不收缩（照原表，可到 100%/0%）", variant(SHRINK="off", MISSING_PENALTY=False)),
           ("两因子 · 全档收缩（75%→50%、25%→50%）", variant(SHRINK="all")),
           ("两因子 · 滞后 2 档（≥50pp 才动）", variant(HYST=2)),
           ("只份额净申购 f_f（全史 · 顺势）", variant(FLOW_MODE="share", USE_P=False, USE_R=False)),
           ("三因子 · 份额 3 日 · 顺势（规格方向）", variant(FLOW_MODE="share")),
           ("三因子 · 份额 3 日 · 逆势", variant(FLOW_MODE="share", FLOW_SIGN=-1))]

    # 资金流口径网格（全史份额 vs 120 天主力的对比，规格 §1 括号条款的复核）
    flow_grid = []
    for mode, tag in (("share", "份额（全史）"), ("netin", "主力净流入（120 天）")):
        for k in (2, 3, 5):
            for sgn, stg in ((1, "顺势"), (-1, "逆势")):
                ps = S.params(FLOW_MODE=mode, FLOW_STREAK=k, FLOW_SIGN=sgn)
                f = S.run(px, d["rate"], d["flow"], ps, shares=d["shares"])
                r = evaluate(f.loc[start:, "state"], px_s, ps)
                flow_grid.append(dict(mode=tag, streak=k, sign=stg, ann=r["m"]["ann"],
                                      mdd=r["m"]["mdd"], sharpe=r["m"]["sharpe"],
                                      exp=r["exp"], trades=r["trades"]))
    fdf = pd.DataFrame(flow_grid)

    # 参数网格（方向一致性，不用于选参）
    grid = []
    for ma in ((5, 10), (10, 20)):
        for brk in (20, 40, 60):
            for th in (15.0, 25.0, 40.0):
                for hy in (1, 2):
                    over = dict(MA_FAST=ma[0], MA_SLOW=ma[1], BRK_WIN=brk, RATE_TH=th, HYST=hy)
                    ps = S.params(**over)
                    f = S.run(px, d["rate"], d["flow"], ps, shares=d["shares"])
                    r = evaluate(f.loc[start:, "state"], px_s, ps)
                    grid.append(dict(ma=f"{ma[0]}/{ma[1]}", brk=brk, th=th, hyst=hy,
                                     ann=r["m"]["ann"], mdd=r["m"]["mdd"],
                                     sharpe=r["m"]["sharpe"], trades=r["trades"]))
    gdf = pd.DataFrame(grid)
    g_ann_win = int((gdf["ann"] > rBH["m"]["ann"]).sum())
    g_mdd_win = int((gdf["mdd"] > rBH["m"]["mdd"]).sum())     # 回撤更浅
    g_sh_win = int((gdf["sharpe"] > rBH["m"]["sharpe"]).sum())

    # 敏感性
    sens = [("基准（MA5/10 · 突破 20 日 · 利率 ±25bp · 滞后 1 档）", rS)]
    for nm, over in (("起手 25%（起手就低配）", dict(START=0.25)),
                     ("起手 75%", dict(START=0.75)),
                     ("低配档 0%（≤−2 清出）", dict(LOW_POS=0.0)),
                     ("低配档 10%（≤−2 留观察仓）", dict(LOW_POS=0.10)),
                     ("事件冻结 ±5 日", dict(EVENT_FREEZE=5)),
                     ("事件冻结关闭", dict(EVENT_FREEZE=0)),
                     ("滑点 0.05%/边", dict(SLIP=0.0005)),
                     ("佣金 万 1（0.01%）", dict(FEE=0.0001))):
        sens.append((nm, variant(**over)))

    ru = ruler(px, p)
    ck = checks(d)

    md = render(d, rS, rBH, rHF, rCA, seg, yrs, yS, yBH, yHF, abl, gdf, g_ann_win,
                g_mdd_win, g_sh_win, sens, ru, ck, fdf)
    assert md.rstrip().splitlines()[-1] == DISCLAIMER, "C7 报告末行不是免责声明"
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
    print("报告:", OUT_MD)
    print("机读:", OUT_JSONL)
    return d


# ---------------------------------------------------------------- 报告
def render(d, rS, rBH, rHF, rCA, seg, yrs, yS, yBH, yHF, abl, gdf, g_ann_win,
           g_mdd_win, g_sh_win, sens, ru, ck, fdf):
    px, df, p = d["px"], d["df"], d["params"]
    st = df["state"]
    yrs_all = years_of(px.loc[d["start"]:].index)
    def ab(name):
        for nm, r in abl:
            if nm == name:
                return r
        raise KeyError(name)

    mS, mBH, mHF = rS["m"], rBH["m"], rHF["m"]
    tS, pkS, ddS = mdd_of(rS["nav"])
    tBH, pkBH, ddBH = mdd_of(rBH["nav"])
    last = df.iloc[-1]
    L = []
    A = L.append

    A("# V5 变种 · 黄金计分仓位（价格趋势 + 实际利率）· 回测")
    A("")
    A(f"> 规格：`E:\\qianwen\\jijin\\gold_rules_v2.md`（计分 + 五档目标仓位 + 滞后防抖 + 事件冻结）。"
      f"标的 = 场内 `{S.SIGNAL_CODE}`（信号/执行/回测同一只 ETF），实际利率 = FRED `DFII10`"
      f"（{d['rate'].index[0].date()} 起），资金流 = 东财主力净流入（**只有 120 天** → 按规格剔除计分），"
      f"另用**上交所每日份额**（全史）做了替代口径的完整复核（§8.2）。")
    A(f"> 样本 {px.loc[d['start']:].index[0].date()} ~ {px.index[-1].date()}（{yrs_all:.1f} 年，"
      f"起算点 = 因子全部就绪）；T 日收盘算信号 → **T+1 收盘成交**；"
      f"单边佣金 {S.FEE:.5%}、滑点 {S.SLIP:.2%}，空仓按货基 {S.CASH_ANNUAL:.2%} 计息。")
    A("> 参数唯一权威源：`factors.py` 顶部常量（因子与映射）+ `score.py` 顶部常量（成本与现金）。")
    A("")

    A("## 0. 先说结论")
    A("")
    A(f"1. **它买到的是回撤，不是收益**：年化 {pct(mS['ann'])} vs 满仓 {pct(mBH['ann'])}"
      f"（少赚 {abs(mS['ann'] - mBH['ann']) * 100:.2f}pp/年），最大回撤 {pct(mS['mdd'])} vs "
      f"{pct(mBH['mdd'])}（**改善 {abs(mS['mdd'] - mBH['mdd']) * 100:.1f}pp**），"
      f"夏普 {num(mS['sharpe'])} vs {num(mBH['sharpe'])}。这与规格 §7 的预期一致。")
    A(f"2. **但它没跑赢「什么都不判断的恒定半仓」**：恒半仓 {pct(mHF['ann'])} / {pct(mHF['mdd'])} / "
      f"夏普 {num(mHF['sharpe'])}，与策略（{pct(mS['ann'])} / {pct(mS['mdd'])} / "
      f"{num(mS['sharpe'])}）几乎逐项重合 —— 平均仓位 {rS['exp']:.0%} vs 50%。"
      "**两个因子在 13 年里给出的净增量接近 0**（§4 消融）。")
    A(f"3. **两个因子的边际贡献极不对称**：只留价格趋势年化 {pct(ab('只价格趋势 f_p（f_r 记 0）')['m']['ann'])}、"
      f"只留实际利率 {pct(ab('只实际利率 f_r（f_p 记 0）')['m']['ann'])}、满仓 {pct(mBH['ann'])}；"
      "价格趋势是「什么时候下车」的粗信号，实际利率在日频上只提供噪声级信息"
      "（`DFII10` 的 10 日变化与黄金日收益同期相关本来就弱，见 §5）。")
    A(f"4. **资金流因子补齐全史后依然不行**（本轮新增，§8.2）：东财「主力净流入」硬上限 120 天，"
      "但 ETF 的真实资金流是**申赎**——上交所每日份额有 2013-07-29 起全史（3234 日、0 缺失），"
      f"拿它当 `f_f` 重跑：**顺势口径 {pct(ab('三因子 · 份额 3 日 · 顺势（规格方向）')['m']['ann'])} / "
      f"{pct(ab('三因子 · 份额 3 日 · 顺势（规格方向）')['m']['mdd'])}**（回撤更差）、"
      f"**逆势口径 {pct(ab('三因子 · 份额 3 日 · 逆势')['m']['ann'])} / "
      f"{pct(ab('三因子 · 份额 3 日 · 逆势')['m']['mdd'])}**（回撤修回来、年化掉下去），"
      "两种方向都跑输恒半仓。**份额是「涨了才申购」的结果变量，不是预测变量**；"
      "规格 §1 把它剔除是对的，现在这条结论有全史数据背书（探针见 "
      "`research/flow_source/_probe_flow.md`）。")
    A(f"5. **它比同族 v6-B（波动目标）差**：v6-B 在同一只 ETF 的 12.1 年里做到 "
      "+9.06% / −16.12% / 夏普 0.74（共同窗口 +10.25% / −16.12% / 0.86），"
      f"本策略 {pct(mS['ann'])} / {pct(mS['mdd'])} / {num(mS['sharpe'])}。"
      "**想要「压回撤」，本仓库的既有答案仍然是波动目标，不是多因子计分**（§10 边界第 1 条）。")
    A("")
    A(f"**当前读数（{df.index[-1].date()}）**：score = **{int(last['score']):+d}**"
      f"（f_p {int(last['f_p']):+d} / f_r {int(last['f_r']):+d}，{last['conf_eff']}置信度）→ "
      f"目标档 **{last['target']:.0%}**（= {last['target'] * S.BAND_PP:.1f}pp / {S.BAND_PP:g}pp）、"
      f"当前 **{last['state']:.0%}**、今日动作 **{last['action']}**。")
    A("")

    A("## 1. 口径与样本")
    A("")
    A("| 项 | 取值 |")
    A("|---|---|")
    A(f"| 信号价 / 执行 | 场内 `{S.SIGNAL_CODE}` 黄金ETF华安（无分红，前复权价与原始价一致） |")
    A(f"| 价格数据 | `data/_ohlcv.json` + `data/_long_klines.json`（`_idxcache.frame('gold')` 合并，"
      f"{px.index[0].date()} 起，**有真实日内高低**） |")
    A(f"| 实际利率 | `data/_dfii10.csv`（FRED DFII10，{d['rate'].index[0].date()} ~ "
      f"{d['rate'].index[-1].date()}），**T 日用 ≤T−1 的值** |")
    A("| 资金流（主口径） | `data/_flow_518880.csv`（东财 `fflow/daykline`，**只回 120 根**）→ 按规格 §1 剔除计分，仅当日提示 |")
    A(f"| 资金流（替代口径·仅研究） | `data/_etf_shares_518880.csv`（**上交所每日份额** `TOT_VOL`，"
      f"{d['shares'].index[0].date()} ~ {d['shares'].index[-1].date()}，"
      f"{len(d['shares'])} 日 0 缺失）→ `Δ份额 × 收盘价 = 净申购`，§8.2 复核 |")
    A(f"| 目标档 | 0 / 25% / 50% / 75% / 100%（占黄金仓位；1.0 = 满带 {S.BAND_PP:g}pp） |")
    A(f"| 成交 | T 日收盘算信号 → T+1 收盘成交；半根 K 线（北京 15:10 前）剔除 |")
    A(f"| 成本 | 单边 {S.FEE:.5%}（万 2.5）+ 滑点 {S.SLIP:.2%}；管理费已在 ETF 净值内，不重复扣 |")
    A(f"| 现金 | {S.CASH_ANNUAL:.2%} 年化（货基/同业存单近似），逐日计息 |")
    A("| 基准 | ① 恒满仓（e≡100%，= 买入持有）② 恒定半仓（e≡50%） |")
    A("")

    A("## 2. 主表（全样本）")
    A("")
    A(HEAD)
    A(row("**策略（两因子计分 → 五档）**", rS, yrs_all))
    A(row("恒满仓（买入持有）", rBH, yrs_all))
    A(row("恒定半仓（e≡50%）", rHF, yrs_all))
    A(row("纯现金（货基）", rCA, yrs_all))
    A("")
    A(f"> 策略最大回撤 {pct(mS['mdd'])}（谷底 {tS.date()} / 峰 {pkS.date()}），"
      f"满仓 {pct(mBH['mdd'])}（谷底 {tBH.date()} / 峰 {pkBH.date()}）。"
      f"平均仓位 {rS['exp']:.0%}、调仓 {rS['trades']} 笔（{rS['trades'] / yrs_all:.1f} 次/年）、"
      f"完整往返 {rS['cycles']} 段、清仓 {rS['exits']} 次。")
    A("")
    A("> ⚠ **两因子口径下五档梯子实际只剩三档**：缺因子 → 置信度降一档 → 极端档向 50% 收缩，"
      "于是 `score ≥ +1 → 75%`、`0 → 50%`、`≤ −1 → 25%`；**100% 与 0% 在本样本不可达**"
      "（只有 `SHRINK=\"off\"` 的变体才会打到 0/100，见 §4）。这是「弱证据不给满配」的直接后果，"
      "也是它必然贴着「恒半仓」走的结构性原因。")
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
    A("> 参数**全部取规格默认值**，IS 段没有做任何搜索（§6 的网格只用于一致性检验），"
      "所以 OOS 段不存在「重标定」问题。三段的分歧主要在「黄金是牛是熊」，不在参数。")
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
    pos_y = [y for y in yrs if yS.get(y) is not None and yBH.get(y) is not None
             and yS[y] > yBH[y]]
    A(f"> 策略跑赢满仓的年份只有 **{len(pos_y)}/{len(yrs)}** 个"
      + (f"（{'、'.join(pos_y)}）" if pos_y else "") +
      "：黄金的单边上涨段它必然少赚（仓位低），"
      "赢的年份是「回撤年」—— 与「砍回撤不增收益」的定位一致。")
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
    d_price = (ab("只价格趋势 f_p（f_r 记 0）")["m"]["ann"] - mHF["ann"]) * 100
    d_rate = (ab("只实际利率 f_r（f_p 记 0）")["m"]["ann"] - mHF["ann"]) * 100
    d_both = (mS["ann"] - mHF["ann"]) * 100
    d_flow = (ab("三因子 · 份额 3 日 · 顺势（规格方向）")["m"]["ann"] - mHF["ann"]) * 100
    A(f"> 以「恒半仓」（同平均曝光的无害基准）为标尺：只价格趋势 {num(d_price)}pp、"
      f"只实际利率 {num(d_rate)}pp、两因子 {num(d_both)}pp、三因子（加份额顺势）"
      f"{num(d_flow)}pp。"
      "**按规格 §5 的「负贡献因子剔除」标准，三个因子都没有通过** —— 它们合成的仓位"
      "与「恒定半仓」的差别在噪声级（或更差），而恒半仓的换手是 0。")
    A(f"> 缺因子口径的三种读法差异很小（§8.1）：不收缩 {pct(ab('两因子 · 不收缩（照原表，可到 100%/0%）')['m']['ann'])} / "
      f"全档收缩 {pct(ab('两因子 · 全档收缩（75%→50%、25%→50%）')['m']['ann'])} / "
      f"默认极端档收缩 {pct(mS['ann'])}，"
      "方向一致 —— 结论不依赖这个歧义。")
    A("")

    A(f"## 5. 参数网格（{len(gdf)} 组 · 只看方向一致性）")
    A("")
    A("| MA | 突破窗口 | 利率阈值 | 滞后 | 年化 | 最大回撤 | 夏普 | 调仓 |")
    A("|---|---:|---:|---:|---:|---:|---:|---:|")
    for _, g in gdf.iterrows():
        A(f"| {g['ma']} | {int(g['brk'])} 日 | ±{g['th']:.0f}bp | {int(g['hyst'])} 档 | "
          f"{pct(g['ann'])} | {pct(g['mdd'])} | {num(g['sharpe'])} | {int(g['trades'])} |")
    A("")
    ng = len(gdf)
    A(f"> {ng} 组里：年化跑赢满仓 **{g_ann_win}/{ng}**、回撤比满仓浅 **{g_mdd_win}/{ng}**、"
      f"夏普高于满仓 **{g_sh_win}/{ng}**。方向一致的是「回撤更浅、年化更低」；"
      f"夏普 {num(gdf['sharpe'].min())} ~ {num(gdf['sharpe'].max())}，中位 "
      f"{num(float(gdf['sharpe'].median()))}，与恒半仓 {num(mHF['sharpe'])} 同一量级 —— "
      "**在这套基因上换参数不改变结论**。")
    A("")

    A("## 6. 换手、成本与事件冻结")
    A("")
    frozen = int(df.loc[d["start"]:, "blocked"].sum())
    A(f"- 调仓 **{rS['trades']} 笔**（{rS['trades'] / yrs_all:.1f} 次/年），"
      f"完整往返 {rS['cycles']} 段；累计成本 {rS['cost'] * 100:.2f}pp = "
      f"**{rS['cost'] * 100 / yrs_all:.2f}pp/年**。")
    A(f"- 事件冻结窗口（FOMC / 非农 / 核心PCE ±{F.EVENT_FREEZE} 个交易日）覆盖 "
      f"{frozen / max(len(df.loc[d['start']:]), 1):.0%} 的交易日；窗口内**只许持有或减仓**。")
    A("- 换手来自「档位相邻跳变」：score 在 0/±1 之间来回时，目标档在 50% ↔ 75% ↔ 25% 之间摆。"
      "规格的滞后是「≥1 档（25pp）」，等于**任何**档位变化都要动。"
      "⚠ 注意 `HYST=2`（≥50pp 才动）在这套三档映射上会**完全冻结**（目标与 50% 的距离最大只有 25pp，"
      "永远够不到 50pp 门槛，退化成恒定半仓，§4 最后一行）—— 所以「加到 2 档」不是防抖，是关掉规则。")
    A("")

    A("## 7. 尾部尺子（XAU 长历史，不进净值）")
    A("")
    if ru:
        A(f"- 518880 的样本只有 {px.index[0].date()} 起（ETF 2013 上市），"
          f"样本内最深回撤 {pct(mBH['mdd'])}。")
        A(f"- 同一条规则 + 同一份 DFII10 跑在 **伦敦金现**（{ru['start'].date()} ~ {ru['end'].date()}，"
          f"{(ru['end'] - ru['start']).days / 365.25:.1f} 年）：策略 {pct(ru['ann'])} / "
          f"{pct(ru['mdd'])} / 夏普 {num(ru['sharpe'])}（平均仓位 {ru['exposure']:.0%}），"
          f"买入持有 {pct(ru['bh_ann'])} / {pct(ru['bh_mdd'])} / 夏普 {num(ru['bh_sharpe'])}。")
        A(f"- 该窗口内 XAU 自身最深回撤 {pct(ru['xau_mdd_hold'])}，"
          f"比 518880 样本内深 **{abs(ru['xau_mdd_hold'] - mBH['mdd']) * 100:.1f}pp** —— "
          "这就是「按 ETF 历史标定会低估尾部」的量级（规格 §5 的 ~11pp 是同一现象）。"
          "**所以主表的回撤改善要打折看**：更长样本里策略自己的回撤也会更深。")
        A("- ⚠ XAU 是**伦敦金现美元计价**，与 518880（人民币、含汇率与溢价）不是同一条序列，"
          "这里只当「跌幅尺子」，不参与任何净值。")
    else:
        A("- 缺 `data/_gold_xau.csv`，跳过尾部尺子。")
    A("")

    A("## 8. 敏感性")
    A("")
    A("### 8.1 起手档 / 低配档 / 事件窗口 / 成本")
    A("")
    A("| 变体 | 年化 | 最大回撤 | 夏普 | Calmar | 平均仓位 | 调仓 | 成本/年 |")
    A("|---|---:|---:|---:|---:|---:|---:|---:|")
    for name, r in sens:
        m = r["m"]
        A(f"| {name} | {pct(m['ann'])} | {pct(m['mdd'])} | {num(m['sharpe'])} | "
          f"{num(r['calmar'])} | {r['exp']:.0%} | {r['trades']} | "
          f"{r['cost'] * 100 / yrs_all:.2f}pp |")
    A("")
    A("> 成本不是这套策略的生死线（万 2.5 单边、年换手约 10 倍带内仓位，"
      f"累计 {rS['cost'] * 100:.2f}pp / {yrs_all:.1f} 年）。真正的变量是**平均仓位**："
      "起手 25% 或 75% 会显著改变年化，但那只是「你在第 0 天拿了多少」，不是规则优劣。")
    A("")
    A("### 8.2 资金流口径复核（规格 §1 括号条款：全史 / 顺势 vs 逆势）")
    A("")
    A("东财「主力净流入」硬上限 120 交易日（个股也一样，见 `research/flow_source/_probe_flow.md`），"
      "但 ETF 的**真实**资金流是申购赎回：上交所每日规模表的 `TOT_VOL`（万份）有 "
      f"{d['shares'].index[0].date()} 起全史、**{len(d['shares'])} 个交易日 0 缺失**。"
      "把它当 `f_f`（连续 ≥N 日净申购/净赎回）跑一遍：")
    A("")
    A("| 资金流口径 | 连续 | 方向 | 年化 | 最大回撤 | 夏普 | 平均仓位 | 调仓 |")
    A("|---|---:|---|---:|---:|---:|---:|---:|")
    for _, g in fdf.iterrows():
        A(f"| {g['mode']} | {int(g['streak'])} 日 | {g['sign']} | {pct(g['ann'])} | "
          f"{pct(g['mdd'])} | {num(g['sharpe'])} | {g['exp']:.0%} | {int(g['trades'])} |")
    A("")
    A(f"> 对照：两因子（无资金流）{pct(mS['ann'])} / {pct(mS['mdd'])} / 夏普 {num(mS['sharpe'])}；"
      f"恒半仓 {pct(mHF['ann'])} / {pct(mHF['mdd'])} / {num(mHF['sharpe'])}。")
    A("> **顺势口径（规格方向）让回撤明显变差**：三因子能让 score 触及 ±3（→ 允许 0%/100%），"
      "而份额恰在冲高月份给出满仓 → 买在阶段顶。**逆势口径**能把回撤修回来，但年化掉得更多、"
      "夏普反而更低。`corr(5 日累计申赎%, 次日收益) = −0.015`、`corr(·, 未来 5 日收益) = −0.013` —— "
      "方向微弱为逆势、量级在噪声里。**结论：ETF 份额是「涨了才申购」的结果变量，不是预测变量。**")
    A("> ⚠ `主力净流入（120 天）` 那几行只有 2026-05 以后有值，前面的日子等价于两因子口径，"
      "所以它与「两因子」几乎相同 —— 这不是「主力净流入没用」的证据，而是**根本没数据**。")
    A("")

    A("## 9. 验收断言（规格 §6 · 不过就抛）")
    A("")
    for c in ck:
        A(f"- {c}")
    A("- C7 报告末行 = 免责声明（落盘前断言 `md.rstrip().splitlines()[-1] == DISCLAIMER`）")
    A("")

    A("## 10. 诚实限制")
    A("")
    A("1. **没有 alpha**：两个因子的合成仓位与「恒定半仓」在 13 年里几乎相同（§4），"
      "换手却是每年 40 余笔。按规格 §7「若连回撤都砍不动，回退恒定半仓，不硬留规则」——"
      "本样本支持「回退恒半仓」这一选项，是否上线由使用者决定。")
    A("2. **实际利率因子是日频弱信息**：`DFII10` 的 10 日变化 ±25bp 是**慢变量**，"
      "在 20 日突破的配合下很少独立触发；它的价值更多在「利率快速上行时压仓位」这个尾部场景，"
      "而本样本的利率单边下行段居多。")
    A("3. **非农/核心PCE 是发布日近似**（每月首个周五 / 月末最后交易日），节假日顺延未建模；"
      "影响的只是「事件窗口内不许加仓」这一条，最多错 1 个交易日。")
    A("4. **资金流因子：主口径无全史、替代口径有全史但有害**。东财 `fflow/daykline` 硬上限 120 根"
      "（`data/_flow_518880.csv` 只作每日提示）；本轮另补了**上交所每日份额**全史"
      f"（`data/_etf_shares_518880.csv`，{len(d['shares'])} 日 0 缺失），按 §8.2 复核："
      "顺势恶化回撤、逆势只修回撤不增收益，两个方向都跑输恒半仓。"
      "**规格 §1 的剔除处理在数据层面已被排除质疑，不是「没数据所以不用」。**")
    A("5. **份额口径与「主力净流入」不是同一个因子**：份额 = 申购赎回（真实资金进出，"
      "含做市商与套利盘），主力净流入 = 按单笔金额分档的成交统计。二者都试不到增益，"
      "但不能据此断言「所有资金流类因子无效」。")
    A("6. **映射的两因子口径存在歧义**：「同表 + 缺因子降置信度 + 极端档收缩」是本实现的读法，"
      "另两种读法在 §4/§8.1 已并列，结论方向不变（都不如恒半仓）。")
    A("7. **样本 13.2 年、只有一轮完整的黄金牛熊**：黄金 2013-2015 长熊、2019-2026 长牛，"
      "结论对起点极敏感；XAU 尺子（§7）说明更长样本的尾部更深。")
    A("8. **与 v6/v9 不叠加**：本策略与 `v6_gold` 都回答「黄金这笔钱怎么拿」，"
      "同时跑会重复记一遍仓位；要叠加必须先定额度归属。")
    A("")
    A(DISCLAIMER)
    return "\n".join(L) + "\n"


if __name__ == "__main__":
    main()
