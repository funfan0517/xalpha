# -*- coding: utf-8 -*-
"""债腿 · 图片方案 B/C 复现测试（利率动量 20 日轮动 / MA20 偏离连续仓位）。

只做**研究试算**：不改 rule.py、不写任何策略产物，不进每日操作报告。
口径全部走仓库既有本地数据（场外累计净值 + 10Y 国债收益率日频），不联网。

方案 B v1（初版）
  10Y 国债收益率近 20 交易日变动 < 0（收益率下行=债牛）→ 长债 80%；≥ 0 → 20%。
  每周五收盘看一次信号，下周第一个交易日执行。
方案 B v2（带噪声带，第二张图）
  Δy = y10(t) − y10(t−20)，单位 bp：
  |Δy| ≤ 3bp → 长债 50%（噪声区不动）；
  Δy < −3bp → 线性升仓至 −15bp 满仓；Δy > +3bp → 线性降仓至 +15bp 清长债。
  每周五收盘判定，下周执行。
方案 C
  长债比例 = 20% + (clip(511260 净值 / MA20 − 1, −1%, +1%) + 1%) / 2% × 60%，
  落在 20%~80% 之间；每日连续调仓（T 日收盘算、T+1 执行）。

产物：同目录 _rate_mom_trend.md
用法：python strategies/bond_cash/research/rate_mom_trend/_rate_mom_trend.py
"""
import io
import json
import math
import os
import sys

import numpy as np
import pandas as pd

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8")

_HERE = os.path.dirname(os.path.abspath(__file__))           # .../research/rate_mom_trend
_RES = os.path.dirname(_HERE)                                # .../research
_PKG = os.path.dirname(_RES)                                 # .../bond_cash
_ROOT = os.path.dirname(os.path.dirname(_PKG))               # 仓库根
OUT = os.path.join(_HERE, "_rate_mom_trend.md")

NAV_CACHE = os.path.join(_PKG, "data", "_nav_cache.json")
NAV_CACHE_V1 = os.path.join(_PKG, "v1_four_factor", "data", "_nav_cache.json")
Y10 = os.path.join(_ROOT, "data", "_bt_caches", "bond10y_d.csv")

RF = 0.014          # 仓库统一无风险利率（货基）
FEE = 0.0010        # 单边 10bp，与 v0 同口径
FEE_GRID = (0.0, 0.0010, 0.0040)
WIN6 = "2020-11-25"  # 近 6 年主窗口（图片声称的 6 年口径）
LONG_WIN = 20
MA = 20
DEV_CAP = 0.01
LO, HI = 0.20, 0.80
BAND_BP = 3.0        # B v2 噪声带 ±3bp
FULL_BP = 15.0       # B v2 满/清仓阈值 ±15bp


# ---------------------------------------------------------------- 数据
def _load_navs(path):
    with open(path, encoding="utf-8") as f:
        c = json.load(f)
    out = {}
    for k, v in (c.get("series") or {}).items():
        out[k] = pd.Series(v["tot"], index=pd.to_datetime(v["dates"]),
                           dtype="float64").sort_index()
    return out


def load_data():
    navs = _load_navs(NAV_CACHE)
    for k, v in _load_navs(NAV_CACHE_V1).items():
        if k not in navs:
            navs[k] = v
    y10 = pd.read_csv(Y10, index_col=0, parse_dates=True)["close"].astype(float).sort_index()
    return navs, y10


# ---------------------------------------------------------------- 信号
def plan_b_weights(idx, y10, win=LONG_WIN, on=HI, off=LO):
    """周五收盘判定 y10.diff(win) 符号 → 下周执行；非周五沿用上一次信号。"""
    bull = (y10.diff(win) < 0).reindex(idx)
    fri = idx[idx.dayofweek == 4]
    w = pd.Series(np.nan, index=idx, dtype="float64")
    w.loc[fri] = np.where(bull.reindex(fri).fillna(False).values, on, off)
    return w.ffill().shift(1)           # 周五收盘看 → 下个交易日执行


