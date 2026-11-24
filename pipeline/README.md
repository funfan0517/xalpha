# 策略流程框架（Pipeline）

把「公共标的池 → 规则化 → 回测报告 → 适配名单 → 每日操作」固化为五阶段标准流程。
当前已注册策略（**按每日操作板块 1→5 排列**，板块定义见 §3）：
`permanent`（**板块1 总策略**·永久组合 9 项基准〔现金腿 10，含货基·债 30·红利 20·美股 20·金 10·A500 2.5·科创 5·沪深300 2.5〕±50% 倾斜，组合层，不走 backtest/select）、
`vol_legs`（**板块2** 波动资产操作规则·A500/纳指100/标普500/科创50/恒生科技/黄金六腿仓位带 + 回撤阶梯 + 季度再平衡，组合层，不走 backtest/select；**2026-11-23 起撤卡（`card=false`），2026-11-24 起不随每日操作跑** —— 板块2 只留「变种操作一览」，产物仍由 `run_daily_vol_legs.ps1` 刷新）、
`v3_macd_boll`（**板块2 变种**·六腿 **MACD+BOLL 自适应窗口**逐腿进出时点，随 `run_daily_vol_legs.ps1` 第 2 步运行，全腿出信号，不走 backtest/select；2026-10-30 顶替 V1/V4 进入本板块）、
`v1_1_ma20`（**板块2 变种（已退出看板）**·**V1 的默认（唯一）实现**：**MA20 穿越**逐腿进出时点，七腿场外复权净值，`daily.enabled=false`，脚本与产物仍可手跑；2026-11-07 由 `v1_ma20` 下沉为 v1_1，2026-11-10 删除量价子策略 V1-2 后成为 V1 唯一实现）、
`v2_boll_breakout`（**板块2 变种（已退出看板）**·六腿 **BOLL(35,2) 上轨突破**逐腿进出时点，**只有回测入口**，`daily.enabled=false` 不参与每日操作；原 `tech_indicators/v16_boll_breakout`）、
`v4_erp_band`（**板块2 变种（已退出看板）**·股债利差 ERP 通道两带滞回，沪深300 × 10Y 国债 0/100 刻度，2026-11-03 起旧 v4/v8 变种已删除、由原 v10 接任；`daily.enabled=false`，脚本仍可手跑）、
`growth_div_rotation`（**板块3** 成长/红利二元轮动 v0：MA20 缓冲带 + 250 日分位；**2026-11-23 起撤卡（`card=false`），2026-11-24 起不随每日操作跑** —— 板块3 只留 v5，产物仍由 `run_daily_growth_div_rotation.ps1` 刷新）、
`gd_v2`（**板块3 变种**·R 的**绝对水位** 0.35/0.65，一次性整仓切换；**信号层裸基准**，随 `run_daily_growth_div_rotation.ps1` 第 3 步运行）、
`gd_v5`（**板块3 变种**·v3 的 R 水位 + 双侧 MA200 闸门 + **18% 波动率目标**，家族风险调整最优，随同一脚本第 4 步运行）、
`gd_v1`（**板块3 变种（已退出看板）**·R 绝对水位 0.20/0.31 + 5 期分批 + 定投，`daily.enabled=false`，脚本与产物仍可手跑）、
`dividend`（**板块4** 红利 **V4 · RSI14 择时**，5 只专用池；2026-10-26 起每日跑它，不再跑 V0 打分）、
`dividend_v9`（**板块4 变种**·红利 6 只**五因子估值温度**体检表，只给加/减仓额度参考、不调仓位，随 `run_daily_dividend.ps1` 第 2 步运行；2026-10-30 起与 V4 分卡）、
`tech_indicators`（**板块5** 技术指标家族·每日固定 **6 策略 MACD/V3/V6/V7/V9/V10**，全池出信号，不走 backtest/select；v9 = 原 `right_side` 右侧买入、v10 = 原 `lights` 亮灯，2026-10-28 作为变种并入）。
`strategies/tech_indicators/v10_lights/`（原 `lights`）可作为**逐标的**新增策略的模板（参数模型见其 `rule.py`）；v9/v10 展示了「状态机型」（自带 rule/engine/scan + 分级报告）如何挂进家族。**组合层**策略参照 `vol_legs`，**轮动**策略参照 `growth_div_rotation`。

