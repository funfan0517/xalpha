# -*- coding: utf-8 -*-
r"""因子库（唯一实现）—— 亮灯策略的因子全部在这里, 不散落在各调用点。

为什么存在: 三个策略此前各自实现了一遍均线/ADX/MACD/周线/量能/换手/主力代理,
公式近似但**实现细节并不一致**（例如周线 MACD 的分组键）。合一之后:
  * 同一因子只有一份代码, 改一处三处一起生效;
  * 回测、每日扫描与调参共用同一口径, 不会分叉。

类型约定:
  * 除 `adx` 外, 所有函数同时支持 **Series**（单标的）与 **DataFrame**（列=标的的全池面板）,
    因为回测/扫描是逐标的、部分分析是全池向量化 —— 同一定义两处结果一致。
  * 返回的布尔序列一律是真 bool（NaN 已按各策略原语义填 False）。

已知缺陷（**暂按原样保留**, 属于调参阶段的 A/B 项, 见 weekly_macd_bull）:
  周线 MACD 的分组键是字符串 "年-周", 字典序把 2016-1 排在 2016-10 之前 ->
  全部标的的周序都非时间序, 约 1/4 交易日的周线多头判断是错的。
  `order` 参数默认 WK_LEXICOGRAPHIC（复现历史结果）; 设为 WK_CHRONO 即修正。
"""
import numpy as np
import pandas as pd

# 周线分组顺序
WK_LEXICOGRAPHIC = "lexicographic"   # 历史口径（字典序, 已知非时间序）
WK_CHRONO = "chrono"                 # 修正口径（按周结束日时间排序）


# ---------------------------------------------------------------- 基础
def sma(s, n):
    """简单移动平均。"""
    return s.rolling(n).mean()


def wilder(s, n):
    """Wilder 平滑（等价于 alpha = 1/n 的 EWM）—— ADX/ATR 用。

    min_periods=n 是**必需**的: 没有它时 ewm 从第 1 根 bar 就有值, 于是 ADX 在
    warm-up 期「数值非空、但统计上无意义」, 使 `indicators_ready` 门槛对 ADX 的
    保护完全失效（实测 adx 的 leading NaN = 0 天, 见 strategies/lights/_gate_audit.py）。
    加上之后 ADX 约需 2n 根才成形, 门槛才真正挡得住新上市标的。
    因为 adjust=False 的递推不依赖 min_periods, **第 n 根之后的值逐位不变**。
    """
    return s.ewm(alpha=1.0 / n, adjust=False, min_periods=n).mean()


def ret(close, n):
    """n 日涨跌幅（close/close.shift(n) - 1）。"""
    return close.pct_change(n)


def trend_up(s, n):
    """s 相对 n 日前上行（斜率 > 0）。"""
    return s > s.shift(n)


def above(a, b):
    """逐元素 a > b（NaN 视为 False）。"""
    return (a > b).fillna(False).astype(bool)


# ---------------------------------------------------------------- 趋势
def adx(high, low, close, n=14):
    """ADX(n), Wilder 平滑。

    仅支持单标的 Series: 目前只有趋势灯用到 ADX, 且是逐标的计算;
    面板版没有需求, 故显式拒绝以免误用（tr 的构造依赖 Series 的 axis=1 语义）。
    """
    if isinstance(high, pd.DataFrame):
        raise TypeError("adx 仅支持单标的 Series")
    up, dn = high.diff(), -low.diff()
    pdm = up.where((up > dn) & (up > 0), 0.0)
    mdm = dn.where((dn > up) & (dn > 0), 0.0)
    tr = pd.concat([(high - low).abs(), (high - close.shift()).abs(),
                    (low - close.shift()).abs()], axis=1).max(axis=1)
    atr = wilder(tr.fillna(0.0), n)
    pdi = 100 * wilder(pdm.fillna(0.0), n) / atr
    mdi = 100 * wilder(mdm.fillna(0.0), n) / atr
    dx = 100 * (pdi - mdi).abs() / (pdi + mdi).replace(0, np.nan)
    # 不要 fillna(0): 那会让外层 Wilder 从第 1 根就以「假的 0」开始累积, 既让 ADX 在
    # warm-up 期数值非空但无意义, 也把内层 min_periods 的作用整个抵消掉。保留前导
    # NaN 后, ADX 约需 2n 根才成形（实测 leading NaN 0 -> 26 日）。
    return wilder(dx, n)


