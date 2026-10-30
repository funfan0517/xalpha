# 红利基金每日操作(V4 · RSI14 择时 + V9 · 五因子估值温度) - 双击运行
# V4 标的池: 择时专用 5 只(按单标的 RSI 择时适配度筛出, 见 strategies/dividend/pool.py::TIMING_POOL)
#  规则: 板块 RSI14 均值 >=70 清仓 / >=50 减半 / 其余满仓; 横截面取 RSI 最低 2 只等权
#  输出: 今日该持什么(当前生效信号) + 预览 + 下一个决策点
# V9 标的池: 6 只(有 etf.run 等权真实 PE/PB 的标的)
#  规则: 五因子估值温度(利差 35/PE 20/PB 16/乖离 15/位置 14); 只出「逐只档位 + 额度参考」, 不调仓位
#  输出: _v9_signal.md/json + 逐标的图 + HTML 看板
# Usage: powershell -ExecutionPolicy Bypass -File run_daily_dividend.ps1
$ErrorActionPreference = "Stop"
Set-Location $PSScriptRoot
$env:PYTHONIOENCODING = "utf-8"

Write-Host "[1/2] V4 RSI14 timing signal (5-fund dividend pool)..."
python -W ignore strategies\dividend\v4_rsi\daily.py --refresh
if ($LASTEXITCODE -ne 0) { throw "dividend v4 signal failed" }

Write-Host ""
Write-Host "[2/2] V9 five-factor valuation temperature (6-fund dividend pool)..."
python -W ignore strategies\dividend\v9_temp5\daily.py --refresh
if ($LASTEXITCODE -ne 0) { throw "dividend v9 daily failed" }
python -W ignore strategies\dividend\v9_temp5\charts.py --refresh
if ($LASTEXITCODE -ne 0) { throw "dividend v9 charts failed" }
python -W ignore strategies\dividend\v9_temp5\dashboard.py
if ($LASTEXITCODE -ne 0) { throw "dividend v9 dashboard failed" }

Write-Host ""
Write-Host "Done."
Write-Host "  V4 signal: strategies\dividend\v4_rsi\daily\_v4_signal.md ; JSON: strategies\dividend\v4_rsi\daily\_v4_signal.json"
Write-Host "  V9 signal: strategies\dividend\v9_temp5\daily\_v9_signal.md ; HTML: strategies\dividend\v9_temp5\daily\_v9_temp.html"
Write-Host "[i] V4 is a MONTH-END timing strategy: confirmed at month-end close, trade next open; intra-month output is preview only."
Write-Host "[i] V9 is a 'valuation thermometer' (贵不贵), NOT a timing/position signal; use it for 加/减仓额度参考."
Write-Host "[i] Since 2026-10-26 daily ops = V4 (replaced V0 scoring); 2026-10-30 V9 temperature merged in. V0 outputs remain in strategies\dividend\v0_rank\daily\; manual rerun: python strategies\dividend\v0_rank\rank.py --refresh"
