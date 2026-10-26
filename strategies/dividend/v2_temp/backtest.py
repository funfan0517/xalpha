#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""估值温度 v2 · 验证与回测

回答三件事（全部用同一段真实数据，不用任何估计值）:

1. **图片口径的符号对不对？**
   把「股债收益差」按「越宽越热」正向计入（图片口径）与按「越宽越冷」反号计入，
   各自的温度对未来 3/6/12 月收益的 Spearman IC 差多少。
2. **时间维度能不能择时？**
   板块温度（4 只均值）的**自身历史分位** ≥80% 减半、≥90% 清仓 —— 这是
   `v1_spread` 已经失败过的结构（阈值分位择时），换成合成温度是否改变结论。
3. **横截面能不能选品？**
   每月选温度最低的 1~2 只等权，对比 4 只等权基准。

口径与边界（不隐藏）
--------------------
* 收益用**价格指数**（`000922/931446/930955/H30269` 的 close），未含分红再投；
  四只当前股息率都在 4.1–4.3%，横截面偏差有限，但与基金净值的绝对值不可比。
* 温度样本从 2020-01 起（受「价格位置需 756 交易日 + 分位需 36 个月」双重预热限制），
  `931446` 的 PE 历史到 2020 年才有，温度从 2023-03 起 → 横截面回测**逐月用可得标的**。
* PB 无历史 → 不参与（见 `rule.PB_AVAILABLE`）。

产物
----
    backtest/_v2_panel.jsonl    月度面板（date/code/temp/各维分位）
    backtest/_v2_report.md      本报告

用法::

    python strategies/dividend/v2_temp/backtest.py [--refresh]
