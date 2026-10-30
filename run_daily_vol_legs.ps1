# 波动资产操作规则 v1.3(A500 5 / 纳指100 10 / 标普500 10 / 科创50 2.5 / 恒生科技 2.5 / 黄金 10 = 40pp) · 每日操作 - 双击运行

# 口径: 信号 + 记账 + 执行 **全部用场外基金复权净值**(rule.SIGNAL_MODE="otc")
# 盘后运行: 刷新场外净值(023299/270042/017641/011609/012805/000216) -> 复算六腿状态
#          (回撤阶梯/趋势闸门/季度再平衡) -> 输出「下一交易日该下单的那一笔」
# 第 2 步: V3 变种(strategies\vol_legs\v3_macd_boll\daily\_daily_scan.py) —— 六腿逐一判定
#          MACD 零轴上金叉 + 布林开口放大 + 收盘上穿上轨 = 买入 / 死叉或跌破中轨 = 卖出
#          布林窗口按 ER(20) 在 20<->30 之间滞回; 与第 1 步的组合层仓位带互不替代
# 已退出默认流程(2026-10-30, 需要时手动跑):
#   python strategies\vol_legs\v1_ma20\daily\_daily_scan.py          # V1 MA20 穿越
#   python strategies\vol_legs\v4_erp_band\daily\_daily_scan.py      # V4 股债利差 ERP 通道
# 参数权威源 strategies/vol_legs/rule.py::CFG 与 v3_macd_boll/macd_boll.py; 口径说明 strategies/vol_legs/README.md
# Usage: powershell -ExecutionPolicy Bypass -File run_daily_vol_legs.ps1
#        已有存量持仓时校准起点: ... -File run_daily_vol_legs.ps1 --hold a500=10,ndx=5,spx=5,kc50=5,hkt=5,gold=10
#        切回场内/美元指数口径: 把 rule.SIGNAL_MODE 改成 "exchange"
$ErrorActionPreference = "Stop"
Set-Location $PSScriptRoot
$env:PYTHONIOENCODING = "utf-8"
# 输出用 UTF-8, 否则中文经 subprocess 捕获会乱码
[Console]::OutputEncoding = [System.Text.Encoding]::UTF8
$OutputEncoding = [System.Text.Encoding]::UTF8

Write-Host "[1/2] Recomputing 6-leg actions on OTC NAV (A500 5 / NDX 10 / SPX 10 / STAR50 2.5 / HSTECH 2.5 / Gold 10)..."
python -W ignore strategies\vol_legs\scan.py --force @args
if ($LASTEXITCODE -ne 0) { throw "vol_legs scan failed" }

# V3 变种(MACD+BOLL 自适应窗口): 逐腿进出时点, 不接管组合层仓位带; 复用第 1 步刚刷新的场外净值缓存
Write-Host ""
Write-Host "[2/2] Recomputing V3 variant (MACD+BOLL breakout, per-leg in/out) ..."
python -W ignore strategies\vol_legs\v3_macd_boll\daily\_daily_scan.py
if ($LASTEXITCODE -ne 0) { throw "v3_macd_boll daily scan failed" }

Write-Host ""
Write-Host "Done."
Write-Host "  Report  : strategies\vol_legs\daily\_vol_legs_report.md"
Write-Host "  Snapshot: strategies\vol_legs\daily\_vol_legs_signal.json"
Write-Host "  Universe: strategies\vol_legs\daily\_universe_vol_legs_active.md"
Write-Host "  V3 MACD+BOLL: strategies\vol_legs\v3_macd_boll\daily\_v3_macd_boll_daily.md (+ .json)"
Write-Host "  Note    : signal on NAV at close, place the order BEFORE 15:00 next trading day to get"
Write-Host "            that day's NAV. QDII NAV prints T+1~T+2 -- do not cancel inside the window."
Write-Host "  Note    : all six legs trade OTC share classes only (never chase on-exchange premium);"
Write-Host "            if the QDII quota is closed, the tier is deferred, not cancelled."
Write-Host "  Note    : NAV cache = data\_otc_nav.json; codes 023299 / 270042 / 017641 / 011609 /"
Write-Host "            012805 / 000216, plus bridge funds 000051 (A500 warm-up) and 050025 (SPX warm-up)."
Write-Host "  Note    : V1 (MA20) / V4 (ERP band) are out of the daily flow since 2026-10-30;"
Write-Host "            run their daily\_daily_scan.py by hand if needed."
