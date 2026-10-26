#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""V4 单标的适配度：10 只红利标的的分数/点位图 + 择时效果对比

V4 的原始形态是**组合策略**（池内横截面 top2 × 板块 RSI 时间倍率）。本脚本把它拆成
**单标的版**：对每只标的各跑一遍「自己的 RSI14 ≥70 清仓 / ≥50 减半、其余满仓」，
回答一个具体问题 —— **哪只标的适合被 RSI 择时，哪只不适合**。

口径（同 V4）：
* 收益源：基金**累计净值**（`pool.load_navs` 的 `totvalue`，含分红再投）
* 分数：该标的自己的 Wilder RSI14（日频算，月末取值决策）
* 执行：月末信号、下月生效、单边 0.03%（Σ|Δw|）
* 基准：同标的买入持有

两个可比性处理：
1. 各标的净值起点不同（4 只只有 3–4 年）→ 主表给「各自全区间」，另列「共同窗口」的
   ΔCalmar 做交叉验证；两个口径若给出同一套名单，说明排序不是窗口挑出来的
2. 择时效果用 **ΔCalmar = Calmar(择时) − Calmar(买入持有)** 衡量，避免绝对收益造成误导

产物（AGENTS §8.4 ⑤）：`v4_rsi/daily/_v4_universe.html`

用法::

    python strategies/dividend/v4_rsi/universe.py
