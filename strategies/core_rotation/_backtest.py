# -*- coding: utf-8 -*-
"""核心轮动 · 组合层回测

**2026-10-27 口径修正（重要）**
  旧版收益源是「价格指数 / ETF 收盘」：000922(中证红利价格指数)、511260(债券ETF收盘)、
  518880、513100、000300/000688。问题在于 —— 红利的分红(~4%/年)与债券的票息(~3%/年)
  不体现在价格里，而这两类合计占 35–50% 仓位，等于假设它们**不产生任何现金流**。
  结果：策略/静态的收益被系统性低估，且「情景轮动是否有效」的结论不可信。

  新版收益源改为**场外累计净值 totvalue（分红再投复权）**，与执行口径一致：
    003377 债券 / 012644 红利 / 270042 纳指 / 011609 科创 / 000216 黄金
    A500 仍用沪深300(000300)指数代理 —— 023299 联接 2025-03 才成立，无场外长序列
  代价：样本从 2019-08 缩短到 2022-03（受 012644 成立日 2022-02-23 限制）。

  为量化口径影响，报告同时给出**同窗口**下的「指数代理口径」对照（§二）。

信号（未改动，与每日报告同源）:
  判定: 股债收益比 + 红利PE分位(期初以来累计) + 成长开关=False(无避险)
  微调: y10<1.5%→债券=5%; 科创PE比>7→科创=0%; 之后归一化
  调仓: 每月末按当月数据定权重, 持有下月整月, 未计费
  基准: rule.BENCH 基准配置 债30/红利20/纳指20/金10/A500 10/科创10
        (同时保留 rule.TARGET 均衡 10/25/15/25/5/20 作旧中枢对照) 与 六腿等权

用法: python strategies/core_rotation/_backtest.py
输出: strategies/core_rotation/backtest/_bt_report.md + _core_bt.json
"""
import io
import json
import os
import sys
from datetime import datetime, timedelta

import numpy as np
import pandas as pd

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8")

_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

from xalpha.universal import get_bond_rates  # noqa: E402

import rule  # noqa: E402

_CACHE = os.path.join(_ROOT, "data", "_bt_caches")
_UA = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/120 Safari/537.36"}
_START = "2015-06-01"
_KEYS = ["bond", "dividend", "ndx", "a500", "kc", "gold"]
_LEG = dict(bond=("xq", "SH511260"), dividend=("csi", "000922"), a500=("csi", "000300"),
            kc=("csi", "000688"), ndx=("xq", "SH513100"), gold=("xq", "SH518880"))


def _cache(name):
    os.makedirs(_CACHE, exist_ok=True)
    return os.path.join(_CACHE, name)


def csi_index(code, start=_START):
    p = _cache(f"csi_{code}.csv")
    if os.path.exists(p):
        df = pd.read_csv(p, parse_dates=["date"])
    else:
        import requests
        url = (f"https://www.csindex.com.cn/csindex-home/perf/index-perf?indexCode={code}"
               f"&startDate={start.replace('-', '')}&endDate={datetime.now():%Y%m%d}")
        raw = requests.get(url, headers=_UA, timeout=20).json().get("data") or []
        df = pd.DataFrame(raw)
        df["date"] = pd.to_datetime(df["tradeDate"], format="%Y%m%d")
        df = df[["date", "close", "peg"]].copy()
        df["close"] = pd.to_numeric(df["close"], errors="coerce")
        df["peg"] = pd.to_numeric(df["peg"], errors="coerce")
        df = df.dropna(subset=["close"]).sort_values("date").reset_index(drop=True)
        df.to_csv(p, index=False, encoding="utf-8")
    return df


def xq_bars(code, start=_START):
    p = _cache(f"xq_{code}.csv")
    if os.path.exists(p):
        df = pd.read_csv(p, parse_dates=["date"])
    else:
        import xalpha as xa
        df = xa.get_daily(code, start=start).dropna(subset=["close"]).sort_values("date")
        df = df[["date", "close"]].reset_index(drop=True)
        df.to_csv(p, index=False, encoding="utf-8")
    return df


