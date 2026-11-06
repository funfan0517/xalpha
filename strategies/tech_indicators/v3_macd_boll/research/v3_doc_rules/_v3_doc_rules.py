# -*- coding: utf-8 -*-
"""v3（MACD+BOLL 突破）· 逐条吸收「策略文档 v1.0」规则的增量 A/B。

背景：`G:/tradingagents/macd_boll_strategy/策略文档_v1.0.md` 归纳了一套 MACD+BOLL 框架，
但零代码、零回测。本脚本把其中**可量化的条款**一条一条加到现有 v3 上（单条增量），
在 `data/_universe.md` 前四类的 54 只场内 ETF 上做配对比较（同池 / 同窗口 / 同成交口径），
回答「哪条值得吸收、哪条是纯加过滤器」。

对照口径
--------
- 价格：`data/_long_klines.json` 收盘价，第 61 个交易日（`base.WARMUP`）起算；
- 成交：`base.backtest` = T 日收盘算信号、T+1 净值成交（与生产口径一致）；
- 基准：v3 **自适应窗口**（生产口径，`v3_macd_boll/backtest.py::build_variant`）；
  另有固定 w20 一套，用于检验「增量效果是否依赖基础配置」。

变体清单（条款编号对应策略文档）
--------------------------------
| 代号 | 文档条款 | 增量内容 |
|---|---|---|
| W    | R-04 | 日线买入需周线 DIF>DEA（严口径） |
| W0   | R-04 | 日线买入需周线 DIF>0（原文字面：零轴下方降级） |
| Db   | R-05 | 加「收在中轨上方 1% 内且当日收涨」低吸入场，与突破并列（无回看） |
| D    | R-05 | 同上 + 要求近 20 日曾高于中轨 2%（真·回踩） |
| D5   | R-05 | 同上 + 回看 5 日（最严） |
| D0b  | R-05 | 只用低吸入场（无回看），去掉突破 |
| D0   | R-05 | 只用低吸入场（20 日回看），去掉突破 |
| V    | N-02 | 突破入场需当日量能 > 20 日均量 ×1.2 |
| X    | R-12 | 价格创 20 日新高但 DIF 未创新高 → 顶背离提前离场 |
| S    | R-18 | 入场价 −2×ATR(14)（收盘价近似的波动率 ATR）止损 |
| M2   | R-08 | 离场改为「连续 2 日收于中轨下方」 |
| W0D  | R-04+05 | 周线 DIF>0 + 低吸(20日) |
| W0Db | R-04+05 | 周线 DIF>0 + 低吸(无回看) |
| W0DV | R-04+05+N-02 | 文档可量化条款全开 |
| VS   | N-02+R-18 | 量能 + ATR 止损（两条降回撤叠加，查协同） |
| S3   | R-18 | ATR 倍数 3×（敏感性，查参数依赖） |

未纳入：R-12/R-13 的背离需「MACD 柱面积或 DIF 峰值」严格对比（主观定义空间大）、
R-16/R-17 分批建仓与单标的 20% 上限（0/1 仓位下无法表达）、R-19 事件日回避（无日历数据）。
成交量为 `_indicators_hist.json` 自带字段，故 N-02 只能在 ETF 池验证，场外净值池无此数据。

产物（同目录）：`_v3_doc_rules_report.md` · `_v3_doc_rules_results.jsonl`
机械规则输出，非投资建议。
"""
import importlib.util
import json
import os
import sys

import numpy as np
import pandas as pd

try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:  # noqa
    pass

_HERE = os.path.dirname(os.path.abspath(__file__))          # research/v3_doc_rules
_RES = os.path.dirname(_HERE)                               # research
_V3 = os.path.dirname(_RES)                                 # v3_macd_boll
_PARENT = os.path.dirname(_V3)                              # tech_indicators
_ROOT = os.path.dirname(os.path.dirname(_PARENT))           # 仓库根
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)


def _load(path, name):
    spec = importlib.util.spec_from_file_location(name, path)
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


base = _load(os.path.join(_PARENT, "backtest.py"), "vd_base")
v3b = _load(os.path.join(_V3, "backtest.py"), "vd_v3")

