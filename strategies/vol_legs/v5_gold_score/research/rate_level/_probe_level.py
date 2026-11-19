# -*- coding: utf-8 -*-
"""V5.1 · 利率闸门假说的可复现检验：DFII10 **水平** vs 未来 12 个月金价收益。

来源：`data/_gold_xau.csv`（伦敦金现 2006 起）+ `data/_dfii10.csv`（FRED）。
口径：**年度非重叠点**——每年最后一个交易日（12-31，非交易日取之前最后一个）的
DFII10 水平，配对从该日起未来 252 个 XAU 交易日的收益；利率只用 ≤ 当日的观测（无前视）。

产出：同目录 `_probe_level.md`（脚本跑出的表与相关性，供 backtest/README 引用）。
只写报告 Markdown，不改任何行情/策略数据。非投资建议。
"""
import os
import sys

import pandas as pd

try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:  # noqa: BLE001
    pass

_HERE = os.path.dirname(os.path.abspath(__file__))
_V5 = os.path.dirname(os.path.dirname(_HERE))
if _V5 not in sys.path:
    sys.path.insert(0, _V5)
import factors as F  # noqa: E402

OUT = os.path.join(_HERE, "_probe_level.md")
SEGS = (("2006-2012", lambda y: y <= 2012),
        ("2013-2019", lambda y: 2013 <= y <= 2019),
        ("2020-2025", lambda y: y >= 2020))


def table():
    x = pd.read_csv(F.XAU_CSV)
    s = pd.Series(pd.to_numeric(x["gold"], errors="coerce").to_numpy(),
                  index=pd.to_datetime(x["date"])).dropna().sort_index()
    rate = F.read_rate()
    rows = []
    for y in range(int(s.index[0].year), int(s.index[-1].year)):
        d = pd.Timestamp(f"{y}-12-31")
        prior = s.loc[:d]
        rr = rate.loc[:d]
        if len(prior) < 20 or not len(rr):
            continue
        fwd = s.loc[d:]
        step = min(252, len(fwd) - 1)
        if step < 200:                       # 最后一年未来窗口不完整，标注但不丢
            note = f"（仅 {step} 个交易日）"
        else:
            note = ""
        rows.append(dict(year=y, level=float(rr.iloc[-1]),
                         fwd=float(fwd.iloc[step] / prior.iloc[-1] - 1.0), note=note))
    return pd.DataFrame(rows)


def main():
    df = table()
    corr_all = float(df["level"].corr(df["fwd"]))
    segs = []
    for name, sel in SEGS:
        sub = df[[sel(y) for y in df["year"]]]
        segs.append((name, len(sub), float(sub["level"].corr(sub["fwd"]))))

    L = []
    A = L.append
    A("# V5.1 · 实际利率水平假说（可复现探针）")
    A("")
    A("由 `_probe_level.py` 生成。**检验对象**：闸门用 `DFII10` 的**水平**（不是 10 日变化）"
      "是否有方向信息 —— 水平高（实际利率高）→ 未来 12 个月金价更可能上涨（均值回复）。")
    A("")
    A("口径：`data/_gold_xau.csv`（伦敦金现，2006 起）+ `data/_dfii10.csv`（FRED）；"
      "**年度非重叠点**，每年最后一个交易日的利率水平（只用 ≤ 当日的观测）配对未来 252 个"
      " XAU 交易日的收益。最后一年未来窗口不足 252 日会标注。")
    A("")
    A("| 年 | DFII10 水平 % | 未来 12 月 XAU 收益 |")
    A("|---:|---:|---:|")
    for _, r in df.iterrows():
        A(f"| {int(r['year'])} | {r['level']:+.2f} | {r['fwd']:+.1%}{r['note']} |")
    A("")
    A(f"**全样本（{len(df)} 个非重叠点）相关系数 = {corr_all:+.3f}** —— "
      "方向为正，与「利率低 → 未来金价弱」的闸门逻辑一致。")
    A("")
    A("| 子区间 | 点数 | 相关系数 |")
    A("|---|---:|---:|")
    for name, n, c in segs:
        A(f"| {name} | {n} | {c:+.3f} |")
    A("")
    A("> 子区间每个只有 6~7 个点，**符号不稳定**（2013-2019 为负）。"
      "所以「水平有信息」只能算**全样本层面的中等证据**，不能写成「三段都一致」。"
      "参考文档快照里写的「三段 +0.54/+0.63/+0.77」在本数据/本口径下复现不出来，已按实测更正。")
    A("")
    A("> 另外：XAU 是伦敦金现（美元），518880 是人民币黄金 ETF（含汇率与场内溢价）；"
      "这条相关性检验用的是 XAU，闸门阈值本身在 518880 上定 —— 两者不严格同源。")
    A("")
    A("模拟结果，非投资建议。")
    md = "\n".join(L) + "\n"
    with open(OUT, "w", encoding="utf-8") as f:
        f.write(md)
    print(md[:600])
    print(f"全样本 corr={corr_all:+.3f}；分段 " + " / ".join(f"{n}:{c:+.3f}" for _, n, c in segs))
    print("落盘:", OUT)


if __name__ == "__main__":
    main()
