# -*- coding: utf-8 -*-
"""右侧交易探针：**站上前高（突破确认）之后再买**，值不值。

动机：v11 §7.1/§7.4 已测「到压力位之后 20/60 日内 73~75% / 89~90% 会突破」，
但**突破率 ≠ 突破后能赚** —— 压力位不是墙，大部分都会破；「破了才买」这个动作本身
能不能提供增量，是另一件事。本探针测的就是这件事。

事件定义（无前视）：
- 前高 `P` = **已确认**的摆动高点（`pivots` 的 high 列，滞后 `SWING_N=10` 日确认）；
- **首次穿越** = `close[i] > P[i]*(1+eps)` 且 `close[i-1] <= P[i-1]*(1+eps)`；
- 冷却 `COOL=20` 个交易日；成交按 **T+1 收盘净值**（与全家族一致）。

两个关键设计（否则结论会反）：
1. **必须控趋势**：突破天然发生在上涨趋势里（实测 66%），拿「全区间买入持有」当基准会把
   趋势的收益算到突破头上，看起来像 alpha。
2. **必须分组**：上涨趋势内的突破 ≈ v5 年线闸门已经覆盖的信息（v11 的支撑侧共线率是 82%）；
   真正的增量只可能在**趋势外的突破**里 —— 那才是 v5 没说过的部分。所以按 `in_trend` 拆两组，
   各组用**自己那组的日子**当基准（趋势日 / 非趋势日），不是拿同一套基准比两组。

产物：`_probe_breakout.md`（同目录）。机械规则输出，非投资建议。
"""
import os
import sys

import numpy as np
import pandas as pd

try:
    sys.stdout.reconfigure(encoding="utf-8")  # type: ignore[attr-defined]
except Exception:  # noqa: BLE001
    pass

_HERE = os.path.dirname(os.path.abspath(__file__))
_V11 = os.path.dirname(os.path.dirname(_HERE))          # research/probe_breakout -> v11
if _V11 not in sys.path:
    sys.path.insert(0, _V11)

import support_pressure as sp   # noqa: E402

OUT_MD = os.path.join(_HERE, "_probe_breakout.md")
COOL = 20
EPS_LIST = (0.0, 0.01, 0.02)
HS = (20, 60, 120)


def breakout_events(px, leg, eps=0.0, cool=COOL, **kw):
    """首次收盘站上已确认前高的独立事件（冷却 cool 日）。"""
    p = sp.params(**kw)
    c = px.astype("float64")
    _, high = sp.pivots(c, p["SWING_N"])
    piv = high.ffill()
    cc = c.to_numpy(dtype=float)
    pv = piv.to_numpy(dtype=float)
    up, _ = sp.trend_flags(c, **kw)
    in_up = up.to_numpy(dtype=bool)
    n = len(cc)
    rows, last = [], -10 ** 9
    for i in range(int(sp.WARMUP), n):
        if not np.isfinite(pv[i]) or pv[i] <= 0:
            continue
        if cc[i] <= pv[i] * (1.0 + eps):                       # 今天没站上
            continue
        prev_p = pv[i - 1] if np.isfinite(pv[i - 1]) else np.inf
        if cc[i - 1] > prev_p * (1.0 + eps):                   # 昨天已在上面 -> 不是首次穿越
            continue
        if i - last < cool:
            continue
        last = i
        seg = cc[i + 1:min(i + 21, n)]
        row = dict(leg=leg, name=sp.rule.LEGS[leg]["name"], i=int(i), date=px.index[i],
                   close=float(cc[i]), pivot_high=float(pv[i]), over=float(cc[i] / pv[i] - 1.0),
                   in_trend=bool(in_up[i]))
        row.update(sp._forward(cc, i, HS))
        row["hold20"] = bool(len(seg) >= 20 and seg.min() > pv[i])      # 20 日都站在前高上方
        row["back20"] = bool(len(seg) >= 20 and seg.min() < pv[i])      # 又跌回前高下方 = 假突破
        row["mdd20"] = float(seg.min() / cc[i] - 1.0) if len(seg) else np.nan
        row["mfe20"] = float(seg.max() / cc[i] - 1.0) if len(seg) else np.nan
        rows.append(row)
    return pd.DataFrame(rows)