> 变种 v10（原 `lights`）是**参数化策略**：所有行为差异由
> `strategies/tech_indicators/v10_lights/rule.py::ACTIVE` 表达，调参入口
> `python strategies/tech_indicators/v10_lights/backtest.py --set key=value`。
> 并入家族后本 json **不再保留它的参数镜像块**（`_sync_meta.py` 已删除），
> `rule.py::ACTIVE` 是唯一权威源。

## 1. 流程总览

```
 universe ─→ rules ─→ backtest ─→ select ─→ daily
 (公共标的池)  (规则化定义)  (回测+报告)  (分级名单)  (每日信号/操作)
```

| 阶段 | 输入 | 动作 | 输出产物 |
|---|---|---|---|
| 1 universe | 公共标的池 | 展示场外池 + 场内内池 + 当前分级名单 | 摘要（唯一池 `_universe.md`：场外 74 只、场内内池 55 只） |
| 2 rules | `pipeline/strategies.json` | 打印规则化定义（灯评分/阈值/回测口径） | 可读规则表（策略参数唯一权威源） |
| 3 backtest | 场内内池标的日线 | 状态机择时 vs 买入持有（最长十年 2016-09 起） | `strategies/tech_indicators/v10_lights/backtest/_lights_bt.jsonl` → `backtest/_bt_report.md` |
| 4 select | 上一步 jsonl | 按回测分级 A/B/不适合 | `v10_lights/daily/_lights_active.md` + `.json`（**2026-10-28 起作废**，不再驱动信号） |
| 5 daily | 全池 54 只（前四类） | 抓当日(含盘中实时 bar)评分 | `v10_lights/daily/_signal_report.md`、`v10_lights/daily/_signal_dashboard.html` |

> 分层：`data/`=池与数据；`strategies/<name>/`=策略实现与报告（tech_indicators 家族、vol_legs、dividend 等）；`pipeline/`=流程编排层。

### 标的池唯一维护（Single Source of Truth）

`data/_universe.md` 是**唯一**的场内外标的维护入口：

- **增/删/改标的只改这一个文件**；不要在各策略代码里硬编码标的清单；
- **编号按类别留空分段**（2026-09-18 起）：宽基/另类 `1xx`、全球/跨境 QDII `2xx`、A 股行业 `3xx`、策略/商品 `4xx`、主动/量化 `5xx`、债券 `6xx`。**新增标的一律追加到所属段末尾**，因此不会再触发全表重编号、也就不会连带改 `doc/核心轮动投资策略手册.md` 与各策略 README 里的 `#N` 引用。编号只是标识（`inner_rows()['idx']`），**不参与排序** —— 顺序由 md 行序决定；
- `pipeline/universe.py` 负责解析：`inner_rows()/inner_codes()` 派生「有场内对应」的内池（当前 55 只），供亮灯回测/每日扫描使用；`offshore_rows()` 给出全部场外清单；
- 无场内对应的行（主动/债券/QDII 细分等）**只作为场外清单**，不进入场内信号回测与每日推荐；
- 改完 `_universe.md` 后：`python pipeline/universe.py` 自查解析 → 新增场内代码需跑 `python data/_refresh_data.py` 补十年库（`data/_long_klines.json`） → 再按阶段重跑 backtest / select / daily。

### 回测报告统一口径（单笔交易统计）

所有策略的 backtest 报告默认含「单笔交易统计」节，口径在 `pipeline/bt_stats.py` 唯一实现，禁止各策略重复实现：

- 引擎记录单笔为一次持仓周期 `{code, entry_date, exit_date, bars, ret}`（`ret` 含扣交易成本与否由引擎注明）；
- `trade_stats(trades)` 统一产出：交易笔数 / 胜率=盈利笔数÷总笔数 / 平均盈利 / 平均亏损 / 盈亏比=平均盈利÷|平均亏损| / 利润因子=总盈利÷|总亏损| / 最佳、最差单笔；
- 报告用 `section_lines(ts)` 渲染默认 md 节，格式与 A 动量报告一致。

