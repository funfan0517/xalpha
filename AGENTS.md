# XAlpha Agent Platform Guidelines

Welcome to `xalpha`. This document defines the operational rules for AI agents and contributors. 

**Core Identity:** `xalpha` is not just a quantitative finance Python library—it is an **AI Agent Platform**. Agents are expected to use natural language instructions to automatically write `xalpha` code, perform financial data mining, backtest strategies, and generate analytical reports.

## 1. The Agentic Workflow
When a user asks for financial analysis or data mining via natural language:
- **Understand the Domain:** Use `xalpha.universal`, `xalpha.fundinfo`, and `xalpha.policy` as your primary tools.
- **Write Scripts:** Do not just explain how to do it; write and execute Python scripts utilizing `xalpha` to fetch real data, compute metrics (e.g., XIRR, volatility, correlation), and save results.
- **Be Proactive:** If a data source (like Investing.com or Xueqiu) throws an error or requires an ID mapping, autonomously debug and ask the user for the fix plan.
- **Synthesize:** Present the final financial analysis clearly to the user, backed by the data you mined.

## 2. Core Compatibility Contracts
Code written or modified by agents MUST be broadly compatible across the scientific Python ecosystem:
- **Pandas 1.x up to 3.x:** Handle frequency format changes (`"M"` vs `"ME"`). Always wrap HTML strings in `io.StringIO()` before `pd.read_html()`. Use explicitly strict type casting (`.astype(float)`) to avoid `LossySetitemError`.
- **Numpy 1.x through 2.x:** Avoid deprecated aliases like `np.float`. Use `float` or `np.float64`.

## 3. Data Scraping & API Resilience
`xalpha` heavily relies on web scraping (`beautifulsoup4`) and API endpoints. 
- **Robust Parsing:** Upstream HTML changes frequently. Avoid fragile exact string matches `soup.find(string="text")`. Use iterative tag searching and `get_text(strip=True)`.
- **Graceful Fallbacks:** If an endpoint fails (e.g., anti-scraping on Investing.com), agents should implement or utilize fallback logic (e.g., JSON APIs vs HTML parsing) and use the `rget` decorator for network resilience.
- **Never Break the DataFrame:** Ensure that any updated scraping logic exactly restores the original DataFrame schema expected by `xalpha`.

## 4. Local Caching
`xalpha` uses local caching (CSV/SQL) for performance.
- When expanding data classes (e.g., adding a new attribute to `fundinfo`), agents MUST update both `_save_csv/_sql` and `_fetch_csv/_sql`.
- Handle legacy caches defensively using `.get("new_key", "default")`.

## 5. Dashboards & Visualization
When generating HTML reports or dashboards (e.g., QDII prediction pages):
- **Rich Aesthetics:** Use modern, light-themed layouts, DataTables, and CSS variables. Keep Python focused on data; offload rendering logic to JS/CSS.

## 6. Code Quality & CI/CD
- **Testing:** Ensure tests pass using `pytest`. Use `pytest.importorskip` for optional dependencies.
- **Testing Efficiency:** Running the entire/global test suite is very heavy. Minimize running global tests, and prefer running targeted tests (e.g. `pytest tests/test_file.py::test_func`) to verify changes.
- **Linting:** Enforce `black` formatting and strict adherence to a **10.00/10** Pylint score for the `xalpha/` directory.

## 7. Development Mindset
1. **Atomic & Precise Changes:** When fixing bugs in the library itself, make the smallest possible change. Avoid unnecessary refactoring of legacy code.
2. **Data-Driven:** When asked to analyze, write the code, run it, and let the data speak. 
3. **Self-Healing & Fail-Fast:** If you encounter a `KeyError` or `NoneType` during data fetching, investigate the upstream response and patch the parsers or input normalizers autonomously. **Avoid over-protective code** (e.g., blanket try-except or returning empty DataFrames) that swallows original errors. Let it fail naturally so the root cause is visible, then fix it at the source.

## 8. 目录与文件放置约定

**判断依据是「文件的性质」，不是「谁生成的」。** 一个策略跑出来的产物，未必属于该策略。

### 8.1 三类文件的落位

| 文件性质 | 落位 | 判据 | 现存例子 |
|---|---|---|---|
| **数据类** | `data/` | 原始/参考数据、外部注入的快照、人工维护的输入、行情库、外部数据缓存。**判据是「别的策略将来可能也会用」** | `_universe.md`（主标的池）、`_long_klines.json`（十年行情库）、`_a500_pe_hist.csv`、`_ndx_fwd_pe_hist.csv`、`_mx_valuation_latest.json`（外部 agent 回填）、`_core_valuation.json`（人工每周维护）、`_bt_caches/` |
| **策略类** | `strategies/<策略名>/` | ①**策略本体**：`rule.py`（参数权威源）、`factors.py`、`engine.py`、`data.py`、`backtest.py`、`scan.py`；②该策略的**产物**：回测产物 `_*_bt.jsonl`、分级名单 `_*_active.{md,json}`、扫描产物 `_*_scan_out.jsonl`、行情缓存 `_*_klines.json`、报告与图 `_*_report.md` / `.png` / `.html`；③该策略的**专项分析**：脚本 + 同名报告 | `strategies/lights/`、`strategies/core_rotation/`、`strategies/ema_cross/`、`strategies/momentum_rotation/` |
| **跨策略共享工具** | `strategies/` 根目录 | 被多个策略或分析脚本复用的工具 | `_tune.py`（参数搜索）、`_walkforward.py`（分段验证）、`_merge_verify.py`（产物逐字段比对）、`_probe_data.py`（数据源探针） |

> **反直觉但重要**：外部写入或人工维护的文件（如 `_mx_valuation_latest.json`、`_core_valuation.json`）属于**数据类**，留在 `data/`。移动它们会**静默打断仓库外的自动化流程 / 人的使用习惯**，而且失败时没有明显报错（只是读到旧文件）。

### 8.2 策略产物的路径写法（强制）

策略产物一律用**相对脚本自身**的路径，**禁止写死绝对路径**：

```python
# 正确
OUT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "_lights_bt.jsonl")
# 禁止
OUT = "g:/xalpha/data/_lights_bt.jsonl"
```

`pipeline/strategies.json` 里的路径相对**仓库根**，指向策略目录，例如 `"raw_out": "strategies/lights/_lights_bt.jsonl"`。

### 8.3 迁移某个策略的产物时

**逐个策略原子迁移** —— 一个策略的「移动文件 + 改路径 + 端到端验证」一次做完，避免中途停下留下半坏状态。检查清单：

1. 移动产物文件到 `strategies/<策略名>/`
2. 改该策略所有脚本的路径常量 —— 注意**两种形式**：`os.path.join(_ROOT, "data", ...)` 与写死的绝对路径串
3. 改 `pipeline/strategies.json` 中该策略的 `raw_out` / `out_active_md` / `out_active_json`
4. 改 README、脚本 docstring、以及 `run_daily_*.ps1` 里的路径引用
5. 跑一次该策略全流程（回测 → 报告 → 分级 → 扫描）验证产物落位

> ⚠ 迁移会**暴露隐藏的路径耦合** —— 例如直接读 `data/` 下产物的分析脚本（用 `_DIR/../data/` 之类的相对层级），只读代码看不出来，**必须真跑一遍才会暴露**。所以第 5 步不可省。
