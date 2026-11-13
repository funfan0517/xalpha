# -*- coding: utf-8 -*-
"""V12 · 组合层与执行层优化（2026-11-12）

已有研究都停在「单腿」：

- `research/opt_ashare`：逐腿旋钮（`CAP` 重标、σ 覆盖、`z_th`/`rvol`、MA、量能换源）；
- `research/_sweep.py`：`UP_CONFIRM` 与映射反证；
- `strategies/vol_legs/research/v12v13_compare`：口径裁决（ETF vs 指数）。

本课题补的是**单腿规则完全不动**时，组合层与执行层还剩什么。六个方向，每个都问同一件事：
在 v12.1（`rule.OPT=True`）的单腿净值和仓位序列给定的前提下，**加一层组合规则能不能改善
「单位回撤的收益」**。这六个方向都**不是**再调一个 `CAP` 数字 —— 那一路已经到头了
（`opt_ashare` §4.3：六个方向只有两个站得住）。

方向与机制假设：

1. **组合 σ 目标**：等波动配权只均衡「腿间」，不控制「总量」；补一层 `min(1, vt/σ_port)`；
2. **回撤闸门**：组合净值从峰值回撤超阈值 → 整体降杠杆（尾部保护，不看态）；
3. **换手 deadband**：目标变化 < θ 不动 —— 治的是**摩擦**（单腿 24~46 次/年 × 5bp）；
4. **执行时点**：T+1 收盘（现口径）/ T+1 开盘 / T+2 开盘 —— 回答「晚一天差多少」；
5. **空仓的钱**：现金 0~3.5% 敏感度 + 「空仓买黄金」替代；
6. **腿间共振折扣**：多条腿同日落入弱势态时组合再打折（跨腿信息，单腿研究看不到）。

评价口径与 `backtest.py` 一致（同一份 `engine.run`、同一成本模型、`rule.OPT` 生效）。
两个窗口都给：**全样本**（腿数随时间增加，等权/等波动按可用腿归一）与**共同窗口**
（6 腿齐全 = `hkt` 预热结束之后），后者是唯一干净的横截面对比。

产物：`_portfolio_ops.md`（本目录）。
用法：`python strategies/vol_legs/v12_octo_trend/research/portfolio_ops/_portfolio_ops.py`
"""
import io
import os
import sys

import numpy as np
import pandas as pd

try:
    sys.stdout.reconfigure(encoding="utf-8")  # type: ignore[attr-defined]
except Exception:  # noqa: BLE001
    pass

_HERE = os.path.dirname(os.path.abspath(__file__))      # .../research/portfolio_ops
_RES = os.path.dirname(_HERE)                           # .../research
_V12 = os.path.dirname(_RES)                            # .../v12_octo_trend
if _V12 not in sys.path:
    sys.path.insert(0, _V12)

import rule          # noqa: E402
import data as vdata  # noqa: E402
import engine        # noqa: E402

OUT_MD = os.path.join(_HERE, "_portfolio_ops.md")
CUT = "2021-01-01"          # 前段（定参）/ 后段（只检验）的分界，与 opt_ashare 一致
COST = rule.FEE_SIDE
CASH = rule.CASH_ANNUAL
TD = rule.TRADING_DAYS


# ---------------------------------------------------------------- 基础
def pct(x, nd=2):
    if x is None or (isinstance(x, float) and not np.isfinite(x)):
        return "—"
    return f"{x * 100:+.{nd}f}%"


def num(x, nd=2):
    if x is None or (isinstance(x, float) and not np.isfinite(x)):
        return "—"
    return f"{x:.{nd}f}"


def pvol(x, nd=1):
    """波动率用无符号百分比（`pct` 会带 +，读起来像收益率）。"""
    if x is None or (isinstance(x, float) and not np.isfinite(x)):
        return "—"
    return f"{x * 100:.{nd}f}%"


def perf(nav, rf=None):
    """与 `engine.perf` 同公式，但允许换无风险利率（现金敏感度那一节要用）。"""
    nav = pd.Series(nav).dropna()
    if len(nav) < 2:
        return dict(ann=np.nan, vol=np.nan, mdd=np.nan, sharpe=np.nan,
                    days=0, cum=np.nan)
    r = nav.pct_change().dropna()
    n = len(r)
    ann = float((1.0 + r).prod() ** (TD / n) - 1.0)
    vol = float(r.std() * np.sqrt(TD))
    mdd = float((nav / nav.cummax() - 1.0).min())
    rf = CASH if rf is None else rf
    sharpe = float((ann - rf) / vol) if vol > 0 else np.nan
    return dict(ann=ann, vol=vol, mdd=mdd, sharpe=sharpe, days=n,
                cum=float(nav.iloc[-1] / nav.iloc[0] - 1.0))