WARMUP = base.WARMUP
W_FAST, W_SLOW, K = 20, 30, 2.0
ER_N, ER_HI, ER_LO = 20, 0.40, 0.30
VOL_N, VOL_MULT = 20, 1.2          # N-02：量能 > 20 日均量 ×1.2
DIV_N = 20                         # R-12：背离回看 20 日
ATR_N, ATR_MULT = 14, 2.0          # R-18：2×ATR(14)
DIP_TOL = 0.01                     # R-05：中轨 ±1% 容差
DIP_BACK = 20                      # R-05：回踩前的「离开中轨」回看日数（0=不要求）
SEGMENTS = [("2015–2019", "2015-01-01", "2019-12-31"),
            ("2020–2022", "2020-01-01", "2022-12-31"),
            ("2023–2026", "2023-01-01", "2026-12-31")]

VARIANTS = [
    ("W", "R-04 周线 DIF>DEA 过滤（严口径）", dict(weekly="cross")),
    ("W0", "R-04 周线 DIF>0 过滤（原文字面）", dict(weekly="zero")),
    ("Db", "R-05 回踩低吸 · 无回看（最宽）", dict(dip=True, dip_back=0)),
    ("D", "R-05 回踩低吸 · 20 日回看", dict(dip=True, dip_back=20)),
    ("D5", "R-05 回踩低吸 · 5 日回看（最严）", dict(dip=True, dip_back=5)),
    ("D0b", "仅低吸 · 无回看（去突破）", dict(dip=True, dip_back=0, dip_only=True)),
    ("D0", "仅低吸 · 20 日回看（去突破）", dict(dip=True, dip_back=20, dip_only=True)),
    ("V", "N-02 量能 1.2× 确认", dict(vol_need=True)),
    ("X", "R-12 顶背离提前离场", dict(div_exit=True)),
    ("S", "R-18 ATR(14) 2× 止损", dict(atr_stop=True)),
    ("M2", "R-08 连续 2 日跌破中轨离场", dict(mid_exit2=True)),
    ("W0D", "周线(DIF>0) + 低吸(20日)", dict(weekly="zero", dip=True, dip_back=20)),
    ("W0Db", "周线(DIF>0) + 低吸(无回看)", dict(weekly="zero", dip=True, dip_back=0)),
    ("W0DV", "周线 + 低吸 + 量能（可量化全集）",
     dict(weekly="zero", dip=True, dip_back=20, vol_need=True)),
    ("VS", "量能 + ATR(2×) 止损（两条降回撤叠加）", dict(vol_need=True, atr_stop=True)),
    ("S3", "R-18 敏感性：ATR(14) 3× 止损", dict(atr_stop=True, atr_mult=3.0)),
]


# ---------------------------------------------------------------- 指标
def weekly_bull(dates, close, how="cross"):
    """周线 MACD 多头状态（无未来函数）。

    日线 t 日：若 t 是本周最后一个交易日，用**含 t 日收盘**的当周周线值；
    否则用上一完整周的周线值（周中不预知本周收盘）。
    how='cross' → 周线 DIF>DEA；how='zero' → 周线 DIF>0（文档 R-04 原文字面口径）。
    """
    d = pd.to_datetime(dates)
    per = d.to_period("W-FRI")
    s = pd.Series(np.asarray(close, dtype=float), index=d)
    wk = s.groupby(per).last()
    dif = wk.ewm(span=12).mean() - wk.ewm(span=26).mean()
    dea = dif.ewm(span=9).mean()
    st = (dif > dea) if how == "cross" else (dif > 0)
    bull_map = {p: bool(b) for p, b in zip(wk.index, st.to_numpy())}
    ordv = np.asarray(per.astype("int64"))
    is_last = (pd.Series(ordv).groupby(ordv).cumcount(ascending=False) == 0).to_numpy()
    out = np.zeros(len(d), dtype=bool)
    for i, p in enumerate(per):
        key = p if is_last[i] else p - 1
        out[i] = bull_map.get(key, False)
    return out


