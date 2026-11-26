# v20 · 黄金 单腿策略：`(V5 + V12)/2 × 波动上限 TV15%`

> **★ 研究超越项（2026-11-26 用户决定）**。规则本体在 [`../v5_gold_score/`](../v5_gold_score/)
> （V5：`factors.py` 参数权威源 + `score.py` 状态机）与 [`../v12_octo_trend/`](../v12_octo_trend/)
> （V12：八态趋势），组合层在 `engine.py`。
>
> 2026-11-26 之前在跑的是 **V5 原样**（黄金 ★1，夏普 1.02 / 回撤 −12.48%）。同日候选池加入
> TV15 / TV10 两档后，`E2×TV` 首次过双闸门：
> - `E2×TV20`：1.06 → 0.98（全历史，未过）
> - **`E2×TV15`：1.06 → 1.06 / 后段 1.55 → 1.77 / 回撤 −12.50% → −8.06%（本次采用）**
> - `E2×TV10`：1.06 → 1.08 / 后段 1.87（研究最优，**未采用**）
>
> 为什么取 TV15 而不是 TV10：TV10 的换手从 5.3 涨到 **57.9 次/年**（10bp 下单边成本就吃掉一半增益），
> 且同一技法在 **XAU 19.5 年**上是反向的（V5×TV10 夏普 0.79→0.72、回撤 −13.9%→−19.3%）。
> TV15 是折中档：保留后段改善（1.55 → 1.77），换手 48.8 次/年，且不依赖「越紧越好」这个方向。

## 规则

```
V5    = clip(10% / 90 日实现波动, 20%, 1) × 实际利率闸门      # 原生状态机，20pp 六档 + 1 档滞后
V12   = 八态趋势的许可上限仓位                                # 原生引擎（黄金腿 TV10 + 死区）
E2    = (V5 + V12) / 2                                       # 跨信号族等权（风险预算 × 趋势）
cap   = min(1, 15% / σ20)                                    # 5 日冷却 + 5pp 死区（v2/v3 的 vol_cap）
仓位  = E2 × cap
成交  = T 日收盘算信号 → T+1 收盘成交
```

- 三档 TV（20 / 15 / 10）都是**事先固定的单参数**，不是网格；取 15 是用户决策，见上方理由。
- V5 与 V12 各自已有一层波动控制（10%/σ90 与 10%/σ20 带死区），`cap` 是**加在融合仓上的快闸**：
  σ20 冲高时立刻压总仓位，不依赖 V5 的 90 日慢窗口。
- 与 v18（科创50 `E2×TV20`）同一套组合层写法，只是 TV 刻度不同。

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
├── engine.py            # LEG / weight() = E2×cap / parts() / detail() / run() / perf()
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

边界：`weight()` 返回的是**决策序列**（t 日收盘做出、t+1 收盘成交），`run()` 内 `shift(1)`
才是「当日收益对应的持仓」—— 与 v14~v19 同语义。日报里的「当前持仓」取 `w[-2]`、
「次日目标」取 `w[-1]`。机械规则输出，非投资建议。