def retest_events(px, leg, eps=0.0, band=0.02, wait=10, exit_eps=0.01, **kw):
    """突破 + 回踩不破：站上前高后**先走出回踩带**，再回踩到前高附近且收盘不破，才买。

    三种结局（都要数，只看买入组会有幸存者偏差）：
    - `retest`：回踩成立 -> 买点 = 回踩确认日（T+1 成交）；
    - `broke`：突破后收盘跌破前高 -> 作废（这类正是「等回踩」想过滤掉的假突破）；
    - `flew` ：`wait` 日内再也没回到前高附近 -> 作废（错过）。

    「先走出回踩带」这一步不能省：否则刚站上 0.3% 就满足 `close <= P*(1+band)`，
    等于变相「突破即买」，测的就不是回踩了。`exit_eps`（走出门槛）与 `band`（回踩宽度）
    **分开两个参数**：否则放宽 band 会同时让「走出」更难，买入数反而变少 —— 那就没法做 band 敏感了。
    """
    cc = px.astype("float64").to_numpy(dtype=float)
    n = len(cc)
    up, _ = sp.trend_flags(px.astype("float64"), **kw)
    in_up = up.to_numpy(dtype=bool)
    br = breakout_events(px, leg, eps=eps, **kw)
    rows = []
    for _, r in br.iterrows():
        i, piv = int(r["i"]), float(r["pivot_high"])
        edge = piv * (1.0 + band)
        leave = piv * (1.0 + exit_eps)
        left, hit, broke = False, None, False
        for j in range(i + 1, min(i + wait, n - 1) + 1):
            if not left:                       # 还没走出突破区，不算回踩
                if cc[j] > leave:
                    left = True
                continue
            if cc[j] < piv:                    # 收盘跌破前高 = 假突破
                broke = True
                break
            if cc[j] <= edge:
                hit = j
                break
        base = dict(leg=leg, name=sp.rule.LEGS[leg]["name"], pivot_high=piv,
                    break_date=px.index[i], break_close=float(cc[i]),
                    break_in_trend=bool(in_up[i]))
        if hit is None:
            rows.append(dict(base, kind="broke" if broke else "flew", wait=np.nan,
                             date=np.nan, close=np.nan, in_trend=False,
                             f20=np.nan, f60=np.nan, f120=np.nan,
                             hold20=np.nan, mdd20=np.nan))
            continue
        seg = cc[hit + 1:min(hit + 21, n)]
        row = dict(base, kind="retest", wait=int(hit - i), date=px.index[hit],
                   close=float(cc[hit]), in_trend=bool(in_up[hit]),
                   hold20=bool(len(seg) >= 20 and seg.min() > piv),
                   mdd20=float(seg.min() / cc[hit] - 1.0) if len(seg) else np.nan)
        row.update(sp._forward(cc, hit, HS))
        rows.append(row)
    return pd.DataFrame(rows)


def _mask_fwd(px, mask, hs=HS):
    """掩码日的前向收益均值 —— 分组基准（趋势内用趋势日、趋势外用非趋势日）。"""
    v = px.to_numpy(dtype=float)
    src = np.flatnonzero(np.asarray(mask, dtype=bool))
    src = src[src < len(v) - max(hs)]
    out = {"days": int(len(src))}
    for h in hs:
        out[f"b{h}"] = float(np.mean(v[src + int(h)] / v[src] - 1.0)) if len(src) else None
    return out


def _uncond(px, hs=HS):
    v = px.to_numpy(dtype=float)
    return {f"u{h}": float(np.mean(v[int(h):] / v[:-int(h)] - 1.0)) for h in hs}


def _wavg(rs, key, cnt="n"):
    sub = [r for r in rs if r.get(cnt) and r.get(key) is not None and np.isfinite(r[key])]
    tot = sum(r[cnt] for r in sub)
    return (sum(r[key] * r[cnt] for r in sub) / tot) if tot else None