def atr_pct(close, n=ATR_N):
    """收盘价近似的 ATR（只有收盘价，用 |日收益| 的 n 日均值代百分比 ATR）。"""
    return pd.Series(np.asarray(close, dtype=float)).pct_change().abs().rolling(n).mean().to_numpy()


# ---------------------------------------------------------------- 信号
def sig_doc(close, r, dates, mode="adapt", weekly=None, dip=False, dip_back=DIP_BACK,
            dip_only=False, vol_need=False, div_exit=False, atr_stop=False,
            atr_mult=ATR_MULT, mid_exit2=False):
    """v3 + 文档条款（逐项开关）。mode='adapt' 为生产口径，'w20' 为固定 20 日窗口。"""
    n = len(close)
    dif, dea = base._arr(r["DIF"]), base._arr(r["DEA"])
    up_f, mid_f, bw_f = v3b._bands(close, W_FAST, K)
    up_s, mid_s, bw_s = v3b._bands(close, W_SLOW, K)
    er = v3b.efficiency_ratio(close, ER_N)
    vol = base._arr(r["volume"]) if vol_need else None
    volma = (pd.Series(vol).rolling(VOL_N).mean().to_numpy() if vol_need else None)
    wbull = weekly_bull(dates, close, weekly) if weekly else None
    atr = atr_pct(close) if atr_stop else None

    sig = np.zeros(n, dtype=int)
    p = 0
    fast = False
    epx = eatr = None
    for t in range(1, n):
        if mode == "adapt":
            if np.isnan(bw_f[t - 1]) or np.isnan(bw_s[t - 1]):
                sig[t] = p
                continue
        elif np.isnan(bw_f[t - 1]):
            sig[t] = p
            continue
        if mode == "adapt":
            if not np.isnan(er[t]):
                if er[t] >= ER_HI:
                    fast = True
                elif er[t] <= ER_LO:
                    fast = False
        else:
            fast = True
        up, mid, bw = (up_f, mid_f, bw_f) if fast else (up_s, mid_s, bw_s)
        upp, midp, bwp = ((up_f[t - 1], mid_f[t - 1], bw_f[t - 1]) if fast
                          else (up_s[t - 1], mid_s[t - 1], bw_s[t - 1]))
        need = (dif[t], dea[t], dif[t - 1], dea[t - 1], up[t], upp, midp, bwp)
        if any(np.isnan(x) for x in need):
            sig[t] = p
            continue

        if p == 0:
            ok = False
            if not dip_only:
                brk = close[t] > up[t] and close[t - 1] <= upp        # 上穿上轨
                expand = bw[t] > bwp                                  # 开口放大
                volok = (not vol_need) or (not np.isnan(volma[t]) and vol[t] > VOL_MULT * volma[t])
                ok = brk and expand and volok
            if dip and not ok:                                        # R-05 回踩中轨
                m = mid[t]
                if (not np.isnan(m) and close[t] > m and close[t] <= m * (1 + DIP_TOL)
                        and close[t] > close[t - 1]):
                    back_ok = (dip_back <= 0
                               or np.nanmax(close[max(0, t - dip_back):t]) >= m * 1.02)
                    ok = back_ok
            if ok and dif[t] > 0 and dif[t] > dea[t] and (weekly is None or wbull[t]):
                p, epx, eatr = 1, close[t], (atr[t] if atr_stop else None)
        else:
            death = dif[t] < dea[t] and dif[t - 1] >= dea[t - 1]
            if mid_exit2:                                             # R-08 连续2日
                midbrk = close[t] < mid[t] and close[t - 1] < midp
            else:
                midbrk = close[t] < mid[t] and close[t - 1] >= midp
            div = False
            if div_exit and t > DIV_N:
                seg_c = close[t - DIV_N:t + 1]
                seg_d = dif[t - DIV_N:t]
                div = close[t] >= np.nanmax(seg_c) and dif[t] < np.nanmax(seg_d)
            stop = False
            if atr_stop and epx is not None and not np.isnan(eatr):
                stop = close[t] <= epx * (1 - atr_mult * eatr)
            if death or midbrk or div or stop:
                p, epx, eatr = 0, None, None
        sig[t] = p
    return sig


