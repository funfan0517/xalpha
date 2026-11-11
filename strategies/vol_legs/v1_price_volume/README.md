# V1 · MA20 穿越（七腿逐腿择时）· 波动资产第 1 号变种

`vol_legs`（波动资产操作规则）的**第 1 号变种**。原 `v1_ma20` 于 **2026-11-07** 改组为本目录
「量价家族」（内分 V1-1 纯价 / V1-2 量价）；**2026-11-10 按用户要求删除 V1-2，
`v1_1_ma20/` 即 V1 的默认（也是唯一）实现** —— 目录名保留 `v1_price_volume` 只为不打断
`pipeline/strategies.json`、`research/v1v10_compare/` 与本家族各处已经写死的路径。

一句话定位：**主策略回答「组合层仓位带怎么摆」**（阶梯 / 年线闸门 / 再平衡），
**本变种回答「每条腿什么时候该下车、什么时候再上车」**。两者可叠加，不可替代。

## 1. 唯一的实现：`v1_1_ma20/`

| 项 | 取值 |
|---|---|
| 信号/执行口径 | **场外基金复权净值**（含分红再投与汇率，就是账户上能拿到的收益） |
| 规则 | 净值**上穿 MA20** 买入、**下穿 MA20** 卖出；未穿越则维持原仓位（0 / 1） |
| 仓位 | 0 / 1 全仓（不加仓、不减仓、不止损止盈、无 MA60 牛熊过滤） |
| 成交 | T 日盘后算信号、T+1 净值成交（`pos = sig.shift(1)`） |
| 空仓 | 按货基 `rule.CASH_ANNUAL`（1.40%）逐日计息 |
| 换手 | 13~16 笔/年 |
| 每日 | `daily/_daily_scan.py` → `daily/_v1_1_ma20_daily.{md,json}` |

组合层（七腿 2021-09~2026-11）：**年化 7.79% / 回撤 -9.20% / 夏普 0.83**
（静态带 10.25% / -17.45% / 0.73）—— **只降回撤，不增收益**；
按 A 类真实费率扣费后年化掉到 **0.55%**，**成本吃光全部超额**（逐腿 4/7 跑赢买入持有）。
详细规则、逐腿表与成本章见 [`v1_1_ma20/README.md`](v1_1_ma20/README.md)。

## 2. 为什么删掉 V1-2（量价趋势跟随）

V1-2（`v1_2_ma_trend_pv/`，2026-11-07 新建）用 20/60/250 三条均线 + 量能确认 + ATR/ADX 过滤
+ 2% 风险预算配仓 + 结构性止损，只能跑**场内 ETF 量价**口径。它的回测结论是诚实的负面：

- 组合层 12.8 年 **3.20%/年（扣费）、回撤 -5.13%、夏普 0.59**，同曝光买入持有 3.36% / -3.22% / 0.96
  → **差 -0.16pp**；逐腿「同曝光」后只有 3/7 加分、中位 0.00pp。
- 归因（`research/attrib/_attrib.md`）：**关掉量能门槛年化 +1.34pp、夏普 0.59→0.95**；
  四层持有型过滤器（MA20 → +年线 → +ADX → +量能）的择时超额全为负（-2.51 → -1.14pp）。
  即**量能/ADX 在日线 ETF 上没有正的边际信息，决定结果的是暴露而非信号**。
- 它唯一的优势是换手 1~2 笔/年（V1 是 13~16）、成本拖累 -0.2pp 量级 —— 但这是「少交易」的功劳，
  不是信号的功劳；真要解决成本，用主策略的阶梯/闸门更省事也更高收益。

删除的影响面（已同步处理）：

1. `pipeline/strategies.json` 的 `v1_2_ma_trend_pv` 条目已移除（策略数 19 → 18）；
2. `data/_ohlcv.json`（仓库级，判据「别的策略也会用」）**写入方只剩
   `strategies/tech_indicators/v18_volume_notes/ohlcv.py`**；`v13_quad_9grid/data.py` 只读，
   其注释已改为指向 v18；
3. `v5_trend_gate/research/quad_pv/` 原来 import 本目录的 `ma_trend_vol.prepare()`，
   现改为自带最小指标面板（`_quad_pv.py::prepare()`），该研究可独立重跑；
4. `research/v1v10_compare/` 与 `research/best_by_leg/` 的 V1 列本来就用 `ma20.py`，不受影响。

代码与产物（含 `ma_trend_vol.py` / `ohlcv.py` / `backtest/_v1_2_bt.*` / `research/attrib/`）
见 git 历史；结论摘要保留在本节与本变种 README（`README.md` §9）。

## 3. 共同点（家族级纪律，仍适用于 V1）

- **池**：`rule.LEG_ORDER` 七腿（中证A500 / 沪深300 / 纳指100 / 标普500 / 科创50 / 恒生科技 / 黄金），
  权重按 `rule.LEGS[*]["band"]` 归一化（Σ = 40pp）；增删腿只改 `rule.py`。
- **空仓计息**：未投部分按货基 `rule.CASH_ANNUAL`（1.40%）逐日计息，不假设 0 收益。
- **成交纪律**：T 日收盘算信号、`pos = *.shift(1)` 生效。
- **不做 A/B 分级**：七腿全部出信号。

## 4. 目录

```
v1_price_volume/
├── README.md                    本文件（V1 说明 + V1-2 删除留档）
└── v1_1_ma20/                   **V1 的默认（唯一）实现**：MA20 穿越（场外净值口径）
    ├── ma20.py  backtest.py  README.md
    └── backtest/  daily/  research/
```

> 目录层级比其它变种多一层（`v1_price_volume/<子策略>/`），路径常量要多推一层：
> `_HERE` = 子策略目录，`_FAMILY` = 家族目录，`_VL` = `strategies/vol_legs`。改目录时必须同步。

## 5. 运行

```powershell
# 回测（逐腿 + 组合层，场外净值口径）
python strategies/vol_legs/v1_price_volume/v1_1_ma20/backtest.py
python strategies/vol_legs/v1_price_volume/v1_1_ma20/research/backtest.py

# 每日信号（不在 run_daily_vol_legs.ps1 默认流程里，需要时手跑）
python strategies/vol_legs/v1_price_volume/v1_1_ma20/daily/_daily_scan.py
```

V1 **不在** `run_daily_vol_legs.ps1` 的默认流程里（`daily.enabled=false`，
2026-10-30 用户要求板块 2 去掉 V1 卡片）。

> 机械规则输出，**非投资建议**。
