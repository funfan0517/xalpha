# 波动资产操作规则 v1.3(A500 5 / 纳指100 10 / 标普500 10 / 科创50 2.5 / 恒生科技 2.5 / 黄金 10 = 40pp) · 每日操作 - 双击运行

# 口径: 信号 + 记账 + 执行 **全部用场外基金复权净值**(rule.SIGNAL_MODE="otc")
# 盘后运行: 刷新场外净值(023299/270042/017641/011609/012805/000216) -> 复算六腿状态
#          (回撤阶梯/趋势闸门/季度再平衡) -> 输出「下一交易日该下单的那一笔」
# 第 2 步: V1 变种(strategies\vol_legs\v1_ma20\daily\_daily_scan.py) —— 六腿逐一判定
#          净值 vs MA20 的穿越(逐腿进出时点), 与第 1 步的组合层仓位带互不替代
# 第 3 步: V4 变种(strategies\vol_legs\v4_erp_band\daily\_daily_scan.py) —— 股债利差(ERP)通道
#          沪深300 × 10Y 国债: 利差在 MA+2σ 上方 = 低估 -> 股票 80%, 回穿均线退出 -> 50%
# 第 4 步: V5 变种(strategies\vol_legs\v5_overlap\daily\_daily_scan.py) —— 两线重合买点
#          沪深300全收益 与 二级债基指数 同锚归一, 红线跌到蓝线附近(R<=1.02) = 买点, 持有一年后卖出
# 参数权威源 strategies/vol_legs/rule.py::CFG 与 v4_erp_band/erp.py::CFG; 口径说明 strategies/vol_legs/README.md
# Usage: powershell -ExecutionPolicy Bypass -File run_daily_vol_legs.ps1
#        已有存量持仓时校准起点: ... -File run_daily_vol_legs.ps1 --hold a500=10,ndx=5,spx=5,kc50=5,hkt=5,gold=10
#        切回场内/美元指数口径: 把 rule.SIGNAL_MODE 改成 "exchange"
$ErrorActionPreference = "Stop"
Set-Location $PSScriptRoot
$env:PYTHONIOENCODING = "utf-8"
# 输出用 UTF-8, 否则中文经 subprocess 捕获会乱码
[Console]::OutputEncoding = [System.Text.Encoding]::UTF8
$OutputEncoding = [System.Text.Encoding]::UTF8

Write-Host "[1/4] Recomputing 6-leg actions on OTC NAV (A500 5 / NDX 10 / SPX 10 / STAR50 2.5 / HSTECH 2.5 / Gold 10)..."
python -W ignore strategies\vol_legs\scan.py --force @args
if ($LASTEXITCODE -ne 0) { throw "vol_legs scan failed" }

# V1 变种(MA20 穿越): 逐腿进出时点, 不接管组合层仓位带; 复用第 1 步刚刷新的场外净值缓存
Write-Host ""
Write-Host "[2/4] Recomputing V1 variant (MA20 cross, per-leg in/out) ..."
python -W ignore strategies\vol_legs\v1_ma20\daily\_daily_scan.py
if ($LASTEXITCODE -ne 0) { throw "v1_ma20 daily scan failed" }

# V4 变种(股债利差 ERP 通道): 沪深300 x 10Y 国债的 80/50/20 仓位刻度
# 自带数据刷新(中证官网 PE + 东财 10Y 中债), 与第 1 步的场外净值无关
# 真实持仓不是"上期目标位"时: ... _daily_scan.py --hold 0.30 --capital 2000000
Write-Host ""
Write-Host "[3/4] Recomputing V4 variant (ERP band, HS300 vs 10Y CGB) ..."
python -W ignore strategies\vol_legs\v4_erp_band\daily\_daily_scan.py
if ($LASTEXITCODE -ne 0) { throw "v4_erp_band daily scan failed" }

# V5 变种(两线重合买点): 沪深300全收益 vs 二级债基指数, R<=1+2% 买、持有一年卖
# 自带数据刷新(中证 H00300 + 8 只老债基累计净值), 与第 1 步的场外净值无关
# 真实已买入时: ... _daily_scan.py --buy 2026-01-05 --capital 2000000
Write-Host ""
Write-Host "[4/4] Recomputing V5 variant (HS300 TR vs bond-fund index overlap) ..."
python -W ignore strategies\vol_legs\v5_overlap\daily\_daily_scan.py
if ($LASTEXITCODE -ne 0) { throw "v5_overlap daily scan failed" }

Write-Host ""
Write-Host "Done."
Write-Host "  Report  : strategies\vol_legs\daily\_vol_legs_report.md"
Write-Host "  Snapshot: strategies\vol_legs\daily\_vol_legs_signal.json"
Write-Host "  Universe: strategies\vol_legs\daily\_universe_vol_legs_active.md"
Write-Host "  V1 MA20 : strategies\vol_legs\v1_ma20\daily\_v1_ma20_daily.md (+ .json)"
Write-Host "  V4 ERP  : strategies\vol_legs\v4_erp_band\daily\_v4_erp_band_daily.md (+ .json)  [HS300 vs 10Y CGB]"
Write-Host "  V5 重合 : strategies\vol_legs\v5_overlap\daily\_v5_overlap_daily.md (+ .json)  [HS300 TR vs bond-fund index]"
Write-Host "  Note    : signal on NAV at close, place the order BEFORE 15:00 next trading day to get"
Write-Host "            that day's NAV. QDII NAV prints T+1~T+2 -- do not cancel inside the window."
Write-Host "  Note    : all six legs trade OTC share classes only (never chase on-exchange premium);"
Write-Host "            if the QDII quota is closed, the tier is deferred, not cancelled."
Write-Host "  Note    : NAV cache = data\_otc_nav.json; codes 023299 / 270042 / 017641 / 011609 /"
Write-Host "            012805 / 000216, plus bridge funds 000051 (A500 warm-up) and 050025 (SPX warm-up)."
