# -*- coding: utf-8 -*-
"""核心轮动 · 组合层回测（数据自洽口径的简化模拟）

口径（README §6）:
  腿(代理): bond=511260(2017-08起) · dividend=中证红利000922 · a500=沪深300(000300, A500 2024-09才发布)
            kc=科创50 000688(2019-07起) · ndx=纳指QDII 513100(含汇率/溢价) · gold=黄金ETF 518880
  估值(统一用中证官网日频 peg, 近似静态PE):
            红利股息率 = 全收益H00922/价格000922 因子近12月增长 × 用蛋卷当前股息率(4.2%)校准
            纳指 Forward PE 分位由 historyofmarket 周频滚动近10年(与每日报告同源, 此处未参与权重调整)
  判定: 复刻手册——股债收益比 + 红利PE分位(期初以来累计) + 成长开关=False(无避险)
  微调: y10<1.5%→债券=5%; 科创PE比>7→科创=0%; 之后归一化
  调仓: 每月末按当月数据定权重, 持有下月整月(月末复权收益), 未计费
  基准: 静态均衡中枢(rule.TARGET 均衡 10/25/15/25/5/20) 与 六腿等权

用法: python strategies/core_rotation/_backtest.py
输出: strategies/core_rotation/_bt_report.md + strategies/core_rotation/_core_bt.json
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


def main():
    m = build_monthly()
    dec = build_decisions(m)
    retm = pd.DataFrame({k: _m(csi_index(c) if kind == "csi" else xq_bars(c))
                         for k, (kind, c) in _LEG.items()})
    retm = retm.pct_change().dropna(how="all")
    # 样本起点取 科创50 指数发布(2019-07)后且债券腿已运行, 保证六腿大体可用
    common = sorted(t for t in (set(dec.index) & set(retm.index))
                    if t >= pd.Timestamp("2019-08-01"))
    yrs = (common[-1] - common[0]).days / 365.25

    w_static = {k: rule.TARGET[k]["均衡"] / sum(rule.TARGET[k]["均衡"] for k in _KEYS)
                for k in _KEYS}
    navs = {"strategy": [], "static": [], "equal": []}
    ns = nb = ne = 1.0
    for t in common:
        r = retm.loc[t]
        w = _alloc(dec.loc[t, "w"], r)
        ns *= 1 + sum(w[k] * r[k] for k in _KEYS if not np.isnan(r[k]))
        wb = _alloc(w_static, r)
        nb *= 1 + sum(wb[k] * r[k] for k in _KEYS if not np.isnan(r[k]))
        vals = [r[k] for k in _KEYS if not np.isnan(r[k])]
        if vals:
            ne *= 1 + float(np.mean(vals))
        navs["strategy"].append(ns)
        navs["static"].append(nb)
        navs["equal"].append(ne)
    ser = {k: pd.Series(v, index=pd.to_datetime(common)) for k, v in navs.items()}

    def perf(nav):
        tot = nav.iloc[-1]
        mdd = float((nav / nav.cummax() - 1).min())
        vol = float(nav.pct_change().std() * np.sqrt(12) * 100)
        return tot, (tot ** (1 / yrs) - 1) * 100, vol, mdd

    L = ["# 核心轮动 · 组合层回测报告", "",
         f"> 生成 {datetime.now():%Y-%m-%d %H:%M} · 样本 {common[0].date()} ~ {common[-1].date()} ({yrs:.1f}年, 月度)",
         "> 口径: A500→沪深300代理; 科创50 2019-07上市; 纳指=513100(QDII)代理; 黄金=518880; 债券=511260(2017-08起)",
         "> 估值统一中证官网 peg(近似静态PE); 红利股息率=全收益/价格近12m×4.2%校准; 期末月末调仓, 未计费。机械输出, 非投资建议。", ""]
    totS, annS, volS, ddS = perf(ser["strategy"])
    totB, annB, volB, ddB = perf(ser["static"])
    totE, annE, volE, ddE = perf(ser["equal"])
    L += ["## 一、绩效(月度再平衡, 未计费)", "", "| 组合 | 累计 | 年化 | 年化波动 | 最大回撤 |",
          "|---|---:|---:|---:|---:|",
          f"| 策略(情景中枢轮动) | {totS*100:.0f}% | {annS:.1f}% | {volS:.1f}% | {ddS*100:.1f}% |",
          f"| 静态均衡中枢(10/25/15/25/5/20) | {totB*100:.0f}% | {annB:.1f}% | {volB:.1f}% | {ddB*100:.1f}% |",
          f"| 六腿等权 | {totE*100:.0f}% | {annE:.1f}% | {volE:.1f}% | {ddE*100:.1f}% |", ""]
    sc_hist = dec.loc[common, "sc"].value_counts()
    L += ["## 二、情景出现频次(样本内)", "", "| 情景 | 月数 | 占比 |", "|---|---:|---:|"]
    for sc, n in sc_hist.items():
        L.append(f"| {sc} | {n} | {n/len(common)*100:.0f}% |")
    wdf = pd.DataFrame([dec.loc[t, "w"] for t in common], index=pd.to_datetime(common))
    L += ["", "## 三、平均实际配置(可用腿内)", "", "| 资产 | 平均权重 |", "|---|---:|"]
    names = {k: rule.pool()[_KEYS.index(k)]["name"] for k in _KEYS}
    for k in _KEYS:
        L.append(f"| {names[k]} | {wdf[k].mean():.1f}% |")
    L.append("")
    L.append("> 免责声明: 公开数据回填的代理口径估算, 不构成投资建议。")
    txt = "\n".join(L) + "\n"
    with open(os.path.join(os.path.dirname(os.path.abspath(__file__)), "backtest", "_bt_report.md"),
              "w", encoding="utf-8") as fh:
        fh.write(txt)
    print(txt)
    out = {"sample": [str(common[0].date()), str(common[-1].date())],
           "strategy": {"cum": totS, "ann": annS, "vol": volS, "mdd": ddS},
           "static": {"cum": totB, "ann": annB, "vol": volB, "mdd": ddB},
           "equal": {"cum": totE, "ann": annE, "vol": volE, "mdd": ddE},
           "scenario_counts": sc_hist.to_dict()}
    json.dump(out, open(os.path.join(os.path.dirname(os.path.abspath(__file__)), "backtest", "_core_bt.json"), "w", encoding="utf-8"),
              ensure_ascii=False, indent=2)


if __name__ == "__main__":
    main()
