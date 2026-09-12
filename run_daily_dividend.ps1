# 红利基金打分策略每日推荐 - 双击运行
# 多标的横向打分: 业绩分(3/5/10 年区间年化 + 最大回撤, 池内横截面分位)
#               + 价值分(PE / PE10年分位 / 股息率 / 股债收益比 / RSI14, 五项各 20 分)
# 输出: 总分排序 + 估值定性 + 操作建议
# Usage: powershell -ExecutionPolicy Bypass -File run_daily_dividend.ps1
$ErrorActionPreference = "Stop"
Set-Location $PSScriptRoot
$env:PYTHONIOENCODING = "utf-8"

Write-Host "[1/1] Ranking dividend funds (multi-target scoring)..."
python -W ignore strategies\dividend\rank.py --refresh
if ($LASTEXITCODE -ne 0) { throw "dividend rank failed" }

Write-Host ""
Write-Host "Done. Rank: strategies\dividend\daily\_dividend_rank.md ; JSON: strategies\dividend\daily\_dividend_rank.json"
Write-Host "[i] 估值锚(中证官网/蛋卷)通常滞后一个交易日; PE分位 = 官网 peg 自算「过去最多10年」累计分位."
Write-Host "[i] 打分窗权 3年50/5年30/10年20 由 backtest_rank.py 回测确认; 重跑: python strategies\dividend\backtest_rank.py"
