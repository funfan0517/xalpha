# -*- coding: utf-8 -*-
"""TRIN（阿姆氏指标 / Arms Index）市场广度择时 · 规则化定义（**唯一参数权威源**）。

问题定义
--------
TRIN 不是个股买卖按钮，而是**全市场情绪温度计**：

    TRIN = (上涨家数 ÷ 下跌家数) ÷ (上涨成交额 ÷ 下跌成交额)

- TRIN ≈ 1：多空平衡（涨跌家数与成交额同比例分布）；
- TRIN > 1：下跌端相对放量 —— 按经典读法是空头占优，但**连续多日高位**更像
  恐慌抛售的尾声（放量杀跌、情绪出清），本策略按视频口径把它当**逆向买点**；
- TRIN < 1：上涨端相对放量，偏强势（不作为买点）。

策略（视频口径，参数集中在下方常量）
------------------------------------
1. 不看单日，只看 **TRIN 的 5 日移动平均**；
2. 连续 N 日 trin_ma5 > 1.4 视为「恐慌确认」，**次日开盘买入**；
3. **持有 HOLD_DAYS(20) 个交易日后收盘卖出**；
4. 不触发 -> 空仓（0/1 状态机，不加仓不减仓）；
5. 风控：收盘较建仓价跌破 -STOP(8%) 当日收盘止损（视频没给具体值，用 -8%）。

选股（可选池子口径，视频原话：错杀、放量温和、基本面/逻辑未坏的票）
--------------------------------------------------------------------
可编码的部分做成硬过滤 + 排序，**「基本面/逻辑未坏」只能用价格结构代理**：

  - 错杀：20 日跌幅在池内处于后 SELECT_Q(20%) 分位；
  - 放量温和：当日量比 vr = vol / vol_ma20 ∈ [VOL_LO(0.6), VOL_HI(2.0)]
    （既不是无量阴跌，也不是恐慌天量）；
  - 未破位：收盘 ≥ MA250 × BROKEN(0.85)（年线附近仍算「逻辑未坏」）；
  - 流动性：20 日均成交额 ≥ AMT_MIN(0.3 亿)；
  取满足条件的标的按 ret20 从低到高取 PICK_K(3) 只等权。

执行口径与两处不得已的偏差
--------------------------
- 单一标的（默认 510300 沪深300ETF）：**信号次日开盘买入**、第 HOLD_DAYS 根 K 线收盘卖出，
  与视频一致（开盘价取自东财前复权日线）。
- 池子口径：十年库 `data/_long_klines.json` **只有收盘价**，无法按次日开盘成交，
  退化为「信号次日收盘买入」，报告里与单标的行并列时必须标注口径差异。
- 单边费率 FEE(0.0003)，未计滑点；无风险利率按 0。

数据来源：`data/_breadth_daily.json`（全市场涨跌家数 / 涨跌成交额，由 `data/_refresh_breadth.py` 维护）。
参数改动只改本文件。
"""
import numpy as np
import pandas as pd

# ---------------------------------------------------------------- 信号参数
TRIN_WIN = 5             # TRIN 移动平均窗口（视频口径：只看 5 日均，不看单日）
TRIN_TH = 1.4           # 恐慌阈值（视频参数）
TRIN_CONFIRM = 2        # 需连续 N 日 trin_ma5 > 阈值（视频未写死，1~3 做参数）
FIELD = "amt"           # TRIN 分母用成交额（视频编码口径）还是成交量：amt / vol
EDGE_TRIGGER = True     # True=条件刚成立那天买（一次），False=仍满足时每天都算买点

# ---------------------------------------------------------------- 持仓 / 风控
HOLD_DAYS = 20          # 持有交易日数（含建仓日；第 HOLD_DAYS 根 K 线收盘卖出）
STOP = 0.08             # 收盘较建仓价回撤止损（0 = 关闭）
SIZE = 1.0              # 单次建仓占总资金比例（<1 = 视频说的「不满仓」，其余留现金）
FEE = 0.0003            # 单边交易成本

# ---------------------------------------------------------------- 选股（池子口径）
PICK_K = 3              # 等权持有只数
SELECT_Q = 0.20         # 「错杀」分位：池内 20 日收益最低的 20%
VOL_LO, VOL_HI = 0.6, 2.0   # 「放量温和」的量比区间
BROKEN = 0.85           # 未破位：收盘 / MA250 下限
AMT_MIN = 3e7           # 流动性下限：20 日均成交额（元）

