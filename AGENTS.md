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
| **策略类** | `strategies/<策略名>/` | ①**策略本体**：`rule.py`（参数权威源）、`factors.py`、`engine.py`、`data.py`、`backtest.py`、`scan.py`；②该策略的**产物**：回测产物 `_*_bt.jsonl`、分级名单 `_*_active.{md,json}`、扫描产物 `_*_scan_out.jsonl`、行情缓存 `_*_klines.json`、报告与图 `_*_report.md` / `.png` / `.html`；③该策略的**专项分析**：脚本 + 同名报告 | `strategies/tech_indicators/v10_lights/`、`strategies/vol_legs/`、`strategies/dividend/` |
| **跨策略共享工具** | `strategies/` 根目录 | 被多个策略或分析脚本复用的工具 | `_tune.py`（参数搜索）、`_walkforward.py`（分段验证）、`_merge_verify.py`（产物逐字段比对）、`_probe_data.py`（数据源探针）、`_panel.py`（面板口径唯一实现 + 停牌污染自检，`python strategies/_panel.py` 可直接跑） |

> **反直觉但重要**：外部写入或人工维护的文件（如 `_mx_valuation_latest.json`、`_core_valuation.json`）属于**数据类**，留在 `data/`。移动它们会**静默打断仓库外的自动化流程 / 人的使用习惯**，而且失败时没有明显报错（只是读到旧文件）。

### 8.2 策略产物的路径写法（强制）

策略产物一律用**相对脚本自身**的路径，**禁止写死绝对路径**：

```python
# 正确
OUT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "backtest", "_lights_bt.jsonl")
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))   # 仓库根：推导，不写死
# 禁止（项目挪走就崩）
OUT = "g:/xalpha/strategies/tech_indicators/v10_lights/backtest/_lights_bt.jsonl"
ROOT = "g:/xalpha"
```

`pipeline/strategies.json` 里的路径相对**仓库根**，指向策略目录，例如 `"raw_out": "strategies/tech_indicators/v10_lights/backtest/_lights_bt.jsonl"`。

**启动脚本**同理，用 `$PSScriptRoot` 而非写死盘符：

```powershell
Set-Location $PSScriptRoot      # 正确
Set-Location g:\xalpha          # 禁止
```

**写进产物的路径**（如分级名单 `_*_active.md` 的「来源」行、`.json` 的 `source` 字段）也用**相对仓库根**的写法 —— 绝对路径会让产物换台机器就打不开。

### 8.3 迁移某个策略的产物时

**逐个策略原子迁移** —— 一个策略的「移动文件 + 改路径 + 端到端验证」一次做完，避免中途停下留下半坏状态。检查清单：

1. 移动产物文件到 `strategies/<策略名>/`
2. 改该策略所有脚本的路径常量 —— 注意**两种形式**：`os.path.join(_ROOT, "data", ...)` 与写死的绝对路径串
3. 改 `pipeline/strategies.json` 中该策略的 `raw_out` / `out_active_md` / `out_active_json`
4. 改 README、脚本 docstring、以及 `run_daily_*.ps1` 里的路径引用
5. 跑一次该策略全流程（回测 → 报告 → 分级 → 扫描）验证产物落位

> ⚠ 迁移会**暴露隐藏的路径耦合** —— 例如直接读 `data/` 下产物的分析脚本（用 `_DIR/../data/` 之类的相对层级），只读代码看不出来，**必须真跑一遍才会暴露**。所以第 5 步不可省。

### 8.4 策略目录内部再分五类

每个 `strategies/<策略名>/` 内部按职能再分五类：

| 类别 | 落位 | 内容 | 能否移动 |
|---|---|---|---|
| **① 规则** | 策略包**根** | `rule.py`（参数权威源）、`factors.py`、`engine.py`、`data.py` | **不能** —— 它们是包的可导入核心，被大量文件 `import`（lights 有 21 处会失败），挪进子目录会让所有引用方的 `sys.path` 失效 |
| **② 数据** | `<策略>/data/` | 该策略自己的行情缓存等 | 能（改路径常量） |
| **③ 回测** | `<策略>/backtest/` | 回测产物 `_*_bt.jsonl` + 报告 `_*_report.md` / `.png` / `.html` | 能 |
| **④ 测试调优** | `<策略>/research/` | 专项分析脚本 + 同名报告；**内部再按课题分子目录**（脚本与该课题的 `.md`/`.png`/`.jsonl` 同放一处，如 `vol_legs/research/{best_by_leg,beat_best}/`） | 能（每个改 1 行 `sys.path`；下移一层后 `_RES=dirname(_HERE)`、`_VL=dirname(_RES)` 要同步补） |
| **⑤ 每日推荐操作报告** | `<策略>/daily/` | 扫描产物、分级名单、信号报告与看板 | 能 |