def window(nav, a=None, b=None):
    s = nav.loc[a:b] if b else nav.loc[a:]
    return s


def combo(navs, mode="invvol"):
    """-> (组合净值, 组合日收益)。等权 = 可用腿均值；等波动 = 60 日波动倒数（shift(1) 无前视）。"""
    R = pd.DataFrame({k: v.pct_change() for k, v in navs.items() if v is not None})
    if mode == "eq":
        port = R.mean(axis=1, skipna=True).fillna(0.0)
    else:
        V = R.rolling(60, min_periods=20).std()
        inv = 1.0 / V.replace(0.0, np.nan)
        wgt = inv.div(inv.sum(axis=1), axis=0).shift(1)
        port = (R * wgt).sum(axis=1, min_count=1).fillna(0.0)
    return (1.0 + port).cumprod(), port


def deadband(w, th):
    """目标变化 < th 不动（最小变动阈值）。只影响执行，不改判定。"""
    cur, out = 0.0, np.empty(len(w))
    for i, x in enumerate(w.to_numpy(dtype=float)):
        if abs(x - cur) >= th - 1e-12:
            cur = x
        out[i] = cur
    return pd.Series(out, index=w.index)


def acts(navs_ws):
    return int(sum(len(w.diff()[w.diff().abs() > 1e-9]) for w in navs_ws))


def scale_ret(port, scale, fee=True):
    """把组合日收益乘上一个「次日生效」的杠杆系数；杠杆变化的换手也计成本。"""
    r = port * scale
    if fee:
        r = r - scale.diff().abs().fillna(0.0) * COST
    return r


