# 变种 4 · BOLL_KDJ_MACD（布林 + KDJ + MACD 三共振）

技术指标策略的**第四个变种**。用**布林带定空间、MACD 看趋势动能、KDJ 抓情绪时机**，三者共振以提高
抄底 / 逃顶判断胜率。它是辅助判断工具，不是稳赚公式。

## 规则（多头 / 空仓 0-1）

| 方向 | 条件 |
|---|---|
| **抄底买入** | 价格接近布林下轨（近 10 日通道位置曾 ≤20%）**且** MACD 金叉（DIF>DEA）**且** KDJ 超卖拐头金叉（近 10 日 K<20 且现 K>D） |
| **逃顶卖出** | 价格接近布林上轨（近 10 日通道位置曾 ≥80%）**且** MACD 死叉（DIF<DEA）**且** KDJ 超买拐头死叉（近 10 日 K>80 且现 K<D） |

通道位置 `bpos = (收盘-下轨)/(上轨-下轨)`。参数：`BUY_POS=0.20`、`SELL_POS=0.80`、`KWIN=10`、`RES=10`。

> **实现说明**：价格贴近下轨时 MACD 通常仍在死叉（MACD 滞后），三者**同日**共振全池仅 51 笔、17 只零交易。
> 故按"共振"的**时序**语义实现：用共振窗口 `RES` 允许"近 RES 日曾触下轨"，再要求 MACD 金叉与 KDJ 超卖拐头金叉同时成立。

> 未量化项：MACD 底/顶背离（主观信号）。

## 与基线的关系

本目录**只读复用**上级基线 `../backtest.py` 的引擎与 5 个基础策略（MA/VOL/MACD/KDJ/BOLL），
用于对照；**不修改、不覆盖**基线文件。

## 运行

```powershell
python strategies/tech_indicators/v4_boll_kdj_macd/backtest.py
```

## 产物

- `backtest/_v4_boll_kdj_macd_report.md` —— 变种 vs 基础策略 vs 买入持有
- `backtest/_v4_boll_kdj_macd_results.jsonl` —— 逐基金明细
- `backtest/_v4_boll_kdj_macd_510300.png` —— 代表标的净值对比图

> 机械规则输出，**非投资建议**。
