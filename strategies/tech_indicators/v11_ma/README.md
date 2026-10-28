# 变种 11 · MA（双均线生命线）

> 技术指标家族的单指标基线之一。2026-10-28 从家族引擎 `../backtest.py` 的
> `build_signals()` 里**迁出**独立成变种目录 —— 引擎此后只做按名分发，
> **`rule.py` 是 MA 策略的唯一实现**。

## 规则

- **入场**：收盘上穿 MA20（生命线）**且** 收盘 > MA60（牛熊线）—— 短均线转头 + 中期趋势同向
- **离场**：收盘下穿 MA20 —— 只认生命线，MA60 只作入场过滤
- **预热**：MA20/MA60 为 NaN 时维持上一状态（回测从第 61 个交易日起算）

## 文件

| 文件 | 作用 |
|---|---|
| `rule.py` | **规则本体**：`signal(close, r) -> 0/1 序列`；家族引擎、其余变种、每日建议都调它 |
| `backtest.py` | 家族式入口：跑全池、出报告、与其余四条基线和买入持有对照 |
| `backtest/_MA_report.md` | 全池汇总（中位年化/回撤/夏普/曝光/胜率）+ 长历史子样本 + 510300 明细 |
| `backtest/_MA_results.jsonl` | 逐标的逐策略指标 |
| `backtest/_MA_510300.png` | 510300 净值对比曲线 |

## 用法

```powershell
python strategies/tech_indicators/v11_ma/backtest.py     # 单独跑本变种
python strategies/tech_indicators/backtest.py            # 五大基线并排对照（本变种是其中一行）
python strategies/tech_indicators/compare.py             # 全家族 13 个策略横截面
```

改规则只改 `rule.py`；`../_baseline_bt.py` 是 v11~v15 共用的回测 runner，一般不用动。
模拟结果，非投资建议。
