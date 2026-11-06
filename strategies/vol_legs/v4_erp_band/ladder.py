# -*- coding: utf-8 -*-
"""波动资产 · V4（股债利差通道）：ERP 通道 + 「满仓 / 维持 / 清仓」两带滞回。

> 编号沿革：本规则 2026-11-03 落地时写成 v10（v9 同日被 `../v7_bottom_confirm/` 占用，该变种已于
> 2026-11-04 删除）；同日用户决定**删除旧的 v4（进轨即买 80/50/20）与 v8（离轨确认）两个变种**，
> 把本规则提升为该家族的 **v4 主口径**。旧 v4 的 ERP 通道/引擎 `erp.py` 与债券长序列
> 缓存原样并入本包，只是不再对外提供旧 v4 的两带规则。

## 0. 这条规则从哪来
先落地成 4 带阶梯（100/50/20/0），2026-11-03 用户改口径为**两条带 + 中间维持区**：

| 带 | z 区间 | 仓位（股票占比） |
|---|---|---|
| B0 | z ≥ 0.80 | **满仓 100%** |
| B1 | 0.20 ≤ z < 0.80 | **维持上个仓位**（不主动调仓） |
| B2 | z < 0.20 | **清仓 0%** |

> 「0.8 以上（满仓）、0.2-0.8（维持上个仓位）、0.2 以下（清仓）；从某个带内离开时执行带内的操作。」

## 1. 执行口径：离开带才动手（读法 A = `hold_left`）

- 离开 B0（z 跌回 0.80 之下）→ 执行 B0 的操作 = **满仓 100%**；
- 离开 B2（z 升回 0.20 之上）→ 执行 B2 的操作 = **清仓 0%**；
- 离开 B1 → 执行 B1 的操作 = **维持**（不动，仓位停在上一个带给的那一档）。

于是有效规则是一条**滞回**：`z` 向下跌破 0.80 买满、向上升破 0.20 清空、中间不动；
状态只有 {100%, 0%}。同一份 z，从上面下来和从下面上来持仓不同（路径依赖）。

**带内趋势提前（2026-11-06 起默认，`CFG["trend_intra"]=True`）**：在 B0 带内读到 v5 的
「上涨趋势」象限（`close>MA250` 且 20 日斜率>0）就立刻买满、在 B2 带内读到「下跌趋势」就
立刻清仓，不必等离带。不改上面那条滞回的主体，只在带内多开一个触发口。14.2 年只多触发
3 次（+0.38pp 年化，噪声内）—— 因为 z≥0.8 时价格已跌够、MA250 斜率几乎必向下。

读法 B（`hold_new`：进入哪个带就用哪个带的仓位）只保留为回测对照 ——
它等价于「z 升破 0.80 买、跌破 0.20 卖」，与 A 方向相反。`CFG["ladder_mode"]` 是 A，别改。

## 2. 状态机（`ladder_series`）

```
每天收盘读出 z -> 带 b
若 b 与当前带不同（跨带）:
    tol 过滤：跌落要 z ≤ 边界−band_tol；升破要 z ≥ 边界+band_tol（默认 0 = 不过滤）
    新仓位 = W(旧带)   # hold_left；W(维持带) = 保持当前仓位
    新仓位 = W(b)      # hold_new；  W(维持带) = 保持当前仓位
    记录一次调仓事件（+1 加仓 / −1 减仓 / 0 维持）
否则：什么都不做（带内不调仓，漂移交给月末回锚）
```

- `weights` 里写 `None` 表示「维持」：该带没有固定目标仓位，跨带那天不动手。
- 一次跨两带（跳空）按**一次**调仓处理，取所跨**第一条**边界做 tol 过滤。
- 首个 ready 日：落在满仓/清仓带 → 直接用该带仓位；落在维持带 → 用 `CFG["init_w"]` 起手。
- 通道未就绪（`ready=False`）不计带、不调仓。

## 3. 仓位与成本
- `weights=(1.00, None, 0.00)`：股票腿 = 沪深300 全收益指数，债券腿 = 剩余。
- **满仓 = 债券砍到 0%**；清仓 = 100% 债券（旧 v4 的 80/50/20 从不打满，本口径会打满）。
- 只在**跨带**那天动手；月末偏离 > `rebal_tol`(5pp) 才回锚（否则股票会随涨跌漂出目标）。
- **T 日收盘算 → T+1 收盘净值成交**（`lag=1`），两条腿各扣申购 0.10% / 赎回 0.50%。

## 4. 数据与引擎
通道（ERP / MA / σ / 上轨 / 下轨 / z）、行情利率采集、两腿记账 `run()`、绩效 `perf()`、
前瞻收益 `forward_stats()`、区间 `episodes()`、绘图 —— 全部在本包的 `erp.py`（原 v4 引擎，
随变种合并一并保留）；`erp.py` 自带的 `state/event/target`（百分位 + 趋势过滤 80/50/20）
只作为**对照信号**保留，不再单独成策略。本文件只负责两带滞回状态机。

机械规则输出，非投资建议。
"""
import os
import sys
from datetime import datetime

