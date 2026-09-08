"""四灯共振(量价代理版) 近5年回测：逐场内标的状态机择时 vs 买入持有。

信号口径:
  趋势灯  真实 MA5/10/20 + MA20斜率 + 收盘>MA20 + ADX14
  主力灯  量价代理: 放量上涨=2 / 温和上涨=1 / 缩量或放量下跌=0
  持续力灯 真实 MACD(DIF/DEA) + 近3日涨幅 + 周线MACD(无前视, 截至最近完整周)
  热度灯  量价代理: 5日涨幅 5-20% 且量能放大=2 / 温和正动量=1 / 下跌或过热=0
规则(同方法论决策表):
  空仓: 总分>=6 且 趋势>=1 且 主力>=1 且 近5日<=25% -> 次日开盘买入
  持仓: 总分<=3 或 (趋势==0 且 主力==0) -> 次日开盘卖出; 否则持有
成本: 每次交易 0.03%(ETF佣金), 免印花税
用法: python _backtest.py [code1,code2,...]   输出每标的一行 JSON
"""
import io
import json
import os
import sys

import numpy as np
import pandas as pd

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8")

_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

import xalpha as xa
from pipeline import bt_stats

START = "2020-06-01"  # 提前给 warm-up，样本窗口 2021-09 ~ 至今
FEE = 0.0003
INV = 252

# 与 _inner_4d.py 一致: 场外idx -> (场内code, 主题, 场外名)
MAPPING = {
    1: ("563360", "中证A500", "汇添富中证A500指数增强C"),
    2: ("588000", "科创50", "易方达上证科创板50ETF联接C"),
    3: ("512800", "银行", "天弘中证银行ETF联接C"),
    4: ("512200", "房地产", "南方中证房地产ETF联接E"),
    5: ("513100", "纳指100", "广发纳斯达克100ETF联接A"),
    6: ("513500", "标普500", "摩根标普500指数(QDII)人民币A"),
    10: ("513030", "德国DAX", "华安德国(DAX)ETF联接C"),
    11: ("513880", "日经225", "华安日经225ETF联接C"),
    12: ("513180", "恒生科技", "广发恒生科技ETF联接C"),
    13: ("501025", "港股银行", "鹏华港股通香港银行(LOF)C"),
    17: ("512480", "半导体", "招商中证半导体产业ETF联接C"),
    18: ("515230", "软件/信创", "嘉实中证软件服务ETF联接C"),
    19: ("515880", "通信", "天弘中证全指通信设备指数C"),
    20: ("159819", "人工智能", "天弘中证人工智能主题ETF联接C"),
    21: ("159732", "消费电子", "华夏国证消费电子ETF联接C"),
    22: ("562500", "机器人", "招商中证机器人ETF联接C"),
    23: ("512660", "军工", "广发中证军工ETF联接C"),
    24: ("159698", "粮食", "博时国证粮食产业ETF联接C"),
    25: ("159869", "动漫游戏", "华夏中证动漫游戏ETF联接C"),
    26: ("512980", "传媒", "广发中证传媒ETF联接C"),
    27: ("515220", "煤炭", "国泰中证煤炭ETF联接C"),
    28: ("512000", "券商", "华宝中证全指证券ETF联接C"),
    29: ("161725", "白酒", "招商中证白酒指数C"),
    30: ("159928", "主要消费", "汇添富中证主要消费ETF联接C"),
    31: ("516160", "新能源", "南方中证新能源ETF联接C"),
    32: ("515790", "光伏", "天弘中证光伏产业指数C"),
    33: ("159326", "电网设备", "华夏中证电网设备ETF联接C"),
    34: ("159870", "化工", "天弘中证细分化工ETF联接C"),
    35: ("512400", "有色金属", "南方中证申万有色金属ETF联接C"),
    36: ("512010", "医药", "易方达沪深300医药ETF联接C"),
    37: ("159992", "创新药", "广发创新药ETF联接C"),
    38: ("515080", "红利(515080代)", "场外红利008163/007760代替"),
    40: ("159201", "自由现金流", "华夏国证自由现金流ETF联接C"),
    41: ("161226", "白银", "国投瑞银白银期货(LOF)C"),
    42: ("518880", "黄金", "华安黄金ETF联接A"),
}
# 38/39 合并同标的，此处 39 省略
DEDUP = {}
for _i, (_c, _t, _n) in MAPPING.items():
    DEDUP[_c] = (_i, _t, _n)


def sh(code):
    return ("SH" if code.startswith(("5", "6", "9")) else "SZ") + code


