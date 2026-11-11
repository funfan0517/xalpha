# -*- coding: utf-8 -*-
"""V13 · 趋势许可九宫格 · 七腿回测（单一口径：回调加仓）。

产出（本目录 `backtest/`）：
- `_v13_quad_9grid_bt.md`   逐腿 + 组合 + 当前格子
- `_v13_quad_9grid_bt.jsonl` 逐腿逐版一行，便于 `strategies/_merge_verify.py` 比对

用法：`python strategies/vol_legs/v13_quad_9grid/backtest.py`
"""
import io
import json
import os
import sys

import numpy as np
import pandas as pd

try:
    sys.stdout.reconfigure(encoding="utf-8")  # type: ignore[attr-defined]
except Exception:  # noqa: BLE001
    pass

_HERE = os.path.dirname(os.path.abspath(__file__))
if _HERE not in sys.path:
    sys.path.insert(0, _HERE)

import rule          # noqa: E402
import data as vdata  # noqa: E402
import engine        # noqa: E402

OUT_DIR = os.path.join(_HERE, "backtest")
OUT_MD = os.path.join(OUT_DIR, "_v13_quad_9grid_bt.md")
OUT_JSONL = os.path.join(OUT_DIR, "_v13_quad_9grid_bt.jsonl")

# 2026-11-10 取消「文档版 / 回调加仓」两版并列：两版只差「向上×跌破」一格，
# 组合 9.32%（回调加仓）vs 9.26%（文档版）、夏普都是 0.95，逐腿互有胜负 —— 差异在噪声内，
# 并列两列只是把噪声摊开。口径统一为回调加仓（用户 2026-11-09 的要求）；
# `rule.CALLBACK_ADD` 降级为**研究开关**，`research/_sweep.py` 仍保留一行文档版做对照。
TAG = "回调加仓"


def pct(x, nd=2):
    return "—" if x is None or (isinstance(x, float) and not np.isfinite(x)) else f"{x * 100:+.{nd}f}%"


def bh(leg, start):
    """同窗口恒持（买入持有），作为对照。"""
    df = vdata.frame(leg)
    px = df["close"].loc[start:]
    nav = px / px.iloc[0]
    return engine.perf(nav)


def combo(results, equal_vol=False):
    """组合：各腿净值日收益。

    - 默认**等权**（缺数据的腿不占权重）；
    - `equal_vol=True` 用滚动 60 日波动的倒数配权（`shift(1)`，无前视）——
      这是 `research/_v13_optimize.md` 里夏普 0.95 → 1.08 的那条改进；
    - **A500 不进组合**：563360 只有 0.84 年样本，进组合只贡献噪声。
    """
    rets = {}
    for key, r in results.items():
        if r["nav"] is None or key == "a500":
            continue
        rets[key] = r["nav"].pct_change()
    if not rets:
        return None
    R = pd.DataFrame(rets)
    if equal_vol:
        V = R.rolling(60, min_periods=20).std()
        inv = 1.0 / V.replace(0.0, np.nan)
        wgt = inv.div(inv.sum(axis=1), axis=0).shift(1)
        port = (R * wgt).sum(axis=1, min_count=1).fillna(0.0)
    else:
        port = R.mean(axis=1, skipna=True).fillna(0.0)
    return (1.0 + port).cumprod()


