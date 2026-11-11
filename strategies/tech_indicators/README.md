# tech_indicators · 技术指标五大策略回测

对 `data/_universe.md` **前四类**（宽基/另类 · 全球QDII · A股行业 · 策略/商品）中有场内
对应、且 `data/_indicators_hist.json` 有数据的 **54 只场内 ETF**，分别按 **MA / VOL / MACD / KDJ / BOLL** 五个指标策略做全历史回测。

## 标的池（v1~v15 统一，唯一来源 `data/_universe.md`）

| 类 | 名称 | 场内只数 |
|---|---|---|
| 一 | 宽基指数 / 另类 | 12 |
| 二 | 全球 / 跨境 QDII | 7 |
| 三 | A 股行业主题 | 31 |
| 四 | 策略因子 / 商品 | 4 |
| **合计** | | **54** |

- **不纳入**第五类「主动/量化」（无场内代理）与第六类「债券」（`511260 十年国债ETF`，风险属性与权益择时不同源）。
- 池的唯一实现是 `_pool.py`（引擎 `backtest.py::load()` 与 v9/v10 的 `data.py` 都走它）；
  **改池子只改 `data/_universe.md`**，代码里不放硬编码代码。
- 2026-10-28 之前是内池全部 55 只（含债券那 1 只），当日改为前四类。
- 各变种 README 里的结论数字是**该变种定稿时**那次回测的结果；池口径/数据更新后，以各自 backtest/_*_report.md（以及 _comparison_report.md）的最新数字为准。

> 2026-10-28 起，这五个单指标策略的**规则本体已各自迁入变种目录**（`v11_ma/` ~ `v15_boll/`
> 的 `rule.py`）；本目录的 `backtest.py` 退化为**引擎 + 按名分发**（`signal()` / `build_signals()`）。
> 下表的规则描述与那五个 `rule.py` 一一对应，改规则改 `rule.py`，不要改引擎。

## 策略规则（多头 / 空仓 0-1）

| 策略 | 买入 | 卖出 |
|---|---|---|
| **MA** | 收盘上穿 MA20（生命线）且价 > MA60（牛熊线） | 收盘下穿 MA20 |
| **VOL** | 放量上涨（价涨且量 > 5日均量） | 放量下跌（价跌量增）或缩量上涨 |
| **MACD** | DIF 上穿 DEA（金叉） | DIF 下穿 DEA（死叉） |
| **KDJ** | K 上穿 D 且 K < 20（超卖金叉） | K 下穿 D 且 K > 80（超买死叉） |
| **BOLL** | 收盘触及/跌破下轨（低吸） | 收盘触及/升破上轨 |

## 口径

- 每只标的从其自身可用区间的第 61 个交易日（MA60 预热完成）起；
- 信号当日收盘成交，收盘价对收盘价计收益；信号仅用当日及之前数据（无未来函数）；
- 未计佣金 / 滑点；无风险利率按 0 计；年化按 252 交易日；
- 单笔统计口径统一走 `pipeline/bt_stats.py`。

## 变种目录（v1~v7 · v9~v18）