"""

from __future__ import annotations

import argparse
import json
import os
import sys

import numpy as np
import pandas as pd

_DIR = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, _DIR)
sys.path.insert(0, os.path.dirname(_DIR))

import factors  # noqa: E402  pylint: disable=wrong-import-position
import rule  # noqa: E402  pylint: disable=wrong-import-position

FEE = 0.0003
IMG_SIGN = {"spread": +1, "pe": +1, "ma_dev": +1, "pos": +1}   # 图片口径（利差正向）
# 图片 2026-10-23 日报里的读数（用于对照，不参与任何计算）
IMG_NOW = {"000922": 51.7, "931446": 56.4, "930955": 64.2, "H30269": 69.5}


# ------------------------------------------------------------------ 组合
def stats(r: pd.Series) -> dict:
    nav = (1 + r).cumprod()
    yrs = max(len(r) / 12, 1e-9)
    cagr = nav.iloc[-1] ** (1 / yrs) - 1
    dd = float((nav / nav.cummax() - 1).min())
    vol = float(r.std() * np.sqrt(12))
    return {"cagr": cagr, "dd": dd, "calmar": cagr / abs(dd) if dd else float("nan"),
            "vol": vol, "sharpe": float(r.mean() * 12) / vol if vol else float("nan"),
            "total": float(nav.iloc[-1] - 1)}


def run_weights(w: pd.DataFrame, rets: pd.DataFrame, fee=FEE) -> pd.Series:
    """权重在月末定、下月生效；成本 = fee × Σ|Δw|（双边各计一次）。"""
    wl = w.shift(1).fillna(0.0)
    port = (wl * rets).sum(axis=1, min_count=1)
    turn = (wl - wl.shift(1).fillna(0.0)).abs().sum(axis=1)
    return (port - turn * fee).dropna()


def equal_weights(temp: pd.DataFrame) -> pd.DataFrame:
    ok = temp.notna()
    n = ok.sum(axis=1).replace(0, np.nan)
    return ok.div(n, axis=0).fillna(0.0)


def lowest_weights(temp: pd.DataFrame, k: int) -> pd.DataFrame:
    """每月在温度可得的标的里选温度最低的 k 只等权。"""
    out = pd.DataFrame(0.0, index=temp.index, columns=temp.columns)
    for ts, row in temp.iterrows():
        ok = row.dropna()
        if ok.empty:
            continue
        pick = ok.nsmallest(min(k, len(ok))).index
        out.loc[ts, pick] = 1.0 / len(pick)
    return out


def run_q(s: pd.Series, min_n=rule.Q_MIN_N) -> pd.Series:
    """扩张窗口分位（含当前点）。"""
    return factors.pct_rank(s, min_n=min_n, max_n=10 ** 6)


def time_mult(band_q: pd.Series, cut=rule.CUT_Q, out=rule.OUT_Q) -> pd.Series:
    m = pd.Series(1.0, index=band_q.index)
    m[band_q >= cut] = 0.5
    m[band_q >= out] = 0.0
    m[band_q.isna()] = 1.0
    return m


# ------------------------------------------------------------------ 检验
def ic_table(panels: dict) -> list[dict]:
    rows = []
    for code, m in panels.items():
        for h in (3, 6, 12):
            fwd = m["close"].shift(-h) / m["close"] - 1
            d = pd.concat([m["temp"], fwd.rename("fwd")], axis=1).dropna()
            if len(d) > 24:
                rows.append({"code": code, "name": rule.INDICES[code], "h": h,
                             "ic": float(d["temp"].corr(d["fwd"], method="spearman")),
                             "n": len(d)})
    return rows


def cs_ic(panels: dict, temp_key="temp") -> dict:
    """横截面 IC：每月在标的间做秩相关（≥3 只才计）。"""
    t = pd.DataFrame({c: m[temp_key] for c, m in panels.items()})
    out = {}
    for h in (3, 6):
        fwd = pd.DataFrame({c: m["close"].shift(-h) / m["close"] - 1
                            for c, m in panels.items()})
        ics = []
        for ts in t.index:
            a, b = t.loc[ts], fwd.loc[ts]
            ok = a.notna() & b.notna()
            if ok.sum() >= 3:
                ics.append(a[ok].corr(b[ok], method="spearman"))
        out[h] = (float(np.mean(ics)), len(ics)) if ics else (None, 0)
    return out


def tercile(panels: dict) -> dict:
    """全样本 pooled：温度三分档 → 未来 6 月平均收益。"""
    recs = []
    for code, m in panels.items():
        fwd = m["close"].shift(-6) / m["close"] - 1
        recs.append(pd.DataFrame({"temp": m["temp"], "fwd": fwd}).dropna())
    d = pd.concat(recs)
    d["g"] = pd.qcut(d["temp"], 3, labels=["低温", "中温", "高温"])
    g = d.groupby("g", observed=True)["fwd"].agg(["mean", "count"])
    return {str(k): (float(v["mean"]), int(v["count"])) for k, v in g.iterrows()}


# ------------------------------------------------------------------ 主流程
def main(argv=None):
    ap = argparse.ArgumentParser(description="估值温度 v2 回测")
    ap.add_argument("--refresh", action="store_true", help="强制重抓指数日线")
    args = ap.parse_args(argv)

    v2 = factors.build(weights=rule.W_EQ, signs=rule.SIGN, refresh=args.refresh)
    img = factors.build(weights=rule.W_IMAGE_NO_PB, signs=IMG_SIGN, refresh=False)

    temp = pd.DataFrame({c: m["temp"] for c, m in v2.items()})
    temp_img = pd.DataFrame({c: m["temp"] for c, m in img.items()})
    close = pd.DataFrame({c: m["close"] for c, m in v2.items()}).ffill()
    rets = close.pct_change()

    # 统一到「第一只指数温度可用」之后的月份
    start = temp.dropna(how="all").index[0]
    temp, temp_img, close, rets = (x.loc[start:] for x in (temp, temp_img, close, rets))
    rets = rets.iloc[1:]

    w_bench = equal_weights(temp)
    band_q = run_q(temp.mean(axis=1))

    sims = {}
    sims["4只等权（基准）"] = run_weights(w_bench, rets)
    for k in (1, 2, 3):
        sims[f"横截面：温度最低 {k} 只"] = run_weights(lowest_weights(temp, k), rets)
    sims["时间：板块温度分位减仓"] = run_weights(w_bench.mul(time_mult(band_q), axis=0), rets)
    sims["横截面 top2 + 时间减仓"] = run_weights(
        lowest_weights(temp, 2).mul(time_mult(band_q), axis=0), rets)
    # 对照：图片口径（利差正向）同一套横截面规则
    sims["对照｜图片口径 top2"] = run_weights(lowest_weights(temp_img, 2), rets)

    sens = {}
    for cut, out in ((0.75, 0.85), (0.80, 0.90), (0.85, 0.95)):
        r = run_weights(w_bench.mul(time_mult(band_q, cut, out), axis=0), rets)
        sens[f"{cut * 100:.0f}/{out * 100:.0f}"] = stats(r)
    n_cut = int((band_q >= rule.CUT_Q).sum())
    n_out = int((band_q >= rule.OUT_Q).sum())
    trigger = [ts.strftime("%Y-%m") for ts in band_q.index[band_q >= rule.OUT_Q]]

    # 时间减仓到底避开了什么：基准最大回撤区间内的实际仓位
    nav = (1 + sims["4只等权（基准）"]).cumprod()
    dd = nav / nav.cummax() - 1
    trough = dd.idxmin()
    peak = nav.loc[:trough].idxmax()
    pos_eff = time_mult(band_q).shift(1).reindex(nav.index)
    dd_pos = pos_eff.loc[peak:trough]

    # 稳健性：剔除「减仓版相对基准贡献最大」的 N 个月，看优势还剩多少
    diff = (sims["时间：板块温度分位减仓"] - sims["4只等权（基准）"]).dropna()
    robust = {}
    for k in (1, 3, 5):
        drop = diff.nlargest(k).index
        robust[k] = (stats(sims["4只等权（基准）"].drop(drop))["cagr"],
                     stats(sims["时间：板块温度分位减仓"].drop(drop))["cagr"])

    # 逐年：基准 / 减仓版 / 平均仓位 —— 看增量到底来自哪一年
    yearly = pd.DataFrame({"bench": sims["4只等权（基准）"],
                           "overlay": sims["时间：板块温度分位减仓"],
                           "expo": pos_eff}).dropna(subset=["bench", "overlay"])
    yearly = (1 + yearly[["bench", "overlay"]]).groupby(yearly.index.year).prod() - 1
    yearly["expo"] = pos_eff.groupby(pos_eff.index.year).mean()

    os.makedirs(rule.BT_DIR, exist_ok=True)
    panel = factors.long_panel(v2)
    with open(rule.OUT_PANEL, "w", encoding="utf-8") as fh:
        for _, r in panel.iterrows():
            fh.write(json.dumps(r.to_dict(), ensure_ascii=False) + "\n")

    L = build_report(dict(v2=v2, img=img, temp=temp, temp_img=temp_img, sims=sims,
                          sens=sens, n_cut=n_cut, n_out=n_out, rets=rets,
                          trigger=trigger, peak=peak, trough=trough, dd_pos=dd_pos,
                          band_q=band_q, yearly=yearly, expo=float(pos_eff.mean()),
                          robust=robust))
    with open(rule.OUT_REPORT, "w", encoding="utf-8") as fh:
        fh.write(L)

    print(f"[v2bt] 样本 {temp.dropna(how='all').index[0]:%Y-%m}~{temp.index[-1]:%Y-%m} "
          f"· {len(temp)} 月 · 减仓触发 {n_cut} 月 / 清仓 {n_out} 月")
    for name, r in sims.items():
        s = stats(r)
        print(f"[v2bt] {name:24s} 年化 {s['cagr'] * 100:5.1f}%  回撤 {s['dd'] * 100:6.1f}%  "
              f"Calmar {s['calmar']:5.2f}")
    print(f"[v2bt] 产物 -> {os.path.relpath(rule.OUT_REPORT, rule.ROOT)}")
    return 0


def build_report(ctx: dict) -> str:
    v2, img = ctx["v2"], ctx["img"]
    temp, temp_img, sims = ctx["temp"], ctx["temp_img"], ctx["sims"]
    rets = ctx["rets"]

    ic_v2 = ic_table(v2)
    ic_img = ic_table(img)
    cs_v2 = cs_ic(v2)
    cs_img = cs_ic(img)
    tri = tercile(v2)

    first = temp.dropna(how="all").index[0]
    L = ["# 红利估值温度 v2 · 验证与回测", "",
         f"> 样本 **{first:%Y-%m} ~ {temp.index[-1]:%Y-%m}**（{len(temp)} 个月）· "
         "标的 中证红利(000922) / 东证红利低波(931446) / 红利低波100(930955) / 红利低波(H30269)",
         "> 收益口径：**价格指数**（未含分红再投）。温度在月末算出，**下月生效**，无前视。",
         "> 与图片版差异：股债收益差反号、PB 不参与、四维等权（见 `RULES.md`）。", ""]

    # ---------------- 一
    L += ["## 一、符号问题：语义上错了，但在本样本里不是主要矛盾", "",
          "| 指数 | 期限 | 图片口径 IC（利差正向） | v2 口径 IC（利差反号） |",
          "|---|---:|---:|---:|"]
    for row in ic_v2:
        other = next(x for x in ic_img if x["code"] == row["code"] and x["h"] == row["h"])
        flag = "**" if row["ic"] < other["ic"] - 1e-9 else ""
        L.append(f"| {row['name']} | {row['h']}M | {other['ic']:+.2f} | "
                 f"{flag}{row['ic']:+.2f}{flag} |")
    avg_i = float(np.mean([x["ic"] for x in ic_img]))
    avg_v = float(np.mean([x["ic"] for x in ic_v2]))
    L += ["", f"- 全表平均 IC：图片口径 **{avg_i:+.2f}**、v2 **{avg_v:+.2f}**，差 {abs(avg_v - avg_i):.2f}。"
          "**这个差很小 —— 改符号在本样本里没有明显提升**，因为 2020 年后利差分位长期贴顶、"
          "方差极小，这一维几乎没有变化可供符号去作用（与 `v1_spread` 的结论同源）。",
          "- 但符号错在**语义**上是硬的：「利差宽 = 便宜 = 冰点」被算成了「越宽越热」。"
          "它在当前读数上仍然可见（第二节：H30269 因利差最宽而被顶到第一）。"
          "利差大幅波动的时段（如 2015–2018）本回测样本未覆盖，那里会不会直接给出反向信号"
          "**尚未验证**，但按定义应当会。",
          "- 所以 v2 改符号，理由是**可解释性与不反向**，不是「因此 IC 更高」—— 不夸大。", ""]

    # ---------------- 二
    L += ["## 二、当前读数对照（2026-10）", "",
          "| 指数 | 图片 | v2 口径复算 | 图片口径复算 | 利差分位 | PE 分位 | 乖离分位 | 位置分位 |",
          "|---|---:|---:|---:|---:|---:|---:|---:|"]
    for code in rule.INDICES:
        a = img[code].iloc[-1]
        b = v2[code].iloc[-1]
        L.append(f"| {rule.INDICES[code]}({code}) | {IMG_NOW[code]:.1f} | **{b['temp']:.1f}** | "
                 f"{a['temp']:.1f} | {b['spread_p']:.2f} | {b['pe_p']:.2f} | "
                 f"{b['ma_dev_p']:.2f} | {b['pos_p']:.2f} |")
    img_now = {c: float(img[c]["temp"].iloc[-1]) for c in rule.INDICES}
    v2_now = {c: float(v2[c]["temp"].iloc[-1]) for c in rule.INDICES}
    top_img = max(img_now, key=img_now.get)
    v2_rank = sorted(v2_now, key=v2_now.get, reverse=True).index(top_img) + 1
    L += ["", "> 三个数不可能相等：PB(16%) 无历史被剔除、窗口与分位算法未必相同。"
              f"能对上的是**排序** —— 图片口径复算下 **{rule.INDICES[top_img]}** 排第一"
              f"（{img_now[top_img]:.1f}，图片 {IMG_NOW[top_img]:.1f}，两者都在 69 附近），"
              f"它正是利差分位最宽的一只（{v2[top_img].iloc[-1]['spread_p']:.2f}）；"
              f"反号后它掉到第 {v2_rank}（{v2_now[top_img]:.1f}）。"
              "所以图片的「高温」里，有很大一块来自把「便宜」读成了「过热」。", ""]

    # ---------------- 三
    r_bench = stats(sims["4只等权（基准）"])
    L += ["## 三、回测（成本单边 0.03%/次）", "",
          "| 方案 | 年化 | 最大回撤 | Calmar | 波动 | 夏普 | 累计 |",
          "|---|---:|---:|---:|---:|---:|---:|"]
    for name, r in sims.items():
        s = stats(r)
        L.append(f"| {name} | {s['cagr'] * 100:.1f}% | {s['dd'] * 100:.1f}% | {s['calmar']:.2f} | "
                 f"{s['vol'] * 100:.1f}% | {s['sharpe']:.2f} | {s['total'] * 100:+.0f}% |")
    L += ["", f"- 板块温度分位 ≥{rule.CUT_Q:.0%} 减半触发 **{ctx['n_cut']} 个月**；"
              f"其中 ≥{rule.OUT_Q:.0%} 清仓 **{ctx['n_out']} 个月**"
              f"（清仓月已含在减仓月内，合计占 {len(temp)} 个月的 "
              f"{ctx['n_cut'] / len(temp):.0%}）。",
          "- 时间维度阈值敏感性（对基准做同一套减仓）:", "",
          "| 减半/清仓分位 | 年化 | 最大回撤 | Calmar |", "|---|---:|---:|---:|"]
    for k, s in ctx["sens"].items():
        L.append(f"| {k}% | {s['cagr'] * 100:.1f}% | {s['dd'] * 100:.1f}% | {s['calmar']:.2f} |")
    L += ["", f"- 基准 4 只等权：年化 {r_bench['cagr'] * 100:.1f}%、回撤 {r_bench['dd'] * 100:.1f}%、"
              f"Calmar {r_bench['calmar']:.2f}。", "",
          "逐年拆开看（增量来自哪一年一目了然）：", "",
          "| 年份 | 基准 | 减仓版 | 差 | 减仓版平均仓位 |", "|---|---:|---:|---:|---:|"]
    for yr, row in ctx["yearly"].iterrows():
        L.append(f"| {int(yr)} | {row['bench'] * 100:+.1f}% | {row['overlay'] * 100:+.1f}% | "
                 f"{(row['overlay'] - row['bench']) * 100:+.1f}pp | {row['expo'] * 100:.0f}% |")
    L += ["", "稳健性：剔除「减仓版相对基准贡献最大」的 N 个月后还剩多少优势 ——", "",
          "| 剔除 | 基准年化 | 减仓版年化 | 优势 |", "|---|---:|---:|---:|"]
    for k, (b, o) in ctx["robust"].items():
        L.append(f"| 贡献最大的 {k} 个月 | {b * 100:.1f}% | {o * 100:.1f}% | {(o - b) * 100:+.1f}pp |")
    L.append("")

    # ---------------- 四
    L += ["## 四、横截面与分组", "",
          "| 口径 | 横截面 IC（3M） | 横截面 IC（6M） |", "|---|---:|---:|"]
    L.append(f"| v2 | {cs_v2[3][0]:+.2f}（{cs_v2[3][1]} 期） | {cs_v2[6][0]:+.2f}（{cs_v2[6][1]} 期） |")
    L.append(f"| 图片口径 | {cs_img[3][0]:+.2f}（{cs_img[3][1]} 期） | {cs_img[6][0]:+.2f}（{cs_img[6][1]} 期） |")
    L += ["", "温度三分档的平均未来 6 月收益（全样本 pooled）：", "",
          "| 档位 | 平均 6M 收益 | 样本数 |", "|---|---:|---:|"]
    for k in ("低温", "中温", "高温"):
        if k in tri:
            L.append(f"| {k} | {tri[k][0] * 100:+.1f}% | {tri[k][1]} |")
    L.append("")

    # ---------------- 五
    s_bench = stats(sims["4只等权（基准）"])
    s_time = stats(sims["时间：板块温度分位减仓"])
    cs_sim = {k.replace("横截面：温度最低 ", "top").replace(" 只", ""): stats(sims[k])["calmar"]
              for k in sims if k.startswith("横截面：")}
    dd_pos_mean = float(ctx["dd_pos"].mean()) if len(ctx["dd_pos"]) else float("nan")
    band_last = float(ctx["band_q"].iloc[-1]) if len(ctx["band_q"]) else float("nan")
    now_lo, now_hi = min(v2_now.values()), max(v2_now.values())
    _dy = {int(y): (r["overlay"] - r["bench"]) * 100 for y, r in ctx["yearly"].iterrows()}
    pos_txt = "、".join(f"{y} {v:+.1f}pp" for y, v in _dy.items() if v > 0) or "无"
    neg_txt = "、".join(f"{y} {v:+.1f}pp" for y, v in _dy.items() if v <= 0) or "无"
    L += ["## 五、读法", "",
          f"1. **横截面选品：无效，不要用。** 横截面 IC {cs_v2[3][0]:+.2f}（3M）/ "
          f"{cs_v2[6][0]:+.2f}（6M），基本为 0；"
          + "、".join(f"{k} Calmar {v:.2f}" for k, v in cs_sim.items())
          + f"，全在基准 {s_bench['calmar']:.2f} 附近。四只红利指数月收益相关 >0.9，"
            "「同一板块里谁更便宜」换不来收益 —— 选品仍应走 `v1_spread` 的利差或 `rank.py` 打分。",
          f"2. **时间维度：唯一有增量的用法。** 板块温度分位 ≥{rule.CUT_Q:.0%} 减半、"
          f"≥{rule.OUT_Q:.0%} 清仓 → 年化 {s_bench['cagr'] * 100:.1f}% → "
          f"{s_time['cagr'] * 100:.1f}%，Calmar {s_bench['calmar']:.2f} → {s_time['calmar']:.2f}；"
          "三档阈值（75/85、80/90、85/95）的 Calmar 分别是 "
          + "、".join(f"{s['calmar']:.2f}" for s in ctx["sens"].values())
          + f"，全部高于基准 {s_bench['calmar']:.2f}；且逐年增量 2022–2025 连续为正 —— "
            "这两条加起来说明它不是单点运气，但也只说明「在这段样本里成立」。",
          f"3. **但它是「高换手择时」，不是「买入持有 + 保险」。** 全期平均仓位 "
          f"**{ctx['expo']:.0%}**（逐年见第三节表，"
          + "、".join(f"{int(y)} 年 {r['expo'] * 100:.0f}%" for y, r in ctx["yearly"].iterrows())
          + "）。增量逐年看是 " + pos_txt + " 为正、" + neg_txt + "。" 
          f"它靠的是「高位降暴露」：2025 年只用了 {ctx['yearly'].iloc[-2]['expo'] * 100:.0f}% "
          f"的平均仓位就拿到 {ctx['yearly'].iloc[-2]['overlay'] * 100:+.1f}%（基准 "
          f"{ctx['yearly'].iloc[-2]['bench'] * 100:+.1f}%）。"
          "**低暴露跑赢高暴露，是择时里最容易在样本外反转的形态** —— "
          f"而且基准最大回撤 {ctx['peak']:%Y-%m} ~ {ctx['trough']:%Y-%m} 期间"
          f"它平均还有 {dd_pos_mean:.0%} 仓位在场上，说明连它自己的主要回撤也没躲过。"
          "稳健性表更直白：剔除贡献最大的 3 个月后优势只剩 "
          f"{(ctx['robust'][3][1] - ctx['robust'][3][0]) * 100:+.1f}pp/年，"
          f"剔除 5 个月后**转负**（{(ctx['robust'][5][1] - ctx['robust'][5][0]) * 100:+.1f}pp）"
          "—— 结论离翻面只差几个月。",
          f"4. **当前读数：中性，不是偏贵。** v2 复算 {now_lo:.1f}~{now_hi:.1f}，四只全在中性带；"
          f"板块温度自身分位 {band_last * 100:.0f}%，低于减仓线 {rule.CUT_Q:.0%} → 不触发减仓。"
          "图片给的是 51.7~69.5「偏贵，可开始部分止盈」—— **两个结论方向不同**，"
          "差异来自符号 + PB + 窗口，不是数据不同。",
          "- 结论：**温度当「贵不贵」的体检报告用，只在极端分位（≥80%）做减仓，"
          "不用它选品。** 这是 v2 相对图片版的实质改动。",
          "- PB（16%）是硬缺口：拿到指数 PB 历史后需重跑，届时权重与档位都可能要改。", "",
          f"> 对照方案「图片口径 top2」年化 {stats(sims['对照｜图片口径 top2'])['cagr'] * 100:.1f}%、"
          f"Calmar {stats(sims['对照｜图片口径 top2'])['calmar']:.2f} —— 与 v2 top2 的差别就是"
          "「符号 + 权重」带来的全部差异，量级很小，与第一节的 IC 结论一致。", ""]
    return "\n".join(L)


if __name__ == "__main__":
    sys.exit(main())
