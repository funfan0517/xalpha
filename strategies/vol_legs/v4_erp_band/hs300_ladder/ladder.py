# -*- coding: utf-8 -*-
"""V4 变种 · HS300 阶梯加减仓（**专属沪深300 · 单腿 0~100%**）。

不是 `../erp.py` 的「股债 80/50/20」，而是**只做沪深300 的仓位**：没投的部分当货币基金
（`rule.CASH_ANNUAL = 1.40%`）。这样回答的问题变了 —— 不是「股 vs 债摆多少」，
而是「**我现在该拿多少股票**」。债基的份额与收益一概不进模型。

## 规则卡（用户 2026-10-30 口述 → 代码）

一次完整的买卖周期：

| 阶段 | 触发 | 动作 | 代码 |
|---|---|---|---|
| 建仓① | ERP **上穿 75% 线** | 投 **20%** | `line_entry1=0.75 / w_entry1=0.20` |
| 建仓② | ERP **上穿上线**（MA+2σ） | 提到 **30%** | `line_entry2=1.0 / w_entry2=0.30` |
| 建仓③ | 持续处在上线之上，**每多 1 个交易日 +10pp** | 直到 **100%**，然后持仓等待 | `step=0.10 / w_max=1.0` |
| 减仓① | **第一次下穿中线**（MA） | 仓位 **减半** | `line_mid=0.50 / halve=0.50` |
| 减仓② | **第一次跌破 25% 线** | 再 **减半** | `line_low=0.25` |
| 清仓 | **第一次跌破下线**（MA−2σ） | **清仓**，本轮结束，回到建仓① | `line_exit=0.0` |

### 「75% / 25% 线」怎么定义

取**通道内位置**（就是图里那条线在上下轨之间的相对位置）：

```
pos = (ERP − 下线) / (上线 − 下线)        # 0 = 下线，0.5 = 中线，1 = 上线
```

于是 `pos = 0.75` 那条线在 **MA + 1σ**（k=2 时），`pos = 0.25` 在 **MA − 1σ**；
`pos ≥ 1` 恰好等价「ERP ≥ 上线」，`pos < 0` 恰好等价「ERP < 下线」。
换句话说，这套刻度与图中的四条线（MA±1σ、MA±2σ）**完全对齐**，不需要额外的分位统计。

> 为什么不用「ERP 历史分位」当 75%/25% 线：分位恒 ≥ 0，「跌破下线清仓」永远不触发，
> 三档减仓的最后一档会失效，规则不再自洽。通道内位置才能让「上线/中线/下线」与
> 100%/50%/0% 一一对应。

### 三个「规则没写、但不定就跑不下去」的口径

1. **每天只按「收盘」判一次**，`pos` 与 `erp` 都用当日收盘值；T 日收盘算 → **T+1 收盘成交**
   （与 `../erp.py::run` 同一套引擎，无前视）。
2. **加仓只加不减**：同一天可能既满足 75% 又满足上线，取两者中更高的目标；
   `above_cnt` 用**累计**口径（自本轮首次上穿上线起，在上线之上的交易日总数），
   所以在上线附近抖动不会卡住阶梯，也不会倒退。
3. **减仓各触发一次，且减仓开始后不再回补**（`refill_after_reduce=False`）。
   否则 ERP 在 25%/50% 之间来回，就会不停减半又加回，摩擦成本吃掉一切。
   清仓后标志位全部重置，等下一次上穿 75% 重新开始一轮。
   > 这一条**被回测证实是对的**：改成可回补后年化 5.90% → 5.28%（夏普 0.51 → 0.47）。

### 通道窗口为什么是 500 日（不是 v4 的 250 日）

规则骨架照抄，但 250 日在**这条**规则上是灾难：2012-08 起 14.2 年年化 +1.20%、夏普 −0.02
（跑输常仓 50% 3.44pp/年）。原因是 250 日 MA 在趋势行情里滞后，ERP 会长期贴在轨道外侧 ——
刚建仓就减仓、减完仓又长期回不来。窗口 500 日时年化 5.90%、夏普 0.51。
120/250/500/750 日的完整对比见 `backtest/_ladder_bt.md` §5.1，**那张表不能当调参依据**
（起点不同、只有 5 个完整周期），但「250 日太短」这个方向是稳的。

## 数据
沪深300 全收益 `H00300`（含分红再投，`data/_bt_caches/csi_H00300.csv`）当股票腿，
信号用 `000300` 的 PE —— 全部来自 `../erp.py::erp_frame()`，本文件只做**仓位状态机**。

**不涉及债券**：未投部分 = 货币基金（`rule.CASH_ANNUAL = 1.40%`），债基的份额/收益不进模型。

## 回测结论（13.1 年，含费，详见 `backtest/_ladder_bt.md`）
年化 +5.90%、最大回撤 −26.58%、夏普 **0.51**、平均仓位 30%：
用四分之一的仓位拿到买入持有沪深300 的 86% 收益、六成回撤。
但逐年极不稳定，优势集中在前半段（后半段超额 −0.83pp）。

模拟结果，非投资建议。
"""
import os
import sys

