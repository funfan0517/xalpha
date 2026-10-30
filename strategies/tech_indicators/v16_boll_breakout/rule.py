# -*- coding: utf-8 -*-
"""变种 16 · BOLL 上轨突破（趋势跟踪）单指标规则。

与变种 15（`../v15_boll/`：触下轨买、触上轨卖，**反转**）方向相反，因此**独立成变种**，
不改 v15 —— v15 是家族基线 BOLL，v7 风格路由的「防御」档与每日汇总都复用它。

**本文件是本策略参数的唯一权威源**（窗口/带宽/止损都只在这里改）。

指标（价格 = 收盘价，仅用当日及之前数据）::

    mid   = MA(close, 35)
    std   = STD(close, 35)          # pandas rolling(n).std()，ddof=1
    upper = mid + 2.0 * std
    lower = mid - 2.0 * std         # 本策略不用下轨（不做反向下轨抄底）

交易规则（0/1，只做多，无反向开空）：

- **开多**：`close[t] > upper[t]` 且 `close[t-1] <= upper[t-1]` —— 要求「上穿上轨」而非
  「站在上轨上方」，避免同一段趋势里重复发信号；
- **平仓**：`close[t] < mid[t]` —— 跌回中轨即退出，不等下轨；
- 上下轨/中轨为 NaN（窗口未满）时维持上一状态；
- **盘中触碰不算成交信号，只看收盘**；
- 无信号时空仓等待（本策略曝光率通常很低，长期大部分时间空仓）。

可选止损（`stop`，默认关闭）：自**建仓日收盘价**回撤达到 `stop`（如 0.08 = -8%）且当日
收盘仍低于成本价则平仓。止损同样只在收盘判定，不用盘中最低价 —— 与本策略「只看收盘」一致。
止损会把部分「假突破 → 小亏 → 后续大盈利」的路径提前切断，是否启用见回测报告敏感性表。

口径提醒：`signal()` 只输出**目标仓位**，成交价由回测/实盘层决定。家族基线口径是
「信号当日收盘成交」，实盘应改为次日开盘或限价单并计入滑点/手续费/涨跌停/停牌，
两者差异见 `backtest.py` 的 lag/费用敏感性表。模拟结果，非投资建议。
"""
import numpy as np
import pandas as pd

VARIANT = "BOLL_BREAK"
FAMILY_KEY = "BOLL_BREAK"
DESC = "收盘上穿上轨（前一日未站上）买入；收盘跌回中轨卖出"
PARAMS = {"N": 35, "K": 2.0, "STOP": None}     # 指标在本文件内计算（指标库只预计算 BOLL(20,2)）


def bands(close, n=None, k=None):
    """返回 (mid, upper, lower)，NaN 表示窗口未满。与 data/_gen_indicators.boll_series 同口径。"""
    n = PARAMS["N"] if n is None else int(n)
    k = PARAMS["K"] if k is None else float(k)
    s = pd.Series(np.asarray(close, dtype=float))
    mid = s.rolling(n).mean()
    std = s.rolling(n).std()
    return mid.to_numpy(), (mid + k * std).to_numpy(), (mid - k * std).to_numpy()


def signal(close, r=None, stop=None, n=None, k=None):
    """按 BOLL 上轨突破生成目标仓位序列（0/1，逐日因果）。

    close: np.ndarray 收盘价；r: 兼容家族引擎的指标字典（**本策略不使用**，指标自算）；
    stop:  可选止损比例（如 0.08），None=不启用；n/k: 覆盖 PARAMS 中的窗口/带宽（供敏感性测试）。
    """
    close = np.asarray([np.nan if v is None else float(v) for v in close], dtype=float)
    n = PARAMS["N"] if n is None else int(n)
    k = PARAMS["K"] if k is None else float(k)
    stop = PARAMS["STOP"] if stop is None else float(stop)
    mid, upper, _lower = bands(close, n, k)

    size = len(close)
    sig = np.zeros(size, dtype=int)
    pos, entry_px = 0, np.nan
    for t in range(size):
        if np.isnan(upper[t]) or np.isnan(mid[t]):
            sig[t] = pos                                  # 窗口未满：维持
            continue
        if pos == 0:
            if t >= 1 and close[t] > upper[t] and close[t - 1] <= upper[t - 1]:
                pos, entry_px = 1, close[t]               # 上穿上轨才买，避免重复信号
        else:
            hit_mid = close[t] < mid[t]                   # 跌回中轨
            hit_stop = stop is not None and close[t] <= entry_px * (1.0 - stop)
            if hit_mid or hit_stop:
                pos, entry_px = 0, np.nan
        sig[t] = pos
    return sig