已接入：`strategies/tech_indicators/v10_lights/backtest.py` 每只标的输出 `t_stats` + `trade_log`，同目录 `_reportbt.py` 汇总出「全体单笔合并统计」。**新增策略按 §5 脚手架生成的 `backtest.py` 应复用上述模块**，在引擎里收集单笔后调用 `bt_stats.section_lines(bt_stats.trade_stats(trades))`。

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
全量按唯一池内池数量分批（当前 55 只分 7 批）：首批加 `--fresh`，其余不加，完成后 `--report`。

## 3. 每日运维

**一键跑全部策略**（所有大策略的每日操作放在一起，推荐）：

```powershell
powershell -ExecutionPolicy Bypass -File run_daily_all.ps1
powershell -ExecutionPolicy Bypass -File run_daily_all.ps1 -Only tech_indicators,vol_variants   # 只跑指定上板策略
```

`run_daily_all.ps1` 只遍历**上板策略**（`daily.enabled=true` 且 `daily.card != false`）的 `daily.runner`，**按每日操作板块顺序**（`daily.block` 对应 `blocks` 的序号）依次执行，**同一 runner 去重只跑一次**，**单策略失败不中断**，最后汇总。不出卡的策略（`enabled=false` / `card=false`）**不随每日操作跑**，只在头部打印一行清单；要刷新它们请手动跑对应 `run_daily_*.ps1`。也可逐个走 pipeline：`python pipeline/run_flow.py daily --strategy <name>`（会做前置闸门检查：非 `rotation`/`full_pool` 模式必须先有分级名单）。

| 板块 | 策略 | 每日 runner | 产物（`daily.signal_report`） |
|---|---|---|---|
| 1 | permanent | `run_daily_permanent.ps1` | `strategies/permanent/daily/_permanent_report.md` |
| 2 | vol_variants（出卡 · 持有家族调度权） | `run_daily_vol_legs.ps1` | `strategies/vol_legs/v2_boll_breakout/daily/_v2_boll_daily.md` 等 7 个变种产物 |
| 2 | vol_5y（研究对照卡） | （空，手跑 `research/beat_best_5y/_daily_positions_5y.py`，不并入每日流程） | `strategies/vol_legs/research/beat_best_5y/_daily_positions_5y.json` |
| 2 | vol_legs | （空，`card=false` → 不随每日操作跑；产物由 `run_daily_vol_legs.ps1` 生成，调度权在 vol_variants） | `strategies/vol_legs/daily/_vol_legs_report.md` |
| 2 | v3_macd_boll | （空，由 `run_daily_vol_legs.ps1` 第 2 步带上） | `strategies/vol_legs/v3_macd_boll/daily/_v3_macd_boll_daily.md` |
| 2 | v1_1_ma20 | （空，`daily.enabled=false` → 2026-10-30 起退出默认流程与看板；V1 的默认实现；手跑 `v1_price_volume/v1_1_ma20/daily/_daily_scan.py`） | `strategies/vol_legs/v1_price_volume/v1_1_ma20/daily/_v1_1_ma20_daily.md` |
| 2 | v2_boll_breakout | （空，**无每日脚本**，`daily.enabled=false` → 跳过、不出看板卡片） | —（无每日产物，只有回测 `backtest/_v2_boll_breakout_bt.md`） |
| 2 | v4_erp_band | （空，`daily.enabled=false` → 2026-10-30 起退出默认流程与看板；手跑 `v4_erp_band/daily/_daily_scan.py`） | `strategies/vol_legs/v4_erp_band/daily/_v4_erp_band_daily.md` |
| 3 | gd_v5（出卡 · 持有家族调度权） | `run_daily_growth_div_rotation.ps1` | `strategies/growth_div_rotation/v5_vol_target/daily/_gd_v5_daily.md` · 另见 `daily.chart` 的仓位图（`backtest/_v5_alloc.png`，每日重画并内嵌进卡片） |
| 3 | growth_div_rotation | （空，`card=false` → 不随每日操作跑；产物由 `run_daily_growth_div_rotation.ps1` 生成，调度权在 gd_v5） | `strategies/growth_div_rotation/v0/daily/_signal_report.md` |
| 3 | gd_v2 | （空，由 `run_daily_growth_div_rotation.ps1` 第 3 步带上） | `strategies/growth_div_rotation/v2_absolute_bands/daily/_gd_v2_daily.md` |
| 3 | gd_v1 | （空，`daily.enabled=false` → 2026-11-02 起退出看板；手跑 `v1_ratio_bands/scan.py`） | `strategies/growth_div_rotation/v1_ratio_bands/daily/_gd_v1_daily.md` |
| 4 | dividend | `run_daily_dividend.ps1` | `strategies/dividend/v4_rsi/daily/_v4_signal.md` |
| 4 | dividend_v9 | （空，由 `run_daily_dividend.ps1` 第 2 步带上） | `strategies/dividend/v9_temp5/daily/_v9_signal.md` |
| 5 | tech_indicators | `run_daily_tech_indicators.ps1`（**一条命令跑全家族**：v9 scan+报告 → v10 scan+报告 → 6 策略汇总表） | `strategies/tech_indicators/daily/_daily_recommend.md` |
| 6 | **bond_cash_v7**（板块默认） | `run_daily_bond_cash.ps1`（**一条命令跑全家族 v0~v8 共 9 步**；调度权在 V7，因为它才是出卡的那一个） | `strategies/bond_cash/v7_risk_budget/daily/_v7_daily.md` |
| 6 | bond_cash（v0 基准） | （空，`daily.enabled=false` → 2026-11-21 起转为基准、不出卡；由 runner 第 1 步刷新） | `strategies/bond_cash/daily/_bond_cash_daily.md` |
| 6 | bond_cash_v1 | （空，`daily.enabled=false` → 只在 runner 第 2 步刷新产物供研究，不进看板） | `strategies/bond_cash/v1_four_factor/daily/_v1_daily.md` |
| 6 | bond_cash_v2 | （空，`daily.enabled=false` → 只在 runner 第 3 步刷新产物供研究，不进看板） | `strategies/bond_cash/v2_cross_momentum/daily/_v2_daily.md` |
| 6 | bond_cash_v3 | （空，`daily.enabled=false` → 只在 runner 第 4 步刷新产物供研究，不进看板） | `strategies/bond_cash/v3_carry_trend/daily/_v3_daily.md` |
| 6 | bond_cash_v4 | （空，`daily.enabled=false` → 只在 runner 第 5 步刷新产物供研究，不进看板） | `strategies/bond_cash/v4_stock_hedge/daily/_v4_daily.md` |
| 6 | bond_cash_v5 | （空，`daily.enabled=false` → 只在 runner 第 6 步刷新产物供研究，不进看板） | `strategies/bond_cash/v5_vol_target/daily/_v5_daily.md` |
| 6 | bond_cash_v6 | （空，`daily.enabled=false` → 只在 runner 第 7 步刷新产物供研究，不进看板） | `strategies/bond_cash/v6_dual_spread/daily/_v6_daily.md` |
| 6 | bond_cash_v8 | （空，`daily.enabled=false` → 只在 runner 第 9 步刷新产物供研究，不进看板） | `strategies/bond_cash/v8_duration_ladder/daily/_v8_daily.md` |

