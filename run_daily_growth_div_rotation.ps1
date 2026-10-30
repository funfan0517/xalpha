# 成长/红利风格轮动 每日信号 - 双击运行
# 盘后运行: 增量刷新指数行情 -> 计算风格比值/均线/分位 -> 输出次日开盘操作信号
# v0 (MA20 缓冲带 + 250日分位) 与 v1 (绝对阈值 0.20/0.31 + 5期分批) 各自落一份产物, 看板分两张卡
# 用法: powershell -ExecutionPolicy Bypass -File run_daily_growth_div_rotation.ps1
$ErrorActionPreference = "Stop"
Set-Location $PSScriptRoot
$env:PYTHONIOENCODING = "utf-8"

Write-Host "[1/3] 增量刷新指数行情缓存..."
python -W ignore strategies\growth_div_rotation\v0\data.py
if ($LASTEXITCODE -ne 0) { throw "行情刷新失败" }

Write-Host "[2/3] 生成 v0 信号 (MA20 缓冲带 + 250日分位)..."
python -W ignore strategies\growth_div_rotation\v0\scan.py
if ($LASTEXITCODE -ne 0) { throw "v0 信号生成失败" }

Write-Host "[3/3] 生成 v1 状态 (绝对阈值 0.20/0.31 + 5期分批)..."
python -W ignore strategies\growth_div_rotation\v1_ratio_bands\scan.py
if ($LASTEXITCODE -ne 0) { throw "v1 状态生成失败" }

Write-Host ""
Write-Host "Done. v0 报告: strategies\growth_div_rotation\v0\daily\_signal_report.md ; 快照: v0\daily\_gd_signal.json"
Write-Host "      v1 报告: strategies\growth_div_rotation\v1_ratio_bands\daily\_gd_v1_daily.md ; 快照: v1_ratio_bands\daily\_gd_v1_signal.json"
Write-Host "[i] 信号用指数收盘(盘后), 执行用次日开盘价(场外按当日15:00前净值); 机械规则, 非投资建议."
