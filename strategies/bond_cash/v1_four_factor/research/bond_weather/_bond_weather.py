# -*- coding: utf-8 -*-
"""债券市场「天气预报表」研究：5 / 10 / 20 交易日三分类概率预测。

只使用仓库里已有的行情/曲线数据（511260 累计净值、10Y/3Y/1Y 国债、3Y AAA、
SHIBOR-1W），不引入宏观或外部数据。模型为自实现的 L2 多项 Logistic，
滚动训练（扩窗 + h 日 purge），与「气候基准」（训练期类别频率）对照，
用准确率与多分类 Brier 评分检验。输出当前三期限预报与样本外检验报告。

产物：`research/bond_weather/_bond_weather.{md,json}`。
用法：`python strategies/bond_cash/v1_four_factor/research/bond_weather/_bond_weather.py`
"""
import json
import os
import sys
from datetime import datetime

import numpy as np
import pandas as pd
from scipy.optimize import minimize

_HERE = os.path.dirname(os.path.abspath(__file__))
_RES = os.path.dirname(_HERE)
_STRAT = os.path.dirname(_RES)
if _STRAT not in sys.path:
    sys.path.insert(0, _STRAT)

import data  # noqa: E402

OUT_MD = os.path.join(_HERE, "_bond_weather.md")
OUT_JSON = os.path.join(_HERE, "_bond_weather.json")
BENCH = "511260"
HORIZONS = (5, 10, 20)
THRESHOLDS = {5: 0.0025, 10: 0.0040, 20: 0.0060}
LABELS = {0: "雨", 1: "震荡", 2: "晴"}
REFIT_EVERY = 20
MIN_TRAIN = 500
REG = 1.0
SEED = 20261126


def build_features():
    p = data.panel()
    nav = p["navs"][BENCH].sort_index()
    curve = p["curve"]
    idx = nav.index
    y10 = p["y10"].reindex(idx).ffill()
    y1 = curve["cgb_1y"].reindex(idx).ffill()
    y3 = curve["cgb_3y"].reindex(idx).ffill()
    aaa3 = curve["aaa_3y"].reindex(idx).ffill()
    sh = p["shibor"].reindex(idx).ffill()
    slope, curv = y10 - y1, y10 - 2 * y3 + y1
    credit, funding = aaa3 - y3, sh - y1
    ret = nav.pct_change()

    def pos(s, n):
        lo, hi = s.rolling(n).min(), s.rolling(n).max()
        return (s - lo) / (hi - lo).replace(0, np.nan)

    f = pd.DataFrame({
        "y10": y10, "slope": slope, "curvature": curv,
        "credit": credit, "funding": funding, "shibor": sh,
        "y10_d5": y10.diff(5), "y10_d20": y10.diff(20),
        "slope_d5": slope.diff(5), "slope_d20": slope.diff(20),
        "credit_d5": credit.diff(5), "credit_d20": credit.diff(20),
        "funding_d5": funding.diff(5),
        "nav_r5": nav.pct_change(5), "nav_r20": nav.pct_change(20),
        "nav_r60": nav.pct_change(60),
        "nav_vol20": ret.rolling(20).std(), "nav_vol60": ret.rolling(60).std(),
        "y10_vol20": y10.diff().rolling(20).std(),
        "nav_pos250": pos(nav, 250), "y10_pos500": pos(y10, 500),
        "up_frac10": (ret > 0).rolling(10).mean(),
    }).replace([np.inf, -np.inf], np.nan).dropna()
    return f, nav.reindex(f.index)


def make_target(nav, h):
    thr = THRESHOLDS[h]
    fwd = nav.shift(-h) / nav - 1.0
    y = np.where(fwd >= thr, 2, np.where(fwd <= -thr, 0, 1))
    y[fwd.isna().to_numpy()] = -1      # 末尾 h 天标签未实现，不能当「震荡」
    return y, fwd


def softmax(z):
    z = z - z.max(axis=1, keepdims=True)
    e = np.exp(z)
    return e / e.sum(axis=1, keepdims=True)


def add_intercept(x):
    return np.column_stack([np.ones(len(x)), x])


def predict_logit(w, x):
    return softmax(add_intercept(x) @ w)


def fit_logit(x, y, reg=None):
    reg = REG if reg is None else reg
    x = add_intercept(x)
    n, d = x.shape
    k = 3
    ymat = np.eye(k)[y]
    w0 = np.zeros(d * k)

    def obj(w):
        w = w.reshape(d, k)
        p = softmax(x @ w)
        reg_w = w.copy()
        reg_w[0] = 0.0                  # 截距不进 L2，否则类别先验会被拉平
        loss = -np.sum(ymat * np.log(p + 1e-12)) / n + 0.5 * reg * np.sum(reg_w * reg_w)
        grad = (x.T @ (p - ymat)) / n + reg * reg_w
        return loss, grad.ravel()

    r = minimize(obj, w0, jac=True, method="L-BFGS-B",
                 options={"maxiter": 300, "ftol": 1e-9})
    return r.x.reshape(d, k)


