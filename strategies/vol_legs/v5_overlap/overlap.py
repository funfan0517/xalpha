# -*- coding: utf-8 -*-
"""波动资产 · V5 变种：两线重合买点 —— 沪深300全收益 × 二级债基指数。

## 1. 规则（用户原话）
把**沪深300全收益指数**（红线）与**二级债基指数**（蓝线）画在同一张图上、归一到同一
起点；两线**重合**（红线跌到蓝线附近或跌破）时，股票相对债券的累计超额全部回吐，
是**止跌点 / 买点**。买点之后**持有一年**（默认 250 个交易日）卖出。

```
锚点 t0（默认 2005-01-04）: 两条线都归一到 1000
R(t) = HS300全收益(t)/HS300全收益(t0) ÷ 二级债基指数(t)/BD(t0)      → R(t0) = 1
买点: R ≤ 1 + level_tol（默认 2%，即「跌破或几乎贴合」）
卖点: 买入后满 hold_days（默认 250 个交易日）
```

## 2. 两条线从哪来
- **红线**：中证官网 `H00300`（沪深300**全收益**，含分红再投）日频收盘
  → 共享缓存 `data/_bt_caches/csi_H00300.csv`（v4 变种也在用）
- **蓝线**：用户说的「二级债基指数」在境内没有公开免费接口（万得 `885007.WI` 拿不到），
  所以本变种**自建**一条：取 8 只 2003~2010 年成立的老债券基金（二级 4 只 + 一级 4 只）
  的**累计净值**（= 分红再投后的真实持有收益），按**日收益等权**复合后累积。
  样本 2005-01 起有 6 只、2010-08 起 8 只全到 → 与红线可比的窗口是 **2005-01 至今**。
  缓存 `data/_v5_bond_funds.csv`（8 只累计净值）+ `data/_v5_bond_index.csv`（复合指数）。

  > 为什么不用单只基金：单只选谁都是事后挑选；等权复合 8 只 ≈「当年债基的平均表现」，
  > 这也正是「指数」二字的含义。单只 `202101` 南方宝元留作敏感性对照。

## 3. 「两线重合」在真实数据下只发生一次 —— 本变种最重要的事实
自建债基指数年化约 **5.1%/年**，沪深300全收益 2005 年以来约 **8.7%/年**。所以 R 的低点
**逐级抬升**（每轮周期的底都比上一轮高）：2005-07 **0.81** / 2008-11 **1.10** /
2014-03 **1.27** / 2019-01 **1.55** / 2024-02 **1.65**。

严格按默认 `R ≤ 1.02` 定义，只有 2005-01~2006-04 一段；2008-11 的底差 10% 就抓不到，
2014/2019/2024 分别差 27%/55%/65%。参考图里的蓝线比真实债基指数**涨得快得多**
（20 年约 5 倍 ≈ 8.4%/年），所以那张图能在 2005/2008/2014/2018/2024 五次画成「贴到蓝线」。
对照图见 `backtest/_v5_overlap_troughs.png`（灰虚线圈 = 5 个周期低点，白实线圈 = 实际买点）。

因此本变种给**两套信号口径**（同一张图、同一份数据，只换阈值）：

| mode | 买点定义 | 触发段数（2005-2026） | 用途 |
|---|---|---|---|
| `level`（默认，忠于原话） | `R ≤ 1 + level_tol` | **1 段**（2005-01~2006-04） | 回答「按字面执行会怎样」 |
| `z`（对照，**不推荐**） | `z = (lnR − MA250(lnR)) / SD250 ≤ −k`（默认 k=1） | **17 段** | 回答「每轮周期的相对低位在哪」 |

`z` 口径的几何含义和「贴到蓝线」一致：红线相对**自己的年线**被压到 −1σ。但回测证明它
**更差**（年化 1.78%、回撤 −59.5%、夏普 0.02）—— 2008-03 就触发满仓，完整吃了 2008 年那段。
「跌得多」不等于「跌到底」，所以主口径坚持绝对水位。

## 4. 记账口径
- 单腿全仓：买点成交后**满仓沪深300全收益**，卖点清仓回货币基金（`rule.CASH_ANNUAL` 1.40%）。
- 成交：`lag = 1`，t 日收盘算出信号 → **t+1 收盘成交**（无前视）。
- 成本：申购 0.10% / 赎回 0.50%（场外 A 类量级），买卖各扣一次。
- `allow_rebuy = True`：卖出日若仍在触发区间内 → 立刻再买（「贴着就滚动持有一年」）。

## 5. 复用的主策略口径
绩效 `engine.metrics`（rf = 货基 1.40%）、现金利率 `rule.CASH_ANNUAL`、费率量级
`rule.FEE_BUY/FEE_SELL` —— 与 vol_legs 主策略、v1_ma20、v4_erp_band 同一套算法。
"""
import math
import os
import sys
import time
from datetime import date

