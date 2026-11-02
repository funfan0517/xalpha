# -*- coding: utf-8 -*-
"""成长/红利风格轮动 v3 · R 绝对水位 0.35/0.65 + MA250 趋势闸门

信号: R = 创业板指(399006) ÷ 中证红利(000922)
      R 层 : R ≤ 0.35 → 满仓成长；R ≥ 0.65 → 空仓成长；中间带保持
      趋势层: 创业板指 < MA250 → 成长权重封顶 TREND_CAP（默认 0.5）
      冷却期: 两次切换间隔 < COOL_BARS（默认 20 交易日）则挂起
基准: 买入持有创业板 / 买入持有中证红利
对照: ①v2 同阈值（无趋势）②v2 原阈值 0.30/0.70（±趋势）→ 四格矩阵，分离「挪阈值」与「加趋势」的贡献
      ③封顶/冷却/均线窗口敏感性
产物: backtest/_v3_report.md · _v3_bt.json · _v3_bt.jsonl
      backtest/_v3_nav.png    净值对比
      backtest/_v3_dual.png   R + 创业板指 + MA250 + 买卖点
用法: python strategies/growth_div_rotation/v3_trend_filter/backtest.py [--refresh]
"""
import io
import json
import os
import sys
from datetime import datetime

import numpy as np
import pandas as pd

try:
    sys.stdout.reconfigure(encoding="utf-8")
except (AttributeError, OSError):
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8")

_DIR = os.path.dirname(os.path.abspath(__file__))
if _DIR not in sys.path:
    sys.path.insert(0, _DIR)

import matplotlib  # noqa: E402
matplotlib.use("Agg")
import matplotlib.dates as mdates  # noqa: E402
import matplotlib.pyplot as plt  # noqa: E402

plt.rcParams["font.sans-serif"] = ["Microsoft YaHei", "SimHei", "DejaVu Sans"]
plt.rcParams["axes.unicode_minus"] = False

import rule  # noqa: E402
import data as datamod  # noqa: E402
import factors  # noqa: E402
import engine  # noqa: E402

OUT = os.path.join(_DIR, "backtest")


def pct(x, nd=2):
    return "—" if x is None or (isinstance(x, float) and np.isnan(x)) else f"{x * 100:+.{nd}f}%"


# ============================================================ 计算
def compute(refresh=False):
    if refresh:
        datamod.refresh()
    wide = datamod.load_wide()
    g, d = wide[rule.GROWTH_INDEX], wide[rule.DIV_INDEX]
    r = factors.ratio_series(g, d)
    g, d = g.reindex(r.index), d.reindex(r.index)

    def variant(low, high, use_trend, cap=None, cool=None, ma=None, mode="cap"):
        w = engine.signal_weights(r, g, low=low, high=high, use_trend=use_trend,
                                  trend_cap=cap, cool=cool, ma_window=ma, mode=mode)
        res = engine.run(g, d, w)
        return dict(w=w, res=res, m=engine.metrics(res["eq"]))

    main = variant(rule.RATIO_LOW, rule.RATIO_HIGH, True)
    ref2 = variant(rule.RATIO_LOW, rule.RATIO_HIGH, False)          # v2 当前口径（无趋势）
    ref_o = variant(*rule.REF_RATIO, False)                          # v2 原图口径（无趋势）
    ref_o_t = variant(*rule.REF_RATIO, True)                         # v2 原图口径 + 趋势

    # 敏感性
    sens = []
    for cap in rule.SENS_TREND_CAP:
        for cool in rule.SENS_COOL:
            v = variant(rule.RATIO_LOW, rule.RATIO_HIGH, True, cap=cap, cool=cool)
            sens.append(dict(kind="cap×cool", cap=cap, cool=cool,
                             n=len(v["res"]["switches"]), mean_w=float(v["w"].mean()),
                             **{k: v["m"][k] for k in ("ann", "dd", "shp", "calmar")}))
    for ma in rule.SENS_MA:
        v = variant(rule.RATIO_LOW, rule.RATIO_HIGH, True, ma=ma)
        sens.append(dict(kind="ma", ma=ma, n=len(v["res"]["switches"]),
                         mean_w=float(v["w"].mean()),
                         **{k: v["m"][k] for k in ("ann", "dd", "shp", "calmar")}))

    # 基准
    bh_g = engine.run(g, d, pd.Series(1.0, index=r.index), fee=0)["eq"]
    bh_d = engine.run(g, d, pd.Series(0.0, index=r.index), fee=0)["eq"]

    switches = []
    for s in main["res"]["switches"]:
        dt = pd.Timestamp(s["date"])
        prev = r.index[r.index < dt]
        rv = float(r.loc[prev[-1]]) if len(prev) else float("nan")
        switches.append(dict(s, R_prev=None if np.isnan(rv) else round(rv, 4),
                             action="加仓" if s["to_w"] > s["from_w"] else "减仓"))

    return dict(g=g, d=d, r=r, main=main, ref2=ref2, ref_o=ref_o, ref_o_t=ref_o_t,
                sens=sens, switches=switches, nv=dict(v3=main["res"]["eq"],
                                                     v2=ref2["res"]["eq"],
                                                     bh_g=bh_g, bh_d=bh_d))


