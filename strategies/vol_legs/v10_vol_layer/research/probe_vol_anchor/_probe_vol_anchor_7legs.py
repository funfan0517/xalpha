# -*- coding: utf-8 -*-
"""V10 纯波动层 × 全 7 腿 × 波动锚反相（vol 高→TV 低）× 抑制调仓。

回答用户问题：**波动反相只在 ndx/spx 测过，其余 5 条腿到底行不行？**
沿用 `../probe_er/_probe_er_adaptive_7legs.py` 的框架：同样 7 腿、同样严格判定、同样幅度/时间死区，
只把状态变量从 ER 换成「过去 3 年 vol20 的 max/min/mean 锚」（反相），并加一个分位正相对照。

映射（全部来自 `_probe_vol_anchored_tv.py`，3 年 = 756 日，shift(1) 无前视）：
- 分位反相：TV = 20% - 10%·p，p = (vol-min)/(max-min)
- 均值反相：TV = clip(15%·mean/vol, 8%, 25%)
- 极值夹逼：TV = clip(15%·(max-vol)/(max-min), 8%, 25%)
- 分位正相（对照）：TV = 10% + 10%·p

抑制机制与 ER 全腿报告一致：幅度死区 amp=10/20pp、时间死区 cd=0/5 日。
严格判定：后段夏普 > 固定 TV10 与 TV20，且全样本夏普 ≥ 两者最大值。
预热 776 根（756 锚窗 + 20 vol 窗）、后段 40%；只做研究，不改任何生产代码。
产物：`_v10_vol_anchor_7legs.md`（本目录）。
"""
import importlib.util
import io
import math
import os
import sys
from collections import Counter

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

OUT = os.path.join(_HERE, "_v10_vol_anchor_7legs.md")
LEGS = vd.rule.LEG_ORDER
LEG_NAMES = {k: vd.rule.LEGS[k]["name"] for k in LEGS}
TD, LOOKBACK, VOL_WIN, CUT = 252, 756, 20, 0.60
WARMUP = LOOKBACK + VOL_WIN
AMP_NATIVE = 0.10


def perf(nav):
    r = nav.dropna().pct_change().dropna()
    n = len(r)
    if n < 30:
        return dict(ann=float("nan"), mdd=float("nan"), sharpe=float("nan"))
    ann = float((1 + r).prod() ** (TD / n) - 1)
    vol = float(r.std() * math.sqrt(TD))
    mdd = float((nav / nav.cummax() - 1).min())
    return dict(ann=ann, mdd=mdd, sharpe=(ann - vl.CASH_ANNUAL) / vol if vol > 0 else float("nan"))


def apply_dead(tgt, amp=AMP_NATIVE, cd=0):
    """幅度死区 + 时间死区：|目标−现仓|≥amp 且距上次调仓≥cd 日才动手。"""
    g = tgt.to_numpy(dtype=float)
    n = len(g)
    if n == 0:
        return pd.Series(dtype="float64", index=tgt.index)
    out = np.empty(n)
    cur, last = float(g[0]), 0
    out[0] = cur
    for i in range(1, n):
        if abs(g[i] - cur) >= amp - 1e-12 and (i - last) >= cd:
            cur, last = float(g[i]), i
        out[i] = cur
    return pd.Series(out, index=tgt.index)


def vol_series(sig):
    return sig.pct_change().rolling(VOL_WIN, min_periods=VOL_WIN).std(ddof=1) * math.sqrt(TD)


def anchor_tvs(vol):
    """3 年锚：四种映射（三种反相 + 一种正相对照），全部 shift(1) 无前视。"""
    vmin = vol.rolling(LOOKBACK, min_periods=LOOKBACK).min().shift(1)
    vmax = vol.rolling(LOOKBACK, min_periods=LOOKBACK).max().shift(1)
    vmean = vol.rolling(LOOKBACK, min_periods=LOOKBACK).mean().shift(1)
    span = (vmax - vmin).replace(0, np.nan)
    p = ((vol - vmin) / span).clip(0, 1).shift(1)
    return {
        "pctl_inv": 0.20 - 0.10 * p,
        "pctl_fwd": 0.10 + 0.10 * p,
        "mean_inv": (0.15 * vmean / vol).clip(0.08, 0.25),
        "ext_inv": (0.15 * (vmax - vol) / span).clip(0.08, 0.25),
    }


