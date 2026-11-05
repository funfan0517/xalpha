# -*- coding: utf-8 -*-
"""波动资产 · V8 变种：**跨资产 12-1 动量**（七腿内每月选最强 N 条）。

一句话：**不判断单条腿的方向，只在七腿之间「汰弱留强」** —— 每月末按过去 12 个月
（跳过最近 1 个月）的收益排序，只持有最强的 `TOPN` 条腿，其余额度放货基。

为什么是 12-1（跳过最近 1 个月）：
- 截面动量文献（Jegadeesh & Titman 1993；Antonacci 的 Dual Momentum）一致用「12 个月跳过最近 1 个月」，
  因为最近 1 个月存在**短期反转**，纳入反而降低信噪比；
- 本仓库的七腿都是宽基/商品指数基金，没有个股的财报与流动性噪声，12-1 的单因子口径最干净。

与前六个变种的层次差异（重要）：
- v1/v2/v3/v5/v7 都是**单腿进出时点**（决定「这条腿今天上不上车」）；
- 本变种是**组合层额度分配**（决定「这 40pp 分给哪几条腿」）——
  因此它**没有逐腿表**，只有组合层；它可以与任何单腿时点变种叠加
  （本变种定配哪几条、时点变种定每条露多少）。

## 规则（可执行版）

| 环节 | 规则 |
|---|---|
| 打分 | `score = P[t−SKIP] / P[t−LOOKBACK] − 1`（默认 12 个月、跳过 1 个月） |
| 排序 | 每月**最后一个交易日**收盘，对「历史足够（≥ LOOKBACK+SKIP 根）」的腿按 score 排序 |
| 选择 | 取最强 `TOPN`（默认 3）条；不足 N 条就全取；一条都不够就空仓放货基 |
| 权重 | `WEIGHT_MODE="norm"`（默认）：选中腿按**各自目标带**比例归一化到满仓（Σ=1）<br>`"band"`：不重分配，只用各腿自己的带位（会留下大量现金） |
| 成交 | 月末盘后算信号、**次一交易日收盘成交**（与 v1~v7 同一纪律） |
| 成本 | 每次调仓按 |Δ权重| 扣申赎费 + 滑点 |

## 已知边界

- **换手是主要成本**：每月最多换掉 N 条腿，A 类场外单次往返约 0.6%，一年可吃掉数个百分点；
  「含费 vs 不计费」两列必须一起看。
- **截面动量在「同涨同跌」的池子里失效**：七腿中长期同向时（如 2020 全涨），排序信息量很低。
- **它会追高**：动量买的是「过去一年最强」，在泡沫末期会重仓最热的资产，回撤可能更深 ——
  这与 v7（美股年线闸门）的「跌破年线才减仓」正好相反，两者叠加才有意义。
- **样本限制**：科创50/恒生科技 2022 年才有场外净值，2022 年之前实际只有 5 条腿参与排序。

模拟结果，非投资建议。
"""
import math
import os
import sys

import numpy as np
import pandas as pd

_HERE = os.path.dirname(os.path.abspath(__file__))
_VL = os.path.dirname(_HERE)
_ROOT = os.path.dirname(_VL)
for _p in (_HERE, _VL, _ROOT):
    if _p not in sys.path:
        sys.path.insert(0, _p)

import data as vdata      # noqa: E402
import engine             # noqa: E402
import rule               # noqa: E402

vdata._otc_stale = lambda: False   # 离线优先：只读本地缓存  # type: ignore[assignment]

LABEL = "V8 跨资产动量"

# ---------------------------------------------------------------- 1. 参数（唯一权威源）
LOOKBACK = 252          # 动量回看（交易日）≈ 12 个月
SKIP = 21               # 跳过最近（交易日）≈ 1 个月（短期反转）
TOPN = 3                # 持有最强的几条腿
REBAL = "M"             # "M" = 月末; "Q" = 季末
WEIGHT_MODE = "norm"    # "norm" = 选中腿按目标带归一化到满仓; "band" = 只用各自带位
MIN_HIST = LOOKBACK + SKIP   # 参与排序所需的最少历史根数
MIN_LEGS = 3            # 可排序的腿少于此数时**维持当前仓位不动**
# 为什么需要 MIN_LEGS：2022 年前只有 5 条腿有场外净值，2013 年前只有 2 条
# （且 a500 早期桥接用的 000051 与 hs300 同源）——池子太小就会把「同一条腿」当成两条堆满仓，
# 那不是动量策略，是单押。少于 MIN_LEGS 时不动，等价于「样本外按兵不动」。
SLIP = 0.001            # 单边滑点 0.1%
CASH_ANNUAL = rule.CASH_ANNUAL
CASH_DAILY = (1.0 + CASH_ANNUAL) ** (1.0 / 252) - 1.0

