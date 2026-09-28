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