import numpy as np
import pandas as pd

try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:  # noqa: BLE001
    pass

_HERE = os.path.dirname(os.path.abspath(__file__))
_VL = os.path.dirname(_HERE)                              # strategies/vol_legs
_ROOT = os.path.dirname(os.path.dirname(_VL))             # 仓库根
for _p in (_HERE, _VL):
    if _p not in sys.path:
        sys.path.insert(0, _p)

import engine  # noqa: E402  —— 绩效口径复用主策略
import rule    # noqa: E402

LABEL = "V5 两线重合买点"

# ---------------------------------------------------------------- 数据文件
_BT = os.path.join(_ROOT, "data", "_bt_caches")
_HS300_TR = os.path.join(_BT, "csi_H00300.csv")                  # 共享：沪深300全收益
_BOND_RAW = os.path.join(_HERE, "data", "_v5_bond_funds.csv")    # 本变种：8 只债基累计净值
_BOND_IDX = os.path.join(_HERE, "data", "_v5_bond_index.csv")    # 本变种：复合债基指数

_CSI_URL = "https://www.csindex.com.cn/csindex-home/perf/index-perf"
_CSI_START = "20050101"
CACHE_DAYS = 3
LEGEND_TR = "沪深300全收益（H00300，含分红再投）"
LEGEND_BD = "二级债基指数（自建：8 只老债基等权）"

# 自建「二级债基指数」的成员：2003~2010 年成立、至今还活着的老债基。
# 二级 = 可投 20% 股票；一级 = 只打新股/转债。四四分配是刻意的：早年只有一级债基有净值，
# 后期二级占比升高，混着才不偏。用**累计净值**（分红再投），不是单位净值。
FUNDS = {
    "202101": ("南方宝元债券A", "二级", "2002-09-20"),
    "240003": ("华宝宝康债券A", "二级", "2003-07-15"),
    "217003": ("招商安泰债券A", "一级", "2003-04-28"),
    "090002": ("大成债券AB", "一级", "2003-06-12"),
    "020002": ("国泰金龙债券A", "一级", "2003-12-05"),
    "100018": ("富国天利增长债券", "一级", "2003-12-02"),
    "110017": ("易方达增强回报债券A", "二级", "2008-03-19"),
    "485111": ("工银双利债券A", "二级", "2010-08-16"),
}

SOURCE = {}
_WARN = []

# ---------------------------------------------------------------- 1. 参数（唯一权威源）
CFG = {
    # 归一化锚点（两线共同起点）
    "anchor": "2005-01-04",
    # 信号口径：level = 字面「跌破/贴合」；z = 相对自己的年线的 z 值
    "mode": "level",
    "level_tol": 0.02,      # R ≤ 1 + tol
    "z_win": 250,           # z 口径的滚动窗口（交易日）
    "z_k": 1.0,             # z ≤ -k 触发
    # 持仓
    "hold_days": 250,       # 持有一年（交易日）；500 = 两年；None = 一直持有
    "allow_rebuy": True,    # 卖出日仍在触发区间 → 立刻续买
    "lag": 1,               # t 日收盘算信号 → t+lag 收盘成交
    # 成本
    "fee_buy": rule.FEE_BUY,
    "fee_sell": rule.FEE_SELL,
    # 数据
    "ffill_limit": 5,       # 债基指数前向填充上限（交易日）
    "bond_baseline": "composite",   # composite（8 只等权）/ "202101"（单只对照）
    # 空仓时拿什么：cash = 货基 1.40%；bond = 二级债基指数（卖了就回债）
    "cash_mode": "cash",
}


def cfg(key, default=None):
    return CFG.get(key, default)


def _warn(msg):
    _WARN.append(msg)
    print(f"[v5:warn] {msg}", file=sys.stderr)


# ---------------------------------------------------------------- 2. 采集
def _cache_fresh(path, days=CACHE_DAYS):
    if not os.path.exists(path):
        return False
    try:
        head = pd.read_csv(path, usecols=["date"], parse_dates=["date"])
    except (OSError, ValueError):
        return False
    return bool(len(head)) and head["date"].max() >= (
        pd.Timestamp.today().normalize() - pd.Timedelta(days=days))