def rows(eps=0.0, **kw):
    """逐腿汇总（基准 = 同腿上涨趋势日）。"""
    out = []
    for k in sp.LEGS:
        px = sp.load(k)
        ev = breakout_events(px, k, eps=eps, **kw)
        c = px.astype("float64")
        up, _ = sp.trend_flags(c, **kw)
        base = _mask_fwd(px, up.to_numpy(dtype=bool))
        uncon = _uncond(px)
        r = dict(leg=k, name=sp.rule.LEGS[k]["name"], n=len(ev),
                 base20=base["b20"], base60=base["b60"],
                 days=base["days"], days_all=max(len(px) - int(sp.WARMUP), 1),
                 u20=uncon["u20"], u60=uncon["u60"])
        if len(ev):
            r.update(f20=float(ev["f20"].mean()), f60=float(ev["f60"].mean()),
                     f120=float(ev["f120"].mean()),
                     w20=float((ev["f20"] > 0).mean()), w60=float((ev["f60"] > 0).mean()),
                     hold20=float(ev["hold20"].mean()), back20=float(ev["back20"].mean()),
                     mdd20=float(ev["mdd20"].mean()), mfe20=float(ev["mfe20"].mean()),
                     over=float(ev["over"].mean()), in_trend=float(ev["in_trend"].mean()))
            r["ex20"] = r["f20"] - r["base20"] if r["base20"] is not None else None
            r["ex60"] = r["f60"] - r["base60"] if r["base60"] is not None else None
        out.append(r)
    return out


def group_rows(eps=0.0, **kw):
    """按「事件是否在上涨趋势内」拆两组，各组用自己那组的日子当基准。"""
    evs, bases = {"in": [], "out": []}, {"in": [], "out": []}
    for k in sp.LEGS:
        px = sp.load(k)
        ev = breakout_events(px, k, eps=eps, **kw)
        if not len(ev):
            continue
        c = px.astype("float64")
        up, _ = sp.trend_flags(c, **kw)
        m = up.to_numpy(dtype=bool)
        bases["in"].append(_mask_fwd(px, m))
        bases["out"].append(_mask_fwd(px, ~m))
        for tag, sel in (("in", ev[ev["in_trend"]]), ("out", ev[~ev["in_trend"]])):
            if not len(sel):
                continue
            evs[tag].append(dict(n=len(sel), f20=float(sel["f20"].mean()),
                                 f60=float(sel["f60"].mean()),
                                 w20=float((sel["f20"] > 0).mean()),
                                 hold20=float(sel["hold20"].mean()),
                                 back20=float(sel["back20"].mean()),
                                 mdd20=float(sel["mdd20"].mean()),
                                 over=float(sel["over"].mean())))
    out = {}
    for tag in ("in", "out"):
        d = dict(n=sum(r["n"] for r in evs[tag]),
                 f20=_wavg(evs[tag], "f20"), f60=_wavg(evs[tag], "f60"),
                 w20=_wavg(evs[tag], "w20"), hold20=_wavg(evs[tag], "hold20"),
                 back20=_wavg(evs[tag], "back20"), mdd20=_wavg(evs[tag], "mdd20"),
                 over=_wavg(evs[tag], "over"),
                 b20=_wavg(bases[tag], "b20", "days"), b60=_wavg(bases[tag], "b60", "days"))
        d["ex20"] = (d["f20"] - d["b20"]) if (d["f20"] is not None and d["b20"] is not None) else None
        d["ex60"] = (d["f60"] - d["b60"]) if (d["f60"] is not None and d["b60"] is not None) else None
        out[tag] = d
    return out


def retest_rows(band=0.02, wait=10, exit_eps=0.01, **kw):
    """逐腿：突破总数 / 回踩买入 / 破位作废 / 飞走作废 + 买入组前向收益（基准同上）。"""
    out = []
    for k in sp.LEGS:
        px = sp.load(k)
        ev = retest_events(px, k, band=band, wait=wait, exit_eps=exit_eps, **kw)
        c = px.astype("float64")
        up, _ = sp.trend_flags(c, **kw)
        base = _mask_fwd(px, up.to_numpy(dtype=bool))
        rt = ev[ev["kind"] == "retest"]
        d = dict(leg=k, name=sp.rule.LEGS[k]["name"], n_break=len(ev), n_retest=len(rt),
                 n_broke=int((ev["kind"] == "broke").sum()),
                 n_flew=int((ev["kind"] == "flew").sum()),
                 days=base["days"], b20=base["b20"], b60=base["b60"])
        if len(rt):
            d.update(f20=float(rt["f20"].mean()), f60=float(rt["f60"].mean()),
                     w20=float((rt["f20"] > 0).mean()), w60=float((rt["f60"] > 0).mean()),
                     hold20=float(rt["hold20"].mean()), mdd20=float(rt["mdd20"].mean()),
                     wait=float(rt["wait"].mean()), in_trend=float(rt["in_trend"].mean()))
        out.append(d)
    return out


