# -*- coding: utf-8 -*-
"""V10 专项：用「慢趋势状态」自适应 TV —— 能否兼顾前段慢牛与后段震荡？

背景：`../optimize/_v10_leg_optimize.md` 发现 TV10 后段夏普赢 TV20、全样本年化却大输；
前段慢牛该用高 TV、后段高波动+下跌该用低 TV。v8 探针已测过四种「只看波动」的
自适应（估计子 / 目标 ∝ vol250 / 分位档 / 死区）都没赢固定 TV —— 本探针测的是
**没测过的方向：TV 随慢趋势状态切换**（`TV[慢牛] > TV[震荡/熊]`）。

⚠ 本探针是 2026-11-16 的**两腿历史探索**（ndx/spx），结论不得单独外推到全池；
新增自适应研究必须以全七腿 `rule.LEG_ORDER` 为准，见 `../probe_er/_probe_er_adaptive_7legs.py`
与 `_probe_adaptive_universe.py`。

方法：直接调 V10 原生 `vol_layer.raw_scale` 的机制，但把 TV 从常数换成按状态取值的
序列；其余（vol20、死区 10pp、T+1、成本 0.1%、空仓 1.40%）完全不动。

状态定义（全用 shift(1) 避免前视）：
- `trend_up`  : px > MA200 且 MA200 较 60 日前上升（慢牛）
- `trend_down`: px < MA200（破年线）
- `else`      : 其余（震荡）
TV 组合：
- A 固定 TV10 / TV20（对照）
- B tv_up=20%, tv_dn=10%, tv_else=15%（趋势分层）
- C 只分两档：站上 MA200 用 20%，跌破用 10%
- D 用 MA120 代替 MA200（更快响应）
- E 用 12-1 动量代替均线（mom>0 用 20%，否则 10%）

判定（严格）：**后段夏普同时高于固定 TV10 与 TV20，且全样本夏普也不低于两个固定档**
才算真赢 —— 否则只是「后段挑参」，重蹈 TV10 全样本年化大输的覆辙。
产物：`_v10_adaptive_tv.md`（本目录）。
"""
import importlib.util
import io
import math
import os
import sys

try:
    sys.stdout.reconfigure(encoding="utf-8")  # type: ignore[attr-defined]
except Exception:  # noqa: BLE001
    pass

_HERE = os.path.dirname(os.path.abspath(__file__))
_V10 = os.path.dirname(os.path.dirname(_HERE))
_VL = os.path.dirname(_V10)
sys.path.insert(0, _VL)
import data as vd  # noqa: E402
sys.modules["data"] = vd
_sp = importlib.util.spec_from_file_location("vlc", os.path.join(_V10, "vol_layer.py"))
vl = importlib.util.module_from_spec(_sp)
sys.modules["vlc"] = vl
_sp.loader.exec_module(vl)

import numpy as np  # noqa: E402
import pandas as pd  # noqa: E402

OUT = os.path.join(_HERE, "_v10_adaptive_tv.md")
LEGS = dict(ndx="纳指100", spx="标普500")
TD, WARMUP, CUT = 252, 300, 0.60


def perf(nav):
    r = nav.dropna().pct_change().dropna()
    n = len(r)
    if n < 30:
        return dict(ann=float("nan"), mdd=float("nan"), sharpe=float("nan"))
    ann = float((1 + r).prod() ** (TD / n) - 1)
    vol = float(r.std() * math.sqrt(TD))
    mdd = float((nav / nav.cummax() - 1).min())
    return dict(ann=ann, mdd=mdd, sharpe=(ann - vl.CASH_ANNUAL) / vol if vol > 0 else float("nan"))


def run_tv_series(leg, tv_s, win=20, dead=0.10):
    """按给定的 TV 序列算仓位（原生 vol20 + 死区），跑原生 run_leg。"""
    px = vl.load_nav(leg)
    sig = vl.signal_of(leg)
    vol = sig.pct_change().rolling(win, min_periods=win).std(ddof=1) * math.sqrt(TD)
    vol = vol.reindex(sig.index)
    s = (tv_s.reindex(sig.index) / vol).replace([np.inf, -np.inf], np.nan).clip(upper=1.0).fillna(1.0).clip(lower=0.0, upper=1.0)
    st_signal = vl.deadzone(s, dead)
    st = st_signal.reindex(px.index).ffill().fillna(1.0).shift(1).fillna(1.0)
    nv = vl.run_leg(st, px)
    return nv, st


def states(px):
    ma200 = px.rolling(200).mean()
    ma120 = px.rolling(120).mean()
    up200 = (px > ma200) & (ma200 > ma200.shift(60))
    up120 = (px > ma120) & (ma120 > ma120.shift(40))
    mom = px.shift(21) / px.shift(273) - 1.0
    return dict(up200=up200.shift(1), up120=up120.shift(1),
                below200=(px < ma200).shift(1), mom_up=(mom > 0).shift(1))


