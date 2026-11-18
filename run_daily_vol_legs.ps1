# 波动资产操作规则 v1.3(A500 5 / 纳指100 10 / 标普500 10 / 科创50 2.5 / 恒生科技 2.5 / 黄金 10 = 40pp) · 每日操作 - 双击运行

# 口径: 信号 + 回测 + 记账 **全部用该市场真实指数收盘**; 黄金用场内 518880; 无口径开关
# 盘后运行: 刷新指数库(SH000510/SH000300/.NDX/.INX/SH000688/HKHSTECH/518880) -> 复算七腿状态
#          (回撤阶梯/趋势闸门/季度再平衡) -> 输出「下一交易日该下单的那一笔」
# 第 2 步: 变种每日产物 —— v2 BOLL突破 / v3 MACD+BOLL / v4 ERP股债通道 / v6 黄金三口径 / v9 波动率目标 / v12 八态趋势
#          (v2/v3 七腿逐腿进出时点; v4 = 沪深300×国债 ERP 通道, 单标的;
#           v6 = 黄金单标的的三口径 A 长期配置 / B 控回撤 / C 低仓位占坑;
#           v9 = 全七腿波动率目标仓位(2026-11-17 扩池) + 按腿三开关(2026-11-18 定稿: 标普010/其余111),
#                月末定仓 + 月中快减/快补; 看板图仍只出纳指/标普两张
#           v12 = 八态趋势(急涨/慢涨/弱回调/强回调 | 急跌/慢跌/弱反弹/强反弹) -> 仓位许可上限,
#                 执行范围=全七腿(2026-11-18 由「只做沪深300」扩到七腿, 额度按回测 §1c 的 Δ夏普排);
#                 它是唯一**硬依赖成交量**的变种, 价格与量能都取同源真实指数)
#          产物写入各变种 daily\*, 由 pipeline\daily_digest.py 汇总到板块2「变种操作一览」卡。
# 第 3 步: 刷新十二张重点图的 PNG(复用第 1/2 步刚刷新的缓存, 只重画不重算回测):
#          v2_boll_breakout\research\_v2_a500.png / _v2_hs300.png / _v2_kc50.png (科创50 2026-11-13 起纳入重点)
#          v3_macd_boll\research\_v3_hkt.png / _v3_kc50.png
#          v4_erp_band\backtest\_v4_erp_band_signals.png (走 research\_plot_signals.py, 不碰回测报告)
#          v6_gold\daily\_v6_gold_A.png / _v6_gold_B.png / _v6_gold_C.png (三口径全历史)
#          v9_vol_target\research\_v9_vt_spx.png / _v9_vt_ndx.png (看板只嵌两条重点美股腿 · TV15%; 池已扩七腿但图仍只这两张)
#          v12_octo_trend\research\_v12_state_hs300.png (八态色带 + 许可阶梯)
#          出图失败只告警, 不阻断主流程(保留上次的图)。
# 已退出默认流程(需要时手动跑):
#   python strategies\vol_legs\v1_price_volume\v1_1_ma20\daily\_daily_scan.py        # V1(默认) MA20 穿越(真实指数); V1-2 已于 2026-11-10 删除
#   python strategies\vol_legs\v7_us_gate\daily\_daily_scan.py       # V7 年线闸门(全七腿, 月末)
#   python strategies\vol_legs\v8_golden_cross\daily\_daily_scan.py  # V8 金叉 50/200(全七腿, 日频)
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

Write-Host "[1/3] Recomputing 7-leg actions on real index closes (A500 2.5 / HS300 2.5 / NDX 10 / SPX 10 / STAR50 2.5 / HSTECH 2.5 / Gold 518880 10)..."
python -W ignore strategies\vol_legs\scan.py --force @args
if ($LASTEXITCODE -ne 0) { throw "vol_legs scan failed" }