def wilder(series, n):
    return series.ewm(alpha=1.0 / n, adjust=False).mean()


def build_signals(df):
    c = df["close"].astype(float)
    o = df["open"].astype(float)
    h = df["high"].astype(float)
    l = df["low"].astype(float)
    v = df["volume"].astype(float)
    n = len(df)

    ma5, ma10, ma20 = c.rolling(5).mean(), c.rolling(10).mean(), c.rolling(20).mean()

    # ADX(14)
    up = h.diff()
    dn = -l.diff()
    pdm = np.where((up > dn) & (up > 0), up, 0.0)
    mdm = np.where((dn > up) & (dn > 0), dn, 0.0)
    tr = pd.concat([(h - l).abs(), (h - c.shift()).abs(), (l - c.shift()).abs()], axis=1).max(axis=1)
    atr = wilder(tr.fillna(0), 14)
    pdi = 100 * wilder(pd.Series(pdm, index=df.index).fillna(0), 14) / atr
    mdi = 100 * wilder(pd.Series(mdm, index=df.index).fillna(0), 14) / atr
    dx = 100 * (pdi - mdi).abs() / (pdi + mdi).replace(0, np.nan)
    adx = wilder(dx.fillna(0), 14)

    tl = np.zeros(n)
    cond2 = (ma5 > ma10) & (ma10 > ma20) & (ma20 > ma20.shift()) & (c > ma20) & (adx > 25)
    cond1 = (ma5 > ma10) & (c > ma20)
    tl[cond2.fillna(False).to_numpy()] = 2
    tl[(cond1 & ~cond2).fillna(False).to_numpy()] = 1

    # 主力灯(量价代理)
    vma5 = v.rolling(5).mean()
    vr = v / vma5
    yang = c > o
    cl = np.zeros(n)
    cl[(yang & (vr >= 1.5)).fillna(False).to_numpy()] = 2
    cl[((yang & (vr < 1.5) & (vr >= 0.7)) | ((vr >= 1.5) & ~yang)).fillna(False).to_numpy()] = 1
    cl[((vr < 0.7) | (~yang & (vr < 1.5))).fillna(False).to_numpy()] = 0

    # MACD(12,26,9)
    e12 = c.ewm(span=12, adjust=False).mean()
    e26 = c.ewm(span=26, adjust=False).mean()
    dif = e12 - e26
    dea = dif.ewm(span=9, adjust=False).mean()
    ret3 = c.pct_change(3)

    # 周线MACD(仅取截至最近完整周, 避免前视)
    wk = df["date"].dt.isocalendar().year.astype(str) + "-" + df["date"].dt.isocalendar().week.astype(str)
    wk_end = df.groupby(wk)["date"].max()
    wk_close = df.groupby(wk)["close"].last()
    we12 = wk_close.ewm(span=12, adjust=False).mean()
    we26 = wk_close.ewm(span=26, adjust=False).mean()
    wdif = we12 - we26
    wdea = wdif.ewm(span=9, adjust=False).mean()
    # index 用每周最后一个交易日的日期，便于与日线日期对齐；ffill 保证只用已完成周
    wk_bull = pd.Series((wdif > wdea).to_numpy(), index=wk_end.to_numpy())
    wk_bull_s = wk_bull.reindex(df["date"]).ffill()

    sl = np.zeros(n)
    dgt = ((dif > dea) & (dif > 0)).to_numpy()
    ret3b = ret3.to_numpy() > 0.03
    wk_b = wk_bull_s.fillna(False).to_numpy()
    cond2s = dgt & ret3b & wk_b
    sl[cond2s] = 2
    sl[dgt & ~cond2s] = 1

    # 热度灯(量价代理)
    w5 = c.pct_change(5)
    hl = np.zeros(n)
    hot2 = (w5 >= 0.05) & (w5 <= 0.20) & (vr >= 1.3)
    hot1 = ((w5 >= 0.05) & (w5 <= 0.20) & (vr < 1.3)) | ((w5 > 0) & (w5 < 0.05))
    hl[hot2.fillna(False).to_numpy()] = 2
    hl[hot1.fillna(False).to_numpy()] = 1

    total = tl + cl + sl + hl
    allow = adx.notna().to_numpy() & dif.notna().to_numpy() & dea.notna().to_numpy() & ma20.notna().to_numpy()
    buy = np.asarray((total >= 6) & (tl >= 1) & (cl >= 1) & (w5.to_numpy() <= 0.25), dtype=bool)
    sell = np.asarray((total <= 3) | ((tl == 0) & (cl == 0)), dtype=bool)
    return dict(open=o.to_numpy(), close=c.to_numpy(), allow=allow, buy=buy,
                sell=sell, total=total)


