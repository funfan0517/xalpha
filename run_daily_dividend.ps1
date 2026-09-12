# 中证红利ETF 策略每日信号 - 双击运行
# 自动抓取: 蛋卷 SH000922 股息率/PE分位(10年) + 10Y 国债收益率 -> 三指标分区 + 合成动作
# 人工项(可选): data/_dividend_redline.json (§4.4 成分股暴雷/下调分红/系统性红线)
# Usage: powershell -ExecutionPolicy Bypass -File run_daily_dividend.ps1
$ErrorActionPreference = "Stop"
Set-Location $PSScriptRoot
$env:PYTHONIOENCODING = "utf-8"

Write-Host "[1/2] Generating dividend ETF daily signal..."
python -W ignore strategies\dividend\_signal.py
if ($LASTEXITCODE -ne 0) { throw "dividend signal failed" }

Write-Host "[2/2] Ranking dividend funds (multi-target scoring)..."
python -W ignore strategies\dividend\rank.py --refresh
if ($LASTEXITCODE -ne 0) { throw "dividend rank failed" }

Write-Host ""
Write-Host "Done. Signal: strategies\dividend\daily\_signal_report.md ; Snapshot: strategies\dividend\daily\_dividend_signal.json"
Write-Host "      Rank:   strategies\dividend\daily\_dividend_rank.md   ; JSON:     strategies\dividend\daily\_dividend_rank.json"
Write-Host "[i] 估值锚(蛋卷)通常滞后一个交易日; PE分位口径与回测重建口径略有差异, 以蛋卷为准时看趋势不看小数点."
Write-Host "[i] 打分窗权 3年50/5年30/10年20 由 backtest_rank.py 回测确认; 重跑: python strategies\dividend\backtest_rank.py"