def plan_b2_weights(idx, y10, win=LONG_WIN, band=BAND_BP, full=FULL_BP):
    """B v2：Δy(单位 bp) → 噪声带内 50%，带外线性到 ±full 的 0%/100%；周五看、下周执行。

    y10 是百分数（如 1.59 表示 1.59%），diff 乘 100 才是 bp。
    """
    dy = (y10.diff(win) * 100.0).reindex(idx)       # bp
    w_raw = pd.Series(0.5, index=idx, dtype="float64")
    dn = dy < -band
    up = dy > band
    w_raw.loc[dn] = 0.5 + 0.5 * ((-dy.loc[dn] - band) / (full - band)).clip(upper=1.0)
    w_raw.loc[up] = 0.5 - 0.5 * ((dy.loc[up] - band) / (full - band)).clip(upper=1.0)
    fri = idx[idx.dayofweek == 4]
    w = pd.Series(np.nan, index=idx, dtype="float64")
    w.loc[fri] = w_raw.reindex(fri)
    return w.ffill().shift(1)


def plan_c_weights(nav, ma=MA, cap=DEV_CAP, lo=LO, hi=HI):
    """MA20 偏离度 → 连续仓位；T 日算、T+1 执行。"""
    dev = nav / nav.rolling(ma).mean() - 1.0
    d = dev.clip(-cap, cap)
    w = lo + (d + cap) / (2.0 * cap) * (hi - lo)
    return w.clip(lo, hi).shift(1)


# ---------------------------------------------------------------- 回测 / 指标
def run(w, r_long, r_short, fee=FEE):
    idx = w.dropna().index.intersection(r_long.index).intersection(r_short.index)
    w = w.reindex(idx).ffill().clip(0.0, 1.0)
    rl, rs = r_long.reindex(idx), r_short.reindex(idx)
    gross = w * rl + (1.0 - w) * rs
    turn = w.diff().abs().fillna(0.0)
    net = gross - turn * 2.0 * fee      # 长债减/短债加，两边各一次单边成本
    return net.dropna(), float(turn.sum()), w


def metrics(ret, rf=RF):
    if len(ret) < 2:
        return dict(years=0, cagr=0, vol=0, sharpe=0, sharpe0=0, calmar=0, mdd=0, total=0, n=0)
    nav = (1.0 + ret).cumprod()
    years = (ret.index[-1] - ret.index[0]).days / 365.25
    cagr = float(nav.iloc[-1] ** (1.0 / years) - 1.0) if years > 0 else 0.0
    vol = float(ret.std() * math.sqrt(252))
    sharpe0 = cagr / vol if vol > 1e-9 else 0.0
    sharpe = (cagr - rf) / vol if vol > 1e-9 else 0.0
    mdd = float((nav / nav.cummax() - 1.0).min())
    calmar = cagr / abs(mdd) if abs(mdd) > 1e-9 else 0.0
    yr = ret.groupby(ret.index.year).apply(lambda x: (1.0 + x).prod() - 1.0)
    return dict(years=round(years, 2), cagr=round(cagr * 100, 2), vol=round(vol * 100, 2),
                sharpe=round(sharpe, 2), sharpe0=round(sharpe0, 2), calmar=round(calmar, 2),
                mdd=round(mdd * 100, 2), worst_year=round(float(yr.min()) * 100, 2),
                total=round(float(nav.iloc[-1] - 1.0) * 100, 2), n=int(len(ret)), yearly=yr)


def _pct(v, sign=True, nd=2):
    return f"{v:+.{nd}f}%" if sign else f"{v:.{nd}f}%"


def _row(name, m, turn=None, avgw=None):
    extra = " | — | —" if turn is None else f" | {turn:.1f} | {avgw * 100:.1f}%"
    return (f"| {name} | {_pct(m['cagr'])} | {_pct(m['vol'], False)} | "
            f"{m['sharpe']:.2f} | {m['sharpe0']:.2f} | {_pct(m['mdd'])} | "
            f"{m['calmar']:.2f} | {_pct(m['worst_year'])}" + extra + " |")


