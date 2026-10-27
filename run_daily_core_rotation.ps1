# 核心轮动(六类资产动态配置)每日监控 - 双击运行
# 自动抓取: 六类场内代理日线(动量) + 场外主仓净值(执行确认) + 10Y 国债收益率
# 人工项: 每周更新 data/_core_valuation.json(估值锚), 可选 data/_core_holdings.json(当前持仓)
# Usage: powershell -ExecutionPolicy Bypass -File run_daily_core_rotation.ps1
$ErrorActionPreference = "Stop"
Set-Location $PSScriptRoot
$env:PYTHONIOENCODING = "utf-8"

Write-Host "[1/1] Generating core rotation daily monitor..."
python -W ignore strategies\core_rotation\_signal.py
if ($LASTEXITCODE -ne 0) { throw "core rotation signal failed" }

Write-Host ""
Write-Host "Done. Report: strategies\core_rotation\daily\_daily_report.md ; Snapshot: strategies\core_rotation\daily\_core_daily.json"
Write-Host "[i] Intraday proxy signals (run at 14:xx) may move with the closing auction; OTC orders before 15:00 use that day's NAV per fund company."
