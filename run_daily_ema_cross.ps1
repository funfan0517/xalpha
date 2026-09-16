# 双均线趋势(EMA12/26)每日信号 - 双击运行
# 盘后运行: 读取本地行情缓存 -> 计算 EMA 排列/金叉死叉 -> 输出次日开盘操作信号
# 用法: powershell -ExecutionPolicy Bypass -File run_daily_ema_cross.ps1
$ErrorActionPreference = "Stop"
Set-Location $PSScriptRoot
$env:PYTHONIOENCODING = "utf-8"

Write-Host "[1/1] 生成双均线趋势每日信号..."
python -W ignore strategies\ema_cross\_signal.py
if ($LASTEXITCODE -ne 0) { throw "ema_cross signal failed" }

Write-Host ""
Write-Host "Done. 报告: strategies\ema_cross\daily\_signal_report.md"
Write-Host "[i] 信号用收盘(盘后), 执行用次日开盘价; 机械规则, 非投资建议."
