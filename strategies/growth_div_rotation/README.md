# 成长/红利风格轮动（Growth/Dividend Style Rotation）

> 同一份方法论（用「成长指数 ÷ 中证红利」风格比值 R 做二元切换）下的**多变种家族**。
> 各变种共享同一份指数缓存（`v0/data/_gd_index_klines.json`）与同一套引擎时序，差别只在**规则本身**。
> 用户方法论来源：《成长/红利风格轮动》手册（规则化实现见 `v1_ratio_bands/`）。

## 变种

| 目录 | 信号形态 | 换仓方式 | 资金模型 | 状态 |
| --- | --- | --- | --- | --- |
| `v0/` | R 相对自身 MA20 ± 1% 缓冲带 + 250 日分位 Q（无量纲、自适应） | 一次性整仓切换，月频上限 4 次 | 纯净值（满仓二元） | **主线，进每日流水线 + flow_select 排名** |
| `v1_ratio_bands/` | R 的绝对水位：`≤0.20` 切成长 / `≥0.31` 切红利（量纲相关、历史拟合，**按科创50/中证红利比值重新标定**） | 清仓 → 资金拆 5 份 → 5 个定投日分买；反向阈值可打断 | 每月定投 1000 + 轮动（资金加权 XIRR） | 研究变种，仅回测对比；**2026-11-02 起退出每日看板**（`daily.enabled=false`，脚本与产物保留可手跑） |
| `v2_absolute_bands/` | R 的绝对水位带：**创业板指** R `≤0.35` 加仓 / `≥0.65` 减仓（**复现用户两张图**，2026-10-30 阈值由 0.30/0.70 挪至 0.35/0.65；分子固定 399006） | 一次性整仓切换（无分批） | 纯净值（与 v0 同口径） | 研究变种；**2026-11-02 起进板块3 每日看板**（信号层裸基准，与 v0/v5 三卡并列） |
| `v3_trend_filter/` | v2 的 0.35/0.65 **+ 双侧趋势闸门**（创业板指 / 中证红利 各看自己 MA200，破线该侧减半、削掉的仓位**转现金** + 20 日冷却） | 一次性整仓切换 | 纯净值 + 现金仓计息 | 研究变种（v2 的回撤控制版），仅回测 + 画图 |
| `v4_graded_bands/` | v3 的**连续化**：R 0.35~0.65 拆 4 档，成长:红利按 5:0 / 4:1 / 1:1 / 1:4 / 0:5 逐级过渡（趋势层与现金仓同 v3） | 逐档调仓（20~30% 单边换手） | 纯净值 + 现金仓计息 | 研究变种，**实测跑输 v3**（+8.38%/-52.53%/Calmar 0.16），留档备查 |
| `v5_vol_target/` | v3 + **波动率目标层**：按 120 日已实现年化波动把总仓位缩放 `min(1, 18%/vol)`，腾出的进现金（死区 0.15 + 全局限速 10 日） | 缩放系数跨档才调（平均暴露 63%） | 纯净值 + 现金仓计息 | 研究变种，**全家族风险调整最优**（+14.23%/-26.74%/Calmar 0.53），代价是年化让 1.4pp；**2026-11-02 起进板块3 每日看板** |

**关键事实**：默认成长指数 = **科创50（000688）**（2026-10-30 由创业板指切换；创业板指改作 `--alt`）。
v1 的「高收益」大半来自阈值拟合与更高成长暴露，风险调整后与 v0 基本打平；
阈值一变结论就反转（0.24/0.28 → 已与 v0 打平），且全期仅 2 个决策点，样本量太小无法区分过拟合。
v1 唯一站得住的优势是**执行层面**：全期仅 2 次换仓 vs v0 的 64 次（其中多段持仓不足 5 个交易日，踩场外 C 类 <7 天赎回费）。

## 目录结构

