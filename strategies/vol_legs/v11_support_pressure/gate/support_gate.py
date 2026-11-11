# -*- coding: utf-8 -*-
"""波动资产 · v11-gate（= v11 的**仓位层**）：支撑位闸门（**参数唯一权威源 + 仓位状态机**）。

位置：`v11_support_pressure/gate/` —— 本文件是 v11 那个事件研究的**仓位化复算**，
与父目录的 `support_pressure.py` 是「同一份指标口径的两种测量」，不是两个独立变种。

文件名不叫 `rule.py`：家族根目录已经有一个 `rule`（总规则），本文件 import 它，
同名会把父级 `rule` 顶掉（v5 用 `trend_gate.py` 也是这个原因）；回测入口因此叫
`gate_bt.py` 而不是 `backtest.py`（父目录已有一个 `backtest.py`，同名会互相顶掉）。

来源链路：父目录 `support_pressure.py`（事件研究）的四条结论 → 本文件把它们变成**可执行的仓位规则**：

| v11 结论（报告 §10） | 在本变种里变成 |
|---|---|
| ① 趋势（MA200 月频上行）在七腿上有正超额 | 趋势成立 → 满带 `FULL`，不成立 → 观察仓 `OBSERVE` |
| ② **贴线买是负增量**（距支撑 ≤2% 时 f60 低于趋势日均值），3~8% 才更好 | 距支撑 `≤ NEAR` → **减半** `HALF`，不贴线才给满带 |
| ③ **0.5×ATR 止损是负优化**、1.5×ATR 才有正 EV；「跌破支撑就走」是对的 | 默认**结构性止损**（`dist<0` → 清仓 + `COOL` 日冷却）；ATR 跟踪止损作可选档 |
| ④ 与 v5 年线闸门 82% 共线；「不抄底、不逆势空」是唯一能落地的半边 | 只做多、不抄底；**风控减仓不受调仓间隔限制** |

与父目录的分工：v11 回答「这笔交易值不值」（单笔 R 口径，**不报年化/回撤**），
v11-gate 回答「这条腿今天该露出多少带」（组合 NAV 口径，报年化/回撤/夏普）。
两者**共用同一份趋势/支撑/ATR 实现**（直接 import 父目录 `support_pressure.build`），口径不会漂移：
改趋势或支撑的定义只改父目录，v11-gate 自动跟随。

成本与记账复用 `v5_trend_gate.trend_gate`（`fee_of / portfolio / perf / run_leg`），
所以「v11-gate vs V5」的差异**只来自规则**，不来自记账口径。
C 类腿（023299/011609/012805）的销售服务费仍未计入（与 v1~v9 同一处妥协）。

模拟结果，非投资建议。
"""
import os
import sys

import numpy as np
import pandas as pd

_HERE = os.path.dirname(os.path.abspath(__file__))   # strategies/vol_legs/v11_support_pressure/gate
_V11 = os.path.dirname(_HERE)                        # strategies/vol_legs/v11_support_pressure
_VL = os.path.dirname(_V11)                          # strategies/vol_legs
for _p in (_HERE, _V11, _VL, os.path.join(_VL, "v5_trend_gate")):
    if _p not in sys.path:
        sys.path.insert(0, _p)

import rule as base            # noqa: E402  波动资产总规则（LEGS / LEG_ORDER / SLEEVE_PP / band）
import support_pressure as v11  # noqa: E402  趋势 / 摆动点 / ATR 的唯一实现（口径同源）
import trend_gate as tg        # noqa: E402  成本表 + NAV 记账（v5 的对照口径）

LEGS = base.LEG_ORDER
SLEEVE_PP = base.SLEEVE_PP
W = {k: float(base.LEGS[k]["band"]) / float(base.SLEEVE_PP) for k in LEGS}   # 带权归一化(Σ=1)

# ---------------------------------------------------------------- 趋势层（跟随 v11）
MA_WIN = 200            # 文档 §4.1 原文（v5 是 250，两者不可混用；敏感性见报告 §6）
SLOPE_N = 20            # MA 近 20 日斜率 > 0 才算上行
TREND_FREQ = "monthly"  # 月末判定、次月整月沿用（文档 §4.1）

