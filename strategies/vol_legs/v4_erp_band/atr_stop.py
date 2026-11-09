# -*- coding: utf-8 -*-
"""V4 可选叠加：**ATR 移动止损**（把「ERP 想持有」的仓位按波动率纪律临时冻结）。

## 它解决 V4 的哪个问题

V4（`ladder.py`）的仓位是 {100% 股票, 0% 股票} 两态、由 ERP 的 z 决定，**与价格趋势无关**：
2015-06 股灾、2018 全年，z 都还没到清仓带，于是 100% 股票硬扛（回撤 -38.96%）。
止损要做的只有一件事：**在 z 还没反应过来之前，先按价格纪律把股票腿砍掉**，而不是改 ERP 的择时逻辑。

## 规则（叠加在 `target` 之上，不改 ladder 本体）

- 交易腿 = `df["tr"]`（沪深300 **全收益**指数，与回测同一列；本包只有收盘价，见下"ATR 口径"）。
- 移动止损：`stop = 持仓期内最高收盘 − k×ATR`；**浮盈 ≥ arm×ATR 之后才接管**
  （在此之前若 `hard_k` 不是 None，用「入场价 − hard_k×ATR」做硬止损；默认关闭 = 只有移动止损）。
- 触发：收盘 ≤ stop → 次日把股票仓位**压到 0**（全部进债券腿），并记一次 `stop`。
- 解冻（三个条件同时满足才恢复）：
  1. ERP 的原始目标 `target` 仍 > 0（ERP 自己没让清仓）；
  2. 收盘重新站上 `reentry_n` 日均线（趋势修复）；
  3. 解冻后 `peak` / `entry` 重置到当天。
- ERP 自己切到 0 时，止损状态一并复位（下次买入是全新一笔）。

## ATR 口径（重要）

本包的数据只有**收盘价**（`csi_000300` 有收盘+PE，`csi_H00300` 只有收盘），没有 H/L，
所以这里的 ATR = `|C − C_prev|` 的 Wilder(14) 平滑 —— 是**真实 ATR 的偏小近似**
（不含日内振幅，经验上低估 20%~30%）。因此 `k` 取 6 相当于真实 ATR 口径的 4~5 倍，
与 `v1_2_ma_trend_pv` 的 `6ATR` 不是同一个刻度，不要横向比较数值。

## 纪律

- 止损只**减仓**、不加仓；`w_stop = min(w_base, 1)` 天然成立。
- 成交沿用 `erp.run(lag=1)`：T 日收盘算出 `target_stop`，T+1 收盘成交，无前视。
- **默认关闭**（`CFG["enabled"]=False`）：参数是回测里挑的，未做样本外验证，只能当"实验性叠加"。

机械规则输出，非投资建议。
"""
import os
import sys

import numpy as np
import pandas as pd

try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:  # noqa: BLE001
    pass

_HERE = os.path.dirname(os.path.abspath(__file__))
_VL = os.path.dirname(_HERE)
for _p in (_HERE, _VL):
    if _p not in sys.path:
        sys.path.insert(0, _p)

import ladder as L  # noqa: E402

LABEL = "V4 叠加 ATR 移动止损"

# ---------------------------------------------------------------- 参数
CFG = {
    "enabled": False,        # 默认关闭；回测/研究里显式打开
    "atr_n": 14,             # Wilder 平滑窗口
    "k": 6.0,                # 移动止损 = 峰值 − k×ATR（收盘-ATR 口径，见模块 docstring）
    "arm": 4.0,              # 浮盈 ≥ arm×ATR 才启用移动止损（之前用 hard_k）
    "hard_k": None,          # None = 不设硬止损（只用移动止损；数值见 research 网格）
    "reentry": "ma",         # 解冻口径："ma" = 收盘重回均线；"erp" = 等 ERP 下一次「买满」事件
    "reentry_n": 20,         # "ma" 口径的均线窗口
    "price_col": "tr",       # 交易腿价格（全收益），止损与回测同源
}


def cfg(key, default=None):
    return CFG.get(key, default)


def atr_close(close, n=None):
    """收盘价 ATR：|C − C_prev| 的 Wilder(14) 平滑（无 H/L，见模块 docstring）。"""
    n = int(CFG["atr_n"] if n is None else n)
    tr = close.astype("float64").diff().abs()
    return tr.ewm(alpha=1.0 / n, adjust=False).mean()