def fetch_csi(code="H00300", start=_CSI_START, tries=3, timeout=40):
    """中证官网日频 -> DataFrame(date, close, peg)。失败抛异常，由调用方决定是否用缓存。"""
    import requests                                       # 延迟导入：离线路径不依赖它
    import urllib3

    urllib3.disable_warnings()
    last = None
    for i in range(tries):
        try:
            s = requests.Session()
            s.headers.update({
                "User-Agent": ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                               "(KHTML, like Gecko) Chrome/124.0 Safari/537.36"),
                "Accept": "application/json, text/plain, */*",
            })
            s.verify = False
            r = s.get(_CSI_URL, params={"indexCode": code, "startDate": start,
                                        "endDate": date.today().strftime("%Y%m%d")},
                      headers={"Referer": "https://www.csindex.com.cn/"},
                      timeout=timeout, verify=False)
            if r.status_code != 200:
                raise RuntimeError(f"HTTP {r.status_code}")
            if not r.text.lstrip().startswith("{"):
                raise RuntimeError(f"返回非 JSON（前 60 字：{r.text[:60]!r}）")
            rows = r.json().get("data") or []
            out, seen = [], set()
            for x in rows:
                dt = str(x.get("tradeDate") or "")
                if not dt or dt in seen:
                    continue
                seen.add(dt)
                try:
                    out.append({"date": dt, "close": float(x.get("close")),
                                "peg": (float(x["peg"]) if x.get("peg") not in (None, "")
                                        else float("nan"))})
                except (TypeError, ValueError):
                    continue
            if not out:
                raise RuntimeError(f"{code} 日频返回空")
            df = pd.DataFrame(out)
            df["date"] = pd.to_datetime(df["date"], format="%Y%m%d")
            return df.sort_values("date").drop_duplicates("date", keep="last").reset_index(drop=True)
        except Exception as exc:                          # noqa: BLE001  —— 反爬/限流靠退避重试
            last = exc
            time.sleep(2.0 * (i + 1))
    raise RuntimeError(f"{code} 抓取失败：{type(last).__name__}: {last}")


def _merge_write(path, new, cols):
    """并集写入（新值覆盖旧值），绝不截断历史 —— 共享缓存可能已被别的策略写得更长。"""
    os.makedirs(os.path.dirname(path), exist_ok=True)
    parts = [new[cols]]
    if os.path.exists(path):
        try:
            old = pd.read_csv(path, parse_dates=["date"])
            if set(cols) <= set(old.columns):
                parts.insert(0, old[cols])
        except (OSError, ValueError):
            pass
    out = (pd.concat(parts, ignore_index=True)
           .drop_duplicates("date", keep="last").sort_values("date"))
    out["date"] = pd.to_datetime(out["date"]).dt.strftime("%Y-%m-%d")
    out.to_csv(path, index=False, encoding="utf-8")


def load_hs300_tr(refresh=False, days=CACHE_DAYS):
    """沪深300**全收益**日频收盘（红线 + 回测股票腿）。缓存优先，抓不到沿用缓存并告警。"""
    if refresh or not _cache_fresh(_HS300_TR, days):
        try:
            df = fetch_csi("H00300")
            _merge_write(_HS300_TR, df, ["date", "close", "peg"])
            SOURCE["hs300_tr"] = (f"中证官网 `H00300` 全收益（实时抓取）· "
                                  f"至 {df['date'].max():%Y-%m-%d}")
        except Exception as exc:                          # noqa: BLE001
            _warn(f"沪深300全收益抓取失败（{type(exc).__name__}: {str(exc)[:120]}），沿用缓存")
    if not os.path.exists(_HS300_TR):
        raise RuntimeError(f"缺 {_HS300_TR} —— 先跑 strategies/vol_legs/v4_erp_band/erp.py 预热")
    df = pd.read_csv(_HS300_TR, parse_dates=["date"]).dropna(subset=["close"]).sort_values("date")
    SOURCE.setdefault("hs300_tr", f"本地缓存 `csi_H00300.csv` · 至 {df['date'].max():%Y-%m-%d}")
    return pd.Series(df["close"].astype("float64").to_numpy(),
                     index=pd.DatetimeIndex(df["date"])).sort_index().rename("tr")


def _fund_nav(code):
    """xalpha 取单只基金**累计净值**（= 分红再投）-> Series(index=date)。"""
    import xalpha as xa
    p = xa.fundinfo(code).price
    if p is None or not len(p):
        raise RuntimeError(f"{code} 净值返回空")
    tot = pd.to_numeric(p.get("totvalue", p["netvalue"]), errors="coerce")
    s = pd.Series(tot.to_numpy(), index=pd.DatetimeIndex(pd.to_datetime(p["date"])))
    return s.dropna().sort_index().rename(str(code))