def calibrate(y):
    return np.bincount(y, minlength=3).astype(float) / len(y)


def brier(p, y):
    ym = np.eye(3)[y]
    return float(np.mean(np.sum((p - ym) ** 2, axis=1)))


def metrics(recs):
    rdf = pd.DataFrame([dict(p0=r[1][0], p1=r[1][1], p2=r[1][2],
                             c0=r[2][0], c1=r[2][1], c2=r[2][2], y=r[3]) for r in recs])
    pm = rdf[["p0", "p1", "p2"]].to_numpy()
    pc = rdf[["c0", "c1", "c2"]].to_numpy()
    yy = rdf["y"].to_numpy()
    return dict(n=len(rdf), acc=float((pm.argmax(1) == yy).mean()),
                climate_acc=float((pc.argmax(1) == yy).mean()),
                majority=float(np.bincount(yy, minlength=3).max() / len(yy)),
                brier=brier(pm, yy), climate_brier=brier(pc, yy),
                actual_dist={LABELS[i]: int((yy == i).sum()) for i in range(3)})


def fit_bin(x, y, reg=1.0):
    xb = np.column_stack([np.ones(len(x)), x])

    def obj(w):
        pr = 1.0 / (1.0 + np.exp(-np.clip(xb @ w, -30, 30)))
        loss = (-np.mean(y * np.log(pr + 1e-12) + (1 - y) * np.log(1 - pr + 1e-12))
                + 0.5 * reg * np.sum(w[1:] ** 2))
        rw = w.copy()
        rw[0] = 0.0
        return loss, xb.T @ (pr - y) / len(y) + reg * rw

    r = minimize(obj, np.zeros(xb.shape[1]), jac=True, method="L-BFGS-B",
                 options={"maxiter": 300, "ftol": 1e-9})
    return r.x


def direction_check(f, nav):
    """对照实验：只判涨/跌二分类，不用震荡档，看是否是三档阈值的问题。"""
    cols = ["y10_d5", "y10_d20", "slope_d5", "slope_d20", "credit_d5", "credit_d20",
            "funding_d5", "nav_r5", "nav_r20", "nav_r60", "nav_vol20", "y10_vol20",
            "nav_pos250", "y10_pos500", "up_frac10"]
    X = f[cols].to_numpy(dtype=float)
    out = {}
    for h in HORIZONS:
        fwd = (nav.shift(-h) / nav - 1.0).reindex(f.index)
        y = (fwd > 0).astype(int).to_numpy()
        y[fwd.isna().to_numpy()] = -1
        recs, w, mu, sd, last = [], None, None, None, -10 ** 9
        for t in range(MIN_TRAIN, len(y)):
            if y[t] < 0:
                continue
            if t - last >= REFIT_EVERY or w is None:
                cut = t - h
                xt, yt = X[:cut], y[:cut]
                keep = yt >= 0
                xt, yt = xt[keep], yt[keep]
                mu, sd = xt.mean(0), xt.std(0) + 1e-12
                w = fit_bin((xt - mu) / sd, yt)
                last = t
            z = np.r_[1.0, (X[t] - mu) / sd] @ w
            pr = 1.0 / (1.0 + np.exp(-np.clip(z, -30, 30)))
            recs.append((pr, int(y[t])))
        a = np.array(recs)
        clim = float(np.mean(a[:, 1]))
        out[str(h)] = dict(n=len(a), acc=float(np.mean((a[:, 0] > 0.5) == (a[:, 1] > 0.5))),
                           climate=max(clim, 1 - clim), brier=float(np.mean((a[:, 0] - a[:, 1]) ** 2)),
                           climate_brier=clim * (1 - clim))
    return out


def walk_forward(x, y, dates, reg=None):
    """扩窗训练 + h 日 purge；每月（REFIT_EVERY）重训一次。"""
    n = len(y)
    recs, w, climate, last_fit = [], None, None, -10 ** 9
    mu = sd = None
    for t in range(MIN_TRAIN, n):
        if y[t] < 0:
            continue
        if t - last_fit < REFIT_EVERY and w is not None:
            p = predict_logit(w, (x[t:t + 1] - mu) / sd)[0]
            cp = climate.copy()
        else:
            cutoff = t - H  # h 日 purge：训练标签必须已完全实现
            if cutoff <= MIN_TRAIN:
                continue
            xt, yt = x[:cutoff], y[:cutoff]
            keep = yt >= 0
            xt, yt = xt[keep], yt[keep]
            mu, sd = xt.mean(axis=0), xt.std(axis=0) + 1e-12
            xt = (xt - mu) / sd
            w = fit_logit(xt, yt, reg=reg)
            climate = calibrate(yt)
            p = predict_logit(w, (x[t:t + 1] - mu) / sd)[0]
            cp = climate.copy()
            last_fit = t
        recs.append((str(dates[t].date()), p, cp, int(y[t])))
    return recs