def _pool_rt(rs):
    """回踩组的池化：收益按**买入笔数**、基准按天数（回踩组没有 "n" 这个键）。"""
    F, B = "n_retest", "days"
    f20, f60 = _wavg(rs, "f20", F), _wavg(rs, "f60", F)
    b20, b60 = _wavg(rs, "b20", B), _wavg(rs, "b60", B)
    return dict(n_break=sum(r["n_break"] for r in rs), n_retest=sum(r["n_retest"] for r in rs),
                n_broke=sum(r["n_broke"] for r in rs), n_flew=sum(r["n_flew"] for r in rs),
                f20=f20, f60=f60,
                ex20=(f20 - b20) if (f20 is not None and b20 is not None) else None,
                ex60=(f60 - b60) if (f60 is not None and b60 is not None) else None,
                w20=_wavg(rs, "w20", F), w60=_wavg(rs, "w60", F),
                hold20=_wavg(rs, "hold20", F), mdd20=_wavg(rs, "mdd20", F),
                wait=_wavg(rs, "wait", F), in_trend=_wavg(rs, "in_trend", F))


def fp(x, nd=0):
    return "—" if x is None or not np.isfinite(x) else f"{x * 100:.{nd}f}%"


def fs(x, nd=2):
    return "—" if x is None or not np.isfinite(x) else f"{x * 100:+.{nd}f}%"