### 每日操作板块（看板与执行顺序）

`strategies.json` 顶层 `blocks` 声明板块的**序号 / 标题 / 说明**，每个策略在自己的 `daily.block` 里认领板块 key。
板块里**不维护策略清单** —— 归属由策略侧声明，因此加策略只需填一个字段，不会出现「板块列表与策略表不同步」。
`run_daily_all.ps1` 按板块顺序执行，`daily_digest.py` 按板块分节渲染。

| 板块 | 定位 | 策略 key |
|---|---|---|
| **1 总策略 · 永久投资组合** | 先定总盘子：9 项基准（债 30 / 红利 20 / 纳指100 10 / 标普500 10 / 黄金 10 / 现金 0 / A500 5 / 科创50 10 / 沪深300 5）按单项 ±50% 倾斜，输出「每项配多少」；现金并入各择时腿空仓、不再单独配重（2026-11-23）；后续板块都在这套额度内做动作 | `permanent` |
| **2 波动类资产 · 仓位带 + 逐腿上下车** | 管辖 40pp（A500 5 / 纳指100 10 / 标普500 10 / 科创50 2.5 / 恒生科技 2.5 / 黄金 10）；主策略回答「仓位带怎么摆、今天动多少 pp」，变种回答「每条腿何时突破上车 / 何时下车」 | `vol_variants`（出卡 · 持有 `run_daily_vol_legs.ps1`）、`vol_5y`（研究对照卡，手跑）；`vol_legs`（card=false）、`v1_1_ma20` / `v2_boll_breakout` / `v4_erp_band` 等均不随每日操作跑 |
| **3 风格轮动 · 成长/红利** | 同一份指数缓存、同一个比值 R：v5 = R 水位 + 双侧 MA200 闸门 + 18% 波动率目标（家族风险调整最优，出卡）；v0/v2 只在产物里留档 | `gd_v5`（出卡 · 持有 `run_daily_growth_div_rotation.ps1`）；`growth_div_rotation`（card=false）、`gd_v1` / `gd_v2` 均不随每日操作跑 |
| **4 稳健红利 · V4 RSI14 择时 + V9 估值温度** | 红利底仓的进退节奏（V4：5 只专用池，板块 RSI14 ≥70 清仓 / ≥50 减半，横截面取 RSI 最低 2 只）＋「贵不贵」体检（V9：6 只有估值标的的五因子温度，只给额度参考、不调仓位） | `dividend`、`dividend_v9` |
| **5 技术指标 · 全池 54 只（前四类 · 6 策略并列）** | MACD / V3 / V6 / V7 / V9 右侧 / V10 亮灯 同时对全池每只出信号，看共振 | `tech_indicators` |
| **6 稳健资产 · V7 门派风险预算（默认）** | `data/_universe.md` 第六类（债券 6xx 11 只）+ 第七类（现金 7xx 4 只）合并成「稳健资产」40pp（债腿 30pp + 现金腿 10pp）。**默认策略 = v7**：债腿按四门派等风险贡献（ERC）分配（利率 ≈50% / 信用 ≈45% / 转债 ≈5%）+ 风险贡献分解，月末调仓、平时不动手；现金腿 5pp 货基 + 5pp 存单固定在外。**v0（35% 003376 + 65% 011983 固定中性）保留为基准**。其余变种只作研究：v1 四因子长端占比（35%~70%）、v2 横截面双动量、v3 carry+趋势双票、v4 = v3+Hedge、v5 波动率目标、v6 双利差锚、v8 六腿久期阶梯 —— **全部 `daily.enabled=false`（不随每日操作跑；产物仍由 `run_daily_bond_cash.ps1` 刷新）**。全谱系 15 只只监控、5 只动手 | `bond_cash_v7`；`bond_cash`(v0 基准) `bond_cash_v1` `bond_cash_v2` `bond_cash_v3` `bond_cash_v4` `bond_cash_v5` `bond_cash_v6` `bond_cash_v8` 均停用 |