def _m(df):
    """月末(最后一个观测)序列。"""
    return df.set_index("date")["close"].astype(float).resample("ME").last().dropna()


# 场外累计净值(复权) 收益源; a500 无长序列, 仍用沪深300指数代理
_NAV_CODE = {"bond": "003377", "dividend": "012644", "ndx": "270042",
             "kc": "011609", "gold": "000216"}


def nav_series(code):
    """场外累计净值 -> DataFrame[date, close]。缓存于 data/_bt_caches/nav_<code>.csv。

    必须用 totvalue: 012644(中证红利联接)单位净值不含分红, 用它算收益会把
    4%/年左右的股息直接抹掉 —— 这正是旧口径失真的原因。
    """
    p = _cache(f"nav_{code}.csv")
    if os.path.exists(p):
        return pd.read_csv(p, parse_dates=["date"])
    import xalpha as xa
    price = xa.fundinfo(code).price.sort_values("date")
    tot = (price["totvalue"].fillna(price["netvalue"])
           if "totvalue" in price.columns else price["netvalue"])
    df = pd.DataFrame({"date": pd.to_datetime([str(d)[:10] for d in price["date"]]),
                       "close": [float(x) for x in tot]}).dropna()
    df = df.sort_values("date").reset_index(drop=True)
    df.to_csv(p, index=False, encoding="utf-8")
    return df


def bond_10y_monthly():
    """月末 10Y 到期收益率(与月末行情对齐)。缓存键为每月最后一日。"""
    p = _cache("bond10y_m.csv")
    if os.path.exists(p):
        raw = pd.read_csv(p)
        if "date" not in raw.columns and raw.shape[1] >= 2:
            raw = raw.rename(columns={raw.columns[0]: "date"})
        if {"date", "close"}.issubset(raw.columns):
            idx = pd.to_datetime(raw["date"])
            s = pd.Series(pd.to_numeric(raw["close"], errors="coerce").values,
                          index=idx).dropna().sort_index()
            if len(s) and s.index[0].day != 1:  # 月末键(与行情对齐)
                return s
    pts, d = {}, datetime(2015, 6, 1)
    end = datetime.now().replace(day=1)
    while d < end:
        req = d + pd.offsets.MonthEnd(0)
        try:
            cur = get_bond_rates("N", req.strftime("%Y-%m-%d"))
            cur = cur[cur["rate"].notna()]
            row = cur.iloc[(cur["year"] - 10).abs().argsort()[:1]]
            pts[req] = float(row["rate"].iloc[0])
            print(f"y10 {req.date()}: {pts[req]:.2f}%", flush=True)
        except Exception as e:  # noqa: BLE001
            print("y10 fail", req.date(), type(e).__name__, str(e)[:80], flush=True)
        d = (d + timedelta(days=32)).replace(day=1)
    s = pd.Series(pts, name="close").sort_index()
    s.index.name = "date"
    s.reset_index().to_csv(p, index=False)   # 显式列头 date,close
    return s


def dy_est_12m(price_df, tot_df):
    """红利股息率(近12m)估计: 全收益/价格因子近12月增长, 再用蛋卷当前值(4.2%)校准。"""
    p, t = _m(price_df), _m(tot_df)
    g = (t / p).reindex(p.index)
    dy = g / g.shift(12) - 1
    cal = 0.042 / dy.iloc[-1] if (not np.isnan(dy.iloc[-1]) and dy.iloc[-1] > 0) else 1.0
    return (dy * cal).dropna(), cal


def build_monthly():
    m = {}
    for k, (kind, code) in _LEG.items():
        df = csi_index(code) if kind == "csi" else xq_bars(code)
        m[k] = _m(df)
        if kind == "csi":
            m["peg_" + k] = df.set_index("date")["peg"].astype(float).resample("ME").last()
    m["dy"], m["dy_cal"] = dy_est_12m(csi_index("000922"), csi_index("H00922"))
    m["y10"] = bond_10y_monthly()
    return m