# 第 2 步: 变种逐腿/单标的每日产物(v2 BOLL / v3 MACD+BOLL / v4 ERP股债 / v6 黄金三口径 / v9 波动率目标)
# 复用第 1 步刚刷新的指数库缓存; v4 额外 --refresh 更新自己的 CSI/国债 CSV。
# 变种单跑失败只告警、不阻断主流程(保留上次产物, 汇总卡照常出)。
Write-Host ""
Write-Host "[2/3] Recomputing variants (v2 BOLL / v3 MACD+BOLL / v4 ERP / v6 gold A-B-C / v9 vol target) ..."

Write-Host "  - v2 BOLL breakout ..."
python -W ignore strategies\vol_legs\v2_boll_breakout\daily\_daily_scan.py
if ($LASTEXITCODE -ne 0) { Write-Warning "v2_boll daily scan failed (exit $LASTEXITCODE) - 保留上次产物" }

Write-Host "  - v3 MACD+BOLL ..."
python -W ignore strategies\vol_legs\v3_macd_boll\daily\_daily_scan.py
if ($LASTEXITCODE -ne 0) { throw "v3_macd_boll daily scan failed" }

Write-Host "  - v4 ERP band ..."
python -W ignore strategies\vol_legs\v4_erp_band\daily\_daily_scan.py --refresh
if ($LASTEXITCODE -ne 0) { Write-Warning "v4_erp_band daily scan failed (exit $LASTEXITCODE) - 保留上次产物" }

Write-Host "  - v6 gold A/B/C ..."
# --no-refresh: A/B/C 共用 518880 同一条序列, 直接复用第 1 步刷新的行情库缓存
python -W ignore strategies\vol_legs\v6_gold\daily\_daily_scan.py --no-refresh
if ($LASTEXITCODE -ne 0) { Write-Warning "v6_gold daily scan failed (exit $LASTEXITCODE) - 保留上次产物" }

Write-Host "  - v9 vol target ..."
# --no-refresh: 第 1 步 scan.py --force 已刷新美股指数(.NDX/.INX), 这里直接复用
python -W ignore strategies\vol_legs\v9_vol_target\daily\_daily_scan.py --no-refresh
if ($LASTEXITCODE -ne 0) { Write-Warning "v9_vol_target daily scan failed (exit $LASTEXITCODE) - 保留上次产物" }

Write-Host "  - v12 octo trend (八态) ..."
# 默认联网: v12 需要**最新成交量**（同源指数量能 + 518880 自身量）, 第 1 步只刷收盘价, 没有量。
# 网络失败时脚本内部退化为用缓存并在报告里标注, 不会中断。
python -W ignore strategies\vol_legs\v12_octo_trend\daily\_daily_scan.py
if ($LASTEXITCODE -ne 0) { Write-Warning "v12_octo_trend daily scan failed (exit $LASTEXITCODE) - 保留上次产物" }

# 第 3 步: 刷新十二张重点图(只重画, 不重算回测; 失败只告警)
Write-Host ""
Write-Host "[3/3] Refreshing 12 key figures (v2 a500/hs300/kc50 · v3 hkt/kc50 · v4 erp signals · v6 gold A/B/C · v9 spx/ndx · v12 hs300) ..."

Write-Host "  - v2_boll_breakout\research\_v2_a500.png / _v2_hs300.png / _v2_kc50.png ..."
python -W ignore strategies\vol_legs\v2_boll_breakout\research\_plot_leg.py --leg a500 --leg hs300 --leg kc50
if ($LASTEXITCODE -ne 0) { Write-Warning "v2 plot_leg failed (exit $LASTEXITCODE) - 保留上次的图" }

Write-Host "  - v3_macd_boll\research\_v3_hkt.png / _v3_kc50.png ..."
python -W ignore strategies\vol_legs\v3_macd_boll\research\_plot_leg.py --leg hkt --leg kc50
if ($LASTEXITCODE -ne 0) { Write-Warning "v3 plot_leg failed (exit $LASTEXITCODE) - 保留上次的图" }

