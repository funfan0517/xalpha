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

Write-Host "[1/4] 稳健资产 V0 每日扫描（债腿 30pp + 现金腿 10pp）..."
$rc = Invoke-Py @scanArgs
if ($rc -ne 0) { throw "bond_cash daily scan failed (exit $rc)" }

# V1 四因子久期档：读同一批 A 类净值 + 中债曲线/SHIBOR，只改债腿内部的长端占比。
# --force 透传：要抓新曲线/SHIBOR 时 v0/v1 一起刷新，避免两边日期不一致。
Write-Host "[2/4] V1 四因子久期档（温度/因子/情景 → 长端占比）..."
$v1Args = @("strategies\bond_cash\v1_four_factor\daily\_daily_scan.py")
if ($Force) { $v1Args += "--force" }
$rc = Invoke-Py @v1Args
if ($rc -ne 0) { throw "bond_cash v1 daily scan failed (exit $rc)" }

# V2 横截面双动量：回测为负、daily.enabled=false → 只刷新产物供研究，不进看板。
Write-Host "[3/4] V2 横截面双动量（对照研究，未启用）..."
$v2Args = @("strategies\bond_cash\v2_cross_momentum\daily\_daily_scan.py")
if ($Force) { $v2Args += "--force" }
$rc = Invoke-Py @v2Args
if ($rc -ne 0) { Write-Host "  [warn] v2 扫描失败（对照策略，不中断）" }

# V3 Carry + 趋势投票：只读 v1 的曲线缓存，月末调仓。
Write-Host "[4/4] V3 Carry + 趋势投票（久期择时）..."
$v3Args = @("strategies\bond_cash\v3_carry_trend\daily\_daily_scan.py")
if ($Force) { $v3Args += "--force" }
$rc = Invoke-Py @v3Args
if ($rc -ne 0) { throw "bond_cash v3 daily scan failed (exit $rc)" }

Write-Host ""
Write-Host "Done."
Write-Host "  V0 report  : strategies\bond_cash\daily\_bond_cash_daily.md"
Write-Host "  V1 report  : strategies\bond_cash\v1_four_factor\daily\_v1_daily.md"
Write-Host "  V2 report  : strategies\bond_cash\v2_cross_momentum\daily\_v2_daily.md （对照，未启用）"
Write-Host "  V3 report  : strategies\bond_cash\v3_carry_trend\daily\_v3_daily.md"
Write-Host "  V1 state   : strategies\bond_cash\v1_four_factor\daily\_v1_state.json （当前长端占比）"
Write-Host "  Note       : V0 固定哑铃 70% 003376 + 30% 011983（默认不动手）;"
Write-Host "               V1 四因子调长端（35%~70%, 滞后带 5.25pp）; V3 曲线双票（0/17.5/35/52.5/70%）."
