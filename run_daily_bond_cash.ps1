# 稳健资产（债腿 30pp + 现金腿 10pp）每日扫描 - 双击运行
# 只跑 V7 门派风险预算（板块6 唯一卡片）。V7 有自己独立的净值缓存，不依赖 V0。
# 2026-11-25 用户要求「和看板无关的策略不要执行」：v0 基准 + v1~v6/v8 变种都不出卡，不再每日跑；
# 需要时手动跑（V7 同样可手动）:
#   python strategies\bond_cash\daily\_daily_scan.py                        # V0 基准
#   python strategies\bond_cash\v1_four_factor\daily\_daily_scan.py        # V1 四因子久期档
#   python strategies\bond_cash\v2_cross_momentum\daily\_daily_scan.py     # V2 横截面双动量
#   python strategies\bond_cash\v3_carry_trend\daily\_daily_scan.py        # V3 Carry+趋势
#   python strategies\bond_cash\v4_stock_hedge\daily\_daily_scan.py        # V4 股债对冲三票
#   python strategies\bond_cash\v5_vol_target\daily\_daily_scan.py         # V5 波动率目标
#   python strategies\bond_cash\v6_dual_spread\daily\_daily_scan.py        # V6 双利差锚
#   python strategies\bond_cash\v8_duration_ladder\daily\_daily_scan.py    # V8 久期阶梯
# 板块6 默认策略 = V7 门派风险预算（2026-11-21 用户指令）；v0 转为基准，不再单独出卡
# 产物: strategies\bond_cash\v7_risk_budget\daily\_v7_daily.{md,json}
# Usage: powershell -ExecutionPolicy Bypass -File run_daily_bond_cash.ps1   # 加 -Force 强制重抓净值
param(
    [switch]$Force      # 强制重抓净值（忽略当日缓存）
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

# V7 门派风险预算：四门派 ERC 配比 + 风险贡献分解（现金 10pp 固定、不进 ERC）。
# 2026-11-21 起这是板块6 的默认策略、看板唯一卡片 → 失败必须中断（产物缺了卡就空）。
Write-Host "[1/1] V7 门派风险预算（板块6 默认 · ERC + 风险贡献分解）..."
$v7Args = @("strategies\bond_cash\v7_risk_budget\daily\_daily_scan.py")
if ($Force) { $v7Args += "--force" }
$rc = Invoke-Py @v7Args
if ($rc -ne 0) { throw "bond_cash v7 daily scan failed (exit $rc)" }

Write-Host ""
Write-Host "Done."
Write-Host "  V7 report : strategies\bond_cash\v7_risk_budget\daily\_v7_daily.md （★ 板块6 默认，看板唯一卡片）"
Write-Host "  V7 json   : strategies\bond_cash\v7_risk_budget\daily\_v7_daily.json"
Write-Host "  Note      : 板块6 默认 = V7 四门派 ERC（现金 10pp 固定、不计入风险预算），2026-11-21 用户指令;"
Write-Host "              v0/v1~v6/v8 都是 daily.enabled=false 的研究变种，不再随每日操作跑;"
Write-Host "              手动命令见本脚本头注释."