```
growth_div_rotation/
├─ README.md              ← 本文件（家族说明）
├─ compare.py             ← 跨变种对比入口（v1 vs v0，产出 _v1_vs_v0_report.md / .png 到本目录）
├─ _compare_v0_v5.py      ← 全家族对比（读六份已落盘产物，产出 _v0_v5_compare.md / .png）
├─ _v0_v5_compare.md      ← 六代综合对比报告（三段窗口、基准标尺、趋势层反转）
├─ _v0_v5_compare.png     ← 六代同窗净值 + 回撤（2021-01 起）
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
├─ v2_absolute_bands/     ← 研究变种（创业板指 0.35/0.65 绝对水位带，复现用户两张图）
│  ├─ rule.py / factors.py / engine.py / data.py / backtest.py / scan.py
│  ├─ backtest/           ← _v2_report.md / _v2_bt.json(l) / _v2_nav.png
│  │                         _v2_band.png（图1）/ _v2_cycle.png（图2）/ _v2_dual.png
│  ├─ daily/              ← _gd_v2_signal.json / _gd_v2_daily.md（板块3 第二张卡）
│  ├─ research/           ← _opt_probe.py（阈值曲面 / 趋势过滤 / 分档对照）
│  └─ README.md
├─ v3_trend_filter/       ← 研究变种（v2 的 0.35/0.65 + 双侧 MA200 闸门 + 现金仓）
│  ├─ rule.py / factors.py / engine.py / data.py / backtest.py
│  ├─ backtest/           ← _v3_report.md / _v3_bt.json(l) / _v3_nav.png / _v3_alloc.png / _v3_dual.png
│  ├─ research/           ← _dd_probe.py（回撤归因）/ _dd_control.py（闸门分层实验）/ _tune.py（参数标定）
│  └─ README.md
├─ v4_graded_bands/       ← 研究变种（v3 的连续化：R 阶梯 5:0→0:5，**实测跑输 v3**）
│  ├─ rule.py / factors.py / engine.py / data.py / backtest.py
│  ├─ backtest/           ← _v4_report.md / _v4_bt.json(l) / _v4_nav.png / _v4_alloc.png / _v4_dual.png
│  └─ README.md
└─ v5_vol_target/         ← 研究变种（v3 + 18% 波动率目标，**全家族风险调整最优**）
   ├─ rule.py / factors.py / engine.py / data.py / backtest.py / scan.py
   ├─ backtest/           ← _v5_report.md / _v5_bt.json(l) / _v5_nav.png / _v5_vol.png / _v5_alloc.png（每日重画）
   │                         / _v5_legs_long.png / _v5_legs_3y.png（两条腿各自的 MA200 趋势线 + 该腿仓位带 +
   │                           ▲▼ 调仓点，全期 / 近三年各一张；`backtest.py --chart` 与 scan.py 每日都会重画）
   └─ daily/              ← _gd_v5_signal.json / _gd_v5_daily.md（板块3 第三张卡，卡片内嵌上面的 _v5_alloc.png）
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

# 全家族对比（v0~v5，只读六份已落盘产物，不重跑）
python strategies/growth_div_rotation/_compare_v0_v5.py

# v2 回测 + 报告 + 四张图（band / cycle / dual / nav）
python strategies/growth_div_rotation/v2_absolute_bands/backtest.py

# v3 回测 + 报告 + 三张图（双侧 MA200 闸门 + 现金仓；nav / alloc / dual）
python strategies/growth_div_rotation/v3_trend_filter/backtest.py

# v4 回测 + 报告 + 三张图（R 阶梯分档；结论是跑输 v3，见其 README）
python strategies/growth_div_rotation/v4_graded_bands/backtest.py

# v5 回测 + 报告 + 三张图（波动率目标；nav / vol / alloc）
python strategies/growth_div_rotation/v5_vol_target/backtest.py
```

## 每日操作（板块3 = v0 / v2 / v5 三卡）

`run_daily_growth_div_rotation.ps1` 一条命令跑三步，产物分别落在各变种的 `daily/`：

| 卡 | 规则 | 脚本 | 产物 |
|---|---|---|---|
| v0 | R 相对 MA20±1% 缓冲带 + 250 日分位（主线） | `v0/scan.py` | `v0/daily/_gd_signal.json` + `_signal_report.md` |
| v2 | R 绝对水位 0.35/0.65，一次性整仓切换（信号层裸基准） | `v2_absolute_bands/scan.py` | `v2_absolute_bands/daily/_gd_v2_signal.json` + `_gd_v2_daily.md` |
| v5 | R 水位 + 双侧 MA200 闸门 + 18% 波动率目标（风险调整最优） | `v5_vol_target/scan.py` | `v5_vol_target/daily/_gd_v5_signal.json` + `_gd_v5_daily.md` + **`v5_vol_target/backtest/_v5_alloc.png`（每日重画）** |

> v5 的仓位图（三资产堆叠 + 创业板指/中证红利点位 + 比值 R）**每天随 `scan.py` 重画**：
> 画图逻辑只写在 `backtest.py::plot_alloc`，`scan.py` 把当日那份权重喂给它，不给每日流程
> 另写一套口径；图落在 `backtest/_v5_alloc.png`，`pipeline/strategies.json` 的
> `daily.chart` 指向它，看板板块3 的 v5 卡片直接内嵌显示。
> 只想重画这张图：`python strategies/growth_div_rotation/v5_vol_target/backtest.py --chart`。

> "明日"的权重靠**追加一个占位 bar** 让状态机自己再走一步得到（复制最新收盘成下一交易日，
> 重跑 `signal_weights` / `target_weights` / `vol_scale` / `throttle`），
> 因此冷却、死区、限速三道状态都与回测逐行同构，扫描脚本里不重写规则。
>
> `v1_ratio_bands/`（绝对阈值 0.20/0.31 + 5 期分批）2026-11-02 起退出看板
> （`daily.enabled=false`），产物与 `scan.py` 保留可手跑；v3 / v4 是 v5 的中间形态，不出每日卡。

## 数据口径与修正（2026-10-29）

- 指数缓存起点 **2020-01-02**（科创50 上市日，故本家族回测窗口为 2020 起，短于手册的 2010-06；创业板指口径为旧版 `--alt`）。
- `rule.index_prefix()` 曾按股票规则（首位 5/6/9→沪）误判 `000922`/`000688` 为深市，取到同号**个股**日线污染回测；
  已改为 `399/159 → 深市、其余 → 沪市`，并加「首值低于合理下界即报错」护栏。此前所有旧缓存绩效数字作废。
- 全部用指数价格收益（未含分红再投/跟踪误差/申赎费），非投资建议。