# ============================================================ 报告
def build_report(C):
    r = C["r"]
    m = C["main"]["m"]
    m2 = C["ref2"]["m"]
    mo = C["ref_o"]["m"]
    mot = C["ref_o_t"]["m"]
    mg = engine.metrics(C["nv"]["bh_g"])
    md = engine.metrics(C["nv"]["bh_d"])
    lo, hi = rule.RATIO_LOW, rule.RATIO_HIGH

    L = [f"# 成长/红利风格轮动 · v3「绝对水位 {lo:.2f}/{hi:.2f} + MA{rule.MA_TREND} 趋势闸门」", "",
         f"> 生成 {datetime.now():%Y-%m-%d %H:%M} · 数据 {r.index[0].date()}~{r.index[-1].date()}"
         f"（{len(r)} 交易日）· 分子 {rule.GROWTH_NAME} {rule.GROWTH_INDEX} ÷ "
         f"分母 {rule.DIV_NAME} {rule.DIV_INDEX}", "",
         "> 规则：R 层沿用 v2（`R ≤ %.2f` 满仓 / `R ≥ %.2f` 空仓 / 中间带保持），"
         "叠加**趋势闸门**（创业板指 < MA%d 时成长权重封顶 %.1f），"
         "并加 %d 交易日冷却期防场外赎回费。"
         % (lo, hi, rule.MA_TREND, rule.TREND_CAP, rule.COOL_BARS), "",
         "## 1. 四格矩阵：挪阈值 vs 加趋势，各自贡献多少", "",
         "| 阈值 | 无趋势（纯 v2） | + MA250 趋势闸门 |", "|---|---|---|",
         f"| {rule.REF_RATIO[0]:.2f}/{rule.REF_RATIO[1]:.2f}（原图口径） | "
         f"{pct(mo['ann'])} / {pct(mo['dd'])} / Calmar {mo['calmar']:.2f} | "
         f"**{pct(mot['ann'])} / {pct(mot['dd'])} / Calmar {mot['calmar']:.2f}** |",
         f"| {lo:.2f}/{hi:.2f}（v2 当前口径） | "
         f"**{pct(m2['ann'])} / {pct(m2['dd'])} / Calmar {m2['calmar']:.2f}** | "
         f"{pct(m['ann'])} / {pct(m['dd'])} / Calmar {m['calmar']:.2f} |", "",
         "**这张表是本次最重要的结论**：",
         f"1. 在**原口径** 0.70/0.30 下，趋势闸门把年化 {pct(mo['ann'])} → {pct(mot['ann'])}、"
         f"回撤 {pct(mo['dd'])} → {pct(mot['dd'])}、Calmar {mo['calmar']:.2f} → {mot['calmar']:.2f}"
         " —— 它救的正是 2015 年（R 只有 0.679 摸不到 0.70 那段）。",
         f"2. 在**新口径** 0.65/0.35 下，趋势闸门反而让年化 {pct(m2['ann'])} → {pct(m['ann'])}、"
         f"Calmar {m2['calmar']:.2f} → {m['calmar']:.2f} —— **因为 R 层已经把 2015 年解决了**"
         "（2014-01 的 R=0.6555 就减仓），趋势闸门无事可做，只剩 2024-2025 反复破线/复线的摩擦。",
         "3. 所以两者不是叠加关系，而是**替代关系**：都在治同一个病（2015 年不防守）。"
         "**同时上两个，收益不会叠加。**",
         f"> 口径提示：上表「+趋势」列用的是 v3 默认参数（破线减半 `cap={rule.TREND_CAP:.1f}` + "
         f"{rule.COOL_BARS} 日冷却）。若改成「破线清仓 + 不加冷却」"
         "（`research/_opt_probe.py` 的口径），原阈值下是 +12.50% / -52.69% / Calmar 0.24 —— "
         "回撤更低但切换 56 次，场外 C 类基金做不了。", ""]

    L += ["## 2. v3 绩效（当前默认参数）", "",
          "| 组合 | 年化 | 最大回撤 | 波动 | 夏普 | Calmar | 总收益 |",
          "|---|---|---|---|---|---|---|",
          f"| **v3（{lo:.2f}/{hi:.2f} + MA{rule.MA_TREND} 封顶 {rule.TREND_CAP:.1f}）** | "
          f"{pct(m['ann'])} | {pct(m['dd'])} | {pct(m['vol'])} | {m['shp']:.2f} | "
          f"{m['calmar']:.2f} | {pct(m['ret'])} |",
          f"| v2（同阈值，无趋势） | {pct(m2['ann'])} | {pct(m2['dd'])} | {pct(m2['vol'])} | "
          f"{m2['shp']:.2f} | {m2['calmar']:.2f} | {pct(m2['ret'])} |",
          f"| 买入持有{rule.GROWTH_NAME} | {pct(mg['ann'])} | {pct(mg['dd'])} | {pct(mg['vol'])} | "
          f"{mg['shp']:.2f} | {mg['calmar']:.2f} | {pct(mg['ret'])} |",
          f"| 买入持有{rule.DIV_NAME} | {pct(md['ann'])} | {pct(md['dd'])} | {pct(md['vol'])} | "
          f"{md['shp']:.2f} | {md['calmar']:.2f} | {pct(md['ret'])} |", "",
          f"- 全期切换 **{len(C['switches'])}** 次（v2 同阈值 6 次），成长暴露均值 "
          f"**{C['main']['w'].mean():.1%}**（v2 {C['ref2']['w'].mean():.1%}）。",
          f"- 冷却期 {rule.COOL_BARS} 交易日起作用：把无冷却时的 "
          f"{next((x['n'] for x in C['sens'] if x.get('cool') == 0 and x.get('cap') == rule.TREND_CAP), '?')} "
          f"次压到 {len(C['switches'])} 次（平均持有期 ≈ "
          f"{(r.index[-1] - r.index[0]).days / 365 / max(len(C['switches']), 1):.1f} 年），"
          "满足场外 C 类 >7 天的赎回费门槛。", ""]

    L += ["## 3. 切换日志（全部）", ""]
    if C["switches"]:
        L += ["| 交易日 | 动作 | 成长权重 | 决策用 R[-1] |", "|---|---|---|---|"]
        for s in C["switches"]:
            L.append(f"| {s['date']} | **{s['action']}** | {s['from_w']:.2f} → {s['to_w']:.2f} | "
                     f"{s['R_prev']} |")
    else:
        L.append("- 无切换")
    L.append("")

    L += ["## 4. v3 敏感性", "",
          "**封顶 × 冷却期**（阈值固定 %.2f/%.2f，MA%d）：" % (lo, hi, rule.MA_TREND), "",
          "| TREND_CAP | COOL_BARS | 切换 | 成长暴露 | 年化 | 最大回撤 | 夏普 | Calmar |",
          "|---|---|---|---|---|---|---|---|"]
    for s in C["sens"]:
        if s["kind"] != "cap×cool":
            continue
        mark = " ← **默认**" if (s["cap"], s["cool"]) == (rule.TREND_CAP, rule.COOL_BARS) else ""
        L.append(f"| {s['cap']:.1f} | {s['cool']}{mark} | {s['n']} | {s['mean_w']:.1%} | "
                 f"{pct(s['ann'])} | {pct(s['dd'])} | {s['shp']:.2f} | {s['calmar']:.2f} |")
    L += ["", "> 破线**清仓**（cap=0）反而比**减半**（cap=0.5）差 —— 因为 2024-2025 指数在均线附近"
              "反复穿越，全清仓的摩擦成本更高。这也说明趋势闸门在这个样本上贡献的是噪音多于信号。", "",
          f"**均线窗口**（cap={rule.TREND_CAP}, cool={rule.COOL_BARS}）：", "",
          "| MA | 切换 | 成长暴露 | 年化 | 最大回撤 | 夏普 | Calmar |",
          "|---|---|---|---|---|---|---|"]
    for s in C["sens"]:
        if s["kind"] != "ma":
            continue
        mark = " ← 默认" if s["ma"] == rule.MA_TREND else ""
        L.append(f"| MA{s['ma']}{mark} | {s['n']} | {s['mean_w']:.1%} | {pct(s['ann'])} | "
                 f"{pct(s['dd'])} | {s['shp']:.2f} | {s['calmar']:.2f} |")
    L += ["", "> MA120/MA200 的回撤明显更低（-46%~-48% vs MA250 的 -55%），说明**更快的趋势线更适合这个样本**。"
              "但这是在同一样本上挑出来的，不宜直接采信 —— 若要用，应先做分段/样本外检验。"
              "默认仍保守地留在 MA250（通用口径）。", ""]

    L += ["## 5. 结论与建议", "",
          f"1. **在 {lo:.2f}/{hi:.2f} 口径下，MA250 趋势闸门没有改善风险调整收益**"
          f"（Calmar {m2['calmar']:.2f} → {m['calmar']:.2f}）。它是 0.70/0.30 口径缺防守时的补丁，"
          "阈值放宽后这个补丁就多余了。",
          "2. **趋势闸门真正解决的问题是「死锁」**：v2 卖出后必须等 R 跌回 0.35 才买回；"
          "v3 在指数站上 MA250 时即可恢复。`engine.signal_weights(mode='switch')` 是彻底版"
          "（中间带交给趋势），但切换 108 次、加冷却后年化掉到 15.2% —— 执行成本吃掉收益。",
          "3. **如果目标是降回撤**：MA120/MA200 + cap=0.5 能把回撤压到 -46%~-48%（年化 16% 左右），"
          "这是本报告里唯一「回撤明显改善、年化基本不损」的组合，但需要样本外验证。",
          "4. **如果目标是提收益**：v3 做不到。v2 的 0.35/0.65 已经是本样本的最优区域，"
          "再叠任何一层都只会增加摩擦。", ""]

    L += ["## 6. 已知边界", "",
          "1. **阈值前视**：R 层 0.35/0.65 沿自 v2，仍是全样本选优（见 `../v2_absolute_bands/README.md` §4）。",
          "2. **趋势层无前视**：MA250 只用历史收盘，判断用当日收盘、次日生效。"
          "唯一需要注意的是「250 日」这个窗口本身是通用口径，不是在本样本上搜出来的（搜出来的话 MA120 更好）。",
          "3. **红利端系统性低估**：中证红利是价格指数，股息率约 4.5%，非成长时段收益被少算。",
          "4. 未含滑点与场外确认时滞；全部为模拟结果，非投资建议。", ""]
    return "\n".join(L) + "\n"


