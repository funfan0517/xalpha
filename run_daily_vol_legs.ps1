# 波动资产操作规则 v1.3(A500 5 / 纳指100 10 / 标普500 10 / 科创50 2.5 / 恒生科技 2.5 / 黄金 10 = 40pp) · 每日操作 - 双击运行

# 口径: 信号 + 记账 + 执行 **全部用场外基金复权净值**(rule.SIGNAL_MODE="otc")
# 盘后运行: 刷新场外净值(023299/000051/270042/017641/011609/012805/000216) -> 复算七腿状态
#          (回撤阶梯/趋势闸门/季度再平衡) -> 输出「下一交易日该下单的那一笔」
# 第 2 步: 变种每日产物 —— v2 BOLL突破 / v3 MACD+BOLL / v4 ERP股债通道 / v5 趋势闸门
#          (v2/v3/v5 七腿逐腿进出时点; v4 = 沪深300×国债 ERP 通道, 单标的)
#          产物写入各变种 daily\*, 由 pipeline\daily_digest.py 汇总到板块2「变种操作一览」卡。
# 第 3 步: 刷新六张重点图的 PNG(复用第 1/2 步刚刷新的缓存, 只重画不重算回测):
#          v2_boll_breakout\research\_v2_a500.png / _v2_hs300.png
#          v3_macd_boll\research\_v3_hkt.png / _v3_kc50.png
#          v4_erp_band\backtest\_v4_erp_band_signals.png (走 research\_plot_signals.py, 不碰回测报告)
#          v5_trend_gate\research\_v5_trend_gold_3y.png
#          出图失败只告警, 不阻断主流程(保留上次的图)。
# 已退出默认流程(需要时手动跑):
#   python strategies\vol_legs\v1_ma20\daily\_daily_scan.py          # V1 MA20 穿越
# 参数权威源 strategies/vol_legs/rule.py::CFG 与 v3_macd_boll/macd_boll.py; 口径说明 strategies/vol_legs/README.md
# Usage: powershell -ExecutionPolicy Bypass -File run_daily_vol_legs.ps1
#        已有存量持仓时校准起点: ... -File run_daily_vol_legs.ps1 --hold a500=2.5,hs300=2.5,ndx=10,spx=10,kc50=2.5,hkt=2.5,gold=10
#        切回场内/美元指数口径: 把 rule.SIGNAL_MODE 改成 "exchange"
$ErrorActionPreference = "Stop"
Set-Location $PSScriptRoot
$env:PYTHONIOENCODING = "utf-8"
# 输出用 UTF-8, 否则中文经 subprocess 捕获会乱码
[Console]::OutputEncoding = [System.Text.Encoding]::UTF8
$OutputEncoding = [System.Text.Encoding]::UTF8

Write-Host "[1/3] Recomputing 7-leg actions on OTC NAV (A500 2.5 / HS300 2.5 / NDX 10 / SPX 10 / STAR50 2.5 / HSTECH 2.5 / Gold 10)..."
python -W ignore strategies\vol_legs\scan.py --force @args
if ($LASTEXITCODE -ne 0) { throw "vol_legs scan failed" }

# 第 2 步: 四个变种逐腿/单标的每日产物(v2 BOLL / v3 MACD+BOLL / v4 ERP股债 / v5 趋势闸门)
# 复用第 1 步刚刷新的场外净值缓存; v4 额外 --refresh 更新自己的 CSI/国债 CSV。
# 变种单跑失败只告警、不阻断主流程(保留上次产物, 汇总卡照常出)。
Write-Host ""
Write-Host "[2/3] Recomputing variants (v2 BOLL / v3 MACD+BOLL / v4 ERP / v5 trend gate) ..."

Write-Host "  - v2 BOLL breakout ..."
python -W ignore strategies\vol_legs\v2_boll_breakout\daily\_daily_scan.py
if ($LASTEXITCODE -ne 0) { Write-Warning "v2_boll daily scan failed (exit $LASTEXITCODE) - 保留上次产物" }

Write-Host "  - v3 MACD+BOLL ..."
python -W ignore strategies\vol_legs\v3_macd_boll\daily\_daily_scan.py
if ($LASTEXITCODE -ne 0) { throw "v3_macd_boll daily scan failed" }

Write-Host "  - v4 ERP band ..."
python -W ignore strategies\vol_legs\v4_erp_band\daily\_daily_scan.py --refresh
if ($LASTEXITCODE -ne 0) { Write-Warning "v4_erp_band daily scan failed (exit $LASTEXITCODE) - 保留上次产物" }

