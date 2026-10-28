# 变种 15 · BOLL（布林通道反转）

> 技术指标家族的单指标基线之一。2026-10-28 从家族引擎 `../backtest.py` 的
> `build_signals()` 里**迁出**独立成变种目录 —— 引擎此后只做按名分发，
> **`rule.py` 是 BOLL 策略的唯一实现**（v7 风格路由的「防御」档也复用它）。

## 规则

- **入场**：收盘触及/跌破布林下轨（`close <= lower`）—— 超跌接反转
- **离场**：收盘触及/升破布林上轨（`close >= upper`）—— 回到上轨就兑现
- 上下轨 NaN 时维持上一状态；布林参数 N=20 / k=2.0，由指标库预计算

## 文件

| 文件 | 作用 |
|---|---|
| `rule.py` | **规则本体**：`signal(close, r) -> 0/1 序列` |
| `backtest.py` | 家族式入口：跑全池、出报告、与其余四条基线和买入持有对照 |
| `backtest/_BOLL_report.md` | 全池汇总 + 长历史子样本 + 510300 明细 |
| `backtest/_BOLL_results.jsonl` | 逐标的指标 |
| `backtest/_BOLL_510300.png` | 510300 净值对比曲线 |

## 用法

```powershell
python strategies/tech_indicators/v15_boll/backtest.py     # 单独跑本变种
python strategies/tech_indicators/backtest.py             # 五大基线并排对照
python strategies/tech_indicators/compare.py              # 全家族横截面
```

模拟结果，非投资建议。
