# 变种 14 · KDJ（超买超卖反转）

> 技术指标家族的单指标基线之一。2026-10-28 从家族引擎 `../backtest.py` 的
> `build_signals()` 里**迁出**独立成变种目录 —— 引擎此后只做按名分发，
> **`rule.py` 是 KDJ 策略的唯一实现**。

## 规则

- **入场**：K 上穿 D（金叉）**且** K < 20（超卖区）—— 只在低位接反转，不追高位金叉
- **离场**：K 下穿 D（死叉）**且** K > 80（超买区）—— 高位死叉才走，低位震荡不折腾
- K/D 任一侧 NaN 时维持上一状态

## 文件

| 文件 | 作用 |
|---|---|
| `rule.py` | **规则本体**：`signal(close, r) -> 0/1 序列` |
| `backtest.py` | 家族式入口：跑全池、出报告、与其余四条基线和买入持有对照 |
| `backtest/_KDJ_report.md` | 全池汇总 + 长历史子样本 + 510300 明细 |
| `backtest/_KDJ_results.jsonl` | 逐标的指标 |
| `backtest/_KDJ_510300.png` | 510300 净值对比曲线 |

## 用法

```powershell
python strategies/tech_indicators/v14_kdj/backtest.py     # 单独跑本变种
python strategies/tech_indicators/backtest.py             # 五大基线并排对照
python strategies/tech_indicators/compare.py              # 全家族横截面
```

模拟结果，非投资建议。