def main():
    os.makedirs(OUT_DIR, exist_ok=True)
    legs = vdata.available()
    dfs = {leg: vdata.frame(leg) for leg in legs}

    results, rows, cur = {}, [], []
    for leg in legs:
        r = engine.backtest_leg(leg, callback_add=rule.CALLBACK_ADD, df=dfs[leg])
        results[leg] = r
        if r["perf"] is None:
            rows.append(dict(leg=leg, name=r["name"], bars=len(dfs[leg]),
                             start=None, end=str(dfs[leg].index[-1].date()),
                             ann=None, mdd=None, sharpe=None, expo=None,
                             acts=0, bh_ann=None, note="预热不足（需 290 根）"))
            continue
        b = bh(leg, r["start"])
        rows.append(dict(leg=leg, name=r["name"], bars=r["bars"],
                         start=str(r["start"].date()), end=str(r["end"].date()),
                         ann=r["perf"]["ann"], mdd=r["perf"]["mdd"],
                         sharpe=r["perf"]["sharpe"], expo=r["expo"],
                         acts=len(r["acts"]), bh_ann=b["ann"], bh_mdd=b["mdd"],
                         note=""))
        last = r["sm"]["diag"].iloc[-1]
        cur.append(dict(leg=leg, name=r["name"], cell=last["cell"],
                        w=float(r["w"].iloc[-1]), expo=r["expo"], end=str(r["end"].date())))

    # ---------------- 报告
    L = [
        "# V13 · 趋势许可九宫格 · 七腿回测",
        "",
        "> 规则：`E:\\qianwen\\jijin\\趋势许可九宫格规则_v1.3.md`（2026-11-10 定稿）。"
        "文字 → 参数的映射见 `rule.py`；状态机与兜底见 `engine.py`。",
        f"> 口径：**场内 ETF 前复权日线**（`data/_ohlcv.json`）· 成交 T 日收盘判格、T+1 起承担仓位 · "
        f"空仓计货基 {rule.CASH_ANNUAL:.2%} · 摩擦单边 {rule.FEE_SIDE * 1e4:.1f}bp · 预热 {rule.WARMUP} 根。",
        "> 口径：**回调加仓**（「向上×跌破」= 满带许可，用户 2026-11-09）。文档版（该格 `hold`）"
        "已测：组合 9.26% vs 9.32%、夏普都是 0.95，逐腿互有胜负 → 差异在噪声内，故**不再并列**；"
        "`rule.CALLBACK_ADD` 仅作研究开关。",
        "",
        "## 1. 逐腿",
        "",
        "| 腿 | 样本 | 年化 | 最大回撤 | 夏普 | 平均许可 | 调仓次数 | 恒持年化 | 差 |",
        "|---|---|---:|---:|---:|---:|---:|---:|---:|",
    ]
    for r in rows:
        if r["ann"] is None:
            L.append(f"| {r['name']} | {r['bars']} 根 | — | — | — | — | — | — | {r['note']} |")
            continue
        edge = (r["ann"] - r["bh_ann"]) * 100
        L.append(f"| {r['name']} | {r['start']} ~ {r['end']}（{r['bars']} 根） | "
                 f"{pct(r['ann'])} | {pct(r['mdd'])} | {r['sharpe']:.2f} | {r['expo']:.0%} | "
                 f"{r['acts']} | {pct(r['bh_ann'])} | {edge:+.2f}pp |")

    L += ["", "## 2. 组合层（A500 不进组合；两种配权）", "",
          "等波动 = 按滚动 60 日波动的倒数配权（`shift(1)`，无前视）。"
          "依据 `research/_v13_optimize.md`：同一规则下等波动比等权夏普高约 0.13、回撤浅约 1.4pp。",
          ""]
    for ev, lbl in ((False, "等权"), (True, "等波动")):
        nav = combo({leg: results[leg] for leg in legs}, equal_vol=ev)
        if nav is None:
            continue
        p = engine.perf(nav)
        L.append(f"- **九宫格 · {lbl}**：累计 {pct(p['cum'], 1)} · 年化 {pct(p['ann'])} · "
                 f"最大回撤 {pct(p['mdd'])} · 夏普 {p['sharpe']:.2f} · 样本 {p['days']} 根")
    bh_navs = {}
    for leg in legs:
        r = results[leg]
        if r["nav"] is None:
            continue
        px = dfs[leg]["close"].loc[r["start"]:]
        bh_navs[leg] = px / px.iloc[0]
    for ev, lbl in ((False, "等权"), (True, "等波动")):
        if not bh_navs:
            break
        nav_bh = combo({k: dict(nav=v) for k, v in bh_navs.items()}, equal_vol=ev)
        pb = engine.perf(nav_bh)
        L.append(f"- **恒持 · {lbl}（对照）**：累计 {pct(pb['cum'], 1)} · 年化 {pct(pb['ann'])} · "
                 f"最大回撤 {pct(pb['mdd'])} · 夏普 {pb['sharpe']:.2f}")

    L += [
        "",
        "## 3. 当前格子（最新一根）",
        "",
        "| 腿 | 格子 | 许可 | 样本内平均许可 | 截至 |",
        "|---|---|---:|---:|---|",
    ]
    for c in cur:
        L.append(f"| {c['name']} | {c['cell']} | {c['w']:.0%} | {c['expo']:.0%} | {c['end']} |")

    L += [
        "",
        "## 4. 失效诊断：兜底占比与格子分布",
        "",
        "兜底是唯一跨格覆盖的规则，对高波动标的会**频繁误触发**（把「涨太快后的正常回落」当成崩盘）。"
        "下表占比越高，越说明这条腿的收益被兜底与 15 日迟滞吃掉了。",
        "",
        "| 腿 | 兜底天数占比 | 主要格子（占比前三） |",
        "|---|---:|---|",
    ]
    for leg in legs:
        r = results[leg]
        if r["nav"] is None:
            continue
        d = r["sm"]["diag"]
        vc = d["cell"].value_counts(normalize=True)
        items = [(k, v) for k, v in vc.items() if k][:3]
        top = "、".join(f"`{k}` {v:.0%}" for k, v in items)
        L.append(f"| {r['name']} | {d['floor'].mean():.0%} | {top} |")

    L += [
        "",
        "**科创50 的负收益不是实现 bug，是规则的必然结果**：2024-09-24 那波急涨里，格子从"
        "「向下×站上」（反弹 = 只持有）→「中性×站上」→「向上×站上」，但升格要连续 15 日，"
        "结果到 10-08 见顶全程只有 0.2 仓；10-08 之后回落又立刻触发兜底。"
        "文档 §7.7 已把这条列为已知失效点（`hold_days=15` 在高波动品种上过钝）——"
        "本表的数字只是把它量化了。",
        "",
        "## 5. 读法",
        "",
        "- **九宫格是「许可上限」不是目标仓位**：数值格=绝对上限（高了立即减、低了只解锁加仓资格），"
        "`hold` 格什么都不做。所以「平均许可」不等于「平均持仓意愿」，它只是这条腿在样本里被允许多少仓。",
        "- **「向上×跌破」这一格已定稿为「回调加仓」（满带许可），不再并列文档版 `hold`**："
        "两版组合只差 0.06pp、夏普都 0.95，说明 15 日升格迟滞把这一格吃掉了（回调通常撑不到 15 日）——"
        "这本身就是结论，不是 bug；要真让回调加仓生效，得给该格单独的 ≤2 日快速通道，那是新策略。",
        "- **A500 只有 1 年样本**（563360 于 2024-10 上市，预热 290 根后剩不到 1 年），其行不具信息量；"
        "文档 §7.1 已把这个限制写在前面。",
        "- **口径不可与场外净值策略混算**：本表的收益是 ETF 价格口径（无分红再投、含折溢价与汇率），"
        "美股两腿用 513100 / 513500 人民币代理，信号里掺汇率。",
        "",
        "> 机械规则输出，非投资建议。",
    ]

    with io.open(OUT_MD, "w", encoding="utf-8", newline="\n") as f:
        f.write("\n".join(L))
    with io.open(OUT_JSONL, "w", encoding="utf-8", newline="\n") as f:
        for r in rows:
            f.write(json.dumps(r, ensure_ascii=False) + "\n")
    print("报告:", OUT_MD)
    print("明细:", OUT_JSONL)

    for r in rows:
        if r["ann"] is None:
            print(f"  {r['name']:8s} 跳过：{r['note']}")
            continue
        print(f"  {r['name']:8s} 年化 {pct(r['ann']):>8s} · 回撤 {pct(r['mdd']):>8s} · "
              f"夏普 {r['sharpe']:4.2f} · 许可 {r['expo']:.0%} · 调仓 {r['acts']:3d} · "
              f"恒持 {pct(r['bh_ann'])}")


if __name__ == "__main__":
    main()