Write-Host "  - v4_erp_band\backtest\_v4_erp_band_signals.png ..."
python -W ignore strategies\vol_legs\v4_erp_band\research\_plot_signals.py
if ($LASTEXITCODE -ne 0) { Write-Warning "v4 plot_signals failed (exit $LASTEXITCODE) - 保留上次的图" }

Write-Host "  - v6_gold\daily\_v6_gold_A.png / _v6_gold_B.png / _v6_gold_C.png ..."
# 默认全历史版(2014-09 起); 近 3 年版 _v6_gold_*_3y.png 不进看板, 需要时手动加 --years 3
python -W ignore strategies\vol_legs\v6_gold\daily\_plot_legs.py
if ($LASTEXITCODE -ne 0) { Write-Warning "v6 plot_legs failed (exit $LASTEXITCODE) - 保留上次的图" }

Write-Host "  - v9_vol_target\research\_v9_vt_spx.png / _v9_vt_ndx.png ..."
python -W ignore strategies\vol_legs\v9_vol_target\research\_plot_target.py --leg spx --leg ndx
if ($LASTEXITCODE -ne 0) { Write-Warning "v9 plot_target failed (exit $LASTEXITCODE) - 保留上次的图" }

Write-Host "  - v12_octo_trend\research\_v12_state_hs300.png ..."
# --leg hs300: 执行范围 2026-11-18 已扩到七腿, 但看板只嵌标定腿这一张; 不传 --leg 会画全部七张(慢且无用)
python -W ignore strategies\vol_legs\v12_octo_trend\research\_plot_state.py --leg hs300
if ($LASTEXITCODE -ne 0) { Write-Warning "v12 plot_state failed (exit $LASTEXITCODE) - 保留上次的图" }

Write-Host ""
Write-Host "Done."
Write-Host "  Report  : strategies\vol_legs\daily\_vol_legs_report.md"
Write-Host "  Snapshot: strategies\vol_legs\daily\_vol_legs_signal.json"
Write-Host "  Universe: strategies\vol_legs\daily\_universe_vol_legs_active.md"
Write-Host "  Variants: v2_boll_breakout / v3_macd_boll / v4_erp_band / v6_gold / v9_vol_target / v12_octo_trend  daily\_*_daily.md (+ .json)"
Write-Host "  Figures : v2\research\_v2_{a500,hs300,kc50}.png · v3\research\_v3_{hkt,kc50}.png ·"
Write-Host "            v4\backtest\_v4_erp_band_signals.png ·"
Write-Host "            v6_gold\daily\_v6_gold_{A,B,C}.png · v9\research\_v9_vt_{spx,ndx}.png ·"
Write-Host "            v12_octo_trend\research\_v12_state_hs300.png"
Write-Host "  Digest  : run_daily_all.ps1 末尾会跑 python pipeline\daily_digest.py, 汇总到 data\_daily_digest.html"
Write-Host "  Note    : signal on real index closes; six index legs execute via OTC A-class funds, gold"
Write-Host "            via on-exchange 518880. Place the OTC order BEFORE 15:00 next trading day;"
Write-Host "            QDII NAV prints T+1~T+2 -- do not cancel inside the window."
Write-Host "  Note    : index cache = data\_global_index_klines.json / _cn_index_klines.json /"
Write-Host "            _us_index_klines.json + data\_long_klines.json (gold 518880)."
Write-Host "            No bridging funds, no second price caliber."

# 单独跑本脚本时, 顺手把各变种产物汇总进看板; 若由 run_daily_all.ps1 批量调用则跳过(它末尾统一 digest)。
if (-not $env:XA_DAILY_BATCH) {
    Write-Host ""
    Write-Host "[digest] 汇总每日操作 -> data\_daily_digest.html"
    python -W ignore (Join-Path $PSScriptRoot "pipeline\daily_digest.py")
    if ($LASTEXITCODE -ne 0) { Write-Warning "汇总失败: 各变种产物已生成, 但看板未更新" }
}