- **新增策略默认 `daily.enabled=false`：未经用户明确同意不得进每日操作报告，也不随每日操作跑**（2026-11-24 规则）。上板要改 `enabled: true` 并在 `enabled_note` 留下「YYYY-MM-DD 谁同意」；`python pipeline/check_daily_config.py` 的 E3/E4/E5 会卡住漏写开关、无凭据上板、以及「不出卡却配 runner」。细则见 `AGENTS.md` §9。
- 新增策略必须填 `daily.block`（取值 `core` / `vol` / `style` / `dividend` / `tech` / `bond`）。漏填不会丢卡片 —— 看板会把它归入末尾的「其他（未认领板块）」，但只有上板时才会被调度。
- 板块是**呈现与执行顺序**，不是新的策略边界：同一板块内的策略可以互相引用（如 `v1_1_ma20` 的腿由 `vol_legs/rule.py::LEGS` 派生），但各自产物与解析器独立。

- 每个策略的 runner 都先刷新行情再出信号；缺少数据的标的会在产物里显式标注（陈旧 / 刷新失败 / 盘中未收盘 bar）。
- **定时任务**：已注册定时任务「亮灯策略每日信号」，工作日 **14:05** 执行。⚠ 2026-11-24 起数据层只写已收盘 bar，该任务拿到的是 **A 股 T-1 收盘**（当日 bar 未收盘）；要盘中口径请看预演版 `data/_daily_digest_intraday.html`（板块5 的 54 只 ETF 已换实时价）。
- `tech_indicators` 的 6 个策略对**全池 54 只（`_universe.md` 前四类）**全部出信号，不做 A/B 分级；其中 v9/v10 为状态机型变种，由各自 `scan.py` 先落盘、汇总脚本只读产物（不重复抓数、不搞两套口径）。v10 的原 A 类 8 只分级名单已作废（池扩到 55 只后无意义），文件保留仅作历史参考。
- 新增 runner 时：在 `pipeline/strategies.json` 的 `daily.runner` 填 `.ps1` 文件名（放仓库根，脚本内用 `$PSScriptRoot` 定位，勿写死盘符），**且必须挂在一张出卡的策略上**；`run_daily_all.ps1` 会自动带上它，同一 runner 被多张卡引用时去重只跑一次。
- **停用某个策略的每日跑（不等于删策略）**：在该策略 `daily` 里加 `"enabled": false`（只想撤卡、保留条目则 `card: false`，此时**不能**配 runner）。`run_daily_all.ps1` 与 `daily_digest.py` 都会跳过它，runner 与历史产物保留，仍可手动跑各自的 `run_daily_*.ps1` 或 `python pipeline/run_flow.py daily --strategy <key>`。

