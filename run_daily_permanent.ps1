# 永久投资组合(8 项 · ±50% 浮动)每日最佳百分比 - 双击运行
# 自动抓取: 9 条腿的场外净值(场外真实执行口径) -> 趋势/动量打分 -> 目标权重
# 无人工项: 全部信号可由净值序列本地算出, 不需要估值锚/开关
# Usage: powershell -ExecutionPolicy Bypass -File run_daily_permanent.ps1
$ErrorActionPreference = "Stop"
Set-Location $PSScriptRoot
$env:PYTHONIOENCODING = "utf-8"
# 输出用 UTF-8, 否则中文经 subprocess 捕获会乱码
[Console]::OutputEncoding = [System.Text.Encoding]::UTF8
$OutputEncoding = [System.Text.Encoding]::UTF8

Write-Host "[1/1] Generating permanent portfolio daily weights..."
python -W ignore strategies\permanent\_signal.py
if ($LASTEXITCODE -ne 0) { throw "permanent signal failed" }

Write-Host ""
Write-Host "Done."
Write-Host "  Report  : strategies\permanent\daily\_permanent_report.md"
Write-Host "  Snapshot: strategies\permanent\daily\_permanent_signal.json"
Write-Host "  Note    : OTC subscribe/redeem by 15:00 T-day; QDII(270042/017641) NAV lags 1-2 days."