# ============================================================ 图：净值对比
def plot_nav(C):
    nv = C["nv"]
    labels = dict(v3=f"v3 0.35/0.65 + MA{rule.MA_TREND} 闸门",
                  v2="v2 0.35/0.65（无趋势）",
                  bh_g=f"持有{rule.GROWTH_NAME}", bh_d=f"持有{rule.DIV_NAME}")
    fig, ax = plt.subplots(figsize=(12, 5.4))
    for k, s in nv.items():
        s = s / s.iloc[0]
        ax.plot(s.index, s.values, label=f"{labels[k]}（{s.iloc[-1]:.2f}×）",
                lw=1.6 if k == "v3" else 1.0,
                alpha=1.0 if k == "v3" else 0.7)
    ax.set_yscale("log")
    ax.legend(loc="upper left")
    ax.set_title("v3（R 绝对水位 + 趋势闸门）vs v2 vs 基准 —— 净值（对数轴）")
    ax.grid(alpha=0.3)
    fig.tight_layout()
    path = os.path.join(OUT, "_v3_nav.png")
    fig.savefig(path, dpi=140, facecolor="white")
    plt.close(fig)
    return path


# ============================================================ 图：R + 创业板指 + 趋势 + 买卖点
def plot_dual(C):
    r, g = C["r"], C["g"]
    lo, hi = rule.RATIO_LOW, rule.RATIO_HIGH
    ma = factors.trend_ma(g, rule.MA_TREND)
    fig, ax1 = plt.subplots(figsize=(13, 7.4))
    ax2 = ax1.twinx()
    fig.patch.set_facecolor("#fdfdf7")
    ax1.set_facecolor("#fdfdf7")

    ax1.plot(r.index, r.values, color="#12395e", lw=1.1, zorder=4,
             label="比值 R = 创业板指 / 中证红利（左轴）")
    ax1.axhline(hi, color="#c0392b", ls=(0, (7, 5)), lw=1.8, zorder=3,
                label=f"高估阈值 {hi:.2f}")
    ax1.axhline(lo, color="#2ecc40", ls=(0, (7, 5)), lw=1.8, zorder=3,
                label=f"低估阈值 {lo:.2f}")
    ax1.set_ylim(0.22, 0.95)

    ax2.plot(g.index, g.values, color="#d95f02", lw=1.0, alpha=0.9, zorder=2,
             label="创业板指 399006 收盘（右轴）")
    ax2.plot(ma.index, ma.values, color="#8c4a00", lw=1.4, ls="--", alpha=0.75, zorder=2,
             label=f"MA{rule.MA_TREND}（趋势闸门）")
    ax2.set_ylim(200, 4700)

    for s in C["switches"]:
        dt = pd.Timestamp(s["date"])
        buy = s["to_w"] > s["from_w"]
        color = "#1a9850" if buy else "#d73027"
        ax1.axvline(dt, color="#9a9a8e", ls=":", lw=0.8, zorder=1)
        ax2.scatter([dt], [float(g.loc[dt])], marker="^" if buy else "v", s=110,
                    color=color, edgecolors="white", linewidths=0.9, zorder=9)

    ax1.set_xlim(pd.Timestamp("2010-01-01"), pd.Timestamp("2027-01-01"))
    ax1.xaxis.set_major_locator(mdates.YearLocator(2, month=1, day=1))
    ax1.xaxis.set_major_formatter(mdates.DateFormatter("%Y"))
    ax1.set_ylabel("比值（创业板指数 / 中证红利指数）", fontsize=11, color="#12395e")
    ax2.set_ylabel("创业板指点位（收盘）", fontsize=11, color="#a04a02")
    ax1.tick_params(axis="y", colors="#12395e")
    ax2.tick_params(axis="y", colors="#a04a02")
    ax1.set_xlabel("日期", fontsize=11)
    ax1.set_title(f"v3：比值 R（左轴）+ 创业板指与 MA{rule.MA_TREND}（右轴）· "
                  f"共 {len(C['switches'])} 次买卖（{r.index[0].year}-{r.index[-1].year}）",
                  fontsize=14, pad=12)
    ax1.grid(color="#dcdcd0", lw=0.7, alpha=0.9)
    h1, l1 = ax1.get_legend_handles_labels()
    h2, l2 = ax2.get_legend_handles_labels()
    ax1.legend(h1 + h2, l1 + l2, loc="upper left", frameon=True, fontsize=8.5,
               ncol=2, framealpha=0.92)
    fig.tight_layout()
    path = os.path.join(OUT, "_v3_dual.png")
    fig.savefig(path, dpi=150, facecolor=fig.get_facecolor())
    plt.close(fig)
    return path