import pandas as pd

try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:  # noqa: BLE001
    pass

_HERE = os.path.dirname(os.path.abspath(__file__))
_VL = os.path.dirname(_HERE)                                   # strategies/vol_legs
for _p in (_HERE, _VL):
    if _p not in sys.path:
        sys.path.insert(0, _p)

import erp  # noqa: E402

LABEL = "V4 股债利差通道 · 两带滞回（0.8 买满 / 0.2 清空 / 中间维持）"

BAND_NAME = ("满仓", "维持", "清仓")
BAND_RANGE = ("z ≥ 0.80", "0.20 ≤ z < 0.80", "z < 0.20")

MODES = ("hold_left", "hold_new")
MODE_TEXT = {
    "hold_left": "离开带 i → 执行**带 i**（刚离开那个带）的操作（已确认执行规则）",
    "hold_new": "离开带 i → 执行**新进入带**的仓位（等价于「按当前带定仓」；仅回测对照）",
}
MODE_SHORT = {"hold_left": "离带执行（滞回：跌破 0.8 买 / 升破 0.2 卖）",
              "hold_new": "按当前带定仓（升破 0.8 买 / 跌破 0.2 卖）"}


def w_text(x):
    """仓位显示：None = 维持带。"""
    return "维持" if x is None else f"{x:.0%}"


# ---------------------------------------------------------------- 1. 参数（唯一权威源）
CFG = {
    # —— 通道（沿用原 v4 引擎 erp.py 的默认）——
    "window": 250,
    "k": 2.0,
    # —— 两带本体 ——
    # B0 z≥0.80 满仓 / B1 0.20~0.80 维持上个仓位 / B2 z<0.20 清仓
    "edges": (0.80, 0.20),            # z 分带边界（降序）→ 3 段
    "weights": (1.00, None, 0.00),    # None = 维持（该带不主动调仓）
    "init_w": 0.50,                   # 首个就绪日落在「维持带」时的起手仓位
    # 读法：2026-11-03 用户确认「从某个带内离开时执行带内的操作」= A(hold_left)。
    # hold_new（进入哪个带就用哪个带）只保留为回测对照，不要改成它。
    "ladder_mode": "hold_left",
    "band_tol": 0.00,                 # 跨带确认余量（0 = 碰到边界就算跨过）
    # —— 锚健康度（**只用于每日报告观测，不参与信号与仓位计算**）——
    # H = 1 − |z − ERP 的长期分位|：z 只测「相对 250 日均值的偏离」，锚会跟着 ERP 漂移；
    # H 衡量 z 是否还代表「绝对估值位置」。H 低 = 背离大 = 贴线误判概率高。
    # 2026-11-06 用户决定先只观测、不改规则（改规则需另开变种并做样本外验证）。
    "health_window": 1250,            # ERP 长期分位的滚动窗口（交易日）≈ 5 年
    "health_minp": 750,               # 预热（交易日）≈ 3 年
    "health_dis": 0.40,               # 背离阈值：|z − 长期分位| > 它 → 判「脱钩」
    # —— 带内趋势提前（2026-11-06 用户决定默认启用）——
    # 在 B0/B2 带内，除了「离带」之外再加一个触发：读到 v5 的极端象限就提前动手。
    #   B0 内出现「上涨趋势」→ 立刻买满；B2 内出现「下跌趋势」→ 立刻清仓。
    # 实测（沪深300 场外净值口径，14.2 年）：只多触发 3 次（2018-05-08 / 2020-02-04 买、
    # 2024-05-24 卖），年化 +12.02%→+12.40%、回撤不变、夏普 0.63→0.65，差异在噪声内。
    # 之所以触发这么少：z≥0.8 时 ERP 高 = 价格已跌够，MA250 斜率几乎必向下（B0 内「上涨」
    # 仅 37/804 日）；z<0.2 时价格已涨够，趋势几乎不向下（B2 内「下跌」仅 1/755 日）。
    "trend_intra": True,              # False = 退回纯「离带才动手」
    "trend_ma": 250,                  # 与 v5 的 MA250+20 同口径（同一函数、同一净值源）
    "trend_slope": 20,
    "trend_leg": "hs300",
    # —— 再平衡 / 成本 / 债券腿（沿用原 v4 引擎 erp.py 的默认）——
    "rebal_freq": "M",
    "rebal_tol": 0.05,
    "lag": 1,
    "fee_eq": (0.0010, 0.0050),
    "fee_bd": (0.0010, 0.0050),
    "slip": 0.0,
    "bond_duration": 8.0,
    "ffill_limit": erp.FFILL_LIMIT,
}


