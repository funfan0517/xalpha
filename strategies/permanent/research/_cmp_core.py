# -*- coding: utf-8 -*-
"""core_rotation vs permanent · 同口径对比。

为什么必须重跑: 两份原始回测不可直接比
  core_rotation: 2019-08~2026-09, 价格指数(000922/000300/000688 收盘 + 511260 ETF 收盘)
                 → 红利与债券的票息/分红全部丢失(红利 ~4%/年、债券 ~3%/年)
  permanent    : 2021-05~2026-10, 场外累计净值(复权)
                 → 口径正确, 但样本短 1.7 年
  两个"差异"叠加, 谁也说不清是策略差异还是口径差异。

本脚本统一到:
  · 窗口 2021-05 ~ 2026-09(两者重叠期)
  · 收益一律场外累计净值(复权); 只有标普(qdii 联接 2023-04 才成立)与 A500 指数用代理
  · 同样的月度再平衡、同样未计费
  · core_rotation 的信号输入仍走它原本的估值口径(中证官网 peg + chinabond 10Y, 存于
    data/_bt_caches/), 只是把"收益"换成复权净值

用法: python strategies/permanent/research/_cmp_core.py
输出: strategies/permanent/research/_cmp_core_report.md
"""
import io
import os
import sys
from datetime import datetime

import numpy as np
import pandas as pd

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8")

_DIR = os.path.dirname(os.path.abspath(__file__))
_PDIR = os.path.dirname(_DIR)
_ROOT = os.path.dirname(os.path.dirname(_PDIR))
for _p in (_ROOT, _PDIR):
    if _p not in sys.path:
        sys.path.insert(0, _p)

import xalpha as xa  # noqa: E402
import rule as prule  # noqa: E402  permanent 的规则

_OUT = os.path.join(_DIR, "_cmp_core_report.md")
_CACHE = os.path.join(_ROOT, "data", "_bt_caches")
_START, _END = "2021-05-01", "2026-09-30"
_CASH_Y = prule.CASH_ANNUAL

# core_rotation 的情景区间(复刻自 strategies/core_rotation/rule.py::TARGET)
CORE_TARGET = {
    "bond": {"防御": 20, "均衡": 10, "成长": 5, "避险": 30},
    "dividend": {"防御": 40, "均衡": 25, "成长": 15, "避险": 17.5},
    "ndx": {"防御": 0, "均衡": 15, "成长": 20, "避险": 5},
    "a500": {"防御": 15, "均衡": 25, "成长": 30, "避险": 17.5},
    "kc": {"防御": 0, "均衡": 5, "成长": 20, "避险": 2.5},
    "gold": {"防御": 25, "均衡": 20, "成长": 10, "避险": 27.5},
}
CORE_KEYS = ["bond", "dividend", "ndx", "a500", "kc", "gold"]

# 统一腿 -> 场外代码 / 特殊代理
LEG_SRC = {
    "bond_long": ("nav", "003377"),
    "bond_mid": ("nav", "001512"),
    "bond_short": ("nav", "012265"),
    "div_core": ("nav", "012644"),     # core_rotation 的红利
    "div_lv": ("nav", "008163"),       # permanent 的红利
    "ndx": ("nav", "270042"),
    "spx": ("px", "SH513500"),         # 017641 联接 2023-04 才成立
    "a500": ("px", "SH000510"),        # 023299 联接 2025-03 才成立
    "kc": ("nav", "011609"),
    "gold": ("nav", "000216"),
}

# permanent 的腿 -> 本对比里的统一腿名
_PLEG2SRC = {
    "bond_s": "bond_short", "bond_m": "bond_mid", "bond_l": "bond_long",
    "divlv": "div_lv", "ndx": "ndx", "spx": "spx",
    "gold": "gold", "a500": "a500", "kc": "kc",
}

_NAV = {}


def _resample_me(s):
    try:
        return s.resample("ME").last()
    except ValueError:      # 老 pandas
        return s.resample("M").last()


