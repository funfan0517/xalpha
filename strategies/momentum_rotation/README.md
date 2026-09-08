# momentum_rotation · 全球相对动量 ETF 轮动（唯一保留版本）

> 定稿（2026-09-08）：经与 B（A股修订版）、C（绝对动量）同源十年对决后，仅保留 **A**。
> 十年同源（xueqiu 前复权、SCAN34 池、统一引擎）：A 年化 +17.3%（夏普 0.66）＞ B +10.6% ＞ C +4.7%。

## 规则

1. 再平衡：每 **21 个交易日**（≈月度），当日收盘算信号、收盘价换仓，当日收益归旧仓（无前视）。
2. 动量：每只 `momentum = close[t] / close[t-120] - 1`，降序排名。
3. **MA20 过滤**：从动量排名最高开始，选第一个 `close ≥ MA20` 的标的（**仅持 1 只**）。
4. 无候选（全部跌破 MA20 / 历史不足）→ **空仓现金**。
5. 成本：卖出 ≤7 交易日收 1.5%（惩罚性赎回费），7 日外 0%（月频基本不触发）；买入 0%。
6. 标的池：`scan_universe.md` 34 只场内 ETF/LOF，动态入选（上市 ≥ lookback+MA=140 交易日）。

## 十年回测（官方，数据 xueqiu `data/_long_klines.json`）

| 段 | 年化 | 最大回撤 | 夏普 | 总收益 | 基准年化 |
|---|---|---|---|---|---|
| 十年全期 | ~+17% | ~-40% | ~0.66 | ~+480% | ~+9.4% |
| 2016~2021H1 | ~+24% | -35% | 0.89 | — | +13.9% |
| 2021H2~2026 | ~+10% | -40% | 0.46 | — | +4.6% |

（以 `python strategies/momentum_rotation/backtest.py` 输出为准；高波动动量策略，非投资建议。）

## 文件

- `fetch.py`：抓 xueqiu 日线 → `data/_long_klines.json`（默认 2015-01 起；`python fetch.py <codes> <start> <out>` 可扩展）
- `rule.py`：SCAN34 池与参数定义
- `backtest.py`：官方 A 回测（输出 `report.md` / `data/_mom_out.json` / `_mom_nav.png`）
- 运行：`python strategies/momentum_rotation/backtest.py`