| 变种 | 目录 | 一句话规则 |
|---|---|---|
| v1 | `v1_kdj_ma10/` | KDJ 超卖金叉后窗口内站稳 MA10 买入 · 跌破 MA10 卖出 |
| v2 | `v2_macd_kdj/` | 零轴上 + 缩量回踩 + KDJ 金叉买入 |
| v3 | `v3_macd_boll/` | 零轴上 + 布林开口放大 + 上穿上轨买入 · 死叉或下穿中轨卖出 |
| v4 | `v4_boll_kdj_macd/` | 布林近下轨 + MACD 金叉 + KDJ 超卖拐头 |
| v5 | `v5_regime_switch/` | 趋势行情用 v3 / 震荡行情用 v4 自动切换 |
| v6 | `v6_score_wf/` | 4 项看多票数打分，票数 ≥4 买 / ≤2 卖 |
| v7 | `v7_style_route/` | 按标的风格自动路由到 BOLL / MACD / v3 / 买入持有 |
| **v9** | `v9_right_side/` | 五票全过（结构止跌 · 结构翻越 · 均线转向 · 零轴上多头 · 放量上涨）= 右侧买点（原 `right_side` 策略） |
| **v10** | `v10_lights/` | 门槛层 6 条 + 7 维灯加权评分 ≥ enter_min 且必备灯达标 = 建仓（原 `lights` 亮灯策略） |
| **v11** | `v11_ma/` | 收盘上穿 MA20 且价 > MA60 买入 · 下穿 MA20 卖出（单指标基线 **MA**） |
| **v12** | `v12_vol/` | 放量上涨买入 · 放量下跌或缩量上涨卖出（单指标基线 **VOL**） |
| **v13** | `v13_macd/` | DIF 金叉买入 · 死叉卖出（单指标基线 **MACD**，每日表 `MACD` 列） |
| **v14** | `v14_kdj/` | K 上穿 D 且 K<20 买入 · K 下穿 D 且 K>80 卖出（单指标基线 **KDJ**） |
| **v15** | `v15_boll/` | 触及/跌破下轨买入 · 触及/升破上轨卖出（单指标基线 **BOLL**） |
| **v16** | `v16_trin/` | **TRIN（阿姆氏指标）市场广度择时**：全市场 TRIN 5 日均 > 1.4 连续 2 日 → 次日开盘买入 510300 · 持有 20 个交易日收盘卖出 · 止损 -8%（状态机型，自带广度库 `data/_breadth_daily.json`） |
| **v17** | `v17_trend_gate/` | **趋势四象限闸门（年线 + 年线斜率）**：`close` 在 MA250 上/下 × MA250 近 20 日朝上/朝下 → 上涨趋势满仓 · 反弹只持有 · 高位回调降档 · 下跌趋势阶梯减半→观察仓；套全池 54 只，输出**带内占比** {1.0/0.5/0.2}（非 0/1，从 `vol_legs/v5_trend_gate` 照搬规则、换 54 只场内 ETF 收盘价口径） |
| **v18** | `v18_volume_notes/` | **成交量操盘笔记（高量锚点 + 3 日观察窗）**：高量日 D0 → 定 S（实体低点）/ R（近 3 个锚点实体高点）→ 窗内按价/量/形态给 观/减/清/上车，输出**带内仓位** {0/0.5/1.0}；只套 7 只量价腿（`588000`/`513180`/`518880` 为上线池），OHLCV 口径、T+1 开盘成交（原 `vol_legs/v13_volume_notes`，2026-11-09 迁入改名 V18） |

> 本家族的每日操作是固定 **6 策略（MACD / V3 / V6 / V7 / V9 / V10）**；v1~v6 为手动入口。
> **v9 / v10 是「状态机型」变种**：自带 `rule.py` / `engine.py` / `scan.py` / 分级报告，需要 OHLCV 与门槛判定，**不进入 `compare.py` 的单信号横截面表**（那套引擎按 `(close, 指标)` 重算，口径不可比）；它们的信号由各自 `scan.py` 先落盘、每日汇总脚本只读产物。
> **v11~v15 是「单指标基线」变种**：五个变种共用 runner `_baseline_bt.py`，各自
> `backtest.py` 只做「载入引擎 → 传入本变种 signal → 出报告」；规则本体在各自 `rule.py`，
> 引擎不再持有任何指标逻辑（`build_signals()` 改为按名分发），全局只有一份实现。
> **v8（仅 MA20 穿越）已于 2026-10-28 迁出本家族** —— 其标的池本就不是「全池」而是几只宽基腿，
> 现归入组合层策略 `vol_legs` 的**第 1 号变种 V1**：`strategies/vol_legs/v1_price_volume/v1_1_ma20/`（七腿、场外复权净值口径，
> 2026-11-07 家族改组时由 `v1_ma20/` 下沉为子策略 V1-1，同日新增的量价子策略 V1-2 已于 2026-11-10 删除，
> 故 **v1_1_ma20 即 V1 的默认实现**）。
> 它 2026-10-30 起退出每日流程与看板（`daily.enabled=false`），需要时手跑 `daily/_daily_scan.py`。
> 因此本目录的横截面对比（`compare.py`）不再包含 v8 一行。
> **v16 原为 BOLL 上轨突破，已于 2026-10-30 迁出本家族** —— 它本来就是 BOLL 的「另一套逻辑」
> （突破做多，与 v15 的反转方向相反），迁出后归入组合层策略 `vol_legs` 作为第三个变种：
> `strategies/vol_legs/v2_boll_breakout/`（六腿场外复权净值口径，规则参数与实现原样保留，
> 只换池；它从来不在 `STRAT` 五基线 / `compare.py` 里，故本目录无删改负担）。
> **v16 槽位已于 2026-11-02 由 TRIN（阿姆氏指标）市场广度择时复用** —— 见 `v16_trin/`：
> 它是**市场级**信号（全市场涨跌家数/成交额 → 一个 TRIN 序列），不是逐只标的的指标，
> 因此同样不进 `compare.py`，也不进每日 6 策略汇总表；每日读数单独出 `v16_trin/daily/_v16_signal.md`。

