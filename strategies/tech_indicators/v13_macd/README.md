# 变种 13 · MACD（基线最强）

> 技术指标家族的单指标基线之一，也是**每日操作表里的 `MACD` 列**。2026-10-28 从家族引擎
> `../backtest.py` 的 `build_signals()` 里**迁出**独立成变种目录 —— 引擎此后只做按名分发，
> **`rule.py` 是 MACD 策略的唯一实现**（每日建议与 v7 风格路由的「中枢/其他」都复用它）。

## 规则

- **入场**：DIF 上穿 DEA（金叉）
- **离场**：DIF 下穿 DEA（死叉）
- **不看零轴**：`DIF>0` 过滤留给 v2 / v3，本变种保持原始口径
- 参数 MACD(12,26,9)，指标由 `data/_indicators_hist.json` 预计算

## 文件

| 文件 | 作用 |
|---|---|
| `rule.py` | **规则本体**：`signal(close, r) -> 0/1 序列` |
| `backtest.py` | 家族式入口：跑全池、出报告、与其余四条基线和买入持有对照 |
| `backtest/_MACD_report.md` | 全池汇总 + 长历史子样本 + 510300 明细 |
| `backtest/_MACD_results.jsonl` | 逐标的指标 |
| `backtest/_MACD_510300.png` | 510300 净值对比曲线 |

## 用法

```powershell
python strategies/tech_indicators/v13_macd/backtest.py     # 单独跑本变种
python strategies/tech_indicators/daily/_daily_recommend.py  # 每日建议（MACD 列走本目录 rule.py）
python strategies/tech_indicators/compare.py               # 全家族横截面
```

模拟结果，非投资建议。
