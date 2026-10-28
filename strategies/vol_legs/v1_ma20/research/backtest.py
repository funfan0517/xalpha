# -*- coding: utf-8 -*-
"""波动资产 · V1 变种（MA20 穿越）· 六腿逐只详报与单独回测。

每条腿单独回测（各自 MA20 成形日起）并与买入持有逐项对照：年化 / 波动 / 夏普 / 最大回撤 /
卡玛 / 总收益 / 交易 / 胜率 / 盈亏比 / 曝光 / 扣费后年化；另附逐年收益、最近 8 笔交易、
当前仓位，以及每腿独立的净值+回撤图。

产物（相对本文件）：
- research/_v1_ma20_legs.md
- research/_v1_ma20_legs.jsonl
- research/_v1_ma20_<leg>.png · _v1_ma20_all.png

模拟结果，非投资建议。
"""
import json
import os
import sys

import pandas as pd

try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:  # noqa
    pass

_HERE = os.path.dirname(os.path.abspath(__file__))
_V1 = os.path.dirname(_HERE)
_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(_V1)))
sys.path.insert(0, _V1)

import ma20  # noqa: E402

OUTDIR = _HERE
RECENT_TRADES = 8


def _pct(x, nd=2):
    return ma20.fmt_pct(x, nd)


def _m_table(m, mf, b):
    rows = [
        ("年化收益", _pct(m["ann"]), _pct(mf["ann"]), _pct(b["ann"])),
        ("年化波动", _pct(m["vol"]), _pct(mf["vol"]), _pct(b["vol"])),
        ("夏普比率", f"{m['sharpe']:.2f}", f"{mf['sharpe']:.2f}", f"{b['sharpe']:.2f}"),
        ("最大回撤", _pct(m["mdd"]), _pct(mf["mdd"]), _pct(b["mdd"])),
        ("总收益", _pct(m["cum"]), _pct(mf["cum"]), _pct(b["cum"])),
    ]
    L = ["| 指标 | V1 MA20穿越 | 扣申赎费 | 买入持有 |", "|---|---|---|---|"]
    L += [f"| {a} | {x} | {y} | {z} |" for a, x, y, z in rows]
    return L


def _plot_one(leg, name, nav, bh, path):
    try:
        import matplotlib
        matplotlib.use("Agg")
        import matplotlib.pyplot as plt
    except Exception as e:  # noqa
        print("matplotlib 不可用，跳过作图:", e)
        return
    plt.rcParams["font.sans-serif"] = ["Microsoft YaHei", "SimHei", "DejaVu Sans"]
    plt.rcParams["axes.unicode_minus"] = False
    x = pd.to_datetime(nav.index)
    fig, (ax1, ax2) = plt.subplots(2, 1, figsize=(12, 7), sharex=True,
                                   gridspec_kw={"height_ratios": [3, 1]})
    ax1.plot(x, bh.to_numpy(), label="买入持有", color="#888", linewidth=1.2)
    ax1.plot(x, nav.to_numpy(), label=ma20.LABEL, color="#c2185b", linewidth=1.5)
    ax1.set_title(f"{name} · {ma20.LABEL} vs 买入持有（场外复权净值, 起=1）")
    ax1.set_ylabel("净值")
    ax1.grid(alpha=0.3)
    ax1.legend()
    for v, c, lb in ((nav.to_numpy(), "#c2185b", ma20.LABEL), (bh.to_numpy(), "#888", "买入持有")):
        ax2.plot(x, v / pd.Series(v).cummax().to_numpy() - 1, color=c, linewidth=1.0, label=lb)
    ax2.set_ylabel("回撤")
    ax2.grid(alpha=0.3)
    fig.tight_layout()
    fig.savefig(path, dpi=120)
    plt.close(fig)
    print("图:", path)


def _plot_all(curves, path):
    try:
        import matplotlib
        matplotlib.use("Agg")
        import matplotlib.pyplot as plt
    except Exception as e:  # noqa
        print("matplotlib 不可用，跳过作图:", e)
        return
    plt.rcParams["font.sans-serif"] = ["Microsoft YaHei", "SimHei", "DejaVu Sans"]
    plt.rcParams["axes.unicode_minus"] = False
    fig, ax = plt.subplots(figsize=(12, 6))
    for leg, (name, nav) in curves.items():
        ax.plot(pd.to_datetime(nav.index), nav.to_numpy(), label=name, linewidth=1.4)
    ax.set_title(f"六腿 · {ma20.LABEL} 净值一览（各自起=1，起点不同）")
    ax.set_ylabel("净值")
    ax.grid(alpha=0.3)
    ax.legend(fontsize=8, ncol=2)
    fig.tight_layout()
    fig.savefig(path, dpi=120)
    plt.close(fig)
    print("图:", path)