### 正式版的数据源与信号时点（2026-11-24 定）

正式版 = `data/_daily_digest.html`（收盘口径、逐日归档）。三条铁律：

1. **只写、只读「已收盘」的 bar**。判据唯一在 `data/_market_clock.py`：A 股（含场内 ETF / QDII / 黄金 `518880`）15:05 · 港股 16:05 · 美股 bar 的日期是**美东交易日**、收盘于**北京次日 05:05**。数据层写库前一律 trim，未收盘的那根**不入库也不上报**（`_idxcache.refresh` / `_refresh_data.refresh` / `refresh_us`）。
2. **`技术指标` 的 14:05 定时任务同样按已收盘 bar 出信号**（A 股 T-1）。要盘中口径请走预演版，不要在正式流程里塞半根 bar。
3. **操作信号** = `八态/规则 → 许可上限 → min(许可, 波动层) → 与昨日对比出动作 pp`；执行恒为 `T 日收盘出信号、T+1 收盘前执行`。

各腿的**数据日与运行时刻**（`T` = 运行日）：

| 运行时刻 | A 股三腿 + 黄金 | 恒生科技 | 纳指100 / 标普500 |
|---|---|---|---|
| **14:35（用户常规）** | **T-1 收盘** | **T-1 收盘** | **T 日 05:00 —— 今天凌晨收盘** |
| 15:05 | T 日收盘 | T-1 收盘 | T 日 05:00 |
| 16:05 之后 | T 日收盘 | T 日收盘 | T 日 05:00 |

- **美股两腿的 `11-23` 就是北京时间 `11-24` 凌晨那根**（美东 16:00 收盘）。产物里 `as_of` / 「数据日」= **数据日**（不是运行日），逐腿日期见 `_v12_daily.json::data_dates` 与报告头的「数据日」行。**不存在标签为北京当日日期的美股 bar**。
- **14:35 是唯一能同时落两笔的窗口**：A 股/港股腿用 T-1 信号（今天 15:00 前下单），美股腿用今天凌晨的收盘（场外 QDII 15:00 截止）。15:05 之后跑，美股那笔当天赶不上。
- 14:35 跑正式版**不是「数据没刷新」**：那正是「A 股/港股 T-1 收盘 + 美股今天凌晨收盘」的设计口径。旧实现靠「往共享库写当日半根 bar」来近似盘中，2026-11-24 起废除（半根会让 `rvol` 失真；港股 15:05~16:00 窗口还会写进 15:0x 快照，实测 11-23 库 3511.35 vs 收盘 3508.00）。
- **预演版口径（2026-11-24 接入）**：`data/_daily_digest_intraday.html` 按「**A股/港股取 T+0 盘中数据、美股仍用 T+0 凌晨收盘**」出数。机制只有一处：`strategies/vol_legs/_idxcache.py::frame` 出口按环境变量 `XALPHA_INTRADAY_BARS` 把今日合成 bar 接到已收盘序列末尾（价格 = `get_rt` 的**指数实时点位**，黄金 `518880` 实时价；量能 = 当日累计量 ÷ 已过交易分钟占比）。标注三级：🟢 盘中价入信号 / 🟠 盘中价 + 量能折算（含 V12 强弱档，可能翻转）/ 🟡 无盘中源。**预演与正式版某个操作不一致时，卡片顶部出红色加粗提示条**（`entry["diff"]`，只有预演版会填）。美股两腿的正式版产物若已是今天凌晨那根收盘就直接沿用（标「数据与正式版同值」），产物落后则改为重算。

