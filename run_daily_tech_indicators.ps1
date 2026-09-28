# 技术指标(大策略家族)每日操作 - 双击运行
# 盘后运行: 刷新行情 -> 固定四策略 MACD / V3 / V6 / V7 各跑一遍 -> 输出今日操作建议
# 用法: powershell -ExecutionPolicy Bypass -File run_daily_tech_indicators.ps1
$ErrorActionPreference = "Stop"
Set-Location $PSScriptRoot
$env:PYTHONIOENCODING = "utf-8"

Write-Host "[1/1] 生成技术指标每日操作建议 (MACD / V3 / V6 / V7)..."
python -W ignore strategies\tech_indicators\daily\_daily_recommend.py
if ($LASTEXITCODE -ne 0) { throw "tech_indicators daily failed" }

Write-Host ""
Write-Host "Done. 报告: strategies\tech_indicators\daily\_daily_recommend.md"
Write-Host "[i] 固定四策略各跑一遍, 全池 55 只不做 A/B 分级; 信号用收盘(盘后), 执行用次日开盘."
Write-Host "[i] 机械规则, 非投资建议."
