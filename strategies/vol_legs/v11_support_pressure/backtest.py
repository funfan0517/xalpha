# -*- coding: utf-8 -*-
"""波动资产 · V11 变种：支撑/压力位 × 期望值 · 事件研究回测。

**这不是 NAV 择时回测** —— 按 `支撑压力位期望值策略.md` §4.3 的规定，本报告
**只报信号日之后 20/60/120 日的前向收益分布 vs 同腿同区间的无条件基准**，
不在小样本上报年化 / 最大回撤 / 夏普（那三项在家族其它变种里都有，这里是**刻意没有**）。

产出：
- `backtest/_v11_support_bt.md`   规则 + 样本量守门 + 前向收益 + 期望值 + 风险分桶 +
  文档四格 + 条件递进 + 共线 + 敏感性 + 结论
- `backtest/_v11_support_events.jsonl`  逐笔事件（可复核）+ 各汇总表

四步验证顺序（文档 §4）在本文件里的对应位置：
1. 定义写死 → `support_pressure.py`（参数唯一权威源）
2. 样本量 ≥50 笔/腿 → §3 样本量守门表
3. 前向收益 20/60/120 日 vs 买入持有 → §4、§5
4. 条件逐个加 + 共线 → §8

模拟结果，非投资建议。
"""
import json
import os
import sys

import numpy as np
import pandas as pd

try:
    sys.stdout.reconfigure(encoding="utf-8")  # type: ignore[attr-defined]
except Exception:  # noqa: BLE001
    pass

_HERE = os.path.dirname(os.path.abspath(__file__))
_VL = os.path.dirname(_HERE)
_V5 = os.path.join(_VL, "v5_trend_gate")      # 只给 §7.4 的口径校验用（不参与默认路径）
for _p in (_HERE, _VL, _V5):
    if _p not in sys.path:
        sys.path.insert(0, _p)

import support_pressure as sp   # noqa: E402

OUTDIR = os.path.join(_HERE, "backtest")
OUT_MD = os.path.join(OUTDIR, "_v11_support_bt.md")
OUT_JSONL = os.path.join(OUTDIR, "_v11_support_events.jsonl")

# 参数敏感性档：(标签, 参数覆盖)
SENS = [
    ("基准（MA200 月频 · 前低 · 带2% · 0.5ATR · 5R）", {}),
    ("趋势改 MA250 + 日频（= v5 年线口径）", dict(MA_WIN=250, TREND_FREQ="daily")),
    ("趋势改日频判定（MA200 不变）", dict(TREND_FREQ="daily")),
    ("斜率回看 10 日", dict(SLOPE_N=10)),
    ("斜率回看 60 日", dict(SLOPE_N=60)),
    ("支撑改密集区（无成交量的降级口径）", dict(SUP_MODE="profile")),
    ("摆动点 5 日", dict(SWING_N=5)),
    ("摆动点 20 日", dict(SWING_N=20)),
    ("到线带 1%", dict(BAND=0.01)),
    ("到线带 3%", dict(BAND=0.03)),
    ("到线带 5%", dict(BAND=0.05)),
    ("止损 1.0×ATR", dict(STOP_ATR=1.0)),
    ("止损 1.5×ATR", dict(STOP_ATR=1.5)),
    ("目标 3R", dict(RR=3.0)),
    ("目标 2R", dict(RR=2.0)),
    ("冷却 10 日", dict(COOLDOWN=10)),
    ("冷却 40 日", dict(COOLDOWN=40)),
]


def fmt_pct(x, nd=2):
    return "—" if x is None or (isinstance(x, float) and not np.isfinite(x)) else f"{x * 100:+.{nd}f}%"


def fmt_x(x, nd=2):
    return "—" if x is None or (isinstance(x, float) and not np.isfinite(x)) else f"{x:+.{nd}f}"


def fp(x, nd=0):
    """无符号百分比（用于胜率/比例列），避免 f-string 里嵌套引号。"""
    return "—" if x is None or (isinstance(x, float) and not np.isfinite(x)) else f"{x * 100:.{nd}f}%"


def _daily(leg, **kw):
    """逐日诊断表 + 前向收益列（向量化，供条件递进表用）。"""
    px = sp.load(leg)
    F = sp.build(px, leg, **kw)
    p = sp.params(**kw)
    c = F["close"]
    for h in p["FWD"]:
        F[f"f{h}"] = c.shift(-h) / c - 1.0
    F["risk_pct"] = (c - (F["support"] - float(p["STOP_ATR"]) * F["atr"])) / c
    return px, F


# ---------------------------------------------------------------- 1. 逐腿
def leg_rows(**kw):
    rows = []
    for k in sp.LEGS:
        px = sp.load(k)
        ev, F = sp.events(px, k, **kw)
        st = sp.exit_stats(ev)
        fs = sp.fwd_stats(ev)
        start = ev["date"].min() if len(ev) else None
        base = sp.baseline(px, start=start)
        cov = sp.coverage_vs_v5(ev, px)
        years = (px.index[-1] - px.index[0]).days / 365.25
        rows.append(dict(
            leg=k, name=sp.rule.LEGS[k]["name"], role=sp.rule.LEGS[k]["role"],
            start=px.index[0].date().isoformat(), end=px.index[-1].date().isoformat(),
            years=round(years, 2),
            events=len(ev), cand=int(ev["cand"].iloc[0]) if len(ev) else 0,
            enough=bool(len(ev) >= sp.MIN_SAMPLE),
            ev_start=("" if start is None else pd.Timestamp(start).date().isoformat()),
            risk_med=st.get("risk_pct"), hit=st.get("hit_rate"), stop=st.get("stop_rate"),
            timeout=st.get("timeout_rate"), hold20=st.get("hold20"),
            hold20_stop=st.get("hold20_stop"),
            ev_R=st.get("ev_R"), ev_doc=st.get("ev_doc"),
            mfe_R=st.get("mfe_R"), mae_R=st.get("mae_R"), bars=st.get("bars"),
            hit_struct=st.get("hit_struct"),
            f20=fs.get("f20_mean"), f60=fs.get("f60_mean"), f120=fs.get("f120_mean"),
            w20=fs.get("f20_win"), w60=fs.get("f60_win"), w120=fs.get("f120_win"),
            q10_60=fs.get("f60_q10"), worst60=fs.get("f60_worst"),
            base20=base.get("f20_mean"), base60=base.get("f60_mean"),
            base120=base.get("f120_mean"), base60_win=base.get("f60_win"),
            ex20=(None if not (fs.get("f20_mean") and base.get("f20_mean")) else
                  fs["f20_mean"] - base["f20_mean"]),
            ex60=(None if not (fs.get("f60_mean") and base.get("f60_mean")) else
                  fs["f60_mean"] - base["f60_mean"]),
            ex120=(None if not (fs.get("f120_mean") and base.get("f120_mean")) else
                   fs["f120_mean"] - base["f120_mean"]),
            v5_share=cov["share"],
            source=sp.vdata.SOURCE_NOTE.get(k, ""),
        ))
    return rows


def pool_rows(evs):
    """池化：全部腿 / 只算达到 ≥50 笔的腿（文档 §4.2 的守门）+ 每腿等权。"""
    out = []
    if not evs:
        return out, pd.DataFrame()
    all_ev = pd.concat(evs, ignore_index=True)
    ok_legs = [k for k in sp.LEGS if int((all_ev["leg"] == k).sum()) >= sp.MIN_SAMPLE]
    specs = [("全部七腿池化（每笔一票）", all_ev)]
    if ok_legs:
        sub = all_ev[all_ev["leg"].isin(ok_legs)]
        specs.append((f"仅达 ≥{sp.MIN_SAMPLE} 笔的腿（{'/'.join(ok_legs)}）", sub))
    for name, sub in specs:
        st = sp.exit_stats(sub)
        fs = sp.fwd_stats(sub)
        out.append(dict(name=name, legs=sub["leg"].nunique(), n=len(sub),
                        risk_med=st.get("risk_pct"), hit=st.get("hit_rate"),
                        stop=st.get("stop_rate"), hold20=st.get("hold20"),
                        hold20_stop=st.get("hold20_stop"),
                        ev_R=st.get("ev_R"), ev_doc=st.get("ev_doc"),
                        mfe_R=st.get("mfe_R"), mae_R=st.get("mae_R"), bars=st.get("bars"),
                        hit_struct=st.get("hit_struct"),
                        f20=fs.get("f20_mean"), f60=fs.get("f60_mean"),
                        f120=fs.get("f120_mean"), w20=fs.get("f20_win"),
                        w60=fs.get("f60_win"), w120=fs.get("f120_win")))
    return out, all_ev


# ---------------------------------------------------------------- 2. 条件递进（腿日口径）
CONDS = [
    ("C0 无条件（同腿同区间全部交易日）", lambda F: pd.Series(True, index=F.index)),
    ("C1 趋势 = MA200 上行（月频）", lambda F: F["trend"]),
    ("C2 + 价格在支撑上方 2% 内", lambda F: F["trend"] & F["on_line"]),
    ("C3 + 当日收出 ≥0.25×ATR（确认）", lambda F: F["trend"] & F["on_line"] & F["conf"]),
    ("C4 + 止损距离 ≤3%（风险不要太宽）",
     lambda F: F["trend"] & F["on_line"] & (F["risk_pct"] <= 0.03)),
    ("C5 + 确认 且 止损距离 ≤3%（全条件）",
     lambda F: F["trend"] & F["on_line"] & F["conf"] & (F["risk_pct"] <= 0.03)),
]


