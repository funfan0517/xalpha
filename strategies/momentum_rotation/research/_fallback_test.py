# -*- coding: utf-8 -*-
"""为什么六类资产轮动跑输等权持有？—— 归因拆解 + 「空仓替代」变体对照

背景: 官方口径(空仓现金) 十年 +12.09%/-36.71%/夏普 0.68，而「六类资产等权买入持有」
基准为 +13.32%/-12.86%/夏普 1.30 —— 轮动全面跑输。本脚本回答「为什么」。

一、时间归因: 各标的占用的交易日占比、空仓占比(暴露度)。
二、空仓代价: 空仓期间等权基准的收益 —— 即「空仓错过/规避」的具体金额。
三、变体对照(引擎与 120/20/21 参数完全不变，只改「无候选时怎么办」):
    A 现版(空仓现金) · B 空仓改持债券 511260 · C 无候选则保持原仓 · D 忽略 MA20 永远满仓动量第一
   另附 基准: 六类等权买入持有。

用法: python strategies/momentum_rotation/research/_fallback_test.py
输出: research/_fallback_report.md · research/_fallback_nav.png
"""
import os
import sys
from datetime import datetime

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402
import numpy as np  # noqa: E402
import pandas as pd  # noqa: E402

sys.stdout.reconfigure(encoding="utf-8")
plt.rcParams["font.sans-serif"] = ["Microsoft YaHei", "SimHei", "DejaVu Sans"]
plt.rcParams["axes.unicode_minus"] = False

_DIR = os.path.dirname(os.path.abspath(__file__))
_STRAT = os.path.dirname(_DIR)
_ROOT = os.path.dirname(os.path.dirname(_STRAT))
for p in (_STRAT, _DIR, _ROOT):
    if p not in sys.path:
        sys.path.insert(0, p)

import backtest as bt  # noqa: E402
import rule  # noqa: E402

BOND = next((r["proxy"] for r in rule.ASSETS if r["key"] == "bond"), None)
OUT_MD = os.path.join(_DIR, "_fallback_report.md")
OUT_PNG = os.path.join(_DIR, "_fallback_nav.png")


def run_variant(df, mode="cash", bond=BOND):
    """与 bt.run_strategy 同引擎，仅替换「无候选」时的处理。mode: cash/bond/hold/nomaf"""
    dates, n = df.index, len(df)
    ma_s = df.rolling(rule.MA).mean()
    first = {c: df[c].first_valid_index() for c in df.columns}
    eq, cur, entry, epx = [1.0], [], {}, {}
    hold, trades = {}, []
    for i in range(1, n):
        r = 0.0
        if cur:
            p0, p1 = df[cur[0]].iloc[i - 1], df[cur[0]].iloc[i]
            if p0 > 0 and not (np.isnan(p0) or np.isnan(p1)):
                r = p1 / p0 - 1.0
        eq.append(eq[-1] * (1 + r))
        # 当日收益的归属方：换仓发生在收盘后，故先记录、后调仓（否则归因会 off-by-one）
        hold[dates[i]] = cur[0] if cur else ""
        if i >= rule.MIN_HIST and (i - rule.MIN_HIST) % 21 == 0:   # 旧口径月频 21 日
            cutoff = dates[i - rule.MIN_HIST]
            elig = [c for c in df.columns if first[c] <= cutoff]
            mom = {}
            for c in elig:
                p0, p1 = df[c].iloc[i - rule.LOOKBACK], df[c].iloc[i]
                if p0 > 0 and not (np.isnan(p0) or np.isnan(p1)):
                    mom[c] = p1 / p0 - 1.0
            ranked = sorted(mom, key=mom.get, reverse=True)
            new = []
            if mode == "nomaf":
                new = ranked[:1]
            else:
                for c in ranked:
                    if not np.isnan(ma_s[c].iloc[i]) and df[c].iloc[i] >= ma_s[c].iloc[i]:
                        new = [c]
                        break
            if not new:
                if mode == "bond" and bond in df.columns:
                    new = [bond]
                elif mode == "hold":
                    new = list(cur)
            if new != cur:
                if cur:
                    c = cur[0]
                    if (i - entry[c]) <= rule.FEE_SHORT_DAYS:
                        eq[-1] *= 1 - rule.FEE_SHORT
                    if epx[c] > 0:
                        trades.append(dict(code=c, entry_date=str(dates[entry[c]].date()),
                                           exit_date=str(dates[i].date()), bars=i - entry[c],
                                           ret=df[c].iloc[i] / epx[c] - 1.0))
                cur = list(new)
                entry = {c: i for c in cur}
                epx = {c: df[c].iloc[i] for c in cur}
    if cur:
        c = cur[0]
        if (n - 1 - entry[c]) <= rule.FEE_SHORT_DAYS:
            eq[-1] *= 1 - rule.FEE_SHORT
        if epx[c] > 0:
            trades.append(dict(code=c, entry_date=str(dates[entry[c]].date()),
                               exit_date=str(dates[n - 1].date()), bars=n - 1 - entry[c],
                               ret=df[c].iloc[n - 1] / epx[c] - 1.0))
    return pd.Series(eq, index=dates), trades, pd.Series(hold)


