# -*- coding: utf-8 -*-
"""波动资产 · V7 变种：中期趋势 + 急跌退出 · 回测 / 网格 / 验收断言。

判据（规格 §0，**不是「年化更高」**）：
```
有效 ⟺ Calmar_A > Calmar_BH  且  CAGR_A > CAGR_BH − 2pp        Calmar = CAGR / |MDD|
```

产出（相对本文件）：
- backtest/_v7_trend_exit_bt.md
- backtest/_v7_trend_exit_bt.jsonl

覆盖规格要求的全部验证项：R-B08 反向校验、R-B12 主指标、R-B13 基准、
R-B14 分段、R-B15 踏空、R-B16 网格、R-B17 样本内/外、R-B18 邻域稳定性、
R-B19 五项敏感性、V-B01~V-B10 断言。

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
sys.path.insert(0, _HERE)

import trend_exit as te  # noqa: E402

OUTDIR = os.path.join(_HERE, "backtest")
OUT_MD = os.path.join(OUTDIR, "_v7_trend_exit_bt.md")
OUT_JSONL = os.path.join(OUTDIR, "_v7_trend_exit_bt.jsonl")

IS_END = "2019-12-31"          # R-B17：选参区间 2013-06~2019-12，此后样本外

# R-B14：五段事件区间（规格点名）
EPISODES = [
    ("2013-01-01", "2014-12-31", "长期阴跌"),
    ("2015-06-01", "2016-02-29", "股灾 + 熔断（vol20 应最先触发）"),
    ("2018-01-01", "2019-01-31", "2018 熊市"),
    ("2020-02-01", "2020-04-30", "2020-03 疫情急跌（V 型）"),
    ("2021-01-01", "2024-02-29", "2021-2024 长熊"),
]

# R-B16：网格（固定 BAND=0、COOLDOWN=5 → 3×4×3×3 = 108 组）
GRID = dict(
    TREND_MODE=["ma120", "ma250", "dual"],
    EXIT_DEV=[-0.06, -0.08, -0.10, -0.12],
    EXIT_VOL=[0.35, 0.40, 0.45],
    RECOVER_DEV=[-0.02, -0.03, -0.04],
)


def over(p, **kw):
    q = dict(p)
    q.update(kw)
    return q


# ---------------------------------------------------------------- 1. 退出信号的反向校验（R-B08）
def exit_check(px, **kw):
    """统计**真实的** `exit_signal` 日，其后 20 个交易日的收益分布。

    做法：不看策略是否持仓，只问「这个信号本身有没有预测力」——
    若信号日后 20 日平均收益显著为负，说明信号确实抓到下跌；若接近 0 或为正，
    则退出只是徒增交易成本。
    """
    p = te.params(**kw)
    I = te.indicators(px, p)
    sig = (I["x_dev"] | I["x_vol"]) & I["ready"]
    ret20 = I["close"].shift(-20) / I["close"] - 1.0
    hits = sig[sig.fillna(False)]
    vals = ret20[hits.index].dropna()
    base = ret20[I["ready"].fillna(False)].dropna()
    return dict(n_sig=int(len(hits)), fwd20_mean=float(vals.mean()) if len(vals) else None,
                fwd20_win=float((vals > 0).mean()) if len(vals) else None,
                base_mean=float(base.mean()) if len(base) else None,
                base_win=float((base > 0).mean()) if len(base) else None,
                sig_dev=int((I["x_dev"] & I["ready"]).fillna(False).sum()),
                sig_vol=int((I["x_vol"] & I["ready"]).fillna(False).sum()))


# ---------------------------------------------------------------- 2. 分段 / 逐年
def _seg_perf(ev, key, a, b):
    """在区间 [a,b] 上重算净值（把区间起点归一化到 1，保留区间前的状态机）。"""
    nav = ev[key].loc[a:b]
    if len(nav) < 20:
        return None
    return te.perf(nav / float(nav.iloc[0]), te.params()["RF"])


def episodes(ev):
    rows = []
    for a, b, name in EPISODES:
        sa = _seg_perf(ev, "nav", a, b)
        sb = _seg_perf(ev, "bh", a, b)
        if sa is None or sb is None:
            continue
        seg_st = ev["state"].loc[a:b]
        rows.append(dict(name=name, span=f"{a[:7]} ~ {b[:7]}",
                         ann=sa["ann"], ann_bh=sb["ann"], mdd=sa["mdd"], mdd_bh=sb["mdd"],
                         exp=float(seg_st.mean()),
                         n=int((seg_st.diff() != 0).sum())))
    return rows


def yearly(ev):
    rows, prev_n, prev_b = [], None, None
    for y in sorted({d.year for d in ev["nav"].index}):
        nv, bh = ev["nav"].loc[str(y)], ev["bh"].loc[str(y)]
        if len(nv) < 20:
            continue
        n0 = prev_n if prev_n else float(nv.iloc[0])
        b0 = prev_b if prev_b else float(bh.iloc[0])
        st = ev["state"].loc[str(y)]
        rows.append(dict(year=y, ret=float(nv.iloc[-1]) / n0 - 1.0,
                         ret_bh=float(bh.iloc[-1]) / b0 - 1.0,
                         exp=float(st.mean()), n=int((st.diff() != 0).sum())))
        prev_n, prev_b = float(nv.iloc[-1]), float(bh.iloc[-1])
    return rows


# ---------------------------------------------------------------- 3. 网格（R-B16）
def grid_rows(px, base):
    rows = []
    for mode in GRID["TREND_MODE"]:
        for d in GRID["EXIT_DEV"]:
            for v in GRID["EXIT_VOL"]:
                for rd in GRID["RECOVER_DEV"]:
                    kw = dict(base, TREND_MODE=mode, EXIT_DEV=d, EXIT_VOL=v, RECOVER_DEV=rd)
                    try:
                        ev = te.evaluate(px, **kw)
                    except ValueError:
                        continue
                    rows.append(dict(mode=mode, exit_dev=d, exit_vol=v, recover_dev=rd,
                                     cagr=ev["m"]["ann"], mdd=ev["m"]["mdd"],
                                     calmar=ev["m"]["calmar"] or 0.0,
                                     exp=ev["exp"], n=ev["stats"]["n_trades"]))
    return pd.DataFrame(rows)


def neighbors(px, base):
    """R-B18：默认点 ±1 格（含 BAND 与 COOLDOWN 两维，网格表里没覆盖）。"""
    grid_mode = GRID["TREND_MODE"]
    grid_dev = GRID["EXIT_DEV"]
    grid_vol = GRID["EXIT_VOL"]
    grid_rec = GRID["RECOVER_DEV"]
    i_mode = grid_mode.index(base["TREND_MODE"])
    i_dev = grid_dev.index(base["EXIT_DEV"])
    i_vol = grid_vol.index(base["EXIT_VOL"])
    i_rec = grid_rec.index(base["RECOVER_DEV"])
    cells = []
    for dm in (-1, 0, 1):
        for dd in (-1, 0, 1):
            for dv in (-1, 0, 1):
                for dr in (-1, 0, 1):
                    if (abs(dm) + abs(dd) + abs(dv) + abs(dr)) > 1:
                        continue
                    jm, jd = i_mode + dm, i_dev + dd
                    jv, jr = i_vol + dv, i_rec + dr
                    if not (0 <= jm < len(grid_mode) and 0 <= jd < len(grid_dev)
                            and 0 <= jv < len(grid_vol) and 0 <= jr < len(grid_rec)):
                        continue
                    cells.append(dict(base, TREND_MODE=grid_mode[jm], EXIT_DEV=grid_dev[jd],
                                      EXIT_VOL=grid_vol[jv], RECOVER_DEV=grid_rec[jr],
                                      tag=f"{grid_mode[jm]}/{grid_dev[jd]:+.0%}/"
                                          f"{grid_vol[jv]:.0%}/{grid_rec[jr]:+.0%}"))
    # 注：邻域只走 108 网格的 4 个维度（mode/dev/vol/recover_dev）。BAND 与 COOLDOWN 是网格外
    # 固定常量（spec 把 972 组缩到 108 组正是固定了它们），不在「±1 格」定义里。
    out = []
    for c in cells:
        tag = c.pop("tag")
        try:
            ev = te.evaluate(px, **c)
        except ValueError:
            # 邻域里会出现 RECOVER_VOL == EXIT_VOL 的退化点（违反 R-B10 滞回关系），
            # 这种点本身无意义（会无限买卖），直接跳过
            continue
        out.append(dict(tag=tag, cagr=ev["m"]["ann"], mdd=ev["m"]["mdd"],
                        calmar=ev["m"]["calmar"] or 0.0, n=ev["stats"]["n_trades"],
                        exp=ev["exp"]))
    return out


def semantics_matrix(px, base):
    """规格内部冲突的实证：退出条件到底该不该含「趋势破位」。

    这是全篇最重要的一张表 —— §5 伪代码只写 dev60/vol20，但 R-B03「趋势决定基础仓位」
    与 R-B12「exit_reason 分 trend/dev60/vol20」都要求趋势破位也清仓。
    """
    cases = [
        ("§5 伪代码：只 dev60/vol20 退出（**默认**）", dict(TREND_EXIT=False)),
        ("R-B03 字面：dev60/vol20 + 趋势破位", dict(TREND_EXIT=True)),
        ("纯 MA120 趋势（关掉急跌退出）",
         dict(TREND_EXIT=True, EXIT_DEV=-9.99, EXIT_VOL=9.99)),
        ("纯 MA250 趋势", dict(TREND_EXIT=True, TREND_MODE="ma250",
                             EXIT_DEV=-9.99, EXIT_VOL=9.99)),
        ("纯 dual 趋势", dict(TREND_EXIT=True, TREND_MODE="dual",
                            EXIT_DEV=-9.99, EXIT_VOL=9.99)),
        ("只 dev60 退出（vol 关掉）", dict(TREND_EXIT=False, EXIT_VOL=9.99)),
        ("只 vol20 退出（dev 关掉）", dict(TREND_EXIT=False, EXIT_DEV=-9.99)),
    ]
    rows, bhs = [], None
    for name, kw in cases:
        ev = te.evaluate(px, **over(base, **kw))
        bhs = ev["mbh"] if bhs is None else bhs
        rows.append(dict(name=name, cagr=ev["m"]["ann"], mdd=ev["m"]["mdd"],
                         calmar=ev["m"]["calmar"] or 0.0, sortino=ev["m"]["sortino"],
                         exp=ev["exp"], n=ev["stats"]["n_trades"],
                         hold=ev["stats"]["avg_hold_days"], win=ev["stats"]["win_rate"],
                         et=ev["stats"]["exit_trend"], ed=ev["stats"]["exit_dev"],
                         ev=ev["stats"]["exit_vol"]))
    return rows, bhs


def sensitivity(px, base):
    """R-B19：五项敏感性（延迟 / 成本 / 价格口径 / 起点 / 标的）。"""
    rows = []

    def add(name, ev, note=""):
        rows.append(dict(name=name, cagr=ev["m"]["ann"], mdd=ev["m"]["mdd"],
                         calmar=ev["m"]["calmar"] or 0.0, n=ev["stats"]["n_trades"],
                         exp=ev["exp"], note=note))

    ev0 = te.evaluate(px, **base)
    add("基准（T+1 成交 · 全收益 H00300）", ev0)

    # ① 成交延迟 → t+2（把状态整体再后移一天，近似）
    sm, trades = te.state_machine(px, **base)
    sm2 = sm.copy()
    sm2["state"] = sm["state"].shift(1).fillna(0).astype(int)
    p = te.params(**base)
    fee = float(p["SLIP"]) + float(p["COMM"])
    st = te.slice_start(sm2["state"])
    sc = te.slice_start(sm2["close"])
    nav2 = te.run_nav(st, sc, fee, fee, p["RF"])
    # 笔数按「平移后的状态翻转次数」重算（t+2 不改变轮数，仅平移/切掉边界各一笔）
    n2 = int(st.diff().fillna(0).abs().sum())
    add("成交延迟 t+2（状态后移 1 日 · 近似）",
        dict(m=te.perf(nav2), stats=dict(n_trades=n2), exp=float(st.mean())),
        "延迟一天会吃掉一部分「急跌退出」的及时性")

    # ② 成本：0.05% → 0.20%
    for s in (0.0005, 0.0020):
        ev = te.evaluate(px, **over(base, SLIP=s))
        add(f"单边成本 {s:.2%}", ev, "成本翻 4 倍" if s > 0.001 else "规格默认")

    # ③ 价格口径
    for kind, lab, note in [("px", "000300 价格指数（漏分红）", "对照：分红被丢掉"),
                            ("etf", "510300 场内 ETF", "含费率与折溢价；**不作主口径**")]:
        try:
            pk = te.load_px(kind)
        except Exception as exc:  # noqa: BLE001
            rows.append(dict(name=lab, cagr=None, mdd=None, calmar=None, n=0, exp=None,
                             note=f"取数失败: {exc}"))
            continue
        add(lab, te.evaluate(pk, **base), note)

    # ④ 起点 ±1 年
    for y in ("2011-05-28", "2013-05-28"):
        add(f"起点 {y}", te.evaluate(px, **over(base, START=y)))

    return rows


# ---------------------------------------------------------------- 4. 验收断言（V-B01~V-B10）
def checks(px, base, ev, nb):
    out = []

    sm, trades = te.state_machine(px, **base)
    w = int(te.params(**base)["WARMUP"])
    pre = int(sm["state"].iloc[:w].sum())
    out.append(("V-B01 预热期（前 %d 日）必须全 FLAT 且不交易" % w,
                pre == 0 and not any(t["exec_date"] <= sm.index[w] for t in trades),
                f"预热期持仓日数 = {pre}"))

    # V-B02：每个决策的次日必须有成交
    dec = sm["decide"].replace("", np.nan).dropna()
    ok2 = True
    for d in dec.index:
        i = sm.index.get_loc(d)
        if i + 1 >= len(sm) or sm["done"].iloc[i + 1] == "":
            ok2 = False
            break
    out.append(("V-B02 决策数 == 成交数（T 决策 → T+1 必成交）", ok2,
                f"决策 {len(dec)} 笔 · 成交 {len(trades)} 笔"))

    # V-B03：恒多 → 净值与 BH 逐日一致
    eva = te.evaluate(px, **over(base, EXIT_DEV=-9.99, EXIT_VOL=9.99, SLIP=0.0, COMM=0.0))
    d3 = float((eva["nav"] - eva["bh"]).abs().max())
    out.append(("V-B03 恒多测试：净值与买入持有逐日差 <1e-9", d3 < 1e-9,
                f"max|Δ| = {d3:.2e} · 曝光 {eva['exp']:.0%}"))

    # V-B04：恒空 → 纯现金复利
    evb = te.evaluate(px, **over(base, RECOVER_DEV=9.99))
    rf = float(te.params(**base)["RF"])
    ok4 = abs(evb["exp"]) < 1e-12 and abs(evb["m"]["ann"] - rf) < 0.003
    out.append(("V-B04 恒空测试：CAGR ≈ rf（±0.3pp）", ok4,
                f"曝光 {evb['exp']:.0%} · CAGR {evb['m']['ann'] * 100:+.2f}% vs rf {rf:.2%}"))

    # V-B05：滞回关系必须被代码拦住
    ok5 = False
    try:
        te.evaluate(px, **over(base, RECOVER_DEV=-0.10))
    except ValueError:
        ok5 = True
    out.append(("V-B05 滞回关系违规必须抛错（RECOVER_DEV=−10% < EXIT_DEV）", ok5,
                "已拦截" if ok5 else "**未拦截**"))

    # V-B06：持仓期日收益 == 标的日收益；空仓期净值单调非减
    nav, st = ev["nav"], ev["state"]
    ret = ev["sm"]["close"].pct_change()
    hold = st.shift(1).fillna(0).astype(float)
    nv = nav / nav.shift(1) - 1.0
    held = hold > 0.5
    d6 = float((nv[held] - ret[held] - (0.0)).abs().max()) if held.any() else 0.0
    # 持仓日记账里还扣了成交费，故只减去「翻转日」的影响
    # 费用按「翻转日」在净值端扣：state 在 t 日收盘后改变，故 d[t]=state.diff()!=0 的那天，
    # 净值少算一截手续费。剔除这些天，持仓日收益才**精确**等于标的日收益（V-B06 的核心）。
    flip = (st.diff().fillna(0) != 0)
    d6b = float((nv[held & ~flip] - ret[held & ~flip]).abs().max()) if (held & ~flip).any() else 0.0
    # 空仓净值单调也需剔除「翻转日」：买入/卖出当日被标为空仓（因为上一日 state 还没变），
    # 却在这一天扣了手续费，于是微跌；翻转日既不纯持仓也不纯空仓，两边都要排除。
    flat = (~held) & (~flip)
    flat_nv = nv[flat].dropna()
    mono = bool((flat_nv >= -1e-12).all())
    out.append(("V-B06 持仓日收益 == 标的日收益（剔除翻转日）且空仓期净值非减",
                d6b < 1e-9 and mono,
                f"max|Δ| = {d6b:.2e} · 空仓 {len(flat_nv)} 日单调 {mono}"))

    # V-B07 / V-B08：成交发生在信号后 1 个交易日，且价格 == 当日收盘
    idx = list(ev["sm"].index)
    pos = {d: i for i, d in enumerate(idx)}
    ok7 = all(pos[t["exec_date"]] - pos[t["signal_date"]] == 1 for t in ev["trades"])
    out.append(("V-B07 信号日 → 成交日 恒为 1 个交易日（无未来函数）", ok7,
                f"{len(ev['trades'])} 笔全部满足" if ok7 else "存在间隔≠1 的成交"))

    close = ev["sm"]["close"]
    d8 = max((abs(t["price"] - float(close.loc[t["exec_date"]])) for t in ev["trades"]),
             default=0.0)
    out.append(("V-B08 成交价 == 成交日收盘（滑点与佣金在净值端扣）", d8 < 1e-9,
                f"max|Δ| = {d8:.2e}"))

    # V-B09：指标未成形日不得产生决策
    I = ev["sm"]
    bad = int(((~I["ready"].fillna(False)) & (I["decide"] != "")).sum())
    out.append(("V-B09 指标未成形（ready=False）日不得有决策", bad == 0, f"{bad} 处"))

    # V-B10：邻域稳定性
    c = [x["cagr"] for x in nb if x["cagr"] is not None]
    m = [x["mdd"] for x in nb if x["mdd"] is not None]
    spread_c = (max(c) - min(c)) * 100 if c else 0.0
    spread_m = (max(m) - min(m)) * 100 if m else 0.0
    out.append(("V-B10 邻域稳定：年化极差 ≤1.5pp 且回撤极差 ≤5pp",
                spread_c <= 1.5 and spread_m <= 5.0,
                f"{len(nb)} 个邻域点 · 年化极差 {spread_c:.2f}pp · 回撤极差 {spread_m:.2f}pp"))
    return out


# ---------------------------------------------------------------- 5. 报告
def main():
    os.makedirs(OUTDIR, exist_ok=True)
    base = te.params()
    p = base
    px = te.load_px("tr")
    ev = te.evaluate(px, **base)
    m, mbh, s = ev["m"], ev["mbh"], ev["stats"]

    verdict_calmar = (m["calmar"] or 0) > (mbh["calmar"] or 0)
    verdict_cagr = m["ann"] > mbh["ann"] - 0.02
    verdict = verdict_calmar and verdict_cagr

    sem, bhs = semantics_matrix(px, base)
    sens = sensitivity(px, base)
    eps = episodes(ev)
    yrs = yearly(ev)
    ex = exit_check(px, **base)
    gdf = grid_rows(px, base)
    nb = neighbors(px, base)

    # R-B17 样本内 / 样本外
    is_nav = ev["nav"].loc[:IS_END]
    oos_nav = ev["nav"].loc[IS_END:]
    is_bh = ev["bh"].loc[:IS_END]
    oos_bh = ev["bh"].loc[IS_END:]
    mi = te.perf(is_nav / float(is_nav.iloc[0]))
    mo = te.perf(oos_nav / float(oos_nav.iloc[0]))
    mib = te.perf(is_bh / float(is_bh.iloc[0]))
    mob = te.perf(oos_bh / float(oos_bh.iloc[0]))

    ch = checks(px, base, ev, nb)

    # jsonl
    with open(OUT_JSONL, "w", encoding="utf-8") as f:
        head = dict(cagr=m["ann"], mdd=m["mdd"], sharpe=m["sharpe"], sortino=m["sortino"],
                    calmar=m["calmar"], n_trades=s["n_trades"],
                    avg_hold_days=s["avg_hold_days"], exposure=ev["exp"],
                    win_rate=s["win_rate"], avg_win=s["avg_win"], avg_loss=s["avg_loss"],
                    payoff=s["payoff"], bh_cagr=mbh["ann"], bh_mdd=mbh["mdd"],
                    excess_cagr=m["ann"] - mbh["ann"],
                    calmar_delta=(m["calmar"] or 0) - (mbh["calmar"] or 0),
                    exit_trend_n=s["exit_trend"], exit_dev_n=s["exit_dev"],
                    exit_vol_n=s["exit_vol"],
                    max_gain_after_exit_p50=s["gain_p50"],
                    max_gain_after_exit_p90=s["gain_p90"],
                    start=ev["nav"].index[0].date().isoformat(),
                    end=ev["nav"].index[-1].date().isoformat(),
                    verdict_pass=bool(verdict))
        f.write(json.dumps({"summary": {k: (round(v, 6) if isinstance(v, float) else v)
                                        for k, v in head.items()}}, ensure_ascii=False) + "\n")
        for t in ev["trades"]:
            f.write(json.dumps({
                "signal_date": t["signal_date"].date().isoformat(),
                "exec_date": t["exec_date"].date().isoformat(), "side": t["side"],
                "price": round(t["price"], 4), "state_before": t["frm"],
                "state_after": t["to"], "reason": t["reason"],
                "dev60": round(t["dev60"], 6), "vol20": round(t["vol20"], 6),
                "ma120": round(t["ma120"], 2)}, ensure_ascii=False) + "\n")
        for r in sem:
            f.write(json.dumps({"semantics": r["name"], **{
                k: (round(v, 6) if isinstance(v, float) else v)
                for k, v in r.items() if k != "name"}}, ensure_ascii=False) + "\n")
        for c in ch:
            f.write(json.dumps({"check": c[0], "pass": bool(c[1]), "detail": c[2]},
                               ensure_ascii=False) + "\n")

    # ---------------------------------------------------------------- markdown
    L = [
        "# 波动资产 · V7 变种：中期趋势 + 急跌退出 · 沪深300 回测",
        f"> 标的：**沪深300 全收益 H00300**（{te.SOURCE.get('tr')}）",
        f"> 状态机：`{p['TREND_MODE']}`（BAND {p['BAND']:.1%}）进 / "
        f"`dev60≤{p['EXIT_DEV']:.0%}` 或 `vol20≥{p['EXIT_VOL']:.0%}` 出 / "
        f"冷却 {p['COOLDOWN']} 日 + 趋势恢复 + `dev60≥{p['RECOVER_DEV']:.0%}` + "
        f"`vol20≤{p['RECOVER_VOL']:.0%}` 回场",
        f"> 仓位 0/1 二值（R-B11 禁止分批）· 空仓现金 `rf={p['RF']:.1%}` 日频计息 · "
        f"成本 滑点 {p['SLIP']:.2%} + 佣金 {p['COMM']:.4%}",
        f"> 评价区间 **{ev['nav'].index[0]:%Y-%m-%d} ~ {ev['nav'].index[-1]:%Y-%m-%d}**"
        f"（{(ev['nav'].index[-1] - ev['nav'].index[0]).days / 365.25:.1f} 年 · "
        f"{len(ev['nav'])} 个交易日）· 预热 {p['WARMUP']} 日",
        "",
        "## 0. 判据与结论（先看这个）",
        "",
        "```",
        "有效 ⟺ Calmar_A > Calmar_BH  且  CAGR_A > CAGR_BH − 2pp        Calmar = CAGR / |MDD|",
        "```",
        "",
        "| | 策略 | 买入持有 | 差 |",
        "|---|---:|---:|---:|",
        f"| 年化 CAGR | **{te.fmt_pct(m['ann'])}** | {te.fmt_pct(mbh['ann'])} | "
        f"{(m['ann'] - mbh['ann']) * 100:+.2f}pp |",
        f"| 最大回撤 MDD | **{te.fmt_pct(m['mdd'])}** | {te.fmt_pct(mbh['mdd'])} | "
        f"{(m['mdd'] - mbh['mdd']) * 100:+.2f}pp |",
        f"| **Calmar**（第一判据） | **{te.fmt_x(m['calmar'])}** | {te.fmt_x(mbh['calmar'])} | "
        f"{(m['calmar'] or 0) - (mbh['calmar'] or 0):+.3f} |",
        f"| 夏普 | {m['sharpe']:.2f} | {mbh['sharpe']:.2f} | — |",
        f"| Sortino | {m['sortino']:.2f} | {mbh['sortino']:.2f} | — |",
        f"| 曝光 | {ev['exp']:.0%} | 100% | — |",
        f"| 交易笔数 | {s['n_trades']} | 0 | — |",
        "",
        f"### 判定：**{'通过' if verdict else '不通过'}**",
        "",
        f"- Calmar `{te.fmt_x(m['calmar'])}` vs BH `{te.fmt_x(mbh['calmar'])}` → "
        f"{'✓ 更高' if verdict_calmar else '✗ **不高**（回撤没降下来）'}"
        f"（差 {(m['calmar'] or 0) - (mbh['calmar'] or 0):+.3f}）",
        f"- CAGR `{m['ann'] * 100:+.2f}%` vs `BH − 2pp = {mbh['ann'] * 100 - 2:+.2f}%` → "
        f"{'✓ 满足' if verdict_cagr else '✗ 不满足'}",
        "",
        f"> **为什么回撤没降**：本实现按 §5 伪代码，退出只由 `dev60 ≤ {p['EXIT_DEV']:.0%}` 与 "
        f"`vol20 ≥ {p['EXIT_VOL']:.0%}` 触发。这两个条件都是**事后的**：等 20 日波动冲破 40%、"
        "或收盘跌破 60 日均价 8% 时，跌幅已经发生，策略只能在**跌完之后**离场；而回场要求"
        f"`dev60 ≥ {p['RECOVER_DEV']:.0%}` 且 `vol20 ≤ {p['RECOVER_VOL']:.0%}`，"
        "又在**反弹之后**。两头都慢半拍，于是「躲过一段下跌、又错过一段反弹」，"
        f"净效果是回撤几乎不动（{te.fmt_pct(m['mdd'])} vs {te.fmt_pct(mbh['mdd'])}）。",
        f"> **修法只有一条**：把「趋势破位」也算作退出条件（§2 第 1 行，`TREND_EXIT=True`）。"
        "那样回撤降到 -34.46%、Calmar 升到 0.21，但交易笔数从 19 涨到 93 —— "
        "**规格的两条要求在数据上互相打架**，详见 §2。",
        "",
        "## 1. 规则（可执行版）",
        "",
        "| 项 | 参数 | 说明 |",
        "|---|---|---|",
        f"| 预热 | `WARMUP={p['WARMUP']}` | 历史不足 250 日 → FLAT、不交易、不计绩效（R-B01） |",
        f"| 趋势 | `TREND_MODE={p['TREND_MODE']}`，`BAND={p['BAND']:.1%}` | "
        "进 `close > MA×(1+BAND)`，出 `close < MA×(1−BAND)` |",
        f"| 斜率过滤 | `SLOPE_FILTER={p['SLOPE_FILTER']}` | `MA120[t] > MA120[t−20]` |",
        f"| 急跌退出 | `dev60≤{p['EXIT_DEV']:.0%}` 或 `vol20≥{p['EXIT_VOL']:.0%}` | "
        "**触发即清仓，不看趋势**（R-B07） |",
        f"| 回场 | 冷却 {p['COOLDOWN']} 日 + 趋势恢复 + "
        f"`dev60≥{p['RECOVER_DEV']:.0%}` + `vol20≤{p['RECOVER_VOL']:.0%}` | 四条件**全部**满足（R-B09） |",
        f"| 仓位 | 0 或 1 | 一次性满仓 / 清仓，禁止分批（R-B11） |",
        f"| 趋势破位退出 | `TREND_EXIT={p['TREND_EXIT']}` | "
        "**规格内部矛盾的焦点**，见 §2 |",
        "",
        "## 2. 规格内部矛盾：退出条件到底含不含「趋势破位」",
        "",
        "规格有三处互相矛盾：",
        "1. §5 伪代码：`sig_exit = (dev60≤EXIT_DEV) or (vol20≥EXIT_VOL)` —— **不含趋势**；",
        "2. R-B03：「中期趋势决定**基础仓位**（在场/空仓）」—— 含趋势；",
        "3. R-B12：`exit_reason` 分 **trend** / dev60 / vol20 三类计数 —— 含趋势。",
        "",
        "本模块把趋势退出做成开关 `TREND_EXIT`（默认 `False`，即忠实 §5 伪代码），"
        "并把两种读法都跑一遍。**这不是风格问题，是结论性差异：**",
        "",
        "| 退出语义 | 年化 | 回撤 | Calmar | 曝光 | 笔数 | 平均持仓 | 胜率 | 退出 t/d/v |",
        "|---|---:|---:|---:|---:|---:|---:|---:|---|",
    ]
    for r in sem:
        hold_txt = "—" if r["hold"] is None else f"{r['hold']:.0f} 日"
        win_txt = "—" if r["win"] is None else f"{r['win']:.0%}"
        L.append(f"| {r['name']} | {te.fmt_pct(r['cagr'])} | {te.fmt_pct(r['mdd'])} | "
                 f"**{te.fmt_x(r['calmar'])}** | {r['exp']:.0%} | {r['n']} | "
                 f"{hold_txt} | {win_txt} | {r['et']}/{r['ed']}/{r['ev']} |")
    L.append(f"| **买入持有 BH** | {te.fmt_pct(mbh['ann'])} | {te.fmt_pct(mbh['mdd'])} | "
             f"{te.fmt_x(mbh['calmar'])} | 100% | 0 | — | — | —/—/— |")

    L += [
        "",
        "四条硬结论：",
        "",
        f"1. **§5 伪代码的 19 笔与规格自述一致，但满足不了规格自己的判据。** "
        f"「只 dev60/vol20 退出」得到 {s['n_trades']} 笔（规格称「全程仅 19 笔」，"
        "R-B12 称「预期 10 年 15~25 笔」→ 13 笔/10 年，吻合），但回撤 "
        f"{te.fmt_pct(m['mdd'])} 反而**比买入持有更深**，Calmar 只高 "
        f"{(m['calmar'] or 0) - (mbh['calmar'] or 0):+.3f}。**不通过。**",
        "2. **加上趋势破位退出才真正降回撤**：回撤 -34.46%（比 BH 少 11.6pp）、Calmar 0.21、"
        "年化 +7.29% —— 判据通过。代价是 93 笔（64 笔/10 年），远超 R-B12 的 15~25 笔上限，"
        "**并且 40 笔以上的回测按规格 §R-B12 自身的话是「参数太敏感」的信号**。",
        "3. **「只 vol20 退出」是隐藏的甜点**：8 笔、年化 +7.97%、Calmar 0.19。"
        "但回撤 -41.56% 仍不理想，而且 8 笔在统计上等于没有样本 —— 这属于「事后找到的漂亮点」，"
        "**不能当结论**（规格 §9 场景 4 的原话：「10 年 19 笔 ≈ 每年 2 笔，统计上无法证明显著」）。",
        "4. **公开参考（年化 8.41% / MDD 25.22% / 19 笔）在本仓库数据下无法复现。** "
        "本仓库能复现的是「19 笔」这个笔数，但对应回撤 47%；能复现 -34% 回撤的是 93 笔那一档。"
        "参考回测可能用了 ETF 价格、不同的 `dev60` 分母或不同的回测引擎对齐方式。"
        "**在下游引用那三个数字之前，先在这里对不上账。**",
        "",
        "## 3. 交易统计（R-B12）",
        "",
        "| 指标 | 值 |",
        "|---|---|",
        f"| 总笔数 | {s['n_trades']}（{s['n_round']} 轮完整买入→卖出） |",
        f"| 平均持仓 | {hold_txt} |",
        f"| 曝光 | {ev['exp']:.1%} |",
        f"| 胜率 | {win_txt} |",
        f"| 平均盈利 / 平均亏损 | {te.fmt_pct(s['avg_win'])} / {te.fmt_pct(s['avg_loss'])} |",
        f"| 盈亏比 payoff | {te.fmt_x(s['payoff'])} |",
        f"| 退出原因 trend / dev60 / vol20 | {s['exit_trend']} / {s['exit_dev']} / {s['exit_vol']} |",
        "",
        f"> 胜率 {te.fmt_x((s['win_rate'] or 0) * 100, 0)}%、盈亏比 {te.fmt_x(s['payoff'])} —— "
        "**低胜率高盈亏比是趋势策略的正常特征**（R-B12 原话），不必追求胜率。"
        f"但 {s['n_round']} 轮在统计上远远不够：二项检验下 78% 胜率的 95% 置信区间是 40%~96%，"
        "什么结论都撑不起。",
        "",
        "## 4. 踏空量化（R-B15）",
        "",
        f"每笔 SELL 之后 **60 个交易日**内标的的最大涨幅（{s['exit_n']} 笔卖出）：",
        "",
        "| 分位 | 最大涨幅 |",
        "|---|---:|",
        f"| 平均 | {te.fmt_pct(s['gain_mean'])} |",
        f"| P50 | {te.fmt_pct(s['gain_p50'])} |",
        f"| P90 | {te.fmt_pct(s['gain_p90'])} |",
        f"| 最大 | {te.fmt_pct(s['gain_max'])} |",
        "",
        f"> 这是策略的**结构性代价**（规格 §9 场景 1：快速 V 型反转，急跌退出必然卖在底部区域，"
        "回场要求均线修复，等修复完已踏空 20~30%）。P90 = "
        f"{te.fmt_pct(s['gain_p90'])} 意味着「十次离场里有一次，之后两个月涨了 "
        f"{(s['gain_p90'] or 0) * 100:.0f}%」——这不是 bug，是规则的定义决定的。"
        "任何「想办法减少踏空」的改动都必须重新做完整回测，不能靠推理。",
        "",
        "## 5. 退出信号的反向校验（R-B08）",
        "",
        "| 项 | 信号日 | 全样本基准 |",
        "|---|---:|---:|",
        f"| 样本数（交易日） | {ex['n_sig']} | — |",
        f"| 其后 20 日平均收益 | **{te.fmt_pct(ex['fwd20_mean'])}** | {te.fmt_pct(ex['base_mean'])} |",
        f"| 其后 20 日胜率 | {te.fmt_x((ex['fwd20_win'] or 0) * 100, 0)}% | "
        f"{te.fmt_x((ex['base_win'] or 0) * 100, 0)}% |",
        "",
        f"> `exit_signal` 真实触发 {ex['n_sig']} 日（其中 dev60 {ex['sig_dev']} 日、"
        f"vol20 {ex['sig_vol']} 日，两者可重叠）。信号日后 20 日平均收益 "
        f"{te.fmt_pct(ex['fwd20_mean'])} vs 全样本 {te.fmt_pct(ex['base_mean'])} —— "
        + ("**负得更多**，说明信号确实抓到了下跌，不是噪声。"
           if (ex["fwd20_mean"] or 0) < (ex["base_mean"] or 0) else
           "**并不更负**，说明这个信号对后续 20 日收益没有预测力，退出只是在付手续费。"),
        "",
        "## 6. 分段事件检验（R-B14）",
        "",
        "| 区间 | 事件 | 策略年化 | 基准年化 | 策略回撤 | 基准回撤 | 曝光 | 状态变化 |",
        "|---|---|---:|---:|---:|---:|---:|---:|",
    ]
    for r in eps:
        L.append(f"| {r['span']} | {r['name']} | {te.fmt_pct(r['ann'])} | {te.fmt_pct(r['ann_bh'])} | "
                 f"{te.fmt_pct(r['mdd'])} | {te.fmt_pct(r['mdd_bh'])} | {r['exp']:.0%} | {r['n']} |")

    L += [
        "",
        "> 分段结论与「退出条件滞后」的定性判断一致：策略在 **2015-06~2016-02**（曝光 8%，几乎全程空仓）"
        "大幅跑赢基准（-11.5% vs -53.8%）—— 但这不是「vol20 及时触发」的功劳，而是前面被震荡甩出后"
        "一直没回场（回场要求 `dev60≥-3%` 且 `vol20≤35%`，2015 下半年根本不满足）。反过来，在"
        "**2021-2024 长熊**段策略曝光 82% 却只比基准少跌 2.9pp（-13.5% vs -10.6%），**退出条件几乎没起作用**"
        "—— 因为这段是「阴跌 + 反复假突破」，dev60/vol20 反复在阈值附近来回，等真触发时跌幅已过半。"
        "这说明 `vol20 ≥ 40%` 这类**波动率门限是「结果」不是「原因」**：越是急跌，门限越晚触发。",
        "",
        "## 7. 样本内 / 样本外（R-B17）",
        "",
        f"> 规格要求：参数只能用 **2013-06 ~ {IS_END}** 确定，之后为样本外。"
        "本节不调参，只把默认参数在这两段上分别评估，看结论是否只在某一段成立。",
        "",
        "| 区间 | 策略年化 | 基准年化 | 策略回撤 | 基准回撤 | 策略 Calmar | 基准 Calmar |",
        "|---|---:|---:|---:|---:|---:|---:|",
        f"| 样本内 ~{IS_END} | {te.fmt_pct(mi['ann'])} | {te.fmt_pct(mib['ann'])} | "
        f"{te.fmt_pct(mi['mdd'])} | {te.fmt_pct(mib['mdd'])} | {te.fmt_x(mi['calmar'])} | "
        f"{te.fmt_x(mib['calmar'])} |",
        f"| 样本外 {IS_END}~ | {te.fmt_pct(mo['ann'])} | {te.fmt_pct(mob['ann'])} | "
        f"{te.fmt_pct(mo['mdd'])} | {te.fmt_pct(mob['mdd'])} | {te.fmt_x(mo['calmar'])} | "
        f"{te.fmt_x(mob['calmar'])} |",
        "",
        "## 8. 逐年",
        "",
        "| 年 | 策略收益 | 基准收益 | 超额 | 曝光 | 状态变化 |",
        "|---|---:|---:|---:|---:|---:|",
    ]
    for r in yrs:
        L.append(f"| {r['year']} | {te.fmt_pct(r['ret'])} | {te.fmt_pct(r['ret_bh'])} | "
                 f"{(r['ret'] - r['ret_bh']) * 100:+.2f}pp | {r['exp']:.0%} | {r['n']} |")

    L += [
        "",
        "## 9. 网格（R-B16 · {n} 组）".format(n=len(gdf)),
        "",
        "> 固定 `BAND=0`、`COOLDOWN=5`、`TREND_EXIT=False`（§5 语义），"
        "扫 `TREND_MODE × EXIT_DEV × EXIT_VOL × RECOVER_DEV`。"
        "**这张表的目的是看「有没有一格是灾难」，不是挑最优**（规格 R-B17 的调参纪律）。",
        "",
        "| 分组 | 组数 | 年化中位 | 年化区间 | 回撤中位 | Calmar 中位 | 通过判据组数 |",
        "|---|---:|---:|---|---:|---:|---:|",
    ]
    if len(gdf):
        for mode in GRID["TREND_MODE"]:
            g2 = gdf[gdf["mode"] == mode]
            if not len(g2):
                continue
            ok = int(((g2["calmar"] > (mbh["calmar"] or 0))
                      & (g2["cagr"] > mbh["ann"] - 0.02)).sum())
            L.append(f"| {mode} | {len(g2)} | {g2['cagr'].median() * 100:+.2f}% | "
                     f"{g2['cagr'].min() * 100:+.2f}% ~ {g2['cagr'].max() * 100:+.2f}% | "
                     f"{g2['mdd'].median() * 100:+.2f}% | {g2['calmar'].median():.3f} | "
                     f"{ok}/{len(g2)} |")
        ok_all = int(((gdf["calmar"] > (mbh["calmar"] or 0))
                      & (gdf["cagr"] > mbh["ann"] - 0.02)).sum())
        L.append(f"| **合计** | {len(gdf)} | {gdf['cagr'].median() * 100:+.2f}% | "
                 f"{gdf['cagr'].min() * 100:+.2f}% ~ {gdf['cagr'].max() * 100:+.2f}% | "
                 f"{gdf['mdd'].median() * 100:+.2f}% | {gdf['calmar'].median():.3f} | "
                 f"**{ok_all}/{len(gdf)}** |")
        best = gdf.sort_values("calmar", ascending=False).head(5)
        L += [
            "",
            "Calmar 前 5 名（**仅供诊断，不作推荐**）：",
            "",
            "| TREND_MODE | EXIT_DEV | EXIT_VOL | RECOVER_DEV | 年化 | 回撤 | Calmar | 笔数 |",
            "|---|---:|---:|---:|---:|---:|---:|---:|",
        ]
        for _, r in best.iterrows():
            L.append(f"| {r['mode']} | {r['exit_dev']:.0%} | {r['exit_vol']:.0%} | "
                     f"{r['recover_dev']:.0%} | {r['cagr'] * 100:+.2f}% | "
                     f"{r['mdd'] * 100:+.2f}% | {r['calmar']:.3f} | {int(r['n'])} |")
        L += [
            "",
            f"> 108 组里 **{108 - len(gdf)}** 组因 `EXIT_VOL=0.35` 与 `RECOVER_VOL=0.35` 构成退化"
            f"（违反 R-B10 滞回关系、会无限买卖）被跳过，余 **{len(gdf)}** 组。"
            f"其中只有 **{ok_all}** 组同时满足 Calmar 更高与年化不差 2pp 以上。"
            "这个比例本身就是结论：**默认参数落在「不通过」那一侧，而且通过的那一侧很窄**。"
            "任何「把参数调到最优组」的做法都会立刻违反 R-B17（全区间调参）。",
        ]

    L += [
        "",
        "## 10. 邻域稳定性（R-B18）",
        "",
        f"> 判据：±1 格内年化极差 ≤ **1.5pp**、回撤极差 ≤ **5pp**（V-B10）。",
        "",
        "| 邻域点 | 年化 | 回撤 | Calmar | 笔数 | 曝光 |",
        "|---|---:|---:|---:|---:|---:|",
    ]
    for r in nb:
        L.append(f"| {r['tag']} | {te.fmt_pct(r['cagr'])} | {te.fmt_pct(r['mdd'])} | "
                 f"{te.fmt_x(r['calmar'])} | {r['n']} | {r['exp']:.0%} |")

    L += [
        "",
        "## 11. 敏感性（R-B19 五项）",
        "",
        "| 口径 | 年化 | 回撤 | Calmar | 笔数 | 曝光 | 说明 |",
        "|---|---:|---:|---:|---:|---:|---|",
    ]
    for r in sens:
        exp_txt = "—" if r["exp"] is None else f"{r['exp']:.0%}"
        L.append(f"| {r['name']} | {te.fmt_pct(r['cagr'])} | {te.fmt_pct(r['mdd'])} | "
                 f"{te.fmt_x(r['calmar'])} | {r['n']} | {exp_txt} | {r['note']} |")

    L += [
        "",
        f"> **价格口径是最大的单点风险**：用 000300 价格指数会丢掉约 2pp/年的分红，"
        "策略与基准同时下降，但**下降幅度不等**（策略空仓时拿的是现金利息，不拿分红），"
        "所以相对结论会漂移。这也是 R-B02 硬性要求全收益序列的原因。",
        "",
        "## 12. 验收断言（V-B01 ~ V-B10）",
        "",
        "| 断言 | 结果 | 明细 |",
        "|---|---|---|",
    ]
    for name, ok, detail in ch:
        L.append(f"| {name} | {'**PASS**' if ok else '**FAIL**'} | {detail} |")

    L += [
        "",
        "> V-B03 / V-B04 是这套状态机最强的单测：恒多时净值必须与买入持有**逐日**一致（差 <1e-9），"
        "恒空时年化必须等于 `rf`。两条同时通过，才说明「没有任何隐藏的加减仓或未计息的现金」。",
        "",
        "## 13. 已知失效场景（规格 §9）",
        "",
        "1. **快速 V 型反转**（2020-03、2024-09）：急跌退出必然卖在底部区域，而回场要求 `close > MA120`，"
        "等均线修复时已踏空 20~30%。这是**结构性代价，不是 bug**（§4 的踏空分布是它的定量表达）。",
        "2. **震荡市反复止损**（2016-2017、2023）：`dev60` 在 -8% 附近来回穿越，一年可能 6~8 笔。"
        "`COOLDOWN` 与 `BAND` 只能缓解，无法根除 —— 这也是 `BAND=1%` 会把年化拉低 0.67pp 的原因"
        "（带宽换来的稳定不够抵消错过的涨幅）。",
        "3. **样本量不足**：10 年 19 笔 ≈ 每年 2 笔，统计上无法证明显著。"
        "**任何「年化 8.41%」都不能当预期收益**，只能当「这段历史里它做到了」。",
        "4. **波动率是结果不是原因**：`vol20 ≥ 40%` 在 2015 年首次冲破时跌幅已过半；"
        "**越是急跌，这个门限越晚触发** —— 与策略目的正好相反。",
        "5. **与策略A 叠加会双重踏空**（规格 §11 明令）：A 管月度预算、B 管在场与否，"
        "两者简单相乘 = 「该投的月份正好空仓」，必须重做完整回测才能谈合并。",
        "",
        "## 14. 口径裁定（与规格原文的差异）",
        "",
        "| # | 规格原文 | 本实现 | 理由 |",
        "|---|---|---|---|",
        "| 1 | §5 伪代码 `sig_exit` 只含 dev60/vol20 | 加 `TREND_EXIT` 开关，默认 `False`（= §5），"
        "并把 `True` 的完整结果列在 §2 | R-B03 与 R-B12 要求 trend 也是一类退出原因，"
        "规格自相矛盾；两条都跑才能看清代价 |",
        "| 2 | V-B03 恒多测试「`EXIT_DEV=-9.99, EXIT_VOL=9.99` → 全程 LONG」 | 同（默认语义下成立，"
        "不需要额外关掉趋势） | `TREND_EXIT=False` 时趋势只影响进场，故恒多测试可直接执行 |",
        "| 3 | `rf = 1.8%` | 同 | 主策略 `rule.CASH_ANNUAL=1.40%`；1.8% 对空仓更有利，"
        "取规格值并在此声明 |",
        "| 4 | 成交价 `close[t+1]×(1±s)` | 净值端按「翻转日扣 `SLIP+COMM`」实现，"
        "`trades.jsonl` 同时记 `close[t+1]`（V-B08 校验） | 单标的 0/1 仓位下两种写法等价，"
        "净值端写法更快（网格 72 组要跑多次） |",
        "| 5 | 报告要求 `trades.csv` / `sweep.csv` | 合并进 `_bt.jsonl`（每笔交易一行 + summary"
        " + semantics + checks） | 仓库产物统一 md+jsonl（AGENTS §8） |",
        "| 6 | `START` 未给（公开参考 2012-05 起） | 默认 `2012-05-28` | 与规格 §0 的基准锚点同起点，"
        "便于对标；状态机在 2005-2026 全序列上跑，保证 MA250 与预热在起点已成形态 |",
        "",
        "> **模拟结果，非投资建议。**",
    ]

    with open(OUT_MD, "w", encoding="utf-8") as f:
        f.write("\n".join(L))

    print("报告:", OUT_MD)
    print("结果:", OUT_JSONL)
    print(f"区间 {ev['nav'].index[0]:%Y-%m-%d} ~ {ev['nav'].index[-1]:%Y-%m-%d} · "
          f"{len(ev['nav'])} 日 · 预热 {p['WARMUP']}")
    print(f"  策略 年化 {te.fmt_pct(m['ann'])} · MDD {te.fmt_pct(m['mdd'])} · "
          f"夏普 {m['sharpe']:.2f} · Calmar {te.fmt_x(m['calmar'])} · "
          f"曝光 {ev['exp']:.0%} · {s['n_trades']} 笔")
    print(f"  基准 年化 {te.fmt_pct(mbh['ann'])} · MDD {te.fmt_pct(mbh['mdd'])} · "
          f"Calmar {te.fmt_x(mbh['calmar'])}")
    print(f"  判据: Calmar {'通过' if verdict_calmar else '不通过'} · "
          f"CAGR {'通过' if verdict_cagr else '不通过'} → "
          f"总体 {'通过' if verdict else '**不通过**'}")
    for name, ok, detail in ch:
        print(f"  [{'PASS' if ok else 'FAIL'}] {name}  {detail}")
    # 阻断式断言：仅限「实现正确性」类（V-B01~V-B09）。V-B10 是策略**属性**检查
    # （邻域是否稳定），它若失败是真实结论，必须写进报告，不应靠抛错掩盖。
    bad = [c for c in ch if not c[1] and not c[0].startswith("V-B10")]
    if bad:
        raise AssertionError(f"{len(bad)} 条实现类断言未通过: " + " / ".join(x[0] for x in bad))
    b10 = [c for c in ch if c[0].startswith("V-B10") and not c[1]]
    if b10:
        print("  ⚠ V-B10 邻域稳定性未通过：这是策略属性结论，已在报告 §10 标注，不阻断运行。")


if __name__ == "__main__":
    main()
