# 波动资产操作规则 v1.3(A500 5 / 纳指100 10 / 标普500 10 / 科创50 2.5 / 恒生科技 2.5 / 黄金 10 = 40pp) · 每日操作 - 双击运行

# 口径: 信号 + 回测 + 记账 **全部用该市场真实指数收盘**; 黄金用场内 518880; 无口径开关
# ⚠ 跑的时间点决定「哪条腿的数据齐了」，已不会再有「写半根」的风险（2026-11-24 起）:
#    指数库/长库只写**已收盘**的 bar, 判据统一在 `data/_market_clock.py`
#    (A 股 15:05 / 港股 16:05 / 美股 bar 收盘于北京次日 05:05), 逐代码按市场判:
#    - **15:05 之后**跑: A 股三腿 + 黄金是当日收盘; 美股是昨夜收盘(正常, QDII 次日成交);
#      **恒生科技仍停在上一交易日**(港股 16:00 才收盘), 日报脚注会显示它的日期落后一天。
#    - **16:05 之后**跑: 七腿全部是各自最新收盘, 推荐最完整 —— 推荐这个时间。
#    2026-11-23 14:43 那次「黄金 11-23 / 其余 11-20」的混排是旧逻辑(全局 15:05 前一律丢当天
#    bar)造成的; 同一套逻辑还会在 15:05~16:00 把恒生科技 15:0x 的半根写进库(实测 11-23
#    库 3511.35 vs 收盘 3508.00), 已一并修正。
# 盘后运行: 刷新指数库(SH000510/SH000300/.NDX/.INX/SH000688/HKHSTECH/518880) -> 复算七腿状态
#          (回撤阶梯/趋势闸门/季度再平衡) -> 输出「下一交易日该下单的那一笔」
# 第 2 步: 只跑板块2「变种操作一览」卡要的 v14~v20 七个单标的 daily 产物（一行一腿）。
#          七个 scan 互不依赖、各写自己目录 → 并行（7 个 python 进程）。
# 第 3 步: 只重画看板图墙嵌的七张 v14~v20 长期图。
#          v14 先串行跑一次预热 matplotlib 字体缓存, 其余 6 张并行。
# 不再每日跑（2026-11-25 用户要求「和看板无关的策略不要执行」，原 -Full 开关已取消）:
#   v2/v3/v4/v6/v9/v12 的 daily_scan 与它们的 9 张图、v12 状态图、v14~v20 的 _3y.png ——
#   需要时手动跑（示例）:
#     python strategies\vol_legs\v2_boll_breakout\daily\_daily_scan.py
#     python strategies\vol_legs\v3_macd_boll\daily\_daily_scan.py
#     python strategies\vol_legs\v4_erp_band\daily\_daily_scan.py --refresh
#     python strategies\vol_legs\v6_gold\daily\_daily_scan.py --no-refresh
#     python strategies\vol_legs\v9_vol_target\daily\_daily_scan.py --no-refresh
#     python strategies\vol_legs\v12_octo_trend\daily\_daily_scan.py
#     python strategies\vol_legs\v2_boll_breakout\research\_plot_leg.py --leg a500 --leg hs300 --leg kc50
#     python strategies\vol_legs\v14_a500_e2\research\_plot_leg.py --years 3   # 近三年图同理
# 已退出默认流程(需要时手动跑):
#   python strategies\vol_legs\v1_price_volume\v1_1_ma20\daily\_daily_scan.py        # V1(默认) MA20 穿越(真实指数); V1-2 已于 2026-11-10 删除
#   python strategies\vol_legs\v7_us_gate\daily\_daily_scan.py       # V7 年线闸门(全七腿, 月末)
#   python strategies\vol_legs\v8_golden_cross\daily\_daily_scan.py  # V8 金叉 50/200(全七腿, 日频)
#   python strategies\vol_legs\v21_livermore_keypoint\daily\_daily_scan.py   # v21 规则仍在验证
# 参数权威源 strategies/vol_legs/rule.py::CFG 与 v3_macd_boll/macd_boll.py; 口径说明 strategies/vol_legs/README.md
# Usage: powershell -ExecutionPolicy Bypass -File run_daily_vol_legs.ps1
#        已有存量持仓时校准起点: ... -File run_daily_vol_legs.ps1 --hold a500=2.5,hs300=2.5,ndx=10,spx=10,kc50=2.5,hkt=2.5,gold=10
#        口径固定: 六腿真实指数 + 黄金场内 518880, 不再有切换开关
$ErrorActionPreference = "Stop"
Set-Location $PSScriptRoot
$env:PYTHONIOENCODING = "utf-8"
# 输出用 UTF-8, 否则中文经 subprocess 捕获会乱码
[Console]::OutputEncoding = [System.Text.Encoding]::UTF8
$OutputEncoding = [System.Text.Encoding]::UTF8