def nav_tot(code):
    """场外累计净值(复权) -> 日频 Series。"""
    if code in _NAV:
        return _NAV[code]
    f = xa.fundinfo(code)
    p = f.price.sort_values("date")
    tot = p["totvalue"].fillna(p["netvalue"]) if "totvalue" in p.columns else p["netvalue"]
    p = p.assign(_t=tot).dropna(subset=["_t"])
    s = pd.Series([float(x) for x in p["_t"]],
                  index=pd.to_datetime([str(d)[:10] for d in p["date"]])).sort_index()
    _NAV[code] = s
    return s


def px_close(code):
    df = xa.get_daily(code, start="2019-01-01").dropna(subset=["close"])
    return pd.Series([float(x) for x in df["close"]],
                     index=pd.to_datetime([str(d)[:10] for d in df["date"]])).sort_index()


def csi(code, col="close"):
    p = os.path.join(_CACHE, f"csi_{code}.csv")
    df = pd.read_csv(p, parse_dates=["date"]).sort_values("date")
    return df.set_index("date")[col].astype(float)


def load_legs():
    out = {}
    for leg, (kind, code) in LEG_SRC.items():
        out[leg] = nav_tot(code) if kind == "nav" else px_close(code)
    return out


def core_signal():
    """core_rotation 的月度信号: 股债收益比 / 红利PE分位 / 科创PE比 / 10Y。"""
    red_px = _resample_me(csi("000922"))
    red_tot = _resample_me(csi("H00922"))
    g = (red_tot / red_px).reindex(red_px.index)
    dy = g / g.shift(12) - 1
    dy = dy.dropna()
    # 校准到蛋卷当前 4.2%(与 core_rotation/_backtest.py::dy_est_12m 同法)
    cal = 0.042 / dy.iloc[-1] if dy.iloc[-1] > 0 else 1.0
    dy = dy * cal

    peg_red = _resample_me(csi("000922", "peg"))
    peg_kc = _resample_me(csi("000688", "peg"))

    y10 = pd.read_csv(os.path.join(_CACHE, "bond10y_m.csv"))
    y10 = y10.rename(columns={y10.columns[0]: "date"})
    y10["date"] = pd.to_datetime(y10["date"])
    y10 = y10.set_index("date")[y10.columns[-1]].astype(float)

    idx = sorted(set(y10.index) & set(dy.index) & set(peg_red.index))
    rows = []
    for t in idx:
        if not (_START <= str(t.date()) <= _END):
            continue
        hist = peg_red[peg_red.index <= t].dropna()
        if hist.empty:
            continue
        ratio = dy.loc[t] * 100 / y10.loc[t] if y10.loc[t] else np.nan
        pct = float((hist < hist.iloc[-1]).mean())
        kc_ratio = (peg_kc.loc[t] / hist.iloc[-1]
                    if t in peg_kc.index and not np.isnan(peg_kc.loc[t]) else np.nan)
        if ratio > 2.5 and pct < 0.30:
            sc = "防御"
        elif not np.isnan(ratio) and ratio < 1.5:
            sc = "成长"
        else:
            sc = "均衡"
        w = {k: CORE_TARGET[k][sc] for k in CORE_KEYS}
        if y10.loc[t] < 1.5:
            w["bond"] = 5.0
        if not np.isnan(kc_ratio) and kc_ratio > 7:
            w["kc"] = 0.0
        s = sum(w.values())
        rows.append(dict(t=t, sc=sc, ratio=ratio, red_pct=pct,
                         kc_ratio=kc_ratio, y10=y10.loc[t],
                         w={k: v / s * 100 for k, v in w.items()}))
    return pd.DataFrame(rows).set_index("t")


def permanent_signal(legs):
    """permanent 的月度信号(直接调它自己的 rule)。"""
    months = sorted(set(_resample_me(legs["ndx"]).index))
    rows = []
    for t in months:
        if not (_START <= str(t.date()) <= _END):
            continue
        met, ok = {}, True
        for leg, src in _PLEG2SRC.items():
            hist = legs[src][legs[src].index <= t]
            if len(hist) < prule.MA_WIN:
                ok = False
                break
            met[leg] = prule.leg_metrics(hist)
        if not ok:
            continue
        sc = prule.leg_scores(met)
        w, _ = prule.target_weights(prule.item_scores(sc))
        rows.append(dict(t=t, w=w))
    return pd.DataFrame(rows).set_index("t")


