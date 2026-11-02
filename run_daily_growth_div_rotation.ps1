# 成长/红利风格轮动 每日信号 - 双击运行
# 盘后运行: 增量刷新指数行情 -> 计算风格比值/均线/分位 -> 输出次日开盘操作信号
# 板块3 的每日看板展示 v0 / v2 / v5 三张卡, 各自落一份产物:
#   v0 (MA20 缓冲带 + 250日分位) / v2 (绝对水位 0.35/0.65) / v5 (水位 + 双 MA200 + 18% 波动目标)
# v1 (绝对阈值 0.20/0.31 + 5期分批) 2026-11-02 起退出看板, 不再每日跑; 需要时手跑 v1_ratio_bands/scan.py
# 用法: powershell -ExecutionPolicy Bypass -File run_daily_growth_div_rotation.ps1
$ErrorActionPreference = "Stop"
Set-Location $PSScriptRoot
$env:PYTHONIOENCODING = "utf-8"

Write-Host "[1/4] 增量刷新指数行情缓存..."
python -W ignore strategies\growth_div_rotation\v0\data.py
if ($LASTEXITCODE -ne 0) { throw "行情刷新失败" }

Write-Host "[2/4] 生成 v0 信号 (MA20 缓冲带 + 250日分位)..."
python -W ignore strategies\growth_div_rotation\v0\scan.py
if ($LASTEXITCODE -ne 0) { throw "v0 信号生成失败" }

Write-Host "[3/4] 生成 v2 状态 (绝对水位 0.35/0.65)..."
python -W ignore strategies\growth_div_rotation\v2_absolute_bands\scan.py
if ($LASTEXITCODE -ne 0) { throw "v2 状态生成失败" }

Write-Host "[4/4] 生成 v5 状态 (水位 + 双 MA200 + 18% 波动目标)..."
python -W ignore strategies\growth_div_rotation\v5_vol_target\scan.py
if ($LASTEXITCODE -ne 0) { throw "v5 状态生成失败" }

Write-Host ""
Write-Host "Done. v0 报告: strategies\growth_div_rotation\v0\daily\_signal_report.md ; 快照: v0\daily\_gd_signal.json"
Write-Host "      v2 报告: strategies\growth_div_rotation\v2_absolute_bands\daily\_gd_v2_daily.md ; 快照: v2_absolute_bands\daily\_gd_v2_signal.json"
Write-Host "      v5 报告: strategies\growth_div_rotation\v5_vol_target\daily\_gd_v5_daily.md ; 快照: v5_vol_target\daily\_gd_v5_signal.json"
Write-Host "[i] 信号用指数收盘(盘后), 执行用次日开盘价(场外按当日15:00前净值); 机械规则, 非投资建议."
