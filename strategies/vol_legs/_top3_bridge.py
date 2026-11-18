# -*- coding: utf-8 -*-
"""v14~v20 单腿策略共享的「原生引擎桥」。

这七个策略的规则全部来自 `research/beat_best/_beat_best_top3.md`，逐日权重直接调用
各变种的**原生引擎函数**，不重写规则。仓库里有 5 个 `data.py` 和多个 `rule.py`/`engine.py`，
所以 import 与**调用**都必须包在 `_isolated()` 里（V12 `engine.py:296` 在调用时还会
二次 `import data` 并做路径 fail-fast 断言）。

对外接口统一为「**决策仓位**」：
    `decision[t]` = t 日收盘做出的仓位决定，t+1 收益开始生效。
每个策略的 `engine.weight(px)` 返回决策序列，`engine.run()` 内部 `shift(1)` 得到实际持仓。
这与 `_beat_best_top3.py` 里的 `hold` 一一对应：`hold[t] = decision[t-1]`。

七腿映射（v14~v20，每策略只有一条腿）：
    v14 a500  V3 MACD+BOLL ............ 研究确认无提升，保持当前 ★1
    v15 hs300  MIN2 双确认 (V2∩V12) ... ★ 超越项
    v16 ndx    V10 波动层 ............. 研究确认无提升，保持当前 ★1
    v17 spx    MAX2 任一在场 (V12∪V13)  ★ 超越项
    v18 kc50   E2×TV20 波动上限 ....... ★ 超越项
    v19 hkt    V3 MACD+BOLL ........... 研究确认无提升，保持当前 ★1
    v20 gold   V6-B 控回撤 ............ 研究确认无提升，保持当前 ★1

用法（各策略 engine.py）：
    from _top3_bridge import bridge
    def weight(px):
        return bridge.v2(px, "hs300")
"""
import contextlib
import importlib
import math
import os
import sys

import numpy as np
import pandas as pd

try:
    sys.stdout.reconfigure(encoding="utf-8")  # type: ignore[attr-defined]
except Exception:  # noqa: BLE001
    pass

_HERE = os.path.dirname(os.path.abspath(__file__))   # strategies/vol_legs
_ROOT = os.path.dirname(os.path.dirname(_HERE))
if _HERE not in sys.path:
    sys.path.insert(0, _HERE)

import data as vdata  # noqa: E402

TD = 252
CASH_ANNUAL = 0.014
FEE_SIDE = 0.0010            # 研究口径：统一单边 10bp（与 _beat_best_top3.md 完全一致）
WARMUP = 300                 # 与研究脚本同一窗口

LEG_NAME = {"a500": "中证A500", "hs300": "沪深300", "ndx": "纳指100", "spx": "标普500",
            "kc50": "科创50", "hkt": "恒生科技", "gold": "黄金"}

_SPECS = {
    "v1": ("v1_price_volume/v1_1_ma20", ("data", "rule", "engine", "ma20"), "ma20"),
    "v2": ("v2_boll_breakout", ("data", "rule", "engine", "boll"), "boll"),
    "v3": ("v3_macd_boll", ("data", "rule", "engine", "macd_boll"), "macd_boll"),
    "v6b": ("v6_gold", ("data", "rule", "engine", "gold"), "gold"),
    "v9": ("v9_vol_target", ("data", "rule", "engine", "vol_target"), "vol_target"),
    "v10": ("v10_vol_layer", ("data", "rule", "engine", "vol_layer"), "vol_layer"),
    "v12": ("v12_octo_trend", ("data", "rule", "engine"), "engine"),
    "v13": ("v13_quad_9grid", ("data", "rule", "engine"), "engine"),
}


# v14~v20 自己的目录必须从原生引擎的搜索路径里临时摘掉：v1/v2/v3/v6/v9/v10
# 都没有自己的 engine.py，import engine 会回落到 sys.path 上的第一个 engine.py。
# 如果此时调用方正是某个新策略的 engine.py（还没初始化完），就会形成循环导入。
_SHADOW_DIRS = {
    os.path.join(_HERE, d) for d in (
        "v14_a500_v3", "v15_hs300_min2", "v16_ndx_v10", "v17_spx_max2",
        "v18_kc50_e2tv", "v19_hkt_v3", "v20_gold_v6b")
} | {os.path.join(_HERE, d, "research") for d in (
        "v14_a500_v3", "v15_hs300_min2", "v16_ndx_v10", "v17_spx_max2",
        "v18_kc50_e2tv", "v19_hkt_v3", "v20_gold_v6b")}


@contextlib.contextmanager
def isolated(directory, names):
    """隔离 sys.path / sys.modules，避免同名模块串味（与 intraday_preview 同范式）。"""
    old_path = sys.path[:]
    saved = {n: sys.modules.pop(n) for n in names if n in sys.modules}
    abs_shadow = {os.path.abspath(p) for p in _SHADOW_DIRS}
    sys.path[:] = [p for p in sys.path
                   if os.path.abspath(p) not in abs_shadow
                   and os.path.abspath(p) != os.path.abspath(directory)]
    sys.path.insert(0, directory)
    if _HERE not in sys.path:            # 兜底：让 import engine 落到 vol_legs/engine.py
        sys.path.append(_HERE)
    try:
        yield
    finally:
        sys.path[:] = old_path
        for n in names:
            sys.modules.pop(n, None)
        sys.modules.update(saved)


