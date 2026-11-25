# 成长/红利风格轮动 每日信号 - 双击运行
# 盘后运行: 增量刷新指数行情 -> 计算风格比值/均线/分位 -> 输出次日开盘操作信号
# 板块3 的每日看板只出 v5 一张卡(gd_v5)，2026-11-25 用户要求「和看板无关的策略不要执行」后
# 本脚本只跑两件看板必需的事：data.py 增量刷新 + v5 scan（内含 _v5_alloc.png 重画）。
# v0（MA20 缓冲带 + 250日分位）与 v2（绝对水位 0.35/0.65）都不出卡，不再每日跑；需要时手动:
#   python strategies\growth_div_rotation\v0\scan.py
#   python strategies\growth_div_rotation\v2_absolute_bands\scan.py
# v1 (绝对阈值 0.20/0.31 + 5期分批) 2026-11-02 起退出看板: python strategies\growth_div_rotation\v1_ratio_bands\scan.py
# 用法: powershell -ExecutionPolicy Bypass -File run_daily_growth_div_rotation.ps1
$ErrorActionPreference = "Stop"
Set-Location $PSScriptRoot
$env:PYTHONIOENCODING = "utf-8"

Write-Host "[1/2] 增量刷新指数行情缓存..."
python -W ignore strategies\growth_div_rotation\v0\data.py
if ($LASTEXITCODE -ne 0) { throw "行情刷新失败" }

Write-Host "[2/2] 生成 v5 状态 (水位 + 双 MA200 + 18% 波动目标)..."
python -W ignore strategies\growth_div_rotation\v5_vol_target\scan.py
if ($LASTEXITCODE -ne 0) { throw "v5 状态生成失败" }

Write-Host ""
Write-Host "Done. v5 报告: strategies\growth_div_rotation\v5_vol_target\daily\_gd_v5_daily.md ; 快照: v5_vol_target\daily\_gd_v5_signal.json"
Write-Host "      v5 图  : strategies\growth_div_rotation\v5_vol_target\backtest\_v5_alloc.png (每日重画, 看板板块3 的 v5 卡片内嵌)"
Write-Host "      v0/v2  : 不出卡, 不再每日跑（手动命令见脚本头注释）"
Write-Host "[i] 信号用指数收盘(盘后), 执行用次日开盘价(场外按当日15:00前净值); 机械规则, 非投资建议."
