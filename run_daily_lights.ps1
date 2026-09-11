# 亮灯策略 daily runner (ALL inner pool = every "has inner" row of data/_universe.md)
# 每日评估: 输出全部标的的「门槛层逐条实际值 vs 阈值 + 各维灯得分与触发依据」, 及各自目标仓位。
# 规则: 门槛全过 且 得分 >= enter_min 且 必需灯达标 -> 目标建仓; 否则目标空仓。
#       信号变化则在次日开盘执行（无前视）。
# 用途: 每天看哪些基金亮了几盏灯、得分多少、为什么没达标 (strategies/lights/_signal_report.md)。
# Usage: powershell -ExecutionPolicy Bypass -File run_daily_lights.ps1
$ErrorActionPreference = "Stop"
Set-Location g:\xalpha
$env:PYTHONIOENCODING = "utf-8"

# Full inner pool: no codes arg passed, scan.py scans universe.inner_rows() by default
Write-Host "[1/2] Scanning gates + lights for all inner-pool funds..."
python -W ignore strategies\lights\scan.py --commit
if ($LASTEXITCODE -ne 0) { throw "lights scan failed" }

Write-Host "[2/2] Generating daily report + dashboard..."
python -W ignore strategies\lights\_scan_report.py
if ($LASTEXITCODE -ne 0) { throw "report generation failed" }

Write-Host ""
Write-Host "Done. Signal: strategies\lights\_signal_report.md ; Dashboard: strategies\lights\_signal_dashboard.html"
Write-Host "[i] Intraday bar used - volume/turnover may move before 15:00 close. Keep a buffer."