def load_bond_funds(refresh=False, days=CACHE_DAYS):
    """8 只老债基的累计净值宽表（缓存优先）-> DataFrame（index=date，8 列）。"""
    if refresh or not _cache_fresh(_BOND_RAW, days):
        have = {}
        if os.path.exists(_BOND_RAW):
            old = pd.read_csv(_BOND_RAW, parse_dates=["date"]).set_index("date")
            have = {str(c): old[c].dropna() for c in old.columns}
        got, fails = 0, []
        for code in FUNDS:
            try:
                have[code] = _fund_nav(code)
                got += 1
            except Exception as exc:                      # noqa: BLE001
                fails.append(f"{code}({type(exc).__name__})")
        if not have:
            raise RuntimeError("8 只债基全部抓取失败且无缓存 —— 无法构造二级债基指数")
        wide = pd.concat(have, axis=1).sort_index()
        wide = wide.loc[:, sorted(wide.columns)]
        os.makedirs(os.path.dirname(_BOND_RAW), exist_ok=True)
        out = wide.copy()
        out.index = out.index.strftime("%Y-%m-%d")
        out.index.name = "date"
        out.reset_index().to_csv(_BOND_RAW, index=False, encoding="utf-8")
        SOURCE["bond_funds"] = (f"xalpha 场外累计净值 {got}/{len(FUNDS)} 只刷新成功 · "
                                f"至 {wide.index[-1]:%Y-%m-%d}")
        if fails:
            _warn("债基净值抓取失败，沿用缓存：" + "、".join(fails))
    df = pd.read_csv(_BOND_RAW, parse_dates=["date"]).set_index("date").sort_index()
    SOURCE.setdefault("bond_funds", f"本地缓存 `_v5_bond_funds.csv` · 至 {df.index[-1]:%Y-%m-%d}")
    return df


def bond_composite(px=None, baseline=None):
    """自建二级债基指数 -> Series（锚 1.0，index=date）。

    `composite`：当日**有净值**的成员按日收益等权平均后累积（成员逐步加入，不回溯补空）；
    传单只代码：直接用那只基金的累计净值归一（敏感性对照）。
    """
    base = CFG["bond_baseline"] if baseline is None else baseline
    px = load_bond_funds() if px is None else px
    if base != "composite":
        if base not in px.columns:
            raise KeyError(f"bond_baseline={base!r} 不在缓存列里：{list(px.columns)}")
        s = px[base].dropna()
        return (s / s.iloc[0]).rename("bd")
    ret = px.pct_change(fill_method=None)
    n_ok = ret.notna().sum(axis=1)
    nav = (1.0 + ret.mean(axis=1, skipna=True).fillna(0.0)).cumprod()
    nav.iloc[0] = 1.0
    nav = nav.rename("bd")
    nav.attrs["n_funds"] = n_ok
    return nav


def load_bond_index(refresh=False, days=CACHE_DAYS, baseline=None):
    """二级债基指数（主口径带缓存）。

    只有 `composite`（自建复合）落缓存；换成单只基金时**不写缓存**，避免污染主口径文件。
    成员/口径变了用 `--refresh` 重建。
    """
    base = str(CFG["bond_baseline"] if baseline is None else baseline)
    if base != "composite":
        s = bond_composite(baseline=base)
        SOURCE["bond_index"] = (f"单只 `{base}` {FUNDS[base][0]} 累计净值（对照口径）· "
                                f"至 {s.index[-1]:%Y-%m-%d}")
        return s
    if refresh or not _cache_fresh(_BOND_IDX, days):
        nav = bond_composite()
        n_ok = nav.attrs.get("n_funds")
        df = pd.DataFrame({"date": nav.index.strftime("%Y-%m-%d"),
                           "close": nav.to_numpy(),
                           "n_funds": (n_ok.to_numpy() if n_ok is not None
                                       else np.full(len(nav), np.nan))})
        os.makedirs(os.path.dirname(_BOND_IDX), exist_ok=True)
        df.to_csv(_BOND_IDX, index=False, encoding="utf-8")
        SOURCE["bond_index"] = (f"自建复合（{len(FUNDS)} 只老债基等权日收益）· "
                                f"至 {nav.index[-1]:%Y-%m-%d}")
    df = pd.read_csv(_BOND_IDX, parse_dates=["date"])
    s = pd.Series(df["close"].astype("float64").to_numpy(),
                  index=pd.DatetimeIndex(df["date"])).sort_index().rename("bd")
    SOURCE.setdefault("bond_index", f"本地缓存 `_v5_bond_index.csv` · 至 {s.index[-1]:%Y-%m-%d}")
    return s