import pandas as pd

try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:  # noqa: BLE001
    pass

_HERE = os.path.dirname(os.path.abspath(__file__))          # .../v4_erp_band/hs300_ladder
_V4 = os.path.dirname(_HERE)                                # .../v4_erp_band
_VL = os.path.dirname(_V4)                                  # strategies/vol_legs
for _p in (_V4, _VL):
    if _p not in sys.path:
        sys.path.insert(0, _p)

import erp  # noqa: E402
import rule  # noqa: E402

LABEL = "V4-L 阶梯加减仓"
IDX_NAME = "沪深300"

# ---------------------------------------------------------------- 参数（唯一权威源）
L_CFG = {
    # 通道：MA / ±2σ 的滚动窗口。**本变种用 500 日（不是 v4 的 250 日）**，理由见 README §2.1
    "channel_window": 500,
    "channel_k": 2.0,
    # 建仓
    "line_entry1": 0.75,    # 上穿 75% 线 -> 投 20%
    "w_entry1": 0.20,
    "line_entry2": 1.00,    # 上穿上线 -> 30%
    "w_entry2": 0.30,
    "step": 0.10,           # 在上线之上每多 1 个交易日 +10pp
    "w_max": 1.00,
    # 减仓
    "line_mid": 0.50,       # 下穿中线 -> 减半
    "line_low": 0.25,       # 首次跌破 25% -> 再减半
    "line_exit": 0.00,      # 跌破下线 -> 清仓
    "halve": 0.50,
    "refill_after_reduce": False,
    "base_weight": 0.00,    # 底仓：任何减仓都不会低于它（0 = 原规则，跌破下线清仓到 0）
    # 成交与成本
    "lag": 1,               # T 日收盘算 -> T+lag 收盘成交
    "fee_buy": rule.FEE_BUY,
    "fee_sell": rule.FEE_SELL,
    "cash_annual": rule.CASH_ANNUAL,
}


def cfg(key, default=None):
    return L_CFG.get(key, default)


# ---------------------------------------------------------------- 0. 数据框架
def frame(window=None, k=None, refresh=False, start=None):
    """沪深300 的 ERP 框架（`../erp.py::erp_frame`），通道窗口按本变种的参数重算。"""
    return erp.erp_frame(window=int(L_CFG["channel_window"] if window is None else window),
                         k=float(L_CFG["channel_k"] if k is None else k),
                         refresh=refresh, start=start or "2011-06-01")


# ---------------------------------------------------------------- 1. 位置刻度
def band_position(df):
    """通道内位置 pos = (ERP − 下线)/(上线 − 下线)。0=下线，0.5=中线，1=上线。

    返回**未裁剪**的原始值：<0 表示已跌破下线，>1 表示已上穿上线 —— 状态机要靠这个判档。
    """
    den = df["upper"] - df["lower"]
    return (df["erp"] - df["lower"]) / den.replace(0.0, float("nan"))


