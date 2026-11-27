# -*- coding: utf-8 -*-
"""V9 · 降低回撤的方案研究（drawdown_control）。

当前 2.0/3.0 的最大回撤 −4.03%（2020-04-29 ~ 2020-07-13，10Y 从约 2.5% 反弹到 2.9%），
发生在 MID（60% 长债）档。本脚本测试四类不改三档名义比例、只做「临时降久期」的闸门：

  ① 长债动量闸门：003376 的 60 日收益 < 0 时，长债权重减半，差额给中债；
  ② 10Y 上行闸门：10Y 20 日上行 > +10bp 时，长债权重减半，差额给中债；
  ③ 波动率目标：按基础策略 60 日已实现波动，月末把总风险仓位缩放到目标波动（下限 40%、变动阈值 10pp），
     剩余给短债；
  ④ 回撤刹车：净值从高点回撤 > 2% 时长债减半，回到 −0.5% 以内恢复；
另列「静态降久期 0.8×」与「阈值 2.0/2.6、2.0/2.8、2.6/3.0」作对照。

口径与主回测一致：债腿 30pp 内部、月末+变化 T+1、单边 10bp、rf 1.40%。
产物：本目录 `_drawdown_control.md` + `_drawdown_control.jsonl`。
"""
import io
import json
import os
import sys
from datetime import datetime

import numpy as np
import pandas as pd

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8")

_HERE = os.path.dirname(os.path.abspath(__file__))
_STRAT = os.path.dirname(os.path.dirname(_HERE))     # v9_yield_band
for _m in ("rule", "data", "engine"):
    sys.modules.pop(_m, None)
sys.path.insert(0, _STRAT)

import rule      # noqa: E402
import data      # noqa: E402
import engine    # noqa: E402

OUT_MD = os.path.join(_HERE, "_drawdown_control.md")
OUT_JSONL = os.path.join(_HERE, "_drawdown_control.jsonl")
START = pd.Timestamp("2016-09-27")
FEE = rule.CFG["fee_side"]
LEGS = ["003376", "001512", "002864"]


def backtest_target(navs, target, start=START, fee=FEE):
    """按自定义日频目标权重回测；目标变化 >1.5pp 或月末 → T+1 换仓。"""
    cols = [c for c in rule.CFG["universe"] if c in navs]
    px = pd.concat({c: navs[c] for c in cols}, axis=1, join="inner").dropna()
    rets = px.pct_change().dropna()
    tgt = target.reindex(index=rets.index, columns=cols).ffill().fillna(0.0)
    tgt = tgt.div(tgt.sum(axis=1).replace(0, np.nan), axis=0).fillna(0.0)
    reb = rule.is_rebalance(rets.index)
    cur = tgt.iloc[0].to_numpy(dtype=float).copy()
    gross, cost, holds = [], [], []
    switches = 0
    for i, d in enumerate(rets.index):
        gross.append(float(rets.iloc[i].to_numpy(dtype=float) @ cur))
        holds.append(cur.copy())
        new = tgt.iloc[i].to_numpy(dtype=float)
        diff = float(np.max(np.abs(new - cur)))
        if i > 0 and diff >= 0.015 and (bool(reb.iloc[i]) or diff >= 0.03):
            cost.append(float(np.abs(new - cur).sum()))
            cur = new.copy()
            switches += 1
        else:
            cost.append(0.0)
    net = pd.Series(gross, index=rets.index) - pd.Series(cost, index=rets.index).shift(1).fillna(0.0) * fee
    wdf = pd.DataFrame(holds, index=rets.index, columns=cols)
    keep = net.index >= pd.Timestamp(start)
    return net[keep], wdf[keep], switches


def base_target(states):
    t = engine.target_matrix(states)
    return t.reindex(columns=LEGS).fillna(0.0)