# ---------------------------------------------------------------- 3. 两线与信号
def build_frame(anchor=None, mode=None, level_tol=None, z_win=None, z_k=None,
                ffill_limit=None, hold_days=None, allow_rebuy=None, bond_baseline=None,
                refresh=False):
    """两线 + R + 信号 -> DataFrame(tr/bd/tr_n/bd_n/R/lnR/ma/sd/z/trig/pos)。

    * `tr`/`bd`   = 原始序列；`tr_n`/`bd_n` = 归一到锚点 1000 的两条线（画图用）
    * `R` = tr_n / bd_n，锚点处 = 1（= 股票相对债券的累计超额）
    * `trig`：当日收盘后是否满足买点条件（不含持仓状态）
    * `pos`：当日收盘成交后的持仓（1 = 满仓，收益从次日起算）
    """
    a = pd.Timestamp(CFG["anchor"] if anchor is None else anchor)
    mode = str(CFG["mode"] if mode is None else mode)
    tol = float(CFG["level_tol"] if level_tol is None else level_tol)
    zw = int(CFG["z_win"] if z_win is None else z_win)
    zk = float(CFG["z_k"] if z_k is None else z_k)
    lim = int(CFG["ffill_limit"] if ffill_limit is None else ffill_limit)

    tr = load_hs300_tr(refresh=refresh)
    bd = load_bond_index(refresh=refresh, baseline=bond_baseline)

    df = pd.DataFrame({"tr": tr})
    df["bd"] = bd.reindex(df.index).ffill(limit=lim)
    df = df.dropna(subset=["tr", "bd"]).sort_index()
    df = df[df.index >= a]
    if df.empty:
        raise RuntimeError(f"锚点 {a:%Y-%m-%d} 之后没有共同交易日")

    df = df.copy()
    df["tr_n"] = df["tr"] / df["tr"].iloc[0] * 1000.0
    df["bd_n"] = df["bd"] / df["bd"].iloc[0] * 1000.0
    df["R"] = df["tr_n"] / df["bd_n"]
    df["lnR"] = np.log(df["R"])
    df["ma"] = df["lnR"].rolling(zw, min_periods=zw).mean()
    df["sd"] = df["lnR"].rolling(zw, min_periods=zw).std(ddof=0)
    df["z"] = (df["lnR"] - df["ma"]) / df["sd"]
    if mode == "level":
        df["trig"] = (df["R"] <= 1.0 + tol)
    elif mode == "z":
        df["trig"] = (df["z"] <= -zk)
    else:
        raise ValueError(f"mode={mode!r} 不支持（level / z）")
    df["trig"] = df["trig"].fillna(False).astype(bool)
    df["pos"] = position_series(df["trig"], hold_days=hold_days,
                                lag=CFG["lag"], allow_rebuy=allow_rebuy)
    return df


def position_series(trig, hold_days=None, lag=None, allow_rebuy=None):
    """触发序列 -> 持仓序列（1 = 当日收盘成交买入，收益从次日起算）。

    * `hold_days=None` → 买入后一直持有（对照口径）
    * `allow_rebuy=False` → 一段触发只买一次（卖出后要等信号重新触发）
    """
    hd = CFG["hold_days"] if hold_days is None else hold_days
    lg = int(CFG["lag"] if lag is None else lag)
    rebuy = bool(CFG["allow_rebuy"] if allow_rebuy is None else allow_rebuy)
    t = np.asarray(trig, dtype=bool)
    n = len(t)
    pos = np.zeros(n, dtype=np.int8)
    i = 0
    while i < n:
        if not t[i]:
            i += 1
            continue
        start = i + lg
        if start >= n:
            break
        end = n if hd is None else min(start + int(hd), n)
        pos[start:end] = 1
        if rebuy:
            i = end                                    # 卖出日若仍触发 → 续买
        else:
            j = end
            while j < n and t[j]:                      # 等信号离开触发区间
                j += 1
            i = j
    return pos


def run(df, fee_buy=None, fee_sell=None, cash_annual=None, pos=None, cash_mode=None):
    """按持仓序列逐日复算单腿组合 -> dict(nav, trades, fees)。

    记账：满仓段收益 = 沪深300全收益日收益；空仓段按 `cash_mode` 计息
    （`cash` = 货基 1.40%；`bond` = 二级债基指数 —— 卖了就回债）；买卖各扣一次费率。
    """
    fb = float(CFG["fee_buy"] if fee_buy is None else fee_buy)
    fs = float(CFG["fee_sell"] if fee_sell is None else fee_sell)
    cash_ann = float(rule.CASH_ANNUAL if cash_annual is None else cash_annual)
    cash_daily = (1.0 + cash_ann) ** (1.0 / 252) - 1.0
    idle_bond = str(CFG["cash_mode"] if cash_mode is None else cash_mode) == "bond"

    p = (df["pos"].to_numpy(dtype=np.int8) if pos is None
         else np.asarray(pos, dtype=np.int8))
    tr = df["tr"].to_numpy(dtype="float64")
    bd = df["bd"].to_numpy(dtype="float64")
    rs = np.zeros(len(tr), dtype="float64")
    rs[1:] = tr[1:] / tr[:-1] - 1.0
    rb = np.zeros(len(bd), dtype="float64")
    rb[1:] = bd[1:] / bd[:-1] - 1.0

    nav, navs, trades, fees_paid = 1.0, [], [], 0.0
    prev = 0
    for i in range(len(tr)):
        if i > 0:
            idle = rb[i] if idle_bond else cash_daily
            nav *= 1.0 + (prev * rs[i] + (1 - prev) * idle)
        cur = int(p[i])
        if cur != prev:
            fee = fb if cur > prev else fs
            nav *= 1.0 - fee
            fees_paid += fee
            trades.append(dict(date=df.index[i].date().isoformat(),
                               side=("buy" if cur > prev else "sell"),
                               R=float(df["R"].iloc[i]), fee=fee))
        navs.append(nav)
        prev = cur
    series = pd.Series(navs, index=df.index, dtype="float64", name="nav")
    return dict(nav=series, trades=trades, fees=fees_paid)