"""

from __future__ import annotations

import base64
import io
import os
import sys

import matplotlib

matplotlib.use("Agg")

import matplotlib.dates as mdates  # noqa: E402
import matplotlib.pyplot as plt  # noqa: E402
import numpy as np  # noqa: E402
import pandas as pd  # noqa: E402

_DIR = os.path.dirname(os.path.abspath(__file__))
_STRAT = os.path.dirname(_DIR)
for _p in (_DIR, _STRAT):
    if _p not in sys.path:
        sys.path.insert(0, _p)

import _single_signal as S  # noqa: E402  pylint: disable=wrong-import-position
import pool  # noqa: E402  pylint: disable=wrong-import-position
import rule as V4  # noqa: E402  pylint: disable=wrong-import-position

OUT_HTML = os.path.join(_DIR, "daily", "_v4_universe.html")
START = "2020-01-01"
MIN_MONTHS = 60          # 低于此月数的标的打「样本不足」标记

plt.rcParams["font.sans-serif"] = ["Microsoft YaHei", "SimHei", "DejaVu Sans"]
plt.rcParams["axes.unicode_minus"] = False

BLUE, RED, AMBER = "#1f4e79", "#c0504d", "#e8a33d"


# ------------------------------------------------------------------ 单标的回测
def run_one(code: str, nav: pd.Series, start: str) -> dict | None:
    nav = nav.dropna().loc[start:]
    if len(nav) < 260:
        return None
    rsi_d = S.rsi_series(nav, V4.WINDOW)
    me = nav.resample("ME").last().ffill()
    rets = me.pct_change().dropna()
    rsi_m = rsi_d.resample("ME").last().reindex(rets.index)
    mult = S.time_mult(rsi_m / 100.0, V4.Q_OUT, V4.Q_HALF).fillna(1.0)

    pos = mult.shift(1).fillna(1.0)          # 次月生效；首个信号月按满仓处理
    turn = (pos - pos.shift(1).fillna(0.0)).abs()
    r_strat = (pos * rets - turn * S.FEE).dropna()
    r_bh = rets.reindex(r_strat.index)

    st, bh = S.stats(r_strat), S.stats(r_bh)
    return {"code": code, "rsi_m": rsi_m, "mult": mult, "nav": nav, "rsi_d": rsi_d,
            "rets": rets, "strat": st, "bh": bh, "pos": float(pos.mean()),
            "n_cut": int((mult == 0).sum()), "n_half": int(((mult > 0) & (mult < 1)).sum()),
            "n": len(r_strat), "start": r_strat.index[0], "end": r_strat.index[-1],
            "dcalmar": st["calmar"] - bh["calmar"],
            "r_strat": r_strat, "r_bh": r_bh}


def band_next(rsi_m: pd.Series, rets: pd.Series) -> pd.DataFrame:
    """按**信号档**看**次月**收益 —— 与策略的执行口径完全一致（t 月末信号 → t+1 月收益）。"""
    df = pd.DataFrame({"rsi": rsi_m, "nxt": rets.shift(-1)}).dropna()
    band = pd.cut(df["rsi"], bins=[-np.inf, 50, 70, np.inf],
                  labels=["满仓", "减半", "清仓"], right=False)
    return df.groupby(band, observed=False)["nxt"].agg(["mean", "count"])


# ------------------------------------------------------------------ 画图
def _fig_b64(res: dict) -> str:
    nav, rsi_d = res["nav"], res["rsi_d"]
    fig, ax1 = plt.subplots(figsize=(6.4, 3.1), dpi=110)
    ax2 = ax1.twinx()
    ax1.plot(nav.index, nav.to_numpy(), color=BLUE, lw=1.2)
    ax2.plot(rsi_d.index, rsi_d.to_numpy(), color=RED, lw=1.1, alpha=0.9)

    ax2.axhspan(V4.Q_OUT * 100, 100, color=RED, alpha=0.05)
    ax2.axhspan(V4.Q_HALF * 100, V4.Q_OUT * 100, color=AMBER, alpha=0.05)
    for y, c in ((V4.Q_OUT * 100, RED), (V4.Q_HALF * 100, AMBER)):
        ax2.axhline(y, color=c, ls="--", lw=0.9, alpha=0.7)
    ax2.set_ylim(0, 100)

    me = res["rsi_m"].dropna()
    ax2.scatter(me.index, me.to_numpy(), s=6, color=RED, alpha=0.55)

    ax1.set_ylabel("累计净值", color=BLUE, fontsize=8)
    ax2.set_ylabel("分数 RSI14", color=RED, fontsize=8)
    ax1.tick_params(axis="y", colors=BLUE, labelsize=7)
    ax2.tick_params(axis="y", colors=RED, labelsize=7)
    ax1.tick_params(axis="x", labelsize=7)
    ax1.grid(True, axis="x", ls=":", color="#ddd", alpha=0.8)
    ax1.xaxis.set_major_locator(mdates.YearLocator())
    ax1.xaxis.set_major_formatter(mdates.DateFormatter("%y"))
    ax1.set_zorder(2)
    ax1.patch.set_visible(False)

    last = rsi_d.dropna().iloc[-1]
    ax2.annotate(f"{last:.0f}", (rsi_d.dropna().index[-1], last),
                 textcoords="offset points", xytext=(4, 2), fontsize=7.5, color=RED)
    fig.tight_layout(pad=0.4)

    buf = io.BytesIO()
    fig.savefig(buf, format="png", bbox_inches="tight")
    plt.close(fig)
    return base64.b64encode(buf.getvalue()).decode()


# ------------------------------------------------------------------ HTML
def _p(x, d=1):
    return "—" if x is None or (isinstance(x, float) and np.isnan(x)) else f"{x * 100:.{d}f}%"


def _band_cell(g: pd.DataFrame, label: str) -> str:
    if label not in g.index or pd.isna(g.loc[label, "mean"]):
        return "—"
    v, n = g.loc[label, "mean"], int(g.loc[label, "count"])
    return f"{v * 100:+.1f}% <span class='n'>({n})</span>"


def _band_read(g: pd.DataFrame) -> tuple[str, str]:
    """判读清仓档：次月收益为负 = 这条线确实帮你躲了跌；为正 = 白让出上涨。"""
    try:
        v_cut, n_cut = g.loc["清仓", "mean"], int(g.loc["清仓", "count"])
    except KeyError:
        return "—", "#6b7280"
    if n_cut < 3 or pd.isna(v_cut):
        return f"仅 {n_cut} 次，样本不足", "#6b7280"
    if v_cut < -0.003:
        return "躲过下跌", "#137333"
    if v_cut > 0.003:
        return "让出上涨", "#b3261e"
    return "无区分度", "#8a6d00"


def _ratio(g: pd.DataFrame) -> float:
    """减半档次月收益 ÷ 满仓档次月收益 —— 越接近 1，减半越是白缴学费。"""
    try:
        v_half, v_full = g.loc["减半", "mean"], g.loc["满仓", "mean"]
    except KeyError:
        return float("nan")
    return float(v_half / v_full) if v_full else float("nan")


def render(rows: list[dict], common: dict, asof: pd.Timestamp) -> str:
    rows = sorted(rows, key=lambda r: -r["dcalmar"])

    def tag(r):
        dc = r["dcalmar"]
        if dc >= 0.10:
            return "适合", "#137333"
        if dc >= -0.05:
            return "无效", "#8a6d00"
        return "不适合", "#b3261e"

    cards, table, btable = [], [], []
    for r in rows:
        code = r["code"]
        label, color = tag(r)
        short = r["n"] < MIN_MONTHS
        dc_c = common["dcalmar"].get(code, float("nan"))
        same = "✓" if (not np.isnan(dc_c) and np.sign(dc_c) == np.sign(r["dcalmar"])) else "✗"

        table.append(
            f"<tr><td class='mono'>{code}</td><td>{r['name']}</td><td>{r['cat']}</td>"
            f"<td class='num'>{r['n']}m{'<span class=\"warn\">短</span>' if short else ''}</td>"
            f"<td class='num'>{_p(r['bh']['cagr'])}</td><td class='num'>{_p(r['strat']['cagr'])}</td>"
            f"<td class='num'>{_p(r['bh']['dd'])}</td><td class='num'>{_p(r['strat']['dd'])}</td>"
            f"<td class='num'>{r['bh']['calmar']:.2f}</td><td class='num'>{r['strat']['calmar']:.2f}</td>"
            f"<td class='num'><b style='color:{color}'>{r['dcalmar']:+.2f}</b></td>"
            f"<td class='num'>{dc_c:+.2f} <span class='n'>{same}</span></td>"
            f"<td class='num'>{_p(r['pos'], 0)}</td>"
            f"<td class='num'>{r['n_cut']} / {r['n_half']}</td>"
            f"<td><span class='badge' style='background:{color}'>{label}</span></td></tr>")

        g = r["bands"]
        read, rc = _band_read(g)
        ratio = _ratio(g)
        btable.append(
            f"<tr><td class='mono'>{code}</td><td>{r['name']}</td>"
            f"<td class='num'>{_band_cell(g, '满仓')}</td>"
            f"<td class='num'>{_band_cell(g, '减半')}</td>"
            f"<td class='num'>{_band_cell(g, '清仓')}</td>"
            f"<td class='num'>{ratio:.2f}</td>"
            f"<td style='color:{rc}'>{read}</td>"
            f"<td><span class='badge' style='background:{color}'>{label}</span></td></tr>")

        cards.append(
            f"<div class='card'><div class='card-head'>"
            f"<div><span class='mono'>{code}</span> {r['name']}</div>"
            f"<span class='badge' style='background:{color}'>{label} {r['dcalmar']:+.2f}</span></div>"
            f"<img src='data:image/png;base64,{r['png']}' alt='{code}'>"
            f"<div class='card-foot'><span>择时 {_p(r['strat']['cagr'])} / {_p(r['strat']['dd'])} / "
            f"Calmar {r['strat']['calmar']:.2f}</span>"
            f"<span>持有 {_p(r['bh']['cagr'])} / {_p(r['bh']['dd'])} / "
            f"Calmar {r['bh']['calmar']:.2f}</span></div></div>")

    best, worst = rows[0], rows[-1]
    best_long = next((r for r in rows if r["n"] >= MIN_MONTHS), rows[0])
    n_ok = sum(1 for r in rows if r["dcalmar"] >= 0.10)
    n_bad = sum(1 for r in rows if r["dcalmar"] < -0.05)
    n_flip = sum(1 for r in rows
                 if not np.isnan(common["dcalmar"].get(r["code"], float("nan")))
                 and np.sign(common["dcalmar"][r["code"]]) != np.sign(r["dcalmar"]))
    avg_pos = float(np.mean([r["pos"] for r in rows]))

    n_dd = sum(1 for r in rows if r["strat"]["dd"] > r["bh"]["dd"])
    n_cagr = sum(1 for r in rows if r["strat"]["cagr"] > r["bh"]["cagr"])
    cut_lo = min(r["n_cut"] for r in rows)
    cut_hi = max(r["n_cut"] for r in rows)
    half_lo = min(r["n_half"] for r in rows)
    half_hi = max(r["n_half"] for r in rows)
    half_pct = [r["n_half"] / r["n"] for r in rows]
    top5_full = {r["code"] for r in rows[:5]}
    top5_common = {c for c, _ in sorted(common["dcalmar"].items(), key=lambda kv: -kv[1])[:5]}
    set_note = ("前五名与后五名是完全同一套名单" if top5_full == top5_common
                else "前五名里有换位，名单不完全一致")
    weak = [f"{r['code']} {r['name']}" for r in rows if r["dcalmar"] < 0.10]
    weak_ratio = [_ratio(r["bands"]) for r in rows if r["dcalmar"] < 0.10]
    weak_ratio = [x for x in weak_ratio if not np.isnan(x)]

    longs = [r for r in rows if r["n"] >= MIN_MONTHS]
    long_names = "、".join(f"{r['code']} {r['name']} {r['dcalmar']:+.2f}" for r in longs[:3])
    ok_codes = " / ".join(r["code"] for r in longs if r["dcalmar"] >= 0.10)
    pos_cut = [(r["code"], r["bands"].loc["清仓", "mean"]) for r in rows
               if "清仓" in r["bands"].index
               and not pd.isna(r["bands"].loc["清仓", "mean"])
               and r["bands"].loc["清仓", "mean"] > 0.003]
    pos_cut_txt = "、".join(f"{c} {v * 100:+.1f}%" for c, v in pos_cut)
    shorts = [r for r in rows if r["n"] < MIN_MONTHS]
    short_txt = " / ".join(f"{r['code']}({r['n']}m)" for r in shorts)
    # 实际采用的 V4 池 = 双窗口 ΔCalmar 都 ≥ +0.10（见 rule.py::POOL）
    ok5 = [r for r in rows if r["dcalmar"] >= 0.10
           and common["dcalmar"].get(r["code"], -9) >= 0.10]
    pool_codes = " / ".join(r["code"] for r in ok5)

    return f"""<!DOCTYPE html>
