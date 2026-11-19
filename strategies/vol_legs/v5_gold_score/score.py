# -*- coding: utf-8 -*-
"""V5.1 · 黄金仓位（风险预算 × 实际利率闸门 → 六档目标仓位）· 核心模块。

**定位**：只针对「投给黄金的这笔钱」给操作推荐（买入/卖出/持有 + 目标仓位档 0~100%），
不涉及总资金占比。标的与数据：信号价 = 场内 `518880` 黄金ETF（OHLCV，2013-07 起）；
实际利率 = FRED `DFII10`（2003 起）。

与同族 `v6_gold/` 的区别：v6 是「黄金这笔钱怎么拿」的三条**纯价格**口径
（保险丝 / 波动目标 / MACD）；本变种在波动目标之上多了**实际利率水平**这一维，
闸门不开时只拿一半风险预算。

**规则（v5.1）**：
1. `scale = clip(10% / 120 日实现波动, 0.2, 1)` —— 风险预算；
2. `gate  = 1.0 if DFII10(T−1) > 0.5% else 0.5` —— 实际利率水平闸门；
3. `e* = 量化(clip(scale × gate, 0, 1), 20%)` —— 六档 0/20/40/60/80/100%；
4. `state[t]` = t 日收盘成交后的仓位；T 日收盘算信号 → **T+1 收盘成交**；
5. 滞后：`|e* − e| ≥ 1 档（20pp）`才动；
6. 成本：万 2.5 单边佣金（`FEE`，默认 `SLIP=0`），空仓按货基 `rule.CASH_ANNUAL` 计息。

模拟结果，非投资建议。
"""
import os
import sys

import numpy as np
import pandas as pd

try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:  # noqa: BLE001
    pass

_HERE = os.path.dirname(os.path.abspath(__file__))        # v5_gold_score
_VL = os.path.dirname(_HERE)                              # strategies/vol_legs
for _p in (_HERE, _VL):
    if _p not in sys.path:
        sys.path.insert(0, _p)

import engine       # noqa: E402
import factors as F  # noqa: E402
import rule         # noqa: E402

LABEL = "V5.1 黄金仓位（风险预算 × 利率闸门）"
LEG = "gold"
NAME = "黄金"
BAND_PP = float(rule.LEGS[LEG]["band"])       # 10pp（本报告内部一律记成「占带比例」）
SIGNAL_CODE = rule.LEGS[LEG]["idx"]           # 518880
EXEC_CODE = rule.LEGS[LEG]["exec"]            # 518880

# ---- 成本与现金（规格 §5：万 2.5 单边，管理费已在 ETF 净值内不重复扣）
FEE = 0.00025          # 单边佣金万 2.5
SLIP = 0.0             # 规格未计滑点；敏感性用 0.0005（半个 tick 量级）
CASH_ANNUAL = rule.CASH_ANNUAL                # 1.40%（货基/同业存单近似）
CASH_DAILY = (1.0 + CASH_ANNUAL) ** (1.0 / 252) - 1.0

KEYS = ("buy", "sell", "hold")
CN = {"buy": "买入", "sell": "卖出", "hold": "持有"}


def params(**kw):
    """成本口径与因子的合并参数包（因子参数见 `factors.params`）。"""
    p = F.params(FEE=FEE, SLIP=SLIP, CASH_ANNUAL=CASH_ANNUAL)
    p.update(kw)
    return p


# ---------------------------------------------------------------- 数据
def load(refresh=False):
    """-> (px, rate, flow, shares)。

    `refresh=True` 时先补**实际利率**（DFII10）、**上交所份额**（只补新交易日，1 次请求/天）
    与主力净流入缓存（联网，失败降级、不中断）。`flow` 只有 120 天，只在 "netin" 口径用。
    """
    notes = []
    if refresh:
        try:
            if F.rate_is_stale():
                _s, note = F.refresh_rate()
                notes.append(note)
        except Exception as exc:  # noqa: BLE001
            notes.append(f"实际利率刷新失败（沿用缓存）：{type(exc).__name__}: {exc}")
        try:
            F.refresh_shares(progress=0)
        except Exception as exc:  # noqa: BLE001
            notes.append(f"上交所份额刷新失败（沿用缓存）：{type(exc).__name__}: {exc}")
        try:
            F.refresh_flow()
        except Exception as exc:  # noqa: BLE001
            notes.append(f"主力净流入刷新失败（不影响计分）：{type(exc).__name__}: {exc}")
    px = F.price_frame()
    rate = F.read_rate()
    flow = F.read_flow()
    shares = F.read_shares()
    for n in notes:
        print("[数据]", n)
    return px, rate, flow, shares