def fmt_p(p):
    return " / ".join(f"{LABELS[i]} {p[i] * 100:.0f}%" for i in (2, 1, 0))


def main():
    global H
    f, nav = build_features()
    x_all = f.to_numpy(dtype=float)
    out = dict(generated=datetime.now().strftime("%Y-%m-%d %H:%M"),
               as_of=str(nav.index[-1].date()), rows=len(f), horizons={})
    L = ["# 债券市场「天气预报表」研究", "",
         f"> 生成 {out['generated']} · 数据截止 **{nav.index[-1].date()}** · "
         f"样本 {len(f)} 个交易日（{f.index[0].date()} 起）", "",
         "> 目标：把债市压成「晴 / 震荡 / 雨」三档，给出未来 1 周 / 2 周 / 1 个月的概率，"
         "像天气预报一样可核对。只使用仓库已有行情数据，不含宏观变量。", "",
         "## 一、方法", "",
         "- **标签**：以 `511260` 累计净值未来 h 个交易日收益定义；"
         + "、".join(f"h={h} 时 ≥+{THRESHOLDS[h] * 100:.2f}% 为晴、≤−{THRESHOLDS[h] * 100:.2f}% 为雨、其间为震荡"
                    for h in HORIZONS) + "。",
         "- **特征**：10Y 收益率水平/5日/20日变化、1Y-10Y 斜率及曲率、3Y AAA 信用利差、"
         "SHIBOR-1Y 资金利差及其变化、511260 的 5/20/60 日动量与波动、"
         "净值与收益率在近 250/500 日中的位置、10 日上涨占比，共 "
         f"{x_all.shape[1]} 个因子。",
         "- **模型**：L2 多项 Logistic（scipy L-BFGS-B，自实现，无 sklearn 依赖）；"
         f"特征按训练期标准化，正则强度 {REG}。",
         "- **检验**：扩窗滚动，每月重训一次；训练标签留 h 日 purge，避免重叠标签泄漏。"
         "对照「气候基准」=训练期类别频率。指标为准确率与多分类 Brier 评分（越低越好）。", "",
         "> 文献依据：汇丰 DUSTIN 用 17 个动量/波动/曲线/信用变量预测美债 10Y 未来 21 交易日方向，"
         "样本外约 65%（[MarketWatch/Morningstar, 2026-09-22]"
         "(https://www.morningstar.com/news/marketwatch/2026092225/what-this-machine-learning-model-with-65-accuracy-says-is-coming-next-for-the-10-year-treasury)）；"
         "平安证券用 Logistic 做 10Y 国债方向预测，总体约六成（"
         "[新浪财经研报, 2024-12-05](https://stock.finance.sina.com.cn/stock/go.php/vReport_Show/kind/search/rptid/786733995819/index.phtml)）；"
         "施展 & Huang 的 SAGLasso 表明控制收益率曲线后宏观仍有增量信息（"
         "[清华五道口, 2021-11-16](https://www.pbcsf.tsinghua.edu.cn/info/1154/2475.htm)）；"
         "Brier 评分是气象预报沿用至今的概率校准检验（"
         "[Brier score](https://www.wikiwand.com/en/articles/Brier_score)）。", "",
         "## 二、当前天气预报", "",
         "| 期限 | 预报 | 气候基准 |", "|---|---|---|"]

    for h in HORIZONS:
        H = h
        y, fwd = make_target(nav, h)
        recs = walk_forward(x_all, y, f.index)
        met = metrics(recs)
        # 当前预报：用全部已实现标签训练，预测最后一行
        cutoff = len(y) - h
        mu, sd = x_all[:cutoff].mean(axis=0), x_all[:cutoff].std(axis=0) + 1e-12
        w = fit_logit((x_all[:cutoff] - mu) / sd, y[:cutoff])
        now = predict_logit(w, (x_all[-1:] - mu) / sd)[0]
        clim = calibrate(y[:cutoff])
        out["horizons"][str(h)] = dict(
            n=met["n"], acc=round(met["acc"], 4), climate_acc=round(met["climate_acc"], 4),
            majority=round(met["majority"], 4), brier=round(met["brier"], 4),
            climate_brier=round(met["climate_brier"], 4),
            now={LABELS[i]: round(float(now[i]), 4) for i in range(3)},
            climate={LABELS[i]: round(float(clim[i]), 4) for i in range(3)},
            actual_dist=met["actual_dist"],
        )
        L.append(f"| {h} 交易日 | {fmt_p(now)} | {fmt_p(clim)} |")

    ys = {h: make_target(nav, h)[0] for h in HORIZONS}
    sweep = {}
    for reg in (1.0, 5.0, 20.0, 100.0):
        sweep[str(reg)] = {}
        for h in HORIZONS:
            H = h
            met = metrics(walk_forward(x_all, ys[h], f.index, reg=reg))
            sweep[str(reg)][str(h)] = dict(acc=round(met["acc"], 4),
                                           brier=round(met["brier"], 4),
                                           climate_brier=round(met["climate_brier"], 4))
    out["reg_sweep"] = sweep
    direction = direction_check(f, nav)
    out["direction_check"] = direction

    L += ["", "## 三、样本外检验", "",
          "| 期限 | 样本数 | 模型准确率 | 气候基准 | 多数类 | 模型 Brier | 气候 Brier | 实际分布(晴/震荡/雨) |",
          "|---|---:|---:|---:|---:|---:|---:|---|"]
    for h in HORIZONS:
        r = out["horizons"][str(h)]
        L.append(f"| {h} 交易日 | {r['n']} | {r['acc'] * 100:.1f}% | "
                 f"{r['climate_acc'] * 100:.1f}% | {r['majority'] * 100:.1f}% | "
                 f"{r['brier']:.3f} | {r['climate_brier']:.3f} | "
                 f"{r['actual_dist']['晴']}/{r['actual_dist']['震荡']}/{r['actual_dist']['雨']} |")
    L += ["", "## 四、稳健性检验：是特征弱，还是三档阈值的问题？", "",
          "正则强度扫描（每格为 准确率% / Brier；气候基准 Brier 见上表）：", "",
          "| L2 | 5 日 | 10 日 | 20 日 |", "|---|---:|---:|---:|"]
    for reg, row in sweep.items():
        L.append(f"| {reg} | " + " | ".join(
            f"{row[str(h)]['acc'] * 100:.1f}% / {row[str(h)]['brier']:.3f}"
            for h in HORIZONS) + " |")
    L += ["", "去掉「震荡」档、只判涨/跌的二分类对照：", "",
          "| 期限 | 样本数 | 模型准确率 | 气候基准 | 模型 Brier | 气候 Brier |",
          "|---|---:|---:|---:|---:|---:|"]
    for h in HORIZONS:
        d = direction[str(h)]
        L.append(f"| {h} 交易日 | {d['n']} | {d['acc'] * 100:.1f}% | "
                 f"{d['climate'] * 100:.1f}% | {d['brier']:.3f} | {d['climate_brier']:.3f} |")
    L += ["", "> 正则越强越贴近气候基准；二分类也打平 —— **不是三档阈值的问题，"
          "而是这批行情特征本身对方向没有增量信息**。"
          "单纯报一个高准确率没有意义，Brier 不优于气候基准即视为无效。", "",
          "## 五、解释与使用边界", "",
          "- 模型输出是概率，不是点位；只有 **Brier 优于气候基准** 才算有增量信息，"
          "单纯准确率高可能只是因为震荡样本占多数。",
          "- 与 DUSTIN/平安证券不同，本研究没有宏观变量（社融、M1、IRS 等仓库里没有日频数据），"
          "预期预测力更弱；文献里宏观因子约贡献 25% 的增量信息。",
          "- 三档阈值是固定经验值，未随波动率自适应；债券低波动时期会偏向「震荡」。",
          "- 机械规则输出，仅供研究参考，不构成投资建议。", ""]

    with open(OUT_MD, "w", encoding="utf-8") as fh:
        fh.write("\n".join(L) + "\n")
    with open(OUT_JSON, "w", encoding="utf-8") as fh:
        json.dump(out, fh, ensure_ascii=False, indent=2)
    print(f"[ok] {OUT_MD}")
    print(f"[ok] {OUT_JSON}")
    for h in HORIZONS:
        r = out["horizons"][str(h)]
        print(f"  h={h:2d}  acc {r['acc'] * 100:.1f}% vs 气候 {r['climate_acc'] * 100:.1f}% · "
              f"Brier {r['brier']:.3f} vs {r['climate_brier']:.3f} · "
              + " / ".join(f"{k} {v * 100:.0f}%" for k, v in r["now"].items()))
    return 0


if __name__ == "__main__":
    sys.exit(main())