# ---------------------------------------------------------------- 样本
VEHICLE = "510300"      # 主口径标的：沪深300ETF（全市场信号的市场 beta 载体）
START = "2015-01-01"    # 广度库起点
WARMUP = 250            # 池子口径 MA250 预热
LABEL = "trin5v20"      # 本参数组标签（写进报告）

PARAMS = dict(
    trin_win=TRIN_WIN, trin_th=TRIN_TH, trin_confirm=TRIN_CONFIRM, field=FIELD,
    edge_trigger=EDGE_TRIGGER, hold_days=HOLD_DAYS, stop=STOP, size=SIZE, fee=FEE,
    pick_k=PICK_K, select_q=SELECT_Q, vol_lo=VOL_LO, vol_hi=VOL_HI,
    broken=BROKEN, amt_min=AMT_MIN, vehicle=VEHICLE, label=LABEL,
)


# ---------------------------------------------------------------- TRIN
def trin_series(breadth, field=FIELD):
    """广度表 -> TRIN 序列（下跌家数/成交额为 0 时置 NaN，不参与平均）。"""
    adv = breadth["adv"].astype(float)
    dec = breadth["dec"].astype(float)
    av = breadth["adv_" + field].astype(float)
    dv = breadth["dec_" + field].astype(float)
    ok = (dec > 0) & (dv > 0)
    out = pd.Series(np.nan, index=breadth.index, dtype=float)
    out[ok] = (adv[ok] / dec[ok]) / (av[ok] / dv[ok])
    return out


def streak(mask):
    """布尔序列 -> 连续为 True 的天数（第 1 天记 1）。"""
    out = np.zeros(len(mask), dtype=int)
    c = 0
    for i, v in enumerate(np.asarray(mask, bool)):
        c = c + 1 if v else 0
        out[i] = c
    return out


def compute_signals(breadth, win=TRIN_WIN, th=TRIN_TH, confirm=TRIN_CONFIRM,
                    field=FIELD, edge=EDGE_TRIGGER):
    """广度表 -> 信号表（index 与广度表一致）。

    返回 DataFrame: trin / trin_ma / hot / streak / buy
      buy[t]=True 表示第 t 日收盘确认，t+1 个交易日开盘（单标的）或收盘（池子）执行。
    """
    f = pd.DataFrame(index=breadth.index)
    f["trin"] = trin_series(breadth, field)
    f["trin_ma"] = f["trin"].rolling(int(win)).mean()
    f["hot"] = (f["trin_ma"] > float(th)).fillna(False)
    f["streak"] = streak(f["hot"].to_numpy(bool))
    hit = f["streak"] >= int(confirm)
    if edge:
        prev = f["streak"].shift(1, fill_value=0) >= int(confirm)
        f["buy"] = hit & (~prev)
    else:
        f["buy"] = hit
    return f


# ---------------------------------------------------------------- 引擎：单一标的
def run_single(open_, close, buy, hold=HOLD_DAYS, stop=STOP, fee=FEE, size=SIZE, begin=0):
    """0/1 状态机：信号次日**开盘**买入，第 hold 根 K 线收盘卖出，收盘跌破止损位当日止损。

    size < 1 时只投入 size 比例资金（视频的「不满仓」），其余留现金。
    返回 (nav, pos_log, trades)；nav 以 begin 日收盘为 1。
    """
    o = np.asarray(open_, float)
    c = np.asarray(close, float)
    b = np.asarray(buy, bool)
    n = len(c)
    cash, shares = 1.0, 0.0
    nav = np.ones(n)
    pos = np.zeros(n, dtype=bool)
    trades = []
    entry_i = entry_px = None

    for i in range(1, n):
        if shares == 0.0 and b[i - 1] and np.isfinite(o[i]) and o[i] > 0:
            entry_px = o[i] * (1.0 + fee)
            spend = cash * float(size)
            shares = spend / entry_px
            cash -= spend
            entry_i = i
        elif shares > 0.0:
            ret = c[i] / entry_px - 1.0
            reason = None
            if stop and ret <= -stop:
                reason = "止损"
            elif i - entry_i >= hold - 1:
                reason = "到期"
            if reason:
                px = c[i] * (1.0 - fee)
                cash += shares * px
                trades.append(dict(entry_i=entry_i, exit_i=i, bars=int(i - entry_i + 1),
                                   ret=float(px / entry_px - 1.0), reason=reason))
                shares, entry_i, entry_px = 0.0, None, None
        pos[i] = shares > 0.0
        nav[i] = cash + shares * c[i]

    if shares > 0.0:                      # 期末仍持仓：按最新收盘估值
        trades.append(dict(entry_i=entry_i, exit_i=n - 1, bars=int(n - entry_i),
                           ret=float(c[-1] / entry_px - 1.0), reason="期末持仓"))
    if begin:
        nav = nav / nav[begin]
    return nav, pos, trades


