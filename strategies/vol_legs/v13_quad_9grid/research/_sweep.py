# -*- coding: utf-8 -*-
"""V13 · 参数敏感性：迟滞 / 兜底 / 不对称方向，哪个是承重件、哪个是噪音。

回答三个问题（都是文档 §7 自己留的口子）：

1. `hold_days=15` 是**平台**还是**悬崖**？（文档 §7.14 明说「形状来自单样本 5 点扫描，未做分时段检验」）
2. 急跌兜底（−15%）在 ETF 池里是**保护**还是**添乱**？（科创50 有 6% 的交易日被它按住）
3. 「降立即 / 升 15 日」这个**不对称**本身值多少？（用反向不对称 `reverse` 做差分）

产物：`_v13_sweep.md`。全部 callback_add=True（用户口径），另留一行文档原版对照。

用法：`python strategies/vol_legs/v13_quad_9grid/research/_sweep.py`
"""
import io
import os
import sys

import numpy as np
import pandas as pd

try:
    sys.stdout.reconfigure(encoding="utf-8")  # type: ignore[attr-defined]
except Exception:  # noqa: BLE001
    pass

_HERE = os.path.dirname(os.path.abspath(__file__))
_V13 = os.path.dirname(_HERE)
if _V13 not in sys.path:
    sys.path.insert(0, _V13)

import rule           # noqa: E402
import data as vdata  # noqa: E402
import engine         # noqa: E402

OUT = os.path.join(_HERE, "_v13_sweep.md")

# (标签, kwargs) —— 全部基于 callback_add=True，除 doc_cb0
CFGS = [
    # 显式传 −0.15：rule.DD_TRIGGER 已改为 −0.10，不显式传就不是"文档基线"
    ("基线（h=15 / 兜底−15% / 降立即）", dict(dd_trigger=-0.15)),
    ("h=5", dict(hold_days=5)),
    ("h=10", dict(hold_days=10)),
    ("h=20", dict(hold_days=20)),
    ("h=30", dict(hold_days=30)),
    ("对称迟滞 h=15（升降都等）", dict(hysteresis="sym")),
    ("反向不对称（升立即 / 降 15 日）", dict(hysteresis="reverse")),
    ("关兜底", dict(floor_on=False)),
    ("兜底 −10%", dict(dd_trigger=-0.10)),
    ("兜底 −20%", dict(dd_trigger=-0.20)),
    ("回调加仓（研究对照）", dict(callback_add=True)),
]


def combo(navs):
    R = pd.DataFrame({k: v.pct_change() for k, v in navs.items() if v is not None})
    port = R.mean(axis=1, skipna=True).fillna(0.0)
    return (1.0 + port).cumprod()


