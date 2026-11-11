# 数据源清单与已知缺陷（`data/` 的唯一说明书）

> 首次审计 2026-11-06。**任何策略引用 `data/` 之前，先读这一页**，尤其是 §5「已知缺陷」和 §6「使用禁忌」。
> 本文件只描述**数据**（字段 / 覆盖 / 维护者 / 坑），不描述策略。策略产物落在各自 `strategies/<策略>/`。

## 0. 读取顺序（强制）

```python
import _refresh_data                 # 需先把 data/ 加入 sys.path
_refresh_data.ensure_fresh()         # 已最新则只做一次日期检查（近零成本），否则联网增量拉
_ind = json.load(open("data/_indicators_hist.json", encoding="utf-8"))   # 指标 + 成交量
_lk = json.load(open("data/_long_klines.json", encoding="utf-8"))        # 收盘价
```

`strategies/tech_indicators/backtest.py::load()` 已经这么做；vol_legs 家族走 `strategies/vol_legs/data.py`（场外净值自有一套刷新）。

**不要**在策略里直接 `xa.get_daily(...)` 绕过刷新器 —— 会绕开增量校验与前复权基准变更的重取逻辑。

## 1. 场内行情（技术指标 / 量价策略）· 55 只 ETF

### 1.1 `_long_klines.json` — 2.1 MB

| 项 | 内容 |
|---|---|
| 字段 | `{code: {dates: [...], close: [...]}}`，**只有收盘价** |
| 价格口径 | **前复权市价**（`xa.get_daily`，雪球）；分红会让历史值整体平移，但**收益率连续**，对回测无害 |
| 覆盖 | 55 只，2015-01-05 ~ 2026-11-06；每只自上市日起 356 ~ 2877 根（中位 1706） |
| 维护者 | `data/_refresh_data.py`（`ensure_fresh`，增量 + 重叠窗口校验） |
| 审计结论 | 无 NaN、无负值、无重复日期；仅 2 笔单日 >20%（159915/159949 于 2024-09-30、10-08 的创业板 20% 涨停，**真实**） |

### 1.2 `_indicators_hist.json` — 14.7 MB

| 项 | 内容 |
|---|---|
| 字段 | `dates` + `MA5/10/20/60` + `DIF/DEA/HIST` + `K/D/J`（×100）+ `BOLL_UPPER/MID/LOWER` + **`volume`** |
| 覆盖 | 与 `_long_klines.json` **日期轴逐一完全相同（55 只，0 处不一致）** → 量价严格同轴 |
| 维护者 | `data/_refresh_data.py` + `_gen_indicators.py`（公式与 `xalpha.indicator` 逐点对齐，见文件内 `_meta`） |
| `volume` | 无 0、无 null。`_meta` 自述「真实成交量(手)」，但按量级推断更像**股**（563360 单日 60 亿）—— **单位未证实，只可用于比值** |
| ⚠ | **存在数量级台阶**，见 §5.1 |

### 1.3 `_us_index_long.json` / `_us_index_klines.json`

| 文件 | 键 | 字段 | 覆盖 |
|---|---|---|---|
| `_us_index_long.json` | `.NDX` `.INX` | `dates` + `close` | 1985 起（超长历史，用于极端分位） |
| `_us_index_klines.json` | `.NDX` `.INX` | `dates` + `close` | 2010 起（近 15 年，vol_legs 的 exchange 口径信号源） |

维护者：`strategies/vol_legs/data.py`。两者内容重叠但用途不同，**不要混用**。

## 2. 场外净值（vol_legs 家族）· 8 只基金

### `_otc_nav.json` — 0.5 MB

| 项 | 内容 |
|---|---|
| 字段 | `{code: {dates, nav, tot}}`；`nav` = 单位净值，`tot` = **累计净值** |
| 回测用哪个 | **`tot`**（`series_for_leg` 取 `tot`，缺则退 `nav`）。用 `nav` 会把分红当成下跌 |
| 来源 | `xalpha.fundinfo`（天天基金） |
| 维护者 | `strategies/vol_legs/data.py::refresh_otc`（七腿扫描前自动调用） |

八只代码与用途（`strategies/vol_legs/rule.py::LEGS` 是权威映射）：

