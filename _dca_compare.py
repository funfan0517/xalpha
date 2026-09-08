"""每日定投 100 元：中证红利 vs 中证A500 对比回测"""
import numpy as np
import pandas as pd
from scipy.optimize import brentq

import xalpha as xa

DAILY = 100.0
NAMES = {"中证红利": "SH000922", "中证A500": "SH000510"}
# 实际可投标的。注意：xalpha 对 ETF 默认返回前复权价，已包含分红再投，且已扣除基金费率
ETF_NAMES = {"中证红利ETF(515080)": "SH515080", "中证A500ETF(159338)": "SZ159338"}
START = "2020-01-02"
TRADING_DAYS = 252


def load_prices():
    series = {}
    for name, code in NAMES.items():
        df = xa.get_daily(code, start="2019-12-01")[["date", "close"]].dropna()
        df["date"] = pd.to_datetime(df["date"])
        series[name] = df.set_index("date")["close"]
    px = pd.DataFrame(series).dropna().sort_index()
    return px[px.index >= START]


def load_etf():
    series = {}
    for name, code in ETF_NAMES.items():
        df = xa.get_daily(code, start="2020-01-01")[["date", "close"]].dropna()
        df["date"] = pd.to_datetime(df["date"])
        series[name] = df.set_index("date")["close"]
    return pd.DataFrame(series).dropna().sort_index()


def xirr(dates, amounts):
    """内部收益率：现金流 NPV=0 时的年化利率"""
    d0 = dates[0]
    t = [(d - d0).days / 365.0 for d in dates]
    npv = lambda r: sum(a / (1 + r) ** ti for a, ti in zip(amounts, t))
    try:
        return brentq(npv, -0.99, 10.0, maxiter=300)
    except ValueError:
        return float("nan")


def dca(prices):
    """每个交易日定投固定金额，按收盘价买入"""
    shares = (DAILY / prices).cumsum()
    value = shares * prices
    cost = pd.Series(DAILY * np.arange(1, len(prices) + 1), index=prices.index)
    ret = value / cost - 1.0
    flows = [-DAILY] * len(prices)
    flows[-1] += value.iloc[-1]
    return {
        "投入本金": cost.iloc[-1],
        "期末市值": value.iloc[-1],
        "绝对收益": value.iloc[-1] - cost.iloc[-1],
        "总收益率": ret.iloc[-1],
        "年化(XIRR)": xirr(list(prices.index), flows),
        "最大浮亏": ret.min(),
        "盈利天数占比": (ret > 0).mean(),
    }


def index_stats(prices):
    """指数自身的风险收益特征"""
    rets = prices.pct_change().dropna()
    years = (prices.index[-1] - prices.index[0]).days / 365.0
    total = prices.iloc[-1] / prices.iloc[0] - 1
    return {
        "区间涨幅": total,
        "年化涨幅": (1 + total) ** (1 / years) - 1,
        "年化波动率": rets.std() * np.sqrt(TRADING_DAYS),
        "最大回撤": (prices / prices.cummax() - 1).min(),
        "夏普比率": rets.mean() / rets.std() * np.sqrt(TRADING_DAYS),
    }


def show(title, rows):
    print(f"\n{'=' * 62}\n{title}\n{'=' * 62}")
    df = pd.DataFrame(rows)
    for col in df.columns:
        if pd.api.types.is_float_dtype(df[col]):
            df[col] = df[col].map(lambda v: f"{v:,.4f}")
    print(df.to_string(index=False))


def main():
    px = load_prices()
    print(f"数据区间: {px.index[0].date()} ~ {px.index[-1].date()}  交易日 {len(px)} 天")
    print(f"定投方式: 每个交易日买入 {DAILY:.0f} 元，按当日收盘价成交（不计手续费）")
    print(f"累计投入: {DAILY * len(px):,.0f} 元/只\n")

    segments = [
        ("全程对比 (2020-01 ~ 至今)", px),
        ("近 3 年", px[px.index >= px.index[-1] - pd.Timedelta(days=1095)]),
        ("近 1 年", px[px.index >= px.index[-1] - pd.Timedelta(days=365)]),
    ]

    for title, sub in segments:
        if len(sub) < 30:
            continue
        rows = {}
        for name in NAMES:
            rows[name] = dca(sub[name])
        show(f"{title}  |  {sub.index[0].date()} ~ {sub.index[-1].date()} ({len(sub)}天)",
             [{"标的": k, **v} for k, v in rows.items()])

    show("指数自身风险收益特征 (全程)",
         [{"标的": k, **index_stats(px[k])} for k in NAMES])

    etf_px = load_etf()
    if len(etf_px) > 30:
        show(f"实际可投 ETF 定投（前复权价=含分红再投，已扣基金费率）| "
             f"{etf_px.index[0].date()} ~ {etf_px.index[-1].date()} ({len(etf_px)}天)",
             [{"标的": k, **dca(etf_px[k])} for k in ETF_NAMES])

        common = etf_px.index.intersection(px.index)
        for ne, ni in zip(ETF_NAMES, NAMES):
            e, i = etf_px[ne][common], px[ni][common]
            gap = (e.iloc[-1] / e.iloc[0]) - (i.iloc[-1] / i.iloc[0])
            print(f"\n{ni} 分红贡献 ({common[0].date()} ~ {common[-1].date()}): "
                  f"ETF含分红 {e.iloc[-1] / e.iloc[0] - 1:+.2%} vs "
                  f"价格指数 {i.iloc[-1] / i.iloc[0] - 1:+.2%} => 差 {gap:+.2%}")
            show(f"同期定投对照：{ni}",
                 [{"标的": ni + "(价格指数)", **dca(i)},
                  {"标的": ne + "(含分红)", **dca(e)}])

    corr = px.pct_change().dropna().corr()
    print(f"\n日收益率相关系数: {corr.iloc[0, 1]:.4f}")

    # 逐年收益
    rows = []
    for year, grp in px.groupby(px.index.year):
        row = {"年份": year}
        for name in NAMES:
            p = grp[name]
            row[name] = dca(p)["总收益率"]
        rows.append(row)
    show("分年度定投收益率", rows)

    # 净值曲线抽样
    print("\n定投账户收益率曲线（每季度末）:")
    for name in NAMES:
        ret = (DAILY / px[name]).cumsum() * px[name]
        ret = ret / (DAILY * np.arange(1, len(px) + 1)) - 1
        s = ret.resample("QE").last().dropna()
        line = "  ".join(f"{d.strftime('%y%m')}:{v:+.0%}" for d, v in s.items())
        print(f"\n{name}\n  {line}")


if __name__ == "__main__":
    main()
