# 全部大策略 · 每日操作一键执行 - 双击运行
# 读取 pipeline/strategies.json 中所有已注册策略的 daily.runner，按注册顺序依次执行，最后汇总成功/失败。
# 跳过 daily.enabled=false 的策略（已移出每日操作，但仍可手动跑各自的 runner）。
# 单个策略失败不中断后续（与本仓库其它 runner 的 fail-fast 行为不同：这里是「全量跑一遍再汇总」）。
# 用法: powershell -ExecutionPolicy Bypass -File run_daily_all.ps1
#       powershell -ExecutionPolicy Bypass -File run_daily_all.ps1 -Only lights,tech_indicators   # 只跑指定策略
param(
    [string[]]$Only = @()
)

$ErrorActionPreference = "Continue"
Set-Location $PSScriptRoot
$env:PYTHONIOENCODING = "utf-8"

# -File 调用时 "a,b" 会作为单个字符串传入，这里补一次拆分，
# 否则 README 里的 `-Only lights,tech_indicators` 实际匹配到 0 个策略。
if ($Only.Count -gt 0) {
    $Only = @($Only | ForEach-Object { "$_" -split ',' } | ForEach-Object { $_.Trim() } | Where-Object { $_ })
}

$cfgPath = Join-Path $PSScriptRoot "pipeline\strategies.json"
if (-not (Test-Path $cfgPath)) { throw "找不到 $cfgPath" }
$cfg = Get-Content $cfgPath -Raw -Encoding UTF8 | ConvertFrom-Json

$ok = @(); $fail = @(); $skip = @()
$names = @($cfg.strategies.PSObject.Properties.Name)
if ($Only.Count -gt 0) { $names = @($names | Where-Object { $Only -contains $_ }) }

Write-Host "每日操作 · 共 $($names.Count) 个策略: $($names -join ', ')"
Write-Host ""

foreach ($n in $names) {
    $st = $cfg.strategies.$n
    if ($st.daily.enabled -eq $false) {
        Write-Host "[$n] $($st.name)  [skip] 已停用 (daily.enabled=false)"
        $skip += $n
        continue
    }
    $runner = $st.daily.runner
    Write-Host ("=" * 70)
    Write-Host "[$n] $($st.name)"
    Write-Host "  runner: $runner"
    Write-Host ("=" * 70)
    if (-not $runner) { Write-Host "  [skip] 未配置 daily.runner"; $skip += $n; continue }
    $rp = Join-Path $PSScriptRoot $runner
    if (-not (Test-Path $rp)) { Write-Host "  [skip] 找不到 $rp"; $skip += $n; continue }
    try {
        & $rp
        if ($LASTEXITCODE -ne 0) { throw "runner 退出码 $LASTEXITCODE" }
        $ok += $n
        Write-Host "  [OK] $n"
    } catch {
        $fail += $n
        Write-Host "  [X] $n 失败: $_"
    }
    Write-Host ""
}

Write-Host ("=" * 70)
Write-Host "汇总: 成功 $($ok.Count) / 失败 $($fail.Count) / 跳过 $($skip.Count)"
if ($ok.Count)   { Write-Host "  OK  : $($ok -join ', ')" }
if ($fail.Count) { Write-Host "  FAIL: $($fail -join ', ')" }
if ($skip.Count) { Write-Host "  SKIP: $($skip -join ', ')" }

# 跨策略汇总: 各策略 daily 产物 -> 历史库 + 单文件 HTML 看板（失败不影响上面的策略结果）
Write-Host ("=" * 70)
Write-Host "[digest] 汇总每日操作 -> data\_daily_digest.html"
python -W ignore (Join-Path $PSScriptRoot "pipeline\daily_digest.py")
if ($LASTEXITCODE -ne 0) { Write-Host "  [!] 汇总失败, 各策略产物已生成但看板未更新" }

Write-Host "[i] 各策略产物见 pipeline/strategies.json 的 daily.signal_report; 机械规则, 非投资建议."