def perf(nav):
    return engine.metrics(nav)


def cycle_lows(df, k=5, min_gap=800, win=61):
    """R 的 k 个**周期**低点 -> [(date, R)] 升序。

    做法：先取 R 的局部极小（rolling 中心最低），再**按时间顺序**贪心挑选，
    相邻两个至少隔 `min_gap` 个交易日（默认 800 ≈ 3.2 年）—— 这样每年轮的底都会被选到，
    而不是被 2008 年那种极端值占满名额。
    """
    R = df["R"]
    local = R[R <= R.rolling(win, center=True, min_periods=1).min()]
    pos = {dt: i for i, dt in enumerate(R.index)}
    pick = []
    for dt, v in local.items():                       # 已按时间升序
        if pick and pos[dt] - pos[pick[-1][0]] < min_gap:
            if v < pick[-1][1]:                       # 同窗口内取更低的那个
                pick[-1] = (dt, float(v))
            continue
        pick.append((dt, float(v)))
    return pick[-k:] if len(pick) > k else pick


def episodes(df, trig_col="trig", gap=20):
    """触发区间 -> [{start, end, days, R_start, R_min, tr_after_1y}]（最近的在前）。"""
    t = df[trig_col].to_numpy(dtype=bool)
    idx = df.index
    out, start, prev = [], None, None
    for i in range(len(t)):
        if not t[i]:
            continue
        if start is None:
            start = prev = i
        elif (idx[i] - idx[prev]).days <= gap:
            prev = i
        else:
            out.append((start, prev))
            start = prev = i
    if start is not None:
        out.append((start, prev))

    rows = []
    tr = df["tr"].to_numpy(dtype="float64")
    for a, b in out:
        seg = df.iloc[a:b + 1]
        j = min(a + 250, len(df) - 1)
        z0 = float(df["z"].iloc[a])
        rows.append(dict(start=idx[a].date().isoformat(), end=idx[b].date().isoformat(),
                         days=int(b - a + 1), R_start=float(df["R"].iloc[a]),
                         R_min=float(seg["R"].min()), R_mean=float(seg["R"].mean()),
                         z_start=(None if math.isnan(z0) else z0),
                         tr_start=float(tr[a]),
                         tr_after_1y=(float(tr[j] / tr[a] - 1.0) if j > a else None)))
    return rows[::-1]


def forward_stats(df, horizons=(20, 60, 250)):
    """触发日 vs 全样本的未来收益对比（回答「买点是不是真的更好」）。"""
    tr = df["tr"]
    rows = []
    for h in horizons:
        fwd = (tr.shift(-h) / tr - 1.0).dropna()
        m = df["trig"].reindex(fwd.index).fillna(False).astype(bool)
        for tag, r in (("触发日", fwd[m]), ("全样本", fwd)):
            if not len(r):
                continue
            rows.append(dict(horizon=h, tag=tag, n=int(len(r)), mean=float(r.mean()),
                             median=float(r.median()), win=float((r > 0).mean()),
                             p10=float(r.quantile(0.10)), p90=float(r.quantile(0.90))))
    return pd.DataFrame(rows)


# ---------------------------------------------------------------- 4. 绘图（复刻参考图）
def buy_marks(df):
    """持仓序列的买入日（pos 从 0 跳到 1 的那一天）。"""
    p = df["pos"].to_numpy(dtype=np.int8)
    return [df.index[i] for i in range(len(p)) if p[i] == 1 and (i == 0 or p[i - 1] == 0)]