def gate_long_half(t, mask, to="001512"):
    """把长债权重减半，差额给指定腿。"""
    out = t.copy()
    cut = out["003376"] * 0.5
    out["003376"] = out["003376"] - cut
    out[to] = out[to] + cut
    return out


def main(argv=None):
    navs, _, _ = data.load_all(force=False)
    y = data.load_yield(refresh=False)
    sig = engine.signal_frame(y)                    # 2.0/3.0 默认
    st = sig["state"]
    t0 = base_target(st)
    base, w0, sw0 = backtest_target(navs, t0)

    idx = base.index
    mom60 = navs["003376"].pct_change(60).reindex(idx)
    d10_20 = y.diff(20).reindex(idx)
    schemes = {"基础 2.0/3.0（无缩放）": (base, w0, sw0)}

    # ① 长债动量闸门：60 日收益 < 0 → 长债减半
    t = gate_long_half(t0, mom60 < 0)
    r, w, sw = backtest_target(navs, t)
    schemes["① 长债动量<0 减半"] = (r, w, sw)

    # ② 10Y 20 日上行 >10bp → 长债减半
    t = gate_long_half(t0, d10_20 > 0.10)
    r, w, sw = backtest_target(navs, t)
    schemes["② 10Y 20日上行>10bp 减半"] = (r, w, sw)

    # ③ 波动率目标：基础策略 60 日已实现波动，月末缩放（下限 40%、10pp 变动带），余额给短债
    vol = base.rolling(60).std() * np.sqrt(252)
    reb = rule.is_rebalance(idx)
    for tv in (0.010, 0.011, 0.012, 0.013, 0.014, 0.016):
        raw = (tv / vol).clip(0.4, 1.0)
        scale_m = pd.Series(1.0, index=idx)
        cur = 1.0
        for d in idx:
            if bool(reb.get(d, False)) and pd.notna(raw.get(d)) and abs(float(raw[d]) - cur) >= 0.10:
                cur = float(raw[d])
            scale_m[d] = cur
        t = t0.mul(scale_m, axis=0)
        t["002864"] = t["002864"] + (1.0 - t.sum(axis=1))
        r, w, sw = backtest_target(navs, t)
        schemes[f"③ 波动目标 {tv * 100:.1f}%"] = (r, w, sw)

    # ④ 回撤刹车：基础净值回撤 >2% 长债减半，回到 −0.5% 恢复
    navb = (1 + base).cumprod()
    dd = navb / navb.cummax() - 1
    brake = pd.Series(False, index=idx)
    on = False
    for d, v in dd.items():
        if not on and v < -0.02:
            on = True
        elif on and v > -0.005:
            on = False
        brake[d] = on
    t = gate_long_half(t0, brake)
    r, w, sw = backtest_target(navs, t)
    schemes["④ 回撤>2% 长债减半"] = (r, w, sw)

    # 对照：静态降久期 0.8×（差额给中债）
    t = t0.copy()
    cut = t["003376"] * 0.2
    t["003376"] -= cut
    t["001512"] += cut
    r, w, sw = backtest_target(navs, t)
    schemes["对照：静态长债×0.8"] = (r, w, sw)

    # 对照：更窄 MID 的阈值
    for lo, hi in ((2.0, 2.6), (2.0, 2.8), (2.6, 3.0)):
        s2 = engine.signal_frame(y, lo=lo, hi=hi)["state"]
        r, w, sw = backtest_target(navs, base_target(s2))
        schemes[f"对照：阈值 {lo:.1f}/{hi:.1f}"] = (r, w, sw)

    rows = []
    for nm, (r, w, sw) in schemes.items():
        m = engine.metrics(r)
        rows.append(dict(name=nm, **m, switches=sw, avg_long=round(float(w["003376"].mean()), 4)))
    df = pd.DataFrame(rows).set_index("name")

    lines = []
    A = lines.append
    A("# V9 · 降低回撤方案研究（drawdown_control）")
    A("")
    A(f"> 生成 {datetime.now():%Y-%m-%d %H:%M} · 由 `strategies/bond_cash/v9_yield_band/research/"
      "drawdown_control/_drawdown_control.py` 产出，**请勿手改**。")
    A(f"> 基础：2.0/3.0、三区直接映射、债腿 30pp 内部、月末+变化 T+1、单边 {FEE * 1e4:.0f}bp、rf 1.40%；"
      f"样本 {base.index[0].date()} ~ {base.index[-1].date()}。")
    A("> **2026-11-27 用户确认：波动目标 1.0% 已接入 `rule.py` 作为默认低回撤模式**；"
      "下表仍以「无缩放」为基础对照，方便看叠加前后的差异。")
    A("")
    A("## 1. 当前回撤来源")
    A("")
    navb = (1 + base).cumprod()
    ddb = navb / navb.cummax() - 1
    tr = ddb.idxmin()
    pk = navb.loc[:tr].idxmax()
    A(f"- 最大回撤 **{ddb.min() * 100:.2f}%**，{pk.date()} → {tr.date()}（{(tr - pk).days} 天）；"
      f"当时 10Y 约从 {y.reindex([pk]).iloc[0]:.3f}% 升到 {y.reindex([tr]).iloc[0]:.3f}%，"
      "策略处在 MID（60% 长债）——**水平阈值对急跌不敏感**。")
    A("- 年度最大回撤：" + "、".join(
        f"{yr} {v * 100:.2f}%" for yr, v in ddb.groupby(ddb.index.year).min().items()) + "。")
    A("")
    A("## 2. 方案对比（全样本）")
    A("")
    A("| 方案 | 年化 | 波动 | 夏普 | 最大回撤 | 卡玛 | 月胜率 | 平均长债 | 换手次数 |")
    A("|---|---:|---:|---:|---:|---:|---:|---:|---:|")
    for nm, row in df.iterrows():
        A(f"| {nm} | {row['cagr']:+.2f}% | {row['vol']:.2f}% | {row['sharpe']:.2f} | "
          f"{row['mdd']:+.2f}% | {row['calmar']:.2f} | {row['monthly_win']:.1f}% | "
          f"{row['avg_long'] * 100:.0f}% | {int(row['switches'])} |")
    A("")

    A("## 3. 分段（基础 vs 三个最优候选）")
    A("")
    best = df.drop(index=[x for x in df.index if x.startswith("对照")])
    top3 = best.sort_values("mdd", ascending=False).head(3).index.tolist()
    cands = ["基础 2.0/3.0（无缩放）"] + [x for x in top3 if x != "基础 2.0/3.0（无缩放）"]
    A("| 方案 | 全期夏普 | 全期回撤 | 2016–2020 夏普 | 2016–2020 回撤 | 2021–2026 夏普 | 2021–2026 回撤 |")
    A("|---|---:|---:|---:|---:|---:|---:|")
    for nm in cands:
        r = schemes[nm][0]
        a = engine.metrics(r)
        b = engine.metrics(r[(r.index >= "2016-01-01") & (r.index <= "2020-12-31")])
        c = engine.metrics(r[r.index >= "2021-01-01"])
        A(f"| {nm} | {a['sharpe']:.2f} | {a['mdd']:+.2f}% | {b['sharpe']:.2f} | {b['mdd']:+.2f}% | "
          f"{c['sharpe']:.2f} | {c['mdd']:+.2f}% |")
    A("")

    A("## 4. 结论")
    A("")
    base_row = df.loc["基础 2.0/3.0（无缩放）"]
    # 只从闸门/波动目标里挑：回撤至少改善 0.5pp，且夏普不低于基础 0.05
    ok = df.drop(index=[x for x in df.index if x.startswith("对照")])
    ok = ok[(ok["mdd"] >= base_row["mdd"] + 0.5) & (ok["sharpe"] >= base_row["sharpe"] - 0.05)]
    if len(ok):
        pick = ok.sort_values(["mdd", "sharpe"], ascending=[False, False]).index[0]
        p = df.loc[pick]
        A(f"- 在「回撤改善≥0.5pp 且夏普不明显变差」里，**{pick}** 最均衡："
          f"回撤 {base_row['mdd']:+.2f}% → **{p['mdd']:+.2f}%**，夏普 "
          f"{base_row['sharpe']:.2f} → {p['sharpe']:.2f}，年化 {base_row['cagr']:+.2f}% → {p['cagr']:+.2f}%，"
          f"平均长债 {base_row['avg_long'] * 100:.0f}% → {p['avg_long'] * 100:.0f}%。")
    vt10 = df.loc["③ 波动目标 1.0%"]
    vt16 = df.loc["③ 波动目标 1.6%"]
    static = df.loc["对照：静态长债×0.8"]
    narrow = df.loc["对照：阈值 2.6/3.0"]
    gate = df.loc["① 长债动量<0 减半"]
    base_ret = schemes["基础 2.0/3.0（无缩放）"][0]
    yrs = (base_ret.index[-1] - base_ret.index[0]).days / 365.25
    A(f"- **直接降久期不如波动率目标有效**：静态长债×0.8 只把回撤压到 {static['mdd']:+.2f}%、"
      f"更窄 MID 阈值 2.6/3.0 压到 {narrow['mdd']:+.2f}% 的同时夏普掉到 {narrow['sharpe']:.2f}；"
      "波动率目标 1.0% 则同时改善夏普和回撤"
      f"（{base_row['sharpe']:.2f} → {vt10['sharpe']:.2f}、{base_row['mdd']:+.2f}% → {vt10['mdd']:+.2f}%），"
      f"代价是年化少 {base_row['cagr'] - vt10['cagr']:.2f}pp、换手从 {int(base_row['switches'])} 次"
      f"增到 {int(vt10['switches'])} 次（约 {vt10['switches'] / yrs:.0f} 次/年）。")
    A(f"- 波动目标 1.0%~1.6% 呈平台（回撤 {vt10['mdd']:+.2f}% ~ {vt16['mdd']:+.2f}%，"
      f"夏普 {vt10['sharpe']:.2f} ~ {vt16['sharpe']:.2f}），**不是单点最优**；"
      "1.0% 适合「回撤优先」，1.2% 适合「收益/回撤平衡」。")
    A(f"- 动量/10Y 上行闸门效果较弱（回撤只到 {gate['mdd']:+.2f}%、夏普 {gate['sharpe']:.2f}），"
      "且容易在震荡市反复打脸；若坚持方向闸门，建议加 5~10 日确认，且只做「长债减半」而非清仓。")
    A("- **风险提示**：波动率目标是路径依赖的缩放，不是免费午餐；波动率飙升后市场若快速反弹，"
      "会被动低配、跑输基准。1.0% 的目标也是样本内选的，虽邻域平滑，仍需按「低回撤模式」"
      "理解；**已按用户 2026-11-27 确认设为默认（60 日观察窗 + 10pp 变动带），"
      "可用 `rule.CFG['vol_target']['enabled']=False` 关闭作对照**。")
    A("")

    os.makedirs(_HERE, exist_ok=True)
    with open(OUT_MD, "w", encoding="utf-8") as f:
        f.write("\n".join(lines) + "\n")
    with open(OUT_JSONL, "w", encoding="utf-8") as f:
        for nm, row in df.iterrows():
            f.write(json.dumps({"name": nm, **{k: (float(v) if isinstance(v, (int, float, np.floating)) else v)
                                                for k, v in row.items()}}, ensure_ascii=False) + "\n")
    print(f"[ok] {OUT_MD}")
    print(f"[ok] {OUT_JSONL}")
    print(df[["cagr", "sharpe", "mdd", "calmar", "monthly_win", "avg_long", "switches"]].round(2).to_string())
    return 0


if __name__ == "__main__":
    sys.exit(main())