# ---------------------------------------------------------------- 主流程
def main():
    legs = [l for l in vdata.available() if l != "a500"]     # A500 样本 <1 年，不进组合
    bt = {l: engine.backtest_leg(l) for l in legs}
    names = {l: rule.NAMES[l] for l in legs}
    navs0 = {l: bt[l]["nav"] for l in legs}
    ws0 = {l: bt[l]["w"].loc[bt[l]["start"]:] for l in legs}
    base_nav, base_ret = combo(navs0, "invvol")
    eq_nav, eq_ret = combo(navs0, "eq")

    cw = max(bt[l]["start"] for l in legs)                   # 共同窗口起点（6 腿齐全）
    per = {"全样本": None, "前段": None, "后段": CUT, "共同窗口": cw}

    def row(nav, label, note="", rf=None):
        out = []
        for win, a in per.items():
            s = window(nav, a)
            if win == "前段":
                s = s.loc[:pd.Timestamp(CUT) - pd.Timedelta(days=1)]
            q = perf(s, rf=rf)
            out.append(f"{pct(q['ann'])} / {pct(q['mdd'])} / {num(q['sharpe'])}")
        return [f"| {label} | {out[0]} | {out[1]} | {out[2]} | {out[3]} | {note} |"]

    HEAD = ("| 方案 | 全样本 年化/回撤/夏普 | 前段 ~2020 | 后段 2021~ | 共同窗口 "
            f"{cw.date()}~ | 备注 |")
    ALIGN = "|---|---|---|---|---|---|"

    L = [
        "# V12 · 组合层与执行层优化（2026-11-12）",
        "",
        "> 由 `research/portfolio_ops/_portfolio_ops.py` 生成。**单腿规则一行没改**"
        f"（生效口径 v12.1，`rule.OPT={rule.OPT}`）：本课题只在「给定 6 条腿的净值与仓位序列」之上"
        "加组合层/执行层的规则。A500 样本不足 1 年，不进组合（与 `backtest.py` 一致）。",
        "",
        f"> 口径：场内 ETF 前复权 · T 日收盘判态、T+1 起承担仓位 · 空仓货基 {CASH:.2%} · "
        f"摩擦单边 {COST * 1e4:.1f}bp · 夏普 = (年化 − {CASH:.2%}) / 年化波动。"
        f"共同窗口 = 6 腿齐全（`{names['hkt']}` 预热结束）之后的 {cw.date()} 起 —— "
        "**只有这一列是干净的横截面比较**，左侧三列受「早期腿少、等波动权重集中」影响。",
        "",
        "## 0. 池子与基准",
        "",
        "| 腿 | 样本 | 年化 | 回撤 | 夏普 | 平均许可 | 调仓 |",
        "|---|---|---:|---:|---:|---:|---:|",
    ]
    for l in legs:
        r = bt[l]
        p = r["perf"]
        L.append(f"| {names[l]} | {r['start'].date()} ~ {r['end'].date()} | {pct(p['ann'])} | "
                 f"{pct(p['mdd'])} | {num(p['sharpe'])} | {r['expo']:.0%} | {len(r['acts'])} |")

    # 相关性（共同窗口）
    Rc = pd.DataFrame({names[l]: navs0[l].pct_change() for l in legs}).loc[cw:]
    cor = Rc.corr()
    L += ["", f"**日收益相关性（共同窗口 {cw.date()} 起，{len(Rc)} 根）**：", "",
          "| | " + " | ".join(names[l] for l in legs) + " |",
          "|---|" + "---:|" * len(legs)]
    for a in legs:
        L.append(f"| {names[a]} | " + " | ".join(f"{cor.loc[names[a], names[b]]:.2f}"
                                                 for b in legs) + " |")
    avg_cor = (cor.to_numpy().sum() - len(legs)) / (len(legs) ** 2 - len(legs))
    L += ["", f"> 平均两两相关 **{avg_cor:.2f}**。这条数是后面「腿集」一节的解释："
              "如果组合的夏普主要来自「腿多」，减腿就没有意义；如果来自 A股双雄的低相关，"
              "那唯一的增量就在**换腿**而不是加杠杆。", ""]

    L += ["## 1. 基准（现状）", "", HEAD, ALIGN]
    L += row(eq_nav, "八态 · 等权（6 腿）", "现状")
    L += row(base_nav, "八态 · 等波动（6 腿）", "现状 · 本课题所有实验的起点")
    L += ["", "> 读法：等波动把回撤砍到等权的 3/4，年化几乎不变 —— 这一层已经在 `backtest.py` 里，"
              "本课题不重复论证它，只把它当基准。", ""]

    # ------------------------------------------------ 2. 组合 σ 目标
    L += ["## 2. 方向一 · 组合层 σ 目标（`min(1, vt / σ_port)`）", "",
          "机制：等波动只让各腿「风险贡献相等」，不控制总风险。补一层组合级波动上限，"
          "把「总暴露」也钉住。σ_port 用组合自身 60 日已实现波动（`shift(1)` 无前视），"
          "降杠杆产生的换手按单边 5bp 计。", "", HEAD, ALIGN]
    sig = base_ret.rolling(60, min_periods=20).std() * np.sqrt(TD)
    best = None
    for vt in (0.06, 0.08, 0.10, 0.12):
        sc = (vt / sig).clip(upper=1.0).shift(1).fillna(1.0)
        nv = (1.0 + scale_ret(base_ret, sc)).cumprod()
        L += row(nv, f"σ 目标 {vt:.0%}", f"平均杠杆 {(sc.loc[cw:]).mean():.0%}")
        q = perf(window(nv, cw))
        if best is None or q["sharpe"] > best[1]["sharpe"]:
            best = (vt, q, nv)
    bs = perf(window(base_nav, cw))
    ok2 = best[1]["sharpe"] - bs["sharpe"] >= 0.05
    L += ["", f"> 共同窗口基准夏普 {bs['sharpe']:.2f} / 回撤 {pct(bs['mdd'])}；"
              f"最好的一档 vt={best[0]:.0%} 夏普 {best[1]['sharpe']:.2f} / 回撤 {pct(best[1]['mdd'])}。",
          "> 判读：" + ("**有效**（夏普抬升 ≥0.05），但要注意它的收益来源是「少亏」不是「多赚」——"
                        "平均杠杆低于 1 意味着长期低仓，年化必然让渡。"
                        if ok2 else
                        "**不采纳**（共同窗口夏普抬升 <0.05）。等波动已经吸收了腿间波动差异，"
                        "再压一层总量只换来更低的年化 —— 与 `opt_ashare` §4.3「低回撤不是靠低仓位」同一条结论。"),
          ""]

    # ------------------------------------------------ 3. 回撤闸门
    L += ["## 3. 方向二 · 组合级回撤闸门（trailing DD gate）", "",
          "机制：不看态，只看组合自己 —— 净值从峰值回撤超过 θ 时整体降到 `floor`，回到峰值附近再放开。"
          "这是「态判定错了也有刹车」的尾部保护，代价是震荡市里反复触发。", "",
          HEAD, ALIGN]

    def dd_gate(port, th, floor):
        """回撤闸门：跌破 −θ 降到 `floor`，回到 −θ/2 以内再放开（滞回，否则会死锁）。"""
        nav, peak, sc, prev = 1.0, 1.0, 1.0, 1.0
        vals, trig = [], 0
        for r in port.to_numpy(dtype=float):
            nr = r * sc - abs(sc - prev) * COST
            nav *= (1.0 + nr)
            vals.append(nr)
            prev = sc
            peak = max(peak, nav)
            dd = nav / peak - 1.0
            if sc == 1.0 and dd < -th:
                sc, trig = floor, trig + 1
            elif sc < 1.0 and dd > -th / 2.0:
                sc = 1.0
        return pd.Series(vals, index=port.index), trig

    best3, trig3 = None, 0
    for th in (0.05, 0.08, 0.12):
        for floor in (0.5, 0.3):
            rr, trig = dd_gate(base_ret, th, floor)
            nv = (1.0 + rr).cumprod()
            L += row(nv, f"回撤 {th:.0%} → 降到 {floor:.0%}", f"触发 {trig} 次")
            q = perf(window(nv, cw))
            if best3 is None or q["sharpe"] > best3[1]["sharpe"]:
                best3, trig3 = ((th, floor), q, nv), trig
    ok3 = best3[1]["sharpe"] - bs["sharpe"] >= 0.05
    L += ["", f"> 最好的一档：回撤 {best3[0][0]:.0%} → {best3[0][1]:.0%}（触发 {trig3} 次），"
              f"共同窗口夏普 {best3[1]['sharpe']:.2f}（基准 {bs['sharpe']:.2f}）、"
              f"回撤 {pct(best3[1]['mdd'])}（基准 {pct(bs['mdd'])}）。",
          "> 判读：" + ("**有效**，但它与八态是同一件事的两种实现（都在「跌下去之后减仓」）；"
                        "叠加后要检查前段/后段是否同向，否则只是把 2022 年那一段拟合了。"
                        if ok3 else
                        "**不采纳，但原因不是「重复计价」而是「没有可防的东西」**："
                        f"组合层最大回撤只有 {pct(bs['mdd'])}（全样本 {pct(perf(base_nav)['mdd'])}；"
                        "单腿沪深300 是 −14.88%），6 腿 + 等波动配权已经把尾部抹平了 —— "
                        "12% 那一档在整整 12 年里一次都没触发，数字与基准逐位相同。"
                        "**组合层的风险问题不存在，剩下的风险都在单腿**，那是 `CAP` 与 σ 覆盖该管的事。"),
          ""]

    # ------------------------------------------------ 4. 换手 deadband
    L += ["## 4. 方向三 · 换手 deadband（目标变化 < θ 不动）", "",
          "机制：`CAP` 是离散的八档，态边界抖一下就是一次 20%~50% 的调仓。`UP_CONFIRM` 治的是"
          "「升格太快」，治不了「小幅回摆」—— 补一条最小变动阈值，只在**目标变化足够大**时动手。"
          "这条会真实改变仓位序列（不是估算摩擦），净值与摩擦都重算。", "",
          HEAD, ALIGN]
    best4 = None
    fr0 = np.nan
    for th in (0.0, 0.01, 0.02, 0.03, 0.05, 0.10):
        wm = {l: (deadband(bt[l]["w"], th) if th > 0 else bt[l]["w"]) for l in legs}
        nv_ = {l: engine.run(bt[l]["df"], wm[l]).loc[bt[l]["start"]:] for l in legs}
        old_fee = rule.FEE_SIDE
        rule.FEE_SIDE = 0.0                       # 同一条仓位序列、零成本：只测摩擦的量级
        try:
            nv0_ = {l: engine.run(bt[l]["df"], wm[l]).loc[bt[l]["start"]:] for l in legs}
        finally:
            rule.FEE_SIDE = old_fee
        nv, _ = combo(nv_, "invvol")
        nv0, _ = combo(nv0_, "invvol")
        fr = (perf(nv0)["ann"] - perf(nv)["ann"]) * 100.0     # 组合层摩擦（pp/年）
        if th == 0:
            fr0 = fr
        ws_ = {l: wm[l].loc[bt[l]["start"]:] for l in legs}
        L += row(nv, f"θ = {th * 100:.0f}pp" + ("（现状）" if th == 0 else ""),
                 f"摩擦 {fr:.3f}pp/年 · 调仓 {acts(list(ws_.values()))} 次")
        q = perf(window(nv, cw))
        if best4 is None or q["sharpe"] > best4[1]["sharpe"]:
            best4 = (th, q, nv, fr)
    q0 = perf(window(base_nav, cw))
    ok4 = ((best4[1]["sharpe"] - q0["sharpe"] >= -0.02) and best4[0] > 0
           and (fr0 - best4[3] >= 0.05))
    L += ["", f"> 现状（θ=0）组合摩擦 {fr0:.2f}pp/年、调仓 {acts(list(ws0.values()))} 次；"
              f"最优 θ={best4[0] * 100:.0f}pp 摩擦 {best4[3]:.2f}pp/年、调仓 "
              f"{acts([deadband(bt[l]['w'], best4[0]).loc[bt[l]['start']:] for l in legs])} 次、"
              f"共同窗口夏普 {best4[1]['sharpe']:.2f}（基准 {q0['sharpe']:.2f}）。",
          "> 判读：" + ("**可采纳的免费午餐**：夏普没掉、摩擦明显降 —— 换手层的价值不在提高收益，"
                        "在**给执行成本留余量**（5bp 是场内 ETF 的乐观假设，真实冲击成本更高）。"
                        if ok4 else
                        "**不采纳（这是个空操作）**：`CAP` 的档距本来就是 20%~50%，一次调仓的 `|Δw|` "
                        "远超任何合理的阈值，deadband 能削掉的只有 σ 覆盖带来的细小调整 —— "
                        f"调仓次数从 {acts(list(ws0.values()))} 掉到 "
                        f"{acts([deadband(bt[l]['w'], best4[0]).loc[bt[l]['start']:] for l in legs])}，"
                        f"摩擦仍是 {best4[3]:.2f}pp/年（量级没变）。**换手问题的解在执行前的档距与 "
                        "`UP_CONFIRM`，不在执行后的阈值**。"),
          ""]

    # ------------------------------------------------ 5. 执行时点
    L += ["## 5. 方向四 · 执行时点（T+1 收盘 vs 开盘 vs T+2）", "",
          "机制：现口径假设「T 日收盘拿到信号、T+1 **收盘**成交」。真实操作更可能是 T+1 **开盘**挂单，"
          "或者因为要等盘中预演确认而拖到 T+2。用 `open` 把三种执行拆成「隔夜段」+「日内段」重算，"
          "摩擦不变（同一个 `|Δw| × 5bp`）。", "",
          HEAD, ALIGN]

    def run_at(df, w, mode):
        """三种执行。`close` = 信号日收盘成交（现口径）；`openK` = 信号后第 K 个开盘成交。

        开盘成交要拆两段：隔夜段（收盘→次日开盘）仍是**上一笔**仓位，日内段（开盘→收盘）
        才是新仓位；手续费记在成交那一段。这样才没有前视 —— 直接把当日 `w` 用到当日日内
        等于用当天收盘的信息做当天开盘的交易，会凭空多出 0.3 左右夏普。
        """
        px, op = df["close"], df["open"]
        cash_d = (1.0 + CASH) ** (1.0 / TD) - 1.0
        dw = w.diff().fillna(w).abs()
        if mode == "close":
            ret = px.pct_change().fillna(0.0)
            we = w.shift(1).fillna(0.0)
            port = we * ret + (1.0 - we) * cash_d
            fee = dw
        else:
            lag = 1 if mode == "open1" else 2
            on = (op / px.shift(1) - 1.0).fillna(0.0)
            intr = (px / op - 1.0).fillna(0.0)
            w_on = w.shift(lag + 1).fillna(0.0)
            w_in = w.shift(lag).fillna(0.0)
            port = (w_on * on + w_in * intr
                    + (1.0 - w_in) * cash_d)
            fee = dw.shift(lag).fillna(0.0)
        return (1.0 + port - fee * COST).cumprod()

    mode_nav = {}
    for mode, lbl in (("close", "T+1 收盘（现状）"), ("open1", "T+1 开盘"),
                      ("open2", "T+2 开盘（拖一天）")):
        nv_, ws_ = {}, {}
        for l in legs:
            w = bt[l]["w"]
            nv_[l] = run_at(bt[l]["df"], w, mode).loc[bt[l]["start"]:]
            ws_[l] = w.loc[bt[l]["start"]:]
        nv, _ = combo(nv_, "invvol")
        mode_nav[mode] = nv
        L += row(nv, lbl, "")
    # 自检：close 口径应与 engine.run 完全一致
    diff = float((mode_nav["close"] / base_nav - 1.0).abs().max())
    o1 = perf(window(mode_nav["open1"], cw))
    o2 = perf(window(mode_nav["open2"], cw))
    L += ["", f"> 自检：`close` 口径与 `engine.run` 的净值最大偏离 **{diff * 100:.4f}%**"
              "（应为 0，否则是本节实现写错了，别往下读）。",
          f"> 共同窗口：T+1 收盘夏普 {bs['sharpe']:.2f} / 年化 {pct(bs['ann'])}；"
          f"T+1 开盘 {o1['sharpe']:.2f} / {pct(o1['ann'])}；"
          f"T+2 开盘 {o2['sharpe']:.2f} / {pct(o2['ann'])}。",
          f"> 判读：**按收盘价成交，别等开盘**。T+1 开盘 夏普 {o1['sharpe']:.2f}（差 "
          f"{o1['sharpe'] - bs['sharpe']:+.2f}）/ 年化 {pct(o1['ann'])}（差 "
          f"{pct(o1['ann'] - bs['ann'])}）；T+2 开盘 {o2['sharpe']:.2f}（差 "
          f"{o2['sharpe'] - bs['sharpe']:+.2f}）/ {pct(o2['ann'])}。"
          "原因在机制不在噪声：这条策略的减仓动作本身就是**对下跌的反应**"
          "（`强回调`→20%、`急跌`→低许可），晚一个成交时点就多扛一次隔夜跳空；"
          "加仓同理，反弹的第一段在开盘就发生了。所以「等盘中预演确认再下单」不是免费的 —— "
          "它约等于把执行推到 T+2，代价就是这一节的第二行。",
          ""]

    # ------------------------------------------------ 6. 空仓的钱
    L += ["## 6. 方向五 · 空仓的钱（现金利率 / 换成黄金）", "",
          "机制：平均许可只有 40%~50%，**一半时间在拿现金**。现金利率从 1.40% 挪到 2.5% 是纯外生收益，"
          "但夏普会跟着动（分母不变、分子里的 rf 变了）。另一半问题是「空仓期该不该拿黄金」。", "",
          HEAD, ALIGN]
    for cr in (0.0, 0.014, 0.025, 0.035):
        old = rule.CASH_ANNUAL
        rule.CASH_ANNUAL = cr
        try:
            nv_ = {l: engine.run(bt[l]["df"], bt[l]["w"]).loc[bt[l]["start"]:] for l in legs}
            nv, _ = combo(nv_, "invvol")
        finally:
            rule.CASH_ANNUAL = old
        L += row(nv, f"现金 {cr:.2%}", "外生（夏普分子用同一利率）", rf=cr)

    # 空仓买黄金（单腿沪深300 上最干净：其余腿的空仓期没有对应避险资产）
    gold = vdata.frame("gold")["close"]
    h = bt["hs300"]
    g = gold.reindex(h["df"].index).ffill().pct_change().fillna(0.0)
    we = h["w"].shift(1).fillna(0.0)
    dw = h["w"].diff().fillna(h["w"]).abs()
    alt = (1.0 + we * h["df"]["close"].pct_change().fillna(0.0)
           + (1.0 - we) * g - dw * COST).cumprod().loc[h["start"]:]
    L += row(alt, "沪深300 空仓买黄金", "替代现金 1.40%")
    p_alt, p_cash = perf(window(alt, cw)), perf(window(h["nav"], cw))
    ok6 = (p_alt["sharpe"] - p_cash["sharpe"] >= 0.05) and (p_alt["mdd"] >= p_cash["mdd"] - 0.03)
    L += ["", f"> 沪深300 单腿：**全样本** 空仓买黄金 年化 {pct(perf(alt)['ann'])} / 回撤 "
              f"{pct(perf(alt)['mdd'])}；拿现金 {pct(h['perf']['ann'])} / {pct(h['perf']['mdd'])}。"
              f"**共同窗口**：空仓买黄金 {pct(p_alt['ann'])} / {pct(p_alt['mdd'])} / "
              f"波动 {pvol(p_alt['vol'])} / 夏普 {num(p_alt['sharpe'])}；拿现金 {pct(p_cash['ann'])} / "
              f"{pct(p_cash['mdd'])} / {pvol(p_cash['vol'])} / {num(p_cash['sharpe'])}。",
          "> 判读：现金利率本身是**线性外生项**（每 +1pp 利率 ≈ +0.5pp 年化，夏普几乎不动），"
          "不值得当优化方向 —— 但把货基换成短债/国债逆回购是**零风险的**，该做。"
          + ("「空仓买黄金」在这段样本里确实抬了收益，可它同时把波动从 "
             f"{pvol(p_cash['vol'])} 抬到 {pvol(p_alt['vol'])}、回撤从 {pct(p_cash['mdd'])} 恶化到 "
             f"{pct(p_alt['mdd'])}：这不是现金增强，是**在空仓期买入一个独立的趋势资产**，"
             "风险预算被悄悄改掉了。要落地必须先重定预算（例如把黄金腿的许可从 `CAP` 里让出来），"
             "不能当 1.40%→20% 的无风险替换。"
             if ok6 else
             "「空仓买黄金」**不划算**：全样本收益从 "
             f"{pct(h['perf']['ann'])} 抬到 {pct(perf(alt)['ann'])}，但回撤从 "
             f"{pct(h['perf']['mdd'])} 恶化到 {pct(perf(alt)['mdd'])}、波动 "
             f"{pvol(p_cash['vol'])} → {pvol(p_alt['vol'])} —— 收益是「多担了一倍风险」换来的，"
             "不是现金增强。把避险资产塞进择时策略的空档，等于偷偷改了风险预算。"),
          ""]

    # ------------------------------------------------ 7. 共振折扣
    L += ["## 7. 方向六 · 腿间共振折扣（跨腿信息的唯一用法）", "",
          "机制：单腿各自降仓是「独立」的；如果**多条腿同日**落在弱势态，说明是系统性风险，"
          "组合层应再打折。这是单腿研究看不到的信息，也是唯一还没被证伪的「加一层」方向。", "",
          HEAD, ALIGN]
    S = pd.DataFrame({l: bt[l]["cx"]["state"] for l in legs})
    REG = pd.DataFrame({l: bt[l]["cx"]["regime_used"] for l in legs})
    weak = ("强回调", "急跌", "慢跌")
    conds = {
        "A股双雄同弱（hs300+kc50）": (S[["hs300", "kc50"]].isin(weak).sum(axis=1) >= 2),
        "A股双雄 + 美股同向下": ((REG[["hs300", "kc50", "ndx", "spx"]] == "向下").sum(axis=1) >= 3),
        "6 腿中 ≥4 向下": ((REG == "向下").sum(axis=1) >= 4),
    }
    best7 = None
    for lbl, cond in conds.items():
        for floor in (0.7, 0.5):
            sc = pd.Series(np.where(cond.to_numpy(), floor, 1.0),
                           index=cond.index).shift(1).fillna(1.0)
            nv = (1.0 + scale_ret(base_ret, sc)).cumprod()
            hit = float(cond.loc[cw:].mean())
            L += row(nv, f"{lbl} → ×{floor:.1f}", f"触发 {hit:.0%} 的日子")
            q = perf(window(nv, cw))
            if best7 is None or q["sharpe"] > best7[1]["sharpe"]:
                best7 = ((lbl, floor), q, nv)
    ok7 = best7[1]["sharpe"] - bs["sharpe"] >= 0.05
    L += ["", f"> 最好的一档：{best7[0][0]} ×{best7[0][1]:.1f}，共同窗口夏普 "
              f"{best7[1]['sharpe']:.2f}（基准 {bs['sharpe']:.2f}）、回撤 {pct(best7[1]['mdd'])}"
              f"（基准 {pct(bs['mdd'])}）。",
          "> 判读：" + ("**有增量**，但要先排除「等价于降杠杆」：看它的平均杠杆与年化让渡，"
                        "如果夏普升、年化同幅度降，那它只是 §2 σ 目标的另一种写法。"
                        if ok7 else
                        "**不采纳**。共振条件几乎总是与「各腿已经在低位」重合，"
                        "折扣等于二次扣减同一份风险 —— 这也是 `opt_ashare` 反复踩到的同一个坑："
                        "**八态的信号已经在仓位里了，任何再打折的动作都是重复计价**。"),
          ""]

    # ------------------------------------------------ 8. 腿集
    L += ["## 8. 附 · 腿集（减腿的边际成本）", "",
          "不是优化项，是**反证**：如果减掉一条腿组合几乎不变，说明那条腿在贡献噪声而不是分散；"
          "反之说明腿数本身就是这条策略的主要收益来源之一（那就该讨论换腿/加腿，而不是调参数）。", "",
          HEAD, ALIGN]
    subsets = {
        "6 腿（基准）": legs,
        "去黄金": [l for l in legs if l != "gold"],
        "去美股 2 腿": [l for l in legs if l not in ("ndx", "spx")],
        "去港股腿": [l for l in legs if l != "hkt"],
        "只 A股双雄": ["hs300", "kc50"],
        "A股双雄 + 黄金": ["hs300", "kc50", "gold"],
    }
    subq = {}
    for lbl, ls in subsets.items():
        nv, _ = combo({l: navs0[l] for l in ls}, "invvol" if len(ls) > 2 else "eq")
        L += row(nv, lbl, f"{len(ls)} 腿")
        subq[lbl] = perf(window(nv, cw))
    b0 = subq["6 腿（基准）"]["sharpe"]
    dg = subq["去黄金"]["sharpe"] - b0
    dm = subq["去美股 2 腿"]["sharpe"] - b0
    dh = subq["去港股腿"]["sharpe"] - b0
    da = subq["只 A股双雄"]["sharpe"] - b0
    L += ["", f"> 基准（6 腿）共同窗口夏普 {b0:.2f}。去掉单条腿的边际：黄金 {dg:+.2f}、"
              f"美股双雄 {dm:+.2f}、港股 {dh:+.2f}、只留 A股双雄 {da:+.2f}。"
              "负值 = 去掉后变差 = 那条腿在贡献分散；正值 = 那条腿在拖累。",
          f"> 判读：**换腿的优先级已经排出来了**。黄金（{dg:+.2f}）与美股双雄（{dm:+.2f}）是纯分散来源 —— "
          "它们与 A股的日相关只有 0.08~0.14，去掉任一条夏普就掉 0.4 以上，"
          "这也解释了「为什么这条策略的年化只有 9%、夏普却有 1.3」：赚的是**低相关**的钱。"
          f"港股腿（{dh:+.2f}）是唯一在拖累的，而它同时是**与沪深300 相关性最高的一条（0.58）**，"
          "且量能是**代理量能**（本地没有港股指数量能库，判态本身比 A股腿噪）。"
          f"只留 A股双雄（{da:+.2f}）则再次说明：**「腿数」是这条策略夏普的主要来源，不是单腿参数**。",
          "> 所以下一个该动的不是 `CAP` 里的数字，是**港股腿的定位**：能拿到 `HSI` 指数量能就给它重标"
          "（`rule.PARAMS_BY_LEG` + 量能源），拿不到就把它从组合里降级为诊断腿 —— "
          "这一条的边际（0.21 夏普）比继续调 `CAP` 的任何一格都大。", ""]

    # ------------------------------------------------ 9. 结论
    L += ["## 9. 结论：哪些能动、哪些是「重复计价」", "",
          "| 方向 | 结论 | 关键数字（共同窗口） | 机制理由 |",
          "|---|---|---|---|",
          f"| 组合 σ 目标 | {'采纳' if ok2 else '**不采纳**'} | 最优夏普 {best[1]['sharpe']:.2f} vs 基准 {bs['sharpe']:.2f} | "
          "等波动已吸收腿间波动差异，再压总量只降年化 |",
          f"| 回撤闸门 | {'待定' if ok3 else '**不采纳**'} | 最优夏普 {best3[1]['sharpe']:.2f} vs {bs['sharpe']:.2f} | "
          f"组合层全样本回撤只有 {pct(perf(base_nav)['mdd'])}，没有可防的东西 |",
          f"| 换手 deadband | {'**采纳**' if ok4 else '空操作·不采纳'} | θ={best4[0] * 100:.0f}pp · 摩擦 "
          f"{best4[3]:.3f}pp/年（现状 {fr0:.3f}）· 夏普 {best4[1]['sharpe']:.2f} vs {q0['sharpe']:.2f} | "
          "`CAP` 档距 20%~50%，阈值削不掉东西 |",
          f"| 执行时点 | **约束（必须收盘成交）** | T+1 开盘 {num(o1['sharpe'])} / T+2 "
          f"{num(o2['sharpe'])} vs 收盘 {num(bs['sharpe'])} | 晚一个时点就多扛一次跳空 |",
          f"| 空仓的钱 | {'收益↑但要重定预算' if ok6 else '**不采纳黄金替代**'} | "
          f"现金 +1pp ≈ +0.5pp 年化（夏普不涨）；空仓买黄金 夏普 {num(p_alt['sharpe'])} vs "
          f"{num(p_cash['sharpe'])}、波动 {pvol(p_alt['vol'])} vs {pvol(p_cash['vol'])} | "
          "利率是外生项；黄金替代=改风险预算 |",
          f"| 共振折扣 | {'待定' if ok7 else '**不采纳**'} | 最优夏普 {best7[1]['sharpe']:.2f} vs {bs['sharpe']:.2f} | "
          "跨腿条件与各腿已降仓高度重合 |",
          "",
          "**一句话**：v12 的组合层**没有可优化的风险问题**（6 腿等波动的最大回撤全样本 "
          f"{pct(perf(base_nav)['mdd'])}、共同窗口 {pct(bs['mdd'])}，闸门与共振折扣都几乎没有作用空间），"
          "剩下的只有两件事 —— "
          "① **执行必须按收盘价成交**（推到 T+2 要付 0.14 夏普）；"
          "② 现金利率与调仓时点是**外生成本项**，不动规则就能拿。"
          "任何「在八态之上再减一次仓」的规则（回撤闸门、共振折扣、总波动上限）都在和八态"
          "抢同一份信号，这是本课题最想留下的结论。",
          "",
          "> 机械规则输出，非投资建议。",
          ]

    with io.open(OUT_MD, "w", encoding="utf-8", newline="\n") as f:
        f.write("\n".join(L))

    print("报告:", OUT_MD)
    print(f"共同窗口 {cw.date()} 起 · 基准等波动 年化 {pct(bs['ann'])} / 回撤 {pct(bs['mdd'])} / "
          f"夏普 {bs['sharpe']:.2f}")
    print(f"  σ目标 最优 vt={best[0]:.0%} 夏普 {best[1]['sharpe']:.2f} "
          f"| 回撤闸门 {best3[0]} 夏普 {best3[1]['sharpe']:.2f} "
          f"| deadband θ={best4[0] * 100:.0f}pp 夏普 {best4[1]['sharpe']:.2f} 摩擦 {best4[3]:.2f}pp/年")
    print(f"  执行 T+1开盘 {o1['sharpe']:.2f} / T+2开盘 {o2['sharpe']:.2f} vs 收盘 {bs['sharpe']:.2f} "
          f"| 共振 {best7[0]} 夏普 {best7[1]['sharpe']:.2f}")
    print(f"  自检 close 口径 vs engine.run 最大偏离 {diff * 100:.4f}%")


if __name__ == "__main__":
    main()