def macd(close, fast=12, slow=26, signal=9):
    """日线 MACD -> (DIF, DEA)。"""
    e_fast = close.ewm(span=fast, adjust=False).mean()
    e_slow = close.ewm(span=slow, adjust=False).mean()
    dif = e_fast - e_slow
    return dif, dif.ewm(span=signal, adjust=False).mean()


def weekly_macd_bull(close, fast=12, slow=26, signal=9, order=WK_LEXICOGRAPHIC):
    """周线 MACD 多头（周 DIF > 周 DEA），日线对齐, 只用已完成周, 无前视。

    以「每周最后交易日」为锚: 该锚日收盘 = 当周收盘, 算完周线指标后 reindex+ffill。
    故周内各日取到的是**上一已完成周**的值; 锚日本身用当周值（当周已收盘）。

    order:
      WK_LEXICOGRAPHIC(默认) —— 按 groupby 的字符串字典序喂给 EWMA。**这是已知缺陷**:
        周键 "2016-1"/"2016-10"/"2016-2" 的字典序不等于时间序, 全部标的都受影响,
        约 1/4 交易日的多头判断因此出错。默认值仅为复现历史回测结果。
      WK_CHRONO —— 先按周结束日时间排序再算, 是正确口径。
    """
    idx = pd.DatetimeIndex(close.index)
    iso = idx.isocalendar()
    key = pd.Index([f"{y}-{w}" for y, w in zip(iso.year, iso.week)])
    wk_close = close.groupby(key).last()
    wk_end = pd.Series(idx, index=key).groupby(level=0).max()
    if order == WK_CHRONO:
        o = np.argsort(pd.to_datetime(wk_end.to_numpy()).to_numpy())
        wk_close = wk_close.iloc[o]
        wk_end = wk_end.iloc[o]
    e_fast = wk_close.ewm(span=fast, adjust=False).mean()
    e_slow = wk_close.ewm(span=slow, adjust=False).mean()
    wdif = e_fast - e_slow
    bull = wdif > wdif.ewm(span=signal, adjust=False).mean()
    bull.index = pd.DatetimeIndex(wk_end.to_numpy())
    return bull.reindex(idx).astype("boolean").ffill().fillna(False).astype(bool)


def weekly_chrono_ok(close):
    """诊断用: 该标的的周序是否已是时间序（True=没有上述缺陷）。"""
    idx = pd.DatetimeIndex(close.index)
    iso = idx.isocalendar()
    key = pd.Index([f"{y}-{w}" for y, w in zip(iso.year, iso.week)])
    end = pd.to_datetime(pd.Series(idx, index=key).groupby(level=0).max().to_numpy())
    return bool((np.diff(end.to_numpy()) > np.timedelta64(0, "D")).all())


# ---------------------------------------------------------------- 量能 / 换手 / 主力
def vr(volume, n):
    """量比: 当日量 / 过去 n 日均量。"""
    return volume / volume.rolling(n).mean()


def cmf_strength(high, low, close, volume, win=3, scale=1.0):
    """主力强度代理: CMF 窗口净流估算 / 成交额 × 100 × scale。

    CMF = Σ((close-low)-(high-close))/(high-low) × volume / Σ volume, 收盘位置加权净流。
    与「主力净额/成交额×100」同号但幅度偏大, 用 mx 快照真实值 OLS 过原点标定 scale
    （当前取 rule.ACTIVE.cap_proxy_scale）。
    """
    rng = (high - low).replace(0.0, np.nan)
    mfv = (((close - low) - (high - close)) / rng * volume).fillna(0.0)
    return mfv.rolling(win).sum() / volume.rolling(win).sum() * 100.0 * scale


def turnover_pct(amount, win=60):
    """换手率分位代理: 当日成交额在过去 win 日的分位（0~1）。

    份额近似恒定的场内 ETF 上, 「成交额分位」与「换手率分位」等价。
    """
    return amount.rolling(win).rank(pct=True)


def divergence(close, win=5):
    """分化度代理: 日收益离散度 / 区间净涨幅（横截面分散度的时间序列类比）。

    ret5 == 0（区间零涨跌）时分母置 NaN, 避免 inf 被误判为「高分化」。
    """
    r1 = close.pct_change(1)
    r5 = close.pct_change(win)
    return r1.rolling(win).std() / r5.abs().replace(0.0, np.nan)


def amount(volume, close):
    """成交额（ETF 份额 × 价格 ≈ 成交额）。"""
    return volume * close
