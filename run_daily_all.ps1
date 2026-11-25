# 全部大策略 · 每日操作一键执行 - 双击运行
# 读取 pipeline/strategies.json 中「上板」策略的 daily.runner，按每日操作板块(daily.block -> blocks 序号)顺序执行，最后汇总成功/失败。
# 调度范围 = daily.enabled=true 且 daily.card != false（即真的出卡的策略）。
# 用户 2026-11-24 规则：不上报告看板的策略不随每日操作跑 —— enabled=false 或 card=false 的一律不进名单；
# 它们需要的产物由「出卡的家族卡」持有的 runner 生成，同一个 .ps1 在本脚本内按名字去重只执行一次。
# 单个策略失败不中断后续（与本仓库其它 runner 的 fail-fast 行为不同：这里是「全量跑一遍再汇总」）。
# 收尾出两份看板（用户 2026-11-06 要求）：
#   data\_daily_digest.html           收盘口径（各策略产物落盘，正式版）
#   data\_daily_digest_intraday.html  盘中预演（最新盘中快照当收盘重算，不写任何数据文件）
# 用法: powershell -ExecutionPolicy Bypass -File run_daily_all.ps1
#       powershell -ExecutionPolicy Bypass -File run_daily_all.ps1 -Only tech_indicators,vol_variants   # 只跑指定上板策略
# 想刷新某个不出卡的变种/研究产物: 手动跑它自己的脚本（命令写在各 run_daily_*.ps1 头注释里）。
param(
    [string[]]$Only = @()
)

$ErrorActionPreference = "Continue"
Set-Location $PSScriptRoot
$env:PYTHONIOENCODING = "utf-8"

# -File 调用时 "a,b" 会作为单个字符串传入，这里补一次拆分，
# 否则 README 里的 `-Only tech_indicators,vol_legs` 实际匹配到 0 个策略。
if ($Only.Count -gt 0) {
    $Only = @($Only | ForEach-Object { "$_" -split ',' } | ForEach-Object { $_.Trim() } | Where-Object { $_ })
}

$cfgPath = Join-Path $PSScriptRoot "pipeline\strategies.json"
if (-not (Test-Path $cfgPath)) { throw "找不到 $cfgPath" }
$cfg = Get-Content $cfgPath -Raw -Encoding UTF8 | ConvertFrom-Json

# 配置自检: 挡住两类「代码没改、结果悄悄错」的配置漂移 ——
#   ① 同一 runner 被 2 个 enabled 策略引用（新增变种忘标 runner=""，该 ps1 会被跑两遍）
#   ② runner 指向不存在的文件 / daily.block 不在 blocks（运行时只打印 [skip]，看板沿用旧数据）
# ERROR 直接中断（跑下去也只会得到错的结果）；WARN 只提示，不阻断。
Write-Host "[config] 自检 pipeline\strategies.json ..."
python -W ignore (Join-Path $PSScriptRoot "pipeline\check_daily_config.py")
if ($LASTEXITCODE -ne 0) { throw "每日操作配置自检失败, 先修上面 [ERROR] 项" }
Write-Host ""

