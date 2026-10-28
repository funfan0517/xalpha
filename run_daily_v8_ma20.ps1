# 技术指标变种8 · MA20 穿越（固定 4 只标的池）每日操作 - 双击运行
# 盘后运行: 刷新行情 -> 对池内 4 只(科创50/恒生科技/中证A500/黄金)判 上穿MA20买入 / 下穿MA20卖出
# 用法: powershell -ExecutionPolicy Bypass -File run_daily_v8_ma20.ps1
$ErrorActionPreference = "Stop"
Set-Location $PSScriptRoot
$env:PYTHONIOENCODING = "utf-8"

Write-Host "[1/1] 生成 MA20 穿越每日买卖标的 (4 只标的池)..."
python -W ignore strategies\tech_indicators\v8_ma20\daily\_daily_scan.py
if ($LASTEXITCODE -ne 0) { throw "v8_ma20 daily failed" }

Write-Host ""
Write-Host "Done. 报告: strategies\tech_indicators\v8_ma20\daily\_v8_ma20_daily.md"
Write-Host "[i] 池定义在 strategies\tech_indicators\v8_ma20\backtest.py::POOL, 增删标的只改那一处."
Write-Host "[i] 信号用收盘(盘后), 执行建议次日开盘; 未计佣金滑点."
Write-Host "[i] 机械规则, 非投资建议."
