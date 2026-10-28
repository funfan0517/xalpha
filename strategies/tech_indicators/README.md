# tech_indicators · 技术指标五大策略回测

对 `data/_indicators_hist.json` 覆盖的 55 只场内 ETF，分别按 **MA / VOL / MACD / KDJ / BOLL** 五个指标策略做全历史回测。

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

## 变种目录（v1~v7 · v9~v15）

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

> 本家族的每日操作是固定 **6 策略（MACD / V3 / V6 / V7 / V9 / V10）**；v1~v6 为手动入口。
> **v9 / v10 是「状态机型」变种**：自带 `rule.py` / `engine.py` / `scan.py` / 分级报告，需要 OHLCV 与门槛判定，**不进入 `compare.py` 的单信号横截面表**（那套引擎按 `(close, 指标)` 重算，口径不可比）；它们的信号由各自 `scan.py` 先落盘、每日汇总脚本只读产物。
> **v11~v15 是「单指标基线」变种**：五个变种共用 runner `_baseline_bt.py`，各自
> `backtest.py` 只做「载入引擎 → 传入本变种 signal → 出报告」；规则本体在各自 `rule.py`，
> 引擎不再持有任何指标逻辑（`build_signals()` 改为按名分发），全局只有一份实现。
> **v8（仅 MA20 穿越）已于 2026-10-28 迁出本家族** —— 其标的池本就不是「全池 55 只」而是几只宽基腿，
> 现归入组合层策略 `vol_legs` 作为第一个变种：`strategies/vol_legs/v1_ma20/`（六腿、场外复权净值口径，
> 每日随 `run_daily_vol_legs.ps1` 第 2 步运行）。因此本目录的横截面对比（`compare.py`）不再包含 v8 一行。

## 运行

```powershell
python strategies/tech_indicators/backtest.py        # 五大基线并排对照（根报告）
python strategies/tech_indicators/v11_ma/backtest.py # 任一单指标变种单独跑（v11~v15 同构）
python strategies/tech_indicators/compare.py         # 全家族横截面（基线五 + v1~v7）
```

## 每日操作（固定 6 策略）

技术指标类（本目录）的每日操作**固定为按 MACD / V3 / V6 / V7 / V9 / V10 六个策略各跑一遍**。
一条命令跑全家族（v9/v10 先扫描落盘，再出 6 策略汇总表）：

```powershell
powershell -ExecutionPolicy Bypass -File run_daily_tech_indicators.ps1
```

拆开看则是 5 步：`v9_right_side/scan.py` → `v9_right_side/_scan_report.py` → `v10_lights/scan.py` → `v10_lights/_scan_report.py` → `daily/_daily_recommend.py`。

- **先刷新行情**：`data/_refresh_data.py` 拉最新日线并重算指标；刷新失败的标的会被标 `❌`，陈旧数据标 `⚠️ 陈旧(日期)`，不作为最新信号。
- 基于最新一根 K 线，对 55 只场内 ETF 输出六个策略的当日动作（买入 0→1 / 卖出 1→0 / 持有 / 空仓；v9 的「观察/否决」与 v10 的「建仓」另计「其他」列），并给出 6 策略共振名单。
- 六个策略的增删只改 `_daily_recommend.py` 顶部的 `STRATS` 一处（**并同步 `pipeline/strategies.json` 的 `daily.strategies`**，看板解析器按它动态取列）；MACD/V3/V6/V7 复用各变种自己的实现，v9/v10 读各自 `daily/*_scan_out.jsonl`，不重复维护。
- 产物：`daily/_daily_recommend.md`。

| 标签 | 策略 | 规则 |
|---|---|---|
| **MACD** | 基础 MACD | DIF 上穿 DEA（金叉）买入 · 下穿（死叉）卖出 |
| **V3** | 变种 3 MACD+BOLL | 零轴上 + 布林开口放大 + 收盘上穿上轨买入 · 死叉或下穿中轨卖出 |
| **V6** | 变种 6 打分制 | 4 项看多票数 ≥4 买、≤2 卖（阈值 = 参数网格最优） |
| **V7** | 变种 7 风格路由 | 按风格自动选：避险→买入持有 · 防御→BOLL · 中枢/其他→MACD · 进攻→V3 |
| **V9** | 变种 9 右侧买入 | 五票确认（缺一票标「观察」，等次日不埋伏）；硬否决：5 日涨幅 >12% / 20 日均额 <0.3 亿 |
| **V10** | 变种 10 亮灯 | 门槛全过 + 灯评分达 enter_min 且必备灯达标 → 目标仓位 >0；否则空仓 |

> 全家族横向对比见 `_comparison_report.md`（`python strategies/tech_indicators/compare.py`）—— 该表覆盖基线五指标 + v1~v7，**不含 v9/v10**（状态机型，口径不同，理由见上）。

## 产物

- `backtest/_tech_indicators_report.md` —— 全池汇总 / 长历史子样本 / 代表标的(510300) / 分策略单笔统计
- `backtest/_tech_indicators_results.jsonl` —— 逐基金逐策略明细
- `backtest/_tech_indicators_510300.png` —— 代表标的净值对比图

> 全部为机械规则输出，**非投资建议**。