$ok = @(); $fail = @(); $skip = @()
# 调度范围 = 上板策略（enabled=true 且 card != false）。用户 2026-11-24 规则：不上看板的策略不跑。
# 不出卡的策略若还有必跑产物（v14~v20、gd_v5 等），调度权已移到出卡的家族卡上（vol_variants / gd_v5），
# 所以这里只需按 runner 去重，不需要再单独调度 card=false 的条目。
$allNames = @($cfg.strategies.PSObject.Properties.Name)
$onBoard = New-Object System.Collections.ArrayList
$offBoard = New-Object System.Collections.ArrayList
foreach ($n in $allNames) {
    $d = $cfg.strategies.$n.daily
    if ($d.enabled -eq $true -and $d.card -ne $false) { [void]$onBoard.Add($n) } else { [void]$offBoard.Add($n) }
}
# 执行顺序 = 每日操作板块顺序（strategies.json 的 blocks），而非策略在 json 里的书写顺序。
# 板块内保持注册顺序；未认领 daily.block 的上板策略排在最后。
$ordered = New-Object System.Collections.ArrayList
foreach ($b in @($cfg.blocks)) {
    if ($null -eq $b) { continue }
    foreach ($n in $onBoard) {
        if ("$($cfg.strategies.$n.daily.block)" -eq "$($b.key)") { [void]$ordered.Add($n) }
    }
}
foreach ($n in $onBoard) { if ($ordered -notcontains $n) { [void]$ordered.Add($n) } }
$names = @($ordered)
if ($Only.Count -gt 0) {
    foreach ($m in @($Only | Where-Object { $names -notcontains $_ })) {
        if ($allNames -notcontains $m) { Write-Host "[$m] 未在 strategies.json 注册"; continue }
        $d = $cfg.strategies.$m.daily
        $hint = if ($d -and $d.runner) { "；要刷新请直接跑 $($d.runner)" } else { "" }
        Write-Host "[$m] 未上板（enabled=$($d.enabled) card=$($d.card)），不随每日操作跑$hint"
    }
    $names = @($names | Where-Object { $Only -contains $_ })
}

Write-Host "每日操作 · 上板 $($names.Count) 张卡（共 $($allNames.Count) 个策略；不出卡 $($offBoard.Count) 个不跑）"
Write-Host "  上板: $($names -join ', ')"
Write-Host "  不跑: $($offBoard -join ', ')"
Write-Host ""
# 批量标记: 让子 runner(如 run_daily_vol_legs.ps1)不要再各自跑 digest, 统一由本脚本末尾汇总一次
$env:XA_DAILY_BATCH = "1"

$ranBy = @{}   # runner -> 首个执行它的策略（同一 .ps1 只跑一次）
foreach ($n in $names) {
    $st = $cfg.strategies.$n
    $runner = $st.daily.runner
    if (-not $runner) {
        Write-Host "[$n] $($st.name)"
        Write-Host "  无独立 runner: 本卡只读其它策略产物, 不单独执行"
        $ok += $n
        Write-Host ""
        continue
    }
    if ($ranBy.ContainsKey($runner)) {
        Write-Host "[$n] $($st.name)"
        Write-Host "  共享 runner $runner —— 已在 [$($ranBy[$runner])] 执行, 去重跳过"
        $ok += $n
        Write-Host ""
        continue
    }
    Write-Host ("=" * 70)
    Write-Host "[$n] $($st.name)"
    Write-Host "  runner: $runner"
    Write-Host ("=" * 70)
    $rp = Join-Path $PSScriptRoot $runner
    if (-not (Test-Path $rp)) { Write-Host "  [skip] 找不到 $rp"; $skip += $n; continue }
    $ranBy[$runner] = $n   # 先登记: 同一 runner 后续共享卡不再重复调用(失败也不重试)
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
Remove-Item Env:\XA_DAILY_BATCH -ErrorAction SilentlyContinue
Write-Host ("=" * 70)
Write-Host "[digest] 汇总每日操作 -> data\_daily_digest.html"
python -W ignore (Join-Path $PSScriptRoot "pipeline\daily_digest.py")
if ($LASTEXITCODE -ne 0) { Write-Host "  [!] 汇总失败, 各策略产物已生成但看板未更新" }

# 第二份: 盘中预演（把最新盘中快照当收盘重算, 不写任何数据文件, 只多出一个 HTML）
# 用户 2026-11-06 要求每天两份: 收盘口径 _daily_digest.html + 预演 _daily_digest_intraday.html
if ($Only.Count -eq 0) {
    Write-Host "[preview] 盘中预演 -> data\_daily_digest_intraday.html"
    python -W ignore (Join-Path $PSScriptRoot "pipeline\intraday_preview.py")
    if ($LASTEXITCODE -ne 0) { Write-Host "  [!] 预演失败, 不影响收盘口径看板" }
}

Write-Host "[i] 各策略产物见 pipeline/strategies.json 的 daily.signal_report; 机械规则, 非投资建议."