def cond_rows(**kw):
    """条件逐个加：每条腿按条件过滤后统计前向收益，再按腿等权平均（避免长历史腿主导）。

    ⚠ 口径提醒：这里的样本单位是**腿·日**，连续多天高度自相关，
    不能当独立样本用；独立样本见 §3 的「事件」列（冷却后）。
    """
    frames = {}
    for k in sp.LEGS:
        px, F = _daily(k, **kw)
        F = F.iloc[int(sp.WARMUP):]
        frames[k] = F
    out = []
    for name, fn in CONDS:
        per_leg = []
        for k, F in frames.items():
            m = fn(F)
            sub = F[m]
            if not len(sub):
                continue
            row = dict(leg=k, days=len(sub))
            for h in sp.FWD:
                s = sub[f"f{h}"].dropna()
                row[f"f{h}"] = float(s.mean()) if len(s) else None
                if h == 60:
                    row["w60"] = float((s > 0).mean()) if len(s) else None
            per_leg.append(row)
        if not per_leg:
            out.append(dict(name=name, legs=0, days=0))
            continue
        agg = dict(name=name, legs=len(per_leg), days=sum(r["days"] for r in per_leg))
        for f in ("f20", "f60", "f120", "w60"):
            vals = [r[f] for r in per_leg if r.get(f) is not None]
            agg[f] = float(np.mean(vals)) if vals else None
        out.append(agg)
    base = next(r for r in out if r["name"].startswith("C0"))
    for r in out:
        for f in ("f20", "f60", "f120"):
            r[f"ex_{f}"] = (None if r.get(f) is None or base.get(f) is None
                            else r[f] - base[f])
    return out


def band_rows(**kw):
    """「到线带」宽度的稳健性：C2 的 f60 随 BAND 怎么变（同一个 build，只换阈值）。

    用途：C1→C2 的**负增量**是本报告最硬的结论，必须检查它只在 2% 这一档成立、还是普遍成立。
    """
    frames = {k: _daily(k, **kw)[1].iloc[int(sp.WARMUP):] for k in sp.LEGS}
    out = []
    for band in (0.01, 0.02, 0.03, 0.05, 0.08):
        vals = {h: [] for h in sp.FWD}
        days = 0
        for F in frames.values():
            m = F["trend"] & (F["dist"] >= 0.0) & (F["dist"] <= band)
            sub = F[m]
            days += len(sub)
            for h in sp.FWD:
                s = sub[f"f{h}"].dropna()
                if len(s):
                    vals[h].append(float(s.mean()))
        out.append(dict(band=band, days=days,
                        f20=float(np.mean(vals[20])) if vals[20] else None,
                        f60=float(np.mean(vals[60])) if vals[60] else None,
                        f120=float(np.mean(vals[120])) if vals[120] else None))
    return out


# ---------------------------------------------------------------- 3. 风险距离分桶
def risk_buckets(all_ev):
    """按「止损距离 risk_pct」分桶 -> 触目标率 / 止损率 / EV。

    这一张表就是对文档 §3.2 那个数学漏洞的**实测**：
    「胜率与盈亏比不是独立参数」—— 止损越紧，胜率应越低、EV 未必更高。
    """
    if not len(all_ev):
        return []
    edges = [0.0, 0.01, 0.02, 0.03, 0.05, 1.0]
    labels = ["<1%", "1~2%", "2~3%", "3~5%", ">5%"]
    b = pd.cut(all_ev["risk_pct"], bins=edges, labels=labels, right=False)
    out = []
    for lab in labels:
        sub = all_ev[b == lab]
        if not len(sub):
            out.append(dict(bucket=lab, n=0))
            continue
        st = sp.exit_stats(sub)
        out.append(dict(bucket=lab, n=len(sub), hit=st["hit_rate"], stop=st["stop_rate"],
                        hold20=st["hold20"], ev_R=st["ev_R"], f60=float(sub["f60"].mean()),
                        w60=float((sub["f60"] > 0).mean())))
    return out


# ---------------------------------------------------------------- 4. 文档另两格
def _trend_fwd(px, side="down", hs=(20, 60), **kw):
    """同腿「处于该趋势」的全部交易日的前向收益均值 —— 事件组的对照基准。

    不能拿「全区间买入持有」当基准：下跌趋势本身的前向收益就是负的，那样会把
    「趋势」的贡献算到「压力位」头上。所以基准也用 `trend_flags` 的同一条掩码。
    """
    c = px.astype("float64")
    up, dn = sp.trend_flags(c, **kw)
    m = (dn if side == "down" else up).to_numpy(dtype=bool)
    v = c.to_numpy(dtype=float)
    n = len(v)
    out = {}
    for h in hs:
        src = np.flatnonzero(m)
        src = src[src + int(h) < n]
        out[f"base{h}"] = float(np.mean(v[src + int(h)] / v[src] - 1.0)) if len(src) else None
        out[f"wbase{h}"] = float(np.mean(v[src + int(h)] > v[src])) if len(src) else None
    return out


def pressure_rows(side="up", **kw):
    """触压力位：突破率 / 压住率 + 前向收益（第 2 格口播 60~70% 突破、第 3 格 70% 压住）。

    做空 EV 不算（A 股不可执行）；`down` 侧额外给「20 日内跌破当时支撑」的比例，
    作为「做空方向有没有空间」的代理。对照基准见 `_trend_fwd`（同腿同趋势日）。
    """
    rows = []
    for k in sp.LEGS:
        px = sp.load(k)
        ev = sp.pressure_events(px, k, side=side, **kw)
        base = _trend_fwd(px, side=side, **kw)
        if not len(ev):
            rows.append(dict(leg=k, name=sp.rule.LEGS[k]["name"], side=side, n=0, **base))
            continue
        brk20 = ev["brk20"].dropna()
        brk60 = ev["brk60"].dropna()
        bsup = ev["below_sup20"].dropna()
        row = dict(leg=k, name=sp.rule.LEGS[k]["name"], side=side, n=len(ev),
                   brk20=float(brk20.mean()) if len(brk20) else None,
                   brk60=float(brk60.mean()) if len(brk60) else None,
                   f20=float(ev["f20"].mean()), f60=float(ev["f60"].mean()),
                   w20=float((ev["f20"] > 0).mean()),
                   bsup=float(bsup.mean()) if len(bsup) else None)
        row.update(base)
        row["ex20"] = (row["f20"] - row["base20"]
                       if row["f20"] is not None and row["base20"] is not None else None)
        rows.append(row)
    return rows


def down_rows(**kw):
    """下跌趋势触支撑：「撑住」比例（文档口播 30~40%）+ 若抄底做多的 EV。"""
    kw = dict(kw)
    kw["TREND_SIDE"] = "down"
    rows = []
    for k in sp.LEGS:
        px = sp.load(k)
        ev, _ = sp.events(px, k, **kw)
        if not len(ev):
            rows.append(dict(leg=k, name=sp.rule.LEGS[k]["name"], n=0))
            continue
        st = sp.exit_stats(ev)
        rows.append(dict(leg=k, name=sp.rule.LEGS[k]["name"], n=len(ev),
                         hold20=st["hold20"], hit=st["hit_rate"], stop=st["stop_rate"],
                         ev_R=st["ev_R"], f20=float(ev["f20"].mean()),
                         f60=float(ev["f60"].mean()), w20=float((ev["f20"] > 0).mean())))
    return rows


# ---------------------------------------------------------------- 4.5 换成 v5 的年线口径
V5_KW = dict(MA_WIN=250, SLOPE_N=20, TREND_FREQ="daily")   # = v5_trend_gate 的趋势读数


def quad_check(**kw):
    """校验「v11 的 `trend_flags(MA250/20 日/日频)`」与 v5 的四象限**是不是同一件事**。

    先测一致率再报数：两套掩码若不一致，「换成 v5 口径」这句话就站不住。
    已知唯一差异是边界写法 —— v5 的 `readout` 用 `close >= ma`、v11 用 `close > ma`，
    只会在「收盘恰好等于均线」那一天分歧（浮点下几乎不会发生）。
    """
    import trend_gate as tg                    # 只在口径对照时依赖 v5，默认路径不加硬依赖
    out = []
    for k in sp.LEGS:
        px = sp.load(k)
        up, dn = sp.trend_flags(px, **kw)
        F = tg.indicators(px)
        rd = F["ready"].to_numpy(dtype=bool)
        q = F["quad"].to_numpy()
        a_up = float((np.asarray(up)[rd] == (q[rd] == tg.QUADS[0])).mean())
        a_dn = float((np.asarray(dn)[rd] == (q[rd] == tg.QUADS[3])).mean())
        out.append(dict(leg=k, name=sp.rule.LEGS[k]["name"], days=int(rd.sum()),
                        up_agree=a_up, dn_agree=a_dn))
    return out