> **入口脚本留在根**：`backtest.py`、`scan.py`、`_reportbt.py`、`_scan_report.py`。它们被 `pipeline/strategies.json` 的 `engine` 字段按**路径**调用，或被其他模块 `import`（如 `research/_tune.py` 会 `import backtest`），移动它们的收益远小于代价。

**参考实现**：`strategies/tech_indicators/v10_lights/`（原独立的 `lights`，2026-10-28 作为变种 v10 并入家族）与同族 `v9_right_side/` 已完成该结构，迁移其余策略时照搬。

> ⚠ **迁移后必须实跑验证**，不要只看代码。这一轮踩到的坑：
> 1. `pipeline/strategies.json` 里除了显眼的 `raw_out` / `out_active_md` / `out_active_json`，还有 **`backtest.report`** 和 **`daily.signal_report` / `daily.dashboard`** 这几个容易漏掉的字段 —— 它们是**回读运行日志**时才暴露的（日志里打印的仍是旧路径）。
> 2. `pipeline/run_flow.py` 的 `--report` 分支原先**从产物目录推断报告生成器**（假设 `_reportbt.py` 与报告产物同目录）。产物一旦移进 `backtest/`，这个假设就失效 —— 已改为**从 `engine` 目录推断**（引擎留在策略包根）。**改动布局时要留意这种"从某个文件位置反推另一个文件位置"的隐式耦合**，它不会报语法错，只会在运行时找不到文件。

## 9. 新策略上线纪律：默认不进每日操作报告

**规则（2026-11-19 定）：新策略 / 新变种默认 `daily.enabled = false`。未经用户明确同意，不得写进每日操作报告（看板）。**

「跑产物」与「进报告」是两件事，但**不出报告就不随每日操作跑**（2026-11-24 用户规则）：

| | 由什么控制 | 新策略的默认 |
|---|---|---|
| 看板（每日操作报告）里出不出这张卡 | `daily.enabled` 且 `daily.card != false`（`daily_digest.py::_card_on`） | **false**（不进报告） |
| 每日脚本跑不跑、产物刷不刷新 | 同一判据：**上板才调度**。`run_daily_all.ps1` 只遍历出卡的策略，并按 `daily.runner` 去重（同一 .ps1 只跑一次） | **不跑** |

> 2026-11-24 之前是「card=false 仍照跑」，靠 card=false 的家族条目（vol_legs / growth_div_rotation）持有调度权。该口径已作废：**不上看板 = 不跑**。必须刷新的产物，把调度权挂到**出卡的家族卡**上（vol_variants / gd_v5 / dividend / tech_indicators / bond_cash_v7），由 `run_daily_all.ps1` 的 runner 去重保证只执行一次。

### 9.1 上板（进每日操作报告）的流程

1. **必须有用户的明确指令**（原话如「把 vN 加进看板 / 上板 / 进每日操作」）。自己判断「策略有效、应该上板」不算同意。
2. 把 `daily.enabled` 改成 `true`，并写 `daily.enabled_note`：**`YYYY-MM-DD 用户同意（原话摘要或出处）`** —— 这一行是唯一的凭据，没有它下次没人知道这张卡为什么在报告里。
3. 补齐 `daily.signal_report` / `daily.signal_json` / `daily.block` 与 `pipeline/daily_digest.py` 的 `PARSERS` 注册。
4. 跑 `python pipeline/check_daily_config.py`（E3/E4 会卡住漏写开关与无凭据上板）。

### 9.2 下板（撤出报告）同样留痕

`daily.enabled = false` + `enabled_note` 写清「date 用户要求撤出/为什么撤出/产物去哪看」。
**产物脚本不要一起删** —— 下板只是不出卡、不进每日调度，研究数据仍可手动跑（参考 v2 / v4~v6 的写法）。

只撤卡片、保留条目（`enabled=true` + `card=false`）也可以，但该条目**不能配 `daily.runner`**（`check_daily_config.py` 的 E5 会拦）：它的产物必须由某张出卡的家族卡代跑，否则永远不会刷新。

### 9.3 为什么要有这条

- `daily.enabled` 的原语义是「`is not False` 即上板」，**漏写键 = 默认上板**。新策略一旦忘写，就会静默挤进报告，把当天的操作版面稀释掉，而且没有任何报错。
- 报告是「每天照着做」的东西，任何未经验证的策略混进去都是**执行风险**，不是展示问题。
- 历史上已经踩过：2026-11-19 新增 `bond_cash` 的 v4/v5/v6 三个研究变种，回测均未跑赢 v0 / 固定中性，创建时默认带出了卡片；同日按本规则改为 `enabled=false`（产物仍由 `run_daily_bond_cash.ps1` 第 5/6/7 步刷新）。