LEGS = rule.LEG_ORDER
BAND = {k: float(rule.LEGS[k]["band"]) for k in LEGS}
SLEEVE = float(rule.SLEEVE_PP)
W = {k: BAND[k] / SLEEVE for k in LEGS}          # 带权归一化(Σ=1)

# 场外申赎成本（(买入, 卖出) 单边费率），与 v1~v7 同一张表：
FEE = {
    "a500": (0.0, 0.0),
    "hs300": (0.001, 0.005),
    "ndx": (0.001, 0.005),
    "spx": (0.001, 0.005),
    "kc50": (0.0, 0.0),
    "hkt": (0.0, 0.0),
    "gold": (0.001, 0.005),
}

QUADS = None  # 兼容占位（本变种无四象限）


def params(**kw):
    """参数打包：回测敏感性扫描时用 `**{...}` 覆盖，不必改模块常量。"""
    p = dict(LOOKBACK=LOOKBACK, SKIP=SKIP, TOPN=TOPN, REBAL=REBAL,
             WEIGHT_MODE=WEIGHT_MODE, MIN_HIST=MIN_HIST, MIN_LEGS=MIN_LEGS)
    p.update(kw)
    return p


def load(leg, mode=None):
    """该腿价格序列（默认场外复权净值）；缺失直接抛错，不静默补位。"""
    return vdata.series_for_leg(leg, mode=mode).dropna()


def fee_of(leg, slip=None):
    """-> (买入费率, 卖出费率)，含滑点。"""
    s = SLIP if slip is None else float(slip)
    b, sl = FEE[leg]
    return float(b) + s, float(sl) + s


# ---------------------------------------------------------------- 2. 面板与打分
def panel(legs=None):
    """七腿价格面板：index = 交易日并集，各腿只在自身有数据的日期有值。"""
    legs = legs or LEGS
    return pd.DataFrame({k: load(k) for k in legs}).sort_index()


def scores(df, **kw):
    """各腿 12-1 动量分（**在该腿自己的交易日序列上 shift**，再对齐到并集日历）。

    用自己序列 shift 而不是并集日历 shift：各腿节假日不同，按行 shift 会错位。
    """
    p = params(**kw)
    out = {}
    for k in df.columns:
        s = df[k].dropna()
        out[k] = (s.shift(int(p["SKIP"])) / s.shift(int(p["LOOKBACK"])) - 1.0) \
            .reindex(df.index).ffill()
    return pd.DataFrame(out)


def nobs(df):
    """各腿截至当日的累计观测数（用于判断历史是否够长）。"""
    return df.notna().cumsum()