> **v17（趋势四象限闸门）已于 2026-11-04 新增** —— 见 `v17_trend_gate/`：它是从 `vol_legs/v5_trend_gate`
> **照搬**的规则与分析（年线位置 × 年线斜率 → 四象限 → 带内占比 {1.0/0.5/0.2}），唯一改动是数据源从
> 「七腿场外复权净值」换成「家族 54 只场内 ETF 收盘价」（`data/_long_klines.json` + `_pool.py` / `_universe.md`
> 前四类），无风险利率按家族惯例取 0。它输出的是**仓位档位**而非 0/1 买卖开关，口径与五基线 / 每日 6 策略表
> 不同，故同样**不进 `compare.py`、不进每日 6 策略表**；但已注册进 `pipeline/strategies.json` 的 `daily.block = "tech"`，
> 作为**板块5「技术指标」的第二张策略卡**由 `pipeline/daily_digest.py` 渲染（先跑 `v17_trend_gate/daily/_daily_scan.py` 再跑 digest）。
> 如需纳入单标的减仓/加仓叠加层，手跑 `v17_trend_gate/backtest.py`。

> **v18（成交量操盘笔记）于 2026-11-09 迁入本家族** —— 原 `strategies/vol_legs/v13_volume_notes/`，
> 按用户要求改名 **V18**。它是本家族里**唯一用 OHLCV + 成交量**的变种（判据全在量与影线上），
> 也是唯一沿用「七腿目标带 + 场内成交 + 单边 7bp + 空仓计货基」口径的变种：
> 腿表/成本冻结在 `v18_volume_notes/legs.py`、绩效函数冻结在 `metrics.py`（不反向 import vol_legs）。
> 行情库是**仓库级 `data/_ohlcv.json`**。原来的另一个写入方
> `vol_legs/v1_2_ma_trend_pv/ohlcv.py` 已随 V1-2 于 2026-11-10 删除，
> **现在唯一的写入方是本目录的 `v18_volume_notes/ohlcv.py`**（`vol_legs/v13_quad_9grid` 只读）；
> 写入方都在 15:05 前裁掉当日半成品 bar。
> **回测结论：不上线** —— 上线池三腿年化 +1.83% vs 同曝光买入持有 +7.39%，回撤持平，
> 即「只降仓位、不产生超额」；故**不进 `compare.py`、不进每日 6 策略表、未注册 `pipeline/strategies.json`**，
> 需要时手跑 `v18_volume_notes/backtest.py` 与 `daily/_daily_scan.py`。

## 运行

```powershell
python strategies/tech_indicators/backtest.py        # 五大基线并排对照（根报告）
python strategies/tech_indicators/v11_ma/backtest.py # 任一单指标变种单独跑（v11~v15 同构）
python strategies/tech_indicators/compare.py         # 全家族横截面（基线五 + v1~v7）
python strategies/tech_indicators/v16_trin/backtest.py   # TRIN 市场广度择时（需先建 data/_breadth_daily.json）
python data/_refresh_breadth.py --full                   # 上一条的前置：回补全市场广度库
python strategies/tech_indicators/v17_trend_gate/backtest.py        # V17 趋势四象限闸门（54 只 ETF，需先刷新行情）
python strategies/tech_indicators/v17_trend_gate/daily/_daily_scan.py   # V17 每日档位（全池象限分布 · 产出 md/json/html 看板）
```

## 每日操作（固定 6 策略）

技术指标类（本目录）的每日操作**固定为按 MACD / V3 / V6 / V7 / V9 / V10 六个策略各跑一遍**。
一条命令跑全家族（v9/v10 先扫描落盘，再出 6 策略汇总表）：

```powershell
powershell -ExecutionPolicy Bypass -File run_daily_tech_indicators.ps1
```

拆开看则是 5 步：`v9_right_side/scan.py` → `v9_right_side/_scan_report.py` → `v10_lights/scan.py` → `v10_lights/_scan_report.py` → `daily/_daily_recommend.py`。

