# 成长/红利风格轮动（Growth/Dividend Style Rotation）

> 方法论：每日收盘后算「成长指数 ÷ 中证红利」风格比值 R → 算均线(20/30)与 ±缓冲带 → 算 250 日分位 Q → 按缓冲带 + 分位双信号做「满仓成长 / 红利(现金)」二元切换，次日开盘执行。
> 默认成长 = 科创50（000688），红利 = 中证红利（000922）；创业板指（399006）为备选分子（`--alt` 切回）。
> 集成日期：2026-09-12 · 2026-10-30 默认成长由创业板指切换为科创50（见 `note`）· 流水线阶段：已注册 `pipeline/strategies.json`，可跑回测 / 每日信号。
> 变种：**v1「绝对阈值 + 5 期分批 + 定投」**见 `../v1_ratio_bands/`（2026-10-29 落地，与本策略的规则同源、形态不同；
> 两者回测对比见 `../_v1_vs_v0_report.md`，由家族根 `../compare.py` 生成）。本 README 描述的是 **v0**（缓冲带 + 分位）。

## 1. 信号与执行通道

| 角色 | 指数（信号，算 R） | 场外执行（申赎） |
| --- | --- | --- |
| 成长（默认） | 科创50 `000688` | `011609` 易方达上证科创板50ETF联接C（唯一池 #109） |
| 成长（备选） | 创业板指 `399006` | `011362` 易方达创业板ETF联接C |
| 红利（固定） | 中证红利 `000922` | `012644` 招商中证红利ETF联接C（唯一池 #403） |

- 信号用**指数日线收盘**直接算风格比值 R（价格口径，未含分红再投与基金跟踪误差）；执行走场外联接基金。
- 缓存起点 **2020-01-02**（科创50 上市日，故本策略回测窗口为 2020 起，远短于手册的 2010-06 起）；`data.py --refresh` 会重抓全量。
- ⚠ 指数代码的交易所归属**不能用股票规则判断**：沪市指数多为 `000xxx`（中证红利 000922、科创50 000688），
  深市指数为 `399xxx`/`159xxx`。2026-10-29 修掉了 `index_prefix()` 按首位数字判交易所的 bug ——
  该 bug 曾让 000922/000688 取到同号**个股**（佳电股份 / 国城矿业）的股价，静默污染整条回测。
- 切回 `创业板指` 只需在回测/信号加 `--alt`（分子换回 `399006`），其余逻辑不变。
- 红利侧 `012644` 与核心轮动/动量轮动共用同一只基金；成长侧联接为方法论固有标的，随指数切换。

## 2. 当日指标计算（盘后）

1. 风格比值：`Rₜ = 成长指数收盘 ÷ 中证红利收盘`
2. 均线信号：`MA20ₜ = R 近 20 日均值`；`上轨 = MA20ₜ×(1+1%)`，`下轨 = MA20ₜ×(1−1%)`
3. 分位信号：`Qₜ = Rₜ 在过去 250 个 R 中 ≤ 当前值的比例`；`Q<0.3` 成长低估，`Q>0.7` 成长高估
4. 辅助：`MA30ₜ = R 近 30 日均值`

## 3. 交易规则（次日开盘执行，无前视）

- 当前持有**红利/现金**：若 `R₋₁ > 上轨 且 Q₋₁ < 0.8` → 满仓成长；否则继续持有红利/现金
- 当前持有**成长**：若 `R₋₁ < 下轨 或 Q₋₁ > 0.9` → 切红利；否则继续持有成长
- 中间地带（`下轨 < R < 上轨 且 0.3 < Q < 0.7`）：不交易，留原仓，减少来回打脸
- 月频上限：当月切换已 ≥ 4 次则暂停下一次（防摩擦）

> 决策用 `R₋₁`（最新可得收盘），执行用次日开盘价，**无前视**；回测与每日信号共用同一份本地数据。

## 4. 数据可达性（实测逻辑）

| 指标 | 通道 | 状态 |
| --- | --- | --- |
| 创业板指 / 中证红利 / 科创50 日线 | `xa.get_daily(SH/SZ+code)`（雪球） | ✅ 自动（`data.py` 落本地缓存） |
| 风格比值 R / 均线 / 缓冲带 / 分位 | `factors.compute_signals`（纯函数） | ✅ 自动 |
| 执行净值确认 | 场外联接基金净值（人工/其他策略通道） | ⚠️ 报告给出代码，成交以实际净值为准 |