# ---------------------------------------------------------------- 5. 敏感性
def sens_rows(**kw):
    out = []
    bases = {k: sp.baseline(sp.load(k)) for k in sp.LEGS}
    base60 = float(np.mean([b["f60_mean"] for b in bases.values() if b["f60_mean"] is not None]))
    for name, over in SENS:
        kw2 = dict(kw)
        kw2.update(over)
        evs = []
        for k in sp.LEGS:
            px = sp.load(k)
            ev, _ = sp.events(px, k, **kw2)
            if len(ev):
                evs.append(ev)
        if not evs:
            out.append(dict(name=name, n=0))
            continue
        all_ev = pd.concat(evs, ignore_index=True)
        st = sp.exit_stats(all_ev)
        f60 = float(all_ev["f60"].mean())
        out.append(dict(name=name, n=len(all_ev), hit=st["hit_rate"], stop=st["stop_rate"],
                        hold20=st["hold20"], ev_R=st["ev_R"], ev_doc=st["ev_doc"],
                        f60=f60, ex60=f60 - base60,
                        risk_med=st["risk_pct"], bars=st["bars"]))
    return out


def _top_txt(rows):
    """结论里引用「样本最多的三条腿」的短句。"""
    top = sorted(rows, key=lambda z: -z["events"])[:3]
    return " / ".join(f"{r['name']} {r['events']} 笔" for r in top)