# ---------------------------------------------------------------- 状态机
def run_target(tgt, ready, blocked=None, p=None):
    """目标档序列 -> (实际仓位, 动作, 成交说明)。回测的状态机唯一实现。

    `state[t]`：t 日收盘成交后的仓位；`action[t]`：t 日盘后给出的**下一交易日**动作；
    `done[t]`：t 日实际成交的说明（来自 t−1 的决策）。T 决策 → T+1 收盘成交。
    """
    p = p or params()
    idx = tgt.index
    n = len(tgt)
    t = tgt.to_numpy(dtype=float)
    rdy = np.asarray(ready, dtype=bool)
    blk = np.zeros(n, dtype=bool) if blocked is None else np.asarray(blocked, dtype=bool)
    gap_min = float(p["HYST"]) * float(p["STEP"]) - 1e-9

    st = np.zeros(n)
    act = np.empty(n, dtype=object)
    act[:] = CN["hold"]
    pend = None
    hold = float(p["START"])
    for i in range(n):
        if pend is not None:
            hold = float(pend)
            pend = None
        st[i] = hold
        if not rdy[i]:
            continue
        want = t[i]
        if abs(want - hold) < gap_min:
            continue
        if want > hold and blk[i]:
            continue
        pend = want
        act[i] = CN["buy"] if want > hold else CN["sell"]
    return pd.Series(st, index=idx), pd.Series(act, index=idx)


def run(px, rate=None, flow=None, p=None, shares=None):
    """-> DataFrame(state / action / done / target / vol / gate / score ...)。

    `flow` = 东财主力净流入（仅 "netin" 口径用）；`shares` = 上交所每日份额（"share" 口径用）。
    """
    p = p or params()
    T = F.table(px, rate, flow, p, shares=shares)
    st, act = run_target(T["target"], T["ready"].to_numpy(dtype=bool), T["blocked"], p)
    done = np.empty(len(T), dtype=object)
    done[:] = ""
    for i in range(1, len(T)):                    # 成交说明：t−1 的决策写在 t 日
        if act.iloc[i - 1] != CN["hold"]:
            done[i] = _note(T, i - 1, act.iloc[i - 1], st.iloc[i])
    out = T.copy()
    out["state"] = st.to_numpy()
    out["action"] = act.to_numpy()
    out["done"] = done
    return out


def _note(T, i, act, new_state):
    r = T.iloc[i]
    return (f"波动 {r['vol']:.1%}→预算 {r['scale']:.2f}；利率 {r['rate_level']:+.2f}%→"
            f"闸门 {r['gate']:.2f} → 目标 {r['target']:.0%}，{act}至 {new_state:.0%}")


def run_leg(state, px, fee=None, slip=None, cash_annual=None):
    """单腿净值（起点 1）：持仓按 `state.shift(1)` 赚当日收益，成本 = |Δstate| × (佣金 + 滑点)。

    `px` 可以是收盘价 Series，也可以是 `factors.price_frame()` 的 OHLCV DataFrame（取 close）。
    """
    fee = FEE if fee is None else fee
    slip = SLIP if slip is None else slip
    cash_ann = CASH_ANNUAL if cash_annual is None else cash_annual
    cash = (1.0 + float(cash_ann)) ** (1.0 / 252) - 1.0
    close = px["close"] if isinstance(px, pd.DataFrame) else px
    ret = close.pct_change().fillna(0.0)
    first = float(state.iloc[0]) if len(state) else 0.0
    hold = state.shift(1).fillna(first).astype(float)
    r = hold * ret + (1.0 - hold) * cash
    d = state.diff().fillna(state).astype(float)
    r = r - d.clip(lower=0.0) * (float(fee) + float(slip)) \
          + d.clip(upper=0.0) * (float(fee) + float(slip))
    return (1.0 + r).cumprod()