def cfg(key, default=None):
    return CFG.get(key, default)


def band_of(z, edges=None):
    """z -> 带号（0 = 最便宜/满仓 … len(edges) = 最贵/清仓）。nan -> -1。"""
    e = CFG["edges"] if edges is None else edges
    if z != z:
        return -1
    for i, x in enumerate(e):
        if z >= x:
            return i
    return len(e)


def exec_w(x, held):
    """把 weights 里的一档变成可执行仓位：None（维持带）→ 保持当前仓位。"""
    return float(held) if x is None else float(x)


# ---------------------------------------------------------------- 1b. 趋势读数（v5 口径）
_TREND_CACHE = {}


def trend_quad(df, ma_win=None, slope_n=None, leg=None):
    """读 v5 的「MA250+20」四象限，对齐到本策略的交易日 -> (up, dn) 两个布尔序列。

    直接复用 `v5_trend_gate` 的 `load` / `indicators`，**不自己用 `close` 重算** —— 实测
    用本策略的指数收盘近似，会把 7.5% 的交易日判成不同象限（分红/净值口径差异），那就
    不是「v5 的趋势」了。

    up : 「上涨趋势」象限（close > MA250 且 20 日斜率 > 0）
    dn : 「下跌趋势」象限（close < MA250 且 20 日斜率 < 0）
    中间两态（反弹 / 高位回调）不参与触发。
    """
    v5_dir = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                          "v5_trend_gate")
    if v5_dir not in sys.path:
        sys.path.insert(0, v5_dir)
    import trend_gate as tg

    w = int(CFG["trend_ma"] if ma_win is None else ma_win)
    n = int(CFG["trend_slope"] if slope_n is None else slope_n)
    lg = str(CFG["trend_leg"] if leg is None else leg)
    key = (w, n, lg)
    if key not in _TREND_CACHE:
        I = tg.indicators(tg.load(lg), KIND="ma", MA_WIN=w, SLOPE_N=n)
        I["quad"] = I["quad"].astype(str)
        _TREND_CACHE[key] = (I, tg.QUADS[0], tg.QUADS[3])
    I, q_up, q_dn = _TREND_CACHE[key]
    I = I.reindex(df.index)
    quad = I["quad"].ffill().astype(str)          # 逐列 ffill，避免整表 downcast 警告
    rdy = I["ready"].astype("float64").ffill().fillna(0.0).to_numpy(dtype=bool)
    up = quad.eq(q_up).to_numpy(dtype=bool) & rdy
    dn = quad.eq(q_dn).to_numpy(dtype=bool) & rdy
    return up, dn


