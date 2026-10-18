# 右侧买入策略每日扫描 - 双击运行（盘后）
# 流程: 刷新公用行情库 -> 全池五票打分 -> 输出「今日达到右侧买点」名单 + 差一票观察名单
# 用法: powershell -ExecutionPolicy Bypass -File run_daily_right_side.ps1
$ErrorActionPreference = "Stop"
Set-Location $PSScriptRoot
$env:PYTHONIOENCODING = "utf-8"

Write-Host "[1/2] 扫描基金池右侧买点（五票确认）..."
python -W ignore strategies\right_side\scan.py
if ($LASTEXITCODE -ne 0) { throw "right_side scan failed" }

Write-Host "[2/2] 生成每日操作报告..."
python -W ignore strategies\right_side\_scan_report.py
if ($LASTEXITCODE -ne 0) { throw "right_side report failed" }

Write-Host ""
Write-Host "Done. 报告: strategies\right_side\daily\_right_side_report.md"
Write-Host "[i] 5 票全过才是买点, 4 票只是观察; 不在 4 票时提前埋伏（那等于左侧）。"
Write-Host "[i] 信号按当日收盘判定, 次日收盘执行; 机械规则, 非投资建议。"
