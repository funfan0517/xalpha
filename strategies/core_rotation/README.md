# 核心轮动 · 六类资产动态配置（场外 6 标的）

> 方法论文档：[doc/核心轮动投资策略手册.md](../../doc/核心轮动投资策略手册.md)
> 集成日期：2026-09-09 · 流水线阶段：已注册 `pipeline/strategies.json`，进入每日监控；回测/select 不适用（组合层资产配置，见下）

## 1. 标的与通道（一类一标的，无第 7 只）

六类资产各配 **1 只场外主仓**（实际申赎，按净值成交）与 **1 只场内代理**（仅日频信号/动量观察，不交易）：

| 资产 | 场外主仓（执行） | 场内代理（信号） |
| --- | --- | --- |
| 中长债 | `003377` 广发中债7-10年国开债指数C | `511260` 十年国债ETF国泰 |
| 中证红利 | `012644` 招商中证红利ETF联接C | `515080` 中证红利ETF招商 |
| 纳指100 | `270042` 广发纳斯达克100ETF联接(QDII)A | `513100` 纳指ETF国泰 |
| 中证A500 | `023299` 汇添富中证A500指数增强C | `563360` A500ETF华泰柏瑞 |
| 科创50 | `011609` 易方达上证科创板50ETF联接C | `588000` 科创50ETF华夏 |
| 黄金 | `000216` 华安黄金ETF联接A | `518880` 黄金ETF华安 |

标的清单不在此硬编码：`rule.pool()` 从唯一池 `data/_universe.md` 的场外行派生（按 `off_code` 匹配）。

## 2. 数据可达性（2026-09-09 实测）

| 指标 | 通道 | 状态 |
| --- | --- | --- |
| 场内代理日线 / 动量（1/5/20日、MA28站线） | `xa.get_daily`（雪球） | ✅ 自动 |
| 场外主仓单位净值（执行确认） | `xa.fundinfo(...).price`（天天基金） | ✅ 自动（QDII 滞后约 1 个交易日） |
| 10Y 国债到期收益率（含近 6 周趋势） | `xalpha.universal.get_bond_rates('N')`（中债收益率曲线） | ✅ 自动 |
| 中证红利股息率 / 红利 PE 分位 | 蛋卷基金估值 `djapi/index_eva/dj`（SH000922，每日） | ✅ 自动 |
| 科创50 PE 分位 / 绝对 PE（→ 自动算 科创PE比） | 蛋卷基金估值（SH000688） | ✅ 自动 |
| A500 发布以来分位 / 绝对 PE | 中证官网 `index-perf`（000510，2024-09 发布起日频 `peg`，纯 Python） | ✅ 自动（本地日频库 `data/_a500_pe_hist.csv`，每日增量） |
| A500/纳指 PE(TTM) 绝对值（辅助/交叉验证） | mx-ds-mcp（东方财富，agent 回填 `data/_mx_valuation_latest.json`） | ✅ 自动（agent 每日查） |
| 纳指 Forward PE 分位（12M 一致预期） | historyofmarket 公开 JSON `api/ndx/forward-pe.json`，官方周频 2001-至今（CC BY 4.0） | ✅ 自动（本地库 `data/_ndx_fwd_pe_hist.csv`，近10年/全窗分位） |
| DXY、美债10Y 实际利率（TIPS） | — | ⚠️ 可选人工（仅微调因子） |
| 成长占优信号 / 系统性风险开关 | — | ⚠️ 人工（布尔） |

> 说明：红利/科创估值=蛋卷（每日自动）；A500 分位=中证官网日频灌库（每日自动，本地算累计分位）；
> mx-ds-mcp 为「agent 工具」，Python 脚本无法直接调用 → A500/纳指的 PE(TTM) 绝对值快照采用
> 「agent 定时查询 → 写回 `data/_mx_valuation_latest.json` → 再跑 `_signal.py`」流程（AGENTS.md 的 Agentic 工作流），见 §4.1。

## 3. 运行方式

```powershell
# 每日（盘后/14:xx 均可；场内代理为观察信号）
python strategies/core_rotation\_signal.py
# 或
powershell -ExecutionPolicy Bypass -File run_daily_core_rotation.ps1
# 或经流水线
python pipeline/run_flow.py daily --strategy core_rotation
```

产物：
- `strategies/core_rotation/_daily_report.md` —— 每日监控报告（七节：行情/净值/利率与估值锚/情景与目标配置/组合监控/月度再平衡检查/风险红线）
- `data/_core_daily.json` —— 机器可读快照
- `strategies/core_rotation/valuation_template.json` —— 估值锚模板（自动生成）

## 4. 估值锚说明（估值分位全部自动；仅 DXY/实际利率/开关可选人工）

运行脚本时**自动**写入：

- 蛋卷（每日）：`div_yield_hs_pct`（红利股息率%）、`hs_pe_pct`（红利 PE 分位）、`kc_pe_over_hs`（科创PE比=科创50 PE÷红利 PE）
- A500 本地日频库（每日，`data/_a500_pe_hist.csv`，源=中证官网 `index-perf` 000510 `peg`，2024-09 发布起）：最新 PE / 区间 / **发布以来累计分位** → 写入 `a500_pe_pct`（标口径：官方日频估算）
- NDX Forward PE 库（每日，`data/_ndx_fwd_pe_hist.csv`，源=historyofmarket `api/ndx/forward-pe.json`，官方周频 2001-至今，CC BY 4.0）：最新 12M 一致预期 Forward PE → **近10年分位**写入 `ndx_fwd_pe_pct`（另给 2001 以来全窗分位）