def apply_stop(df, target=None, **override):
    """在 `target` 之上叠加 ATR 移动止损 -> DataFrame（index 与 df 相同）。

    列：
      w_base      原始目标（ladder 给的）
      w_stop      叠加后的目标（可直接当 erp.run 的 target）
      stop        当日止损价（无持仓/未 armed 时为 NaN）
      stopped     当日是否处于「刚被止损、等待解冻」状态
      hit         当日是否触发止损
      release     当日是否解冻
    """
    opt = {k: (override[k] if k in override else v) for k, v in CFG.items()}
    tgt = (df["target"] if target is None else target).astype("float64")
    px = df[opt["price_col"]].astype("float64")
    atr = atr_close(px, opt["atr_n"])
    ma = px.rolling(int(opt["reentry_n"]), min_periods=int(opt["reentry_n"])).mean()

    k, arm = float(opt["k"]), float(opt["arm"])
    hk = opt["hard_k"]
    hk = None if hk is None else float(hk)

    w_in = tgt.to_numpy(dtype="float64")
    p = px.to_numpy(dtype="float64")
    a = atr.to_numpy(dtype="float64")
    m = ma.to_numpy(dtype="float64")
    n = len(w_in)

    w_out = np.zeros(n)
    stop_lv = np.full(n, np.nan)
    stopped = np.zeros(n, dtype=bool)
    hit = np.zeros(n, dtype=bool)
    release = np.zeros(n, dtype=bool)

    mode = str(opt["reentry"]).lower()
    frozen = False          # 是否处于「止损后等解冻」
    peak = np.nan
    entry = np.nan
    armed = False           # **粘性**：一旦浮盈够过 arm 就一直 armed 到这笔结束
    prev_want = 0.0
    for t in range(n):
        want = w_in[t]
        x = p[t]
        if not np.isfinite(x) or not np.isfinite(a[t]):
            w_out[t] = 0.0
            prev_want = want
            continue
        if want <= 0:
            # ERP 自己不要股票：止损状态复位，什么都不做
            # （冻结期间若 ERP 先清仓，冻结随之结束 —— 记为 release，否则事件表会把
            #  「其实早已解冻」的区间错算成超长冻结）
            if frozen:
                release[t] = True
            frozen, peak, entry, armed = False, np.nan, np.nan, False
            w_out[t] = 0.0
            prev_want = want
            continue
        if frozen:
            stopped[t] = True
            if mode == "erp":
                # 解冻 = 必须等 ERP **重新**从空仓切回满仓（一次新的买满事件）
                ok = prev_want <= 0
            else:
                # 解冻 = 收盘重回均线（趋势修复）
                ok = bool(np.isfinite(m[t]) and x > m[t])
            if ok:
                frozen, peak, entry, armed = False, x, x, False
                release[t] = True
                w_out[t] = want
            else:
                w_out[t] = 0.0
            prev_want = want
            continue
        if not (np.isfinite(entry) and np.isfinite(peak)):
            entry, peak = x, x
        else:
            peak = max(peak, x)
        # arm 是「粘性开关」：浮盈一旦达到 arm×ATR 就永久打开（直到这笔结束），
        # 不能用「当日价 − 入场价」判定 —— 否则一旦回落就自动关闭，等于止损形同虚设。
        if not armed and (peak - entry) >= arm * a[t]:
            armed = True
        lvl = (peak - k * a[t]) if armed else (
            (entry - hk * a[t]) if hk is not None else -np.inf)
        stop_lv[t] = lvl if np.isfinite(lvl) else np.nan
        if np.isfinite(lvl) and x <= lvl:
            frozen, peak, entry, armed = True, np.nan, np.nan, False
            hit[t] = True
            stopped[t] = True
            w_out[t] = 0.0
        else:
            w_out[t] = want

    out = pd.DataFrame({"w_base": w_in, "w_stop": w_out, "stop": stop_lv,
                        "stopped": stopped, "hit": hit, "release": release},
                       index=df.index)
    return out


def attach(df, target=None, **override):
    """把叠加结果并到 df 上（新增 target_stop 等列），返回新 df。"""
    s = apply_stop(df, target=target, **override)
    out = df.copy()
    for col in ("w_stop", "stop", "stopped", "hit", "release"):
        out[col] = s[col].to_numpy()
    out["target_stop"] = s["w_stop"].to_numpy()
    return out


def target_with_stop(df):
    """按 `CFG["enabled"]` 决定是否叠加：关闭时原样返回 `df["target"]`。

    这是**策略层唯一入口**（回测/日报都该走它）；`apply_stop()` 是研究接口，总是按传入参数计算。
    """
    return attach(df)["target_stop"] if CFG["enabled"] else df["target"]


def summary(stop_df):
    """止损事件概览（回测/日报用）。"""
    hit = stop_df["hit"].to_numpy(dtype=bool)
    rel = stop_df["release"].to_numpy(dtype=bool)
    idx = stop_df.index
    eps = []
    start = None
    for t, h in enumerate(hit):
        if h:
            start = t
        elif start is not None and rel[t]:
            eps.append((idx[start], idx[t], t - start))
            start = None
    if start is not None:
        eps.append((idx[start], idx[-1], len(idx) - 1 - start))
    return dict(n_hit=int(hit.sum()), n_release=int(rel.sum()),
                stopped_share=float(stop_df["stopped"].mean()),
                episodes=eps)


def _self_check():
    df = L.frame()
    d = df[df["ready"]]
    print(f"{LABEL} · 自检（样本 {d.index[0]:%Y-%m-%d} ~ {d.index[-1]:%Y-%m-%d}）")
    print(f"参数: k={CFG['k']:g} arm={CFG['arm']:g} hard_k={CFG['hard_k']} "
          f"reentry=MA{CFG['reentry_n']} atr_n={CFG['atr_n']}")
    s = apply_stop(df)
    sm = summary(s)
    print(f"触发止损 {sm['n_hit']} 次 · 解冻 {sm['n_release']} 次 · "
          f"被止损冻结的天数占比 {sm['stopped_share']:.1%}")
    for a, b, n in sm["episodes"]:
        print(f"  {a:%Y-%m-%d} → {b:%Y-%m-%d}（{n} 个交易日）")
    last = s.iloc[-1]
    print(f"最新: w_base={last['w_base']:.0%} w_stop={last['w_stop']:.0%} "
          f"止损价={last['stop']:.1f}" if np.isfinite(last["stop"]) else "最新: 空仓/未 armed")


if __name__ == "__main__":
    _self_check()