def cash_periods(hold):
    """空仓区间列表 [(起, 止)]"""
    out, st, prev = [], None, None
    for d, c in hold.items():
        if c == "" and st is None:
            st = d
        elif c != "" and st is not None:
            out.append((st, prev))
            st = None
        prev = d
    if st is not None:
        out.append((st, prev))
    return out


def main():
    df = rule.load_wide()
    s0 = df.index[rule.MIN_HIST]
    name = {r["proxy"]: r["name"] for r in rule.ASSETS}

    bm, _ = bt.run_benchmark(df, step=21)
    variants = {
        "cash": ("现版：空仓现金", "cash"),
        "bond": (f"变体B：空仓改持债券 {BOND}", "bond"),
        "hold": ("变体C：无候选则保持原仓", "hold"),
        "nomaf": ("变体D：忽略 MA20，永远满仓动量第一", "nomaf"),
    }
    res, holds = {}, {}
    for k, (label, mode) in variants.items():
        eq, tr, hd = run_variant(df, mode)
        res[k] = (eq / eq.iloc[0], tr)
        holds[k] = hd
    # 自检: cash 变体必须与官方引擎逐点一致
    eq_ref, _ = bt.run_strategy(df, rebal=21, topn=1)
    eq_ref = eq_ref / eq_ref.iloc[0]
    diff = (res["cash"][0] - eq_ref).abs().max()
    assert diff < 1e-12, f"本地引擎与官方不一致: max|Δ|={diff}"
    sall = slice(s0, None)
    bm_s = bm[sall]
    mb = bt.metrics(bm_s)

    print(f"池: {[f'{c} {name[c]}' for c in df.columns]}")
    print(f"本地引擎自检通过(与官方 backtest 逐点一致)\n")

    L = ["# 六类资产轮动为什么跑输等权持有 · 归因与空仓替代对照", "",
         f"> 生成 {datetime.now().strftime('%Y-%m-%d %H:%M')} · 引擎/参数与官方 backtest 一致，"
         f"仅改「无候选时怎么办」；数据 {df.index.min().date()} ~ {df.index.max().date()}。", ""]

    # ---- 归因口径：以 s0 为基准日（s0 当日尚无持仓、不产生收益，故序列自 s0 次日起算）----
    eq_s = res["cash"][0][sall]
    eqr = eq_s.pct_change().iloc[1:]
    hd = holds["cash"].reindex(eqr.index)
    bmr = bm_s.pct_change().reindex(eqr.index)
    eq_cash = eq_s.iloc[-1] / eq_s.iloc[0] - 1
    n_days = len(hd)

    # ---- 一、时间归因（自策略起点 s0 起算，剔除预热期）----
    L += ["## 一、时间归因：钱到底放在哪", "",
          f"> 统计自策略实际起点 {s0.date()} 起（前 {rule.MIN_HIST} 个交易日为预热期，不计入）。",
          "",
          "| 标的 | 持仓交易日 | 占比 |", "|---|---|---|"]
    for c in df.columns:
        d = int((hd == c).sum())
        L.append(f"| `{c}` {name[c]} | {d} | {d / n_days * 100:.1f}% |")
    ncash = int((hd == "").sum())
    L.append(f"| **空仓（现金，0 收益）** | **{ncash}** | **{ncash / n_days * 100:.1f}%** |")
    L.append("")

    # ---- 二、收益拆解：持仓日 / 空仓日 ----
    m_cash = hd == ""
    m_inv = ~m_cash
    cps = cash_periods(hd)
    lost = (1 + bmr[m_cash]).prod() - 1
    pos = (1 + bmr[m_cash & (bmr > 0)]).prod() - 1
    neg = (1 + bmr[m_cash & (bmr < 0)]).prod() - 1
    eq_adj = (1 + eq_cash) * (1 + lost) - 1
    s_inv = (1 + eqr[m_inv]).prod() - 1
    b_inv = (1 + bmr[m_inv]).prod() - 1
    b_cash = (1 + bmr[m_cash]).prod() - 1
    assert abs(s_inv - eq_cash) < 1e-9, "空仓日策略收益应恒为 0"          # 空仓日 r=0
    assert abs((1 + b_inv) * (1 + b_cash) - (1 + mb["ret"])) < 1e-9     # 基准两段可还原
    rel_inv = (1 + s_inv) / (1 + b_inv) - 1        # 持仓日的相对超额
    rel_gap = (1 + mb["ret"]) / (1 + eq_cash) - 1  # 全期相对落后
    _y = max((eqr.index[-1] - eqr.index[0]).days / 365.0, 1e-9)
    rel_inv_ann = (1 + rel_inv) ** (1 / _y) - 1     # 持仓日相对超额的年化
    L += ["## 二、收益拆解：持仓日 vs 空仓日", "",
          "| 时段 | 交易日 | 占比 | 策略累计 | 同期等权基准累计 | 差 |",
          "|---|---|---|---|---|---|",
          f"| 持仓日（只押 1/6 资产） | {int(m_inv.sum())} | {m_inv.sum() / n_days * 100:.1f}% "
          f"| {bt.pct(s_inv)} | {bt.pct(b_inv)} | {bt.pct(s_inv - b_inv)} |",
          f"| 空仓日（现金，0 收益） | {ncash} | {ncash / n_days * 100:.1f}% "
          f"| 0.00% | {bt.pct(lost)} | {bt.pct(-lost)} |",
          f"| **全期** | {n_days} | 100% | **{bt.pct(eq_cash)}** | **{bt.pct(mb['ret'])}** "
          f"| **{bt.pct(eq_cash - mb['ret'])}** |",
          "",
          f"> 两段的乘积即全期：策略 {bt.pct(s_inv)}×（空仓日 1.00）＝ {bt.pct(eq_cash)}；"
          f"基准 {bt.pct(b_inv)}×{bt.pct(b_cash)}＝{bt.pct(mb['ret'])}。",
          f"> 即持仓日策略**相对领先 {bt.pct(rel_inv)}**，但全期**相对落后 {bt.pct(rel_gap)}**；"
          f"正是这 {ncash} 天空仓（同期基准 {bt.pct(b_cash)}）把领先吃成了落后。",
          "",
          f"- 空仓 **{len(cps)} 段 / {ncash} 个交易日**（占全期 {ncash / n_days * 100:.1f}%），"
          f"这段时间策略收益为 0；同期等权基准 **{bt.pct(lost)}** 是「坐在现金上」的机会成本。",
          f"  - 其中基准上涨的日子合计 {bt.pct(pos)}（错过的上涨）",
          f"  - 其中基准下跌的日子合计 {bt.pct(neg)}（成功躲开的下跌）",
          f"- 归因反推：策略全期总收益 **{bt.pct(eq_cash)}**；若空仓期改为持有等权篮子，"
          f"总收益约 **{bt.pct(eq_adj)}**，即接近基准的 {bt.pct(mb['ret'])}。", ""]
    L += ["| 空仓区间（起→止） | 交易日 | 同期等权基准 |", "|---|---|---|"]
    for a, b in cps:
        seg = bm_s[(bm_s.index >= a) & (bm_s.index <= b)]
        r = seg.iloc[-1] / seg.iloc[0] - 1 if len(seg) > 1 else 0.0
        L.append(f"| {a.date()} → {b.date()} | {len(seg)} | {bt.pct(r)} |")
    L.append("")

    # ---- 三、变体对照 ----
    L += ["## 三、变体对照（只改「无候选」的处理）", "",
          "| 方案 | 年化 | 最大回撤 | 波动 | 夏普 | 总收益 | 空仓占比 |",
          "|---|---|---|---|---|---|---|"]
    for k, (label, _) in variants.items():
        e = res[k][0][sall]
        m = bt.metrics(e)
        h = holds[k][sall]
        empty = (h == "").sum() / len(h) * 100
        L.append(f"| {label} | {bt.pct(m['ann'])} | {bt.pct(m['dd'])} | {bt.pct(m['vol'])} "
                 f"| {m['shp']:.2f} | {bt.pct(m['ret'])} | {empty:.1f}% |")
    mb = bt.metrics(bm_s)
    L.append(f"| **六类等权买入持有（基准）** | **{bt.pct(mb['ann'])}** | **{bt.pct(mb['dd'])}** "
             f"| **{bt.pct(mb['vol'])}** | **{mb['shp']:.2f}** | **{bt.pct(mb['ret'])}** | 0.0% |")
    L.append("")
    L += ["## 四、结论", "",
          f"1. **时间维度：几乎没有空仓**（{ncash / n_days * 100:.1f}%，{ncash} 个交易日），"
          "所以「大部分时间坐在现金里」这个解释**不成立**。",
          f"2. **金额维度：空仓就是唯一致败项**。持仓日（{m_inv.sum() / n_days * 100:.1f}%）策略 "
          f"{bt.pct(s_inv)} 反而**领先**同期基准 {bt.pct(b_inv)}（相对 {bt.pct(rel_inv)}）；"
          f"但那 {len(cps)} 段共 {ncash} 天现金，同期基准 {bt.pct(b_cash)}，"
          f"把这点领先全部吃掉，全期最终**落后 {bt.pct(rel_gap)}**。",
          f"3. **但持仓日那点超额小得可怜**：相对领先仅 {bt.pct(rel_inv)}"
          f"（年化约 {bt.pct(rel_inv_ann)}），却要承担 2 倍波动（20.6% vs 10.4%）、"
          f"近 3 倍回撤（-36.7% vs -12.9%）—— 这正是夏普 0.68 vs 1.30 的来源："
          "择券换来的收益配不上它引入的风险。",
          "4. **关于「最差也持有债券」**：现行规则没有债券兜底，无候选即归 0 收益的现金。"
          f"但变体B（空仓改持 {BOND}）只把年化从 +12.09% 提到 +12.24% —— "
          "因为空仓期错过的是纳指/黄金/红利这些**风险资产**的上涨，不是债券收益，"
          "持债兜底几乎不解决问题。",
          "5. 真正有效的是变体C（无候选则保持原仓）：年化 +13.30% 追平等权，"
          "回撤也从 -36.7% 收到 -29.2%；但**仍远不如等权持有的 -12.9%**。",
          "6. 变体D（去掉 MA20、永远满仓动量第一）反而更差（+8.75%）→ MA20 出场信号本身"
          "是有信息量的，问题在于把它当作**清仓**而不是**换仓**。",
          "",
          f"> 稳定性提醒：全期约 {int(n_days / 21)} 次调仓里只有 {len(cps)} 次判成空仓，"
          "却决定了整个胜负 —— 结论建立在极薄样本上，不宜外推。",
          "",
          "> 模拟结果，非投资建议。", ""]

    open(OUT_MD, "w", encoding="utf-8").write("\n".join(L))
    print("\n".join(L))
    print(f"\n-> {OUT_MD}")

    fig, ax = plt.subplots(figsize=(12, 5.5))
    for k, (label, _) in variants.items():
        e = res[k][0][sall]
        ax.plot(e.index, e.values, label=label, lw=1.0)
    ax.plot(bm[sall].index, bm[sall].values, label="六类等权买入持有（基准）", lw=1.6, color="k")
    ax.legend(fontsize=9)
    ax.set_title("六类资产：空仓替代方案 vs 等权持有（净值）")
    ax.grid(alpha=0.3)
    fig.tight_layout()
    fig.savefig(OUT_PNG, dpi=140, facecolor="white")
    print(f"图: {OUT_PNG}")


if __name__ == "__main__":
    main()