# ---------------------------------------------------------------- 2. 状态机
def ladder_series(df, mode=None, edges=None, weights=None, band_tol=None, init_w=None):
    """两带滞回：进带只计划，离带才执行 -> DataFrame(plan_w, target, band, event, ...)。

    plan_w : 当前所在带的目标仓位（维持带 = 当前实际仓位，所以「计划 = 实际」）
    target : 实际执行的股票目标仓位（离带时才变）
    band   : 当前带号（-1 = 通道未就绪）
    event  : +1 加仓 / -1 减仓 / 0 不动（只在跨带且真的换仓位那天非零）
    cross  : 当天是否跨带
    """
    m = str(CFG["ladder_mode"] if mode is None else mode)
    if m not in MODES:
        raise ValueError(f"ladder_mode 只支持 {MODES}，收到 {m!r}")
    e = tuple(CFG["edges"] if edges is None else edges)
    w = tuple(CFG["weights"] if weights is None else weights)
    if len(w) != len(e) + 1:
        raise ValueError(f"weights 需要 {len(e) + 1} 档，收到 {len(w)} 档")
    if all(x is None for x in w):
        raise ValueError("weights 不能全是「维持」，至少要有一个固定仓位")
    tol = float(CFG["band_tol"] if band_tol is None else band_tol)
    w0 = float(CFG["init_w"] if init_w is None else init_w)

    z = df["z"].to_numpy(dtype="float64")
    ready = df["ready"].to_numpy(dtype=bool)
    up = dn = None
    if CFG["trend_intra"]:
        up, dn = trend_quad(df)
    cur, held = -1, 0.0
    rows = []
    for i in range(len(df)):
        if not ready[i]:
            plan = exec_w(w[cur], held) if cur >= 0 else 0.0
            rows.append((plan, held, cur, 0, False, False))
            continue
        b = band_of(z[i], e)
        if cur < 0:                     # 首个就绪日：满仓/清仓带按该带起手，维持带按 init_w
            cur = b
            held = w0 if w[b] is None else float(w[b])
            rows.append((exec_w(w[b], held), held, b, 0, False, False))
            continue
        if b == cur:
            # 带内趋势提前：B0 读到「上涨」就买满、末带读到「下跌」就清仓（不必等离带）
            plan, ev, intra = exec_w(w[cur], held), 0, False
            if up is not None and b in (0, len(e)):
                tgt_b = exec_w(w[b], held)      # 维持带的 w 为 None → 等于 held，不会触发
                if tgt_b > held + 1e-12 and up[i]:
                    plan, ev, intra = tgt_b, 1, True
                elif tgt_b < held - 1e-12 and dn[i]:
                    plan, ev, intra = tgt_b, -1, True
            if intra:
                held = plan
            rows.append((plan, held, cur, ev, False, intra))
            continue
        # 跨带：先过 tol 过滤（跌落看下边界、升破看上边界）
        if b > cur:
            ok = z[i] <= e[b - 1] - tol
        else:
            ok = z[i] >= e[b] + tol
        if not ok:
            rows.append((exec_w(w[cur], held), held, cur, 0, False, False))
            continue
        new = exec_w(w[cur] if m == "hold_left" else w[b], held)
        ev = 1 if new > held + 1e-12 else (-1 if new < held - 1e-12 else 0)
        cur, held = b, new
        rows.append((exec_w(w[b], held), held, b, ev, True, False))

    return pd.DataFrame(rows, index=df.index,
                        columns=["plan_w", "target", "band", "event", "cross", "intra"])


def target_weight(series):
    return series


# ---------------------------------------------------------------- 3. 组帧
def frame(window=None, k=None, refresh=False, mode=None, edges=None, weights=None,
          band_tol=None):
    """完整框架：erp.py 的通道列 + 两带滞回状态机 + 引擎自带带位信号（对照用）。"""
    base = erp.erp_frame(window=window, k=k, refresh=refresh)
    df = base.copy()
    df["eng_state"] = base["state"]          # erp.py 自带：百分位 + 趋势过滤（旧 v4 口径）
    df["eng_event"] = base["event"]
    df["eng_target"] = base["target"]
    sig = ladder_series(df, mode=mode, edges=edges, weights=weights, band_tol=band_tol)
    for c in sig.columns:
        df[c] = sig[c].to_numpy()
    df["ma200"] = df["close"].rolling(200, min_periods=200).mean()
    df["vs_ma200"] = df["close"] / df["ma200"] - 1.0
    return df


# ---------------------------------------------------------------- 3b. 锚健康度（只观测）
def anchor_health(df, window=None, minp=None, thresh=None):
    """锚健康度 H = 1 − |z − ERP 长期分位| -> DataFrame(erp, z, erp_pct, z_gap, health, dislocated)。

    为什么需要它：`z` 是「ERP 相对过去 250 日均值的偏离」，250 日均线会追着 ERP 一起漂
    （实测年变化率中位 +1.6%，P10/P90 = -33%/+46%），于是同一 ERP 水平在不同年份被赋予
    相反含义（z≥0.8 的天里 ERP 是 3.95~7.30%，z<0.2 的天里是 2.07~5.57%，重叠 3.95~5.57）。
    用 ERP 的**长期分位**当「绝对估值位置」的代理：它与 z 同向（0=最贵、1=最便宜），
    两者一致说明 z 还代表贵贱；背离说明 z 已经退化成短期动量。

    ⚠ **只用于每日报告的观测与事后复核，不参与信号、不改变仓位。**
    """
    w = int(CFG["health_window"] if window is None else window)
    mp = int(CFG["health_minp"] if minp is None else minp)
    th = float(CFG["health_dis"] if thresh is None else thresh)
    pct = df["erp"].rolling(w, min_periods=mp).apply(
        lambda x: float((x <= x[-1]).mean()), raw=True)
    gap = (df["z"] - pct).abs()
    return pd.DataFrame({"erp": df["erp"], "z": df["z"], "erp_pct": pct,
                         "z_gap": gap, "health": 1.0 - gap,
                         "dislocated": gap > th}, index=df.index)


