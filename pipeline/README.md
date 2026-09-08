# 策略流程框架（Pipeline）

把「公共标的池 → 规则化 → 回测报告 → 适配名单 → 每日操作」固化为五阶段标准流程。
当前已注册策略：`four_lights`（四灯共振·场内量价代理版），可作为新增策略的模板。

## 1. 流程总览

```
 universe ─→ rules ─→ backtest ─→ select ─→ daily
 (公共标的池)  (规则化定义)  (回测+报告)  (分级名单)  (每日信号/操作)
```

| 阶段 | 输入 | 动作 | 输出产物 |
|---|---|---|---|
| 1 universe | 公共标的池 | 展示场外池 + 场内唯一映射 + 当前分级名单 | 摘要（含场外 52 只、场内映射 35 只） |
| 2 rules | `pipeline/strategies.json` | 打印规则化定义（灯评分/阈值/回测口径） | 可读规则表（策略参数唯一权威源） |
| 3 backtest | 场内映射标的日线 | 状态机择时 vs 买入持有（近 5 年） | `data/_bt_out.jsonl` → `strategies/four_lights/_bt_report.md`、`strategies/four_lights/_bt_dashboard.html` |
| 4 select | `data/_bt_out.jsonl` | 按回测分级 A/B/不适合 | `data/_universe_4d_active.md` + `.json` |
| 5 daily | 分级名单 A | 抓当日(含盘中实时 bar)评分 | `strategies/four_lights/_inner_report.md`、`strategies/four_lights/_4d_dashboard.html` |

> 分层：`data/`=池与数据；`strategies/<name>/`=策略实现与报告（four_lights、momentum_rotation）；`pipeline/`=流程编排层。

### 回测报告统一口径（单笔交易统计）

所有策略的 backtest 报告默认含「单笔交易统计」节，口径在 `pipeline/bt_stats.py` 唯一实现，禁止各策略重复实现：

- 引擎记录单笔为一次持仓周期 `{code, entry_date, exit_date, bars, ret}`（`ret` 含扣交易成本与否由引擎注明）；
- `trade_stats(trades)` 统一产出：交易笔数 / 胜率=盈利笔数÷总笔数 / 平均盈利 / 平均亏损 / 盈亏比=平均盈利÷|平均亏损| / 利润因子=总盈利÷|总亏损| / 最佳、最差单笔；
- 报告用 `section_lines(ts)` 渲染默认 md 节，格式与 A 动量报告一致。

已接入：`strategies/momentum_rotation/backtest.py`（本地十年库，直接可用）；`strategies/four_lights/_backtest.py` 每只标的输出 `t_stats` + `trade_log`，`strategies/four_lights/_reportbt.py` 汇总出「全体单笔合并统计」。**新增策略按 §5 脚手架生成的 `backtest.py` 应复用上述模块**，在引擎里收集单笔后调用 `bt_stats.section_lines(bt_stats.trade_stats(trades))`。

## 2. 使用命令（均在 `g:/xalpha` 下）

```powershell
python pipeline/run_flow.py list                        # 查看策略与阶段
python pipeline/run_flow.py universe                    # 阶段1 标的池摘要
python pipeline/run_flow.py rules                       # 阶段2 规则化打印
python pipeline/run_flow.py backtest --fresh --codes "512800,588000"   # 阶段3 回测(分批/清空重建)
python pipeline/run_flow.py backtest --report           # 仅重生成回测报告
python pipeline/run_flow.py select                      # 阶段4 自动生成 A/B 名单
python pipeline/run_flow.py daily                       # 阶段5 当日信号(等同 run_daily_4d.ps1)
```

阶段 3 说明：引擎一次执行一个 Python 进程，**建议分批传入 codes**（如每批 ≤9 只）避免单命令过长；
全量 35 只分 4 批：首批加 `--fresh`，其余不加，完成后 `--report`。

## 3. 每日运维

- 自动：已注册定时任务「四灯A类每日信号」，工作日 **14:05** 执行（盘中实时 bar，15:00 前场外申赎仍按当日净值）。
- 手动：双击 `run_daily_4d.bat`，或 `python pipeline/run_flow.py daily`。
- 信号只覆盖 A 类 6 只（`515230/588000/562500/512200/513180/516160`），A 类之外不做四灯推送。

## 4. 分级口径（select 判定，样本 ≥ 3 年）

| 级 | 条件 | 用途 |
|---|---|---|
| A | 策略年化 > 0 且 超额(策略−基准) > 0 | 用该策略主推 / 每日推荐 |
| B | 超额 > 0 且 策略年化 ≤ 0 | 防守参考，不主动加仓 |
| 不适合 | 其余 | 买入持有/定投替代，不推该策略信号 |

## 5. 如何新增一个策略

一键脚手架：

```powershell
python pipeline/run_flow.py scaffold --name <strategy_name>
```

会注册 `pipeline/strategies.json` 配置块，并生成 `strategies/<name>/`（`README.md`、`rule.py` 评分骨架、`backtest.py` 回测骨架）。

之后的标准接入流程（用户提供策略文档后按此执行）：

1. **解析文档** → 抽取：标的池范围、信号维度/指标、判定阈值、交易/止损规则、建议回测口径。
2. **数据可达性核对** → 文档所需字段 vs `get_daily`/快照可得性；缺口显式列出并与用户确认代理/降级（参照四灯主力灯/热度灯的量价代理经验）。
3. **规则化** → 在 `strategies/<name>/rule.py` 落地评分；同步填 `strategies.json` 的 `rules/thresholds/backtest/daily.codes`。
4. **回测** → `python pipeline/run_flow.py backtest --strategy <name> --codes ...` 分批 + `--report`。
5. **名单** → `python pipeline/run_flow.py select --strategy <name>`（A/B/不适合自动分级）。
6. **每日** → 对 A 名单出信号；确认后创建 daily runner 与 automation（参照 `run_daily_4d.ps1`）。
7. 各阶段产物与门槛复用同一 `run_flow.py`，`--strategy` 切换即可。

说明：规则实现默认放 `strategies/<name>/`；four_lights 第一版（原 `4d/`）已于 2026-09-08 迁入 `strategies/four_lights/`。

## 6. 已知边界与同步点

- 主力灯/热度灯在历史与盘中无「主力资金/换手率」快照时降级为量价代理（回测与盘中均注明）。
- 盘中(14:05)信号用当日未收盘 bar，收阳/量比可能随尾盘变化，操作留缓冲。
- A 类 codes 变更时需**同步两处**：`pipeline/strategies.json.daily.codes` 与 `run_daily_4d.ps1`（或改为读取 `data/_universe_4d_active.json`）。
- 产物均非投资建议；实盘前须人工复核。