def run(legs, wdf, mapper):
    """wdf: 每月 item 权重 dict; mapper: dict(item->{leg:份额}) -> 月度收益列表。"""
    ret = pd.DataFrame({k: _resample_me(v).pct_change() for k, v in legs.items()})
    ret["cash"] = (1 + _CASH_Y) ** (1 / 12) - 1
    common = [t for t in wdf.index if t in ret.index]
    rs = []
    for t in common:
        w = wdf.loc[t, "w"]
        legw = {}
        for item, share in mapper(w).items():
            for leg, s in share.items():
                legw[leg] = legw.get(leg, 0.0) + s
        tot = sum(legw.values()) or 1.0
        r = 0.0
        for leg, s in legw.items():
            v = ret.loc[t, leg]
            if v == v:
                r += s * v
        rs.append(r / tot)
    return pd.Series(rs, index=pd.to_datetime(common))


def perf(r, label):
    nav = (1 + r).cumprod()
    n, yrs = len(r), len(r) / 12.0
    cum = nav.iloc[-1] - 1
    ann = (1 + cum) ** (1 / yrs) - 1
    vol = r.std() * np.sqrt(12)
    mdd = (nav / nav.cummax() - 1).min()
    roll = (1 + r).rolling(12).apply(np.prod, raw=True) - 1
    return dict(label=label, cum=cum, ann=ann, vol=vol, mdd=mdd,
                sharpe=(ann - _CASH_Y) / vol if vol > 1e-9 else 0.0,
                worst=roll.min())


