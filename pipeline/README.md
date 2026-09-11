# 策略流程框架（Pipeline）

把「公共标的池 → 规则化 → 回测报告 → 适配名单 → 每日操作」固化为五阶段标准流程。
当前已注册策略：`lights`（亮灯策略·共振策略重构版·全参数化）、
`momentum_rotation`（全球相对动量轮动）、`ema_cross`（双均线趋势 EMA12/26·金叉死叉）；
`core_rotation`（六类资产动态配置·场外6标的，组合层，不走 backtest/select）。
`lights` 可作为**逐标的**新增策略的模板（其参数模型见 `strategies/lights/rule.py`）；**组合层/轮动**策略参照 `momentum_rotation`。

> `lights` 是**参数化策略**：所有行为差异由 `strategies/lights/rule.py::ACTIVE` 表达，
> 调参入口 `backtest.py --set key=value`；改完跑 `strategies/lights/_sync_meta.py`
> 同步本 json 的镜像块。

## 1. 流程总览

```
 universe ─→ rules ─→ backtest ─→ select ─→ daily
 (公共标的池)  (规则化定义)  (回测+报告)  (分级名单)  (每日信号/操作)
```

| 阶段 | 输入 | 动作 | 输出产物 |
|---|---|---|---|
| 1 universe | 公共标的池 | 展示场外池 + 场内内池 + 当前分级名单 | 摘要（唯一池 `_universe.md`：场外 56 只、场内内池 37 只） |
| 2 rules | `pipeline/strategies.json` | 打印规则化定义（灯评分/阈值/回测口径） | 可读规则表（策略参数唯一权威源） |
| 3 backtest | 场内内池标的日线 | 状态机择时 vs 买入持有（最长十年 2016-09 起） | `strategies/lights/_lights_bt.jsonl` → `strategies/lights/_bt_report.md`、`strategies/lights/_bt_dashboard.html` |
| 4 select | `strategies/lights/_lights_bt.jsonl` | 按回测分级 A/B/不适合 | `strategies/lights/_lights_active.md` + `.json` |
| 5 daily | 分级名单 A | 抓当日(含盘中实时 bar)评分 | `strategies/lights/_signal_report.md`、`strategies/lights/_signal_dashboard.html` |

> 分层：`data/`=池与数据；`strategies/<name>/`=策略实现与报告（lights、momentum_rotation）；`pipeline/`=流程编排层。

### 标的池唯一维护（Single Source of Truth）

`data/_universe.md` 是**唯一**的场内外标的维护入口：

- **增/删/改标的只改这一个文件**；不要在各策略代码里硬编码标的清单；
- `pipeline/universe.py` 负责解析：`inner_rows()/inner_codes()` 派生「有场内对应」的内池（当前 37 只），供亮灯回测/每日扫描与动量十年库回测共同使用；`offshore_rows()` 给出全部场外清单；
- 无场内对应的行（主动/债券/QDII 细分等）**只作为场外清单**，不进入场内信号回测与每日推荐；
- 改完 `_universe.md` 后：`python pipeline/universe.py` 自查解析 → 新增场内代码需 `strategies/momentum_rotation/fetch.py <新代码>` 补十年库 → 再按阶段重跑 backtest / select / daily。

### 回测报告统一口径（单笔交易统计）

所有策略的 backtest 报告默认含「单笔交易统计」节，口径在 `pipeline/bt_stats.py` 唯一实现，禁止各策略重复实现：

- 引擎记录单笔为一次持仓周期 `{code, entry_date, exit_date, bars, ret}`（`ret` 含扣交易成本与否由引擎注明）；
- `trade_stats(trades)` 统一产出：交易笔数 / 胜率=盈利笔数÷总笔数 / 平均盈利 / 平均亏损 / 盈亏比=平均盈利÷|平均亏损| / 利润因子=总盈利÷|总亏损| / 最佳、最差单笔；
- 报告用 `section_lines(ts)` 渲染默认 md 节，格式与 A 动量报告一致。

已接入：`strategies/momentum_rotation/backtest.py`（本地十年库，直接可用）；`strategies/lights/backtest.py` 每只标的输出 `t_stats` + `trade_log`，`strategies/lights/_reportbt.py` 汇总出「全体单笔合并统计」。**新增策略按 §5 脚手架生成的 `backtest.py` 应复用上述模块**，在引擎里收集单笔后调用 `bt_stats.section_lines(bt_stats.trade_stats(trades))`。

