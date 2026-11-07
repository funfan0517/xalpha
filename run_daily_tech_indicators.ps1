# 技术指标(大策略家族)每日操作 - 双击运行
# 盘后运行: 口径自检 -> 刷新行情 -> 变种 v9/v10 各自扫描 + 出报告 -> 固定 6 策略 MACD/V3/V6/V7/V9/V10 汇总表
# 用法: powershell -ExecutionPolicy Bypass -File run_daily_tech_indicators.ps1
$ErrorActionPreference = "Stop"
Set-Location $PSScriptRoot
$env:PYTHONIOENCODING = "utf-8"

# [0] 口径闸门: 停牌污染(fix#2) / volume-dates 错位 / 日期索引异常。
# 只读本地缓存、不联网, 秒级; 违规直接中断 —— 让「顺手加个 ffill」当天就响,
# 而不是三个月后回测数字对不上再倒查。
Write-Host "[0/6] 数据口径自检 (strategies\_panel.py)..."
python -W ignore strategies\_panel.py
if ($LASTEXITCODE -ne 0) { throw "面板口径自检失败, 先看上面 [ERROR] 项" }

Write-Host "[1/6] 变种9 右侧买入 · 全池扫描 (五票确认)..."
python -W ignore strategies\tech_indicators\v9_right_side\scan.py
if ($LASTEXITCODE -ne 0) { throw "v9 scan failed" }

Write-Host "[2/6] 变种9 右侧买入 · 每日操作报告..."
python -W ignore strategies\tech_indicators\v9_right_side\_scan_report.py
if ($LASTEXITCODE -ne 0) { throw "v9 report failed" }

Write-Host "[3/6] 变种10 亮灯 · 全池扫描 (门槛+灯评分)..."
python -W ignore strategies\tech_indicators\v10_lights\scan.py
if ($LASTEXITCODE -ne 0) { throw "v10 scan failed" }

Write-Host "[4/6] 变种10 亮灯 · 每日操作报告..."
python -W ignore strategies\tech_indicators\v10_lights\_scan_report.py
if ($LASTEXITCODE -ne 0) { throw "v10 report failed" }

Write-Host "[5/6] 生成技术指标每日操作建议 (MACD / V3 / V6 / V7 / V9 / V10)..."
python -W ignore strategies\tech_indicators\daily\_daily_recommend.py
if ($LASTEXITCODE -ne 0) { throw "tech_indicators daily failed" }

Write-Host "[6/6] 完成"
Write-Host ""
Write-Host "Done. 报告: strategies\tech_indicators\daily\_daily_recommend.md"
Write-Host "      看板: strategies\tech_indicators\v9_right_side\daily\_right_side_dashboard.html"
Write-Host "            strategies\tech_indicators\v10_lights\daily\_signal_dashboard.html"
Write-Host "[i] 固定 6 策略各跑一遍, 全池 = _universe.md 前四类(54 只)不做 A/B 分级; 信号用收盘(盘后), 执行用次日开盘."
Write-Host "[i] v9/v10 是状态机型变种, 由各自 scan.py 先落盘, 汇总脚本只读产物."
Write-Host "[i] 机械规则, 非投资建议."