**跨策略汇总看板**（一页看「每天 × 每个策略」的操作）：

```powershell
python pipeline/daily_digest.py                 # 归档今天 + 重绘看板（run_daily_all.ps1 已自动调用）
python pipeline/daily_digest.py --html-only     # 只按已有历史重绘
python pipeline/daily_digest.py --date 2026-10-21   # 指定归档日（内容仍取当前产物，只适合当天补跑）
```

- 产物：历史库 `data/_daily_digest.json`、看板 `data/_daily_digest.html`（单文件、无外链，双击可看；支持日期切换 / 搜索 / 「只看有动作」）。
- **双版本必须同步**（`AGENTS.md` §10）：正式版 `data/_daily_digest.html`（收盘口径，归档）与预演版 `data/_daily_digest_intraday.html`（`intraday_preview.py`，把最新盘中快照当收盘价重算、不写数据文件）。两版共用 `_HTML_TMPL` 与 `_blocks()`，所以改渲染 / 列 / 板块文案一处两版同源 —— 但**改完正式版必须再跑一次 `python pipeline/intraday_preview.py`**，否则预演版还是旧 HTML。`--html-only` 只重绘正式版，不算同步。
- **卡片可带图**：策略在 `daily.chart` 填产物图的**仓库相对路径**，看板就在该卡片的表格下方内嵌显示（带图卡片默认展开）。图片按原产物路径引用、不复制不进历史库 —— 图由策略自己的脚本每日重画（当前只有 `gd_v5` 用：`backtest/_v5_alloc.png`，由 `v5_vol_target/scan.py` 每日重画）。HTML 在 `data/` 下，渲染时用 `payload.root` 换算相对路径，换机器/换盘符仍能打开。
- 看板按 **每日操作板块 1→5** 分节（见上文「每日操作板块」），每节标题旁给出该板块的买/卖/观察计数与策略数；老归档无需重建 —— 板块定义每次渲染都从 `strategies.json` 读，按策略 key 重新归组。
- 解析口径：优先读结构化产物（json / jsonl）；`tech_indicators` 只落 markdown，由脚本解析其自产表格（**列宽按 `daily.strategies` 动态取**，增删策略只改 json）；`v1_1_ma20` / `v3_macd_boll` 读各自的 `_*_daily.json`（`as_of` / `summary` / `legs`，v3 的 leg 多带 `dif`/`dea`/`er`/`window`/`dist_kind`）；`permanent` 基准读 `_permanent_signal.json::base`、资产名/场外代码/该腿最优策略读 `daily_digest.py::_PERM_BEST`、各腿仓位读该腿策略自己的 `daily/_*_daily.json`（json 里只有 `bond/divlv/...` 这类键名，所以名字与代码是常量表而不是产物）。单个策略解析失败只影响本卡片，不中断汇总。
- **历史从首次归档起累积**：各策略产物都是当日覆盖写，此前的日期无法回溯。
- 新增策略时在 `pipeline/daily_digest.py::PARSERS` 补一个解析器，否则该卡片显示「未支持」。

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

说明：规则实现默认放 `strategies/<name>/`。`lights`（现 `tech_indicators/v10_lights/`）的演进：共振策略于 2026-09-10 重构为**单一参数化实现**，参数集中在 `rule.py::ACTIVE`；2026-10-28 作为变种 v10 并入 `tech_indicators`，池统一为全池 55 只。