| 代码 | 基金 | 起点 | 用途 |
|---|---|---|---|
| `023299` | 汇添富中证A500指数增强C | 2025-03-27 | a500 执行 |
| `000051` | 华夏沪深300ETF联接A | 2009-07-10 | hs300 执行 **+ a500 前段桥接** |
| `270042` | 广发纳斯达克100ETF联接(QDII)A | 2012-08-15 | ndx 执行 |
| `017641` | 摩根标普500指数(QDII)人民币A | 2023-04-06 | spx 执行 |
| `050025` | 博时标普500ETF联接A | 2012-06-14 | **spx 前段桥接** |
| `011609` | 易方达上证科创板50ETF联接C | 2021-03-01 | kc50 执行 |
| `012805` | 广发恒生科技ETF联接QDII-C | 2021-08-11 | hkt 执行 |
| `000216` | 华安黄金ETF联接A | 2013-08-22 | gold 执行 |

最新日期 2026-11-04 / 11-05（QDII 腿天然滞后 1~2 日，见 §5.2）。

> 桥接（`OTC_USE_PROXY_HISTORY`）只影响样本早期：用同类老基金按拼接日净值缩放，拼接日之后是真实执行标的。

## 3. 宏观与估值

| 文件 | 字段 | 覆盖 | 最新 | 更新方 |
|---|---|---|---|---|
| `_us10y.csv` | `date, value` | 1962-01-02 起，16195 行 | 2026-11-03 | 外部/手动 |
| `_us_vix.csv` | `date, value` | 1990-01-02 起，9309 行 | 2026-11-03 | 外部/手动 |
| `_etfrun_eva.csv` | `date, index_code, pe, pb, close` | 2015-05-20 起，18931 行 | 2026-11-05 | 外部 |
| `_danjuan_eva.csv` | `date, index_code, pe, pb` | 2016-10-27 起，1422 行 | 2026-10-27 | 外部 |
| `_div_yield_m.csv` | `date, index_code, dy, method, src_index` | 2015-01-31 起，994 行 | 2026-10-31 | 外部 |
| `_a500_pe_hist.csv` | `date, close, peg` | 2024-09-23 起，504 行 | 2026-10-26 | 外部（A500 指数 2024 才发布） |
| `_ndx_fwd_pe_hist.csv` | `date, value, kind` | 2001-04-27 起，1291 行 | 2026-10-23（较旧） | 外部 |

## 4. 其他

| 文件 | 性质 | 说明 |
|---|---|---|
| `_universe.md` | **人工维护**的标的池 | 133 个代码（含场外 / 港股 / 美股 / 债券）；`strategies/tech_indicators/_pool.py` 只取**前四类 55 只**，其余不进技术指标回测 |
| `_core_valuation.json` | **人工每周维护** | 核心估值底稿 |
| `_mx_valuation_latest.json` | **外部 agent 回填** | 移动/改名会静默打断仓库外流程 |
| `_breadth_daily.json` | `data/_refresh_breadth.py` 生成 | 市场广度（0.24 MB，2026-11-02） |
| `_bt_caches/` | 回测缓存（23 csv + 1 json） | 可随时删，重跑重建 |
| `_daily_digest*.{html,json}`、`_daily_digest_assets/` | **产物**（非数据源） | 由 `pipeline/daily_digest.py` 生成 |
| `_refresh_data.py` / `_refresh_breadth.py` | 维护脚本 | 本目录唯一的"代码" |

## 5. 已知缺陷（使用前必读）

### 5.1 成交量存在数量级台阶 —— 绝对值不可用

多只 ETF 的**年度中位成交量**年际跳变 43~91 倍，且会回落（不是份额增长的自然结果）：

| 代码 | 台阶 | 倍率 |
|---|---|---|
| 510210 | 2018 → 2019 | 89× |
| 159949 | 2017 → 2018 | 91× |
| 501025 | 2019 → 2020 | 65× |
| 512010 | 2017 → 2018 | 43× |
| **510300** | — | **1.8×（干净）** |
| **510050** | — | **2.3×（干净）** |

成因是早期数据单位/口径不一致或分段拼接。**55 只里 17 只有台阶，其余 38 只干净**（判据见 §7 自检）。