def plot_overlay(df, path, title="红线跌破蓝线，或几乎贴合", buy=None, start=None, end=None,
                 subtitle=None, note=None, extra_marks=None):
    """双线（同锚归一）+ 红线低于蓝线的浅色区 + 买点圆圈 —— 复刻用户参考图。"""
    try:
        import matplotlib
        matplotlib.use("Agg")
        import matplotlib.pyplot as plt
        from matplotlib.ticker import StrMethodFormatter
    except Exception as exc:                              # noqa: BLE001
        print("matplotlib 不可用，跳过作图:", exc)
        return None

    plt.rcParams["font.sans-serif"] = ["Microsoft YaHei", "SimHei", "DejaVu Sans"]
    plt.rcParams["axes.unicode_minus"] = False

    d = df.loc[start:end]
    if d.empty:
        return None

    c_tr, c_bd = "#d43f3a", "#3f7fd0"
    fig, ax = plt.subplots(figsize=(13, 7.6))
    ax.fill_between(d.index, d["tr_n"], d["bd_n"],
                    where=(d["tr_n"].to_numpy() < d["bd_n"].to_numpy()), interpolate=True,
                    color="#f4cdc8", alpha=0.6, zorder=0, label="浅色区域：红线低于蓝线")
    ax.plot(d.index, d["tr_n"], color=c_tr, lw=1.35, label=LEGEND_TR, zorder=3)
    ax.plot(d.index, d["bd_n"], color=c_bd, lw=1.9, label=LEGEND_BD, zorder=2)

    marks = buy_marks(df) if buy is None else buy
    for k, dt in enumerate([x for x in marks if x in d.index][:15], 1):
        y = float(d.loc[dt, "tr_n"])
        ax.scatter([dt], [y], s=440, facecolors="none", edgecolors="white", linewidths=2.4,
                   zorder=5)
        ax.scatter([dt], [y], s=440, facecolors="none", edgecolors="#333333", linewidths=0.8,
                   zorder=6, alpha=0.55)
        ax.annotate(str(k), xy=(dt, y), xytext=(0, -24), textcoords="offset points",
                    ha="center", va="center", fontsize=10, color="#333333",
                    bbox=dict(boxstyle="circle,pad=0.22", fc="white", ec="#999999", lw=0.8),
                    zorder=7)

    # 参考图里的「圈」标在周期低点上（不一定触发买点）—— 灰色虚线圈
    for k, m in enumerate(extra_marks or [], 1):
        dt = m["date"] if isinstance(m, dict) else m
        label = m.get("label", str(k)) if isinstance(m, dict) else str(k)
        if dt not in d.index:
            continue
        y = float(d.loc[dt, "tr_n"])
        ax.scatter([dt], [y], s=520, facecolors="none", edgecolors="#8a8a8a",
                   linewidths=1.4, linestyles="dashed", zorder=4)
        ax.annotate(label, xy=(dt, y), xytext=(0, 26), textcoords="offset points",
                    ha="center", va="center", fontsize=9, color="#8a8a8a", zorder=6)

    ax.set_ylabel("两条线归一化到同一起点 = 1000", fontsize=10)
    ax.yaxis.set_major_formatter(StrMethodFormatter("{x:,.0f}"))
    ax.grid(axis="y", ls="--", lw=0.6, alpha=0.45)
    ax.grid(axis="x", ls=":", lw=0.5, alpha=0.25)
    ax.set_axisbelow(True)
    ax.spines["top"].set_visible(False)
    ax.spines["right"].set_visible(False)
    ax.legend(loc="upper left", frameon=False, fontsize=10)

    head = title if not subtitle else f"{title}    |    {subtitle}"
    fig.suptitle(head, x=0.055, ha="left", y=0.975, fontsize=15, color="#1b2b3a", weight="bold")
    fig.text(0.055, 0.014,
             note or ("红 = 沪深300全收益；蓝 = 自建二级债基指数（8 只老债基累计净值等权）。"
                      "两线同锚归一 1000，圆圈 = 买入点。  模拟结果，非投资建议。"),
             fontsize=9, color="#5b6b7a")
    fig.subplots_adjust(left=0.07, right=0.97, top=0.90, bottom=0.10)
    fig.savefig(path, dpi=130, facecolor="white")
    plt.close(fig)
    return path


def plot_nav(navs, path, title="净值对比"):
    """多档净值对比（对数坐标）。navs = {标签: Series}。"""
    try:
        import matplotlib
        matplotlib.use("Agg")
        import matplotlib.pyplot as plt
    except Exception as exc:                              # noqa: BLE001
        print("matplotlib 不可用，跳过作图:", exc)
        return None
    plt.rcParams["font.sans-serif"] = ["Microsoft YaHei", "SimHei", "DejaVu Sans"]
    plt.rcParams["axes.unicode_minus"] = False
    colors = ["#d43f3a", "#3f7fd0", "#7f7f7f", "#2f9e6b", "#e08c33", "#8e5ea2"]
    fig, ax = plt.subplots(figsize=(13, 6.6))
    for k, (name, s) in enumerate(navs.items()):
        ax.plot(s.index, s / s.iloc[0], lw=1.6, color=colors[k % len(colors)], label=name)
    ax.set_yscale("log")
    ax.grid(ls="--", lw=0.6, alpha=0.45)
    ax.set_axisbelow(True)
    ax.spines["top"].set_visible(False)
    ax.spines["right"].set_visible(False)
    ax.legend(loc="upper left", frameon=False, fontsize=10)
    first = next(iter(navs.values()))
    ax.set_title(title, x=0.02, ha="left", fontsize=14)
    fig.text(0.02, 0.012, f"对数坐标 · {first.index[0]:%Y-%m-%d} ~ {first.index[-1]:%Y-%m-%d}"
                          "  ·  模拟结果，非投资建议。", fontsize=9, color="#5b6b7a")
    fig.tight_layout(rect=(0, 0.03, 1, 1))
    fig.savefig(path, dpi=130, facecolor="white")
    plt.close(fig)
    return path