# ---------------------------------------------------------------- 引擎：池子等权
def pool_factors(close, volume):
    """池子因子表（全部因果）：close / ret20 / vr(量比) / ma250 / amt20。"""
    c = close.astype(float)
    v = volume.astype(float)
    f = {"_close": c}
    f["ret20"] = c / c.shift(20) - 1.0
    f["vr"] = v / v.rolling(20).mean().replace(0.0, np.nan)
    f["ma250"] = c.rolling(250).mean()
    f["amt20"] = (c * v).rolling(20).mean()
    return f


def pick_codes(fac, i, k=PICK_K, q=SELECT_Q, vol_lo=VOL_LO, vol_hi=VOL_HI,
               broken=BROKEN, amt_min=AMT_MIN):
    """第 i 日（信号日收盘）从池子里挑「错杀 + 放量温和 + 未破位」的 k 只。

    只用 i 日及之前的数据；返回 (codes, snapshot) —— snapshot 供报告展示筛选过程。
    """
    r20 = fac["ret20"].iloc[i].dropna()
    if r20.empty:
        return [], dict(reason="ret20 未就绪")
    vr = fac["vr"].iloc[i]
    ma = fac["ma250"].iloc[i]
    amt = fac["amt20"].iloc[i]
    px_all = fac["_close"].iloc[i]
    thr = r20.quantile(q)
    ok = []
    for code in r20.index:
        if not np.isfinite(vr.get(code, np.nan)) or not np.isfinite(ma.get(code, np.nan)):
            continue
        if not (vol_lo <= float(vr[code]) <= vol_hi):
            continue
        if float(amt.get(code, 0.0)) < amt_min:
            continue
        px = float(px_all.get(code, np.nan))
        if not np.isfinite(px) or px < float(ma[code]) * broken:
            continue
        if float(r20[code]) > thr:
            continue
        ok.append(code)
    ok.sort(key=lambda x: float(r20[x]))
    return ok[:int(k)], dict(threshold=round(float(thr), 4), eligible=len(ok))


def run_pool(close, buy, fac, hold=HOLD_DAYS, stop=STOP, fee=FEE, k=PICK_K,
             begin=WARMUP, **pick_kw):
    """池子等权 0/1 状态机：信号次日**收盘**买入 k 只等权，到期/止损逐腿卖出。

    close: DataFrame(index=日期, columns=code)。返回 (nav, pos_log, trades, picks)。
    """
    idx = close.index
    c = close.to_numpy(float)
    cols = list(close.columns)
    pos_of = {code: j for j, code in enumerate(cols)}
    b = np.asarray(buy, bool)
    n = len(idx)
    cash, legs, nav = 1.0, {}, np.ones(n)
    pos = np.zeros(n, dtype=bool)
    trades, picks = [], []

    for i in range(n):
        # 1) 先处理卖出（收盘价成交）
        for code in list(legs):
            j = pos_of[code]
            px = c[i, j]
            if not np.isfinite(px):
                continue
            st = legs[code]
            ret = px / st["entry_px"] - 1.0
            reason = None
            if stop and ret <= -stop:
                reason = "止损"
            elif i - st["entry_i"] >= hold - 1:
                reason = "到期"
            if reason:
                cash += st["shares"] * px * (1.0 - fee)
                trades.append(dict(code=code, entry_i=st["entry_i"], exit_i=i,
                                   bars=int(i - st["entry_i"] + 1),
                                   ret=float(px * (1.0 - fee) / st["entry_px"] - 1.0),
                                   reason=reason))
                del legs[code]
        # 2) 再处理买入（信号日 i-1，i 日收盘成交）
        if i >= begin and not legs and i > 0 and b[i - 1]:
            codes, snap = pick_codes(fac, i - 1, k=k, **pick_kw)
            picks.append(dict(date=str(idx[i - 1].date()), codes=codes, **snap))
            live = [x for x in codes if np.isfinite(c[i, pos_of[x]])]
            if live:
                w = cash / len(live)
                for code in live:
                    px = c[i, pos_of[code]]
                    entry_px = px * (1.0 + fee)
                    legs[code] = dict(entry_i=i, entry_px=entry_px, shares=w / entry_px)
                    cash -= w
        pos[i] = bool(legs)
        nav[i] = cash + sum(st["shares"] * (c[i, pos_of[code]] if np.isfinite(c[i, pos_of[code]])
                                            else st["entry_px"]) for code, st in legs.items())

    if begin:
        nav = nav / nav[begin]
    return nav, pos, trades, picks