### 9.4 强制校验（写进 CI 习惯）

```powershell
python pipeline/check_daily_config.py          # E3 缺显式 enabled / E4 上板无凭据 / E5 不出卡却配 runner，任一即失败
```

校验通过时会打印**当前上板清单 + 不跑的清单 + 共享 runner 提示**，改完 `strategies.json` 顺手看一眼，比翻 JSON 靠谱。

### 9.5 不出卡就不跑（2026-11-24 用户规则）

- `run_daily_all.ps1` 的调度名单 = 上板策略（`enabled=true` 且 `card != false`），其余一律不执行，只在头部打印一行清单。
- 同一 `daily.runner` 被多张出卡卡共享时**去重执行一次**（家族脚本一次聚合多张卡的产物，不可能也不该按卡重复跑）。
- 家族 runner 只跑**看板要读的产物**（2026-11-25 用户要求「和看板无关的策略不要执行」）：vol_legs 只跑 v14~v20 + 七张长期图 + 5y 快照；bond_cash 只跑 V7；tech 跑 panel 自检 + v9/v10 scan + 每日汇总 + V17；dividend 只跑 V4/V9 的 signal json；gd 只跑 data.py + v5。
- 其余产物全部退出每日流程，**不再有 `-Full` 开关**（2026-11-25 用户要求「-Full 与默认一样」）：v2/v3/v4/v6/v9/v12 的 daily_scan 与图、`_3y.png`、bond_cash v0/v1~v6/v8、v9/v10 的 md/html/png、红利逐标的图/dashboard/独立 fit、gd 的 v0/v2。
- 想刷新这些不出卡的产物：手动跑对应命令（都写在各 `run_daily_*.ps1` 的头注释里）；不要再给它们配 `daily.runner`。
- 家族调度权的现行归属：板块2 `vol_variants` → `run_daily_vol_legs.ps1`；板块3 `gd_v5` → `run_daily_growth_div_rotation.ps1`；板块4 `dividend` → `run_daily_dividend.ps1`；板块5 `tech_indicators` → `run_daily_tech_indicators.ps1`；板块6 `bond_cash_v7` → `run_daily_bond_cash.ps1`。
- 板块1 `permanent` **无 runner**（2026-11-25 用户要求）：这张卡只是「基准% × 各腿最优策略仓位%」的择时汇总，不展示 §2 的 ±50% 倾斜权重，所以不随每日操作跑 —— `daily.runner` 置空，卡片只读其它策略的 `daily/_*_daily.json`，`_permanent_signal.json`（base 快照）需要时手动 `run_daily_permanent.ps1`。

## 10. 看板双版本必须同步

**规则（2026-11-24 定）：改 `data/_daily_digest.html`（正式版）时，必须顺手同步 `data/_daily_digest_intraday.html`（盘中预演版）。两版除「数据源是盘中数据」外，结构、列名、文案、样式保持一致。**

改动落在哪、两版怎么同步：

| 改动位置 | 预演版是否自动跟上 | 要做什么 |
|---|---|---|
| `pipeline/daily_digest.py` 的 `_HTML_TMPL`（CSS / JS / 渲染器） | **是** —— 预演版共用同一份模板（`dd._HTML_TMPL`）与板块定义（`dd._blocks`） | 仍要**重跑**才有新 HTML |
| `pipeline/daily_digest.py` 的 `parse_*`（列 / 摘要 / 说明） | **是** —— 预演版直接复用 `dd.PARSERS` | 同上 |
| `pipeline/strategies.json` 的 `blocks`（标题 / desc / note） | **是** | 同上 |
| `intraday_preview.py` 里硬编码的口径说明（`_BANNER` / `_mark*` 文案） | 否 | 手工同步 |

```powershell
python pipeline/daily_digest.py         # 正式版：归档 + 重绘 data/_daily_digest.html
python pipeline/intraday_preview.py     # 预演版：只写 data/_daily_digest_intraday.html
```

验收：两版都 grep 一遍新列名 / 新文案（例如 `总资产最优占比`、`col_cls`），并确认板块1 卡片的 `cols` 与 `col_cls` 一致。

> 不要用 `--html-only` 假装同步 —— 它只重绘正式版，预演版还是旧的，`generated` 时间会露馅（预演版首行数据里带 `"intraday": true`，日期只有当天一天）。
> 预演版**不写任何数据文件**：策略产物进 `G:\xalpha_preview_*` 临时树、跑完即删，不碰 `data/_daily_digest.json`、不归档历史、不复制 assets（见 §1 的既有约定）。