- **先刷新行情**：`data/_refresh_data.py` 拉最新日线并重算指标；刷新失败的标的会被标 `❌`，陈旧数据标 `⚠️ 陈旧(日期)`，不作为最新信号。
- 基于最新一根 K 线，对 54 只场内 ETF（前四类）输出六个策略的当日动作（买入 0→1 / 卖出 1→0 / 持有 / 空仓；v9 的「观察/否决」与 v10 的「建仓」另计「其他」列），并给出 6 策略共振名单。
- 六个策略的增删只改 `_daily_recommend.py` 顶部的 `STRATS` 一处（**并同步 `pipeline/strategies.json` 的 `daily.strategies`**，看板解析器按它动态取列）；MACD/V3/V6/V7 复用各变种自己的实现，v9/v10 读各自 `daily/*_scan_out.jsonl`，不重复维护。
- 产物：`daily/_daily_recommend.md`。

| 标签 | 策略 | 规则 | 样本外证据 |
|---|---|---|---|
| **MACD** | 基础 MACD | DIF 上穿 DEA（金叉）买入 · 下穿（死叉）卖出 | 基准（OOS 夏普 0.54） |
| **V3** | 变种 3 MACD+BOLL | 零轴上 + 布林开口放大 + 收盘上穿上轨买入 · 死叉或下穿中轨卖出 | 未单独做 WF |
| **V6** | 变种 6 打分制 | 4 项看多票数 ≥4 买、≤2 卖（阈值 = 参数网格最优） | ⚠ **WF OOS 夏普 0.14**，低于 MACD 0.54 |
| **V7** | 变种 7 风格路由 | 按风格自动选：避险→买入持有 · 防御→BOLL · 中枢/其他→MACD · 进攻→V3 | ⚠ **真实 OOS 0.41**（0.55 那行是全样本映射，不是 OOS） |
| **V9** | 变种 9 右侧买入 | 五票确认（缺一票标「观察」，等次日不埋伏）；硬否决：5 日涨幅 >12% / 20 日均额 <0.3 亿 | 中位年化 +1.3% < BH +3.9%，换回撤 |
| **V10** | 变种 10 亮灯 | 门槛全过 + 灯评分达 enter_min 且必备灯达标 → 目标仓位 >0；否则空仓 | 扩池后 +4.8% vs BH +5.0%（≈0） |

> ⚠ **V6 / V7 不要与 MACD 等权使用**：两者的真实样本外都跑不过同一口径的基础 MACD，
> 保留在表里只为观察；「N 策略共振」请把这两列剔除后再读。
> 另：MACD/V3/V6/V7 的回测按「信号日收盘价」成交，V9 是次日收盘、V10 是次日开盘，
> **六列的历史绩效不可直接横向比较**（隔夜跳空实测约 3bp/次）。

> 全家族横向对比见 `_comparison_report.md`（`python strategies/tech_indicators/compare.py`）—— 该表覆盖基线五指标 + v1~v7，**不含 v9/v10/v16**（状态机型/市场级信号，口径不同，理由见上）。

## v16 TRIN（市场广度择时 · 不进每日 6 策略表）

`v16_trin/` 的口径与其他变种不同：它的输入是**全市场**涨跌家数与涨跌成交额
（`data/_breadth_daily.json`，由 `data/_refresh_breadth.py` 维护），输出是**一条**市场级信号，
执行标的是 510300。规则：TRIN 5 日均 > 1.4 连续 2 日 → 次日开盘买入 · 持有 20 个交易日收盘卖出 · 止损 -8%。

- 回测（2015-01 ~ 2026-10）：年化 **+0.42%**、回撤 **-31.72%**、持仓 15.8%、28 笔、胜率 57.1%、
  择时边际 +14.5bp/日；买入持有 510300 为 +3.34% / -52.97%。**只降回撤、不增收益**，
  且 1.4 在参数曲面上是「越高越差」的斜坡而不是平台 —— 详见 `v16_trin/backtest/_v16_report.md`。
- 每日读数：`python strategies/tech_indicators/v16_trin/scan.py` → `v16_trin/daily/_v16_signal.md`。
- 换标的（沪深300 / 中证A500 / 科创50）：`v16_trin/research/_index_compare.md` ——
  2015-2019 策略明显优于买入持有（沪深300 +4.31%/回撤 -15.5%/胜率 69%），2020 后三者全转负。

## 产物

- `backtest/_tech_indicators_report.md` —— 全池汇总 / 长历史子样本 / 代表标的(510300) / 分策略单笔统计
- `backtest/_tech_indicators_results.jsonl` —— 逐基金逐策略明细
- `backtest/_tech_indicators_510300.png` —— 代表标的净值对比图

> 全部为机械规则输出，**非投资建议**。