缓存落 `strategies/growth_div_rotation/v0/data/_gd_index_klines.json`（**策略类**行情缓存，随 v0 目录移动；v1 只读不写）。

## 5. 运行方式

```powershell
# 0) 首次/定期刷新指数行情缓存（十年库）
python strategies/growth_div_rotation/v0/data.py --refresh
#    日常增量（盘后补最新 bar）
python strategies/growth_div_rotation/v0/data.py

# 1) 回测（默认科创50；--alt 切回创业板指）
python strategies/growth_div_rotation/v0/backtest.py [--alt] [--refresh]
#    或经流水线
python pipeline/run_flow.py backtest --strategy growth_div_rotation --codes GD_ROT
python pipeline/run_flow.py backtest --strategy growth_div_rotation --report

# 2) 每日信号（盘后运行，决策次日开盘）
python strategies/growth_div_rotation/v0/scan.py [--alt]
#    或经流水线
python pipeline/run_flow.py daily --strategy growth_div_rotation
#    或直接双击
powershell -ExecutionPolicy Bypass -File run_daily_growth_div_rotation.ps1

# 3) 变种 v1（绝对阈值 + 5 期分批 + 定投）回测 + 与 v0 对比（家族根入口）
python strategies/growth_div_rotation/compare.py [--rerun-v0]
#    报告 ../_v1_vs_v0_report.md（会自动重跑 v0 保证同窗同数据）
```

产物（按职能分子目录，见 `AGENTS.md §8.4`）：
- `v0/backtest/_bt_report.md` · `_gd_bt.json` · `_gd_bt.jsonl` · `_gd_nav.png` —— 回测报告/明细/摘要/净值图
- `v0/daily/_signal_report.md` · `_gd_signal.json` —— 每日信号报告/机器可读快照
- `v0/data/_gd_index_klines.json` —— 指数行情缓存（策略类，**v0 与 v1 共享**，只由 `v0/data.py` 维护）
- `../v1_ratio_bands/` —— v1 变种（自带 `rule.py`/`engine.py`/`data.py`/`engine.py` 与 `backtest/` 产物）
- `../_v1_vs_v0_report.md` / `../_v1_vs_v0.png` —— 跨变种对比报告与净值图（由 `../compare.py` 生成）
- `research/` —— 调优记录（待补）

## 6. 参数权威源

全部阈值集中在 `rule.py`：`MA_FAST=20` / `MA_SLOW=30` / `QUANTILE_WINDOW=250` / `BUFFER=±1%` / `ENTER_Q_MAX=0.8` / `EXIT_Q_MIN=0.9` / `Q_LOW=0.3` / `Q_HIGH=0.7` / `MAX_TRADES_PER_MONTH=4` / `FEE=0.03%`。引擎/信号层禁止内联常量，微调只改 `rule.py`。

## 7. 已知边界与风险

- 回测用指数价格收益，未含分红再投、基金跟踪误差与申赎时滞（QDII/联接）；实际收益会略低于回测。
- 科创50 波动特征与创业板指不同，缓冲带建议 0.5%~1%（见 `rule.BUFFER_RANGE`）。
- 历史回测 ≠ 未来收益；最大回撤仍可达 50%+，**单策略不宜满仓**。建议与红利/债券做 50/50 仓位中枢，仅用部分资金轮动。
- 状态机起始仓固定为「红利/现金」（防御侧）；可改为起始成长做对照实验（改 `engine.run_strategy` 初值）。

## 8. 调参指引

- 震荡大（R 频繁穿均线）：加宽缓冲带到 1.5% 或改用 30 日均线（`MA_FAST`/`MA_SLOW`）
- 交易成本高（佣金+滑点 > 0.3%）：提高缓冲带、限制月频 ≤2 次（`MAX_TRADES_PER_MONTH`）
- 分位窗口固定 250 日；入场/离场分位阈值（`ENTER_Q_MAX`/`EXIT_Q_MIN`）可在 0.8/0.9 附近微调以收紧/放松切换