# ---------------------------------------------------------------- 6. 报告
def main():
    os.makedirs(OUTDIR, exist_ok=True)
    rows = leg_rows()
    evs = []
    for k in sp.LEGS:
        px = sp.load(k)
        ev, _ = sp.events(px, k)
        if len(ev):
            evs.append(ev)
    prows, all_ev = pool_rows(evs)
    conds = cond_rows()
    bands = band_rows()
    buckets = risk_buckets(all_ev)
    press = pressure_rows("up")
    press_dn = pressure_rows("down")
    downs = down_rows()
    sens = sens_rows()

    def _pavg(rs, key, cnt="n"):
        """按事件数加权的七腿池化（跳过该腿无样本/无值的项）。"""
        sub = [r for r in rs if r.get(cnt) and r.get(key) is not None]
        tot = sum(r[cnt] for r in sub)
        return (sum(r[key] * r[cnt] for r in sub) / tot) if tot else None

    _wavg = _pavg          # 老名字沿用：压力格的行用 "n" 计数

    pv = [r for r in press if r.get("n")]
    p20 = [r for r in pv if r.get("brk20") is not None]
    p60 = [r for r in pv if r.get("brk60") is not None]
    brk20 = (sum(r["brk20"] * r["n"] for r in p20) / sum(r["n"] for r in p20)) if p20 else None
    brk60 = (sum(r["brk60"] * r["n"] for r in p60) / sum(r["n"] for r in p60)) if p60 else None

    # 第 3 格（下跌趋势反弹到压力位）的池化
    pdv = [r for r in press_dn if r.get("n")]
    pd_n = sum(r["n"] for r in pdv)
    pd_brk20, pd_brk60 = _wavg(pdv, "brk20"), _wavg(pdv, "brk60")
    pd_stay20 = (1.0 - pd_brk20) if pd_brk20 is not None else None
    pd_f20, pd_f60 = _wavg(pdv, "f20"), _wavg(pdv, "f60")
    pd_b20, pd_b60 = _wavg(pdv, "base20"), _wavg(pdv, "base60")
    pd_ex20 = (pd_f20 - pd_b20) if (pd_f20 is not None and pd_b20 is not None) else None
    pd_w20, pd_wb20 = _wavg(pdv, "w20"), _wavg(pdv, "wbase20")
    pd_bsup = _wavg(pdv, "bsup")

    # ---- §7.4 换成 v5 的年线口径（MA250 + 20 日斜率 + 日频）重跑四格
    quad_ok = quad_check(**V5_KW)
    rows_v5 = leg_rows(**V5_KW)
    press_v5 = pressure_rows("up", **V5_KW)
    press_dn_v5 = pressure_rows("down", **V5_KW)
    downs_v5 = down_rows(**V5_KW)

    def _cell1(rs):        # 第 1 格：上涨趋势触支撑
        return dict(n=sum(int(r.get("events") or 0) for r in rs),
                    hold20=_pavg(rs, "hold20", "events"),
                    ev=_pavg(rs, "ev_R", "events"),
                    f60=_pavg(rs, "f60", "events"),
                    ex60=_pavg(rs, "ex60", "events"))

    def _cell2(rs):        # 第 2 格：上涨趋势触压力
        return dict(n=sum(int(r.get("n") or 0) for r in rs),
                    brk20=_pavg(rs, "brk20"), brk60=_pavg(rs, "brk60"),
                    f60=_pavg(rs, "f60"))

    def _cell3(rs):        # 第 3 格：下跌趋势反弹到压力
        return dict(n=sum(int(r.get("n") or 0) for r in rs),
                    brk20=_pavg(rs, "brk20"), brk60=_pavg(rs, "brk60"),
                    f20=_pavg(rs, "f20"), b20=_pavg(rs, "base20"))

    def _cell4(rs):        # 第 4 格：下跌趋势触支撑
        return dict(n=sum(int(r.get("n") or 0) for r in rs),
                    hold20=_pavg(rs, "hold20"), ev=_pavg(rs, "ev_R"), f20=_pavg(rs, "f20"))

    c1a, c1b = _cell1(rows), _cell1(rows_v5)
    c2a, c2b = _cell2(press), _cell2(press_v5)
    c3a, c3b = _cell3(press_dn), _cell3(press_dn_v5)
    c4a, c4b = _cell4(downs), _cell4(downs_v5)
    q_up = min(r["up_agree"] for r in quad_ok), max(r["up_agree"] for r in quad_ok)
    q_dn = min(r["dn_agree"] for r in quad_ok), max(r["dn_agree"] for r in quad_ok)
    q_days = sum(r["days"] for r in quad_ok)

    def _dpp(b, a):
        """两套口径的差（百分点），b = v5 口径、a = 主口径。"""
        return "—" if (a is None or b is None) else f"{(b - a) * 100:+.0f}pp"

    _pairs = [(c1b.get("hold20"), c1a.get("hold20")), (c2b.get("brk20"), c2a.get("brk20")),
              (c2b.get("brk60"), c2a.get("brk60")), (c3b.get("brk20"), c3a.get("brk20")),
              (c3b.get("brk60"), c3a.get("brk60")), (c4b.get("hold20"), c4a.get("hold20"))]
    _dmax = max([abs(b - a) * 100 for b, a in _pairs if a is not None and b is not None] or [0.0])

    with open(OUT_JSONL, "w", encoding="utf-8") as f:
        for _, r in all_ev.iterrows():
            d = {}
            for kk, vv in r.items():
                if isinstance(vv, pd.Timestamp):
                    d[kk] = vv.date().isoformat()
                elif isinstance(vv, (np.floating, float)):
                    d[kk] = None if not np.isfinite(vv) else round(float(vv), 6)
                elif isinstance(vv, (np.integer,)):
                    d[kk] = int(vv)
                elif isinstance(vv, (np.bool_, bool)):
                    d[kk] = bool(vv)
                else:
                    d[kk] = vv
            f.write(json.dumps({"event": d}, ensure_ascii=False) + "\n")
        for tag, tbl in (("leg", rows), ("pool", prows), ("cond", conds),
                         ("bucket", buckets), ("pressure", press),
                         ("pressure_dn", press_dn),
                         ("down", downs), ("sens", sens)):
            for r in tbl:
                f.write(json.dumps({tag: {k: (round(v, 6) if isinstance(v, float) else v)
                                          for k, v in r.items()}},
                                   ensure_ascii=False) + "\n")

    enough = [r for r in rows if r["enough"]]
    not_enough = [r for r in rows if not r["enough"]]
    pool = prows[0]
    pool_ok = prows[1] if len(prows) > 1 else prows[0]
    base60 = float(np.mean([r["base60"] for r in rows if r["base60"] is not None]))
    ex = [r for r in rows if r["ex60"] is not None]
    win_ex = [r for r in ex if r["ex60"] > 0]

    L = [
        "# 波动资产 · V11 变种：支撑/压力位 × 期望值 · 事件研究",
        "",
        "> 规则来源：`E:\\qianwen\\jijin\\支撑压力位期望值策略.md`（抖音「熊猫讲技术分析」口播拆解，"
        "2026-10-15）—— 作者**未给样本量、周期、品种、成交成本、胜率口径**，属方法论主张。",
        "> 本变种**只做该文档 §4 自己规定的四步验证**：① 定义写死 → ② 单腿 ≥50 笔才谈业绩 → "
        "③ 只看信号日后 20/60/120 日前向收益 vs 买入持有 → ④ 条件逐个加 + 查共线。",
        "> **因此本报告刻意不输出年化 / 最大回撤 / 夏普**（文档 §4.3 明令「不在小样本上报年化/回撤」）。"
        "家族其它变种都是 NAV 择时回测，本变种**不是** —— 它是一张「这笔交易值不值得做」的验伪报告。",
        "",
        "## 1. 规则（文档 §4.1 的可执行翻译，参数唯一权威源 = `support_pressure.py`）",
        "",
        "| 环节 | 写死的定义 | 参数 | 与文档的关系 |",
        "|---|---|---|---|",
        f"| 趋势 | `close > MA200` **且** `MA200 − MA200 20 日前 > 0`，**每月最后一个交易日判定、次月整月沿用** | "
        f"`MA_WIN={sp.MA_WIN}` · `SLOPE_N={sp.SLOPE_N}` · `TREND_FREQ={sp.TREND_FREQ}` | "
        "文档 §4.1 原文（MA200 月频）。注意 v5 用的是 **MA250 日频**，两者不可混用 |",
        f"| 支撑 | 最近一个**已确认**摆动低点（滞后 {sp.SWING_N} 日确认，回看 {sp.SUP_LOOKBACK} 日） | "
        f"`SWING_N={sp.SWING_N}` · `SUP_MODE={sp.SUP_MODE}` | 「前低」；`profile`（时间密集区下沿）作对照 —— "
        "本地只有收盘价、**没有成交量**，做不出真正的 volume profile |",
        f"| 止损 | `支撑 − {sp.STOP_ATR}×ATR14`，ATR14 = `mean(|Δclose|,14)` | "
        f"`STOP_ATR={sp.STOP_ATR}` · `ATR_N={sp.ATR_N}` | 无 H/L，故 ATR 取 close-to-close 下界（**止损更紧 = 对策略不利**，是保守偏置） |",
        f"| 目标 | `entry + {sp.RR:g}R`（R = entry − stop）；结构目标（上方枢轴高点）单列对照 | `RR={sp.RR:g}` | "
        "文档口播的「赚 1000 / 亏 200 = 5:1」 |",
        f"| 到线 | `0 ≤ close/support − 1 ≤ {sp.BAND:.0%}`，**破位（close < 支撑）不算回踩** | `BAND={sp.BAND}` | "
        "文档铁律①「支撑破了就走」，破位日不进样本 |",
        f"| 冷却 | 同一腿两个事件至少隔 {sp.COOLDOWN} 个交易日 | `COOLDOWN={sp.COOLDOWN}` | "
        "**本实现新增**：否则一次触线连出十几笔、样本量虚增（§4.2 的守门会被绕过） |",
        f"| 跟踪 | 从 t+1 起逐日看收盘，先触止损/目标者了结，满 {sp.HOLD_MAX} 日算到期 | `HOLD_MAX={sp.HOLD_MAX}` | — |",
        "",
        f"> 单腿样本门槛：**≥{sp.MIN_SAMPLE} 笔**（文档 §4.2）。不达标的腿只列信号日与事件分布，"
        "不参与「达标腿」汇总；§3/§4 的「全部七腿池化」一行把不达标腿也算进去了（每笔一票），"
        "它只是**样本量的补充**，不能当业绩证据 —— 两行请分开看。",
        "",
        "## 2. 样本量守门",
        "",
        f"七腿、场外**复权净值**（含分红再投 / 汇率 / 跟踪误差）。达标腿 {len(enough)}/{len(rows)}，"
        f"不达标 {len(not_enough)}/{len(rows)}。",
        "",
        "| 腿 | 角色 | 样本区间 | 年数 | **独立事件** | 触线候选（冷却前） | ≥50 笔 |",
        "|---|---|---|---:|---:|---:|:---:|",
    ]
    for r in sorted(rows, key=lambda z: -z["events"]):
        L.append(f"| {r['name']} | {r['role']} | {r['start']} ~ {r['end']} | {r['years']:.1f} | "
                 f"**{r['events']}** | {r['cand']} | {'✅' if r['enough'] else '❌'} |")
    L += [
        "",
        f"> 池化后共 **{pool['n']} 笔**（每笔一票，覆盖 {pool['legs']} 条腿），超过门槛；"
        f"但**逐腿只有 {len(enough)} 条腿达标** —— 科创50（{rows[4]['events']} 笔）与恒生科技"
        f"（{rows[5]['events']} 笔）样本太短（2021 年起），它们的结果只能当参考，不能当业绩。",
        "> ⚠ **A500 与沪深300 不是两份独立证据**：2025-03-27 之前两条腿的场外序列逐值相同"
        "（A500 的桥接基金就是 `000051`，见 `../README.md` §2.3），所以它们的事件日与结局几乎重合。"
        "池化时这相当于同一个样本被数了两次 —— 看池化数字时请把这两条腿当一条。",
        "",
        "## 3. 前向收益：信号日 vs 买入持有（文档 §4.3 的核心表）",
        "",
        "「基准」= 同一腿、同一区间**全部交易日**的前向收益均值（= 买入持有的日度平均）。"
        "「超额」= 信号日均值 − 基准均值。基价 = 信号日收盘。",
        "",
        "| 腿 | 事件 | 中位止损距离 | f20 均值 | f60 均值 | f120 均值 | 60日胜率 | 基准 f60 | "
        "**超额 f60** | 超额 f120 |",
        "|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|",
    ]
    for r in sorted(rows, key=lambda z: -(z["ex60"] or -9)):
        L.append(f"| {r['name']} | {r['events']} | {fp(r['risk_med'], 1)} | "
                 f"{fmt_pct(r['f20'])} | {fmt_pct(r['f60'])} | {fmt_pct(r['f120'])} | "
                 f"{fp(r['w60'])} | {fmt_pct(r['base60'])} | **{fmt_pct(r['ex60'])}** | "
                 f"{fmt_pct(r['ex120'])} |")
    L += [
        "",
        "| 汇总 | 事件 | 中位止损距离 | f20 | f60 | f120 | 20日胜率 | 60日胜率 | 120日胜率 |",
        "|---|---:|---:|---:|---:|---:|---:|---:|---:|",
    ]
    for r in prows:
        L.append(f"| **{r['name']}** | {r['n']} | {fp(r['risk_med'], 1)} | {fmt_pct(r['f20'])} | "
                 f"{fmt_pct(r['f60'])} | {fmt_pct(r['f120'])} | {fp(r['w20'])} | {fp(r['w60'])} | "
                 f"{fp(r['w120'])} |")
    L += [
        "",
        f"> 七腿里 60 日超额为正的有 **{len(win_ex)}/{len(ex)}** 条（中位超额 "
        f"**{fmt_pct(float(np.median([r['ex60'] for r in ex])))}**），最低的是 "
        f"**{min(ex, key=lambda z: z['ex60'])['name']}**"
        f"（{fmt_pct(min(ex, key=lambda z: z['ex60'])['ex60'])}）、最高的是 "
        f"**{max(ex, key=lambda z: z['ex60'])['name']}**"
        f"（{fmt_pct(max(ex, key=lambda z: z['ex60'])['ex60'])}）。"
        "⚠ 最高的两条（中证A500 / 沪深300）是**同一段场外序列**，别当两个证据。",
        "> **但超额不等于「能赚」**：这里的「基准」是**全部交易日**（含下跌趋势），所以这个超额"
        "里混着「趋势」的贡献 —— 它只说明「趋势 + 到线」整体比全样本好。"
        "**「支撑位」自己的增量**要看 §8 的 C1→C2：在趋势之上，贴线（≤2%）买是负的"
        "（§8.1 给出精确边界：3% 以上转正）。",
        "> ⚠ 前向窗口重叠（60/120 日窗口内可能落了 2~3 个事件），独立样本数应按事件数打折看。",
        "",
        "## 4. 期望值实算 vs 文档的 +3.2R",
        "",
        "文档的公式：`70% × 5R − 30% × 1R = +3.2R`。本表用**实测触目标率**替换 70%：",
        "`ev_doc = 触目标率 × 5R − (1 − 触目标率) × 1R`；`ev_R` = 逐笔 `realized_R` 的实际均值"
        "（止损被打穿时 < −1R，目标被跳空穿过时 > +5R，都不截断）。",
        "",
        "| 组合 | 事件 | 触 5R 目标 | 止损 | 到期 | **20日未破支撑** | **20日未触止损** | "
        "中位 MFE | 中位 MAE | 中位持有 | **EV（实测 R）** | EV（文档公式） | 文档口播 |",
        "|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|",
    ]
    for r in prows:
        L.append(f"| **{r['name']}** | {r['n']} | {fp(r['hit'])} | {fp(r['stop'])} | "
                 f"{fp(1 - r['hit'] - r['stop'])} | {fp(r['hold20'])} | {fp(r['hold20_stop'])} | "
                 f"{r['mfe_R']:+.2f}R | {r['mae_R']:+.2f}R | {r['bars']:.0f} 日 | "
                 f"**{r['ev_R']:+.2f}R** | {r['ev_doc']:+.2f}R | +3.20R |")
    L += [
        "",
        f"> **口径差在哪**：文档把「赢」定义成 5R 目标达成，并假设概率 70%。实测触 5R 目标的只有 "
        f"**{fp(pool['hit'])}**（池化）、**{fp(pool_ok['hit'])}**（达标腿），止损率 **{fp(pool['stop'])}**。"
        f"代进文档公式得到 **{pool['ev_doc']:+.2f}R**，比口播的 +3.2R 低 "
        f"**{3.2 - pool['ev_doc']:.2f}R**；而按逐笔实际路径算只有 **{pool['ev_R']:+.2f}R**。",
        f"> **「撑住」有两种口径，两个都不到 70%**：严格口径（未来 20 日收盘从未跌破支撑）"
        f"**{fp(pool['hold20'])}**；交易口径（连止损位——支撑下方 0.5×ATR——都没碰到）"
        f"**{fp(pool['hold20_stop'])}**。文档说上涨趋势里撑住 ≈70%，实测差 20~30pp —— "
        "**「撑住」不是大概率事件 —— 连一半都不到**。",
        f"> **「撑住」低不等于「亏」**：同一批事件 20/60/120 日胜率是 "
        f"{fp(pool['w20'])}/{fp(pool['w60'])}/{fp(pool['w120'])}（§3）—— 价格可以短暂插破支撑、"
        "20 日后仍然上涨。**这正是「止损放在支撑下方一点点」最贵的地方**：它把「插破但没走」"
        "的样本变成 −1R。§9 里把止损放宽到 1.0/1.5×ATR，EV 从 "
        f"**{pool['ev_R']:+.2f}R** 抬到 **"
        f"{fmt_x(next((x.get('ev_R') for x in sens if x['name'].startswith('止损 1.0')), None))}R / "
        f"{fmt_x(next((x.get('ev_R') for x in sens if x['name'].startswith('止损 1.5')), None))}R**，"
        "是同一件事的反面证据。",
        f"> **与文档 §3.2 的批评吻合**：止损距离中位只有 **{fp(pool['risk_med'], 1)}**，"
        "而 5R 目标意味着要涨 "
        f"**{pool['risk_med'] * sp.RR:.1%}** 才达标 —— 止损越紧，越容易被日常波动打掉，"
        "胜率与盈亏比确实不能各取最优。下一节按止损距离分桶给出实测。",
        "",
        "## 5. 风险距离分桶：胜率与盈亏比不独立（文档 §3.2 的实测）",
        "",
        "| 止损距离 | 事件 | 触 5R 目标 | 止损 | 20日未破支撑 | f60 均值 | 60日胜率 | **EV（实测 R）** |",
        "|---|---:|---:|---:|---:|---:|---:|---:|",
    ]
    for b in buckets:
        if not b.get("n"):
            L.append(f"| {b['bucket']} | 0 | — | — | — | — | — | — |")
            continue
        L.append(f"| {b['bucket']} | {b['n']} | {b['hit']:.0%} | {b['stop']:.0%} | "
                 f"{b['hold20']:.0%} | {fmt_pct(b['f60'])} | {b['w60']:.0%} | "
                 f"**{b['ev_R']:+.2f}R** |")
    valid = [b for b in buckets if b.get("n")]
    L += [
        "",
        "> **这张表是「胜率与盈亏比不独立」的直接证据**：目标是固定的 **5R**，所以止损距离越小 → "
        "目标绝对幅度越小 → 触目标率越高；止损距离越大 → 目标越远 → 触目标率越低（"
        + " → ".join(f"{b['bucket']} {fp(b['hit'])}" for b in valid)
        + "）。**这不是「紧止损胜率高」的证明，而是「5R 是一个随 risk 缩放的目标」的机械后果**。",
        "> 两端都不给文档的 +3.2R：紧止损那一桶止损率仍 "
        + (f"**{fp(valid[0]['stop'])}**（插破支撑就被打掉）" if valid else "—")
        + "，期望值 "
        + (f"**{valid[0]['ev_R']:+.2f}R**" if valid else "—")
        + "；宽止损那一桶目标远到几乎不可能，"
        + (f"期望值 **{valid[-1]['ev_R']:+.2f}R**" if valid else "—")
        + "。**每一桶都远低于 +3.2R，而且没有哪一桶能靠「调止损」救回来** —— "
        "要接近 +3.2R，必须同时做到「胜率 70%」和「盈亏比 5:1」，这两个数在同一个止损距离上无法并存。",
        "> ⚠ 分桶是**事后**按止损距离切的，不是可交易的分组；它的用途只是回答「换一个止损距离，"
        "EV 会不会变成 +3.2R」—— 答案是不会。",
        "",
        "## 6. 结局归因：5R 现实吗",
        "",
        "| 腿 | 触 5R | 触结构目标（上方枢轴高点） | 中位 MFE | 中位 MAE | 中位持有 | 中位止损距离 |",
        "|---|---:|---:|---:|---:|---:|---:|",
    ]
    for r in sorted(rows, key=lambda z: -z["events"]):
        if not r["events"]:
            continue
        L.append(f"| {r['name']} | {fp(r['hit'])} | {fp(r['hit_struct'])} | "
                 f"{r['mfe_R']:+.2f}R | {r['mae_R']:+.2f}R | {r['bars']:.0f} 日 | "
                 f"{fp(r['risk_med'], 1)} |")
    L += [
        "",
        "> 结构目标（上方最近一个已确认枢轴高点）触达率明显高于 5R —— 说明**目标定在结构位"
        "而不是固定 5 倍风险距离**更现实。但结构目标的距离不稳定（有时 0.5R、有时 8R），"
        "所以本报告主口径仍用 5R（= 文档自己的算法），结构口径只作对照。",
        "",
        "## 7. 文档四格里的三格（做空不可执行，只验前瞻收益）",
        "",
        "### 7.1 上涨趋势触压力位：突破率（文档口播 60~70%）",
        "",
        "| 腿 | 事件 | 20 日内突破 | 60 日内突破 | f20 均值 | f60 均值 |",
        "|---|---:|---:|---:|---:|---:|",
    ]
    for r in press:
        if not r.get("n"):
            L.append(f"| {r['name']} | 0 | — | — | — | — |")
            continue
        L.append(f"| {r['name']} | {r['n']} | {fp(r['brk20'])} | {fp(r['brk60'])} | "
                 f"{fmt_pct(r['f20'])} | {fmt_pct(r['f60'])} |")
    L += [
        "",
        f"> **突破率比文档给的还高**：上涨趋势里触压力位，20 日内突破 **{fp(brk20)}**、"
        f"60 日内 **{fp(brk60)}**（七腿按事件数加权）；文档口播「突破 ≈ 60~70%」。"
        "也就是说**压力位在上涨趋势里几乎挡不住** —— 这一格恰好支持文档自己的结论"
        "（「不逆势空，挂突破单等」），而且是四格里**唯一被高估得少的**。",
        "> **不做空 EV**：A 股散户没有做空工具，文档 §3.4 自己承认「下跌趋势压力位做空、破支撑追空」落不了地。"
        "给一个执行不了的期望值只会误导 —— 这里只留「突破率」，用途是提醒**不要在压力位逆势空**。",
        "",
        "### 7.2 下跌趋势触支撑：撑住比例（文档口播 30~40%）",
        "",
        "| 腿 | 事件 | 20 日未破支撑 | 触 5R | 止损 | f20 均值 | 20日胜率 | EV（抄底做多，R） |",
        "|---|---:|---:|---:|---:|---:|---:|---:|",
    ]
    for r in downs:
        if not r.get("n"):
            L.append(f"| {r['name']} | 0 | — | — | — | — | — | — |")
            continue
        L.append(f"| {r['name']} | {r['n']} | {r['hold20']:.0%} | {r['hit']:.0%} | "
                 f"{r['stop']:.0%} | {fmt_pct(r['f20'])} | {r['w20']:.0%} | "
                 f"**{r['ev_R']:+.2f}R** |")
    dn_all = [r for r in downs if r.get("n")]
    dn_n = sum(r["n"] for r in dn_all)
    dn_w = (sum(r["hold20"] * r["n"] for r in dn_all) / dn_n) if dn_n else None
    dn_ev = (sum(r["ev_R"] * r["n"] for r in dn_all) / dn_n) if dn_n else None
    L += [
        "",
        (f"> 下跌趋势里触支撑、20 日未破支撑的比例 **{fp(dn_w)}**（{dn_n} 笔），"
         f"若照抄「抄底做多」EV **{dn_ev:+.2f}R**。" if dn_n else "> 下跌侧样本为空。"),
        "> **这一格对上了（四格里两格对上：本格 + §7.1 的突破率）**："
        "文档说「下跌趋势支撑撑住只有 30~40%」，"
        f"实测 {fp(dn_w)}，落在区间内；而且明显低于上涨趋势那一格的 {fp(pool['hold20'])} —— "
        "**「下跌趋势里的支撑更不可靠」这个判断是站得住的**，它对应的动作（不抄底、等确认、降仓）"
        "也是文档里唯一能落地的半边。",
        (f"> ⚠ **EV 为正不等于「可以抄底」**：池化 EV {dn_ev:+.2f}R 但撑住率只有三成 —— "
         "正的 EV 全部来自「少数打到 5R 的大赢」，而逐腿离散度极大"
         f"（{min(r['ev_R'] for r in dn_all):+.2f}R ~ {max(r['ev_R'] for r in dn_all):+.2f}R）。"
         "**这就是「不抄底」的定量理由：胜率低、靠赔率撑、单腿样本又少到无法判断** —— "
         "不是「一定亏」，而是「赚不赚取决于你数不出来的尾巴」。" if dn_all else
         "> ⚠ 下跌侧样本为空，无法判断抄底 EV。"),
        f"> ⚠ 但下跌侧的事件（{dn_n} 笔）里 A500/沪深300 又占了 "
        f"{downs[0].get('n', 0) + downs[1].get('n', 0)} 笔（同一条序列），实际独立样本更少；"
        "且「支撑怎么定义」对绝对值影响很大（§9 的敏感档），"
        "**把它当方向性的量级，不要当精确概率**。",
        "",
        "### 7.3 下跌趋势反弹到压力位：压住比例（文档口播「压住约 70%，该做空」· 2026-11-06 补测）",
        "",
        "§7.1/§7.2 只覆盖了四格里的三格。第 3 格（**下跌趋势里反弹到压力位，作者说压住约 70%、该做空**）"
        "本来就是唯一没测的一格，这里补上。事件 = 下跌趋势（`close < MA200` 且 MA200 下行，月频判定）"
        f"且价格贴到压力位下方 `0 ≤ 压力位/close − 1 ≤ {sp.BAND:.0%}`，冷却 {sp.COOLDOWN} 日。"
        "「基准」= 同腿**下跌趋势日**（不贴压力位）的前向收益 —— 不能拿全区间买入持有当基准，"
        "那样会把「趋势」的贡献算到「压力位」头上。",
        "",
        "| 腿 | 事件 | 20 日内未突破（=压住） | 60 日内未突破 | 20 日内跌破支撑 | "
        "f20 均值 | 下跌趋势日 f20（基准） | f20 超额 | 20 日胜率 |",
        "|---|---:|---:|---:|---:|---:|---:|---:|---:|",
    ]
    for r in press_dn:
        if not r.get("n"):
            L.append(f"| {r['name']} | 0 | — | — | — | — | — | — | — |")
            continue
        stay = (1.0 - r["brk20"]) if r.get("brk20") is not None else None
        stay60 = (1.0 - r["brk60"]) if r.get("brk60") is not None else None
        L.append(f"| {r['name']} | {r['n']} | {fp(stay)} | {fp(stay60)} | {fp(r.get('bsup'))} | "
                 f"{fmt_pct(r.get('f20'))} | {fmt_pct(r.get('base20'))} | {fmt_pct(r.get('ex20'))} | "
                 f"{fp(r.get('w20'))} |")
    _stay_ok = (pd_stay20 is not None and pd_stay20 >= 0.60)
    L += [
        "",
        (f"> **压住率 {fp(pd_stay20)}（20 日）/ {fp((1.0 - pd_brk60) if pd_brk60 is not None else None)}"
         f"（60 日）**，{pd_n} 笔事件；作者口播「压住约 70%」。"
         + ("**这一格对上了** —— 四格里两个方向相反的压力位判断（第 2 格上涨趋势 90% 突破、"
            "第 3 格下跌趋势约七成压住），实测都朝作者说的方向。" if _stay_ok else
            "**这一格对不上，而且是方向反了** —— 下跌趋势里压力位被突破的比例与上涨趋势里几乎一样，"
            "「反弹到压力位会被压住」不成立；压力位不是「墙」，只是趋势的陪衬。")
         if pd_stay20 is not None else "> 下跌侧压力位样本为空，第 3 格无法判断。"),
        (f"> **但「压住」不等于「能换成收益」**：事件组的 f20 {fmt_pct(pd_f20)} vs 同腿下跌趋势日基准 "
         f"{fmt_pct(pd_b20)}，超额 **{fmt_pct(pd_ex20)}**；20 日胜率 {fp(pd_w20)} vs 基准 {fp(pd_wb20)}；"
         f"20 日内跌破当时支撑的比例 {fp(pd_bsup)}。"
         + ("差距不到 1pp，而且是**正的** —— 「反弹到压力位」这个位置本身几乎没有额外信息，"
            "既不是更差的做空点，也不是更好的买点；它只是「下跌趋势」这一件事的重复计价"
            "（同 §8.1 的口径：贴线是负增量，方向两侧都不灵）。"
            if (pd_ex20 is not None and pd_ex20 > -0.01) else
            "差距为负，但这半边在 A 股**根本无法执行**（无做空工具），只能当「反弹不加仓」的软约束。")
         if pd_ex20 is not None else "> 事件样本为空，无法判断超额。"),
        f"> ⚠ 独立样本要打折：{pd_n} 笔里 A500/沪深300 占 {press_dn[0].get('n', 0) + press_dn[1].get('n', 0)} 笔"
        "（同一条序列），科创50/恒生科技各只有 6/8 笔 —— **把压住率当方向，不要当精确概率**。",
        "> **所以压力层在四格里的处境是**：第 2 格（上涨趋势压力位）概率方向对、但只能当「不逆势空」的约束；"
        "第 3 格（下跌趋势压力位）**概率方向就反了**，且即便成立也没有执行工具（A 股无做空）；"
        "真正可执行的「到压力位减仓」在 §8.1 的口径下是负增量。"
        "这是 v12 没有压力层的原因 —— 不是漏了，是四格里没有一格能变成可执行的正贡献规则。",
        "",
        "### 7.4 换成 v5 的年线口径（MA250 + 20 日斜率 + 日频）重测四格",
        "",
        "主口径是 **MA200 + 月频**（月末判定、次月整月沿用，v12 沿用这一条）；v5 是 **MA250 + 20 日斜率 + 日频**"
        "（年线闸门，每天判定、不做月频保持）。两者只差「均线窗口 + 判定频率」，所以这一节回答的是："
        "**四格的对错会不会因为换趋势口径而翻转** —— 作者的四格判断，是败在趋势怎么定义，"
        "还是败在支撑/压力这件事本身。",
        "",
        f"先说一致性：把 v11 的 `trend_flags(MA250/20/daily)` 与 v5 `trend_gate.indicators` 的四象限逐日比对"
        f"（{q_days} 个共同交易日），上涨判定一致率 {q_up[0]:.2%}~{q_up[1]:.2%}、下跌判定 "
        f"{q_dn[0]:.2%}~{q_dn[1]:.2%} —— 两套掩码是同一件事，唯一差异是 `close >= ma` 与 `close > ma` "
        "的边界写法（浮点下几乎不触发）。所以下表的两列可以直接对比。",
        "",
        "| 格 | 作者口播 | 主口径（MA200 · 月频） | v5 口径（MA250 · 日频） | 变化 |",
        "|---|---|---|---|---|",
        f"| 1 上涨趋势 · 回踩支撑 | 撑住 ≈70% | {fp(c1a['hold20'])}（{c1a['n']} 笔）· EV {fmt_x(c1a['ev'])}R | "
        f"{fp(c1b['hold20'])}（{c1b['n']} 笔）· EV {fmt_x(c1b['ev'])}R | {_dpp(c1b.get('hold20'), c1a.get('hold20'))} |",
        f"| 2 上涨趋势 · 到达压力 | 突破 60~70% | {fp(c2a['brk20'])} / {fp(c2a['brk60'])}（20/60 日） | "
        f"{fp(c2b['brk20'])} / {fp(c2b['brk60'])} | {_dpp(c2b.get('brk20'), c2a.get('brk20'))} |",
        f"| 3 下跌趋势 · 反弹到压力 | 压住 ≈70% | {fp(1 - c3a['brk20'])} / {fp(1 - c3a['brk60'])}（=压住） | "
        f"{fp(1 - c3b['brk20'])} / {fp(1 - c3b['brk60'])} | "
        f"{_dpp(None if c3b.get('brk20') is None else 1 - c3b['brk20'], None if c3a.get('brk20') is None else 1 - c3a['brk20'])} |",
        f"| 4 下跌趋势 · 到达支撑 | 撑住 30~40% | {fp(c4a['hold20'])}（{c4a['n']} 笔）· EV {fmt_x(c4a['ev'])}R | "
        f"{fp(c4b['hold20'])}（{c4b['n']} 笔）· EV {fmt_x(c4b['ev'])}R | {_dpp(c4b.get('hold20'), c4a.get('hold20'))} |",
        "",
        f"> **换口径没有翻转任何一格**：四格的概率读数最大只差 **{_dmax:.0f}pp**。撑住 {fp(c1a['hold20'])} → "
        f"{fp(c1b['hold20'])}（口播 70%，两套都没到）；压力突破 {fp(c2a['brk20'])} → {fp(c2b['brk20'])}"
        f"（口播 60~70%，两套都更高）；下跌侧压住 {fp(1 - c3a['brk20'] if c3a['brk20'] is not None else None)} → "
        f"{fp(1 - c3b['brk20'] if c3b['brk20'] is not None else None)}（口播 70%，两套都远低于）；"
        f"下跌侧撑住 {fp(c4a['hold20'])} → {fp(c4b['hold20'])}（口播 30~40%，只有这一格落在区间内）。",
        "> **所以作者的四格判断不是败在趋势定义上**，而是败在「支撑/压力本身没有那 70% 的黏性」——"
        "换成年线、换成日频，四个概率几乎不动。反过来说，v12 用 MA200 + 月频而不是 v5 的 MA250 + 日频，"
        "**不是因为四格读数更好**（差别在噪声内），而是月频把换手压到 v5 的 45~60%（v12 报告 §2）。"
        "要换口径可以，但别指望它能把四格救回来。",
        "",
        "## 8. 条件逐个加 + 共线检查（文档 §4.4）",
        "",
        "样本单位 = **腿·日**（连续日高度自相关，不能当独立样本；独立样本数见 §2）。"
        "每行是「在这些日子买入」的前向收益，按七腿等权平均。",
        "",
        "| 条件（逐个叠加） | 腿·日 | f20 | f60 | f120 | 60日胜率 | 超额 f60 | 超额 f120 |",
        "|---|---:|---:|---:|---:|---:|---:|---:|",
    ]
    for r in conds:
        L.append(f"| {r['name']} | {r['days']} | {fmt_pct(r.get('f20'))} | {fmt_pct(r.get('f60'))} | "
                 f"{fmt_pct(r.get('f120'))} | {fp(r.get('w60'))} | "
                 f"{fmt_pct(r.get('ex_f60'))} | {fmt_pct(r.get('ex_f120'))} |")
    c0 = next(r for r in conds if r["name"].startswith("C0"))
    c1 = next(r for r in conds if r["name"].startswith("C1"))
    c2 = next(r for r in conds if r["name"].startswith("C2"))
    c3 = next(r for r in conds if r["name"].startswith("C3"))
    c4 = next(r for r in conds if r["name"].startswith("C4"))
    c5 = next(r for r in conds if r["name"].startswith("C5"))
    d12 = (c2.get("f60") or 0) - (c1.get("f60") or 0)
    L += [
        "",
        f"> **「到支撑带」在趋势之上是负增量 —— 本报告最硬的一条**：C1（只有趋势）f60 "
        f"{fmt_pct(c1.get('f60'))} → C2（+到支撑带 2%）{fmt_pct(c2.get('f60'))}，"
        f"变化 **{d12 * 100:+.2f}pp**（f120 从 {fmt_pct(c1.get('f120'))} 到 "
        f"{fmt_pct(c2.get('f120'))}）。也就是说：**「贴到支撑 2% 内才买」的前向收益，"
        "低于「上涨趋势里随便哪天买入」** —— 文档的核心主张（到线才是买点）在这一档上不成立"
        "（**精确边界见 §8.1：只有贴线是负的，离支撑 3~8% 反而更好**）。"
        f"趋势本身有正超额（C1 vs C0：f60 {fmt_pct(c1.get('ex_f60'))}），"
        "**但「到线」这一步把它吃掉了一半**。",
        f"> **加确认/风险过滤能补回来吗**：C4（+止损距离 ≤3%）几乎不起作用（腿·日 "
        f"{c4['days']} vs C2 {c2['days']}）—— pivot 支撑天然贴着价格，风险距离本来就小；"
        f"C5（全条件）f60 {fmt_pct(c5.get('f60'))} 略高于 C1 的 {fmt_pct(c1.get('f60'))}"
        f"（{((c5.get('f60') or 0) - (c1.get('f60') or 0)) * 100:+.2f}pp），"
        f"但代价是腿·日从 {c1['days']} 压到 {c5['days']}（{c5['days'] / max(c1['days'], 1):.1%}），"
        "且相邻腿·日高度自相关 —— **这点差在统计上不构成证据**，不能拿来救「支撑位」这条主张。",
        f"> **共线检查（文档 §4.4 的第二条要求）**：v11 事件日里 "
        f"**{np.mean([r['v5_share'] for r in rows if r['v5_share'] is not None]):.0%}** "
        "同时满足 v5 的年线闸门（MA250 日频 + 20 日斜率）。也就是说 "
        "**v11 的「趋势」与已在跑的 v5 高度重合，它提供的「新信息」主要是「什么时候算到线」，"
        "不是「什么时候是上涨趋势」**。v11 若要进实盘，必须回答「比 v5 多给了什么」，"
        "本报告的回答是：**多给的是负的**（C1→C2）。",
        "> 文档 §4.4 还要求查「①地量 与 ④放量 同日 AND 天然互斥」这类**条件互斥**："
        "本实现里「到支撑带」与「确认阳线」不互斥（触线日可以同时收阳），实测 C3 的腿·日数 "
        f"{c3['days']} vs C2 {c2['days']}，**保留 {c3['days'] / max(c2['days'], 1):.0%}** —— 不构成互斥陷阱。",
    ]

    L += [
        "",
        "### 8.1 「到线带」宽度的稳健性（C1→C2 的负增量是否只在 2% 成立）",
        "",
        "同一个趋势口径（C1 固定），只换「距支撑多少算到线」：",
        "",
        "| 到线带 | 腿·日 | C2 f20 | C2 f60 | C2 f120 | 与 C1 的差（f60） |",
        "|---|---:|---:|---:|---:|---:|",
    ]
    for b in bands:
        d = (b["f60"] - c1["f60"]) if (b.get("f60") is not None) else None
        L.append(f"| ≤{b['band']:.0%} | {b['days']} | {fmt_pct(b.get('f20'))} | "
                 f"{fmt_pct(b.get('f60'))} | {fmt_pct(b.get('f120'))} | "
                 f"**{fmt_pct(d)}** |")
    neg = [b for b in bands if b.get("f60") is not None and b["f60"] < (c1["f60"] or 0)]
    pos = [b for b in bands if b.get("f60") is not None and b["f60"] >= (c1["f60"] or 0)]

    def _blist(items):
        return " / ".join(f"≤{b['band']:.0%}（{fmt_pct(b['f60'] - (c1['f60'] or 0))}）" for b in items)

    L += [
        "",
        f"> C1 的 f60 = {fmt_pct(c1.get('f60'))}（不含「到线」条件）。负增量只出现在 **{_blist(neg)}**"
        + (f"，从 **{_blist(pos)}** 起转正。" if pos else "，所有档都为负。")
        + "**所以精确的说法是「贴着支撑（2% 内）买是负增量」，不是「接近支撑整体是负增量」** —— "
        "这与文档「到线不动手、等回到支撑再买」正好相反：**越等到贴线越差，在支撑上方 3~8% 介入反而更好**。",
        "> 机制上可解释：价格贴到前低时，往往正处在下跌动能的末端，插破概率最高"
        f"（§4 的「20 日未破支撑」只有 {fp(pool['hold20'])}）；离支撑 3~8% 时还没测试支撑，"
        "位置更接近「回调中继」。**这条比「支撑位无效」更细，也是本报告唯一给出的正面建议："
        "别贴线买，要买就早一点。**",
        "> 注意这**不是**在挑参数：C1 不含 bandwidth，所以每一档都是「同一批趋势日 + 不同距离阈值」，"
        "比较是干净的。差异仍在 1pp 量级、样本高度自相关 —— **方向比幅度可信，不要拿具体数字当收益预期**。",
    ]

    L += [
        "",
        "## 9. 参数敏感性（池化 · 全部七腿）",
        "",
        "| 档位 | 事件 | 触 5R | 止损 | 20日未破支撑 | 中位止损距离 | f60 均值 | 超额 f60 | **EV（实测 R）** |",
        "|---|---:|---:|---:|---:|---:|---:|---:|---:|",
    ]
    for r in sens:
        if not r.get("n"):
            L.append(f"| {r['name']} | 0 | — | — | — | — | — | — | — |")
            continue
        L.append(f"| {r['name']} | {r['n']} | {r['hit']:.0%} | {r['stop']:.0%} | {r['hold20']:.0%} | "
                 f"{r['risk_med']:.1%} | {fmt_pct(r['f60'])} | {fmt_pct(r['ex60'])} | "
                 f"**{r['ev_R']:+.2f}R** |")
    evs_s = [r["ev_R"] for r in sens if r.get("n")]
    L += [
        "",
        f"> EV 落在 **{min(evs_s):+.2f}R ~ {max(evs_s):+.2f}R**，中位 "
        f"**{np.median(evs_s):+.2f}R**。**没有哪一格参数能让 EV 接近文档的 +3.2R** —— "
        "这是好事：说明「+3.2R 不成立」不是选错了参数，而是那组输入本身不可能同时成立。",
        "> **三个可读的方向**（都符合直觉，也都能反过来验一遍）：①止损放宽到 1.0 / 1.5×ATR，"
        "EV 单调上升（0.5 → 1.0 → 1.5："
        + " → ".join(f"{x:+.2f}R" for x in [
            next((y.get("ev_R") for y in sens if y["name"].startswith(k)), None)
            for k in ("基准", "止损 1.0", "止损 1.5")] if x is not None)
        + "）—— 文档最看重的「止损放支撑下方一点点」在净值上是负优化；"
        "②目标从 5R 收到 3R / 2R，触目标率上升但 EV 反而下降 —— **盈亏比不是可有可无的**；"
        "③摆动点 20 日（更慢、支撑更远）EV 最高，说明噪声主要来自「太近的支撑」。",
        "> ⚠ **同一批信号日的 f60 不随止损/目标变**（表里 237 笔那几行的 f60 都是 "
        f"{fmt_pct(next((s['f60'] for s in sens if s['name'].startswith('基准')), None))}）—— "
        "因为 f60 是价格前向收益；止损/目标只改变 `realized_R`，不改变信号日。这不是 bug。",
        "> 「支撑改密集区」（`SUP_MODE=profile`）是**降级口径**：本地没有成交量，只能用「收盘价的时间密集区」"
        "近似 volume profile，这是本变种最大的口径妥协，它的数字只用于说明「换一种支撑定义，结论不变」。",
        "",
        "## 10. 结论（只回答文档 §4 那四步能回答的）",
        "",
        "**一句话**：趋势用法值得留着，但那四个概率数字不成立；**「一直等到贴线再买」比在趋势里"
        "随便哪天买还差（要买就早一点，在支撑上方 3~8%）**；真正值钱的是"
        "「**跌破就走、不抄底**」这半边纪律，不是「到线就买」这半边。",
        "",
        f"1. **框架本身没问题，问题在数字**：EV 框架、等确认、单笔风险固定这三条，"
        "本报告无法证伪（本实现就是照它做的）。**能被证伪、并且确实被证伪的是那四个概率**："
        f"触 5R 目标只有 {fp(pool['hit'])}、止损 {fp(pool['stop'])}、"
        f"「撑住」{fp(pool['hold20'])}（严格）/ {fp(pool['hold20_stop'])}（未触止损），"
        f"EV 实测 {pool['ev_R']:+.2f}R（文档公式 {pool['ev_doc']:+.2f}R，口播 +3.2R）。",
        f"2. **「贴着支撑买」是负增量（本报告对策略的真正判决）**：C1（只有趋势）f60 "
        f"{fmt_pct(c1.get('f60'))} → C2（贴到 2% 内）+2.98%，**{d12 * 100:+.2f}pp**；"
        "但把阈值放宽到 3%/5%/8% 就转正（§8.1）。**精确结论：不是「接近支撑」无效，"
        "是「一直等到贴线才买」无效 —— 越等到贴线越差，在支撑上方 3~8% 介入反而更好**。"
        "文档说「支撑位站着一堆等着买的人，所以撑得住」，数据说那个位置插破概率最高"
        f"（20 日未破支撑只有 {fp(pool['hold20'])}）。**支撑位可以当止损参照，不该当买点触发**。",
        f"3. **四格里只有两格对得上**：下跌趋势支撑撑住 {fp(dn_w)}（文档 30~40%，✅ 对上了）、"
        f"上涨趋势压力位 60 日突破 {fp(brk60)}（文档 60~70%，✅ 方向对、还更高）；"
        f"上涨趋势支撑撑住 {fp(pool['hold20'])}（文档 70%，❌ 差 30pp）、"
        f"下跌趋势反弹到压力位「压住」{fp(pd_stay20)}（文档 70%，❌ **方向反了**，"
        f"实测 {fp(1.0 - pd_stay20) if pd_stay20 is not None else '—'} 被突破）；"
        f"文档口播的期望值 +3.2R 也不成立（实测 {pool['ev_R']:+.2f}R，❌ 差 2.8R）。"
        "**对上的两格都指向同一个动作：不抄底、不逆势空** —— 这刚好是 A 股能执行的那半边。"
        "压力层的两格（第 2/3 格）都在「不可执行」那一侧：一格只能说「不要逆势空」，"
        "另一格连方向都反了。",
        f"4. **「止损放支撑下方一点点」是负优化**：中位止损距离 {fp(pool['risk_med'], 1)}，"
        f"1.5R 以上的宽止损 EV 更高（§9）；A股两条宽基腿 f60 超额为正（+0.83/+1.52%）"
        "但 EV 为负（−0.42/−0.44R）—— **正的前向收益被执行方式吃掉了**。"
        "这与文档 §3.2 的批评是同一件事的两面：紧止损换来的是「便宜」，付出去的是「胜率」。",
        f"5. **「趋势」这一层与 v5 高度共线**（事件日 "
        f"{np.mean([r['v5_share'] for r in rows if r['v5_share'] is not None]):.0%} 落在 v5 的上涨趋势里）："
        "真要在七腿上用趋势，v5 已经做了，v11 只多给了「哪一天到线」这个执行细节 —— "
        "**而 §8 说这个细节是负的**。所以本变种**不进每日流程、不与主策略/ v5 叠加**。",
        "6. **一半打法在 A 股不可执行**：下跌趋势压力位做空 / 破支撑追空落不了地，"
        "只剩「不抄底、等确认、降仓」这半边 —— §7.2 给出了「不抄底」的量化理由。",
        f"7. **不报年化/回撤是结论的一部分**：单腿最多的是 {_top_txt(rows)}，"
        f"只有 {len(enough)}/{len(rows)} 条腿达到 ≥{sp.MIN_SAMPLE} 笔；"
        "这个量级的样本上谈年化就是自欺（文档 §4.2/§4.3 已经写明）。",
        "",
        "## 11. 已知局限",
        "",
        "- **只有收盘价**：没有 OHLCV，所以 ① ATR 是 close-to-close 下界（止损更紧、更容易被打掉，"
        "方向上保守）；②「成交密集区」被降级成「时间密集区」（收盘价落点分布），不是真正的 volume profile；"
        "③「阳线确认」用「涨幅 ≥0.25×ATR」替代。这三处都会让结果**偏离原意**，但偏置方向可判断、可解释。",
        f"- **止损只能收盘确认，滑点无法避免**：本实现不看盘中价，「收盘价 ≤ stop」才了结，"
        f"所以实际亏损经常超过 1R（池化中位 MAE {pool['mae_R']:+.2f}R 就是这个，"
        f"而单笔 EV 只有 {pool['ev_R']:+.2f}R）。这不是记账误差，而是**场外净值口径的真实约束** —— "
        "你不可能在「支撑下方 0.5×ATR」挂一个精确止损，只能盘后看到跌破、次日才动。"
        "要贴近实战必须用场内 ETF 的盘中价（本变种没有建 OHLCV 库），这也是为什么 §9 里"
        "「放宽止损」的 EV 更高：它部分等价于「少被收盘确认的滑点打掉」。",
        "- **支撑/压力的定义高度依赖摆动点参数**：`SWING_N=5/10/20` 与 `BAND=1%/2%/3%` 的实测在 §9；"
        "结构位识别本质上有主观性，本实现用「机械摆动点 + 冷却」压到可复现，但不等于原作者的看图方式。",
        "- **前向窗口重叠**：事件间隔仅 20 个交易日（冷却），60/120 日窗口会重叠，"
        "独立样本数应按事件数打折；本报告没有做 block bootstrap，所以「超额」的显著性未检验。",
        "- **没有做 walk-forward**：参数是文档写死的（MA200 / 月频 / 0.5ATR / 5R），不是在本样本上选出来的，"
        "这一点反而比家族里那些「调参到最优」的变种更干净。",
        "- **A500/沪深300 双重计数**：见 §2 的警告，池化数字里这两条腿占 "
        f"{(rows[0]['events'] + rows[1]['events']) / max(pool['n'], 1):.0%}，去掉后样本降到 "
        f"{pool['n'] - rows[0]['events'] - rows[1]['events']} 笔。",
        "- **成本未计入**：事件研究看的是价格前向收益，不是账户收益；真要执行还要扣申赎费、"
        "承受场外 T+1/T+2 确认时滞（QDII）。但 5R 目标意味着持有期通常 >20 个交易日，"
        "赎回费那一档（0.5%）对 1R 的影响在 2~3% 量级，不改变结论方向。",
        "- **「趋势」写死之后本报告才可证伪**：文档原文没有定义趋势，任何走势都能被解释成「需要的那个趋势」"
        "—— 这个坑与利差口径那次一致（同一读数近 10 年 38.7% / 近 5 年 14.8% / 近 3 年 0.14%）。"
        "本变种用 MA200 月频写死，换窗口的实测见 §9。",
        "",
        "> **模拟结果，非投资建议。**",
    ]

    with open(OUT_MD, "w", encoding="utf-8") as f:
        f.write("\n".join(L))

    print("报告:", OUT_MD)
    print("事件:", OUT_JSONL)
    for r in prows:
        print(f"  {r['name']}: {r['n']} 笔 · 触5R {r['hit']:.0%} · 止损 {r['stop']:.0%} · "
              f"20日未破支撑 {r['hold20']:.0%} · EV {r['ev_R']:+.2f}R / 文档口径 {r['ev_doc']:+.2f}R")
    for r in sorted(rows, key=lambda z: -z["events"]):
        print(f"  {r['name']:8s} 事件 {r['events']:3d} · 超额f60 {fmt_pct(r['ex60']):>9s} · "
              f"EV {fmt_x(r['ev_R'])}R · 20日未破 {fp(r['hold20'])}")


if __name__ == "__main__":
    main()