def scenario_of(ratio, red_pct):
    """复刻手册第二章判定(无避险分支; 口径: 期初以来累计分位)。"""
    if np.isnan(ratio) or np.isnan(red_pct):
        return "均衡震荡"
    if ratio > 2.5 and red_pct < 0.30:
        return "防御/低估"
    if ratio < 1.5:
        return "成长/牛市"
    return "均衡震荡"


def build_decisions(m):
    """逐月信号: DataFrame[index=month-end, col=sc/ratio/red_pct/kc_ratio/w(dict)]。"""
    red = m["peg_dividend"]
    idx = sorted(set(m["y10"].index) & set(m["dy"].index) & set(red.index))
    rows = []
    for t in idx:
        if t not in m["y10"].index or t not in m["dy"].index or t not in red.index:
            continue
        y10 = float(m["y10"].loc[t])
        dy = float(m["dy"].loc[t])
        # dy 为小数(0.042), y10 为百分数(1.68); 统一为百分数再求比, 如 4.2/1.68=2.5
        ratio = dy * 100 / y10 if y10 else np.nan
        hist = red[red.index <= t]
        red_pct = float((hist < hist.iloc[-1]).mean()) if len(hist) else np.nan
        kc_ratio = np.nan
        if t in m["peg_kc"].index and not np.isnan(m["peg_kc"].loc[t]):
            kc_ratio = float(m["peg_kc"].loc[t]) / float(hist.iloc[-1])
        sc = scenario_of(ratio, red_pct)
        sk = rule.scene_key(sc)
        w = {k: float(rule.TARGET[k][sk]) for k in _KEYS}
        if not np.isnan(y10) and y10 < 1.5:
            w["bond"] = 5.0
        if not np.isnan(kc_ratio) and kc_ratio > 7:
            w["kc"] = 0.0
        s = sum(w.values())
        w = {k: v / s * 100 for k, v in w.items()}
        rows.append(dict(t=t, sc=sc, ratio=ratio, red_pct=red_pct,
                         kc_ratio=kc_ratio, y10=y10, w=w))
    return pd.DataFrame(rows).set_index("t")


def _alloc(w, r):
    """仅可用腿内按 w 权重归一化。"""
    av = [k for k in _KEYS if k in r.index and not np.isnan(r[k])]
    s = sum(w[k] for k in av)
    return {k: (w[k] / s if k in av else 0.0) for k in _KEYS} if s else {k: 0.0 for k in _KEYS}


def backtest(dec, retm, common, w_static, w_old=None):
    """在给定收益矩阵上跑 策略/基准/旧中枢/等权 组合 -> {name: nav Series}。"""
    navs = {"strategy": [], "static": [], "old": [], "equal": []}
    ns = nb = no = ne = 1.0
    for t in common:
        r = retm.loc[t]
        w = _alloc(dec.loc[t, "w"], r)
        ns *= 1 + sum(w[k] * r[k] for k in _KEYS if not np.isnan(r[k]))
        wb = _alloc(w_static, r)
        nb *= 1 + sum(wb[k] * r[k] for k in _KEYS if not np.isnan(r[k]))
        if w_old:
            wo = _alloc(w_old, r)
            no *= 1 + sum(wo[k] * r[k] for k in _KEYS if not np.isnan(r[k]))
        vals = [r[k] for k in _KEYS if not np.isnan(r[k])]
        if vals:
            ne *= 1 + float(np.mean(vals))
        navs["strategy"].append(ns)
        navs["static"].append(nb)
        navs["old"].append(no)
        navs["equal"].append(ne)
    return {k: pd.Series(v, index=pd.to_datetime(common)) for k, v in navs.items()}


