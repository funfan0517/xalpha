# -*- coding: utf-8 -*-
"""优化探针 2：风险预算（波动目标）为核心，检验多因子是否还有增量。

对照 v6-B：vol = std(120) * sqrt(252)，scale = clip(10%/vol, 0.2, 1)，死区 15%、限速 10 日。
"""
import os
import sys

import numpy as np
import pandas as pd

_HERE = os.path.dirname(os.path.abspath(__file__))
_V5 = os.path.dirname(os.path.dirname(_HERE))
sys.path.insert(0, os.path.dirname(_V5))
sys.path.insert(0, _V5)
sys.path.insert(0, _HERE)

import _probe_opt as P  # noqa: E402
import engine  # noqa: E402
import factors as F  # noqa: E402


def deadband_speed(raw, dead=0.15, speed=10):
    """死区 + 限速（v6-B 口径）：系数变动 < dead 不动；两次变动至少隔 speed 日。"""
    s = pd.Series(np.asarray(raw, dtype=float).ravel(), index=raw.index if hasattr(raw, "index") else None)
    first = s.first_valid_index()
    v = s.ffill().fillna(0.0).to_numpy(dtype=float)
    out = np.empty(len(v))
    cur = v[0] if len(v) else 0.0
    last = -10 ** 9
    for i, x in enumerate(v):
        if abs(x - cur) >= dead - 1e-12 and (i - last) >= speed:
            cur, last = x, i
        out[i] = cur
    out = pd.Series(out, index=s.index)
    return out.where(out.index >= first) if first is not None else out


def report(tag, dec, close):
    dec = pd.Series(dec, index=close.index) if not isinstance(dec, pd.Series) else dec
    nav, st, _ = P.nav_of(dec, close)
    out = [tag]
    for a, b in ((None, None), ("2013-08-23", "2019-12-31"),
                 ("2020-01-02", "2023-12-29"), ("2024-01-02", None)):
        m = engine.metrics(nav.loc[a:b] if a else nav)
        out.append(f"{m['ann']:+7.2%}/{m['mdd']:7.2%}/{m['sharpe']:5.2f}")
    m = engine.metrics(nav)
    tr = int((st.diff().abs() > 1e-9).sum())
    cost = float(st.diff().abs().sum() * P.FEE)
    return (f"{tag:<30} " + "  ".join(out) +
            f" | 仓位 {st.mean():4.0%} 调仓 {tr:4d} 成本 {cost:5.2%}")


if __name__ == "__main__":
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:  # noqa: BLE001
        pass
    px = F.price_frame()
    close = px["close"].astype(float)
    ret = close.pct_change()
    v20 = ret.rolling(20).std() * np.sqrt(252)
    v60 = ret.rolling(60).std() * np.sqrt(252)
    v120 = ret.rolling(120).std() * np.sqrt(252)
    rate = F.read_rate()
    T = F.table(px, rate, None, shares=None)
    score = T["score"].astype(float)
    m200 = close.rolling(200).mean()
    mom120 = close / close.shift(120) - 1.0

    print("列 = 全样本 / IS / OOS1 / OOS2（年化/回撤/夏普）\n")

    rows = []
    rows.append(report("B1 恒半仓", pd.Series(0.5, index=close.index), close))
    rows.append(report("B0 恒满仓", pd.Series(1.0, index=close.index), close))
    rows.append(report("V0 现规则", T["target"], close))

    # v6-B 口径
    for tv, vw, v in (("10%", 120, v120), ("8%", 120, v120), ("12%", 120, v120)):
        raw = (float(tv[:-1]) / 100.0 / v).clip(0.2, 1.0)
        rows.append(report(f"V6B {tv}/{vw}日 原始", raw, close))
        rows.append(report(f"V6B {tv}/{vw}日 死区限速", deadband_speed(raw), close))

    for tv in ("8%", "10%", "12%"):
        raw = (float(tv[:-1]) / 100.0 / v20).clip(0.2, 1.0)
        rows.append(report(f"VT {tv}/20日 原始", raw, close))
        rows.append(report(f"VT {tv}/20日 死区限速", deadband_speed(raw), close))

    # 因子叠加在风险预算之上
    base = (0.10 / v120).clip(0.2, 1.0)
    tilt_cfg = {
        "score×1.15/1.00/0.85": np.select([score >= 1, score <= -1], [1.15, 0.85], 1.0),
        "score×1.10/1.00/0.90": np.select([score >= 1, score <= -1], [1.10, 0.90], 1.0),
        "score>0 全仓 else 0.85": np.where(score > 0, 1.0, 0.85),
        "score<0 减半": np.where(score < 0, 0.5, 1.0),
        "年线上方才满配": np.where(close > m200, 1.0, 0.85),
        "动量120>0 才满配": np.where(mom120 > 0, 1.0, 0.85),
    }
    for tag, mult in tilt_cfg.items():
        rows.append(report(f"VT10/120 × {tag}", deadband_speed((base * mult).clip(0, 1)), close))

    print("\n".join(rows))
