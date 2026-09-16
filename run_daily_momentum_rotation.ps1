# 全球相对动量ETF轮动(六类资产)每日信号 - 双击运行
# 先刷新十年行情库(xueqiu), 再读库生成调仓信号(只读本地库, 无前视)
# Usage: powershell -ExecutionPolicy Bypass -File run_daily_momentum_rotation.ps1
$ErrorActionPreference = "Stop"
Set-Location $PSScriptRoot
$env:PYTHONIOENCODING = "utf-8"

Write-Host "[1/2] 刷新十年行情库(xueqiu)..."
python -W ignore strategies\momentum_rotation\fetch.py
if ($LASTEXITCODE -ne 0) { throw "行情刷新失败" }

Write-Host "[2/2] 生成动量轮动每日调仓信号..."
python -W ignore strategies\momentum_rotation\scan.py
if ($LASTEXITCODE -ne 0) { throw "信号生成失败" }

Write-Host ""
Write-Host "Done. 报告: strategies\momentum_rotation\daily\_signal_report.md ; 快照: strategies\momentum_rotation\daily\_mom_signal.json"
Write-Host "[i] 信号用场内代理收盘(盘后); 执行用场外主仓T日15:00前净值; 机械规则, 非投资建议."
