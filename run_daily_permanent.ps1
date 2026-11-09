# 永久投资组合(8 项 · ±50% 浮动)每日最佳百分比 - 双击运行
# 自动抓取: 9 条腿的场外净值(场外真实执行口径) -> 趋势/动量打分 -> 目标权重
# 无人工项: 全部信号可由净值序列本地算出, 不需要估值锚/开关
# Usage: powershell -ExecutionPolicy Bypass -File run_daily_permanent.ps1
$ErrorActionPreference = "Stop"
Set-Location $PSScriptRoot
$env:PYTHONIOENCODING = "utf-8"
# 输出用 UTF-8, 否则中文经 subprocess 捕获会乱码
[Console]::OutputEncoding = [System.Text.Encoding]::UTF8
$OutputEncoding = [System.Text.Encoding]::UTF8

# python 的 logger.warning 走 stderr; PS 5.1 在 $ErrorActionPreference="Stop" 下会把 native
# 命令的 stderr 当成 terminating error 直接中断脚本(2026-11-09 永久组合/红利因此被误判失败,
# 实际 python 退出码 0)。这里先把 stderr 合进输出流再打印, $LASTEXITCODE 仍是 python 真实退出码。
function Invoke-Py {
    param([Parameter(ValueFromRemainingArguments = $true)][string[]]$PyArgs)
    # 函数内赋值 = 局部作用域, 不影响脚本级 Stop; 但只靠 2>&1 在 PS 5.1 下仍会抛
    # NativeCommandError, 必须把 ErrorActionPreference 降成 Continue 才真正压得住。
    $ErrorActionPreference = "Continue"
    & python -W ignore @PyArgs 2>&1 | ForEach-Object { Write-Host "$_" }
    return $LASTEXITCODE
}

Write-Host "[1/1] Generating permanent portfolio daily weights..."
$rc = Invoke-Py strategies\permanent\_signal.py
if ($rc -ne 0) { throw "permanent signal failed (exit $rc)" }

Write-Host ""
Write-Host "Done."
Write-Host "  Report  : strategies\permanent\daily\_permanent_report.md"
Write-Host "  Snapshot: strategies\permanent\daily\_permanent_signal.json"
Write-Host "  Note    : OTC subscribe/redeem by 15:00 T-day; QDII(270042/017641) NAV lags 1-2 days."