def position(df):
    """仓位刻度 = 通道内位置。**不用历史分位**：分位口径里「跌破下线」永不发生
    （分位恒 ≥ 0），三档减仓的最后一档会失效，规则不再自洽。详见 README §1.1。"""
    return band_position(df)


# ---------------------------------------------------------------- 2. 仓位状态机
def ladder_target(df, pos=None, detail=False, **overrides):
    """按规则逐日推目标仓位 -> Series（0~1，收盘后的目标，次日成交）。

    `detail=True` 时返回 (target, events)，events 记录每一次动作，用于报告与图上的标记。
    """
    cf = dict(L_CFG)
    cf.update(overrides)
    p = position(df) if pos is None else pos
    p = p.to_numpy(dtype="float64")
    ready = df["ready"].to_numpy(dtype=bool) if "ready" in df else pd.Series(
        True, index=df.index).to_numpy()
    erp_v = df["erp"].to_numpy(dtype="float64")
    idx = df.index

    e1, w1 = float(cf["line_entry1"]), float(cf["w_entry1"])
    e2, w2 = float(cf["line_entry2"]), float(cf["w_entry2"])
    step, wmax = float(cf["step"]), float(cf["w_max"])
    mid, low, ext, half = (float(cf["line_mid"]), float(cf["line_low"]),
                           float(cf["line_exit"]), float(cf["halve"]))
    refill = bool(cf["refill_after_reduce"])
    base = float(cf.get("base_weight") or 0.0)

    held, above, f_mid, f_low = base, 0, False, False
    out, events = [], []

    def _log(d, act, before, after):
        events.append(dict(date=d, action=act, before=round(before, 4),
                           after=round(after, 4), erp=round(float(erp_v[i]), 4),
                           pos=round(float(p[i]), 4)))

    for i in range(len(df)):
        if not ready[i] or p[i] != p[i]:            # 未就绪 / NaN -> 不决策
            out.append(held)
            continue
        pi = float(p[i])
        d = idx[i]

        # --- 建仓（减仓开始后不再回补，除非显式打开 refill）
        if not f_mid or refill:
            before = held
            if pi >= e1:
                held = max(held, w1)
            if pi >= e2:
                above += 1
                held = max(held, min(wmax, w2 + step * (above - 1)))
            if held > before + 1e-12:
                act = "建仓满档" if held >= wmax - 1e-12 else "加仓"
                _log(d, act, before, held)

        # --- 减仓（各一次；同日跨两档就减两次）。底仓 base 是下限，不会跌破。
        if held > base + 1e-12:
            if pi < ext:
                _log(d, "清仓到基础仓" if base > 0 else "清仓", held, base)
                held, above, f_mid, f_low = base, 0, False, False
            else:
                if pi < mid and not f_mid:
                    tgt_w = max(base, held * half)
                    _log(d, "下穿中线·减半", held, tgt_w)
                    held, f_mid = tgt_w, True
                if pi < low and not f_low:
                    tgt_w = max(base, held * half)
                    _log(d, "跌破25%·再减半", held, tgt_w)
                    held, f_low = tgt_w, True

        out.append(held)

    tgt = pd.Series(out, index=idx, dtype="float64")
    return (tgt, events) if detail else tgt


# ---------------------------------------------------------------- 3. 回测（单腿 + 现金）
def cash_nav(index, annual=None):
    """现金腿净值（货基按日计息）。当作「债券腿」喂给 `erp.run`，费率置 0。"""
    a = float(L_CFG["cash_annual"] if annual is None else annual)
    n = len(index)
    return pd.Series((1.0 + a) ** (pd.RangeIndex(n) / 252.0), index=index, dtype="float64")