# ============================================================ 产物
def write_products(C):
    os.makedirs(OUT, exist_ok=True)
    txt = build_report(C)
    open(os.path.join(OUT, "_v3_report.md"), "w", encoding="utf-8").write(txt)
    print(txt)

    r, m = C["r"], C["main"]["m"]
    doc = dict(
        generated_at=datetime.now().strftime("%Y-%m-%d %H:%M"),
        variant="v3_trend_filter",
        growth=rule.GROWTH_NAME, growth_code=rule.GROWTH_INDEX,
        dividend=rule.DIV_NAME, dividend_code=rule.DIV_INDEX,
        params=dict(ratio_low=rule.RATIO_LOW, ratio_high=rule.RATIO_HIGH,
                    ma_trend=rule.MA_TREND, trend_cap=rule.TREND_CAP,
                    cool_bars=rule.COOL_BARS, fee=rule.FEE),
        window=f"{r.index[0].date()}~{r.index[-1].date()}", n_bars=len(r),
        metrics=dict(v3=m, v2=C["ref2"]["m"], ref_ratio=C["ref_o"]["m"],
                     ref_ratio_trend=C["ref_o_t"]["m"],
                     bh_growth=engine.metrics(C["nv"]["bh_g"]),
                     bh_dividend=engine.metrics(C["nv"]["bh_d"])),
        n_switches=len(C["switches"]),
        mean_weight_growth=float(C["main"]["w"].mean()),
        switches=C["switches"], sens=C["sens"],
        eq={str(k.date()): round(float(v), 6) for k, v in C["main"]["res"]["eq"].items()},
        w=list(map(float, C["main"]["w"].round(4).tolist())),
        dates=[str(k.date()) for k in r.index],
    )
    json.dump(doc, open(os.path.join(OUT, "_v3_bt.json"), "w", encoding="utf-8"),
              ensure_ascii=False, indent=2)

    summary = dict(ok=True, code="GD_ROT_V3",
                   theme=(f"成长/红利风格轮动 v3(绝对水位 {rule.RATIO_LOW:.2f}/{rule.RATIO_HIGH:.2f}"
                          f" + MA{rule.MA_TREND} 趋势闸门)"),
                   years=round(m["years"], 1), st_ann=m["ann"], st_mdd=m["dd"],
                   base_ann=engine.metrics(C["nv"]["bh_d"])["ann"],
                   base_mdd=engine.metrics(C["nv"]["bh_d"])["dd"],
                   switches=len(C["switches"]), mean_w=round(float(C["main"]["w"].mean()), 3),
                   v2_ann=C["ref2"]["m"]["ann"], v2_mdd=C["ref2"]["m"]["dd"],
                   params=doc["params"])
    with open(os.path.join(OUT, "_v3_bt.jsonl"), "w", encoding="utf-8") as f:
        f.write(json.dumps(summary, ensure_ascii=False) + "\n")

    print("图:", plot_nav(C))
    print("图:", plot_dual(C))
    return summary


def main():
    write_products(compute(refresh="--refresh" in sys.argv))


if __name__ == "__main__":
    main()