# ---------------------------------------------------------------- 支撑层
SWING_N = 10            # 摆动点确认窗口（滞后 10 日确认，无前视）
SUP_LOOKBACK = 250      # 支撑回看
SUP_MODE = "pivot"      # 前低（`profile` = 时间密集区下沿，降级口径）

# ---------------------------------------------------------------- 仓位档（带内占比）
FULL, HALF, OBSERVE, EMPTY = 1.0, 0.5, 0.2, 0.0
LEVEL_TXT = {1.0: "满带", 0.5: "半仓", 0.2: "观察仓", 0.0: "空仓"}

USE_SUPPORT = True      # False = 只用趋势层（消融用，隔离「支撑位」的贡献）
CLEAR_ON_BREAK = True   # False = 破位只当贴线（减半），不清仓（消融用）

NEAR = 0.03             # 距支撑 ≤3% 视为「贴线」→ 减半（v11 §8.1：≤2% 负增量，3% 起转正）
HYST = 0.02             # 滞回：减到半仓后要涨破 NEAR+HYST 才回满带（防在 3% 边界来回摩擦）
COOL = 20               # 破位清仓后的冷却交易日（期间不因反弹回补，防反复被割）
ACTION_GAP = 10         # 两次非风控换档的最小间隔（破位/止损不受限 —— 风控优先）

# ---------------------------------------------------------------- 可选 ATR 跟踪止损
STOP_USE = False        # 默认关：结构性止损（破位）已覆盖；开 = 从持仓期最高点回撤 N×ATR
STOP_ATR = 1.5          # v11 §9：0.5×ATR 是负优化，1.5×ATR 的 EV 最高
ATR_N = 14

WARMUP = max(MA_WIN + SLOPE_N, SUP_LOOKBACK)   # 趋势与支撑都成形的最早交易日


def params(**kw):
    """参数打包：消融/敏感性扫描时用 `**{...}` 覆盖，不必改模块常量。"""
    p = dict(MA_WIN=MA_WIN, SLOPE_N=SLOPE_N, TREND_FREQ=TREND_FREQ,
             SWING_N=SWING_N, SUP_LOOKBACK=SUP_LOOKBACK, SUP_MODE=SUP_MODE,
             USE_SUPPORT=USE_SUPPORT, CLEAR_ON_BREAK=CLEAR_ON_BREAK,
             NEAR=NEAR, HYST=HYST, COOL=COOL, ACTION_GAP=ACTION_GAP,
             FULL=FULL, HALF=HALF, OBSERVE=OBSERVE,
             STOP_USE=STOP_USE, STOP_ATR=STOP_ATR, ATR_N=ATR_N)
    p.update(kw)
    return p


def fee_of(leg):
    """该腿 (买入费率, 卖出费率)，含滑点 —— 与 v5/v1~v9 完全同一张表。"""
    return tg.fee_of(leg)


def load(leg, mode=None):
    """该腿价格序列（默认场外复权净值）；缺失直接抛错，不静默补位。"""
    return v11.load(leg, mode=mode)


# ---------------------------------------------------------------- 诊断 + 状态机
def indicators(px, **kw):
    """趋势 / 支撑 / 距离 / ATR 的逐日诊断表（直接复用 v11 的 build，口径同源）。

    额外补 `ma` / `slope` 两列：v11 的 `build` 只用它们算出 `trend` 布尔量、不留下原值，
    但每日报告与操作图要显示「现价距 MA200 多远、MA200 斜率朝哪边」，所以在这里补。
    窗口与 `min_periods` 与 `v11.trend_flags` 逐字一致，两处不会漂移。
    """
    p = params(**kw)
    F = v11.build(px, "gate", MA_WIN=p["MA_WIN"], SLOPE_N=p["SLOPE_N"],
                  TREND_FREQ=p["TREND_FREQ"], SWING_N=p["SWING_N"],
                  SUP_LOOKBACK=p["SUP_LOOKBACK"], SUP_MODE=p["SUP_MODE"],
                  BAND=p["NEAR"], ATR_N=p["ATR_N"])
    ma = px.astype("float64").rolling(int(p["MA_WIN"]), min_periods=int(p["MA_WIN"])).mean()
    F["ma"] = ma
    F["slope"] = ma - ma.shift(int(p["SLOPE_N"]))
    return F