**唯一安全的用法是 20 日内的相对量比**（台阶在短窗口内抵消）；若必须用长窗口，先按「量/前 20 日均量 > 8 且次日回落」标出断点并剔除该日。

### 5.2 QDII 腿：日期错位 + 场内溢价

| 对比 | 日收益相关（shift 0 / +1） | 判断 |
|---|---|---|
| 纳指 `270042` vs ETF `513100` | 0.17 / **0.57** | 晚 1 天 |
| 标普 `017641` vs ETF `513500` | 0.14 / **0.63** | 晚 1 天 |
| 恒生科技 `012805` vs ETF `513180` | **0.91** / 0.09 | 正常（不错位） |

场外 QDII 净值 T+1~T+2 公布；即使平移对齐，剩余相关只有 0.6 —— 差额来自**场内溢价**（额度限制下纳指 ETF 长期溢价 5~10%）。
**结论：QDII 腿的场外净值不能与 ETF 的量直接按日期对齐**，做量价组合必须先平移一天并接受噪声。

### 5.3 两套价格口径，不能混用

| 腿 | 场外净值年化 | ETF 市价年化 | 差 |
|---|---|---|---|
| 沪深300 | +3.25% | +2.83% | +0.42pp/年 |
| 科创50 | +0.11% | -0.53% | +0.65pp/年 |
| 黄金 | +10.90% | +11.14% | -0.24pp/年 |

差在费率、分红再投、折溢价。**一个策略内部只能用一个口径**：v11/v11-gate 全用场外净值，v6 全用 ETF 价。
跨策略比数字前，先确认两边口径。

## 6. 使用禁忌

| 想做的事 | 能不能 | 正确做法 |
|---|---|---|
| 读收盘价 | ✅ | `_long_klines.json`（前复权市价，收益率连续） |
| 算均线/MACD/KDJ/BOLL | ✅ | 直接用 `_indicators_hist.json`，不要自己重算（口径已对齐 `xalpha.indicator`） |
| 算「当日量 / 5 日均量」 | ✅ | 短窗比值，台阶自动抵消 |
| 用绝对成交量做阈值 | ❌ | 台阶会让 2019 年前的量整体偏小两个数量级 |
| 跨年比较成交量、全历史量分位 | ❌ | 同上 |
| 把 QDII 场外净值与 ETF 量按日期对齐 | ❌ | 先 `shift(1)` 对齐，并接受相关只有 0.6 |
| 场外净值用 `nav` 做收益 | ❌ | 用 `tot`（累计净值），否则分红日会被算成下跌 |
| 在策略里直接 `xa.get_daily` | ❌ | 走 `_refresh_data.ensure_fresh()`，否则绕开前复权基准校验 |
| 删除/移动 `_mx_valuation_latest.json`、`_core_valuation.json` | ❌ | 仓库外流程/人工习惯依赖它们，移动后**静默**读到旧值 |

## 7. 快速校验（复制即跑）

```python
import json
import numpy as np

lk = json.load(open("data/_long_klines.json", encoding="utf-8"))
ind = json.load(open("data/_indicators_hist.json", encoding="utf-8"))
ind.pop("_meta", None)

assert all(ind[k]["dates"] == lk[k]["dates"] for k in ind), "两库日期轴不一致！"
assert not [k for k, v in lk.items() if len(v["dates"]) != len(set(v["dates"]))], "有重复日期"

for k, v in ind.items():                       # 成交量台阶自检：>8 倍的相邻日跳变
    vol = np.array(v["volume"], dtype=float)
    jumps = int((vol[1:] / vol[:-1] > 8).sum())
    if jumps > 3:
        print(f"[台阶] {k}: {jumps} 次 >8 倍日跳变 -> 该标的的量只能做短窗比值")

print(f"codes={len(lk)}  dates={min(v['dates'][0] for v in lk.values())} ~ "
      f"{max(v['dates'][-1] for v in lk.values())}")
```

## 8. 变更记录

| 日期 | 变更 |
|---|---|
| 2026-11-06 | 建立本文件：首次全量审计（结构 / 覆盖 / 两库一致性 / 价格异常 / 量台阶 / QDII 错位 / 口径差） |
