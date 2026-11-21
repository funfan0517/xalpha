# 稳健资产（债腿 30pp + 现金腿 10pp）每日扫描 - 双击运行
# 抓取 data/_universe.md 第六类(债券 6xx)+第七类(现金 7xx) 共 15 只场外累计净值
#   -> v0 基准 35/65 固定中性 + v1~v8 各变种，一次跑完 9 步（产物全刷新，看板只出 V7）
# 板块6 默认策略 = V7 门派风险预算（2026-11-21 用户指令）；v0 转为基准，不再单独出卡
# 产物: strategies\bond_cash\daily\_bond_cash_daily.{md,json} + _universe_bond_cash_active.{md,json}
#       以及各变种 <策略目录>\daily\_vN_daily.{md,json}
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

Write-Host "[1/9] 稳健资产 V0 每日扫描（基准口径：债腿 35/65 固定中性 + 现金腿 10pp）..."
$rc = Invoke-Py @scanArgs
if ($rc -ne 0) { throw "bond_cash daily scan failed (exit $rc)" }

# V1 四因子久期档：读同一批 A 类净值 + 中债曲线/SHIBOR，只改债腿内部的长端占比。
# --force 透传：要抓新曲线/SHIBOR 时 v0/v1 一起刷新，避免两边日期不一致。
Write-Host "[2/9] V1 四因子久期档（温度/因子/情景 → 长端占比）..."
$v1Args = @("strategies\bond_cash\v1_four_factor\daily\_daily_scan.py")
if ($Force) { $v1Args += "--force" }
$rc = Invoke-Py @v1Args
if ($rc -ne 0) { throw "bond_cash v1 daily scan failed (exit $rc)" }

# V2 横截面双动量：回测为负、daily.enabled=false → 只刷新产物供研究，不进看板。
Write-Host "[3/9] V2 横截面双动量（对照研究，未启用）..."
$v2Args = @("strategies\bond_cash\v2_cross_momentum\daily\_daily_scan.py")
if ($Force) { $v2Args += "--force" }
$rc = Invoke-Py @v2Args
if ($rc -ne 0) { Write-Host "  [warn] v2 扫描失败（对照策略，不中断）" }

# V3 Carry + 趋势投票：只读 v1 的曲线缓存，月末调仓。
Write-Host "[4/9] V3 Carry + 趋势投票（久期择时）..."
$v3Args = @("strategies\bond_cash\v3_carry_trend\daily\_daily_scan.py")
if ($Force) { $v3Args += "--force" }
$rc = Invoke-Py @v3Args
if ($rc -ne 0) { throw "bond_cash v3 daily scan failed (exit $rc)" }

# V4 股债对冲三票：v3 的两票 + 沪深300 动量极端分位（Hedge 票）。回测显示 Hedge 贡献≈0，只作研究。
Write-Host "[5/9] V4 股债对冲三票（Carry + Trend + Hedge）..."
$v4Args = @("strategies\bond_cash\v4_stock_hedge\daily\_daily_scan.py")
if ($Force) { $v4Args += "--force" }
$rc = Invoke-Py @v4Args
if ($rc -ne 0) { Write-Host "  [warn] v4 扫描失败（研究变种，不中断）" }

# V5 波动率目标：不预测方向，按长端腿已实现波动缩放敞口。
Write-Host "[6/9] V5 波动率目标（按 σ 缩放久期敞口）..."
$v5Args = @("strategies\bond_cash\v5_vol_target\daily\_daily_scan.py")
if ($Force) { $v5Args += "--force" }
$rc = Invoke-Py @v5Args
if ($rc -ne 0) { Write-Host "  [warn] v5 扫描失败（研究变种，不中断）" }

# V6 双利差锚：期限利差定久期 × 信用利差定品种（利率 011983 / 信用 000032）。
Write-Host "[7/9] V6 双利差锚（期限利差 × 信用利差）..."
$v6Args = @("strategies\bond_cash\v6_dual_spread\daily\_daily_scan.py")
if ($Force) { $v6Args += "--force" }
$rc = Invoke-Py @v6Args
if ($rc -ne 0) { Write-Host "  [warn] v6 扫描失败（研究变种，不中断）" }

# V7 门派风险预算：四门派 ERC 配比 + 风险贡献分解（现金 10pp 固定、不进 ERC）。
# 2026-11-21 起这是板块6 的默认策略、看板唯一卡片 → 失败必须中断（产物缺了卡就空）。
Write-Host "[8/9] V7 门派风险预算（板块6 默认 · ERC + 风险贡献分解）..."
$v7Args = @("strategies\bond_cash\v7_risk_budget\daily\_daily_scan.py")
if ($Force) { $v7Args += "--force" }
$rc = Invoke-Py @v7Args
if ($rc -ne 0) { throw "bond_cash v7 daily scan failed (exit $rc)" }

# V8 久期阶梯风险预算：六腿 0~100% 权重（长端 20/中端 45/短端 5/信用 30）
#                        + 中端同档择一 + 进攻档波动煞车（只砍仓不追高）。
Write-Host "[9/9] V8 久期阶梯风险预算（六腿 0~100%）..."
$v8Args = @("strategies\bond_cash\v8_duration_ladder\daily\_daily_scan.py")
if ($Force) { $v8Args += "--force" }
$rc = Invoke-Py @v8Args
if ($rc -ne 0) { Write-Host "  [warn] v8 扫描失败（研究变种，不中断）" }

Write-Host ""
Write-Host "Done."
Write-Host "  V0 report  : strategies\bond_cash\daily\_bond_cash_daily.md （基准，不上板）"
Write-Host "  V1 report  : strategies\bond_cash\v1_four_factor\daily\_v1_daily.md"
Write-Host "  V2 report  : strategies\bond_cash\v2_cross_momentum\daily\_v2_daily.md （对照，未启用）"
Write-Host "  V3 report  : strategies\bond_cash\v3_carry_trend\daily\_v3_daily.md"
Write-Host "  V4 report  : strategies\bond_cash\v4_stock_hedge\daily\_v4_daily.md （研究）"
Write-Host "  V5 report  : strategies\bond_cash\v5_vol_target\daily\_v5_daily.md （研究）"
Write-Host "  V6 report  : strategies\bond_cash\v6_dual_spread\daily\_v6_daily.md （研究）"
Write-Host "  V7 report  : strategies\bond_cash\v7_risk_budget\daily\_v7_daily.md （★ 板块6 默认，看板唯一卡片）"
Write-Host "  V8 report  : strategies\bond_cash\v8_duration_ladder\daily\_v8_daily.md （研究，不上板）"
Write-Host "  V1 state   : strategies\bond_cash\v1_four_factor\daily\_v1_state.json （当前长端占比）"
Write-Host "  Note       : 板块6 默认 = V7 四门派 ERC（现金 10pp 固定、不计入风险预算），2026-11-21 用户指令;"
Write-Host "               基准 V0 固定中性 35% 003376 + 65% 011983（2026-11-20 由 70/30 改）;"
Write-Host "               V1 四因子调长端（35%~70%, 滞后带 5.25pp）; V3 曲线双票（0/17.5/35/52.5/70%）;"
Write-Host "               V4 V3+Hedge 票; V5 按 σ 缩放长端; V6 久期轴×品种轴（011983/000032）;"
Write-Host "               V8 六腿久期阶梯权重 20/45/5/30 + 同档择一 + 进攻档煞车."
Write-Host "               以上 v0/v1~v6/v8 均 daily.enabled=false：产物照跑、看板不出卡."
