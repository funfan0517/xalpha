# tech_indicators · 技术指标五大策略回测

对 `data/_indicators_hist.json` 覆盖的 55 只场内 ETF，分别按 **MA / VOL / MACD / KDJ / BOLL** 五个指标策略做全历史回测。

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

## 变种目录（v1~v8）

| 变种 | 目录 | 一句话规则 |
|---|---|---|
| v1 | `v1_kdj_ma10/` | KDJ 超卖金叉后窗口内站稳 MA10 买入 · 跌破 MA10 卖出 |
| v2 | `v2_macd_kdj/` | 零轴上 + 缩量回踩 + KDJ 金叉买入 |
| v3 | `v3_macd_boll/` | 零轴上 + 布林开口放大 + 上穿上轨买入 · 死叉或下穿中轨卖出 |
| v4 | `v4_boll_kdj_macd/` | 布林近下轨 + MACD 金叉 + KDJ 超卖拐头 |
| v5 | `v5_regime_switch/` | 趋势行情用 v3 / 震荡行情用 v4 自动切换 |
| v6 | `v6_score_wf/` | 4 项看多票数打分，票数 ≥4 买 / ≤2 卖 |
| v7 | `v7_style_route/` | 按标的风格自动路由到 BOLL / MACD / v3 / 买入持有 |
| v8 | `v8_ma20/` | **仅 MA20 穿越：上穿买入 · 下穿卖出（无 MA60 过滤）· 标的池 = 科创50 / 恒生科技 / 中证A500 / 黄金** |

> 本家族的每日操作仍是固定四策略（MACD / V3 / V6 / V7）；其余变种为手动入口。
> **例外：v8 已于 2026-10-28 作为独立策略 `v8_ma20` 纳入每日操作**（`run_daily_v8_ma20.ps1`，`run_daily_all.ps1` 自动带上），
> 因为它自带每日扫描与独立标的池，混进四策略表会被误读为「对全池有效」。v8 的两个额外入口：
> `python strategies/tech_indicators/v8_ma20/daily/_daily_scan.py`（产物 `v8_ma20/daily/_v8_ma20_daily.md`）、
> `python strategies/tech_indicators/v8_ma20/research/backtest.py`（产物 `v8_ma20/research/_v8_focus_report.md`）。

## 运行

```powershell
python strategies/tech_indicators/backtest.py
```

## 每日操作（固定四策略）

技术指标类（本目录）的每日操作**固定为按 MACD / V3 / V6 / V7 四个策略各跑一遍**：

```powershell
python strategies/tech_indicators/daily/_daily_recommend.py
```

- **先刷新行情**：`data/_refresh_data.py` 拉最新日线并重算指标；刷新失败的标的会被标 `❌`，陈旧数据标 `⚠️ 陈旧(日期)`，不作为最新信号。
- 基于最新一根 K 线，对 55 只场内 ETF 输出四个策略的当日动作（买入 0→1 / 卖出 1→0 / 持有 / 空仓），并给出四策略共振名单。
- 四个策略的增删只改 `_daily_recommend.py` 顶部的 `STRATS` 一处；策略规则复用各变种自己的实现（`base.build_signals` / `v3_macd_boll` / `v6_score_wf` / `v7_style_route`），不重复维护。
- 产物：`daily/_daily_recommend.md`。

| 标签 | 策略 | 规则 |
|---|---|---|
| **MACD** | 基础 MACD | DIF 上穿 DEA（金叉）买入 · 下穿（死叉）卖出 |
| **V3** | 变种 3 MACD+BOLL | 零轴上 + 布林开口放大 + 收盘上穿上轨买入 · 死叉或下穿中轨卖出 |
| **V6** | 变种 6 打分制 | 4 项看多票数 ≥4 买、≤2 卖（阈值 = 参数网格最优） |
| **V7** | 变种 7 风格路由 | 按风格自动选：避险→买入持有 · 防御→BOLL · 中枢/其他→MACD · 进攻→V3 |

> 全家族横向对比见 `_comparison_report.md`（`python strategies/tech_indicators/compare.py`）。

## 产物

- `backtest/_tech_indicators_report.md` —— 全池汇总 / 长历史子样本 / 代表标的(510300) / 分策略单笔统计
- `backtest/_tech_indicators_results.jsonl` —— 逐基金逐策略明细
- `backtest/_tech_indicators_510300.png` —— 代表标的净值对比图

> 全部为机械规则输出，**非投资建议**。
