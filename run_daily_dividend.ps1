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

# python 的 logger.warning 走 stderr; PS 5.1 在 $ErrorActionPreference="Stop" 下会把 native
# 命令的 stderr 当成 terminating error 直接中断脚本(2026-11-09 「561060 赎回费信息异常」这条
# warning 就让整个红利流程在第一行停住, 实际 python 退出码 0)。这里先把 stderr 合进输出流
# 再打印, $LASTEXITCODE 仍是 python 真实退出码。
function Invoke-Py {
    param([Parameter(ValueFromRemainingArguments = $true)][string[]]$PyArgs)
    # 函数内赋值 = 局部作用域, 不影响脚本级 Stop; 但只靠 2>&1 在 PS 5.1 下仍会抛
    # NativeCommandError, 必须把 ErrorActionPreference 降成 Continue 才真正压得住。
    $ErrorActionPreference = "Continue"
    & python -W ignore @PyArgs 2>&1 | ForEach-Object { Write-Host "$_" }
    return $LASTEXITCODE
}

Write-Host "[1/2] V4 RSI14 timing signal (5-fund dividend pool)..."
$rc = Invoke-Py strategies\dividend\v4_rsi\daily.py --refresh
if ($rc -ne 0) { throw "dividend v4 signal failed (exit $rc)" }

Write-Host ""
Write-Host "[2/2] V9 five-factor valuation temperature (6-fund dividend pool)..."
$rc = Invoke-Py strategies\dividend\v9_temp5\daily.py --refresh
if ($rc -ne 0) { throw "dividend v9 daily failed (exit $rc)" }
$rc = Invoke-Py strategies\dividend\v9_temp5\charts.py --refresh
if ($rc -ne 0) { throw "dividend v9 charts failed (exit $rc)" }
$rc = Invoke-Py strategies\dividend\v9_temp5\dashboard.py
if ($rc -ne 0) { throw "dividend v9 dashboard failed (exit $rc)" }

Write-Host ""
Write-Host "Done."
Write-Host "  V4 signal: strategies\dividend\v4_rsi\daily\_v4_signal.md ; JSON: strategies\dividend\v4_rsi\daily\_v4_signal.json"
Write-Host "  V9 signal: strategies\dividend\v9_temp5\daily\_v9_signal.md ; HTML: strategies\dividend\v9_temp5\daily\_v9_temp.html"
Write-Host "[i] V4 is a MONTH-END timing strategy: confirmed at month-end close, trade next open; intra-month output is preview only."
Write-Host "[i] V9 is a 'valuation thermometer' (贵不贵), NOT a timing/position signal; use it for 加/减仓额度参考."
Write-Host "[i] Since 2026-10-26 daily ops = V4 (replaced V0 scoring); 2026-10-30 V9 temperature merged in. V0 outputs remain in strategies\dividend\v0_rank\daily\; manual rerun: python strategies\dividend\v0_rank\rank.py --refresh"