> **三类策略的流程差异**（重要）：
>
> - **逐标的型**（`dividend`）：每只标的一次独立回测 → `flow_select.py` 按 A/B/不适合分级 → `daily` 只推 A 名单。走 `run_flow backtest/select`。`daily` 闸门要求存在分级名单。
> - **组合层/轮动型**（`growth_div_rotation`、`permanent`、`vol_legs`，`pipeline_mode = "rotation"`）：回测是**整个组合的净值**（横截面选标的 + 权重分配 + 调仓），逐标的 A/B 分级不适用。**直接运行 `strategies/<name>/backtest.py`**，不走 `run_flow backtest/select`；`daily` 闸门按 `rotation` 跳过。
> - **全池固定策略集型**（`tech_indicators`，`pipeline_mode = "full_pool"`）：回测是**一次跑完全池 × 全家族**（`strategies/tech_indicators/compare.py`，不接受 `--codes` 分批），每日对全池每只标的都出信号、不逐码筛选标的（阈值只由回测网格取优）。`daily` 闸门按 `full_pool` 跳过。
> - **家族变种型**（`tech_indicators` 的 v9 右侧买入 / v10 亮灯）：**不再单独注册**，随家族每日一起跑。两者都是状态机型（自带 `rule.py`/`engine.py`/`scan.py`），因此**不进入** `compare.py` 的单信号横截面表（那套引擎按 `(close, 指标)` 签名重算，状态机需要 OHLCV 与门槛判定，口径不可比）；自己的回测/报告/看板产物随目录保留。
> - **策略变种型**（`v1_1_ma20`，`pipeline_mode = "full_pool"`）：`vol_legs` 的**第 1 号变种 V1**——2026-11-07 由 `v1_ma20` 改组为 `v1_price_volume/`，原内分两条子策略（V1-1 MA20 穿越 · 纯价基线 / V1-2 20/60/250 量价趋势跟随 · 场内 ETF 量价）；**2026-11-10 按用户要求 `v1_1_ma20` 成为 V1 的默认（唯一）实现，同日删除 V1-2**（与同曝光买入持有差 -0.16pp）。池直接继承主策略 `strategies/vol_legs/rule.py::LEGS`（七腿），不硬编码，增删腿自动跟随；回测一次跑完七腿、不接受 `--codes` 分批；`daily` 闸门按 `full_pool` 跳过；`daily.runner` 留空，只影响 `run_daily_all` 的脚本调度，不影响 `daily_digest` 出卡片。**为什么独立登记而不并入 `vol_legs` 卡片**：它回答的是「逐腿进出时点」，与主策略的「组合层仓位带」是两套动作，混在一张表里会被误读成同一层决策。

## 6. 已知边界与同步点

- 主力灯/热度灯在历史与盘中无「主力资金/换手率」快照时降级为量价代理（回测与盘中均注明）。
- 盘中(14:05)信号用当日未收盘 bar，收阳/量比可能随尾盘变化，操作留缓冲。
- 变种 v10（原 `lights`）的资金/换手维度走**量价代理**（历史无主力资金与换手率明细）：**主力强度**→CMF 三日净流 × 标定系数（`cap_proxy_scale`，与真实值同号，幅度经 mx 快照 OLS 标定）、**换手分位**→成交额 60 日分位代理（份额近似恒定时等价）。回测结论**不代表真实资金口径下的表现**；真实值要到实盘扫描才可用（`mx_snapshot_*.json` 每日落盘，扫描报告第 5 节做同号交叉验证）。
- 变种 v10 为**逐标的独立状态机 + 每日评估**（信号变化 → 次日开盘执行）。单只标的的信号满足率不高（门槛层 × 得分层双重收敛），故平均持仓占比低。判读时须同时看 `_bt_report.md` 的逐只明细：**持仓占比决定收益上限，择时边际决定暴露是否用在刀刃上**。当前生效配置 `c7`（池子 2026-10-28 起 = **全池 55 只**；历史口径 47 只是并族前的池；**参数本体仍在 23 只行业池上选出、未重调**）的择时边际 **+5.0bp/日**（27/47 为正），持仓 15.6%，年化 +4.8% / 超额 -0.3%；23 行业基线为 +14.0bp / 16-23 / 14.8% / +6.9% / +3.4%。
- **调参入口**：`python strategies/tech_indicators/v10_lights/backtest.py --set key=value`（可覆盖任意参数，如 `--set enter_min=4,lights={"trend":["ma_short_adx",1]}`）；批量搜参与 walk-forward 见同目录 `research/_tune.py` / `research/_walkforward.py`。
- 变种 v10 每日扫描报告（`strategies/tech_indicators/v10_lights/daily/_signal_report.md`）含逐标的的**门槛层逐条实际值 vs 阈值**与**各维灯得分 + 触发依据**；盘中运行时换手分位等会因未收盘 bar 偏低，以尾盘复核为准。
- 产物均非投资建议；实盘前须人工复核。
