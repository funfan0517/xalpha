# 变种 12 · VOL（量价）

> 技术指标家族的单指标基线之一。2026-10-28 从家族引擎 `../backtest.py` 的
> `build_signals()` 里**迁出**独立成变种目录 —— 引擎此后只做按名分发，
> **`rule.py` 是 VOL 策略的唯一实现**。

## 规则

- **入场**：放量上涨 —— 收盘涨 **且** 成交量 > 5 日均量
- **离场**：放量下跌（跌 + 量放大）**或** 缩量上涨（涨但量不足）—— 量价不配合就走
- **缺失值**：量或量均线为 NaN 时维持上一状态

## 文件

| 文件 | 作用 |
|---|---|
| `rule.py` | **规则本体**：`signal(close, r) -> 0/1 序列` |
| `backtest.py` | 家族式入口：跑全池、出报告、与其余四条基线和买入持有对照 |
| `backtest/_VOL_report.md` | 全池汇总 + 长历史子样本 + 510300 明细 |
| `backtest/_VOL_results.jsonl` | 逐标的指标 |
| `backtest/_VOL_510300.png` | 510300 净值对比曲线 |

## 用法

```powershell
python strategies/tech_indicators/v12_vol/backtest.py     # 单独跑本变种
python strategies/tech_indicators/backtest.py             # 五大基线并排对照
python strategies/tech_indicators/compare.py              # 全家族横截面
```

模拟结果，非投资建议。