def _target(trend, dist, cool_left, p, hold=EMPTY):
    """单日目标档位（不含限速）。**风控优先于趋势**：破位/冷却一律清仓。

    「贴线 → 减半」带滞回（`HYST`）：满带时 dist 跌破 `NEAR` 才减半；半仓/观察仓要等
    dist 涨破 `NEAR+HYST` 才回满带 —— 否则 dist 在 3% 边界上抖动会天天换档。

    `USE_SUPPORT=False` 退化成纯趋势闸门（消融档）：趋势成立 → 满带，否则观察仓。
    """
    if not p["USE_SUPPORT"]:
        return ((float(p["FULL"]), "趋势成立→满带") if trend
                else (float(p["OBSERVE"]), "趋势不成立→观察仓"))
    if cool_left > 0:
        return float(EMPTY), f"破位冷却（剩 {cool_left} 日）"
    if np.isfinite(dist) and dist < 0.0:
        if not p["CLEAR_ON_BREAK"]:
            return float(p["HALF"]), "破位（消融：不清仓）→半仓"
        return float(EMPTY), "跌破支撑→清仓"
    if not trend:
        return float(p["OBSERVE"]), "趋势不成立→观察仓"
    if not np.isfinite(dist):
        return float(p["FULL"]), "支撑未成形→满带"
    thr = float(p["NEAR"]) + (float(p["HYST"]) if hold <= float(p["HALF"]) + 1e-9 else 0.0)
    if dist <= thr:
        return float(p["HALF"]), f"贴线 {dist:.1%}→半仓"
    return float(p["FULL"]), f"距支撑 {dist:.1%}→满带"


def state_series(px, leg="gate", **kw):
    """-> DataFrame(index=px.index)，列:

    - `state`   : 目标档位（带内占比，次日成交）
    - `trend` / `dist` / `support` / `ma` / `slope` / `atr` / `hold_hi` : 诊断量
    - `cool`    : 剩余破位冷却交易日（0 = 不在冷却）
    - `why`     : 当日档位的成因（中文，便于每日复盘）

    预热期（`WARMUP` 之前）一律 `OBSERVE`，回测里会截断，不进样本。
    """
    p = params(**kw)
    F = indicators(px, **kw)
    c = F["close"].to_numpy(dtype=float)
    trend = F["trend"].to_numpy(dtype=bool)
    dist = F["dist"].to_numpy(dtype=float)
    atrv = F["atr"].to_numpy(dtype=float)
    sup = F["support"].to_numpy(dtype=float)
    ma = F["ma"].to_numpy(dtype=float)
    slope = F["slope"].to_numpy(dtype=float)
    n = len(c)

    state = np.full(n, float(p["OBSERVE"]))
    why = np.empty(n, dtype=object)
    why[:] = "预热期"
    hi_v = np.full(n, np.nan)
    cool_v = np.zeros(n)

    hold = float(p["OBSERVE"])
    last_act = -10 ** 9         # 上次非风控换档的索引（破位/止损不受此限）
    cool_until = -1
    need_clear = False          # 破位/止损后：必须**离开贴线带**才允许回补
    hi = np.nan

    for t in range(n):
        if t < int(WARMUP):
            state[t], hi_v[t] = float(p["OBSERVE"]), np.nan
            continue

        # 破位后回补的唯一条件：价格重新站到支撑上方 NEAR 以上（离开贴线带）。
        # 少这一条会在「跌破 → 冷却 → 贴着支撑回补 → 再跌破」里反复摩擦。
        if need_clear and (not np.isfinite(dist[t]) or dist[t] > float(p["NEAR"])):
            need_clear = False

        tgt, note = _target(bool(trend[t]), float(dist[t]), cool_until - t, p, hold)

        # 可选 ATR 跟踪止损：从**持仓期最高收盘**回撤 ≥ STOP_ATR×ATR 就清仓
        if p["STOP_USE"] and np.isfinite(atrv[t]) and c[t] > 0:
            if hold > 0.0:
                hi = c[t] if not np.isfinite(hi) else max(hi, c[t])
            a_pct = float(atrv[t]) / float(c[t])
            if np.isfinite(hi) and hold > 0.0 and c[t] <= hi * (1.0 - float(p["STOP_ATR"]) * a_pct):
                tgt, note = float(EMPTY), f"ATR 跟踪止损（距高点 {c[t] / hi - 1.0:.1%}）"
                hi = np.nan
        if hold <= 0.0:
            hi = np.nan

        if need_clear:
            tgt, note = float(EMPTY), "破位后未离开贴线带→空仓"

        # 限速：非风控换档要等 ACTION_GAP；破位清仓/止损**立即执行**（风控优先）
        risk = ("跌破支撑" in note) or ("止损" in note) or need_clear
        if abs(tgt - hold) > 1e-9 and not risk and (t - last_act) < int(p["ACTION_GAP"]):
            tgt, note = hold, note + "（换档间隔未到，维持）"
        if abs(tgt - hold) > 1e-9:
            last_act = t
            if tgt < hold - 1e-9 and ("跌破支撑" in note or "止损" in note):
                cool_until = t + int(p["COOL"])
                need_clear = True

        hold = tgt
        state[t], why[t], hi_v[t] = hold, note, hi
        cool_v[t] = max(0, cool_until - t)

    return pd.DataFrame(dict(state=state, trend=trend, dist=dist, support=sup,
                             ma=ma, slope=slope, atr=atrv, hold_hi=hi_v, cool=cool_v,
                             why=why), index=px.index)