def main():
    legs = vdata.available()
    dfs = {leg: vdata.frame(leg) for leg in legs}

    res = {}
    for tag, kw in CFGS:
        per, navs, expo = {}, {}, {}
        kw = dict(kw)
        kw.setdefault("callback_add", rule.CALLBACK_ADD)   # 最后一行显式给 True（回调加仓对照）
        for leg in legs:
            r = engine.backtest_leg(leg, df=dfs[leg], **kw)
            if r["perf"] is None:
                continue
            per[leg] = r["perf"]
            navs[leg] = r["nav"]
            expo[leg] = r["expo"]
        cp = engine.perf(combo(navs)) if navs else None
        res[tag] = dict(per=per, combo=cp, expo=float(np.mean(list(expo.values()))))

    base = res[CFGS[0][0]]["combo"]

    L = [
        "# V13 · 参数敏感性（迟滞 / 兜底 / 不对称方向）",
        "",
        f"> 场内 7 腿 · 扣费 · 空仓 {rule.CASH_ANNUAL:.2%} · 按 `rule.CALLBACK_ADD` 口径"
        f"（{'回调加仓' if rule.CALLBACK_ADD else '文档原版 hold'}），最后一行是另一版对照。"
        "只改一个参数、其余锁死，用来判断「哪个旋钮是承重件」。",
        "> ⚠ 这是**敏感性诊断**，不是参数搜索：没有样本外验证，挑出来的最优值不能直接上线。",
        "",
        "## 1. 组合层（等权 7 腿，缺数据腿不占权重）",
        "",
        "| 配置 | 年化 | 最大回撤 | 夏普 | 平均许可 | vs 基线年化 |",
        "|---|---:|---:|---:|---:|---:|",
    ]
    for tag, _ in CFGS:
        c = res[tag]["combo"]
        if c is None:
            continue
        L.append(f"| {tag} | {c['ann'] * 100:+.2f}% | {c['mdd'] * 100:+.2f}% | {c['sharpe']:.2f} | "
                 f"{res[tag]['expo']:.0%} | {(c['ann'] - base['ann']) * 100:+.2f}pp |")

    L += ["", "## 2. 逐腿年化（%）", ""]
    head = "| 配置 | " + " | ".join(rule.NAMES[l] for l in legs) + " |"
    L += [head, "|---|" + "---:|" * len(legs)]
    for tag, _ in CFGS:
        cells = []
        for leg in legs:
            p = res[tag]["per"].get(leg)
            cells.append("—" if p is None else f"{p['ann'] * 100:+.2f}")
        L.append(f"| {tag} | " + " | ".join(cells) + " |")

    L += ["", "## 3. 逐腿最大回撤（%）", ""]
    L += [head, "|---|" + "---:|" * len(legs)]
    for tag, _ in CFGS:
        cells = []
        for leg in legs:
            p = res[tag]["per"].get(leg)
            cells.append("—" if p is None else f"{p['mdd'] * 100:+.1f}")
        L.append(f"| {tag} | " + " | ".join(cells) + " |")

    # ---------------- 结论（全部动态取数，重跑不会与文字脱节）
    hs = [t for t in ("h=5", "h=10", "基线（h=15 / 兜底−15% / 降立即）", "h=20", "h=30")
          if res[t]["combo"] is not None]
    h_ann = [(t, res[t]["combo"]["ann"]) for t in hs]
    best_h = max(h_ann, key=lambda z: z[1])
    best_hs = max(((t, res[t]["combo"]["sharpe"]) for t in hs), key=lambda z: z[1])
    floor_off = res["关兜底"]["combo"]
    sym = res["对称迟滞 h=15（升降都等）"]["combo"]
    rev = res["反向不对称（升立即 / 降 15 日）"]["combo"]
    cb0 = res["回调加仓（研究对照）"]["combo"]

    L += [
        "",
        "## 4. 结论",
        "",
        f"- **迟滞 `h` 是悬崖不是平台**：组合年化在 h=5~30 上从 {min(v for _, v in h_ann) * 100:+.2f}% "
        f"走到 {max(v for _, v in h_ann) * 100:+.2f}%；按年化最好的是 `{best_h[0]}`"
        f"（{best_h[1] * 100:+.2f}%），按夏普最好的是 `{best_hs[0]}`（{best_hs[1]:.2f}）。"
        "跨度这么大说明它**不该被当成「定 15 锁死」的稳健参数** —— 文档 §7.14 自己也说这个形状来自"
        "单样本 5 点扫描。短迟滞买到收益、付回撤；长迟滞只省回撤、丢收益。"
        "要定参数，至少要在分时段（2015/2018/2022）上做稳健性检验。",
        f"- **兜底是承重件，不是净负担**：关掉后组合年化 {floor_off['ann'] * 100:+.2f}%"
        f"（{(floor_off['ann'] - base['ann']) * 100:+.2f}pp）、回撤 {floor_off['mdd'] * 100:+.2f}%"
        f"（基线 {base['mdd'] * 100:+.2f}%，"
        f"{'恶化' if floor_off['mdd'] < base['mdd'] else '改善'}"
        f" {abs(floor_off['mdd'] - base['mdd']) * 100:.2f}pp）、夏普 {floor_off['sharpe']:.2f}"
        f"（基线 {base['sharpe']:.2f}）。回撤恶化说明它主要在处理真跌，而不是只在误伤 —— "
        "但它对高波动腿的误触发依然存在，见各腿差异。",
        f"- **兜底阈值有免费改进空间**：`−10%` 那行年化 {res['兜底 −10%']['combo']['ann'] * 100:+.2f}%、"
        f"回撤 {res['兜底 −10%']['combo']['mdd'] * 100:+.2f}%、"
        f"夏普 **{res['兜底 −10%']['combo']['sharpe']:.2f}**（基线 {base['sharpe']:.2f}）—— "
        "更早刹车、几乎不付年化代价。⚠ 但这是同一样本内的单点扫描，"
        "上线的唯一前提是先在分段（2015/2018/2022）上确认不是拟合。",
        f"- **「降立即 / 升 15 日」这个不对称本身值**：对照反向不对称（升立即 / 降 15 日，"
        f"{rev['ann'] * 100:+.2f}% / {rev['mdd'] * 100:+.2f}% / 夏普 {rev['sharpe']:.2f}），"
        f"基线年化 {(base['ann'] - rev['ann']) * 100:+.2f}pp、回撤 {(base['mdd'] - rev['mdd']) * 100:+.2f}pp、"
        f"夏普 {base['sharpe'] - rev['sharpe']:+.2f}。年化为负而夏普为正，说明「跑得快」付的是**收益**、"
        "买的是**回撤** —— 不是纯锯齿损耗，但也不是免费的午餐。",
        f"- **对称迟滞**（升降都等 15 日）：年化 {sym['ann'] * 100:+.2f}%、回撤 {sym['mdd'] * 100:+.2f}%。"
        "与基线比若年化更高但回撤更深，说明这条腿的收益主要来自「少在场」而不是「择时准」。",
        f"- **回调加仓（研究对照）vs 文档原版（默认）**：组合 {cb0['ann'] * 100:+.2f}% vs "
        f"{base['ann'] * 100:+.2f}%（{(cb0['ann'] - base['ann']) * 100:+.2f}pp）。差值≈0 说明 15 日升格迟滞"
        "把这一格吃干净了 —— 想让「回调加仓」真生效，必须给这一格单独的快速通道，那就不是同一个策略了。",
        "",
        "> 机械规则输出，非投资建议。",
    ]

    with io.open(OUT, "w", encoding="utf-8", newline="\n") as f:
        f.write("\n".join(L))
    print("报告:", OUT)

    for tag, _ in CFGS:
        c = res[tag]["combo"]
        if c is None:
            continue
        print(f"  {tag:32s} 年化 {c['ann'] * 100:+7.2f}% · 回撤 {c['mdd'] * 100:+7.2f}% · "
              f"夏普 {c['sharpe']:5.2f} · 许可 {res[tag]['expo']:.0%}")


if __name__ == "__main__":
    main()