def health_episodes(h, n=3):
    """最近的脱钩段（`dislocated` 连续为 True）-> list[dict]，最近的在前。

    段尾取**最后一个仍脱钩的交易日**（不是第一个恢复的日子），否则段长会多算一天、
    且「段在最后一天结束」会被误标成「进行中」。
    """
    segs, cur, prev = [], None, None
    for ts, v in h["dislocated"].items():
        if v:
            if cur is None:
                cur = ts
        elif cur is not None:
            segs.append((cur, prev, False))
            cur = None
        prev = ts
    if cur is not None:
        segs.append((cur, prev, True))
    out = []
    for a, b, open_ in segs[-n:][::-1]:
        s = h.loc[a:b]
        out.append(dict(start=a, end=b, days=int(len(s)),
                        z_mean=float(s["z"].mean()), erp_mean=float(s["erp"].mean()),
                        gap_max=float(s["z_gap"].max()), open_=bool(open_)))
    return out


# ---------------------------------------------------------------- 4. 读数（每日报告用）
def _days_since(flags):
    """最近一次 True 之后的天数（含当天）。"""
    n = 0
    for v in reversed(flags.tolist()):
        n += 1
        if v:
            break
    return n


def latest_reading(refresh=False, mode=None, edges=None, weights=None, band_tol=None):
    e = tuple(CFG["edges"] if edges is None else edges)
    w = tuple(CFG["weights"] if weights is None else weights)
    df = frame(refresh=refresh, mode=mode, edges=e, weights=w, band_tol=band_tol)
    last = df.iloc[-1]
    z = float(last["z"])
    b = int(last["band"])
    plan, tgt = float(last["plan_w"]), float(last["target"])

    # 距上下边界：b 带的上界（更便宜方向）/ 下界（更贵方向）
    up = e[b - 1] if b > 0 else None                  # 涨到它 → 跨进更便宜的带
    dn = e[b] if b < len(e) else None                 # 跌到它 → 跨进更贵的带
    gap_up = (up - z) if up is not None else float("nan")
    gap_dn = (z - dn) if dn is not None else float("nan")

    if w[b] is None:
        side = f"维持带：不主动调仓，仓位停在 {tgt:.0%}，等**跨出本带**才动"
    elif tgt > plan + 1e-9:
        side = (f"实际比本带计划 **高 {(tgt - plan) * 100:.0f}pp** —— "
                "等**离开本带**才降回本带的计划仓位")
    elif tgt < plan - 1e-9:
        side = (f"实际比本带计划 **低 {(plan - tgt) * 100:.0f}pp** —— "
                "等**离开本带**才升到本带的计划仓位")
    else:
        side = "实际 = 本带计划"

    # —— 带内趋势（v5 MA250+20 四象限）：今日象限 + 本带内是否刚被趋势提前触发 ——
    t_up, t_dn = trend_quad(df) if CFG["trend_intra"] else (None, None)
    if t_up is None:
        trend_q, trend_txt = "—", "未启用（trend_intra=False）"
    else:
        if t_up[-1]:
            trend_q = "上涨趋势"
        elif t_dn[-1]:
            trend_q = "下跌趋势"
        else:
            trend_q = "反弹/高位回调"
        intra_today = bool(last["intra"]) if "intra" in df.columns else False
        if intra_today:
            trend_txt = (f"**{trend_q}** → 本带内提前触发，已{'买入' if tgt > plan - 1e-9 else '卖出'}")
        elif b == 0:
            trend_txt = (f"{trend_q}；等到「上涨趋势」可不等离带直接买满"
                         if not pd.isna(z) else "")
        elif b == len(e):
            trend_txt = (f"{trend_q}；等到「下跌趋势」可不等离带直接清仓"
                         if not pd.isna(z) else "")
        else:
            trend_txt = f"{trend_q}（维持带，不看趋势）"
    intra_events = int(df["intra"].sum()) if "intra" in df.columns else 0

    # —— 锚健康度（只观测，不影响上面的仓位与信号）——
    h = anchor_health(df)
    hl = h.iloc[-1]
    h_ok = bool(pd.notna(hl["health"]))
    health = float(hl["health"]) if h_ok else None
    erp_pct = float(hl["erp_pct"]) if h_ok else None
    z_gap = float(hl["z_gap"]) if h_ok else None
    dislocated = bool(hl["dislocated"]) if h_ok else False
    if not h_ok:
        health_txt = f"预热中（需 {CFG['health_minp']} 个交易日）"
    elif dislocated:
        health_txt = (f"脱钩：|z − 长期分位| = {z_gap:.2f} > {CFG['health_dis']:g}，"
                      "z 更接近短期动量")
    else:
        health_txt = "正常"

    return dict(date=df.index[-1], df=df, mode=str(CFG["ladder_mode"] if mode is None else mode),
                erp=float(last["erp"]), pe=float(last["pe"]), y10=float(last["y10"]),
                ma=float(last["ma"]), std=float(last["std"]), upper=float(last["upper"]),
                lower=float(last["lower"]), z=z, band=b, plan_w=plan, target=tgt,
                band_name=BAND_NAME[b] if 0 <= b < len(BAND_NAME) else "—",
                up=up, dn=dn, gap_up=gap_up, gap_dn=gap_dn, side=side,
                days_in_band=_days_since(df["cross"]),
                events=int((df["event"] != 0).sum()),
                health=health, health_ok=h_ok, health_txt=health_txt,
                erp_pct=erp_pct, z_gap=z_gap, dislocated=dislocated,
                health_eps=health_episodes(h),
                health_window=int(CFG["health_window"]),
                health_dis=float(CFG["health_dis"]),
                trend_q=trend_q, trend_txt=trend_txt, trend_intra=bool(CFG["trend_intra"]),
                trend_ma=int(CFG["trend_ma"]), trend_slope=int(CFG["trend_slope"]),
                intra_events=intra_events,
                source=dict(erp.SOURCE))