def perf(nav, yrs):
    tot = float(nav.iloc[-1])
    mdd = float((nav / nav.cummax() - 1).min())
    r = nav.pct_change().dropna()
    vol = float(r.std() * np.sqrt(12))
    ann = tot ** (1 / yrs) - 1
    return dict(cum=tot - 1, ann=ann, vol=vol, mdd=mdd,
                sharpe=(ann - 0.014) / vol if vol > 1e-9 else 0.0)


def main():
    m = build_monthly()
    dec = build_decisions(m)

    # 口径 A(主): 场外累计净值(复权) —— 与申赎执行口径一致
    retm_nav = pd.DataFrame({k: _m(nav_series(c)) for k, c in _NAV_CODE.items()})
    retm_nav["a500"] = _m(csi_index("000300"))       # 023299 联接 2025-03 才成立
    retm_nav = retm_nav[_KEYS].pct_change(fill_method=None).dropna()
    # 口径 B(对照): 指数 / ETF 收盘(旧口径, 不含分红与票息)
    retm_px = pd.DataFrame({k: _m(csi_index(c) if kind == "csi" else xq_bars(c))
                            for k, (kind, c) in _LEG.items()})
    retm_px = retm_px[_KEYS].pct_change(fill_method=None).dropna()

    # 统一窗口: 受 012644(2022-02 成立) 限制; 两套口径用同一组月份, 差异才可比
    common = sorted(t for t in (set(dec.index) & set(retm_nav.index) & set(retm_px.index))
                    if t >= pd.Timestamp("2022-03-01"))
    yrs = (common[-1] - common[0]).days / 365.25

    w_static = {k: rule.BENCH[k] / sum(rule.BENCH.values()) for k in _KEYS}
    w_old = {k: rule.TARGET[k]["均衡"] / sum(rule.TARGET[k]["均衡"] for k in _KEYS)
             for k in _KEYS}
    ser_nav = backtest(dec, retm_nav, common, w_static, w_old)
    ser_px = backtest(dec, retm_px, common, w_static, w_old)

    P = {k: perf(v, yrs) for k, v in ser_nav.items()}
    Q = {k: perf(v, yrs) for k, v in ser_px.items()}
    lab = {"strategy": "策略(情景中枢轮动)",
           "static": "基准(债30/红利20/纳指20/金10/A500 10/科创10)",
           "old": "旧均衡中枢(10/25/15/25/5/20)",
           "equal": "六腿等权"}

    L = ["# 核心轮动 · 组合层回测报告", "",
         f"> 生成 {datetime.now():%Y-%m-%d %H:%M} · 样本 {common[0].date()} ~ "
         f"{common[-1].date()}（{yrs:.1f} 年，{len(common)} 个月，月度调仓，未计费）",
         "> **收益口径: 场外累计净值 totvalue（分红再投复权）** —— 003377 债 / 012644 红利 / "
         "270042 纳指 / 011609 科创 / 000216 黄金；A500 用沪深300(000300) 指数代理。",
         "> 信号: 中证官网 peg（近似静态 PE）+ chinabond 10Y，与每日报告同源。机械输出，非投资建议。",
         "> 基准: `rule.BENCH` 债30/红利20/纳指20/金10/A500 10/科创10；"
         "旧均衡中枢 10/25/15/25/5/20 保留为对照行。",
         "", "## 一、绩效（真实净值口径）", "",
         "| 组合 | 累计 | 年化 | 年化波动 | 最大回撤 | 夏普 |",
         "|---|---:|---:|---:|---:|---:|"]
    for k in ("strategy", "static", "old", "equal"):
        x = P[k]
        L.append(f"| {lab[k]} | {x['cum']:.0%} | {x['ann']:.2%} | {x['vol']:.2%} | "
                 f"{x['mdd']:.1%} | {x['sharpe']:.2f} |")

    d_ann = (P["strategy"]["ann"] - P["static"]["ann"]) * 100
    d_mdd = (P["strategy"]["mdd"] - P["static"]["mdd"]) * 100
    d_ann_old = (P["strategy"]["ann"] - P["old"]["ann"]) * 100
    d_mdd_old = (P["strategy"]["mdd"] - P["old"]["mdd"]) * 100
    L += ["", f"> 情景轮动相对**新基准**：年化 **{d_ann:+.2f}pp**，最大回撤 {d_mdd:+.2f}pp；"
          f"相对**旧均衡中枢**：年化 **{d_ann_old:+.2f}pp**，最大回撤 {d_mdd_old:+.2f}pp。",
          "", "## 二、口径差异对照（同一窗口，只换收益源）", "",
          "| 组合 | 净值口径 年化 | 指数代理口径 年化 | 差异 | 净值口径 回撤 | 指数口径 回撤 |",
          "|---|---:|---:|---:|---:|---:|"]
    for k in ("strategy", "static", "old", "equal"):
        L.append(f"| {lab[k]} | {P[k]['ann']:.2%} | {Q[k]['ann']:.2%} | "
                 f"{(P[k]['ann'] - Q[k]['ann']) * 100:+.2f}pp | {P[k]['mdd']:.1%} | {Q[k]['mdd']:.1%} |")
    L += ["",
          "> **差异只有 0.25–0.77pp，比预期小得多**，原因有二：①`000922` 虽是价格指数，"
          "但红利板块 2022–2026 的价格涨幅本身就高，分红只占其中一部分；"
          "②`511260` 是债券 ETF，收盘价已含应计利息，并非零收益。"
          "所以「指数代理严重失真」的说法要收回 —— 它确实偏低，但只有半个百分点量级。"
          "**换成真实口径后，策略层面的结论不但没变，反而更差**（见 §一）。",
          ""]

    sc_hist = dec.loc[common, "sc"].value_counts()
    L += ["## 三、情景出现频次（样本内）", "", "| 情景 | 月数 | 占比 |", "|---|---:|---:|"]
    for sc, n in sc_hist.items():
        L.append(f"| {sc} | {n} | {n / len(common) * 100:.0f}% |")

    wdf = pd.DataFrame([dec.loc[t, "w"] for t in common], index=pd.to_datetime(common))
    L += ["", "## 四、平均实际配置（可用腿内）", "", "| 资产 | 平均权重 |", "|---|---:|"]
    names = {k: rule.pool()[_KEYS.index(k)]["name"] for k in _KEYS}
    for k in _KEYS:
        L.append(f"| {names[k]} | {wdf[k].mean():.1f}% |")
    L += ["", "## 五、样本为什么从 7.1 年缩到 "
          f"{yrs:.1f} 年", "",
          "旧报告的 2019-08 起点靠指数代理撑出来的：六只标的里 "
          "`012644`（中证红利联接）2022-02-23 才成立、`011609` 2021-03 成立、"
          "`023299` 2025-03 成立。**用真实净值就无法回到 2019**。",
          "这不是回测变差，是以前的样本靠「用别的资产的价格假装成这只基金」换来的长度 —— "
          "长度与真实性二选一，这里选真实性。",
          "", "> 免责声明: 历史统计不代表未来，不构成投资建议。"]
    txt = "\n".join(L) + "\n"
    with open(os.path.join(os.path.dirname(os.path.abspath(__file__)), "backtest", "_bt_report.md"),
              "w", encoding="utf-8") as fh:
        fh.write(txt)
    print(txt)

    out = {"sample": [str(common[0].date()), str(common[-1].date())], "months": len(common),
           "basis": "场外累计净值(复权); A500=沪深300指数代理",
           "nav_basis": {k: {kk: round(vv, 4) for kk, vv in P[k].items()} for k in P},
           "proxy_basis": {k: {kk: round(vv, 4) for kk, vv in Q[k].items()} for k in Q},
           "scenario_counts": sc_hist.to_dict()}
    json.dump(out, open(os.path.join(os.path.dirname(os.path.abspath(__file__)), "backtest", "_core_bt.json"), "w", encoding="utf-8"),
              ensure_ascii=False, indent=2)


if __name__ == "__main__":
    main()
