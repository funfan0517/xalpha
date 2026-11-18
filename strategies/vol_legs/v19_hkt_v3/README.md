# v19 · 恒生科技 单腿策略：V3 MACD+BOLL（当前 ★1）

> **研究确认无提升**。规则与数字来源：
> [`research/beat_best/_beat_best_top3.md`](../research/beat_best/_beat_best_top3.md)（2026-11-18）。
> _beat_best_top3.md：全腿候选夏普都趴在基线附近；保持当前 ★1 V3。

## 规则

V3 MACD+BOLL 开口突破（原生 macd_boll.weight）+ 波动上限 TV20%。t 日收盘出决策，**t+1 收盘成交**；空仓按货基 1.40% 计息。
逐日权重来自各变种**原生引擎**，经 `strategies/vol_legs/_top3_bridge.py` 隔离加载，不重写规则。
成本为**研究同尺口径**：单边 10bp（不是该腿原生费率），故绝对数与 `best_by_leg.md` 不可直接对账。
回测先在全样本上算信号、再截预热窗（状态层不会在窗口起点被重置），故数字与研究报告
「先截窗再算信号」略有差异；**相对结论与超越关系不变**。

## 文件

```
v19_hkt_v3/
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
python strategies/vol_legs/v19_hkt_v3/backtest.py
python strategies/vol_legs/v19_hkt_v3/daily/_daily_scan.py
python strategies/vol_legs/v19_hkt_v3/research/_plot_leg.py
python strategies/vol_legs/v19_hkt_v3/research/_plot_leg.py --years 3
```

机械规则输出，非投资建议。