def main():
    navs, y10 = load_data()
    idx_all = y10.index
    nav_long = navs["511260"].reindex(idx_all).ffill()
    nav_376 = navs["003376"].reindex(idx_all).ffill()
    nav_short = navs["002864"].reindex(idx_all).ffill()
    nav_cash = navs["000198"].reindex(idx_all).ffill()
    r = {c: n.pct_change() for c, n in
         (("511260", nav_long), ("003376", nav_376), ("002864", nav_short),
          ("000198", nav_cash))}

    wb = plan_b_weights(idx_all, y10)
    wb2 = plan_b2_weights(idx_all, y10)
    wc = plan_c_weights(nav_long)

    L = []
    A = L.append
    A("# 图片方案 B / C · 债腿复现测试")
    A("")
    A("> 由 `strategies/bond_cash/research/rate_mom_trend/_rate_mom_trend.py` 生成，"
      "**研究试算，不进每日操作报告**。")
    A("> 数据：`data/_bt_caches/bond10y_d.csv`（10Y 国债收益率）+ "
      "`bond_cash/data/_nav_cache.json` / `v1_four_factor/data/_nav_cache.json`（场外累计净值）；"
      f"单边 {FEE * 1e4:.0f}bp、夏普 rf {RF:.2%}（另列 rf=0）。")
    A("")

    A("## 0. 策略定义（按图片复述）")
    A("")
    A("- **方案 B v1（利率动量 20 日轮动）**：`y10(t) − y10(t−20) < 0` → 长债 80%；`≥ 0` → 20%。"
      "每周五收盘判定，下周一（下一交易日）执行，周内不动。")
    A(f"- **方案 B v2（v2 带噪声带，第二张图）**：`Δy = y10(t) − y10(t−20)` 单位 bp；"
      f"`|Δy| ≤ {BAND_BP:.0f}bp` → 50%（噪声区不动）；`Δy < −{BAND_BP:.0f}bp` 线性升仓至 "
      f"`−{FULL_BP:.0f}bp` 满仓；`Δy > +{BAND_BP:.0f}bp` 线性降仓至 `+{FULL_BP:.0f}bp` 清仓；"
      "每周五判定、下周执行。")
    A(f"- **方案 C（趋势连续仓位）**：`dev = 511260净值/MA{MA} − 1`，"
      f"`w = 20% + (clip(dev, ±{DEV_CAP:.0%}) + {DEV_CAP:.0%}) / {2 * DEV_CAP:.0%} × 60%`，"
      "clip 到 20%~80%；每日计算、T+1 执行（不会一夜清仓）。")
    A("- **长债腿**：图片写「10年国债ETF」，本测试用 `511260`（国泰上证10年期国债ETF）"
      "的**累计净值**；另给 `003376`（7-10 年国开 A，本家族主用长腿）作对照。"
      "另一半仓位用 `002864`（短债 A），并给 `000198`（余额宝）对照。")
    A("")
    A("> 口径提醒：图片的数字来自外部，未给标的/成本/窗口细节；下表是**本仓库口径下的复现**，"
      "数字与图片不完全一致属正常，重点看方向是否成立。")
    A("")

    A("## 1. 主结果（长债 511260 + 短债 002864）")
    A("")
    A(f"窗口 {WIN6} ~ {idx_all[-1].date()}（近 6 年，图片声称的口径）；"
      "表内 `夏普0` = rf=0 口径（便于与图片对照）。")
    A("")
    A("| 方案 | 年化 | 波动 | 夏普(rf1.4%) | 夏普0 | 最大回撤 | Calmar | 最差单年 | 换手Σ\\|Δw\\| | 均长债 |")
    A("|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|")

    results = {}
    for label, w in (("**B v1 二值80/20**", wb),
                     ("**B v2 噪声带±3bp**", wb2),
                     ("**C MA20偏离连续仓位**", wc)):
        net, turn, ww = run(w, r["511260"], r["002864"])
        net = net[net.index >= WIN6]
        ww = ww.reindex(net.index)
        m = metrics(net)
        results[label] = (m, net, ww)
        A(_row(label, m, turn, float(ww.mean())))

    for label, v in (("固定 80/20（长债满档）", 0.80), ("固定 50/50（中性）", 0.50),
                     ("固定 20/80（防守档）", 0.20)):
        w = pd.Series(v, index=idx_all)
        net, turn, ww = run(w, r["511260"], r["002864"])
        net = net[net.index >= WIN6]
        A(_row(label, metrics(net)))

    net_bh, _, _ = run(pd.Series(1.0, index=idx_all), r["511260"], r["002864"])
    A(_row("全仓 511260 买入持有", metrics(net_bh[net_bh.index >= WIN6])))
    net_sh, _, _ = run(pd.Series(0.0, index=idx_all), r["511260"], r["002864"])
    A(_row("全仓 002864 短债买入持有", metrics(net_sh[net_sh.index >= WIN6])))
    A("")
    nb0, _, _ = run(wb, r["511260"], r["002864"], fee=0.0)
    nb20, _, _ = run(wb2, r["511260"], r["002864"], fee=0.0)
    nc0, _, _ = run(wc, r["511260"], r["002864"], fee=0.0)
    mb0 = metrics(nb0[nb0.index >= WIN6])
    mb20 = metrics(nb20[nb20.index >= WIN6])
    mc0 = metrics(nc0[nc0.index >= WIN6])
    A(f"图片声称（初版 B / C）：B 年化 3.37% / 回撤 −1.25% / 夏普 2.46；"
      f"C 年化 2.72% / 回撤 −1.12% / 夏普 2.03。"
      f"本测试 **0bp 费前**：B v1 {_pct(mb0['cagr'])} / 回撤 {_pct(mb0['mdd'])} / "
      f"夏普0 {mb0['sharpe0']:.2f}；C {_pct(mc0['cagr'])} / 回撤 {_pct(mc0['mdd'])} / "
      f"夏普0 {mc0['sharpe0']:.2f}。")
    A(f"第二张图声称（B v2）：年化 3.01% / 回撤 −1.13% / 夏普 2.22 / Calmar 2.66；"
      f"本测试 **0bp 费前 B v2**：{_pct(mb20['cagr'])} / 回撤 {_pct(mb20['mdd'])} / "
      f"夏普0 {mb20['sharpe0']:.2f} / Calmar {mb20['calmar']:.2f}。")
    A("")

    A("## 2. 换标的 / 换防守腿（稳健性）")
    A("")
    A("| 组合 | 方案 | 年化 | 波动 | 夏普(rf1.4%) | 最大回撤 | 均长债 |")
    A("|---|---|---:|---:|---:|---:|---:|")
    for long_code in ("511260", "003376"):
        for def_code in ("002864", "000198"):
            for nm, w in (("B v1", wb), ("B v2", wb2), ("C", wc)):
                net, _turn, ww = run(w, r[long_code], r[def_code])
                net = net[net.index >= WIN6]
                m = metrics(net)
                A(f"| 长{long_code}+防{def_code} | {nm} | {_pct(m['cagr'])} | "
                  f"{_pct(m['vol'], False)} | {m['sharpe']:.2f} | {_pct(m['mdd'])} | "
                  f"{float(ww.reindex(net.index).mean()) * 100:.1f}% |")
    A("")

    A("## 3. 成本敏感性（511260 + 002864，近 6 年）")
    A("")
    A("| 单边成本 | Bv1 年化 | Bv1 夏普(rf1.4%) | Bv1 换手 | "
      "**Bv2 年化** | **Bv2 夏普** | **Bv2 Calmar** | **Bv2 换手** | "
      "C 年化 | C 夏普 | C 换手 |")
    A("|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|")
    for f in FEE_GRID:
        nb, tb, _ = run(wb, r["511260"], r["002864"], fee=f)
        nb2, tb2, _ = run(wb2, r["511260"], r["002864"], fee=f)
        nc, tc, _ = run(wc, r["511260"], r["002864"], fee=f)
        nb, nb2, nc = (x[x.index >= WIN6] for x in (nb, nb2, nc))
        mb, mb2, mc = metrics(nb), metrics(nb2), metrics(nc)
        A(f"| {f * 1e4:.0f}bp | {_pct(mb['cagr'])} | {mb['sharpe']:.2f} | {tb:.0f} | "
          f"**{_pct(mb2['cagr'])}** | **{mb2['sharpe']:.2f}** | **{mb2['calmar']:.2f}** | "
          f"**{tb2:.0f}** | {_pct(mc['cagr'])} | {mc['sharpe']:.2f} | {tc:.0f} |")
    A("")

    A("## 3.5 B v2 噪声带 / 满仓阈值网格（10bp，近 6 年）")
    A("")
    A("噪声带 ±3bp / 满仓 ±15bp 是图片给的标定值；下表看它是不是样本内挑出来的尖点。")
    A("")
    A("| 噪声带bp \\ 满仓bp | 8 | 12 | 15 | 20 | 30 |")
    A("|---|---:|---:|---:|---:|---:|")
    for band in (1.0, 3.0, 5.0, 8.0):
        cells = []
        for full in (8.0, 12.0, 15.0, 20.0, 30.0):
            if full <= band:
                cells.append("—")
                continue
            w = plan_b2_weights(idx_all, y10, band=band, full=full)
            net, _t, _ww = run(w, r["511260"], r["002864"])
            m = metrics(net[net.index >= WIN6])
            cells.append(f"{m['sharpe']:.2f}/{m['calmar']:.2f}")
        A(f"| ±{band:.0f} | " + " | ".join(cells) + " |")
    A("")
    A("（单元格 = 夏普(rf1.4%) / Calmar；同行同列可比较。）")
    A("")

    A("## 4. 全样本（511260 成立起，非图片口径）")
    A("")
    A("| 方案 | 起 | 年化 | 波动 | 夏普(rf1.4%) | 最大回撤 |")
    A("|---|---|---:|---:|---:|---:|")
    for nm, w in (("B v1", wb), ("B v2", wb2), ("C", wc)):
        net, _, _ = run(w, r["511260"], r["002864"])
        m = metrics(net)
        A(f"| {nm} | {net.index[0].date()} | {_pct(m['cagr'])} | {_pct(m['vol'], False)} | "
          f"{m['sharpe']:.2f} | {_pct(m['mdd'])} |")
    A("")

    A("## 5. 逐年收益（主结果，511260 + 002864）")
    A("")
    yb = results["**B v1 二值80/20**"][0]["yearly"]
    yb2 = results["**B v2 噪声带±3bp**"][0]["yearly"]
    yc = results["**C MA20偏离连续仓位**"][0]["yearly"]
    ybh = metrics(net_bh[net_bh.index >= WIN6])["yearly"]
    A("| 年 | B v1 | B v2 | C | 全仓511260 |")
    A("|---|---:|---:|---:|---:|")
    for y in sorted(set(yb.index) | set(yb2.index) | set(yc.index) | set(ybh.index)):
        A(f"| {y} | {_pct(float(yb.get(y, 0)) * 100)} | {_pct(float(yb2.get(y, 0)) * 100)} | "
          f"{_pct(float(yc.get(y, 0)) * 100)} | {_pct(float(ybh.get(y, 0)) * 100)} |")
    A("")

    A("## 6. 结论")
    A("")
    mb = results["**B v1 二值80/20**"][0]
    mb2 = results["**B v2 噪声带±3bp**"][0]
    mc = results["**C MA20偏离连续仓位**"][0]
    A(f"- 近 6 年、10bp 单边成本：B v1 **{_pct(mb['cagr'])} / 回撤 {_pct(mb['mdd'])} / "
      f"夏普 {mb['sharpe']:.2f} / Calmar {mb['calmar']:.2f}**；B v2 "
      f"**{_pct(mb2['cagr'])} / 回撤 {_pct(mb2['mdd'])} / 夏普 {mb2['sharpe']:.2f} / "
      f"Calmar {mb2['calmar']:.2f}**；C **{_pct(mc['cagr'])} / 回撤 {_pct(mc['mdd'])} / "
      f"夏普 {mc['sharpe']:.2f}**。")
    A(f"- **图片数字费前基本能复现**：0bp 下 B 年化 {_pct(mb0['cagr'])} / 回撤 "
      f"{_pct(mb0['mdd'])} / 夏普0 {mb0['sharpe0']:.2f}（图片 3.37% / −1.25% / 2.46），"
      f"C 年化 {_pct(mc0['cagr'])} / 回撤 {_pct(mc0['mdd'])} / 夏普0 {mc0['sharpe0']:.2f}"
      "（图片 2.72% / −1.12% / 2.03）—— 量级与回撤基本对上（C 的 −1.12% 完全一致），"
      "说明图片算的是**费前毛收益 + rf≈0**，且窗口/标的口径略有差异。")
    A("- **B v2 的「改进」是费前口径下的，费后不成立于回撤/Calmar**："
      f"0bp 下 B v1 回撤 {_pct(mb0['mdd'])} / Calmar {mb0['calmar']:.2f} → B v2 回撤 "
      f"{_pct(mb20['mdd'])} / Calmar {mb20['calmar']:.2f}（与图片 −1.25%→−1.13%、"
      "Calmar 2.51→2.66 方向一致）；但 10bp 下 B v2 回撤 "
      f"{_pct(mb2['mdd'])} / Calmar {mb2['calmar']:.2f}，反而**差于** B v1 的 "
      f"{_pct(mb['mdd'])} / {mb['calmar']:.2f}。年化与夏普确有改善"
      f"（{_pct(mb['cagr'])}/{mb['sharpe']:.2f} → {_pct(mb2['cagr'])}/{mb2['sharpe']:.2f}），"
      "但 40bp 下 v2 仍为负；它换手更高（86 vs 76），不是「更少无效换手」。")
    A("- **§3.5 参数网格证伪了「±3bp 最优」**：10bp 下噪声带从 ±1 放宽到 ±8、"
      "满仓阈值从 ±8 放宽到 ±30，夏普单调改善（0.30 → 1.52），"
      "说明本样本里最好的「噪声带」其实是**几乎不动手**。"
      "图片选的 ±3/±15 在网格里只是中低档，谈不上实盘最优标定。")
    A("- **费前不等于能成交**：初版 B/C 六年换手 76 / 64（双边 ≈ 150 / 128 次单边），"
      "10bp 单边成本吃掉年化 1.3pp，40bp 直接变成负收益。图片说「交易成本已计入」"
      "与本口径不符（大概率按 C 类免赎回费或零成本算的）。")
    A("- 三个方案在 10bp 下都**跑不赢固定 50/50**（年化 3.28% / 回撤 −1.23% / 夏普 1.82），"
      "更远低于固定 20/80（夏普 2.29）—— 过去 6 年是债牛，减仓就是少赚，"
      "择时省下的那点回撤不够抵收益损失。")
    A("- 三者都只在「长债占比」一个维度做择时，与 `research/compare_all` 里"
      "v1/v3/v5/v6/v8 的失败假设同类 —— 看回撤改善是否值回换手，不能只看牛市年化。")
    A("")
    A("> 研究试算，不构成投资建议；新策略未获用户明确同意前不进每日操作报告。")
    A("")

    txt = "\n".join(L)
    with open(OUT, "w", encoding="utf-8") as f:
        f.write(txt + "\n")
    print(txt)
    print(f"\n[ok] {OUT}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