# ---------------------------------------------------------------- 5. 自检
def _self_check():
    print(f"{LABEL} · 自检（{datetime.now():%Y-%m-%d %H:%M}）")
    e, w = CFG["edges"], CFG["weights"]
    print("带:", " · ".join(f"B{i} {BAND_NAME[i]}带" if x is None else f"B{i} {BAND_NAME[i]}={w_text(x)}"
                            for i, x in enumerate(w)) + f"（边界 {e}）")
    df = frame()
    d = df[df["ready"]]
    print(f"样本 {d.index[0]:%Y-%m-%d} ~ {d.index[-1]:%Y-%m-%d} · {len(d)} 个交易日")
    for m in MODES:
        s = ladder_series(df, mode=m)
        n_ev = int((s.loc[d.index, "event"] != 0).sum())
        gg = s.loc[d.index].groupby("band")["target"].mean()
        share = s.loc[d.index].groupby("band").size() / len(d)
        txt = " · ".join(f"B{b}: {w_text(w[b])} → 实际 {gg.get(b, float('nan')):.0%} "
                         f"({share.get(b, 0):.0%} 天数)" for b in range(len(w)))
        print(f"  {m:10s} 调仓 {n_ev:2d} 次 · {txt}")
    r = latest_reading()
    print(f"最新 {r['date']:%Y-%m-%d}: z={r['z']:.2f} · 带 B{r['band']} {r['band_name']} "
          f"(计划 {r['plan_w']:.0%}) · 实际目标 {r['target']:.0%} · {r['side']}")
    def _cross_txt(bound, gap, nb, word):
        nw = exec_w(w[r["band"]] if r["mode"] == "hold_left" else w[nb], r["target"])
        return (f"z {word} {bound:.2f}（再走 {gap:+.2f}）→ 跨进 B{nb} {BAND_NAME[nb]}，"
                f"执行后目标仓位 {nw:.0%}")

    up_txt = ("无上界（已是最便宜带）" if r["up"] is None
              else _cross_txt(r["up"], r["gap_up"], r["band"] - 1, "升到"))
    dn_txt = ("无下界（已是最贵带）" if r["dn"] is None
              else _cross_txt(r["dn"], r["gap_dn"], r["band"] + 1, "跌到"))
    print(f"  上跨：{up_txt}")
    print(f"  下跨：{dn_txt}")


if __name__ == "__main__":
    _self_check()