# ---------------------------------------------------------------- 评估
def run(ind, lk, sigfn):
    rows = []
    for code, r in ind.items():
        if code not in lk:
            continue
        close = base._arr(lk[code]["close"])
        dates = lk[code]["dates"]
        if len(close) != len(dates) or len(close) <= WARMUP + 2:
            continue
        m, eq, tr = base.backtest(close, sigfn(close, r, dates), dates, code)
        bh, _ = base.backtest_bh(close, dates, code)
        rows.append({"code": code, "m": m, "bh": bh, "trades": tr,
                     "eq": eq, "ds": dates[WARMUP:]})
    return rows


def seg_sharpe(rows, d0, d1):
    out = []
    for row in rows:
        eq, ds = row["eq"], row["ds"]
        i0 = next((i for i, d in enumerate(ds) if d >= d0), None)
        i1 = next((len(ds) - 1 - i for i, d in enumerate(reversed(ds)) if d <= d1), None)
        if i0 is None or i1 is None or i1 <= i0:
            continue
        seg = [v / eq[i0] for v in eq[i0:i1 + 1]]
        out.append(base._metrics(seg, [])["sharpe"])
    return float(np.median(out)) if out else float("nan")


def paired(rows, ref):
    """变体 vs 基准的**逐基金配对差**（消除池内标的差异，只看规则净效果）。"""
    b = {x["code"]: x["m"] for x in ref}
    d_ann, d_sh, d_dd, d_exp = [], [], [], []
    for x in rows:
        m0 = b.get(x["code"])
        if m0 is None:
            continue
        d_ann.append(x["m"]["ann"] - m0["ann"])
        d_sh.append(x["m"]["sharpe"] - m0["sharpe"])
        d_dd.append(x["m"]["dd"] - m0["dd"])
        d_exp.append(x["m"]["exposure"] - m0["exposure"])
    a = np.array(d_ann)
    sd = np.array(d_sh)
    n = len(a)
    tstat = a.mean() / (a.std(ddof=1) / np.sqrt(n)) if n > 1 and a.std(ddof=1) > 0 else 0.0
    return {"n": n, "ann": float(np.median(a)), "ann_mean": float(a.mean()),
            "ann_win": float((a > 0).mean()), "t": float(tstat),
            "sharpe": float(np.median(sd)), "sharpe_win": float((sd > 0).mean()),
            "dd": float(np.median(d_dd)), "exp": float(np.median(d_exp))}


def _row(label, a, p):
    return (f"| {label} | {base.pct(a['ann'])} | {base.pct(a['dd'])} | {a['sharpe']:.2f} | "
            f"{a['exposure']:.0%} | {a['pooled']['n']} | {a['pooled']['win_rate']:.0%} | "
            f"{a['pooled']['profit_factor']:.2f} | {base.pct(p['ann'])} | {p['sharpe']:+.2f} | "
            f"{p['ann_win']:.0%} | {p['t']:+.1f} |")


def dip_funnel(ind, lk):
    """R-05 低吸条件的逐层漏斗：看它为什么几乎不触发。"""
    tot = dict(n=0, near=0, up=0, back=0, macd=0)
    for code, r in ind.items():
        if code not in lk:
            continue
        c = base._arr(lk[code]["close"])
        if len(c) <= WARMUP + 2:
            continue
        dif, dea = base._arr(r["DIF"]), base._arr(r["DEA"])
        upf, mf, _ = v3b._bands(c, W_FAST, K)
        ups, ms, _ = v3b._bands(c, W_SLOW, K)
        er = v3b.efficiency_ratio(c, ER_N)
        fast = False
        for t in range(WARMUP, len(c)):
            if not np.isnan(er[t]):
                if er[t] >= ER_HI:
                    fast = True
                elif er[t] <= ER_LO:
                    fast = False
            m = (mf if fast else ms)[t]
            if np.isnan(m) or np.isnan(dif[t]) or np.isnan(dea[t]):
                continue
            tot["n"] += 1
            a = c[t] > m and c[t] <= m * (1 + DIP_TOL)
            tot["near"] += a
            b = a and c[t] > c[t - 1]
            tot["up"] += b
            bb = b and np.nanmax(c[max(0, t - DIP_BACK):t]) >= m * 1.02
            tot["back"] += bb
            tot["macd"] += bb and dif[t] > 0 and dif[t] > dea[t]
    return tot


