# 成长/红利风格轮动 每日信号 - 双击运行
# 盘后运行: 增量刷新指数行情 -> 计算风格比值/均线/分位 -> 输出次日开盘操作信号
# 用法: powershell -ExecutionPolicy Bypass -File run_daily_growth_div_rotation.ps1
$ErrorActionPreference = "Stop"
Set-Location $PSScriptRoot
$env:PYTHONIOENCODING = "utf-8"

Write-Host "[1/2] 增量刷新指数行情缓存..."
python -W ignore strategies\growth_div_rotation\data.py
if ($LASTEXITCODE -ne 0) { throw "行情刷新失败" }

Write-Host "[2/2] 生成成长/红利风格轮动每日信号..."
python -W ignore strategies\growth_div_rotation\scan.py
if ($LASTEXITCODE -ne 0) { throw "信号生成失败" }

Write-Host ""
Write-Host "Done. 报告: strategies\growth_div_rotation\daily\_signal_report.md ; 快照: strategies\growth_div_rotation\daily\_gd_signal.json"
Write-Host "[i] 信号用指数收盘(盘后), 执行用次日开盘价(场外按当日15:00前净值); 机械规则, 非投资建议."