def run(df, target=None, fees=True, lag=None, month_end=False, tol=0.0):
    """单腿回测 -> dict(nav, w, trades, fees)。未投部分 = 现金（按货基计息）。

    直接复用 `../erp.py::run`（股+债两腿引擎）：把**现金净值**当成「债券腿」，
    债券腿费率置 0 —— 于是它就是一个「一条股票腿 + 现金」的逐日复算器，
    与 v4 用同一份成交/费用/记账代码。

    `month_end=True` 时每月末回锚到目标（只给常仓基准用；本规则自身不回锚，
    因为规则本身就规定了什么时候动）。
    """
    tgt = ladder_target(df) if target is None else target
    pe = df["tr"].astype("float64")
    ok = tgt.notna() & pe.notna()
    pe, tgt = pe[ok], tgt[ok]
    pb = cash_nav(pe.index)
    fee_eq = (float(L_CFG["fee_buy"]), float(L_CFG["fee_sell"])) if fees else (0.0, 0.0)
    pe_ends = (erp.period_end_flags(pe.index) if month_end
               else pd.Series(False, index=pe.index))
    r = erp.run(pe, pb, tgt,
                period_end=pe_ends,
                lag=int(L_CFG["lag"] if lag is None else lag),
                tol=float(tol), fee_eq=fee_eq, fee_bd=(0.0, 0.0), slip=0.0)
    r["m"] = erp.perf(r["nav"])
    r["target"] = tgt
    return r


def latest(df=None, **overrides):
    """当前读数（每日/自检共用）。"""
    df = frame() if df is None else df
    tgt, events = ladder_target(df, detail=True, **overrides)
    p = position(df)
    last = df.index[-1]
    return dict(date=last, df=df, pos=float(p.iloc[-1]), erp=float(df["erp"].iloc[-1]),
                pe=float(df["pe"].iloc[-1]), y10=float(df["y10"].iloc[-1]),
                ma=float(df["ma"].iloc[-1]), upper=float(df["upper"].iloc[-1]),
                lower=float(df["lower"].iloc[-1]), target=float(tgt.iloc[-1]),
                events=events, source=dict(erp.SOURCE))


def _self_check():
    df = frame()
    tgt, events = ladder_target(df, detail=True)
    r = latest(df)
    print(f"{LABEL} · 标的 {IDX_NAME}（单腿 0~100%，未投部分 = 货基 "
          f"{L_CFG['cash_annual']:.2%}）")
    print(f"通道 {L_CFG['channel_window']} 日 / ±{L_CFG['channel_k']:g}σ · 样本 "
          f"{df.index[0]:%Y-%m-%d} ~ {df.index[-1]:%Y-%m-%d} · 通道就绪 "
          f"{df[df['ready']].index[0]:%Y-%m-%d} 起")
    ready = df["ready"]
    print(f"位置口径 = 通道内位置 · 空仓 "
          f"{(tgt[ready] < 1e-9).mean():.1%} / 满仓 {(tgt[ready] > 1 - 1e-9).mean():.1%} · "
          f"平均仓位 {tgt[ready].mean():.0%}")
    print(f"动作共 {len(events)} 次：加仓 "
          f"{sum(1 for e in events if '建仓' in e['action'] or e['action'] == '加仓')} · "
          f"减半 {sum(1 for e in events if '减半' in e['action'])} · "
          f"清仓 {sum(1 for e in events if e['action'] == '清仓')}")
    print(f"最新 {r['date']:%Y-%m-%d}: pos {r['pos']:.2f} · ERP {r['erp']:.2f}% "
          f"(MA {r['ma']:.2f} / 上线 {r['upper']:.2f} / 下线 {r['lower']:.2f}) · "
          f"目标仓位 {r['target']:.0%}")
    print("最近 6 次动作:")
    for e in events[-6:][::-1]:
        print(f"  {e['date']:%Y-%m-%d} {e['action']:12s} "
              f"{e['before']:.0%} → {e['after']:.0%} · pos {e['pos']:.2f} · ERP {e['erp']:.2f}%")


if __name__ == "__main__":
    _self_check()
