# v20 · 黄金 单腿策略：V5.1 黄金仓位（风险预算 × 实际利率闸门）

> **当前 ★1**（`best_by_leg.md` §0，2026-11-19 重跑）。规则本体在
> [`../v5_gold_score/`](../v5_gold_score/)（`factors.py` 参数权威源 + `score.py` 状态机）。
>
> 黄金腿 ★1 从 **V6-B**（夏普 0.74 / 回撤 −16.12%）换成 **V5.1**（夏普 **1.02** / 回撤 **−12.33%**）：
> 年化只差 0.20pp（+9.61% vs +9.79%），回撤浅 2.9pp、夏普高 0.28。
> `research/beat_best/_beat_best_top3.md` 里九个「前三名之上的组合/叠加」候选
> **无一通过双闸门**（最好的 MIN2 0.98 < 基线 1.04），所以 v20 直接采用 ★1 本身。

## 规则

```
scale = clip(10% / 120 日实现波动, 20%, 1)          # 风险预算（风险归一化，不是 alpha）
gate  = 1.0 if DFII10(T−1) > 0.5% + 0.15pp           # 实际利率水平闸门（带死区）
      = 0.5 if DFII10(T−1) < 0.5% − 0.15pp
      = 上一状态（死区内不动）
e*    = 量化(clip(scale × gate, 0, 1), 20%)          # 六档 0/20/40/60/80/100%
操作  = |e* − e| ≥ 1 档（20pp）才动，否则持有
成交  = T 日收盘算信号 → T+1 收盘成交
```

与旧口径（V6-B 纯波动层 TV10%/120 日）的区别只有一条：多了**实际利率水平**这一维 ——
闸门不开（实际利率过低、未来更可能上行）时只拿一半风险预算。两者共用同一风险机制，
**不可叠加在同一份额度上**。

成本：研究同尺单边 10bp（V5 自己的产物用场内万 2.5），空仓货基 1.40%。
`best_by_leg.md` 里的 1.02 是 V5 原生费率档，**不要与这里的同尺数字直接对账**。

## 数据依赖（与 v14~v19 不同）

| 数据 | 缓存 | 更新方 |
|---|---|---|
| 黄金价格 `518880` OHLCV | `data/_long_klines.json` / `_idxcache` | 第 1 步 `scan.py --force` |
| 实际利率 FRED `DFII10` | `data/_dfii10.csv` | 每日扫描**过期时自动联网补一次**（失败沿用缓存并标注） |

闸门是慢变量（13 年只切换个位数次），缺一天不影响档位；但**缓存不能长期不更新** ——
`rate_is_stale()` 只容忍 1 天，跑每日流程时在线即可。

## 文件

```
v20_gold_v5/
├── engine.py            # LEG / weight() / detail() / run() / perf()（走 _top3_bridge → v5 原生状态机）
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
python strategies/vol_legs/v20_gold_v5/backtest.py
python strategies/vol_legs/v20_gold_v5/daily/_daily_scan.py          # 联网补 DFII10（可失败降级）
python strategies/vol_legs/v20_gold_v5/research/_plot_leg.py
python strategies/vol_legs/v20_gold_v5/research/_plot_leg.py --years 3
```

边界：`weight()` 返回的是**收盘成交后的仓位**（v5 的 `state` 已含 T+1 成交滞后），
`run()` 内再 `shift(1)` 才是「当日收益对应的持仓」；这与 v14~v19 的「决策序列」语义差一天，
改代码时别混。机械规则输出，非投资建议。
