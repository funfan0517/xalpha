# v14 · 中证A500 单腿策略：E3 前三等权融合（V3 + V2 + V12）

> **★ 研究超越项**。规则与数字来源：
> [`research/beat_best/_beat_best_top3.md`](../research/beat_best/_beat_best_top3.md)（2026-11-19 重跑）。
> 同尺夏普 0.66 → **0.82**，后段 0.34 → **0.38**，年化 +6.59% → +9.22%，
> 回撤 −13.74% → −16.20%；毛夏普 0.74 → 0.92（**信号效应为主**），换手 8.5 → 44.8 次/年。
>
> 2026-11-19 前本策略是「V3 单支、研究确认无提升」。V12 把 `慢跌` 改到 0% 后，
> A500 的 ★3 变成 V12（夏普 0.81），于是「前二等权（E2）」只差 0.01 没跨过闸门、
> 「**前三等权（E3）**」才是唯一通过双闸门的组合 —— 基线从「一条腿的规则」变成
> 「三条腿的融合」，这是本次口径变化的原因。

## 规则

`(V3 MACD+BOLL 开口突破 + V2 BOLL(35,2) 突破 + V12 八态趋势许可) / 3` ——
三条**同族不同信息源**的信号等权：V2 只看价格突破、V3 要 MACD 零轴上 + 布林开口、
V12 是 regime × 位置 × 速度 × 量能的许可上限。t 日收盘出决策，**t+1 收盘成交**；
空仓按货基 1.40% 计息。

逐日权重来自各变种**原生引擎**，经 `strategies/vol_legs/_top3_bridge.py` 隔离加载，不重写规则。
成本为**研究同尺口径**：单边 10bp（不是该腿原生费率），故绝对数与 `best_by_leg.md` 不可直接对账。
回测先在全样本上算信号、再截预热窗（状态层不会在窗口起点被重置），故数字与研究报告
「先截窗再算信号」略有差异；**相对结论与超越关系不变**。

## 文件

```
v14_a500_e3/
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
python strategies/vol_legs/v14_a500_e3/backtest.py
python strategies/vol_legs/v14_a500_e3/daily/_daily_scan.py
python strategies/vol_legs/v14_a500_e3/research/_plot_leg.py
python strategies/vol_legs/v14_a500_e3/research/_plot_leg.py --years 3
```

机械规则输出，非投资建议。
