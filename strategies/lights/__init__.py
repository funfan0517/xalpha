# -*- coding: utf-8 -*-
"""lights —— 亮灯策略（Lights）：逐标的独立状态机的场内 ETF 择时策略。

模块分工:
  rule.py      ★ 参数权威源（ACTIVE）+ 灯/门槛/事件注册表 + 配置文件（base_config/active_config）
  factors.py   因子库（唯一实现）: 均线 / ADX / MACD / 周线多头 / 量能 / 换手分位 /
               主力强度代理 / 分化度
  engine.py    统一状态机: 调仓窗口 + 目标仓位 + 回撤止损 + 持仓上限 + 逐日成本
  data.py      标的池与行情面板（唯一池 data/_universe.md，本地缓存）
  backtest.py  回测入口      _reportbt.py    回测报告
  scan.py      每日信号扫描  _scan_report.py 每日操作报告
  _sync_meta.py  把 pipeline/strategies.json 的参数镜像块从 ACTIVE 同步

约定:
  * 因子函数同时支持 **单标的 Series** 与 **全池面板 DataFrame**（列=标的）,
    同一定义两处可用、结果一致。
  * 引擎一律「当日收盘算信号 -> 次日开盘成交」, 无前视; 成本 = 单边佣金 (+ 可选滑点)。
  * 数据一律用**标的自身交易日**（不做 ffill）, 避免停牌日被补齐后污染量比类因子。
"""