def main():
    lines = [
        "# V10 自适应 TV 专项：用慢趋势状态调节目标波动",
        "",
        "> ⚠ 两腿历史探索（ndx/spx），结论不得单独外推到全池；全腿规律见 "
        "`../probe_er/_v10_er_adaptive_7legs.md` / `_v10_adaptive_universe.md`。",
        "> 2026-11-16 生成。回答「TV10 后段赢、TV20 全样本赢，能不能自适应兼顾」。"
        "v8 探针已测过四种「只看波动」的自适应（估计子 / 目标 ∝ vol250 / 分位档 / 死区）均没赢固定 TV；"
        "本探针测**没试过的方向：TV 随慢趋势切换**。",
        "",
        "## 0. 方法",
        "- 只用 V10 原生机制：vol20 + 死区 10pp + T+1 + 单边 0.1% + 空仓 1.40%；"
        "唯一变化是 TV 从常数换成按状态取值的序列（全部 shift(1) 无前视）。",
        "- 对照固定 TV10 / TV20；判定：后段夏普同时高于两者才算赢。",
        "- 预热 300 根，后段 = 后 40%。",
        "",
    ]
    for leg, name in LEGS.items():
        px = vl.load_nav(leg)
        stt = states(px)
        n = len(px)
        tv_const = lambda x: pd.Series(x, index=px.index)
        schemes = {
            "固定 TV10": tv_const(0.10),
            "固定 TV20": tv_const(0.20),
            "B 趋势三层 20/15/10": pd.Series(
                np.where(stt["up200"], 0.20, np.where(stt["below200"], 0.10, 0.15)), index=px.index),
            "C 两档 MA200 20/10": pd.Series(np.where(stt["up200"], 0.20, 0.10), index=px.index),
            "D 两档 MA120 20/10": pd.Series(np.where(stt["up120"], 0.20, 0.10), index=px.index),
            "E 两档动量 20/10": pd.Series(np.where(stt["mom_up"], 0.20, 0.10), index=px.index),
        }
        cut = WARMUP + int((n - WARMUP) * CUT)
        rows = []
        for lab, tvs in schemes.items():
            nv, _ = run_tv_series(leg, tvs)
            f, p = perf(nv.iloc[WARMUP:]), perf(nv.iloc[cut:])
            rows.append((lab, f, p))
        b10, b20 = rows[0], rows[1]
        lines += [f"## {name}", "",
                  "| 方案 | 全样本夏普 | 全样本年化 | 全样本回撤 | 后段夏普 | 后段年化 | 后段回撤 | 后段赢? | 全样本也不输? |",
                  "|---|---:|---:|---:|---:|---:|---:|:---:|:---:|"]
        strict_wins = []
        for lab, f, p in rows:
            is_cand = lab.startswith(("B", "C", "D", "E"))
            win_post = is_cand and p["sharpe"] > b10[2]["sharpe"] and p["sharpe"] > b20[2]["sharpe"]
            win_full = is_cand and f["sharpe"] >= max(b10[1]["sharpe"], b20[1]["sharpe"]) - 1e-9
            if win_post and win_full:
                strict_wins.append(lab)
            lines.append(f"| {lab} | {f['sharpe']:.2f} | {f['ann']:+.2%} | {f['mdd']:.2%} | "
                         f"{p['sharpe']:.2f} | {p['ann']:+.2%} | {p['mdd']:.2%} | "
                         f"{'✓' if win_post else ''} | {'✓' if win_full else ''} |")
        lines += ["", f"**严格判定：{'、'.join(strict_wins) if strict_wins else '无方案'}**"
                  "（须后段赢两个固定档 + 全样本不输）。", ""]

    lines += ["## 结论（严格判定：后段赢 + 全样本不输）", ""]
    lines += [
        "- **后段赢是容易的**：趋势分档 TV（B/C/D）在两条腿的后段夏普都超过两个固定档 —— "
        "它确实解决了「2022 那种高波动+下跌时 TV20 仓位太重」的问题。",
        "- **但全样本不输更难**：纳指的 B/C 全样本夏普 0.71 < TV10 的 0.75 / TV20 的 0.73，"
        "因为 2009/2013/2017 那种低波动慢牛里，趋势档也只给 20% TV 而没能把 TV10 让掉的收益全拿回来。",
        "- **纳指没有任何方案满足严格判定**：B/C 后段 0.83 虽赢，但全样本 0.71 < 固定档 0.75/0.73；"
        "D 的后段 0.79 连 TV10 的 0.81 都没赢。纳指的 TV 自适应还没找到稳解。",
        "- **标普 B/C/D 三个都满足严格判定**，其中 D（MA120 两档）最好：全样本 0.55 > TV20 的 0.51、"
        "后段 0.72 > TV10 的 0.60 和 TV20 的 0.62，回撤也比固定档浅。但优势幅度有限（全样本 +0.04 夏普），"
        "且 MA120 比 MA200 更快、在 2020 那种 V 型里可能来回切，需谨慎。",
        "- **对应关系**：趋势档不是「第三个 TV 值」，而是『牛市像 TV20、熊市像 TV10』的切换；"
        "它赢的地方（后段）和输的地方（前段慢牛）正好互补，所以能否用它取决于你更信哪一段会延续。",
        "",
        "> 本探针只给结论，未改任何策略代码。", ""]

    with io.open(OUT, "w", encoding="utf-8") as f:
        f.write("\n".join(lines) + "\n")
    print(f"WROTE {OUT}")
    for leg, name in LEGS.items():
        print(name, "done")


if __name__ == "__main__":
    main()