Write-Host "  - v5 trend gate ..."
python -W ignore strategies\vol_legs\v5_trend_gate\daily\_daily_scan.py --no-refresh
if ($LASTEXITCODE -ne 0) { Write-Warning "v5_trend_gate daily scan failed (exit $LASTEXITCODE) - 保留上次产物" }

# 第 3 步: 刷新六张重点图(只重画, 不重算回测; 失败只告警)
Write-Host ""
Write-Host "[3/3] Refreshing 6 key figures (v2 a500/hs300 · v3 hkt/kc50 · v4 erp signals · v5 gold 3y) ..."

Write-Host "  - v2_boll_breakout\research\_v2_a500.png / _v2_hs300.png ..."
python -W ignore strategies\vol_legs\v2_boll_breakout\research\_plot_leg.py --leg a500 --leg hs300
if ($LASTEXITCODE -ne 0) { Write-Warning "v2 plot_leg failed (exit $LASTEXITCODE) - 保留上次的图" }

Write-Host "  - v3_macd_boll\research\_v3_hkt.png / _v3_kc50.png ..."
python -W ignore strategies\vol_legs\v3_macd_boll\research\_plot_leg.py --leg hkt --leg kc50
if ($LASTEXITCODE -ne 0) { Write-Warning "v3 plot_leg failed (exit $LASTEXITCODE) - 保留上次的图" }

Write-Host "  - v4_erp_band\backtest\_v4_erp_band_signals.png ..."
python -W ignore strategies\vol_legs\v4_erp_band\research\_plot_signals.py
if ($LASTEXITCODE -ne 0) { Write-Warning "v4 plot_signals failed (exit $LASTEXITCODE) - 保留上次的图" }

Write-Host "  - v5_trend_gate\research\_v5_trend_gold_3y.png ..."
python -W ignore strategies\vol_legs\v5_trend_gate\research\_plot_trend.py --years 3 --leg gold
if ($LASTEXITCODE -ne 0) { Write-Warning "v5 plot_trend failed (exit $LASTEXITCODE) - 保留上次的图" }

Write-Host ""
Write-Host "Done."
Write-Host "  Report  : strategies\vol_legs\daily\_vol_legs_report.md"
Write-Host "  Snapshot: strategies\vol_legs\daily\_vol_legs_signal.json"
Write-Host "  Universe: strategies\vol_legs\daily\_universe_vol_legs_active.md"
Write-Host "  Variants: v2_boll_breakout / v3_macd_boll / v4_erp_band / v5_trend_gate  daily\_*_daily.md (+ .json)"
Write-Host "  Figures : v2\research\_v2_{a500,hs300}.png · v3\research\_v3_{hkt,kc50}.png ·"
Write-Host "            v4\backtest\_v4_erp_band_signals.png · v5\research\_v5_trend_gold_3y.png"
Write-Host "  Digest  : run_daily_all.ps1 末尾会跑 python pipeline\daily_digest.py, 汇总到 data\_daily_digest.html"
Write-Host "  Note    : signal on NAV at close, place the order BEFORE 15:00 next trading day to get"
Write-Host "            that day's NAV. QDII NAV prints T+1~T+2 -- do not cancel inside the window."
Write-Host "  Note    : all seven legs trade OTC share classes only (never chase on-exchange premium);"
Write-Host "            if the QDII quota is closed, the tier is deferred, not cancelled."
Write-Host "  Note    : NAV cache = data\_otc_nav.json; codes 023299 / 000051 / 270042 / 017641 / 011609 /"
Write-Host "            012805 / 000216; 000051 is BOTH the HS300 leg itself and the A500 warm-up series"
Write-Host "            (they are identical before 2025-03-27), 050025 is the SPX warm-up."

# 单独跑本脚本时, 顺手把各变种产物汇总进看板; 若由 run_daily_all.ps1 批量调用则跳过(它末尾统一 digest)。
if (-not $env:XA_DAILY_BATCH) {
    Write-Host ""
    Write-Host "[digest] 汇总每日操作 -> data\_daily_digest.html"
    python -W ignore (Join-Path $PSScriptRoot "pipeline\daily_digest.py")
    if ($LASTEXITCODE -ne 0) { Write-Warning "汇总失败: 各变种产物已生成, 但看板未更新" }
}