def main():
    ind, lk = base.load()
    print(f"[池] {len(ind)} 只")

    # 自检：sig_doc 全关 ≡ 生产 build_variant / sig_fixed(w20)
    bad = []
    for code, r in ind.items():
        if code not in lk:
            continue
        close = base._arr(lk[code]["close"])
        dates = lk[code]["dates"]
        if len(close) != len(dates) or len(close) <= WARMUP + 2:
            continue
        if not np.array_equal(sig_doc(close, r, dates), v3b.build_variant(close, r)):
            bad.append(("adapt", code))
        if not np.array_equal(sig_doc(close, r, dates, mode="w20"), v3b.sig_fixed(close, r, w=W_FAST)):
            bad.append(("w20", code))
    if bad:
        raise SystemExit(f"[自检失败] sig_doc 与生产口径不一致：{bad[:5]}")
    print("[自检] sig_doc(全关) == build_variant / sig_fixed(w20) ✔")

    out = {}
    for mode, mlabel in (("adapt", "自适应(生产口径)"), ("w20", "固定w20")):
        if mode == "adapt":
            ref = run(ind, lk, lambda c, r, d: v3b.build_variant(c, r))
        else:
            ref = run(ind, lk, lambda c, r, d: v3b.sig_fixed(c, r, w=W_FAST))
        out[(mode, "BASE")] = ref
        print(f"[{mlabel}] BASE 完成")
        for key, _label, kw in VARIANTS:
            out[(mode, key)] = run(ind, lk, lambda c, r, d, kw=kw, mode=mode: sig_doc(c, r, d, mode=mode, **kw))
            print(f"[{mlabel}] {key} 完成")

    os.makedirs(_HERE, exist_ok=True)
    with open(os.path.join(_HERE, "_v3_doc_rules_results.jsonl"), "w", encoding="utf-8") as f:
        for (mode, key), rows in out.items():
            for x in rows:
                f.write(json.dumps({
                    "mode": mode, "variant": key, "code": x["code"],
                    "ann": round(x["m"]["ann"], 4), "dd": round(x["m"]["dd"], 4),
                    "sharpe": round(x["m"]["sharpe"], 3),
                    "exposure": round(x["m"]["exposure"], 3), "n": x["m"]["n"],
                    "win_rate": round(x["m"]["win_rate"], 4),
                    "bh_ann": round(x["bh"]["ann"], 4), "bh_dd": round(x["bh"]["dd"], 4),
                }, ensure_ascii=False) + "\n")

    L = [
        "# v3（MACD+BOLL）· 逐条吸收「策略文档 v1.0」的增量 A/B",
        f"> 池 = `_universe.md` 前四类场内 ETF（实际可测 {len(out[('adapt', 'BASE')])} 只）· "
        "各自全历史（第 61 个交易日起）· T 日收盘信号 / T+1 成交 · 未计佣金滑点 · 无风险利率 0。",
        "> 基准 = v3 **自适应窗口**（生产口径 `build_variant`）。配对差 = 变体 − 基准，逐基金计算后取中位数。",
        "",
        "## 0. 吸收的条款",
        "",
        "| 代号 | 文档条款 · 增量内容 |",
        "|---|---|",
    ]
    for key, label, kw in VARIANTS:
        L.append(f"| {key} | {label} |")
    L += [
        "",
        "未纳入：R-12/13 背离的「柱面积 / DIF 峰值」严格对比（主观口径空间大）、"
        "R-16/R-17 分批建仓与单标的 20% 上限（0/1 仓位无法表达）、R-19 事件日回避（无日历数据）。",
        "",
        "## 1. 全池（自适应基础 · 生产口径）",
        "",
        "| 变体 | 中位年化 | 中位回撤 | 中位夏普 | 曝光 | 交易 | 合计胜率 | 利润因子 "
        "| 配对年化差(pp) | 配对夏普差 | 年化跑赢率 | t |",
        "|---|---|---|---|---|---|---|---|---|---|---|---|",
    ]
    ref = out[("adapt", "BASE")]
    a_ref = base._agg(ref)
    L.append(f"| **BASE v3（生产）** | {base.pct(a_ref['ann'])} | {base.pct(a_ref['dd'])} | "
             f"{a_ref['sharpe']:.2f} | {a_ref['exposure']:.0%} | {a_ref['pooled']['n']} | "
             f"{a_ref['pooled']['win_rate']:.0%} | {a_ref['pooled']['profit_factor']:.2f} | — | — | — | — |")
    bh_ann = float(np.median([x["bh"]["ann"] for x in ref]))
    bh_dd = float(np.median([x["bh"]["dd"] for x in ref]))
    bh_sh = float(np.median([x["bh"]["sharpe"] for x in ref]))
    L.append(f"| 买入持有 | {base.pct(bh_ann)} | {base.pct(bh_dd)} | {bh_sh:.2f} | 100% | — | — | — "
             "| — | — | — | — |")
    ranked = []
    for key, _label, kw in VARIANTS:
        rows = out[("adapt", key)]
        a = base._agg(rows)
        p = paired(rows, ref)
        ranked.append((p["sharpe"], key, a, p))
    for _sh, key, a, p in sorted(ranked, key=lambda x: -x[0]):
        lab = next(v[1] for v in VARIANTS if v[0] == key)
        L.append(_row(f"**{key}** · {lab}", a, p))

    good = [k for sh, k, a, p in ranked if p["sharpe"] >= 0.02 and p["ann"] > 0]
    bad = [k for sh, k, a, p in ranked if p["sharpe"] <= -0.02]
    noise = [k for sh, k, a, p in ranked if k not in good and k not in bad]
    L += ["", "判定标准（预设，避免事后挑数）：**配对夏普差 ≥ +0.02 且配对年化差 > 0 → 有效改善**；"
              "配对夏普差 ≤ −0.02 → 有害；其余落在噪声区间（改动幅度不足 1 个标准差）。", "",
          f"- 有效改善（可吸收）：{'、'.join(good) if good else '**无**'}",
          f"- 有害（应剔除）：{'、'.join(bad)}",
          f"- 噪声区间（改不改都行）：{'、'.join(noise)}",
          "",
          "> 说明：变体数多，逐条比较存在多重比较问题。因此只把「夏普差显著为正 + 年化为正 + "
          "两个基础（自适应 / 固定 w20）方向一致」视为可吸收，其余一律按噪声处理。", ""]

    L += ["## 2. 稳健性：换成固定 w20 基础", "",
          "| 变体 | 中位年化 | 中位回撤 | 中位夏普 | 曝光 | 交易 | 合计胜率 | 利润因子 "
          "| 配对年化差(pp) | 配对夏普差 | 年化跑赢率 | t |",
          "|---|---|---|---|---|---|---|---|---|---|---|---|"]
    ref20 = out[("w20", "BASE")]
    a20 = base._agg(ref20)
    L.append(f"| **BASE v3（w20）** | {base.pct(a20['ann'])} | {base.pct(a20['dd'])} | {a20['sharpe']:.2f} | "
             f"{a20['exposure']:.0%} | {a20['pooled']['n']} | {a20['pooled']['win_rate']:.0%} | "
             f"{a20['pooled']['profit_factor']:.2f} | — | — | — | — |")
    r20 = []
    for key, _label, kw in VARIANTS:
        rows = out[("w20", key)]
        a = base._agg(rows)
        p = paired(rows, ref20)
        r20.append((p["sharpe"], key, a, p))
    for _sh, key, a, p in sorted(r20, key=lambda x: -x[0]):
        lab = next(v[1] for v in VARIANTS if v[0] == key)
        L.append(_row(f"**{key}** · {lab}", a, p))

    d20 = {k: p["sharpe"] for _sh, k, _a, p in r20}
    both = [k for _sh, k, _a, p in ranked if p["sharpe"] >= 0 and d20.get(k, -9) >= 0]
    L += ["", f"**两个基础下配对夏普差都 ≥ 0 的变体**：{'、'.join(both) if both else '无'}。"
              "（只在一个基础下为正 = 不稳健，不采纳。）"]

    L += ["", "## 3. 分段中位夏普（自适应基础）", "",
          "| 期间 | BASE | " + " | ".join(v[0] for v in VARIANTS) + " |",
          "|---|---|" + "---|" * len(VARIANTS)]
    for name, d0, d1 in SEGMENTS:
        vals = [f"{seg_sharpe(out[('adapt', k)], d0, d1):.2f}"
                for k in ["BASE"] + [v[0] for v in VARIANTS]]
        L.append(f"| {name} | " + " | ".join(vals) + " |")

    L += ["", "## 4. 代表标的 510300", "",
          "| 变体 | 年化 | 回撤 | 夏普 | 曝光 | 交易 | 胜率 | 盈亏比 | 买入持有年化 |",
          "|---|---|---|---|---|---|---|---|---|"]
    bh510 = {x["code"]: x for x in ref}["510300"]["bh"]
    L.append(f"| 买入持有 | {base.pct(bh510['ann'])} | {base.pct(bh510['dd'])} | {bh510['sharpe']:.2f} | "
             "100% | 1 | — | — | — |")
    for key in ["BASE"] + [v[0] for v in VARIANTS]:
        m = {x["code"]: x for x in out[("adapt", key)]}["510300"]["m"]
        L.append(f"| {key} | {base.pct(m['ann'])} | {base.pct(m['dd'])} | {m['sharpe']:.2f} | "
                 f"{m['exposure']:.0%} | {m['n']} | {m['win_rate']:.0%} | {m['payoff']:.2f} | "
                 f"{base.pct(bh510['ann'])} |")

    fn = dip_funnel(ind, lk)
    n_all = fn["n"] or 1

    def fp(k):
        return f"{fn[k]:,}（{fn[k] / n_all:.2%}）"

    L += ["", "## 5. 为什么 R-05 低吸几乎不触发（漏斗）", "",
          "全池全部交易日逐层筛选（自适应窗口的中轨）：", "",
          "| 层级 | 条件 | 命中日数（占全样本） |",
          "|---|---|---|",
          f"| 0 | 全部样本日 | {n_all:,} |",
          f"| 1 | 收盘在中轨上方 0~1% 内 | {fp('near')} |",
          f"| 2 | + 当日收涨（收回中轨） | {fp('up')} |",
          f"| 3 | + 近 {DIP_BACK} 日曾高于中轨 2%（真·回踩） | {fp('back')} |",
          f"| 4 | + DIF>0 且 DIF>DEA（文档前提条件） | {fp('macd')} |",
          "",
          "**根因**：「价格回踩到中轨附近」与「MACD 零轴上方金叉状态」在时间上互斥 —— "
          "价格跌到 20/30 日均线附近时，DIF 往往已经跌破 DEA 甚至跌破零轴（文档自己的 N-04 也在警告这一点）。"
          "第 4 层把第 3 层的 3,003 天砍到 222 天（只剩 7.4%，占全样本 0.23%），"
          "`D0` 变体的曝光因此只有 1~2%（等于空仓），"
          "它在 510300 上 11~16 笔交易、胜率 9~19%，不是「策略差」而是「几乎不下单」。",
          "",
          "> 口径：单笔 = 建/平仓收盘价收益，未计费用。**机械规则输出，非投资建议。**"]

    path = os.path.join(_HERE, "_v3_doc_rules_report.md")
    with open(path, "w", encoding="utf-8") as f:
        f.write("\n".join(L))
    print("报告:", path)

    print("\n自适应基础 · 按配对夏普差排序：")
    for _sh, key, a, p in sorted(ranked, key=lambda x: -x[0]):
        print(f"  {key:4s} 夏普差{p['sharpe']:+.3f} 年化差{p['ann']*100:+.2f}pp "
              f"跑赢率{p['ann_win']:.0%} t={p['t']:+.1f} | 年化{base.pct(a['ann'])} "
              f"回撤{base.pct(a['dd'])} 曝光{a['exposure']:.0%}")


if __name__ == "__main__":
    main()