> 口径：中证官网 `peg` 为近似静态 PE、historyofmarket 为一致预期 Forward PE，二者与蛋卷/东财的 TTM 口径不同但各自全序列同口径，分位自洽；报告均显式标注来源口径。

**可选覆盖键**（脚本不覆盖；已有手工值会优先于自动值；留空则用上面的自动值）：

```json
{
  "as_of": "2026-09-08",      // 自动写为数据日, 无需手动
  "a500_pe_pct": 0.38,        // 可选覆盖 A500 分位(如你有 TTM 口径)
  "ndx_fwd_pe_pct": 0.52,     // 可选覆盖纳指 Forward 分位(留空=自动周频共识10年分位)
  "dxy": 96.5,                // 可选: 美元指数
  "us_real_yield": 1.9,       // 可选: 美债10Y 实际利率 %
  "growth_yes": false,        // 手动: 创业板连5日跑赢红利 & 两市>2.3万亿
  "risk_hedge": false,        // 手动: 系统性风险/科创暴雷/地缘冲突
  "risk_red_component": false,// 手动: 红利成分股暴雷/下调分红
  "note": ""
}
```

> 报告会在估值数据日超过 7 天时标黄提醒。可选 `data/_core_holdings.json`（当前持仓权重）以启用第六节月度再平衡偏差检查：

```json
{"as_of": "2026-09-01",
 "weights": {"003377": 0.10, "012644": 0.25, "270042": 0.15, "023299": 0.25, "011609": 0.05, "000216": 0.20}}
```

## 4.1 通过 mx-ds-mcp（东方财富）取 A500 / 纳指估值

已连接 `mx-ds-mcp`（工具 `mx_index_block_finance_data` / `mx_us_finance_data`，2026-09-09 实测）：

| 需要 | 东财实际返回 | 结论 |
| --- | --- | --- |
| 中证A500(000510) PE(TTM) | ✅ 16.05（2026-09-08） | 绝对值快照（agent 每日回填），与本地官方日频库互相印证 |
| 中证A500 历史分位 | ⚠️ 东财只给年内/月度窗口分位；**官方指数 2024-09 才发布，"10 年分位"不存在** | ✅ 已落地：改用「发布以来累计分位」，由中证官网日频 `peg` 本地灌库 `data/_a500_pe_hist.csv` 自动算（A 方案） |
| 科创50/中证红利 PE | ✅ 135.5 / 8.618 | 与蛋卷交叉验证一致 |
| 纳指100(NDX) PE(TTM) | ✅ 32.85 + 分位 | 绝对值快照（agent 每日回填） |
| 纳指 Forward PE | ❌ 东财未提供 | ✅ 改走 historyofmarket 官方周频(2001-至今)，`_ndx_pe.py` 本地算 10 年分位 |

**接入形态**（Python 脚本无法直接调 MCP）：
1. A500 分位、纳指 Forward PE 10 年分位均已**本地化全自动**（`_a500_pe.py` / `_ndx_pe.py`，纯 HTTP，无需 agent/MCP）；
2. mx-ds-mcp 只承担「绝对 PE 快照 / 交叉验证」（A500、纳指 PE-TTM）：agent 每日调 `mx_index_block_finance_data`/`mx_us_finance_data` → 写 `data/_mx_valuation_latest.json` → 再跑 `python strategies/core_rotation/_signal.py`；
3. 步骤 2 可做成每日定时任务；至此**估值分位(红利/科创/A500/纳指 Forward)全部自动**，仅剩可选的 DXY/实际利率与风险开关人工。

## 5. 规则与参数唯一权威源

`rule.py` 集中了手册的全部阈值（情景三元组合、中长债利率分级、配置矩阵区间与情景中枢、单类上限、黄金超买、再平衡阈值）与标的派生逻辑；`_signal.py` 只做抓数与出报告，禁止在信号层改参数。

## 6. 为什么不做逐码 backtest / select

- 这是**组合层资产配置**（权重在六类间分配），不是单标的状态机择时，pipeline 现有 backtest 引擎逐码输出「策略 vs 买入持有」的口径不适用；
- 手册主键是估值类指标（PE 分位/股息率/Forward PE），这些没有可靠的历史序列可回测（见 §2）；
- 因此按手册定档固定 6 类（`data/_universe_core_rotation_active.json`），直接进入每日监控，不做 A/B 分级。

## 7. 待办 / 已知边界

- [x] 红利/科创估值锚自动化（蛋卷接口，每日）
- [x] A500 发布以来分位自动化（本地日频库，中证官网 index-perf，每日增量）
- [x] 纳指 Forward PE 10 年分位自动化（historyofmarket 官方周频 2001-至今，本地库）
- [ ] 每日 mx-ds-mcp 绝对PE快照 → 报告 做成定时任务（可选）
- [ ] `data/_core_navcache.json` 净值本地增量缓存（当前每次运行实时拉取，约 5–8 秒）；
- [ ] QDII 场内溢价与 TMT 拥挤度仍需人工/外部快照，报告内已留检查位；
- [ ] 每半年复核参数稳健性（手册第九章），阈值改动只改 `rule.py`。

> 免责声明：机械规则输出仅供研究参考，不构成投资建议；市场有风险，投资需谨慎。