def actions(state):
    """档位变化 -> 动作表（成交日 = 决策次日）。折法与 v5 的 `tg.actions` 一致。"""
    s = pd.Series(state)
    v = s.to_numpy(dtype=float)
    idx = s.index
    out, prev_i = [], 0
    for i in range(1, len(v)):
        if abs(v[i] - v[i - 1]) > 1e-9:
            out.append(dict(date=idx[i], frm=float(v[i - 1]), to=float(v[i]),
                            kind="buy" if v[i] > v[i - 1] else "sell", gap=i - prev_i))
            prev_i = i
    return out


def exposure(state):
    """带内平均曝光（0–1）。"""
    s = pd.Series(state).dropna()
    return float(s.mean()) if len(s) else 0.0


def leg_slice(leg, **kw):
    """-> (px, sm)，样本自「趋势 + 支撑」都成形之后起算（与 v5 的 leg_slice 同一口径）。"""
    px_full = load(leg)
    sm = state_series(px_full, leg, **kw)
    start = px_full.index[min(int(WARMUP), max(len(px_full) - 2, 0))]
    return px_full.loc[start:], sm.loc[start:]


def _self_check():
    """七腿逐日档位分布 + 换手 + 平均曝光（回测前先看规则是否「动得太勤」）。"""
    print(f"v11-gate 支撑位闸门 · MA{MA_WIN}/斜率{SLOPE_N} {TREND_FREQ} · "
          f"贴线带 {NEAR:.0%}→半仓 · 破位清仓+冷却 {COOL} 日 · "
          f"加仓间隔 {ACTION_GAP} 日 · ATR 止损 {'开' if STOP_USE else '关'}")
    for k in LEGS:
        px, sm = leg_slice(k)
        s = sm["state"]
        vc = s.value_counts(normalize=True)
        acts = actions(s)
        yrs = max((s.index[-1] - s.index[0]).days / 365.25, 1e-9)
        print(f"{k:6s} {base.LEGS[k]['name']:8s} n={len(s):5d} "
              f"满带 {vc.get(1.0, 0):.0%} / 半仓 {vc.get(0.5, 0):.0%} / "
              f"观察 {vc.get(0.2, 0):.0%} / 空仓 {vc.get(0.0, 0):.0%} · "
              f"曝光 {exposure(s):.0%} · 动作 {len(acts) / yrs:.1f} 次/年")


if __name__ == "__main__":
    try:
        sys.stdout.reconfigure(encoding="utf-8")  # type: ignore[attr-defined]
    except Exception:  # noqa: BLE001
        pass
    _self_check()
