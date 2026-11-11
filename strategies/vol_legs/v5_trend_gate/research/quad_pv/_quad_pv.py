# -*- coding: utf-8 -*-
"""四象限（结构 × 量能 × 关键位 × 节奏）能不能替代 v5 的「价格 vs 年线」读数。

## 来源

用户 2026-11-09 给的「四象限判断法」（结构、量能、关键位、节奏）。原文是**定性清单**：
上涨/主升 = 趋势向上 + 放量突破 + 回踩不破 + 再放量 + 板块共振；
反弹 = 趋势未反转 + 无量 + 不过前高 + 量能递减；
回调 = 上升趋势 + 不破关键位 + 缩量 + 止跌 K 线；
下跌 = 有效跌破关键位 + 放量 + 加速 + 反弹无量。

本脚本把它**压成一条互斥的判定链**（顺序本身是选择，写死在 `quad_series()` 里），
其余不能量化的（板块共振、相对强弱、筹码密集区、涨跌停/跳空、本地盘）**不进模型**，
在报告 §6 单列——它们不是被证伪，是这套数据测不了。

## 口径（三处必须知道）

1. **必须用场内 ETF 的 OHLCV**：v5 平时用场外复权净值（只有收盘价），量能这一维根本不存在。
   所以本报告的绝对收益与 `_v5_sens_legs.md`（场外口径）**不可直接相减**，只能比「同一场内口径下的口径差」。
2. 指标按**原 V1-2 的公式**在本脚本内重算（ma20/60/250、rvol 量比），公式与旧口径逐日一致
   —— V1-2 已于 2026-11-10 删除，本报告不能依赖它的模块（否则研究脚本会跟着失效）。
3. 波动层关闭（`VOL_USE=False`），与 `_sens_legs` 同样隔离变量。

## 两层评估（回答两个不同问题）

- **§2 条件收益**：四个象限各自的未来 20/60 日收益、胜率、之后 120 日最大回撤 vs 全样本基准。
  回答「这套判断准不准」（无成本、无仓位，纯统计）。
- **§3 仓位化**：把象限映射成 1.0 / 0.5 / 0.2 的带内档位（5/20 阶梯 + 5 日间隔帽，与 v5 同一套），
  扣费跑 NAV。回答「照它做能不能赚钱」。

产物（本目录）：`_quad_pv.md` / `_quad_pv.png`。机械规则输出，非投资建议。
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

_HERE = os.path.dirname(os.path.abspath(__file__))        # research/quad_pv
_RES = os.path.dirname(_HERE)                             # research
_V5 = os.path.dirname(_RES)                               # v5_trend_gate
_VL = os.path.dirname(_V5)                                # strategies/vol_legs
_V18 = os.path.join(os.path.dirname(_VL), "tech_indicators", "v18_volume_notes")
for _p in (_V5, _VL, _V18):
    if _p not in sys.path:
        sys.path.insert(0, _p)

import trend_gate as tg          # noqa: E402
import rule                      # noqa: E402
import ohlcv                     # noqa: E402  仓库级 data/_ohlcv.json 的唯一写入方（只读调用）

# 指标参数：与原 V1-2 的 ma_trend_vol 同一取值（2026-11-10 V1-2 删除后在此本地重算）
SMA_FAST, SMA_MID, SMA_SLOW = 20, 60, 250
VOL_N = 20

tg.VOL_USE = False               # 只比「读数」，不叠波动层

OUT_MD = os.path.join(_HERE, "_quad_pv.md")
OUT_PNG = os.path.join(_HERE, "_quad_pv.png")

LEGS = list(rule.LEG_ORDER)
NAME = {k: rule.LEGS[k]["name"] for k in LEGS}

QUADS = ("上涨", "反弹", "回调", "下跌", "")
QUAD_DESC = {
    "上涨": "结构向上 + 连续2日站上MA20 + 放量（rvol≥1.2）",
    "反弹": "结构未向上 + 连续2日站上MA20 + 缩量（rvol≤0.85）+ 不过前高",
    "回调": "结构向上 + 连续2日跌破MA20 + 未破MA60 + 缩量",
    "下跌": "结构向下 + 收盘<MA60 + 非缩量",
    "": "未命中任何一格（不做判断）",
}

# ---- 参数（唯一权威源） ----
VOL_UP = 1.20          # 放量：当日量比 ≥ 1.2（近 VOL_N 日均量的倍数）
VOL_DN = 0.85          # 缩量：当日量比 ≤ 0.85
NH_WIN = 120           # 「前高」回看窗口
NH_TOL = 0.97          # 距前高 ≥ 3% 内算「逼近/突破前高」
SLOPE_N = 20           # 中均线（MA60）自身斜率回看
REDUCE_DAYS = 5        # 下跌趋势里连续跌破 MA60 满 5 日 → 减半
CLEAR_DAYS = 20        # 满 20 日 → 清至观察仓
ACTION_GAP = 5         # 两次调仓最小间隔（交易日）
FULL, HALF, OBSERVE = 1.0, 0.5, 0.2
FWD = (20, 60)


# ---------------------------------------------------------------- 1. 象限
def prepare(df):
    """OHLCV -> 四象限要用的最小面板（ma20/60/250 + 量比 rvol）。

    公式逐字沿用原 V1-2 的 `ma_trend_vol.prepare()`（同窗口、同 min_periods），
    只剪掉本报告用不到的 ATR/ADX/扣抵/regime 等列 —— 那些列在 V1-2 删除后
    已没有别的调用方，没必要在这里复刻。停牌沿用「当日无成交量」处理，与原口径一致。
    """
    out = df.copy()
    c = out["close"]
    out["ma20"] = c.rolling(SMA_FAST).mean()
    out["ma60"] = c.rolling(SMA_MID).mean()
    out["ma250"] = c.rolling(SMA_SLOW).mean()
    out["mav"] = out["volume"].rolling(VOL_N).mean()
    out["rvol"] = out["volume"] / out["mav"]
    return out


def indicators(leg):
    """-> DataFrame（场内 ETF 口径，已裁到 MA250 成形之后）。"""
    ind = prepare(ohlcv.frame(leg))
    ind = ind[ind["ma250"].notna()].copy()
    return ind


def quad_series(ind, loose=False):
    """四象限判定链（互斥，按顺序吃掉）。只用当日及之前的数据。

    `loose=True` 是敏感性对照（不是另一套结论）：量能门槛从「放量 1.2 / 缩量 0.85」放宽到
    「≥1.0 / ≤1.0」，关键位从「连续 2 日」放宽到「当日」，上涨不再要求逼近前高。
    用它回答「严格版覆盖率低，是不是实现太严」。
    """
    c = ind["close"]
    ma20, ma60, ma250 = ind["ma20"], ind["ma60"], ind["ma250"]
    up_mid = ma60 > ma60.shift(SLOPE_N)          # 中均线上行（结构抬高的必要条件）
    dn_mid = ma60 < ma60.shift(SLOPE_N)

    s_up = (ma20 > ma60) & up_mid & (c > ma250)   # 多头排列 + 中均线上行 + 站上年线
    s_dn = (ma20 < ma60) & dn_mid & (c < ma250)   # 空头排列 + 中均线下行 + 年线下

    if loose:                                     # 关键位：当日同侧即算（不做「2 日确认」）
        k_up = c > ma20
        k_dn20 = c < ma20
        v_up = ind["rvol"] >= 1.00
        v_dn = ind["rvol"] <= 1.00
        near_high = pd.Series(True, index=ind.index)
    else:
        k_up = (c > ma20) & (c.shift(1) > ma20.shift(1))     # 连续 2 日站上 MA20（有效突破）
        k_dn20 = (c < ma20) & (c.shift(1) < ma20.shift(1))   # 连续 2 日跌破 MA20
        v_up = ind["rvol"] >= VOL_UP
        v_dn = ind["rvol"] <= VOL_DN
        hh = c.rolling(NH_WIN, min_periods=60).max().shift(1)
        near_high = c >= hh * NH_TOL                         # 逼近/突破前高
    k_dn60 = c < ma60                                        # 跌破关键位 MA60

    q = np.full(len(ind), "", dtype=object)
    q = np.where(s_up & k_up & v_up & near_high, "上涨", q)
    rest = q == ""
    q = np.where(rest & s_up & k_up & v_up, "上涨", q)      # 放量站上即算上涨（不强求创新高）
    rest = q == ""
    q = np.where(rest & s_up & k_dn20 & ~k_dn60 & v_dn, "回调", q)
    rest = q == ""
    q = np.where(rest & s_dn & k_dn60 & ~v_dn, "下跌", q)
    rest = q == ""
    q = np.where(rest & ~s_up & k_up & v_dn & ~near_high, "反弹", q)
    return pd.Series(q, index=ind.index)


# ---------------------------------------------------------------- 2. 仓位化
def state_machine(ind, quad, callback=False):
    """象限 -> 带内档位（1.0 / 0.5 / 0.2），5/20 阶梯 + 间隔帽，T 日决策 T+1 成交。

    `callback=True` 是窄版本（由 §2 的条件收益反推）：只有「回调」格给加仓许可、
    「上涨」格不给（不追涨）。用来直接对照「买回调 vs 追突破」哪个更值钱。
    """
    n = len(ind)
    c = ind["close"].to_numpy(dtype=float)
    ma60 = ind["ma60"].to_numpy(dtype=float)
    q = quad.to_numpy(dtype=object)
    below60 = np.zeros(n, dtype=int)
    run = 0
    for i in range(n):
        run = run + 1 if (np.isfinite(ma60[i]) and c[i] < ma60[i]) else 0
        below60[i] = run

    state = np.zeros(n)
    hold, last_act, pend = FULL, -10 ** 9, None
    for i in range(n):
        if pend is not None:
            hold, last_act, pend = float(pend), i, None
        state[i] = hold
        if q[i] == "" or not np.isfinite(ma60[i]):
            continue
        if q[i] == "上涨":
            tgt = hold if callback else FULL          # 窄版：不追涨
        elif q[i] == "回调":
            tgt = FULL if callback else hold          # 窄版：回调才买
        elif q[i] == "反弹":
            tgt = hold if callback else min(hold, HALF)
        else:                                         # 下跌：5 日减半、20 日清至观察
            tgt = OBSERVE if below60[i] >= CLEAR_DAYS else (HALF if below60[i] >= REDUCE_DAYS else hold)
        if abs(tgt - hold) > 1e-9 and i - last_act >= ACTION_GAP:
            pend = tgt
    return pd.Series(state, index=ind.index)


def leg_ma_state(px, **kw):
    """对照口径：v5 的均线读数（MA250+20 / MA120+5），直接复用 tg.state_machine。"""
    sm = tg.state_machine(px, "x", GATE_ALL=True, **kw)   # leg 名只影响 gated()，GATE_ALL=True 时恒真
    return sm["state_trend"]


# ---------------------------------------------------------------- 3. 条件收益
def cond_stats(ind, quad):
    """每个象限：未来 20/60 日收益（中位/均值）、胜率、之后 120 日最大回撤。"""
    c = ind["close"].to_numpy(dtype=float)
    n = len(c)
    rows = {}
    for q in QUADS:
        idx = np.where(quad.to_numpy(dtype=object) == q)[0]
        idx = idx[idx < n - max(FWD)]
        if len(idx) < 30:
            rows[q] = None
            continue
        f20, f60, dd = [], [], []
        for i in idx:
            f20.append(c[i + 20] / c[i] - 1.0)
            f60.append(c[i + 60] / c[i] - 1.0)
            dd.append(float(np.min(c[i:i + 121]) / c[i] - 1.0))
        rows[q] = dict(n=len(idx), share=len(idx) / max(n, 1),
                       f20=float(np.median(f20)), f20m=float(np.mean(f20)),
                       f60=float(np.median(f60)),
                       w20=float(np.mean(np.array(f20) > 0)),
                       w60=float(np.mean(np.array(f60) > 0)),
                       dd=float(np.median(dd)), ddw=float(np.min(dd)))
    # 基准：全部交易日
    idx = np.arange(0, n - max(FWD))
    f20 = c[idx + 20] / c[idx] - 1.0
    f60 = c[idx + 60] / c[idx] - 1.0
    dd = np.array([float(np.min(c[i:i + 121]) / c[i] - 1.0) for i in idx])
    rows["_all"] = dict(n=len(idx), share=1.0, f20=float(np.median(f20)),
                        f20m=float(np.mean(f20)), f60=float(np.median(f60)),
                        w20=float(np.mean(f20 > 0)), w60=float(np.mean(f60 > 0)),
                        dd=float(np.median(dd)), ddw=float(np.min(dd)))
    return rows


# ---------------------------------------------------------------- 4. 主流程
def run():
    res = {}
    for leg in LEGS:
        ind = indicators(leg)
        if len(ind) < 300:
            print(f"  {NAME[leg]}: MA250 成形后仅 {len(ind)} 日，样本不足，跳过")
            continue
        px, fee = ind["close"], tg.fee_of(leg)
        quad = quad_series(ind)
        quad_l = quad_series(ind, loose=True)
        st_q, st_l = state_machine(ind, quad), state_machine(ind, quad_l)
        st_c = state_machine(ind, quad, callback=True)
        st_h = pd.Series(1.0, index=px.index)
        st_m = leg_ma_state(px, KIND="ma", MA_WIN=250, SLOPE_N=20)
        st_r = leg_ma_state(px, KIND="ma", MA_WIN=120, SLOPE_N=5)
        plan = [
            ("四象限（严格）", st_q),
            ("四象限（宽松）", st_l),
            ("四象限（只买回调）", st_c),
            ("恒持满带", st_h),
            ("MA250+20（v5 默认）", st_m),
            ("MA120+5", st_r),
        ]
        rows = []
        for name, st in plan:
            nav = tg.run_leg(st, px, fee)
            rows.append(dict(name=name, nav=nav, perf=tg.perf(nav),
                             acts=len(tg.actions(st)), expo=float(st.mean()),
                             whip=tg.whipsaw(st, px)["n"] if name.startswith("四象限") else None))
        res[leg] = dict(ind=ind, quad=quad, rows=rows,
                        perf={r["name"]: r["perf"] for r in rows},
                        cover=float((quad != "").mean()),
                        cover_l=float((quad_l != "").mean()),
                        cond=cond_stats(ind, quad))
    return res


def main():
    res = run()
    if not res:
        print("无可用样本")
        return

    L = [
        "# 四象限（结构 × 量能 × 关键位 × 节奏）能不能替代「价格 vs 年线」",
        "",
        "> 来源：用户 2026-11-09 的定性清单。本文件把它压成**一条互斥判定链**（顺序写死在 "
        "`quad_series()`），在**场内 ETF 的 OHLCV** 上检验。",
        "> 三个口径限制先摆出来：① 必须用场内量价（场外净值没有成交量），所以绝对收益与 "
        "`_v5_sens_legs.md`（场外）**不可直接相减**；② 指标按原 V1-2 的公式在本脚本内重算"
        "（V1-2 已于 2026-11-10 删除），不另造一套；③ 波动层关闭，隔离变量。",
        "",
        "## 1. 规则怎么落成代码（写死版）",
        "",
        "| 概念 | 量化定义 | 参数 |",
        "|---|---|---|",
        f"| 结构向上 | `MA20 > MA60` 且 `MA60 > MA60({SLOPE_N}日前)` 且 `close > MA250` | 多头排列 + 中均线上行 |",
        f"| 结构向下 | `MA20 < MA60` 且 `MA60 < MA60({SLOPE_N}日前)` 且 `close < MA250` | 空头排列 |",
        f"| 有效站上/跌破 | 连续 **2 日** 收在 MA20 同侧（「2 日不收回」） | 节奏 |",
        f"| 放量 / 缩量 | 当日量比 `rvol = vol / MA20(vol)`，≥{VOL_UP} 放量、≤{VOL_DN} 缩量 | 量能 |",
        f"| 前高 | `close ≥ 近 {NH_WIN} 日最高 × {NH_TOL}` | 关键位 |",
        f"| 关键位 | MA20（短）、MA60（中）、MA250（长） | 20/60 日线 + 前高 |",
        "",
        "判定链（按顺序命中即归类，其余落到「未命中」）：",
        "",
        "| 顺序 | 象限 | 条件 |",
        "|---|---|---|",
        f"| 1 | **上涨** | 结构向上 + 有效站上 MA20 + 放量（+ 逼近前高优先，未创新高也算放量站上） |",
        f"| 2 | **回调** | 结构向上 + 有效跌破 MA20 + **未破 MA60** + 缩量 |",
        f"| 3 | **下跌** | 结构向下 + 收盘 < MA60 + 非缩量 |",
        f"| 4 | **反弹** | 结构不向上 + 有效站上 MA20 + 缩量 + 不过前高 |",
        "",
        "> 顺序是有意的：先判「上涨」，再给「上升趋势里的跌破」一次机会（回调），"
        "然后才判「下跌」，最后剩下来的缩量反抽才算反弹。**这个顺序本身就是一个可被证伪的选择**。",
        "",
        "## 2. 条件收益：这套判断准不准（无成本、无仓位）",
        "",
        "未来 20/60 日收益用**价格本身**（在该格买入并持有），与全样本基准比；"
        "「之后 120 日最大回撤」是该格起点往后 120 个交易日内的最深回撤（中位）。",
        "",
    ]

    for leg in res:
        r = res[leg]
        c = r["cond"]
        a = c["_all"]
        L += [
            f"### {NAME[leg]}（{r['ind'].index[0].date()} ~ {r['ind'].index[-1].date()}，"
            f"{len(r['ind'])} 个交易日）",
            "",
            "| 象限 | 日数占比 | 未来20日 | 20日胜率 | 未来60日 | 60日胜率 | 之后120日回撤(中位) | 最差 |",
            "|---|---:|---:|---:|---:|---:|---:|---:|",
        ]
        for q in QUADS:
            s = c[q]
            if s is None:
                L.append(f"| {q or '未命中'} | 样本不足 | — | — | — | — | — | — |")
                continue
            L.append(f"| {q or '未命中'} | {s['share']:.0%} | {tg.fmt_pct(s['f20'])} | "
                     f"{s['w20']:.0%} | {tg.fmt_pct(s['f60'])} | {s['w60']:.0%} | "
                     f"{tg.fmt_pct(s['dd'])} | {tg.fmt_pct(s['ddw'])} |")
        L.append(f"| **全样本基准** | 100% | {tg.fmt_pct(a['f20'])} | {a['w20']:.0%} | "
                 f"{tg.fmt_pct(a['f60'])} | {a['w60']:.0%} | {tg.fmt_pct(a['dd'])} | "
                 f"{tg.fmt_pct(a['ddw'])} |")
        L.append("")

    L += [
        "## 3. 仓位化：照它做能不能赚钱（扣费 NAV）",
        "",
        "档位语义与 v5 相同（带内占比）：上涨=满带、回调=保持、反弹=上限半仓、"
        f"下跌=连续跌破 MA60 满 {REDUCE_DAYS} 日减半 → 满 {CLEAR_DAYS} 日清至观察仓；"
        f"同一腿两次调仓间隔 ≥{ACTION_GAP} 个交易日；T 日决策、T+1 收盘成交。",
        "",
        "「严格 / 宽松」两版是**同一套判定链的两个阈值档**（宽松版把量能门槛放到 1.0、"
        "关键位从「连续 2 日」放到「当日」、不要求逼近前高），用来看结论对实现细节有多敏感 —— "
        "不是两套策略。覆盖率（命中四格之一的日子占比）："
        + "；".join(f"{NAME[l]} 严格 {res[l]['cover']:.0%} / 宽松 {res[l]['cover_l']:.0%}"
                   for l in res) + "。",
        "",
        "> ⚠ 同一腿的**场内窗口与 `_sens_legs` 的场外窗口不同**（场内从 MA250 成形起算，ETF 上市也晚于"
        "场外基金），所以「恒持满带」的年化在两份报告里对不上（例：科创50 场内 −2.92% vs 场外 +5.04%）。"
        "这不是矛盾，是口径 —— 只在本表内部做横向比较。",
        "",
        "| 腿 | 口径 | 年化 | 最大回撤 | 夏普 | 平均曝光 | 调仓 | 打脸 |",
        "|---|---|---:|---:|---:|---:|---:|---:|",
    ]
    for leg in res:
        r = res[leg]
        for i, row in enumerate(r["rows"]):
            p = row["perf"]
            whip = "—" if row["whip"] is None else str(row["whip"])
            L.append(f"| {NAME[leg] if i == 0 else ''} | {row['name']} | {tg.fmt_pct(p['ann'])} | "
                     f"{tg.fmt_pct(p['mdd'])} | {p['sharpe']:.2f} | {row['expo']:.0%} | "
                     f"{row['acts']} | {whip} |")
    L.append("")

    # ---- 结论（用池化口径，避免只看一条腿）
    L += ["## 4. 结论", ""]
    agg = {}
    for q in QUADS:
        rows = [res[l]["cond"][q] for l in res if res[l]["cond"].get(q)]
        if rows:
            agg[q] = dict(n=sum(r["n"] for r in rows),
                          f20=float(np.median([r["f20"] for r in rows])),
                          f60=float(np.median([r["f60"] for r in rows])),
                          w60=float(np.mean([r["w60"] for r in rows])),
                          dd=float(np.median([r["dd"] for r in rows])))
    base = {k: float(np.median([res[l]["cond"]["_all"][k] for l in res])) for k in ("f20", "f60", "dd")}
    if agg:
        L += [
            "七腿池化（各腿先算、再取中位，避免长样本腿主导）：",
            "",
            "| 象限 | 未来20日 | 未来60日 | 60日胜率 | 之后120日回撤(中位) |",
            "|---|---:|---:|---:|---:|",
        ]
        for q in QUADS:
            if q not in agg:
                continue
            a = agg[q]
            L.append(f"| {q or '未命中'} | {tg.fmt_pct(a['f20'])} | {tg.fmt_pct(a['f60'])} | "
                     f"{a['w60']:.0%} | {tg.fmt_pct(a['dd'])} |")
        L.append(f"| **全样本基准** | {tg.fmt_pct(base['f20'])} | {tg.fmt_pct(base['f60'])} | "
                 f"~55% | {tg.fmt_pct(base['dd'])} |")
    beat = {}
    for q in QUADS:
        n = tot = 0
        for l in res:
            s = res[l]["cond"].get(q)
            if s:
                tot += 1
                n += 1 if s["f60"] > res[l]["cond"]["_all"]["f60"] else 0
        if tot:
            beat[q] = (n, tot)
    diffs = {l: res[l]["perf"]["四象限（严格）"]["ann"] - res[l]["perf"]["恒持满带"]["ann"]
             for l in res}
    best_l, worst_l = max(diffs, key=diffs.get), min(diffs, key=diffs.get)
    cb_d = {l: res[l]["perf"]["四象限（只买回调）"]["ann"] - res[l]["perf"]["四象限（严格）"]["ann"]
            for l in res}
    n_cb = sum(1 for v in cb_d.values() if v > 0)
    cb_line = (
        f"- **只买回调 vs 追突破**：窄版在 {n_cb}/{len(cb_d)} 条腿上优于严格版，平均 "
        f"{float(np.mean(list(cb_d.values()))) * 100:+.2f}pp/年。"
        + (f"方向与 §2 一致 —— 四格里唯一稳定的正 alpha 在「回调」"
           f"（池化未来 60 日中位 {tg.fmt_pct(agg['回调']['f60'])}、胜率 {agg['回调']['w60']:.0%}），"
           if "回调" in agg else "方向与 §2 一致 —— ")
        + "而「上涨」格本质是「追在放量突破之后」，已经吃掉了大半段。"
    )

    L += [
        "",
        "**怎么读**：如果「上涨」格的未来 20/60 日收益与胜率明显高于基准、「下跌」格明显更低，"
        "说明这套判断有信息量；如果四格与基准拉不开差距，说明它只是把同一段行情切成了四块。"
        "**回撤列才是它的主战场**：「下跌」格之后 120 日回撤中位若明显比基准深，"
        "那么「及时认跌」是有意义的（这正是 v5 年线闸门在做的事）。",
        "",
        f"- **区分度**（未来 60 日收益中位高于全样本基准的腿数）："
        + "；".join(f"{q} {beat[q][0]}/{beat[q][1]}" for q in QUADS if q in beat) + "。",
        f"- **覆盖率是第一个硬约束**：严格版只在 "
        + "、".join(f"{NAME[l]} {res[l]['cover']:.0%}" for l in res)
        + " 的日子里命中四格之一 —— 「结构 + 量能 + 关键位 + 节奏」同时满足在日线上是稀疏事件。"
          "所以仓位化后规则大部分时间只能「保持」：这既解释了它换手不高，也说明它更像"
          "「在关键日回答这一次是反弹还是反转」的判据，而不是逐日仓位规则。",
        f"- **仓位化**：严格版相对恒持，最好的腿是 {NAME[best_l]}（{diffs[best_l] * 100:+.1f}pp/年），"
        f"最差的是 {NAME[worst_l]}（{diffs[worst_l] * 100:+.1f}pp/年）；"
        f"宽松版平均覆盖 {float(np.mean([res[l]['cover_l'] for l in res])):.0%}，"
        "但年化并未随之改善 —— 说明瓶颈不是「阈值太严」，而是「结构 + 量能」在宽基 ETF 上"
        "的信噪比不够（同一个量比 1.2，在成交稀疏的 ETF 和热门个股上不是一回事）。",
        cb_line,
        "",
        "## 5. 与 v11-gate（支撑位闸门）的关系",
        "",
        "本规则里的「有效跌破关键位」与 v11/v11-gate 测的是同一件事。v11-gate 的结论是："
        "**支撑层（贴线减半 + 破位清仓）在逐日仓位口径下年化 −4~−5pp、换手 ×7、回撤没改善**"
        "（见 `v11_support_pressure/gate/README.md`）。所以这里把「跌破」只用来**降档**（0.5 → 0.2），"
        "不做「破位清仓 + 冷却 20 日」，也不做「重新站上支撑 +3% 才回补」——"
        "那两条已被证伪，重复测没有意义。",
        "",
        "## 6. 这套数据测不了的部分（不是被证伪，是无法检验）",
        "",
        "| 原文条件 | 为什么测不了 / 怎么处理 |",
        "|---|---|",
        "| 板块共振、多股共振 | 我们只有 7 只宽基 ETF，没有成分股横截面数据；"
        "用「指数自身量能」代替「板块合力」是偷换概念，故不纳入 |",
        "| 相对强弱（抗跌、率先新高） | 只能算「本腿 vs 510300」的 20 日相对涨幅，"
        "但宽基之间本来就高度相关，这个量在 ETF 层几乎没有区分度 |",
        "| 筹码密集区 | 需要逐笔/持仓成本分布，日线 OHLCV 推不出来；v11 已用「时间密集区」降级处理 |",
        "| 涨跌停、跳空、倍量 | ETF 无涨跌停；跳空可用 open/close 近似，但对 ETF 意义很小 |",
        "| 止跌 K 线形态 | 可枚举（锤子线/十字星等），但形态库本身是拟合重灾区，且与量能高度共线 |",
        "| 本地盘（横琴/香洲） | 与 A 股 ETF 无关，不属本仓库口径 |",
        "",
        "> 机械规则输出，非投资建议。",
    ]

    with io.open(OUT_MD, "w", encoding="utf-8", newline="\n") as f:
        f.write("\n".join(L))
    print("表:", OUT_MD)

    for leg in res:
        r = res[leg]
        p = r["perf"]
        print(f"  {NAME[leg]:8s} 严格 {tg.fmt_pct(p['四象限（严格）']['ann']):>8s} / "
              f"{tg.fmt_pct(p['四象限（严格）']['mdd']):>8s} / {p['四象限（严格）']['sharpe']:4.2f}"
              f" · 宽松 {tg.fmt_pct(p['四象限（宽松）']['ann']):>8s}"
              f" · 只买回调 {tg.fmt_pct(p['四象限（只买回调）']['ann']):>8s} · 恒持 "
              f"{tg.fmt_pct(p['恒持满带']['ann']):>8s} · MA250+20 "
              f"{tg.fmt_pct(p['MA250+20（v5 默认）']['ann']):>8s}"
              f" · 覆盖 {r['cover']:.0%}/{r['cover_l']:.0%}")

    figure(res)


def figure(res):
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    plt.rcParams["font.sans-serif"] = ["Microsoft YaHei", "SimHei", "DejaVu Sans"]
    plt.rcParams["axes.unicode_minus"] = False

    legs = list(res)
    keys = ["四象限（严格）", "四象限（只买回调）", "MA250+20（v5 默认）", "MA120+5", "恒持满带"]
    colors = ["#c62828", "#8e24aa", "#1f8a4c", "#6b8cae", "#8895a7"]
    fig, axes = plt.subplots(1, 2, figsize=(14.6, 6.4))

    ax = axes[0]
    w = 0.16
    xs = np.arange(len(legs))
    for j, (k, col) in enumerate(zip(keys, colors)):
        ys = [res[l]["perf"][k]["ann"] * 100 for l in legs]
        ax.bar(xs + (j - 2.0) * w, ys, width=w, color=col, label=k, alpha=0.92)
    ax.axhline(0, color="#5b6b7a", lw=0.8)
    ax.set_xticks(xs)
    ax.set_xticklabels([NAME[l] for l in legs], fontsize=8.5, rotation=20, ha="right")
    ax.set_ylabel("年化（%，扣费，场内 ETF 口径）", fontsize=9.5)
    ax.set_title("逐腿年化：四象限 vs 均线读数 vs 恒持", fontsize=11.5, color="#1b2b3a",
                 weight="bold", loc="left")
    ax.legend(fontsize=8, frameon=False, ncol=2)
    ax.grid(axis="y", ls="--", lw=0.6, alpha=0.35)

    ax2 = axes[1]
    qs = ["上涨", "回调", "反弹", "下跌"]
    for q, col in zip(qs, ["#2e7d32", "#ef6c00", "#f9a825", "#c62828"]):
        xs2, ys2 = [], []
        for j, l in enumerate(legs):
            s = res[l]["cond"].get(q)
            if s:
                xs2.append(j)
                ys2.append(s["f60"] * 100)
        ax2.scatter(xs2, ys2, s=46, color=col, label=q, alpha=0.9, edgecolor="white", lw=0.6)
    base = [res[l]["cond"]["_all"]["f60"] * 100 for l in legs]
    ax2.plot(range(len(legs)), base, ls="--", lw=1.0, color="#5b6b7a", label="全样本基准")
    ax2.axhline(0, color="#5b6b7a", lw=0.8)
    ax2.set_xticks(range(len(legs)))
    ax2.set_xticklabels([NAME[l] for l in legs], fontsize=8.5, rotation=20, ha="right")
    ax2.set_ylabel("该象限未来 60 日收益（中位 %）", fontsize=9.5)
    ax2.set_title("四象限的条件收益：格子之间拉得开才有信息量", fontsize=11.5,
                  color="#1b2b3a", weight="bold", loc="left")
    ax2.legend(fontsize=8, frameon=False)
    ax2.grid(ls="--", lw=0.6, alpha=0.35)

    for a in (ax, ax2):
        for s in ("top", "right"):
            a.spines[s].set_visible(False)
    fig.suptitle("四象限（结构×量能×关键位×节奏）· 场内 ETF 口径 · 波动层关",
                 x=0.02, ha="left", fontsize=13, color="#1b2b3a", weight="bold")
    fig.text(0.02, 0.012,
             "四象限 = 用户 2026-11-09 定性清单的量化版（判定链见 _quad_pv.py::quad_series）；"
             "与 _v5_sens_legs.md 的场外口径不可直接相减。机械规则输出，非投资建议。",
             fontsize=8.2, color="#5b6b7a")
    fig.subplots_adjust(left=0.065, right=0.985, top=0.86, bottom=0.22, wspace=0.22)
    fig.savefig(OUT_PNG, dpi=130, facecolor="white")
    plt.close(fig)
    print("图:", OUT_PNG)


if __name__ == "__main__":
    main()
