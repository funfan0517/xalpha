# 4D-signal daily runner (A-core 6 ETFs only)
# Runs intraday at ~14:05 so off-market subscriptions can settle by 15:00 (T-day NAV).
# NOTE: get_daily returns today's LIVE bar during trading hours (close = last price,
# volume = accumulated). Signal therefore uses today's unfinished bar; it may change
# toward the close (esp. volume/close-vs-open). Keep a buffer before 15:00.
# Usage: powershell -ExecutionPolicy Bypass -File run_daily_4d.ps1
$ErrorActionPreference = "Stop"
Set-Location g:\xalpha
$env:PYTHONIOENCODING = "utf-8"

# A-core universe (see data/_universe_4d_active.md). No other symbols are scanned/pushed.
$codes = "588000,515230,562500,516160,513180,512200"

Write-Host "[1/3] Fetching (incl. live today bar) and scoring 4D lights: $codes"
python -W ignore 4d\_inner_4d.py $codes > data\_inner_out.jsonl
if ($LASTEXITCODE -ne 0) { throw "4D scan failed" }

Write-Host "[2/3] Generating signal report..."
python -W ignore 4d\_report4d.py
if ($LASTEXITCODE -ne 0) { throw "report generation failed" }

Write-Host "[3/3] Updating dashboard..."
python -W ignore 4d\_visual.py
if ($LASTEXITCODE -ne 0) { throw "visualization failed" }

Write-Host ""
Write-Host "Done. Signal: 4d\_inner_report.md ; Dashboard: 4d\_4d_dashboard.html"
Write-Host "[i] Intraday signal - prices may move before 15:00 close."