def main():
    main_rows = rows(0.0)
    pool = dict(n=sum(r["n"] for r in main_rows),
                f20=_wavg(main_rows, "f20"), f60=_wavg(main_rows, "f60"),
                f120=_wavg(main_rows, "f120"),
                w20=_wavg(main_rows, "w20"), w60=_wavg(main_rows, "w60"),
                hold20=_wavg(main_rows, "hold20"), back20=_wavg(main_rows, "back20"),
                mdd20=_wavg(main_rows, "mdd20"), mfe20=_wavg(main_rows, "mfe20"),
                over=_wavg(main_rows, "over"), in_trend=_wavg(main_rows, "in_trend"),
                # 基准按**天数**加权（与事件数无关），否则 A500/沪深300 这类事件多的腿会盖住长样本腿
                base20=_wavg(main_rows, "base20", "days"), base60=_wavg(main_rows, "base60", "days"),
                u20=_wavg(main_rows, "u20", "days_all"), u60=_wavg(main_rows, "u60", "days_all"))
    pool["ex20"] = pool["f20"] - pool["base20"]
    pool["ex60"] = pool["f60"] - pool["base60"]
    pool["ew20"] = pool["f20"] - pool["u20"]
    pool["ew60"] = pool["f60"] - pool["u60"]

    sens = []
    for eps in EPS_LIST:
        rs = rows(eps)
        g = group_rows(eps)
        # 超额 = 事件组(按事件数) − 基准组(按天数)；与 §3 池化同一种加总，不能改成「先算每腿超额再平均」
        _f20, _f60 = _wavg(rs, "f20"), _wavg(rs, "f60")
        _b20, _b60 = _wavg(rs, "base20", "days"), _wavg(rs, "base60", "days")
        sens.append(dict(eps=eps, n=sum(r["n"] for r in rs), f20=_f20, f60=_f60,
                         ex20=_f20 - _b20, ex60=_f60 - _b60,
                         hold20=_wavg(rs, "hold20"), back20=_wavg(rs, "back20"),
                         mdd20=_wavg(rs, "mdd20"),
                         g_in=g["in"]["ex60"], g_out=g["out"]["ex60"]))

    L = [
        "# 右侧交易探针：站上前高再买（突破确认）",
        "",
        "> 由 `_probe_breakout.py` 生成。口径与 v11 同源（`pivots` 前高、滞后 10 日确认、无前视、T+1 成交）。",
        "",
        "## 1. 事件定义",
        "",
        "- 前高 `P` = **已确认**的摆动高点（21 日窗口极值，滞后 10 个交易日确认）；",
        "- **首次穿越** = `close > P`（今日）且 `close[-1] <= P`（昨日在下方），冷却 20 个交易日；",
        "- 站上即买、T+1 收盘成交，持有 20 / 60 / 120 个交易日；",
        "- 三套基准：**同腿上涨趋势日**（控趋势）、**同腿非趋势日**（分组用）、**全区间**（买着不动）。",
        "",
        "## 2. 逐腿结果（eps = 0，纯「收盘越过高点」）",
        "",
        "| 腿 | 事件 | 平均越过 | f20 | f60 | f120 | 20日胜率 | 60日胜率 | 站稳20日 | 假突破 | "
        "20日最大回撤 | 20日均涨 | 趋势内 | 超额f60（控趋势） |",
        "|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|",
    ]
    for r in main_rows:
        if not r["n"]:
            L.append(f"| {r['name']} | 0 | — | — | — | — | — | — | — | — | — | — | — | — |")
            continue
        L.append(f"| {r['name']} | {r['n']} | {fs(r['over'])} | {fs(r['f20'])} | {fs(r['f60'])} | "
                 f"{fs(r['f120'])} | {fp(r['w20'])} | {fp(r['w60'])} | {fp(r['hold20'])} | "
                 f"{fp(r['back20'])} | {fs(r['mdd20'])} | {fs(r['mfe20'])} | {fp(r['in_trend'])} | "
                 f"{fs(r.get('ex60'))} |")

    g = group_rows(0.0)
    L += [
        "",
        "## 3. 池化：突破 vs 三套基准",
        "",
        "| 口径 | 样本 | f20 | f60 | 20日胜率 | 60日胜率 |",
        "|---|---:|---:|---:|---:|---:|",
        f"| **站上前高（eps=0）** | {pool['n']} 笔 | {fs(pool['f20'])} | {fs(pool['f60'])} | "
        f"{fp(pool['w20'])} | {fp(pool['w60'])} |",
        f"| 上涨趋势日（控趋势） | — | {fs(pool['base20'])} | {fs(pool['base60'])} | — | — |",
        f"| 全区间（买着不动） | — | {fs(pool['u20'])} | {fs(pool['u60'])} | — | — |",
        f"| **超额 vs 趋势日** | — | **{fs(pool['ex20'])}** | **{fs(pool['ex60'])}** | — | — |",
        f"| 超额 vs 全区间 | — | {fs(pool['ew20'])} | {fs(pool['ew60'])} | — | — |",
        "",
        f"站稳率 {fp(pool['hold20'])}、假突破率 {fp(pool['back20'])}、20 日平均最大回撤 "
        f"{fs(pool['mdd20'])}、20 日平均最大涨幅 {fs(pool['mfe20'])}。",
        "**注意第二行与第四行**：绝对收益为正（+f60）几乎全部来自「上涨趋势日本来就涨」，"
        "控掉趋势后超额是负的 —— 这就是只看绝对收益会得到的错结论。",
        "",
        "## 4. 关键分组：突破发生在「上涨趋势内」还是「趋势外」",
        "",
        "趋势内的突破 ≈ v5 年线闸门已经覆盖的信息；真正的增量只可能在**趋势外突破**里。"
        "两组各用自己那组的日子当基准（趋势日 / 非趋势日），不能拿同一套基准比两组。",
        "",
        "| 组 | 事件 | 平均越过前高 | f20 | f60 | 基准f20 | 基准f60 | **超额f20** | **超额f60** | "
        "20日胜率 | 站稳20日 | 假突破 | 20日最大回撤 |",
        "|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|",
    ]
    for tag, label in (("in", "上涨趋势内"), ("out", "趋势外（非上涨趋势）")):
        d = g[tag]
        L.append(f"| {label} | {d['n']} | {fs(d.get('over'))} | {fs(d['f20'])} | {fs(d['f60'])} | "
                 f"{fs(d['b20'])} | {fs(d['b60'])} | **{fs(d['ex20'])}** | **{fs(d['ex60'])}** | "
                 f"{fp(d['w20'])} | {fp(d['hold20'])} | {fp(d['back20'])} | {fs(d['mdd20'])} |")

    L += [
        "",
        "## 5. 突破缓冲敏感（eps = 0 / 1% / 2%）",
        "",
        "| eps | 事件 | f20 | f60 | 超额f20 | 超额f60 | 趋势内超额f60 | 趋势外超额f60 | "
        "站稳20日 | 假突破 | 20日最大回撤 |",
        "|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|",
    ]
    for s in sens:
        L.append(f"| {s['eps'] * 100:.0f}% | {s['n']} | {fs(s['f20'])} | {fs(s['f60'])} | "
                 f"{fs(s['ex20'])} | {fs(s['ex60'])} | {fs(s['g_in'])} | {fs(s['g_out'])} | "
                 f"{fp(s['hold20'])} | {fp(s['back20'])} | {fs(s['mdd20'])} |")

    L += ["", "## 6. 结论", ""]
    out_ex, in_ex = g["out"]["ex60"], g["in"]["ex60"]
    L.append(
        f"> **1. 绝对值能赚，但那是趋势的功劳。** {pool['n']} 笔突破后 60 日平均 "
        f"{fs(pool['f60'])}，看起来不错；但同样「处在上涨趋势里的普通一天」就有 "
        f"{fs(pool['base60'])}，**控掉趋势后超额 {fs(pool['ex60'])}** —— 突破确认在这批腿上是"
        + ("负增量。" if (pool["ex60"] is not None and pool["ex60"] < 0) else "接近零的增量。"))
    L.append(
        f"> **2. 分组把答案说清楚了**：趋势内 {g['in']['n']} 笔、超额 **{fs(in_ex)}** —— "
        "这正是 v5 年线闸门已经覆盖的时段，等于噪声；"
        f"趋势外 {g['out']['n']} 笔、超额 **{fs(out_ex)}**，"
        + ("同样在噪声内。**右侧交易所谓的「确认」，在剔除趋势后什么都没剩。**"
           if (out_ex is not None and abs(out_ex) <= 0.005) else
           "这是唯一值得继续挖的口子（v5 覆盖不到的那部分）。"))
    L.append(
        f"> **3. 加缓冲主要是把样本筛小，不是把信号变强**：eps 0→2% 时事件 "
        f"{sens[0]['n']}→{sens[-1]['n']}、总超额f60 {fs(sens[0]['ex60'])}→{fs(sens[-1]['ex60'])}"
        "（几乎没动）；唯一变好的是趋势外那一格 "
        f"{fs(sens[0]['g_out'])}→{fs(sens[-1]['g_out'])}，但它是 206 笔里约 70 笔的子集 —— "
        "样本一小，这种量级的差异随时会翻号，不能当信号。")
    L.append(
        f"> **4. 执行上最难的不是信号而是回撤**：20 日平均最大回撤 {fs(pool['mdd20'])}、"
        f"假突破率 {fp(pool['back20'])}（20 日内又跌回前高下方）、站稳率只有 {fp(pool['hold20'])}。"
        "「站上前高才买」在三分之二的样本里，买入后 20 日内又跌回前高下方 —— 拿不住。")
    L.append(
        "> **5. 为什么「60 日突破率 89~90%」和「突破后没有超额」不矛盾**：突破是**常态事件**，"
        "不是一个稀缺信息。v11 §7.1 测的是「到压力位之后会不会破」，答案是「几乎总会破」；"
        "既然总会破，那「破了才买」就没有筛掉任何东西 —— 它只是把「买」延后了几天，"
        "而延后的这几天正是趋势已经涨过的那几天。")
    L.append("> 机械规则输出，非投资建议。")

    # ---- §8 突破 + 回踩不破
    rt_w10 = _pool_rt(retest_rows(0.02, 10))
    rt_sens = [(_pool_rt(retest_rows(0.02, w)), w) for w in (5, 10, 20)]
    rt_band = [(_pool_rt(retest_rows(b, 10)), b) for b in (0.01, 0.02, 0.03)]
    rt_rows = retest_rows(0.02, 10)

    L += [
        "",
        "## 7. 还能怎么试（以及为什么先验不高）",
        "",
        "| 方案 | 逻辑 | 先验 | 数据可行性 |",
        "|---|---|---|---|",
        f"| 突破后**回踩不破**再买（双条件） | 突破 + 回踩确认，把「追高」变成「站稳」 | "
        f"{'低' if (rt_w10['ex60'] is not None and rt_w10['ex60'] <= 0.005) else '中'} —— 实测见 §8 | **已测（§8）** |",
        "| 把突破当**过滤**而非触发（突破后 N 日内才允许买） | 与 v5 的趋势闸门同构 | 低 —— 与 v5 共线 66~82% | 可做 |",
        "| 突破时**量能/广度**确认（放量突破） | 这才是「真突破 vs 假突破」的常见判据 | 中 | **做不了** —— 本地只有收盘价，无成交量/OHLCV |",
        "| 突破后**波动率**压缩再买（低波突破） | 低波环境下的突破更少假信号 | 中低 | 可做（有 ATR） |",
        "",
        "> 共同先验：只要「突破」本身是 90% 会发生的事，任何**以突破为触发**的设计都先天缺信息。"
        "要做右侧，必须叠加一个**不常发生**的条件（放量、低波、突破前盘整时长），"
        "而本地数据缺成交量这一项 —— 这也是这题目前的硬边界。",
        "",
        "## 8. 突破 + 回踩不破（band=2%，wait=10 日）",
        "",
        "「站上前高就买」的问题是追高。回踩版把买点挪到**突破后先走出回踩带、再回踩到前高附近"
        "且收盘不破**的那一天 —— 逻辑上是在过滤假突破，代价是错过直接飞走的那部分。"
        "三种结局都要数，只看买入组会有幸存者偏差：",
        "",
        "| 结局 | 笔数 | 占比 |",
        "|---|---:|---:|",
        f"| **回踩成立（买入）** | {rt_w10['n_retest']} | "
        f"{fp(rt_w10['n_retest'] / max(rt_w10['n_break'], 1))} |",
        f"| 回踩但跌破前高（作废） | {rt_w10['n_broke']} | "
        f"{fp(rt_w10['n_broke'] / max(rt_w10['n_break'], 1))} |",
        f"| 飞走没回踩（作废） | {rt_w10['n_flew']} | "
        f"{fp(rt_w10['n_flew'] / max(rt_w10['n_break'], 1))} |",
        f"| 突破总数 | {rt_w10['n_break']} | 100% |",
        "",
        "| 口径 | 样本 | f20 | f60 | 超额f20 | 超额f60 | 20日胜率 | 站稳20日 | 20日最大回撤 |",
        "|---|---:|---:|---:|---:|---:|---:|---:|---:|",
        f"| 突破即买（§2） | {pool['n']} | {fs(pool['f20'])} | {fs(pool['f60'])} | "
        f"{fs(pool['ex20'])} | {fs(pool['ex60'])} | {fp(pool['w20'])} | {fp(pool['hold20'])} | "
        f"{fs(pool['mdd20'])} |",
        f"| **回踩不破才买** | {rt_w10['n_retest']} | {fs(rt_w10['f20'])} | {fs(rt_w10['f60'])} | "
        f"{fs(rt_w10['ex20'])} | {fs(rt_w10['ex60'])} | {fp(rt_w10['w20'])} | "
        f"{fp(rt_w10['hold20'])} | {fs(rt_w10['mdd20'])} |",
        "",
        "等回踩的窗口长度（band 固定 2%）：",
        "",
        "| wait | 买入 | 作废·破 | 作废·飞 | f20 | f60 | 超额f20 | 超额f60 | 站稳20日 | 20日最大回撤 |",
        "|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|",
    ]
    for p_, w in rt_sens:
        L.append(f"| {w} 日 | {p_['n_retest']} | {p_['n_broke']} | {p_['n_flew']} | "
                 f"{fs(p_['f20'])} | {fs(p_['f60'])} | {fs(p_['ex20'])} | {fs(p_['ex60'])} | "
                 f"{fp(p_['hold20'])} | {fs(p_['mdd20'])} |")
    L += [
        "",
        "回踩带宽度（wait 固定 10 日）：",
        "",
        "| band | 买入 | 作废·破 | f20 | f60 | 超额f60 | 站稳20日 |",
        "|---|---:|---:|---:|---:|---:|---:|",
    ]
    for p_, b in rt_band:
        L.append(f"| ±{b * 100:.0f}% | {p_['n_retest']} | {p_['n_broke']} | {fs(p_['f20'])} | "
                 f"{fs(p_['f60'])} | {fs(p_['ex60'])} | {fp(p_['hold20'])} |")
    L += [
        "",
        "逐腿（band=2%、wait=10）：",
        "",
        "| 腿 | 突破 | 买入 | 作废·破 | 作废·飞 | f20 | f60 | 超额f60 | 站稳20日 | 买入时在趋势内 |",
        "|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|",
    ]
    for r in rt_rows:
        L.append(f"| {r['name']} | {r['n_break']} | {r['n_retest']} | {r['n_broke']} | "
                 f"{r['n_flew']} | {fs(r.get('f20'))} | {fs(r.get('f60'))} | "
                 f"{fs((r['f60'] - r['b60']) if (r.get('f60') is not None and r.get('b60') is not None) else None)} | "
                 f"{fp(r.get('hold20'))} | {fp(r.get('in_trend'))} |")

    _rt_ok = (rt_w10["ex60"] is not None and rt_w10["ex60"] > 0.005)
    L += [
        "",
        f"> **等回踩的结果**：{rt_w10['n_break']} 次突破里只有 {rt_w10['n_retest']} 次"
        f"（{fp(rt_w10['n_retest'] / max(rt_w10['n_break'], 1))}）能在 10 日内回踩到 ±2% 且不破，"
        f"另有 {rt_w10['n_broke']} 次是「回踩即破」（这批本来会被买在假突破上，过滤掉了）、"
        f"{rt_w10['n_flew']} 次直接飞走（错过了）。买入组的 60 日超额 "
        f"**{fs(rt_w10['ex60'])}**（突破即买是 {fs(pool['ex60'])}）。"
        + ("**回踩确认把负超额修回正的** —— 这是本探针唯一一次出现正增量。"
           if _rt_ok else
           "**比「突破即买」更差**。原因不是「错过飞走」，而是回踩把买点挪到了突破已经回落之后 —— "
           "等于**贴着前高买**，而 v11 §3 早就测过「贴线买」是负增量（f60 从 +3.64% 掉到 +2.98%）。"
           "同一个坑，从下面绕上去还是掉进去。"),
        (f"> 回踩唯一的正面作用是**拿得住**：站稳率 "
         f"{fp(rt_w10['hold20'])} vs 突破即买 {fp(pool['hold20'])}、假突破更少；"
         f"但代价是收益更低（f60 {fs(rt_w10['f60'])} vs {fs(pool['f60'])}），"
         "而策略要的是收益，不是「买完不难受」。"),
        f"> 窗口与带宽的敏感：wait 5/10/20 日、band 1%/2%/3% 各档，超额f60 在 "
        f"{fs(min((p_['ex60'] for p_, _ in rt_sens + rt_band if p_['ex60'] is not None), default=None))}"
        f" ~ {fs(max((p_['ex60'] for p_, _ in rt_sens + rt_band if p_['ex60'] is not None), default=None))} 之间——"
        "参数不是救命稻草。",
        "",
        "> 机械规则输出，非投资建议。",
    ]

    with open(OUT_MD, "w", encoding="utf-8") as f:
        f.write("\n".join(L) + "\n")

    print("报告:", OUT_MD)
    print(f"总 {pool['n']} 笔 | f20 {fs(pool['f20'])} f60 {fs(pool['f60'])} | "
          f"超额vs趋势日 f20 {fs(pool['ex20'])} f60 {fs(pool['ex60'])} | "
          f"超额vs全区间 f20 {fs(pool['ew20'])} f60 {fs(pool['ew60'])}")
    print(f"站稳 {fp(pool['hold20'])} 假突破 {fp(pool['back20'])} mdd20 {fs(pool['mdd20'])} "
          f"趋势内占比 {fp(pool['in_trend'])}")
    print(f"分组: 趋势内 n={g['in']['n']} ex60 {fs(g['in']['ex60'])} | "
          f"趋势外 n={g['out']['n']} ex60 {fs(g['out']['ex60'])}")
    for s in sens:
        print(f"  eps={s['eps']:.0%}: n={s['n']} f60 {fs(s['f60'])} ex60 {fs(s['ex60'])} "
              f"趋势内 {fs(s['g_in'])} 趋势外 {fs(s['g_out'])} 站稳 {fp(s['hold20'])}")
    print(f"回踩不破（band2%/wait10）: 突破 {rt_w10['n_break']} -> 买入 {rt_w10['n_retest']} "
          f"/ 破 {rt_w10['n_broke']} / 飞 {rt_w10['n_flew']} | f60 {fs(rt_w10['f60'])} "
          f"超额f60 {fs(rt_w10['ex60'])} 站稳 {fp(rt_w10['hold20'])}")
    for p_, w in rt_sens:
        print(f"  wait={w:2d}: 买入 {p_['n_retest']:3d} ex60 {fs(p_['ex60'])} 站稳 {fp(p_['hold20'])}")
    for p_, b in rt_band:
        print(f"  band={b:.0%}: 买入 {p_['n_retest']:3d} ex60 {fs(p_['ex60'])}")


if __name__ == "__main__":
    main()
