# 稳健资产（债腿 30pp + 现金腿 10pp）每日扫描 - 双击运行
# 抓取 data/_universe.md 第六类(债券 6xx)+第七类(现金 7xx) 共 15 只场外累计净值
#   -> 债腿 70/30 固定哑铃的 pp 配置 + 可选动量开关的影子档位 + 连跌纪律 + 卫星条件
# 产物: strategies\bond_cash\daily\_bond_cash_daily.{md,json} + _universe_bond_cash_active.{md,json}
# Usage: powershell -ExecutionPolicy Bypass -File run_daily_bond_cash.ps1
param(
    [switch]$Force,     # 强制重抓净值（忽略当日缓存）
    [switch]$Gate       # 按动量开关口径出档位（默认关，固定哑铃）
)
$ErrorActionPreference = "Stop"
Set-Location $PSScriptRoot
$env:PYTHONIOENCODING = "utf-8"
[Console]::OutputEncoding = [System.Text.Encoding]::UTF8
$OutputEncoding = [System.Text.Encoding]::UTF8

# xalpha 的 logger.warning 走 stderr（如「赎回费信息异常」），PS 5.1 在 Stop 下会当成
# terminating error 中断脚本，而 python 的退出码其实是 0 —— 与 run_daily_permanent.ps1 同法处理。
function Invoke-Py {
    param([Parameter(ValueFromRemainingArguments = $true)][string[]]$PyArgs)
    $ErrorActionPreference = "Continue"
    & python -W ignore @PyArgs 2>&1 | ForEach-Object { Write-Host $_ }
    return $LASTEXITCODE
}

$scanArgs = @("strategies\bond_cash\daily\_daily_scan.py")
if ($Force) { $scanArgs += "--force" }
if ($Gate)  { $scanArgs += "--gate" }

Write-Host "[1/1] 稳健资产每日扫描（债腿 30pp + 现金腿 10pp）..."
$rc = Invoke-Py @scanArgs
if ($rc -ne 0) { throw "bond_cash daily scan failed (exit $rc)" }

Write-Host ""
Write-Host "Done."
Write-Host "  Report  : strategies\bond_cash\daily\_bond_cash_daily.md"
Write-Host "  Snapshot: strategies\bond_cash\daily\_bond_cash_daily.json"
Write-Host "  Note    : 债腿固定哑铃 70% 003376 + 30% 011983（默认不动手）;"
Write-Host "            现金腿 000198 + 014437 各 5pp; 场外按 T 日净值成交, 净 T+1 披露."