def run_backtest(df):
    sig = build_signals(df)
    o, c = sig["open"], sig["close"]
    n = len(df)
    cash, shares, pos = 1.0, 0.0, False
    nav = np.ones(n)
    pos_log = np.zeros(n, dtype=bool)
    open_px = None
    trade_log = []  # 每笔 = 一次持仓周期(买入开盘价 → 卖出开盘价/期末收盘价估值)
    for i in range(n):
        if i >= 1:
            if pos and sig["sell"][i - 1] and sig["allow"][i - 1]:
                cash = shares * o[i] * (1 - FEE)
                shares, pos = 0.0, False
                if open_px and open_px > 0:
                    trade_log.append(dict(e1=str(df["date"].iloc[i].date()),
                                          ret=o[i] / open_px * (1 - FEE) ** 2 - 1.0))
                open_px = None
            elif (not pos) and sig["buy"][i - 1] and sig["allow"][i - 1]:
                shares = cash * (1 - FEE) / o[i]
                cash, pos = 0.0, True
                open_px = o[i]
        pos_log[i] = pos
        nav[i] = cash + shares * c[i]
    if open_px and open_px > 0:  # 期末仍持仓: 按最新收盘估值计入(未扣卖出费)
        trade_log.append(dict(e1=str(df["date"].iloc[-1].date()),
                              ret=c[-1] / open_px - 1.0))

    base_start = int(sig["allow"].argmax()) if sig["allow"].any() else n - 1
    if base_start >= n - 1 or c[base_start] <= 0:
        return None
    bh = c / c[base_start]
    nav = nav / nav[base_start]  # 归一到策略首次可交易日起

    d0, d1 = df["date"].iloc[base_start], df["date"].iloc[-1]
    years = max((d1 - d0).days / 365.0, 1e-9)

    def stat(series):
        ret = series[-1] / series[0] - 1
        ann = (1 + ret) ** (1 / years) - 1 if ret > -1 else -1.0
        dd = (series / np.maximum.accumulate(series) - 1).min()
        return ret, ann, dd

    base_ret, base_ann, base_mdd = stat(bh[base_start:])
    st_ret, st_ann, st_mdd = stat(nav[base_start:])
    ts = bt_stats.trade_stats(trade_log)
    return dict(
        days=len(df) - base_start, years=round(years, 2),
        base_ret=round(base_ret, 4), base_ann=round(base_ann, 4), base_mdd=round(base_mdd, 4),
        st_ret=round(st_ret, 4), st_ann=round(st_ann, 4), st_mdd=round(st_mdd, 4),
        trades=ts["n"], t_stats=ts, trade_log=trade_log,
        pos_ratio=round(float(pos_log[base_start:].mean()), 3),
    )


def main():
    want = set(sys.argv[1].split(",")) if len(sys.argv) > 1 and sys.argv[1] else None
    for code, (idx, theme, off) in DEDUP.items():
        if want and code not in want:
            continue
        try:
            df = xa.get_daily(sh(code), start=START)
            df = df.dropna(subset=["close", "open", "high", "low", "volume"])
            df["date"] = pd.to_datetime(df["date"])
            df = df.sort_values("date").reset_index(drop=True)
            # 样本窗口：近5年(含少量warm之前的缓冲由2020-06起提供)
            df = df[df["date"] >= "2021-08-01"].reset_index(drop=True)
            if len(df) < 120:
                print(json.dumps({"code": code, "idx": idx, "theme": theme, "ok": False,
                                  "err": "样本不足(上市晚)"}, ensure_ascii=False), flush=True)
                continue
        except Exception as e:
            print(json.dumps({"code": code, "idx": idx, "theme": theme, "ok": False,
                              "err": f"{type(e).__name__}: {e}"}, ensure_ascii=False), flush=True)
            continue
        r = run_backtest(df)
        if r is None:
            print(json.dumps({"code": code, "idx": idx, "theme": theme, "ok": False,
                              "err": "样本不足"}, ensure_ascii=False), flush=True)
            continue
        r.update({"ok": True, "code": code, "idx": idx, "theme": theme, "off": off,
                  "end": str(df["date"].iloc[-1].date())})
        print(json.dumps(r, ensure_ascii=False), flush=True)


if __name__ == "__main__":
    main()