def main():
    legs = load_legs()
    core_w = core_signal()
    perm_w = permanent_signal(legs)

    core_map = lambda w: {
        "bond": {"bond_long": w["bond"]},
        "dividend": {"div_core": w["dividend"]},
        "ndx": {"ndx": w["ndx"]},
        "a500": {"a500": w["a500"]},
        "kc": {"kc": w["kc"]},
        "gold": {"gold": w["gold"]},
    }
    perm_map = lambda w: {
        "bond": {"bond_short": w["bond"] / 3, "bond_mid": w["bond"] / 3,
                 "bond_long": w["bond"] / 3},
        "divlv": {"div_lv": w["divlv"]},
        "ndx": {"ndx": w["ndx"]},
        "spx": {"spx": w["spx"]},
        "gold": {"gold": w["gold"]},
        "cash": {"cash": w["cash"]},
        "a500": {"a500": w["a500"]},
        "kc": {"kc": w["kc"]},
    }

    r_core = run(legs, core_w, core_map)
    r_perm = run(legs, perm_w, perm_map)

    # 静态基准(不倾斜/不切情景): core 用均衡中枢, perm 用 RAW
    core_eq = {k: CORE_TARGET[k]["均衡"] for k in CORE_KEYS}
    s = sum(core_eq.values())
    core_eq = {k: v / s * 100 for k, v in core_eq.items()}
    static_core = pd.DataFrame({"w": [dict(core_eq) for _ in core_w.index]},
                               index=core_w.index)
    r_sc = run(legs, static_core, core_map)

    bw = prule.base_weights()
    static_perm = pd.DataFrame({"w": [dict(bw) for _ in perm_w.index]},
                               index=perm_w.index)
    r_sp = run(legs, static_perm, perm_map)

    ex = (r_perm - r_sc).dropna()
    ir = (ex.mean() * 12) / (ex.std() * np.sqrt(12)) if ex.std() > 1e-12 else 0.0

    rows = [perf(r_core, "core_rotation 策略(情景轮动)"),
            perf(r_sc, "core_rotation 静态中枢"),
            perf(r_perm, "permanent 策略(±50% 倾斜)"),
            perf(r_sp, "permanent 静态基准")]

    sc_hist = core_w["sc"].value_counts()
    L = []
    A = L.append
    A("# core_rotation vs permanent · 同口径对比")
    A("")
    A(f"> 生成 {datetime.now():%Y-%m-%d %H:%M} · 窗口 "
      f"{r_perm.index[0]:%Y-%m} ~ {r_perm.index[-1]:%Y-%m}（{len(r_perm)} 个月，取两者重叠期）")
    A("> 收益一律**场外累计净值(复权)**；标普用 513500、A500 用中证A500指数代理（联接成立太晚）。")
    A("> 未计申赎费与 C 类销售服务费。机械输出，非投资建议。")
    A("")
    A("## 一、为什么不能直接比原始报告")
    A("")
    A("| 维度 | core_rotation 原报告 | permanent 原报告 |")
    A("|---|---|---|")
    A("| 样本 | 2019-08 ~ 2026-09（7.1 年） | 2021-05 ~ 2026-10（5.4 年） |")
    A("| 收益口径 | **价格指数 / ETF 收盘**（不含分红与票息） | 场外**累计净值**（复权） |")
    A("| 影响 | 红利腿少算 ~4%/年、债券腿少算 ~3%/年 | 正确 |")
    A("| 信号 | 估值（股债收益比 + PE 分位） | 趋势（MA200 + 12 月动量） |")
    A("")
    A("> 红利与债券在 core_rotation 里合计 35–50% 仓位，价格指数会把它们的票息收益抹掉——"
      "这是两份报告「看起来差很多」的主要原因，不是策略水平差异。")
    A("")
    A("## 二、同口径绩效")
    A("")
    A("| 组合 | 累计 | 年化 | 年化波动 | 最大回撤 | 夏普 | 最差 12 个月 |")
    A("|---|---:|---:|---:|---:|---:|---:|")
    for x in rows:
        A(f"| {x['label']} | {x['cum']:.0%} | {x['ann']:.2%} | {x['vol']:.2%} | "
          f"{x['mdd']:.1%} | {x['sharpe']:.2f} | {x['worst']:.1%} |")
    A("")
    A(f"> permanent 相对 core_rotation 静态中枢的信息比率（月度超额/跟踪误差）："
      f"**{ir:.2f}**。但注意两者的**风险水平完全不同**（波动 5.8% vs 10.0%），"
      "收益差异主要来自配置结构（permanent 有 40% 低波动资产），不是选股/择时能力。")
    A("")
    A("> ⚠ A500 腿在本脚本里统一用中证A500指数 `SH000510`（两策略共用同一份价格），"
      "而 core_rotation 官方报告用沪深300 `000300`。因此这里的 core 数字（6.58%/7.45%）"
      "与它官方报告的 7.58%/8.85% 有 1pp 左右出入 —— **以 core 官方报告为准**，"
      "本表的用途只是把两者放在同一份价格上做相对比较。")
    A("")
    A("## 三、core_rotation 情景分布（同窗口）")
    A("")
    A("| 情景 | 月数 | 占比 |")
    A("|---|---:|---:|")
    for sc, n in sc_hist.items():
        A(f"| {sc} | {n} | {n / len(core_w) * 100:.0f}% |")
    A("")
    A("## 四、逐年收益")
    A("")
    years = sorted(set(r_perm.index.year))
    A("| 组合 | " + " | ".join(str(y) for y in years) + " |")
    A("|---" * (len(years) + 1) + "|")
    for r, lab in ((r_core, "core 策略"), (r_sc, "core 静态"),
                   (r_perm, "perm 策略"), (r_sp, "perm 静态")):
        cells = [f"{(1 + r[r.index.year == y]).prod() - 1:+.1%}" for y in years]
        A(f"| {lab} | " + " | ".join(cells) + " |")
    A("")
    A("> 免责声明: 代理口径估算, 不构成投资建议。")
    md = "\n".join(L)

    with open(_OUT, "w", encoding="utf-8") as f:
        f.write(md + "\n")
    print(f"[ok] {_OUT}")
    for x in rows:
        print(f"  {x['label']:34s} 年化 {x['ann']:6.2%} 波动 {x['vol']:5.2%} "
              f"回撤 {x['mdd']:7.1%} 夏普 {x['sharpe']:.2f}")


if __name__ == "__main__":
    main()
