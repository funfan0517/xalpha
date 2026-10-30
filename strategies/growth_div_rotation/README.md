# 成长/红利风格轮动（Growth/Dividend Style Rotation）

> 同一份方法论（用「成长指数 ÷ 中证红利」风格比值 R 做二元切换）下的**多变种家族**。
> 各变种共享同一份指数缓存（`v0/data/_gd_index_klines.json`）与同一套引擎时序，差别只在**规则本身**。
> 用户方法论来源：《成长/红利风格轮动》手册（规则化实现见 `v1_ratio_bands/`）。

## 变种

| 目录 | 信号形态 | 换仓方式 | 资金模型 | 状态 |
| --- | --- | --- | --- | --- |
| `v0/` | R 相对自身 MA20 ± 1% 缓冲带 + 250 日分位 Q（无量纲、自适应） | 一次性整仓切换，月频上限 4 次 | 纯净值（满仓二元） | **主线，进每日流水线 + flow_select 排名** |
| `v1_ratio_bands/` | R 的绝对水位：`≤0.20` 切成长 / `≥0.31` 切红利（量纲相关、历史拟合，**按科创50/中证红利比值重新标定**） | 清仓 → 资金拆 5 份 → 5 个定投日分买；反向阈值可打断 | 每月定投 1000 + 轮动（资金加权 XIRR） | 研究变种，仅回测对比 |
| `v2_absolute_bands/` | R 的绝对水位带：**创业板指** R `≤0.30` 加仓 / `≥0.70` 减仓（**复现用户两张图**；分子固定 399006） | 一次性整仓切换（无分批） | 纯净值（与 v0 同口径） | 研究变种，仅回测 + 画图 |

**关键事实**：默认成长指数 = **科创50（000688）**（2026-10-30 由创业板指切换；创业板指改作 `--alt`）。
v1 的「高收益」大半来自阈值拟合与更高成长暴露，风险调整后与 v0 基本打平；
阈值一变结论就反转（0.24/0.28 → 已与 v0 打平），且全期仅 2 个决策点，样本量太小无法区分过拟合。
v1 唯一站得住的优势是**执行层面**：全期仅 2 次换仓 vs v0 的 64 次（其中多段持仓不足 5 个交易日，踩场外 C 类 <7 天赎回费）。

## 目录结构

```
growth_div_rotation/
├─ README.md              ← 本文件（家族说明）
├─ compare.py             ← 跨变种对比入口（产出 _v1_vs_v0_report.md / .png 到本目录）
├─ _v1_vs_v0_report.md    ← 对比报告（口径 A/B、分段、阈值/分批敏感性、决策延迟、换仓日志）
├─ _v1_vs_v0.png          ← 对比净值曲线（对数轴）
├─ v0/                    ← 主线变种（缓冲带 + 分位）
│  ├─ rule.py / factors.py / engine.py / data.py / scan.py   ← 本体 + 每日信号 + 缓存维护
│  ├─ backtest.py / _reportbt.py / _dbg_select.py
│  ├─ backtest/           ← _bt_report.md / _gd_bt.json / .jsonl / _gd_nav.png
│  ├─ daily/              ← _signal_report.md / _gd_signal.json / _universe_growth_div_active.*
│  ├─ data/               ← _gd_index_klines.json（跨变种共享，仅此处维护）
│  └─ README.md
├─ v1_ratio_bands/        ← 研究变种（绝对阈值 + 5 期分批 + 定投，实现用户手册规则）
│  ├─ rule.py / engine.py / data.py
│  ├─ backtest/           ← _v1_bt.json / _v1_bt.jsonl（v1 单行摘要）
│  └─ README.md
└─ v2_absolute_bands/     ← 研究变种（创业板指 0.30/0.70 绝对水位带，复现用户两张图）
   ├─ rule.py / factors.py / engine.py / data.py / backtest.py
   ├─ backtest/           ← _v2_report.md / _v2_bt.json(l) / _v2_nav.png
   │                         _v2_band.png（图1 比值带状）/ _v2_cycle.png（图2 周期轮动）
   └─ README.md
```

## 运行

```powershell
# 主线 v0 每日信号（盘后）
powershell -ExecutionPolicy Bypass -File run_daily_growth_div_rotation.ps1

# v0 回测 / 报告
python strategies/growth_div_rotation/v0/backtest.py [--alt]
python pipeline/run_flow.py backtest --strategy growth_div_rotation --report

# 跨变种对比（v1 vs v0，自动重跑 v0 保证同窗同数据）
python strategies/growth_div_rotation/compare.py [--rerun-v0]

# v2 回测 + 报告 + 复现两张图（band / cycle）
python strategies/growth_div_rotation/v2_absolute_bands/backtest.py
```

## 数据口径与修正（2026-10-29）

- 指数缓存起点 **2020-01-02**（科创50 上市日，故本家族回测窗口为 2020 起，短于手册的 2010-06；创业板指口径为旧版 `--alt`）。
- `rule.index_prefix()` 曾按股票规则（首位 5/6/9→沪）误判 `000922`/`000688` 为深市，取到同号**个股**日线污染回测；
  已改为 `399/159 → 深市、其余 → 沪市`，并加「首值低于合理下界即报错」护栏。此前所有旧缓存绩效数字作废。
- 全部用指数价格收益（未含分红再投/跟踪误差/申赎费），非投资建议。
