# v16 · 纳指100 单腿策略：MA200 切换（上=V10 波动层 / 下=V12 八态趋势）

> **★ 研究超越项（最佳）**。规则与数字来源：
> [`research/beat_best/_beat_best_top3.md`](../research/beat_best/_beat_best_top3.md)（2026-11-20 第三次重跑）。
> 同尺夏普 **0.77 → 0.81**、后段 0.94 → **1.01**、回撤 −27.38% → **−12.97%**，PASS 双闸门。
> 2026-11-20 由 `v16_ndx_v10`（纯 V10）改为本规则，目录同步改名。

## 规则

**`px > MA200` 上方用 V10 波动层**（ER20/10 自适应 + 5 日死区，吃趋势 beta），
**下方用 V12 八态趋势许可**（八态在下跌段自带降仓/空仓）。闸门用 t 日收盘的 `px vs MA200`
（未滞后），T 日收盘出决策 → **T+1 收盘成交**；空仓按货基 1.40% 计息。

逐日权重来自各变种**原生引擎**，经 `strategies/vol_legs/_top3_bridge.py` 隔离加载，不重写规则。
成本为**研究同尺口径**：单边 10bp（不是该腿原生费率），故绝对数与 `best_by_leg.md` 不可直接对账。
回测先在全样本上算信号、再截预热窗（状态层不会在窗口起点被重置），故数字与研究报告
「先截窗再算信号」略有差异（0.83 vs 0.81）；**相对结论与超越关系不变**。

## 为什么是这条腿、这条规则

1. 纳指的 ★1 一直是 **V10**（`_best_by_leg.md`，0.73），但 V10 的弱点是**回撤 −27%**——
   高平均仓位（67%）在下跌段只靠 vol20 压仓，慢跌里压不住。
2. `_beat_best_top3.md` 的 `SW` 规则是「MA200 上方用 ★1、下方切到前三里平均仓位最低的一条」。
   2026-11-20 v12 的美股两腿改 C5（无死区）后，V12 在纳指的含费夏普由 0.63 升到 0.69、
   挤进 ★2，而它的平均仓位只有 47% —— **下方目标从 V9 换成 V12**，回撤立刻合规。
3. 邻域检验（报告自己要求的，MA 100~300）：100/150/175/200/225/250 全部仍过双闸门
   （夏普 0.78~0.86），只有 275/300 掉线 —— 不是 200 这一个点的拟合。
4. 代价：换手 20.8 → 33.1 次/年（10bp 下摩擦仍约 0.7pp/年），且**依赖两条引擎**（V10 + V12），
   任一改参数都会动这条腿 —— 改动前先跑本目录 `backtest.py` 复核。

## 结果（`backtest/_v16_bt.md`，2026-11-20）

| 口径 | 年化 | 波动 | 最大回撤 | 夏普 | Calmar | 平均仓位 | 调仓/年 |
|---|---:|---:|---:|---:|---:|---:|---:|
| v16 择时 | +10.62% | 11.16% | −12.97% | 0.83 | 0.82 | 61% | 32.6 |
| 买入持有 | +14.41% | 22.46% | −53.71% | 0.58 | 0.27 | 100% | 0.0 |

后段（自 2018-08-08）夏普 **1.01**。

## 文件

```
v16_ndx_sw/
├── engine.py            # LEG / MA_WIN / gate() / weight() / detail() / run() / perf()
├── backtest.py          # 回测入口 -> backtest/_<v>_bt.{md,jsonl}
├── backtest/
├── daily/_daily_scan.py # 每日操作 -> daily/_<v>_daily.{md,json}（含闸门读数与两条腿分支仓位）
├── daily/
├── research/_plot_leg.py# 图 -> research/_<v>___leg__.png / _<v>___leg___3y.png
├── research/
└── README.md
```

## 运行

```powershell
python strategies/vol_legs/v16_ndx_sw/backtest.py
python strategies/vol_legs/v16_ndx_sw/daily/_daily_scan.py
python strategies/vol_legs/v16_ndx_sw/research/_plot_leg.py
python strategies/vol_legs/v16_ndx_sw/research/_plot_leg.py --years 3
```

机械规则输出，非投资建议。