# 并行跑一组 python 脚本（v14~v20 的 scan 与图互不依赖、各写自己目录）。
# 用 .NET Process 而不是 Start-Process -PassThru：PS 5.1 下后者配 RedirectStandardOutput 时
# ExitCode 恒为 null，会把成功当失败告警。stdout/stderr 用异步读，避免子进程写满缓冲区阻塞；
# 结束后按传入顺序打印，失败只告警、不阻断主流程（与串行时"单腿失败保留上次产物"一致）。
function Invoke-PyParallel {
    param([string[]]$Scripts)
    $wd = (Get-Location).Path
    $utf8 = New-Object System.Text.UTF8Encoding $false
    $jobs = @()
    foreach ($s in $Scripts) {
        $psi = New-Object System.Diagnostics.ProcessStartInfo
        $psi.FileName = "python"
        $psi.Arguments = "-W ignore `"$s`""
        $psi.WorkingDirectory = $wd
        $psi.UseShellExecute = $false
        $psi.CreateNoWindow = $true
        $psi.RedirectStandardOutput = $true
        $psi.RedirectStandardError = $true
        $psi.StandardOutputEncoding = $utf8
        $psi.StandardErrorEncoding = $utf8
        $psi.EnvironmentVariables["PYTHONIOENCODING"] = "utf-8"
        $p = New-Object System.Diagnostics.Process
        $p.StartInfo = $psi
        [void]$p.Start()
        $jobs += [pscustomobject]@{
            P = $p
            S = $s
            O = $p.StandardOutput.ReadToEndAsync()
            E = $p.StandardError.ReadToEndAsync()
        }
    }
    $bad = @()
    foreach ($j in $jobs) {
        $j.P.WaitForExit()
        $out = $j.O.Result
        $err = $j.E.Result
        if ($out) { Write-Host $out.TrimEnd() }
        if ($j.P.ExitCode -ne 0) {
            if ($err) { Write-Host $err.TrimEnd() }
            $bad += "$($j.S) (exit $($j.P.ExitCode))"
        }
    }
    return $bad
}

Write-Host "[1/3] Recomputing 7-leg actions on real index closes (A500 2.5 / HS300 2.5 / NDX 10 / SPX 10 / STAR50 2.5 / HSTECH 2.5 / Gold 518880 10)..."
python -W ignore strategies\vol_legs\scan.py --force @args
if ($LASTEXITCODE -ne 0) { throw "vol_legs scan failed" }

# 第 2 步: v14~v20 七个单标的策略的每日产物（板块2 变种卡的一行一腿）
Write-Host ""
Write-Host "[2/3] Recomputing v14~v20 single-leg variants (7 parallel python processes) ..."
$legScans = @(
    "strategies\vol_legs\v14_a500_e2\daily\_daily_scan.py",
    "strategies\vol_legs\v15_hs300_min2\daily\_daily_scan.py",
    "strategies\vol_legs\v16_ndx_sw\daily\_daily_scan.py",
    "strategies\vol_legs\v17_spx_v12\daily\_daily_scan.py",
    "strategies\vol_legs\v18_kc50_e2tv\daily\_daily_scan.py",
    "strategies\vol_legs\v19_hkt_v3\daily\_daily_scan.py",
    "strategies\vol_legs\v20_gold_v5\daily\_daily_scan.py"
)
foreach ($b in @(Invoke-PyParallel -Scripts $legScans)) {
    Write-Warning "$b failed - 保留上次产物"
}

# 第 3 步: 只重画看板图墙嵌的七张 v14~v20 长期图(只重画, 不重算回测; 失败只告警)
Write-Host ""
Write-Host "[3/3] Refreshing 7 figure-wall PNGs (v14~v20 long) ..."

Write-Host "  - v14 中证A500 长期图(串行预热 matplotlib) ..."
python -W ignore strategies\vol_legs\v14_a500_e2\research\_plot_leg.py
if ($LASTEXITCODE -ne 0) { Write-Warning "v14 plot failed (exit $LASTEXITCODE) - 保留上次的图" }

Write-Host "  - v15~v20 六张长期图并行（6 个 python 进程）..."
$legFigs = @(
    "strategies\vol_legs\v15_hs300_min2\research\_plot_leg.py",
    "strategies\vol_legs\v16_ndx_sw\research\_plot_leg.py",
    "strategies\vol_legs\v17_spx_v12\research\_plot_leg.py",
    "strategies\vol_legs\v18_kc50_e2tv\research\_plot_leg.py",
    "strategies\vol_legs\v19_hkt_v3\research\_plot_leg.py",
    "strategies\vol_legs\v20_gold_v5\research\_plot_leg.py"
)
foreach ($b in @(Invoke-PyParallel -Scripts $legFigs)) {
    Write-Warning "$b failed - 保留上次的图"
}

# ---- 板块2「近五年口径」对照卡：只刷「今日目标仓位」，**不重跑排名研究** ----
# 重跑 `_beat_best_5y.py` 要 ~40s 且会改写研究产物；落地清单沿用上次的 `_beat_best_5y.jsonl`。
# 卡片（pipeline/daily_digest.py::parse_vol_5y）读 `_daily_positions_5y.json`，
# 该 json 里带生成时间与行情日，过期会在卡片 badge 上标「⚠ 过期 N 天」。
Write-Host "  - beat_best_5y 今日目标仓位（看板板块2 5y 对照卡）..."
python -W ignore (Join-Path $PSScriptRoot "strategies\vol_legs\research\beat_best_5y\_daily_positions_5y.py")
if ($LASTEXITCODE -ne 0) { Write-Warning "5y 仓位快照失败（exit $LASTEXITCODE）— 看板将沿用上次 json" }

Write-Host ""
Write-Host "Done."
Write-Host "  Report  : strategies\vol_legs\daily\_vol_legs_report.md"
Write-Host "  Snapshot: strategies\vol_legs\daily\_vol_legs_signal.json"
Write-Host "  Universe: strategies\vol_legs\daily\_universe_vol_legs_active.md"
Write-Host "  Variants: v14~v20 daily\_*_daily.md (+ .json)"
Write-Host "  Figures : strategies\vol_legs\v14~v20\research\_v{14..20}_{leg}.png（看板图墙这 7 张）"
Write-Host "  Digest  : run_daily_all.ps1 末尾会跑 python pipeline\daily_digest.py, 汇总到 data\_daily_digest.html"
Write-Host "  Note    : signal on real index closes; six index legs execute via OTC A-class funds, gold"
Write-Host "            via on-exchange 518880. Place the OTC order BEFORE 15:00 next trading day;"
Write-Host "            QDII NAV prints T+1~T+2 -- do not cancel inside the window."
Write-Host "  Note    : index cache = data\_global_index_klines.json / _cn_index_klines.json /"
Write-Host "            _us_index_klines.json + data\_long_klines.json (gold 518880)."
Write-Host "            No bridging funds, no second price caliber."
Write-Host "  Note    : v2/v3/v4/v6/v9/v12 与 _3y.png 不再每日跑, 手动命令见脚本头注释."

# 单独跑本脚本时, 顺手把各变种产物汇总进看板; 若由 run_daily_all.ps1 批量调用则跳过(它末尾统一 digest)。
if (-not $env:XA_DAILY_BATCH) {
    Write-Host ""
    Write-Host "[digest] 汇总每日操作 -> data\_daily_digest.html"
    python -W ignore (Join-Path $PSScriptRoot "pipeline\daily_digest.py")
    if ($LASTEXITCODE -ne 0) { Write-Warning "汇总失败: 各变种产物已生成, 但看板未更新" }
}