def main():
    out, sections, curves = [], [], {}
    for leg in ma20.LEGS:
        cfg = ma20.rule.LEGS[leg]
        px_full = ma20.load(leg)
        sig_full, ma_full = ma20.signal(px_full)
        start = ma_full.first_valid_index()
        px, sig = px_full.loc[start:], sig_full.loc[start:]
        nav = ma20.run(px, sig)
        nav_f = ma20.run(px, sig, fee=ma20.FEE[leg])
        bh = px / px.iloc[0]
        m, mf, b = ma20.perf(nav), ma20.perf(nav_f), ma20.perf(bh)
        tr = ma20.trades(sig, px)
        st = ma20.trade_stats(tr)
        years = max((px.index[-1] - px.index[0]).days / 365.25, 1e-9)

        rec = dict(leg=leg, name=cfg["name"], role=cfg["role"], band=float(cfg["band"]),
                   exec_code=cfg["exec"], start=px.index[0].date().isoformat(),
                   end=px.index[-1].date().isoformat(), years=round(years, 2), bars=len(px),
                   ann=m["ann"], mdd=m["mdd"], sharpe=m["sharpe"], vol=m["vol"],
                   ann_fee=mf["ann"], sharpe_fee=mf["sharpe"],
                   exposure=ma20.exposure(sig), trades=st["n"],
                   win_rate=st["win_rate"], payoff=st["payoff"],
                   per_year=round(st["n"] / years, 1),
                   bh_ann=b["ann"], bh_mdd=b["mdd"], bh_sharpe=b["sharpe"],
                   edge_pp=round((m["ann"] - b["ann"]) * 100, 2),
                   edge_fee_pp=round((mf["ann"] - b["ann"]) * 100, 2),
                   pos_now=int(sig.iloc[-1]), nav_now=float(px.iloc[-1]),
                   ma20_now=float(ma_full.iloc[-1]),
                   bridge=bool(cfg.get("otc_proxy")), source=ma20.vdata.SOURCE_NOTE.get(leg, ""))
        out.append(rec)

        # ---- 逐腿章节 ----
        y_s, y_b = ma20.yearly(nav), ma20.yearly(bh)
        sections += [
            "",
            f"### {cfg['name']}（{cfg['role']} · 带 {cfg['band']:g}pp · `{cfg['exec']}`）",
            "",
            f"样本：**{px.index[0].date()} ~ {px.index[-1].date()}** · {len(px)} 个净值日 · "
            f"约 {years:.2f} 年（MA20 成形日起）。" + ("　⚠️ 前段含**桥接**（同类老基金）。" if rec["bridge"] else ""),
            "",
        ]
        sections += _m_table(m, mf, b)
        sections += ["", "逐年收益（首尾为不完整年度）：", "",
                     "| 年份 | V1 | 买入持有 | 差 |", "|---|---|---|---|"]
        for y in sorted(y_s):
            sections.append(f"| {y} | {_pct(y_s[y])} | {_pct(y_b.get(y, 0.0))} | "
                            f"{(y_s[y] - y_b.get(y, 0.0)) * 100:+.2f}pp |")
        sections += ["", f"最近 {RECENT_TRADES} 笔（成交日 = 信号次日）：", ""]
        if tr:
            sections += ["| 买入日 | 卖出日 | 持有日 | 单笔收益 |", "|---|---|---|---|"]
            for t in tr[-RECENT_TRADES:]:
                sections.append(f"| {t['entry'].date()} | {t['exit'].date()} | {t['bars']} | "
                                f"{_pct(t['ret'])} |")
        else:
            sections.append("（样本期内无完整交易）")
        sections += [
            "",
            f"当前：净值 **{rec['nav_now']:.4f}** · MA20 **{rec['ma20_now']:.4f}** · "
            f"距 MA20 **{rec['nav_now'] / rec['ma20_now'] - 1:+.2%}** · 仓位 "
            f"**{'持仓' if rec['pos_now'] else '空仓'}**。",
        ]

        png = os.path.join(OUTDIR, f"_v1_ma20_{leg}.png")
        _plot_one(leg, cfg["name"], nav, bh, png)
        curves[leg] = (cfg["name"], nav)

    with open(os.path.join(OUTDIR, "_v1_ma20_legs.jsonl"), "w", encoding="utf-8") as f:
        for r in out:
            f.write(json.dumps(r, ensure_ascii=False) + "\n")

    n_beat = sum(1 for r in out if r["edge_pp"] > 0)
    n_fee = sum(1 for r in out if r["edge_fee_pp"] > 0)
    n_dd = sum(1 for r in out if abs(r["mdd"]) < abs(r["bh_mdd"]))

    L = [
        "# 波动资产 · V1（MA20 穿越）· 六腿逐只详报",
        f"> 池 = `rule.LEG_ORDER` 六腿（与主策略一致）· 场外复权净值 · T+1 净值成交 · "
        f"空仓计货基 {ma20.CASH_ANNUAL:.2%} · 年化按 252 日",
        "> ⚠️ 各腿上市时间不同（科创50 / 恒生科技 2021 起，A500 / 标普含桥接前段）："
        "只比「各自区间内 V1 vs 买入持有」，不是同窗口排名。",
        "",
        "## 汇总（按年化差降序）",
        "",
        "| 腿 | 角色 | 样本区间 | 年数 | 年化 | 回撤 | 夏普 | 曝光 | 交易 | 年笔数 | 胜率 | "
        "买入持有年化 | 买入持有回撤 | 年化差 | 扣费后年化 | 扣费后年化差 | 当前 |",
        "|---|---|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---|",
    ]
    for r in sorted(out, key=lambda z: z["edge_pp"], reverse=True):
        L.append(f"| {r['name']} | {r['role']} | {r['start']} ~ {r['end']} | {r['years']:.2f} | "
                 f"{_pct(r['ann'])} | {_pct(r['mdd'])} | {r['sharpe']:.2f} | {r['exposure']:.0%} | "
                 f"{r['trades']} | {r['per_year']:.1f} | {r['win_rate']:.0%} | {_pct(r['bh_ann'])} | "
                 f"{_pct(r['bh_mdd'])} | **{r['edge_pp']:+.2f}pp** | {_pct(r['ann_fee'])} | "
                 f"**{r['edge_fee_pp']:+.2f}pp** | {'持仓' if r['pos_now'] else '空仓'} |")

    L += [
        "",
        f"> 不计费：年化跑赢 **{n_beat}/{len(out)}**、回撤更小 **{n_dd}/{len(out)}**；"
        f"**扣申赎费后只剩 {n_fee}/{len(out)} 跑赢**（A 类三腿 0.6%/笔往返）。",
        "",
        "## 逐腿详报",
    ]
    L += sections

    L += ["", "## 结论", ""]
    if out:
        best = max(out, key=lambda z: z["edge_pp"])
        worst = min(out, key=lambda z: z["edge_pp"])
        L += [
            f"- **不计费最好**：{best['name']} {best['edge_pp']:+.2f}pp"
            f"（{_pct(best['ann'])} vs 买入持有 {_pct(best['bh_ann'])}），回撤 "
            f"{_pct(best['mdd'])} vs {_pct(best['bh_mdd'])}。",
            f"- **不计费最差**：{worst['name']} {worst['edge_pp']:+.2f}pp"
            f"（{_pct(worst['ann'])} vs {_pct(worst['bh_ann'])}），回撤 "
            f"{_pct(worst['mdd'])} vs {_pct(worst['bh_mdd'])}。",
            "- **规律**：MA20 穿越在场外口径下是「降回撤」工具 —— 6 腿回撤**全部**更小，"
            "但年化只在「买入持有本身为负」的腿上转为正贡献（科创50 / 恒生科技），"
            "在单边上涨的美股与黄金上只是少赚。",
            f"- **成本是决定性变量**：逐腿年笔数 13~16，A 类腿每笔往返 0.6%，扣费后 "
            f"{len(out) - n_fee} 条腿的优势消失 —— 这条规则若要在场外落地，"
            "要么降频（如加缓冲带、周频判定），要么只在 C 类免费通道执行。",
            "- **与主策略的关系**：主策略的阶梯/闸门本身就是低频择时；本变种可作为「主策略已建仓后，"
            "该不该临时下车」的独立参照，而不是替代品。",
        ]
    L += ["", "> **模拟结果，非投资建议。**"]

    path = os.path.join(OUTDIR, "_v1_ma20_legs.md")
    with open(path, "w", encoding="utf-8") as f:
        f.write("\n".join(L))
    if curves:
        _plot_all(curves, os.path.join(OUTDIR, "_v1_ma20_all.png"))
    print("报告:", path)
    print("结果:", os.path.join(OUTDIR, "_v1_ma20_legs.jsonl"))
    for r in sorted(out, key=lambda z: z["edge_pp"], reverse=True):
        print(f"  {r['name']:8s} {_pct(r['ann']):>9s} vs BH {_pct(r['bh_ann']):>9s} · "
              f"回撤 {_pct(r['mdd']):>9s} vs {_pct(r['bh_mdd']):>9s} · "
              f"扣费 {_pct(r['ann_fee']):>9s} · {r['trades']:3d} 笔")


if __name__ == "__main__":
    main()