def rebal_dates(idx, rebal="M"):
    """调仓决策日：月末（或季末）的最后一个交易日。"""
    idx = pd.DatetimeIndex(idx)
    out = []
    for i in range(len(idx) - 1):
        a, b = idx[i], idx[i + 1]
        if rebal == "Q":
            if (a.year, (a.month - 1) // 3) != (b.year, (b.month - 1) // 3):
                out.append(a)
        elif (a.year, a.month) != (b.year, b.month):
            out.append(a)
    if len(idx):
        out.append(idx[-1])
    return out


def targets(df, hist=None, **kw):
    """每个调仓日 -> {date: dict(pick, score, w)}。`w` 是占总资产（满仓=1）的权重。

    `hist` 是「各腿累计观测数」面板（`nobs(全样本)`）：回测在大样本上算好再传进来，
    否则对切片后的 df 重新计数会把「历史够长」判错（切片首年会被误判为历史不足）。
    """
    p = params(**kw)
    sc = scores(df, **kw)
    nb = nobs(df) if hist is None else hist.reindex(df.index)
    need = int(p["MIN_HIST"])
    out = {}
    for d in rebal_dates(df.index, p["REBAL"]):
        row = sc.loc[d]
        ok = [k for k in df.columns if row[k] == row[k] and float(nb.loc[d, k]) >= need]
        if int(p["TOPN"]) <= 0:                          # TOPN=0：全部放货基
            out[d] = dict(pick=[], score={}, w={})
            continue
        if len(ok) < int(p["MIN_LEGS"]):                 # 池子太小：维持当前仓位（w=None）
            out[d] = dict(pick=[], score={}, w=None)
            continue
        pick = sorted(ok, key=lambda k: float(row[k]), reverse=True)[:int(p["TOPN"])]
        if p["WEIGHT_MODE"] == "norm":
            tot = sum(BAND[k] for k in pick)
            w = {k: BAND[k] / tot for k in pick}
        else:
            w = {k: BAND[k] / SLEEVE for k in pick}
        out[d] = dict(pick=pick, score={k: float(row[k]) for k in pick}, w=w)
    return out


# ---------------------------------------------------------------- 3. 回测
def simulate(df, tgt=None, fee=True, slip=None, detail=False):
    """逐日复算组合净值（起点 = 1）。

    仓位 `cur[k]` 是「占总资产的权重」。收盘执行上一交易日收盘做出的目标权重，
    因此 t 日的收益由 t−1 日收盘的仓位赚取（无前视）。
    """
    idx = df.index
    px = df.ffill()
    ret = px.pct_change().fillna(0.0)
    cols = list(df.columns)
    cur = {k: 0.0 for k in cols}
    nav, navs, expos, trades = 1.0, [], [], []
    pend = None
    for i, d in enumerate(idx):
        if i > 0:                                        # 1) 昨日收盘的仓位赚今日收益
            e = sum(cur.values())
            r = sum(cur[k] * float(ret[k].iloc[i]) for k in cols)
            r += (1.0 - e) * CASH_DAILY
            nav *= (1.0 + r)
            if pend is not None:                         # 2) 收盘执行（次一交易日成交）
                cost = 0.0
                for k in cols:
                    dw = float(pend.get(k, 0.0)) - cur[k]
                    if abs(dw) > 1e-12:
                        if fee:
                            rate = fee_of(k, slip)[0] if dw > 0 else fee_of(k, slip)[1]
                            cost += abs(dw) * rate
                        trades.append(dict(date=d, leg=k, dw=dw))
                    cur[k] = float(pend.get(k, 0.0))
                nav *= (1.0 - cost)
                pend = None
        if tgt is not None and d in tgt and tgt[d].get("w") is not None:
            pend = dict(tgt[d]["w"])                     # 3) 收盘做出决策 -> 次日成交（w=None 不动）
        navs.append(nav)
        expos.append(sum(cur.values()))
    nav_s = pd.Series(navs, index=idx, name="nav")
    exp_s = pd.Series(expos, index=idx, name="exposure")
    return (nav_s, exp_s, trades) if detail else nav_s


def static_nav(df, weight=None, fee=True, slip=None, detail=False):
    """静态组合（第一天收盘建仓后不动）：weight 默认各腿目标带。"""
    weight = weight or W
    tgt = {df.index[0]: dict(pick=list(df.columns), score={}, w=dict(weight))}
    return simulate(df, tgt, fee=fee, slip=slip, detail=detail)


def perf(nav):
    """绩效指标（复用主策略 engine.metrics，无风险利率 = 货基 CASH_ANNUAL）。"""
    return engine.metrics(nav)


def yearly(nav):
    """按自然年切分的年度收益（首尾为不完整年度）。"""
    out, seg = {}, {}
    vals = nav.to_numpy(dtype=float)
    for i, d in enumerate(nav.index):
        y = str(d.year)
        st = 1.0 if i == 0 else vals[i - 1]
        if y not in seg:
            seg[y] = [st, vals[i]]
        seg[y][1] = vals[i]
    for y, (a, b) in seg.items():
        out[y] = b / a - 1.0
    return out


def fmt_pct(x, nd=2):
    return "—" if x is None else f"{x * 100:+.{nd}f}%"


def _self_check():
    """快速自检：面板可读、权重和 ≤ 1、最近一期名单可打印。"""
    df = panel()
    tgt = targets(df)
    bad = [d for d, v in tgt.items()
           if v["w"] is not None and sum(v["w"].values()) > 1.0 + 1e-9]
    assert not bad, f"权重和 > 1: {bad[:3]}"
    act = [d for d, v in tgt.items() if v["w"] is not None]
    last = max(act)
    print(f"{df.index[0].date()} ~ {df.index[-1].date()} · 调仓日 {len(tgt)} 个"
          f"（池子够大、实际调仓 {len(act)} 个）")
    print(f"最近一期 {last.date()}: " + ", ".join(
        f"{rule.LEGS[k]['name']}({v:.1%})" for k, v in tgt[last]["w"].items()))
    print(f"打分 12-1（跳过 {SKIP} 日）· 持有最强 {TOPN} 条 · 权重 {WEIGHT_MODE} · 频率 {REBAL}")


if __name__ == "__main__":
    _self_check()