def actions(state):
    """仓位变化 -> [{date, frm, to, kind}]。"""
    s = np.asarray(state, dtype=float)
    idx = state.index
    out = []
    for i in range(1, len(s)):
        if abs(s[i] - s[i - 1]) > 1e-9:
            out.append(dict(date=idx[i], frm=float(s[i - 1]), to=float(s[i]),
                            kind="buy" if s[i] > s[i - 1] else "sell"))
    return out


def cost_drag(state, p=None):
    """累计成本（pp 占净值）：Σ |Δstate| × (佣金 + 滑点)。"""
    p = p or params()
    s = np.asarray(state, dtype=float)
    return float(np.abs(np.diff(s, prepend=s[0])).sum() * (float(p["FEE"]) + float(p["SLIP"])))


def exposure(state):
    return float(np.mean(state)) if len(state) else 0.0


def perf(nav, rf=None):
    return engine.metrics(nav, rf=CASH_ANNUAL if rf is None else rf)


def calmar(m):
    return (m["ann"] / abs(m["mdd"])) if abs(m["mdd"]) > 1e-9 else float("nan")


def bands(state, p=None):
    """「独立完整段」计数（规格 §5 稳健性：段数 ≥50 才写成规则，否则降级为描述）。

    `trades` = 调仓笔数；`cycles` = 一买一卖的配对段数（先买后卖算一段）；
    `full_exits` = 清到 0 的次数（本策略默认收缩口径下多为 0，因为档位下限是 25%）。
    """
    acts = actions(state)
    cycles = 0
    open_pos = False
    full_exits = 0
    for a in acts:
        if a["kind"] == "buy" and a["to"] > a["frm"] + 1e-9:
            open_pos = True
        elif a["kind"] == "sell":
            if open_pos:
                cycles += 1
                open_pos = False
            if a["to"] <= 1e-9:
                full_exits += 1
    return dict(trades=len(acts), cycles=cycles, full_exits=full_exits)


def build(refresh=False, **kw):
    """-> dict(px, rate, flow, shares, df, start, params)。`start` = 因子全部就绪的首个交易日。"""
    p = params(**kw)
    px, rate, flow, shares = load(refresh=refresh)
    df = run(px, rate, flow, p, shares=shares)
    ready = df["ready"].to_numpy(dtype=bool)
    start = df.index[ready][0] if ready.any() else df.index[min(len(df) - 1, 25)]
    return dict(px=px, rate=rate, flow=flow, shares=shares, df=df, start=start, params=p)


# ---------------------------------------------------------------- 自检
def _self_check():
    d = build(refresh=False)
    df, px = d["df"], d["px"]
    st = df["state"]
    assert len(st) == len(px), "长度不一致"
    assert float(st.min()) >= -1e-9 and float(st.max()) <= 1.0 + 1e-9, "仓位越界"
    assert float(((st / F.STEP) - np.round(st / F.STEP)).abs().max()) < 1e-9, st.unique()
    assert set(df["action"].unique()) <= set(CN.values()), df["action"].unique()
    last = df.iloc[-1]
    b = bands(st)
    print(f"{SIGNAL_CODE} {px.index[0].date()} ~ {px.index[-1].date()} n={len(px)} · "
          f"起算 {d['start'].date()}")
    print(f"最新 {df.index[-1].date()}: 波动 {last['vol']:.1%}→预算 {last['scale']:.2f} · "
          f"利率 {last['rate_level']:+.2f}%→闸门 {last['gate']:.2f} · "
          f"目标 {last['target']:.0%} 当前 {last['state']:.0%} 动作 {last['action']} · "
          f"平均仓位 {exposure(st):.0%} · 调仓 {b['trades']} 次 / 完整往返 {b['cycles']} 段 / "
          f"清仓 {b['full_exits']} 次")
    print(f"参数：风险预算 × 利率闸门 · 波动目标 {F.VOL_TARGET:.0%}/{F.VOL_WINDOW}日 "
          f"下限 {F.VOL_MIN:.0%} · 利率闸门 {F.RATE_LEVEL_TH:+.1f}pp 否则×{F.RATE_LEVEL_LOW:g} · "
          f"步长 {F.STEP:.0%} · 滞后 {F.HYST} 档 · 事件冻结 ±{F.EVENT_FREEZE} 日")
    print(f"成本：单边 {FEE:.5%} + 滑点 {SLIP:.2%} · 现金 {CASH_ANNUAL:.2%} · 带 {BAND_PP:g}pp")


if __name__ == "__main__":
    _self_check()