class _Bridge:
    def __init__(self):
        self.mods, self.dmods = {}, {}
        for key, (sub, names, mod) in _SPECS.items():
            d = os.path.join(_HERE, sub)
            with isolated(d, names):
                self.mods[key] = importlib.import_module(mod)
                self.dmods[key] = importlib.import_module("data")

    def _ctx(self, key):
        sub, names, _mod = _SPECS[key]
        return isolated(os.path.join(_HERE, sub), names)

    # ---------------------------------------------------------- 原生决策仓位
    def v1(self, px, leg=None):
        with self._ctx("v1"):
            sig, _ = self.mods["v1"].signal(px)
        return pd.Series(sig, index=px.index).astype(float)

    def v2(self, px, leg=None):
        with self._ctx("v2"):
            m = self.mods["v2"]
            return m.weight(px, m.signal(px)[0]).reindex(px.index).fillna(0.0).clip(0.0, 1.0)

    def v3(self, px, leg=None):
        with self._ctx("v3"):
            m = self.mods["v3"]
            return m.weight(px, m.signal(px)).reindex(px.index).fillna(0.0).clip(0.0, 1.0)

    def v6b(self, px, leg=None):
        with self._ctx("v6b"):
            st = self.mods["v6b"].state_b(px)
        return st["state"].reindex(px.index).fillna(0.0).clip(0.0, 1.0)

    def v9(self, px, leg):
        """`machine()` 的 s[t] = t 收盘已持有仓 = 决策[t-1]；作为决策序列直接用 s。"""
        with self._ctx("v9"):
            m = self.mods["v9"]
            s, _ = m.machine(px, m.leg_params(leg), leg=leg)
        return s.reindex(px.index).fillna(1.0).clip(0.0, 1.0)

    def v10(self, px, leg):
        """`state_daily[t] = target_raw[t-1]` = 决策[t-1]；作为决策序列直接用 state_daily。

        与研究脚本一致：hold[t] = state_daily[t-1] = target_raw[t-2]（T+2 生效）。
        """
        with self._ctx("v10"):
            s = self.mods["v10"].state_daily(px.index, leg)
        return s.reindex(px.index).fillna(1.0).clip(0.0, 1.0)

    def v12(self, px, leg):
        with self._ctx("v12"):
            r = self.mods["v12"].backtest_leg(leg, df=self.dmods["v12"].frame(leg))
        return r["w"].reindex(px.index).ffill().fillna(0.0).clip(0.0, 1.0)

    def v13(self, px, leg):
        with self._ctx("v13"):
            sm = self.mods["v13"].state_machine(self.dmods["v13"].frame(leg), callback_add=True)
        return sm["w"].reindex(px.index).ffill().fillna(0.0).clip(0.0, 1.0)

    # ---------------------------------------------------------- v18 的波动上限层
    def vol_cap(self, px, tv=0.20, win=20, dead=0.05, gap=5):
        """与研究脚本完全同一算式：`raw = min(1, TV/σ).shift(1)` + 死区/限速。"""
        vol = px.pct_change().rolling(win, min_periods=win).std() * math.sqrt(TD)
        raw = (tv / vol).clip(upper=1.0).shift(1)
        out = np.zeros(len(raw))
        cur, last = 0.0, -10 ** 9
        for i, v in enumerate(raw.to_numpy(dtype=float)):
            if np.isfinite(v) and (i - last) >= gap and abs(v - cur) >= dead - 1e-12:
                cur, last = float(v), i
            out[i] = cur
        return pd.Series(out, index=px.index)

    def cap_for_decision(self, px, tv=0.20):
        """把 `vol_cap` 左移一格：decision[t] 要乘的是 cap[t+1]（用 t 日收盘已知的信息）。"""
        c = self.vol_cap(px, tv=tv).shift(-1)
        if len(c):
            c.iloc[-1] = self.vol_cap(px, tv=tv).iloc[-1]
        return c.fillna(0.0).clip(0.0, 1.0)

    # ---------------------------------------------------------- 公共回测/绩效
    @staticmethod
    def load(leg):
        return vdata.series_for_leg(leg).dropna()

    @staticmethod
    def run(px, decision, cash=None, fee=None):
        """T 日收盘决策 → T+1 收益；成本按 |Δ持仓| 单边扣（研究口径 10bp）。"""
        cash_d = ((CASH_ANNUAL if cash is None else float(cash)) + 1.0) ** (1.0 / TD) - 1.0
        fee = FEE_SIDE if fee is None else fee
        h = decision.reindex(px.index).fillna(0.0).astype(float).clip(0.0, 1.0).shift(1).fillna(0.0)
        ret = px.pct_change().fillna(0.0)
        d = h.diff().fillna(h)
        cost = d.abs() * fee if not isinstance(fee, tuple) else (
            d.clip(lower=0.0) * fee[0] + (-d.clip(upper=0.0)) * fee[1])
        return (1.0 + (h * ret + (1.0 - h) * cash_d - cost)).cumprod()

    @staticmethod
    def perf(nav):
        """与研报同口径：年化 / 波动 / 回撤 / 夏普 / Calmar / 累计。"""
        r = nav.pct_change().dropna()
        n = len(r)
        if n < 2:
            return dict(ann=np.nan, vol=np.nan, mdd=np.nan, sharpe=np.nan,
                        calmar=np.nan, cum=np.nan)
        ann = float(nav.iloc[-1] ** (TD / n) - 1.0)
        vol = float(r.std() * math.sqrt(TD))
        mdd = float((nav / nav.cummax() - 1.0).min())
        return dict(ann=ann, vol=vol, mdd=mdd,
                    sharpe=(ann - CASH_ANNUAL) / vol if vol > 0 else np.nan,
                    calmar=ann / abs(mdd) if mdd < 0 else np.nan,
                    cum=float(nav.iloc[-1] - 1.0))


bridge = _Bridge()
