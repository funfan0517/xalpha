# v17 · 标普500 单腿策略：V12 八态趋势（当前 ★1）

> **研究确认无提升**。规则与数字来源：
> [`research/beat_best/_beat_best_top3.md`](../research/beat_best/_beat_best_top3.md)（2026-11-19 重跑）。
> 同尺基线夏普 0.56（V12）；十一个候选（E2/E3、双确认、任一在场、月末轮动、MA200 切换、TV10/15/20 三档上限）
> **无一通过双闸门** —— 最好的 MAX2 只把全样本抬到 0.61，后段 0.74 < 基线 0.80，判过拟合不采纳。
>
> 2026-11-19 前本策略是「MAX2 任一在场（max(V12, V13)）」的 ★超越项。V12 把 `慢跌` 改到 0% 后，
> 它自己在标普500 上从 0.59 升到 ★1，MAX2 相对它不再有增量，故**退化为单一 V12**
> （目录名 `v17_spx_max2` → `v17_spx_v12`）。

## 规则

V12 八态趋势 → 带内仓位许可上限（原生 `engine.backtest_leg` 的逐日权重，v12.1 生效口径：
`rule.CAP` + 沪深300/科创50 的 `CAP_OPT` 与 σ 覆盖；标普500 走规格 `CAP`）。
t 日收盘出决策，**t+1 收盘成交**；空仓按货基 1.40% 计息。

逐日权重来自 V12 **原生引擎**，经 `strategies/vol_legs/_top3_bridge.py` 隔离加载，不重写规则。
成本为**研究同尺口径**：单边 10bp（不是该腿原生费率），故绝对数与 `best_by_leg.md` 不可直接对账。
标普500 的「最优」对费率极敏感（`best_by_leg.md` §3-②）—— 同尺 10bp 下 V12 0.59、
V13 0.59、V9 0.56 三支的差距在噪声内，**不要当成 V12 明显胜出**。

## 文件

```
v17_spx_v12/
├── engine.py            # LEG / weight() / run() / perf()
├── backtest.py          # 回测入口 -> backtest/_<v>_bt.{md,jsonl}
├── backtest/
├── daily/_daily_scan.py # 每日操作 -> daily/_<v>_daily.{md,json}
├── daily/
├── research/_plot_leg.py# 图 -> research/_<v>___leg__.png / _<v>___leg___3y.png
├── research/
└── README.md
```

## 运行

```powershell
python strategies/vol_legs/v17_spx_v12/backtest.py
python strategies/vol_legs/v17_spx_v12/daily/_daily_scan.py
python strategies/vol_legs/v17_spx_v12/research/_plot_leg.py
python strategies/vol_legs/v17_spx_v12/research/_plot_leg.py --years 3
```

机械规则输出，非投资建议。