# ---------------------------------------------------------------- 5. 工具
def fmt_pct(x, nd=2):
    return "—" if x is None or (isinstance(x, float) and math.isnan(x)) else f"{x * 100:+.{nd}f}%"


def fmt_x(x, nd=2):
    return "—" if x is None or (isinstance(x, float) and math.isnan(x)) else f"{x:.{nd}f}"


def hold_state(df, hold_days=None):
    """当前持仓 -> dict(held, held_n, left, start)，`left` = 还需持有几个交易日。"""
    hd = int(CFG["hold_days"] if hold_days is None else hold_days)
    p = df["pos"].to_numpy(dtype=np.int8)
    n = len(p)
    if not n or p[-1] != 1:
        return dict(held=False, held_n=0, left=0, start=None)
    i = n - 1
    while i > 0 and p[i - 1] == 1:
        i -= 1
    return dict(held=True, held_n=n - i, left=max(hd - (n - i), 0), start=df.index[i])


def days_left(df, hold_days=None):
    """当前持仓还要持有多少个交易日（0 = 空仓或已到期）。"""
    return hold_state(df, hold_days)["left"]


def latest_reading(**kw):
    """当前读数（每日报告 / 自检共用）。kw 透传给 build_frame。"""
    df = build_frame(**kw)
    last = df.iloc[-1]
    mode = str(kw.get("mode", CFG["mode"]))
    thr = (f"R ≤ {1 + float(kw.get('level_tol', CFG['level_tol'])):.2f}" if mode == "level"
           else f"z ≤ -{float(kw.get('z_k', CFG['z_k'])):g}")
    z0 = float(last["z"])
    return dict(date=df.index[-1], df=df, mode=mode, thr_txt=thr,
                R=float(last["R"]), tr_n=float(last["tr_n"]), bd_n=float(last["bd_n"]),
                tr=float(last["tr"]), bd=float(last["bd"]),
                z=(None if math.isnan(z0) else z0),
                trig=bool(last["trig"]), pos=float(last["pos"]),
                state=("持仓中" if float(last["pos"]) == 1 else "空仓"),
                days_left=days_left(df), days_held=int((df["pos"] == 1).sum()),
                eps=episodes(df), source=dict(SOURCE))


def _self_check():
    """命令行自检：两线起点、触发段、买点、基准收益。"""
    for mode in ("level", "z"):
        df = build_frame(mode=mode)
        trig = int(df["trig"].sum())
        eps = episodes(df)
        print(f"[{mode}] {df.index[0]:%Y-%m-%d} ~ {df.index[-1]:%Y-%m-%d} · {len(df)} 日 · "
              f"触发 {trig} 日 / {len(eps)} 段 · 累计在场 {int(df['pos'].sum())} 日")
    df = build_frame()
    r = latest_reading()
    yrs = (df.index[-1] - df.index[0]).days / 365.25
    tr_ann = (df["tr"].iloc[-1] / df["tr"].iloc[0]) ** (1 / yrs) - 1
    bd_ann = (df["bd"].iloc[-1] / df["bd"].iloc[0]) ** (1 / yrs) - 1
    print(f"红线 沪深300全收益 年化 {tr_ann:+.2%} · 蓝线 自建二级债基指数 年化 {bd_ann:+.2%}")
    print(f"来源: {SOURCE.get('hs300_tr')} | {SOURCE.get('bond_index')}")
    print(f"最新 {r['date']:%Y-%m-%d}: R {r['R']:.3f} · z "
          f"{'—' if r['z'] is None else format(r['z'], '.2f')} · "
          f"{r['state']} · 触发 {r['trig']} · 买点 {r['thr_txt']} · 累计在场 {r['days_held']} 日")
    print("最近 6 段触发区间:")
    for e in r["eps"][:6]:
        print(f"  {e['start']} ~ {e['end']} ({e['days']} 日) R {e['R_start']:.2f}"
              f" 最低 {e['R_min']:.2f} · 之后 250 日 "
              f"{'—' if e['tr_after_1y'] is None else format(e['tr_after_1y'], '+.1%')}")
    p = run(df)
    m = perf(p["nav"])
    print(f"主口径回测: 累计 {m['cum']:+.1%} · 年化 {m['ann']:+.2%} · 回撤 {m['mdd']:+.2%} · "
          f"夏普 {m['sharpe']:.2f} · 交易 {len(p['trades'])} 笔 · 费用 {p['fees']:.2%}")
    if _WARN:
        print("告警:", len(_WARN))
        for w in _WARN[:5]:
            print("  -", w)


if __name__ == "__main__":
    if "--refresh" in sys.argv:
        load_hs300_tr(refresh=True)
        load_bond_funds(refresh=True)
        load_bond_index(refresh=True)
    _self_check()