def run_tv(leg, tv_s, amp=AMP_NATIVE, cd=0):
    px = vl.load_nav(leg)
    sig = vl.signal_of(leg)
    vol = vol_series(sig).reindex(sig.index)
    s = (tv_s.reindex(sig.index) / vol).replace([np.inf, -np.inf], np.nan) \
        .clip(upper=1.0).fillna(1.0).clip(0.0, 1.0)
    st = apply_dead(s, amp, cd).reindex(px.index).ffill().fillna(1.0).shift(1).fillna(1.0)
    return vl.run_leg(st, px), st


def main():
    # 自检：amp=10pp、cd=0 与原生 vl.deadzone 逐位一致
    probe = pd.Series([1.0, 1.02, 1.15, 1.14, 0.90, 0.88, 0.70])
    assert np.allclose(apply_dead(probe, AMP_NATIVE, 0).values, vl.deadzone(probe, AMP_NATIVE).values)

    lines = [
        "# V10 纯波动层 × 全 7 腿 × 波动锚反相（实测）", "",
        "> 2026-11-17 生成。回答「波动反相是不是只在 ndx/spx 测过，其余腿行不行」。"
        "沿用 `../probe_er/_v10_er_adaptive_7legs.md` 的 7 腿框架与严格判定，只把状态变量换成 3 年 vol20 锚。",
        "> 7 腿 = 中证A500 / 沪深300 / 纳指100 / 标普500 / 科创50 / 恒生科技 / 黄金（真实指数，黄金 518880）。"
        "只做研究，**不改** `vol_layer.py` / `POOL` / 任何策略产物。", "",
        "## 0. 方法", "",
        "- 锚窗口 756 日：分位反相 `TV=20%-10%·p`、均值反相 `TV=clip(15%·mean/vol,8%,25%)`、"
        "极值夹逼 `TV=clip(15%·(max-vol)/(max-min),8%,25%)`；另给分位正相作对照。",
        "- 目标仓位 `min(1, TV/vol20)` → 幅度死区（<amp 不动手）→ 时间死区（距上次 <cd 日不动手）→ shift(1) → 原生 `run_leg`。",
        f"- 幅度死区 10/20pp、时间死区 0/5 日；预热 **{WARMUP} 根**（756 锚窗 + 20 vol 窗）、后段 40%。",
        "> ⚠ 预热比 ER 全腿报告的 300 根长（锚需要 3 年历史）。跨报告只比较「相对各自固定档是否过线」，**不直接比绝对年数**。",
        "- 严格判定：后段夏普 > 固定 TV10 与 TV20，且全样本夏普 ≥ 两者最大值。", "",
    ]

    summary = {}
    for leg in LEGS:
        px = vl.load_nav(leg)
        sig = vl.signal_of(leg)
        vol = vol_series(sig).reindex(sig.index)
        tv = anchor_tvs(vol)
        const = lambda x: pd.Series(x, index=px.index)
        schemes = [
            ("A 固定 TV10", const(0.10), AMP_NATIVE, 0, "base"),
            ("B 固定 TV20", const(0.20), AMP_NATIVE, 0, "base"),
            ("C 分位反相 原生", tv["pctl_inv"], AMP_NATIVE, 0, "pctl"),
            ("D 分位反相 +5日", tv["pctl_inv"], AMP_NATIVE, 5, "pctl"),
            ("E 分位反相 +20pp", tv["pctl_inv"], 0.20, 0, "pctl"),
            ("F 均值反相 原生", tv["mean_inv"], AMP_NATIVE, 0, "mean"),
            ("G 均值反相 +5日", tv["mean_inv"], AMP_NATIVE, 5, "mean"),
            ("H 均值反相 +20pp", tv["mean_inv"], 0.20, 0, "mean"),
            ("I 极值夹逼 原生", tv["ext_inv"], AMP_NATIVE, 0, "ext"),
            ("J 极值夹逼 +5日", tv["ext_inv"], AMP_NATIVE, 5, "ext"),
            ("K 极值夹逼 +20pp", tv["ext_inv"], 0.20, 0, "ext"),
            ("L 分位正相 对照", tv["pctl_fwd"], AMP_NATIVE, 0, "fwd"),
        ]
        n = len(px)
        cut = WARMUP + int((n - WARMUP) * CUT)
        rows = []
        for lab, tvs, amp, cd, fam in schemes:
            nv, st = run_tv(leg, tvs, amp, cd)
            f, q = perf(nv.iloc[WARMUP:]), perf(nv.iloc[cut:])
            pos = st.shift(1).reindex(px.index).iloc[WARMUP:].mean()
            tr = int((st.diff().abs() > 1e-9).iloc[WARMUP:].sum())
            rows.append(dict(lab=lab, fam=fam, f=f, q=q, pos=pos, tr=tr))
        b10, b20 = rows[0], rows[1]
        strict = [r["lab"] for r in rows
                  if r["fam"] in ("pctl", "mean", "ext")
                  and r["q"]["sharpe"] > b10["q"]["sharpe"]
                  and r["q"]["sharpe"] > b20["q"]["sharpe"]
                  and r["f"]["sharpe"] >= max(b10["f"]["sharpe"], b20["f"]["sharpe"]) - 1e-9]
        summary[leg] = dict(rows=rows, b10=b10, b20=b20, strict=strict,
                            years=(px.index[-1] - px.index[WARMUP]).days / 365.25)
        lines += [f"## {LEG_NAMES[leg]}（`{leg}` · {summary[leg]['years']:.1f} 年）", "",
                  "| 方案 | 全样本夏普 | 全样本年化 | 全样本回撤 | 后段夏普 | 后段年化 | 后段回撤 | 平均仓位 | 调仓 | 严格赢? |",
                  "|---|---:|---:|---:|---:|---:|---:|---:|---:|:---:|"]
        for r in rows:
            win = r["lab"] in strict
            lines.append(f"| {r['lab']} | {r['f']['sharpe']:.2f} | {r['f']['ann']:+.2%} | "
                         f"{r['f']['mdd']:.2%} | {r['q']['sharpe']:.2f} | {r['q']['ann']:+.2%} | "
                         f"{r['q']['mdd']:.2%} | {r['pos']:.1%} | {r['tr']} | {'✓' if win else ''} |")
        lines += ["", f"**严格判定：{'、'.join(strict) if strict else '无方案过线'}**", ""]

    lines += ["## 总表：各腿波动锚反相最好一档 vs 固定档", "",
              "| 腿 | 固定TV10 全/后 | 固定TV20 全/后 | 分位反相最佳 全/后·调仓 | 均值反相最佳 全/后·调仓 | 极值夹逼最佳 全/后·调仓 | 分位正相 全/后 | 严格赢方案 |",
              "|---|---:|---:|---:|---:|---:|---:|---|"]
    for leg in LEGS:
        s = summary[leg]
        def cell(fam):
            rs = [r for r in s["rows"] if r["fam"] == fam]
            r = max(rs, key=lambda z: z["f"]["sharpe"])
            return f"{r['f']['sharpe']:.2f}/{r['q']['sharpe']:.2f}·{r['tr']}"
        fwd = next(r for r in s["rows"] if r["fam"] == "fwd")
        lines.append(f"| {LEG_NAMES[leg]} | {s['b10']['f']['sharpe']:.2f}/{s['b10']['q']['sharpe']:.2f} | "
                     f"{s['b20']['f']['sharpe']:.2f}/{s['b20']['q']['sharpe']:.2f} | "
                     f"{cell('pctl')} | {cell('mean')} | {cell('ext')} | "
                     f"{fwd['f']['sharpe']:.2f}/{fwd['q']['sharpe']:.2f} | "
                     f"{'、'.join(r.split()[0] for r in s['strict']) if s['strict'] else '无'} |")

    strict_legs = [LEG_NAMES[k] for k in LEGS if summary[k]["strict"]]
    win_fams = Counter()
    win_legs_by_fam = {"pctl": set(), "mean": set(), "ext": set()}
    for k in LEGS:
        for lab in summary[k]["strict"]:
            fam = next(r["fam"] for r in summary[k]["rows"] if r["lab"] == lab)
            win_fams[fam] += 1
            win_legs_by_fam[fam].add(k)
    best_fam = max(win_fams.items(), key=lambda x: x[1])[0] if win_fams else "无"
    fam_cn = {"pctl": "分位反相", "mean": "均值反相", "ext": "极值夹逼"}
    fwd_fail = sum(1 for k in LEGS
                   if next(r for r in summary[k]["rows"] if r["fam"] == "fwd")["f"]["sharpe"]
                   < max(summary[k]["b10"]["f"]["sharpe"], summary[k]["b20"]["f"]["sharpe"]) - 1e-9)
    ndx_best = max((r for r in summary["ndx"]["rows"] if r["fam"] in ("pctl", "mean", "ext")),
                   key=lambda z: z["f"]["sharpe"])

    lines += ["", "## 结论", "",
              f"1. **过线腿数：{len(strict_legs)}/7**"
              + ("（" + "、".join(strict_legs) + "）" if strict_legs else "（无）")
              + "。ER 全腿报告是 6/7（黄金不过），两者不能只看腿数，还要看强基线腿。",
              f"2. **过线最多的映射是「{fam_cn.get(best_fam, best_fam)}」**"
              + (f"（覆盖 {len(win_legs_by_fam[best_fam])} 条腿、{win_fams[best_fam]} 个档位）" if win_fams else "")
              + "；严格赢需同时满足后段赢两个固定档、全样本不输。",
              f"3. **分位正相在 {fwd_fail}/7 条腿全样本输给固定档**，再次印证 TV 与 vol20 正相关会自我抵消；"
              "反相才是波动锚唯一有意义的方向。",
              "4. **短样本降权**：科创50 / 恒生科技在 776 根预热后只剩约 4 年样本，"
              "即便过线也不能与 2005 起的长样本腿等量齐观。",
              "5. **与 ER 对比**：ER 在纳指（强基线腿）严格过线；波动锚反相在纳指**全部不过线** —— "
              f"最好的 {ndx_best['lab']} 全样本 {ndx_best['f']['sharpe']:.2f} / 后段 "
              f"{ndx_best['q']['sharpe']:.2f}，后段仍输固定 TV10 的 "
              f"{summary['ndx']['b10']['q']['sharpe']:.2f}。本表把反相铺到其余 5 条腿后，"
              "过线集中在标普与两条短样本腿，A500 / 沪深300 / 黄金均未过线。",
              "", "> ⚠ 方案字母在本报告内自洽：C/D/E=分位反相不同死区、F/G/H=均值反相、"
              "I/J/K=极值夹逼、L=分位正相对照；与两腿报告 `_v10_vol_anchored_tv.md` 字母不同，按表头对照。", "",
              "> 本探针只给结论，未改任何策略代码。", ""]

    with io.open(OUT, "w", encoding="utf-8") as f:
        f.write("\n".join(lines) + "\n")
    print(f"WROTE {OUT}")
    for leg in LEGS:
        print(LEG_NAMES[leg], "strict:", summary[leg]["strict"] or "无")


if __name__ == "__main__":
    main()