## 2. 使用命令（均在**仓库根**下）

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
全量按唯一池内池数量分批（当前 37 只分 7 批）：首批加 `--fresh`，其余不加，完成后 `--report`。

## 3. 每日运维

- 自动：已注册定时任务「亮灯策略每日信号」，工作日 **14:05** 执行（盘中实时 bar，15:00 前场外申赎仍按当日净值）。
- 手动：双击 `run_daily_4d.bat`，或 `python pipeline/run_flow.py daily`。
- 信号只覆盖 A 类 8 只（`588000/515230/512200/562500/512980/512000/513180/161725`），A 类之外不做信号推送。

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
2. **数据可达性核对** → 文档所需字段 vs `get_daily`/快照可得性；缺口显式列出并与用户确认代理/降级（参照亮灯策略主力强度的量价代理经验）。
3. **规则化** → 在 `strategies/<name>/rule.py` 落地评分；同步填 `strategies.json` 的 `rules/thresholds/backtest/daily.codes`。
4. **回测** → `python pipeline/run_flow.py backtest --strategy <name> --codes ...` 分批 + `--report`。
5. **名单** → `python pipeline/run_flow.py select --strategy <name>`（A/B/不适合自动分级）。
6. **每日** → 对 A 名单出信号；确认后创建 daily runner 与 automation（参照 `run_daily_4d.ps1`）。
7. 各阶段产物与门槛复用同一 `run_flow.py`，`--strategy` 切换即可。

说明：规则实现默认放 `strategies/<name>/`。`lights` 的演进：共振策略于 2026-09-10 重构为**单一参数化实现**（`strategies/lights/`），参数集中在 `rule.py::ACTIVE`，此处不再保留多份实现。

> **两类策略的流程差异**（重要）：
>
> - **逐标的型**（`lights`、`ema_cross`）：每只标的一次独立回测 → `flow_select.py` 按 A/B/不适合分级 → `daily` 只推 A 名单。走 `run_flow backtest/select`。
> - **组合层/轮动型**（`momentum_rotation`、`core_rotation`）：回测是**整个组合的净值**（横截面选标的 + 权重分配 + 调仓），逐标的 A/B 分级不适用。**直接运行 `strategies/<name>/backtest.py`**，不走 `run_flow backtest/select`。

## 6. 已知边界与同步点

- 主力灯/热度灯在历史与盘中无「主力资金/换手率」快照时降级为量价代理（回测与盘中均注明）。
- 盘中(14:05)信号用当日未收盘 bar，收阳/量比可能随尾盘变化，操作留缓冲。
- A 类 codes 变更时需**同步两处**：`pipeline/strategies.json.daily.codes` 与 `run_daily_lights.ps1`（或改为读取 `strategies/lights/_lights_active.json`）。
- `lights` 的资金/换手维度走**量价代理**（历史无主力资金与换手率明细）：**主力强度**→CMF 三日净流 × 标定系数（`cap_proxy_scale`，与真实值同号，幅度经 mx 快照 OLS 标定）、**换手分位**→成交额 60 日分位代理（份额近似恒定时等价）。回测结论**不代表真实资金口径下的表现**；真实值要到实盘扫描才可用（`mx_snapshot_*.json` 每日落盘，扫描报告第 5 节做同号交叉验证）。
- `lights` 为**逐标的独立状态机 + 每日评估**（信号变化 → 次日开盘执行）。单只标的的信号满足率不高（门槛层 × 得分层双重收敛），故平均持仓占比低。判读时须同时看 `_bt_report.md` 的逐只明细：**持仓占比决定收益上限，择时边际决定暴露是否用在刀刃上**。当前生效配置 `c7`（池子 = **23 只 A 股行业 ETF**）的择时边际 **+14.0bp/日**（16/23 为正），持仓 14.8%，年化 +6.9% / 超额 +3.4%。
- **调参入口**：`python strategies/lights/backtest.py --set key=value`（可覆盖任意参数，如 `--set enter_min=4,lights={"trend":["ma_short_adx",1]}`）。
- `lights` 每日扫描报告（`strategies/lights/_signal_report.md`）含逐标的的**门槛层逐条实际值 vs 阈值**与**各维灯得分 + 触发依据**；盘中运行时换手分位等会因未收盘 bar 偏低，以尾盘复核为准。
- 产物均非投资建议；实盘前须人工复核。