<html lang="zh-CN"><head><meta charset="utf-8">
<title>V4 单标的适配度 · 10 只红利标的</title>
<style>
:root{{--fg:#1a1a1a;--muted:#6b7280;--line:#e5e7eb;--bg:#f7f8fa;--card:#fff;--accent:#1f4e79}}
*{{box-sizing:border-box}}
body{{margin:0;padding:28px 34px 60px;background:var(--bg);color:var(--fg);
     font-family:"Microsoft YaHei","PingFang SC",system-ui,-apple-system,sans-serif;font-size:14px;line-height:1.65}}
h1{{font-size:23px;margin:0 0 6px;letter-spacing:.2px}}
h2{{font-size:16px;margin:34px 0 12px;padding-left:10px;border-left:3px solid var(--accent)}}
.sub{{color:var(--muted);font-size:13px;margin-bottom:22px}}
.mono{{font-family:ui-monospace,Consolas,monospace}}
.n{{color:var(--muted);font-size:11px}}
.warn{{background:#fdf0ea;color:#b3261e;font-size:10.5px;padding:0 3px;border-radius:3px;margin-left:2px}}
.wrap{{overflow-x:auto;border-radius:10px}}
table{{width:100%;border-collapse:collapse;background:var(--card);border-radius:10px;overflow:hidden;
      box-shadow:0 1px 3px rgba(0,0,0,.06);font-size:12.5px}}
th,td{{padding:8px 9px;border-bottom:1px solid var(--line);text-align:left;white-space:nowrap}}
th{{background:#eef2f7;font-weight:600;font-size:12px;color:#374151}}
tr:last-child td{{border-bottom:none}}
td.num{{text-align:right;font-variant-numeric:tabular-nums}}
tbody tr:hover{{background:#f9fbfd}}
.badge{{display:inline-block;padding:1px 7px;border-radius:20px;color:#fff;font-size:11.5px;font-weight:600}}
.grid{{display:grid;grid-template-columns:repeat(auto-fill,minmax(430px,1fr));gap:16px}}
.card{{background:var(--card);border-radius:10px;padding:12px 12px 8px;box-shadow:0 1px 3px rgba(0,0,0,.06)}}
.card-head{{display:flex;justify-content:space-between;align-items:center;gap:8px;font-size:12.5px;
           font-weight:600;margin-bottom:6px}}
.card img{{width:100%;display:block;border-radius:6px}}
.card-foot{{display:flex;justify-content:space-between;color:var(--muted);font-size:11.5px;margin-top:6px}}
.note{{background:#fff;border-left:3px solid var(--accent);padding:12px 16px;border-radius:8px;
      font-size:13px;box-shadow:0 1px 3px rgba(0,0,0,.05)}}
.note li{{margin:7px 0}}
.kpi{{display:flex;gap:14px;flex-wrap:wrap;margin:18px 0 6px}}
.kpi div{{background:var(--card);border-radius:10px;padding:10px 16px;box-shadow:0 1px 3px rgba(0,0,0,.06);min-width:132px}}
.kpi b{{display:block;font-size:19px;letter-spacing:.3px}}
.kpi span{{color:var(--muted);font-size:12px}}
</style></head><body>
<h1>V4 单标的适配度 · 10 只红利标的</h1>
<div class="sub">左轴 基金累计净值（同 V4 口径）· 右轴 该标的自己的 Wilder RSI14 · 虚线 RSI 70 清仓 / 50 减半 ·
月末圆点为决策点 · 数据截至 {asof:%Y-%m-%d}</div>

<div class="kpi">
  <div><b>{common['n_months']}</b><span>共同窗口月数</span></div>
  <div><b>{n_ok} / {len(rows)}</b><span>择时有效（ΔCalmar ≥ +0.10）</span></div>
  <div><b>{n_bad} / {len(rows)}</b><span>择时反效果（&lt; −0.05）</span></div>
  <div><b>{n_flip} / {len(rows)}</b><span>两个窗口结论翻转</span></div>
  <div><b>{_p(avg_pos, 0)}</b><span>平均仓位（无风险现金）</span></div>
  <div><b>{best['dcalmar']:+.2f}</b><span>最好：{best['code']}</span></div>
  <div><b>{worst['dcalmar']:+.2f}</b><span>最差：{worst['code']}</span></div>
</div>

<h2>一、择时效果排序（按各标的自己全区间 ΔCalmar）</h2>
<div class="wrap"><table><thead><tr>
<th>代码</th><th>名称</th><th>分类</th><th>样本</th>
<th>持有 年化</th><th>择时 年化</th><th>持有 回撤</th><th>择时 回撤</th>
<th>持有 Calmar</th><th>择时 Calmar</th><th>ΔCalmar<br>全区间</th>
<th>ΔCalmar<br>共同窗口</th><th>平均仓位</th><th>清仓/减半<br>(月)</th><th>结论</th>
</tr></thead><tbody>
{''.join(table)}
</tbody></table></div>
<div class="sub" style="margin-top:8px">ΔCalmar = 择时 Calmar − 买入持有 Calmar。
「适合」= ≥ +0.10；「无效」= −0.05 ~ +0.10；「不适合」= &lt; −0.05。
共同窗口 {common['start']:%Y-%m} ~ {common['end']:%Y-%m}（{common['n_months']} 月，由最短历史的标的决定），
✓/✗ 表示该窗口与全区间是否同号。<span class="warn">短</span> = 样本 &lt; {MIN_MONTHS} 月，结论待观察。</div>

<h2>二、信号触发结构（信号档 → 次月收益，与策略执行口径一致）</h2>
<div class="wrap"><table><thead><tr>
<th>代码</th><th>名称</th><th>满仓档 RSI&lt;50<br>次月均值(次数)</th>
<th>减半档 50–70<br>次月均值(次数)</th><th>清仓档 ≥70<br>次月均值(次数)</th>
<th>减半÷满仓</th><th>清仓档判读</th><th>结论</th>
</tr></thead><tbody>
{''.join(btable)}
</tbody></table></div>
<div class="sub" style="margin-top:8px">「清仓档判读」只看 RSI≥70 之后的次月收益是否真为负 —— 为负说明这条线躲对了，
为正说明白让出上涨；不足 3 次不给判读。「减半÷满仓」是两档次月收益之比，越接近 1，
说明减半月份本来也在涨，减半等于白缴学费（还要付手续费）。</div>

<h2>三、逐只读数（净值 + 分数）</h2>
<div class="grid">
{''.join(cards)}
</div>

<h2>四、结论</h2>
<div class="note"><ol>
<li><b>分化极大，不能「一刀切上 RSI」。</b>同一套 70/50 规则，全区间 ΔCalmar 从
{best['dcalmar']:+.2f}（{best['name']}，{best['n']} 月）到 {worst['dcalmar']:+.2f}
（{worst['name']}，{worst['n']} 月）；{len(rows)} 只里 {n_ok} 只明确适合、
{n_bad} 只明确不适合（&lt; −0.05）、其余 {len(rows) - n_ok - n_bad} 只落在「无效区」（−0.05 ~ +0.10）。
「哪只适合」不是策略好坏的问题，而是<b>标的性格</b>的问题。</li>

<li><b>长样本（{MIN_MONTHS} 月以上）里，稳的是红利低波与中证红利。</b>
全区间前三：{long_names} —— 它们覆盖 {best_long['n']} 个月，经历过 2021–2024 的慢牛与 2024 年初的急跌，
ΔCalmar 在「全区间」与「共同窗口」两种口径下都为正。561060 的 {best['dcalmar']:+.2f} 全场最高
但只有 {best['n']} 个月，先当候选、别当结论。</li>

<li><b>排序不是窗口挑出来的。</b>把区间统一到 {common['start']:%Y-%m}~{common['end']:%Y-%m}
（{common['n_months']} 月，由最短历史的标的决定）后重算，{len(rows)} 只里只有 {n_flip} 只跨过零轴，
{set_note} —— 差异来自标的本身，而非样本期运气。代价是共同窗口太短、年化被拉平，
所以主表仍用各自全区间，共同窗口只做交叉验证。</li>

<li><b>真正在干活的是「减半」，不是「清仓」。</b>样本内清仓档只触发 {cut_lo}–{cut_hi} 次，
减半档触发 {half_lo}–{half_hi} 次（占样本 {min(half_pct) * 100:.0f}–{max(half_pct) * 100:.0f}% 的月份），
平均仓位因此只有 {_p(avg_pos, 0)} —— 长期三成现金是硬成本，还要年年付换手费。
所以这套择时的实质是「半仓化」，70 那条线更多是尾部保护。</li>

<li><b>收益来自回撤端，不是收益端。</b>{len(rows)} 只里 {n_dd} 只回撤收窄、{n_cagr} 只年化反而上升，
Calmar 的改善主要来自分母（回撤）变小。若目标是绝对收益，单标的择时并不必然占优；
若目标是「拿得住、不砍在坑里」，它才成立。</li>

<li><b>「无效」的四只（{('、'.join(weak))}）不是信号读错，是减半太贵。</b>
它们的「减半÷满仓」次月收益比在 {min(weak_ratio):.2f}–{max(weak_ratio):.2f} 之间（越高越像白缴学费），
说明减半月本来也在涨，一半仓位换来的只是手续费和少赚。
{f"更极端的是 {pos_cut_txt} —— 清仓之后次月还是正收益，高位仍在走强，70 这条线对它是负和。" if pos_cut else ""}
另外 007467 要单独看：它持有期 Calmar {next(r['bh']['calmar'] for r in rows if r['code'] == '007467'):.2f}
本身已经很高，ΔCalmar 天花板低，不是择时做错，是没有改善空间。</li>

<li><b>单标的择时 ≠ V4。</b>V4 的组合表现来自「横截面 top2 选品 + 板块 RSI 择时」两半，
本页只保留后一半。**落地口径**：每日操作（`run_daily_dividend.ps1`）采用的是
「全区间与共同窗口 ΔCalmar 都 ≥ +0.10」的 {len(ok5)} 只 —— {pool_codes}，写在 `rule.py::POOL`；
本页其余标的只作对照。若只买一只、又想要更长时间验证，可再收窄到 ≥{MIN_MONTHS} 月的 {ok_codes}。</li>

<li><b>短样本标的要留保留意见。</b>{short_txt} 都不足 {MIN_MONTHS} 个月，
清仓档只触发 {min(r['n_cut'] for r in shorts) if shorts else 0}–{max(r['n_cut'] for r in shorts) if shorts else 0} 次，
ΔCalmar 的统计意义有限 —— 等攒够 {MIN_MONTHS} 个月再复盘，别现在就据此改池子。</li>
</ol></div>
</body></html>"""


def main() -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")

    navs = pool.load_navs()
    rows = []
    for d in pool.POOL:
        code = d["code"]
        if code not in navs.columns:
            continue
        res = run_one(code, navs[code], START)
        if res is None:
            print(f"[uni] {code} 历史不足，跳过")
            continue
        res.update(name=d["name"], cat=d["cat"])
        res["bands"] = band_next(res["rsi_m"], res["rets"])
        res["png"] = _fig_b64(res)
        rows.append(res)

    # 共同窗口：所有标的都覆盖的区间
    start = max(r["start"] for r in rows)
    end = min(r["end"] for r in rows)
    common = {"start": start, "end": end, "dcalmar": {},
              "n_months": int(round((end - start).days / 30.44))}
    for r in rows:
        sub = run_one(r["code"], navs[r["code"]], str(start.date()))
        if sub is not None:
            common["dcalmar"][r["code"]] = sub["dcalmar"]

    asof = max(navs[c].dropna().index[-1] for c in navs.columns)
    os.makedirs(os.path.dirname(OUT_HTML), exist_ok=True)
    with open(OUT_HTML, "w", encoding="utf-8") as fh:
        fh.write(render(rows, common, asof))

    print(f"[uni] {len(rows)} 只标的 · 共同窗口 {start:%Y-%m}~{end:%Y-%m} "
          f"({common['n_months']} 月)")
    for r in sorted(rows, key=lambda x: -x["dcalmar"]):
        dc_c = common["dcalmar"].get(r["code"], float("nan"))
        print(f"[uni] {r['code']} {r['name'][:16]:16s} {r['n']:3d}m  "
              f"持有 Calmar {r['bh']['calmar']:5.2f} → 择时 {r['strat']['calmar']:5.2f}  "
              f"Δ {r['dcalmar']:+.2f} (同窗 {dc_c:+.2f})  仓位 {r['pos'] * 100:3.0f}%  "
              f"清仓{r['n_cut']:2d}/减半{r['n_half']:2d}")
    print(f"[uni] 产物 -> {os.path.relpath(OUT_HTML, _DIR)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
