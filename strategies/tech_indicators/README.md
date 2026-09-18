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

## 产物

- `backtest/_tech_indicators_report.md` —— 全池汇总 / 长历史子样本 / 代表标的(510300) / 分策略单笔统计
- `backtest/_tech_indicators_results.jsonl` —— 逐基金逐策略明细
- `backtest/_tech_indicators_510300.png` —— 代表标的净值对比图

> 全部为机械规则输出，**非投资建议**。
