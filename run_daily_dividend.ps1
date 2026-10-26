# 红利基金每日操作(V4 · RSI14 择时) - 双击运行
# 标的池: V4 专用 5 只(按单标的 RSI 择时适配度筛出, 见 strategies/dividend/v4_rsi/rule.py::POOL)
# 规则: 板块 RSI14 均值 >=70 清仓 / >=50 减半 / 其余满仓; 横截面取 RSI 最低 2 只等权
# 输出: 今日该持什么(当前生效信号) + 预览 + 下一个决策点
# Usage: powershell -ExecutionPolicy Bypass -File run_daily_dividend.ps1
$ErrorActionPreference = "Stop"
Set-Location $PSScriptRoot
$env:PYTHONIOENCODING = "utf-8"

Write-Host "[1/1] V4 RSI14 timing signal (5-fund dividend pool)..."
python -W ignore strategies\dividend\v4_rsi\daily.py --refresh
if ($LASTEXITCODE -ne 0) { throw "dividend v4 signal failed" }

Write-Host ""
Write-Host "Done. Signal: strategies\dividend\v4_rsi\daily\_v4_signal.md ; JSON: strategies\dividend\v4_rsi\daily\_v4_signal.json"
Write-Host "[i] V4 is a MONTH-END strategy: signal confirmed at month-end close, trade next open; intra-month output is preview only."
Write-Host "[i] Since 2026-10-26 daily ops = V4 (replaced V0 scoring). V0 outputs remain in strategies\dividend\v0_rank\daily\; manual rerun: python strategies\dividend\v0_rank\rank.py --refresh"
